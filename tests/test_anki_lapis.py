"""RED tests: Lapis/Anki setup integrity.

Guards that the copyable card templates and the field-mapping doc are mutually
consistent: every Anki field referenced by the front/back templates is declared
in the mapping table, and every Yomitan marker the mapping uses is one this
dictionary actually populates (so nobody wires a card to data we never ship,
e.g. audio or pitch). No bespoke Anki tooling -- just static integrity checks.
"""
import pathlib
import re

ANKI = pathlib.Path(__file__).resolve().parent.parent / "anki"

# Yomitan markers this dictionary is capable of populating. Audio / pitch are
# intentionally excluded (out of scope) and must never appear in the mapping.
SUPPORTED_MARKERS = {
    "{expression}", "{reading}", "{glossary}", "{glossary-first}",
    "{frequency-harmonic-rank}", "{cloze-body}",
}
FORBIDDEN_MARKERS = {"{audio}", "{pitch-accents}", "{pitch-accent-graphs}"}


def _read(name):
    return (ANKI / name).read_text(encoding="utf-8")


def _template_fields(text):
    # Anki field refs like {{Word}}, {{cloze:Sentence}}, {{#WordReading}}.
    fields = set()
    for m in re.findall(r"\{\{([#/^]?)([a-zA-Z][\w:-]*)\}\}", text):
        name = m[1]
        if ":" in name:
            name = name.split(":", 1)[1]  # {{cloze:Sentence}} -> Sentence
        if name != "FrontSide":
            fields.add(name)
    return fields


def _mapping_table():
    """Parse the | Lapis field | Yomitan marker | rows from anki/README.md."""
    rows = {}
    for line in _read("README.md").splitlines():
        m = re.match(r"\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|", line)
        if m:
            rows[m.group(1)] = m.group(2)
    return rows


def test_all_files_present():
    for f in ("README.md", "front.html", "back.html", "styling.css"):
        assert (ANKI / f).exists(), f


def test_mapping_table_parsed_and_nonempty():
    rows = _mapping_table()
    assert rows, "no | Lapis | marker | rows parsed from anki/README.md"
    assert "Word" in rows and rows["Word"] == "{expression}"


def test_every_mapping_marker_is_supported():
    for field, marker in _mapping_table().items():
        assert marker in SUPPORTED_MARKERS, f"{field} -> {marker} not supported"


def test_no_audio_or_pitch_markers_anywhere():
    blob = _read("README.md") + _read("front.html") + _read("back.html")
    for bad in FORBIDDEN_MARKERS:
        assert bad not in blob
    for word in ("PitchPosition", "WordAudio", "SentenceAudio"):
        # they may be *named* as intentionally-unmapped, but never given a marker
        assert f"`{word}`" not in _mapping_table()


def test_template_fields_are_declared_in_mapping():
    declared = set(_mapping_table().keys())
    used = _template_fields(_read("front.html")) | _template_fields(_read("back.html"))
    missing = used - declared
    assert not missing, f"template fields not documented in mapping: {missing}"


def test_templates_have_no_script_or_iframe():
    for f in ("front.html", "back.html"):
        low = _read(f).lower()
        assert "<script" not in low and "<iframe" not in low


def test_styling_honours_reduced_motion_and_scopes_our_markers():
    css = _read("styling.css")
    assert "prefers-reduced-motion" in css
    assert "data-sc-bee-role" in css
