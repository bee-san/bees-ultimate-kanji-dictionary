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


def test_chart_image_is_bounded_and_scoped():
    # The base reading-chart rule bounds the packaged PNG with an explicit
    # width + max-width (the narrow media-query override only restacks it).
    assert '[data-sc-bee-role="reading-chart"]' in CSS
    assert "width: 4.6em" in CSS
    assert "max-width: 40%" in CSS


def test_dark_theme_still_present():
    assert "prefers-color-scheme: dark" in CSS
