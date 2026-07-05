# ──────────────────────────────────────────────────────────────
# services/ai_service.py — Gemini-powered resume tailoring
# ──────────────────────────────────────────────────────────────
#
# WHAT THIS FILE DOES
# -------------------
# One public function: tailor_resume(profile, job_description).
# It sends the user's full profile + a job description to Gemini,
# and receives a structured JSON response describing which items
# to include and how to rewrite bullets for maximum relevance.
#
# HOW STRUCTURED OUTPUT WORKS
# ───────────────────────────
# The google-genai SDK lets you pass a Pydantic model class as
# `response_schema`.  Under the hood, three things happen:
#
# 1. SCHEMA EXTRACTION — The SDK converts TailoredProfile into a
#    JSON Schema (field names, types, required/optional, nesting).
#
# 2. CONSTRAINED DECODING — Gemini's decoder is constrained at
#    *token generation time* to only produce tokens that are valid
#    according to the schema.  This isn't "generate then validate"
#    — it's "prevent invalid tokens from ever being sampled."
#    Think of it like an FSM (finite state machine) overlaid on
#    the decoder: after producing `{"company": "`, only string
#    tokens are allowed, never `[` or a number.
#
# 3. PARSING — The SDK parses the JSON response and returns it
#    via `response.parsed` as a fully typed Pydantic object.
#    No manual json.loads() or try/except needed.
#
# WHY JSON, NOT LATEX?
# ────────────────────
# If we asked Gemini to output LaTeX directly:
#   • Any typo (\textbf{ without }) crashes the compiler.
#   • The model needs to know our template's exact structure.
#   • Template changes would require prompt rewrites.
#   • We'd lose type safety — just a raw string to debug.
#
# With JSON structured output:
#   • Constrained decoding guarantees valid JSON.
#   • Pydantic validates the schema before we touch LaTeX.
#   • Our code owns all rendering — the AI just picks content.
#   • The template can evolve independently of the AI prompt.
#
# ANTI-FABRICATION PROMPT DESIGN
# ──────────────────────────────
# LLMs can "hallucinate" — confidently stating things that aren't
# true.  For a resume, this is disastrous: a fabricated company
# name or inflated metric could end a career.  Our defenses:
#
# 1. EXPLICIT RULES — The prompt says "ONLY use information from
#    the provided profile.  Do NOT invent."  Clear, direct.
#
# 2. FULL CONTEXT — We send the ENTIRE profile, not a summary.
#    The model has all the raw material it needs, reducing the
#    temptation to "fill in gaps" creatively.
#
# 3. ANCHORING — By including exact company names, dates, and
#    bullet text, the model has concrete facts to rephrase
#    rather than vague concepts to elaborate on.
#
# 4. STRUCTURAL CONSTRAINTS — The response_schema forces output
#    into typed fields (company: str, bullets: list[str]) rather
#    than free-form prose.  It's harder to fabricate a structured
#    record than to slip something into a paragraph.
#
# 5. DEFENSIVE FRAMING — The prompt says "You may REPHRASE but
#    must NOT fabricate."  This gives the model permission to be
#    creative within bounds, rather than a blanket "be creative"
#    that could encourage invention.
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import os

from google import genai
from google.genai import types

from models.profile import Profile
from models.tailored_profile import TailoredProfile


# ── Initialize the Gemini client ─────────────────────────────
# The client reads GEMINI_API_KEY from the environment.
# load_dotenv() in app.py has already injected .env values into
# os.environ by the time this module is imported.
#
# WHY A MODULE-LEVEL CLIENT?
# Creating the client once at import time means every call to
# tailor_resume() reuses the same HTTP session.  This is both
# faster (no repeated TLS handshakes) and cleaner than passing
# the key around.
# ──────────────────────────────────────────────────────────────

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """
    Create the Gemini client lazily so .env has been loaded before
    GEMINI_API_KEY is read.
    """
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set. Add it to your .env file.")
        _client = genai.Client(api_key=api_key)
    return _client

# ── Model selection ──────────────────────────────────────────
# gemini-2.5-flash supports structured output and gives better
# rephrasing quality than 2.0 Flash while staying fast enough for UI use.
_MODEL = "gemini-2.5-flash"


def _plain(value: str | None) -> str:
    return (value or "").strip()


def _as_key(*parts: str | None) -> tuple[str, ...]:
    return tuple(_plain(part).casefold() for part in parts)


