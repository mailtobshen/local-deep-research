"""Regression test for live vision blueprint registration."""

from flask import Flask

from local_deep_research.web.app_factory import register_blueprints


def test_vision_blueprint_registers():
    """Ensure POST /api/vision/test_connection is wired in the Flask app."""
    app = Flask(__name__)
    register_blueprints(app)
    rules = [rule.rule for rule in app.url_map.iter_rules()]
    assert "/api/vision/test_connection" in rules
