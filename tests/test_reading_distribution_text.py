"""RED/GREEN: the reading distribution ships as TEXT, never a raster/graphic.

The user's authoritative correction: keep the "Reading distribution" section and
its truthful percentage values, but remove the pie/donut graphic entirely. The
distribution must render as a concise heading followed by a clean textual list of
reading labels and percentages (exact counts retained compactly), with:

  * NO ``img`` node, NO packaged PNG, NO ``path`` reference to any media,
  * NO replacement chart of any kind (no SVG, canvas, conic-gradient, bar),
  * NO colour swatch graphic (colour must not be a channel at all now),
  * NO warning / disclaimer prose,
  * the same truthful percentage math and segment order as before.

The build must not import Pillow or generate any per-entry chart asset.
"""
import json
import pathlib

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


def rec(name):
    return bk.normalize_record(json.loads((FIX / name).read_text(encoding="utf-8")))


def _walk(node):
    if isinstance(node, dict):
        yield node
        yield from _walk(node.get("content"))
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _text(node):
    """Flatten all string content beneath a node."""
    out = []
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        out.extend(_text(node.get("content")))
    elif isinstance(node, list):
        for item in node:
            out.extend(_text(item))
    return "".join(out)


def _role(n):
    return n.get("data", {}).get("beeRole") if isinstance(n, dict) else None


# --- the section is textual, present, and titled ------------------------------

def test_distribution_node_present_and_titled():
    for char in ("場", "生"):
        node = bk.build_reading_distribution_node(rec(f"{char}.json"))
        assert node is not None, f"{char}: distribution section missing"
        blob = json.dumps(node, ensure_ascii=False)
        assert "Reading distribution" in blob


def test_distribution_node_has_no_image_or_media_reference():
    for char in ("場", "生"):
        node = bk.build_reading_distribution_node(rec(f"{char}.json"))
        # no img tag anywhere in the subtree
        assert not [n for n in _walk(node) if isinstance(n, dict) and n.get("tag") == "img"], \
            f"{char}: distribution must not contain an img node"
        blob = json.dumps(node, ensure_ascii=False)
        # no packaged media path, no raster/vector graphic machinery
        assert ".png" not in blob.lower(), f"{char}: no PNG reference allowed"
        assert "reading-distribution/" not in blob, f"{char}: no packaged asset path"
        assert '"path"' not in blob, f"{char}: no media path key allowed"
        assert '"tag": "img"' not in blob
        assert '"tag": "canvas"' not in blob
        assert '"tag": "svg"' not in blob
        assert "conic-gradient" not in blob


def test_distribution_node_is_a_text_list_of_labels_and_percents():
    node = bk.build_reading_distribution_node(rec("場.json"))
    blob = json.dumps(node, ensure_ascii=False)
    # a real textual list carries every reading label, its class, its percent
    # and its exact entry count, compactly.
    assert '"tag": "ul"' in blob
    assert "じょう" in blob and "(On)" in blob
    assert "ば" in blob and "(Kun)" in blob
    assert "58%" in blob and "42%" in blob
    assert "2,904" in blob or "2904" in blob
    assert "entries" in blob or "entry" in blob


def test_distribution_node_carries_no_colour_swatch_graphic():
    # colour was previously the sole non-text channel; with the graphic gone the
    # legend must be plain text -- no swatch element, no inline colour styling.
    for char in ("場", "生"):
        node = bk.build_reading_distribution_node(rec(f"{char}.json"))
        roles = [_role(n) for n in _walk(node)]
        assert "donut-swatch" not in roles, f"{char}: no colour swatch allowed"
        assert "donut-graphic" not in roles, f"{char}: no chart graphic wrapper"
        blob = json.dumps(node, ensure_ascii=False)
        # the filled-square swatch glyph must be gone
        assert "\u25a0" not in blob, f"{char}: swatch glyph must be removed"
        # no hex colours smuggled in via style
        assert "#0072b2" not in blob.lower()


def test_distribution_node_carries_no_disclaimer_or_warning():
    node = bk.build_reading_distribution_node(rec("生.json"))
    scan = json.dumps(node, ensure_ascii=False).lower()
    for banned in ("usage frequency", "token frequency", "corpus", "probability",
                   "most used", "pronunciation", "real-world frequency", "chance",
                   "warning", "disclaimer", "note:", "caution"):
        assert banned not in scan, banned


def test_distribution_percentages_and_order_unchanged():
    # The removal of the graphic must not disturb the truthful math or order.
    dist = bk.reading_distribution(rec("場.json"))
    pct = [(s["reading"], s["percent"]) for s in dist["segments"]]
    assert pct == [("じょう", 58), ("ば", 42), ("えき", 0)]
    assert sum(s["percent"] for s in dist["segments"]) == 100

    sei = bk.reading_distribution(rec("生.json"))
    assert sei["total"] == 3922
    assert sum(s["percent"] for s in sei["segments"]) == 100
    assert [s["reading"] for s in sei["segments"][:4]] == ["せい", "お", "う", "しょう"]


def test_distribution_node_absent_when_no_valid_totals():
    fallback = bk.kanjidic2_record(
        "々", {"meanings": ["repetition mark"], "on": [], "kun": [],
               "nanori": [], "stroke_count": 3, "grade": None, "jlpt": None},
    )
    assert bk.build_reading_distribution_node(fallback) is None


# --- no PNG generation machinery survives -------------------------------------

def test_png_generation_functions_are_gone():
    for name in ("build_reading_distribution_png", "reading_distribution_asset_name",
                 "reading_chart_alt_text", "build_reading_chart_node",
                 "_flatten_to_palette_png", "_hex_to_rgba", "READING_CHART_SIZE"):
        assert not hasattr(bk, name), f"{name} must be removed"


def test_module_source_has_no_pillow_import_or_png_machinery():
    src = pathlib.Path(bk.__file__).read_text(encoding="utf-8")
    assert "from PIL" not in src and "import PIL" not in src, "no Pillow import"
    assert "pieslice" not in src and "ImageDraw" not in src
    assert "reading-distribution/" not in src, "no packaged chart asset path"


def test_pillow_is_not_a_declared_runtime_dependency():
    root = pathlib.Path(bk.__file__).resolve().parent.parent
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8").lower()
    assert "pillow" not in pyproject, "Pillow must no longer be a dependency"
