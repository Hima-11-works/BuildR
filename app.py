# ──────────────────────────────────────────────────────────────
# app.py — Application entry-point for the Resume Generator
# ──────────────────────────────────────────────────────────────
#
# PURPOSE
# -------
# This is the *single* file you run to start the web server.
# It wires together configuration, routes, and (later) services.
#
# KEY DECISIONS
# -------------
# 1. We call load_dotenv() BEFORE creating the Flask app so that
#    every module that reads os.getenv() sees the values from .env.
#
# 2. Flask(__name__) tells Flask to look for templates/ and static/
#    relative to THIS file's directory.  (More on this below.)
#
# 3. debug=True enables:
#      • Auto-reload when you edit Python files (no manual restart).
#      • A rich in-browser debugger on unhandled exceptions.
#    NEVER use debug=True in production — it exposes a Python shell.
#
# ROUTE MAP
# ---------
#   GET  /              → Serves the profile editor HTML page
#   GET  /api/profile   → Returns the full profile as JSON
#   PUT  /api/profile   → Accepts JSON, validates via Pydantic,
#                          saves to disk, returns success or errors
# ──────────────────────────────────────────────────────────────

import os
from pathlib import Path as _Path
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, send_file
from pydantic import ValidationError

from models.profile import Profile
from services.storage_service import load_profile, save_profile
from services.latex_service import render_latex
from services.pdf_service import compile_pdf, PdfCompilationError
from services.resume_library import (
    save_resume, list_resumes, get_resume_path, delete_resume,
)

# ── Step 1: Load environment variables from .env ─────────────
# load_dotenv() reads the .env file in the project root and
# injects each KEY=VALUE pair into os.environ.  This keeps
# secrets out of source code.
load_dotenv()

from services.ai_service import tailor_resume, parse_resume_text
from services.scraper_service import fetch_job_description, ScrapingError
from services.parser_service import extract_text_from_pdf, extract_text_from_docx

# ── Step 2: Create the Flask application instance ────────────
# Flask(__name__) uses the location of THIS module to determine:
#   • templates/  → folder for Jinja2 HTML templates
#   • static/     → folder for CSS, JS, images served at /static/
# You can override these with template_folder= and static_folder=
# parameters, but the defaults work perfectly for our layout.
app = Flask(__name__)


# ── Step 3: Define routes ────────────────────────────────────
@app.route("/")
def index():
    """
    Home page — serves the profile editor interface.

    render_template("index.html") tells Flask:
      1. Look in the templates/ directory (resolved from __name__).
      2. Find index.html.
      3. Run it through the Jinja2 engine (so {{ }} expressions work).
      4. Return the rendered HTML string as an HTTP response.
    """
    return render_template("index.html")


# ── JSON API ─────────────────────────────────────────────────
# These two routes form a simple REST-style API.  The frontend
# JavaScript uses fetch() to talk to them — no page reloads,
# no form submissions.  Data travels as JSON in both directions.
#
# WHY SEPARATE API ROUTES?
# The index route serves HTML (for humans / browsers).
# The /api/ routes serve JSON (for JavaScript code).
# Keeping them separate means the same API could later be used
# by a mobile app, a CLI tool, or any other client.
# ──────────────────────────────────────────────────────────────

@app.route("/api/profile", methods=["GET"])
def api_get_profile():
    """
    Return the current profile as JSON.

    HOW IT WORKS
    ------------
    1. load_profile() reads storage/profile.json (or returns defaults).
    2. profile.model_dump() converts the Pydantic model to a plain dict.
    3. jsonify() serializes that dict to a JSON HTTP response with the
       correct Content-Type header (application/json).

    The browser receives something like:
        {
            "personal_info": {"name": "Alice", "email": "a@b.com", ...},
            "education": [...],
            ...
        }
    """
    try:
        profile = load_profile()
        profile_dict = profile.model_dump()
        
        # Determine if a valid master resume exists (requiring at least name and email)
        has_valid_resume = False
        if profile.personal_info.name.strip() and profile.personal_info.email.strip():
            has_valid_resume = True
            
        profile_dict["has_valid_resume"] = has_valid_resume
        return jsonify(profile_dict)
    except Exception as e:
        # If the JSON file is corrupt or unreadable, tell the client
        return jsonify({"error": f"Failed to load profile: {str(e)}"}), 500


