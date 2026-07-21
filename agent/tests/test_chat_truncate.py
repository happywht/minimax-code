"""Tests for sampler.chat_truncate (R212, ``xai-grok-sampling-types`` ``types.rs``).

Covers the ``types.rs`` free-function leaf -- :func:`chat_truncate_for_prompt`,
the pure algorithm that counts how many leading chat messages to keep so the
slice closes after the ``(target + 1)``-th user prompt (inclusive of that
prompt's follow-up assistant / tool messages) and excludes every later user
prompt. Strategy-B fallback after the :class:`ChatCompletionRequest` container
proved blocked on three un-landed deps (``ToolDefinition`` +
``crate::rs::ResponseFormat`` + ``Box<dyn TraceContext>``).

Migration invariants tested below:

- Empty history -> ``0``.
- No user-role messages -> the full length (the truncate branch never trips).
- ``target=0`` truncates AT the second user message (``user_count=2 > 0+1``):
  the triggering user is EXCLUDED from the kept slice.
- ``target=N`` keeps through the ``(N+1)``-th user round, truncating at the
  ``(N+2)``-th user.
- A target at or beyond the user count -> the full length.
- Only ``Role::User`` advances ``user_count`` (system / assistant / tool do
  not).
- The ``chat_history`` parameter accepts any :class:`~collections.abc.Sequence`
  (``list`` or ``tuple``) -- mirrors grok's ``&[T]`` borrow.
"""

from __future__ import annotations

from minimax_code.sampler import chat_truncate_for_prompt
from minimax_code.sampler.chat_completion_leaves import Role
from minimax_code.sampler.chat_request_message import ChatRequestMessage

# ---------------------------------------------------------------------------
# Helpers: build real ChatRequestMessage instances via the R210 constructors.
# ---------------------------------------------------------------------------


def _user() -> ChatRequestMessage:
    return ChatRequestMessage.user("hi")


def _assistant() -> ChatRequestMessage:
    return ChatRequestMessage.assistant("ok", "model-1")


def _system() -> ChatRequestMessage:
    return ChatRequestMessage.system("be helpful")


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_module_barrel_exposes_one_symbol() -> None:
    """1 function = 1 re-exported symbol."""
    import minimax_code.sampler.chat_truncate as chat_truncate_module

    assert chat_truncate_module.__all__ == ["chat_truncate_for_prompt"]


def test_package_barrel_re_exports_chat_truncate_for_prompt() -> None:
    """The package barrel flattens the chat_truncate_for_prompt symbol."""
    import minimax_code.sampler as sampler

    assert "chat_truncate_for_prompt" in sampler.__all__


# ---------------------------------------------------------------------------
# Boundary cases: empty / no-user / single-user.
# ---------------------------------------------------------------------------


def test_empty_history_returns_zero() -> None:
    """An empty history keeps nothing (0 messages)."""
    assert chat_truncate_for_prompt((), 0) == 0


def test_no_user_messages_returns_full_length() -> None:
    """A history with no user-role messages keeps everything (the loop never
    triggers the truncate branch -- ``user_count`` stays 0)."""
    history = (_system(), _assistant())
    assert chat_truncate_for_prompt(history, 0) == 2


def test_single_user_target_zero_keeps_all() -> None:
    """``[user]`` target=0 -> 1 (``user_count=1``, ``1 > 0+1`` is false, so
    ``keep_count`` carries to the end)."""
    history = (_user(),)
    assert chat_truncate_for_prompt(history, 0) == 1


# ---------------------------------------------------------------------------
# Truncation: target=0 truncates AT the second user (excluded).
# ---------------------------------------------------------------------------


def test_target_zero_truncates_at_second_user() -> None:
    """``[u0, a0, u1]`` target=0 -> 2 (keep ``u0 + a0``, truncate AT ``u1``;
    the triggering second user is EXCLUDED -- ``keep_count = i = 2``)."""
    history = (_user(), _assistant(), _user())
    assert chat_truncate_for_prompt(history, 0) == 2


def test_triggering_user_is_excluded_from_kept_slice() -> None:
    """The kept slice ``history[:keep]`` excludes the user that triggered the
    truncation -- only the first user round survives a target=0 truncation."""
    history = (_user(), _assistant(), _user(), _assistant())
    keep = chat_truncate_for_prompt(history, 0)
    assert keep == 2
    kept = history[:keep]
    # Exactly one user in the kept slice (the first, at index 0); the
    # triggering second user at index 2 is excluded.
    assert [i for i, msg in enumerate(kept) if msg.role == Role.USER] == [0]


def test_leading_system_messages_are_kept() -> None:
    """``[sys, u0, a0, u1]`` target=0 -> 3 (the leading system message is
    kept; truncate AT the second user ``u1``)."""
    history = (_system(), _user(), _assistant(), _user())
    assert chat_truncate_for_prompt(history, 0) == 3


# ---------------------------------------------------------------------------
# Higher target: keep through the (N+1)-th user round.
# ---------------------------------------------------------------------------


def test_target_one_keeps_through_second_user_round() -> None:
    """``[u0, a0, u1, a1, u2]`` target=1 -> 4 (keep both user rounds
    ``u0 + a0 + u1 + a1``, truncate AT the third user ``u2`` -- ``user_count=3
    > 1+1``)."""
    history = (_user(), _assistant(), _user(), _assistant(), _user())
    assert chat_truncate_for_prompt(history, 1) == 4


def test_target_index_beyond_user_count_returns_full_length() -> None:
    """A target at or beyond the user-message count never trips the truncate
    branch (``user_count`` never exceeds ``target + 1``) -> the full history
    length."""
    history = (_user(), _assistant(), _user())
    assert chat_truncate_for_prompt(history, 99) == 3


# ---------------------------------------------------------------------------
# Role discrimination: only Role::User advances user_count.
# ---------------------------------------------------------------------------


def test_tool_messages_do_not_advance_user_count() -> None:
    """Tool-role messages are NOT counted as user prompts (only ``Role::User``
    advances ``user_count``) -- a tool round between two user prompts is kept
    in the slice."""
    # u0, tool(result of u0), u1 -- target=0 truncates at u1, keeping u0 + tool.
    history = (
        _user(),
        ChatRequestMessage.tool("call-1", "result"),
        _user(),
    )
    assert chat_truncate_for_prompt(history, 0) == 2


def test_assistant_messages_do_not_advance_user_count() -> None:
    """Assistant-role messages are NOT counted as user prompts -- a run of
    assistant messages between two user prompts is kept in the slice."""
    # u0, a, a, a, u1 -- target=0 truncates at u1, keeping u0 + 3 assistants.
    history = (
        _user(),
        _assistant(),
        _assistant(),
        _assistant(),
        _user(),
    )
    assert chat_truncate_for_prompt(history, 0) == 4


# ---------------------------------------------------------------------------
# Sequence contract: list input works the same as tuple input.
# ---------------------------------------------------------------------------


def test_accepts_list_sequence() -> None:
    """The ``chat_history`` parameter accepts any :class:`~collections.abc.
    Sequence` (``list`` or ``tuple``) -- mirrors grok's ``&[T]`` borrow
    accepting any slice."""
    history = [_user(), _assistant(), _user()]
    assert chat_truncate_for_prompt(history, 0) == 2
