修复 langgraph 路径下研究报告里"参考文献章节缺失 + 引用编号不升序"的 bug (research 5e033c22, 2026-08-27 10:12)。

根因: `_finalize` 接收的 `nr_of_links` 参数是在 `analyze_topic` 入口处（line 978）记录的，**此时 collector 是空的**（`len(self.all_links_of_system) = 0`）。后续 5 轮迭代后 collector 累积了 90 个结果，但 `nr_of_links` 仍然是 0。`_finalize` 把 `nr_of_links=0` 传给 `analyze_followup`，导致 LLM prompt 里 documents 列表为空，LLM 自己乱编引用编号 `[[26]]` 等；后续 sources 块拼接也因为 all_search_results 等的判断路径问题，最终报告里完全没有 `## 参考文献` 章节。

修复: 在 `_finalize` 入口处用 `nr_of_links = max(nr_of_links, len(self.collector.results))` 重新计算，覆盖 collector 已累积的实际结果数。