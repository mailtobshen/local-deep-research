# 统一抓取+提图调度器 — 设计

**Date:** 2026-07-21
**Branch:** i18n-zh-translation (已合并到 master @ 6fb3136)
**Builds on:**
- 2026-07-17 firecrawl 集成 spec (`e11915eb`)
- 2026-07-20 report image extraction spec (`980cc4dd`)
- 2026-07-21 SearXNG-mode image support spec (`64214c88`) — 本设计的直接前置

## Goal

合并 `fetch_content` 和 `fetch_content_with_images` 为一个**内部统一调度器**，按 `enable_images` 决定输出形式（text-only 或 `{text, images}`），同时纠正之前的"Firecrawl-first"逻辑为 **Playwright 优先 / Firecrawl 兜底**，让 `report.enable_images` 与 `firecrawl.use_for_content_fetch` 真正协同工作。

## Root Cause (verified)

之前的代码（commit `b9d2cda2`, `e527dbd2`, `b37122f9`）把"提图"和"抓取"切成两条独立路径：

| 路径 | 入口 | 抓取后端 |
|---|---|---|
| `enable_images=false` | `fetch_content` | Firecrawl-first，失败回落 Playwright（违反需求） |
| `enable_images=true` | `fetch_content_with_images` | **永远** Playwright（无视 Firecrawl 设置） |

**两个问题**：
1. **违反 firecrawl 集成 spec** (`e11915eb`) 的需求："use_for_content_fetch=true 时 SearXNG 搜索并**先尝试 Playwright 抓内容**，**抓取失败再由 firecrawl 抓取兜底**"。当前 `fetch_content` 是 Firecrawl-first。
2. **绕过 Firecrawl 的 `enable_images=true`**：当前 `fetch_content_with_images` 直接调 `AutoHTMLDownloader`，无视 `_firecrawl_enabled`，导致 firecrawl 用户开了 `enable_images` 后图片仍从 Playwright 抓——可能与正文来源不一致。

## Unified Gating Invariant (core constraint)

**两个 gate 独立但协同**：

| `firecrawl.use_for_content_fetch` | `report.enable_images` | 行为 |
|---|---|---|
| `false` (默认) | `false` (默认) | 纯 Playwright，无图（与现状完全一致） |
| `false` | `true` | 纯 Playwright + 提图（图片从 Playwright html 提） |
| `true` | `false` | Playwright 优先，失败 → Firecrawl scrape（markdown only）|
| `true` | `true` | Playwright 优先，失败 → Firecrawl scrape(include_html=True) 拿 markdown + html，提图 |

**两个 gate 默认 `false`，因此默认行为零变化**（YAGNI 不变性）。

## Architecture & Data Flow

**内部唯一调度点**：`_fetch_content_dispatcher(urls, snapshot, enable_images, ...) -> Dict[url, {text, images}]`

**外部两个 wrapper**（保持向后兼容）：
- `fetch_content(...)` → 调 dispatcher + 丢弃 images 字段
- `fetch_content_with_images(...)` → 调 dispatcher + 保持原 `{text, images}` 结构

```
_per_url(url):
  text, raw_html = Playwright.download_with_html(url)         # 单 fetch
  if text:
    if enable_images and raw_html:
      images = extract_images(raw_html, url, title)
    else:
      images = []
    return {text, images}
  elif use_for_content_fetch and Firecrawl enabled:
    response = FirecrawlClient.scrape(url, include_html=enable_images)
    if response:
      text = response["markdown"]
      images = extract_images(response["html"], url, title) if (enable_images and response["html"]) else []
      return {text, images}
  return {text: None, images: []}
```

## Components & Interfaces

**新增** `pipeline._fetch_content_dispatcher`：
```python
def _fetch_content_dispatcher(
    urls: List[str],
    titles: Optional[Dict[str, str]] = None,
    settings_snapshot: Optional[dict] = None,
    language: str = "English",
    enable_js_rendering: bool = False,
    enable_images: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """Single fetcher+image-extraction dispatcher.

    Reuses existing `_firecrawl_enabled(settings_snapshot)` helper (which
    checks both `firecrawl.enable` AND `firecrawl.use_for_content_fetch`)
    to decide whether to attempt Firecrawl fallback.

    For each url:
      1. Playwright download (text + raw_html) via download_with_html()
      2. If Playwright yields text → extract images if enable_images
      3. Else if _firecrawl_enabled(snapshot) →
         single scrape(link, include_html=enable_images); text=markdown, images from html
      4. Else → {text: None, images: []}
    """
```

**改动 `firecrawl_client.scrape`**：
- 现状：default `include_html=False` (b37122f9 之后)
- **保持不变** —— dispatcher 显式传 `include_html=enable_images`

**改动 `fetch_content`**：
- 现状：Firecrawl-first，failed URLs → batch_fetch_and_extract
- 改为薄 wrapper：调 dispatcher，返回 `{url: data["text"]}`

**改动 `fetch_content_with_images`**：
- 现状：直接 Playwright，绕过 fetch_content 调度层
- 改为薄 wrapper：调 dispatcher，返回 `{url: {text, images}}`

**`FullSearchResults._get_full_content` 与 `search_engine_firecrawl._get_full_content`**：
- 不动 — 已在 dispatcher 之外按 `enable_images` gate 选择调哪个 wrapper

## Error Handling & Boundaries

