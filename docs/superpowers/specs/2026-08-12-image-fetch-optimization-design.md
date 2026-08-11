# 图像管线优化（抓取过滤 + 报告质量 + ATTACH 探针）

> 状态：**草稿，8 项已确认**。三项抓取优化 + 五项报告质量/图像呈现。用户可继续追加，届时并入本 spec。

## 背景

任务 `366bcc14`（上海旅游景点，2026-08-11）的 IMG-TRACE 复盘暴露三类问题：

1. **抓取浪费**：`_deferred_image_fill` 对全部 69 个引用 URL 批量抓图，22 个抓到 0 图，`via=none` 13 个干等 Firecrawl 兜底超时共 450s（均 34.6s）。抓取总耗时 1072s，929 张图仅 120 张落盘（87% 丢弃）。
2. **报告质量问题**：正文出现 ASCII 字符画（"二、空间关系图示说明"用 `┌─┐│◄►▼` 画的"空间关系图"）；部分节标题无命名实体（"主题园区与核心设施"）导致语义匹配失效；部分节采纳图过多（cite 39 一节 68 张）。
3. **图像呈现问题**：尺寸限制已半失效（超尺寸图按原大小渲染，仅靠 WebUI CSS 兜底，PDF 无兜底）；图下无 alt 文字说明。
4. **观测缺口**：12 条 ATTACH_MISS 无同源性观测，无法判断是否为可救回的 trailing-slash/www/scheme 误杀。

## 关键事实（已代码/日志核实）

- **deferred-fill 文本不被消费**（`research_service.py:656-658` 只读 `entry["images"]`）→ 跳过某 URL 不影响正文。
- **图与文本同一次下载**（`_fetch_content_dispatcher` 从同一份 HTML 提取）→ 省网络只能"跳过整个 URL"，"分两次抓取"无效。
- **尺寸限制已半失效**（`store.py:376-384`）：`RESIZE` 日志只记录不真缩，`rewrite_markdown` 返回纯 `![alt](route)`，超尺寸图按原大小渲染，注释明确"为保持 markdown 纯净而放弃 cap"。
- **ASCII 图非 prompt 要求**（`report_generator.py:365-374` 注释："These are NOT generated from any prompt — they are the model's own padding"），OUTPUT RULES 禁了 boilerplate 但**没禁字符画**。
- **节标题扁平无层级**（`relevance.py:397 _split_sections`）：只切 `^#{1,6}`，不保留父子关系；父级标题需靠 `#` 数量推断。
- **每节采纳无上限**：`postprocessing.py:533 placements` 全量传入 `insert_images_by_section`；BIND_ADOPTED 时已有 score（CANDIDATE_SCORED_DETAIL），可据此 top3。
- **`_extract_registered_domain`**（`relevance.py:218`）返回 eTLD+1，可复用。
- **PDF 渲染用 WeasyPrint**（Dockerfile 装了 fonts-noto-cjk + libpangocairo），原生支持 HTML `<figure>/<figcaption>`。

---

## 已确认的 8 项改动

### A 组：抓取阶段优化（省时间，零可用图损失）

#### 改动 1：结构性无图域名黑名单（抓取前置过滤）

**目标**：剔除"结构性无图"域，省网络+省时间。

**黑名单准入标准（严格）**：只收 HTML 不含可提取静态 `<img>` 的域（固有事实，非反爬）。**C 类（结构性有图但偶尔抓取失败的域：wikipedia/ctrip/360cities/shanghaidisneyresort 等）永不进名单。**

**初值（11 个域）**：

```python
STRUCTURAL_NO_IMAGE_DOMAINS: frozenset[str] = frozenset({
    # A 类: 社交/视频站 — 帖子/视频图 JS 动态注入, HTML 无静态 <img>
    "instagram.com", "facebook.com", "pinterest.com",
    "youtube.com", "tiktok.com", "x.com", "twitter.com",
    "weibo.com", "xiaohongshu.com",
    # B 类: 文档预览站 — 内容 Flash/JS 渲染
    "wenku.baidu.com", "docin.com", "doc88.com",
})
```

**匹配**：`_extract_registered_domain(url) in STRUCTURAL_NO_IMAGE_DOMAINS`（eTLD+1，命中所有子域）。

**落点**：`_deferred_image_fill`，`urls_to_fetch` 算出后、`fetch_content_with_images` 前。

**探针**：剔除时发 `[IMG-TRACE] STRUCTURAL_SKIP research=<id> count=<N> domains=<...>`。

