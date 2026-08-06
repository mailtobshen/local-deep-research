# 2026-08-07 IMG-TRACE audit fix — close 5 unanswered gaps

## Background

After auditing Aug 6 run `219e7be7-…` (the "上海旅游景点" re-run)
against the dual-condition standard (source-code reasoning AND
log-event evidence must both confirm an assertion), five gaps in
the IMG-TRACE pipeline remained:

| Gap | Question | Source code evidence | Aug 6 log evidence |
|-----|----------|----------------------|-------------------|
| G1 | Where did `url_to_html["Jin_Mao_Tower"]` and `url_to_html["Oriental_Pearl_Tower"]` come from? | All write paths either unused (`_ensure_images_for_results` no caller) or disabled (`include_full_content=False`) | 0 hits for `attach_html_content`, `[FETCH]`, `_register_in_collector` |
| G2 | Which `cite_num`s does each `sec_idx` contain? | `section_to_nums[idx]` computed but never logged | only `len(section_to_nums)` shown in CITATION_INDEX |
| G3 | What URLs are in `url_to_html`? | dict built in `relevance.py:670-690` | only `len(url_to_html)` shown |
| G4 | Which alt went where, was kept/dropped, and why? | `keep`/`drop` decisions made in `postprocessing.py:355-405` | only top-line `CITATION_INDEX html_covered=N`, no per-(sec, cite, img) trail |
| G5 | Did the langgraph agent actually call `fetch_content`? | tool exists at `tools/fetch/__init__.py:93, 141` | 0 `[FETCH]` log lines in Aug 6 |

The Aug 6 run showed `html_covered=2`, 7 images adopted in the
"2.2 金茂大厦" section, and 0 hits for `fetch_content`/etc. — but
the dual-condition check could not pinpoint **which source-code
path** wrote those 2 html_content entries, because no log line
observed the write.

## Goal

Close all 5 gaps with **log-only additions** so that after the Aug 7
re-run, the entire (sec, cite, img, decision) matrix can be
reconstructed from `docker logs` alone. **Zero behavior change** to
the pipeline.

## Design — 8 new IMG-TRACE events

All events follow the existing `[IMG-TRACE] EVENT_NAME` loguru
naming convention and INFO level. Each emits one or more structured
fields that can be `grep`-parsed post-hoc.

### Event 1 — `SEC_CITE_INDEX` (closes G2)

**Where**: `src/local_deep_research/images/postprocessing.py` — insert
just before the existing `CITATION_INDEX` log block at line 198.

**Fields**:
```
[IMG-TRACE] SEC_CITE_INDEX research=<rid>
  sec=<idx> sec_phrase="<heading+entities, 80 chars>"
  cite_nums=[<comma-separated list>]
```

**Emits**: one line per `section_to_nums` entry with non-empty
`nums`. Skips secs with no `[[N]]` body tokens (which is most of
them in Aug 6: 175 of 190).

**Why INFO**: postmortem needs to know "sec=14 got cite=5 only"
without re-running `build_citation_index`.

### Event 2 — `URL_HTML_MAP` (closes G3)

**Where**: `src/local_deep_research/images/postprocessing.py` — insert
just before the existing `CITATION_INDEX` log at line 201.

**Fields**:
```
[IMG-TRACE] URL_HTML_MAP research=<rid>
  url=<url> html_len=<N> src={"findings"|"all_links"|"deferred_backfill"}
```

**Emits**: one line per `url_to_html` entry. `src` records which of
the two `build_citation_index` source paths populated it (line 671
"findings" vs line 686 "all_links"), plus `"deferred_backfill"` if
introduced by Event 9 in a future fix.

**Why INFO**: distinguishes "html came from langgraph stage" vs
"html came from DEFERRED stage". Aug 6 has only 2 entries with
unknown source — this event makes that source observable.

### Event 3 — `FETCH_CONTENT_TOOL_CALL` (closes G5)

**Where**: `src/local_deep_research/advanced_search_system/tools/fetch/__init__.py`
— insert just before `return f"[{cite_idx}] Title: {title}…"` at
line 114 (full mode) and just before `return f"[{cite_idx}] Title:…"`
at line 206 (summary mode).

