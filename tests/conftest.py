"""Shared pytest fixtures for the BuildR test suite."""
import os
import sys
from pathlib import Path

# Default test password. Tests that want to verify the wrong-password
# rejection path mint their own client with a different password.
DEFAULT_TEST_PASSWORD = "test-password-123"

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
# Skip the legacy-storage migration so test runs don't accidentally touch
# any pre-existing single-user storage on a developer's machine.
os.environ.setdefault("BUILDR_SKIP_LEGACY_MIGRATION", "1")

import pytest


# ── Per-test fixtures ─────────────────────────────────────────
# All three storage layers (resume_library, session_service, storage_service)
# route through services.auth_service.user_root(), which reads USERS_ROOT at
# call time. We patch USERS_ROOT to a per-test tmp directory so the entire
# per-user tree is isolated.

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
    monkeypatch,
):
    """
    A Flask test client wired to fully isolated storage — never touches the
    real storage/users/ directory.

    Auto-signs-in a default test user (test@example.com) before yielding so
    the existing data routes — all of which now require authentication —
    can be exercised without per-test boilerplate.

    The Flask session cookie is signed with the production app's secret
    key (whatever is set in app.py at import time), so we drive sign-in
    via the actual /api/auth/sign-in endpoint rather than poking
    `session` directly. That way the cookie configuration
    (HttpOnly, SameSite, Secure flags) is part of the test surface.
    """
    import app as app_module

    # Re-route the service modules' bound names to the same module objects
    # already isolated by isolated_users_root. These monkeypatches match
    # the explicit imports in app.py, so a "save_resume" call in app.py
    # lands on the patched module.
    monkeypatch.setattr(app_module, "save_resume", isolated_resume_library.save_resume)
    monkeypatch.setattr(app_module, "list_resumes", isolated_resume_library.list_resumes)
    monkeypatch.setattr(app_module, "get_resume_path", isolated_resume_library.get_resume_path)
    monkeypatch.setattr(app_module, "delete_resume", isolated_resume_library.delete_resume)
    monkeypatch.setattr(app_module, "delete_resumes_by_type", isolated_resume_library.delete_resumes_by_type)
    monkeypatch.setattr(app_module, "rename_resume", isolated_resume_library.rename_resume)
    monkeypatch.setattr(app_module, "duplicate_resume", isolated_resume_library.duplicate_resume)
    monkeypatch.setattr(app_module, "load_profile", isolated_storage.load_profile)
    monkeypatch.setattr(app_module, "save_profile", isolated_storage.save_profile)
    # session_service is referenced via `services.session_service as session_service`
    # in app.py — the alias points to the same module object that
    # isolated_session_service exposes, so its methods already see the
    # redirected USERS_ROOT. No additional patch needed.

    app_module.app.config.update(TESTING=True)

    with app_module.app.test_client() as client:
        # Sign up + sign in a default test user. The /api/auth/sign-up
        # endpoint is idempotent for legacy users (it attaches a password
        # hash if one is missing), so it's safe to call even if the user
        # already exists from a previous test in the same process.
        _sign_up_and_in(
            client,
            email="test@example.com",
            password=DEFAULT_TEST_PASSWORD,
        )
        yield client


def _sign_up_and_in(client, *, email, password):
    """
    Helper used by both app_client fixtures. Calls sign-up (idempotent
    if the user already exists) and then sign-in. Returns True on
    success; raises AssertionError if either step fails so a
    misconfigured fixture produces a clear test failure.
    """
    su = client.post("/api/auth/sign-up", json={"email": email, "password": password})
    # 200 = created, 409 = already exists (still need to sign in)
    assert su.status_code in (200, 409), f"Sign-up failed: {su.get_json()}"
    si = client.post("/api/auth/sign-in", json={"email": email, "password": password})
    assert si.status_code == 200, f"Sign-in failed: {si.get_json()}"


@pytest.fixture
def app_client_factory(
    isolated_users_root,
    isolated_resume_library,
    isolated_session_service,
    isolated_storage,
    monkeypatch,
):
    """
    Factory fixture for tests that need multiple signed-in clients with
    distinct identities (the user-isolation suite). Returns a function
    that mints a fresh signed-in Flask test client for any email.
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

    def make_client(email: str, password: str = DEFAULT_TEST_PASSWORD):
        client = app_module.app.test_client()
        _sign_up_and_in(client, email=email, password=password)
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
