"""Inline code completion — lightweight LLM call bypassing AgentCore.

The completion endpoint uses a single LLM call with a Fill-In-the-Middle
(FIM) prompt to generate code completions. It bypasses the full agent
loop (history loading, tool dispatch, permission gating) for sub-500ms
latency.

Usage::

    from .completion import complete, CompletionRequest

    resp = await complete(CompletionRequest(
        file_path="main.py",
        content_before="def hello():\\n    ",
        content_after="\\n    return",
        language="python",
    ), llm_client)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .llm import MiniMaxClient

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a code completion engine. Output ONLY the code to insert "
    "at the cursor position. No explanations, no markdown fences, "
    "no comments about what you are doing. Just the raw code."
)

_MAX_CONTENT_CHARS = 50_000  # ~12K tokens budget for prefix+suffix


@dataclass
class CompletionRequest:
    """Inbound completion request from the editor."""

    file_path: str
    content_before: str = ""   # text before cursor
    content_after: str = ""    # text after cursor (infix completion)
    language: str | None = None
    max_tokens: int = 256
    temperature: float = 0.2


@dataclass
class CompletionResponse:
    """Single completion result."""

    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0


def _build_fim_prompt(req: CompletionRequest) -> list[dict[str, str]]:
    """Build a Fill-In-the-Middle prompt from the request.

    Returns a messages list with system + user messages.
    The user message wraps the prefix/suffix in XML-style tags
    so the model knows exactly where to insert.
    """
    prefix = req.content_before[:_MAX_CONTENT_CHARS // 2]
    suffix = req.content_after[:_MAX_CONTENT_CHARS // 2]

    lang_hint = f" ({req.language})" if req.language else ""

    user_parts = [
        f"File: {req.file_path}{lang_hint}",
        "",
        "<prefix>",
        prefix,
        "</prefix>",
        "<cursor/>",
        "<suffix>",
        suffix,
        "</suffix>",
        "",
        "Complete the code at <cursor/>:",
    ]

    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


async def complete(
    req: CompletionRequest,
    llm: MiniMaxClient,
) -> CompletionResponse:
    """Build a FIM prompt and call the LLM once.

    Parameters
    ----------
    req:
        The completion request with cursor context.
    llm:
        A pre-configured :class:`MiniMaxClient` instance.

    Returns
    -------
    CompletionResponse with the generated code.
    """
    messages = _build_fim_prompt(req)
    t0 = time.monotonic()

    resp = await llm.chat(
        messages,
        tools=None,  # no tool calling for completion
        max_tokens=req.max_tokens,
        temperature=req.temperature,
    )

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    text = ""
    if resp.message and isinstance(resp.message.get("content"), str):
        text = resp.message["content"]

    return CompletionResponse(
        text=text,
        model=resp.model or llm.default_model,
        tokens_in=resp.usage.get("input_tokens", 0) if resp.usage else 0,
        tokens_out=resp.usage.get("output_tokens", 0) if resp.usage else 0,
        latency_ms=elapsed_ms,
    )
