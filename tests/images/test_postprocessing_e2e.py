# tests/images/test_postprocessing_e2e.py
"""End-to-end integration test for enhance_report_with_images().

Stubs out the external collaborators (semantic model, DB session) and
drives the post-processing pipeline with realistic data. Verifies:
- the public entry point returns a markdown string (no exception)
- a report with no citable sources comes out unchanged
- enable_images=False short-circuits before any work
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from local_deep_research.images.postprocessing import enhance_report_with_images


def _img_json(url, alt, source_url):
    return json.dumps(
        [{
            "url": url, "alt": alt,
            "source_url": source_url, "source_title": "t",
            "width": None, "height": None,
        }]
    )


def _search_result(link, title, content, html_content):
    return {
        "link": link, "title": title, "content": content,
        "snippet": title, "html_content": html_content,
    }


def _patch_image_store(monkeypatch):
    """Stub out ImageStore so the test does not need a real DB.
    persist() returns the chosen URL -> stored URL map (identity);
    rewrite_markdown() returns the markdown unchanged."""
    import local_deep_research.images.postprocessing as pp

    class FakeStore:
        def __init__(self, *a, **kw):
            self._last_url_to_size = {}

        def persist(self, chosen, url_to_alt=None, url_to_source=None):
            return {u: u for u in chosen}

        def rewrite_markdown(self, md, url_to_route, url_to_size=None):
            return md

    monkeypatch.setattr(pp, "ImageStore", FakeStore)


def test_postprocessing_empty_section_pool_returns_section_unchanged(
    monkeypatch,
):
    """A report with no citations produces no images and the markdown
    is preserved intact."""
    _patch_image_store(monkeypatch)

    clean_markdown = (
        "The Eiffel Tower is a famous landmark in Paris, France, "
        "built in 1889 for the World's Fair."
    )
    results = {
        "research_query": "Eiffel Tower history",
        "findings": [
            {"search_results": [
                _search_result(
                    "https://a.ctrip.com/eiffel",
                    "Eiffel Tower Guide",
                    "Eiffel Tower Paris France landmark",
                    _img_json(
                        "https://img.ctrip.com/eiffel.jpg",
                        "Eiffel photo",
                        "https://a.ctrip.com/eiffel",
                    ),
                ),
            ]}
        ],
    }

    out = enhance_report_with_images(
        research_id="test-e2e-2",
        clean_markdown=clean_markdown,
        results=results,
        db_session=MagicMock(),
        enable_images=True,
        vision_model="",
    )
    assert isinstance(out, str)
    # No citations -> no image bank -> output equals input
    assert out == clean_markdown.strip()


def test_postprocessing_disabled_images_returns_input_unchanged(monkeypatch):
    """enable_images=False short-circuits before any work runs."""
    _patch_image_store(monkeypatch)

    out = enhance_report_with_images(
        research_id="test-e2e-3",
        clean_markdown="# Heading\n\nbody",
        results={"findings": [], "research_query": "x"},
        db_session=MagicMock(),
        enable_images=False,
        vision_model="",
    )
    assert out == "# Heading\n\nbody"
