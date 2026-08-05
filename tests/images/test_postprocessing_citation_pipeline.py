"""End-to-end test of the citation-anchored image pipeline.

Uses a fake HF model so no download/network is required. The fake
returns a hand-picked vector per phrase; we arrange vectors so the
cited image's alt is highly similar to its section phrase and an
unrelated image is dissimilar.
"""
from unittest.mock import MagicMock, patch

from local_deep_research.images import postprocessing


def _fake_model(vectors: dict[str, list[float]]):
    """Return a fake model whose encode(phrase) -> vectors[phrase]."""

    import numpy as np

    class _M:
        def encode(self, phrases, normalize_embeddings=True):
            return [np.array(vectors.get(p, [0.0, 0.0, 0.0, 0.0])) for p in phrases]

    return _M()


def test_cited_image_passes_gate_and_is_inserted(monkeypatch):
    """An image whose alt matches its citation's section is inserted there."""
    md = (
        "## Canton Tower\n\nThe tower [1] is tall.\n\n"
        "## 参考文献\n\n"
        "[1] Canton Tower source\n   URL: https://src/page\n"
    )
    results = {"findings": [{"search_results": [
        {"url": "https://src/page", "html_content": (
            '[{"url": "https://img/tower.jpg", "alt": "Canton Tower", '
            '"source_url": "https://src/page", "source_title": "ct", '
            '"width": null, "height": null}]'
        )},
    ]}]}

    fake = _fake_model({
        "Canton Tower": [1.0, 0.0, 0.0, 0.0],   # alt
        # section phrase = heading + entities; arrange same direction.
    })
    monkeypatch.setattr(postprocessing.semantic_matcher, "get_model", lambda *a, **k: fake)
    # build_report_entity_pool + _canonical_section_phrase run on the
    # real markdown; make the section phrase embed to the same vector
    # by having encode fall through to the default for unknown phrases
    # and patching _canonical_section_phrase to return "Canton Tower".
    monkeypatch.setattr(
        postprocessing.semantic_matcher, "_canonical_section_phrase",
        lambda heading, entities: "Canton Tower",
    )

    with patch.object(postprocessing, "ImageStore") as store_mock:
        store_mock.return_value.persist.return_value = (
            {"https://img/tower.jpg": "/images/r/t.jpg"}
        )
        store_mock.return_value.rewrite_markdown.side_effect = (
            lambda md, mapping, **kw: md
        )
        out = postprocessing.enhance_report_with_images(
            research_id="r",
            clean_markdown=md,
            results=results,
            db_session=MagicMock(),
            enable_images=True,
            vision_model="",
        )
    # The cited image is inserted into the Canton Tower section.
    assert "![Canton Tower](https://img/tower.jpg)" in out


def test_uncited_source_image_not_extracted(monkeypatch):
    """An image whose source is never cited in the body is never even considered."""
    md = (
        "## Canton Tower\n\nThe tower [[1]].\n\n"
        "## 参考文献\n\n"
        "[1] Source\n   URL: https://src/cited\n"
    )
    results = {"findings": [{"search_results": [
        # Cited source has no images.
        {"url": "https://src/cited", "html_content": "[]"},
        # Uncited source HAS an image — must be ignored entirely.
        {"url": "https://src/uncited", "html_content": (
            '[{"url": "https://img/stray.jpg", "alt": "Canton Tower", '
            '"source_url": "https://src/uncited", "source_title": "", '
            '"width": null, "height": null}]'
        )},
    ]}]}
    fake = _fake_model({"Canton Tower": [1.0, 0.0, 0.0, 0.0]})
    monkeypatch.setattr(postprocessing.semantic_matcher, "get_model", lambda *a, **k: fake)
    monkeypatch.setattr(
        postprocessing.semantic_matcher, "_canonical_section_phrase",
        lambda heading, entities: "Canton Tower",
    )
    with patch.object(postprocessing, "ImageStore") as store_mock:
        store_mock.return_value.persist.return_value = {}
        store_mock.return_value.rewrite_markdown.side_effect = lambda md, m, **k: md
        out = postprocessing.enhance_report_with_images(
            research_id="r", clean_markdown=md, results=results,
            db_session=MagicMock(), enable_images=True, vision_model="",
        )
    assert "stray.jpg" not in out


