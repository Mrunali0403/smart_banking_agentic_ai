"""Offline policy RAG: markdown loading, overlapping chunks, local embeddings, retrieval."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path

_TOKEN_PATTERN = re.compile(r"[a-z0-9₹]+")
_STOP_WORDS = {"a", "an", "and", "are", "about", "for", "from", "how", "i", "in", "is", "it", "me", "my", "of", "on", "or", "the", "to", "what", "when", "where", "which", "with"}


def format_policy_markdown(text: str) -> str:
    """Normalize retrieved policy headings and remove embedded source labels."""
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("source:"):
            continue
        heading = re.match(r"^#{1,6}\s+(.+)$", stripped)
        if heading:
            lines.append(f"**{heading.group(1).strip()}**")
        else:
            lines.append(line.rstrip())
    return "\n".join(lines).strip()


@dataclass(frozen=True)
class PolicyChunk:
    text: str
    source: str
    embedding: tuple[float, ...]


def embed_text(text: str, dimensions: int = 512) -> tuple[float, ...]:
    """Create a deterministic feature-hash embedding without a remote service."""
    vector = [0.0] * dimensions
    words = _TOKEN_PATTERN.findall(text.lower())
    features = words + [f"{left}_{right}" for left, right in zip(words, words[1:])]
    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        index = value % dimensions
        vector[index] += 1.0 if value & (1 << 63) else -1.0
    norm = math.sqrt(sum(component * component for component in vector))
    return tuple(component / norm for component in vector) if norm else tuple(vector)


def _split_text(text: str, chunk_words: int, overlap_words: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + chunk_words, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start = end - overlap_words
    return chunks


class PolicyRetriever:
    def __init__(self, policy_dir: str | Path, *, chunk_words: int = 180, overlap_words: int = 30):
        if chunk_words < 1 or overlap_words < 0 or overlap_words >= chunk_words:
            raise ValueError("Chunk overlap must be non-negative and smaller than the chunk size.")
        self.policy_dir = Path(policy_dir)
        self.chunks: list[PolicyChunk] = []
        for path in sorted(self.policy_dir.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            for piece in _split_text(text, chunk_words, overlap_words):
                self.chunks.append(PolicyChunk(piece, path.name, embed_text(piece)))

    def retrieve(self, query: str, *, limit: int = 3) -> list[dict[str, str | float]]:
        if not query.strip():
            return []
        query_vector = embed_text(query)
        query_terms = {word for word in _TOKEN_PATTERN.findall(query.lower()) if word not in _STOP_WORDS}

        def relevance(chunk: PolicyChunk) -> float:
            cosine = sum(left * right for left, right in zip(query_vector, chunk.embedding))
            chunk_terms = {word for word in _TOKEN_PATTERN.findall(chunk.text.lower()) if word not in _STOP_WORDS}
            lexical_coverage = len(query_terms & chunk_terms) / max(1, len(query_terms))
            return 0.35 * cosine + 0.65 * lexical_coverage

        ranked = sorted(
            self.chunks,
            key=relevance,
            reverse=True,
        )
        return [
            {"text": chunk.text, "source": chunk.source, "score": round(relevance(chunk), 4)}
            for chunk in ranked[: max(1, limit)]
        ]

    def context_for(self, query: str, *, limit: int = 3) -> str:
        results = self.retrieve(query, limit=limit)
        if not results:
            return "No policy documents are available for this question."
        return "\n\n".join(f"Source: {item['source']}\n{item['text']}" for item in results)