"""Bee's Ultimate Kanji Dictionary -- minimal Jiten -> Yomitan generator.

One module owns the whole pipeline: fetch -> normalize -> validate -> build.
Kept small and understandable on purpose. No service layers, no plugins.
"""

import hashlib
import io
import json
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

# --- Reading normalization ---------------------------------------------------

# Katakana block starts 0x30A1; hiragana 0x3041. Same layout, offset by 0x60.
_KATA_TO_HIRA_OFFSET = 0x3041 - 0x30A1


def _katakana_to_hiragana(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:  # katakana range with hiragana counterparts
            out.append(chr(code + _KATA_TO_HIRA_OFFSET))
        else:
            out.append(ch)
    return "".join(out)


def normalize_reading(reading: str) -> str:
    """Normalize a Jiten reading to a bare hiragana stem for matching.

    - trims surrounding whitespace
    - drops KANJIDIC okurigana separator "." and everything after it
    - drops leading/trailing affix marker "-"
    - converts katakana (on readings) to hiragana
    """
    if reading is None:
        return ""
    text = reading.strip()
    if not text:
        return ""
    text = text.replace("-", "")
    if "." in text:
        text = text.split(".", 1)[0]
    return _katakana_to_hiragana(text)


def classify_reading(reading: str, on_readings, kun_readings) -> str:
    """Classify a hiragana word-group reading as On, Kun, or Other."""
    norm = normalize_reading(reading)
    on = {normalize_reading(r) for r in (on_readings or []) if normalize_reading(r)}
    kun = {normalize_reading(r) for r in (kun_readings or []) if normalize_reading(r)}
    if norm in on:
        return "On"
    if norm in kun:
        return "Kun"
    return "Other"


# --- String cleaning ---------------------------------------------------------

_WS = re.compile(r"\s+")
_JUNK = {"missing", "???"}


def clean_text(text):
    """Return a cleaned display string, or None if it is junk.

    Rejects empty strings, case-insensitive 'missing', '???', and any string
    containing an angle bracket (leaked HTML/XML markup).
    """
    if not isinstance(text, str):
        return None
    collapsed = _WS.sub(" ", text).strip()
    if not collapsed:
        return None
    if collapsed.lower() in _JUNK:
        return None
    if "<" in collapsed or ">" in collapsed:
        return None
    return collapsed


def clean_meanings(meanings):
    """Clean a list of meanings: drop junk, dedupe, preserve first-seen order."""
    if not isinstance(meanings, (list, tuple)):
        return []
    out = []
    seen = set()
    for m in meanings or []:
        c = clean_text(m)
        if c is None or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def clean_strings(values):
    """Clean and deduplicate display strings while preserving their order."""
    if not isinstance(values, (list, tuple)):
        return []
    out = []
    seen = set()
    for value in values or []:
        cleaned = clean_text(value)
        if cleaned is None or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


# Furigana bracket notation: base text followed by [reading] segments, e.g.
# "場[ば]所[しょ]" or "測[はか]る" (trailing kana without a bracket).
_FURI_TOKEN = re.compile(r"([^\[\]]+)(?:\[([^\[\]]*)\])?")


def parse_furigana(furigana):
    """Parse Jiten furigana bracket notation into (base, reading) segments.

    Returns a list of (base, reading) tuples, or None if the string is empty
    or contains malformed (unbalanced) ruby brackets.
    """
    if not isinstance(furigana, str) or not furigana.strip():
        return None
    if furigana.count("[") != furigana.count("]"):
        return None
    segments = []
    pos = 0
    for match in _FURI_TOKEN.finditer(furigana):
        if match.start() != pos:  # gap => unparseable
            return None
        pos = match.end()
        base = match.group(1)
        reading = match.group(2) or ""
        if base:
            segments.append((base, reading))
    if pos != len(furigana) or not segments:
        return None
    return segments


# --- Record normalization ----------------------------------------------------

class MalformedPayload(ValueError):
    """Raised when a Jiten payload cannot be trusted / parsed."""


# Selection tuning constants (from the frozen contract).
RANK_CUTOFF = 25000       # drop example words rarer than this
MAX_GROUPS = 3            # at most 3 useful reading groups
MAX_PER_GROUP = 2         # 1-2 examples per group
MAX_EXAMPLES = 6          # never more than 6 examples total


def _clean_example(word):
    """Return a clean example dict, or None if the word is junk."""
    if not isinstance(word, dict):
        return None
    surface = clean_text(word.get("reading"))
    gloss = clean_text(word.get("mainDefinition"))
    rank = word.get("frequencyRank")
    if surface is None or gloss is None:
        return None
    if not isinstance(rank, int) or rank <= 0 or rank > RANK_CUTOFF:
        return None
    ruby = parse_furigana(word.get("readingFurigana"))
    if ruby is None:
        return None
    word_id = word.get("wordId")
    reading_index = word.get("readingIndex", 0)
    if not isinstance(word_id, int):
        return None
    return {
        "surface": surface,
        "gloss": gloss,
        "rank": rank,
        "ruby": ruby,
        "word_id": word_id,
        "reading_index": reading_index if isinstance(reading_index, int) else 0,
    }


def _select_examples(payload, on, kun):
    """Pick up to MAX_GROUPS reading groups, MAX_PER_GROUP words each, capped
    at MAX_EXAMPLES total, chosen by best (lowest) word rank -- never by
    totalWords. Falls back to topWords when grouped candidates run short.
    """
    seen = set()  # (word_id, reading_index) dedup keys, global

    def take(words):
        out = []
        for w in sorted(words, key=lambda e: e["rank"]):
            key = (w["word_id"], w["reading_index"])
            if key in seen:
                continue
            seen.add(key)
            out.append(w)
            if len(out) >= MAX_PER_GROUP:
                break
        return out

    groups = []
    for grp in payload.get("wordsByReading") or []:
        if not isinstance(grp, dict):
            continue
        reading = clean_text(grp.get("reading"))
        if reading is None:
            continue
        cands = [c for c in (_clean_example(w) for w in grp.get("words") or []) if c]
        if not cands:
            continue
        best_rank = min(c["rank"] for c in cands)
        groups.append({
            "reading": reading,
            "reading_class": classify_reading(reading, on, kun),
            "best_rank": best_rank,
            "candidates": cands,
        })

    # Sort reading groups by their best candidate rank (most common first).
    groups.sort(key=lambda g: g["best_rank"])

    result = []
    total = 0
    for g in groups:
        if len(result) >= MAX_GROUPS or total >= MAX_EXAMPLES:
            break
        words = take(g["candidates"])
        if not words:
            continue
        words = words[: max(0, MAX_EXAMPLES - total)]
        if not words:
            break
        result.append({
            "reading": g["reading"],
            "reading_class": g["reading_class"],
            "label": g["reading_class"],
            "words": words,
        })
        total += len(words)

    # Fallback: if grouped selection produced nothing, use topWords.
    if not result:
        cands = [c for c in (_clean_example(w) for w in payload.get("topWords") or []) if c]
        picked = []
        for w in sorted(cands, key=lambda e: e["rank"]):
            key = (w["word_id"], w["reading_index"])
            if key in seen:
                continue
            seen.add(key)
            picked.append(w)
            if len(picked) >= MAX_GROUPS:
                break
        if picked:
            result.append({
                "reading": "",
                "reading_class": "Other",
                "label": "Other",
                "words": picked,
            })
    return result


def _select_global_words(payload, limit=6):
    """Pick the globally highest-frequency, de-duplicated example words.

    Draws candidates from ``topWords`` and every ``wordsByReading`` group,
    cleans them, de-duplicates by (word_id, reading_index) keeping the lowest
    (best) frequency rank, and returns the ``limit`` best by Jiten frequency
    rank. Deterministic ties/fallbacks: within an equal rank the order is
    (rank, word_id, reading_index, surface, gloss). This is a GLOBAL selection
    across all readings -- NOT the per-reading example groups -- so the compact
    card can surface the single most useful vocabulary regardless of reading.
    """
    candidates = []
    sources = [payload.get("topWords") or []]
    sources.extend(
        (group.get("words") or [])
        for group in payload.get("wordsByReading") or []
        if isinstance(group, dict)
    )
    for source in sources:
        for item in source:
            clean = _clean_example(item)
            if clean is not None:
                candidates.append(clean)

    best = {}
    for item in candidates:
        key = (item["word_id"], item["reading_index"])
        prior = best.get(key)
        order = (item["rank"], item["surface"], item["gloss"])
        if prior is None or order < (prior["rank"], prior["surface"], prior["gloss"]):
            best[key] = item

    return sorted(
        best.values(),
        key=lambda item: (
            item["rank"], item["word_id"], item["reading_index"],
            item["surface"], item["gloss"],
        ),
    )[:limit]


def _reading_entry_counts(payload, on, kun):
    """Extract the complete Jiten vocabulary-entry counts per reading group.

    For every ``wordsByReading`` group, accept ``totalWords`` ONLY when it is a
    genuine positive integer -- booleans, missing values, zero, negatives,
    strings, floats, and non-finite values are all rejected. Counts are
    aggregated by the NORMALIZED reading label (so katakana on-readings collapse
    onto their hiragana stem and never silently drop a duplicate), never merely
    by On/Kun/Other class. Each retained reading carries its class as secondary
    text only. The result is deterministic: ordered by descending count, then
    normalized reading, then first source position.

    This is the complete Jiten group count (``TotalWords = g.Count()``), NOT the
    handful of preview words in ``words`` and NOT the few examples selected for
    display; it is the truthful denominator for the reading-share statistic.
    """
    counts = {}
    order = {}
    seen = 0
    for grp in payload.get("wordsByReading") or []:
        if not isinstance(grp, dict):
            continue
        total = grp.get("totalWords")
        # reject bool (a subclass of int), non-int, and non-positive values
        if isinstance(total, bool) or not isinstance(total, int) or total <= 0:
            continue
        reading = normalize_reading(grp.get("reading"))
        if not reading:
            continue
        if reading not in counts:
            counts[reading] = 0
            order[reading] = seen
            seen += 1
        counts[reading] += total

    ordered = sorted(
        counts, key=lambda r: (-counts[r], r, order[r])
    )
    return [
        {
            "reading": r,
            "count": counts[r],
            "reading_class": classify_reading(r, on, kun),
        }
        for r in ordered
    ]


def normalize_record(payload):
    """Normalize a raw Jiten kanji payload into a clean record dict.

    Raises MalformedPayload if the payload is not a usable kanji object.
    """
    if not isinstance(payload, dict):
        raise MalformedPayload("payload is not an object")
    character = payload.get("character")
    if not isinstance(character, str) or len(character) != 1:
        raise MalformedPayload("missing/invalid single-character 'character'")

    senses = clean_meanings(payload.get("meanings"))
    on = clean_strings(payload.get("onReadings"))
    kun = clean_strings(payload.get("kunReadings"))

    rank = payload.get("frequencyRank")
    rank = rank if isinstance(rank, int) and rank > 0 else None

    def _int_or_none(v):
        return v if isinstance(v, int) else None

    return {
        "character": character,
        "keyword": senses[0] if senses else None,
        "senses": senses,
        "on": on,
        "kun": kun,
        "frequency_rank": rank,
        "stroke_count": _int_or_none(payload.get("strokeCount")),
        "grade": _int_or_none(payload.get("grade")),
        "jlpt": _int_or_none(payload.get("jlptLevel")),
        "examples": _select_examples(payload, on, kun),
        "global_words": _select_global_words(payload),
        "reading_entry_counts": _reading_entry_counts(payload, on, kun),
    }


# --- KANJIDIC2 simple licensed fallback --------------------------------------

# KANJIDIC2 (EDRDG) is the simple licensed fallback source for characters Jiten
# does not serve. We use ONLY the fields it plainly provides -- English
# meanings, Japanese on/kun/nanori readings, stroke count, grade, JLPT -- and
# never manufacture examples, ranks, percentages, donuts, phonetic families, or
# Jiten attribution for data Jiten did not provide. Jiten stays authoritative
# wherever it has a character; KANJIDIC2 only fills genuine gaps.


def parse_kanjidic2(xml_text):
    """Parse a KANJIDIC2 XML document into {character: supported-fields}.

    Extracts only the fields this generator honestly supports:
      meanings  -- English glosses (<meaning> with no m_lang attribute)
      on / kun  -- ja_on / ja_kun readings, preserved verbatim
      nanori    -- name readings (<nanori> under reading_meaning)
      stroke_count / grade / jlpt -- integer misc fields when present

    Entries are skipped when the <literal> is not a single Unicode scalar, or
    when the character carries no useful data (no English meaning AND no on/kun
    reading) -- so no blank/filler records are ever produced. Deterministic:
    keyed by character; the caller orders by codepoint.
    """
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_text)
    out = {}
    for char in root.findall("character"):
        literal = (char.findtext("literal") or "").strip()
        if len(literal) != 1:
            continue  # not a single-character kanji
        rm = char.find("reading_meaning")
        if rm is None:
            continue  # pure index entry, nothing to surface

        meanings, on, kun, nanori = [], [], [], []
        for m in rm.iter("meaning"):
            if m.get("m_lang") is None:  # English glosses have no m_lang
                txt = (m.text or "").strip()
                if txt:
                    meanings.append(txt)
        for r in rm.iter("reading"):
            txt = (r.text or "").strip()
            if not txt:
                continue
            if r.get("r_type") == "ja_on":
                on.append(txt)
            elif r.get("r_type") == "ja_kun":
                kun.append(txt)
        for n in rm.findall("nanori"):
            txt = (n.text or "").strip()
            if txt:
                nanori.append(txt)

        if not (meanings or on or kun):
            continue  # no useful data -> do not manufacture a blank entry

        misc = char.find("misc")

        def _int(tag):
            if misc is None:
                return None
            txt = misc.findtext(tag)
            if txt is None:
                return None
            try:
                return int(txt)
            except ValueError:
                return None

        out[literal] = {
            "meanings": meanings,
            "on": on,
            "kun": kun,
            "nanori": nanori,
            "stroke_count": _int("stroke_count"),
            "grade": _int("grade"),
            "jlpt": _int("jlpt"),
        }
    return out


