# 恢复状态：报告图片提取功能实现

**状态**：✅ 全部完成（Task 1~10），2026-07-21
**分支**：`i18n-zh-translation`

## ✅ 完成总结

10 个任务全部实现、测试通过、已提交。单元测试 32 passed + 集成测试 1 passed。

| Task | Commit | 测试 |
|---|---|---|
| 1 ImageExtractor | 77d4a5a7 | 7 |
| 2 ImageBank | 4962ab8b | 4 |
| 3 VisionDescriber | e508629d | 4 |
| 4 Model+migration 0011 | 573d7d73 | 3 |
| 5 ImageEnhancer | 95928b8a | 4 |
| 6 ImageStore | dde70ad5 | 4 |
| 7 Firecrawl HTML | 531a9147 | 2 |
| 8 后处理接入 | c6e85df6 | 2 |
| 9 路由+API+级联删 | bebfe904 | 2 |
| 10 集成验证 | a07f4034 | 1 |

**关键计划偏差修正**（实现时现场核实后改的）：
- SSRF HTTP：`security.safe_requests.safe_get/safe_post`（模块级函数），非计划假设的 `proxy_config.safe_requests` 对象
- `UtcDateTime/utcnow` 来自 `sqlalchemy_utc`，非 `.common`
- 设置用 `category=report_parameters` + `type=REPORT`（真实约定），非计划的 APP
- list API 用 `get_user_db_session` context manager，非未验证的 `db_manager.get_session`
- serve route traversal 返回 (json,404) tuple 便于单测，非 abort()
- `scrape()` 仅一个调用点，str→dict 改动安全

**迁移已在真实加密 DB 应用**：rev==head==0011，research_images 表 + search_results.html_content 均存在。

## 剩余人工验收（需 WebUI + 真实 Firecrawl，非自动）
计划 Task 10 Step 3-6：在 WebUI 开启 `report.enable_images` → 跑一次源页面含图的 research → 查报告含 `/images/` 路由 → 删研究验证 `/data/images/<rid>/` 被清除。

---

## （历史）原恢复指令

---

## ⚠️ 恢复后第一件事：读这个

中断发生在 **Task 1 的 Step 3 刚完成**（代码写好）但 **Step 4（跑测试验证）和 Step 5（提交）还没做**。

恢复后立即执行：
1. 读本文件了解全局状态
2. 读 `docs/superpowers/plans/2026-07-20-report-image-extraction.md` 的 Task 1
3. 跑 Task 1 Step 4（验证测试通过）
4. 提交 Task 1
5. 继续 Task 2~10

---

## 执行进度（10 个任务）

| Task | 状态 | 说明 |
|---|---|---|
| 1. ImageExtractor | 🟡 进行中（代码写完，未验证未提交） | 见下方"Task 1 待办" |
| 2. ImageBank | ⬜ 未开始 | |
| 3. VisionDescriber | ⬜ 未开始 | |
| 4. Settings+Model+迁移 | ⬜ 未开始 | |
| 5. ImageEnhancer | ⬜ 未开始 | |
| 6. ImageStore | ⬜ 未开始 | |
| 7. Firecrawl scrape html | ⬜ 未开始 | |
| 8. 后处理贯通 | ⬜ 未开始 | |
| 9. 路由+API+级联删 | ⬜ 未开始 | |
| 10. 集成验证 | ⬜ 未开始 | |

TaskList 工具里的任务 ID：Task1=#7, Task2=#8, Task3=#9, Task4=#10, Task5=#11, Task6=#12, Task7=#13, Task8=#14, Task9=#15, Task10=#16。

---

## Task 1 待办（恢复后立即做）

**已创建的文件**（代码已写好，待验证）：
- `src/local_deep_research/images/__init__.py` ✓
- `src/local_deep_research/images/extractor.py` ✓
- `tests/images/test_extractor.py` ✓（7 个测试）

**Step 4：跑测试验证**（命令）：
```bash
cd /home/administrator/local-deep-research
docker cp tests/images/test_extractor.py ldr-local:/tmp/ldr_tests/test_extractor.py
docker exec ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest test_extractor.py -v'
```
预期：7 passed。若失败，修 extractor.py。

**Step 5：提交**：
```bash
git add src/local_deep_research/images/__init__.py src/local_deep_research/images/extractor.py tests/images/test_extractor.py
git commit -m "feat(images): add ImageExtractor — pure HTML to image list"
```
然后 TaskUpdate #7 = completed，开始 Task 2 (#8)。

---

## 🧪 测试执行工作流（重要，所有任务通用）

**环境约束**（已验证）：
- 宿主 `python3`（3.12）**无 pytest、无 LDR 包**，不能直接跑测试。
- 容器 `ldr-local` 内 venv `/install/.venv`（python3.14）**已装 pytest 9.1.1**。
- 容器内 `local_deep_research` 包 = 宿主 `src/local_deep_research` 的 **ro bind mount**（宿主改 src → 容器立即可见）。
- 容器内 **tests 目录没挂载**，宿主 `tests/` 在容器内不可见。

