"""Operation timer that logs START/FINISHED/FAILED boundaries (R127).

Fusion of grok-build's ``xai-tracing/src/timer.rs``. One symbol lands:
:class:`Timer` — a small RAII timer that logs the runtime of an
operation. Construct with a human-readable ``message``; the timer logs
``START`` immediately, ``FINISHED in <secs>s`` when :meth:`stop` runs,
and ``FAILED after <secs>s`` when :meth:`force_stop` runs (or when the
timer is dropped / the context exits without an explicit stop). Each
boundary line is prefixed with a ``uuid4`` so the START/FINISHED/FAILED
triple for one operation can be correlated in the log stream.

Why timer lands first
---------------------

``xai-tracing`` is a cross-cutting observability crate; this round opens
its Python landing. The crate's ``lib.rs`` re-exports six modules
(``dispatch`` / ``grpc_client`` / ``timer`` / ``fastrace`` /
``http_client`` / ``tokio``, plus the test-only ``testing``). ``timer``
is the smallest and the only leaf with *no* fastrace / opentelemetry /
tokio dependency — it is pure ``Instant`` + ``Uuid`` + the ``log`` crate.
So it is the natural first leaf: a self-contained primitive whose
Python mapping (``time.monotonic`` + ``uuid.uuid4`` + ``logging``) needs
none of the heavier observability stack the later leaves pull in.

tokio::time::Instant -> time.monotonic
--------------------------------------

Rust's :struct:`tokio::time::Instant` is a monotonic clock
(``Instant::now`` / ``elapsed``) — immune to system-wall-clock jumps, so
a timing never goes negative if the operator rolls the clock back. The
faithful Python counterpart is :func:`time.monotonic` (also monotonic,
also not affected by system-time adjustments). :func:`time.time` would
be wrong: it is a wall clock and can move backwards. The elapsed value
is kept as a Python ``float`` (f64) where Rust keeps ``f32``; the log
line formats both at ``{:.3}`` (three decimals), so the displayed
precision matches while the in-memory precision is slightly higher on
the Python side.

Rust Drop -> Python __del__ + context manager
---------------------------------------------

Rust's ``Timer`` logs ``FAILED`` from its ``Drop`` impl if it was never
explicitly stopped — a deterministic RAII guarantee (``Drop`` runs the
moment the binding goes out of scope). Python has no deterministic
destructor: ``__del__`` runs at GC time, which is unspecified and may be
suppressed entirely, and during interpreter shutdown the ``logging``
machinery may already be torn down. So the Python landing offers *two*
exits that together recover the Rust discipline:

* :meth:`__enter__` / :meth:`__exit__` — the deterministic, preferred
  path. ``__exit__`` mirrors ``Drop``: if the timer was not explicitly
  stopped inside the ``with`` block, it runs ``force_stop`` (logs
  ``FAILED``). This is the faithful counterpart to Rust's scope exit.
* :meth:`__del__` — a best-effort safety net for the case where the
  caller neither uses ``with`` nor calls ``stop`` / ``force_stop``
  explicitly. It is wrapped in ``try``/``except`` so a ``__del__``
  firing during interpreter teardown never raises.

This is a deliberate semantic downgrade from Rust's guaranteed ``Drop``
to Python's "deterministic via ``with``, best-effort via ``__del__``" —
recorded here so the loss of guarantee is explicit, not silent. Callers
who want the Rust guarantee should use the context manager or call
``stop`` / ``force_stop`` explicitly.

The ``stop`` method is generic in Rust (``stop<T>(result: T) -> T``) so
a call site can time a computation and return its value in one
expression; the Python landing mirrors that with a :data:`typing.TypeVar`
so the passed-in result is returned at the same static type.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from types import TracebackType

__all__ = ["Timer"]

_log = logging.getLogger(__name__)

#: Generic over the value :meth:`Timer.stop` returns to its caller —
#: mirrors Rust's ``stop<T>(result: T) -> T`` so a call site can time a
#: computation and hand back its result in one expression.
T = TypeVar("T")


class Timer:
    """A simple timer that logs the runtime of an operation (R127).

    Construct with a ``message``; the timer logs ``START`` immediately.
    Call :meth:`stop` to record ``FINISHED`` (and return the timed
    result through), or :meth:`force_stop` to record ``FAILED``. Both
    are idempotent — a second call is a no-op. If neither runs, the
    context-manager exit (or, as a last resort, ``__del__``) records
    ``FAILED`` — mirroring Rust's ``Drop``.

    Usage::

        with Timer("compute thing") as t:
            value = do_work()
            t.stop(value)   # logs FINISHED, returns value

    Or, without the context manager::

        t = Timer("compute thing")
        try:
            value = do_work()
            return t.stop(value)
        except Exception:
            t.force_stop()
            raise

    Each boundary line is prefixed with a ``uuid4`` so the
    START / FINISHED / FAILED triple for one operation can be
    correlated in a interleaved log stream.
    """

    __slots__ = ("_start", "_id", "_message", "_stopped")

    def __init__(self, message: str) -> None:
        """Create a timer and log ``START`` immediately (Rust ``new``).

        ``message`` is the human-readable label logged at every
        boundary. The start instant is captured from
        :func:`time.monotonic` (monotonic, immune to wall-clock jumps),
        and a fresh ``uuid4`` is generated to correlate this timer's
        boundary lines.
        """
        self._start = time.monotonic()
        self._id = uuid.uuid4()
        self._message = message
        self._stopped = False
        _log.info("[%s] START: %s", self._id, self._message)

    def stop(self, result: T) -> T:
        """Stop the timer, log ``FINISHED``, and return ``result`` (Rust ``stop``).

        Idempotent: the first call logs ``FINISHED in <secs>s`` and flips
        the stopped flag; subsequent calls are silent and just return
        ``result``. The passed-in ``result`` is returned unchanged so a
        call site can time a computation and yield its value in one
        expression (``return t.stop(value)``).
        """
        if not self._stopped:
            runtime = time.monotonic() - self._start
            _log.info(
                "[%s] FINISHED in %.3fs: %s", self._id, runtime, self._message
            )
            self._stopped = True
        return result

    def force_stop(self) -> None:
        """Stop the timer prematurely and log ``FAILED`` (Rust ``force_stop``).

        Idempotent: the first call logs ``FAILED after <secs>s`` and
        flips the stopped flag; subsequent calls are silent. Use this on
        the error path of a timed operation, or rely on the context
        manager / ``__del__`` to call it when ``stop`` was never run.
        """
        if not self._stopped:
            runtime = time.monotonic() - self._start
            _log.error(
                "[%s] FAILED after %.3fs: %s", self._id, runtime, self._message
            )
            self._stopped = True

    def __enter__(self) -> Timer:
        """Enter the deterministic RAII scope (preferred over ``__del__``)."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the scope: ``force_stop`` if never stopped (mirrors Rust ``Drop``).

        Whether the ``with`` block completed normally or raised, if
        :meth:`stop` / :meth:`force_stop` was not called inside it, this
        records ``FAILED`` — the faithful counterpart to Rust's ``Drop``
        running ``force_stop``. Returning ``None`` never suppresses an
        exception raised inside the block.
        """
        if not self._stopped:
            self.force_stop()

    def __del__(self) -> None:
        """Best-effort ``force_stop`` if never stopped (last-resort ``Drop`` mirror).

        Unlike Rust's deterministic ``Drop``, Python's ``__del__`` runs
        at GC time and may be suppressed or fire during interpreter
        teardown (when ``logging`` may already be gone). The
        :keyword:`try`/:keyword:`except` keeps teardown safe. Prefer the
        context manager or an explicit :meth:`stop` / :meth:`force_stop`
        for the Rust guarantee.
        """
        try:
            if not self._stopped:
                self.force_stop()
        except Exception:
            pass
