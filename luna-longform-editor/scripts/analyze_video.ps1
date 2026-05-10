param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$VideoPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Require-Command {
    param([Parameter(Mandatory = $true)][string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was not found in PATH. Install FFmpeg and reopen PowerShell/Codex."
    }
}

Require-Command "ffmpeg"
Require-Command "ffprobe"

if (-not (Test-Path -LiteralPath $VideoPath)) {
    throw "Input video not found: $VideoPath"
}

$source = (Resolve-Path -LiteralPath $VideoPath).Path
$stem = [IO.Path]::GetFileNameWithoutExtension($source) -replace "[^A-Za-z0-9._-]+", "_"
if ([string]::IsNullOrWhiteSpace($stem)) {
    $stem = "video"
}

$outDir = Join-Path (Join-Path (Get-Location) "output") ($stem + "_analysis")
$proxyDir = Join-Path (Join-Path (Get-Location) "work") "proxies"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
New-Item -ItemType Directory -Force -Path $proxyDir | Out-Null

$ffprobeOut = Join-Path $outDir "ffprobe.txt"
$volumeOut = Join-Path $outDir "volume.txt"
$durationOut = Join-Path $outDir "duration.txt"
$contactSheet = Join-Path $outDir "contact_sheet.jpg"
$readme = Join-Path $outDir "README.txt"
$proxy = Join-Path $proxyDir ($stem + "_edit_proxy.mp4")
$proxyPath = Join-Path $outDir "proxy_path.txt"

Write-Host "Probing video..."
& ffprobe -hide_banner -v error -show_format -show_streams $source 2>&1 | Out-File -Encoding utf8 $ffprobeOut
& ffprobe -v error -show_entries format=duration -of "default=noprint_wrappers=1:nokey=1" $source 2>&1 | Out-File -Encoding utf8 $durationOut

Write-Host "Checking volume..."
& ffmpeg -hide_banner -i $source -af volumedetect -vn -sn -dn -f null NUL 2>&1 | Out-File -Encoding utf8 $volumeOut

Write-Host "Creating contact sheet..."
& ffmpeg -hide_banner -y -i $source -vf "fps=1/20,scale=480:-1,tile=5x6" -frames:v 1 $contactSheet

Write-Host "Creating edit proxy..."
$proxyArgs = @(
    "-hide_banner", "-y",
    "-i", $source,
    "-map", "0:v:0",
    "-map", "0:a:0",
    "-c:v", "libx264",
    "-preset", "veryfast",
    "-crf", "18",
    "-c:a", "aac",
    "-b:a", "192k",
    "-ac", "2",
    "-movflags", "+faststart",
    $proxy
)
& ffmpeg @proxyArgs
$proxy | Out-File -Encoding utf8 $proxyPath

@"
Luna Longform Editor analysis package

Open contact_sheet.jpg to review visual sections.
Read ffprobe.txt for stream details and duration.txt for source duration.
Read volume.txt for peak/mean volume issues.
Use the proxy path in proxy_path.txt for final rendering if the original file is hard for FFmpeg to seek.

This Windows helper does not create pause-only drafts. Final edits should still be semantic: transcript, visual review, keep list, spoken-pacing tightening, audio-boundary snapping, render, rendered transcript audit.
"@ | Out-File -Encoding utf8 $readme

Write-Host "Analysis folder: $outDir"
Write-Host "Contact sheet: $contactSheet"
Write-Host "Edit proxy: $proxy"
