# ──────────────────────────────────────────────────────────────
# services/auth_service.py — Email-based sign-in + per-user data isolation
# ──────────────────────────────────────────────────────────────
#
# WHAT THIS FILE DOES
# -------------------
# Provides four primitives the rest of the app uses to gate every
# data-touching operation on an authenticated user:
#
#   • user_id_from_email(email)  → str   deterministic 16-char hex user_id
#   • sign_in(email)             → str   sets the session cookie, returns user_id
#   • sign_out()                 → None  clears the session cookie
#   • current_user_id()          → str | None  reads user_id from session cookie
#   • require_auth               → decorator: 401 if no session, else passes
#   • cleanup_expired_sessions_for_all_users() → iterates storage/users/* and
#                                                 delegates to session_service
#
# HOW IDENTITY WORKS
# ------------------
# A user's identity is their email address. There is NO password.
#
# user_id = sha256(email.lower().strip())[:16]
#
# This is deterministic (the same email always maps to the same user_id),
# opaque (the user's email does not appear on disk), and path-safe
# (hex characters only — no escapes, no separators, no length variation).
#
# Per-user data lives at:
#     <project>/storage/users/<user_id>/
#         profile.json     (master profile)
#         resumes/         (saved PDFs / .tex source / metadata.json)
#         sessions/        (tailoring workspace scratch — auto-cleaned after 7d)
#
# SECURITY NOTE — EMAIL-ONLY AUTH (KNOWN LIMITATION)
# -------------------------------------------------
# This module is intentionally minimal: the email IS the credential.
# Anyone who knows (or guesses) another user's email can sign in as
# them and read or overwrite their data. This is the model the user
# explicitly requested — they wanted "sign in with email" behavior,
# not a password system — and it is appropriate for a single-tenant
# deployment or trusted-user environment.
#
# If this is ever deployed in a hostile environment, this file is the
# single place that needs to change — replace `sign_in` with one that
# verifies a password / OTP / OAuth token before setting the session
# cookie. The `user_id_from_email` helper and the @require_auth
# decorator do not need to change.
#
# COOKIE CONFIGURATION (set in app.py)
# ------------------------------------
# Flask's signed session cookie is the auth token. It carries
# {"user_id": "...", "email": "..."}. Hardening in app.py:
#
#   SESSION_COOKIE_HTTPONLY = True   JS cannot read the cookie (XSS safety)
#   SESSION_COOKIE_SAMESITE = "Lax"  CSRF defense for cross-origin POSTs
#   SESSION_COOKIE_SECURE   = True   when not in debug mode
#
# PRODUCTION DEPLOYMENT MUST SET SECRET_KEY
# -----------------------------------------
# `app.secret_key` is signed with SECRET_KEY (or a random per-process
# fallback). With per-process fallback, every restart invalidates every
# user's session — they all see the auth overlay again on next load.
# Set SECRET_KEY in .env (or Render dashboard) to a stable random value
# generated with:  python -c "import secrets; print(secrets.token_hex(32))"
#
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import hashlib
import logging
import re
from functools import wraps
from pathlib import Path
from typing import Callable, Optional

from flask import jsonify, session


logger = logging.getLogger(__name__)


# ── Identity hashing ──────────────────────────────────────────
_USER_ID_LEN = 16
# Minimal shape check: one local part (no @, no whitespace), exactly
# one @, a domain with at least one dot, no @ anywhere else, and no
# whitespace. Doesn't reject "+", dots-in-local-part, or IDN, but the
# email is *the* credential so we don't need to over-engineer validation.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


def user_id_from_email(email: str) -> str:
    """
    Deterministically derive a 16-character hex user_id from an email.

    Two users with the same email produce the same user_id (that's
    the whole point — same identity maps to the same on-disk folder).
    Different emails produce different user_ids with overwhelming
    probability (sha256 collision space is 2^64).
    """
    normalized = (email or "").strip().lower()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest[:_USER_ID_LEN]


def validate_email(email: object) -> Optional[str]:
    """
    Return a normalized (lower-cased, trimmed) email if it looks
    well-formed, else None. Caller should treat None as "reject this".
    """
    if not isinstance(email, str):
        return None
    candidate = email.strip()
    if not candidate or not _EMAIL_RE.match(candidate):
        return None
    return candidate.lower()


# ── Per-user filesystem root ──────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
USERS_ROOT = _PROJECT_ROOT / "storage" / "users"


def user_root(user_id: str) -> Path:
    """
    Return <project>/storage/users/<user_id>, creating the directory
    if it does not yet exist. Callers use this as the base for
    profile.json, resumes/, and sessions/.

    We validate the user_id against the same path-safe character class
    used elsewhere (alnum + "-" + "_") so that even if a route handler
    is buggy and forwards an attacker-controlled id, this layer refuses
    to materialize a directory outside the users tree. Production user_ids
    are 16-char hex strings from user_id_from_email(), which trivially
    satisfies this allow-list.

    We create the directory eagerly so subsequent writes never have
    to wonder whether their parent dir exists.
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
    user is signed in.
    """
    return session.get("user_id")


def current_user_email() -> Optional[str]:
    """
    Return the email from the active session cookie, or None. This
    is purely informational; authorization decisions must use
    current_user_id(), never this.
    """
    return session.get("email")


def sign_in(email: str) -> tuple[str, str]:
    """
    Set the session cookie to identify the user. Returns
    (user_id, email). Raises ValueError if the email is malformed.

    Side effect: persists (or updates) <USERS_ROOT>/<user_id>/user.json
    with creation / last-seen timestamps so we have an audit trail
    and can sanity-check that the directory actually exists.
    """
    normalized = validate_email(email)
    if normalized is None:
        raise ValueError("Please enter a valid email address.")

    user_id = user_id_from_email(normalized)
    session.clear()
    session["user_id"] = user_id
    session["email"] = normalized
    session.permanent = True  # obeys PERMANENT_SESSION_LIFETIME if set

    # Touch user.json so the directory is materialized and so a
    # future admin / debug tool can list users without scraping dirs.
    import datetime
    user_dir = user_root(user_id)
    meta_path = user_dir / "user.json"
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if meta_path.exists():
        try:
            import json
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {}
    else:
        meta = {}
    meta["user_id"] = user_id
    meta["email"] = normalized
    meta.setdefault("created_at", now)
    meta["last_seen_at"] = now
    import json as _json
    meta_path.write_text(_json.dumps(meta, indent=2), encoding="utf-8")

    logger.info("Signed in user_id=%s email=%s", user_id, normalized)
    return user_id, normalized


def sign_out() -> None:
    """
    Clear the session cookie. Idempotent — calling when no user is
    signed in is a no-op.
    """
    if session.get("user_id"):
        logger.info("Signed out user_id=%s", session["user_id"])
    session.clear()


# ── Route decorator ───────────────────────────────────────────
def require_auth(view: Callable) -> Callable:
    """
    Decorator that 401s any request without a signed-in user.

    Usage:
        @app.route("/api/profile")
        @require_auth
        def api_get_profile():
            user_id = current_user_id()  # safe — we know it is set
            ...

    The handler can call current_user_id() and trust it to be a
    valid hex string. If you need the email, call current_user_email().
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
    # Import lazily to avoid a circular import: session_service
    # imports Profile from models.profile, which doesn't import
    # auth_service, so the circle is fine — but importing at module
    # top-level would force eager evaluation. Lazy is cleaner.
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
        except Exception as exc:  # noqa: BLE001 — never let cleanup block startup
            logger.warning(
                "Cleanup failed for user_id=%s: %s", user_id, exc
            )
    return total
