"""
Centralized network proxy + TLS configuration for LDR outbound requests.

This module is the single source of truth for the ``app.network.*`` settings
(:ref:`network-proxy`). It exposes:

- :func:`get_proxy_settings` — read the proxy URL from env/DB and return a
  ``requests``-style ``{"http", "https"}`` mapping (or ``None``).
- :func:`should_bypass_proxy` — whether a target URL is local/private and must
  NOT be sent through the external proxy (protects Ollama/SearXNG/LMStudio).
- :func:`apply_proxy_to_wikipedia_env` — the ``wikipedia`` PyPI library has no
  proxy API but honors ``HTTP_PROXY``/``HTTPS_PROXY``/``NO_PROXY`` env via its
  bare ``requests.get``; this writes those env vars when a proxy is configured.
- :func:`get_allow_insecure_tls` — read the insecure-TLS fallback toggle
  (kept for backward compatibility; no longer gates Stage 3).
- :func:`fetch_with_cert_fallback` — TLS fallback for downloaders:
  verify → AIA intermediate-CA fetch → unconditional one-off ``verify=False``
  retry on any remaining SSLError. ``verify=True`` remains the default for all
  other requests; nothing "insecure" is persisted.

Why this exists: WSL2/container environments cannot reach the public internet
directly (zh.wikipedia.org, etc. time out) and must egress through a forward
proxy. Previously only SearXNG had a proxy configured (hardcoded in
docker-compose); LDR's own downloaders and the ``wikipedia`` library went
direct and failed. This module funnels everything through one setting.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from loguru import logger

from ..settings.manager import check_env_setting
from .security_monitor import record_tls_fallback
from .ssrf_validator import is_ip_blocked


# Setting keys (see defaults/default_settings.json, category "app_network").
_PROXY_ENABLED_KEY = "app.network.proxy_enabled"
_PROXY_URL_KEY = "app.network.proxy_url"
_ALLOW_INSECURE_TLS_KEY = "app.network.allow_insecure_tls"


def _get_setting(key: str, default: Any = None) -> Any:
    """Read a setting, env-first, falling back to DB.

    ``check_env_setting`` handles the ``LDR_<KEY>`` env-var override. If no env
    override is present we try the DB-backed ``SettingsManager``. When neither
    is available (e.g. pure library usage with no DB), ``default`` is returned.
    """
    env_value = check_env_setting(key)
    if env_value is not None:
        return env_value
    try:
        from ..utilities.db_utils import get_settings_manager

        manager = get_settings_manager()
        return manager.get_setting(key, default, check_env=False)
    except Exception:
        # No DB context (e.g. CLI/library use, or DB not initialized yet).
        return default


def _truthy(value: Any) -> bool:
    """Parse a checkbox-style setting value to bool."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("true", "1", "yes", "on", "enabled")


def get_proxy_url() -> Optional[str]:
    """Return the configured proxy URL string, or ``None`` if unset/blank."""
    url = _get_setting(_PROXY_URL_KEY, None)
    if url is None:
        return None
    url = str(url).strip()
    return url or None


def get_proxy_settings() -> Optional[Dict[str, str]]:
    """Return a ``requests``-style proxies dict, or ``None`` when disabled.

    When ``app.network.proxy_enabled`` is true and a proxy URL is set, returns
    ``{"http": url, "https": url}`` (``requests`` accepts a single URL mapped
    to both schemes for HTTP and HTTPS targets alike, including ``socks5h://``).

    Returns ``None`` when the proxy is disabled or the URL is blank, so callers
    can do ``kwargs.setdefault("proxies", get_proxy_settings())`` harmlessly.
    """
    if not _truthy(_get_setting(_PROXY_ENABLED_KEY, False)):
        return None
    url = get_proxy_url()
    if not url:
        return None
    return {"http": url, "https": url}


