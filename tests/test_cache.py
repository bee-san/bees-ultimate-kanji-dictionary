"""RED tests: daily acquisition with a resumable per-character cache.

fetch_all(characters, cache_dir, date, fetcher) writes cache/DATE/<char>.json,
reuses files already fetched that UTC day (zero requests on rerun), fetches
only missing characters after an interruption, and skips 404s. The fetcher is
injected so these tests make no network calls.
"""
import json

import pytest

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


def test_jiten_cache_records_the_exact_positive_and_negative_bytes_consumed(tmp_path):
    date = "2026-08-19"
    sources = {}

    def fetcher(character):
        if character == "乙":
            raise bk.NotFound(character)
        return {"character": character}

    assert bk.fetch_all(
        ["甲", "乙"], tmp_path, date, fetcher, consumed_sources=sources
    ) == {"甲": {"character": "甲"}}
    positive = f"cache/{date}/{bk.cache_filename('甲')}"
    negative = f"cache/{date}/{bk.cache_filename('乙').removesuffix('.json')}.missing"
    assert set(sources) == {positive, negative}
    for item in sources.values():
        assert item.path is not None
        assert item.path.read_bytes() in (b"", '{"character": "甲"}'.encode())


def test_sitemap_cache_records_the_exact_bytes_consumed(tmp_path):
    sources = {}
    date = "2026-08-19"
    assert bk.fetch_sitemap_cached(
        tmp_path,
        date,
        fetcher=lambda: ["甲"],
        consumed_sources=sources,
    ) == ["甲"]
    item = sources[f"cache/{date}/sitemap.json"]
    assert item.path is not None
    assert item.path.read_bytes() == '["甲"]'.encode()


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


def test_valid_jiten_payload_wins_over_stale_negative_marker(tmp_path):
    date = "2026-08-19"
    day = tmp_path / date
    day.mkdir()
    path = day / bk.cache_filename("丐")
    path.write_text(json.dumps({"character": "丐"}), encoding="utf-8")
    missing = path.with_suffix(".missing")
    missing.touch()

    def should_not_fetch(_character):
        raise AssertionError("a valid positive cache entry must win")

    assert bk.fetch_all(["丐"], tmp_path, date, should_not_fetch) == {
        "丐": {"character": "丐"}
    }
    assert not missing.exists()


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


class _FakeResponse:
    def __init__(self, data, content_length=None):
        self.data = data
        self.offset = 0
        self.read_sizes = []
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size):
        self.read_sizes.append(size)
        chunk = self.data[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk


def test_http_bytes_rejects_oversized_content_length_before_read(monkeypatch):
    response = _FakeResponse(b"ignored", content_length=33)
    monkeypatch.setattr(bk.urllib.request, "urlopen", lambda *_a, **_k: response)

    with pytest.raises(bk.MalformedPayload, match="Content-Length"):
        bk.http_get_bytes("https://example.invalid/data", max_bytes=32)
    assert response.read_sizes == []


def test_http_bytes_streams_with_a_hard_limit(monkeypatch):
    response = _FakeResponse(b"x" * 33)
    monkeypatch.setattr(bk.urllib.request, "urlopen", lambda *_a, **_k: response)

    with pytest.raises(bk.MalformedPayload, match="byte limit"):
        bk.http_get_bytes("https://example.invalid/data", max_bytes=32)
    assert response.read_sizes
    assert all(size <= 33 for size in response.read_sizes)


def test_http_bytes_enforces_total_deadline_after_a_slow_read(monkeypatch):
    response = _FakeResponse(b"x")
    times = iter((0.0, 0.0, 0.0, 11.0))
    monkeypatch.setattr(bk.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(bk.urllib.request, "urlopen", lambda *_a, **_k: response)

    with pytest.raises(bk.MalformedPayload, match="total deadline"):
        bk.http_get_bytes(
            "https://example.invalid/data", max_bytes=32, total_seconds=10
        )
