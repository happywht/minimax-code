# Interactive entry for an installed release. No environment or data migration is performed here.
$ErrorActionPreference = "Stop"

function Read-ProfilePath {
    $value = Read-Host "Absolute path to a trusted launch profile JSON file"
    if (-not [System.IO.Path]::IsPathRooted($value)) { throw "The profile path must be absolute." }
    return [System.IO.Path]::GetFullPath($value)
}

function Read-LaunchProfile($path) {
    $item = Get-Item -LiteralPath $path -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or $item.Length -gt 16384) {
        throw "The profile must be a regular file no larger than 16 KiB."
    }
    $profile = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    $names = @($profile.PSObject.Properties.Name | Sort-Object)
    if (($names -join ',') -cne 'dataDir,format,workspace' -or
        $profile.format -cne 'minimax-code-windows-launch-v1' -or
        $profile.dataDir -isnot [string] -or $profile.workspace -isnot [string] -or
        -not [System.IO.Path]::IsPathRooted($profile.dataDir) -or
        -not [System.IO.Path]::IsPathRooted($profile.workspace)) {
        throw "Invalid launch profile."
    }
    return $profile
}

function Save-LaunchProfile($path, $dataPath, $workspacePath) {
    if (-not [System.IO.Path]::IsPathRooted($dataPath) -or -not [System.IO.Path]::IsPathRooted($workspacePath)) {
        throw "Data and workspace paths must be absolute before saving a profile."
    }
    # Do not place the profile in the release, data or workspace, including through a junction.
    $parent = [System.IO.Path]::GetDirectoryName($path)
    $roots = @((Split-Path -Parent $PSScriptRoot), $dataPath, $workspacePath)
    foreach ($directory in @($parent) + $roots) {
        $item = Get-Item -LiteralPath $directory -Force
        if (-not $item.PSIsContainer) { throw "Profile parent and bound paths must be existing directories." }
        while ($null -ne $item) {
            if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                throw "Profile parent and bound paths cannot contain junctions or symbolic links when saving."
            }
            $item = $item.Parent
        }
    }
    $parentFull = [System.IO.Path]::GetFullPath($parent).TrimEnd('\') + '\'
    foreach ($root in $roots) {
        $rootFull = [System.IO.Path]::GetFullPath($root).TrimEnd('\') + '\'
        if ($parentFull.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Save the profile outside the release, data and workspace directories."
        }
    }
    # CreateNew prevents replacing an existing file, including an existing profile.
    $stream = [System.IO.File]::Open($path, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try {
        $writer = New-Object System.IO.StreamWriter($stream, (New-Object System.Text.UTF8Encoding($false)))
        try {
            [ordered]@{ format = 'minimax-code-windows-launch-v1'; dataDir = $dataPath; workspace = $workspacePath } |
                ConvertTo-Json | ForEach-Object { $writer.WriteLine($_) }
        } finally { $writer.Dispose() }
    } finally { $stream.Dispose() }
}

try {
    $choice = Read-Host "Press Enter to enter paths, or enter L to load a saved profile"
    if ($choice -ieq 'L') {
        $profile = Read-LaunchProfile (Read-ProfilePath)
        $dataPath = $profile.dataDir
        $workspacePath = $profile.workspace
        Write-Host "Data: $dataPath"
        Write-Host "Workspace: $workspacePath"
        if ((Read-Host "Enter YES to use these paths") -cne 'YES') { throw "Launch cancelled." }
        $mode = ''
    } elseif ($choice -eq '') {
        $dataPath = Read-Host "Existing absolute data directory"
        $workspacePath = Read-Host "Existing absolute workspace directory"
        $mode = Read-Host "Initialize a new empty data directory? Enter YES, or press Enter for an existing database"
        if ($mode -cne 'YES' -and $mode -ne '') { throw "Invalid initialization choice." }
        if ((Read-Host "Save these paths to a new external launch profile? Enter YES or press Enter") -ceq 'YES') {
            Save-LaunchProfile (Read-ProfilePath) $dataPath $workspacePath
            Write-Host "Profile saved. It contains paths only; future launches always use an existing database."
        }
    } else { throw "Invalid launch choice." }
    $launchArgs = @((Join-Path $PSScriptRoot "start-release.mjs"), "--data-dir", $dataPath, "--workspace", $workspacePath)
    if ($mode -ceq "YES") { $launchArgs += "--initialize-new" }
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        throw "Node.js was not found on PATH. Prepare runtime and dependencies separately."
    }
    & node @launchArgs
    exit $LASTEXITCODE
} catch {
    Write-Error $_
    exit 1
}
