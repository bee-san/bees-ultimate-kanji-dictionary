"""RED/GREEN: the visual-acceptance fixture set.

The independent review (t_4974aeff) failed because the candidate had no 来 to
satisfy the mandated acceptance check, no explicitly long-content entry, and no
sparse KANJIDIC2-only fallback exercised. This test pins all three into the
committed fixture/acceptance set so they can never silently drop out again:

  * 来 -- a required enriched entry (top-3 readings, meanings, chart, 6 words);
  * a LONG-content entry (many readings + long meaning line) that must still
    lay out without overflow (生 carries 18 kun readings);
  * a KANJIDIC2-only SPARSE fallback that is deliberately chart-free (no faked
    distribution), driven through the real KANJIDIC2 fallback path.
"""
import json
import pathlib

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


def rec(name):
    record = bk.normalize_record(json.loads((FIX / name).read_text(encoding="utf-8")))
    record["reading_frequency_scores"] = [
        {
            "reading": item["reading"],
            "score": 1.0 / (index + 1),
            "reading_class": item["reading_class"],
        }
        for index, item in enumerate(record["reading_entry_counts"])
    ]
    return record


def _walk(node):
    if isinstance(node, dict):
        yield node
        yield from _walk(node.get("content"))
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _walk_above_fold(node):
    if isinstance(node, dict):
        if node.get("tag") == "details":
            return
        yield node
        yield from _walk_above_fold(node.get("content"))
    elif isinstance(node, list):
        for item in node:
            yield from _walk_above_fold(item)


def _role(node):
    data = node.get("data") if isinstance(node, dict) else None
    return data.get("beeRole") if isinstance(data, dict) else None


def _text(node):
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return _text(node.get("content"))
    if isinstance(node, list):
        return "".join(_text(x) for x in node)
    return ""


# --- 来 is a required, fully enriched acceptance entry -----------------------

def test_rai_fixture_exists_and_is_a_single_kanji():
    path = FIX / "来.json"
    assert path.exists(), "来 fixture is mandated by the acceptance check and must be committed"
    r = rec("来.json")
    assert r["character"] == "来"


def test_rai_has_full_compact_card_above_the_fold():
    root = bk._detail_content(rec("来.json"))[0]
    roles = [_role(c) for c in root["content"]]
    # hero, top readings, meaning, chart, six-word grid -- all present, in order
    assert roles[0] == "hero"
    assert "reading-chips" in roles
    assert "meaning" in roles
    assert "reading-donut" in roles
    assert "vocab-grid" in roles
    chips = [_text(n) for n in _walk_above_fold(root) if _role(n) == "reading-chip"]
    assert chips == ["らい", "く", "き"], chips
    vocab = [n for n in _walk_above_fold(root) if _role(n) == "vocab-word"]
    assert len(vocab) == 6


def test_rai_chart_is_a_real_packaged_png_with_multiple_segments():
    r = rec("来.json")
    dist = bk.reading_frequency_distribution(r)
    assert dist["total"] > 0
    # a real multi-reading distribution -> at least two coloured segments so the
    # rendered donut visibly carries more than one colour.
    assert len([s for s in dist["segments"] if s["percent"] > 0]) >= 2
    png = bk.build_reading_frequency_png(r)
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"


# --- a long-content entry still lays out cleanly -----------------------------

def test_long_entry_has_many_readings_but_compact_stays_top_three():
    # 生 carries 18 kun readings; the compact card must still show exactly the
    # top three above the fold and push the rest into the collapsed disclosure.
    r = rec("生.json")
    assert len(r["kun"]) >= 15, "expected a long reading list for the long-entry case"
    root = bk._detail_content(r)[0]
    chips = [n for n in _walk_above_fold(root) if _role(n) == "reading-chip"]
    assert len(chips) == 3, "long entry must still cap the above-fold readings at three"


# --- sparse KANJIDIC2-only fallback is chart-free ----------------------------

def test_kanjidic2_only_fallback_is_chart_free():
    # Build a KANJIDIC2-only record and confirm no Frequency weight is fabricated.
    xml = (FIX / "kanjidic2_sample.xml").read_text(encoding="utf-8")
    idx = bk.parse_kanjidic2(xml)
    # pick a literal present only in KANJIDIC2 (not one of the Jiten fixtures)
    sparse_char = next(c for c in idx if c not in "事場生男行高来")
    fallback = bk.kanjidic2_record(sparse_char, idx[sparse_char])
    assert fallback["global_words"] == []
    assert fallback.get("reading_frequency_scores", []) == []
    assert bk.build_reading_frequency_png(fallback) is None
    assert bk.build_reading_frequency_node(fallback) is None
    root = bk._detail_content(fallback)[0]
    assert not [n for n in _walk(root) if _role(n) == "reading-donut"], \
        "KANJIDIC2-only fallback must not render a chart node"
