"""Tests for image-chain noise suppression (research e14f3600 follow-up).

- Flood cap: extract_images truncates listing/directory pages (>100
  images) to the first 30 before they reach the scoring loop.
- Wiki variant collapse: _deferred_image_fill's canonicalisation maps
  zh.wikipedia /zh-cn/ /zh-tw/ /zh-hk/ /zh/ variants onto /wiki/, and
  the payload write-back aliases every variant URL.
"""

import pytest

from local_deep_research.images.extractor import (
    _PAGE_IMAGE_FLOOD_AT,
    _PAGE_IMAGE_FLOOD_KEEP,
    extract_images,
)
from local_deep_research.web.services.research_service import (
    _WIKI_VARIANT_PATH_RE,
)


def _html_n_imgs(n: int, base: str = "https://example.com/listing") -> str:
    imgs = "\n".join(
        f'<img src="{base}/img_{i}.png" alt="figure {i}">'
        for i in range(n)
    )
    return f"<html><body><article>{imgs}</article></body></html>"


class TestPageImageFloodCap:
    def test_flood_page_truncated_to_keep(self):
        out = extract_images(
            _html_n_imgs(300), "https://example.com/listing", "Listing"
        )
        assert len(out) == _PAGE_IMAGE_FLOOD_KEEP

    def test_ntu_scale_flood_truncated(self):
        # e14f3600 cite 28: dlbs.liberal.ntu.edu.tw fulltext = 7088
        out = extract_images(
            _html_n_imgs(7088, "https://dlbs.liberal.ntu.edu.tw/en/fulltext"),
            "https://dlbs.liberal.ntu.edu.tw/en/fulltext",
            "Fulltext",
        )
        assert len(out) == _PAGE_IMAGE_FLOOD_KEEP

    def test_below_threshold_untouched(self):
        html = _html_n_imgs(_PAGE_IMAGE_FLOOD_AT, "https://example.com/a")
        out = extract_images(html, "https://example.com/a", "A")
        assert len(out) == _PAGE_IMAGE_FLOOD_AT

    def test_normal_article_untouched(self):
        html = _html_n_imgs(18, "https://zh.wikipedia.org/wiki/X")
        out = extract_images(html, "https://zh.wikipedia.org/wiki/X", "X")
        assert len(out) == 18

    def test_truncation_keeps_document_order(self):
        out = extract_images(
            _html_n_imgs(150), "https://example.com/listing", "L"
        )
        assert out[0].url.endswith("img_0.png")
        assert out[-1].url.endswith(f"img_{_PAGE_IMAGE_FLOOD_KEEP - 1}.png")


class TestWikiVariantPathRegex:
    @pytest.mark.parametrize(
        "path,expected_title",
        [
            ("/zh-cn/第十四世达赖喇嘛", "第十四世达赖喇嘛"),
            ("/zh-tw/Article", "Article"),
            ("/zh-hk/A", "A"),
            ("/zh-sg/B", "B"),
            ("/zh/C", "C"),
        ],
    )
    def test_variants_match(self, path, expected_title):
        m = _WIKI_VARIANT_PATH_RE.match(path)
        assert m is not None and m.group(2 if m.lastindex == 2 else 1) == expected_title or m.group(1) == expected_title

    def test_canonical_wiki_path_no_match(self):
        assert _WIKI_VARIANT_PATH_RE.match("/wiki/Article") is None

    def test_en_wikipedia_no_match(self):
        # Regex only applied to *.wikipedia.org hosts in the caller,
        # but the path itself on /zh-cn/ still matches — host gate is
        # what protects non-wiki hosts. Verify a non-variant path.
        assert _WIKI_VARIANT_PATH_RE.match("/foo/bar") is None
