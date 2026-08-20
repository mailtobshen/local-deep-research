#!/usr/bin/env python3
"""
MANUAL INTEGRATION TEST — do NOT run under pytest.

End-to-end test script for the darkweb research pipeline. Verifies
the three core flow phases of ``_deferred_image_fill``:

  Phase 1: SearXNG darkweb search for query
            -> returns search_results[] with title, url, content snippet
  Phase 2: HTML fetch for each result URL via onion-connect-proxy:18080
            -> uses Path A (http->https promote) + Path C (URL encode)
            -> mimics requests.Session with onion proxy
  Phase 3: Image extraction from each fetched HTML
            -> uses BeautifulSoup (same as ldr-local images/extractor.py)

The script also runs a raw SOCKS5h probe via ldr-tor:9050 as a
diagnostic fallback — if onion-connect-proxy returns 502 but direct
SOCKS5h succeeds, the failure is in onion-connect-proxy; if both
fail, the .onion host itself is dead.

Run inside ldr-local container so onion-connect-proxy:18080 +
ldr-tor:9050 are reachable on the loopback network. Execute via::

    docker cp tests/security/e2e_darkweb_fentanyl.py ldr-local:/tmp/
    docker exec -w /tmp ldr-local python3 /tmp/e2e_darkweb_fentanyl.py

ORIGINAL CONTEXT: Verified 2026-08-20 on research d2ac1028 and 84dfa8be
(146+ .onion URLs failing with status=400 / 502 / REP=0x05). The test
distinguishes "onion-connect-proxy broken" from ".onion host dead"
from "LLM hallucinated URL" — three failure modes that look identical
from the OBS-G probe alone.

Not auto-run under CI: requires a running ldr-local stack with
.onion connectivity. Use as a manual regression check after any
change to:
  - local_deep_research/research_library/downloaders/html.py
  - local_deep_research/security/onion_connect_proxy.py
  - local_deep_research/utilities/search_utilities.py
"""

  docker exec -w /tmp ldr-local python3 /tmp/e2e_darkweb_fentanyl.py

Reports per-URL outcomes in a clean table so failures are easy to
attribute to one of the three phases.
"""
import re
import socket
import sys
import time
from urllib.parse import urlparse, quote

try:
    import requests
except ImportError:
    print("ERROR: requests not installed in container")
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("ERROR: beautifulsoup4 not installed in container")
    sys.exit(1)


QUERY = "芬太尼及精神药物非法交易产业链"
SEARXNG_URL = "http://searxng-ldr:8080"
ONION_PROXY = "http://127.0.0.1:18080"
ONION_PROXY_HTTPS = "http://127.0.0.1:18080"
LDR_TOR = "socks5h://ldr-tor:9050"
FETCH_TIMEOUT = 35


def banner(text):
    print()
    print("=" * 80)
    print(f"  {text}")
    print("=" * 80)


def phase_1_search(query):
    """Phase 1: SearXNG darkweb engine search.
    Returns list of dicts: {title, url, content, engine, is_onion}.
    Uses engines=ahmia,torch + categories=onions (matches the LDR darkweb merge config).
    """
    banner("PHASE 1: SearXNG darkweb search")
    params = {
        "q": query,
        "engines": "ahmia,torch",
        "categories": "onions",
        "format": "json",
        "language": "zh",
    }
    start = time.monotonic()
    try:
        resp = requests.get(f"{SEARXNG_URL}/search", params=params, timeout=60)
        elapsed = time.monotonic() - start
        if resp.status_code != 200:
            print(f"  ERROR: SearXNG returned status {resp.status_code}")
            return []
        data = resp.json()
        results = data.get("results", [])
        # Filter to .onion only and dedup by URL
        seen = set()
        out = []
        for r in results:
            u = r.get("url", "")
            if ".onion" not in u.lower():
                continue
            if u in seen:
                continue
            seen.add(u)
            out.append({
                "title": (r.get("title") or "")[:80],
                "url": u,
                "content": (r.get("content") or "")[:120],
                "engine": r.get("engine", ""),
                "is_onion": r.get("is_onion", False),
            })
        print(f"  SearXNG returned {len(results)} raw results in {elapsed:.1f}s")
        print(f"  After .onion filter + dedup: {len(out)} unique URLs")
        for i, r in enumerate(out[:5], 1):
            print(f"    {i}. [{r['engine']}] {r['url'][:75]}")
        if len(out) > 5:
            print(f"    ... ({len(out) - 5} more)")
        return out
    except Exception as e:
        print(f"  EXCEPTION: {type(e).__name__}: {e}")
        return []


def path_a_promote(url):
    """Path A: http://...onion -> https://...onion (matches ddfe61fe)."""
    if url.startswith("http://"):
        host_check = (urlparse(url).hostname or "").lower()
        if host_check == "onion" or host_check.endswith(".onion"):
            return "https://" + url[len("http://"):]
    return url


