from local_deep_research.images.extractor import extract_images, ExtractedImage


def test_extracts_content_image_with_alt():
    html = '<html><body><img src="https://example.com/a/tower.jpg" alt="Canton Tower" width="800" height="600"></body></html>'
    imgs = extract_images(html, "https://example.com/a", "Example Page")
    assert len(imgs) == 1
    assert imgs[0].url == "https://example.com/a/tower.jpg"
    assert imgs[0].alt == "Canton Tower"
    assert imgs[0].source_url == "https://example.com/a"
    assert imgs[0].source_title == "Example Page"
    assert imgs[0].width == 800


def test_skips_data_uri():
    html = '<img src="data:image/png;base64,iVBORw0KGgo=" alt="x">'
    assert extract_images(html, "https://example.com", "t") == []


def test_skips_tiny_icon():
    html = '<img src="https://example.com/icon.png" width="16" height="16">'
    assert extract_images(html, "https://example.com", "t") == []


def test_skips_blacklisted_url_keywords():
    for kw in ["logo", "icon", "avatar", "sprite", "tracker", "blank.gif"]:
        html = f'<img src="https://example.com/{kw}.png" width="200" height="200">'
        assert extract_images(html, "https://example.com", "t") == [], kw


def test_resolves_relative_url():
    html = '<img src="/img/tower.jpg" alt="t" width="200">'
    imgs = extract_images(html, "https://example.com/page", "p")
    assert imgs[0].url == "https://example.com/img/tower.jpg"


def test_alt_empty_string_preserved():
    html = '<img src="https://example.com/x.jpg" width="200">'
    imgs = extract_images(html, "https://example.com", "t")
    assert len(imgs) == 1
    assert imgs[0].alt == ""


def test_missing_width_height_kept_when_url_ok():
    html = '<img src="https://example.com/big.jpg" alt="t">'
    imgs = extract_images(html, "https://example.com", "t")
    assert len(imgs) == 1
    assert imgs[0].width is None


def test_root_selector_scopes_to_article():
    html = (
        '<article>'
        '  <img src="https://example.com/a/main.jpg" alt="main" width="200">'
        '</article>'
        '<aside>'
        '  <img src="https://example.com/b/side.jpg" alt="side" width="200">'
        '</aside>'
    )
    scoped = extract_images(html, "https://example.com", "p", roots=["article"])
    assert [i.url for i in scoped] == ["https://example.com/a/main.jpg"]
    full = extract_images(html, "https://example.com", "p", roots=[])
    assert {i.url for i in full} == {
        "https://example.com/a/main.jpg",
        "https://example.com/b/side.jpg",
    }


def test_root_selector_falls_back_when_no_match():
    """Pages without any content-root selector still return their images."""
    html = '<img src="https://example.com/x.jpg" alt="t" width="200">'
    imgs = extract_images(html, "https://example.com", "t", roots=["article"])
    assert [i.url for i in imgs] == ["https://example.com/x.jpg"]


def test_roots_chain_picks_first_subtree_with_images():
    """When <article> is empty but <main> has images, <main> wins."""
    html = (
        '<article></article>'
        '<main>'
        '  <img src="https://example.com/m/main.jpg" alt="m" width="200">'
        '</main>'
        '<footer>'
        '  <img src="https://example.com/f/foot.jpg" alt="f" width="200">'
        '</footer>'
    )
    imgs = extract_images(html, "https://example.com", "p")
    assert [i.url for i in imgs] == ["https://example.com/m/main.jpg"]


def test_roots_chain_falls_back_to_full_page_when_all_empty():
    """When every selector matches an empty subtree, fall back to whole doc."""
    html = (
        '<article></article>'
        '<main></main>'
        '<div class="content">'
        '  <img src="https://example.com/c/x.jpg" alt="c" width="200">'
        '</div>'
    )
    imgs = extract_images(html, "https://example.com", "p")
    # Falls back to full-page extraction since none of the default roots
    # actually contain an <img>.
    assert [i.url for i in imgs] == ["https://example.com/c/x.jpg"]
