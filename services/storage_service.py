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
# The user_id is the SHA-256 prefix of the user's email (see
# services/auth_service.py) and is supplied by the route handler after
# @require_auth has confirmed a signed-in session. Storage code does NOT
# trust user-supplied identifiers — auth_service.user_root() rejects
# anything that isn't a valid hex string before this layer is reached.
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


def load_profile(user_id: str) -> Profile:
    """
    Read the user's profile from disk and return a validated
    Profile object.

    If the file doesn't exist yet (first run for this user),
    returns a blank Profile with safe defaults — no crash, no
    special setup.

    Parameters
    ----------
    user_id : str
        The authenticated user's id. Comes from
        auth_service.current_user_id() after @require_auth.

    Raises
    ------
    pydantic.ValidationError
        If profile.json exists but contains data that violates
        the schema (e.g., missing required fields, wrong types).
        This is intentional — we WANT a loud failure here so the
        user (or developer) knows the file needs fixing.

    json.JSONDecodeError
        If profile.json exists but isn't valid JSON.
    """

    profile_path = user_profile_path(user_id)
    if not profile_path.exists():
        # ── First run: return empty defaults ──────────────────
        # Profile's default_factory fields produce a valid
        # (but empty) profile automatically.
        return Profile()

    # ── Read the raw JSON ─────────────────────────────────────
    raw_text = profile_path.read_text(encoding="utf-8")
    raw_data = json.loads(raw_text)

    # ── Validate and build the Profile ────────────────────────
    # model_validate() is the recommended Pydantic v2 method.
    # It accepts a dict and returns a fully validated model, or
    # raises ValidationError with every problem it found.
    profile = Profile.model_validate(raw_data)

    return profile


def save_profile(user_id: str, profile: Profile) -> None:
    """
    Validate a Profile object and write it to disk as JSON.

    Parameters
    ----------
    user_id : str
        The authenticated user's id.
    profile : Profile
        The profile to persist.  Even though it's already a
        Pydantic model, we re-validate to catch any in-memory
        mutations that might have broken invariants.

    Why re-validate on save?
    ------------------------
    Python objects are mutable.  Between load and save, any code
    could do:
        profile.personal_info.name = ""   # violates min_length=1
        profile.education[0].gpa = -1.0   # violates ge=0.0

    Re-validating ensures we never write garbage to disk.
    """

    # ── Populate Metadata Timestamps ──────────────────────────
    import datetime
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if not profile.metadata.created_at:
        profile.metadata.created_at = now_iso
    profile.metadata.updated_at = now_iso
    profile.metadata.version = 1

    # ── Re-validate the entire profile tree ───────────────────
    # model_validate() on an existing model instance triggers
    # a full re-check of every field and nested model.
    # If anything is wrong, this line raises ValidationError.
    validated = Profile.model_validate(profile.model_dump())

    # ── Serialize to a clean JSON string ──────────────────────
    # model_dump_json() produces a JSON string directly.
    # indent=2 makes the file human-readable (easy to inspect
    # and debug).  The slight file-size cost is irrelevant for
    # a single profile document.
    json_string = validated.model_dump_json(indent=2)

    # ── Write atomically-ish ──────────────────────────────────
    # For a personal tool, write_text is fine.  In a production
    # system, you'd write to a temp file and rename (atomic swap)
    # to prevent corruption from partial writes.
    profile_path = user_profile_path(user_id)
    profile_path.write_text(json_string, encoding="utf-8")
