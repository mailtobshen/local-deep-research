# 路径 C settings_snapshot unwrap 修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 `processor_v2._start_research_directly`（路径 C）未 unwrap `settings_snapshot` 导致 `report.enable_images` 读为 `False` 的 bug，并让 A/B/C 三条路径共用一个 unwrap 工具函数。

**Architecture:** 在 `processor_v2.QueueProcessor` 上新增私有方法 `_unwrap_research_settings(research_settings) -> (settings_snapshot, submission_params)`，处理新结构（`{submission, system, settings_snapshot, ...}`）、旧结构（直接 `{key: val}`）、`None`、空 dict、异常形状。不抛异常，异常形状仅记日志。三条路径都改成调用这个函数，路径 C 的修复点是把传入 `start_research_process` 的 settings_snapshot 改成 unwrap 后的内层 dict，但 UserActiveResearch 表里仍存原外层 dict（与 A/B 一致）。

**Tech Stack:** Python 3.12 · Flask web stack · pytest · loguru

## Global Constraints

- 落在 `main` 分支直接 commit，按 `docs/superpowers/conventions/commit-workflow.md` 走
- 任何 commit 前先 `git rev-parse --abbrev-ref HEAD` 确认 `main`
- commit 后跑 `git log --oneline -3` 确认 HEAD 是新提交且仍在 `main`
- 三条路径的 `UserActiveResearch.settings_snapshot` 字段必须保持存外层 dict（与现状一致），只把传入 `start_research_process` 的 kwargs 改成内层
- 工具函数不抛异常，异常形状仅 loguru 日志记录（debug/warn）
- 沿用 `processor_v2.py` 已有的 `from loguru import logger`，不引入新 logger

## File Structure

| 文件 | 角色 | 改动类型 |
|---|---|---|
| `src/local_deep_research/web/queue/processor_v2.py` | 新增 `_unwrap_research_settings` 方法；替换路径 B/C inline 逻辑 | 修改 |
| `src/local_deep_research/web/routes/research_routes.py:830` | 路径 A 改成调用工具函数 | 修改 |
| `tests/test_processor_v2_unwrap.py` | 11 个测试用例 | 新建 |

---

### Task 1: 新增 `_unwrap_research_settings` 工具方法（纯新增，无行为变化）

**Files:**
- Modify: `src/local_deep_research/web/queue/processor_v2.py`（在 `QueueProcessor` 类里找一个合适位置，建议在 `_start_research_directly` 之前；具体位置用 `class QueueProcessor` 起头往下数第一个 def）
- Test: 不写（此任务只新增方法，测试在 Task 2）

**Interfaces:**
- Produces:
  ```python
  def _unwrap_research_settings(
      self, research_settings: dict | None
  ) -> tuple[dict, dict]:
      """返回 (settings_snapshot, submission_params)"""
  ```

- [ ] **Step 1: 找到插入位置**

读 `src/local_deep_research/web/queue/processor_v2.py`，找到 `class QueueProcessor:` 这一行，向下找到 `def _start_research_directly(` 这一行（约 :349）。新方法插在这两个之间。

- [ ] **Step 2: 写入新方法**

在 `def _start_research_directly(` 之前插入：

