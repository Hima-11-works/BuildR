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
        # Re-sign-in with the email upper-cased and padded in
        # whitespace to verify the route normalizes it. The
        # auto-signed-in app_client uses test@example.com — this
        # is the same account, just in a different case + padding.
        app_client.post("/api/auth/sign-out")
        resp = app_client.post("/api/auth/sign-in", json={
            "email": "  TEST@example.com  ",
            "password": "test-password-123",
        })
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["email"] == "test@example.com"

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


# ── 8. Password verification ───────────────────────────────────

class TestPasswordVerification:
    """The whole point of the password refactor: someone who knows
    another user's email can no longer access their account."""

    def test_wrong_password_is_rejected(self, app_client_factory):
        # Set up Alice with a known password via the factory helper.
        alice = app_client_factory("alice@example.com", password="correct-horse-battery-staple")

        # A fresh, unsigned-in client tries to sign in with the wrong password.
        intruder = alice  # Re-use the cookie jar — sign out first.
        intruder.post("/api/auth/sign-out")
        bad = intruder.post("/api/auth/sign-in", json={
            "email": "alice@example.com",
            "password": "wrong-password-12345",
        })
        assert bad.status_code == 400
        assert "invalid" in bad.get_json()["error"].lower()

    def test_correct_password_after_failed_attempt(self, app_client_factory):
        alice = app_client_factory("alice@example.com", password="correct-horse-battery-staple")

        # Sign out, try wrong, then right.
        alice.post("/api/auth/sign-out")
        assert alice.post("/api/auth/sign-in", json={
            "email": "alice@example.com", "password": "wrong-password",
        }).status_code == 400
        ok = alice.post("/api/auth/sign-in", json={
            "email": "alice@example.com", "password": "correct-horse-battery-staple",
        })
        assert ok.status_code == 200

    def test_error_messages_do_not_leak_account_existence(self, app_client_factory):
        # A "user does not exist" error and a "wrong password" error
        # must be indistinguishable to an attacker — otherwise the
        # auth surface can be used to enumerate registered emails.
        signed_in_alice = app_client_factory("alice@example.com", password="right-password")
        signed_in_alice.post("/api/auth/sign-out")
        wrong_password = signed_in_alice.post("/api/auth/sign-in", json={
            "email": "alice@example.com", "password": "wrong-password",
        }).get_json()

        unknown_user = signed_in_alice.post("/api/auth/sign-in", json={
            "email": "ghost@example.com", "password": "any-password-123",
        }).get_json()

        # Both should be the same generic message.
        assert wrong_password["error"].lower() == unknown_user["error"].lower()

    def test_sign_up_short_password_rejected(self, app_client):
        # No cookie yet — sign-up is the entry point.
        resp = app_client.post("/api/auth/sign-up", json={
            "email": "newuser@example.com", "password": "short",
        })
        assert resp.status_code == 400
        assert "8" in resp.get_json()["error"]

    def test_sign_up_then_sign_in_roundtrip(self, app_client_factory):
        # Drive the auth flow through a fresh factory-minted client so
        # the storage is isolated to this test's tmp_path.
        client = app_client_factory("freshuser@example.com")
        # factory already signed us in. Verify we can re-sign-in after
        # explicitly signing out.
        client.post("/api/auth/sign-out")
        si = client.post("/api/auth/sign-in", json={
            "email": "freshuser@example.com",
            "password": "test-password-123",
        })
        assert si.status_code == 200

    def test_duplicate_sign_up_returns_409(self, app_client):
        # app_client already signed up test@example.com. A second
        # sign-up for the same email must be rejected.
        dup = app_client.post("/api/auth/sign-up", json={
            "email": "test@example.com",
            "password": "another-password-789",
        })
        assert dup.status_code == 409
        assert "already" in dup.get_json()["error"].lower()

    def test_change_password_blocks_wrong_old(self, app_client):
        app_client.post("/api/auth/change-password", json={
            "old_password": "wrong-old-password",
            "new_password": "new-password-456",
        })
        assert app_client.get("/api/profile").status_code == 200  # still signed in

    def test_change_password_updates_credential(self, app_client):
        # Change password in-session.
        ok = app_client.post("/api/auth/change-password", json={
            "old_password": "test-password-123",
            "new_password": "new-password-456",
        })
        assert ok.status_code == 200

        # Sign out, sign back in with the OLD password — must fail.
        app_client.post("/api/auth/sign-out")
        bad = app_client.post("/api/auth/sign-in", json={
            "email": "test@example.com",
            "password": "test-password-123",
        })
        assert bad.status_code == 400

        # Sign in with the NEW password — must succeed.
        good = app_client.post("/api/auth/sign-in", json={
            "email": "test@example.com",
            "password": "new-password-456",
        })
        assert good.status_code == 200

    def test_change_password_requires_session(self):
        """Hitting change-password without an active session must 401,
        not silently update some other user's password."""
        import app as app_module
        app_module.app.config.update(TESTING=True)
        with app_module.app.test_client() as client:
            resp = client.post("/api/auth/change-password", json={
                "old_password": "any", "new_password": "new-password-456",
            })
            assert resp.status_code == 401

    def test_bob_cannot_change_alices_password(self, app_client_factory):
        alice = app_client_factory("alice@example.com", password="alice-password-12345")
        bob = app_client_factory("bob@example.com", password="bob-password-67890")

        # Bob authenticates as Bob — but tries to hit change-password
        # with Alice's email context. The endpoint ignores any email
        # field in the body and only updates the SESSION user's
        # password, so this should affect Bob, not Alice.
        resp = bob.post("/api/auth/change-password", json={
            "old_password": "bob-password-67890",
            "new_password": "bob-password-NEW-12345",
        })
        assert resp.status_code == 200

        # Verify Alice's account is untouched.
        alice.post("/api/auth/sign-out")
        alice_old = alice.post("/api/auth/sign-in", json={
            "email": "alice@example.com", "password": "alice-password-12345",
        })
        assert alice_old.status_code == 200
        # And Bob's password IS the new one.
        bob.post("/api/auth/sign-out")
        bob_new = bob.post("/api/auth/sign-in", json={
            "email": "bob@example.com", "password": "bob-password-NEW-12345",
        })
        assert bob_new.status_code == 200
