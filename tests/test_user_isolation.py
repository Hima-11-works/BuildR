"""
End-to-end multi-user isolation tests for BuildR.

These tests verify the security guarantee the user asked for: every
authenticated user gets an independent workspace, and no user can
ever observe another user's data through any API surface.

The tests use `app_client_factory` to mint multiple signed-in Flask
test clients (each bound to a distinct email) within a single pytest
session — all sharing one isolated_users_root tmp directory so the
on-disk layout actually has separate <user_id>/ trees.

If any of these regress, two users will leak into each other again.
"""
import json

from models.profile import PersonalInfo, Profile
from services.auth_service import user_id_from_email


# ── 1. Alice's profile is invisible to Bob ─────────────────────

class TestProfileIsolation:
    def test_alice_saves_bob_gets_empty_profile(self, app_client_factory):
        alice = app_client_factory("alice@example.com")
        bob = app_client_factory("bob@example.com")

        alice.put("/api/profile", json={
            "personal_info": {
                "name": "Alice Anderson",
                "email": "alice@example.com",
                "phone": None,
                "links": {},
            },
            "education": [], "experience": [], "projects": [],
            "skills": {"categories": {}}, "certifications": [],
            "achievements": "",
        })
        assert alice.get("/api/profile").get_json()["personal_info"]["name"] == "Alice Anderson"

        # Bob's profile is the empty default — Alice's data did not leak.
        bob_profile = bob.get("/api/profile").get_json()
        assert bob_profile["personal_info"]["name"] == ""
        assert bob_profile["personal_info"]["email"] == ""
        assert bob_profile["has_valid_resume"] is False

    def test_bob_saving_does_not_overwrite_alice(self, app_client_factory):
        alice = app_client_factory("alice@example.com")
        bob = app_client_factory("bob@example.com")

        alice.put("/api/profile", json={
            "personal_info": {"name": "Alice Anderson", "email": "alice@example.com",
                              "phone": None, "links": {}},
            "education": [], "experience": [], "projects": [],
            "skills": {"categories": {}}, "certifications": [], "achievements": "",
        })
        bob.put("/api/profile", json={
            "personal_info": {"name": "Bob Brown", "email": "bob@example.com",
                              "phone": None, "links": {}},
            "education": [], "experience": [], "projects": [],
            "skills": {"categories": {}}, "certifications": [], "achievements": "",
        })

        # Both clients still see their own data.
        assert alice.get("/api/profile").get_json()["personal_info"]["name"] == "Alice Anderson"
        assert bob.get("/api/profile").get_json()["personal_info"]["name"] == "Bob Brown"


# ── 2. Alice's resume library is invisible to Bob ───────────────

class TestResumeLibraryIsolation:
    def _save_master(self, client, name):
        client.put("/api/profile", json={
            "personal_info": {"name": name, "email": f"{name.lower().replace(' ', '')}@example.com",
                              "phone": None, "links": {}},
            "education": [], "experience": [], "projects": [],
            "skills": {"categories": {}}, "certifications": [], "achievements": "",
        })
        return client.post("/api/resume/master").get_json()

    def test_alice_resumes_invisible_to_bob(self, app_client_factory):
        alice = app_client_factory("alice@example.com")
        bob = app_client_factory("bob@example.com")

        alice_data = self._save_master(alice, "Alice Anderson")
        assert alice_data["status"] == "ok"
        alice_resume_id = alice_data["id"]

        # Alice sees her one master.
        alice_listing = alice.get("/api/resumes").get_json()
        assert len(alice_listing) == 1
        assert alice_listing[0]["id"] == alice_resume_id

        # Bob's listing is empty.
        bob_listing = bob.get("/api/resumes").get_json()
        assert bob_listing == []

    def test_bob_cannot_download_alices_resume(self, app_client_factory):
        alice = app_client_factory("alice@example.com")
        bob = app_client_factory("bob@example.com")

        alice_data = self._save_master(alice, "Alice Anderson")
        alice_resume_id = alice_data["id"]

        # Alice can fetch her own.
        assert alice.get(f"/api/resumes/{alice_resume_id}/pdf").status_code == 200
        # Bob cannot — the resume is not in his library.
        assert bob.get(f"/api/resumes/{alice_resume_id}/pdf").status_code == 404

    def test_bob_cannot_delete_alices_resume(self, app_client_factory):
        alice = app_client_factory("alice@example.com")
        bob = app_client_factory("bob@example.com")

        alice_data = self._save_master(alice, "Alice Anderson")
        alice_resume_id = alice_data["id"]

        # Bob's DELETE is a 404 (the resume does not exist in his namespace).
        assert bob.delete(f"/api/resumes/{alice_resume_id}").status_code == 404
        # Alice's resume is untouched.
        assert alice.get(f"/api/resumes/{alice_resume_id}/pdf").status_code == 200


# ── 3. Alice's tailoring session_id is unreachable by Bob ───────

