"""RED tests: built banks validate against the pinned official Yomitan schemas.

This is the hard correctness gate -- if these entries do not satisfy Yomitan's
own schemas they will not import. We validate representative term, kanji, and
meta entries plus the alias entry.
"""
import json
import pathlib

import jsonschema
import bees_kanji as bk

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures"
SCHEMAS = ROOT / "schemas"


def schema(name):
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def rec(name):
    return bk.normalize_record(json.loads((FIX / name).read_text(encoding="utf-8")))


def test_term_bank_validates_against_schema():
    entries = [bk.build_term_entry(rec(f"{c}.json")) for c in "場男事生行高"]
    entries.append(bk.build_alias_term_entry("髙", "高"))
    jsonschema.validate(entries, schema("dictionary-term-bank-v3-schema.json"))


def test_kanji_bank_validates_against_schema():
    entries = [bk.build_kanji_entry(rec(f"{c}.json")) for c in "場男事生行高"]
    jsonschema.validate(entries, schema("dictionary-kanji-bank-v3-schema.json"))


def test_term_meta_bank_validates_against_schema():
    entries = [m for c in "場男事生行高" if (m := bk.build_term_meta(rec(f"{c}.json")))]
    jsonschema.validate(entries, schema("dictionary-term-meta-bank-v3-schema.json"))


def test_kanji_meta_bank_validates_against_schema():
    entries = [m for c in "場男事生行高" if (m := bk.build_kanji_meta(rec(f"{c}.json")))]
    jsonschema.validate(entries, schema("dictionary-kanji-meta-bank-v3-schema.json"))
