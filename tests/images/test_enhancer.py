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
