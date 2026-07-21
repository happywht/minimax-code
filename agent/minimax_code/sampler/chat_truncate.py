"""Chat history truncation primitive (R212, ``xai-grok-sampling-types`` ``types.rs``).

R212 lands a free-function leaf from ``types.rs`` --
:func:`chat_truncate_for_prompt` -- a pure algorithm that, given a chat
history and a 0-based target prompt index, returns how many leading
messages to keep so that the slice closes after the ``(target + 1)``-th user
prompt (inclusive of that prompt's follow-up assistant / tool messages) and
excludes every later user prompt.

Strategy-A target (:class:`ChatCompletionRequest`) is blocked on three
un-landed dependencies (``ToolDefinition`` re-exported from
``xai-grok-tools`` + ``crate::rs::ResponseFormat`` + the
``Box<dyn TraceContext>`` tracing-crate YAGNI), so this round falls through
to Strategy B -- the same free-function leaf posture as the R211
:mod:`serde_helpers`. Zero external dependency (consumes only the R206
:class:`Role` + the R210 :class:`ChatRequestMessage`).

- :func:`chat_truncate_for_prompt` -- mirror
  ``crate::types::chat_truncate_for_prompt``: walk ``chat_history`` counting
  user-role messages; on encountering the ``(target + 2)``-th user message
  (``user_count > target_prompt_index + 1``), truncate at that index
  (``keep_count = i`` -- the triggering user is EXCLUDED); otherwise carry
  ``keep_count = i + 1`` to the end.

This module is no-I/O (a pure algorithm over an in-memory sequence).
Migration map (grok -> Python):

- ``pub fn chat_truncate_for_prompt(chat_history: &[ChatRequestMessage],
  target_prompt_index: usize) -> usize`` -> a pure
  ``def chat_truncate_for_prompt(chat_history: Sequence[ChatRequestMessage],
  target_prompt_index: int) -> int``. The grok ``&[T]`` borrow becomes the
  read-only :class:`collections.abc.Sequence` (any indexed iterable --
  ``tuple`` / ``list``); ``usize`` -> ``int`` (Python's ``int`` is unbounded,
  so the ``target_prompt_index + 1`` addition never wraps -- grok's ``usize``
  addition would wrap on overflow, a Rust-specific hazard absent here).
- ``matches!(msg.role, Role::User)`` -> ``msg.role == Role.USER`` (the R206
  :class:`Role` is a :class:`enum.StrEnum`, and the R210
  :class:`ChatRequestMessage.role` is parsed strictly to that enum, so the
  identity comparison is faithful -- both sides are the ``Role.USER``
  singleton).
- ``chat_history.iter().enumerate()`` -> :func:`enumerate` over the sequence;
  ``keep_count = i + 1`` / ``keep_count = i`` / ``break`` map verbatim.

Naming: :func:`chat_truncate_for_prompt` keeps the grok name verbatim (it is
a free function, no Anthropic Messages API peer collision; the label
documents the truncation contract precisely -- "for a target prompt index").

YAGNI: none -- the algorithm is the whole contract. No serde
``Serialize``/``Deserialize`` surface (the function operates on the already-
parsed :class:`ChatRequestMessage` instances, not on the wire ``Value``).
"""

from __future__ import annotations

from collections.abc import Sequence

from minimax_code.sampler.chat_completion_leaves import Role
from minimax_code.sampler.chat_request_message import ChatRequestMessage

__all__ = ["chat_truncate_for_prompt"]


def chat_truncate_for_prompt(
    chat_history: Sequence[ChatRequestMessage],
    target_prompt_index: int,
) -> int:
    """Mirror ``crate::types::chat_truncate_for_prompt``: count how many
    leading chat messages to keep so the slice closes after the
    ``(target_prompt_index + 1)``-th user prompt.

    Walks ``chat_history`` counting user-role messages. On encountering the
    ``(target + 2)``-th user message (``user_count > target_prompt_index + 1``)
    the slice truncates AT that index (``keep_count = i`` -- the triggering
    user message and everything after it is dropped); otherwise ``keep_count``
    carries to ``len(chat_history)``. The returned count is the number of
    messages to retain from the head of the history.

    Args:
        chat_history: the ordered message sequence (any read-only
            :class:`~collections.abc.Sequence` -- ``tuple`` / ``list``).
        target_prompt_index: 0-based index of the target user prompt (should
            be non-negative -- mirrors grok's ``usize``; a value at or beyond
            the user-message count returns the full length).

    Returns:
        The number of leading messages to keep (``0`` for an empty history).

    Examples:
        ``[u0, a0, u1, a1, u2]`` with ``target=0`` -> ``2`` (keep ``u0, a0``,
        truncate at ``u1``). With ``target=1`` -> ``4`` (keep through ``a1``,
        truncate at ``u2``). With ``target=99`` -> ``5`` (the whole history).
    """
    user_count = 0
    keep_count = 0
    for i, msg in enumerate(chat_history):
        if msg.role == Role.USER:
            user_count += 1
            # If we've seen more user messages than target + 1, stop here.
            if user_count > target_prompt_index + 1:
                keep_count = i
                break
        keep_count = i + 1
    return keep_count
