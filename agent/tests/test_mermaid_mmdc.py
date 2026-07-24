"""Tests for the optional ``mmdc`` engine (R278d) -- the behavioral-equivalent
port of grok's ``mmdc.rs`` plus the crate-root ``default_engine()`` factory.

Coverage mirrors the contract surface of :mod:`minimax_code.mermaid.mmdc` and
the grok semantics it preserves:

* **Barrel surface** -- the package root re-exports ``MmdcEngine`` /
  ``detect_mmdc`` / ``default_engine`` (grok ``lib.rs`` L54--L55 + L197--L203),
  and the module's own ``__all__`` lists exactly its two public symbols.
* **Pure accessors / mappers** -- ``DEFAULT_MMDC_TIMEOUT`` (1.5s, grok
  ``Duration::from_millis(1500)``), ``_theme_arg`` (LIGHT->"default" /
  DARK->"dark"), ``binary`` round-trip, ``with_timeout`` builder ergonomics,
  and the 4-branch ``_map_subprocess_error`` dispatch (Spawn -> Unsupported,
  Timeout -> Timeout, NonZeroExit -> Layout, Wait -> Rasterize).
* **End-to-end ``render``** -- a missing binary surfaces as
  :class:`MermaidUnsupportedError` (cross-platform real spawn); the
  ``with_timeout`` value reaches the runner; the raster YAGNI sentinel fires
  after a successful SVG half.
* **``detect()`` classmethod** -- builds an engine only when ``mmdc`` is on
  ``PATH``.
* **``default_engine()`` factory** -- always the offline
  :class:`PureRustEngine`, never ``mmdc`` (the standing "off by default"
  contract), and protocol-satisfying.
* **Unix-only fake-``mmdc`` suite** -- a ``#!/bin/sh`` stand-in for ``mmdc``
  (``$4`` = output path, ``$8`` = theme) drives each branch of the subprocess
  error taxonomy: success -> raster sentinel, zero-exit-no-output -> Layout,
  non-zero exit -> Layout, timeout -> Timeout. Skipped on Windows (no POSIX
  shell); the cross-platform tests above run everywhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from minimax_code.mermaid import (
    MermaidEngine,
    MermaidLayoutError,
    MermaidRasterizeError,
    MermaidTimeoutError,
    MermaidUnsupportedError,
    MmdcEngine,
    PureRustEngine,
    default_engine,
    detect_mmdc,
)
from minimax_code.mermaid import (
    mmdc as mmdc_mod,
)
from minimax_code.mermaid.subprocess import (
    NonZeroExitSubprocessError,
    SpawnSubprocessError,
    TimeoutSubprocessError,
    WaitSubprocessError,
)
from minimax_code.mermaid.types import MermaidTheme, RenderParams

# --------------------------------------------------------------------------
# Barrel surface
# --------------------------------------------------------------------------


def test_barrel_exposes_mmdc_engine_detect_and_default_engine() -> None:
    """The package root re-exports the mmdc surface (grok lib.rs L54-L55 + L197-L203)."""
    import minimax_code.mermaid as mermaid

    assert len(mermaid.__all__) == 27
    assert "MmdcEngine" in mermaid.__all__
    assert "detect_mmdc" in mermaid.__all__
    assert "default_engine" in mermaid.__all__
    assert "PureRustEngine" in mermaid.__all__
    # The barrel aliases bind to the canonical objects (no shadow copies).
    assert mermaid.MmdcEngine is MmdcEngine
    assert mermaid.detect_mmdc is detect_mmdc
    assert mermaid.default_engine is default_engine


def test_module_all_lists_public_surface() -> None:
    """The module exports exactly its two public symbols (grok L54 re-export shape)."""
    assert mmdc_mod.__all__ == ["MmdcEngine", "detect_mmdc"]


def test_default_timeout_matches_grok() -> None:
    """``DEFAULT_MMDC_TIMEOUT`` mirrors grok ``Duration::from_millis(1500)``."""
    assert mmdc_mod.DEFAULT_MMDC_TIMEOUT == 1.5


# --------------------------------------------------------------------------
# Pure accessors and mappers
# --------------------------------------------------------------------------


def test_theme_arg_maps_light_to_default() -> None:
    assert mmdc_mod._theme_arg(MermaidTheme.LIGHT) == "default"


def test_theme_arg_maps_dark_to_dark() -> None:
    assert mmdc_mod._theme_arg(MermaidTheme.DARK) == "dark"


def test_binary_accessor_round_trips() -> None:
    """``binary`` returns the path the engine was constructed with (grok ``binary``)."""
    assert MmdcEngine("/usr/local/bin/mmdc").binary == "/usr/local/bin/mmdc"


def test_with_timeout_builder_sets_timeout_and_returns_self() -> None:
    """The builder mutates the timeout and returns self for chained ergonomics (grok ``with_timeout``)."""
    engine = MmdcEngine("/p")
    # grok's builder consumes and returns self; the port returns the same instance.
    assert engine.with_timeout(7.5) is engine


def test_map_subprocess_error_spawn_to_unsupported() -> None:
    """A spawn failure means the engine is unavailable (Unsupported, not bad input)."""
    mapped = mmdc_mod._map_subprocess_error(SpawnSubprocessError(OSError("boom")))
    assert isinstance(mapped, MermaidUnsupportedError)
    assert "could not spawn mmdc" in str(mapped)


def test_map_subprocess_error_timeout_to_timeout() -> None:
    """A wall-clock breach is its own error category."""
    mapped = mmdc_mod._map_subprocess_error(TimeoutSubprocessError())
    assert isinstance(mapped, MermaidTimeoutError)


def test_map_subprocess_error_nonzeroexit_to_layout() -> None:
    """A non-zero exit is a render / layout failure."""
    mapped = mmdc_mod._map_subprocess_error(NonZeroExitSubprocessError(3))
    assert isinstance(mapped, MermaidLayoutError)
    assert "exited with 3" in str(mapped)


def test_map_subprocess_error_wait_to_rasterize() -> None:
    """A wait failure is a pipeline (Rasterize) error."""
    mapped = mmdc_mod._map_subprocess_error(WaitSubprocessError(RuntimeError("x")))
    assert isinstance(mapped, MermaidRasterizeError)
    assert "mmdc wait failed" in str(mapped)


# --------------------------------------------------------------------------
# End-to-end render (cross-platform)
# --------------------------------------------------------------------------


def test_missing_binary_is_unsupported() -> None:
    """Spawning a binary that does not exist surfaces as Unsupported (real spawn, cross-platform)."""
    engine = MmdcEngine("definitely-not-a-real-binary-9f8a7b6c5d4e")
    with pytest.raises(MermaidUnsupportedError, match="could not spawn mmdc"):
        engine.render("flowchart LR\n A-->B", RenderParams())


def test_with_timeout_propagates_to_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``with_timeout`` value reaches ``run_with_timeout`` and the theme flag is wired."""
    captured: dict[str, object] = {}

    async def fake_runner(cmd, *, timeout, **_kwargs):  # type: ignore[no-untyped-def]
        captured["timeout"] = timeout
        captured["cmd"] = list(cmd)
        # Write a valid SVG to the output path so render proceeds to the raster step.
        output = cmd[cmd.index("--output") + 1]
        Path(output).write_text("<svg></svg>", encoding="utf-8")

    monkeypatch.setattr(mmdc_mod, "run_with_timeout", fake_runner)

    engine = MmdcEngine("/usr/local/bin/mmdc").with_timeout(7.5)
    with pytest.raises(MermaidRasterizeError):
        engine.render("flowchart LR\n A-->B", RenderParams())

    assert captured["timeout"] == 7.5
    # The default (light) theme maps onto mmdc's "default" theme flag.
    cmd = captured["cmd"]
    assert cmd[cmd.index("--theme") + 1] == "default"
    # The CLI argument shape mirrors grok's mmdc invocation.
    assert cmd[cmd.index("--outputFormat") + 1] == "svg"


