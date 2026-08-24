"""Self-check + retry: when the LLM produces a synthesis with zero
inline [N] markers in the body, retry once with an explicit reminder.

Observed 2026-08-24 (research 0043b4af, FPV穿越机): the
langgraph-agent's prompt instructs the LLM to put inline `[N]`
markers in the body, but the LLM ignored the instruction and put
all citations in the trailing `## 参考文献` block. The downstream
``enforce_sources_ascending_and_drop_orphans`` then dropped every
row (because body had no inline cites), leaving an empty block.

Fix: in ``analyze_followup``, after the LLM produces its synthesis,
detect when the body has zero inline [N] markers and retry once
with a corrective prompt. Capped at 1 retry to avoid runaway cost
on a stubborn LLM. If the retry still has zero markers, emit a
diagnostic so the operator can see WHY.

3 tests pin the contract:
1. test_inline_cite_retry_when_body_has_no_citations — first call
   returns no [N], second call (after retry prompt) has [N] → result
   is the retried synthesis.
2. test_inline_cite_no_retry_when_body_has_citations — first call
   has [N] markers → no second call.
3. test_inline_cite_retry_capped_at_one — first call no [N], second
   call also no [N] → no third call, result is the empty second
   call's output, diagnostic emitted.
"""

from unittest.mock import MagicMock


def _make_handler(llm_responses):
    """Build a StandardCitationHandler with a mock LLM that returns
    the supplied sequence of contents, in order, on .invoke()."""
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
        settings_snapshot={
            "general.output_instructions": "",
            "general.enable_fact_checking": False,
        },
    )


def test_inline_cite_retry_when_body_has_no_citations():
    """Reproducer for 22:14: first LLM call returns a body with zero
    inline [N] markers. The fix should issue ONE corrective retry
    that does emit [N] markers. The retried synthesis is what gets
    returned to the caller (analyze_followup returns the fixed body,
    not the broken one)."""
    handler = _make_handler([
        "Plain prose body with zero inline citations. Just text.",
        "Fixed synthesis: According to [1] the answer is 42. "
        "Per [2] also relevant.",
    ])
    result = handler.analyze_followup(
        question="test",
        search_results="[]",
        previous_knowledge="prev",
        nr_of_links=0,
    )
    # The second (retried) LLM call's output is what we return.
    assert "According to [1]" in result["content"]
    assert "Per [2]" in result["content"]
    # 2 LLM calls total: original + retry
    assert handler.llm.invoke.call_count == 2
    # The retry prompt should mention the self-check failure
    retry_prompt = handler.llm.invoke.call_args_list[1][0][0]
    assert (
        "inline" in retry_prompt.lower()
        or "[N]" in retry_prompt
        or "citation" in retry_prompt.lower()
    ), (
        "the retry prompt should remind the LLM about inline "
        f"citation markers. Got: {retry_prompt[:500]!r}"
    )


def test_inline_cite_no_retry_when_body_has_citations():
    """Happy path: first call already has inline [N] markers → no
    retry issued. The original synthesis is returned unchanged."""
    handler = _make_handler([
        "Good synthesis: According to [1] the answer is 42.",
    ])
    result = handler.analyze_followup(
        question="test",
        search_results="[]",
        previous_knowledge="prev",
        nr_of_links=0,
    )
    assert "According to [1]" in result["content"]
    assert handler.llm.invoke.call_count == 1


def test_inline_cite_retry_capped_at_one():
    """Cap: if the retry also produces a body with no inline [N]
    markers, do NOT issue a third call. Return whatever the retry
    produced (likely empty) and emit a diagnostic so the operator
    can see the failure."""
    handler = _make_handler([
        "Broken body one: no inline citations at all.",
        "Still broken retry: also no inline citations.",
    ])
    result = handler.analyze_followup(
        question="test",
        search_results="[]",
        previous_knowledge="prev",
        nr_of_links=0,
    )
    # Exactly 2 LLM calls (original + 1 retry). No third call.
    assert handler.llm.invoke.call_count == 2
    # The returned content is whatever the last call produced.
    assert "Still broken retry" in result["content"]
