"""Conversation memory and durable facts (STAGE 6).

SQLite, on purpose: one file, no server, survives restarts, and you can inspect
it with `sqlite3 var/agent.db` when something looks wrong.

Two things are stored:
  * `turns` — the rolling conversation, so `--session prod` picks up where it
    left off instead of re-discovering the account every time.
  * `facts` — durable notes the agent (or you) decided are worth keeping:
    "the prod NAT gateway is in eu-west-1b", "alarm X is known-noisy".
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import get_config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session    TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session, id);

CREATE TABLE IF NOT EXISTS facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scope      TEXT NOT NULL DEFAULT 'global',
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'agent',
    created_at REAL NOT NULL,
    UNIQUE(scope, key)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    region     TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_kind ON snapshots(kind, region, id);
"""


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    cfg = get_config()
    path = db_path or cfg.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


# --- conversation ----------------------------------------------------------


def save_turn(session: str, role: str, content: Any) -> None:
    payload = content if isinstance(content, str) else json.dumps(content, default=str)
    with connect() as conn:
        conn.execute(
            "INSERT INTO turns (session, role, content, created_at) VALUES (?,?,?,?)",
            (session, role, payload, time.time()),
        )


def load_turns(session: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return the last `limit` turns, oldest first, as Messages-API dicts."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT role, content FROM turns WHERE session = ? ORDER BY id DESC LIMIT ?",
            (session, limit),
        ).fetchall()
    out = []
    for row in reversed(rows):
        content: Any = row["content"]
        if content.startswith(("[", "{")):
            with contextlib.suppress(json.JSONDecodeError):
                content = json.loads(content)
        out.append({"role": row["role"], "content": content})
    return out


def clear_session(session: str) -> int:
    with connect() as conn:
        cur = conn.execute("DELETE FROM turns WHERE session = ?", (session,))
        return cur.rowcount


def list_sessions() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT session, COUNT(*) AS turns, MAX(created_at) AS last_used "
            "FROM turns GROUP BY session ORDER BY last_used DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# --- facts -----------------------------------------------------------------


def remember(key: str, value: str, scope: str = "global", source: str = "agent") -> dict[str, Any]:
    with connect() as conn:
        conn.execute(
            "INSERT INTO facts (scope, key, value, source, created_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(scope, key) DO UPDATE SET value=excluded.value, "
            "source=excluded.source, created_at=excluded.created_at",
            (scope, key, value, source, time.time()),
        )
    return {"stored": True, "scope": scope, "key": key, "value": value}


def recall(query: str = "", scope: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
    sql = "SELECT scope, key, value, source, created_at FROM facts"
    clauses, params = [], []
    if scope:
        clauses.append("scope = ?")
        params.append(scope)
    if query:
        clauses.append("(key LIKE ? OR value LIKE ?)")
        params += [f"%{query}%", f"%{query}%"]
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def forget(key: str, scope: str = "global") -> int:
    with connect() as conn:
        return conn.execute("DELETE FROM facts WHERE scope = ? AND key = ?", (scope, key)).rowcount


# --- snapshots (trend detection) -------------------------------------------


def save_snapshot(kind: str, payload: dict[str, Any], region: str | None = None) -> None:
    cfg = get_config()
    with connect() as conn:
        conn.execute(
            "INSERT INTO snapshots (kind, region, payload, created_at) VALUES (?,?,?,?)",
            (kind, region or cfg.region, json.dumps(payload, default=str), time.time()),
        )


def previous_snapshot(kind: str, region: str | None = None, offset: int = 1) -> dict[str, Any] | None:
    """The Nth-most-recent snapshot of a kind — used to diff 'what changed'."""
    cfg = get_config()
    with connect() as conn:
        row = conn.execute(
            "SELECT payload, created_at FROM snapshots WHERE kind = ? AND region = ? "
            "ORDER BY id DESC LIMIT 1 OFFSET ?",
            (kind, region or cfg.region, offset),
        ).fetchone()
    if not row:
        return None
    return {"created_at": row["created_at"], "payload": json.loads(row["payload"])}
