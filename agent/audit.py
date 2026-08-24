"""Append-only audit trail (STAGE 7/8).

Every guard decision, AWS call and model turn lands here as one JSON object per
line. This is the artefact you hand an auditor when they ask what the agent did
last Tuesday — it is written before the call runs, so even a crash leaves a
record of the intent.
"""

from __future__ import annotations

import json
import os
import re
import socket
import time
import uuid
from pathlib import Path
from typing import Any

from .config import get_config

_ACCOUNT_ID_RE = re.compile(r"\b\d{12}\b")
_SESSION_ID = uuid.uuid4().hex[:12]


def session_id() -> str:
    return _SESSION_ID


def _redact(obj: Any) -> Any:
    """Optionally mask 12-digit account ids before anything is persisted."""
    if not get_config().redact_account_id:
        return obj
    if isinstance(obj, str):
        return _ACCOUNT_ID_RE.sub("<account-id>", obj)
    if isinstance(obj, dict):
        return {k: _redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def record(event: str, **fields: Any) -> dict[str, Any]:
    """Write one audit entry and return it."""
    cfg = get_config()
    entry: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        "session": _SESSION_ID,
        "actor": os.environ.get("AGENT_ACTOR") or os.environ.get("USER") or "unknown",
        "host": socket.gethostname(),
        "region": cfg.region,
        **_redact(fields),
    }
    line = json.dumps(entry, default=str, separators=(",", ":"))
    try:
        cfg.ensure_dirs()
        with cfg.audit_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        # A read-only or full filesystem (e.g. a Lambda root fs) must never take
        # the agent down — fall back to stdout so the record still reaches logs.
        print(line, flush=True)
    if cfg.json_logs:
        print(line, flush=True)
    return entry


def tail(limit: int = 20, path: Path | None = None) -> list[dict[str, Any]]:
    path = path or get_config().audit_path
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
