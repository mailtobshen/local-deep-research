from unittest.mock import patch, MagicMock
from local_deep_research.images import vision


def test_init_with_base_url_and_api_key_uses_openai_endpoint():
    with patch(
        "local_deep_research.images.vision._build_chat_model"
    ) as mock_build:
        mock_llm = MagicMock()
        mock_build.return_value = mock_llm
        desc = vision.VisionDescriber(
            model_name="gpt-4o",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
        )
    mock_build.assert_called_once_with(
        provider="openai_endpoint",
        model_name="gpt-4o",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        settings_snapshot=None,
    )
    assert desc.enabled is True


def test_init_empty_model_disables():
    desc = vision.VisionDescriber()
    assert desc.enabled is False
    assert desc._llm is None


def test_init_backward_compat_single_positional_arg():
    with patch(
        "local_deep_research.images.vision._build_chat_model"
    ) as mock_build:
        mock_build.return_value = MagicMock()
        desc = vision.VisionDescriber("llava")
    mock_build.assert_called_once()
    assert desc.enabled is True
    # Old API didn't pass base_url/api_key — both default to None
    kwargs = mock_build.call_args.kwargs
    assert kwargs["model_name"] == "llava"
    assert kwargs["base_url"] is None
    assert kwargs["api_key"] is None


def test_init_uses_ollama_for_localhost_url():
    with patch(
        "local_deep_research.images.vision._build_chat_model"
    ) as mock_build:
        mock_build.return_value = MagicMock()
        vision.VisionDescriber(
            model_name="llava",
            base_url="http://localhost:11434",
        )
    mock_build.assert_called_once_with(
        provider="openai_endpoint",
        model_name="llava",
        base_url="http://localhost:11434",
        api_key=None,
        settings_snapshot=None,
    )