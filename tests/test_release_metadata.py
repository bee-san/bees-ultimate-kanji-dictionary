"""Strict non-executable release-candidate metadata tests."""

import pytest

from scripts.release_metadata import MetadataError, create_metadata, parse_metadata


def test_release_metadata_round_trip_is_strict_json(tmp_path):
    path = tmp_path / "METADATA.json"
    create_metadata(path, "2026-08-19", "2026.08.19.1", "a" * 40)
    assert parse_metadata(path) == {
        "date": "2026-08-19",
        "revision": "2026.08.19.1",
        "commit": "a" * 40,
    }


@pytest.mark.parametrize(
    "raw",
    [
        b'{"date":"2026-08-19","revision":"2026.08.19.1","commit":"' + b"a" * 40 + b'","extra":1}',
        b'{"date":"2026-08-19","date":"2026-08-20","revision":"2026.08.19.1","commit":"' + b"a" * 40 + b'"}',
        b'{"date":"2026-08-19","revision":"2026.08.20","commit":"' + b"a" * 40 + b'"}',
        b'{"date":"2026-08-19","revision":"2026.08.19.1","commit":"$(touch /tmp/pwned)"}',
        b'date=2026-08-19\nrevision=2026.08.19.1\ncommit=' + b"a" * 40 + b"\n",
    ],
)
def test_release_metadata_rejects_unknown_duplicate_mismatched_and_shell_content(
    tmp_path, raw
):
    path = tmp_path / "METADATA.json"
    path.write_bytes(raw)
    with pytest.raises(MetadataError):
        parse_metadata(path)
