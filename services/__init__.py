# ──────────────────────────────────────────────────────────────
# services/__init__.py
# ──────────────────────────────────────────────────────────────
# This package holds business-logic services:
#   • ai_service.py      — Talks to the MiniMax API (OpenAI-compatible)
#   • scraper_service.py — Fetches & parses job postings
#   • latex_service.py   — Renders LaTeX and compiles to PDF
#   • pdf_service.py     — Wraps the Tectonic CLI
#   • parser_service.py  — Extracts text from uploaded PDF/DOCX resumes
#   • storage_service.py — Reads/writes the master profile JSON
#   • session_service.py — Manages per-user tailoring workspaces
#   • resume_library.py  — Catalogs generated resumes
#
# Separating services from routes (app.py) follows the
# "thin controller, fat service" pattern: routes only handle
# HTTP; all real work happens in services.
# ──────────────────────────────────────────────────────────────
