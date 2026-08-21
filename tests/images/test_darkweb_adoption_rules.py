"""Darkweb (.onion) image-adoption rules.

Per the 2026-08-21 rule change, darkweb-sourced citations bypass the
alt-vs-section semantic gate entirely. An image from a darkweb cited
page is adopted iff:
  1. the cite_num's ref_url is same-origin (eTLD+1 == the .onion site)
     as the image's source page, AND
  2. it survives the same min-dimension logo/icon filter as clearnet.

These tests use the fake-model machinery from the citation-pipeline
suite but MUST NOT rely on it for the darkweb path — the fast path
never touches the semantic model.
"""
from unittest.mock import MagicMock, patch

from local_deep_research.images import postprocessing


def _fake_model(vectors: dict[str, list[float]]):
    import numpy as np

    class _M:
        def encode(self, phrases, normalize_embeddings=True):
            return [np.array(vectors.get(p, [0.0, 0.0, 0.0, 0.0])) for p in phrases]

    return _M()


_ONION = "http://abc123xyz456.onion/listing.html"
_ONION_IMG = "http://abc123xyz456.onion/img/photo.jpg"
_OTHER_ONION = "http://otheronion789qrs.onion/listing.html"


def _run(monkeypatch, html_json, ref_url=_ONION):
    md = (
        "## Market overview\n\nThe market [[1]] is active.\n\n"
        "## 参考文献\n\n"
        f"[1] Source\n   URL: {ref_url}\n"
    )
    results = {"findings": [{"search_results": [
        {"url": ref_url, "html_content": html_json},
    ]}]}

    def _boom(*a, **k):
        raise AssertionError("darkweb path must not load the semantic model")

    monkeypatch.setattr(postprocessing.semantic_matcher, "get_model", _boom)

    with patch.object(postprocessing, "ImageStore") as store_mock:
        store_mock.return_value.persist.return_value = (
            {_ONION_IMG: "/images/r/p.jpg"}
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
    return out


def _img_json(url=_ONION_IMG, source_url=_ONION, width=None, height=None, alt="listing photo"):
    return (
        f'[{{"url": "{url}", "alt": "{alt}", '
        f'"source_url": "{source_url}", "source_title": "t", '
        f'"width": {str(width).lower() if width is not None else "null"}, '
        f'"height": {str(height).lower() if height is not None else "null"}}}]'
    )


def test_darkweb_same_origin_and_large_enough_is_adopted(monkeypatch):
    out = _run(monkeypatch, _img_json(width=600, height=400))
    assert f"![listing photo]({_ONION_IMG})" in out


def test_darkweb_cross_origin_image_dropped(monkeypatch):
    # Image extracted from the cited page but its source_url points at
    # another .onion site -> not same-origin -> dropped.
    out = _run(monkeypatch, _img_json(source_url=_OTHER_ONION))
    assert _ONION_IMG not in out


def test_darkweb_too_small_image_dropped(monkeypatch):
    out = _run(monkeypatch, _img_json(width=24, height=24))
    assert _ONION_IMG not in out


def test_darkweb_missing_dimensions_kept(monkeypatch):
    # No width/height attributes -> extractor's "unknown size" leniency.
    out = _run(monkeypatch, _img_json())
    assert f"![listing photo]({_ONION_IMG})" in out


def test_darkweb_meaningless_alt_dropped(monkeypatch):
    """Theme-artifact alts ('300x300', 'shop', 'Placeholder') are
    dropped by the darkweb fast path — no semantic gate exists there,
    so alt quality is the only caption signal."""
    import json
    out = _run(monkeypatch, json.dumps([
        {"url": _ONION_IMG, "alt": "300x300",
         "source_url": _ONION, "source_title": "t",
         "width": 600, "height": 400},
        {"url": _ONION_IMG + "?2", "alt": "real product photo",
         "source_url": _ONION, "source_title": "t",
         "width": 600, "height": 400},
    ]))
    assert "300x300" not in out
    assert "![real product photo]" in out
