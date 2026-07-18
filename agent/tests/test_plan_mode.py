"""Tests for the R26 plan-mode state machine.

Pins the contract ported from grok-build's ``xai-grok-shell::session::plan_mode``:

* :class:`PlanModeTracker` — the four-state lifecycle
  (Inactive → Pending → Active → ExitPending) and every transition
  method, including the mid-turn activation buffer and its rollback.
* :class:`PlanModeSnapshot` — persistence + the transient-state collapse
  rule (Pending / ExitPending → Inactive on restore) and legacy-field
  tolerance (``awaiting_plan_approval`` defaults to False).
* :func:`is_plan_file_write` / :func:`is_markdown_file_path` — the plan-file
  edit gate and the markdown-suffix recogniser (Unicode-safe).
* :class:`PromptMode` — ``from_meta_str`` parsing, read-only flag, default.
* The five reminder templates + :func:`render_reminder` — slot resolution
  and the ``plan_has_content`` conditional.

The tests mirror grok's own ``plan_mode.rs`` test suite (same scenarios,
same state sequences) so a behaviour change shows up on both sides.
"""

from __future__ import annotations

from pathlib import Path

from minimax_code.plan_mode import (
    PLAN_MODE_EDIT_REJECTED_TEMPLATE,
    PLAN_MODE_EXIT_REMINDER_TEMPLATE,
    PLAN_MODE_REENTRY_REMINDER_TEMPLATE,
    PLAN_MODE_REMINDER_FULL_TEMPLATE,
    PLAN_MODE_REMINDER_SPARSE_TEMPLATE,
    PendingActivation,
    PlanModeSnapshot,
    PlanModeState,
    PlanModeTracker,
    PromptMode,
    is_markdown_file_path,
    is_plan_file_write,
    render_reminder,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_SESSION_DIR = Path("/tmp/test-session")
_TOOL_NAMES = {
    "edit": "search_replace",
    "read": "read_file",
    "list": "list_dir",
    "search": "grep",
    "ask_user": "ask_user_question",
    "exit_plan": "exit_plan_mode",
}


def _tracker() -> PlanModeTracker:
    return PlanModeTracker.new(_SESSION_DIR)


def _render(
    template: str,
    plan_path: str = "/tmp/plan.md",
    plan_has_content: bool = True,
) -> str:
    return render_reminder(
        template,
        plan_path=plan_path,
        plan_has_content=plan_has_content,
        tool_names=_TOOL_NAMES,
    )


# ---------------------------------------------------------------------------
# Core lifecycle
# ---------------------------------------------------------------------------


def test_user_initiated_lifecycle() -> None:
    t = _tracker()
    assert t.state is PlanModeState.INACTIVE
    assert t.enter_pending()
    assert t.state is PlanModeState.PENDING
    assert t.activate()
    assert t.state is PlanModeState.ACTIVE
    assert t.deactivate_approved()
    assert t.state is PlanModeState.INACTIVE


def test_user_exit_while_turn_in_flight() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate()
    t.user_exit(turn_in_flight=True)
    assert t.state is PlanModeState.EXIT_PENDING
    t.complete_deferred_exit()
    assert t.state is PlanModeState.INACTIVE
    assert t.has_pending_exit_reminder()


def test_pending_cancel_is_clean() -> None:
    t = _tracker()
    t.enter_pending()
    t.user_exit(turn_in_flight=False)
    assert t.state is PlanModeState.INACTIVE
    assert not t.has_pending_exit_reminder()


def test_agent_initiated_skips_pending() -> None:
    t = _tracker()
    assert t.activate_from_tool()
    assert t.state is PlanModeState.ACTIVE


def test_reentry_detected() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate()
    t.deactivate_approved()
    t.enter_pending()
    assert t.is_reentry()


def test_reminder_alternation() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate()
    assert t.should_use_full_reminder()  # even count → full
    t.record_reminder_injected()
    assert not t.should_use_full_reminder()  # odd → sparse
    t.record_reminder_injected()
    assert t.should_use_full_reminder()  # even again


def test_plan_file_in_session_dir() -> None:
    t = PlanModeTracker.new(Path("/home/user/.grok/sessions/proj/abc-123"))
    assert t.plan_file_path == Path("/home/user/.grok/sessions/proj/abc-123/plan.md")


def test_compaction_resets_to_full_reminder() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate()
    t.record_reminder_injected()
    t.reset_after_compaction()
    assert t.should_use_full_reminder()


# ---------------------------------------------------------------------------
# Mid-turn activation buffer
# ---------------------------------------------------------------------------


def test_midturn_activation_buffers_and_delivers_exactly_once() -> None:
    t = _tracker()
    t.enter_pending()
    assert t.activate_mid_turn("reminder text")
    assert t.state is PlanModeState.ACTIVE
    assert t.has_pending_activation()
    assert t.should_use_full_reminder()
    assert t.take_pending_activation() == "reminder text"
    assert not t.has_pending_activation()
    t.record_reminder_injected()
    assert not t.should_use_full_reminder()
    assert t.take_pending_activation() is None
    assert t.take_pending_activation() is None


def test_midturn_activation_requires_pending() -> None:
    t = _tracker()
    assert not t.activate_mid_turn("x")  # Inactive
    t.enter_pending()
    t.activate()
    assert not t.activate_mid_turn("dup")  # already Active
    assert not t.has_pending_activation()
    t.user_exit(turn_in_flight=True)
    assert not t.activate_mid_turn("x")  # ExitPending
    assert not t.has_pending_activation()


def test_user_exit_withdraws_undelivered_activation() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate_mid_turn("reminder text")
    t.user_exit(turn_in_flight=True)
    assert t.state is PlanModeState.INACTIVE
    assert not t.has_pending_activation()
    assert not t.has_pending_exit_reminder()
    t.enter_pending()
    assert not t.is_reentry()  # rollback restored the prior flag


def test_user_exit_after_delivery_defers_exit_normally() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate_mid_turn("reminder text")
    t.take_pending_activation()
    t.record_reminder_injected()
    t.user_exit(turn_in_flight=True)
    assert t.state is PlanModeState.EXIT_PENDING


def test_withdrawal_preserves_real_reentry_flag() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate()
    t.deactivate_approved()
    t.enter_pending()
    # prior_was_previously_active is True (real prior activation); the
    # buffered mid-turn activation must not overwrite that on withdrawal.
    t.activate_mid_turn("reminder text")
    t.user_exit(turn_in_flight=True)
    t.enter_pending()
    assert t.is_reentry()


def test_compaction_drops_undelivered_activation() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate_mid_turn("reminder text")
    t.reset_after_compaction()
    assert not t.has_pending_activation()
    assert t.state is PlanModeState.ACTIVE


# ---------------------------------------------------------------------------
# No-op / boundary guards
# ---------------------------------------------------------------------------


def test_double_enter_pending_is_noop() -> None:
    t = _tracker()
    assert t.enter_pending()
    assert not t.enter_pending()
    assert t.state is PlanModeState.PENDING


def test_activate_from_inactive_only() -> None:
    t = _tracker()
    assert not t.activate()
    assert t.state is PlanModeState.INACTIVE


def test_activate_from_tool_when_already_active() -> None:
    t = _tracker()
    t.activate_from_tool()
    assert not t.activate_from_tool()
    assert t.state is PlanModeState.ACTIVE


def test_deactivate_when_not_active() -> None:
    t = _tracker()
    assert not t.deactivate_approved()
    assert t.state is PlanModeState.INACTIVE


def test_user_exit_from_inactive_is_noop() -> None:
    t = _tracker()
    t.user_exit(turn_in_flight=False)
    assert t.state is PlanModeState.INACTIVE
    assert not t.has_pending_exit_reminder()


def test_complete_deferred_exit_when_not_exit_pending_is_noop() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate()
    t.complete_deferred_exit()
    assert t.state is PlanModeState.ACTIVE
    assert not t.has_pending_exit_reminder()


def test_user_exit_while_idle_sets_exit_reminder() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate()
    t.user_exit(turn_in_flight=False)
    assert t.state is PlanModeState.INACTIVE
    assert t.has_pending_exit_reminder()
    t.clear_pending_exit_reminder()
    assert not t.has_pending_exit_reminder()


def test_enter_pending_clears_pending_exit_reminder() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate()
    t.user_exit(turn_in_flight=False)
    assert t.has_pending_exit_reminder()
    t.enter_pending()
    assert not t.has_pending_exit_reminder()


def test_activate_from_tool_clears_pending_exit_reminder() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate()
    t.user_exit(turn_in_flight=False)
    assert t.has_pending_exit_reminder()
    t.activate_from_tool()
    assert not t.has_pending_exit_reminder()


def test_deactivate_approved_does_not_set_pending_exit_reminder() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate()
    assert not t.has_pending_exit_reminder()
    t.deactivate_approved()
    assert not t.has_pending_exit_reminder()


def test_queue_exit_reminder_arms_flag() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate()
    t.deactivate_approved()
    assert not t.has_pending_exit_reminder()
    t.queue_exit_reminder()
    assert t.has_pending_exit_reminder()
    t.clear_pending_exit_reminder()
    assert not t.has_pending_exit_reminder()


def test_compaction_reset_only_when_active() -> None:
    """reset_after_compaction on a non-Active state is a no-op (smoke)."""
    t = _tracker()
    t.enter_pending()
    t.activate()
    t.record_reminder_injected()
    t.deactivate_approved()
    t.reset_after_compaction()  # Inactive: no-op, must not raise


# ---------------------------------------------------------------------------
# ExitPending re-entry + full lifecycle
# ---------------------------------------------------------------------------


def test_reenter_from_exit_pending_cancels_deferred_exit() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate()
    t.user_exit(turn_in_flight=True)
    assert t.state is PlanModeState.EXIT_PENDING
    assert t.enter_pending()
    assert t.state is PlanModeState.ACTIVE
    assert not t.has_pending_exit_reminder()
    t.complete_deferred_exit()  # no longer ExitPending → no-op
    assert t.state is PlanModeState.ACTIVE


def test_was_previously_active_persists_through_agent_exit() -> None:
    t = _tracker()
    t.activate_from_tool()
    assert t.is_active()
    t.deactivate_approved()
    assert t.state is PlanModeState.INACTIVE
    t.enter_pending()
    assert t.is_reentry()


def test_full_lifecycle_with_exit_pending() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate()
    assert t.should_use_full_reminder()
    t.record_reminder_injected()
    assert not t.should_use_full_reminder()
    t.record_reminder_injected()
    t.user_exit(turn_in_flight=True)
    assert t.state is PlanModeState.EXIT_PENDING
    t.complete_deferred_exit()
    assert t.state is PlanModeState.INACTIVE
    assert t.has_pending_exit_reminder()
    t.clear_pending_exit_reminder()
    assert not t.has_pending_exit_reminder()
    t.enter_pending()
    assert t.is_reentry()
    t.activate()
    assert t.state is PlanModeState.ACTIVE
    assert t.should_use_full_reminder()


# ---------------------------------------------------------------------------
# Snapshot — round-trip, transient collapse, legacy tolerance
# ---------------------------------------------------------------------------


def test_snapshot_round_trip_active() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate()
    t.record_reminder_injected()
    snap = t.snapshot()
    assert snap.state is PlanModeState.ACTIVE
    assert snap.was_previously_active
    assert snap.reminder_count == 1
    restored = PlanModeTracker.from_snapshot(_SESSION_DIR, snap)
    assert restored.state is PlanModeState.ACTIVE
    assert not restored.should_use_full_reminder()


def test_snapshot_pending_collapses_to_inactive() -> None:
    t = _tracker()
    t.enter_pending()
    snap = t.snapshot()
    assert snap.state is PlanModeState.PENDING
    restored = PlanModeTracker.from_snapshot(_SESSION_DIR, snap)
    assert restored.state is PlanModeState.INACTIVE


def test_snapshot_exit_pending_collapses_to_inactive_with_reminder() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate()
    t.user_exit(turn_in_flight=True)
    snap = t.snapshot()
    assert snap.state is PlanModeState.EXIT_PENDING
    restored = PlanModeTracker.from_snapshot(_SESSION_DIR, snap)
    assert restored.state is PlanModeState.INACTIVE
    assert restored.has_pending_exit_reminder()


def test_snapshot_inactive_restores_cleanly() -> None:
    t = _tracker()
    snap = t.snapshot()
    restored = PlanModeTracker.from_snapshot(_SESSION_DIR, snap)
    assert restored.state is PlanModeState.INACTIVE
    assert not restored.has_pending_exit_reminder()


def test_snapshot_to_dict_from_dict_roundtrip() -> None:
    snap = PlanModeSnapshot(
        state=PlanModeState.ACTIVE,
        was_previously_active=True,
        reminder_count=3,
        pending_exit_reminder=True,
        awaiting_plan_approval=True,
    )
    data = snap.to_dict()
    # Keys match grok's snake_case snapshot schema.
    assert set(data) == {
        "state",
        "was_previously_active",
        "reminder_count",
        "pending_exit_reminder",
        "awaiting_plan_approval",
    }
    assert data["state"] == "Active"  # PascalCase state tagging
    restored = PlanModeSnapshot.from_dict(data)
    assert restored == snap


def test_snapshot_without_awaiting_field_defaults_false() -> None:
    """A legacy snapshot missing awaiting_plan_approval degrades to False."""
    legacy = {
        "state": "Active",
        "was_previously_active": True,
        "reminder_count": 0,
        "pending_exit_reminder": False,
    }
    snap = PlanModeSnapshot.from_dict(legacy)
    assert not snap.awaiting_plan_approval


def test_snapshot_unknown_state_falls_back_to_inactive() -> None:
    snap = PlanModeSnapshot.from_dict({"state": "Bogus", "reminder_count": 0})
    assert snap.state is PlanModeState.INACTIVE


# ---------------------------------------------------------------------------
# awaiting_plan_approval
# ---------------------------------------------------------------------------


def test_awaiting_plan_approval_survives_snapshot_round_trip() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate()
    t.set_awaiting_plan_approval(True)
    assert t.is_awaiting_plan_approval()
    restored = PlanModeTracker.from_snapshot(_SESSION_DIR, t.snapshot())
    assert restored.state is PlanModeState.ACTIVE
    assert restored.is_awaiting_plan_approval()


def test_deactivate_approved_clears_awaiting_flag() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate()
    t.set_awaiting_plan_approval(True)
    t.deactivate_approved()
    assert not t.is_awaiting_plan_approval()


def test_user_exit_clears_awaiting_flag() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate()
    t.set_awaiting_plan_approval(True)
    t.user_exit(turn_in_flight=False)
    assert not t.is_awaiting_plan_approval()


# ---------------------------------------------------------------------------
# Plan-file helpers
# ---------------------------------------------------------------------------


def test_is_plan_file_write_exact_match() -> None:
    plan = Path("/home/user/.grok/sessions/proj/abc/plan.md")
    assert is_plan_file_write(plan, plan)


def test_is_plan_file_write_different_path() -> None:
    plan = Path("/home/user/.grok/sessions/proj/abc/plan.md")
    target = Path("/home/user/project/src/main.rs")
    assert not is_plan_file_write(target, plan)


def test_is_markdown_file_path_recognizes_extensions() -> None:
    assert is_markdown_file_path(Path("/x/plan.md"))
    assert is_markdown_file_path(Path("notes.MDX"))
    assert is_markdown_file_path(Path("readme.markdown"))
    assert is_markdown_file_path(Path("/a/guide.mdown"))
    assert is_markdown_file_path(Path("x.mkd"))
    assert is_markdown_file_path(Path("x.MKDN"))
    assert not is_markdown_file_path(Path("/src/lib.rs"))
    assert not is_markdown_file_path(Path("/no-extension"))
    assert not is_markdown_file_path(Path("/src/notmd.rs"))
    # Unicode names — only the suffix matters.
    assert is_markdown_file_path(Path("企业AI决策清单.md"))
    assert is_markdown_file_path(Path("计划.markdown"))
    assert not is_markdown_file_path(Path("企业AI决策清单.html"))
    assert not is_markdown_file_path(Path("md"))  # bare "md", no dot
    assert not is_markdown_file_path(Path("x"))


def test_auto_approve_edit_when_active_and_plan_file() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate()
    plan = t.plan_file_path
    assert t.should_auto_approve_edit(plan)


def test_no_auto_approve_edit_when_active_but_different_file() -> None:
    t = _tracker()
    t.enter_pending()
    t.activate()
    assert not t.should_auto_approve_edit(Path("/some/other/file.rs"))


def test_no_auto_approve_edit_when_inactive() -> None:
    t = _tracker()
    plan = t.plan_file_path
    assert not t.should_auto_approve_edit(plan)


def test_no_auto_approve_edit_when_pending() -> None:
    t = _tracker()
    t.enter_pending()
    plan = t.plan_file_path
    assert not t.should_auto_approve_edit(plan)


# ---------------------------------------------------------------------------
# PromptMode
# ---------------------------------------------------------------------------


def test_prompt_mode_from_meta_str_known_values() -> None:
    assert PromptMode.from_meta_str("ask") is PromptMode.ASK
    assert PromptMode.from_meta_str("plan") is PromptMode.PLAN
    assert PromptMode.from_meta_str("agent") is PromptMode.AGENT


def test_prompt_mode_from_meta_str_unknown_defaults_to_agent() -> None:
    assert PromptMode.from_meta_str("") is PromptMode.AGENT
    assert PromptMode.from_meta_str("unknown") is PromptMode.AGENT
    assert PromptMode.from_meta_str("ASK") is PromptMode.AGENT  # case-sensitive
    assert PromptMode.from_meta_str("Plan") is PromptMode.AGENT
    assert PromptMode.from_meta_str("code") is PromptMode.AGENT


def test_prompt_mode_is_read_only() -> None:
    assert not PromptMode.AGENT.is_read_only
    assert PromptMode.ASK.is_read_only
    assert PromptMode.PLAN.is_read_only


def test_prompt_mode_default_is_agent() -> None:
    assert PromptMode.default() is PromptMode.AGENT


def test_prompt_mode_str_values_match_grok_snake_case() -> None:
    """Values are snake_case to match grok's serde tagging."""
    assert PromptMode.AGENT.value == "agent"
    assert PromptMode.ASK.value == "ask"
    assert PromptMode.PLAN.value == "plan"


# ---------------------------------------------------------------------------
# Templates + render_reminder
# ---------------------------------------------------------------------------


def test_full_reminder_with_existing_plan() -> None:
    text = _render(
        PLAN_MODE_REMINDER_FULL_TEMPLATE,
        plan_path="/tmp/session/plan.md",
        plan_has_content=True,
    )
    assert "A plan file exists at /tmp/session/plan.md" in text
    assert "search_replace tool" in text
    assert "Plan mode is active" in text
    assert "## Plan File:" in text
    assert "only file you are allowed to edit" in text
    assert "No plan written yet" not in text
    assert "${{" not in text  # every slot resolved


def test_full_reminder_without_plan() -> None:
    text = _render(
        PLAN_MODE_REMINDER_FULL_TEMPLATE,
        plan_path="/tmp/session/plan.md",
        plan_has_content=False,
    )
    assert "No plan written yet" in text
    assert "/tmp/session/plan.md" in text
    assert "search_replace tool" in text
    assert "Plan mode is active" in text
    assert "A plan file exists at" not in text
    assert "${{" not in text


def test_full_reminder_resolves_all_tool_names() -> None:
    text = _render(PLAN_MODE_REMINDER_FULL_TEMPLATE, plan_has_content=True)
    assert "search_replace tool" in text
    assert "ask_user_question to clarify requirements" in text
    assert "exit_plan_mode to present your plan to the user" in text
    assert "${{" not in text


def test_sparse_reminder_is_static_read_only_nudge() -> None:
    text = _render(PLAN_MODE_REMINDER_SPARSE_TEMPLATE, plan_has_content=False)
    assert text == (
        "Plan mode is still active. Do not make any edits or writes to the "
        "system except for the plan file."
    )
    assert "/tmp/plan.md" not in text
    assert "${{" not in text


def test_reentry_reminder_renders() -> None:
    text = _render(PLAN_MODE_REENTRY_REMINDER_TEMPLATE, plan_has_content=False)
    assert "Returning to Plan Mode" in text
    assert "/tmp/plan.md" in text
    assert "entering plan mode again" in text
    assert "exit_plan_mode" in text
    assert "ask_user_question" in text
    assert "${{" not in text


def test_exit_reminder_renders() -> None:
    text = _render(PLAN_MODE_EXIT_REMINDER_TEMPLATE, plan_has_content=False)
    assert text == (
        "You have exited plan mode. You can now make edits, run tools, "
        "and take actions."
    )
    assert "${{" not in text


def test_edit_rejected_template_renders() -> None:
    text = _render(
        PLAN_MODE_EDIT_REJECTED_TEMPLATE,
        plan_path="/tmp/session/plan.md",
        plan_has_content=False,
    )
    assert text == (
        "Rejected: file edits are not allowed in plan mode - the only "
        "editable file is the plan file (/tmp/session/plan.md)."
    )


def test_render_reminder_unknown_tool_kind_left_intact() -> None:
    """An unknown tools.by_kind.<kind> is left visible, not silently blanked."""
    text = render_reminder(
        "use ${{ tools.by_kind.bogus }} now",
        tool_names={"edit": "search_replace"},
    )
    assert "${{ tools.by_kind.bogus }}" in text


def test_templates_have_no_hardcoded_tool_names() -> None:
    """The constants carry slots, never hardcoded client tool names."""
    hardcoded = {
        "search_replace",
        "read_file",
        "list_dir",
        "grep",
        "ask_user_question",
        "exit_plan_mode",
    }
    templates = [
        PLAN_MODE_REMINDER_FULL_TEMPLATE,
        PLAN_MODE_REMINDER_SPARSE_TEMPLATE,
        PLAN_MODE_REENTRY_REMINDER_TEMPLATE,
        PLAN_MODE_EXIT_REMINDER_TEMPLATE,
        PLAN_MODE_EDIT_REJECTED_TEMPLATE,
    ]
    for tpl in templates:
        for name in hardcoded:
            assert name not in tpl, f"template hardcodes tool name {name!r}"


# ---------------------------------------------------------------------------
# PendingActivation value object
# ---------------------------------------------------------------------------


def test_pending_activation_carries_rollback_state() -> None:
    pa = PendingActivation(text="hi", prior_was_previously_active=True)
    assert pa.text == "hi"
    assert pa.prior_was_previously_active is True
