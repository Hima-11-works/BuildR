# ──────────────────────────────────────────────────────────────
# services/storage_service.py — Load & save the user's profile
# ──────────────────────────────────────────────────────────────
#
# WHAT THIS FILE DOES
# -------------------
# Two functions:
#   load_profile()  →  reads  storage/profile.json  →  Profile object
#   save_profile()  →  Profile object  →  writes  storage/profile.json
#
# That's it — intentionally thin.  The rest of the app never
# touches the filesystem directly; it always goes through here.
# This gives us ONE place to change if we later swap JSON files
# for a database, cloud storage, etc.
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


# ── Where profile data lives on disk ─────────────────────────
# Path(__file__) points to THIS file (storage_service.py).
# .resolve() makes it absolute, .parent goes up to services/,
# .parent again goes to the project root.
# We then descend into storage/profile.json.
#
# Using Path objects (not string concatenation) is a best
# practice because Path handles OS differences (/ vs \)
# and gives us clean methods like .exists() and .read_text().
# ──────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = _PROJECT_ROOT / "storage" / "profile.json"


def load_profile() -> Profile:
    """
    Read the user's profile from disk and return a validated
    Profile object.

    If the file doesn't exist yet (first run), returns a blank
    Profile with safe defaults — no crash, no special setup.

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

    if not PROFILE_PATH.exists():
        # ── First run: return empty defaults ──────────────────
        # Profile's default_factory fields produce a valid
        # (but empty) profile automatically.
        return Profile()

    # ── Read the raw JSON ─────────────────────────────────────
    raw_text = PROFILE_PATH.read_text(encoding="utf-8")
    raw_data = json.loads(raw_text)

    # ── Validate and build the Profile ────────────────────────
    # model_validate() is the recommended Pydantic v2 method.
    # It accepts a dict and returns a fully validated model, or
    # raises ValidationError with every problem it found.
    #
    # Why model_validate() instead of Profile(**raw_data)?
    #   Both work, but model_validate() is more explicit and
    #   supports extra options (strict mode, context, etc.)
    #   that we might need later.
    profile = Profile.model_validate(raw_data)

    return profile


def save_profile(profile: Profile) -> None:
    """
    Validate a Profile object and write it to disk as JSON.

    Parameters
    ----------
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

    # ── Ensure the storage/ directory exists ──────────────────
    # parents=True creates any missing intermediate directories.
    # exist_ok=True means "don't error if it already exists."
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # ── Write atomically-ish ──────────────────────────────────
    # For a personal tool, write_text is fine.  In a production
    # system, you'd write to a temp file and rename (atomic swap)
    # to prevent corruption from partial writes.
    PROFILE_PATH.write_text(json_string, encoding="utf-8")
