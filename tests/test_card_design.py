"""RED/GREEN: the reworked polished structured card (not a wall of text).

The canonical term card must be a coherent, scannable design rather than a
stack of plain divs. These tests lock the structural contract of the reworked
``_detail_content`` so the layout carries clear hierarchy and every graphic has
a semantic text equivalent:

  * a strong kanji + keyword HERO header (data-sc-bee-role="hero"), with the
    glyph and the keyword distinguished
  * On and Kun readings rendered as separated, labelled reading CHIPS grouped by
    class (data-sc-bee-role="reading-group" / "reading-chip"), not one run-on
    line
  * a compact meaning line (data-sc-bee-role="meaning")
  * small aligned rank / grade / JLPT / stroke BADGES
    (data-sc-bee-role="badge-row" / "badge")
  * the accessible reading-distribution donut (unchanged truthful data source)
  * common vocabulary grouped by reading with ruby + glosses
    (data-sc-bee-role="vocab-group")
  * the expandable learning-aids section (phonetic family + stroke order)

Semantic text fallback is preserved: reading class labels, badge labels, and
the donut legend are real readable text, so colour/CSS is never the only
information channel.
"""
import json
import pathlib

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


def rec(name):
    return bk.normalize_record(json.loads((FIX / name).read_text(encoding="utf-8")))


def _roles(node, acc=None):
    """Collect every data.beeRole present anywhere in a structured-content tree."""
    if acc is None:
        acc = []
    if isinstance(node, dict):
        data = node.get("data")
        if isinstance(data, dict) and "beeRole" in data:
            acc.append(data["beeRole"])
        _roles(node.get("content"), acc)
    elif isinstance(node, list):
        for x in node:
            _roles(x, acc)
    return acc


def _find(node, role):
    """Return the first node with the given beeRole, else None."""
    if isinstance(node, dict):
        data = node.get("data")
        if isinstance(data, dict) and data.get("beeRole") == role:
            return node
        found = _find(node.get("content"), role)
        if found is not None:
            return found
    elif isinstance(node, list):
        for x in node:
            found = _find(x, role)
            if found is not None:
                return found
    return None


def _text(node):
    """Flatten all string content in a structured-content subtree."""
    out = []
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        out.append(_text(node.get("content")))
    elif isinstance(node, list):
        out.extend(_text(x) for x in node)
    return "".join(out)


def _detail(char="場"):
    r = rec(f"{char}.json")
    return bk._detail_content(r)


def test_card_has_hero_header_with_glyph_and_keyword():
    detail = _detail("場")
    hero = _find(detail, "hero")
    assert hero is not None, "reworked card must have a hero header"
    glyph = _find(hero, "hero-glyph")
    keyword = _find(hero, "hero-keyword")
    assert glyph is not None and _text(glyph) == "場"
    assert keyword is not None and _text(keyword)  # non-empty keyword


def test_readings_render_as_labelled_chips_grouped_by_class():
    detail = _detail("場")
    r = rec("場.json")
    # There must be reading chips, and On/Kun groups are labelled distinctly.
    roles = _roles(detail)
    assert "reading-group" in roles
    assert roles.count("reading-chip") >= 1
    blob = _text(detail)
    if r["on"]:
        assert "On" in blob
        for reading in r["on"]:
            assert reading in blob
    if r["kun"]:
        assert "Kun" in blob


def test_meanings_are_a_distinct_hierarchy_not_a_runon_div():
    detail = _detail("場")
    meaning = _find(detail, "meaning")
    assert meaning is not None
    r = rec("場.json")
    if r["senses"]:
        assert r["senses"][0] in _text(meaning)


def test_badges_are_small_aligned_and_labelled():
    detail = _detail("場")
    row = _find(detail, "badge-row")
    assert row is not None, "rank/grade/JLPT/stroke badges must be a badge row"
    roles = _roles(row)
    assert roles.count("badge") >= 2
    r = rec("場.json")
    blob = _text(row)
    if r["frequency_rank"] is not None:
        assert "Rank" in blob and str(r["frequency_rank"]) in blob
    if r["stroke_count"] is not None:
        assert "strokes" in blob or "Strokes" in blob


def test_donut_still_present_with_truthful_source_and_data():
    detail = _detail("生")  # 生 has full Jiten group totals (denominator 3922)
    assert _find(detail, "reading-donut") is not None
    blob = _text(detail)
    assert bk.DONUT_TITLE in blob
    assert bk.DONUT_DISCLAIMER in blob
    assert bk.reading_distribution(rec("生.json"))["total"] == 3922


def test_vocabulary_grouped_by_reading_with_ruby_and_gloss():
    detail = _detail("場")
    group = _find(detail, "vocab-group")
    assert group is not None
    blob = json.dumps(detail, ensure_ascii=False)
    assert '"ruby"' in blob and '"rt"' in blob  # furigana present


def test_learning_aids_stay_in_expandable_section():
    r = rec("生.json")
    char = "生"
    fam = {"component": "\u5bfa", "members": ["\u5f85", char], "source": "KanjiVG"}
    enr = {
        "strokes": {char: {"stroke_count": 5, "components": [char],
                           "asset": bk.kanjivg_asset_name(char)}},
        "families_by_char": {char: fam},
        "families": {"\u5bfa": fam},
        "assets": {bk.kanjivg_asset_name(char): "<svg></svg>"},
    }
    detail = bk._detail_content(r, enrichment=enr)
    blob = json.dumps(detail, ensure_ascii=False)
    assert '"details"' in blob and '"summary"' in blob
    assert "stroke-order" in blob and "phonetic-family" in blob


def test_no_enrichment_still_produces_hero_and_donut_no_empty_aids():
    # Without enrichment the card is still coherent (hero, chips, donut) and
    # contains NO empty learning-aids section.
    detail = _detail("生")
    assert _find(detail, "hero") is not None
    assert _find(detail, "reading-donut") is not None
    # no learning-aids details when there is no enrichment
    assert '"summary"' not in json.dumps(detail, ensure_ascii=False)
