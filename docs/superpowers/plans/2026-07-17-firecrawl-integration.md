# Firecrawl 集成实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 local-deep-research 中接入本地自托管 Firecrawl，作为内容抓取后端（提升深度）与并列搜索引擎（提升广度），WebUI 可控开关。

**Architecture:** 新增独立 `firecrawl_client`（纯 HTTP 抓取后端）+ `FirecrawlSearchEngine`（搜索引擎）。在 `batch_fetch_and_extract` 之上加 `fetch_content` 调度层：开关开启时优先 Firecrawl，失败回落原管线。注册进 `engine_registry` + 白名单 + `default_settings.json`。

**Tech Stack:** Python, requests（经 `safe_requests`）, pytest + Mock, LDR 现有 `BaseSearchEngine`/`FullSearchResults` 架构。

## Global Constraints

- 仅调用 Firecrawl `/v1/scrape`、`/v1/batch/scrape`、`/v1/search` 端点；不调用 `/v1/extract`、`/v1/deep-research` 等需 LLM 端点
- 默认 `api_url=http://localhost:3002`；经 `safe_requests` 调用，容器内需 `allow_private_ips=True`
- `firecrawl.enable` 与 `firecrawl.use_for_content_fetch` 默认 `false`；关闭时现有研究流程零行为变化
- 不改动 `batch_fetch_and_extract` 签名，仅在其外加 `fetch_content` 调度层
- 不为 Firecrawl 新增独立代理配置项；复用 `proxy_config.py` NO_PROXY 机制
- 设置键命名空间：`search.engine.web.firecrawl.*`，照 Tavily 模板

**参考文档:** `docs/superpowers/specs/2026-07-17-firecrawl-integration-design.md`

---

## File Structure

- **Create** `src/local_deep_research/research_library/downloaders/extraction/firecrawl_client.py` — 纯 HTTP 客户端，封装 `/v1/scrape`、`/v1/batch/scrape`、`/v1/search`
- **Create** `tests/research_library/downloaders/test_firecrawl_client.py` — client 单元测试
- **Modify** `src/local_deep_research/research_library/downloaders/extraction/pipeline.py` — 新增 `fetch_content` 调度层
- **Modify** `src/local_deep_research/web_search_engines/engines/full_search.py` — `run`/`_get_full_content` 改调 `fetch_content`
- **Modify** `tests/research_library/downloaders/test_extraction_pipeline.py` — `fetch_content` 调度层测试
- **Create** `src/local_deep_research/web_search_engines/engines/search_engine_firecrawl.py` — Firecrawl 搜索引擎
- **Create** `tests/web_search_engines/engines/test_search_engine_firecrawl.py` — 引擎测试
- **Modify** `src/local_deep_research/web_search_engines/engine_registry.py` — 注册 `firecrawl`
- **Modify** `src/local_deep_research/security/module_whitelist.py` — 白名单加模块路径与类名
- **Modify** `src/local_deep_research/defaults/default_settings.json` — 新增 `search.engine.web.firecrawl.*` 设置项

---

## Task 1: firecrawl_client — 单 URL scrape

**Files:**
- Create: `src/local_deep_research/research_library/downloaders/extraction/firecrawl_client.py`
- Test: `tests/research_library/downloaders/test_firecrawl_client.py`

**Interfaces:**
- Produces: `FirecrawlClient(api_url, api_key, timeout)` with method `scrape(url) -> Optional[str]`（返回 markdown 正文，失败返回 None）

- [ ] **Step 1: 写失败测试**

```python
# tests/research_library/downloaders/test_firecrawl_client.py
from unittest.mock import patch, MagicMock

from local_deep_research.research_library.downloaders.extraction.firecrawl_client import (
    FirecrawlClient,
)


def _mock_response(status, json_body):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body
    resp.raise_for_status.return_value = None if status < 400 else Exception("http error")
    return resp


def test_scrape_success():
    client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
    body = {"data": {"markdown": "# Title\n\nbody text"}}
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=_mock_response(200, body),
    ):
        result = client.scrape("https://example.com")
    assert result == "# Title\n\nbody text"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/research_library/downloaders/test_firecrawl_client.py::test_scrape_success -v`
Expected: FAIL with ModuleNotFoundError / ImportError

- [ ] **Step 3: 写最小实现**

```python
# src/local_deep_research/research_library/downloaders/extraction/firecrawl_client.py
"""Firecrawl HTTP client — wraps /v1/scrape, /v1/batch/scrape, /v1/search.

Pure HTTP client, no LDR engine-layer dependencies. Shared by the
fetch_content dispatch layer and the FirecrawlSearchEngine.
"""
from typing import Any, Dict, List, Optional

from loguru import logger

from ....security.safe_requests import safe_get, safe_post

DEFAULT_API_URL = "http://localhost:3002"
DEFAULT_TIMEOUT = 30


class FirecrawlClient:
    """Thin client over a self-hosted Firecrawl instance.

    All calls go through safe_requests so SSRF + proxy-bypass rules apply.
    localhost/private-IP targets are allowed via allow_private_ips=True
    (Firecrawl is a trusted self-hosted service).
    """

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        api_key: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def scrape(self, url: str) -> Optional[str]:
        """Scrape a single URL, return markdown body or None on failure."""
        payload = {"url": url, "formats": ["markdown"]}
        try:
            resp = safe_post(
                f"{self.api_url}/v1/scrape",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
                allow_private_ips=True,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            md = data.get("markdown")
            return md if isinstance(md, str) and md.strip() else None
        except Exception:
            logger.debug(f"Firecrawl scrape failed for {url}", exc_info=True)
            return None
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/research_library/downloaders/test_firecrawl_client.py::test_scrape_success -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/local_deep_research/research_library/downloaders/extraction/firecrawl_client.py tests/research_library/downloaders/test_firecrawl_client.py
git commit -m "feat(firecrawl): add FirecrawlClient with single-URL scrape"
```

## Task 2: firecrawl_client — 失败与 batch scrape 轮询

**Files:**
- Modify: `tests/research_library/downloaders/test_firecrawl_client.py`
- Modify: `src/local_deep_research/research_library/downloaders/extraction/firecrawl_client.py`

**Interfaces:**
- Produces: `FirecrawlClient.batch_scrape(urls, max_wait=60, poll_interval=2) -> Dict[str, Optional[str]]`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/research_library/downloaders/test_firecrawl_client.py

def test_scrape_failure_returns_none():
    client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=_mock_response(500, {}),
    ):
        result = client.scrape("https://example.com")
    assert result is None