**因此每个任务的测试执行流程**：
1. 测试文件写在宿主 `tests/images/test_xxx.py`（纳入 git）。
2. 跑测试：
   ```bash
   docker cp tests/images/test_xxx.py ldr-local:/tmp/ldr_tests/test_xxx.py
   docker exec ldr-local bash -c 'cd /tmp/ldr_tests && /install/.venv/bin/python -m pytest test_xxx.py -v'
   ```
3. 代码改动写在宿主 `src/`（hot-mount，容器立即可见；测试是新 python 进程 import，**无需 restart 容器**）。

**已验证可用**：pytest 能 import `local_deep_research`、`bs4`、`sqlalchemy`、`langchain_core`。

**容器 venv 装过的东西**（重启容器会丢，重启后需重装）：
- `pip`（via ensurepip）
- `pytest 9.1.1`
若容器重启过，恢复时先重装：
```bash
docker exec ldr-local bash -c 'cd /install && .venv/bin/python -m ensurepip && .venv/bin/python -m pip install pytest'
```

---

## 📁 工作区状态（会话开始时的预存改动，与本计划无关，不要动）

这些是会话早期的工作（CSP 修复等），**不要提交、不要回滚**，与本功能实现独立共存：
```
M docker-compose.ldr-local.yml
M src/local_deep_research/diagnostics/engine_health.py
M src/local_deep_research/security/security_headers.py   ← CSP img-src 放行 https (本功能依赖)
M src/local_deep_research/web/static/js/components/progress.js
M src/local_deep_research/web/translations/en.json
M src/local_deep_research/web/translations/zh.json
?? tests/database/test_thread_session_cleanup.py
```

**每个任务只 `git add` 本任务涉及的文件**，用精确路径，避免误提交预存改动。

---

## 🔑 关键密码（用户提供的，用于容器内 DB 验证）

- LDR 用户：`admin`
- LDR 密码：`123456aB`
- 加密 DB：`/data/encrypted_databases/ldr_user_8c6976e5b5410415.db`
- 用于 Task 10 集成验证时打开 DB 查报告内容。

---

## 设计/计划的核心要点（防止上下文丢失）

**根因**（已确认）：报告里图片 URL 是 LLM 幻觉的 Wikimedia 死链（API 返回 missing），不是 LDR 抓取来的。LDR 原本没有任何找图能力。

**架构**：后处理增强（不是在 strategy 内注入，因为 WebUI 报告由 run_research_process 驱动，合成分散在多个 strategy）。
- 阶段0：Firecrawl scrape 返回 {markdown, html}，html 存进 `SearchResult.html_content`
- 阶段1：后处理从 findings.search_results[].html_content 提取图片建 ImageBank
- 阶段2：ImageEnhancer 一次 LLM 调用插图（强约束：只能用清单内真实 URL，禁造 URL）
- 阶段3：Vision 兜底（alt 缺失时，配了 report.image_vision_model 才生效）
- 落盘：ImageStore 下载选中图到 /data/images/<rid>/，写 research_images 表，markdown 改写为 /images/<rid>/<hash>

**关键决策**：
- `report.enable_images` 默认 false（默认关）
- `report.image_vision_model` 默认空（禁用 vision 兜底）
- ImageBank 是纯后处理局部对象（不跨层、不要线程上下文）
- 迁移 0011：建 research_images 表 + 给 search_results 加 html_content 列

**Plan 自审里标注的"Note for implementer"待现场核实点**（实现各任务时注意）：
- Task 3: `safe_requests` 的确切符号名（grep src/local_deep_research/security/）
- Task 4: `UtcDateTime`/`utcnow` 的 import 来源（看 research.py 顶部）
- Task 7: `safe_post` 符号名；FullSearchResults 是否透传 html_content
- Task 8: `settings_snapshot` 和 `db_session` 是否在 run_research_process 作用域内（grep）

---

## 提交历史（本功能相关，已完成）

```
81f4bd76 docs(plan): report image extraction implementation plan (10 tasks)
980cc4dd docs(design): fix ImageBank data flow via html_content on SearchResult
69d6cfdf docs(design): revise image injection to post-processing approach
9026eaaf docs(design): report real-image extraction & local mirror subsystem
(earlier)  security_headers.py CSP img-src 改动（未提交，在工作区）
```

---

## 恢复指令（给下一个会话的我）

1. `cat docs/superpowers/RESUME-STATE-image-extraction.md`（本文件）
2. 完成 Task 1 Step 4 + Step 5（上面写了命令）
3. 按 `docs/superpowers/plans/2026-07-20-report-image-extraction.md` 逐个任务执行 Task 2~10
4. 每个任务用 TaskUpdate 标记 in_progress / completed
5. 测试用上面"测试执行工作流"的 docker cp + pytest 方式
6. 全部完成后，用 superpowers:finishing-a-development-branch 收尾
