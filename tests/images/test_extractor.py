from local_deep_research.images.extractor import extract_images, ExtractedImage


def test_extracts_content_image_with_alt():
    html = '<html><body><img src="https://example.com/a/tower.jpg" alt="Canton Tower" width="800" height="600"></body></html>'
    imgs = extract_images(html, "https://example.com/a", "Example Page")
    assert len(imgs) == 1
    assert imgs[0].url == "https://example.com/a/tower.jpg"
    assert imgs[0].alt == "Canton Tower"
    assert imgs[0].source_url == "https://example.com/a"
    assert imgs[0].source_title == "Example Page"
    assert imgs[0].width == 800


def test_skips_data_uri():
    html = '<img src="data:image/png;base64,iVBORw0KGgo=" alt="x">'
    assert extract_images(html, "https://example.com", "t") == []


def test_skips_tiny_icon():
    html = '<img src="https://example.com/icon.png" width="16" height="16">'
    assert extract_images(html, "https://example.com", "t") == []


def test_skips_blacklisted_url_keywords():
    for kw in ["logo", "icon", "avatar", "sprite", "tracker", "blank.gif"]:
        html = f'<img src="https://example.com/{kw}.png" width="200" height="200">'
        assert extract_images(html, "https://example.com", "t") == [], kw


def test_resolves_relative_url():
    html = '<img src="/img/tower.jpg" alt="t" width="200">'
    imgs = extract_images(html, "https://example.com/page", "p")
    assert imgs[0].url == "https://example.com/img/tower.jpg"


def test_alt_empty_string_preserved():
    html = '<img src="https://example.com/x.jpg" width="200">'
    imgs = extract_images(html, "https://example.com", "t")
    assert len(imgs) == 1
    assert imgs[0].alt == ""


def test_missing_width_height_kept_when_url_ok():
    html = '<img src="https://example.com/big.jpg" alt="t">'
    imgs = extract_images(html, "https://example.com", "t")
    assert len(imgs) == 1
    assert imgs[0].width is None


def test_root_selector_scopes_to_article():
    html = (
        '<article>'
        '  <img src="https://example.com/a/main.jpg" alt="main" width="200">'
        '</article>'
        '<aside>'
        '  <img src="https://example.com/b/side.jpg" alt="side" width="200">'
        '</aside>'
    )
    scoped = extract_images(html, "https://example.com", "p", roots=["article"])
    assert [i.url for i in scoped] == ["https://example.com/a/main.jpg"]
    full = extract_images(html, "https://example.com", "p", roots=[])
    assert {i.url for i in full} == {
        "https://example.com/a/main.jpg",
        "https://example.com/b/side.jpg",
    }


def test_root_selector_falls_back_when_no_match():
    """Pages without any content-root selector still return their images."""
    html = '<img src="https://example.com/x.jpg" alt="t" width="200">'
    imgs = extract_images(html, "https://example.com", "t", roots=["article"])
    assert [i.url for i in imgs] == ["https://example.com/x.jpg"]


def test_roots_chain_picks_first_subtree_with_images():
    """When <article> is empty but <main> has images, <main> wins."""
    html = (
        '<article></article>'
        '<main>'
        '  <img src="https://example.com/m/main.jpg" alt="m" width="200">'
        '</main>'
        '<footer>'
        '  <img src="https://example.com/f/foot.jpg" alt="f" width="200">'
        '</footer>'
    )
    imgs = extract_images(html, "https://example.com", "p")
    assert [i.url for i in imgs] == ["https://example.com/m/main.jpg"]


def test_roots_chain_falls_back_to_full_page_when_all_empty():
    """When every selector matches an empty subtree, fall back to whole doc."""
    html = (
        '<article></article>'
        '<main></main>'
        '<div class="content">'
        '  <img src="https://example.com/c/x.jpg" alt="c" width="200">'
        '</div>'
    )
    imgs = extract_images(html, "https://example.com", "p")
    # Falls back to full-page extraction since none of the default roots
    # actually contain an <img>.
    assert [i.url for i in imgs] == ["https://example.com/c/x.jpg"]


