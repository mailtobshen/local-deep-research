"""Task 8: oversized images are PIL-resized at persist time.

The long-side cap (_MAX_DISPLAY_PX = 600) used to be half-disabled:
RESIZE events were logged but the bytes were saved verbatim, so
oversized images rendered at native size (PDF export has no CSS
max-width). persist() now resizes before writing to disk, and
url_to_size reflects the saved (reduced) dimensions.

These tests use the existing ``patch.object(store, "_download")`` seam
(the established pattern in test_store.py) rather than a separate
``_fetcher=`` parameter, because persist() has no such parameter and
_download is the natural injection point.
"""

import io
from unittest.mock import MagicMock, patch

from PIL import Image as PILImage

from local_deep_research.images.store import ImageStore, _MAX_DISPLAY_PX


def _png_bytes(w: int, h: int) -> bytes:
    """Return a real minimal PNG of (w, h)."""
    buf = io.BytesIO()
    PILImage.new("RGB", (w, h), (255, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


def _read_saved_size(local_path) -> tuple:
    with PILImage.open(local_path) as im:
        return im.size


def test_oversized_landscape_resized_on_persist(tmp_path):
    """A 1200x800 PNG (long side 1200 > 600) must be persisted at
    long side 600 (600x400), aspect ratio preserved."""
    store = ImageStore("r1", MagicMock(), base_dir=tmp_path)
    url = "https://example.com/big.png"
    with patch.object(store, "_download") as dl:
        dl.return_value = (_png_bytes(1200, 800), "image/png")
        routes = store.persist([url])
    route = routes[url]
    local = tmp_path / "r1" / route.split("/")[-1]
    w, h = _read_saved_size(local)
    assert max(w, h) <= _MAX_DISPLAY_PX
    # 1200:800 = 3:2 -> 600x400
    assert (w, h) == (600, 400)


def test_oversized_portrait_resized_on_persist(tmp_path):
    """A 800x1200 portrait (long side 1200) -> 400x600, aspect preserved."""
    store = ImageStore("r2", MagicMock(), base_dir=tmp_path)
    url = "https://example.com/tall.png"
    with patch.object(store, "_download") as dl:
        dl.return_value = (_png_bytes(800, 1200), "image/png")
        routes = store.persist([url])
    local = tmp_path / "r2" / routes[url].split("/")[-1]
    assert _read_saved_size(local) == (400, 600)


def test_under_cap_image_not_resized(tmp_path):
    """A 400x300 image (long side 400 <= 600) is saved as-is."""
    store = ImageStore("r3", MagicMock(), base_dir=tmp_path)
    url = "https://example.com/small.png"
    with patch.object(store, "_download") as dl:
        dl.return_value = (_png_bytes(400, 300), "image/png")
        routes = store.persist([url])
    local = tmp_path / "r3" / routes[url].split("/")[-1]
    assert _read_saved_size(local) == (400, 300)


def test_exactly_at_cap_not_resized(tmp_path):
    """Long side == 600 is NOT oversized (strict >), saved as-is."""
    store = ImageStore("r4", MagicMock(), base_dir=tmp_path)
    url = "https://example.com/exact.png"
    with patch.object(store, "_download") as dl:
        dl.return_value = (_png_bytes(600, 400), "image/png")
        routes = store.persist([url])
    local = tmp_path / "r4" / routes[url].split("/")[-1]
    assert _read_saved_size(local) == (600, 400)


def test_url_to_size_reflects_resized_dims(tmp_path):
    """The stashed _last_url_to_size must report the SAVED (resized)
    dimensions, not the original — rewrite_markdown (Task 9) reads
    this to emit width/height attributes."""
    store = ImageStore("r5", MagicMock(), base_dir=tmp_path)
    url = "https://example.com/big.png"
    with patch.object(store, "_download") as dl:
        dl.return_value = (_png_bytes(1500, 1000), "image/png")
        store.persist([url])
    sizes = getattr(store, "_last_url_to_size", {})
    assert sizes[url] == (600, 400)


def test_record_receives_resized_dims(tmp_path):
    """The DB record (width/height) must also reflect the resized
    dimensions, not the original."""
    mock_session = MagicMock()
    store = ImageStore("r6", mock_session, base_dir=tmp_path)
    url = "https://example.com/big.png"
    with patch.object(store, "_download") as dl:
        dl.return_value = (_png_bytes(1200, 800), "image/png")
        store.persist([url])
    # The Image(...) call's width/height kwargs.
    image_kwargs = mock_session.add.call_args.args[0].__dict__
    assert image_kwargs["width"] == 600
    assert image_kwargs["height"] == 400


# --- undersize upscale (min-size floor) ---------------------------------


def test_undersized_landscape_upscaled_on_persist(tmp_path):
    """A 150x120 image (area 18,000 < 40,000) must upscale so both
    sides reach >= 300, aspect (5:4) preserved -> 375x300."""
    store = ImageStore("r7", MagicMock(), base_dir=tmp_path)
    url = "https://example.com/tiny.png"
    with patch.object(store, "_download") as dl:
        dl.return_value = (_png_bytes(150, 120), "image/png")
        routes = store.persist([url])
    local = tmp_path / "r7" / routes[url].split("/")[-1]
    w, h = _read_saved_size(local)
    assert min(w, h) >= 300, f"short side must reach 300, got {w}x{h}"
    assert (w, h) == (375, 300), f"aspect 5:4 preserved, got {w}x{h}"


def test_undersized_portrait_upscaled_on_persist(tmp_path):
    """A 90x120 portrait (area 10,800) upscales to 300x400 (short side
    300 = width). Guards orientation handling."""
    store = ImageStore("r8", MagicMock(), base_dir=tmp_path)
    url = "https://example.com/tiny_portrait.png"
    with patch.object(store, "_download") as dl:
        dl.return_value = (_png_bytes(90, 120), "image/png")
        routes = store.persist([url])
    local = tmp_path / "r8" / routes[url].split("/")[-1]
    assert _read_saved_size(local) == (300, 400)


def test_midband_image_not_resized(tmp_path):
    """A 250x200 image (area 50,000, between 40,000 and 600²) is saved
    at native size — neither downscaled nor upscaled."""
    store = ImageStore("r9", MagicMock(), base_dir=tmp_path)
    url = "https://example.com/mid.png"
    with patch.object(store, "_download") as dl:
        dl.return_value = (_png_bytes(250, 200), "image/png")
        routes = store.persist([url])
    local = tmp_path / "r9" / routes[url].split("/")[-1]
    assert _read_saved_size(local) == (250, 200)


def test_at_area_threshold_not_upscaled(tmp_path):
    """Area == 40,000 (200x200) is NOT upscaled (strict <)."""
    store = ImageStore("r10", MagicMock(), base_dir=tmp_path)
    url = "https://example.com/exact_area.png"
    with patch.object(store, "_download") as dl:
        dl.return_value = (_png_bytes(200, 200), "image/png")
        routes = store.persist([url])
    local = tmp_path / "r10" / routes[url].split("/")[-1]
    assert _read_saved_size(local) == (200, 200)
