# Interactive Windows entry for manual release upgrade backup, copy and restore.
# It never switches, starts, or stops a service.
$ErrorActionPreference = 'Stop'

function Is-AbsolutePath([string]$value) {
    return $value -match '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))'
}

function Read-AbsolutePath([string]$label, [string]$defaultValue = '') {
    $prompt = if ($defaultValue) { "$label (Enter for $defaultValue)" } else { $label }
    $value = Read-Host $prompt
    if (-not $value -and $defaultValue) { $value = $defaultValue }
    if (-not (Is-AbsolutePath $value)) { throw "$label must be a fully qualified absolute path." }
    return [System.IO.Path]::GetFullPath($value)
}

function Read-OldPython([string]$release) {
    $candidate = Join-Path $release 'agent\.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        Write-Host "Using old release Python: $candidate"
        return $candidate
    }
    return Read-AbsolutePath 'Absolute path to the old release Python executable'
}

try {
    $releaseRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
    Write-Host 'Manual upgrade maintenance: B = prepare backup, C = copy prepared backup, R = restore backup to a NEW directory.'
    Write-Host 'No service is stopped, switched or started. This is data-only and excludes workspaces, keyring and external files.'
    Write-Host 'Protect backups and restored data: the database may contain sensitive information. Restrict Windows ACLs yourself.'
    $choice = Read-Host 'Choose B, C or R; press Enter to cancel'
    if ($choice -eq '') { Write-Host 'Cancelled; no operation started.'; exit 0 }

    if ($choice -ieq 'B') {
        $script = Join-Path $PSScriptRoot 'prepare-release-upgrade.py'
        $old = Read-AbsolutePath 'Old release directory' $releaseRoot
        $new = Read-AbsolutePath 'New release directory'
        $python = Read-OldPython $old
        $data = Read-AbsolutePath 'Stopped service data directory'
        $backup = Read-AbsolutePath 'Existing private backup parent directory'
        Write-Host "Old release: $old`nNew release: $new`nOld Python: $python`nData: $data`nBackup parent: $backup"
        if ((Read-Host 'Have all related services, tasks and other writers stopped? Enter YES to confirm') -cne 'YES') {
            Write-Host 'Cancelled; no backup started.'; exit 0
        }
        if ((Read-Host 'Create and verify a new data-only backup transaction? Enter YES to proceed') -cne 'YES') {
            Write-Host 'Cancelled; no backup started.'; exit 0
        }
        $opArgs = @('--old-release', $old, '--new-release', $new, '--old-python', $python,
                    '--data-dir', $data, '--backup-dir', $backup, '--confirm-stopped')
    } elseif ($choice -ieq 'C') {
        $script = Join-Path $PSScriptRoot 'copy-upgrade-backup.py'
        $python = Read-OldPython $releaseRoot
        $source = Read-AbsolutePath 'Completed manual-upgrade transaction directory'
        $parent = Read-AbsolutePath 'Existing private destination parent directory'
        Write-Host "Source transaction: $source`nDestination parent: $parent`nPython: $python"
        if ((Read-Host 'Create a separate copy without modifying the source? Enter YES to proceed') -cne 'YES') {
            Write-Host 'Cancelled; no copy started.'; exit 0
        }
        $opArgs = @('--source-transaction', $source, '--destination-parent', $parent)
    } elseif ($choice -ieq 'R') {
        $script = Join-Path $PSScriptRoot 'restore-upgrade-backup.py'
        $source = Read-AbsolutePath 'Completed manual-upgrade transaction or copy directory'
        $old = Read-AbsolutePath 'Matching old release directory' $releaseRoot
        $python = Read-OldPython $old
        $target = Read-AbsolutePath 'NEW restore data directory (must not exist)'
        Write-Host "Source transaction: $source`nOld release: $old`nOld Python: $python`nNew data directory: $target"
        if ((Read-Host 'Have all related services, tasks and other writers stopped? Enter YES to confirm') -cne 'YES') {
            Write-Host 'Cancelled; no restore started.'; exit 0
        }
        if ((Read-Host 'Have you independently verified the backup source and matching old release? Enter YES to confirm') -cne 'YES') {
            Write-Host 'Cancelled; no restore started.'; exit 0
        }
        if ((Read-Host 'Restore this backup to the NEW directory without starting it? Enter YES to proceed') -cne 'YES') {
            Write-Host 'Cancelled; no restore started.'; exit 0
        }
        $opArgs = @('--source-transaction', $source, '--old-release', $old, '--old-python', $python,
                    '--new-data-dir', $target, '--confirm-stopped', '--confirm-trusted-source')
    } else { throw 'Invalid choice.' }

    if (-not (Test-Path -LiteralPath $script -PathType Leaf)) { throw 'Maintenance script is missing from this release.' }
    if (-not (Is-AbsolutePath $python) -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'Python must be an existing absolute executable path.'
    }
    & $python $script @opArgs
    $result = $LASTEXITCODE
    if ($result -eq 0) {
        Write-Host 'Operation completed. Review its transaction, exclusions and output; the source and services were not switched.'
        Write-Host 'A restore requires separate review before use. Hashes and manifests do not authenticate the backup source.'
    } else {
        Write-Host 'Operation not confirmed. Inspect any retained transaction, staging or destination before another attempt.'
        Write-Host 'Do not start or switch a service using an unreviewed restore.'
    }
    exit $result
} catch {
    Write-Host "Cannot start upgrade maintenance: $_"
    exit 1
}
