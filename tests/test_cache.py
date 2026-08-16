"""RED tests: daily acquisition with a resumable per-character cache.

fetch_all(characters, cache_dir, date, fetcher) writes cache/DATE/<char>.json,
reuses files already fetched that UTC day (zero requests on rerun), fetches
only missing characters after an interruption, and skips 404s. The fetcher is
injected so these tests make no network calls.
"""
import json
import pathlib

import bees_kanji as bk


def make_fetcher(responses, calls):
    """responses: {char: payload or 404}; records each requested char."""
    def fetcher(char):
        calls.append(char)
        r = responses.get(char)
        if r == 404:
            raise bk.NotFound(char)
        return r
    return fetcher


def test_fetch_all_writes_dated_cache_and_returns_payloads(tmp_path):
    calls = []
    responses = {"場": {"character": "場"}, "男": {"character": "男"}}
    fetcher = make_fetcher(responses, calls)
    out = bk.fetch_all(["場", "男"], tmp_path, "2026-08-16", fetcher)
    assert out["場"] == {"character": "場"}
    assert out["男"] == {"character": "男"}
    assert calls == ["場", "男"]
    # cache files written under the dated directory
    assert (tmp_path / "2026-08-16" / bk.cache_filename("場")).exists()
    assert (tmp_path / "2026-08-16" / bk.cache_filename("男")).exists()


def test_second_same_day_run_makes_zero_requests(tmp_path):
    calls = []
    responses = {"場": {"character": "場"}, "男": {"character": "男"}}
    bk.fetch_all(["場", "男"], tmp_path, "2026-08-16", make_fetcher(responses, calls))
    assert len(calls) == 2
    # rerun same day: everything cached -> no new fetcher calls
    calls2 = []
    out = bk.fetch_all(["場", "男"], tmp_path, "2026-08-16", make_fetcher(responses, calls2))
    assert calls2 == []
    assert out["場"] == {"character": "場"}


def test_interrupted_run_fetches_only_missing(tmp_path):
    # Simulate a prior partial run: 場 already cached, 男 missing.
    day = tmp_path / "2026-08-16"
    day.mkdir(parents=True)
    (day / bk.cache_filename("場")).write_text(
        json.dumps({"character": "場"}), encoding="utf-8"
    )
    calls = []
    responses = {"男": {"character": "男"}}
    out = bk.fetch_all(["場", "男"], tmp_path, "2026-08-16", make_fetcher(responses, calls))
    assert calls == ["男"]                 # only the missing one
    assert out["場"]["character"] == "場"
    assert out["男"]["character"] == "男"


def test_404_is_negatively_cached_for_same_day_resume(tmp_path):
    calls = []
    responses = {"場": {"character": "場"}, "髙": 404}
    out = bk.fetch_all(["場", "髙"], tmp_path, "2026-08-16", make_fetcher(responses, calls))
    assert "場" in out
    assert "髙" not in out                 # 404 skipped
    assert not (tmp_path / "2026-08-16" / bk.cache_filename("髙")).exists()
    calls.clear()
    out = bk.fetch_all(["髙"], tmp_path, "2026-08-16", make_fetcher(responses, calls))
    assert out == {}
    assert calls == []                     # known daily miss is not requested again


def test_sitemap_is_cached_by_utc_day_and_offline_reuses_it(tmp_path):
    calls = []

    def fetcher():
        calls.append(True)
        return ["場", "男"]

    assert bk.fetch_sitemap_cached(tmp_path, "2026-08-16", fetcher) == ["場", "男"]
    assert bk.fetch_sitemap_cached(tmp_path, "2026-08-16", fetcher, offline=True) == ["場", "男"]
    assert calls == [True]


def test_offline_sitemap_fails_closed_when_daily_cache_is_missing(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        bk.fetch_sitemap_cached(tmp_path, "2026-08-16", offline=True)


def test_cache_uses_codepoint_escaping_for_filesystem_safety(tmp_path):
    # Surrogate-pair / unusual characters must produce a safe filename.
    calls = []
    weird = "\U000209A0"  # 𠦠, a CJK-ext-B char from the live sitemap
    responses = {weird: {"character": weird}}
    out = bk.fetch_all([weird], tmp_path, "2026-08-16", make_fetcher(responses, calls))
    assert out[weird]["character"] == weird
    files = list((tmp_path / "2026-08-16").glob("*.json"))
    assert len(files) == 1
