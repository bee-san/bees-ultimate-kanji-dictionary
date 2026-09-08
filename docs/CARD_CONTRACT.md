# Bee's Ultimate Kanji Dictionary — Frozen Card Contract

**Status: AUTHORITATIVE / FROZEN.** This document is the single source of truth
for the canonical card surface of *Bee's Ultimate Kanji Dictionary* — the one
self-updating Jiten→Yomitan single-ZIP kanji dictionary. Every downstream
implementation, verification, and publish task MUST satisfy this contract
exactly. Do not deviate from it. Where this document and code disagree, the
build is wrong, not this document.

Canonical output: exactly **one** ZIP, `bees-ultimate-kanji-dictionary.zip`,
carrying exactly **one** card contract. There are no "editions" and no variant
cards to choose between.

The contract below is enforced today by `src/bees_kanji.py`
(`_detail_content`, `build_reading_frequency_node`, `build_manifest`) and its
`tests/` suite; the anchor symbols are named so downstream cards can locate the
enforcement point.

---

## 1. Above-the-fold order (HARD contract)

The compact card puts only the highest-value material above the fold, in this
exact order. Assembled by `_detail_content(record, enrichment)` into the
`[data-sc-bee-role="detail"]` wrapper's direct children:

1. **Hero** — the kanji glyph paired with its keyword
   (`beeRole="hero"` → `hero-glyph` + `hero-keyword`).
2. **Top-3 reading chips** — the three readings with the highest Jiten
   vocabulary-entry totals, as plain mixed chips (NOT split into On/Kun groups),
   `beeRole="reading-chips"` (`_top_reading_chip_row`, `COMPACT_READING_COUNT`).
3. **Meaning line** — a single compact `beeRole="meaning"` line
   (`"; "`-joined senses).
4. **Reading distribution — the compact "Frequency weight" pie**
   (`beeRole="reading-donut"`; see §2). Omitted, never faked, when frequency
   data is unavailable (see §2 and §3).
5. **Exactly six global common words** in a responsive two-column grid
   (`beeRole="vocab-grid"`, `grid-template-columns: 1fr 1fr`): six items → three
   rows, laid out 3-left / 3-right on ordinary popups and a single column on
   narrow popups (`_global_vocab_grid`, `record['global_words']`).

**Everything else is collapsed below the fold** in keyboard-accessible Yomitan
`<details>` disclosures (`beeRole="section"`): All readings (full On/Kun lists),
Details (rank / grade / JLPT / strokes), More vocabulary, and Learning aids
(phonetic family + stroke-order diagram). Empty disclosures are never emitted.

Every graphic above or below the fold has a real text equivalent (chip text,
legend, alt text, badge text); colour or CSS is never the sole carrier of
information.

---

## 2. The compact "Frequency weight" pie (left)

- **Caption:** the pie is captioned **"Frequency weight"** (`beeRole="donut-caption"`).
- **Segments:** the **top-4 readings by weight plus one "Other" tail** — at most
  five labelled segments total (`MAX_DONUT_SEGMENTS = 5`: four named + Other).
  Any tail beyond the top four collapses into a single Other slice.
- **"Other" is English in BOTH the slice label AND the legend — NEVER `他`.**
  The rasterizer draws Latin "Other" inside the slice and the legend lists
  "Other" identically.
- **Slice labels** are fit-checked: readings that fit legibly are drawn inside
  their slice; any that cannot fit remain available in the visible percentage
  legend (`beeRole="donut-legend"`).
- **No** Word-variety chart, **no** comparison bars / arrows / table, and **no**
  On/Kun suffixes in the compact legend. The legend shows only `reading: N%`.
- **Weights are authoritative from the Jiten Global CSV** (`Word,Form,Rank`),
  joined locally to Jiten's kanji-reading groups. No per-word API crawl.
- **No technical caveats on the card face.** The rank-derived methodology is
  documented ONLY in `README.md` and `MANIFEST.json` provenance — never printed
  per-entry on the card.
- **Frequency weight is rank-derived, not corpus probability.** It is a
  matched-form rank-score share, never an observed usage probability. Canonical
  definition (kept out of the card face): rank `r` contributes `1/sqrt(r)`;
  ranks above the tail threshold (100,000) receive the additional
  `(100000/r)^2` factor; reading totals are normalized to the displayed
  percentages (`rank_frequency_weight`). Integer percents sum to exactly 100.
- **If frequency data is unavailable for a character, OMIT the pie entirely.**
  `build_reading_frequency_node` returns `None`; the card simply has no pie.
  Do **NOT** restore an entry-share reading distribution as a substitute, and do
  **NOT** relabel raw entry counts as "frequency."

---

## 3. KANJIDIC2 fallback stays plain

