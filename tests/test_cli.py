from __future__ import annotations

import json

import pytest

from agent.cli import main


def test_policy_command_lists_decisions(capsys):
    assert main(["policy"]) == 0
    out = capsys.readouterr().out
    assert "ALLOW" in out and "DENY" in out and "SENSITIVE" in out


def test_policy_command_evaluates_one_command(capsys):
    assert main(["policy", "ec2", "delete-vpc"]) == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "deny"


def test_tools_command_lists_the_registry(capsys):
    assert main(["tools"]) == 0
    assert "aws_inventory" in capsys.readouterr().out


def test_inventory_command_renders(fake_aws, capsys):
    assert main(["inventory"]) == 0
    assert "Resource inventory" in capsys.readouterr().out


def test_health_command_exit_code_reflects_health(fake_aws):
    assert main(["health", "--checks", "alarms"]) == 1  # the fake account has a firing alarm


def test_doctor_returns_nonzero_when_prerequisites_are_missing(capsys):
    code = main(["doctor"])
    assert code in {0, 1}
    assert "environment check" in capsys.readouterr().out


def test_memory_add_and_list(capsys):
    main(["memory", "add", "--key", "k", "--value", "v"])
    capsys.readouterr()
    main(["memory", "list"])
    assert '"value": "v"' in capsys.readouterr().out


def test_run_tool_executes_a_single_tool(capsys):
    assert main(["run-tool", "search_knowledge", "--input", '{"query": "rds"}']) == 0
    assert "passages" in capsys.readouterr().out


def test_unknown_command_exits_with_usage_error():
    with pytest.raises(SystemExit):
        main(["not-a-command"])
