"""Remote-settings + campaign/refresh value types (R66).

Fusion of grok-build's ``xai-grok-config-types::lib`` — the leaf types for
the cli-chat-proxy ``GET /v1/settings`` payload (``RemoteSettings``) and its
nested helpers: campaign overrides, doom-loop recovery, display-refresh
probe, contextual hints, goal role-models, and the inline announcement DTO.

Pure types + tolerant-parse logic, zero I/O. Part of R66's runtime config
type contract (alongside :mod:`.flags`, :mod:`.permission`, :mod:`.pool`,
:mod:`.memory`, :mod:`.mcp`).

Forward-migrated from Rust to pydantic v2. Four serde patterns compose here:

1. **Per-field ``skip_serializing_if = "Option::is_none"``** on
   :class:`DoomLoopRecoverySettings` / :class:`DisplayRefreshSettings` /
   several ``RemoteSettings`` goal fields → reproduced via hand-written
   :meth:`to_wire` (omit ``None``).
2. **``flatten`` of an unknown-key catch-all** (``CampaignOverride.patch``,
   ``DisplayRefreshSettings.extra``) → pydantic ``extra="allow"``; the catch
   -all is read back via ``model_extra``.
3. **Tolerant deserialisers** (``de_opt_bool_tolerant`` /
   ``de_opt_u32_tolerant`` / ``deserialize_tolerant_announcements`` /
   ``deserialize_tolerant_goal_role_model`` /
   ``deserialize_tolerant_goal_skeptic_models``) → ``field_validator``
   with ``mode="before"`` that swallows wrong-typed or malformed items
   into ``None`` / dropped (one bad item must never poison the whole payload).
4. **``#[serde(default)]`` on every ``RemoteSettings`` field + no
   ``deny_unknown_fields``** → pydantic field defaults + ``extra="ignore"``;
   old servers omit keys, future servers add keys, both parse cleanly.

Cross-crate notes
-----------------

* ``RemoteAnnouncement`` originates in the ``xai_grok_announcements`` crate.
  It is **inlined** here as a tolerant pydantic model (``extra="allow"``) so
  this type layer stays dependency-free; only the wire fields the proxy is
  observed to send are named, the rest fall through to ``model_extra``.
* ``tracing::warn!`` calls in the tolerant deserialisers are omitted (this is
  a pure-type module; logging belongs to the consuming layer).

Wire-fidelity caveat (RemoteSettings serialisation)
---------------------------------------------------

``RemoteSettings`` is consumed almost entirely on the **deserialisation** side
(parsing the proxy response); it is never persisted. Five goal fields carry
``skip_serializing_if`` in Rust (``goal_verifier_count``,
``goal_classifier_max_runs``, ``goal_strategist_every``,
``goal_planner_model``, ``goal_strategist_model``) plus the
``goal_skeptic_models`` vec (``skip_serializing_if = "Vec::is_empty"``); in
Rust these are **omitted** when empty, whereas pydantic's default dump emits
``null`` / ``[]``. This is a documented on-the-wire byte difference with
**identical round-trip semantics** (deserialise treats absent and ``null``
the same, because every field has ``#[serde(default)]``), so it is not
chased with a 143-field hand-written ``to_wire`` (YAGNI).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "CampaignOverride",
    "ContextualHintsRemote",
    "DisplayRefreshSettings",
    "DoomLoopRecoverySettings",
    "GoalRoleModel",
    "RemoteAnnouncement",
    "RemoteSettings",
]

#: ``u32`` upper bound (``u32::MAX``), used by the tolerant u32 parser.
_U32_MAX: int = 4294967295


# ---------------------------------------------------------------------------
# Campaign override — id gate + flattened config patch
# ---------------------------------------------------------------------------


class CampaignOverride(BaseModel):
    """A ``campaigns[]`` entry: an ``id`` gate plus a full-power config patch.

    Wire shape (grok serde): ``id`` accepts alias ``campaign_id``; everything
    else is flattened into ``patch`` (an arbitrary JSON object — the JSON
    sibling of a ``[[campaigns]]`` TOML override). The patch is captured via
    pydantic ``extra="allow"`` and read back through :attr:`patch`.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str | None = Field(default=None, alias="campaign_id")

    @property
    def patch(self) -> dict[str, Any]:
        """The flattened config patch (every non-``id`` wire key)."""
        return dict(self.model_extra or {})

    def to_wire(self) -> dict[str, Any]:
        """Emit ``campaign_id`` (when set) + the patch keys flattened."""
        out: dict[str, Any] = dict(self.model_extra or {})
        if self.id is not None:
            out["campaign_id"] = self.id
        return out


