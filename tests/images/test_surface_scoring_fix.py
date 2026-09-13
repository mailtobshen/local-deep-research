"""Tests for the 2026-09-13 multi-surface alt-scoring fix (c34cb8fa).

Root cause: filename-derived token-soup alts embed near Chinese section
phrases (cosine 0.61–0.65, verified offline) while real cross-lingual
alts score 0.22–0.28 — junk adopted, real images all dropped.

- Fix A: ``_alt_has_semantic_content`` gate rejects token-soup alts
  before the encoder ever sees them.
- Fix B (superseded 2026-09-13): scoring takes max over the
  (sec, ref_text, query) surfaces — ref_text is now alt-vs-citing-
  context similarity (``build_cite_context_index``, research
  b7ec824a); see postprocessing scoring loop and
  test_ref_text_context.py.
"""

from local_deep_research.images.postprocessing import (
    _alt_has_semantic_content,
)


class TestAltSemanticContentGate:
    def test_baidu_token_soup_rejected(self):
        # The exact alts adopted in research c34cb8fa.
        for alt in (
            "ldgjhfknbaicem1496932883",
            "ldmfajnhcbkegi1496932864",
            "fnejlmkaghcdib1496932842",
            "bnhdmeifkclgaj1496935048",
        ):
            assert not _alt_has_semantic_content(alt), alt

    def test_real_words_pass(self):
        for alt in (
            "illustration",
            "Winrock International",
            "Card image cap",
            "Master of Public Service student working in the field.",
            "salary illustration hover",
        ):
            assert _alt_has_semantic_content(alt), alt

    def test_cjk_alt_passes(self):
        assert _alt_has_semantic_content("核心业务领域")
        assert _alt_has_semantic_content("温思罗普·洛克菲勒肖像")

    def test_cjk_ui_chatter_rejected(self):
        # 2026-09-13 replay residue: '【使用帮助】图3' (johnvocab junk
        # source) cleared the CJK branch and scored 0.61 vs a template
        # heading. Page-furniture captions are whole-alt matched.
        for alt in (
            "【使用帮助】图3",
            "使用帮助",
            "图3",
            "图 12",
            "【logo】",
            "二维码",
            "箭头图标",
            "首页轮播 1",
            "幻灯片3",
        ):
            assert not _alt_has_semantic_content(alt), alt

    def test_empty_and_trivial_rejected(self):
        for alt in ("", "   ", None, "250px"):
            assert not _alt_has_semantic_content(alt), alt
