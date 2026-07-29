# tests/images/test_per_section_domain_filter.py
"""Per-section eTLD+1 domain filter: hard same-registered-domain gate."""
from types import SimpleNamespace

from local_deep_research.images.bank import ImageBank
from local_deep_research.images.enhancer import ImageEnhancer
from local_deep_research.images.extractor import ExtractedImage
from local_deep_research.images.relevance import (
    _candidates_for_section,
    _extract_registered_domain,
    build_section_allowed_domains,
    domains_match,
    filter_candidates_by_section_citations,
)
from local_deep_research.images.vision import VisionDescriber


def _img(url, source_url):
    return ExtractedImage(
        url=url, alt="x",
        source_url=source_url, source_title="t",
        width=None, height=None,
    )


# ---- _extract_registered_domain ----

def test_extract_registered_domain_basic():
    assert _extract_registered_domain("https://a1.ctrip.com/x.html") == "ctrip.com"
    assert _extract_registered_domain("https://www.example.com/path") == "example.com"
    assert _extract_registered_domain("https://bbc.co.uk/news") == "bbc.co.uk"
    assert _extract_registered_domain("") == ""
    # A URL with no extractable hostname → ""
    assert _extract_registered_domain("https://") == ""


# ---- _extract_registered_domain boundary cases ----

def test_extract_registered_domain_strips_explicit_port():
    assert _extract_registered_domain("https://a.ctrip.com:8080/x") == "ctrip.com"
    assert _extract_registered_domain("http://192.168.1.1:8080/x") == "192.168.1.1"


def test_extract_registered_domain_handles_userinfo():
    assert _extract_registered_domain("https://user:pass@a.ctrip.com/x") == "ctrip.com"


def test_extract_registered_domain_handles_path_query_fragment():
    assert _extract_registered_domain(
        "https://a.ctrip.com/path?q=1&z=2#anchor"
    ) == "ctrip.com"


def test_extract_registered_domain_is_lowercase():
    assert _extract_registered_domain("HTTPS://A1.CTRIP.COM/x") == "ctrip.com"


def test_extract_registered_domain_idn_chinese():
    """Punycode / Unicode IDN domains come back as eTLD+1 with the
    Chinese label (tldextract normalises to the Unicode form)."""
    assert _extract_registered_domain("https://例子.cn/x") == "例子.cn"


def test_extract_registered_domain_ip_urls():
    """IP literals are returned verbatim. They will not match any
    registered-domain entry on the allow-list, which is the correct
    fail-closed behavior — IPs are not part of the citation graph."""
    assert _extract_registered_domain("http://127.0.0.1/x.jpg") == "127.0.0.1"
    assert _extract_registered_domain("http://[::1]/x.jpg") == "[::1]"
    assert _extract_registered_domain("http://[2001:db8::1]/x.jpg") == "[2001:db8::1]"


def test_extract_registered_domain_strips_whitespace():
    """Without explicit stripping, "  https://x.com  " was tokenised
    by tldextract into "https" as the registered domain. Must be
    stripped so the real domain is extracted."""
    assert _extract_registered_domain("  https://a.ctrip.com/x  ") == "ctrip.com"


def test_extract_registered_domain_rejects_control_bytes():
    """Embedded NUL or other control chars confuse downstream URL
    parsers. We never expect them in real search-result URLs — fail
    closed with empty string."""
    assert _extract_registered_domain("https://a.ctrip.com\x00.com/x") == ""
    assert _extract_registered_domain("https://a.ctrip.com\x07.com/x") == ""


def test_extract_registered_domain_localhost_no_tld():
    """`localhost` is a single-label host with no eTLD+1; tldextract
    returns it as-is. Will not match any registered-domain allow-list
    entry — caller treats as unknown."""
    assert _extract_registered_domain("http://localhost/x") == "localhost"


def test_extract_registered_domain_extreme_length():
    """A 2000+ char URL should not blow up; tldextract parses the
    host regardless of path length."""
    long_path = "a" * 2000
    assert _extract_registered_domain(
        f"https://a.ctrip.com/{long_path}"
    ) == "ctrip.com"


# ---- domains_match (eTLD+1-based pure-domain comparison) ----

