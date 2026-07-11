"""Fixtures for error_handling module tests."""

import pytest
from unittest.mock import MagicMock


@pytest.fixture(autouse=True)
def force_english_locale(monkeypatch):
    """Force the translator to English for error_handling tests.

    The ErrorReportGenerator wraps its user-visible strings in _(), which
    resolves to Simplified Chinese by default (the app's default
    language). These tests assert on the English wording of those
    strings, so pin the locale to ``en`` for the duration of each test
    instead of rewriting every assertion.
    """
    from local_deep_research.web import translations

    # ``current_language`` is a read-only property; override the getter
    # on the class so every resolution returns "en" for the test.
    monkeypatch.setattr(
        type(translations.translator),
        "current_language",
        property(lambda self: "en"),
    )


@pytest.fixture
def error_reporter():
    """Create ErrorReporter instance."""
    from local_deep_research.error_handling.error_reporter import ErrorReporter

    return ErrorReporter()


@pytest.fixture
def error_report_generator():
    """Create ErrorReportGenerator instance."""
    from local_deep_research.error_handling.report_generator import (
        ErrorReportGenerator,
    )

    return ErrorReportGenerator()


@pytest.fixture
def sample_partial_results():
    """Sample partial results for testing."""
    return {
        "current_knowledge": "This is the current knowledge about the topic. "
        * 20,
        "search_results": [
            {"title": "Result 1", "url": "https://example.com/1"},
            {"title": "Result 2", "url": "https://example.com/2"},
            {"title": "Result 3", "url": "https://example.com/3"},
        ],
        "findings": [
            {"phase": "Phase 1", "content": "Finding 1 content"},
            {"phase": "Phase 2", "content": "Finding 2 content"},
        ],
    }


@pytest.fixture
def mock_notification_manager():
    """Mock NotificationManager."""
    return MagicMock()


@pytest.fixture
def mock_context_with_username():
    """Context dict with username."""
    return {
        "username": "testuser",
        "query": "test query",
        "research_id": "test-uuid",
    }
