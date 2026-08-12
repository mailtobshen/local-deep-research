# Canonical Attach — Treat Same-Origin URLs as Hits in the Deferred Fill

**Date:** 2026-08-12
**Status:** Approved design, pending implementation plan
**Supersedes / extends:** The "Known gap — URL normalization" section of `docs/superpowers/plans/2026-08-07-deferred-fill-attach-asymmetry.md`

## Problem

The deferred image-fill attach loop (`research_service.py:760-822`) judges
record matches with strict string equality (`sr_url != url`,
`rec_url != url`). A cited URL that differs from the stored record only by a
content-irrelevant transform is silently dropped as `ATTACH_MISS`, even though
the fetched images belong on that page.

Live evidence — research `c325e2a0-a761-4c54-9c49-f229a195569f`
(2026-08-12, detailed mode): **22 of 25 `ATTACH_MISS` events are this class**,
each paired with an `ATTACH_NEAR_MATCH via=trailing_slash`. The code already
*knows* the URLs are same-origin (the NEAR_MATCH probe computes exactly that),
yet the probe's docstring says *"Observes only — does NOT set attached or
change filled."* So the images are discarded after a successful fetch.

Two raw-difference shapes appear in production, both canonical-equal:

- **Mode A — trailing slash (17 cases):** cite `.../8d7n-shanghai-disneyland`
  vs record `.../8d7n-shanghai-disneyland/`.
- **Mode B — Steam `?id` separator (5 cases):** cite
  `steamcommunity.com/sharedfiles/filedetails?id=3506925216` vs record
  `.../filedetails/?id=3506925216` (slash before the query separator).

The remaining 3 of 25 misses are genuine noise (e.g. `baidu.com/?a=`, PHP
query-string pages never indexed) and are out of scope.

## Goal

Same-origin URLs attach. When a cited URL has no exact-match record but a
record canonicalizes equal to it, the deferred fill writes `html_content`
onto that record and counts the citation as filled — instead of emitting
`ATTACH_MISS` + an observe-only `ATTACH_NEAR_MATCH`.

## Non-goals

- Do **not** change `_canonicalize_url` (`images/relevance.py:218`). It is
  already content-safe: scheme→https, host lowercase, drop `www.`,
  `path.rstrip("/")`, drop fragment, **query preserved verbatim** (so
  `?id=1` never canonicalizes equal to `?id=2`), fail-closed on parse error.
  Only consume it.
- Do **not** change `_classify_url_diff` (`research_service.py:489`). Its
  `via` vocabulary (`trailing_slash` / `www` / `scheme` / `fragment` /
  `combined` / `other`) is reused as-is.
- Do **not** broaden matching beyond canonical equality (no eTLD+1, no
  fuzzy host). The 22 production cases are all canonical-equal; nothing
  wider is needed.
- Do **not** change the exact-match path, the `DEFERRED_FILLED` event, or
  the read side (`build_citation_index`).

## Design

### Matching semantics (the core change)

For each cited URL, in the attach block currently at
`research_service.py:760-822`:

1. **Exact pass (unchanged):** scan `findings[].search_results[]` then
   `all_links_of_system`; for every record whose `url`/`link` string equals
   the cited URL, write `html_content`, set `attached = True`. Identical to
   today.
2. **Canonical pass (new), only if `not attached`:** scan both surfaces in
   the same order; find the **first** record with
   `_canonicalize_url(cand) == _canonicalize_url(url)` and `cand != url`.
   On hit: write `html_content` onto that one record, set `attached = True`,
   remember `canonical_hit = cand`.
3. **Counting (unchanged invariant, made explicit):** `filled += 1` iff
   `attached`. A citation contributes **at most 1** to `filled` regardless
   of how many records received the payload or how many passes ran. The
   `attached` flag gates the canonical pass, so exact and canonical never
   double-count.

### Probe: new `ATTACH_CANONICAL`

Emitted iff the canonical pass was what set `attached`. Reuses the existing
field vocabulary; no new names:

```
[IMG-TRACE] ATTACH_CANONICAL research=<id> cite_num=<n> ref_url=<cited URL> record_url=<matched record URL> via=<classify output>
```

- `via = _classify_url_diff(url, canonical_hit)` — reused verbatim.
- Followed by the normal `DEFERRED_FILLED` line (unchanged).

### Probe behavior after the change