def test_domains_match_ctrip_subdomains_collapse():
    """Distributed image CDNs under the same operator match.

    This is the original motivation for the helper: the per-section
    eTLD+1 allow-list collapses a1.ctrip.com and b.ctrip.com to the
    same registrable unit, so a Canton-Tower image hosted on either
    subdomain is accepted when the section cites ctrip.com once.
    """
    assert domains_match(
        "https://a1.ctrip.com/guide/canton-tower",
        "https://b.ctrip.com/photo-of-tower",
    ) is True
    assert domains_match(
        "https://a1.ctrip.com/x", "https://ctrip.com"
    ) is True


def test_domains_match_wikipedia_subdomain_variants():
    """en/zh/ja wikipedia subdomains are the same source."""
    assert domains_match(
        "https://en.wikipedia.org/wiki/Canton_Tower",
        "https://zh.wikipedia.org/wiki/广州塔",
    ) is True


def test_domains_match_rejects_pedia_trap():
    """``wikipedia.org`` and ``pedia.org`` share the SLD ``pedia`` as
    a substring accident but their eTLD+1s differ, so they must NOT
    match. Catches a naive right-of-dot-suffix implementation."""
    assert domains_match(
        "https://wikipedia.org", "https://pedia.org"
    ) is False


def test_domains_match_rejects_different_registrable():
    """``bbc.co.uk`` and ``bbc.com`` are different registrable units
    (different public suffixes), not the same source."""
    assert domains_match(
        "https://bbc.co.uk", "https://bbc.com"
    ) is False


def test_domains_match_ignores_path_query_fragment():
    """Path / query / fragment / case / scheme are not part of the
    domain — they should not affect the result."""
    assert domains_match(
        "https://x.com",
        "https://x.com/path?query=1#frag",
    ) is True
    assert domains_match(
        "HTTPS://X.COM/x",
        "https://x.com/y",
    ) is True


def test_domains_match_fail_closed_on_bad_input():
    """An unparseable URL cannot be allowed through; returns False."""
    assert domains_match("", "https://x.com") is False
    assert domains_match("https://x.com", "") is False
    assert domains_match("not a url", "https://x.com") is False
    assert domains_match("https://x.com", "also not a url") is False
    # Embedded control byte: rejected by _extract_registered_domain.
    assert domains_match(
        "https://x.com", "https://x.com\x00.com"
    ) is False


# ---- build_section_allowed_domains ----

def test_build_section_allowed_domains_collapses_subdomains():
    citations = [
        ["https://a1.ctrip.com/x", "https://a2.ctrip.com/y"],
        ["https://example.com/z"],
        [],
    ]
    out = build_section_allowed_domains(citations)
    assert out == {0: {"ctrip.com"}, 1: {"example.com"}, 2: set()}


# ---- _candidates_for_section ----

def test_candidates_for_section_keeps_matching_domain():
    img = _img("https://img.ctrip.com/x.jpg", "https://a1.ctrip.com/page")
    kept, dropped_no_source, dropped_domain_mismatch = _candidates_for_section(
        [img], {"ctrip.com"}, section_idx=0
    )
    assert len(kept) == 1
    assert dropped_no_source == 0
    assert dropped_domain_mismatch == 0


def test_candidates_for_section_drops_mismatched_domain():
    img = _img("https://other.com/x.jpg", "https://other.com/page")
    kept, dropped_no_source, dropped_domain_mismatch = _candidates_for_section(
        [img], {"ctrip.com"}, section_idx=0
    )
    assert kept == []
    assert dropped_no_source == 0
    assert dropped_domain_mismatch == 1


def test_candidates_for_section_drops_empty_source_url():
    img = _img("https://x.com/x.jpg", "")
    kept, dropped_no_source, dropped_domain_mismatch = _candidates_for_section(
        [img], {"x.com"}, section_idx=0
    )
    assert kept == []
    assert dropped_no_source == 1
    assert dropped_domain_mismatch == 0


def test_candidates_for_section_drops_all_when_allowed_empty():
    img = _img("https://a.com/x.jpg", "https://a.com/page")
    kept, dropped_no_source, dropped_domain_mismatch = _candidates_for_section(
        [img], set(), section_idx=0
    )
    assert kept == []
    assert dropped_no_source == 0
    assert dropped_domain_mismatch == 1


# ---- filter_candidates_by_section_citations (single-pass filter) ----

