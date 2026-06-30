# ──────────────────────────────────────────────────────────────
# services/resume_library.py — Persistent resume storage & retrieval
# ──────────────────────────────────────────────────────────────
#
# WHAT THIS FILE DOES
# -------------------
# Manages a library of generated resumes on disk.  Each resume
# lives in its own folder under storage/resumes/, containing:
#   • resume.tex   — the editable LaTeX source
#   • resume.pdf   — the compiled PDF
#   • metadata.json — type, label, date, job description (if any)
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
#   • Human-readable — you can browse storage/resumes/ directly.
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
#   1. Construct candidate path: RESUMES_DIR / id
#   2. Call .resolve() to collapse .., symlinks, etc.
#   3. Assert resolved path starts with RESUMES_DIR.resolve()
#   4. Only then call shutil.rmtree()
# This blocks ../../../etc/passwd and symlink attacks.
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional


# ── Base directory for all saved resumes ──────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESUMES_DIR = _PROJECT_ROOT / "storage" / "resumes"

# ── Filenames used inside each resume folder ──────────────────
_TEX_FILENAME = "resume.tex"
_PDF_FILENAME = "resume.pdf"
_META_FILENAME = "metadata.json"

# ── Files produced by pdf_service.compile_pdf() ───────────────
# compile_pdf() writes master_resume.tex and master_resume.pdf
# into whatever output_dir we give it.  After compilation, we
# rename these to our canonical names (resume.tex, resume.pdf).
_COMPILED_TEX = "master_resume.tex"
_COMPILED_PDF = "master_resume.pdf"


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
    tool, this is sufficient — you won't generate two resumes
    in the same second.
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_label = _sanitize_label(label)
    return f"{timestamp}_{safe_label}"


def _validate_resume_id(resume_id: str) -> Path:
    """
    Validate a resume ID and return the safe, resolved path.

    SECURITY: This is the gatekeeper for all read/delete ops.
    It prevents path traversal by:
      1. Constructing the candidate path from RESUMES_DIR / id
      2. Resolving to an absolute canonical path
      3. Verifying it lives inside RESUMES_DIR

    Raises
    ------
    ValueError
        If the ID is empty, contains suspicious characters,
        or resolves to a path outside RESUMES_DIR.
    FileNotFoundError
        If the resolved folder doesn't exist on disk.
    """
    if not resume_id or not resume_id.strip():
        raise ValueError("Resume ID cannot be empty.")

    # Construct and resolve
    candidate = (RESUMES_DIR / resume_id).resolve()
    base = RESUMES_DIR.resolve()

    # ── Path traversal check ──────────────────────────────────
    # .resolve() collapses .., follows symlinks, etc.
    # We verify the result is still inside our base directory.
    if not str(candidate).startswith(str(base) + ("/" if not str(base).endswith("/") else "")):
        # Use os.sep check that works on Windows too
        if candidate != base and not str(candidate).startswith(str(base) + "\\") and not str(candidate).startswith(str(base) + "/"):
            raise ValueError(f"Invalid resume ID: path traversal detected.")

    if not candidate.is_dir():
        raise FileNotFoundError(f"Resume '{resume_id}' not found.")

    return candidate


# ══════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════

def save_resume(
    tex_string: str,
    pdf_path: Path,
    resume_type: str,
    label: str,
    job_description: Optional[str] = None,
) -> str:
    """
    Persist a generated resume to its own folder.

    Parameters
    ----------
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
    resume_dir = RESUMES_DIR / resume_id
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
    }
    meta_path = resume_dir / _META_FILENAME
    meta_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return resume_id


def list_resumes() -> list[dict]:
    """
    Scan storage/resumes/ and return metadata for all saved resumes.

    Returns a list of metadata dicts, sorted newest-first.
    Folders without a valid metadata.json are silently skipped
    (they might be leftover temp files from failed compilations).
    """
    RESUMES_DIR.mkdir(parents=True, exist_ok=True)

    resumes = []
    for entry in RESUMES_DIR.iterdir():
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


def get_resume_path(resume_id: str, file_type: str) -> Path:
    """
    Get the path to a specific file in a resume folder.

    Parameters
    ----------
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
    resume_dir = _validate_resume_id(resume_id)

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


def delete_resume(resume_id: str) -> bool:
    """
    Delete a saved resume and its entire folder.

    Uses resolve-and-verify to prevent path traversal:
      1. Construct: RESUMES_DIR / resume_id
      2. Resolve:   collapse .., symlinks → canonical path
      3. Verify:    canonical path is inside RESUMES_DIR
      4. Delete:    shutil.rmtree() only if all checks pass

    Parameters
    ----------
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
    resume_dir = _validate_resume_id(resume_id)

    # ── Safe to delete ────────────────────────────────────────
    shutil.rmtree(resume_dir)
    return True
