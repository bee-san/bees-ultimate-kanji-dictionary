"""RED tests: KANJIDIC2 simple licensed fallback for characters absent from Jiten.

The dictionary must expand beyond Jiten's ~3,904-character sitemap using
KANJIDIC2 as a simple fallback, WITHOUT weakening the enriched Jiten entries.

Contract proven here:
  - parse_kanjidic2 extracts only supported fields (English meanings, on/kun/
    nanori readings, stroke count, grade, JLPT) and skips entries with no
    useful data or a non-single-character literal;
  - a KANJIDIC2-only character yields a clean fallback record whose term and
    kanji bank entries carry the honest readings/meanings;
  - fallback records omit unsupported statistics: no frequency rank/meta, no
    examples, no reading-distribution donut, no phonetic/enrichment fabrication,
    and no Jiten attribution for data Jiten did not provide;
  - Jiten wins on every duplicate character (its keyword/meanings/readings/rank/
    examples/enrichment are untouched; KANJIDIC2 never overwrites them);
  - merged records are unique by character (duplicates impossible) and every
    character remains a single valid Unicode scalar;
  - the merge is deterministic (codepoint-ordered, stable across runs).
"""
import io
import json
import pathlib
import zipfile

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"
KD2_XML = (FIX / "kanjidic2_sample.xml").read_text(encoding="utf-8")


def _jiten_rec(name):
    return bk.normalize_record(json.loads((FIX / name).read_text(encoding="utf-8")))


# --- parser -----------------------------------------------------------------

def test_parse_kanjidic2_extracts_supported_fields_only():
    idx = bk.parse_kanjidic2(KD2_XML)
    assert set("場唖丂").issubset(idx)          # useful entries kept
    a = idx["唖"]
    assert a["meanings"] == ["mute", "dumb"]     # English only; es dropped
    assert a["on"] == ["ア", "アク"]
    assert a["kun"] == ["おし"]
    assert a["nanori"] == ["あお"]
    assert a["stroke_count"] == 10
    assert a["grade"] is None                    # absent in source -> None
    assert a["jlpt"] is None


def test_parse_kanjidic2_skips_useless_and_nonsingle_literals():
    idx = bk.parse_kanjidic2(KD2_XML)
    # multi-character literal is not a kanji -> skipped
    assert "everonlyindex" not in idx
    # 丄 has only a pinyin reading (no eng/on/kun) -> no useful data -> skipped
    assert "丄" not in idx
    # every surviving key is a single valid Unicode scalar
    for ch in idx:
        assert isinstance(ch, str) and len(ch) == 1
        assert 0 <= ord(ch) <= 0x10FFFF


# --- fallback record --------------------------------------------------------

def test_kanjidic2_only_char_yields_clean_fallback_record():
    idx = bk.parse_kanjidic2(KD2_XML)
    rec = bk.kanjidic2_record("唖", idx["唖"])
    assert rec["character"] == "唖"
    assert rec["keyword"] == "mute"
    assert rec["senses"] == ["mute", "dumb"]
    assert rec["on"] == ["ア", "アク"]
    assert rec["kun"] == ["おし"]
    assert rec["stroke_count"] == 10
    # honest omissions: no rank, no examples (nothing Jiten-derived invented)
    assert rec["frequency_rank"] is None
    assert rec["examples"] == []
    # no reading-share statistic can be fabricated for a fallback-only record
    assert rec["reading_entry_counts"] == []
    assert rec["grade"] is None
    assert rec["jlpt"] is None


