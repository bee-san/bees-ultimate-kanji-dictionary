"""RED tests: string cleaning and ruby parsing.

Every displayed string must be trimmed/collapsed and rejected if it is empty,
case-insensitive 'missing', '???', contains raw HTML/XML tags, or malformed
ruby. Furigana bracket notation must parse into Yomitan ruby segments.
"""
import bees_kanji as bk


def test_clean_text_trims_and_collapses_whitespace():
    assert bk.clean_text("  measure   place ") == "measure place"


def test_clean_text_rejects_junk_returns_none():
    assert bk.clean_text("") is None
    assert bk.clean_text("   ") is None
    assert bk.clean_text("missing") is None
    assert bk.clean_text("MISSING") is None
    assert bk.clean_text("???") is None
    assert bk.clean_text("<b>plumb</b>") is None  # raw tags rejected
    assert bk.clean_text("a < b tag") is None      # stray angle bracket rejected


def test_clean_text_keeps_ordinary_gloss():
    assert bk.clean_text("entrance (on the stage)") == "entrance (on the stage)"


def test_clean_meanings_dedups_and_drops_junk_preserving_order():
    raw = ["  missing ", "measure", "measure", "???", "fathom", "<b>plumb</b>"]
    assert bk.clean_meanings(raw) == ["measure", "fathom"]


def test_parse_furigana_valid_bracket_notation():
    # 場[ば]所[しょ] -> two ruby segments
    segs = bk.parse_furigana("場[ば]所[しょ]")
    assert segs == [("場", "ば"), ("所", "しょ")]


def test_parse_furigana_base_without_reading():
    # 測[はか]る -> kanji ruby then trailing kana with no reading
    segs = bk.parse_furigana("測[はか]る")
    assert segs == [("測", "はか"), ("る", "")]


def test_parse_furigana_rejects_malformed_ruby():
    assert bk.parse_furigana("bad[ruby") is None
    assert bk.parse_furigana("") is None
