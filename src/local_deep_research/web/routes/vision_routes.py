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
        # Distinguish common failure modes so the WebUI can show
        # actionable feedback instead of a generic "404 page not
        # found" string that actually came from a nested LangChain
        # exception wrapper.
        err_msg, err_kind, err_status = _classify_vision_error(e, model, url)
        logger.info(f"Vision test_connection failed: {err_kind} {err_msg}")
        return jsonify(
            {
                "success": False,
                "error": err_msg,
                "error_kind": err_kind,
                "status_code": err_status,
                "latency_ms": latency_ms,
            }
        ), 200


def _classify_vision_error(
    e: Exception, model_name: str = "", endpoint_url: str = ""
) -> tuple:
    """Return (message, kind, status_code) for a vision test failure.

    LangChain wraps upstream OpenAI SDK errors, so messages look like:
      BadRequestError("Error code: 404 - {'error': {'message':
        \"model 'llava' not found\", 'type': '...', 'code': '...'}}")

    We try to extract the upstream status code and message instead
    of forwarding the nested wrapper text. Falls back to the wrapper
    text if nothing better is found.
    """
    import json as _json
    import re as _re

    raw = str(e) or type(e).__name__
    err_kind = "unknown"
    err_status = None
    err_msg = raw

    # LangChain's OpenAI chat wrapper prefixes HTTP status:
    #   "Error code: 404 - {body}".
    # Capture that status, then strip the wrapper to get the body.
    m = _re.search(
        r"Error code:\s*(\d{3})\s*-\s*(.*)", raw, flags=_re.DOTALL
    )
    upstream_status = None
    body_text = raw
    if m:
        try:
            upstream_status = int(m.group(1))
        except ValueError:
            upstream_status = None
        body_text = m.group(2).strip()

    # Try to parse the body as JSON (OpenAI-style error envelope).
    parsed_body = None
    if body_text:
        try:
            parsed_body = _json.loads(body_text)
        except (ValueError, TypeError):
            # Body might be a stringly-wrapped JSON like
            # "{'error': {'message': '...'}}". Try ast.literal_eval.
            import ast

            try:
                parsed_body = ast.literal_eval(body_text)
            except (ValueError, SyntaxError):
                parsed_body = None

    nested = None
    if isinstance(parsed_body, dict):
        nested = (
            parsed_body.get("error", {}).get("message")
            if isinstance(parsed_body.get("error"), dict)
            else parsed_body.get("message")
        )
    if not nested and isinstance(parsed_body, dict):
        nested = parsed_body.get("error")
    if isinstance(nested, dict):
        nested = nested.get("message") or nested.get("detail")

    if upstream_status is not None:
        err_status = upstream_status
    elif getattr(e, "status_code", None):
        err_status = int(e.status_code)
    elif getattr(e, "response", None) and getattr(e.response, "status_code", None):
        err_status = int(e.response.status_code)

    # Classify by upstream status.
    if err_status == 401:
        err_kind = "auth"
        err_msg = "认证失败 (401) — 检查 API key 是否正确"
    elif err_status == 403:
        err_kind = "forbidden"
        err_msg = "无权限 (403) — API key 可能无 vision 权限"
    elif err_status == 404:
        # Distinguish "endpoint doesn't exist" vs "model not found"
        # vs "model exists but no vision variant" by inspecting the
        # upstream message text.
        body_lower = body_text.lower()
        if "model" in body_lower and (
            "not found" in body_lower or "does not exist" in body_lower
        ):
            err_kind = "model_not_found"
            err_msg = (
                f"模型 '{model_name}' 不存在或服务端未下载 — "
                "如用 Ollama 请先 ollama pull 该模型"
            )
        elif "not found" in body_lower or "no such" in body_lower:
            err_kind = "endpoint_not_found"
            err_msg = f"端点路径不存在 (404) — 检查 URL 是否含 /v1 等路径后缀"
        else:
            err_kind = "not_found"
            err_msg = "资源未找到 (404) — 检查 URL/模型名拼写"
    elif err_status == 429:
        err_kind = "rate_limited"
        err_msg = "请求被限流 (429) — 稍后重试"
    elif err_status in (500, 502, 503):
        err_kind = "server_error"
        err_msg = f"服务端错误 ({err_status}) — Ollama 可能 OOM/未启动/崩溃"
    elif err_status is not None and err_status >= 400:
        err_kind = "http_error"
        err_msg = f"HTTP {err_status} — {nested or body_text[:200]}"
    else:
        # Network / non-HTTP failures: connect refused, timeout, DNS,
        # OOM, etc. Inspect the exception type.
        name = type(e).__name__.lower()
        msg_lower = raw.lower()
        if "connection" in msg_lower and (
            "refused" in msg_lower or "reset" in msg_lower
        ):
            err_kind = "connection_refused"
            err_msg = (
                f"连接被拒 — Ollama/服务端未运行或 URL/端口错: {endpoint_url}"
            )
        elif "timeout" in name or "timeout" in msg_lower:
            err_kind = "timeout"
            err_msg = (
                "连接超时 — 模型首次加载或网络慢，"
                "可点 '重试' 或增加超时"
            )
        elif "out of memory" in msg_lower or "oom" in msg_lower:
            err_kind = "out_of_memory"
            err_msg = (
                "显存不足 — Ollama 模型需要 16GB+ GPU 显存，"
                "可换更小模型（如 moondream2 1.8B）"
            )
        elif "name" in msg_lower and "resolve" in msg_lower:
            err_kind = "dns_error"
            err_msg = f"DNS 解析失败 — 检查 URL: {endpoint_url}"
        elif "ssl" in msg_lower or "certificate" in msg_lower:
            err_kind = "ssl_error"
            err_msg = "SSL/TLS 错误 — 检查证书"
        else:
            err_kind = "network"
            err_msg = nested or body_text[:200] or raw[:200]

    # If we have a nested message but the err_msg is still generic,
    # prefer the nested message.
    if (
        nested
        and err_kind in ("http_error", "network", "unknown")
        and err_msg in (raw, body_text[:200])
    ):
        err_msg = f"{err_msg} — {nested}"

    return err_msg, err_kind, err_status
