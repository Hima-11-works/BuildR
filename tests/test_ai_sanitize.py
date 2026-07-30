"""
Tests for services/ai_service.py's _sanitize_tailored_output() — the
anti-hallucination guard that runs on every Gemini tailoring response
before it reaches the user. Only exercises the sanitizer function itself;
no network calls / API key needed.
"""
import json

from models.profile import (
    Profile, PersonalInfo, Experience, Project, Skills, Certification,
)
from models.tailored_profile import (
    TailoredProfile, TailoredPersonalInfo, TailoredLink,
    TailoredExperience, TailoredProject, TailoredSkills, TailoredSkillCategory,
)
from services.ai_service import (
    _sanitize_tailored_output,
    _canon_skill,
    _skills_equivalent,
    _strip_response_wrappers,
)


def make_original_profile(**overrides):
    defaults = dict(
        personal_info=PersonalInfo(name="Jane Doe", email="jane@example.com", phone="555-1234"),
        experience=[
            Experience(
                company="Acme Corp", role="Engineer",
                start_date="Jan 2022", end_date="Present",
                bullets=["Built things"], technologies=["Python", "Flask"],
            )
        ],
        projects=[
            Project(name="Cool Project", description="A thing", link="https://github.com/jane/cool", technologies=["React"]),
        ],
        skills=Skills(categories={"Languages": ["Python", "JavaScript"]}),
        certifications=[Certification(name="AWS SAA", issuer="AWS", date="2024")],
    )
    defaults.update(overrides)
    return Profile(**defaults)


def make_tailored_from(profile: Profile, **overrides) -> TailoredProfile:
    """Build a TailoredProfile that faithfully mirrors `profile`, so tests
    only need to override the specific field they're corrupting."""
    base = dict(
        personal_info=TailoredPersonalInfo(
            name=profile.personal_info.name,
            email=profile.personal_info.email,
            phone=profile.personal_info.phone,
            links=[TailoredLink(label=k, url=v) for k, v in profile.personal_info.links.items()],
        ),
        education=list(profile.education),
        experience=[
            TailoredExperience(
                company=e.company, role=e.role, start_date=e.start_date, end_date=e.end_date,
                work_mode=e.work_mode, bullets=list(e.bullets), technologies=list(e.technologies),
            ) for e in profile.experience
        ],
        projects=[
            TailoredProject(
                name=p.name, description=p.description, bullets=list(p.bullets),
                technologies=list(p.technologies), link=p.link,
            ) for p in profile.projects
        ],
        skills=TailoredSkills(categories=[
            TailoredSkillCategory(category=k, skills=list(v)) for k, v in profile.skills.categories.items()
        ]),
        certifications=list(profile.certifications),
        achievements=profile.achievements,
    )
    base.update(overrides)
    return TailoredProfile(**base)


class TestPersonalInfoTamperRestored:
    def test_altered_personal_info_is_restored(self):
        profile = make_original_profile()
        tailored = make_tailored_from(
            profile,
            personal_info=TailoredPersonalInfo(name="Someone Else", email="hacked@evil.com", phone=None, links=[]),
        )
        _sanitize_tailored_output(profile, tailored)
        restored = tailored.personal_info.to_personal_info()
        assert restored.name == "Jane Doe"
        assert restored.email == "jane@example.com"


class TestExperienceFabrication:
    def test_fabricated_experience_is_removed(self):
        profile = make_original_profile()
        tailored = make_tailored_from(profile)
        tailored.experience.append(
            TailoredExperience(
                company="Fake Corp", role="CEO", start_date="2020", end_date="2021",
                bullets=["Made this up"], technologies=[],
            )
        )
        _sanitize_tailored_output(profile, tailored)
        companies = [e.company for e in tailored.experience]
        assert "Fake Corp" not in companies
        assert "Acme Corp" in companies

    def test_invented_technology_is_stripped(self):
        profile = make_original_profile()
        tailored = make_tailored_from(profile)
        tailored.experience[0].technologies = ["Python", "Kubernetes"]  # Kubernetes never existed
        _sanitize_tailored_output(profile, tailored)
        assert "Kubernetes" not in tailored.experience[0].technologies
        assert "Python" in tailored.experience[0].technologies

    def test_real_technology_survives(self):
        profile = make_original_profile()
        tailored = make_tailored_from(profile)
        tailored.experience[0].technologies = ["Flask"]
        _sanitize_tailored_output(profile, tailored)
        assert tailored.experience[0].technologies == ["Flask"]


class TestProjectHandling:
    def test_fabricated_project_is_removed(self):
        profile = make_original_profile()
        tailored = make_tailored_from(profile)
        tailored.projects.append(
            TailoredProject(name="Fabricated App", description="Never built", bullets=[], technologies=[], link=None)
        )
        _sanitize_tailored_output(profile, tailored)
        names = [p.name for p in tailored.projects]
        assert "Fabricated App" not in names

    def test_changed_link_is_restored(self):
        profile = make_original_profile()
        tailored = make_tailored_from(profile)
        tailored.projects[0].link = "https://evil.example.com/phishing"
        _sanitize_tailored_output(profile, tailored)
        assert tailored.projects[0].link == "https://github.com/jane/cool"


