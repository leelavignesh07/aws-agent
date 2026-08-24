"""Tool registry shared by the CLI and the model loop.

A `Tool` is a plain Python function plus the JSON schema Claude sees. Registering
one here makes it available to `agent ask`, to the HTTP server, and to the
model's tool loop at the same time.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .. import audit

ToolFn = Callable[..., Any]


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: ToolFn
    stage: int = 3
    tags: list[str] = field(default_factory=list)

    def spec(self) -> dict[str, Any]:
        """The definition sent to the Messages API."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def __call__(self, **kwargs: Any) -> Any:
        return self.fn(**kwargs)


REGISTRY: dict[str, Tool] = {}


def tool(
    name: str,
    description: str,
    input_schema: dict[str, Any],
    *,
    stage: int = 3,
    tags: list[str] | None = None,
) -> Callable[[ToolFn], ToolFn]:
    def decorator(fn: ToolFn) -> ToolFn:
        REGISTRY[name] = Tool(name, description, input_schema, fn, stage, tags or [])
        return fn

    return decorator


def specs() -> list[dict[str, Any]]:
    """Tool definitions in a stable order — important for prompt caching."""
    return [REGISTRY[k].spec() for k in sorted(REGISTRY)]


def dispatch(name: str, payload: dict[str, Any] | None = None) -> Any:
    """Run a tool by name, recording the call and never raising into the loop."""
    payload = payload or {}
    entry = REGISTRY.get(name)
    if entry is None:
        return {"ok": False, "error": f"unknown tool: {name}"}

    started = time.monotonic()
    audit.record("tool_call", tool=name, input=payload)
    try:
        result = entry(**payload)
        ok = True
    except TypeError as exc:  # bad arguments from the model
        result, ok = {"ok": False, "error": f"invalid arguments for {name}: {exc}"}, False
    except Exception as exc:  # noqa: BLE001 - a tool failure must not kill the loop
        result, ok = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, False

    audit.record(
        "tool_result",
        tool=name,
        ok=ok,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return result


def obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    """Shorthand for a strict object schema."""
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }
