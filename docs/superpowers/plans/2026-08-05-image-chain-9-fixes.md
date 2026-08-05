# 图片增强全链条修复计划（2026-08-05）

> **Status: Completed (2026-08-06)** — 全部 9 个修复落地，main 分支 14 个新 commit，710 测试通过 / 17 既有失败 unchanged / 0 回归。

> 起因：8 月 5 日 e2ec21ad run `ELIGIBLE_BANK=0 / END empty`，从日志反推真实路径后审计出 10 处错漏。
> 范围：sourcing → fetch → serialize → store → 增强阶段 → Markdown 注入全链条。

## 改动总览

| # | 错漏 | 严重度 | commit 数 |
|---|---|---|---|
| #2 | `_attach_images_if_enabled` 静默 | 低 | 1 |
| #7 | `dumps_images` / `loads_images` 静默失败 | 中 | 1 |
| #4 | alt 为空的图仍抓 / 仍写 html_content | 中 | 1 |
| #1+#6 | detailed-mode `reset()` 清掉累积的 html_content；`results` 与 `all_links_of_system` 两套数据 | **高** | 1 |
| #3 | `html_covered` / `to_fetch` 计数语义错 | 中 | 1 |
| #10 | PERSIST_FAIL 时 Markdown 残留 broken `![alt](url)` | 中 | 1 |
| #8 | `summary_focus_query` 模式下 fetch 工具抓图无意义 | 中 | 1 |
| #5 | LLM fetch 时同步抓图，但引用未定时就抓 | **高**（依赖 #1+#6） | 1 |

每条 commit 之间独立可验证，先 #2 / #7 / #4 这三条"日志+过滤"低风险改动落定后再做架构改动。

---

## 错漏 #2 — `_attach_images_if_enabled` 静默

**位置**：`src/local_deep_research/advanced_search_system/tools/fetch/__init__.py:80-126`

**症状**：成功写 html_content / 抓图失败都不打 IMG-TRACE 日志，本次 run 我花了 3 轮才确认 html_covered=2 的来源。

**改动**：
1. 进入函数时打 `logger.debug("[IMG-TRACE] LANGGRAPH_FILL_BEGIN url={url} enable_images={...}")`
2. `fetch_content_with_images` 返回后打 `logger.info("[IMG-TRACE] LANGGRAPH_FILLED src_url={url} images={len(images)}")`
3. except 分支从 `logger.exception(...)` 升级为 `logger.warning("[IMG-TRACE] LANGGRAPH_FILL_FAILED url={url} reason={type(e).__name__}: {e}")`
4. 给 `attach_html_content` 加一行 `logger.debug("[IMG-TRACE] LANGGRAPH_ATTACH url={url} images={len(images)} alt_chars={...}")`，让它和 fetch 路径配对

**验证**：
- `grep "\[IMG-TRACE\] LANGGRAPH" /tmp/run_*.log` 应该返回 ≥ LLM 调 fetch_content 次数的日志
- `attach_html_content` 写入成功的 URL 都能在日志里看到

---

## 错漏 #7 — `dumps_images` / `loads_images` 静默失败

**位置**：`src/local_deep_research/images/serialize.py:14-31, 34-58`

**症状**：`dumps_images` 异常时返回 `"[]"`，`loads_images` json 失败时返回 `[]`——上层 `_attach_images_if_enabled` 不知道自己抓的图丢了。

**改动**：
1. `dumps_images`：失败时返回 `"[]"` 之外，加 `logger.warning("[IMG-TRACE] DUMPS_FAIL reason={type(e).__name__}: {e}")`
2. `loads_images`：json.loads 失败时 `logger.warning("[IMG-TRACE] LOADS_FAIL reason={type(e).__name__} raw_first_100={raw[:100]!r}")`
3. 单条 record 缺 `url` 字段时 `logger.debug("[IMG-TRACE] LOADS_SKIP entry={entry!r}")`

**验证**：
- 故意往 `sr["html_content"]` 塞坏 JSON，跑一遍增强流程，logs 应有 LOADS_FAIL
- dumps 抛异常（如不可序列化对象）时不再静默

---

## 错漏 #4 — alt 为空的图仍抓 / 仍写 html_content

