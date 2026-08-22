"""Frequency-weight reading chart tests."""

import hashlib
import io
import json
import math
import zipfile

import pytest
from PIL import Image

import bees_kanji as bk


def _record():
    return {
        "character": "X",
        "on": [],
        "kun": [],
        "senses": [],
        "keyword": None,
        "frequency_rank": None,
        "grade": None,
        "jlpt": None,
        "stroke_count": None,
        "examples": [],
        "global_words": [],
        "reading_entry_counts": [
            {"reading": "a", "count": 60, "reading_class": "On"},
            {"reading": "b", "count": 30, "reading_class": "Kun"},
            {"reading": "c", "count": 5, "reading_class": "Other"},
            {"reading": "d", "count": 3, "reading_class": "Other"},
            {"reading": "e", "count": 2, "reading_class": "Other"},
        ],
        "reading_frequency_scores": [
            {"reading": "a", "score": 20.0, "reading_class": "On"},
            {"reading": "b", "score": 1.0, "reading_class": "Kun"},
            {"reading": "c", "score": 50.0, "reading_class": "Other"},
            {"reading": "d", "score": 20.0, "reading_class": "Other"},
            {"reading": "e", "score": 9.0, "reading_class": "Other"},
        ],
    }


def test_frequency_distribution_keeps_its_four_largest_readings():
    distribution = bk.reading_frequency_distribution(_record())

    assert [segment["reading"] for segment in distribution["segments"]] == ["c", "a", "d", "e", ""]
    assert [segment["percent"] for segment in distribution["segments"]] == [50, 20, 20, 9, 1]
    assert sum(segment["percent"] for segment in distribution["segments"]) == 100
    assert distribution["collapsed"] is True


def test_frequency_node_has_one_pie_and_one_simple_legend():
    node = bk.build_reading_frequency_node(_record())
    blob = json.dumps(node, ensure_ascii=False)

    assert "Frequency weight" in blob
    assert "Word variety" not in blob
    assert "Reading mix" not in blob
    assert blob.count('"tag": "img"') == 1
    assert bk.reading_frequency_asset_name("X") in blob
    assert f"reading-distribution/{ord('X'):05x}.png" not in blob
    assert "c: 50%" in blob
    assert "a: 20%" in blob
    assert "Other: 1%" in blob
    assert "(On)" not in blob and "(Kun)" not in blob
    assert "corpus usage" not in blob.lower()


