"""
Flask route tests using isolated storage (see tests/conftest.py's app_client
fixture) — never touches the project's real storage/ directory.
"""
import shutil
from pathlib import Path

import pytest

from services.pdf_service import _find_tectonic

_tectonic_available = shutil.which("tectonic") is not None or Path(_find_tectonic()).exists()
requires_tectonic = pytest.mark.skipif(not _tectonic_available, reason="Tectonic is not installed")


def test_index_page_loads(app_client):
    resp = app_client.get("/")
    assert resp.status_code == 200


def test_index_page_home_and_history_markup(app_client):
    """
    Regression test for the home-page/History-page restructure: the primary
    home action button was renamed and gained a conditional "Master Resume
    Available" tag, a History preview section was added below the two home
    buttons, the builder page's full Resume Library section was replaced
    with a redirect card, and a dedicated #view-history page (mirroring the
    old Resume Library section) was added.
    """
    resp = app_client.get("/")
    html = resp.get_data(as_text=True)

    assert "Update Personal Information" in html
    assert "Build Your Resume" not in html
    assert 'id="home-master-tag"' in html

    assert 'id="home-history-list"' in html
    assert 'id="btn-see-all-history"' in html

    assert 'id="btn-goto-history"' in html
    # The full library toolbar (search/filter) must exist exactly once now —
    # on the History page — not duplicated on the builder page anymore.
    assert html.count('id="library-search-input"') == 1
    assert html.count('id="resume-library-list"') == 1

    assert 'id="view-history"' in html


def test_tailor_page_has_resume_library_redirect(app_client):
    """The Tailor Your Resume page gets the same Resume Library redirect
    card as the builder page, linking to #history via its own unique id
    (btn-goto-history is already used on the builder page)."""
    resp = app_client.get("/")
    html = resp.get_data(as_text=True)
    assert 'id="btn-goto-history-tailor"' in html
    assert html.count('id="btn-goto-history-tailor"') == 1


def test_home_page_has_footer_credit(app_client):
    resp = app_client.get("/")
    html = resp.get_data(as_text=True)
    assert "Built by" in html
    assert "Himanshi Saxena" in html
    assert 'href="https://www.linkedin.com/in/himanshi-saxena-6094bb326/"' in html
    assert 'rel="noopener noreferrer"' in html


def test_favicon_ico_served_at_root(app_client):
    resp = app_client.get("/favicon.ico")
    assert resp.status_code == 200
    assert resp.data[:4] == b"\x00\x00\x01\x00"  # ICO file magic bytes


def test_index_page_references_favicon_links(app_client):
    resp = app_client.get("/")
    html = resp.get_data(as_text=True)
    assert "favicon.svg" in html
    assert "favicon-32x32.png" in html
    assert "apple-touch-icon.png" in html


def test_get_profile_on_empty_storage(app_client):
    resp = app_client.get("/api/profile")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["has_valid_resume"] is False


def test_put_profile_validation_error(app_client):
    resp = app_client.put("/api/profile", json={"personal_info": {"name": "Jane"}})  # missing required email
    assert resp.status_code == 422
    assert "errors" in resp.get_json()


def test_put_then_get_profile_roundtrip(app_client):
    payload = {
        "personal_info": {"name": "Jane Doe", "email": "jane@example.com", "phone": None, "links": {}},
        "education": [], "experience": [], "projects": [],
        "skills": {"categories": {}}, "certifications": [], "achievements": "",
    }
    put_resp = app_client.put("/api/profile", json=payload)
    assert put_resp.status_code == 200
    assert put_resp.get_json()["status"] == "ok"

    get_resp = app_client.get("/api/profile")
    data = get_resp.get_json()
    assert data["personal_info"]["name"] == "Jane Doe"
    assert data["has_valid_resume"] is True


def test_generate_master_resume_without_name_returns_400(app_client):
    resp = app_client.post("/api/resume/master")
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_delete_resume_path_traversal_blocked(app_client):
    resp = app_client.delete("/api/resumes/..%2F..%2Fetc")
    assert resp.status_code in (400, 404)


def test_download_nonexistent_resume_pdf_404s(app_client):
    resp = app_client.get("/api/resumes/20260101-000000_nonexistent/pdf")
    assert resp.status_code == 404


def test_tailor_get_draft_missing_session_404s(app_client):
    resp = app_client.get("/api/tailor/draft/session_does_not_exist_000000_abcdef")
    assert resp.status_code == 404


