"""Bee's Ultimate Kanji Dictionary -- minimal Jiten -> Yomitan generator.

One module owns the whole pipeline: fetch -> normalize -> validate -> build.
Kept small and understandable on purpose. No service layers, no plugins.
"""

import hashlib
import io
import json
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
    out = []
    seen = set()
    for m in meanings or []:
        c = clean_text(m)
        if c is None or c in seen:
            continue
        seen.add(c)
        out.append(c)
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
    on = [r for r in (payload.get("onReadings") or []) if isinstance(r, str) and r.strip()]
    kun = [r for r in (payload.get("kunReadings") or []) if isinstance(r, str) and r.strip()]

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
    on = [r for r in (fields.get("on") or []) if isinstance(r, str) and r.strip()]
    kun = [r for r in (fields.get("kun") or []) if isinstance(r, str) and r.strip()]
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
    """Compute a truthful reading-class distribution over the entry's examples.

    Counts the example words ACTUALLY shown in this entry, grouped by their
    honest reading class (On / Kun / Other) -- never Jiten's totalWords. Returns
    {"total": N, "segments": [{"label","count","percent","color"}, ...]} with at
    most MAX_DONUT_SEGMENTS segments; any tail collapses into one explicit
    "Other" segment. Percentages are integers summing to 100.
    """
    tally = {}
    order = []
    for group in record.get("examples") or []:
        label = group.get("label") or group.get("reading_class") or "Other"
        n = len(group.get("words") or [])
        if n == 0:
            continue
        if label not in tally:
            tally[label] = 0
            order.append(label)
        tally[label] += n

    total = sum(tally.values())
    if total == 0:
        return {"total": 0, "segments": []}

    # Sort labels by count (descending), then by first-seen order for stability.
    ranked = sorted(order, key=lambda lbl: (-tally[lbl], order.index(lbl)))

    # Cap: keep the top (MAX_DONUT_SEGMENTS - 1) then collapse the rest to Other,
    # but only collapse when there is genuinely a tail to fold.
    kept = []
    overflow = 0
    if len(ranked) > MAX_DONUT_SEGMENTS:
        head = ranked[: MAX_DONUT_SEGMENTS - 1]
        tail = ranked[MAX_DONUT_SEGMENTS - 1:]
        kept = [(lbl, tally[lbl]) for lbl in head]
        overflow = sum(tally[lbl] for lbl in tail)
    else:
        kept = [(lbl, tally[lbl]) for lbl in ranked]

    labels = [lbl for lbl, _ in kept]
    counts = [c for _, c in kept]
    has_explicit_other = False
    if overflow:
        # Fold overflow into an existing "Other" segment if present, else add one.
        if "Other" in labels:
            counts[labels.index("Other")] += overflow
        else:
            labels.append("Other")
            counts.append(overflow)
        has_explicit_other = True

    percents = _largest_remainder_percents(counts, total)
    segments = []
    color_i = 0
    for lbl, cnt, pct in zip(labels, counts, percents):
        if lbl == "Other":
            color = _DONUT_OTHER_COLOR
        else:
            color = _DONUT_COLORS[color_i % (len(_DONUT_COLORS) - 1)]
            color_i += 1
        segments.append({"label": lbl, "count": cnt, "percent": pct, "color": color})
    # mark whether a collapse happened (useful for tests / callers)
    return {"total": total, "segments": segments, "collapsed": has_explicit_other}


def _conic_gradient(segments):
    """Build a deterministic CSS conic-gradient value from ordered segments."""
    stops = []
    acc = 0
    for seg in segments:
        start = acc
        acc += seg["percent"]
        stops.append(f"{seg['color']} {start}% {acc}%")
    return "conic-gradient(" + ", ".join(stops) + ")"


