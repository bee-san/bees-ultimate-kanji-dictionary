#!/usr/bin/env python3
"""Render a few enriched dictionary entries to a standalone HTML page.

Mimics Yomitan's structured-content -> DOM transform (data keys become
data-sc-* attributes) and applies the bundled styles.css, so the reading
distribution pie chart, phonetic family, and stroke-order diagram can be verified in a real
(normal, narrow/responsive, reduced-motion, and text-only accessibility views).

Usage: python scripts/render_preview.py <output.html> [char ...]
Reads live KanjiVG (or the dated cache) + the local fixtures / Jiten cache.
"""
import html
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import bees_kanji as bk  # noqa: E402


def _kebab(key):
    out = []
    for ch in key:
        if ch.isupper():
            out.append("-" + ch.lower())
        else:
            out.append(ch)
    return "".join(out)


def render_node(node):
    """Structured-content node -> HTML string (Yomitan-compatible enough)."""
    if isinstance(node, str):
        return html.escape(node)
    if isinstance(node, list):
        return "".join(render_node(n) for n in node)
    if not isinstance(node, dict):
        return ""
    tag = node.get("tag", "span")
    if tag == "img":
        attrs = f' src="{html.escape(node.get("path", ""))}"'
        attrs += f' alt="{html.escape(node.get("alt", ""))}"'
        if node.get("title"):
            attrs += f' title="{html.escape(node["title"])}"'
        for k, v in (node.get("data") or {}).items():
            attrs += f' data-sc-{_kebab(k)}="{html.escape(str(v))}"'
        return f"<img{attrs}>"
    attrs = ""
    if node.get("lang"):
        attrs += f' lang="{html.escape(node["lang"])}"'
    if node.get("title"):
        attrs += f' title="{html.escape(node["title"])}"'
    if node.get("open"):
        attrs += " open"
    for k, v in (node.get("data") or {}).items():
        attrs += f' data-sc-{_kebab(k)}="{html.escape(str(v))}"'
    style = node.get("style") or {}
    if style:
        decls = "; ".join(f"{_kebab(k)}: {v}" for k, v in style.items())
        attrs += f' style="{html.escape(decls)}"'
    inner = render_node(node.get("content", ""))
    return f"<{tag}{attrs}>{inner}</{tag}>"


def main():
    out_path = pathlib.Path(sys.argv[1])
    chars = sys.argv[2:] or ["\u751f", "\u6642", "\u5834"]

    fix = ROOT / "fixtures"
    cache = ROOT / "cache"
    recs = []
    for c in chars:
        for base in (fix / f"{c}.json", cache / "2026-08-16" / bk.cache_filename(c)):
            if base.exists():
                recs.append(bk.normalize_record(json.loads(base.read_text(encoding="utf-8"))))
                break

    ranks = {r["character"]: r["frequency_rank"] for r in recs}
    svgs = bk.fetch_kanjivg_all([r["character"] for r in recs],
                                str(ROOT / "kanjivg-cache"), "2026-08-16")
    enr = bk.assemble_enrichment(svgs, ranks)

    # Per-entry reading-distribution PNG charts (packaged as binary media).
    for r in recs:
        png = bk.build_reading_distribution_png(r)
        if png is not None:
            enr["assets"][bk.reading_distribution_asset_name(r["character"])] = png

    # Write assets next to the HTML so <img src="..."> resolves. SVGs are text,
    # reading-distribution charts are PNG bytes.
    for path, data in enr["assets"].items():
        p = out_path.parent / path
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, (bytes, bytearray)):
            p.write_bytes(data)
        else:
            p.write_text(data, encoding="utf-8")

    blocks = []
    for r in recs:
        entry = bk.build_term_entry(r, enr)
        sc = entry[5][0]["content"]
        blocks.append(
            f'<section class="entry"><h2>{html.escape(r["character"])} '
            f'&mdash; {html.escape(r["keyword"] or "")}</h2>'
            f'<div class="gloss">{render_node(sc)}</div></section>'
        )

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bee's Ultimate Kanji Dictionary — enriched entry preview</title>
<style>
body {{ font-family: system-ui, "Noto Sans JP", sans-serif; margin: 1.5rem;
       --background-color: #fff; color: #111; }}
.entry {{ border: 1px solid #ccc; border-radius: 8px; padding: 1rem;
          margin-bottom: 1.5rem; max-width: 42rem; }}
h2 {{ margin-top: 0; }}
{bk.STYLES_CSS}
</style></head>
<body>
<h1>Enriched entry preview</h1>
<p>Structured content rendered as Yomitan would, with the bundled
<code>styles.css</code> applied. Verifies reading pie chart, phonetic family,
and animated stroke order with text fallbacks.</p>
{''.join(blocks)}
</body></html>"""
    out_path.write_text(page, encoding="utf-8")
    print(f"wrote {out_path} ({len(recs)} entries, {len(enr['assets'])} assets, "
          f"{len(enr['families'])} families)")


if __name__ == "__main__":
    main()
