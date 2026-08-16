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


def _resolve_darkweb_engines(
    settings_snapshot: Optional[dict] = None,
) -> tuple[str, ...]:
    """Read the darkweb engine list from settings, with module-level
    fallback. The settings key is the comma-separated string written
    by the darkweb default_params.engines entry in default_settings.json
    (e.g. ``"ahmia,torch"``); we split on ``,`` and strip whitespace.

    Keeping a module-level fallback is important for the
    ``_make_darkweb_engine` call site that has no settings_snapshot
    (e.g. the post-merge flow when a research thread is created
    without the global settings context).
    """
    if settings_snapshot is None:
        return DARKWEB_DEFAULT_ENGINES
    raw = settings_snapshot.get(
        "search.engine.web.darkweb.default_params.engines"
    )
    if isinstance(raw, dict):
        raw = raw.get("value")
    if not raw:
        return DARKWEB_DEFAULT_ENGINES
    parts = tuple(p.strip() for p in str(raw).split(",") if p.strip())
    return parts or DARKWEB_DEFAULT_ENGINES


def _make_darkweb_engine(
    instance_url: Optional[str] = None,
    settings_snapshot: Optional[dict] = None,
) -> SearXNGSearchEngine:
    """Instantiate a SearXNG client configured for darkweb engines.

    Parameters
    ----------
    instance_url : str, optional
        SearXNG instance URL. Defaults to ``DARKWEB_DEFAULT_INSTANCE_URL``
        (``http://searxng-ldr:8080`` — the in-network sidecar).
    settings_snapshot : dict, optional
        Per-research settings snapshot. When present, reads
        ``search.engine.web.darkweb.default_params.engines`` to honour
        any user-customised engine list (e.g. adding ``haystak``);
        falls back to ``DARKWEB_DEFAULT_ENGINES`` when missing.
    """
    return SearXNGSearchEngine(
        instance_url=instance_url or DARKWEB_DEFAULT_INSTANCE_URL,
        engines=list(_resolve_darkweb_engines(settings_snapshot)),
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
        ``_make_darkweb_engine().results(query)``.

    Returns
    -------
    list[dict]
        The same list, mutated in place and returned for chaining.
    """
    for r in results:
        r.setdefault("metadata", {})["source"] = "darkweb"
        r["is_darkweb"] = True
    return results