"""Ship one rich term surface and keep character ranks in kanji metadata.

A kanji_meta_bank contains metadata, not a competing native kanji card. It must
not be confused with the kanji_bank that would replace the rich term surface.
Character ranks must never enter term metadata or change word-frequency averages.
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


def test_built_zip_excludes_native_kanji_entries():
    names = _zip_names()
    assert "kanji_bank_1.json" not in names, (
        "native kanji_bank must not ship: the kanji-click flow routes to the "
        "unstyleable native renderer and hides the rich structured card"
    )
    assert "kanji_meta_bank_1.json" in names


def test_built_zip_keeps_the_structured_term_surface():
    names = _zip_names()
    # The canonical rich card lives in the term bank; only kanji meta carries ranks.
    assert "term_bank_1.json" in names
    assert "term_meta_bank_1.json" not in names
    assert "index.json" in names
    assert "styles.css" in names
    assert "LICENSE-data.txt" in names


def test_shipped_bank_files_table_separates_kanji_ranks_from_word_metadata():
    shipped = {name for _, name in bk.BANK_FILES}
    assert shipped == {"term_bank", "kanji_meta_bank"}


def test_shipped_kanji_ranks_match_the_rich_card_without_word_frequency_rows():
    banks = _banks()
    with zipfile.ZipFile(io.BytesIO(bk.build_zip(banks, revision="2026.10.24"))) as zf:
        assert not any(name.startswith("term_meta_bank_") for name in zf.namelist())
        ranks = json.loads(zf.read("kanji_meta_bank_1.json"))
    assert ranks == banks["kanji_meta_bank"]
    assert ["場", "freq", 57] in ranks
    assert all(row[0] != "髙" for row in ranks), "aliases have no invented rank"


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
