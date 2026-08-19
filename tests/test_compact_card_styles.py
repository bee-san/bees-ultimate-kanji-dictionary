"""RED/GREEN: the compact card's responsive vocabulary grid + chart CSS.

The six global words render in a two-column grid (3 left / 3 right) on ordinary
popups and collapse to a single column on narrow popups. The reading chart is a
packaged raster image, so the obsolete conic-gradient ring CSS must be gone.
"""
import bees_kanji as bk

CSS = bk.STYLES_CSS


def _rule(selector):
    start = CSS.index(selector)
    return CSS[start: CSS.index("}", start) + 1]


def test_vocab_grid_is_two_column_by_default():
    grid = _rule('[data-sc-bee-role="vocab-grid"]')
    assert "display: grid" in grid
    assert "grid-template-columns: 1fr 1fr" in grid or "grid-template-columns: repeat(2" in grid


def test_narrow_popup_collapses_vocab_grid_to_one_column():
    assert "@media (max-width:" in CSS
    # somewhere under a max-width query the grid drops to a single column
    idx = CSS.index("@media (max-width:")
    narrow = CSS[idx:]
    assert "grid-template-columns: 1fr" in narrow


def test_obsolete_conic_gradient_ring_css_removed():
    assert "conic-gradient" not in CSS
    assert 'data-sc-bee-role="donut-ring"' not in CSS
    assert 'data-sc-bee-role="donut-hole"' not in CSS


def test_chart_image_is_bounded_via_preserved_wrappers():
    # Real Yomitan (structured-content-generator.js createDefinitionImage)
    # DISCARDS the data attributes on an <img> node: the chart image is rendered
    # as a.gloss-image-link > span.gloss-image-container > canvas.gloss-image,
    # and none of those carry data-sc-bee-role. The pie is therefore bounded by
    # targeting the preserved wrapper under our own retained reading-pie div.
    selector = '[data-sc-bee-role="reading-pie"] .gloss-image-container'
    assert selector in CSS, \
        "chart size must target the preserved .gloss-image-container wrapper"
    graphic = _rule(selector)
    assert "width: 4.25em" in graphic
    assert "max-width: 4.25em" in graphic


def test_no_dead_image_data_attribute_selectors():
    # We must NOT ship selectors on the <img>'s own data attribute -- Yomitan
    # discards those, so any rule keyed on the image data-sc marker is dead CSS.
    assert '[data-sc-bee-role="reading-chart"]' not in CSS


def test_dark_theme_still_present():
    assert "prefers-color-scheme: dark" in CSS
