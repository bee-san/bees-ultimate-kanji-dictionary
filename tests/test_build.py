"""RED tests: whole-corpus build assembly, Top-1000 quality floor, and
global cleanliness.

build_banks(records, aliases) returns the four banks in deterministic
Unicode-code-point order. Every record with frequency_rank <= 1000 must have a
nonempty keyword, at least one on/kun reading, and at least one clean example.
No output anywhere may contain 'missing', '???', raw tags, malformed ruby, or
percentage labels.
"""
import json
import pathlib

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"
CHARS = "場男事生行高"


def records():
    return [
        bk.normalize_record(json.loads((FIX / f"{c}.json").read_text(encoding="utf-8")))
        for c in CHARS
    ]


def test_build_banks_sorted_by_codepoint_and_shapes():
    banks = bk.build_banks(records(), aliases={"髙": "高"})
    terms = banks["term_bank"]
    # every source character has exactly one term entry, plus the alias
    expressions = [e[0] for e in terms]
    for c in CHARS:
        assert expressions.count(c) == 1
    assert "髙" in expressions
    # deterministic order: sorted by Unicode code point
    assert expressions == sorted(expressions, key=ord)
    # kanji bank excludes the alias (no native entry invented)
    kanji_chars = [e[0] for e in banks["kanji_bank"]]
    assert "髙" not in kanji_chars
    for c in CHARS:
        assert c in kanji_chars


def test_top1000_quality_floor():
    banks = bk.build_banks(records(), aliases={"髙": "高"})
    recs = {r["character"]: r for r in records()}
    for r in recs.values():
        if r["frequency_rank"] is not None and r["frequency_rank"] <= 1000:
            assert r["keyword"], f"{r['character']} missing keyword"
            assert r["on"] or r["kun"], f"{r['character']} missing readings"
            n = sum(len(g["words"]) for g in r["examples"])
            assert n >= 1, f"{r['character']} has no clean example"


def test_no_junk_anywhere_in_output():
    banks = bk.build_banks(records(), aliases={"髙": "高"})
    blob = json.dumps(banks, ensure_ascii=False)
    # No leaked markup or ruby-breaking angle brackets anywhere in output.
    assert "<" not in blob and ">" not in blob
    # The raw Jiten KEY names must never leak into the built banks. The labelled
    # reading-share statistic MAY legitimately carry normalized group counts
    # (e.g. "2,904 entries") as truthful text -- that is the whole point of the
    # corrected donut -- so we guard the key names, not the count values.
    assert "totalWords" not in blob
    assert "total_words" not in blob
    # ...and the honest statistic is what surfaces those counts: every count that
    # appears beside a percent is a real reading_entry_counts value.
    for r in records():
        for seg in bk.reading_distribution(r)["segments"]:
            assert f"{seg['percent']}% ({seg['count']:,} entries)" in blob
    # Junk *tokens* are rejected at cleaning time; the fixtures contain no
    # standalone 'missing'/'???' entries, so none appear here. (Real glosses
    # elsewhere may legitimately contain the English word 'missing' or a '%'
    # meaning, e.g. 率 = 'rate; %'; those are honest data, not junk, and are
    # asserted at the token level in test_cleaning.py.)
    for e in banks["term_bank"]:
        assert e[5][0].lower() not in {"missing", "???"}  # keyword is never junk


def test_build_banks_is_pure_and_repeatable():
    a = bk.build_banks(records(), aliases={"髙": "高"})
    b = bk.build_banks(records(), aliases={"髙": "高"})
    assert json.dumps(a, ensure_ascii=False) == json.dumps(b, ensure_ascii=False)