def test_fallback_banks_are_honest_no_freq_no_donut_no_enrichment():
    idx = bk.parse_kanjidic2(KD2_XML)
    rec = bk.kanjidic2_record("唖", idx["唖"])
    # native kanji entry carries readings + meanings, no frequency stat
    kanji = bk.build_kanji_entry(rec)
    assert kanji[0] == "唖"
    assert kanji[1] == "ア アク"
    assert kanji[2] == "おし"
    assert kanji[4] == ["mute", "dumb"]
    assert "Frequency rank" not in kanji[5]
    # no frequency meta for a rank-less fallback record
    assert bk.build_term_meta(rec) is None
    assert bk.build_kanji_meta(rec) is None
    # term entry detail must not fabricate a distribution / percentages /
    # enrichment, and must not claim Jiten attribution for data Jiten did not provide.
    term = bk.build_term_entry(rec)
    blob = json.dumps(term, ensure_ascii=False)
    assert "reading-distribution" not in blob
    assert "reading-donut" not in blob
    assert "%" not in blob
    assert "phonetic-family" not in blob
    assert "stroke-order" not in blob
    assert bk.build_reading_distribution_node(rec) is None


# --- merge (Jiten wins, unique, valid, deterministic) -----------------------

def test_merge_jiten_wins_on_duplicate_characters():
    idx = bk.parse_kanjidic2(KD2_XML)
    jiten = [_jiten_rec("場.json")]
    merged = bk.merge_kanjidic2(jiten, idx)
    by_char = {r["character"]: r for r in merged}
    ba = by_char["場"]
    # Jiten's enriched record is preserved verbatim -- KANJIDIC2 sample carried
    # a deliberately WRONG meaning + a French gloss + freq 52; none may appear.
    assert ba is jiten[0]
    assert ba["senses"] == ["location", "place"]
    assert ba["keyword"] == "location"
    assert ba["frequency_rank"] == 57            # Jiten's rank, not KD2 freq 52
    assert ba["examples"]                        # Jiten examples intact
    assert ba["reading_entry_counts"]            # full reading counts preserved
    assert "WRONG-SHOULD-NOT-WIN" not in json.dumps(ba, ensure_ascii=False)


def test_merge_adds_only_absent_characters_uniquely_and_sorted():
    idx = bk.parse_kanjidic2(KD2_XML)
    jiten = [_jiten_rec("場.json")]
    merged = bk.merge_kanjidic2(jiten, idx)
    chars = [r["character"] for r in merged]
    # no duplicates possible
    assert len(chars) == len(set(chars))
    # 場 kept once (from Jiten), 唖 and 丂 added from KANJIDIC2
    assert set(chars) == {"場", "唖", "丂"}
    # deterministic codepoint ordering
    assert chars == sorted(chars, key=ord)
    # every merged character is a single valid Unicode scalar
    for ch in chars:
        assert len(ch) == 1 and 0 <= ord(ch) <= 0x10FFFF


def test_merge_is_deterministic_across_runs():
    idx = bk.parse_kanjidic2(KD2_XML)
    jiten = [_jiten_rec("場.json"), _jiten_rec("生.json")]
    a = bk.merge_kanjidic2(jiten, idx)
    b = bk.merge_kanjidic2(jiten, idx)
    assert [r["character"] for r in a] == [r["character"] for r in b]
    assert bk.dump_json(bk.build_banks(a)) == bk.dump_json(bk.build_banks(b))


def test_merged_build_materially_exceeds_jiten_and_stays_valid():
    idx = bk.parse_kanjidic2(KD2_XML)
    jiten = [_jiten_rec("場.json")]
    merged = bk.merge_kanjidic2(jiten, idx)
    # sample fixture: 1 Jiten + 2 KANJIDIC2-only additions
    assert len(merged) == 3
    assert len(merged) > len(jiten)
    banks = bk.build_banks(merged)
    # every in-memory kanji-bank + term-bank entry expression is a single scalar
    for entry in banks["kanji_bank"]:
        assert len(entry[0]) == 1
    z = bk.build_zip(banks, revision="2026.01.01")
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        names = zf.namelist()
        tb = json.loads(zf.read("term_bank_1.json"))
    # The native kanji bank is deliberately not shipped; the structured term
    # bank is the single canonical surface and carries every merged character.
    assert "kanji_bank_1.json" not in names
    assert "kanji_meta_bank_1.json" not in names
    assert len(tb) == 3
