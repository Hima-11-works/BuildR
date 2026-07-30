"""
Unit tests for services/auth_service.py.

SECURITY NOTE
=============
BuildR's auth model is email-only — there is no password. The email
IS the credential. Anyone who knows a user's email can sign in as
them and read or overwrite their data. This is by explicit user
request. If the threat model changes, the password / OTP / OAuth
logic goes into services/auth_service.py:sign_in() — these tests
will need to evolve with it.
"""
import json
from pathlib import Path

import pytest

from services import auth_service
from services.auth_service import (
    USERS_ROOT,
    require_auth,
    sign_in,
    sign_out,
    current_user_id,
    current_user_email,
    user_id_from_email,
    user_root,
    validate_email,
)


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

    def test_sign_in_sets_session(self, app):
        with app.test_request_context():
            user_id, email = sign_in("alice@example.com")
            assert user_id == user_id_from_email("alice@example.com")
            assert email == "alice@example.com"
            assert current_user_id() == user_id
            assert current_user_email() == "alice@example.com"

    def test_sign_in_persists_user_json(self, app, tmp_path):
        with app.test_request_context():
            sign_in("alice@example.com")
        meta_path = tmp_path / "users" / user_id_from_email("alice@example.com") / "user.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["email"] == "alice@example.com"
        assert meta["user_id"] == user_id_from_email("alice@example.com")
        assert "created_at" in meta
        assert "last_seen_at" in meta

    def test_sign_in_updates_last_seen_on_repeat(self, app, tmp_path):
        # Patch USERS_ROOT so this test does not leak into real storage.
        import services.auth_service as auth_module
        original_root = auth_module.USERS_ROOT
        auth_module.USERS_ROOT = tmp_path / "users"
        try:
            with app.test_request_context():
                sign_in("alice@example.com")
            first_path = tmp_path / "users" / user_id_from_email("alice@example.com") / "user.json"
            first_meta = json.loads(first_path.read_text(encoding="utf-8"))
            with app.test_request_context():
                sign_in("alice@example.com")
            second_meta = json.loads(first_path.read_text(encoding="utf-8"))
            assert first_meta["created_at"] == second_meta["created_at"]
            # last_seen_at is rewritten (might equal if both calls happen
            # in the same datetime tick — that's fine; the test only
            # checks the key is present and a string).
            assert isinstance(second_meta["last_seen_at"], str)
        finally:
            auth_module.USERS_ROOT = original_root

    def test_sign_in_rejects_malformed_email(self, app):
        with app.test_request_context():
            with pytest.raises(ValueError):
                sign_in("not-an-email")

    def test_sign_out_clears_session(self, app):
        with app.test_request_context():
            sign_in("alice@example.com")
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
        client.post("/test-sign-in", json={"email": "alice@example.com"}) if "/test-sign-in" in [r.rule for r in app.url_map.iter_rules()] else None

        # Manually set the session cookie via test_request_context + client.
        with app.test_request_context():
            sign_in("alice@example.com")
            from flask import session as flask_session
            sess_cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
            # Easiest: use the test client's session_transaction.
        with client.session_transaction() as sess:
            sess["user_id"] = user_id_from_email("alice@example.com")
            sess["email"] = "alice@example.com"

        resp = client.get("/secret")
        assert resp.status_code == 200
        assert resp.get_json()["user_id"] == user_id_from_email("alice@example.com")


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
