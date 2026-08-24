"""Observability: emit INFO-level enter/exit + self-check events for
``analyze_followup`` so the self-check + retry path (commit 9c50e0d9)
is visible in production logs.

Before this commit: ``analyze_followup`` was completely silent in
the langgraph path — neither the call's entry, the LLM prompt, the
self-check decision, the retry, nor the exit was logged. Operators
could not tell whether the function had run, whether the LLM had
complied with the inline-cite directive, or whether the retry path
had been triggered.

Observed 2026-08-25 (research f8f3a63a, FPV穿越机, post-9c50e0d9):
the 5-min synthesis window produced 96 events but ZERO from
``advanced_search_system.*`` modules. We can confirm the fix is
loaded in the container (3/3 in-container probes pass) but we
cannot confirm it ran for the live research without these INFO
lines.

Fix: emit at minimum:
- [CITE-INLINE] analyze_followup_enter (with question prefix + nr_of_links)
- [CITE-INLINE] self_check (with hits count: 0 = retry path, N = pass)
- [CITE-INLINE] analyze_followup_exit (with final inline_cite count)

3 tests pin the contract:
1. test_enter_exit_logs_fire
2. test_self_check_log_includes_hit_count
3. test_no_retry_log_when_inline_cites_pass_on_first_try
"""

import io
import re

import pytest
from loguru import logger


def _make_handler(llm_responses):
    """Standard handler with mock LLM that returns supplied sequence."""
    from local_deep_research.citation_handlers.standard_citation_handler import (
        StandardCitationHandler,
    )

    mock_llm = MagicMock()
    if isinstance(llm_responses, str):
        llm_responses = [llm_responses]
    mock_llm.invoke.side_effect = [
        MagicMock(content=text) for text in llm_responses
    ]
    return StandardCitationHandler(
        llm=mock_llm,
        settings_snapshot={"general.enable_fact_checking": False},
    )


from unittest.mock import MagicMock


@pytest.fixture
def log_capture():
    buf = io.StringIO()
    sink_id = logger.add(buf, level="INFO", format="{message}")
    logger.enable("local_deep_research")
    try:
        yield buf
    finally:
        logger.disable("local_deep_research")
        logger.remove(sink_id)


def test_enter_exit_logs_fire(log_capture):
    """analyze_followup must emit an ENTER log on entry and an EXIT log
    on return, so operators can see the function ran at all."""
    handler = _make_handler([
        "Good synthesis: According to [1] the answer is 42.",
    ])
    handler.analyze_followup(
        question="what is the answer", search_results="[]",
        previous_knowledge="prev", nr_of_links=3,
    )
    text = log_capture.getvalue()
    # ENTER: question + nr_of_links for trace correlation
    assert "[CITE-INLINE] analyze_followup_enter" in text, (
        "no enter log — operators cannot tell analyze_followup ran. "
        f"Full log: {text[-2000:]!r}"
    )
    assert "what is the answer" in text
    assert "nr_of_links=3" in text
    # EXIT: final inline_cite count
    assert "[CITE-INLINE] analyze_followup_exit" in text, (
        "no exit log — operators cannot tell the function completed. "
        f"Full log: {text[-2000:]!r}"
    )
    # Exit must carry inline_cite count (so it's greppable for stats)
    assert re.search(r"inline_cites=\d+", text) is not None, (
        "exit log should carry inline_cite count for stat-greps. "
        f"Full log: {text[-2000:]!r}"
    )


def test_self_check_log_includes_hit_count(log_capture):
    """When the LLM produces a body with 0 inline [N] markers, the
    self-check must emit a log with hits=0. This is what makes the
    retry path observable in production logs."""
    handler = _make_handler([
        "Plain prose with no inline citations at all.",
        "Retry: According to [1] the answer is 42.",
    ])
    handler.analyze_followup(
        question="q", search_results="[]",
        previous_knowledge="prev", nr_of_links=0,
    )
    text = log_capture.getvalue()
    assert "[CITE-INLINE] self_check" in text, (
        "no self_check log — the retry trigger is invisible. "
        f"Full log: {text[-2000:]!r}"
    )
    # The log must record hits=0 (zero matches → retry path)
    assert "hits=0" in text, (
        "self_check log should record hits=0 so the retry path is "
        f"greppable. Full log: {text[-2000:]!r}"
    )


def test_no_retry_log_when_inline_cites_pass_on_first_try(log_capture):
    """When the LLM produces a body with inline [N] on the first call,
    the self_check must record hits>0 (so the pass path is also
    observable, not just the retry path)."""
    handler = _make_handler([
        "Good: According to [1] the answer is 42. Also [2] relevant.",
    ])
    handler.analyze_followup(
        question="q", search_results="[]",
        previous_knowledge="prev", nr_of_links=0,
    )
    text = log_capture.getvalue()
    assert "[CITE-INLINE] self_check" in text
    # Hits must be >= 1 (two markers in this case)
    assert re.search(r"hits=[1-9]\d*", text) is not None, (
        "self_check log should record hits>=1 when the LLM "
        f"complies on first try. Full log: {text[-2000:]!r}"
    )
    # No retry path triggered → no self_check_retry_failed
    assert "self_check_retry_failed" not in text
