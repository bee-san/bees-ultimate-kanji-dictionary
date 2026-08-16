"""RED tests: normalize a raw Jiten kanji payload into a clean record.

Covers the contract behaviors for 場 (both reading groups produce examples),
男/事/生/行 (canonical readings preserved, examples chosen by low word rank
rather than totalWords, no percentages), example cutoffs and dedup, and
malformed-input rejection.
"""
import json
import pathlib

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_normalize_ba_keyword_readings_and_two_reading_groups():
    rec = bk.normalize_record(load("場.json"))
    assert rec["character"] == "場"
    assert rec["keyword"] == "location"          # first cleaned meaning
    assert rec["senses"] == ["location", "place"]
    assert rec["on"] == ["ジョウ", "チョウ"]       # preserved verbatim
    assert rec["kun"] == ["ば"]
    assert rec["frequency_rank"] == 57
    assert rec["stroke_count"] == 12
    assert rec["grade"] == 2
    assert rec["jlpt"] == 3
    # 場 should surface clean examples for the じょう (On) and ば (Kun) groups.
    labels = {g["label"] for g in rec["examples"]}
    assert "On" in labels and "Kun" in labels
    # every example has a surface, gloss, positive rank, and ruby segments.
    for group in rec["examples"]:
        assert group["reading_class"] in {"On", "Kun", "Other"}
        for ex in group["words"]:
            assert ex["surface"]
            assert ex["gloss"]
            assert ex["rank"] > 0
            assert ex["ruby"]  # parsed furigana segments


def test_examples_selected_by_word_rank_not_totalwords():
    # 男: the 'お' group has the largest totalWords (1401) but its best word
    # rank (41135) is worse than the cutoff, while 'おとこ' has rank 154. So
    # ordering must be driven by word rank, not totalWords: おとこ (best rank)
    # is selected and appears first; the high-totalWords 'お' group is dropped
    # because all its candidates are rarer than the cutoff.
    rec = bk.normalize_record(load("男.json"))
    ordered_readings = [g["reading"] for g in rec["examples"]]
    assert ordered_readings[0] == "おとこ"
    assert "お" not in ordered_readings
    # No group exposes totalWords as a statistic/percentage.
    for g in rec["examples"]:
        assert "total_words" not in g
        assert "percent" not in g


def test_example_group_and_count_limits():
    # 生 has ~28 reading groups; keep at most 3 groups and <=6 examples total.
    rec = bk.normalize_record(load("生.json"))
    assert len(rec["examples"]) <= 3
    total = sum(len(g["words"]) for g in rec["examples"])
    assert 1 <= total <= 6
    for g in rec["examples"]:
        assert 1 <= len(g["words"]) <= 2


def test_rank_cutoff_drops_rare_examples():
    # 高 'た' group's only word 高価い is rank 34028 (> 25000) -> excluded.
    rec = bk.normalize_record(load("高.json"))
    for g in rec["examples"]:
        for ex in g["words"]:
            assert ex["rank"] <= 25000


def test_canonical_readings_preserved_for_multiple_kanji():
    for name, on, kun in [
        ("事.json", ["ジ", "ズ"], ["こと", "つか.う", "つか.える"]),
        ("生.json", ["セイ", "ショウ"], None),
        ("行.json", ["コウ", "ギョウ", "アン"], None),
    ]:
        rec = bk.normalize_record(load(name))
        assert rec["on"] == on
        if kun is not None:
            assert rec["kun"] == kun


def test_examples_deduped_by_word_and_reading_index():
    rec = bk.normalize_record(load("場.json"))
    keys = []
    for g in rec["examples"]:
        for ex in g["words"]:
            keys.append((ex["word_id"], ex["reading_index"]))
    assert len(keys) == len(set(keys))


def test_normalize_rejects_malformed_payloads():
    import pytest

    for bad in [None, [], "x", {}, {"character": ""}, {"character": 5}]:
        with pytest.raises(bk.MalformedPayload):
            bk.normalize_record(bad)


def test_dirty_payload_cleans_meanings_and_examples():
    rec = bk.normalize_record(load("malformed/測.json"))
    assert rec["keyword"] == "measure"                 # 'missing' dropped
    assert "???" not in rec["senses"]
    for g in rec["examples"]:
        for ex in g["words"]:
            assert "<" not in ex["gloss"] and ">" not in ex["gloss"]
            assert ex["gloss"].lower() != "missing"
            assert ex["rank"] <= 25000


def test_readings_are_cleaned_and_deduplicated_without_rewriting_valid_text():
    payload = load("場.json")
    payload["onReadings"] = [" ジョウ ", "missing", "ジョウ", "<b>チョウ</b>", None]
    payload["kunReadings"] = ["ば", "???", " ば ", ""]

    rec = bk.normalize_record(payload)

    assert rec["on"] == ["ジョウ"]
    assert rec["kun"] == ["ば"]


def test_scalar_reading_and_meaning_fields_are_rejected_as_malformed_containers():
    payload = load("場.json")
    payload["meanings"] = "place"
    payload["onReadings"] = "ジョウ"
    payload["kunReadings"] = "ば"

    rec = bk.normalize_record(payload)

    assert rec["senses"] == []
    assert rec["on"] == []
    assert rec["kun"] == []
