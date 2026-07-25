# 报告图片与文字段落同源过滤方案

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 报告 markdown 中每段文字配图必须严格来自该段文字的同源 URL——无同源图不强求插图，不允许跨源乱配。

**Architecture:** 在 `enhance_report_with_images` 之前从 `results.findings[].search_results[].link` 推导出"每段允许的 source URL 集"；对 `ImageBank` 跨 source 的候选 drop；LLM 增强与 section_fallback 都限制在该段允许集内；langgraph fill 改为"只 fetch 缺 html_content 的 source"。

**Tech Stack:** Python（re/loguru）, existing pytest harness for `tests/images/`, existing IMG-TRACE observability conventions.

## Global Constraints

- 不引入任何"城市/类别"字典；段 ↔ source 匹配完全靠 token 重合度（中文 unigram/bigram + 英文单词 + 罗马词包含子串），与现有 `fill_section_images` 复用同一评分函数。
- 段无同源候选 → 不插图，不退化到跨源候选。
- 现有 alt 截断 / `<img>` HTML 形态 / 500 cap / dedupe / section_fallback 不变。
- 所有 images 链路日志必须用 `from loguru import logger` + f-string；新增 `THEME_DROP` / `LANGGRAPH_FILL` / `NO_SOURCE_META` 三类 IMG-TRACE 行。
- 改动需 `docker compose -f docker-compose.ldr-local.yml restart local-deep-research` 让 Flask 重新加载。
- 测试优先 TDD：先写红测试，再改代码。

---

### Task 1: 段 ↔ source URL 匹配函数

**Files:**
- Modify: `src/local_deep_research/images/postprocessing.py`
- Create: `tests/images/test_segment_sources.py`

**Interfaces:**
- Consumes: `clean_markdown`、`results`（含 `findings[].search_results[].link/title/content/snippet`）。
- Produces: `extract_segment_sources(markdown, results) -> list[tuple[str, str, list[str]]]`，每项 `(heading, body, allowed_urls)`；按 markdown 出现顺序，节段继承父级允许集（当前段无 ≥1 强匹配时 fallback 到上一段集）。

- [ ] **Step 1: 写失败测试**

```python
def test_extract_segment_sources_chinese_alignment():
    md = "## 鼓浪嶼\n\n厦门鼓浪屿介绍。\n\n## 越秀公园\n\n广州越秀公园介绍。\n"
    results = {
        "findings": [
            {"search_results": [
                {"link": "https://xiamen-travel.com/places", "title": "厦门鼓浪屿",
                 "content": "鼓浪屿是厦门著名景点", "snippet": "鼓浪屿"},
                {"link": "https://gzdaily.com/places", "title": "广州越秀公园",
                 "content": "越秀公园在广州", "snippet": "越秀公园"},
            ]}
        ]
    }
    out = extract_segment_sources(md, results)
    assert [seg[2] for seg in out] == [
        ["https://xiamen-travel.com/places"],
        ["https://gzdaily.com/places"],
    ]


def test_extract_segment_sources_inherits_when_no_match():
    md = "## 鼓浪嶼\n\nA\n\n## Foo\n\nB\n"
    results = {"findings": [
        {"search_results": [
            {"link": "https://x", "title": "t", "content": "A", "snippet": ""}
        ]}
    ]}
    out = extract_segment_sources(md, results)
    # Second segment inherits from first since no match.
    assert out[1][2] == out[0][2]


def test_extract_segment_sources_no_results_returns_empty():
    md = "## A\n\nbody"
    assert extract_segment_sources(md, {}) == []


def test_extract_segment_sources_uses_top_n_per_segment():
    md = "## 鼓浪嶼\n\n厦门鼓浪屿\n"
    results = {"findings": [
        {"search_results": [
            {"link": "https://xiamen-travel.com", "title": "鼓浪屿", "content": "鼓浪屿", "snippet": ""},
            {"link": "https://gzdaily.com", "title": "广州", "content": "广州", "snippet": ""},
            {"link": "https://others.com", "title": "Other", "content": "Other", "snippet": ""},
        ]}
    ]}
    out = extract_segment_sources(md, results, top_n=1)
    assert out[0][2] == ["https://xiamen-travel.com"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/images/test_segment_sources.py -q`
