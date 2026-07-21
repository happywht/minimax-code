"""Sandbox profile configuration barrel (R236, ``xai-grok-sandbox``).

Re-exports the pure type-contract leaf migrated in R236 from grok's
``xai-grok-sandbox/src/profiles.rs``. This module opens the ``sandbox`` Python
package: OS-level execution-isolation profile types + the **global-wins** merge
policy that gates tool execution in a hostile-workspace world.

Migrated leaves:
- R236 (2026-07-22): ``profiles.py`` -- :class:`ProfileName` tagged union
  (5 built-in fieldless variants + :class:`Custom`), :class:`ProfileConfig` /
  :class:`SandboxConfig` serde-defaulted DTOs, and the two pure merge
  functions (:func:`mismatched_profile_names`, :func:`merge_project_profiles`).
  11 symbols. Mirrors ``profiles.rs`` pure-logic subset.

Deferred (runtime / kernel layer -- YAGNI until a Python sandbox backend
exists): ``load_sandbox_config`` / ``load_config_file`` (filesystem + ``toml``
+ ``tracing``), ``resolve`` / ``resolve_profile`` (``read_dir`` + path
probing), and ``to_capability_set*`` (nono ``CapabilitySet`` -- Landlock /
Seatbelt kernel primitives with no Python equivalent).
"""

from __future__ import annotations

from minimax_code.sandbox.profiles import (
    Custom,
    Devbox,
    Off,
    ProfileConfig,
    ProfileName,
    ReadOnly,
    SandboxConfig,
    Strict,
    Workspace,
    merge_project_profiles,
    mismatched_profile_names,
)

__all__ = [
    "Custom",
    "Devbox",
    "Off",
    "ProfileConfig",
    "ProfileName",
    "ReadOnly",
    "SandboxConfig",
    "Strict",
    "Workspace",
    "merge_project_profiles",
    "mismatched_profile_names",
]
