# Strict Context-Entity Image Gate Design

**Date:** 2026-07-25  
**Status:** Proposed for review  
**Scope:** Report image candidate filtering and insertion only

## 1. Problem

The current image pipeline treats a candidate's source page as the primary relevance signal. That is insufficient in three cases:

1. A source page and its image are both unrelated to the report and must be rejected.
2. An image's alt entity is relevant, but its `source_url` is not in any section's cited source pool; the image must be rejected because its provenance is unverifiable.
3. A source page is related, but an individual image on that page is unrelated. This occurs on feeds and aggregation pages where one page contains posts about multiple cities.

The current production path also applies source filtering only to the LLM enhancer, then passes the unfiltered bank to `fill_section_images()`. Consequently, section fallback can reinsert candidates rejected by the earlier filter.

The revised strategy prioritizes precision over image coverage: insufficient evidence means rejection, and image-free sections remain image-free.

## 2. Goals

- Reject candidates whose source and image entities are unrelated to the report.
- Reject candidates whose alt entity is relevant but whose `source_url` is not in any section's cited source pool.
- Reject candidates from related pages when the candidate's own alt entities are unrelated or conflicting.
- Require a non-empty original alt containing at least one substantial named entity.
- Derive relevance only from the current research context and textual entity relationships.
- Ensure every downstream insertion path consumes one filtered eligible bank.
- Preserve explainability through reason-coded IMG-TRACE events.

## 3. Non-goals

- No visual or multimodal relevance verification.
- No Vision-based alt generation in this report-image path.
- No external knowledge-base lookup.
- No attempt to ensure that every report or section has an image.
- No section fallback or other post-LLM image backfill.
- No changes to image downloading, persistence, resizing, database schema, or URL rewriting.

## 4. Governing policy

The gate is fail-closed:

- Missing original alt: reject.
- Alt contains no named entity: reject.
- Entity extraction fails: reject.
- Entity relationship cannot be confirmed from the current report context: reject.
- Entity conflicts with the report: reject.
- Too few eligible images: do not relax any rule.

A source-page match is supporting evidence, not authority. It cannot rescue an entity-conflicting image. Conversely, a missing source citation now *does* reject an image: under the hard Step 5 same-origin check, even an entity explicitly confirmed by current report context is dropped with `source_url_not_cited` when its `source_url` is absent from the cited pool.

## 5. Named-entity requirement

### 5.1 Accepted entity categories

The first version accepts only named entities:

- Geographic entities: Guangzhou, Guangdong, Pearl River, Baiyun Mountain.
- Named attractions and buildings: Canton Tower, Chen Clan Ancestral Hall, Sun Yat-sen Memorial Hall.
- Named institutions and organizations: Guangdong Museum, Guangzhou Metro.
- Named people: Sun Yat-sen.
- Named events: Canton Fair, Guangzhou International Light Festival.

### 5.2 Rejected generic text

Generic categories and topic words do not satisfy the gate:

- travel guide
- popular attraction
- city night view
- ancient architecture
- village
- waterfall
- temple
- food map
- transportation guide
- recommended attraction
- featured image

Examples:

| Alt | Result |
|---|---|
| `博物馆` | Reject: no named entity |
| `广东省博物馆` | Continue: named institution |
| `城市夜景` | Reject: no named entity |
| `广州塔珠江夜景` | Continue: named entities |
| `古建筑` | Reject: no named entity |
| `陈家祠古建筑` | Continue: named attraction |

The gate uses the original extracted alt. It does not generate an alt for a candidate that lacks one.

### 5.3 Substantial-anchor thresholds

A candidate alt entity counts as substantial only when it meets a minimum character length, to reject short generic or fragmentary spans:

- CJK spans: at least 3 CJK characters. A 2-character CJK span (for example `广州`, `北京`, `旅游`) is rejected as too short to be a reliable anchor, even if it appears in context.
- Latin spans: at least 5 characters. A 4-character or shorter Latin span (for example `Asia`, `Photo`, `Tower`) is rejected.

These thresholds are hard requirements applied before any context matching.

### 5.4 Hard source-URL same-origin check (Step 5)

After the named-entity and conflict checks pass, the candidate must additionally satisfy a hard source-URL same-origin check:

