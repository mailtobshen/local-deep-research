"""ref_text surface redefinition (2026-09-13, research b7ec824a).

``ref_text`` used to compare the image alt to its OWN source page's
passage (``build_url_text_index``), which rubber-stamped any cited
page's images — a CapCut Black-Friday banner cited into a
"非洲地区办公室" section scored ref_text=0.67 while sec=0.01 and
query=0.14. The surface now anchors to the report itself:
``build_cite_context_index`` maps each citation number to the body
paragraph containing its first ``[N]`` marker, and ref_text is the
alt-vs-that-context cosine.
"""

from unittest.mock import MagicMock, patch

import numpy as np

from local_deep_research.images import postprocessing
from local_deep_research.images.relevance import build_cite_context_index

ALT = "PC上的CapCutPro可享受40%的折扣"
SEC_PHRASE = "国际办公室和代表处的地理位置 非洲地区"
QUERY = "Winrock International"


class TestBuildCiteContextIndex:
    MD = (
        "## 非洲地区\n\n"
        "Winrock 在肯尼亚和埃塞俄比亚设有办公室 [1]。\n\n"
        "后续扩展到了塞内加尔 [[2]] 以及加纳 [3](https://x/g)。\n\n"
        "## 参考文献\n\n"
        "[1] CapCut促销\n   URL: https://src/page\n"
        "[2] 另一来源\n   URL: https://src/2\n"
        "[3] 第三来源\n   URL: https://src/3\n"
    )

    def test_first_occurrence_paragraph_with_markers_stripped(self):
        idx = build_cite_context_index(self.MD)
        assert idx["1"] == "Winrock 在肯尼亚和埃塞俄比亚设有办公室 。"
        # hyperlink form [3](url) also stripped
        assert "https" not in idx["3"]
        assert "加纳" in idx["3"]

    def test_all_marker_forms_found(self):
        idx = build_cite_context_index(self.MD)
        assert set(idx) == {"1", "2", "3"}

    def test_references_block_excluded(self):
        # The refs block cites nothing itself; its own "[1]" rows must
        # not become context.
        assert all("URL:" not in v for v in
                   build_cite_context_index(self.MD).values())

    def test_uncited_number_absent(self):
        assert "9" not in build_cite_context_index(self.MD)


def _fake_model(vectors):
    class _M:
        def encode(self, phrases, normalize_embeddings=True):
            return [np.array(vectors[p]) for p in phrases]

    return _M()


def _run(monkeypatch, vectors, **extra):
    md = (
        "## 非洲地区\n\n"
        "Winrock 在肯尼亚设有办公室 [1]。\n\n"
        "## 参考文献\n\n"
        "[1] CapCut促销\n   URL: https://src/page\n"
    )
    context = build_cite_context_index(md)["1"]
    results = {"findings": [{"search_results": [
        {"url": "https://src/page", "html_content": (
            '[{"url": "https://img/ad.jpg", "alt": "' + ALT + '", '
            '"source_url": "https://src/page", "source_title": "capcut", '
            '"width": 800, "height": 600}]'
        )},
    ]}]}
    monkeypatch.setattr(
        postprocessing.semantic_matcher, "get_model",
        lambda *a, **k: _fake_model(vectors),
    )
    monkeypatch.setattr(
        postprocessing.semantic_matcher, "_canonical_section_phrase",
        lambda heading, entities, parent_heading="": SEC_PHRASE,
    )
    with patch.object(postprocessing, "ImageStore") as store_mock:
        store_mock.return_value.persist.return_value = {}
        store_mock.return_value.rewrite_markdown.side_effect = (
            lambda md, m, **kw: md
        )
        out = postprocessing.enhance_report_with_images(
            research_id="r",
            clean_markdown=md,
            results=results,
            db_session=MagicMock(),
            enable_images=True,
            vision_model="",
            research_query=QUERY,
            **extra,
        )
    return out, context


def test_cross_lingual_ref_text_kept_by_dedicated_threshold(
    monkeypatch, loguru_caplog
):
    """The WWII case (research b7ec824a): EN alt genuinely matching the
    ZH citing paragraph scores 0.50 — below the uniform 0.6 gate but
    above the dedicated alt_ref_text_threshold=0.45 → keep."""
    _, context = _run(monkeypatch, {})
    # Unit alt vector: ref_text=0.55 (context=e1), sec=0.30 (e2),
    # query=0.00 — only the dedicated 0.45 gate can pass it.
    vectors = {
        ALT: [0.55, 0.30, 0.50, 0.5932],
        context: [1.0, 0.0, 0.0, 0.0],
        SEC_PHRASE: [0.0, 1.0, 0.0, 0.0],
        QUERY: [0.0, 0.0, 0.0, 1.0],
    }
    # Without the dedicated threshold: uniform 0.6 → drop.
    out, _ = _run(monkeypatch, vectors)
    assert "CANDIDATE_DROPPED" in "\n".join(
        r.getMessage() for r in loguru_caplog.records
    )
    # With alt_ref_text_threshold=0.45: keep via the ref_text surface.
    out, _ = _run(monkeypatch, vectors, alt_ref_text_threshold=0.45)
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    assert "CANDIDATE_KEPT" in text
    detail = [l for l in text.splitlines()
              if "CANDIDATE_SCORED_DETAIL" in l and "decision=keep" in l]
    assert detail and "surface=ref_text" in detail[0]


def test_b7ec824a_replay_now_drops(monkeypatch, loguru_caplog):
    """CapCut banner: the context surface is anchored to the citing
    paragraph (about Winrock offices), so the ad alt scores low on
    every surface → drop."""
    # Probe pass to learn the derived context text, then the real run.
    _, context = _run(monkeypatch, {})
    vectors = {
        ALT: [1.0, 0.0, 0.0, 0.0],
        context: [0.0, 1.0, 0.0, 0.0],
        SEC_PHRASE: [0.0, 1.0, 0.0, 0.0],
        QUERY: [0.0, 0.0, 1.0, 0.0],
    }
    out, _ = _run(monkeypatch, vectors)
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    assert "CANDIDATE_DROPPED" in text
    assert "CANDIDATE_KEPT" not in text
    assert "https://img/ad.jpg" not in out


def test_context_match_keeps_despite_low_heading_and_query(
    monkeypatch, loguru_caplog
):
    """The rescue case ref_text exists for: alt matches the citing
    paragraph (cross-lingual/entity-dense context) while the heading
    is a template and the query is far → keep via the ref_text
    surface."""
    _, context = _run(monkeypatch, {})
    vectors = {
        ALT: [1.0, 0.5, 0.0, 0.0],
        context: [1.0, 0.4, 0.0, 0.0],    # cosine vs alt ≈ 0.98
        SEC_PHRASE: [0.0, 1.0, 0.0, 0.0],  # sec = 0.45, below threshold
        QUERY: [0.0, 0.0, 1.0, 0.0],       # query = 0.00
    }
    out, _ = _run(monkeypatch, vectors)
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    assert "CANDIDATE_KEPT" in text
    detail = [l for l in text.splitlines()
              if "CANDIDATE_SCORED_DETAIL" in l and "decision=keep" in l]
    assert detail and "surface=ref_text" in detail[0]
