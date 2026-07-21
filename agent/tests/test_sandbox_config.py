"""Barrel surface regression baseline for ``minimax_code.sandbox`` (R236).

Locks the public symbol set re-exported by ``minimax_code.sandbox`` so a future
leaf migration (R237+) must update both the count assertion and the set
baseline, or append a new group -- preventing silent barrel drift (a symbol
renamed in a leaf but not in the barrel would silently disappear from the
public API).

R236 baseline (profiles.py, 2026-07-22): 11 symbols = 9 classes (``ProfileName``
union: base + 5 fieldless built-ins ``Workspace`` / ``Devbox`` / ``ReadOnly`` /
``Strict`` / ``Off`` + ``Custom``, plus ``ProfileConfig`` + ``SandboxConfig``
DTOs) + 2 pure merge functions (``merge_project_profiles`` /
``mismatched_profile_names``).
"""

from __future__ import annotations

import minimax_code.sandbox as sandbox


def test_package_barrel_exposes_eleven_symbols() -> None:
    """R236 opens the sandbox package with profiles.py -- 11 symbols (9
    classes + 2 functions). Future leaves must update count + set."""
    assert len(sandbox.__all__) == 11
    assert set(sandbox.__all__) == {
        # R236 (profiles.py): ProfileName union + 2 DTOs + 2 merge functions.
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
    }


def test_all_exported_names_resolve_to_real_attributes() -> None:
    """Every ``__all__`` entry must resolve to a real attribute on the package
    -- catches stale ``__all__`` entries pointing at renamed/removed symbols
    (the set assertion above only checks membership, not resolvability)."""
    for name in sandbox.__all__:
        assert hasattr(sandbox, name), f"sandbox missing exported symbol: {name}"


def test_barrel_all_is_global_ascii_sorted() -> None:
    """The barrel ``__all__`` list itself must be pure global ASCII sort
    (uppercase 65-90 before lowercase 97-122, alphabetical within each group).

    This guards against accidental grouping-by-migration-round IN the barrel
    list -- the set assertion above is free-form, but the barrel must stay
    sorted so re-exports read deterministically across leaves. Contrast with
    the sampler barrel where the set assertion groups by round (test-only
    convenience); the barrel list there is also ASCII-sorted."""
    assert sandbox.__all__ == sorted(sandbox.__all__)
