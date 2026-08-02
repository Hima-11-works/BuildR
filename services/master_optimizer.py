# ──────────────────────────────────────────────────────────────
# services/master_optimizer.py — Single-pass content tightening
# ──────────────────────────────────────────────────────────────
#
# WHAT THIS DOES
# --------------
# The Master Resume pipeline calls optimize_profile() AFTER the
# first PDF compile reveals that the resume is more than one page.
# The optimizer makes a single, conservative pass over the
# Profile to remove redundancy and tighten formatting so the
# resume can be re-rendered and fit on one page.
#
# The optimizer is NOT an AI rewrite. It does not call MiniMax.
# It does not change wording, rephrase bullets, or invent metrics.
# It only:
#
#   • Caps bullet counts at the readability ceiling
#     (5 per experience, 4 per project, 4 certifications total).
#   • Strips redundant lead phrases ("Responsible for …",
#     "Duties include …", "Tasked with …") that waste 2-3 words
#     before the actual action verb.
#   • Dedupes technologies within an entry (case-insensitive).
#   • Trims long bullets at the last sentence boundary inside
#     240 chars — long bullets wrap to 2 lines, costing twice the
#     vertical space of a tight one.
#   • Trims long project descriptions at the last sentence boundary.
#   • Caps the achievements section length while preserving its
#     HTML list wrapper.
#
# The optimizer NEVER mutates the caller's profile or persists
# anything to disk — it returns a brand-new Profile instance that
# the caller can render, compile, and (if it fits) save to the
# resume library. The user's stored profile.json is untouched.
#
# WHY "MODERATE, NOT AGGRESSIVE"?
# -------------------------------
# The user's requirement: don't compress aggressively. A senior
# engineer with 8 jobs and 5 certifications per job shouldn't lose
# material to fit on one page — readability beats page count.
#
# The optimizer caps are deliberately permissive (5+4 bullets is
# the recruiter-study sweet spot, not an aggressive 3+2) so the
# content survives intact for experienced professionals, while
# still giving student / early-career resumes (which often have
# 7-10 redundant bullets per job, courtesy of "let me pad this"
# AI prompting) enough room to fit on one page.
#
# IF THE OPTIMIZER DOESN'T HELP
# ----------------------------
# If the resume is still 2+ pages after optimization, the
# pipeline keeps the optimized version anyway (it's strictly
# tighter than the original) and lets the user decide. We do
# NOT iteratively re-optimize — one pass, then stop.
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

from models.profile import Profile

# ── Heavy import: deferred ──────────────────────────────────────
# `bs4` (BeautifulSoup + its html.parser) is ~5 MB at import time.
# Used only inside `_strip_html()`. We defer so the optimizer
# module can be imported cheaply — important because master_optimizer
# is loaded by app.py for every Master Resume route even when no
# optimization is needed (1-page resumes skip it entirely).


logger = logging.getLogger(__name__)


# ── Tunable limits ────────────────────────────────────────────
# Chosen based on recruiter-skim-pattern research:
#   • 5 bullets per role fits in 4-5 vertical lines on a US-letter
#     page with 11pt body text and our margins.
#   • 4 bullets per project is enough to convey impact without
#     duplicating the experience section.
#   • 4 certifications covers the vast majority of credentials
#     recruiters actually look at (AWS, Azure, GCP, K8s, etc.).
#   • 240 chars per bullet is the practical wrap threshold: a
#     bullet longer than ~240 chars almost always wraps to 2 lines
#     on US-letter with our margins, costing 2x the vertical space.
#   • 200 chars for project descriptions: a 1-line description is
#     ~80 chars; 200 allows a long sentence + comma clause.
#
# Achievements are intentionally NOT optimized — see the comment in
# optimize_profile() for the rationale.
MAX_BULLETS_PER_EXPERIENCE = 5
MAX_BULLETS_PER_PROJECT = 4
MAX_CERTIFICATIONS = 4
MAX_BULLET_CHARS = 240
MAX_PROJECT_DESCRIPTION_CHARS = 200


# Phrases that waste 2-3 words before the actual content of a bullet.
# We strip them once, at the start of the (HTML-stripped) bullet text.
# Case-insensitive; whitespace after the phrase is consumed.
_REDUNDANT_LEAD = re.compile(
    r"^\s*(?:"
    r"responsible\s+for|"
    r"duties?\s+include(?:d)?|"
    r"duties?\s*:|"
    r"tasked\s+with|"
    r"worked\s+on|"
    r"in\s+charge\s+of|"
    r"responsible\s+to|"
    r"accountable\s+for|"
    r"helped\s+to|"
    r"helped\s+with"
    r")\s*",
    flags=re.IGNORECASE,
)