# --------------------------------------------------------------------------
# detect() classmethod
# --------------------------------------------------------------------------


def test_detect_returns_engine_when_mmdc_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mmdc_mod, "detect_mmdc", lambda: "/usr/local/bin/mmdc")
    engine = MmdcEngine.detect()
    assert engine is not None
    assert engine.binary == "/usr/local/bin/mmdc"


def test_detect_returns_none_when_mmdc_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mmdc_mod, "detect_mmdc", lambda: None)
    assert MmdcEngine.detect() is None


# --------------------------------------------------------------------------
# default_engine() factory (grok lib.rs L197-L203)
# --------------------------------------------------------------------------


def test_default_engine_returns_pure_rust_engine_instance() -> None:
    assert isinstance(default_engine(), PureRustEngine)


def test_default_engine_satisfies_protocol() -> None:
    """PureRustEngine satisfies the MermaidEngine protocol (runtime-checkable)."""
    assert isinstance(default_engine(), MermaidEngine)


def test_default_engine_does_not_probe_for_mmdc(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default factory never consults ``detect_mmdc`` (grok: "mmdc is never selected automatically")."""
    calls: list[int] = []

    def spy() -> str | None:
        calls.append(1)
        return "/usr/local/bin/mmdc"

    monkeypatch.setattr(mmdc_mod, "detect_mmdc", spy)
    engine = default_engine()
    assert isinstance(engine, PureRustEngine)
    assert calls == []  # detect_mmdc was never consulted.


# --------------------------------------------------------------------------
# Unix-only fake-mmdc suite (exercises every subprocess-error branch end-to-end)
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="fake mmdc uses a POSIX shell script; the cross-platform tests above run on Windows",
)
class TestFakeMmdcUnixOnly:
    """End-to-end ``render`` against a fake ``mmdc`` shell script (Unix only).

    A tiny ``#!/bin/sh`` stand-in for ``mmdc``: it writes the SVG to ``$4``
    (the ``--output`` path) and reads ``$8`` (the ``--theme`` value), mirroring
    grok's ``mmdc.rs`` integration shape. Each case exercises one branch of the
    subprocess error taxonomy the engine maps onto the host error classes.
    """

    @pytest.fixture
    def make_fake_mmdc(self, tmp_path: Path):
        """Build an executable fake ``mmdc`` script for the given behavioral mode."""
        counter = [0]

        def _make(mode: str) -> str:
            counter[0] += 1
            script = tmp_path / f"fake_mmdc_{counter[0]}.sh"
            if mode == "success":
                # Write a valid SVG to $4 so render reaches the raster step.
                body = (
                    'cat > "$4" <<\'SVG\'\n'
                    '<svg xmlns="http://www.w3.org/2000/svg"></svg>\n'
                    "SVG"
                )
            elif mode == "empty":
                # Zero exit, but no output file -> reading the SVG fails.
                body = "exit 0"
            elif mode == "fail":
                # Non-zero exit -> NonZeroExitSubprocessError -> Layout.
                body = "exit 3"
            elif mode == "slow":
                # Sleep past the wall-clock budget -> Timeout.
                body = "sleep 30"
            else:  # pragma: no cover - defensive
                raise ValueError(f"unknown mode: {mode}")
            script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
            script.chmod(0o755)
            return str(script)

        return _make

    def test_success_runs_svg_half_then_raster_sentinel(self, make_fake_mmdc) -> None:
        engine = MmdcEngine(make_fake_mmdc("success"))
        # SVG half ran (mmdc produced the SVG); raster half is the R278b YAGNI gap.
        with pytest.raises(MermaidRasterizeError):
            engine.render("flowchart LR\n A-->B", RenderParams())

    def test_zero_exit_without_output_is_layout_error(self, make_fake_mmdc) -> None:
        engine = MmdcEngine(make_fake_mmdc("empty"))
        with pytest.raises(MermaidLayoutError, match="no readable SVG"):
            engine.render("flowchart LR\n A-->B", RenderParams())

    def test_nonzero_exit_maps_to_layout(self, make_fake_mmdc) -> None:
        engine = MmdcEngine(make_fake_mmdc("fail"))
        with pytest.raises(MermaidLayoutError, match="exited with 3"):
            engine.render("flowchart LR\n A-->B", RenderParams())

    def test_timeout_maps_to_timeout(self, make_fake_mmdc) -> None:
        engine = MmdcEngine(make_fake_mmdc("slow")).with_timeout(0.1)
        with pytest.raises(MermaidTimeoutError):
            engine.render("flowchart LR\n A-->B", RenderParams())
