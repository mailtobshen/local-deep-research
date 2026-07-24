from pathlib import Path

from local_deep_research.images.store import ImageStore


def _store() -> ImageStore:
    return ImageStore("test-research", db_session=None, base_dir=Path("/tmp"))


def test_rewrite_emits_html_img_with_attrs_for_oversized():
    md = "![长隆](https://example.com/big.jpg)"
    sizes = {"https://example.com/big.jpg": (2000, 1000)}
    routes = {"https://example.com/big.jpg": "/images/abc.jpg"}

    out = _store().rewrite_markdown(md, routes, sizes)

    assert "<img" in out
    assert 'src="/images/abc.jpg"' in out
    assert 'width="600"' in out
    assert 'height="300"' in out
    assert out.strip().startswith("<img") and out.strip().endswith("/>")


def test_rewrite_keeps_markdown_for_small_or_unknown():
    md = "![small](https://example.com/small.jpg)"
    sizes = {"https://example.com/small.jpg": (200, 150)}
    routes = {"https://example.com/small.jpg": "/images/small.jpg"}

    out = _store().rewrite_markdown(md, routes, sizes)

    assert out == "![small](/images/small.jpg)"
