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

from .app import register_app_handlers
from .config import Config
from .ipc.server import IPCServer
from .logging_setup import configure_logging

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


def cli_entry() -> None:
    """Synchronous entry point registered as a console_script."""
    args = parse_args()
    config = Config.from_env()
    if args.log_level:
        config = config.model_copy(update={"log_level": args.log_level})
    configure_logging(config.log_level)

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
