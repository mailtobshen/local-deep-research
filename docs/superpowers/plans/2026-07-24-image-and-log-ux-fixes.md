# 报告图片与日志 UX 修补方案

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复报告中的图片 alt 文本、尺寸控制、段落配图与研究日志方向四个 UX 问题。

**Architecture:** 报告图片：在生成链路中"清洗 alt/清空无效属性/把宽高语法改成纯 Markdown 兼容形态"，并对超大图直接按 PIL 测量尺寸生成 `<img>` HTML。日志方向：把 `#console-log-container` 切回普通 `column` 流，DOM 直接追加在尾部，自动滚动到 `scrollHeight`。

**Tech Stack:** Python（re/loguru/mistune/markdown-it PyPI）, Vitest, jsdom, existing log-panel test harness.

## Global Constraints

- 报告图片：维持 `images/*` 当前日志链路（`IMG-TRACE`）不变；只改 alt 规范化、rewrite 语法、段落无图决策三处。
- 日志：保持 `MAX_LOG_ENTRIES=500`、dedupe、500 cap 不变；只改 DOM 顺序、CSS `flex-direction` 和自动滚动定位。
- 改动前后必须可重复运行：先写红测试，再修代码。
- 任何在 images 链路中新增的日志点必须用 `from loguru import logger` 并使用 f-string（参考 IMG-TRACE 备忘）。
- Python 与 JS 代码改动后，`docker compose -f docker-compose.ldr-local.yml restart local-deep-research` 让 Flask 与前端 worker 重新加载。

---

### Task 1: 修复超大图尺寸语法

**Files:**
- Modify: `src/local_deep_research/images/store.py` `rewrite_markdown`
- Test: `tests/images/test_store_rewrite.py`（新增文件）

**Interfaces:**
- Consumes: 当前 `_IMG_RE` 与 `_MAX_DISPLAY_PX`。
- Produces: 一个新常量函数 `format_image(alt, route, size)` 返回 `![alt](route)` 或 `<img src=route width=… height=… alt=… loading=lazy>`；按 size 决定是否走 HTML 形态。

- [ ] **Step 1: 写失败的纯函数测试**

```python
def test_rewrite_emits_html_img_with_attrs_for_oversized():
    md = "![长隆](https://example.com/big.jpg)"
    sizes = {"https://example.com/big.jpg": (2000, 1000)}
    routes = {"https://example.com/big.jpg": "/images/abc.jpg"}
    out = ImageStore(...).rewrite_markdown(md, routes, sizes)
    assert '<img' in out
    assert 'src="/images/abc.jpg"' in out
    assert 'width="600"' in out
    assert 'height="300"' in out
    assert out.strip().startswith('<img') and out.strip().endswith('/>')


def test_rewrite_keeps_markdown_for_small_or_unknown():
    md = "![small](https://example.com/small.jpg)"
    sizes = {"https://example.com/small.jpg": (200, 150)}
    routes = {"https://example.com/small.jpg": "/images/small.jpg"}
    out = ImageStore(...).rewrite_markdown(md, routes, sizes)
    assert out == "![small](/images/small.jpg)"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/images/test_store_rewrite.py -v`
Expected: 两个用例都失败（当前实现返回 `{width=600}` 字符串）。

- [ ] **Step 3: 实现 rewrite_markdown**

将 `repl` 改为：长边 ≤ 600 → 保持 `![alt](route)`；否则按等比生成 `<img src="route" alt="alt" width="..." height="..." loading="lazy" />`（landscape → width=600, height=round(600*h/w)；portrait 同理 height=600）。HTML 形态通过 `html.escape(alt, quote=True)` 防 XSS。

- [ ] **Step 4: 重跑测试**

Expected: 通过。`git commit` 修复。

### Task 2: 截断 alt 文本

**Files:**
- Modify: `src/local_deep_research/images/postprocessing.py` `enhance_report_with_images` 或新 `images/serializer.py`
- Test: `tests/images/test_alt_sanitizer.py`

**Interfaces:**
- Consumes: `bank.candidates_with_alt()` 返回的 `ExtractedImage`。
- Produces: `_safe_alt(alt, max_len=120) -> str`：去掉 `[..]` 中括号、移除换行、把空白折叠为单空格、超过 120 字时在词边界截断 + `…`。

- [ ] **Step 1: 写失败测试**

