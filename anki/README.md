# Anki / Lapis card setup for Bee's Ultimate Kanji Dictionary

This is a small, copyable setup — **not** an add-on, database, or template
framework. It maps the standard Yomitan Anki field markers this dictionary
populates onto the fields of the popular **Lapis** note type, and provides
minimal front/back templates you can paste into Anki's card template editor.

Everything here reuses fields Yomitan already exports; nothing bespoke is added.
There is no audio and no pitch accent (out of scope for this dictionary).

## 1. One-time setup

1. Install the [Lapis](https://github.com/donkuri/lapis) note type in Anki
   (import its `.apkg`, or create a note type with the fields listed below).
2. In Yomitan → Settings → **Anki**, enable Anki integration and pick the Lapis
   note type for the *Term* card type. This dictionary intentionally includes
   single-character term entries so Yomitan can create the note through Lapis's
   standard term-card path.
3. Map each Lapis field to the Yomitan marker in the table in §2.
4. Paste the templates in §3 into the Lapis card's Front/Back/Styling if you
   want the compact, accessible layout tuned for this dictionary. (Optional —
   the stock Lapis templates also work; these just render our structured
   glossary, stroke diagram, and reading donut cleanly.)

## 2. Yomitan → Lapis field mapping

Set these in Yomitan's Anki field-mapping UI. Left = Lapis field name, right =
the exact Yomitan marker to enter for it.

| Lapis field           | Yomitan marker                                      | Notes                                    |
| --------------------- | --------------------------------------------------- | ---------------------------------------- |
| `Expression`          | `{expression}`                                      | the kanji character and Lapis dedupe key |
| `ExpressionFurigana`  | `{furigana-plain}`                                  | plain fallback; normally just the kanji  |
| `ExpressionReading`   | `{reading}`                                         | empty for multi-reading kanji (expected) |
| `MainDefinition`      | `{glossary-first}`                                  | the recall keyword (first glossary item) |
| `Glossary`            | `{glossary}`                                        | full structured detail and learning aids |
| `Frequency`           | `{frequencies}`                                     | display value from the frequency bank    |
| `FreqSort`            | `{frequency-harmonic-rank}`                         | numeric rank sort key                    |
| `Sentence`            | `{cloze-prefix}<b>{cloze-body}</b>{cloze-suffix}`   | optional sentence context                |

Fields left unmapped (e.g. `WordAudio`, `SentenceAudio`, `PitchPosition`,
`Picture`) stay empty — this dictionary intentionally ships no audio or pitch
data, and the templates degrade gracefully when those fields are blank.

The bundled dictionary CSS (`styles.css`) already scopes the stroke diagram,
reading donut, and phonetic family via `data-sc-bee-role` markers, so the
structured `{glossary}` renders the same way inside an Anki card as it does in
the Yomitan popup, including the reduced-motion and non-colour fallbacks.

## 3. Minimal card templates

Copy `front.html`, `back.html`, and `styling.css` from this directory into the
matching boxes of the Lapis kanji card template. They are deliberately tiny and
reuse the fields above; the heavy lifting is Yomitan's structured `{glossary}`.

## 4. Attribution reminder (share-alike)

Cards created from this dictionary embed CC BY-SA content (JMdict/KANJIDIC via
Jiten, and KanjiVG stroke data). If you share a deck built from it, keep the
attribution — see `LICENSE-data.txt` and `LICENSE-kanjivg.txt` in the release
ZIP, and the repository `README.md`.
