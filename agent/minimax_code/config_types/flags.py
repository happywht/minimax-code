"""Configuration-source resolution leaf types (R66).

Fusion of grok-build's ``xai-grok-config-types::flags`` — the
config-source resolution vocabulary used to decide where a boolean config
flag ultimately came from, in priority order:

    requirement > cli > env > config > managed > feature-flag > default

Pure types + pure logic, zero I/O (the only side-effect is :func:`env_bool`
reading ``os.environ``, which is the whole point of the ``env`` source).

This module is part of the **runtime config type contract** (R66), the
symmetric counterpart to R64 (extension system wire DTO) and R65 (tool
schema vocabulary):

* R64 — extension system wire contract (hooks / plugins / MCP / marketplace).
* R65 — tool system schema vocabulary (tool / argument / type-tag).
* R66 — runtime configuration value types (the leaf ``[section]`` structs).

Forward-migrated from Rust (``strum serialize_all = "snake_case"`` +
``#[derive(Default)]``) to :class:`~enum.StrEnum` + :func:`dataclass`.

Cross-crate note: the original crate calls ``xai_grok_config::env_bool`` —
a helper that lives in the *parent* ``xai-grok-config`` crate, not in
``xai-grok-config-types``. It is **inlined** here as :func:`env_bool` so
this type layer stays self-contained and dependency-free (the parent crate
is itself just a thin re-export + env helper, no logic worth a module).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar

__all__ = [
    "ConfigSource",
    "Resolved",
    "env_bool",
    "resolve_bool_flag",
    "BoolFlag",
    "LazinessDetectorPerModelConfig",
]

T = TypeVar("T")


class ConfigSource(StrEnum):
    """Where a resolved config value ultimately came from.

    Wire form is snake_case (Rust ``strum serialize_all = "snake_case"``),
    which **splits CamelCase**: ``SystemManagedConfig`` serialises as
    ``"system_managed_config"``. As a :class:`~enum.StrEnum` the value
    round-trips through ``json`` unchanged and compares equal to its bare
    string (``ConfigSource.MANAGED_CONFIG == "managed_config"``).

    Ordered roughly by precedence for documentation; the actual priority
    is encoded in :func:`resolve_bool_flag`.
    """

    REQUIREMENT = "requirement"
    CLI = "cli"
    ENV = "env"
    SYSTEM_MANAGED_CONFIG = "system_managed_config"
    MANAGED_CONFIG = "managed_config"
    USER_CONFIG = "user_config"
    CONFIG = "config"
    REMOTE = "remote"
    DEFAULT = "default"


@dataclass
class Resolved(Generic[T]):
    """A value paired with the :class:`ConfigSource` it resolved from.

    Mirrors Rust ``Resolved<T>``. ``Display`` renders as ``"{value} ({source})"``
    (e.g. ``"True (cli)"``); equality is derived from both fields.
    """

    value: T
    source: ConfigSource

    def __str__(self) -> str:
        return f"{self.value} ({self.source.value})"


def env_bool(env_var: str) -> bool | None:
    """Parse a boolean environment variable (inlined from ``xai_grok_config``).

    Returns ``None`` when the variable is **unset** (so callers fall through
    to the next-priority source). When set, common truthy/falsy spellings
    are recognised case-insensitively; an unrecognised value also yields
    ``None`` (treated as "not a usable override") rather than raising —
    matching the tolerant spirit of the original helper.
    """
    if env_var not in os.environ:
        return None
    low = os.environ[env_var].strip().lower()
    if low in ("1", "true", "yes", "on"):
        return True
    if low in ("0", "false", "no", "off"):
        return False
    return None


def resolve_bool_flag(
    *,
    requirement: bool | None,
    cli: bool | None,
    env_var: str | None,
    config: bool | None,
    managed: bool | None,
    feature_flag: bool | None,
    default: bool,
) -> Resolved[bool]:
    """Resolve a boolean flag across all sources by priority.

    Pure function — the env-var lookup is delegated to :func:`env_bool`.
    Priority (highest first), mirroring Rust ``resolve_bool_flag``:

    1. ``requirement`` → :attr:`ConfigSource.REQUIREMENT`
    2. ``cli``         → :attr:`ConfigSource.CLI`
    3. ``env``         → :attr:`ConfigSource.ENV`         (only if the var
       is set to a recognised bool; otherwise falls through)
    4. ``config``      → :attr:`ConfigSource.CONFIG`
    5. ``managed``     → :attr:`ConfigSource.MANAGED_CONFIG`
    6. ``feature_flag``→ :attr:`ConfigSource.REMOTE`
    7. ``default``     → :attr:`ConfigSource.DEFAULT`
    """
    if requirement is not None:
        return Resolved(requirement, ConfigSource.REQUIREMENT)
    if cli is not None:
        return Resolved(cli, ConfigSource.CLI)
    if env_var is not None:
        env_value = env_bool(env_var)
        if env_value is not None:
            return Resolved(env_value, ConfigSource.ENV)
    if config is not None:
        return Resolved(config, ConfigSource.CONFIG)
    if managed is not None:
        return Resolved(managed, ConfigSource.MANAGED_CONFIG)
    if feature_flag is not None:
        return Resolved(feature_flag, ConfigSource.REMOTE)
    return Resolved(default, ConfigSource.DEFAULT)


@dataclass
class BoolFlag:
    """A builder that resolves a single boolean flag across all sources.

    Construct with :meth:`default` (or instantiate directly), chain the
    ``with_*`` setters (each returns ``self``), then call :meth:`resolve`.
    Fields default to ``None`` (= "this source has nothing to say"),
    except :attr:`default` which is ``False``.

    The ``with_*`` prefix matches the R65 tool-schema builder convention;
    Rust names the setters after the fields (``.requirement(true)``), but
    in Python the field name and a same-named method would collide, so
    the prefix is required.
    """

    requirement: bool | None = None
    cli: bool | None = None
    env_var: str | None = None
    config: bool | None = None
    managed: bool | None = None
    feature_flag: bool | None = None
    default: bool = False

    # -- builder chain (each returns self) ---------------------------------

    def with_requirement(self, value: bool) -> BoolFlag:
        """Set the hard requirement (CLI ``--require``)."""
        self.requirement = value
        return self

    def with_cli(self, value: bool) -> BoolFlag:
        """Set the explicit CLI flag value."""
        self.cli = value
        return self

    def with_env_var(self, env_var: str) -> BoolFlag:
        """Name the env var to read for the ``env`` source."""
        self.env_var = env_var
        return self

    def with_config(self, value: bool) -> BoolFlag:
        """Set the user/managed config-file value."""
        self.config = value
        return self

    def with_managed(self, value: bool) -> BoolFlag:
        """Set the managed-config override."""
        self.managed = value
        return self

    def with_feature_flag(self, value: bool) -> BoolFlag:
        """Set the server-side feature-flag value (resolves to REMOTE)."""
        self.feature_flag = value
        return self

    def with_default(self, value: bool) -> BoolFlag:
        """Set the static fallback (resolves to DEFAULT)."""
        self.default = value
        return self

    def resolve(self) -> Resolved[bool]:
        """Resolve the flag across all configured sources by priority."""
        return resolve_bool_flag(
            requirement=self.requirement,
            cli=self.cli,
            env_var=self.env_var,
            config=self.config,
            managed=self.managed,
            feature_flag=self.feature_flag,
            default=self.default,
        )


@dataclass
class LazinessDetectorPerModelConfig:
    """Per-model laziness-detector config (``[laziness_detector.<model>]``).

    All fields default (Rust ``#[serde(default)]`` + ``#[derive(Default)]``):
    laziness detection is off by default.
    """

    enabled: bool = False
    max_nudges_per_session: int = 0
    idle_threshold_ms: int | None = None
    min_confidence: float | None = None
    include_reasoning: bool | None = None
