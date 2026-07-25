# tests/images/test_postprocessing.py
from unittest.mock import MagicMock, patch

from local_deep_research.images import postprocessing
from local_deep_research.images.postprocessing import enhance_report_with_images


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


def test_enabled_builds_bank_from_image_list_json():
    import json
    findings = [{
        "search_results": [{
            "url": "https://src/page", "title": "\u5e7f\u5dde\u5854",
            "html_content": json.dumps([{
                "url": "https://real/a.jpg", "alt": "\u5e7f\u5dde\u5854",
                "source_url": "https://src/page", "source_title": "\u5e7f\u5dde\u5854",
                "width": 800, "height": 600,
            }]),
        }],
    }]
    with patch("local_deep_research.images.postprocessing.get_llm") as gl, \
         patch("local_deep_research.images.postprocessing.ImageEnhancer") as IEnh, \
         patch("local_deep_research.images.postprocessing.ImageStore") as IStore:
        gl.return_value = MagicMock()
        inst = IEnh.return_value
        inst.enhance.return_value = "# \u5e7f\u5dde\u5efa\u7b51\n## \u5e7f\u5dde\u5854\n![\u5e7f\u5dde\u5854](https://real/a.jpg)\n"
        store_inst = IStore.return_value
        store_inst.persist.return_value = {"https://real/a.jpg": "/images/rid/a.png"}
        store_inst.rewrite_markdown.side_effect = lambda md, m, **kwargs: md.replace("https://real/a.jpg", "/images/rid/a.png")
        out = enhance_report_with_images(
            research_id="rid",
            clean_markdown="# \u5e7f\u5dde\u5efa\u7b51\n## \u5e7f\u5dde\u5854\n\u4ecb\u7ecd",
            results={"findings": findings},
            db_session=MagicMock(), enable_images=True, vision_model="",
        )
    assert "/images/rid/a.png" in out
    IEnh.assert_called_once()


def test_enabled_legacy_html_content_yields_empty_bank():
    findings = [{"search_results": [{"url": "u", "title": "t",
                 "html_content": "<html><img src='x'></html>"}]}]
    with patch("local_deep_research.images.postprocessing.get_llm") as gl:
        gl.return_value = MagicMock()
        out = enhance_report_with_images(
            research_id="rid", clean_markdown="# R", results={"findings": findings},
            db_session=MagicMock(), enable_images=True, vision_model="",
        )
    assert out == "# R"  # non-JSON html_content -> empty bank -> unchanged


def _run_with_capturing_vision(*, vision_model="gpt-4o", vision_url=None, vision_api_key=None):
    # The candidate uses a named entity (\u5e7f\u5dde\u5854) so the strict
    # context-entity gate keeps it and the report path proceeds past the
    # gate to construct the VisionDescriber (whose URL/key we want to
    # inspect). A missing-alt candidate would be dropped by the gate and
    # VisionDescriber would never be built.
    findings = [{
        "search_results": [{
            "url": "https://src/page",
            "title": "\u5e7f\u5dde\u5854",
            "html_content": (
                '[{"url": "https://real/a.jpg", "alt": "\u5e7f\u5dde\u5854",'
                ' "source_url": "https://src/page",'
                ' "source_title": "\u5e7f\u5dde\u5854"}]'
            ),
        }],
    }]
    captured = {}

    def capturing_vd(*, model_name=None, base_url=None, api_key=None):
        captured.update(model_name=model_name, base_url=base_url, api_key=api_key)
        inst = MagicMock(enabled=True)
        inst.describe.return_value = "alt text"
        return inst

    with patch.object(postprocessing, "VisionDescriber", side_effect=capturing_vd), \
         patch.object(postprocessing, "get_llm", return_value=MagicMock()), \
         patch.object(postprocessing, "ImageEnhancer") as enhancer_mock, \
         patch.object(postprocessing, "ImageStore") as store_mock:
        enhancer_mock.return_value.enhance.return_value = (
            "# \u5e7f\u5dde\u5efa\u7b51\n## \u5e7f\u5dde\u5854\n![\u5e7f\u5dde\u5854](https://real/a.jpg)"
        )
        store_mock.return_value.persist.return_value = {
            "https://real/a.jpg": "/images/rid/a.png"
        }
        enhance_report_with_images(
            research_id="rid",
            clean_markdown="# \u5e7f\u5dde\u5efa\u7b51\n## \u5e7f\u5dde\u5854\n\u4ecb\u7ecd",
            results={"findings": findings},
            db_session=MagicMock(),
            enable_images=True,
            vision_model=vision_model,
            vision_url=vision_url,
            vision_api_key=vision_api_key,
        )
    return captured


def test_postprocessing_passes_url_and_key_to_vision():
    captured = _run_with_capturing_vision(
        vision_model="gpt-4o",
        vision_url="https://api.openai.com/v1",
        vision_api_key="sk-test",
    )
    assert captured == {
        "model_name": "gpt-4o",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test",
    }