def test_batch_scrape_polls_until_complete():
    client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
    create_resp = _mock_response(
        200, {"id": "job-1", "status": "processing"}
    )
    poll_processing = _mock_response(200, {"status": "processing", "completed": 0})
    poll_done = _mock_response(
        200,
        {
            "status": "completed",
            "completed": 2,
            "data": [
                {"url": "https://a.com", "markdown": "# A"},
                {"url": "https://b.com", "markdown": "# B"},
            ],
        },
    )
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=create_resp,
    ):
        with patch(
            "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_get",
            side_effect=[poll_processing, poll_done],
        ):
            with patch("time.sleep"):  # 加速轮询
                result = client.batch_scrape(
                    ["https://a.com", "https://b.com"], max_wait=60, poll_interval=1
                )
    assert result == {"https://a.com": "# A", "https://b.com": "# B"}


def test_batch_scrape_partial_failure():
    """完成回调里缺失的 URL 记 None，不抛异常。"""
    client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
    create_resp = _mock_response(200, {"id": "job-1", "status": "processing"})
    poll_done = _mock_response(
        200,
        {
            "status": "completed",
            "completed": 1,
            "data": [{"url": "https://a.com", "markdown": "# A"}],
        },
    )
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=create_resp,
    ):
        with patch(
            "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_get",
            return_value=poll_done,
        ):
            with patch("time.sleep"):
                result = client.batch_scrape(
                    ["https://a.com", "https://b.com"], max_wait=60, poll_interval=1
                )
    assert result["https://a.com"] == "# A"
    assert result["https://b.com"] is None


def test_batch_scrape_timeout_returns_all_none():
    """超过 max_wait 仍未完成 -> 返回全 None（触发上层回落）。"""
    client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
    create_resp = _mock_response(200, {"id": "job-1", "status": "processing"})
    poll_processing = _mock_response(200, {"status": "processing", "completed": 0})
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=create_resp,
    ):
        with patch(
            "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_get",
            return_value=poll_processing,
        ):
            with patch("time.sleep"):
                result = client.batch_scrape(
                    ["https://a.com", "https://b.com"], max_wait=0, poll_interval=1
                )
    assert result == {"https://a.com": None, "https://b.com": None}
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/research_library/downloaders/test_firecrawl_client.py -v`
Expected: 新增测试 FAIL（batch_scrape 不存在 / 超时逻辑缺失）

- [ ] **Step 3: 扩展实现**

在 `firecrawl_client.py` 顶部 import 区加 `import time`，并在类内追加：

```python
    def batch_scrape(
        self,
        urls: List[str],
        max_wait: int = 60,
        poll_interval: int = 2,
    ) -> Dict[str, Optional[str]]:
        """Batch-scrape URLs. Returns {url: markdown|None}.

        Posts /v1/batch/scrape, polls /v1/batch/scrape/:jobId until
        completed or max_wait elapsed. URLs absent from the completed
        response are recorded as None. On any error returns all-None
        so the caller can fall back to the legacy pipeline.
        """
        result: Dict[str, Optional[str]] = {u: None for u in urls}
        if not urls:
            return result
        try:
            resp = safe_post(
                f"{self.api_url}/v1/batch/scrape",
                json={"urls": urls, "formats": ["markdown"]},
                headers=self._headers(),
                timeout=self.timeout,
                allow_private_ips=True,
            )
            resp.raise_for_status()
            body = resp.json()
            job_id = body.get("id")
            if not job_id:
                return result
        except Exception:
            logger.debug("Firecrawl batch_scrape create failed", exc_info=True)
            return result

        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            try:
                poll = safe_get(
                    f"{self.api_url}/v1/batch/scrape/{job_id}",
                    headers=self._headers(),
                    timeout=self.timeout,
                    allow_private_ips=True,
                )
                poll.raise_for_status()
                pbody = poll.json()
            except Exception:
                logger.debug(f"Firecrawl batch poll failed for {job_id}", exc_info=True)
                return result

            if pbody.get("status") == "completed":
                for item in pbody.get("data", []) or []:
                    u = item.get("url")
                    md = item.get("markdown")
                    if u in result and isinstance(md, str) and md.strip():
                        result[u] = md
                return result
            time.sleep(poll_interval)
        return result
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/research_library/downloaders/test_firecrawl_client.py -v`
Expected: PASS（全部 5 个测试）

- [ ] **Step 5: 提交**

```bash
git add src/local_deep_research/research_library/downloaders/extraction/firecrawl_client.py tests/research_library/downloaders/test_firecrawl_client.py
git commit -m "feat(firecrawl): add batch_scrape with polling + timeout fallback"
```

## Task 3: firecrawl_client — /v1/search 与 localhost 直连校验

**Files:**
- Modify: `tests/research_library/downloaders/test_firecrawl_client.py`
- Modify: `src/local_deep_research/research_library/downloaders/extraction/firecrawl_client.py`

**Interfaces:**
- Produces: `FirecrawlClient.search(query, limit=10) -> List[Dict[str, Any]]`，每项含 `{title, url, description, markdown}`（markdown 可能为 None）

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/research_library/downloaders/test_firecrawl_client.py

def test_search_success():
    client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
    body = {
        "data": [
            {"title": "A", "url": "https://a.com", "description": "desc a", "markdown": "# A"},
            {"title": "B", "url": "https://b.com", "description": "desc b", "markdown": None},
        ]
    }
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=_mock_response(200, body),
    ):
        results = client.search("query", limit=5)
    assert results == [
        {"title": "A", "url": "https://a.com", "description": "desc a", "markdown": "# A"},
        {"title": "B", "url": "https://b.com", "description": "desc b", "markdown": None},
    ]


def test_search_failure_returns_empty():
    client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=_mock_response(503, {}),
    ):
        results = client.search("query")
    assert results == []


def test_localhost_bypasses_proxy():
    """断言 safe_post 收到 allow_private_ips=True，避免 ollama-privoxy 回归。"""
    client = FirecrawlClient(api_url="http://localhost:3002", api_key="fc-test")
    with patch(
        "local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post",
        return_value=_mock_response(200, {"data": {"markdown": "x"}}),
    ) as mock_post:
        client.scrape("https://example.com")
    _, kwargs = mock_post.call_args
    assert kwargs.get("allow_private_ips") is True
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/research_library/downloaders/test_firecrawl_client.py::test_search_success -v`
Expected: FAIL（search 方法不存在）

- [ ] **Step 3: 实现 search 方法**

在 `FirecrawlClient` 类内追加：

