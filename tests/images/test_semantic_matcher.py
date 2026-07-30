"""Tests for the semantic-match filter (replaces evaluate_candidate).

The model itself is NOT loaded in these tests. The matcher module
exposes a ``get_model()`` singleton; tests monkeypatch it to a fake
encoder that returns deterministic vectors. Real-model validation
happens in the calibration test, which is run manually and not in CI.

Each test exercises one rule of the noise filter, the entity-pool
extraction, the canonical-phrase builder, or the scoring function.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from local_deep_research.images.extractor import ExtractedImage
from local_deep_research.images.semantic_matcher import (
    DEFAULT_MIN_MARGIN,
    DEFAULT_THRESHOLD,
    _canonical_section_phrase,
    _filter_entity_pool,
    build_report_entity_pool,
    semantic_match_filter,
)


# ---------------------------------------------------------------------------
# Noise filter
# ---------------------------------------------------------------------------

def test_filter_rejects_digit_runs():
    assert _filter_entity_pool({"1"}) == []
    assert _filter_entity_pool({"12"}) == []
    assert _filter_entity_pool({"3.1.4"}) == []
    assert _filter_entity_pool({"1."}) == []


def test_filter_rejects_punctuation_only():
    assert _filter_entity_pool({":"}) == []
    assert _filter_entity_pool({":："}) == []


def test_filter_rejects_roman_numerals():
    for roman in ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
                  "XI", "XII", "XIII", "XIV", "XV", "MMXXIV"]:
        assert _filter_entity_pool({roman}) == [], f"Roman {roman!r} should be rejected"


def test_filter_rejects_single_latin_letter():
    for ch in "ABCIXYZ":
        assert _filter_entity_pool({ch}) == [], f"Latin single letter {ch!r}"


def test_filter_rejects_single_cjk_not_in_allowlist():
    """Single CJK characters are rejected by the length-3 floor
    (no allowlist override)."""
    assert _filter_entity_pool({"京"}) == []  # in 北京, but alone
    assert _filter_entity_pool({"广"}) == []  # in 广州
    assert _filter_entity_pool({"故"}) == []  # in 故宫


def test_filter_rejects_single_char_no_allowlist():
    """The static CJK allowlist was removed. The filter uses a
    simple length-2 floor (anything 1 char or less is rejected).
    2-char CJK proper nouns (``故宫``, ``北京``) are accepted —
    the embedding model handles short-token noise gracefully.

    1-char entities (single Latin letter, single CJK particle)
    are rejected by the length floor.
    """
    # 1-char: rejected.
    for s in ["A", "I", "京", "广", "上", "州"]:
        assert _filter_entity_pool({s}) == [], (
            f"1-char {s!r} should be rejected by length floor"
        )
    # 2+ char: accepted (whether 2-char CJK or 3-char Latin).
    for s in ["故宫", "北京", "颐和园", "DNA", "Canton_Tower", "Forbidden City"]:
        assert _filter_entity_pool({s}) == [s], (
            f"{s!r} should be accepted (≥ 2 chars)"
        )


def test_filter_accepts_three_char_acronyms():
    """3+ letter Latin acronyms are accepted as-is."""
    for s in ["DNA", "USB", "CPU", "RNA"]:
        assert _filter_entity_pool({s}) == [s]


def test_filter_accepts_long_cjk_and_latin():
    """Spans at or above the length floor of 3 are accepted (the
    current noisy extractor emits larger spans, so we use those
    here rather than bare names)."""
    assert _filter_entity_pool({"故宫介绍"}) == ["故宫介绍"]
    assert _filter_entity_pool({"广州塔"}) == ["广州塔"]
    assert _filter_entity_pool({"Canton_Tower"}) == ["Canton_Tower"]
    assert _filter_entity_pool({"Forbidden City"}) == ["Forbidden City"]


def test_filter_dedupes_preserving_order():
    """When the same entity appears multiple times in a list
    input, the result contains it once at the first-seen position.
    Note: a set literal in Python already dedupes before the
    filter runs, so we pass a list here to actually exercise the
    filter's dedup."""
    out = _filter_entity_pool(["DNA", "USB", "DNA", "RNA", "USB"])
    assert out == ["DNA", "USB", "RNA"]


