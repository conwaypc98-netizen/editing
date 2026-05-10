#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTO_EDITOR="$("$SCRIPT_DIR/setup_free_editor.sh")"
MAKE_PROXY="$SCRIPT_DIR/make_edit_proxy.sh"

usage() {
  echo "Usage: $0 /path/to/video.mov"
  echo
  echo "Creates analysis files in ./output/<video-name>_analysis/."
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

base="$(basename "$INPUT")"
stem="${base%.*}"
safe_stem="$(printf '%s' "$stem" | tr -c 'A-Za-z0-9._-' '_')"
OUT_DIR="$PWD/output/${safe_stem}_analysis"
mkdir -p "$OUT_DIR"

echo "Analyzing: $INPUT"
echo "Writing:   $OUT_DIR"

ffprobe -hide_banner -i "$INPUT" >"$OUT_DIR/ffprobe.txt" 2>&1 || true

ffmpeg -hide_banner -nostats -i "$INPUT" \
  -map 0:a:0 -af volumedetect -f null - \
  >"$OUT_DIR/volume.txt" 2>&1 || true

ffmpeg -hide_banner -y -i "$INPUT" \
  -vf "fps=1/30,scale=360:-1,tile=5x6" \
  -frames:v 1 "$OUT_DIR/contact_sheet.jpg" \
  >"$OUT_DIR/contact_sheet.log" 2>&1 || true

PROXY=""
if PROXY="$("$MAKE_PROXY" "$INPUT" 2>"$OUT_DIR/proxy.log")"; then
  echo "$PROXY" >"$OUT_DIR/proxy_path.txt"
else
  cat >"$OUT_DIR/proxy_path.txt" <<EOF
No edit proxy was created. The file may be missing audio.
See proxy.log.
EOF
fi

run_preview() {
  local name="$1"
  local threshold="$2"
  local margin="$3"
  if [[ -z "$PROXY" || ! -f "$PROXY" ]]; then
    echo "Skipped: no edit proxy available." >"$OUT_DIR/preview_${name}.txt"
    return 0
  fi
  "$AUTO_EDITOR" "$PROXY" \
    --edit "audio:$threshold,stream=0" \
    --margin "$margin" \
    --preview \
    --no-open \
    >"$OUT_DIR/preview_${name}.txt" 2>&1 || true
}

run_preview loose 0.020 "0.35s,0.70s"
run_preview balanced 0.040 "0.25s,0.45s"
run_preview tight 0.070 "0.15s,0.25s"

cat >"$OUT_DIR/README.txt" <<EOF
Analysis complete.

Open these first:
- ffprobe.txt: video/audio technical info
- volume.txt: whether the audio track is actually usable
- contact_sheet.jpg: visual skim every ~30 seconds
- preview_loose.txt: estimated cut using light silence removal
- preview_balanced.txt: estimated cut using normal silence removal
- preview_tight.txt: estimated cut using aggressive silence removal

If one preview lands around the target 3-6 minute range, run:
  $SCRIPT_DIR/rough_cut_luna.sh "$INPUT" balanced

Available presets: loose, balanced, tight, all
EOF

echo
echo "Done. Open: $OUT_DIR"
