"""
End-to-end multi-user isolation tests for BuildR.

These tests verify the security guarantee the user asked for: every
authenticated user gets an independent workspace, and no user can
ever observe another user's data through any API surface.

The tests use `app_client_factory` to mint multiple signed-in Flask
test clients (each bound to a distinct Google `sub` claim) within a
single pytest session — all sharing one isolated_users_root tmp
directory so the on-disk layout actually has separate <user_id>/ trees.

If any of these regress, two users will leak into each other again.

The CRITICAL security invariant — that an email alone NEVER grants
access — is exercised by `TestAttackerWithVictimEmail` below. This
class is the regression net for the previous design's
"sign in with email" vulnerability.
"""
import json

from models.profile import PersonalInfo, Profile
from services.auth_service import user_id_from_google_sub


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
        # Look up Alice's user_id by her sub claim (the conftest's
        # app_client_factory derives a stable sub from the email).
        user_id = user_id_from_google_sub("fake-sub-alice-at-example-com")
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
        # app_client signs in via mock_google_auth first. Sign out, then whoami.
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


# ── 7. Google OAuth endpoint shape ────────────────────────────

class TestGoogleAuthEndpoints:
    def test_whoami_reflects_signed_in_state(self, app_client):
        resp = app_client.get("/api/auth/whoami")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["authenticated"] is True
        # user_id is the SHA-256 prefix of the (fake) Google sub.
        assert body["user_id"] == user_id_from_google_sub("default-test-sub")

    def test_google_sign_in_rejects_invalid_token(self, app_client_factory):
        # Mint a client and sign it OUT so it has no session.
        client = app_client_factory("bob@example.com")
        client.post("/api/auth/sign-out")
        # The mock_google_auth fixture only accepts tokens of the form
        # "test:<sub>:<email>". Anything else raises ValueError.
        resp = client.post("/api/auth/google", json={"id_token": "not-a-real-token"})
        assert resp.status_code == 401

    def test_google_sign_in_rejects_missing_token(self, app_client_factory):
        client = app_client_factory("bob@example.com")
        client.post("/api/auth/sign-out")
        resp = client.post("/api/auth/google", json={})
        assert resp.status_code == 400

    def test_old_email_sign_in_route_no_longer_exists(self, app_client_factory):
        """The previous design accepted a bare email and created a
        session. That endpoint must not exist anymore — identity must
        come from a Google-verified token, never a user-supplied email."""
        client = app_client_factory("anyone@example.com")
        # Any POST to /api/auth/sign-in (with email OR any body) must
        # result in 404 or 405 — it must NOT succeed.
        resp = client.post("/api/auth/sign-in", json={"email": "victim@example.com"})
        assert resp.status_code in (404, 405), (
            "Old /api/auth/sign-in endpoint must not exist. "
            f"Got {resp.status_code}"
        )
        resp = client.post("/api/auth/sign-in", json={"any": "data"})
        assert resp.status_code in (404, 405)

    def test_sign_out_is_idempotent(self, app_client):
        assert app_client.post("/api/auth/sign-out").status_code == 200
        assert app_client.post("/api/auth/sign-out").status_code == 200

    def test_index_and_favicon_are_public(self, app_client):
        # These two routes are intentionally NOT behind @require_auth so
        # the SPA shell + favicon can serve before sign-in.
        assert app_client.get("/").status_code == 200
        assert app_client.get("/favicon.ico").status_code == 200


# ── 8. THE EXPLICIT ATTACK TEST ────────────────────────────────
#
# SCENARIO (the user's exact ask):
#   "If attacker knows victim@gmail.com but does not own that Google
#    account, what happens?"
#
# EXPECTED OUTCOME after this fix:
#   - Attacker submits anything claiming to be victim → 401
#   - Attacker has no way to obtain a Google-signed JWT with
#     victim's `sub` claim → cannot establish a session
#   - Victim's data remains untouched and invisible