Expected: 4 用例全失败（`ImportError: cannot import name 'extract_segment_sources'`）。

- [ ] **Step 3: 实现**

`postprocessing.py` 顶部新增：

```python
def _match_terms(text: str) -> set[str]:
    """Tokenizer for cross-language overlap. Reused by fill_section_images."""
    # 与 fill_section_images 内的实现保持一致（bigram + 罗马词 + 拼接 token）。
    ...


def _score_match(heading_terms: set[str], alt_terms: set[str]) -> int:
    """Token-overlap score with substring bonus. Reused."""
    ...


def _sectionize(markdown: str) -> list[tuple[str, str]]:
    """Split markdown into (heading, body) pairs by `## `."""
    parts = re.split(r"(?m)^(##\s+.*)$", markdown)
    out = []
    for i in range(1, len(parts), 2):
        heading = parts[i]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        out.append((heading, body))
    if not out and markdown.strip():
        out = [("", markdown)]
    return out


def extract_segment_sources(
    markdown: str, results, top_n: int = 3
) -> list[tuple[str, str, list[str]]]:
    """For each ## section, return the top-N search_result URLs whose text
    most closely matches the section body. Sections with no match inherit
    the previous section's allow-list. Returns [] if `results` has no
    search_results (the caller should fall back to global matching)."""
    if not isinstance(results, dict):
        return []
    candidates: list[dict] = []
    for finding in results.get("findings", []) or []:
        for sr in finding.get("search_results", []) or []:
            if not isinstance(sr, dict):
                continue
            url = sr.get("link") or sr.get("url")
            if not url:
                continue
            candidates.append({
                "url": url,
                "title": sr.get("title") or "",
                "content": sr.get("content") or sr.get("snippet") or "",
                "snippet": sr.get("snippet") or "",
            })
    if not candidates:
        return []

    sections = _sectionize(markdown)
    out: list[tuple[str, str, list[str]]] = []
    inherited: list[str] = []
    for heading, body in sections:
        section_text = re.sub(r"^##\s+", "", heading) + "\n" + body
        section_terms = _match_terms(section_text)
        if not section_terms:
            out.append((heading, body, list(inherited)))
            continue
        scored = []
        for c in candidates:
            cand_terms = _match_terms(
                " ".join([c["title"], c["content"], c["snippet"]])
            )
            scored.append((_score_match(section_terms, cand_terms), c["url"]))
        scored.sort(key=lambda x: x[0], reverse=True)
        # Keep URLs that score > 0; if none, inherit.
        allowed = [url for score, url in scored if score > 0][:top_n]
        if not allowed:
            allowed = list(inherited)
        out.append((heading, body, allowed))
        inherited = allowed
    return out
```

- [ ] **Step 4: 重跑测试**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/images/test_segment_sources.py -q`
Expected: 4 通过。`git commit` 提交。

---

### Task 2: 跨源候选 drop + IMG-TRACE `THEME_DROP`

**Files:**
- Modify: `src/local_deep_research/images/postprocessing.py` `enhance_report_with_images`
- Test: `tests/images/test_postprocessing.py`（新增"跨 source 候选被 drop"用例）

**Interfaces:**
- Consumes: `extract_segment_sources(markdown, results)`、现有 `ImageBank`。
- Produces: 在 `_dedupe_images(enhanced)` 之后、`fill_section_images` 之前计算每段允许集；对 `bank.candidates_with_alt()` 过滤：URL ∈ 任一段允许集或当前 section 文本的 url 集才保留；IMG-TRACE `THEME_DROP research=… kept_in=… segment_dropped=…`；最终把过滤后的 URL 集传给 LLM 增强和 fallback。

