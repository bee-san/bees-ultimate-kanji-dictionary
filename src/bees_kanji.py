"""Bee's Ultimate Kanji Dictionary -- minimal Jiten -> Yomitan generator.

One module owns the whole pipeline: fetch -> normalize -> validate -> build.
Kept small and understandable on purpose. No service layers, no plugins.
"""

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
