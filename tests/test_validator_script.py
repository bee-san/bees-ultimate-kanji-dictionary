"""End-to-end: the Node validator accepts a freshly built ZIP.

This exercises scripts/validate_yomitan.mjs against the official schemas via
ajv, catching any drift between our Python builder and Yomitan's real schema
expectations. Skips gracefully if node/node_modules are unavailable.
"""
import json
import pathlib
import shutil
import subprocess

import pytest
import bees_kanji as bk

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures"
CHARS = list("場男事生行高")


def _banks():
    recs = [
        bk.normalize_record(json.loads((FIX / f"{c}.json").read_text(encoding="utf-8")))
        for c in CHARS
    ]
    return bk.build_banks(recs, {"髙": "高"})


def _fake_kvg(c, phon=None):
    pa = f' kvg:phon="{phon}"' if phon else ""
    return (f'<svg xmlns:kvg="x"><g kvg:element="{c}"{pa}>'
            '<path d="M1,1c1,1 2,2 3,3"/><path d="M5,5c1,1 2,2 3,3"/></g></svg>')


def _enriched():
    recs = [
        bk.normalize_record(json.loads((FIX / f"{c}.json").read_text(encoding="utf-8")))
        for c in CHARS
    ]
    ranks = {r["character"]: r["frequency_rank"] for r in recs}
    svgs = {c: _fake_kvg(c) for c in CHARS}
    # give two chars a shared phonetic to exercise family rendering
    svgs["場"] = _fake_kvg("場", "\u661c")
    svgs["生"] = _fake_kvg("生", "\u661c")
    enr = bk.assemble_enrichment(svgs, ranks)
    banks = bk.build_banks(recs, {"髙": "高"}, enrichment=enr)
    return banks, enr


def test_node_validator_accepts_built_zip(tmp_path):
    if shutil.which("node") is None:
        pytest.skip("node not available")
    if not (ROOT / "node_modules" / "ajv").exists():
        pytest.skip("node_modules not installed")
    zip_path = tmp_path / bk.ZIP_NAME
    zip_path.write_bytes(bk.build_zip(_banks(), "2026.08.16"))
    proc = subprocess.run(
        ["node", str(ROOT / "scripts" / "validate_yomitan.mjs"), str(zip_path)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "Yomitan validation passed" in proc.stdout


def test_node_validator_accepts_enriched_zip_with_assets(tmp_path):
    if shutil.which("node") is None:
        pytest.skip("node not available")
    if not (ROOT / "node_modules" / "ajv").exists():
        pytest.skip("node_modules not installed")
    banks, enr = _enriched()
    zip_path = tmp_path / bk.ZIP_NAME
    zip_path.write_bytes(bk.build_zip(banks, "2026.08.16", assets=enr["assets"]))
    proc = subprocess.run(
        ["node", str(ROOT / "scripts" / "validate_yomitan.mjs"), str(zip_path)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "referenced img assets all resolve" in proc.stdout
