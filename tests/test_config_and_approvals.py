from __future__ import annotations

from agent import approvals
from agent.config import Config, get_config, load_dotenv
from agent.guard import Verdict, evaluate
from agent.secrets import hydrate_api_key


def test_config_reads_the_environment(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("AGENT_MAX_ITERATIONS", "3")
    cfg = get_config(reload=True)
    assert cfg.model == "claude-sonnet-5"
    assert cfg.max_iterations == 3


def test_bad_integers_fall_back_to_the_default(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_ITERATIONS", "not-a-number")
    assert get_config(reload=True).max_iterations == 24


def test_dotenv_never_overrides_a_real_environment_variable(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("AGENT_MODEL=from-file\nAGENT_EFFORT=low\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_MODEL", "from-environment")
    monkeypatch.delenv("AGENT_EFFORT", raising=False)
    load_dotenv(env_file)
    assert Config().model == "from-environment"
    assert Config().effort == "low"


def test_deny_mode_refuses_sensitive_reads(monkeypatch):
    monkeypatch.setenv("AGENT_APPROVAL_MODE", "deny")
    get_config(reload=True)
    result = approvals.request_approval(
        evaluate("iam", "get-credential-report"), "aws iam get-credential-report"
    )
    assert result.granted is False


def test_allow_mode_is_break_glass(monkeypatch):
    monkeypatch.setenv("AGENT_APPROVAL_MODE", "allow")
    get_config(reload=True)
    result = approvals.request_approval(evaluate("s3api", "get-object"), "aws s3api get-object")
    assert result.granted is True
    assert "break-glass" in result.reason


def test_ask_mode_declines_when_nobody_can_answer(monkeypatch):
    """A non-interactive session must fail closed rather than hang or auto-allow."""
    monkeypatch.setenv("AGENT_APPROVAL_MODE", "ask")
    get_config(reload=True)
    result = approvals.request_approval(evaluate("s3api", "get-object"), "aws s3api get-object")
    assert result.granted is False
    assert "non-interactive" in result.reason


def test_approval_is_only_ever_asked_for_sensitive_calls():
    assert evaluate("ec2", "describe-instances").verdict is Verdict.ALLOW
    assert evaluate("ec2", "delete-vpc").verdict is Verdict.DENY


def test_secret_hydration_is_a_noop_without_configuration(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_SECRET_ARN", raising=False)
    assert hydrate_api_key() is False


def test_secret_hydration_keeps_an_existing_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-existing")
    monkeypatch.setenv("ANTHROPIC_SECRET_ARN", "arn:aws:secretsmanager:eu-west-1:1:secret:x")
    assert hydrate_api_key() is True
    import os

    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-existing"
