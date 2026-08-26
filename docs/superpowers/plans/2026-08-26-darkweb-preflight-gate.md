# 暗网 preflight 探测 gate 修复 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 `run_preflight_check` 的暗网探测 gate：当用户没有请求暗网时，跳过暗网探测（返回 skipped 状态），并把前端勾选框在全局开关关闭时改为 disabled 灰显。

**Architecture:**
- 后端：在 `engine_health.py` 的 `run_preflight_check` 把 darkweb gate 改为三信号 OR（`search.tool == "darkweb"` OR `snapshot.darkweb.enabled`），跳过时改为返回 skipped EngineStatus 列表（与 `probe_firecrawl`/`probe_proxy` 一致），新增 `_darkweb_skipped_status` 函数。
- 前端：`research.js` 中 `darkweb-status` fetch 改为始终显示 container，按 enabled 切换 checkbox disabled。
- 测试：更新 `test_preflight_skips_darkweb_when_disabled`，新增 3 个测试覆盖三信号 OR 行为。

**Tech Stack:** Python 3.x stdlib + existing `pytest` + `unittest.mock.patch`; Vanilla JavaScript (无前端测试基础设施)。

## Global Constraints

- Hot-mount source changes require `docker restart ldr-local` 才生效（CLAUDE.md 已记录）
- 不修改 `_apply_darkweb_override`（已经正确编码 include_darkweb → snapshot）
- 不修改 `probe_ldr_tor_proxy`（用户已确认保持现状）
- 不修改 `_darkweb_engine_list`、`probe_darkweb` 函数本身
- 不修改 HTML template 的初始 `display: none`（无 JS 时 container 仍隐藏）
- CSS 灰显由现有 `:disabled` 样式处理，不新增 CSS
- 不修改 `changelog.d/README.md`（只加新的 changelog fragment 文件）
- Commit message style: `fix(scope):` for fixes, `feat(scope):` for new functions, `test(scope):` for tests
- 实施前先 `git rev-parse --abbrev-ref HEAD` 确认在 main 上
- 提交命令必须前台运行（CLAUDE.md git 规则）

## File Structure

| File | Type | Responsibility |
|---|---|---|
| `src/local_deep_research/diagnostics/engine_health.py` | MODIFY | 修改 `run_preflight_check` 的 darkweb gate；新增 `_darkweb_skipped_status` |
| `src/local_deep_research/web/static/js/components/research.js` | MODIFY | `darkweb-status` fetch 改为始终显示 container + checkbox disabled 切换 |
| `tests/diagnostics/test_darkweb_probe.py` | MODIFY | 更新 1 个测试 + 新增 3 个测试 |
| `changelog.d/+darkweb-preflight-gate.bugfix.md` | NEW | 新增 changelog fragment |

---

## Task 1: Update existing test + add new tests for the three-signal OR gate

**Files:**
- Modify: `tests/diagnostics/test_darkweb_probe.py:177-198` (existing `test_preflight_skips_darkweb_when_disabled`)
- Create: 3 new tests appended to the same file

**Interfaces:**
- Reads: `run_preflight_check(settings_snapshot)` — existing function in `engine_health.py:807`
- Patches: `probe_darkweb` (to verify call/no-call), `_darkweb_skipped_status` (will be added in Task 2)

- [ ] **Step 1: Write failing tests — update existing + add 3 new**

Replace `tests/diagnostics/test_darkweb_probe.py:177-198` (the existing
`test_preflight_skips_darkweb_when_disabled` function) with:

```python
def test_preflight_skips_darkweb_when_disabled():
    """开关关闭时不应付出 60 秒探测代价。

    修复后跳过时返回 skipped 列表 (而非 None), 让 preflight 表里
    能看到"为什么没探测"。
    """
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
    # 修复后: 跳过时返回 skipped 列表 (每引擎一行)
    darkweb_statuses = [s for s in statuses if s.kind == "darkweb"]
    assert all(s.status == "skipped" for s in darkweb_statuses)
    assert any("暗网未启用" in s.detail for s in darkweb_statuses)
```

Append these 3 new tests after the existing tests (just before the end of file):

