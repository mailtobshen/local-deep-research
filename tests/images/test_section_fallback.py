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


def test_section_image_fallback_matches_malay_alt():
    """Malay alt with the landmark's name in Roman letters should match too."""
    md = "## Canton Tower\n\nThe tower is 600 m tall.\n"
    candidates = [
        ExtractedImage(
            "https://x/y.jpg", "Guangzhou Canton Tower", "", "", 600, 400
        )
    ]

    out = fill_section_images(md, candidates)

    assert "![Guangzhou Canton Tower](https://x/y.jpg)" in out


def test_section_image_fallback_matches_substring_chinese():
    """Heading is a substring of the alt (e.g. alt is more verbose)."""
    md = "## 越秀公园\n\n介绍越秀公园。\n"
    candidates = [
        ExtractedImage(
            "https://x/y.jpg", "越秀公园 Guangzhou Yuexiu Park entrance",
            "",
            "",
            600,
            400,
        )
    ]

    out = fill_section_images(md, candidates)

    assert "![越秀公园 Guangzhou Yuexiu Park entrance](https://x/y.jpg)" in out


def test_section_image_fallback_does_not_reuse_url_across_sections():
    """A single candidate may fill at most one section."""
    md = (
        "## 越秀公园\n\nbody1\n\n## 越秀公园东门\n\nbody2\n"
    )
    candidates = [
        ExtractedImage("https://x/y.jpg", "越秀公园 entrance", "", "", 600, 400)
    ]

    out = fill_section_images(md, candidates)

    # Only the first section picks up the image; the second stays image-free
    # because the only candidate is already used.
    assert out.count("https://x/y.jpg") == 1

