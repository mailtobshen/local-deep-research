"""ref_text anti-rubber-stamp gate (2026-09-13, research b7ec824a).

``ref_text`` compares the image alt to its OWN source page's text, so
it is near-always high — a CapCut Black-Friday banner cited into a
"非洲地区办公室" section passed the gate with ref_text=0.67 while
sec=0.01 and query=0.14. The fix: ``ref_text`` may only be the winning
surface when at least one report-anchored surface (sec or query) clears
the 0.30 alignment floor; otherwise it is vetoed and the image is
scored on the anchored surfaces alone.
"""

from unittest.mock import MagicMock, patch

import numpy as np

from local_deep_research.images import postprocessing


ALT = "PC上的CapCutPro可享受40%的折扣"
REF_TEXT = "CapCut黑色星期五促銷 在電腦上以40%的折扣升級到專業版"
SEC_PHRASE = "国际办公室和代表处的地理位置 非洲地区"
QUERY = "Winrock International"


def _fake_model(vectors):
    class _M:
        def encode(self, phrases, normalize_embeddings=True):
            return [np.array(vectors[p]) for p in phrases]

    return _M()


def _run(monkeypatch, vectors):
    md = (
        "## 非洲地区\n\n办公室 [1] 分布广泛。\n\n"
        "## 参考文献\n\n"
        "[1] CapCut促销\n   URL: https://src/page\n"
    )
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
    monkeypatch.setattr(
        postprocessing, "build_url_text_index",
        lambda results: {"https://src/page": REF_TEXT},
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
        )
    return out


def test_ref_text_cannot_win_when_anchored_surfaces_low(
    monkeypatch, loguru_caplog
):
    """The b7ec824a case: ref_text=0.67, sec=0.01, query=0.14 → drop."""
    vectors = {
        # alt aligned with its own source page's text only
        ALT: [1.0, 0.0, 0.0, 0.0],
        REF_TEXT: [0.8, 0.6, 0.0, 0.0],   # cosine vs alt ≈ 0.80
        SEC_PHRASE: [0.0, 1.0, 0.0, 0.0],  # cosine vs alt = 0.00
        QUERY: [0.0, 0.0, 1.0, 0.0],       # cosine vs alt = 0.00
    }
    out = _run(monkeypatch, vectors)
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    assert "REF_TEXT_VETO" in text, "expected the veto event to fire"
    assert "CANDIDATE_DROPPED" in text
    assert "CANDIDATE_KEPT" not in text
    assert "https://img/ad.jpg" not in out


def test_ref_text_still_wins_when_section_aligned(
    monkeypatch, loguru_caplog
):
    """Control: ref_text high AND sec above the 0.30 floor → keep."""
    vectors = {
        ALT: [0.7071067811865476, 0.7071067811865476, 0.0, 0.0],
        REF_TEXT: [1.0, 0.5, 0.0, 0.0],   # cosine vs alt ≈ 0.85
        SEC_PHRASE: [0.0, 1.0, 0.0, 0.0],  # cosine vs alt ≈ 0.71
        QUERY: [0.0, 0.0, 1.0, 0.0],
    }
    out = _run(monkeypatch, vectors)
    text = "\n".join(r.getMessage() for r in loguru_caplog.records)
    assert "REF_TEXT_VETO" not in text
    assert "CANDIDATE_KEPT" in text