```python
def test_preflight_skips_darkweb_when_include_darkweb_false_with_global_on():
    """全局开 + 用户未勾 include_darkweb → skipped (用户原始报告场景)。

    _apply_darkweb_override 已把 include_darkweb=False 编码为
    snapshot.darkweb.enabled.value=False, 所以 preflight 应跳过。
    """
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
            {
                "search.engine.web.darkweb.enabled": {"value": False},
                "search.tool": {"value": "searxng"},
            }
        )

    pd.assert_not_called()
    darkweb_statuses = [s for s in statuses if s.kind == "darkweb"]
    assert all(s.status == "skipped" for s in darkweb_statuses)


def test_preflight_probes_darkweb_when_primary_engine_selected():
    """全局关 + search.tool='darkweb' → 探测 (用户主动选暗网下拉框)。

    fail-closed 边界: snapshot.darkweb.enabled=False (来自 _apply_darkweb_override
    的 fail-closed 行为), 但 search.tool='darkweb' 表示用户主动选了,
    preflight 必须探测 (让用户看到 Tor 不可用的诊断)。
    """
    from local_deep_research.diagnostics.engine_health import (
        EngineStatus,
        run_preflight_check,
    )

    fake_statuses = [
        EngineStatus("darkweb/ahmia", "ok", "ok", kind="darkweb"),
    ]

    with patch(
        "local_deep_research.diagnostics.engine_health.get_searxng_engines",
        return_value=[],
    ), patch(
        "local_deep_research.diagnostics.engine_health.probe_proxy"
    ), patch(
        "local_deep_research.diagnostics.engine_health.probe_firecrawl"
    ), patch(
        "local_deep_research.diagnostics.engine_health.probe_darkweb",
        return_value=fake_statuses,
    ) as pd:
        statuses = run_preflight_check(
            {
                "search.engine.web.darkweb.enabled": {"value": False},
                "search.tool": {"value": "darkweb"},
            }
        )

    pd.assert_called_once()
    # darkweb status 应来自 probe_darkweb 的返回值, 而非 skipped
    darkweb_statuses = [s for s in statuses if s.kind == "darkweb"]
    assert darkweb_statuses == fake_statuses


def test_preflight_skips_darkweb_when_global_off_and_default_state():
    """全局关 + search.tool='searxng' (默认) → skipped (矩阵第四行)。

    行为从"不返回 darkweb 条目"改为"返回 skipped 行", 展示更明确。
    """
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
    darkweb_statuses = [s for s in statuses if s.kind == "darkweb"]
    assert all(s.status == "skipped" for s in darkweb_statuses)
```

- [ ] **Step 2: Run tests to verify they fail**

Run from repo root:

```bash
pytest tests/diagnostics/test_darkweb_probe.py -v
```

