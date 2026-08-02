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


def test_title_digits_do_not_keep_rows():
    """Digits later in the head line (years, day counts, usernames) are
    title text and must not count as citation numbers — only the leading
    [N] bracket does. Regression: the old whole-head extraction kept 190
    of 230 rows on the real B3 report solely via title digits."""
    md = (
        "## Section\n\n"
        "Text cites [[2024]](https://example.com/2024).\n\n"
        "## Sources\n"
        "[7] Beijing 2024 Olympics (source nr: 7)\n"
        "   URL: https://example.com/7\n"
        "[2024] 2024 Travel Guide (source nr: 2024)\n"
        "   URL: https://example.com/2024\n"
    )
    out = sanitize_references(md)
    # Bracket {2024} ∩ used -> kept.
    assert "Travel Guide" in out
    # Title digit 2024 must NOT keep the uncited [7] row.
    assert "Beijing 2024 Olympics" not in out


def test_fullwidth_body_citations_are_counted():
    """Full-width 【N】 citations (accepted by CITE_INLINE_RE and the
    citation index's section scan) count as used in the body."""
    md = (
        "## Section\n\nText cites 【7】 here.\n\n"
        "## 参考文献\n"
        "[7] A\n   URL: https://a.com\n"
        "[8] B\n   URL: https://b.com\n"
    )
    out = sanitize_references(md)
    assert "[7] A" in out
    assert "[8] B" not in out


def test_comma_group_row_rewritten_to_cited_members():
    """A comma-group row keeps only its cited members: '[1, 1224]'
    becomes '[1224]' when only [[1224]] is cited, and the
    '(source nr: ...)' echo is synced. Every member of a production
    row shares one URL (format_links_to_markdown groups by canonical
    URL), so the rewrite breaks no URL mapping."""
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
    assert "[1224] Beijing City Walk (source nr: 1224)" in out
    assert "[1, 1224]" not in out
    assert "Uncited" not in out


def test_comma_group_all_members_cited_kept_verbatim():
    """Both members cited -> the row survives byte-for-byte."""
    md = (
        "## Section\n\n"
        "Text cites [[1]](https://a.com/1) and [[1224]](https://a.com/1).\n\n"
        "## Sources\n"
        "[1, 1224] Shared source (source nr: 1, 1224)\n"
        "   URL: https://a.com/1\n"
    )
    out = sanitize_references(md)
    assert "[1, 1224] Shared source (source nr: 1, 1224)" in out


def test_comma_group_first_member_only():
    """Only the FIRST member cited -> '[1, 1224]' becomes '[1]'."""
    md = (
        "## Section\n\n"
        "Text cites [[1]](https://a.com/1).\n\n"
        "## Sources\n"
        "[1, 1224] Shared source (source nr: 1, 1224)\n"
        "   URL: https://a.com/1\n"
        "[1224] Own row (source nr: 1224)\n"
        "   URL: https://b.com/1224\n"
    )
    out = sanitize_references(md)
    assert "[1] Shared source (source nr: 1)" in out
    assert "[1, 1224]" not in out
    assert "Own row" not in out


def test_comma_group_non_echo_suffix_left_untouched():
    """A '(source nr: ...)' suffix that does NOT mirror the bracket is
    not rewritten (it may be title text in LLM-written rows) — only
    the leading bracket is filtered."""
    md = (
        "## Section\n\n"
        "Text cites [[1224]](https://a.com/1224).\n\n"
        "## Sources\n"
        "[1, 1224] Paper (source nr: 7)\n"
        "   URL: https://a.com/1224\n"
    )
    out = sanitize_references(md)
    assert "[1224] Paper (source nr: 7)" in out
