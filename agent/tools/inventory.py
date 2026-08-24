"""Resource inventory — 'what exists in this account?' (STAGE 3).

One sweep across the services a platform team actually cares about, executed as
parallel read-only AWS CLI calls. Services that return AccessDenied are reported
as such rather than silently dropped: knowing the agent *cannot* see EKS is as
useful as the EKS count itself.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .. import awscli, memory
from ..config import get_config
from ..guard import Verdict, evaluate
from .base import obj, tool


class Spec:
    """One inventory probe: a CLI call plus where the countable list lives."""

    def __init__(
        self,
        label: str,
        service: str,
        operation: str,
        path: list[str],
        args: list[str] | None = None,
        nested: str | None = None,
        scope: str = "regional",
    ) -> None:
        self.label = label
        self.service = service
        self.operation = operation
        self.path = path
        self.args = args or []
        self.nested = nested  # e.g. Reservations[].Instances[]
        self.scope = scope


SPECS: list[Spec] = [
    Spec("ec2_instances", "ec2", "describe-instances", ["Reservations"], nested="Instances"),
    Spec("ebs_volumes", "ec2", "describe-volumes", ["Volumes"]),
    Spec("ebs_snapshots", "ec2", "describe-snapshots", ["Snapshots"], ["--owner-ids", "self"]),
    Spec("elastic_ips", "ec2", "describe-addresses", ["Addresses"]),
    Spec("security_groups", "ec2", "describe-security-groups", ["SecurityGroups"]),
    Spec("vpcs", "ec2", "describe-vpcs", ["Vpcs"]),
    Spec("subnets", "ec2", "describe-subnets", ["Subnets"]),
    Spec("nat_gateways", "ec2", "describe-nat-gateways", ["NatGateways"]),
    Spec("internet_gateways", "ec2", "describe-internet-gateways", ["InternetGateways"]),
    Spec("key_pairs", "ec2", "describe-key-pairs", ["KeyPairs"]),
    Spec("auto_scaling_groups", "autoscaling", "describe-auto-scaling-groups", ["AutoScalingGroups"]),
    Spec("load_balancers", "elbv2", "describe-load-balancers", ["LoadBalancers"]),
    Spec("target_groups", "elbv2", "describe-target-groups", ["TargetGroups"]),
    Spec("rds_instances", "rds", "describe-db-instances", ["DBInstances"]),
    Spec("rds_clusters", "rds", "describe-db-clusters", ["DBClusters"]),
    Spec("rds_snapshots", "rds", "describe-db-snapshots", ["DBSnapshots"]),
    Spec("lambda_functions", "lambda", "list-functions", ["Functions"]),
    Spec("ecs_clusters", "ecs", "list-clusters", ["clusterArns"]),
    Spec("eks_clusters", "eks", "list-clusters", ["clusters"]),
    Spec("ecr_repositories", "ecr", "describe-repositories", ["repositories"]),
    Spec("dynamodb_tables", "dynamodb", "list-tables", ["TableNames"]),
    Spec("sns_topics", "sns", "list-topics", ["Topics"]),
    Spec("sqs_queues", "sqs", "list-queues", ["QueueUrls"]),
    Spec("efs_file_systems", "efs", "describe-file-systems", ["FileSystems"]),
    Spec("cloudwatch_alarms", "cloudwatch", "describe-alarms", ["MetricAlarms"]),
    Spec("log_groups", "logs", "describe-log-groups", ["logGroups"]),
    Spec("cloudformation_stacks", "cloudformation", "describe-stacks", ["Stacks"]),
    Spec("acm_certificates", "acm", "list-certificates", ["CertificateSummaryList"]),
    Spec("secrets", "secretsmanager", "list-secrets", ["SecretList"]),
    Spec("s3_buckets", "s3api", "list-buckets", ["Buckets"], scope="global"),
    Spec("iam_users", "iam", "list-users", ["Users"], scope="global"),
    Spec("iam_roles", "iam", "list-roles", ["Roles"], scope="global"),
    Spec("iam_policies", "iam", "list-policies", ["Policies"], ["--scope", "Local"], scope="global"),
    Spec("route53_hosted_zones", "route53", "list-hosted-zones", ["HostedZones"], scope="global"),
    Spec(
        "cloudfront_distributions",
        "cloudfront",
        "list-distributions",
        ["DistributionList", "Items"],
        scope="global",
    ),
]


def _extract(data: Any, path: list[str]) -> Any:
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _probe(spec: Spec) -> dict[str, Any]:
    # Defence in depth: every spec is re-checked against the guard, so a typo
    # here can never turn into a mutating call.
    if evaluate(spec.service, spec.operation, spec.args).verdict is not Verdict.ALLOW:
        return {"resource": spec.label, "status": "blocked", "count": None}

    result = awscli.run(spec.service, spec.operation, spec.args)
    if not result.ok:
        err = (result.denied_reason or result.stderr or "").strip()
        status = "access_denied" if "AccessDenied" in err or "not authorized" in err else "error"
        if "could not be found" in err or "UnrecognizedClient" in err:
            status = "unavailable"
        return {
            "resource": spec.label,
            "status": status,
            "count": None,
            "detail": err.splitlines()[-1][:200] if err else "",
        }

    items = _extract(result.data, spec.path) or []
    if spec.nested and isinstance(items, list):
        items = [child for parent in items for child in (parent.get(spec.nested) or [])]
    count = len(items) if isinstance(items, list) else 0

    entry: dict[str, Any] = {
        "resource": spec.label,
        "status": "ok",
        "count": count,
        "scope": spec.scope,
        "command": result.command,
    }
    if spec.label == "ec2_instances" and isinstance(items, list):
        states: dict[str, int] = {}
        for inst in items:
            state = (inst.get("State") or {}).get("Name", "unknown")
            states[state] = states.get(state, 0) + 1
        entry["by_state"] = states
    if spec.label == "rds_instances" and isinstance(items, list):
        entry["by_status"] = _tally(items, "DBInstanceStatus")
    if spec.label == "cloudwatch_alarms" and isinstance(items, list):
        entry["by_state"] = _tally(items, "StateValue")
    return entry


def _tally(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        value = item.get(key, "unknown")
        out[value] = out.get(value, 0) + 1
    return out


@tool(
    name="aws_inventory",
    description=(
        "Count every resource the agent can see in the configured AWS region "
        "(EC2, EBS, VPC, ELB, ASG, RDS, Lambda, ECS/EKS/ECR, DynamoDB, SNS/SQS, "
        "CloudWatch, S3, IAM, Route53, CloudFront and more). Use this for "
        "'how many X do I have', 'what is running', or as the first step of any "
        "account overview. Returns per-resource counts plus which probes were "
        "denied by IAM. Read-only."
    ),
    input_schema=obj(
        {
            "only": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional subset of resource labels, e.g. ['ec2_instances','rds_instances'].",
            },
            "include_empty": {
                "type": "boolean",
                "description": "Include resources with a count of zero (default true).",
            },
        }
    ),
    stage=3,
    tags=["inventory"],
)
def aws_inventory(only: list[str] | None = None, include_empty: bool = True) -> dict[str, Any]:
    cfg = get_config()
    selected = [s for s in SPECS if not only or s.label in set(only)]
    if not selected:
        return {"ok": False, "error": f"no inventory probes matched {only}"}

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(_probe, selected))

    visible = [r for r in rows if r["status"] == "ok"]
    if not include_empty:
        visible = [r for r in visible if r["count"]]

    counts = {r["resource"]: r["count"] for r in visible}
    problems = [r for r in rows if r["status"] != "ok"]
    report = {
        "ok": True,
        "region": cfg.region,
        "total_resources": sum(counts.values()),
        "counts": counts,
        "details": {r["resource"]: r for r in visible if r.get("by_state") or r.get("by_status")},
        "unavailable": [
            {"resource": r["resource"], "status": r["status"], "detail": r.get("detail", "")}
            for r in problems
        ],
    }
    memory.save_snapshot("inventory", {"counts": counts})
    return report


@tool(
    name="aws_inventory_changes",
    description=(
        "Compare the current resource inventory against the previously saved "
        "snapshot and report what appeared or disappeared. Use for 'what changed "
        "since yesterday' or drift questions. Read-only."
    ),
    input_schema=obj({}),
    stage=6,
    tags=["inventory", "memory"],
)
def aws_inventory_changes() -> dict[str, Any]:
    previous = memory.previous_snapshot("inventory", offset=0)
    current = aws_inventory()
    if not previous:
        return {
            "ok": True,
            "note": "no earlier snapshot — this run becomes the baseline",
            "counts": current["counts"],
        }
    before = previous["payload"].get("counts", {})
    after = current["counts"]
    deltas = {
        key: {
            "before": before.get(key, 0),
            "after": after.get(key, 0),
            "delta": after.get(key, 0) - before.get(key, 0),
        }
        for key in sorted(set(before) | set(after))
        if before.get(key, 0) != after.get(key, 0)
    }
    return {
        "ok": True,
        "baseline_taken_at": previous["created_at"],
        "changed": deltas,
        "unchanged_count": len(set(before) & set(after)) - len(deltas),
    }
