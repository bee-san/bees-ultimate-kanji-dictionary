"""RED tests: the single build pipeline and revision-on-change decision.

run_build(characters, cache_dir, date, aliases, fetcher) fetches (via cache),
normalizes, and builds the banks + ZIP, returning a result with the content
hash. decide_revision(content_hash, previous_hash, date, previous_revision)
returns a monotonic dot-numeric revision only when content changed.
"""
import json
import pathlib

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"
CHARS = list("場男事生行高")


def offline_fetcher(char):
    return json.loads((FIX / f"{char}.json").read_text(encoding="utf-8"))


def test_run_build_produces_banks_zip_and_hash(tmp_path):
    res = bk.run_build(CHARS, tmp_path, "2026-08-16", {"髙": "高"}, offline_fetcher)
    assert set(res["banks"]) == {
        "term_bank", "term_meta_bank", "kanji_bank", "kanji_meta_bank"
    }
    assert isinstance(res["zip_bytes"], bytes) and res["zip_bytes"]
    assert len(res["content_hash"]) == 64
    # term bank includes all six characters + the alias
    exprs = [e[0] for e in res["banks"]["term_bank"]]
    for c in CHARS:
        assert c in exprs
    assert "髙" in exprs


def test_decide_revision_new_when_no_previous():
    rev = bk.decide_revision("abc", None, "2026-08-16", None)
    assert rev == "2026.08.16"


def test_decide_revision_none_when_unchanged():
    # same hash -> no new release
    rev = bk.decide_revision("abc", "abc", "2026-08-17", "2026.08.16")
    assert rev is None


def test_decide_revision_bumps_on_change_same_day():
    # content changed on the same UTC day as the previous revision -> add a
    # monotonic dot-numeric suffix so the revision strictly increases.
    rev = bk.decide_revision("xyz", "abc", "2026-08-16", "2026.08.16")
    assert rev is not None and rev > "2026.08.16"


def test_decide_revision_uses_date_on_change_new_day():
    rev = bk.decide_revision("xyz", "abc", "2026-08-17", "2026.08.16")
    assert rev == "2026.08.17"
    assert rev > "2026.08.16"
