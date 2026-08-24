"""Environment pre-flight checks (STAGE 1).

`make doctor` answers one question: is this machine ready to run the agent?
Every check reports pass / warn / fail plus the exact command that fixes it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

from . import awscli
from .config import get_config
from .knowledge import get_kb

PASS, WARN, FAIL = "pass", "warn", "fail"


def _check(name: str, status: str, detail: str, fix: str = "") -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def run_checks() -> list[dict[str, Any]]:
    cfg = get_config()
    checks: list[dict[str, Any]] = []

    version = sys.version_info
    checks.append(
        _check(
            "python",
            PASS if version >= (3, 10) else FAIL,
            f"Python {version.major}.{version.minor}.{version.micro}",
            "install Python 3.10 or newer",
        )
    )

    try:
        import anthropic  # noqa: F401

        checks.append(_check("anthropic sdk", PASS, f"anthropic {anthropic.__version__}"))
    except ImportError:
        checks.append(_check("anthropic sdk", FAIL, "not installed", "make setup"))

    key_set = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    checks.append(
        _check(
            "anthropic credentials",
            PASS if key_set else FAIL,
            "ANTHROPIC_API_KEY is set" if key_set else "no ANTHROPIC_API_KEY in the environment",
            "add ANTHROPIC_API_KEY=sk-ant-... to .env",
        )
    )

    try:
        aws_path = awscli.aws_binary()
    except awscli.AwsCliNotFound:
        aws_path = ""
    if aws_path:
        try:
            out = subprocess.run(
                [aws_path, "--version"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
                env=awscli.cli_env(),
            )
            detail = (out.stdout or out.stderr).strip().splitlines()[0]
        except (subprocess.SubprocessError, OSError):
            detail = aws_path
        status = PASS if "aws-cli/2" in detail else WARN
        checks.append(
            _check("aws cli", status, detail, "" if status == PASS else "AWS CLI v2 is recommended")
        )
    else:
        checks.append(_check("aws cli", FAIL, "not on PATH", "make install-awscli"))

    if aws_path:
        identity = awscli.caller_identity()
        if identity.ok and identity.data:
            account = identity.data.get("Account", "?")
            arn = identity.data.get("Arn", "?")
            checks.append(_check("aws credentials", PASS, f"account {account} as {arn}"))
        else:
            checks.append(
                _check(
                    "aws credentials",
                    FAIL,
                    (identity.stderr or identity.denied_reason or "sts get-caller-identity failed")
                    .strip()
                    .splitlines()[-1][:160],
                    "aws configure  (or attach an instance role)",
                )
            )
    else:
        checks.append(_check("aws credentials", FAIL, "skipped — no aws CLI", "make install-awscli"))

    checks.append(_check("region", PASS, f"{cfg.region} (AWS_REGION)"))
    checks.append(
        _check(
            "approval mode",
            PASS if cfg.approval_mode in {"ask", "deny"} else WARN,
            f"AGENT_APPROVAL_MODE={cfg.approval_mode}",
            "'allow' auto-approves sensitive reads — use deliberately",
        )
    )

    try:
        cfg.ensure_dirs()
        probe = cfg.state_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(_check("state directory", PASS, f"{cfg.state_dir} is writable"))
    except OSError as exc:
        checks.append(_check("state directory", WARN, f"{cfg.state_dir}: {exc}", "set AGENT_STATE_DIR"))

    kb = get_kb(reload=True)
    checks.append(
        _check(
            "knowledge base",
            PASS if kb.size else WARN,
            f"{kb.size} passages from {len({c.doc for c in kb.chunks})} documents",
            "add markdown runbooks under knowledge/",
        )
    )
    return checks


def render(checks: list[dict[str, Any]]) -> str:
    colours = {PASS: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m"}
    marks = {PASS: "PASS", WARN: "WARN", FAIL: "FAIL"}
    lines = ["", "AWS Monitoring Agent — environment check", "-" * 60]
    for check in checks:
        colour = colours.get(check["status"], "")
        lines.append(f" {colour}[{marks[check['status']]}]\033[0m {check['name']:<22} {check['detail']}")
        if check["status"] != PASS and check["fix"]:
            lines.append(f"        fix: {check['fix']}")
    failed = sum(1 for c in checks if c["status"] == FAIL)
    lines += [
        "-" * 60,
        f" {len(checks) - failed}/{len(checks)} checks passed"
        + ("" if not failed else f" — {failed} must be fixed before the agent can run"),
        "",
    ]
    return "\n".join(lines)


def exit_code(checks: list[dict[str, Any]]) -> int:
    return 1 if any(c["status"] == FAIL for c in checks) else 0
