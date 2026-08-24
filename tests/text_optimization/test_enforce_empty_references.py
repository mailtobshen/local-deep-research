"""Diagnostic for the empty References block case.

Observed 2026-08-24 (research 0043b4af, FPV穿越机): the
langgraph-agent emitted a `## 参考文献` block with 31 rows, all
URLs intact. The body of the report had no inline `[N]` markers.
``enforce_sources_ascending_and_drop_orphans`` parsed the 31 rows,
then dropped ALL of them as "orphan" (the body never cited any
of them). The rebuilt block was `## 参考文献\\n` — heading + zero
entries. The final report the user sees shows the heading with no
rows underneath.

The function emitted no diagnostic to inform the operator. The
behavior was: input N rows -> output 0 rows, silently.

Fix: pure observability. The orphan-row policy is unchanged
(intentionally drops uncited rows per test_uncited_rows_are_dropped),
but a new ``[CITE-ENFORCE] rebuilt_empty`` diagnostic line tells
operators WHY the block is empty. A separate "fix the LLM" prompt
tweak (or a recover-orphan-rows follow-up) is the user-facing
remedy; this commit is the observability hook so that follow-up
has a one-grep entry point.
"""

import io

import pytest
from loguru import logger


def _md_with_orphan_references() -> str:
    """Mimic the 22:14 scenario: ## 参考文献 block with 3 rows, body
    has no inline [N] markers."""
    return (
        "# FPV Drone Industry\n\n"
        "Body prose without any inline [N] markers.\n\n"
        "## 参考文献\n\n"
        "[1] 联合中文担保交易集市 (source nr: 1)\n"
        "   URL: http://example.com/one\n\n"
        "[2] 雇佣匿名黑客 (source nr: 2)\n"
        "   URL: http://example.com/two\n\n"
        "[3] bitcoin mixer (source nr: 3)\n"
        "   URL: http://example.com/three\n"
    )


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


def test_rebuilt_empty_diag_fires_when_all_rows_are_orphan(log_capture):
    """When the body has 0 inline [N] markers and every Sources row
    gets dropped as orphan, emit a [CITE-ENFORCE] rebuilt_empty
    diagnostic so the operator can grep for the failure mode. Without
    this, the report silently renders a `## 参考文献` heading with
    zero entries (research 0043b4af, 2026-08-24, 22:14)."""
    from local_deep_research.text_optimization.citation_formatter import (
        enforce_sources_ascending_and_drop_orphans,
    )
    enforce_sources_ascending_and_drop_orphans(
        _md_with_orphan_references()
    )
    log_text = log_capture.getvalue()
    assert "[CITE-ENFORCE] rebuilt_empty" in log_text, (
        "expected a diagnostic when all References rows are orphan "
        "(body has no inline [N] markers). The fix adds observability "
        "to surface the empty-block failure mode. "
        f"Full log: {log_text[-2000:]!r}"
    )
    assert "rows_in=3" in log_text
    assert "rows_kept=0" in log_text
    assert "no_body_inline_citations" in log_text


def test_rebuilt_empty_diag_does_not_fire_when_body_cites_rows(log_capture):
    """Sanity: when the body has at least one inline [N] that matches
    a Sources row, the diagnostic must NOT fire (the rebuilt block
    is populated, no operator attention needed)."""
    from local_deep_research.text_optimization.citation_formatter import (
        enforce_sources_ascending_and_drop_orphans,
    )
    md = (
        "# Title\n\n"
        "Body cites [1] and [2].\n\n"
        "## 参考文献\n\n"
        "[1] First source\n"
        "   URL: http://example.com/one\n\n"
        "[2] Second source\n"
        "   URL: http://example.com/two\n"
    )
    enforce_sources_ascending_and_drop_orphans(md)
    log_text = log_capture.getvalue()
    assert "[CITE-ENFORCE] rebuilt_empty" not in log_text


def test_orphan_row_policy_preserved(log_capture):
    """The 2026-08-23 orphan-row policy (test_uncited_rows_are_dropped
    in test_citation_formatter_edge_cases) must NOT regress. The
    fix is pure observability — uncited rows are still dropped.
    This test pins that contract for the 22:14 case shape."""
    from local_deep_research.text_optimization.citation_formatter import (
        enforce_sources_ascending_and_drop_orphans,
    )
    out = enforce_sources_ascending_and_drop_orphans(
        _md_with_orphan_references()
    )
    # Uncited rows are still dropped (no behaviour change). The
    # heading is preserved.
    assert "## 参考文献" in out
    # But the row contents are gone.
    assert "联合中文担保交易集市" not in out
    assert "雇佣匿名黑客" not in out
    assert "bitcoin mixer" not in out