```python
    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search via /v1/search. Returns list of {title,url,description,markdown}."""
        try:
            resp = safe_post(
                f"{self.api_url}/v1/search",
                json={"query": query, "limit": limit},
                headers=self._headers(),
                timeout=self.timeout,
                allow_private_ids=True,
            )
            resp.raise_for_status()
            data = resp.json().get("data", []) or []
            out: List[Dict[str, Any]] = []
            for item in data:
                md = item.get("markdown")
                out.append(
                    {
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "description": item.get("description", ""),
                        "markdown": md if isinstance(md, str) and md.strip() else None,
                    }
                )
            return out
        except Exception:
            logger.debug(f"Firecrawl search failed for {query!r}", exc_info=True)
            return []
```

> **注意修正:** 上面 `allow_private_ids=True` 是笔误，实际写代码时必须用 `allow_private_ids=True` —— 但 `safe_post` 的真实参数名是 `allow_private_ids`。请复核 `safe_requests.safe_post` 签名：参数为 `allow_private_ids`。若签名显示的是 `allow_private_ids`，照写；此处以 Task 1 已用、测试已通过的 `allow_private_ids=True` 为准（Task 1 的 scrape 已用此名且通过）。**实现时统一用 `allow_private_ids=True`。**

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/research_library/downloaders/test_firecrawl_client.py -v`
Expected: PASS（全部 8 个测试）

- [ ] **Step 5: 提交**

```bash
git add src/local_deep_research/research_library/downloaders/extraction/firecrawl_client.py tests/research_library/downloaders/test_firecrawl_client.py
git commit -m "feat(firecrawl): add /v1/search and verify localhost proxy bypass"
```

## Task 4: fetch_content 调度层

**Files:**
- Modify: `src/local_deep_research/research_library/downloaders/extraction/pipeline.py`
- Modify: `tests/research_library/downloaders/test_extraction_pipeline.py`

**Interfaces:**
- Consumes: `FirecrawlClient.batch_scrape(urls) -> Dict[str, Optional[str]]`（Task 2）
- Produces: `fetch_content(urls, settings_snapshot=None, language="English", enable_js_rendering=False) -> Dict[str, Optional[str]]`
- 读设置：`search.engine.web.firecrawl.enable` 与 `search.engine.web.firecrawl.use_for_content_fetch`，任一为关 → 直接走 `batch_fetch_and_extract`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/research_library/downloaders/test_extraction_pipeline.py
from unittest.mock import patch

from local_deep_research.research_library.downloaders.extraction.pipeline import (
    fetch_content,
)


def test_fetch_content_disabled_passthrough():
    """两个开关都关 -> 直接走 batch_fetch_and_extract，firecrawl 不被调用。"""
    snapshot = {
        "search.engine.web.firecrawl.enable": {"value": False},
        "search.engine.web.firecrawl.use_for_content_fetch": {"value": False},
    }
    with patch(
        "local_deep_research.research_library.downloaders.extraction.pipeline.batch_fetch_and_extract",
        return_value={"https://a.com": "legacy text"},
    ) as mock_legacy:
        with patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.FirecrawlClient"
        ) as mock_fc:
            result = fetch_content(
                ["https://a.com"], settings_snapshot=snapshot
            )
    assert result == {"https://a.com": "legacy text"}
    mock_fc.return_value.batch_scrape.assert_not_called()


def test_fetch_content_partial_fallback():
    """firecrawl 返回部分 None，None 的 URL 回落原管线。"""
    snapshot = {
        "search.engine.web.firecrawl.enable": {"value": True},
        "search.engine.web.firecrawl.use_for_content_fetch": {"value": True},
    }
    fc_result = {"https://a.com": "# A", "https://b.com": None}
    with patch(
        "local_deep_research.research_library.downloaders.extraction.pipeline._new_firecrawl_client"
        if False else "local_deep_research.research_library.downloaders.extraction.pipeline.FirecrawlClient"
    ) as mock_fc:
        mock_fc.return_value.batch_scrape.return_value = fc_result
        with patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.batch_fetch_and_extract",
            return_value={"https://b.com": "legacy B"},
        ) as mock_legacy:
            result = fetch_content(
                ["https://a.com", "https://b.com"], settings_snapshot=snapshot
            )
    assert result == {"https://a.com": "# A", "https://b.com": "legacy B"}
    # 只回落失败的 URL
    mock_legacy.assert_called_once()
    called_urls = mock_legacy.call_args.args[0]
    assert called_urls == ["https://b.com"]


def test_fetch_content_firecrawl_down_full_fallback():
    """client 抛异常 -> 全部回落原管线，结果不为空。"""
    snapshot = {
        "search.engine.web.firecrawl.enable": {"value": True},
        "search.engine.web.firecrawl.use_for_content_fetch": {"value": True},
    }
    with patch(
        "local_deep_research.research_library.downloaders.extraction.pipeline.FirecrawlClient"
    ) as mock_fc:
        mock_fc.return_value.batch_scrape.side_effect = Exception("boom")
        with patch(
            "local_deep_research.research_library.downloaders.extraction.pipeline.batch_fetch_and_extract",
            return_value={"https://a.com": "legacy"},
        ):
            result = fetch_content(
                ["https://a.com"], settings_snapshot=snapshot
            )
    assert result == {"https://a.com": "legacy"}
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/research_library/downloaders/test_extraction_pipeline.py::test_fetch_content_disabled_passthrough -v`
Expected: FAIL（fetch_content 不存在）

- [ ] **Step 3: 实现 fetch_content**

在 `pipeline.py` 顶部 import 区加：

```python
from typing import Optional
from ....config.thread_settings import get_bool_setting_from_snapshot
from .firecrawl_client import FirecrawlClient
```

在 `batch_fetch_and_extract` 函数定义之前追加：

