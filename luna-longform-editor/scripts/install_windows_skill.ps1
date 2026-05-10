param(
    [switch]$SkipSetup,
    [switch]$InstallFfmpeg
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$sourceSkill = (Resolve-Path -LiteralPath (Join-Path $scriptDir "..")).Path
$skillsRoot = Join-Path $HOME ".codex\skills"
$destinationSkill = Join-Path $skillsRoot "luna-longform-editor"

New-Item -ItemType Directory -Force -Path $skillsRoot | Out-Null

$sourceFull = [IO.Path]::GetFullPath($sourceSkill).TrimEnd("\")
$destinationFull = [IO.Path]::GetFullPath($destinationSkill).TrimEnd("\")

if ($sourceFull -ieq $destinationFull) {
    Write-Host "Skill is already installed at $destinationSkill"
}
else {
    if (Test-Path -LiteralPath $destinationSkill) {
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $backup = "$destinationSkill.backup-$stamp"
        Write-Host "Existing skill found. Moving it to $backup"
        Move-Item -LiteralPath $destinationSkill -Destination $backup
    }

    Write-Host "Installing skill to $destinationSkill"
    Copy-Item -LiteralPath $sourceSkill -Destination $destinationSkill -Recurse -Force
}

if (-not $SkipSetup) {
    $setup = Join-Path $destinationSkill "scripts\setup_windows.ps1"
    & $setup -InstallFfmpeg:$InstallFfmpeg
}

Write-Host "Luna Longform Editor skill is ready."
