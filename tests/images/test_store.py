# tests/images/test_store.py
import io
from unittest.mock import MagicMock, patch
from PIL import Image as PILImage
from local_deep_research.images.store import (
    ImageStore,
    _MAX_DISPLAY_PX,
    _probe_size,
)


def _png_bytes(w: int, h: int) -> bytes:
    """Return a real minimal PNG of (w, h) for PIL probing."""
    buf = io.BytesIO()
    PILImage.new("RGB", (w, h), (255, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


def test_persist_downloads_and_returns_routes(tmp_path):
    store = ImageStore("rid-123", db_session=MagicMock(), base_dir=tmp_path)
    with patch.object(store, "_download") as dl:
        dl.return_value = (b"\x89PNG fake", "image/png")
        routes = store.persist(["https://x/a.jpg"])
    assert "https://x/a.jpg" in routes
    route = routes["https://x/a.jpg"]
    assert route.startswith("/images/rid-123/")
    # local file created
    local_files = list((tmp_path / "rid-123").iterdir())
    assert len(local_files) == 1


def test_persist_skips_failed_download(tmp_path):
    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    with patch.object(store, "_download", side_effect=Exception("net")):
        assert store.persist(["https://x/a.jpg"]) == {}


def test_rewrite_markdown_replaces_urls():
    store = ImageStore("rid", MagicMock(), base_dir="/tmp")
    md = "![t](https://x/a.jpg) and ![u](https://y/b.jpg)"
    out = store.rewrite_markdown(md, {"https://x/a.jpg": "/images/rid/h1.png"})
    assert "/images/rid/h1.png" in out
    assert "https://y/b.jpg" in out  # unmapped url left intact


def test_persist_path_traversal_safe(tmp_path):
    store = ImageStore("..%2fevil", MagicMock(), base_dir=tmp_path)
    with patch.object(store, "_download") as dl:
        dl.return_value = (b"\x89PNG", "image/png")
        routes = store.persist(["https://x/a.jpg"])
    # route must contain only the safe research_id segment, no traversal
    route = routes["https://x/a.jpg"]
    assert ".." not in route


def test_probe_size_reads_real_png_dimensions():
    data = _png_bytes(1200, 800)
    w, h = _probe_size(data)
    assert (w, h) == (1200, 800)


def test_probe_size_returns_none_for_garbage():
    assert _probe_size(b"not an image") is None


def test_persist_stashes_url_to_size(tmp_path):
    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    with patch.object(store, "_download") as dl:
        # 1200x800 — over threshold
        dl.return_value = (_png_bytes(1200, 800), "image/png")
        store.persist(["https://x/big.jpg"])
    sizes = getattr(store, "_last_url_to_size", {})
    assert sizes.get("https://x/big.jpg") == (1200, 800)


def test_rewrite_markdown_injects_width_for_landscape_oversize(tmp_path):
    """Image wider than _MAX_DISPLAY_PX gets width={cap}; height untouched."""
    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    md = "![t](https://x/a.jpg)"
    sizes = {"https://x/a.jpg": (1200, 800)}
    out = store.rewrite_markdown(
        md, {"https://x/a.jpg": "/local.png"}, url_to_size=sizes
    )
    assert f"{{width={_MAX_DISPLAY_PX}}}" in out
    assert f"height=" not in out


def test_rewrite_markdown_injects_height_for_portrait_oversize(tmp_path):
    """Image taller than wide (portrait) gets height={cap}; width untouched."""
    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    md = "![t](https://x/a.jpg)"
    sizes = {"https://x/a.jpg": (800, 1200)}
    out = store.rewrite_markdown(
        md, {"https://x/a.jpg": "/local.png"}, url_to_size=sizes
    )
    assert f"{{height={_MAX_DISPLAY_PX}}}" in out
    assert f"width=" not in out


def test_rewrite_markdown_no_size_attribute_under_threshold(tmp_path):
    """Image whose long side is <= threshold gets no size attribute."""
    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    md = "![t](https://x/a.jpg)"
    sizes = {"https://x/a.jpg": (400, 300)}
    out = store.rewrite_markdown(
        md, {"https://x/a.jpg": "/local.png"}, url_to_size=sizes
    )
    assert "{width=" not in out
    assert "{height=" not in out


def test_rewrite_markdown_unknown_size_no_attribute(tmp_path):
    """Missing size entry → no size attribute (graceful fallback)."""
    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    md = "![t](https://x/a.jpg)"
    out = store.rewrite_markdown(
        md, {"https://x/a.jpg": "/local.png"}, url_to_size={}
    )
    assert "{width=" not in out
    assert "{height=" not in out


def test_rewrite_markdown_uses_stashed_size_from_persist(tmp_path):
    """Without explicit url_to_size, rewrite falls back to instance stash."""
    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    with patch.object(store, "_download") as dl:
        dl.return_value = (_png_bytes(1500, 1000), "image/png")
        store.persist(["https://x/big.jpg"])
    md = "![t](https://x/big.jpg)"
    out = store.rewrite_markdown(md, {"https://x/big.jpg": "/local.png"})
    assert f"{{width={_MAX_DISPLAY_PX}}}" in out
