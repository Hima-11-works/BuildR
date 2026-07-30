"""Shared pytest fixtures for the BuildR test suite.

The auth model is Google OAuth. Tests cannot reach a real Google token,
so the `mock_google_auth` fixture monkeypatches `verify_google_id_token`
to a deterministic fake that accepts any token of the form
``"test:<sub>:<email>"`` and returns those claims. Tests then POST
fake tokens to /api/auth/google to drive the real auth flow — including
the cookie signing, session creation, and per-user storage scoping.

The SECURITY invariant we care about — that knowing an email is never
sufficient for access — is exercised by `test_user_isolation.py`
in the dedicated attacker-with-victim-email test.
"""
import os
import sys
from pathlib import Path

# Make the project root importable regardless of how/where pytest is invoked
# from (there's no src/ layout here — models/ and services/ live at the repo
# root next to this tests/ folder).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ai_service.py reads MINIMAX_API_KEY lazily (only when an AI call is
# actually made), but set a dummy value up front so importing it — or
# importing app.py, which imports it — never fails during collection.
os.environ.setdefault("MINIMAX_API_KEY", "test-dummy-key")
# Provide a placeholder GOOGLE_CLIENT_ID so the auth module can be
# imported. The mock_google_auth fixture replaces the verifier itself,
# so this value is never actually checked against a real token.
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-google-client-id")
# Skip the legacy-storage migration so test runs don't accidentally touch
# any pre-existing single-user storage on a developer's machine.
os.environ.setdefault("BUILDR_SKIP_LEGACY_MIGRATION", "1")

import pytest


# ── Per-test fixtures ─────────────────────────────────────────
# All three storage layers (resume_library, session_service, storage_service)
# route through services.auth_service.user_root(), which reads USERS_ROOT at
# call time. We patch USERS_ROOT to a per-test tmp directory so the entire
# per-user tree is isolated.


def _make_fake_google_token(sub: str, email: str) -> str:
    """Build a token the mock verifier recognizes. The format is
    ``test:<sub>:<email>`` — never accidentally confused with a real
    Google JWT (which has three base64url-encoded JSON segments)."""
    return f"test:{sub}:{email}"


@pytest.fixture
def mock_google_auth(monkeypatch):
    """
    Replace `services.auth_service.verify_google_id_token` with a fake
    that recognizes any token of the form ``test:<sub>:<email>`` and
    returns those claims (with `email_verified=True`). Tests that need
    to simulate rejection can override the behavior by setting
    `mock_google_auth.reject = True` before exercising the route.

    SECURITY NOTE: This fixture short-circuits the real signature check
    that `google-auth` performs. It exists so tests can drive the rest
    of the auth flow (cookie signing, session creation, per-user
    storage scoping) without needing real Google credentials. The
    CRITICAL assertion — that knowing an email is not sufficient to
    gain access — is NOT covered by this fixture; it is exercised
    explicitly by `test_user_isolation.py::TestAttackerWithVictimEmail`.
    """
    import services.auth_service as auth_service

    def fake_verify(token: str) -> dict:
        if not isinstance(token, str) or not token.startswith("test:"):
            raise ValueError("Unrecognized test token (mock verifier only).")
        parts = token.split(":", 2)
        if len(parts) != 3:
            raise ValueError("Malformed test token; expected test:<sub>:<email>.")
        _, sub, email = parts
        if not sub or not email:
            raise ValueError("Test token must include non-empty sub and email.")
        return {
            "sub": sub,
            "email": email,
            "email_verified": True,
            "name": email.split("@", 1)[0],
        }

    monkeypatch.setattr(auth_service, "verify_google_id_token", fake_verify)
    return _make_fake_google_token


@pytest.fixture
def isolated_users_root(tmp_path, monkeypatch):
    """Point auth_service.USERS_ROOT at a per-test directory. All per-user
    storage helpers (storage_service, resume_library, session_service)
    derive their paths from this, so patching it once isolates everything."""
    import services.auth_service as auth_service
    users_root = tmp_path / "users"
    monkeypatch.setattr(auth_service, "USERS_ROOT", users_root)
    return users_root


@pytest.fixture
def isolated_resume_library(isolated_users_root):
    """Re-export services.resume_library with USERS_ROOT redirected to tmp."""
    import services.resume_library as resume_library
    return resume_library


@pytest.fixture
def isolated_session_service(isolated_users_root):
    """Re-export services.session_service with USERS_ROOT redirected to tmp."""
    import services.session_service as session_service
    return session_service


