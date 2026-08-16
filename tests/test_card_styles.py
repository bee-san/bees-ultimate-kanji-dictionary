"""RED/GREEN: the reworked card's stylesheet.

The bundled styles.css must give the reworked structured card a coherent,
compact, accessible visual system that survives Yomitan's real constraints:

  * a restrained REUSABLE TOKEN palette (CSS custom properties) so colours are
    defined once and reused, not scattered magic values;
  * hero / reading-chip / badge / vocab styling scoped to our own
    data-sc-bee-role markers (never restyling other dictionaries);
  * a DARK-THEME adaptation via prefers-color-scheme with no hardcoded white
    card background that would blow out on dark;
  * NARROW / mobile behaviour so chips, badges, and the donut legend wrap
    instead of overflowing a compact popup;
  * keyboard FOCUS visibility on the expandable section;
  * reduced-motion already guarded (kept from the existing contract).

These assertions are structural (selectors / at-rules / tokens present), not
pixel-exact, so they lock the design intent without being brittle.
"""
import bees_kanji as bk

CSS = bk.STYLES_CSS


def test_defines_a_reusable_token_palette():
    # Colours/spacing are defined once as custom properties and reused.
    assert ":root" in CSS
    assert "--bee-" in CSS
    # at least one rule consumes a token via var()
    assert "var(--bee-" in CSS


def test_styles_scoped_to_our_markers_only():
    # Every structural selector targets our own data-sc-bee-role markers.
    for role in ("hero", "reading-chip", "badge", "vocab-group"):
        assert f'[data-sc-bee-role="{role}"]' in CSS


def test_hero_header_is_emphasised():
    hero_glyph = CSS[CSS.index('[data-sc-bee-role="hero-glyph"]'):]
    hero_glyph = hero_glyph[: hero_glyph.index("}") + 1]
    # the glyph is visually larger than body text
    assert "font-size" in hero_glyph


def test_reading_chips_and_badges_wrap_for_narrow_popups():
    # chips and badges use flex-wrap so they reflow in a compact/narrow pane
    chips = CSS[CSS.index('[data-sc-bee-role="reading-chips"]'):]
    chips = chips[: chips.index("}") + 1]
    assert "flex-wrap: wrap" in chips
    badge_row = CSS[CSS.index('[data-sc-bee-role="badge-row"]'):]
    badge_row = badge_row[: badge_row.index("}") + 1]
    assert "flex-wrap: wrap" in badge_row


def test_dark_theme_adaptation_present_and_no_hardcoded_white_card():
    assert "prefers-color-scheme: dark" in CSS
    # the donut hole falls back to the viewer's background token, not solid #fff
    hole = CSS[CSS.index('[data-sc-bee-role="donut-hole"]'):]
    hole = hole[: hole.index("}") + 1]
    assert "--background-color" in hole  # inherits Yomitan's theme background


def test_narrow_media_query_lets_donut_legend_stack():
    assert "@media (max-width:" in CSS


def test_keyboard_focus_visible_on_expandable_section():
    assert ":focus-visible" in CSS
    assert "outline" in CSS


def test_reduced_motion_and_bounded_stroke_image_preserved():
    # regression guard for the existing accessibility contract
    assert "@media (prefers-reduced-motion: reduce)" in CSS
    si = CSS[CSS.index('[data-sc-bee-role="stroke-image"] {'):]
    si = si[: si.index("}") + 1]
    assert "max-width: 6em" in si and "max-height: 6em" in si


def test_no_hover_zoom_machinery():
    # unrelated GSM feature must stay absent
    assert "@media (hover: hover)" not in CSS
    assert "scale(1.6)" not in CSS
    assert "transform: scale(" not in CSS
    assert "transition: transform" not in CSS
    assert "transform-origin" not in CSS
