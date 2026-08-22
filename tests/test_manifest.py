"""RED tests: the source/revision manifest.

The canonical release exposes a machine-readable MANIFEST.json describing the
build: the dictionary title/revision, the revision-independent content hash,
the UTC acquisition date, per-source record counts (Jiten authoritative +
KANJIDIC2 fallback), the enrichment counts (stroke sets, phonetic families,
bundled KanjiVG assets), the code revision that produced it, and the licences /
attribution. It is bundled inside the ZIP (so an importer can inspect
provenance offline) and emitted alongside the ZIP as a release asset. Bundling
it must NOT break deterministic rebuilds from identical inputs.
"""
import hashlib
import io
import json
import pathlib
import stat
import zipfile

import pytest

import bees_kanji as bk
from scripts.verify_source_snapshot import verify_and_extract

FIX = pathlib.Path(bk.__file__).resolve().parent.parent / "fixtures"
CHARS = list("\u5834\u751f")  # 場 生


def _records():
    return [
        bk.normalize_record(json.loads((FIX / f"{c}.json").read_text(encoding="utf-8")))
        for c in CHARS
    ]


def _sample_manifest():
    return bk.build_manifest(
        revision="2026.08.16.2",
        content_hash="deadbeef",
        date="2026-08-16",
        source_counts={"jiten": 3904, "kanjidic2": 8729},
        enrichment_counts={"strokes": 3892, "families": 284, "assets": 3892},
        sitemap_size=3904,
        code_revision="abc1234",
    )


def test_manifest_carries_source_and_revision_provenance():
    m = _sample_manifest()
    assert m["title"] == bk.TITLE
    assert m["revision"] == "2026.08.16.2"
    assert m["contentHash"] == "deadbeef"
    assert m["buildDate"] == "2026-08-16"
    assert m["downloadUrl"].endswith(bk.ZIP_NAME)
    # per-source record provenance (Jiten authoritative + KANJIDIC2 fallback)
    assert m["sources"]["jiten"]["records"] == 3904
    assert m["sources"]["jiten"]["url"] == "https://jiten.moe"
    assert m["sources"]["jiten"]["sitemapCharacters"] == 3904
    assert m["sources"]["jiten"]["acquisition"] == "once per UTC day, unauthenticated"
    assert m["sources"]["kanjidic2"]["records"] == 8729
    assert m["sources"]["kanjivg"]["assets"] == 3892
    # enrichment provenance
    assert m["enrichment"]["strokeSets"] == 3892
    assert m["enrichment"]["phoneticFamilies"] == 284
    # code provenance + licences
    assert m["codeRevision"] == "abc1234"
    assert "CC BY-SA" in m["attribution"]
    assert any("KanjiVG" in lic for lic in m["licences"])


def test_manifest_records_bulk_frequency_metric_and_exact_source_digest():
    m = bk.build_manifest(
        revision="2026.08.19",
        content_hash="deadbeef",
        date="2026-08-19",
        source_counts={"jiten": 2, "kanjidic2": 0},
        enrichment_counts={"strokes": 0, "families": 0, "assets": 2},
        sitemap_size=2,
        code_revision="abc1234",
        frequency_stats={
            "url": bk.JITEN_FREQUENCY_CSV_URL,
            "sha256": "f" * 64,
            "byteCount": 15_928_560,
            "retrievedDate": "2026-08-19",
            "schema": "Word,Form,Rank",
            "sourceRows": 449_400,
            "exactDuplicateRows": 6,
            "conflictingSurfaceReadingPairs": 1_803,
            "excludedConflictingRows": 3_608,
            "rows": 445_786,
            "relevantRows": 300_000,
            "alignedRows": 250_000,
            "ambiguousOrUnalignedRows": 50_000,
            "readingAssignments": 400_000,
            "relevantRankWeight": 1_000.0,
            "alignedRankWeight": 950.0,
            "rankWeightCoverage": 0.95,
            "charactersWithScores": 5_000,
            "readingGroupsWithScores": 20_000,
            "algorithm": "test-algorithm-v1",
            "metric": "rank-derived test metric",
        },
    )

    source = m["sources"]["jitenGlobalFrequency"]
    assert source["url"] == bk.JITEN_FREQUENCY_CSV_URL
    assert source["sha256"] == "f" * 64
    assert source["byteCount"] == 15_928_560
    assert source["sourceRows"] == 449_400
    assert source["rows"] == 445_786
    assert source["conflictingSurfaceReadingPairs"] == 1_803
    assert source["alignedRows"] == 250_000
    assert source["rankWeightCoverage"] == 0.95
    assert source["algorithm"] == "test-algorithm-v1"
    assert "ComputationJob.cs" in source["algorithmSource"]
    assert "KanjiReadingDecomposer.cs" in source["alignmentSource"]
    assert source["metric"] == "rank-derived test metric"
    assert source["semantics"] == "rank-derived weight, not observed occurrence probability"
    assert source["licenceUrl"].startswith("https://creativecommons.org/")


