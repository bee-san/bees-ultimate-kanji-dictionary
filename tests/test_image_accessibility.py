"""RED/GREEN: image accessibility contract.

Real Yomitan renders every structured-content <img> as a focusable
<a class="gloss-image-link"> whose only intrinsic text is the generic word
"Image". The one thing we control that feeds an accessible name / tooltip is the
image node's own `alt` and `title`. So every image node we emit MUST carry a
non-empty, meaningful `alt` AND a matching `title`, so the focusable image link
is never an unnamed/again-generic control -- it is described by real content.
"""
import json
import pathlib

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


def rec(name):
    return bk.normalize_record(json.loads((FIX / name).read_text(encoding="utf-8")))


def _walk(node):
    if isinstance(node, dict):
        yield node
        yield from _walk(node.get("content"))
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _all_images(char, enrichment=None):
    body = bk._detail_content(rec(f"{char}.json"), enrichment=enrichment)
    return [n for n in _walk(body) if isinstance(n, dict) and n.get("tag") == "img"]


def test_chart_image_has_meaningful_alt_and_title():
    for char in ("場", "生"):
        imgs = _all_images(char)
        assert imgs, f"{char}: expected a chart image"
        for img in imgs:
            alt = img.get("alt") or ""
            title = img.get("title") or ""
            assert alt.strip(), f"{char}: image missing non-empty alt"
            assert title.strip(), f"{char}: image missing non-empty title"
            # alt is meaningful (the reading-distribution description), not generic
            assert alt != "Image", f"{char}: image alt must not be the generic 'Image'"


def test_chart_image_is_not_collapsible_extra_toggle():
    # The chart already has a full visible text legend beside it; it must not add
    # an extra collapse toggle (another focusable control) on top of the
    # focusable image link Yomitan always creates.
    for char in ("場", "生"):
        for img in _all_images(char):
            if img.get("data", {}).get("beeRole") == "reading-chart":
                assert img.get("collapsible") in (None, False), \
                    "chart image must not be collapsible (avoids a redundant toggle)"
