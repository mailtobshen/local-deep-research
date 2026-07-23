# tests/images/test_enhancer.py
from unittest.mock import MagicMock
from local_deep_research.images.extractor import ExtractedImage
from local_deep_research.images.bank import ImageBank
from local_deep_research.images.enhancer import (
    ImageEnhancer,
    _FILTER_THRESHOLD,
)


def _img(url, alt):
    return ExtractedImage(
        url=url, alt=alt, source_url="s", source_title="t", width=None, height=None
    )


def test_enhance_inserts_real_url_from_bank():
    bank = ImageBank()
    bank.add([_img("https://real/a.jpg", "Canton Tower")])
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(
        content="# Report\n\n![Canton Tower](https://real/a.jpg)\n"
    )
    vision = MagicMock()
    vision.enabled = False
    out = ImageEnhancer(llm, vision).enhance("# Report\n\ntext", bank)
    assert "https://real/a.jpg" in out
    llm.invoke.assert_called_once()


def test_enhance_returns_original_when_llm_fails():
    bank = ImageBank()
    bank.add([_img("https://real/a.jpg", "x")])
    llm = MagicMock()
    llm.invoke.side_effect = Exception("boom")
    vision = MagicMock()
    vision.enabled = False
    original = "# Report\n\ntext"
    assert ImageEnhancer(llm, vision).enhance(original, bank) == original


def test_enhance_skips_when_bank_empty():
    bank = ImageBank()
    llm = MagicMock()
    vision = MagicMock()
    vision.enabled = False
    out = ImageEnhancer(llm, vision).enhance("# Report", bank)
    assert out == "# Report"
    llm.invoke.assert_not_called()


def test_vision_fallback_called_for_altless_images():
    bank = ImageBank()
    bank.add([_img("https://real/b.jpg", "")])  # no alt
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="# R\n\n![tower](https://real/b.jpg)\n")
    vision = MagicMock()
    vision.enabled = True
    vision.describe.return_value = "a tower"
    ImageEnhancer(llm, vision).enhance("# R", bank)
    vision.describe.assert_called_once_with("https://real/b.jpg")


def test_enhance_skips_filter_when_candidates_small():
    """< _FILTER_THRESHOLD candidates → single LLM call, all candidates passed."""
    bank = ImageBank()
    # 10 unique candidates (well below threshold)
    bank.add([_img(f"https://x/{i}.jpg", f"alt {i}") for i in range(10)])
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(
        content="# Report\n\n![a](https://x/0.jpg)\n"
    )
    vision = MagicMock()
    vision.enabled = False
    ImageEnhancer(llm, vision).enhance(
        "# Report\n\nsome text body", bank
    )
    # Single LLM call (not per-section)
    assert llm.invoke.call_count == 1
    # The single call's prompt should contain all 10 candidates
    prompt = llm.invoke.call_args[0][0]
    for i in range(10):
        assert f"https://x/{i}.jpg" in prompt


def test_enhance_filters_per_section_when_many_candidates():
    """≥ threshold candidates + multi-section → one LLM call per section."""
    bank = ImageBank()
    # Above threshold; build with topic-specific alts
    alts = (
        [f"Canton Tower {i}" for i in range(30)]
        + [f"Chen Clan Ancestral Hall {i}" for i in range(30)]
        + [f"Shamian Island {i}" for i in range(20)]
    )
    bank.add([_img(f"https://x/{i}.jpg", a) for i, a in enumerate(alts)])
    llm = MagicMock()
    # Return valid sectioned markdown for any prompt
    llm.invoke.return_value = MagicMock(
        content="![a](https://x/0.jpg)"
    )
    vision = MagicMock()
    vision.enabled = False
    md = (
        "# Canton Tower\n\n"
        "About Canton Tower height and tickets.\n\n"
        "## Chen Clan\n\n"
        "About Chen Clan Ancestral Hall history.\n\n"
        "## Shamian\n\n"
        "About Shamian Island colonial architecture."
    )
    ImageEnhancer(llm, vision).enhance(md, bank)
    # 3 sections → 3 LLM calls
    assert llm.invoke.call_count == 3
    # Each call's prompt should contain fewer URLs than the full bank
    for call in llm.invoke.call_args_list:
        prompt = call[0][0]
        assert prompt.count("https://x/") < 80


def test_enhance_section_with_no_match_passes_through():
    """Section whose text shares no token with any alt → falls back to full list
    and still produces enhanced markdown (LLM call still happens)."""
    bank = ImageBank()
    # Build a bank large enough to trigger per-section mode
    alts = [f"Topic X variant {i}" for i in range(_FILTER_THRESHOLD + 5)]
    bank.add([_img(f"https://x/{i}.jpg", a) for i, a in enumerate(alts)])
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="unchanged")
    vision = MagicMock()
    vision.enabled = False
    md = (
        "# Section A\n\n"
        "Body about completely unrelated zzzzz topic.\n\n"
        "# Section B\n\n"
        "Body also about completely unrelated zzzzz topic."
    )
    out = ImageEnhancer(llm, vision).enhance(md, bank)
    # No token overlap with any alt → fallback to full list per section,
    # so each section gets its own LLM call
    assert llm.invoke.call_count == 2
    assert out  # something non-empty returned


def test_enhance_handles_no_headings_gracefully():
    """Markdown with no H1-H3 → falls back to single-shot path."""
    bank = ImageBank()
    bank.add([_img(f"https://x/{i}.jpg", f"alt {i}") for i in range(40)])
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="some markdown")
    vision = MagicMock()
    vision.enabled = False
    out = ImageEnhancer(llm, vision).enhance(
        "Just text without any headings at all", bank
    )
    # No headings → _split_sections returns [] → single-shot path
    assert llm.invoke.call_count == 1
    assert out == "some markdown"
