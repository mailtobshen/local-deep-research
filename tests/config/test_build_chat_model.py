from unittest.mock import patch, MagicMock
from local_deep_research.config.llm_config import _build_chat_model


def test_build_chat_model_openai_endpoint_with_base_url():
    with patch(
        "local_deep_research.config.llm_config.ChatOpenAI"
    ) as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        llm = _build_chat_model(
            provider="openai_endpoint",
            model_name="llava",
            base_url="http://localhost:11434/v1",
            api_key="",
        )
    mock_cls.assert_called_once()
    kwargs = mock_cls.call_args.kwargs
    assert kwargs["model_name"] == "llava"
    assert kwargs["base_url"] == "http://localhost:11434/v1"
    # Empty string API key is normalized to None for some providers — just assert no exception
    assert llm is mock_instance


def test_build_chat_model_openai_provider():
    with patch(
        "local_deep_research.config.llm_config.ChatOpenAI"
    ) as mock_cls:
        _build_chat_model(
            provider="openai",
            model_name="gpt-4o",
            api_key="sk-test",
        )
    kwargs = mock_cls.call_args.kwargs
    assert kwargs["model_name"] == "gpt-4o"
    assert kwargs["api_key"] == "sk-test"


def test_build_chat_model_unknown_provider_raises():
    import pytest
    with pytest.raises(ValueError):
        _build_chat_model(provider="not-a-real-provider", model_name="x")