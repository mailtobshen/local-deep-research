"""Citation-diversity directive + self-check.

Observed 2026-08-25 (research 610f5486, FPV穿越机): the LLM emitted 43
inline markers but they referenced only TWO distinct sources ([2] and
[3] — 21 sections all citing ['2'] or ['3']). The image pipeline is
citation-anchored, so every section read html_covered=0 (neither cited
page yielded images) and the report rendered with zero images, while
the deferred fill had spent 8.4 min scraping 31 URLs whose material
could never be placed.

Fix: (1) the analyze_followup prompt demands citing at least
``_MIN_DISTINCT_SOURCES`` (3) different sources when that many exist;
(2) the existing self-check is extended to count DISTINCT cited
numbers — a body with ≥3 markers but 1-2 distinct sources triggers the
same single corrective retry as the zero-marker case.
"""

from unittest.mock import MagicMock, patch

from local_deep_research.citation_handlers.standard_citation_handler import (
    StandardCitationHandler,
    _count_distinct_cited_sources,
)

_MIN = 3


def _make_handler(llm_responses):
    mock_llm = MagicMock()
    if isinstance(llm_responses, str):
        llm_responses = [llm_responses]
    mock_llm.invoke.side_effect = [
        MagicMock(content=text) for text in llm_responses
    ]
    return StandardCitationHandler(
        llm=mock_llm,
        settings_snapshot={
            "general.output_instructions": "",
            "general.enable_fact_checking": False,
        },
    )


class TestCountDistinct:
    def test_repeated_single_source_counts_one(self):
        body = "Claim [2]. Another [2]. Third [2]. More [2]."
        assert _count_distinct_cited_sources(body) == 1

    def test_mixed_markers(self):
        body = "A [2] b [2, 5] c [3] d [2](http://x.onion/) e [[7]](http://y.onion/)"
        assert _count_distinct_cited_sources(body) == 4

    def test_no_citations_zero(self):
        assert _count_distinct_cited_sources("plain prose") == 0


class TestDiversityRetry:
    def test_low_diversity_triggers_retry(self):
        """610f5486 shape: many markers, 2 distinct sources → one
        corrective retry; the more-diverse retry wins."""
        handler = _make_handler([
            "A [2]. B [2]. C [3]. D [2]. E [3]. F [2]. G [3].",
            "A [1]. B [2]. C [3]. D [1]. E [2]. F [3].",
        ])
        out = handler.analyze_followup(
            "q", [{"title": "t", "url": f"http://s{i}.onion/", "snippet": "x"} for i in range(5)],
            previous_knowledge="pk", nr_of_links=0,
        )
        assert "[1]" in out["content"] and "[3]" in out["content"]
        assert handler.llm.invoke.call_count == 2

    def test_diverse_body_no_retry(self):
        handler = _make_handler("A [1]. B [2]. C [3]. D [4].")
        handler.analyze_followup(
            "q", [{"title": "t", "url": f"http://s{i}.onion/", "snippet": "x"} for i in range(5)],
            previous_knowledge="pk", nr_of_links=0,
        )
        assert handler.llm.invoke.call_count == 1

    def test_retry_capped_at_one(self):
        handler = _make_handler([
            "A [2]. B [2]. C [2].",
            "A [2]. B [2]. C [2].",
        ])
        out = handler.analyze_followup(
            "q", [{"title": "t", "url": f"http://s{i}.onion/", "snippet": "x"} for i in range(5)],
            previous_knowledge="pk", nr_of_links=0,
        )
        assert handler.llm.invoke.call_count == 2  # no third call

    def test_small_source_pool_no_diversity_requirement(self):
        """When fewer than _MIN distinct sources exist, the diversity
        gate must not fire (can't cite what doesn't exist)."""
        handler = _make_handler("A [1]. B [1]. C [1].")
        handler.analyze_followup(
            "q", [{"title": "t", "url": "http://only.onion/", "snippet": "x"}],
            previous_knowledge="pk", nr_of_links=0,
        )
        assert handler.llm.invoke.call_count == 1

    def test_prompt_carries_diversity_directive(self):
        handler = _make_handler("A [1]. B [2]. C [3].")
        handler.analyze_followup(
            "q", [{"title": "t", "url": "http://s.onion/", "snippet": "x"}],
            previous_knowledge="pk", nr_of_links=0,
        )
        prompt = handler.llm.invoke.call_args[0][0]
        assert "different sources" in prompt.lower() or "不同的来源" in prompt