def kanjidic2_record(character, fields):
    """Build a normalized fallback record from parsed KANJIDIC2 fields.

    Shares the exact record shape produced by normalize_record so every bank
    builder works unchanged. Honest omissions: frequency_rank is None (Jiten's
    rank scale is not KANJIDIC2's newspaper-frequency and the two must not be
    conflated), and examples is empty (KANJIDIC2 supplies no example words), so
    no frequency meta, reading-distribution donut, or enrichment is ever
    fabricated for these characters.
    """
    senses = clean_meanings(fields.get("meanings"))
    on = clean_strings(fields.get("on"))
    kun = clean_strings(fields.get("kun"))
    return {
        "character": character,
        "keyword": senses[0] if senses else None,
        "senses": senses,
        "on": on,
        "kun": kun,
        "frequency_rank": None,   # KANJIDIC2 provides no Jiten-comparable rank
        "stroke_count": fields.get("stroke_count"),
        "grade": fields.get("grade"),
        "jlpt": fields.get("jlpt"),
        "examples": [],            # never invent example words
        "global_words": [],        # KANJIDIC2 supplies no example vocabulary
        "reading_entry_counts": [],  # never fabricate a reading-share statistic
    }


def merge_kanjidic2(jiten_records, kanjidic2_index):
    """Merge Jiten records with KANJIDIC2 fallbacks, Jiten authoritative.

    Every Jiten record is preserved verbatim (its enriched keyword, meanings,
    readings, rank, examples, and reading distribution are untouched). For each
    KANJIDIC2 character ABSENT from Jiten, a clean fallback record is appended.
    The result is unique by character (duplicates impossible) and ordered
    deterministically by Unicode codepoint.
    """
    present = {r["character"] for r in jiten_records}
    merged = list(jiten_records)
    for char in sorted(kanjidic2_index, key=lambda c: ord(c)):
        if char in present:
            continue  # Jiten wins on every duplicate
        merged.append(kanjidic2_record(char, kanjidic2_index[char]))
        present.add(char)
    merged.sort(key=lambda r: ord(r["character"]))
    return merged


# --- Reading-distribution donut ----------------------------------------------

# At most this many labelled donut segments; any tail collapses into "Other".
MAX_DONUT_SEGMENTS = 5

# Accessible, colour-blind-distinguishable palette (Okabe-Ito derived). Order is
# stable so identical inputs always produce identical colours.
_DONUT_COLORS = [
    "#0072b2",  # blue
    "#d55e00",  # vermillion
    "#009e73",  # green
    "#cc79a7",  # reddish purple
    "#e69f00",  # orange
    "#767676",  # grey ("Other" / overflow)
]
_DONUT_OTHER_COLOR = "#767676"


def _largest_remainder_percents(counts, total):
    """Round counts to integer percents that sum to exactly 100.

    Uses the largest-remainder method so the reported percentages are truthful
    (each within 1 of its exact share) and always total 100. Ties break on the
    original index for deterministic output.
    """
    if total <= 0 or not counts:
        return []
    exact = [c * 100.0 / total for c in counts]
    floors = [int(x) for x in exact]
    remainder = 100 - sum(floors)
    order = sorted(range(len(counts)), key=lambda i: (-(exact[i] - floors[i]), i))
    for i in order[: max(0, remainder)]:
        floors[i] += 1
    return floors


def reading_distribution(record):
    """Compute the truthful share of Jiten vocabulary entries by reading.

    Uses the COMPLETE Jiten group totals in ``record['reading_entry_counts']``
    (each ``wordsByReading[].totalWords``, the full vocabulary-entry count for a
    reading group) -- NEVER the 1-2 example words displayed in the entry. Each
    segment keeps its actual reading plus its On/Kun/Other class as secondary
    text. Segments are ordered by descending count, then normalized reading,
    then first source position (already the order of reading_entry_counts). At
    most ``MAX_DONUT_SEGMENTS`` segments: the top readings plus one explicit
    "Other" segment folding the remaining tail. Percentages are integers summing
    to exactly 100 via the largest-remainder method; the exact entry count rides
    alongside so a nonzero tiny group rendered as 0%% stays explicit.

    Returns {"total": N, "segments": [...]} with total 0 and no segments when no
    valid positive group total exists -- the caller then omits the statistic
    entirely rather than fabricating one from examples or ranks.
    """
    counts = record.get("reading_entry_counts") or []
    total = sum(c["count"] for c in counts)
    if total <= 0 or not counts:
        return {"total": 0, "segments": []}

    # counts is already deterministically ordered (desc count, reading, source).
    if len(counts) > MAX_DONUT_SEGMENTS:
        head = counts[: MAX_DONUT_SEGMENTS - 1]
        tail = counts[MAX_DONUT_SEGMENTS - 1:]
        kept = [(c["reading"], c["reading_class"], c["count"]) for c in head]
        overflow = sum(c["count"] for c in tail)
        kept.append(("", "Other", overflow))
        collapsed = True
    else:
        kept = [(c["reading"], c["reading_class"], c["count"]) for c in counts]
        collapsed = False

    seg_counts = [c for _, _, c in kept]
    percents = _largest_remainder_percents(seg_counts, total)

    segments = []
    color_i = 0
    for (reading, cls, cnt), pct in zip(kept, percents):
        if cls == "Other" and reading == "":
            color = _DONUT_OTHER_COLOR
        else:
            color = _DONUT_COLORS[color_i % (len(_DONUT_COLORS) - 1)]
            color_i += 1
        segments.append({
            "reading": reading,
            "reading_class": cls,
            "count": cnt,
            "percent": pct,
            "color": color,
        })
    return {"total": total, "segments": segments, "collapsed": collapsed}


DONUT_TITLE = "Reading distribution"


def _segment_label(segment):
    """Human legend label for a segment: reading (Class) or plain Other."""
    reading = segment.get("reading") or ""
    cls = segment.get("reading_class") or "Other"
    if not reading:
        return "Other"
    return f"{reading} ({cls})"


# --- Per-entry raster reading-distribution chart (packaged PNG media) ---------

# The compact card ships the reading distribution as a deterministic per-entry
# PNG packaged as Yomitan dictionary media (referenced through a supported
# structured-content <img>), rather than an inline CSS conic-gradient ring.
# A raster image is what official Yomitan renders from an archive `path`, so it
# survives every renderer / theme without relying on inline gradient support.

# Fixed square canvas. 128px keeps the packaged bytes tiny while staying crisp
# when the popup scales the image down to a few em.
READING_CHART_SIZE = 128


def _hex_to_rgba(hex_color, alpha=255):
    """Convert a #rrggbb string to an (r, g, b, a) tuple."""
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def reading_distribution_asset_name(character):
    """Archive path for a character's packaged reading-distribution PNG.

    Zero-padded lowercase hex code point under ``reading-distribution/`` so the
    path is deterministic, collision-free, and independent of the glyph itself
    (which may be filesystem-hostile).
    """
    return f"reading-distribution/{ord(character):05x}.png"


def build_reading_distribution_png(record):
    """Render the reading-distribution donut for a record as PNG bytes.

    Deterministic: the same record always yields byte-identical output. Uses the
    same truthful segment data as :func:`reading_distribution` (share of Jiten
    vocabulary entries by reading), drawn as an anti-aliased donut ring on a
    128x128 transparent RGBA canvas. Returns ``None`` when the record has no
    valid positive Jiten reading total (e.g. KANJIDIC2-only records), so the
    caller omits the chart rather than fabricating one.
    """
    from PIL import Image, ImageDraw

    dist = reading_distribution(record)
    if dist["total"] <= 0 or not dist["segments"]:
        return None

    # Supersample for smooth arc edges, then downsample to the target size.
    scale = 4
    size = READING_CHART_SIZE * scale
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = int(size * 0.06)
    box = [pad, pad, size - pad, size - pad]

    # Draw wedges by cumulative percent. Start at -90 deg (12 o'clock) and go
    # clockwise so the largest (first) segment leads from the top.
    start = -90.0
    for seg in dist["segments"]:
        sweep = seg["percent"] * 3.6  # percent -> degrees
        end = start + sweep
        # A zero-percent (but nonzero-count) tail contributes no wedge; skip it
        # so we never emit a degenerate arc.
        if sweep > 0:
            draw.pieslice(box, start, end, fill=_hex_to_rgba(seg["color"]))
        start = end

    # Punch a centred transparent hole to make it a donut (ring), not a pie.
    hole = int(size * 0.30)
    cx = cy = size // 2
    draw.ellipse([cx - hole, cy - hole, cx + hole, cy + hole], fill=(0, 0, 0, 0))

    img = img.resize((READING_CHART_SIZE, READING_CHART_SIZE), Image.LANCZOS)

    buf = io.BytesIO()
    # Fixed PNG encoder options + no timestamp chunk => byte-deterministic.
    img.save(buf, format="PNG", optimize=False, compress_level=9)
    return buf.getvalue()


