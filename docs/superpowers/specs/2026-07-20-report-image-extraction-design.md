# 设计文档：报告真实图片提取与本地镜像管理

**日期**：2026-07-20
**状态**：待评审
**分支**：i18n-zh-translation

## 1. 背景与问题

用户用 LDR 生成"广州旅游景点介绍（图文并茂）"报告后，WebUI 里所有图片都显示不出来。

**根因调查结论**（已用证据确认）：

1. 报告 markdown 里的 8 张图片 URL 全是 **LLM 幻觉生成**的，指向 `upload.wikimedia.org` 上**根本不存在**的文件（Wikimedia API 返回 `missing`，原始图 404，缩略图 400）。
2. LDR **没有任何"找图片/插入图片"的能力**：`FirecrawlClient.scrape()` 只请求 `formats: ["markdown"]`，丢弃了页面 HTML 里的 `<img>`；报告生成 prompt 里也没有图片素材。LLM 为满足"图文并茂"要求只能凭空编 URL。
3. （次要）CSP `img-src 'self' data:` 也拦截外部图，但这不是本报告不显示图的主因——URL 本身是死的。

**结论**：必须让 LDR 真正获取真实图片，而不是让 LLM 编 URL。

## 2. 目标与非目标

### 目标
- 从研究过程已抓取的源页面 HTML 中提取真实 `<img>`（零额外搜索网络开销）。
- 让报告 LLM 基于真实图片素材（alt + 来源）选图、插图。
- 选中的图下载到本地镜像，WebUI 从本地加载（根治外链 403/反盗链/被删/CSP 问题）。
- 图片与研究任务绑定，支持按研究任务查询图片集合。
- 删除研究任务时级联删除其关联图片（DB 行 + 本地文件）。
- alt 缺失的图，可配置 vision LLM 兜底看图生成描述后再选。

### 非目标（本次不做）
- 不做额外的图片搜索引擎调用（Firecrawl /v1/search 搜图、Bing Image 等）。
- 不做多模态"看图写正文"，vision 仅用于补 alt。
- 不做跨研究报告的图片复用/全局图库。
- 不做 WebUI 图片管理页面（仅提供查询 API；前端管理 UI 留作后续迭代，本次只保证报告内图片能正常显示 + API 可查）。

## 3. 关键决策（来自需求澄清）

| 维度 | 决策 |
|---|---|
| 图片来源 | 仅从已抓取源页面 HTML 提取 |
| 提取方式 | `scrape()` 增加 `html` 格式，解析 HTML |
| 选图策略 | alt 文本优先（文本 LLM 判断）；alt 缺失由 vision LLM 兜底 |
| Vision 配置 | 新增 setting `report.image_vision_model`，空=禁用兜底 |
| 功能开关 | 新增 setting `report.enable_images`，**默认 false** |
| 存放 | 选中的图下载到本地 `/data/images/<research_id>/`，元数据入 DB |
| 下载时机 | 只下载被选中的图（两阶段：先报告选图，再 vision 兜底，最后落盘） |
| Vision 流程 | 兜底补完 alt 后**重新喂 LLM 增补选图**，保证质量 |
| WebUI 渲染 | 报告 markdown 内图片 URL 改写为本地路由 `/images/<hash>`，走 `img-src 'self'` |

## 4. 架构总览

新增一个与研究流程正交的"图片素材"子系统，分四个阶段：

```
阶段 0 — 研究抓取期（随现有抓取流程）
  scrape() → {markdown, html}
  ImageExtractor.extract(html, src_url, src_title) → 图片清单（仅元数据，不下载）
  ImageBank.add(research_id, 清单)   去重，记录每图来源 source_url

阶段 1 — 报告后处理增强（统一在 run_research_process，对所有 strategy 通用）
  在 run_research_process 拿到 clean_markdown 之后、写库之前（research_service.py
  formatter 格式化之前），插入一个独立的图片增强步骤：
  ImageEnhancer.enhance(markdown, image_bank, vision_describer) → 带 ![](真实url) 的 markdown
    - 把 ImageBank.candidates_with_alt() 的图（url + alt + 源标题）连同报告 markdown
      一起喂给一次 LLM，让它在合适位置插图，只准用清单里的真实 url
  注：WebUI 报告不在 IntegratedReportGenerator 生成（那是 benchmarks 死码路径），
  实际由各 strategy 产出 formatted_findings 后拼成；合成分散在多个 strategy 内，
  因此图片注入走后处理（收敛一处、通用），而非在每个 strategy 的合成 prompt 改。

阶段 2 — Vision 兜底（仅当 report.image_vision_model 已配）
  若 ImageBank 有 alt 缺失的候选图：先下载（限质限量）→ VisionDescriber 生成 alt
  → 回填 ImageBank → 把这些"刚补 alt 的图"加入清单，再跑一次阶段 1 的 LLM 增补选图

阶段 3 — 落盘选中图
  解析最终报告 markdown 里的 ![](url)
  这些 url 对应的图：下载到 /data/images/<research_id>/<hash>.<ext>
  写 Image 表；markdown 内 url 改写为 /images/<hash>

WebUI 渲染
  浏览器请求 /images/<hash> → 后端从本地文件返回（img-src 'self'）

管理
  GET  /api/research/<id>/images   列出该研究所有图
  DELETE research                  级联删 Image 行 + 删本地文件目录
```

