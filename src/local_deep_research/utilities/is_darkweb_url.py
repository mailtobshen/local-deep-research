"""URL-level darkweb detection.

The darkweb pipeline tags SearXNG results with `metadata.source="darkweb"`
at the search-engine layer, but downstream consumers (image pipeline,
citation formatter, report rendering) need a cheap URL-only check
that works even when the original metadata has been dropped by a
list/dict copy somewhere along the way.

The authority is the URL itself: `.onion` is the Tor reserved TLD,
and no clearnet domain can ever end in `.onion`. So we accept the
subdomain as well (e.g. `exppyuzz4wqqyqhjn.onion/path` matches,
but `notonion.com` and `evil.onion.attacker.com` do not — the latter
is `attacker.com`).
"""
from __future__ import annotations

from urllib.parse import urlparse

_ONION_SUFFIX = ".onion"


def is_darkweb_url(url: str) -> bool:
    """Return True iff *url*'s hostname ends in ``.onion``.

    Args:
        url: Any string. Malformed URLs and empty inputs return False.

    Returns:
        True for ``http://abc.onion/path``, ``https://duckduckgo.onion``,
        and bare ``onion`` host. False for clearnet domains, empty
        strings, and subdomain traps like ``evil.onion.attacker.com``
        (host is ``attacker.com``, not onion).
    """
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except (ValueError, AttributeError):
        return False
    if not host:
        return False
    # Match "onion" exactly (bare TLD) or any subdomain ending in ".onion".
    return host == "onion" or host.endswith(_ONION_SUFFIX)