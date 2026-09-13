"""A: Sources block must only keep body-cited sources.

Root cause (research a4c512fa, 2026-09-13): the langgraph
synthesis path built the trailing Sources block from ALL
accumulated search results (188 rows) regardless of whether the
body ever cited them, so irrelevant SearXNG noise (Mattress Firm,
PIGAV, platform.deepseek.com ...) survived into 参考文献.
"""

from local_deep_research.text_optimization.citation_formatter import (
    extract_cited_targets,
    filter_links_to_cited,
)
from local_deep_research.utilities.search_utilities import (
    canonical_url_key,
)


def _link(idx, url, title="T"):
    return {"title": title, "url": url, "index": str(idx)}


LINKS = [
    _link(1, "https://en.wikipedia.org/wiki/Nury_Turkel"),
    _link(2, "https://pigav.ws/", "PIGAV 朱古力"),
    _link(3, "https://platform.deepseek.com/"),
    _link(4, "https://outlook.cloud.microsoft/"),
]


def test_extract_targets_hyperlink_and_plain():
    body = (
        "Text [[1]](https://en.wikipedia.org/wiki/Nury_Turkel) mid "
        "\\[2\\](https://pigav.ws/) tail [3] end"
    )
    nums, urls = extract_cited_targets(body)
    assert nums == {1, 2, 3}  # hyperlink numbers (incl. [[1]]) + plain
    assert canonical_url_key(
        "https://en.wikipedia.org/wiki/Nury_Turkel"
    ) in urls
    assert canonical_url_key("https://pigav.ws/") in urls


def test_extract_targets_group_citation():
    nums, _ = extract_cited_targets("claims [1, 4] here")
    assert nums == {1, 4}


def test_filter_keeps_only_cited():
    body = "Only [[1]](https://en.wikipedia.org/wiki/Nury_Turkel) cited"
    kept = filter_links_to_cited(LINKS, body)
    assert [l["url"] for l in kept] == [
        "https://en.wikipedia.org/wiki/Nury_Turkel"
    ]


def test_filter_by_plain_number_keeps_row():
    body = "Cites [2] only"
    kept = filter_links_to_cited(LINKS, body)
    assert [l["url"] for l in kept] == ["https://pigav.ws/"]


def test_filter_no_citations_returns_all():
    body = "No citations at all"
    assert filter_links_to_cited(LINKS, body) == LINKS


def test_filter_utm_variant_hyperlink_matches_canonical():
    body = "[[4]](https://outlook.cloud.microsoft/?utm_source=x)"
    kept = filter_links_to_cited(LINKS, body)
    assert [l["url"] for l in kept] == ["https://outlook.cloud.microsoft/"]
