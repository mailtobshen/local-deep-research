在 standard_citation_handler.py 的 analyze_followup prompt 中加一条 "CITATION NUMBERING" 指令, 明确要求 LLM:
- 使用 prompt 中 sources 列表已经预设的 1..K 编号
- 不许 LLM 自己乱编号
- 不许跳过编号

根因 (research 478a92d8, 2026-08-27 16:24): LLM 接受了 30 个 sources 后, 在正文里用了 [[5]] [[26]] [[27]] 等非 prompt 编号的引用, 因为 prompt 只说 "[1] [2] etc.", LLM 自由发挥乱编号。

修复效果:
- 之前: LLM 用 [[26]] 引用 trip.com/attraction, 但 trip.com URL 确实是 sources 之一, enforcer 的 url_is_kept 通过, 不会丢弃。视觉上看起来"渲染成功", 但 [[N]] 编号错位。
- 之后: LLM 严格用 prompt 给的 1..30 编号, 编号和正文一一对应。