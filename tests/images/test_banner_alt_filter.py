"""Banner-substring alts must be filtered.

Observed 2026-08-26 (research 9b514fa0, AK47): the Kalashnikov product
site's ad banners — alt ``Partner Banner`` (2340x300 partner_1.png …
partner_3.gif) — passed the meaningless-alt filter 51 times
(``decision=keep``) and 3 shipped into the report body. The existing
rule is the EXACT-match ``^banner$``; a prefixed phrase like
``Partner Banner`` escapes it.

Policy: like ``icon`` and ``shop`` (2026-08-22 substring policy),
banner-ness is vocabulary-driven regardless of prefix — ad banners are
never research content. Word-boundary substring: matches ``banner``,
``Partner Banner``, ``banner 2``, ``site banner``; does NOT match
prose like ``bannerman`` (rare surname) — word boundary keeps that
safe.
"""

import pytest

from local_deep_research.images.postprocessing import (
    _alt_is_meaningless,
)


class TestBannerSubstring:
    @pytest.mark.parametrize(
        "alt",
        [
            "banner",
            "Banner",
            "Partner Banner",
            "partner banner 2",
            "Site banner",
            "top banner ad",
        ],
    )
    def test_banner_variants_filtered(self, alt):
        assert _alt_is_meaningless(alt), f"{alt!r} should be filtered"

    def test_non_banner_content_kept(self):
        assert not _alt_is_meaningless("AK-47 receiver diagram")
        assert not _alt_is_meaningless("Kalashnikov portrait 1947")

    def test_existing_exact_rules_still_work(self):
        assert _alt_is_meaningless("logo")
        assert _alt_is_meaningless("Awaiting product image")
        assert _alt_is_meaningless("Language selector icon")
