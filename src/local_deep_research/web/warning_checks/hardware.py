"""Pure hardware/settings-based warning checks.

These functions have zero dependencies on Flask or SQLAlchemy —
they take primitive values and return warning dicts (or None).
"""

from typing import Optional

from ..translations import _

LOCAL_PROVIDERS = frozenset({"ollama", "llamacpp", "lmstudio"})


def check_high_context(
    provider: str, local_context: int, dismissed: bool
) -> Optional[dict]:
    """Return a high_context warning dict if context exceeds 8192 for a local provider."""
    if provider not in LOCAL_PROVIDERS:
        return None
    if local_context <= 8192:
        return None
    if dismissed:
        return None

    return {
        "type": "high_context",
        "icon": "⚠️",
        "title": _("High Context Warning"),
        "message": _(
            "Context size ({size} tokens) requires sufficient VRAM. "
            "This is recommended for the langgraph-agent strategy. "
            "If you experience slowdowns, reduce context size in settings "
            "and switch to the source-based strategy instead. "
            "Tip: check the metrics page in each research history entry "
            "to monitor actual token usage and VRAM consumption.",
            size=f"{local_context:,}",
        ),
        "dismissKey": "app.warnings.dismiss_high_context",
        "actionUrl": "/metrics/context-overflow",
        "actionLabel": _("View context metrics"),
    }


def check_model_mismatch(
    provider: str, model: str, local_context: int, dismissed: bool
) -> Optional[dict]:
    """Return a model_mismatch warning dict for large models with high context."""
    if not model:
        return None
    if provider not in LOCAL_PROVIDERS:
        return None
    if "70b" not in model.lower():
        return None
    if local_context <= 8192:
        return None
    if dismissed:
        return None

    return {
        "type": "model_mismatch",
        "icon": "🧠",
        "title": _("Model & Context Warning"),
        "message": _(
            "Large model ({model}) with high context ({size}) "
            "may exceed VRAM. Consider reducing context size or upgrading "
            "GPU memory.",
            model=model,
            size=f"{local_context:,}",
        ),
        "dismissKey": "app.warnings.dismiss_model_mismatch",
    }


def check_legacy_server_config(dismissed: bool) -> Optional[dict]:
    """Return a warning only if server_config.json has non-default values."""
    from ..server_config import has_legacy_customizations

    if dismissed:
        return None
    if not has_legacy_customizations():
        return None
    return {
        "type": "legacy_server_config",
        "icon": "ℹ️",
        "title": _("server_config.json Detected"),
        "message": _(
            "A server_config.json file was found with non-default settings. "
            "Environment variables are the preferred configuration method. "
            "See the documentation for migration details."
        ),
        "dismissKey": "app.warnings.dismiss_legacy_config",
    }
