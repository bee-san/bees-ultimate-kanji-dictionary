"""RED tests: the KANJIDIC2 fallback is wired into the daily build pipeline.

The build fetches the static KANJIDIC2 XML once per UTC day (resumable dated
cache, reused on same-day reruns), merges its fallbacks with the authoritative
Jiten records, and produces a ZIP whose character count materially exceeds the
Jiten-only set -- without weakening any Jiten entry.
"""
import io
import json
import pathlib
import zipfile

import bees_kanji as bk


def test_kanjidic2_download_uses_https():
    assert bk.KANJIDIC2_URL.startswith("https://")

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"
KD2_XML = (FIX / "kanjidic2_sample.xml").read_text(encoding="utf-8")
CHARS = list("場生")


def jiten(char):
    return json.loads((FIX / f"{char}.json").read_text(encoding="utf-8"))


def test_fetch_kanjidic2_caches_once_per_day_and_reuses(tmp_path):
    calls = []

    def fetcher():
        calls.append(1)
        return KD2_XML

    text1 = bk.fetch_kanjidic2_source(str(tmp_path), "2026-08-16", fetcher)
    text2 = bk.fetch_kanjidic2_source(str(tmp_path), "2026-08-16", fetcher)
    assert text1 == KD2_XML and text2 == KD2_XML
    assert len(calls) == 1  # same-day rerun reuses the cached XML, no refetch


def test_run_build_merges_kanjidic2_fallbacks_into_zip(tmp_path):
    cache = tmp_path / "cache"
    kd2_cache = tmp_path / "kd2"
    res = bk.run_build(
        CHARS, str(cache), "2026-08-16", aliases={}, fetcher=jiten,
        kanjidic2_cache_dir=str(kd2_cache),
        kanjidic2_fetcher=lambda: KD2_XML,
    )
    chars = [r["character"] for r in res["records"]]
    # Jiten characters kept, KANJIDIC2-only characters added, no duplicates
    assert set(CHARS).issubset(chars)
    assert "唖" in chars and "丂" in chars
    assert len(chars) == len(set(chars))
    # materially more than the Jiten-only input
    assert len(chars) > len(CHARS)
    with zipfile.ZipFile(io.BytesIO(res["zip_bytes"])) as zf:
        names = zf.namelist()
        tb = json.loads(zf.read("term_bank_1.json"))
    # Native kanji bank is not shipped; the structured term bank is the single
    # canonical surface and must carry both Jiten and KANJIDIC2-only characters.
    assert "kanji_bank_1.json" not in names
    tb_chars = {e[0] for e in tb}
    assert "唖" in tb_chars and "場" in tb_chars
    # Jiten's enriched 場 entry is untouched: its examples/donut survive
    ba_term = next(e for e in tb if e[0] == "場")
    blob = json.dumps(ba_term, ensure_ascii=False)
    assert "location" in blob
    assert "WRONG-SHOULD-NOT-WIN" not in blob


def test_run_build_without_kanjidic2_is_unchanged(tmp_path):
    # Omitting the KANJIDIC2 source yields the Jiten-only build (back-compat).
    cache = tmp_path / "cache"
    res = bk.run_build(CHARS, str(cache), "2026-08-16", aliases={}, fetcher=jiten)
    chars = {r["character"] for r in res["records"]}
    assert chars == set(CHARS)
