# 暗网检索 阶段一：连接探测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 `probe_darkweb` 四级探测能力，并通过设置页按钮与研究前 preflight 两个入口暴露，用以判定暗网检索是否具备落地前提。

**Architecture:** 在既有 `diagnostics/engine_health.py` 中新增单一探测函数，复用其 `get_searxng_engines()` 与 `probe_searxng_engine()`；REST 端点与 preflight 共用该函数，不重复实现探测逻辑。SearXNG 侧引擎块以模板形式入库，由人工合入被 gitignore 的 `settings.yml`。

**Tech Stack:** Python 3.12（宿主 venv）/ 3.14（容器）、pytest、Flask、requests、SearXNG、Tor

## Global Constraints

- 分支：所有提交落在 `main`。每次提交前运行 `git rev-parse --abbrev-ref HEAD` 确认；不是 `main` 则停止。
- **禁止重启 `ldr-local` 容器**（`docker restart` / `docker compose up --force-recreate` / `down`）。其日志是研究任务的唯一证据来源，重建即永久销毁。需要代码生效时向用户报告并等待批准。
- 重启 `searxng-ldr` 是允许的，它不影响 ldr-local 日志。
- 探测超时固定 60 秒，显著高于其他引擎 —— Tor 首次建链慢是固有特性而非故障。
- 所有新增用户可见文案走 `report.language` 本地化机制，不硬编码中文。
- 测试命令一律使用 `.venv/bin/python -m pytest`，不使用系统 python。
- **阶段一具有否决权**：Task 5 的实测若拿不到 `.onion` 结果，停止并向用户报告，不得继续阶段二、三。

---

## 本阶段不做（明确划界）

- **设置页的「测试连接」按钮前端**：本阶段只做 REST 端点。按钮需要挂在暗网设置
  区块旁，而该区块的其余配置项（`display_name`、`reliability`、
  `default_params.*`）属于阶段二。阶段一通过 `curl` 与单测驱动端点即可完成
  否决判定，不必为此提前引入前端改动。
- 引擎注册、研究页勾选框、检索流程改动 → 阶段二
- `.onion` 判据、`[D1]` 编号、参考文献分组、图片管道防护 → 阶段三

---

## File Structure

| 文件 | 职责 |
|---|---|
| `src/local_deep_research/diagnostics/engine_health.py`（修改） | 新增 `DARKWEB_ENGINES`、`probe_darkweb()`；接入 `run_preflight_check()` |
| `tests/diagnostics/test_darkweb_probe.py`（新建） | `probe_darkweb` 四级分支的单测 |
| `src/local_deep_research/web/routes/settings_routes.py`（修改） | 新增 `POST /settings/api/test-darkweb` 端点 |
| `tests/web/test_darkweb_test_endpoint.py`（新建） | 端点的响应结构与错误路径 |
| `searxng/engines-darkweb.yml.template`（新建） | SearXNG 引擎块模板，入库 |
| `docs/darkweb-searxng-setup.md`（新建） | 合入模板与重启 searxng-ldr 的操作说明 |

---

### Task 1: `probe_darkweb` 四级探测

**Files:**
- Modify: `src/local_deep_research/diagnostics/engine_health.py`
- Test: `tests/diagnostics/test_darkweb_probe.py`

**Interfaces:**
- Consumes（均已存在于同文件）：
  - `EngineStatus(name: str, status: str, detail: str = "", latency_ms: int = 0, kind: str = "searxng")`，`status` ∈ `"ok" | "error" | "timeout" | "skipped"`
  - `_get_searxng_url(settings_snapshot: Optional[dict]) -> str`
  - `get_searxng_engines(instance_url: str, timeout: int = _PROBE_TIMEOUT) -> list[str]`
- Produces（后续 Task 依赖）：
  - `DARKWEB_ENGINES: tuple[str, ...]`，值为 `("ahmia", "torch")`
  - `probe_darkweb(settings_snapshot: Optional[dict] = None, timeout: int = 60) -> EngineStatus`
    - `name` 恒为 `"darkweb"`，`kind` 恒为 `"darkweb"`
    - `detail` 以 `"L1:" | "L2:" | "L3:" | "L4:"` 开头，标明到达的级别

- [ ] **Step 1: 写失败测试**

