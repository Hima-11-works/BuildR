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
# ──────────────────────────────────────────────────────────────

import os
from dotenv import load_dotenv
from flask import Flask, render_template

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
    Home page — for now it just confirms the app is running.

    render_template("index.html") tells Flask:
      1. Look in the templates/ directory (resolved from __name__).
      2. Find index.html.
      3. Run it through the Jinja2 engine (so {{ }} expressions work).
      4. Return the rendered HTML string as an HTTP response.
    """
    return render_template("index.html")


# ── Step 4: Run the dev server ───────────────────────────────
if __name__ == "__main__":
    # This block only executes when you run `python app.py` directly.
    # It will NOT run when a production server (gunicorn, etc.) imports the module.
    app.run(debug=True, port=5000)
