param(
    [switch]$SkipSetup,
    [switch]$InstallFfmpeg
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$installer = Join-Path $PSScriptRoot "luna-longform-editor\scripts\install_windows_skill.ps1"
if (-not (Test-Path -LiteralPath $installer)) {
    throw "Could not find installer: $installer"
}

& $installer -SkipSetup:$SkipSetup -InstallFfmpeg:$InstallFfmpeg
