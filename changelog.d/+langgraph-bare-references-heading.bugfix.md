修复 langgraph 路径下研究报告里两个"参考资料"章节同时出现的回归 bug (research 0b7402fb, 2026-08-27 01:27)。同时修复正文 `[8]` 等引用因为段落与列表间缺空行导致 marked 无法识别为列表的 bug。

根因：
1. langgraph 路径直接拼接 LLM 输出 + 标准 `## 参考文献` 块，但 LLM 已经在自己末尾追加了"参考文献说明："块，导致重复
2. `_ensure_markdown_block_boundaries` 之前只在 `format_findings` 里调，langgraph 路径不经过

修复：
1. 在 `_SOURCES_SECTION_PATTERNS` / `_SOURCES_SECTION_CJK_PATTERNS` 中扩展 keyword 列表包含"参考文献说明"，并把前导星号从 `\*{1,3}` 改为 `\*{0,3}` 接受 LLM 自创的无星号 bare heading
2. 在 langgraph_agent_strategy.py 拼接前调 `_ensure_markdown_block_boundaries` + `strip_per_section_sources_block` 剥离 LLM 自创 sources 块
3. 在 research_service.py quick-summary 路径做同样的剥离（防御性冗余）