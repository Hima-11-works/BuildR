"""
Unit tests for services/master_optimizer.py.

The optimizer is a deterministic in-memory transform — no AI calls,
no network, no disk writes. Each test exercises one of the
optimization branches.
"""
import pytest

from models.profile import (
    Profile, PersonalInfo, Experience, Project, Certification,
    Skills,
)
from services.master_optimizer import (
    optimize_profile,
    MAX_BULLETS_PER_EXPERIENCE,
    MAX_BULLETS_PER_PROJECT,
    MAX_CERTIFICATIONS,
    MAX_BULLET_CHARS,
    MAX_PROJECT_DESCRIPTION_CHARS,
)


# ── Fixtures ──────────────────────────────────────────────────
def make_profile(**overrides):
    """Minimal profile with empty sections; tests fill in what they need."""
    defaults = dict(
        personal_info=PersonalInfo(name="Test", email="t@example.com"),
        experience=[],
        projects=[],
        skills=Skills(),
        certifications=[],
        achievements="",
    )
    defaults.update(overrides)
    return Profile(**defaults)


def _make_bullet(text: str) -> str:
    """Wrap plain text in the <ul><li> shape the editor produces."""
    return f"<ul><li>{text}</li></ul>"


def _make_project(**overrides):
    """Build a Project with a valid placeholder description (the model
    requires description; tests can override with their own)."""
    defaults = dict(name="Cool Project", description="A short blurb.")
    defaults.update(overrides)
    return Project(**defaults)


# ── Empty / trivial inputs ────────────────────────────────────

class TestNoOp:
    def test_empty_profile_returns_no_changes(self):
        profile = make_profile()
        optimized, changes = optimize_profile(profile)
        assert changes == []
        assert optimized.model_dump() == profile.model_dump()

    def test_already_tight_profile_returns_no_changes(self):
        """A small profile within all caps should produce zero changes."""
        profile = make_profile(
            experience=[
                Experience(
                    company="Acme", role="Engineer",
                    start_date="Jan 2022", end_date="Present",
                    bullets=[_make_bullet(f"Built thing {i}") for i in range(3)],
                    technologies=["Python", "Flask"],
                ),
            ],
        )
        optimized, changes = optimize_profile(profile)
        assert changes == []
        assert optimized.experience[0].bullets == profile.experience[0].bullets

    def test_returns_validated_profile(self):
        """The returned Profile must round-trip through Pydantic validation."""
        profile = make_profile(
            experience=[
                Experience(
                    company="Acme", role="Engineer",
                    start_date="Jan 2022", end_date="Present",
                    bullets=[_make_bullet("Built thing")] * 10,
                ),
            ],
        )
        optimized, _changes = optimize_profile(profile)
        # Re-validating must not raise.
        Profile.model_validate(optimized.model_dump())


# ── Bullet caps ───────────────────────────────────────────────

class TestBulletCaps:
    def test_caps_experience_bullets(self):
        too_many = [_make_bullet(f"Bullet {i}") for i in range(8)]
        profile = make_profile(
            experience=[
                Experience(
                    company="Acme", role="Engineer",
                    start_date="Jan 2022", end_date="Present",
                    bullets=too_many,
                ),
            ],
        )
        optimized, changes = optimize_profile(profile)
        assert len(optimized.experience[0].bullets) == MAX_BULLETS_PER_EXPERIENCE
        # The first MAX_BULLETS_PER_EXPERIENCE are kept (strongest first).
        assert optimized.experience[0].bullets == too_many[:MAX_BULLETS_PER_EXPERIENCE]
        assert any("Trimmed 3 bullet(s)" in c for c in changes)

    def test_caps_project_bullets(self):
        too_many = [_make_bullet(f"Built {i}") for i in range(6)]
        profile = make_profile(
            projects=[_make_project(name="Cool Project", bullets=too_many)],
        )
        optimized, changes = optimize_profile(profile)
        assert len(optimized.projects[0].bullets) == MAX_BULLETS_PER_PROJECT
        assert any("Trimmed 2 bullet(s) from project" in c for c in changes)

    def test_does_not_pad_below_cap(self):
        few = [_make_bullet(f"Only {i}") for i in range(3)]
        profile = make_profile(
            experience=[
                Experience(
                    company="Acme", role="Engineer",
                    start_date="Jan 2022", end_date="Present",
                    bullets=few,
                ),
            ],
        )
        optimized, changes = optimize_profile(profile)
        assert len(optimized.experience[0].bullets) == 3
        assert not any("Trimmed" in c for c in changes)


# ── Redundant lead stripping ─────────────────────────────────

