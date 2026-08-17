"""RED tests: reading-share donut derived from complete Jiten group totals.

The statistic is a share of Jiten VOCABULARY ENTRIES by reading -- computed from
every valid ``wordsByReading[].totalWords`` group total (the complete Jiten
vocabulary-entry count for that reading), NOT the 1-2 example words displayed in
the entry. Percentages are truthful integer shares summing to exactly 100 via
largest-remainder rounding, reproducible, capped at a sensible segment count
with an explicit "Other" tail, and rendered with a visible text legend plus a
semantic fallback so nothing depends on colour or CSS alone. When no valid group
total exists the statistic is omitted entirely rather than faked from examples.
"""
import json
import pathlib

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


def rec(name):
    return bk.normalize_record(json.loads((FIX / name).read_text(encoding="utf-8")))


def _syn(reading_entry_counts):
    """A minimal normalized record carrying only the reading counts we test."""
    return {
        "character": "X", "on": [], "kun": [], "senses": [], "keyword": None,
        "frequency_rank": None, "grade": None, "jlpt": None,
        "stroke_count": None, "examples": [],
        "reading_entry_counts": reading_entry_counts,
    }


def test_distribution_uses_full_totalwords_not_shown_examples():
    # 場 has only 1-2 example words shown, but the donut denominator is the full
    # Jiten group totals じょう=2904 + ば=2083 + えき=1 = 4988.
    r = rec("場.json")
    dist = bk.reading_distribution(r)
    assert dist["total"] == 4988
    # segment counts sum back to the denominator (no lost/invented entries)
    assert sum(seg["count"] for seg in dist["segments"]) == 4988
    by_reading = {seg["reading"]: seg for seg in dist["segments"]}
    assert by_reading["じょう"]["count"] == 2904
    assert by_reading["ば"]["count"] == 2083
    assert by_reading["えき"]["count"] == 1
    # the count is NOT the shown-example count (which is tiny)
    shown = sum(len(g["words"]) for g in r["examples"])
    assert dist["total"] != shown


def test_ba_percentages_are_truthful_and_sum_to_100():
    r = rec("場.json")
    dist = bk.reading_distribution(r)
    pct = {seg["reading"]: seg["percent"] for seg in dist["segments"]}
    assert pct == {"じょう": 58, "ば": 42, "えき": 0}   # largest-remainder
    assert sum(seg["percent"] for seg in dist["segments"]) == 100
    # a nonzero tiny group renders 0% but keeps its exact count visible
    assert next(s for s in dist["segments"] if s["reading"] == "えき")["count"] == 1


def test_sei_denominator_is_3922_over_all_28_groups():
    r = rec("生.json")
    dist = bk.reading_distribution(r)
    assert dist["total"] == 3922
    assert sum(seg["count"] for seg in dist["segments"]) == 3922
    assert sum(seg["percent"] for seg in dist["segments"]) == 100


def test_segments_annotate_reading_and_class():
    # Each segment carries the actual reading plus its On/Kun/Other class as
    # secondary text -- the class is never the aggregation key.
    r = rec("場.json")
    dist = bk.reading_distribution(r)
    by_reading = {seg["reading"]: seg for seg in dist["segments"]}
    assert by_reading["じょう"]["reading_class"] == "On"
    assert by_reading["ば"]["reading_class"] == "Kun"


def test_distribution_reproducible():
    r = rec("生.json")
    assert bk.reading_distribution(r) == bk.reading_distribution(r)


def test_distribution_caps_segments_with_explicit_other():
    # 生 has 28 reading groups; keep the top four distinct readings and collapse
    # the remaining tail into one explicit "Other" segment.
    r = rec("生.json")
    dist = bk.reading_distribution(r)
    assert len(dist["segments"]) == bk.MAX_DONUT_SEGMENTS
    readings = [s["reading"] for s in dist["segments"][:4]]
    assert readings == ["せい", "お", "う", "しょう"]
    other = dist["segments"][-1]
    assert other["reading_class"] == "Other"
    assert other["reading"] in ("", None) or other["reading"] == "Other"
    # the Other segment is the exact remainder of the denominator
    assert other["count"] == 3922 - (1817 + 621 + 295 + 295)
    assert sum(s["count"] for s in dist["segments"]) == 3922
    assert sum(s["percent"] for s in dist["segments"]) == 100


def test_distribution_omitted_when_no_valid_totals():
    # No valid positive integer total -> omit the statistic entirely; never fall
    # back to sample-derived percentages or fabricated zeros.
    dist = bk.reading_distribution(_syn([]))
    assert dist["total"] == 0
    assert dist["segments"] == []


def test_duplicate_labels_aggregate_deterministically():
    # Duplicate normalized reading labels are aggregated in reading_entry_counts;
    # the distribution reflects the aggregated counts.
    r = _syn([
        {"reading": "じょう", "count": 105, "reading_class": "On"},
        {"reading": "ば", "count": 20, "reading_class": "Kun"},
    ])
    dist = bk.reading_distribution(r)
    assert dist["total"] == 125
    by_reading = {s["reading"]: s["count"] for s in dist["segments"]}
    assert by_reading == {"じょう": 105, "ば": 20}


def test_donut_node_has_concise_title_and_legend():
    r = rec("生.json")
    node = bk.build_donut_node(r)
    blob = json.dumps(node, ensure_ascii=False)
    assert "Reading distribution" in blob
    assert "Counts distinct Jiten vocabulary" not in blob
    assert "not usage frequency" not in blob
    assert "donut-disclaimer" not in blob
    # Misleading wording never appears.
    scan = blob.lower()
    for banned in ("usage frequency", "token frequency", "corpus", "probability",
                   "most used", "pronunciation", "real-world frequency", "chance"):
        assert banned not in scan, banned
    # a visible textual legend with percentages and exact entry counts exists
    assert "%" in blob
    assert "entries" in blob or "entry" in blob
    dist = bk.reading_distribution(r)
    for seg in dist["segments"]:
        if seg["reading"]:
            assert seg["reading"] in blob
    assert '"tag": "ul"' in blob
    assert "ariaLabel" not in blob and "ariaHidden" not in blob


def test_donut_legend_shows_reading_class_percent_and_count():
    r = rec("場.json")
    node = bk.build_donut_node(r)
    blob = json.dumps(node, ensure_ascii=False)
    # legend form: reading (class): percent% (count entries)
    assert "じょう" in blob and "(On)" in blob
    assert "58%" in blob
    assert "2,904" in blob or "2904" in blob   # exact count shown beside percent


def test_donut_node_absent_when_no_valid_totals():
    assert bk.build_donut_node(_syn([])) is None


def test_donut_omitted_for_fixture_with_only_invalid_totals():
    # 苔: every wordsByReading group has an invalid total (zero, negative,
    # string, float, boolean, missing) -> no valid positive integer total ->
    # omit the statistic entirely, never fabricate zeros or sample percentages.
    r = rec("malformed/苔.json")
    assert r["reading_entry_counts"] == []
    dist = bk.reading_distribution(r)
    assert dist["total"] == 0
    assert dist["segments"] == []
    assert bk.build_donut_node(r) is None


def test_donut_present_for_fixture_with_valid_totals():
    # 測: そく=400 + はか=20 = 420 valid positive totals -> statistic present,
    # computed over the full totals rather than the 1-3 shown example words.
    r = rec("malformed/測.json")
    dist = bk.reading_distribution(r)
    assert dist["total"] == 420
    assert sum(s["count"] for s in dist["segments"]) == 420
    assert sum(s["percent"] for s in dist["segments"]) == 100
    assert bk.build_donut_node(r) is not None
