"""Runtime configuration, loaded from the environment (and an optional .env).

Everything the agent needs is here so no other module reads os.environ directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_DIR = PROJECT_ROOT / "var"


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader — real values in the environment always win."""
    path = path or PROJECT_ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Config:
    # --- Claude ---------------------------------------------------------
    model: str = field(default_factory=lambda: os.environ.get("AGENT_MODEL", "claude-opus-5"))
    effort: str = field(default_factory=lambda: os.environ.get("AGENT_EFFORT", "high"))
    max_tokens: int = field(default_factory=lambda: _int("AGENT_MAX_TOKENS", 32000))
    max_iterations: int = field(default_factory=lambda: _int("AGENT_MAX_ITERATIONS", 24))
    show_thinking: bool = field(default_factory=lambda: _bool("AGENT_SHOW_THINKING", False))

    # --- AWS ------------------------------------------------------------
    region: str = field(
        default_factory=lambda: (
            os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
        )
    )
    profile: str = field(default_factory=lambda: os.environ.get("AWS_PROFILE", ""))
    cli_timeout: int = field(default_factory=lambda: _int("AGENT_CLI_TIMEOUT", 90))
    cache_ttl: int = field(default_factory=lambda: _int("AGENT_CACHE_TTL", 60))

    # --- Safety (STAGE 7) -----------------------------------------------
    # ask   : prompt a human on the terminal for SENSITIVE calls
    # deny  : refuse every SENSITIVE call (the right default for servers)
    # allow : auto-approve SENSITIVE calls (break-glass; audited loudly)
    approval_mode: str = field(default_factory=lambda: os.environ.get("AGENT_APPROVAL_MODE", "ask"))
    redact_account_id: bool = field(default_factory=lambda: _bool("AGENT_REDACT_ACCOUNT_ID", False))

    # --- State (STAGE 6/8) ----------------------------------------------
    state_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("AGENT_STATE_DIR", str(DEFAULT_STATE_DIR)))
    )
    knowledge_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("AGENT_KNOWLEDGE_DIR", str(PROJECT_ROOT / "knowledge")))
    )
    json_logs: bool = field(default_factory=lambda: _bool("AGENT_JSON_LOGS", False))

    # --- Server (STAGE 4/8) ---------------------------------------------
    api_token: str = field(default_factory=lambda: os.environ.get("AGENT_API_TOKEN", ""))

    @property
    def db_path(self) -> Path:
        return self.state_dir / "agent.db"

    @property
    def audit_path(self) -> Path:
        return self.state_dir / "audit.jsonl"

    def ensure_dirs(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)


_config: Config | None = None


def get_config(reload: bool = False) -> Config:
    global _config
    if _config is None or reload:
        load_dotenv()
        from .secrets import hydrate_api_key  # local import keeps this module dependency-free

        hydrate_api_key()
        _config = Config()
        _config.ensure_dirs()
    return _config