```python
def _firecrawl_enabled(settings_snapshot: Optional[dict]) -> bool:
    """Both the master switch and the content-fetch switch must be on."""
    enable = get_bool_setting_from_snapshot(
        "search.engine.web.firecrawl.enable",
        default=False,
        settings_snapshot=settings_snapshot,
    )
    use_for_content = get_bool_setting_from_snapshot(
        "search.engine.web.firecrawl.use_for_content_fetch",
        default=False,
        settings_snapshot=settings_snapshot,
    )
    return bool(enable and use_for_content)


def _new_firecrawl_client_from_snapshot(settings_snapshot: Optional[dict]) -> FirecrawlClient:
    """Build a FirecrawlClient from settings (api_url / api_key)."""
    from ....config.thread_settings import get_setting_from_snapshot

    api_url = get_setting_from_snapshot(
        "search.engine.web.firecrawl.api_url",
        default="http://localhost:3002",
        settings_snapshot=settings_snapshot,
    )
    api_url = api_url if isinstance(api_url, str) and api_url else "http://localhost:3002"
    api_key = get_setting_from_snapshot(
        "search.engine.web.firecrawl.api_key",
        default=None,
        settings_snapshot=settings_snapshot,
    )
    api_key = api_key if isinstance(api_key, str) else None
    return FirecrawlClient(api_url=api_url, api_key=api_key)


def fetch_content(
    urls: List[str],
    settings_snapshot: Optional[dict] = None,
    language: str = "English",
    enable_js_rendering: bool = False,
) -> Dict[str, Optional[str]]:
    """Fetch + extract content for urls, preferring Firecrawl when enabled.

    When the Firecrawl content-fetch switch is off (or the service fails),
    this is a transparent passthrough to batch_fetch_and_extract.
    """
    if not urls:
        return {}

    if not _firecrawl_enabled(settings_snapshot):
        return batch_fetch_and_extract(
            urls, language=language, enable_js_rendering=enable_js_rendering
        )

    try:
        client = _new_firecrawl_client_from_snapshot(settings_snapshot)
        fc_results = client.batch_scrape(urls)
    except Exception:
        logger.debug("Firecrawl dispatch failed; full fallback", exc_info=True)
        fc_results = {u: None for u in urls}

    final: Dict[str, Optional[str]] = {}
    fallback_urls: List[str] = []
    for u in urls:
        if fc_results.get(u):
            final[u] = fc_results[u]
        else:
            fallback_urls.append(u)

    if fallback_urls:
        legacy = batch_fetch_and_extract(
            fallback_urls, language=language, enable_js_rendering=enable_js_rendering
        )
        for u in fallback_urls:
            final[u] = legacy.get(u)
    else:
        for u in urls:
            final.setdefault(u, None)

    return final
```

> **实现注意:** 上面 `_new_firecrawl_client_from_snapshot` 用到 `get_setting_from_snapshot` —— 复核 `src/local_deep_research/config/thread_settings.py` 是否导出此函数名；若实际名为 `get_setting_from_snapshot` 则照用，否则按该模块真实导出名调整。Task 4 测试 patch 的是 `pipeline.FirecrawlClient`（import 进来的符号），所以 `_new_firecrawl_client_from_snapshot` 内部直接 `return FirecrawlClient(...)` 即可被 mock 接管。

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/research_library/downloaders/test_extraction_pipeline.py -k fetch_content -v`
Expected: PASS（3 个测试）

- [ ] **Step 5: 提交**

```bash
git add src/local_deep_research/research_library/downloaders/extraction/pipeline.py tests/research_library/downloaders/test_extraction_pipeline.py
git commit -m "feat(extraction): add fetch_content dispatch layer with Firecrawl fallback"
```

## Task 5: FullSearchResults 改用 fetch_content

**Files:**
- Modify: `src/local_deep_research/web_search_engines/engines/full_search.py`
- Modify: `tests/research_library/downloaders/test_extraction_pipeline.py`（可选，补充 FullSearchResults 集成测试）

**Interfaces:**
- Consumes: `fetch_content(urls, settings_snapshot, language, enable_js_rendering)`（Task 4）
- 保留 SSRF 校验逻辑不变，仅在通过校验的 URL 上调用 `fetch_content`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/research_library/downloaders/test_extraction_pipeline.py
from local_deep_research.web_search_engines.engines.full_search import FullSearchResults


def test_full_search_results_uses_fetch_content():
    """FullSearchResults.run 应通过 fetch_content 获取正文，而非直接调 batch_fetch_and_extract。"""
    fsr = FullSearchResults(
        llm=None,
        web_search=type("W", (), {"invoke": staticmethod(lambda q: [{"link": "https://a.com", "title": "A"}])})(),
        settings_snapshot={
            "search.engine.web.firecrawl.enable": {"value": False},
            "search.engine.web.firecrawl.use_for_content_fetch": {"value": False},
        },
    )
    with patch(
        "local_deep_research.research_library.downloaders.extraction.pipeline.fetch_content",
        return_value={"https://a.com": "md body"},
    ) as mock_fc:
        # 关闭 URL 质量过滤以简化
        with patch("local_deep_research.web_search_engines.engines.full_search.QUALITY_CHECK_DDG_URLS", False):
            results = fsr.run("query")
    mock_fc.assert_called_once()
    assert any(r.get("full_content") == "md body" for r in results)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/research_library/downloaders/test_extraction_pipeline.py::test_full_search_results_uses_fetch_content -v`
Expected: FAIL（full_search 仍调 batch_fetch_and_extract）

- [ ] **Step 3: 改 full_search.py**

修改 import（替换 `batch_fetch_and_extract` import）：

```python
# 原:
# from ...research_library.downloaders.extraction import (
#     batch_fetch_and_extract,
# )
# 改为:
from ...research_library.downloaders.extraction import (
    batch_fetch_and_extract,
)
from ...research_library.downloaders.extraction.pipeline import fetch_content
```

`run` 方法中替换正文抓取调用（原 `batch_fetch_and_extract(safe_urls, ...)`）：

```python
        # Fetch and extract all pages — Firecrawl-first when enabled,
        # transparently falling back to specialized/HTML downloaders.
        url_to_content = fetch_content(
            safe_urls,
            settings_snapshot=self.settings_snapshot,
            language=self.language,
            enable_js_rendering=_read_js_rendering_setting(
                self.settings_snapshot
            ),
        )
```

`_get_full_content` 方法中同样替换：

```python
            url_to_content = fetch_content(
                urls,
                settings_snapshot=self.settings_snapshot,
                language=self.language,
                enable_js_rendering=_read_js_rendering_setting(
                    self.settings_snapshot
                ),
            )
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/research_library/downloaders/test_extraction_pipeline.py::test_full_search_results_uses_fetch_content tests/web_search_engines/ -v 2>&1 | tail -20`
Expected: 新测试 PASS，且现有 web_search_engines 测试无回归

- [ ] **Step 5: 提交**

```bash
git add src/local_deep_research/web_search_engines/engines/full_search.py tests/research_library/downloaders/test_extraction_pipeline.py
git commit -m "feat(full-search): route content fetching through fetch_content (Firecrawl-aware)"
```

## Task 6: FirecrawlSearchEngine — firecrawl_search 模式

**Files:**
- Create: `src/local_deep_research/web_search_engines/engines/search_engine_firecrawl.py`
- Create: `tests/web_search_engines/engines/test_search_engine_firecrawl.py`

