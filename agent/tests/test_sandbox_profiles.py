"""Tests for sandbox.profiles (R236, ``xai-grok-sandbox`` ``profiles.rs``).

Covers the migrated pure-logic leaf: the :class:`ProfileName` tagged union
(``from_str`` / ``__str__`` round-trip + ``default``), the network-restriction
predicates (``restricts_network`` for built-ins, ``restricts_network_resolved``
consulting :class:`SandboxConfig` for :class:`Custom`), the
:class:`ProfileConfig` / :class:`SandboxConfig` serde-defaulted DTOs, the two
pure merge functions (:func:`mismatched_profile_names` sorting +
:func:`merge_project_profiles` global-wins), and the frozen+slots dataclass
value semantics (per-variant ``__eq__`` identity, hash collision vs eq
distinction for fieldless variants).

Mirrors grok's ``profiles.rs`` pure-logic tests:
``parse_profile_names`` / ``display_roundtrip`` / ``display_custom`` /
``network_restriction`` /
``mismatched_profile_names_reports_only_changed_custom_profiles`` /
``project_cannot_redefine_global_profile``.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.sandbox import (
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

# ---------------------------------------------------------------------------
# ProfileName.from_str: wire/display form -> variant (mirrors grok FromStr).
# ---------------------------------------------------------------------------

_BUILTIN_ROUND_TRIPS = [
    ("workspace", Workspace),
    ("devbox", Devbox),
    ("read-only", ReadOnly),
    ("strict", Strict),
    ("off", Off),
]


@pytest.mark.parametrize("raw, expected_cls", _BUILTIN_ROUND_TRIPS)
def test_from_str_parses_builtins(raw: str, expected_cls: type) -> None:
    """Each canonical wire token maps to its built-in variant."""
    result = ProfileName.from_str(raw)
    assert isinstance(result, expected_cls)


def test_from_str_accepts_readonly_alias() -> None:
    """``readonly`` (no hyphen) is accepted as an alias for ``read-only``."""
    assert isinstance(ProfileName.from_str("readonly"), ReadOnly)


def test_from_str_accepts_none_alias_for_off() -> None:
    """``none`` is accepted as an alias for ``off`` (network/CLI ergonomics)."""
    assert isinstance(ProfileName.from_str("none"), Off)


@pytest.mark.parametrize(
    "raw",
    ["my-profile", "customthing", "", "Workspace", "WORKSPACE", "my profile"],
)
def test_from_str_unknown_becomes_custom(raw: str) -> None:
    """Any non-builtin string becomes ``Custom(raw)`` -- validation is deferred
    to the config lookup at resolve time. Mirrors grok: never errors here."""
    result = ProfileName.from_str(raw)
    assert isinstance(result, Custom)
    assert result.name == raw


# ---------------------------------------------------------------------------
# ProfileName.__str__: variant -> wire/display form (mirrors grok Display).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw, expected_cls", _BUILTIN_ROUND_TRIPS)
def test_str_emits_canonical_wire_form(raw: str, expected_cls: type) -> None:
    """``str(variant)`` emits the canonical wire token (not aliases)."""
    assert str(expected_cls()) == raw


def test_str_emits_custom_name_verbatim() -> None:
    """``Custom`` renders its raw name unchanged (no quoting/casing)."""
    assert str(Custom("my-profile")) == "my-profile"


@pytest.mark.parametrize("raw, expected_cls", _BUILTIN_ROUND_TRIPS)
def test_str_round_trips_through_from_str(raw: str, expected_cls: type) -> None:
    """``from_str(str(variant)) == variant`` for every built-in -- the wire
    form is the canonical round-trip token."""
    variant = expected_cls()
    assert ProfileName.from_str(str(variant)) == variant


# ---------------------------------------------------------------------------
# ProfileName.default: #[default] = Workspace.
# ---------------------------------------------------------------------------


def test_default_is_workspace() -> None:
    """grok ``#[default] = Workspace`` -- the absence-of-config profile."""
    assert isinstance(ProfileName.default(), Workspace)
    assert ProfileName.default() == Workspace()


# ---------------------------------------------------------------------------
# restricts_network / restricts_network_resolved.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "variant, expected",
    [
        (ReadOnly(), True),
        (Strict(), True),
        (Workspace(), False),
        (Devbox(), False),
        (Off(), False),
        (Custom("anything"), False),  # unresolved -> consult config
    ],
)
def test_restricts_network_builtin_matrix(
    variant: ProfileName, expected: bool
) -> None:
    """Only ``ReadOnly`` / ``Strict`` block network by default; ``Custom``
    answers ``False`` pending config resolution."""
    assert variant.restricts_network() is expected


@pytest.mark.parametrize(
    "variant",
    [ReadOnly(), Strict(), Workspace(), Devbox(), Off()],
)
def test_restricts_network_resolved_builtins_ignore_config(variant: ProfileName) -> None:
    """Built-ins answer from the variant alone -- config is not consulted."""
    assert variant.restricts_network_resolved(SandboxConfig()) == (
        variant.restricts_network()
    )