# ---------------------------------------------------------------------------
# Doom-loop recovery — local TOML + remote object share ONE struct
# ---------------------------------------------------------------------------


class DoomLoopRecoverySettings(BaseModel):
    """Doom-loop recovery knobs (``[doom_loop_recovery]`` / remote object).

    All fields ``Option`` with ``skip_serializing_if = "Option::is_none"``:
    a partial object never fails the parse, and unset fields fall through
    per-field in the resolver (env > TOML > remote > default).
    """

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool | None = None
    max_threshold: int | None = None
    max_retries: int | None = None

    def to_wire(self) -> dict[str, Any]:
        """Emit only the set fields (mirrors ``skip_serializing_if``)."""
        out: dict[str, Any] = {}
        if self.enabled is not None:
            out["enabled"] = self.enabled
        if self.max_threshold is not None:
            out["max_threshold"] = self.max_threshold
        if self.max_retries is not None:
            out["max_retries"] = self.max_retries
        return out


# ---------------------------------------------------------------------------
# Display-refresh probe + auto-cadence — tolerant, extra-preserving
# ---------------------------------------------------------------------------


def _tolerant_bool(v: Any) -> bool | None:
    """``de_opt_bool_tolerant``: a real bool passes; anything else → ``None``.

    Mirrors the Rust visitor: only ``visit_bool`` yields ``Some(v)``; null /
    string / number / wrong type all collapse to ``None`` (a wrong-typed knob
    is silently ignored rather than failing the whole parse).
    """
    if isinstance(v, bool):
        return v
    return None


def _tolerant_u32(v: Any) -> int | None:
    """``de_opt_u32_tolerant``: an in-range integer passes; else ``None``.

    Mirrors the Rust visitor: ``u64`` / ``i64`` arms run ``u32::try_from``
    (negative or ``> u32::MAX`` → ``None``); every other arm → ``None``.
    ``bool`` is an ``int`` subclass in Python but is **not** a valid u32 on
    the wire, so it is excluded explicitly.
    """
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if 0 <= v <= _U32_MAX else None
    return None


