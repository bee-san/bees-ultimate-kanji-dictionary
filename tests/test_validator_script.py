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
