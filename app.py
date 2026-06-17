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
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify
from pydantic import ValidationError

from models.profile import Profile
from services.storage_service import load_profile, save_profile

# ── Step 1: Load environment variables from .env ─────────────
# load_dotenv() reads the .env file in the project root and
# injects each KEY=VALUE pair into os.environ.  This keeps
# secrets out of source code.
load_dotenv()

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
        return jsonify(profile.model_dump())
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


# ── Step 4: Run the dev server ───────────────────────────────
if __name__ == "__main__":
    # This block only executes when you run `python app.py` directly.
    # It will NOT run when a production server (gunicorn, etc.) imports the module.
    app.run(debug=True, port=5000)
