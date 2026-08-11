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
