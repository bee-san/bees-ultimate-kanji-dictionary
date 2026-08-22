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


def test_screenshots_are_genuine_real_yomitan_captures():
    # The README screenshot strip must show GENUINE captures of the dictionary
    # imported into the official Yomitan UI (not generated package previews),
    # sourced from docs/images/real-yomitan/, and must not mislabel a preview
    # as a real Yomitan screenshot.
    links = _local_image_links(README)
    hero = [rel for rel in links if "real-yomitan/" in rel]
    assert hero, "README must reference genuine real-Yomitan captures under docs/images/real-yomitan/"
    for rel in hero:
        assert (ROOT / rel).is_file(), f"missing real-Yomitan capture: {rel}"
    low = README.lower()
    assert "real yomitan" in low or "official yomitan" in low, (
        "README screenshot caption must state the captures are from real/official Yomitan"
    )


def test_frequency_weight_hero_strip_is_the_exact_final_yomitan_capture_set():
    """Keep stale entry-count screenshots from silently returning to GitHub."""
    import hashlib

    from PIL import Image

    expected = {
        "docs/images/real-yomitan/sei-compact-light.png": (
            (1280, 900),
            "4d9437af030ca0c6cc340fb8ff678b9efbf4f427c97371801891c0dd07816bac",
        ),
        "docs/images/real-yomitan/sei-expanded-light.png": (
            (1280, 900),
            "94d829f3cae333709697c21ebfe7d249310685fe858ed4268ea33cc6bc6a77d8",
        ),
        "docs/images/real-yomitan/ba-narrow-expanded.png": (
            (380, 820),
            "751a899c30034b1c6672fe436595784353a02e3ee72f2534fa0f64568753caa9",
        ),
    }
    for relative, (size, digest) in expected.items():
        path = ROOT / relative
        with Image.open(path) as image:
            assert image.size == size, relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, relative
    assert "380px" in README
    assert "300px" not in README


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


def test_frequency_weight_is_documented_concisely_and_honestly():
    low = README.lower()
    assert "frequency weight" in low
    assert "rank-derived, not corpus probability" in low
    assert "1/sqrt(r)" in low
    assert "entry counts are never relabelled as frequency" in low


def test_stroke_diagram_is_documented_as_static():
    low = README.lower()
    assert "static kanjivg stroke-order diagram" in low
    assert "animated kanjivg" not in low


def test_bundled_kanjivg_licence_describes_static_adaptation():
    import bees_kanji as bk

    low = bk.LICENSE_KANJIVG_TEXT.lower()
    assert "static" in low
    assert "animation" not in low
