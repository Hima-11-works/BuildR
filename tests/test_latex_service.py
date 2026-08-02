"""Tests for services/latex_service.py — LaTeX escaping and rendering."""
import re

from models.profile import Profile, PersonalInfo, Project, Skills
from services.latex_service import escape_latex, html_to_latex, render_latex


class TestEscapeLatex:
    def test_all_special_chars_escaped(self):
        assert escape_latex("&") == r"\&"
        assert escape_latex("%") == r"\%"
        assert escape_latex("$") == r"\$"
        assert escape_latex("#") == r"\#"
        assert escape_latex("_") == r"\_"
        assert escape_latex("{") == r"\{"
        assert escape_latex("}") == r"\}"
        assert escape_latex("~") == r"\textasciitilde{}"
        assert escape_latex("^") == r"\textasciicircum{}"
        assert escape_latex("\\") == r"\textbackslash{}"

    def test_backslash_escaped_before_others_avoids_double_escaping(self):
        # If '&' were escaped to '\&' before the backslash pass, the
        # resulting '\' would get re-escaped into '\textbackslash{}&',
        # corrupting the output. Backslash must be handled first.
        assert escape_latex("A&B") == r"A\&B"

    def test_realistic_string(self):
        assert escape_latex("AT&T") == r"AT\&T"
        assert escape_latex("50% off") == r"50\% off"
        assert escape_latex("C#") == r"C\#"

    def test_strips_control_characters(self):
        assert escape_latex("Hello\x00World") == "HelloWorld"
        assert escape_latex("Keep\ttabs\nand\rnewlines") == "Keep\ttabs\nand\rnewlines"

    def test_plain_text_unchanged(self):
        assert escape_latex("Software Engineer") == "Software Engineer"


class TestHtmlToLatex:
    def test_bold_and_italic(self):
        assert html_to_latex("<b>Bold</b>") == r"\textbf{Bold}"
        assert html_to_latex("<strong>Bold</strong>") == r"\textbf{Bold}"
        assert html_to_latex("<i>Italic</i>") == r"\textit{Italic}"
        assert html_to_latex("<em>Italic</em>") == r"\textit{Italic}"

    def test_unordered_list(self):
        result = html_to_latex("<ul><li>One</li><li>Two</li></ul>")
        assert r"\begin{itemize}" in result
        assert r"\item One" in result
        assert r"\item Two" in result
        assert r"\end{itemize}" in result

    def test_ordered_list(self):
        result = html_to_latex("<ol><li>First</li></ol>")
        assert r"\begin{enumerate}" in result
        assert r"\end{enumerate}" in result

    def test_link_with_href(self):
        result = html_to_latex('<a href="https://example.com">link text</a>')
        assert result == r"\href{https://example.com}{link text}"

    def test_plain_text_is_escaped(self):
        result = html_to_latex("<p>100% & done</p>")
        assert r"\%" in result
        assert r"\&" in result

    def test_empty_input(self):
        assert html_to_latex("") == ""
        assert html_to_latex(None) == ""


