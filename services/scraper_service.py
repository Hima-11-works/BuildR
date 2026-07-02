# ──────────────────────────────────────────────────────────────
# services/scraper_service.py — Fetch & extract job descriptions
# ──────────────────────────────────────────────────────────────
#
# WHAT THIS FILE DOES
# -------------------
# One public function: fetch_job_description(url).
# It downloads a web page, strips navigation/script/style noise,
# and returns the cleaned plain text — ready to feed into the
# AI tailoring pipeline.
#
# WHY SCRAPING IS UNRELIABLE
# ──────────────────────────
# Many major job boards (LinkedIn, Indeed, Glassdoor) actively
# block automated requests through:
#
#   1. BOT DETECTION — CAPTCHAs, JavaScript challenges, and
#      TLS fingerprinting reject non-browser traffic before the
#      page even loads.
#
#   2. LOGIN WALLS — The job description is hidden behind a
#      sign-in prompt.  Our requests.get() has no session or
#      cookies, so we see the login page, not the job.
#
#   3. CLIENT-SIDE RENDERING — Single-Page Applications load
#      the content via JavaScript after the initial HTML shell.
#      requests.get() only sees the shell (often just a <div
#      id="root"></div>), not the rendered content.  Handling
#      this would require a headless browser (Puppeteer,
#      Playwright) — far too heavy for a lightweight feature.
#
#   4. RATE LIMITING — Even if the first request succeeds,
#      repeated requests from the same IP get throttled or
#      blocked.
#
# DEFENSIVE DESIGN
# ────────────────
# Because of all the above, this scraper is a *convenience*
# shortcut for simple sites (company career pages, smaller
# job boards), not a reliable pipeline component.  The UI
# always keeps the manual-paste textarea as the guaranteed
# fallback.  Every failure path returns a clear, actionable
# error message telling the user to paste the text instead.
#
# HOW BEAUTIFULSOUP EXTRACTS TEXT
# ───────────────────────────────
# 1. PARSE — BeautifulSoup(html, "html.parser") tokenizes the
#    raw HTML string and builds an in-memory tree of Tag, String,
#    and Comment objects mirroring the DOM hierarchy.  We use
#    Python's built-in html.parser (no C extensions needed).
#
# 2. REMOVE NOISE — We call .decompose() on tags that carry
#    no useful job-description content: <script>, <style>,
#    <nav>, <footer>, <header>, <aside>, <iframe>, <noscript>.
#    .decompose() removes the tag AND all its children from the
#    tree entirely — it's gone, not just hidden.
#
# 3. EXTRACT TEXT — .get_text(separator="\n") walks the
#    remaining tree depth-first, visiting every text node and
#    concatenating them.  The separator="\n" argument inserts a
#    newline between sibling elements so paragraphs don't run
#    together into an unreadable wall of text.
#
# 4. CLEAN — We collapse runs of whitespace/blank lines and
#    strip leading/trailing whitespace for a tidy result.
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


# ── Custom exception ─────────────────────────────────────────
# A dedicated exception lets the caller (app.py) distinguish
# scraping failures from other errors and return a targeted
# user-facing message with a "paste manually" hint.
# ──────────────────────────────────────────────────────────────

class ScrapingError(Exception):
    """
    Raised when a job posting URL cannot be fetched or yields
    too little text to be useful.

    The message is safe to show directly to the user — it never
    contains raw HTML, stack traces, or internal details.
    """
    pass


# ── Tags to strip before extracting text ─────────────────────
# These elements carry navigation chrome, tracking scripts,
# legal footers, and other boilerplate — not job description
# content.  Removing them before .get_text() dramatically
# improves signal-to-noise ratio.
# ──────────────────────────────────────────────────────────────

_NOISE_TAGS = [
    "script",    # JavaScript — tracking, analytics, app bundles
    "style",     # CSS rules — no visible text
    "nav",       # Navigation menus
    "footer",    # Site footer (links, legal, copyright)
    "header",    # Site header (logo, nav, search bar)
    "aside",     # Sidebars (ads, related jobs, filters)
    "iframe",    # Embedded content (ads, widgets)
    "noscript",  # Fallback content for JS-disabled browsers
    "form",      # Application forms, search boxes
    "svg",       # Icons and graphics (no readable text)
]

# Minimum character count for extracted text to be considered
# useful.  Anything shorter is likely a bot-blocked page, a
# login wall, or a JS-rendered shell with no real content.
_MIN_TEXT_LENGTH = 50

