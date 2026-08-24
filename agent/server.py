"""HTTP API (STAGE 4 / STAGE 8).

Same agent, exposed over HTTP so a dashboard, a Slack bot or a cron job can use
it. Every endpoint is read-only; the write boundary is enforced a layer below in
`guard.py`, so nothing here can widen it.

Auth is a bearer token compared in constant time. The server refuses to start
without one unless you explicitly opt out with AGENT_ALLOW_ANON=1 — an
unauthenticated endpoint that can enumerate your AWS account is a bad default.
"""

from __future__ import annotations

import hmac
import os
import time
from typing import Any

from . import audit, doctor, memory, reports
from .config import get_config
from .llm import LLMUnavailable
from .loop import ask as agent_ask
from .tools import dispatch

START_TIME = time.time()
_REQUESTS: dict[str, int] = {}


def _count(endpoint: str) -> None:
    _REQUESTS[endpoint] = _REQUESTS.get(endpoint, 0) + 1


def create_app() -> Any:
    try:
        from fastapi import Depends, FastAPI, Header, HTTPException
        from pydantic import BaseModel
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("the HTTP API needs fastapi and uvicorn — run `make setup-server`") from exc

    cfg = get_config()
    allow_anon = os.environ.get("AGENT_ALLOW_ANON", "").lower() in {"1", "true", "yes"}
    if not cfg.api_token and not allow_anon:
        raise RuntimeError(
            "AGENT_API_TOKEN is not set. Set one, or start with AGENT_ALLOW_ANON=1 "
            "if you are certain the port is private."
        )

    app = FastAPI(
        title="AWS Monitoring Agent",
        version="1.0.0",
        description="Read-only AWS status, health and inventory.",
    )

    def authorise(authorization: str = Header(default="")) -> None:
        if allow_anon and not cfg.api_token:
            return
        supplied = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied, cfg.api_token):
            audit.record("http_auth_failed")
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    class AskRequest(BaseModel):
        question: str
        session: str | None = None

    @app.get("/health")
    def health() -> dict[str, Any]:
        """Liveness only — deliberately makes no AWS calls."""
        _count("health")
        return {"status": "ok", "uptime_seconds": round(time.time() - START_TIME, 1), "region": cfg.region}

    @app.get("/ready", dependencies=[Depends(authorise)])
    def ready() -> dict[str, Any]:
        _count("ready")
        checks = doctor.run_checks()
        return {"ready": doctor.exit_code(checks) == 0, "checks": checks}

    @app.get("/inventory", dependencies=[Depends(authorise)])
    def inventory() -> Any:
        _count("inventory")
        return dispatch("aws_inventory", {"include_empty": False})

    @app.get("/aws-health", dependencies=[Depends(authorise)])
    def aws_health() -> Any:
        _count("aws_health")
        return dispatch("aws_health", {})

    @app.get("/status", dependencies=[Depends(authorise)])
    def status(cost: bool = True, security: bool = False) -> Any:
        _count("status")
        return reports.status_report(include_cost=cost, include_security=security)

    @app.get("/security", dependencies=[Depends(authorise)])
    def security() -> Any:
        _count("security")
        return dispatch("aws_security_posture", {})

    @app.post("/ask", dependencies=[Depends(authorise)])
    def ask(request: AskRequest) -> Any:
        _count("ask")
        try:
            result = agent_ask(request.question, session=request.session, stream_to_stdout=False)
        except LLMUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return result.as_dict()

    @app.get("/audit", dependencies=[Depends(authorise)])
    def audit_tail(limit: int = 50) -> Any:
        _count("audit")
        return {"entries": audit.tail(min(limit, 500))}

    @app.get("/facts", dependencies=[Depends(authorise)])
    def facts(query: str = "") -> Any:
        _count("facts")
        return {"facts": memory.recall(query)}

    @app.get("/metrics", dependencies=[Depends(authorise)])
    def metrics() -> Any:
        """Prometheus text exposition — scrape target for STAGE 8."""
        _count("metrics")
        from fastapi.responses import PlainTextResponse

        lines = [
            "# HELP agent_uptime_seconds Seconds since the process started.",
            "# TYPE agent_uptime_seconds gauge",
            f"agent_uptime_seconds {round(time.time() - START_TIME, 1)}",
            "# HELP agent_requests_total Requests served, by endpoint.",
            "# TYPE agent_requests_total counter",
        ]
        lines += [f'agent_requests_total{{endpoint="{k}"}} {v}' for k, v in sorted(_REQUESTS.items())]
        return PlainTextResponse("\n".join(lines) + "\n")

    return app


def serve(host: str = "0.0.0.0", port: int = 8080) -> None:  # noqa: S104 - container-facing
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("uvicorn is not installed — run `make setup-server`") from exc
    audit.record("server_start", host=host, port=port)
    uvicorn.run(create_app(), host=host, port=port, log_level="info")
