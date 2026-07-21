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
