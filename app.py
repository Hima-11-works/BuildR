# ──────────────────────────────────────────────────────────────
# app.py — Application entry-point for the Resume Generator
# ──────────────────────────────────────────────────────────────
#
# PURPOSE
# -------
# This is the *single* file you run to start the web server.
# It wires together configuration, routes, and services.
#
# MULTI-USER ARCHITECTURE
# -----------------------
# Every data-touching route is wrapped with @require_auth. The decorator
# reads the signed session cookie, and the route handler retrieves the
# user_id via `current_user_id()`. That user_id is threaded into every
# service call (load_profile, save_resume, session_service.create_session,
# etc.) so storage paths are scoped per-user.
#
# ROUTE MAP (public)
# ------------------
#   GET  /                       → Serves the SPA (sign-in overlay
#                                   renders until whoami returns true)
#   GET  /favicon.ico            → Favicon
#   POST /api/auth/sign-in       → Establish a session for an email
#   POST /api/auth/sign-out      → Tear down the session
#   GET  /api/auth/whoami        → Tell the SPA who (if anyone) is signed in
#
# ROUTE MAP (authenticated — @require_auth)
# ------------------------------------------
#   GET/PUT /api/profile         → master profile (read/write)
#   POST /api/profile/parse      → upload + AI parse of a resume file
#   POST /api/resume/master      → generate master PDF
#   POST /api/scrape-job         → fetch + extract a job posting URL
#   POST /api/analyze-job        → AI-extract skills/keywords
#   POST /api/tailor/start       → start tailoring workspace
#   POST /api/tailor/chat        → refine draft via AI
#   POST /api/tailor/snapshot    → save named snapshot of draft
#   POST /api/tailor/restore     → restore a snapshot
#   POST /api/tailor/save        → save draft to library
#   GET/PUT /api/tailor/draft/<id> → fetch / edit active draft
#   POST /api/tailor/download/<id>  → compile + download draft
#   GET /api/tailor/preview/<id>/...→ on-the-fly PDF preview
#   POST /api/resume/tailored    → one-shot tailored PDF
#   GET /api/resumes             → list user's library
#   GET /api/resumes/<id>/pdf    → download PDF
#   GET /api/resumes/<id>/tex    → download LaTeX source
#   DELETE /api/resumes/<id>     → delete resume
#   PATCH /api/resumes/<id>      → rename
#   POST /api/resumes/<id>/duplicate → duplicate
# ──────────────────────────────────────────────────────────────

import io
import json
import logging
import os
import secrets
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path as _Path
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, send_file
from pydantic import ValidationError

logger = logging.getLogger(__name__)

from models.profile import Profile
from services.storage_service import load_profile, save_profile
from services.latex_service import render_latex
from services.pdf_service import compile_pdf, PdfCompilationError
from services.resume_library import (
    save_resume, list_resumes, get_resume_path, delete_resume, delete_resumes_by_type,
    rename_resume, duplicate_resume,
)
from services.auth_service import (
    sign_in_with_google,
    sign_out as auth_sign_out,
    current_user_id,
    current_user_email,
    require_auth,
    user_id_from_google_sub,
    cleanup_expired_sessions_for_all_users,
    verify_google_id_token,
)

# ── Step 1: Load environment variables from .env ─────────────
# load_dotenv() reads the .env file in the project root and
# injects each KEY=VALUE pair into os.environ.  This keeps
# secrets out of source code.
load_dotenv()

from services.ai_service import (
    tailor_resume, parse_resume_text, analyze_job_description,
    tailor_resume_v2, chat_tailor_resume
)
from services.scraper_service import fetch_job_description, ScrapingError
from services.parser_service import extract_text_from_pdf, extract_text_from_docx
import services.session_service as session_service

# ── Step 2: Create the Flask application instance ────────────
# Flask(__name__) uses the location of THIS module to determine:
#   • templates/  → folder for Jinja2 HTML templates
#   • static/     → folder for CSS, JS, images served at /static/
# You can override these with template_folder= and static_folder=
# parameters, but the defaults work perfectly for our layout.
app = Flask(__name__)

# ── Request size cap ──────────────────────────────────────────
# Applies to every route (resume uploads, pasted job descriptions, profile
# saves). Without it, request.files["file"].read() / request.get_json() in
# the upload and parse routes below would buffer an arbitrarily large body
# into memory. 20 MB comfortably covers real resume PDFs/DOCX files (a few
# MB at most) with headroom, while still bounding worst-case memory use.
# Flask returns a 413 automatically once this is exceeded.
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB

