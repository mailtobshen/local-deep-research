# 图片门控最终方案 v5（A+B，待审核）

> 状态：**草稿 v5，待用户审核** ｜ 日期：2026-08-01
> 取代 v1–v4。本版基于已逐行核实的代码事实，无臆测项。
> 关联记忆：`[[semantic-matcher-crosslingual-threshold]]`、`[[per-section-image-same-source-filter]]`、`[[img-trace-observability]]`
> 触发研究：`4b97170e-cde9-47ca-9901-c74af3c1a866`（97 候选 → kept=0）

---

## 0. 总体目标（用户确认基准）

研究输出中文 Markdown 时，把引用网页的**关键性佐证图**（段落提到的人物/组织/物品/事件）下载到本地、嵌入正文正确位置。约束：
1. proxy 后大量非中文网页作引用素材（越/英/日 alt）。
2. 正文强制中文 → alt（原文）与段落（中文）固定跨语言困境。
3. **宁缺毋滥**：要求准确，不要低相关图。
4. 最终插图 source_url 必须与所在段落 `[N]` 引用 source_url **同源**（硬约束）。
5. 语义过滤走**实体级双路径**：① alt 实体翻译成中文 → 与段落中文实体比对；② alt 实体保留原文 → 跨语言比对；**取最大相似度**。
6. alt 处理：**先做 NER（字符数≥2），后续判断基于提取的实体，而非整段 alt**。
7. 纯文本路径，不调 vision。
8. 不推倒重来，基于现有方案调整。

## 1. 根因（已核实）

当前 `semantic_match_filter`（semantic_matcher.py:344）直接 `model.encode([c.alt])` 编码**整段 alt 文本**，与段落实体向量比 cosine。整段向量在跨语言下分数 0.44–0.54 < 阈值 0.6 → 97 候选 0 kept。

**关键发现**：现有代码已有 alt 实体提取器 `_extract_alt_entities`（relevance.py:809），但被旁路未用。本方案的核心就是**接回它**，把比对从"整段向量"改为"实体级双路径"。

## 2. 已核实的现有基础设施（决定改动量）

| 组件 | 位置 | 状态 | 本方案用途 |
|---|---|---|---|
| `_extract_alt_entities` | relevance.py:809 | 存在，被旁路 | **接回**：alt NER（字符≥2 门槛已满足，见下） |
| 字符≥2 门槛 | relevance.py:804 `if len(span)>=2` | ✅ 已是 ≥2 | 满足目标6，不改 |
| CJK 实体提取 | relevance.py:767-783 | ✅ 连续串+2/3字符前后缀 | 越/英/日 alt 实体提取可用 |
| `build_report_entity_pool` | semantic_matcher.py:133 | ✅ 在用 | 段落 NER，不动 |
| `filter_candidates_by_section_citations` | relevance.py:600 | ✅ 在用（被 best_idx 限局部） | 同源初筛，提前+改全局 |
| `_cosine` | semantic_matcher.py:253 | 单向量一对一 | 复用，外加实体级聚合（新增） |
| extractor 噪音过滤 | extractor.py | ✅ 尺寸/黑名单/DOM作用域 | 不动 |
| enhancer + `_dedupe_images` | enhancer.py / postprocessing.py | ✅ "never force" + URL去重 | 不动 |

**不存在、需新增的**：① alt 实体批量翻译（路径1）；② 实体级多对多相似度聚合。

## 3. 方案设计

### 第 0 步 — alt NER（接回现有 `_extract_alt_entities`）

每张带 alt 候选：`alt_entities = _extract_alt_entities(alt, context_entities=段落实体集)`。
- 字符数≥2 门槛已由现有函数满足（relevance.py:804）。
- 后续所有判断基于 `alt_entities`，**不再用整段 alt**。
- 若 `alt_entities` 为空 → 该候选语义判定直接不通过（无实体可比对）。

### 第 1 步 — 严格同源初筛（全局，先跑，语言无关）

对每个非跳过段落，用 `filter_candidates_by_section_citations` 扫**全量**候选，按该段落 `[N]` 引用集判 eTLD+1 同源 → 同源者进入该段落候选池 `per_section_candidates[sidx]`。
- 满足目标4（同源硬约束）。
- 语言无关，跨语言图不受影响。

### 第 2 步 — 实体级双路径语义相关性（在段落池里，后跑）

对每段池内候选，用其 `alt_entities` 做双路径：

```
路径1（翻译对齐）:
  alt_entities → 批量翻译成中文（新增，单次 LLM 调用，按实体哈希缓存）
  → 每个实体编码 → 与该段落中文实体做多对多相似度 → 取最大

路径2（原文跨语言）:
  alt_entities 原文编码 → 与段落实体做多对多相似度 → 取最大

候选相似度 = max(路径1最大, 路径2最大)
≥ 0.45 → 相关，留在池；否则从该池移除
```

- **多对多聚合规则**：取最大实体对相似度（alt 任一实体与段落任一实体最高分）。理由：一张佐证图通常佐证单个要素（alt=`广州塔珠江夜景`，只要"广州塔"对上即可），取最大符合"佐证单要素"语义，且与"宁缺毋滥"不冲突（阈值 0.45 仍把关）。
- **阈值 0.45**：基于跨语言实测 0.44–0.54（记忆 `[[semantic-matcher-crosslingual-threshold]]`），实施后用真实研究 IMG-TRACE 校准。
- **删 margin**：段落归属由第1步同源决定，语义不再决定"归哪段"，margin（防语义归段歧义）失效。

### 第 3 步 — enhancer + 去重（不变）

`enhancer.enhance(per_section_candidates)` 每段池内 "never force" 自挑 → `_dedupe_images` + `CHOSEN_DROP` 去重。

## 4. 为什么双路径解决跨语言

