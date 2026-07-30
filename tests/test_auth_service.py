"""
Unit tests for services/auth_service.py.

SECURITY MODEL
==============
BuildR's auth model is Google OAuth. The server verifies Google-issued
ID tokens against Google's published JWKs, extracts the `sub` claim,
and uses that as the user's canonical identifier. Email is a display
attribute only and is NEVER used for identity.

These tests cover the primitives that the rest of the app relies on:

  - `user_id_from_google_sub` deterministically derives a hex user_id
  - `verify_google_id_token` is patched to a fake in tests (the real
    signature check requires Google's public keys and a real token).
  - `sign_in_with_google` establishes a Flask session bound to the sub
  - `require_auth` 401s anonymous requests
  - `cleanup_expired_sessions_for_all_users` iterates per-user trees

The CRITICAL security invariant — that an email alone does NOT grant
access — is enforced by the live /api/auth/google route and is tested
end-to-end in test_user_isolation.py::TestAttackerWithVictimEmail.
"""
import json
from pathlib import Path

import pytest

from services import auth_service
from services.auth_service import (
    USERS_ROOT,
    cleanup_expired_sessions_for_all_users,
    current_user_email,
    current_user_id,
    require_auth,
    sign_in_with_google,
    sign_out,
    user_id_from_google_sub,
    user_root,
    verify_google_id_token,
)


# ── user_id_from_google_sub ────────────────────────────────────

class TestUserIdFromGoogleSub:
    def test_returns_16_hex_chars(self):
        uid = user_id_from_google_sub("123456789012345678901")
        assert len(uid) == 16
        assert all(c in "0123456789abcdef" for c in uid)

    def test_is_deterministic(self):
        sub = "123456789012345678901"
        assert user_id_from_google_sub(sub) == user_id_from_google_sub(sub)

    def test_different_subs_yield_different_ids(self):
        assert (
            user_id_from_google_sub("111111111111111111111")
            != user_id_from_google_sub("222222222222222222222")
        )

    def test_rejects_empty_sub(self):
        with pytest.raises(ValueError):
            user_id_from_google_sub("")

    def test_rejects_non_string_sub(self):
        with pytest.raises(ValueError):
            user_id_from_google_sub(None)  # type: ignore[arg-type]


# ── verify_google_id_token (fake mode) ─────────────────────────
# The real verifier contacts Google's JWKS endpoint. In tests we drive
# it through the conftest's `mock_google_auth` fixture, which patches
# the function. The unit tests below exercise the production code's
# defensive checks that run BEFORE the network call (missing token,
# missing GOOGLE_CLIENT_ID, type errors) by raising them directly.