- The candidate's `source_url` must appear in the section's cited source pool, i.e. the `SECTION_SOURCES` set emitted per section.
- A candidate whose `source_url` is not cited by any section is dropped with reason `source_url_not_cited`, regardless of how well its alt entity matches the report context.

This replaces the earlier `context_entity_rescue` escape hatch. There is no longer a "rescue" path that keeps a candidate whose source was not selected by section-source mapping; the source URL must be explicitly cited. This trades a small amount of coverage for higher precision, eliminating candidates whose alt happens to match context but whose provenance is unverifiable.

The cited source pool is derived strictly from current-run section sources; no external lookup is performed.

## 6. Report entity context

A `ReportEntityContext` is built from current-run evidence only:

1. Original research query.
2. Report title.
3. `##` section headings.
4. Section bodies.
5. Search-result titles.
6. Search-result content and snippets.
7. Source titles and citation-linked search results.

Conceptual shape:

```python
ReportEntityContext(
    primary_entities={"广州"},
    section_entities={
        0: {"广州塔", "珠江"},
        1: {"陈家祠", "荔湾区"},
        2: {"中山纪念堂", "越秀区", "孙中山"},
    },
    all_entities={...},
    entity_relations={...},
)
```

Entity relationships must be evidenced by the current material. For example, a sentence such as `中山纪念堂位于广州市越秀区` may establish:

```text
中山纪念堂 --located_in--> 广州
中山纪念堂 --located_in--> 越秀区
```

The system must not rely on unstated model world knowledge. If the current context cannot establish the relationship, the result is unresolved and the candidate is rejected.

## 7. Relevance decision model

Each raw candidate receives one terminal decision:

```python
ImageRelevanceDecision(
    status="keep" | "drop",
    reason="...",
    entities={...},
    matched_sections={...},
    source_signal="strong" | "weak" | "none",
    evidence_refs=[...],
)
```

There is no terminal `review` status because visual review is out of scope. Any unresolved case is `drop`.

### 7.1 Keep decisions

A candidate is kept only when its alt contains a named entity and current-run context explicitly confirms that entity as relevant.

Supported evidence:

1. **Exact section-heading entity match**  
   Example: section `中山纪念堂`, alt `中山纪念堂`.

2. **Section-body entity evidence**  
   The candidate entity appears in the section body and is related to the report's primary entity.

3. **Search-context entity evidence**  
   A current search result explicitly connects the candidate entity to the report topic or section.

4. **Explicit entity relation**  
   Current evidence contains a relation such as `中山纪念堂位于广州`.

5. **Source URL cited** (hard Step 5 check)  
   The candidate's `source_url` is present in the section's cited `SECTION_SOURCES` pool. A context-confirmed entity whose `source_url` is not cited is dropped with `source_url_not_cited`; there is no longer a `context_entity_rescue` path.

### 7.2 Drop decisions

Reason codes:

- `missing_alt`: original alt is empty.
- `no_named_entity`: alt contains no accepted named entity.
- `entity_extraction_failed`: extraction infrastructure failed.
- `foreign_entity_conflict`: alt contains a conflicting location/entity and no report-relevant entity.
- `unrelated_named_entity`: alt has a valid named entity, but current context shows no report relationship.
- `unresolved_entity_relation`: a relationship could neither be confirmed nor contradicted.
- `source_url_not_cited`: entity is context-confirmed but the candidate's `source_url` is not in any section's cited `SECTION_SOURCES` pool.
- `context_build_failed`: report context could not be built safely.

An explicit image-entity conflict outranks source relevance. For example:

```text
Report: Guangzhou tourism
Source: Instagram page named "Guangzhou sightseeing"
Alt: "第一次来重庆，别只玩市区的景点"
Decision: DROP foreign_entity_conflict
```

## 8. Source signal

Source mapping remains useful but becomes auxiliary.

- `strong`: current section directly cites or strongly maps to the source page.
- `weak`: source has positive but non-authoritative overlap, was omitted from top-N, or is an aggregation/feed page.
- `none`: no current report relationship.

Decision matrix:

