"""Tests for sampler.serde_helpers (R211, ``xai-grok-sampling-types`` ``serde_helpers.rs``).

Covers the ``serde_helpers.rs`` leaf -- the single ``deserialize_with`` hook
:func:`empty_string_as_none` consumed by two ``Option<String>`` fingerprint
fields (``types.rs`` ``system_fingerprint`` on the ChatCompletion response +
``conversation.rs`` ``model_fingerprint`` aliasing ``system_fingerprint``).

Migration invariants tested below:

- ``Some("")`` -> ``None`` (the empty-string -> ``None`` normalization that is
  the hook's whole purpose: some upstream services emit an empty string to
  mean "absent").
- ``Some("x")`` -> ``"x"`` (a non-empty string passes through unchanged; a
  whitespace-only string is NOT empty).
- ``None`` -> ``None`` (a missing / null wire value stays ``None``).
"""

from __future__ import annotations

import pytest

from minimax_code.sampler.serde_helpers import empty_string_as_none

# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_module_barrel_exposes_one_symbol() -> None:
    """1 function = 1 re-exported symbol."""
    import minimax_code.sampler.serde_helpers as serde_helpers

    assert serde_helpers.__all__ == ["empty_string_as_none"]


def test_package_barrel_re_exports_empty_string_as_none() -> None:
    """The package barrel flattens the empty_string_as_none symbol."""
    import minimax_code.sampler as sampler

    assert "empty_string_as_none" in sampler.__all__


# ---------------------------------------------------------------------------
# empty_string_as_none: Some("") -> None, Some("x") -> "x", None -> None.
# ---------------------------------------------------------------------------


def test_empty_string_normalizes_to_none() -> None:
    """``Some("")`` -> ``None`` (the hook's whole purpose: some upstream
    services emit an empty string to mean "absent")."""
    assert empty_string_as_none("") is None


def test_non_empty_string_passes_through() -> None:
    """``Some("x")`` -> ``"x"`` (a non-empty string is unchanged)."""
    assert empty_string_as_none("grok-1") == "grok-1"


def test_whitespace_only_string_is_not_empty() -> None:
    """A whitespace-only string is NOT empty (grok ``s.is_empty()`` checks
    length, not content) -> it passes through unchanged."""
    assert empty_string_as_none(" ") == " "


def test_none_passes_through() -> None:
    """``None`` -> ``None`` (a missing / null wire value stays ``None``)."""
    assert empty_string_as_none(None) is None


@pytest.mark.parametrize("value", ["", None])
def test_falsy_values_collapse_to_none(value: str | None) -> None:
    """Both falsy wire cases (``""`` and ``None``) collapse to ``None``
    (mirrors grok's ``opt.filter(|s| !s.is_empty())``)."""
    assert empty_string_as_none(value) is None
