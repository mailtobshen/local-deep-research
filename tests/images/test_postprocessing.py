# tests/images/test_postprocessing.py
from unittest.mock import MagicMock, patch
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


def test_enabled_builds_bank_from_findings_and_enhances():
    findings = [
        {
            "search_results": [
                {
                    "url": "https://src/page",
                    "title": "Page",
                    "html_content": '<img src="https://real/a.jpg" alt="tower" width="200">',
                }
            ],
        }
    ]
    with patch(
        "local_deep_research.images.postprocessing.get_llm"
    ) as gl, patch(
        "local_deep_research.images.postprocessing.ImageEnhancer"
    ) as IEnh, patch(
        "local_deep_research.images.postprocessing.ImageStore"
    ) as IStore:
        gl.return_value = MagicMock()
        inst = IEnh.return_value
        inst.enhance.return_value = "# R\n\n![tower](https://real/a.jpg)\n"
        store_inst = IStore.return_value
        store_inst.persist.return_value = {"https://real/a.jpg": "/images/rid/a.png"}
        store_inst.rewrite_markdown.side_effect = lambda md, m: md.replace(
            "https://real/a.jpg", "/images/rid/a.png"
        )
        out = enhance_report_with_images(
            research_id="rid",
            clean_markdown="# R",
            results={"findings": findings},
            db_session=MagicMock(),
            enable_images=True,
            vision_model="",
        )
    assert "/images/rid/a.png" in out
    IEnh.assert_called_once()
