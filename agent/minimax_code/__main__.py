"""Entry point for `python -m minimax_code`.

Boots logging, config, then either:

- ``--http`` (default): uvicorn + FastAPI serving ``POST /rpc`` and
  ``GET /ws`` on ``127.0.0.1:<port>`` (default 8765).
- ``--stdio``: the original line-delimited JSON-RPC server over
  stdio. Kept for tests and CLI debugging.

The two modes share the handler registry: the same
``register_app_handlers(server)`` lights up both transports. Don't
run them in the same process — they share stdout and the event
listener list, so dual-mode would interleave.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import TYPE_CHECKING, Any

from .app import register_app_handlers
from .config import Config
from .ipc.server import IPCServer
from .logging_setup import configure_logging

if TYPE_CHECKING:
    from .crash import CrashReport

# Default port for the HTTP + WebSocket transport. Overridable
# via ``--http-port`` or ``MINIMAX_CODE_HTTP_PORT``. The host
# stays at ``127.0.0.1`` for v0.2.0 — do not bind ``0.0.0.0``.
DEFAULT_HTTP_PORT = 8765
DEFAULT_HTTP_HOST = "127.0.0.1"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="minimax-code-agent",
        description="MiniMax Code Python agent (HTTP/WS bridge + stdio fallback)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Override log level (defaults to MINIMAX_CODE_LOG_LEVEL env).",
    )
    # Transport selection. ``--http`` is the default for v0.2.0.
    # ``--stdio`` keeps the old line-delimited JSON-RPC server for
    # tests + ad-hoc CLI debugging.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--http",
        dest="mode",
        action="store_const",
        const="http",
        default="http",
        help="Run the HTTP + WebSocket server (default).",
    )
    mode.add_argument(
        "--stdio",
        dest="mode",
        action="store_const",
        const="stdio",
        help="Run the stdio JSON-RPC server (for tests / CLI debugging).",
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=None,
        help=(
            "TCP port for the HTTP + WebSocket server "
            f"(default: env MINIMAX_CODE_HTTP_PORT or {DEFAULT_HTTP_PORT})."
        ),
    )
    parser.add_argument(
        "--http-host",
        default=None,
        help=(
            "Bind address for the HTTP + WebSocket server "
            f"(default: env MINIMAX_CODE_HTTP_HOST or {DEFAULT_HTTP_HOST}; "
            "do NOT bind 0.0.0.0 in v0.2.0)."
        ),
    )
    return parser.parse_args(argv)


async def amain(config: Config, mode: str, port: int, host: str) -> int:
    # Eagerly bring up the runtime singletons (progress tracker,
    # sessions DAO, sub-agent LLM, …) before registering handlers
    # so that the very first ``agent.invoke`` request finds the
    # injected ``MiniMaxClient`` in
    # :func:`minimax_code.orchestrator.subagent.get_subagent_runtime`
    # instead of falling back to the deterministic stub.
    # ``init_runtime`` is idempotent and async-safe.
    try:
        from .app import init_runtime

        await init_runtime()
    except Exception:
        # Storage may be disabled (MINIMAX_CODE_NO_DB=1) or the
        # DB may be briefly unavailable at boot — both are fine,
        # the handlers will lazily retry on first use.
        pass
    server = IPCServer(config=config, stdin=sys.stdin, stdout=sys.stdout)
    register_app_handlers(server)

    if mode == "stdio":
        await server.run_forever()
        return 0

    # HTTP mode. uvicorn is blocking, so we run it in a thread
    # executor and keep this coroutine as a placeholder for any
    # async lifecycle work the team adds later.
    import uvicorn

    from .http_server import build_app

    app = build_app(server)
    # Stash the FastAPI app so mobile push handlers can reach the WSManager
    from .app import set_http_app
    set_http_app(app)
    config_uv = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=config.log_level.lower(),
        access_log=False,
    )
    server_uv = uvicorn.Server(config_uv)

    # Install signal handlers for graceful shutdown.
    # uvicorn installs its own SIGINT handler that sets
    # ``should_exit``; we just make sure the process doesn't
    # hard-crash on SIGTERM.
    import signal

    def _sigterm_handler(signum: int, frame: Any) -> None:
        logger.info("received SIGTERM, triggering graceful shutdown")
        server_uv.should_exit = True

    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, _sigterm_handler)
    except (NotImplementedError, OSError):
        # Windows doesn't support add_signal_handler; fall back
        # to signal.signal which works but is less precise.
        signal.signal(signal.SIGTERM, _sigterm_handler)

    # Print the canonical "listening on …" line so the dev
    # workflow scripts (and the architecture doc) can grep for it.
    logger = __import__("logging").getLogger("minimax_code")
    logger.info(
        "agent server listening on http://%s:%d", host, port
    )
    # Echo to stdout too so `python -m minimax_code | head` still
    # shows the line. uvicorn's log config doesn't get to stdout
    # until the first request, and we want it up front.
    print(f"agent server listening on http://{host}:{port}", flush=True)
    await server_uv.serve()
    return 0


def _wire_xai_crash_handler(app_version: str) -> CrashReport | None:
    """Install the xai-crash-handler CrashBlob layer + recover the previous
    session's structured crash report (R230, app-startup wiring).

    Complementary to R12's marker-file protocol in :func:`cli_entry`:

    * R12 (``runtime.crash_detect``) owns the *marker* lifecycle -- did the
      previous run exit cleanly? -- plus the fatal-signal
      ``last_fault.trace`` file sink, and feeds orphan-run recovery.
    * This layer (``crash`` package, R225-R229) owns the *CrashBlob* capture:
      :data:`sys.excepthook` / :data:`threading.excepthook` persist an
      uncaught Python exception as ``crashes/last-crash.json``, and on the
      next boot :func:`~minimax_code.crash.check_previous_crash` reads it
      back into a structured :class:`~minimax_code.crash.CrashReport`
      (signal name + faulting address + app version + backtrace).

    Faulthandler coordination is the one real overlap: ``crash.install``
    re-enables faulthandler with the stderr default (R225-R229's
    fatal-signal safety net), which would reset R12's ``last_fault.trace``
    file sink + ``all_threads=True``. R12's ``install_faulthandler`` is
    re-run immediately after so the file sink wins -- R12 keeps its
    traceback destination, this layer keeps its excepthook replacements.
    The two subsystems do not otherwise touch (R12 never replaces
    ``sys.excepthook``; this layer never touches the marker file), so they
    coexist as the honest "did it crash?" (R12 marker) + "what crashed?"
    (CrashBlob) pair.

    Fail-open like R12: any install / recovery failure is swallowed and
    logged at debug level so a crash-subsystem issue never blocks boot.

    :param app_version: version stamped into each persisted crash record.
    :return: the previous session's :class:`~minimax_code.crash.CrashReport`
        if one was recovered, else ``None``.
    """
    import logging
    from pathlib import Path

    from .crash import CrashHandlerConfig, check_previous_crash, install
    from .runtime.crash_detect import install_faulthandler
    from .storage.db import default_data_dir

    logger = logging.getLogger(__name__)
    crash_dir = Path(default_data_dir()) / "crashes"
    try:
        install(CrashHandlerConfig(app_version=app_version, crash_dir=crash_dir))
        # Restore R12's last_fault.trace file sink: crash.install above
        # called faulthandler.enable() (stderr default), resetting R12's
        # file sink + all_threads=True. Re-run install_faulthandler LAST so
        # R12's configuration wins; the excepthook replacements above are
        # independent of faulthandler and stay armed.
        install_faulthandler(crash_dir.parent)
        previous = check_previous_crash(crash_dir)
        if previous is not None:
            logger.warning(
                "previous session crash recovered: signal=%s addr=%#x "
                "version=%s report=%s",
                previous.signal_name,
                previous.faulting_address,
                previous.app_version,
                previous.report_path,
            )
        return previous
    except Exception:
        logger.debug(
            "xai-crash-handler wiring failed (fail-open)", exc_info=True
        )
        return None


def cli_entry() -> None:
    """Synchronous entry point registered as a console_script."""
    args = parse_args()
    config = Config.from_env()
    if args.log_level:
        config = config.model_copy(update={"log_level": args.log_level})
    configure_logging(config.log_level)

    # R12 — crash detection. Install faulthandler (captures segfault
    # tracebacks to last_fault.trace), drop a dirty-start marker, and arm
    # ``atexit`` to clear it on graceful exit. ``atexit`` does NOT fire on
    # hard crashes (OOM / segfault / ``kill -9``), so a leftover marker on
    # the next boot means the previous run died unexpectedly →
    # ``_run_crash_recovery`` (called from ``_maybe_open_db``) consumes it
    # and recovers orphan runs. Fail-open: a missing storage layer or a
    # locked data dir never blocks boot — the agent still starts.
    try:
        import atexit
        from pathlib import Path

        from .runtime.crash_detect import (
            install_faulthandler,
            mark_clean_exit,
            mark_dirty_start,
        )
        from .storage.db import default_database_path

        _data_dir = Path(default_database_path()).parent
        install_faulthandler(_data_dir)
        mark_dirty_start(_data_dir)
        atexit.register(mark_clean_exit, _data_dir)
    except Exception:
        import logging

        logging.getLogger(__name__).debug(
            "crash-detection install failed (fail-open)", exc_info=True
        )

    # R230 — xai-crash-handler CrashBlob layer (write + read half).
    # Complementary to R12 above: installs sys.excepthook /
    # threading.excepthook replacements that persist an uncaught Python
    # exception as crashes/last-crash.json, and reads back the previous
    # session's structured CrashReport. _wire_xai_crash_handler re-runs
    # R12's install_faulthandler so the faulthandler file sink survives
    # crash.install's stderr-default re-enable.
    from . import __version__

    _wire_xai_crash_handler(__version__)

    if args.mode == "http":
        port = (
            args.http_port
            if args.http_port is not None
            else int(os.environ.get("MINIMAX_CODE_HTTP_PORT", str(DEFAULT_HTTP_PORT)))
        )
        host = (
            args.http_host
            if args.http_host is not None
            else os.environ.get("MINIMAX_CODE_HTTP_HOST", DEFAULT_HTTP_HOST)
        )
    else:
        port = DEFAULT_HTTP_PORT
        host = DEFAULT_HTTP_HOST

    try:
        rc = asyncio.run(amain(config, args.mode, port=port, host=host))
    except KeyboardInterrupt:
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    cli_entry()
