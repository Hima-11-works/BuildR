# ──────────────────────────────────────────────────────────────
# services/storage_service.py — Per-user profile load/save
# ──────────────────────────────────────────────────────────────
#
# WHAT THIS FILE DOES
# -------------------
# Two functions:
#   load_profile(user_id)  →  reads  storage/users/<user_id>/profile.json
#                              → Profile object
#   save_profile(user_id, profile)  →  Profile object
#                              → writes  storage/users/<user_id>/profile.json
#
# That's it — intentionally thin.  The rest of the app never
# touches the filesystem directly; it always goes through here.
# This gives us ONE place to change if we later swap JSON files
# for a database, cloud storage, etc.
#
# WHY PER-USER SCOPING
# --------------------
# Each user's master profile lives at storage/users/<user_id>/profile.json
# so two users can never overwrite each other's data, even by accident.
# The user_id is supplied by the route handler (typically DEFAULT_USER_ID).
# Storage code scopes paths through auth_service.user_root(), which validates
# the user_id against safe characters before this layer is reached.
#
# WHY WE VALIDATE ON BOTH LOAD *AND* SAVE
# ─────────────────────────────────────────
# You might wonder: "If we validate when saving, why validate
# again when loading?"
#
# 1. **Save-time validation** catches bugs in YOUR code.
#    If a service accidentally sets `gpa = "excellent"` instead
#    of a float, Pydantic stops it before it ever hits disk.
#
# 2. **Load-time validation** catches problems from OUTSIDE:
#      • Someone hand-edited profile.json and introduced a typo.
#      • A future version of the app changed the schema, but
#        the file on disk is from an older version.
#      • The file got partially written (power failure, crash).
#    Without load validation, those bad values silently flow
#    through the app and cause cryptic errors much later — maybe
#    in the PDF renderer, maybe in the AI prompt.
#
# Think of it as "trust no one" — validate at every boundary
# where data crosses between systems (disk ↔ memory).
#
# HOW PYDANTIC VALIDATION WORKS UNDER THE HOOD
# ─────────────────────────────────────────────
# When you call  Profile(**data)  or  Profile.model_validate(data):
#
#   1. Pydantic walks every field in the model.
#   2. For each field it checks:
#        a. Is the value present?  (If required and missing → error)
#        b. Does the type match?   (str where int expected → try coerce)
#        c. Do extra validators pass?  (min_length, ge, le, etc.)
#   3. For nested models (like PersonalInfo inside Profile),
#      it recurses and validates the child model too.
#   4. If ANYTHING fails, it collects ALL errors and raises a
#      single ValidationError with every problem listed.
#
# This means one call validates your entire profile tree — you
# don't need manual if-checks scattered across your codebase.
# ──────────────────────────────────────────────────────────────

import datetime
import json
from pathlib import Path

from models.profile import Profile

from services.auth_service import user_root


def user_profile_path(user_id: str) -> Path:
    """
    Return <project>/storage/users/<user_id>/profile.json. The user's
    root directory is created if it does not yet exist. The actual
    profile.json file may or may not exist — callers handle the
    "first run" case.
    """
    return user_root(user_id) / "profile.json"


import logging
from services import firestore_service

logger = logging.getLogger(__name__)


def load_profile(user_id: str) -> Profile:
    """
    Read the user's profile from Firestore (or local disk fallback)
    and return a validated Profile object.

    First checks Firestore. If not found or if Firestore is
    unavailable, falls back to storage/users/<user_id>/profile.json.
    If neither exists yet (first run for this user), returns a blank
    Profile with safe defaults.
    """
    # ── 1. Attempt to load from Firestore ─────────────────────
    try:
        firestore_data = firestore_service.load_user_profile(user_id)
        if firestore_data:
            profile = Profile.model_validate(firestore_data)
            # Sync to local cache so offline/fallback stays updated
            try:
                profile_path = user_profile_path(user_id)
                profile_path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
            except Exception as cache_exc:
                logger.debug("Could not update local profile cache: %s", cache_exc)
            return profile
    except Exception as exc:
        logger.warning("Could not read profile from Firestore for user %s: %s", user_id, exc)

    # ── 2. Fallback to local filesystem ───────────────────────
    profile_path = user_profile_path(user_id)
    if not profile_path.exists():
        return Profile()

    raw_text = profile_path.read_text(encoding="utf-8")
    raw_data = json.loads(raw_text)
    profile = Profile.model_validate(raw_data)

    # If profile exists locally but wasn't in Firestore, sync it
    try:
        firestore_service.save_user_profile(user_id, profile.model_dump())
    except Exception as sync_exc:
        logger.debug("Background sync to Firestore skipped: %s", sync_exc)

    return profile


def save_profile(user_id: str, profile: Profile) -> None:
    """
    Validate a Profile object, write it to local disk as JSON,
    and persist the profile data values to Firestore.
    """

    # ── Populate Metadata Timestamps ──────────────────────────
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if not profile.metadata.created_at:
        profile.metadata.created_at = now_iso
    profile.metadata.updated_at = now_iso
    profile.metadata.version = 1

    # ── Re-validate the entire profile tree ───────────────────
    validated = Profile.model_validate(profile.model_dump())

    # ── 1. Write to local disk cache ──────────────────────────
    json_string = validated.model_dump_json(indent=2)
    profile_path = user_profile_path(user_id)
    profile_path.write_text(json_string, encoding="utf-8")

    # ── 2. Persist to Firestore ───────────────────────────────
    try:
        firestore_service.save_user_profile(user_id, validated.model_dump())
    except Exception as exc:
        logger.warning("Failed to persist profile to Firestore for user %s: %s", user_id, exc)
