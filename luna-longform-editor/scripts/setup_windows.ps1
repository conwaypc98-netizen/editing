param(
    [switch]$InstallFfmpeg
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-Command {
    param([Parameter(Mandatory = $true)][string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Find-Python {
    $candidates = @(
        @{ Exe = "py"; Args = @("-3.11") },
        @{ Exe = "py"; Args = @("-3.12") },
        @{ Exe = "py"; Args = @("-3") },
        @{ Exe = "python"; Args = @() },
        @{ Exe = "python3"; Args = @() }
    )

    foreach ($candidate in $candidates) {
        $exe = $candidate.Exe
        if (-not (Test-Command $exe)) {
            continue
        }

        $args = @()
        $args += $candidate.Args
        $checkArgs = @()
        $checkArgs += $args
        $checkArgs += @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)")

        & $exe @checkArgs | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return @{
                Exe = $exe
                Args = $args
            }
        }
    }

    throw "Python 3.10+ was not found. Install Python from python.org or with: winget install Python.Python.3.11"
}

Write-Host "Setting up Luna Longform Editor for Windows..."

if (-not (Test-Command "ffmpeg") -or -not (Test-Command "ffprobe")) {
    if ($InstallFfmpeg) {
        if (-not (Test-Command "winget")) {
            throw "FFmpeg is missing and winget is not available. Install FFmpeg manually and make sure ffmpeg.exe and ffprobe.exe are in PATH."
        }
        Write-Host "Installing FFmpeg with winget..."
        winget install --id Gyan.FFmpeg -e --source winget
    }
    else {
        Write-Warning "FFmpeg is not in PATH. Install it with: winget install --id Gyan.FFmpeg -e --source winget"
        Write-Warning "After installing FFmpeg, reopen PowerShell/Codex so PATH refreshes."
    }
}

$python = Find-Python
$toolRoot = if ($env:LUNA_EDITOR_TOOL_DIR) {
    $env:LUNA_EDITOR_TOOL_DIR
}
else {
    Join-Path $HOME ".codex\tools\luna-longform-editor"
}
$venvPath = Join-Path $toolRoot "transcribe-venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

New-Item -ItemType Directory -Force -Path $toolRoot | Out-Null

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Creating transcription environment at $venvPath"
    $venvArgs = @()
    $venvArgs += $python.Args
    $venvArgs += @("-m", "venv", $venvPath)
    & $python.Exe @venvArgs
}

Write-Host "Installing transcription dependencies..."
& $venvPython -m pip install --upgrade pip wheel setuptools
& $venvPython -m pip install --upgrade faster-whisper

Write-Host ""
Write-Host "Windows setup complete."
Write-Host "Use: .\scripts\transcribe_video.ps1 C:\path\to\video.mp4"
Write-Host "Use: .\scripts\analyze_video.ps1 C:\path\to\video.mp4"
