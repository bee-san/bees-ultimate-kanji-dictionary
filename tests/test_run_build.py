"""RED tests: run_build threads KanjiVG enrichment + assets into the ZIP, and
the content hash reacts to enrichment changes so a new revision is published
when the visual data changes.
"""
import io
import json
import pathlib
import zipfile

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"
CHARS = list("\u5834\u751f")  # 場 生


def _payload(c):
    return json.loads((FIX / f"{c}.json").read_text(encoding="utf-8"))


def _fake_kvg(c, phon=None):
    pa = f' kvg:phon="{phon}"' if phon else ""
    return (f'<svg xmlns:kvg="x"><g kvg:element="{c}"{pa}>'
            '<path d="M1,1c1,1 2,2 3,3"/></g></svg>')


def test_content_hash_changes_with_enrichment():
    recs = [bk.normalize_record(_payload(c)) for c in CHARS]
    plain = bk.build_banks(recs)
    svgs = {c: _fake_kvg(c) for c in CHARS}
    enr = bk.assemble_enrichment(svgs, {r["character"]: r["frequency_rank"] for r in recs})
    enriched = bk.build_banks(recs, enrichment=enr)
    assert bk.content_hash(plain) != bk.content_hash(enriched)


def test_run_build_acquires_kanjivg_and_bundles_assets(tmp_path):
    cache = tmp_path / "cache"
    kvg_cache = tmp_path / "kanjivg"

    def jiten(c):
        return _payload(c)

    def kvg(c):
        return _fake_kvg(c)

    result = bk.run_build(
        CHARS, str(cache), "2026-08-16",
        aliases={}, fetcher=jiten,
        kanjivg_cache_dir=str(kvg_cache), kanjivg_fetcher=kvg,
    )
    assert "enrichment" in result
    assert result["enrichment"]["strokes"]  # stroke info assembled
    with zipfile.ZipFile(io.BytesIO(result["zip_bytes"])) as zf:
        names = set(zf.namelist())
    assert any(n.startswith("kanjivg/") for n in names)
    assert "styles.css" in names
    # term entries carry the enrichment
    with zipfile.ZipFile(io.BytesIO(result["zip_bytes"])) as zf:
        tb = zf.read("term_bank_1.json").decode("utf-8")
    assert "stroke-order" in tb


def _walk_img_paths(node, out):
    if isinstance(node, list):
        for x in node:
            _walk_img_paths(x, out)
    elif isinstance(node, dict):
        if node.get("tag") == "img" and isinstance(node.get("path"), str):
            out.add(node["path"])
        for v in node.values():
            _walk_img_paths(v, out)


def test_run_build_bundles_frequency_weight_pngs_with_no_dangling_refs(tmp_path):
    # Every rank-derived chart the term cards reference must be a real ZIP member.
    cache = tmp_path / "cache"
    frequency_cache = tmp_path / "frequency-cache"
    frequency_csv = "Word,Form,Rank\n場,ば,100\n生,せい,50\n"

    result = bk.run_build(
        CHARS,
        str(cache),
        "2026-08-16",
        aliases={},
        fetcher=_payload,
        frequency_cache_dir=str(frequency_cache),
        frequency_fetcher=lambda: frequency_csv,
    )

    with zipfile.ZipFile(io.BytesIO(result["zip_bytes"])) as zf:
        names = set(zf.namelist())
        term_bank = json.loads(zf.read("term_bank_1.json"))

        pngs = [n for n in names if n.startswith("reading-frequency/")]
        assert pngs, "expected packaged Frequency weight PNGs"
        for name in pngs:
            data = zf.read(name)
            assert data[:8] == b"\x89PNG\r\n\x1a\n"

    referenced = set()
    _walk_img_paths(term_bank, referenced)
    charts = {p for p in referenced if p.startswith("reading-frequency/")}
    assert charts, "term cards must reference the packaged chart PNGs"
    dangling = charts - names
    assert not dangling, f"dangling chart references: {dangling}"
    for c in CHARS:
        assert bk.reading_frequency_asset_name(c) in names
    assert not any(name.startswith("reading-distribution/") for name in names)
    assert result["enrichment_counts"]["assets"] == 0
    assert result["frequency_stats"]["chartAssets"] == 2
