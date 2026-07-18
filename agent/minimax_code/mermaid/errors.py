"""Mermaid render errors (R38).

Ports the **error taxonomy** of grok's ``xai-grok-mermaid`` — why a diagram
failed to render. grok models this as a ``thiserror::Error`` enum
(:rust:src:`MermaidError`); per the R32 policy (a thiserror enum → an exception
hierarchy) it is mapped to a base :class:`MermaidError` with one subclass per
Rust variant.

Every variant maps to the same user-facing outcome (fall back to the source
code block); they differ only for observability and for callers that want to
react to a specific failure mode (e.g. retry on ``Timeout``). The carried
``message`` is the human-readable detail; ``Timeout`` carries none.

Mapping
-------

The 6 Rust variants → 6 subclasses, each preserving the variant's carried
``String`` payload as its ``message``:

  =============== ===================== ===============
  Rust variant    Python class          Carries message
  =============== ===================== ===============
  ``Parse``       :class:`MermaidParseError`          yes
  ``Layout``      :class:`MermaidLayoutError`         yes
  ``Rasterize``   :class:`MermaidRasterizeError`      yes
  ``Timeout``     :class:`MermaidTimeoutError`        no
  ``Unsupported`` :class:`MermaidUnsupportedError`    yes
  ``Panic``       :class:`MermaidPanicError`          yes
  =============== ===================== ===============

``Display`` (the ``#[error("…")]`` format) is mirrored by each subclass's
``__str__`` so the ``to_string()`` of a Python instance matches the Rust
``Display`` output (the assertion the grok tests make about "a distinguishing
word + interpolated payload").

Product fusion
--------------

A future wiring round renders model mermaid output over untrusted source.
The error split lets the back end degrade honestly — surface ``Parse`` vs
``Timeout`` vs ``Panic`` in telemetry and in the code-block fallback reason
rather than a generic "render failed", so a flaky renderer (timeout) is not
confused with a malformed diagram (parse).
"""

from __future__ import annotations

__all__ = [
    "MermaidError",
    "MermaidParseError",
    "MermaidLayoutError",
    "MermaidRasterizeError",
    "MermaidTimeoutError",
    "MermaidUnsupportedError",
    "MermaidPanicError",
]


class MermaidError(Exception):
    """Why a diagram failed to render.

    Base of the mermaid error taxonomy (grok ``MermaidError``). Callers should
    normally catch this base class; subclasses exist for observability and for
    callers that branch on a specific failure mode. Every subclass produces a
    user-facing "fall back to the source code block" outcome.
    """


class _MessageError(MermaidError):
    """Base for variants that carry a human-readable detail string.

    Mirrors grok variants shaped ``Variant(String)``: the payload round-trips
    verbatim through :meth:`message` and is interpolated into ``__str__``.
    Subclasses set :attr:`_KIND` to the distinguishing word used in the
    ``Display`` format (e.g. ``"parse"``, ``"layout"``).
    """

    _KIND: str = ""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self._message = message

    @property
    def message(self) -> str:
        """The carried detail string (round-trips the Rust ``String`` payload)."""
        return self._message

    def __str__(self) -> str:
        return f"mermaid {self._KIND} error: {self._message}"


class MermaidParseError(_MessageError):
    """The source could not be parsed into a diagram (grok ``Parse``)."""

    _KIND = "parse"


class MermaidLayoutError(_MessageError):
    """The diagram parsed but layout failed (grok ``Layout``)."""

    _KIND = "layout"


class MermaidRasterizeError(_MessageError):
    """The SVG could not be rasterized to PNG (grok ``Rasterize``)."""

    _KIND = "rasterize"


class MermaidTimeoutError(MermaidError):
    """An external engine exceeded its wall-clock budget (grok ``Timeout``).

    Carries no message — a timeout has no detail beyond itself. ``__str__``
    mirrors grok's fixed ``"mermaid render timed out"`` Display.
    """

    def __str__(self) -> str:
        return "mermaid render timed out"


class MermaidUnsupportedError(_MessageError):
    """The engine cannot render this input (grok ``Unsupported``).

    Unknown/exotic diagram, disabled engine, or a breached resource limit such
    as oversized source (raised by :func:`~.engine.render_checked` before the
    engine runs).
    """

    _KIND = "unsupported"


class MermaidPanicError(_MessageError):
    """The engine raised an unexpected exception (grok ``Panic``).

    grok catches a Rust panic via ``catch_unwind`` and converts it to this
    variant; the Python :func:`~.engine.render_checked` analogue wraps any
    non-:class:`MermaidError` exception raised by the engine as this error,
    carrying ``str(exc)``. Mirrors grok's panic-isolation contract for
    untrusted source.
    """

    _KIND = "engine panicked"
