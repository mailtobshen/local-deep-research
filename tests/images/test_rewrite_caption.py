import re
from pathlib import Path

from local_deep_research.images.store import ImageStore


def test_rewrite_emits_figure_with_caption(tmp_path):
    store = ImageStore(
        research_id="r1", db_session=None, base_dir=Path(str(tmp_path))
    )
    url = "https://example.com/x.jpg"
    markdown = f"![上海酒店]({url})"
    out = store.rewrite_markdown(
        markdown,
        url_to_route={url: "/images/r1/abc.jpg"},
        url_to_size={url: (600, 400)},
        url_to_source={},
    )
    assert "<figure" in out and 'class="ldr-img"' in out
    assert "<figcaption>上海酒店</figcaption>" in out
    # img must carry width/height when size known
    assert re.search(r'<img[^>]*width="600"[^>]*height="400"', out)


def test_rewrite_caption_escapes_alt(tmp_path):
    store = ImageStore(
        research_id="r2", db_session=None, base_dir=Path(str(tmp_path))
    )
    url = "https://example.com/y.jpg"
    markdown = f'![a <b> & "q"]({url})'
    out = store.rewrite_markdown(
        markdown,
        url_to_route={url: "/images/r2/def.jpg"},
        url_to_size={url: (300, 200)},
        url_to_source={},
    )
    assert "<b>" not in out  # raw < escaped
    assert "&lt;b&gt;" in out
    assert "&amp;" in out
    assert "&quot;" in out or "&#x27;" in out or "&#34;" in out


def test_rewrite_no_size_omits_width_height(tmp_path):
    store = ImageStore(
        research_id="r3", db_session=None, base_dir=Path(str(tmp_path))
    )
    url = "https://example.com/z.jpg"
    markdown = f"![alt]({url})"
    out = store.rewrite_markdown(
        markdown,
        url_to_route={url: "/images/r3/ghi.jpg"},
        url_to_size={},
        url_to_source={},
    )
    assert "<figure" in out and "<figcaption>alt</figcaption>" in out
    assert "width=" not in out