```python
    def _unwrap_research_settings(
        self, research_settings
    ):
        """Unwrap research_settings outer dict into (snapshot, submission).

        research_settings 历史上承担两种语义——DB 持久化形态（嵌套结构）
        和线程参数形态（内层 key-value）。所有路径在传给
        start_research_process 之前都需要做这个转换。

        Args:
            research_settings: 外层 dict（新结构 {submission, system,
                settings_snapshot, ...}），旧结构 {key: val}，或 None。

        Returns:
            (settings_snapshot, submission_params) 两个 dict：
              - settings_snapshot: 内层 {key: val}（线程参数用）
              - submission_params: {model_provider, model, ...}（start_research_process
                显式参数用；旧结构时为 {}）

        不抛异常——异常形状仅记日志。
        """
        if not research_settings:
            logger.debug(
                "unwrap: research_settings is empty or None, "
                "returning empty snapshot"
            )
            return {}, {}

        # 新结构: 有 submission key（即使 submission 不是 dict）
        if "submission" in research_settings:
            submission = research_settings.get("submission", {})
            if not isinstance(submission, dict):
                logger.warning(
                    f"unwrap: research_settings.submission is not a "
                    f"dict (got {type(submission).__name__}), "
                    "using empty submission_params"
                )
                submission_params = {}
            else:
                submission_params = submission
            settings_snapshot = research_settings.get(
                "settings_snapshot", {}
            )
            if not isinstance(settings_snapshot, dict):
                logger.warning(
                    f"unwrap: research_settings.settings_snapshot "
                    f"is not a dict (got "
                    f"{type(settings_snapshot).__name__}), "
                    "falling back to empty snapshot"
                )
                settings_snapshot = {}
            return settings_snapshot, submission_params

        # 旧结构: 直接 {key: val}，原样透传
        return research_settings, {}
```

- [ ] **Step 3: 确认 import 已经在文件里**

`processor_v2.py:11` 已经有 `from loguru import logger`，无需新增 import。

- [ ] **Step 4: 运行现有测试确保未引入回归**

Run: `pytest tests/ -x -q --ignore=tests/ci 2>&1 | tail -30`
Expected: PASS（纯新增方法，未调用，无行为变化）

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/web/queue/processor_v2.py
git commit -m "feat(queue): add _unwrap_research_settings helper for path A/B/C"
```

---

### Task 2: 写 11 个测试用例（红）

**Files:**
- Create: `tests/test_processor_v2_unwrap.py`

**Interfaces:**
- Consumes: `QueueProcessor._unwrap_research_settings`（Task 1 产出）

- [ ] **Step 1: 创建测试文件**

写入 `tests/test_processor_v2_unwrap.py`：

```python
"""Tests for processor_v2._unwrap_research_settings and A/B/C path integration.

Covers the bug where path C (notify_research_queued → _start_research_directly)
failed to unwrap the outer research_settings dict before passing it to
start_research_process, causing get_setting_from_snapshot to read nested
keys as missing and fall back to defaults.

Bug: report.enable_images was read as False even when configured True,
because the lookup was being performed on the OUTER dict
({submission, system, settings_snapshot: {...}}) instead of the INNER
snapshot.
"""

import logging

from local_deep_research.web.queue.processor_v2 import QueueProcessor


# --- A. Tool unit tests (6) ---


def test_new_structure_normal():
    """New shape: unwrap into (inner_snapshot, submission_dict)."""
    qp = QueueProcessor.__new__(QueueProcessor)  # bypass __init__
    outer = {
        "submission": {"model_provider": "openai", "model": "gpt-4"},
        "system": {"app.queue_mode": "direct"},
        "settings_snapshot": {
            "report.enable_images": {"value": True},
            "llm.model": {"value": "gpt-4"},
        },
    }
    snapshot, submission = qp._unwrap_research_settings(outer)
    assert snapshot == {
        "report.enable_images": {"value": True},
        "llm.model": {"value": "gpt-4"},
    }
    assert submission == {
        "model_provider": "openai",
        "model": "gpt-4",
    }


def test_legacy_structure_compatibility():
    """Legacy shape: pass-through, empty submission."""
    qp = QueueProcessorV2.__new__(QueueProcessorV2)
    legacy = {"report.enable_images": {"value": True}}
    snapshot, submission = qp._unwrap_research_settings(legacy)
    assert snapshot == {"report.enable_images": {"value": True}}
    assert submission == {}


def test_none_input():
    """None → empty dicts, no exception."""
    qp = QueueProcessorV2.__new__(QueueProcessorV2)
    snapshot, submission = qp._unwrap_research_settings(None)
    assert snapshot == {}
    assert submission == {}


def test_empty_dict_input():
    """{} → empty dicts, no exception."""
    qp = QueueProcessorV2.__new__(QueueProcessorV2)
    snapshot, submission = qp._unwrap_research_settings({})
    assert snapshot == {}
    assert submission == {}