class TestSkillsHandling:
    def test_invented_skill_category_removed(self):
        profile = make_original_profile()
        tailored = make_tailored_from(profile)
        tailored.skills.categories.append(TailoredSkillCategory(category="Fake Skills", skills=["Telepathy"]))
        _sanitize_tailored_output(profile, tailored)
        cat_names = [c.category for c in tailored.skills.categories]
        assert "Fake Skills" not in cat_names

    def test_invented_skill_within_real_category_removed(self):
        profile = make_original_profile()
        tailored = make_tailored_from(profile)
        tailored.skills.categories[0].skills = ["Python", "Rust"]  # Rust never existed
        _sanitize_tailored_output(profile, tailored)
        assert "Rust" not in tailored.skills.categories[0].skills
        assert "Python" in tailored.skills.categories[0].skills

    def test_renamed_category_mapped_back_to_original(self):
        profile = make_original_profile()
        tailored = make_tailored_from(profile)
        tailored.skills.categories[0].category = "Programming Languages"  # AI renamed "Languages"
        _sanitize_tailored_output(profile, tailored)
        cat_names = [c.category for c in tailored.skills.categories]
        assert "Languages" in cat_names
        assert "Programming Languages" not in cat_names


class TestFuzzySkillMatching:
    def test_punctuation_variant_is_equivalent(self):
        assert _skills_equivalent("React.js", "ReactJS")
        assert _skills_equivalent("Node.js", "NodeJS")
        assert _skills_equivalent("CI/CD", "CICD")

    def test_distinct_technologies_never_match(self):
        assert not _skills_equivalent("PostgreSQL", "Postgres")
        assert not _skills_equivalent("React", "Vue")

    def test_repeated_token_typo_is_equivalent(self):
        assert _canon_skill("LLMsLLMs") == _canon_skill("LLMs")


class TestAchievementsHtmlRestoration:
    def test_stripped_html_list_is_rewrapped(self):
        profile = make_original_profile(achievements="<ul><li>Won a hackathon</li><li>Published a paper</li></ul>")
        tailored = make_tailored_from(profile, achievements="Won a hackathon\nPublished a paper")
        _sanitize_tailored_output(profile, tailored)
        assert "<ul>" in tailored.achievements
        assert "<li>" in tailored.achievements

    def test_collapsed_multi_item_list_falls_back_to_original(self):
        profile = make_original_profile(achievements="<ul><li>Item one</li><li>Item two</li></ul>")
        tailored = make_tailored_from(profile, achievements="Item one and item two combined into one sentence")
        _sanitize_tailored_output(profile, tailored)
        assert tailored.achievements == profile.achievements


class TestStripResponseWrappers:
    """
    Defense-in-depth tests for _strip_response_wrappers().

    minimax-m3 is a reasoning model and sometimes emits  think ... think
    blocks or markdown code fences even when asked for json_object
    output. The wrapper-stripping helper must extract the JSON object
    so Pydantic validation can run.
    """

    # Build marker constants via concatenation so the test source itself
    # doesn't get any surrounding tooling that might strip  think tags.
    _THINK_OPEN = "<" + "think" + ">"
    _THINK_CLOSE = "<" + "/" + "think" + ">"
    _FENCE = "```"

    def test_passes_clean_json_through_unchanged(self):
        original = '{"name":"Ada","experience":[]}'
        assert _strip_response_wrappers(original) == original

    def test_strips_markdown_code_fences(self):
        wrapped = self._FENCE + "json\n{\"name\":\"Ada\"}\n" + self._FENCE
        cleaned = _strip_response_wrappers(wrapped)
        assert json.loads(cleaned) == {"name": "Ada"}

    def test_strips_think_block_followed_by_json(self):
        wrapped = (
            self._THINK_OPEN
            + "\nLet me carefully extract the fields...\n"
            + self._THINK_CLOSE
            + "\n{\"name\":\"Ada\",\"experience\":[]}"
        )
        cleaned = _strip_response_wrappers(wrapped)
        assert json.loads(cleaned) == {"name": "Ada", "experience": []}

    def test_strips_think_block_followed_by_code_fence(self):
        wrapped = (
            self._THINK_OPEN
            + "\nreasoning text\n"
            + self._THINK_CLOSE
            + "\n"
            + self._FENCE
            + "json\n{\"name\":\"Ada\"}\n"
            + self._FENCE
        )
        cleaned = _strip_response_wrappers(wrapped)
        assert json.loads(cleaned) == {"name": "Ada"}

    def test_extracts_json_from_surrounding_prose(self):
        wrapped = 'Here you go:\n{"name":"Ada"}\nThanks!'
        cleaned = _strip_response_wrappers(wrapped)
        assert json.loads(cleaned) == {"name": "Ada"}

    def test_handles_unrecoverable_input_gracefully(self):
        # No JSON, no closing think tag — must not raise, just return
        # the input unchanged so the caller surfaces the original error.
        wrapped = self._THINK_OPEN + "\njust thinking, no JSON"
        assert _strip_response_wrappers(wrapped) == wrapped

    def test_handles_empty_input(self):
        assert _strip_response_wrappers("") == ""
        assert _strip_response_wrappers("   \n  ") == "   \n  ".strip()
