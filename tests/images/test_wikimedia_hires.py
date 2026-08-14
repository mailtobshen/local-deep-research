"""Request a larger Wikimedia thumbnail instead of upscaling by interpolation.

Wikipedia embeds 250px-wide thumbnails. Those land at ~250x170, whose
area (~42,000) sits just above the ``_MIN_DISPLAY_AREA`` = 40,000 upscale
floor, so they were persisted at 250px even though the short side (~170)
is far below the ``_MIN_DISPLAY_SIDE`` = 300 target.

Interpolating them up adds no detail. Wikimedia thumbnail URLs carry the
width in the last path segment (``/250px-NAME.jpg``), so a larger
rendition of *real* pixels can simply be requested instead. Verified
against the live CDN: 250px -> 250x173 (19,099 B), 500px -> 500x347
(41,541 B). Requesting more than the source width returns HTTP 400, so
the fallback to the original URL is required, not defensive padding.
"""

import io
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image as PILImage

from local_deep_research.images.store import (
    ImageStore,
    _MAX_DISPLAY_PX,
    _wikimedia_candidates,
    _wikimedia_larger_url,
)


def _png_bytes(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (w, h), (0, 128, 255)).save(buf, "PNG")
    return buf.getvalue()


THUMB = (
    "https://upload.wikimedia.org/wikipedia/commons/thumb/4/41/"
    "The_HSBC_Building_and_the_Customs_House.jpg/"
    "250px-The_HSBC_Building_and_the_Customs_House.jpg"
)


@pytest.mark.parametrize(
    "url,expected",
    [
        # Standard thumbnail: width bumped to the display cap.
        (THUMB, THUMB.replace("250px-", f"{_MAX_DISPLAY_PX}px-")),
        # Query string (the real URLs carry utm_* params) is preserved.
        (
            THUMB + "?utm_source=zh.wikipedia.org",
            THUMB.replace("250px-", f"{_MAX_DISPLAY_PX}px-")
            + "?utm_source=zh.wikipedia.org",
        ),
        # Already at or above the cap: nothing to gain.
        (THUMB.replace("250px-", f"{_MAX_DISPLAY_PX}px-"), None),
        (THUMB.replace("250px-", "1200px-"), None),
        # Not a thumbnail path (original file) — no width to rewrite.
        (
            "https://upload.wikimedia.org/wikipedia/commons/4/41/Foo.jpg",
            None,
        ),
        # Other hosts must never be rewritten.
        ("https://example.com/250px-Foo.jpg", None),
        # Lookalike host must not match on a substring.
        ("https://evil-upload.wikimedia.org.attacker.test/250px-a.jpg", None),
    ],
)
def test_wikimedia_larger_url_rewrite(url, expected):
    assert _wikimedia_larger_url(url) == expected


def test_persist_prefers_hi_res_rendition(tmp_path):
    """When the larger rendition downloads, its pixels are what get
    saved — and the map stays keyed by the ORIGINAL url, since that is
    what the report markdown references.
    """
    store = ImageStore("r1", MagicMock(), base_dir=tmp_path)
    hi_url = _wikimedia_larger_url(THUMB)

    def fake_download(url, source_url=None):
        if url == hi_url:
            return _png_bytes(600, 415), "image/png"
        return _png_bytes(250, 173), "image/png"

    with patch.object(store, "_download", side_effect=fake_download):
        routes = store.persist([THUMB], url_to_alt={THUMB: "外滩"})

    assert THUMB in routes, "route must be keyed by the original URL"
    saved = tmp_path / routes[THUMB].removeprefix("/images/")
    with PILImage.open(saved) as im:
        assert im.size == (600, 415), "hi-res pixels should be saved"


def test_non_wikimedia_url_makes_no_extra_request(tmp_path):
    """The hi-res attempt must not add a wasted round-trip for hosts that
    cannot serve a parametrised rendition.
    """
    store = ImageStore("r3", MagicMock(), base_dir=tmp_path)
    url = "https://example.com/photo.png"
    calls = []

    def fake_download(u, source_url=None):
        calls.append(u)
        return _png_bytes(250, 173), "image/png"

    with patch.object(store, "_download", side_effect=fake_download):
        store.persist([url])

    assert calls == [url]


ORIGINAL = (
    "https://upload.wikimedia.org/wikipedia/commons/4/41/"
    "The_HSBC_Building_and_the_Customs_House.jpg"
)


def test_candidates_ladder_ends_at_the_original_file():
    """Measured on the live CDN: this file's source is only 500 px wide,
    so the 600 px thumbnail 400s. The original file is then the correct
    next try — and a 400 *proves* the source is under 600 px, so the
    original is guaranteed small. Order matters: bounded thumbnail first.
    """
    assert _wikimedia_candidates(THUMB) == [
        THUMB.replace("250px-", f"{_MAX_DISPLAY_PX}px-"),
        ORIGINAL,
    ]


def test_candidates_empty_for_non_wikimedia():
    assert _wikimedia_candidates("https://example.com/250px-a.jpg") == []


def test_persist_falls_through_to_original_file(tmp_path):
    """The user-reported HSBC case end to end: 600 px thumb rejected,
    original file used, short side finally clears _MIN_DISPLAY_SIDE.
    """
    store = ImageStore("r4", MagicMock(), base_dir=tmp_path)
    hi_url, orig_url = _wikimedia_candidates(THUMB)
    calls = []

    def fake_download(url, source_url=None):
        calls.append(url)
        if url == hi_url:
            raise ValueError("HTTP 400 from CDN")
        if url == orig_url:
            return _png_bytes(500, 347), "image/png"
        return _png_bytes(250, 173), "image/png"

    with patch.object(store, "_download", side_effect=fake_download):
        routes = store.persist([THUMB])

    assert calls == [hi_url, orig_url], "must try thumb before original"
    saved = tmp_path / routes[THUMB].removeprefix("/images/")
    with PILImage.open(saved) as im:
        assert im.size == (500, 347)
        assert min(im.size) >= 300


def test_persist_uses_thumb_url_when_every_rendition_fails(tmp_path):
    """If both upgrades fail, the original 250 px thumbnail must still be
    persisted — the image is never lost to a failed optimisation.
    """
    store = ImageStore("r5", MagicMock(), base_dir=tmp_path)
    hi_url, orig_url = _wikimedia_candidates(THUMB)

    def fake_download(url, source_url=None):
        if url in (hi_url, orig_url):
            raise ValueError("nope")
        return _png_bytes(250, 173), "image/png"

    with patch.object(store, "_download", side_effect=fake_download):
        routes = store.persist([THUMB])

    assert THUMB in routes
