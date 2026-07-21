# Firecrawl 集成设计 — 增强搜索与内容抓取能力

**日期**: 2026-07-17
**状态**: 待审阅
**方案**: A — 双引擎 + 抓取后端钩子

## 目标

在 local-deep-research 中接入本地自托管 Firecrawl（`http://localhost:3002`），增强信息获取的广度与深度：

- **深度**：用 Firecrawl 的 `/v1/scrape` 作为正文抓取后端，补强现有 `batch_fetch_and_extract` 对 JS 渲染/反爬站点的弱点，所有现有引擎受益。
- **广度**：新增 `firecrawl` 搜索引擎，与 SearXNG/Tavily 并列，提供 `/v1/search` 搜索能力。
- **可控**：WebUI 提供总开关、内容抓取开关、`api_url` 配置，关闭时对现有研究流程零影响。

## 需求确认

1. 接入范围：全量（抓取后端 + 搜索引擎 + 两者组合）
2. 开关设计：总开关 `enable` + 独立的「内容抓取后端」开关 `use_for_content_fetch`
3. 内容抓取后端：优先 Firecrawl，失败回落现有 `batch_fetch_and_extract`
4. 搜索阶段：`firecrawl` 搜索引擎内部 `search_mode` 子开关，可在 `/v1/search` 与「LDR 现有引擎搜 + Firecrawl 抓」之间切换
5. 网络/代理：Firecrawl 容器自带 proxy；LDR 调 `http://localhost:3002` 走本地直连，复用现有 `proxy_config.py` 的 NO_PROXY 机制
6. 端点范围：仅用 `/v1/scrape`、`/v1/batch/scrape`、`/v1/search`、`/v1/map`（纯抓取/搜索）；不调用 `/v1/extract`、`/v1/deep-research` 等需 LLM 的端点

## 架构与组件边界

新增/改动共 5 个单元，各自单一职责：

### 1. `web_search_engines/engines/search_engine_firecrawl.py`（新增）— Firecrawl 搜索引擎

- 继承 `BaseSearchEngine`，`is_public=True`、`is_generic=True`（与 Tavily 一致，进 auto-search 候选）
- `search_mode` 决定搜索阶段：
  - `"firecrawl_search"` → 调 `/v1/search`（Firecrawl 自带搜+抓）
  - `"ldr_search"` → 内部委托一个轻量 preview 获取器拿链接，再用 Firecrawl 抓正文。preview 源优先 SearXNG，未配置时回落 DDG（DuckDuckGo，无需 API key，已注册于 `engine_registry`），保证该模式总有可用 preview 源
- `_get_previews` / `_get_full_content` 两阶段，与 Tavily 同构
- API key 经 `_resolve_api_key(..., "search.engine.web.firecrawl.api_key", ...)` 解析（自托管可空）

### 2. `research_library/downloaders/extraction/firecrawl_client.py`（新增）— Firecrawl 抓取后端

- 单一职责：封装 `/v1/scrape`（同步）与 `/v1/batch/scrape`，返回 `Dict[url, Optional[str]]`（markdown 正文）
- 纯 HTTP 客户端，不依赖 LDR 引擎层；被调度层和搜索引擎共用
- 走 `safe_requests`（与 Tavily 一致），`localhost:3002` 由现有 `proxy_config.py` 的 NO_PROXY 直连
- 超时：scrape 单 URL 默认 30s，batch 轮询间隔 2s、总上限 60s

### 3. `research_library/downloaders/extraction/pipeline.py`（改动）— 抓取调度层

- 新增 `fetch_content(urls, settings_snapshot, language, enable_js_rendering)`：开关开启时先调 `firecrawl_client.batch_scrape`，对返回 `None` 的 URL 回落到现有 `batch_fetch_and_extract`
- `batch_fetch_and_extract` 保持原样不动（作为回落路径）
- `FullSearchResults._get_full_content` 与 `run` 改为调用 `fetch_content`（SSRF 校验逻辑保留，在通过校验的 URL 上调用）

