"""POST /api/vision/test_connection — verify a vision endpoint config works.

Sends a 1x1 transparent PNG + "Reply with the single word: ok" through
the configured endpoint and reports whether the call succeeded. Useful
for users to validate their vision model + URL + API key before running
a full research.
"""
from __future__ import annotations

import base64
import logging
import time
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from ...config.llm_config import _build_chat_model

logger = logging.getLogger(__name__)

# 1x1 transparent PNG. Minimal valid base64 image — does not need to be
# rendered by the model, just needs to be parseable.
_1X1_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)

# 1-char "vision probe" message. Asks for a 1-word reply so the call
# is fast and cheap (no actual image understanding needed; we just want
# to confirm the endpoint is reachable and the model accepts multimodal
# input).
_PROBE_TEXT = "Reply with the single word: ok"


vision_bp = Blueprint("vision", __name__)


@vision_bp.route("/test_connection", methods=["POST"])
def test_vision_connection():
    """Verify a vision endpoint is reachable and accepts multimodal input."""
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    api_key = body.get("api_key") or ""
    model = (body.get("model") or "").strip()

    if not url or not model:
        return jsonify(
            {
                "success": False,
                "error": "Both 'url' and 'model' are required.",
            }
        ), 200

    t0 = time.time()
    try:
        llm = _build_chat_model(
            provider="openai_endpoint",
            model_name=model,
            base_url=url,
            api_key=api_key,
        )

        # Build the multimodal probe message.
        from langchain_core.messages import HumanMessage

        msg = HumanMessage(
            content=[
                {"type": "text", "text": _PROBE_TEXT},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{_1X1_PNG_BASE64}"},
                },
            ]
        )
        response = llm.invoke([msg])
        content = getattr(response, "content", None) or str(response)
        latency_ms = int((time.time() - t0) * 1000)
        return jsonify(
            {
                "success": True,
                "response": str(content)[:200],
                "latency_ms": latency_ms,
            }
        ), 200
    except Exception as e:
        latency_ms = int((time.time() - t0) * 1000)
        err = str(e) or type(e).__name__
        logger.info(f"Vision test_connection failed: {err}")
        # Try to extract status code from common error message shapes.
        status_code = None
        for needle in ("401", "403", "404", "429", "500", "502", "503"):
            if needle in err:
                status_code = int(needle)
                break
        return jsonify(
            {
                "success": False,
                "error": err,
                "status_code": status_code,
                "latency_ms": latency_ms,
            }
        ), 200