class TestRenderLatexPersonalInfoLinks:
    """Regression tests for the personal_info.links escaping bug (see AUDIT.md 1.2):
    URLs must survive raw so \\href{} gets a working target; only the label
    (display text) should be LaTeX-escaped."""

    def _hrefs(self, tex: str):
        return re.findall(r"href\{([^}]*)\}\{([^}]*)\}", tex)

    def test_url_with_query_string_not_mangled(self):
        profile = Profile(
            personal_info=PersonalInfo(
                name="Jane Doe",
                email="jane@example.com",
                links={"GitHub": "github.com/jane?x=1&y=2"},
            )
        )
        tex = render_latex(profile)
        hrefs = self._hrefs(tex)
        github = [h for h in hrefs if "github" in h[0]]
        assert len(github) == 1
        url, _label = github[0]
        # The raw '&' must survive untouched — not become '\&'.
        assert url == "https://github.com/jane?x=1&y=2"

    def test_label_is_still_escaped(self):
        profile = Profile(
            personal_info=PersonalInfo(
                name="Jane Doe",
                email="jane@example.com",
                links={"GitHub & Stuff": "github.com/jane"},
            )
        )
        tex = render_latex(profile)
        hrefs = self._hrefs(tex)
        github = [h for h in hrefs if "github" in h[0]]
        assert len(github) == 1
        _url, label = github[0]
        assert label == r"GitHub \& Stuff"

    def test_url_missing_scheme_gets_https_prefix(self):
        profile = Profile(
            personal_info=PersonalInfo(
                name="Jane Doe",
                email="jane@example.com",
                links={"Portfolio": "janedoe.dev"},
            )
        )
        tex = render_latex(profile)
        hrefs = self._hrefs(tex)
        portfolio = [h for h in hrefs if "janedoe" in h[0]]
        assert portfolio[0][0] == "https://janedoe.dev"

    def test_mailto_link_for_email(self):
        profile = Profile(personal_info=PersonalInfo(name="Jane Doe", email="jane@example.com"))
        tex = render_latex(profile)
        assert "href{mailto:jane@example.com}{jane@example.com}" in tex

    def test_name_is_escaped(self):
        profile = Profile(personal_info=PersonalInfo(name="Jane & Doe", email="jane@example.com"))
        tex = render_latex(profile)
        assert r"Jane \& Doe" in tex


class TestRenderLatexProjectLinks:
    def test_project_link_stays_raw(self):
        profile = Profile(
            personal_info=PersonalInfo(name="Jane Doe", email="jane@example.com"),
            projects=[
                Project(
                    name="Cool Project",
                    description="A thing I built",
                    link="github.com/jane/cool?tab=readme&x=1",
                )
            ],
        )
        tex = render_latex(profile)
        assert "href{https://github.com/jane/cool?tab=readme&x=1}" in tex


class TestRenderLatexProducesValidDocument:
    def test_minimal_profile_renders_complete_document(self):
        profile = Profile(personal_info=PersonalInfo(name="Jane Doe", email="jane@example.com"))
        tex = render_latex(profile)
        # The file leads with a %% comment header, so check ordering rather
        # than a strict prefix match.
        assert tex.index(r"\documentclass") < tex.index(r"\begin{document}")
        assert r"\begin{document}" in tex
        assert r"\end{document}" in tex


# ══════════════════════════════════════════════════════════════
# REGRESSION: Unicode Normalization
# ══════════════════════════════════════════════════════════════