def get_allow_insecure_tls() -> bool:
    """Whether the insecure-TLS fallback is enabled (env/DB)."""
    return _truthy(_get_setting(_ALLOW_INSECURE_TLS_KEY, False))


# Hostnames that always bypass the proxy regardless of IP resolution.
_BYPASS_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
    }
)


def _host_is_private(host: str) -> bool:
    """True if ``host`` resolves to (or literally is) a loopback/private IP.

    Literal IPs are checked directly. Hostnames are resolved via the OS
    resolver; if any resolved address is loopback/private, the host is treated
    as private. On resolution failure we conservatively return ``False`` (do
    NOT bypass) so an unresolved public host still goes through the proxy.
    """
    if not host:
        return False
    if host.lower() in _BYPASS_HOSTNAMES:
        return True

    # Literal IP?
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None

    if ip is not None:
        # In strict mode (no allow flags) is_ip_blocked returns True for
        # loopback/private/CGNAT/link-local ranges — exactly the set we want to
        # bypass. Allowing these would invert the result, so we call it strict.
        return is_ip_blocked(str(ip))

    # Hostname — resolve and check each address.
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            if is_ip_blocked(addr):
                return True
        except ValueError:
            continue
    return False


def _host_of_url(url: str) -> Optional[str]:
    """Return the bare hostname/IP of ``url``, or ``None`` if not parseable.

    Used to build plain-host ``NO_PROXY`` entries that ``httpx`` (unlike
    ``requests``) can match — it cannot match CIDR ranges, only literal hosts.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return None
    host = parsed.hostname
    return host or None


def _is_bare_ipv6(entry: str) -> bool:
    """True if ``entry`` is a bare IPv6 literal/CIDR (no scheme, no brackets).

    Such entries crash httpx when present in NO_PROXY (URLPattern raises
    ``InvalidURL: Invalid port: ':'``), so they must be filtered out. A bare
    IPv6 literal contains two or more colons and no ``://`` and no ``[``.
    """
    if not entry or "://" in entry or "[" in entry:
        return False
    return entry.count(":") >= 2


def should_bypass_proxy(url: str) -> bool:
    """Whether ``url`` should skip the configured proxy.

    Returns ``True`` for loopback/RFC1918/CGNAT/link-local targets so local
    services (Ollama on :11434, SearXNG on :8080, LMStudio) are reached
    directly instead of being tunneled through the external proxy. This mirrors
    the semantics of ``requests``' ``NO_PROXY`` for private ranges.
    """
    if not url:
        return True
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False
    host = parsed.hostname
    if not host:
        return False
    return _host_is_private(host)


def apply_proxy_to_wikipedia_env() -> None:
    """Populate ``HTTP_PROXY``/``HTTPS_PROXY``/``NO_PROXY`` for the wikipedia lib.

    The ``wikipedia`` PyPI library makes requests via a bare
    ``requests.get(API_URL, ...)`` with no session and no ``proxies=`` hook
    (see ``wikipedia/wikipedia.py``). ``requests.get`` honors the
    ``HTTP_PROXY``/``HTTPS_PROXY``/``NO_PROXY`` environment variables when
    ``trust_env`` is True (the default), so writing these env vars is the only
    way to route it through a proxy.

    This is a process-level side effect. LDR is a single-process app and the
    proxy is operator-opted-in, so this is acceptable; we log a warning so the
    behavior is discoverable. ``NO_PROXY`` covers loopback + RFC1918 so any
    local Wikipedia mirror is still reached directly.
    """
    proxies = get_proxy_settings()
    if not proxies:
        return
    url = proxies["http"]
    # Merge with any pre-existing NO_PROXY rather than clobbering it.
    existing_no_proxy = os.environ.get("NO_PROXY", "") or os.environ.get(
        "no_proxy", ""
    )
    # NO_PROXY has two audiences with DIFFERENT matching semantics:
    #
    # 1. ``requests`` / ``urllib`` (used by the ``wikipedia`` lib) honor CIDR
    #    notation, so ``172.16.0.0/12`` correctly exempts every RFC1918 host.
    #
    # 2. ``httpx`` (used by the ``ollama`` / ``openai`` / ``langchain_*`` LLM
    #    clients, all built with ``trust_env=True``) does NOT honor CIDR — a
    #    ``NO_PROXY`` entry of ``172.16.0.0/12`` is parsed as the literal
    #    hostname "172.16.0.0", so it never matches a real private host like
    #    the Ollama gateway ``172.25.128.1``. The Ollama call is then tunneled
    #    through the forward proxy, which returns a ``500 Internal Privoxy
    #    Error`` HTML page that the ``ollama`` lib surfaces as a
    #    ``ResponseError`` — crashing the LangGraph agent strategy.
    #
    # We therefore emit BOTH the CIDR ranges (for requests/urllib) AND a set of
    # plain IPs/hostnames for the concrete local services LDR talks to
    # (Ollama/SearXNG/LMStudio), which httpx CAN match. CIDR ranges are too
    # large to enumerate, so we add the literal hosts pulled from the configured
    # local-service URLs instead. This keeps LLM calls direct while leaving the
    # wikipedia CIDR bypass intact.
    no_proxy_parts = {
        # CIDR ranges — honored by requests/urllib (wikipedia lib).
        "localhost",
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "169.254.0.0/16",
        # Plain IPs/hostnames — honored by httpx (ollama/openai LLM clients).
        # These are the concrete local-service targets LDR reaches directly;
        # without them httpx tunnels LLM calls through the forward proxy and
        # the proxy returns a 500 Privoxy error that kills the agent run.
        "localhost",
        "127.0.0.1",
        "searxng-ldr",
        "ldr-local",
    }
    # Add any IP/hostname embedded in configured local-service URLs
    # (Ollama/LMStudio/llama.cpp/SearXNG base URLs), so httpx bypasses the
    # proxy for the very hosts that must not be proxied.
    for candidate in (
        os.environ.get("LDR_LLM_OLLAMA_URL", ""),
        os.environ.get("OLLAMA_HOST", ""),
        os.environ.get("LDR_SEARCH_ENGINE_WEB_SEARXNG_DEFAULT_PARAMS_INSTANCE_URL", ""),
    ):
        host = _host_of_url(candidate)
        if host:
            no_proxy_parts.add(host)
    # NOTE: bare IPv6 entries ("::1", "fc00::/7", "fe80::/10") are deliberately
    # OMITTED. httpx parses each NO_PROXY entry as a URLPattern, and a bare
    # IPv6 literal like "::1" is misparsed — the second ":" is treated as a
    # host:port separator, so int(":") raises and httpx raises
    # ``InvalidURL: Invalid port: ':'``. This crashes any httpx-based client
    # constructed while these env vars are set, most critically the ``ollama``
    # library, whose ``__init__`` eagerly builds ``Client()`` (and thus an
    # ``httpx.Client``) at import time — so merely importing ChatOllama during
    # a research task fails before any network call. Bracketed forms
    # ("[::1]") are also rejected by httpx's URLPattern. IPv6 loopback/private
    # mirrors are rare; if one is ever needed it must be added as a hostname,
    # not a bare IPv6 literal.
    if existing_no_proxy:
        for p in existing_no_proxy.split(","):
            p = p.strip()
            if not p:
                continue
            # Drop bare IPv6 entries from a pre-existing NO_PROXY (e.g. a host
            # shell that exported NO_PROXY=::1). httpx's get_environment_proxies
            # wraps IPv6 hosts as ``all://[<host>]`` and URLPattern then chokes
            # on the second ':' of a bare CIDR like ``fc00::/7`` (raises
            # ``InvalidURL: Invalid port: ':'``), crashing every httpx client
            # built with trust_env — most critically the ollama lib. A bare
            # ``::1`` happens to parse, but CIDR IPv6 ranges do not; to be safe
            # we drop ALL bare IPv6 literals here. IPv6 mirrors are rare; if one
            # is needed it must be added as a bracketed URL-form entry
            # (``all://[::1]``) upstream, not a bare literal in NO_PROXY.
            if _is_bare_ipv6(p):
                logger.debug(
                    "Dropping bare IPv6 NO_PROXY entry {!r} — httpx cannot "
                    "parse bare IPv6/CIDR literals and would crash LLM clients.",
                    p,
                )
                continue
            no_proxy_parts.add(p)
    no_proxy = ",".join(sorted(no_proxy_parts))
    os.environ["HTTP_PROXY"] = url
    os.environ["HTTPS_PROXY"] = url
    os.environ["NO_PROXY"] = no_proxy
    # Lower-case variants some tools prefer.
    os.environ["http_proxy"] = url
    os.environ["https_proxy"] = url
    os.environ["no_proxy"] = no_proxy
    logger.warning(
        "Network proxy enabled: HTTP_PROXY/HTTPS_PROXY set to {} for the "
        "wikipedia library and any trust_env requests. NO_PROXY covers "
        "loopback/private ranges. (app.network.proxy_enabled)",
        url,
    )


def apply_timeout_to_wikipedia_requests(
    timeout: tuple = (10, 30),
) -> None:
    """Bound every ``wikipedia`` library API call to a connect/read ``timeout``.

    The ``wikipedia`` PyPI library's ``_wiki_request`` calls
    ``requests.get(API_URL, ...)`` with **no** ``timeout=`` argument. When
    egress goes through a flaky forward proxy that accepts the connection but
    never responds, ``requests.get`` blocks indefinitely — there is no read
    timeout, no socket timeout, and no outer deadline. This stalls the
    research thread on a single hung Wikipedia summary/search call (observed
    20+ minute gaps, eventually requiring a container restart).

    This is a one-time, idempotent monkeypatch of
    ``wikipedia.wikipedia._wiki_request`` — the single module-global function
    every library network call (``search``, ``summary``, ``page``,
    ``__load``, ``__continued_query``, ``html``) routes through. It
    reimplements that function verbatim, adding only ``timeout=`` to the
    ``requests.get``. A timed-out call now raises ``requests.Timeout``, which
    the engine's existing ``_summary_with_retry`` tenacity guard (3 attempts)
    retries; on final failure the per-title ``except`` in ``_get_previews``
    ``continue``s past the failed title instead of hanging forever.

    Args:
        timeout: ``(connect_seconds, read_seconds)`` passed to
            ``requests.get``. Defaults to ``(10, 30)`` — generous enough for
            a slow proxy, short enough that a dead connection is abandoned
            within one retry window instead of stalling the whole research.
    """
    try:
        from wikipedia import wikipedia as _wp_mod
    except ImportError:
        # No wikipedia library installed — nothing to patch.
        return

    if getattr(_wp_mod._wiki_request, "_ldr_timeout_patched", False):
        return

    # Snapshot the originals at patch time. The library reads these as module
    # globals on each call, but the patched function must not re-resolve them
    # through the (now-replaced) module attribute.
    _orig_request = _wp_mod._wiki_request
    _requests = _wp_mod.requests
    _datetime = _wp_mod.datetime
    _time = _wp_mod.time

    def _patched_wiki_request(params):  # type: ignore[no-untyped-def]
        # Verbatim reimplementation of wikipedia.wikipedia._wiki_request,
        # with timeout= added to requests.get. See upstream lines 712-742.
        global_rate_limit = _wp_mod.RATE_LIMIT
        rate_limit_last_call = _wp_mod.RATE_LIMIT_LAST_CALL
        rate_limit_min_wait = _wp_mod.RATE_LIMIT_MIN_WAIT

        params["format"] = "json"
        if "action" not in params:
            params["action"] = "query"

        headers = {"User-Agent": _wp_mod.USER_AGENT}

        if (
            global_rate_limit
            and rate_limit_last_call
            and rate_limit_last_call + rate_limit_min_wait > _datetime.now()
        ):
            wait_time = (
                rate_limit_last_call + rate_limit_min_wait
            ) - _datetime.now()
            _time.sleep(int(wait_time.total_seconds()))

        r = _requests.get(
            _wp_mod.API_URL,
            params=params,
            headers=headers,
            timeout=timeout,
        )

        if global_rate_limit:
            _wp_mod.RATE_LIMIT_LAST_CALL = _datetime.now()

        return r.json()

    _patched_wiki_request._ldr_timeout_patched = True  # type: ignore[attr-defined]
    _patched_wiki_request._ldr_orig = _orig_request  # type: ignore[attr-defined]
    _wp_mod._wiki_request = _patched_wiki_request
    logger.info(
        "Wikipedia API calls bounded to timeout=({}s connect, {}s read) to "
        "prevent indefinite stalls on a flaky proxy.",
        timeout[0],
        timeout[1],
    )


# ---------------------------------------------------------------------------
# SSL / TLS certificate fallback
# ---------------------------------------------------------------------------


def _fetch_intermediate_ca_bundle(url: str) -> Optional[str]:
    """Best-effort: fetch the server's missing intermediate CA via AIA.

    Some servers (e.g. certain ``.edu.tw`` hosts) send only their leaf
    certificate and omit the intermediate CA, so verification fails with
    "unable to get local issuer certificate". RFC 5280 Authority Information
    Access (AIA) often points to the intermediate ("CA Issuers" URI).

    This retrieves the leaf cert's AIA "CA Issuers" URL, downloads the
    intermediate, and writes a temporary PEM bundle = certifi + intermediate.
    Returns the bundle path on success, ``None`` otherwise.

    Implemented with the standard library only (``ssl``, ``socket``,
    ``http.client``) to avoid adding a dependency. On any failure returns
    ``None`` — callers fall back to the ``allow_insecure_tls`` toggle.
    """
    try:
        import tempfile
        import ssl
        import http.client
        from urllib.parse import urlparse as _urlparse

        from certifi import where as certifi_where
    except Exception:
        return None

    parsed = _urlparse(url)
    host = parsed.hostname
    if not host:
        return None
    port = parsed.port or 443

    # Connect and fetch the server's DER-encoded cert chain (leaf only, per
    # OpenSSL's get_unverified_chain — but we only need the leaf's AIA).
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                dercert = ssock.getpeercert(binary_form=True)
    except Exception:
        return None
    if not dercert:
        return None

    # Parse the leaf cert to extract AIA CA Issuers URL using ssl.DER_cert_to_PEM_cert
    # + a minimal ASN.1 walk is heavy; instead use the stdlib ssl module's
    # parsed form (getpeercert returns a dict when not binary — but that needs
    # verification). Fall back to cryptography if available; else bail.
    aia_url = _extract_aia_ca_issuers_url(dercert)
    if not aia_url:
        return None

    # Download the intermediate. AIA CA Issuers is commonly DER-encoded.
    try:
        aia = _urlparse(aia_url)
        conn = http.client.HTTPSConnection(aia.hostname, aia.port or 443, timeout=10)
        conn.request("GET", aia.path or "/")
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
    except Exception:
        return None
    if not body:
        return None

    pem_intermediate = _maybe_der_to_pem(body)
    if not pem_intermediate:
        return None

    try:
        with open(certifi_where(), "r", encoding="utf-8") as f:
            ca_bundle = f.read()
    except Exception:
        return None

    try:
        fd, path = tempfile.mkstemp(prefix="ldr_ca_bundle_", suffix=".pem")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(ca_bundle)
            f.write(pem_intermediate)
        return path
    except Exception:
        return None


def _extract_aia_ca_issuers_url(dercert: bytes) -> Optional[str]:
    """Extract the AIA "CA Issuers" URI from a DER-encoded leaf cert.

    Prefers ``cryptography`` (a transitive dependency of ``requests``/``urllib3``
    in most installs) for robust parsing. Falls back to ``None`` if unavailable
    or the extension is absent — callers then proceed to the insecure toggle.
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
    except Exception:
        return None
    try:
        cert = x509.load_der_x509_certificate(dercert)
    except Exception:
        return None
    try:
        ext = cert.extensions.get_extension_for_class(
            x509.AuthorityInformationAccess
        )
    except x509.ExtensionNotFound:
        return None
    for desc in ext.value:
        if desc.access_method == x509.AuthorityInformationAccessOID.CA_ISSUERS:
            val = desc.access_location.value
            if isinstance(val, str) and val.startswith("http"):
                return val
    return None