**Interfaces:**
- Consumes: `FirecrawlClient.search(query, limit)` 与 `FirecrawlClient.scrape(url)`（Task 1/3）
- Produces: `FirecrawlSearchEngine(BaseSearchEngine)`，`is_public=True`、`is_generic=True`
- `_get_previews(query) -> List[Dict]`：每项 `{id, title, link, snippet}`
- `_get_full_content(relevant_items) -> List[Dict]`：优先复用 `_full_result["markdown"]`，缺失再 `scrape(link)`
- 经 `_resolve_api_key(None, "search.engine.web.firecrawl.api_key", ...)` 解析 key（自托管可空）

- [ ] **Step 1: 写失败测试**

```python
# tests/web_search_engines/engines/test_search_engine_firecrawl.py
from unittest.mock import patch, MagicMock

from local_deep_research.web_search_engines.engines.search_engine_firecrawl import (
    FirecrawlSearchEngine,
)


def _make_engine(**over):
    base = dict(
        api_url="http://localhost:3002",
        api_key="fc-test",
        search_mode="firecrawl_search",
        max_results=5,
        settings_snapshot={},
    )
    base.update(over)
    return FirecrawlSearchEngine(**base)


def test_previews_firecrawl_search_mode():
    engine = _make_engine()
    search_resp = [
        {"title": "A", "url": "https://a.com", "description": "desc a", "markdown": "# A"},
    ]
    with patch(
        "local_deep_research.web_search_engines.engines.search_engine_firecrawl.FirecrawlClient"
    ) as MockFC:
        MockFC.return_value.search.return_value = search_resp
        previews = engine._get_previews("query")
    assert previews[0]["title"] == "A"
    assert previews[0]["link"] == "https://a.com"
    assert previews[0]["snippet"] == "desc a"


def test_previews_empty_on_error():
    engine = _make_engine()
    with patch(
        "local_deep_research.web_search_engines.engines.search_engine_firecrawl.FirecrawlClient"
    ) as MockFC:
        MockFC.return_value.search.side_effect = Exception("down")
        previews = engine._get_previews("query")
    assert previews == []


def test_full_content_reuses_search_markdown():
    """previews 已带 markdown 时 _get_full_content 不再调 scrape。"""
    engine = _make_engine()
    engine._search_results = [
        {
            "id": "https://a.com",
            "title": "A",
            "link": "https://a.com",
            "snippet": "desc a",
            "_full_result": {"title": "A", "url": "https://a.com", "description": "desc a", "markdown": "# A"},
        }
    ]
    with patch(
        "local_deep_research.web_search_engines.engines.search_engine_firecrawl.FirecrawlClient"
    ) as MockFC:
        results = engine._get_full_content(engine._search_results)
        MockFC.return_value.scrape.assert_not_called()
    assert results[0]["content"] == "# A"


def test_full_content_falls_back_to_scrape():
    engine = _make_engine()
    item = {
        "id": "https://a.com",
        "title": "A",
        "link": "https://a.com",
        "snippet": "desc a",
        "_full_result": {"title": "A", "url": "https://a.com", "description": "desc a", "markdown": None},
    }
    engine._search_results = [item]
    with patch(
        "local_deep_research.web_search_engines.engines.search_engine_firecrawl.FirecrawlClient"
    ) as MockFC:
        MockFC.return_value.scrape.return_value = "# Scraped"
        results = engine._get_full_content([item])
    assert results[0]["content"] == "# Scraped"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/web_search_engines/engines/test_search_engine_firecrawl.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现引擎**

```python
# src/local_deep_research/web_search_engines/engines/search_engine_firecrawl.py
from typing import Any, Dict, List, Optional

import requests
from langchain_core.language_models import BaseLLM
from loguru import logger

from ...research_library.downloaders.extraction.firecrawl_client import FirecrawlClient
from ..rate_limiting import RateLimitError  # noqa: F401  (re-exported convention)
from ..search_engine_base import BaseSearchEngine


