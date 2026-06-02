"""Entry point for `python -m minimax_code`.

Boots logging, config, then the stdio JSON-RPC server. The server runs
until stdin is closed (EOF) or a fatal error occurs.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from .app import register_app_handlers
from .config import Config
from .ipc.server import IPCServer
from .logging_setup import configure_logging


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="minimax-code-agent",
        description="MiniMax Code Python agent (Tauri sidecar)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Override log level (defaults to MINIMAX_CODE_LOG_LEVEL env).",
    )
    return parser.parse_args(argv)


async def amain(config: Config) -> int:
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
    await server.run_forever()
    return 0


def cli_entry() -> None:
    """Synchronous entry point registered as a console_script."""
    args = parse_args()
    config = Config.from_env()
    if args.log_level:
        config = config.model_copy(update={"log_level": args.log_level})
    configure_logging(config.log_level)
    try:
        rc = asyncio.run(amain(config))
    except KeyboardInterrupt:
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    cli_entry()
