# 路径 C settings_snapshot 未 unwrap 修复设计

**日期**: 2026-07-27
**状态**: 已批准（待实施）
**触发 run**: 1d899743（图片增强仅在该 run 触发的根因）

## 问题

研究提交后图片增强（`report.enable_images`）功能在路径 C 上失效，导致大量 run
跳过图片增强。根因：路径 C 传入 `start_research_process` 的 `settings_snapshot`
参数是**外层 dict**（`{submission, system, settings_snapshot, ...}` 嵌套结构），
而 `get_setting_from_snapshot` 只匹配顶层 key 或点分前缀，不递归进入嵌套子
字典，所以查不到内层 `report.enable_images`，命中默认值 `False`，跳过图片增强。

## 三条路径的现状

| 路径 | 调用点 | 当前 unwrap 状态 |
|---|---|---|
| A. 直接启动 | `research_routes.py:830` | 单层 `.get("settings_snapshot", {})` |
| B. 队列取出 | `processor_v2.py:1011-1022` | 完整"new vs legacy"分支 |
| C. 延迟直启 | `processor_v2.py:363` | **缺失 — bug 所在** |

更结构性的根因：`research_settings` 这个 dict 同时承担两种语义——DB 持久
化形态（嵌套结构）和线程参数形态（内层 key-value）。三条路径都要 unwrap，
C 是后加的，漏了。

## 设计

### 目标

修复当前 bug，同时把三条路径的 unwrap 逻辑收敛到单一函数，避免未来新增
"第四条路径"时再次漏 unwrap。

### 新组件

`processor_v2.py` 内 `QueueProcessor` 类新增私有方法：

```python
def _unwrap_research_settings(
    self, research_settings
) -> tuple[dict, dict]:
    """把外层 research_settings 拆成 (settings_snapshot, submission_params)。

    输入形状：
      - 新结构 {"submission": {...}, "system": {...},
                "settings_snapshot": {key: val, ...}, ...}
      - 旧结构 {key: val, ...}（QueuedResearch 表可能残留旧行）
      - None 或 {}

    返回：
      - settings_snapshot: 内层 {key: val}（线程用）
      - submission_params: {model_provider, model, custom_endpoint, ...}
        （start_research_process 显式参数用）
        旧结构时为 {}

    不抛异常；异常形状仅记日志，不阻塞调用。
    """
```

### 调用方变化

| 路径 | 改动前 | 改动后 |
|---|---|---|
| A | `research_routes.py:830` 单层 `.get` | `snapshot_data, _ = queue_processor._unwrap_research_settings(research_settings)` |
| B | `processor_v2.py:1011-1022` 14 行 inline | 同上，1 行调用 |
| C | `processor_v2.py:363` 无 unwrap | 同上，补当前 bug |

### 日志策略

| 输入 | 行为 | 日志级别 |
|---|---|---|
| `research_settings = None` | 返回 `({}, {})` | debug |
| `research_settings = {}` | 返回 `({}, {})` | debug |
| 新结构但 `submission` 不是 dict | 返回 `(内层, {})` | warn |
| 旧结构（无 `submission` key） | 返回 `(research_settings, {})` | 无（合法形状） |

沿用 `processor_v2.py` 已有模块 logger，不引入新 logger。

### 测试策略

新增 `tests/test_processor_v2_unwrap.py`，11 个用例：

**A. 工具函数单元测试**（6 个）
1. 新结构正常：`(内层, submission)`
2. 旧结构兼容：`(原 dict, {})`
3. None 输入：`({}, {})` + 不抛
4. 空 dict：`({}, {})` + 不抛
5. 异常形状（`submission` 不是 dict）：fallback + warn 日志
6. 大小写混合 key：原样透传

**B. 路径 C 回归测试**（2 个，bug 核心）
7. 模拟路径 C 链路：断言 `start_research_process` 收到内层
8. 断言 `get_setting_from_snapshot("report.enable_images", ...) == True`

**C. 路径 A 不回归**（1 个）
9. 路径 A 调用函数后行为与改前一致

**D. 路径 B 等价性**（2 个）
10. 路径 B + 新结构：内层正确透传
11. 路径 B + 旧结构：向后兼容

测试用 `monkeypatch` 替换 `start_research_process` 为 mock；不依赖真实
DB / LLM / 网络。

### 实施步骤

1. 新增 `_unwrap_research_settings` 方法（纯新增，零行为变化）
2. 先写 11 个测试用例（红）
3. 替换路径 C（修 bug）→ 重跑用例确认转绿
4. 替换路径 B → 重跑确认
5. 替换路径 A → 重跑确认
6. 跑 `pytest tests/test_processor_v2_unwrap.py -v`，再跑 `pytest tests/ -x` 全量
7. commit 落 main（按 `commit-workflow.md`）

### 风险与缓解

| 风险 | 概率 | 缓解 |
|---|---|---|
| `research_routes.py` 与 `processor_v2` 循环 import | 低 | 先 grep 现状确认；若有，改成 module-level 私有函数 + 类里 thin wrapper |
| 路径 A 旧行为隐式依赖 | 中 | 函数对 None/空 dict 返回 `({}, {})`，与现状 `.get("settings_snapshot", {})` 等价 |
| 工具函数日志刷屏 | 低 | 旧结构是合法形状，不记日志；新结构异常才 warn |
| 全量回归失败 | 低 | 先 mock 测试通过，再全量 |

### 回滚

单 commit 包含所有改动；`git revert <hash>` 一键回退。工具函数是新代码，
回滚时直接删除即可，三条路径原 inline 逻辑保留在 git 历史里。

### 不在本设计范围内

- `run_research_process` 入口兜底 unwrap：方案 2 已让所有进入路径先经
  工具函数，入口兜底冗余
- 重构 `research_settings` 双身份本身：触及 QueuedResearch 模型 + DB schema，
  风险远大于收益
- `notify_research_queued` 改用显式参数而非 kwargs：scope 过大

## 评审记录

2026-07-27 用户答复：
- 先评审路径 A/B/C 设计合理性再修 bug
- 评审后再决定具体方案
- 选定方案 2：抽出统一 unwrap 工具函数
- ABC 三条路径都要等价性测试
- 工具函数不抛异常，但要日志输出（边界形状留痕）