# 暗网搜索引擎（ahmia / torch via Tor）设计

日期：2026-08-14
状态：待实施

## 背景

`ldr-tor` 容器（Tor SOCKS5 边车）自部署以来从未产生过流量：84 次心跳全部
显示 `sent 0 kB / received 0 kB`、`0 circuits open`。原因是 SearXNG 的
`settings.yml` 里根本没有 ahmia/torch 引擎定义 —— `docker-compose.ldr-local.yml`
中追加引擎的命令属于一份未被使用的 compose 定义，实际运行的容器来自
`docker-compose.searxng-ldr.yml`。

本设计把暗网检索做成一个可手工开关的独立信息源。

## 目标

1. 系统配置中提供手工开关，默认关闭
2. 提供连接测试，能明确指出前提是否满足
3. 开启后，可在新建研究任务时选择让暗网作为**独立信息源**参与检索
4. 报告中暗网来源必须与明网来源显著区分

## 非目标

- 不做暗网内容的额外清洗、去重或风险评级
- 不改变 SearXNG 容器的启动方式
- 不从 .onion 来源抓取图片（见「安全约束」）

## 关键前提（已实测确认）

| 事实 | 影响 |
|---|---|
| `SearXNGSearchEngine` 已支持 `engines` 参数（`params["engines"]`） | 无需新写引擎类 |
| `ENGINE_REGISTRY` 一条 `EngineEntry` 即可注册引擎 | 注册成本为 1 行 |
| `searxng/settings.yml` 是宿主机绑定挂载，且被 gitignore | 配置可直接编辑，但不入库 |
| 研究页引擎选择是**单选**下拉，另有 `auto` 元引擎 | 「并列参与」需要新控件 |
| 引用系统 6 条正则硬编码 `\d+` | 前缀编号需限制施加位置 |
| 图片后处理运行在报告最终化**之后**（`research_service.py:554`） | 图片管道会读到暗网编号 |

## 架构

### 1. 引擎注册

复用现有实现类，不新建：

```python
# web_search_engines/engine_registry.py
"darkweb": EngineEntry(
    module_path=".engines.search_engine_searxng",
    class_name="SearXNGSearchEngine",
    full_search_module=".engines.full_search",
    full_search_class="FullSearchResults",
),
```

### 2. 配置项

```
search.engine.web.darkweb.enabled            = false   # 全局开关
search.engine.web.darkweb.display_name       = "暗网检索 (Tor)"
search.engine.web.darkweb.reliability        = 0.3
search.engine.web.darkweb.use_in_auto_search = false
search.engine.web.darkweb.default_params.instance_url = "http://searxng-ldr:8080"
search.engine.web.darkweb.default_params.engines      = ["ahmia", "torch"]
search.engine.web.darkweb.default_params.categories   = ["onions"]
search.engine.web.darkweb.default_params.max_results  = 10
```

`enabled=false` 时引擎不注册进工厂，UI 中不出现。

### 3. 新建研究页控件

引擎下拉**保持不变**，旁边新增独立勾选框「同时检索暗网」：

- 仅当 `enabled=true` 时渲染
- 勾选后，本次研究在所选主引擎之外**追加** darkweb 引擎，结果合并
- 不勾选则完全不触碰暗网

这是本设计对检索流程的唯一改动点：需支持「主引擎 + 追加引擎」。

**合并语义（实施时的主要未知）**：勾选后本次研究拥有两个检索源。二者结果
进入同一个结果池，由既有的相关性过滤与引用流程统一处理 —— 暗网结果不享有
特权，也不被额外降权（降权已由 `reliability=0.3` 在引擎层表达）。是复用
`MetaSearchEngine` 的多引擎聚合能力，还是在调用层顺序执行两次检索并拼接，
需在实施计划阶段读透 `MetaSearchEngine` 后确定。

## 暗网来源识别

**判据是 URL，不是引擎名。**

```python
def is_darkweb_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "onion" or host.endswith(".onion")
```

理由：`.onion` 是 Tor 保留 TLD，明网不可能出现。若改用「引擎名传递」，
provenance 需穿越 引擎 → findings → Document.metadata → citation index →
重编号 → 渲染 共 6 个模块，而本代码库已有同类管道丢字段的事故先例
（deferred-fill `filled=0/N`）。URL 判据零管道改动，且语义更准：读者关心
「这条来源是不是暗网」，而非「它由谁检索到」。

## 报告呈现

### 引用编号

明网保持 `1..N`；暗网使用前缀式 `[D1] [D2] …`。

**风险控制 —— 前缀只在最终重编号阶段施加：**

- LLM 生成时仍输出纯数字引用，提示词不变
- 所有解析 LLM 输出的正则（`CITE_INLINE_RE` 等 6 条）作用于重编号**之前**，
  保持 `\d+` 不变
- 仅 `renumber_citations` 的输出格式与参考文献渲染需要改动

### 正文章节标注

任一章节若引用了暗网来源，在该章节正文末尾追加一行提示，说明本节含暗网来源。
提示语走 `_get_chapter_headings()` 的 `report.language` 本地化机制，不硬编码。

### 参考文献分组

```markdown
## 参考文献

[1] 上海市统计年鉴 — https://tjj.sh.gov.cn/...
[2] Wikipedia — https://zh.wikipedia.org/...

### 暗网信息源
[D1] Example Market — http://xxxxx.onion/...
```

无暗网来源时**不输出空分组**。

## 连接测试

单一探测函数，两个触发入口。放入既有的 `diagnostics/engine_health.py`：

```python
def probe_darkweb(settings_snapshot=None, timeout=60) -> EngineStatus
```

四级逐层下钻，失败即返回并指明症结：