# ── Session cookie security ──────────────────────────────────
# Flask's session cookies are cryptographically signed with this
# key.  In production, set SECRET_KEY in your environment (or .env).
# If not set, we generate a random key — sessions won't survive
# server restarts, but it's safe for dev/demo usage.
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)

# Cookie hardening: JS can't read the session cookie (XSS safety),
# cross-origin POSTs need an explicit SameSite consent, and the
# cookie only travels over HTTPS in production (skipped in debug to
# keep `python app.py` ergonomic).
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = not bool(int(os.getenv("FLASK_DEBUG", "0")))

# ── Legacy single-user storage migration ─────────────────────
# If a deploy is upgrading from the pre-multi-user BuildR layout
# (storage/profile.json + storage/resumes/ at the project root),
# copy that data into a synthetic user directory so the upgrade is
# non-destructive. Tests can opt out via BUILDR_SKIP_LEGACY_MIGRATION=1.
def _migrate_legacy_storage():
    from services.auth_service import USERS_ROOT
    if os.environ.get("BUILDR_SKIP_LEGACY_MIGRATION"):
        return

    legacy_profile = _PROJECT_ROOT / "storage" / "profile.json"
    legacy_resumes = _PROJECT_ROOT / "storage" / "resumes"
    legacy_sessions = _PROJECT_ROOT / "storage" / "sessions"

    if not (legacy_profile.exists() or legacy_resumes.exists() or legacy_sessions.exists()):
        return  # nothing to migrate

    # If anyone already lives under storage/users/, assume the new
    # layout is in use and don't touch the legacy files. Mixed
    # layouts are messy and the user should clean up manually.
    if USERS_ROOT.exists() and any(USERS_ROOT.iterdir()):
        print(
            "Legacy single-user storage detected at storage/profile.json "
            "or storage/resumes/, but storage/users/ already has user "
            "directories. Skipping migration to avoid mixing layouts. "
            "Move old files into storage/users/<some-id>/ manually if "
            "you want to recover them."
        )
        return

    # Migration under the new auth model is impossible: legacy files
    # were keyed by the old email-derived user_id, and the new model
    # keys by Google `sub`. We move the legacy files under a marker
    # so they're not re-detected next start, and log a clear notice
    # that any pre-existing user data is unrecoverable through the
    # new auth model unless the user manually re-keys it.
    legacy_marker = _PROJECT_ROOT / "storage" / ".legacy-migrated"
    legacy_marker.mkdir(exist_ok=True)
    for src in (legacy_profile, legacy_resumes, legacy_sessions):
        if src.exists():
            shutil.move(str(src), str(legacy_marker / src.name))
    print(
        "Legacy single-user storage detected and relocated to "
        f"{legacy_marker}. NOTE: under the new Google OAuth auth model, "
        "legacy data is no longer accessible through any user account. "
        "Sign in with Google to start a fresh workspace. "
        "The legacy files are preserved on disk for manual recovery if "
        "you need them."
    )


_PROJECT_ROOT = _Path(__file__).resolve().parent
_migrate_legacy_storage()

# ── Startup cleanup ──────────────────────────────────────────
# Sweep out abandoned tailoring sessions older than a week for
# every user.  These are scratch workspace state, not permanent
# records — finished resumes live independently in the Resume Library.
# This runs at module-import time so it executes under both
# `python app.py` and `gunicorn app:app`.  The operation is idempotent
# (rmtree with ignore_errors=True), so concurrent Gunicorn workers
# importing the module simultaneously is harmless.
_removed = cleanup_expired_sessions_for_all_users()
if _removed:
    print(f"Cleaned up {_removed} expired tailoring session(s).")


@app.errorhandler(413)
def handle_request_too_large(e):
    return jsonify({
        "error": "The uploaded file or request body is too large (limit: 20 MB)."
    }), 413


