"""Tests for the IMG-TRACE schema on the mandatory kept-image path.

The user-facing requirement (per trace-style inventory requests): every
image that survives the citation-anchored gate and is actually placed
into the report must surface the four fields below on the IMG-TRACE
event line so that one ``grep`` hit on the log reconstructs the full
provenance:

* ``img_alt``         — the ``<img alt="...">`` text
* ``img_url``         — the image's own absolute URL
* ``img_source_url``  — the page the image was extracted from
* ``cite_num``        — the inline-citation number ``[N]`` in the
                        report body that references the image's
                        source page
* ``ref_url``         — the cited reference URL (== ``img_source_url``;
                        emitted under this name to make the
                        "参考文献 url" semantic explicit)

Both ``CANDIDATE_KEPT`` (per-image gate decision) and ``PLACEMENT``
(per-image insertion) must carry the same five-key vocabulary.
"""

from unittest.mock import MagicMock, patch

import pytest

from local_deep_research.images import postprocessing


def _fake_model(vectors: dict[str, list[float]]):
    import numpy as np

    class _M:
        def encode(self, phrases, normalize_embeddings=True):
            return [np.array(vectors.get(p, [0.0, 0.0, 0.0, 0.0])) for p in phrases]

    return _M()


def _img_event_lines(caplog_or_text, marker="CANDIDATE_KEPT"):
    """Match IMG-TRACE lines either by pytest caplog records or by a
    raw loguru text dump. The latter covers the simpler cases where
    we just want to grep the message."""
    if hasattr(caplog_or_text, "records"):
        records = caplog_or_text.records
        return [
            r for r in records
            if "[IMG-TRACE]" in r.getMessage() and marker in r.getMessage()
        ]
    text = caplog_or_text
    return [line for line in text.splitlines()
            if "[IMG-TRACE]" in line and marker in line]


def _field(line: str, key: str) -> str:
    """Pull a single key=value token out of an IMG-TRACE line.

    Token boundaries are whitespace, but the value may be a quoted
    string (e.g. ``img_alt='Canton Tower'``) that itself contains
    spaces. In that case the parser stitches the following tokens
    together until the closing quote is found.
    """
    prefix = f"{key}="
    idx = line.find(prefix)
    if idx < 0:
        return ""
    rest = line[idx + len(prefix):]
    if rest.startswith("'") or rest.startswith('"'):
        quote = rest[0]
        end = rest.find(quote, 1)
        if end < 0:
            return rest[1:]
        return rest[1:end]
    # Unquoted value: read until next whitespace.
    end = len(rest)
    for i, c in enumerate(rest):
        if c.isspace():
            end = i
            break
    return rest[:end]


@pytest.fixture
def kept_image_setup(monkeypatch):
    """Stand up the minimum pipeline that emits one CANDIDATE_KEPT.

    The cited image's alt matches its section phrase so it survives the
    gate. Returns a context dict the assertions below consume.
    """
    md = (
        "## Canton Tower\n\nThe tower [1] is tall.\n\n"
        "## 参考文献\n\n"
        "[1] Canton Tower source\n   URL: https://src/page\n"
    )
    results = {"findings": [{"search_results": [
        {"url": "https://src/page", "html_content": (
            '[{"url": "https://img/tower.jpg", "alt": "Canton Tower", '
            '"source_url": "https://src/page", "source_title": "ct", '
            '"width": null, "height": null}]'
        )},
    ]}]}

    fake = _fake_model({"Canton Tower": [1.0, 0.0, 0.0, 0.0]})
    monkeypatch.setattr(
        postprocessing.semantic_matcher, "get_model", lambda *a, **k: fake
    )
    monkeypatch.setattr(
        postprocessing.semantic_matcher, "_canonical_section_phrase",
        lambda heading, entities, parent_heading="": "Canton Tower",
    )
    return {"md": md, "results": results, "ref_url": "https://src/page",
            "img_url": "https://img/tower.jpg", "img_alt": "Canton Tower",
            "cite_num": "1"}


