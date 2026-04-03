"""Session and semantic memory for AgentScript runtime execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    key: str
    value: str
    score: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SemanticMemoryStore(Protocol):
    """Protocol for semantic memory backends."""

    def upsert(self, key: str, value: object) -> None:
        """Store or update a memory entry."""

    def search(self, query: str, *, limit: int = 5) -> list[MemoryEntry]:
        """Search the store semantically."""


@dataclass(slots=True)
class SessionMemory:
    """Mutable session-scoped key/value memory."""

    values: dict[str, object]

    def __init__(self) -> None:
        self.values = {}

    def put(self, key: str, value: object) -> None:
        self.values[key] = value

    def get(self, key: str) -> object:
        return self.values[key]

    def snapshot(self) -> dict[str, object]:
        return dict(self.values)


class InMemorySemanticStore:
    """Deterministic semantic store using lexical similarity heuristics."""

    def __init__(self) -> None:
        self._entries: dict[str, str] = {}

    def upsert(self, key: str, value: object) -> None:
        self._entries[key] = serialize_memory_value(value)

    def search(self, query: str, *, limit: int = 5) -> list[MemoryEntry]:
        query_tokens = tokenize(query)
        ranked: list[tuple[float, str, str]] = []

        for key, value in self._entries.items():
            score = lexical_similarity(query_tokens, tokenize(value))
            if score <= 0.0:
                continue
            ranked.append((score, key, value))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [
            MemoryEntry(key=key, value=value, score=score)
            for score, key, value in ranked[:limit]
        ]


class HashEmbeddingFunction:
    """Small local embedding function suitable for offline Chroma usage."""

    def __init__(self, *, dimension: int = 64) -> None:
        self.dimension = dimension

    def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        return [self._embed(text) for text in input]

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = tokenize(text)
        if not tokens:
            return vector

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:2], "big") % self.dimension
            sign = 1.0 if digest[2] % 2 == 0 else -1.0
            vector[index] += sign

        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class ChromaSemanticMemoryStore:
    """Optional semantic store backed by ChromaDB."""

    def __init__(self, *, collection_name: str = "agentscript-memory") -> None:
        try:
            import chromadb
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "ChromaDB is not installed. Install it to use ChromaSemanticMemoryStore."
            ) from exc

        client = chromadb.Client()
        self._collection = client.get_or_create_collection(
            name=collection_name,
            embedding_function=HashEmbeddingFunction(),
        )

    def upsert(self, key: str, value: object) -> None:
        document = serialize_memory_value(value)
        self._collection.upsert(
            ids=[key],
            documents=[document],
            metadatas=[{"key": key}],
        )

    def search(self, query: str, *, limit: int = 5) -> list[MemoryEntry]:
        result = self._collection.query(query_texts=[query], n_results=limit)
        documents = result.get("documents", [[]])[0]
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]

        entries: list[MemoryEntry] = []
        for key, value, distance in zip(ids, documents, distances or []):
            score = 1.0 / (1.0 + float(distance))
            entries.append(MemoryEntry(key=key, value=value, score=score))

        if entries:
            return entries

        return [
            MemoryEntry(key=key, value=value, score=1.0)
            for key, value in zip(ids, documents)
        ]


class MemoryManager:
    """Combines session memory with a semantic search backend."""

    def __init__(self, *, semantic_store: SemanticMemoryStore | None = None) -> None:
        self.session = SessionMemory()
        self.semantic_store = semantic_store or create_default_semantic_store()

    def write(self, key: str, value: object) -> None:
        self.session.put(key, value)
        self.semantic_store.upsert(key, value)

    def search(self, query: str, *, limit: int = 5) -> list[MemoryEntry]:
        return self.semantic_store.search(query, limit=limit)

    def snapshot(self) -> dict[str, object]:
        return self.session.snapshot()


def create_default_semantic_store() -> SemanticMemoryStore:
    """Create the default semantic memory backend."""

    return InMemorySemanticStore()


def serialize_memory_value(value: object) -> str:
    """Render arbitrary runtime values into stable text for indexing."""

    if isinstance(value, MemoryEntry):
        return json.dumps(value.to_dict(), sort_keys=True)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return str(value)
    if isinstance(value, list):
        return json.dumps(
            [
                item.to_dict() if isinstance(item, MemoryEntry) else item
                for item in value
            ],
            sort_keys=True,
        )
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return str(value)


def tokenize(text: str) -> list[str]:
    """Tokenize memory text into lowercase lexical units."""

    return re.findall(r"[A-Za-z0-9_]+", text.lower())


def lexical_similarity(query_tokens: list[str], candidate_tokens: list[str]) -> float:
    """Compute a deterministic lexical similarity score."""

    if not query_tokens or not candidate_tokens:
        return 0.0

    query_counts = _counts(query_tokens)
    candidate_counts = _counts(candidate_tokens)
    dot = sum(query_counts[token] * candidate_counts.get(token, 0) for token in query_counts)
    if dot == 0:
        joined_query = " ".join(query_tokens)
        joined_candidate = " ".join(candidate_tokens)
        return 0.4 if joined_query in joined_candidate else 0.0

    query_norm = math.sqrt(sum(count * count for count in query_counts.values()))
    candidate_norm = math.sqrt(sum(count * count for count in candidate_counts.values()))
    return dot / (query_norm * candidate_norm)


def _counts(tokens: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    return counts