# ── Auth endpoints (no @require_auth — these establish or tear
#    down the session itself) ──────────────────────────────────
@app.route("/api/auth/google", methods=["POST"])
def api_sign_in_google():
    """
    Verify a Google-issued ID token and establish a session.

    SECURITY MODEL — DO NOT WEAKEN
    ------------------------------
    The only path to a session is a Google-signed ID token whose
    signature passes `verify_google_id_token`. We deliberately do NOT
    accept an email field on the request body — the previous
    email-only design was vulnerable to "I know your email, give me
    your data" attacks. Anyone can submit any email to this server,
    but they cannot forge a Google-issued token whose `sub` claim
    belongs to a Google account they don't control.

    Body: {"id_token": "<JWT from Google Identity Services>"}
    Returns: {"status": "ok", "user_id": "...", "email": "..."}

    Status codes
    ------------
    200 — token verified, session established
    400 — body missing or no id_token
    401 — token invalid / expired / wrong audience / unverified email
    500 — GOOGLE_CLIENT_ID not configured (operator error)
    """
    data = request.get_json(silent=True) or {}
    token = data.get("id_token")
    if not token:
        return jsonify({"error": "An id_token is required."}), 400
    if not isinstance(token, str):
        return jsonify({"error": "id_token must be a string."}), 400

    try:
        user_id, email = sign_in_with_google(token)
    except ValueError as exc:
        # Verification failed: bad signature, expired, wrong audience,
        # unverified email, etc. We return 401 (not 400) because the
        # request itself was well-formed — the credentials inside it
        # were not acceptable. The error message is intentionally
        # generic; specific failure details go to the server log only,
        # not the response, so attackers can't probe for which field
        # of their token was wrong.
        logger.info("Google sign-in rejected: %s", exc)
        return jsonify({"error": "Authentication failed."}), 401
    except RuntimeError as exc:
        # GOOGLE_CLIENT_ID not configured. This is an operator
        # problem, not a user problem — log loudly.
        logger.error("Google OAuth misconfigured: %s", exc)
        return jsonify({"error": "Server is not configured for Google sign-in."}), 500

    return jsonify({"status": "ok", "user_id": user_id, "email": email})


@app.route("/api/auth/sign-out", methods=["POST"])
def api_sign_out():
    """
    Tear down the current session. Idempotent.
    """
    auth_sign_out()
    return jsonify({"status": "ok"})


@app.route("/api/auth/whoami", methods=["GET"])
def api_whoami():
    """
    Tell the SPA who (if anyone) is signed in. Used on page load to
    decide whether to render the auth overlay or the app shell.
    """
    uid = current_user_id()
    if not uid:
        return jsonify({"authenticated": False})
    return jsonify({
        "authenticated": True,
        "user_id": uid,
        "email": current_user_email(),
    })


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


@app.route("/favicon.ico")
def favicon():
    """
    Serve the favicon at the conventional root path. Browsers probe
    GET /favicon.ico directly (independent of the <link rel="icon"> tags
    in index.html's <head>) for things like tab icons before the page
    has loaded or when bookmarking, so this needs to exist at the true
    site root, not just under /static/.
    """
    return app.send_static_file("favicon.ico")


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
@require_auth
def api_get_profile():
    """
    Return the current user's profile as JSON.

    HOW IT WORKS
    ------------
    1. current_user_id() reads the signed session cookie.
    2. load_profile(user_id) reads that user's profile.json
       (or returns defaults if first run).
    3. profile.model_dump() converts the Pydantic model to a plain dict.

    The browser receives something like:
        {
            "personal_info": {"name": "Alice", "email": "a@b.com", ...},
            "education": [...],
            ...
        }
    """
    try:
        user_id = current_user_id()
        profile = load_profile(user_id)
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
@require_auth
def api_put_profile():
    """
    Accept a JSON profile, validate it, and save it to the current
    user's profile.json.

    HOW IT WORKS
    ------------
    1. request.get_json() parses the raw JSON body into a Python dict.
       Flask does this automatically when Content-Type is application/json.

    2. Profile.model_validate(data) runs the full Pydantic validation.
       If anything fails, Pydantic raises a ValidationError.

    3. On success: save_profile(user_id, profile) writes the validated
       profile to storage/users/<user_id>/profile.json, and we return
       {"status": "ok"}.

    4. On ValidationError: we extract the structured error list from
       Pydantic and return it as a 422 response.
    """
    data = request.get_json()

    # ── Guard: no JSON body at all ────────────────────────────
    if data is None:
        return jsonify({"error": "Request body must be JSON"}), 400

    try:
        # ── Validate through Pydantic ─────────────────────────
        profile = Profile.model_validate(data)

        # ── Persist to disk under this user's directory ───────
        user_id = current_user_id()
        save_profile(user_id, profile)

        return jsonify({"status": "ok"})

    except ValidationError as e:
        # ── Extract structured errors ─────────────────────────
        errors = []
        for err in e.errors():
            errors.append({
                "loc": list(err["loc"]),
                "msg": err["msg"],
                "type": err["type"],
            })

        return jsonify({"errors": errors}), 422


@app.route("/api/profile/parse", methods=["POST"])
@require_auth
def api_parse_resume():
    """
    Accept a PDF or DOCX file, extract text, call MiniMax to parse it
    into a Profile model, and return the parsed JSON to the caller.
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

        # Parse text via MiniMax into Profile object
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


@contextmanager
def _isolated_compile(tex_string: str):
    """
    Compile tex_string inside a private temporary directory.

    WHY THIS EXISTS
    ----------------
    compile_pdf() always writes fixed filenames (master_resume.tex /
    master_resume.pdf) into whatever directory it's given. If two
    requests both compiled straight into the shared resume library
    directory at the same time, one request's in-flight .tex/.pdf could
    be clobbered by the other before save_resume() gets a chance to copy
    it out. Compiling into a private per-request temp directory
    (auto-cleaned on exit) eliminates that race entirely — the shared
    resume library directory is only ever touched by save_resume(),
    which writes into a unique, freshly-made resume_id folder.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        yield compile_pdf(tex_string, _Path(tmpdir))