def test_detected_code_revision_is_an_unambiguous_full_object_id():
    revision = bk._code_revision()
    assert revision == "unknown" or len(revision) == 40


def test_manifest_is_bundled_in_the_zip():
    banks = bk.build_banks(_records())
    manifest = _sample_manifest()
    z = bk.build_zip(banks, revision="2026.08.16.2", manifest=manifest)
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        names = set(zf.namelist())
        assert "MANIFEST.json" in names
        bundled = json.loads(zf.read("MANIFEST.json"))
    assert bundled["revision"] == "2026.08.16.2"
    assert bundled["contentHash"] == "deadbeef"


def test_bundling_manifest_keeps_zip_deterministic():
    banks = bk.build_banks(_records())
    manifest = _sample_manifest()
    z1 = bk.build_zip(banks, revision="2026.08.16.2", manifest=manifest)
    z2 = bk.build_zip(banks, revision="2026.08.16.2", manifest=manifest)
    assert z1 == z2  # byte-identical from identical inputs


def test_manifest_bundled_does_not_enter_content_hash():
    # content_hash must stay revision/manifest-independent so a code-revision or
    # build-date change alone (with unchanged normalized data) does NOT force a
    # spurious new published revision.
    banks = bk.build_banks(_records())
    assert bk.content_hash(banks) == bk.content_hash(banks)


def test_content_hash_ignores_frequency_acquisition_date():
    banks = bk.build_banks(_records())
    stats = {
        "sha256": "a" * 64,
        "byteCount": 123,
        "rows": 2,
        "retrievedDate": "2026-08-19",
    }
    first = bk.content_hash(banks, frequency_stats=stats)
    second = bk.content_hash(
        banks,
        frequency_stats={**stats, "retrievedDate": "2026-08-20"},
    )
    assert first == second


def test_bundled_data_licence_is_the_repository_notice():
    repository_notice = (FIX.parent / "LICENSE-data.txt").read_text(encoding="utf-8")
    assert bk.LICENSE_DATA_TEXT == repository_notice
    banks = bk.build_banks(_records())
    with zipfile.ZipFile(io.BytesIO(bk.build_zip(banks, "2026.08.19"))) as archive:
        assert archive.read("LICENSE-data.txt").decode("utf-8") == repository_notice


