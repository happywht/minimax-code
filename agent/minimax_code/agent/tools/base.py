"""Tool abstraction layer.

Every tool the agent can call inherits from :class:`Tool` and is
registered either explicitly via :meth:`ToolRegistry.register` or
implicitly with the :func:`register_tool` decorator. The registry
exposes a stable conversion to the LLM's *function-calling* schema,
so the agent loop can hand the tool list straight to the model.

A tool's contract
-----------------

* :attr:`name`  — short, snake_case identifier shown to the LLM.
* :attr:`description` — natural-language explanation used by the
  LLM to decide when to call the tool.
* :attr:`parameters` — JSON Schema (object) describing the args.
* :meth:`run`   — async callable that executes the tool and returns
  a :class:`ToolResult`.

The :class:`ToolResult` payload is plain JSON-serializable data
(``dict`` / ``list`` / ``str`` / ``int`` / ``bool`` / ``None``) so it
can be embedded directly into the conversation transcript and
serialized to the SQLite ``messages`` table.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ToolResult:
    """Outcome of a single tool invocation.

    Attributes
    ----------
    success:
        ``True`` if the tool ran without raising. The LLM uses this
        flag to decide whether to retry, ignore, or surface the
        error verbatim to the user.
    output:
        Structured payload (JSON-serializable). The LLM gets this
        inlined into its next prompt. ``None`` is allowed.
    error:
        Short human-readable error description when ``success`` is
        ``False``. Kept short because the model has limited context.
    metadata:
        Free-form extra context (line counts, timings, file sizes
        …). Not shown to the LLM by default; logged and stored
        alongside the message in the database.
    """

    success: bool
    output: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "success": self.success,
            "output": self.output,
        }
        if self.error is not None:
            d["error"] = self.error
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    @classmethod
    def ok(cls, output: Any = None, **metadata: Any) -> ToolResult:
        return cls(success=True, output=output, metadata=dict(metadata))

    @classmethod
    def fail(cls, error: str, output: Any = None, **metadata: Any) -> ToolResult:
        return cls(success=False, output=output, error=error, metadata=dict(metadata))


# ---------------------------------------------------------------------------
# Tool base class
# ---------------------------------------------------------------------------


class Tool:
    """Abstract base class for agent-callable tools.

    Subclasses must define :attr:`name`, :attr:`description`, and
    :attr:`parameters` (a JSON Schema object) and implement the
    :meth:`run` coroutine. The class itself is intentionally
    minimal — anything else (validation, sandboxing) is the
    subclass's responsibility.
    """

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:  # pragma: no cover
        raise NotImplementedError

    def validate_args(self, args: dict[str, Any]) -> None:
        """Cheap structural check against the declared schema.

        Only checks the *shape* — type/length/range is the model's
        job. Raises :class:`ValueError` for obvious malformations.
        """
        if not isinstance(args, dict):
            raise ValueError("tool args must be a JSON object")
        required = list(self.parameters.get("required") or [])
        missing = [k for k in required if k not in args]
        if missing:
            raise ValueError(f"missing required arg(s): {missing}")
        props = self.parameters.get("properties") or {}
        unknown = [k for k in args if k not in props]
        if unknown and not self.parameters.get("additionalProperties", True):
            raise ValueError(f"unknown arg(s): {unknown}")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """In-memory tool catalogue.

    The registry is the only piece of state the agent loop touches
    to resolve a tool name to a callable. The same registry backs
    the JSON-RPC ``tool.list`` introspection endpoint (added in a
    later task) and the LLM ``tools=`` payload for function-calling.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # -- mutation ----------------------------------------------------------

    def register(self, tool: Tool) -> Tool:
        """Add ``tool`` to the registry. Returns the tool for chaining."""
        if not tool.name:
            raise ValueError(f"{tool!r} has empty name")
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name!r}")
        self._tools[tool.name] = tool
        logger.debug("registered tool %s", tool.name)
        return tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def clear(self) -> None:
        self._tools.clear()

    # -- access ------------------------------------------------------------

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name!r}") from exc

    def has(self, name: str) -> bool:
        return name in self._tools

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools.keys())

    # -- LLM schema --------------------------------------------------------

    def to_llm_functions(self) -> list[dict[str, Any]]:
        """Convert the registry to the LLM's function-calling format.

        The shape matches OpenAI's ``tools=[{type: "function",
        function: {...}}]`` payload, which the MiniMax API mirrors.
        """
        out: list[dict[str, Any]] = []
        for tool in self._tools.values():
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": dict(tool.parameters),
                    },
                }
            )
        return out

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """Alias for :meth:`to_llm_functions` (the wire format is identical)."""
        return self.to_llm_functions()

    # -- execution ---------------------------------------------------------

    async def dispatch(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Look up ``name``, validate ``args``, and call ``run``.

        The wrapper catches *all* exceptions and turns them into
        :class:`ToolResult` failures so the agent loop never has to
        worry about exception types — it just inspects
        ``result.success``.
        """
        try:
            tool = self.get(name)
        except KeyError as exc:
            return ToolResult.fail(str(exc))
        try:
            tool.validate_args(args)
        except ValueError as exc:
            return ToolResult.fail(f"invalid args for {name}: {exc}")
        try:
            parameters = list(inspect.signature(tool.run).parameters.values())
            accepts_keyword_args = any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
            uses_legacy_args_dict = (
                len(parameters) == 1
                and parameters[0].kind
                in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                and not accepts_keyword_args
            )
            if uses_legacy_args_dict:
                return await tool.run(args)  # type: ignore[arg-type]
            return await tool.run(**args)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("tool %s raised", name)
            return ToolResult.fail(f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Module-level registry + decorator
# ---------------------------------------------------------------------------


_GLOBAL_REGISTRY = ToolRegistry()


def get_default_registry() -> ToolRegistry:
    """Return the process-wide default registry.

    Tools use :func:`register_tool` to self-register; the agent
    loop calls this to obtain the populated registry at startup.
    """
    return _GLOBAL_REGISTRY


def register_tool(cls: type[Tool]) -> type[Tool]:
    """Class decorator that auto-registers a :class:`Tool` subclass.

    The decorator is the *single* point where subclasses get hooked
    into the global registry, so importing the module that defines
    a tool is enough to make it available to the agent.
    """
    instance = cls()
    _GLOBAL_REGISTRY.register(instance)
    return cls


__all__ = [
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "get_default_registry",
    "register_tool",
]
