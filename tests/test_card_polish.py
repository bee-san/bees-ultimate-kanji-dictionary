"""RED/GREEN: focused visual-polish regressions for the compact card.

These lock the polish deltas made on top of the parent-verified compact
baseline:

  * the term glossary carries NO redundant standalone plain-text gloss line
    above the structured card -- the hero already names the keyword, so a
    leading gloss string is a duplicate Yomitan would render twice;
  * the six always-visible common words are CLICKABLE through supported
    internal Yomitan ``?query=`` links (structured-content ``<a>`` tags), so a
    reader can pivot to any word's own lookup with one click and no custom JS;
  * the clickable word still carries its ruby surface + concise gloss, and the
    query targets the plain (annotation-free) surface form.
"""
import json
import pathlib

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"

CHARS = ["場", "生", "来", "事", "男", "行", "高"]


def rec(name):
    return bk.normalize_record(json.loads((FIX / name).read_text(encoding="utf-8")))


def _walk(node):
    if isinstance(node, dict):
        yield node
        yield from _walk(node.get("content"))
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _role(node):
    if not isinstance(node, dict):
        return None
    data = node.get("data")
    return data.get("beeRole") if isinstance(data, dict) else None


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


# --- 1. no duplicate standalone gloss ----------------------------------------

def test_term_glossary_has_no_redundant_standalone_gloss():
    """The glossary must be exactly the single structured-content card -- no
    leading plain-text gloss string, which Yomitan would paint as a duplicate
    line above the hero keyword."""
    for char in CHARS:
        glossary = bk.build_term_entry(rec(f"{char}.json"))[5]
        assert len(glossary) == 1, (
            f"{char}: glossary must be just the structured card, got {len(glossary)} items"
        )
        only = glossary[0]
        assert isinstance(only, dict) and only.get("type") == "structured-content", (
            f"{char}: the single glossary item must be the structured-content card"
        )
        # no bare string gloss anywhere at the top level
        assert not any(isinstance(g, str) for g in glossary), (
            f"{char}: a bare string gloss duplicates the hero keyword"
        )


def test_hero_keyword_still_present_after_gloss_removal():
    """Removing the standalone gloss must not lose the keyword -- it still lives
    in the hero header."""
    for char in ("場", "生", "来"):
        detail = bk._detail_content(rec(f"{char}.json"))[0]
        kw = [n for n in _walk(detail) if _role(n) == "hero-keyword"]
        assert kw and _surface_text(kw[0]).strip(), f"{char}: hero keyword missing"


# --- 2. clickable common words via internal ?query= links --------------------

def test_visible_words_are_internal_query_links():
    """Each of the six above-the-fold common words is a supported internal
    Yomitan query link: an <a> whose href starts with ?query= targeting the
    word's plain surface form."""
    for char in ("場", "生", "来"):
        detail = bk._detail_content(rec(f"{char}.json"))[0]
        grid = next(n for n in _walk(detail) if _role(n) == "vocab-grid")
        links = [n for n in _walk(grid) if isinstance(n, dict) and n.get("tag") == "a"]
        assert len(links) == 6, f"{char}: expected 6 word links, got {len(links)}"
        for link in links:
            href = link.get("href", "")
            assert href.startswith("?query="), f"{char}: link href not internal query: {href!r}"
            surface = _surface_text(link.get("content"))
            assert surface, f"{char}: link has no visible surface text"
            # the query targets the plain surface form (annotation-free)
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(href).query).get("query", [""])[0]
            assert q == surface, f"{char}: query {q!r} != surface {surface!r}"


def test_word_links_keep_ruby_and_gloss():
    """A clickable word keeps its ruby surface, and the concise gloss stays
    beside it."""
    detail = bk._detail_content(rec("場.json"))[0]
    grid = next(n for n in _walk(detail) if _role(n) == "vocab-grid")
    # ruby lives inside the link content
    links = [n for n in _walk(grid) if isinstance(n, dict) and n.get("tag") == "a"]
    assert any(
        any(isinstance(d, dict) and d.get("tag") == "ruby" for d in _walk(link))
        for link in links
    ), "at least one word link must carry ruby"
    glosses = [n for n in _walk(grid) if _role(n) == "vocab-gloss"]
    assert len(glosses) == 6 and all(_surface_text(g).strip() for g in glosses)


def test_link_href_matches_schema_internal_pattern():
    """The generated href must match the schema's internal-link pattern so the
    term bank still validates against the official Yomitan schema."""
    import re
    pattern = re.compile(r"^(?:https?:|\?)[\w\W]*")
    for char in CHARS:
        detail = bk._detail_content(rec(f"{char}.json"))[0]
        links = [n for n in _walk(detail) if isinstance(n, dict) and n.get("tag") == "a"]
        for link in links:
            assert pattern.match(link.get("href", "")), f"{char}: bad href {link.get('href')!r}"