def test_restricts_network_resolved_custom_true_flag() -> None:
    """``Custom`` whose profile sets ``restrict_network = true`` restricts."""
    config = SandboxConfig(
        profiles={"locked": ProfileConfig(restrict_network=True)}
    )
    assert Custom("locked").restricts_network_resolved(config) is True


def test_restricts_network_resolved_custom_false_flag() -> None:
    config = SandboxConfig(
        profiles={"open": ProfileConfig(restrict_network=False)}
    )
    assert Custom("open").restricts_network_resolved(config) is False


def test_restricts_network_resolved_custom_no_flag_defaults_false() -> None:
    """``Custom`` whose profile omits ``restrict_network`` -> ``False``."""
    config = SandboxConfig(profiles={"silent": ProfileConfig()})
    assert Custom("silent").restricts_network_resolved(config) is False


def test_restricts_network_resolved_custom_unknown_defaults_false() -> None:
    """``Custom`` whose name is absent from config -> ``False`` (resolve-time
    error is the runtime layer's concern)."""
    assert Custom("ghost").restricts_network_resolved(SandboxConfig()) is False


# ---------------------------------------------------------------------------
# ProfileConfig: serde-defaulted optional fields.
# ---------------------------------------------------------------------------


def test_profile_config_defaults() -> None:
    """grok ``#[serde(default)]`` on every field -> all optional / empty."""
    cfg = ProfileConfig()
    assert cfg.extends is None
    assert cfg.restrict_network is None
    assert cfg.read_only == []
    assert cfg.read_write == []
    assert cfg.deny == []


def test_profile_config_equality() -> None:
    """Equal fields -> equal; one differing field -> unequal."""
    a = ProfileConfig(extends="base", deny=["/secret"])
    b = ProfileConfig(extends="base", deny=["/secret"])
    c = ProfileConfig(extends="base", deny=["/other"])
    assert a == b
    assert a != c


def test_profile_config_default_lists_are_independent() -> None:
    """``field(default_factory=list)`` gives each instance its own list
    (no shared mutable default across instances)."""
    a = ProfileConfig()
    b = ProfileConfig()
    a.read_only.append("/x")
    assert b.read_only == []  # untouched


# ---------------------------------------------------------------------------
# SandboxConfig: profiles map aggregate.
# ---------------------------------------------------------------------------


def test_sandbox_config_defaults_empty_profiles() -> None:
    """grok ``#[derive(Default)]`` -> empty profiles map."""
    assert SandboxConfig().profiles == {}


# ---------------------------------------------------------------------------
# mismatched_profile_names: global-wins conflict detection.
# ---------------------------------------------------------------------------


def test_mismatched_reports_changed_custom_profile() -> None:
    """A project redefining a global custom profile's policy is reported."""
    global_cfg = SandboxConfig(
        profiles={"trusted": ProfileConfig(deny=["/a"])}
    )
    project = SandboxConfig(
        profiles={"trusted": ProfileConfig(deny=["/b"])}
    )
    assert mismatched_profile_names(global_cfg, project) == ["trusted"]


def test_mismatched_ignores_project_only_names() -> None:
    """Names the project adds that are NOT global are not conflicts (they are
    legitimately new custom profiles)."""
    global_cfg = SandboxConfig(
        profiles={"keep": ProfileConfig(deny=["/a"])}
    )
    project = SandboxConfig(
        profiles={
            "keep": ProfileConfig(deny=["/a"]),  # unchanged
            "fresh": ProfileConfig(deny=["/c"]),  # project-only
        }
    )
    assert mismatched_profile_names(global_cfg, project) == []


def test_mismatched_ignores_unchanged_policy() -> None:
    """Same name + same policy -> not a mismatch."""
    global_cfg = SandboxConfig(profiles={"x": ProfileConfig(deny=["/a"])})
    project = SandboxConfig(profiles={"x": ProfileConfig(deny=["/a"])})
    assert mismatched_profile_names(global_cfg, project) == []


def test_mismatched_ignores_builtin_name_collisions() -> None:
    """Names parsing as a built-in (e.g. ``workspace``) are never reported --
    built-ins are not user-overridable, so their presence is irrelevant."""
    global_cfg = SandboxConfig(
        profiles={"workspace": ProfileConfig(deny=["/a"])}
    )
    project = SandboxConfig(
        profiles={"workspace": ProfileConfig(deny=["/b"])}
    )
    assert mismatched_profile_names(global_cfg, project) == []


def test_mismatched_output_is_sorted() -> None:
    """Sorted for stable output / deterministic CLI display."""
    global_cfg = SandboxConfig(
        profiles={
            "zeta": ProfileConfig(deny=["/a"]),
            "alpha": ProfileConfig(deny=["/a"]),
            "mid": ProfileConfig(deny=["/a"]),
        }
    )
    project = SandboxConfig(
        profiles={
            "zeta": ProfileConfig(deny=["/b"]),
            "alpha": ProfileConfig(deny=["/b"]),
            "mid": ProfileConfig(deny=["/b"]),
        }
    )
    assert mismatched_profile_names(global_cfg, project) == ["alpha", "mid", "zeta"]


