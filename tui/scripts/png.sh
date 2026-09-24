#!/usr/bin/env bash
# Convertit docs/screenshots/*.html en PNG (Chromium headless).
# Chromium en snap n'écrit pas dans /tmp : on passe par son dossier ~/snap/chromium/common.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../docs/screenshots" && pwd)"
SNAP="$HOME/snap/chromium/common"; mkdir -p "$SNAP"
for html in "$DIR"/${1:-*}.html; do
  name="$(basename "$html" .html)"
  cp "$html" "$SNAP/$name.html"
  chromium-browser --headless=new --no-sandbox --disable-gpu --hide-scrollbars --window-size="${WIDTH:-1500},${HEIGHT:-930}" \
    --virtual-time-budget=6000 --screenshot="$SNAP/$name.png" "file://$SNAP/$name.html" >/dev/null 2>&1 || true
  mv "$SNAP/$name.png" "$DIR/$name.png" && rm -f "$SNAP/$name.html" && echo "🖼  $DIR/$name.png"
done