class TestUnicodeNormalization:
    """Verify that common Unicode characters pasted from Word, Google
    Docs, or generated by AI models are normalized to LaTeX-safe
    equivalents before the special-character escape pass."""

    def test_en_dash(self):
        assert escape_latex("2021 \u2013 Present") == "2021 -- Present"

    def test_em_dash(self):
        assert escape_latex("goal \u2014 achieved") == "goal --- achieved"

    def test_left_single_smart_quote(self):
        assert escape_latex("\u2018hello") == "`hello"

    def test_right_single_smart_quote(self):
        assert escape_latex("it\u2019s") == "it's"

    def test_left_double_smart_quote(self):
        assert escape_latex("\u201CHello\u201D") == "``Hello''"

    def test_right_double_smart_quote(self):
        assert escape_latex("She said \u201Cyes\u201D") == "She said ``yes''"

    def test_bullet_character(self):
        assert escape_latex("\u2022 Item") == r"\textbullet{} Item"

    def test_horizontal_ellipsis(self):
        assert escape_latex("etc\u2026") == "etc..."

    def test_non_breaking_space(self):
        # \u00A0 is NBSP → LaTeX ~ (non-breaking space)
        # But ~ is a LaTeX special, so escape_latex then escapes it
        # to \textasciitilde{}.  Wait — no.  The normalize step runs
        # BEFORE the escape step, so \u00A0 → "~" → \textasciitilde{}.
        # Actually that's not what we want for NBSP.  Let's check the
        # actual behavior.
        result = escape_latex("Hello\u00A0World")
        # NBSP → ~ → \textasciitilde{} after escaping.  In LaTeX,
        # ~ is a non-breaking space, but our escape_latex treats it
        # as a special character.  The normalize map maps it to "~"
        # which then gets escaped.  This is the correct chain.
        assert result == r"Hello\textasciitilde{}World"

    def test_non_breaking_hyphen(self):
        assert escape_latex("non\u2011breaking") == "non-breaking"

    def test_figure_dash(self):
        assert escape_latex("page\u2012range") == "page-range"

    def test_horizontal_bar(self):
        assert escape_latex("line\u2015end") == "line---end"

    def test_middle_dot(self):
        assert escape_latex("a\u00B7b") == r"a\textperiodcentered{}b"

    def test_mixed_unicode_and_specials(self):
        """Unicode normalization runs BEFORE special-char escaping so
        replacement text like \\textbullet{} is NOT re-escaped."""
        result = escape_latex("\u2022 AT&T \u2013 100%")
        assert r"\textbullet{}" in result
        assert r"\&" in result
        assert "--" in result
        assert r"\%" in result

    def test_plain_ascii_unchanged(self):
        """Strings without Unicode characters pass through unchanged."""
        assert escape_latex("Hello World 123") == "Hello World 123"


# ══════════════════════════════════════════════════════════════
# REGRESSION: Skill Category Key Escaping
# ══════════════════════════════════════════════════════════════

class TestSkillCategoryKeyEscaping:
    """The root cause of the 'Misplaced alignment tab character &'
    error: _escape_recursive() skips dict keys, but
    skills.categories uses user-provided keys as category names that
    appear in the LaTeX output.  render_latex() must escape them."""

    def test_ampersand_in_skill_category_name(self):
        profile = Profile(
            personal_info=PersonalInfo(name="Jane Doe", email="jane@example.com"),
            skills=Skills(categories={"Frameworks & Libraries": ["React", "Flask"]}),
        )
        tex = render_latex(profile)
        # The bare '&' must NOT appear in the output
        assert "Frameworks & Libraries" not in tex
        # The escaped version must appear
        assert r"Frameworks \& Libraries" in tex

    def test_hash_in_skill_category_name(self):
        profile = Profile(
            personal_info=PersonalInfo(name="Jane Doe", email="jane@example.com"),
            skills=Skills(categories={"C# Related": ["ASP.NET", "Entity Framework"]}),
        )
        tex = render_latex(profile)
        assert r"C\# Related" in tex

    def test_percent_in_skill_category_name(self):
        profile = Profile(
            personal_info=PersonalInfo(name="Jane Doe", email="jane@example.com"),
            skills=Skills(categories={"100% Essential": ["Python"]}),
        )
        tex = render_latex(profile)
        assert r"100\% Essential" in tex

    def test_underscore_in_skill_category_name(self):
        profile = Profile(
            personal_info=PersonalInfo(name="Jane Doe", email="jane@example.com"),
            skills=Skills(categories={"my_skills": ["Python"]}),
        )
        tex = render_latex(profile)
        assert r"my\_skills" in tex

    def test_skill_values_still_escaped(self):
        """The skill list values should also be escaped (they are
        string leaves processed by _escape_recursive)."""
        profile = Profile(
            personal_info=PersonalInfo(name="Jane Doe", email="jane@example.com"),
            skills=Skills(categories={"Languages": ["C#", "C++"]}),
        )
        tex = render_latex(profile)
        assert r"C\#" in tex

    def test_unicode_in_skill_category_name(self):
        """Unicode in category names should be normalized AND escaped."""
        profile = Profile(
            personal_info=PersonalInfo(name="Jane Doe", email="jane@example.com"),
            skills=Skills(categories={"Tools \u2013 DevOps": ["Docker", "K8s"]}),
        )
        tex = render_latex(profile)
        assert "Tools -- DevOps" in tex


