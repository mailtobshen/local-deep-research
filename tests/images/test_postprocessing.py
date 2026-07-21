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
            "url": "https://src/page", "title": "Page",
            "html_content": json.dumps([{
                "url": "https://real/a.jpg", "alt": "tower",
                "source_url": "https://src/page", "source_title": "Page",
                "width": 800, "height": 600,
            }]),
        }],
    }]
    with patch("local_deep_research.images.postprocessing.get_llm") as gl, \
         patch("local_deep_research.images.postprocessing.ImageEnhancer") as IEnh, \
         patch("local_deep_research.images.postprocessing.ImageStore") as IStore:
        gl.return_value = MagicMock()
        inst = IEnh.return_value
        inst.enhance.return_value = "# R\n\n![tower](https://real/a.jpg)\n"
        store_inst = IStore.return_value
        store_inst.persist.return_value = {"https://real/a.jpg": "/images/rid/a.png"}
        store_inst.rewrite_markdown.side_effect = lambda md, m: md.replace("https://real/a.jpg", "/images/rid/a.png")
        out = enhance_report_with_images(
            research_id="rid", clean_markdown="# R", results={"findings": findings},
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
    findings = [{
        "search_results": [{
            "html_content": '[{"url": "https://real/a.jpg", "alt": ""}]',
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
        enhancer_mock.return_value.enhance.return_value = "![alt text](https://real/a.jpg)"
        store_mock.return_value.persist.return_value = {"https://real/a.jpg": "/images/rid/a.png"}
        enhance_report_with_images(
            research_id="rid",
            clean_markdown="# R",
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