class TestRedundantLeadStripping:
    @pytest.mark.parametrize("lead,replacement_text", [
        ("Responsible for building the API gateway", "Building the API gateway"),
        ("Duties included shipping features", "shipping features"),
        ("Duties: shipping features", "shipping features"),
        ("Tasked with shipping features", "shipping features"),
        ("Worked on shipping features", "shipping features"),
        ("Helped to deploy the service", "deploy the service"),
        ("Helped with the migration", "the migration"),
    ])
    def test_strips_redundant_lead(self, lead, replacement_text):
        profile = make_profile(
            experience=[
                Experience(
                    company="Acme", role="Engineer",
                    start_date="Jan 2022", end_date="Present",
                    bullets=[_make_bullet(lead)],
                ),
            ],
        )
        optimized, changes = optimize_profile(profile)
        bullet_text = optimized.experience[0].bullets[0]
        # Strip HTML to compare
        from bs4 import BeautifulSoup
        plain = BeautifulSoup(bullet_text, "html.parser").get_text(" ", strip=True)
        assert not plain.lower().startswith(lead.split()[0].lower() + " " + lead.split()[1].lower())
        # The substantive part survives.
        assert replacement_text.split()[0] in plain or replacement_text.lower() in plain.lower()
        assert any("verbose bullets" in c for c in changes)

    def test_does_not_strip_mid_sentence_redundant_phrase(self):
        """A bullet where 'responsible for' is mid-sentence must not be touched."""
        profile = make_profile(
            experience=[
                Experience(
                    company="Acme", role="Engineer",
                    start_date="Jan 2022", end_date="Present",
                    bullets=[_make_bullet("Built a system responsible for monitoring uptime")],
                ),
            ],
        )
        optimized, _changes = optimize_profile(profile)
        bullet_text = optimized.experience[0].bullets[0]
        from bs4 import BeautifulSoup
        plain = BeautifulSoup(bullet_text, "html.parser").get_text(" ", strip=True)
        # Mid-sentence "responsible for" must remain.
        assert "responsible for" in plain.lower()


# ── Technology deduplication ─────────────────────────────────

class TestTechDedupe:
    def test_dedupe_tech_case_insensitive(self):
        profile = make_profile(
            experience=[
                Experience(
                    company="Acme", role="Engineer",
                    start_date="Jan 2022", end_date="Present",
                    technologies=["Python", "python", "PYTHON", "Python "],
                ),
            ],
        )
        optimized, changes = optimize_profile(profile)
        assert optimized.experience[0].technologies == ["Python"]
        assert any("De-duplicated 3 tech stack" in c for c in changes)

    def test_keeps_distinct_techs(self):
        profile = make_profile(
            experience=[
                Experience(
                    company="Acme", role="Engineer",
                    start_date="Jan 2022", end_date="Present",
                    technologies=["Python", "Flask", "React"],
                ),
            ],
        )
        optimized, changes = optimize_profile(profile)
        assert optimized.experience[0].technologies == ["Python", "Flask", "React"]
        assert not any("De-duplicated" in c for c in changes)

    def test_dedupe_project_tech(self):
        profile = make_profile(
            projects=[_make_project(
                name="Cool Project",
                technologies=["Postgres", "postgres", "PostgreSQL"],
            )],
        )
        optimized, _changes = optimize_profile(profile)
        # "Postgres" and "PostgreSQL" are different strings → both kept.
        # But "Postgres"/"postgres" collapse to one. Result: 2 distinct.
        assert len(optimized.projects[0].technologies) == 2


# ── Bullet length trimming ───────────────────────────────────

class TestBulletTrimming:
    def test_long_bullet_truncated_at_sentence_boundary(self):
        # Construct a bullet that's comfortably over MAX_BULLET_CHARS so
        # trimming kicks in. The exact text doesn't matter — what matters
        # is the length + sentence boundaries.
        long_text = (
            "Built the entire payment processing pipeline from scratch, "
            "including the fraud detection module, the retry queue, "
            "the dead-letter exchange, the audit log emitter, and the "
            "integration with three external payment gateways (Stripe, "
            "Adyen, Braintree), while simultaneously migrating the legacy "
            "SOAP endpoints to a modern REST API with backwards-compatible "
            "shims."
        )
        assert len(long_text) > MAX_BULLET_CHARS
        profile = make_profile(
            experience=[
                Experience(
                    company="Acme", role="Engineer",
                    start_date="Jan 2022", end_date="Present",
                    bullets=[_make_bullet(long_text)],
                ),
            ],
        )
        optimized, changes = optimize_profile(profile)
        from bs4 import BeautifulSoup
        plain = BeautifulSoup(optimized.experience[0].bullets[0], "html.parser").get_text(" ", strip=True)
        assert len(plain) <= MAX_BULLET_CHARS + 2  # small margin for trailing punctuation
        # Ends at a sentence boundary, not mid-word.
        assert plain.endswith((".", "!", "?"))
        assert any("verbose bullets" in c for c in changes)

    def test_short_bullet_unchanged(self):
        profile = make_profile(
            experience=[
                Experience(
                    company="Acme", role="Engineer",
                    start_date="Jan 2022", end_date="Present",
                    bullets=[_make_bullet("Built a thing.")],
                ),
            ],
        )
        optimized, _changes = optimize_profile(profile)
        assert optimized.experience[0].bullets[0] == _make_bullet("Built a thing.")