创建 `tests/diagnostics/test_darkweb_probe.py`：

```python
"""probe_darkweb 的四级下钻诊断。

暗网检索有三个彼此独立的前提：SearXNG 在跑、ahmia/torch 已合入其
settings.yml、Tor 线路能真的取回 .onion 结果。任何一个不满足，症状都
表现为"检索无结果"。四级探测把这三者拆开，让失败直接指向症结。
"""
from unittest.mock import patch

from local_deep_research.diagnostics.engine_health import (
    DARKWEB_ENGINES,
    probe_darkweb,
)


def test_darkweb_engines_are_ahmia_and_torch():
    assert DARKWEB_ENGINES == ("ahmia", "torch")


def test_l1_searxng_unreachable():
    """SearXNG 本身没起来 —— 后三级无从谈起。"""
    with patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_engines",
        side_effect=OSError("connection refused"),
    ):
        st = probe_darkweb()
    assert st.name == "darkweb"
    assert st.kind == "darkweb"
    assert st.status == "error"
    assert st.detail.startswith("L1:")


def test_l2_engine_block_not_merged():
    """SearXNG 活着但引擎列表里没有 ahmia/torch —— 模板未合入。"""
    with patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_engines",
        return_value=["google", "wikipedia"],
    ):
        st = probe_darkweb()
    assert st.status == "error"
    assert st.detail.startswith("L2:")
    assert "ahmia" in st.detail


def test_l3_no_onion_results():
    """引擎已配置但查不到 .onion —— Tor 线路不通或引擎超时。"""
    from local_deep_research.diagnostics.engine_health import EngineStatus

    with patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_engines",
        return_value=["ahmia", "torch", "google"],
    ), patch(
        "local_deep_research.diagnostics.engine_health._darkweb_onion_hits",
        return_value=(0, EngineStatus("ahmia", "ok")),
    ):
        st = probe_darkweb()
    assert st.status == "error"
    assert st.detail.startswith("L3:")


def test_l4_ok_reports_hits_and_latency():
    """全通 —— 报告命中数与耗时,供人判断是否值得开启。"""
    from local_deep_research.diagnostics.engine_health import EngineStatus

    with patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_engines",
        return_value=["ahmia", "torch"],
    ), patch(
        "local_deep_research.diagnostics.engine_health._darkweb_onion_hits",
        return_value=(7, EngineStatus("ahmia", "ok", latency_ms=4200)),
    ):
        st = probe_darkweb()
    assert st.status == "ok"
    assert st.detail.startswith("L4:")
    assert "7" in st.detail


def test_never_raises_on_unexpected_error():
    """preflight 依赖它绝不抛异常,否则会拖垮整个研究启动。"""
    with patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_engines",
        side_effect=RuntimeError("boom"),
    ):
        st = probe_darkweb()
    assert st.status == "error"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/diagnostics/test_darkweb_probe.py -q`
Expected: FAIL，`ImportError: cannot import name 'DARKWEB_ENGINES'`

- [ ] **Step 3: 实现**

在 `src/local_deep_research/diagnostics/engine_health.py` 中，`probe_firecrawl` 定义之后追加：

