"""Intra-compaction config + trigger (R28).

Ports the first deliverable slice of grok-build's ``xai-grok-compaction``
crate: the configuration types and the pure trigger-decision function. The
heavier select / sample / guard / commit passes (``compact.rs``,
``sampler.rs``, ``select.rs``, ``observer``, ``traits``) are future rounds;
this package is the language-agnostic policy + decision core that they will
build on, and the first real consumer of R27's token estimation.
"""

from __future__ import annotations

from .config import (
    DEFAULT_COMPACTION_MODEL_NAME,
    IntraCompactionConfig,
    IntraCompactionMode,
    IntraSummarizer,
)
from .trigger import IntraCompactionTrigger, should_compact

__all__ = [
    # config
    "DEFAULT_COMPACTION_MODEL_NAME",
    "IntraCompactionMode",
    "IntraSummarizer",
    "IntraCompactionConfig",
    # trigger
    "IntraCompactionTrigger",
    "should_compact",
]