**Fields**:
```
[IMG-TRACE] FETCH_CONTENT_TOOL_CALL research=<rid>
  url=<url> mode={"full"|"summary"}
  result_status={"success"|"failed"|"timeout"}
  html_len=<N>
```

**Why INFO**: definitive evidence whether LLM called `fetch_content`
during the run. Aug 6 had 0 calls — Aug 7 can verify whether
APPEND_PROMPT (or other interventions) cause LLM to call it.

### Event 4 — `ATTACH_HTML_CONTENT` (closes G1)

**Where**: `src/local_deep_research/advanced_search_system/strategies/langgraph_agent_strategy.py`
— insert just after `if self.collector.attach_html_content(url, dumps_images(images)):`
at line 965, in both the True and False branches.

**Fields**:
```
[IMG-TRACE] ATTACH_HTML_CONTENT research=<rid>
  url=<url> updated={True|False}
  prev_len=<N|0> new_len=<M|0>
```

**Why INFO**: Aug 6 had 0 hits for `attach_html_content` — this
event makes any future call to `attach_html_content` (the only
known writer of `r["html_content"]`) directly observable.

### Event 5 — `SEC_BINDING` (closes G4, per-alt trace)

**Where**: `src/local_deep_research/images/postprocessing.py` — insert
just before the existing `CITATION_CANDIDATES` log block at line 298.

**Fields**:
```
[IMG-TRACE] SEC_BINDING research=<rid>
  sec=<sidx> sec_phrase="<heading+entities, 80 chars>"
  cite_num=<num> ref_url=<url>
  cand_count=<N> kept_alts=<K>
  sample_alts=[<a, b, c>]
```

**Emits**: one line per (sidx, num, url) triple. `sample_alts`
truncated to first 3 for log volume control.

**Why INFO**: postmortem gets "sec=14 got 9 candidate imgs from
cite=5 (OPT), sample alts=[Map, Map, ...]" without re-running
`postprocessing`.

### Event 6 — `CANDIDATE_NO_ALT` (closes G4, alt-missing path)

**Where**: `src/local_deep_research/images/postprocessing.py:309`.
Currently a `logger.debug(...)` — change to `logger.info(...)`.

**Fields** (unchanged):
```
[IMG-TRACE] CANDIDATE_NO_ALT research=<rid>
  src_url=<url> img_url=<img.url>
```

**Why INFO**: empty-alt images are silently dropped in Aug 6 but
represent a real image failure path. Promote to INFO so postmortem
can count empty-alt losses per source.

### Event 7 — `CANDIDATE_SCORED_DETAIL` (closes G4, decision reason)

**Where**: `src/local_deep_research/images/postprocessing.py` — insert
just after the existing `CANDIDATE_SCORED` log block at line 327.

**Fields**:
```
[IMG-TRACE] CANDIDATE_SCORED_DETAIL research=<rid>
  sec=<sidx> cite_num=<num> ref_url=<url>
  img_alt="<alt[:120]>" img_url=<img_url>
  score=<score:.3f> decision={"keep"|"drop"}
  reason={"phrase_similarity"|"low_alt_or_url"}
```

**Emits**: one line per scored candidate, regardless of keep/drop.

**Why INFO**: postmortem gets the precise per-image score + decision.
Aug 6 had `Map` images at score ≈ 0.000 for OPT sections — these
events reveal **why** they dropped vs why the 7 JMT images were
kept.

### Event 8 — `PLACEMENT_DECISION` (closes G4, adoption trail)

**Where**: `src/local_deep_research/images/postprocessing.py` — insert
just after `binding.setdefault(url, []).append((num, sidx))` (or
right before the existing `binding` post-loop ends).

**Fields**:
```
[IMG-TRACE] PLACEMENT_DECISION research=<rid>
  sec=<sidx> img_url=<url>
  action={"attach"|"skip_duplicate"|"skip_filter"}
  reason=<phrase>
```

**Emits**: one line per `binding` decision.

**Why INFO**: final-stage provenance — postmortem reads "this img
ended up attached to sec=X via Y path".

## Files Changed

