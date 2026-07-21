from local_deep_research.images.extractor import ExtractedImage
from local_deep_research.images.serialize import dumps_images, loads_images


def _img(url="https://x/a.jpg"):
    return ExtractedImage(url=url, alt="A", source_url="https://x", source_title="T", width=800, height=600)


def test_roundtrip():
    imgs = [_img("https://x/a.jpg"), _img("https://x/b.jpg")]
    out = loads_images(dumps_images(imgs))
    assert [i.url for i in out] == ["https://x/a.jpg", "https://x/b.jpg"]
    assert out[0].alt == "A"
    assert out[0].width == 800


def test_loads_empty_string_returns_empty():
    assert loads_images("") == []


def test_loads_none_returns_empty():
    assert loads_images(None) == []


def test_loads_legacy_html_returns_empty():
    assert loads_images("<html><img src='x'></html>") == []


def test_loads_malformed_json_returns_empty():
    assert loads_images('{"not": "a list"}') == []
    assert loads_images('[{"missing_url": 1}]') == []


def test_dumps_empty_list():
    assert loads_images(dumps_images([])) == []
