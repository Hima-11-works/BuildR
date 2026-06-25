# ──────────────────────────────────────────────────────────────
# models/profile.py — Pydantic models for the master resume profile
# ──────────────────────────────────────────────────────────────
#
# WHAT THIS FILE DOES
# -------------------
# Defines the *shape* of every piece of data our app stores about
# the user.  Think of each model class as a strict contract:
#
#   "A PersonalInfo object MUST have a name and email,
#    MAY have a phone, and MAY have a dict of links."
#
# Pydantic enforces that contract automatically — if someone tries
# to create a PersonalInfo without a name, Pydantic raises a
# ValidationError *before* bad data ever reaches our database,
# our JSON file, or (worst case) a finished PDF.
#
# WHY PYDANTIC? (vs plain dicts / dataclasses)
# ---------------------------------------------
# 1. **Type coercion** — pass "3.9" for GPA and Pydantic converts
#    it to the float 3.9 automatically.
# 2. **Validation** — pass "not-an-email" for email and it rejects
#    it with a clear error message.
# 3. **Serialization** — .model_dump() gives us a clean dict,
#    .model_dump_json() gives a JSON string. No manual conversion.
# 4. **IDE support** — Your editor auto-completes field names and
#    catches typos instantly because these are real Python classes.
#
# REQUIRED vs OPTIONAL — THE MENTAL MODEL
# ----------------------------------------
# • A *required* field (e.g., `name: str`) means "this data MUST
#   exist for the object to make sense."  A resume with no name
#   is useless, so name is required.
#
# • An *Optional* field (e.g., `phone: Optional[str] = None`)
#   means "this data is nice to have but not essential."  Not
#   everyone lists a phone number, so we default it to None.
#
# • Fields with a *default_factory* (e.g., `list[str] = Field(
#   default_factory=list)`) start as empty containers.  This is
#   safer than `= []` because Python would share one list across
#   all instances (the "mutable default argument" gotcha).
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator


# ─── PersonalInfo ────────────────────────────────────────────
# The "header" of any resume.  Name and email are non-negotiable;
# everything else is optional because different people include
# different contact details.
#
# SCHEMA CHOICE: `links` is a Dict[str, str] instead of separate
# github / linkedin / portfolio fields.  Why?
#   • Extensible — users can add "twitter", "kaggle", "dribbble"
#     without us touching the schema.
#   • Simpler UI — we can render a dynamic list of link inputs
#     rather than hard-coding a form field for every platform.
#   • The *key* is the label ("GitHub"), the *value* is the URL.
# ──────────────────────────────────────────────────────────────

class PersonalInfo(BaseModel):
    """Contact details that appear in the resume header."""

    name: str = Field(
        ...,  # ... means REQUIRED — Pydantic will reject if missing
        # NOTE: No min_length here!  A brand-new profile starts with
        # name="" (empty string).  We enforce "name must be non-empty"
        # later, at resume-generation time — not at the storage layer.
        description="Full name as it should appear on the resume.",
    )
    email: str = Field(
        ...,
        description="Primary email address.",
    )
    phone: Optional[str] = Field(
        default=None,
        description="Phone number (any format). Optional.",
    )
    links: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Labeled links, e.g. {'GitHub': 'https://github.com/user', "
            "'LinkedIn': 'https://linkedin.com/in/user'}."
        ),
    )

    # ── Why no EmailStr? ──────────────────────────────────────
    # Pydantic has a built-in EmailStr type that validates email
    # format, but it requires the `email-validator` package.
    # We keep it as `str` for now to avoid an extra dependency.
    # If you want strict validation, change the type to EmailStr
    # and run:  pip install email-validator
    # ──────────────────────────────────────────────────────────


# ─── Education ───────────────────────────────────────────────
# SCHEMA CHOICES:
#   • `start_date` / `end_date` are strings, NOT Python date
#     objects.  Resumes use varied formats ("May 2023",
#     "2021 – Present", "Expected Dec 2025") that don't fit a
#     strict date parser.  Storing as str keeps it flexible.
#   • `gpa` is Optional[float] — some students include it,
#     many professionals omit it.
#   • `coursework` is a list of strings so the AI can later
#     pick relevant courses to highlight for a specific job.
# ──────────────────────────────────────────────────────────────

class Education(BaseModel):
    """One educational credential (degree, bootcamp, etc.)."""

    institution: str = Field(
        ..., min_length=1,
        description="Name of university / school / bootcamp.",
    )
    degree: str = Field(
        ..., min_length=1,
        description="Degree or credential earned, e.g. 'B.S. Computer Science'.",
    )
    start_date: str = Field(
        ...,
        description="Start date in any human-readable format.",
    )
    end_date: str = Field(
        ...,
        description="End date or 'Present' / 'Expected …'.",
    )
    gpa: Optional[float] = Field(
        default=None,
        ge=0.0,  # GPA can't be negative
        le=10.0,  # accommodates both 4.0 and 10.0 scales
        description="Grade point average (omit if not relevant).",
    )
    coursework: list[str] = Field(
        default_factory=list,
        description="Relevant courses — the AI can cherry-pick from these.",
    )


# ─── Experience ──────────────────────────────────────────────
# SCHEMA CHOICES:
#   • `bullets` is a list[str], not a single block of text.
#     This is critical: when the AI tailors a resume to a job
#     description, it needs to rewrite / reorder / drop
#     individual bullet points.  A single paragraph would make
#     that much harder.
#   • `technologies` is a separate list so we can auto-build
#     a "Tech Stack" tag line or match against job requirements.
# ──────────────────────────────────────────────────────────────

