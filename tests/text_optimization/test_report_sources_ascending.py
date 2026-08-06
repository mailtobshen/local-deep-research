"""Test that the trailing Sources block built inside
`_format_final_report` is strictly ascending 1..N in body-first-cite
order for both branches (per-subsection docs captured vs back-compat).

When the body cites a higher displayed_n before a lower one (e.g.
``[[2]](b)`` then ``[[1]](a)``), the per-subsection-docs path used
to emit the Sources block in raw insertion order, producing a
non-ascending ``[2] A / [1] B`` display — exactly the
``[6],[1],[2],[8],[4]...`` pattern the user reported. The fix
sorts by ``new_idx`` so the display reads ``[1], [2], ..., [N]``
strictly ascending in every case.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from langchain_core.documents import Document

# The Sources heading is locale-driven; default report.language=zh-CN
# emits `## 参考文献`, English users get `## Sources`. Either is a
# valid marker — tests below resolve whichever appears in the output.
_SOURCES_HEADINGS = ("## Sources", "## 参考文献")


def _make_generator():
    from local_deep_research.report_generator import IntegratedReportGenerator

    system = MagicMock()
    system.strategy = MagicMock()
    system.strategy.settings_snapshot = {"search.iterations": 1}
    system.strategy.max_iterations = 1
    system.all_links_of_system = []
    model = MagicMock()
    return IntegratedReportGenerator(llm=model, search_system=system)


def _basic_structure():
    return [
        {
            "name": "示例章节",
            "subsections": [
                {"name": "示例子章节", "purpose": "示例目的"},
            ],
        }
    ]


def _sources_tail(content: str) -> str:
    """Return the substring starting at the Sources heading."""
    for marker in _SOURCES_HEADINGS:
        if marker in content:
            return content[content.index(marker) :]
    raise AssertionError(
        f"no Sources heading found in content (searched {_SOURCES_HEADINGS})"
    )


class TestReportSourcesAscending:
    """The trailing Sources block built by `_format_final_report` must
    display bracket numbers strictly ``[1],[2],...,[N]`` ascending in
    body-first-cite order, regardless of how the upstream
    ``all_links_of_system`` ordered the URLs (langgraph's original
    collector produced ``[6],[1],[2],[8],[4]...`` for typical
    multi-iteration reports).
    """

    def test_sources_block_is_strictly_ascending_zh(self):
        """Back-compat (no per-subsection docs captured) path rebuilds
        a strictly ascending Sources block in body-first-cite order."""
        gen = _make_generator()
        # Simulate a langgraph-style ``all_links_of_system`` whose
        # displayed_n is in the user's reported buggy order.
        gen.search_system.all_links_of_system = [
            # indexes 1..N matched to displayed_n [6,1,2,8,4] below.
            {"title": "F", "link": "https://f.example", "url": "https://f.example"},
            {"title": "A", "link": "https://a.example", "url": "https://a.example"},
            {"title": "B", "link": "https://b.example", "url": "https://b.example"},
            {"title": "H", "link": "https://h.example", "url": "https://h.example"},
            {"title": "D", "link": "https://d.example", "url": "https://d.example"},
        ]
        sections = {
            "示例章节": (
                "# 1. 示例章节\n\n"
                "## 1.1 示例子章节\n\n"
                "见 [[3]](https://c.example) 与 [[1]](https://a.example) "
                "还有 [[2]](https://b.example)。\n"
            ),
        }
        report = gen._format_final_report(sections, _basic_structure(), "示例")
        content = report["content"]
        tail = _sources_tail(content)
        nums = [int(x) for x in re.findall(r"^\[(\d+)\]", tail, re.MULTILINE)]
        assert nums == list(range(1, len(nums) + 1))

    def test_sources_block_is_strictly_ascending_when_docs_captured(self):
        """Per-subsection-docs-captured path also produces ascending
        Sources — including the bug-triggering case where the body
        cites [[2]] BEFORE [[1]] (so old_to_new maps 1↔2
        differently) and the per-subsection-doc insertion order
        would otherwise leak into the displayed block."""
        gen = _make_generator()
        # Force the per-subsection docs branch by setting the cached
        # attribute directly. Sections are ordered A then B; the body
        # cites B before A so the renumber must produce [[1]]=B,
        # [[2]]=A but the Sources display must also be [1],[2] (not
        # [2],[1] in raw insertion order).
        gen._section_documents_per_subsection = [
            [
                Document(
                    page_content="x",
                    metadata={
                        "index": 1,
                        "title": "A",
                        "source": "https://a.example",
                    },
                )
            ],
            [
                Document(
                    page_content="y",
                    metadata={
                        "index": 2,
                        "title": "B",
                        "source": "https://b.example",
                    },
                )
            ],
        ]
        sections = {
            "示例章节": (
                "# 1. 示例章节\n\n"
                "## 1.1 示例子章节\n\n"
                "见 [[2]](https://b.example) 与 [[1]](https://a.example)。\n"
            ),
        }
        report = gen._format_final_report(sections, _basic_structure(), "示例")
        content = report["content"]
        tail = _sources_tail(content)
        nums = [int(x) for x in re.findall(r"^\[(\d+)\]", tail, re.MULTILINE)]
        assert nums == [1, 2]
