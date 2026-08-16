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
- a compact, accessible **reading-distribution donut** whose percentages are
  computed truthfully from the very examples shown in the entry (never from
  Jiten's `totalWords`), with a visible text legend and a semantic fallback so
  no information depends on colour, SVG, or CSS alone,
- a keyboard-accessible **Learning aids** disclosure carrying:
  - the **phonetic family** — the kanji that share a phonetic component,
    sourced directly from KanjiVG's `kvg:phon` markers (never inferred), ordered
    by frequency rank, with a compact source attribution;
  - a **stroke-order diagram** built from sanitized KanjiVG stroke geometry with
    a lightweight, dependency-free CSS animation (reduced-motion guarded), plus a
    text stroke-count / component line that survives when the image, SVG, or
    script is unavailable,
- **rank, grade, JLPT, and stroke count** facts.

Presentation uses restrained, compact CSS (bundled as `styles.css` in the ZIP,
scoped to this dictionary's own `data-sc-bee-role` markers) with clear
hierarchy, progressive disclosure, reduced-motion support, non-colour fallbacks,
and keyboard/screen-reader accessibility.

Single-character term entries are preserved so ordinary dictionary clicks work,
and native kanji-bank entries are included from the same data. Frequency banks
use rank-based mode. Junk (`missing`, `???`, leaked markup, malformed ruby,
misleading `totalWords`-derived percentages) is removed. There is exactly **one
canonical ZIP** — no Core/Standard/Extended editions.

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
- KanjiVG stroke SVGs are acquired the same way — once per UTC day into
  `kanjivg-cache/<UTC-date>/`, resumable and cache-first — then sanitized and
  bundled. No API key, no extra acquisition machinery.
- The build is reproducible: identical inputs produce a byte-identical ZIP.
- A new release is published only when the normalized dictionary content
  (including the bundled stroke/phonetic enrichment) actually changes; the
  revision is a monotonic dot-numeric UTC date.

Useful flags: `--limit N` (build the first N characters, for a quick check),
`--offline` (use the cache only), `--no-kanjivg` (data-only build, skip stroke
enrichment), `--date YYYY-MM-DD`.

## Tests

```bash
python -m pytest -q
```

Focused tests cover 場 / 男 / 事 / 生 / 行 / 髙, malformed API data, the
Top-1000 quality floor, deterministic output, the reading-distribution donut,
KanjiVG phonetic families / stroke sanitization / animation fallback, the
Anki/Lapis field mapping, and validation against the pinned official Yomitan
schemas (also checked end-to-end via `scripts/validate_yomitan.mjs`, which
additionally verifies bundled SVGs are sanitized and every referenced media
asset resolves).

## Anki / Lapis cards

A small, copyable setup (no add-on, database, or template framework) lives in
[`anki/`](anki/): a Yomitan→Lapis field-mapping table plus minimal front/back
templates and styling that reuse the dictionary's own fields. See
[`anki/README.md`](anki/README.md). There is no audio or pitch accent.

## Licence

Generator code is MIT (see `LICENSE`). Dictionary **data** is redistributed
under CC BY-SA 4.0:

> Dictionary data derived from Jiten (https://jiten.moe), using JMdict/KANJIDIC
> data from the Electronic Dictionary Research and Development Group (EDRDG).
> Data is redistributed under CC BY-SA 4.0; see
> https://creativecommons.org/licenses/by-sa/4.0/ and
> https://www.edrdg.org/edrdg/licence.html.

**Stroke-order diagrams and phonetic families** are derived from
[KanjiVG](https://kanjivg.tagaini.net/) (© Ulrich Apel), distributed under
CC BY-SA 3.0. The bundled SVGs are sanitized adaptations, redistributed under
the same share-alike licence. See `LICENSE-kanjivg.txt`.

Both notices (`LICENSE-data.txt` and, when stroke assets ship, `LICENSE-kanjivg.txt`)
are bundled inside every release ZIP alongside the data.
