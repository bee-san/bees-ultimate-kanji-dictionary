"""Security contract for source-snapshot verification and replay extraction."""

import hashlib
import json
import stat
import zipfile

import pytest

from scripts.verify_source_snapshot import SnapshotError, verify_and_extract


def _info(name, mode=stat.S_IFREG | 0o644):
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = mode << 16
    return info


def _snapshot(tmp_path, members, *, lock_files=None):
    if lock_files is None:
        lock_files = [
            {
                "path": name,
                "byteCount": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
            for name, raw, _mode in members
        ]
    lock = {"schemaVersion": 1, "files": lock_files}
    lock_bytes = (json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n").encode()
    archive_path = tmp_path / "source-snapshot.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw, mode in members:
            archive.writestr(_info(name, mode), raw)
        archive.writestr(_info("SOURCE-LOCK.json"), lock_bytes)
    expected_lock = tmp_path / "SOURCE-LOCK.json"
    expected_lock.write_bytes(lock_bytes)
    return archive_path, expected_lock


def test_verified_snapshot_extracts_exact_declared_inventory(tmp_path):
    sitemap = '["生"]'.encode()
    members = [
        ("cache/2026-08-19/sitemap.json", sitemap, stat.S_IFREG | 0o644),
        ("cache/2026-08-19/751f.missing", b"", stat.S_IFREG | 0o644),
    ]
    archive, lock = _snapshot(tmp_path, members)
    destination = tmp_path / "replay"

    summary = verify_and_extract(archive, lock, destination)

    assert summary["fileCount"] == 2
    assert (destination / "cache/2026-08-19/sitemap.json").read_bytes() == sitemap
    assert (destination / "cache/2026-08-19/751f.missing").read_bytes() == b""
    assert (destination / "SOURCE-LOCK.json").read_bytes() == lock.read_bytes()


@pytest.mark.parametrize(
    "members, lock_files, message",
    [
        (
            [
                ("cache/x", b"a", stat.S_IFREG | 0o644),
                ("cache/./x", b"a", stat.S_IFREG | 0o644),
            ],
            None,
            "canonical",
        ),
        (
            [
                ("cache/x", b"a", stat.S_IFREG | 0o644),
                ("extra", b"b", stat.S_IFREG | 0o644),
            ],
            [
                {
                    "path": "cache/x",
                    "byteCount": 1,
                    "sha256": hashlib.sha256(b"a").hexdigest(),
                }
            ],
            "inventory",
        ),
        (
            [("cache/link", b"target", stat.S_IFLNK | 0o777)],
            None,
            "regular file",
        ),
    ],
)
def test_snapshot_rejects_noncanonical_undeclared_and_special_members(
    tmp_path, members, lock_files, message
):
    archive, lock = _snapshot(tmp_path, members, lock_files=lock_files)
    with pytest.raises(SnapshotError, match=message):
        verify_and_extract(archive, lock, tmp_path / "replay")
    assert not (tmp_path / "replay").exists()


def test_snapshot_verifies_every_hash_before_installing_destination(tmp_path):
    members = [("cache/x", b"tampered", stat.S_IFREG | 0o644)]
    lock_files = [
        {
            "path": "cache/x",
            "byteCount": len(b"tampered"),
            "sha256": hashlib.sha256(b"expected").hexdigest(),
        }
    ]
    archive, lock = _snapshot(tmp_path, members, lock_files=lock_files)
    with pytest.raises(SnapshotError, match="SHA-256"):
        verify_and_extract(archive, lock, tmp_path / "replay")
    assert not (tmp_path / "replay").exists()
