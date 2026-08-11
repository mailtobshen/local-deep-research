from local_deep_research.images.relevance import _canonicalize_url


def test_canonicalize_trailing_slash():
    assert _canonicalize_url("https://a.com/page/") == "https://a.com/page"


def test_canonicalize_strip_whitespace():
    assert _canonicalize_url("  https://a.com/x  ") == "https://a.com/x"


def test_canonicalize_http_to_https():
    assert _canonicalize_url("http://a.com/x") == "https://a.com/x"


def test_canonicalize_www_prefix():
    assert _canonicalize_url("https://www.a.com/x") == "https://a.com/x"


def test_canonicalize_lowercase_host_and_scheme():
    assert _canonicalize_url("HTTPS://Example.COM/X") == "https://example.com/X"


def test_canonicalize_drops_fragment():
    assert _canonicalize_url("https://a.com/x#sec") == "https://a.com/x"


def test_canonicalize_keeps_query_verbatim():
    # Anti-mismatch red line: query is NEVER dropped/reordered.
    assert _canonicalize_url("https://a.com/x?id=1") == "https://a.com/x?id=1"
    assert (
        _canonicalize_url("https://a.com/x?id=1")
        != _canonicalize_url("https://a.com/x?id=2")
    ), "different query values must NOT canonicalize equal"


def test_canonicalize_empty_and_garbage_fail_closed():
    assert _canonicalize_url("") == ""
    # garbage must not raise; return stripped original
    assert _canonicalize_url("not a url") == "not a url"
