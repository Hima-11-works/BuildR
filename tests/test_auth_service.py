"""
Unit tests for services/auth_service.py.

SECURITY MODEL
==============
BuildR's auth model is email + password. The email identifies the
account; the password proves ownership. Passwords are hashed with
werkzeug's generate_password_hash (scrypt by default) and never
stored or logged in plaintext.

The tests below exercise both happy-path flows and the failure
modes that prevent account enumeration (single generic error
message for wrong-password vs unknown-email).
"""
import json
from pathlib import Path

import pytest

from services import auth_service
from services.auth_service import (
    USERS_ROOT,
    hash_password,
    verify_password,
    require_auth,
    sign_in,
    sign_up,
    sign_out,
    change_password,
    current_user_id,
    current_user_email,
    user_id_from_email,
    user_root,
    user_exists_with_password,
    validate_email,
    validate_password,
)

TEST_PASSWORD = "test-password-123"


# ── user_id_from_email / validate_email ───────────────────────

class TestUserIdFromEmail:
    def test_returns_16_hex_chars(self):
        uid = user_id_from_email("alice@example.com")
        assert len(uid) == 16
        assert all(c in "0123456789abcdef" for c in uid)

    def test_is_deterministic(self):
        assert user_id_from_email("alice@example.com") == user_id_from_email("alice@example.com")

    def test_is_case_insensitive(self):
        assert user_id_from_email("Alice@Example.com") == user_id_from_email("alice@example.com")

    def test_strips_whitespace(self):
        assert user_id_from_email("  alice@example.com  ") == user_id_from_email("alice@example.com")

    def test_different_emails_yield_different_ids(self):
        assert user_id_from_email("alice@example.com") != user_id_from_email("bob@example.com")

    def test_handles_empty_string_safely(self):
        # Should not raise — just produce *some* deterministic hash.
        uid = user_id_from_email("")
        assert len(uid) == 16


class TestValidateEmail:
    @pytest.mark.parametrize("good", [
        "alice@example.com",
        "ALICE@EXAMPLE.COM",
        "a.b+c@sub.example.co.uk",
        "  trim.me@example.com  ",
    ])
    def test_accepts_well_formed(self, good):
        result = validate_email(good)
        assert result is not None
        assert "@" in result
        # Lower-cased + trimmed
        assert result == result.lower().strip()

    @pytest.mark.parametrize("bad", [
        "",
        "   ",
        "no-at-sign",
        "@no-local-part.com",
        "no-domain@",
        "two@@signs.com",
        None,
        123,
        [],
    ])
    def test_rejects_malformed(self, bad):
        assert validate_email(bad) is None


# ── user_root ─────────────────────────────────────────────────

class TestUserRoot:
    def test_creates_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(auth_service, "USERS_ROOT", tmp_path / "users")
        p = user_root("abcdef0123456789")
        assert p.is_dir()
        assert p.parent == tmp_path / "users"

    def test_rejects_invalid_user_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(auth_service, "USERS_ROOT", tmp_path / "users")
        with pytest.raises(ValueError):
            user_root("../etc")
        with pytest.raises(ValueError):
            user_root("has spaces")
        with pytest.raises(ValueError):
            user_root("not/a/safe/path")
        with pytest.raises(ValueError):
            user_root("")


# ── sign_in / sign_out / current_user_id (Flask session) ──────

