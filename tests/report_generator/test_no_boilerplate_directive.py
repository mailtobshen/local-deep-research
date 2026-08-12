"""Tests for the anti-boilerplate OUTPUT RULES directive.

The directive built by ``ReportGenerator._build_no_boilerplate_directive``
is injected into every section/subsection prompt. It must explicitly
forbid ASCII / box-drawing diagrams (┌─┐│◄►▼ etc.), which the model emits
as 'space relationship' filler when a section's research output is thin.
"""

from local_deep_research.report_generator import IntegratedReportGenerator


def test_output_rules_ban_ascii_diagrams():
    """The anti-boilerplate directive must explicitly forbid ASCII
    box-drawing diagrams (┌─┐│◄►▼ etc.), which the model emits as
    'space relationship' filler.

    ``_build_no_boilerplate_directive`` is a pure string builder that
    never touches ``self``, so we invoke the unbound function on a dummy
    object rather than constructing a full ``IntegratedReportGenerator``
    (whose ``__init__`` instantiates an LLM and an
    ``AdvancedSearchSystem``).
    """
    # In Python 3, accessing the method on the class yields a plain
    # function, so no ``__func__`` is needed — call it directly with a
    # dummy ``self`` (the method never uses it).
    directive = (
        IntegratedReportGenerator._build_no_boilerplate_directive(object())
    )
    lower = directive.lower()
    assert "ascii" in lower or "box-drawing" in lower, (
        "directive must mention ASCII / box-drawing"
    )
    assert "┌" in directive or "diagram" in lower, (
        "directive must show a forbidden box-drawing example or the word diagram"
    )


def test_output_rules_ban_arrow_and_connector_diagrams():
    """The directive must ALSO forbid arrows/connectors (→ ↓ ──) and
    name the content shapes that use them — route maps, distribution
    diagrams, flow charts — not just box-drawing characters.

    Regression: run dfa00057 rendered '经典拍摄路线: 外滩源 → 外白渡桥 → ...'
    and '商业业态分布图' with ↓/──→ columns, because the original ASCII
    ban only listed box-drawing chars (┌─┐│├└┘═║) and the model routed
    around it using arrows instead. Arrows, connectors, and the named
    content shapes must all be explicitly banned now.
    """
    directive = (
        IntegratedReportGenerator._build_no_boilerplate_directive(object())
    )
    lower = directive.lower()
    # An arrow character must appear in the banned list.
    assert any(ch in directive for ch in ("→", "←", "↑", "↓")), (
        "directive must list at least one arrow character as banned"
    )
    # Routes / flows / distributions must be named so the model knows
    # these content shapes are banned, not just the box-drawing chars.
    assert "route" in lower or "flow" in lower, (
        "directive must name route/flow as a banned diagram shape"
    )
    # And the replacement must be prose/table/ordered-list, not silence.
    assert "table" in lower or "list" in lower or "prose" in lower, (
        "directive must offer prose/table/list as the replacement"
    )
