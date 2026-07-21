"""
Security monitoring and metrics collection for LDR.

This module provides centralized security event tracking and metrics,
focusing on TLS fallback events, proxy usage, and security-relevant
operations that need monitoring and alerting.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from loguru import logger


class SecurityEvent:
    """Represents a security-relevant event with structured metadata."""

    def __init__(
        self,
        event_type: str,
        severity: str,
        message: str,
        url: Optional[str] = None,
        **metadata,
    ):
        self.event_type = event_type
        self.severity = severity  # info, warning, error, critical
        self.message = message
        self.url = url
        self.metadata = metadata
        self.timestamp = datetime.utcnow()

    def to_dict(self) -> dict:
        """Convert event to dictionary for serialization."""
        return {
            "event_type": self.event_type,
            "severity": self.severity,
            "message": self.message,
            "url": self.url,
            "timestamp": self.timestamp.isoformat(),
            **self.metadata,
        }


class SecurityMonitor:
    """
    Centralized security monitoring and metrics collection.

    Tracks security-relevant events with thread-safe operations,
    provides aggregated statistics, and maintains event history
    for debugging and compliance purposes.
    """

    def __init__(self, max_events: int = 1000, retention_hours: int = 24):
        """
        Initialize the security monitor.

        Args:
            max_events: Maximum number of events to keep in memory
            retention_hours: How long to keep events in memory
        """
        self._events: List[SecurityEvent] = []
        self._lock = threading.Lock()
        self._max_events = max_events
        self._retention_hours = retention_hours
        self._counters: Dict[str, int] = defaultdict(int)
        self._last_cleanup = time.time()

    def record_event(self, event: SecurityEvent) -> None:
        """
        Record a security event with thread-safe operations.

        Args:
            event: SecurityEvent to record
        """
        with self._lock:
            self._events.append(event)
            self._counters[f"{event.event_type}.{event.severity}"] += 1
            self._cleanup_old_events()

        # Log the event for immediate visibility
        log_func = {
            "info": logger.info,
            "warning": logger.warning,
            "error": logger.error,
            "critical": logger.critical,
        }.get(event.severity, logger.info)

        log_func(
            f"Security Event [{event.event_type}]: {event.message}",
            extra={"security_event": event.to_dict()},
        )

    def _cleanup_old_events(self) -> None:
        """Remove events older than retention period."""
        now = time.time()
        # Only cleanup every 5 minutes to avoid frequent iterations
        if now - self._last_cleanup < 300:
            return

        cutoff = datetime.utcnow() - timedelta(hours=self._retention_hours)
        self._events = [e for e in self._events if e.timestamp > cutoff]
        self._last_cleanup = now

        # Trim to max events if needed
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]

    def get_events(
        self,
        event_type: Optional[str] = None,
        severity: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        """
        Retrieve security events with optional filtering.

        Args:
            event_type: Filter by event type (optional)
            severity: Filter by severity level (optional)
            limit: Maximum number of events to return

        Returns:
            List of event dictionaries
        """
        with self._lock:
            events = self._events.copy()

        filtered = events
        if event_type:
            filtered = [e for e in filtered if e.event_type == event_type]
        if severity:
            filtered = [e for e in filtered if e.severity == severity]

        # Return most recent first
        filtered = sorted(filtered, key=lambda e: e.timestamp, reverse=True)
        return [e.to_dict() for e in filtered[:limit]]

    def get_statistics(self) -> dict:
        """
        Get aggregated security statistics.

        Returns:
            Dictionary with event counts and statistics
        """
        with self._lock:
            total_events = len(self._events)
            counters = self._counters.copy()

        # Calculate statistics for recent time windows
        now = datetime.utcnow()
        last_hour = now - timedelta(hours=1)
        last_24h = now - timedelta(hours=24)

        with self._lock:
            events = self._events.copy()

        stats = {
            "total_events": total_events,
            "last_hour_count": len([e for e in events if e.timestamp > last_hour]),
            "last_24h_count": len([e for e in events if e.timestamp > last_24h]),
            "by_type_and_severity": dict(counters),
            "monitoring_active": True,
            "retention_hours": self._retention_hours,
            "max_events": self._max_events,
        }

        return stats

    def get_tls_fallback_summary(self) -> dict:
        """
        Get summary of TLS fallback events for monitoring.

        Returns:
            Dictionary with TLS fallback statistics
        """
        tls_events = self.get_events(event_type="tls_fallback", limit=1000)

        # Group by outcome
        outcomes = defaultdict(int)
        urls = set()
        last_24h = datetime.utcnow() - timedelta(hours=24)

        for event in tls_events:
            outcomes[event.metadata.get("stage", "unknown")] += 1
            urls.add(event.url)
            # Filter for recent events
            event_time = datetime.fromisoformat(event["timestamp"])
            if event_time > last_24h:
                outcomes[f"recent_{event.metadata.get('stage', 'unknown')}"] += 1

        return {
            "total_fallbacks": len(tls_events),
            "unique_urls_affected": len(urls),
            "by_outcome": dict(outcomes),
            "last_24h_count": outcomes.get("recent_stage3_insecure", 0),
        }


# Global security monitor instance
_security_monitor: Optional[SecurityMonitor] = None


def get_security_monitor() -> SecurityMonitor:
    """
    Get the global security monitor instance.

    Returns:
        SecurityMonitor singleton instance
    """
    global _security_monitor
    if _security_monitor is None:
        _security_monitor = SecurityMonitor()
        logger.info("Security monitoring initialized")
    return _security_monitor


def record_tls_fallback(
    url: str, stage: str, success: bool, error_message: Optional[str] = None
) -> None:
    """
    Record a TLS fallback event with structured metadata.

    Args:
        url: The URL that triggered TLS fallback
        stage: Which fallback stage was used (stage1_normal, stage2_aia, stage3_insecure)
        success: Whether the request ultimately succeeded
        error_message: Optional error message for debugging
    """
    monitor = get_security_monitor()

    severity = "warning" if stage == "stage3_insecure" else "info"
    message = f"TLS fallback {stage} for {url} - {'SUCCESS' if success else 'FAILED'}"

    event = SecurityEvent(
        event_type="tls_fallback",
        severity=severity,
        message=message,
        url=url,
        stage=stage,
        success=success,
        error_message=error_message,
    )

    monitor.record_event(event)


def record_proxy_bypass(url: str, is_private: bool, reason: str) -> None:
    """
    Record a proxy bypass decision for monitoring.

    Args:
        url: The URL being evaluated
        is_private: Whether the URL was classified as private
        reason: Explanation for the bypass decision
    """
    monitor = get_security_monitor()

    event = SecurityEvent(
        event_type="proxy_bypass",
        severity="info",
        message=f"Proxy bypass for {url} - private={is_private}, reason={reason}",
        url=url,
        is_private=is_private,
        reason=reason,
    )

    monitor.record_event(event)