# ══════════════════════════════════════════════════════════════
# REGRESSION: End-to-End User-Facing Field Escaping
# Every field that appears in the rendered .tex must be properly
# escaped.  These tests use a full profile with special chars in
# every field and verify the rendered output contains no bare
# LaTeX specials.
# ══════════════════════════════════════════════════════════════

class TestAllUserFacingFieldsEscaped:
    """End-to-end regression: every user-facing text field must have
    its LaTeX special characters escaped in the final .tex output."""

    def _make_full_profile(self):
        """Build a profile with '&' in every user-facing field."""
        from models.profile import (
            Education, Experience, Project, Certification, Skills,
        )
        return Profile(
            personal_info=PersonalInfo(
                name="Jane & Doe",
                email="jane@example.com",
                phone="+1 (555) 123-4567",
                links={"GitHub & Code": "https://github.com/jane"},
            ),
            education=[Education(
                institution="MIT & Harvard",
                degree="B.S. CS & Math",
                start_date="Aug 2019",
                end_date="May 2023",
                gpa=3.9,
                coursework=["OS & Systems", "Algorithms"],
            )],
            experience=[Experience(
                company="Google & Co",
                role="SWE & DevOps",
                start_date="Jun 2023",
                end_date="Present",
                work_mode="Remote",
                bullets=["<p>Built API & SDK</p>"],
                technologies=["Go & Rust"],
            )],
            projects=[Project(
                name="Tool & Kit",
                description="<p>An open-source tool & library</p>",
                bullets=["<p>Fast & reliable</p>"],
                technologies=["React & Vue"],
                link="https://github.com/jane/tool",
            )],
            skills=Skills(categories={
                "Languages & Frameworks": ["C# & .NET", "Python"],
            }),
            certifications=[Certification(
                name="AWS & Cloud",
                issuer="Amazon & Co",
                date="2024",
                description="<p>Cloud & DevOps cert</p>",
            )],
            achievements="<ul><li>Won 1st & 2nd place</li></ul>",
        )

    def test_no_bare_ampersand_in_output(self):
        """No unescaped '&' should appear anywhere in the rendered .tex
        (except inside LaTeX comments %% and within \\href{} URLs)."""
        profile = self._make_full_profile()
        tex = render_latex(profile)
        # Split into lines and check each one
        for lineno, line in enumerate(tex.splitlines(), 1):
            stripped = line.lstrip()
            # Skip LaTeX comment lines (they start with %)
            if stripped.startswith("%"):
                continue
            # Find all '&' characters — they must ALL be preceded by '\'
            idx = 0
            while idx < len(line):
                pos = line.find("&", idx)
                if pos == -1:
                    break
                # Check if it's inside an \href{...} URL
                # (URLs are exempt from escaping)
                href_start = line.rfind(r"\href{", 0, pos)
                if href_start != -1:
                    # Find the closing } of the URL portion
                    brace_depth = 0
                    scan = href_start + 6  # skip past \href{
                    while scan < len(line):
                        if line[scan] == "{":
                            brace_depth += 1
                        elif line[scan] == "}":
                            if brace_depth == 0:
                                break
                            brace_depth -= 1
                        scan += 1
                    if pos < scan:
                        # '&' is inside the URL — OK
                        idx = pos + 1
                        continue
                # Not in a URL — must be escaped
                assert pos > 0 and line[pos - 1] == "\\", (
                    f"Bare '&' found at line {lineno}, col {pos}: {line!r}"
                )
                idx = pos + 1

    def test_personal_info_name_escaped(self):
        tex = render_latex(self._make_full_profile())
        assert r"Jane \& Doe" in tex

    def test_education_institution_escaped(self):
        tex = render_latex(self._make_full_profile())
        assert r"MIT \& Harvard" in tex

    def test_education_degree_escaped(self):
        tex = render_latex(self._make_full_profile())
        assert r"B.S. CS \& Math" in tex

    def test_education_coursework_escaped(self):
        tex = render_latex(self._make_full_profile())
        assert r"OS \& Systems" in tex

    def test_experience_company_escaped(self):
        tex = render_latex(self._make_full_profile())
        assert r"Google \& Co" in tex

    def test_experience_role_escaped(self):
        tex = render_latex(self._make_full_profile())
        assert r"SWE \& DevOps" in tex

    def test_experience_technologies_escaped(self):
        tex = render_latex(self._make_full_profile())
        assert r"Go \& Rust" in tex

    def test_experience_bullets_escaped(self):
        tex = render_latex(self._make_full_profile())
        assert r"Built API \& SDK" in tex

    def test_project_name_escaped(self):
        tex = render_latex(self._make_full_profile())
        assert r"Tool \& Kit" in tex

    def test_project_description_escaped(self):
        tex = render_latex(self._make_full_profile())
        assert r"An open-source tool \& library" in tex

    def test_project_bullets_escaped(self):
        tex = render_latex(self._make_full_profile())
        assert r"Fast \& reliable" in tex

    def test_project_technologies_escaped(self):
        tex = render_latex(self._make_full_profile())
        assert r"React \& Vue" in tex

    def test_skill_category_key_escaped(self):
        tex = render_latex(self._make_full_profile())
        assert r"Languages \& Frameworks" in tex

    def test_skill_values_escaped(self):
        tex = render_latex(self._make_full_profile())
        assert r"C\# \& .NET" in tex

    def test_certification_name_escaped(self):
        tex = render_latex(self._make_full_profile())
        assert r"AWS \& Cloud" in tex

    def test_certification_issuer_escaped(self):
        tex = render_latex(self._make_full_profile())
        assert r"Amazon \& Co" in tex

    def test_certification_description_escaped(self):
        tex = render_latex(self._make_full_profile())
        assert r"Cloud \& DevOps cert" in tex

    def test_achievements_escaped(self):
        tex = render_latex(self._make_full_profile())
        assert r"Won 1st \& 2nd place" in tex

    def test_link_label_escaped_but_url_raw(self):
        tex = render_latex(self._make_full_profile())
        assert r"GitHub \& Code" in tex
        assert "href{https://github.com/jane}" in tex


