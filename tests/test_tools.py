from __future__ import annotations

from agent.tools import REGISTRY, dispatch, specs


def test_every_tool_has_a_usable_schema():
    for name, tool in REGISTRY.items():
        schema = tool.input_schema
        assert schema["type"] == "object", name
        assert schema.get("additionalProperties") is False, name
        assert len(tool.description) > 60, f"{name} needs a description the model can act on"
        for field, definition in schema["properties"].items():
            assert "type" in definition, f"{name}.{field}"


def test_specs_are_stable_for_prompt_caching():
    assert [s["name"] for s in specs()] == sorted(REGISTRY)


def test_unknown_tool_is_reported_not_raised():
    assert dispatch("nope", {})["ok"] is False


def test_bad_arguments_are_reported_not_raised():
    result = dispatch("search_knowledge", {"wrong_argument": 1})
    assert result["ok"] is False and "invalid arguments" in result["error"]


def test_aws_cli_tool_refuses_mutations(fake_aws):
    result = dispatch("aws_cli", {"service": "ec2", "operation": "terminate-instances"})
    assert result["ok"] is False
    assert "DENIED by policy" in result["error"]
    assert not fake_aws.exists()


def test_aws_cli_tool_runs_reads(fake_aws):
    result = dispatch("aws_cli", {"service": "ec2", "operation": "describe-instances"})
    assert result["ok"] is True
    assert result["data"]["Reservations"]


def test_inventory_counts_nested_resources(fake_aws):
    result = dispatch("aws_inventory", {"only": ["ec2_instances", "s3_buckets"]})
    assert result["counts"]["ec2_instances"] == 2
    assert result["counts"]["s3_buckets"] == 2
    assert result["details"]["ec2_instances"]["by_state"] == {"running": 1, "stopped": 1}


def test_inventory_reports_blind_spots(fake_aws, monkeypatch):
    monkeypatch.setenv("FAKE_AWS_FAIL", "1")
    result = dispatch("aws_inventory", {"only": ["ec2_instances"]})
    assert result["counts"] == {}
    assert result["unavailable"][0]["status"] == "access_denied"


def test_health_marks_alarms_as_degraded(fake_aws):
    result = dispatch("aws_health", {"checks": ["alarms"]})
    assert result["overall"] == "unhealthy"
    assert result["checks"]["alarms"]["in_alarm"] == 1


def test_health_reports_unknown_when_aws_fails(fake_aws, monkeypatch):
    monkeypatch.setenv("FAKE_AWS_FAIL", "1")
    result = dispatch("aws_health", {"checks": ["rds"]})
    assert result["overall"] == "partial"
    assert result["unavailable_checks"] == ["rds"]


def test_security_posture_finds_open_ssh_and_public_rds(fake_aws):
    result = dispatch("aws_security_posture", {})
    kinds = {f["type"] for f in result["findings"]}
    assert "security_group_open_to_internet" in kinds
    assert "rds_publicly_accessible" in kinds
    assert "ebs_unencrypted_volumes" in kinds
    assert result["findings"][0]["severity"] == "high"


def test_memory_tools_round_trip():
    dispatch("remember", {"key": "k", "value": "v"})
    assert dispatch("recall", {"query": "k"})["facts"][0]["value"] == "v"


def test_knowledge_tool_returns_passages():
    result = dispatch("search_knowledge", {"query": "rds storage running low"})
    assert result["matches"] >= 1