```python
# 暗网引擎。名称必须与 searxng/settings.yml 中的 `name:` 完全一致。
DARKWEB_ENGINES: tuple[str, ...] = ("ahmia", "torch")

# Tor 首次建链常需数十秒,远慢于明网引擎。这不是故障。
_DARKWEB_TIMEOUT = 60


def _darkweb_onion_hits(
    instance_url: str, timeout: int
) -> tuple[int, EngineStatus]:
    """发一次真实查询,返回 (.onion 结果数, 底层探测状态)。

    单独成函数是为了让 L3 在测试中可被替换,而不必打桩整个 HTTP 层。
    """
    status = probe_searxng_engine(
        instance_url, DARKWEB_ENGINES[0], timeout=timeout
    )
    if status.status != "ok":
        return 0, status
    params = {
        "q": "market",
        "engines": ",".join(DARKWEB_ENGINES),
        "format": "json",
        "categories": "onions",
        "pageno": 1,
    }
    resp = requests.get(
        f"{instance_url}/search", params=params, timeout=timeout
    )
    resp.raise_for_status()
    results = resp.json().get("results") or []
    hits = sum(
        1
        for r in results
        if ".onion" in str(r.get("url") or "").lower()
    )
    return hits, status


def probe_darkweb(
    settings_snapshot: Optional[dict] = None,
    timeout: int = _DARKWEB_TIMEOUT,
) -> EngineStatus:
    """四级下钻诊断暗网检索的前提条件。

    L1 SearXNG 可达 → L2 引擎已合入 → L3 能取回 .onion → L4 记录耗时。
    失败即返回,detail 前缀标明到达的级别,使症结一眼可辨。绝不抛异常。
    """
    start = time.monotonic()
    try:
        instance_url = _get_searxng_url(settings_snapshot)
        # L1
        try:
            available = get_searxng_engines(instance_url, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            return EngineStatus(
                "darkweb",
                "error",
                f"L1: SearXNG 不可达 ({instance_url}) — {str(e)[:60]}",
                kind="darkweb",
            )
        # L2
        missing = [e for e in DARKWEB_ENGINES if e not in available]
        if missing:
            return EngineStatus(
                "darkweb",
                "error",
                (
                    f"L2: SearXNG 未启用 {'/'.join(missing)} — "
                    "引擎块尚未合入 searxng/settings.yml"
                ),
                kind="darkweb",
            )
        # L3
        hits, inner = _darkweb_onion_hits(instance_url, timeout)
        if hits == 0:
            return EngineStatus(
                "darkweb",
                "error",
                (
                    "L3: 未取回任何 .onion 结果 — "
                    f"Tor 线路不通或引擎超时 ({inner.detail or inner.status})"
                ),
                kind="darkweb",
            )
        # L4
        elapsed = int((time.monotonic() - start) * 1000)
        return EngineStatus(
            "darkweb",
            "ok",
            f"L4: 取回 {hits} 条 .onion 结果",
            latency_ms=elapsed,
            kind="darkweb",
        )
    except Exception as e:  # noqa: BLE001
        return EngineStatus(
            "darkweb", "error", f"探测异常: {str(e)[:70]}", kind="darkweb"
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/diagnostics/test_darkweb_probe.py -q`
Expected: PASS（6 项）

- [ ] **Step 5: 回归 + lint**

Run: `.venv/bin/python -m pytest tests/diagnostics/ -q`
Expected: 全部通过

Run: `.venv/bin/ruff check src/local_deep_research/diagnostics/engine_health.py tests/diagnostics/test_darkweb_probe.py`
Expected: `All checks passed!`。若报错，先用 `git show HEAD:<file> | .venv/bin/ruff check --stdin-filename <file> -` 确认是否既有问题；既有的不要修。

- [ ] **Step 6: 提交**

```bash
git rev-parse --abbrev-ref HEAD   # 必须输出 main
git add src/local_deep_research/diagnostics/engine_health.py tests/diagnostics/test_darkweb_probe.py
git commit -m "feat(diagnostics): four-level darkweb connectivity probe"
git log --oneline -3
```

---

### Task 2: SearXNG 引擎模板与操作文档

**Files:**
- Create: `searxng/engines-darkweb.yml.template`
- Create: `docs/darkweb-searxng-setup.md`

**Interfaces:**
- Consumes: `DARKWEB_ENGINES`（引擎 `name:` 必须与之逐字一致，否则 Task 1 的 L2 会误报）
- Produces: 无代码接口；供 Task 5 人工合入

- [ ] **Step 1: 写模板**

创建 `searxng/engines-darkweb.yml.template`：

```yaml
# 暗网检索引擎块 —— 合入 searxng/settings.yml 的 `engines:` 段落
#
# name 必须与 diagnostics/engine_health.py 的 DARKWEB_ENGINES 逐字一致,
# 否则连接测试的 L2 级会误报"引擎未合入"。
#
# socks5h 的 h 表示由 Tor 做 DNS 解析 —— .onion 域名无法经普通 DNS 解析,
# 用 socks5（无 h）会直接失败。
  - name: ahmia
    engine: ahmia
    using_tor_proxy: true
    timeout: 40.0
    proxies:
      all://: socks5h://ldr-tor:9050

  - name: torch
    engine: xpath
    using_tor_proxy: true
    timeout: 40.0
    paging: true
    proxies:
      all://: socks5h://ldr-tor:9050
    search_url: http://xmh57jrknzkhv6y3ls3ubitzfqnkrwxhopf5aygthi7d6rplyvk3noyd.onion/cgi-bin/omega/omega?P={query}&DEFAULTOP=and
    results_xpath: //table//tr
    url_xpath: ./td[2]/a
    title_xpath: ./td[2]/b
    content_xpath: ./td[2]/small
```