- [ ] **Step 1: 写失败测试**

```python
def test_enhance_drops_candidates_from_off_topic_source(monkeypatch):
    # Bank holds two candidates: one from "guangzhou-source", one from
    # "xiamen-source". The report's only segment matches the guangzhou
    # source, so the xiamen candidate must be dropped.
    bank = ImageBank()
    bank.add([ExtractedImage("https://gz/x.jpg", "越秀公园", "https://gz", "广州", 100, 80)])
    bank.add([ExtractedImage("https://xm/y.jpg", "鼓浪屿", "https://xm", "厦门", 100, 80)])
    monkeypatch.setattr("local_deep_research.images.postprocessing.loads_images", lambda html: [])
    results = {"findings": [
        {"search_results": [
            {"link": "https://gz", "title": "广州景点", "content": "越秀公园 广州", "snippet": ""},
        ]}
    ]}
    md = "## 越秀公园\n\n介绍越秀公园\n"
    from local_deep_research.images.postprocessing import _filter_bank_by_segments
    kept = _filter_bank_by_segments(bank.all_urls(), bank, md, results)
    assert "https://gz/x.jpg" in kept
    assert "https://xm/y.jpg" not in kept
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/images/test_postprocessing.py::test_enhance_drops_candidates_from_off_topic_source -q`
Expected: 失败（`_filter_bank_by_segments` 不存在）。

- [ ] **Step 3: 实现**

在 `enhance_report_with_images` 中：

```python
# After _dedupe_images(enhanced), before fill_section_images.
segment_sources = extract_segment_sources(enhanced, results)
allowed_urls: set[str] = set()
for _, _, urls in segment_sources:
    allowed_urls.update(urls)

def _allowed(url: str) -> bool:
    if not allowed_urls:
        return True  # graceful fallback when no source metadata
    return any(url.startswith(prefix) or prefix in url for prefix in allowed_urls)
# Match by the candidate's source_url (where it was crawled from), not
# the image URL itself. image.source_url is set in ExtractedImage.
# Filter the bank before LLM enhancement.
filtered_candidates = [
    c for c in bank.candidates_with_alt() + bank.candidates_without_alt(limit=10**9)
    if _allowed(c.source_url)
]
dropped = (
    len(bank.candidates_with_alt()) + len(bank.candidates_without_alt(limit=10**9))
    - len(filtered_candidates)
)
logger.info(
    f"[IMG-TRACE] THEME_DROP research={research_id} "
    f"kept_in={len(allowed_urls)} segment_dropped={dropped}"
)
```

Pass `filtered_candidates` to the enhancer and to `fill_section_images`. If `allowed_urls` is empty (results had no source metadata), keep the previous global behavior and log `NO_SOURCE_META research={research_id}`.

- [ ] **Step 4: 重跑测试**

Expected: 通过。`git commit` 提交。

---

### Task 3: LLM prompt 提示同源校验

**Files:**
- Modify: `src/local_deep_research/images/enhancer.py` `_PROMPT`
- Test: 现有 enhancer 测试套（无新增）

**Interfaces:**
- Consumes: 现有 `_format_list` 函数。
- Produces: 在候选列表中给每条追加 `source=` 字段，并在 STRICT RULES 加一条"source 字段必须与 section 主题同源"。

- [ ] **Step 1: 改 prompt**

```python
_PROMPT = """You are editing a research report to add real images.

STRICT RULES:
- You may ONLY use image URLs from the "Available images" list below.
- You MUST NOT invent, modify, or guess any image URL.
- Do NOT change any factual text, numbers, or citations in the report.
- STRICT SAME-SOURCE RULE: For each image, both the image's alt text AND
  its source URL must be topically related to the section you place it in.
  "Same source" means the page the image was crawled from is about the
  same subject as the section. If a section has no image whose source
  page matches, LEAVE THAT SECTION IMAGE-FREE — do not borrow an
  image from a different source.
- Each image URL may appear at most ONCE in the output.
- Insert images using markdown: ![alt](url), placed immediately after the
  section's heading line.
- If no available image fits a section, insert nothing there — never force
  an image.

Available images (url | alt | source_url):
{image_list}

Report to enhance:
---
{markdown}
---

Return ONLY the enhanced report markdown, nothing else."""
```

