# ──────────────────────────────────────────────────────────────
# services/auth_service.py — Email + password sign-in & per-user isolation
# ──────────────────────────────────────────────────────────────
#
# WHAT THIS FILE DOES
# -------------------
# Provides the primitives the rest of the app uses to gate every
# data-touching operation on an authenticated user:
#
#   • user_id_from_email(email)        → 16-char hex user_id
#   • validate_email(email)            → normalized email or None
#   • hash_password(plain)             → scrypt-based stored hash
#   • verify_password(plain, stored)  → bool
#   • sign_up(email, password)         → create account + set session cookie
#   • sign_in(email, password)         → verify + set session cookie
#   • sign_out()                       → clear session cookie
#   • current_user_id()                → str | None  (from cookie)
#   • current_user_email()             → str | None
#   • change_password(old, new)        → update stored hash, requires session
#   • require_auth                     → decorator: 401 if no session
#   • cleanup_expired_sessions_for_all_users()
#   • user_root(user_id)               → <project>/storage/users/<id>
#
# HOW IDENTITY WORKS
# ------------------
# A user's identity is their email address; the password is what
# proves they are who they claim to be.
#
# user_id = sha256(email.lower().strip())[:16]
#
# This is deterministic (the same email always maps to the same
# user_id), opaque (the user's email does not appear on disk in any
# data file), and path-safe (hex characters only — no escapes, no
# separators, no length variation).
#
# Per-user data lives at:
#     <project>/storage/users/<user_id>/
#         user.json       (audit metadata + password hash)
#         profile.json    (master profile)
#         resumes/        (saved PDFs / .tex source / metadata.json)
#         sessions/       (tailoring workspace scratch — auto-cleaned after 7d)
#
# PASSWORD HASHING
# ----------------
# Passwords are hashed with werkzeug.security.generate_password_hash,
# which by default uses scrypt (PBKDF2-HMAC-SHA256 fallback). The hash
# includes a per-password random salt, so two users with the same
# password have different stored hashes, and a leaked hash cannot be
# brute-forced without significant compute.
#
# The plaintext password NEVER touches disk. user.json stores only
# the hashed form. sign_in compares via werkzeug's constant-time
# check_password_hash.
#
# PASSWORD POLICY
# ---------------
# Minimum 8 characters. We deliberately do NOT enforce complexity
# rules (uppercase + digit + symbol) — length is a stronger signal
# of password strength than composition, and arbitrary composition
# rules push users toward predictable patterns like "Password1!".
#
# MIGRATION FROM EMAIL-ONLY MODE
# ------------------------------
# If a user.json exists without a `password_hash` field (legacy
# email-only account), sign_in refuses them with a "set a password
# first" error. They then call sign_up with the same email and a
# new password, which attaches the hash to the existing user.json
# (preserving created_at / last_seen_at).
#
# COOKIE CONFIGURATION (set in app.py)
# ------------------------------------
# Flask's signed session cookie is the auth token. Hardening in app.py:
#   SESSION_COOKIE_HTTPONLY = True   JS cannot read the cookie (XSS safety)
#   SESSION_COOKIE_SAMESITE = "Lax"  CSRF defense for cross-origin POSTs
#   SESSION_COOKIE_SECURE   = True   when not in debug mode
#
# PRODUCTION DEPLOYMENT MUST SET SECRET_KEY
# -----------------------------------------
# `app.secret_key` is signed with SECRET_KEY (or a random per-process
# fallback). With per-process fallback, every restart invalidates every
# user's session — they all see the auth overlay again on next load.
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
from functools import wraps
from pathlib import Path
from typing import Callable, Optional

from flask import jsonify, session
from werkzeug.security import check_password_hash, generate_password_hash


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


# ── Password hashing ─────────────────────────────────────────
_MIN_PASSWORD_LEN = 8


def hash_password(plain: str) -> str:
    """
    Hash a plaintext password for storage. Uses werkzeug's
    generate_password_hash, which by default uses scrypt (with
    PBKDF2-HMAC-SHA256 as a portable fallback). The returned
    string embeds the algorithm, work factor, salt, and digest —
    self-contained, no separate salt column needed.

    Never log or persist the plaintext password.
    """
    if not isinstance(plain, str) or not plain:
        raise ValueError("Password must be a non-empty string.")
    return generate_password_hash(plain)


def verify_password(plain: str, stored_hash: str) -> bool:
    """
    Constant-time comparison of a plaintext password against a
    stored hash. Returns False for any structural problem
    (missing hash, malformed string) — never raises.
    """
    if not isinstance(plain, str) or not isinstance(stored_hash, str) or not stored_hash:
        return False
    try:
        return check_password_hash(stored_hash, plain)
    except (ValueError, TypeError):
        # Malformed hash, unsupported scheme, etc. — treat as mismatch.
        return False


def validate_password(plain: object) -> Optional[str]:
    """
    Return the plaintext password if it meets the minimum-length
    policy, else None. We intentionally do NOT enforce composition
    rules (uppercase + digit + symbol) — length is a stronger signal
    of strength, and arbitrary composition rules push users toward
    predictable patterns.

    NOTE: the returned string IS the plaintext. The caller is
    responsible for hashing it before storage.
    """
    if not isinstance(plain, str):
        return None
    if len(plain) < _MIN_PASSWORD_LEN:
        return None
    return plain


