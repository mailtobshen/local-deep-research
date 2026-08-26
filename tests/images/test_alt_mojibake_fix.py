"""Mojibake repair for image alt text (latin1→utf8 re-encode).

Observed 2026-08-26 (research 659cedd1, 上海旅游景点): kekenet.com
image alts arrived as UTF-8 bytes mis-decoded as Latin-1 —
``ä¸\x8aæµ·è¿ªå£«å°¼ä¹\x90å\x9b­é¦\x96æ¬¡å\x8f\x91å¸\x83`` instead of
``上海迪士尼乐园首次发布国内景致组图``. The mojibake fails the
semantic-matching gate (can never match a Chinese section phrase), so
the image is dropped regardless of relevance.

Fix: ``_fix_mojibake`` detects the UTF-8-read-as-Latin-1 signature
(also called "double encoding") and repairs it by re-encoding as
Latin-1 and decoding as UTF-8. Applied to the raw ``alt`` attribute in
``_resolve_alt`` before any other use. Conservative: only fires when
the round-trip succeeds AND the result contains non-ASCII (CJK etc.)
AND the original contained the telltale Latin-1 punctuation cluster
(ä/å/ç/è sequences with control-range bytes).
"""

from local_deep_research.images.extractor import _fix_mojibake


class TestFixMojibake:
    def test_kekenet_shape_repaired(self):
        moji = (
            "ä¸\x8aæµ·è¿ªå£«å°¼ä¹\x90å\x9b­é¦\x96æ¬¡å\x8f\x91å¸\x83"
            "å\x9b­å\x86\x85æ\x99¯è\x87´ç»\x84å\x9b¾"
        )
        out = _fix_mojibake(moji)
        assert "上海" in out, repr(out)
        assert "迪士尼" in out, repr(out)

    def test_clean_chinese_untouched(self):
        s = "上海迪士尼乐园首次发布国内景致组图"
        assert _fix_mojibake(s) == s

    def test_plain_english_untouched(self):
        assert _fix_mojibake("Hydra darknet market homepage") == "Hydra darknet market homepage"

    def test_genuine_latin1_text_untouched(self):
        """Real French/German text that round-trips cleanly must not
        be mangled (no UTF-8 signature bytes)."""
        s = "Café München naïve"
        assert _fix_mojibake(s) == s

    def test_empty_and_ascii(self):
        assert _fix_mojibake("") == ""
        assert _fix_mojibake("banner 2") == "banner 2"
