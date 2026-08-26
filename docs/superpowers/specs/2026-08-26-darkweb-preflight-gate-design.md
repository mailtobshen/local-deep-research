# 暗网 preflight 探测 gate 修复 — 设计

日期：2026-08-26
状态：待实施

## 背景

`run_preflight_check` (`src/local_deep_research/diagnostics/engine_health.py:807`)
中的暗网探测 gate 仅检查**全局 admin 开关** `search.engine.web.darkweb.enabled`
（lines 863-872）。当全局开关为开、但用户在新建研究表单中**没有勾选**
`include_darkweb`、且**没有把搜索引擎下拉框**设为"暗网 (Tor)"时，preflight
仍会探测 ahmia/torch 等暗网引擎，造成两类问题：

1. 暗网探测超时长（每个引擎最坏 60s），白白拖慢研究启动
2. 用户在表单上没有勾选暗网，preflight 表里却出现暗网探测结果，
   视觉上与用户意图不一致

UI 侧同时存在对称问题：全局开关关闭时，`<div id="darkweb-container">`
直接 `display: none` 隐藏整个控件 (`research.html:229`)；用户既看不到
暗网选项，也无法感知到「全局开关已关闭」这一事实。前端隐藏与后端跳过
不对称，导致调试时容易混淆。

## 目标

1. 后端：`run_preflight_check` 在「用户没有请求暗网」时，跳过暗网探测，
   返回 `skipped` 状态的 `EngineStatus`（与 `probe_firecrawl` /
   `probe_proxy` 的 self-skip 模式一致）
2. 前端：`include_darkweb` 勾选框在全局开关关闭时**显示但 disabled 灰显**，
   与后端"不探测"语义对齐

## 非目标

- 不修改 `probe_ldr_tor_proxy` 的 gate（用户已确认保持现状：仅当
  `search.tool == "darkweb"` 即探测）
- 不修改 `_apply_darkweb_override` 的 fail-closed 行为
- 不改变暗网引擎的实际探测超时/失败策略
- 不引入新的 settings 字段

## 关键前提（已实测确认）

| 事实 | 影响 |
|---|---|
| `_apply_darkweb_override` (`research_routes.py:216-269`) 已把 `include_darkweb=False` 编码为 `snapshot["search.engine.web.darkweb.enabled"]["value"]=False` | 无需新增 per-research 字段；snapshot 已携带信号 |
| `_apply_darkweb_override` 在 `search_engine=="darkweb"` 且全局关闭时**故意保留 False**（fail-closed） | preflight 必须额外读取 `search.tool`，否则会误跳过用户主动选暗网的情况 |
| `EngineStatus(kind="darkweb")` 已存在 | `_darkweb_skipped_status` 直接复用 |
| `format_status_table` (`engine_health.py:973`) 已把 `skipped` 视为非 active | `active_count = sum(1 for s in statuses if s.status != "skipped")`，无需调整展示逻辑 |
| `probe_ldr_tor_proxy` (`engine_health.py:391-504`) 已同时检查 `search.tool` 和 `darkweb.enabled` | preflight 的 `probe_darkweb` gate 与之对称即可 |

## 架构

### 1. 后端：`run_preflight_check` 的暗网 gate

把 `engine_health.py:863-872` 当前逻辑：

```python
darkweb_enabled = get_bool_setting_from_snapshot(
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

改为三信号 OR gate：

```python
primary_tool = (
    get_setting_from_snapshot(
        "search.tool", "searxng", settings_snapshot=settings_snapshot
    )
    if settings_snapshot
    else "searxng"
)
darkweb_enabled = get_bool_setting_from_snapshot(
    "search.engine.web.darkweb.enabled",
    default=False,
    settings_snapshot=settings_snapshot,
)
# 三信号 OR: 全局开关 OR 主引擎下拉框选了暗网。
# (`include_darkweb` 已被 _apply_darkweb_override 编码进
# snapshot.darkweb.enabled, 这里只需 OR 上 search.tool)
darkweb_requested = darkweb_enabled or primary_tool == "darkweb"
darkweb_future = pool.submit(
    probe_darkweb if darkweb_requested else _darkweb_skipped_status,
    settings_snapshot,
)
```

跳过时**返回 skipped 列表**而不是 `None`，确保 `format_status_table`
能渲染一行 `darkweb/<engine>  skipped  暗网未启用`，
与 `probe_firecrawl` / `probe_proxy` 的处理对称。

### 2. 后端：新增 `_darkweb_skipped_status` 函数

在 `engine_health.py:614` `DARKWEB_ENGINES` 常量附近新增：

```python
def _darkweb_skipped_status(
    settings_snapshot: Optional[dict] = None,
) -> list[EngineStatus]:
    """未请求暗网时, 返回一行 skipped EngineStatus。

    与 probe_firecrawl / probe_proxy 的 self-skip 模式一致。
    让 preflight 表里能看到"为什么没探测"。
    """
    engines = _darkweb_engine_list(settings_snapshot)
    return [
        EngineStatus(
            f"darkweb/{name}",
            "skipped",
            "暗网未启用",
            kind="darkweb",
        )
        for name in engines
    ]
