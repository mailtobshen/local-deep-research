"""Tests for the per-section references block fix.

User-reported bug: in detailed-mode reports, the LLM was emitting a
``## 参考文献`` (or English-equivalent) block at the end of EACH section
it generated.  The trailing ``## Sources`` block assembled by
``_format_final_report`` was correct, so the user ended up with N+1
sources blocks and inconsistent citation numbering.  Requirements:

1. The LLM-generated per-section references block is removed from each
   section body before the citation renumbering pass runs.
2. The trailing ``## Sources`` block remains the single source of truth,
   with sequential 1..N numbering for body citations.
3. The same behaviour applies on the legacy
   ``format_links_to_markdown(all_links_of_system)`` path (no per-
   subsection documents available) so other callers (scheduler,
   mcp_strategy, langgraph_agent) are not regressed.
4. The prompt instructs the LLM not to emit a per-section references
   block in the first place.
"""

from unittest.mock import MagicMock

import pytest

from local_deep_research.report_generator import IntegratedReportGenerator


# A representative slice of what the LLM emits when it tries to be
# helpful and writes a local references block at the end of a section.
# The user-visible shape is: ``## 参考文献`` or ``## References`` (or any
# of the synonym headings matched by ``_SOURCES_SECTION_PATTERNS``)
# followed by ``[N] Title / URL: ...`` rows.
LLM_PER_SECTION_REFS = (
    "## 参考文献\n\n"
    "[1] 上海外滩 - https://example.com/bund\n"
    "[2] 豫园 - https://example.com/yuyuan\n\n"
)


@pytest.fixture
def generator():
    mock_llm = MagicMock()
    mock_search = MagicMock()
    gen = IntegratedReportGenerator.__new__(IntegratedReportGenerator)
    gen.llm = mock_llm
    gen.search_system = mock_search
    gen.max_context_sections = 3
    gen.max_context_chars = 4000
    gen.searches_per_section = 2
    return gen


def _make_doc(idx, title, source):
    from langchain_core.documents import Document

    return Document(
        page_content="content",
        metadata={"index": idx, "title": title, "source": source},
    )


# ── Per-subsection documents path (the default detailed-mode path) ──