class TestTailoringSessionIsolation:
    def test_bob_cannot_load_alice_session(self, app_client_factory):
        """Bob cannot GET Alice's tailoring draft by knowing her session_id,
        because session_id lookup is scoped per-user."""
        alice = app_client_factory("alice@example.com")
        bob = app_client_factory("bob@example.com")

        alice.put("/api/profile", json={
            "personal_info": {"name": "Alice", "email": "alice@example.com",
                              "phone": None, "links": {}},
            "education": [], "experience": [], "projects": [],
            "skills": {"categories": {}}, "certifications": [], "achievements": "",
        })

        # Build a session for Alice by going through her client.
        # We can't actually call /api/tailor/start (it would invoke the
        # LLM), so seed it directly via the session_service.
        from services import session_service
        user_id = user_id_from_email("alice@example.com")
        profile = Profile(personal_info=PersonalInfo(name="Alice", email="alice@example.com"))
        alice_session_id = session_service.create_session(
            user_id, profile, {"job_description": "Senior backend role"}
        )
        session_service.update_draft(user_id, alice_session_id, profile, {"v": 1})

        # Alice can load her own draft.
        assert alice.get(f"/api/tailor/draft/{alice_session_id}").status_code == 200

        # Bob trying the same session_id hits a 404 — the id only resolves
        # under Alice's user namespace.
        assert bob.get(f"/api/tailor/draft/{alice_session_id}").status_code == 404


# ── 4. Returning user recovers their own data ──────────────────

class TestReturningUser:
    def test_alice_returns_to_her_own_data(self, app_client_factory):
        """Alice signs in, saves, signs out, signs back in — she sees
        her saved data again. Bob's session in between doesn't pollute
        her workspace."""
        alice1 = app_client_factory("alice@example.com")

        alice1.put("/api/profile", json={
            "personal_info": {"name": "Alice Anderson", "email": "alice@example.com",
                              "phone": None, "links": {}},
            "education": [], "experience": [], "projects": [],
            "skills": {"categories": {}}, "certifications": [], "achievements": "",
        })

        # Alice signs out.
        signout = alice1.post("/api/auth/sign-out")
        assert signout.status_code == 200

        # Bob signs in and adds his own data — this should not touch
        # anything Alice already saved.
        bob = app_client_factory("bob@example.com")
        bob.put("/api/profile", json={
            "personal_info": {"name": "Bob Brown", "email": "bob@example.com",
                              "phone": None, "links": {}},
            "education": [], "experience": [], "projects": [],
            "skills": {"categories": {}}, "certifications": [], "achievements": "",
        })

        # Alice signs back in.
        alice2 = app_client_factory("alice@example.com")
        recovered = alice2.get("/api/profile").get_json()

        # Alice's saved data is intact.
        assert recovered["personal_info"]["name"] == "Alice Anderson"
        assert recovered["has_valid_resume"] is True

        # Bob's data is separate and intact.
        bob_recovered = bob.get("/api/profile").get_json()
        assert bob_recovered["personal_info"]["name"] == "Bob Brown"


# ── 5. Sign-out blocks subsequent API calls ────────────────────

class TestSignOutBlocksCalls:
    def test_sign_out_blocks_subsequent_calls(self, app_client_factory):
        alice = app_client_factory("alice@example.com")

        # Confirm we can hit a protected endpoint while signed in.
        assert alice.get("/api/profile").status_code == 200

        # Sign out, then try again — must be 401.
        assert alice.post("/api/auth/sign-out").status_code == 200
        assert alice.get("/api/profile").status_code == 401


# ── 6. Unauthenticated requests get 401 ────────────────────────

class TestAnonymousAccess:
    def test_whoami_when_signed_out(self, app_client):
        # app_client signs in test@example.com first. Sign out, then whoami.
        app_client.post("/api/auth/sign-out")
        resp = app_client.get("/api/auth/whoami")
        assert resp.status_code == 200
        assert resp.get_json() == {"authenticated": False}

    def test_protected_endpoints_require_auth(self, app_client_factory):
        # Mint a client, sign it OUT, then exercise protected endpoints.
        client = app_client_factory("transient@example.com")
        client.post("/api/auth/sign-out")

        # The route handlers all require auth — every one of these
        # should 401 without a session.
        protected = [
            ("GET", "/api/profile"),
            ("GET", "/api/resumes"),
            ("POST", "/api/resume/master"),
            ("POST", "/api/scrape-job"),
            ("POST", "/api/analyze-job"),
            ("POST", "/api/tailor/start"),
        ]
        for method, url in protected:
            resp = client.open(url, method=method, json={})
            assert resp.status_code == 401, f"{method} {url} returned {resp.status_code}"


# ── 7. Auth endpoint shape ─────────────────────────────────────

class TestAuthEndpoints:
    def test_sign_in_rejects_malformed_email(self, app_client):
        resp = app_client.post("/api/auth/sign-in", json={"email": "not-an-email"})
        assert resp.status_code == 400
        assert "valid email" in resp.get_json()["error"].lower()

    def test_sign_in_normalizes_case(self, app_client):
        resp = app_client.post("/api/auth/sign-in", json={"email": "  ALICE@example.com  "})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["email"] == "alice@example.com"

    def test_whoami_reflects_signed_in_state(self, app_client):
        resp = app_client.get("/api/auth/whoami")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["authenticated"] is True
        assert body["email"] == "test@example.com"
        assert body["user_id"] == user_id_from_email("test@example.com")

    def test_sign_out_is_idempotent(self, app_client):
        assert app_client.post("/api/auth/sign-out").status_code == 200
        assert app_client.post("/api/auth/sign-out").status_code == 200

    def test_index_and_favicon_are_public(self, app_client):
        # These two routes are intentionally NOT behind @require_auth so
        # the SPA shell + favicon can serve before sign-in.
        assert app_client.get("/").status_code == 200
        assert app_client.get("/favicon.ico").status_code == 200
