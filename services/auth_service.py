# ──────────────────────────────────────────────────────────────
# services/auth_service.py — Google OAuth + per-user isolation
# ──────────────────────────────────────────────────────────────
#
# WHAT THIS FILE DOES
# -------------------
# Verifies Google-issued ID tokens and establishes a Flask session
# bound to the Google account's verified `sub` claim.
#
#     POST /api/auth/google   { id_token: "<JWT from Google>" }
#        │
#        ├─→ verify_google_id_token(token)
#        │      fetches Google's public keys (cached)
#        │      verifies JWT signature, audience, issuer, expiry
#        │      enforces email_verified == True
#        │      returns the claims dict
#        │
#        ├─→ user_id_from_google_sub(sub)   # sha256(sub)[:16]
#        │
#        └─→ session["user_id"] = <derived id>
#           session["email"]    = <display only>
#
# HOW IDENTITY WORKS
# ------------------
# Identity comes from Google's `sub` claim — a stable, opaque
# identifier that Google assigns to each Google account. It is the
# ONLY field used to derive the on-disk user_id. Email is stored
# purely as a display attribute and is never trusted as proof of
# identity.
#
# SECURITY MODEL
# --------------
# This module is what defends against "I know your email, give me
# your data". A request body that contains an email field is NEVER
# sufficient to sign in. The only path to a session is:
#
#   1. Frontend invokes Google Identity Services (GIS).
#   2. GIS handles Google's OAuth flow in a popup.
#   3. GIS hands the SPA a cryptographically-signed JWT.
#   4. The SPA POSTs that JWT to /api/auth/google.
#   5. THIS module verifies the JWT's signature against Google's
#      published public keys (https://www.googleapis.com/oauth2/v3/certs).
#   6. Only then is a Flask session created.
#
# An attacker who knows a victim's email but does NOT control the
# victim's Google account cannot produce a valid JWT with the
# victim's `sub`. They cannot sign in. They cannot read or modify
# the victim's data.
#
# WHAT THIS FILE DOES NOT DO
# --------------------------
# - It does NOT call any third-party auth service beyond Google.
# - It does NOT trust `email` for any authorization decision.
# - It does NOT trust any field on the request body for identity.
# - It does NOT accept a password (there is no password — the
#   user's Google account IS the credential).
#
# PRODUCTION DEPLOYMENT MUST SET GOOGLE_CLIENT_ID AND SECRET_KEY
# -------------------------------------------------------------
# GOOGLE_CLIENT_ID is your OAuth 2.0 Client ID from Google Cloud
# Console → APIs & Services → Credentials. Without it, this module
# refuses to verify any token (returns 500 with a clear log line).
#
# SECRET_KEY is used by Flask to sign the session cookie. Set it
# to a stable random value in production so user sessions survive
# process restarts. Generate with:
#
#     python -c "import secrets; print(secrets.token_hex(32))"
#
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import hashlib
import logging
import os
import re
from functools import wraps
from pathlib import Path
from typing import Callable, Optional

from flask import jsonify, session


logger = logging.getLogger(__name__)


# ── Identity derivation ────────────────────────────────────────
_USER_ID_LEN = 16


def user_id_from_google_sub(sub: str) -> str:
    """
    Deterministically derive a 16-character hex user_id from a
    Google `sub` claim.

    Two Google accounts with the same `sub` always produce the same
    user_id (the whole point — same identity maps to the same on-disk
    folder). Different `sub` values produce different user_ids with
    overwhelming probability (sha256 collision space is 2^64).
    """
    if not sub or not isinstance(sub, str):
        raise ValueError("Google sub claim is required.")
    digest = hashlib.sha256(sub.strip().encode("utf-8")).hexdigest()
    return digest[:_USER_ID_LEN]


# ── Google ID token verification ───────────────────────────────
# We use google-auth's id_token.verify_oauth2_token because it:
#   1. Fetches Google's published JWKs (with caching) — never trust
#      a hardcoded key.
#   2. Verifies the JWT signature against those keys.
#   3. Validates `iss` is `https://accounts.google.com` or
#      `accounts.google.com`.
#   4. Validates `aud` matches our GOOGLE_CLIENT_ID.
#   5. Validates `exp` is in the future.
#
# Importing google.auth.transport.requests lazily inside the verifier
# keeps the module importable even in environments where the request
# transport isn't fully usable (e.g. some test setups).