def test_candidate_kept_event_has_user_required_fields(
    kept_image_setup, loguru_caplog
):
    """CANDIDATE_KEPT must carry img_alt, img_url, img_source_url,
    cite_num, ref_url — one line, one grep hit."""
    s = kept_image_setup
    with patch.object(postprocessing, "ImageStore") as store_mock:
        store_mock.return_value.persist.return_value = (
            {s["img_url"]: "/images/r/t.jpg"}
        )
        store_mock.return_value.rewrite_markdown.side_effect = (
            lambda md, mapping, **kw: md
        )
        postprocessing.enhance_report_with_images(
            research_id="r",
            clean_markdown=s["md"],
            results=s["results"],
            db_session=MagicMock(),
            enable_images=True,
            vision_model="",
        )

    kept = _img_event_lines(loguru_caplog, "CANDIDATE_KEPT")
    assert len(kept) == 1, f"expected 1 CANDIDATE_KEPT, got {len(kept)}: {kept}"
    line = kept[0].getMessage()

    # All five required keys must be present and well-formed.
    for key in ("img_alt", "img_url", "img_source_url", "cite_num", "ref_url"):
        assert f"{key}=" in line, f"missing {key}= in: {line!r}"

    # Values must match the test fixture exactly.
    assert _field(line, "img_alt") == "Canton Tower"
    assert _field(line, "img_url") == s["img_url"]
    assert _field(line, "img_source_url") == s["ref_url"]
    assert _field(line, "ref_url") == s["ref_url"]
    # img_source_url and ref_url are the same page by design.
    assert _field(line, "img_source_url") == _field(line, "ref_url")
    assert _field(line, "cite_num") == s["cite_num"]


def test_placement_event_has_user_required_fields(
    kept_image_setup, loguru_caplog
):
    """PLACEMENT (the actual-insertion counterpart of CANDIDATE_KEPT)
    must carry the same five-key vocabulary so log readers can
    correlate the two events per image without a second parse."""
    s = kept_image_setup
    with patch.object(postprocessing, "ImageStore") as store_mock:
        store_mock.return_value.persist.return_value = (
            {s["img_url"]: "/images/r/t.jpg"}
        )
        store_mock.return_value.rewrite_markdown.side_effect = (
            lambda md, mapping, **kw: md
        )
        postprocessing.enhance_report_with_images(
            research_id="r",
            clean_markdown=s["md"],
            results=s["results"],
            db_session=MagicMock(),
            enable_images=True,
            vision_model="",
        )

    placed = _img_event_lines(loguru_caplog, "PLACEMENT")
    assert len(placed) == 1, f"expected 1 PLACEMENT, got {len(placed)}"
    line = placed[0].getMessage()
    for key in ("img_alt", "img_url", "img_source_url", "cite_num", "ref_url"):
        assert f"{key}=" in line, f"missing {key}= in: {line!r}"
    assert _field(line, "img_alt") == "Canton Tower"
    assert _field(line, "img_url") == s["img_url"]
    assert _field(line, "cite_num") == s["cite_num"]
    assert _field(line, "ref_url") == s["ref_url"]


def test_candidate_dropped_event_remains_compatible(
    kept_image_setup, loguru_caplog
):
    """Sanity: the CANDIDATE_DROPPED event is unchanged in shape so
    existing log parsers keep working. It does NOT need to carry
    cite_num/ref_url — the drop is per-image, not per-citation."""
    # Force the similarity gate to reject by giving the section phrase
    # a different direction from the alt vector.
    md = (
        "## Canton Tower\n\nThe tower [1] is tall.\n\n"
        "## 参考文献\n\n"
        "[1] Source\n   URL: https://src/page\n"
    )
    results = {"findings": [{"search_results": [
        {"url": "https://src/page", "html_content": (
            '[{"url": "https://img/tower.jpg", "alt": "alt", '
            '"source_url": "https://src/page", "source_title": "", '
            '"width": null, "height": null}]'
        )},
    ]}]}
    fake = _fake_model({
        "alt": [1.0, 0.0, 0.0, 0.0],  # alt dir
        "DIFFERENT SECTION": [-1.0, 0.0, 0.0, 0.0],  # anti-parallel sec
    })
    import local_deep_research.images.postprocessing as pp
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        pp.semantic_matcher, "get_model", lambda *a, **k: fake
    )
    monkeypatch.setattr(
        pp.semantic_matcher, "_canonical_section_phrase",
        lambda heading, entities, parent_heading="": "DIFFERENT SECTION",
    )
    with patch.object(pp, "ImageStore") as store_mock:
        store_mock.return_value.persist.return_value = {}
        store_mock.return_value.rewrite_markdown.side_effect = (
            lambda md, m, **kw: md
        )
        pp.enhance_report_with_images(
            research_id="r",
            clean_markdown=md,
            results=results,
            db_session=MagicMock(),
            enable_images=True,
            vision_model="",
        )
    monkeypatch.undo()
    # CANDIDATE_KEPT should NOT fire on a rejection.
    assert not _img_event_lines(loguru_caplog, "CANDIDATE_KEPT")
    # CITATION_MATCH still fires with kept=0 to record the rejection
    # aggregated at the citation level.
    match_lines = _img_event_lines(loguru_caplog, "CITATION_MATCH")
    assert match_lines
    assert "kept=0" in match_lines[0].getMessage()
