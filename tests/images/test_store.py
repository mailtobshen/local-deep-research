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
    # Unmapped url = download failed all retries → dropped entirely, not
    # left as a remote URL in the final report.
    assert "https://y/b.jpg" not in out


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


def test_download_passes_trusted_host_suffixes_to_safe_get(tmp_path):
    """ImageStore._download must forward the image-CDN suffix allowlist
    to safe_get so that Instagram/Ctrip/etc. CDN URLs aren't rejected
    by the SSRF guard at validation time."""
    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    with patch(
        "local_deep_research.security.safe_requests.safe_get"
    ) as sg:
        sg.return_value = MagicMock(status_code=200, content=b"\x89PNG", headers={})
        store._download("https://scontent-hkg1-2.cdninstagram.com/x.jpg")
    kwargs = sg.call_args.kwargs
    assert "trusted_host_suffixes" in kwargs
    assert "cdninstagram.com" in kwargs["trusted_host_suffixes"]
    assert "fbcdn.net" in kwargs["trusted_host_suffixes"]


def test_persist_logs_persist_fail_on_value_error(tmp_path, loguru_caplog):
    """Outer `except Exception` must surface as [IMG-TRACE] PERSIST_FAIL."""
    import logging
    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    with patch.object(
        store, "_download",
        side_effect=ValueError("URL failed security validation (possible SSRF): https://x/a.jpg"),
    ):
        with loguru_caplog.at_level(logging.WARNING):
            routes = store.persist(["https://x/a.jpg"])
    assert routes == {}
    assert "[IMG-TRACE] PERSIST_FAIL" in loguru_caplog.text
    assert "https://x/a.jpg" in loguru_caplog.text


def test_persist_logs_persist_download_fail_before_re_raise(tmp_path, loguru_caplog):
    """The `except Exception: raise` block in _download must now log
    PERSIST_DOWNLOAD_FAIL before re-raising so the per-attempt reason
    is observable even though the outer PERSIST_FAIL will fire too."""
    import logging
    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    with patch(
        "local_deep_research.security.safe_requests.safe_get",
        side_effect=ConnectionError("DNS hiccup"),
    ):
        with loguru_caplog.at_level(logging.WARNING):
            store.persist(["https://x/a.jpg"])
    assert "[IMG-TRACE] PERSIST_DOWNLOAD_FAIL" in loguru_caplog.text
    assert "[IMG-TRACE] PERSIST_FAIL" in loguru_caplog.text


def test_persist_logs_persist_record_fail_when_db_raises(tmp_path, loguru_caplog):
    """_record swallow must now log PERSIST_RECORD_FAIL (warning) so DB
    loss is observable. URL still gets into url_to_route because _record
    doesn't propagate — that behavior is unchanged."""
    import logging
    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    # Patch the inner DB operation (commit) so the exception fires from
    # inside _record's try/except — exercising the real swallow path.
    store.db_session.commit.side_effect = RuntimeError("db down")
    with patch(
        "local_deep_research.security.safe_requests.safe_get",
        return_value=MagicMock(status_code=200, content=b"\x89PNG", headers={}),
    ):
        with loguru_caplog.at_level(logging.WARNING):
            routes = store.persist(["https://x/a.jpg"])
    assert "https://x/a.jpg" in routes  # swallow behavior preserved
    assert "[IMG-TRACE] PERSIST_RECORD_FAIL" in loguru_caplog.text


def test_probe_size_logs_at_debug_level_on_failure(loguru_caplog):
    import logging
    with loguru_caplog.at_level(logging.DEBUG):
        result = _probe_size(b"not an image", url="https://example.com/x.jpg")
    assert result is None
    assert "[IMG-TRACE] PROBE_SIZE_FAIL" in loguru_caplog.text


def test_persist_stashes_url_to_size(tmp_path):
    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    with patch.object(store, "_download") as dl:
        # 1200x800 — over threshold
        dl.return_value = (_png_bytes(1200, 800), "image/png")
        store.persist(["https://x/big.jpg"])
    sizes = getattr(store, "_last_url_to_size", {})
    assert sizes.get("https://x/big.jpg") == (1200, 800)


def test_rewrite_markdown_emits_html_for_landscape_oversize(tmp_path):
    """Oversized landscape image becomes an <img> with width=600, height=400."""
    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    md = "![t](https://x/a.jpg)"
    sizes = {"https://x/a.jpg": (1200, 800)}
    out = store.rewrite_markdown(
        md, {"https://x/a.jpg": "/local.png"}, url_to_size=sizes
    )
    assert out.startswith("<img")
    assert 'src="/local.png"' in out
    assert f'width="{_MAX_DISPLAY_PX}"' in out
    assert 'height="400"' in out
    assert "loading=\"lazy\"" in out