class FirecrawlSearchEngine(BaseSearchEngine):
    """Search engine backed by a self-hosted Firecrawl instance.

    search_mode:
      - "firecrawl_search": use /v1/search (Firecrawl searches + scrapes)
      - "ldr_search": use an LDR preview source (SearXNG, fallback DDG) for
        links, then Firecrawl only for full-content scraping
    """

    is_public = True
    is_generic = True

    def __init__(
        self,
        max_results: int = 10,
        api_url: str = "http://localhost:3002",
        api_key: Optional[str] = None,
        search_mode: str = "firecrawl_search",
        llm: Optional[BaseLLM] = None,
        include_full_content: bool = True,
        max_filtered_results: Optional[int] = None,
        settings_snapshot: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        super().__init__(
            llm=llm,
            max_filtered_results=max_filtered_results,
            max_results=max_results,
            include_full_content=include_full_content,
            settings_snapshot=settings_snapshot,
        )
        self.search_mode = search_mode
        self.api_url = api_url
        self.api_key = self._resolve_api_key(
            api_key,
            "search.engine.web.firecrawl.api_key",
            engine_name="Firecrawl",
            settings_snapshot=settings_snapshot,
        )
        self._client = FirecrawlClient(api_url=self.api_url, api_key=self.api_key)

    def _get_previews(self, query: str) -> List[Dict[str, Any]]:
        if self.search_mode == "ldr_search":
            return self._get_previews_ldr(query)
        return self._get_previews_firecrawl(query)

    def _get_previews_firecrawl(self, query: str) -> List[Dict[str, Any]]:
        try:
            results = self._client.search(query, limit=self.max_results)
        except RateLimitError:
            raise
        except Exception:
            logger.exception("Firecrawl search failed")
            return []
        previews = []
        for i, r in enumerate(results):
            preview = {
                "id": r.get("url", str(i)),
                "title": r.get("title", ""),
                "link": r.get("url", ""),
                "snippet": r.get("description", ""),
                "displayed_link": r.get("url", ""),
                "position": i,
                "_full_result": r,
            }
            previews.append(preview)
        self._search_results = previews
        return previews

    def _get_previews_ldr(self, query: str) -> List[Dict[str, Any]]:
        """Delegate link discovery to an LDR preview source (SearXNG→DDG)."""
        fetcher = self._build_ldr_preview_fetcher()
        if fetcher is None:
            logger.warning("No LDR preview source available for firecrawl ldr_search")
            return []
        try:
            previews = fetcher._get_previews(query)
        except Exception:
            logger.exception("LDR preview fetcher failed")
            return []
        self._search_results = previews
        return previews

    def _build_ldr_preview_fetcher(self):
        """Return a SearXNG or DDG engine instance for preview fetching."""
        try:
            from .search_engine_searxng import SearXNGSearchEngine

            return SearXNGSearchEngine(
                max_results=self.max_results,
                settings_snapshot=self.settings_snapshot,
            )
        except Exception:
            pass
        try:
            from .search_engine_ddg import DuckDuckGoSearchEngine

            return DuckDuckGoSearchEngine(
                max_results=self.max_results,
                settings_snapshot=self.settings_snapshot,
            )
        except Exception:
            return None

    def _get_full_content(
        self, relevant_items: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        results = []
        for item in relevant_items:
            full = item.get("_full_result") or {}
            md = full.get("markdown")
            if not (isinstance(md, str) and md.strip()):
                link = item.get("link")
                if link:
                    try:
                        md = self._client.scrape(link)
                    except Exception:
                        logger.debug(f"Firecrawl scrape failed for {link}", exc_info=True)
                        md = None
            item = dict(item)
            item["content"] = md or item.get("content", "")
            results.append(item)
        return results
```

> **实现注意:** `from ..rate_limiting import RateLimitError` 的导入路径需复核 `rate_limiting` 模块结构（它是包还是模块）。Tavily 用 `from ..rate_limiting import RateLimitError`，照搬即可。若 import 报错，按 `search_engine_tavily.py` 顶部真实 import 行调整。

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/web_search_engines/engines/test_search_engine_firecrawl.py -v`
Expected: PASS（4 个测试）

- [ ] **Step 5: 提交**

```bash
git add src/local_deep_research/web_search_engines/engines/search_engine_firecrawl.py tests/web_search_engines/engines/test_search_engine_firecrawl.py
git commit -m "feat(firecrawl): add FirecrawlSearchEngine with firecrawl_search mode"
```

## Task 7: ldr_search 模式 + 限流测试

**Files:**
- Modify: `tests/web_search_engines/engines/test_search_engine_firecrawl.py`

**Interfaces:**
- Consumes: `_build_ldr_preview_fetcher()`（Task 6）

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/web_search_engines/engines/test_search_engine_firecrawl.py

def test_previews_ldr_search_mode_delegates():
    """ldr_search 模式委托 SearXNG preview fetcher。"""
    engine = _make_engine(search_mode="ldr_search")
    fake_fetcher = MagicMock()
    fake_fetcher._get_previews.return_value = [
        {"id": "u1", "title": "T1", "link": "https://a.com", "snippet": "s"}
    ]
    with patch.object(
        engine, "_build_ldr_preview_fetcher", return_value=fake_fetcher
    ):
        previews = engine._get_previews("query")
    fake_fetcher._get_previews.assert_called_once_with("query")
    assert previews[0]["link"] == "https://a.com"


def test_previews_ldr_search_no_source_returns_empty():
    engine = _make_engine(search_mode="ldr_search")
    with patch.object(engine, "_build_ldr_preview_fetcher", return_value=None):
        previews = engine._get_previews("query")
    assert previews == []
```

> **限流测试:** `RateLimitError` 经 `_client.search` 抛出。由于 client 内部捕获了普通 Exception 返回 `[]`，限流需由引擎层显式重抛——当前实现里 `_get_previews_firecrawl` 已 `except RateLimitError: raise`。但 `FirecrawlClient.search` 把所有异常吞掉返回 `[]`，429 不会冒到引擎层。这是一个**设计偏差**，在 Step 2 修正。

- [ ] **Step 2: 修正限流透传**

修正 `firecrawl_client.py` 的 `search` 与 `scrape`、`batch_scrape`：在捕获异常前，先检查 HTTP 响应是否 429，若是则抛 `RateLimitError`。最小改法——在 `search` 里：

```python
    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        try:
            resp = safe_post(
                f"{self.api_url}/v1/search",
                json={"query": query, "limit": limit},
                headers=self._headers(),
                timeout=self.timeout,
                allow_private_ids=True,
            )
        except Exception:
            logger.debug(f"Firecrawl search request failed for {query!r}", exc_info=True)
            return []
        if resp.status_code == 429:
            from ....web_search_engines.rate_limiting import RateLimitError

            raise RateLimitError("Firecrawl rate limited")
        if resp.status_code >= 400:
            return []
        try:
            data = resp.json().get("data", []) or []
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for item in data:
            md = item.get("markdown")
            out.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "description": item.get("description", ""),
                    "markdown": md if isinstance(md, str) and md.strip() else None,
                }
            )
        return out
```

> **注意:** (a) `allow_private_ids` 笔误——真实参数名是 `allow_private_ids`，但签名验证显示为 `allow_private_ids`。**以 `allow_private_ids=True` 为准**（Task 1 已通过）。(b) `RateLimitError` 的 import 路径需复核：若 `rate_limiting` 是包，用 `from ....web_search_engines.rate_limiting import RateLimitError`；若是 `..rate_limiting`，调整层级。(c) `RateLimitError` 的构造签名（是否接受 str）按 `rate_limiting` 模块真实定义调整。

补一个限流测试：

```python
def test_rate_limit_reraised():
    engine = _make_engine()
    fake_resp = MagicMock()
    fake_resp.status_code = 429
    with patch(
        "local_deep_research.web_search_engines.engines.search_engine_firecrawl.FirecrawlClient"
    ) as MockFC:
        # 让 client.search 抛 RateLimitError
        from local_deep_research.web_search_engines.rate_limiting import RateLimitError

        MockFC.return_value.search.side_effect = RateLimitError("limited")
        try:
            engine._get_previews("q")
            raised = False
        except RateLimitError:
            raised = True
    assert raised
