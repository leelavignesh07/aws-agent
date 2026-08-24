"""Composite reports (STAGE 5).

These run without the model — pure AWS CLI reads assembled into a fixed shape.
That matters for two reasons: a scheduled report should not depend on an LLM
being reachable, and it gives the model a cheap, high-signal starting point when
it is involved.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from . import memory
from .config import get_config
from .tools import dispatch


def status_report(include_cost: bool = True, include_security: bool = False) -> dict[str, Any]:
    """Inventory + health (+ optional cost and security) in one payload."""
    cfg = get_config()
    report: dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "region": cfg.region,
        "inventory": dispatch("aws_inventory", {"include_empty": False}),
        "health": dispatch("aws_health", {}),
    }
    if include_cost:
        report["cost"] = dispatch("aws_cost", {"days": 7})
    if include_security:
        report["security"] = dispatch("aws_security_posture", {})

    report["summary"] = _summarise(report)
    memory.save_snapshot("status_report", report["summary"])
    return report


def _summarise(report: dict[str, Any]) -> dict[str, Any]:
    health = report.get("health", {})
    inventory = report.get("inventory", {})
    cost = report.get("cost", {})
    security = report.get("security", {})

    headline = {
        "overall_health": health.get("overall", "unknown"),
        "resources_seen": inventory.get("total_resources", 0),
        "degraded_checks": health.get("degraded_checks", []),
        "blind_spots": [u["resource"] for u in inventory.get("unavailable", [])][:12],
    }
    alarms = health.get("checks", {}).get("alarms") or {}
    if alarms.get("in_alarm"):
        headline["alarms_in_alarm"] = alarms["in_alarm"]
    if cost.get("ok"):
        headline["spend_last_7d_usd"] = cost.get("total")
        if cost.get("trend"):
            headline["day_over_day_pct"] = cost["trend"]["day_over_day_pct"]
        if cost.get("top"):
            headline["top_service"] = cost["top"][0]
    if security.get("ok"):
        headline["security_findings"] = security.get("by_severity", {})
    return headline


def render_text(report: dict[str, Any]) -> str:
    """Terminal rendering — the shape you want in a Slack paste or an email."""
    summary = report.get("summary", {})
    health = report.get("health", {})
    inventory = report.get("inventory", {})
    lines = [
        "=" * 68,
        f" AWS STATUS REPORT — {report.get('region')} — {report.get('generated_at')}",
        "=" * 68,
        "",
        f" Overall health : {str(summary.get('overall_health', 'unknown')).upper()}",
        f" Resources seen : {summary.get('resources_seen', 0)}",
    ]
    if "alarms_in_alarm" in summary:
        lines.append(f" Alarms firing  : {summary['alarms_in_alarm']}")
    if "spend_last_7d_usd" in summary:
        trend = summary.get("day_over_day_pct")
        suffix = f"  ({trend:+.1f}% day over day)" if isinstance(trend, (int, float)) else ""
        lines.append(f" Spend (7 days) : ${summary['spend_last_7d_usd']}{suffix}")
    if summary.get("security_findings"):
        counts = summary["security_findings"]
        lines.append(f" Security       : {counts.get('high', 0)} high, {counts.get('medium', 0)} medium")

    lines += ["", "-- Resource counts " + "-" * 49]
    counts = inventory.get("counts", {})
    for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        if count:
            lines.append(f"   {name:<28} {count:>6}")
    if not any(counts.values()):
        lines.append("   (nothing visible — check credentials and region)")

    lines += ["", "-- Health checks " + "-" * 51]
    for name, result in (health.get("checks") or {}).items():
        state = str(result.get("status", "?")).upper()
        marker = {"OK": "PASS", "DEGRADED": "FAIL", "ALARM": "FAIL", "UNKNOWN": "SKIP"}.get(state, state)
        lines.append(f"   [{marker}] {name}")
        for entry in (result.get("impaired") or result.get("not_available") or [])[:5]:
            lines.append(f"          - {entry}")
        for alarm in (result.get("alarms") or [])[:5]:
            lines.append(f"          - {alarm.get('name')}: {alarm.get('reason', '')[:90]}")
        for target in (result.get("unhealthy_targets") or [])[:5]:
            lines.append(
                f"          - {target.get('target')} in {target.get('target_group')}: {target.get('state')}"
            )

    if summary.get("blind_spots"):
        lines += ["", f" Not visible to this agent: {', '.join(summary['blind_spots'])}"]

    security = report.get("security") or {}
    if security.get("findings"):
        lines += ["", "-- Security findings " + "-" * 47]
        for finding in security["findings"][:15]:
            lines.append(f"   [{finding['severity'].upper():<6}] {finding['type']}: {finding['detail'][:80]}")

    cost = report.get("cost") or {}
    if cost.get("ok") and cost.get("top"):
        lines += ["", "-- Top spend by service (7 days) " + "-" * 35]
        for row in cost["top"][:8]:
            lines.append(f"   {row['key']:<40} ${row['cost']:>10.2f}")

    lines += ["", "=" * 68, " Read-only report. This agent cannot change any resource.", "=" * 68]
    return "\n".join(lines)
