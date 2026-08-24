"""Claude client wrapper (STAGE 2).

Thin on purpose: the interesting logic lives in `loop.py`. This module owns the
SDK call, streaming, usage accounting and error translation, so the rest of the
agent never imports `anthropic` directly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .config import get_config

# USD per million tokens, for the local spend estimate printed after each run.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


class LLMUnavailable(RuntimeError):
    """No API key, or the SDK is not installed."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    turns: int = 0

    def add(self, usage: Any) -> None:
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.turns += 1

    def estimated_cost(self, model: str) -> float:
        rate_in, rate_out = PRICING.get(model, PRICING["claude-opus-5"])
        billable_in = self.input_tokens + self.cache_write_tokens * 1.25 + self.cache_read_tokens * 0.1
        return round(billable_in / 1e6 * rate_in + self.output_tokens / 1e6 * rate_out, 4)

    def as_dict(self, model: str) -> dict[str, Any]:
        return {
            "turns": self.turns,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "estimated_cost_usd": self.estimated_cost(model),
        }


@dataclass
class Client:
    """Lazily-constructed Anthropic client plus cumulative usage."""

    usage: Usage = field(default_factory=Usage)
    _client: Any = None

    def _ensure(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - environment problem
            raise LLMUnavailable("the `anthropic` package is not installed — run `make setup`") from exc
        try:
            self._client = anthropic.Anthropic()
        except Exception as exc:  # noqa: BLE001 - surfaces as a friendly CLI error
            raise LLMUnavailable(
                f"could not create the Anthropic client — set ANTHROPIC_API_KEY in .env ({exc})"
            ) from exc
        return self._client

    def complete(
        self,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_text: Callable[[str], None] | None = None,
        on_thinking: Callable[[str], None] | None = None,
    ) -> Any:
        """One streamed request. Returns the accumulated final message."""
        cfg = get_config()
        client = self._ensure()

        kwargs: dict[str, Any] = {
            "model": cfg.model,
            "max_tokens": cfg.max_tokens,
            "system": system,
            "messages": messages,
            # Adaptive thinking: Claude decides how much to reason per turn.
            "thinking": {"type": "adaptive", "display": "summarized" if cfg.show_thinking else "omitted"},
            "output_config": {"effort": cfg.effort},
        }
        if tools:
            kwargs["tools"] = tools

        # Streaming, so a long multi-tool investigation never trips an HTTP timeout.
        with client.messages.stream(**kwargs) as stream:
            for event in stream:
                if event.type != "content_block_delta":
                    continue
                delta = event.delta
                if delta.type == "text_delta" and on_text:
                    on_text(delta.text)
                elif delta.type == "thinking_delta" and on_thinking:
                    on_thinking(delta.thinking)
            message = stream.get_final_message()

        self.usage.add(message.usage)
        return message


def text_of(message: Any) -> str:
    """Concatenate the text blocks of a message."""
    return "".join(b.text for b in message.content if getattr(b, "type", "") == "text").strip()
