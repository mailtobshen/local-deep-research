"""
Flask endpoints for security monitoring and metrics.

Provides administrative endpoints for viewing security events,
TLS fallback statistics, and proxy bypass monitoring.
"""

from flask import Blueprint, jsonify, request
from functools import wraps

from ..security.security_monitor import get_security_monitor

security_bp = Blueprint("security", __name__, url_prefix="/api/admin/security")


def require_admin(f):
    """Decorator to require admin access for security endpoints."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # TODO: Implement proper authentication/authorization
        # For now, this is a placeholder that should be replaced with
        # proper admin authentication (e.g., @login_required, admin role check)
        return f(*args, **kwargs)
    return decorated_function


@security_bp.route("/events", methods=["GET"])
@require_admin
def get_security_events():
    """
    Get security events with optional filtering.

    Query Parameters:
        event_type: Filter by event type (optional)
        severity: Filter by severity level (optional)
        limit: Maximum number of events to return (default: 100)

    Returns:
        JSON array of security events
    """
    event_type = request.args.get("event_type")
    severity = request.args.get("severity")
    limit = min(int(request.args.get("limit", 100)), 1000)  # Cap at 1000

    monitor = get_security_monitor()
    events = monitor.get_events(event_type=event_type, severity=severity, limit=limit)

    return jsonify({
        "events": events,
        "count": len(events),
    })


@security_bp.route("/statistics", methods=["GET"])
@require_admin
def get_security_statistics():
    """
    Get aggregated security statistics.

    Returns:
        JSON object with security metrics and event counts
    """
    monitor = get_security_monitor()
    stats = monitor.get_statistics()

    return jsonify(stats)


@security_bp.route("/tls-fallbacks", methods=["GET"])
@require_admin
def get_tls_fallback_summary():
    """
    Get TLS fallback summary for monitoring.

    Returns:
        JSON object with TLS fallback statistics
    """
    monitor = get_security_monitor()
    summary = monitor.get_tls_fallback_summary()

    return jsonify(summary)


@security_bp.route("/health", methods=["GET"])
@require_admin
def security_monitoring_health():
    """
    Health check endpoint for security monitoring.

    Returns:
        JSON object indicating monitoring system health
    """
    monitor = get_security_monitor()
    stats = monitor.get_statistics()

    health_status = {
        "monitoring_active": stats.get("monitoring_active", False),
        "total_events_tracked": stats.get("total_events", 0),
        "recent_activity": stats.get("last_hour_count", 0),
        "system_healthy": True,  # Can be enhanced with actual health checks
    }

    # If no recent events but system is active, still healthy
    if health_status["recent_activity"] == 0 and health_status["monitoring_active"]:
        health_status["status_message"] = "No recent security events - system normal"
    elif health_status["recent_activity"] > 100:
        health_status["system_healthy"] = False
        health_status["status_message"] = "High volume of security events detected"
    else:
        health_status["status_message"] = "Security monitoring active"

    return jsonify(health_status)


@security_bp.route("/clear-events", methods=["POST"])
@require_admin
def clear_security_events():
    """
    Clear all security events from memory (with confirmation).

    This endpoint should be used carefully as it deletes monitoring data.
    Consider implementing a confirmation mechanism.

    Returns:
        JSON response indicating success/failure
    """
    # TODO: Implement confirmation mechanism (e.g., require ?confirm=true)
    confirm = request.args.get("confirm") == "true"

    if not confirm:
        return jsonify({
            "error": "Confirmation required",
            "message": "Add ?confirm=true to confirm event deletion"
        }), 400

    try:
        monitor = get_security_monitor()
        monitor._events.clear()
        monitor._counters.clear()

        return jsonify({
            "success": True,
            "message": "Security events cleared successfully"
        })
    except Exception as e:
        return jsonify({
            "error": str(e),
            "message": "Failed to clear security events"
        }), 500