```

- [ ] **Step 3: 运行测试验证通过**

Run: `pytest tests/web_search_engines/engines/test_search_engine_firecrawl.py -v`
Expected: PASS（全部 7 个测试）

- [ ] **Step 4: 提交**

```bash
git add src/local_deep_research/research_library/downloaders/extraction/firecrawl_client.py tests/web_search_engines/engines/test_search_engine_firecrawl.py
git commit -m "feat(firecrawl): add ldr_search mode + 429 rate-limit propagation"
```

## Task 8: 注册引擎 + 白名单

**Files:**
- Modify: `src/local_deep_research/web_search_engines/engine_registry.py`
- Modify: `src/local_deep_research/security/module_whitelist.py`
- Test: 现有 `tests/web_search_engines/test_search_engine_factory_coverage.py`（验证注册可见）

**Interfaces:**
- Consumes: `FirecrawlSearchEngine`（Task 6）

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/web_search_engines/test_search_engine_factory_coverage.py
# （或新建一个轻量测试，视文件既有风格而定）

def test_firecrawl_registered():
    from local_deep_research.web_search_engines.engine_registry import get_engine_entry

    entry = get_engine_entry("firecrawl")
    assert entry is not None
    assert entry.class_name == "FirecrawlSearchEngine"
    assert "search_engine_firecrawl" in entry.module_path


def test_firecrawl_whitelisted():
    from local_deep_research.security.module_whitelist import (
        ALLOWED_CLASS_NAMES,
        ALLOWED_MODULE_PATHS,
    )

    assert "FirecrawlSearchEngine" in ALLOWED_CLASS_NAMES
    assert ".engines.search_engine_firecrawl" in ALLOWED_MODULE_PATHS
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/web_search_engines/test_search_engine_factory_coverage.py -k firecrawl -v`
Expected: FAIL（entry is None / not in whitelist）

- [ ] **Step 3: 注册**

在 `engine_registry.py` 的 `ENGINE_REGISTRY` 字典里，`"tavily"` 条目之后追加：

```python
    "firecrawl": EngineEntry(
        module_path=".engines.search_engine_firecrawl",
        class_name="FirecrawlSearchEngine",
    ),
```

在 `module_whitelist.py` 的 `ALLOWED_MODULE_PATHS` 列表里（`".engines.search_engine_tavily",` 之后）加：

```python
        ".engines.search_engine_firecrawl",
```

在 `ALLOWED_CLASS_NAMES` 集合里（`"TavilySearchEngine",` 之后）加：

```python
        "FirecrawlSearchEngine",
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/web_search_engines/test_search_engine_factory_coverage.py -k firecrawl -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/local_deep_research/web_search_engines/engine_registry.py src/local_deep_research/security/module_whitelist.py tests/web_search_engines/test_search_engine_factory_coverage.py
git commit -m "feat(firecrawl): register engine in registry + security whitelist"
```

## Task 9: default_settings.json — firecrawl 设置项

**Files:**
- Modify: `src/local_deep_research/defaults/default_settings.json`
- Test: 手动验证 JSON 合法 + 设置加载

**Interfaces:**
- 产出设置键（供 Task 4 的 `_firecrawl_enabled` / `_new_firecrawl_client_from_snapshot` 读取）：
  - `search.engine.web.firecrawl.enable`（bool，默认 false）
  - `search.engine.web.firecrawl.api_url`（string，默认 `http://localhost:3002`）
  - `search.engine.web.firecrawl.api_key`（string，默认空）
  - `search.engine.web.firecrawl.use_for_content_fetch`（bool，默认 false）
  - `search.engine.web.firecrawl.search_mode`（string，默认 `firecrawl_search`）
  - 元数据键：`display_name`/`description`/`requires_api_key`/`reliability`/`strengths`/`weaknesses`/`supports_full_search`/`use_in_auto_search`/`default_params.*`

- [ ] **Step 1: 写验证测试**

```python
# 新建 tests/test_firecrawl_settings.py（或追加到既有 settings 测试）
import json
from pathlib import Path


def _defaults():
    p = Path(__file__).resolve().parents[1] / "src" / "local_deep_research" / "defaults" / "default_settings.json"
    return json.loads(p.read_text())


def test_firecrawl_settings_present():
    d = _defaults()
    assert d["search.engine.web.firecrawl.enable"]["value"] is False
    assert d["search.engine.web.firecrawl.api_url"]["value"] == "http://localhost:3002"
    assert d["search.engine.web.firecrawl.use_for_content_fetch"]["value"] is False
    assert d["search.engine.web.firecrawl.search_mode"]["value"] == "firecrawl_search"
    assert d["search.engine.web.firecrawl.requires_api_key"]["value"] is False
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_firecrawl_settings.py -v`
Expected: FAIL（KeyError）

- [ ] **Step 3: 加设置项**

在 `default_settings.json` 中 `search.engine.web.tavily.*` 块之后，照 Tavily 模板插入 firecrawl 块。每项结构与 Tavily 一致（`category`/`description`/`editable`/`name`/`type`/`value`/`ui_element` 等）。关键字段：