def test_postprocessing_defaults_url_key_to_none():
    captured = _run_with_capturing_vision(vision_model="llava")
    assert captured["model_name"] == "llava"
    assert captured["base_url"] is None
    assert captured["api_key"] is None


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


def test_enhance_report_runs_dedupe_when_llm_repeats_url():
    """End-to-end: if the LLM returns the same URL twice, the
    [IMG-TRACE] DEDUPE line must fire and 'chosen' must contain the
    URL only once before persist() is called.

    The original version asserted on the conftest `loguru_caplog`
    fixture to confirm the DEDUPE trace log fired. That fixture is
    unavailable under ``--noconftest`` (the brief's invocation
    workaround), so we now rely on the persist-side observable:
    the LLM's duplicate URL only survives once into ``chosen``."""
    import json

    findings = [{
        "search_results": [{
            "url": "https://src/page", "title": "\u5e7f\u5dde\u5854",
            "html_content": json.dumps([{
                "url": "https://real/a.jpg", "alt": "\u5e7f\u5dde\u5854",
                "source_url": "https://src/page", "source_title": "\u5e7f\u5dde\u5854",
                "width": 800, "height": 600,
            }]),
        }],
    }]
    dup_md = (
        "# \u5e7f\u5dde\u5efa\u7b51\n## A\n\n![\u5e7f\u5dde\u5854](https://real/a.jpg)\n\n"
        "## B\n\n![\u5e7f\u5dde\u5854](https://real/a.jpg)\n"
    )
    with patch("local_deep_research.images.postprocessing.get_llm") as gl, \
         patch("local_deep_research.images.postprocessing.ImageEnhancer") as IEnh, \
         patch("local_deep_research.images.postprocessing.ImageStore") as IStore:
        gl.return_value = MagicMock()
        IEnh.return_value.enhance.return_value = dup_md
        store_inst = IStore.return_value
        store_inst.persist.return_value = {
            "https://real/a.jpg": "/images/rid/a.png"
        }
        store_inst.rewrite_markdown.side_effect = lambda md, m, **kw: md
        enhance_report_with_images(
            research_id="rid",
            clean_markdown="# \u5e7f\u5dde\u5efa\u7b51\n## A\n\u4ecb\u7ecd\n\n## B\n\u4ecb\u7ecd",
            results={"findings": findings}, db_session=MagicMock(),
            enable_images=True, vision_model="",
        )
    # Persist should be called with only ONE entry (dedup'd) — the
    # observable side effect of the DEDUPE pass running successfully.
    persist_args = store_inst.persist.call_args
    chosen_urls = persist_args.args[0]
    assert chosen_urls == ["https://real/a.jpg"]



# --- Strict context-entity gate integration tests ---------------------------


def test_postprocessing_passes_only_entity_eligible_images_to_enhancer():
    """The gate's `keep` decisions are the only URLs the enhancer sees."""
    import json

    findings = [{
        "search_results": [{
            "url": "https://source/page",
            "title": "\u5e7f\u5dde\u5854",
            "html_content": json.dumps([
                {
                    "url": "https://img/guangzhou.jpg",
                    "alt": "\u5e7f\u5dde\u5854",
                    "source_url": "https://source/page",
                    "source_title": "\u5e7f\u5dde\u5854",
                },
                {
                    "url": "https://img/chongqing.jpg",
                    "alt": "\u91cd\u5e86\u6d2a\u5d16\u6d1e",
                    "source_url": "https://source/page",
                    "source_title": "\u91cd\u5e86\u6d2a\u5d16\u6d1e",
                },
            ]),
        }],
    }]
    with patch.object(postprocessing, "get_llm", return_value=MagicMock()), \
         patch.object(postprocessing, "ImageEnhancer") as enhancer_cls, \
         patch.object(postprocessing, "ImageStore") as store_cls, \
         patch.object(postprocessing, "fill_section_images",
                      side_effect=AssertionError("fallback called"),
                      create=True):
        enhancer_cls.return_value.enhance.return_value = (
            "# \u5e7f\u5dde\u5efa\u7b51\n## \u5e7f\u5dde\u5854\n![\u5e7f\u5dde\u5854](https://img/guangzhou.jpg)"
        )
        store_cls.return_value.persist.return_value = {
            "https://img/guangzhou.jpg": "/images/rid/a.jpg"
        }
        enhance_report_with_images(
            research_id="rid",
            clean_markdown="# \u5e7f\u5dde\u5efa\u7b51\n## \u5e7f\u5dde\u5854\n\u5e7f\u5dde\u5854\u4ecb\u7ecd",
            results={"findings": findings},
            db_session=MagicMock(),
            enable_images=True,
            vision_model="",
        )
    passed_bank = enhancer_cls.return_value.enhance.call_args.args[1]
    assert passed_bank.all_urls() == ["https://img/guangzhou.jpg"]