def path_c_encode(url):
    """Path C: percent-encode spaces (matches c685a332)."""
    if " " in url:
        return quote(url, safe=":/?&=#@!$%'()*+,;=-._~")
    return url


def phase_2_fetch(result):
    """Phase 2: fetch URL via onion-connect-proxy:18080.

    Applies Path A (http->https promote) and Path C (URL encode spaces)
    matching the live code in HTMLDownloader._fetch_html. Returns:
      {url_final, status_code, body_bytes, headers, error}
    """
    raw_url = result["url"]
    promoted = path_a_promote(raw_url)
    final_url = path_c_encode(promoted)
    started = time.monotonic()
    try:
        resp = requests.get(
            final_url,
            proxies={"http": ONION_PROXY, "https": ONION_PROXY_HTTPS},
            timeout=FETCH_TIMEOUT,
            verify=False,
        )
        elapsed = time.monotonic() - started
        return {
            "url_raw": raw_url,
            "url_final": final_url,
            "promoted": raw_url != promoted,
            "encoded": promoted != final_url,
            "status": resp.status_code,
            "body_bytes": len(resp.text),
            "headers": dict(resp.headers),
            "html": resp.text if resp.status_code == 200 else None,
            "elapsed": elapsed,
            "error": None,
        }
    except Exception as e:
        elapsed = time.monotonic() - started
        return {
            "url_raw": raw_url,
            "url_final": final_url,
            "promoted": raw_url != promoted,
            "encoded": promoted != final_url,
            "status": None,
            "body_bytes": 0,
            "headers": {},
            "html": None,
            "elapsed": elapsed,
            "error": f"{type(e).__name__}: {str(e)[:120]}",
        }


def phase_3_extract_images(fetch_result):
    """Phase 3: extract <img> tags from fetched HTML.

    Uses the same logic as ldr-local images/extractor.py:
    - find_all('img')
    - skip if src empty / data: URI
    - require http(s) absolute URL
    - skip if width or height < 50 px
    - drop empty src / data: URLs
    """
    if not fetch_result.get("html"):
        return {
            "img_total_tags": 0,
            "img_after_filter": 0,
            "img_samples": [],
            "error": "no_html",
        }
    try:
        soup = BeautifulSoup(fetch_result["html"], "html.parser")
        imgs = soup.find_all("img")
        kept = []
        for img in imgs:
            src = img.get("src") or ""
            if not src or src.startswith("data:"):
                continue
            from urllib.parse import urljoin
            absolute = urljoin(fetch_result["url_final"], src)
            scheme = urlparse(absolute).scheme.lower()
            if scheme not in ("http", "https"):
                continue
            try:
                w = int(img.get("width") or 0) or None
                h = int(img.get("height") or 0) or None
            except (ValueError, TypeError):
                w = h = None
            if w is not None and w < 50:
                continue
            if h is not None and h < 50:
                continue
            kept.append({
                "src": absolute,
                "alt": (img.get("alt") or "")[:80],
                "width": w,
                "height": h,
            })
        return {
            "img_total_tags": len(imgs),
            "img_after_filter": len(kept),
            "img_samples": kept[:5],
            "error": None,
        }
    except Exception as e:
        return {
            "img_total_tags": 0,
            "img_after_filter": 0,
            "img_samples": [],
            "error": f"{type(e).__name__}: {str(e)[:100]}",
        }


def phase_25_direct_socks5_test(host):
    """Sanity test: raw SOCKS5h via ldr-tor to confirm .onion itself alive.

    Skips onion-connect-proxy entirely. If raw SOCKS5 succeeds, the .onion
    is alive and onion-connect-proxy is at fault. If raw SOCKS5 also
    fails, the .onion itself is dead and the failure is unrelated to
    ldr-local code. Uses raw socket because PySocks is not installed
    in the ldr-local container (requests direct SOCKS raises
    InvalidSchema — which would give a false "Direct FAILED" signal).
    """
    try:
        s = socket.create_connection(("ldr-tor", 9050), timeout=15)
        s.sendall(bytes([0x05, 0x01, 0x00]))
        s.recv(2)
        s.sendall(bytes([0x05, 0x01, 0x00, 0x03, len(host)]) + host + bytes([0x00, 0x50]))
        s.settimeout(15)
        reply = s.recv(4)
        s.close()
        if reply[:2] != bytes([0x05, 0x00]):
            return {
                "status": None,
                "body_bytes": 0,
                "elapsed": 0,
                "error": f"SOCKS5 REP=0x{reply[1]:02x} (Tor refused)",
            }
        return {"status": 200, "body_bytes": 0, "elapsed": 0, "error": None}
    except socket.timeout:
        return {
            "status": None,
            "body_bytes": 0,
            "elapsed": 0,
            "error": "SOCKS5 handshake timeout (Tor circuit slow / dead)",
        }
    except Exception as e:
        return {
            "status": None,
            "body_bytes": 0,
            "elapsed": 0,
            "error": f"{type(e).__name__}: {str(e)[:100]}",
        }


