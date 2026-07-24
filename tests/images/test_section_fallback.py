from local_deep_research.images.extractor import ExtractedImage
from local_deep_research.images.postprocessing import fill_section_images


def test_section_fallback_drops_off_source_candidate():
    md = "## 越秀公园\n\nbody"
    candidates = [
        ExtractedImage(
            "https://gz/x.jpg",
            "越秀公园",
            "https://gz-source",
            "广州",
            600,
            400,
        ),
        ExtractedImage(
            "https://xm/y.jpg",
            "鼓浪屿",
            "https://xm-source",
            "厦门",
            600,
            400,
        ),
    ]

    out = fill_section_images(
        md,
        candidates,
        segment_allow=[
            ("## 越秀公园\n", "body", ["https://gz-source"])
        ],
    )

    assert "https://gz/x.jpg" in out
    assert "https://xm/y.jpg" not in out
