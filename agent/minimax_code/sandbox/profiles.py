"""Pure type contract layer for sandbox profiles (R236, ``xai-grok-sandbox`` ``profiles.rs``).

Migrates the pure-logic subset of grok's OS-level sandbox profile configuration:

- :class:`ProfileName` -- the tagged union selecting a sandbox policy. Five
  built-in variants (``Workspace`` / ``Devbox`` / ``ReadOnly`` / ``Strict`` /
  ``Off``) are fieldless subclasses; ``Custom`` carries the profile name to
  look up in :class:`SandboxConfig`. Mirrors grok ``enum ProfileName`` with
  ``#[default] = Workspace``, plus ``Display`` (``__str__``) / ``FromStr``
  (``from_str``) and the two network-restriction predicates
  (``restricts_network`` / ``restricts_network_resolved``).
- :class:`ProfileConfig` -- a single custom profile's serde-defaulted
  optional fields (``extends`` / ``restrict_network`` / ``read_only`` /
  ``read_write`` / ``deny``), the TOML ``[profiles.<name>]`` row.
- :class:`SandboxConfig` -- the ``profiles: HashMap<String, ProfileConfig>``
  aggregate loaded from ``~/.grok/sandbox.toml`` + ``.grok/sandbox.toml``.
- :func:`mismatched_profile_names` + :func:`merge_project_profiles` -- the two
  pure config-merge functions encoding the **global-wins** policy: a project
  config may ADD custom profile names but cannot redefine one already present
  globally (last-write-wins would let a malicious workspace hollow out a
  trusted user/enterprise profile's ``deny`` while keeping its name).

Deliberately out of scope (YAGNI -- runtime / kernel layer): ``load_sandbox_config``
/ ``load_config_file`` (filesystem + ``toml`` + ``tracing``), ``resolve`` /
``resolve_profile`` (``read_dir`` + ``cfg!(unix)`` path probing), and
``to_capability_set*`` (nono ``CapabilitySet`` -- Landlock/Seatbelt kernel
primitives with no Python equivalent). Those stay on the Rust side until a
Python sandbox backend exists; this leaf captures the type contract + merge
policy a Python caller (tool-execution gating, config validation) needs
without pulling in ``toml`` / ``tracing`` / kernel primitives.

Mirrors grok's ``profiles.rs`` pure-logic tests (``parse_profile_names`` /
``display_roundtrip`` / ``display_custom`` / ``network_restriction`` /
``mismatched_profile_names_reports_only_changed_custom_profiles`` /
``project_cannot_redefine_global_profile``). The TOML-parsing test
(``parse_toml_config``) is replaced by direct :class:`ProfileConfig`
construction here -- the ``toml`` runtime stays deferred.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProfileName:
    """Tagged union base for sandbox profile selection.

    Mirrors grok ``enum ProfileName`` (``#[derive(Default, PartialEq, Eq)]``;
    ``#[default] = Workspace``). The five built-in variants are fieldless
    subclasses (``Workspace`` / ``Devbox`` / ``ReadOnly`` / ``Strict`` /
    ``Off``); ``Custom`` carries the profile name. Discrimination is by
    ``isinstance`` -- Python's structural equivalent of Rust's match arms.
    Dataclass-generated ``__eq__`` checks ``other.__class__ is self.__class__``
    first, so distinct fieldless variants compare unequal (matching Rust's
    per-variant identity) while equal-named ``Custom`` instances compare equal.
    """

    @classmethod
    def default(cls) -> ProfileName:
        """Default profile (mirrors grok ``#[default]`` = ``Workspace``)."""
        return Workspace()

    def restricts_network(self) -> bool:
        """Whether this profile blocks child-process network by default.

        Only ``ReadOnly`` and ``Strict`` restrict network; the rest leave it
        open (the agent needs LLM API access). ``Custom`` answers ``False``
        here -- use :meth:`restricts_network_resolved` to consult its config.
        """
        return isinstance(self, (ReadOnly, Strict))

    def restricts_network_resolved(self, config: SandboxConfig) -> bool:
        """Resolve network restriction, consulting ``config`` for ``Custom``.

        Built-ins answer directly; ``Custom(name)`` looks up the profile's
        optional ``restrict_network`` flag in ``config.profiles`` (``False``
        when the flag is absent or the profile is undefined).
        """
        if isinstance(self, (ReadOnly, Strict)):
            return True
        if isinstance(self, (Workspace, Devbox, Off)):
            return False
        if isinstance(self, Custom):
            entry = config.profiles.get(self.name)
            if entry is None or entry.restrict_network is None:
                return False
            return entry.restrict_network
        return False

    def __str__(self) -> str:
        """Wire/display form (mirrors grok ``impl Display for ProfileName``)."""
        if isinstance(self, Workspace):
            return "workspace"
        if isinstance(self, Devbox):
            return "devbox"
        if isinstance(self, ReadOnly):
            return "read-only"
        if isinstance(self, Strict):
            return "strict"
        if isinstance(self, Off):
            return "off"
        if isinstance(self, Custom):
            return self.name
        return ""

    @classmethod
    def from_str(cls, raw: str) -> ProfileName:
        """Parse a profile name from its wire/display form.

        Accepts ``workspace`` / ``devbox`` / ``read-only`` (also ``readonly``)
        / ``strict`` / ``off`` (also ``none``). Any other string becomes a
        :class:`Custom` profile whose name is the raw input -- validation is
        deferred to the config lookup at resolve time. Mirrors grok
        ``FromStr``; never errors.
        """
        if raw == "workspace":
            return Workspace()
        if raw == "devbox":
            return Devbox()
        if raw in ("read-only", "readonly"):
            return ReadOnly()
        if raw == "strict":
            return Strict()
        if raw in ("off", "none"):
            return Off()
        return Custom(raw)


@dataclass(frozen=True)
class Workspace(ProfileName):
    """Built-in: full workspace write access, network open, default-read on."""


@dataclass(frozen=True)
class Devbox(ProfileName):
    """Built-in: everything writable except ``/data``, network open."""


@dataclass(frozen=True)
class ReadOnly(ProfileName):
    """Built-in: minimal writes (``~/.grok`` + temp), network blocked."""


@dataclass(frozen=True)
class Strict(ProfileName):
    """Built-in: explicit system read-allowlist, no default-read, net blocked."""


@dataclass(frozen=True)
class Off(ProfileName):
    """Built-in: sandbox disabled (no kernel enforcement applied)."""


@dataclass(frozen=True)
class Custom(ProfileName):
    """User-defined profile name, resolved against :class:`SandboxConfig``.

    Carries the profile ``name`` looked up in ``config.profiles``; an undefined
    name is an error at resolve time (deferred to the runtime layer).
    """

    name: str


@dataclass(frozen=True)
class ProfileConfig:
    """A single custom profile's serde-defaulted optional fields.

    Mirrors grok ``struct ProfileConfig`` (``#[derive(Debug, Clone, PartialEq,
    Eq, Deserialize, Serialize)]``; every field ``#[serde(default)]``). The
    TOML ``[profiles.<name>]`` row: an optional ``extends`` base, an optional
    ``restrict_network`` override, and three path lists (``read_only`` /
    ``read_write`` / ``deny``) that compose on top of the extended base.
    """

    extends: str | None = None
    restrict_network: bool | None = None
    read_only: list[str] = field(default_factory=list)
    read_write: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SandboxConfig:
    """Aggregate of named custom profiles (``profiles: HashMap<...>``).

    Mirrors grok ``struct SandboxConfig`` (``#[derive(Default)]`` -- empty
    ``profiles`` map). The merge target for :func:`merge_project_profiles`;
    :func:`mismatched_profile_names` diffs two of these to surface conflicts.
    """

    profiles: dict[str, ProfileConfig] = field(default_factory=dict)


def mismatched_profile_names(
    global_config: SandboxConfig, project: SandboxConfig
) -> list[str]:
    """Custom profile names defined in both configs whose policy differs.

    A project config may ADD custom profile names but cannot redefine one
    already present globally -- this surfaces the names where a project
    attempts a conflicting redefinition (the merge silently keeps the global
    one). Only names that parse as :class:`Custom` are considered (built-in
    name collisions are not user-overridable). Sorted for stable output.

    Mirrors grok ``mismatched_profile_names``.
    """
    names = [
        name
        for name, project_profile in project.profiles.items()
        if isinstance(ProfileName.from_str(name), Custom)
        and (global_config.profiles.get(name) is not None)
        and global_config.profiles[name] != project_profile
    ]
    names.sort()
    return names


def merge_project_profiles(config: SandboxConfig, project: SandboxConfig) -> None:
    """Merge ``project`` profiles into ``config`` in place (global-wins).

    Names already present in ``config`` (the global config) are NOT
    overwritten -- a workspace cannot replace a trusted global custom
    profile's policy (e.g. hollow out its ``deny`` / widen ``read_write``)
    while keeping the trusted name. New project-only names are inserted.

    Mutates ``config.profiles`` in place (the dict is mutable even though the
    dataclass is frozen); mirrors grok ``merge_project_profiles(&mut config, ...)``.
    """
    for name, profile in project.profiles.items():
        config.profiles.setdefault(name, profile)
