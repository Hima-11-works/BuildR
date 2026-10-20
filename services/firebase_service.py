# ──────────────────────────────────────────────────────────────
# services/firebase_service.py — Firebase Admin SDK initializer
# ──────────────────────────────────────────────────────────────
#
# WHAT THIS FILE DOES
# -------------------
# Initialises the Firebase Admin SDK once at startup using the
# environment variables loaded from .env by app.py (python-dotenv).
#
# The Admin SDK is the *server-side* Firebase library.  It lets the
# Flask backend perform privileged operations such as:
#   • Verifying Firebase ID tokens sent by the browser (auth)
#   • Reading / writing Firestore documents
#   • Sending FCM push notifications
#   • Managing Firebase Storage objects
#
# CLIENT CONFIG
# -------------
# Firebase's client-side SDK (loaded in the browser) also needs a
# config object that contains the public API key, project ID, etc.
# We build that dict here from the same env vars so there is a single
# source of truth.  The dict is exposed via `get_client_config()` and
# injected into index.html by the Flask index route as a Jinja2
# template variable (see app.py).
#
# IDEMPOTENCY
# -----------
# firebase_admin.initialize_app() raises ValueError if called a second
# time on the same default app.  We guard against double-init (e.g.
# when Flask runs with --reload and imports the module twice) by
# checking firebase_admin.get_app() first.
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

load_dotenv()

import requests
import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials

logger = logging.getLogger(__name__)

# Set at module level by _init_firebase(); used by verify_id_token().
_firebase_app: firebase_admin.App | None = None
_has_service_account: bool = False


def _init_firebase() -> firebase_admin.App:
    """
    Initialise (or return the already-initialised) default Firebase app.

    We use Application Default Credentials (ADC) when a service-account
    key file is present (GOOGLE_APPLICATION_CREDENTIALS env var), which
    is the recommended approach for production Cloud Run / GCE deployments.
    For local development — where ADC may not be configured — we fall back
    to a minimal credential built from the public-facing config, which is
    sufficient for Firestore emulator usage and token verification when a
    service-account file is later added.

    Currently the app is initialised with no service-account JSON so that
    setup requires *only* the env vars already in .env.  Add a
    GOOGLE_APPLICATION_CREDENTIALS path (or embed service-account JSON)
    when you need Admin SDK features that require elevated privileges
    (e.g., custom token minting, FCM).
    """
    try:
        return firebase_admin.get_app()
    except ValueError:
        pass  # Not yet initialised — proceed below.

    project_id = os.getenv("FIREBASE_PROJECT_ID")
    if not project_id:
        raise EnvironmentError(
            "FIREBASE_PROJECT_ID is not set. "
            "Please add it to your .env file."
        )

    # If a service-account key file is configured or present, use it.
    sa_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not sa_path:
        root = Path(__file__).resolve().parent.parent
        for candidate in (root / "serviceAccountKey.json", root / "firebase-service-account.json", root / "service-account.json"):
            if candidate.exists():
                sa_path = str(candidate)
                break

    global _has_service_account
    if sa_path and Path(sa_path).exists():
        cred = credentials.Certificate(sa_path)
        _has_service_account = True
        logger.info("Firebase Admin SDK: using service-account credentials from '%s'.", sa_path)
    else:
        _has_service_account = False
        # No service-account key — initialise with ApplicationDefault so the
        # SDK still works on GCP managed environments.  In plain local dev
        # without ADC this results in an unauthenticated app, which is fine
        # until routes that call Admin SDK features are added.
        cred = credentials.ApplicationDefault()
        logger.info(
            "Firebase Admin SDK: using Application Default Credentials. "
            "Set GOOGLE_APPLICATION_CREDENTIALS to a service-account key "
            "file for full Admin SDK access in local development."
        )

    app = firebase_admin.initialize_app(cred, {"projectId": project_id})
    logger.info("Firebase Admin SDK initialised for project '%s'.", project_id)
    return app


