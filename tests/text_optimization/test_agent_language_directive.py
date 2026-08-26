"""Agent system prompt must carry the report-language directive.

Observed 2026-08-26 (research d4002a64, 炸弹制作): the LLM's safety
refusal shipped in ENGLISH on a zh-CN deployment — the agent loop's
system prompt had no language constraint (the citation handler's
prompt does, but it never ran: refusal at 2.4 s, zero searches). The
directive now sits in the agent prompt itself and explicitly covers
refusals (a refusal is still an output the user reads).

User decision: prompt-side constraint (simple, covers the common
path) over post-hoc detection+translation.
"""

from local_deep_research.advanced_search_system.strategies.langgraph_agent_strategy import (
    LangGraphAgentStrategy,
)


def _strategy(snapshot):
    s = LangGraphAgentStrategy.__new__(LangGraphAgentStrategy)
    s.settings_snapshot = snapshot
    return s


class TestLanguageDirective:
    def test_zh_directive_includes_refusal_clause(self):
        d = _strategy({"report.language": "zh-CN"})._language_directive()
        assert "Simplified Chinese" in d
        assert "REFUSAL" in d  # refusal must also be in-language

    def test_default_snapshot_is_zh(self):
        d = _strategy({})._language_directive()
        assert "Simplified Chinese" in d

    def test_english_empty(self):
        assert _strategy({"report.language": "en"})._language_directive() == ""

    def test_unknown_code_empty(self):
        assert _strategy({"report.language": "xx-YY"})._language_directive() == ""


class TestWrappedSnapshotValue:
    def test_dict_wrapped_value_unwrapped(self):
        """098f8a1a crash regression: snapshot value arrives as
        {'value': 'zh-CN', ...} — the raw .get(dict) lookup raised
        TypeError and killed the research at prompt-build time."""
        d = _strategy(
            {"report.language": {"value": "zh-CN", "source": "db"}}
        )._language_directive()
        assert "Simplified Chinese" in d

    def test_dict_wrapped_english_empty(self):
        s = _strategy({"report.language": {"value": "en"}})
        assert s._language_directive() == ""

    def test_none_value_defaults_zh(self):
        d = _strategy({"report.language": None})._language_directive()
        assert "Simplified Chinese" in d
