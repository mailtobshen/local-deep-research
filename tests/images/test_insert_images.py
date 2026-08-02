from local_deep_research.images.postprocessing import insert_images_by_section


def test_inserts_image_after_section_heading():
    md = "## A\n\nBody text.\n\n## B\n\nMore text.\n"
    out = insert_images_by_section(
        md, [(0, "https://x/a.jpg", "Tower")]
    )
    assert "## A\n\n![Tower](https://x/a.jpg)" in out
    # Section B unchanged.
    assert "## B\n\nMore text." in out


def test_multiple_images_one_section_inserted_in_order():
    md = "## A\n\nBody.\n"
    out = insert_images_by_section(
        md,
        [(0, "https://x/1.jpg", "One"), (0, "https://x/2.jpg", "Two")],
    )
    assert out.index("https://x/1.jpg") < out.index("https://x/2.jpg")
    assert "![One](https://x/1.jpg)" in out
    assert "![Two](https://x/2.jpg)" in out


def test_empty_alt_skipped():
    md = "## A\n\nBody.\n"
    out = insert_images_by_section(md, [(0, "https://x/a.jpg", "")])
    assert "https://x/a.jpg" not in out


def test_section_idx_out_of_range_skipped():
    md = "## A\n\nBody.\n"
    out = insert_images_by_section(md, [(5, "https://x/a.jpg", "Tower")])
    assert out == md


def test_sanitizes_alt_via_safe_alt():
    """alt with brackets/newlines is cleaned before insertion.

    _safe_alt('hello [world]\\nfoo') == 'hello world foo' (strips [ ],
    collapses whitespace). Verified: the rendered markdown carries the
    cleaned alt, not the raw one.
    """
    md = "## A\n\nBody.\n"
    out = insert_images_by_section(
        md, [(0, "https://x/a.jpg", "hello [world]\nfoo")]
    )
    assert "![hello world foo](https://x/a.jpg)" in out
    assert "[" not in out  # brackets stripped from the alt