| alt 情况 | 路径1（翻译） | 路径2（原文） | 取最大 |
|---|---|---|---|
| alt 中文实体 | 翻译≈自身，高分 | 跨语言对齐，中高分 | 高 ✅ |
| alt 越南语实体 `part Memory` | 翻译成中文→对齐高分 | mpnet 跨语言，中分 | 高 ✅ |
| alt 英文实体 `Canton Tower` | 翻译"广州塔"→对齐 | 跨语言对齐 | 高 ✅ |
| 翻译失真/丢信息 | 低 | 原文兜底 | 中 ✅ |

取最大 = 任一路径对上即过，互为兜底。

## 5. 改动清单（surgical，2 文件 + 翻译模块 + 测试）

| 文件 | 改动 |
|---|---|
| `src/local_deep_research/images/semantic_matcher.py` | 1) `DEFAULT_THRESHOLD` 0.6→0.45；2) 新增 `_entity_max_similarity(alt_entities, section_entities)`（多对多取最大，复用 `_cosine` + `_encode_phrase_cached`）；3) 新增 `_translate_entities(entities, llm)`（批量翻译+缓存）；4) 重写 `semantic_match_filter` 为双路径实体级：输入 alt_entities，路径1（翻译）+路径2（原文）取最大，删 margin/同源（同源已移第1步） |
| `src/local_deep_research/images/postprocessing.py` | 1) 第1步同源过滤提前：`filter_candidates_by_section_citations` 扫全量候选归位到段落；2) 第0步 alt NER：对每候选调 `_extract_alt_entities`；3) 第2步语义过滤改为对每段池内候选的双路径判定；4) 移除 best_idx/_keep_per_section 中间结构（段落归属现由同源决定）；5) 保留 SECTION_INDEX_DRIFT 守卫、_skipped_sections、所有 IMG-TRACE 事件 |
| `src/local_deep_research/images/translate.py`（新增） | `_translate_entities`：收集所有非中文实体，单次 LLM 调用翻译（prompt 逐行返回中文），LRU 缓存（按实体哈希）。复用 `get_llm()` |
| `tests/images/test_semantic_matcher.py` + `test_postprocessing_e2e.py` | 双路径命中、取最大、多对多聚合、空实体丢弃、同源前置、阈值/margin 用例调整 |

**不改动**：`relevance.py`（`_extract_alt_entities`/`filter_candidates_by_section_citations` 本体已正确）、`enhancer.py`、`extractor.py`、`store.py`、`bank.py`、提示词。

## 6. IMG-TRACE 可观测性

- 第0步：`ALT_NER research=… candidates=97 with_entities=… empty=…`
- 第1步：`PER_SECTION_CANDIDATES`（已有）+ `total_after_citation_filter`
- 第2步：`ENTITY_MATCH research=… raw=池大小 kept=N path1_only=A path2_only=B both=C low=D`（双路径命中分布）
- 第3步：现有 `SECTION_ENHANCE`/`DEDUPE`/`ENHANCE`
- 复用 `CANDIDATE_JSON`（`LDR_IMG_TRACE_CANDIDATES=1`）逐条记录 alt_entities / 命中路径 / 相似度。

## 7. 预期效果

| 指标 | 当前 | 改造后预估 |
|---|---|---|
| 97 候选 → 同源归位 | 0 | 24（第1步） |
| → 实体双路径≥0.45 留池 | 0 | ~18–22（空实体/低相关被去） |
| → enhancer 最终插入 | 0 | enhancer 自判，预估 5–10 |

噪音由 extractor 入库前过滤 + enhancer "never force" 把关。

## 8. 风险与边界

- **翻译 LLM 调用**：每研究一次批量翻译所有非中文实体（非每图一次），成本可控。失败时降级为仅路径2（原文），不阻塞。
- **多对多聚合取最大的松紧**：取最大偏宽松，靠阈值 0.45 把关；若实测噪音多，可改"取最大 + 要求第二高实体非强冲突"（参数可调，非逻辑）。
- **阈值 0.45 校准**：参数，实施后 IMG-TRACE 复核。
- **同源多段落**：候选进多池，由 `_dedupe_images`（URL 至多一次）兜底。
- **残留**：被引用网页侧栏图若同源+实体过阈，仍可能进 enhancer 池；由 enhancer + extractor DOM 作用域把关。纯文本路径固有盲区。

## 9. 验证计划

1. **单测**：
   - alt NER：`alt='广州塔珠江夜景'` → 实体含 `广州塔`/`珠江`等（≥2 字符）。
   - 双路径：越南语实体，路径1（翻译后）高分；英文实体，两路径都中高分；取最大正确。
   - 多对多聚合：alt 多实体 vs 段落多实体 → 取最大实体对。
   - 空实体：alt 无≥2字符实体 → 语义不通过。
   - 同源前置：同源但实体分低的候选仍进段落池（由 enhancer 决定）。
2. **e2e 回放**（`test_postprocessing_e2e.py`，本次 `4b97170e` 数据）：
   - 断言 24 同源图进 per-section 池。
   - 断言 LongWriter/visitbeijing 等相关图实体双路径过阈。
3. **真实研究**：`LDR_IMG_TRACE_CANDIDATES=1` 跑一次，核对 ENTITY_MATCH 路径分布 + 正文插图数。

---

**审核请求**：请确认 §3（alt NER → 同源前置 → 实体双路径 → enhancer）、§3 多对多聚合取最大、§5 改动清单、§8 风险。确认后按 §5 在 `main` 分支小步实施：
1. alt NER 接回 + 单测
2. 同源前置（postprocessing 顺序反转）+ 单测
3. 实体双路径 + 翻译模块 + 单测
4. e2e 回放 + 真实研究 + 阈值校准
