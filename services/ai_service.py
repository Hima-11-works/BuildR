# ──────────────────────────────────────────────────────────────
# services/ai_service.py — MiniMax-powered resume tailoring
# ──────────────────────────────────────────────────────────────
#
# WHAT THIS FILE DOES
# -------------------
# Public helpers:
#   • tailor_resume(profile, job_description)
#   • parse_resume_text(text)
#   • analyze_job_description(job_description)
#   • tailor_resume_v2(profile, job_description, preferences)
#   • chat_tailor_resume(master, active, jd, message, history)
#
# They all send the user's profile + a job description to MiniMax
# via an OpenAI-compatible chat-completions endpoint, and receive
# a structured JSON response describing which items to include
# and how to rewrite bullets for maximum relevance.
#
# HOW STRUCTURED OUTPUT WORKS
# ───────────────────────────
# We use response_format={"type": "json_object"} so the model is
# instructed (and MiniMax-side constrained) to emit only valid
# JSON. The flow is:
#
#   1. The system prompt says "respond with strict JSON in the
#      schema described in the user message", and we hand the
#      model the target Pydantic schema as a JSON-Schema dict in
#      the user message so it knows the exact field shape.
#   2. MiniMax returns a JSON string in the assistant message.
#   3. We parse with `schema.model_validate_json(...)`, which:
#        - decodes JSON,
#        - validates types and required fields via Pydantic,
#        - raises ValidationError on shape mismatches.
#   4. The downstream `_sanitize_tailored_output()` performs the
#      anti-hallucination pass against the master profile.
#
# WHY JSON, NOT LATEX?
# ────────────────────
# If we asked the model to output LaTeX directly:
#   • Any typo (\textbf{ without }) crashes the compiler.
#   • The model needs to know our template's exact structure.
#   • Template changes would require prompt rewrites.
#   • We'd lose type safety — just a raw string to debug.
#
# With JSON structured output:
#   • The model is constrained to valid JSON.
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
# 4. STRUCTURAL CONSTRAINTS — The JSON-only response format
#    forces output into typed fields (company: str, bullets:
#    list[str]) rather than free-form prose.  It's harder to
#    fabricate a structured record than to slip something into
#    a paragraph.
#
# 5. DEFENSIVE FRAMING — The prompt says "You may REPHRASE but
#    must NOT fabricate."  This gives the model permission to be
#    creative within bounds, rather than a blanket "be creative"
#    that could encourage invention.
# ──────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Type, TypeVar

from models.profile import Profile
from models.tailored_profile import TailoredProfile

# ── Heavy import: deferred ──────────────────────────────────────
# The `openai` SDK and its transitive deps (httpx, anyio, h11, httpcore,
# jiter, tqdm, …) add ~30 MB to the process at import time — measured
# by `tracemalloc`. We defer the import to first use inside
# `_get_client()` so routes that don't call the LLM (auth, profile,
# resume download, etc.) don't pay that cost. Function behavior is
# identical: a module-level `OpenAI` symbol is still available via
# lazy load on first call.
if TYPE_CHECKING:  # pragma: no cover — type-checkers only
    from openai import OpenAI  # noqa: F401  (only imported for typing)


logger = logging.getLogger(__name__)


# ── Initialize the MiniMax client ─────────────────────────────
# The client reads MINIMAX_API_KEY from the environment.
# load_dotenv() in app.py has already injected .env values into
# os.environ by the time this module is imported.
#
# The MiniMax API is OpenAI-compatible, so we use the official
# `openai` SDK with a custom base_url. Base URL is itself
# configurable via MINIMAX_BASE_URL — defaults to MiniMax's
# public endpoint.
#
# WHY A MODULE-LEVEL CLIENT?
# Creating the client once at import time means every call to
# tailor_resume() reuses the same HTTP session.  This is both
# faster (no repeated TLS handshakes) and cleaner than passing
# the key around.
# ──────────────────────────────────────────────────────────────

_DEFAULT_BASE_URL = "https://api.minimax.io/v1"

_client: OpenAI | None = None

# ── Request timeout ──────────────────────────────────────────
# Without an explicit timeout, a hung MiniMax call blocks the
# Flask request thread indefinitely — on the single-threaded dev
# server that stalls the whole app. 90s comfortably covers normal
# tailoring calls (which send a full profile + job description)
# while still failing fast if the API is unreachable. Configurable
# via MINIMAX_TIMEOUT_MS for slower connections.
_DEFAULT_TIMEOUT_MS = 90_000


def _get_client() -> OpenAI:
    """
    Create the MiniMax (OpenAI-compatible) client lazily so .env
    has been loaded before MINIMAX_API_KEY is read.

    The `openai` SDK is imported HERE on first call, not at module
    load time — see the deferred-import note above the import
    section. This keeps routes that never call the LLM (e.g. the
    auth/profile/resume-library routes) free of the ~30 MB the
    openai SDK + httpx + anyio stack pulls in.
    """
    global _client
    if _client is None:
        # Lazy import: pulls openai + httpx + anyio + h11 + httpcore + jiter
        # only on first LLM call. Measured ~30 MB saved at idle.
        from openai import OpenAI

        api_key = os.getenv("MINIMAX_API_KEY")
        if not api_key:
            raise RuntimeError(
                "MINIMAX_API_KEY is not set. Add it to your .env file."
            )
        base_url = os.getenv("MINIMAX_BASE_URL", _DEFAULT_BASE_URL)
        timeout_ms = int(os.getenv("MINIMAX_TIMEOUT_MS", _DEFAULT_TIMEOUT_MS))
        _client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_ms / 1000.0,
        )
    return _client

