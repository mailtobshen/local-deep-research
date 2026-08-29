"""Post-enforce single-pass image pipeline contract tests.

2026-08-29 reorder: all image fetching/insertion now runs AFTER
sources_enforce + citation_format, reading the FINALIZED References
block directly. Plan B (findings[].search_results[] membership check)
no longer touches the image fetch set, and the html_content
already-fetched bookkeeping is gone — the deferred pass fetches every
body-cited URL unconditionally and hands the payloads straight to
``enhance_report_with_images`` via the new ``fetched_html`` channel.
"""

from unittest.mock import patch

from local_deep_research.images.postprocessing import (
    enhance_report_with_images,
)
from local_deep_research.web.services.research_service import (
    _deferred_image_fill,
)

REPORT = """# 报告

## 一、景点

外滩是著名的滨江景观带 [[1]]。南京路是第一商业街 [[2]]。

## References

[1] 外滩介绍
URL: https://example.com/bund
[2] 南京路介绍
URL: https://example.com/nanjing
"""

# results WITHOUT any matching search_results rows — the Plan B era
# dropped 32/32 cited URLs in this shape (research 6216a2f2).
RESULTS_WITHOUT_MATCH = {
    "findings": [
        {"search_results": [{"url": "https://unrelated.example/x"}]}
    ],
    "all_links_of_system": [],
}


def _fake_fetch(urls, titles=None, settings_snapshot=None):
    """Two pages: bund has one image, nanjing has one image."""
    class Img:
        def __init__(self, alt, url, source_url):
            self.alt = alt
            self.url = url
            self.source_url = source_url
            self.source_title = alt  # dumps_images requires these
            self.width = 600
            self.height = 400

    return {
        "https://example.com/bund": {
            "text": "bund page",
            "images": [Img("外滩", "https://img.example/bund.jpg",
                           "https://example.com/bund")],
        },
        "https://example.com/nanjing": {
            "text": "nanjing page",
            "images": [Img("南京路", "https://img.example/nj.jpg",
                           "https://example.com/nanjing")],
        },
    }


def test_deferred_fill_fetches_urls_missing_from_search_results():
    """The fetch set comes from the References block alone — URLs that
    appear nowhere in findings[].search_results[] must still be fetched
    (Plan B must NOT prune the image fetch set)."""
    with patch(
        "local_deep_research.research_library.downloaders.extraction.pipeline."
        "fetch_content_with_images",
        side_effect=_fake_fetch,
    ):
        filled = _deferred_image_fill(
            "test-research",
            final_markdown=REPORT,
            results=dict(RESULTS_WITHOUT_MATCH),
            settings_snapshot={"report.enable_images": True},
        )
    assert filled == 2, "both References URLs must be fetched despite no search_results match"


def test_enhance_inserts_images_from_fetched_html_channel():
    """enhance_report_with_images places images when the fetched payloads
    arrive via the fetched_html override — no html_content pre-population
    of results required."""
    import os

    # Point at the container's offline HF cache and hard-disable any
    # network fallback so the test cannot hang on a model download.
    os.environ.setdefault("HF_HOME", "/home/ldruser/.cache/huggingface")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

    # Use the post-enforce/format body shape: hyperlinked citations.
    post_format = REPORT.replace(
        "[[1]]", "[\\[1\\]](https://example.com/bund)"
    ).replace("[[2]]", "[\\[2\\]](https://example.com/nanjing)")
    fetched = {
        "https://example.com/bund": _fake_fetch(None)["https://example.com/bund"],
    }
    from local_deep_research.images.serialize import dumps_images

    fetched_html = {
        url: dumps_images(entry["images"])
        for url, entry in fetched.items()
    }

    # Stub the semantic gate: this test exercises the fetched_html
    # plumbing, not the embedding scorer. get_model() -> None makes the
    # cosine gate degrade to its no-model path; build_report_entity_pool
    # -> {} keeps section-phrase assembly cheap.
    from local_deep_research.images import postprocessing as pp

    orig_build = pp.semantic_matcher.build_report_entity_pool
    orig_model = pp.semantic_matcher.get_model
    pp.semantic_matcher.build_report_entity_pool = lambda md: {}
    pp.semantic_matcher.get_model = lambda *a, **k: None
    try:
        out = enhance_report_with_images(
            research_id="test-research",
            clean_markdown=post_format,
            results=RESULTS_WITHOUT_MATCH,
            fetched_html=fetched_html,
            db_session=None,
            enable_images=True,
            vision_model="",
            alt_similarity_threshold=0.0,
        )
    finally:
        pp.semantic_matcher.build_report_entity_pool = orig_build
        pp.semantic_matcher.get_model = orig_model
    assert "![外滩]" in out or "外滩" in out, (
        "image from the fetched_html channel must reach the report; "
        f"got: {out[:300]!r}"
    )
