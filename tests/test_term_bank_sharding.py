"""Term-bank sharding keeps Android imports bounded and deterministic."""

import io
import json
import zipfile

import bees_kanji as bk


def _term_entry(index):
    expression = f"字{index:05d}"
    return [expression, "", "", "", 0, [f"entry {index}"], index, ""]


def _banks(term_count):
    return {
        "term_bank": [_term_entry(index) for index in range(term_count)],
        "term_meta_bank": [],
        "kanji_bank": [],
        "kanji_meta_bank": [],
    }


def _term_bank_names(archive):
    return sorted(
        (name for name in archive.namelist() if name.startswith("term_bank_")),
        key=lambda name: int(name.removeprefix("term_bank_").removesuffix(".json")),
    )


def test_build_zip_splits_term_entries_into_contiguous_1000_entry_banks():
    banks = _banks(2_501)

    with zipfile.ZipFile(io.BytesIO(bk.build_zip(banks, "2026.09.01"))) as archive:
        names = _term_bank_names(archive)
        chunks = [json.loads(archive.read(name)) for name in names]

    assert names == ["term_bank_1.json", "term_bank_2.json", "term_bank_3.json"]
    assert [len(chunk) for chunk in chunks] == [1_000, 1_000, 501]
    assert [entry for chunk in chunks for entry in chunk] == banks["term_bank"]


def test_rebuilding_a_smaller_dictionary_cannot_retain_stale_term_banks():
    first = bk.build_zip(_banks(2_001), "2026.09.01")
    second = bk.build_zip(_banks(1), "2026.09.01")

    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert _term_bank_names(archive) == [
            "term_bank_1.json",
            "term_bank_2.json",
            "term_bank_3.json",
        ]
    with zipfile.ZipFile(io.BytesIO(second)) as archive:
        assert _term_bank_names(archive) == ["term_bank_1.json"]


def test_content_hash_covers_the_term_bank_sharding_policy(monkeypatch):
    banks = _banks(3)
    baseline = bk.content_hash(banks)

    monkeypatch.setattr(bk, "TERM_BANK_ENTRY_LIMIT", 2)

    assert bk.content_hash(banks) != baseline
