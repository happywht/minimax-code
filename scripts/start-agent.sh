#!/usr/bin/env bash
# scripts/start-agent.sh — start the Python agent alone (no Tauri).
#
# Useful when debugging the JSON-RPC stdio channel with a manual client
# (e.g. `nc` or a small Python harness).
#
# Usage:
#   ./scripts/start-agent.sh
#   LOG_LEVEL=DEBUG ./scripts/start-agent.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
AGENT_DIR="${REPO_ROOT}/agent"

cd "${AGENT_DIR}"

export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8
export MINIMAX_CODE_LOG_LEVEL="${LOG_LEVEL:-INFO}"

if command -v uv >/dev/null 2>&1; then
  exec uv run python -m minimax_code
else
  echo "error: 'uv' is required. Install from https://docs.astral.sh/uv/" >&2
  exit 1
fi
