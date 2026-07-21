# tests/images/test_models.py
def test_image_model_columns():
    from local_deep_research.database.models import Image

    cols = {c.name for c in Image.__table__.columns}
    for required in {
        "id",
        "research_id",
        "original_url",
        "local_path",
        "local_route",
        "alt",
        "source_url",
        "source_title",
        "content_hash",
        "width",
        "height",
        "created_at",
    }:
        assert required in cols, required


def test_search_result_has_html_content():
    from local_deep_research.database.models import SearchResult

    cols = {c.name for c in SearchResult.__table__.columns}
    assert "html_content" in cols


def test_settings_registered():
    import json
    import os
    import local_deep_research

    pkg_dir = os.path.dirname(local_deep_research.__file__)
    path = os.path.join(pkg_dir, "defaults", "default_settings.json")
    with open(path) as f:
        d = json.load(f)
    assert "report.enable_images" in d
    assert d["report.enable_images"]["value"] is True
    assert "report.image_vision_model" in d
    assert d["report.image_vision_model"]["value"] == ""