def test_frequency_pie_is_a_valid_packaged_png():
    png = bk.build_reading_frequency_png(_record())

    assert png is not None
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (bk.READING_CHART_SIZE, bk.READING_CHART_SIZE)
        assert image.format == "PNG"
        centre = image.convert("RGBA").getpixel(
            (bk.READING_CHART_SIZE // 2, bk.READING_CHART_SIZE // 2)
        )
        assert isinstance(centre, tuple) and centre[3] == 255


def test_normalization_ignores_untrusted_payload_frequency_scores():
    payload = {
        "character": "X",
        "meanings": ["example"],
        "onReadings": ["セイ"],
        "kunReadings": ["お"],
        "wordsByReading": [
            {"reading": "セイ", "totalWords": 2, "frequencyScore": 1.5, "words": []},
            {"reading": "せい", "totalWords": 1, "frequencyScore": 0.5, "words": []},
            {"reading": "お", "totalWords": 1, "frequencyScore": True, "words": []},
            {"reading": "う", "totalWords": 1, "frequencyScore": float("inf"), "words": []},
        ],
    }

    record = bk.normalize_record(payload)

    assert record["reading_frequency_scores"] == []


def test_run_build_without_bulk_rank_source_bundles_no_reading_pie(tmp_path):
    payload = {
        "character": "X",
        "meanings": ["example"],
        "onReadings": ["エックス"],
        "kunReadings": [],
        "wordsByReading": [
            {"reading": "え", "totalWords": 3, "frequencyScore": 1.0, "words": []},
            {"reading": "くす", "totalWords": 1, "frequencyScore": 3.0, "words": []},
        ],
    }
    result = bk.run_build(
        ["X"],
        str(tmp_path / "cache"),
        "2026-08-19",
        fetcher=lambda _character: payload,
    )

    with zipfile.ZipFile(io.BytesIO(result["zip_bytes"])) as archive:
        names = set(archive.namelist())
    assert f"reading-distribution/{ord('X'):05x}.png" not in names
    assert bk.reading_frequency_asset_name("X") not in names


def test_styles_keep_the_single_frequency_pie_compact_and_responsive():
    css = bk.STYLES_CSS

    assert '[data-sc-bee-role="reading-donut"]' in css
    assert '[data-sc-bee-role="donut-caption"]' in css
    assert '[data-sc-bee-role="reading-pie"]' in css
    assert '[data-sc-bee-role="donut-legend"]' in css
    assert "max-width: 4.25em" in css
    assert '[data-sc-bee-role="reading-mix"]' not in css
    assert '[data-sc-bee-role="reading-bar-segment"]' not in css
    assert "data-sc-bee-share" not in css
    assert '[data-sc-bee-role="reading-donut"] { justify-content: center; }' not in css
    assert "flex-basis: 100%; text-align: center" not in css
    assert '[data-sc-bee-role="reading-donut"] > [data-sc-bee-role="donut-legend"] { flex-basis: 100%; }' not in css


def test_missing_frequency_data_omits_the_chart_instead_of_falling_back():
    record = _record()
    record["reading_frequency_scores"] = []

    node = bk.build_reading_frequency_node(record)
    assert node is None


def _payload(character, reading):
    return {
        "character": character,
        "meanings": ["example"],
        "onReadings": [reading],
        "kunReadings": [],
        "frequencyRank": None,
        "wordsByReading": [
            {
                "reading": reading,
                "totalWords": 1,
                "words": [
                    {
                        "wordId": ord(character),
                        "readingIndex": 0,
                        "reading": character,
                        "readingFurigana": f"{character}[{reading}]",
                        "mainDefinition": "example",
                        "frequencyRank": 100,
                    }
                ],
            }
        ],
        "topWords": [],
    }


def test_parse_global_frequency_csv_is_strict_and_deduplicates_rows():
    text = (
        "Word,Form,Rank\n"
        "甲,こう,100\n甲,こう,100\n"
        "乙,おつ,400\n"
        "丙,へい,500\n丙,へい,600\n"
    )

    assert bk.parse_jiten_frequency_csv(text) == [
        ("甲", "こう", 100),
        ("乙", "おつ", 400),
    ]
    rows, stats = bk.parse_jiten_frequency_csv_with_stats(text)
    assert rows == [("甲", "こう", 100), ("乙", "おつ", 400)]
    assert stats == {
        "sourceRows": 5,
        "exactDuplicateRows": 1,
        "conflictingSurfaceReadingPairs": 1,
        "excludedConflictingRows": 2,
        "rows": 2,
    }

    with pytest.raises(bk.MalformedPayload):
        bk.parse_jiten_frequency_csv("Surface,Reading,Rank\n甲,こう,100\n")
    with pytest.raises(bk.MalformedPayload):
        bk.parse_jiten_frequency_csv("Word,Form,Rank\n甲,こう,not-a-rank\n")
    for malformed_rank in ("+1", "1_0", "١", "１", "01", " 1"):
        with pytest.raises(bk.MalformedPayload):
            bk.parse_jiten_frequency_csv(
                f"Word,Form,Rank\n甲,こう,{malformed_rank}\n"
            )


def test_parse_global_frequency_csv_resolves_duplicates_after_canonicalization():
    text = (
        "Word,Form,Rank\n"
        "発,はつ,100\n"
        "発,ハツ,200\n"
        "Ａ,エー,300\n"
        "A,えー,300\n"
        "乙,おつ,400\n"
    )
    rows, stats = bk.parse_jiten_frequency_csv_with_stats(text)
    assert rows == [("A", "えー", 300), ("乙", "おつ", 400)]
    assert stats == {
        "sourceRows": 5,
        "exactDuplicateRows": 1,
        "conflictingSurfaceReadingPairs": 1,
        "excludedConflictingRows": 2,
        "rows": 2,
    }


def test_frequency_form_alignment_requires_one_complete_segmentation():
    options = {
        "甲": ("こう", "こ"),
        "乙": ("おつ",),
    }

    assert bk.align_frequency_form("甲乙", "こうおつ", options) == (
        (0, "甲", "こう"),
        (1, "乙", "おつ"),
    )
    assert bk.align_frequency_form("甲乙", "かんおつ", options) is None

    ambiguous = {
        "甲": ("こう", "こ"),
        "乙": ("あ", "うあ"),
    }
    assert bk.align_frequency_form("甲乙", "こうあ", ambiguous) is None
    assert bk.align_frequency_form("生々", "ナマナマ", {"生": ("なま",)}) == (
        (0, "生", "なま"),
        (1, "生", "なま"),
    )
    # Unknown Han characters from every current range are never literal anchors.
    for unknown_han in (
        "乙",
        "〇",
        "⺀",
        "⼀",
        "〡",
        "〸",
        "㇀",
        "\U00016fe2",
        "\U00016ff0",
        "\U0002ebf0",
        "\U0002ee5f",
        "\U0002f800",
        "\U000323b0",
        "\U0003347f",
    ):
        assert bk.align_frequency_form(
            "甲" + unknown_han,
            "こう" + unknown_han,
            {"甲": ("こう",)},
        ) is None


def test_compounds_use_kanjidic_stems_not_curated_single_kanji_labels():
    groups = {"桐": ("きり",), "生": ("せい", "い", "いく")}
    compositional = {"桐": ("きり",), "生": ("せい", "い")}

    assert bk.align_frequency_form(
        "桐生", "きりいく", groups, compositional
    ) is None
    assert bk.align_frequency_form("生", "いく", groups, compositional) == (
        (0, "生", "いく"),
    )


def test_rank_weight_is_finite_bounded_and_tail_penalized():
    assert bk.rank_frequency_weight(100) == pytest.approx(0.1)
    assert bk.rank_frequency_weight(400) == pytest.approx(0.05)
    assert bk.rank_frequency_weight(200_000) == pytest.approx(
        (1.0 / math.sqrt(200_000)) * 0.25
    )
    assert bk.rank_frequency_weight(0) == 0.0
    assert bk.rank_frequency_weight(True) == 0.0
    assert bk.rank_frequency_weight(10 ** 10_000) == 0.0


def test_bulk_rows_produce_scores_from_production_shaped_payloads():
    payloads = {
        "甲": _payload("甲", "こう"),
        "乙": _payload("乙", "おつ"),
    }
    rows = [
        ("甲", "こう", 100),
        ("甲乙", "こうおつ", 400),
    ]

    scores = bk.calculate_reading_frequency_scores(payloads, rows)

    assert scores["甲"] == [
        {
            "reading": "こう",
            "score": pytest.approx(0.15),
            "reading_class": "On",
        }
    ]
    assert scores["乙"] == [
        {
            "reading": "おつ",
            "score": pytest.approx(0.05),
            "reading_class": "On",
        }
    ]


def test_scheduled_build_quality_floor_rejects_partial_alignment():
    healthy = {
        "sourceRows": 449_397,
        "exactDuplicateRows": 1,
        "conflictingSurfaceReadingPairs": 1,
        "excludedConflictingRows": 2,
        "rows": 449_394,
        "relevantRows": 293_000,
        "alignedRows": 259_000,
        "ambiguousOrUnalignedRows": 34_000,
        "readingAssignments": 500_000,
        "relevantRankWeight": 1000.0,
        "alignedRankWeight": 940.0,
        "rankWeightCoverage": 0.94,
        "charactersWithScores": 3_846,
        "readingGroupsWithScores": 4_200,
    }
    bk.validate_jiten_frequency_coverage(healthy)

    for key, value in (
        ("rows", 10),
        ("relevantRows", 10),
        ("alignedRows", 10),
        ("rankWeightCoverage", 0.2),
        ("charactersWithScores", 10),
    ):
        partial = dict(healthy)
        partial[key] = value
        with pytest.raises(bk.MalformedPayload):
            bk.validate_jiten_frequency_coverage(partial)

    impossible = [
        {**healthy, "alignedRows": healthy["relevantRows"] + 1},
        {**healthy, "relevantRows": healthy["rows"] + 1},
        {**healthy, "rankWeightCoverage": 1.01},
        {**healthy, "rankWeightCoverage": float("nan")},
        {**healthy, "alignedRankWeight": 1001.0},
        {**healthy, "ambiguousOrUnalignedRows": 1},
        {**healthy, "sourceRows": 0},
        {**healthy, "exactDuplicateRows": -1},
        {**healthy, "conflictingSurfaceReadingPairs": -1},
        {**healthy, "excludedConflictingRows": -1},
        {**healthy, "sourceRows": healthy["sourceRows"] + 1},
        {**healthy, "charactersWithScores": healthy["readingAssignments"] + 1},
        {**healthy, "readingGroupsWithScores": -1},
        {**healthy, "readingGroupsWithScores": healthy["readingAssignments"] + 1},
        {**healthy, "relevantRankWeight": healthy["relevantRows"] + 1.0},
        {**healthy, "alignedRankWeight": healthy["alignedRows"] + 1.0},
    ]
    for stats in impossible:
        with pytest.raises(bk.MalformedPayload):
            bk.validate_jiten_frequency_coverage(stats)


def test_global_frequency_source_is_cached_once_per_utc_day(tmp_path):
    calls = []
    source = b"Word,Form,Rank\r\n\xe7\x94\xb2,\xe3\x81\x93\xe3\x81\x86,100\r\n"

    def fetcher():
        calls.append(True)
        return source

    first = bk.fetch_jiten_frequency_csv_source(tmp_path, "2026-08-19", fetcher)
    second = bk.fetch_jiten_frequency_csv_source(tmp_path, "2026-08-19", fetcher)

    assert first == second == source.decode("utf-8")
    assert calls == [True]
    assert bk.jiten_frequency_csv_digest(tmp_path, "2026-08-19") == hashlib.sha256(
        source
    ).hexdigest()


def test_corrupt_frequency_cache_refetches_online_and_fails_offline(tmp_path):
    day = tmp_path / "2026-08-19"
    day.mkdir()
    path = day / "jiten_freq_global.csv"
    path.write_text("<html>bad cache</html>", encoding="utf-8")
    valid = b"Word,Form,Rank\n\xe7\x94\xb2,\xe3\x81\x93\xe3\x81\x86,100\n"
    calls = []

    result = bk.fetch_jiten_frequency_csv_source(
        tmp_path,
        "2026-08-19",
        lambda: calls.append(True) or valid,
    )
    assert result == valid.decode("utf-8")
    assert calls == [True]
    assert path.read_bytes() == valid

    path.write_text("not csv", encoding="utf-8")
    with pytest.raises(bk.MalformedPayload):
        bk.fetch_jiten_frequency_csv_source(
            tmp_path,
            "2026-08-19",
            lambda: pytest.fail("offline mode must not fetch"),
            offline=True,
        )


def test_frequency_source_cache_is_byte_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(bk, "MAX_JITEN_FREQUENCY_BYTES", 32)
    day = tmp_path / "2026-08-19"
    day.mkdir()
    (day / "jiten_freq_global.csv").write_bytes(b"x" * 33)

    with pytest.raises(bk.MalformedPayload, match="cached Jiten frequency CSV is invalid"):
        bk.fetch_jiten_frequency_csv_source(
            tmp_path,
            "2026-08-19",
            lambda: pytest.fail("offline mode must not fetch"),
            offline=True,
        )


def test_failed_production_quality_gate_quarantines_daily_frequency_cache(tmp_path):
    payloads = {"甲": _payload("甲", "こう")}
    frequency_cache = tmp_path / "frequency-cache"

    with pytest.raises(bk.MalformedPayload):
        bk.run_build(
            ["甲"],
            str(tmp_path / "cache"),
            "2026-08-19",
            fetcher=lambda character: payloads[character],
            frequency_cache_dir=str(frequency_cache),
            frequency_fetcher=lambda: "Word,Form,Rank\n甲,こう,100\n",
            enforce_frequency_quality=True,
        )

    assert not (frequency_cache / "2026-08-19" / "jiten_freq_global.csv").exists()


def test_run_build_carries_the_exact_scored_frequency_snapshot(tmp_path):
    raw = b"Word,Form,Rank\r\n\xe7\x94\xb2,\xe3\x81\x93\xe3\x81\x86,100\r\n"
    result = bk.run_build(
        ["甲"],
        str(tmp_path / "cache"),
        "2026-08-19",
        fetcher=lambda _character: _payload("甲", "こう"),
        frequency_cache_dir=str(tmp_path / "frequency-cache"),
        frequency_fetcher=lambda: raw,
    )
    snapshot = result["frequency_source"]
    assert snapshot.raw == raw
    assert snapshot.sha256 == hashlib.sha256(raw).hexdigest()
    assert result["frequency_stats"]["sha256"] == snapshot.sha256


def test_real_build_path_uses_bulk_source_and_never_packages_entry_count_pie(tmp_path):
    payloads = {
        "甲": _payload("甲", "こう"),
        "乙": _payload("乙", "おつ"),
        "丙": _payload("丙", "へい"),
    }
    csv_text = "Word,Form,Rank\n甲,こう,100\n乙,おつ,400\n"

    result = bk.run_build(
        ["甲", "乙", "丙"],
        str(tmp_path / "cache"),
        "2026-08-19",
        fetcher=lambda character: payloads[character],
        frequency_cache_dir=str(tmp_path / "frequency-cache"),
        frequency_fetcher=lambda: csv_text,
    )

    with zipfile.ZipFile(io.BytesIO(result["zip_bytes"])) as archive:
        names = set(archive.namelist())
        term_bank = json.loads(archive.read("term_bank_1.json"))
    assert bk.reading_frequency_asset_name("甲") in names
    assert bk.reading_frequency_asset_name("乙") in names
    assert f"reading-distribution/{ord('甲'):05x}.png" not in names
    assert f"reading-distribution/{ord('乙'):05x}.png" not in names
    assert bk.reading_frequency_asset_name("丙") not in names
    assert f"reading-distribution/{ord('丙'):05x}.png" not in names
    assert "Frequency weight" in json.dumps(term_bank, ensure_ascii=False)
    assert "reading-distribution/" not in json.dumps(term_bank, ensure_ascii=False)


def test_payload_scores_are_never_a_bulk_frequency_substitute():
    payload = _payload("甲", "こう")
    payload["wordsByReading"] = [
        {"reading": "こう", "totalWords": 1, "frequencyScore": 10 ** 10_000, "words": []},
        {"reading": "こう", "totalWords": 1, "frequencyScore": 1e308, "words": []},
        {"reading": "コウ", "totalWords": 1, "frequencyScore": 1e308, "words": []},
    ]

    assert bk.normalize_record(payload)["reading_frequency_scores"] == []