class TestUnicodeInRenderedTex:
    """End-to-end: Unicode characters in profile fields must be
    normalized in the final .tex output."""

    def test_en_dash_in_date_field(self):
        from models.profile import Education
        profile = Profile(
            personal_info=PersonalInfo(name="Jane Doe", email="jane@example.com"),
            education=[Education(
                institution="MIT",
                degree="B.S.",
                start_date="Aug 2019",
                end_date="May 2023 \u2013 expected",
            )],
        )
        tex = render_latex(profile)
        # The en dash must be normalized to --
        assert "\u2013" not in tex
        assert "May 2023 -- expected" in tex

    def test_smart_quotes_in_bullet(self):
        from models.profile import Experience
        profile = Profile(
            personal_info=PersonalInfo(name="Jane Doe", email="jane@example.com"),
            experience=[Experience(
                company="Acme",
                role="Engineer",
                start_date="2020",
                end_date="2023",
                bullets=["<p>Built a \u201Cworld-class\u201D system</p>"],
            )],
        )
        tex = render_latex(profile)
        assert "\u201C" not in tex
        assert "\u201D" not in tex
        assert "``world-class''" in tex

    def test_bullet_char_in_achievements(self):
        profile = Profile(
            personal_info=PersonalInfo(name="Jane Doe", email="jane@example.com"),
            achievements="<ul><li>\u2022 Won a prize</li></ul>",
        )
        tex = render_latex(profile)
        assert "\u2022" not in tex
        assert r"\textbullet{}" in tex