### 4. `web_search_engines/engine_registry.py` + `security/module_whitelist.py`（改动）— 注册

- `ENGINE_REGISTRY` 加 `"firecrawl"` 条目：`module_path=".engines.search_engine_firecrawl"`, `class_name="FirecrawlSearchEngine"`
- `module_whitelist.py`：`ALLOWED_MODULE_PATHS` 加模块路径，`ALLOWED_CLASS_NAMES` 加 `FirecrawlSearchEngine`

### 5. `defaults/default_settings.json`（改动）— 设置项

`search.engine.web.firecrawl.*`（照 Tavily 模板）：
- `enable`（总开关，默认 `false`）
- `api_url`（默认 `http://localhost:3002`）
- `api_key`（自托管可空）
- `use_for_content_fetch`（内容抓取开关，默认 `false`）
- `search_mode`（默认 `"firecrawl_search"`，可选 `"ldr_search"`）
- `default_params.*`：`max_results`、`include_full_content`、`timeout`、`batch_max_wait`
- 元数据：`display_name`、`description`、`requires_api_key`（自托管设 `false`）、`reliability`、`strengths`、`weaknesses`、`supports_full_search`、`use_in_auto_search`

### 关键边界

搜索引擎（1）与抓取后端（2）解耦——引擎可选地调用 client，调度层（3）也调用同一个 client，两者不互相依赖。`firecrawl_client` 是两者的唯一共享依赖。

## 数据流

### 流程一：Firecrawl 作为内容抓取后端

开关 `use_for_content_fetch=true` 且 `enable=true` 时，所有引擎受益：

```
任意引擎 _get_previews → BaseSearchEngine.run → FullSearchResults._get_full_content
  → fetch_content(urls, settings_snapshot)         [新调度层]
      ├─ 读 firecrawl.enable + use_for_content_fetch
      │   任一为关 → 直接 batch_fetch_and_extract()  [零行为变化]
      ├─ firecrawl_client.batch_scrape(urls)        [优先]
      │     POST /v1/batch/scrape → 轮询 /v1/batch/scrape/:jobId
      │     返回 {url: markdown | None}
      ├─ 对 None 的 url → batch_fetch_and_extract()  [回落，原管线]
      └─ 合并返回 {url: text}
```

关闭时对现有研究流程完全透明。

### 流程二：firecrawl 搜索引擎（search_mode=firecrawl_search）

```
FirecrawlSearchEngine._get_previews(query)
  → POST /v1/search {query, limit}
  → 解析 data[].url/title/description → previews[{id,title,link,snippet}]
FirecrawlSearchEngine._get_full_content(relevant_items)
  → 优先用 /v1/search 返回的 markdown（去重，避免重复请求）
  → 缺失再补 firecrawl_client.scrape(link)
  → result["content"] = markdown
```

### 流程三：firecrawl 搜索引擎（search_mode=ldr_search）

```
_get_previews → 委托内部 _ldr_preview_fetcher（复用 SearXNG 引擎实例拿链接）
_get_full_content → firecrawl_client.batch_scrape(links)  [Firecrawl 只负责抓]
```

此模式下 Firecrawl 不参与搜索，只当抓取器——复用 LDR 已有搜索质量，靠 Firecrawl 补深度。

### 设置读取

所有开关经 `get_setting_from_snapshot(..., settings_snapshot)` 读取，与 Tavily/SearXNG 一致，支持每用户/线程隔离。

## 错误处理与网络/代理

### 韧性策略

