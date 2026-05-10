#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 /path/to/video.mov"
  echo
  echo "Creates a clean H.264/AAC edit proxy under ./work/proxies/ and prints its path."
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || $# -ne 1 ]]; then
  usage
  exit 0
fi

INPUT="$1"
if [[ ! -f "$INPUT" ]]; then
  echo "Input file not found: $INPUT" >&2
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "ffmpeg/ffprobe are required. Install with: brew install ffmpeg" >&2
  exit 1
fi

audio_streams="$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$INPUT" | wc -l | tr -d ' ')"
if [[ "$audio_streams" == "0" ]]; then
  echo "This file has no audio stream, so an audio-based edit proxy cannot be created." >&2
  exit 1
fi

base="$(basename "$INPUT")"
stem="${base%.*}"
safe_stem="$(printf '%s' "$stem" | tr -c 'A-Za-z0-9._-' '_')"
PROXY_DIR="$PWD/work/proxies"
PROXY="$PROXY_DIR/${safe_stem}_edit_proxy.mp4"
mkdir -p "$PROXY_DIR"

if [[ -f "$PROXY" && "$PROXY" -nt "$INPUT" ]]; then
  printf '%s\n' "$PROXY"
  exit 0
fi

echo "Creating edit proxy: $PROXY" >&2

if ffmpeg -hide_banner -y -i "$INPUT" \
  -map 0:v:0 -map 0:a:0 \
  -c:v h264_videotoolbox -b:v 20000k \
  -c:a aac -b:a 192k -ac 2 \
  -movflags +faststart \
  "$PROXY" >&2; then
  printf '%s\n' "$PROXY"
  exit 0
fi

echo "Hardware encode failed. Trying libx264 fallback..." >&2
ffmpeg -hide_banner -y -i "$INPUT" \
  -map 0:v:0 -map 0:a:0 \
  -c:v libx264 -preset veryfast -crf 18 \
  -c:a aac -b:a 192k -ac 2 \
  -movflags +faststart \
  "$PROXY" >&2

printf '%s\n' "$PROXY"
