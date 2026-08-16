"""README integrity: keep the user-facing claims and links valid without
locking in verbose prose.

These guard the *facts* the README asserts -- that the linked screenshots
exist, the download / auto-update URLs match what the generator emits, the
documented build command targets the real module, and the required licence /
attribution notices are present. Prose can be reworded freely; only broken
links, drifted URLs, or dropped legal notices fail here.
"""
import pathlib
import re

import bees_kanji as bk

ROOT = pathlib.Path(bk.__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _local_image_links(text):
    # Markdown ![alt](path) with a non-URL target -> repo-relative asset.
    links = []
    for target in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text):
        if not re.match(r"https?://", target):
            links.append(target.split(" ", 1)[0].strip())
    return links


def test_referenced_screenshots_exist():
    links = _local_image_links(README)
    assert links, "README references no local screenshots"
    for rel in links:
        assert (ROOT / rel).is_file(), f"missing README asset: {rel}"


def test_download_and_update_urls_match_generator():
    # The README must point at the same canonical download + auto-update URLs
    # the generator writes into index.json, so the docs never drift from code.
    idx = bk.build_index(revision="0.0.0")
    assert idx["downloadUrl"] in README, "README download URL drifted from generator"
    assert idx["indexUrl"] in README, "README auto-update index URL drifted from generator"


def test_documented_build_command_targets_real_module():
    # README documents `python -m bees_kanji`; that module must be importable
    # and expose a CLI entrypoint.
    assert "python -m bees_kanji" in README
    assert hasattr(bk, "main"), "bees_kanji.main() entrypoint missing"


def test_required_licence_and_attribution_notices_present():
    # Legal notices that MUST survive any concise rewrite.
    assert "CC BY-SA 4.0" in README  # data licence
    assert "CC BY-SA 3.0" in README  # KanjiVG licence
    assert "KanjiVG" in README
    assert "edrdg.org" in README  # EDRDG licence link
    assert "LICENSE-data.txt" in README
    assert "LICENSE-kanjivg.txt" in README


def test_reading_share_semantics_stated_truthfully():
    # The verified Jiten-backed semantics: a share of vocabulary *entries* by
    # reading, explicitly NOT usage/occurrence frequency.
    low = README.lower()
    assert "jiten vocabulary entries by reading" in low
    assert "not" in low and ("occurrence" in low or "usage frequency" in low)
