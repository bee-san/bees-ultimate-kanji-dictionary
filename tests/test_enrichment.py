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


def test_fetch_kanjivg_negatively_caches_daily_404(tmp_path):
    calls = []

    def missing(ch):
        calls.append(ch)
        raise bk.NotFound(ch)

    assert bk.fetch_kanjivg_all(["\u9ad9"], str(tmp_path), "2026-08-16", missing) == {}
    assert bk.fetch_kanjivg_all(["\u9ad9"], str(tmp_path), "2026-08-16", missing) == {}
    assert calls == ["\u9ad9"]


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


def test_styles_css_has_accessibility_and_reading_distribution_rules():
    css = bk.STYLES_CSS
    assert "prefers-reduced-motion" in css
    assert "data-sc-bee-role" in css       # scopes to our structured content
    assert "reading-distribution" in css.lower()
    assert "donut" not in css.lower()      # no chart/graphic CSS survives


def test_styles_css_has_no_hover_zoom_on_stroke_image():
    """Package contract: NO hover-to-scale zoom on dictionary stroke images.

    CSS hover-only image zoom is a GSM Hoshidicts feature, not a Bee's Ultimate
    Kanji Dictionary one. The emitted STYLES_CSS must contain none of the
    hover-zoom machinery added by commit 3c2f5d3:
      - no `@media (hover: hover)` stroke-image hover scale block,
      - no `scale(1.6)` (or any hover scale-up transform),
      - no `transform` transition / `transform-origin` added solely for zoom,
      - no position/z-index added solely to lift the enlarged image.

    Legitimate, unrelated behavior MUST be preserved: bounded stroke-image
    sizing, KanjiVG stroke animation, progressive disclosure, accessibility
    text, static fallback, and prefers-reduced-motion animation disabling.
    """
    css = bk.STYLES_CSS

    # --- the hover-zoom must be gone -------------------------------------
    assert "@media (hover: hover)" not in css
    assert '[data-sc-bee-role="stroke-image"]:hover' not in css
    assert "scale(1.6)" not in css
    assert "transform: scale(" not in css
    assert "transition: transform" not in css
    assert "transform-origin" not in css

    # No positioning/z-index bolted onto the stroke image solely for the zoom.
    # (Yomitan discards the <img> data attribute, so the stroke image is bounded
    # via the preserved .gloss-image-container wrapper under our stroke-order div.)
    si_block = css[css.index('[data-sc-bee-role="stroke-order"] .gloss-image-container {'):]
    si_block = si_block[: si_block.index("}") + 1]
    assert "position:" not in si_block
    assert "z-index" not in si_block

    # --- legitimate behavior preserved -----------------------------------
    # bounded stroke-image sizing stays
    assert "max-width: 6em" in si_block
    assert "max-height: 6em" in si_block
    # prefers-reduced-motion still disables bundled animation
    rm_block = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    assert '[data-sc-bee-role="stroke-order"] .gloss-image' in rm_block
    assert "animation: none" in rm_block


def test_built_zip_styles_css_has_no_hover_zoom():
    """The public package (built ZIP) must ship no hover-scale CSS.

    Extracts styles.css from a freshly built canonical ZIP and asserts the
    hover-zoom machinery is absent from what actually reaches users.
    """
    import io
    import zipfile

    b = {"term_bank": [], "term_meta_bank": [], "kanji_bank": [], "kanji_meta_bank": []}
    raw = bk.build_zip(b, revision="2026.08.16")
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        css = zf.read("styles.css").decode("utf-8")

    assert "@media (hover: hover)" not in css
    assert '[data-sc-bee-role="stroke-image"]:hover' not in css
    assert "scale(1.6)" not in css
    assert "transform: scale(" not in css
    assert "transition: transform" not in css
    assert "transform-origin" not in css
