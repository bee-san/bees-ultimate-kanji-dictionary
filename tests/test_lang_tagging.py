"""RED/GREEN: every Japanese-script content node is tagged lang="ja".

Screen readers and font selection need Japanese text explicitly marked so a
Japanese voice/font is chosen rather than the popup's default (often Chinese
Han rendering on CJK-ambiguous glyphs). Every node whose *own* text is Japanese
(kanji glyph, reading chips, vocabulary surface, reading-group labels used as
Japanese readings) must carry lang="ja" -- above the fold AND inside the
collapsed disclosures.
"""
import json
import pathlib

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


def rec(name):
    return bk.normalize_record(json.loads((FIX / name).read_text(encoding="utf-8")))


def _detail_root(char):
    return bk._detail_content(rec(f"{char}.json"))[0]


def _walk(node):
    if isinstance(node, dict):
        yield node
        yield from _walk(node.get("content"))
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _role(node):
    data = node.get("data") if isinstance(node, dict) else None
    return data.get("beeRole") if isinstance(data, dict) else None


def test_above_fold_reading_chips_are_lang_ja():
    for char in ("場", "生"):
        root = _detail_root(char)
        chips = [n for n in _walk(root) if _role(n) == "reading-chip"]
        assert chips, f"{char}: no reading chips"
        for c in chips:
            assert c.get("lang") == "ja", f"{char}: reading chip missing lang=ja"


def test_disclosure_reading_chips_are_lang_ja():
    # The full On/Kun lists inside the collapsed 'All readings' disclosure are
    # Japanese readings and must be tagged too, not just the above-fold chips.
    for char in ("場", "生"):
        root = _detail_root(char)
        chips = [n for n in _walk(root) if _role(n) == "reading-chip"]
        # there are more chips than the 3 above-fold ones once the disclosure is included
        assert len(chips) > 3, f"{char}: expected disclosure chips beyond the top 3"
        for c in chips:
            assert c.get("lang") == "ja", f"{char}: disclosure reading chip missing lang=ja"


def test_vocab_words_are_lang_ja():
    for char in ("場", "生"):
        root = _detail_root(char)
        words = [n for n in _walk(root) if _role(n) == "vocab-word"]
        assert words, f"{char}: no vocab words"
        for w in words:
            assert w.get("lang") == "ja", f"{char}: vocab-word missing lang=ja"


def test_hero_glyph_is_lang_ja():
    root = _detail_root("生")
    glyph = next(n for n in _walk(root) if _role(n) == "hero-glyph")
    assert glyph.get("lang") == "ja"