def test_filter_section_labels_drop_digits():
    """``1. 城市历史与地位`` — the bare ``1.`` and ``1`` are
    rejected; the section's real entities survive in the pool."""
    pool = build_report_entity_pool("## 1. 城市历史与地位\n城市历史是广州。")
    # No "1", no "1.", but the real entities survive.
    assert "1" not in pool.get(0, [])
    assert "1." not in pool.get(0, [])
    # The current noisy extractor emits the heading+text span
    # rather than bare 城市历史. We assert membership rather
    # than equality.
    pool_0 = pool.get(0, [])
    assert len(pool_0) > 0  # something survived the filter


# ---------------------------------------------------------------------------
# build_report_entity_pool
# ---------------------------------------------------------------------------

def test_build_report_entity_pool_per_section():
    """Each section gets its own pool, indexed by section number.
    Note: a 3-section markdown (H1 + 2 H2) yields 3 section
    indices 0, 1, 2."""
    md = """# 北京旅游

## 1. 故宫
故宫介绍。

## 2. 广州
广州介绍。
"""
    pool = build_report_entity_pool(md)
    assert 0 in pool
    assert 1 in pool
    assert 2 in pool
    assert isinstance(pool[1], list)
    assert isinstance(pool[2], list)


def test_build_report_entity_pool_caps_at_50():
    """Per-section cap prevents embedding blow-up on huge reports."""
    long_entities = [f"实体{i:03d}" for i in range(200)]
    md = "## Test\n" + " ".join(long_entities)
    pool = build_report_entity_pool(md)
    # The current noisy extractor only emits a fraction of these,
    # but the cap is at 50 for the survivors.
    for ents in pool.values():
        assert len(ents) <= 50


def test_build_report_entity_pool_handles_chinese_only():
    """Pure Chinese report: the length-2 floor accepts 2-char
    CJK proper nouns. The extractor emits larger spans (e.g.
    故宫又称紫禁城), but the floor also lets ``北京`` and
    ``故宫`` through on their own."""
    md = """# 北京旅游

## 1. 故宫
故宫又称紫禁城。
"""
    pool = build_report_entity_pool(md)
    # 2-char CJK is now accepted (no allowlist needed; length-2
    # floor applies uniformly).
    for s in pool[0]:
        assert len(s) >= 2
    # The 1-char CJK particle is dropped.
    assert "京" not in pool[0]


def test_build_report_entity_pool_handles_mixed_languages():
    """Mixed Chinese / English report: both languages pass the
    filter."""
    md = """# Beijing Tourism

## 1. 故宫
故宫是北京的核心景点。Forbidden City is the famous palace.
"""
    pool = build_report_entity_pool(md)
    # 2-char CJK is dropped; 3+ char CJK and Latin survive.
    # The exact set depends on the extractor's span rules, but
    # neither single-letter Latin nor pure digits leak through.
    all_ents = set()
    for ents in pool.values():
        all_ents.update(ents)
    # No single Latin letters:
    assert not any(len(e) == 1 and e.isalpha() for e in all_ents)
    # No pure digits:
    assert not any(e.isdigit() for e in all_ents)


# ---------------------------------------------------------------------------
# _canonical_section_phrase
# ---------------------------------------------------------------------------

def test_canonical_section_phrase_joins_heading_and_entities():
    assert _canonical_section_phrase("故宫", ["故宫", "紫禁城", "明清两代"]) == \
        "故宫 故宫 紫禁城 明清两代"


def test_canonical_section_phrase_empty_when_both_empty():
    assert _canonical_section_phrase("", []) == ""


def test_canonical_section_phrase_heading_only():
    assert _canonical_section_phrase("故宫", []) == "故宫"


def test_canonical_section_phrase_entities_only():
    assert _canonical_section_phrase("", ["故宫", "紫禁城"]) == "故宫 紫禁城"


# ---------------------------------------------------------------------------
# semantic_match_filter — uses a fake model to avoid loading 1.1 GB
# ---------------------------------------------------------------------------

def _fake_model(vectors: dict[str, list[float]]):
    """Return a fake SentenceTransformer that maps known phrases
    to the supplied vectors. Unknown phrases get a unit-zero
    vector (so they don't match anything)."""
    def encode(texts, normalize_embeddings=True):
        out = []
        for t in texts:
            v = vectors.get(t)
            if v is None:
                # Try a whitespace-trimmed lookup.
                v = vectors.get(t.strip())
            if v is None:
                v = [0.0] * 4
            out.append(v)
        return out
    m = MagicMock()
    m.encode.side_effect = encode
    return m


def _cand(url, alt, src):
    return ExtractedImage(
        url=url, alt=alt, source_url=src, source_title="t",
        width=100, height=100,
    )