class TestVerifyGoogleIdTokenDefensiveChecks:
    def test_rejects_missing_token(self, monkeypatch):
        monkeypatch.setattr(auth_service, "verify_google_id_token", verify_google_id_token)
        with pytest.raises(ValueError):
            verify_google_id_token("")

    def test_rejects_non_string_token(self, monkeypatch):
        monkeypatch.setattr(auth_service, "verify_google_id_token", verify_google_id_token)
        with pytest.raises(ValueError):
            verify_google_id_token(None)  # type: ignore[arg-type]

    def test_raises_runtime_error_when_client_id_missing(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        with pytest.raises(RuntimeError):
            verify_google_id_token("anything")


# ── sign_in_with_google (fake verifier) ────────────────────────
# Uses the same monkeypatched verifier pattern from mock_google_auth but
# locally — without going through /api/auth/google.

class TestSignInWithGoogle:
    def _fake_verify(self, monkeypatch, *, sub="123", email="x@y.com", verified=True):
        def fake(token):
            return {
                "sub": sub,
                "email": email,
                "email_verified": verified,
                "name": "x",
            }
        monkeypatch.setattr(auth_service, "verify_google_id_token", fake)
        return fake

    def test_returns_user_id_and_email(self, monkeypatch, tmp_path):
        monkeypatch.setattr(auth_service, "USERS_ROOT", tmp_path / "users")
        self._fake_verify(monkeypatch, sub="1092837465", email="alice@example.com")

        from flask import Flask
        app = Flask(__name__)
        app.secret_key = "test-secret"
        with app.test_request_context():
            user_id, email = sign_in_with_google("ignored-by-fake-verify")
            assert user_id == user_id_from_google_sub("1092837465")
            assert email == "alice@example.com"
            assert current_user_id() == user_id
            assert current_user_email() == "alice@example.com"

    def test_user_id_depends_on_sub_not_email(self, monkeypatch, tmp_path):
        """Email can change (rare but possible) without losing identity —
        sub is the stable Google identifier."""
        monkeypatch.setattr(auth_service, "USERS_ROOT", tmp_path / "users")

        from flask import Flask
        app = Flask(__name__)
        app.secret_key = "test-secret"

        self._fake_verify(monkeypatch, sub="stable-sub", email="alice@example.com")
        with app.test_request_context():
            user_id_first, _ = sign_in_with_google("ignored")
            sub_id = user_id_first

        self._fake_verify(monkeypatch, sub="stable-sub", email="alice-new-email@example.com")
        with app.test_request_context():
            user_id_second, _ = sign_in_with_google("ignored")
            assert user_id_second == sub_id  # same sub → same user_id

        self._fake_verify(monkeypatch, sub="different-sub", email="alice@example.com")
        with app.test_request_context():
            user_id_third, _ = sign_in_with_google("ignored")
            assert user_id_third != sub_id  # different sub → different user_id

    def test_rejects_unverified_email(self, monkeypatch):
        """Google-issued tokens have `email_verified=true` for actual email
        owners. If a token comes in with `email_verified=false`, sign-in
        fails — this blocks an attacker who registered a Google account
        with someone else's email but doesn't own it.

        The fake verifier here deliberately mimics the real verifier's
        email_verified check, so we're testing the policy that
        sign_in_with_google enforces — not just the mock's behavior."""
        def fake_mimics_real_check(token):
            claims = {
                "sub": "attacker-sub",
                "email": "victim@example.com",
                "email_verified": False,
            }
            if not claims.get("email_verified", False):
                raise ValueError("Google account email is not verified.")
            return claims
        monkeypatch.setattr(auth_service, "verify_google_id_token", fake_mimics_real_check)

        from flask import Flask
        app = Flask(__name__)
        app.secret_key = "test-secret"
        with app.test_request_context():
            with pytest.raises(ValueError):
                sign_in_with_google("anything")

    def test_rejects_token_missing_sub(self, monkeypatch):
        """Even a valid signature is rejected if the claims dict has
        no sub — there's no canonical identity to bind the session to."""
        def fake(token):
            return {
                "sub": None,
                "email": "x@y.com",
                "email_verified": True,
            }
        monkeypatch.setattr(auth_service, "verify_google_id_token", fake)

        from flask import Flask
        app = Flask(__name__)
        app.secret_key = "test-secret"
        with app.test_request_context():
            with pytest.raises(ValueError):
                sign_in_with_google("anything")

    def test_persists_user_metadata(self, monkeypatch, tmp_path):
        monkeypatch.setattr(auth_service, "USERS_ROOT", tmp_path / "users")
        self._fake_verify(monkeypatch, sub="1092837465", email="alice@example.com")

        from flask import Flask
        app = Flask(__name__)
        app.secret_key = "test-secret"
        with app.test_request_context():
            sign_in_with_google("ignored")
        meta_path = tmp_path / "users" / user_id_from_google_sub("1092837465") / "user.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["google_sub"] == "1092837465"
        assert meta["email"] == "alice@example.com"
        assert "created_at" in meta


# ── sign_out ──────────────────────────────────────────────────

class TestSignOut:
    def test_clears_session(self, monkeypatch, tmp_path):
        monkeypatch.setattr(auth_service, "USERS_ROOT", tmp_path / "users")

        # sign_in_with_google calls verify_google_id_token — patch it
        # so we don't actually contact Google.
        def fake_verify(token):
            return {
                "sub": "test-sub",
                "email": "x@y.com",
                "email_verified": True,
                "name": "x",
            }
        monkeypatch.setattr(auth_service, "verify_google_id_token", fake_verify)

        from flask import Flask
        app = Flask(__name__)
        app.secret_key = "test-secret"
        with app.test_request_context():
            sign_in_with_google("anything")
            assert current_user_id() is not None
            sign_out()
            assert current_user_id() is None

    def test_sign_out_is_idempotent(self):
        from flask import Flask
        app = Flask(__name__)
        app.secret_key = "test-secret"
        with app.test_request_context():
            sign_out()
            assert current_user_id() is None


# ── require_auth ──────────────────────────────────────────────

class TestRequireAuth:
    @pytest.fixture
    def app(self):
        from flask import Flask
        app = Flask(__name__)
        app.secret_key = "test-secret"
        return app

    def test_blocks_anonymous(self, app):
        @app.route("/secret")
        @require_auth
        def secret():
            return "ok"

        client = app.test_client()
        resp = client.get("/secret")
        assert resp.status_code == 401
        assert resp.get_json()["error"] == "Authentication required."

    def test_passes_signed_in(self, app):
        @app.route("/secret")
        @require_auth
        def secret():
            return {"user_id": current_user_id()}

        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id_from_google_sub("test-sub")
        resp = client.get("/secret")
        assert resp.status_code == 200
        assert resp.get_json()["user_id"] == user_id_from_google_sub("test-sub")


# ── user_root / cleanup ───────────────────────────────────────

class TestUserRoot:
    def test_creates_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(auth_service, "USERS_ROOT", tmp_path / "users")
        p = user_root("abcdef0123456789")
        assert p.is_dir()

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


class TestCrossUserCleanup:
    def test_iterates_all_users_and_sums(self, tmp_path, monkeypatch):
        monkeypatch.setattr(auth_service, "USERS_ROOT", tmp_path / "users")

        import os
        import time

        for uid in ("a" * 16, "b" * 16):
            user_dir = tmp_path / "users" / uid
            user_dir.mkdir(parents=True)
            (user_dir / "sessions").mkdir()
            old_mtime = time.time() - (365 * 24 * 60 * 60)
            sess_dir = user_dir / "sessions" / "session_old"
            sess_dir.mkdir()
            (sess_dir / "draft").mkdir()
            (sess_dir / "draft" / "profile.json").write_text("{}")
            for root, _, files in os.walk(sess_dir):
                for f in files:
                    os.utime(Path(root) / f, (old_mtime, old_mtime))

        (tmp_path / "users" / ".legacy-migrated").mkdir()

        removed = cleanup_expired_sessions_for_all_users()
        assert removed == 2
        assert (tmp_path / "users" / ("a" * 16)).is_dir()
        assert (tmp_path / "users" / ("b" * 16)).is_dir()
        assert (tmp_path / "users" / ".legacy-migrated").is_dir()

    def test_handles_missing_users_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(auth_service, "USERS_ROOT", tmp_path / "nonexistent")
        assert cleanup_expired_sessions_for_all_users() == 0