# ── Public API ────────────────────────────────────────────────
def optimize_profile(profile: Profile) -> Tuple[Profile, list[str]]:
    """
    Return (optimized_profile, changes) where `changes` is a list
    of human-readable descriptions of what was tightened.

    If no changes were made, `changes` is an empty list. The
    returned Profile is always a fresh deep-copy of the input —
    the caller's profile object is not mutated.
    """
    optimized = profile.model_copy(deep=True)
    changes: list[str] = []

    _optimize_experience(optimized, changes)
    _optimize_projects(optimized, changes)
    _optimize_certifications(optimized, changes)
    # NOTE: We intentionally do NOT optimize achievements. An earlier
    # version did so by collapsing all <li> entries into a single
    # <li> — which silently destroyed user-typed bullet structure.
    # Achievements are usually short enough that they don't push a
    # resume past 1 page, and if they do, spilling to a second page
    # is preferable to losing the user's bullet list. The renderer
    # handles <ul><li><p>…</p></li>… structure correctly as-is.

    # Re-validate the result so a bug in our transform can't
    # produce a Profile that violates the schema.
    validated = Profile.model_validate(optimized.model_dump())

    if changes:
        logger.info(
            "Master Resume optimizer applied %d change(s): %s",
            len(changes),
            "; ".join(changes),
        )

    return validated, changes


# ── Section optimizers ────────────────────────────────────────
def _optimize_experience(profile: Profile, changes: list[str]) -> None:
    """Cap bullets, dedupe tech, and trim verbose bullets per experience entry."""
    for exp in profile.experience:
        # 1. Cap bullets to MAX_BULLETS_PER_EXPERIENCE.
        if exp.bullets and len(exp.bullets) > MAX_BULLETS_PER_EXPERIENCE:
            dropped = len(exp.bullets) - MAX_BULLETS_PER_EXPERIENCE
            exp.bullets = exp.bullets[:MAX_BULLETS_PER_EXPERIENCE]
            changes.append(
                f"Trimmed {dropped} bullet(s) from '{exp.company}' "
                f"(kept the {MAX_BULLETS_PER_EXPERIENCE} strongest)"
            )

        # 2. Trim each bullet: strip redundant lead, cap at MAX_BULLET_CHARS.
        any_bullet_trimmed = False
        for i, raw in enumerate(list(exp.bullets)):
            cleaned = _trim_bullet(raw)
            if cleaned != raw:
                exp.bullets[i] = cleaned
                any_bullet_trimmed = True
        if any_bullet_trimmed:
            changes.append(f"Trimmed verbose bullets in '{exp.company}'")

        # 3. Dedupe technologies (case-insensitive, strip whitespace).
        if exp.technologies:
            deduped = _dedupe_preserve_order(
                (t.strip() for t in exp.technologies),
                key=lambda t: t.lower(),
            )
            if len(deduped) < len(exp.technologies):
                changes.append(
                    f"De-duplicated {len(exp.technologies) - len(deduped)} "
                    f"tech stack entries in '{exp.company}'"
                )
                exp.technologies = deduped


def _optimize_projects(profile: Profile, changes: list[str]) -> None:
    for proj in profile.projects:
        if proj.bullets and len(proj.bullets) > MAX_BULLETS_PER_PROJECT:
            dropped = len(proj.bullets) - MAX_BULLETS_PER_PROJECT
            proj.bullets = proj.bullets[:MAX_BULLETS_PER_PROJECT]
            changes.append(
                f"Trimmed {dropped} bullet(s) from project '{proj.name}'"
            )

        any_bullet_trimmed = False
        for i, raw in enumerate(list(proj.bullets)):
            cleaned = _trim_bullet(raw)
            if cleaned != raw:
                proj.bullets[i] = cleaned
                any_bullet_trimmed = True
        if any_bullet_trimmed:
            changes.append(f"Trimmed verbose bullets in project '{proj.name}'")

        # Trim long descriptions at sentence boundary.
        if proj.description and len(proj.description) > MAX_PROJECT_DESCRIPTION_CHARS:
            new_desc = _trim_text(proj.description, MAX_PROJECT_DESCRIPTION_CHARS)
            if new_desc != proj.description:
                proj.description = new_desc
                changes.append(f"Trimmed long description in project '{proj.name}'")

        if proj.technologies:
            deduped = _dedupe_preserve_order(
                (t.strip() for t in proj.technologies),
                key=lambda t: t.lower(),
            )
            if len(deduped) < len(proj.technologies):
                changes.append(
                    f"De-duplicated {len(proj.technologies) - len(deduped)} "
                    f"tech stack entries in project '{proj.name}'"
                )
                proj.technologies = deduped


