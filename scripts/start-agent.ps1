# scripts/start-agent.ps1 — start the Python agent alone (no Tauri).
#
# Mirrors scripts/start-agent.sh. Use this from PowerShell.
#
# Usage:
#   .\scripts\start-agent.ps1
#   $env:LOG_LEVEL="DEBUG"; .\scripts\start-agent.ps1

[CmdletBinding()]
param(
    [string]$LogLevel = $env:MINIMAX_CODE_LOG_LEVEL
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
$AgentDir = Join-Path $RepoRoot "agent"

Push-Location $AgentDir
try {
    $env:PYTHONUNBUFFERED = "1"
    $env:PYTHONIOENCODING = "utf-8"
    if (-not $LogLevel) { $LogLevel = "INFO" }
    $env:MINIMAX_CODE_LOG_LEVEL = $LogLevel

    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) {
        Write-Error "uv is required. Install from https://docs.astral.sh/uv/"
        exit 1
    }
    & uv run python -m minimax_code
} finally {
    Pop-Location
}
