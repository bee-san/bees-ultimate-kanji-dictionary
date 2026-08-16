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