def verify_google_id_token(id_token_str: str) -> dict:
    """
    Verify a Google-issued ID token (JWT). Returns the verified claims
    dict on success. Raises ValueError on any failure.

    The returned dict always contains at least:
        sub             — Google account's stable unique id
        email           — display only; NEVER used as identity
        email_verified  — True iff Google has confirmed email ownership
        name            — display name (may be absent)
        picture         — avatar URL (may be absent)

    Verification guarantees (per google-auth library):
        - Signature is valid against Google's published JWKs.
        - `iss` claim is `https://accounts.google.com` or
          `accounts.google.com`.
        - `aud` claim equals our GOOGLE_CLIENT_ID.
        - `exp` is in the future (token not expired).
        - `nbf` (if present) is in the past.
        - `iat` is in the past.

    We additionally enforce:
        - `sub` is present (Google always issues this).
        - `email_verified` is True (Google only issues tokens with
          verified=True for email owners; this rejects attacker
          accounts that register someone else's email).

    Raises
    ------
    ValueError
        If the token is missing, malformed, expired, for a different
        audience, or for an unverified email.
    RuntimeError
        If GOOGLE_CLIENT_ID is not configured.
    """
    if not id_token_str or not isinstance(id_token_str, str):
        raise ValueError("An id_token is required.")

    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    if not client_id:
        raise RuntimeError(
            "GOOGLE_CLIENT_ID is not configured. Set it in your .env "
            "or environment. Get one from Google Cloud Console → "
            "APIs & Services → Credentials."
        )

    # Lazy import: google.auth.transport.requests uses urllib3 under
    # the hood to fetch Google's JWKs. Importing inside the function
    # means a misconfigured transport won't break module import.
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests

    request_adapter = google_requests.Request()
    try:
        claims = google_id_token.verify_oauth2_token(
            id_token_str,
            request_adapter,
            audience=client_id,
        )
    except Exception as exc:  # noqa: BLE001 — google-auth raises a
        # variety of exception classes (GoogleAuthError, ValueError)
        # for any verification failure. We collapse them all into
        # a single ValueError so callers handle one error type.
        logger.warning("Google ID token verification failed: %s", exc)
        raise ValueError("Invalid Google ID token.") from exc

    sub = claims.get("sub")
    if not sub:
        raise ValueError("Google ID token missing sub claim.")

    if not claims.get("email_verified", False):
        # An attacker can register a Google account with someone
        # else's email address, but Google only issues an
        # email_verified=true token to the actual owner of the
        # Google account. Rejecting unverified tokens prevents the
        # attacker from proving they own an email they don't.
        raise ValueError("Google account email is not verified.")

    logger.info(
        "Verified Google ID token: sub=%s email=%s",
        sub, claims.get("email", "(no email)"),
    )
    return claims


# ── Per-user filesystem root ──────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
USERS_ROOT = _PROJECT_ROOT / "storage" / "users"


def user_root(user_id: str) -> Path:
    """
    Return <project>/storage/users/<user_id>, creating the directory
    if it does not yet exist. Callers use this as the base for
    profile.json, resumes/, and sessions/.

    Validation uses the same path-safe character class as session IDs
    so even a buggy caller forwarding attacker input cannot materialize
    a directory outside the users tree. Production user_ids are
    16-char hex strings from user_id_from_google_sub, which trivially
    satisfies this allow-list.
    """
    if not user_id or not re.match(r"^[A-Za-z0-9_-]+$", user_id):
        raise ValueError(f"Invalid user_id: {user_id!r}")
    path = USERS_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path


# ── Flask session helpers ─────────────────────────────────────
def current_user_id() -> Optional[str]:
    """
    Return the user_id from the active session cookie, or None if no
    user is signed in. The user_id is the SHA-256 prefix of the
    Google `sub` claim — see user_id_from_google_sub().
    """
    return session.get("user_id")


def current_user_email() -> Optional[str]:
    """
    Return the email from the active session cookie, or None. This
    is purely informational (display); authorization decisions must
    use current_user_id(), never this.
    """
    return session.get("email")


def sign_in_with_google(id_token_str: str) -> tuple[str, str]:
    """
    Verify a Google-issued ID token, derive the user_id from its
    verified `sub` claim, and establish a Flask session.

    Returns (user_id, email). The caller (app.py) reports these to
    the SPA, which uses email as a display label and forgets the
    user_id immediately after the response.

    Raises
    ------
    ValueError
        If the token is invalid, expired, for a different audience,
        or for an unverified email. The caller should surface a
        401 to the SPA.
    RuntimeError
        If GOOGLE_CLIENT_ID is not configured.
    """
    claims = verify_google_id_token(id_token_str)
    sub = claims["sub"]
    email = claims.get("email", "")
    user_id = user_id_from_google_sub(sub)

    session.clear()
    session["user_id"] = user_id
    session["email"] = email
    session["google_sub"] = sub  # stored for audit/debugging only
    session.permanent = True

    # Persist (or refresh) the user's record file with the verified
    # Google identity. This is an audit trail only — never the
    # source of truth for identity decisions.
    import datetime
    import json
    user_dir = user_root(user_id)
    meta_path = user_dir / "user.json"
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {}
    else:
        meta = {}
    meta["user_id"] = user_id
    meta["google_sub"] = sub
    meta["email"] = email
    meta.setdefault("created_at", now)
    meta["last_seen_at"] = now
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    logger.info(
        "Signed in user_id=%s google_sub=%s email=%s",
        user_id, sub, email,
    )
    return user_id, email


def sign_out() -> None:
    """
    Clear the session cookie. Idempotent — calling when no user is
    signed in is a no-op.
    """
    if session.get("user_id"):
        logger.info(
            "Signed out user_id=%s google_sub=%s",
            session["user_id"], session.get("google_sub"),
        )
    session.clear()


# ── Route decorator ───────────────────────────────────────────
def require_auth(view: Callable) -> Callable:
    """
    Decorator that 401s any request without a signed-in user.

    The handler can call current_user_id() and trust it to be a
    valid hex string derived from a Google-verified sub claim.
    """

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user_id():
            return jsonify({"error": "Authentication required."}), 401
        return view(*args, **kwargs)

    return wrapper


# ── Cross-user startup cleanup ────────────────────────────────
def cleanup_expired_sessions_for_all_users() -> int:
    """
    Iterate every user directory under USERS_ROOT and run the
    session-service cleanup for each. Called once at app startup
    so abandoned tailoring workspaces don't accumulate forever.

    Returns total sessions removed across all users.
    """
    if not USERS_ROOT.exists():
        return 0
    from services import session_service

    total = 0
    for entry in USERS_ROOT.iterdir():
        if not entry.is_dir():
            continue
        user_id = entry.name
        if not re.match(r"^[A-Za-z0-9_-]+$", user_id):
            # Skip unrelated directories (e.g. ".legacy-migrated").
            continue
        try:
            total += session_service.cleanup_expired_sessions(user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Cleanup failed for user_id=%s: %s", user_id, exc
            )
    return total
