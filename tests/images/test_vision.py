# tests/images/test_vision.py
from unittest.mock import MagicMock, patch
from local_deep_research.images.vision import VisionDescriber


def test_disabled_when_no_model():
    v = VisionDescriber(None)
    assert v.enabled is False
    assert v.describe("https://x/a.jpg") is None


def test_disabled_when_empty_model():
    assert VisionDescriber("").enabled is False


def test_describe_returns_alt_on_success():
    v = VisionDescriber("fake-vision-model")
    # Force enabled even though real get_llm may have failed in this env.
    v._llm = MagicMock()
    assert v.enabled is True
    fake_resp = MagicMock()
    fake_resp.content = "A tall tower at night"
    with patch.object(v._llm, "invoke", return_value=fake_resp) as mock_inv, \
         patch.object(v, "_download", return_value=b"\x89PNG fake bytes"):
        assert v.describe("https://x/a.jpg") == "A tall tower at night"
        mock_inv.assert_called_once()


def test_describe_returns_none_on_failure():
    v = VisionDescriber("fake-vision-model")
    v._llm = MagicMock()
    with patch.object(v, "_download", side_effect=Exception("network")):
        assert v.describe("https://x/a.jpg") is None