| Entity relationship | Source signal | Decision |
|---|---|---|
| Confirmed relevant | strong | Keep |
| Confirmed relevant | weak | Keep |
| Confirmed relevant | none | Drop `source_url_not_cited` |
| Explicit conflict | any | Drop |
| Unrelated | any | Drop |
| Unresolved | any | Drop |

Aggregation pages must not become strong evidence merely because their URL contains the report location. Examples include Instagram popular/search pages, TikTok search pages, Pinterest boards, and generic galleries. Source-signal classification is performed on `urlparse(source_url).hostname` against an allow-listed set of known aggregation hosts (`instagram.com`, `tiktok.com`, `pinterest.com`, `twitter.com`, `x.com`, `t.me`, `telegram.org`, `facebook.com`) and their subdomains; subdomains like `www.instagram.com` are also classified as weak, while unrelated hosts (for example `anti-instagrambot.com`) are not. The list is a snapshot of today's aggregator landscape and must be updated when observed aggregation destinations change.

## 9. No visual path

This design performs no visual relevance work:

- Do not call `VisionDescriber.describe()` for candidate admission.
- Do not add a multimodal classifier.
- Do not generate missing alt text in this report-image path.
- Do not rescue unresolved candidates via image pixels.

Existing Vision classes may remain for other consumers, but `ImageEnhancer` must receive only candidates already admitted by the text/entity gate.

## 10. Single eligible bank

Introduce one filtered bank and make it the only candidate source after gating:

```python
raw_bank = ImageBank()
decisions = relevance_gate.evaluate(raw_bank, report_context)
eligible_bank = raw_bank.subset(decision.url for decision in decisions if decision.status == "keep")
```

Downstream flow:

```text
Raw Bank
  -> Context-Entity Gate
  -> Eligible Bank
  -> ImageEnhancer
  -> Markdown URL dedupe
  -> Persist
  -> Rewrite local URLs
```

No downstream component may return to `raw_bank` to select additional images.

## 11. Disable section fallback

Remove the production-path call to `fill_section_images()`.

After LLM enhancement:

```python
enhanced = enhancer.enhance(clean_markdown, eligible_bank)
enhanced, original_count, unique_count = _dedupe_images(enhanced)
chosen = extract_markdown_image_urls(enhanced)
```

Required behavior:

- A section without an LLM-selected eligible image remains image-free.
- Deduplication does not trigger replacement selection.
- An empty eligible bank produces an image-free report.
- Low image count never causes gate relaxation.

The existing fallback function may remain temporarily as unused code because the requested behavior is to pause production use. No feature flag is required in the first revision.

## 12. Component boundaries

### 12.1 New `images/relevance.py`

Responsibilities:

- Build or consume `ReportEntityContext`.
- Extract named entities from original alt text.
- Classify source strength.
- Determine entity relationships and conflicts.
- Return reason-coded decisions and matched section indices.

It must not:

- Download images.
- Modify Markdown.
- Persist records.
- Invoke visual models.

### 12.2 `images/bank.py`

Add a public subset operation:

```python
subset(urls: Iterable[str]) -> ImageBank
```

This avoids direct use of the private `_by_url` mapping.

### 12.3 `images/postprocessing.py`

- Build raw Bank as today.
- Build report entity context.
- Invoke the relevance gate.
- Construct one eligible Bank.
- Pass only that Bank to `ImageEnhancer`.
- Remove section fallback invocation.
- Persist only URLs that survived LLM selection and dedupe.

### 12.4 `images/enhancer.py`

- Consume eligible candidates only.
- Do not call `_vision_fill()` in this report-image flow.
- Retain the prompt's same-topic and no-forced-image requirements as defense in depth.

### 12.5 Unchanged components

- Extractor and serialized image format.
- Image download and retry behavior.
- Persistence and `research_images` schema.
- Resizing.
- Markdown remote-to-local URL rewriting.

## 13. Failure handling

The gate fails closed.

| Failure | Behavior |
|---|---|
| Report entity context cannot be built | Drop all candidates; return report without images |
| Entity extraction fails for one candidate | Drop that candidate |
| Entity relationship is unknown | Drop that candidate |
| Search results have no source metadata | Judge only from current report entities; unresolved means drop |
| Eligible Bank is empty | Skip enhancement and preserve image-free Markdown |
| LLM enhancement fails | Preserve original image-free Markdown |
| Too few eligible candidates | Do not relax rules |

