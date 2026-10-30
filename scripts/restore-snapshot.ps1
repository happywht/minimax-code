# Interactive wrapper for restoring a standalone SQLite snapshot to a new directory.
$ErrorActionPreference = 'Stop'

function Is-AbsolutePath([string]$value) {
    # IsPathRooted also accepts drive-relative paths on Windows.
    return $value -match '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))'
}

try {
    $releaseRoot = Split-Path -Parent $PSScriptRoot
    $restoreScript = Join-Path $releaseRoot 'agent\minimax_code\snapshot_restore.py'
    $python = Join-Path $releaseRoot 'agent\.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        $python = Read-Host 'Absolute path to an existing Python 3.11+ executable'
    } else {
        Write-Host "Using release Python: $python"
    }
    if (-not (Is-AbsolutePath $python) -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'Python must be an existing absolute executable path.'
    }
    if (-not (Test-Path -LiteralPath $restoreScript -PathType Leaf)) {
        throw 'The snapshot restore module is missing from this release.'
    }
    Write-Host 'Restore copies only a standalone SQLite snapshot into a NEW data directory.'
    Write-Host 'It does not include vault files, workspaces, keyring credentials, or environment variables.'
    Write-Host 'Stop and reconcile related services and tasks. Keep the source and target parent trusted and unchanged.'
    $snapshot = Read-Host 'Absolute path to the completed standalone .db backup'
    $destination = Read-Host 'Absolute path to the NEW data directory (must not exist)'
    if (-not (Is-AbsolutePath $snapshot) -or -not (Is-AbsolutePath $destination)) {
        throw 'Both paths must be fully qualified absolute paths.'
    }
    Write-Host "Snapshot: $snapshot"
    Write-Host "New data directory: $destination"
    if ((Read-Host 'Enter YES to copy into this new directory, or press Enter to cancel') -cne 'YES') {
        Write-Host 'Cancelled; no restore was started.'
        exit 0
    }
    & $python $restoreScript --snapshot $snapshot --new-data-dir $destination
    $result = $LASTEXITCODE
    if ($result -eq 0) {
        Write-Host 'Copy completed. Check version compatibility, external effects and missing files before any startup.'
        Write-Host 'The copy is not a verified full restore; no service was started or migrated.'
    } else {
        Write-Host 'Restore failed. Inspect any retained incomplete directory; do not start it.'
    }
    exit $result
} catch {
    Write-Host "Cannot start restore: $_"
    exit 1
}