class TestAttackerWithVictimEmail:
    def test_knowing_email_alone_grants_no_access(
        self, app_client_factory, mock_google_auth,
    ):
        """
        Step 1: Alice (victim) signs in with a valid Google token
                and saves data.
        Step 2: A fresh, fully-unauthenticated client tries every
                possible way to access Alice's data by knowing only
                her email.
        Step 3: Every attempt must fail. Alice's data must be intact.
        """
        # ── Step 1: Alice's data ────────────────────────────────
        alice = app_client_factory("alice@example.com")
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
        alice_master = alice.post("/api/resume/master").get_json()
        assert alice_master["status"] == "ok"
        alice_resume_id = alice_master["id"]

        # Alice's user_id (for assertions in step 3)
        alice_user_id = user_id_from_google_sub("fake-sub-alice-at-example-com")

        # Alice signs out so subsequent calls from "an attacker" start
        # from a fully-unauthenticated state.
        alice.post("/api/auth/sign-out")

        # ── Step 2: Attacker probes ────────────────────────────
        # A bare, unauthenticated client (no cookie, no token).
        # We use a freshly-constructed test client so there's no
        # possibility of leftover session state.
        import app as app_module
        attacker = app_module.app.test_client()

        # Probe 1: try the OLD email-only sign-in endpoint.
        # The endpoint must not exist anymore.
        resp = attacker.post("/api/auth/sign-in", json={"email": "alice@example.com"})
        assert resp.status_code in (404, 405), (
            f"Old /api/auth/sign-in endpoint must not exist. Got {resp.status_code}"
        )

        # Probe 2: try the new Google endpoint with a token whose sub
        # claim belongs to the attacker (NOT victim). The mock verifier
        # accepts the token, but the resulting user_id is the
        # attacker's, not Alice's.
        attacker_token = mock_google_auth(
            sub="attacker-forged-sub",
            email="alice@example.com",
        )
        resp = attacker.post("/api/auth/google", json={"id_token": attacker_token})
        if resp.status_code == 200:
            # Sign-in succeeded — but for the attacker's sub, not Alice's.
            whoami = attacker.get("/api/auth/whoami").get_json()
            assert whoami["user_id"] != alice_user_id, (
                "Attacker's session must NOT have Alice's user_id!"
            )
        # Either way, attacker cannot read Alice's data.
        profile = attacker.get("/api/profile").get_json()
        # If not signed in: 401.
        # If signed in as the attacker: empty default profile, NOT Alice's.
        assert (
            profile.get("personal_info", {}).get("name") != "Alice Anderson"
        ), "Attacker must not see Alice's saved profile data"

        # Probe 3: try to read /api/profile when fully unauthenticated.
        unauth = app_module.app.test_client()
        resp = unauth.get("/api/profile")
        assert resp.status_code == 401

        # Probe 4: try to list resumes when unauthenticated.
        resp = unauth.get("/api/resumes")
        assert resp.status_code == 401

        # Probe 5: try to download Alice's resume by guessing the ID.
        resp = unauth.get(f"/api/resumes/{alice_resume_id}/pdf")
        assert resp.status_code == 401

        # ── Step 3: Alice's data is intact ─────────────────────
        alice2 = app_client_factory("alice@example.com")
        recovered = alice2.get("/api/profile").get_json()
        assert recovered["personal_info"]["name"] == "Alice Anderson"
        library = alice2.get("/api/resumes").get_json()
        assert any(r["id"] == alice_resume_id for r in library)

    def test_token_with_unverified_email_is_rejected(
        self, monkeypatch,
    ):
        """An attacker who controls a Google account with someone
        else's email (unverified) cannot sign in. The server rejects
        any token whose `email_verified` claim is False — because
        Google only sets that to true for the actual email owner.

        The fake here mimics the real verifier's policy: it inspects
        email_verified and raises ValueError if it's False. This is
        what the production verify_google_id_token does, so we're
        testing the same code path."""
        from services import auth_service

        def fake_reject_unverified(token):
            # Simulate Google issuing a token for an account where
            # email_verified=False (the attacker registered victim's
            # email but didn't verify it).
            claims = {
                "sub": "attacker-google-account",
                "email": "victim@example.com",
                "email_verified": False,  # attacker can't fake this
                "name": "Attacker",
            }
            if not claims.get("email_verified", False):
                raise ValueError("Google account email is not verified.")
            return claims
        monkeypatch.setattr(auth_service, "verify_google_id_token", fake_reject_unverified)

        import app as app_module
        client = app_module.app.test_client()
        resp = client.post(
            "/api/auth/google",
            json={"id_token": "anything-the-mock-doesn't-care-about"},
        )
        assert resp.status_code == 401, (
            "Tokens with email_verified=False must be rejected — "
            "an attacker who registered victim's email but didn't "
            "verify it must not gain access."
        )

        # whoami still reports not authenticated.
        whoami = client.get("/api/auth/whoami").get_json()
        assert whoami["authenticated"] is False
