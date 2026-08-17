# Real-Yomitan Visual Acceptance — Feature Inventory

**Product:** Bee's Ultimate Kanji Dictionary
**Release verified:** `v2026.08.17` (revision `2026.08.17`)
**Canonical ZIP SHA-256:** `70c2ff1033a69ede7c57f3ba8f7bbe463811fafee3e52c5b8b227638306b31b9`
**Direct ZIP:** https://github.com/bee-san/bees-ultimate-kanji-dictionary/releases/latest/download/bees-ultimate-kanji-dictionary.zip
**Updater index:** https://raw.githubusercontent.com/bee-san/bees-ultimate-kanji-dictionary/main/dist/index.json
**Official Yomitan tested:** Yomitan Popup Dictionary **26.7.29.0** (unpacked official build), fresh/clean persistent profile, dictionary imported from the freshly downloaded public ZIP (not the local build).
**Import result:** 1 dictionary installed, `12,634` term entries; single canonical rich card is the sole visible result (the two other `.entry` nodes Yomitan renders are its built-in *"No results found"* / *"No dictionaries…"* placeholders, both `height: 0`, invisible — **no duplicate native flat result**).

> **Visual method note.** This session's LLM vision provider was unreachable
> (`could not resolve credentials`). Every screenshot was therefore inspected
> at full resolution with **deterministic pixel analysis** instead of an LLM
> describing pixels: donut segments detected by matching the Okabe–Ito
> colour-blind-safe palette declared in `styles.css`; ring geometry (≈45×45,
> hollow centre) confirmed; stroke-diagram "ink" measured; horizontal overflow
> and dead-space measured; theme adaptation (light/dark) confirmed. The
> raw full-page captures and the analysis scripts are archived under
> `docs/images/real-yomitan/` and the QA workspace. All captures are genuine,
> unmocked, unannotated renders of the imported dictionary in the official
> Yomitan UI.

## Representative entries exercised

| Character | Keyword | Source | Why chosen |
| :--- | :--- | :--- | :--- |
| 来 | come | Jiten (enriched) | required enriched char; 5-segment donut |
| 場 | location | Jiten (enriched) | required enriched char; phonetic family; 3-segment donut |
| 生 | life | Jiten (enriched) | required enriched char; 20 reading chips; 5-segment donut |
| 語 | word | Jiten (enriched) | phonetic family + provenance; long entry; dominant 94% segment |
| 懐 | feelings | Jiten (enriched) | longest structured entry (long/wrapping test) |
| 薔 | water pepper | Jiten (enriched) | **missing** JLPT & Grade badges (optional-enrichment omission) |
| 㐆 | to follow | KANJIDIC2 fallback | plain card: keyword/meaning/strokes only — truthful omission |

## Advertised-visible-feature inventory

Each row is a claim from the current public README / package. **PASS** = visibly
present and correct in real Yomitan. Representative character and genuine
screenshot given.

