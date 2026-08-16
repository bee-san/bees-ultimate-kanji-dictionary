"""RED tests: reading normalization and On/Kun/Other classification.

These are the foundation for turning Jiten's hiragana wordsByReading labels
into honest On/Kun/Other row labels in the popup.
"""
import bees_kanji as bk


def test_katakana_on_reading_normalizes_to_hiragana():
    # On readings arrive as katakana; normalized form is hiragana for matching.
    assert bk.normalize_reading("ジョウ") == "じょう"
    assert bk.normalize_reading("コウ") == "こう"


def test_kun_reading_strips_okurigana_markers():
    # Kun readings carry a "." okurigana separator and "-" affix marks.
    assert bk.normalize_reading("たか.い") == "たか"
    assert bk.normalize_reading("-ゆ.き") == "ゆ"
    assert bk.normalize_reading("つか.える") == "つか"


def test_normalize_reading_trims_and_handles_empty():
    assert bk.normalize_reading("  ば  ") == "ば"
    assert bk.normalize_reading("") == ""
    assert bk.normalize_reading("   ") == ""


def test_classify_reading_matches_on_then_kun_else_other():
    on = ["ジョウ", "チョウ"]
    kun = ["ば"]
    # 場: じょう is an on reading, ば is kun, えき is neither.
    assert bk.classify_reading("じょう", on, kun) == "On"
    assert bk.classify_reading("ば", on, kun) == "Kun"
    assert bk.classify_reading("えき", on, kun) == "Other"


def test_classify_reading_respects_okurigana_kun_forms():
    # 高: kun たか.い -> normalized たか must classify たか as Kun.
    on = ["コウ"]
    kun = ["たか.い", "たか", "-だか", "たか.まる"]
    assert bk.classify_reading("たか", on, kun) == "Kun"
    assert bk.classify_reading("こう", on, kun) == "On"
    assert bk.classify_reading("だか", on, kun) == "Kun"  # -だか -> だか
