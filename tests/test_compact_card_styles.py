"""RED/GREEN: the compact card's responsive vocabulary grid + distribution CSS.

The six global words render in a two-column grid (3 left / 3 right) on ordinary
popups and collapse to a single column on narrow popups. The reading
distribution is now plain text, so all obsolete chart/donut graphic CSS (the
conic-gradient ring and the packaged-image wrappers) must be gone.
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


def test_reading_distribution_is_styled_as_a_text_list():
    # The distribution ships as a heading + plain <ul>; the CSS must style those
    # roles and NOT any chart/graphic wrapper.
    assert '[data-sc-bee-role="reading-distribution"]' in CSS
    assert '[data-sc-bee-role="reading-dist-caption"]' in CSS
    assert '[data-sc-bee-role="reading-dist-list"]' in CSS
    listing = _rule('[data-sc-bee-role="reading-dist-list"]')
    assert "list-style: none" in listing


def test_no_dead_image_or_graphic_selectors():
    # No selectors keyed on the removed image / graphic / swatch machinery may
    # linger -- they would be dead CSS for a card that no longer emits them.
    for dead in ('[data-sc-bee-role="reading-chart"]',
                 '[data-sc-bee-role="donut-graphic"]',
                 '[data-sc-bee-role="donut-legend"]',
                 '[data-sc-bee-role="donut-swatch"]',
                 '[data-sc-bee-role="donut-caption"]',
                 '[data-sc-bee-role="reading-donut"]'):
        assert dead not in CSS, f"dead selector still present: {dead}"


def test_dark_theme_still_present():
    assert "prefers-color-scheme: dark" in CSS