@app.route("/api/resume/master", methods=["POST"])
@require_auth
def api_generate_master_resume():
    """
    Generate the master resume as a PDF for the signed-in user.

    PIPELINE
    --------
    1. load_profile(user_id)        → Profile from this user's profile.json
    2. render_latex()              → .tex string (escaped + templated)
    3. compile_pdf()               → .pdf file (via Tectonic subprocess)
    4. delete_resumes_by_type +    → user-scoped — replaces any prior
       save_resume                   "Master" entry in this user's library
    """
    try:
        user_id = current_user_id()

        # ── Step 1: Load the profile ──────────────────────────
        profile = load_profile(user_id)

        # ── Guard: don't generate a resume with no name ───────
        if not profile.personal_info.name.strip():
            return jsonify({
                "error": "Your profile has no name. "
                         "Please fill in at least your name before generating."
            }), 400

        # ── Step 2: Render LaTeX ──────────────────────────────
        tex_string = render_latex(profile)

        # ── Step 3 & 4: Compile + persist to the user's library ─
        with _isolated_compile(tex_string) as pdf_path:
            delete_resumes_by_type(user_id, "master")
            resume_id = save_resume(
                user_id=user_id,
                tex_string=tex_string,
                pdf_path=pdf_path,
                resume_type="master",
                label="Master",
            )

        return jsonify({
            "status": "ok",
            "id": resume_id,
            "label": "Master",
        })

    except PdfCompilationError as e:
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

@app.route("/api/scrape-job", methods=["POST"])
@require_auth
def api_scrape_job():
    """
    Scrape a job posting URL and return the raw extracted text.
    Handles scraping failures gracefully with custom friendly instructions.
    """
    data = request.get_json()
    if not data or "url" not in data:
        return jsonify({"error": "Missing URL parameter."}), 400

    url = data["url"].strip()
    try:
        scraped_text = fetch_job_description(url)
        return jsonify({
            "status": "ok",
            "job_description": scraped_text
        })
    except ScrapingError as e:
        return jsonify({
            "error": "This website doesn't allow automated extraction. Please copy and paste the job description below instead."
        }), 400
    except Exception as e:
        return jsonify({
            "error": "This website doesn't allow automated extraction. Please copy and paste the job description below instead."
        }), 400


@app.route("/api/analyze-job", methods=["POST"])
@require_auth
def api_analyze_job():
    """
    Perform a lightweight analysis of the job description text using
    MiniMax to extract critical skills and keywords.
    """
    data = request.get_json()
    if not data or "job_description" not in data:
        return jsonify({"error": "Missing job description."}), 400

    job_description = data["job_description"].strip()
    if not job_description:
        return jsonify({"error": "Job description is empty."}), 400

    try:
        analysis = analyze_job_description(job_description)
        return jsonify({
            "status": "ok",
            "skills": analysis.skills,
            "keywords": analysis.keywords
        })
    except Exception as e:
        print(f"Error analyzing job description: {str(e)}")
        return jsonify({
            "status": "error",
            "error": str(e),
            "skills": [],
            "keywords": []
        }), 500


