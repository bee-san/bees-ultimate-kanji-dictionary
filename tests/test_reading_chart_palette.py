"""RED/GREEN: the reading-distribution PNG is a compact deterministic paletted
image, not a bulky truecolor RGBA raster.

The donut only ever uses the fixed Okabe-Ito segment palette (at most six solid
colours) on a transparent field, so a palette-mode ("P") PNG with 1-bit alpha
represents it faithfully at a fraction of the bytes. With thousands of packaged
per-character charts in the real release, this keeps the ZIP a reasonable size
without any performance framework. It must stay byte-deterministic (identical
record -> identical bytes) and still paint every truthful segment.
"""
import io
import json
import pathlib

import bees_kanji as bk
from PIL import Image

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


def rec(name):
    return bk.normalize_record(json.loads((FIX / name).read_text(encoding="utf-8")))


def test_reading_chart_png_is_palette_mode_with_transparency():
    png = bk.build_reading_distribution_png(rec("生.json"))
    im = Image.open(io.BytesIO(png))
    assert im.mode == "P", f"chart PNG must be paletted, got {im.mode}"
    # a transparent field (donut hole + outside the ring) must be preserved
    assert "transparency" in im.info, "paletted chart must keep a transparent index"


def test_reading_chart_png_is_compact():
    """The paletted encoding must be materially smaller than a truecolor RGBA
    render of the same donut -- a raster chart per character otherwise bloats a
    release with thousands of entries."""
    png = bk.build_reading_distribution_png(rec("生.json"))
    # A prior RGBA render of this donut was ~8.6 KB; paletted must be well under
    # half that. Guard a concrete, comfortable ceiling.
    assert len(png) < 4000, f"paletted chart too large: {len(png)} bytes"


def test_reading_chart_png_is_deterministic():
    a = bk.build_reading_distribution_png(rec("生.json"))
    b = bk.build_reading_distribution_png(rec("生.json"))
    assert a == b, "chart PNG must be byte-deterministic"


def test_reading_chart_png_paints_every_segment_colour():
    """Every truthful segment colour must actually appear in the rasterised
    palette image -- the optimization must not drop or merge segments."""
    r = rec("生.json")
    png = bk.build_reading_distribution_png(r)
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    src = im.load()
    painted = set()
    for y in range(im.height):
        for x in range(im.width):
            px = src[x, y]
            if px[3] >= 128:
                painted.add(px[:3])
    dist = bk.reading_distribution(r)
    for seg in dist["segments"]:
        if seg["percent"] <= 0:
            continue  # a zero-percent tail contributes no wedge
        h = seg["color"].lstrip("#")
        want = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        assert want in painted, f"segment colour {seg['color']} missing from chart"


def test_fallback_record_still_has_no_chart():
    """A KANJIDIC2-only record (no Jiten reading totals) still yields no chart."""
    xml = (FIX / "kanjidic2_sample.xml").read_text(encoding="utf-8")
    kd2 = bk.parse_kanjidic2(xml)
    char = next(iter(kd2))
    r = bk.kanjidic2_record(char, kd2[char])
    assert bk.build_reading_distribution_png(r) is None