| File | Lines changed | Behavior change |
|---|---|---|
| `src/local_deep_research/images/postprocessing.py` | +60 (5 events + 1 INFO promotion) | 0 |
| `src/local_deep_research/advanced_search_system/tools/fetch/__init__.py` | +12 (2 events) | 0 |
| `src/local_deep_research/advanced_search_system/strategies/langgraph_agent_strategy.py` | +6 (1 event) | 0 |
| `docs/superpowers/plans/2026-08-07-img-trace-audit-fix.md` | new file | n/a |
| `tests/images/test_img_trace_audit_events.py` | new file, +60 | 0 |

**Total**: 5 files, ~140 lines new, 0 modified.

## Audit matrix (Aug 7 re-run post-mortem)

| Question | Answering event |
|----------|-----------------|
| Which URLs did each subsection fetch? | Event 1 (`SEC_CITE_INDEX`) |
| How long was each fetched URL's text? | Event 2 (`URL_HTML_MAP.html_len`) + Event 3 (`FETCH_CONTENT_TOOL_CALL.html_len`) |
| How many candidate images did each (sec, cite) pair see? | Event 5 (`SEC_BINDING.cand_count`) |
| What were the alt texts of candidates? | Event 5 (`sample_alts`) + Event 7 (`CANDIDATE_SCORED_DETAIL.img_alt`) |
| How many candidates had empty alt? | Event 6 (`CANDIDATE_NO_ALT`) |
| Why was each image kept/dropped? | Event 7 (`decision` + `reason` + `score`) |
| Which images were finally adopted? | Event 8 (`PLACEMENT_DECISION.action=attach`) |
| Where did `url_to_html` entries come from? | Event 2 (`URL_HTML_MAP.src=`) |
| Did LLM call `fetch_content`? | Event 3 count |

All 5 gaps (G1–G5) closed with single-event semantics.

## Testing

`tests/images/test_img_trace_audit_events.py` — 5 unit tests, one per
gap:

1. `test_sec_cite_index_logs_nonempty_sections`: post a mock
   `build_citation_index` return, assert N `SEC_CITE_INDEX` lines = N
   sections with non-empty nums.
2. `test_url_html_map_logs_each_entry_with_source`: mock `url_to_html`
   with 3 entries, assert 3 `URL_HTML_MAP` lines with `src=`
   matching the insertion path.
3. `test_fetch_content_tool_call_logs_full_and_summary`: invoke
   `_make_full_fetch_tool` with a stubbed `ContentFetcher`, assert 1
   `FETCH_CONTENT_TOOL_CALL` line with `mode=full result_status=success`.
4. `test_sec_binding_logs_each_triple`: mock 2 secs × 2 cites × 5
   imgs, assert 4 `SEC_BINDING` lines with correct `cand_count`
   and `sample_alts`.
5. `test_candidate_scored_detail_logs_decision_and_reason`:
   assert keep/drop + reason + score fields populate per candidate.

Run with `pytest tests/images/test_img_trace_audit_events.py -v`.

## Rollback

Revert the single commit. All 8 events are additive logger.info
calls — no pipeline state changes. No migration needed.

## Risk

- **Log volume**: worst case ~1500 lines per run (190 secs × 8 events).
  Aug 6 was ~6300 lines total; +25% volume. Acceptable for INFO
  level; DEBUG users unaffected.
- **String escaping**: loguru's `!r` formatter is used for `alt`
  fields to handle quotes/unicode. Tested manually with Aug 6's
  sample alts including `"The Jin Mao Tower's retail area"`.

## Related work

- `img-trace-five-key-schema.md` (memory) — extends schema with the
  8 new events; will be updated as part of this commit.
- `Aug 6 IMG-TRACE inventory` (`/tmp/aug6_respawn.log`) — re-runnable
  against Aug 7 logs to validate gap closure.

## Commit

```
fix(observability): add 8 IMG-TRACE events to close Aug-6 audit gaps

After dual-condition audit (source-code reasoning AND log-event
evidence) of Aug 6 run 219e7be7-..., five gaps remained unresolved:
G1 url_to_html source path, G2 section_to_nums per-sec visibility,
G3 url_to_html entries, G4 alt-to-sec binding trace, G5 fetch_content
tool-call visibility.

This change adds 8 read-only logger.info events at existing
observation points — no pipeline behaviour change. After Aug 7
re-run the postmortem becomes fully provable from docker logs.

Co-Authored-By: ...
```