@app.route("/api/tailor/start", methods=["POST"])
@require_auth
def api_tailor_start():
    """
    Initializes a tailoring session for the signed-in user, calls MiniMax
    for the initial tailoring, and saves it to the user's session folder.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    job_description = (data.get("job_description") or "").strip()
    job_url = (data.get("job_url") or "").strip()
    preferences = data.get("preferences") or {}
    contact_info = data.get("contact_info") or {}

    if not job_description and job_url:
        try:
            job_description = fetch_job_description(job_url)
        except ScrapingError as e:
            return jsonify({"error": str(e)}), 400

    if not job_description:
        return jsonify({"error": "Job description is required."}), 400

    try:
        user_id = current_user_id()
        profile = load_profile(user_id)
        if not profile.personal_info.name.strip():
            return jsonify({"error": "Your profile has no name. Please create a Master Resume first."}), 400

        # Overwrite contact info with custom overrides from the setup page
        if contact_info.get("name"):
            profile.personal_info.name = contact_info["name"].strip()
        if contact_info.get("email"):
            profile.personal_info.email = contact_info["email"].strip()
        if "phone" in contact_info:
            profile.personal_info.phone = contact_info["phone"].strip() if contact_info["phone"] else None

        # Create tailoring session folder scoped to this user
        job_context = {
            "job_description": job_description,
            "job_url": job_url,
            "preferences": preferences,
            "contact_info": contact_info
        }
        session_id = session_service.create_session(user_id, profile, job_context)

        # Call MiniMax tailoring v2
        tailoring_result = tailor_resume_v2(profile, job_description, preferences)

        # Save to this user's session active draft folder
        session_service.update_draft(
            user_id=user_id,
            session_id=session_id,
            profile=tailoring_result.profile.to_profile(),
            metadata={
                "suggestions": [s.model_dump() for s in tailoring_result.suggestions],
                "keywords_not_included_list": tailoring_result.keywords_not_included_list,
                "stats": tailoring_result.stats.model_dump(),
                "insights": tailoring_result.insights.model_dump()
            }
        )

        # Return state
        return jsonify({
            "status": "ok",
            "session_id": session_id,
            "suggestions": [s.model_dump() for s in tailoring_result.suggestions],
            "keywords_not_included_list": tailoring_result.keywords_not_included_list,
            "stats": tailoring_result.stats.model_dump(),
            "insights": tailoring_result.insights.model_dump()
        })

    except Exception as e:
        print(f"Error starting tailoring workspace: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Failed to tailor resume: {str(e)}"}), 500


@app.route("/api/tailor/chat", methods=["POST"])
@require_auth
def api_tailor_chat():
    """
    Accepts user instructions, calls MiniMax to refine the active draft,
    and updates the active draft files for the signed-in user.
    """
    data = request.get_json()
    if not data or "session_id" not in data or "message" not in data:
        return jsonify({"error": "Missing required parameters."}), 400

    user_id = current_user_id()
    session_id = data["session_id"]
    message = data["message"].strip()

    try:
        master_profile = session_service.load_master_profile(user_id, session_id)
        active_profile, _ = session_service.load_draft(user_id, session_id)
        job_context = session_service.load_job_context(user_id, session_id)
        chat_history = session_service.load_chat_history(user_id, session_id)

        # Call MiniMax chat refinement
        tailoring_result = chat_tailor_resume(
            master_profile=master_profile,
            active_profile=active_profile,
            job_description=job_context["job_description"],
            message=message,
            chat_history=chat_history
        )

        # Save to chat history
        chat_history.append({"role": "user", "content": message})
        chat_history.append({"role": "model", "content": "Updated the resume."})
        session_service.save_chat_history(user_id, session_id, chat_history)

        # Save revised draft profile & metadata
        session_service.update_draft(
            user_id=user_id,
            session_id=session_id,
            profile=tailoring_result.profile.to_profile(),
            metadata={
                "suggestions": [s.model_dump() for s in tailoring_result.suggestions],
                "keywords_not_included_list": tailoring_result.keywords_not_included_list,
                "stats": tailoring_result.stats.model_dump(),
                "insights": tailoring_result.insights.model_dump()
            }
        )

        return jsonify({
            "status": "ok",
            "suggestions": [s.model_dump() for s in tailoring_result.suggestions],
            "keywords_not_included_list": tailoring_result.keywords_not_included_list,
            "stats": tailoring_result.stats.model_dump(),
            "insights": tailoring_result.insights.model_dump()
        })

    except Exception as e:
        print(f"Error refining resume with chat: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Failed to refine resume: {str(e)}"}), 500


@app.route("/api/tailor/snapshot", methods=["POST"])
@require_auth
def api_tailor_snapshot():
    """
    Saves an explicit named snapshot of the current active draft for the
    signed-in user.
    """
    data = request.get_json()
    if not data or "session_id" not in data or "name" not in data:
        return jsonify({"error": "Missing required parameters."}), 400

    user_id = current_user_id()
    session_id = data["session_id"]
    snapshot_name = data["name"].strip()

    try:
        snapshot_id = session_service.save_snapshot(user_id, session_id, snapshot_name)
        snapshots = session_service.list_snapshots(user_id, session_id)
        return jsonify({
            "status": "ok",
            "snapshot_id": snapshot_id,
            "snapshots": snapshots
        })
    except Exception as e:
        return jsonify({"error": f"Failed to save snapshot: {str(e)}"}), 500


@app.route("/api/tailor/restore", methods=["POST"])
@require_auth
def api_tailor_restore():
    """
    Overwrites the signed-in user's active draft with a selected snapshot's data.
    """
    data = request.get_json()
    if not data or "session_id" not in data or "snapshot_id" not in data:
        return jsonify({"error": "Missing required parameters."}), 400

    user_id = current_user_id()
    session_id = data["session_id"]
    snapshot_id = data["snapshot_id"]

    try:
        session_service.restore_snapshot(user_id, session_id, snapshot_id)
        _, metadata = session_service.load_draft(user_id, session_id)
        return jsonify({
            "status": "ok",
            "suggestions": metadata.get("suggestions", []),
            "keywords_not_included_list": metadata.get("keywords_not_included_list", []),
            "stats": metadata.get("stats", {}),
            "insights": metadata.get("insights", {})
        })
    except Exception as e:
        return jsonify({"error": f"Failed to restore snapshot: {str(e)}"}), 500


@app.route("/api/tailor/save", methods=["POST"])
@require_auth
def api_tailor_save():
    """
    Saves the signed-in user's current active draft to their Resume Library.
    """
    data = request.get_json()
    if not data or "session_id" not in data:
        return jsonify({"error": "Missing session_id parameter."}), 400

    user_id = current_user_id()
    session_id = data["session_id"]
    try:
        profile, _ = session_service.load_draft(user_id, session_id)
        job_context = session_service.load_job_context(user_id, session_id)
        job_description = job_context["job_description"]

        # Render LaTeX from draft profile
        tex_string = render_latex(profile)

        # Extract a filesystem safe label
        label = job_description[:60].strip()
        if len(job_description) > 60:
            label += "…"

        # Compile to PDF (isolated per-request dir) and persist
        with _isolated_compile(tex_string) as pdf_path:
            resume_id = save_resume(
                user_id=user_id,
                tex_string=tex_string,
                pdf_path=pdf_path,
                resume_type="tailored",
                label=label,
                job_description=job_description
            )

        return jsonify({
            "status": "ok",
            "id": resume_id,
            "label": label
        })
    except Exception as e:
        print(f"Error saving tailored resume to library: {str(e)}")
        return jsonify({"error": f"Failed to save tailored resume: {str(e)}"}), 500


@app.route("/api/tailor/draft/<session_id>", methods=["GET"])
@require_auth
def api_tailor_get_draft(session_id):
    """
    Return the signed-in user's current active draft profile and AI metadata
    for the workspace editor.
    """
    user_id = current_user_id()
    try:
        profile, metadata = session_service.load_draft(user_id, session_id)
        return jsonify({
            "status": "ok",
            "profile": profile.model_dump(),
            "suggestions": metadata.get("suggestions", []),
            "keywords_not_included_list": metadata.get("keywords_not_included_list", []),
            "stats": metadata.get("stats", {}),
            "insights": metadata.get("insights", {}),
        })
    except FileNotFoundError:
        return jsonify({"error": "Session draft not found."}), 404
    except Exception as e:
        print(f"Error loading draft: {str(e)}")
        return jsonify({"error": f"Failed to load draft: {str(e)}"}), 500


@app.route("/api/tailor/draft/<session_id>", methods=["PUT"])
@require_auth
def api_tailor_update_draft(session_id):
    """
    Accept a manually edited profile JSON from the workspace editor and save
    it as the signed-in user's active draft. Preserves existing AI metadata
    (suggestions, stats, insights) so only the profile content is overwritten.

    This is the bridge that lets AI and user collaborate on the SAME working
    draft: the user's manual edits are persisted here, and when the AI chat
    endpoint runs next, it calls load_draft() and sees those edits.
    """
    user_id = current_user_id()
    data = request.get_json()
    if not data or "profile" not in data:
        return jsonify({"error": "Missing profile data."}), 400

    try:
        # Validate the incoming profile through Pydantic
        profile = Profile.model_validate(data["profile"])

        # Load existing metadata so we don't lose AI suggestions / stats / insights
        try:
            _, existing_metadata = session_service.load_draft(user_id, session_id)
        except Exception:
            existing_metadata = {}

        session_service.update_draft(
            user_id=user_id,
            session_id=session_id,
            profile=profile,
            metadata=existing_metadata,
        )

        return jsonify({"status": "ok"})

    except ValidationError as e:
        errors = [{"loc": list(err["loc"]), "msg": err["msg"]} for err in e.errors()]
        return jsonify({"errors": errors}), 422
    except Exception as e:
        print(f"Error updating draft: {str(e)}")
        return jsonify({"error": f"Failed to update draft: {str(e)}"}), 500


@app.route("/api/tailor/download/<session_id>", methods=["POST"])
@require_auth
def api_tailor_download(session_id):
    """
    Generate and download a PDF from the signed-in user's latest active draft.

    Pipeline:
    1. load_draft() → Profile (includes all manual + AI edits)
    2. render_latex() → .tex string
    3. compile_pdf() → .pdf file
    4. save_resume() → persist to user's library
    5. send_file() → return PDF as attachment download

    The downloaded PDF always reflects the latest state of the editor.
    """
    user_id = current_user_id()
    try:
        profile, _ = session_service.load_draft(user_id, session_id)
        job_context = session_service.load_job_context(user_id, session_id)
        job_description = job_context.get("job_description", "")

        # Render LaTeX
        tex_string = render_latex(profile)

        # Build a label from the job description
        label = job_description[:60].strip()
        if len(job_description) > 60:
            label += "…"

        # Compile (isolated per-request dir) and persist to library
        with _isolated_compile(tex_string) as pdf_path:
            resume_id = save_resume(
                user_id=user_id,
                tex_string=tex_string,
                pdf_path=pdf_path,
                resume_type="tailored",
                label=label,
                job_description=job_description,
            )

        # Get path to the saved PDF in the library to ensure we return the persistent, isolated copy
        saved_pdf_path = get_resume_path(user_id, resume_id, "pdf")

        # Return the PDF for download
        response = send_file(
            saved_pdf_path,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"BuildR_Tailored_{resume_id}.pdf",
        )
        response.headers["Content-Type"] = "application/pdf"
        response.headers["Content-Disposition"] = f"attachment; filename=BuildR_Tailored_{resume_id}.pdf"
        return response

    except PdfCompilationError as e:
        print(f"PDF compilation error (download): {e}")
        return jsonify({"error": str(e), "log": e.log}), 500
    except Exception as e:
        print(f"Error downloading tailored resume: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Failed to download resume: {str(e)}"}), 500


@app.route("/api/tailor/preview/<session_id>/<version_type>/<version_id>", methods=["GET"])
@require_auth
def api_tailor_preview(session_id, version_type, version_id):
    """
    Generates the PDF preview on the fly for the signed-in user's active
    draft or snapshot, meaning we never store intermediate PDF files
    permanently on disk.
    """
    user_id = current_user_id()
    try:
        profile = None
        if version_type == "draft":
            profile, _ = session_service.load_draft(user_id, session_id)
        elif version_type == "snapshot":
            session_dir = session_service.get_session_dir(user_id, session_id)
            snapshot_path = session_dir / "snapshots" / version_id / "profile.json"
            if not snapshot_path.exists():
                return "Snapshot not found", 404
            with open(snapshot_path, "r", encoding="utf-8") as f:
                profile_data = json.load(f)
            profile = Profile.model_validate(profile_data)
        else:
            return "Invalid preview version type", 400

        # Compile LaTeX to PDF on-demand inside a temporary directory
        tex_string = render_latex(profile)

        with _isolated_compile(tex_string) as pdf_filepath:
            # Read the compiled bytes into memory BEFORE the temp directory
            # is cleaned up. send_file(path, ...) would otherwise hand back
            # a Response whose file object is still open when this `with`
            # block exits (temp dir deletion happens as part of unwinding
            # the `return` statement, before the WSGI layer has streamed the
            # body) — on Windows that's a guaranteed PermissionError trying
            # to delete a file that's still open; on other platforms it's a
            # latent race either way. Serving from an in-memory BytesIO has
            # no such lifetime dependency.
            pdf_bytes = pdf_filepath.read_bytes()

        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=False,
        )


    except Exception as e:
        print(f"Error compiling on-the-fly preview: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"Failed to generate PDF preview: {str(e)}", 500


@app.route("/api/resume/tailored", methods=["POST"])
@require_auth
def api_generate_tailored_resume():
    """
    Generate a tailored resume as a PDF for the signed-in user, optimized
    for a specific job.

    EXPECTS
    -------
    JSON body — at least one of:
        { "job_description": "<pasted job posting text>" }
        { "job_url": "https://careers.example.com/posting/12345" }

    If both are provided, job_description (pasted text) takes priority
    because it's the reliable path — the user already has the text.

    If only job_url is given, we attempt to scrape the page.

    PIPELINE
    --------
    1. Parse the job description from the request body (text or URL).
    2. Load the user's saved profile from disk.
    3. Send both to MiniMax → get back a TailoredProfile (JSON).
    4. Convert TailoredProfile → Profile (for rendering).
    5. Render LaTeX → compile PDF → save to user's library.
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
            try:
                job_description = fetch_job_description(job_url)
            except ScrapingError as e:
                return jsonify({"error": str(e)}), 400
        else:
            return jsonify({
                "error": "Please paste a job description or provide a URL."
            }), 400

    try:
        user_id = current_user_id()

        # ── Step 2: Load the user's profile ───────────────────
        profile = load_profile(user_id)

        # ── Guard: don't tailor an empty profile ──────────────
        if not profile.personal_info.name.strip():
            return jsonify({
                "error": "Your profile has no name. "
                         "Please fill in your profile before tailoring."
            }), 400

        # ── Step 3: Call MiniMax for tailoring ─────────────────
        tailored = tailor_resume(profile, job_description)

        # ── Step 4: Convert to Profile for rendering ─────────
        tailored_profile = tailored.to_profile()

        # ── Step 5: Render LaTeX ───────────────────────────────
        tex_string = render_latex(tailored_profile)

        # ── Step 6: Persist to the user's library ─────────────
        label = job_description[:60].strip()
        if len(job_description) > 60:
            label += "…"

        with _isolated_compile(tex_string) as pdf_path:
            resume_id = save_resume(
                user_id=user_id,
                tex_string=tex_string,
                pdf_path=pdf_path,
                resume_type="tailored",
                label=label,
                job_description=job_description,
            )

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
@require_auth
def api_list_resumes():
    """
    List the signed-in user's saved resumes with their metadata.

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
        user_id = current_user_id()
        resumes = list_resumes(user_id)
        return jsonify(resumes)
    except Exception as e:
        return jsonify({"error": f"Failed to list resumes: {str(e)}"}), 500


@app.route("/api/resumes/<resume_id>/pdf", methods=["GET"])
@require_auth
def api_download_resume_pdf(resume_id):
    """
    Download the PDF for one of the signed-in user's saved resumes.

    The resume_id is the folder name (e.g. "20260701-005402_master").
    Path traversal is blocked by resume_library.get_resume_path(), and
    the resolve-and-verify check is anchored on the user's resumes dir
    so a malformed id cannot reach another user's folder.
    """
    try:
        user_id = current_user_id()
        pdf_path = get_resume_path(user_id, resume_id, "pdf")
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
@require_auth
def api_download_resume_tex(resume_id):
    """
    Download the editable .tex source for one of the signed-in user's
    saved resumes. Same safety checks as the PDF route.
    """
    try:
        user_id = current_user_id()
        tex_path = get_resume_path(user_id, resume_id, "tex")
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
@require_auth
def api_delete_resume(resume_id):
    """
    Delete one of the signed-in user's saved resumes and its entire folder.

    SECURITY
    --------
    delete_resume() uses resolve-and-verify to ensure the target path
    is inside the user's resumes dir. Path traversal attempts
    (e.g. "../../etc") are rejected with a 400. The user's id is also
    derived from the signed session cookie — never from URL params —
    so a malicious client cannot supply a different user_id.
    """
    try:
        user_id = current_user_id()
        delete_resume(user_id, resume_id)
        return jsonify({"status": "ok"})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Failed to delete resume: {str(e)}"}), 500


@app.route("/api/resumes/<resume_id>", methods=["PATCH"])
@require_auth
def api_rename_resume(resume_id):
    """
    Rename one of the signed-in user's saved resume's display label (the
    folder/ID is unchanged, so existing download links keep working).
    """
    data = request.get_json()
    if not data or "label" not in data:
        return jsonify({"error": "Missing 'label' parameter."}), 400

    try:
        user_id = current_user_id()
        meta = rename_resume(user_id, resume_id, data["label"])
        return jsonify({"status": "ok", "label": meta["label"]})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Failed to rename resume: {str(e)}"}), 500


@app.route("/api/resumes/<resume_id>/duplicate", methods=["POST"])
@require_auth
def api_duplicate_resume(resume_id):
    """
    Duplicate one of the signed-in user's saved resumes (tex + pdf +
    metadata) into a new library entry.
    """
    data = request.get_json(silent=True) or {}
    try:
        user_id = current_user_id()
        new_id = duplicate_resume(user_id, resume_id, data.get("label"))
        return jsonify({"status": "ok", "id": new_id})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Failed to duplicate resume: {str(e)}"}), 500


# ── Step 4: Run the dev server ───────────────────────────────
if __name__ == "__main__":
    # This block only executes when you run `python app.py` directly.
    # It will NOT run when a production server (gunicorn, etc.) imports the module.

    # debug=True enables Werkzeug's auto-reloader AND its interactive
    # in-browser debugger, which lets anyone who can reach the port
    # execute arbitrary Python via a shell in the traceback page. That's
    # fine on localhost during development but must never be the
    # unconditional default — opt in explicitly with FLASK_DEBUG=1 (e.g.
    # in your .env file) rather than having it hardcoded on.
    debug_mode = os.getenv("FLASK_DEBUG", "0").strip().lower() in ("1", "true", "yes")
    port = int(os.getenv("PORT", "5000"))
    app.run(debug=debug_mode, port=port)
