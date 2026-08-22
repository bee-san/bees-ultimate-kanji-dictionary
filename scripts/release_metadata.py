#!/usr/bin/env python3
"""Create and parse strict, non-executable release candidate metadata."""

import argparse
import datetime
import json
import os
import pathlib
import re

MAX_METADATA_BYTES = 4096
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REVISION_RE = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})(?:\.([1-9][0-9]*))?$")


class MetadataError(RuntimeError):
    """Release metadata is malformed, ambiguous, or inconsistent."""


def _duplicate_safe_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MetadataError(f"duplicate metadata key: {key}")
        result[key] = value
    return result


def _validate(values):
    if not isinstance(values, dict) or set(values) != {"date", "revision", "commit"}:
        raise MetadataError("metadata must contain exactly date, revision, and commit")
    date = values["date"]
    revision = values["revision"]
    commit = values["commit"]
    if not all(isinstance(value, str) for value in (date, revision, commit)):
        raise MetadataError("metadata values must be strings")
    try:
        parsed_date = datetime.date.fromisoformat(date)
    except ValueError as exc:
        raise MetadataError("metadata date is invalid") from exc
    if parsed_date.isoformat() != date:
        raise MetadataError("metadata date is not canonical")
    match = REVISION_RE.fullmatch(revision)
    if match is None or ".".join(match.group(1, 2, 3)) != date.replace("-", "."):
        raise MetadataError("metadata revision does not match its date")
    if COMMIT_RE.fullmatch(commit) is None:
        raise MetadataError("metadata commit is not a canonical full SHA")
    return {"date": date, "revision": revision, "commit": commit}


def create_metadata(path, date, revision, commit):
    values = _validate({"date": date, "revision": revision, "commit": commit})
    raw = (json.dumps(values, sort_keys=True, separators=(",", ":")) + "\n").encode()
    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(raw)
    os.replace(temporary, target)


def parse_metadata(path):
    path = pathlib.Path(path)
    if path.is_symlink() or not path.is_file():
        raise MetadataError("metadata path is missing or invalid")
    if path.stat().st_size > MAX_METADATA_BYTES:
        raise MetadataError("metadata exceeds its byte limit")
    with path.open("rb") as handle:
        raw = handle.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise MetadataError("metadata exceeds its byte limit")
    try:
        values = json.loads(raw.decode("utf-8"), object_pairs_hook=_duplicate_safe_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MetadataError("metadata is not strict UTF-8 JSON") from exc
    return _validate(values)


def _write_outputs(path, values):
    with pathlib.Path(path).open("a", encoding="utf-8") as handle:
        handle.writelines(
            f"{key}={values[key]}\n" for key in ("date", "revision", "commit")
        )


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("path")
    create.add_argument("--date", required=True)
    create.add_argument("--revision", required=True)
    create.add_argument("--commit", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("path")
    verify.add_argument("--date", required=True)
    verify.add_argument("--revision", required=True)
    verify.add_argument("--output", required=True)
    args = parser.parse_args()

    if args.command == "create":
        create_metadata(args.path, args.date, args.revision, args.commit)
        return
    values = parse_metadata(args.path)
    if values["date"] != args.date or values["revision"] != args.revision:
        raise MetadataError("metadata does not match the workflow coordinate")
    _write_outputs(args.output, values)


if __name__ == "__main__":
    main()