@app.route("/api/profile", methods=["PUT"])
def api_put_profile():
    """
    Accept a JSON profile, validate it, and save it to disk.

    HOW IT WORKS
    ------------
    1. request.get_json() parses the raw JSON body into a Python dict.
       Flask does this automatically when Content-Type is application/json.

    2. Profile.model_validate(data) runs the full Pydantic validation:
       - Checks every required field is present
       - Coerces types where possible (e.g., "3.9" → 3.9 for GPA)
       - Enforces constraints (min_length, ge, le, etc.)
       - Validates all nested models recursively
       If anything fails, Pydantic raises a ValidationError.

    3. On success: save_profile() writes the validated profile to disk,
       and we return {"status": "ok"}.

    4. On ValidationError: we extract the structured error list from
       Pydantic and return it as a 422 response.  Each error has:
         - loc:  path to the bad field, e.g. ["education", 0, "institution"]
         - msg:  human-readable message, e.g. "String should have at least 1 character"
         - type: error type code, e.g. "string_too_short"

    WHY 422?
    HTTP 422 means "Unprocessable Entity" — the JSON was syntactically
    valid, but the data didn't pass our business rules.  This is more
    precise than 400 (Bad Request), which usually means malformed syntax.
    """
    data = request.get_json()

    # ── Guard: no JSON body at all ────────────────────────────
    if data is None:
        return jsonify({"error": "Request body must be JSON"}), 400

    try:
        # ── Validate through Pydantic ─────────────────────────
        profile = Profile.model_validate(data)

        # ── Persist to disk ───────────────────────────────────
        save_profile(profile)

        return jsonify({"status": "ok"})

    except ValidationError as e:
        # ── Extract structured errors ─────────────────────────
        # e.errors() returns a list of dicts, each like:
        # {
        #     "type": "string_too_short",
        #     "loc": ("education", 0, "institution"),
        #     "msg": "String should have at least 1 character",
        #     "input": "",
        #     ...
        # }
        #
        # We convert `loc` tuples to lists for JSON serialization,
        # and pick only the fields the frontend needs.
        errors = []
        for err in e.errors():
            errors.append({
                "loc": list(err["loc"]),
                "msg": err["msg"],
                "type": err["type"],
            })

        return jsonify({"errors": errors}), 422