| 场景 | 处理 |
|------|------|
| Firecrawl 服务不可达（连接拒绝/超时） | client 返回 `{url: None}`；调度层把所有 URL 回落到 `batch_fetch_and_extract`；引擎层 `_get_previews` 返回 `[]` 让 MetaSearchEngine 走 fallback（与 Tavily 失败行为一致） |
| 单个 URL scrape 失败 | 该 URL 记 `None`，其余继续；`batch_scrape` 里失败的 URL 同样回落原管线 |
| `/v1/batch/scrape` 超时过长 | 设 `max_wait` 上限（默认 60s），到点放弃该批次 → 回落；不阻塞研究主流程 |
| Firecrawl 返回 429/限流 | 经 `rate_limiting` 的 `_raise_if_rate_limit`（与 Tavily 同机制）；引擎层捕获 `RateLimitError` 重抛 |
| 自托管 Firecrawl 未配 LLM | `/v1/search` 仍可用（自托管 ✅）；不调用 `/v1/extract`/`/v1/deep-research` 等需 LLM 端点 |

### 网络/代理

- client 用 `safe_requests`（与 Tavily 一致），`api_url` 默认 `http://localhost:3002`
- 复用现有 `proxy_config.py`：`localhost`/`127.0.0.1` 在 NO_PROXY 语义内直连——显式确认 `localhost:3002` 命中 NO_PROXY，不依赖 CIDR，避免重蹈 ollama-privoxy 500 坑
- 不为 Firecrawl 新增独立代理配置项（容器侧自理），保持单一 `api_url`

### SSRF 安全

Firecrawl 抓取的 URL 仍要先过 `validate_url`（`FullSearchResults` 现有逻辑保留）——调度层只在已通过 SSRF 校验的 URL 上调用 client，不绕过。

## 测试

沿用 LDR 现有测试结构（`tests/web_search_engines/engines/` 与 `tests/research_library/downloaders/`），`pytest` + `Mock`，不打真实网络。

### `firecrawl_client` 单元测试（`tests/research_library/downloaders/test_firecrawl_client.py`）

- `test_scrape_success` — mock `/v1/scrape` 返回 markdown，断言解析正确
- `test_scrape_failure_returns_none` — 5xx/超时/空 body → 返回 `None`，不抛
- `test_batch_scrape_partial_failure` — 3 URL 中 1 个失败，返回 dict 含 2 成功 1 None
- `test_batch_scrape_polls_until_complete` — mock jobId 轮询：processing→completed
- `test_batch_scrape_timeout_fallback` — 超过 `max_wait` → 返回全 None（触发上层回落）
- `test_localhost_bypasses_proxy` — 断言请求经 NO_PROXY 直连（防 ollama-privoxy 回归）

### 调度层测试（扩 `tests/research_library/downloaders/test_extraction_pipeline.py`）

- `test_fetch_content_disabled_passthrough` — 总开关/抓取开关关 → 直接走 `batch_fetch_and_extract`，firecrawl_client 不被调用
- `test_fetch_content_partial_fallback` — firecrawl 返回部分 None，None 的 URL 进原管线
- `test_fetch_content_firecrawl_down_full_fallback` — client 抛异常 → 全部回落原管线，结果不为空

### 引擎测试（`tests/web_search_engines/engines/test_search_engine_firecrawl.py`）

- `test_previews_firecrawl_search_mode` — mock `/v1/search`，断言 preview 格式 `{id,title,link,snippet}`
- `test_previews_ldr_search_mode` — mock 内部 preview fetcher，断言委托调用
- `test_full_content_reuses_search_markdown` — `/v1/search` 已带 markdown → 不再调 `/v1/scrape`
- `test_previews_empty_on_error` — 服务异常 → 返回 `[]`（符合 MetaSearchEngine fallback 契约）
- `test_rate_limit_reraised` — 429 → `RateLimitError` 重抛

### 验证目标

关闭开关时现有所有测试零回归（Python 侧跑上述测试 + `test_search_engine_base`）。

## 不做（YAGNI）

- 不调用 `/v1/extract`、`/v1/deep-research`、`/v2/agent`、`/v2/browser` 等 LLM/付费端点
- 不实现 `/v1/crawl`（整站爬取）——当前研究流程是「搜+抓单页」，整站爬取与现有迭代式研究模型不匹配
- 不为 Firecrawl 新增独立代理配置项
- 不改动 `batch_fetch_and_extract` 签名（仅在其外加调度层）
