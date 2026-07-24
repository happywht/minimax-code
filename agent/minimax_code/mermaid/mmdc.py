"""Optional ``mmdc`` (mermaid-cli) engine -- behavioral-equivalent port of grok's ``mmdc.rs``.

Direction (1) brick 12 (R278d). Ports grok's ``mmdc.rs`` (the optional
:class:`MmdcEngine` host-shell leaf that shells out to the ``mmdc`` CLI /
headless Chromium) as a **behavioral-equivalent port**: each function's
contract -- what it does, the errors it raises, the taxonomy it exposes to
callers -- is preserved, but the implementation is idiomatic Python, NOT a
line-by-line translation of the Rust. Where grok leans on Rust language
features or sibling crates, the port reaches for the Python equivalent that
expresses the same semantics.

Behavioral-equivalence mapping (same framework as R278c)
--------------------------------------------------------

* grok ``which::which("mmdc")`` -> :func:`shutil.which` (the PATH lookup).
* grok ``MmdcEngine { bin, timeout }`` struct + ``new`` / ``detect`` /
  ``with_timeout`` / ``binary`` -> a class with the same four operations.
  grok's builder ``with_timeout(mut self, timeout) -> Self`` (consumes and
  returns self) -> a method returning a new-or-mutated instance.
* grok's ``?`` (early error return) -> ``try`` / ``except`` /
  ``raise ... from exc``.
* grok's ``match SubprocessError { ... }`` -> ``isinstance`` dispatch over
  the four subclasses, grouped by the host error category each maps to.
* grok ``write_private`` (Unix ``OpenOptions::create_new().mode(0o600)`` /
  Windows plain ``fs::write``) -> :func:`os.open` with ``O_CREAT|O_EXCL`` and
  mode ``0o600`` on Unix / :meth:`pathlib.Path.write_text` on Windows (the
  same atomic-create-with-owner-only-mode vs plain-write split).

Function-not-line decisions (the user's standing method)
--------------------------------------------------------

Per "clone by function, not line-by-line", two grok dependencies are NOT
minutely ported because their *function* is already provided by R278a's
:mod:`.subprocess`:

* ``xai_tty_utils::pager_env`` (a pager-killing env overlay) -> NOT ported as
  a separate helper. Its function -- stop the child from trying to page or
  grab a TTY -- is already covered: the child is spawned with null stdio
  (``stdin`` / ``stdout`` / ``stderr`` all :data:`~subprocess.DEVNULL`) and
  runs in batch file-mode (``--input`` / ``--output`` paths, no interactive
  stream). We pass ``env=None`` (inherit the parent environment). A future
  wiring round that finds a real ``mmdc`` / Chromium env foot-gun adds one
  overlay call here.
* ``xai_tty_utils::detach_std_command`` (``setsid`` / console detach) -> NOT
  ported separately. R278a's
  :func:`~minimax_code.mermaid.subprocess.run_with_timeout` already spawns
  with ``start_new_session=True`` (the ``setsid`` equivalent -- the child is
  its own session/group leader) and reaps the whole process group on a
  breach. The function is identical; the plumbing already lives in the
  shared runner.

Async-to-sync bridge
--------------------

grok's :rust:fn:`run_with_timeout` is synchronous (``std::thread::scope``
isolates the blocking wait). The host's
:func:`~minimax_code.mermaid.subprocess.run_with_timeout` is an asyncio
coroutine (the natural Python equivalent on this stack). But the
:class:`~minimax_code.mermaid.engine.MermaidEngine` protocol's ``render`` is
synchronous (R38; :func:`~minimax_code.mermaid.engine.render_checked` calls
``engine.render`` directly). :meth:`MmdcEngine.render` therefore stays
synchronous and drives the coroutine itself via :func:`_run_async_sync`: a
fresh event loop when none is running, or a worker thread with its own loop
when the caller is already inside a loop (so the host loop is never nested
or deadlocked). The worker-thread fallback mirrors grok's
``std::thread::scope`` isolation of the blocking wait -- the same semantic,
just triggered by Python's no-nested-runloop rule rather than by Rust's
borrow of a blocking wait.

Raster half -- YAGNI sentinel (R278b symmetry)
----------------------------------------------

grok's ``MmdcEngine::render`` ends with ``crate::rasterize(&svg, params)``:
the child produces the SVG, then the shared pure-Rust raster stack turns it
into PNG bytes. That raster stack is unportable (R278b: no pure-Python
``resvg`` / ``usvg`` / ``tiny-skia`` / ``fontdb`` equivalent; no Rust
toolchain shipped here; PNG is optional -- the React front end renders SVG).
So :meth:`MmdcEngine.render` runs the SVG half (spawn ``mmdc``, read the
emitted SVG) and then raises :class:`~minimax_code.mermaid.errors.MermaidRasterizeError`
at the raster step rather than fabricating PNG bytes -- exactly symmetric
with :class:`~minimax_code.mermaid.pure.PureRustEngine.render`. The SVG path
is exercised on every call (it runs before the raster step raises), so the
engine is not dead code.

Public surface (2 symbols): :class:`MmdcEngine` + :func:`detect_mmdc` (grok
``lib.rs`` L54 ``pub use mmdc::{MmdcEngine, detect_mmdc}``).
``default_engine`` lives in :mod:`.mermaid` (the package root, mirroring grok
``lib.rs`` the crate root) rather than here.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import NoReturn

from .errors import (
    MermaidError,
    MermaidLayoutError,
    MermaidRasterizeError,
    MermaidTimeoutError,
    MermaidUnsupportedError,
)
from .subprocess import (
    NonZeroExitSubprocessError,
    SpawnSubprocessError,
    SubprocessError,
    TimeoutSubprocessError,
    WaitSubprocessError,
    run_with_timeout,
)
from .types import MermaidTheme, RenderedDiagram, RenderParams

__all__ = ["MmdcEngine", "detect_mmdc"]

#: Default wall-clock budget (seconds) for an ``mmdc`` invocation.
#: grok ``DEFAULT_MMDC_TIMEOUT = Duration::from_millis(1500)``.
DEFAULT_MMDC_TIMEOUT: float = 1.5

#: Unix-only flag (mirrors grok's ``#[cfg(unix)]`` branches).
_IS_UNIX: bool = sys.platform != "win32"


def detect_mmdc() -> str | None:
    """Locate the ``mmdc`` binary on ``PATH``, if installed (grok ``detect_mmdc``).

    Returns the resolved path as a string, or ``None`` if ``mmdc`` is not on
    the ``PATH``. Mirrors grok's ``which::which("mmdc").ok()`` -- the PATH
    lookup with a clean "not found" sentinel rather than an exception.
    """
    return shutil.which("mmdc")


def _theme_arg(theme: MermaidTheme) -> str:
    """Map the host theme onto the ``mmdc`` ``--theme`` flag value (grok ``theme_arg``).

    ``mmdc`` accepts ``default`` (light) or ``dark``; the host's coarse
    light/dark split maps directly. grok ``MermaidTheme::Light => "default"`` /
    ``MermaidTheme::Dark => "dark"``.
    """
    if theme is MermaidTheme.LIGHT:
        return "default"
    # MermaidTheme.DARK (the only other variant).
    return "dark"


def _map_subprocess_error(exc: SubprocessError) -> MermaidError:
    """Map a subprocess failure onto the engine error taxonomy (grok ``map_subprocess_error``).

    A spawn failure means ``mmdc`` is unavailable
    (:class:`MermaidUnsupportedError` -- "this engine isn't usable here", not
    "this input is bad"); a timeout is its own category; a non-zero exit is a
    render / layout failure; a wait failure is a pipeline
    (:class:`MermaidRasterizeError`) error.
    """
    if isinstance(exc, SpawnSubprocessError):
        return MermaidUnsupportedError(f"could not spawn mmdc: {exc.cause}")
    if isinstance(exc, TimeoutSubprocessError):
        return MermaidTimeoutError()
    if isinstance(exc, NonZeroExitSubprocessError):
        return MermaidLayoutError(f"mmdc exited with {exc.returncode}")
    if isinstance(exc, WaitSubprocessError):
        return MermaidRasterizeError(f"mmdc wait failed: {exc.cause}")
    # Defensive: SubprocessError has exactly these 4 variants (grok's ``match``
    # is exhaustive; the fallthrough is unreachable for a well-typed error but
    # keeps the mapping total so a future variant surfaces as Rasterize rather
    # than leaking the raw SubprocessError).
    return MermaidRasterizeError(f"mmdc wait failed: {exc}")


def _write_private(path: Path, contents: str) -> None:
    """Write ``contents`` to ``path`` owner-only (grok ``write_private``).

    On Unix the file is created atomically with mode ``0o600`` via
    ``O_CREAT | O_EXCL`` so there is no umask / chmod TOCTOU window (mirrors
    grok's ``OpenOptions::create_new().mode(0o600)``); the parent temp dir is
    already ``0700`` (:func:`tempfile.mkdtemp` default). On Windows grok's
    ``not(unix)`` branch is a plain ``fs::write`` -- mirrored by a plain
    :meth:`pathlib.Path.write_text` (no Unix mode bits exist).
    """
    if _IS_UNIX:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                fp.write(contents)
        except BaseException:
            # fdopen succeeded: it owns the fd and closed it. fdopen failed:
            # close the raw fd we opened before propagating.
            try:
                os.close(fd)
            except OSError:
                pass
            raise
    else:
        path.write_text(contents, encoding="utf-8")


def _run_async_sync(coro):  # type: ignore[no-untyped-def]
    """Drive an async coroutine to completion from synchronous code.

    The host's :func:`~minimax_code.mermaid.subprocess.run_with_timeout` is an
    asyncio coroutine, but the :class:`~minimax_code.mermaid.engine.MermaidEngine`
    ``render`` protocol is synchronous. This helper bridges the two by running
    the coroutine on a fresh event loop. When a loop is already running on
    this thread (``render`` called from async host code), it runs the
    coroutine in a worker thread with its own loop so the host loop is
    neither nested nor deadlocked -- mirroring grok's ``std::thread::scope``
    isolation of the blocking wait. Coroutine exceptions propagate to the
    caller verbatim.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop running on this thread: drive one here (the common path).
        return asyncio.run(coro)

    # A loop is already running (nested call from async host code). Run in a
    # worker thread so we neither nest loops nor block the host loop.
    box: dict[str, object] = {}

    def _runner() -> None:
        try:
            box["result"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 -- propagate verbatim.
            box["exc"] = exc

    worker = threading.Thread(target=_runner)
    worker.start()
    worker.join()
    if (exc := box.get("exc")) is not None:
        raise exc  # type: ignore[misc]
    return box.get("result")


class MmdcEngine:
    """An engine that shells out to ``mmdc`` (mermaid-cli) (grok ``MmdcEngine``).

    Off by default: it requires Node + headless Chromium, so a caller must
    construct it explicitly (via :meth:`detect` to build one only if
    ``mmdc`` is present, or :meth:`__init__` with a known binary path).
    """

    __slots__ = ("_bin", "_timeout")

    def __init__(self, binary: str) -> None:
        """Build an engine that runs the ``mmdc`` binary at ``binary`` (grok ``new``).

        The timeout defaults to :data:`DEFAULT_MMDC_TIMEOUT`; override it with
        :meth:`with_timeout`.
        """
        self._bin = binary
        self._timeout = DEFAULT_MMDC_TIMEOUT

    @classmethod
    def detect(cls) -> MmdcEngine | None:
        """Build an engine if (and only if) ``mmdc`` is found on ``PATH`` (grok ``detect``).

        Returns ``None`` when ``mmdc`` is absent, so a caller can fall back to
        the default engine without a try/except.
        """
        found = detect_mmdc()
        if found is None:
            return None
        return cls(found)

    def with_timeout(self, timeout: float) -> MmdcEngine:
        """Override the wall-clock timeout (grok ``with_timeout``).

        grok's builder consumes and returns ``self``; the Python port mutates
        the timeout in place and returns ``self`` for the same chained-call
        ergonomics (``MmdcEngine.new(bin).with_timeout(t).render(...)``).
        """
        self._timeout = timeout
        return self

    @property
    def binary(self) -> str:
        """The resolved ``mmdc`` binary path (grok ``binary`` accessor)."""
        return self._bin

    def render(self, source: str, params: RenderParams) -> RenderedDiagram:
        """Render mermaid ``source`` under ``params`` via the ``mmdc`` CLI (grok ``render``).

        Writes the source to a private temp file (mode ``0600``), spawns
        ``mmdc`` to emit an SVG, reads the SVG back, then rasterizes it.

        Raises:
            MermaidUnsupportedError: ``mmdc`` could not be spawned (missing /
                not executable).
            MermaidTimeoutError: ``mmdc`` exceeded its wall-clock budget.
            MermaidLayoutError: ``mmdc`` exited non-zero, or produced no
                readable SVG output.
            MermaidRasterizeError: the SVG could not be rasterized to PNG
                (always, until a Python rasterizer is wired in -- R278b), or
                the source temp file could not be written, or the wait on the
                child failed.
        """
        dir_path = Path(tempfile.mkdtemp(prefix="xai-mermaid-"))
        input_path = dir_path / "diagram.mmd"
        output_path = dir_path / "diagram.svg"
        try:
            # Source via a private temp file (0600 on Unix). An IO failure
            # here is a pipeline (Rasterize) error, not Unsupported (which
            # connotes "engine unavailable") -- mirrors grok's mapping.
            try:
                _write_private(input_path, source)
            except OSError as exc:
                raise MermaidRasterizeError(
                    f"could not write source: {exc}"
                ) from exc

            # The child reads the source from the temp file and writes the SVG
            # to the output file; null stdio (the runner's default) + batch
            # file-mode covers grok's pager_env / TTY-detach function (see the
            # module docstring's function-not-line decisions).
            cmd = [
                self._bin,
                "--input",
                str(input_path),
                "--output",
                str(output_path),
                "--outputFormat",
                "svg",
                "--theme",
                _theme_arg(params.theme),
            ]
            try:
                _run_async_sync(run_with_timeout(cmd, timeout=self._timeout))
            except SubprocessError as exc:
                raise _map_subprocess_error(exc) from exc

            # Read the emitted SVG back. The contents are consumed by the raster
            # step (below); the read itself is the failure surface -- if ``mmdc``
            # wrote nothing (zero exit, no output), this raises OSError -> Layout.
            try:
                output_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise MermaidLayoutError(
                    f"mmdc produced no readable SVG output: {exc}"
                ) from exc

            # Raster half (YAGNI, R278b): grok calls
            # ``crate::rasterize(&svg, params)`` to produce the PNG. The
            # pure-Rust raster stack is unportable and PNG is optional here,
            # so raise the typed sentinel rather than fabricating PNG bytes --
            # symmetric with PureRustEngine.render. A future wiring round that
            # adopts a Python rasterizer replaces this one statement.
            raise MermaidRasterizeError(
                "SVG -> PNG rasterization is not available in the Python port "
                "(R278b): the pure-Rust resvg/usvg/tiny-skia/fontdb stack has "
                "no pure-Python equivalent, and PNG is an optional output"
            )
        finally:
            # Best-effort teardown of the temp dir (grok's TempDir drops on
            # return; Python has no RAII dir, so rmtree in finally).
            shutil.rmtree(dir_path, ignore_errors=True)


def _mmdc_engine_is_send_safe() -> NoReturn:
    """Placeholder documenting grok's ``Send + Sync`` bound (not ported).

    grok's ``lib.rs`` test ``default_engine_is_constructible_and_send_sync``
    asserts ``Arc<dyn MermaidEngine>: Send + Sync``. Python's GIL + reference
    counting make every object trivially thread-safe across the GIL boundary,
    so there is no Python equivalent to assert. The function exists only to
    document the asymmetry; it is deliberately unreachable.
    """
    raise NotImplementedError("documented asymmetry; never called")