```

`_darkweb_engine_list` 复用现有实现（line 620-635），无需新增。

### 3. 前端：`darkweb-status` fetch 的 UI 同步

`research.js:331-339` 当前逻辑：

```javascript
fetch('/settings/api/darkweb-status', { credentials: 'same-origin' })
    .then(r => r.json())
    .then(data => {
        const c = document.getElementById('darkweb-container');
        if (c && data && data.enabled) {
            c.style.display = '';
        }
    })
    .catch(() => { /* leave hidden */ });
```

改为：

```javascript
fetch('/settings/api/darkweb-status', { credentials: 'same-origin' })
    .then(r => r.json())
    .then(data => {
        const c = document.getElementById('darkweb-container');
        const cb = document.getElementById('include_darkweb');
        if (!c) return;
        // 始终显示 container, 让用户看到暗网选项存在
        c.style.display = '';
        if (data && data.enabled) {
            // 全局开关开 → checkbox 可用
            if (cb) cb.disabled = false;
        } else {
            // 全局开关关 → checkbox disabled + 取消勾选 + 灰显
            if (cb) {
                cb.disabled = true;
                cb.checked = false;
            }
        }
    })
    .catch(() => { /* leave hidden */ });
```

HTML template (`research.html:229`) 保留 `style="display: none;"`
初始值（无 JS 时 container 仍隐藏），由 JS 在 fetch 完成后**强制显示**。
灰显样式由现有的 `.ldr-form-check-input:disabled` CSS 处理，
无需新增 CSS 规则。

## 行为矩阵

修复前后端到端对照表（基于 `run_research_process` 的 snapshot 路径，
已应用 `_apply_darkweb_override` 之后的 snapshot）：

| 全局开关 | search.tool | include_darkweb | snapshot.darkweb.enabled | 当前行为 (preflight) | 修复后行为 (preflight) |
|---|---|---|---|---|---|
| 开 | searxng | 未勾 | False (override 写入) | **探测 (bug)** | **skipped** |
| 开 | searxng | 勾 | True | 探测 | 探测 |
| 开 | darkweb | (强制未勾) | True (fail-open, 全局已开) | 探测 | 探测 |
| 关 | searxng | 未勾 | False | 不探测 (None) | skipped (统一展示) |
| 关 | searxng | 勾 | False (fail-closed) | 不探测 (None) | skipped |
| **关** | **darkweb** | **(强制未勾)** | **False (fail-closed, 保留)** | **不探测 (None) (bug)** | **探测 (search.tool 信号触发)** |

用户原始报告场景是**第一行**：全局开 + 用户未勾 include_darkweb。
当前 `darkweb_enabled=True`（来自 snapshot），preflight 会探测；
期望是 skipped。

第六行是另一个 bug：`search.tool == "darkweb"` 表示用户主动选了
暗网作为主引擎，但当前 preflight 仅读 `darkweb_enabled`，
`fail-closed` 后 snapshot 为 False → 不探测 → 用户看不到 Tor 不可用
的诊断。修复后 `primary_tool == "darkweb"` 触发探测。

`_apply_darkweb_override` 已经正确地把"全局开 + 用户未勾"编码为
`snapshot.darkweb.enabled.value=False`，也正确地把
"`search_engine='darkweb'` + 全局关" 保留为 False（fail-closed）。
preflight gate 只需把 `search.tool == "darkweb"` 也作为探测触发
条件 — 这样 snapshot 的两个语义边角都被覆盖。

## 不修改的内容

- `_apply_darkweb_override` (`research_routes.py:216-269`)：
  已经正确编码 `include_darkweb` → snapshot.darkweb.enabled
- `probe_ldr_tor_proxy` (`engine_health.py:391-504`)：
  用户已确认保持现状
- `probe_darkweb` 函数本身 (`engine_health.py:708-804`)：
  探测逻辑不变
- `_darkweb_engine_list` (`engine_health.py:620-635`)：
  引擎列表读取逻辑不变
- HTML template (`research.html:229`)：
  初始 `display: none` 保留，由 JS 强制显示
- CSS：现有的 `:disabled` 灰显样式已足够

## 改动范围总结

| 文件 | 类型 | 改动 |
|---|---|---|
| `src/local_deep_research/diagnostics/engine_health.py` | 修 | `run_preflight_check` 的 darkweb gate 改成三信号 OR；新增 `_darkweb_skipped_status` 函数 |
| `src/local_deep_research/web/static/js/components/research.js` | 修 | `darkweb-status` fetch 改为始终显示 container + 按 enabled 切换 checkbox disabled |
| `tests/diagnostics/test_darkweb_probe.py` | 改 | 增加 3 个测试用例 |
| `changelog.d/+darkweb-preflight-gate.bugfix.md` | 增 | 新增 changelog |

## 测试

### 现有测试需更新

`tests/diagnostics/test_darkweb_probe.py::test_preflight_skips_darkweb_when_disabled`
当前断言 `probe_darkweb` **未被调用**且 status 列表中**没有 darkweb 条目**。
修复后跳过时改为返回 `_darkweb_skipped_status` 列表（含 skipped 行），
需要把断言改为"未被调用 + status 列表含 `skipped` 的 darkweb 条目"。

### 新增 3 个测试用例

#### `test_preflight_skips_darkweb_when_include_darkweb_false_with_global_on`

- 输入：`settings_snapshot={"search.engine.web.darkweb.enabled": {"value": False}}`，
  `search.tool="searxng"`
- 预期：`probe_darkweb` **未被调用**，
  `run_preflight_check` 返回的 status 列表含
  `darkweb/<engine>  skipped  暗网未启用`
- 这是用户原始报告的场景：全局开 → `_apply_darkweb_override`
  把 `include_darkweb=False` 写为 snapshot.darkweb.enabled=False →
  preflight 跳过

#### `test_preflight_probes_darkweb_when_primary_engine_selected`

- 输入：`settings_snapshot={"search.engine.web.darkweb.enabled": {"value": False}}`，
  `search.tool="darkweb"`
- 预期：`probe_darkweb` **被调用**（mock 它返回正常 list）
- 这是 fail-closed 的边界：全局关，但用户主动选暗网下拉框，
  preflight 仍应探测（让用户看到 Tor 不可用的诊断）

#### `test_preflight_skips_darkweb_when_global_off_and_default_state`

- 输入：`settings_snapshot={"search.engine.web.darkweb.enabled": {"value": False}}`，
  `search.tool="searxng"`（默认）
- 预期：`probe_darkweb` **未被调用**，返回 skipped 列表
- 这是矩阵第四行：全局关 + 默认状态，行为从"不返回 darkweb 条目"
  改为"返回 skipped 行"，**展示更明确**

每个测试用 `unittest.mock.patch` 替换 `probe_darkweb`，
并断言它是否被调用。

### 前端测试

不新增前端自动化测试（项目无前端测试基础设施）。
UI 行为通过手动检查 `/research` 页面验证：
1. 全局暗网开关 ON → checkbox 可用，可勾选
2. 全局暗网开关 OFF → checkbox disabled 灰显，无法勾选

## 风险与回滚

| 风险 | 缓解 |
|---|---|
| 现有 `test_preflight_skips_darkweb_when_disabled` (在 test_darkweb_probe.py 中) 假设 `darkweb_future=None` 时 status 列表里**没有** darkweb 条目 | 该测试需要相应更新：修复后跳过时返回 skipped 列表，列表非空 |
| `format_status_table` 对 skipped 行的渲染 | 已验证 (`engine_health.py:973`)，skipped 行不计入 `active_count` 但仍展示 |
| 前端把 container 从 `display:none` 改为可见，可能影响布局 | container 在表单中预留了位置 (`research.html:229`)，可见不会破坏布局 |