def get_client_config() -> dict:
    """
    Return the Firebase *client-side* SDK configuration dictionary.

    This config is safe to expose to the browser — it contains only
    public identifiers (API key, project ID, etc.).  The actual security
    is enforced by Firebase Security Rules in the Firebase console.

    Usage in app.py::

        from services.firebase_service import get_client_config
        ...
        return render_template("index.html", firebase_config=get_client_config())
    """
    return {
        "apiKey":            os.getenv("FIREBASE_API_KEY", ""),
        "authDomain":        os.getenv("FIREBASE_AUTH_DOMAIN", ""),
        "projectId":         os.getenv("FIREBASE_PROJECT_ID", ""),
        "storageBucket":     os.getenv("FIREBASE_STORAGE_BUCKET", ""),
        "messagingSenderId": os.getenv("FIREBASE_MESSAGING_SENDER_ID", ""),
        "appId":             os.getenv("FIREBASE_APP_ID", ""),
        "measurementId":     os.getenv("FIREBASE_MEASUREMENT_ID", ""),
    }


def verify_id_token(id_token: str) -> dict[str, Any]:
    """
    Verify a Firebase ID token sent by the browser after Google Sign-In.

    Returns the decoded token payload (a dict) which includes:
      • ``uid``       – the Firebase user ID (stable, use as storage key)
      • ``email``     – the user's email address
      • ``name``      – the user's display name (from Google profile)
      • ``picture``   – URL to the user's profile photo

    Verification Strategy:
      1. If a service account is configured, verify using Firebase Admin SDK.
      2. If running in client-credential mode without a service account (no ADC),
         verify against Google's Identity Toolkit accounts:lookup API using the
         public FIREBASE_API_KEY. This allows local development and production
         without requiring a serviceAccountKey.json file.
      3. Fallback to Google's tokeninfo endpoint.
    """
    # ── Strategy 1: Admin SDK (when service account key is available) ──
    if _firebase_app is not None and _has_service_account:
        try:
            return firebase_auth.verify_id_token(id_token, app=_firebase_app)
        except Exception as exc:
            logger.debug("Admin SDK token verification failed (%s), falling back to Google Auth API.", exc)

    # ── Strategy 2: Google Identity Toolkit REST API ───────────
    api_key = os.getenv("FIREBASE_API_KEY")
    if api_key:
        try:
            resp = requests.post(
                f"https://identitytoolkit.googleapis.com/v1/accounts:lookup?key={api_key}",
                json={"idToken": id_token},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                users = data.get("users", [])
                if users:
                    user = users[0]
                    return {
                        "uid": user.get("localId", ""),
                        "email": user.get("email", ""),
                        "name": user.get("displayName", ""),
                        "picture": user.get("photoUrl", ""),
                    }
            elif resp.status_code == 400:
                err_data = resp.json().get("error", {})
                err_msg = err_data.get("message", "Invalid ID token.")
                raise ValueError(f"Invalid ID token: {err_msg}")
        except ValueError:
            raise
        except Exception as exc:
            logger.warning("Identity Toolkit lookup failed: %s", exc)

    # ── Strategy 3: Google OAuth2 tokeninfo endpoint ───────────
    try:
        resp = requests.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": id_token},
            timeout=10,
        )
        if resp.status_code == 200:
            info = resp.json()
            return {
                "uid": info.get("sub", ""),
                "email": info.get("email", ""),
                "name": info.get("name", ""),
                "picture": info.get("picture", ""),
            }
        elif resp.status_code == 400:
            raise ValueError("Invalid ID token: Google OAuth2 tokeninfo rejected token.")
    except ValueError:
        raise
    except Exception as exc:
        logger.warning("tokeninfo endpoint failed: %s", exc)

    raise ValueError("Could not verify ID token with Google identity services.")


# ── Module-level initialisation ───────────────────────────────
# Runs once when the module is first imported.  Errors are logged
# but not re-raised so a misconfigured Firebase doesn't prevent the
# rest of the app from starting during development.
try:
    _firebase_app = _init_firebase()
except Exception as exc:  # noqa: BLE001
    _firebase_app = None
    logger.warning(
        "Firebase Admin SDK could not be initialised: %s. "
        "Firebase-dependent features will be unavailable.",
        exc,
    )