# Request timeout in seconds.  Long enough for slow servers,
# short enough to not hang the UI.
_REQUEST_TIMEOUT = 15

# A realistic User-Agent header.  Some sites return different
# content (or block entirely) based on this string.  We mimic
# a recent Chrome browser on Windows.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def fetch_job_description(url: str) -> str:
    """
    Fetch a job posting URL and extract the main text content.

    Parameters
    ----------
    url : str
        The full URL of the job posting page.

    Returns
    -------
    str
        The cleaned plain-text content of the page, suitable
        for feeding into the AI tailoring pipeline.

    Raises
    ------
    ScrapingError
        If the URL is invalid, the page can't be fetched, or
        the extracted text is too short to be a real job posting.
        The error message is user-friendly and always suggests
        pasting the text manually.
    """

    # ── Step 1: Validate the URL ─────────────────────────────
    # Reject obviously invalid inputs before making any network
    # request.  This catches typos, file:// paths, and other
    # non-HTTP schemes.
    url = (url or "").strip()
    if not url:
        raise ScrapingError("No URL provided.")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ScrapingError(
            "Invalid URL — only http:// and https:// links are supported. "
            "Please paste the job description text manually."
        )
    if not parsed.netloc:
        raise ScrapingError(
            "That doesn't look like a valid URL. "
            "Please paste the job description text manually."
        )

    # ── Step 2: Fetch the page ───────────────────────────────
    # We use a realistic User-Agent and a reasonable timeout.
    # Any network or HTTP error is caught and wrapped in a
    # ScrapingError with a user-friendly message.
    try:
        response = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT,
            # Follow redirects (default), but cap at 5 to avoid
            # infinite redirect loops on broken sites.
            allow_redirects=True,
        )
        response.raise_for_status()

    except requests.exceptions.Timeout:
        raise ScrapingError(
            "The page took too long to load (timed out after "
            f"{_REQUEST_TIMEOUT}s). Please paste the job description "
            "text manually."
        )
    except requests.exceptions.TooManyRedirects:
        raise ScrapingError(
            "Too many redirects — the site may be blocking automated "
            "access. Please paste the job description text manually."
        )
    except requests.exceptions.ConnectionError:
        raise ScrapingError(
            "Could not connect to that URL. Please check the link "
            "and try again, or paste the job description text manually."
        )
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        if status == 403:
            raise ScrapingError(
                "Access denied (HTTP 403) — this site blocks automated "
                "access. Please paste the job description text manually."
            )
        elif status == 404:
            raise ScrapingError(
                "Page not found (HTTP 404). Please check the URL "
                "or paste the job description text manually."
            )
        else:
            raise ScrapingError(
                f"The server returned an error (HTTP {status}). "
                "Please paste the job description text manually."
            )
    except requests.exceptions.RequestException:
        raise ScrapingError(
            "Failed to fetch the page. Please paste the job "
            "description text manually."
        )

    # ── Step 3: Parse HTML and strip noise ────────────────────
    # BeautifulSoup builds an in-memory DOM tree from the raw
    # HTML.  We then remove elements that carry navigation,
    # scripts, styles, and other boilerplate — leaving only the
    # content-bearing elements.
    soup = BeautifulSoup(response.text, "html.parser")

    # .decompose() removes each matched tag AND all its children
    # from the tree entirely.  This is more thorough than just
    # hiding them — they won't contribute any text to get_text().
    for tag_name in _NOISE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # ── Step 4: Extract and clean text ────────────────────────
    # .get_text(separator="\n") walks the tree depth-first and
    # concatenates all remaining text nodes, inserting a newline
    # between each element so paragraphs stay separate.
    raw_text = soup.get_text(separator="\n")

    # Collapse multiple blank lines into single newlines and
    # strip leading/trailing whitespace from each line.
    lines = [line.strip() for line in raw_text.splitlines()]
    cleaned = "\n".join(line for line in lines if line)

    # ── Step 5: Guard against empty/thin results ──────────────
    # If very little text came through, the page is likely:
    #   • A JS-rendered SPA (content loaded by JavaScript)
    #   • A login/CAPTCHA wall
    #   • A bot-detection block page
    # In all cases, the text won't contain a real job description.
    if len(cleaned) < _MIN_TEXT_LENGTH:
        raise ScrapingError(
            "The page didn't contain enough text — it may require "
            "JavaScript or a login to view. Please paste the job "
            "description text manually."
        )

    return cleaned
