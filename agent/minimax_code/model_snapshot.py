"""Model routing captured synchronously when a native request is admitted."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelSnapshot:
    client: Any
    model: str
    reasoning_effort: str | None
    context_window: int
    provider_id: str | None
    protocol: str | None

    @classmethod
    def capture(cls) -> ModelSnapshot:
        from .app import get_reasoning_effort_override, get_subagent_llm
        from .models import context_window_for, default_model

        client = get_subagent_llm()
        if client is None:
            raise RuntimeError("Model runtime is not initialized")
        if isinstance(error := getattr(client, "configuration_error", None), str) and error:
            raise RuntimeError(error)
        model = str(getattr(client, "default_model", "") or default_model())
        return cls(client, model, get_reasoning_effort_override(),
                   context_window_for(model), getattr(client, "provider_id", None),
                   getattr(client, "protocol", None))

    def metadata(self) -> dict[str, Any]:
        # Explicit allowlist: never serialize the client or its credential/URL.
        return {"engine": "native", "provider_id": self.provider_id, "model": self.model,
                "protocol": self.protocol, "reasoning_effort": self.reasoning_effort,
                "context_window": self.context_window}