def reading_chart_alt_text(record):
    """Concise text equivalent of the reading-distribution chart (alt text)."""
    dist = reading_distribution(record)
    if dist["total"] <= 0:
        return ""
    parts = [
        f"{_segment_label(s)} {s['percent']} percent"
        for s in dist["segments"]
    ]
    return DONUT_TITLE + ": " + ", ".join(parts)


def build_reading_chart_node(record):
    """Build the packaged-PNG reading-distribution chart structured content.

    The graphic is a single supported ``img`` referencing the packaged PNG by
    its archive path; a caption and a visible text legend (colour swatch +
    reading (class) + percent + exact entry count) carry the same data as real
    text, so colour is never the sole channel and the chart degrades gracefully
    when the image cannot load.
    """
    dist = reading_distribution(record)
    if dist["total"] == 0:
        return None
    segments = dist["segments"]
    alt = reading_chart_alt_text(record)

    caption = {
        "tag": "div",
        "data": {"beeRole": "donut-caption"},
        "content": DONUT_TITLE,
    }

    image = {
        "tag": "img",
        "data": {"beeRole": "reading-chart"},
        "path": reading_distribution_asset_name(record["character"]),
        "width": 4.6,
        "height": 4.6,
        "sizeUnits": "em",
        "alt": alt,
        "title": alt,
        "background": False,
    }

    legend_items = []
    for s in segments:
        swatch = {
            "tag": "span",
            "data": {"beeRole": "donut-swatch"},
            "style": {"background": s["color"], "color": s["color"]},
            "content": "\u25a0",  # filled square, visible even without CSS colour
        }
        legend_items.append({
            "tag": "li",
            "content": [
                swatch,
                f"{_segment_label(s)}: {s['percent']}% ({s['count']:,} entries)",
            ],
        })
    legend = {
        "tag": "ul",
        "data": {"beeRole": "donut-legend"},
        "content": legend_items,
    }

    return {
        "tag": "div",
        "data": {"beeRole": "reading-donut"},
        "lang": "en",
        "title": alt,
        "content": [
            caption,
            {"tag": "div", "data": {"beeRole": "donut-graphic"},
             "content": [image]},
            legend,
        ],
    }


# --- KanjiVG-sourced phonetic families ---------------------------------------

# KanjiVG records the phonetic component via a kvg:phon attribute on the
# phonetic-component sub-group (e.g. 時 = 日 + 寺, where the 寺 group carries
# kvg:phon="寺"). We only ever surface a relationship KanjiVG itself marks.
_KVG_PHON = re.compile(r'kvg:phon="([^"]+)"')

PHONETIC_SOURCE = "KanjiVG"


def extract_phonetic_component(svg_text, character):
    """Return the phonetic component KanjiVG marks for a character, else None.

    KanjiVG attaches kvg:phon to the phonetic sub-component group anywhere in
    the glyph tree; its value is the phonetic component of the whole character.
    Never inferred. A marker equal to the character itself is not a phonetic
    family relationship and returns None. When several markers appear, the first
    (outermost) is used deterministically.
    """
    if not isinstance(svg_text, str) or not isinstance(character, str):
        return None
    for comp in _KVG_PHON.findall(svg_text):
        if comp and comp != character:
            return comp
    return None


def build_phonetic_families(phon_map, ranks):
    """Group characters that share a phonetic component into ordered families.

    phon_map: {character: phonetic_component} sourced from KanjiVG kvg:phon.
    ranks:    {character: frequency_rank} used to order members usefully.

    Members are ordered by frequency rank ascending (most common first), then
    by stable Unicode codepoint for characters without a rank. Singleton
    components (only one member) are dropped -- a family needs >= 2 members.
    Returns {component: {"component", "members", "source"}}, deterministic.
    """
    grouped = {}
    for char, comp in (phon_map or {}).items():
        if not comp or comp == char:
            continue
        grouped.setdefault(comp, set()).add(char)

    families = {}
    for comp in sorted(grouped, key=lambda c: [ord(x) for x in c]):
        members = grouped[comp]
        if len(members) < 2:
            continue

        def sort_key(ch):
            r = ranks.get(ch)
            # ranked chars first (by rank), unranked after (by codepoint)
            return (0, r, ord(ch)) if isinstance(r, int) else (1, 0, ord(ch))

        ordered = sorted(members, key=sort_key)
        families[comp] = {
            "component": comp,
            "members": ordered,
            "source": PHONETIC_SOURCE,
        }
    return families


def build_phonetic_family_node(character, family):
    """Build a structured-content node for a character's phonetic family.

    Names the shared phonetic component, lists the sibling members (excluding
    the character itself), and records a compact source attribution. Returns
    None when the character has no recorded family.
    """
    if not family:
        return None
    siblings = [m for m in family["members"] if m != character]
    if not siblings:
        return None
    comp = family["component"]
    body = [
        {"tag": "span", "data": {"beeRole": "phon-label"},
         "content": f"Phonetic \u97f3 {comp}: "},
        {"tag": "span", "data": {"beeRole": "phon-members"},
         "content": "\u3001".join(siblings)},
        {"tag": "span", "data": {"beeRole": "phon-source"},
         "title": f"Source: {family['source']} (kvg:phon)",
         "content": f" \u2014 {family['source']}"},
    ]
    return {
        "tag": "div",
        "data": {"beeRole": "phonetic-family"},
        "content": body,
    }


# --- KanjiVG stroke / component enrichment -----------------------------------

_SVG_NS = "http://www.w3.org/2000/svg"
_KVG_ELEMENT = re.compile(r'kvg:element="([^"]+)"')
_KVG_PATH = re.compile(r'<path\b[^>]*\bd="([^"]+)"[^>]*>')
_KVG_STROKE_NUMBER = re.compile(
    r'<text transform="matrix\(1 0 0 1 (-?[0-9]+(?:\.[0-9]+)?) '
    r'(-?[0-9]+(?:\.[0-9]+)?)\)">([1-9][0-9]*)</text>'
)


def kanjivg_asset_name(character):
    """Deterministic bundled-asset path for a character's KanjiVG SVG."""
    return f"kanjivg/{ord(character):05x}.svg"


def parse_kanjivg(svg_text, character):
    """Extract deterministic stroke count + component list from a KanjiVG SVG.

    stroke_count = number of <path> stroke elements. components = the ordered,
    de-duplicated list of kvg:element values (the character and its parts).
    Returns None when the text has no stroke paths.
    """
    if not isinstance(svg_text, str):
        return None
    paths = _KVG_PATH.findall(svg_text)
    if not paths:
        return None
    seen = set()
    components = []
    for el in _KVG_ELEMENT.findall(svg_text):
        if el and el not in seen:
            seen.add(el)
            components.append(el)
    if character not in components:
        components.insert(0, character)
    return {
        "stroke_count": len(paths),
        "components": components,
        "asset": kanjivg_asset_name(character),
    }


def sanitize_kanjivg_svg(svg_text, character):
    """Rebuild a minimal, safe, static SVG from a KanjiVG source string.

    Strips the XML declaration, DOCTYPE, comments, kvg namespaced attributes,
    labels that do not match KanjiVG's strict numeric form, and anything
    script/external (scripts, event handlers, xlink, <image>). Rebuilds a clean
    <svg> containing the stroke <path> geometry and safe stroke numbers as a
    deterministic, static high-contrast stroke-order diagram. Yomitan rasterizes
    dictionary media into a canvas, so animation is intentionally omitted.
    Output is deterministic and safe for reduced-motion users.
    """
    paths = _KVG_PATH.findall(svg_text)
    strokes = "".join(
        f'<path class="bee-stroke-outline" fill="none" stroke="#ffffff" '
        f'stroke-width="5" stroke-linecap="round" stroke-linejoin="round" d="{d}"/>'
        f'<path class="bee-stroke-ink" fill="none" stroke="#0072b2" '
        f'stroke-width="3" stroke-linecap="round" stroke-linejoin="round" d="{d}"/>'
        for d in paths
    )
    numbers = "".join(
        f'<text class="bee-stroke-number" x="{x}" y="{y}" '
        'font-family="sans-serif" font-size="8" font-weight="700" '
        'fill="#0072b2" stroke="#ffffff" stroke-width="2" '
        f'paint-order="stroke" stroke-linejoin="round">{number}</text>'
        for x, y, number in _KVG_STROKE_NUMBER.findall(svg_text)
    )
    title = f"Stroke order for {character}"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 109 109" '
        'width="109" height="109" role="img" '
        f'aria-label="{title}"><title>{title}</title>'
        f"{strokes}{numbers}</svg>"
    )


def build_stroke_node(character, info):
    """Build a structured-content node for stroke order + components.

    When a bundled SVG asset is available, references it via an img node (which
    carries alt text) and follows it with a text line naming the stroke count
    and components -- so the information survives even if the media, SVG, or
    character asset is unavailable. Returns None when no info is present.
    """
    if not info:
        return None
    strokes = info.get("stroke_count")
    components = [c for c in (info.get("components") or []) if c != character]
    asset = info.get("asset")

    text_bits = []
    if isinstance(strokes, int):
        text_bits.append(f"{strokes} strokes")
    if components:
        text_bits.append("components " + "\u3001".join(components))
    text_line = {
        "tag": "div",
        "data": {"beeRole": "stroke-text"},
        "content": " \u00b7 ".join(text_bits) if text_bits else "stroke data",
    }

    content = []
    if asset:
        alt = f"{character} stroke order diagram"
        if isinstance(strokes, int):
            alt += f" ({strokes} strokes)"
        content.append({
            "tag": "img",
            "path": asset,
            "alt": alt,
            "title": alt,
            "collapsible": True,
            "collapsed": False,
            "background": False,
            "data": {"beeRole": "stroke-image"},
        })
    content.append(text_line)
    return {
        "tag": "div",
        "data": {"beeRole": "stroke-order"},
        "content": content,
    }


# --- Yomitan bank builders ---------------------------------------------------

def _jlpt_label(level):
    """Map a numeric JLPT level to Yomitan-style 'N#', else None."""
    if isinstance(level, int) and 1 <= level <= 5:
        return f"N{level}"
    return None