def _maybe_der_to_pem(body: bytes) -> Optional[str]:
    """Convert a downloaded CA cert (DER or already-PEM) to PEM text."""
    import base64

    # Already PEM?
    if body.lstrip().startswith(b"-----BEGIN"):
        return body.decode("ascii", errors="replace")
    # Assume DER — wrap to PEM.
    try:
        b64 = base64.b64encode(body).decode("ascii")
        lines = [b64[i : i + 64] for i in range(0, len(b64), 64)]
        return "-----BEGIN CERTIFICATE-----\n" + "\n".join(lines) + "\n-----END CERTIFICATE-----\n"
    except Exception:
        return None


def fetch_with_cert_fallback(session, url: str, **kwargs):
    """Run ``session.get`` with a TLS certificate fallback chain.

    Stage 1: normal request (verify=True, the default).
    Stage 2: on :class:`requests.exceptions.SSLError`, try fetching the missing
        intermediate CA via AIA and retry with a combined trust bundle.
    Stage 3: unconditional one-off retry with ``verify=False``. This applies
        ONLY to this single ``session.get`` call — ``session.verify`` is never
        mutated, so the next request to any URL still defaults to verify=True.
        ``get_allow_insecure_tls`` no longer gates this stage (the toggle is
        retained only for backward compatibility).

    Non-SSL exceptions are never caught here — they propagate to the caller's
    existing error handling.

    SSRF validation is unaffected: ``SafeSession.send`` validates the host on
    every request regardless of ``verify``.
    """
    import requests

    # Stage 1: Normal request with certificate verification
    try:
        response = session.get(url, **kwargs)
        record_tls_fallback(url, "stage1_normal", True)
        return response
    except requests.exceptions.SSLError as e:
        logger.debug(f"SSL verification failed for {url}; attempting AIA intermediate-CA fetch")
        record_tls_fallback(url, "stage1_normal", False, str(e))

    # Stage 2: AIA intermediate-CA fetch
    bundle = _fetch_intermediate_ca_bundle(url)
    if bundle:
        try:
            response = session.get(url, verify=bundle, **kwargs)
            record_tls_fallback(url, "stage2_aia", True)
            return response
        except requests.exceptions.SSLError as e:
            logger.debug(f"AIA bundle retry still failed SSL for {url}")
            record_tls_fallback(url, "stage2_aia", False, str(e))
        finally:
            try:
                os.unlink(bundle)
            except OSError:
                pass

    # Stage 3: per-request insecure fallback.
    # verify=False applies ONLY to this one retry; the next request to any URL
    # still defaults to verify=True. We do not persist any "insecure" state.
    # SSRF protection is unaffected: SafeSession.send validates the host on
    # every request regardless of verify.
    logger.warning(
        "TLS verification skipped for {} as a one-off retry: server certificate "
        "chain could not be completed (AIA intermediate-CA fetch also failed). "
        "verify=True is restored for subsequent requests.",
        url,
    )

    try:
        response = session.get(url, verify=False, **kwargs)
        record_tls_fallback(url, "stage3_insecure", True)
        return response
    except Exception as e:
        record_tls_fallback(url, "stage3_insecure", False, str(e))
        raise