def test_no_eligible_bank_skips_enhancer_and_preserves_markdown():
    """When the gate drops every candidate, the enhancer is never called
    and the original markdown is returned unchanged."""
    with patch.object(postprocessing, "get_llm", return_value=MagicMock()), \
         patch.object(postprocessing, "ImageEnhancer") as enhancer_cls, \
         patch.object(postprocessing, "ImageStore") as store_cls, \
         patch.object(postprocessing, "fill_section_images",
                      side_effect=AssertionError("fallback called"),
                      create=True):
        result = enhance_report_with_images(
            research_id="rid",
            clean_markdown="# \u5e7f\u5dde\u5efa\u7b51\n## \u5e7f\u5dde\u5854\n\u4ecb\u7ecd",
            results={"findings": [{"search_results": [{
                "html_content": '[{"url":"https://img/a.jpg","alt":"\u65c5\u6e38\u653b\u7565"}]'
            }]}]},
            db_session=MagicMock(),
            enable_images=True,
            vision_model="",
        )
    assert result == "# \u5e7f\u5dde\u5efa\u7b51\n## \u5e7f\u5dde\u5854\n\u4ecb\u7ecd"
    enhancer_cls.assert_not_called()
    store_cls.assert_not_called()


def test_postprocessing_never_invokes_fill_section_images_from_production():
    """The legacy section fallback is no longer wired into the production
    pipeline. Patching it to raise must be a no-op across the supported
    inputs."""
    import json

    findings = [{
        "search_results": [{
            "url": "https://source/page",
            "title": "\u5e7f\u5dde\u5854",
            "html_content": json.dumps([{
                "url": "https://img/guangzhou.jpg",
                "alt": "\u5e7f\u5dde\u5854",
                "source_url": "https://source/page",
                "source_title": "\u5e7f\u5dde\u5854",
            }]),
        }],
    }]
    with patch.object(postprocessing, "fill_section_images",
                      side_effect=AssertionError("fallback called"),
                      create=True), \
         patch.object(postprocessing, "get_llm", return_value=MagicMock()), \
         patch.object(postprocessing, "ImageEnhancer") as enhancer_cls, \
         patch.object(postprocessing, "ImageStore") as store_cls:
        enhancer_cls.return_value.enhance.return_value = (
            "# \u5e7f\u5dde\u5efa\u7b51\n## \u5e7f\u5dde\u5854\n![\u5e7f\u5dde\u5854](https://img/guangzhou.jpg)"
        )
        store_cls.return_value.persist.return_value = {
            "https://img/guangzhou.jpg": "/images/rid/a.jpg"
        }
        # Identity rewrite so the assertion sees the markdown itself
        # rather than a MagicMock return value.
        store_cls.return_value.rewrite_markdown.side_effect = (
            lambda md, mapping, **kw: md
        )
        out = enhance_report_with_images(
            research_id="rid",
            clean_markdown="# \u5e7f\u5dde\u5efa\u7b51\n## \u5e7f\u5dde\u5854\n\u5e7f\u5dde\u5854\u4ecb\u7ecd",
            results={"findings": findings},
            db_session=MagicMock(),
            enable_images=True,
            vision_model="",
        )
    assert "https://img/guangzhou.jpg" in out


def test_postprocessing_builds_enhancer_with_vision_fill_disabled():
    """The report-path enhancer must be constructed with
    `allow_vision_fill=False` so the legacy Vision fill cannot re-introduce
    alts behind the entity gate's back."""
    import json

    findings = [{
        "search_results": [{
            "url": "https://source/page",
            "title": "\u5e7f\u5dde\u5854",
            "html_content": json.dumps([{
                "url": "https://img/guangzhou.jpg",
                "alt": "\u5e7f\u5dde\u5854",
                "source_url": "https://source/page",
                "source_title": "\u5e7f\u5dde\u5854",
            }]),
        }],
    }]
    with patch.object(postprocessing, "get_llm", return_value=MagicMock()), \
         patch.object(postprocessing, "ImageEnhancer") as enhancer_cls, \
         patch.object(postprocessing, "ImageStore") as store_cls, \
         patch.object(postprocessing, "VisionDescriber") as vision_cls:
        enhancer_cls.return_value.enhance.return_value = (
            "# \u5e7f\u5dde\u5efa\u7b51\n## \u5e7f\u5dde\u5854\n![\u5e7f\u5dde\u5854](https://img/guangzhou.jpg)"
        )
        store_cls.return_value.persist.return_value = {
            "https://img/guangzhou.jpg": "/images/rid/a.jpg"
        }
        enhance_report_with_images(
            research_id="rid",
            clean_markdown="# \u5e7f\u5dde\u5efa\u7b51\n## \u5e7f\u5dde\u5854\n\u5e7f\u5dde\u5854\u4ecb\u7ecd",
            results={"findings": findings},
            db_session=MagicMock(),
            enable_images=True,
            vision_model="",
        )
    enhancer_kwargs = enhancer_cls.call_args.kwargs
    assert enhancer_kwargs.get("allow_vision_fill") is False
    # VisionDescriber is constructed for dependency compatibility but its
    # `describe` method must never be invoked from the report path.
    vision_inst = vision_cls.return_value
    vision_inst.describe.assert_not_called()
