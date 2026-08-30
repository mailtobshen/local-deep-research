"""Per-section cap tests, updated for the e14f3600 spread policy.

2026-08-30 policy: with spread enabled (the production clearnet path),
the effective per-section cap is 1 and each URL is seated once; the
overflow joins the spread pool. Legacy (spread=False) keeps the
explicit ``cap`` argument — these tests pin BOTH behaviours.
"""

from local_deep_research.images.postprocessing import _build_placements


class _Img:
    def __init__(self, url, alt):
        self.url, self.alt = url, alt


def test_legacy_cap3_keeps_highest_scores():
    """spread=False honours cap=3: top-3 by score kept, u4/u5 dropped."""
    binding = {
        "u1": [(1, 0, 0.90)],
        "u2": [(1, 0, 0.80)],
        "u3": [(1, 0, 0.70)],
        "u4": [(1, 0, 0.60)],
        "u5": [(1, 0, 0.50)],
    }
    bank_by_url = {u: _Img(u, f"a{i}") for i, u in enumerate(binding, 1)}
    placements = _build_placements(binding, bank_by_url, cap=3, spread=False)
    urls_in_sec0 = [u for (sidx, u, _alt) in placements if sidx == 0]
    assert set(urls_in_sec0) == {"u1", "u2", "u3"}, (
        "top-3 by score must be kept; u4/u5 dropped"
    )


def test_legacy_under_3_all_kept():
    binding = {"u1": [(1, 0, 0.9)], "u2": [(1, 0, 0.8)]}
    bank_by_url = {"u1": _Img("u1", "a1"), "u2": _Img("u2", "a2")}
    placements = _build_placements(binding, bank_by_url, cap=3, spread=False)
    assert len(placements) == 2


def test_spread_mode_caps_section_at_one():
    """spread=True (production clearnet): one seat per section — the
    highest-scoring candidate stays, the rest join the spread pool."""
    binding = {
        "u1": [(1, 0, 0.90)],
        "u2": [(1, 0, 0.80)],
        "u3": [(1, 0, 0.70)],
    }
    bank_by_url = {u: _Img(u, f"a{i}") for i, u in enumerate(binding, 1)}
    placements = _build_placements(
        binding,
        bank_by_url,
        cap=3,
        spread=True,
        # no alt_vecs: spread can't re-seat, so overflow simply drops
        num_to_url={1: "https://s.example.com"},
        section_to_nums={0: [1]},
        section_vecs={0: [1.0, 0.0]},
        section_phrases={0: "sec0"},
        alt_vecs={},
        spread_relaxed_threshold=0.0,
    )
    urls_in_sec0 = [u for (sidx, u, _a) in placements if sidx == 0]
    assert urls_in_sec0 == ["u1"], "only the top-score seat stays"
