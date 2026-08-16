"""RED tests: deterministic serialization, revision hashing, index, and ZIP.

Two builds from identical inputs must be byte-identical, including the ZIP
hash. index.json must carry the exact stable URLs, rank mode, and the given
revision. content_hash must depend only on normalized bank content, not on the
revision string or build time.
"""
import hashlib
import io
import json
import pathlib
import zipfile

import bees_kanji as bk

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures"
CHARS = "場男事生行高"


def banks():
    recs = [
        bk.normalize_record(json.loads((FIX / f"{c}.json").read_text(encoding="utf-8")))
        for c in CHARS
    ]
    return bk.build_banks(recs, aliases={"髙": "高"})


def test_canonical_json_is_stable_utf8():
    obj = {"b": 1, "a": "場"}
    out1 = bk.dump_json(obj)
    out2 = bk.dump_json(obj)
    assert out1 == out2
    assert "場" in out1          # UTF-8, not \uXXXX escaping
    assert "\\u" not in out1


def test_index_has_exact_stable_fields():
    idx = bk.build_index("2026.08.16")
    assert idx["title"] == "Bee's Ultimate Kanji Dictionary"
    assert idx["format"] == 3
    assert idx["revision"] == "2026.08.16"
    assert idx["isUpdatable"] is True
    assert idx["frequencyMode"] == "rank-based"
    assert idx["indexUrl"] == (
        "https://raw.githubusercontent.com/bee-san/"
        "bees-ultimate-kanji-dictionary/main/dist/index.json"
    )
    assert idx["downloadUrl"] == (
        "https://github.com/bee-san/bees-ultimate-kanji-dictionary/"
        "releases/latest/download/bees-ultimate-kanji-dictionary.zip"
    )
    assert idx["url"] == "https://github.com/bee-san/bees-ultimate-kanji-dictionary"
    assert idx["sourceLanguage"] == "ja"
    assert idx["targetLanguage"] == "en"
    assert "CC BY-SA 4.0" in idx["attribution"]


def test_content_hash_ignores_revision():
    b = banks()
    h1 = bk.content_hash(b)
    h2 = bk.content_hash(b)
    assert h1 == h2
    assert isinstance(h1, str) and len(h1) == 64  # sha256 hex


def test_zip_is_byte_identical_across_builds():
    b = banks()
    z1 = bk.build_zip(b, revision="2026.08.16")
    z2 = bk.build_zip(b, revision="2026.08.16")
    assert z1 == z2
    assert hashlib.sha256(z1).hexdigest() == hashlib.sha256(z2).hexdigest()


def test_zip_members_at_root_with_expected_names():
    b = banks()
    z = bk.build_zip(b, revision="2026.08.16")
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        names = zf.namelist()
    expected = {
        "index.json",
        "term_bank_1.json",
        "term_meta_bank_1.json",
        "kanji_bank_1.json",
        "kanji_meta_bank_1.json",
        "LICENSE-data.txt",
    }
    assert set(names) == expected
    for n in names:
        assert "/" not in n  # all at ZIP root, no subfolders


def test_zip_index_matches_dist_index():
    b = banks()
    z = bk.build_zip(b, revision="2026.08.16")
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        zip_index = json.loads(zf.read("index.json"))
    dist_index = bk.build_index("2026.08.16")
    assert zip_index == dist_index
