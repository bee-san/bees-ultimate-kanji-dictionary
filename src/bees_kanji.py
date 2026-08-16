"""Bee's Ultimate Kanji Dictionary -- minimal Jiten -> Yomitan generator.

One module owns the whole pipeline: fetch -> normalize -> validate -> build.
Kept small and understandable on purpose. No service layers, no plugins.
"""

import hashlib
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

# --- Reading normalization ---------------------------------------------------

# Katakana block starts 0x30A1; hiragana 0x3041. Same layout, offset by 0x60.
_KATA_TO_HIRA_OFFSET = 0x3041 - 0x30A1


def _katakana_to_hiragana(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:  # katakana range with hiragana counterparts
            out.append(chr(code + _KATA_TO_HIRA_OFFSET))
        else:
            out.append(ch)
    return "".join(out)


def normalize_reading(reading: str) -> str:
    """Normalize a Jiten reading to a bare hiragana stem for matching.

    - trims surrounding whitespace
    - drops KANJIDIC okurigana separator "." and everything after it
    - drops leading/trailing affix marker "-"
    - converts katakana (on readings) to hiragana
    """
    if reading is None:
        return ""
    text = reading.strip()
    if not text:
        return ""
    text = text.replace("-", "")
    if "." in text:
        text = text.split(".", 1)[0]
    return _katakana_to_hiragana(text)


def classify_reading(reading: str, on_readings, kun_readings) -> str:
    """Classify a hiragana word-group reading as On, Kun, or Other."""
    norm = normalize_reading(reading)
    on = {normalize_reading(r) for r in (on_readings or []) if normalize_reading(r)}
    kun = {normalize_reading(r) for r in (kun_readings or []) if normalize_reading(r)}
    if norm in on:
        return "On"
    if norm in kun:
        return "Kun"
    return "Other"


# --- String cleaning ---------------------------------------------------------

_WS = re.compile(r"\s+")
_JUNK = {"missing", "???"}


def clean_text(text):
    """Return a cleaned display string, or None if it is junk.

    Rejects empty strings, case-insensitive 'missing', '???', and any string
    containing an angle bracket (leaked HTML/XML markup).
    """
    if not isinstance(text, str):
        return None
    collapsed = _WS.sub(" ", text).strip()
    if not collapsed:
        return None
    if collapsed.lower() in _JUNK:
        return None
    if "<" in collapsed or ">" in collapsed:
        return None
    return collapsed


def clean_meanings(meanings):
    """Clean a list of meanings: drop junk, dedupe, preserve first-seen order."""
    out = []
    seen = set()
    for m in meanings or []:
        c = clean_text(m)
        if c is None or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


# Furigana bracket notation: base text followed by [reading] segments, e.g.
# "場[ば]所[しょ]" or "測[はか]る" (trailing kana without a bracket).
_FURI_TOKEN = re.compile(r"([^\[\]]+)(?:\[([^\[\]]*)\])?")


def parse_furigana(furigana):
    """Parse Jiten furigana bracket notation into (base, reading) segments.

    Returns a list of (base, reading) tuples, or None if the string is empty
    or contains malformed (unbalanced) ruby brackets.
    """
    if not isinstance(furigana, str) or not furigana.strip():
        return None
    if furigana.count("[") != furigana.count("]"):
        return None
    segments = []
    pos = 0
    for match in _FURI_TOKEN.finditer(furigana):
        if match.start() != pos:  # gap => unparseable
            return None
        pos = match.end()
        base = match.group(1)
        reading = match.group(2) or ""
        if base:
            segments.append((base, reading))
    if pos != len(furigana) or not segments:
        return None
    return segments


# --- Record normalization ----------------------------------------------------

class MalformedPayload(ValueError):
    """Raised when a Jiten payload cannot be trusted / parsed."""


# Selection tuning constants (from the frozen contract).
RANK_CUTOFF = 25000       # drop example words rarer than this
MAX_GROUPS = 3            # at most 3 useful reading groups
MAX_PER_GROUP = 2         # 1-2 examples per group
MAX_EXAMPLES = 6          # never more than 6 examples total


def _clean_example(word):
    """Return a clean example dict, or None if the word is junk."""
    if not isinstance(word, dict):
        return None
    surface = clean_text(word.get("reading"))
    gloss = clean_text(word.get("mainDefinition"))
    rank = word.get("frequencyRank")
    if surface is None or gloss is None:
        return None
    if not isinstance(rank, int) or rank <= 0 or rank > RANK_CUTOFF:
        return None
    ruby = parse_furigana(word.get("readingFurigana"))
    if ruby is None:
        return None
    word_id = word.get("wordId")
    reading_index = word.get("readingIndex", 0)
    if not isinstance(word_id, int):
        return None
    return {
        "surface": surface,
        "gloss": gloss,
        "rank": rank,
        "ruby": ruby,
        "word_id": word_id,
        "reading_index": reading_index if isinstance(reading_index, int) else 0,
    }


def _select_examples(payload, on, kun):
    """Pick up to MAX_GROUPS reading groups, MAX_PER_GROUP words each, capped
    at MAX_EXAMPLES total, chosen by best (lowest) word rank -- never by
    totalWords. Falls back to topWords when grouped candidates run short.
    """
    seen = set()  # (word_id, reading_index) dedup keys, global

    def take(words):
        out = []
        for w in sorted(words, key=lambda e: e["rank"]):
            key = (w["word_id"], w["reading_index"])
            if key in seen:
                continue
            seen.add(key)
            out.append(w)
            if len(out) >= MAX_PER_GROUP:
                break
        return out

    groups = []
    for grp in payload.get("wordsByReading") or []:
        if not isinstance(grp, dict):
            continue
        reading = clean_text(grp.get("reading"))
        if reading is None:
            continue
        cands = [c for c in (_clean_example(w) for w in grp.get("words") or []) if c]
        if not cands:
            continue
        best_rank = min(c["rank"] for c in cands)
        groups.append({
            "reading": reading,
            "reading_class": classify_reading(reading, on, kun),
            "best_rank": best_rank,
            "candidates": cands,
        })

    # Sort reading groups by their best candidate rank (most common first).
    groups.sort(key=lambda g: g["best_rank"])

    result = []
    total = 0
    for g in groups:
        if len(result) >= MAX_GROUPS or total >= MAX_EXAMPLES:
            break
        words = take(g["candidates"])
        if not words:
            continue
        words = words[: max(0, MAX_EXAMPLES - total)]
        if not words:
            break
        result.append({
            "reading": g["reading"],
            "reading_class": g["reading_class"],
            "label": g["reading_class"],
            "words": words,
        })
        total += len(words)

    # Fallback: if grouped selection produced nothing, use topWords.
    if not result:
        cands = [c for c in (_clean_example(w) for w in payload.get("topWords") or []) if c]
        picked = []
        for w in sorted(cands, key=lambda e: e["rank"]):
            key = (w["word_id"], w["reading_index"])
            if key in seen:
                continue
            seen.add(key)
            picked.append(w)
            if len(picked) >= MAX_GROUPS:
                break
        if picked:
            result.append({
                "reading": "",
                "reading_class": "Other",
                "label": "Other",
                "words": picked,
            })
    return result


def normalize_record(payload):
    """Normalize a raw Jiten kanji payload into a clean record dict.

    Raises MalformedPayload if the payload is not a usable kanji object.
    """
    if not isinstance(payload, dict):
        raise MalformedPayload("payload is not an object")
    character = payload.get("character")
    if not isinstance(character, str) or len(character) != 1:
        raise MalformedPayload("missing/invalid single-character 'character'")

    senses = clean_meanings(payload.get("meanings"))
    on = [r for r in (payload.get("onReadings") or []) if isinstance(r, str) and r.strip()]
    kun = [r for r in (payload.get("kunReadings") or []) if isinstance(r, str) and r.strip()]

    rank = payload.get("frequencyRank")
    rank = rank if isinstance(rank, int) and rank > 0 else None

    def _int_or_none(v):
        return v if isinstance(v, int) else None

    return {
        "character": character,
        "keyword": senses[0] if senses else None,
        "senses": senses,
        "on": on,
        "kun": kun,
        "frequency_rank": rank,
        "stroke_count": _int_or_none(payload.get("strokeCount")),
        "grade": _int_or_none(payload.get("grade")),
        "jlpt": _int_or_none(payload.get("jlptLevel")),
        "examples": _select_examples(payload, on, kun),
    }


# --- Yomitan bank builders ---------------------------------------------------

def _jlpt_label(level):
    """Map a numeric JLPT level to Yomitan-style 'N#', else None."""
    if isinstance(level, int) and 1 <= level <= 5:
        return f"N{level}"
    return None


def _ruby_node(segments):
    """Build a structured-content node for one furigana word from ruby segs."""
    content = []
    for base, reading in segments:
        if reading:
            content.append({"tag": "ruby", "content": [base, {"tag": "rt", "content": reading}]})
        else:
            content.append(base)
    return content


def _detail_content(record):
    """Build the structured-content body for a term entry's detail item."""
    body = []

    # Readings line: honest On / Kun lists as supplied.
    reading_bits = []
    if record["on"]:
        reading_bits.append("On: " + "、".join(record["on"]))
    if record["kun"]:
        reading_bits.append("Kun: " + "、".join(record["kun"]))
    if reading_bits:
        body.append({"tag": "div", "content": " / ".join(reading_bits)})

    # Senses line: compact common meanings.
    if record["senses"]:
        body.append({"tag": "div", "content": "; ".join(record["senses"])})

    # Facts line: rank / grade / jlpt / strokes (only known values).
    facts = []
    if record["frequency_rank"] is not None:
        facts.append(f"Rank {record['frequency_rank']}")
    if record["grade"] is not None:
        facts.append(f"Grade {record['grade']}")
    jl = _jlpt_label(record["jlpt"])
    if jl:
        facts.append(f"JLPT {jl}")
    if record["stroke_count"] is not None:
        facts.append(f"{record['stroke_count']} strokes")
    if facts:
        body.append({"tag": "div", "content": " · ".join(facts)})

    # Example words grouped by reading with honest On/Kun/Other labels.
    for group in record["examples"]:
        items = []
        for ex in group["words"]:
            line = _ruby_node(ex["ruby"]) + [" — " + ex["gloss"]]
            items.append({"tag": "li", "content": line})
        if items:
            body.append({"tag": "div", "content": group["label"]})
            body.append({"tag": "ul", "content": items})

    return body


def build_term_entry(record):
    """Build one Yomitan term-bank entry for a normalized kanji record."""
    keyword = record["keyword"] or record["character"]
    glossary = [
        keyword,
        {"type": "structured-content", "content": _detail_content(record)},
    ]
    return [
        record["character"],   # expression
        "",                    # reading: empty for a multi-reading kanji
        "",                    # definition tags
        "",                    # rule identifiers
        0,                     # score (neutral)
        glossary,
        ord(record["character"]),  # sequence = Unicode code point
        "",                    # term tags
    ]


def build_kanji_entry(record):
    """Build one Yomitan kanji-bank native entry for a normalized record."""
    stats = {}
    if record["frequency_rank"] is not None:
        stats["Frequency rank"] = str(record["frequency_rank"])
    if record["grade"] is not None:
        stats["Grade"] = str(record["grade"])
    jl = _jlpt_label(record["jlpt"])
    if jl:
        stats["JLPT"] = jl
    if record["stroke_count"] is not None:
        stats["Strokes"] = str(record["stroke_count"])
    return [
        record["character"],
        " ".join(record["on"]),
        " ".join(record["kun"]),
        "",                    # tags
        list(record["senses"]),
        stats,
    ]


def build_term_meta(record):
    """Frequency meta for the term bank, or None when rank is unknown."""
    if record["frequency_rank"] is None:
        return None
    return [record["character"], "freq", record["frequency_rank"]]


def build_kanji_meta(record):
    """Frequency meta for the kanji bank, or None when rank is unknown."""
    if record["frequency_rank"] is None:
        return None
    return [record["character"], "freq", record["frequency_rank"]]


def build_alias_term_entry(alias, canonical):
    """Build a term-only alias entry pointing an old form to its canonical form.

    Used for compatibility characters Jiten does not serve (e.g. 髙 -> 高).
    Contributes no native-kanji or frequency data.
    """
    detail = {
        "type": "structured-content",
        "content": [{"tag": "div", "content": f"Variant form of {canonical}"}],
    }
    return [
        alias,
        "",
        "",
        "",
        0,
        [f"variant of {canonical}", detail],
        ord(alias),
        "",
    ]


def build_banks(records, aliases=None):
    """Assemble the four Yomitan banks from normalized records + term aliases.

    Records are sorted deterministically by Unicode code point. Aliases (old
    form -> canonical form) contribute only term-bank entries. Returns a dict
    with term_bank, term_meta_bank, kanji_bank, kanji_meta_bank.
    """
    aliases = aliases or {}
    ordered = sorted(records, key=lambda r: ord(r["character"]))

    term_bank = []
    term_meta_bank = []
    kanji_bank = []
    kanji_meta_bank = []

    for r in ordered:
        term_bank.append(build_term_entry(r))
        tm = build_term_meta(r)
        if tm is not None:
            term_meta_bank.append(tm)
        kanji_bank.append(build_kanji_entry(r))
        km = build_kanji_meta(r)
        if km is not None:
            kanji_meta_bank.append(km)

    for alias in sorted(aliases, key=ord):
        term_bank.append(build_alias_term_entry(alias, aliases[alias]))

    # Keep term bank ordered by code point including aliases.
    term_bank.sort(key=lambda e: ord(e[0]))

    return {
        "term_bank": term_bank,
        "term_meta_bank": term_meta_bank,
        "kanji_bank": kanji_bank,
        "kanji_meta_bank": kanji_meta_bank,
    }


# --- Serialization, index, hashing, ZIP --------------------------------------

TITLE = "Bee's Ultimate Kanji Dictionary"
REPO = "bee-san/bees-ultimate-kanji-dictionary"
ZIP_NAME = "bees-ultimate-kanji-dictionary.zip"

ATTRIBUTION = (
    "Dictionary data derived from Jiten (https://jiten.moe), using "
    "JMdict/KANJIDIC data from the Electronic Dictionary Research and "
    "Development Group (EDRDG). Data is redistributed under CC BY-SA 4.0; "
    "see https://creativecommons.org/licenses/by-sa/4.0/ and "
    "https://www.edrdg.org/edrdg/licence.html."
)

LICENSE_DATA_TEXT = (
    "Dictionary data derived from Jiten (https://jiten.moe), using JMdict/KANJIDIC\n"
    "data from the Electronic Dictionary Research and Development Group (EDRDG).\n"
    "Data is redistributed under CC BY-SA 4.0; see\n"
    "https://creativecommons.org/licenses/by-sa/4.0/ and\n"
    "https://www.edrdg.org/edrdg/licence.html.\n"
)

BANK_FILES = [
    ("term_bank_1.json", "term_bank"),
    ("term_meta_bank_1.json", "term_meta_bank"),
    ("kanji_bank_1.json", "kanji_bank"),
    ("kanji_meta_bank_1.json", "kanji_meta_bank"),
]


def dump_json(obj):
    """Serialize to canonical, deterministic UTF-8 JSON (no ASCII escaping)."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_index(revision):
    """Build the Yomitan index.json object with the frozen stable fields."""
    return {
        "title": TITLE,
        "format": 3,
        "revision": revision,
        "sequenced": False,
        "author": "bee-san",
        "isUpdatable": True,
        "indexUrl": f"https://raw.githubusercontent.com/{REPO}/main/dist/index.json",
        "downloadUrl": f"https://github.com/{REPO}/releases/latest/download/{ZIP_NAME}",
        "url": f"https://github.com/{REPO}",
        "description": "Minimal kanji dictionary generated from Jiten data.",
        "attribution": ATTRIBUTION,
        "sourceLanguage": "ja",
        "targetLanguage": "en",
        "frequencyMode": "rank-based",
    }


def content_hash(banks):
    """SHA-256 over the normalized bank content only (revision-independent)."""
    payload = dump_json({name: banks[name] for _, name in BANK_FILES})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Fixed ZIP member metadata for reproducible archives.
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def _zip_member(name, data):
    info = zipfile.ZipInfo(filename=name, date_time=_ZIP_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o644 & 0xFFFF) << 16  # -rw-r--r--
    info.create_system = 3  # unix
    return info, data


def build_zip(banks, revision):
    """Build a deterministic Yomitan ZIP (bytes) with all members at the root.

    Member order, timestamps, and permissions are fixed so two builds from the
    same inputs are byte-identical.
    """
    members = [("index.json", dump_json(build_index(revision)))]
    for filename, key in BANK_FILES:
        members.append((filename, dump_json(banks[key])))
    members.append(("LICENSE-data.txt", LICENSE_DATA_TEXT))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in members:
            info, data = _zip_member(name, text.encode("utf-8"))
            zf.writestr(info, data)
    return buf.getvalue()


# --- Daily acquisition with a resumable per-character cache ------------------

API_BASE = "https://api.jiten.moe/api/kanji"
SITEMAP_URL = f"{API_BASE}/sitemap-characters"
USER_AGENT = "bees-ultimate-kanji-dictionary (+https://github.com/bee-san/bees-ultimate-kanji-dictionary)"
TIMEOUT_SECONDS = 30
MAX_RETRIES = 2  # bounded retries for transport / 429 / 5xx only


class NotFound(Exception):
    """The Jiten API returned 404 for a character (skip it)."""


def cache_filename(character):
    """Filesystem-safe cache filename for a character (by code points)."""
    codepoints = "_".join(f"{ord(c):x}" for c in character)
    return f"{codepoints}.json"


def http_get_json(url):
    """GET a URL and parse JSON, with bounded retries for transient errors.

    Raises NotFound on 404. Retries only transport errors, 429, and 5xx.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_err = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(url) from e
            if e.code == 429 or 500 <= e.code < 600:
                last_err = e
            else:
                raise
        except urllib.error.URLError as e:
            last_err = e
        if attempt < MAX_RETRIES:
            time.sleep(2 ** attempt)  # small backoff: 1s, 2s
    raise last_err if last_err is not None else RuntimeError(f"failed to GET {url}")


def fetch_kanji(character):
    """Fetch a single kanji payload from the live Jiten API."""
    return http_get_json(f"{API_BASE}/{urllib.parse.quote(character)}")


def fetch_sitemap():
    """Fetch the list of characters from the Jiten sitemap endpoint."""
    data = http_get_json(SITEMAP_URL)
    if not isinstance(data, list):
        raise MalformedPayload("sitemap is not a list")
    return [c for c in data if isinstance(c, str) and c]


def fetch_all(characters, cache_dir, date, fetcher=fetch_kanji):
    """Fetch payloads for characters, using a resumable dated on-disk cache.

    Cache layout: cache_dir/DATE/<codepoints>.json. Files already present for
    DATE are reused (no fetcher call). Only missing characters are fetched
    sequentially. 404s are skipped (not cached, not returned). Returns an
    ordered dict {character: payload} for the characters that produced data.
    """
    import pathlib as _pl

    day_dir = _pl.Path(cache_dir) / date
    day_dir.mkdir(parents=True, exist_ok=True)

    out = {}
    for char in characters:
        path = day_dir / cache_filename(char)
        if path.exists():
            try:
                out[char] = json.loads(path.read_text(encoding="utf-8"))
                continue
            except (ValueError, OSError):
                pass  # corrupt cache file -> refetch
        try:
            payload = fetcher(char)
        except NotFound:
            continue  # skip 404s
        # atomic-ish write: temp then replace
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        out[char] = payload
    return out


# --- Build pipeline and revision decision ------------------------------------

def run_build(characters, cache_dir, date, aliases=None, fetcher=fetch_kanji):
    """Fetch (via cache) -> normalize -> build banks + ZIP for the given date.

    Returns {records, banks, content_hash, zip_bytes(unrevised placeholder)}.
    The ZIP here uses a placeholder revision; the caller stamps the final
    revision after the change decision. content_hash is revision-independent.
    """
    payloads = fetch_all(characters, cache_dir, date, fetcher)
    records = []
    for char in characters:
        payload = payloads.get(char)
        if payload is None:
            continue
        try:
            records.append(normalize_record(payload))
        except MalformedPayload:
            continue  # skip characters whose payload cannot be trusted
    banks = build_banks(records, aliases or {})
    chash = content_hash(banks)
    return {
        "records": records,
        "banks": banks,
        "content_hash": chash,
        "zip_bytes": build_zip(banks, date_to_revision(date)),
    }


def date_to_revision(date):
    """Convert a UTC date 'YYYY-MM-DD' to a dot-numeric revision 'YYYY.MM.DD'."""
    return date.replace("-", ".")


def decide_revision(content_hash, previous_hash, date, previous_revision):
    """Decide the revision string, or None when content is unchanged.

    - unchanged content -> None (publish nothing)
    - changed, new day   -> that day's 'YYYY.MM.DD'
    - changed, same base day as previous revision -> append a monotonic
      dot-numeric suffix so the revision strictly increases
    """
    if previous_hash is not None and content_hash == previous_hash:
        return None
    base = date_to_revision(date)
    if previous_revision is None:
        return base
    if previous_revision == base or previous_revision.startswith(base + "."):
        # Same UTC day already released: bump the trailing counter.
        parts = previous_revision.split(".")
        if len(parts) == 4:
            return f"{base}.{int(parts[3]) + 1}"
        return f"{base}.1"
    return base


def _load_previous(dist_index_path):
    """Return (previous_revision, previous_content_hash) from dist/index.json.

    The content hash is stored alongside the index as dist/content.sha256 so we
    can detect changes without re-downloading the released ZIP.
    """
    import pathlib as _pl

    idx = _pl.Path(dist_index_path)
    prev_rev = None
    if idx.exists():
        try:
            prev_rev = json.loads(idx.read_text(encoding="utf-8")).get("revision")
        except (ValueError, OSError):
            prev_rev = None
    prev_hash = None
    hpath = idx.with_name("content.sha256")
    if hpath.exists():
        try:
            prev_hash = hpath.read_text(encoding="utf-8").strip() or None
        except OSError:
            prev_hash = None
    return prev_rev, prev_hash


def main(argv=None):
    """Single command: fetch/refresh -> normalize -> validate -> build.

    Writes the ZIP + SHA256SUMS to the output dir and refreshes dist/index.json
    (plus dist/content.sha256) only when normalized content changed.
    """
    import argparse
    import datetime
    import pathlib as _pl
    import subprocess

    parser = argparse.ArgumentParser(description="Build Bee's Ultimate Kanji Dictionary")
    parser.add_argument("--cache", default="cache")
    parser.add_argument("--out", default="build")
    parser.add_argument("--dist", default="dist")
    parser.add_argument("--date", default=None, help="UTC date YYYY-MM-DD (default: today)")
    parser.add_argument("--limit", type=int, default=None, help="limit characters (debug)")
    parser.add_argument("--offline", action="store_true", help="use cache only; no network")
    args = parser.parse_args(argv)

    date = args.date or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    out_dir = _pl.Path(args.out)
    dist_dir = _pl.Path(args.dist)
    out_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)

    print(f"[build] date={date}")
    characters = fetch_sitemap()
    if args.limit:
        characters = characters[: args.limit]
    print(f"[build] {len(characters)} characters from sitemap")

    fetcher = _offline_fetcher(args.cache, date) if args.offline else fetch_kanji
    result = run_build(characters, args.cache, date, DEFAULT_ALIASES, fetcher)
    print(f"[build] {len(result['records'])} clean records; hash={result['content_hash'][:12]}")

    prev_rev, prev_hash = _load_previous(dist_dir / "index.json")
    revision = decide_revision(result["content_hash"], prev_hash, date, prev_rev)
    if revision is None:
        print("[build] content unchanged; nothing to publish")
        return 0

    zip_bytes = build_zip(result["banks"], revision)
    zip_path = out_dir / ZIP_NAME
    zip_path.write_bytes(zip_bytes)

    digest = hashlib.sha256(zip_bytes).hexdigest()
    (out_dir / "SHA256SUMS").write_text(f"{digest}  {ZIP_NAME}\n", encoding="utf-8")

    (dist_dir / "index.json").write_text(
        dump_json(build_index(revision)) + "\n", encoding="utf-8"
    )
    (dist_dir / "content.sha256").write_text(result["content_hash"] + "\n", encoding="utf-8")

    # Validate the built banks against the official schemas via Node.
    script = _pl.Path(__file__).resolve().parent.parent / "scripts" / "validate_yomitan.mjs"
    if script.exists():
        rc = subprocess.call(["node", str(script), str(zip_path)])
        if rc != 0:
            raise SystemExit("Yomitan schema validation failed")

    print(f"[build] revision={revision} zip={zip_path} sha256={digest}")
    return 0


def _offline_fetcher(cache_dir, date):
    """Return a fetcher that only reads the dated cache (raises if missing)."""
    import pathlib as _pl

    day = _pl.Path(cache_dir) / date

    def fetcher(char):
        path = day / cache_filename(char)
        if not path.exists():
            raise NotFound(char)
        return json.loads(path.read_text(encoding="utf-8"))

    return fetcher


# Compatibility aliases: forms Jiten does not serve, mapped to canonical forms.
DEFAULT_ALIASES = {"髙": "高"}


if __name__ == "__main__":
    raise SystemExit(main())
