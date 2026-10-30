# Explicit interactive entry for offline data-only bundle creation and restore.
$ErrorActionPreference = 'Stop'

function Is-AbsolutePath([string]$value) {
    return $value -match '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))'
}

try {
    $releaseRoot = Split-Path -Parent $PSScriptRoot
    $agentDir = Join-Path $releaseRoot 'agent'
    $module = Join-Path $agentDir 'minimax_code\bundle.py'
    $python = Join-Path $agentDir '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        $python = Read-Host 'Absolute path to an existing Python 3.11+ with this version dependencies installed'
    } else {
        Write-Host "Using release Python: $python"
    }
    if (-not (Is-AbsolutePath $python) -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'Python must be an existing absolute executable path.'
    }
    if (-not (Test-Path -LiteralPath $module -PathType Leaf)) {
        throw 'The data bundle module is missing from this version.'
    }

    Write-Host 'Data-only: includes the database and registered text and attachment versions supported by this bundle version.'
    Write-Host 'It excludes workspaces, OS keyring, environment variables and other external files.'
    Write-Host 'The database may itself contain credentials; protect the bundle and restored data.'
    Write-Host 'Stop related services and tasks before creating. A restored directory must not be started directly.'
    Write-Host 'Read the data-vault bundle documentation for exclusions, compatibility and reconciliation.'
    $choice = Read-Host 'Enter C to create a bundle, R to restore a trusted bundle, or press Enter to cancel'
    if ($choice -ieq 'C') {
        $source = Read-Host 'Absolute path to the stopped source data directory'
        $target = Read-Host 'Absolute path to the NEW output .zip file (must not exist)'
        $bundleArgs = @('create', '--data-dir', $source, '--output', $target)
    } elseif ($choice -ieq 'R') {
        $source = Read-Host 'Absolute path to the trusted bundle .zip'
        $target = Read-Host 'Absolute path to the NEW data directory (must not exist)'
        $bundleArgs = @('restore', '--bundle', $source, '--new-data-dir', $target)
    } elseif ($choice -eq '') {
        Write-Host 'Cancelled; no operation was started.'
        exit 0
    } else { throw 'Invalid choice.' }
    if (-not (Is-AbsolutePath $source) -or -not (Is-AbsolutePath $target)) {
        throw 'Both paths must be fully qualified absolute paths.'
    }
    Write-Host "Source: $source"
    Write-Host "New target: $target"
    if ((Read-Host 'Enter YES to proceed, or press Enter to cancel') -cne 'YES') {
        Write-Host 'Cancelled; no operation was started.'
        exit 0
    }
    Push-Location -LiteralPath $agentDir
    try {
        & $python -m minimax_code.bundle @bundleArgs
        $result = $LASTEXITCODE
    } finally { Pop-Location }
    if ($result -eq 0) {
        Write-Host 'Operation completed. Review its output and the documented exclusions and recovery boundaries.'
        Write-Host 'No service was started, migrated, or automatically rebound.'
    } else {
        Write-Host 'Operation failed. Read the output and inspect any retained files before another attempt.'
    }
    exit $result
} catch {
    Write-Host "Cannot start data bundle operation: $_"
    exit 1
}
