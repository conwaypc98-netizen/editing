#!/usr/bin/env bash
set -euo pipefail

VERSION="29.3.1"
CACHE_DIR="${LUNA_EDITOR_CACHE:-$HOME/.codex/tools/luna-longform-editor}"
BIN="$CACHE_DIR/auto-editor-bin"

mkdir -p "$CACHE_DIR"

if [[ -x "$BIN" ]]; then
  printf '%s\n' "$BIN"
  exit 0
fi

arch="$(uname -m)"
case "$arch" in
  arm64)
    asset="auto-editor-macos-arm64"
    ;;
  x86_64)
    asset="auto-editor-macos-x86_64"
    ;;
  *)
    echo "Unsupported macOS architecture: $arch" >&2
    exit 1
    ;;
esac

url="https://github.com/WyattBlue/auto-editor/releases/download/${VERSION}/${asset}"

echo "Downloading Auto-Editor ${VERSION}: $url" >&2
curl -L --fail -o "$BIN" "$url"
chmod +x "$BIN"
printf '%s\n' "$BIN"
