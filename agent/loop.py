"""The agent loop (STAGE 2 → STAGE 3).

A manual Messages-API tool loop rather than the SDK tool runner, because every
iteration here also has to write the audit trail, persist memory, enforce the
iteration budget and stay readable as the teaching artefact of this project.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import audit, memory, prompt, tools
from .config import get_config
from .llm import Client, LLMUnavailable, Usage, text_of


@dataclass
class AgentResult:
    answer: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    iterations: int = 0
    stopped_because: str = "end_turn"

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "tool_calls": self.tool_calls,
            "usage": self.usage,
            "iterations": self.iterations,
            "stopped_because": self.stopped_because,
        }


def _stderr(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def ask(
    question: str,
    *,
    session: str | None = None,
    use_tools: bool = True,
    stream_to_stdout: bool = True,
    client: Client | None = None,
    extra_context: str | None = None,
) -> AgentResult:
    """Answer one question, running as many read-only tool calls as it takes."""
    cfg = get_config()
    client = client or Client()

    facts = memory.recall(limit=20) if session else []
    system = prompt.build(extra=extra_context, facts=facts)
    tool_specs = tools.specs() if use_tools else None

    history: list[dict[str, Any]] = memory.load_turns(session) if session else []
    messages: list[dict[str, Any]] = [*history, {"role": "user", "content": question}]
    if session:
        memory.save_turn(session, "user", question)

    audit.record("agent_ask", question=question[:500], session=session, tools=bool(tool_specs))

    called: list[dict[str, Any]] = []
    answer = ""
    stopped = "end_turn"
    iteration = 0

    on_text: Callable[[str], None] | None = (
        (lambda chunk: print(chunk, end="", flush=True)) if stream_to_stdout else None
    )
    on_thinking = (
        (lambda chunk: print(f"\033[90m{chunk}\033[0m", end="", flush=True))
        if (stream_to_stdout and cfg.show_thinking)
        else None
    )

    while iteration < cfg.max_iterations:
        iteration += 1
        message = client.complete(
            messages, system=system, tools=tool_specs, on_text=on_text, on_thinking=on_thinking
        )

        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            stopped = f"refusal:{getattr(details, 'category', 'unknown')}"
            answer = text_of(message) or "The model declined to answer this request."
            break

        # Echo the assistant turn back verbatim — thinking and tool_use blocks
        # included — so the next request keeps a valid, replayable history.
        messages.append({"role": "assistant", "content": message.content})

        if message.stop_reason == "pause_turn":
            continue  # a long turn was paused server-side; resume it

        tool_uses = [b for b in message.content if getattr(b, "type", "") == "tool_use"]
        if not tool_uses:
            answer = text_of(message)
            stopped = message.stop_reason or "end_turn"
            break

        results: list[dict[str, Any]] = []
        for block in tool_uses:
            payload = block.input if isinstance(block.input, dict) else {}
            if stream_to_stdout:
                _stderr(f"\n\033[36m▸ {block.name}({_compact(payload)})\033[0m")
            output = tools.dispatch(block.name, payload)
            called.append({"tool": block.name, "input": payload})
            is_error = isinstance(output, dict) and output.get("ok") is False
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(output, default=str)[:120_000],
                    **({"is_error": True} if is_error else {}),
                }
            )
        # All results for one assistant turn go back in a single user message.
        messages.append({"role": "user", "content": results})
    else:
        stopped = "max_iterations"
        answer = answer or (
            f"Stopped after {cfg.max_iterations} tool iterations without a final answer. "
            "Narrow the question, or raise AGENT_MAX_ITERATIONS."
        )

    if session:
        memory.save_turn(session, "assistant", answer)

    usage = client.usage.as_dict(cfg.model)
    audit.record(
        "agent_answer",
        session=session,
        iterations=iteration,
        tool_calls=len(called),
        stopped_because=stopped,
        **usage,
    )
    return AgentResult(answer.strip(), called, usage, iteration, stopped)


def _compact(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, default=str)
    return text if len(text) <= 120 else text[:117] + "..."


def chat(session: str = "default") -> None:
    """Interactive REPL (STAGE 2)."""
    cfg = get_config()
    client = Client()
    print(f"AWS Monitoring Agent — region {cfg.region}, model {cfg.model}")
    print("Read-only. Type 'exit' to quit, '/facts' to list memory, '/clear' to reset this session.\n")
    while True:
        try:
            line = input("\033[1myou ›\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line in {"exit", "quit"}:
            return
        if line == "/facts":
            for fact in memory.recall():
                print(f"  {fact['scope']}/{fact['key']}: {fact['value']}")
            continue
        if line == "/clear":
            print(f"  cleared {memory.clear_session(session)} turns")
            continue

        print("\033[1magent ›\033[0m ", end="", flush=True)
        try:
            result = ask(line, session=session, client=client, stream_to_stdout=True)
        except LLMUnavailable as exc:
            print(f"\n\033[31m{exc}\033[0m")
            continue
        print(
            f"\n\033[90m[{result.iterations} turns · {result.usage['estimated_cost_usd']} USD est.]\033[0m\n"
        )


__all__ = ["ask", "chat", "AgentResult", "Usage"]
