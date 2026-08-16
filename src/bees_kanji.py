"""Bee's Ultimate Kanji Dictionary -- minimal Jiten -> Yomitan generator.

One module owns the whole pipeline: fetch -> normalize -> validate -> build.
Kept small and understandable on purpose. No service layers, no plugins.
"""

import hashlib
import io
import json
import re
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


def _detail_content(record):
    """Build the structured-content body for a term entry's detail item."""
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

    # Example words grouped by reading with honest On/Kun/Other labels.
    for group in record["examples"]:
        items = []
        for ex in group["words"]:
            line = _ruby_node(ex["ruby"]) + [" — " + ex["gloss"]]
            items.append({"tag": "li", "content": line})
        if items:
            body.append({"tag": "div", "content": group["label"]})
            body.append({"tag": "ul", "content": items})

    return body


def build_term_entry(record):
    """Build one Yomitan term-bank entry for a normalized kanji record."""
    keyword = record["keyword"] or record["character"]
    glossary = [
        keyword,
        {"type": "structured-content", "content": _detail_content(record)},
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


def build_banks(records, aliases=None):
    """Assemble the four Yomitan banks from normalized records + term aliases.

    Records are sorted deterministically by Unicode code point. Aliases (old
    form -> canonical form) contribute only term-bank entries. Returns a dict
    with term_bank, term_meta_bank, kanji_bank, kanji_meta_bank.
    """
    aliases = aliases or {}
    ordered = sorted(records, key=lambda r: ord(r["character"]))

    term_bank = []
    term_meta_bank = []
    kanji_bank = []
    kanji_meta_bank = []

    for r in ordered:
        term_bank.append(build_term_entry(r))
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
    "Dictionary data derived from Jiten (https://jiten.moe), using "
    "JMdict/KANJIDIC data from the Electronic Dictionary Research and "
    "Development Group (EDRDG). Data is redistributed under CC BY-SA 4.0; "
    "see https://creativecommons.org/licenses/by-sa/4.0/ and "
    "https://www.edrdg.org/edrdg/licence.html."
)

LICENSE_DATA_TEXT = (
    "Dictionary data derived from Jiten (https://jiten.moe), using JMdict/KANJIDIC\n"
    "data from the Electronic Dictionary Research and Development Group (EDRDG).\n"
    "Data is redistributed under CC BY-SA 4.0; see\n"
    "https://creativecommons.org/licenses/by-sa/4.0/ and\n"
    "https://www.edrdg.org/edrdg/licence.html.\n"
)

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
        "description": "Minimal kanji dictionary generated from Jiten data.",
        "attribution": ATTRIBUTION,
        "sourceLanguage": "ja",
        "targetLanguage": "en",
        "frequencyMode": "rank-based",
    }


def content_hash(banks):
    """SHA-256 over the normalized bank content only (revision-independent)."""
    payload = dump_json({name: banks[name] for _, name in BANK_FILES})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Fixed ZIP member metadata for reproducible archives.
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def _zip_member(name, data):
    info = zipfile.ZipInfo(filename=name, date_time=_ZIP_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o644 & 0xFFFF) << 16  # -rw-r--r--
    info.create_system = 3  # unix
    return info, data


def build_zip(banks, revision):
    """Build a deterministic Yomitan ZIP (bytes) with all members at the root.

    Member order, timestamps, and permissions are fixed so two builds from the
    same inputs are byte-identical.
    """
    members = [("index.json", dump_json(build_index(revision)))]
    for filename, key in BANK_FILES:
        members.append((filename, dump_json(banks[key])))
    members.append(("LICENSE-data.txt", LICENSE_DATA_TEXT))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in members:
            info, data = _zip_member(name, text.encode("utf-8"))
            zf.writestr(info, data)
    return buf.getvalue()
