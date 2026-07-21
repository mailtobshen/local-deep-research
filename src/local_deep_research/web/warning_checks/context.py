"""DB-backed context warning checks.

These functions take an open SQLAlchemy session — no Flask dependency.
"""

from typing import Optional

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from ...database.models import TokenUsage
from ..translations import _


def check_context_below_history(
    db_session: Session, local_context: int
) -> Optional[dict]:
    """Warn if the current context setting is below what 99% of past researches used.

    Requires at least 5 historical records to produce a meaningful percentile.
    """
    recent_contexts = (
        db_session.query(TokenUsage.context_limit)
        .filter(TokenUsage.context_limit.isnot(None))
        .order_by(desc(TokenUsage.timestamp))
        .limit(100)
        .all()
    )

    if not recent_contexts or len(recent_contexts) < 5:
        return None

    context_values = sorted([r[0] for r in recent_contexts if r[0]])
    if not context_values:
        return None

    # 1st percentile — 99% of researches used at least this much
    percentile_idx = max(0, int(len(context_values) * 0.01))
    min_safe_context = context_values[percentile_idx]

    if local_context >= min_safe_context:
        return None

    return {
        "type": "context_below_history",
        "icon": "📉",
        "title": _("Context Below Historical Usage"),
        "message": _(
            "Current context ({current}) is below the context window "
            "size that 99% of your past researches ran with "
            "(min safe: {safe}). This may cause truncation.",
            current=f"{local_context:,}",
            safe=f"{min_safe_context:,}",
        ),
        "dismissKey": "app.warnings.dismiss_context_reduced",
        "actionUrl": "/metrics/context-overflow",
        "actionLabel": _("View context metrics"),
    }


def check_context_truncation_history(
    db_session: Session, local_context: int
) -> Optional[dict]:
    """Warn if past researches experienced truncation at the same or higher context."""
    truncation_count = (
        db_session.query(func.count(TokenUsage.id))
        .filter(TokenUsage.context_truncated.is_(True))
        .filter(TokenUsage.context_limit >= local_context)
        .scalar()
    )

    if not truncation_count or truncation_count <= 0:
        return None

    return {
        "type": "context_truncation_history",
        "icon": "⚠️",
        "title": _("Previous Truncation Detected"),
        "message": _(
            "Research was truncated {count} time(s) with similar "
            "or higher context. Consider increasing context window.",
            count=truncation_count,
        ),
        "dismissKey": "app.warnings.dismiss_context_reduced",
        "actionUrl": "/metrics/context-overflow",
        "actionLabel": _("View context metrics"),
    }
