"""get_onion_proxies() returns the local CONNECT proxy URL only for .onion
hosts, and is otherwise None. Combined with the kwargs.setdefault pattern,
this layers onion-specific routing on top of any existing app.network
proxy without overriding HTTP_PROXY/HTTPS_PROXY env.
"""
from local_deep_research.security.proxy_config import get_onion_proxies


def test_onion_url_returns_proxy():
    out = get_onion_proxies("http://kx5thpx2oluwml4w.onion/path")
    assert out == {"http": "http://127.0.0.1:18080", "https": "http://127.0.0.1:18080"}


def test_onion_https_url_returns_proxy():
    out = get_onion_proxies(
        "https://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion/"
    )
    assert out is not None
    assert out["http"] == "http://127.0.0.1:18080"


def test_clearnet_returns_none():
    assert get_onion_proxies("https://example.com/") is None
    assert get_onion_proxies("http://1.1.1.1/") is None


def test_uppercase_onion_returns_proxy():
    """Case-insensitive suffix match (.ONION should also work)."""
    out = get_onion_proxies("http://EXAMPLE.ONION/")
    assert out is not None


def test_malformed_url_returns_none():
    """Garbage in, None out — never raises."""
    assert get_onion_proxies("not a url") is None
    assert get_onion_proxies("") is None