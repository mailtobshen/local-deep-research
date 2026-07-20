"""Vision-LLM fallback to describe images that have no alt text."""
from __future__ import annotations

import base64
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class VisionDescriber:
    """Describes images via a vision-capable LLM. Disabled when no model configured."""

    def __init__(self, model_name: Optional[str]) -> None:
        self.model_name = (model_name or "").strip()
        self._llm = None
        if self.model_name:
            try:
                from ..config.llm_config import get_llm

                self._llm = get_llm(model_name=self.model_name)
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