- [ ] **Step 2: 写操作文档**

创建 `docs/darkweb-searxng-setup.md`：

```markdown
# 启用暗网检索（SearXNG 侧配置）

LDR 的开关只控制"要不要去查暗网引擎"。引擎本身必须先在 SearXNG 中启用 ——
这一步在 LDR 之外，需人工操作。

## 背景

`searxng/settings.yml` 是宿主机绑定挂载（`searxng/` → 容器 `/etc/searxng`），
且被 `.gitignore` 排除，因此模板入库、实际配置不入库。

## 步骤

1. 备份现有配置：

       cp searxng/settings.yml searxng/settings.yml.bak.$(date +%s)

2. 把 `searxng/engines-darkweb.yml.template` 的内容追加到 `settings.yml`
   的 `engines:` 段落末尾，保持 YAML 缩进一致（条目为 2 空格缩进的 `- name:`）。

3. 重启 SearXNG（**只重启 searxng-ldr，不要动 ldr-local**，后者的日志是研究
   任务的唯一证据来源）：

       docker compose -f docker-compose.searxng-ldr.yml restart searxng-ldr

4. 验证：在 LDR 设置页点击「测试暗网连接」，或运行连接探测。期望到达 L4。

## 排错

| 探测结果 | 含义 | 处理 |
|---|---|---|
| L1 | SearXNG 未运行 | `docker ps` 查 searxng-ldr 状态 |
| L2 | 引擎块未生效 | 检查 YAML 缩进；确认已重启 searxng-ldr |
| L3 | 取不到 .onion 结果 | Tor 线路问题，见下 |
| L4 | 正常 | — |

L3 常见成因：`ldr-tor` 的 Tor 从未建立过线路。用
`docker logs ldr-tor | grep Heartbeat` 查看，若持续显示
`0 kB sent / 0 kB received` 且 `0 circuits open`，说明 Tor 未真正出网。
注意 `ldr-tor` 的 torrc 把自身出口挂在 Privoxy 之后
（`HTTPSProxy 172.25.128.1:10888`），这条链路本身也需验证。
```

- [ ] **Step 3: 校验模板 YAML 合法**

Run:
```bash
.venv/bin/python -c "
import yaml
d=yaml.safe_load(open('searxng/engines-darkweb.yml.template'))
names=[e['name'] for e in d]
print('engines:', names)
assert names==['ahmia','torch'], names
print('YAML OK')
"
```
Expected: 输出 `engines: ['ahmia', 'torch']` 与 `YAML OK`

- [ ] **Step 4: 校验模板与代码常量一致**

Run:
```bash
.venv/bin/python -c "
import yaml
from local_deep_research.diagnostics.engine_health import DARKWEB_ENGINES
d=yaml.safe_load(open('searxng/engines-darkweb.yml.template'))
assert tuple(e['name'] for e in d)==DARKWEB_ENGINES
print('模板与 DARKWEB_ENGINES 一致')
"
```
Expected: `模板与 DARKWEB_ENGINES 一致`

- [ ] **Step 5: 确认模板未被 gitignore 排除**

Run: `git check-ignore -v searxng/engines-darkweb.yml.template || echo "未被忽略,可入库"`
Expected: 输出 `未被忽略,可入库`。若被忽略，在 `.gitignore` 中该规则下方加 `!searxng/*.template`。

- [ ] **Step 6: 提交**

```bash
git rev-parse --abbrev-ref HEAD   # 必须输出 main
git add searxng/engines-darkweb.yml.template docs/darkweb-searxng-setup.md
git commit -m "docs(darkweb): SearXNG engine template + setup guide"
git log --oneline -3
```

---

### Task 3: 设置页测试端点

