# ──────────────────────────────────────────────────────────────
# services/latex_service.py — Render a LaTeX resume from profile data
# ──────────────────────────────────────────────────────────────
#
# WHAT THIS FILE DOES
# -------------------
# Two responsibilities:
#   1. ESCAPE user-provided strings so they are safe inside LaTeX.
#   2. RENDER the Jinja2 LaTeX template with the escaped data.
#
# WHY WE NEED A STANDALONE JINJA2 ENVIRONMENT
# ────────────────────────────────────────────
# Flask ships with its own Jinja2 environment, but that one is
# configured for HTML templates:
#   • It uses {{ }} and {% %} delimiters.
#   • It auto-escapes HTML entities (&amp; etc.).
#
# LaTeX templates need DIFFERENT delimiters (because LaTeX uses
# { } everywhere) and NO HTML escaping (we do LaTeX escaping
# instead).  So we create a completely separate Environment.
#
# CUSTOM DELIMITERS — THE FULL STORY
# ───────────────────────────────────
# Consider this LaTeX snippet:
#
#   \textbf{Education}
#
# If Jinja2 uses its default delimiters, it sees the { and }
# and might interpret them as part of a {{ variable }} or
# {% block %} tag, causing a TemplateSyntaxError.
#
# Our solution uses delimiters that START with a backslash and
# an uppercase word — a pattern that never appears in real LaTeX
# commands (which are always lowercase like \textbf, \section):
#
#   \VAR{ name }        →  inserts a variable
#   \BLOCK{ if x }      →  starts a logic block
#   \COMMENT{ note }    →  a template comment (stripped)
#
# This is a well-known convention in the LaTeX+Jinja2 community.
#
# ESCAPE ORDER MATTERS
# ────────────────────
# We MUST escape the backslash (\) FIRST.  If we escaped & → \&
# before escaping \, then the backslash in \& would itself get
# escaped to \\&, which is wrong.  By handling \ first, all
# subsequent replacements produce safe output.
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

from pathlib import Path
from typing import Any

import jinja2

from models.profile import Profile


# ── Path to the LaTeX templates directory ─────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _PROJECT_ROOT / "templates_latex"


# ══════════════════════════════════════════════════════════════
# 1. LATEX ESCAPING
# ══════════════════════════════════════════════════════════════

# The 10 LaTeX special characters and their safe replacements.
# ORDER MATTERS — backslash must be first (see module docstring).
_LATEX_ESCAPE_RULES: list[tuple[str, str]] = [
    ("\\", r"\textbackslash{}"),   # \ → \textbackslash{}
    ("&",  r"\&"),                 # & → \&   (column separator)
    ("%",  r"\%"),                 # % → \%   (comment character)
    ("$",  r"\$"),                 # $ → \$   (math mode toggle)
    ("#",  r"\#"),                 # # → \#   (macro parameter)
    ("_",  r"\_"),                 # _ → \_   (subscript in math)
    ("{",  r"\{"),                 # { → \{   (group open)
    ("}",  r"\}"),                 # } → \}   (group close)
    ("~",  r"\textasciitilde{}"),  # ~ → \textasciitilde{}
    ("^",  r"\textasciicircum{}"), # ^ → \textasciicircum{}
]


def escape_latex(value: str) -> str:
    """
    Escape LaTeX special characters in a string.

    This makes arbitrary user input safe to embed in a .tex file.
    Without escaping, a name like "O'Brien & Co." would cause a
    LaTeX compilation error because & is the column separator.

    Parameters
    ----------
    value : str
        Raw user-provided text.

    Returns
    -------
    str
        Text with all 10 LaTeX specials replaced by safe commands.

    Examples
    --------
    >>> escape_latex("AT&T")
    'AT\\&T'
    >>> escape_latex("50% off")
    '50\\% off'
    >>> escape_latex("C#")
    'C\\#'
    """
    for char, replacement in _LATEX_ESCAPE_RULES:
        value = value.replace(char, replacement)
    return value


def _escape_recursive(obj: Any) -> Any:
    """
    Deep-walk a nested structure (dicts, lists, strings) and
    apply escape_latex() to every string leaf.

    Non-string leaves (int, float, None, bool) are left unchanged.
    This is how we escape ALL user data in one shot — we call this
    on the entire profile dict before passing it to the template.

    Parameters
    ----------
    obj : Any
        A dict, list, string, or primitive from the profile.

    Returns
    -------
    Any
        The same structure with all strings LaTeX-escaped.
    """
    if isinstance(obj, str):
        return escape_latex(obj)
    if isinstance(obj, dict):
        # DON'T escape keys — they are structural field names
        # (personal_info, start_date, etc.) used by Jinja2 for
        # variable lookup.  They never appear in the LaTeX output.
        # Escaping them would turn "personal_info" into
        # "personal\_info", breaking template rendering.
        return {k: _escape_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_escape_recursive(item) for item in obj]
    # int, float, None, bool — pass through untouched
    return obj


# ══════════════════════════════════════════════════════════════
# 2. JINJA2 ENVIRONMENT WITH CUSTOM DELIMITERS
# ══════════════════════════════════════════════════════════════