| Stage | Failure | Handling |
|---|---|---|
| Playwright `_fetch_html` | network/timeout/non-HTML | returns `(None, None)` → dispatcher 进 Firecrawl 兜底（如果启用） |
| Playwright `extract_images` | malformed HTML / bs4 error | text unaffected, images=[]（decoupled try/except）|
| Firecrawl `scrape` | HTTP/timeout/429 | per-URL try/except；429 propagate RateLimitError |
| Firecrawl `scrape` | returned `None` 或 partial | text=None, images=[] |
| `_fetch_content_dispatcher` 全局异常 | unexpected | degrade to `batch_fetch_and_extract` 仅 text（保持与现行 fetch_content 一致韧性）|

**关键边界**：
1. **默认配置（两个 gate 都 false）**：dispatcher 行为 = 纯 Playwright，无图。与现 `fetch_content` 行为**完全一致**。
2. **Playwright 失败的 URL 在 firecrawl 关闭时**：`{text: None, images: []}`，caller 已处理（fallback 到空内容）。
3. **Playwright 失败的 URL 在 firecrawl 开启时**：进 Firecrawl scrape（单 URL 模式），firecrawl 失败 → `{text: None, images: []}`。
4. **提图异常隔离**：`extract_images` 用独立 try/except，不影响 text 返回（已有 invariant）。

**Security:** 无新网络 surface（仅 dispatcher 内部多了一个 firecrawl scrape 调用场景，firecrawl client 已有 SSRF + safe_requests 防护）。

## Testing Strategy

**新增 dispatcher 单元测试**（`tests/research_library/downloaders/test_extraction_dispatcher.py`）：
- 4 配置 × Playwright success/failure × Firecrawl success/failure 矩阵
- 关键断言：
  - 默认配置：dispatcher = `batch_fetch_and_extract` (proxy via Playwright)
  - Playwright 失败 + firecrawl 关闭 → `{text: None, images: []}`
  - Playwright 失败 + firecrawl 开启 → firecrawl.scrape 调一次，images 来自 firecrawl html
  - enable_images=true + Playwright 成功 → images 来自 Playwright html
  - enable_images=true + firecrawl scrape include_html=True → 验证调用签名

**更新 `test_fetch_content_with_images.py`**：
- 现有 happy-path 测试不变（仍通过 wrapper 走 dispatcher）
- 新增 firecrawl 兜底场景
- 现有 firecrawl-first 旧测试（如有）改写

**保留 `test_gate_off_invariant.py`**：
- 仍锁 OFF=markdown only + dispatch 无 firecrawl 调用

**保持 `test_extraction_pipeline.py`**：
- 现有 fetch_content 测试更新为 wrapper 形式（仍验证 default = batch_fetch_and_extract passthrough）

**Integration (closeout)**：
- 容器重启 + 跑 SearXNG 研究（默认 config）→ 报告文本与现状一致
- `enable_images=true` 跑 SearXNG 研究 → 报告含 `/images/` 路由，html_content 是 image-list JSON
- `use_for_content_fetch=true` 跑 SearXNG 研究 → Playwright 失败的 URL 进 firecrawl（需 firecrawl 容器可达）

## Changed / New Files

**改动**：
- `research_library/downloaders/extraction/pipeline.py`
  - 新增 `_fetch_content_dispatcher`
  - `fetch_content` 改 wrapper（delegates to dispatcher）
  - `fetch_content_with_images` 改 wrapper（delegates to dispatcher）
- `tests/research_library/downloaders/test_extraction_pipeline.py`
  - 更新 fetch_content wrapper 测试
- `tests/images/test_fetch_content_with_images.py` (in `tests/images/`)
  - 更新断言反映 dispatcher 行为
- `tests/images/test_gate_off_invariant.py` (in `tests/images/`)
  - 保持（仍锁 OFF invariant）

**新增**：
- `tests/research_library/downloaders/test_extraction_dispatcher.py`
  - 4×2×2 = 16 路径单元测试

## Interaction with Delivered Work

**复用不变**：
- `extract_images` (image plan T1) — dispatcher 调用点
- `ImageBank/ImageEnhancer/ImageStore` (image plan T2/T5/T6) — 完整保留
- `FirecrawlClient.scrape(url, include_html=False)` (image plan T5) — dispatcher 显式传 include_html
- `HTMLDownloader.download_with_html` (searxng-image T2) — dispatcher Playwright 调用点
- `dumps_images/loads_images` (searxng-image T1) — image-list JSON 序列化保持
- `fetch_content_with_images` 已有 4/4 测试覆盖：保留 happy-path，新增 firecrawl 兜底场景

**被覆盖**：
- 之前 `fetch_content` 的 Firecrawl-first 行为（违反需求）→ 改为 Playwright-first
- 之前 `fetch_content_with_images` 绕过 firecrawl 抓取 → 改为走 dispatcher

## YAGNI

- 不合并两个公开 API 为单一函数（保持向后兼容）
- 不为 dispatcher 加新设置（仅消费现有 `firecrawl.use_for_content_fetch` + `report.enable_images`）
- 不动 `batch_fetch_and_extract`（作为 dispatcher 最底层调用）
- 不为 firecrawl 加新批量带 html 接口（fallback URL 少，单 URL scrape 够用）
- 不改 FullSearchResults / search_engine_firecrawl 的 gate 逻辑（wrapper 之外已正确）

## Caller Pre-Verification (verified 2026-07-21)

- `fetch_content` 调用方：grep 已确认仅 `FullSearchResults._get_full_content` 调用。Wrapper 改动不破坏。
- `fetch_content_with_images` 调用方：grep 已确认仅 `FullSearchResults._get_full_content`（enable_images ON 分支）调用。Wrapper 改动不破坏。
- `firecrawl_client.scrape` 调用方：grep 已确认仅 `search_engine_firecrawl._get_full_content` + dispatcher 新增。`include_html` default=False 不破坏现有 caller。