**Files:**
- Modify: `src/local_deep_research/web/routes/settings_routes.py`
- Test: `tests/web/test_darkweb_test_endpoint.py`

**Interfaces:**
- Consumes: `probe_darkweb(settings_snapshot=None, timeout=60) -> EngineStatus`（Task 1）
- Produces: `POST /settings/api/test-darkweb`，响应 JSON
  `{"status": "ok"|"error", "detail": str, "latency_ms": int}`，HTTP 恒为 200

- [ ] **Step 1: 写失败测试**

创建 `tests/web/test_darkweb_test_endpoint.py`：

```python
"""设置页「测试暗网连接」端点。

端点在探测失败时仍返回 HTTP 200 —— 探测结果本身就是要展示的内容,
失败不是请求错误。前端据 status 字段渲染成功/失败。
"""
from unittest.mock import patch

from local_deep_research.diagnostics.engine_health import EngineStatus


def test_endpoint_returns_probe_result(client, auth_headers):
    with patch(
        "local_deep_research.web.routes.settings_routes.probe_darkweb",
        return_value=EngineStatus(
            "darkweb", "ok", "L4: 取回 7 条 .onion 结果",
            latency_ms=4200, kind="darkweb",
        ),
    ):
        resp = client.post(
            "/settings/api/test-darkweb", headers=auth_headers
        )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["detail"].startswith("L4:")
    assert body["latency_ms"] == 4200


def test_endpoint_reports_failure_without_http_error(client, auth_headers):
    """探测失败仍是 200 —— 失败详情是正常响应内容,不是请求错误。"""
    with patch(
        "local_deep_research.web.routes.settings_routes.probe_darkweb",
        return_value=EngineStatus(
            "darkweb", "error",
            "L2: SearXNG 未启用 ahmia/torch — 引擎块尚未合入 searxng/settings.yml",
            kind="darkweb",
        ),
    ):
        resp = client.post(
            "/settings/api/test-darkweb", headers=auth_headers
        )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "error"
    assert "L2:" in body["detail"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/web/test_darkweb_test_endpoint.py -q`
Expected: FAIL（404 或 `AttributeError: probe_darkweb`）

若 `client` / `auth_headers` fixture 不存在，先运行
`grep -rn "def client\|def auth_headers" tests/web/conftest.py tests/conftest.py` 找到实际
fixture 名并改用之；不要新建认证脚手架。

- [ ] **Step 3: 实现端点**

在 `settings_routes.py` 顶部导入区加入：

```python
from ...diagnostics.engine_health import probe_darkweb
```

在文件中已有 `api_get_categories` 之后追加。装饰器照下面写 —— 本端点不查数据库，
因此不需要 `@with_user_session`（相邻端点带它是因为它们要 `db_session`）：

```python
@settings_bp.route("/api/test-darkweb", methods=["POST"])
@login_required
def api_test_darkweb():
    """运行暗网连接四级探测并返回结构化结果。

    探测失败仍返回 200：失败详情是要展示给用户的正常内容。
    """
    status = probe_darkweb()
    return jsonify(
        {
            "status": status.status,
            "detail": status.detail,
            "latency_ms": status.latency_ms,
        }
    )
```

