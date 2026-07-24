# tests/images/test_alt_sanitizer.py
from local_deep_research.images.postprocessing import _safe_alt


def test_safe_alt_strips_brackets_and_newlines():
    assert (
        _safe_alt(
            "Pelayaran Malam Sungai Pearl Guangzhou "
            "[Menikmati Pemandangan Malam Menara Guangzhou + "
            "Kapal Bertema Kebangsaan Jinxi dengan Persembahan Langsung]"
        )
        == "Pelayaran Malam Sungai Pearl Guangzhou Menikmati Pemandangan Malam Menara Guangzhou + "
        "Kapal Bertema Kebangsaan Jinxi dengan Persembahan Langsung"[:120]
        + "…"
    )


def test_safe_alt_truncates_long():
    long_alt = "abc " * 100
    out = _safe_alt(long_alt)
    assert len(out) <= 121
    assert out.endswith("…")
