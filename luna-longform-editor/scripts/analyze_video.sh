#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATOR="$SCRIPT_DIR/luna_editor.py"

usage() {
  echo "Usage: $0 /path/to/video.mov [whisper-model]"
  echo
  echo "Creates an isolated semantic-editing job in ./output/luna_jobs/."
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || $# -lt 1 || $# -gt 2 ]]; then
  usage
  exit 0
fi

INPUT="$1"
MODEL="${2:-small.en}"

if [[ ! -f "$INPUT" ]]; then
  echo "Input file not found: $INPUT" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required." >&2
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "ffmpeg and ffprobe are required. Install with: brew install ffmpeg" >&2
  exit 1
fi

JOB="$(python3 "$ORCHESTRATOR" init \
  --mode edit \
  --source "$INPUT" \
  --jobs-root "$PWD/output/luna_jobs")"

python3 "$ORCHESTRATOR" prepare --job "$JOB" --model "$MODEL"

echo
echo "Evidence-ready Luna job: $JOB"
echo "Open: $JOB/analysis/review.html"
echo "Read: $JOB/analysis/EDITORIAL_REVIEW.md"
echo "Next: write and validate $JOB/plans/edit_plan.json"
