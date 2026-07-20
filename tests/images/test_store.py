# tests/images/test_store.py
from unittest.mock import MagicMock, patch
from local_deep_research.images.store import ImageStore


def test_persist_downloads_and_returns_routes(tmp_path):
    store = ImageStore("rid-123", db_session=MagicMock(), base_dir=tmp_path)
    with patch.object(store, "_download") as dl:
        dl.return_value = (b"\x89PNG fake", "image/png")
        routes = store.persist(["https://x/a.jpg"])
    assert "https://x/a.jpg" in routes
    route = routes["https://x/a.jpg"]
    assert route.startswith("/images/rid-123/")
    # local file created
    local_files = list((tmp_path / "rid-123").iterdir())
    assert len(local_files) == 1


def test_persist_skips_failed_download(tmp_path):
    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    with patch.object(store, "_download", side_effect=Exception("net")):
        assert store.persist(["https://x/a.jpg"]) == {}


def test_rewrite_markdown_replaces_urls():
    store = ImageStore("rid", MagicMock(), base_dir="/tmp")
    md = "![t](https://x/a.jpg) and ![u](https://y/b.jpg)"
    out = store.rewrite_markdown(md, {"https://x/a.jpg": "/images/rid/h1.png"})
    assert "/images/rid/h1.png" in out
    assert "https://y/b.jpg" in out  # unmapped url left intact


def test_persist_path_traversal_safe(tmp_path):
    store = ImageStore("..%2fevil", MagicMock(), base_dir=tmp_path)
    with patch.object(store, "_download") as dl:
        dl.return_value = (b"\x89PNG", "image/png")
        routes = store.persist(["https://x/a.jpg"])
    # route must contain only the safe research_id segment, no traversal
    route = routes["https://x/a.jpg"]
    assert ".." not in route
