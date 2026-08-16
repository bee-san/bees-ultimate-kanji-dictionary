"""Bee's Ultimate Kanji Dictionary -- minimal Jiten -> Yomitan generator.

One module owns the whole pipeline: fetch -> normalize -> validate -> build.
Kept small and understandable on purpose. No service layers, no plugins.
"""

import re

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
