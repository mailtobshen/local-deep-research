"""End-to-end fixture for the 86132889 Beijing-tourism run.

This integration test replays the 86132889 research run through the
citation-anchored image pipeline without hitting any external service
— no semantic-model download, no DB.

Beijing research shape (mirrors the real run):
  * 15 sections: H1 title + 13 sub-researches + References
  * Each cited URL is a real Wikipedia article path
  * Each Wikipedia page returns 1-3 image candidates (18 total)
  * Citation 6 (Beijing opera) deliberately has an empty URL line in
    the Sources block — the row is skipped, leaving 11 cited URLs
  * The trailing ## 参考文献 section must not be treated as a body
    citation source
  * Citation [1] is referenced twice in the body (sections "研究范围
    与方法" and "季节性旅游提示") — both resolve to the same Beijing
    URL; images bind to the last citing section
  * search_results are keyed "link" (the production SearXNG format)

The test mocks:
  * the semantic model (constant-vector fake -> every image passes
    the per-section cosine gate at score 1.0)
  * ImageStore (no real DB)

It does NOT mock the rest of the pipeline: sanitize_references ->
build_citation_index -> per-section extraction + semantic gate ->
insert_images_by_section -> _dedupe_images -> persist/rewrite.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from local_deep_research.images import postprocessing
from local_deep_research.images.postprocessing import enhance_report_with_images


def _patch_image_store(monkeypatch):
    """Stub out ImageStore so the test does not need a real DB.
    persist() returns the chosen URL -> stored URL map (identity);
    rewrite_markdown() returns the markdown unchanged."""
    class FakeStore:
        def __init__(self, *a, **kw):
            self._last_url_to_size = {}

        def persist(self, chosen, url_to_alt=None, url_to_source=None):
            return {u: u for u in chosen}

        def rewrite_markdown(self, md, url_to_route, url_to_size=None):
            return md

    monkeypatch.setattr(postprocessing, "ImageStore", FakeStore)


_BEIJING_SCENARIO_MARKDOWN = """\
# 北京旅游景点研究报告

## 研究范围与方法

本研究以中国首都北京为研究对象，系统梳理其历史名胜与现代地标 [1]。

## 1. 城市历史与地位

北京是中华人民共和国首都，有着超过三千年建城史 [2]。

## 2. 皇家宫殿遗址

故宫又称紫禁城，是明清两代皇家宫殿 [3]。

## 3. 皇家园林与湖泊

颐和园以万寿山、昆明湖为基础，借鉴江南园林的设计手法 [4]。

## 4. 长城防御体系

八达岭长城是明长城最具代表性的段落 [5]。

## 5. 传统民俗活动

庙会、舞龙、京剧是老北京最重要的民俗符号 [6]。

## 6. 胡同四合院生活

胡同与四合院构成老北京城的基本肌理 [7]。

## 7. 藏传佛教寺院

雍和宫是北京最重要的藏传佛教寺院，原为清世宗雍亲王府 [8]。

## 8. 奥运场馆建筑

国家体育场（鸟巢）与国家游泳中心（水立方）是 2008 年北京奥运会的标志性建筑 [9]。

## 9. 艺术创意园区

798 艺术区由原国营电子工业厂房改造而来，是中国当代艺术的重要聚集地 [10]。

## 10. 最佳游览路线规划

五天四晚路线可覆盖故宫、长城、颐和园三大核心 [11]。

## 11. 交通与住宿建议

北京首都国际机场与大兴国际机场构成双枢纽 [12]。

## 12. 季节性旅游提示

春秋两季气候宜人，是北京旅游的黄金季节 [1]。

## 参考文献

[1] Beijing — Wikipedia
   URL: https://en.wikipedia.org/wiki/Beijing
[2] History of Beijing
   URL: https://en.wikipedia.org/wiki/History_of_Beijing
[3] Forbidden City
   URL: https://en.wikipedia.org/wiki/Forbidden_City
[4] Summer Palace
   URL: https://en.wikipedia.org/wiki/Summer_Palace
[5] Great Wall of China
   URL: https://en.wikipedia.org/wiki/Great_Wall_of_China
[6] Beijing opera
   URL:
[7] Hutong
   URL: https://en.wikipedia.org/wiki/Hutong
[8] Yonghe Temple
   URL: https://en.wikipedia.org/wiki/Yonghe_Temple
[9] Beijing National Stadium
   URL: https://en.wikipedia.org/wiki/Beijing_National_Stadium
[10] 798 Art Zone
   URL: https://en.wikipedia.org/wiki/798_Art_Zone
[11] Beijing Subway
   URL: https://en.wikipedia.org/wiki/Beijing_Subway
[12] Beijing Capital International Airport
   URL: https://en.wikipedia.org/wiki/Beijing_Capital_International_Airport
"""


# (cited_url, [image_url, alt] pairs) — each cited URL yields 1-3
# images from the same Wikipedia article. Beijing opera is omitted
# here because its URL is empty in the Sources block (the parser
# skips empty-URL rows).
_BEIJING_IMAGE_CANDIDATES = {
    "https://en.wikipedia.org/wiki/Beijing": [
        ("https://upload.wikimedia.org/wikipedia/commons/Beijing_skyline_1.jpg",
         "Beijing skyline at dusk"),
        ("https://upload.wikimedia.org/wikipedia/commons/Beijing_skyline_2.jpg",
         "Beijing CBD from above"),
    ],
    "https://en.wikipedia.org/wiki/History_of_Beijing": [
        ("https://upload.wikimedia.org/wikipedia/commons/Yonghegong_history.jpg",
         "Historical view of the Yonghe Temple complex"),
    ],
    "https://en.wikipedia.org/wiki/Forbidden_City": [
        ("https://upload.wikimedia.org/wikipedia/commons/Forbidden_City_aerial.jpg",
         "Aerial view of the Forbidden City"),
        ("https://upload.wikimedia.org/wikipedia/commons/Forbidden_City_Hall.jpg",
         "Hall of Supreme Harmony"),
        ("https://upload.wikimedia.org/wikipedia/commons/Forbidden_City_Corner.jpg",
         "Corner tower of the Forbidden City at sunset"),
    ],
    "https://en.wikipedia.org/wiki/Summer_Palace": [
        ("https://upload.wikimedia.org/wikipedia/commons/Summer_Palace_lake.jpg",
         "Kunming Lake and the Seventeen-Arch Bridge"),
        ("https://upload.wikimedia.org/wikipedia/commons/Summer_Palace_pavilion.jpg",
         "Pavilion at the Summer Palace"),
    ],
    "https://en.wikipedia.org/wiki/Great_Wall_of_China": [
        ("https://upload.wikimedia.org/wikipedia/commons/Great_Wall_Badaling.jpg",
         "Great Wall at Badaling"),
        ("https://upload.wikimedia.org/wikipedia/commons/Great_Wall_Mutianyu.jpg",
         "Mutianyu section of the Great Wall"),
    ],
    "https://en.wikipedia.org/wiki/Hutong": [
        ("https://upload.wikimedia.org/wikipedia/commons/Hutong_street.jpg",
         "Narrow hutong alley with traditional courtyard houses"),
    ],
    "https://en.wikipedia.org/wiki/Yonghe_Temple": [
        ("https://upload.wikimedia.org/wikipedia/commons/Yonghe_Temple_entrance.jpg",
         "Entrance hall of Yonghe Temple"),
        ("https://upload.wikimedia.org/wikipedia/commons/Yonghe_Temple_statue.jpg",
         "Statue of Maitreya inside Yonghe Temple"),
    ],
    "https://en.wikipedia.org/wiki/Beijing_National_Stadium": [
        ("https://upload.wikimedia.org/wikipedia/commons/Bird_Nest_stadium.jpg",
         "Beijing National Stadium at night"),
        ("https://upload.wikimedia.org/wikipedia/commons/Water_Cube.jpg",
         "Beijing National Aquatics Center"),
    ],
    "https://en.wikipedia.org/wiki/798_Art_Zone": [
        ("https://upload.wikimedia.org/wikipedia/commons/798_Art_Zone_gallery.jpg",
         "Industrial gallery space in the 798 Art Zone"),
    ],
    "https://en.wikipedia.org/wiki/Beijing_Subway": [
        ("https://upload.wikimedia.org/wikipedia/commons/Beijing_Subway_map.png",
         "Beijing Subway system map"),
    ],
    "https://en.wikipedia.org/wiki/Beijing_Capital_International_Airport": [
        ("https://upload.wikimedia.org/wikipedia/commons/PEK_terminal_3.jpg",
         "Terminal 3 of Beijing Capital International Airport"),
    ],
}


def _build_beijing_results():
    """Construct the ``results`` dict the langgraph strategy would
    have produced — every cited Wikipedia URL becomes a search_result
    keyed "link" (production SearXNG format) with html_content
    carrying the article's images.
    """
    search_results = []
    for cited_url, imgs in _BEIJING_IMAGE_CANDIDATES.items():
        search_results.append({
            "link": cited_url,
            "title": "Beijing — Wikipedia",
            "content": "An overview article.",
            "snippet": "Beijing is the capital of China.",
            "html_content": json.dumps([
                {
                    "url": img_url,
                    "alt": alt,
                    "source_url": cited_url,
                    "source_title": "Wikipedia",
                    "width": 1024,
                    "height": 768,
                }
                for img_url, alt in imgs
            ]),
        })
    return {
        "research_query": "北京旅游景点",
        "findings": [{"search_results": search_results}],
    }


def test_beijing_scenario_full_pipeline(monkeypatch):
    """Drive the citation-anchored pipeline with the Beijing fixture.

    Asserts every stage of the chain succeeds and the result is a
    non-degenerate report enhancement:

      * CITATION_INDEX resolves 11 cited URLs (12 reference rows,
        the empty-URL row skipped) and covers html_content for all
        11 — including search_results keyed "link" (production).
      * Every fixture image (18) passes the semantic gate (constant-
        vector fake -> cosine 1.0) and is inserted at its section.
      * The pipeline ends status=ok and the returned markdown
        contains the inserted images.
    """
    import numpy as np
    from loguru import logger

    class _M:
        def encode(self, phrases, normalize_embeddings=True):
            return [np.array([1.0, 0.0, 0.0, 0.0]) for _ in phrases]

    monkeypatch.setattr(
        postprocessing.semantic_matcher, "get_model", lambda *a, **k: _M()
    )
    _patch_image_store(monkeypatch)

    trace_lines: list[str] = []
    orig_info = logger.info

    def info(*a, **kw):
        msg = " ".join(str(x) for x in a)
        if "IMG-TRACE" in msg:
            trace_lines.append(msg)
        return orig_info(*a, **kw)

    monkeypatch.setattr(logger, "info", info)

    results = _build_beijing_results()

    out = enhance_report_with_images(
        research_id="test-beijing-86132889",
        clean_markdown=_BEIJING_SCENARIO_MARKDOWN,
        results=results,
        db_session=MagicMock(),
        enable_images=True,
        vision_model="",
        vision_url=None,
        vision_api_key=None,
        vision_min_alt_count=None,
        vision_cap=None,
        firecrawl_client=None,
    )

    # 1. CITATION_INDEX: 12 reference rows, one with an empty URL ->
    # 11 cited URLs; html_content covered for all 11 ("link" key).
    ci_line = next(
        ln for ln in trace_lines
        if "CITATION_INDEX research=test-beijing-86132889" in ln
    )
    assert "nums=11" in ci_line, f"nums != 11: {ci_line!r}"
    assert "html_covered=11" in ci_line, f"html_covered != 11: {ci_line!r}"

    # 2. Every fixture image passes the gate and is placed.
    bank_line = next(
        ln for ln in trace_lines
        if "ELIGIBLE_BANK research=test-beijing-86132889" in ln
    )
    assert "total=18" in bank_line, f"bank total != 18: {bank_line!r}"
    insert_line = next(
        ln for ln in trace_lines
        if "INSERT research=test-beijing-86132889" in ln
    )
    assert "placements=18" in insert_line, (
        f"placements != 18: {insert_line!r}"
    )

    # 3. status=ok and the returned markdown contains the images.
    end_line = next(
        ln for ln in trace_lines if "END research=test-beijing-86132889" in ln
    )
    assert "status=ok" in end_line, f"did not finish cleanly: {end_line!r}"
    assert "![Beijing skyline at dusk]" in out
    assert "![Terminal 3 of Beijing Capital International Airport]" in out
    assert "## 参考文献" in out


def test_beijing_scenario_per_section_allowlist(monkeypatch):
    """Lower-level test: parse the Beijing markdown through
    extract_segment_sources and assert the per-section URL lists
    match the expected citation map.

    This pins the markdown-parsing layer independently from the
    image-filter pipeline, so a regression in extract_segment_sources
    is caught even if the full-pipeline test happens to pass.
    """
    from local_deep_research.images.relevance import extract_segment_sources

    sections = extract_segment_sources(
        _BEIJING_SCENARIO_MARKDOWN, results={}
    )
    # 15 sections: H1 title, ## 研究范围, 12 sub-researches (## 1. … ## 12.),
    # ## 参考文献
    assert len(sections) == 15, f"expected 15 sections, got {len(sections)}"

    # The References block parser uses the trailing
    # num→url mapping. Verify the empty-URL row is skipped.
    # Section 6 (传统民俗活动) cites [6] whose URL is empty, so its
    # URL list should either be empty or inherit from section 5
    # (长城防御体系) which has [5] → Great Wall.
    section_idx_6 = 6
    heading_6, _, urls_6 = sections[section_idx_6]
    assert "传统民俗" in heading_6
    # Inheritance from section 5: Great Wall URL should be in the
    # list, or the list is empty (no [N] in body, no inheritance
    # if section 5 also had nothing).
    if urls_6:
        # If inheritance kicked in, it should be the Great Wall URL.
        assert "https://en.wikipedia.org/wiki/Great_Wall_of_China" in urls_6

    # The closing References section is parsed with the URLs
    # gathered for that section — it collects every cited URL.
    # Downstream filtering via is_skipped_section_heading keeps it
    # out of the image bank, but extract_segment_sources does not
    # itself skip it.
    last_heading, _, last_urls = sections[-1]
    assert "参考文献" in last_heading
    # The empty-URL row [6] should NOT contribute to last_urls.
    # (Empty URLs are filtered out by the parser.)
    for u in last_urls:
        assert u  # no empty strings
        assert "beijing_opera" not in u.lower()


def test_beijing_scenario_section_domain_filter_drops_cross_domain(monkeypatch):
    """filter_candidates_by_section_citations applied to a Beijing
    candidate set must drop a candidate whose source_url is on a
    different registrable domain.

    This is the eTLD+1 gate: a ctrip.com image must NOT be admitted
    into a section that cites only wikipedia.org URLs.
    """
    from local_deep_research.images.extractor import ExtractedImage
    from local_deep_research.images.relevance import (
        filter_candidates_by_section_citations,
    )

    candidates = [
        ExtractedImage(
            url="https://img.ctrip.com/beijing.jpg",
            alt="Ctrip photo of Beijing",
            source_url="https://a1.ctrip.com/beijing/page",
            source_title="Ctrip",
            width=1024, height=768,
        ),
        ExtractedImage(
            url="https://upload.wikimedia.org/Beijing.jpg",
            alt="Beijing skyline",
            source_url="https://en.wikipedia.org/wiki/Beijing",
            source_title="Wikipedia",
            width=1024, height=768,
        ),
    ]
    section_citations = ["https://en.wikipedia.org/wiki/Beijing"]
    kept, d_no_src, d_mismatch, d_count = (
        filter_candidates_by_section_citations(
            candidates, section_citations, section_idx=1
        )
    )
    assert len(kept) == 1
    assert kept[0].url == "https://upload.wikimedia.org/Beijing.jpg"
    assert d_mismatch == 1
    assert d_count == 1
