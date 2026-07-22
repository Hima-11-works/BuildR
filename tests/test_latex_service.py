"""Tests for services/latex_service.py — LaTeX escaping and rendering."""
import re

from models.profile import Profile, PersonalInfo, Project
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