- [ ] **Step 2: 更新 `_format_list`**

```python
def _format_list(images: list[ExtractedImage]) -> str:
    return "\n".join(
        f"- {i.url} | {i.alt} | {i.source_url or '(unknown)'}"
        for i in images
    )
```

- [ ] **Step 3: 重跑测试**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/images -q`
Expected: 通过。`git commit` 提交。

---

### Task 4: section_fallback 限制在段允许集内

**Files:**
- Modify: `src/local_deep_research/images/postprocessing.py` `enhance_report_with_images`
- Test: `tests/images/test_section_fallback.py`（新增"段允许集外的候选被 drop"用例）

**Interfaces:**
- Consumes: 已过滤的 `filtered_candidates`、每段允许 URL 集。
- Produces: 在 `fill_section_images` 调用前传入 `segment_allow=segment_sources`；修改 `fill_section_images` 签名以接受该参数并按段过滤候选。

- [ ] **Step 1: 写失败测试**

```python
def test_section_fallback_drops_off_source_candidate():
    md = "## 越秀公园\n\nbody"
    candidates = [
        ExtractedImage("https://gz/x.jpg", "越秀公园", "https://gz-source", "广州", 600, 400),
        ExtractedImage("https://xm/y.jpg", "鼓浪屿", "https://xm-source", "厦门", 600, 400),
    ]
    segment_allow = [("## 越秀公园\n", "body", ["https://gz-source"])]
    out = fill_section_images(md, candidates, segment_allow=segment_allow)
    assert "https://gz/x.jpg" in out
    assert "https://xm/y.jpg" not in out
```

- [ ] **Step 2: 跑测试确认失败**

Expected: 失败（`fill_section_images` 不接受 `segment_allow`）。

- [ ] **Step 3: 改 `fill_section_images`**

```python
def fill_section_images(
    markdown: str, candidates, segment_allow=None
) -> str:
    """If segment_allow is provided, only candidates whose source_url is
    in the section's allowed list may be placed in that section."""
    sections = list(_sectionize(markdown))
    # zip sections with their allow-list; fall back to global (empty
    # allow-list == no restriction) for tests that don't pass it.
    allow_for_index = []
    for i in range(len(sections)):
        if segment_allow and i < len(segment_allow):
            allow_for_index.append(set(segment_allow[i][2]))
        else:
            allow_for_index.append(set())
    used_urls = {match.group(2) for match in _IMG_RE.finditer(markdown)}
    parts = re.split(r"(?m)^(##\s+.*)$", markdown)

    for index in range(1, len(parts), 2):
        sec_index = (index - 1) // 2
        heading = parts[index]
        body = parts[index + 1] if index + 1 < len(parts) else ""
        if _IMG_RE.search(body):
            continue
        allow = allow_for_index[sec_index] if sec_index < len(allow_for_index) else set()
        heading_terms = _match_terms(re.sub(r"^##\s+", "", heading))
        if not heading_terms:
            continue
        best = None
        best_score = 0
        for candidate in candidates:
            if not candidate.alt or candidate.url in used_urls:
                continue
            if allow and (candidate.source_url or "") not in allow:
                continue
            score = _score_match(heading_terms, _match_terms(candidate.alt))
            if score > best_score:
                best = candidate
                best_score = score
        if best is not None:
            parts[index] = f"{heading}\n![{best.alt}]({best.url})"
            used_urls.add(best.url)
    return "".join(parts)