**位置**：
- `src/local_deep_research/advanced_search_system/tools/fetch/__init__.py:117-124`（summary 路径）
- `src/local_deep_research/advanced_search_system/tools/fetch/__init__.py:140-156`（full 路径）
- `src/local_deep_research/advanced_search_system/strategies/langgraph_agent_strategy.py:957-963`（legacy auto-fill 路径）
- `src/local_deep_research/images/serialize.py:14-31`（序列化时）
- `src/local_deep_research/research_library/downloaders/extraction/pipeline.py`（抓图时——`FETCHED_IMG` 已发出，可以事后过滤）

**症状**：4 个源页（stories-rezoned、上博、澎湃×2、人民日报）alt 全空，仍被抓、写入 `html_content`、再被增强阶段读出来——纯浪费。

**改动**：
1. 在 `_attach_images_if_enabled` 内 `dumps_images` 之前加：
   ```python
   before = len(images)
   images = [i for i in images if (i.alt or "").strip()]
   if before != len(images):
       logger.info(
           f"[IMG-TRACE] LANGGRAPH_FILL alt_filter dropped={before - len(images)}"
       )
   ```
2. 同步改 `dumps_images`：可选参数 `drop_empty_alt=False`，默认 False 保持兼容；增强阶段显式传 `drop_empty_alt=True`（双保险，避免 5+ 个 caller 漏改）
3. `ImageBank.add` 也加过滤，但保持幂等（`drop_empty_alt=True` 默认值与 dumps_images 同步）

**验证**：
- 跑同一份 12 个 URL 的 fetch，统计 `dumps_images` 调用前后 image 数；filtered 数应该等于各源"alt 为空"的数
- 增强阶段 `candidates_with_alt` 与 `all_urls` 的差应该为 0 或接近 0

---

## 错漏 #1 + #6 — detailed-mode 数据割裂

**位置**：
- `src/local_deep_research/web/services/research_service.py:1912-1935`（detailed 分支的 deferred pass / enhance 调用）
- `src/local_deep_research/images/relevance.py:670-678`（`url_to_html` 从 search_results 读）
- `src/local_deep_research/images/postprocessing.py:194-198`（`build_citation_index` 调用）

**症状**：
- detailed 模式下每子阶段 `collector.reset()` 清 `_results`，前面 fetch 工具写入的 html_content 全丢
- deferred pass 拿到的是初次 `analyze_topic(query)` 的 `results` dict，其 `findings[0].search_results` 只有初次 fetch 留下的少量记录
- `search_system.all_links_of_system`（累积所有 subsection 的 URL）从不被传进 deferred pass

**改动**：
1. 在 `research_service.py`（detailed 分支 + quick 分支 1514 附近），deferred 调用前注入：
   ```python
   if search_system is not None:
       results = dict(results)  # 不改原 dict
       results["all_links_of_system"] = list(search_system.all_links_of_system)
   ```
2. `relevance.build_citation_index` 在 `url_to_html` 构造后追加一段：若 `all_links_of_system` 字段存在，遍历该列表把每个 `r["html_content"]` 也合进 `url_to_html`（覆盖优先，以累积版为准）。`num_to_url` / `section_to_nums` 保持原逻辑（仍然从 markdown 解析）。
3. `postprocessing.enhance_report_with_images` 接收 results 形参不变；deferred pass `_deferred_image_fill` 也接收 results 形参不变；都通过上面的修改自动获得累积数据。

**验证**：
- 详细模式下 fetch 12 个 URL 后 deferred pass 的 `to_fetch` 应显著小于 71（已经填的 url_to_html 不需要再 fetch）
- 单元测试：构造一个 mock，3 个 subsection 各自 fetch 一个 URL，verify final `all_links_of_system` 包含 3 条带 html_content 的记录

**风险**：改动 #1+#6 是这次修复的核心，下面的 #3 / #8 / #5 都依赖它。先跑测试用例再合。

---

## 错漏 #3 — `html_covered` / `to_fetch` 计数语义错

**位置**：
- `src/local_deep_research/web/services/research_service.py:543-557`（deferred 循环）
- `src/local_deep_research/images/relevance.py:670-678`（`url_to_html` 来源）

