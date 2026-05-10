#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_DIR="${LUNA_EDITOR_TOOL_DIR:-$HOME/.codex/tools/luna-longform-editor}"
VENV="$TOOL_DIR/transcribe-venv"
PY="$VENV/bin/python"

usage() {
  echo "Usage: $0 /path/to/video.mov [model]"
  echo
  echo "Creates ./output/<video-name>_transcript/transcript.json, transcript.txt, and words.txt."
  echo "Default model: small.en"
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

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "ffmpeg/ffprobe are required. Install with: brew install ffmpeg" >&2
  exit 1
fi

if [[ ! -x "$PY" ]]; then
  echo "Transcription environment missing: $PY" >&2
  echo "Create it with: python3.11 -m venv $VENV && $VENV/bin/python -m pip install faster-whisper" >&2
  exit 1
fi

if ! "$PY" -c "import faster_whisper" >/dev/null 2>&1; then
  echo "faster-whisper is not installed in $VENV" >&2
  echo "Install it with: $PY -m pip install faster-whisper" >&2
  exit 1
fi

base="$(basename "$INPUT")"
stem="${base%.*}"
safe_stem="$(printf '%s' "$stem" | tr -c 'A-Za-z0-9._-' '_')"
OUT_DIR="$PWD/output/${safe_stem}_transcript"
AUDIO="$OUT_DIR/audio_16k.wav"
mkdir -p "$OUT_DIR"

echo "Extracting audio..."
ffmpeg -hide_banner -y -i "$INPUT" \
  -map 0:a:0 -ac 1 -ar 16000 -vn "$AUDIO" \
  >"$OUT_DIR/audio_extract.log" 2>&1

echo "Transcribing with faster-whisper model: $MODEL"
"$PY" "$SCRIPT_DIR/transcribe_with_faster_whisper.py" "$AUDIO" \
  --out-dir "$OUT_DIR" \
  --model "$MODEL" \
  >"$OUT_DIR/transcribe.log" 2>&1

echo "Done. Open: $OUT_DIR"