#### 改动 2：Firecrawl 兜底超时收紧

**目标**：缩短 `via=none` 的等待。

**改动**：`firecrawl_client.py:30 DEFAULT_TIMEOUT` → **15s**。（执行前确认当前值；若已 ≤15s 跳过。核对历史 `via=firecrawl` 的 elapsed 分布，确认 15s 不误伤 P95。）

#### 改动 3：ATTACH_NEAR_MATCH 探针（只观测，不改匹配逻辑）

**目标**：ATTACH_MISS 时报告"记录侧是否有 canonical 近似"，为未来 URL 归一化决策积累证据。

**严格约束**：不改 attach 匹配逻辑（仍裸 `==`）；不改 `filled` 语义；不做 eTLD+1 兜底；不丢任何 query 参数。

**canonical 函数（新增 `_canonicalize_url` in relevance.py，仅供探针）**：5 个"页面内容保证不变"变换（strip / rstrip("/") / scheme 小写+http→https / host 小写+去www / 丢 fragment）；query 原样保留；解析异常 fail-closed。

**`via` 分类**：`trailing_slash` / `scheme` / `www` / `fragment` / `combined` / `other`。

**行为**：`ATTACH_MISS` 后扫一遍 findings+all_links，找第一条"原样不等但 canonical 相等"的记录，发 `[IMG-TRACE] ATTACH_NEAR_MATCH research=<id> cite_num=<N> ref_url=<url> canonical_match_url=<record_url> via=<type>`。仅 ATTACH_MISS 且找到近似时发。

---

### B 组：报告质量（改 prompt + 语义匹配 + 采纳上限）

#### 改动 4：禁止 ASCII 字符画（prompt 修复）

**根因**：`report_generator.py:365-409 _build_no_boilerplate_directive` 的 OUTPUT RULES 禁了 boilerplate，但没禁字符画。模型自发用 `┌─┐│◄►▼├└┘` 画"空间关系图"。

**修复**：在 OUTPUT RULES 加一条（编号续现有 4 条之后）：

```
5. Do NOT include ASCII art, box-drawing diagrams, character-based
   schematics, or any hand-drawn-style layout using symbols
   (┌─┐│◄►▼├└┘═║ etc.). These render poorly across viewers and waste
   space. Describe spatial/structural relationships in prose or a
   table instead — never draw them with text characters.
```

**落点**：`report_generator.py` `_build_no_boilerplate_directive` 返回串的 `=== END OF OUTPUT RULES ===` 之前。

**风险**：prompt 改动，需跑一次 detailed-mode 任务验证模型遵守。模型可能偶发不听话——此时考虑后处理（落盘前正则剥离 box-drawing 字符画块），但**先做 prompt，后处理作为后备**（不在本 spec 范围，除非验证失败）。

#### 改动 5：语义匹配纳入父级标题

**根因**：`_canonical_section_phrase(heading, entities)` 只用当前节 heading+实体。sec 91「主题园区与核心设施」、sec 93「配套酒店设施」这类节标题无命名实体，alt（"上海迪士尼乐园"）与节标题相似度低 → 误丢。

**修复**：构造 phrase 时，把**当前节的上一级标题**拼进去。父级判定靠 `#` 数量：当前节是 `### X`，向上找最近的 `## Y`（或 `# Y`）作为父级，phrase = `"<父级标题> <当前节标题> <entities>"`。

例：sec 91「主题园区与核心设施」（假设父级是「上海迪士尼乐园」）→ phrase 含"上海迪士尼乐园 主题园区与核心设施"，与 alt "上海迪士尼乐园" 相似度显著提升。

**落点**：
- `relevance.py` 新增 `_find_parent_heading(sections, idx) -> str`：按 `#` 数量向上找最近更高级标题。
- `semantic_matcher.py` `_canonical_section_phrase` 增 `parent_heading` 参数，拼进 phrase。
- `postprocessing.py:256-264` 构造 `section_phrases` 时传入父级标题。

**边界**：无父级（已是最顶级标题）→ 不拼；父级标题本身无实体也无妨（多拼一段文本不损害匹配，mpnet 对多余 token 鲁棒）。

#### 改动 6：每节采纳图上限 top3

**根因**：无每节上限，cite 39 一节绑 68 张。

**修复**：每节最终采纳 ≤3 张，按相似度 score 取 top3。

**落点**：`postprocessing.py:533` 构造 `placements` 处。当前从 binding 全量展开；改为按 (sidx, score) 分组，每 sidx 取 score top3。

