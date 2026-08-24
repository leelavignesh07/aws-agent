"""Read-only policy engine — the security core of the agent (STAGE 7).

Every AWS call the agent can possibly make is routed through `evaluate()` first.
The rule is simple and deliberately boring: *if it is not provably a read, it is
denied*. Nothing here can create, modify or delete an AWS resource, and no
amount of prompting can talk the agent past it — the model never touches the
subprocess layer directly, it only proposes `(service, operation, args)` triples
that this module has to approve.

Three verdicts are possible:

    ALLOW      — a plain read (`describe-*`, `list-*`, `get-*`, ...)
    SENSITIVE  — a read, but one that returns credential material, raw object
                 data or a full IAM map. Requires an explicit human approval
                 (see `approvals.py`).
    DENY       — anything else: writes, deletes, session starts, credential
                 vending, or a flag that could redirect traffic off AWS.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import Enum

__all__ = ["Verdict", "Decision", "evaluate", "explain"]


class Verdict(str, Enum):
    ALLOW = "allow"
    SENSITIVE = "sensitive"
    DENY = "deny"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str
    service: str = ""
    operation: str = ""

    @property
    def allowed(self) -> bool:
        """True when the call may run *without* asking a human first."""
        return self.verdict is Verdict.ALLOW

    @property
    def denied(self) -> bool:
        return self.verdict is Verdict.DENY


# --------------------------------------------------------------------------
# 1. Operation verbs that are read-only by AWS convention.
# --------------------------------------------------------------------------
READ_VERBS = frozenset(
    {
        "describe",
        "list",
        "get",
        "lookup",
        "head",
        "search",
        "scan",  # dynamodb scan — a read
        "query",  # dynamodb query — a read
        "batch-get",
        "check",  # e.g. route53domains check-domain-availability
        "estimate",
        "preview",
        "simulate",  # iam simulate-principal-policy — evaluates, changes nothing
        "view",
        "select",  # s3api select-object-content
        "filter",  # logs filter-log-events
        "test",  # e.g. ses test-render-template
        "validate",  # cloudformation validate-template
        "generate",  # ce generate-savings-plans... (read-only projections)
    }
)

# Verb prefixes that look like reads but are not, or that we simply refuse.
FORBIDDEN_VERBS = frozenset(
    {
        "create",
        "delete",
        "put",
        "update",
        "modify",
        "run",
        "start",
        "stop",
        "terminate",
        "reboot",
        "attach",
        "detach",
        "associate",
        "disassociate",
        "authorize",
        "revoke",
        "enable",
        "disable",
        "register",
        "deregister",
        "add",
        "remove",
        "set",
        "reset",
        "tag",
        "untag",
        "copy",
        "move",
        "sync",
        "restore",
        "invoke",
        "publish",
        "send",
        "execute",
        "apply",
        "import",
        "export",
        "upload",
        "download",
        "cancel",
        "accept",
        "reject",
        "purchase",
        "request",
        "release",
        "assume",
        "change",
        "replace",
        "rotate",
        "promote",
        "failover",
        "deploy",
        "configure",
        "login",
        "wait",
        "presign",
    }
)

# --------------------------------------------------------------------------
# 2. Explicit rules that override the verb heuristic.
# --------------------------------------------------------------------------

# Read-only calls whose verb is not in READ_VERBS, or that need whitelisting
# because the service uses a non-standard command name.
ALLOW_EXTRA = frozenset(
    {
        "s3:ls",
        "logs:filter-log-events",
        "cloudtrail:lookup-events",
        "ce:get-cost-and-usage",
        "ce:get-cost-forecast",
        "sts:get-caller-identity",
        "iam:generate-service-last-accessed-details",  # read-only analysis job
    }
)

# Calls that ARE reads but hand back secrets, credentials, raw object bytes or a
# complete authorization map. Allowed only with an explicit human approval.
SENSITIVE_CALLS = frozenset(
    {
        "iam:get-credential-report",
        "iam:get-account-authorization-details",
        "iam:get-user-policy",
        "iam:get-role-policy",
        "iam:get-group-policy",
        "iam:get-policy-version",
        "lambda:get-function",  # response embeds a presigned code-download URL
        "lambda:get-function-configuration",
        "s3api:get-object",
        "s3api:select-object-content",
        "dynamodb:scan",
        "dynamodb:query",
        "dynamodb:batch-get-item",
        "dynamodb:get-item",
        "ssm:describe-parameters",
        "ec2:get-password-data",
        "ec2:describe-instance-attribute",  # can return user-data (often secrets)
        "cloudformation:get-template",
        "apigateway:get-api-key",
        "apigateway:get-api-keys",
    }
)

# Reads that are never worth the blast radius — they vend credentials or bulk
# secret material. Denied outright, with or without approval.
DENY_CALLS = frozenset(
    {
        "sts:get-session-token",
        "sts:get-federation-token",
        "sts:assume-role",
        "sts:assume-role-with-saml",
        "sts:assume-role-with-web-identity",
        "secretsmanager:get-secret-value",
        "secretsmanager:get-random-password",
        "ssm:get-parameter",
        "ssm:get-parameters",
        "ssm:get-parameters-by-path",
        "ecr:get-login-password",
        "ecr:get-authorization-token",
        "ecr-public:get-login-password",
        "ecr-public:get-authorization-token",
        "kms:decrypt",
        "kms:generate-data-key",
        "cognito-idp:get-user",
        "gamelift:get-instance-access",
        "connect:get-federation-token",
        "signer:get-revocation-status",
    }
)

# Whole services/command groups the agent may never drive, because their
# subcommands are file transfers, shells or deploy actions rather than API reads.
DENY_SERVICES = frozenset(
    {
        "configure",
        "deploy",  # `aws deploy` = CodeDeploy pushes
        "ssm-session",
        "sso",
        "sso-oidc",
        "codeartifact",  # login/token vending
        "history",
        "help",
    }
)

# `aws s3` is the high-level file-transfer command set. Only `ls` is a read.
S3_HIGHLEVEL_ALLOWED = frozenset({"ls"})

# --------------------------------------------------------------------------
# 3. CLI flags that could redirect the call or weaken transport security.
# --------------------------------------------------------------------------
DENY_FLAGS = frozenset(
    {
        "--endpoint-url",  # could point the call at an attacker-controlled host
        "--no-verify-ssl",
        "--ca-bundle",
        "--cli-input-json",  # smuggles an entirely different request body
        "--cli-input-yaml",
        "--profile",  # profile selection belongs to config, not to the model
        "--region",  # ditto: the executor sets the region
        "--output",  # the executor pins JSON output
        "--endpoint",
    }
)

_SHELL_METACHARS = re.compile(r"[;&|`$><\n\r\\]")
_SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
_OPERATION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")


def _verb_of(operation: str) -> str:
    """First hyphen-separated token, with `batch-get-*` treated as one verb."""
    if operation.startswith("batch-get"):
        return "batch-get"
    return operation.split("-", 1)[0]


def evaluate(service: str, operation: str, args: list[str] | None = None) -> Decision:
    """Classify one `aws <service> <operation> [args...]` invocation."""
    args = list(args or [])
    service = (service or "").strip().lower()
    operation = (operation or "").strip().lower()
    key = f"{service}:{operation}"

    if not _SERVICE_RE.match(service):
        return Decision(Verdict.DENY, f"malformed service name: {service!r}")
    if not _OPERATION_RE.match(operation):
        return Decision(Verdict.DENY, f"malformed operation name: {operation!r}")

    if service in DENY_SERVICES:
        return Decision(
            Verdict.DENY,
            f"`aws {service}` is not an API read surface (file transfer, shell or deploy commands)",
            service,
            operation,
        )

    if key in DENY_CALLS:
        return Decision(
            Verdict.DENY,
            f"{key} vends credentials or secret material — never permitted",
            service,
            operation,
        )

    # Argument hygiene. The executor never uses a shell, so this is belt and
    # braces, but it also catches an operator trying to chain commands by hand.
    for arg in args:
        if _SHELL_METACHARS.search(arg):
            return Decision(
                Verdict.DENY,
                f"argument contains shell metacharacters: {arg!r}",
                service,
                operation,
            )
        flag = arg.split("=", 1)[0]
        if flag in DENY_FLAGS:
            return Decision(
                Verdict.DENY,
                f"flag {flag} is not allowed (it can redirect or downgrade the call)",
                service,
                operation,
            )

    if service == "s3" and operation not in S3_HIGHLEVEL_ALLOWED:
        return Decision(
            Verdict.DENY,
            "only `aws s3 ls` is allowed from the high-level s3 command set; "
            "use s3api describe/list/get-* calls for metadata",
            service,
            operation,
        )

    if key in SENSITIVE_CALLS:
        return Decision(
            Verdict.SENSITIVE,
            f"{key} returns credential material, raw data or a full IAM map — approval required",
            service,
            operation,
        )

    if key in ALLOW_EXTRA:
        return Decision(Verdict.ALLOW, f"{key} is an explicitly allow-listed read", service, operation)

    verb = _verb_of(operation)
    if verb in FORBIDDEN_VERBS:
        return Decision(
            Verdict.DENY,
            f"operation verb '{verb}' mutates state — this agent is read-only",
            service,
            operation,
        )
    if verb not in READ_VERBS:
        return Decision(
            Verdict.DENY,
            f"operation verb '{verb}' is not a recognised read verb; "
            f"allowed verbs: {', '.join(sorted(READ_VERBS))}",
            service,
            operation,
        )

    return Decision(Verdict.ALLOW, f"{key} is a read-only {verb}-* call", service, operation)


def explain(command: str) -> Decision:
    """Evaluate a full command line, e.g. `aws ec2 describe-instances --max-items 5`."""
    parts = shlex.split(command)
    if parts and parts[0] == "aws":
        parts = parts[1:]
    if len(parts) < 2:
        return Decision(Verdict.DENY, "expected at least `<service> <operation>`")
    return evaluate(parts[0], parts[1], parts[2:])