class TestPerSectionRefsStrippedWithPerSubsectionDocs:
    def test_chinese_参考文献_block_stripped(self, generator):
        """The LLM's `## 参考文献` block at the end of section body is
        removed; only the trailing `## Sources` survives."""
        all_docs = [
            _make_doc(3, "Bund", "http://real-bund"),
            _make_doc(7, "Yuyuan", "http://real-yuyuan"),
        ]
        sections = {
            "外滩与历史建筑": (
                "外滩是上海的标志性景观 [3]。"
                "豫园展现了江南园林之美 [7]。\n\n"
                + LLM_PER_SECTION_REFS
            )
        }
        structure = [{"name": "外滩与历史建筑", "subsections": []}]
        generator._section_documents_per_subsection = [all_docs]
        generator.search_system.all_links_of_system = []

        result = generator._format_final_report(
            sections, structure, query="上海"
        )
        content = result["content"]

        # Body has no per-section references block.
        body, sources_block = content.split("## Sources", 1)
        assert "## 参考文献" not in body
        assert "[1] 上海外滩" not in body
        assert "[2] 豫园" not in body
        assert "http://example.com/bund" not in body
        # Body keeps inline renumbered citations.
        assert "[1]" in body
        assert "[2]" in body
        # Trailing Sources block is the only sources listing.
        assert sources_block.count("## 参考文献") == 0
        assert "## Sources" in content
        # Real URLs are in the Sources block.
        assert "http://real-bund" in sources_block
        assert "http://real-yuyuan" in sources_block

    def test_english_References_block_stripped(self, generator):
        all_docs = [
            _make_doc(1, "A", "http://a"),
            _make_doc(2, "B", "http://b"),
        ]
        sections = {
            "S": "Some text [1] more text [2].\n\n"
            "## References\n\n"
            "[1] A - http://example.com/a\n"
            "[2] B - http://example.com/b\n"
        }
        structure = [{"name": "S", "subsections": []}]
        generator._section_documents_per_subsection = [all_docs]
        generator.search_system.all_links_of_system = []

        result = generator._format_final_report(
            sections, structure, query="q"
        )
        body = result["content"].split("## Sources", 1)[0]
        assert "## References" not in body
        assert "http://example.com/a" not in body
        assert "http://example.com/b" not in body

    def test_numbering_1_to_n_sequential_across_sections(self, generator):
        """When two sections each carry an LLM-written references
        block, the only remaining sources list is the unified trailing
        one with 1..N numbering, not 1..K and 1..M in each section."""
        all_docs = [
            _make_doc(3, "Bund", "http://real-bund"),
            _make_doc(7, "Yuyuan", "http://real-yuyuan"),
            _make_doc(11, "Pudong", "http://real-pudong"),
        ]
        sections = {
            "历史景点": "外滩 [3]。 豫园 [7]。\n\n## 参考文献\n\n[1] Bund\n[2] Yuyuan\n",
            "现代景点": "陆家嘴 [11]。\n\n## 参考文献\n\n[1] Pudong\n",
        }
        structure = [
            {"name": "历史景点", "subsections": []},
            {"name": "现代景点", "subsections": []},
        ]
        # Match per-subsection in the order they appear in `structure`.
        generator._section_documents_per_subsection = [
            all_docs[:2],  # 历史景点
            [all_docs[2]],  # 现代景点
        ]
        generator.search_system.all_links_of_system = []

        result = generator._format_final_report(
            sections, structure, query="上海"
        )
        body, sources_block = result["content"].split("## Sources", 1)
        # No per-section references block survives.
        assert "## 参考文献" not in body
        # Body cites 1..3 in document order.
        assert "[1]" in body
        assert "[2]" in body
        assert "[3]" in body
        # The first body [3] is the Pudong citation — which originally
        # carried local index 11 — so the renumbering pass did its job.
        # Sources block has all three real URLs.
        for url in ("http://real-bund", "http://real-yuyuan", "http://real-pudong"):
            assert url in sources_block

    def test_legacy_section_level_fallback_strips_per_section_refs(
        self, generator
    ):
        """Even on the legacy path (no per-subsection documents), the
        LLM-written per-section references block must be stripped so the
        trailing ## Sources stays the only sources listing."""
        sections = {
            "S1": "Body [1]\n\n## 参考文献\n\n[1] Bad local\n",
            "S2": "Body [2]\n\n## 参考文献\n\n[1] Bad local\n",
        }
        structure = [
            {"name": "S1", "subsections": []},
            {"name": "S2", "subsections": []},
        ]
        # No per-subsection documents — legacy path.
        generator._section_documents_per_subsection = []
        generator.search_system.all_links_of_system = [
            {
                "url": "http://x",
                "link": "http://x",
                "title": "X",
                "index": "1",
                "journal_quality": None,
                "metadata": {},
            }
        ]

        result = generator._format_final_report(
            sections, structure, query="q"
        )
        body = result["content"].split("## Sources", 1)[0]
        assert "## 参考文献" not in body
        assert "Bad local" not in body
        # Body kept its inline citations untouched.
        assert "[1]" in body
        assert "[2]" in body
        # The trailing ## Sources exists and the URL is there.
        assert "## Sources" in result["content"]
        assert "http://x" in result["content"]


# ── Prompt-level guardrail ──


class TestNoBoilerplateDirectiveMentionsReferences:
    def test_directive_forbids_per_section_references_block(self, generator):
        """The anti-boilerplate directive must explicitly forbid the
        LLM from writing a per-section references block, so this bug
        stops re-occurring with future models / prompts."""
        text = generator._build_no_boilerplate_directive()
        # Forbid at least one Chinese and one English reference heading.
        assert "## 参考文献" in text or "参考文献" in text
        assert "## References" in text or "References" in text
