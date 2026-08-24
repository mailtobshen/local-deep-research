"""Diagnostic for status=empty research with HTML-loaded cites.

Observed 2026-08-24 (research 0043b4af, FPV穿越机): the langgraph-agent
populated num_to_url (31 cites) and url_to_html (13 of them have HTML),
but the report body had NO inline [N] markers — every cite was only
listed in the trailing References block. Result: section_to_nums was
all-empty, the per-(sidx, num) loop iterated 0 times, no CANDIDATE_SCORED
event ever fired, and the research ended with ELIGIBLE_BANK total=0 /
END status=empty with **no diagnostic** explaining why the image pipeline
produced nothing.

Fix: emit a new [IMG-TRACE] NO_SECTION_BINDING event when entering
the per-section loop with 0 (sidx, num) pairs but num_to_url and
url_to_html are both non-empty. The event carries the counts that
matter (num_to_url, url_to_html, section_to_nums_total_pairs=0,
reason). This lets a single grep tell the operator that body prose
needs inline citation markers (or the cite-to-section mapping needs a
fallback).
"""

import io
from unittest.mock import MagicMock, patch

import pytest
from loguru import logger


def _img(html_url, alt_text):
    return (
        f'<html><body><img src="http://x/{html_url}.jpg" alt="{alt_text}">'
        f"</body></html>"
    )


@pytest.fixture
def stubbed_postproc(monkeypatch):
    """Stub the sentence-transformer model so the postproc function never
    blocks on a real model load. The diagnostic we test fires before
    any model encode() call, so a no-op get_model is enough."""
    from local_deep_research.images import postprocessing

    class _M:
        def encode(self, phrases, normalize_embeddings=True):
            # Return a zero vector for every phrase — irrelevant for
            # the diagnostic path.
            import numpy as np

            return [np.zeros(4) for _ in phrases]

    monkeypatch.setattr(
        postprocessing.semantic_matcher, "get_model", lambda *a, **k: _M()
    )
    monkeypatch.setattr(
        postprocessing.semantic_matcher,
        "_canonical_section_phrase",
        lambda heading, entities, parent_heading="": heading or "",
    )
    return postprocessing


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


def _run_enhance(postproc, md, results):
    return postproc.enhance_report_with_images(
        research_id="r",
        clean_markdown=md,
        results=results,
        db_session=MagicMock(),
        enable_images=True,
        vision_model="ollama/test",
        alt_similarity_threshold=0.5,
    )


def test_diag_fires_when_cites_have_html_but_no_inline_body_markers(
    stubbed_postproc, log_capture
):
    """The reproducer for research 0043b4af: body prose without [N]
    markers + References block with HTML-loaded URLs. Expect a
    NO_SECTION_BINDING diagnostic with the right counts."""
    md = (
        "# FPV Drone Industry\n\n"
        "Body 1 has plain prose with no inline cite markers.\n\n"
        "# Section 2\n\n"
        "Body 2 also has plain prose.\n\n"
        "# References\n\n"
        "[1] news one\n   URL: http://example.com/one\n"
        "[2] news two\n   URL: http://example.com/two\n"
    )
    results = {
        "findings": [
            {
                "search_results": [
                    {
                        "url": "http://example.com/one",
                        "html_content": _img("a", "one"),
                    },
                    {
                        "url": "http://example.com/two",
                        "html_content": _img("b", "two"),
                    },
                ]
            }
        ]
    }
    _run_enhance(stubbed_postproc, md, results)
    log_text = log_capture.getvalue()
    assert "[IMG-TRACE] NO_SECTION_BINDING" in log_text, (
        "expected the new diagnostic to fire when num_to_url>0 and "
        "url_to_html>0 but section_to_nums_total_pairs=0. "
        f"Full log: {log_text[-2000:]!r}"
    )
    assert "num_to_url=2" in log_text
    assert "url_to_html=2" in log_text
    assert "section_to_nums_total_pairs=0" in log_text
    assert "no_inline_cite_markers_in_body" in log_text


def test_diag_does_not_fire_when_body_has_inline_markers(
    stubbed_postproc, log_capture
):
    """Sanity: when body sections actually carry [N] markers, the
    diagnostic must NOT fire (the pipeline is doing its job)."""
    md = (
        "# Section 1\n\n"
        "Body 1 cites darkweb source [1] and [2].\n\n"
        "# Section 2\n\n"
        "Body 2 cites [3] and [4].\n\n"
        "# References\n\n"
        "[1] news one\n   URL: http://example.com/one\n"
        "[2] news two\n   URL: http://example.com/two\n"
        "[3] news three\n   URL: http://example.com/three\n"
        "[4] news four\n   URL: http://example.com/four\n"
    )
    results = {
        "findings": [
            {
                "search_results": [
                    {
                        "url": "http://example.com/one",
                        "html_content": _img("a", "one"),
                    },
                    {
                        "url": "http://example.com/two",
                        "html_content": _img("b", "two"),
                    },
                    {
                        "url": "http://example.com/three",
                        "html_content": _img("c", "three"),
                    },
                    {
                        "url": "http://example.com/four",
                        "html_content": _img("d", "four"),
                    },
                ]
            }
        ]
    }
    _run_enhance(stubbed_postproc, md, results)
    log_text = log_capture.getvalue()
    assert "[IMG-TRACE] NO_SECTION_BINDING" not in log_text, (
        "diagnostic must NOT fire when body sections have inline [N] "
        f"markers. Full log: {log_text[-2000:]!r}"
    )


def test_diag_does_not_fire_when_no_cites_at_all(
    stubbed_postproc, log_capture
):
    """Empty research (no References block) — existing BANK_EMPTY path
    is unchanged. The new diagnostic must not double-fire."""
    md = "# Section 1\n\nBody 1 plain prose.\n"
    results = {"findings": []}
    _run_enhance(stubbed_postproc, md, results)
    log_text = log_capture.getvalue()
    assert "[IMG-TRACE] BANK_EMPTY" in log_text
    assert "[IMG-TRACE] NO_SECTION_BINDING" not in log_text
