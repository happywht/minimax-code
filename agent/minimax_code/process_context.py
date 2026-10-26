"""Required process target cannot be weakened by an inner workspace policy."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from .process_types import ExecutionOwner, ExecutionTargetSpec, ProcessBoundaryError


@dataclass(frozen=True)
class RequiredProcessTarget:
    target: ExecutionTargetSpec
    owner: ExecutionOwner | None = None


_required: ContextVar[RequiredProcessTarget | None] = ContextVar(
    "required_process_target", default=None)


def current_process_target() -> RequiredProcessTarget | None:
    return _required.get()


@contextmanager
def required_target_scope(target: ExecutionTargetSpec | None,
                          owner: ExecutionOwner | None = None) -> Iterator[None]:
    parent = _required.get()
    if parent is not None:
        if target != parent.target:
            raise ProcessBoundaryError("An inherited process target cannot be weakened or replaced")
        if owner is None:
            owner = parent.owner
        elif parent.owner is not None and owner != parent.owner:
            raise ProcessBoundaryError("A child owner requires explicit registered child binding")
    if target is None and owner is not None:
        raise ProcessBoundaryError("A process owner requires a restricted target")
    token = _required.set(RequiredProcessTarget(target, owner) if target else None)
    try:
        yield
    finally:
        _required.reset(token)


@contextmanager
def registered_child_target_scope(owner: ExecutionOwner, *, parent_run_id: str) -> Iterator[None]:
    """Bind only after the caller has admitted the actual child run in SQLite.

    Supervisor admission independently verifies run/family membership. This helper
    cannot change the target and rejects a detached or cross-family child.
    """
    parent = _required.get()
    if parent is None or parent.owner is None or parent.owner.run_id != parent_run_id:
        raise ProcessBoundaryError("Restricted child requires its bound parent run")
    if (owner.run_id == parent_run_id or owner.family_id != parent.owner.family_id
            or owner.family_root_run_id != parent.owner.family_root_run_id):
        raise ProcessBoundaryError("Restricted child must have its own run in the same family")
    token = _required.set(RequiredProcessTarget(parent.target, owner))
    try:
        yield
    finally:
        _required.reset(token)