def _create_latex_env() -> jinja2.Environment:
    """
    Build a Jinja2 Environment configured for LaTeX templates.

    KEY CONFIGURATION
    -----------------
    block_start_string / block_end_string:
        \BLOCK{ ... }  replaces  {% ... %}

    variable_start_string / variable_end_string:
        \VAR{ ... }    replaces  {{ ... }}

    comment_start_string / comment_end_string:
        \COMMENT{ ... }  replaces  {# ... #}

    autoescape=False:
        We do NOT want HTML escaping (&amp; etc.) — we handle
        LaTeX escaping ourselves via escape_latex().

    WHY FileSystemLoader?
    ---------------------
    We point it at our templates_latex/ directory so we can load
    templates by filename:  env.get_template("resume.tex")
    """
    return jinja2.Environment(
        # ── Custom delimiters (the whole point) ───────────────
        block_start_string=r"\BLOCK{",
        block_end_string="}",
        variable_start_string=r"\VAR{",
        variable_end_string="}",
        comment_start_string=r"\COMMENT{",
        comment_end_string="}",

        # ── Template loader ───────────────────────────────────
        loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),

        # ── No HTML escaping — we do LaTeX escaping instead ───
        autoescape=False,

        # ── Useful extras ─────────────────────────────────────
        # keep_trailing_newline: don't strip the final newline
        # (LaTeX files should end with a newline for clean diffs)
        keep_trailing_newline=True,
    )


# ══════════════════════════════════════════════════════════════
# 3. KEYWORD HIGHLIGHTING
# ══════════════════════════════════════════════════════════════

def _apply_highlights(text: str, keywords_csv: str) -> str:
    """
    Bold specific keywords/phrases in an already-escaped LaTeX string.

    Parameters
    ----------
    text : str
        An already LaTeX-escaped string (e.g. a bullet point).
    keywords_csv : str
        Comma-separated list of keywords to highlight.
        Each keyword is escaped with escape_latex() before matching
        so that the replacement aligns with the escaped text.

    Returns
    -------
    str
        The text with matching phrases wrapped in \\textbf{…}.
    """
    if not keywords_csv or not keywords_csv.strip():
        return text
    for raw_kw in keywords_csv.split(","):
        raw_kw = raw_kw.strip()
        if not raw_kw:
            continue
        # Escape the keyword the same way the text was escaped
        escaped_kw = escape_latex(raw_kw)
        if escaped_kw in text:
            text = text.replace(escaped_kw, r"\textbf{" + escaped_kw + "}")
    return text


def _highlight_entries(escaped_data: dict) -> dict:
    """
    Walk through experience, projects, and achievements in the
    already-escaped profile data and apply keyword highlighting
    to bullet points and achievement titles.

    This must be called AFTER _escape_recursive() so that both
    the text and the keywords have been through the same escaping.
    The \\textbf{} commands we inject are raw LaTeX — they will
    NOT be escaped because we add them after the escaping pass.
    """
    # Experience bullets
    for exp in escaped_data.get("experience", []):
        kw_csv = exp.get("highlight_keywords", "")
        exp["bullets"] = [_apply_highlights(b, kw_csv) for b in exp.get("bullets", [])]

    # Project bullets
    for proj in escaped_data.get("projects", []):
        kw_csv = proj.get("highlight_keywords", "")
        proj["bullets"] = [_apply_highlights(b, kw_csv) for b in proj.get("bullets", [])]

    # Achievement titles
    for ach in escaped_data.get("achievements", []):
        kw_csv = ach.get("highlight_keywords", "")
        ach["title"] = _apply_highlights(ach.get("title", ""), kw_csv)

    return escaped_data


# ══════════════════════════════════════════════════════════════
# 4. RENDER THE FINAL .tex STRING
# ══════════════════════════════════════════════════════════════

def render_latex(profile: Profile) -> str:
    """
    Render a complete .tex string from a Profile object.

    PIPELINE
    --------
    1. Convert the Profile to a plain dict via .model_dump().
    2. Deep-escape every string in that dict (escape_latex on all leaves).
    3. Apply keyword highlighting (bold matching phrases).
    4. Load the resume.tex Jinja2 template.
    5. Render with the escaped data → final .tex source.

    Parameters
    ----------
    profile : Profile
        The validated user profile (from storage or API).

    Returns
    -------
    str
        A complete LaTeX document ready to be compiled by Tectonic.
    """
    # ── Step 1: Profile → dict ────────────────────────────────
    data = profile.model_dump()

    # ── Step 2: Escape all strings ────────────────────────────
    # We do this BEFORE rendering so the template never sees raw
    # user input.  The template's \VAR{ name } will receive the
    # already-safe "AT\&T" instead of the dangerous "AT&T".
    escaped_data = _escape_recursive(data)

    # ── Step 3: Apply keyword highlighting ────────────────────
    # This wraps user-specified keywords in \textbf{} AFTER
    # escaping, so the LaTeX commands are not themselves escaped.
    escaped_data = _highlight_entries(escaped_data)

    # ── Step 4–5: Load template and render ────────────────────
    env = _create_latex_env()
    template = env.get_template("resume.tex")
    tex_string = template.render(**escaped_data)

    return tex_string

