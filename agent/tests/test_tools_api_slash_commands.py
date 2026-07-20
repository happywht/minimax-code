"""Tests for tools_api.slash_commands (R190).

Mirrors grok-build's ``slash_commands.rs`` inline ``#[cfg(test)]`` suite (5
tests) plus platform structural coverage. The Rust tests are ``contains``-based
(keyword tokens), not full-string equality, so the Python suite follows suit --
this is also the right level given the Rust -> Python line-continuation
adaptation (see :mod:`minimax_code.tools_api.slash_commands` docstring).
"""

from __future__ import annotations

import minimax_code.tools_api as tools_api
from minimax_code.tools_api import slash_commands
from minimax_code.tools_api.slash_commands import (
    GOAL_COMMAND_NAME,
    GOAL_RESERVED_SUBCOMMANDS,
    IMAGE_GEN_TOOL_NAME,
    IMAGE_TO_VIDEO_TOOL_NAME,
    IMAGINE_COMMAND_NAME,
    IMAGINE_VIDEO_COMMAND_NAME,
    SCHEDULER_CREATE_TOOL_NAME,
    UPDATE_GOAL_TOOL_NAME,
    goal_instruction,
    goal_usage_message,
    imagine_instruction,
    imagine_usage_message,
    imagine_video_instruction,
    imagine_video_usage_message,
    loop_schedule_instruction,
    loop_usage_message,
)

# ---------------------------------------------------------------------------
# Structural: barrel exposes the submodule; __all__ surface; constant values.
# ---------------------------------------------------------------------------


def test_barrel_exposes_slash_commands_submodule() -> None:
    """``pub mod slash_commands`` -> accessible from the package barrel."""
    assert tools_api.slash_commands is slash_commands
    assert "slash_commands" in tools_api.__all__


def test_barrel_all_is_just_the_submodule() -> None:
    """R190 barrel exposes only slash_commands (config_validation lands R191)."""
    assert set(tools_api.__all__) == {"slash_commands"}


def test_slash_commands_all_surface_is_sixteen_symbols() -> None:
    """8 public constants + 8 functions = 16 symbols in the leaf __all__."""
    assert len(slash_commands.__all__) == 16
    assert set(slash_commands.__all__) == {
        "GOAL_COMMAND_NAME",
        "GOAL_RESERVED_SUBCOMMANDS",
        "IMAGE_GEN_TOOL_NAME",
        "IMAGE_TO_VIDEO_TOOL_NAME",
        "IMAGINE_COMMAND_NAME",
        "IMAGINE_VIDEO_COMMAND_NAME",
        "SCHEDULER_CREATE_TOOL_NAME",
        "UPDATE_GOAL_TOOL_NAME",
        "goal_instruction",
        "goal_usage_message",
        "imagine_instruction",
        "imagine_usage_message",
        "imagine_video_instruction",
        "imagine_video_usage_message",
        "loop_schedule_instruction",
        "loop_usage_message",
    }


def test_tool_name_constants_pin_canonical_values() -> None:
    """Gating code keys on these exact names -- drift would break /loop etc."""
    assert SCHEDULER_CREATE_TOOL_NAME == "scheduler_create"
    assert IMAGE_GEN_TOOL_NAME == "image_gen"
    assert IMAGE_TO_VIDEO_TOOL_NAME == "image_to_video"
    assert UPDATE_GOAL_TOOL_NAME == "update_goal"


def test_command_name_constants_pin_advertised_names() -> None:
    assert IMAGINE_COMMAND_NAME == "imagine"
    assert IMAGINE_VIDEO_COMMAND_NAME == "imagine-video"
    assert GOAL_COMMAND_NAME == "goal"


def test_goal_reserved_subcommands_preserve_order() -> None:
    """Lifecycle subcommands must match the shell's /goal grammar exactly."""
    assert GOAL_RESERVED_SUBCOMMANDS == ("status", "pause", "resume", "clear", "edit")


# ---------------------------------------------------------------------------
# Rust inline-test semantics: prompt verbatim + contract tokens, no host-side
# default interval, goal contract tokens, usage messages carry no default claim.
# ---------------------------------------------------------------------------


def test_imagine_instruction_carries_prompt_verbatim() -> None:
    text = imagine_instruction("a golden sunset")
    assert "a golden sunset" in text
    assert "image_gen" in text
    assert "verbatim" in text


def test_imagine_video_instruction_carries_prompt_and_workflow() -> None:
    text = imagine_video_instruction("a cat playing piano")
    assert "a cat playing piano" in text
    assert "image_to_video" in text
    assert "FFmpeg" in text


def test_instruction_carries_args_and_contract_tokens() -> None:
    text = loop_schedule_instruction("every 30 minutes do x")
    assert "every 30 minutes do x" in text
    assert "<number><unit>" in text
    assert "ask the user how often" in text
    assert "10m" not in text, "no host-side default interval"


def test_goal_instruction_carries_objective_and_contract_tokens() -> None:
    text = goal_instruction("ship the widget")
    assert "ship the widget" in text
    assert "update_goal(completed: true" in text
    assert "blocked_reason" in text
    assert "If update_goal returns an error" in text
    assert "system-reminder" not in text, (
        "expansions ride as user messages and must not claim reminder authority"
    )
    assert "Usage: /goal" in goal_usage_message()


def test_usage_message_has_no_default_claim() -> None:
    assert "Usage: /loop" in loop_usage_message()
    assert "10m" not in loop_usage_message()


# ---------------------------------------------------------------------------
# Return-type + interpolation sanity (platform-specific).
# ---------------------------------------------------------------------------


def test_all_builders_return_str() -> None:
    assert isinstance(loop_usage_message(), str)
    assert isinstance(loop_schedule_instruction("x"), str)
    assert isinstance(imagine_usage_message(), str)
    assert isinstance(imagine_instruction("x"), str)
    assert isinstance(imagine_video_usage_message(), str)
    assert isinstance(imagine_video_instruction("x"), str)
    assert isinstance(goal_usage_message(), str)
    assert isinstance(goal_instruction("x"), str)


def test_loop_schedule_instruction_ends_with_args_verbatim() -> None:
    """The ``## Input`` section carries the raw args as the final line."""
    text = loop_schedule_instruction("do the thing")
    assert text.endswith("do the thing")
    assert "## Input\ndo the thing" in text


def test_imagine_instruction_prompt_is_final_line() -> None:
    text = imagine_instruction("draw a fox")
    assert text.endswith("Prompt: draw a fox")
