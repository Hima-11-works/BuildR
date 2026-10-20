# ──────────────────────────────────────────────────────────────
# services/auth_service.py — Per-user filesystem layout + session helpers
# ──────────────────────────────────────────────────────────────
#
# WHAT THIS FILE DOES
# -------------------
# Provides the filesystem primitives that the rest of the app uses
# to scope data to a user directory:
#
#   • USERS_ROOT                        → <project>/storage/users
#   • user_root(user_id)                → <project>/storage/users/<id>
#   • cleanup_expired_sessions_for_all_users()
#
# AUTH SESSION HELPERS
# --------------------
# After a Firebase ID token is verified by /api/auth/login, the Flask
# session stores two keys:
#
#   session["uid"]  – the Firebase UID (used as the filesystem user_id)
#   session["user"] – a dict with display info: name, email, picture
#
# These helpers centralise all session reads in one place:
#
#   current_user_id()  → str | None   (returns None if not logged in)
#   current_user()     → dict | None  (returns None if not logged in)
#   require_auth       → decorator    (aborts with 401 if not logged in)
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import functools
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Callable

from flask import session, jsonify


logger = logging.getLogger(__name__)


# ── Per-user filesystem root ──────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve_users_root() -> Path:
    """
    Resolve the root directory for storing user files.

    In serverless deployments (such as AWS Lambda or Vercel where code is mounted
    at /var/task), the deployment filesystem is strictly read-only. We detect this
    condition and automatically point storage to a writable temporary directory
    (/tmp/buildr_storage/users).

    An explicit BUILDR_STORAGE_DIR or STORAGE_DIR environment variable can also
    be supplied to override this location.
    """
    custom = os.getenv("BUILDR_STORAGE_DIR") or os.getenv("STORAGE_DIR")
    if custom:
        target = Path(custom) / "users"
        try:
            target.mkdir(parents=True, exist_ok=True)
            return target
        except Exception as exc:
            logger.warning("Custom storage dir '%s' could not be created: %s", custom, exc)

    target = _PROJECT_ROOT / "storage" / "users"
    try:
        target.mkdir(parents=True, exist_ok=True)
        # Test writability
        test_file = target / ".writetest"
        test_file.touch()
        test_file.unlink()
        return target
    except (OSError, PermissionError):
        # Read-only filesystem (e.g. AWS Lambda / Vercel /var/task)
        fallback = Path(tempfile.gettempdir()) / "buildr_storage" / "users"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        logger.info("Read-only filesystem detected; using writable directory '%s'", fallback)
        return fallback


USERS_ROOT = _resolve_users_root()


def user_root(user_id: str) -> Path:
    """
    Return <USERS_ROOT>/<user_id>, creating the directory
    if it does not yet exist. Callers use this as the base for
    profile.json, resumes/, and sessions/.

    We validate the user_id against a path-safe character class
    (alnum + "-" + "_") so that even if a route handler is buggy
    and forwards an attacker-controlled id, this layer refuses to
    materialize a directory outside the users tree.

    If USERS_ROOT fails with a read-only filesystem error at runtime,
    it dynamically switches to the system temporary directory.
    """
    if not user_id or not re.match(r"^[A-Za-z0-9_-]+$", user_id):
        raise ValueError(f"Invalid user_id: {user_id!r}")

    global USERS_ROOT
    try:
        path = USERS_ROOT / user_id
        path.mkdir(parents=True, exist_ok=True)
        return path
    except (OSError, PermissionError):
        USERS_ROOT = Path(tempfile.gettempdir()) / "buildr_storage" / "users"
        path = USERS_ROOT / user_id
        path.mkdir(parents=True, exist_ok=True)
        return path


# ── Session helpers ───────────────────────────────────────────

def current_user_id() -> str | None:
    """
    Return the Firebase UID stored in the Flask session, or None
    if the user is not authenticated.

    This is the canonical way every route handler should obtain the
    user_id — never read session["uid"] directly.
    """
    return session.get("uid")


def current_user() -> dict | None:
    """
    Return the user info dict stored in the Flask session, or None
    if the user is not authenticated.

    The dict contains at minimum: name, email, picture.
    """
    return session.get("user")


def require_auth(f: Callable) -> Callable:
    """
    Route decorator that enforces authentication.

    If the request has no valid session:
      • Returns HTTP 401 JSON for API routes (paths starting with /api/).
      • Redirects to /login for all other routes.

    Usage::

        @app.route("/api/profile")
        @require_auth
        def api_get_profile():
            uid = current_user_id()
            ...
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if current_user_id() is None:
            from flask import request, redirect, url_for
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required.", "code": "UNAUTHENTICATED"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


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