def build_donut_node(record):
    """Build an accessible reading-distribution donut structured-content node.

    The donut ring is a CSS conic-gradient (deterministic, no per-entry asset)
    with a hole punched by a centred disc; it carries an aria-label so it is
    described to assistive tech. A visible text legend lists every segment with
    its label, count, and truthful percentage so NOTHING depends on colour, SVG,
    or CSS alone. Returns None when the entry has no example words.
    """
    dist = reading_distribution(record)
    if dist["total"] == 0:
        return None
    segments = dist["segments"]

    aria = "Reading distribution: " + ", ".join(
        f"{s['label']} {s['percent']} percent ({s['count']})" for s in segments
    )

    # Donut ring: a coloured conic-gradient disc with a centred hole. The
    # conic-gradient BACKGROUND is data-driven (per entry) so it must be inline;
    # all sizing/shape lives in the bundled styles.css keyed on data-sc-bee-role.
    # Marked aria-hidden because the legend below carries the same info as text.
    ring = {
        "tag": "div",
        "data": {"beeRole": "donut-ring", "ariaHidden": "true"},
        "style": {"background": _conic_gradient(segments)},
        "content": {
            "tag": "div",
            "data": {"beeRole": "donut-hole"},
            "content": "",
        },
    }

    # Visible text legend: colour swatch + label + count + truthful percent.
    legend_items = []
    for s in segments:
        swatch = {
            "tag": "span",
            "data": {"beeRole": "donut-swatch", "ariaHidden": "true"},
            # colour is data-driven; the glyph keeps the swatch visible with no CSS
            "style": {"background": s["color"], "color": s["color"]},
            "content": "\u25a0",  # filled square, visible even without CSS colour
        }
        legend_items.append({
            "tag": "li",
            "content": [swatch, f"{s['label']}: {s['percent']}% ({s['count']})"],
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
        "title": aria,
        "content": [
            {"tag": "div", "data": {"beeRole": "donut-graphic", "ariaLabel": aria},
             "content": [ring]},
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


# Deterministic, dependency-free stroke-order animation. Each stroke is drawn in
# sequence via stroke-dashoffset; the final frame leaves every stroke fully
# drawn so a browser that ignores SVG animation still shows the complete glyph.
# prefers-reduced-motion shows the finished glyph immediately (no motion).
_STROKE_STYLE_TEMPLATE = (
    "<style>"
    "@keyframes beeDraw{{to{{stroke-dashoffset:0;}}}}"
    "@media (prefers-reduced-motion:no-preference){{"
    ".bee-stroke{{stroke-dasharray:1000;stroke-dashoffset:1000;"
    "animation:beeDraw 0.8s linear forwards;}}"
    "{rules}"
    "}}"
    "@media (prefers-reduced-motion:reduce){{"
    ".bee-stroke{{animation:none;stroke-dashoffset:0;}}}}"
    "</style>"
)


def sanitize_kanjivg_svg(svg_text, character):
    """Rebuild a minimal, safe, animated SVG from a KanjiVG source string.

    Strips the XML declaration, DOCTYPE, comments, kvg namespaced attributes,
    stroke-number labels, and anything script/external (scripts, event
    handlers, xlink, <image>). Rebuilds a clean <svg> containing only the stroke
    <path> geometry plus an internal <style> block driving a deterministic,
    reduced-motion-guarded stroke-order animation. Output is deterministic.
    """
    paths = _KVG_PATH.findall(svg_text)
    # Per-stroke staggered start so strokes animate in order; delays are fixed.
    rules = []
    for i in range(len(paths)):
        delay = round(i * 0.6, 2)
        rules.append(
            f".bee-stroke:nth-of-type({i + 1}){{animation-delay:{delay}s;}}"
        )
    style = _STROKE_STYLE_TEMPLATE.format(rules="".join(rules))

    path_els = "".join(
        f'<path class="bee-stroke" fill="none" stroke="currentColor" '
        f'stroke-width="3" stroke-linecap="round" stroke-linejoin="round" d="{d}"/>'
        for d in paths
    )
    title = f"Stroke order for {character}"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 109 109" '
        'width="109" height="109" role="img" '
        f'aria-label="{title}"><title>{title}</title>'
        f"{style}{path_els}</svg>"
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


def _detail_content(record, enrichment=None):
    """Build the structured-content body for a term entry's detail item.

    `enrichment` (optional) adds visual learning aids: a reading-distribution
    donut computed from the shown examples, plus a keyboard-accessible
    progressive-disclosure section carrying the phonetic family and stroke-order
    diagram. Without it the body is exactly the honest legacy content.
    """
    char = record["character"]
    body = []

    # Readings line: honest On / Kun lists as supplied.
    reading_bits = []
    if record["on"]:
        reading_bits.append("On: " + "、".join(record["on"]))
    if record["kun"]:
        reading_bits.append("Kun: " + "、".join(record["kun"]))
    if reading_bits:
        body.append({"tag": "div", "content": " / ".join(reading_bits)})

    # Senses line: compact common meanings.
    if record["senses"]:
        body.append({"tag": "div", "content": "; ".join(record["senses"])})

    # Facts line: rank / grade / jlpt / strokes (only known values).
    facts = []
    if record["frequency_rank"] is not None:
        facts.append(f"Rank {record['frequency_rank']}")
    if record["grade"] is not None:
        facts.append(f"Grade {record['grade']}")
    jl = _jlpt_label(record["jlpt"])
    if jl:
        facts.append(f"JLPT {jl}")
    if record["stroke_count"] is not None:
        facts.append(f"{record['stroke_count']} strokes")
    if facts:
        body.append({"tag": "div", "content": " · ".join(facts)})

    # Reading-distribution donut: truthful percentages over the shown examples.
    donut = build_donut_node(record)
    if donut is not None:
        body.append(donut)

    # Example words grouped by reading with honest On/Kun/Other labels.
    for group in record["examples"]:
        items = []
        for ex in group["words"]:
            line = _ruby_node(ex["ruby"]) + [" — " + ex["gloss"]]
            items.append({"tag": "li", "content": line})
        if items:
            body.append({"tag": "div", "content": group["label"]})
            body.append({"tag": "ul", "content": items})

    # Learning aids fold into a keyboard-accessible progressive-disclosure
    # section so the core entry stays compact. Only added when enrichment data
    # actually exists for this character (never an empty, misleading section).
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
# geometry only, re-styled for animation); the share-alike obligation is met by
# redistributing them under the same/compatible CC BY-SA licence.
LICENSE_KANJIVG_TEXT = (
    "Stroke-order diagrams and phonetic-component (kvg:phon) relationships are\n"
    "derived from KanjiVG, Copyright (C) 2009-2011 Ulrich Apel.\n"
    "KanjiVG is distributed under the Creative Commons Attribution-Share Alike\n"
    "3.0 licence; see https://creativecommons.org/licenses/by-sa/3.0/ and\n"
    "https://kanjivg.tagaini.net/. The bundled SVGs are adaptations (stroke\n"
    "geometry extracted and re-styled for lightweight stroke-order animation);\n"
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

[data-sc-bee-role="detail"] { line-height: 1.5; }

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

/* Reading-distribution donut: the ring is a conic-gradient disc (inline
   background) with a punched hole; the visible legend carries the same data. */
[data-sc-bee-role="reading-donut"] { margin: 0.3em 0; }
[data-sc-bee-role="donut-graphic"] { display: inline-block; vertical-align: middle; }
[data-sc-bee-role="donut-ring"] {
  display: inline-block;
  width: 3.2em;
  height: 3.2em;
  border-radius: 50%;
  vertical-align: middle;
  margin-right: 0.6em;
}
[data-sc-bee-role="donut-hole"] {
  width: 1.6em;
  height: 1.6em;
  margin: 0.8em;
  border-radius: 50%;
  background: var(--background-color, #ffffff);
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

BANK_FILES = [
    ("term_bank_1.json", "term_bank"),
    ("term_meta_bank_1.json", "term_meta_bank"),
    ("kanji_bank_1.json", "kanji_bank"),
    ("kanji_meta_bank_1.json", "kanji_meta_bank"),
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


def content_hash(banks, assets=None):
    """SHA-256 over the normalized bank content + bundled assets.

    Revision-independent (excludes the revision string and build time) but
    covers KanjiVG-derived assets so a change in stroke/phonetic enrichment
    yields a new hash and therefore a new published revision.
    """
    material = {name: banks[name] for _, name in BANK_FILES}
    if assets:
        material["_assets"] = {k: assets[k] for k in sorted(assets)}
    payload = dump_json(material)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Fixed ZIP member metadata for reproducible archives.
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def _zip_member(name, data):
    info = zipfile.ZipInfo(filename=name, date_time=_ZIP_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o644 & 0xFFFF) << 16  # -rw-r--r--
    info.create_system = 3  # unix
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
    if assets:
        members.append(("LICENSE-kanjivg.txt", LICENSE_KANJIVG_TEXT))
    # Sort asset paths for deterministic ordering irrespective of insertion.
    for path in sorted(assets):
        members.append((path, assets[path]))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in members:
            info, data = _zip_member(name, text.encode("utf-8"))
            zf.writestr(info, data)
    return buf.getvalue()


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
        try:
            records.append(normalize_record(payload))
        except MalformedPayload:
            continue  # skip characters whose payload cannot be trusted

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

    banks = build_banks(records, aliases or {}, enrichment=enrichment)
    chash = content_hash(banks, enrichment.get("assets"))
    return {
        "records": records,
        "banks": banks,
        "enrichment": enrichment,
        "content_hash": chash,
        "source_counts": {"jiten": jiten_count, "kanjidic2": kanjidic2_count},
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
    """Return the short git revision of the generator, or 'unknown'.

    Deterministic within a single checkout; recorded in the manifest so a
    published release is traceable back to the exact code that produced it.
    """
    import pathlib as _pl
    import subprocess as _sp

    root = _pl.Path(__file__).resolve().parent.parent
    try:
        out = _sp.check_output(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
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