class TestSessionLifecycle:
    """Drive the session helpers through a real Flask app context."""

    @pytest.fixture
    def app(self, tmp_path, monkeypatch):
        # Point USERS_ROOT at tmp so user.json side effects are isolated.
        monkeypatch.setattr(auth_service, "USERS_ROOT", tmp_path / "users")

        # Use a fresh Flask app — we don't need the full BuildR app
        # to test the session primitives.
        from flask import Flask
        app = Flask(__name__)
        app.secret_key = "test-secret"
        return app

    def test_sign_up_creates_account_and_sets_session(self, app):
        with app.test_request_context():
            user_id, email = sign_up("alice@example.com", TEST_PASSWORD)
            assert user_id == user_id_from_email("alice@example.com")
            assert email == "alice@example.com"
            assert current_user_id() == user_id
            assert current_user_email() == "alice@example.com"

    def test_sign_up_persists_password_hash(self, app, tmp_path):
        with app.test_request_context():
            sign_up("alice@example.com", TEST_PASSWORD)
        meta_path = tmp_path / "users" / user_id_from_email("alice@example.com") / "user.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["email"] == "alice@example.com"
        assert meta["user_id"] == user_id_from_email("alice@example.com")
        assert "created_at" in meta
        assert "last_seen_at" in meta
        # Password hash is stored, plaintext is not.
        assert "password_hash" in meta
        assert meta["password_hash"] != TEST_PASSWORD  # not plaintext
        # The stored hash verifies correctly.
        assert verify_password(TEST_PASSWORD, meta["password_hash"])

    def test_sign_in_with_correct_password_sets_session(self, app):
        with app.test_request_context():
            sign_up("alice@example.com", TEST_PASSWORD)
            sign_out()
            user_id, email = sign_in("alice@example.com", TEST_PASSWORD)
            assert user_id == user_id_from_email("alice@example.com")
            assert current_user_id() == user_id

    def test_sign_in_with_wrong_password_raises(self, app):
        with app.test_request_context():
            sign_up("alice@example.com", TEST_PASSWORD)
            sign_out()
            with pytest.raises(ValueError) as exc_info:
                sign_in("alice@example.com", "wrong-password-456")
            # Generic error message — does not leak that the email is registered.
            assert "invalid" in str(exc_info.value).lower()

    def test_sign_in_for_unknown_email_raises(self, app):
        with app.test_request_context():
            with pytest.raises(ValueError) as exc_info:
                sign_in("nobody@example.com", TEST_PASSWORD)
            # Same generic message as wrong-password — prevents enumeration.
            assert "invalid" in str(exc_info.value).lower()

    def test_sign_in_with_empty_password_raises(self, app):
        with app.test_request_context():
            sign_up("alice@example.com", TEST_PASSWORD)
            sign_out()
            with pytest.raises(ValueError):
                sign_in("alice@example.com", "")

    def test_sign_in_rejects_malformed_email(self, app):
        with app.test_request_context():
            with pytest.raises(ValueError):
                sign_in("not-an-email", TEST_PASSWORD)

    def test_sign_up_rejects_short_password(self, app):
        with app.test_request_context():
            with pytest.raises(ValueError) as exc_info:
                sign_up("alice@example.com", "short")
            assert "8" in str(exc_info.value)

    def test_sign_up_rejects_duplicate_account(self, app):
        with app.test_request_context():
            sign_up("alice@example.com", TEST_PASSWORD)
            sign_out()
            with pytest.raises(ValueError) as exc_info:
                sign_up("alice@example.com", "another-password-789")
            assert "already" in str(exc_info.value).lower()

    def test_sign_up_attaches_password_to_legacy_user(self, app, tmp_path):
        """Legacy email-only accounts (user.json exists without
        password_hash) can be upgraded by calling sign_up with the
        same email + a new password. Existing created_at preserved."""
        user_id = user_id_from_email("alice@example.com")
        legacy_path = tmp_path / "users" / user_id / "user.json"
        legacy_path.parent.mkdir(parents=True)
        legacy_path.write_text(json.dumps({
            "user_id": user_id,
            "email": "alice@example.com",
            "created_at": "2025-01-01T00:00:00+00:00",
            "last_seen_at": "2025-06-01T00:00:00+00:00",
        }), encoding="utf-8")

        with app.test_request_context():
            sign_up("alice@example.com", TEST_PASSWORD)

        meta = json.loads(legacy_path.read_text(encoding="utf-8"))
        assert "password_hash" in meta
        assert meta["created_at"] == "2025-01-01T00:00:00+00:00"  # preserved

    def test_sign_out_clears_session(self, app):
        with app.test_request_context():
            sign_up("alice@example.com", TEST_PASSWORD)
            assert current_user_id() is not None
            sign_out()
            assert current_user_id() is None

    def test_sign_out_is_idempotent(self, app):
        with app.test_request_context():
            sign_out()  # No user signed in — should not raise.
            assert current_user_id() is None

    def test_require_auth_blocks_anonymous(self, app):
        @app.route("/secret")
        @require_auth
        def secret():
            return "ok"

        client = app.test_client()
        resp = client.get("/secret")
        assert resp.status_code == 401
        assert resp.get_json()["error"] == "Authentication required."

    def test_require_auth_passes_signed_in(self, app):
        @app.route("/secret")
        @require_auth
        def secret():
            return {"user_id": current_user_id()}

        client = app.test_client()
        with app.test_request_context():
            sign_up("alice@example.com", TEST_PASSWORD)
        with client.session_transaction() as sess:
            sess["user_id"] = user_id_from_email("alice@example.com")
            sess["email"] = "alice@example.com"

        resp = client.get("/secret")
        assert resp.status_code == 200
        assert resp.get_json()["user_id"] == user_id_from_email("alice@example.com")

    def test_change_password_requires_correct_old(self, app):
        with app.test_request_context():
            sign_up("alice@example.com", TEST_PASSWORD)
            with pytest.raises(ValueError) as exc_info:
                change_password("wrong-old-password", "new-password-456")
            assert "incorrect" in str(exc_info.value).lower()

    def test_change_password_updates_stored_hash(self, app):
        with app.test_request_context():
            sign_up("alice@example.com", TEST_PASSWORD)
            change_password(TEST_PASSWORD, "new-password-456")
            # Old password no longer works.
            sign_out()
            with pytest.raises(ValueError):
                sign_in("alice@example.com", TEST_PASSWORD)
            # New password works.
            sign_in("alice@example.com", "new-password-456")

    def test_change_password_rejects_short_new_password(self, app):
        with app.test_request_context():
            sign_up("alice@example.com", TEST_PASSWORD)
            with pytest.raises(ValueError) as exc_info:
                change_password(TEST_PASSWORD, "short")
            assert "8" in str(exc_info.value)


