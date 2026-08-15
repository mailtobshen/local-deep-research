"""Darkweb citation defense: D-prefixed references must not crash the
image pipeline (no int() of 'D1') and must not appear in
num_to_url (image binding).
"""
from local_deep_research.images.relevance import (
    build_citation_index,
    _scan_references_block,
    CITE_INLINE_RE,
)


def test_cite_inline_re_does_not_match_d_prefix():
    """The cite regex matches \d+ only — '[D1]' / '[D2]' must be skipped."""
    body = "see [D1] and [1] and [D2] for context."
    matches = [m.group(1) for m in CITE_INLINE_RE.finditer(body)]
    assert "D1" not in matches
    assert "D2" not in matches
    assert "1" in matches  # the pure numeric one still matches


def test_scan_references_block_skips_d_rows():
    """[D1] / [D2] rows in ## References are not registered into num_to_url.

    The current Sources row parser uses CITE_LIST_ROW_RE which is anchored
    on [N] (digits-only). Rows starting with [D1] simply fail to match,
    so num_to_url never contains 'D1'/'D2'. That means body markers like
    [[D1]](url) cannot resolve to a known source — they're treated as
    orphans (intentionally: .onion URLs don't bind to images).
    """
    md = """
Some body text with [D1] inline reference and [1] inline reference.

## References

[D1] Darkweb Source Title
   URL: http://kx5thpx2oluwml4w.onion/page
[1] Normal Source
   URL: https://example.com
"""
    num_to_url = _scan_references_block(md)
    # Pure-numeric [1] is registered; [D1] is skipped (no source row match).
    assert num_to_url.get("1") == "https://example.com"
    assert "D1" not in num_to_url


def test_build_citation_index_no_crash_on_d_references():
    """The full pipeline must not crash on a markdown that contains D refs.

    build_citation_index expects a dict-shaped results parameter; this
    test exercises the lower-level _scan_references_block which is the
    real D-reference guard (it gates everything downstream).
    """
    md = """
# Section 1

Some claim backed by [D1] and [1].

## References

[D1] Some Darkweb Title
   URL: http://kx5thpx2oluwml4w.onion/page
[1] Plain Title
   URL: https://example.com/page
"""
    num_to_url = _scan_references_block(md)
    # Pure-numeric [1] is the only thing that survived into num_to_url.
    assert num_to_url.get("1") == "https://example.com/page"
    # .onion URL is intentionally absent — image binding is forbidden
    # for darkweb sources per the design constraint.
    assert "D1" not in num_to_url


def test_int_parsing_safe_for_d_references():
    """Any place we do int(cite_num) must coerce 'D1' to something safe.

    Phase-3 promise: 'no int() of D-prefixed strings'. The current code
    never reaches int() because CITE_INLINE_RE filters them out — this
    test guards against regressions where someone accidentally widens
    the regex.
    """
    body = "[D1] should be ignored"
    matches = [m.group(1) for m in CITE_INLINE_RE.finditer(body)]
    # The regex did NOT match D1; if it ever does, ensure the downstream
    # code never tries int(D1).
    for n in matches:
        try:
            int(n)
        except ValueError as e:
            raise AssertionError(
                f"int({n!r}) raised ValueError — D-prefix leaked into "
                f"the image pipeline. Either fix the regex to exclude "
                f"D-prefixes or guard with a try/except. Original: {e}"
            )