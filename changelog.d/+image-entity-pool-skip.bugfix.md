修复图片 alt 语义匹配低分导致没有图被采纳的 bug。

根因 (`semantic_matcher.py:_canonical_section_phrase`):
section phrase 拼接 = `parent_heading + heading + entities`。
对于研究 199acec3 (2026-08-27) sec=9 `（六）上海新天地与田子坊`,
entities 包含 `['田子坊','天地','小店','上海新',...]`,alt='上海新天地' 与
被大量田子坊相关词稀释,score=0.40 < threshold 0.6,被 drop。

修复 (3 处):
1. `semantic_matcher.py:_canonical_section_phrase`: 去掉 entity list
   拼接 (entities 现在是被忽略的参数)。
2. `postprocessing.py:enhance_report_with_images`: 去掉 `if not entities`
   filter — entity-poor section 也用 heading+parent 生成 phrase。
3. 测试更新: `test_semantic_matcher.py`、`test_section_phrase_parent.py` 中
   反映 entity 不再拼接的新行为。

两种研究模式 (quick summary / detailed report) 都走 `enhance_report_with_images`,
所以这次修复统一影响两种模式。