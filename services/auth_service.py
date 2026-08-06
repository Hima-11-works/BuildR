# ──────────────────────────────────────────────────────────────
# services/auth_service.py — Per-user filesystem layout
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
# All sign-in / sign-up / sign-out code has been removed.  The app
# operates in single-user mode with a fixed DEFAULT_USER_ID defined
# in app.py.
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
import re
from pathlib import Path


logger = logging.getLogger(__name__)


# ── Per-user filesystem root ──────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
USERS_ROOT = _PROJECT_ROOT / "storage" / "users"


def user_root(user_id: str) -> Path:
    """
    Return <project>/storage/users/<user_id>, creating the directory
    if it does not yet exist. Callers use this as the base for
    profile.json, resumes/, and sessions/.

    We validate the user_id against a path-safe character class
    (alnum + "-" + "_") so that even if a route handler is buggy
    and forwards an attacker-controlled id, this layer refuses to
    materialize a directory outside the users tree.

    We create the directory eagerly so subsequent writes never have
    to wonder whether their parent dir exists.
    """
    if not user_id or not re.match(r"^[A-Za-z0-9_-]+$", user_id):
        raise ValueError(f"Invalid user_id: {user_id!r}")
    path = USERS_ROOT / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path


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
