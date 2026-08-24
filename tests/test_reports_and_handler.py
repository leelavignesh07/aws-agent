from __future__ import annotations

import json

from agent import reports
from agent.lambda_handler import handler


def test_status_report_has_a_summary(fake_aws):
    report = reports.status_report(include_cost=False)
    assert report["summary"]["overall_health"] in {"healthy", "unhealthy", "partial"}
    assert "inventory" in report and "health" in report


def test_render_text_is_printable(fake_aws):
    text = reports.render_text(reports.status_report(include_cost=False))
    assert "AWS STATUS REPORT" in text
    assert "This agent cannot change any resource." in text


def test_handler_health_route():
    response = handler({"rawPath": "/health", "requestContext": {"http": {"method": "GET"}}})
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["status"] == "ok"


def test_handler_unknown_route_is_404():
    response = handler({"rawPath": "/admin", "requestContext": {"http": {"method": "GET"}}})
    assert response["statusCode"] == 404


def test_handler_ask_without_question_is_400():
    response = handler(
        {
            "rawPath": "/ask",
            "requestContext": {"http": {"method": "POST"}},
            "body": json.dumps({}),
        }
    )
    assert response["statusCode"] == 400


def test_handler_inventory_route(fake_aws):
    response = handler({"rawPath": "/inventory", "requestContext": {"http": {"method": "GET"}}})
    assert response["statusCode"] == 200
    assert "counts" in json.loads(response["body"])


def test_direct_invoke_report_does_not_publish_without_a_topic(fake_aws):
    result = handler({"action": "report", "include_cost": False, "include_security": False})
    assert result["ok"] is True
    assert result["delivery"]["published"] is False
