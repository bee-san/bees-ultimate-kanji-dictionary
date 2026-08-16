"""RED/GREEN: the structured single-character term entry is the ONE canonical surface.

Real-Yomitan reproduction (parent task t_08284e0e) proved the shipped package
carried BOTH a rich structured term entry AND a native ``kanji_bank`` entry for
the same character. Yomitan's kanji-click / "view kanji" flow routes
*exclusively* to the native kanji renderer (a fixed Meaning/Readings table that
dictionary CSS cannot restyle), so a user who clicked a headword kanji landed on
the flat card and never saw the reading-distribution donut.

The smallest robust fix is to stop packaging the competing native banks. These
tests lock that contract: the built ZIP must contain the term banks (+ media +
licences + manifest + styles) and MUST NOT contain ``kanji_bank_1.json`` or
``kanji_meta_bank_1.json``.

Verified selection contract (official Yomitan 26.7.29.0, real import, see
/tmp/verify): the ORDINARY lookup path -- scanning/hovering a character in text,
or typing it in the search box -- is a TERM lookup, and every single character
resolves to our one structured term entry, so the rich donut card is exactly
what the reader sees on the canonical path. The secondary ``type=kanji``
drilldown reached only by explicitly clicking a headword-kanji link now returns
Yomitan's built-in "No results found" instead of a competing flat card: with no
native kanji dictionary shipped there is deliberately nothing to route it to.
That is the documented, supported selection -- we ship a single canonical
character surface (the rich term card) and no flat kanji surface -- not a
regression, because the reader's ordinary lookup already delivers the full card.
"""
import io
import json
import pathlib
import zipfile

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"
CHARS = "場男事生行高"


def _banks():
    recs = [
        bk.normalize_record(json.loads((FIX / f"{c}.json").read_text(encoding="utf-8")))
        for c in CHARS
    ]
    return bk.build_banks(recs, aliases={"髙": "高"})


def _zip_names(revision="2026.08.16"):
    z = bk.build_zip(_banks(), revision=revision)
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        return set(zf.namelist())


def test_built_zip_excludes_native_kanji_banks():
    names = _zip_names()
    assert "kanji_bank_1.json" not in names, (
        "native kanji_bank must not ship: the kanji-click flow routes to the "
        "unstyleable native renderer and hides the rich structured card"
    )
    assert "kanji_meta_bank_1.json" not in names


def test_built_zip_keeps_the_structured_term_surface():
    names = _zip_names()
    # The canonical rich card lives in the term bank; term meta carries ranks.
    assert "term_bank_1.json" in names
    assert "term_meta_bank_1.json" in names
    assert "index.json" in names
    assert "styles.css" in names
    assert "LICENSE-data.txt" in names


def test_shipped_bank_files_table_carries_only_term_banks():
    """The shipped-bank manifest (drives both build_zip and content_hash) must
    no longer reference the native kanji banks, so nothing can reintroduce a
    competing flat surface or re-add it to the deterministic content hash."""
    shipped = {name for _, name in bk.BANK_FILES}
    assert "kanji_bank" not in shipped
    assert "kanji_meta_bank" not in shipped
    assert shipped == {"term_bank", "term_meta_bank"}


def test_every_shipped_character_has_exactly_one_structured_term_entry():
    """No character may ship two competing surfaces. Each canonical character
    resolves to a single structured-content term entry (the rich card)."""
    z = bk.build_zip(_banks(), revision="2026.08.16")
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        term_bank = json.loads(zf.read("term_bank_1.json"))
        assert "kanji_bank_1.json" not in zf.namelist()
    for c in CHARS:
        entries = [e for e in term_bank if e[0] == c]
        assert len(entries) == 1, f"{c} must have exactly one canonical entry"
        glossary = entries[0][5]
        # The single entry is the structured-content rich card.
        assert any(
            isinstance(g, dict) and g.get("type") == "structured-content"
            for g in glossary
        ), f"{c} entry must be the structured rich card, not a flat gloss"
