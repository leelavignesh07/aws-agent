"""Human-in-the-loop approval gate for SENSITIVE reads (STAGE 7).

The guard classifies a call; this module decides whether a human has to say yes
before it runs. Non-interactive contexts (CI, Lambda, a cron job) never get a
silent yes — if nobody can answer, the call is refused.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from . import audit
from .config import get_config
from .guard import Decision, Verdict


@dataclass(frozen=True)
class ApprovalResult:
    granted: bool
    reason: str


def request_approval(decision: Decision, command: str) -> ApprovalResult:
    """Ask (or decide) whether a SENSITIVE call may proceed."""
    mode = get_config().approval_mode.strip().lower()

    if mode == "allow":
        result = ApprovalResult(True, "auto-approved by AGENT_APPROVAL_MODE=allow (break-glass)")
    elif mode == "deny":
        result = ApprovalResult(False, "AGENT_APPROVAL_MODE=deny refuses all sensitive reads")
    elif not (sys.stdin.isatty() and sys.stdout.isatty()):
        result = ApprovalResult(
            False,
            "sensitive call needs a human, but this session is non-interactive; "
            "re-run on a terminal or set AGENT_APPROVAL_MODE=allow deliberately",
        )
    else:
        print("\n\033[33m╭─ APPROVAL REQUIRED ─────────────────────────────────────\033[0m")
        print(f"\033[33m│\033[0m command : {command}")
        print(f"\033[33m│\033[0m reason  : {decision.reason}")
        print("\033[33m╰─────────────────────────────────────────────────────────\033[0m")
        try:
            answer = input("Allow this read-only call? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        granted = answer in {"y", "yes"}
        result = ApprovalResult(granted, "operator approved" if granted else "operator declined")

    audit.record(
        "approval",
        command=command,
        verdict=Verdict.SENSITIVE.value,
        granted=result.granted,
        mode=mode,
        reason=result.reason,
    )
    return result
