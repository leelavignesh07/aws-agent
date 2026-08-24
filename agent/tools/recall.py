"""Memory and knowledge tools exposed to the model (STAGE 6)."""

from __future__ import annotations

from typing import Any

from .. import memory
from ..knowledge import get_kb
from .base import obj, tool


@tool(
    name="remember",
    description=(
        "Store a durable fact about this AWS account so future sessions know it — "
        "known-noisy alarms, ownership, deliberate exceptions. Use when the operator "
        "tells you something worth keeping, or when you establish a fact that would be "
        "expensive to rediscover."
    ),
    input_schema=obj(
        {
            "key": {"type": "string", "description": "Short stable identifier, e.g. 'noisy-alarm-crm-cpu'."},
            "value": {"type": "string", "description": "The fact, in one or two sentences."},
            "scope": {"type": "string", "description": "Optional grouping, e.g. 'prod'. Default 'global'."},
        },
        required=["key", "value"],
    ),
    stage=6,
    tags=["memory"],
)
def remember(key: str, value: str, scope: str = "global") -> dict[str, Any]:
    return memory.remember(key, value, scope=scope)


@tool(
    name="recall",
    description=(
        "Look up facts stored earlier about this account. Call this before "
        "investigating anything that might already be known (recurring alarms, "
        "documented exceptions, ownership)."
    ),
    input_schema=obj(
        {
            "query": {"type": "string", "description": "Substring to search for; omit to list recent facts."},
            "scope": {"type": "string", "description": "Optional scope filter."},
        }
    ),
    stage=6,
    tags=["memory"],
)
def recall(query: str = "", scope: str | None = None) -> dict[str, Any]:
    facts = memory.recall(query, scope=scope)
    return {"ok": True, "count": len(facts), "facts": facts}


@tool(
    name="search_knowledge",
    description=(
        "Search the team's runbooks and account conventions (the markdown files under "
        "knowledge/). Call this whenever a check comes back degraded — the runbook "
        "usually states the triage order and whether the fix needs a human."
    ),
    input_schema=obj(
        {
            "query": {"type": "string", "description": "What you need guidance on, e.g. 'rds storage low'."},
            "limit": {"type": "integer", "description": "Max passages to return (default 4)."},
        },
        required=["query"],
    ),
    stage=6,
    tags=["knowledge"],
)
def search_knowledge(query: str, limit: int = 4) -> dict[str, Any]:
    hits = get_kb().search(query, limit=limit)
    return {"ok": True, "query": query, "matches": len(hits), "passages": hits}
