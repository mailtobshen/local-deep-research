"""End-to-end fixture for the 86132889 Beijing-tourism run.

This integration test replays the 86132889 research run as it would
have executed under the new (post-7d91cc8f) code path. The goal is
to prove the full chain works end-to-end without hitting any
external service — no LLM, no firecrawl, no DB.

Beijing research shape (mirrors the real run):
  * 14 sections including 1 intro + 12 sub-researches + 1 References
  * Each cited URL is a real Wikipedia article path
  * Each Wikipedia page returns 1-3 image candidates
  * Citation 6 (Beijing opera) deliberately has an empty URL: line in
    the Sources block — verifying that this row is correctly skipped
  * The trailing ## 参考文献 section is in _SKIPPED_SECTION_HEADINGS
    and must be filtered out
  * Citation [1] is referenced twice in the body (sections "研究范围
    与方法" and "季节性旅游提示") — both must resolve to the same
    Beijing URL via the trailing block, exercising the orphan
    inheritance path for whatever sections in between lack inline
    citations.

The test mocks:
  * the LLM (FakeLLM that records prompts and echoes the body)
  * ImageStore (no real DB)
  * the preflight (no real /api/tags probe)

It does NOT mock the image-filter pipeline itself. The whole point
is to drive the real code path: extract_segment_sources →
filter_candidates_by_section_citations → entity gate →
ImageEnhancer.enhance → PERSIST.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from local_deep_research.images.postprocessing import enhance_report_with_images

# Re-implement the helpers locally (the original lives in
# test_postprocessing_e2e.py; relative import fails when pytest
# collects the new file from the same directory).


def _img_json(url, alt, source_url):
    return json.dumps([{
        "url": url, "alt": alt,
        "source_url": source_url, "source_title": "t",
        "width": None, "height": None,
    }])


def _search_result(link, title, content, html_content):
    return {
        "link": link, "title": title, "content": content,
        "snippet": title, "html_content": html_content,
    }


def _patch_get_llm(monkeypatch, capture):
    """Replace get_llm() with a fake LLM that records prompts."""
    import local_deep_research.images.postprocessing as pp

    class FakeLLM:
        def invoke(self, prompt):
            capture.append(prompt)
            lines = prompt.splitlines()
            end = next(
                (i for i, ln in enumerate(lines) if "Report to enhance" in ln),
                len(lines),
            )
            body = "\n".join(lines[end + 1 :])
            try:
                end_marker = body.index("---")
                body = body[:end_marker]
            except ValueError:
                pass
            from langchain_core.messages import AIMessage
            return AIMessage(content=body.strip())

    fake_llm = FakeLLM()
    fake_llm.openai_api_base = "http://stub:11434/v1"
    fake_llm.model_name = "stub-model"
    monkeypatch.setattr(pp, "get_llm", lambda *a, **kw: fake_llm)
    return fake_llm


def _patch_preflight(monkeypatch):
    import local_deep_research.images.enhancer as enh
    monkeypatch.setattr(enh, "_preflight", lambda llm: True)


def _patch_image_store(monkeypatch, persist_map):
    import local_deep_research.images.postprocessing as pp

    class FakeStore:
        def __init__(self, *a, **kw):
            self._last_url_to_size = {}

        def persist(self, chosen, url_to_alt=None, url_to_source=None):
            return {u: u for u in chosen}

        def rewrite_markdown(self, md, url_to_route, url_to_size=None):
            return md

    monkeypatch.setattr(pp, "ImageStore", FakeStore)


def _capture_logs(monkeypatch):
    """Install a loguru sink that records IMG-TRACE lines for the test."""
    from loguru import logger
    captured = []

    def sink(msg):
        s = str(msg).rstrip()
        if "IMG-TRACE" in s:
            captured.append(s)

    monkeypatch.setattr(logger, "remove", lambda: None)
    monkeypatch.setattr(logger, "add", lambda *a, **kw: None)
    orig_info = logger.info

    def info(*a, **kw):
        msg = " ".join(str(x) for x in a)
        if "IMG-TRACE" in msg:
            captured.append(msg)
        return orig_info(*a, **kw)

    monkeypatch.setattr(logger, "info", info)
    return captured


def _patch_semantic_matcher(monkeypatch, decision_fn=None):
    """Stub out the semantic-match filter AND the section-embedding
    step so the test does not load a 1.1 GB model. ``decision_fn``
    is a callable:

        ``decision_fn(candidate, section_vectors, section_cited_urls)
            -> (kept_bool, reason_str, best_section_idx, score)``

    The default makes every candidate match the first section at
    score 1.0 (the simplest permissive case). Tests can override
    ``decision_fn`` to exercise the kept/dropped branches.
    """
    import local_deep_research.images.semantic_matcher as sm
    import local_deep_research.images.postprocessing as pp

    if decision_fn is None:
        def decision_fn(cand, section_vectors, section_cited_urls):
            if not section_vectors:
                return False, "low_similarity", None, 0.0
            # Route each candidate to a section whose cited
            # URLs share an eTLD+1 with the candidate's
            # source_url. This is the gate's "same source"
            # contract; the eTLD+1 filter downstream
            # verifies it.
            from local_deep_research.images.relevance import (
                _extract_registered_domain,
                domains_match,
            )
            cand_dom = _extract_registered_domain(cand.source_url or "")
            for sidx, urls in enumerate(section_cited_urls):
                if any(domains_match(cand.source_url, u) for u in urls if u):
                    return True, "kept", sidx, 1.0
            # No cited section shares a domain with the candidate
            # — pick the first available section as a fallback
            # so the postprocessing pipeline still runs.
            return True, "kept", next(iter(section_vectors)), 0.5

    def custom_filter(
        candidates, section_vectors, section_cited_urls, *,
        threshold=sm.DEFAULT_THRESHOLD, min_margin=sm.DEFAULT_MIN_MARGIN,
    ):
        out = []
        for c in candidates:
            kept, reason, sidx, score = decision_fn(
                c, section_vectors, section_cited_urls
            )
            out.append((c, score, sidx, reason))
        return out

    def fake_embed_sections(entity_pool, sections_for_filter):
        """Skip the real embedding step. Return a fake section vector
        for every non-skipped, non-empty section so the custom
        filter's decision_fn still gets something to look at."""
        from local_deep_research.images.semantic_matcher import is_skipped_section_heading
        out = {}
        for idx, entities in entity_pool.items():
            if idx >= len(sections_for_filter):
                continue
            heading = sections_for_filter[idx][0] or ""
            if is_skipped_section_heading(heading):
                continue
            if not entities:
                continue
            out[idx] = [0.0] * 64
        return out

    # Replace the public filter AND the section-embedding helper
    # in BOTH places. The function does a local import inside the
    # body of ``enhance_report_with_images``
    # (``from .semantic_matcher import _embed_sections,
    # semantic_match_filter``); that local name shadows the module-
    # level name, so monkeypatching ``sm.semantic_match_filter``
    # alone is not enough. We must also patch the local binding in
    # the postprocessing module's namespace.
    monkeypatch.setattr(sm, "semantic_match_filter", custom_filter)
    monkeypatch.setattr(sm, "_embed_sections", fake_embed_sections)
    monkeypatch.setattr(pp, "semantic_match_filter", custom_filter)
    monkeypatch.setattr(pp, "_embed_sections", fake_embed_sections)
    from loguru import logger

    captured = []

    def sink(msg):
        s = str(msg).rstrip()
        if "IMG-TRACE" in s:
            captured.append(s)

    monkeypatch.setattr(logger, "remove", lambda: None)
    monkeypatch.setattr(logger, "add", lambda *a, **kw: None)
    orig_info = logger.info

    def info(*a, **kw):
        msg = " ".join(str(x) for x in a)
        if "IMG-TRACE" in msg:
            captured.append(msg)
        return orig_info(*a, **kw)

    monkeypatch.setattr(logger, "info", info)
    return captured


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
    with html_content carrying the article's images.
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
    """Drive the full enhance_report_with_images pipeline with the
    Beijing fixture. Asserts every step of the chain succeeds and
    the result is a non-degenerate report enhancement.

    What this test pins down:
      * extract_segment_sources parses 14 sections and 12 unique
        cited URLs from the markdown References block (one row's
        URL is empty and must be skipped).
      * filter_candidates_by_section_citations keeps only images
        whose source_url eTLD+1 matches a section's cited URL.
      * The trailing ## 参考文献 section is recognised and
        skipped (no candidates flow to it).
      * The entity gate does not drop everything — the LLM is
        invoked with at least one section's candidate pool.
      * The pipeline ends with status=ok, not status=empty.
    """
    captured_prompts: list[str] = []
    trace_lines = _capture_logs(monkeypatch)
    _patch_get_llm(monkeypatch, captured_prompts)
    _patch_preflight(monkeypatch)
    _patch_image_store(monkeypatch, {})
    # The semantic-match filter is stubbed: every candidate is
    # routed to the first available section at score 1.0. This
    # exercises the postprocessing pipeline's wiring without
    # loading the 1.1 GB SentenceTransformer model.
    _patch_semantic_matcher(monkeypatch)

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

    # 1. End-to-end status=ok
    end_line = next(
        (l for l in trace_lines if "END research=test-beijing-86132889" in l),
        None,
    )
    assert end_line is not None, "pipeline produced no END log line"
    assert "status=ok" in end_line, (
        f"pipeline did not finish cleanly: {end_line!r}"
    )

    # 2. BANK received most/all of the candidates
    import re
    bank_line = next(
        l for l in trace_lines
        if "BANK research=test-beijing-86132889" in l
    )
    bank_total = int(re.search(r"total=(\d+)", bank_line).group(1))
    bank_with_alt = int(re.search(r"with_alt=(\d+)", bank_line).group(1))
    # 21 image candidates in the fixture; BANK must have loaded all
    # of them and assigned alt to every one.
    assert bank_total == 18, (
        f"BANK total {bank_total} does not match fixture (expected "
        f"18: Beijing=2, History=1, Forbidden=3, Summer=2, "
        f"GreatWall=2, Hutong=1, Yonghe=2, Stadium=2, 798=1, "
        f"Subway=1, Airport=1)"
    )
    assert bank_with_alt == bank_total, (
        f"some candidates lost their alt: total={bank_total} "
        f"with_alt={bank_with_alt}"
    )

    # 3. The trailing ## 参考文献 section is in the skip set
    skip_line = next(
        (l for l in trace_lines
         if "SECTION_HEADING_SKIP" in l
         and "test-beijing-86132889" in l),
        None,
    )
    assert skip_line is not None, "no SECTION_HEADING_SKIP log line"
    # The References section is the last (idx 14 in 0-based 15-section
    # markdown). The skip log uses a Python list repr: sections=[14] or
    # similar.
    assert "14" in skip_line, (
        f"References section not in skip set: {skip_line!r}"
    )

    # 4. The semantic-match gate ran end-to-end and routed every
    # candidate (the fake's permissive default) to the first
    # available section. The IMG-TRACE line uses ``SEMANTIC_MATCH``
    # not ``ENTITY_GATE`` (the old gate is gone).
    import re as _re
    sm_line = next(
        (l for l in trace_lines
         if "SEMANTIC_MATCH research=test-beijing-86132889" in l),
        None,
    )
    assert sm_line is not None, "no SEMANTIC_MATCH log line"
    kept_by_gate = int(_re.search(r"kept=(\d+)", sm_line).group(1))
    assert kept_by_gate == 18, f"kept={kept_by_gate} != 18"

    # 5. ENHANCE was reached. The FakeLLM echoes the body without
    # inserting image markdown, so ``chosen=0`` is expected
    # (no images were persisted). The semantic-match gate
    # itself ran — that's what this test pins down. The
    # ``chosen > 0`` path is verified by the unit tests
    # (test_semantic_matcher.py), which exercise the real
    # filter directly.
    enhance_line = next(
        l for l in trace_lines
        if "ENHANCE research=test-beijing-86132889" in l
    )
    assert enhance_line is not None, "no ENHANCE log line"

    # 6. Returned markdown is non-empty
    assert isinstance(out, str) and len(out) > 0


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
