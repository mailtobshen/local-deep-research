"""Pre-research VPN proxy reachability check."""
from __future__ import annotations

import socket
import urllib.request
from urllib.parse import urlparse


class VPNCheckError(Exception):
    """Raised when VPN proxy is unreachable or cannot reach external network."""


def _parse_proxy_url(proxy_url: str) -> tuple[str, int]:
    """Parse http://host:port or socks5h://host:port → (host, port).

    Raises VPNCheckError if hostname or port is missing.
    """
    p = urlparse(proxy_url)
    if not p.hostname or not p.port:
        raise VPNCheckError(f"Invalid proxy URL: {proxy_url!r}")
    return p.hostname, p.port


def check_vpn_proxy(
    proxy_url: str,
    *,
    external_probe_url: str = "https://www.google.com/generate_204",
    timeout: float = 3.0,
) -> None:
    """Two-step reachability check. Raises VPNCheckError on failure.

    Step 1: TCP connect to (host, port) — proves proxy process is up.
    Step 2: HTTP HEAD via proxy to external_probe_url — proves proxy can
            transit to the open internet.

    Both steps must succeed. timeout applies per step (total ≤ 6s).

    NOTE: Step 2 is added in Task 3; this task adds step 1 only.
    """
    host, port = _parse_proxy_url(proxy_url)

    # Step 1: proxy port reachable
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except (socket.timeout, OSError) as e:
        raise VPNCheckError(
            f"VPN proxy port unreachable: {host}:{port} ({e})"
        ) from e