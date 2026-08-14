"""Darkweb (SearXNG + ahmia/torch) search engine factory.

Reuses SearXNGSearchEngine; configures it with the darkweb engine list
and the ``onions`` category. Tor egress is handled server-side by
SearXNG's own ``proxies: socks5h://ldr-tor:9050`` setting — no Tor
client is needed in this process for the search phase. Full-content
fetch is handled by the onion CONNECT tunnel (see
security/proxy_config.get_onion_proxies).
"""
from typing import Optional

from local_deep_research.web_search_engines.engines.search_engine_searxng import (
    SearXNGSearchEngine,
)

DARKWEB_DEFAULT_INSTANCE_URL = "http://searxng-ldr:8080"
DARKWEB_DEFAULT_ENGINES = ("ahmia", "torch")
DARKWEB_DEFAULT_CATEGORIES = ("onions",)
DARKWEB_DEFAULT_MAX_RESULTS = 10


def _make_darkweb_engine(
    instance_url: Optional[str] = None,
) -> SearXNGSearchEngine:
    """Instantiate a SearXNG client configured for darkweb engines.

    Parameters
    ----------
    instance_url : str, optional
        SearXNG instance URL. Defaults to ``DARKWEB_DEFAULT_INSTANCE_URL``
        (``http://searxng-ldr:8080`` — the in-network sidecar).
    """
    return SearXNGSearchEngine(
        instance_url=instance_url or DARKWEB_DEFAULT_INSTANCE_URL,
        engines=list(DARKWEB_DEFAULT_ENGINES),
        categories=list(DARKWEB_DEFAULT_CATEGORIES),
        max_results=DARKWEB_DEFAULT_MAX_RESULTS,
    )


def tag_darkweb(results: list[dict]) -> list[dict]:
    """Mark each result as a darkweb hit.

    Tags ``is_darkweb=True`` and ``metadata.source="darkweb"`` so
    downstream consumers can branch on provenance. The URL is the
    authoritative signal (``.onion`` suffix); this tagging is a
    redundancy for grep-ability and for callers that filter before
    URL parsing.

    Parameters
    ----------
    results : list[dict]
        Raw SearXNG result dicts from
        ``_make_darkweb_engine().search()``.

    Returns
    -------
    list[dict]
        The same list, mutated in place and returned for chaining.
    """
    for r in results:
        r.setdefault("metadata", {})["source"] = "darkweb"
        r["is_darkweb"] = True
    return results