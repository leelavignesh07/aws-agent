"""AWS Lambda entry point (STAGE 4).

Handles three shapes of event so one function covers every deployment path:

  * a Function URL / API Gateway request   -> routed by path
  * a direct invoke, e.g. from EventBridge -> {"action": "report"}
  * a scheduled report                     -> publishes the rendered text to SNS

Nothing here can write to AWS other than `sns:Publish` to the report topic, and
even that is optional and off unless REPORT_TOPIC_ARN is configured.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from . import audit, reports
from .config import get_config
from .llm import LLMUnavailable
from .loop import ask as agent_ask
from .tools import dispatch


def _response(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def _publish_report(text: str, summary: dict[str, Any]) -> dict[str, Any]:
    """Optional SNS delivery for the scheduled report.

    This is the one non-read call the deployed agent can make; IAM scopes it to a
    single topic ARN, and it only ever sends the report body.
    """
    topic = os.environ.get("REPORT_TOPIC_ARN", "").strip()
    if not topic:
        return {"published": False, "reason": "REPORT_TOPIC_ARN not set"}
    cfg = get_config()
    subject = f"AWS status {cfg.region}: {summary.get('overall_health', 'unknown')}"[:99]
    proc = subprocess.run(  # noqa: S603 - argv list, no shell
        [
            "aws",
            "sns",
            "publish",
            "--topic-arn",
            topic,
            "--subject",
            subject,
            "--message",
            text[:250_000],
            "--region",
            cfg.region,
            "--no-cli-pager",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    audit.record("report_published", topic=topic, ok=proc.returncode == 0)
    return {"published": proc.returncode == 0, "error": proc.stderr.strip()[:300] or None}


def handler(event: dict[str, Any] | None, context: Any = None) -> dict[str, Any]:
    event = event or {}
    audit.record("lambda_invoke", keys=sorted(event)[:12])

    # --- HTTP shape (Function URL / API Gateway v2) ------------------------
    request_context = event.get("requestContext") or {}
    if "http" in request_context or "rawPath" in event:
        path = (event.get("rawPath") or "/").rstrip("/") or "/"
        method = (request_context.get("http") or {}).get("method", "GET").upper()
        try:
            if path in {"/", "/health"}:
                return _response(200, {"status": "ok", "region": get_config().region})
            if path == "/inventory":
                return _response(200, dispatch("aws_inventory", {"include_empty": False}))
            if path == "/aws-health":
                return _response(200, dispatch("aws_health", {}))
            if path == "/security":
                return _response(200, dispatch("aws_security_posture", {}))
            if path == "/status":
                return _response(200, reports.status_report())
            if path == "/ask" and method == "POST":
                body = json.loads(event.get("body") or "{}")
                question = (body.get("question") or "").strip()
                if not question:
                    return _response(400, {"error": "body must contain a 'question'"})
                result = agent_ask(question, session=body.get("session"), stream_to_stdout=False)
                return _response(200, result.as_dict())
            return _response(404, {"error": f"no route for {method} {path}"})
        except LLMUnavailable as exc:
            return _response(503, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - never leak a stack trace to the caller
            audit.record("lambda_error", error=f"{type(exc).__name__}: {exc}")
            return _response(500, {"error": "internal error — see CloudWatch logs"})

    # --- Direct / scheduled invoke ----------------------------------------
    action = (event.get("action") or "report").lower()
    if action == "ask":
        result = agent_ask(event.get("question", "Summarise account health."), stream_to_stdout=False)
        return {"ok": True, **result.as_dict()}
    if action == "inventory":
        return {"ok": True, "inventory": dispatch("aws_inventory", {"include_empty": False})}
    if action == "health":
        return {"ok": True, "health": dispatch("aws_health", {})}

    report = reports.status_report(
        include_cost=event.get("include_cost", True),
        include_security=event.get("include_security", True),
    )
    text = reports.render_text(report)
    print(text, flush=True)  # lands in CloudWatch Logs
    return {"ok": True, "summary": report["summary"], "delivery": _publish_report(text, report["summary"])}
