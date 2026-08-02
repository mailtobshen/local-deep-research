"""End-to-end test of the citation-anchored image pipeline.

Uses a fake HF model so no download/network is required. The fake
returns a hand-picked vector per phrase; we arrange vectors so the
cited image's alt is highly similar to its section phrase and an
unrelated image is dissimilar.
"""
from unittest.mock import MagicMock, patch

from local_deep_research.images import postprocessing


def _fake_model(vectors: dict[str, list[float]]):
    """Return a fake model whose encode(phrase) -> vectors[phrase]."""

    import numpy as np

    class _M:
        def encode(self, phrases, normalize_embeddings=True):
            return [np.array(vectors.get(p, [0.0, 0.0, 0.0, 0.0])) for p in phrases]

    return _M()


def test_cited_image_passes_gate_and_is_inserted(monkeypatch):
    """An image whose alt matches its citation's section is inserted there."""
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

    fake = _fake_model({
        "Canton Tower": [1.0, 0.0, 0.0, 0.0],   # alt
        # section phrase = heading + entities; arrange same direction.
    })
    monkeypatch.setattr(postprocessing.semantic_matcher, "get_model", lambda *a, **k: fake)
    # build_report_entity_pool + _canonical_section_phrase run on the
    # real markdown; make the section phrase embed to the same vector
    # by having encode fall through to the default for unknown phrases
    # and patching _canonical_section_phrase to return "Canton Tower".
    monkeypatch.setattr(
        postprocessing.semantic_matcher, "_canonical_section_phrase",
        lambda heading, entities: "Canton Tower",
    )

    with patch.object(postprocessing, "ImageEnhancer") as enh_mock, \
         patch.object(postprocessing, "ImageStore") as store_mock:
        store_mock.return_value.persist.return_value = (
            {"https://img/tower.jpg": "/images/r/t.jpg"}
        )
        store_mock.return_value.rewrite_markdown.side_effect = (
            lambda md, mapping, **kw: md
        )
        out = postprocessing.enhance_report_with_images(
            research_id="r",
            clean_markdown=md,
            results=results,
            db_session=MagicMock(),
            enable_images=True,
            vision_model="",
        )
    # The cited image is inserted into the Canton Tower section.
    assert "![Canton Tower](https://img/tower.jpg)" in out
    # ImageEnhancer must NOT have been called (it is paused).
    enh_mock.assert_not_called()


def test_uncited_source_image_not_extracted(monkeypatch):
    """An image whose source is never cited in the body is never even considered."""
    md = (
        "## Canton Tower\n\nThe tower [[1]].\n\n"
        "## 参考文献\n\n"
        "[1] Source\n   URL: https://src/cited\n"
    )
    results = {"findings": [{"search_results": [
        # Cited source has no images.
        {"url": "https://src/cited", "html_content": "[]"},
        # Uncited source HAS an image — must be ignored entirely.
        {"url": "https://src/uncited", "html_content": (
            '[{"url": "https://img/stray.jpg", "alt": "Canton Tower", '
            '"source_url": "https://src/uncited", "source_title": "", '
            '"width": null, "height": null}]'
        )},
    ]}]}
    fake = _fake_model({"Canton Tower": [1.0, 0.0, 0.0, 0.0]})
    monkeypatch.setattr(postprocessing.semantic_matcher, "get_model", lambda *a, **k: fake)
    monkeypatch.setattr(
        postprocessing.semantic_matcher, "_canonical_section_phrase",
        lambda heading, entities: "Canton Tower",
    )
    with patch.object(postprocessing, "ImageEnhancer"), \
         patch.object(postprocessing, "ImageStore") as store_mock:
        store_mock.return_value.persist.return_value = {}
        store_mock.return_value.rewrite_markdown.side_effect = lambda md, m, **k: md
        out = postprocessing.enhance_report_with_images(
            research_id="r", clean_markdown=md, results=results,
            db_session=MagicMock(), enable_images=True, vision_model="",
        )
    assert "stray.jpg" not in out


def test_low_similarity_image_dropped(monkeypatch):
    """An image whose alt is orthogonal to its section is dropped."""
    md = (
        "## Canton Tower\n\n[[1]].\n\n"
        "## 参考文献\n\n[1] S\n   URL: https://src/p\n"
    )
    results = {"findings": [{"search_results": [
        {"url": "https://src/p", "html_content": (
            '[{"url": "https://img/x.jpg", "alt": "Banana", '
            '"source_url": "https://src/p", "source_title": "", '
            '"width": null, "height": null}]'
        )},
    ]}]}
    fake = _fake_model({
        "Banana": [1.0, 0.0, 0.0, 0.0],          # alt direction
        # section phrase default -> [0,0,0,0], cosine 0 < threshold.
    })
    monkeypatch.setattr(postprocessing.semantic_matcher, "get_model", lambda *a, **k: fake)
    monkeypatch.setattr(
        postprocessing.semantic_matcher, "_canonical_section_phrase",
        lambda heading, entities: "section phrase orthogonal",
    )
    with patch.object(postprocessing, "ImageEnhancer"), \
         patch.object(postprocessing, "ImageStore") as store_mock:
        store_mock.return_value.persist.return_value = {}
        store_mock.return_value.rewrite_markdown.side_effect = lambda md, m, **k: md
        out = postprocessing.enhance_report_with_images(
            research_id="r", clean_markdown=md, results=results,
            db_session=MagicMock(), enable_images=True, vision_model="",
        )
    assert "Banana" not in out
    assert "img/x.jpg" not in out


def test_enable_images_false_returns_markdown_unchanged():
    out = postprocessing.enhance_report_with_images(
        research_id="r", clean_markdown="# hi", results={"findings": []},
        db_session=MagicMock(), enable_images=False, vision_model="",
    )
    assert out == "# hi"
