"""DevOps capabilities: cost, logs, security posture, and the raw CLI escape
hatch (STAGE 5).

These are the questions a platform engineer asks on a Monday morning: what did
we spend, what is erroring, what is exposed to the internet.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .. import awscli
from ..config import get_config
from .base import obj, tool

SAFE_PORTS = {80, 443}


def _day(offset: int) -> str:
    return (dt.date.today() - dt.timedelta(days=offset)).isoformat()


@tool(
    name="aws_cost",
    description=(
        "Break down AWS spend for the last N days using Cost Explorer, grouped by "
        "service (or another dimension). Use for 'what am I spending', 'why did the "
        "bill go up', cost reviews and spike triage. Read-only. Note: Cost Explorer "
        "charges a small per-request fee."
    ),
    input_schema=obj(
        {
            "days": {"type": "integer", "description": "Look-back window in days (default 7, max 90)."},
            "granularity": {"type": "string", "enum": ["DAILY", "MONTHLY"]},
            "group_by": {
                "type": "string",
                "enum": ["SERVICE", "USAGE_TYPE", "REGION", "LINKED_ACCOUNT", "INSTANCE_TYPE"],
            },
        }
    ),
    stage=5,
    tags=["cost"],
)
def aws_cost(days: int = 7, granularity: str = "DAILY", group_by: str = "SERVICE") -> dict[str, Any]:
    days = max(1, min(int(days), 90))
    args = [
        "--time-period",
        f"Start={_day(days)},End={_day(0)}",
        "--granularity",
        granularity,
        "--metrics",
        "UnblendedCost",
        "--group-by",
        f"Type=DIMENSION,Key={group_by}",
    ]
    result = awscli.run("ce", "get-cost-and-usage", args, region="us-east-1")
    if not result.ok:
        return result.summary()

    periods = (result.data or {}).get("ResultsByTime", [])
    totals: dict[str, float] = {}
    daily: list[dict[str, Any]] = []
    for period in periods:
        day_total = 0.0
        for group in period.get("Groups", []):
            key = (group.get("Keys") or ["unknown"])[0]
            amount = float(((group.get("Metrics") or {}).get("UnblendedCost") or {}).get("Amount", 0))
            totals[key] = totals.get(key, 0.0) + amount
            day_total += amount
        daily.append({"start": (period.get("TimePeriod") or {}).get("Start"), "total": round(day_total, 4)})

    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    trend = None
    if len(daily) >= 2 and daily[-2]["total"]:
        change = (daily[-1]["total"] - daily[-2]["total"]) / daily[-2]["total"] * 100
        trend = {"day_over_day_pct": round(change, 1)}
    return {
        "ok": True,
        "window_days": days,
        "grouped_by": group_by,
        "total": round(sum(totals.values()), 2),
        "currency": "USD",
        "top": [{"key": k, "cost": round(v, 2)} for k, v in ranked[:15]],
        "daily": daily,
        "trend": trend,
    }


@tool(
    name="aws_logs",
    description=(
        "Search CloudWatch Logs. With no log group, lists the available groups; with "
        "one, returns matching events from the last N minutes. Use for 'are there "
        "errors in <service>', post-deployment checks and incident triage. Read-only."
    ),
    input_schema=obj(
        {
            "log_group": {"type": "string", "description": "Exact log group name, e.g. /aws/lambda/my-fn."},
            "pattern": {
                "type": "string",
                "description": "CloudWatch filter pattern, e.g. ERROR or '?ERROR ?Exception'. Default ERROR.",
            },
            "minutes": {"type": "integer", "description": "Look-back window in minutes (default 60)."},
            "limit": {"type": "integer", "description": "Max events to return (default 25, max 100)."},
            "name_contains": {"type": "string", "description": "Filter the log-group listing by substring."},
        }
    ),
    stage=5,
    tags=["logs"],
)
def aws_logs(
    log_group: str | None = None,
    pattern: str = "ERROR",
    minutes: int = 60,
    limit: int = 25,
    name_contains: str | None = None,
) -> dict[str, Any]:
    if not log_group:
        args = ["--max-items", "60"]
        if name_contains:
            args += ["--log-group-name-prefix", name_contains]
        result = awscli.run("logs", "describe-log-groups", args)
        if not result.ok:
            return result.summary()
        groups = (result.data or {}).get("logGroups", [])
        return {
            "ok": True,
            "hint": "call again with log_group=<name> to search events",
            "log_groups": [
                {
                    "name": g.get("logGroupName"),
                    "retention_days": g.get("retentionInDays"),
                    "stored_bytes": g.get("storedBytes"),
                }
                for g in groups
            ],
        }

    start_ms = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes)).timestamp() * 1000)
    args = [
        "--log-group-name",
        log_group,
        "--start-time",
        str(start_ms),
        "--limit",
        str(max(1, min(int(limit), 100))),
    ]
    if pattern:
        args += ["--filter-pattern", pattern]
    result = awscli.run("logs", "filter-log-events", args, use_cache=False)
    if not result.ok:
        return result.summary()
    events = (result.data or {}).get("events", [])
    return {
        "ok": True,
        "log_group": log_group,
        "pattern": pattern,
        "window_minutes": minutes,
        "matches": len(events),
        "events": [
            {
                "timestamp": dt.datetime.fromtimestamp(
                    e.get("timestamp", 0) / 1000, dt.timezone.utc
                ).isoformat(),
                "stream": e.get("logStreamName"),
                "message": (e.get("message") or "").strip()[:800],
            }
            for e in events
        ],
    }


@tool(
    name="aws_security_posture",
    description=(
        "Read-only security review: security groups open to 0.0.0.0/0 on ports other "
        "than 80/443, S3 buckets without a public access block, publicly accessible or "
        "unencrypted RDS instances, unencrypted EBS volumes, and IAM users whose access "
        "keys are older than 90 days. Use for audits and 'is anything exposed'. "
        "Reports findings only — it cannot change anything."
    ),
    input_schema=obj(
        {"max_buckets": {"type": "integer", "description": "Bucket checks to sample (default 25)."}}
    ),
    stage=5,
    tags=["security"],
)
def aws_security_posture(max_buckets: int = 25) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    checked: list[str] = []
    skipped: list[dict[str, str]] = []

    # 1. Security groups open to the world.
    sgs = awscli.run("ec2", "describe-security-groups")
    if sgs.ok:
        checked.append("security_groups")
        for group in (sgs.data or {}).get("SecurityGroups", []):
            for rule in group.get("IpPermissions", []):
                open_v4 = any(r.get("CidrIp") == "0.0.0.0/0" for r in rule.get("IpRanges", []))
                open_v6 = any(r.get("CidrIpv6") == "::/0" for r in rule.get("Ipv6Ranges", []))
                if not (open_v4 or open_v6):
                    continue
                from_port, to_port = rule.get("FromPort"), rule.get("ToPort")
                if from_port in SAFE_PORTS and to_port in SAFE_PORTS:
                    continue
                findings.append(
                    {
                        "severity": "high" if from_port in {22, 3389, 3306, 5432, None} else "medium",
                        "type": "security_group_open_to_internet",
                        "resource": group.get("GroupId"),
                        "detail": f"{group.get('GroupName')} allows "
                        f"{rule.get('IpProtocol')} {from_port}-{to_port} from anywhere",
                    }
                )
    else:
        skipped.append({"check": "security_groups", "reason": sgs.summary().get("error", "")[:160]})

    # 2. S3 public access blocks.
    buckets = awscli.run("s3api", "list-buckets")
    if buckets.ok:
        checked.append("s3_public_access")
        for bucket in ((buckets.data or {}).get("Buckets") or [])[: max(1, int(max_buckets))]:
            name = bucket.get("Name")
            pab = awscli.run("s3api", "get-public-access-block", ["--bucket", name])
            if not pab.ok:
                findings.append(
                    {
                        "severity": "medium",
                        "type": "s3_no_public_access_block",
                        "resource": name,
                        "detail": "bucket has no public access block configured",
                    }
                )
                continue
            cfg = (pab.data or {}).get("PublicAccessBlockConfiguration", {})
            if not all(
                cfg.get(k)
                for k in ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets")
            ):
                findings.append(
                    {
                        "severity": "medium",
                        "type": "s3_partial_public_access_block",
                        "resource": name,
                        "detail": f"public access block incomplete: {cfg}",
                    }
                )
    else:
        skipped.append({"check": "s3_public_access", "reason": buckets.summary().get("error", "")[:160]})

    # 3. RDS exposure and encryption.
    rds = awscli.run("rds", "describe-db-instances")
    if rds.ok:
        checked.append("rds_exposure")
        for db in (rds.data or {}).get("DBInstances", []):
            if db.get("PubliclyAccessible"):
                findings.append(
                    {
                        "severity": "high",
                        "type": "rds_publicly_accessible",
                        "resource": db.get("DBInstanceIdentifier"),
                        "detail": "database is reachable from the internet",
                    }
                )
            if not db.get("StorageEncrypted"):
                findings.append(
                    {
                        "severity": "medium",
                        "type": "rds_unencrypted_storage",
                        "resource": db.get("DBInstanceIdentifier"),
                        "detail": "storage encryption is disabled",
                    }
                )
    else:
        skipped.append({"check": "rds_exposure", "reason": rds.summary().get("error", "")[:160]})

    # 4. Unencrypted EBS volumes.
    volumes = awscli.run("ec2", "describe-volumes")
    if volumes.ok:
        checked.append("ebs_encryption")
        unencrypted = [
            v.get("VolumeId") for v in (volumes.data or {}).get("Volumes", []) if not v.get("Encrypted")
        ]
        if unencrypted:
            findings.append(
                {
                    "severity": "medium",
                    "type": "ebs_unencrypted_volumes",
                    "resource": f"{len(unencrypted)} volumes",
                    "detail": ", ".join(unencrypted[:10]),
                }
            )
    else:
        skipped.append({"check": "ebs_encryption", "reason": volumes.summary().get("error", "")[:160]})

    # 5. Stale IAM access keys (metadata only — key material is never returned).
    users = awscli.run("iam", "list-users")
    if users.ok:
        checked.append("iam_access_key_age")
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=90)
        for user in ((users.data or {}).get("Users") or [])[:50]:
            name = user.get("UserName")
            keys = awscli.run("iam", "list-access-keys", ["--user-name", name])
            if not keys.ok:
                continue
            for key in (keys.data or {}).get("AccessKeyMetadata", []):
                if key.get("Status") != "Active":
                    continue
                created = str(key.get("CreateDate", ""))
                try:
                    created_at = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if created_at < cutoff:
                    findings.append(
                        {
                            "severity": "medium",
                            "type": "iam_stale_access_key",
                            "resource": f"{name}:{key.get('AccessKeyId')}",
                            "detail": f"active access key created {created_at.date()} "
                            f"({(dt.datetime.now(dt.timezone.utc) - created_at).days} days old)",
                        }
                    )
    else:
        skipped.append({"check": "iam_access_key_age", "reason": users.summary().get("error", "")[:160]})

    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: order.get(f["severity"], 3))
    return {
        "ok": True,
        "region": get_config().region,
        "checks_run": checked,
        "checks_skipped": skipped,
        "finding_count": len(findings),
        "by_severity": {
            sev: sum(1 for f in findings if f["severity"] == sev) for sev in ("high", "medium", "low")
        },
        "findings": findings[:60],
    }


@tool(
    name="aws_cli",
    description=(
        "Run any read-only AWS CLI call the other tools do not cover, e.g. "
        "service='ec2', operation='describe-vpc-endpoints'. Only describe-*, list-*, "
        "get-*, lookup-* style operations are permitted; anything that could create, "
        "modify or delete a resource is refused by policy before it runs, and calls "
        "that vend credentials are refused outright. Prefer the purpose-built tools "
        "when one fits; use this for the long tail."
    ),
    input_schema=obj(
        {
            "service": {"type": "string", "description": "AWS CLI service name, e.g. ec2, s3api, rds."},
            "operation": {"type": "string", "description": "Read-only operation, e.g. describe-vpcs."},
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Extra CLI arguments, e.g. ['--max-items','10','--filters','Name=vpc-id,Values=vpc-123'].",
            },
        },
        required=["service", "operation"],
    ),
    stage=3,
    tags=["escape-hatch"],
)
def aws_cli(service: str, operation: str, args: list[str] | None = None) -> dict[str, Any]:
    result = awscli.run(service, operation, args or [])
    payload = result.summary()
    # Very large payloads waste context and money — truncate with a clear marker.
    data = payload.get("data")
    if isinstance(data, str) and len(data) > 20000:
        payload["data"] = data[:20000] + "\n... [truncated]"
    return payload