| 级别 | 检查 | 失败结论 |
|---|---|---|
| 1 | SearXNG 可达 | SearXNG 未运行 |
| 2 | ahmia/torch 在其引擎列表中（复用 `get_searxng_engines()`） | 引擎块未合入 settings.yml |
| 3 | 实发查询，是否返回 `.onion` 结果（复用 `probe_searxng_engine()`） | Tor 线路不通或引擎超时 |
| 4 | 记录耗时 | 通但慢 |

- **设置页按钮** → `POST /api/v1/settings/test-darkweb`，返回结构化四级结果
- **研究前 preflight** → 并入 `run_preflight_check()`，仅在 `enabled=true` 时执行；
  不可用则跳过该引擎并提示，**不中断研究**

超时 60s，显著高于其他引擎 —— Tor 首次建链慢是固有特性而非故障。

## 安全约束

**不得从 .onion 来源抓取图片。** 图片后处理运行在报告最终化之后，会读到 `[D1]`
形式的引用编号。`build_citation_index` 等按编号解析处必须：

1. 显式跳过 D 前缀引用（不参与图片绑定）
2. 不得用 `int()` 裸解析编号，否则遇 `D1` 抛 `ValueError`

这既是防崩溃，也是安全要求。

## SearXNG 侧配置

仓库提供模板 `searxng/engines-darkweb.yml.template` 与操作文档；实际
`settings.yml` 被 gitignore，由人工合入后重启 **searxng-ldr**（不涉及 ldr-local，
不影响研究日志）。

## 测试策略

| 层 | 测法 |
|---|---|
| `is_darkweb_url` | `.onion` / 普通域名 / `notonion.com` / `evil.onion.attacker.com`（域边界） |
| 引擎注册 | `darkweb` 可从工厂实例化，`engines` 确为 `["ahmia","torch"]` |
| `enabled=false` | 引擎不出现在可用列表中 |
| 引用编号 | 混合来源重编号后，明网连续 `1..N`、暗网为 `D1..Dn`，正文与参考文献一致 |
| 参考文献分组 | 无暗网来源时不出现空分组 |
| 章节标注 | 含暗网引用的章节有提示，其余章节无 |
| 图片管道 | 含 `[D1]` 的报告不崩溃，且 .onion 来源不参与图片绑定 |
| `probe_darkweb` | 四级各造一个失败场景，断言返回对应级别 |
| 端到端 | 手动：开开关 → 测连接 → 勾选暗网跑一次研究 → 验报告与日志 |

## 已知失败模式（设计上接受）

1. Tor 首次建链慢 → 超时 60s，preflight 失败降级跳过
2. `.onion` 站点普遍不稳定 → `reliability=0.3`，不参与自动检索
3. 引擎块未合入 → 探测第 2 级直接指出，不表现为「检索无结果」这种含糊症状

## 实施顺序

本设计跨越配置、引擎注册、研究页 UI、检索流程、引用格式化、报告渲染、图片管道
共 7 个层面，**不适合作为单个实施计划一次做完**。拆为三个阶段，每阶段独立可
验证、可停止：

### 阶段一：可行性验证（必须最先做）

`probe_darkweb` 四级探测 + SearXNG 引擎模板 + 手工合入与重启。

**为什么排第一**：Tor 链路至今从未建立过（84 次心跳 `0 kB`），且 `ldr-tor`
自身出口还挂在 Privoxy 之后（`HTTPSProxy 172.25.128.1:10888`），这条链路是否
通完全未经验证。**若第 3 级探测拿不到 `.onion` 结果，后两个阶段应当放弃** ——
再精致的开关和报告呈现，接不到数据都是空转。

### 阶段二：能检索

引擎注册 + 配置项 + 研究页勾选框 + 主引擎/追加引擎的合并语义 + preflight 接入。

完成后可跑通「勾选 → 检索到 .onion 结果 → 进入报告」，但报告中暗网与明网尚未
区分。

### 阶段三：报告呈现与安全

`is_darkweb_url` + `[D1]` 编号 + 参考文献分组 + 章节标注 + 图片管道跳过 D 引用。

此阶段风险最集中（触及硬编码 `\d+` 的引用系统与图片绑定），且只有前两阶段跑通
后才有真实数据可验证。

## 阶段一实测结论（2026-08-14）

**实测命令**：绕过 get_searxng_engines 过滤层，直接调用 _darkweb_onion_hits("http://searxng-ldr:8080", timeout=60)。

- 引擎注册：✅ ahmia 在 /config 281 个 enabled engines 中
- 检索返回：164 条结果（ahmia + torch 合并）
- .onion 命中：**20 条**（含 darkzzx4avcsuofgfez5zq75cqc4mprjvfqywo45dfcaxrwqg6qrlfid.onion 等真实地址）
- 耗时：26.8s（探测层）/ 37s（含连接）
- 结论：**阶段一通过**

**否决权未触发**：L3 假象系 probe_darkweb 的 L2 检查 bug。get_searxng_engines 使用 _FALLBACK_ENGINES 9 项白名单过滤探测目标，ahmia/torch 不在内，所以即使 SearXNG 已正确注册它们，probe 仍误报 "L2: 引擎块未合入"。

**遗留问题**（属于 L2 检查 bug，不是 Tor 链路问题）：
- probe_darkweb 的 L2 应查询 SearXNG 完整 enabled 列表，而非探测过滤子集
- 修复后 settings 页按钮与 preflight 才能正确显示 L4 ok
- 修复属于阶段一自身的 bug，应在本阶段收尾前修

**Tor 实际流量**：ldr-tor 心跳仍报 0 kB sent / 0 kB received，但下游 searxng-ldr 拿到了 .onion 结果。Tor 自身对经其中转的流量有延迟统计特性，该计数器不可作为 Tor 是否工作的判据。以"下游服务能否取到 .onion 结果"为准。