**症状**：日志显示 `cited=71 already_html=0 to_fetch=0` —— 这三个数字在原代码逻辑下不可能同时成立（除非 search_results 与 cited_urls 完全不交），但本次 run 实际是 `html_covered=2`（被 #1+#6 屏蔽），`to_fetch` 应该是 69 而不是 0。

**改动**（依赖 #1+#6 完成）：
1. deferred 循环改成"以 cited_urls 为真值源"：
   ```python
   already_html = {url for url in cited_urls if url_to_html.get(url)}
   urls_to_fetch = [url for url in cited_urls if not url_to_html.get(url)]
   ```
   把 `for finding in results.get("findings", [])` 的两层循环干掉，直接用 `cited_urls` 和 `url_to_html` 这两个 dict。
2. log 行加 `covered=len(already_html) gap=len(urls_to_fetch)`，含义明确
3. `relevance.build_citation_index` 的 `url_to_html` 构造保持原行为（从 search_results + all_links_of_system 合并后的 dict 读），不要在 relevance 内做"以 cited_urls 为真值"的判断——让 deferred pass 自己决定 fetch 列表

**验证**：
- 任何模式跑完后，log 里 `covered + gap == len(cited_urls)` 永远成立（不变性）
- 当 url_to_html 为空时 `covered=0 gap=71`，不再自相矛盾

---

## 错漏 #10 — PERSIST_FAIL 时 Markdown 残留 broken `![alt](url)`

**位置**：`src/local_deep_research/images/postprocessing.py:478-510`

**症状**：增强阶段 INSERT 进了 5 张图 → `store.persist` 因为 anti-hotlink 失败 2 张 → 那 2 张的 `![alt](url)` 还留在 Markdown 里，但 url 是原始的 `https://...` 而非本地 route → 用户看到的图片链接是 404 / 反盗链拦截。

**改动**：
1. `store.rewrite_markdown` 之后增加一行：
   ```python
   failed = [u for u in chosen if u not in mapping]
   if failed:
       logger.warning(
           f"[IMG-TRACE] PERSIST_BROKEN_LINKS research={research_id} "
           f"count={len(failed)} urls={failed[:5]!r}"
       )
   ```
2. 给 `enhance_report_with_images` 加一个选项 `drop_persist_failures: bool = True`：当 True 时把 failed 的 `![alt](url)` 行从 markdown 里删除；False 时保留 + 打 warning
3. 默认 True；但快速模式下 `enable_images=False` 时不传

**验证**：
- 构造 mock：3 张图注入，2 张 persist 失败，verify 最终 markdown 只剩 1 张
- log 有 PERSIST_BROKEN_LINKS 行

---

## 错漏 #8 — `summary_focus_query` 模式下 fetch 工具抓图无意义

**位置**：
- `src/local_deep_research/advanced_search_system/tools/fetch/__init__.py:80-126`（三个 fetch tool builder 共享 `_attach_images_if_enabled`）

**症状**：本次 run `search.fetch.mode=summary_focus_query`——LLM 拿到的是 LLM 重写过的摘要+`[N]` 引用标记，**和最终 markdown 的"## References 块"对不上**。html_content 写的是原页的图，但增强阶段的 `section_to_nums` 用最终 markdown 解析原页 URL 的引用——其实 html_content 还是有用的，**前提是 LLM 真在最终 markdown 里 cite 了那个 URL**。这个错漏的真正问题是：**fetch 工具在 LLM 调 fetch_content 时同步抓图时 LLM 还没决定是否引用**——把 12 个 URL 的图全抓了，但最终 markdown 只引用了其中 2 个。

**改动**（依赖 #1+#6 完成）：
1. `_attach_images_if_enabled` 内加判断：当前 `search.fetch.mode` 是 `summary_focus_query` 时，**不写 html_content**（让 deferred pass 在知道真正引用的 URL 后再抓）。`full` 模式保留行为（LLM 看到全文+立即可读图，html_content 在增强阶段也能用）。
2. 在 `langgraph_agent_strategy.py:413`（sub_fetch 构建）后加 `_attach_images_if_enabled_enabled` 参数传递；`build_fetch_tool` 接收 `image_extraction_mode: "deferred"|"immediate"`，默认 `deferred`

