"""RED/GREEN: the compact card carries NO warning / disclaimer / hedging prose.

The design must stay minimal: every entry is real dictionary content (readings,
meanings, vocabulary, provenance metadata), never editorial disclaimers,
warnings, or "may be approximate" hedging. This guards against a regression that
re-introduces such prose into the term entries or the stylesheet.
"""
import json
import pathlib
import re

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"

BANNED = re.compile(
    r"\b(disclaimer|warning|caution|please note|note that|beware|caveat|"
    r"may be inaccurate|not guaranteed|use at your own risk|"
    r"approximate only|unofficial data)\b",
    re.IGNORECASE,
)

CHARS = ["場", "生", "来", "事", "男", "行", "高"]


def rec(name):
    return bk.normalize_record(json.loads((FIX / name).read_text(encoding="utf-8")))


def test_no_warning_or_disclaimer_prose_in_term_entries():
    for char in CHARS:
        entry = bk.build_term_entry(rec(f"{char}.json"))
        blob = json.dumps(entry, ensure_ascii=False)
        hits = BANNED.findall(blob)
        assert not hits, f"{char}: banned warning/disclaimer prose present: {hits}"


def test_no_warning_or_disclaimer_prose_in_styles():
    assert not BANNED.findall(bk.STYLES_CSS)