def _validate_tailored_output(profile: Profile, tailored: TailoredProfile) -> None:
    """
    Check that Gemini selected from the real profile rather than inventing
    new records or skills. Rewritten bullets are allowed, but identities,
    links, dates, certifications, and skill names must trace to the source.
    """
    if tailored.personal_info.to_personal_info().model_dump() != profile.personal_info.model_dump():
        raise ValueError("Gemini changed personal information, which is not allowed.")

    original_education = {edu.model_dump_json() for edu in profile.education}
    for edu in tailored.education:
        if edu.model_dump_json() not in original_education:
            raise ValueError(f"Gemini returned an education entry that is not in the profile: {edu.institution}")

    original_certifications = {cert.model_dump_json() for cert in profile.certifications}
    for cert in tailored.certifications:
        if cert.model_dump_json() not in original_certifications:
            raise ValueError(f"Gemini returned a certification that is not in the profile: {cert.name}")

    original_experience = {
        _as_key(exp.company, exp.role, exp.start_date, exp.end_date): exp
        for exp in profile.experience
    }
    for exp in tailored.experience:
        key = _as_key(exp.company, exp.role, exp.start_date, exp.end_date)
        original = original_experience.get(key)
        if original is None:
            raise ValueError(f"Gemini returned an experience that is not in the profile: {exp.company} - {exp.role}")
        original_tech = {_plain(tech).casefold() for tech in original.technologies}
        invented_tech = [tech for tech in exp.technologies if _plain(tech).casefold() not in original_tech]
        if invented_tech:
            raise ValueError(f"Gemini added technologies not present in {exp.company}: {', '.join(invented_tech)}")

    original_projects = {
        _plain(project.name).casefold(): project
        for project in profile.projects
    }
    for project in tailored.projects:
        original = original_projects.get(_plain(project.name).casefold())
        if original is None:
            raise ValueError(f"Gemini returned a project that is not in the profile: {project.name}")
        if _plain(project.link) != _plain(original.link):
            raise ValueError(f"Gemini changed the project link for: {project.name}")
        original_tech = {_plain(tech).casefold() for tech in original.technologies}
        invented_tech = [tech for tech in project.technologies if _plain(tech).casefold() not in original_tech]
        if invented_tech:
            raise ValueError(f"Gemini added technologies not present in {project.name}: {', '.join(invented_tech)}")

    original_skills = {
        category.casefold(): {skill.casefold() for skill in skills}
        for category, skills in profile.skills.categories.items()
    }
    for skill_category in tailored.skills.categories:
        category = skill_category.category
        original_category = original_skills.get(category.casefold())
        if original_category is None:
            raise ValueError(f"Gemini added a skill category not present in the profile: {category}")
        invented_skills = [
            skill for skill in skill_category.skills
            if skill.casefold() not in original_category
        ]
        if invented_skills:
            raise ValueError(f"Gemini added skills not present in {category}: {', '.join(invented_skills)}")


# ── The system prompt ────────────────────────────────────────
# This prompt is the single most important piece of the feature.
# Every sentence is deliberate — see the module docstring above
# for the reasoning behind the anti-fabrication design.
# ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a professional resume tailoring assistant.

You will receive:
1. A candidate's COMPLETE master profile as JSON.
2. A job description they are applying to.

Your task: produce a tailored resume by selecting and rewriting the most
relevant content from the profile to match the job description.

STRICT RULES

1. ONLY use information that exists in the provided profile. Do NOT invent,
   fabricate, or hallucinate any experience, project, skill, company, metric,
   achievement, certification, link, or technology.

2. You may rephrase bullet points to emphasize relevance to the job
   description. The underlying facts, metrics, and achievements must remain
   truthful and traceable to the original profile content.

3. Select the most relevant experiences, projects, skills, and certifications
   for this specific job. You do not need to include everything.

4. Order sections by relevance. Put the most relevant items first.

5. You may omit items that are clearly irrelevant to the job.

6. Pass personal_info through exactly as provided. Do not modify names,
   emails, phone numbers, or links. Return links as a list of objects with
   label and url fields, not as a JSON object/dictionary.

7. Keep education entries factual. Do not modify institution names, degree
   titles, dates, GPAs, or coursework. You may select which education entries
   to include and reorder them.

8. For skills, only include skill categories and individual skills that exist
   in the profile. Return skill categories as a list of objects with category
   and skills fields, not as a JSON object/dictionary.

9. For certifications, pass selected entries through unchanged.

