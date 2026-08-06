"""Shared pytest fixtures for the BuildR test suite."""
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

    No sign-in required — the app operates in single-user mode with
    DEFAULT_USER_ID.
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
        yield client
