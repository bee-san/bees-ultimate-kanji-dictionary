# Real-Yomitan Visual Acceptance — Feature Inventory

**Product:** Bee's Ultimate Kanji Dictionary

**Release target:** the current immutable `releases/latest` artifact, cross-checked
against its tag, updater index, standalone and bundled manifests, GitHub asset
digests, and `SHA256SUMS` during publication.

**Direct ZIP:** https://github.com/bee-san/bees-ultimate-kanji-dictionary/releases/latest/download/bees-ultimate-kanji-dictionary.zip

**Updater index:** https://raw.githubusercontent.com/bee-san/bees-ultimate-kanji-dictionary/main/dist/index.json

**Official Yomitan tested:** Yomitan Popup Dictionary **26.7.29.0** (unpacked
official build), fresh/clean persistent profile, exact release ZIP imported.
**Import result:** 1 dictionary installed; `12,633` canonical records plus one
compatibility alias produce `12,634` term entries, with one canonical rich card
as the sole visible result.

The three README hero captures are pinned to the exact-final genuine-Yomitan
capture set:

| Capture | Viewport | SHA-256 |
| :--- | :--- | :--- |
| `sei-compact-light.png` | 1280×900 light | `4d9437af030ca0c6cc340fb8ff678b9efbf4f427c97371801891c0dd07816bac` |
| `sei-expanded-light.png` | 1280×900 light | `94d829f3cae333709697c21ebfe7d249310685fe858ed4268ea33cc6bc6a77d8` |
| `ba-narrow-expanded.png` | 380×820 light | `751a899c30034b1c6672fe436595784353a02e3ee72f2534fa0f64568753caa9` |

> **Visual method note.** The capture report records the imported ZIP path,
> SHA-256 before and after QA, Yomitan extension ID/version, viewport, DOM
> geometry, body text, and screenshot SHA-256. Independent pixel analysis then
> matches every expected packaged pie colour in the rendered Yomitan canvas and
> checks visible non-background pixels. The exact-final candidate used for these
> pinned images was revision `2026.08.19.2`, ZIP SHA-256
> `e7a75ebdbb8125c71bfda98e3050ba38b00749252e0832a9cc5566a64a260341`;
> post-publication QA must import the downloaded public bytes and reproduce or
> deliberately refresh this evidence. All captures are unmodified, unmocked,
> unannotated official-Yomitan renders, not generated HTML previews.

## Representative entries exercised

| Character | Keyword | Source | Why chosen |
| :--- | :--- | :--- | :--- |
| 場 | location | Jiten (enriched) | two-segment Frequency weight pie; 380 px layout |
| 生 | life | Jiten (enriched) | four named segments plus Other; long reading list |
| 語 | word | Jiten (enriched) | phonetic family and long-entry wrapping |
| 懐 | feelings | Jiten (enriched) | long gloss / disclosure wrapping |
| 薔 | water pepper | Jiten (enriched) | optional Grade/JLPT omission |
| 㐆 | to follow | KANJIDIC2 fallback | plain card with no invented weight or examples |

## Advertised-visible-feature inventory

Each **PASS** below is checked in the official Yomitan host. The first three
captures are the exact files embedded in the GitHub README; additional theme,
media, and fallback states live in the release QA evidence.

| # | Advertised visible item | Representative | Expected rendered state | Result | Evidence |
| :-- | :-- | :-- | :-- | :-- | :-- |
| 1 | Single canonical result routing | 場 / 生 | One visible rich term card; native kanji drilldown has no competing flat entry | **PASS** | `sei-compact-light.png` + host report |
| 2 | Kanji/keyword hero | 生 → “life” | Large glyph and keyword with clear hierarchy | **PASS** | `sei-compact-light.png` |
| 3 | Compact and complete readings | 生 | Three useful top chips; complete class-labelled On/Kun lists in “All readings” | **PASS** | compact + expanded 生 captures |
| 4 | Meanings | 生 → “life; genuine; birth” | Compact text line, no raw JSON | **PASS** | `sei-compact-light.png` |
| 5 | Rank / Grade / JLPT / strokes | 生 | Real-text badges inside “Details”; only known fields shown | **PASS** | host report |
| 6 | **Frequency weight pie** | 生 / 場 | Exactly one compact filled pie, left of its visible legend | **PASS** | all three README captures + pixel report |
| 7 | Top four + Other | 生 | せい 54%; い 11%; なま 9%; う 8%; Other 18% | **PASS** | both 生 captures |
| 8 | Narrow chart geometry | 場 @ 380 px | ば 61%; じょう 39%; pie remains left of legend with no overlap/overflow | **PASS** | `ba-narrow-expanded.png` |
| 9 | Ruby vocabulary | 場 / 生 | Clickable ruby surfaces and concise glosses | **PASS** | all three README captures |
| 10 | KanjiVG learning aids | 場 / 生 | Static numbered diagram, at least 80 CSS px, with text fallback | **PASS** | host + painted-pixel report |
| 11 | Progressive disclosure | 場 / 生 | Keyboard-openable native `<summary>` sections; no empty section | **PASS** | expanded captures + host report |
| 12 | Dark and reduced motion | 場 / 生 | Pie remains painted; static stroke diagram remains legible | **PASS** | 380 px dark/reduced-motion QA states |
| 13 | KANJIDIC2 fallback | 㐆 | Meanings/readings/strokes only; no pie, rank, or examples | **PASS** | fallback captures + package inspection |
| 14 | No horizontal clipping | 場 @ 380 px | `scrollWidth == clientWidth`; pie and legend do not overlap | **PASS** | `ba-narrow-expanded.png` + host geometry |
| 15 | No hover-scale animation | all | Media stays static; reduced-motion does not hide it | **PASS** | stylesheet + host QA |

## Truthfulness / scope checks

- **Frequency weight is ordinal-rank-derived relative weight, not observed token
  count, corpus probability, or a unique-entry/form count.** Each accepted Global
  CSV rank `r` contributes `1/sqrt(r)` through rank 100,000; larger ranks also
  receive `(100000/r)^2`. Per-reading sums are normalized within that kanji.
- Only complete, unique reading alignments from the official Jiten Global
  `Word,Form,Rank` CSV are accepted. Conflicting normalized `Word,Form` ranks are
  excluded rather than guessed.
- The visible chart contains no raw entry counts, payload `frequencyScore`,
  “Word variety”, comparison bar, or entry-count fallback. Missing aligned rank
  data omits the chart.
- KANJIDIC2 fallback entries carry no invented rank, examples, or Frequency
  weight pie.
- The release bundles every referenced media member; all 10,249 structured image
  references resolve in the exact-final package.

## Verdict

**PASS.** The pinned README images visibly show the new **FREQUENCY WEIGHT**
filled pie—not the stale **READING DISTRIBUTION** entry-count chart. Official
Yomitan renders the pie left of its legend at 380 px in light and
 dark/reduced-motion modes, with no overlap or horizontal clipping. The residual
semantic boundary is explicit: this is a conservative, CSV-only transform of
ordinal ranks; it is not an observed frequency distribution, and unmatched or
ambiguous forms contribute nothing.