def _ruby_node(segments):
    """Build a structured-content node for one furigana word from ruby segs."""
    content = []
    for base, reading in segments:
        if reading:
            content.append({"tag": "ruby", "content": [base, {"tag": "rt", "content": reading}]})
        else:
            content.append(base)
    return content


def _reading_group_node(label, readings):
    """Build a labelled reading group whose readings are separate chips.

    Renders as: a small class label ("On"/"Kun") followed by one chip per
    reading. The label is real text so the On/Kun distinction never depends on
    colour. Returns None when there are no readings for this class.
    """
    if not readings:
        return None
    chips = [
        {"tag": "span", "data": {"beeRole": "reading-chip"}, "content": reading}
        for reading in readings
    ]
    return {
        "tag": "div",
        "data": {"beeRole": "reading-group"},
        "content": [
            {"tag": "span", "data": {"beeRole": "reading-label"}, "content": label},
            {"tag": "span", "data": {"beeRole": "reading-chips"}, "content": chips},
        ],
    }


def _badge_node(text):
    """A single small metadata badge with real text (e.g. 'Rank 57')."""
    return {"tag": "span", "data": {"beeRole": "badge"}, "content": text}


# Number of top readings shown above the fold, selected by Jiten totals.
COMPACT_READING_COUNT = 3
# Exactly this many globally highest-frequency words fill the compact grid.
COMPACT_WORD_COUNT = 6


def _top_reading_chip_row(record):
    """Top readings (by Jiten vocabulary-entry totals) as plain chips.

    Selects the ``COMPACT_READING_COUNT`` readings with the highest Jiten
    vocabulary-entry totals from ``reading_entry_counts`` (already ordered
    desc count, reading, source) and renders each as a plain chip. This is a
    single mixed row -- NOT split into On/Kun labelled groups -- because the
    compact card ranks readings by how much vocabulary actually uses them, not
    by reading class. Returns None when the record carries no reading totals.
    """
    counts = record.get("reading_entry_counts") or []
    top = [c["reading"] for c in counts[:COMPACT_READING_COUNT] if c.get("reading")]
    if not top:
        return None
    chips = [
        {"tag": "span", "data": {"beeRole": "reading-chip"}, "lang": "ja",
         "content": reading}
        for reading in top
    ]
    return {
        "tag": "div",
        "data": {"beeRole": "reading-chips"},
        "content": chips,
    }


def _global_vocab_grid(record):
    """Exactly the six globally highest-frequency words in a responsive grid.

    Uses ``record['global_words']`` (top six de-duplicated words by Jiten
    frequency rank across all readings). Each cell carries the ruby surface plus
    a concise gloss. The grid renders 3-left / 3-right on ordinary popups and a
    single column on narrow popups (see styles.css). Returns None when the
    record has no global words (KANJIDIC2-only entries).
    """
    words = record.get("global_words") or []
    if not words:
        return None
    cells = []
    for ex in words:
        cells.append({
            "tag": "div",
            "data": {"beeRole": "vocab-item"},
            "content": [
                {"tag": "span", "data": {"beeRole": "vocab-word"},
                 "lang": "ja", "content": _ruby_node(ex["ruby"])},
                {"tag": "span", "data": {"beeRole": "vocab-gloss"},
                 "content": " \u2014 " + ex["gloss"]},
            ],
        })
    return {
        "tag": "div",
        "data": {"beeRole": "vocab-grid"},
        "content": cells,
    }


def _reading_disclosure(record):
    """Collapsed On/Kun reading disclosure (full lists, class-labelled)."""
    reading_groups = []
    on_group = _reading_group_node("On", record["on"])
    if on_group is not None:
        reading_groups.append(on_group)
    kun_group = _reading_group_node("Kun", record["kun"])
    if kun_group is not None:
        reading_groups.append(kun_group)
    if not reading_groups:
        return None
    return {
        "tag": "details",
        "data": {"beeRole": "section"},
        "content": [
            {"tag": "summary", "content": "All readings"},
            {"tag": "div", "data": {"beeRole": "readings"}, "content": reading_groups},
        ],
    }


def _metadata_disclosure(record):
    """Collapsed rank / grade / JLPT / strokes disclosure."""
    badges = []
    if record["frequency_rank"] is not None:
        badges.append(_badge_node(f"Rank {record['frequency_rank']}"))
    if record["grade"] is not None:
        badges.append(_badge_node(f"Grade {record['grade']}"))
    jl = _jlpt_label(record["jlpt"])
    if jl:
        badges.append(_badge_node(f"JLPT {jl}"))
    if record["stroke_count"] is not None:
        badges.append(_badge_node(f"{record['stroke_count']} strokes"))
    if not badges:
        return None
    return {
        "tag": "details",
        "data": {"beeRole": "section"},
        "content": [
            {"tag": "summary", "content": "Details"},
            {"tag": "div", "data": {"beeRole": "badge-row"}, "content": badges},
        ],
    }


def _more_vocab_disclosure(record):
    """Collapsed additional vocabulary grouped by reading (beyond the six)."""
    groups = []
    shown = {(w["word_id"], w["reading_index"]) for w in (record.get("global_words") or [])}
    for group in record["examples"]:
        items = []
        for ex in group["words"]:
            if (ex["word_id"], ex["reading_index"]) in shown:
                continue
            line = [{"tag": "span", "data": {"beeRole": "vocab-word"},
                     "lang": "ja", "content": _ruby_node(ex["ruby"])},
                    {"tag": "span", "data": {"beeRole": "vocab-gloss"},
                     "content": " \u2014 " + ex["gloss"]}]
            items.append({"tag": "li", "data": {"beeRole": "vocab-item"}, "content": line})
        if items:
            groups.append({
                "tag": "div",
                "data": {"beeRole": "vocab-group"},
                "content": [
                    {"tag": "div", "data": {"beeRole": "vocab-label"},
                     "content": group["label"]},
                    {"tag": "ul", "data": {"beeRole": "vocab-list"}, "content": items},
                ],
            })
    if not groups:
        return None
    return {
        "tag": "details",
        "data": {"beeRole": "section"},
        "content": [
            {"tag": "summary", "content": "More vocabulary"},
            {"tag": "div", "data": {"beeRole": "more-vocab"}, "content": groups},
        ],
    }


def _detail_content(record, enrichment=None):
    """Build the structured-content body for a term entry's detail item.

    The compact card puts only the highest-value material above the fold, in a
    fixed order:

      1. a HERO header pairing the kanji glyph with its keyword,
      2. the TOP THREE readings by Jiten vocabulary-entry totals as plain chips
         (not split into On/Kun groups),
      3. a compact meaning line,
      4. the reading-distribution chart as a packaged raster PNG (omitted, never
         faked, for KANJIDIC2-only records) with alt text + a visible legend,
      5. exactly SIX globally highest-frequency de-duplicated words in a
         responsive two-column (3-left / 3-right) grid with ruby + gloss.

    Everything secondary -- the complete On/Kun lists, rank/grade/JLPT/strokes,
    additional vocabulary, the stroke-order diagram, the phonetic family, and
    sources -- lives in collapsed Yomitan ``details`` disclosures BELOW the
    fold. Every graphic still has a real text equivalent (chip text, legend,
    alt text, badge text), so colour or CSS is never the only carrier of
    information. Empty disclosures are never emitted.
    """
    char = record["character"]
    keyword = record["keyword"] or char
    body = []

    # 1. Hero header: the glyph paired with its keyword.
    body.append({
        "tag": "div",
        "data": {"beeRole": "hero"},
        "content": [
            {"tag": "span", "data": {"beeRole": "hero-glyph"},
             "lang": "ja", "content": char},
            {"tag": "span", "data": {"beeRole": "hero-keyword"}, "content": keyword},
        ],
    })

    # 2. Top readings by Jiten vocabulary totals (mixed chips, no On/Kun split).
    chip_row = _top_reading_chip_row(record)
    if chip_row is not None:
        body.append(chip_row)

    # 3. Meaning: a compact, distinct hierarchy line.
    if record["senses"]:
        body.append({
            "tag": "div",
            "data": {"beeRole": "meaning"},
            "content": "; ".join(record["senses"]),
        })

    # 4. Reading-distribution chart as a packaged raster PNG. Omitted (never
    #    faked) when the record has no valid Jiten reading totals.
    chart = build_reading_chart_node(record)
    if chart is not None:
        body.append(chart)

    # 5. Exactly six globally highest-frequency words in a two-column grid.
    grid = _global_vocab_grid(record)
    if grid is not None:
        body.append(grid)

    # 6. Secondary material -> collapsed, keyboard-accessible disclosures.
    reading_section = _reading_disclosure(record)
    if reading_section is not None:
        body.append(reading_section)

    meta_section = _metadata_disclosure(record)
    if meta_section is not None:
        body.append(meta_section)

    more_vocab = _more_vocab_disclosure(record)
    if more_vocab is not None:
        body.append(more_vocab)

    # Learning aids (phonetic family + stroke-order diagram) stay in their own
    # collapsed section, only when enrichment data actually exists.
    if enrichment:
        aids = []
        fam = (enrichment.get("families_by_char") or {}).get(char)
        phon_node = build_phonetic_family_node(char, fam)
        if phon_node is not None:
            aids.append(phon_node)
        stroke_info = (enrichment.get("strokes") or {}).get(char)
        stroke_node = build_stroke_node(char, stroke_info)
        if stroke_node is not None:
            aids.append(stroke_node)
        if aids:
            body.append({
                "tag": "details",
                "data": {"beeRole": "section"},
                "content": [
                    {"tag": "summary", "content": "Learning aids"},
                    {"tag": "div", "data": {"beeRole": "aids"}, "content": aids},
                ],
            })

    return [{"tag": "div", "data": {"beeRole": "detail"}, "content": body}]


def build_term_entry(record, enrichment=None):
    """Build one Yomitan term-bank entry for a normalized kanji record.

    `enrichment` (optional) threads the visual learning aids into the detail.
    """
    keyword = record["keyword"] or record["character"]
    glossary = [
        keyword,
        {"type": "structured-content", "content": _detail_content(record, enrichment)},
    ]
    return [
        record["character"],   # expression
        "",                    # reading: empty for a multi-reading kanji
        "",                    # definition tags
        "",                    # rule identifiers
        0,                     # score (neutral)
        glossary,
        ord(record["character"]),  # sequence = Unicode code point
        "",                    # term tags
    ]


def build_kanji_entry(record):
    """Build one Yomitan kanji-bank native entry for a normalized record."""
    stats = {}
    if record["frequency_rank"] is not None:
        stats["Frequency rank"] = str(record["frequency_rank"])
    if record["grade"] is not None:
        stats["Grade"] = str(record["grade"])
    jl = _jlpt_label(record["jlpt"])
    if jl:
        stats["JLPT"] = jl
    if record["stroke_count"] is not None:
        stats["Strokes"] = str(record["stroke_count"])
    return [
        record["character"],
        " ".join(record["on"]),
        " ".join(record["kun"]),
        "",                    # tags
        list(record["senses"]),
        stats,
    ]


def build_term_meta(record):
    """Frequency meta for the term bank, or None when rank is unknown."""
    if record["frequency_rank"] is None:
        return None
    return [record["character"], "freq", record["frequency_rank"]]


