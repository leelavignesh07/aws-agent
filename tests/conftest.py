"""Shared fixtures.

Every test runs against a throwaway state directory and a fake `aws` binary, so
the suite needs neither AWS credentials nor network access.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import awscli  # noqa: E402
from agent.config import get_config  # noqa: E402

FAKE_AWS = (Path(__file__).resolve().parent / "fake_aws_cli.py").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Point config at a temp state dir and drop cached singletons."""
    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.setenv("AGENT_APPROVAL_MODE", "deny")
    monkeypatch.setenv("AGENT_CACHE_TTL", "0")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("ANTHROPIC_SECRET_ARN", raising=False)
    get_config(reload=True)
    awscli.clear_cache()
    yield
    awscli.clear_cache()


@pytest.fixture
def fake_aws(tmp_path, monkeypatch):
    """Install a fake `aws` on PATH; yields the path of its argv log."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "aws"
    fake.write_text(FAKE_AWS, encoding="utf-8")
    fake.chmod(0o755)
    log = tmp_path / "argv.log"
    monkeypatch.setenv("FAKE_AWS_LOG", str(log))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return log
