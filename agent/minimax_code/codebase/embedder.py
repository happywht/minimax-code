"""Lightweight code embedding for the codebase RAG index.

The default :class:`SimpleNgramEmbedder` is intentionally dependency-free:
it hashes character n-grams into a dense unit vector. This is enough to
exercise the vector/hybrid retrieval pipeline and provide a deterministic
offline fallback. Production deployments can swap in a sentence-transformer
or MiniMax embedding model via the :class:`CodebaseEmbedder` interface.
"""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod


class CodebaseEmbedder(ABC):
    """Abstract embedder — maps text snippets to dense unit vectors."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Vector dimension produced by :meth:`embed`."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one normalized embedding vector per input text."""


class SimpleNgramEmbedder(CodebaseEmbedder):
    """Deterministic, dependency-free n-gram hash embedder.

    The vector is built by hashing character n-grams and accumulating
    signed contributions into a fixed-size dense vector, then L2-normalizing.
    It captures token overlap better than pure random hashing while keeping
    the package lightweight.
    """

    def __init__(self, dimension: int = 128, ngram: int = 3) -> None:
        self._dimension = dimension
        self._ngram = ngram

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_normalize(self._vectorize(t)) for t in texts]

    def _vectorize(self, text: str) -> list[float]:
        vec = [0.0] * self._dimension
        normalized = _normalize_text(text)
        length = len(normalized)
        if length < self._ngram:
            # Very short inputs still get a deterministic vector.
            seed = hashlib.blake2b(normalized.encode("utf-8"), digest_size=16).digest()
            return _seed_to_vector(seed, self._dimension)

        for i in range(length - self._ngram + 1):
            gram = normalized[i : i + self._ngram]
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=16).digest()
            for j in range(self._dimension):
                # Use alternating sign based on byte parity to reduce collisions.
                byte = digest[j % len(digest)]
                sign = 1 if (byte & 1) else -1
                vec[j] += sign * (byte / 255.0)
        return vec


def _normalize_text(text: str) -> str:
    """Lowercase and collapse whitespace for stable hashing."""
    return " ".join(text.lower().split())


def _seed_to_vector(seed: bytes, dimension: int) -> list[float]:
    """Deterministically expand ``seed`` into a dense vector."""
    vec = [0.0] * dimension
    for j in range(dimension):
        byte = seed[j % len(seed)]
        sign = 1 if (byte & 1) else -1
        vec[j] = sign * (byte / 255.0)
    return vec


def _normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector; return a zero vector if its norm is zero."""
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def get_default_embedder() -> CodebaseEmbedder:
    """Return the default lightweight embedder."""
    return SimpleNgramEmbedder()


__all__ = [
    "CodebaseEmbedder",
    "SimpleNgramEmbedder",
    "get_default_embedder",
]