def build_kanji_meta(record):
    """Frequency meta for the kanji bank, or None when rank is unknown."""
    if record["frequency_rank"] is None:
        return None
    return [record["character"], "freq", record["frequency_rank"]]


def build_alias_term_entry(alias, canonical):
    """Build a term-only alias entry pointing an old form to its canonical form.

    Used for compatibility characters Jiten does not serve (e.g. 髙 -> 高).
    Contributes no native-kanji or frequency data.
    """
    detail = {
        "type": "structured-content",
        "content": [{"tag": "div", "content": f"Variant form of {canonical}"}],
    }
    return [
        alias,
        "",
        "",
        "",
        0,
        [f"variant of {canonical}", detail],
        ord(alias),
        "",
    ]


def build_banks(records, aliases=None, enrichment=None):
    """Assemble the four Yomitan banks from normalized records + term aliases.

    Records are sorted deterministically by Unicode code point. Aliases (old
    form -> canonical form) contribute only term-bank entries. `enrichment`
    (optional) threads visual learning aids into the term entries only -- the
    kanji/meta/frequency banks are untouched, so no percentage or totalWords
    statistic can leak into them. Returns a dict with the four banks.
    """
    aliases = aliases or {}
    ordered = sorted(records, key=lambda r: ord(r["character"]))

    term_bank = []
    term_meta_bank = []
    kanji_bank = []
    kanji_meta_bank = []

    for r in ordered:
        term_bank.append(build_term_entry(r, enrichment))
        tm = build_term_meta(r)
        if tm is not None:
            term_meta_bank.append(tm)
        kanji_bank.append(build_kanji_entry(r))
        km = build_kanji_meta(r)
        if km is not None:
            kanji_meta_bank.append(km)

    for alias in sorted(aliases, key=ord):
        term_bank.append(build_alias_term_entry(alias, aliases[alias]))

    # Keep term bank ordered by code point including aliases.
    term_bank.sort(key=lambda e: ord(e[0]))

    return {
        "term_bank": term_bank,
        "term_meta_bank": term_meta_bank,
        "kanji_bank": kanji_bank,
        "kanji_meta_bank": kanji_meta_bank,
    }


# --- Serialization, index, hashing, ZIP --------------------------------------

TITLE = "Bee's Ultimate Kanji Dictionary"
REPO = "bee-san/bees-ultimate-kanji-dictionary"
ZIP_NAME = "bees-ultimate-kanji-dictionary.zip"

ATTRIBUTION = (
    "Dictionary data derived from Jiten (https://jiten.moe) and directly from "
    "KANJIDIC2, using JMdict/KANJIDIC data from the Electronic Dictionary "
    "Research and Development Group (EDRDG). Data is redistributed under CC BY-SA 4.0; "
    "see https://creativecommons.org/licenses/by-sa/4.0/ and "
    "https://www.edrdg.org/edrdg/licence.html."
)

LICENSE_DATA_TEXT = (
    "Dictionary data derived from Jiten (https://jiten.moe) and directly from\n"
    "KANJIDIC2, using JMdict/KANJIDIC data from the Electronic Dictionary\n"
    "Research and Development Group (EDRDG).\n"
    "Data is redistributed under CC BY-SA 4.0; see\n"
    "https://creativecommons.org/licenses/by-sa/4.0/ and\n"
    "https://www.edrdg.org/edrdg/licence.html.\n"
)

# KanjiVG (stroke-order SVGs + phonetic-component markers) is Copyright (C)
# Ulrich Apel, distributed under CC BY-SA 3.0. Bundled SVGs are adapted (stroke
# geometry and numbers, re-styled as static high-contrast diagrams); share-alike is met by
# redistributing them under the same/compatible CC BY-SA licence.
LICENSE_KANJIVG_TEXT = (
    "Stroke-order diagrams and phonetic-component (kvg:phon) relationships are\n"
    "derived from KanjiVG, Copyright (C) 2009-2011 Ulrich Apel.\n"
    "KanjiVG is distributed under the Creative Commons Attribution-Share Alike\n"
    "3.0 licence; see https://creativecommons.org/licenses/by-sa/3.0/ and\n"
    "https://kanjivg.tagaini.net/. The bundled SVGs are adaptations (stroke\n"
    "geometry and numbers extracted and re-styled as static high-contrast diagrams);\n"
    "they are redistributed under the same CC BY-SA licence (share-alike).\n"
)

# Compact, accessible stylesheet scoped to this dictionary's structured content.
# Loaded by Yomitan as styles.css at the ZIP root. Structured-content `data`
# keys become `data-sc-*` attributes, so every rule below targets our own
# `data-sc-bee-role` markers -- it never restyles unrelated dictionaries.
STYLES_CSS = """\
/* Bee's Ultimate Kanji Dictionary -- compact accessible structured-content CSS.
   All rules are scoped to our own data-sc-bee-role markers. Colour is never the
   sole information channel; every graphic has a visible text equivalent. */

/* Restrained reusable token palette: colours and spacing are defined once here
   and consumed via var() below, so nothing hardcodes a stray magic value. The
   accent uses the colour-blind-safe Okabe-Ito blue; every token degrades
   sensibly when a token is missing. currentColor / the viewer's own
   --background-color and --text-color are preferred so the card inherits
   Yomitan's active (light or dark) theme rather than fighting it. */
:root {
  --bee-accent: #0072b2;
  --bee-chip-bg: color-mix(in srgb, currentColor 10%, transparent);
  --bee-chip-border: color-mix(in srgb, currentColor 22%, transparent);
  --bee-badge-bg: color-mix(in srgb, currentColor 8%, transparent);
  --bee-muted: color-mix(in srgb, currentColor 65%, transparent);
  --bee-gap: 0.35em;
  --bee-radius: 0.4em;
}

[data-sc-bee-role="detail"] { line-height: 1.5; }

/* Hero header: the glyph is the large unambiguous anchor; the keyword names the
   character beside it. Real text, so it survives with images/CSS off. */
[data-sc-bee-role="hero"] {
  display: flex;
  align-items: baseline;
  gap: 0.5em;
  flex-wrap: wrap;
  margin: 0 0 0.4em;
}
[data-sc-bee-role="hero-glyph"] {
  font-size: 2.4em;
  line-height: 1;
  font-weight: 600;
}
[data-sc-bee-role="hero-keyword"] {
  font-size: 1.05em;
  font-weight: 600;
  color: var(--bee-accent, currentColor);
}

/* Readings: On / Kun as separated, labelled chip groups. The class label is
   real text so the On/Kun distinction never depends on colour. Chips wrap in a
   narrow popup instead of overflowing. */
[data-sc-bee-role="readings"] { margin: 0.3em 0; }
[data-sc-bee-role="reading-group"] {
  display: flex;
  align-items: baseline;
  gap: var(--bee-gap, 0.35em);
  flex-wrap: wrap;
  margin: 0.15em 0;
}
[data-sc-bee-role="reading-label"] {
  font-size: 0.8em;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--bee-muted, currentColor);
  min-width: 2.2em;
}
[data-sc-bee-role="reading-chips"] {
  display: inline-flex;
  flex-wrap: wrap;
  gap: var(--bee-gap, 0.35em);
}
[data-sc-bee-role="reading-chip"] {
  display: inline-block;
  padding: 0.05em 0.5em;
  border: 1px solid var(--bee-chip-border, currentColor);
  border-radius: 999px;
  background: var(--bee-chip-bg, transparent);
  font-size: 0.95em;
  white-space: nowrap;
}

/* Compact above-the-fold reading chips: a single wrapping row of the top
   readings by Jiten vocabulary totals (no On/Kun split above the fold). */
[data-sc-bee-role="reading-chips"] {
  display: flex;
  flex-wrap: wrap;
  gap: var(--bee-gap, 0.35em);
  margin: 0.3em 0;
}
[data-sc-bee-role="reading-chip"] {
  display: inline-block;
  padding: 0.05em 0.5em;
  border: 1px solid var(--bee-chip-border, currentColor);
  border-radius: 999px;
  background: var(--bee-chip-bg, transparent);
  font-size: 0.95em;
  white-space: nowrap;
}

/* Meaning: a distinct, compact hierarchy line -- not merged with readings. */
[data-sc-bee-role="meaning"] { margin: 0.3em 0; }

/* Badges: a small aligned, wrapping row of metadata pills (inside disclosures). */
[data-sc-bee-role="badge-row"] {
  display: flex;
  flex-wrap: wrap;
  gap: var(--bee-gap, 0.35em);
  margin: 0.3em 0;
}
[data-sc-bee-role="badge"] {
  display: inline-block;
  padding: 0.05em 0.45em;
  border-radius: var(--bee-radius, 0.4em);
  background: var(--bee-badge-bg, transparent);
  font-size: 0.8em;
  font-weight: 600;
  white-space: nowrap;
  color: var(--bee-muted, currentColor);
}

/* Six globally-highest-frequency words in a responsive two-column grid
   (3 left / 3 right on ordinary popups). Each cell carries ruby + a quiet
   gloss. On a narrow popup the grid collapses to a single column (below). */
[data-sc-bee-role="vocab-grid"] {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.1em 1em;
  margin: 0.4em 0;
}
[data-sc-bee-role="vocab-grid"] > [data-sc-bee-role="vocab-item"] {
  margin: 0.1em 0;
  min-width: 0;
  overflow-wrap: anywhere;
}

/* Additional vocabulary (inside the "More vocabulary" disclosure). */
[data-sc-bee-role="vocab-group"] { margin: 0.4em 0; }
[data-sc-bee-role="vocab-label"] {
  font-size: 0.8em;
  font-weight: 700;
  color: var(--bee-muted, currentColor);
  margin: 0.2em 0 0.1em;
}
[data-sc-bee-role="vocab-list"] { margin: 0; padding-left: 1.1em; }
[data-sc-bee-role="vocab-item"] { margin: 0.1em 0; }
[data-sc-bee-role="vocab-word"] ruby rt { font-size: 0.7em; }
[data-sc-bee-role="vocab-gloss"] { color: var(--bee-muted, currentColor); }

/* Dark theme: derive slightly stronger separators so chips/badges stay legible
   on a dark background, and never paint a solid white card. The card inherits
   the viewer's background/text; only our token separators strengthen. */
@media (prefers-color-scheme: dark) {
  [data-sc-bee-role="detail"] {
    --bee-accent: #56b4e9;
    --bee-chip-border: color-mix(in srgb, currentColor 38%, transparent);
    --bee-chip-bg: color-mix(in srgb, currentColor 16%, transparent);
    --bee-badge-bg: color-mix(in srgb, currentColor 14%, transparent);
  }
}

/* Narrow / mobile-sized popups: stack the hero, chips, and chart, and collapse
   the six-word vocabulary grid to a single column instead of overflowing a
   compact pane. */
@media (max-width: 24em) {
  [data-sc-bee-role="hero-glyph"] { font-size: 2em; }
  [data-sc-bee-role="reading-group"] { align-items: flex-start; }
  [data-sc-bee-role="vocab-grid"] { grid-template-columns: 1fr; }
  [data-sc-bee-role="reading-chart"] { display: block; margin: 0 auto 0.4em; }
  [data-sc-bee-role="donut-legend"] { display: block; }
}

/* Progressive disclosure: restrained, keyboard-focusable summaries. */
[data-sc-bee-role="section"] > summary {
  cursor: pointer;
  font-weight: 600;
  opacity: 0.85;
}
[data-sc-bee-role="section"] > summary:focus-visible {
  outline: 2px solid currentColor;
  outline-offset: 2px;
}

/* Reading-distribution chart: a packaged raster PNG (donut) plus a visible text
   legend carrying the same data. The image is bounded and scoped; the caption
   and legend degrade gracefully if the image cannot load. */
[data-sc-bee-role="reading-donut"] { margin: 0.3em 0; }
[data-sc-bee-role="donut-caption"] { font-size: 0.9em; font-weight: 600; margin: 0 0 0.25em; }
[data-sc-bee-role="donut-graphic"] { display: inline-block; vertical-align: middle; }
[data-sc-bee-role="reading-chart"] {
  display: inline-block;
  width: 4.6em;
  height: 4.6em;
  max-width: 40%;
  vertical-align: middle;
  margin-right: 0.6em;
}
[data-sc-bee-role="donut-legend"] {
  display: inline-block;
  list-style: none;
  margin: 0;
  padding: 0;
  vertical-align: middle;
  font-size: 0.9em;
}
[data-sc-bee-role="donut-swatch"] {
  display: inline-block;
  width: 0.8em;
  height: 0.8em;
  border-radius: 0.15em;
  margin-right: 0.4em;
  overflow: hidden;
  vertical-align: middle;
}

/* Phonetic family line: quiet, wraps gracefully. */
[data-sc-bee-role="phonetic-family"] { font-size: 0.95em; opacity: 0.9; }
[data-sc-bee-role="phon-source"] { opacity: 0.65; font-size: 0.85em; }

/* Stroke-order diagram: bounded, centred, with a text fallback beneath it. */
[data-sc-bee-role="stroke-image"] {
  max-width: 6em;
  max-height: 6em;
}
[data-sc-bee-role="stroke-text"] { font-size: 0.9em; opacity: 0.9; }

/* Honour reduced-motion for any bundled animation the viewer might run. */
@media (prefers-reduced-motion: reduce) {
  [data-sc-bee-role="stroke-image"] { animation: none !important; }
}
"""

