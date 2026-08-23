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


def test_darkweb_alt_policy_lenient_size_strict(monkeypatch):
    """2026-08-22 policy: alt filter is LENIENT (only pure UI vocabulary
    drops — 'home'/'logo'/'placeholder'); dimension-style alts pass and
    their embedded size feeds the STRICT icon filter instead."""
    import json
    out = _run(monkeypatch, json.dumps([
        # dimension alt + explicit big size -> kept (may be real content)
        {"url": _ONION_IMG, "alt": "300x300",
         "source_url": _ONION, "source_title": "t",
         "width": 600, "height": 400},
        # UI vocabulary -> dropped regardless of size
        {"url": _ONION_IMG + "?home", "alt": "Home",
         "source_url": _ONION, "source_title": "t",
         "width": 800, "height": 600},
        # dimension alt, attrs missing, dims big -> kept
        {"url": _ONION_IMG + "?dim", "alt": "image 740x555",
         "source_url": _ONION, "source_title": "t",
         "width": None, "height": None},
        # dimension alt, attrs missing, dims tiny -> dropped as icon
        {"url": _ONION_IMG + "?tiny", "alt": "40x40",
         "source_url": _ONION, "source_title": "t",
         "width": None, "height": None},
    ]))
    assert "![300x300]" in out
    assert "?home" not in out and "Home" not in out
    assert "![image 740x555]" in out
    assert "?tiny" not in out


def test_darkweb_home_and_thumbnail_alts_dropped(monkeypatch):
    """Bare 'Home'/'Thumbnail' alts are theme artifacts; 'Thumbnail for X'
    keeps the image (inner text is the real caption)."""
    import json
    out = _run(monkeypatch, json.dumps([
        {"url": _ONION_IMG, "alt": "Home",
         "source_url": _ONION, "source_title": "t",
         "width": 600, "height": 400},
        {"url": _ONION_IMG + "?2", "alt": "Thumbnail",
         "source_url": _ONION, "source_title": "t",
         "width": 600, "height": 400},
        {"url": _ONION_IMG + "?3", "alt": "Thumbnail for FENTANYL | Fentanyl",
         "source_url": _ONION, "source_title": "t",
         "width": 600, "height": 400},
    ]))
    assert "![Home]" not in out
    assert "![Thumbnail]" not in out
    assert "![Thumbnail for FENTANYL | Fentanyl]" in out


def test_darkweb_cap5_and_tiebreak_order():
    """2026-08-23 policy: darkweb cap=5 (clearnet 3); ties break by
    area desc, then substance (alt > filename > neither)."""
    from local_deep_research.images.postprocessing import _build_placements
    from local_deep_research.images.extractor import ExtractedImage

    def img(url, alt="", w=None, h=None):
        return ExtractedImage(url=url, alt=alt, source_url="http://x.onion/p",
                              source_title="T", width=w, height=h)

    cands = [
        ("http://x.onion/shop.png", "shop", 40, 40),
        ("http://x.onion/big-content.jpg", "黑产招工月赚几十万", 800, 600),
        ("http://x.onion/med.png", "FENTANYL product", 300, 300),
        ("http://x.onion/big-empty.jpg", "", 700, 500),
        ("http://x.onion/small.jpg", "", 100, 100),
        ("http://x.onion/dim.jpg", "image 399x600", None, None),
    ]
    bank = {u: img(u, a, w, h) for u, a, w, h in cands}
    binding = {u: [(1, 3, 0.0)] for u in bank}

    dark = _build_placements(binding, bank, _caption_fallback=True, darkweb=True)
    clear = _build_placements(binding, bank, _caption_fallback=True, darkweb=False)
    assert len(dark) == 5, "darkweb cap = 5"
    assert len(clear) == 3, "clearnet cap = 3"
    # area desc + substance rank: big-content wins over big-empty
    assert dark[0][1].endswith("big-content.jpg")
    # with cap=5 of 6 candidates, the UI icon (shop, tiny, no
    # substance) is the one eliminated — not the small-but-legit image
    assert all(not p[1].endswith("shop.png") for p in dark)


def test_darkweb_messaging_evidence_force_adopted(monkeypatch):
    """2026-08-23 policy: darkweb images whose alt/filename references a
    messaging platform or email (QQ/WeChat/Telegram/... contact cards)
    are force-adopted — immune to same-origin, size and alt filters —
    and rank first in placement."""
    import json
    out = _run(monkeypatch, json.dumps([
        # cross-origin but >=50px: still adopted (messaging override)
        {"url": "http://other.onion/qq-card.png", "alt": "QQ客服 123456",
         "source_url": "http://other.onion/p", "source_title": "T",
         "width": 300, "height": 200},
        # messaging evidence but <50px: dropped by the absolute floor
        {"url": "http://other.onion/qq-tiny.png", "alt": "QQ客服 999",
         "source_url": "http://other.onion/p", "source_title": "T",
         "width": 40, "height": 40},
        {"url": _ONION_IMG, "alt": "product photo",
         "source_url": _ONION, "source_title": "t",
         "width": 600, "height": 400},
    ]))
    assert "![QQ客服 123456]" in out
    assert "qq-tiny.png" not in out
    assert "![product photo]" in out


