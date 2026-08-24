"""HTTP API tests. Skipped when the server extras are not installed."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from agent.server import create_app  # noqa: E402

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AGENT_API_TOKEN", TOKEN)
    from agent.config import get_config

    get_config(reload=True)
    return TestClient(create_app())


def test_health_needs_no_token(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize("path", ["/inventory", "/aws-health", "/status", "/audit", "/facts", "/metrics"])
def test_every_data_endpoint_requires_a_token(client, path):
    assert client.get(path).status_code == 401


def test_a_wrong_token_is_rejected(client):
    assert client.get("/inventory", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_inventory_endpoint(client, fake_aws):
    response = client.get("/inventory", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["counts"]["ec2_instances"] == 2


def test_metrics_are_prometheus_text(client):
    response = client.get("/metrics", headers=AUTH)
    assert response.status_code == 200
    assert "agent_uptime_seconds" in response.text


def test_server_refuses_to_start_without_a_token(monkeypatch):
    monkeypatch.delenv("AGENT_API_TOKEN", raising=False)
    monkeypatch.delenv("AGENT_ALLOW_ANON", raising=False)
    from agent.config import get_config

    get_config(reload=True)
    with pytest.raises(RuntimeError, match="AGENT_API_TOKEN"):
        create_app()


def test_anonymous_mode_is_opt_in(monkeypatch):
    monkeypatch.delenv("AGENT_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENT_ALLOW_ANON", "1")
    from agent.config import get_config

    get_config(reload=True)
    assert TestClient(create_app()).get("/health").status_code == 200