# Only the structured single-character TERM banks ship. The native
# kanji_bank/kanji_meta_bank are deliberately NOT packaged: Yomitan's
# kanji-click / "view kanji" flow routes exclusively to the native kanji
# renderer (a fixed Meaning/Readings table dictionary CSS cannot restyle), so a
# shipped kanji_bank silently supersedes the rich structured card on the user's
# ordinary lookup path. Dropping it makes the structured term entry the single
# canonical visible surface for every character (verified against real Yomitan
# 26.7.29.0 in parent task t_08284e0e). build_banks still produces the kanji
# banks in-memory for unit coverage, but this table is the shipped/hashed set,
# so nothing reintroduces a competing flat entry.
BANK_FILES = [
    ("term_bank_1.json", "term_bank"),
    ("term_meta_bank_1.json", "term_meta_bank"),
]


def dump_json(obj):
    """Serialize to canonical, deterministic UTF-8 JSON (no ASCII escaping)."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_index(revision):
    """Build the Yomitan index.json object with the frozen stable fields."""
    return {
        "title": TITLE,
        "format": 3,
        "revision": revision,
        "sequenced": False,
        "author": "bee-san",
        "isUpdatable": True,
        "indexUrl": f"https://raw.githubusercontent.com/{REPO}/main/dist/index.json",
        "downloadUrl": f"https://github.com/{REPO}/releases/latest/download/{ZIP_NAME}",
        "url": f"https://github.com/{REPO}",
        "description": "Kanji dictionary from Jiten data with KANJIDIC2 fallback coverage.",
        "attribution": ATTRIBUTION,
        "sourceLanguage": "ja",
        "targetLanguage": "en",
        "frequencyMode": "rank-based",
    }


def build_manifest(revision, content_hash, date, source_counts,
                   enrichment_counts, sitemap_size, code_revision):
    """Build the machine-readable source/revision manifest (MANIFEST.json).

    Describes exactly how this canonical release was produced so an importer or
    auditor can trace every entry to its source: the dictionary revision, the
    revision-independent content hash, the UTC acquisition date, per-source
    record counts (Jiten stays authoritative; KANJIDIC2 is a licensed fallback
    for characters Jiten does not serve), the KanjiVG-derived enrichment counts,
    the code revision that generated it, and the licences/attribution. It is
    bundled in the ZIP (offline provenance) and published as a release asset.

    Every field is derived deterministically from the build inputs, so bundling
    it inside the ZIP does not disturb reproducible rebuilds.
    """
    sc = source_counts or {}
    ec = enrichment_counts or {}
    return {
        "title": TITLE,
        "revision": revision,
        "contentHash": content_hash,
        "buildDate": date,
        "downloadUrl": f"https://github.com/{REPO}/releases/latest/download/{ZIP_NAME}",
        "indexUrl": f"https://raw.githubusercontent.com/{REPO}/main/dist/index.json",
        "url": f"https://github.com/{REPO}",
        "codeRevision": code_revision,
        "sources": {
            "jiten": {
                "url": "https://jiten.moe",
                "records": sc.get("jiten", 0),
                "sitemapCharacters": sitemap_size,
                "acquisition": "once per UTC day, unauthenticated",
                "role": "authoritative (enriched entries)",
            },
            "kanjidic2": {
                "url": "https://www.edrdg.org/wiki/index.php/KANJIDIC_Project",
                "records": sc.get("kanjidic2", 0),
                "role": "licensed fallback for characters Jiten does not serve",
            },
            "kanjivg": {
                "url": "https://kanjivg.tagaini.net/",
                "revision": KANJIVG_REVISION,
                "assets": ec.get("assets", 0),
                "role": "stroke-order diagrams + phonetic-component relationships",
            },
        },
        "records": {
            "total": sc.get("jiten", 0) + sc.get("kanjidic2", 0),
            "jiten": sc.get("jiten", 0),
            "kanjidic2Fallback": sc.get("kanjidic2", 0),
        },
        "enrichment": {
            "strokeSets": ec.get("strokes", 0),
            "phoneticFamilies": ec.get("families", 0),
            "bundledSvgAssets": ec.get("assets", 0),
        },
        "attribution": ATTRIBUTION,
        "licences": [
            "Dictionary data (JMdict/KANJIDIC via Jiten, EDRDG): CC BY-SA 4.0",
            "KanjiVG stroke/phonetic data: CC BY-SA 3.0",
            "Generator code: MIT",
        ],
    }


def content_hash(banks, assets=None, source_counts=None,
                 enrichment_counts=None, sitemap_size=0):
    """SHA-256 over all revision-independent package content.

    The revision and generated manifest fields are excluded, but banks, assets,
    updater metadata, styles, and bundled licence notices are covered. Any
    user-visible package change therefore requires a fresh release revision.
    """
    material = {
        "banks": {name: banks[name] for _, name in BANK_FILES},
        "package": {
            "index": build_index(""),
            "manifest": build_manifest(
                revision="",
                content_hash="",
                date="",
                source_counts=source_counts or {},
                enrichment_counts=enrichment_counts or {},
                sitemap_size=sitemap_size,
                code_revision="",
            ),
            "styles.css": STYLES_CSS,
            "LICENSE-data.txt": LICENSE_DATA_TEXT,
            "LICENSE-kanjivg.txt": LICENSE_KANJIVG_TEXT if (
                assets and any(str(p).lower().endswith(".svg") for p in assets)
            ) else None,
        },
    }
    if assets:
        material["assets"] = {
            k: (
                "sha256:" + hashlib.sha256(assets[k]).hexdigest()
                if isinstance(assets[k], (bytes, bytearray))
                else assets[k]
            )
            for k in sorted(assets)
        }
    payload = dump_json(material)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Fixed ZIP member metadata for reproducible archives.
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def _zip_member(name, data):
    info = zipfile.ZipInfo(filename=name, date_time=_ZIP_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o644 & 0xFFFF) << 16  # -rw-r--r--
    info.create_system = 3  # unix
    if isinstance(data, str):
        data = data.encode("utf-8")
    return info, data


def build_zip(banks, revision, assets=None, manifest=None):
    """Build a deterministic Yomitan ZIP (bytes) with all members at the root.

    `assets` is an optional {path: text} map of extra bundled files (e.g.
    sanitized KanjiVG SVGs under kanjivg/). `manifest` is an optional
    source/revision manifest dict (see build_manifest) bundled as MANIFEST.json
    so provenance travels inside the package. styles.css is always bundled; the
    KanjiVG licence notice is bundled only when KanjiVG-derived assets ship, to
    honour the share-alike obligation for exactly what is redistributed. Member
    order, timestamps, and permissions are fixed so two builds from identical
    inputs are byte-identical regardless of dict insertion order.
    """
    assets = assets or {}
    members = [("index.json", dump_json(build_index(revision)))]
    if manifest is not None:
        members.append(("MANIFEST.json", dump_json(manifest)))
    for filename, key in BANK_FILES:
        members.append((filename, dump_json(banks[key])))
    members.append(("styles.css", STYLES_CSS))
    members.append(("LICENSE-data.txt", LICENSE_DATA_TEXT))
    if any(str(path).lower().endswith(".svg") for path in assets):
        members.append(("LICENSE-kanjivg.txt", LICENSE_KANJIVG_TEXT))
    # Sort asset paths for deterministic ordering irrespective of insertion.
    for path in sorted(assets):
        members.append((path, assets[path]))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, payload in members:
            info, data = _zip_member(name, payload)
            zf.writestr(info, data)
    return buf.getvalue()


def bind_release_artifacts_to_code_revision(out_dir, code_revision, revision=None):
    """Rebind built release artifacts to the commit that will own their tag.

    The updater files are committed after the initial content build, so the
    final release commit does not exist when that build starts. Rewriting only
    the revision-independent provenance field after the updater commit avoids
    publishing a manifest that points at its parent. ZIP metadata and member
    order stay deterministic, the standalone manifest stays byte-identical to
    the bundled copy, and SHA256SUMS is regenerated for the changed bytes.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", code_revision):
        raise ValueError("code_revision must be a full 40-character git object ID")

    out_dir = pathlib.Path(out_dir)
    zip_path = out_dir / ZIP_NAME
    manifest_path = out_dir / "MANIFEST.json"
    checksum_path = out_dir / "SHA256SUMS"

    with zipfile.ZipFile(zip_path) as source:
        infos = source.infolist()
        names = [info.filename for info in infos]
        if names.count("MANIFEST.json") != 1 or names.count("index.json") != 1:
            raise ValueError("release ZIP must contain exactly one manifest and index")
        members = [(info.filename, source.read(info)) for info in infos]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["codeRevision"] = code_revision
    if revision is not None:
        if not re.fullmatch(r"[0-9]{4}\.[0-9]{2}\.[0-9]{2}(?:\.[0-9]+)?", revision):
            raise ValueError("revision must be a versioned YYYY.MM.DD release revision")
        manifest["revision"] = revision
    manifest_bytes = dump_json(manifest).encode("utf-8")
    replaced = False

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as target:
        for name, data in members:
            if name == "MANIFEST.json":
                data = manifest_bytes
                replaced = True
            elif name == "index.json" and revision is not None:
                index = json.loads(data)
                index["revision"] = revision
                data = dump_json(index).encode("utf-8")
            info, data = _zip_member(name, data)
            target.writestr(info, data)
    if not replaced:
        raise ValueError("release ZIP does not contain MANIFEST.json")

    zip_bytes = buf.getvalue()
    zip_path.write_bytes(zip_bytes)
    manifest_path.write_bytes(manifest_bytes)
    checksum_path.write_text(
        f"{hashlib.sha256(zip_bytes).hexdigest()}  {ZIP_NAME}\n"
        f"{hashlib.sha256(manifest_bytes).hexdigest()}  MANIFEST.json\n",
        encoding="utf-8",
    )