**为什么是后处理而非 strategy 内注入**：探索代码发现 WebUI 报告由 `run_research_process`（`research_service.py:300`）驱动，最终内容 = 各 strategy 产出的 `formatted_findings`。LLM 合成分散在 `parallel_search_strategy`、`focused_iteration_strategy` 等多个 strategy 的 "Final synthesis" phase，逐个改注入点既发散又易漏、且 vision 二轮调用难统一挂载。改为在 `run_research_process` 的后处理位置统一增强，一次 LLM 调用、所有 strategy 通用。

## 5. 组件设计

三个新组件，单一职责、可独立单测。全部放在新目录 `src/local_deep_research/images/`。

### 5.1 ImageExtractor（纯函数式）

**职责**：HTML 字符串 → 标准化图片元数据清单。无 IO，无状态。

```python
# src/local_deep_research/images/extractor.py
from dataclasses import dataclass
from typing import List, Optional
from bs4 import BeautifulSoup

@dataclass
class ExtractedImage:
    url: str            # 绝对 URL（相对路径已用 source_url 解析）
    alt: str            # alt 文本，可能为空字符串
    source_url: str     # 该图所在源页面 URL
    source_title: str   # 源页面标题
    width: Optional[int]   # 从 width 属性/style 解析，用于过滤小图标
    height: Optional[int]

def extract_images(html: str, source_url: str, source_title: str) -> List[ExtractedImage]:
    """
    从 HTML 解析 <img>，返回标准化清单。

    过滤规则（丢弃）：
      - data: URI（base64 内嵌，通常是图标/追踪像素）
      - 无法解析成绝对 URL 的相对路径
      - 明显的非内容图：width 或 height < 50px（图标/像素图）
      - URL 命中黑名单关键字：'logo'、'icon'、'avatar'、'sprite'、'pixel'、'tracker'、'blank.gif'
      - 扩展名为 .svg/.gif 且尺寸过小（多为 UI 元素）
    """
```

**决策点**：过滤阈值（50px、黑名单关键字）先取保守值，可在 setting 里暴露但 MVP 硬编码。

### 5.2 ImageBank（内存聚合器）

**职责**：跨抓取聚合图片元数据；按 url 去重；按 alt 有/无分组提供候选；回填 vision 生成的 alt。

**说明**：候选清单是**全局**的（不按 section 切分）。阶段 1 把所有有 alt 的图整体喂给每个 section 的 prompt，由报告 LLM 自行判断哪些图与该 section 相关。这避免了"图与 section 归属"的机械匹配难题，与 §4 流程一致。

```python
# src/local_deep_research/images/bank.py
class ImageBank:
    def __init__(self) -> None: ...
    def add(self, images: List[ExtractedImage]) -> None:
        """加入清单，按 url 去重（同 url 只保留首次，合并来源）。"""
    def candidates_with_alt(self) -> List[ExtractedImage]:
        """返回所有 alt 非空的图（阶段 1 喂报告 LLM）。"""
    def candidates_without_alt(self, limit: int = 20) -> List[ExtractedImage]:
        """返回 alt 为空的图（阶段 2a 喂 vision），限量。"""
    def set_alt(self, url: str, alt: str) -> None:
        """vision 回填 alt 后调用。"""
    def all_urls(self) -> List[str]:
        """所有已知图 url（阶段 2b 落盘时按报告实际引用取交集）。"""
```

**生命周期**：随 `AdvancedSearchSystem` 实例存活，研究结束销毁。不持久化（持久化靠 DB Image 表，由落盘阶段写入）。

### 5.3 VisionDescriber

**职责**：alt 缺失时，下载图字节 → 调 vision LLM → 返回 alt 文本。未配 vision 模型时返回 None（禁用兜底）。

```python
# src/local_deep_research/images/vision.py
class VisionDescriber:
    def __init__(self, model_name: Optional[str]) -> None:
        """model_name 为 None 或空 → self.enabled=False。"""
    @property
    def enabled(self) -> bool: ...
    def describe(self, image_url: str) -> Optional[str]:
        """
        下载图（经 safe_requests，走代理/SSRF 规则），转 base64，
        调 vision LLM 生成 ≤30 字中文 alt。失败返回 None。
        """
```

