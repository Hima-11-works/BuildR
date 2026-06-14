# ──────────────────────────────────────────────────────────────
# services/__init__.py
# ──────────────────────────────────────────────────────────────
# This package will hold business-logic services:
#   • gemini_service.py  — Talks to the Gemini API
#   • scraper_service.py — Fetches & parses job postings
#   • latex_service.py   — Renders LaTeX and compiles to PDF
#
# Separating services from routes (app.py) follows the
# "thin controller, fat service" pattern: routes only handle
# HTTP; all real work happens in services.
# ──────────────────────────────────────────────────────────────