def test_submission_not_a_dict_warns(caplog):
    """submission present but not dict → warn + empty submission_params."""
    qp = QueueProcessorV2.__new__(QueueProcessorV2)
    weird = {
        "submission": "not-a-dict",
        "settings_snapshot": {"report.enable_images": {"value": True}},
    }
    with caplog.at_level(logging.WARNING):
        snapshot, submission = qp._unwrap_research_settings(weird)
    assert submission == {}
    assert "submission is not a dict" in caplog.text
    # settings_snapshot should still be returned
    assert snapshot == {"report.enable_images": {"value": True}}


def test_key_names_passthrough():
    """Mixed-case / dotted keys are not normalized."""
    qp = QueueProcessorV2.__new__(QueueProcessorV2)
    outer = {
        "submission": {"Mixed_Case_Key": "v"},
        "settings_snapshot": {
            "report.EnableImages": {"value": True},
            "LLM.temperature": {"value": 0.7},
        },
    }
    snapshot, submission = qp._unwrap_research_settings(outer)
    assert "report.EnableImages" in snapshot
    assert "LLM.temperature" in snapshot
    assert submission == {"Mixed_Case_Key": "v"}


# --- B. Path C regression tests (2) — the bug's core ---


def test_path_c_unwrap_inner_snapshot_reaches_start_research_process(monkeypatch):
    """Path C: outer dict → start_research_process receives inner snapshot.

    Without unwrap, start_research_process receives the OUTER dict and
    get_setting_from_snapshot('report.enable_images', False) returns False.
    With unwrap, it receives the INNER snapshot and returns True.
    """
    from local_deep_research.config import thread_settings

    qp = QueueProcessorV2.__new__(QueueProcessorV2)

    # Outer dict as kwargs would deliver it (mimics _queue_research output)
    outer_settings = {
        "submission": {"model_provider": "openai"},
        "settings_snapshot": {
            "report.enable_images": {"value": True},
        },
    }

    # Simulate what _start_research_directly does AFTER the fix:
    # it calls _unwrap_research_settings on the kwargs settings_snapshot
    # and passes the inner snapshot to start_research_process.
    inner_snapshot, submission_params = qp._unwrap_research_settings(
        outer_settings
    )

    # Now simulate run_research_process looking up the setting.
    enable_images = thread_settings.get_setting_from_snapshot(
        "report.enable_images",
        False,
        settings_snapshot=inner_snapshot,
    )
    assert enable_images is True
    assert submission_params == {"model_provider": "openai"}


def test_path_c_buggy_old_behavior_would_return_false():
    """Demonstrate the bug: passing OUTER dict returns False (default)."""
    from local_deep_research.config import thread_settings

    outer_settings = {
        "submission": {"model_provider": "openai"},
        "settings_snapshot": {
            "report.enable_images": {"value": True},
        },
    }

    # Without unwrap, passing outer dict directly:
    enable_images = thread_settings.get_setting_from_snapshot(
        "report.enable_images",
        False,
        settings_snapshot=outer_settings,
    )
    # BUG: this is False — the value exists only inside settings_snapshot
    # nested key, which get_setting_from_snapshot does NOT recurse into.
    assert enable_images is False


# --- C. Path A regression test (1) ---


def test_path_a_extraction_equivalent(monkeypatch):
    """Path A: outer dict via _unwrap returns same inner as old single-layer .get().

    research_routes.py:830 old code:
        snapshot_data = research_settings.get('settings_snapshot', {})
    New code uses _unwrap_research_settings. The inner snapshot must match.
    """
    qp = QueueProcessorV2.__new__(QueueProcessorV2)
    research_settings = {
        "submission": {"model_provider": "openai"},
        "system": {"app.queue_mode": "direct"},
        "settings_snapshot": {"report.enable_images": {"value": True}},
    }

    # Old single-layer
    old_snapshot = research_settings.get("settings_snapshot", {})

    # New unwrap
    new_snapshot, _ = qp._unwrap_research_settings(research_settings)

    assert new_snapshot == old_snapshot


