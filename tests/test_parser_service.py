"""Tests for services/parser_service.py — list-detection heuristics."""
from services.parser_service import text_to_html, postprocess_parsed_profile
from models.profile import Profile, PersonalInfo, Certification


class TestTextToHtml:
    def test_already_html_passthrough(self):
        html = "<ul><li>Item</li></ul>"
        assert text_to_html(html) == html

    def test_single_line_returned_as_is(self):
        assert text_to_html("Just one line") == "Just one line"

    def test_bullet_list_detected(self):
        text = "- First point\n- Second point\n- Third point"
        result = text_to_html(text)
        assert result == "<ul><li>First point</li><li>Second point</li><li>Third point</li></ul>"

    def test_bullet_char_variants_detected(self):
        text = "• Alpha\n• Beta"
        result = text_to_html(text)
        assert result.startswith("<ul>")
        assert "<li>Alpha</li>" in result
        assert "<li>Beta</li>" in result

    def test_numbered_list_detected(self):
        text = "1. First\n2. Second\n3. Third"
        result = text_to_html(text)
        assert result == "<ol><li>First</li><li>Second</li><li>Third</li></ol>"

    def test_multiline_plain_text_preserves_breaks(self):
        text = "Line one\nLine two"
        result = text_to_html(text)
        assert result == "Line one<br>Line two"

    def test_minority_bullet_lines_not_treated_as_list(self):
        # Only 1 of 3 lines has a marker (< 50%) — should NOT become a <ul>.
        text = "A paragraph that happens to start with a dash somewhere\n- not really a list\nJust more prose here"
        result = text_to_html(text)
        assert "<ul>" not in result

    def test_html_special_chars_escaped_in_list_items(self):
        text = "- Built <MyComponent/> using React & TypeScript\n- Shipped it"
        result = text_to_html(text)
        assert "&lt;MyComponent/&gt;" in result
        assert "&amp;" in result

    def test_empty_input(self):
        assert text_to_html("") == ""
        assert text_to_html("   ") == "   "


class TestPostprocessParsedProfile:
    def test_achievements_converted_to_html_list(self):
        profile = Profile(
            personal_info=PersonalInfo(name="Jane", email="jane@example.com"),
            achievements="- Won a hackathon\n- Published a paper",
        )
        postprocess_parsed_profile(profile)
        assert profile.achievements == "<ul><li>Won a hackathon</li><li>Published a paper</li></ul>"

    def test_certification_description_converted(self):
        profile = Profile(
            personal_info=PersonalInfo(name="Jane", email="jane@example.com"),
            certifications=[
                Certification(
                    name="AWS SAA",
                    issuer="AWS",
                    date="2024",
                    description="1. Passed with 900/1000\n2. Renewed in 2025",
                )
            ],
        )
        postprocess_parsed_profile(profile)
        assert profile.certifications[0].description == (
            "<ol><li>Passed with 900/1000</li><li>Renewed in 2025</li></ol>"
        )

    def test_single_line_achievement_untouched(self):
        profile = Profile(
            personal_info=PersonalInfo(name="Jane", email="jane@example.com"),
            achievements="Dean's List, Fall 2023",
        )
        postprocess_parsed_profile(profile)
        assert profile.achievements == "Dean's List, Fall 2023"