# --- Daily acquisition with a resumable per-character cache ------------------

API_BASE = "https://api.jiten.moe/api/kanji"
SITEMAP_URL = f"{API_BASE}/sitemap-characters"
USER_AGENT = "bees-ultimate-kanji-dictionary (+https://github.com/bee-san/bees-ultimate-kanji-dictionary)"
TIMEOUT_SECONDS = 30
MAX_RETRIES = 2  # bounded retries for transport / 429 / 5xx only


class NotFound(Exception):
    """The Jiten API returned 404 for a character (skip it)."""


def cache_filename(character):
    """Filesystem-safe cache filename for a character (by code points)."""
    codepoints = "_".join(f"{ord(c):x}" for c in character)
    return f"{codepoints}.json"


def http_get_json(url):
    """GET a URL and parse JSON, with bounded retries for transient errors.

    Raises NotFound on 404. Retries only transport errors, 429, and 5xx.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_err = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(url) from e
            if e.code == 429 or 500 <= e.code < 600:
                last_err = e
            else:
                raise
        except urllib.error.URLError as e:
            last_err = e
        if attempt < MAX_RETRIES:
            time.sleep(2 ** attempt)  # small backoff: 1s, 2s
    raise last_err if last_err is not None else RuntimeError(f"failed to GET {url}")


def fetch_kanji(character):
    """Fetch a single kanji payload from the live Jiten API."""
    return http_get_json(f"{API_BASE}/{urllib.parse.quote(character)}")


def fetch_sitemap():
    """Fetch the list of characters from the Jiten sitemap endpoint."""
    data = http_get_json(SITEMAP_URL)
    if not isinstance(data, list):
        raise MalformedPayload("sitemap is not a list")
    return [c for c in data if isinstance(c, str) and c]


def fetch_sitemap_cached(cache_dir, date, fetcher=fetch_sitemap, offline=False):
    """Return the daily sitemap, reusing an atomic dated cache on reruns."""
    import pathlib as _pl

    day_dir = _pl.Path(cache_dir) / date
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / "sitemap.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list) and all(isinstance(c, str) and c for c in data):
                return data
        except (ValueError, OSError):
            pass
    if offline:
        raise FileNotFoundError(f"Jiten sitemap cache missing: {path}")
    data = fetcher()
    if not isinstance(data, list) or not all(isinstance(c, str) and c for c in data):
        raise MalformedPayload("sitemap is not a list of characters")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return data


def fetch_all(characters, cache_dir, date, fetcher=fetch_kanji):
    """Fetch payloads for characters, using a resumable dated on-disk cache.

    Cache layout: cache_dir/DATE/<codepoints>.json. Files already present for
    DATE are reused (no fetcher call). Only missing characters are fetched
    sequentially. 404s are negatively cached for that day and not returned. Returns an
    ordered dict {character: payload} for the characters that produced data.
    """
    import pathlib as _pl

    day_dir = _pl.Path(cache_dir) / date
    day_dir.mkdir(parents=True, exist_ok=True)

    out = {}
    for char in characters:
        path = day_dir / cache_filename(char)
        missing = path.with_suffix(".missing")
        if missing.exists():
            continue
        if path.exists():
            try:
                out[char] = json.loads(path.read_text(encoding="utf-8"))
                continue
            except (ValueError, OSError):
                pass  # corrupt cache file -> refetch
        try:
            payload = fetcher(char)
        except NotFound:
            missing.touch()
            continue  # skip 404s
        # atomic-ish write: temp then replace
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        out[char] = payload
    return out


# --- KanjiVG asset acquisition (same resumable dated-cache pattern) ----------

KANJIVG_REVISION = "61e39cfc29724132a6f8823b166296932985a0ff"
KANJIVG_BASE = f"https://raw.githubusercontent.com/KanjiVG/kanjivg/{KANJIVG_REVISION}/kanji"


def kanjivg_cache_filename(character):
    """Filesystem-safe KanjiVG cache filename (zero-padded codepoint .svg)."""
    return f"{ord(character):05x}.svg"


def http_get_text(url):
    """GET a URL and return decoded text, with the same bounded retries."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_err = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(url) from e
            if e.code == 429 or 500 <= e.code < 600:
                last_err = e
            else:
                raise
        except urllib.error.URLError as e:
            last_err = e
        if attempt < MAX_RETRIES:
            time.sleep(2 ** attempt)
    raise last_err if last_err is not None else RuntimeError(f"failed to GET {url}")


def fetch_kanjivg(character):
    """Fetch a single KanjiVG SVG for a character from the live source."""
    return http_get_text(f"{KANJIVG_BASE}/{kanjivg_cache_filename(character)}")


def fetch_kanjivg_all(characters, cache_dir, date, fetcher=fetch_kanjivg):
    """Fetch KanjiVG SVGs, reusing a resumable dated on-disk cache.

    Cache layout mirrors the Jiten fetch: cache_dir/DATE/<codepoint>.svg. Files
    present for DATE are reused (no fetcher call); only missing characters are
    fetched sequentially; 404s are negatively cached for that day. Returns
    {character: svg_text}. No
    new machinery -- the same once-per-day, resumable, cache-first flow.
    """
    import pathlib as _pl

    day_dir = _pl.Path(cache_dir) / date
    day_dir.mkdir(parents=True, exist_ok=True)

    out = {}
    for char in characters:
        path = day_dir / kanjivg_cache_filename(char)
        missing = path.with_suffix(".missing")
        if missing.exists():
            continue
        if path.exists():
            try:
                out[char] = path.read_text(encoding="utf-8")
                continue
            except OSError:
                pass
        try:
            svg = fetcher(char)
        except NotFound:
            missing.touch()
            continue
        tmp = path.with_suffix(".svg.tmp")
        tmp.write_text(svg, encoding="utf-8")
        tmp.replace(path)
        out[char] = svg
    return out


def assemble_enrichment(svgs, ranks):
    """Assemble deterministic stroke info, phonetic families, and SVG assets.

    svgs:  {character: kanjivg_svg_text}. ranks: {character: frequency_rank}.
    Returns {
        "strokes": {char: parse_kanjivg(...)},
        "families": {component: family},
        "families_by_char": {char: family},   # convenience lookup
        "assets": {asset_path: sanitized_svg}, # only referenced assets
    }.
    """
    strokes = {}
    phon_map = {}
    assets = {}
    for char in sorted(svgs, key=ord):
        svg = svgs[char]
        info = parse_kanjivg(svg, char)
        if info is None:
            continue
        strokes[char] = info
        assets[info["asset"]] = sanitize_kanjivg_svg(svg, char)
        comp = extract_phonetic_component(svg, char)
        if comp:
            phon_map[char] = comp

    families = build_phonetic_families(phon_map, ranks)
    families_by_char = {}
    for fam in families.values():
        for member in fam["members"]:
            families_by_char[member] = fam

    return {
        "strokes": strokes,
        "families": families,
        "families_by_char": families_by_char,
        "assets": assets,
    }


# --- KANJIDIC2 static-source acquisition (once-per-day, resumable cache) ------

KANJIDIC2_URL = "https://www.edrdg.org/kanjidic/kanjidic2.xml.gz"


def fetch_kanjidic2():
    """Fetch and gunzip the static KANJIDIC2 XML from EDRDG (single request)."""
    import gzip

    req = urllib.request.Request(KANJIDIC2_URL, headers={"User-Agent": USER_AGENT})
    last_err = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                return gzip.decompress(resp.read()).decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 429 or 500 <= e.code < 600:
                last_err = e
            else:
                raise
        except urllib.error.URLError as e:
            last_err = e
        if attempt < MAX_RETRIES:
            time.sleep(2 ** attempt)
    raise last_err if last_err is not None else RuntimeError("failed to GET KANJIDIC2")


def fetch_kanjidic2_source(cache_dir, date, fetcher=fetch_kanjidic2):
    """Return the KANJIDIC2 XML for DATE, fetching the static source once a day.

    Mirrors the Jiten/KanjiVG resumable dated-cache flow: the decompressed XML
    is stored at cache_dir/DATE/kanjidic2.xml and reused on same-day reruns, so
    the daily workflow downloads the static source at most once per UTC day.
    """
    import pathlib as _pl

    day_dir = _pl.Path(cache_dir) / date
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / "kanjidic2.xml"
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            pass
    xml_text = fetcher()
    tmp = path.with_suffix(".xml.tmp")
    tmp.write_text(xml_text, encoding="utf-8")
    tmp.replace(path)
    return xml_text


# --- Build pipeline and revision decision ------------------------------------

