"""Tests for the strip_per_section_sources_block pattern matching
loose CJK / English heading variants (R4).

The original pattern matched only exact heading text like
``## 参考文献`` or ``## Sources``. qwen3.5-opus:9b on Aug 5 invented
``## 参考文献标注说明`` / ``## Sources Notes`` suffixes — those
escaped the strip, leaving per-section reference blocks in the
final report.

Pattern fix: allow an optional short suffix of CJK/Latin chars
(bounded to 12) after the keyword.
"""

from local_deep_research.text_optimization.citation_formatter import (
    strip_per_section_sources_block,
)


class TestStripLooseCJKHeading:
    """Heading variants the LLM actually produced."""

    def test_exact_参考文献(self):
        text = "Body\n\n## 参考文献\n[1] x\n\nEnd."
        out = strip_per_section_sources_block(text)
        assert "## 参考文献" not in out
        assert "[1]" not in out

    def test_参考文献标注说明(self):
        text = "Body\n\n## 参考文献标注说明\n[1] x\n\nEnd."
        out = strip_per_section_sources_block(text)
        assert "## 参考文献标注说明" not in out
        assert "[1]" not in out

    def test_参考资料_列表_with_space(self):
        text = "Body\n\n## 参考资料 列表\n[1] x\n\nEnd."
        out = strip_per_section_sources_block(text)
        assert "## 参考资料 列表" not in out

    def test_参考资料_Notes_mixed(self):
        text = "Body\n\n## 参考资料 Notes\n[1] x"
        out = strip_per_section_sources_block(text)
        assert "## 参考资料 Notes" not in out

    def test_参考文献_full_width_space_then_suffix(self):
        """Full-width space (　) between keyword and suffix."""
        text = "Body\n\n## 参考文献　标注\n[1] x"
        out = strip_per_section_sources_block(text)
        assert "## 参考文献" not in out

    def test_returns_unchanged_when_no_heading(self):
        text = "Body\n\nNo heading here at all"
        out = strip_per_section_sources_block(text)
        assert out == text


class TestStripLooseEnglishHeading:

    def test_exact_Sources(self):
        text = "Body\n\n## Sources\n[1] x"
        out = strip_per_section_sources_block(text)
        assert "## Sources" not in out

    def test_Sources_Notes(self):
        text = "Body\n\n## Sources Notes\n[1] x"
        out = strip_per_section_sources_block(text)
        assert "## Sources Notes" not in out

    def test_References_List(self):
        text = "Body\n\n## References List\n[1] x"
        out = strip_per_section_sources_block(text)
        assert "## References List" not in out

    def test_Bibliography(self):
        text = "Body\n\n# Bibliography\n[1] x"
        out = strip_per_section_sources_block(text)
        assert "## Bibliography" not in out or "# Bibliography" not in out

    def test_Citations_Heading(self):
        text = "Body\n\n### Citations\n[1] x"
        out = strip_per_section_sources_block(text)
        assert "Citations" not in out


class TestStripPreservesContentBeforeHeading:
    """The strip must keep everything BEFORE the heading intact."""

    def test_keeps_body_paragraphs(self):
        body = "Foo [1] bar\n\nMore content with [2] baz\n\n## Sources Notes\n[1] x"
        out = strip_per_section_sources_block(body)
        assert "Foo [1] bar" in out
        assert "More content with [2] baz" in out
        assert "[1] x" not in out

    def test_preserves_citation_markers_in_body(self):
        """Body [[N]](url) markers must NOT be touched by strip."""
        body = "Foo [[1]](https://a) bar\n\n## 参考文献标注说明\n[1] x"
        out = strip_per_section_sources_block(body)
        assert "[[1]](https://a)" in out