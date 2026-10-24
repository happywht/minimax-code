"""NativeEngine — the built-in Python agent loop (always available)."""

from __future__ import annotations

from .base import CAPABILITY_KEYS, EngineInfo


class NativeEngine:
    """Wraps the existing in-process AgentCore pipeline.

    The run path itself is unchanged (``agent.send_message`` / the
    RunService); this class exists so ``engine.*`` can present a
    uniform capability card and so run.start can validate ``engine:
    "native"`` against a real registry instead of a literal.
    """

    id = "native"

    def info(self) -> EngineInfo:
        from .. import __version__

        return EngineInfo(
            id=self.id,
            name="Native (built-in Python agent)",
            available=True,
            capabilities={key: True for key in CAPABILITY_KEYS},
            version=__version__,
        )


__all__ = ["NativeEngine"]
