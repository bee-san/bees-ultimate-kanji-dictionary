"""RED tests: KanjiVG stroke/component enrichment and sanitization.

Only license-compatible KanjiVG data is used. Parsing yields deterministic
component and stroke information. Sanitization strips scripts, external refs,
DOCTYPE, comments, and kvg namespaced attributes, and rebuilds a motion-free,
high-contrast diagram with sanitized stroke numbers. The structured-content
node references the bundled SVG via img with alt text plus a text
component/stroke line so nothing
depends on media, script, SVG, or a character asset being available.
"""
import bees_kanji as bk

# A compact but realistic KanjiVG SVG (生, 5 strokes, one element).
SEI_SVG = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<!-- Copyright (C) 2009 Ulrich Apel. CC BY-SA 3.0 -->\n'
    '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.0//EN" "http://www.w3.org/x.dtd" [\n'
    '<!ATTLIST g kvg:element CDATA #IMPLIED >\n]>\n'
    '<svg xmlns="http://www.w3.org/2000/svg" width="109" height="109" '
    'viewBox="0 0 109 109" xmlns:kvg="https://kanjivg.tagaini.net/">\n'
    '<g id="kvg:StrokePaths_0751f" style="fill:none;stroke:#000000;stroke-width:3">\n'
    '<g id="kvg:0751f" kvg:element="\u751f" kvg:radical="general">\n'
    '<path id="kvg:0751f-s1" kvg:type="x" d="M31,25c0.3,1.3-0.05,3.7-14,24"/>\n'
    '<path id="kvg:0751f-s2" kvg:type="x" d="M31,40c2.3,0.3 35-5 44-6"/>\n'
    '<path id="kvg:0751f-s3" kvg:type="x" d="M52,12c1.2,1.2 2,69 2,75"/>\n'
    '<path id="kvg:0751f-s4" kvg:type="x" d="M29,64c2.6,0.6 42-3 50-4"/>\n'
    '<path id="kvg:0751f-s5" kvg:type="x" d="M15,90c3,0.7 68-4 78-4"/>\n'
    '</g></g>\n'
    '<g id="kvg:StrokeNumbers_0751f" style="font-size:8">\n'
    '<text transform="matrix(1 0 0 1 20.00 20.00)">1</text>\n'
    '<text transform="matrix(1 0 0 1 25.50 35.25)">2</text>\n'
    '</g>\n'
    '</svg>\n'
)


def test_kanjivg_downloads_use_an_immutable_source_revision():
    assert "/61e39cfc29724132a6f8823b166296932985a0ff/kanji" in bk.KANJIVG_BASE


def test_parse_stroke_count_and_components():
    info = bk.parse_kanjivg(SEI_SVG, "\u751f")
    assert info["stroke_count"] == 5             # five <path> stroke elements
    assert "\u751f" in info["components"]         # the character element itself


def test_parse_components_deterministic():
    a = bk.parse_kanjivg(SEI_SVG, "\u751f")
    b = bk.parse_kanjivg(SEI_SVG, "\u751f")
    assert a == b


def test_sanitize_strips_unsafe_and_namespaced_content():
    out = bk.sanitize_kanjivg_svg(SEI_SVG, "\u751f")
    lowered = out.lower()
    assert "<script" not in lowered
    assert "<!doctype" not in lowered
    assert "<!--" not in out               # comments stripped
    assert "kvg:" not in out               # kvg namespaced attrs stripped
    assert "xlink" not in lowered          # no external refs
    assert "<image" not in lowered
    assert "on" + "load" not in lowered    # no event handlers
    # still a valid-looking svg carrying the stroke paths
    assert out.startswith("<svg")
    assert out.count("<path") == 10


def test_sanitize_uses_a_static_diagram_safe_for_reduced_motion_and_canvas_snapshots():
    out = bk.sanitize_kanjivg_svg(SEI_SVG, "\u751f")
    assert "@keyframes" not in out
    assert "animation" not in out
    assert "stroke-dash" not in out


def test_sanitized_strokes_have_a_static_base_for_yomitan_canvas_snapshots():
    out = bk.sanitize_kanjivg_svg(SEI_SVG, "\u751f")
    assert out.count('class="bee-stroke-outline"') == 5
    assert out.count('class="bee-stroke-ink"') == 5


def test_sanitized_static_diagram_retains_safe_stroke_order_numbers():
    out = bk.sanitize_kanjivg_svg(SEI_SVG, "\u751f")
    assert out.count('class="bee-stroke-number"') == 2
    assert '<text class="bee-stroke-number" x="20.00" y="20.00"' in out
    assert 'paint-order="stroke"' in out


def test_sanitized_strokes_remain_legible_in_dark_embedded_images():
    out = bk.sanitize_kanjivg_svg(SEI_SVG, "\u751f")
    assert 'stroke="#ffffff" stroke-width="5"' in out
    assert 'stroke="#0072b2" stroke-width="3"' in out
    assert "prefers-color-scheme" not in out


def test_sanitize_deterministic_bytes():
    assert bk.sanitize_kanjivg_svg(SEI_SVG, "\u751f") == bk.sanitize_kanjivg_svg(SEI_SVG, "\u751f")


def test_stroke_node_references_asset_with_text_fallback():
    info = {"stroke_count": 5, "components": ["\u751f"], "asset": "kanjivg/0751f.svg"}
    node = bk.build_stroke_node("\u751f", info)
    import json
    blob = json.dumps(node, ensure_ascii=False)
    # references the bundled SVG asset via an img node with a path
    assert "kanjivg/0751f.svg" in blob
    # alt / text fallback naming stroke count so info survives without the image
    assert "5" in blob
    assert "stroke" in blob.lower()


def test_stroke_node_preserves_baked_high_contrast_colours_in_yomitan():
    info = {"stroke_count": 5, "components": ["生"], "asset": "kanjivg/0751f.svg"}
    node = bk.build_stroke_node("生", info)
    image = node["content"][0]
    assert image["tag"] == "img"
    assert "appearance" not in image


def test_stroke_node_text_only_when_no_asset():
    # When no SVG asset is available, still show a text component/stroke line.
    info = {"stroke_count": 5, "components": ["\u751f"], "asset": None}
    node = bk.build_stroke_node("\u751f", info)
    import json
    blob = json.dumps(node, ensure_ascii=False)
    assert "img" not in blob            # no image node without an asset
    assert "5" in blob                  # stroke count text still present


def test_stroke_node_none_when_no_info():
    assert bk.build_stroke_node("\u751f", None) is None