`jsonify` 与 `login_required` 在该文件中均已导入，无需新增 import。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/web/test_darkweb_test_endpoint.py -q`
Expected: PASS（2 项）

- [ ] **Step 5: 回归 + lint**

Run: `.venv/bin/python -m pytest tests/web/ -q --timeout=120`
Expected: 与改动前失败集相同。若有新增失败，用
`git stash push -- src/local_deep_research/web/routes/settings_routes.py` 比对干净树，
确认是否既有失败；**不要用 `git stash -u`**（会因 `searxng/` 权限问题失败）。

Run: `.venv/bin/ruff check src/local_deep_research/web/routes/settings_routes.py tests/web/test_darkweb_test_endpoint.py`

- [ ] **Step 6: 提交**

```bash
git rev-parse --abbrev-ref HEAD   # 必须输出 main
git add src/local_deep_research/web/routes/settings_routes.py tests/web/test_darkweb_test_endpoint.py
git commit -m "feat(settings): darkweb connectivity test endpoint"
git log --oneline -3
```

---

### Task 4: 接入研究前 preflight

**Files:**
- Modify: `src/local_deep_research/defaults/default_settings.json`
- Modify: `src/local_deep_research/diagnostics/engine_health.py`（`run_preflight_check`）
- Test: `tests/diagnostics/test_darkweb_probe.py`（追加）

**Interfaces:**
- Consumes: `probe_darkweb`（Task 1）、`run_preflight_check(settings_snapshot=None) -> list[EngineStatus]`
- Produces:
  - 设置键 `search.engine.web.darkweb.enabled`，布尔，默认 `false`
  - `run_preflight_check` 的返回列表中，当且仅当该键为真时包含一个
    `name="darkweb"` 的条目

> 设置键在本任务创建而非单独成任务：它唯一的消费者就是这里的开关判断，
> 拆开会让两个任务都无法独立验证。引擎的其余配置项（`display_name`、
> `reliability`、`default_params.*`）属于阶段二，此处不要提前加入。

- [ ] **Step 1: 新增设置键**

在 `src/local_deep_research/defaults/default_settings.json` 中加入（保持文件既有的
键名字典序位置，紧邻其他 `search.engine.web.*` 条目）：

```json
    "search.engine.web.darkweb.enabled": {
        "category": "darkweb",
        "description": "启用暗网检索（需先在 SearXNG 中合入 ahmia/torch 引擎块，见 docs/darkweb-searxng-setup.md）",
        "editable": true,
        "max_value": null,
        "min_value": null,
        "name": "启用暗网检索",
        "options": null,
        "step": null,
        "type": "SEARCH",
        "ui_element": "checkbox",
        "value": false,
        "visible": true
    },
```

- [ ] **Step 2: 校验 JSON 合法且默认为关**

Run:
```bash
.venv/bin/python -c "
import json
d=json.load(open('src/local_deep_research/defaults/default_settings.json'))
e=d['search.engine.web.darkweb.enabled']
assert e['value'] is False, e['value']
assert e['ui_element']=='checkbox'
print('设置键 OK,默认关闭')
"
```
Expected: `设置键 OK,默认关闭`

- [ ] **Step 3: 写失败测试**

追加到 `tests/diagnostics/test_darkweb_probe.py`：

```python
def test_preflight_skips_darkweb_when_disabled():
    """开关关闭时不应付出 60 秒探测代价。"""
    from local_deep_research.diagnostics.engine_health import (
        run_preflight_check,
    )

    with patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_engines",
        return_value=[],
    ), patch(
        "local_deep_research.diagnostics.engine_health.probe_proxy"
    ), patch(
        "local_deep_research.diagnostics.engine_health.probe_firecrawl"
    ), patch(
        "local_deep_research.diagnostics.engine_health.probe_darkweb"
    ) as pd:
        statuses = run_preflight_check(
            {"search.engine.web.darkweb.enabled": {"value": False}}
        )

    pd.assert_not_called()
    assert not [s for s in statuses if s.name == "darkweb"]


def test_preflight_includes_darkweb_when_enabled():
    from local_deep_research.diagnostics.engine_health import (
        EngineStatus,
        run_preflight_check,
    )

    with patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_engines",
        return_value=[],
    ), patch(
        "local_deep_research.diagnostics.engine_health.probe_proxy"
    ), patch(
        "local_deep_research.diagnostics.engine_health.probe_firecrawl"
    ), patch(
        "local_deep_research.diagnostics.engine_health.probe_darkweb",
        return_value=EngineStatus(
            "darkweb", "ok", "L4: 取回 3 条 .onion 结果", kind="darkweb"
        ),
    ):
        statuses = run_preflight_check(
            {"search.engine.web.darkweb.enabled": {"value": True}}
        )

    assert [s for s in statuses if s.name == "darkweb"]
```

- [ ] **Step 4: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/diagnostics/test_darkweb_probe.py -q -k preflight`
Expected: FAIL —— `pd.assert_not_called()` 通过但第二个测试找不到 darkweb 条目

- [ ] **Step 5: 实现**

在 `run_preflight_check` 内，`fc_future = pool.submit(probe_firecrawl, settings_snapshot)` 之后追加：

