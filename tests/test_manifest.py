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
import zipfile

import bees_kanji as bk

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


def _emit_release(out_dir, revision, banks, manifest):
    """Mirror main()'s artifact emission: bundle the manifest in the ZIP, write
    the standalone MANIFEST.json, and a SHA256SUMS covering both files."""
    zip_bytes = bk.build_zip(banks, revision, manifest=manifest)
    (out_dir / bk.ZIP_NAME).write_bytes(zip_bytes)
    manifest_text = bk.dump_json(manifest)
    (out_dir / "MANIFEST.json").write_text(manifest_text, encoding="utf-8")
    zip_digest = hashlib.sha256(zip_bytes).hexdigest()
    man_digest = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
    (out_dir / "SHA256SUMS").write_text(
        f"{zip_digest}  {bk.ZIP_NAME}\n{man_digest}  MANIFEST.json\n",
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
