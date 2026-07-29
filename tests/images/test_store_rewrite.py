from pathlib import Path
from unittest.mock import patch

import requests

from local_deep_research.images.store import ImageStore


def _store() -> ImageStore:
    return ImageStore("test-research", db_session=None, base_dir=Path("/tmp"))


def test_rewrite_keeps_markdown_for_oversized():
    """Oversized images stay as standard markdown `![alt](route)`.

    The earlier implementation emitted `<img width=600 height=300>` HTML
    to enforce a 600px long-side cap; we dropped that so the report
    body stays pure markdown. WebUI CSS handles display-size capping
    downstream (see styles.css `.ldr-markdown-content img`).
    """
    md = "![长隆](https://example.com/big.jpg)"
    sizes = {"https://example.com/big.jpg": (2000, 1000)}
    routes = {"https://example.com/big.jpg": "/images/abc.jpg"}

    out = _store().rewrite_markdown(md, routes, sizes)

    assert out == "![长隆](/images/abc.jpg)"
    assert "<img" not in out


def test_rewrite_keeps_markdown_for_small_or_unknown():
    md = "![small](https://example.com/small.jpg)"
    sizes = {"https://example.com/small.jpg": (200, 150)}
    routes = {"https://example.com/small.jpg": "/images/small.jpg"}

    out = _store().rewrite_markdown(md, routes, sizes)

    assert out == "![small](/images/small.jpg)"


def test_rewrite_drops_unpersisted_image_entirely():
    """An image whose URL has no local route (download failed 3x) must be
    removed from the markdown completely — no remote URL, no <img>, no
    broken alt text leaking into the final report.
    """
    md = (
        "Before text.\n\n"
        "![lost](https://example.com/failed.jpg)\n\n"
        "After text."
    )
    # Only the successful image has a route; failed.jpg has none.
    routes = {"https://example.com/ok.jpg": "/images/ok.jpg"}
    sizes = {"https://example.com/ok.jpg": (200, 150)}

    out = _store().rewrite_markdown(md, routes, sizes)

    assert "https://example.com/failed.jpg" not in out
    assert "failed.jpg" not in out
    assert "<img" not in out
    assert "![lost]" not in out
    assert "Before text." in out
    assert "After text." in out


def test_rewrite_preserves_body_text_drops_only_image_marker():
    """A failed image sits between body paragraphs. Only the image marker
    (![alt](url)) is removed; every surrounding paragraph survives
    verbatim and any successfully-persisted image is still rewritten to
    its local route. This guards against over-broad deletion that could
    eat report body text.
    """
    md = (
        "# 陈家祠\n\n"
        "陈家祠建于清代，是标志性建筑。\n\n"
        "![陈家祠堂](https://example.com/failed.jpg)\n\n"
        "内部装饰精美，值得细看。\n\n"
        "![正门](https://example.com/ok.jpg)\n\n"
        "门票便宜，推荐前往。"
    )
    routes = {"https://example.com/ok.jpg": "/images/ok.jpg"}
    sizes = {"https://example.com/ok.jpg": (200, 150)}

    out = _store().rewrite_markdown(md, routes, sizes)

    # Failed image fully gone (URL, alt, marker).
    assert "failed.jpg" not in out
    assert "![陈家祠堂]" not in out
    # Persisted image rewritten to local route.
    assert "/images/ok.jpg" in out
    # ALL body paragraphs survive verbatim, untouched.
    assert "# 陈家祠" in out
    assert "陈家祠建于清代，是标志性建筑。" in out
    assert "内部装饰精美，值得细看。" in out
    assert "门票便宜，推荐前往。" in out


def test_persist_retries_on_requests_timeout(tmp_path):
    """A requests.exceptions.ReadTimeout (the real type safe_get raises on
    read timeout) must be retried up to _MAX_ATTEMPTS, not fail on the
    first attempt. Guards against the bug where _RETRIABLE only listed the
    built-in TimeoutError, which requests.* timeouts do not subclass.
    """
    store = ImageStore("rid", db_session=None, base_dir=tmp_path)
    calls = {"n": 0}

    def fake_download(url, source_url=None):
        calls["n"] += 1
        raise requests.exceptions.ReadTimeout("read timed out")

    with patch.object(store, "_download", side_effect=fake_download):
        routes = store.persist(["https://x/a.jpg"])

    assert routes == {}  # all attempts failed → no route
    import local_deep_research.images.store as store_mod
    assert calls["n"] == store_mod._MAX_ATTEMPTS