class Experience(BaseModel):
    """One work-experience entry."""

    company: str = Field(
        ..., min_length=1,
        description="Employer name.",
    )
    role: str = Field(
        ..., min_length=1,
        description="Job title / role.",
    )
    start_date: str = Field(
        ...,
        description="Start date (flexible format).",
    )
    end_date: str = Field(
        ...,
        description="End date or 'Present'.",
    )
    work_mode: Optional[str] = Field(
        default=None,
        description="Work mode: 'Onsite', 'Remote', or 'Hybrid'. Optional.",
    )
    bullets: list[str] = Field(
        default_factory=list,
        description=(
            "Achievement-oriented bullet points. "
            "Keep each one atomic — the AI will mix & match."
        ),
    )
    list_type: Optional[str] = Field(
        default="bullet",
        description="Type of list formatting: 'bullet' or 'numbered'.",
    )
    technologies: list[str] = Field(
        default_factory=list,
        description="Technologies / tools used in this role.",
    )


# ─── Project ─────────────────────────────────────────────────
# SCHEMA CHOICES:
#   • Projects are separate from Experience because students
#     and career-changers often have more projects than jobs.
#   • `link` is Optional — personal projects often have a repo,
#     but school assignments might not.
# ──────────────────────────────────────────────────────────────

class Project(BaseModel):
    """A personal, academic, or open-source project."""

    name: str = Field(
        ..., min_length=1,
        description="Project title.",
    )
    description: str = Field(
        ..., min_length=1,
        description="One-line summary of what the project does.",
    )
    bullets: list[str] = Field(
        default_factory=list,
        description="Key accomplishments / technical highlights.",
    )
    list_type: Optional[str] = Field(
        default="bullet",
        description="Type of list formatting: 'bullet' or 'numbered'.",
    )
    technologies: list[str] = Field(
        default_factory=list,
        description="Languages, frameworks, tools used.",
    )
    link: Optional[str] = Field(
        default=None,
        description="URL to repo, live demo, or write-up.",
    )


# ─── Skills ──────────────────────────────────────────────────
# SCHEMA CHOICE: a Dict[str, list[str]] instead of flat list.
#
# Why categorize?
#   • Resumes almost always group skills:
#       Languages: Python, Java, C++
#       Frameworks: Flask, React, Spring
#       Tools: Git, Docker, AWS
#   • A flat list loses that structure, forcing the AI to
#     re-categorize every time.
#   • Dict lets users define their own categories — no need
#     for us to predict every possible grouping.
# ──────────────────────────────────────────────────────────────

class Skills(BaseModel):
    """Categorized technical and non-technical skills."""

    categories: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Skill groups, e.g. "
            "{'Languages': ['Python', 'Java'], "
            "'Frameworks': ['Flask', 'React']}."
        ),
    )


# ─── Certification ───────────────────────────────────────────
# Kept intentionally simple — name, who issued it, when.
# We can extend later with an optional `credential_url`.
# ──────────────────────────────────────────────────────────────

class Certification(BaseModel):
    """A professional certification or credential."""

    name: str = Field(
        ..., min_length=1,
        description="Certification title, e.g. 'AWS Solutions Architect'.",
    )
    issuer: str = Field(
        ..., min_length=1,
        description="Issuing organization, e.g. 'Amazon Web Services'.",
    )
    date: str = Field(
        ...,
        description="Date earned (flexible format).",
    )
    description: Optional[str] = Field(
        default="",
        description="Optional rich-text details or description of the certification.",
    )





# ─── Profile (top-level document) ────────────────────────────
# This is the *root* model — it contains everything.
#
# WHY ONE BIG MODEL?
#   • It maps directly to a single JSON file on disk.
#   • Any service (AI tailoring, PDF rendering, the web UI)
#     receives a complete Profile and can reach any data via
#     dot notation:  profile.personal_info.name
#   • Pydantic validates the *entire tree* in one shot: if any
#     nested model is invalid, you get all errors at once.
#
# All fields use default_factory so a brand-new user starts
# with a valid (but empty) profile — no crash, no None checks.
# ──────────────────────────────────────────────────────────────

class Profile(BaseModel):
    """
    The master resume profile — a single document that contains
    every piece of information about the user.

    This is what gets saved to / loaded from storage/profile.json.
    """

    personal_info: PersonalInfo = Field(
        default_factory=lambda: PersonalInfo(name="", email=""),
        description="Contact details for the resume header.",
    )
    education: list[Education] = Field(
        default_factory=list,
        description="Educational credentials, most recent first.",
    )
    experience: list[Experience] = Field(
        default_factory=list,
        description="Work experience entries, most recent first.",
    )
    projects: list[Project] = Field(
        default_factory=list,
        description="Personal / academic / open-source projects.",
    )
    skills: Skills = Field(
        default_factory=Skills,
        description="Categorized skills.",
    )
    certifications: list[Certification] = Field(
        default_factory=list,
        description="Professional certifications and credentials.",
    )
    achievements: str = Field(
        default="",
        description="Notable achievements and accomplishments.",
    )

    @field_validator("achievements", mode="before")
    @classmethod
    def convert_achievements(cls, v: Any) -> Any:
        if v is None:
            return ""
        if isinstance(v, list):
            bullets = []
            for item in v:
                if isinstance(item, dict) and "title" in item:
                    bullets.append(item["title"])
                elif isinstance(item, str):
                    bullets.append(item)
            
            li_items = []
            for b in bullets:
                content = b.strip()
                if content.startswith("<p>") and content.endswith("</p>"):
                    content = content[3:-4].strip()
                if content:
                    li_items.append(f"<li>{content}</li>")
            if li_items:
                return f"<ul>{''.join(li_items)}</ul>"
            return ""
        return v
