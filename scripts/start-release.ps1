# Interactive entry for an installed release. No environment or data migration is performed here.
$ErrorActionPreference = "Stop"
$releaseRoot = Split-Path -Parent $PSScriptRoot
$dataPath = Read-Host "Existing absolute data directory"
$workspacePath = Read-Host "Existing absolute workspace directory"
$mode = Read-Host "Initialize a new empty data directory? Enter YES, or press Enter for an existing database"
$launchArgs = @((Join-Path $PSScriptRoot "start-release.mjs"), "--data-dir", $dataPath, "--workspace", $workspacePath)
if ($mode -ceq "YES") { $launchArgs += "--initialize-new" }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Error "Node.js was not found on PATH. Prepare runtime and dependencies separately."
    exit 1
}
& node @launchArgs
exit $LASTEXITCODE