def test_low_similarity_image_dropped(monkeypatch):
    """An image whose alt is orthogonal to its section is dropped."""
    md = (
        "## Canton Tower\n\n[[1]].\n\n"
        "## 参考文献\n\n[1] S\n   URL: https://src/p\n"
    )
    results = {"findings": [{"search_results": [
        {"url": "https://src/p", "html_content": (
            '[{"url": "https://img/x.jpg", "alt": "Banana", '
            '"source_url": "https://src/p", "source_title": "", '
            '"width": null, "height": null}]'
        )},
    ]}]}
    fake = _fake_model({
        "Banana": [1.0, 0.0, 0.0, 0.0],          # alt direction
        # section phrase default -> [0,0,0,0], cosine 0 < threshold.
    })
    monkeypatch.setattr(postprocessing.semantic_matcher, "get_model", lambda *a, **k: fake)
    monkeypatch.setattr(
        postprocessing.semantic_matcher, "_canonical_section_phrase",
        lambda heading, entities: "section phrase orthogonal",
    )
    with patch.object(postprocessing, "ImageStore") as store_mock:
        store_mock.return_value.persist.return_value = {}
        store_mock.return_value.rewrite_markdown.side_effect = lambda md, m, **k: md
        out = postprocessing.enhance_report_with_images(
            research_id="r", clean_markdown=md, results=results,
            db_session=MagicMock(), enable_images=True, vision_model="",
        )
    assert "Banana" not in out
    assert "img/x.jpg" not in out


def test_enable_images_false_returns_markdown_unchanged():
    out = postprocessing.enhance_report_with_images(
        research_id="r", clean_markdown="# hi", results={"findings": []},
        db_session=MagicMock(), enable_images=False, vision_model="",
    )
    assert out == "# hi"


def test_firecrawl_client_forwarded_to_image_store(monkeypatch):
    """The anti-hotlink Firecrawl fallback must survive the pipeline
    rewrite: firecrawl_client is forwarded to ImageStore at construction
    (regression: the rewritten pipeline accepted-but-ignored it)."""
    md = (
        "## Canton Tower\n\n[[1]].\n\n"
        "## 参考文献\n\n[1] S\n   URL: https://src/p\n"
    )
    results = {"findings": [{"search_results": [
        {"url": "https://src/p", "html_content": (
            '[{"url": "https://img/x.jpg", "alt": "Canton Tower", '
            '"source_url": "https://src/p", "source_title": "", '
            '"width": null, "height": null}]'
        )},
    ]}]}
    fake = _fake_model({"Canton Tower": [1.0, 0.0, 0.0, 0.0]})
    monkeypatch.setattr(postprocessing.semantic_matcher, "get_model", lambda *a, **k: fake)
    monkeypatch.setattr(
        postprocessing.semantic_matcher, "_canonical_section_phrase",
        lambda heading, entities: "Canton Tower",
    )
    with patch.object(postprocessing, "ImageStore") as store_mock:
        store_mock.return_value.persist.return_value = (
            {"https://img/x.jpg": "/images/r/x.jpg"}
        )
        store_mock.return_value.rewrite_markdown.side_effect = lambda md, m, **k: md
        postprocessing.enhance_report_with_images(
            research_id="r", clean_markdown=md, results=results,
            db_session=MagicMock(), enable_images=True, vision_model="",
            firecrawl_client="FIRE",
        )
    assert store_mock.call_args.kwargs.get("firecrawl_client") == "FIRE"


def test_same_url_cited_in_two_sections_multi_bind_then_dedup(monkeypatch):
    """Same source cited in two sections — the multi-bind path
    inserts the image in BOTH sections, then the post-insert
    ``_dedupe_images`` pass collapses to first-occurrence-only
    so the final markdown shows the image exactly once (in
    Section A, the earlier section).
    """
    md = (
        "## Section A\n\n[[7]]\n\n"
        "## Section B\n\n[[7]]\n\n"
        "## 参考文献\n\n[7] S\n   URL: https://src/p\n"
    )
    results = {"findings": [{"search_results": [
        {"url": "https://src/p", "html_content": (
            '[{"url": "https://img/a.jpg", "alt": "Canton Tower", '
            '"source_url": "https://src/p", "source_title": "", '
            '"width": null, "height": null}]'
        )},
    ]}]}
    fake = _fake_model({"Canton Tower": [1.0, 0.0, 0.0, 0.0]})
    monkeypatch.setattr(postprocessing.semantic_matcher, "get_model", lambda *a, **k: fake)
    monkeypatch.setattr(
        postprocessing.semantic_matcher, "_canonical_section_phrase",
        lambda heading, entities: "Canton Tower",
    )
    with patch.object(postprocessing, "ImageStore") as store_mock:
        store_mock.return_value.persist.return_value = (
            {"https://img/a.jpg": "/images/r/a.jpg"}
        )
        store_mock.return_value.rewrite_markdown.side_effect = lambda md, m, **k: md
        out = postprocessing.enhance_report_with_images(
            research_id="r", clean_markdown=md, results=results,
            db_session=MagicMock(), enable_images=True, vision_model="",
        )
    # Final markdown shows the image exactly once, in Section A
    # (the first-occurrence-wins semantic of _dedupe_images).
    assert out.count("img/a.jpg") == 1
    before_b, _after_b = out.split("## Section B")
    assert "img/a.jpg" in before_b
    assert "img/a.jpg" not in _after_b