Expected: The 4 tests above FAIL with one of:
- `AttributeError: module 'local_deep_research.diagnostics.engine_health' has no attribute '_darkweb_skipped_status'`
- `assert not [s for s in statuses if s.kind == "darkweb"]` (existing test) — but updated assertion passes if function exists
- Mock `probe_darkweb` called (because the new code hasn't been written yet)

The exact error doesn't matter — what matters is **at least one of the 4 tests fails**.

- [ ] **Step 3: Commit**

```bash
git add tests/diagnostics/test_darkweb_probe.py
git commit -m "test(preflight): darkweb gate 三信号 OR — 更新 1 + 新增 3

为三信号 OR gate 添加测试覆盖:
- 全局开 + include_darkweb=False → skipped (用户报告场景)
- 全局关 + search.tool='darkweb' → 探测 (fail-closed 边界)
- 全局关 + 默认 search.tool → skipped

更新 test_preflight_skips_darkweb_when_disabled 断言
从"无 darkweb 条目"改为"全 skipped 且 detail 含 '暗网未启用'"。"

git rev-parse --abbrev-ref HEAD  # 确认在 main 上
git log --oneline -1             # 确认 commit 在 main
```

## Task 2: Implement `_darkweb_skipped_status` and update `run_preflight_check` gate

**Files:**
- Modify: `src/local_deep_research/diagnostics/engine_health.py:863-872` (the darkweb gate in `run_preflight_check`)
- Modify: `src/local_deep_research/diagnostics/engine_health.py:614` area (add `_darkweb_skipped_status` near `_darkweb_engine_list`)

**Interfaces:**
- Produces: `def _darkweb_skipped_status(settings_snapshot: Optional[dict] = None) -> list[EngineStatus]` — returns one `EngineStatus(name="darkweb/<engine>", status="skipped", detail="暗网未启用", kind="darkweb")` per engine in `_darkweb_engine_list(settings_snapshot)`
- Consumes: `_darkweb_engine_list(settings_snapshot)` (existing helper at line 620)
- Modifies: `run_preflight_check` (line 807) — the darkweb gate currently at lines 863-872

- [ ] **Step 1: Add `_darkweb_skipped_status` function**

Insert this function **immediately after** `_darkweb_engine_list` ends
(currently at line 635, after the `return engines` line in `_darkweb_engine_list`).
The exact location in `engine_health.py` is after line 635 and before the
next blank line + function definition.

Insert:

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

- [ ] **Step 2: Update the darkweb gate in `run_preflight_check`**

Replace the existing block at `engine_health.py:863-872`:

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

with:

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

**Note**: `darkweb_future` no longer needs the `is None` branch
because the future is always submitted. If the existing code after this
block does `if darkweb_future is not None:` (line 873+), it must still
work because the future is now always defined. Verify by reading
`engine_health.py:873-960` after the edit — the existing `for fut in ...`
iteration should not break.

- [ ] **Step 3: Run tests to verify they pass**

```bash
pytest tests/diagnostics/test_darkweb_probe.py -v
```

Expected: All 4 updated/new tests PASS. Existing tests (`test_l1_*`,
`test_l2_*`, etc.) still pass.

If `darkweb_future` is iterated in a way that breaks (e.g., the existing
code at line 873+ expects `None` to be filtered), read those lines and
adjust. The most likely issue is `for fut, ... in engine_futures.items()`
not touching `darkweb_future` at all, so no fix needed.

- [ ] **Step 4: Run full diagnostics test suite**

```bash
pytest tests/diagnostics/ -v
```

Expected: All tests pass. If `test_ldr_tor_circuit_probe.py` fails,
verify the `probe_ldr_tor_proxy` skip path still works (it's gated on
`primary_tool` and `darkweb.enabled` — unchanged).

- [ ] **Step 5: Commit**

```bash
git rev-parse --abbrev-ref HEAD  # 确认在 main 上
git add src/local_deep_research/diagnostics/engine_health.py
git commit -m "fix(preflight): darkweb gate 三信号 OR, 跳过时返回 skipped

- run_preflight_check 的 darkweb gate 改为:
  darkweb_enabled OR search.tool == 'darkweb'
- 跳过时返回 _darkweb_skipped_status (而非 None), 与
  probe_firecrawl/probe_proxy 的 self-skip 模式一致
- 新增 _darkweb_skipped_status 函数: 每引擎一行 skipped
  (name='darkweb/<engine>', detail='暗网未启用')

修复两个 bug:
1. 全局开 + 用户未勾 include_darkweb → 当前探测 (应 skipped)
2. 全局关 + search.tool='darkweb' → 当前不探测 (应探测)"

git log --oneline -3  # 确认 commit 在 main
```

## Task 3: Frontend — `darkweb-status` fetch 显示 + disabled 切换

**Files:**
- Modify: `src/local_deep_research/web/static/js/components/research.js:331-339`

**Interfaces:**
- DOM: `<div id="darkweb-container">` (research.html:229), `<input id="include_darkweb">` (research.html:234)
- API: `GET /settings/api/darkweb-status` → `{"enabled": bool}` (existing endpoint, no backend changes)

- [ ] **Step 1: Replace the fetch block in `research.js:331-339`**

Find the block:

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

Replace with:

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

- [ ] **Step 2: Verify no syntax error**

```bash
node -e "require('/home/administrator/local-deep-research/src/local_deep_research/web/static/js/components/research.js')" 2>&1 | head -5
```

Expected: Module exports or runs without SyntaxError. If it errors with
"Cannot find module" that's fine (no module exports); only fail if it's
a `SyntaxError`.

If `node` not available, use:

```bash
grep -c "darkweb-container" src/local_deep_research/web/static/js/components/research.js
```

Expected: At least 2 matches (the new code has 2 references: const + style.display).

- [ ] **Step 3: Manual verification instructions**

Front-end changes cannot be auto-tested in this repo. Add a note to the
commit message (not a separate file) documenting manual verification:

```markdown
## Manual verification

1. Set `search.engine.web.darkweb.enabled=false` (admin toggle off)
2. Open `/research` page in browser
3. Verify: `include_darkweb` checkbox is visible and disabled (灰显)
4. Try to click the checkbox — should not respond
5. Set `search.engine.web.darkweb.enabled=true`, reload page
6. Verify: checkbox is visible and enabled (可勾选)
```

- [ ] **Step 4: Commit**

```bash
git rev-parse --abbrev-ref HEAD  # 确认在 main 上
git add src/local_deep_research/web/static/js/components/research.js
git commit -m "fix(research-ui): darkweb 勾选框全局开关关闭时 disabled 灰显

research.js 的 darkweb-status fetch 改为:
- 始终显示 '#darkweb-container' (而非全局关时隐藏)
- 全局关时设置 checkbox.disabled=true + checked=false
- 全局开时 checkbox.disabled=false

与后端 run_preflight_check 的 skip 行为对称: 用户视觉上
看到 disabled 就知道 preflight 不会探测暗网。"

git log --oneline -3  # 确认 commit 在 main
```

## Task 4: Add changelog fragment

**Files:**
- Create: `changelog.d/+darkweb-preflight-gate.bugfix.md`

**Interfaces:**
- Convention: file naming `+<description>.<type>.md`, where type is one of `feature|bugfix|security`

- [ ] **Step 1: Create changelog fragment**

```bash
cat > /home/administrator/local-deep-research/changelog.d/+darkweb-preflight-gate.bugfix.md <<'EOF'
type: bugfix
title: 暗网 preflight gate — 未选择时不探测
description: |
  `run_preflight_check` 的暗网 gate 改为三信号 OR
  (`search.tool == "darkweb"` OR `snapshot.darkweb.enabled`)。
  用户未选择暗网时, preflight 跳过暗网探测并返回
  `skipped` 状态行, 而非静默消耗 60s 超时。
  前端同步: 全局开关关闭时, `include_darkweb` 勾选框
  显示但 disabled 灰显 (与后端跳过语义对齐)。
EOF
```

- [ ] **Step 2: Verify changelog directory layout**

```bash
ls /home/administrator/local-deep-research/changelog.d/ | head -20
```

Expected: `+darkweb-preflight-gate.bugfix.md` is listed alongside
existing entries like `+faiss-merge-write-race.bugfix.md`.

- [ ] **Step 3: Commit**

```bash
git rev-parse --abbrev-ref HEAD  # 确认在 main 上
git add changelog.d/+darkweb-preflight-gate.bugfix.md
git commit -m "changelog: 暗网 preflight gate 修复

描述: run_preflight_check 三信号 OR gate, 未选择时返回 skipped;
前端 include_darkweb 勾选框全局关闭时 disabled 灰显。"

git log --oneline -5  # 确认全部 4 个 commit 在 main
```

## Self-Review

**1. Spec coverage:**
- ✅ 后端修改 `run_preflight_check` 的 darkweb gate → Task 2
- ✅ 新增 `_darkweb_skipped_status` 函数 → Task 2
- ✅ 前端 `darkweb-status` fetch 改为显示 + disabled 切换 → Task 3
- ✅ 3 个新测试 → Task 1
- ✅ 更新 1 个现有测试 → Task 1
- ✅ changelog → Task 4
- ✅ 不修改 `_apply_darkweb_override` → 不在计划中（spec 已声明）
- ✅ 不修改 `probe_ldr_tor_proxy` → 不在计划中（spec 已声明）

**2. Placeholder scan:** No TBD/TODO/"fill in details" placeholders.

**3. Type consistency:**
- `_darkweb_skipped_status` signature uses `Optional[dict] = None`, matches `probe_darkweb` signature
- Returns `list[EngineStatus]`, matches existing pattern
- `EngineStatus(name, status, detail, kind=...)` kwargs match existing usages

**4. Test naming:** 3 new tests use `test_preflight_*` prefix consistent
with existing `test_preflight_skips_darkweb_when_disabled` /
`test_preflight_includes_per_darkweb_engine_when_enabled`.

---

## End of Plan
