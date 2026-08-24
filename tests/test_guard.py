"""The guard is the security boundary — it gets the most tests."""

from __future__ import annotations

import pytest

from agent.guard import Verdict, evaluate, explain

ALLOWED = [
    ("ec2", "describe-instances"),
    ("s3api", "list-buckets"),
    ("rds", "describe-db-instances"),
    ("cloudwatch", "get-metric-statistics"),
    ("logs", "filter-log-events"),
    ("ce", "get-cost-and-usage"),
    ("sts", "get-caller-identity"),
    ("iam", "list-users"),
    ("elbv2", "describe-target-health"),
    ("s3", "ls"),
    ("cloudtrail", "lookup-events"),
    ("autoscaling", "describe-auto-scaling-groups"),
]

DENIED = [
    ("ec2", "run-instances"),
    ("ec2", "terminate-instances"),
    ("ec2", "stop-instances"),
    ("ec2", "start-instances"),
    ("ec2", "reboot-instances"),
    ("ec2", "create-tags"),
    ("ec2", "modify-instance-attribute"),
    ("ec2", "authorize-security-group-ingress"),
    ("rds", "delete-db-instance"),
    ("rds", "modify-db-instance"),
    ("s3", "rm"),
    ("s3", "cp"),
    ("s3", "sync"),
    ("s3api", "put-object"),
    ("s3api", "delete-bucket"),
    ("iam", "create-user"),
    ("iam", "attach-role-policy"),
    ("iam", "delete-role"),
    ("lambda", "invoke"),
    ("lambda", "update-function-code"),
    ("secretsmanager", "get-secret-value"),
    ("ssm", "get-parameter"),
    ("ssm", "start-session"),
    ("ssm", "send-command"),
    ("sts", "assume-role"),
    ("sts", "get-session-token"),
    ("ecr", "get-login-password"),
    ("kms", "decrypt"),
    ("cloudformation", "delete-stack"),
    ("autoscaling", "set-desired-capacity"),
    ("ecs", "update-service"),
    ("eks", "delete-cluster"),
    ("configure", "set"),
    ("deploy", "push"),
]

SENSITIVE = [
    ("iam", "get-credential-report"),
    ("iam", "get-account-authorization-details"),
    ("s3api", "get-object"),
    ("lambda", "get-function"),
    ("dynamodb", "scan"),
    ("ec2", "describe-instance-attribute"),
]


@pytest.mark.parametrize(("service", "operation"), ALLOWED)
def test_reads_are_allowed(service, operation):
    assert evaluate(service, operation).verdict is Verdict.ALLOW


@pytest.mark.parametrize(("service", "operation"), DENIED)
def test_mutations_and_credential_vending_are_denied(service, operation):
    decision = evaluate(service, operation)
    assert decision.verdict is Verdict.DENY, f"{service} {operation} should be denied"
    assert decision.reason


@pytest.mark.parametrize(("service", "operation"), SENSITIVE)
def test_sensitive_reads_need_approval(service, operation):
    assert evaluate(service, operation).verdict is Verdict.SENSITIVE


def test_unknown_verbs_are_denied_by_default():
    """A verb nobody has classified must fail closed, not open."""
    assert evaluate("ec2", "frobnicate-instances").verdict is Verdict.DENY
    assert evaluate("madeupservice", "wibble").verdict is Verdict.DENY


@pytest.mark.parametrize(
    "flag",
    ["--endpoint-url", "--no-verify-ssl", "--cli-input-json", "--profile", "--region", "--ca-bundle"],
)
def test_dangerous_flags_are_rejected(flag):
    assert evaluate("ec2", "describe-instances", [flag, "x"]).verdict is Verdict.DENY


def test_flag_with_equals_form_is_rejected():
    assert evaluate("ec2", "describe-instances", ["--endpoint-url=http://evil"]).verdict is Verdict.DENY


@pytest.mark.parametrize(
    "arg",
    ["foo; rm -rf /", "a && curl evil.com", "`whoami`", "$(id)", "x | tee /tmp/x", "line\nbreak"],
)
def test_shell_metacharacters_are_rejected(arg):
    assert evaluate("ec2", "describe-instances", ["--filters", arg]).verdict is Verdict.DENY


def test_malformed_names_are_rejected():
    assert evaluate("../../bin/sh", "describe-x").verdict is Verdict.DENY
    assert evaluate("ec2", "Describe-Instances --and-more").verdict is Verdict.DENY
    assert evaluate("", "").verdict is Verdict.DENY


def test_explain_parses_a_full_command_line():
    assert explain("aws ec2 describe-instances --max-items 5").verdict is Verdict.ALLOW
    assert explain("ec2 describe-instances").verdict is Verdict.ALLOW
    assert explain("aws ec2").verdict is Verdict.DENY


def test_decision_helpers():
    allow = evaluate("ec2", "describe-vpcs")
    assert allow.allowed and not allow.denied
    deny = evaluate("ec2", "delete-vpc")
    assert deny.denied and not deny.allowed