def test_source_snapshot_is_deterministic_complete_and_self_verifying(tmp_path):
    date = "2026-08-19"
    roots = {
        "cache_dir": tmp_path / "cache",
        "kanjivg_cache_dir": tmp_path / "kanjivg-cache",
        "kanjidic2_cache_dir": tmp_path / "kanjidic2-cache",
        "frequency_cache_dir": tmp_path / "jiten-frequency-cache",
    }
    files = {
        roots["cache_dir"] / date / "sitemap.json": b'["\xe7\x94\x9f"]',
        roots["cache_dir"] / date / "751f.json": b'{"character":"\xe7\x94\x9f"}',
        roots["cache_dir"] / date / "751f.missing": b"",
        roots["kanjivg_cache_dir"] / date / "0751f.svg": b"<svg/>",
        roots["kanjivg_cache_dir"] / date / "0751f.missing": b"",
        roots["kanjivg_cache_dir"] / date / "0ffff.svg": b"<svg id='stale'/>",
        roots["kanjidic2_cache_dir"] / date / "kanjidic2.xml": b"<kanjidic2/>",
    }
    for path, data in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    frequency_bytes = b"Word,Form,Rank\n\xe7\x94\x9f,\xe3\x81\x9b\xe3\x81\x84,1\n"
    frequency_path = roots["frequency_cache_dir"] / date / "jiten_freq_global.csv"
    frequency_path.parent.mkdir(parents=True)
    frequency_path.write_bytes(b"replaced-after-scoring")
    consumed = {}
    for path, archive_name, limit in (
        (roots["cache_dir"] / date / "sitemap.json", f"cache/{date}/sitemap.json", 1024),
        (roots["cache_dir"] / date / "751f.json", f"cache/{date}/751f.json", 1024),
        (roots["kanjivg_cache_dir"] / date / "0751f.svg", f"kanjivg-cache/{date}/0751f.svg", 1024),
        (roots["kanjidic2_cache_dir"] / date / "kanjidic2.xml", f"kanjidic2-cache/{date}/kanjidic2.xml", 1024),
    ):
        raw = path.read_bytes()
        bk.record_consumed_source(
            consumed, archive_name, raw, path=path, max_bytes=limit
        )
    bk.record_consumed_source(
        consumed,
        f"jiten-frequency-cache/{date}/jiten_freq_global.csv",
        frequency_bytes,
        max_bytes=bk.MAX_JITEN_FREQUENCY_BYTES,
    )

    first_zip, first_lock = bk.build_source_snapshot(
        date=date,
        consumed_sources=consumed,
    )
    second_zip, second_lock = bk.build_source_snapshot(
        date=date,
        consumed_sources=consumed,
    )
    assert first_zip == second_zip
    assert first_lock == second_lock
    assert first_lock["generator"]["container"] == bk.PINNED_BUILD_CONTAINER
    assert first_lock["generator"]["python"] == "3.11.15"
    assert len(first_lock["generator"]["uvLockSha256"]) == 64
    assert len(first_lock["generator"]["packageLockSha256"]) == 64

    with zipfile.ZipFile(io.BytesIO(first_zip)) as archive:
        assert f"kanjivg-cache/{date}/0ffff.svg" not in archive.namelist()
        assert f"cache/{date}/751f.json" in archive.namelist()
        assert f"cache/{date}/751f.missing" not in archive.namelist()
        assert f"kanjivg-cache/{date}/0751f.svg" in archive.namelist()
        assert f"kanjivg-cache/{date}/0751f.missing" not in archive.namelist()
        assert archive.read(f"jiten-frequency-cache/{date}/jiten_freq_global.csv") == frequency_bytes
        lock_bytes = archive.read(bk.SOURCE_LOCK_NAME)
        assert json.loads(lock_bytes) == first_lock
        assert all(
            stat.S_IFMT((info.external_attr >> 16) & 0xFFFF) == stat.S_IFREG
            for info in archive.infolist()
        )
        for item in first_lock["files"]:
            raw = archive.read(item["path"])
            assert len(raw) == item["byteCount"]
            assert hashlib.sha256(raw).hexdigest() == item["sha256"]

    archive_path = tmp_path / bk.SOURCE_SNAPSHOT_NAME
    lock_path = tmp_path / bk.SOURCE_LOCK_NAME
    archive_path.write_bytes(first_zip)
    lock_path.write_bytes(lock_bytes)
    replay_path = tmp_path / "strict-replay"
    summary = verify_and_extract(archive_path, lock_path, replay_path)
    assert summary["fileCount"] == len(first_lock["files"])
    assert (replay_path / bk.SOURCE_LOCK_NAME).read_bytes() == lock_bytes


def test_source_snapshot_rejects_mutation_after_source_consumption(tmp_path):
    path = tmp_path / "sitemap.json"
    original = b'["\xe7\x94\x9f"]'
    path.write_bytes(original)
    consumed = {}
    bk.record_consumed_source(
        consumed,
        "cache/2026-08-19/sitemap.json",
        original,
        path=path,
        max_bytes=1024,
    )
    path.write_bytes(b'["\xe7\x94\xb2"]')

    with pytest.raises(bk.MalformedPayload, match="changed after consumption"):
        bk.build_source_snapshot(date="2026-08-19", consumed_sources=consumed)


