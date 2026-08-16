"""RED tests: KanjiVG acquisition orchestration + enrichment assembly + ZIP.

The KanjiVG acquisition reuses the same simple resumable dated-cache pattern as
the Jiten fetch (no new machinery). Enrichment assembly produces a deterministic
map of stroke info, a phonetic-family index, and the exact set of sanitized SVG
assets actually referenced. The canonical ZIP bundles styles.css, the KanjiVG
licence/attribution, and only the needed SVG assets -- still one ZIP, still
byte-deterministic.
"""
import io
import json
import zipfile

import bees_kanji as bk


def _fake_kvg(character):
    cp = ord(character)
    # two chars share phonetic 寺; one has none.
    phon = {"\u6642": "\u5bfa", "\u6301": "\u5bfa"}.get(character)
    phon_attr = f' kvg:phon="{phon}"' if phon else ""
    return (
        '<svg xmlns:kvg="x">'
        f'<g kvg:element="{character}"{phon_attr}>'
        f'<path d="M1,1c1,1 2,2 3,3"/><path d="M5,5c1,1 2,2 3,3"/>'
        "</g></svg>"
    )


def test_fetch_kanjivg_uses_resumable_dated_cache(tmp_path):
    calls = []

    def fetcher(ch):
        calls.append(ch)
        return _fake_kvg(ch)

    chars = ["\u6642", "\u6301"]
    out1 = bk.fetch_kanjivg_all(chars, str(tmp_path), "2026-08-16", fetcher)
    assert set(out1) == set(chars)
    # second run: cache hit, zero new fetcher calls
    calls.clear()
    out2 = bk.fetch_kanjivg_all(chars, str(tmp_path), "2026-08-16", fetcher)
    assert calls == []
    assert out2 == out1


def test_assemble_enrichment_builds_strokes_phonetics_and_assets():
    svgs = {c: _fake_kvg(c) for c in ["\u6642", "\u6301", "\u751f"]}
    ranks = {"\u6642": 200, "\u6301": 400}
    enr = bk.assemble_enrichment(svgs, ranks)
    # stroke info per character
    assert enr["strokes"]["\u6642"]["stroke_count"] == 2
    # phonetic family: 時 and 持 share 寺
    fam = enr["families_by_char"]["\u6642"]
    assert "\u6301" in fam["members"]
    # only referenced SVG assets are produced, sanitized (no kvg:)
    assert "kanjivg/06642.svg" in enr["assets"]
    assert "kvg:" not in enr["assets"]["kanjivg/06642.svg"]


def test_zip_bundles_styles_kanjivg_license_and_assets():
    b = {"term_bank": [], "term_meta_bank": [], "kanji_bank": [], "kanji_meta_bank": []}
    assets = {"kanjivg/06642.svg": "<svg></svg>"}
    z = bk.build_zip(b, revision="2026.08.16", assets=assets)
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        names = set(zf.namelist())
    assert "styles.css" in names
    assert "LICENSE-kanjivg.txt" in names
    assert "kanjivg/06642.svg" in names
    # still exactly one canonical archive with the core banks at the root
    assert "index.json" in names and "term_bank_1.json" in names


def test_zip_without_assets_still_has_styles():
    b = {"term_bank": [], "term_meta_bank": [], "kanji_bank": [], "kanji_meta_bank": []}
    z = bk.build_zip(b, revision="2026.08.16")
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        names = set(zf.namelist())
    assert "styles.css" in names


def test_zip_deterministic_with_assets():
    b = {"term_bank": [], "term_meta_bank": [], "kanji_bank": [], "kanji_meta_bank": []}
    assets = {"kanjivg/06301.svg": "<svg>b</svg>", "kanjivg/06642.svg": "<svg>a</svg>"}
    z1 = bk.build_zip(b, revision="2026.08.16", assets=assets)
    z2 = bk.build_zip(b, revision="2026.08.16", assets=assets)
    assert z1 == z2  # member order fixed regardless of dict insertion order


def test_styles_css_has_accessibility_and_donut_rules():
    css = bk.STYLES_CSS
    assert "prefers-reduced-motion" in css
    assert "data-sc-bee-role" in css       # scopes to our structured content
    assert "donut" in css.lower()


def test_styles_css_hover_zoom_is_scoped_hover_only_and_reduced_motion_safe():
    """Dictionary-content images (stroke diagrams) get a CSS-only hover zoom.

    The effect must be scoped to our own stroke-image marker (never repo badges,
    README images, icons, or unrelated UI), only apply where hover is supported,
    scale up on hover, and drop the transition under prefers-reduced-motion.
    """
    css = bk.STYLES_CSS
    # a short transform transition on the dictionary-content image itself
    assert "transition: transform" in css
    # hover zoom lives behind an actual hover-capability query, scoped to us
    hover_block = css[css.index("@media (hover: hover)"):]
    assert '[data-sc-bee-role="stroke-image"]:hover' in hover_block
    assert "scale(" in hover_block
    assert "transform-origin" in css
    # reduced-motion disables the transform transition for our image
    rm_block = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    assert '[data-sc-bee-role="stroke-image"]' in rm_block
    assert "transition: none" in rm_block