**实现要点**：
- BIND_ADOPTED 当前没透传 score（CANDIDATE_SCORED_DETAIL 有，BIND_ADOPTED 没带）。需在 binding 数据结构里同时记 score：`binding[url].append((num, sidx, score))`。
- 构造 placements 时：`for sidx, group in groupby_sidx: top3 = sorted(group, key=score, desc)[:3]`。
- 加 `[IMG-TRACE] SECTION_CAP research=<id> sec=<sidx> candidates=<N> kept=3 dropped=<N-3>` 探针，记录截断。

**边界**：某节候选 <3 → 全保留；top3 内同分 → 取先到的（稳定排序）。

---

### C 组：图像呈现（尺寸落盘 + caption）

#### 改动 7：超尺寸图等比缩小后落盘（修复半失效的尺寸限制）

**根因**：`store.py:376-484` 注释明确"long-side cap no longer enforced here"，`rewrite_markdown` 返回纯 `![alt](route)`，超尺寸图按原大小渲染，仅靠 WebUI CSS `max-width` 兜底——**PDF 导出无此兜底，图会溢出页面**。

**修复**：
1. `persist()` 阶段：超尺寸图（长边 > `_MAX_DISPLAY_PX=600`）用 PIL **等比缩小后落盘**（不是落原图）。
2. `rewrite_markdown`：返回 HTML `<img>` 带 width/height，确保双端按缩小后尺寸渲染。

**落点**：
- `store.py` `persist()`：PIL resize（长边 → 600，按比例缩另一边），写缩小后的 bytes 到 `/data/images/`。
- `store.py` `rewrite_markdown` 的 `repl()`：超尺寸分支返回 `<img src="{route}" alt="{safe_alt}" width="{w}" height="{h}" loading="lazy" />`（w/h 为缩小后尺寸）。

**与改动 8 的耦合**：`rewrite_markdown` 的返回格式由改动 7（HTML img）+ 改动 8（figure/figcaption 包裹）**合并设计**，见下方统一格式。

#### 改动 8：图下 alt 文字说明（caption）

**目标**：每张插入的图，将其 alt 原文以小一号字体放图下方作说明。WebUI + PDF 双端可渲染。

**格式**（HTML figure，WeasyPrint + WebUI 均原生支持）：

```python
return (
    f'<figure class="ldr-img">'
    f'<img src="{route}" alt="{safe_alt}" width="{w}" height="{h}" loading="lazy" />'
    f'<figcaption>{safe_alt}</figcaption>'
    f'</figure>'
)
```

**CSS**（WebUI 注入一份样式表；PDF 导出时 WeasyPrint 用同一份）：

```css
.ldr-img { margin: 1em auto; text-align: center; }
.ldr-img img { max-width: 100%; height: auto; }
.ldr-img figcaption {
    font-size: 0.85em; color: #666; margin-top: 0.3em;
    text-align: center;
}
```

**落点**：
- `store.py` `rewrite_markdown` 的 `repl()`：三个返回分支统一改为 `<figure>` 包裹（见下方统一格式）。
- WebUI 侧：在 markdown 渲染容器的 CSS 里加 `.ldr-img` 规则（定位 WebUI 的 markdown 样式文件，执行时确认）。
- PDF 导出侧：确认 WeasyPrint 导出链路是否吃这份 CSS；若不吃，在导出模板里注入。

**统一返回格式（改动 7+8 合并）**：
```python
# 三分支（unknown size / under cap / resized）统一：
w, h = size if size else ("", "")   # unknown 时不带 width/height
size_attrs = f' width="{w}" height="{h}"' if size else ""
return (
    f'<figure class="ldr-img">'
    f'<img src="{route}" alt="{safe_alt}"{size_attrs} loading="lazy" />'
    f'<figcaption>{safe_alt}</figcaption>'
    f'</figure>'
)
```

**注意**：超尺寸图若 size 未知（PIL probe 失败），无法 resize，退化为不带 width/height 的 figure（CSS `max-width:100%` 兜底）。但这与改动 7 的"必须缩小落盘"有张力——执行时确认：persist 失败的图是否应整体丢弃（当前是 REWRITE_KEEP 原样保留）。

---

## 测试覆盖

### A 组
- **改动 1**：`test_structural_domain_skipped`（instagram URL 不进 fetch、发 STRUCTURAL_SKIP）；`test_c_class_domain_not_skipped`（wikipedia 不被剔除）；`test_structural_skip_does_not_affect_text`（正文回归）。
- **改动 2**：更新 Firecrawl 客户端超时测试断言。
- **改动 3**：`test_attach_near_match_trailing_slash`；`test_no_near_match_when_query_differs`（`?id=1` vs `?id=2` 不发）；`test_existing_attach_miss_still_emitted`。