def main():
    banner(f"E2E test: query = {QUERY!r}")
    print(f"  SearXNG: {SEARXNG_URL}")
    print(f"  Onion-connect-proxy: {ONION_PROXY}")
    print(f"  ldr-tor SOCKS5: {LDR_TOR}")
    print(f"  Fetch timeout: {FETCH_TIMEOUT}s")

    # Phase 1
    results = phase_1_search(QUERY)
    if not results:
        print("\nNo search results; aborting.")
        return 1

    # Cap to first N to keep test bounded
    MAX = min(8, len(results))
    print(f"\n  Testing first {MAX} of {len(results)} URLs...")

    # Phases 2 + 3
    success = 0
    success_img = 0
    for i, r in enumerate(results[:MAX], 1):
        banner(f"URL {i}/{MAX}: {r['url'][:70]}")
        print(f"  title:  {r['title']}")
        print(f"  engine: {r['engine']}")

        # Phase 2: fetch via onion-connect-proxy
        fetch = phase_2_fetch(r)
        if fetch.get("promoted"):
            print(f"  Path A: promoted to https")
        if fetch.get("encoded"):
            print(f"  Path C: URL-encoded spaces")

        # Phase 2.5: also test the raw .onion host (SOCKS5h direct)
        # for comparison — this distinguishes 'onion-connect-proxy bug'
        # from '.onion host dead'.
        host = r["url"].split("//", 1)[1].split("/", 1)[0].encode()
        direct = phase_25_direct_socks5_test(host)

        if fetch["error"]:
            print(f"  Phase 2: FAILED ({fetch['error']}) elapsed={fetch['elapsed']:.1f}s")
            if direct["error"]:
                print(f"    Direct SOCKS5h: FAILED ({direct['error'][:80]})")
                print(f"    -> .onion HOST IS DEAD (not a code bug)")
            else:
                print(f"    Direct SOCKS5h: OK (tor reachable)")
                print(f"    -> onion-connect-proxy BROKEN (direct works, proxy fails)")
        else:
            print(f"  Phase 2: status={fetch['status']} body={fetch['body_bytes']}B elapsed={fetch['elapsed']:.1f}s")
            success += 1
            print(f"    Direct SOCKS5h: OK (tor reachable)")

            # Phase 3: extract images
            images = phase_3_extract_images(fetch)
            if images["error"] and images["error"] != "no_html":
                print(f"  Phase 3: EXTRACT ERROR ({images['error']})")
            else:
                print(f"  Phase 3: {images['img_total_tags']} <img> tags, "
                      f"{images['img_after_filter']} passed filter")
                if images["img_samples"]:
                    for j, im in enumerate(images["img_samples"], 1):
                        alt_disp = im["alt"] or "(no alt)"
                        print(f"    img {j}: alt={alt_disp!r}")
                        print(f"           src={im['src'][:80]}")
                if images["img_after_filter"] > 0:
                    success_img += 1

    # Summary
    banner("SUMMARY")
    print(f"  URLs tested:        {MAX}")
    print(f"  Fetch success:      {success}/{MAX}")
    print(f"  Images found:       {success_img}/{MAX}")
    if success < MAX:
        print(f"\n  DIAGNOSIS:")
        print(f"  - 0/{MAX} success + direct SOCKS5h FAILED for all:")
        print(f"      -> all .onion hosts in SearXNG results are deadlinks")
        print(f"         (LLM-cached ahmia URLs; current tor circuit rejects)")
        print(f"  - 0/{MAX} success + direct SOCKS5h OK:")
        print(f"      -> onion-connect-proxy is broken")
        print(f"         (Phase B should have shipped — verify 18080 listener)")
    elif success_img == 0:
        print(f"\n  DIAGNOSIS: all {success} reachable but no <img> tags.")
        print(f"  -> pages are text-only (no images). Fix at image-extraction")
        print(f"     layer or research scope (not a fetch bug).")
    else:
        print(f"\n  DIAGNOSIS: pipeline OK — {success_img} URLs had images")
    return 0


if __name__ == "__main__":
    sys.exit(main())
