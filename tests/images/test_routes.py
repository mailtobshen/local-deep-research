# tests/images/test_routes.py
from unittest.mock import patch
from flask import Flask


def _app_ctx():
    return Flask(__name__).test_request_context()


def test_image_route_serves_file_and_blocks_traversal(tmp_path):
    rid_dir = tmp_path / "rid"
    rid_dir.mkdir()
    (rid_dir / "a.png").write_bytes(b"\x89PNG")
    from local_deep_research.web.routes import research_routes

    # Success path needs an app context (send_from_directory reads current_app).
    with patch.object(research_routes, "_IMAGES_BASE_DIR", tmp_path), _app_ctx():
        resp = research_routes.serve_research_image("rid", "a.png")
        assert getattr(resp, "status_code", 200) == 200
    # Traversal is rejected before send_from_directory -> (json, 404) tuple.
    with patch.object(research_routes, "_IMAGES_BASE_DIR", tmp_path), _app_ctx():
        resp = research_routes.serve_research_image("rid", "../../etc/passwd")
        status = resp[1] if isinstance(resp, tuple) else resp.status_code
        assert status == 404


def test_delete_research_removes_image_dir(tmp_path):
    from local_deep_research.web.routes import research_routes

    rid_dir = tmp_path / "rid"
    rid_dir.mkdir()
    (rid_dir / "a.png").write_bytes(b"x")
    with patch.object(research_routes, "_IMAGES_BASE_DIR", tmp_path):
        research_routes._cleanup_image_dir("rid")
    assert not rid_dir.exists()
    # idempotent when missing
    with patch.object(research_routes, "_IMAGES_BASE_DIR", tmp_path):
        research_routes._cleanup_image_dir("rid")  # no error
