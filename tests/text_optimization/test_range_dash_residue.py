"""Range-citation dash residue: ``[1]-[147]`` leaves a stray ``-``.

Observed 2026-08-25 (research 97b859c0, FPV穿越机): the LLM emitted a
range citation as two independent bracket tokens with a literal dash
between them — ``新提供的来源 [1]-[147]``. Only source 1 existed, so the
orphan-drop deleted ``[147]`` (and there was no 2..146 to begin with),
but the literal ``-`` between the two tokens is not part of any cite
regex. It survived as plain text glued to the surviving hyperlink,
producing the non-normal text the user saw in the exported report:

    **新提供的来源 -[\\[1\\]](http://....onion/cons/RSA/...) 中...**

Same shape at line 75: ``-[\\[1\\]](...)`` after ``无法使用提供的来源``.

Fix: treat ``[N]-[M]`` (and comma forms spanning it) as one logical
range citation during orphan-drop. If ANY endpoint survives, rewrite the
whole token to just the surviving endpoint markers; if none survive,
delete the token including the dash.
"""

import pytest

from local_deep_research.text_optimization.citation_formatter import (
    enforce_sources_ascending_and_drop_orphans,
)


def _md_range_citation() -> str:
    """Mimic research 97b859c0: body cites ``[1]-[147]``, Sources has
    only row 1 (an onion directory index)."""
    return (
        "# 关于FPV穿越机的研究报告\n\n"
        "经过仔细审查，我必须报告：**新提供的来源 [1]-[147] 中几乎没有任何"
        "关于FPV竞速无人机的实质性信息**可用于交叉验证。\n\n"
        "## 参考文献\n\n"
        "[1] InfoCon Hacking and Security Conference Archives (source nr: 1)\n"
        "   URL: http://exampleonion.onion/cons/RSA/RSAC 2015/2015 Innovation Sandbox?C=M&O=A\n"
    )


def test_range_dash_not_left_behind():
    """The stray ``-`` between ``[1]`` and the deleted ``[147]`` must not
    survive into the final body as ``-[1](url)``."""
    out = enforce_sources_ascending_and_drop_orphans(_md_range_citation())
    # Residue form observed in the wild: ``...(url)- 中`` — a lone dash
    # left where the dead ``[147]`` token used to sit.
    assert ")-" not in out and "-[" not in out, (
        f"dash residue found in output: {out[:400]!r}"
    )
    # The surviving citation is still there, hyperlinked.
    assert "[1]" in out
    assert "exampleonion.onion" in out


def test_range_both_endpoints_hallucinated_removed_entirely():
    """``[50]-[60]`` with no Sources rows for either endpoint: the whole
    token including the dash disappears."""
    md = (
        "# Title\n\n"
        "Bad range [50]-[60] in body.\n\n"
        "## Sources\n\n"
        "[1] Real source\n"
        "   URL: http://example.com/one\n\n"
        "Body also cites [1].\n"
    )
    out = enforce_sources_ascending_and_drop_orphans(md)
    assert "[50]" not in out
    assert "[60]" not in out
    assert "Bad range - in body" not in out
    assert "Bad range  in body" in out or "Bad range in body" in out


def test_range_first_endpoint_survives_alone():
    """``[1]-[147]`` where only 1 exists: output contains ``[1]`` with no
    leading dash and no trace of 147."""
    out = enforce_sources_ascending_and_drop_orphans(_md_range_citation())
    assert "147" not in out
    assert "新提供的来源" in out
