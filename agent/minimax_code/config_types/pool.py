"""Worktree-pool config value type (R66).

Fusion of grok-build's ``xai-grok-config-types::pool`` — the leaf type for
the ``[worktree_pool]`` section of config.toml. The pool pre-creates linked
git worktrees in the background so fork flows can grab a ready worktree
instead of creating one from scratch.

Pure type, zero I/O. Part of R66's runtime config type contract.

Forward-migrated from Rust (per-field ``#[serde(default = "fn")]`` +
``#[derive(Default)]``) to pydantic v2 field defaults + a :meth:`default`
classmethod that re-asserts them, mirroring the Rust ``Default`` impl.
An empty ``{}`` table therefore applies every default — verified by the
``empty_table_applies_all_field_defaults`` source-crate test.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

__all__ = ["PoolConfig"]


class PoolConfig(BaseModel):
    """Configuration for the pre-created worktree pool.

    All four fields carry their own serde default (so an empty ``{}``
    table applies every default), reproduced here as field defaults.
    :meth:`default` re-asserts them, mirroring the Rust ``Default`` impl.
    """

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = True
    pool_size: int = 2
    file_count_threshold: int = 50000
    parallelism: int = 3

    @classmethod
    def default(cls) -> PoolConfig:
        """The all-default pool config (identical to ``cls()``)."""
        return cls()
