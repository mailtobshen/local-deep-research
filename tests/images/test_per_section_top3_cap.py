def test_per_section_top3_cap_keeps_highest_scores():
    """When 5 candidates bind to one section with distinct scores, only
    the top-3 by score reach placements."""
    # Build a minimal bank + binding scenario where section 0 has 5
    # bindings with scores 0.9, 0.8, 0.7, 0.6, 0.5. After the cap,
    # placements for sec 0 must be exactly the 0.9/0.8/0.7 ones.
    #
    # This test exercises the placements-construction function in
    # isolation. If that logic is inline in enhance_report_with_images
    # (not a separate function), refactor it into a helper
    # `_build_placements(binding, bank_by_url, cap=3)` first, THEN
    # write this test against the helper. See Step 3.
    from local_deep_research.images.postprocessing import _build_placements
    binding = {
        "u1": [(1, 0, 0.90)],
        "u2": [(1, 0, 0.80)],
        "u3": [(1, 0, 0.70)],
        "u4": [(1, 0, 0.60)],
        "u5": [(1, 0, 0.50)],
    }
    class _Img:
        def __init__(self, url, alt): self.url, self.alt = url, alt
    bank_by_url = {
        "u1": _Img("u1", "a1"), "u2": _Img("u2", "a2"),
        "u3": _Img("u3", "a3"), "u4": _Img("u4", "a4"),
        "u5": _Img("u5", "a5"),
    }
    placements = _build_placements(binding, bank_by_url, cap=3)
    urls_in_sec0 = [u for (sidx, u, alt) in placements if sidx == 0]
    assert set(urls_in_sec0) == {"u1", "u2", "u3"}, (
        "top-3 by score must be kept; u4/u5 dropped"
    )


def test_per_section_under_3_all_kept():
    from local_deep_research.images.postprocessing import _build_placements
    binding = {"u1": [(1, 0, 0.9)], "u2": [(1, 0, 0.8)]}
    class _Img:
        def __init__(self, url, alt): self.url, self.alt = url, alt
    bank_by_url = {"u1": _Img("u1", "a1"), "u2": _Img("u2", "a2")}
    placements = _build_placements(binding, bank_by_url, cap=3)
    assert len(placements) == 2
