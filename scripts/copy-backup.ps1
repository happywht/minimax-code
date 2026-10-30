# Interactive entry for copying a completed standalone backup.
$ErrorActionPreference = 'Stop'

function Is-AbsolutePath([string]$value) {
    return $value -match '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))'
}

try {
    $releaseRoot = Split-Path -Parent $PSScriptRoot
    $copyScript = Join-Path $releaseRoot 'agent\minimax_code\backup_copy.py'
    $python = Join-Path $releaseRoot 'agent\.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        $python = Read-Host 'Absolute path to an existing Python 3.11+ executable'
    } else {
        Write-Host "Using release Python: $python"
    }
    if (-not (Is-AbsolutePath $python) -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'Python must be an existing absolute executable path.'
    }
    if (-not (Test-Path -LiteralPath $copyScript -PathType Leaf)) {
        throw 'The backup copy module is missing from this release.'
    }
    Write-Host 'Copy one completed standalone .db snapshot or data-vault .zip to a new subdirectory.'
    Write-Host 'The copy contains only the selected file. Credentials, workspaces and other files may be excluded.'
    Write-Host 'Use trusted paths and keep them unchanged while copying. Protect destination permissions.'
    $source = Read-Host 'Absolute path to the completed backup file'
    $destination = Read-Host 'Absolute path to an existing destination parent directory'
    if (-not (Is-AbsolutePath $source) -or -not (Is-AbsolutePath $destination)) {
        throw 'Both paths must be fully qualified absolute paths.'
    }
    Write-Host "Backup: $source"
    Write-Host "Destination parent: $destination"
    if ((Read-Host 'Enter YES to create a new copy, or press Enter to cancel') -cne 'YES') {
        Write-Host 'Cancelled; no copy was started.'
        exit 0
    }
    & $python $copyScript --source $source --destination-parent $destination
    $result = $LASTEXITCODE
    if ($result -eq 0) {
        Write-Host 'Copy completed. Keep the original and review the receipt and exclusions.'
        Write-Host 'This does not verify a restore or start or migrate a service.'
    } else {
        Write-Host 'Copy failed. Inspect any retained incomplete directory; do not use it as a backup.'
    }
    exit $result
} catch {
    Write-Host "Cannot start backup copy: $_"
    exit 1
}
