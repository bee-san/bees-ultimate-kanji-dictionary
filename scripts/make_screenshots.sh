#!/usr/bin/env bash
# Regenerate the README screenshots from the *packaged* dictionary.
#
# Fetches (or reuses) the canonical release ZIP, verifies its SHA256, extracts
# it, renders three package-preview HTML views (scripts/render_screenshots.py),
# captures each with headless Chromium at 2x, auto-crops to the card, and
# optimizes into docs/images/. Every pixel comes from the shipped ZIP + its
# bundled styles.css, so the shots are an honest package preview.
#
# Usage: scripts/make_screenshots.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
OUT="$ROOT/docs/images"
mkdir -p "$OUT"

ZIP_URL="https://github.com/bee-san/bees-ultimate-kanji-dictionary/releases/download/latest/bees-ultimate-kanji-dictionary.zip"
SUMS_URL="https://github.com/bee-san/bees-ultimate-kanji-dictionary/releases/download/latest/SHA256SUMS"

echo ">> fetching canonical ZIP + SHA256SUMS"
curl -fsSL "$ZIP_URL" -o "$WORK/bees-ultimate-kanji-dictionary.zip"
curl -fsSL "$SUMS_URL" -o "$WORK/SHA256SUMS"
( cd "$WORK" && sha256sum -c SHA256SUMS )

echo ">> extracting"
mkdir -p "$WORK/pkg"
( cd "$WORK/pkg" && unzip -oq ../bees-ultimate-kanji-dictionary.zip )

echo ">> rendering package-preview HTML (primary=場, narrow=生)"
python3 "$ROOT/scripts/render_screenshots.py" "$WORK/pkg" "$WORK/html" 場 生

# Locate the Playwright-cached Chromium (portable across the two pinned builds).
CHROME="$(ls -d "$HOME"/.cache/ms-playwright/chromium-*/chrome-linux64/chrome 2>/dev/null | sort | tail -1 || true)"
if [ -z "${CHROME:-}" ] || [ ! -x "$CHROME" ]; then
  CHROME="$(command -v chromium || command -v chromium-browser || command -v google-chrome || true)"
fi
[ -n "${CHROME:-}" ] || { echo "!! no Chromium binary found"; exit 1; }
echo ">> chromium: $CHROME"

shoot() {  # name window_w window_h out.png
  local name="$1" w="$2" h="$3" dst="$4"
  "$CHROME" --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
    --force-device-scale-factor=2 --window-size="$w","$h" \
    --screenshot="$WORK/${name}_raw.png" "file://$WORK/html/${name}.html" >/dev/null 2>&1
  python3 - "$WORK/${name}_raw.png" "$dst" <<'PY'
import sys
from PIL import Image, ImageChops
src, dst = sys.argv[1], sys.argv[2]
im = Image.open(src).convert("RGB")
bg = im.getpixel((0, 0))
diff = ImageChops.difference(im, Image.new("RGB", im.size, bg)).convert("L")
diff = diff.point(lambda p: 255 if p > 18 else 0)
bbox = diff.getbbox()
if bbox:
    pad = 20
    l, t, r, b = bbox
    im = im.crop((max(0, l - pad), max(0, t - pad),
                  min(im.width, r + pad), min(im.height, b + pad)))
im.save(dst, optimize=True)
print(f"{dst}: {im.size}")
PY
}

echo ">> capturing + cropping"
shoot compact  480 560  "$OUT/entry-compact.png"
shoot expanded 500 700  "$OUT/entry-expanded.png"
shoot narrow   360 760  "$OUT/entry-narrow.png"

# Extra optimization pass if optipng is available (lossless).
if command -v optipng >/dev/null 2>&1; then
  optipng -quiet -o2 "$OUT"/entry-*.png || true
fi

echo ">> done:"
ls -l "$OUT"/entry-*.png
rm -rf "$WORK"