**验证**：
- 详细模式下 `summary_focus_query`，`html_covered` 应该 = 0（fetch 工具不再写 html_content，全靠 deferred pass）
- `full` 模式下行为不变

**风险**：会减少 langgraph 阶段抓图数量，但 deferred pass 会在 markdown 渲染后补抓相同数量的图（且只抓真正引用的 URL，更精确）。

---

## 错漏 #5 — LLM fetch 时同步抓图，但引用未定时就抓

**位置**：`src/local_deep_research/advanced_search_system/tools/fetch/__init__.py:80-126`

**改动**：
1. 把 `_attach_images_if_enabled` 内 `dumps_images` 移到 `_attach_images_deferred`（仅记录 URL → 返回空的 `dumps_images([])` + `attach_html_content(url, "")` 占位）
2. `enhance_report_with_images` 之前的 `_deferred_image_fill` 阶段遍历 cited_urls，对每个 URL 调 `fetch_content_with_images` 抓图，然后写 `sr["html_content"]`（覆盖之前的占位空字符串）
3. `attach_html_content` 增加一个 sentinel `""` 表示"已知 URL，等 deferred 抓"，避免增强阶段再次尝试（早期版本 `if existing: already_html.add(url)` 在空字符串时会跳过，但 `if existing` 判 `""` 也是 truthy——需要明确用 `if existing is not None`）

**验证**：
- 任何模式下 deferred pass 后 `url_to_html` 包含所有 cited_urls（前提是 fetch 不抛错）
- 减少 fetch_content 调用次数（每个 URL 只抓一次图，不是 fetch 同步 + deferred 两次）

**风险**：必须先有 #1+#6 和 #3 才能上；否则 deferred pass 拿不到 cited_urls 全集。

---

## 实施顺序

1. **#2 + #7 + #4** — 日志 + 过滤的纯增量改动，30 分钟内跑完，1 commit 一次（推荐每个 # 一 commit，共 3 commit）
2. **#1 + #6** — 数据合并核心改动，30 分钟 + 测试，1 commit
3. **#3** — 计数语义改，依赖 #1+#6，1 commit
4. **#10** — broken link 处理，1 commit
5. **#8** — fetch 模式分流，依赖 #1+#6，1 commit
6. **#5** — fetch→deferred 重新分工，依赖所有前置，1 commit；完成后跑回归测试 + 测墙时

## 测试策略

每个 commit 前必须跑：
- `pytest tests/images/ -q`（`test_postprocessing_citation_pipeline.py` / `test_postprocessing_beijing_scenario.py` / `test_postprocessing_imgtrace_schema.py` / `test_postprocessing_imgtrace_full_schema.py`）
- 新增 mock 覆盖：模拟 #1+#6 的"3 个 subsection 各自 fetch" 场景，verify 累积正确
- 新增测试覆盖 #10：构造 2 张图 persist 失败的场景，verify markdown 行为

测试文件位置：`tests/images/`，遵循现有 fixture 风格（`tmp_db_session` / mock `search_system`）。

## 监控指标（commit 后台看）

每次跑完后查 3 条 IMG-TRACE：
1. `LANGGRAPH_FILLED` 数 = LLM 调 fetch_content 数（#2 验证）
2. `html_covered + to_fetch == nums`（#3 验证）
3. `PERSIST chosen > 0`（如果 0，意味着 LLM 写报告未引用任何图，需人工看）

## 风险登记

| 风险 | 缓解 |
|---|---|
| #5 大改可能让 fetch 工具变慢（deferred 抓图有等待） | 并发：deferred 用 `concurrent.futures.ThreadPoolExecutor` 并行抓 5 个 URL |
| #8 改变 `summary_focus_query` 行为，老用户配置习惯可能不同 | 加 settings key `report.image_extraction_mode`，默认 `deferred`；用户显式设 `immediate` 保留旧行为 |
| #1+#6 让 `_all_links` 数据量变大（71+ URL） | deferred pass 在循环前做一次 dedup（按 url） |
| #10 默认 drop_persist_failures=True 可能误删 | 加 IMG-TRACE 日志 `PERSIST_BROKEN_LINKS count=N`，便于审计 |