**注意**：vision LLM 的实例化复用 `config.llm_config.get_llm()`，但需要支持 vision 的模型；`model_name` 来自 setting。LDR 目前无 vision 配置位，这是首个。

### 5.4 ImageEnhancer（后处理增强，阶段 1+2 的编排者）

**职责**：编排整个后处理增强流程——一次 LLM 调用把图片清单插入报告；vision 启用时先补 alt 再增补一轮。这是 `run_research_process` 后处理位置的单一入口。

```python
# src/local_deep_research/images/enhancer.py
class ImageEnhancer:
    def __init__(self, llm, vision: VisionDescriber) -> None: ...
    def enhance(self, markdown: str, bank: ImageBank) -> str:
        """
        阶段 1：把 bank.candidates_with_alt()（url + alt + 源标题）连同 markdown 喂一次 LLM，
                prompt 强约束：只能在合适位置插入清单内已有的真实 url，禁止编造新 url、
                禁止改写正文事实。返回带 ![](真实url) 的 markdown。
        阶段 2（vision.enabled 且 bank 有 alt 缺失图）：
                对 candidates_without_alt() 逐个 vision.describe → set_alt 回填；
                把新补 alt 的图加入清单，再跑一次阶段 1 的 LLM 增补选图。
        任一阶段失败：返回原 markdown 不报错（降级为纯文本报告）。
        """
```

**LLM prompt 关键约束**：明确告知模型"只能使用下面清单中的图片 URL，不得编造、不得修改 URL"，从源头杜绝幻觉图链（本次 bug 的根因）。

### 5.5 ImageStore（落盘 + DB）

**职责**：把选中的图下载到本地、写 DB、改写报告 markdown 内的 url。

```python
# src/local_deep_research/images/store.py
class ImageStore:
    def __init__(self, research_id: str, base_dir: Path = Path("/data/images")) -> None: ...
    def persist(self, urls: List[str], alt_map: Dict[str, str]) -> PersistResult:
        """
        下载 urls 列表里的图到 /data/images/<research_id>/<sha1>.<ext>，
        写 Image 表，返回 {url -> local_route} 映射。
        单图下载失败跳过（不阻塞报告）。
        """
    def rewrite_markdown(self, markdown: str, url_to_route: Dict[str, str]) -> str:
        """把报告 markdown 里的 ![alt](原url) 替换成 ![alt](/images/<hash>)。"""
```

## 6. 数据模型

新增一张表，与 `research_history` 同库（用户加密 DB）。

```python
# src/local_deep_research/database/models/images.py  （新文件）
class Image(Base):
    __tablename__ = "research_images"

    id = Column(Integer, primary_key=True)
    research_id = Column(String(36), ForeignKey("research_history.id", ondelete="CASCADE"), nullable=False, index=True)
    original_url = Column(Text, nullable=False)        # 抓取到的原始图 URL
    local_path = Column(Text, nullable=False)          # 容器内绝对路径 /data/images/<rid>/<hash>.<ext>
    local_route = Column(Text, nullable=False)         # WebUI 访问路由 /images/<hash>.<ext>
    alt = Column(Text)                                  # 最终 alt（可能由 vision 补）
    source_url = Column(Text)                           # 图所在源页面 URL
    source_title = Column(Text)
    content_hash = Column(String(64), index=True)       # 图字节 sha1，用于跨研究去重（未来）
    width = Column(Integer)
    height = Column(Integer)
    created_at = Column(UtcDateTime, default=utcnow())
```

**迁移**：新增 alembic 迁移脚本 `0011_research_images.py`（最新为 `0010`）。外键 `ondelete="CASCADE"` 让 DB 层保证删 research 时级联删 image 行——但**本地文件**DB 管不到，需在应用层 `delete_research()` 加文件清理钩子（见 §8）。

## 7. 改动点（最小化）

