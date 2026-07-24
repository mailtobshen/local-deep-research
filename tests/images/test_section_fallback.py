from local_deep_research.images.extractor import ExtractedImage
from local_deep_research.images.postprocessing import fill_section_images


def test_section_image_fallback_adds_image_for_empty_section():
    md = "# Title\n\n## 石室圣心大教堂\n\n石室圣心大教堂是广州著名景点。\n"
    candidates = [
        ExtractedImage(
            "https://x/y.jpg", "石室圣心大教堂", "", "", 600, 400
        )
    ]

    out = fill_section_images(md, candidates)

    assert "![石室圣心大教堂](https://x/y.jpg)" in out


def test_section_image_fallback_skips_sections_with_image():
    md = "## 珠江夜游\n\n![img](https://kept.jpg)\n\n文字"
    candidates = [
        ExtractedImage("https://x/y.jpg", "珠江夜游", "", "", 600, 400)
    ]

    out = fill_section_images(md, candidates)

    assert "https://kept.jpg" in out
    assert "https://x/y.jpg" not in out