def test_rewrite_markdown_emits_html_for_portrait_oversize(tmp_path):
    """Oversized portrait image becomes an <img> with height=600, width=300."""
    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    md = "![t](https://x/a.jpg)"
    sizes = {"https://x/a.jpg": (800, 1200)}
    out = store.rewrite_markdown(
        md, {"https://x/a.jpg": "/local.png"}, url_to_size=sizes
    )
    assert out.startswith("<img")
    assert f'height="{_MAX_DISPLAY_PX}"' in out
    assert 'width="400"' in out


def test_rewrite_markdown_keeps_markdown_for_small_images(tmp_path):
    """Image whose long side is <= threshold stays as ![alt](route) markdown."""
    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    md = "![t](https://x/a.jpg)"
    sizes = {"https://x/a.jpg": (400, 300)}
    out = store.rewrite_markdown(
        md, {"https://x/a.jpg": "/local.png"}, url_to_size=sizes
    )
    assert out == "![t](/local.png)"


def test_rewrite_markdown_unknown_size_keeps_markdown(tmp_path):
    """Missing size entry → keep markdown form (graceful fallback)."""
    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    md = "![t](https://x/a.jpg)"
    out = store.rewrite_markdown(
        md, {"https://x/a.jpg": "/local.png"}, url_to_size={}
    )
    assert out == "![t](/local.png)"


def test_rewrite_markdown_uses_stashed_size_from_persist(tmp_path):
    """Without explicit url_to_size, rewrite falls back to instance stash."""
    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    with patch.object(store, "_download") as dl:
        dl.return_value = (_png_bytes(1500, 1000), "image/png")
        store.persist(["https://x/big.jpg"])
    md = "![t](https://x/big.jpg)"
    out = store.rewrite_markdown(md, {"https://x/big.jpg": "/local.png"})
    assert out.startswith("<img")
    assert f'width="{_MAX_DISPLAY_PX}"' in out


def test_download_sets_referer_from_source_url_origin(tmp_path):
    """Anti-hotlink: Referer is built from the source page's scheme+host."""
    from local_deep_research.security import safe_requests

    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.headers = {"Content-Type": "image/jpeg"}
    fake_resp.content = b"\xff\xd8\xff fake"
    with patch.object(
        safe_requests, "safe_get", return_value=fake_resp
    ) as sg:
        store._download(
            "http://www.fuluyou.com/upfiles/2025/x.jpg",
            source_url="http://www.fuluyou.com/info/1877",
        )
    kwargs = sg.call_args.kwargs
    assert kwargs["headers"]["Referer"] == "http://www.fuluyou.com/"
    assert "User-Agent" in kwargs["headers"]
    assert "Mozilla" in kwargs["headers"]["User-Agent"]


def test_download_omits_referer_when_no_source_url(tmp_path):
    """No source_url → no Referer header (graceful, not crash)."""
    from local_deep_research.security import safe_requests

    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.headers = {"Content-Type": "image/png"}
    fake_resp.content = b"\x89PNG fake"
    with patch.object(
        safe_requests, "safe_get", return_value=fake_resp
    ) as sg:
        store._download("https://cdn.example.com/x.png", source_url=None)
    kwargs = sg.call_args.kwargs
    assert "Referer" not in kwargs["headers"]
    assert "User-Agent" in kwargs["headers"]


def test_download_omits_referer_for_unparseable_source_url(tmp_path):
    """Pathological source_url (no scheme/netloc) → no Referer header."""
    from local_deep_research.security import safe_requests

    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.headers = {"Content-Type": "image/png"}
    fake_resp.content = b"\x89PNG fake"
    with patch.object(
        safe_requests, "safe_get", return_value=fake_resp
    ) as sg:
        store._download(
            "https://cdn.example.com/x.png", source_url="not-a-url"
        )
    kwargs = sg.call_args.kwargs
    assert "Referer" not in kwargs["headers"]


def test_persist_passes_source_url_to_download(tmp_path):
    """The url_to_source map flows through to safe_get's Referer."""
    from local_deep_research.security import safe_requests

    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.headers = {"Content-Type": "image/jpeg"}
    fake_resp.content = b"\xff\xd8\xff fake"
    with patch.object(
        safe_requests, "safe_get", return_value=fake_resp
    ) as sg:
        store.persist(
            ["https://cdn.example.com/img.jpg"],
            url_to_source={
                "https://cdn.example.com/img.jpg": (
                    "https://news.example.com/article/1",
                    "Article",
                )
            },
        )
    kwargs = sg.call_args.kwargs
    assert kwargs["headers"]["Referer"] == "https://news.example.com/"


# ---- Firecrawl fallback ---------------------------------------------------

