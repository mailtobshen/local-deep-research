修复 research d620e6f5 (2026-08-28 01:05, langgraph-agent) 用户报告的"研究报告完全没参考文献章节" bug。

根因: c22635d5 在 langgraph_agent_strategy._finalize 和 research_service.py 双方都调用了 strip_per_section_sources_block。`_finalize` 流程是 (1) strip LLM 自创 sources block, (2) 拼 canonical `## 参考文献` + sources_md。research_service.py:2176 在 `_finalize` return **之后**又调了一次 strip,把刚 append 的 canonical block 也 strip 掉了。

修复: research_service.py quick-summary 路径只调 `_ensure_markdown_block_boundaries`,不重复调 strip (因为 _finalize 已经处理过了)。详细模式路径不变,继续只调 enforce。

测试 (tests/utilities/test_search_utilities.py::TestCanonicalSourcesBlockSurvivesStrip):
- 验证 canonical `## 参考文献` block 一旦被 strip,内容会丢
- 防止以后再有人在 research_service.py 错误地添加 strip 调用