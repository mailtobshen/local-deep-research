# 2026-08-15 ldr-local image rebuild history

7 sequential `docker build --no-cache` attempts on 2026-08-15 to bake
Chromium binaries into the ldr-local:i18n image so that the darkweb
Phase-2 Task 4 (Playwright + SOCKS5 → ldr-tor) would survive
`--force-recreate` without needing to re-run `playwright install` in
the container.

## Files

| Log | Outcome | Commit that addressed it |
|---|---|---|
| `ldr-build.log` | First attempt; chromium binary landed in builder-base only — did not propagate to final `ldr` stage. Image lacked chromium. | (the fix that motivated build 2) |
| `ldr-build2.log` | Added chromium install to final `ldr` stage, but the install ran in builder-base (which doesn't propagate). Same problem. | (the fix that motivated build 3) |
| `ldr-build3.log` | Moved install to the final `ldr` stage; chromium-1208 baked — but Python playwright 1.60.0 (from hot-mount site-packages) expected chromium-1223. | `c3cc31d1` |
| `ldr-build4.log` | Routed playwright downloads through `PLAYWRIGHT_DOWNLOAD_HOST=https://registry.npmmirror.com/-/binary/playwright` (CN mirror). 30× faster. | `4531ff1b` |
| `ldr-build5.log` | Switched to `pip install playwright && playwright install`. Got chromium-1234 (latest playwright); still didn't match hot-mount's 1.60.0. | `2ab82bd0` |
| `ldr-build6.log` | Pinned `playwright==1.60.0` to match hot-mount. Got chromium-1223, but `libnspr4.so` missing — chrome couldn't launch. | `e14d9c72` / `35b2d258` |
| `ldr-build7.log` | Added `--with-deps` so `playwright install` runs the apt-get install of Chromium's system libs (libnspr4, libnss3, etc.). Image now contains both chromium-1223 binaries AND their system deps. Verified end-to-end with Playwright + SOCKS5 .onion fetch (DuckDuckGo, 53.1s, 169 KB). | (final — image live since) |

## Final image

`ldr-local:i18n` tagged 2026-08-15 22:30 CST, 4.87 GB.

The container `ldr-local` (started 2026-08-15 22:33) has been running
with this image ever since. Phase-2 + Phase-3 work has been committed
on top of this image.

## What this archive is good for

If anyone in the future runs into the same trap (chromium binary
missing from the final image, or Playwright version mismatch between
image and hot-mount site-packages), read these logs in order. Each
attempt has its error message preserved verbatim, which makes it easy
to pattern-match against the next failure.

## Keep or remove?

The 1 MB total cost is negligible. Useful as a reference for the
next person who edits `Dockerfile` and forgets that the image
bake / hot-mount layers need to agree on `playwright`'s
browsers.json revision.

## Related commits (main branch)

`946664de` `1626181b` `a7475563` `c3cc31d1` `4531ff1b`
`2ab82bd0` `e14d9c72` `35b2d258`