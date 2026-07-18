"""Mermaid render engine protocol, limits, and the panic-isolating guard (R38).

Ports the **guard layer** of grok's ``xai-grok-mermaid`` — the resource cap, the
pluggable-engine trait, and the :func:`render_checked` entry point that enforces
the cap and isolates engine failures over untrusted source.

The layout engine (``mermaid-to-svg``) and the rasterizer are NOT ported (Rust
rendering stacks); this module defines the contract a future Python renderer
(``mmdc`` CLI subprocess or ``mermaid.js`` over a headless browser) will
implement. What is ported is the host-agnostic guard logic around any engine:

* :class:`RenderLimits` — caps applied **before** the engine runs so an
  oversized payload cannot exhaust memory.
* :class:`MermaidEngine` — the pluggable backend protocol (grok ``trait
  MermaidEngine: Send + Sync``).
* :func:`render_checked` — the entry point: reject oversized source without
  invoking the engine, then isolate any non-:class:`~.errors.MermaidError`
  exception the engine raises as a :class:`~.errors.MermaidPanicError`.

Mapping
-------

* ``RenderLimits`` carries ``#[derive(Debug, Clone, Copy, PartialEq, Eq)]`` +
  ``impl Default`` → **frozen=True, slots=True dataclass** with a field
  default (``64 * 1024``, grok's 64 KiB cap). ``usize`` → ``int``.
* ``trait MermaidEngine: Send + Sync`` → ``typing.Protocol`` with a ``render``
  method. ``Send + Sync`` has no Python meaning (GIL); the protocol is the
  duck-typed contract. ``@runtime_checkable`` is added so ``isinstance`` works
  for spy/fake engines in tests, mirroring grok's trait-object dispatch.
* ``render_checked`` — the source-size check uses **byte** length
  (``len(source.encode("utf-8"))``) to mirror Rust ``str::len()`` (bytes, not
  chars) and grok's ``"…-byte limit"`` message. The ``catch_unwind`` panic
  isolation maps to: a raised :class:`~.errors.MermaidError` is re-raised
  verbatim (the "expected" channel, mirroring ``Ok(Err(e))``); any other
  ``Exception`` is wrapped as :class:`~.errors.MermaidPanicError(str(exc))`
  (the "panic" channel, mirroring ``Err(payload)``). ``BaseException``
  subclasses (``KeyboardInterrupt``/``SystemExit``) are deliberately NOT caught
  — grok's ``catch_unwind`` does not intercept ``abort`` either.

Product fusion
--------------

A future wiring round renders untrusted model mermaid output. ``render_checked``
is the contract the worker calls: it guarantees a malformed/huge diagram
degrades to a typed error (→ code-block fallback) rather than crashing the
agent or hanging on an oversized payload. The size cap and the panic→error
upgrade are the two defenses that make rendering untrusted source safe in
process; the wall-clock timeout (grok enforces it *out of process* via the
pager's child) is a separate wiring concern and is not ported here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .errors import MermaidError, MermaidPanicError, MermaidUnsupportedError
from .types import RenderedDiagram, RenderParams

__all__ = [
    "RenderLimits",
    "MermaidEngine",
    "render_checked",
]


@dataclass(frozen=True, slots=True)
class RenderLimits:
    """Caps applied by :func:`render_checked` before the engine runs.

    So untrusted source cannot trivially exhaust memory via an oversized
    payload. This enforces :attr:`max_source_bytes`. A wall-clock timeout for a
    runaway render is enforced *out of process* by the caller (grok's pager
    spawns a short-lived child per diagram); the output pixmap area/height are
    capped inside the rasterizer (not ported).

    Frozen value type (grok ``Debug + Clone + Copy + PartialEq + Eq``).
    """

    #: Maximum accepted source length in bytes. Larger input is rejected with
    #: :class:`~.errors.MermaidUnsupportedError` *before* the engine runs.
    max_source_bytes: int = 64 * 1024


@runtime_checkable
class MermaidEngine(Protocol):
    """A pluggable Mermaid rendering backend (grok ``trait MermaidEngine``).

    Implementations turn mermaid source into a rasterized
    :class:`~.types.RenderedDiagram`. Prefer calling :func:`render_checked`
    over ``render`` directly: it applies :class:`RenderLimits` and isolates
    failures.

    Implementations may raise any exception on pathological input; callers are
    expected to wrap via :func:`render_checked`. ``render`` is expected to
    raise a :class:`~.errors.MermaidError` subclass for *expected* failures
    (parse/layout/rasterize/timeout/unsupported) so they pass through
    unchanged.
    """

    def render(self, source: str, params: RenderParams) -> RenderedDiagram:
        """Render ``source`` to a PNG using ``params``.

        Raises a :class:`~.errors.MermaidError` subclass on expected failure;
        any other exception is treated as a panic by :func:`render_checked`.
        """
        ...  # pragma: no cover — protocol body


def render_checked(
    engine: MermaidEngine,
    source: str,
    params: RenderParams,
    limits: RenderLimits,
) -> RenderedDiagram:
    """Render ``source`` with ``engine``, enforcing ``limits`` and isolating failures.

    This is the entry point a caller (e.g. a render worker) should use over
    ``engine.render`` directly:

    * Source whose **byte** length exceeds :attr:`RenderLimits.max_source_bytes`
      is rejected with :class:`~.errors.MermaidUnsupportedError` **without
      invoking the engine**.
    * A raised :class:`~.errors.MermaidError` is re-raised unchanged (the
      "expected" channel).
    * Any other :class:`Exception` the engine raises is caught and re-raised as
      :class:`~.errors.MermaidPanicError` carrying ``str(exc)`` (the "panic"
      channel). ``BaseException`` subclasses propagate — they are not engine
      panics.

    Note on panic isolation
    -----------------------

    grok's ``catch_unwind`` only intercepts panics under ``panic = "unwind"``;
    under ``panic = "abort"`` a panicking engine aborts the whole process. The
    Python analogue (``except Exception``) similarly cannot contain a hard
    crash (segfault in native code, ``os._exit``). True crash-isolation over
    untrusted source comes from running the engine *out of process*; this
    in-process guard still upgrades an unexpected Python exception to a clean
    error rather than letting it escape the render call.
    """
    source_bytes = len(source.encode("utf-8"))
    if source_bytes > limits.max_source_bytes:
        raise MermaidUnsupportedError(
            f"source is {source_bytes} bytes, over the {limits.max_source_bytes}-byte limit"
        )

    try:
        return engine.render(source, params)
    except MermaidError:
        # Expected engine failure (parse/layout/rasterize/timeout/unsupported):
        # pass through verbatim, mirroring grok's ``Ok(Err(e)) => Err(e)`` arm.
        raise
    except Exception as exc:  # noqa: BLE001 — mirror catch_unwind: anything else is a panic.
        # The message can embed untrusted source fragments, so callers should
        # keep it out of default-visible logs (grok logs it at ``debug``).
        raise MermaidPanicError(str(exc)) from exc
