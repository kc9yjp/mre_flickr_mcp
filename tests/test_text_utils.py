"""Tests for text_utils.html_to_text — converting Flickr's HTML group/photo
descriptions into plain text an LLM can read without markup or run-on lines."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from text_utils import html_to_text


class TestHtmlToText:
    def test_empty_input(self):
        assert html_to_text("") == ""
        assert html_to_text(None) == ""

    def test_plain_text_passthrough(self):
        assert html_to_text("Trees only, please.") == "Trees only, please."

    def test_br_becomes_newline(self):
        text = html_to_text("Rules:<br />1. No watermarks<br/>2. One post per day")
        assert text == "Rules:\n1. No watermarks\n2. One post per day"

    def test_paragraphs_become_blank_line_separated(self):
        text = html_to_text("<p>Trees only.</p><p>Max 1/day.</p>")
        assert text == "Trees only.\n\nMax 1/day."

    def test_list_items_become_bullets(self):
        text = html_to_text("<ul><li>Rule A</li><li>Rule B</li></ul>")
        lines = [ln for ln in text.split("\n") if ln.strip()]
        assert lines == ["- Rule A", "- Rule B"]

    def test_entities_are_unescaped(self):
        text = html_to_text("Fish &amp; wildlife &mdash; no watermarks &#39;please&#39;")
        assert "&amp;" not in text
        assert "Fish & wildlife" in text
        assert "'please'" in text

    def test_remaining_inline_tags_are_stripped(self):
        text = html_to_text("<strong>Important:</strong> <a href='x'>read the rules</a>")
        assert "<" not in text
        assert "Important:" in text
        assert "read the rules" in text

    def test_nbsp_becomes_regular_space(self):
        text = html_to_text("no&nbsp;watermarks")
        assert text == "no watermarks"

    def test_no_run_on_lines_from_blind_tag_stripping(self):
        """Regression: the previous implementation replaced every tag with a
        single space, so "<li>Rule one</li><li>Rule two</li>" became one
        space-joined line. Structure must survive."""
        text = html_to_text("<li>Rule one</li><li>Rule two</li>")
        assert "Rule one Rule two" not in text
        assert "Rule one" in text
        assert "Rule two" in text

    def test_limit_truncates(self):
        text = html_to_text("a" * 100, limit=10)
        assert text == "a" * 10

    def test_limit_applied_after_conversion_not_before(self):
        # Truncating raw HTML first could cut mid-tag and leak markup.
        text = html_to_text("<p>hello</p><p>world</p>", limit=8)
        assert "<" not in text
