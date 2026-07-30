# ──────────────────────────────────────────────────────────────
# services/resume_library.py — Per-user persistent resume library
# ──────────────────────────────────────────────────────────────
#
# WHAT THIS FILE DOES
# -------------------
# Manages a per-user library of generated resumes on disk. Each resume
# lives in its own folder under storage/users/<user_id>/resumes/,
# containing:
#   • resume.tex   — the editable LaTeX source
#   • resume.pdf   — the compiled PDF
#   • metadata.json — type, label, date, job description (if any)
#
# PER-USER SCOPING
# ----------------
# Every public function takes user_id as its first argument. All paths
# derive from `user_resumes_dir(user_id)` — there is no shared global
# directory. Two users on the same server cannot see each other's
# resumes: their libraries live in distinct folders and the path-
# traversal resolve() check is anchored on the per-user dir.
#
# FOLDER NAMING / ID SCHEME
# ─────────────────────────
# Each folder is named:  YYYYMMDD-HHMMSS_sanitized-label
#   e.g.  20260701-005402_master
#   e.g.  20260701-010530_google-software-engineer
#
# WHY THIS SCHEME:
#   • Timestamp prefix guarantees uniqueness (to the second) and
#     sorts chronologically in the filesystem.
#   • Sanitized label keeps only [a-zA-Z0-9_-], truncated to 50
#     chars.  This prevents path traversal (../), null bytes,
#     and OS-illegal characters.
#   • Human-readable — you can browse the library directly.
#   • The folder name doubles as the resume ID in API routes,
#     so no database is required.
#
# WHY STORE metadata.json ALONGSIDE FILES
# ────────────────────────────────────────
#   • Self-describing archives: each folder is self-contained.
#   • No database dependency: directory listing + JSON files.
#   • Future-proof: metadata supports filtering/search later.
#   • Decoupled from filenames: metadata.label is the display
#     name (can contain spaces), folder name is the safe version.
#
# DELETE SAFETY (PATH TRAVERSAL PROTECTION)
# ──────────────────────────────────────────
# The delete function uses a resolve-and-verify pattern:
#   1. Construct candidate path: user_resumes_dir(user_id) / id
#   2. Call .resolve() to collapse .., symlinks, etc.
#   3. Assert resolved path is strictly inside the user's dir
#   4. Only then call shutil.rmtree()
# This blocks ../../../etc/passwd and symlink attacks — and it
# also ensures Alice's resume_id cannot reach Bob's directory.
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from services.auth_service import user_root


def user_resumes_dir(user_id: str) -> Path:
    """
    Return <project>/storage/users/<user_id>/resumes, creating the
    directory tree if it does not yet exist. Every function in this
    module that touches a resume file uses this helper, so all
    library operations are automatically scoped to the authenticated
    user.
    """
    resumes_dir = user_root(user_id) / "resumes"
    resumes_dir.mkdir(parents=True, exist_ok=True)
    return resumes_dir


# ── Filenames used inside each resume folder ──────────────────
_TEX_FILENAME = "resume.tex"
_PDF_FILENAME = "resume.pdf"
_META_FILENAME = "metadata.json"


def _sanitize_label(raw: str) -> str:
    """
    Convert a raw label into a filesystem-safe string.

    Rules:
      1. Lowercase everything.
      2. Replace spaces and underscores with hyphens.
      3. Strip all characters except [a-z0-9-].
      4. Collapse consecutive hyphens.
      5. Truncate to 50 characters.
      6. Fall back to "resume" if the result is empty.

    Examples
    --------
    >>> _sanitize_label("Google - Software Engineer (L4)")
    'google-software-engineer-l4'
    >>> _sanitize_label("../../etc/passwd")
    'etcpasswd'
    >>> _sanitize_label("")
    'resume'
    """
    label = raw.lower().strip()
    label = label.replace(" ", "-").replace("_", "-")
    label = re.sub(r"[^a-z0-9-]", "", label)
    label = re.sub(r"-{2,}", "-", label)
    label = label.strip("-")
    return label[:50] if label else "resume"


