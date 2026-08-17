"""RED/GREEN: per-entry raster reading-distribution PNG media.

The reading-distribution chart is a deterministic per-entry PNG packaged as
Yomitan dictionary media and referenced through supported structured content.

Contract:
  * ``build_reading_distribution_png(record)`` returns raw PNG bytes for any
    record with a positive Jiten reading total, else ``None``.
  * The image is a real PNG (signature), RGBA, and 128x128.
  * It is deterministic: identical input -> byte-identical output.
  * ``reading_distribution_asset_name(char)`` -> ``reading-distribution/{cp:05x}.png``.
  * ``build_zip`` bundles binary (bytes) assets verbatim at their archive path,
    so the packaged PNG round-trips byte-for-byte through the ZIP.
"""
import io
import json
import pathlib
import re
import zipfile

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"
ROOT = pathlib.Path(__file__).resolve().parent.parent

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def test_pillow_is_a_declared_runtime_dependency():
    # build_reading_distribution_png imports PIL at build time, so Pillow must
    # be a declared runtime dependency -- otherwise the build crashes on a clean
    # install even though it happens to pass where PIL is already present.
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    deps_block = re.search(r"dependencies\s*=\s*\[(.*?)\]", pyproject, re.DOTALL)
    assert deps_block, "no [project].dependencies array found"
    deps = deps_block.group(1).lower()
    assert "pillow" in deps, f"Pillow not declared as a runtime dependency: {deps!r}"


def rec(name):
    return bk.normalize_record(json.loads((FIX / name).read_text(encoding="utf-8")))


def test_asset_name_is_zero_padded_codepoint_under_reading_distribution():
    assert bk.reading_distribution_asset_name("場") == f"reading-distribution/{ord('場'):05x}.png"
    assert bk.reading_distribution_asset_name("生") == f"reading-distribution/{ord('生'):05x}.png"


def test_png_bytes_are_real_128x128_rgba_png():
    png = bk.build_reading_distribution_png(rec("場.json"))
    assert png is not None
    assert png[:8] == PNG_SIG, "not a PNG signature"
    from PIL import Image
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG"
    assert img.size == (128, 128)
    assert img.mode == "RGBA"


def test_png_is_deterministic():
    a = bk.build_reading_distribution_png(rec("生.json"))
    b = bk.build_reading_distribution_png(rec("生.json"))
    assert a == b and a is not None


def test_png_paints_visible_segments_not_blank():
    from PIL import Image
    png = bk.build_reading_distribution_png(rec("生.json"))
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    # count distinct opaque colours across the disc -- a real multi-segment
    # chart paints more than one solid colour.
    colors = img.getcolors(maxcolors=1 << 20) or []
    opaque = {rgba[:3] for _, rgba in colors if rgba[3] > 200}
    assert len(opaque) >= 2, f"chart looks blank/single-colour: {len(opaque)} colours"


def test_missing_distribution_yields_no_png():
    # KANJIDIC2-only record (no Jiten reading totals) -> no chart.
    kd = bk.kanjidic2_record("々", {"meanings": ["repetition mark"], "on": [], "kun": [],
                                    "nanori": [], "stroke_count": 3, "grade": None, "jlpt": None})
    assert bk.build_reading_distribution_png(kd) is None


def test_build_zip_bundles_binary_png_asset_byte_for_byte():
    png = bk.build_reading_distribution_png(rec("場.json"))
    path = bk.reading_distribution_asset_name("場")
    banks = bk.build_banks([rec("場.json")])
    zip_bytes = bk.build_zip(banks, "2026.01.01.1", assets={path: png})
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert path in names, f"{path} not packaged; have {names}"
        assert zf.read(path) == png, "packaged PNG bytes differ from source"


def test_zip_still_accepts_text_assets_alongside_binary():
    # Text assets (SVGs) and binary assets (PNGs) coexist in one build_zip call.
    png = bk.build_reading_distribution_png(rec("場.json"))
    assets = {
        bk.reading_distribution_asset_name("場"): png,
        "kanjivg/00000.svg": "<svg></svg>",
    }
    banks = bk.build_banks([rec("場.json")])
    zip_bytes = bk.build_zip(banks, "2026.01.01.1", assets=assets)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        assert zf.read("kanjivg/00000.svg").decode("utf-8") == "<svg></svg>"
        assert zf.read(bk.reading_distribution_asset_name("場")) == png
