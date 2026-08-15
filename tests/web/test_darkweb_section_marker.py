"""Phase-3: chapter section marker.

When a section's body cites a .onion URL, append an explicit localized
note so readers know the section contains darkweb-sourced claims.
"""
from local_deep_research.utilities.is_darkweb_url import is_darkweb_url

import re as _re_for_test
_URL_RE = _re_for_test.compile(r"https?://[^\s\)\]]+")


def _make_section_marker(section_body: str, lang: str = "zh-CN") -> str:
    """Mirror the runtime logic from report_generator._format_final_report."""
    if any(is_darkweb_url(u) for u in _URL_RE.findall(section_body)):
        if lang and lang.startswith("zh"):
            return section_body + (
                "\n\n*本节包含来自暗网（.onion）来源的信息。*\n"
            )
        return section_body + (
            "\n\n*This section contains claims "
            "sourced from darkweb (.onion) providers.*\n"
        )
    return section_body


def test_clearnet_section_gets_no_marker():
    body = "see [1](https://example.com/page) for context."
    out = _make_section_marker(body)
    assert "暗网" not in out
    assert "darkweb" not in out.lower()


def test_darkweb_section_gets_zh_marker():
    body = "see [D1](http://kx5thpx2oluwml4w.onion/page) for context."
    out = _make_section_marker(body, lang="zh-CN")
    assert "本节包含来自暗网（.onion）来源的信息" in out


def test_darkweb_section_gets_en_marker():
    body = "see [D1](http://kx5thpx2oluwml4w.onion/page) for context."
    out = _make_section_marker(body, lang="en")
    assert "This section contains claims" in out
    assert ".onion" in out


def test_mixed_section_triggers_marker():
    """A section with at least one .onion URL gets the marker."""
    body = (
        "see [1](https://example.com/) and "
        "[D1](http://kx5thpx2oluwml4w.onion/page)"
    )
    out = _make_section_marker(body)
    assert "暗网" in out


def test_subdomain_trap_does_not_trigger_marker():
    """`evil.onion.attacker.com` — not actually a Tor hidden service."""
    body = "phishing at [1](https://evil.onion.attacker.com/page)"
    out = _make_section_marker(body)
    assert "暗网" not in out


def test_uppercase_onion_triggers_marker():
    body = "[1](https://EXAMPLE.ONION/page)"
    out = _make_section_marker(body)
    assert "暗网" in out