def test_alt_falls_back_to_figcaption_when_img_has_no_alt():
    """Wikipedia-style <figure><img><figcaption>: when the <img> has no
    alt, the sibling <figcaption> text is used as alt (it is the author's
    description of the image)."""
    html = (
        '<figure class="mw-default-size">'
        '  <a href="/wiki/File:The_HSBC_Building.jpg">'
        '    <img src="//upload.wikimedia.org/wikipedia/commons/thumb/4/41/The_HSBC_Building.jpg/250px-The_HSBC_Building.jpg"'
        '         width="250" height="174">'
        '  </a>'
        '  <figcaption>第二代汇丰银行大楼以及建造中的江海关大楼</figcaption>'
        '</figure>'
    )
    imgs = extract_images(html, "https://zh.wikipedia.org/wiki/外滩", "外滩")
    assert len(imgs) == 1
    assert imgs[0].alt == "第二代汇丰银行大楼以及建造中的江海关大楼"


def test_explicit_alt_wins_over_figcaption():
    """When both an explicit alt AND a figcaption exist, the explicit alt wins
    (figcaption is only a fallback)."""
    html = (
        '<figure><img src="https://example.com/a/x.jpg" alt="explicit alt" width="200">'
        '<figcaption>caption text</figcaption></figure>'
    )
    imgs = extract_images(html, "https://zh.wikipedia.org/wiki/x", "t")
    assert imgs[0].alt == "explicit alt"


def test_figcaption_fallback_scoped_to_wikipedia_only():
    """The <figure>/<figcaption> fallback is scoped to wikipedia.org pages —
    on an unrelated domain, a <figcaption> must NOT be mined for alt (it is
    the author's description only in the wiki/media context we tuned for)."""
    html = (
        '<figure>'
        '  <img src="https://example.com/a/tower.jpg" width="250" height="174">'
        '  <figcaption>some caption text</figcaption>'
        '</figure>'
    )
    imgs = extract_images(html, "https://example.com/article", "Article")
    assert len(imgs) == 1
    # Non-wikipedia: figcaption is NOT used. The filename "tower" carries an
    # entity, so the filename fallback yields it (proving we skipped figcaption
    # but the rest of the chain is intact).
    assert imgs[0].alt == "tower"


def test_figcaption_fallback_works_on_wikipedia_source():
    """Same DOM on wikipedia.org DOES use the figcaption."""
    html = (
        '<figure>'
        '  <img src="//upload.wikimedia.org/wikipedia/commons/a/ab/Tower.jpg" width="250" height="174">'
        '  <figcaption>A famous tower</figcaption>'
        '</figure>'
    )
    imgs = extract_images(html, "https://en.wikipedia.org/wiki/Tower", "Tower")
    assert imgs[0].alt == "A famous tower"


def test_figcaption_fallback_works_on_wikimedia_img_host():
    """The figcaption fallback also fires when the <img> is served from
    upload.wikimedia.org even if source_url parsing is ambiguous."""
    html = (
        '<figure>'
        '  <img src="https://upload.wikimedia.org/wikipedia/commons/a/ab/Tower.jpg" width="250" height="174">'
        '  <figcaption>wikimedia caption</figcaption>'
        '</figure>'
    )
    imgs = extract_images(html, "https://example.com/unknown", "u")
    assert imgs[0].alt == "wikimedia caption"


def test_alt_falls_back_to_filename_entity_when_no_alt_no_figcaption():
    """A filename carrying a named entity (e.g. a person's name) yields a
    human-readable alt when the <img> has neither alt nor figcaption.
    Steven_Spielberg_(Berlinale_2023).jpg -> "Steven Spielberg"."""
    html = (
        '<a href="/wiki/File:MKr25402_Steven_Spielberg_(Berlinale_2023).jpg">'
        '  <img src="//upload.wikimedia.org/wikipedia/commons/thumb/4/4d/'
        'MKr25402_Steven_Spielberg_%28Berlinale_2023%29.jpg/250px-'
        'MKr25402_Steven_Spielberg_%28Berlinale_2023%29.jpg"'
        '       width="225" height="337">'
        '</a>'
    )
    imgs = extract_images(html, "https://en.wikipedia.org/wiki/Steven_Spielberg", "t")
    assert len(imgs) == 1
    assert imgs[0].alt == "Steven Spielberg"


def test_filename_alt_strips_pure_numeric_prefix_and_brackets():
    """Filename MKr25402_<name>(...).jpg: the leading all-digit/alnum code
    and the parenthesized clause are removed, leaving the name."""
    html = '<img src="https://upload.wikimedia.org/x/ABC12345_Statue_of_Liberty_(daytime).jpg" width="200">'
    imgs = extract_images(html, "https://example.com", "t")
    assert imgs[0].alt == "Statue of Liberty"


