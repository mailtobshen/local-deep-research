from local_deep_research.images.reference_sanitizer import sanitize_references


def _md(used_nums: list[int], all_nums: list[int]) -> str:
    """Build a markdown with a body citing `used_nums` and a References block listing `all_nums`."""
    body_cites = "".join(f"[[{n}]]" for n in used_nums)
    refs = "".join(
        f"[[{n}]] Title {n}\n   URL: https://example.com/{n}\n" for n in all_nums
    )
    return f"## Section\n\nText {body_cites}.\n\n## 参考文献\n\n{refs}"


def test_drops_uncited_reference_rows():
    """Rows whose number is not cited in the body are removed."""
    md = _md(used_nums=[1, 3], all_nums=list(range(1, 6)))  # body cites [1],[3]
    out = sanitize_references(md)
    assert "[[1]] Title 1" in out
    assert "[[3]] Title 3" in out
    assert "Title 2" not in out
    assert "Title 4" not in out
    assert "Title 5" not in out


def test_preserves_original_numbering():
    """Cited numbers keep their original value (no renumbering)."""
    md = _md(used_nums=[7], all_nums=[1, 7, 9])
    out = sanitize_references(md)
    assert "[[7]] Title 7" in out
    assert "[[1]] Title 1" not in out
    assert "[[9]] Title 9" not in out


def test_no_references_block_returns_unchanged():
    """No 参考文献/References heading -> markdown returned verbatim."""
    md = "## Section\n\nBody [[1]] with no references block.\n"
    assert sanitize_references(md) == md


def test_body_num_without_reference_row_is_silently_dropped():
    """A body [[N]] whose N has no References row just yields no row for it."""
    md = _md(used_nums=[1, 99], all_nums=[1])  # [99] cited but absent from refs
    out = sanitize_references(md)
    assert "[[1]] Title 1" in out
    # No crash; [99] simply has no row to keep or drop.
    assert "99" not in out.split("参考文献")[-1]
