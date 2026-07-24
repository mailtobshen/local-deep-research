"""Pre-research VPN proxy reachability check."""
from __future__ import annotations

import socket
import urllib.request
from urllib.parse import urlparse


class VPNCheckError(Exception):
    """Raised when VPN proxy is unreachable or cannot reach external network."""


def _vpn_error_to_info(error: Exception) -> dict:
    """Convert a VPNCheckError into i18n-friendly {key, args}.

    Returns a dict suitable for jsonify so the frontend can translate the
    message in the user's active language. Falls back to message_raw for
    unrecognized error formats.

    Recognized message formats (must match the `raise` statements below):
      - "VPN proxy port unreachable: HOST:PORT (REASON)"
      - "VPN proxy cannot reach external network: REASON"
      - "VPN proxy returned HTTP {status} from {url}"
      - "Invalid proxy URL: 'URL'"
    """
    msg = str(error)
    if msg.startswith("VPN proxy port unreachable:"):
        try:
            tail = msg.split(":", 1)[1].strip()
            host_port, rest = tail.split(" ", 1)
            reason = rest.strip().strip("()")
            return {
                "message_key": "vpn_check.port_unreachable",
                "message_args": {"host_port": host_port, "reason": reason},
            }
        except (ValueError, IndexError):
            pass
    if msg.startswith("VPN proxy cannot reach external network:"):
        reason = msg.split(":", 1)[1].strip()
        return {
            "message_key": "vpn_check.cannot_reach_external",
            "message_args": {"reason": reason},
        }
    if msg.startswith("VPN proxy returned HTTP"):
        # e.g. "VPN proxy returned HTTP 500 from https://www.google.com/generate_204"
        try:
            status = msg.split("HTTP ", 1)[1].split(" ", 1)[0]
            url = msg.split(" from ", 1)[1]
            return {
                "message_key": "vpn_check.bad_status",
                "message_args": {"status": status, "url": url},
            }
        except (ValueError, IndexError):
            pass
    if msg.startswith("Invalid proxy URL:"):
        url = msg.split(":", 1)[1].strip().strip("'\"")
        return {
            "message_key": "vpn_check.invalid_proxy_url",
            "message_args": {"url": url},
        }
    return {"message_key": None, "message_raw": msg}


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

    Uses stdlib urllib (NOT safe_get) because safe_get's SSRF validator
    would block private-IP proxy hosts like 172.25.128.1.
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

    # Step 2: proxy can reach external network
    proxy_handler = urllib.request.ProxyHandler({
        "http": proxy_url,
        "https": proxy_url,
    })
    opener = urllib.request.build_opener(proxy_handler)
    try:
        req = urllib.request.Request(external_probe_url, method="HEAD")
        resp = opener.open(req, timeout=timeout)
        if resp.status not in (200, 204):
            raise VPNCheckError(
                f"VPN proxy returned HTTP {resp.status} from "
                f"{external_probe_url}"
            )
    except VPNCheckError:
        raise
    except Exception as e:
        raise VPNCheckError(
            f"VPN proxy cannot reach external network: {e}"
        ) from e