def test_darkweb_commercial_ad_alts_dropped():
    """2026-08-23 policy: site-promo / commercial-ad alts (the Monero
    service-page batch and generic signup/download/pricing vocab) are
    theme artifacts — dropped. Real-content alts incl. empty pass."""
    from local_deep_research.images.postprocessing import _alt_is_meaningless
    for a in ["Create wallet", "Exchange", "Merchants", "Contribute",
              "FAQ", "onion service", "Get started", "Sign up",
              "Pricing", "Subscribe"]:
        assert _alt_is_meaningless(a), a
    for a in ["黑产招工月赚几十万", "FENTANYL | Fentanyl", "QQ客服 123456",
              "300x300", "", "my wallet was stolen evidence"]:
        assert not _alt_is_meaningless(a), a


def test_unknown_area_defaults_to_300x300():
    """2026-08-23 policy: images with no dimension info (attrs missing,
    alt carries no dims) rank at 300x300 area — competitive with small
    known thumbnails, still below real large images."""
    from local_deep_research.images.postprocessing import _area
    from local_deep_research.images.extractor import ExtractedImage

    def img(alt="", w=None, h=None):
        return ExtractedImage(url="http://x.onion/a.jpg", alt=alt,
                              source_url="http://x.onion/p",
                              source_title="T", width=w, height=h)

    assert _area(img()) == 90000          # unknown → default
    assert _area(img("", 100, 100)) == 10000  # small known < default
    assert _area(img("", 800, 600)) == 480000  # large known > default


def test_entry_threshold_150px_both_dims():
    """2026-08-23 policy: width AND height must both be >=150px
    (extractor + fast path, darkweb and clearnet alike; 50→200→150
    after tuning). Unknown dimensions stay lenient."""
    import json
    out = _run(monkeypatch=None, html_json=json.dumps([
        {"url": _ONION_IMG, "alt": "at200", "source_url": _ONION,
         "source_title": "t", "width": 200, "height": 200},
        {"url": _ONION_IMG + "?w199", "alt": "w199", "source_url": _ONION,
         "source_title": "t", "width": 199, "height": 800},
        {"url": _ONION_IMG + "?h199", "alt": "h199", "source_url": _ONION,
         "source_title": "t", "width": 800, "height": 199},
    ])) if False else None
    from unittest.mock import MagicMock, patch
    from local_deep_research.images import postprocessing as pp
    md = "## S\n\n[[1]]\n\n## 参考文献\n\n[1] Src\n   URL: http://x.onion/p\n"
    imgs = json.dumps([
        {"url": "http://x.onion/ok.jpg", "alt": "at150", "source_url": "http://x.onion/p",
         "source_title": "t", "width": 150, "height": 150},
        {"url": "http://x.onion/w149.jpg", "alt": "w", "source_url": "http://x.onion/p",
         "source_title": "t", "width": 149, "height": 800},
        {"url": "http://x.onion/h149.jpg", "alt": "h", "source_url": "http://x.onion/p",
         "source_title": "t", "width": 800, "height": 149},
        {"url": "http://x.onion/w199.jpg", "alt": "w199", "source_url": "http://x.onion/p",
         "source_title": "t", "width": 199, "height": 700},
        {"url": "http://x.onion/unk.jpg", "alt": "", "source_url": "http://x.onion/p",
         "source_title": "t", "width": None, "height": None},
    ])
    results = {"findings": [{"search_results": [{"url": "http://x.onion/p", "html_content": imgs}]}]}
    def _boom(*a, **k): raise AssertionError
    with patch.object(pp.semantic_matcher, "get_model", _boom), patch.object(pp, "ImageStore") as sm:
        sm.return_value.persist.return_value = {}
        sm.return_value.rewrite_markdown.side_effect = lambda md, m, **kw: md
        out = pp.enhance_report_with_images(research_id="r", clean_markdown=md,
            results=results, db_session=MagicMock(), enable_images=True, vision_model="")
    assert "![at150]" in out         # exactly 150x150 passes
    assert "w149.jpg" not in out     # width 149 < 150
    assert "h149.jpg" not in out     # height 149 < 150
    assert "w199.jpg" in out         # 199 now above the 150 floor
    assert "unk.jpg" in out          # unknown dims lenient