@requires_tectonic
class TestRequiresTectonic:
    def test_generate_master_resume_end_to_end(self, app_client):
        payload = {
            "personal_info": {"name": "Jane Doe", "email": "jane@example.com", "phone": None, "links": {}},
            "education": [], "experience": [], "projects": [],
            "skills": {"categories": {}}, "certifications": [], "achievements": "",
        }
        app_client.put("/api/profile", json=payload)

        resp = app_client.post("/api/resume/master")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        resume_id = data["id"]

        pdf_resp = app_client.get(f"/api/resumes/{resume_id}/pdf")
        assert pdf_resp.status_code == 200
        assert pdf_resp.data[:5] == b"%PDF-"

    def test_regenerating_master_resume_does_not_duplicate_library_entry(self, app_client):
        """Regression test for AUDIT.md 2.3, exercised through the real HTTP route."""
        payload = {
            "personal_info": {"name": "Jane Doe", "email": "jane@example.com", "phone": None, "links": {}},
            "education": [], "experience": [], "projects": [],
            "skills": {"categories": {}}, "certifications": [], "achievements": "",
        }
        app_client.put("/api/profile", json=payload)

        for _ in range(3):
            resp = app_client.post("/api/resume/master")
            assert resp.status_code == 200

        list_resp = app_client.get("/api/resumes")
        resumes = list_resp.get_json()
        masters = [r for r in resumes if r["type"] == "master"]
        assert len(masters) == 1

    def test_snapshot_preview_endpoint_does_not_500_on_missing_json_import(
        self, app_client, isolated_session_service,
    ):
        """
        Regression test for AUDIT.md 1.1: api_tailor_preview()'s snapshot
        branch calls json.load() — app.py used to be missing `import json`
        entirely, so every snapshot preview 500'd with NameError. This test
        seeds a real session/draft/snapshot on disk (bypassing the Gemini
        call) and hits the actual preview route to prove it now returns a
        real PDF instead of crashing.
        """
        from models.profile import Profile, PersonalInfo
        from services.auth_service import user_id_from_email

        ss = isolated_session_service
        profile = Profile(personal_info=PersonalInfo(name="Jane Doe", email="jane@example.com"))
        # The auto-signed-in app_client uses test@example.com — derive the
        # matching user_id so the seeded session is visible to the route.
        user_id = user_id_from_email("test@example.com")
        session_id = ss.create_session(user_id, profile, {"job_description": "A job"})
        ss.update_draft(user_id, session_id, profile, {"suggestions": []})
        snapshot_id = ss.save_snapshot(user_id, session_id, "First Draft")

        resp = app_client.get(f"/api/tailor/preview/{session_id}/snapshot/{snapshot_id}")
        assert resp.status_code == 200
        assert resp.data[:5] == b"%PDF-"

    def test_rename_and_duplicate_resume_routes(self, app_client):
        payload = {
            "personal_info": {"name": "Jane Doe", "email": "jane@example.com", "phone": None, "links": {}},
            "education": [], "experience": [], "projects": [],
            "skills": {"categories": {}}, "certifications": [], "achievements": "",
        }
        app_client.put("/api/profile", json=payload)
        create_resp = app_client.post("/api/resume/master")
        resume_id = create_resp.get_json()["id"]

        rename_resp = app_client.patch(f"/api/resumes/{resume_id}", json={"label": "My Renamed Master"})
        assert rename_resp.status_code == 200
        assert rename_resp.get_json()["label"] == "My Renamed Master"

        list_resp = app_client.get("/api/resumes")
        assert list_resp.get_json()[0]["label"] == "My Renamed Master"
        assert list_resp.get_json()[0]["id"] == resume_id  # id/download links unchanged

        dup_resp = app_client.post(f"/api/resumes/{resume_id}/duplicate")
        assert dup_resp.status_code == 200
        new_id = dup_resp.get_json()["id"]
        assert new_id != resume_id

        pdf_resp = app_client.get(f"/api/resumes/{new_id}/pdf")
        assert pdf_resp.status_code == 200
        assert pdf_resp.data[:5] == b"%PDF-"

    def test_rename_missing_label_returns_400(self, app_client):
        resp = app_client.patch("/api/resumes/20260101-000000_nope", json={})
        assert resp.status_code == 400

    def test_rename_nonexistent_resume_404s(self, app_client):
        resp = app_client.patch("/api/resumes/20260101-000000_nope", json={"label": "New"})
        assert resp.status_code == 404

    def test_duplicate_nonexistent_resume_404s(self, app_client):
        resp = app_client.post("/api/resumes/20260101-000000_nope/duplicate")
        assert resp.status_code == 404
