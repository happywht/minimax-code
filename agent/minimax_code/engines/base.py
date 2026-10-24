"""Engine abstraction — pluggable agent runtimes behind one interface (M4).

An *engine* drives turns with its own agent loop; MiniMax Code owns the
workbench around it: sessions, runs, permissions surface, budget
accounting and the acceptance evidence pipeline. Two engines ship:

- :class:`~minimax_code.engines.native.NativeEngine` — the built-in
  Python agent loop (default; always available).
- :class:`~minimax_code.engines.codex.CodexEngine` — the Codex CLI's
  App Server over stdio JSON-RPC (available when ``codex`` is on PATH).

Design contract (planning §3.2):

- A run pins its engine at admission; engines are never switched
  mid-run and never silently fall back.
- Unavailable engines report a *reason* (``engine.status``), so the UI
  can disable them explicitly instead of pretending.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EngineInfo:
    """Static capability card for one engine."""

    id: str
    name: str
    available: bool
    unavailable_reason: str | None = None
    # What this engine can and cannot honour — the UI disables what is
    # missing instead of assuming (M4: 无法保障的能力在 UI 中明确禁用).
    capabilities: dict[str, bool] = field(default_factory=dict)
    version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "capabilities": self.capabilities,
            "version": self.version,
        }


# Capability vocabulary shared by all engines. Absent keys mean False.
CAPABILITY_KEYS = (
    "streaming",        # token-level deltas
    "resume",           # continue a prior external session
    "cancel",           # interrupt a running turn
    "approvals",        # permission prompts surfaced to the user
    "usage",            # token accounting reported back
    "steer",            # mid-turn input injection
)


__all__ = ["EngineInfo", "CAPABILITY_KEYS"]
