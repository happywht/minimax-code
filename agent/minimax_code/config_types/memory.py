"""Memory-system config value types (R66).

Fusion of grok-build's ``xai-grok-config-types::memory`` — the leaf
``[memory.*]`` and ``[compaction.*]`` sub-config structs for the memory
subsystem: indexing, embedding, hybrid search scoring (with temporal decay
+ MMR diversity), injection, session-save, dream consolidation, watcher,
GC, flush, and context pruning.

Pure types + pure logic, zero I/O. Part of R66's runtime config type
contract. The aggregate ``MemoryConfig`` and its ``resolve()`` loader stay
in the shell layer (``resolve()`` depends on ``toml`` and shell-internal
flag resolution); only the leaf structs migrate here.

Forward-migrated from Rust (``#[serde(default)]`` + per-struct
``#[derive(Default)]`` + custom ``deserialize_clamped_unit`` /
``deserialize_clamped_unit_option`` clampers) to pydantic v2 field defaults
+ :meth:`default` classmethods + ``field_validator(mode="after")`` clamps.

Mapping notes
-------------

* ``deserialize_clamped_unit`` (clamp an ``f32`` to ``[0.0, 1.0]``) → a
  module-level :func:`_clamp_unit` applied via a per-field ``mode="after"``
  validator (on :attr:`MmrConfig.lambda_` and
  :attr:`MemoryFlushConfig.semantic_dedup_threshold`).
* The ``lambda`` field (Rust) is a Python keyword, so the pydantic field
  is named ``lambda_`` with wire alias ``"lambda"`` (via
  ``populate_by_name=True``); the member value stays ``0.7``.
* ``effective_half_life_days`` reproduces the Rust three-way priority:
  explicit ``temporal_decay`` → legacy ``recency_decay`` conversion →
  ``None``.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "DEFAULT_RECENCY_DECAY",
    "MemoryIndexConfig",
    "MemoryEmbeddingConfig",
    "MemorySearchConfig",
    "TemporalDecayConfig",
    "MmrConfig",
    "MemoryInitialInjectionConfig",
    "MemorySessionConfig",
    "MemoryDreamConfig",
    "MemoryWatcherConfig",
    "MemoryGcConfig",
    "MemoryFlushConfig",
    "PruningConfig",
]

#: Per-day decay factor for the **deprecated** ``recency_decay`` field.
#: ``0.95`` is also the sentinel meaning "unset" for ``effective_half_life_days``.
DEFAULT_RECENCY_DECAY: float = 0.95


def _clamp_unit(v: float) -> float:
    """Clamp a float into ``[0.0, 1.0]`` (mirrors ``deserialize_clamped_unit``)."""
    return max(0.0, min(1.0, v))


def _default_source_weights() -> dict[str, float]:
    """Default per-source-type weight multipliers (all 1.0)."""
    return {"workspace": 1.0, "session": 1.0, "global": 1.0}


# ---------------------------------------------------------------------------
# Index / embedding
# ---------------------------------------------------------------------------


class MemoryIndexConfig(BaseModel):
    """Indexing & chunking config (``[memory.index]``)."""

    model_config = ConfigDict(populate_by_name=True)

    max_chunk_chars: int = 1600
    chunk_overlap_chars: int = 320

    @classmethod
    def default(cls) -> MemoryIndexConfig:
        return cls()


class MemoryEmbeddingConfig(BaseModel):
    """Embedding provider config (``[memory.embedding]``).

    ``provider`` is ``"api"`` / ``"local"`` / ``"auto"``; ``model=None``
    disables vector embeddings.
    """

    model_config = ConfigDict(populate_by_name=True)

    provider: str = "api"
    model: str | None = None
    dimensions: int = 1024

    @classmethod
    def default(cls) -> MemoryEmbeddingConfig:
        return cls()


# ---------------------------------------------------------------------------
# Temporal decay + MMR (sub-models of search)
# ---------------------------------------------------------------------------


class TemporalDecayConfig(BaseModel):
    """Time-aware score-decay config (``[memory.search.temporal_decay]``).

    Only ``session`` chunks decay (evergreen ``global`` / ``workspace``
    sources are exempt). Decay is exponential with a half-life:

    .. code-block:: text

        decayed_score = base_score × e^(-λ × age_days)
        where λ = ln(2) / half_life_days
    """

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = True
    half_life_days: float = 7.0

    @classmethod
    def default(cls) -> TemporalDecayConfig:
        return cls()


class MmrConfig(BaseModel):
    """MMR (Maximal Marginal Relevance) diversity re-ranking config.

    Opt-in (``enabled=False`` by default). When on, results are re-ranked to
    penalise redundancy using Jaccard similarity on tokenised snippets:

    .. code-block:: text

        MMR(d) = λ × relevance(d) - (1-λ) × max_similarity(d, selected)

    ``λ`` is clamped to ``[0.0, 1.0]`` on read (mirrors
    ``deserialize_clamped_unit``). The Rust field is named ``lambda``; the
    Python attribute is ``lambda_`` (alias ``"lambda"``) because ``lambda``
    is a keyword.
    """

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = False
    lambda_: float = Field(default=0.7, alias="lambda")

    @field_validator("lambda_", mode="after")
    @classmethod
    def _clamp_lambda(cls, v: float) -> float:
        return _clamp_unit(v)

    @classmethod
    def default(cls) -> MmrConfig:
        return cls()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class MemorySearchConfig(BaseModel):
    """Hybrid search scoring config (``[memory.search]``).

    Blends vector similarity and BM25 text similarity, applies temporal
    decay and optional MMR diversity re-ranking, and weights results by
    source type.
    """

    model_config = ConfigDict(populate_by_name=True)

    max_results: int = 6
    min_score: float = 0.35
    vector_weight: float = 0.7
    text_weight: float = 0.3
    #: **Deprecated** — use ``temporal_decay``. Per-day decay factor
    #: (``0.0``–``1.0``). Ignored when ``temporal_decay.enabled``; otherwise
    #: converted to an approximate half-life.
    recency_decay: float = DEFAULT_RECENCY_DECAY
    temporal_decay: TemporalDecayConfig = Field(default_factory=TemporalDecayConfig.default)
    mmr: MmrConfig = Field(default_factory=MmrConfig.default)
    source_weights: dict[str, float] = Field(default_factory=_default_source_weights)

    @classmethod
    def default(cls) -> MemorySearchConfig:
        return cls()

    def effective_half_life_days(self) -> float | None:
        """Resolve the effective half-life (days) for temporal decay.

        Three-way priority, mirroring the Rust impl:

        1. If ``temporal_decay.enabled`` → its explicit ``half_life_days``.
        2. Else if ``recency_decay`` was set away from the default
           (:data:`DEFAULT_RECENCY_DECAY`) → the legacy conversion
           ``-1.0 / log2(recency_decay)``.
        3. Else → ``None`` (no temporal decay).
        """
        if self.temporal_decay.enabled:
            return self.temporal_decay.half_life_days
        if self.recency_decay != DEFAULT_RECENCY_DECAY:
            return -1.0 / math.log2(self.recency_decay)
        return None


# ---------------------------------------------------------------------------
# Injection / session / dream / watcher / gc
# ---------------------------------------------------------------------------


class MemoryInitialInjectionConfig(BaseModel):
    """Initial context-injection config (``[memory.injection]``)."""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = True
    min_score: float | None = None

    @classmethod
    def default(cls) -> MemoryInitialInjectionConfig:
        return cls()


class MemorySessionConfig(BaseModel):
    """Per-session memory config (``[memory.session]``)."""

    model_config = ConfigDict(populate_by_name=True)

    save_on_end: bool = True

    @classmethod
    def default(cls) -> MemorySessionConfig:
        return cls()


class MemoryDreamConfig(BaseModel):
    """Background "dream" consolidation config (``[memory.dream]``).

    Triggers a consolidation pass once enough sessions have accumulated and
    enough time has passed. ``check_interval_secs=None`` disables the
    periodic check (the dream runs only on explicit signal).
    """

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = True
    min_hours: int = 4
    min_sessions: int = 3
    stale_lock_secs: int = 3600
    check_interval_secs: int | None = None

    @classmethod
    def default(cls) -> MemoryDreamConfig:
        return cls()


class MemoryWatcherConfig(BaseModel):
    """Memory-index watcher config (``[memory.watcher]``)."""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = True
    stale_claim_secs: int = 60

    @classmethod
    def default(cls) -> MemoryWatcherConfig:
        return cls()


class MemoryGcConfig(BaseModel):
    """Memory garbage-collection config (``[memory.gc]``)."""

    model_config = ConfigDict(populate_by_name=True)

    max_age_days: int = 30

    @classmethod
    def default(cls) -> MemoryGcConfig:
        return cls()


# ---------------------------------------------------------------------------
# Flush + pruning
# ---------------------------------------------------------------------------


class MemoryFlushConfig(BaseModel):
    """Background flush config (``[memory.flush]``).

    Flushes accumulated memory writes when the soft token threshold is hit
    or after an idle period. ``semantic_dedup_threshold`` is clamped to
    ``[0.0, 1.0]`` on read (mirrors ``deserialize_clamped_unit_option``).
    """

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = True
    soft_threshold_tokens: int = 4000
    flush_model: str | None = None
    max_flush_write_chars: int = 8000
    idle_timeout_secs: int | None = None
    semantic_dedup_threshold: float | None = None

    @field_validator("semantic_dedup_threshold", mode="after")
    @classmethod
    def _clamp_semantic_dedup(cls, v: float | None) -> float | None:
        return _clamp_unit(v) if v is not None else None

    @classmethod
    def default(cls) -> MemoryFlushConfig:
        return cls()


class PruningConfig(BaseModel):
    """Context-window pruning config (``[compaction]`` / ``[pruning]``).

    Prunes old turns from the context window: a soft trim (head + tail kept,
    middle summarised) at the soft threshold, a hard clear after a turn age.
    """

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = True
    keep_last_n_turns: int = 3
    soft_trim_threshold: int = 4000
    soft_trim_head: int = 1500
    soft_trim_tail: int = 1500
    hard_clear_age_turns: int = 10

    @classmethod
    def default(cls) -> PruningConfig:
        return cls()