def run_build(characters, cache_dir, date, aliases=None, fetcher=fetch_kanji,
              kanjivg_cache_dir=None, kanjivg_fetcher=fetch_kanjivg,
              kanjidic2_cache_dir=None, kanjidic2_fetcher=fetch_kanjidic2):
    """Fetch (via cache) -> normalize -> merge -> enrich -> build banks + ZIP.

    Acquires Jiten payloads AND (when kanjivg_cache_dir is given) KanjiVG SVGs,
    both through the same resumable dated cache. When kanjidic2_cache_dir is
    given, the static KANJIDIC2 XML is fetched once for the day and every
    character it covers that Jiten does NOT serve is added as a simple honest
    fallback record (Jiten stays authoritative on every duplicate). Assembles
    the visual enrichment (stroke info, phonetic families, sanitized SVG assets)
    over the merged record set and threads it into the term entries and the
    canonical ZIP. content_hash is revision-independent and covers the
    enrichment (assets), so a change in stroke/phonetic/fallback data triggers a
    new revision. Returns {records, banks, enrichment, content_hash,
    zip_bytes(placeholder revision), source_counts}.
    """
    payloads = fetch_all(characters, cache_dir, date, fetcher)
    records = []
    for char in characters:
        payload = payloads.get(char)
        if payload is None:
            continue
        if not isinstance(payload, dict) or payload.get("character") != char:
            actual = payload.get("character") if isinstance(payload, dict) else None
            raise MalformedPayload(f"requested {char} but received payload for {actual!r}")
        try:
            record = normalize_record(payload)
        except MalformedPayload:
            continue  # skip characters whose payload cannot be trusted
        if record["frequency_rank"] is not None and record["frequency_rank"] <= 1000:
            example_count = sum(len(group["words"]) for group in record["examples"])
            if not record["keyword"] or not (record["on"] or record["kun"]) or example_count < 1:
                raise MalformedPayload(f"Top-1000 quality floor failed for {char}")
        records.append(record)

    jiten_count = len(records)
    if kanjidic2_cache_dir:
        xml_text = fetch_kanjidic2_source(kanjidic2_cache_dir, date, kanjidic2_fetcher)
        kd2_index = parse_kanjidic2(xml_text)
        records = merge_kanjidic2(records, kd2_index)
    kanjidic2_count = len(records) - jiten_count

    ranks = {r["character"]: r["frequency_rank"] for r in records}
    enrichment = {"strokes": {}, "families": {}, "families_by_char": {}, "assets": {}}
    if kanjivg_cache_dir:
        present = [r["character"] for r in records]
        svgs = fetch_kanjivg_all(present, kanjivg_cache_dir, date, kanjivg_fetcher)
        enrichment = assemble_enrichment(svgs, ranks)

    # Generate the per-entry reading-distribution PNG media for every record
    # that carries a truthful Jiten reading total, and bundle each as a packaged
    # asset the term card references by path. Records without a distribution
    # (KANJIDIC2-only fallbacks) get no chart and no asset -- never faked.
    for r in records:
        png = build_reading_distribution_png(r)
        if png is not None:
            enrichment["assets"][reading_distribution_asset_name(r["character"])] = png

    banks = build_banks(records, aliases or {}, enrichment=enrichment)
    source_counts = {"jiten": jiten_count, "kanjidic2": kanjidic2_count}
    enrichment_counts = {
        "strokes": len(enrichment["strokes"]),
        "families": len(enrichment["families"]),
        "assets": len(enrichment["assets"]),
    }
    chash = content_hash(
        banks,
        enrichment.get("assets"),
        source_counts=source_counts,
        enrichment_counts=enrichment_counts,
        sitemap_size=len(characters),
    )
    return {
        "records": records,
        "banks": banks,
        "enrichment": enrichment,
        "content_hash": chash,
        "source_counts": source_counts,
        "zip_bytes": build_zip(banks, date_to_revision(date), enrichment.get("assets")),
    }


def date_to_revision(date):
    """Convert a UTC date 'YYYY-MM-DD' to a dot-numeric revision 'YYYY.MM.DD'."""
    return date.replace("-", ".")


def decide_revision(content_hash, previous_hash, date, previous_revision):
    """Decide the revision string, or None when content is unchanged.

    - unchanged content -> None (publish nothing)
    - changed, new day   -> that day's 'YYYY.MM.DD'
    - changed, same base day as previous revision -> append a monotonic
      dot-numeric suffix so the revision strictly increases
    """
    if previous_hash is not None and content_hash == previous_hash:
        return None
    base = date_to_revision(date)
    if previous_revision is None:
        return base
    if previous_revision == base or previous_revision.startswith(base + "."):
        # Same UTC day already released: bump the trailing counter.
        parts = previous_revision.split(".")
        if len(parts) == 4:
            return f"{base}.{int(parts[3]) + 1}"
        return f"{base}.1"
    return base


def _load_previous(dist_index_path):
    """Return (previous_revision, previous_content_hash) from dist/index.json.

    The content hash is stored alongside the index as dist/content.sha256 so we
    can detect changes without re-downloading the released ZIP.
    """
    import pathlib as _pl

    idx = _pl.Path(dist_index_path)
    prev_rev = None
    if idx.exists():
        try:
            prev_rev = json.loads(idx.read_text(encoding="utf-8")).get("revision")
        except (ValueError, OSError):
            prev_rev = None
    prev_hash = None
    hpath = idx.with_name("content.sha256")
    if hpath.exists():
        try:
            prev_hash = hpath.read_text(encoding="utf-8").strip() or None
        except OSError:
            prev_hash = None
    return prev_rev, prev_hash


def _code_revision():
    """Return the full git revision of the generator, or 'unknown'.

    Deterministic within a single checkout; recorded in the manifest so a
    published release is traceable back to the exact code that produced it.
    """
    import pathlib as _pl
    import subprocess as _sp

    root = _pl.Path(__file__).resolve().parent.parent
    try:
        out = _sp.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            stderr=_sp.DEVNULL,
        )
        return out.decode("utf-8").strip() or "unknown"
    except (OSError, _sp.CalledProcessError):
        return "unknown"


def main(argv=None):
    """Single command: fetch/refresh -> normalize -> validate -> build.

    Writes the ZIP + SHA256SUMS to the output dir and refreshes dist/index.json
    (plus dist/content.sha256) only when normalized content changed.
    """
    import argparse
    import datetime
    import pathlib as _pl
    import subprocess

    parser = argparse.ArgumentParser(description="Build Bee's Ultimate Kanji Dictionary")
    parser.add_argument("--cache", default="cache")
    parser.add_argument("--kanjivg-cache", default="kanjivg-cache",
                        help="dated cache dir for KanjiVG stroke SVGs")
    parser.add_argument("--out", default="build")
    parser.add_argument("--dist", default="dist")
    parser.add_argument("--date", default=None, help="UTC date YYYY-MM-DD (default: today)")
    parser.add_argument("--limit", type=int, default=None, help="limit characters (debug)")
    parser.add_argument("--offline", action="store_true", help="use cache only; no network")
    parser.add_argument("--no-kanjivg", action="store_true",
                        help="skip KanjiVG acquisition/enrichment (data-only build)")
    parser.add_argument("--kanjidic2-cache", default="kanjidic2-cache",
                        help="dated cache dir for the static KANJIDIC2 XML source")
    parser.add_argument("--no-kanjidic2", action="store_true",
                        help="skip KANJIDIC2 fallback (Jiten-only build)")
    args = parser.parse_args(argv)

    date = args.date or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    out_dir = _pl.Path(args.out)
    dist_dir = _pl.Path(args.dist)
    out_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)

    print(f"[build] date={date}")
    characters = fetch_sitemap_cached(args.cache, date, offline=args.offline)
    if args.limit:
        characters = characters[: args.limit]
    print(f"[build] {len(characters)} characters from sitemap")

    fetcher = _offline_fetcher(args.cache, date) if args.offline else fetch_kanji
    kanjivg_cache = None if args.no_kanjivg else args.kanjivg_cache
    kvg_fetcher = (
        _offline_kanjivg_fetcher(args.kanjivg_cache, date) if args.offline else fetch_kanjivg
    )
    kanjidic2_cache = None if args.no_kanjidic2 else args.kanjidic2_cache
    kd2_fetcher = (
        _offline_kanjidic2_fetcher(args.kanjidic2_cache, date) if args.offline else fetch_kanjidic2
    )
    result = run_build(
        characters, args.cache, date, DEFAULT_ALIASES, fetcher,
        kanjivg_cache_dir=kanjivg_cache, kanjivg_fetcher=kvg_fetcher,
        kanjidic2_cache_dir=kanjidic2_cache, kanjidic2_fetcher=kd2_fetcher,
    )
    enr = result["enrichment"]
    sc = result.get("source_counts", {"jiten": len(result["records"]), "kanjidic2": 0})
    print(
        f"[build] {len(result['records'])} clean records "
        f"(Jiten {sc['jiten']} + KANJIDIC2 fallback {sc['kanjidic2']}); "
        f"{len(enr['strokes'])} stroke sets; {len(enr['families'])} phonetic families; "
        f"hash={result['content_hash'][:12]}"
    )

    prev_rev, prev_hash = _load_previous(dist_dir / "index.json")
    revision = decide_revision(result["content_hash"], prev_hash, date, prev_rev)
    if revision is None:
        print("[build] content unchanged; nothing to publish")
        return 0

    manifest = build_manifest(
        revision=revision,
        content_hash=result["content_hash"],
        date=date,
        source_counts=sc,
        enrichment_counts={
            "strokes": len(enr["strokes"]),
            "families": len(enr["families"]),
            "assets": len(enr.get("assets") or {}),
        },
        sitemap_size=len(characters),
        code_revision=_code_revision(),
    )

    zip_bytes = build_zip(result["banks"], revision, enr.get("assets"), manifest=manifest)
    zip_path = out_dir / ZIP_NAME
    zip_path.write_bytes(zip_bytes)

    # Emit the manifest as a standalone release asset too, byte-identical to the
    # bundled MANIFEST.json member, so its SHA256SUMS line verifies either copy.
    manifest_text = dump_json(manifest)
    (out_dir / "MANIFEST.json").write_text(manifest_text, encoding="utf-8")
    manifest_digest = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()

    digest = hashlib.sha256(zip_bytes).hexdigest()
    (out_dir / "SHA256SUMS").write_text(
        f"{digest}  {ZIP_NAME}\n{manifest_digest}  MANIFEST.json\n",
        encoding="utf-8",
    )

    (dist_dir / "index.json").write_text(
        dump_json(build_index(revision)) + "\n", encoding="utf-8"
    )
    (dist_dir / "content.sha256").write_text(result["content_hash"] + "\n", encoding="utf-8")

    # Validate the built banks against the official schemas via Node.
    script = _pl.Path(__file__).resolve().parent.parent / "scripts" / "validate_yomitan.mjs"
    if script.exists():
        rc = subprocess.call(["node", str(script), str(zip_path)])
        if rc != 0:
            raise SystemExit("Yomitan schema validation failed")

    print(f"[build] revision={revision} zip={zip_path} sha256={digest}")
    return 0


def _offline_fetcher(cache_dir, date):
    """Return a fetcher that only reads the dated cache (raises if missing)."""
    import pathlib as _pl

    day = _pl.Path(cache_dir) / date

    def fetcher(char):
        path = day / cache_filename(char)
        if not path.exists():
            raise NotFound(char)
        return json.loads(path.read_text(encoding="utf-8"))

    return fetcher


def _offline_kanjivg_fetcher(cache_dir, date):
    """Return a KanjiVG fetcher that only reads the dated cache (else NotFound)."""
    import pathlib as _pl

    day = _pl.Path(cache_dir) / date

    def fetcher(char):
        path = day / kanjivg_cache_filename(char)
        if not path.exists():
            raise NotFound(char)
        return path.read_text(encoding="utf-8")

    return fetcher


def _offline_kanjidic2_fetcher(cache_dir, date):
    """Return a KANJIDIC2 source fetcher that only reads the dated cache."""
    import pathlib as _pl

    day = _pl.Path(cache_dir) / date

    def fetcher():
        path = day / "kanjidic2.xml"
        if not path.exists():
            raise FileNotFoundError(f"KANJIDIC2 cache missing: {path}")
        return path.read_text(encoding="utf-8")

    return fetcher


# Compatibility aliases: forms Jiten does not serve, mapped to canonical forms.
DEFAULT_ALIASES = {"髙": "高"}


if __name__ == "__main__":
    raise SystemExit(main())
