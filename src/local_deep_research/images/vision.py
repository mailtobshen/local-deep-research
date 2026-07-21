"""Vision-LLM fallback to describe images that have no alt text."""
from __future__ import annotations

import base64
import logging
from typing import Optional

from ..config.llm_config import _build_chat_model

logger = logging.getLogger(__name__)


class VisionDescriber:
    """Describes images via a vision-capable LLM. Disabled when no model configured."""

    def __init__(
        self,
        model_name: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self.model_name = (model_name or "").strip()
        self._base_url = (base_url or "").strip() or None
        self._api_key = api_key or None
        self._llm = None
        if self.model_name:
            try:
                # Decide provider from base_url heuristic:
                #   localhost / 127.0.0.1 / no base_url → openai_endpoint
                #     (covers Ollama-via-OpenAI-compat, LM Studio, vLLM).
                # For simplicity and to keep the public surface small,
                # always use openai_endpoint — ChatOpenAI works against
                # Ollama-via-/v1, OpenAI, OpenRouter, LM Studio, vLLM,
                # llama.cpp, etc.
                self._llm = _build_chat_model(
                    provider="openai_endpoint",
                    model_name=self.model_name,
                    base_url=self._base_url,
                    api_key=self._api_key,
                    settings_snapshot=None,
                )
            except Exception:
                logger.exception(
                    "Failed to init vision LLM %s; fallback disabled",
                    self.model_name,
                )
                self._llm = None

    @property
    def enabled(self) -> bool:
        return self._llm is not None

    def _download(self, image_url: str) -> bytes:
        from ..security.safe_requests import safe_get  # SSRF-safe HTTP client

        resp = safe_get(image_url, timeout=20, allow_private_ips=False)
        resp.raise_for_status()
        return resp.content

    def describe(self, image_url: str) -> Optional[str]:
        """Return a short alt description, or None on any failure."""
        if not self.enabled:
            return None
        try:
            data = self._download(image_url)
            b64 = base64.b64encode(data).decode("ascii")
            # LangChain multimodal HumanMessage with image_url.
            from langchain_core.messages import HumanMessage

            msg = HumanMessage(
                content=[
                    {
                        "type": "text",
                        "text": "Describe this image in one short Chinese sentence (<=30 chars). Output only the description.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ]
            )
            resp = self._llm.invoke([msg])
            text = str(getattr(resp, "content", "")).strip()
            return text[:60] or None
        except Exception:
            logger.debug("Vision describe failed for %s", image_url, exc_info=True)
            return None
