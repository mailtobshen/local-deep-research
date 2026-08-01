# 编号锚定的图片流水线设计

**日期**: 2026-08-02
**状态**: 设计已确认，待审查
**前置**: 三项暂停已 commit（`1df26062`：inherited 继承 / ambiguous_match / no_source_url_match）

## 背景与问题

B3 replay 验证（research `4b97170e`「北京旅游景点」，97 张候选图）显示当前图片流水线采纳率 **0%**。根因诊断：

1. **LLM 引用幻觉**：报告 References 块含 2902 条引用，正文实际只用 10 个编号（`[[1]]`~`[[10]]`）。LLM 把 SearXNG 返回的噪声页（instagram/tiktok/github issue）张冠李戴写进引用位。
2. **跨语言语义门误杀**：中文报告 vs 英文/噪声 alt，cosine 相似度 0.44–0.54 < 0.6 阈值，`low_similarity` 杀 80/97。
3. **LLM enhancer 第二瓶颈**：通过门控的图进入 `ImageEnhancer.enhance`（LLM 猜位置），B3 中因 ollama 配置直接 `status=error`。

三项暂停后（commit `1df26062`）门控 kept 从 0 提升到 ~19，证明门控策略部分有效，但 LLM enhancer 与引用幻觉仍是根因。

## 系统目标

1. 研究报告正文 Markdown 的参考文献不能出现 LLM 幻觉。
2. 基于真实引用编号，从对应 `source_url` 的 `html_content` 提取 `<img>`，依据 alt 命名实体与引用编号所在 section 的（标题+段落）命名实体做相似度度量，达阈值入 BANK。
3. 在每个带引用编号的 section 合适位置插入相关图片。
4. 全文检查，去除重复插入的图片。

## 关键架构决策（已确认）

- **无补抓、无新持久化、无 DB migration**：`enhance_report_with_images` 在同一进程内收到 `results`，其 `findings[].search_results[].html_content`（图片源头）未丢失。B3 读空 DB 是独立脚本限制，非真实流水线问题。设计是对现有进程内数据流的重排。
- **信任正文 `[[N]]` 编号为锚点**：它们解析到同进程内存里的真实 search_results。References 块清洗（1A）+ 综合 LLM 约束（1B）负责减少幻觉。
- **删除 LLM 增强 → 改为屏蔽**：图片位置由编号物理决定，不需 LLM。`ImageEnhancer` 屏蔽（保留代码、绕过调用），非删除。
- **孤儿 section（无 `[[N]]`）不配图**（与意见1一致）。
- **代码组织方案 A**：原地重写 `enhance_report_with_images` 函数体，入口签名不变（`research_service.py:1249` 调用处零改动）。

## 整体架构与数据流

```
research_service.py:1249 传入
  clean_markdown (正文 [[N]] + References 块)
  results (findings[].search_results[].html_content ← 图片源头,同进程内存)
        │
        ▼
阶段0  引用映射构建  build_citation_index(markdown, results)
       num -> source_url            (来自 References 块, 复用 _scan_references_block)
       section_idx -> [num]         (正文 [[N]] 反查, 复用 _split_sections/_section_offsets)
       source_url -> html_content   (来自 results, 真实抓取内容)
        │
        ▼
阶段1  References 清洗  sanitize_references(markdown)           (目标1A)
       正文未引用的编号 → 从 References 块删除 (2902→~10)
        │
        ▼
阶段2  编号驱动提图 + 语义门控入 BANK                            (目标2)
       对每个 (section_idx, [num]):
         url = num_to_url[num]; html = url_to_html[url]
         imgs = loads_images(html)
         对每张 img:
           score = cosine(嵌入(alt), 嵌入(编号N所在section的标题+段落实体))
           score >= threshold ? 入BANK(绑定 (num,section_idx)) : 丢 low_similarity
        │
        ▼
阶段3  编号物理定位插入  insert_images_by_section                (目标3, 无LLM)
       每张BANK图已绑定 section_idx → 在该section标题后插入 ![alt](url)
        │
        ▼
阶段4  全文去重  _dedupe_images                                  (目标4, 复用)
        │
        ▼
       返回增强后 markdown → ImageStore.persist (复用,不变)
```

**关键不变量**：
- 图片只来自正文真实引用过的编号对应的 source_url（杜绝噪声页面）。
- 一张图能否进 BANK 只由"alt vs 其编号所在 section"的相似度决定（唯一一道门，阈值门槛）。
- 图的位置由编号物理决定，不靠 LLM 猜。