# ── user.json helpers ─────────────────────────────────────────
def _read_user_meta(user_id: str) -> dict:
    """
    Read user.json for the given user_id, returning {} on any
    failure (missing file, malformed JSON). Never raises.
    """
    meta_path = user_root(user_id) / "user.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_user_meta(user_id: str, meta: dict) -> None:
    """
    Persist user.json atomically-enough for our purposes. The
    directory is materialized by user_root() before this is called,
    so write_text won't race against a missing parent.
    """
    meta_path = user_root(user_id) / "user.json"
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def user_exists_with_password(user_id: str) -> bool:
    """
    True iff a user.json exists for user_id AND has a password_hash
    field. Used by sign_in to distinguish "unknown email" (no user
    at all) from "legacy email-only account" (user.json exists but
    lacks password_hash).
    """
    meta = _read_user_meta(user_id)
    return bool(meta.get("password_hash"))


def user_exists(user_id: str) -> bool:
    """True iff a user.json exists for this user_id."""
    return (user_root(user_id) / "user.json").exists()


# ── Sign-up / Sign-in / Sign-out ──────────────────────────────
def sign_up(email: str, password: str) -> tuple[str, str]:
    """
    Create a new account for `email` with the given plaintext
    `password`, then set the session cookie so the caller is
    immediately signed in. Returns (user_id, email).

    Raises:
      ValueError — email malformed, password too short, or an
                   account with this email already exists and
                   already has a password.

    Side effect: writes <USERS_ROOT>/<user_id>/user.json with the
    password hash, creation timestamp, and last-seen timestamp.

    Special case: if a user.json exists for this email but has no
    `password_hash` field (legacy email-only account), sign_up
    attaches a password hash to the existing record and proceeds.
    This is the documented migration path.
    """
    normalized = validate_email(email)
    if normalized is None:
        raise ValueError("Please enter a valid email address.")
    plain = validate_password(password)
    if plain is None:
        raise ValueError(
            f"Password must be at least {_MIN_PASSWORD_LEN} characters."
        )

    user_id = user_id_from_email(normalized)
    existing = _read_user_meta(user_id)
    if existing.get("password_hash"):
        raise ValueError(
            "An account with this email already exists. "
            "Please sign in instead."
        )

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    existing["user_id"] = user_id
    existing["email"] = normalized
    existing["password_hash"] = hash_password(plain)
    existing.setdefault("created_at", now)
    existing["last_seen_at"] = now
    _write_user_meta(user_id, existing)

    # Set the session cookie.
    session.clear()
    session["user_id"] = user_id
    session["email"] = normalized
    session.permanent = True

    logger.info("Signed up user_id=%s email=%s", user_id, normalized)
    return user_id, normalized


def sign_in(email: str, password: str) -> tuple[str, str]:
    """
    Verify the email + password combination and set the session
    cookie. Returns (user_id, email).

    Raises:
      ValueError — email malformed, password wrong, account has
                   no password set (must sign up first), or no such
                   user exists.

    Error messages are deliberately generic ("Invalid email or
    password") for the wrong-password case so we don't leak whether
    a given email is registered.
    """
    normalized = validate_email(email)
    if normalized is None:
        # Don't reveal whether the email was malformed vs. unknown.
        raise ValueError("Invalid email or password.")
    if not isinstance(password, str) or not password:
        raise ValueError("Invalid email or password.")

    user_id = user_id_from_email(normalized)
    meta = _read_user_meta(user_id)
    stored_hash = meta.get("password_hash")
    if not stored_hash or not verify_password(password, stored_hash):
        # Single error message for both "no such user" and "wrong
        # password" — prevents account enumeration via timing/error
        # differentiation.
        raise ValueError("Invalid email or password.")

    # Touch last_seen_at (cheap; useful audit signal).
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    meta["last_seen_at"] = now
    _write_user_meta(user_id, meta)

    session.clear()
    session["user_id"] = user_id
    session["email"] = normalized
    session.permanent = True

    logger.info("Signed in user_id=%s email=%s", user_id, normalized)
    return user_id, normalized


def change_password(old_password: str, new_password: str) -> None:
    """
    Update the signed-in user's stored password hash. Verifies
    `old_password` first; raises ValueError on mismatch or weak new
    password. Requires an active session — call current_user_id()
    before invoking if you need to handle the unauthenticated case.
    """
    user_id = current_user_id()
    if not user_id:
        raise ValueError("Not signed in.")
    meta = _read_user_meta(user_id)
    stored_hash = meta.get("password_hash")
    if not stored_hash or not verify_password(old_password, stored_hash):
        raise ValueError("Current password is incorrect.")
    new_plain = validate_password(new_password)
    if new_plain is None:
        raise ValueError(
            f"New password must be at least {_MIN_PASSWORD_LEN} characters."
        )
    meta["password_hash"] = hash_password(new_plain)
    meta["password_changed_at"] = datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat()
    _write_user_meta(user_id, meta)
    logger.info("Password changed for user_id=%s", user_id)


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