# ── Model selection ──────────────────────────────────────────
# minimax-m3 is MiniMax's current production-tier model and gives
# strong rephrasing quality for tailoring. Override at deploy
# time via the MINIMAX_MODEL env var (e.g. to fall back to an
# older generation without code changes).
_MODEL = os.getenv("MINIMAX_MODEL", "minimax-m3")


# ── Typed chat helper ────────────────────────────────────────
_SchemaT = TypeVar("_SchemaT")


# Output-format rule appended to every system prompt. MiniMax's
# minimax-m3 is a reasoning model and will sometimes emit a
#  think ... think  chain-of-thought block (and/or wrap the JSON
# in markdown code fences) even when response_format asks for
# pure JSON. This hard, repeated instruction reduces — but does
# not eliminate — that behavior. The regexes in
# `_strip_response_wrappers` provide defense in depth.
_OUTPUT_FORMAT_INSTRUCTION = (
    "\n\nOUTPUT FORMAT (STRICT): Your entire reply must be a single "
    "valid JSON object that matches the schema provided in the user "
    "message. Do NOT include any of the following anywhere in your "
    "response:\n"
    "  •  think ... think  reasoning blocks (or any other chain-of-thought)\n"
    "  • Markdown code fences such as ``` json or ```\n"
    "  • Prose, commentary, or explanation before or after the JSON\n"
    "  • Any text outside of the JSON object itself\n"
    "Reply with only the JSON, starting with `{` and ending with `}`."
)


