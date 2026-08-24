"""Guarded `aws` CLI executor (STAGE 3).

All AWS information is collected by shelling out to the AWS CLI, exactly as an
engineer would at a terminal — which makes every action the agent takes
reproducible by hand and greppable in the audit log.

Nothing reaches `subprocess` until `guard.evaluate()` has approved it, and the
argument list is passed to the OS directly (never through a shell), so there is
no string that could be re-interpreted as a second command.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from . import audit
from .approvals import request_approval
from .config import get_config
from .guard import Verdict, evaluate


class AwsCliNotFound(RuntimeError):
    pass


class CallDenied(PermissionError):
    """Raised when the guard (or a human) refuses a call."""


@dataclass
class CliResult:
    ok: bool
    command: str
    data: Any = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_ms: int = 0
    cached: bool = False
    denied_reason: str = ""

    def summary(self) -> dict[str, Any]:
        """Compact, model-friendly view of the result."""
        out: dict[str, Any] = {"ok": self.ok, "command": self.command}
        if self.cached:
            out["cached"] = True
        if self.ok:
            out["data"] = self.data if self.data is not None else self.stdout.strip()
        else:
            out["error"] = self.denied_reason or self.stderr.strip() or f"exit {self.exit_code}"
        return out


# --------------------------------------------------------------------------
# A small TTL cache. Monitoring questions repeat the same describe-* calls a lot
# and every repeat costs latency and API quota.
# --------------------------------------------------------------------------
@dataclass
class _CacheEntry:
    value: CliResult
    expires_at: float


_cache: dict[str, _CacheEntry] = {}


def clear_cache() -> None:
    _cache.clear()


def aws_binary() -> str:
    path = shutil.which("aws")
    if not path:
        raise AwsCliNotFound("the `aws` CLI is not on PATH — run `make setup` (or install AWS CLI v2) first")
    return path


# Services whose API endpoint only exists in one region. The CLI would build an
# invalid hostname if we passed the caller's region, so we pin these ourselves.
GLOBAL_ENDPOINT_REGIONS = {
    "ce": "us-east-1",
    "cost-optimization-hub": "us-east-1",
    "support": "us-east-1",
    "organizations": "us-east-1",
    "cloudfront": "us-east-1",
    "route53": "us-east-1",
    "iam": "us-east-1",
    "budgets": "us-east-1",
}


def build_command(
    service: str,
    operation: str,
    args: list[str] | None = None,
    region: str | None = None,
) -> list[str]:
    """Assemble the full argv, pinning output format and region ourselves."""
    cfg = get_config()
    argv = [aws_binary()]
    if cfg.profile:
        argv += ["--profile", cfg.profile]
    argv += [service, operation, *(args or [])]
    if service != "s3":  # the high-level `s3 ls` command has no --output json
        argv += ["--output", "json"]
    effective = region or GLOBAL_ENDPOINT_REGIONS.get(service) or cfg.region
    argv += ["--region", effective, "--no-cli-pager"]
    return argv


def run(
    service: str,
    operation: str,
    args: list[str] | None = None,
    *,
    use_cache: bool = True,
    region: str | None = None,
) -> CliResult:
    """Evaluate, (maybe) approve, then execute one read-only AWS CLI call.

    `region` is an internal override for callers inside this package (global
    endpoints, cross-region sweeps). It is deliberately not reachable from the
    model-facing `aws_cli` tool, which always runs in the configured region.
    """
    cfg = get_config()
    args = [str(a) for a in (args or [])]
    pretty = " ".join(["aws", service, operation, *args])

    decision = evaluate(service, operation, args)
    if decision.verdict is Verdict.DENY:
        audit.record(
            "aws_call_denied", command=pretty, verdict=decision.verdict.value, reason=decision.reason
        )
        return CliResult(ok=False, command=pretty, denied_reason=f"DENIED by policy: {decision.reason}")

    if decision.verdict is Verdict.SENSITIVE:
        approval = request_approval(decision, pretty)
        if not approval.granted:
            return CliResult(
                ok=False,
                command=pretty,
                denied_reason=f"DENIED — approval not granted: {approval.reason}",
            )

    try:
        aws_binary()
    except AwsCliNotFound as exc:
        audit.record("aws_call_unavailable", command=pretty, reason=str(exc))
        return CliResult(ok=False, command=pretty, denied_reason=str(exc))

    cache_key = f"{region or cfg.region}|{cfg.profile}|{pretty}"
    if use_cache and cfg.cache_ttl > 0:
        hit = _cache.get(cache_key)
        if hit and hit.expires_at > time.monotonic():
            cached = CliResult(**{**hit.value.__dict__, "cached": True})
            return cached

    argv = build_command(service, operation, args, region=region)
    audit.record("aws_call", command=pretty, verdict=decision.verdict.value, reason=decision.reason)

    started = time.monotonic()
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, never a shell string
            argv,
            capture_output=True,
            text=True,
            timeout=cfg.cli_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        result = CliResult(
            ok=False,
            command=pretty,
            stderr=f"timed out after {cfg.cli_timeout}s",
            exit_code=124,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        audit.record("aws_call_result", command=pretty, ok=False, error="timeout")
        return result

    duration_ms = int((time.monotonic() - started) * 1000)
    data: Any = None
    if proc.returncode == 0 and proc.stdout.strip():
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            data = None

    result = CliResult(
        ok=proc.returncode == 0,
        command=pretty,
        data=data,
        stdout=proc.stdout,
        stderr=proc.stderr,
        exit_code=proc.returncode,
        duration_ms=duration_ms,
    )
    audit.record(
        "aws_call_result",
        command=pretty,
        ok=result.ok,
        exit_code=proc.returncode,
        duration_ms=duration_ms,
        bytes=len(proc.stdout),
        error=None if result.ok else proc.stderr.strip()[:500],
    )

    if result.ok and use_cache and cfg.cache_ttl > 0:
        _cache[cache_key] = _CacheEntry(result, time.monotonic() + cfg.cache_ttl)
    return result


def caller_identity() -> CliResult:
    return run("sts", "get-caller-identity")


def available() -> bool:
    return shutil.which("aws") is not None