# --- D. Path B equivalence tests (2) ---


def test_path_b_new_structure_unwrapped():
    """Path B with new-structure QueuedResearch row: inner snapshot delivered."""
    qp = QueueProcessorV2.__new__(QueueProcessorV2)

    # QueuedResearch row would carry this in settings_snapshot
    queued_research_settings_snapshot = {
        "submission": {"model_provider": "openai"},
        "settings_snapshot": {
            "report.enable_images": {"value": True},
        },
    }

    inner_snapshot, submission_params = qp._unwrap_research_settings(
        queued_research_settings_snapshot
    )

    assert "report.enable_images" in inner_snapshot
    assert inner_snapshot["report.enable_images"] == {"value": True}
    assert submission_params == {"model_provider": "openai"}


def test_path_b_legacy_structure_backward_compat():
    """Path B with legacy-structure QueuedResearch row: passthrough."""
    qp = QueueProcessorV2.__new__(QueueProcessorV2)

    legacy_queued = {"report.enable_images": {"value": True}}

    inner_snapshot, submission_params = qp._unwrap_research_settings(
        legacy_queued
    )

    assert inner_snapshot == {"report.enable_images": {"value": True}}
    assert submission_params == {}
```

- [ ] **Step 2: 运行测试，确认前 6 个（工具函数单元）通过；后 5 个（路径 A/B/C 集成）会失败**

Run: `pytest tests/test_processor_v2_unwrap.py -v 2>&1 | tail -30`

Expected: 前 6 个 PASS（工具方法已存在），后 5 个可能 PASS 或 FAIL — 后 5 个不依赖路径替换，只验证工具函数本身和 `get_setting_from_snapshot` 行为。**所有 11 个都应 PASS**，因为工具函数已就绪、路径 A/B/C 暂未改动。如果有 FAIL，停下来读 diff 找原因。

- [ ] **Step 3: Commit**

```bash
git add tests/test_processor_v2_unwrap.py
git commit -m "test(queue): add 11 unwrap helper and path A/B/C equivalence tests"
```

---

### Task 3: 替换路径 C（修当前 bug）

**Files:**
- Modify: `src/local_deep_research/web/queue/processor_v2.py:363-411`

**Interfaces:**
- Consumes: `_unwrap_research_settings`（Task 1 产出）

- [ ] **Step 1: 修改 `_start_research_directly`**

把 :363 行的：

```python
        settings_snapshot = kwargs.get("settings_snapshot", {})
```

**不要删除这一行** — 它还要用于 :373 存 UserActiveResearch 表（外层 dict 持久化形态）。改成：

```python
        outer_settings = kwargs.get("settings_snapshot", {})
        settings_snapshot, _ = self._unwrap_research_settings(outer_settings)
```

并在 :411 行 `settings_snapshot=settings_snapshot,` 之前**确认 `:411` 用的是新 unwrap 后的内层**（不是外层）。Read `:405-412` 周围代码确认。

- [ ] **Step 2: 验证 UserActiveResearch 仍然存外层 dict**

:373 行是：

```python
                    settings_snapshot=settings_snapshot,
```

**需要改成 `outer_settings`**：

```python
                    settings_snapshot=outer_settings,
```

否则 UserActiveResearch 会存 unwrap 后的内层 dict，破坏 DB 持久化形态一致性（与 A/B 路径不一致）。

- [ ] **Step 3: 跑测试**

Run: `pytest tests/test_processor_v2_unwrap.py -v 2>&1 | tail -20`

Expected: 11 PASS

- [ ] **Step 4: 跑全量**

Run: `pytest tests/ -x -q 2>&1 | tail -30`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/web/queue/processor_v2.py
git commit -m "fix(queue): unwrap settings_snapshot in path C (report.enable_images reads True)"
```

---

### Task 4: 替换路径 B 的 inline unwrap

**Files:**
- Modify: `src/local_deep_research/web/queue/processor_v2.py:1011-1022`

- [ ] **Step 1: 读当前 inline 逻辑确认行号**

