"""Image accessibility contract.

Real Yomitan renders every structured-content <img> as a focusable
<a class="gloss-image-link"> whose only intrinsic text is the generic word
"Image". The one thing we control that feeds an accessible name / tooltip is the
image node's own `alt` and `title`. So every image node we emit MUST carry a
non-empty, meaningful `alt` AND a matching `title`, so the focusable image link
is never an unnamed/again-generic control -- it is described by real content.

The reading distribution is now plain text, so the compact card emits NO image
at all above the fold; the only remaining image is the stroke-order diagram in
the collapsed learning-aids disclosure, which must still be accessibly named.
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


def _stroke_enrichment(char):
    svg = (f'<svg xmlns:kvg="x"><g kvg:element="{char}">'
           '<path d="M1,1c1,1 2,2 3,3"/><path d="M5,5c1,1 2,2 3,3"/></g></svg>')
    return bk.assemble_enrichment({char: svg}, {char: 1})


def test_no_reading_distribution_image_is_emitted():
    # The distribution is textual now: the compact card emits no chart image, so
    # with no enrichment the card carries zero image nodes.
    for char in ("場", "生"):
        assert _all_images(char) == [], f"{char}: no image should be emitted"


def test_remaining_images_have_meaningful_alt_and_title():
    # The stroke-order diagram (in the learning-aids disclosure) is the only
    # image; it must still carry a non-empty, meaningful alt AND matching title.
    for char in ("場", "生"):
        imgs = _all_images(char, enrichment=_stroke_enrichment(char))
        assert imgs, f"{char}: expected a stroke-order image with enrichment"
        for img in imgs:
            alt = img.get("alt") or ""
            title = img.get("title") or ""
            assert alt.strip(), f"{char}: image missing non-empty alt"
            assert title.strip(), f"{char}: image missing non-empty title"
            assert alt != "Image", f"{char}: image alt must not be the generic 'Image'"
