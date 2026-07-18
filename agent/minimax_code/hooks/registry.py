"""Hook registry — loads JSON hook specs and indexes them by event.

Accepts the Claude-Code ``settings.json`` shape::

    {
      "hooks": {
        "pre_tool_use": [
          {"command": ["./lint.sh"], "matcher": {"tool_name": "edit_*"}, "timeout": 5}
        ],
        "session_start": [
          {"command": ["./welcome.sh"]}
        ]
      }
    }

The top-level ``"hooks"`` key is optional — a bare ``{<event>: [...]}``
mapping is also accepted.
"""

from __future__ import annotations

import fnmatch
import json
import logging
from collections import defaultdict
from pathlib import Path

from .types import TOOL_EVENTS, HookConfig, HookEvent, HookMatcher

logger = logging.getLogger(__name__)


class HookRegistry:
    """In-memory index of hook specs keyed by event."""

    def __init__(self) -> None:
        self._hooks: dict[HookEvent, list[HookConfig]] = defaultdict(list)

    # -- mutation ----------------------------------------------------------

    def add(self, hook: HookConfig) -> HookConfig:
        """Register one hook. Tool events default to match-all."""
        if hook.event in TOOL_EVENTS and hook.matcher is None:
            hook.matcher = HookMatcher()
        self._hooks[hook.event].append(hook)
        return hook

    def load_dict(self, data: dict) -> int:
        """Load a hooks mapping. Returns the number of hooks added."""
        raw = data.get("hooks", data)
        count = 0
        for event_name, specs in raw.items():
            try:
                event = HookEvent(event_name)
            except ValueError:
                logger.warning("unknown hook event %r, skipping", event_name)
                continue
            if not isinstance(specs, list):
                logger.warning("hooks for %s must be a list, skipping", event_name)
                continue
            for spec in specs:
                if not isinstance(spec, dict):
                    continue
                try:
                    hook = HookConfig.model_validate({**spec, "event": event.value})
                except Exception as exc:  # noqa: BLE001 — fail-open per spec
                    logger.warning("invalid hook spec %r: %s", spec, exc)
                    continue
                self.add(hook)
                count += 1
        return count

    def load_file(self, path: str | Path) -> int:
        """Load hooks from a JSON file. Missing file → 0 (fail-open)."""
        p = Path(path)
        if not p.exists():
            return 0
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("could not load hooks file %s: %s", p, exc)
            return 0
        if not isinstance(data, dict):
            return 0
        return self.load_dict(data)

    def clear(self) -> None:
        self._hooks.clear()

    # -- queries -----------------------------------------------------------

    def for_event(
        self, event: HookEvent, tool_name: str | None = None
    ) -> list[HookConfig]:
        """Hooks for ``event``, filtered by ``tool_name`` glob when applicable."""
        out: list[HookConfig] = []
        for hook in self._hooks.get(event, []):
            if event in TOOL_EVENTS and tool_name is not None:
                m = hook.matcher
                if m is not None and m.tool_name is not None:
                    patterns = m.tool_name if isinstance(m.tool_name, list) else [m.tool_name]
                    if not any(fnmatch.fnmatch(tool_name, pat) for pat in patterns):
                        continue
            out.append(hook)
        return out

    def all(self) -> list[HookConfig]:
        return [h for hooks in self._hooks.values() for h in hooks]

    def count(self, event: HookEvent | None = None) -> int:
        if event is None:
            return sum(len(v) for v in self._hooks.values())
        return len(self._hooks.get(event, []))


__all__ = ["HookRegistry"]