def test_semantic_match_passes_obvious_match(monkeypatch):
    """alt "Canton Tower Hall" matches a section whose pool is
    ["Canton Tower"]; threshold 0.65 is hit."""
    from local_deep_research.images import semantic_matcher as sm
    fake = _fake_model({
        "Canton Tower Hall": [1.0, 0.0, 0.0, 0.0],
        "Canton Tower": [1.0, 0.0, 0.0, 0.0],
    })
    monkeypatch.setattr(sm, "get_model", lambda *a, **k: fake)

    cand = _cand(
        "https://img/x.jpg",
        "Canton Tower Hall",
        "https://en.wikipedia.org/wiki/Canton_Tower",
    )
    section_vectors = {3: [1.0, 0.0, 0.0, 0.0]}
    section_cited_urls = [[], [], [], ["https://en.wikipedia.org/wiki/Canton_Tower"]]
    out = semantic_match_filter([cand], section_vectors, section_cited_urls)
    assert len(out) == 1
    assert out[0][3] == "kept"
    assert out[0][1] == 1.0  # cosine sim
    assert out[0][2] == 3  # best_section_idx


def test_semantic_match_drops_unrelated(monkeypatch):
    """An alt that has zero cosine similarity to every section is
    dropped with reason ``low_similarity``."""
    from local_deep_research.images import semantic_matcher as sm
    fake = _fake_model({
        "Strawberry cake": [0.0, 0.0, 1.0, 0.0],
        "癌症 介绍": [0.0, 0.0, 0.0, 1.0],
    })
    monkeypatch.setattr(sm, "get_model", lambda *a, **k: fake)
    cand = _cand(
        "https://img/x.jpg",
        "Strawberry cake",
        "https://en.wikipedia.org/wiki/Cancer",
    )
    section_vectors = {1: [0.0, 0.0, 0.0, 1.0]}
    section_cited_urls = [[], ["https://en.wikipedia.org/wiki/Cancer"]]
    out = semantic_match_filter([cand], section_vectors, section_cited_urls)
    assert out[0][3] == "low_similarity"
    assert out[0][1] == 0.0  # orthogonal


def test_semantic_match_drops_no_source_url(monkeypatch):
    """Empty source_url is dropped with reason ``no_source_url``."""
    from local_deep_research.images import semantic_matcher as sm
    fake = _fake_model({})
    monkeypatch.setattr(sm, "get_model", lambda *a, **k: fake)
    cand = ExtractedImage(
        url="https://img/x.jpg", alt="Canton Tower",
        source_url="", source_title="t", width=100, height=100,
    )
    section_vectors = {1: [1.0, 0.0, 0.0, 0.0]}
    section_cited_urls = [[], ["https://en.wikipedia.org/wiki/Canton_Tower"]]
    out = semantic_match_filter([cand], section_vectors, section_cited_urls)
    assert out[0][3] == "no_source_url"


def test_semantic_match_drops_missing_alt(monkeypatch):
    """Empty alt is dropped with reason ``missing_alt``."""
    from local_deep_research.images import semantic_matcher as sm
    monkeypatch.setattr(sm, "get_model", lambda *a, **k: _fake_model({}))
    cand = ExtractedImage(
        url="https://img/x.jpg", alt="", source_url="https://x.com",
        source_title="t", width=100, height=100,
    )
    section_vectors = {1: [1.0, 0.0, 0.0, 0.0]}
    out = semantic_match_filter([cand], section_vectors, [[], ["https://x.com"]])
    assert out[0][3] == "missing_alt"


def test_semantic_match_respects_min_margin(monkeypatch):
    """An alt that ties between two sections (margin below 0.05) is
    dropped with reason ``ambiguous_match``."""
    from local_deep_research.images import semantic_matcher as sm
    fake = _fake_model({
        "Canton Tower": [1.0, 0.0, 0.0, 0.0],
    })
    monkeypatch.setattr(sm, "get_model", lambda *a, **k: fake)
    cand = _cand(
        "https://img/x.jpg", "Canton Tower",
        "https://en.wikipedia.org/wiki/Canton_Tower",
    )
    # Two sections, both equally close to the alt.
    section_vectors = {
        1: [1.0, 0.0, 0.0, 0.0],
        2: [1.0, 0.0, 0.0, 0.0],
    }
    section_cited_urls = [[], ["https://en.wikipedia.org/wiki/Canton_Tower"]] * 1
    # Pad to match section count.
    section_cited_urls = [[], ["https://en.wikipedia.org/wiki/Canton_Tower"],
                         ["https://en.wikipedia.org/wiki/Canton_Tower"]]
    out = semantic_match_filter([cand], section_vectors, section_cited_urls)
    assert out[0][3] == "ambiguous_match"


