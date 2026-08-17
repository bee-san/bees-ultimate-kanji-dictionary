"""RED tests: enrichment wired into the term entry with progressive disclosure.

The term detail must fold the visual enrichments (reading donut, phonetic
family, stroke order) into a keyboard-accessible progressive-disclosure section
without disturbing the honest keyword / readings / senses / examples that
existing tests already guard. Entries with no enrichment stay exactly as before.
"""
import json
import pathlib

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


def rec(name):
    return bk.normalize_record(json.loads((FIX / name).read_text(encoding="utf-8")))


def _enrichment_for(char):
    fam = {"component": "\u5bfa", "members": ["\u5f85", char], "source": "KanjiVG"}
    return {
        "strokes": {char: {"stroke_count": 5, "components": [char],
                           "asset": bk.kanjivg_asset_name(char)}},
        "families_by_char": {char: fam},
        "families": {"\u5bfa": fam},
        "assets": {bk.kanjivg_asset_name(char): "<svg></svg>"},
    }


def test_term_entry_without_enrichment_matches_legacy_shape():
    r = rec("\u5834.json")  # 場
    entry = bk.build_term_entry(r)  # no enrichment arg -> unchanged
    assert entry[0] == "\u5834"
    assert entry[5][0] == "location"  # keyword still first glossary item
    detail = entry[5][1]
    assert detail["type"] == "structured-content"


def test_term_entry_embeds_donut_when_reading_counts_present():
    r = rec("\u751f.json")  # 生 has full wordsByReading group totals
    entry = bk.build_term_entry(r, enrichment=None)
    blob = json.dumps(entry, ensure_ascii=False)
    # donut renders the truthful share of Jiten vocabulary entries by reading,
    # computed over the complete group totals (denominator 3922), NOT examples
    assert "reading-donut" in blob
    assert "%" in blob
    assert "Reading distribution" in blob
    assert "Counts distinct Jiten vocabulary" not in blob
    assert bk.reading_distribution(r)["total"] == 3922


def test_term_entry_embeds_stroke_and_phonetic_when_enriched():
    r = rec("\u751f.json")
    enr = _enrichment_for("\u751f")
    entry = bk.build_term_entry(r, enrichment=enr)
    blob = json.dumps(entry, ensure_ascii=False)
    assert "stroke-order" in blob
    assert "phonetic-family" in blob
    assert bk.kanjivg_asset_name("\u751f") in blob


def test_term_entry_uses_progressive_disclosure_section():
    r = rec("\u751f.json")
    enr = _enrichment_for("\u751f")
    entry = bk.build_term_entry(r, enrichment=enr)
    blob = json.dumps(entry, ensure_ascii=False)
    # a details/summary progressive-disclosure wrapper carries the extras
    assert '"details"' in blob and '"summary"' in blob


def test_build_banks_threads_enrichment_into_term_entries():
    r = rec("\u751f.json")
    enr = _enrichment_for("\u751f")
    banks = bk.build_banks([r], enrichment=enr)
    blob = json.dumps(banks["term_bank"], ensure_ascii=False)
    assert "stroke-order" in blob


def test_no_percentages_leak_into_kanji_meta_or_frequency():
    # Enrichment must not introduce % or totalWords into meta/frequency banks.
    r = rec("\u751f.json")
    enr = _enrichment_for("\u751f")
    banks = bk.build_banks([r], enrichment=enr)
    for key in ("kanji_meta_bank", "term_meta_bank"):
        blob = json.dumps(banks[key], ensure_ascii=False)
        assert "%" not in blob
        assert "totalWords" not in blob
