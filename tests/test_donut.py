"""RED tests: reading-distribution donut derived from selected examples.

Percentages must be truthful (integer counts over the total selected example
words shown in THIS entry -- never Jiten totalWords), reproducible, capped at a
sensible segment count with an explicit "other" segment, and rendered with a
visible text legend plus a semantic/text fallback so nothing depends on color
or CSS alone.
"""
import json
import pathlib

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


def rec(name):
    return bk.normalize_record(json.loads((FIX / name).read_text(encoding="utf-8")))


def test_distribution_counts_selected_example_words_not_totalwords():
    r = rec("生.json")
    dist = bk.reading_distribution(r)
    # total equals the number of example words actually shown in the entry
    shown = sum(len(g["words"]) for g in r["examples"])
    assert dist["total"] == shown
    assert dist["total"] > 0
    # segment counts sum to the total (no lost/invented words)
    assert sum(seg["count"] for seg in dist["segments"]) == dist["total"]
    # no Jiten totalWords value leaks in as a count
    assert all(seg["count"] <= dist["total"] for seg in dist["segments"])


def test_distribution_percentages_are_truthful_and_sum_to_100():
    r = rec("生.json")
    dist = bk.reading_distribution(r)
    # each percent is round(count/total*100) and the reported percents sum 100
    assert sum(seg["percent"] for seg in dist["segments"]) == 100
    for seg in dist["segments"]:
        assert 0 <= seg["percent"] <= 100


def test_distribution_reproducible():
    r = rec("生.json")
    assert bk.reading_distribution(r) == bk.reading_distribution(r)


def test_distribution_caps_segments_with_explicit_other():
    # A synthetic record with many DISTINCT-label single-word groups must
    # collapse the tail into one explicit "Other" segment, never exceeding cap.
    labels = ["On", "Kun", "Nanori", "Kan-on", "Go-on", "To-on", "Kwan"]
    groups = [
        {"reading": f"r{i}", "reading_class": lbl, "label": lbl,
         "words": [{"surface": f"w{i}", "gloss": "g", "rank": i + 1,
                    "ruby": [(f"w{i}", "")], "word_id": i, "reading_index": 0}]}
        for i, lbl in enumerate(labels)
    ]
    r = {"character": "X", "on": [], "kun": [], "senses": [], "keyword": None,
         "frequency_rank": None, "grade": None, "jlpt": None,
         "stroke_count": None, "examples": groups}
    dist = bk.reading_distribution(r)
    assert len(dist["segments"]) <= bk.MAX_DONUT_SEGMENTS
    seg_labels = [s["label"] for s in dist["segments"]]
    assert "Other" in seg_labels  # tail collapsed
    assert sum(s["count"] for s in dist["segments"]) == len(labels)
    assert sum(s["percent"] for s in dist["segments"]) == 100


def test_distribution_empty_when_no_examples():
    r = {"character": "X", "on": [], "kun": [], "senses": [], "keyword": None,
         "frequency_rank": None, "grade": None, "jlpt": None,
         "stroke_count": None, "examples": []}
    dist = bk.reading_distribution(r)
    assert dist["total"] == 0
    assert dist["segments"] == []


def test_donut_node_has_text_legend_and_semantic_fallback():
    r = rec("生.json")
    node = bk.build_donut_node(r)
    blob = json.dumps(node, ensure_ascii=False)
    # a visible textual legend with percentages exists (not color-only)
    assert "%" in blob
    # every segment label appears as text somewhere in the node
    dist = bk.reading_distribution(r)
    for seg in dist["segments"]:
        assert seg["label"] in blob
    # accessible: the graphic carries a text alternative via data/aria label
    assert "aria-label" in blob or "reading distribution" in blob.lower()


def test_donut_node_absent_when_no_examples():
    r = {"character": "X", "on": [], "kun": [], "senses": [], "keyword": None,
         "frequency_rank": None, "grade": None, "jlpt": None,
         "stroke_count": None, "examples": []}
    assert bk.build_donut_node(r) is None
