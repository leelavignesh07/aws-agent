"""Bootstrap the agent's own API key from AWS Secrets Manager (STAGE 4/7).

This is the one place that reads a secret, and it is deliberately *not* routed
through `guard.py`: it fetches the agent's own Anthropic key from one ARN fixed
by the deployment, before any model or tool is running. The model cannot reach
this code path, cannot choose the ARN, and never sees the value — it lands in
the process environment only.

The guard still refuses `secretsmanager:get-secret-value` for every call the
model makes, which is the property that matters.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess


def _aws_binary() -> str:
    """Resolve the CLI the same way `awscli.aws_binary` does.

    Kept local rather than imported: this module runs before anything else is
    wired up, and deliberately depends on nothing inside the package.
    """
    return os.environ.get("AGENT_AWS_BIN", "").strip() or shutil.which("aws") or "aws"


def hydrate_api_key() -> bool:
    """Populate ANTHROPIC_API_KEY from ANTHROPIC_SECRET_ARN. Returns True if set."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    arn = os.environ.get("ANTHROPIC_SECRET_ARN", "").strip()
    if not arn:
        return False

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell, ARN from deployment config
            [
                _aws_binary(),
                "secretsmanager",
                "get-secret-value",
                "--secret-id",
                arn,
                "--query",
                "SecretString",
                "--output",
                "text",
                "--region",
                region,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env={**os.environ, "AWS_PAGER": ""},
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        print(f"[secrets] could not read {arn}: {proc.stderr.strip()[:200]}", flush=True)
        return False

    raw = proc.stdout.strip()
    value = raw
    if raw.startswith("{"):  # a JSON secret — accept the common key names
        try:
            blob = json.loads(raw)
            value = blob.get("ANTHROPIC_API_KEY") or blob.get("api_key") or blob.get("key") or ""
        except json.JSONDecodeError:
            value = ""
    if not value:
        return False
    os.environ["ANTHROPIC_API_KEY"] = value
    return True