10. For achievements, pass through or trim the original text. Do not add new
    achievements.

QUALITY GUIDELINES

- Rewrite bullets using strong action verbs and quantified impact where the
  original data supports it.
- Mirror keywords and phrases from the job description naturally.
- Aim for a concise, one-page resume: typically 2-3 experiences and 2-3
  projects unless the profile is very senior.
- Each bullet should be one impactful sentence, not a paragraph.\
"""


def tailor_resume(profile: Profile, job_description: str) -> TailoredProfile:
    """
    Send the full profile + job description to Gemini and get back
    a tailored resume as a validated Pydantic object.

    Parameters
    ----------
    profile : Profile
        The user's complete master profile (from storage/profile.json).
    job_description : str
        The raw text of the job posting the user is targeting.

    Returns
    -------
    TailoredProfile
        A structured object describing which items to include, in what
        order, and with rewritten bullet points.

    Raises
    ------
    Exception
        If the Gemini API call fails (network error, invalid key,
        rate limit, etc.).  The caller (app.py) should catch this
        and return a helpful error to the frontend.

    HOW IT WORKS
    ------------
    1. Serialize the profile to a JSON string (pretty-printed for
       the model to read easily).
    2. Construct the user message with both the profile and JD.
    3. Call Gemini with structured output (response_schema).
    4. Return the parsed TailoredProfile object.
    """

    # ── Step 1: Serialize the profile ─────────────────────────
    # model_dump_json() produces a clean JSON string.  We use
    # indent=2 so the model can read it easily (LLMs process
    # formatted JSON better than minified JSON).
    profile_json = profile.model_dump_json(indent=2)

    # ── Step 2: Build the user message ────────────────────────
    user_message = (
        f"══ CANDIDATE PROFILE (JSON) ══\n"
        f"{profile_json}\n\n"
        f"══ JOB DESCRIPTION ══\n"
        f"{job_description}\n\n"
        f"Now produce the tailored resume JSON."
    )

    # ── Step 3: Call Gemini with structured output ────────────
    # The key parameters:
    #   response_mime_type="application/json"
    #     → Tells Gemini to output JSON (not free text).
    #
    #   response_schema=TailoredProfile
    #     → The SDK converts this Pydantic class to a JSON Schema
    #       and sends it to the API.  Gemini's decoder is then
    #       constrained to only produce tokens valid under this
    #       schema.  This is NOT post-hoc validation — it's
    #       built into the sampling process.
    #
    #   response.parsed
    #     → The SDK automatically deserializes the JSON into a
    #       TailoredProfile instance, fully validated by Pydantic.
    response = _get_client().models.generate_content(
        model=_MODEL,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=TailoredProfile,
            temperature=0.3,  # Low temperature for factual accuracy
        ),
    )

    # ── Step 4: Return the parsed result ──────────────────────
    # response.parsed is already a TailoredProfile object.
    # If parsing fails (should be impossible with constrained
    # decoding, but defense in depth), this will raise.
    tailored: TailoredProfile = response.parsed
    _validate_tailored_output(profile, tailored)

    return tailored


def parse_resume_text(text: str) -> Profile:
    """
    Send the raw resume text to Gemini and parse it into a structured Profile Pydantic object.
    """
    system_instruction = (
        "You are an expert AI resume parser. Your job is to extract all information from the provided resume text "
        "and return it structured exactly matching the schema. Extract all contact details (name, email, phone, links), "
        "education, work experience, projects, skills, certifications, and achievements. Be as accurate and complete as possible, "
        "preserving all dates, details, bullets, and tools. Do not invent any information that does not exist in the resume text.\n\n"
        "Crucial formatting rule: Clean up any PDF extraction artifacts like raw ligatures (e.g. replace '\\ufb01' or similar raw characters with 'fi', "
        "replace '\\ufb03' with 'ffi', replace '\\ufb02' with 'fl') and fix word spacing issues (e.g. 'JA V A' -> 'JAVA', 'F rameworks' -> 'Frameworks', "
        "'T echnologies' -> 'Technologies'). Ensure all output strings have proper spelling, casing, and spacing."
    )

    response = _get_client().models.generate_content(
        model=_MODEL,
        contents=f"══ RESUME TEXT ══\n{text}\n\nExtract and structure the resume into the response schema.",
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=TailoredProfile,
            temperature=0.1,  # Low temperature for highest extraction accuracy
        ),
    )

    tailored: TailoredProfile = response.parsed
    return tailored.to_profile()