@app.route("/api/profile/parse", methods=["POST"])
def api_parse_resume():
    """
    Accept a PDF or DOCX file, extract text, call Gemini to parse it into Profile model, and return as JSON.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file part in the request"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400
    
    filename = file.filename.lower()
    if not (filename.endswith(".pdf") or filename.endswith(".docx")):
        return jsonify({"error": "Unsupported file format. Please upload a PDF or DOCX file."}), 400
        
    try:
        import io
        file_stream = io.BytesIO(file.read())
        
        if filename.endswith(".pdf"):
            text = extract_text_from_pdf(file_stream)
        else:
            text = extract_text_from_docx(file_stream)
            
        if not text.strip():
            return jsonify({"error": "The uploaded file contains no readable text."}), 400
            
        # Parse text via Gemini into Profile object
        parsed_profile = parse_resume_text(text)
        
        return jsonify(parsed_profile.model_dump())
    except Exception as e:
        print(f"Error parsing resume: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Failed to parse resume: {str(e)}"}), 500


# ── Resume Generation API ────────────────────────────────────
# This route orchestrates the entire document pipeline:
#   1. Load the saved profile from disk
#   2. Render it into a LaTeX .tex string (with escaping)
#   3. Compile the .tex to PDF via Tectonic
#   4. Return the PDF as a downloadable file
#
# WHY POST AND NOT GET?
# Generating a PDF is an expensive, side-effect-producing
# operation (it writes files to disk and spawns a subprocess).
# POST is the correct HTTP verb for "perform an action",
# while GET is for "retrieve existing data".
# ──────────────────────────────────────────────────────────────

# Where generated resumes live on disk
_RESUMES_DIR = _Path(__file__).resolve().parent / "storage" / "resumes"


@app.route("/api/resume/master", methods=["POST"])
def api_generate_master_resume():
    """
    Generate the master resume as a PDF and return it for download.

    PIPELINE
    --------
    1. load_profile()     → Profile object (from storage/profile.json)
    2. render_latex()     → .tex string (escaped + templated)
    3. compile_pdf()      → .pdf file (via Tectonic subprocess)
    4. send_file()        → HTTP response with PDF as attachment

    ERROR HANDLING
    --------------
    • Empty profile (no name)  → 400 with a helpful message.
    • Tectonic not installed    → 500 with install instructions.
    • LaTeX compilation error   → 500 with the Tectonic log.
    • Any other exception       → 500 with the error message.
    """
    try:
        # ── Step 1: Load the profile ──────────────────────────
        profile = load_profile()

        # ── Guard: don't generate a resume with no name ───────
        if not profile.personal_info.name.strip():
            return jsonify({
                "error": "Your profile has no name. "
                         "Please fill in at least your name before generating."
            }), 400

        # ── Step 2: Render LaTeX ──────────────────────────────
        tex_string = render_latex(profile)

        # ── Step 3: Compile to PDF ────────────────────────────
        pdf_path = compile_pdf(tex_string, _RESUMES_DIR)

        # ── Step 4: Persist to the resume library ─────────────
        # save_resume() creates a timestamped subfolder with the
        # .tex, .pdf, and metadata.json — making this resume
        # browsable and downloadable later.
        resume_id = save_resume(
            tex_string=tex_string,
            pdf_path=pdf_path,
            resume_type="master",
            label="Master",
        )

        # ── Step 5: Return JSON with the resume ID ────────────
        # The frontend uses this ID to download via
        # /api/resumes/<id>/pdf rather than receiving a blob.
        return jsonify({
            "status": "ok",
            "id": resume_id,
            "label": "Master",
        })

    except PdfCompilationError as e:
        # Tectonic-specific errors (not installed, compile failure)
        print(f"\n{'='*60}")
        print(f"PDF COMPILATION ERROR: {e}")
        if e.log:
            print(f"Tectonic log:\n{e.log}")
        print(f"{'='*60}\n")
        return jsonify({
            "error": str(e),
            "log": e.log,
        }), 500

    except Exception as e:
        print(f"\n{'='*60}")
        print(f"RESUME GENERATION ERROR: {e}")
        import traceback
        traceback.print_exc()
        print(f"{'='*60}\n")
        return jsonify({
            "error": f"Failed to generate resume: {str(e)}"
        }), 500


# ── Tailored Resume Generation API (AI-powered) ─────────────
# This route is the AI-powered counterpart of /api/resume/master.
# Instead of rendering the full profile as-is, it:
#   1. Loads the saved profile from disk.
#   2. Sends it + a job description to Gemini via ai_service.
#   3. Gemini returns structured JSON (not LaTeX!) describing
#      which items to include and how to rewrite them.
#   4. Converts that JSON to a Profile and renders it through
#      the same LaTeX → PDF pipeline.
#
# THE "AI DECIDES, CODE RENDERS" PATTERN
# ──────────────────────────────────────
# The AI never sees or produces LaTeX.  It only decides WHAT
# content to include and HOW to phrase it.  Our code handles
# all rendering — escaping, templating, compilation.
#
# This is more reliable than asking the AI to produce LaTeX
# because:
#   • Constrained decoding guarantees valid JSON output.
#   • Any LaTeX typo (unmatched brace, missing \) would crash
#     the compiler.  JSON can't have such issues.
#   • We can validate the AI's output with Pydantic before
#     even touching LaTeX.
#   • The template can change without rewriting the AI prompt.
# ──────────────────────────────────────────────────────────────

@app.route("/api/resume/tailored", methods=["POST"])
def api_generate_tailored_resume():
    """
    Generate a tailored resume as a PDF, optimized for a specific job.

    EXPECTS
    -------
    JSON body — at least one of:
        { "job_description": "<pasted job posting text>" }
        { "job_url": "https://careers.example.com/posting/12345" }

    If both are provided, job_description (pasted text) takes priority
    because it's the reliable path — the user already has the text.

    If only job_url is given, we attempt to scrape the page.  Scraping
    is best-effort: many sites block bots, require JavaScript, or hide
    content behind login walls.  On failure, we return a clear message
    telling the user to paste the text manually.

    PIPELINE
    --------
    1. Parse the job description from the request body (text or URL).
    2. Load the saved profile from disk.
    3. Send both to Gemini → get back a TailoredProfile (JSON).
    4. Convert TailoredProfile → Profile (for rendering).
    5. Render LaTeX → compile PDF → return as download.
    """
    # ── Step 1: Get the job description (text or URL) ─────────
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON."}), 400

    job_description = (data.get("job_description") or "").strip()
    job_url = (data.get("job_url") or "").strip()

    # Text takes priority — it's the reliable path
    if not job_description:
        if job_url:
            # Attempt to scrape the URL.  ScrapingError carries
            # a user-friendly message that we can return directly.
            try:
                job_description = fetch_job_description(job_url)
            except ScrapingError as e:
                return jsonify({"error": str(e)}), 400
        else:
            return jsonify({
                "error": "Please paste a job description or provide a URL."
            }), 400

    try:
        # ── Step 2: Load the profile ──────────────────────────
        profile = load_profile()

        # ── Guard: don't tailor an empty profile ──────────────
        if not profile.personal_info.name.strip():
            return jsonify({
                "error": "Your profile has no name. "
                         "Please fill in your profile before tailoring."
            }), 400

        # ── Step 3: Call Gemini for tailoring ─────────────────
        tailored = tailor_resume(profile, job_description)

        # ── Step 4: Convert to Profile for rendering ─────────
        tailored_profile = tailored.to_profile()

        # ── Step 5: Render LaTeX → PDF ────────────────────────
        tex_string = render_latex(tailored_profile)
        pdf_path = compile_pdf(tex_string, _RESUMES_DIR)

        # ── Step 6: Persist to the resume library ─────────────
        # Extract a meaningful label from the job description.
        # Use the first ~60 chars as a rough label; the full
        # description is preserved in metadata.json.
        label = job_description[:60].strip()
        if len(job_description) > 60:
            label += "…"

        resume_id = save_resume(
            tex_string=tex_string,
            pdf_path=pdf_path,
            resume_type="tailored",
            label=label,
            job_description=job_description,
        )

        # ── Step 7: Return JSON with the resume ID ────────────
        return jsonify({
            "status": "ok",
            "id": resume_id,
            "label": label,
        })

    except PdfCompilationError as e:
        print(f"\n{'='*60}")
        print(f"PDF COMPILATION ERROR (tailored): {e}")
        if e.log:
            print(f"Tectonic log:\n{e.log}")
        print(f"{'='*60}\n")
        return jsonify({
            "error": str(e),
            "log": e.log,
        }), 500

    except Exception as e:
        print(f"\n{'='*60}")
        print(f"TAILORED RESUME ERROR: {e}")
        import traceback
        traceback.print_exc()
        print(f"{'='*60}\n")
        return jsonify({
            "error": f"Failed to generate tailored resume: {str(e)}"
        }), 500


# ── Resume Library API ───────────────────────────────────────
# These routes let the frontend browse, download, and delete
# previously generated resumes.  Each resume is stored in its
# own folder under storage/resumes/ with .tex, .pdf, and
# metadata.json.
# ──────────────────────────────────────────────────────────────

@app.route("/api/resumes", methods=["GET"])
def api_list_resumes():
    """
    List all saved resumes with their metadata.

    Returns a JSON array sorted newest-first, e.g.:
    [
        {
            "id": "20260701-005402_master",
            "type": "master",
            "label": "Master",
            "date": "2026-07-01T00:54:02",
            "has_pdf": true,
            "has_tex": true
        },
        ...
    ]
    """
    try:
        resumes = list_resumes()
        return jsonify(resumes)
    except Exception as e:
        return jsonify({"error": f"Failed to list resumes: {str(e)}"}), 500


@app.route("/api/resumes/<resume_id>/pdf", methods=["GET"])
def api_download_resume_pdf(resume_id):
    """
    Download the PDF for a specific saved resume.

    The resume_id is the folder name (e.g. "20260701-005402_master").
    Path traversal is blocked by resume_library.get_resume_path().
    """
    try:
        pdf_path = get_resume_path(resume_id, "pdf")
        return send_file(
            pdf_path,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"{resume_id}.pdf",
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Failed to download PDF: {str(e)}"}), 500


@app.route("/api/resumes/<resume_id>/tex", methods=["GET"])
def api_download_resume_tex(resume_id):
    """
    Download the editable .tex source for a specific saved resume.

    Same safety checks as the PDF route.
    """
    try:
        tex_path = get_resume_path(resume_id, "tex")
        return send_file(
            tex_path,
            mimetype="application/x-tex",
            as_attachment=True,
            download_name=f"{resume_id}.tex",
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Failed to download .tex: {str(e)}"}), 500


@app.route("/api/resumes/<resume_id>", methods=["DELETE"])
def api_delete_resume(resume_id):
    """
    Delete a saved resume and its entire folder.

    SECURITY
    --------
    delete_resume() uses resolve-and-verify to ensure the
    target path is inside storage/resumes/.  Path traversal
    attempts (e.g. "../../etc") are rejected with a 400.
    """
    try:
        delete_resume(resume_id)
        return jsonify({"status": "ok"})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Failed to delete resume: {str(e)}"}), 500


# ── Step 4: Run the dev server ───────────────────────────────
if __name__ == "__main__":
    # This block only executes when you run `python app.py` directly.
    # It will NOT run when a production server (gunicorn, etc.) imports the module.
    app.run(debug=True, port=5000)
