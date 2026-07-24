# tests/images/test_enhancer.py
from unittest.mock import MagicMock
from local_deep_research.images.extractor import ExtractedImage
from local_deep_research.images.bank import ImageBank
from local_deep_research.images.enhancer import ImageEnhancer


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


def test_vision_triggered_when_with_alt_below_threshold():
    """≤ 3 alts + alt-less images present → vision fills up to cap=10."""
    bank = ImageBank()
    # 2 with-alt (under the default min_alt_count=3 threshold) + 5 without.
    bank.add(
        [_img(f"https://real/{i}.jpg", "alt") for i in range(2)]
        + [_img(f"https://real/n{i}.jpg", "") for i in range(5)]
    )
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="# R\n")
    vision = MagicMock()
    vision.enabled = True
    vision.describe.return_value = "described"
    ImageEnhancer(llm, vision).enhance("# R", bank)
    assert vision.describe.call_count == 5


def test_vision_skipped_when_bank_already_rich():
    """> min_alt_count alts → vision not invoked even if configured."""
    bank = ImageBank()
    # 5 with-alt + 5 without — well above the 3-trigger, vision is overkill.
    bank.add(
        [_img(f"https://real/{i}.jpg", f"alt {i}") for i in range(5)]
        + [_img(f"https://real/n{i}.jpg", "") for i in range(5)]
    )
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(
        content="![a](https://real/0.jpg)"
    )
    vision = MagicMock()
    vision.enabled = True
    vision.describe.return_value = "described"
    ImageEnhancer(llm, vision).enhance("# R\n\nbody", bank)
    vision.describe.assert_not_called()


def test_vision_caps_at_limit():
    """Even with many alt-less images, vision describes at most cap."""
    cap = 3

    bank = ImageBank()
    bank.add(
        [_img(f"https://real/{i}.jpg", "alt") for i in range(2)]  # trigger
        + [_img(f"https://real/n{i}.jpg", "") for i in range(cap + 5)]
    )
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="# R\n")
    vision = MagicMock()
    vision.enabled = True
    vision.describe.return_value = "described"
    ImageEnhancer(llm, vision, cap=cap).enhance("# R", bank)
    assert vision.describe.call_count == cap


def test_vision_thresholds_are_overridable_per_instance():
    """Custom min_alt_count / cap override module defaults."""
    bank = ImageBank()
    # 4 with-alt + 4 without. With min_alt_count=5 (high), vision runs;
    # with cap=2, only 2 vision calls.
    bank.add(
        [_img(f"https://real/{i}.jpg", f"alt {i}") for i in range(4)]
        + [_img(f"https://real/n{i}.jpg", "") for i in range(4)]
    )
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="# R\n")
    vision = MagicMock()
    vision.enabled = True
    vision.describe.return_value = "described"
    ImageEnhancer(llm, vision, min_alt_count=5, cap=2).enhance("# R", bank)
    assert vision.describe.call_count == 2