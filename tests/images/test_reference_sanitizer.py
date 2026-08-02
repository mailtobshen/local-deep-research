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


def test_production_format_rows_are_trimmed():
    """Production rows (format_links_to_markdown output) are single-bracket
    comma groups — '[1, 1224] Title (source nr: 1, 1224)' + '   URL:'
    — while body citations are '[[N]](url)' markdown links.

    Before this fix the row matcher only recognized '[[N]]' rows, so
    the sanitizer silently no-oped on real reports (verified against
    the B3 report: 1831 rows kept, 0 trimmed, output byte-identical).
    """
    md = (
        "## Section\n\n"
        "Text cites [[7]](https://en.wikipedia.org/wiki/Beijing) and "
        "[[10]](https://www.instagram.com/popular/x).\n\n"
        "## Sources\n"
        "[1, 1224] Beijing City Walk (source nr: 1, 1224)\n"
        "   URL: https://www.instagram.com/reel/DO3hsrEAXfr\n"
        "[7] Beijing — Wikipedia (source nr: 7)\n"
        "   URL: https://en.wikipedia.org/wiki/Beijing\n"
        "[10] 北京適合旅遊季節 - Instagram (source nr: 10)\n"
        "   URL: https://www.instagram.com/popular/x\n"
        "[42] Uncited row (source nr: 42)\n"
        "   URL: https://example.com/42\n"
    )
    out = sanitize_references(md)
    assert "[7] Beijing — Wikipedia" in out
    assert "[10] 北京適合旅遊季節" in out
    assert "Uncited row" not in out
    # Comma-group row: neither 1 nor 1224 is cited -> dropped.
    assert "Beijing City Walk" not in out


def test_production_comma_group_row_kept_when_any_number_cited():
    """'[1, 1224]' row is kept as soon as ONE of its numbers is cited."""
    md = (
        "## Section\n\n"
        "Text cites [[1224]](https://www.instagram.com/reel/DO3hsrEAXfr).\n\n"
        "## Sources\n"
        "[1, 1224] Beijing City Walk (source nr: 1, 1224)\n"
        "   URL: https://www.instagram.com/reel/DO3hsrEAXfr\n"
        "[2] Uncited (source nr: 2)\n"
        "   URL: https://example.com/2\n"
    )
    out = sanitize_references(md)
    assert "Beijing City Walk" in out
    assert "Uncited" not in out


def test_single_bracket_body_citations_are_counted():
    """Legacy body shape '[N]' (plain brackets, fixture style) counts as used."""
    md = (
        "## Section\n\nText cites [1] and [3].\n\n"
        "## 参考文献\n"
        "[1] A\n   URL: https://a.com\n"
        "[2] B\n   URL: https://b.com\n"
        "[3] C\n   URL: https://c.com\n"
    )
    out = sanitize_references(md)
    assert "[1] A" in out
    assert "[3] C" in out
    assert "[2] B" not in out
