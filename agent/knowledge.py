"""Local knowledge base with BM25 retrieval (STAGE 6).

Runbooks, escalation rules and account conventions live as markdown under
`knowledge/`. This module chunks them by heading and ranks chunks with BM25 —
no embedding service, no vector database, no extra dependency, and it is fast
enough for the size of corpus a platform team actually maintains.

Swap this for a managed vector store later if the corpus outgrows it; the tool
interface (`search`) stays the same.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import get_config

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.-]*")
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "how",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "then",
        "there",
        "these",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
    ]
)

# BM25 tuning: k1 controls term-frequency saturation, b the length normalisation.
_K1 = 1.5
_B = 0.75


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


@dataclass
class Chunk:
    doc: str
    title: str
    text: str
    tokens: list[str]

    def to_dict(self, score: float | None = None) -> dict[str, Any]:
        out = {"doc": self.doc, "title": self.title, "text": self.text}
        if score is not None:
            out["score"] = round(score, 3)
        return out


def _split_markdown(path: Path) -> list[Chunk]:
    """Split a markdown file into one chunk per heading section."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    chunks: list[Chunk] = []
    title = path.stem
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            chunks.append(Chunk(path.name, title, body, tokenize(f"{title}\n{body}")))

    for line in raw.splitlines():
        if line.startswith("#"):
            flush()
            buf = [line]
            title = line.lstrip("#").strip() or path.stem
        else:
            buf.append(line)
    flush()
    return chunks


class KnowledgeBase:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory or get_config().knowledge_dir)
        self.chunks: list[Chunk] = []
        self.df: Counter[str] = Counter()
        self.avg_len = 0.0
        self.load()

    def load(self) -> None:
        self.chunks = []
        if self.directory.is_dir():
            for path in sorted(self.directory.rglob("*.md")):
                self.chunks.extend(_split_markdown(path))
        self.df = Counter()
        for chunk in self.chunks:
            for term in set(chunk.tokens):
                self.df[term] += 1
        lengths = [len(c.tokens) for c in self.chunks] or [1]
        self.avg_len = sum(lengths) / len(lengths)

    @property
    def size(self) -> int:
        return len(self.chunks)

    def _score(self, chunk: Chunk, query_terms: list[str]) -> float:
        if not chunk.tokens:
            return 0.0
        tf = Counter(chunk.tokens)
        n = max(len(self.chunks), 1)
        score = 0.0
        for term in query_terms:
            freq = tf.get(term, 0)
            if not freq:
                continue
            idf = math.log(1 + (n - self.df[term] + 0.5) / (self.df[term] + 0.5))
            norm = (
                freq * (_K1 + 1) / (freq + _K1 * (1 - _B + _B * len(chunk.tokens) / max(self.avg_len, 1e-9)))
            )
            score += idf * norm
        return score

    def search(self, query: str, limit: int = 4) -> list[dict[str, Any]]:
        terms = tokenize(query)
        if not terms or not self.chunks:
            return []
        scored = [(self._score(c, terms), c) for c in self.chunks]
        scored = [(s, c) for s, c in scored if s > 0]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [c.to_dict(s) for s, c in scored[:limit]]

    def stats(self) -> dict[str, Any]:
        docs = sorted({c.doc for c in self.chunks})
        return {
            "directory": str(self.directory),
            "documents": len(docs),
            "chunks": self.size,
            "terms": len(self.df),
            "files": docs,
        }


_kb: KnowledgeBase | None = None


def get_kb(reload: bool = False) -> KnowledgeBase:
    global _kb
    if _kb is None or reload:
        _kb = KnowledgeBase()
    return _kb