```

- [ ] **Step 4: 重跑测试**

Expected: 通过。`git commit` 提交。

---

### Task 5: langgraph 抓取收紧

**Files:**
- Modify: `src/local_deep_research/advanced_search_system/strategies/langgraph_agent_strategy.py` `_ensure_images_for_results`
- Test: 现有测试应不受影响（langgraph 测试覆盖有限）

**Interfaces:**
- Consumes: `all_search_results`。
- Produces: 改为"对每个缺 `html_content` 的 source 各 fetch 一次"；IMG-TRACE `LANGGRAPH_FILL research=… filled=N total=M`；不再有"前 10 个"硬限制。

- [ ] **Step 1: 改函数**

```python
def _ensure_images_for_results(self, all_search_results: list) -> None:
    if not get_bool_setting_from_snapshot(
        "report.enable_images", default=False,
        settings_snapshot=self.settings_snapshot,
    ):
        logger.info("[IMG-TRACE] langgraph auto-image-fill: skipped (report.enable_images=off)")
        return

    urls_to_fetch: list[str] = []
    titles_attr = getattr(self, "titles", None)
    titles = titles_attr if isinstance(titles_attr, dict) else {}
    for r in all_search_results:
        if not isinstance(r, dict):
            continue
        url = r.get("link") or r.get("url")
        if not url or r.get("html_content"):
            continue
        if url not in urls_to_fetch:
            urls_to_fetch.append(url)
    if not urls_to_fetch:
        logger.info(
            "[IMG-TRACE] langgraph auto-image-fill: skipped (no source missing html_content)"
        )
        return

    logger.info(
        f"[IMG-TRACE] langgraph auto-image-fill: fetching {len(urls_to_fetch)} URLs for image extraction"
    )
    try:
        from local_deep_research.images.serialize import dumps_images
        from local_deep_research.research_library.downloaders.extraction.pipeline import (
            fetch_content_with_images,
        )
        data = fetch_content_with_images(
            urls_to_fetch,
            titles={u: titles.get(u, "") for u in urls_to_fetch},
            settings_snapshot=self.settings_snapshot,
        )
    except Exception:
        logger.exception("langgraph auto-image-fill: fetch_content_with_images failed")
        return

    filled = 0
    for url in urls_to_fetch:
        entry = data.get(url) if isinstance(data, dict) else None
        images = entry.get("images", []) if entry else []
        if self.collector.attach_html_content(url, dumps_images(images)):
            filled += 1
    logger.info(
        f"[IMG-TRACE] LANGGRAPH_FILL filled={filled}/{len(urls_to_fetch)}"
    )
```

- [ ] **Step 2: 更新调用点签名**

`_finalize` 中的调用（line 874）改为 `_ensure_images_for_results(all_search_results)`，去掉 `max_n=10` 入参。

- [ ] **Step 3: 跑测试**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests -q -x -k "langgraph or image"`
Expected: 通过（无现有失败）。`git commit` 提交。

---

### Task 6: 整体验证 + 重启

**Files:** 无新增。

- [ ] **Step 1: 跑完整测试套**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/images -q
```
Expected: 通过。

- [ ] **Step 2: 重启容器**

```bash
docker compose -f docker-compose.ldr-local.yml restart local-deep-research
docker inspect --format '{{.State.Health.Status}}' ldr-local
```
Expected: `healthy`。

- [ ] **Step 3: 文档化**

将本方案追加到 `.superpowers/sdd/progress.md`，并把"图与文同源"作为 IMG-TRACE 新观察点写进 `img-trace-observability` 备忘。

---

## 旧版计划（`2026-07-24-image-and-log-ux-fixes.md`）状态

Tasks 1-4 已在 `main` 落地并 review 通过；Task 5（验证 & 部署）已完成。本文件 Task 1-6 是后续修复，**叠加**在旧计划之上，不替代。旧计划的所有代码改动保留。

## 验证

- 单元：`pytest tests/images -q` 通过
- 集成：跑一个新研究 query "广州景点"，IMG-TRACE 应输出 `THEME_DROP … kept_in=2 segment_dropped=4`（trip.com 厦门 16 张全 drop），报告内厦门图片不出现。
- 容器：`docker inspect … healthy`
