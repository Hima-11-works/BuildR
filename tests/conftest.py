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

# ai_service.py reads GEMINI_API_KEY lazily (only when a Gemini call is
# actually made), but set a dummy value up front so importing it — or
# importing app.py, which imports it — never fails during collection.
os.environ.setdefault("GEMINI_API_KEY", "test-dummy-key")

import pytest


@pytest.fixture
def isolated_resume_library(tmp_path, monkeypatch):
    """Point services.resume_library at a throwaway directory for this test."""
    import services.resume_library as resume_library
    resume_dir = tmp_path / "resumes"
    monkeypatch.setattr(resume_library, "RESUMES_DIR", resume_dir)
    return resume_library


@pytest.fixture
def isolated_session_service(tmp_path, monkeypatch):
    """Point services.session_service at a throwaway directory for this test."""
    import services.session_service as session_service
    sessions_dir = tmp_path / "sessions"
    monkeypatch.setattr(session_service, "SESSIONS_DIR", sessions_dir)
    return session_service


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    """Point services.storage_service at a throwaway profile.json for this test."""
    import services.storage_service as storage_service
    profile_path = tmp_path / "storage" / "profile.json"
    monkeypatch.setattr(storage_service, "PROFILE_PATH", profile_path)
    return storage_service


@pytest.fixture
def app_client(isolated_resume_library, isolated_session_service, isolated_storage, monkeypatch, tmp_path):
    """
    A Flask test client wired to fully isolated storage — never touches the
    real storage/ directory. Also patches app.py's own references to the
    resume-library/session-service modules, since app.py imports specific
    names from them at import time.
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
    # app_module.session_service IS services.session_service (same module
    # object) — isolated_session_service already redirected its SESSIONS_DIR,
    # so app.py's session_service.* calls automatically use the tmp dir too.

    app_module.app.config.update(TESTING=True)
    with app_module.app.test_client() as client:
        yield client
