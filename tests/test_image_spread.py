"""Tests for the image spread pass (research e14f3600 follow-up).

Policy: one image per section; over-cap images adopted from a cite are
re-seated into OTHER sections citing the SAME cite_num whose
alt-vs-section cosine clears a relaxed threshold (main − 0.15), front
sections first.
"""

import pytest

from local_deep_research.images.postprocessing import (
    _build_placements,
    _SPREAD_THRESHOLD_RELAX,
)


class _Img:
    def __init__(self, url, alt, area=40000):
        self.url = url
        self.alt = alt
        self.source_url = "https://src.example.com"
        self.source_title = "Source"
        self.width = 200
        self.height = area // 200


def _vecs(vec_by_sec):
    # identity-ish vectors: build simple orthogonal proxies where
    # cosine(v[a], v[b]) ≈ 1.0 if same key else ~0. e2e-lite via
    # 2-d vectors on the unit circle.
    import math

    n = len(vec_by_sec)
    out = {}
    for i, k in enumerate(vec_by_sec):
        ang = 2 * math.pi * i / max(n, 1)
        out[k] = [math.cos(ang), math.sin(ang)]
    return out


class TestSpreadPass:
    def _run(self, binding, bank, sec_vecs, alt_vecs, nums_by_sec,
             threshold=0.2, relax=None):
        return _build_placements(
            binding,
            bank,
            cap=1,
            spread=True,
            num_to_url={1: "https://src.example.com"},
            section_to_nums=nums_by_sec,
            section_vecs=sec_vecs,
            section_phrases={s: f"sec{s}" for s in nums_by_sec},
            alt_vecs=alt_vecs,
            spread_relaxed_threshold=(
                max(0.0, threshold - _SPREAD_THRESHOLD_RELAX)
                if relax is None else relax
            ),
        )

    def test_overflow_spreads_to_same_cite_section(self):
        # sec0 cites cite 1 and has 2 adopted images (cap=1); sec2
        # also cites cite 1, has no placement, and its vector is
        # near-identical to img B's alt vector → B moves to sec2.
        imgA, imgB = _Img("https://x/a.jpg", "A"), _Img("https://x/b.jpg", "B")
        bank = {i.url: i for i in (imgA, imgB)}
        binding = {
            "https://x/a.jpg": [(1, 0, 0.9)],
            "https://x/b.jpg": [(1, 0, 0.8)],
        }
        sec_vecs = {0: [1.0, 0.0], 2: [1.0, 0.0]}
        alt_vecs = {
            "https://x/a.jpg": [1.0, 0.0],
            "https://x/b.jpg": [0.99, 0.01],
        }
        nums_by_sec = {0: [1], 2: [1]}
        placements = self._run(binding, bank, sec_vecs, alt_vecs, nums_by_sec)
        secs = sorted({p[0] for p in placements})
        assert secs == [0, 2], placements
        # B (overflow) is the one that moved.
        assert (2, "https://x/b.jpg") in [(p[0], p[1]) for p in placements]

    def test_front_section_priority(self):
        # secs 2 and 5 both cite cite 1, both empty, both similar;
        # the overflow image must land in the EARLIER section.
        imgA, imgB = _Img("https://x/a.jpg", "A"), _Img("https://x/b.jpg", "B")
        bank = {i.url: i for i in (imgA, imgB)}
        binding = {
            "https://x/a.jpg": [(1, 0, 0.9)],
            "https://x/b.jpg": [(1, 0, 0.8)],
        }
        sec_vecs = {0: [1.0, 0.0], 2: [1.0, 0.0], 5: [1.0, 0.0]}
        alt_vecs = {
            "https://x/a.jpg": [1.0, 0.0],
            "https://x/b.jpg": [1.0, 0.0],
        }
        nums_by_sec = {0: [1], 2: [1], 5: [1]}
        placements = self._run(binding, bank, sec_vecs, alt_vecs, nums_by_sec)
        assert (2, "https://x/b.jpg") in [(p[0], p[1]) for p in placements]
        assert 5 not in {p[0] for p in placements}

    def test_no_spread_to_different_cite_section(self):
        # sec2 cites a DIFFERENT number — overflow must NOT move there
        # even with perfect similarity (provenance rule) — it FALLS
        # BACK to its original section instead of being dropped.
        imgA, imgB = _Img("https://x/a.jpg", "A"), _Img("https://x/b.jpg", "B")
        bank = {i.url: i for i in (imgA, imgB)}
        binding = {
            "https://x/a.jpg": [(1, 0, 0.9)],
            "https://x/b.jpg": [(1, 0, 0.8)],
        }
        sec_vecs = {0: [1.0, 0.0], 2: [1.0, 0.0]}
        alt_vecs = {"https://x/b.jpg": [1.0, 0.0]}
        nums_by_sec = {0: [1], 2: [7]}  # sec2 cites 7, not 1
        placements = self._run(binding, bank, sec_vecs, alt_vecs, nums_by_sec)
        # Both adopted images keep their original home (sec 0).
        assert (0, "https://x/a.jpg") in [(p[0], p[1]) for p in placements]
        assert (0, "https://x/b.jpg") in [(p[0], p[1]) for p in placements]

    def test_below_relaxed_threshold_falls_back_home(self):
        # Target section orthogonal to the alt vector → sim ~0 < relax
        # → no qualified target → the overflow image FALLS BACK to its
        # original section (never dropped).
        imgA, imgB = _Img("https://x/a.jpg", "A"), _Img("https://x/b.jpg", "B")
        bank = {i.url: i for i in (imgA, imgB)}
        binding = {
            "https://x/a.jpg": [(1, 0, 0.9)],
            "https://x/b.jpg": [(1, 0, 0.8)],
        }
        sec_vecs = {0: [1.0, 0.0], 2: [0.0, 1.0]}
        alt_vecs = {"https://x/b.jpg": [1.0, 0.0]}
        nums_by_sec = {0: [1], 2: [1]}
        placements = self._run(binding, bank, sec_vecs, alt_vecs, nums_by_sec)
        assert (0, "https://x/a.jpg") in [(p[0], p[1]) for p in placements]
        assert (0, "https://x/b.jpg") in [(p[0], p[1]) for p in placements]
        # And it never landed in the orthogonal section.
        assert 2 not in {p[0] for p in placements}

    def test_already_placed_url_never_duplicated(self):
        # URL already seated in another section is skipped by spread
        # (the dedup pass would collapse it anyway).
        imgA, imgB = _Img("https://x/a.jpg", "A"), _Img("https://x/b.jpg", "B")
        bank = {i.url: i for i in (imgA, imgB)}
        binding = {
            "https://x/a.jpg": [(1, 0, 0.9), (1, 2, 0.9)],  # bound to 0 AND 2
            "https://x/b.jpg": [(1, 0, 0.8)],
        }
        sec_vecs = {0: [1.0, 0.0], 2: [1.0, 0.0], 5: [1.0, 0.0]}
        alt_vecs = {
            "https://x/a.jpg": [1.0, 0.0],
            "https://x/b.jpg": [1.0, 0.0],
        }
        nums_by_sec = {0: [1], 2: [1], 5: [1]}
        placements = self._run(binding, bank, sec_vecs, alt_vecs, nums_by_sec)
        urls_per_sec = {s: [p[1] for p in placements if p[0] == s]
                        for s in {p[0] for p in placements}}
        flat = [p[1] for p in placements]
        assert flat.count("https://x/a.jpg") <= 1

    def test_spread_off_returns_cap_only(self):
        imgA, imgB = _Img("https://x/a.jpg", "A"), _Img("https://x/b.jpg", "B")
        bank = {i.url: i for i in (imgA, imgB)}
        binding = {
            "https://x/a.jpg": [(1, 0, 0.9)],
            "https://x/b.jpg": [(1, 0, 0.8)],
        }
        placements = _build_placements(
            binding, bank, cap=1, spread=False
        )
        assert len(placements) == 1
