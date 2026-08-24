"""Health and status checks — 'is anything broken right now?' (STAGE 3/5)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from .. import awscli
from ..config import get_config
from .base import obj, tool


def _iso(minutes_ago: int) -> str:
    stamp = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_ago)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _alarms() -> dict[str, Any]:
    result = awscli.run("cloudwatch", "describe-alarms", ["--state-value", "ALARM"])
    if not result.ok:
        return {"status": "unknown", "error": result.summary().get("error", "")}
    alarms = (result.data or {}).get("MetricAlarms", []) + (result.data or {}).get("CompositeAlarms", [])
    return {
        "status": "alarm" if alarms else "ok",
        "in_alarm": len(alarms),
        "alarms": [
            {
                "name": a.get("AlarmName"),
                "metric": a.get("MetricName"),
                "namespace": a.get("Namespace"),
                "since": a.get("StateUpdatedTimestamp"),
                "reason": (a.get("StateReason") or "")[:200],
            }
            for a in alarms[:25]
        ],
    }


def _ec2_status() -> dict[str, Any]:
    result = awscli.run("ec2", "describe-instance-status", ["--include-all-instances", "--max-items", "200"])
    if not result.ok:
        return {"status": "unknown", "error": result.summary().get("error", "")}
    statuses = (result.data or {}).get("InstanceStatuses", [])
    impaired = [
        {
            "instance_id": s.get("InstanceId"),
            "az": s.get("AvailabilityZone"),
            "state": (s.get("InstanceState") or {}).get("Name"),
            "system_check": (s.get("SystemStatus") or {}).get("Status"),
            "instance_check": (s.get("InstanceStatus") or {}).get("Status"),
        }
        for s in statuses
        if (s.get("SystemStatus") or {}).get("Status") not in {"ok", None}
        or (s.get("InstanceStatus") or {}).get("Status") not in {"ok", None}
    ]
    running = [s for s in statuses if (s.get("InstanceState") or {}).get("Name") == "running"]
    return {
        "status": "degraded" if impaired else "ok",
        "instances_reporting": len(statuses),
        "running": len(running),
        "impaired": impaired,
    }


def _rds_status() -> dict[str, Any]:
    result = awscli.run("rds", "describe-db-instances")
    if not result.ok:
        return {"status": "unknown", "error": result.summary().get("error", "")}
    instances = (result.data or {}).get("DBInstances", [])
    unhealthy = [
        {
            "id": db.get("DBInstanceIdentifier"),
            "status": db.get("DBInstanceStatus"),
            "engine": db.get("Engine"),
            "multi_az": db.get("MultiAZ"),
        }
        for db in instances
        if db.get("DBInstanceStatus") != "available"
    ]
    return {
        "status": "degraded" if unhealthy else "ok",
        "total": len(instances),
        "not_available": unhealthy,
    }


def _asg_status() -> dict[str, Any]:
    result = awscli.run("autoscaling", "describe-auto-scaling-groups")
    if not result.ok:
        return {"status": "unknown", "error": result.summary().get("error", "")}
    groups = (result.data or {}).get("AutoScalingGroups", [])
    problems = []
    for group in groups:
        healthy = [i for i in group.get("Instances", []) if i.get("HealthStatus") == "Healthy"]
        if len(healthy) < group.get("DesiredCapacity", 0):
            problems.append(
                {
                    "name": group.get("AutoScalingGroupName"),
                    "desired": group.get("DesiredCapacity"),
                    "healthy": len(healthy),
                    "in_service": sum(
                        1 for i in group.get("Instances", []) if i.get("LifecycleState") == "InService"
                    ),
                }
            )
    return {"status": "degraded" if problems else "ok", "total": len(groups), "under_capacity": problems}


def _target_health(max_groups: int = 10) -> dict[str, Any]:
    listing = awscli.run("elbv2", "describe-target-groups")
    if not listing.ok:
        return {"status": "unknown", "error": listing.summary().get("error", "")}
    groups = (listing.data or {}).get("TargetGroups", [])[:max_groups]
    unhealthy = []
    for group in groups:
        arn = group.get("TargetGroupArn")
        health = awscli.run("elbv2", "describe-target-health", ["--target-group-arn", arn])
        if not health.ok:
            continue
        for entry in (health.data or {}).get("TargetHealthDescriptions", []):
            state = (entry.get("TargetHealth") or {}).get("State")
            if state and state != "healthy":
                unhealthy.append(
                    {
                        "target_group": group.get("TargetGroupName"),
                        "target": (entry.get("Target") or {}).get("Id"),
                        "state": state,
                        "reason": (entry.get("TargetHealth") or {}).get("Reason"),
                    }
                )
    return {
        "status": "degraded" if unhealthy else "ok",
        "target_groups_checked": len(groups),
        "unhealthy_targets": unhealthy,
    }


def _ecs_status() -> dict[str, Any]:
    listing = awscli.run("ecs", "list-clusters")
    if not listing.ok:
        return {"status": "unknown", "error": listing.summary().get("error", "")}
    arns = (listing.data or {}).get("clusterArns", [])
    if not arns:
        return {"status": "ok", "clusters": 0}
    described = awscli.run("ecs", "describe-clusters", ["--clusters", *arns[:10]])
    clusters = (described.data or {}).get("clusters", []) if described.ok else []
    return {
        "status": "ok",
        "clusters": len(arns),
        "detail": [
            {
                "name": c.get("clusterName"),
                "status": c.get("status"),
                "running_tasks": c.get("runningTasksCount"),
                "pending_tasks": c.get("pendingTasksCount"),
                "active_services": c.get("activeServicesCount"),
            }
            for c in clusters
        ],
    }


CHECKS = {
    "alarms": _alarms,
    "ec2": _ec2_status,
    "rds": _rds_status,
    "asg": _asg_status,
    "load_balancers": _target_health,
    "ecs": _ecs_status,
}


@tool(
    name="aws_health",
    description=(
        "Run the account health sweep: CloudWatch alarms currently in ALARM, EC2 "
        "instance status checks, RDS instance states, Auto Scaling group capacity, "
        "load balancer target health and ECS cluster task counts. Use this for "
        "'is anything broken', 'what is the status of my infrastructure', or "
        "before/after a deployment. Read-only."
    ),
    input_schema=obj(
        {
            "checks": {
                "type": "array",
                "items": {"type": "string", "enum": sorted(CHECKS)},
                "description": "Subset of checks to run; omit to run all of them.",
            }
        }
    ),
    stage=3,
    tags=["health"],
)
def aws_health(checks: list[str] | None = None) -> dict[str, Any]:
    cfg = get_config()
    selected = [c for c in (checks or list(CHECKS)) if c in CHECKS]
    results = {name: CHECKS[name]() for name in selected}

    degraded = [n for n, r in results.items() if r.get("status") in {"degraded", "alarm"}]
    unknown = [n for n, r in results.items() if r.get("status") == "unknown"]
    overall = "unhealthy" if degraded else ("partial" if unknown else "healthy")
    return {
        "ok": True,
        "region": cfg.region,
        "overall": overall,
        "degraded_checks": degraded,
        "unavailable_checks": unknown,
        "checks": results,
    }


@tool(
    name="aws_metric",
    description=(
        "Fetch CloudWatch metric statistics for one metric over a time window "
        "(e.g. AWS/EC2 CPUUtilization, AWS/RDS FreeStorageSpace, AWS/Lambda Errors). "
        "Use when a health check points at a resource and you need the trend. Read-only."
    ),
    input_schema=obj(
        {
            "namespace": {"type": "string", "description": "e.g. AWS/EC2, AWS/RDS, AWS/Lambda"},
            "metric_name": {"type": "string", "description": "e.g. CPUUtilization"},
            "dimensions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Dimensions as Name=X,Value=Y strings, e.g. ['Name=InstanceId,Value=i-0abc'].",
            },
            "minutes": {"type": "integer", "description": "Look-back window in minutes (default 180)."},
            "period": {"type": "integer", "description": "Sample period in seconds (default 300)."},
            "statistics": {
                "type": "array",
                "items": {"type": "string", "enum": ["Average", "Sum", "Maximum", "Minimum", "SampleCount"]},
            },
        },
        required=["namespace", "metric_name"],
    ),
    stage=5,
    tags=["health", "metrics"],
)
def aws_metric(
    namespace: str,
    metric_name: str,
    dimensions: list[str] | None = None,
    minutes: int = 180,
    period: int = 300,
    statistics: list[str] | None = None,
) -> dict[str, Any]:
    args = [
        "--namespace",
        namespace,
        "--metric-name",
        metric_name,
        "--start-time",
        _iso(minutes),
        "--end-time",
        _iso(0),
        "--period",
        str(period),
        "--statistics",
        *(statistics or ["Average", "Maximum"]),
    ]
    if dimensions:
        args += ["--dimensions", *dimensions]
    result = awscli.run("cloudwatch", "get-metric-statistics", args)
    if not result.ok:
        return result.summary()
    points = sorted((result.data or {}).get("Datapoints", []), key=lambda p: p.get("Timestamp", ""))
    return {
        "ok": True,
        "namespace": namespace,
        "metric": metric_name,
        "unit": points[0].get("Unit") if points else None,
        "datapoints": points[-60:],
        "count": len(points),
    }
