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

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


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
    their home directory (C:\\\\Users\\\\<name>\\\\tectonic.exe) without
    adding that folder to PATH. subprocess.run(["tectonic", ...])
    raises FileNotFoundError because it only searches PATH.

    On Render we install Tectonic to <project_root>/.tectonic/
    tectonic via render-build.sh. The project root is carried into
    the runtime image verbatim, so this lookup is reliable across
    both local development and Render deploys.

    SEARCH ORDER
    ------------
    1. PATH (via shutil.which — covers system / apt installs)
    2. <project_root>/.tectonic/tectonic (render-build.sh target)
    3. ~/tectonic and ~/tectonic.exe (legacy local Windows setup)
    4. Return the bare name "tectonic" so subprocess raises
       FileNotFoundError and our caller can produce a helpful
       message. Every probe is logged for debuggability.

    Returns
    -------
    str
        Absolute path to the tectonic binary, or "tectonic" if
        not found (so subprocess gives a clean error).
    """
    candidates: list[str] = []

    # ── 1. Check PATH ─────────────────────────────────────────
    on_path = shutil.which("tectonic")
    if on_path:
        candidates.append(on_path)
        logger.info("Tectonic: candidate found on PATH: %s", on_path)

    # ── 2. Check <project_root>/.tectonic/tectonic ────────────
    # This file is the deployment target on Render. Project root
    # is two parents up from this file (services/pdf_service.py).
    project_root = Path(__file__).resolve().parent.parent
    bundled = project_root / ".tectonic" / "tectonic"
    if bundled.is_file():
        candidates.append(str(bundled))
        logger.info("Tectonic: candidate found in .tectonic/: %s", bundled)

    # ── 3. Check user's home directory ────────────────────────
    for home_name in ("tectonic", "tectonic.exe"):
        home_candidate = Path.home() / home_name
        if home_candidate.is_file():
            candidates.append(str(home_candidate))
            logger.info("Tectonic: candidate found in $HOME: %s", home_candidate)

    if not candidates:
        logger.error(
            "Tectonic binary not found on PATH or in $HOME (%s); "
            "subprocess will likely raise FileNotFoundError.",
            Path.home(),
        )
        return "tectonic"

    # Prefer the first candidate that the OS confirms is executable.
    for candidate in candidates:
        if os.access(candidate, os.X_OK):
            if candidate != candidates[0]:
                logger.info("Tectonic: using executable candidate %s", candidate)
            return candidate

    # Files exist but none are executable — surface that explicitly
    # so callers see a clearer error than a raw PermissionError.
    logger.warning(
        "Tectonic: candidate files exist but none are executable: %s",
        candidates,
    )
    return candidates[0] if candidates else "tectonic"


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

    # Pre-flight: if the resolver fell back to the bare name "tectonic"
    # and that name isn't resolvable on PATH, fail fast with the helpful
    # message instead of letting subprocess raise an opaque error.
    _NOT_FOUND_MSG = (
        "Tectonic is not installed or not on your PATH. "
        "Install it with one of:\n"
        "  Windows:  winget install --id=AnotherRedFox.Tectonic -e\n"
        "  macOS:    brew install tectonic\n"
        "  Linux:    cargo install tectonic\n"
        "  Any OS:   conda install -c conda-forge tectonic\n"
        "On Render, ensure render-build.sh ran successfully and that "
        "Tectonic is at .tectonic/tectonic inside the project root.\n"
        "Then restart the server."
    )
    if tectonic_bin == "tectonic" and shutil.which("tectonic") is None:
        logger.error(_NOT_FOUND_MSG)
        raise PdfCompilationError(_NOT_FOUND_MSG, log="")

    try:
        # ── Build subprocess env ────────────────────────────────
        # Repoint Tectonic's package cache to <project_root>/.tectonic/
        # cache/ ONLY when that directory has been pre-populated by
        # render-build.sh. The default cache (Tectonic's
        # $HOME/.cache/Tectonic) is fine on dev machines — overriding
        # it there would force a cold package download on every run
        # even when the user's real cache is already warm.
        #
        # On Render, render-build.sh creates .tectonic/cache/ and
        # populates it via a smoke compile, so the override takes
        # effect and the runtime image already has every package
        # Tectonic needs.
        project_root = Path(__file__).resolve().parent.parent
        cache_dir = project_root / ".tectonic" / "cache"
        sub_env = os.environ.copy()
        if cache_dir.is_dir() and any(cache_dir.iterdir()):
            sub_env["TECTONIC_CACHE_DIR"] = str(cache_dir)
            logger.debug("Tectonic using project cache: %s", cache_dir)

        result = subprocess.run(
            [tectonic_bin, _TEX_FILENAME],
            cwd=str(output_dir),
            env=sub_env,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError:
        # Tectonic binary is not on PATH
        raise PdfCompilationError(_NOT_FOUND_MSG, log="")
    except PermissionError as exc:
        # The file was located but isn't executable (chmod +x missing
        # or stripped during image build). Surface a clear message.
        logger.error("Tectonic found but not executable: %s", tectonic_bin)
        raise PdfCompilationError(
            f"Tectonic was located at {tectonic_bin} but is not "
            "executable. Rebuild the deployment to reapply chmod +x.",
            log=str(exc),
        )
    except subprocess.TimeoutExpired:
        raise PdfCompilationError(
            "Tectonic timed out after 180 seconds. "
            "This usually means the package cache is empty and "
            "Tectonic is downloading LaTeX packages over a slow "
            "network. On Render, ensure render-build.sh ran the "
            "pre-warm step successfully. Locally, try "
            "'tectonic --version' once to populate the cache.",
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
