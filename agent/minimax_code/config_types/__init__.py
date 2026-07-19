"""Runtime configuration value-type contract (R66).

Fusion of grok-build's ``xai-grok-config-types`` crate — the leaf
configuration value types for the grok CLI, extracted as a dependency-light
contract layer. This package is the **runtime config type contract**, the
symmetric counterpart to:

* R64 — extension system wire contract (hooks / plugins / MCP / marketplace).
* R65 — tool system schema vocabulary (tool / argument / type-tag).
* R66 — runtime configuration value types (the leaf ``[section]`` structs +
  the ``RemoteSettings`` proxy payload) — **this package**.

Everything here is pure types + pure logic (the only side-effects are
:func:`.flags.env_bool`, :meth:`.mcp.RelaySyncConfig.is_enabled`, and
:func:`.mcp.resolve_oauth_client_secret` reading ``os.environ``, which is
their whole purpose). No I/O, no logging, no dependency on the rest of the
agent — the crate's reason for existing in grok (dependency inversion) is
preserved here as a clean layering boundary.

Sub-modules
-----------

* :mod:`.flags`      — config-source resolution vocabulary (priority chain).
* :mod:`.permission` — permission-policy rule types (allow / deny / ask).
* :mod:`.pool`       — worktree-pool config.
* :mod:`.memory`     — memory-subsystem + pruning leaf configs.
* :mod:`.mcp`        — MCP server transport + relay-sync config.
* :mod:`.types`      — ``RemoteSettings`` + campaign / refresh / hints DTOs.
"""

from __future__ import annotations

from minimax_code.config_types.flags import (
    BoolFlag,
    ConfigSource,
    LazinessDetectorPerModelConfig,
    Resolved,
    env_bool,
    resolve_bool_flag,
)
from minimax_code.config_types.mcp import (
    McpConfig,
    McpJsonOAuthBlock,
    McpServerConfig,
    RelaySyncConfig,
    StdioTransport,
    StreamableHttpTransport,
    resolve_oauth_client_secret,
)
from minimax_code.config_types.memory import (
    DEFAULT_RECENCY_DECAY,
    MemoryDreamConfig,
    MemoryEmbeddingConfig,
    MemoryFlushConfig,
    MemoryGcConfig,
    MemoryIndexConfig,
    MemoryInitialInjectionConfig,
    MemorySearchConfig,
    MemorySessionConfig,
    MemoryWatcherConfig,
    MmrConfig,
    PruningConfig,
    TemporalDecayConfig,
)
from minimax_code.config_types.permission import (
    PatternMode,
    PermissionConfig,
    PermissionRule,
    RuleAction,
    ToolFilter,
)
from minimax_code.config_types.pool import PoolConfig
from minimax_code.config_types.types import (
    CampaignOverride,
    ContextualHintsRemote,
    DisplayRefreshSettings,
    DoomLoopRecoverySettings,
    GoalRoleModel,
    RemoteAnnouncement,
    RemoteSettings,
)

__all__ = [
    # flags
    "BoolFlag",
    "ConfigSource",
    "LazinessDetectorPerModelConfig",
    "Resolved",
    "env_bool",
    "resolve_bool_flag",
    # permission
    "PatternMode",
    "PermissionConfig",
    "PermissionRule",
    "RuleAction",
    "ToolFilter",
    # pool
    "PoolConfig",
    # memory
    "DEFAULT_RECENCY_DECAY",
    "MemoryDreamConfig",
    "MemoryEmbeddingConfig",
    "MemoryFlushConfig",
    "MemoryGcConfig",
    "MemoryIndexConfig",
    "MemoryInitialInjectionConfig",
    "MemorySearchConfig",
    "MemorySessionConfig",
    "MemoryWatcherConfig",
    "MmrConfig",
    "PruningConfig",
    "TemporalDecayConfig",
    # mcp
    "McpConfig",
    "McpJsonOAuthBlock",
    "McpServerConfig",
    "RelaySyncConfig",
    "StdioTransport",
    "StreamableHttpTransport",
    "resolve_oauth_client_secret",
    # types
    "CampaignOverride",
    "ContextualHintsRemote",
    "DisplayRefreshSettings",
    "DoomLoopRecoverySettings",
    "GoalRoleModel",
    "RemoteAnnouncement",
    "RemoteSettings",
]
