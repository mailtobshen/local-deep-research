from local_deep_research.images.relevance import (
    _find_parent_heading, _section_levels, _split_sections,
)
from local_deep_research.images.semantic_matcher import _canonical_section_phrase


def test_section_levels_aligned_with_split_sections():
    md = "## A\nx\n### B\ny\n# C\nz\n"
    secs = _split_sections(md)
    levels = _section_levels(md)
    assert len(levels) == len(secs)
    assert levels == [2, 3, 1]


def test_find_parent_heading_child_under_parent():
    md = (
        "## 上海迪士尼乐园\n"
        "intro\n"
        "### 主题园区与核心设施\n"
        "body\n"
    )
    secs = _split_sections(md)
    levels = _section_levels(md)
    child_idx = next(i for i, (h, _) in enumerate(secs)
                     if "主题园区" in h)
    parent = _find_parent_heading(secs, levels, child_idx)
    assert "上海迪士尼乐园" in parent


def test_find_parent_heading_top_level_returns_empty():
    md = "## Top\nbody\n"
    secs = _split_sections(md)
    levels = _section_levels(md)
    assert _find_parent_heading(secs, levels, 0) == ""


def test_find_parent_heading_length_mismatch_fails_closed():
    """If levels/sections lengths disagree (shouldn't happen but guard),
    fail closed — return ''."""
    secs = [("A", "x"), ("B", "y")]
    levels = [2]  # shorter than sections
    assert _find_parent_heading(secs, levels, 1) == ""


def test_canonical_section_phrase_includes_parent():
    phrase = _canonical_section_phrase(
        "主题园区与核心设施",
        entities=[],
        parent_heading="上海迪士尼乐园",
    )
    assert "上海迪士尼乐园" in phrase
    assert "主题园区与核心设施" in phrase


def test_canonical_section_phrase_without_parent_unchanged():
    phrase = _canonical_section_phrase(
        "Some Section", entities=["e1"], parent_heading=""
    )
    assert "Some Section" in phrase and "e1" in phrase