## 14. Observability

Summary event:

```text
[IMG-TRACE] ENTITY_GATE research=<id>
total=475
keep_context_match=29
keep_context_rescue=0
drop_missing_alt=83
drop_no_named_entity=210
drop_foreign_conflict=36
drop_unrelated_entity=92
drop_unresolved_relation=20
drop_source_url_not_cited=15
```

Eligible bank:

```text
[IMG-TRACE] ELIGIBLE_BANK research=<id> total=34
```

Fallback state:

```text
[IMG-TRACE] SECTION_FALLBACK research=<id> status=disabled
```

Debug-only per-candidate event:

```json
{
  "research_id": "...",
  "url": "...",
  "alt": "第一次来重庆，别只玩市区的景点",
  "source_url": "https://instagram.com/popular/广州市的观光景点/",
  "entities": ["重庆"],
  "matched_sections": [],
  "decision": "drop",
  "reason": "foreign_entity_conflict",
  "evidence_refs": []
}
```

Do not emit Vision markers in this path because no visual work occurs.

## 15. Test design

### 15.1 Hard gate

- Empty alt -> `missing_alt`; no Vision call.
- `旅游景点攻略推荐` -> `no_named_entity`.
- `古村落瀑布和寺庙` -> `no_named_entity`.
- Entity extractor exception -> `entity_extraction_failed`.

### 15.2 Context matches

- Report contains `广州塔`; alt `广州塔珠江夜景` -> keep.
- Section heading `中山纪念堂`; alt `中山纪念堂` -> keep.
- Search evidence states `中山纪念堂位于广州`; alt `中山纪念堂` -> keep.

### 15.3 Source URL not cited

- Relevant alt entity appears in report context, but the candidate's `source_url` is absent from every section's cited `SECTION_SOURCES` pool -> drop with `source_url_not_cited`.

### 15.4 Cross-region conflict

- Guangzhou report + `重庆洪崖洞旅游攻略` -> drop.
- Guangzhou report + `江西婺源古村落` -> drop.
- Guangzhou Instagram aggregation source + Chongqing alt -> drop.

### 15.5 Unresolved named entities

- Alt has a building proper name not present or related anywhere in current context -> `unresolved_entity_relation` or `unrelated_named_entity`, according to available contradictory evidence.

### 15.6 Pipeline isolation

- Raw Bank contains Guangzhou and Chongqing candidates; eligible Bank contains only Guangzhou.
- Assert enhancer receives only eligible candidates.
- Assert no production call to `fill_section_images()`.
- Assert no Vision invocation.
- Assert chosen URLs are a subset of the eligible Bank.

### 15.7 No-image behavior

- Empty eligible Bank returns the report without images.
- Sections for which the LLM inserts no image remain image-free.
- Markdown deduplication does not cause replacement selection.

## 16. Acceptance criteria

1. Every eligible candidate has a non-empty original alt.
2. Every eligible candidate alt contains at least one accepted named entity.
3. Every eligible entity has explicit relevance evidence in current-run context.
4. Cross-region conflicts are rejected even when the source page appears relevant.
5. Source mapping misses (an uncited `source_url`) reject even context-confirmed image entities with `source_url_not_cited`.
6. Unresolved entity relationships are rejected.
7. No candidate is admitted through Vision or generated alt text.
8. LLM enhancement and all later selection consume only the eligible Bank.
9. Production code does not invoke section fallback.
10. Image-free sections and reports remain image-free.
11. The gate never relaxes because too few images remain.
12. IMG-TRACE reports reason-coded gate totals and eligible count.
13. CJK alt anchors shorter than 3 characters and Latin alt anchors shorter than 5 characters are rejected.

## 17. Minimal implementation scope

Expected files:

- Add `src/local_deep_research/images/relevance.py`.
- Add `tests/images/test_relevance.py`.
- Modify `src/local_deep_research/images/bank.py`.
- Modify `src/local_deep_research/images/postprocessing.py`.
- Modify `src/local_deep_research/images/enhancer.py` only as needed to stop Vision fill in this path.
- Extend `tests/images/test_postprocessing.py` and relevant enhancer tests.

No database migration or UI change is required.