def test_generic_filename_yields_empty_alt():
    """A filename with no recognizable entity (x.jpg, img.jpg, 1.jpg) does NOT
    synthesize a meaningless alt — it stays empty, preserving the
    'empty alt preserved' contract for non-descriptive filenames."""
    assert _filename_alt_only("https://example.com/x.jpg") == ""
    assert _filename_alt_only("https://example.com/img/1.png") == ""
    assert _filename_alt_only("https://example.com/image.jpg") == ""


def test_filename_alt_decodes_percent_encoding():
    """Spaces encoded as %20 / %28 / %29 in URLs decode to their characters
    before entity extraction."""
    assert _filename_alt_only(
        "https://example.com/Jane_Doe_%282023%29.jpg"
    ) == "Jane Doe"


def test_filename_alt_rejects_pure_hash():
    """A bare hex/content-hash filename (Baidu BOS, CDN hashes) is NOT a
    named entity — it must yield '' so we don't synthesize a garbage alt."""
    assert _filename_alt_only(
        "https://bkimg.cdn.bcebos.com/smart/838ba61ea8d3fd1f41340279351c321f95cad1c8f16c"
    ) == ""
    assert _filename_alt_only(
        "https://example.com/d41d8cd98f00b204e9800998ecf8427e.png"
    ) == ""


def test_baike_alt_from_title_span():
    """Baidu Baike lemma picture: the <img> has no alt/figcaption, but the
    enclosing picture div has a .titleSpan whose text is the caption. The
    alt fallback chain must pick it up (Baike domain only)."""
    html = (
        '<div class="lemmaPicture_Slljq" data-single-image="{&quot;title&quot;:&quot;远眺陆家嘴&quot;}">'
        '  <a class="imageLink_tqZJ_" href="/pic/x" target="_blank" title="远眺陆家嘴">'
        '    <img src="https://bkimg.cdn.bcebos.com/smart/838ba61ea8d3fd1f41340279351c321f95cad1c8f16c"'
        '         class="picture_cDlsk" width="599" height="250">'
        '  </a>'
        '  <span class="titleSpan_UqY5D richTest_rbLrd"><span>远眺陆家嘴</span></span>'
        '</div>'
    )
    imgs = extract_images(html, "https://baike.baidu.com/item/陆家嘴", "陆家嘴")
    assert len(imgs) == 1
    assert imgs[0].alt == "远眺陆家嘴"


def test_baike_alt_falls_back_to_anchor_title_when_no_title_span():
    """When .titleSpan is absent but the wrapping <a> has title, use it."""
    html = (
        '<div class="lemmaPicture_Slljq">'
        '  <a class="imageLink_tqZJ_" href="/pic/x" title="东方明珠塔夜景">'
        '    <img src="https://bkimg.cdn.bcebos.com/pic/abc123.jpg" width="300">'
        '  </a>'
        '</div>'
    )
    imgs = extract_images(html, "https://baike.baidu.com/item/东方明珠", "东方明珠")
    assert imgs[0].alt == "东方明珠塔夜景"


def test_baike_alt_falls_back_to_data_single_image_json():
    """When neither .titleSpan nor <a title> exist, parse the
    data-single-image JSON attribute's "title" field."""
    html = (
        '<div class="lemmaPicture_Slljq" '
        '      data-single-image="{&quot;title&quot;:&quot;外滩全景&quot;,&quot;url&quot;:&quot;https://x/y.jpg&quot;}">'
        '  <img src="https://bkimg.cdn.bcebos.com/pic/abc123.jpg" width="300">'
        '</div>'
    )
    imgs = extract_images(html, "https://baike.baidu.com/item/外滩", "外滩")
    assert imgs[0].alt == "外滩全景"


def test_baike_alt_not_applied_off_baike_domain():
    """The Baike structural fallback is scoped to baike.baidu.com — on
    another domain, the same DOM does NOT trigger it (no spurious <a title>
    capture from an unrelated site's hover tooltip)."""
    html = (
        '<div><a href="/x" title="some tooltip"><img src="https://example.com/a.jpg" width="200"></a></div>'
    )
    imgs = extract_images(html, "https://example.com/page", "p")
    # example.com + nondescript filename a.jpg -> empty alt (no Baike fallback fired)
    assert imgs[0].alt == ""


# --- helpers used by the tests above -----------------------------
from local_deep_research.images.extractor import _alt_from_filename  # noqa: E402


def _filename_alt_only(url: str) -> str:
    """Expose the filename->alt transform in isolation for unit tests."""
    return _alt_from_filename(url)
