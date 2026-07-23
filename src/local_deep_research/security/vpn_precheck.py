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