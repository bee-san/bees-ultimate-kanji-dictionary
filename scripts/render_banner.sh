#!/usr/bin/env bash
# Rasterize the project banner (docs/images/banner.svg -> docs/images/banner.png)
# at the 1280x640 GitHub social-preview size, using the same headless Chromium
# the screenshot pipeline uses. The SVG is the source of truth; the PNG is a
# reproducible raster of it (kept in the repo so GitHub's social preview and the
# README header work without a build step).
#
# Usage: scripts/render_banner.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SVG="$ROOT/docs/images/banner.svg"
OUT="$ROOT/docs/images/banner.png"
[ -f "$SVG" ] || { echo "!! missing $SVG"; exit 1; }

CHROME="$(ls -d "$HOME"/.cache/ms-playwright/chromium-*/chrome-linux64/chrome 2>/dev/null | sort | tail -1 || true)"
if [ -z "${CHROME:-}" ] || [ ! -x "$CHROME" ]; then
  CHROME="$(command -v chromium || command -v chromium-browser || command -v google-chrome || true)"
fi
[ -n "${CHROME:-}" ] || { echo "!! no Chromium binary found"; exit 1; }
echo ">> chromium: $CHROME"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Wrap the SVG in a zero-margin page so the raster is exactly 1280x640.
cat > "$WORK/banner.html" <<HTML
<!doctype html><html><head><meta charset="utf-8">
<style>html,body{margin:0;padding:0}svg{display:block}</style></head>
<body>$(cat "$SVG")</body></html>
HTML

"$CHROME" --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
  --force-device-scale-factor=1 --window-size=1280,640 \
  --default-background-color=00000000 \
  --screenshot="$WORK/banner_raw.png" "file://$WORK/banner.html" >/dev/null 2>&1

python3 - "$WORK/banner_raw.png" "$OUT" <<'PY'
import sys
from PIL import Image
src, dst = sys.argv[1], sys.argv[2]
im = Image.open(src).convert("RGB")
# The window is exactly the banner size; guard against any stray extra rows.
im = im.crop((0, 0, 1280, 640))
im.save(dst, optimize=True)
print(f"{dst}: {im.size}")
PY

echo ">> done"
ls -l "$OUT"
