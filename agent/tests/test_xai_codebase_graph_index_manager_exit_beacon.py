"""Black-box tests for the ``index_manager`` actor-lifecycle beacon +
workspace-dedup singleton (R306e).

Ported **by function** from grok ``index_manager.rs``:

* ``ExitBeacon`` (L540-L550) -- the ``#[cfg(test)]`` RAII guard whose ``Drop``
  flips an ``Arc<AtomicBool>`` so a test can confirm the actor loop *actually*
  exited (not merely that the last ``Weak`` stopped upgrading).
* ``ACTIVE_MANAGERS`` (L58) -- the
  ``Lazy<DashMap<PathBuf, Weak<IndexManagerHandle>>>`` process-level singleton
  that dedups live actors by workspace.
* ``spawn`` canonicalize step (L591) -- ``dunce::canonicalize`` as the dedup
  key, so two paths to the same workspace collide.

All three are lifecycle/dedup infrastructure for the actor runtime that lands
in R306f; this suite pins their Pythonic shapes (mutable ``bool`` beacon,
:func:`pathlib.Path.resolve` dedup key, :class:`weakref.WeakValueDictionary`
singleton + ``__weakref__`` slot on the handle) without instantiating the
actor. The ``spawn`` entrypoint itself lands in R306f.
"""

from __future__ import annotations

import asyncio
import gc
import weakref
from pathlib import Path

import minimax_code.xai_codebase_graph as xcg_root
from minimax_code.xai_codebase_graph import index_manager as im
from minimax_code.xai_codebase_graph.index_manager import (
    ExitBeacon,
    IndexManagerHandle,
)


def _make_handle() -> IndexManagerHandle:
    """Build a fresh open handle for weakref / singleton tests."""
    return IndexManagerHandle(mailbox=asyncio.Queue(), closed=asyncio.Event())


# === ExitBeacon: exit-flag semantics ====================================


def test_exit_beacon_starts_not_exited() -> None:
    """A fresh beacon reads ``exited=False`` (actor still running)."""
    beacon = ExitBeacon()
    assert beacon.exited is False


def test_exit_beacon_close_flips_flag() -> None:
    """``close()`` mirrors grok ``Drop`` -- the flag flips to ``True``."""
    beacon = ExitBeacon()
    beacon.close()
    assert beacon.exited is True


def test_exit_beacon_context_manager_flips_on_exit() -> None:
    """The ``with`` form mirrors grok scoped ``Drop`` at block exit."""
    beacon = ExitBeacon()
    assert beacon.exited is False
    with beacon as same:
        assert same is beacon
        assert beacon.exited is False
    assert beacon.exited is True


def test_exit_beacon_exited_is_boolean_property() -> None:
    """``exited`` is a bool property (not a callable) -- Pythonic probe."""
    beacon = ExitBeacon()
    assert isinstance(beacon.exited, bool)


def test_exit_beacon_close_is_idempotent() -> None:
    """Repeated ``close()`` keeps the flag ``True`` (no toggle back)."""
    beacon = ExitBeacon()
    beacon.close()
    beacon.close()
    assert beacon.exited is True


def test_exit_beacon_uses_slots() -> None:
    """``__slots__`` -> no ``__dict__`` (lean beacon, same posture as the handle)."""
    beacon = ExitBeacon()
    assert not hasattr(beacon, "__dict__")


# === ExitBeacon: visibility (test-only, not in __all__) =================


def test_exit_beacon_not_in_leaf_all() -> None:
    """grok gates ``ExitBeacon`` behind ``#[cfg(test)]``; Python keeps it out
    of ``__all__`` (test-only visibility, same surface as grok's test gate)."""
    assert "ExitBeacon" not in im.__all__


def test_exit_beacon_not_in_crate_root_all() -> None:
    """The test-only beacon never reaches the crate root either."""
    assert "ExitBeacon" not in xcg_root.__all__


# === _canonicalize_root: dedup key ======================================


def test_canonicalize_root_returns_absolute_str() -> None:
    """A relative path resolves to an absolute string (the dedup key)."""
    resolved = im._canonicalize_root(".")
    assert isinstance(resolved, str)
    assert Path(resolved).is_absolute()


