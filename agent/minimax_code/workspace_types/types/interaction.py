"""User-question / user-answer interaction shapes (R67).

Fusion of grok-build's ``xai-grok-workspace-types::types::interaction``.

Two adjacent-tagged payloads flow through the workspace:

* :class:`UserQuestion` (a struct) is emitted as the body of a
  ``NeedUserAnswer`` chunk.
* :class:`UserAnswer` (adjacent-tagged enum) is the client's reply.

Wire subtlety
-------------

This module hosts the crate's only ``skip_serializing_if`` field —
``UserQuestionOption.preview`` (``Option<String>``). Every other field
in the crate uses ``#[serde(default)]`` and always emits (even at the
default), but ``preview`` is **omitted** when ``None``. So both
:class:`UserQuestionOption` and :class:`UserQuestion` override
:meth:`to_wire`; :class:`UserQuestion` recurses into each option's
:meth:`to_wire` so the skip propagates.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from minimax_code.workspace_types._tagged import AdjacentTagged
from minimax_code.workspace_types._wire import WireModel

__all__ = ["UserQuestionOption", "UserQuestion", "UserAnswer"]


class UserQuestionOption(WireModel):
    """One selectable option in a :class:`UserQuestion`.

    ``preview`` carries an optional code/markdown preview shown alongside
    the option; it is omitted from the wire entirely when ``None``
    (``skip_serializing_if = "Option::is_none"``).
    """

    label: str
    description: str = ""
    preview: str | None = None

    def to_wire(self) -> dict[str, Any]:
        # Override: preview is skipped when None (skip_serializing_if).
        out: dict[str, Any] = {"label": self.label, "description": self.description}
        if self.preview is not None:
            out["preview"] = self.preview
        return out


class UserQuestion(WireModel):
    """A pending question emitted via ``NeedUserAnswer`` chunks."""

    question: str
    options: list[UserQuestionOption] = Field(default_factory=list)
    multi_select: bool = False

    def to_wire(self) -> dict[str, Any]:
        # Override: recurse into each option's to_wire so preview skip
        # propagates (model_dump would emit preview: null).
        return {
            "question": self.question,
            "options": [opt.to_wire() for opt in self.options],
            "multi_select": self.multi_select,
        }


class UserAnswer(AdjacentTagged):
    """User's reply to a :class:`UserQuestion` (adjacent-tagged).

    Wire shapes::

        {"type": "selected", "data": "<option-label>"}
        {"type": "other",     "data": "<free-form text>"}
        {"type": "multiple",  "data": ["<label>", ...]}
    """

    _VARIANTS = ("selected", "other", "multiple")

    @classmethod
    def selected(cls, value: str) -> UserAnswer:
        """Single-option pick (mirrors ``Selected(String)``)."""
        return cls("selected", value)

    @classmethod
    def other(cls, value: str) -> UserAnswer:
        """Free-form text answer (mirrors ``Other(String)``)."""
        return cls("other", value)

    @classmethod
    def multiple(cls, values: list[str]) -> UserAnswer:
        """Multi-select answer (mirrors ``Multiple(Vec<String>)``)."""
        return cls("multiple", list(values))
