"""Regression contract for the visible, compact Frequency weight pie chart."""
import io
import json
import pathlib

from PIL import Image

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


def test_frequency_weight_contains_packaged_pie_with_compact_host_geometry():
    record = rec("生.json")
    node = bk.build_reading_frequency_node(record)
    image = next(n for n in _walk(node) if n.get("tag") == "img")
    assert image["path"] == bk.reading_frequency_asset_name("生")
    assert image["collapsed"] is False
    assert image["collapsible"] is False

    css = bk.STYLES_CSS
    wrapper_rule = css[css.index('[data-sc-bee-role="reading-pie"] .gloss-image-container'):]
    wrapper_rule = wrapper_rule[:wrapper_rule.index("}") + 1]
    assert "width: 4.25em" in wrapper_rule
    assert "max-width: 4.25em" in wrapper_rule


def test_generated_chart_is_a_filled_pie_not_a_donut():
    png = bk.build_reading_frequency_png(rec("場.json"))
    image = Image.open(io.BytesIO(png)).convert("RGBA")
    assert image.size == (128, 128)
    center = image.getpixel((image.width // 2, image.height // 2))
    assert center[3] == 255, "pie centre must be filled; a transparent centre is a donut"
    assert center[:3] != (255, 255, 255), "pie centre must be a data segment, not a white hole"


def test_visible_text_legend_remains_the_accessible_source_of_truth():
    record = rec("場.json")
    node = bk.build_reading_frequency_node(record)
    blob = json.dumps(node, ensure_ascii=False)
    assert "Frequency weight" in blob
    assert "Rank-derived frequency weight" in blob
    for segment in bk.reading_frequency_distribution(record)["segments"]:
        label = segment["reading"] or "Other"
        assert f"{label}: {segment['percent']}%" in blob
    assert "2,904 entries" not in blob
    assert "2,083 entries" not in blob
