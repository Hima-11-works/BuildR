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
import bs4
from bs4 import BeautifulSoup

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
# 3. HTML TO LATEX CONVERTER (For Rich Text Fields)
# ══════════════════════════════════════════════════════════════

def html_to_latex(html_str: str) -> str:
    """
    Parse a rich HTML string (e.g. from the TipTap editor) and
    convert its formatting tags (bold, italic, lists, links) into
    LaTeX equivalents. Non-rich text content is fully escaped.
    """
    if not html_str:
        return ""
    
    html_str = html_str.strip()
    if not html_str:
        return ""
        
    soup = BeautifulSoup(html_str, "html.parser")
    result = _node_to_latex(soup)
    return result.strip()


def _node_to_latex(node: bs4.PageElement) -> str:
    """
    Recursively walk BS4 elements and map HTML formatting tags
    to LaTeX commands, while escaping plain text leaf nodes.
    """
    if isinstance(node, bs4.NavigableString):
        # Escaping LaTeX specials on plain text string leaf node
        return escape_latex(str(node))
        
    if isinstance(node, bs4.Tag):
        name = node.name.lower()
        if name in ("strong", "b"):
            return r"\textbf{" + "".join(_node_to_latex(c) for c in node.children) + "}"
        elif name in ("em", "i"):
            return r"\textit{" + "".join(_node_to_latex(c) for c in node.children) + "}"
        elif name == "ul":
            inner = "".join(_node_to_latex(c) for c in node.children).strip()
            if not inner:
                return ""
            return r"\begin{itemize}" + "\n" + inner + "\n" + r"\end{itemize}"
        elif name == "ol":
            inner = "".join(_node_to_latex(c) for c in node.children).strip()
            if not inner:
                return ""
            return r"\begin{enumerate}" + "\n" + inner + "\n" + r"\end{enumerate}"
        elif name == "li":
            inner = "".join(_node_to_latex(c) for c in node.children).strip()
            return r"\item " + inner + "\n"
        elif name == "br":
            return "\n"
        elif name in ("p", "div"):
            inner = "".join(_node_to_latex(c) for c in node.children).strip()
            if not inner:
                return ""
            return inner + "\n"
        elif name == "a":
            href = node.get("href", "")
            inner = "".join(_node_to_latex(c) for c in node.children)
            if href:
                return r"\href{" + escape_latex(href) + r"}{" + inner + r"}"
            return inner
        else:
            # Fallback for generic styling tags: process children
            return "".join(_node_to_latex(c) for c in node.children)
            
    # BeautifulSoup root or other node types
    return "".join(_node_to_latex(c) for c in node.children)


# ══════════════════════════════════════════════════════════════
# 4. RENDER THE FINAL .tex STRING
# ══════════════════════════════════════════════════════════════

def render_latex(profile: Profile) -> str:
    """
    Render a complete .tex string from a Profile object.

    PIPELINE
    --------
    1. Convert the Profile to a plain dict via .model_dump().
    2. Extract rich text fields to prevent double-escaping.
    3. Deep-escape remaining plain text fields recursively.
    4. Translate HTML tags in rich text fields to LaTeX.
    5. Load templates_latex/resume.tex and render with data.
    """
    # ── Step 1: Profile → dict ────────────────────────────────
    data = profile.model_dump()

    # ── Step 2: Extract rich text fields ──────────────────────
    rich_fields = {}
    
    # Experience bullets
    rich_fields["experience_bullets"] = []
    rich_fields["experience_list_type"] = []
    for exp in data.get("experience", []):
        rich_fields["experience_bullets"].append(exp.pop("bullets", []))
        rich_fields["experience_list_type"].append(exp.pop("list_type", "bullet"))

    # Project description & bullets
    rich_fields["projects_desc"] = []
    rich_fields["projects_bullets"] = []
    rich_fields["projects_list_type"] = []
    for proj in data.get("projects", []):
        rich_fields["projects_desc"].append(proj.pop("description", ""))
        rich_fields["projects_bullets"].append(proj.pop("bullets", []))
        rich_fields["projects_list_type"].append(proj.pop("list_type", "bullet"))

    # Achievements
    rich_fields["achievements"] = data.pop("achievements", "")

    # Certification descriptions
    rich_fields["certifications_desc"] = []
    for cert in data.get("certifications", []):
        rich_fields["certifications_desc"].append(cert.pop("description", ""))

    # ── Step 3: Escape plain text fields recursively ──────────
    escaped_data = _escape_recursive(data)

    # ── Step 4: Convert and restore rich text fields ──────────
    # Experience
    for i, exp in enumerate(escaped_data.get("experience", [])):
        bullets = rich_fields["experience_bullets"][i]
        list_type = rich_fields["experience_list_type"][i]
        exp["bullets_type"] = "enumerate" if list_type == "numbered" else "itemize"
        exp["bullets"] = [html_to_latex(b) for b in bullets]

    # Projects
    for i, proj in enumerate(escaped_data.get("projects", [])):
        desc = rich_fields["projects_desc"][i]
        bullets = rich_fields["projects_bullets"][i]
        list_type = rich_fields["projects_list_type"][i]
        proj["bullets_type"] = "enumerate" if list_type == "numbered" else "itemize"
        proj["description"] = html_to_latex(desc)
        proj["bullets"] = [html_to_latex(b) for b in bullets]

    # Achievements
    achievements_html = rich_fields.get("achievements", "").strip()
    soup = BeautifulSoup(achievements_html, "html.parser")
    has_text = bool(soup.get_text().strip())
    if has_text:
        escaped_data["achievements"] = html_to_latex(achievements_html)
    else:
        escaped_data["achievements"] = ""

    # Certifications
    for i, cert in enumerate(escaped_data.get("certifications", [])):
        desc = rich_fields["certifications_desc"][i]
        cert["description"] = html_to_latex(desc)

    # ── Step 5: Load template and render ──────────────────────
    env = _create_latex_env()
    template = env.get_template("resume.tex")
    tex_string = template.render(**escaped_data)

    return tex_string