class TestPasswordHashing:
    def test_hash_password_returns_non_plaintext(self):
        h = hash_password("any-password-123")
        assert h != "any-password-123"
        assert isinstance(h, str)
        assert len(h) > 20  # scrypt hashes are long

    def test_hash_password_includes_salt(self):
        """Two hashes of the same password should differ (per-password
        random salt), but both must verify."""
        h1 = hash_password("same-password-123")
        h2 = hash_password("same-password-123")
        assert h1 != h2
        assert verify_password("same-password-123", h1)
        assert verify_password("same-password-123", h2)

    def test_verify_password_correct(self):
        h = hash_password("a-password-12345")
        assert verify_password("a-password-12345", h) is True

    def test_verify_password_wrong(self):
        h = hash_password("a-password-12345")
        assert verify_password("different-password", h) is False

    def test_verify_password_handles_garbage(self):
        # Never raise on malformed inputs — return False.
        assert verify_password("any-password", "") is False
        assert verify_password("any-password", "not-a-real-hash") is False
        assert verify_password("", "any-hash") is False
        assert verify_password(None, "any-hash") is False

    def test_validate_password_enforces_minimum_length(self):
        assert validate_password("a") is None
        assert validate_password("1234567") is None  # 7 chars
        assert validate_password("12345678") == "12345678"  # 8 chars OK
        assert validate_password("a-much-longer-password-here") == "a-much-longer-password-here"
        assert validate_password(None) is None
        assert validate_password(123) is None

    def test_user_exists_with_password(self, tmp_path, monkeypatch):
        monkeypatch.setattr(auth_service, "USERS_ROOT", tmp_path / "users")
        # No user yet
        assert user_exists_with_password(user_id_from_email("alice@example.com")) is False

        # Sign up via real Flask context
        from flask import Flask
        app = Flask(__name__)
        app.secret_key = "t"
        with app.test_request_context():
            sign_up("alice@example.com", TEST_PASSWORD)

        assert user_exists_with_password(user_id_from_email("alice@example.com")) is True


# ── cleanup_expired_sessions_for_all_users ────────────────────

class TestCrossUserCleanup:
    def test_iterates_all_users_and_sums(self, tmp_path, monkeypatch):
        monkeypatch.setattr(auth_service, "USERS_ROOT", tmp_path / "users")

        # Set up two user dirs each with an obviously-expired session file.
        import time, json
        for uid in ("a" * 16, "b" * 16):
            user_dir = tmp_path / "users" / uid
            user_dir.mkdir(parents=True)
            (user_dir / "sessions").mkdir()
            # mtime far in the past
            old_mtime = time.time() - (365 * 24 * 60 * 60)
            sess_dir = user_dir / "sessions" / "session_old"
            sess_dir.mkdir()
            (sess_dir / "draft").mkdir()
            (sess_dir / "draft" / "profile.json").write_text("{}")
            # backdate
            import os
            for root, _, files in os.walk(sess_dir):
                for f in files:
                    os.utime(Path(root) / f, (old_mtime, old_mtime))

        # Also create an unrelated directory that should be SKIPPED.
        (tmp_path / "users" / ".legacy-migrated").mkdir()

        removed = auth_service.cleanup_expired_sessions_for_all_users()
        # Each user had one expired session
        assert removed == 2
        # User dirs still exist (cleanup removes the session inside, not the user dir)
        assert (tmp_path / "users" / ("a" * 16)).is_dir()
        assert (tmp_path / "users" / ("b" * 16)).is_dir()
        assert (tmp_path / "users" / ".legacy-migrated").is_dir()

    def test_handles_missing_users_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(auth_service, "USERS_ROOT", tmp_path / "nonexistent")
        assert auth_service.cleanup_expired_sessions_for_all_users() == 0
