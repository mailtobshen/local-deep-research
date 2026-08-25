"""Enforce kill ledger: per-dropped-cite diagnostic.

Observed 2026-08-25 (research c749f8f2, 偷渡): 20 hyperlink markers
(8 distinct sources) entered enforce; exit kept 3. The 17 dropped
markers left " 。" gaps in the user's report, but NOTHING in the logs
identified WHICH markers died or WHY — the entry probe's head= field
truncates at 120 chars, before any marker appears. Debugging required
guesswork (the URL-drift hypothesis took a full repro session to
falsify).

Fix: enforce emits one ``[CITE-ENFORCE] kill`` line per dropped body
hyperlink citation — cite number, its URL, and the reason (no row with
that number / URL matched no surviving row). Dropped-plain-marker
reasons stay aggregated (they're index-only).
"""

import io

import pytest
from loguru import logger


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


def test_kill_ledger_names_dropped_hyperlink_cite(log_capture):
    from local_deep_research.text_optimization.citation_formatter import (
        enforce_sources_ascending_and_drop_orphans,
    )

    md = (
        "# 报告\n\n"
        "甲 [\\[1\\]](http://dead.onion/gone) 乙 [\\[2\\]](http://live.onion/ok)。\n\n"
        "## 参考文献\n\n"
        "[2] 乙 (source nr: 2)\n   URL: http://live.onion/ok\n"
    )
    enforce_sources_ascending_and_drop_orphans(md)
    log = log_capture.getvalue()
    assert "[CITE-ENFORCE] kill" in log, (
        f"expected per-dropped-cite kill ledger, got: {log[-800:]!r}"
    )
    # the ledger must name the dead cite's url so the operator can see
    # exactly which URL form failed to match
    assert "http://dead.onion/gone" in log


def test_kill_ledger_silent_when_all_survive(log_capture):
    from local_deep_research.text_optimization.citation_formatter import (
        enforce_sources_ascending_and_drop_orphans,
    )

    md = (
        "# 报告\n\n"
        "甲 [\\[1\\]](http://live.onion/ok)。\n\n"
        "## 参考文献\n\n"
        "[1] 甲 (source nr: 1)\n   URL: http://live.onion/ok\n"
    )
    enforce_sources_ascending_and_drop_orphans(md)
    assert "[CITE-ENFORCE] kill" not in log_capture.getvalue()
