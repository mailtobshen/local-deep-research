# tests/images/test_postprocessing.py
from unittest.mock import MagicMock, patch

from local_deep_research.images import postprocessing
from local_deep_research.images.postprocessing import enhance_report_with_images


def _patch_constant_vectors(monkeypatch):
    """Patch the semantic model so every phrase encodes to the same
    unit vector -> cosine 1.0 for every alt (permissive gate)."""
    import numpy as np

    class _M:
        def encode(self, phrases, normalize_embeddings=True):
            return [np.array([1.0, 0.0, 0.0, 0.0]) for _ in phrases]

    monkeypatch.setattr(
        postprocessing.semantic_matcher, "get_model", lambda *a, **k: _M()
    )


def test_disabled_returns_markdown_unchanged():
    out = enhance_report_with_images(
        research_id="rid",
        clean_markdown="# hi",
        results={"findings": []},
        db_session=MagicMock(),
        enable_images=False,
        vision_model="",
    )
    assert out == "# hi"


def test_enabled_builds_bank_from_image_list_json(monkeypatch):
    """Real images from the cited source's html_content survive the
    semantic gate and are persisted through ImageStore (persist ->
    rewrite_markdown local-route contract)."""
    import json

    md = (
        "## 广州塔\n\n广州塔 [1] 是地标。\n\n"
        "## 参考文献\n\n"
        "[1] 广州塔介绍\n   URL: https://src/page\n"
    )
    findings = [{
        "search_results": [{
            "url": "https://src/page", "title": "广州塔",
            "html_content": json.dumps([{
                "url": "https://real/a.jpg", "alt": "广州塔",
                "source_url": "https://src/page", "source_title": "广州塔",
                "width": 800, "height": 600,
            }]),
        }],
    }]
    _patch_constant_vectors(monkeypatch)
    with patch.object(postprocessing, "ImageStore") as store_mock:
        store_inst = store_mock.return_value
        store_inst.persist.return_value = {
            "https://real/a.jpg": "/images/rid/a.png"
        }
        store_inst.rewrite_markdown.side_effect = (
            lambda md, m, **kwargs: md.replace(
                "https://real/a.jpg", "/images/rid/a.png"
            )
        )
        out = enhance_report_with_images(
            research_id="rid",
            clean_markdown=md,
            results={"findings": findings},
            db_session=MagicMock(), enable_images=True, vision_model="",
        )
    assert "/images/rid/a.png" in out
    assert "https://real/a.jpg" not in out
    store_inst.persist.assert_called_once()


def test_enabled_legacy_html_content_yields_empty_bank(monkeypatch):
    """Legacy HTML (non-JSON) html_content parses to zero images ->
    no eligible bank -> markdown preserved."""
    md = (
        "## A\n\nText [1].\n\n"
        "## 参考文献\n\n"
        "[1] S\n   URL: https://src/page\n"
    )
    findings = [{"search_results": [{"url": "https://src/page", "title": "t",
                 "html_content": "<html><img src='x'></html>"}]}]
    _patch_constant_vectors(monkeypatch)
    out = enhance_report_with_images(
        research_id="rid", clean_markdown=md, results={"findings": findings},
        db_session=MagicMock(), enable_images=True, vision_model="",
    )
    assert out == md  # non-JSON html_content -> no images -> unchanged


# --- _dedupe_images tests ---------------------------------------------------


def test_dedupe_images_collapses_duplicates():
    from local_deep_research.images.postprocessing import _dedupe_images

    md = (
        "# Section A\n\n"
        "![tower](https://x/a.jpg)\n\n"
        "# Section B\n\n"
        "![tower again](https://x/a.jpg)\n\n"
        "# Section C\n\n"
        "![leaf](https://x/b.jpg)\n"
    )
    out, orig, unique = _dedupe_images(md)
    assert orig == 3
    assert unique == 2
    # First occurrence of url1 kept, second deleted
    assert out.count("https://x/a.jpg") == 1
    assert out.count("https://x/b.jpg") == 1
    # Section ordering preserved
    assert out.find("Section A") < out.find("Section B") < out.find("Section C")


def test_dedupe_images_keeps_first_occurrence_with_first_alt():
    from local_deep_research.images.postprocessing import _dedupe_images

    md = "![first alt](https://x/a.jpg) ... ![second alt](https://x/a.jpg)"
    out, _, _ = _dedupe_images(md)
    assert "first alt" in out
    assert "second alt" not in out


def test_dedupe_images_no_op_when_all_unique():
    from local_deep_research.images.postprocessing import _dedupe_images

    md = "![a](https://x/1.jpg) and ![b](https://x/2.jpg)"
    out, orig, unique = _dedupe_images(md)
    assert orig == unique == 2
    assert out == md


def test_dedupe_images_collapses_runs_of_blank_lines():
    from local_deep_research.images.postprocessing import _dedupe_images

    md = "![a](https://x/a.jpg)\n\n\n\n![a dup](https://x/a.jpg)\n"
    out, _, _ = _dedupe_images(md)
    # Three or more consecutive newlines squeezed to two.
    assert "\n\n\n\n" not in out


def test_no_eligible_bank_preserves_markdown():
    """No body citations -> no image bank; markdown preserved, the
    store is never touched."""
    with patch.object(postprocessing, "ImageStore") as store_cls:
        result = enhance_report_with_images(
            research_id="rid",
            clean_markdown="# 广州建筑\n## 广州塔\n介绍",
            results={"findings": [{"search_results": [{
                "html_content": '[{"url":"https://img/a.jpg","alt":"旅游攻略"}]'
            }]}]},
            db_session=MagicMock(),
            enable_images=True,
            vision_model="",
        )
    assert result == "# 广州建筑\n## 广州塔\n介绍"
    store_cls.assert_not_called()
