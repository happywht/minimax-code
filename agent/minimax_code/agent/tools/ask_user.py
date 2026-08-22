"""Interactive clarification tool — the ``ask_user`` capability.

v1.1.1 — models frequently emit structured clarification questions
(same shape as the well-known AskUserQuestion tool schema) when a task
is ambiguous. Without this tool the call fails and burns an iteration
on guesswork; with it the model can pause and *ask*.

Design note: the tool itself only validates and packages the request.
The actual wait happens in :class:`AgentCore` (``_execute_tool_call``),
which intercepts the :data:`ASK_USER_MARKER` metadata, emits the
``agent.ask_user`` stream event and suspends until the user answers via
the ``agent.answer_user`` RPC (or the timeout elapses). Keeping the
wait *outside* ``Tool.run`` is deliberate: registry dispatch is wrapped
in ``asyncio.wait_for(tool_timeout)``, which would otherwise cut a
legitimately-waiting question short after ~60s.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import Tool, ToolResult, register_tool

logger = logging.getLogger(__name__)

# Metadata key AgentCore looks for on the tool result to take over the
# interactive wait. Kept in metadata (not output) so the pending marker
# never leaks into the conversation transcript if interception misses.
ASK_USER_MARKER = "ask_user"

# Schema limits mirror the AskUserQuestion contract: at most 4
# questions per call, at most 4 options per question, header chips capped
# at 12 chars.
MAX_QUESTIONS = 4
MAX_OPTIONS = 4
MAX_HEADER_CHARS = 12

# How long the agent waits for a human answer before giving up and
# telling the model nobody replied. Generous on purpose — the user may
# be reading code before deciding.
ASK_USER_TIMEOUT_S = 600.0


def _validate_questions(questions: Any) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Normalise + validate the ``questions`` argument.

    Returns ``(normalised, None)`` on success or ``(None, reason)``
    when the payload violates the contract. Normalisation drops
    unknown keys so the emitted event stays schema-stable for the
    frontend.
    """
    if not isinstance(questions, list) or not questions:
        return None, "'questions' must be a non-empty array"
    if len(questions) > MAX_QUESTIONS:
        return None, f"at most {MAX_QUESTIONS} questions per call, got {len(questions)}"

    normalised: list[dict[str, Any]] = []
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            return None, f"questions[{index}] must be an object"
        text = question.get("question")
        if not isinstance(text, str) or not text.strip():
            return None, f"questions[{index}].question must be a non-empty string"
        header = question.get("header") or text.strip()[:MAX_HEADER_CHARS]
        if not isinstance(header, str) or not header.strip():
            return None, f"questions[{index}].header must be a non-empty string"
        header = header.strip()[:MAX_HEADER_CHARS]
        options = question.get("options")
        if not isinstance(options, list) or not 2 <= len(options) <= MAX_OPTIONS:
            return None, (
                f"questions[{index}].options must contain 2..{MAX_OPTIONS} options"
            )
        clean_options: list[dict[str, str]] = []
        labels: set[str] = set()
        for option_index, option in enumerate(options):
            if not isinstance(option, dict):
                return None, f"questions[{index}].options[{option_index}] must be an object"
            label = option.get("label")
            if not isinstance(label, str) or not label.strip():
                return None, (
                    f"questions[{index}].options[{option_index}].label must be a non-empty string"
                )
            label = label.strip()
            if label in labels:
                return None, f"questions[{index}] has duplicate option label {label!r}"
            labels.add(label)
            description = option.get("description", "")
            if not isinstance(description, str):
                return None, (
                    f"questions[{index}].options[{option_index}].description must be a string"
                )
            clean_options.append({"label": label, "description": description.strip()})
        multi_select = question.get("multiSelect", question.get("multi_select", False))
        if not isinstance(multi_select, bool):
            return None, f"questions[{index}].multiSelect must be a boolean"
        normalised.append(
            {
                "question": text.strip(),
                "header": header,
                "options": clean_options,
                "multiSelect": multi_select,
            }
        )
    return normalised, None


@register_tool
class AskUserTool(Tool):
    """Ask the user structured clarifying questions mid-task."""

    name = "ask_user"
    description = (
        "Ask the user clarifying questions when the task is ambiguous and the "
        "answer changes what you build. Provide up to 4 questions, each with "
        "2-4 concrete options (label + one-line description) and an optional "
        "multiSelect flag. Use this ONLY for decisions genuinely the user's "
        "to make (requirements, trade-offs, destructive paths) — never for "
        "things you can verify from the codebase yourself. The tool suspends "
        "the turn until the user answers or a 600s timeout expires."
    )
    parameters = {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_QUESTIONS,
                "description": "1-4 clarifying questions to ask the user.",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Complete question ending with a question mark.",
                        },
                        "header": {
                            "type": "string",
                            "maxLength": MAX_HEADER_CHARS,
                            "description": "Very short label chip for the question.",
                        },
                        "options": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": MAX_OPTIONS,
                            "description": "2-4 mutually exclusive choices.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {
                                        "type": "string",
                                        "description": "Concise choice label (1-5 words).",
                                    },
                                    "description": {
                                        "type": "string",
                                        "description": (
                                            "What this choice means / its trade-off."
                                        ),
                                    },
                                },
                                "required": ["label", "description"],
                                "additionalProperties": False,
                            },
                        },
                        "multiSelect": {
                            "type": "boolean",
                            "default": False,
                            "description": "Allow multiple answers for this question.",
                        },
                    },
                    "required": ["question", "header", "options"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["questions"],
        "additionalProperties": False,
    }

    async def run(self, *, questions: Any = None, **_: Any) -> ToolResult:
        normalised, error = _validate_questions(questions)
        if error is not None:
            return ToolResult.fail(f"ask_user: {error}")
        # Pending marker only — AgentCore takes over: emits the
        # ``agent.ask_user`` event, awaits ``agent.answer_user`` and
        # replaces this result with the user's answers.
        return ToolResult.ok(
            output={"status": "pending", "questions": normalised},
            **{ASK_USER_MARKER: normalised},
        )


def format_answers(questions: list[dict[str, Any]], answers: list[Any]) -> str:
    """Render the user's answers back into text the LLM reads next turn.

    ``answers`` is position-aligned with ``questions``; each entry is a
    string (single choice) or a list of strings (multi-select / Other
    free text). Tolerant of missing entries so a partial answer still
    reads sensibly.
    """
    lines: list[str] = []
    for index, question in enumerate(questions):
        label = question.get("header") or f"Q{index + 1}"
        answer = answers[index] if index < len(answers) else None
        if isinstance(answer, list):
            picked = "; ".join(str(item) for item in answer if str(item).strip()) or "(no answer)"
        elif answer is None or str(answer).strip() == "":
            picked = "(no answer)"
        else:
            picked = str(answer)
        lines.append(f"- {label}: {picked}")
    return "The user answered:\n" + "\n".join(lines)
