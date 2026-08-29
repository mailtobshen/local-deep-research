"""Process-info leakage sanitizer contract tests (research afdd3cf7).

Two leak classes observed in the saved report body:

1. The LLM (small local model) echoed its own subsection prompt —
   "Research task: ...", "=== OUTPUT RULES ... ===", "=== INLINE
   CITATIONS ... ===", "CRITICAL:", "=== CONTENT ALREADY WRITTEN ==="
   — into ``current_knowledge``, which _research_and_generate_sections
   appended verbatim.
2. The strategy's no-results sentinel ("## 未搜索到相关结果 ... 本次研究
   主动停止 ... ldr-tor 可达") is a user-facing *search* suggestion
   meant for the whole-run failure path; when it surfaces as one
   subsection's body it reads as process chatter.
"""

from local_deep_research.report_generator import IntegratedReportGenerator

ECHOED_PROMPT = """3. 政策主张体系（特别是两岸关系立场）

Research task: Create comprehensive content for the '政策主张体系' section in a report about '台湾地区民进党领导人赖清德'.

=== CONTENT ALREADY WRITTEN (DO NOT REPEAT) ===
[个人基本信息 > 个人基本信息]
五、参考资料说明
本报告内容基于提供的多个来源进行综合整理。
=== END OF PREVIOUS CONTENT ===

CRITICAL: The above content has already been written.

=== OUTPUT RULES — READ CAREFULLY ===
Write ONLY content that directly answers the user's research question.
=== END OF OUTPUT RULES ===

=== INLINE CITATIONS (REQUIRED) ===
After each factual claim, insert a source marker like [3]-[1].
"""

NO_RESULTS_SENTINEL = (
    "## 未搜索到相关结果\n\n"
    "查询 `Research task: ...` 通过搜索引擎 `searxng` 调取了多个子查询"
    "（暗网模式下还会按中英文短语拆分），但均未返回任何结果。\n\n"
    "为避免生成没有真实来源的内容，本次研究主动停止。建议：\n"
    "1. 调整查询语句（缩短关键词、增加具体名词）\n"
    "2. 换一个搜索引擎（设置中的 `search.tool`）\n"
    "3. 若目标是暗网资源，请确认 SearXNG 的 `engines-darkweb.yml` 已合并、"
    "`ldr-tor` 可达"
)

CLEAN_CONTENT = (
    "## 个人基本信息与成长背景\n\n"
    "赖清德于1959年10月6日出生于台湾省新北市万里区 [1]。"
)


def test_prompt_echo_artifacts_stripped():
    gen = IntegratedReportGenerator.__new__(IntegratedReportGenerator)
    out = gen._strip_process_leakage(ECHOED_PROMPT)
    for marker in (
        "Research task:",
        "=== OUTPUT RULES",
        "=== INLINE CITATIONS",
        "CRITICAL:",
        "CONTENT ALREADY WRITTEN",
        "END OF PREVIOUS CONTENT",
        "END OF OUTPUT RULES",
        "参考资料说明",
    ):
        assert marker not in out, f"{marker!r} must be stripped; got: {out[:200]!r}"
    assert out.strip(), "non-prompt residue (section heading) must survive"


def test_no_results_sentinel_replaced_with_brief_notice():
    gen = IntegratedReportGenerator.__new__(IntegratedReportGenerator)
    out = gen._strip_process_leakage(NO_RESULTS_SENTINEL)
    for chatter in (
        "主动停止",
        "暗网模式",
        "search.tool",
        "ldr-tor",
        "engines-darkweb.yml",
    ):
        assert chatter not in out, f"{chatter!r} must not appear in body"
    assert out.strip(), "a brief localized notice must remain, not empty"


def test_clean_content_untouched():
    gen = IntegratedReportGenerator.__new__(IntegratedReportGenerator)
    assert gen._strip_process_leakage(CLEAN_CONTENT) == CLEAN_CONTENT
