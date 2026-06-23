# ──────────────────────────────────────────────────────────────
# services/pdf_service.py — Compile a .tex string to PDF via Tectonic
# ──────────────────────────────────────────────────────────────
#
# WHAT THIS FILE DOES
# -------------------
# One function: compile_pdf().
#   Input:  a complete .tex string (from latex_service.render_latex)
#   Output: the Path to the generated .pdf file
#
# HOW SUBPROCESS CALLS TECTONIC
# ─────────────────────────────
# Tectonic is a standalone LaTeX compiler.  Unlike a full TeX Live
# install (which is 4+ GB), Tectonic is a single binary (~50 MB)
# that auto-downloads only the LaTeX packages your document needs.
#
# We call it via Python's subprocess module:
#
#   subprocess.run(
#       ["tectonic", "master_resume.tex"],
#       cwd=output_dir,           # run in the directory with the .tex
#       capture_output=True,      # capture stdout + stderr
#       text=True,                # decode output as UTF-8 strings
#       timeout=120,              # kill if it takes > 2 minutes
#   )
#
# WHAT EACH ARGUMENT DOES:
#   ["tectonic", "master_resume.tex"]
#       → The command + arguments, as a list (safer than a shell string).
#         Tectonic reads the .tex file, resolves \usepackage{} deps,
#         and writes master_resume.pdf in the same directory.
#
#   cwd=output_dir
#       → Sets the working directory for the child process.  Tectonic
#         writes output files relative to cwd, so the .pdf lands in
#         our storage/resumes/ folder.
#
#   capture_output=True
#       → Equivalent to stdout=PIPE, stderr=PIPE.  We capture both
#         streams so we can include them in error messages.
#
#   text=True
#       → Returns stdout/stderr as str instead of bytes.
#
#   timeout=120
#       → Safety net.  If Tectonic hangs (e.g., downloading a huge
#         package over a slow network), we kill it after 2 minutes
#         rather than blocking the web server forever.
#
# RETURN CODE HANDLING:
#   result.returncode == 0  → success, .pdf exists
#   result.returncode != 0  → compilation failed; stderr has the log
#
# INSTALLING TECTONIC
# ───────────────────
# If Tectonic is not on PATH, subprocess raises FileNotFoundError.
# We catch this and raise a clear error telling the user to install:
#   Windows:  winget install --id=AnotherRedFox.Tectonic -e
#   macOS:    brew install tectonic
#   Linux:    cargo install tectonic
#   Any OS:   conda install -c conda-forge tectonic
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


# ── Custom exception for compilation failures ─────────────────
class PdfCompilationError(Exception):
    """
    Raised when Tectonic fails to compile a .tex file to PDF.

    Attributes
    ----------
    log : str
        The full Tectonic error log (stderr + stdout combined).
        This is included so the API route can return it to the
        user for debugging.
    """

    def __init__(self, message: str, log: str = ""):
        super().__init__(message)
        self.log = log


def _find_tectonic() -> str:
    """
    Locate the Tectonic binary.

    WHY WE NEED THIS
    -----------------
    On Windows, users often download tectonic.exe and drop it in
    their home directory (C:\\Users\\<name>\\tectonic.exe) without
    adding that folder to PATH.  subprocess.run(["tectonic", ...])
    raises FileNotFoundError because it only searches PATH.

    SEARCH ORDER
    ------------
    1. PATH (via shutil.which — the standard approach)
    2. User's home directory (common drop location on Windows)
    3. Give up and return "tectonic" — let subprocess raise the
       FileNotFoundError so our caller can produce a helpful message.

    Returns
    -------
    str
        Absolute path to the tectonic binary, or "tectonic" if
        not found (so subprocess gives a clean error).
    """
    # ── 1. Check PATH ─────────────────────────────────────────
    found = shutil.which("tectonic")
    if found:
        return found

    # ── 2. Check user's home directory (Windows common case) ──
    home_path = Path.home() / "tectonic.exe"
    if home_path.exists():
        return str(home_path)

    # Also check without .exe (Linux/macOS)
    home_path_unix = Path.home() / "tectonic"
    if home_path_unix.exists():
        return str(home_path_unix)

    # ── 3. Not found — return bare name for the error path ────
    return "tectonic"


# ── Output filenames ──────────────────────────────────────────
_TEX_FILENAME = "master_resume.tex"
_PDF_FILENAME = "master_resume.pdf"


def compile_pdf(tex_string: str, output_dir: Path) -> Path:
    """
    Write a .tex string to disk and compile it to PDF via Tectonic.

    Parameters
    ----------
    tex_string : str
        A complete LaTeX document (output of render_latex()).
    output_dir : Path
        Directory where the .tex and .pdf files will be written.
        Typically storage/resumes/.

    Returns
    -------
    Path
        Absolute path to the generated .pdf file.

    Raises
    ------
    PdfCompilationError
        If Tectonic is not installed, the .tex has errors, or
        compilation times out.

    HOW IT WORKS
    ------------
    1. Ensure output_dir exists (create if needed).
    2. Write tex_string to output_dir/master_resume.tex.
    3. Run `tectonic master_resume.tex` in that directory.
    4. If Tectonic succeeds → return path to the .pdf.
       If it fails → raise PdfCompilationError with the log.
    """

    # ── Step 1: Ensure directory exists ───────────────────────
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 2: Write the .tex file ───────────────────────────
    tex_path = output_dir / _TEX_FILENAME
    tex_path.write_text(tex_string, encoding="utf-8")

    # ── Step 3: Call Tectonic ─────────────────────────────────
    tectonic_bin = _find_tectonic()
    try:
        result = subprocess.run(
            [tectonic_bin, _TEX_FILENAME],
            cwd=str(output_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        # Tectonic binary is not on PATH
        raise PdfCompilationError(
            "Tectonic is not installed or not on your PATH. "
            "Install it with one of:\n"
            "  Windows:  winget install --id=AnotherRedFox.Tectonic -e\n"
            "  macOS:    brew install tectonic\n"
            "  Linux:    cargo install tectonic\n"
            "  Any OS:   conda install -c conda-forge tectonic\n"
            "Then restart the server.",
            log="",
        )
    except subprocess.TimeoutExpired:
        raise PdfCompilationError(
            "Tectonic timed out after 120 seconds. "
            "This can happen on the first run when it downloads "
            "LaTeX packages.  Try again — subsequent runs use the cache.",
            log="",
        )

    # ── Step 4: Check result ──────────────────────────────────
    if result.returncode != 0:
        # Combine stdout + stderr for a complete error log.
        full_log = (result.stdout or "") + "\n" + (result.stderr or "")
        raise PdfCompilationError(
            "Tectonic failed to compile the LaTeX file. "
            "See the log for details.",
            log=full_log.strip(),
        )

    # ── Success: return the path to the PDF ───────────────────
    pdf_path = output_dir / _PDF_FILENAME
    if not pdf_path.exists():
        # Paranoia check — Tectonic said success but no PDF?
        raise PdfCompilationError(
            "Tectonic reported success but no PDF was generated. "
            "This is unexpected — please check the .tex file manually.",
            log=(result.stdout or "") + "\n" + (result.stderr or ""),
        )

    return pdf_path
