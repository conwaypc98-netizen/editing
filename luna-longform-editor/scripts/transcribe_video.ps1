param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$VideoPath,

    [string]$Model = "small.en"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Require-Command {
    param([Parameter(Mandatory = $true)][string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was not found in PATH. Run setup_windows.ps1 and install FFmpeg if needed."
    }
}

Require-Command "ffmpeg"

if (-not (Test-Path -LiteralPath $VideoPath)) {
    throw "Input video not found: $VideoPath"
}

$source = (Resolve-Path -LiteralPath $VideoPath).Path
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$transcriber = Join-Path $scriptDir "transcribe_with_faster_whisper.py"

$toolRoot = if ($env:LUNA_EDITOR_TOOL_DIR) {
    $env:LUNA_EDITOR_TOOL_DIR
}
else {
    Join-Path $HOME ".codex\tools\luna-longform-editor"
}
$venvPython = Join-Path $toolRoot "transcribe-venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Transcription environment not found at $venvPython. Run scripts\setup_windows.ps1 first."
}

$stem = [IO.Path]::GetFileNameWithoutExtension($source) -replace "[^A-Za-z0-9._-]+", "_"
if ([string]::IsNullOrWhiteSpace($stem)) {
    $stem = "video"
}

$outDir = Join-Path (Join-Path (Get-Location) "output") ($stem + "_transcript")
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$audio = Join-Path $outDir ($stem + "_16k.wav")
$jsonOut = Join-Path $outDir "transcript.json"
$txtOut = Join-Path $outDir "transcript.txt"

Write-Host "Extracting mono 16k audio..."
& ffmpeg -hide_banner -y -i $source -map "0:a:0" -ac 1 -ar 16000 -vn $audio

Write-Host "Transcribing with faster-whisper model $Model..."
& $venvPython $transcriber --audio $audio --model $Model --json-out $jsonOut --text-out $txtOut

Write-Host "Transcript JSON: $jsonOut"
Write-Host "Transcript text: $txtOut"
