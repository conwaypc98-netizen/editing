param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$VideoPath,

    [string]$Model = "small.en"
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
        $checkArgs = @()
        $checkArgs += $candidate.Args
        $checkArgs += @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)")
        & $exe @checkArgs | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return $candidate
        }
    }
    throw "Python 3.10+ was not found. Run scripts\setup_windows.ps1 first."
}

if (-not (Test-Command "ffmpeg") -or -not (Test-Command "ffprobe")) {
    throw "ffmpeg and ffprobe were not found in PATH. Run scripts\setup_windows.ps1 first."
}
if (-not (Test-Path -LiteralPath $VideoPath)) {
    throw "Input video not found: $VideoPath"
}

$source = (Resolve-Path -LiteralPath $VideoPath).Path
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$orchestrator = Join-Path $scriptDir "luna_editor.py"
$jobsRoot = Join-Path (Join-Path (Get-Location) "output") "luna_jobs"
$python = Find-Python

$initArgs = @()
$initArgs += $python.Args
$initArgs += @($orchestrator, "init", "--mode", "edit", "--source", $source, "--jobs-root", $jobsRoot)
$initOutput = & $python.Exe @initArgs
if ($LASTEXITCODE -ne 0) {
    throw "Could not initialize the Luna editing job."
}
$job = [string]($initOutput | Select-Object -Last 1)
$job = $job.Trim()
if ([string]::IsNullOrWhiteSpace($job)) {
    throw "The Luna job path was not returned."
}

$prepareArgs = @()
$prepareArgs += $python.Args
$prepareArgs += @($orchestrator, "prepare", "--job", $job, "--model", $Model)
& $python.Exe @prepareArgs
if ($LASTEXITCODE -ne 0) {
    throw "Could not prepare the Luna evidence dossier."
}

Write-Host ""
Write-Host "Evidence-ready Luna job: $job"
Write-Host "Open: $(Join-Path $job 'analysis\review.html')"
Write-Host "Read: $(Join-Path $job 'analysis\EDITORIAL_REVIEW.md')"
Write-Host "Next: write and validate $(Join-Path $job 'plans\edit_plan.json')"