class DisplayRefreshSettings(BaseModel):
    """Display-refresh probe + auto-cadence settings.

    Field-wise tolerant deserialise (wrong types → ``None``); unknown keys
    are kept in ``model_extra`` (via ``extra="allow"``) so a settings rewrite
    can never drop future knobs. One struct serves local ``[ui.display_refresh]``,
    the remote ``display_refresh`` object, and ``UiConfig``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    probe_enabled: bool | None = None
    auto_cadence_enabled: bool | None = None
    floor_ms: int | None = None
    ceiling_ms: int | None = None
    min_hz: int | None = None
    max_hz: int | None = None

    @field_validator("probe_enabled", "auto_cadence_enabled", mode="before")
    @classmethod
    def _tol_bool(cls, v: Any) -> bool | None:
        return _tolerant_bool(v)

    @field_validator("floor_ms", "ceiling_ms", "min_hz", "max_hz", mode="before")
    @classmethod
    def _tol_u32(cls, v: Any) -> int | None:
        return _tolerant_u32(v)

    def is_default(self) -> bool:
        """True when no field is set and no extra knob is present."""
        return (
            self.probe_enabled is None
            and self.auto_cadence_enabled is None
            and self.floor_ms is None
            and self.ceiling_ms is None
            and self.min_hz is None
            and self.max_hz is None
            and not self.model_extra
        )

    def to_wire(self) -> dict[str, Any]:
        """Emit the six knobs (when set) + preserved extra keys."""
        out: dict[str, Any] = dict(self.model_extra or {})
        if self.probe_enabled is not None:
            out["probe_enabled"] = self.probe_enabled
        if self.auto_cadence_enabled is not None:
            out["auto_cadence_enabled"] = self.auto_cadence_enabled
        if self.floor_ms is not None:
            out["floor_ms"] = self.floor_ms
        if self.ceiling_ms is not None:
            out["ceiling_ms"] = self.ceiling_ms
        if self.min_hz is not None:
            out["min_hz"] = self.min_hz
        if self.max_hz is not None:
            out["max_hz"] = self.max_hz
        return out


# ---------------------------------------------------------------------------
# Contextual hints remote tier
# ---------------------------------------------------------------------------


class ContextualHintsRemote(BaseModel):
    """Remote enable tier for the per-tip contextual hints.

    Each field is a soft default for one tip: ``Some(False)`` disables,
    ``Some(True)`` enables, absent / null defers to the client default (on).
    """

    model_config = ConfigDict(populate_by_name=True)

    undo: bool | None = None
    plan_mode: bool | None = None
    image_input: bool | None = None
    send_now: bool | None = None
    small_screen: bool | None = None
    word_select: bool | None = None


# ---------------------------------------------------------------------------
# Goal role model — model + harness pair
# ---------------------------------------------------------------------------


class GoalRoleModel(BaseModel):
    """A model id + the harness whose system-prompt/toolset flavor it runs.

    The pair is the atomic configurable unit because a model is only
    guaranteed to work with a compatible harness. Both fields are required
    (no ``#[serde(default)]`` in Rust), so a present-but-malformed value is
    dropped to ``None`` by the tolerant deserialiser rather than parsed here.
    """

    model_config = ConfigDict(populate_by_name=True)

    model: str
    agent_type: str


# ---------------------------------------------------------------------------
# Remote announcement — inlined, tolerant (cross-crate)
# ---------------------------------------------------------------------------


class RemoteAnnouncement(BaseModel):
    """One remote announcement (inlined from ``xai_grok_announcements``).

    Tolerant shape: every field is ``Option`` and ``extra="allow"`` so unknown
    / future announcement members round-trip through ``model_extra`` without
    dropping. ``cta`` is typed ``Any`` because it is commonly a nested
    ``{label, url}`` object on the wire.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str | None = None
    message: str | None = None
    severity: str | None = None
    title: str | None = None
    cta: Any | None = None
    updated_at: str | None = None
    expires_at: str | None = None
    dismissible: bool | None = None
    persistent: bool | None = None


# ---------------------------------------------------------------------------
# Tolerant parsers shared by the goal role-model fields
# ---------------------------------------------------------------------------


def _parse_goal_role_model_tolerant(value: Any) -> GoalRoleModel | None:
    """Parse one value as :class:`GoalRoleModel`, dropping malformed → ``None``.

    Mirrors ``parse_goal_role_model_tolerant``: a malformed value becomes
    ``None`` (with a ``tracing::warn!`` in Rust, omitted here) instead of
    erroring, so one bad remote payload cannot nuke the whole
    ``RemoteSettings`` parse.
    """
    if not isinstance(value, dict):
        return None
    try:
        return GoalRoleModel.model_validate(value)
    except Exception:  # noqa: BLE001 — tolerant parse: any failure → drop
        return None


# ---------------------------------------------------------------------------
# Remote settings — the big proxy payload
# ---------------------------------------------------------------------------


class RemoteSettings(BaseModel):
    """Remote settings fetched from cli-chat-proxy ``GET /v1/settings``.

    Every field is ``Option`` with ``#[serde(default)]`` so missing fields
    from old servers are ignored, new fields don't break existing clients,
    and callers can distinguish "server said false" from "server didn't say".

    Field order mirrors the Rust source for ease of cross-checking. Unknown
    keys are ignored (Rust has no ``deny_unknown_fields``); the two ``Vec``
    fields (``campaigns``, ``goal_skeptic_models``) default to empty.

    The three tolerant goal/announcement fields swallow malformed items
    rather than failing the whole parse (see the module docstring).
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    # -- leader / upload / shell ------------------------------------------
    leader_mode: bool | None = None
    max_upload_file_bytes: int | None = None
    max_upload_untracked_bytes: int | None = None
    non_git_workspace_capture: bool | None = None
    persistent_local_shell: bool | None = None
    release_channel: str | None = None
    loc_tracking: bool | None = None

    # -- memory subsystem -------------------------------------------------
    memory_enabled: bool | None = None
    memory_search_max_results: int | None = None
    memory_search_min_score: float | None = None
    memory_initial_injection_enabled: bool | None = None
    memory_initial_injection_min_score: float | None = None
    memory_embedding_model: str | None = None
    memory_embedding_dimensions: int | None = None
    pruning_enabled: bool | None = None
    pruning_keep_last_n_turns: int | None = None
    pruning_soft_trim_threshold: int | None = None
    flush_enabled: bool | None = None
    flush_soft_threshold_tokens: int | None = None
    flush_idle_timeout_secs: int | None = None
    flush_semantic_dedup_threshold: float | None = None
    memory_temporal_decay_enabled: bool | None = None
    memory_temporal_decay_half_life_days: float | None = None
    memory_mmr_enabled: bool | None = None
    memory_mmr_lambda: float | None = None
    memory_watcher_enabled: bool | None = None
    dream_enabled: bool | None = None
    dream_min_hours: int | None = None
    dream_min_sessions: int | None = None
    dream_check_interval_secs: int | None = None
    subscription_watch_interval_secs: int | None = None
    writeback_enabled: bool | None = None

    # -- auth / oauth -----------------------------------------------------
    oauth2_issuer: str | None = None
    oauth2_client_id: str | None = None
    grok_oauth_enabled: bool | None = None

    # -- tools / mcp / lsp -----------------------------------------------
    lsp_tools_enabled: bool | None = None
    folder_trust_enabled: bool | None = None
    write_file_enabled: bool | None = None
    file_toolset: str | None = None
    inference_idle_timeout_secs: int | None = None
    mcp_startup_timeout_secs: int | None = None
    max_mcp_output_bytes: int | None = None
    session_registry_enabled: bool | None = None

    # -- doom loop / todo gate -------------------------------------------
    doom_loop_recovery: DoomLoopRecoverySettings | None = None
    todo_gate_enabled: bool | None = None
    todo_gate_max_fires_per_prompt: int | None = None

    # -- auto-wake + vendor-session sync ---------------------------------
    auto_wake_enabled: bool | None = None
    cursor_skills_enabled: bool | None = None
    cursor_rules_enabled: bool | None = None
    cursor_agents_enabled: bool | None = None
    claude_skills_enabled: bool | None = None
    claude_rules_enabled: bool | None = None
    claude_agents_enabled: bool | None = None
    cursor_mcps_enabled: bool | None = None
    cursor_hooks_enabled: bool | None = None
    claude_mcps_enabled: bool | None = None
    claude_hooks_enabled: bool | None = None
    cursor_sessions_enabled: bool | None = None
    claude_sessions_enabled: bool | None = None
    codex_sessions_enabled: bool | None = None

    # -- goal subsystem ---------------------------------------------------
    goal_enabled: bool | None = None
    goal_classifier_enabled: bool | None = None
    goal_planner_enabled: bool | None = None
    goal_summary_enabled: bool | None = None
    goal_verifier_count: int | None = None
    goal_classifier_max_runs: int | None = None
    goal_strategist_every: int | None = None
    goal_planner_model: GoalRoleModel | None = None
    goal_strategist_model: GoalRoleModel | None = None
    goal_skeptic_models: list[GoalRoleModel] = Field(default_factory=list)

    # -- managed mcp / telemetry -----------------------------------------
    managed_mcps_enabled: bool | None = None
    managed_mcp_gateway_tools_enabled: bool | None = None
    external_otel_disabled: bool | None = None
    external_otel_content_gates_locked: bool | None = None
    telemetry_enabled: bool | None = None
    telemetry_mode: str | None = None
    trace_upload_enabled: bool | None = None
    feedback_enabled: bool | None = None

    # -- compaction / tips / warnings ------------------------------------
    two_pass_compaction_enabled: bool | None = None
    tips: list[str] | None = None
    non_git_warning: bool | None = None
    official_marketplace_auto_register: bool | None = None
    plugin_cta: bool | None = None
    announcements: list[RemoteAnnouncement] | None = None

    # -- model pins -------------------------------------------------------
    web_search_model: str | None = None
    session_summary_model: str | None = None
    image_description_model: str | None = None
    prompt_suggestion_model: str | None = None
    default_model: str | None = None

    # -- campaigns + bash / ask-user / subagent --------------------------
    campaigns: list[CampaignOverride] = Field(default_factory=list)
    auto_background_on_timeout: bool | None = None
    allow_background_operator: bool | None = None
    ask_user_question_timeout_enabled: bool | None = None
    ask_user_question_timeout_secs: int | None = None
    subagent_worktree_snapshot_enabled: bool | None = None

    # -- media tools ------------------------------------------------------
    image_gen_enabled: bool | None = None
    image_gen_model_override: str | None = None
    video_gen_enabled: bool | None = None
    image_normalize_cache_enabled: bool | None = None
    path_not_found_hints: bool | None = None

    # -- UI / worktree / session behaviour --------------------------------
    contextual_hints: ContextualHintsRemote | None = None
    worktree_type: str | None = None
    restore_code: bool | None = None
    cancel_rewind_enabled: bool | None = None
    session_recap: bool | None = None
    ask_user_question_enabled: bool | None = None
    web_fetch_enabled: bool | None = None
    web_fetch_proxy: str | None = None
    web_fetch_allowed_domains: list[str] | None = None
    show_resolved_model: bool | None = None
    sharing_enabled: bool | None = None
    voice_mode_enabled: bool | None = None

    # -- access / subscription gate --------------------------------------
    zdr_access_enabled: bool | None = None
    remember_tool_approvals: bool | None = None
    crash_handler_enabled: bool | None = None

    # -- TUI scrollback ---------------------------------------------------
    show_thinking_blocks: bool | None = None
    group_tool_verbs: bool | None = None
    collapsed_edit_blocks: bool | None = None
    display_refresh: DisplayRefreshSettings | None = None

    # -- permission / billing --------------------------------------------
    auto_mode: Any | None = None
    permission_mode: str | None = None
    subscription_tier: str | None = None
    gate_message: str | None = None
    gate_url: str | None = None
    gate_label: str | None = None
    session_picker_grouped: bool | None = None
    allow_access: bool | None = None
    subscription_tier_display: str | None = None
    on_demand_enabled: bool | None = None
    usage_billing_redirect_url: str | None = None

    # -- suggestions / compaction knobs ----------------------------------
    suggestions_enabled: bool | None = None
    suggestions_ai_enabled: bool | None = None
    auto_compact_threshold_percent: int | None = None
    system_prompt_label: str | None = None
    compaction_wall_clock_budget_secs: int | None = None
    compaction_mode: str | None = None
    compaction_detail: str | None = None
    compaction_verbatim_input: bool | None = None

    # -- imagine denylist / workspace / jemalloc -------------------------
    imagine_tools_disabled: list[str] | None = None
    workspace_command_enabled: bool | None = None
    jemalloc_heap_profile_enabled: bool | None = None
    jemalloc_heap_profile_thresholds_bytes: list[int] | None = None
    jemalloc_heap_profile_poll_interval_secs: int | None = None

    # -- tolerant deserialisers (mirrors Rust deserialize_with) ----------

    @field_validator("announcements", mode="before")
    @classmethod
    def _tolerant_announcements(cls, v: Any) -> list[RemoteAnnouncement] | None:
        """``deserialize_tolerant_announcements``: null / non-array → ``None``;
        array → each valid dict parsed, malformed items dropped."""
        if v is None:
            return None
        if not isinstance(v, list):
            return None
        out: list[RemoteAnnouncement] = []
        for item in v:
            if not isinstance(item, dict):
                continue
            try:
                out.append(RemoteAnnouncement.model_validate(item))
            except Exception:  # noqa: BLE001 — tolerant: drop malformed item
                continue
        return out

    @field_validator("goal_planner_model", "goal_strategist_model", mode="before")
    @classmethod
    def _tolerant_goal_role_model(cls, v: Any) -> GoalRoleModel | None:
        """``deserialize_tolerant_goal_role_model``: null / malformed → ``None``."""
        return _parse_goal_role_model_tolerant(v)

    @field_validator("goal_skeptic_models", mode="before")
    @classmethod
    def _tolerant_goal_skeptic_models(cls, v: Any) -> list[GoalRoleModel]:
        """``deserialize_tolerant_goal_skeptic_models``: non-array → ``[]``;
        array → each valid dict parsed, malformed entries dropped (order kept)."""
        if not isinstance(v, list):
            return []
        out: list[GoalRoleModel] = []
        for item in v:
            parsed = _parse_goal_role_model_tolerant(item)
            if parsed is not None:
                out.append(parsed)
        return out

    # -- imagine denylist helper -----------------------------------------

    def imagine_tool_disabled(self, tool: str) -> bool:
        """True when the server denylist contains ``tool`` (force-off).

        Mirrors ``RemoteSettings::imagine_tool_disabled``: absent / unlisted →
        ``False`` (defer to the tool's own default); present and listed →
        ``True`` (authoritative removal; local env/config cannot re-enable).
        """
        return self.imagine_tools_disabled is not None and tool in self.imagine_tools_disabled