| attach outcome | log |
|---|---|
| exact match | `DEFERRED_FILLED` (silent, no extra event) |
| canonical match | `ATTACH_CANONICAL` + `DEFERRED_FILLED` |
| no match, canonical near-neighbor exists in records | `ATTACH_MISS` + `ATTACH_NEAR_MATCH` (observe-only, semantics unchanged — now near-impossible for trailing-slash; only exotic drifts) |
| no match, no near-neighbor (noise citations) | `ATTACH_MISS` |

**Expected post-fix signal on the next detailed-mode run:** `ATTACH_MISS`
drops from ~25 toward ~3 (the genuine-noise citations); a new
`ATTACH_CANONICAL` count appears at roughly the current NEAR_MATCH level
(~22). `filled=N/M` rises accordingly.

## Testing

All new tests append to `tests/web/test_deferred_image_fill.py`, reusing the
file's existing conventions: the `_extracted_image` helper, patching
`local_deep_research.research_library.downloaders.extraction.pipeline.fetch_content_with_images`,
`settings_snapshot={"report.enable_images": True}`, and the `loguru_caplog`
fixture (`tests/conftest.py:604`) joined exactly as
`test_attach_miss_event_emitted_when_no_record_matches` does.

1. **`test_canonical_attach_on_trailing_slash`** — cite `.../disneyland`,
   record `.../disneyland/`. Assert `filled == 1`, the record gained
   `html_content`, and the log carries `ATTACH_CANONICAL ... via=trailing_slash`.
   *Fails against current `main` (today it yields `filled == 0`)* — reproduces
   Mode A.
2. **`test_canonical_attach_steam_question_mark`** — cite
   `.../filedetails?id=3506925216`, record `.../filedetails/?id=3506925216`.
   Same assertions, `via=trailing_slash`. Covers Mode B.
3. **`test_exact_match_takes_precedence_over_canonical`** — records contain
   BOTH an exact match and a canonical near-neighbor. Assert `filled == 1`,
   the exact record was the one written, and **no** `ATTACH_CANONICAL` event
   fires. Pins "exact precedence".
4. **`test_canonical_never_merges_distinct_query`** — cite `?id=1`, record
   `?id=2`. Assert `filled == 0`, `ATTACH_MISS` fires, **no**
   `ATTACH_CANONICAL`. Pins "query preserved → distinct pages never merge".
5. **`test_attach_canonical_carries_five_key_fields`** — assert the
   `ATTACH_CANONICAL` line contains both `cite_num` and `ref_url`, aligning
   with the five-key IMG-TRACE schema audit pattern in
   `tests/images/test_img_trace_audit_events.py`.

**Regression:** existing `test_attach_miss_event_emitted_when_no_record_matches`
must still pass — under the new semantics it still represents the "genuine
no-match" path (its fixture uses a URL with no record at all, which is
neither exact nor canonical). If the fixture's URL happens to canonicalize
to something present, adjust the fixture to a truly-unmatched URL (the test
intent is unchanged). Full gate:
`LDR_BOOTSTRAP_ALLOW_UNENCRYPTED=true .venv/bin/python -m pytest tests/web/ tests/images/ -q`
must be green.

## Architecture fit

- The canonical pass reuses the NEAR_MATCH block's existing two-surface scan
  structure (it already finds the first canonical-equal candidate). The
  change is to *act on* the found candidate rather than only log it.
- One new probe event, consistent with the IMG-TRACE observability pattern
  (`[[img-trace-five-key-schema]]`, `[[img-trace-observability]]`): every
  per-image/per-citation event stays self-describing so a single grep
  reconstructs the chain.
- No new module, no new abstraction, no config knob. Matching rule is
  hardcoded canonical equality, matching the production evidence.

## Files

| File | Change |
|---|---|
| `src/local_deep_research/web/services/research_service.py` | Attach block (~760-822): add canonical pass, emit `ATTACH_CANONICAL`. Imports of `_canonicalize_url` / `_classify_url_diff` already present. |
| `tests/web/test_deferred_image_fill.py` | Add the 5 tests above. |

## Success criteria

- 5 new tests pass; `tests/web/ tests/images/` full suite green.
- On the next live detailed-mode run (captured before a container restart,
  per `[[deferred-fill-attach-fix-verified]]`'s log-window caveat):
  `ATTACH_CANONICAL` appears, `ATTACH_MISS` drops toward the genuine-noise
  floor (~3), `filled=N/M` rises, `BANK_EMPTY` stays 0, `END status=ok`.