# Compiled patterns for stripping common wrappers. Order matters:
# we try  think  first, then code fences, then a first-{ to last-}
# slice. Each step is idempotent.
_THINK_RE = re.compile(r"<!--.*?-->|```.*?```|\[.*?\]", flags=re.DOTALL)
# Reasoning models emit `` blocks (lowercase). Some variants use
# <thinking>...</thinking> or <reasoning>...</reasoning>.
_THINK_BLOCK_RE = re.compile(
    r"<\s*(?:think|thinking|reasoning|reflection|scratchpad)\s*>.*?<\s*/\s*(?:think|thinking|reasoning|reflection|scratchpad)\s*>",
    flags=re.DOTALL | re.IGNORECASE,
)
_CODE_FENCE_RE = re.compile(
    r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?\s*```\s*$",
    flags=re.DOTALL,
)


def _strip_response_wrappers(content: str) -> str:
    """
    Extract the JSON payload from a model response that may have
    been wrapped in a reasoning block (``) or
    markdown code fences (```` ``` json ... ``` ````).

    Strategy (applied in order):
      1. Strip any `` (or similar) blocks.
      2. Strip ``` ... ``` code fences.
      3. If the result still doesn't start with `{`, slice from
         the first `{` to the last `}`.
      4. Trim whitespace.

    Returns the cleaned string. If no `{` is present, returns the
    fully-stripped content as-is (the caller will fail to parse it
    and surface the original content for debugging).
    """
    s = content.strip()
    if not s:
        return s
    s = _THINK_BLOCK_RE.sub("", s)
    s = _CODE_FENCE_RE.sub(r"\1", s)
    s = s.strip()
    if s.startswith("{"):
        return s
    first = s.find("{")
    last = s.rfind("}")
    if first != -1 and last > first:
        return s[first : last + 1].strip()
    return s


def _chat_json(
    *,
    system: str,
    user: str,
    schema: Type[_SchemaT],
    temperature: float,
    schema_hint_name: str | None = None,
) -> _SchemaT:
    """
    Call MiniMax with a system + user prompt and parse the
    response into the given Pydantic model.

    The model is instructed via `response_format={"type":
    "json_object"}` (and a strict OUTPUT FORMAT block in the
    system prompt) to emit a single JSON object. We then validate
    that JSON against the supplied `schema` (Pydantic v2's
    model_validate_json). On any shape mismatch a ValidationError
    propagates up so the caller can surface a useful error.

    Parameters
    ----------
    system : str
        System prompt (anti-fabrication rules, output schema).
    user : str
        User payload (profile JSON, job description, etc.).
    schema : Type[BaseModel]
        Pydantic model class describing the expected response
        shape. Used to validate the parsed JSON.
    temperature : float
        Sampling temperature. Low = more deterministic.
    schema_hint_name : str | None
        Optional friendly name for the response schema, only used
        for logging/debugging today.

    Returns
    -------
    An instance of `schema` populated with the model's output.

    Raises
    ------
    pydantic.ValidationError
        If the model returned invalid JSON or JSON that does not
        match the supplied Pydantic schema.
    openai.OpenAIError
        For transport / auth / rate-limit failures.
    """
    client = _get_client()
    logger.debug(
        "MiniMax chat: model=%s schema=%s temperature=%s",
        _MODEL,
        schema_hint_name or schema.__name__,
        temperature,
    )
    response = client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": system + _OUTPUT_FORMAT_INSTRUCTION},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=temperature,
    )
    raw = (response.choices[0].message.content or "")
    if not raw.strip():
        raise RuntimeError("MiniMax returned empty content.")

    content = _strip_response_wrappers(raw)

    # First attempt: parse the cleaned content directly. Most calls
    # land here cleanly.
    try:
        return schema.model_validate_json(content)
    except Exception as first_exc:
        # Defense in depth: if stripping didn't yield parseable
        # JSON, surface the cleaned content in the exception so the
        # caller can log/display it. We re-raise so app.py still
        # treats this as a validation failure.
        if content != raw.strip():
            logger.warning(
                "MiniMax response had to be unwrapped; raw[:200]=%r cleaned[:200]=%r",
                raw[:200],
                content[:200],
            )
        raise first_exc from None


def _plain(value: str | None) -> str:
    return (value or "").strip()


def _as_key(*parts: str | None) -> tuple[str, ...]:
    return tuple(_plain(part).casefold() for part in parts)


def _canon_skill(value: str | None) -> str:
    """
    Canonical form of a skill / technology name used for *fuzzy* comparison.

    Goal
    ----
    Allow harmless wording variations through the anti-hallucination
    validator while still rejecting genuinely invented skills.

    Allowed matches
    - "LLMs"        <-> "LLMsLLMs"      (whole-string repetition pattern)
    - "React.js"    <-> "ReactJS"       (punctuation dropped, single token)
    - "Node.js"     <-> "NodeJS"        (punctuation dropped)
    - "CI/CD"       <-> "CICD"          (punctuation dropped)
    - "PostgreSQL"  -> "postgresql"     (single canonical form, no aliasing)
    - "loooong"     -> "long"           (3+ character-run collapsed)

    The function NEVER invents matches across distinct technologies
    (PostgreSQL vs Postgres, React vs Vue are NOT matched here).

    Strategy
    --------
    1. Lowercase, strip whitespace.
    2. Remove every non-alphanumeric character (punctuation, slashes,
       hyphens, dots, plus, hash, etc.).
    3. Collapse only runs of 3 or more duplicate characters so that
       "loooong" -> "long" but "hello" stays "hello".
    4. Detect the smallest string period: if the whole string is a
       repetition of a shorter substring (e.g. "llmsllms" = "llms"
       repeated twice), return that shorter substring.
    """
    if not value:
        return ""
    import re as _re
    s = value.strip().casefold()
    # Strip every non-alphanumeric char.
    s = _re.sub(r"[^a-z0-9]+", "", s)
    if not s:
        return ""
    # Collapse only runs of 3+ identical chars (handles "loooong" -> "long"
    # without touching legitimate double letters like the "ll" in "hello").
    s = _re.sub(r"(.)\1{2,}", r"\1", s)
    # Whole-string period detection: if `s` is a repetition of a shorter
    # substring, return that shorter substring.
    n = len(s)
    for period in range(1, n // 2 + 1):
        if n % period == 0 and s[:period] * (n // period) == s:
            return s[:period]
    return s


def _skills_equivalent(a: str | None, b: str | None) -> bool:
    """
    Return True if two skill strings are equivalent for the purposes of
    anti-hallucination validation.

    Two skills are considered equivalent if either canonical form equals
    the other. The canonical form is intentionally strict: it collapses
    case, whitespace, punctuation, and obvious repeated-character typos
    but does NOT alias distinct technologies.
    """
    return _canon_skill(a) != "" and _canon_skill(a) == _canon_skill(b)


def _is_canonical_member(canon: str, canon_set: set[str]) -> bool:
    """
    Check whether a canonical-form skill is a member of a set of
    canonical-form skills.

    Membership rule: returns True if `canon` exactly equals any element of
    `canon_set`, OR if `canon` is a contiguous repetition of an element
    (e.g. "llmsllms" vs "llms"), OR if an element is a contiguous
    repetition of `canon`.

    This catches obvious duplicate-token typos like "LLMsLLMs" vs "LLMs"
    without inventing matches across unrelated technologies.
    """
    if not canon or not canon_set:
        return False
    if canon in canon_set:
        return True
    # Check repetition patterns: "llmsllms" is "llms" repeated twice.
    # Detect smallest period of `canon` and check whether repeating it
    # reconstructs the original.
    n = len(canon)
    for period in range(1, n // 2 + 1):
        if n % period == 0 and canon[:period] * (n // period) == canon:
            if canon[:period] in canon_set:
                return True
    # And the other direction: an existing canonical skill might itself be
    # a repetition of `canon`.
    for member in canon_set:
        m = len(member)
        if m % len(canon) == 0 and len(canon) * (m // len(canon)) == member:
            return True
        # Also: check the period of `member` and compare to `canon`.
        for period in range(1, m // 2 + 1):
            if m % period == 0 and member[:period] * (m // period) == member:
                if member[:period] == canon:
                    return True
    return False


def _sanitize_tailored_output(profile: Profile, tailored: "TailoredProfile") -> list[str]:
    """
    Sanitize the AI-generated tailored output **in-place** using the original
    profile as the single source of truth.

    Instead of raising on deviations, this function:
    - Silently removes certifications, experiences, projects, and skills that
      are not present in the original profile.
    - Maps renamed skill category names back to the original names (fuzzy
      match via canonical form) and drops any that cannot be matched.
    - Strips invented technologies from experience and project entries.
    - Restores the original project link if Gemini changed it.
    - Silently resets personal_info to the original if Gemini tampered with it.

    Returns a list of human-readable warning strings describing every
    correction made (useful for server-side logging / debugging).

    Raises
    ------
    ValueError
        Only for truly unrecoverable structural errors (currently none;
        all known AI deviations are recoverable).
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)
    warnings: list[str] = []

    # ── 1. Personal info — silently restore from original ────────
    original_pi = profile.personal_info
    tailored_pi = tailored.personal_info.to_personal_info()
    if tailored_pi.model_dump() != original_pi.model_dump():
        warnings.append("Gemini altered personal_info — restored from original profile.")
        from models.tailored_profile import TailoredLink, TailoredPersonalInfo
        tailored.personal_info = TailoredPersonalInfo(
            name=original_pi.name,
            email=original_pi.email,
            phone=original_pi.phone,
            links=[
                TailoredLink(label=label, url=url)
                for label, url in original_pi.links.items()
            ],
        )

    # ── 2. Education — drop entries not in original ───────────────
    original_education = {edu.model_dump_json() for edu in profile.education}
    before = len(tailored.education)
    tailored.education = [
        edu for edu in tailored.education
        if edu.model_dump_json() in original_education
    ]
    dropped = before - len(tailored.education)
    if dropped:
        warnings.append(f"Removed {dropped} education entry/entries not found in the original profile.")

    # ── 3. Certifications — drop entries not in original ─────────
    original_certifications = {cert.model_dump_json() for cert in profile.certifications}
    before = len(tailored.certifications)
    tailored.certifications = [
        cert for cert in tailored.certifications
        if cert.model_dump_json() in original_certifications
    ]
    dropped = before - len(tailored.certifications)
    if dropped:
        warnings.append(f"Removed {dropped} certification(s) not found in the original profile.")

    # ── 4. Experience — drop unknown entries, strip invented tech ─
    original_experience = {
        _as_key(exp.company, exp.role, exp.start_date, exp.end_date): exp
        for exp in profile.experience
    }
    kept_experience = []
    for exp in tailored.experience:
        key = _as_key(exp.company, exp.role, exp.start_date, exp.end_date)
        original = original_experience.get(key)
        if original is None:
            warnings.append(
                f"Removed unknown experience '{exp.company} – {exp.role}' introduced by Gemini."
            )
            continue
        # Strip any invented technologies.
        original_tech = {_canon_skill(t) for t in original.technologies if _canon_skill(t)}
        before_tech = list(exp.technologies)
        exp.technologies = [
            t for t in exp.technologies
            if not _canon_skill(t) or _is_canonical_member(_canon_skill(t), original_tech)
        ]
        invented = [t for t in before_tech if t not in exp.technologies]
        if invented:
            warnings.append(
                f"Stripped invented technologies from '{exp.company}': {', '.join(invented)}"
            )
        kept_experience.append(exp)
    tailored.experience = kept_experience

    # ── 5. Projects — drop unknown entries, fix links, strip tech ─
    original_projects = {
        _plain(p.name).casefold(): p
        for p in profile.projects
    }
    kept_projects = []
    for project in tailored.projects:
        original = original_projects.get(_plain(project.name).casefold())
        if original is None:
            warnings.append(
                f"Removed unknown project '{project.name}' introduced by Gemini."
            )
            continue
        # Restore original link if Gemini changed it.
        if _plain(project.link) != _plain(original.link):
            warnings.append(
                f"Restored original link for project '{project.name}' (Gemini changed it)."
            )
            project.link = original.link
        # Strip invented technologies.
        original_tech = {_canon_skill(t) for t in original.technologies if _canon_skill(t)}
        before_tech = list(project.technologies)
        project.technologies = [
            t for t in project.technologies
            if not _canon_skill(t) or _is_canonical_member(_canon_skill(t), original_tech)
        ]
        invented = [t for t in before_tech if t not in project.technologies]
        if invented:
            warnings.append(
                f"Stripped invented technologies from project '{project.name}': {', '.join(invented)}"
            )
        kept_projects.append(project)
    tailored.projects = kept_projects

    # ── 6. Skills — remap renamed categories, drop invented skills ─
    # Build two lookups over the *original* categories:
    #   canon_to_original_name  – canonical form  -> original display name
    #   canon_to_skill_set      – canonical form  -> set of canonical skill strings
    canon_to_original_name: dict[str, str] = {}
    canon_to_skill_set: dict[str, set[str]] = {}
    for cat_name, skills_list in profile.skills.categories.items():
        ck = _canon_skill(cat_name)
        canon_to_original_name[ck] = cat_name
        canon_to_skill_set[ck] = {_canon_skill(s) for s in skills_list if _canon_skill(s)}

    sanitized_categories = []
    for skill_category in tailored.skills.categories:
        cat_name = skill_category.category
        canon_cat = _canon_skill(cat_name)

        # Try to match the Gemini category name to an original one.
        matched_ck: str | None = None
        if canon_cat in canon_to_original_name:
            matched_ck = canon_cat
        else:
            # Broader fallback: substring or case-insensitive match.
            plain_cat = _plain(cat_name).casefold()
            for ck, orig_name in canon_to_original_name.items():
                if plain_cat == _plain(orig_name).casefold():
                    matched_ck = ck
                    break
            if matched_ck is None:
                # Last resort: check if either name is a substring of the other.
                for ck, orig_name in canon_to_original_name.items():
                    if plain_cat in _plain(orig_name).casefold() or _plain(orig_name).casefold() in plain_cat:
                        matched_ck = ck
                        break

        if matched_ck is None:
            warnings.append(
                f"Removed unknown skill category '{cat_name}' introduced by Gemini."
            )
            continue

        # Restore the original category name.
        original_cat_name = canon_to_original_name[matched_ck]
        if skill_category.category != original_cat_name:
            warnings.append(
                f"Renamed skill category '{skill_category.category}' -> '{original_cat_name}'."
            )
            skill_category.category = original_cat_name

        # Strip invented skills within this category.
        allowed_skills = canon_to_skill_set[matched_ck]
        before_skills = list(skill_category.skills)
        skill_category.skills = [
            s for s in skill_category.skills
            if not _canon_skill(s) or _is_canonical_member(_canon_skill(s), allowed_skills)
        ]
        invented_skills = [s for s in before_skills if s not in skill_category.skills]
        if invented_skills:
            warnings.append(
                f"Removed invented skills from '{original_cat_name}': {', '.join(invented_skills)}"
            )

        sanitized_categories.append(skill_category)
    tailored.skills.categories = sanitized_categories

    # ── 7. Achievements — restore HTML list formatting ────────
    # The master resume stores achievements as HTML with <ul>/<li> tags
    # from the rich-text editor.  Gemini's structured output schema
    # treats achievements as a plain str, so the model often strips the
    # HTML wrapping and returns bare text.  When that happens the LaTeX
    # pipeline receives text without list markup, producing plain
    # paragraphs instead of bullet points in the PDF.
    #
    # Strategy: if the *original* achievements contain HTML list tags
    # (<ul> or <ol>) but the tailored version does not, re-wrap the
    # tailored text items back into the same list type so the
    # downstream html_to_latex converter can emit \begin{itemize}.
    import re as _re
    from bs4 import BeautifulSoup as _BS

    original_ach = (profile.achievements or "").strip()
    tailored_ach = (tailored.achievements or "").strip()

    if original_ach and tailored_ach:
        original_has_list = bool(_re.search(r"<[uo]l[\s>]", original_ach, _re.IGNORECASE))
        tailored_has_list = bool(_re.search(r"<[uo]l[\s>]", tailored_ach, _re.IGNORECASE))

        if original_has_list and not tailored_has_list:
            # Determine the list type used in the original (<ul> or <ol>).
            list_tag = "ul" if "<ul" in original_ach.lower() else "ol"

            # Count how many items the original list contained.
            original_soup = _BS(original_ach, "html.parser")
            original_item_count = len(original_soup.find_all("li"))

            # Extract individual text items from the tailored plain text.
            # The AI may return newline-separated lines, or a single block;
            # also handle residual <p>/<li> fragments if present.
            soup = _BS(tailored_ach, "html.parser")
            raw_text = soup.get_text("\n")
            lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]

            # Strip common leading list markers (•, -, *, etc.) the AI
            # may have inserted as plain-text bullets.
            cleaned = []
            for ln in lines:
                ln = _re.sub(r"^[•·▪▸►\-\*–—]\s+", "", ln)
                ln = _re.sub(r"^\d+[.)]\s+", "", ln)
                if ln:
                    cleaned.append(ln)

            # If the AI collapsed multiple achievements into a single line
            # but the original had multiple items, the content is too
            # mangled to recover reliably — fall back to the original.
            if len(cleaned) <= 1 and original_item_count > 1:
                tailored.achievements = original_ach
                warnings.append(
                    "AI collapsed achievements into a single string — "
                    "restored original achievements verbatim."
                )
            elif cleaned:
                li_items = "".join(f"<li>{item}</li>" for item in cleaned)
                tailored.achievements = f"<{list_tag}>{li_items}</{list_tag}>"
                warnings.append(
                    "Restored HTML list formatting on achievements "
                    f"(AI returned plain text; re-wrapped in <{list_tag}>)."
                )

    # Log all corrections at DEBUG level for server-side visibility.
    for w in warnings:
        _log.debug("[sanitize_tailored_output] %s", w)

    return warnings


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

4. Do NOT reorder top-level sections. Keep the exact section structure identical to the master profile. Within each section, you may order individual entries (like work experiences or projects) by relevance, putting the most relevant items first.

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
    achievements. IMPORTANT: The achievements field may contain HTML list markup
    (e.g. <ul><li>…</li></ul>). You MUST preserve the HTML tags exactly as given.
    Do NOT strip the HTML or convert to plain text.

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
    Send the full profile + job description to MiniMax and get back
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
        If the MiniMax API call fails (network error, invalid key,
        rate limit, validation error on the response, etc.).  The
        caller (app.py) should catch this and return a helpful
        error to the frontend.

    HOW IT WORKS
    ------------
    1. Serialize the profile to a JSON string (pretty-printed for
       the model to read easily).
    2. Construct the user message with both the profile and JD,
       plus the target JSON Schema so the model knows the shape.
    3. Call MiniMax with response_format=json_object and parse
       the response as TailoredProfile via Pydantic.
    4. Run the anti-fabrication sanitizer against the master
       profile.
    """
    # ── Step 1: Serialize the profile ─────────────────────────
    # model_dump_json() produces a clean JSON string.  We use
    # indent=2 so the model can read it easily (LLMs process
    # formatted JSON better than minified JSON).
    profile_json = profile.model_dump_json(indent=2)
    schema_json = json.dumps(TailoredProfile.model_json_schema(), indent=2)

    # ── Step 2: Build the user message ────────────────────────
    user_message = (
        f"══ CANDIDATE PROFILE (JSON) ══\n"
        f"{profile_json}\n\n"
        f"══ JOB DESCRIPTION ══\n"
        f"{job_description}\n\n"
        f"══ RESPONSE SCHEMA (JSON Schema for TailoredProfile) ══\n"
        f"{schema_json}\n\n"
        f"Now produce the tailored resume JSON."
    )

    # ── Step 3: Call MiniMax with JSON-object response format ─
    tailored = _chat_json(
        system=_SYSTEM_PROMPT,
        user=user_message,
        schema=TailoredProfile,
        temperature=0.3,  # Low temperature for factual accuracy
        schema_hint_name="TailoredProfile",
    )

    # ── Step 4: Anti-fabrication sanitizer ────────────────────
    # Even though the model is constrained to JSON matching the
    # schema, content inside that JSON could still misrepresent
    # the master profile. Run the sanitizer to drop anything
    # that doesn't trace back to `profile`.
    _sanitize_tailored_output(profile, tailored)

    return tailored


def parse_resume_text(text: str) -> Profile:
    """
    Send the raw resume text to MiniMax and parse it into a structured Profile Pydantic object.
    After parsing, list-like free-text fields (achievements, certification descriptions) are
    post-processed into semantic HTML so the rich-text editor receives properly formatted content.
    """
    schema_json = json.dumps(TailoredProfile.model_json_schema(), indent=2)
    system_instruction = (
        "You are an expert AI resume parser. Your job is to extract all information from the provided resume text "
        "and return it structured exactly matching the schema. Extract all contact details (name, email, phone, links), "
        "education, work experience, projects, skills, certifications, and achievements. Be as accurate and complete as possible, "
        "preserving all dates, details, bullets, and tools. Do not invent any information that does not exist in the resume text.\n\n"
        "Crucial formatting rule: Clean up any PDF extraction artifacts like raw ligatures (e.g. replace '\\ufb01' or similar raw characters with 'fi', "
        "replace '\\ufb03' with 'ffi', replace '\\ufb02' with 'fl') and fix word spacing issues (e.g. 'JA V A' -> 'JAVA', 'F rameworks' -> 'Frameworks', "
        "'T echnologies' -> 'Technologies'). Ensure all output strings have proper spelling, casing, and spacing.\n\n"
        "List formatting rules (IMPORTANT):\n"
        "- For the 'achievements' field: if the source contains multiple distinct achievements, "
        "achievements, or bullet points, return them as separate entries joined by newlines (one achievement per line). "
        "Do NOT collapse them into a single run-on sentence. Preserve any leading markers (e.g. '• ', '- ', '1. ') "
        "exactly as they appear in the source so downstream formatting can detect the list type.\n"
        "- For certification 'description' fields: apply the same rule — one point per line, preserving "
        "original list markers if present.\n"
        "- For experience bullets and project bullets: each bullet point must be its own separate string in the list. "
        "Never merge multiple bullet points into one string."
    )

    user_message = (
        f"══ RESUME TEXT ══\n{text}\n\n"
        f"══ RESPONSE SCHEMA (JSON Schema for TailoredProfile) ══\n"
        f"{schema_json}\n\n"
        f"Extract and structure the resume into the response schema."
    )

    tailored = _chat_json(
        system=system_instruction,
        user=user_message,
        schema=TailoredProfile,
        temperature=0.1,  # Low temperature for highest extraction accuracy
        schema_hint_name="TailoredProfile (parser)",
    )
    profile = tailored.to_profile()

    # Post-process rich-text fields: convert plain-text list patterns to HTML
    # so that achievements and certification descriptions render as proper lists
    # in the rich-text editor instead of collapsing into a single paragraph.
    from services.parser_service import postprocess_parsed_profile
    postprocess_parsed_profile(profile)

    return profile


# ── Job Analysis Schema and Service ───────────────────────────
from pydantic import BaseModel, Field

class JobAnalysis(BaseModel):
    """Structured analysis results from a job description."""
    skills: list[str] = Field(
        description="Top 5-8 critical technical skills, programming languages, libraries, frameworks, databases, or developer tools requested."
    )
    keywords: list[str] = Field(
        description="Top 5-8 crucial domain concepts, industry buzzwords, methodologies, workflows, or certifications requested (e.g., Agile, Scrum, CI/CD, System Design, Unit Testing)."
    )


def analyze_job_description(job_description: str) -> JobAnalysis:
    """
    Send a job description to MiniMax and parse it into structured JobAnalysis (skills and keywords).
    """
    schema_json = json.dumps(JobAnalysis.model_json_schema(), indent=2)
    system_instruction = (
        "You are an expert AI recruiter and technical job analyst.\n\n"
        "Your task is to analyze the provided job description and extract:\n"
        "1. The top 5-8 most critical technical skills (languages, frameworks, databases, developer tools, libraries).\n"
        "2. The top 5-8 most critical keywords, domain concepts, or methodologies (e.g., Agile, Scrum, CI/CD, System Design, Unit Testing, AWS, certifications).\n\n"
        "Keep each skill and keyword concise (usually 1-3 words, e.g., 'React', 'TypeScript', 'Unit Testing').\n"
        "Do NOT invent requirements. Only extract what is explicitly mentioned or strongly, clearly implied in the job description."
    )

    user_message = (
        f"══ JOB DESCRIPTION ══\n{job_description}\n\n"
        f"══ RESPONSE SCHEMA (JSON Schema for JobAnalysis) ══\n"
        f"{schema_json}\n\n"
        f"Analyze and extract the key skills and keywords."
    )

    return _chat_json(
        system=system_instruction,
        user=user_message,
        schema=JobAnalysis,
        temperature=0.1,  # Low temperature for highest analytical accuracy
        schema_hint_name="JobAnalysis",
    )


# ── Tailor Resume Workspace Helpers ───────────────────────────
from models.tailoring_result import TailoringResult

def tailor_resume_v2(profile: Profile, job_description: str, preferences: dict) -> TailoringResult:
    """
    Initial tailoring from the setup inputs (preferences like style, focus areas, job level).
    Returns a structured TailoringResult.
    """
    profile_json = profile.model_dump_json(indent=2)
    style = preferences.get("style", "balanced")
    focus_areas = preferences.get("focus_areas", ["skills", "projects", "experience", "summary"])
    job_level = preferences.get("job_level", "entry")
    
    # Customize the system instruction based on style, focus areas, and job level
    style_guideline = ""
    if style == "conservative":
        style_guideline = "Style preference: Conservative (Minimal AI rephrasing; preserve original bullet points as much as possible, focusing on selecting the most relevant sections)."
    elif style == "aggressive":
        style_guideline = "Style preference: Aggressive (Rewrite bullet points heavily to maximize ATS matching and keywords, but remain strictly truthful. Do not invent any metrics or facts)."
    else:
        style_guideline = "Style preference: Balanced (Optimize bullet point phrasing to match keywords while carefully preserving the original meanings and facts)."
        
    focus_guideline = f"Prioritized Focus Areas: {', '.join(focus_areas)}. Optimize and expand these sections as top priority."
    tone_guideline = f"Tone / Job Level expectation: {job_level.upper()} level professionals. Use appropriate vocabulary and industry terminology."
    
    system_instruction = f"""\
You are an elite professional resume tailoring assistant.

You will receive:
1. A candidate's COMPLETE master profile as JSON.
2. A job description they are applying to.

Your task: Produce a tailored resume by selecting and optimizing content from the profile to match the job description.

ABSOLUTE TRUTHFULNESS RULES (these override every other instruction):
A. The candidate's Master Resume is the ONLY source of truth. You may ONLY use
   information that already exists in the master profile.
B. You are STRICTLY FORBIDDEN from adding ANY of the following, even if they look
   like obvious "fixes", typos, or improvements:
      - new skills
      - new technologies, frameworks, libraries, or tools
      - new certifications, courses, or awards
      - new work experiences, internships, or roles
      - new projects, side projects, or hackathons
      - new companies, clients, or organizations
      - new metrics, percentages, numbers, or dollar amounts
      - new links, GitHub repos, or portfolio URLs
   Every skill, technology, certification, project, experience, company, and
   link in the tailored profile MUST be a verbatim member of an existing
   category or record in the master profile. Do NOT silently "clean up", "fix
   the casing of", "expand the acronym of", or otherwise rename skill strings.
   If the master profile says "LLMsLLMs", the tailored profile must also say
   "LLMsLLMs". Treat the master data as immutable.
C. Keywords that the job description asks for but that are NOT supported by
   the master profile MUST go ONLY into the `keywords_not_included_list`
   field. They MUST NEVER be inserted into the tailored profile.
D. You may REWRITE wording of bullet points (action verbs, phrasing, emphasis)
   as long as the underlying facts, metrics, technologies, and entities stay
   truthful to the master profile.
E. Do NOT reorder top-level sections. The section order (Personal Info, Education, Experience, Projects, Skills, Certifications, Achievements) must remain exactly identical to the master profile. You may reorder individual entries within a section (such as experience entries, project entries, or skills) so the most relevant ones appear first.
F. You may EMPHASIZE existing experience by re-surfacing it more prominently
   (e.g. moving a relevant project up within the projects list) but you may NOT fabricate new
   experiences to fill a gap.

STYLE GUIDELINES:
4. {style_guideline}
5. {focus_guideline}
6. {tone_guideline}

REQUIRED OUTPUT SCHEMA (TailoringResult):
- profile: The tailored profile content. Must contain ONLY entries (skills,
  experiences, projects, certifications, links) that already exist in the
  master profile, with rewritten bullets and original top-level section ordering.
- suggestions: Expanded list of recommendations / modifications made. Provide a
  title and detailed transparent explanation explaining WHY you made each
  suggestion based on the job description.
- keywords_not_included_list: A list of key skill terms or keywords requested
  in the job description that could not be added because they are missing
  from the candidate's master profile. (Never fabricate experience to include
  them.) This list is the ONLY place where job-description skills that the
  candidate does not have may appear.
- stats: Factual optimization statistics:
    - keywords_added: count of job description keywords successfully added
      (i.e. keywords from the JD that already existed in the master profile
      and were emphasized or surfaced in the tailored profile).
    - bullets_improved: count of bullet points modified.
    - sections_reordered: count of sections whose content was improved or tailored.
    - keywords_not_included: count of job description keywords that were
      omitted because they are missing from the master resume.
- insights: Resume analysis insights (key strengths, opportunities for
  improvement, resume length review, and AI confidence level).
"""

    user_message = (
        f"══ CANDIDATE PROFILE (JSON) ══\n"
        f"{profile_json}\n\n"
        f"══ JOB DESCRIPTION ══\n"
        f"{job_description}\n\n"
        f"══ RESPONSE SCHEMA (JSON Schema for TailoringResult) ══\n"
        f"{json.dumps(TailoringResult.model_json_schema(), indent=2)}\n\n"
        f"Now produce the tailored resume JSON structured exactly as TailoringResult."
    )

    result = _chat_json(
        system=system_instruction,
        user=user_message,
        schema=TailoringResult,
        temperature=0.2,
        schema_hint_name="TailoringResult (initial v2)",
    )
    _sanitize_tailored_output(profile, result.profile)
    return result


def chat_tailor_resume(
    master_profile: Profile,
    active_profile: Profile,
    job_description: str,
    message: str,
    chat_history: list[dict[str, str]]
) -> TailoringResult:
    """
    Applies a user instruction (chat prompt) to refine the active tailored profile.
    Uses the master profile as reference to prevent fabrication.
    """
    master_json = master_profile.model_dump_json(indent=2)
    active_json = active_profile.model_dump_json(indent=2)
    
    # Format chat history for context
    history_str = ""
    for turn in chat_history:
        role = "User" if turn["role"] == "user" else "AI Assistant"
        history_str += f"{role}: {turn['content']}\n"
        
    system_instruction = """\
You are an elite technical resume editor assistant collaborating interactively with a candidate.

Your goal is to refine the candidate's CURRENT tailored resume based on their custom instructions (chat message).

ABSOLUTE TRUTHFULNESS RULES (these override every other instruction):
A. The ORIGINAL MASTER RESUME is the single source of truth. Every skill,
   technology, certification, project, experience, company, and link in the
   tailored profile MUST be a verbatim member of an existing entry in the
   master profile. Do NOT rename, "fix", expand acronyms of, or otherwise
   mutate skill strings.
B. You are STRICTLY FORBIDDEN from adding ANY of the following, even when a
   user chat instruction might encourage it:
      - new skills, technologies, frameworks, libraries, or tools
      - new certifications, courses, or awards
      - new work experiences, internships, or roles
      - new projects, side projects, or hackathons
      - new companies, clients, or organizations
      - new metrics, percentages, numbers, or dollar amounts
      - new links, GitHub repos, or portfolio URLs
C. If the user's instruction implies adding something that does not exist in
   the master profile, DO NOT add it. Instead, list it in
   `keywords_not_included_list` and explain in the suggestions that the
   requested item is not supported by the master resume.
D. Do NOT reorder top-level sections. The section order (Personal Info, Education, Experience, Projects, Skills, Certifications, Achievements) must remain exactly identical to the master profile. You may REWRITE wording of bullets (action verbs, phrasing, emphasis) and reorder individual items within a section (like experience or project entries) to satisfy the user's request, but you may NOT fabricate.
E. Be transparent: in `suggestions`, outline what changes were made in
   response to this instruction and WHY, including any items you refused to
   add because they are not supported by the master profile.

REQUIRED OUTPUT SCHEMA (TailoringResult):
- profile: The updated tailored profile. All entries must still trace back
  to the master profile verbatim (skill strings, technologies, links,
  company names, dates), and top-level sections must remain in their original order.
- suggestions: Specific details of what changed in this turn and WHY,
  including any refused additions.
- keywords_not_included_list: Skills/keywords the user asked for that the
  candidate does not have in the master profile.
- stats: Factual statistics after these modifications.
- insights: Updated resume analysis insights (strengths, opportunities,
  length, confidence).
"""

    contents = f"""\
══ JOB DESCRIPTION ══
{job_description}

══ ORIGINAL MASTER RESUME ══
{master_json}

══ CURRENT TAILORED RESUME DRAFT ══
{active_json}

══ RECENT CHAT HISTORY ══
{history_str}

══ NEW USER REFINEMENT REQUEST ══
{message}

══ RESPONSE SCHEMA (JSON Schema for TailoringResult) ══
{json.dumps(TailoringResult.model_json_schema(), indent=2)}

Please modify the CURRENT TAILORED RESUME DRAFT to satisfy the request. Ensure all updates remain 100% truthful to the ORIGINAL MASTER RESUME facts.
"""

    result = _chat_json(
        system=system_instruction,
        user=contents,
        schema=TailoringResult,
        temperature=0.2,
        schema_hint_name="TailoringResult (chat refine)",
    )
    _sanitize_tailored_output(master_profile, result.profile)
    return result


