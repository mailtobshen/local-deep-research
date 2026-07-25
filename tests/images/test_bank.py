# tests/images/test_bank.py
from local_deep_research.images.extractor import ExtractedImage
from local_deep_research.images.bank import ImageBank

def _img(url, alt=""):
    return ExtractedImage(url=url, alt=alt, source_url="s", source_title="t", width=None, height=None)

def test_dedupes_by_url():
    b = ImageBank()
    b.add([_img("https://x/a.jpg", "A"), _img("https://x/a.jpg", "A")])
    assert len(b.all_urls()) == 1

def test_groups_by_alt_presence():
    b = ImageBank()
    b.add([_img("https://x/a.jpg", "A"), _img("https://x/b.jpg", "")])
    assert [i.url for i in b.candidates_with_alt()] == ["https://x/a.jpg"]
    assert [i.url for i in b.candidates_without_alt()] == ["https://x/b.jpg"]

def test_set_alt_moves_image_to_with_alt():
    b = ImageBank()
    b.add([_img("https://x/b.jpg", "")])
    b.set_alt("https://x/b.jpg", "tower")
    assert [i.url for i in b.candidates_with_alt()] == ["https://x/b.jpg"]
    assert b.candidates_without_alt() == []

def test_without_alt_respects_limit():
    b = ImageBank()
    b.add([_img(f"https://x/{i}.jpg", "") for i in range(50)])
    assert len(b.candidates_without_alt(limit=20)) == 20


def image(url):
    return ExtractedImage(url, "广州塔", "https://source", "", None, None)


def test_subset_preserves_order_and_does_not_expose_private_map():
    bank = ImageBank()
    bank.add([image("https://a"), image("https://b")])

    subset = bank.subset(["https://b", "https://missing", "https://a"])

    assert subset.all_urls() == ["https://b", "https://a"]
    assert subset.candidates_with_alt()[0].url == "https://b"