def test_filter_candidates_by_section_citations_keeps_matching_domain():
    """A cited URL set → eTLD+1 set is matched against each
    candidate's source_url eTLD+1 in one pass. Subdomain variants
    on both sides collapse via tldextract."""
    cands = [
        _img("https://img.ctrip.com/a.jpg", "https://a1.ctrip.com/x"),
        _img("https://img.example.com/b.jpg", "https://example.com/y"),
    ]
    citations = ["https://b.ctrip.com/photo"]
    kept, d_no_src, d_mismatch, d_count = filter_candidates_by_section_citations(
        cands, citations, section_idx=3
    )
    assert [c.url for c in kept] == ["https://img.ctrip.com/a.jpg"]
    assert d_no_src == 0
    assert d_mismatch == 1
    assert d_count == 1  # one eTLD+1 in citations


def test_filter_candidates_by_section_citations_drops_empty_source_url():
    """A candidate whose source_url is empty / un-parseable is
    dropped (fail-closed)."""
    cands = [
        _img("https://img.x.com/1.jpg", ""),
    ]
    kept, d_no_src, d_mismatch, d_count = filter_candidates_by_section_citations(
        cands, ["https://x.com/page"], section_idx=4
    )
    assert kept == []
    assert d_no_src == 1
    assert d_mismatch == 0
    assert d_count == 1


def test_filter_candidates_by_section_citations_empty_citations_means_empty_pool():
    """If the section has no parseable citations, the section comes
    out image-free. Same fail-closed contract as
    build_section_allowed_domains returning an empty set."""
    cands = [
        _img("https://img.x.com/1.jpg", "https://x.com/page"),
    ]
    kept, d_no_src, d_mismatch, d_count = filter_candidates_by_section_citations(
        cands, [], section_idx=5
    )
    assert kept == []
    assert d_no_src == 0
    assert d_mismatch == 1
    assert d_count == 0


def test_filter_candidates_by_section_citations_multiple_cited_domains():
    """Multiple cited URLs mapping to multiple eTLD+1s produce a
    matching cited_domain_count."""
    cands = [
        _img("https://img.ctrip.com/a.jpg", "https://a1.ctrip.com/x"),
        _img("https://img.wikipedia.com/b.jpg", "https://en.wikipedia.org/wiki/X"),
    ]
    citations = [
        "https://b.ctrip.com/y",
        "https://en.wikipedia.org/wiki/Y",
    ]
    kept, _, _, d_count = filter_candidates_by_section_citations(
        cands, citations, section_idx=6
    )
    assert len(kept) == 2
    assert d_count == 2  # ctrip.com + wikipedia.org


def test_filter_candidates_by_section_citations_pedia_trap():
    """``wikipedia.org`` and ``pedia.org`` do not share an eTLD+1,
    so a wikipedia image is rejected when the section cites only
    ``pedia.org`` (and vice versa). Locks in the tldextract-based
    discrimination of substring SLD overlaps."""
    cands = [
        _img("https://img.wikipedia.com/a.jpg", "https://wikipedia.org"),
    ]
    kept, _, _, _ = filter_candidates_by_section_citations(
        cands, ["https://pedia.org"], section_idx=7
    )
    assert kept == []


# ---- ImageEnhancer.enhance integration ----

class _CaptureLLM:
    """Records prompts so we can assert per-section candidate pools."""

    def __init__(self):
        self.calls = []

    def invoke(self, prompt):
        self.calls.append(prompt)
        # Extract the section body (between --- markers after
        # "Report to enhance:"), and return it untouched.
        lines = prompt.splitlines()
        end = next(
            (i for i, line in enumerate(lines) if "Report to enhance" in line),
            len(lines),
        )
        body_lines = lines[end + 1 :]
        try:
            end_marker = body_lines.index("---")
        except ValueError:
            end_marker = len(body_lines)
        body = "\n".join(body_lines[:end_marker]).strip()
        return SimpleNamespace(content=body)


def _enhancer(llm):
    return ImageEnhancer(llm, VisionDescriber(model_name=""))


