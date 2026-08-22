#!/usr/bin/env python3
"""Strictly verify and install a deterministic replay source snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import tempfile
import zipfile

SOURCE_LOCK_NAME = "SOURCE-LOCK.json"
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_LOCK_BYTES = 8 * 1024 * 1024
MAX_MEMBER_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 25_000
CHUNK_BYTES = 64 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SnapshotError(RuntimeError):
    """The source snapshot is unsafe, undeclared, corrupt, or non-canonical."""


def _canonical_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or name.endswith("/"):
        raise SnapshotError(f"source member path is not canonical: {name!r}")
    path = pathlib.PurePosixPath(name)
    if (
        path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or str(path) != name
    ):
        raise SnapshotError(f"source member path is not canonical: {name!r}")
    return name


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError(f"duplicate JSON key in source lock: {key}")
        result[key] = value
    return result


def _parse_lock(raw: bytes) -> tuple[dict, dict[str, dict]]:
    if not raw or len(raw) > MAX_LOCK_BYTES:
        raise SnapshotError("SOURCE-LOCK.json exceeds its byte limit")
    try:
        lock = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotError("SOURCE-LOCK.json is not strict UTF-8 JSON") from exc
    if not isinstance(lock, dict) or lock.get("schemaVersion") != 1:
        raise SnapshotError("SOURCE-LOCK.json has an unsupported schema")
    files = lock.get("files")
    if not isinstance(files, list) or len(files) > MAX_MEMBERS - 1:
        raise SnapshotError("SOURCE-LOCK.json file inventory is invalid")

    indexed = {}
    total = 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "byteCount", "sha256"}:
            raise SnapshotError("SOURCE-LOCK.json contains an invalid file record")
        raw_name = item.get("path")
        if not isinstance(raw_name, str):
            raise SnapshotError("SOURCE-LOCK.json contains a non-text file path")
        name = _canonical_name(raw_name)
        if name == SOURCE_LOCK_NAME or name in indexed:
            raise SnapshotError("SOURCE-LOCK.json contains a duplicate file path")
        size = item.get("byteCount")
        digest = item.get("sha256")
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or size > MAX_MEMBER_BYTES
        ):
            raise SnapshotError(f"locked byte count is invalid: {name}")
        if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
            raise SnapshotError(f"locked SHA-256 is invalid: {name}")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise SnapshotError("source snapshot exceeds the expanded byte limit")
        indexed[name] = item
    return lock, indexed


def _read_member_bounded(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    chunks = []
    total = 0
    with archive.open(info, "r") as source:
        while True:
            chunk = source.read(min(CHUNK_BYTES, limit + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise SnapshotError(f"source member exceeds its declared size: {info.filename}")
            chunks.append(chunk)
    return b"".join(chunks)


def verify_and_extract(archive_path, expected_lock_path, destination):
    archive_path = pathlib.Path(archive_path)
    expected_lock_path = pathlib.Path(expected_lock_path)
    destination = pathlib.Path(destination)
    if destination.exists():
        raise SnapshotError(f"replay destination already exists: {destination}")
    if archive_path.is_symlink() or not archive_path.is_file():
        raise SnapshotError("source snapshot archive is missing or invalid")
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise SnapshotError("source snapshot archive exceeds its byte limit")
    if expected_lock_path.is_symlink() or not expected_lock_path.is_file():
        raise SnapshotError("standalone SOURCE-LOCK.json is missing or invalid")
    if expected_lock_path.stat().st_size > MAX_LOCK_BYTES:
        raise SnapshotError("standalone SOURCE-LOCK.json exceeds its byte limit")
    with expected_lock_path.open("rb") as handle:
        expected_lock = handle.read(MAX_LOCK_BYTES + 1)
    if len(expected_lock) > MAX_LOCK_BYTES:
        raise SnapshotError("standalone SOURCE-LOCK.json exceeds its byte limit")

    temp_root = None
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_MEMBERS:
                raise SnapshotError("source snapshot member count is invalid")
            names = []
            by_name = {}
            advertised_total = 0
            for info in infos:
                name = _canonical_name(info.filename)
                if name in by_name:
                    raise SnapshotError("source snapshot contains duplicate canonical paths")
                if info.flag_bits & 0x1:
                    raise SnapshotError(f"encrypted source member is forbidden: {name}")
                if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise SnapshotError(f"unsupported source compression method: {name}")
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_IFMT(mode) != stat.S_IFREG:
                    raise SnapshotError(f"source member must be a regular file: {name}")
                if info.file_size < 0 or info.file_size > MAX_MEMBER_BYTES:
                    raise SnapshotError(f"source member is oversized: {name}")
                advertised_total += info.file_size
                if advertised_total > MAX_TOTAL_BYTES + MAX_LOCK_BYTES:
                    raise SnapshotError("source snapshot exceeds the expanded byte limit")
                names.append(name)
                by_name[name] = info

            lock_info = by_name.get(SOURCE_LOCK_NAME)
            if lock_info is None:
                raise SnapshotError("source snapshot is missing SOURCE-LOCK.json")
            inner_lock = _read_member_bounded(archive, lock_info, MAX_LOCK_BYTES)
            if inner_lock != expected_lock:
                raise SnapshotError("embedded and standalone SOURCE-LOCK.json differ")
            _lock, indexed = _parse_lock(inner_lock)
            expected_names = set(indexed) | {SOURCE_LOCK_NAME}
            if set(names) != expected_names or len(names) != len(expected_names):
                raise SnapshotError("source snapshot inventory does not exactly match SOURCE-LOCK.json")
            for name, item in indexed.items():
                if by_name[name].file_size != item["byteCount"]:
                    raise SnapshotError(f"locked byte count differs from ZIP metadata: {name}")

            destination.parent.mkdir(parents=True, exist_ok=True)
            temp_root = pathlib.Path(
                tempfile.mkdtemp(prefix=f".{destination.name}.verify-", dir=destination.parent)
            )
            verified_total = 0
            for name, item in sorted(indexed.items()):
                raw = _read_member_bounded(archive, by_name[name], item["byteCount"])
                verified_total += len(raw)
                if len(raw) != item["byteCount"]:
                    raise SnapshotError(f"source member ended before its locked size: {name}")
                if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                    raise SnapshotError(f"source member SHA-256 mismatch: {name}")
                target = temp_root.joinpath(*pathlib.PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            (temp_root / SOURCE_LOCK_NAME).write_bytes(inner_lock)
        os.replace(temp_root, destination)
        temp_root = None
        return {
            "fileCount": len(indexed),
            "byteCount": verified_total,
            "lockSha256": hashlib.sha256(expected_lock).hexdigest(),
        }
    except SnapshotError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise SnapshotError(f"source snapshot verification failed: {exc}") from exc
    finally:
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    parser.add_argument("expected_lock")
    parser.add_argument("destination")
    args = parser.parse_args()
    summary = verify_and_extract(args.archive, args.expected_lock, args.destination)
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