def test_citation_candidates_and_scored_events_emitted(monkeypatch):
    """The two new IMG-TRACE events — CITATION_CANDIDATES (right
    after loads_images, with the full candidate list) and
    CANDIDATE_SCORED (right before _cosine, with the vector
    fingerprints) — fire for every (cite, section) pair. Log
    parsers can reconstruct the candidate set and the scoring
    decision from the grep hits.
    """
    md = (
        "## Section A\n\n[[7]]\n\n"
        "## 参考文献\n\n[7] S\n   URL: https://src/p\n"
    )
    results = {"findings": [{"search_results": [
        {"url": "https://src/p", "html_content": (
            '[{"url": "https://img/a.jpg", "alt": "Canton Tower", '
            '"source_url": "https://src/p", "source_title": "", '
            '"width": null, "height": null}, '
            '{"url": "https://img/b.jpg", "alt": "", '
            '"source_url": "https://src/p", "source_title": "", '
            '"width": null, "height": null}]'
        )},
    ]}]}
    fake = _fake_model({"Canton Tower": [1.0, 0.0, 0.0, 0.0], "": [0.0, 0.0, 0.0, 0.0]})
    monkeypatch.setattr(postprocessing.semantic_matcher, "get_model", lambda *a, **k: fake)
    monkeypatch.setattr(
        postprocessing.semantic_matcher, "_canonical_section_phrase",
        lambda heading, entities: "Canton Tower",
    )
    # Capture loguru output by monkey-patching the logger's bound
    # ``info`` method on the postprocessing module. loguru's
    # ``logger.add(sink)`` approach is unreliable across the test
    # runner's per-test fixture setup; binding info directly via
    # monkeypatch.setattr captures every call reliably.
    captured: list[str] = []
    real_info = postprocessing.logger.info
    real_debug = postprocessing.logger.debug

    def _capture_info(message, *a, **k):
        captured.append(str(message))
        return real_info(message, *a, **k)

    def _capture_debug(message, *a, **k):
        captured.append(str(message))
        return real_debug(message, *a, **k)

    monkeypatch.setattr(postprocessing.logger, "info", _capture_info)
    monkeypatch.setattr(postprocessing.logger, "debug", _capture_debug)
    with patch.object(postprocessing, "ImageStore") as store_mock:
        store_mock.return_value.persist.return_value = {}
        store_mock.return_value.rewrite_markdown.side_effect = lambda md, m, **k: md
        postprocessing.enhance_report_with_images(
            research_id="r", clean_markdown=md, results=results,
            db_session=MagicMock(), enable_images=True, vision_model="",
        )
    log_text = "\n".join(captured)
    # CITATION_CANDIDATES was emitted with count=1 (only the
    # alt-bearing image; the empty-alt candidate is excluded
    # from the candidate list and emits CANDIDATE_NO_ALT instead).
    assert "CITATION_CANDIDATES research=r" in log_text
    assert "cite_num=7 ref_url=https://src/p sec=0 count=1" in log_text
    assert "img_url=https://img/a.jpg" in log_text
    # Empty-alt candidate must NOT appear in the CITATION_CANDIDATES
    # candidate list. It surfaces via the per-image CANDIDATE_NO_ALT
    # debug event below.
    candidates_line = [
        line for line in captured
        if "CITATION_CANDIDATES research=r" in line
    ][0]
    assert "img_url=https://img/b.jpg" not in candidates_line
    # CANDIDATE_SCORED emitted for the alt-bearing image only.
    scored_count = log_text.count("CANDIDATE_SCORED research=r")
    assert scored_count == 1
    # The event now emits the raw inputs (alt text + section
    # phrase text) instead of opaque vector fingerprints, so the
    # log line is human-readable and re-computable.
    scored_line = [
        line for line in captured
        if "CANDIDATE_SCORED research=r" in line
    ][0]
    assert "img_alt='Canton Tower'" in scored_line
    assert "img_url=https://img/a.jpg" in scored_line
    assert "sec_phrase_text='Canton Tower'" in scored_line
    assert "alt_vec_fp=" not in scored_line
    assert "sec_vec_fp=" not in scored_line
    # The empty-alt candidate is recorded as CANDIDATE_NO_ALT
    # (debug-level). Since our monkeypatch captures both info and
    # debug, the event should appear in `captured`.
    assert "CANDIDATE_NO_ALT" in log_text
    assert "img_url=https://img/b.jpg" in log_text