| 文件 | 改动 |
|---|---|
| `research_library/downloaders/extraction/firecrawl_client.py` | `scrape()` 的 `formats` 改为 `["markdown", "html"]`；返回类型从 `Optional[str]` 改为 `Optional[Dict]`（`{markdown, html}`）。同步改调用方 `web_search_engines/engines/search_engine_firecrawl.py:151`。 |
| `web_search_engines/engines/search_engine_firecrawl.py` | scrape 调用点适配新返回结构（取 `result["markdown"]`），并把 `result["html"]` + source_url/title 喂给 ImageExtractor → ImageBank。 |
| `web/services/research_service.py` | `run_research_process` 中拿到 `clean_markdown` 后、formatter 格式化前（约 1085 行），插入后处理：`ImageEnhancer.enhance(clean_markdown, image_bank)` → `ImageStore.persist + rewrite_markdown`。由 `report.enable_images` 开关门控。 |
| `web/routes/research_routes.py` | `delete_research()`（915 行）增加级联删图：删本地 `/data/images/<research_id>/` 目录（DB 行靠外键 CASCADE）。 |
| `web/routes/` | 新增 `GET /images/<research_id>/<filename>` 路由（路径穿越防护 + 正确 content-type）。新增 `GET /api/research/<id>/images`（列出该研究图片）。 |
| `defaults/default_settings.json` | 新增 2 个 setting（见 §9）。 |
| `database/models/images.py` | 新增 Image 模型。 |
| `database/migrations/versions/0011_research_images.py` | 新增建表迁移。 |
| `security/security_headers.py` | 已改：`img-src 'self' data: https:`（保留；本地路由走 'self'，https 留作直链兜底）。 |
| `images/` 新目录 | extractor.py、bank.py、vision.py、enhancer.py、store.py、`__init__.py`。 |

## 8. 级联删除细节

`web/routes/research_routes.py` 的 `delete_research(research_id)`（删整条 research，915 行）：

1. 先查该 research 的 Image 行，收集 `local_path`。
2. 删 research_history 行（现有逻辑）→ DB 外键 CASCADE 自动删 Image 行。
3. 删本地文件：`shutil.rmtree(/data/images/<research_id>/, ignore_errors=True)`。

**边界**：若 research 无图，目录不存在，`ignore_errors=True` 安全跳过。

## 9. 新增 Setting

加到 `defaults/default_settings.json`：

```json
"report.enable_images": {
    "category": "report",
    "name": "Enable Report Images",
    "description": "Extract real images from source pages and embed them in reports. When off, reports are text-only (no images). Requires Firecrawl as the content fetcher.",
    "editable": true,
    "type": "APP",
    "ui_element": "checkbox",
    "value": false,
    "visible": true
},
"report.image_vision_model": {
    "category": "report",
    "name": "Vision Model for Image Alt Text",
    "description": "Model name with vision capability (e.g. gpt-4o, qwen-vl-max) used to describe images that have no alt text. Leave empty to disable vision fallback (images without alt are skipped).",
    "editable": true,
    "type": "APP",
    "ui_element": "text",
    "value": "",
    "visible": true
}
```

`report.enable_images=false` 时：整个图片子系统旁路（不提取、不注入、不下载），行为与当前完全一致。

## 10. 错误处理

- **单图下载失败**：`ImageStore.persist` 跳过该图，不阻塞报告；报告 markdown 里该 url 保留原样（外链，可能显示不出，但不破报告）。
- **vision 调用失败/超时**：`VisionDescriber.describe` 返回 None，该图保留无 alt 状态 → 不进入候选清单 → 不被选中（可接受降级）。
- **Firecrawl 未启用/无 html 返回**：`ImageExtractor.extract` 收到空 html → 返回空清单 → 报告纯文本（等同关闭功能）。
- **磁盘写失败**：`persist` 整体失败时，报告 markdown 不改写（保留原始外链 url），记 error 日志，不抛异常给上层。
- **研究线程密码问题**：复用现有 `open_user_database` 流程，不引入新的密码传递路径。

## 11. 测试策略

每个组件独立单测（`tests/images/`）：

- `test_extractor.py`：给定含各种 `<img>` 的 HTML 片段，断言过滤正确（保留内容图、丢弃图标/data:/小尺寸/黑名单关键字）。
- `test_bank.py`：去重、alt 有无分组、回填 alt、限量。
- `test_vision.py`：mock vision LLM，验证 enabled 判断、describe 返回、失败返回 None。
- `test_store.py`：mock 下载，验证落盘路径、DB 写入、markdown 改写（`![](原url)` → `![](/images/<hash>)`）、单图失败不阻塞。
- `test_cascade_delete.py`：删 research 后 DB 行消失 + 本地目录被删（用 tmp 路径）。
- 集成测试：`report.enable_images=true` 跑一遍小型研究，断言报告含 `![](/images/...)` 且本地文件存在。

## 12. 风险与权衡

- **抓取量增加**：scrape 多返回 html 字段，单次响应变大。实测 Firecrawl 对多数页面 html 字段开销可接受；若发现瓶颈，可加 setting 控制是否请求 html。
- **vision 成本**：仅对 alt 缺失图触发，且限量（≤20）；未配模型时零成本。
- **存储增长**：图片落盘，长期需关注。未来可加清理策略（如按 content_hash 全局去重、过期清理），本次不做。
- **本地图片路由的安全**：`/images/<filename>` 必须做路径穿越防护（filename 限定为 `<research_id>/<hash>.<ext>` 形态，拒绝 `..`）。