**屏蔽项**（保留代码、绕过调用、加 paused 标记）：
- `enhancer.py` 的 `ImageEnhancer` 类 —— 不再实例化，文件/类/`__init__.py` 导出保留。
- `semantic_match_filter` —— 被新的"编号锚定单段语义度量"取代，函数保留但主流程不再调用。

**复用项**（不动）：
- `extractor.py`（extract_images / ExtractedImage）
- `serialize.py`（loads_images）
- `bank.py`（ImageBank）
- `store.py`（ImageStore.persist / rewrite_markdown）
- `relevance.py`（_scan_references_block / _split_sections / _section_offsets / build_report_entity_pool / _extract_alt_entities）
- `semantic_matcher.py`（get_model / _encode_phrase_cached / _cosine / _canonical_section_phrase）

## 阶段 0：引用映射构建

**接口**（新增纯函数，放 `relevance.py`，与现有引用解析同模块）：

```python
def build_citation_index(
    markdown: str,
    results: dict,
) -> tuple[
    dict[str, str],          # num -> source_url
    dict[int, list[str]],    # section_idx -> [num]
    dict[str, str],          # source_url -> html_content
]:
```

实现要点：
- `num -> source_url`：复用 `_scan_references_block(markdown)`。
- `section_idx -> [num]`：复用 `_split_sections` + `_section_offsets` 切节，对每节 body 扫 `[N]` 标记（复用 `extract_segment_sources` 现有的 `CITE_INLINE_RE` / `CITE_INLINE_GROUP_RE` 扫描逻辑，该正则从关联模块导入、已可用）。孤儿节 → 空列表。
- `source_url -> html_content`：遍历 `results["findings"][].search_results[]`，建 `{sr["url"]: sr["html_content"]}`。

**不变量**：阶段 0 只做对接，不做判断。

## 阶段 1：References 清洗（目标 1A）

**接口**（新增纯函数，放新文件 `images/reference_sanitizer.py`）：

```python
def sanitize_references(markdown: str) -> str:
    """删除 References 块里正文未引用的 [[N]] 条目,返回新 markdown。"""
```

实现要点：
- 用 `_scan_references_block` 找 References 块位置。
- 扫正文（References 块之前）实际用到的编号集合 `used_nums`。
- 重写 References 块：只保留 `N ∈ used_nums` 的行，**保留原编号**（正文 `[[7]]` 仍指向 `[7]`）。
- 同一 URL 多编号：不合并（合并会改变正文编号引用，风险大）。

边界：
- 没有 References 块 → 原样返回。
- 正文用了某编号但 References 块无该条目 → 不报错（阶段 2 因无 source_url 跳过）。

## 阶段 2：编号驱动提图 + 语义门控入 BANK（核心）

**数据流**：

```
对每个 (section_idx, [num]) in citation_index.section_to_nums:
  heading = sections[section_idx][0]                      # 来自 _split_sections
  section_entities = entity_pool[section_idx]             # 来自 build_report_entity_pool
  section_phrase = _canonical_section_phrase(heading, section_entities)
  对每个 num:
    url = num_to_url.get(num)
    html = url_to_html.get(url)
    imgs = loads_images(html or "")
    对每张 img:
      score = cosine(
        _encode_phrase_cached(img.alt),
        _encode_phrase_cached(section_phrase)
      )
      if score >= threshold:
        BANK.add([img]); binding[url] = (num, section_idx)
      else:
        drop(low_similarity)
```

**设计点**：
- **嵌入范围收窄到"编号所在单节"**：alt 只与该编号绑定的那一节比，而非全文 N 节选最佳。信号更强，误杀更少。
- **section 短语取法**：复用 `build_report_entity_pool` + `_canonical_section_phrase(heading, entities)`，不新增。
- **BANK 绑定信息用旁挂 dict** `{url: (num, section_idx)}`：绑定是流水线内部临时路由数据，不污染 `ExtractedImage` 数据类。
- **阈值 threshold**：先用现有 0.6 跑 B3 验证，用数据定，不预设新值。
- **同源/ambiguous 校验不做**（已暂停）。

## 阶段 3：编号物理定位插入（目标 3，无 LLM）

**接口**（新增纯函数，放 `postprocessing.py`）：