def test_enhance_per_section_partitioning_visible_to_llm():
    """Section A sees A's pool only; Section B sees B's pool only."""
    img_a = _img("https://img.ctrip.com/a.jpg", "https://a1.ctrip.com/p")
    img_b = _img("https://img.example.com/b.jpg", "https://b.example.com/p")
    bank = ImageBank()
    bank.add([img_a, img_b])

    llm = _CaptureLLM()
    per_section = {
        0: [img_a],
        1: [img_b],
    }
    md = "# Section A\n\nbody a\n\n## Section B\n\nbody b"
    _enhancer(llm).enhance(md, bank, per_section_candidates=per_section)

    assert len(llm.calls) == 2
    p0, p1 = llm.calls
    assert "img.ctrip.com/a.jpg" in p0
    assert "img.example.com/b.jpg" not in p0
    assert "img.example.com/b.jpg" in p1
    assert "img.ctrip.com/a.jpg" not in p1


def test_enhance_backward_compat_no_third_arg():
    """When per_section_candidates is None, the LLM sees the full pool
    on every section (legacy behavior)."""
    img_a = _img("https://img.ctrip.com/a.jpg", "https://a1.ctrip.com/p")
    img_b = _img("https://img.example.com/b.jpg", "https://b.example.com/p")
    bank = ImageBank()
    bank.add([img_a, img_b])

    llm = _CaptureLLM()
    md = "# Section A\n\nbody a\n\n## Section B\n\nbody b"
    _enhancer(llm).enhance(md, bank)

    assert len(llm.calls) == 2
    for prompt in llm.calls:
        assert "img.ctrip.com/a.jpg" in prompt
        assert "img.example.com/b.jpg" in prompt


def test_enhance_single_section_empty_pool_skips_llm():
    """A section with no candidates must not trigger an LLM call;
    the section markdown is returned unchanged. Saves ~2 s per
    empty section."""
    bank = ImageBank()
    bank.add([_img("https://a.com/x.jpg", "https://a.com/p")])

    llm = _CaptureLLM()
    per_section = {0: []}
    md = "# Solo\n\nbody"
    out = _enhancer(llm).enhance(md, bank, per_section_candidates=per_section)

    assert llm.calls == []
    assert out == md


def test_enhance_multi_section_one_pool_empty_skips_that_section():
    """When only one of two per-section pools is empty, the LLM
    is called for the populated section only; the empty section is
    returned unchanged and the other section is enhanced."""
    img_a = _img("https://img.ctrip.com/a.jpg", "https://a1.ctrip.com/p")
    bank = ImageBank()
    bank.add([img_a])

    llm = _CaptureLLM()
    per_section = {0: [img_a], 1: []}
    md = "# Section A\n\nbody a\n\n## Section B\n\nbody b"
    out = _enhancer(llm).enhance(md, bank, per_section_candidates=per_section)

    assert len(llm.calls) == 1
    assert "body a" in out
    assert "body b" in out


# ---- Quick-summary path (single section, document-level filter) ----

def test_enhance_quick_summary_filters_to_cited_domains():
    """Quick-summary markdown typically has no headings → a single
    'section' (idx=0). The eTLD+1 filter must still apply at the
    document level: only images whose source_url eTLD+1 matches one
    of the document's cited domains reach the LLM prompt.

    Simulates the path: bank contains 3 candidates; per_section_candidates
    has only the kept pool at idx=0 (postprocessing would compute this
    from _keep_per_section + allowed_per_section).
    """
    img_ctrip = _img(
        "https://img.ctrip.com/tower.jpg", "https://a1.ctrip.com/p"
    )
    img_other = _img(
        "https://img.other.com/x.jpg", "https://other.com/p"
    )
    img_orphan = _img(
        "https://cdn.com/x.jpg", ""  # no source_url → fail-closed drop
    )
    bank = ImageBank()
    bank.add([img_ctrip, img_other, img_orphan])

    # postprocessing.py would have built this from _keep_per_section
    # intersected with the allowed-domain set for the document:
    # only img_ctrip survives.
    per_section = {0: [img_ctrip]}

    # Quick-summary markdown: prose, no headings.
    md = "Canton Tower is a 604m landmark in Guangzhou. Visitors enjoy views."

    llm = _CaptureLLM()
    _enhancer(llm).enhance(md, bank, per_section_candidates=per_section)

    # Exactly one LLM call (single section path).
    assert len(llm.calls) == 1
    p0 = llm.calls[0]
    # Cited-domain image is in the pool.
    assert "img.ctrip.com/tower.jpg" in p0
    # Non-cited-domain image is filtered out.
    assert "img.other.com/x.jpg" not in p0
    # Orphan (no source_url) is filtered out.
    assert "cdn.com/x.jpg" not in p0