def test_403_with_firecrawl_falls_back(tmp_path):
    """safe_get 403 → Firecrawl re-fetches source, matches basename, downloads."""
    from local_deep_research.security import safe_requests

    # Rendered source page HTML contains <img src="...x.jpg"> with a
    # *different* signed-suffix URL — that's what Firecrawl hands back.
    source_html = (
        '<html><body>'
        '<img src="https://cdn.example.com/upfiles/x.jpg?sig=abc" />'
        '</body></html>'
    )
    fc = MagicMock()
    fc.scrape.return_value = {"markdown": "x", "html": source_html}

    fast_resp = MagicMock(status_code=403, headers={})

    # Second safe_get call (for the matched src) succeeds with bytes.
    success_resp = MagicMock(
        status_code=200,
        headers={"Content-Type": "image/jpeg"},
        content=b"\xff\xd8\xff fake",
    )

    store = ImageStore(
        "rid", MagicMock(), base_dir=tmp_path, firecrawl_client=fc
    )

    with patch.object(
        safe_requests,
        "safe_get",
        side_effect=[fast_resp, success_resp],
    ) as sg:
        data, ctype = store._download(
            "https://cdn.example.com/upfiles/x.jpg?sig=old",
            source_url="https://www.example.com/article/1",
        )

    # First call: fast path with Referer to the source page origin.
    assert sg.call_args_list[0].kwargs["headers"]["Referer"] == (
        "https://www.example.com/"
    )
    # Second call (the fallback download) uses the matched src.
    assert (
        sg.call_args_list[1].args[0]
        == "https://cdn.example.com/upfiles/x.jpg?sig=abc"
    )
    fc.scrape.assert_called_once_with(
        "https://www.example.com/article/1", include_html=True
    )
    assert data == b"\xff\xd8\xff fake"
    assert ctype == "image/jpeg"


def test_403_without_firecrawl_does_not_call_fallback(tmp_path):
    """No firecrawl_client configured → just raise HTTPError (back-compat)."""
    from requests import HTTPError

    from local_deep_research.security import safe_requests

    fast_resp = MagicMock(status_code=403, headers={})
    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)  # no fc

    with patch.object(
        safe_requests, "safe_get", return_value=fast_resp
    ):
        try:
            store._download(
                "https://cdn.example.com/x.jpg",
                source_url="https://example.com/",
            )
        except HTTPError:
            pass
        else:
            raise AssertionError("expected HTTPError when no fallback available")


def test_403_without_source_url_does_not_call_fallback(tmp_path):
    """Even with firecrawl_client, no source_url → raise HTTPError."""
    from requests import HTTPError

    from local_deep_research.security import safe_requests

    fc = MagicMock()
    store = ImageStore(
        "rid", MagicMock(), base_dir=tmp_path, firecrawl_client=fc
    )
    with patch.object(
        safe_requests,
        "safe_get",
        return_value=MagicMock(status_code=403, headers={}),
    ):
        try:
            store._download("https://cdn.example.com/x.jpg", source_url=None)
        except HTTPError:
            pass
        else:
            raise AssertionError(
                "expected HTTPError when source_url missing"
            )
    fc.scrape.assert_not_called()


def test_firecrawl_no_match_raises(tmp_path):
    """Firecrawl rendered HTML has no matching img → HTTPError wrapping 403."""
    from requests import HTTPError

    from local_deep_research.security import safe_requests

    fc = MagicMock()
    fc.scrape.return_value = {
        "markdown": "x",
        "html": '<html><body><img src="https://other.com/y.jpg" /></body></html>',
    }
    store = ImageStore(
        "rid", MagicMock(), base_dir=tmp_path, firecrawl_client=fc
    )
    with patch.object(
        safe_requests,
        "safe_get",
        return_value=MagicMock(status_code=403, headers={}),
    ):
        try:
            store._download(
                "https://cdn.example.com/upfiles/x.jpg",
                source_url="https://www.example.com/article/1",
            )
        except HTTPError as e:
            assert "403" in str(e)
        else:
            raise AssertionError("expected HTTPError wrapping original status")
    fc.scrape.assert_called_once()


def test_firecrawl_returns_no_html_raises(tmp_path):
    """Firecrawl returns no html (scrape failed) → HTTPError on original 403."""
    from requests import HTTPError

    from local_deep_research.security import safe_requests

    fc = MagicMock()
    fc.scrape.return_value = {"markdown": "x", "html": None}
    store = ImageStore(
        "rid", MagicMock(), base_dir=tmp_path, firecrawl_client=fc
    )
    with patch.object(
        safe_requests,
        "safe_get",
        return_value=MagicMock(status_code=403, headers={}),
    ):
        try:
            store._download(
                "https://cdn.example.com/upfiles/x.jpg",
                source_url="https://www.example.com/article/1",
            )
        except HTTPError:
            pass
        else:
            raise AssertionError("expected HTTPError")
