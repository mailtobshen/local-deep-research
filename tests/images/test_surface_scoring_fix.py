"""Tests for the 2026-09-13 multi-surface alt-scoring fix (c34cb8fa).

Root cause: filename-derived token-soup alts embed near Chinese section
phrases (cosine 0.61–0.65, verified offline) while real cross-lingual
alts score 0.22–0.28 — junk adopted, real images all dropped.

- Fix A: ``_alt_has_semantic_content`` gate rejects token-soup alts
  before the encoder ever sees them.
- Fix B: ``build_url_text_index`` supplies the cited reference's
  textual passage; scoring takes max over (sec, ref_text, query)
  surfaces — see postprocessing scoring loop.
"""

from local_deep_research.images.postprocessing import (
    _alt_has_semantic_content,
)
from local_deep_research.images.relevance import build_url_text_index


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
        assert _alt_has_semantic_content("【使用帮助】图3")
        assert _alt_has_semantic_content("核心业务领域")

    def test_empty_and_trivial_rejected(self):
        for alt in ("", "   ", None, "250px"):
            assert not _alt_has_semantic_content(alt), alt


class TestBuildUrlTextIndex:
    def test_merges_title_content_snippet(self):
        results = {
            "findings": [
                {
                    "search_results": [
                        {
                            "url": "https://example.org/a",
                            "title": "Winrock partnership",
                            "content": "Winrock works with USAID on agriculture.",
                            "snippet": "partnership since 2010",
                        }
                    ]
                }
            ]
        }
        idx = build_url_text_index(results)
        text = idx["https://example.org/a"]
        assert "Winrock partnership" in text
        assert "USAID" in text
        assert "partnership since 2010" in text

    def test_reads_all_links_channel_and_link_key(self):
        results = {
            "all_links_of_system": [
                {
                    "link": "https://example.org/b",
                    "title": "Second source",
                }
            ]
        }
        assert "Second source" in build_url_text_index(results)[
            "https://example.org/b"
        ]

    def test_caps_length_and_skips_empty(self):
        results = {
            "findings": [
                {
                    "search_results": [
                        {"url": "https://x/1", "title": "t" * 2000},
                        {"url": "https://x/2"},
                    ]
                }
            ]
        }
        idx = build_url_text_index(results)
        assert len(idx["https://x/1"]) == 800
        assert "https://x/2" not in idx

    def test_first_occurrence_wins(self):
        results = {
            "findings": [
                {"search_results": [{"url": "https://x/1", "title": "first"}]},
                {"search_results": [{"url": "https://x/1", "title": "second"}]},
            ]
        }
        assert build_url_text_index(results)["https://x/1"] == "first"
