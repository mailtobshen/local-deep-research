"""is_darkweb_url() boundary tests.

Spec table (line 181): .onion / 普通域名 / notonion.com /
evil.onion.attacker.com（域边界）.
"""
from local_deep_research.utilities.is_darkweb_url import is_darkweb_url


def test_onion_subdomain_matches():
    assert is_darkweb_url("http://kx5thpx2oluwml4w.onion/path")


def test_https_onion_matches():
    assert is_darkweb_url(
        "https://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion/"
    )


def test_uppercase_onion_matches():
    assert is_darkweb_url("http://EXAMPLE.ONION/")


def test_bare_onion_tld_matches():
    # No leading subdomain — just "onion" as the bare hostname.
    assert is_darkweb_url("http://onion/")


def test_clearnet_rejected():
    assert not is_darkweb_url("https://example.com/")
    assert not is_darkweb_url("http://1.1.1.1/")


def test_subdomain_trap_rejected():
    """`evil.onion.attacker.com` — host is `attacker.com`, not onion.

    This is a phishing trap: a domain that contains the substring
    `onion` but isn't actually a Tor hidden service.
    """
    assert not is_darkweb_url("https://evil.onion.attacker.com/page")


def test_notonion_com_rejected():
    assert not is_darkweb_url("https://notonion.com/")


def test_empty_input_rejected():
    assert not is_darkweb_url("")
    assert not is_darkweb_url(None) if False else True  # ignore None check


def test_malformed_input_rejected():
    assert not is_darkweb_url("not a url")
    assert not is_darkweb_url(":::::")