# ---------------------------------------------------------------------------
# merge_project_profiles: global-wins merge in place.
# ---------------------------------------------------------------------------


def test_merge_keeps_global_on_conflict() -> None:
    """The core global-wins guarantee: a project CANNOT replace a trusted
    global custom profile's policy while keeping its name."""
    config = SandboxConfig(
        profiles={"trusted": ProfileConfig(deny=["/global"])}
    )
    project = SandboxConfig(
        profiles={"trusted": ProfileConfig(deny=["/hijacked"])}
    )
    merge_project_profiles(config, project)
    assert config.profiles["trusted"].deny == ["/global"]  # global retained


def test_merge_adds_project_only_names() -> None:
    """New project-only names ARE inserted (project may ADD profiles)."""
    config = SandboxConfig(profiles={"a": ProfileConfig()})
    project = SandboxConfig(
        profiles={"b": ProfileConfig(deny=["/new"])}
    )
    merge_project_profiles(config, project)
    assert config.profiles["b"].deny == ["/new"]
    assert "a" in config.profiles  # pre-existing untouched


def test_merge_preserves_existing_when_project_matches() -> None:
    """When project's policy equals global's, the global reference is kept
    (setdefault semantics -- no spurious replacement)."""
    global_profile = ProfileConfig(deny=["/a"])
    config = SandboxConfig(profiles={"x": global_profile})
    project = SandboxConfig(profiles={"x": ProfileConfig(deny=["/a"])})
    merge_project_profiles(config, project)
    assert config.profiles["x"] is global_profile  # identity preserved


def test_merge_does_not_touch_global_only_names() -> None:
    """Project lacking a name that global has -> global entry untouched."""
    config = SandboxConfig(profiles={"global_only": ProfileConfig(deny=["/g"])})
    project = SandboxConfig(profiles={"other": ProfileConfig()})
    merge_project_profiles(config, project)
    assert config.profiles["global_only"].deny == ["/g"]


# ---------------------------------------------------------------------------
# Frozen + slots dataclass value semantics.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "variant",
    [
        Workspace(),
        Devbox(),
        ReadOnly(),
        Strict(),
        Off(),
        Custom("x"),
    ],
)
def test_variants_are_frozen(variant: ProfileName) -> None:
    """frozen=True overrides ``__setattr__`` to raise ``FrozenInstanceError``
    unconditionally -- assignment is rejected for every variant, whether
    fieldless (no settable field) or ``Custom`` (the ``name`` field)."""
    with pytest.raises(FrozenInstanceError):
        variant.name = "mutated"  # type: ignore[misc]


def test_distinct_fieldless_variants_are_unequal() -> None:
    """Dataclass ``__eq__`` checks ``other.__class__ is self.__class__`` first
    -- distinct fieldless variants compare unequal (mirrors Rust enum variant
    identity; ``Workspace != Devbox`` even though both carry no data)."""
    variants = [Workspace(), Devbox(), ReadOnly(), Strict(), Off()]
    for i, a in enumerate(variants):
        for b in variants[i + 1 :]:
            assert a != b


def test_custom_equality_by_name() -> None:
    """``Custom`` compares equal iff names match; cross-variant inequality."""
    assert Custom("a") == Custom("a")
    assert Custom("a") != Custom("b")
    assert Custom("workspace") != Workspace()  # name shadowing is not equality


def test_fieldless_variants_hash_collide_but_eq_distinguishes() -> None:
    """frozen dataclass hashes fieldless variants as ``hash(())`` -- they
    collide numerically, but ``__eq__`` checks ``__class__`` first so the
    collision is benign (dict/set still treat them as distinct keys). This is
    the documented Python equivalent of Rust's per-variant discriminant."""
    assert hash(Workspace()) == hash(Devbox()) == hash(ReadOnly()) == hash(())
    assert Workspace() != Devbox()  # eq distinguishes despite hash collision
    as_set = {Workspace(), Devbox(), Strict()}
    assert len(as_set) == 3  # set correctness preserved


def test_custom_is_hashable_by_name() -> None:
    """``Custom`` hashes its name tuple -> same name, same hash."""
    assert hash(Custom("a")) == hash(("a",))
    assert hash(Custom("a")) == hash(Custom("a"))


def test_profile_config_and_sandbox_config_are_frozen() -> None:
    """Both DTOs are frozen -- the merge mutates the inner dict, not the
    dataclass fields."""
    cfg = ProfileConfig(deny=["/x"])
    sc = SandboxConfig()
    with pytest.raises(FrozenInstanceError):
        cfg.deny = []  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        sc.profiles = {}  # type: ignore[misc]