def _make_resume_id(label: str) -> str:
    """
    Generate a unique resume ID from a label.

    Format: YYYYMMDD-HHMMSS_sanitized-label
    Example: 20260701-005402_master

    The timestamp is precise to the second.  For a single-user
    library, this is sufficient — you won't generate two resumes
    in the same second.
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_label = _sanitize_label(label)
    return f"{timestamp}_{safe_label}"


def _validate_resume_id(user_id: str, resume_id: str) -> Path:
    """
    Validate a resume ID and return the safe, resolved path inside
    the user's resumes directory.

    SECURITY: This is the gatekeeper for all read/delete ops.
    It prevents path traversal by:
      1. Constructing the candidate path from user_resumes_dir(user_id) / id
      2. Resolving to an absolute canonical path
      3. Verifying it lives strictly *inside* the user's dir (not
         equal to it — resume_id="." would otherwise resolve to
         the user's resumes dir itself, and delete_resume(".")
         would rmtree() the user's entire library)

    Raises
    ------
    ValueError
        If the ID is empty, contains suspicious characters,
        or resolves to a path outside (or equal to) the user's dir.
    FileNotFoundError
        If the resolved folder doesn't exist on disk.
    """
    if not resume_id or not resume_id.strip():
        raise ValueError("Resume ID cannot be empty.")

    base = user_resumes_dir(user_id).resolve()
    candidate = (base / resume_id).resolve()

    if candidate == base or not candidate.is_relative_to(base):
        raise ValueError("Invalid resume ID: path traversal detected.")

    if not candidate.is_dir():
        raise FileNotFoundError(f"Resume '{resume_id}' not found.")

    return candidate


# ══════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════

def save_resume(
    user_id: str,
    tex_string: str,
    pdf_path: Path,
    resume_type: str,
    label: str,
    job_description: Optional[str] = None,
) -> str:
    """
    Persist a generated resume to its own folder inside the user's
    library.

    Parameters
    ----------
    user_id : str
        The authenticated user's id.
    tex_string : str
        The complete LaTeX source that produced the PDF.
    pdf_path : Path
        Path to the compiled PDF (from compile_pdf()).
        This file will be COPIED into the new folder.
    resume_type : str
        Either "master" or "tailored".
    label : str
        Human-readable label (e.g. "Master", "Google SWE").
    job_description : str, optional
        The job description used for tailored resumes.

    Returns
    -------
    str
        The resume ID (which is also the folder name).
    """
    resume_id = _make_resume_id(label)
    resume_dir = user_resumes_dir(user_id) / resume_id
    resume_dir.mkdir(parents=True, exist_ok=True)

    # ── Write the .tex source ─────────────────────────────────
    tex_path = resume_dir / _TEX_FILENAME
    tex_path.write_text(tex_string, encoding="utf-8")

    # ── Copy the .pdf ─────────────────────────────────────────
    # We copy rather than move because the caller (app.py) might
    # still need the original path for error handling or logging.
    dest_pdf = resume_dir / _PDF_FILENAME
    shutil.copy2(pdf_path, dest_pdf)

    # ── Write metadata.json ───────────────────────────────────
    metadata = {
        "id": resume_id,
        "type": resume_type,
        "label": label,
        "date": datetime.now().isoformat(),
        "job_description": job_description,
        "user_id": user_id,
    }
    meta_path = resume_dir / _META_FILENAME
    meta_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return resume_id


def list_resumes(user_id: str) -> list[dict]:
    """
    Scan the user's resumes directory and return metadata for all
    saved resumes. Returns a list of metadata dicts sorted newest-first.
    Folders without a valid metadata.json are silently skipped
    (they might be leftover temp files from failed compilations).
    """
    resumes_dir = user_resumes_dir(user_id)

    resumes = []
    for entry in resumes_dir.iterdir():
        if not entry.is_dir():
            continue

        meta_path = entry / _META_FILENAME
        if not meta_path.exists():
            continue

        try:
            raw = meta_path.read_text(encoding="utf-8")
            meta = json.loads(raw)
            # Ensure the ID matches the folder name
            meta["id"] = entry.name
            # Check that the PDF actually exists
            meta["has_pdf"] = (entry / _PDF_FILENAME).exists()
            meta["has_tex"] = (entry / _TEX_FILENAME).exists()
            resumes.append(meta)
        except (json.JSONDecodeError, KeyError):
            # Corrupt metadata — skip this entry
            continue

    # Sort by date, newest first
    resumes.sort(key=lambda r: r.get("date", ""), reverse=True)
    return resumes


def delete_resumes_by_type(user_id: str, resume_type: str) -> int:
    """
    Delete every saved resume of the given type (e.g. "master") for
    the given user.

    WHY THIS EXISTS
    ----------------
    Every "Generate Master PDF" click used to call save_resume() with
    a fresh timestamp, so regenerating the same master resume after
    every profile edit piled up an ever-growing stack of "Master"
    entries that only differed by date. The master resume is a single
    canonical document, not a history — callers use this to clear out
    prior master entries immediately before saving the newly generated
    one, so exactly one "Master" entry ever exists in a user's library
    at a time. Tailored resumes are unaffected; each one is a distinct,
    intentionally-kept artifact for a specific job application.

    IMPORTANT: This must call list_resumes(user_id) — the user-scoped
    list — not a global one, so it never deletes another user's masters.

    Returns the number of resume folders removed.
    """
    removed = 0
    for meta in list_resumes(user_id):
        if meta.get("type") != resume_type:
            continue
        try:
            delete_resume(user_id, meta["id"])
            removed += 1
        except (ValueError, FileNotFoundError):
            continue
    return removed


def get_resume_path(user_id: str, resume_id: str, file_type: str) -> Path:
    """
    Get the path to a specific file in a resume folder inside the
    user's library.

    Parameters
    ----------
    user_id : str
        The authenticated user's id.
    resume_id : str
        The folder name / resume ID.
    file_type : str
        Either "pdf" or "tex".

    Returns
    -------
    Path
        Absolute path to the requested file.

    Raises
    ------
    ValueError
        If the resume ID fails validation or file_type is invalid.
    FileNotFoundError
        If the resume folder or file doesn't exist.
    """
    resume_dir = _validate_resume_id(user_id, resume_id)

    if file_type == "pdf":
        file_path = resume_dir / _PDF_FILENAME
    elif file_type == "tex":
        file_path = resume_dir / _TEX_FILENAME
    else:
        raise ValueError(f"Invalid file type: {file_type!r}. Use 'pdf' or 'tex'.")

    if not file_path.exists():
        raise FileNotFoundError(
            f"File '{file_type}' not found for resume '{resume_id}'."
        )

    return file_path


def delete_resume(user_id: str, resume_id: str) -> bool:
    """
    Delete a saved resume and its entire folder from the user's library.

    Uses resolve-and-verify to prevent path traversal:
      1. Construct: user_resumes_dir(user_id) / resume_id
      2. Resolve:   collapse .., symlinks → canonical path
      3. Verify:    canonical path is inside the user's resumes dir
      4. Delete:    shutil.rmtree() only if all checks pass

    Parameters
    ----------
    user_id : str
        The authenticated user's id.
    resume_id : str
        The folder name / resume ID to delete.

    Returns
    -------
    bool
        True if the folder was deleted.

    Raises
    ------
    ValueError
        If the ID fails safety validation.
    FileNotFoundError
        If the resume doesn't exist.
    """
    resume_dir = _validate_resume_id(user_id, resume_id)

    # ── Safe to delete ────────────────────────────────────────
    shutil.rmtree(resume_dir)
    return True


def rename_resume(user_id: str, resume_id: str, new_label: str) -> dict:
    """
    Update a saved resume's display label in metadata.json.

    Only the human-readable label changes — the folder name / resume
    ID stays the same, so existing download links keep working.

    Parameters
    ----------
    user_id : str
        The authenticated user's id.
    resume_id : str
        The folder name / resume ID to rename.
    new_label : str
        The new display label. Whitespace-only input is rejected.

    Returns
    -------
    dict
        The updated metadata.

    Raises
    ------
    ValueError
        If the resume ID fails validation, or new_label is empty.
    FileNotFoundError
        If the resume doesn't exist.
    """
    new_label = (new_label or "").strip()
    if not new_label:
        raise ValueError("Label cannot be empty.")

    resume_dir = _validate_resume_id(user_id, resume_id)
    meta_path = resume_dir / _META_FILENAME
    if not meta_path.exists():
        raise FileNotFoundError(f"Metadata not found for resume '{resume_id}'.")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["label"] = new_label
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    meta["id"] = resume_id
    return meta


def duplicate_resume(user_id: str, resume_id: str, new_label: Optional[str] = None) -> str:
    """
    Copy a saved resume (tex + pdf + metadata) into a new library entry.

    Useful for branching a tailored resume before making further manual
    edits, without losing the original.

    Parameters
    ----------
    user_id : str
        The authenticated user's id.
    resume_id : str
        The folder name / resume ID to duplicate.
    new_label : str, optional
        Display label for the copy. Defaults to "<original label> (Copy)".

    Returns
    -------
    str
        The new resume's ID.

    Raises
    ------
    ValueError
        If the source resume ID fails validation.
    FileNotFoundError
        If the source resume doesn't exist.
    """
    source_dir = _validate_resume_id(user_id, resume_id)
    meta_path = source_dir / _META_FILENAME
    if not meta_path.exists():
        raise FileNotFoundError(f"Metadata not found for resume '{resume_id}'.")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    label = (new_label or "").strip() or f"{meta.get('label', 'Resume')} (Copy)"

    new_id = _make_resume_id(label)
    new_dir = user_resumes_dir(user_id) / new_id
    shutil.copytree(source_dir, new_dir)

    new_meta = dict(meta)
    new_meta["id"] = new_id
    new_meta["label"] = label
    new_meta["date"] = datetime.now().isoformat()
    new_meta["user_id"] = user_id
    (new_dir / _META_FILENAME).write_text(
        json.dumps(new_meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return new_id
