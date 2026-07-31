"""Small text-cleanup helpers with no other project dependencies."""

import html
import re

_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_LI_OPEN_RE = re.compile(r"<li[^>]*>", re.IGNORECASE)
_BLOCK_CLOSE_RE = re.compile(r"</(p|div|li|ul|ol|h[1-6]|blockquote|tr)>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RUN_RE = re.compile(r"[ \t]+")
_SPACE_AROUND_NEWLINE_RE = re.compile(r"[ \t]*\n[ \t]*")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def html_to_text(content: str, limit: int | None = None) -> str:
    """Convert Flickr's HTML descriptions (groups, photos) into plain text
    an LLM can read without paragraphs and list items running together.

    Flickr descriptions come back as HTML — ``<p>``, ``<br />``, ``<ul><li>``,
    links, bold/italic. Blindly stripping every tag to a single space (the
    previous approach) collapses a numbered rules list like
    ``"1. No watermarks<br>2. One post per day"`` into one run-on line,
    which is exactly the structure an LLM most needs to parse correctly.
    This keeps block boundaries as blank lines, turns ``<li>`` into "- "
    bullets, then strips remaining markup and unescapes entities.
    """
    if not content:
        return ""
    text = _BR_RE.sub("\n", content)
    text = _LI_OPEN_RE.sub("\n- ", text)
    text = _BLOCK_CLOSE_RE.sub("\n\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = _SPACE_RUN_RE.sub(" ", text)
    text = _SPACE_AROUND_NEWLINE_RE.sub("\n", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    text = text.strip()
    if limit is not None:
        text = text[:limit].strip()
    return text
