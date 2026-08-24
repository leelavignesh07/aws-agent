from __future__ import annotations

from agent import memory


def test_facts_round_trip():
    memory.remember("nat-gateway", "prod NAT is in eu-west-1b")
    facts = memory.recall("NAT")
    assert len(facts) == 1
    assert facts[0]["value"] == "prod NAT is in eu-west-1b"


def test_remember_is_idempotent_on_key():
    memory.remember("owner", "team-a")
    memory.remember("owner", "team-b")
    facts = memory.recall("owner")
    assert len(facts) == 1 and facts[0]["value"] == "team-b"


def test_scopes_are_independent():
    memory.remember("owner", "team-a", scope="prod")
    memory.remember("owner", "team-b", scope="staging")
    assert len(memory.recall("owner")) == 2
    assert memory.recall("owner", scope="prod")[0]["value"] == "team-a"


def test_forget_removes_only_the_named_fact():
    memory.remember("a", "1")
    memory.remember("b", "2")
    assert memory.forget("a") == 1
    assert {f["key"] for f in memory.recall()} == {"b"}


def test_turns_are_returned_oldest_first():
    for i in range(5):
        memory.save_turn("s", "user", f"question {i}")
    turns = memory.load_turns("s", limit=3)
    assert [t["content"] for t in turns] == ["question 2", "question 3", "question 4"]


def test_structured_turn_content_survives_the_round_trip():
    memory.save_turn("s", "assistant", [{"type": "text", "text": "hi"}])
    assert memory.load_turns("s")[0]["content"] == [{"type": "text", "text": "hi"}]


def test_sessions_are_isolated_and_clearable():
    memory.save_turn("one", "user", "a")
    memory.save_turn("two", "user", "b")
    assert {s["session"] for s in memory.list_sessions()} == {"one", "two"}
    assert memory.clear_session("one") == 1
    assert memory.load_turns("one") == []
    assert len(memory.load_turns("two")) == 1


def test_snapshots_support_diffing():
    memory.save_snapshot("inventory", {"counts": {"ec2_instances": 2}})
    memory.save_snapshot("inventory", {"counts": {"ec2_instances": 5}})
    latest = memory.previous_snapshot("inventory", offset=0)
    earlier = memory.previous_snapshot("inventory", offset=1)
    assert latest["payload"]["counts"]["ec2_instances"] == 5
    assert earlier["payload"]["counts"]["ec2_instances"] == 2


def test_previous_snapshot_is_none_when_empty():
    assert memory.previous_snapshot("nothing-here") is None