def test_canonicalize_root_accepts_str_and_path() -> None:
    """grok takes ``PathBuf``; Python accepts both ``str`` and ``Path``."""
    key_str = im._canonicalize_root(".")
    key_path = im._canonicalize_root(Path("."))
    assert key_str == key_path  # both collapse to the same canonical key


def test_canonicalize_root_aliases_same_workspace() -> None:
    """Two spellings of the same workspace collide (the dedup invariant).

    grok's ``dunce::canonicalize`` collapses ``a/b/../b`` to ``a/b`` so a
    second ``spawn`` reuses the live actor instead of forking a duplicate.
    :func:`pathlib.Path.resolve` does the same collapsing, so the dedup key
    is stable across equivalent path spellings.
    """
    a = im._canonicalize_root("a/b/../b")
    b = im._canonicalize_root("a/b")
    assert a == b


# === _ACTIVE_MANAGERS: WeakValueDictionary singleton ====================


def test_active_managers_is_weak_value_dictionary() -> None:
    """``DashMap<PathBuf, Weak<_>>`` -> :class:`weakref.WeakValueDictionary`.

    Same value-side semantics (weak ref to the handle); the sharding that
    ``DashMap`` provides for multi-thread access is dropped (a single
    event-loop thread has no contention to shard against).
    """
    assert isinstance(im._ACTIVE_MANAGERS, weakref.WeakValueDictionary)


def test_active_managers_is_module_level_singleton() -> None:
    """The map is a module-level singleton -- two lookups see the same object."""
    assert im._ACTIVE_MANAGERS is im._ACTIVE_MANAGERS


def test_active_managers_drops_handle_when_strong_ref_gone() -> None:
    """A dead weak ref is auto-removed on GC -- subsumes grok's remove+retry.

    grok's ``spawn`` (L600-L626) loops on ``entry(k)`` and removes an
    occupied-but-dead slot before retrying. A :class:`weakref.WeakValueDictionary`
    *automatically* drops a dead entry on GC, so the occupied-but-dead case
    never surfaces in Python -- the whole retry arm is a no-op here.
    """
    key = "test-workspace-gc-cleanup"
    handle = _make_handle()
    try:
        im._ACTIVE_MANAGERS[key] = handle
        assert im._ACTIVE_MANAGERS.get(key) is handle  # live: strong ref held
    finally:
        del handle
    gc.collect()
    assert key not in im._ACTIVE_MANAGERS  # auto-removed (grok needs manual remove)


def test_active_managers_keeps_handle_while_strong_ref_held() -> None:
    """While a strong ref exists, the weak entry stays live (reuse on spawn)."""
    key = "test-workspace-live"
    handle = _make_handle()
    try:
        im._ACTIVE_MANAGERS[key] = handle
        assert im._ACTIVE_MANAGERS.get(key) is handle
    finally:
        del handle
        gc.collect()
        # leave the singleton clean for other tests.
        im._ACTIVE_MANAGERS.pop(key, None)


def test_active_managers_not_in_leaf_all() -> None:
    """grok ``static ACTIVE_MANAGERS`` is crate-private -> not in ``__all__``."""
    assert "_ACTIVE_MANAGERS" not in im.__all__


# === IndexManagerHandle: weakref support (R306e __weakref__ slot) =======


def test_handle_is_weakly_referenceable() -> None:
    """R306e adds ``__weakref__`` to the handle slots so the singleton can hold
    a :class:`weakref.ref` -- the Python analog of grok ``Arc::downgrade``."""
    handle = _make_handle()
    ref = weakref.ref(handle)
    assert ref() is handle  # strong ref held -> ref resolves


def test_handle_weakref_dies_when_strong_ref_gone() -> None:
    """Once the strong ref drops, the weak ref resolves to ``None``."""
    handle = _make_handle()
    ref = weakref.ref(handle)
    assert ref() is handle
    del handle
    gc.collect()
    assert ref() is None


def test_handle_weakref_slot_is_declared() -> None:
    """The ``__weakref__`` slot is declared (enables weak referencing)."""
    assert "__weakref__" in IndexManagerHandle.__slots__


def test_handle_still_has_no_dict() -> None:
    """Adding ``__weakref__`` does not regress the ``__dict__``-free guarantee."""
    handle = _make_handle()
    assert not hasattr(handle, "__dict__")
