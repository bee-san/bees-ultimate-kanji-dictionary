"""RED tests: build Yomitan banks from normalized records + 髙 alias.

Validates term-bank single-character entries (keyword + structured detail),
kanji-bank native entries, frequency meta banks, and the explicit 髙->高 term
alias with no invented native/frequency data.
"""
import json
import pathlib

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


def rec(name):
    return bk.normalize_record(json.loads((FIX / name).read_text(encoding="utf-8")))


def test_term_bank_entry_shape_for_ba():
    r = rec("場.json")
    entry = bk.build_term_entry(r)
    # Yomitan term entry: [expression, reading, tags, rules, score, glossary, seq, termTags]
    assert isinstance(entry, list) and len(entry) == 8
    assert entry[0] == "場"          # expression
    assert entry[1] == ""            # reading (multi-reading kanji -> empty)
    assert entry[4] == 0             # neutral score
    glossary = entry[5]
    assert glossary[0] == "location"  # first item is the recall keyword
    detail = glossary[1]
    assert detail["type"] == "structured-content"
    assert entry[6] == ord("場")     # sequence = code point


def test_term_detail_uses_full_jiten_reading_share_denominator():
    r = rec("場.json")
    entry = bk.build_term_entry(r)
    blob = json.dumps(entry, ensure_ascii=False)
    # The raw Jiten KEY names must never leak, but the labelled reading-share
    # statistic legitimately carries the normalized group counts as text.
    assert "totalWords" not in blob and "total_words" not in blob
    # The donut is a share of Jiten vocabulary ENTRIES computed over the full
    # group totals (denominator 4988), NOT the 1-2 shown example words.
    dist = bk.reading_distribution(r)
    assert dist["total"] == 4988
    assert sum(s["count"] for s in dist["segments"]) == 4988
    # every visible legend line matches the honest full-payload count + percent
    for seg in dist["segments"]:
        assert f"{seg['percent']}% ({seg['count']:,} entries)" in blob
    # the truthful title and non-occurrence disclaimer are present
    assert "Share of Jiten vocabulary entries by reading" in blob
    assert bk.DONUT_DISCLAIMER in blob


def test_kanji_bank_entry_shape_for_ba():
    r = rec("場.json")
    entry = bk.build_kanji_entry(r)
    # Yomitan kanji entry: [character, onyomi, kunyomi, tags, meanings, stats]
    assert isinstance(entry, list) and len(entry) == 6
    assert entry[0] == "場"
    assert entry[1] == "ジョウ チョウ"      # space-joined on readings
    assert entry[2] == "ば"                # space-joined kun readings
    assert entry[4] == ["location", "place"]
    stats = entry[5]
    assert stats["Frequency rank"] == "57"
    assert stats["Grade"] == "2"
    assert stats["JLPT"] == "N3"
    assert stats["Strokes"] == "12"


def test_frequency_meta_entries_present_when_rank_known():
    r = rec("場.json")
    tm = bk.build_term_meta(r)
    km = bk.build_kanji_meta(r)
    assert tm == ["場", "freq", 57]
    assert km == ["場", "freq", 57]


def test_frequency_meta_omitted_when_rank_absent():
    r = rec("malformed/苺.json")     # no frequencyRank
    assert r["frequency_rank"] is None
    assert bk.build_term_meta(r) is None
    assert bk.build_kanji_meta(r) is None


def test_takahashi_alias_term_only_no_native_or_freq():
    entry = bk.build_alias_term_entry("髙", "高")
    assert isinstance(entry, list) and len(entry) == 8
    assert entry[0] == "髙"
    assert entry[6] == ord("髙")
    # glossary references 高 as the canonical form, no invented Jiten fields
    blob = json.dumps(entry, ensure_ascii=False)
    assert "高" in blob
    # alias contributes only a term entry: no native kanji or frequency builder
    assert not hasattr(bk, "build_alias_kanji_entry")
    assert not hasattr(bk, "build_alias_kanji_meta")