```python
        # 暗网探测仅在开关开启时执行 —— 它的超时是 60s,远高于其他引擎,
        # 关闭状态下不应让研究启动为此等待。
        darkweb_enabled = get_setting_from_snapshot(
            "search.engine.web.darkweb.enabled",
            default=False,
            settings_snapshot=settings_snapshot,
        )
        darkweb_future = (
            pool.submit(probe_darkweb, settings_snapshot)
            if darkweb_enabled
            else None
        )
```

在收集结果处（`statuses.append` firecrawl 结果的相邻位置）追加：

```python
        if darkweb_future is not None:
            try:
                statuses.append(darkweb_future.result(timeout=_DARKWEB_TIMEOUT + 5))
            except FutureTimeout:
                statuses.append(
                    EngineStatus(
                        "darkweb", "timeout", "探测超时", kind="darkweb"
                    )
                )
            except Exception as e:  # noqa: BLE001
                statuses.append(
                    EngineStatus(
                        "darkweb", "error", str(e)[:80], kind="darkweb"
                    )
                )
```

- [ ] **Step 6: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/diagnostics/test_darkweb_probe.py -q`
Expected: PASS（8 项）

- [ ] **Step 7: 回归 + lint**

Run: `.venv/bin/python -m pytest tests/diagnostics/ -q`

Run: `.venv/bin/ruff check src/local_deep_research/diagnostics/engine_health.py`

- [ ] **Step 8: 提交**

```bash
git rev-parse --abbrev-ref HEAD   # 必须输出 main
git add src/local_deep_research/defaults/default_settings.json src/local_deep_research/diagnostics/engine_health.py tests/diagnostics/test_darkweb_probe.py
git commit -m "feat(diagnostics): run darkweb probe in preflight when enabled"
git log --oneline -3
```

---

### Task 5: 实机验证与否决判定

**Files:** 无代码改动。本任务产出的是一个决策。

**Interfaces:**
- Consumes: Task 1–4 的全部产出、Task 2 的操作文档

- [ ] **Step 1: 合入引擎块**

按 `docs/darkweb-searxng-setup.md` 操作：备份 → 追加模板 → 重启 **searxng-ldr**。

**不得重启 ldr-local。**

- [ ] **Step 2: 确认引擎已被 SearXNG 注册**

Run:
```bash
docker exec searxng-ldr sh -c 'grep -c "name: ahmia\|name: torch" /etc/searxng/settings.yml'
```
Expected: `2`

- [ ] **Step 3: 跑真实探测**

Run:
```bash
docker exec -i ldr-local python -c "
from local_deep_research.diagnostics.engine_health import probe_darkweb
st = probe_darkweb()
print('status :', st.status)
print('detail :', st.detail)
print('latency:', st.latency_ms, 'ms')
"
```

- [ ] **Step 4: 判定**

| 结果 | 动作 |
|---|---|
| `L4` 且 `status=ok` | 阶段一通过。向用户报告命中数与耗时，请示是否进入阶段二 |
| `L1` / `L2` | 配置问题。按文档排错表处理后重试 Step 3 |
| **`L3`** | **触发否决权。** 停止，向用户报告 Tor 链路不通，**不得开始阶段二、三** |

L3 时一并附上诊断依据：

```bash
docker logs ldr-tor 2>&1 | grep -o "I've sent [0-9]* kB and received [0-9]* kB" | tail -3
```

- [ ] **Step 5: 记录结论**

把实测结果（status / detail / latency / 判定）追加到
`docs/superpowers/specs/2026-08-14-darkweb-search-engine-design.md` 末尾新增的
「阶段一实测结论」小节，并提交：

```bash
git rev-parse --abbrev-ref HEAD   # 必须输出 main
git add docs/superpowers/specs/2026-08-14-darkweb-search-engine-design.md
git commit -m "docs(darkweb): record phase-one connectivity verdict"
git log --oneline -3
```

---

## 阶段一完成标准

- [ ] `probe_darkweb` 四级分支各有单测覆盖，且绝不抛异常
- [ ] 设置页端点在成功与失败下均返回 200 与结构化结果
- [ ] preflight 在开关关闭时不调用探测（不付出 60s 代价）
- [ ] 模板 `name` 与 `DARKWEB_ENGINES` 有自动一致性校验
- [ ] 实机探测结论已记录，并据此作出继续/放弃的决定
