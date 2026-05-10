#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTO_EDITOR="$("$SCRIPT_DIR/setup_free_editor.sh")"
MAKE_PROXY="$SCRIPT_DIR/make_edit_proxy.sh"
WORK_DIR="$PWD/work/auto-editor"

usage() {
  echo "Usage: $0 /path/to/video.mov [loose|balanced|tight|all]"
  echo
  echo "Default preset: balanced"
  echo "Outputs go to: ./output/<video-name>_cuts/"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || $# -lt 1 || $# -gt 2 ]]; then
  usage
  exit 0
fi

INPUT="$1"
PRESET="${2:-balanced}"

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
  echo "This file has no audio stream, so the free silence-cut editor cannot tell what to keep." >&2
  echo "Use a source export with the mic/game audio included, or ask Codex to make a visual-only review proxy instead." >&2
  exit 1
fi

base="$(basename "$INPUT")"
stem="${base%.*}"
safe_stem="$(printf '%s' "$stem" | tr -c 'A-Za-z0-9._-' '_')"
OUT_DIR="$PWD/output/${safe_stem}_cuts"
mkdir -p "$OUT_DIR" "$WORK_DIR"

PROXY_LOG="$OUT_DIR/${safe_stem}_proxy.log"
echo "Preparing clean edit proxy..."
PROXY="$("$MAKE_PROXY" "$INPUT" 2>"$PROXY_LOG")"
echo "Proxy: $PROXY"

preset_settings() {
  case "$1" in
    loose)
      echo "0.020|0.35s,0.70s|Light cuts. Keeps more pauses and context."
      ;;
    balanced)
      echo "0.040|0.25s,0.45s|Default. Good first rough cut for talking videos."
      ;;
    tight)
      echo "0.070|0.15s,0.25s|Aggressive. Best when the target is a much shorter cut."
      ;;
    *)
      return 1
      ;;
  esac
}

render_preset() {
  local name="$1"
  local settings threshold margin note out log sheet
  settings="$(preset_settings "$name")"
  IFS='|' read -r threshold margin note <<<"$settings"

  out="$OUT_DIR/${safe_stem}_${name}_rough_cut.mp4"
  log="$OUT_DIR/${safe_stem}_${name}_rough_cut.log"
  sheet="$OUT_DIR/${safe_stem}_${name}_contact_sheet.jpg"

  echo
  echo "Rendering $name preset"
  echo "Threshold: $threshold"
  echo "Margin:    $margin"
  echo "Note:      $note"
  echo "Output:    $out"

  "$AUTO_EDITOR" "$PROXY" \
    --edit "audio:$threshold,stream=0" \
    --margin "$margin" \
    --when-normal nil \
    --when-silent cut \
    --audio-normalize ebu \
    --video-codec libx264 \
    --video-bitrate 14000k \
    --audio-codec aac \
    --audio-bitrate 192k \
    --faststart \
    --no-open \
    --temp-dir "$WORK_DIR" \
    -o "$out" \
    >"$log" 2>&1

  ffmpeg -hide_banner -y -i "$out" \
    -vf "fps=1/20,scale=360:-1,tile=5x6" \
    -frames:v 1 "$sheet" \
    >>"$log" 2>&1 || true

  ffprobe -v error -show_entries format=duration \
    -of default=noprint_wrappers=1:nokey=1 "$out" \
    | awk '{ printf "Finished duration: %.2f minutes\n", $1 / 60 }'
}

case "$PRESET" in
  loose|balanced|tight)
    render_preset "$PRESET"
    ;;
  all)
    render_preset loose
    render_preset balanced
    render_preset tight
    ;;
  *)
    echo "Unknown preset: $PRESET" >&2
    usage
    exit 1
    ;;
esac

cat >"$OUT_DIR/README.txt" <<EOF
Rough-cut outputs for:
$INPUT

Presets:
- loose: keeps more breathing room and context
- balanced: best default for talking videos
- tight: most aggressive automatic cut

Review the MP4 files and contact sheets in this folder.
EOF

echo
echo "Done. Open: $OUT_DIR"