```python
def test_safe_alt_strips_brackets_and_newlines():
    assert _safe_alt("Pelayaran Malam Sungai Pearl Guangzhou [Menikmati Pemandangan Malam Menara Guangzhou + Kapal Bertema Kebangsaan Jinxi dengan Persembahan Langsung]") == "Pelayaran Malam Sungai Pearl Guangzhou Menikmati Pemandangan Malam Menara Guangzhou + Kapal Bertema Kebangsaan Jinxi dengan Persembahan Langsung"[:120] + "…"


def test_safe_alt_truncates_long():
    long = "abc " * 100
    out = _safe_alt(long)
    assert len(out) <= 121
    assert out.endswith("…")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/images/test_alt_sanitizer.py -v`
Expected: 失败（函数尚未存在）。

- [ ] **Step 3: 在 `postprocessing.py` 实现 `_safe_alt`**

位置：`enhance_report_with_images` 顶部（局部辅助函数）。步骤：
1. `re.sub(r"\[[^\]]*\]", "", alt)` 去方括号段。
2. `re.sub(r"\s+", " ", alt).strip()` 折叠空白。
3. 若 `len > 120` 则 `out = out[:120].rsplit(' ', 1)[0] + "…"`。

- [ ] **Step 4: 在调用点替换**

将 `url_to_alt = {u: m.alt for u, m in url_to_meta.items() if m.alt}` 改为 `url_to_alt = {u: _safe_alt(m.alt) for u, m in url_to_meta.items() if m.alt}`，同步影响 `chosen` 后传递给 `rewrite_markdown` 的 alt。

- [ ] **Step 5: 重跑测试并提交**

Expected: 通过。

### Task 3: 段落配图覆盖（heuristic fallback）

**Files:**
- Modify: `src/local_deep_research/images/postprocessing.py` 在 `enhance_report_with_images` 中、LLM 增强后、`_dedupe_images` 后

**Interfaces:**
- Consumes: `enhanced` 字符串、bank、`chosen` URL 列表。
- Produces: 当 LLM 漏配某节段时，对每个 `^##\s+` 标题下无图片的段，按"标题关键词 ∩ alt 关键词"最佳匹配挑一张 bank 中已有 alt 的图补到节段首行后；同一张图不得在 fallback 阶段被复用（去重）。

- [ ] **Step 1: 写失败测试**

```python
def test_section_image_fallback_adds_image_for_empty_section():
    md = "# Title\n\n## 石室圣心大教堂\n\n石室圣心大教堂是广州著名景点。\n"
    candidates = [ExtractedImage("https://x/y.jpg", "石室圣心大教堂", "", "", 600, 400)]
    out = fill_section_images(md, candidates)
    assert "![石室圣心大教堂](https://x/y.jpg)" in out


def test_section_image_fallback_skips_sections_with_image():
    md = "## 珠江夜游\n\n![img](https://kept.jpg)\n\n文字"
    out = fill_section_images(md, [ExtractedImage("https://x/y.jpg", "珠江夜游", "", "", 600, 400)])
    assert "https://kept.jpg" in out
    assert "https://x/y.jpg" not in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/images/test_section_fallback.py -v`
Expected: 失败（`fill_section_images` 不存在）。

- [ ] **Step 3: 实现 `fill_section_images`**

- 用 `re.split(r'(?m)^(##\s+.*)$', md)` 解析节段。
- 收集已用 URL（`_IMG_RE` 命中）。
- 关键词匹配：`section_words = set(jieba 不必引入；用 re 切词 + 中文 unicode 范围覆盖；按 2-gram 重叠数排）`。
- 每个空节段取最高分候选插入"标题下一行"；不重用已用 URL。
- IMG-TRACE 增点：`section_fallback research={id} placed={n} considered={k}`。

- [ ] **Step 4: 在 `enhance_report_with_images` 中调用**

在 `_dedupe_images(enhanced)` 之后、`chosen = [m.group(2) for m in _IMG_RE.finditer(enhanced)]` 之前插入：

```python
enhanced = fill_section_images(enhanced, bank.candidates_with_alt())
```

并把它纳入到 IMG-TRACE `ENHANCE` 日志前的"重算 chosen"流程。

- [ ] **Step 5: 重跑并提交**

Expected: 通过。

### Task 4: 研究日志方向改为底部追加

