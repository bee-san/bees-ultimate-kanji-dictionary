# Bee's Ultimate Kanji Dictionary

A small, understandable generator that turns [Jiten](https://jiten.moe) kanji
data into a single canonical [Yomitan](https://github.com/yomidevs/yomitan)
dictionary. It rebuilds once per day and publishes a self-updating ZIP.

## What you get

One Yomitan dictionary covering every clean character Jiten serves. Each entry
prioritizes what actually helps recall:

- a **keyword / meaning** and a compact list of common senses,
- honest **on / kun readings** exactly as Jiten supplies them,
- a few **common vocabulary examples** grouped by reading (On / Kun / Other),
  chosen by word frequency rank and rendered with furigana,
- **rank, grade, JLPT, and stroke count** facts.

Single-character term entries are preserved so ordinary dictionary clicks work,
and native kanji-bank entries are included from the same data. Frequency banks
use rank-based mode. Junk (`missing`, `???`, leaked markup, malformed ruby,
misleading percentages) is removed.

## Install in Yomitan

1. Download `bees-ultimate-kanji-dictionary.zip` from the
   [latest release](https://github.com/bee-san/bees-ultimate-kanji-dictionary/releases/latest).
2. In Yomitan → Settings → Dictionaries → **Import**, select the ZIP.
3. Yomitan will offer updates when a newer revision is published (it checks the
   updater index on demand, when you use its dictionary update check).

## Build it yourself

One command fetches (or refreshes from cache), normalizes, validates, and
builds deterministically:

```bash
uv venv && . .venv/bin/activate
uv pip install -e . jsonschema
npm install                      # adm-zip + ajv for schema validation
python -m bees_kanji             # writes build/ + refreshes dist/index.json
```

- Uses the unauthenticated Jiten API **once per UTC day**. There is no API key.
- Responses are cached under `cache/<UTC-date>/`, so a same-day rerun makes zero
  requests and an interrupted run fetches only the characters it still needs.
- The build is reproducible: identical inputs produce a byte-identical ZIP.
- A new release is published only when the normalized dictionary content
  actually changes; the revision is a monotonic dot-numeric UTC date.

Useful flags: `--limit N` (build the first N characters, for a quick check),
`--offline` (use the cache only), `--date YYYY-MM-DD`.

## Tests

```bash
python -m pytest -q
```

Focused tests cover 場 / 男 / 事 / 生 / 行 / 髙, malformed API data, the
Top-1000 quality floor, deterministic output, and validation against the pinned
official Yomitan schemas (also checked end-to-end via `scripts/validate_yomitan.mjs`).

## Licence

Generator code is MIT (see `LICENSE`). Dictionary **data** is redistributed
under CC BY-SA 4.0:

> Dictionary data derived from Jiten (https://jiten.moe), using JMdict/KANJIDIC
> data from the Electronic Dictionary Research and Development Group (EDRDG).
> Data is redistributed under CC BY-SA 4.0; see
> https://creativecommons.org/licenses/by-sa/4.0/ and
> https://www.edrdg.org/edrdg/licence.html.

See `LICENSE-data.txt` (also bundled inside every release ZIP).
