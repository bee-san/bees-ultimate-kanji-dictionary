"""RED/GREEN: the reading-distribution chart must render LARGE and legible.

The v2026.08.17.2 release shipped a visually unacceptable chart: a tiny 128x128
raster constrained to ``width: 4.6em; max-width: 40%`` -- barely a thumbnail.

This module locks the *large chart* contract deterministically, independent of a
running browser:

  * the img structured-content node declares a prominent size (~13-16rem), so a
    renderer that honours the node size draws it large;
  * the PRESERVED Yomitan wrapper (``a.gloss-image-link >
    span.gloss-image-container > canvas.gloss-image``) -- which is the channel
    real Yomitan actually sizes the image through -- is bounded to a prominent
    ~13-16rem on ordinary popups with a sensible narrow-popup cap so it never
    overflows a compact pane;
  * the chart occupies its OWN centred row with the legend placed cleanly below
    (not crammed beside it as a tiny inline thumbnail).
"""
import json
import pathlib
import re

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"
CSS = bk.STYLES_CSS


def rec(name):
    return bk.normalize_record(json.loads((FIX / name).read_text(encoding="utf-8")))


def _rule(selector):
    """Return the full top-level `selector { ... }` block text.

    Matches the base rule (selector at column 0), never a copy nested inside a
    ``@media`` block (which is indented), so geometry assertions read the
    ordinary-popup rule deterministically regardless of source ordering.
    """
    m = re.search(r"(?m)^" + re.escape(selector) + r"[^{]*\{[^}]*\}", CSS)
    assert m, f"no top-level rule for {selector!r}"
    return m.group(0)


def _lengths(block, prop):
    """All numeric values (with unit) for a CSS property in a declaration block."""
    out = []
    for m in re.finditer(rf"{re.escape(prop)}\s*:\s*([^;}}]+)", block):
        for tok in re.findall(r"(\d+(?:\.\d+)?)\s*(rem|em|px|%)", m.group(1)):
            out.append((float(tok[0]), tok[1]))
    return out


def _walk(node):
    if isinstance(node, dict):
        yield node
        yield from _walk(node.get("content"))
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _chart_img(char):
    node = bk.build_reading_chart_node(rec(f"{char}.json"))
    imgs = [n for n in _walk(node) if n.get("tag") == "img"]
    assert len(imgs) == 1, f"{char}: expected exactly one chart img, got {len(imgs)}"
    return imgs[0]


# --- 1. the img structured-content node declares a prominent size -------------

def test_chart_img_node_declares_a_large_em_size():
    for char in ("生", "場"):
        img = _chart_img(char)
        assert img.get("sizeUnits") == "em", f"{char}: chart size should be in em"
        w = float(img.get("width"))
        h = float(img.get("height"))
        assert w == h, f"{char}: chart img must stay square, got {w}x{h}"
        assert 13.0 <= w <= 16.0, (
            f"{char}: chart img node must be a prominent ~13-16em chart, got {w}em"
        )


# --- 2. the PRESERVED Yomitan wrapper is sized large with a narrow cap ---------

def test_preserved_container_is_prominent_on_ordinary_popups():
    # Real Yomitan sizes the rendered image through the preserved
    # .gloss-image-container wrapper. It must be a prominent ~13-16rem chart, not
    # the old 4.6em thumbnail.
    block = _rule('[data-sc-bee-role="donut-graphic"] .gloss-image-container')
    widths = _lengths(block, "width")
    assert widths, "chart container must set an explicit width"
    val, unit = widths[0]
    assert unit in ("rem", "em"), f"width should be a font-relative unit, got {unit}"
    assert 13.0 <= val <= 16.0, (
        f"chart container width must be a prominent ~13-16{unit}, got {val}{unit}"
    )
    # It must NOT be capped to a tiny fraction of the popup (the old max-width:40%
    # squashed it). A percentage cap, if present, must be generous.
    for pct_val, pct_unit in _lengths(block, "max-width"):
        if pct_unit == "%":
            assert pct_val >= 90.0, (
                f"a percentage max-width must not squash the chart, got {pct_val}%"
            )


def test_narrow_popup_has_a_sensible_chart_cap():
    # On a narrow popup the chart must be capped so it never overflows the pane,
    # while staying meaningfully large.
    assert "@media (max-width:" in CSS
    idx = CSS.index("@media (max-width:")
    narrow = CSS[idx:]
    assert '[data-sc-bee-role="donut-graphic"]' in narrow, (
        "narrow-popup media query must cap the chart graphic"
    )
    # somewhere under the narrow query the chart container is capped by max-width
    block_start = narrow.index('[data-sc-bee-role="donut-graphic"]')
    narrow_block = narrow[block_start: block_start + 400]
    caps = _lengths(narrow_block, "max-width")
    assert caps, "narrow-popup chart must set a max-width cap"
    val, unit = caps[0]
    if unit == "%":
        assert val <= 100.0
    else:
        assert 6.0 <= val <= 16.0, f"narrow cap out of sensible range: {val}{unit}"


def test_chart_is_no_longer_the_old_tiny_thumbnail():
    # Guard against regressing to the shipped-broken 4.6em / max-width:40% chart.
    block = _rule('[data-sc-bee-role="donut-graphic"] .gloss-image-container')
    assert "4.6em" not in block, "chart must not regress to the tiny 4.6em thumbnail"
    assert "max-width: 40%" not in block, "chart must not regress to the 40% squash cap"


# --- 3. chart gets its own centred row; legend placed cleanly below -----------

def test_chart_graphic_is_a_centred_block_row_not_an_inline_thumbnail():
    block = _rule('[data-sc-bee-role="donut-graphic"]')
    assert "display: inline-block" not in block, (
        "chart must not be an inline thumbnail crammed beside the legend"
    )
    assert "display: block" in block or "display: flex" in block, (
        "chart graphic must occupy its own row (block/flex)"
    )
    assert "margin" in block and "auto" in block, (
        "chart graphic must be horizontally centred (margin auto)"
    )


def test_legend_sits_below_the_chart_as_a_block_not_inline_beside_it():
    block = _rule('[data-sc-bee-role="donut-legend"]')
    assert "display: inline-block" not in block, (
        "legend must not be inline beside a tiny chart -- it sits below the chart"
    )