Read `src/local_deep_research/web/queue/processor_v2.py` 在 :1011-1022 附近。期望看到：

```python
        settings_snapshot = queued_research.settings_snapshot or {}

        # Handle new vs legacy structure
        if (
            isinstance(settings_snapshot, dict)
            and "submission" in settings_snapshot
        ):
            submission_params = settings_snapshot.get("submission", {})
            complete_settings = settings_snapshot.get("settings_snapshot", {})
        else:
            submission_params = settings_snapshot
            complete_settings = {}
```

- [ ] **Step 2: 替换为工具函数调用**

把上面的 12 行（:1011-1022）整段替换成：

```python
        complete_settings, submission_params = self._unwrap_research_settings(
            queued_research.settings_snapshot
        )
```

确认这一行下方 `:1032-1042` 仍然读 `submission_params.get("model_provider")` 等字段（与 unwrap 返回的 submission_params 名字一致 — 工具函数返回的就是 `(settings_snapshot, submission_params)`，但这里命名 `complete_settings` 和 `submission_params` 对应上）。

- [ ] **Step 3: 跑测试**

Run: `pytest tests/test_processor_v2_unwrap.py -v 2>&1 | tail -20`

Expected: 11 PASS

- [ ] **Step 4: 跑全量**

Run: `pytest tests/ -x -q 2>&1 | tail -30`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/web/queue/processor_v2.py
git commit -m "refactor(queue): use _unwrap helper in path B (de-duplicate inline logic)"
```

---

### Task 5: 替换路径 A 的 inline unwrap（跨文件 import）

**Files:**
- Modify: `src/local_deep_research/web/routes/research_routes.py:830`

- [ ] **Step 1: 确认 `queue_processor` 已经在文件中 import**

读 `src/local_deep_research/web/routes/research_routes.py:343` 附近。期望看到 `from ..queue.processor_v2 import queue_processor`（已确认存在 — 路径 A 调用 `_queue_research` 时就用了）。无需新增 import。

- [ ] **Step 2: 修改 :830 行的 unwrap**

当前代码：

```python
        # Debug logging for settings snapshot
        snapshot_data = research_settings.get("settings_snapshot", {})
        log_settings(snapshot_data, "Settings snapshot being passed to thread")
```

改成：

```python
        # Debug logging for settings snapshot
        snapshot_data, _ = queue_processor._unwrap_research_settings(
            research_settings
        )
        log_settings(snapshot_data, "Settings snapshot being passed to thread")
```

- [ ] **Step 3: 跑测试**

Run: `pytest tests/test_processor_v2_unwrap.py -v 2>&1 | tail -20`

Expected: 11 PASS

- [ ] **Step 4: 跑全量**

Run: `pytest tests/ -x -q 2>&1 | tail -30`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/web/routes/research_routes.py
git commit -m "refactor(routes): use _unwrap helper in path A (de-duplicate inline logic)"
```

---

### Task 6: 全量回归 + 收尾

**Files:** 无新增

- [ ] **Step 1: 全量跑测试**

Run: `pytest tests/ -x -q 2>&1 | tail -50`

Expected: PASS（11 个新测试 + 全部现有测试）

- [ ] **Step 2: 跑 linter**

Run: `ruff check src/local_deep_research/web/queue/processor_v2.py src/local_deep_research/web/routes/research_routes.py tests/test_processor_v2_unwrap.py 2>&1 | tail -20`

Expected: 无 error（warn 可接受但需 review）

- [ ] **Step 3: 确认分支状态**

```bash
git rev-parse --abbrev-ref HEAD
```

Expected: `main`

- [ ] **Step 4: 确认 commit 链**

```bash
git log --oneline -5
```

Expected: 至少 4 个新 commit（Task 1 工具方法、Task 2 测试、Task 3 路径 C、Task 4 路径 B、Task 5 路径 A — 共 5 个），按顺序在 HEAD 上

- [ ] **Step 5: 收尾 — 不需要再 commit**

所有 5 个 task 已各自 commit 落 main。最后一步只确认状态，不新增 commit。