```python
def insert_images_by_section(
    markdown: str,
    placements: list[tuple[int, str, str]],  # (section_idx, url, alt) 按 section_idx 排序
) -> str:
```

插入规则（每个 section）：
- 定位 section 边界（复用 `_section_offsets`）。
- 在该 section 第一个 `##`/`###` 标题行之后插入图片。
- 多张图：依次插入（标题后、按 BANK 顺序）。
- 图片用 markdown `![{alt}]({url})`，alt 经 `_safe_alt` 清洗。

边界：
- 该 section 已有同 url 图 → 跳过。
- alt 为空 → 用 `source_title` 兜底，仍空则跳过。
- section_idx 越界 → 跳过。

**不变量**：插入位置完全由 `section_idx` 决定，不调用任何 LLM。

## 阶段 4：全文去重（目标 4）

**完全复用** `_dedupe_images(markdown)`：按 url 去重，保留首次出现，折叠多余空行。

## 错误处理

沿用容错骨架：整个 `enhance_report_with_images` 包在 `try/except Exception`，任何失败 → `status=error` + 返回原始 `clean_markdown`。

| 失败情形 | 处理 |
|---|---|
| `results` 无 findings / html_content 全空 | 记 `BANK_EMPTY`，原样返回 |
| References 块不存在 | 跳过阶段1，`num_to_url` 为空 → 无图 |
| 清洗解析异常 | 跳过清洗，用原始 References 继续 |
| HF 模型加载失败 | 全部图丢 `low_similarity` |
| 某编号无 html / 无 url | 跳过该编号 |
| section_idx 越界 / alt 空 | 跳过该图 |
| 任意未预期异常 | 外层 try/except 兜底 |

**不变量**：图片流水线任何失败绝不让研究报告生成失败。

## 测试策略

**1. 单元测试（纯函数，快）**
- `sanitize_references`：2902→10、保留原编号、无块原样返回。
- `build_citation_index`：三张映射正确、孤儿节空列表、html 缺失。
- `insert_images_by_section`：插入位置、多图、边界跳过。

**2. 语义门控单元测试（mock HF）**
- 沿用 `test_semantic_matcher.py` 的 `_fake_model` 模式：高相似→入 BANK 绑定 (num, section_idx)、低相似→丢。

**3. B3 回归（端到端，真实数据）**
- research `4b97170e`，97 候选。
- 标尺：阶段2 入 BANK 数、最终插入正文图数。
- 新方案阶段3 无 LLM，B3 能跑完整链到插入（不再有 ollama error）。

## 文件改动清单

**新增**：
- `images/reference_sanitizer.py`（~40 行）
- `tests/images/test_reference_sanitizer.py`
- `tests/images/test_citation_index.py`
- `tests/images/test_insert_images.py`

**修改**：
- `relevance.py`：加 `build_citation_index`（~30 行）
- `postprocessing.py`：重写 `enhance_report_with_images` 函数体 + `insert_images_by_section`，屏蔽 `ImageEnhancer` 调用
- `enhancer.py`：加 paused 标记注释（不删）

**不动（复用）**：
- `extractor.py` / `serialize.py` / `bank.py` / `store.py` / `semantic_matcher.py`

## IMG-TRACE 可观测性（保留）

沿用现有日志体系，新增覆盖新阶段：
- `CITATION_INDEX num=X sections=Y html_covered=Z`（阶段0）
- `REFERENCES_CLEANED before=A after=B`（阶段1）
- `CITATION_MATCH num=N imgs=K kept=M low_similarity=L`（阶段2，每编号）
- `INSERT placements=P skipped=S`（阶段3）

## 目标 1B（综合 LLM 约束，延后）

综合阶段给 LLM 传真实抓取过的 `[(url, title)]` 清单，约束每个 `[[N]]` 必须对应清单内真实 URL，禁止编造未使用的引用。

**延后理由**：1B 改综合 prompt，独立于图片流水线重写。本次设计聚焦图片流水线（目标1A/2/3/4）。1B 作为后续独立工作。

## 非目标

- 不改搜索阶段（SearXNG 噪声治理是独立问题，见 memory [[searxng-engine-suspend-tuning]]）。
- 不改抓取阶段（`_ensure_images_for_results` 全量抓取策略不变）。
- 不删除 `enhancer.py` / `semantic_match_filter`（屏蔽，待新方案验证后另议）。