@pytest.fixture
def isolated_storage(isolated_users_root):
    """Re-export services.storage_service with USERS_ROOT redirected to tmp."""
    import services.storage_service as storage_service
    return storage_service


@pytest.fixture
def app_client(
    isolated_users_root,
    isolated_resume_library,
    isolated_session_service,
    isolated_storage,
    mock_google_auth,
    monkeypatch,
):
    """
    A Flask test client wired to fully isolated storage — never touches the
    real storage/users/ directory.

    Auto-signs-in a default test user via the real /api/auth/google
    endpoint with a fake Google token. This drives the cookie signing,
    session creation, and per-user storage scoping through the actual
    route — so the cookie config (HttpOnly, SameSite, Secure) is part
    of the test surface.
    """
    import app as app_module

    # Re-route the service modules' bound names to the same module objects
    # already isolated by isolated_users_root.
    monkeypatch.setattr(app_module, "save_resume", isolated_resume_library.save_resume)
    monkeypatch.setattr(app_module, "list_resumes", isolated_resume_library.list_resumes)
    monkeypatch.setattr(app_module, "get_resume_path", isolated_resume_library.get_resume_path)
    monkeypatch.setattr(app_module, "delete_resume", isolated_resume_library.delete_resume)
    monkeypatch.setattr(app_module, "delete_resumes_by_type", isolated_resume_library.delete_resumes_by_type)
    monkeypatch.setattr(app_module, "rename_resume", isolated_resume_library.rename_resume)
    monkeypatch.setattr(app_module, "duplicate_resume", isolated_resume_library.duplicate_resume)
    monkeypatch.setattr(app_module, "load_profile", isolated_storage.load_profile)
    monkeypatch.setattr(app_module, "save_profile", isolated_storage.save_profile)

    app_module.app.config.update(TESTING=True)

    with app_module.app.test_client() as client:
        # Auto-sign-in a default user. Existing tests don't care who the
        # user is, only that *some* signed-in session exists so the
        # @require_auth decorator lets them through.
        token = mock_google_auth(sub="default-test-sub", email="test@example.com")
        resp = client.post("/api/auth/google", json={"id_token": token})
        assert resp.status_code == 200, f"Auto-sign-in failed: {resp.get_json()}"
        yield client


@pytest.fixture
def app_client_factory(
    isolated_users_root,
    isolated_resume_library,
    isolated_session_service,
    isolated_storage,
    mock_google_auth,
    monkeypatch,
):
    """
    Factory fixture for tests that need multiple signed-in clients with
    distinct Google identities (the user-isolation suite). Returns a
    function that mints a fresh signed-in Flask test client for any email.

    Each call uses a unique fake `sub` so the resulting user_ids are
    distinct — the email is for display only, identity is the sub.
    """
    import app as app_module
    monkeypatch.setattr(app_module, "save_resume", isolated_resume_library.save_resume)
    monkeypatch.setattr(app_module, "list_resumes", isolated_resume_library.list_resumes)
    monkeypatch.setattr(app_module, "get_resume_path", isolated_resume_library.get_resume_path)
    monkeypatch.setattr(app_module, "delete_resume", isolated_resume_library.delete_resume)
    monkeypatch.setattr(app_module, "delete_resumes_by_type", isolated_resume_library.delete_resumes_by_type)
    monkeypatch.setattr(app_module, "rename_resume", isolated_resume_library.rename_resume)
    monkeypatch.setattr(app_module, "duplicate_resume", isolated_resume_library.duplicate_resume)
    monkeypatch.setattr(app_module, "load_profile", isolated_storage.load_profile)
    monkeypatch.setattr(app_module, "save_profile", isolated_storage.save_profile)

    app_module.app.config.update(TESTING=True)

    def make_client(email: str):
        # Derive a stable but distinct fake sub from the email.
        sub = "fake-sub-" + email.replace("@", "-at-").replace(".", "-")
        client = app_module.app.test_client()
        token = mock_google_auth(sub=sub, email=email)
        resp = client.post("/api/auth/google", json={"id_token": token})
        assert resp.status_code == 200, f"Sign-in failed for {email}: {resp.get_json()}"
        return client

    yield make_client


@pytest.fixture(autouse=True)
def _sign_out_after_each_test():
    """
    Defensive: after each test, clear the test client session so that
    cookies don't accidentally carry across tests via shared Flask app
    state. This is belt-and-suspenders alongside the per-test
    `with app.test_client()` context, which already isolates cookies.
    """
    yield
    # No-op teardown; the test_client context manager handles isolation.