**Files:**
- Modify: `src/local_deep_research/web/static/css/styles.css` `.ldr-console-log` 段
- Modify: `src/local_deep_research/web/static/js/components/logpanel.js`（插入位置、批量顺序、`scrollLogContainerToLatest`）
- Modify: `src/local_deep_research/web/static/js/services/socket.js`（fallback 路径）
- Modify: `tests/js/components/logpanel.test.js`

**Interfaces:**
- Consumes: 现有 `column-reverse` 布局，oldest→newest DOM 顺序。
- Produces: 改为普通 `column`，DOM 顺序保持 oldest→newest（自然 append 在尾），自动滚动 `scrollTop = scrollHeight`。

- [ ] **Step 1: 写失败测试**

```js
it('appends new entries to the visual bottom', async () => {
    const container = document.getElementById('console-log-container');
    Object.defineProperty(container, 'scrollHeight', { configurable: true, value: 800 });
    Object.defineProperty(container, 'clientHeight', { configurable: true, value: 200 });
    let scrollTop = 0;
    Object.defineProperty(container, 'scrollTop', { configurable: true, get: () => scrollTop, set: (v) => { scrollTop = v; } });

    // Pre-seed an existing entry
    const old = document.createElement('div');
    old.className = 'ldr-console-log-entry';
    old.dataset.logId = 'old-1';
    old.dataset.logTimeMs = String(Date.now() - 60_000);
    container.appendChild(old);

    window._logPanelState.expanded = true;
    window._logPanelState.autoscroll = true;
    logPanel.addLog('newest', 'info');
    await vi.runAllTimersAsync();

    // DOM order: oldest first, newest last
    const ids = Array.from(container.querySelectorAll('.ldr-console-log-entry')).map(n => n.dataset.logId);
    expect(ids).toEqual(['old-1', expect.stringMatching(/^.*-newest$|live-newest/)]);
    // Auto-scroll to bottom
    expect(container.scrollTop).toBe(800);
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run tests/js/components/logpanel.test.js -t "appends new entries"`
Expected: 失败（当前 `column-reverse` + `insertBefore` 模式让新记录插入到 DOM 头部，且 `scrollTop=0` 不是底部）。

- [ ] **Step 3: 改 CSS**

`styles.css:2140`：

```css
.ldr-console-log {
    ...
    display: flex;
    flex-direction: column;     /* was: column-reverse */
}
```

- [ ] **Step 4: 改 `logpanel.js`**

- `loadLogsForResearch` 批量：`for (let i = 0; i < sortedLogs.length; i++)`（newest-first → DOM 顺序 newest 在末）。
- `addLogEntryToPanel` 实时：把 `insertBefore(element, nextNewerEntry || null)` 改为 `appendChild(element)`。
- `scrollLogContainerToLatest()`：把 `consoleLogContainer.scrollTop = 0` 改为 `consoleLogContainer.scrollTop = consoleLogContainer.scrollHeight`。
- `toggleAutoscroll` 内同样改成 `scrollTop = scrollHeight`。
- 更新注释（不再有 column-reverse 假设）。

- [ ] **Step 5: 改 `socket.js` fallback**

确认两处 fallback (`addLogToUI`/`safeAddLog`) 已使用 `appendChild(entry)`；如果是 `insertBefore` 模式则改为 `appendChild`。

- [ ] **Step 6: 重跑 logpanel 测试 + 提交**

Run: `npx vitest run tests/js/components/logpanel.test.js`
Expected: 全部通过。提交。

### Task 5: 验证 & 重新部署

**Files:** 无新增；只跑命令。

- [ ] **Step 1: 跑后端测试**

Run:
```bash
pytest tests/images -q
```
Expected: 通过。

- [ ] **Step 2: 跑前端测试**

Run:
```bash
npx vitest run tests/js/components/logpanel.test.js
```
Expected: 通过。

- [ ] **Step 3: 重启容器**

```bash
docker compose -f docker-compose.ldr-local.yml restart local-deep-research
docker inspect --format '{{.State.Health.Status}}' ldr-local
```
Expected: `healthy`。

- [ ] **Step 4: 文档化**

将本次修复记录到 `.superpowers/sdd/progress.md` 与 `MEMORY.md` 的相关条目中（更新 `img-trace-observability` 备忘 + 新增 `log-direction-bottom-append`）。