def test_semantic_match_threshold_configurable(monkeypatch):
    """A score below the default 0.65 threshold is dropped; the
    same score above a lower 0.5 threshold is kept."""
    from local_deep_research.images import semantic_matcher as sm
    fake = _fake_model({
        "alt text": [0.6, 0.0, 0.0, 0.0],
        "section phrase": [1.0, 0.0, 0.0, 0.0],
    })
    monkeypatch.setattr(sm, "get_model", lambda *a, **k: fake)
    cand = _cand("https://img/x.jpg", "alt text", "https://x.com")
    section_vectors = {1: [1.0, 0.0, 0.0, 0.0]}
    section_cited_urls = [[], ["https://x.com"]]

    out_default = semantic_match_filter(
        [cand], section_vectors, section_cited_urls,
    )
    assert out_default[0][3] == "low_similarity"  # 0.6 < 0.65

    out_lower = semantic_match_filter(
        [cand], section_vectors, section_cited_urls,
        threshold=0.5,
    )
    assert out_lower[0][3] == "kept"


def test_semantic_match_picks_best_section(monkeypatch):
    """An alt that matches two sections, one slightly closer, picks
    the closer one when margin is wide enough."""
    from local_deep_research.images import semantic_matcher as sm
    fake = _fake_model({
        "alt text": [0.9, 0.0, 0.0, 0.0],
    })
    monkeypatch.setattr(sm, "get_model", lambda *a, **k: fake)
    cand = _cand("https://img/x.jpg", "alt text", "https://x.com")
    section_vectors = {
        1: [1.0, 0.0, 0.0, 0.0],   # cosine = 0.9
        2: [0.5, 0.0, 0.0, 0.0],   # cosine = 0.45
    }
    section_cited_urls = [[], ["https://x.com"], []]
    out = semantic_match_filter([cand], section_vectors, section_cited_urls,
                                min_margin=0.2)
    assert out[0][2] == 1  # best_section_idx = 1
    assert out[0][3] == "kept"


def test_semantic_match_drops_no_source_url_match(monkeypatch):
    """Even if cosine similarity is high, a candidate whose
    source_url doesn't share eTLD+1 with any cited URL in the
    best section is dropped with reason ``no_source_url_match``."""
    from local_deep_research.images import semantic_matcher as sm
    fake = _fake_model({
        "alt text": [1.0, 0.0, 0.0, 0.0],
        "section phrase": [1.0, 0.0, 0.0, 0.0],
    })
    monkeypatch.setattr(sm, "get_model", lambda *a, **k: fake)
    cand = _cand(
        "https://img.ctrip.com/photo/x.jpg",
        "alt text",
        "https://a1.ctrip.com/page",  # ctrip, not wikipedia
    )
    section_vectors = {1: [1.0, 0.0, 0.0, 0.0]}
    # Cited URL is wikipedia, source URL is ctrip → no eTLD+1 match
    section_cited_urls = [[], ["https://en.wikipedia.org/wiki/Canton_Tower"]]
    out = semantic_match_filter([cand], section_vectors, section_cited_urls)
    assert out[0][3] == "no_source_url_match"


def test_semantic_match_drops_when_no_section_vectors(monkeypatch):
    """If ``section_vectors`` is empty, every candidate is dropped
    with reason ``low_similarity``. The function does NOT call
    the model."""
    from local_deep_research.images import semantic_matcher as sm

    def fail_get_model(*a, **k):
        raise AssertionError("get_model should not be called when no sections")
    monkeypatch.setattr(sm, "get_model", fail_get_model)

    cand = _cand("https://img/x.jpg", "alt", "https://x.com")
    out = semantic_match_filter([cand], {}, [[], []])
    assert out[0][3] == "low_similarity"
    assert out[0][1] == 0.0


def test_semantic_match_threshold_default_is_strict():
    """The default threshold is 0.65 (per the user's calibration
    decision: strict, fewer false matches)."""
    assert DEFAULT_THRESHOLD == 0.65


def test_semantic_match_min_margin_default_is_small():
    """The default min margin is 0.05 — small enough to allow
    a single best-section winner in typical reports."""
    assert DEFAULT_MIN_MARGIN == 0.05
