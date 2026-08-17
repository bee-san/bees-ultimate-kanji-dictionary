"""RED/GREEN: the compact kanji card contract (parent-verified).

The always-visible (above-the-fold) region of the canonical term card must be,
in this exact order:

  1. HERO header (glyph + keyword)
  2. the TOP THREE readings selected by Jiten vocabulary-entry totals
     (``reading_entry_counts``), rendered as plain reading CHIPS -- NOT split
     into On/Kun labelled groups
  3. a compact MEANING line
  4. the reading distribution, rendered as a concise heading followed by a
     plain textual list of reading labels and percentages (no graphic/image)
  5. exactly SIX globally highest-frequency, de-duplicated vocabulary words in a
     responsive two-column grid (3 left / 3 right), each with ruby + concise gloss
  6. collapsed Yomitan disclosures (``details``) carrying every piece of
     secondary material: complete On/Kun lists, rank/grade/JLPT/strokes, the
     stroke diagram, the phonetic family, and sources.

None of the secondary material (On/Kun split, metadata badges, stroke diagram,
phonetic family, sources) may appear above the fold.
"""
import json
import pathlib

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


def rec(name):
    return bk.normalize_record(json.loads((FIX / name).read_text(encoding="utf-8")))


def payload(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# --- structured-content walkers ------------------------------------------------

def _walk(node):
    if isinstance(node, dict):
        yield node
        yield from _walk(node.get("content"))
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _walk_above_fold(node):
    """Walk the tree but do NOT descend into collapsed <details> disclosures."""
    if isinstance(node, dict):
        if node.get("tag") == "details":
            return
        yield node
        yield from _walk_above_fold(node.get("content"))
    elif isinstance(node, list):
        for item in node:
            yield from _walk_above_fold(item)


def _role(node):
    if not isinstance(node, dict):
        return None
    data = node.get("data")
    return data.get("beeRole") if isinstance(data, dict) else None


def _text(node):
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return _text(node.get("content"))
    if isinstance(node, list):
        return "".join(_text(x) for x in node)
    return ""


def _surface_text(node):
    """Flatten text but drop <rt> ruby annotations (keep only base surface)."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if node.get("tag") == "rt":
            return ""
        return _surface_text(node.get("content"))
    if isinstance(node, list):
        return "".join(_surface_text(x) for x in node)
    return ""


def _detail_root(char):
    detail = bk._detail_content(rec(f"{char}.json"))
    # _detail_content returns [ {beeRole: detail} ]
    return detail[0]


def _above_fold_roles(char):
    """Top-level child roles of the detail wrapper (the always-visible stack)."""
    root = _detail_root(char)
    return [_role(child) for child in root["content"]]


# --- 1. hero ------------------------------------------------------------------

def test_hero_is_first_and_pairs_glyph_with_keyword():
    root = _detail_root("場")
    first = root["content"][0]
    assert _role(first) == "hero"
    assert _text(bk_find(first, "hero-glyph")) == "場"
    assert _text(bk_find(first, "hero-keyword"))  # non-empty


def bk_find(node, role):
    for n in _walk(node):
        if _role(n) == role:
            return n
    return None


# --- 2. top-three readings by Jiten totals, NOT On/Kun split ------------------

def test_reading_chips_are_top_three_by_jiten_totals():
    for char, expected in (("場", ["じょう", "ば", "えき"]),
                           ("生", ["せい", "お", "う"])):
        root = _detail_root(char)
        chips = [_text(n) for n in _walk_above_fold(root) if _role(n) == "reading-chip"]
        assert chips == expected, f"{char}: chips {chips} != {expected}"


def test_no_onkun_split_above_the_fold():
    # No reading-label / reading-group in the always-visible stack.
    for char in ("場", "生"):
        root = _detail_root(char)
        labels = [n for n in _walk_above_fold(root) if _role(n) == "reading-label"]
        assert not labels, f"{char}: On/Kun labels must not be above the fold"


# --- 3. meaning ---------------------------------------------------------------

def test_meaning_line_present_and_distinct():
    root = _detail_root("場")
    meaning = bk_find(root, "meaning")
    assert meaning is not None
    assert "location" in _text(meaning)


# --- 4. textual reading distribution ------------------------------------------

def test_distribution_is_a_textual_list_no_image():
    for char in ("場", "生"):
        root = _detail_root(char)
        dist = bk_find(root, "reading-distribution")
        assert dist is not None, f"{char}: distribution node missing"
        # no img anywhere in the section
        imgs = [n for n in _walk(dist) if n.get("tag") == "img"]
        assert not imgs, f"{char}: distribution must not contain an image"
        blob = json.dumps(dist, ensure_ascii=False)
        assert ".png" not in blob.lower(), f"{char}: no PNG reference allowed"
        assert "reading-distribution/" not in blob, f"{char}: no packaged asset path"
        assert '"path"' not in blob, f"{char}: no media path key"
        # it IS a text list with the heading and percentages
        assert '"tag": "ul"' in blob
        assert "Reading distribution" in blob
        assert "%" in blob


def test_no_conic_gradient_or_graphic_nodes_remain():
    for char in ("場", "生"):
        root = _detail_root(char)
        roles = [_role(n) for n in _walk(root)]
        assert "donut-ring" not in roles
        assert "donut-hole" not in roles
        assert "donut-graphic" not in roles
        assert "donut-swatch" not in roles
        assert "reading-donut" not in roles
        # no inline conic-gradient anywhere
        blob = json.dumps(root, ensure_ascii=False)
        assert "conic-gradient" not in blob


# --- 5. exactly six global words in a grid -----------------------------------

def test_six_global_words_exact_order():
    cases = {
        "場": ["場所", "場合", "場", "立場", "現場", "その場"],
        "生": ["生きる", "先生", "生まれる", "人生", "生活", "一生"],
    }
    for char, expected in cases.items():
        root = _detail_root(char)
        vocab = [_surface_text(n) for n in _walk_above_fold(root) if _role(n) == "vocab-word"]
        assert vocab == expected, f"{char}: vocab {vocab} != {expected}"
        assert len(vocab) == 6


def test_vocab_words_have_ruby_and_gloss():
    root = _detail_root("場")
    grid = bk_find(root, "vocab-grid")
    assert grid is not None, "six-word grid must be a vocab-grid node"
    blob = json.dumps(grid, ensure_ascii=False)
    assert '"ruby"' in blob and '"rt"' in blob
    glosses = [n for n in _walk(grid) if _role(n) == "vocab-gloss"]
    assert len(glosses) == 6 and all(_text(g).strip() for g in glosses)


# --- 6. secondary material lives in collapsed disclosures --------------------

def test_metadata_badges_not_above_the_fold():
    for char in ("場", "生"):
        roles = _above_fold_roles(char)
        assert "badge-row" not in roles, f"{char}: badges must be inside a disclosure"


def test_onkun_and_metadata_inside_disclosures():
    for char in ("場", "生"):
        r = rec(f"{char}.json")
        root = _detail_root(char)
        details = [n for n in _walk(root) if n.get("tag") == "details"]
        assert details, f"{char}: no disclosure sections"
        details_text = "".join(_text(d) for d in details)
        for reading in r["on"] + r["kun"]:
            assert reading in details_text, f"{char}: {reading} not in disclosure"
        for label in ("Rank", "Grade", "JLPT", "strokes"):
            assert label in details_text, f"{char}: {label} not in disclosure"


def test_disclosures_are_collapsed_not_open_by_default():
    # Yomitan-supported <details> without the `open` attribute stay collapsed.
    root = _detail_root("場")
    for d in (n for n in _walk(root) if n.get("tag") == "details"):
        assert not d.get("open"), "secondary disclosures must be collapsed by default"