# ── Certifications cap ───────────────────────────────────────

class TestCertifications:
    def test_caps_oldest_certifications(self):
        certs = [
            Certification(name=f"Cert {i}", issuer="Acme", date=f"202{i}-01-01")
            for i in range(6)
        ]
        profile = make_profile(certifications=certs)
        optimized, changes = optimize_profile(profile)
        assert len(optimized.certifications) == MAX_CERTIFICATIONS
        # The first MAX_CERTIFICATIONS are kept; the LAST 2 are dropped.
        assert optimized.certifications[0].name == "Cert 0"
        assert optimized.certifications[-1].name == f"Cert {MAX_CERTIFICATIONS - 1}"
        assert any("Dropped 2 of the oldest certifications" in c for c in changes)

    def test_under_cap_unchanged(self):
        certs = [
            Certification(name=f"Cert {i}", issuer="Acme", date=f"202{i}-01-01")
            for i in range(3)
        ]
        profile = make_profile(certifications=certs)
        optimized, changes = optimize_profile(profile)
        assert optimized.certifications == certs
        assert not any("certifications" in c for c in changes)


# ── Achievements NOT optimized (regression guard) ──────────────

class TestAchievementsNotOptimized:
    """An earlier version of the optimizer silently collapsed multi-bullet
    achievement lists into a single <li> when the section exceeded 400
    chars, which destroyed the user's bullet structure. The current
    optimizer must leave achievements untouched so the LaTeX renderer
    can preserve every <li> verbatim."""

    def test_short_achievements_unchanged(self):
        profile = make_profile(achievements="<ul><li>Won a hackathon</li></ul>")
        optimized, changes = optimize_profile(profile)
        assert optimized.achievements == "<ul><li>Won a hackathon</li></ul>"
        assert not any("achievements" in c for c in changes)

    def test_long_achievements_unchanged(self):
        long_html = "<ul>" + "".join(
            f"<li><p>Achievement {i}: led a major initiative that moved a key metric</p></li>"
            for i in range(20)
        ) + "</ul>"
        profile = make_profile(achievements=long_html)
        optimized, changes = optimize_profile(profile)
        # Every <li> survives, untouched.
        assert optimized.achievements == long_html
        assert not any("achievements" in c for c in changes)


# ── Project description trimming ─────────────────────────────

class TestProjectDescription:
    def test_long_description_truncated(self):
        long_desc = (
            "An ambitious full-stack project that combines a Next.js front-end, "
            "a Go microservice backend, and a Postgres database with Redis caching, "
            "deployed on AWS ECS, monitoring via Datadog, with extensive unit "
            "and integration test coverage and a CI/CD pipeline via GitHub Actions."
        )
        assert len(long_desc) > MAX_PROJECT_DESCRIPTION_CHARS
        profile = make_profile(
            projects=[_make_project(name="Cool Project", description=long_desc)],
        )
        optimized, changes = optimize_profile(profile)
        assert len(optimized.projects[0].description) <= MAX_PROJECT_DESCRIPTION_CHARS + 5
        assert any("Trimmed long description" in c for c in changes)

    def test_short_description_unchanged(self):
        profile = make_profile(
            projects=[_make_project(name="Cool Project", description="A short blurb.")],
        )
        optimized, _changes = optimize_profile(profile)
        assert optimized.projects[0].description == "A short blurb."


# ── Deep-copy isolation ──────────────────────────────────────

class TestImmutability:
    def test_does_not_mutate_input_profile(self):
        original_bullets = [_make_bullet(f"Original bullet {i}") for i in range(8)]
        original_tech = ["Python", "python"]
        profile = make_profile(
            experience=[
                Experience(
                    company="Acme", role="Engineer",
                    start_date="Jan 2022", end_date="Present",
                    bullets=list(original_bullets),
                    technologies=list(original_tech),
                ),
            ],
        )
        optimize_profile(profile)
        # The caller's profile must not have been modified.
        assert profile.experience[0].bullets == original_bullets
        assert profile.experience[0].technologies == original_tech
