#!/usr/bin/env python3
"""Render README screenshots from the *packaged* dictionary, not mockups.

Reads the exact term-bank entries, styles.css, and bundled KanjiVG SVG assets
straight out of an extracted release ZIP, applies Yomitan's structured-content
-> DOM transform (data keys become data-sc-* attributes), and frames each entry
as a compact Yomitan-style popup card. Emits three standalone HTML pages that
the screenshot step captures:

  1. compact.html    -- a normal entry with Learning aids collapsed
  2. expanded.html   -- the same entry with Learning aids open (donut, phonetic
                        family, stroke-order diagram)
  3. narrow.html     -- a reduced-motion / narrow-viewport static-fallback view

Because the input is the shipped package, the screenshots are an honest package
preview of what Yomitan renders, not an invented UI.

Usage: python scripts/render_screenshots.py <extracted-zip-dir> <out-dir> [char ...]
"""
import html
import json
import pathlib
import sys


def _kebab(key):
    out = []
    for ch in key:
        out.append("-" + ch.lower() if ch.isupper() else ch)
    return "".join(out)


def render_node(node, embed_svg=None):
    """Structured-content node -> HTML string (Yomitan-compatible enough).

    embed_svg: optional {relative-path -> svg-text} map. When an <img> points at
    a bundled asset we inline the SVG so the screenshot needs no extra fetch and
    the exact bundled stroke geometry is shown.
    """
    if isinstance(node, str):
        return html.escape(node)
    if isinstance(node, list):
        return "".join(render_node(n, embed_svg) for n in node)
    if not isinstance(node, dict):
        return ""
    tag = node.get("tag", "span")
    if tag == "img":
        path = node.get("path", "")
        if embed_svg is not None and path in embed_svg:
            wrap_attrs = ""
            for k, v in (node.get("data") or {}).items():
                wrap_attrs += f' data-sc-{_kebab(k)}="{html.escape(str(v))}"'
            title = html.escape(node.get("title", ""))
            return (f'<span{wrap_attrs} title="{title}" role="img" '
                    f'aria-label="{html.escape(node.get("alt", ""))}">'
                    f'{embed_svg[path]}</span>')
        attrs = f' src="{html.escape(path)}" alt="{html.escape(node.get("alt", ""))}"'
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
    inner = render_node(node.get("content", ""), embed_svg)
    return f"<{tag}{attrs}>{inner}</{tag}>"


def _set_details_open(node, is_open):
    """Return a copy of the structured content with <details> forced open/closed."""
    if isinstance(node, list):
        return [_set_details_open(n, is_open) for n in node]
    if not isinstance(node, dict):
        return node
    n = dict(node)
    if n.get("tag") == "details":
        if is_open:
            n["open"] = True
        else:
            n.pop("open", None)
    if "content" in n:
        n["content"] = _set_details_open(n["content"], is_open)
    return n


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bee's Ultimate Kanji Dictionary — package preview</title>
<style>
:root {{ color-scheme: light; }}
html, body {{ margin: 0; background: {page_bg}; }}
body {{ display: inline-block; padding: 14px; }}
/* A compact Yomitan-style popup card. */
.yomitan-popup {{
  font-family: "Noto Sans JP", system-ui, -apple-system, "Segoe UI", sans-serif;
  --background-color: #ffffff;
  background: var(--background-color);
  color: #1a1a1a;
  width: {width};
  border: 1px solid #d0d0d0;
  border-radius: 10px;
  box-shadow: 0 6px 22px rgba(0,0,0,0.16);
  padding: 12px 14px;
  box-sizing: border-box;
}}
.head {{ display: flex; align-items: baseline; gap: 10px; margin: 0 0 6px; }}
.head .glyph {{ font-size: 2.1rem; line-height: 1; }}
.head .kw {{ font-size: 1.05rem; font-weight: 600; color: #333; }}
.entry {{ font-size: 0.95rem; }}
{styles_css}
/* The bundled styles.css caps the stroke <img> at 6em; mirror that for the
   inlined <svg> the preview substitutes so sizing matches Yomitan. */
[data-sc-bee-role="stroke-image"] svg {{ width: 6em; height: 6em; max-width: 6em; max-height: 6em; }}
</style></head>
<body>
<div class="yomitan-popup">
  <div class="head"><span class="glyph" lang="ja">{glyph}</span><span class="kw">{keyword}</span></div>
  <div class="entry">{body}</div>
</div>
</body></html>"""


def main():
    zip_dir = pathlib.Path(sys.argv[1])
    out_dir = pathlib.Path(sys.argv[2])
    chars = sys.argv[3:] or ["\u5834", "\u751f"]
    out_dir.mkdir(parents=True, exist_ok=True)

    term_bank = json.loads((zip_dir / "term_bank_1.json").read_text(encoding="utf-8"))
    styles_css = (zip_dir / "styles.css").read_text(encoding="utf-8")
    by_char = {e[0]: e for e in term_bank}

    # Preload bundled SVG assets so they can be inlined into the screenshots.
    embed = {}
    kd = zip_dir / "kanjivg"
    if kd.is_dir():
        for p in kd.glob("*.svg"):
            embed[f"kanjivg/{p.name}"] = p.read_text(encoding="utf-8")

    def card(char, is_open, width, page_bg, reduced_motion=False):
        entry = by_char[char]
        keyword = entry[5][0]
        sc = _set_details_open(entry[5][1]["content"], is_open)
        css = styles_css
        if reduced_motion:
            # Emulate a client honouring prefers-reduced-motion: no animation.
            css += ('\n[data-sc-bee-role="stroke-image"] svg *,'
                    '\n[data-sc-bee-role="stroke-image"] svg { animation: none !important; }\n')
        return PAGE.format(
            page_bg=page_bg, width=width, styles_css=css,
            glyph=html.escape(char), keyword=html.escape(keyword or ""),
            body=render_node(sc, embed),
        )

    primary = chars[0]
    (out_dir / "compact.html").write_text(
        card(primary, False, "420px", "#eef1f5"), encoding="utf-8")
    (out_dir / "expanded.html").write_text(
        card(primary, True, "440px", "#eef1f5"), encoding="utf-8")
    (out_dir / "narrow.html").write_text(
        card(primary, True, "300px", "#eef1f5", reduced_motion=True),
        encoding="utf-8")

    print(f"wrote compact/expanded/narrow to {out_dir} "
          f"(primary={primary}, {len(embed)} svg assets available)")


if __name__ == "__main__":
    main()