| # | Advertised visible item | Representative | Expected rendered state | Result | Genuine screenshot |
| :-- | :-- | :-- | :-- | :-- | :-- |
| 1 | Single canonical result routing (one rich term card, no native flat kanji card, no duplicate) | 来/場/生 | Exactly one visible `.entry` (`type=term`); native kanji drilldown reports "no results" by design | **PASS** | `sei-compact-light.png` |
| 2 | Kanji/keyword hero header | 生 → "life" | Large glyph + one-word keyword, clear top hierarchy | **PASS** | `sei-compact-light.png` |
| 3 | On/Kun readings as labelled chips | 生 (ON: セイ/ショウ…, KUN: い.きる…) | Real "On"/"Kun" text labels + separated chips; distinction not colour-dependent | **PASS** | `sei-compact-light.png` |
| 4 | Meanings line | 懐 → "feelings; heart; yearn; miss someone; …" | Compact distinct meaning line, no raw JSON | **PASS** | `kai-expanded-long-entry.png` |
| 5 | Rank / Grade / JLPT / stroke badges | 生 → Rank 19, Grade 1, JLPT N4, 5 strokes | Small aligned badge row; only known values shown | **PASS** | `sei-compact-light.png` |
| 6 | **Reading-share donut** (share of Jiten vocabulary entries by reading) | 生, 場, 語, 来, 懐, 薔 | Real multi-colour conic-gradient ring (≈45×45) with legend, percentages, exact entry counts, and the "counts distinct links, not occurrences… not usage frequency" disclaimer | **PASS** | `sei-expanded-light.png`, `ba-narrow-expanded.png` |
| 7 | Precisely labelled reading counts + percentages | 場 → じょう(On) 58% (2,904); ば(Kun) 42% (2,083); えき(Other) 0% (1) | Per-reading label(class): % (N entries) | **PASS** | `ba-expanded-light.png` |
| 8 | Ruby vocabulary grouped by reading (On/Kun/Other) | 語 → Kun / Other / On groups w/ furigana | Grouped example words with `<ruby>` furigana + glosses | **PASS** | `go-expanded-phonetic-family.png` |
| 9 | Phonetic-family + provenance (KanjiVG source label) | 語 → "Phonetic 音 吾: 悟 — KanjiVG", "Source: KanjiVG (kvg:phon)" | Family members + explicit KanjiVG source label | **PASS** | `go-expanded-phonetic-family.png` |
| 10 | KanjiVG stroke diagram (static, numbered, high-contrast) with text fallback | 生, 場, 来 | Static numbered stroke image (canvas 109×109) + "N strokes" text; no animation | **PASS** | `sei-expanded-light.png` |
| 11 | Progressive disclosure ("Learning aids") | 生 | Keyboard-openable `<summary>Learning aids</summary>`; only present when aids exist | **PASS** | `sei-expanded-light.png` |
| 12 | Reduced-motion / static behaviour | 来 (dark, reduce) | Stroke diagram remains static & legible; no motion | **PASS** | `rai-dark-reduced-motion-expanded.png` |
| 13 | Dark-theme adaptation | 生 (dark) | Card inherits Yomitan dark theme; donut & text remain legible | **PASS** | `sei-expanded-dark.png` |
| 14 | Narrow popup (no clipping/overflow) | 場 @ 360px | `scrollWidth == clientWidth == 360`; content wraps cleanly | **PASS** | `ba-narrow-expanded.png` |
| 15 | Long/wrapping entry | 懐 (16 strokes, 7-word gloss, 10 chips) | Clean wrapping, no clipping, no overflow | **PASS** | `kai-expanded-long-entry.png` |
| 16 | KANJIDIC2 fallback (plain card) | 㐆 → keyword/meaning/6 strokes only | No donut, no vocab, no rank, no learning aids — nothing faked | **PASS** | `kanjidic2-fallback-compact-light.png`, `kanjidic2-fallback-dark.png` |
| 17 | Truthful omission of optional enrichments | 薔 → Rank 2197 + 16 strokes only (no JLPT/Grade) | Missing badges simply absent, no placeholder/empty section | **PASS** | `bara-compact-missing-jlpt-grade.png` |
| 18 | No misleading empty sections / dead space / raw JSON | all | No empty disclosure, no JSON-like prose; card is dense & scannable | **PASS** | all captures |
| 19 | No image hover-scale zoom (belongs to GSM Hoshidicts, not this dict) | all | Static stroke image; ordinary touch/focus only; **no** zoom-on-hover | **PASS** | `styles.css` has no hover-scale; stroke image static |
| 20 | Nanori / name readings | — | README/package **do not advertise Nanori** | **N/A — not claimed** | (correctly absent) |

## Truthfulness / scope checks

- **Reading percentages** use the parent-verified **Jiten vocabulary-entry
  distribution** semantics ("share of Jiten vocabulary entries by reading"),
  with the explicit disclaimer that counts are distinct form/reading links,
  **not** occurrences in text and **not** usage frequency/probability. Verified
  against on-card legend text (e.g. 生: せい 46% / 1,817 entries).
- **No Anki / Lapis** material anywhere in the shipped package or README.
- **No fabricated frequency / probability claims.** Ranks come from Jiten;
  KANJIDIC2 fallback entries carry no rank, no examples, no donut.
- **Donut RED-flag (per task comment):** on the current public release the
  reading donut **renders as a visibly present coloured ring** for every
  supported enriched character (来/場/生/語/懐/薔) in the freshly downloaded
  public ZIP imported into official Yomitan — light, dark, and narrow. The
  earlier v2026.08.16.5 RED (data hidden in banks / not visible) is resolved:
  chromatic donut-segment pixels are measurable in the genuine captures and
  absent on the fallback entry (㐆), confirming the donut is real, not mocked
  and not faked.

## Verdict

**PASS.** Every advertised user-visible feature is present and correct in the
official Yomitan UI after importing the freshly downloaded canonical public ZIP
(SHA-256 `70c2ff10…b31b9`), with a single canonical rich card, a genuinely
rendered reading-share donut, static numbered KanjiVG stroke diagrams,
phonetic-family provenance, honest On/Kun labelling, truthful KANJIDIC2
fallback, correct omission of unsupported optional enrichments, and no
clipping/overflow/dead-space/duplicate-flat-result. Nanori is not advertised
and correctly not shown. No misleading claim remains.
