**PDF export now embeds images served by the app itself.** Research
reports rewritten by ``images.store.rewrite_markdown`` embed
relative ``/images/<id>/<fn>`` URLs, but the previous WeasyPrint
config had no ``base_url`` and the SSRF guard defaulted to
``allow_localhost=False`` — so every local image fetch failed and
the exported PDF silently had no images (every image slot was filled
with the 1×1 placeholder PNG introduced in the previous fix). The
PDF service now accepts a ``base_url`` and ``trusted_hosts`` tuple
wired through ``ExportOptions`` and the Flask route, so WeasyPrint
resolves the relative routes against the same origin that serves the
images, and the url_fetcher trusts loopback + the request host while
keeping cloud-metadata IPs (ALWAYS_BLOCKED_METADATA_IPS) blocked.