def test_source_snapshot_rejects_aggregate_before_rereading_members(tmp_path, monkeypatch):
    consumed = {}
    for index in range(2):
        path = tmp_path / str(index)
        raw = b"ab"
        path.write_bytes(raw)
        bk.record_consumed_source(
            consumed, f"cache/{index}", raw, path=path, max_bytes=2
        )
    monkeypatch.setattr(bk, "MAX_SOURCE_SNAPSHOT_BYTES", 3)
    calls = []
    original = bk._read_file_bytes_bounded

    def tracked(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(bk, "_read_file_bytes_bounded", tracked)
    with pytest.raises(bk.MalformedPayload, match="total byte limit"):
        bk.build_source_snapshot(date="2026-08-19", consumed_sources=consumed)
    assert calls == []


@pytest.mark.parametrize("name", ["cache/./x", "cache/../x", "/cache/x", "cache\\x"])
def test_consumed_source_names_must_be_unique_canonical_archive_paths(name):
    with pytest.raises(bk.MalformedPayload, match="archive name"):
        bk.record_consumed_source({}, name, b"x")


def _emit_release(out_dir, revision, banks, manifest):
    """Mirror main()'s artifact emission: bundle the manifest in the ZIP, write
    the standalone manifest and pinned Global CSV, then checksum every asset."""
    zip_bytes = bk.build_zip(banks, revision, manifest=manifest)
    (out_dir / bk.ZIP_NAME).write_bytes(zip_bytes)
    manifest_text = bk.dump_json(manifest)
    (out_dir / "MANIFEST.json").write_text(manifest_text, encoding="utf-8")
    frequency_bytes = b"Word,Form,Rank\n\xe7\x94\xb2,\xe3\x81\x93\xe3\x81\x86,100\n"
    (out_dir / bk.JITEN_FREQUENCY_ASSET_NAME).write_bytes(frequency_bytes)
    lock_bytes = bk.dump_json({"schemaVersion": 1, "files": []}).encode("utf-8")
    (out_dir / bk.SOURCE_LOCK_NAME).write_bytes(lock_bytes)
    source_buffer = io.BytesIO()
    with zipfile.ZipFile(source_buffer, "w") as archive:
        info, raw = bk._zip_member(bk.SOURCE_LOCK_NAME, lock_bytes)
        archive.writestr(info, raw)
    source_bytes = source_buffer.getvalue()
    (out_dir / bk.SOURCE_SNAPSHOT_NAME).write_bytes(source_bytes)
    zip_digest = hashlib.sha256(zip_bytes).hexdigest()
    man_digest = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
    frequency_digest = hashlib.sha256(frequency_bytes).hexdigest()
    source_digest = hashlib.sha256(source_bytes).hexdigest()
    lock_digest = hashlib.sha256(lock_bytes).hexdigest()
    (out_dir / "SHA256SUMS").write_text(
        f"{zip_digest}  {bk.ZIP_NAME}\n"
        f"{man_digest}  MANIFEST.json\n"
        f"{frequency_digest}  {bk.JITEN_FREQUENCY_ASSET_NAME}\n"
        f"{source_digest}  {bk.SOURCE_SNAPSHOT_NAME}\n"
        f"{lock_digest}  {bk.SOURCE_LOCK_NAME}\n",
        encoding="utf-8",
    )
    return zip_bytes


def test_release_artifacts_verify_against_sha256sums(tmp_path):
    banks = bk.build_banks(_records())
    manifest = _sample_manifest()
    _emit_release(tmp_path, "2026.08.16.2", banks, manifest)

    lines = (tmp_path / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert any(line.endswith(bk.ZIP_NAME) for line in lines)
    assert any(line.endswith("MANIFEST.json") for line in lines)
    assert any(line.endswith(bk.JITEN_FREQUENCY_ASSET_NAME) for line in lines)
    assert any(line.endswith(bk.SOURCE_SNAPSHOT_NAME) for line in lines)
    assert any(line.endswith(bk.SOURCE_LOCK_NAME) for line in lines)
    assert {path.name for path in tmp_path.iterdir()} == set(bk.RELEASE_ASSET_NAMES)

    # Each recorded digest matches the on-disk file bytes (sha256sum -c parity).
    import hashlib
    for line in lines:
        digest, name = line.split("  ", 1)
        actual = hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
        assert actual == digest, name

    # The standalone MANIFEST.json is byte-identical to the bundled member.
    with zipfile.ZipFile(tmp_path / bk.ZIP_NAME) as zf:
        bundled = zf.read("MANIFEST.json")
    assert bundled == (tmp_path / "MANIFEST.json").read_bytes()


def test_release_artifacts_can_be_bound_to_the_final_tag_commit(tmp_path):
    banks = bk.build_banks(_records())
    manifest = _sample_manifest()
    first = _emit_release(tmp_path, "2026.08.16.2", banks, manifest)

    final_commit = "0123456789abcdef0123456789abcdef01234567"
    bk.bind_release_artifacts_to_code_revision(
        tmp_path, final_commit, revision="2026.08.16.3"
    )

    with zipfile.ZipFile(tmp_path / bk.ZIP_NAME) as zf:
        bundled = json.loads(zf.read("MANIFEST.json"))
        bundled_index = json.loads(zf.read("index.json"))
    standalone = json.loads((tmp_path / "MANIFEST.json").read_text(encoding="utf-8"))
    assert bundled["codeRevision"] == final_commit
    assert bundled["revision"] == "2026.08.16.3"
    assert bundled_index["revision"] == "2026.08.16.3"
    assert standalone == bundled
    assert (tmp_path / bk.ZIP_NAME).read_bytes() != first

    lines = (tmp_path / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    for line in lines:
        digest, name = line.split("  ", 1)
        actual = hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
        assert actual == digest, name

    first_bound = (tmp_path / bk.ZIP_NAME).read_bytes()
    bk.bind_release_artifacts_to_code_revision(
        tmp_path, final_commit, revision="2026.08.16.3"
    )
    assert (tmp_path / bk.ZIP_NAME).read_bytes() == first_bound


def test_changed_content_publishes_unchanged_content_does_not():
    # unchanged normalized content -> no new revision (no release)
    assert bk.decide_revision("h", "h", "2026-08-17", "2026.08.16") is None
    # changed content on a new day -> that day's revision (a release)
    assert bk.decide_revision("h2", "h", "2026-08-17", "2026.08.16") == "2026.08.17"
    # changed content same day as previous -> monotonic bump (a release)
    assert bk.decide_revision("h2", "h", "2026-08-16", "2026.08.16") == "2026.08.16.1"
