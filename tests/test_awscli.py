from __future__ import annotations

import json

from agent import awscli
from agent.config import get_config


def test_build_command_pins_output_and_region(fake_aws):
    argv = awscli.build_command("ec2", "describe-instances", ["--max-items", "5"])
    assert argv[-4:] == ["--output", "json", "--region", "eu-west-1"] or "--no-cli-pager" in argv
    assert "--output" in argv and "json" in argv
    assert argv[argv.index("--region") + 1] == "eu-west-1"
    assert "--no-cli-pager" in argv


def test_global_endpoint_services_are_pinned_to_us_east_1(fake_aws):
    argv = awscli.build_command("ce", "get-cost-and-usage")
    assert argv[argv.index("--region") + 1] == "us-east-1"


def test_s3_high_level_command_gets_no_output_flag(fake_aws):
    argv = awscli.build_command("s3", "ls")
    assert "--output" not in argv


def test_successful_call_parses_json(fake_aws):
    result = awscli.run("ec2", "describe-instances")
    assert result.ok
    assert result.data["Reservations"][0]["Instances"][0]["InstanceId"] == "i-aaa"
    recorded = json.loads(fake_aws.read_text().splitlines()[0])
    assert recorded[:2] == ["ec2", "describe-instances"]


def test_denied_call_never_reaches_the_binary(fake_aws):
    result = awscli.run("ec2", "terminate-instances", ["--instance-ids", "i-aaa"])
    assert not result.ok
    assert "DENIED by policy" in result.denied_reason
    assert not fake_aws.exists(), "a denied call must not invoke the aws binary"


def test_sensitive_call_is_refused_in_deny_mode(fake_aws):
    result = awscli.run("iam", "get-credential-report")
    assert not result.ok
    assert "approval not granted" in result.denied_reason
    assert not fake_aws.exists()


def test_failed_call_surfaces_the_error(fake_aws, monkeypatch):
    monkeypatch.setenv("FAKE_AWS_FAIL", "1")
    result = awscli.run("ec2", "describe-instances")
    assert not result.ok
    assert "AccessDenied" in result.summary()["error"]


def test_cache_prevents_a_second_invocation(fake_aws, monkeypatch):
    monkeypatch.setenv("AGENT_CACHE_TTL", "60")
    get_config(reload=True)
    awscli.clear_cache()
    first = awscli.run("ec2", "describe-instances")
    second = awscli.run("ec2", "describe-instances")
    assert first.ok and second.ok
    assert second.cached is True
    assert len(fake_aws.read_text().splitlines()) == 1


def test_audit_log_records_every_call(fake_aws):
    from agent import audit

    awscli.run("ec2", "describe-instances")
    awscli.run("ec2", "delete-vpc")
    events = [e["event"] for e in audit.tail(20)]
    assert "aws_call" in events
    assert "aws_call_denied" in events