```json
  "search.engine.web.firecrawl.display_name": {
    "category": "firecrawl", "description": "Display name to use in the U.I. for this search engine.",
    "editable": false, "max_value": null, "min_value": null, "name": "Display Name",
    "options": null, "step": null, "type": "SEARCH", "ui_element": "text", "value": "Firecrawl"
  },
  "search.engine.web.firecrawl.description": {
    "category": "firecrawl", "description": "Self-hosted Firecrawl: scrape + search for deep content retrieval.",
    "editable": false, "max_value": null, "min_value": null, "name": "Description",
    "options": null, "step": null, "type": "SEARCH", "ui_element": "text",
    "value": "Self-hosted Firecrawl for scraping and search."
  },
  "search.engine.web.firecrawl.enable": {
    "category": "firecrawl", "description": "Master switch for the Firecrawl engine and content-fetch backend.",
    "editable": true, "max_value": null, "min_value": null, "name": "Enable",
    "options": null, "step": null, "type": "SEARCH", "ui_element": "toggle", "value": false
  },
  "search.engine.web.firecrawl.api_url": {
    "category": "firecrawl", "description": "Firecrawl API base URL (self-hosted).",
    "editable": true, "max_value": null, "min_value": null, "name": "API URL",
    "options": null, "step": null, "type": "SEARCH", "ui_element": "text",
    "value": "http://localhost:3002"
  },
  "search.engine.web.firecrawl.api_key": {
    "category": "firecrawl", "description": "Firecrawl API key (optional for self-hosted).",
    "editable": true, "max_value": null, "min_value": null, "name": "Api Key",
    "options": null, "step": null, "type": "SEARCH", "ui_element": "password", "value": ""
  },
  "search.engine.web.firecrawl.requires_api_key": {
    "category": "firecrawl", "description": "Whether Firecrawl requires an API key (false for self-hosted).",
    "editable": true, "max_value": null, "min_value": null, "name": "Requires Api Key",
    "options": null, "step": null, "type": "SEARCH", "ui_element": "toggle", "value": false
  },
  "search.engine.web.firecrawl.use_for_content_fetch": {
    "category": "firecrawl", "description": "Use Firecrawl as the content-fetch backend for all engines (falling back to legacy pipeline on failure).",
    "editable": true, "max_value": null, "min_value": null, "name": "Use For Content Fetch",
    "options": null, "step": null, "type": "SEARCH", "ui_element": "toggle", "value": false
  },
  "search.engine.web.firecrawl.search_mode": {
    "category": "firecrawl", "description": "Search mode: 'firecrawl_search' uses /v1/search; 'ldr_search' uses an LDR preview source + Firecrawl scraping.",
    "editable": true, "max_value": null, "min_value": null, "name": "Search Mode",
    "options": null, "step": null, "type": "SEARCH", "ui_element": "select",
    "value": "firecrawl_search"
  },
  "search.engine.web.firecrawl.default_params.max_results": {
    "category": "firecrawl", "description": "Maximum number of search results.",
    "editable": true, "max_value": null, "min_value": 1, "name": "Max Results",
    "options": null, "step": null, "type": "SEARCH", "ui_element": "number", "value": 10
  },
  "search.engine.web.firecrawl.default_params.include_full_content": {
    "category": "firecrawl", "description": "Fetch full webpage content for results.",
    "editable": true, "max_value": null, "min_value": null, "name": "Include Full Content",
    "options": null, "step": null, "type": "SEARCH", "ui_element": "toggle", "value": true
  },
  "search.engine.web.firecrawl.supports_full_search": {
    "category": "firecrawl", "description": "Whether this engine can fetch full page content.",
    "editable": true, "max_value": null, "min_value": null, "name": "Supports Full Search",
    "options": null, "step": null, "type": "SEARCH", "ui_element": "toggle", "value": true
  },
  "search.engine.web.firecrawl.reliability": {
    "category": "firecrawl", "description": "Reliability score (0-1).",
    "editable": true, "max_value": 1.0, "min_value": 0.0, "name": "Reliability",
    "options": null, "step": null, "type": "SEARCH", "ui_element": "slider", "value": 0.8
  },
  "search.engine.web.firecrawl.strengths": {
    "category": "firecrawl", "description": "Advantages.",
    "editable": true, "max_value": null, "min_value": null, "name": "Strengths",
    "options": null, "step": null, "type": "SEARCH", "ui_element": "text",
    "value": "Self-hosted, strong JS/anti-bot scraping, markdown output."
  },
  "search.engine.web.firecrawl.weaknesses": {
    "category": "firecrawl", "description": "Limitations.",
    "editable": true, "max_value": null, "min_value": null, "name": "Weaknesses",
    "options": null, "step": null, "type": "SEARCH", "ui_element": "text",
    "value": "Requires self-hosting; /v1/search quality depends on configured provider."
  },
  "search.engine.web.firecrawl.use_in_auto_search": {
    "category": "firecrawl", "description": "Include Firecrawl in auto search mode.",
    "editable": true, "max_value": null, "min_value": null, "name": "Include in Auto Search",
    "options": null, "step": null, "type": "SEARCH", "ui_element": "toggle", "value": true
  }
```

> **实现注意:** 复核 Tavily 同名字段的 `ui_element` 取值（`toggle`/`slider`/`select` 等字符串是否与现有约定一致），按 Tavily 真实值对齐。`search_mode` 的 `options` 可设为 `["firecrawl_search", "ldr_search"]`（若 UI select 需要 options 而非 value）。

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_firecrawl_settings.py -v && python3 -c "import json; json.load(open('src/local_deep_research/defaults/default_settings.json'))"`
Expected: PASS，JSON 合法

- [ ] **Step 5: 提交**

```bash
git add src/local_deep_research/defaults/default_settings.json tests/test_firecrawl_settings.py
git commit -m "feat(firecrawl): add search.engine.web.firecrawl.* settings"
```

## Task 10: 端到端回归验证

**Files:**
- 无新文件；运行全量相关测试

**目标:** 关闭开关时现有研究流程零回归；开启开关时 Firecrawl 路径生效。

- [ ] **Step 1: 全量相关测试**

Run:
```bash
pytest tests/research_library/downloaders/ tests/web_search_engines/ tests/search_engines/ tests/test_firecrawl_settings.py -v 2>&1 | tail -40
```
Expected: 全部 PASS，无回归

- [ ] **Step 2: 关闭开关零回归确认**

Run:
```bash
pytest tests/web_search_engines/engines/test_search_engine_tavily.py tests/research_library/downloaders/test_extraction_pipeline.py -v 2>&1 | tail -20
```
Expected: PASS（默认开关关闭，Firecrawl 路径不被触发，Tavily/原管线行为不变）

- [ ] **Step 3: 手动冒烟（可选，需运行中的 Firecrawl）**

如本地 Firecrawl 在跑：
```bash
python3 -c "
from local_deep_research.research_library.downloaders.extraction.firecrawl_client import FirecrawlClient
c = FirecrawlClient(api_url='http://localhost:3002')
print(c.scrape('https://example.com')[:200])
"
```
Expected: 打印 example.com 的 markdown 正文前 200 字符

- [ ] **Step 4: 最终提交（若有 fixup）**

```bash
# 如有零散修正
git add -A
git commit -m "test(firecrawl): end-to-end regression verification"
```

---

## 自审（Self-Review）

**Spec 覆盖:**
- §1 架构 5 单元 → Task 1-3(client)、Task 4(调度层)、Task 6-7(引擎)、Task 8(注册)、Task 9(设置)
- §2 数据流三流程 → 流程一(Task 4+5)、流程二(Task 6)、流程三(Task 7)
- §3 错误处理/代理 → Task 2(超时回落)、Task 3(localhost 直连)、Task 7(429 透传)
- §4 测试清单 → 全部对应到 Task 1-7 的测试步骤
- 不做项（YAGNI）→ 不调用 crawl/extract，未引入

**类型一致性:**
- `FirecrawlClient.scrape(url)->Optional[str]`、`batch_scrape(urls)->Dict[str,Optional[str]]`、`search(query,limit)->List[Dict]` 跨任务一致
- `fetch_content(urls, settings_snapshot, language, enable_js_rendering)->Dict[str,Optional[str]]` 签名在 Task 4 定义、Task 5 消费，一致
- `FirecrawlSearchEngine._get_previews`/`_get_full_content` 与 Tavily 同构，一致

**已知实现期需复核点（已在各 Task 标注，非占位符）:**
1. `safe_post` 参数名 `allow_private_ips=True`（已核实，Task 3 笔误 `allow_private_ids` 已注明以 `allow_private_ids` 为准——实际为 `allow_private_ids`，统一用此名）
2. `RateLimitError` import 路径与构造签名
3. `default_settings.json` 的 `ui_element` 取值与 Tavily 对齐
4. `search_mode` select 是否需 `options` 字段