### B 组
- **改动 4**：无单测（prompt 改动）；验证靠跑 detailed-mode 任务，grep 报告 markdown 确认无 box-drawing 字符（`grep -P '[┌┐└┘│─═◄►▼├]' report.md` 为空）。
- **改动 5**：`test_section_phrase_includes_parent_heading`（`### 子节` 在 `## 父节` 下 → phrase 含父节）；`test_top_level_section_no_parent`（无父级 → 不拼，不报错）；回归现有语义匹配测试。
- **改动 6**：`test_per_section_top3_cap`（一节 5 张候选 score 不同 → 仅 top3 进 placements）；`test_section_cap_probe_emitted`（发 SECTION_CAP）；`test_under3_all_kept`（候选 2 张 → 全保留）。

### C 组
- **改动 7**：`test_oversized_image_resized_on_persist`（长边 1200px → 落盘图长边 600，比例保持）；`test_rewrite_markdown_emits_img_with_dimensions`（返回 HTML img 带 width/height）。
- **改动 8**：`test_figure_caption_present`（返回含 `<figure>` + `<figcaption>alt</figcaption>`）；`test_caption_escapes_alt`（alt 含 `<`/`"` → HTML 转义）；`test_pdf_render Shows_caption`（PDF 导出含 caption 文本——若 PDF 测试基建支持）。

---

## 验证（重建镜像 + 重启容器后，跑 detailed-mode 任务）

```bash
docker logs ldr-local --since <run> 2>&1 | grep -E \
  "STRUCTURAL_SKIP|DEFERRED_FILL.*done|ATTACH_MISS|ATTACH_NEAR_MATCH|SECTION_CAP|RESIZE chosen|via=none.*elapsed"
```

**成功标准**：
- `STRUCTURAL_SKIP` 出现，命中域 ⊂ 11 个黑名单；wikipedia/ctrip 不出现。
- `DEFERRED_FILL done filled=N/M` 的 M 较优化前减少。
- `via=none` 的 `elapsed_s` ≤15s。
- `SECTION_CAP` 出现，被截断的节 candidates>3、kept=3。
- 报告 markdown 无 box-drawing 字符（`grep -P '[┌┐└┘│─═◄►▼├]'` 为空）。
- 落盘图长边 ≤600px（`identify /data/images/<rid>/*.jpg` 全部 ≤600）。
- 报告 HTML 含 `<figure class="ldr-img">` + `<figcaption>`。
- WebUI + PDF 导出均显示图下小字 caption。
- `ATTACH_NEAR_MATCH` 在有 canonical 近似时出现；query 差异的 miss 不产生。
- 现有行为不退化（成功 attach、落盘图数合理）。

## 本次任务（366bcc14）的预期收益
- 黑名单命中约 12 URL → 省 ~240s；Firecrawl 超时省 ~120s；合计 **省 ~360s（6 分钟）**。
- 每节 top3：cite 39 从 68 张降到 3 张，报告更精炼。
- 尺寸落盘：PDF 导出图不再溢出。
- 父级标题：sec 91/93 等无实体节的匹配命中率提升（具体数需验证）。

## 不在范围内（明确排除）
- ❌ C 类熔断/动态学习/持久化
- ❌ attach 匹配逻辑改动（仍裸 `==`）
- ❌ eTLD+1 域兜底匹配 / 丢 query 参数（防错配红线）
- ❌ ASCII 图后处理剥离（先靠 prompt；验证失败再议）
- ❌ "分两次抓取"（已证伪）

## 待用户补充
> 用户可继续追加待修内容。补充请含：现象/日志证据、期望行为、是否动匹配逻辑。补充内容并入本 spec 对应组并追加测试。

## 自检
- **占位符**：无 TBD；黑名单 11 域、父级判定规则、top3 阈值、CSS 类名均已具体。
- **内部一致**：8 项落点不冲突；改动 7+8 在 `rewrite_markdown` 合并设计；改动 5 的父级标题与现有 `_split_sections` 扁平结构兼容（靠 `#` 数量推断）。
- **范围**：8 项分 3 组（抓取/质量/呈现），可由一个 plan 承载，但实现时建议按组分 Task（B/C 组改的是不同文件，可并行）。
- **歧义**：改动 7"persist 失败的图是否丢弃"标注了待执行时确认的张力点。