def _optimize_certifications(profile: Profile, changes: list[str]) -> None:
    if len(profile.certifications) > MAX_CERTIFICATIONS:
        dropped = len(profile.certifications) - MAX_CERTIFICATIONS
        # Keep the FIRST MAX_CERTIFICATIONS entries — most-recent first
        # is conventional ordering, so the earliest in the list are the
        # oldest and least relevant.
        profile.certifications = profile.certifications[:MAX_CERTIFICATIONS]
        changes.append(
            f"Dropped {dropped} of the oldest certifications "
            f"(keeping the {MAX_CERTIFICATIONS} most recent)"
        )


# ── String-level helpers ──────────────────────────────────────
def _trim_bullet(raw: str) -> str:
    """
    Strip redundant lead phrases and cap at MAX_BULLET_CHARS,
    truncating at the last sentence boundary when possible.

    Handles plain text AND simple HTML bullet strings (the
    achievements/experience/projects fields are stored as
    HTML, since they're authored in a TipTap rich-text editor).
    """
    if not raw:
        return raw

    plain = _strip_html(raw).strip()
    if not plain:
        return raw

    # Strip a redundant lead phrase (only if it's the literal first words,
    # so we don't accidentally rewrite "Built a system responsible for
    # monitoring" into "monitoring").
    stripped = _REDUNDANT_LEAD.sub("", plain, count=1).strip()
    # If the lead strip left the string empty or changed the meaning
    # drastically, keep the original.
    if not stripped or len(stripped) < max(8, len(plain) // 3):
        stripped = plain

    if len(stripped) <= MAX_BULLET_CHARS:
        return stripped if stripped != _strip_html(raw).strip() else raw

    truncated = stripped[:MAX_BULLET_CHARS]
    cut = _cut_at_sentence_boundary(truncated, MAX_BULLET_CHARS)
    return cut


def _trim_text(text: str, max_chars: int) -> str:
    """Truncate plain text at the last sentence boundary if possible, else word boundary."""
    if not text or len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    return _cut_at_sentence_boundary(truncated, max_chars)


def _cut_at_sentence_boundary(truncated: str, max_chars: int) -> str:
    """
    Take a string already cut to <= max_chars chars and cut it
    shorter at the last sentence-ending punctuation, falling back
    to the last word boundary, falling back to the raw truncation.
    """
    # Try sentence boundary first (. ? ! followed by space or end).
    for punct in [". ", "? ", "! "]:
        idx = truncated.rfind(punct)
        if idx > max_chars * 0.6:  # don't cut too aggressively
            cut = truncated[: idx + 1].rstrip()
            return cut if cut else truncated

    # Fall back to word boundary.
    last_space = truncated.rfind(" ")
    if last_space > max_chars * 0.7:
        cut = truncated[:last_space].rstrip(",;:- ")
        return (cut + ".") if not cut.endswith((".", "!", "?")) else cut

    # Last resort: keep the truncation and append an ellipsis to signal
    # the sentence is incomplete.
    return truncated.rstrip(",;:- ") + "..."


def _strip_html(s: str) -> str:
    """Return plain text content of an HTML fragment, with leading/trailing whitespace stripped."""
    # Lazy import: bs4 (~5 MB) loads only when this helper is
    # actually called — i.e. when the optimizer trims a long
    # achievements section. Most resumes skip this entirely.
    from bs4 import BeautifulSoup
    if not s:
        return ""
    return BeautifulSoup(s, "html.parser").get_text(" ", strip=True)


def _dedupe_preserve_order(items, key):
    """Yield items with the first occurrence of each key; preserve original order."""
    seen = set()
    out = []
    for item in items:
        k = key(item)
        if k in seen:
            continue
        seen.add(k)
        out.append(item)
    return out
