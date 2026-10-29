# Read-only preflight for an installed release. It does not start application code.
$ErrorActionPreference = 'Stop'
$release = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$issues = 0
function Report([string]$name, [bool]$ok, [string]$detail) {
    if ($ok) { Write-Host "[OK] $name - $detail" }
    else { Write-Host "[ACTION] $name - $detail"; $script:issues++ }
}
function RegularFile([string]$path) {
    try {
        $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
        return (-not $item.PSIsContainer -and -not ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint))
    } catch { return $false }
}
function DirectoryPath([string]$path) {
    try { return [System.IO.Path]::IsPathRooted($path) -and (Test-Path -LiteralPath $path -PathType Container) }
    catch { return $false }
}
try {
    Write-Host "Read-only installed release diagnostic: $release"
    Write-Host 'No application, installer, database, or network connection will be started.'
    $node = Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    Report 'Node.js on PATH' ($null -ne $node) $(if ($node) { $node.Source } else { 'Install Node.js separately and reopen this window.' })
    Report 'Installation marker' (RegularFile (Join-Path $release '.INSTALL-COMPLETE')) 'If missing, install the release into a new directory using the documented installer.'
    Report 'Incomplete marker absent' (-not (Test-Path -LiteralPath (Join-Path $release '.INSTALL-INCOMPLETE'))) 'If present, inspect the incomplete installation; do not remove the marker to bypass it.'
    Report 'Release manifest' (RegularFile (Join-Path $release 'RELEASE-MANIFEST.json')) 'If missing, use a complete installed release.'
    Report 'Frontend' (RegularFile (Join-Path $release 'web\dist\index.html')) 'If missing, prepare a release with a built frontend.'
    Report 'Launcher' (RegularFile (Join-Path $release 'scripts\start-release.mjs')) 'If missing, use a complete installed release.'
    $agent = Join-Path $release 'agent'
    $python = $env:MINIMAX_CODE_PYTHON
    if (-not $python) {
        $venv = $env:UV_PROJECT_ENVIRONMENT
        if (-not $venv) { $venv = '.venv' }
        if (-not [System.IO.Path]::IsPathRooted($venv)) { $venv = Join-Path $agent $venv }
        $python = Join-Path $venv 'Scripts\python.exe'
    }
    $pythonOk = [System.IO.Path]::IsPathRooted($python) -and (Test-Path -LiteralPath $python -PathType Leaf)
    Report 'Python executable' $pythonOk "Selected: $python. If missing, prepare the environment separately or set MINIMAX_CODE_PYTHON to an absolute path."
    $data = Read-Host 'Existing absolute data directory (leave blank to skip)'
    $workspace = Read-Host 'Existing absolute workspace directory (leave blank to skip)'
    if ($data) {
        $dataOk = DirectoryPath $data
        Report 'Data directory' $dataOk 'Select an existing absolute directory.'
        if ($dataOk) {
            $db = Join-Path $data 'data.db'
            if (RegularFile $db) { Report 'Existing database' $true $db }
            else { Report 'Existing database' $false 'No regular data.db found. For a new installation, explicitly initialize an empty directory in the launcher; never overwrite existing data.' }
        }
    } else { Write-Host '[SKIPPED] Data directory and database.' }
    if ($workspace) { Report 'Workspace' (DirectoryPath $workspace) 'Select an existing absolute directory.' }
    else { Write-Host '[SKIPPED] Workspace.' }
    Write-Host 'This checks presence only: versions, dependency imports, manifest contents, path overlap, schema, permissions, port availability and readiness are not checked.'
    Write-Host 'See docs\deployment.md for preparation, backup, upgrade and startup instructions.'
    if ($issues) { exit 1 } else { exit 0 }
} catch {
    Write-Host "Diagnostic could not finish: $($_.Exception.Message)"
    exit 1
}