Entries served only by KANJIDIC2 (characters Jiten does not serve) render as a
**plain** card: English meanings, on/kun readings, stroke count, grade/JLPT.

- **No invented pie** (no Frequency weight section).
- **No invented ranks.**
- **No invented example vocabulary** (no global-words grid).

Nothing is fabricated for a fallback record — no distribution, rank, or example
is ever synthesized to fill the gap.

---

## 4. Source acquisition + provenance

Three data sources, acquired at most once per UTC day, unauthenticated; a
release appears only when normalized content changes. Builds are **fail-closed**
on the pinned source snapshot: an offline replay requires an exact canonical
inventory, regular-file modes, and every locked size and SHA-256 digest before
the verified snapshot is installed.

| Source | Role | Acquisition |
|---|---|---|
| **Jiten** (https://jiten.moe) | Authoritative for enriched entries. Global CSV (`Word,Form,Rank`) join = frequency-weight source; kanji-reading group totals = source for any truthful distribution. | Once per UTC day, unauthenticated; bulk Global CSV, no per-word crawl. |
| **KANJIDIC2** (EDRDG) | Licensed fallback for characters Jiten does not serve. | Once per UTC day. |
| **KanjiVG** (https://kanjivg.tagaini.net/) | Static stroke-order SVG + phonetic-component relationships. | Pinned revision (`KANJIVG_REVISION`). |

**Snapshot / lock (release pins every build source):**
`jiten-global-frequency.csv` is published directly. `source-snapshot.zip`
retains the exact dated Jiten payloads, sitemap, KANJIDIC2 XML, and
pinned-revision KanjiVG files used by the build. `SOURCE-LOCK.json` records each
member's size and SHA-256. All derived and retained-source assets are covered by
`SHA256SUMS`. Production builds require at least 6,000 valid KanjiVG stroke sets
so a stale negative cache cannot silently strip learning-aid media.

**KanjiVG licence bundling:** the KanjiVG licence notice (`LICENSE-kanjivg.txt`,
CC BY-SA 3.0) is bundled **only when a `.svg` asset actually ships** in the ZIP.
`LICENSE-data.txt` (Jiten + KANJIDIC2/EDRDG, CC BY-SA 4.0) is bundled in every
release.

### MANIFEST.json provenance fields (recorded by `build_manifest`)

- **Release identity:** `title`, `revision`, `contentHash` (revision-independent),
  `buildDate` (UTC), `codeRevision` (`[0-9a-f]{40}`), `downloadUrl`, `indexUrl`.
- **Record counts:** `records.total`, `records.jiten`, `records.kanjidic2Fallback`.
- **`sources.jiten`** — url, records, `sitemapCharacters`, acquisition,
  `role: "authoritative (enriched entries)"`.
- **`sources.kanjidic2`** — url, records,
  `role: "licensed fallback for characters Jiten does not serve"`.
- **`sources.kanjivg`** — url, `revision`, `assets`, role.
- **`sources.jitenGlobalFrequency`** — url, `sha256`, `byteCount`,
  `retrievedDate`, `schema` (`Word,Form,Rank`), row accounting
  (`sourceRows`, `exactDuplicateRows`, `conflictingSurfaceReadingPairs`,
  `excludedConflictingRows`, `rows`, `relevantRows`, `alignedRows`,
  `ambiguousOrUnalignedRows`, `readingAssignments`), weight coverage
  (`relevantRankWeight`, `alignedRankWeight`, `rankWeightCoverage`,
  `charactersWithScores`, `readingGroupsWithScores`), `algorithm` +
  `algorithmSource` + `alignmentSource`, `metric`,
  `semantics: "rank-derived weight, not observed occurrence probability"`,
  `modifications` (exact-duplicate collapse; conflicting Word/Form rank
  exclusion; KANJIDIC-constrained alignment aggregation; CSV-only omission of
  Jiten's SQL fallback rank and 3% pruning/renormalization), `acquisition`,
  `licence`/`licenceUrl`. **This is the ONLY place the frequency-weight
  methodology text lives** — never per-entry on the card.
- **`sources.sourceSnapshot`** (when a snapshot ships) — `asset`, `sha256`,
  `byteCount`, `lockAsset`, `lockSha256`, `fileCount`,
  `role: "exact dated inputs sufficient for an offline rebuild"`.

---

## 5. Single canonical output

Exactly one ZIP (`bees-ultimate-kanji-dictionary.zip`), exactly one card
contract. One structured **term** entry per character is the shipped surface;
the native `kanji_bank` is deliberately NOT packaged (Yomitan's "view kanji"
drilldown routes to a flat renderer CSS cannot style and would hide the pie).
There are no multiple editions.

---

## Change control

Changing anything in §1–§5 requires an explicit new user contract; it is not a
developer decision. Downstream cards implement and verify against this document
as frozen.
