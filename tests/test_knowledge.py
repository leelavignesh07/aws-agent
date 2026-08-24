from __future__ import annotations

import pytest

from agent.knowledge import KnowledgeBase, tokenize


@pytest.fixture
def kb(tmp_path):
    (tmp_path / "runbook.md").write_text(
        "# RDS storage low\n"
        "Check FreeStorageSpace and whether autoscaling is enabled.\n"
        "# Cost spike triage\n"
        "Group Cost Explorer output by SERVICE, then by USAGE_TYPE.\n",
        encoding="utf-8",
    )
    (tmp_path / "conventions.md").write_text(
        "# Naming\nResources follow app-env-component.\n", encoding="utf-8"
    )
    return KnowledgeBase(tmp_path)


def test_chunks_split_on_headings(kb):
    titles = {c.title for c in kb.chunks}
    assert {"RDS storage low", "Cost spike triage", "Naming"} <= titles


def test_search_ranks_the_relevant_passage_first(kb):
    hits = kb.search("rds free storage space")
    assert hits and hits[0]["title"] == "RDS storage low"


def test_search_matches_a_different_topic(kb):
    hits = kb.search("cost explorer usage type")
    assert hits[0]["title"] == "Cost spike triage"


def test_unrelated_query_returns_nothing(kb):
    assert kb.search("kubernetes ingress certificate rotation") == []


def test_empty_directory_is_safe(tmp_path):
    empty = KnowledgeBase(tmp_path / "does-not-exist")
    assert empty.size == 0
    assert empty.search("anything") == []


def test_stopwords_and_short_tokens_are_dropped():
    assert tokenize("The a is of RDS storage") == ["rds", "storage"]


def test_stats_reports_the_corpus(kb):
    stats = kb.stats()
    assert stats["documents"] == 2
    assert stats["chunks"] >= 3
