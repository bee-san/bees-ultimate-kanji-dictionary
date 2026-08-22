"""Bee's Ultimate Kanji Dictionary -- minimal Jiten -> Yomitan generator.

One module owns the whole pipeline: fetch -> normalize -> validate -> build.
Kept small and understandable on purpose. No service layers, no plugins.
"""

import csv
import functools
import hashlib
import importlib.metadata
import io
import json
import math
import pathlib
import re
import stat
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass

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
    if not isinstance(meanings, (list, tuple)):
        return []
    out = []
    seen = set()
    for m in meanings or []:
        c = clean_text(m)
        if c is None or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def clean_strings(values):
    """Clean and deduplicate display strings while preserving their order."""
    if not isinstance(values, (list, tuple)):
        return []
    out = []
    seen = set()
    for value in values or []:
        cleaned = clean_text(value)
        if cleaned is None or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
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


def _select_global_words(payload, limit=6):
    """Pick the globally highest-frequency, de-duplicated example words.

    Draws candidates from ``topWords`` and every ``wordsByReading`` group,
    cleans them, de-duplicates by (word_id, reading_index) keeping the lowest
    (best) frequency rank, and returns the ``limit`` best by Jiten frequency
    rank. Deterministic ties/fallbacks: within an equal rank the order is
    (rank, word_id, reading_index, surface, gloss). This is a GLOBAL selection
    across all readings -- NOT the per-reading example groups -- so the compact
    card can surface the single most useful vocabulary regardless of reading.
    """
    candidates = []
    sources = [payload.get("topWords") or []]
    sources.extend(
        (group.get("words") or [])
        for group in payload.get("wordsByReading") or []
        if isinstance(group, dict)
    )
    for source in sources:
        for item in source:
            clean = _clean_example(item)
            if clean is not None:
                candidates.append(clean)

    best = {}
    for item in candidates:
        key = (item["word_id"], item["reading_index"])
        prior = best.get(key)
        order = (item["rank"], item["surface"], item["gloss"])
        if prior is None or order < (prior["rank"], prior["surface"], prior["gloss"]):
            best[key] = item

    return sorted(
        best.values(),
        key=lambda item: (
            item["rank"], item["word_id"], item["reading_index"],
            item["surface"], item["gloss"],
        ),
    )[:limit]


def _reading_entry_counts(payload, on, kun):
    """Extract the complete Jiten vocabulary-entry counts per reading group.

    For every ``wordsByReading`` group, accept ``totalWords`` ONLY when it is a
    genuine positive integer -- booleans, missing values, zero, negatives,
    strings, floats, and non-finite values are all rejected. Counts are
    aggregated by the NORMALIZED reading label (so katakana on-readings collapse
    onto their hiragana stem and never silently drop a duplicate), never merely
    by On/Kun/Other class. Each retained reading carries its class as secondary
    text only. The result is deterministic: ordered by descending count, then
    normalized reading, then first source position.

    This is the complete Jiten group count (``TotalWords = g.Count()``), NOT the
    handful of preview words in ``words`` and NOT the few examples selected for
    display; it is the truthful denominator for the reading-share statistic.
    """
    counts = {}
    order = {}
    seen = 0
    for grp in payload.get("wordsByReading") or []:
        if not isinstance(grp, dict):
            continue
        total = grp.get("totalWords")
        # reject bool (a subclass of int), non-int, and non-positive values
        if isinstance(total, bool) or not isinstance(total, int) or total <= 0:
            continue
        reading = normalize_reading(grp.get("reading"))
        if not reading:
            continue
        if reading not in counts:
            counts[reading] = 0
            order[reading] = seen
            seen += 1
        counts[reading] += total

    ordered = sorted(
        counts, key=lambda r: (-counts[r], r, order[r])
    )
    return [
        {
            "reading": r,
            "count": counts[r],
            "reading_class": classify_reading(r, on, kun),
        }
        for r in ordered
    ]


# --- Jiten bulk rank -> per-reading frequency weights -----------------------

JITEN_FREQUENCY_CSV_URL = (
    "https://api.jiten.moe/api/frequency-list/download?downloadType=csv"
)
JITEN_FREQUENCY_ASSET_NAME = "jiten-global-frequency.csv"
MAX_JITEN_FREQUENCY_BYTES = 64 * 1024 * 1024
MAX_JITEN_FREQUENCY_RANK = 10_000_000
MAX_JITEN_FREQUENCY_ROWS = 600_000
MAX_JITEN_FREQUENCY_SURFACE_LENGTH = 128
MAX_JITEN_FREQUENCY_READING_LENGTH = 256
FREQUENCY_TAIL_START = 100_000


def _normalize_frequency_text(text):
    """Normalize a bulk-list surface/reading without dropping okurigana."""
    if not isinstance(text, str):
        return ""
    return _katakana_to_hiragana(unicodedata.normalize("NFKC", text).strip())


def parse_jiten_frequency_csv_with_stats(text):
    """Parse and conservatively deduplicate Jiten's Global frequency CSV.

    Exact duplicate triples collapse to one row. If the same ``(Word, Form)``
    pair has conflicting ranks, every row for that pair is excluded because the
    export does not carry a lossless word-form identity with which to resolve it.
    """
    if not isinstance(text, str):
        raise MalformedPayload("Jiten frequency CSV is not text")
    try:
        reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    except csv.Error as exc:
        raise MalformedPayload("Jiten frequency CSV cannot be parsed") from exc
    if reader.fieldnames != ["Word", "Form", "Rank"]:
        raise MalformedPayload("Jiten frequency CSV header must be Word,Form,Rank")

    unique_rows = []
    seen_triples = set()
    pair_ranks = {}
    source_rows = 0
    exact_duplicates = 0
    try:
        for row_number, source_row in enumerate(reader, 1):
            if row_number > MAX_JITEN_FREQUENCY_ROWS:
                raise MalformedPayload("Jiten frequency CSV exceeds the row limit")
            source_rows += 1
            if None in source_row or any(value is None for value in source_row.values()):
                raise MalformedPayload("Jiten frequency CSV contains a malformed row")
            surface = source_row.get("Word")
            reading = source_row.get("Form")
            raw_rank = source_row.get("Rank")
            if not isinstance(surface, str) or not surface.strip():
                raise MalformedPayload("Jiten frequency CSV contains an empty Word")
            if not isinstance(reading, str) or not reading.strip():
                raise MalformedPayload("Jiten frequency CSV contains an empty Form")
            surface = unicodedata.normalize("NFKC", surface.strip())
            reading = _normalize_frequency_text(reading)
            if not surface or not reading:
                raise MalformedPayload("Jiten frequency CSV contains an empty normalized form")
            if len(surface) > MAX_JITEN_FREQUENCY_SURFACE_LENGTH:
                raise MalformedPayload("Jiten frequency CSV Word exceeds the length limit")
            if len(reading) > MAX_JITEN_FREQUENCY_READING_LENGTH:
                raise MalformedPayload("Jiten frequency CSV Form exceeds the length limit")
            if not isinstance(raw_rank, str) or re.fullmatch(r"[1-9][0-9]*", raw_rank) is None:
                raise MalformedPayload("Jiten frequency CSV contains an invalid Rank")
            rank = int(raw_rank)
            if rank <= 0 or rank > MAX_JITEN_FREQUENCY_RANK:
                raise MalformedPayload("Jiten frequency CSV Rank is outside the accepted range")
            row = (surface, reading, rank)
            if row in seen_triples:
                exact_duplicates += 1
                continue
            seen_triples.add(row)
            unique_rows.append(row)
            pair_ranks.setdefault((surface, reading), set()).add(rank)
    except csv.Error as exc:
        raise MalformedPayload("Jiten frequency CSV contains malformed quoting") from exc

    conflicting_pairs = {
        pair for pair, ranks in pair_ranks.items() if len(ranks) > 1
    }
    excluded_conflicting_rows = sum(
        1 for surface, reading, _rank in unique_rows
        if (surface, reading) in conflicting_pairs
    )
    rows = [
        row for row in unique_rows
        if (row[0], row[1]) not in conflicting_pairs
    ]
    if not rows:
        raise MalformedPayload("Jiten frequency CSV contains no unambiguous rows")
    stats = {
        "sourceRows": source_rows,
        "exactDuplicateRows": exact_duplicates,
        "conflictingSurfaceReadingPairs": len(conflicting_pairs),
        "excludedConflictingRows": excluded_conflicting_rows,
        "rows": len(rows),
    }
    return rows, stats


def parse_jiten_frequency_csv(text):
    """Return strictly parsed, conflict-free Global frequency rows."""
    return parse_jiten_frequency_csv_with_stats(text)[0]


@functools.lru_cache(maxsize=8192)
def _normalized_frequency_options(options):
    return tuple(sorted({
        normalized
        for option in options
        if (normalized := _normalize_frequency_text(option))
    }, key=lambda value: (-len(value), value)))


def _is_frequency_kana(character):
    code = ord(character)
    return (
        0x3040 <= code <= 0x309F
        or 0x30A0 <= code <= 0x30FF
        or character == "ー"
    )


def _is_han_character(character):
    """Return whether a Han ideograph must be aligned rather than anchored."""
    code = ord(character)
    return (
        0x2E80 <= code <= 0x2EFF  # CJK radicals supplement
        or 0x2F00 <= code <= 0x2FDF  # Kangxi radicals
        or code in {0x3005, 0x3007}  # iteration mark and ideographic zero
        or 0x3021 <= code <= 0x3029  # Hangzhou numerals
        or 0x3038 <= code <= 0x303B  # ideographic numerals/iteration marks
        or 0x31C0 <= code <= 0x31EF  # CJK strokes
        or 0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x16FE2 <= code <= 0x16FE3  # ideographic marks
        or 0x16FF0 <= code <= 0x16FF1  # ideographic reading marks
        or 0x20000 <= code <= 0x2EE5F  # CJK Extensions B through I
        or 0x2F800 <= code <= 0x2FA1F  # compatibility supplement
        or 0x30000 <= code <= 0x3347F  # CJK Extensions G through J
        or character in {"々", "〆"}
    )


_RENDAKU = dict(zip(
    "かきくけこさしすせそたちつてとはひふへほ",
    "がぎぐげござじずぜぞだぢづでどばびぶべぼ",
))
_HANDAKUTEN = dict(zip("はひふへほ", "ぱぴぷぺぽ"))


def _kanjidic_compositional_options(payload, allowed_labels):
    """Port Jiten's KANJIDIC candidate inventory for multi-kanji runs."""
    readings = []
    for raw in payload.get("onReadings") or []:
        if not isinstance(raw, str):
            continue
        clean = "".join(
            character for character in unicodedata.normalize("NFKC", raw)
            if _is_frequency_kana(character) or character == "."
        )
        candidate = _katakana_to_hiragana(clean).replace(".", "")
        if candidate:
            readings.append(candidate)
    for raw in payload.get("kunReadings") or []:
        if not isinstance(raw, str):
            continue
        clean = "".join(
            character for character in unicodedata.normalize("NFKC", raw)
            if _is_frequency_kana(character) or character == "."
        )
        stem = _katakana_to_hiragana(clean).split(".", 1)[0]
        if stem:
            readings.append(stem)

    distinct = list(dict.fromkeys(readings))
    variants = []
    for reading in distinct:
        if reading[0] in _RENDAKU:
            variants.append(_RENDAKU[reading[0]] + reading[1:])
        if reading[0] in _HANDAKUTEN:
            variants.append(_HANDAKUTEN[reading[0]] + reading[1:])
        if len(reading) >= 2 and reading[-1] in "くきちつ":
            variants.append(reading[:-1] + "っ")
    allowed = set(allowed_labels)
    return _normalized_frequency_options(tuple(
        reading for reading in dict.fromkeys(distinct + variants)
        if reading in allowed
    ))


def _frequency_surface_tokens(surface, normalized_options):
    tokens = []
    previous_kanji = None
    for surface_index, character in enumerate(surface):
        if character in normalized_options:
            tokens.append((surface_index, character, None))
            previous_kanji = character
        elif character == "々" and previous_kanji in normalized_options:
            tokens.append((surface_index, previous_kanji, None))
        elif _is_han_character(character):
            # An unknown Han glyph makes this segmentation incomplete. It must
            # never consume a mixed-script Form character as a literal anchor.
            return None
        else:
            tokens.append((surface_index, None, _normalize_frequency_text(character)))
            previous_kanji = None
    return tokens


def _align_frequency_form_normalized(
    surface, full_reading, normalized_options, compositional_options=None
):
    """Align one form, using KANJIDIC candidates inside multi-kanji runs."""
    surface = unicodedata.normalize("NFKC", surface) if isinstance(surface, str) else ""
    reading = _normalize_frequency_text(full_reading)
    if not surface or not reading:
        return None

    tokens = _frequency_surface_tokens(surface, normalized_options)
    if tokens is None:
        return None
    run_lengths = [0] * len(tokens)
    start = 0
    while start < len(tokens):
        if tokens[start][1] is None:
            start += 1
            continue
        end = start
        while end < len(tokens) and tokens[end][1] is not None:
            end += 1
        for index in range(start, end):
            run_lengths[index] = end - start
        start = end

    # (reading offset, assignments). A cap keeps adversarial ambiguity bounded;
    # anything beyond it is omitted rather than guessed.
    states = {(0, ())}
    for token_index, (surface_index, source_character, literal) in enumerate(tokens):
        next_states = set()
        for reading_index, assignments in states:
            if source_character is not None:
                inventory = normalized_options
                if run_lengths[token_index] > 1 and compositional_options is not None:
                    inventory = compositional_options
                for option in inventory.get(source_character, ()):
                    if reading.startswith(option, reading_index):
                        next_states.add((
                            reading_index + len(option),
                            assignments + ((surface_index, source_character, option),),
                        ))
            elif literal and reading.startswith(literal, reading_index):
                next_states.add((reading_index + len(literal), assignments))
            if len(next_states) > 64:
                return None
        if not next_states:
            return None
        states = next_states

    solutions = {
        assignments for reading_index, assignments in states
        if reading_index == len(reading)
    }
    return next(iter(solutions)) if len(solutions) == 1 else None


def align_frequency_form(
    surface, full_reading, reading_options, compositional_options=None
):
    """Return one complete per-kanji reading alignment, else ``None``.

    Curated endpoint group labels are accepted for isolated kanji runs. A caller
    can supply Jiten/KANJIDIC-derived ``compositional_options`` for compounds;
    rows with no complete solution or multiple segmentations are omitted.
    """
    normalized_options = {}
    normalized_compositional = {}
    surface_characters = set(surface) if isinstance(surface, str) else set()
    for character in surface_characters:
        options = reading_options.get(character)
        if options:
            normalized_options[character] = _normalized_frequency_options(tuple(options))
        if compositional_options is not None:
            compound = compositional_options.get(character)
            if compound:
                normalized_compositional[character] = _normalized_frequency_options(
                    tuple(compound)
                )
    return _align_frequency_form_normalized(
        surface,
        full_reading,
        normalized_options,
        normalized_compositional if compositional_options is not None else None,
    )


def rank_frequency_weight(rank):
    """Convert a Jiten global ordinal rank into a finite importance weight.

    This is deliberately a *rank-derived weight*, not an occurrence estimate:
    inverse square root keeps the high-frequency head useful without letting the
    first few terms consume the chart, and a quadratic multiplier suppresses the
    unverifiable long tail beyond rank 100,000.
    """
    if isinstance(rank, bool) or not isinstance(rank, int):
        return 0.0
    if rank <= 0 or rank > MAX_JITEN_FREQUENCY_RANK:
        return 0.0
    try:
        weight = 1.0 / math.sqrt(rank)
        if rank > FREQUENCY_TAIL_START:
            weight *= (FREQUENCY_TAIL_START / rank) ** 2
    except (OverflowError, ValueError, ZeroDivisionError):
        return 0.0
    return weight if math.isfinite(weight) and weight > 0 else 0.0


def _calculate_reading_frequency_scores_with_stats(payloads, rows):
    """Join bulk ranked forms to Jiten reading groups without per-word calls."""
    options = {}
    compositional_options = {}
    orders = {}
    for character, payload in payloads.items():
        if not isinstance(payload, dict):
            continue
        order = []
        for group in payload.get("wordsByReading") or []:
            if not isinstance(group, dict):
                continue
            reading = normalize_reading(group.get("reading"))
            if reading and reading not in order:
                order.append(reading)
        if order:
            options[character] = tuple(order)
            compound = _kanjidic_compositional_options(payload, order)
            if compound:
                compositional_options[character] = compound
            orders[character] = order

    values = {}
    stats = {
        "rows": len(rows),
        "relevantRows": 0,
        "alignedRows": 0,
        "ambiguousOrUnalignedRows": 0,
        "readingAssignments": 0,
        "relevantRankWeight": 0.0,
        "alignedRankWeight": 0.0,
    }
    for surface, full_reading, rank in rows:
        if not any(character in options for character in surface):
            continue
        stats["relevantRows"] += 1
        weight = rank_frequency_weight(rank)
        if weight <= 0:
            continue
        stats["relevantRankWeight"] += weight
        alignment = _align_frequency_form_normalized(
            surface, full_reading, options, compositional_options
        )
        if alignment is None:
            stats["ambiguousOrUnalignedRows"] += 1
            continue
        stats["alignedRows"] += 1
        stats["alignedRankWeight"] += weight
        assignments = {(character, reading) for _, character, reading in alignment}
        stats["readingAssignments"] += len(assignments)
        for character, reading in assignments:
            values.setdefault((character, reading), []).append(weight)

    result = {}
    for character, order in orders.items():
        payload = payloads[character]
        on = clean_strings(payload.get("onReadings"))
        kun = clean_strings(payload.get("kunReadings"))
        items = []
        for reading in order:
            weights = values.get((character, reading))
            if not weights:
                continue
            score = math.fsum(weights)
            if not math.isfinite(score) or score <= 0:
                continue
            items.append({
                "reading": reading,
                "score": score,
                "reading_class": classify_reading(reading, on, kun),
            })
        if items:
            result[character] = items
    stats["charactersWithScores"] = len(result)
    stats["readingGroupsWithScores"] = sum(len(items) for items in result.values())
    relevant_weight = stats["relevantRankWeight"]
    stats["rankWeightCoverage"] = (
        stats["alignedRankWeight"] / relevant_weight if relevant_weight > 0 else 0.0
    )
    return result, stats


def calculate_reading_frequency_scores(payloads, rows):
    """Public pure helper returning per-character rank-derived scores."""
    return _calculate_reading_frequency_scores_with_stats(payloads, rows)[0]


MIN_JITEN_FREQUENCY_ROWS = 400_000
MIN_JITEN_FREQUENCY_RELEVANT_ROWS = 250_000
MIN_JITEN_FREQUENCY_ALIGNED_ROWS = 180_000
MIN_JITEN_FREQUENCY_WEIGHT_COVERAGE = 0.85
MIN_JITEN_FREQUENCY_CHARACTERS = 3_500


def validate_jiten_frequency_coverage(stats):
    """Fail closed on partial, impossible, or internally inconsistent metrics."""
    requirements = (
        ("rows", MIN_JITEN_FREQUENCY_ROWS),
        ("relevantRows", MIN_JITEN_FREQUENCY_RELEVANT_ROWS),
        ("alignedRows", MIN_JITEN_FREQUENCY_ALIGNED_ROWS),
        ("charactersWithScores", MIN_JITEN_FREQUENCY_CHARACTERS),
    )
    for key, minimum in requirements:
        value = stats.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise MalformedPayload(
                f"Jiten frequency coverage floor failed: {key}={value!r} < {minimum}"
            )

    source_rows = stats.get("sourceRows")
    exact_duplicates = stats.get("exactDuplicateRows")
    conflicting_pairs = stats.get("conflictingSurfaceReadingPairs")
    excluded_conflicts = stats.get("excludedConflictingRows")
    parser_counts = (
        source_rows,
        exact_duplicates,
        conflicting_pairs,
        excluded_conflicts,
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in parser_counts
    ):
        raise MalformedPayload("Jiten frequency parser statistics are inconsistent")

    rows = stats["rows"]
    relevant = stats["relevantRows"]
    aligned = stats["alignedRows"]
    if (
        source_rows > MAX_JITEN_FREQUENCY_ROWS
        or source_rows != rows + exact_duplicates + excluded_conflicts
        or excluded_conflicts < 2 * conflicting_pairs
        or (conflicting_pairs == 0) != (excluded_conflicts == 0)
    ):
        raise MalformedPayload("Jiten frequency source-row accounting is inconsistent")

    ambiguous = stats.get("ambiguousOrUnalignedRows")
    assignments = stats.get("readingAssignments")
    if not (
        0 <= aligned <= relevant <= rows <= MAX_JITEN_FREQUENCY_ROWS
        and isinstance(ambiguous, int)
        and not isinstance(ambiguous, bool)
        and ambiguous == relevant - aligned
        and isinstance(assignments, int)
        and not isinstance(assignments, bool)
        and assignments >= aligned
    ):
        raise MalformedPayload("Jiten frequency coverage statistics are inconsistent")

    characters = stats.get("charactersWithScores")
    groups = stats.get("readingGroupsWithScores")
    if not (
        isinstance(characters, int)
        and not isinstance(characters, bool)
        and isinstance(groups, int)
        and not isinstance(groups, bool)
        and 0 < characters <= groups <= assignments <= aligned * MAX_JITEN_FREQUENCY_SURFACE_LENGTH
    ):
        raise MalformedPayload("Jiten frequency assignment statistics are inconsistent")

    coverage = stats.get("rankWeightCoverage")
    if (
        isinstance(coverage, bool)
        or not isinstance(coverage, (int, float))
        or not math.isfinite(coverage)
        or coverage < MIN_JITEN_FREQUENCY_WEIGHT_COVERAGE
        or coverage > 1.0
    ):
        raise MalformedPayload(
            "Jiten frequency coverage floor failed: "
            f"rankWeightCoverage={coverage!r}"
        )

    relevant_weight = stats.get("relevantRankWeight")
    aligned_weight = stats.get("alignedRankWeight")
    weights = (relevant_weight, aligned_weight)
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in weights
    ) or not (
        0 < aligned_weight <= relevant_weight <= relevant
        and aligned_weight <= aligned
    ):
        raise MalformedPayload("Jiten frequency rank-weight totals are inconsistent")
    expected_coverage = aligned_weight / relevant_weight
    if not math.isclose(coverage, expected_coverage, rel_tol=1e-12, abs_tol=1e-12):
        raise MalformedPayload("Jiten frequency rank-weight coverage is inconsistent")


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
    on = clean_strings(payload.get("onReadings"))
    kun = clean_strings(payload.get("kunReadings"))

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
        "global_words": _select_global_words(payload),
        "reading_entry_counts": _reading_entry_counts(payload, on, kun),
        # Populated only by the validated bulk Global CSV join in run_build().
        "reading_frequency_scores": [],
    }


# --- KANJIDIC2 simple licensed fallback --------------------------------------

# KANJIDIC2 (EDRDG) is the simple licensed fallback source for characters Jiten
# does not serve. We use ONLY the fields it plainly provides -- English
# meanings, Japanese on/kun/nanori readings, stroke count, grade, JLPT -- and
# never manufacture examples, ranks, percentages, donuts, phonetic families, or
# Jiten attribution for data Jiten did not provide. Jiten stays authoritative
# wherever it has a character; KANJIDIC2 only fills genuine gaps.


def parse_kanjidic2(xml_text):
    """Parse a KANJIDIC2 XML document into {character: supported-fields}.

    Extracts only the fields this generator honestly supports:
      meanings  -- English glosses (<meaning> with no m_lang attribute)
      on / kun  -- ja_on / ja_kun readings, preserved verbatim
      nanori    -- name readings (<nanori> under reading_meaning)
      stroke_count / grade / jlpt -- integer misc fields when present

    Entries are skipped when the <literal> is not a single Unicode scalar, or
    when the character carries no useful data (no English meaning AND no on/kun
    reading) -- so no blank/filler records are ever produced. Deterministic:
    keyed by character; the caller orders by codepoint.
    """
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_text)
    out = {}
    for char in root.findall("character"):
        literal = (char.findtext("literal") or "").strip()
        if len(literal) != 1:
            continue  # not a single-character kanji
        rm = char.find("reading_meaning")
        if rm is None:
            continue  # pure index entry, nothing to surface

        meanings, on, kun, nanori = [], [], [], []
        for m in rm.iter("meaning"):
            if m.get("m_lang") is None:  # English glosses have no m_lang
                txt = (m.text or "").strip()
                if txt:
                    meanings.append(txt)
        for r in rm.iter("reading"):
            txt = (r.text or "").strip()
            if not txt:
                continue
            if r.get("r_type") == "ja_on":
                on.append(txt)
            elif r.get("r_type") == "ja_kun":
                kun.append(txt)
        for n in rm.findall("nanori"):
            txt = (n.text or "").strip()
            if txt:
                nanori.append(txt)

        if not (meanings or on or kun):
            continue  # no useful data -> do not manufacture a blank entry

        misc = char.find("misc")

        def _int(tag, node=misc):
            if node is None:
                return None
            txt = node.findtext(tag)
            if txt is None:
                return None
            try:
                return int(txt)
            except ValueError:
                return None

        out[literal] = {
            "meanings": meanings,
            "on": on,
            "kun": kun,
            "nanori": nanori,
            "stroke_count": _int("stroke_count"),
            "grade": _int("grade"),
            "jlpt": _int("jlpt"),
        }
    return out


def kanjidic2_record(character, fields):
    """Build a normalized fallback record from parsed KANJIDIC2 fields.

    Shares the exact record shape produced by normalize_record so every bank
    builder works unchanged. Honest omissions: frequency_rank is None (Jiten's
    rank scale is not KANJIDIC2's newspaper-frequency and the two must not be
    conflated), and examples is empty (KANJIDIC2 supplies no example words), so
    no frequency meta, Frequency weight pie, or enrichment is ever
    fabricated for these characters.
    """
    senses = clean_meanings(fields.get("meanings"))
    on = clean_strings(fields.get("on"))
    kun = clean_strings(fields.get("kun"))
    return {
        "character": character,
        "keyword": senses[0] if senses else None,
        "senses": senses,
        "on": on,
        "kun": kun,
        "frequency_rank": None,   # KANJIDIC2 provides no Jiten-comparable rank
        "stroke_count": fields.get("stroke_count"),
        "grade": fields.get("grade"),
        "jlpt": fields.get("jlpt"),
        "examples": [],            # never invent example words
        "global_words": [],        # KANJIDIC2 supplies no example vocabulary
        "reading_entry_counts": [],  # never fabricate a reading-share statistic
    }


def merge_kanjidic2(jiten_records, kanjidic2_index):
    """Merge Jiten records with KANJIDIC2 fallbacks, Jiten authoritative.

    Every Jiten record is preserved verbatim (its enriched keyword, meanings,
    readings, rank, examples, and reading distribution are untouched). For each
    KANJIDIC2 character ABSENT from Jiten, a clean fallback record is appended.
    The result is unique by character (duplicates impossible) and ordered
    deterministically by Unicode codepoint.
    """
    present = {r["character"] for r in jiten_records}
    merged = list(jiten_records)
    for char in sorted(kanjidic2_index, key=lambda c: ord(c)):
        if char in present:
            continue  # Jiten wins on every duplicate
        merged.append(kanjidic2_record(char, kanjidic2_index[char]))
        present.add(char)
    merged.sort(key=lambda r: ord(r["character"]))
    return merged


# --- Reading-distribution pie chart ------------------------------------------

# At most this many labelled pie segments; any tail collapses into "Other".
MAX_DONUT_SEGMENTS = 5

# Accessible, colour-blind-distinguishable palette (Okabe-Ito derived). Order is
# stable so identical inputs always produce identical colours.
_DONUT_COLORS = [
    "#0072b2",  # blue
    "#d55e00",  # vermillion
    "#009e73",  # green
    "#cc79a7",  # reddish purple
    "#e69f00",  # orange
    "#767676",  # grey ("Other" / overflow)
]
_DONUT_OTHER_COLOR = "#767676"


def _largest_remainder_percents(counts, total):
    """Round counts to integer percents that sum to exactly 100.

    Uses the largest-remainder method so the reported percentages are truthful
    (each within 1 of its exact share) and always total 100. Ties break on the
    original index for deterministic output.
    """
    if total <= 0 or not counts:
        return []
    exact = [c * 100.0 / total for c in counts]
    floors = [int(x) for x in exact]
    remainder = 100 - sum(floors)
    order = sorted(range(len(counts)), key=lambda i: (-(exact[i] - floors[i]), i))
    for i in order[: max(0, remainder)]:
        floors[i] += 1
    return floors


def reading_frequency_distribution(record):
    """Build a frequency-weight-only pie distribution for one kanji."""
    scores = {}
    classes = {}
    for item in record.get("reading_frequency_scores") or []:
        reading = item.get("reading")
        score = item.get("score")
        if (
            not reading
            or not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not math.isfinite(score)
            or score <= 0
        ):
            continue
        scores[reading] = scores.get(reading, 0.0) + float(score)
        classes.setdefault(reading, item.get("reading_class") or "Other")
    total = sum(scores.values())
    if total <= 0:
        return {"total": 0.0, "segments": [], "collapsed": False}

    ordered = sorted(scores, key=lambda reading: (-scores[reading], reading))
    collapsed = len(ordered) >= MAX_DONUT_SEGMENTS
    named = ordered[: MAX_DONUT_SEGMENTS - 1] if collapsed else ordered
    labels = list(named)
    values = [scores[reading] for reading in named]
    if collapsed:
        tail = ordered[MAX_DONUT_SEGMENTS - 1:]
        labels.append("")
        values.append(sum(scores[reading] for reading in tail))

    percents = _largest_remainder_percents(values, total)
    segments = []
    for index, (reading, percent) in enumerate(zip(labels, percents)):
        is_other = reading == ""
        segments.append({
            "reading": reading,
            "reading_class": "Other" if is_other else classes.get(reading, "Other"),
            "percent": percent,
            "color": _DONUT_OTHER_COLOR if is_other else _DONUT_COLORS[index],
        })
    return {"total": total, "segments": segments, "collapsed": collapsed}


# --- Per-entry raster Frequency weight chart (packaged PNG media) -------------

# The compact card ships Frequency weight as a deterministic per-entry
# PNG packaged as Yomitan dictionary media (referenced through a supported
# structured-content <img>), rather than an inline CSS conic-gradient ring.
# A raster image is what official Yomitan renders from an archive `path`, so it
# survives every renderer / theme without relying on inline gradient support.

# Fixed square canvas. 128px keeps the packaged bytes tiny while staying crisp
# when the popup scales the image down to a few em.
READING_CHART_SIZE = 128


def _hex_to_rgba(hex_color, alpha=255):
    """Convert a #rrggbb string to an (r, g, b, a) tuple."""
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def reading_frequency_asset_name(character):
    """Archive path for a character's frequency-weighted reading pie."""
    return f"reading-frequency/{ord(character):05x}.png"


def build_reading_frequency_png(record):
    """Return a deterministic PNG for Jiten frequency-weight shares."""
    distribution = reading_frequency_distribution(record)
    if not distribution["segments"]:
        return None
    return _build_pie_png(distribution["segments"])


def _build_pie_png(segments):
    """Draw one deterministic filled pie from prepared shared segments."""
    from PIL import Image, ImageDraw

    scale = 4
    size = READING_CHART_SIZE * scale
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = int(size * 0.06)
    box = [pad, pad, size - pad, size - pad]
    start = -90.0
    for segment in segments:
        sweep = segment["percent"] * 3.6
        end = start + sweep
        if sweep > 0:
            draw.pieslice(box, start, end, fill=_hex_to_rgba(segment["color"]))
        start = end

    img = img.resize((READING_CHART_SIZE, READING_CHART_SIZE), Image.LANCZOS)
    return _flatten_to_palette_png(img)


def _flatten_to_palette_png(rgba):
    """Flatten an RGBA pie to a deterministic fixed-palette PNG (bytes).

    Palette index 0 is the transparent field; the remaining indices are the
    fixed Okabe-Ito segment colours (see ``_DONUT_COLORS``). Every opaque pixel
    is snapped to its nearest segment colour; pixels below 50%% alpha become the
    transparent index. The palette order is fixed, so identical inputs yield
    byte-identical output.
    """
    from PIL import Image

    palette_rgb = [(0, 0, 0)] + [
        (int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)) for h in _DONUT_COLORS
    ]
    solids = palette_rgb[1:]

    out = Image.new("P", rgba.size, 0)
    src = rgba.load()
    dst = out.load()
    nearest_cache = {}
    for y in range(rgba.height):
        for x in range(rgba.width):
            r, g, b, a = src[x, y]
            if a < 128:
                dst[x, y] = 0
                continue
            key = (r, g, b)
            idx = nearest_cache.get(key)
            if idx is None:
                best_i, best_d = 1, None
                for i, (cr, cg, cb) in enumerate(solids):
                    d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
                    if best_d is None or d < best_d:
                        best_d, best_i = d, i + 1
                idx = nearest_cache[key] = best_i
            dst[x, y] = idx

    flat = []
    for c in palette_rgb:
        flat.extend(c)
    flat.extend([0, 0, 0] * (256 - len(palette_rgb)))
    out.putpalette(flat)

    buf = io.BytesIO()
    # Fixed PNG encoder options + no timestamp chunk => byte-deterministic.
    out.save(buf, format="PNG", optimize=False, compress_level=9, transparency=0)
    return buf.getvalue()


def build_reading_frequency_node(record):
    """Build the packaged-PNG Frequency weight structured content.

    The graphic is a single supported ``img`` referencing the packaged PNG by
    its archive path; a caption and a visible text legend (colour swatch +
    reading (class) + percent) carry the same data as real
    text, so colour is never the sole channel and the chart degrades gracefully
    when the image cannot load.
    """
    frequency = reading_frequency_distribution(record)
    if frequency["segments"]:
        segments = frequency["segments"]
        alt = "Rank-derived frequency weight: " + ", ".join(
            f"{segment.get('reading') or 'Other'} {segment['percent']} percent"
            for segment in segments
        )
        legend_items = []
        for segment in segments:
            swatch = {
                "tag": "span",
                "data": {"beeRole": "donut-swatch"},
                "style": {"background": segment["color"], "color": segment["color"]},
                "content": "\u25a0",
            }
            legend_items.append({
                "tag": "li",
                "content": [
                    swatch,
                    f"{segment.get('reading') or 'Other'}: {segment['percent']}%",
                ],
            })
        return {
            "tag": "div",
            "data": {"beeRole": "reading-donut"},
            "lang": "en",
            "title": alt,
            "content": [
                {"tag": "div", "data": {"beeRole": "donut-caption"},
                 "content": "Frequency weight"},
                {"tag": "div", "data": {"beeRole": "reading-pie"}, "content": [{
                    "tag": "img",
                    "data": {"beeRole": "reading-chart"},
                    "path": reading_frequency_asset_name(record["character"]),
                    "width": 4.25,
                    "height": 4.25,
                    "sizeUnits": "em",
                    "alt": alt,
                    "title": alt,
                    "collapsible": False,
                    "collapsed": False,
                    "background": False,
                }]},
                {"tag": "ul", "data": {"beeRole": "donut-legend"},
                 "content": legend_items},
            ],
        }
    return None


# --- KanjiVG-sourced phonetic families ---------------------------------------

# KanjiVG records the phonetic component via a kvg:phon attribute on the
# phonetic-component sub-group (e.g. 時 = 日 + 寺, where the 寺 group carries
# kvg:phon="寺"). We only ever surface a relationship KanjiVG itself marks.
_KVG_PHON = re.compile(r'kvg:phon="([^"]+)"')

PHONETIC_SOURCE = "KanjiVG"


def extract_phonetic_component(svg_text, character):
    """Return the phonetic component KanjiVG marks for a character, else None.

    KanjiVG attaches kvg:phon to the phonetic sub-component group anywhere in
    the glyph tree; its value is the phonetic component of the whole character.
    Never inferred. A marker equal to the character itself is not a phonetic
    family relationship and returns None. When several markers appear, the first
    (outermost) is used deterministically.
    """
    if not isinstance(svg_text, str) or not isinstance(character, str):
        return None
    for comp in _KVG_PHON.findall(svg_text):
        if comp and comp != character:
            return comp
    return None


def build_phonetic_families(phon_map, ranks):
    """Group characters that share a phonetic component into ordered families.

    phon_map: {character: phonetic_component} sourced from KanjiVG kvg:phon.
    ranks:    {character: frequency_rank} used to order members usefully.

    Members are ordered by frequency rank ascending (most common first), then
    by stable Unicode codepoint for characters without a rank. Singleton
    components (only one member) are dropped -- a family needs >= 2 members.
    Returns {component: {"component", "members", "source"}}, deterministic.
    """
    grouped = {}
    for char, comp in (phon_map or {}).items():
        if not comp or comp == char:
            continue
        grouped.setdefault(comp, set()).add(char)

    families = {}
    for comp in sorted(grouped, key=lambda c: [ord(x) for x in c]):
        members = grouped[comp]
        if len(members) < 2:
            continue

        def sort_key(ch):
            r = ranks.get(ch)
            # ranked chars first (by rank), unranked after (by codepoint)
            return (0, r, ord(ch)) if isinstance(r, int) else (1, 0, ord(ch))

        ordered = sorted(members, key=sort_key)
        families[comp] = {
            "component": comp,
            "members": ordered,
            "source": PHONETIC_SOURCE,
        }
    return families


def build_phonetic_family_node(character, family):
    """Build a structured-content node for a character's phonetic family.

    Names the shared phonetic component, lists the sibling members (excluding
    the character itself), and records a compact source attribution. Returns
    None when the character has no recorded family.
    """
    if not family:
        return None
    siblings = [m for m in family["members"] if m != character]
    if not siblings:
        return None
    comp = family["component"]
    body = [
        {"tag": "span", "data": {"beeRole": "phon-label"},
         "content": f"Phonetic \u97f3 {comp}: "},
        {"tag": "span", "data": {"beeRole": "phon-members"},
         "content": "\u3001".join(siblings)},
        {"tag": "span", "data": {"beeRole": "phon-source"},
         "title": f"Source: {family['source']} (kvg:phon)",
         "content": f" \u2014 {family['source']}"},
    ]
    return {
        "tag": "div",
        "data": {"beeRole": "phonetic-family"},
        "content": body,
    }


# --- KanjiVG stroke / component enrichment -----------------------------------

_SVG_NS = "http://www.w3.org/2000/svg"
_KVG_ELEMENT = re.compile(r'kvg:element="([^"]+)"')
_KVG_PATH = re.compile(r'<path\b[^>]*\bd="([^"]+)"[^>]*>')
_KVG_STROKE_NUMBER = re.compile(
    r'<text transform="matrix\(1 0 0 1 (-?[0-9]+(?:\.[0-9]+)?) '
    r'(-?[0-9]+(?:\.[0-9]+)?)\)">([1-9][0-9]*)</text>'
)


def kanjivg_asset_name(character):
    """Deterministic bundled-asset path for a character's KanjiVG SVG."""
    return f"kanjivg/{ord(character):05x}.svg"


def parse_kanjivg(svg_text, character):
    """Extract deterministic stroke count + component list from a KanjiVG SVG.

    stroke_count = number of <path> stroke elements. components = the ordered,
    de-duplicated list of kvg:element values (the character and its parts).
    Returns None when the text has no stroke paths.
    """
    if not isinstance(svg_text, str):
        return None
    paths = _KVG_PATH.findall(svg_text)
    if not paths:
        return None
    seen = set()
    components = []
    for el in _KVG_ELEMENT.findall(svg_text):
        if el and el not in seen:
            seen.add(el)
            components.append(el)
    if character not in components:
        components.insert(0, character)
    return {
        "stroke_count": len(paths),
        "components": components,
        "asset": kanjivg_asset_name(character),
    }


def sanitize_kanjivg_svg(svg_text, character):
    """Rebuild a minimal, safe, static SVG from a KanjiVG source string.

    Strips the XML declaration, DOCTYPE, comments, kvg namespaced attributes,
    labels that do not match KanjiVG's strict numeric form, and anything
    script/external (scripts, event handlers, xlink, <image>). Rebuilds a clean
    <svg> containing the stroke <path> geometry and safe stroke numbers as a
    deterministic, static high-contrast stroke-order diagram. Yomitan rasterizes
    dictionary media into a canvas, so animation is intentionally omitted.
    Output is deterministic and safe for reduced-motion users.
    """
    paths = _KVG_PATH.findall(svg_text)
    strokes = "".join(
        f'<path class="bee-stroke-outline" fill="none" stroke="#ffffff" '
        f'stroke-width="5" stroke-linecap="round" stroke-linejoin="round" d="{d}"/>'
        f'<path class="bee-stroke-ink" fill="none" stroke="#0072b2" '
        f'stroke-width="3" stroke-linecap="round" stroke-linejoin="round" d="{d}"/>'
        for d in paths
    )
    numbers = "".join(
        f'<text class="bee-stroke-number" x="{x}" y="{y}" '
        'font-family="sans-serif" font-size="8" font-weight="700" '
        'fill="#0072b2" stroke="#ffffff" stroke-width="2" '
        f'paint-order="stroke" stroke-linejoin="round">{number}</text>'
        for x, y, number in _KVG_STROKE_NUMBER.findall(svg_text)
    )
    title = f"Stroke order for {character}"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 109 109" '
        'width="109" height="109" role="img" '
        f'aria-label="{title}"><title>{title}</title>'
        f"{strokes}{numbers}</svg>"
    )


def build_stroke_node(character, info):
    """Build a structured-content node for stroke order + components.

    When a bundled SVG asset is available, references it via an img node (which
    carries alt text) and follows it with a text line naming the stroke count
    and components -- so the information survives even if the media, SVG, or
    character asset is unavailable. Returns None when no info is present.
    """
    if not info:
        return None
    strokes = info.get("stroke_count")
    components = [c for c in (info.get("components") or []) if c != character]
    asset = info.get("asset")

    text_bits = []
    if isinstance(strokes, int):
        text_bits.append(f"{strokes} strokes")
    if components:
        text_bits.append("components " + "\u3001".join(components))
    text_line = {
        "tag": "div",
        "data": {"beeRole": "stroke-text"},
        "content": " \u00b7 ".join(text_bits) if text_bits else "stroke data",
    }

    content = []
    if asset:
        alt = f"{character} stroke order diagram"
        if isinstance(strokes, int):
            alt += f" ({strokes} strokes)"
        content.append({
            "tag": "img",
            "path": asset,
            "width": 6,
            "height": 6,
            "sizeUnits": "em",
            "alt": alt,
            "title": alt,
            "collapsible": False,
            "collapsed": False,
            "background": False,
            "data": {"beeRole": "stroke-image"},
        })
    content.append(text_line)
    return {
        "tag": "div",
        "data": {"beeRole": "stroke-order"},
        "content": content,
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


def _reading_group_node(label, readings):
    """Build a labelled reading group whose readings are separate chips.

    Renders as: a small class label ("On"/"Kun") followed by one chip per
    reading. The label is real text so the On/Kun distinction never depends on
    colour. Returns None when there are no readings for this class.
    """
    if not readings:
        return None
    chips = [
        {"tag": "span", "data": {"beeRole": "reading-chip"}, "lang": "ja",
         "content": reading}
        for reading in readings
    ]
    return {
        "tag": "div",
        "data": {"beeRole": "reading-group"},
        "content": [
            {"tag": "span", "data": {"beeRole": "reading-label"}, "content": label},
            {"tag": "span", "data": {"beeRole": "reading-chips"}, "content": chips},
        ],
    }


def _badge_node(text):
    """A single small metadata badge with real text (e.g. 'Rank 57')."""
    return {"tag": "span", "data": {"beeRole": "badge"}, "content": text}


# Number of top readings shown above the fold, selected by Jiten totals.
COMPACT_READING_COUNT = 3
# Exactly this many globally highest-frequency words fill the compact grid.
COMPACT_WORD_COUNT = 6


def _top_reading_chip_row(record):
    """Top readings (by Jiten vocabulary-entry totals) as plain chips.

    Selects the ``COMPACT_READING_COUNT`` readings with the highest Jiten
    vocabulary-entry totals from ``reading_entry_counts`` (already ordered
    desc count, reading, source) and renders each as a plain chip. This is a
    single mixed row -- NOT split into On/Kun labelled groups -- because the
    compact card ranks readings by how much vocabulary actually uses them, not
    by reading class. Returns None when the record carries no reading totals.
    """
    counts = record.get("reading_entry_counts") or []
    top = [c["reading"] for c in counts[:COMPACT_READING_COUNT] if c.get("reading")]
    if not top:
        return None
    chips = [
        {"tag": "span", "data": {"beeRole": "reading-chip"}, "lang": "ja",
         "content": reading}
        for reading in top
    ]
    return {
        "tag": "div",
        "data": {"beeRole": "reading-chips"},
        "content": chips,
    }


def _query_link(surface, content, lang="ja"):
    """Wrap content in a supported internal Yomitan ``?query=`` link.

    Yomitan structured content treats an ``<a>`` whose ``href`` begins with
    ``?`` as an internal link, rewriting it to ``search.html?query=...`` and
    wiring it through the content manager -- no custom JavaScript. The schema
    forbids a ``data`` attribute on ``<a>``; Yomitan renders it as
    ``a.gloss-link > span.gloss-link-text``, so styling hooks onto that
    preserved class. The query targets the plain (annotation-free) surface.
    """
    return {
        "tag": "a",
        "href": "?query=" + urllib.parse.quote(surface, safe=""),
        "lang": lang,
        "content": content,
    }


def _global_vocab_grid(record):
    """Exactly the six globally highest-frequency words in a responsive grid.

    Uses ``record['global_words']`` (top six de-duplicated words by Jiten
    frequency rank across all readings). Each cell carries the ruby surface --
    wrapped in a supported internal Yomitan ``?query=`` link so a reader can
    pivot to that word's own lookup with one click -- plus a concise gloss. The
    grid renders 3-left / 3-right on ordinary popups and a single column on
    narrow popups (see styles.css). Returns None when the record has no global
    words (KANJIDIC2-only entries).
    """
    words = record.get("global_words") or []
    if not words:
        return None
    cells = []
    for ex in words:
        cells.append({
            "tag": "div",
            "data": {"beeRole": "vocab-item"},
            "content": [
                {"tag": "span", "data": {"beeRole": "vocab-word"},
                 "lang": "ja",
                 "content": _query_link(ex["surface"], _ruby_node(ex["ruby"]))},
                {"tag": "span", "data": {"beeRole": "vocab-gloss"},
                 "content": " \u2014 " + ex["gloss"]},
            ],
        })
    return {
        "tag": "div",
        "data": {"beeRole": "vocab-grid"},
        "content": cells,
    }


def _reading_disclosure(record):
    """Collapsed On/Kun reading disclosure (full lists, class-labelled)."""
    reading_groups = []
    on_group = _reading_group_node("On", record["on"])
    if on_group is not None:
        reading_groups.append(on_group)
    kun_group = _reading_group_node("Kun", record["kun"])
    if kun_group is not None:
        reading_groups.append(kun_group)
    if not reading_groups:
        return None
    return {
        "tag": "details",
        "data": {"beeRole": "section"},
        "content": [
            {"tag": "summary", "content": "All readings"},
            {"tag": "div", "data": {"beeRole": "readings"}, "content": reading_groups},
        ],
    }


def _metadata_disclosure(record):
    """Collapsed rank / grade / JLPT / strokes disclosure."""
    badges = []
    if record["frequency_rank"] is not None:
        badges.append(_badge_node(f"Rank {record['frequency_rank']}"))
    if record["grade"] is not None:
        badges.append(_badge_node(f"Grade {record['grade']}"))
    jl = _jlpt_label(record["jlpt"])
    if jl:
        badges.append(_badge_node(f"JLPT {jl}"))
    if record["stroke_count"] is not None:
        badges.append(_badge_node(f"{record['stroke_count']} strokes"))
    if not badges:
        return None
    return {
        "tag": "details",
        "data": {"beeRole": "section"},
        "content": [
            {"tag": "summary", "content": "Details"},
            {"tag": "div", "data": {"beeRole": "badge-row"}, "content": badges},
        ],
    }


def _more_vocab_disclosure(record):
    """Collapsed additional vocabulary grouped by reading (beyond the six)."""
    groups = []
    shown = {(w["word_id"], w["reading_index"]) for w in (record.get("global_words") or [])}
    for group in record["examples"]:
        items = []
        for ex in group["words"]:
            if (ex["word_id"], ex["reading_index"]) in shown:
                continue
            line = [{"tag": "span", "data": {"beeRole": "vocab-word"},
                     "lang": "ja", "content": _ruby_node(ex["ruby"])},
                    {"tag": "span", "data": {"beeRole": "vocab-gloss"},
                     "content": " \u2014 " + ex["gloss"]}]
            items.append({"tag": "li", "data": {"beeRole": "vocab-item"}, "content": line})
        if items:
            groups.append({
                "tag": "div",
                "data": {"beeRole": "vocab-group"},
                "content": [
                    {"tag": "div", "data": {"beeRole": "vocab-label"},
                     "content": group["label"]},
                    {"tag": "ul", "data": {"beeRole": "vocab-list"}, "content": items},
                ],
            })
    if not groups:
        return None
    return {
        "tag": "details",
        "data": {"beeRole": "section"},
        "content": [
            {"tag": "summary", "content": "More vocabulary"},
            {"tag": "div", "data": {"beeRole": "more-vocab"}, "content": groups},
        ],
    }


def _detail_content(record, enrichment=None):
    """Build the structured-content body for a term entry's detail item.

    The compact card puts only the highest-value material above the fold, in a
    fixed order:

      1. a HERO header pairing the kanji glyph with its keyword,
      2. the TOP THREE readings by Jiten vocabulary-entry totals as plain chips
         (not split into On/Kun groups),
      3. a compact meaning line,
      4. the Frequency weight pie as a packaged raster PNG (omitted, never faked,
         for KANJIDIC2-only records) with alt text + a visible legend,
      5. exactly SIX globally highest-frequency de-duplicated words in a
         responsive two-column (3-left / 3-right) grid with ruby + gloss.

    Everything secondary -- the complete On/Kun lists, rank/grade/JLPT/strokes,
    additional vocabulary, the stroke-order diagram, the phonetic family, and
    sources -- lives in collapsed Yomitan ``details`` disclosures BELOW the
    fold. Every graphic still has a real text equivalent (chip text, legend,
    alt text, badge text), so colour or CSS is never the only carrier of
    information. Empty disclosures are never emitted.
    """
    char = record["character"]
    keyword = record["keyword"] or char
    body = []

    # 1. Hero header: the glyph paired with its keyword.
    body.append({
        "tag": "div",
        "data": {"beeRole": "hero"},
        "content": [
            {"tag": "span", "data": {"beeRole": "hero-glyph"},
             "lang": "ja", "content": char},
            {"tag": "span", "data": {"beeRole": "hero-keyword"}, "content": keyword},
        ],
    })

    # 2. Top readings by Jiten vocabulary totals (mixed chips, no On/Kun split).
    chip_row = _top_reading_chip_row(record)
    if chip_row is not None:
        body.append(chip_row)

    # 3. Meaning: a compact, distinct hierarchy line.
    if record["senses"]:
        body.append({
            "tag": "div",
            "data": {"beeRole": "meaning"},
            "content": "; ".join(record["senses"]),
        })

    # 4. Rank-derived Frequency weight as a packaged raster PNG. Omitted when
    #    the validated bulk source has no aligned reading weights.
    chart = build_reading_frequency_node(record)
    if chart is not None:
        body.append(chart)

    # 5. Exactly six globally highest-frequency words in a two-column grid.
    grid = _global_vocab_grid(record)
    if grid is not None:
        body.append(grid)

    # 6. Secondary material -> collapsed, keyboard-accessible disclosures.
    reading_section = _reading_disclosure(record)
    if reading_section is not None:
        body.append(reading_section)

    meta_section = _metadata_disclosure(record)
    if meta_section is not None:
        body.append(meta_section)

    more_vocab = _more_vocab_disclosure(record)
    if more_vocab is not None:
        body.append(more_vocab)

    # Learning aids (phonetic family + stroke-order diagram) stay in their own
    # collapsed section, only when enrichment data actually exists.
    if enrichment:
        aids = []
        fam = (enrichment.get("families_by_char") or {}).get(char)
        phon_node = build_phonetic_family_node(char, fam)
        if phon_node is not None:
            aids.append(phon_node)
        stroke_info = (enrichment.get("strokes") or {}).get(char)
        stroke_node = build_stroke_node(char, stroke_info)
        if stroke_node is not None:
            aids.append(stroke_node)
        if aids:
            body.append({
                "tag": "details",
                "data": {"beeRole": "section"},
                "content": [
                    {"tag": "summary", "content": "Learning aids"},
                    {"tag": "div", "data": {"beeRole": "aids"}, "content": aids},
                ],
            })

    return [{"tag": "div", "data": {"beeRole": "detail"}, "content": body}]


def build_term_entry(record, enrichment=None):
    """Build one Yomitan term-bank entry for a normalized kanji record.

    `enrichment` (optional) threads the visual learning aids into the detail.

    The glossary is the single structured-content card ONLY: the hero header
    already names the keyword, so a leading plain-text gloss string would make
    Yomitan paint the keyword twice (a redundant standalone line above the
    card). A one-item structured-content glossary is the supported minimal
    form and keeps the rich card the sole visible surface.
    """
    glossary = [
        {"type": "structured-content", "content": _detail_content(record, enrichment)},
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


def build_banks(records, aliases=None, enrichment=None):
    """Assemble the four Yomitan banks from normalized records + term aliases.

    Records are sorted deterministically by Unicode code point. Aliases (old
    form -> canonical form) contribute only term-bank entries. `enrichment`
    (optional) threads visual learning aids into the term entries only -- the
    kanji/meta/frequency banks are untouched, so no percentage or totalWords
    statistic can leak into them. Returns a dict with the four banks.
    """
    aliases = aliases or {}
    ordered = sorted(records, key=lambda r: ord(r["character"]))

    term_bank = []
    term_meta_bank = []
    kanji_bank = []
    kanji_meta_bank = []

    for r in ordered:
        term_bank.append(build_term_entry(r, enrichment))
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
SOURCE_SNAPSHOT_NAME = "source-snapshot.zip"
SOURCE_LOCK_NAME = "SOURCE-LOCK.json"
RELEASE_ASSET_NAMES = (
    ZIP_NAME,
    "MANIFEST.json",
    "SHA256SUMS",
    JITEN_FREQUENCY_ASSET_NAME,
    SOURCE_SNAPSHOT_NAME,
    SOURCE_LOCK_NAME,
)
PINNED_BUILD_CONTAINER = (
    "python:3.11.15-bookworm@sha256:"
    "a8f8fbe1a0edc9e4dddafa64ba73f7e04be7be5ebc23f332362e779e0a2e4e52"
)

ATTRIBUTION = (
    "Dictionary data derived from Jiten (https://jiten.moe), including its Global "
    "frequency-list CSV, and directly from KANJIDIC2, using JMdict/KANJIDIC data "
    "from the Electronic Dictionary Research and Development Group (EDRDG). Data "
    "and rank-weight adaptations are redistributed under CC BY-SA 4.0; see "
    "https://creativecommons.org/licenses/by-sa/4.0/ and "
    "https://www.edrdg.org/edrdg/licence.html."
)

LICENSE_DATA_TEXT = (
    pathlib.Path(__file__).resolve().parent.parent / "LICENSE-data.txt"
).read_text(encoding="utf-8")

# KanjiVG (stroke-order SVGs + phonetic-component markers) is Copyright (C)
# Ulrich Apel, distributed under CC BY-SA 3.0. Bundled SVGs are adapted (stroke
# geometry and numbers, re-styled as static high-contrast diagrams); share-alike is met by
# redistributing them under the same/compatible CC BY-SA licence.
LICENSE_KANJIVG_TEXT = (
    "Stroke-order diagrams and phonetic-component (kvg:phon) relationships are\n"
    "derived from KanjiVG, Copyright (C) 2009-2011 Ulrich Apel.\n"
    "KanjiVG is distributed under the Creative Commons Attribution-Share Alike\n"
    "3.0 licence; see https://creativecommons.org/licenses/by-sa/3.0/ and\n"
    "https://kanjivg.tagaini.net/. The bundled SVGs are adaptations (stroke\n"
    "geometry and numbers extracted and re-styled as static high-contrast diagrams);\n"
    "they are redistributed under the same CC BY-SA licence (share-alike).\n"
)

# Compact, accessible stylesheet scoped to this dictionary's structured content.
# Loaded by Yomitan as styles.css at the ZIP root. Structured-content `data`
# keys become `data-sc-*` attributes, so every rule below targets our own
# `data-sc-bee-role` markers -- it never restyles unrelated dictionaries.
STYLES_CSS = """\
/* Bee's Ultimate Kanji Dictionary -- compact accessible structured-content CSS.
   All rules are scoped to our own data-sc-bee-role markers. Colour is never the
   sole information channel; every graphic has a visible text equivalent. */

/* Restrained reusable token palette: colours and spacing are defined once here
   and consumed via var() below, so nothing hardcodes a stray magic value. The
   accent uses the colour-blind-safe Okabe-Ito blue; every token degrades
   sensibly when a token is missing. currentColor / the viewer's own
   --background-color and --text-color are preferred so the card inherits
   Yomitan's active (light or dark) theme rather than fighting it.

   The tokens are scoped to our own detail wrapper (never the document root) so
   our custom properties never leak into Yomitan's chrome or any other
   dictionary's structured content -- the whole card stays self-contained. */
[data-sc-bee-role="detail"] {
  --bee-accent: #0072b2;
  --bee-chip-bg: color-mix(in srgb, currentColor 10%, transparent);
  --bee-chip-border: color-mix(in srgb, currentColor 22%, transparent);
  --bee-badge-bg: color-mix(in srgb, currentColor 8%, transparent);
  --bee-muted: color-mix(in srgb, currentColor 65%, transparent);
  --bee-gap: 0.35em;
  --bee-radius: 0.4em;
  line-height: 1.5;
}

/* Hero header: the glyph is the large unambiguous anchor; the keyword names the
   character beside it. Real text, so it survives with images/CSS off. */
[data-sc-bee-role="hero"] {
  display: flex;
  align-items: baseline;
  gap: 0.5em;
  flex-wrap: wrap;
  margin: 0 0 0.45em;
}
[data-sc-bee-role="hero-glyph"] {
  font-size: 2.6em;
  line-height: 1;
  font-weight: 600;
}
[data-sc-bee-role="hero-keyword"] {
  font-size: 1.15em;
  font-weight: 700;
  letter-spacing: 0.01em;
  color: var(--bee-accent, currentColor);
}

/* Readings: On / Kun as separated, labelled chip groups. The class label is
   real text so the On/Kun distinction never depends on colour. Chips wrap in a
   narrow popup instead of overflowing. */
[data-sc-bee-role="readings"] { margin: 0.3em 0; }
[data-sc-bee-role="reading-group"] {
  display: flex;
  align-items: baseline;
  gap: var(--bee-gap, 0.35em);
  flex-wrap: wrap;
  margin: 0.15em 0;
}
[data-sc-bee-role="reading-label"] {
  font-size: 0.8em;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--bee-muted, currentColor);
  min-width: 2.2em;
}
[data-sc-bee-role="reading-chips"] {
  display: inline-flex;
  flex-wrap: wrap;
  gap: var(--bee-gap, 0.35em);
}
[data-sc-bee-role="reading-chip"] {
  display: inline-block;
  padding: 0.05em 0.5em;
  border: 1px solid var(--bee-chip-border, currentColor);
  border-radius: 999px;
  background: var(--bee-chip-bg, transparent);
  font-size: 0.95em;
  white-space: nowrap;
}

/* Compact above-the-fold reading chips: a single wrapping row of the top
   readings by Jiten vocabulary totals (no On/Kun split above the fold). */
[data-sc-bee-role="reading-chips"] {
  display: flex;
  flex-wrap: wrap;
  gap: var(--bee-gap, 0.35em);
  margin: 0.3em 0;
}
[data-sc-bee-role="reading-chip"] {
  display: inline-block;
  padding: 0.05em 0.5em;
  border: 1px solid var(--bee-chip-border, currentColor);
  border-radius: 999px;
  background: var(--bee-chip-bg, transparent);
  font-size: 0.95em;
  white-space: nowrap;
}

/* Meaning: a distinct, compact hierarchy line -- not merged with readings. */
[data-sc-bee-role="meaning"] { margin: 0.35em 0; font-size: 1em; line-height: 1.45; }

/* Badges: a small aligned, wrapping row of metadata pills (inside disclosures). */
[data-sc-bee-role="badge-row"] {
  display: flex;
  flex-wrap: wrap;
  gap: var(--bee-gap, 0.35em);
  margin: 0.3em 0;
}
[data-sc-bee-role="badge"] {
  display: inline-block;
  padding: 0.05em 0.45em;
  border-radius: var(--bee-radius, 0.4em);
  background: var(--bee-badge-bg, transparent);
  font-size: 0.8em;
  font-weight: 600;
  white-space: nowrap;
  color: var(--bee-muted, currentColor);
}

/* Six globally-highest-frequency words in a responsive two-column grid
   (3 left / 3 right on ordinary popups). Each cell carries a clickable ruby
   word + a quiet gloss. On a narrow popup the grid collapses to a single
   column (below). */
[data-sc-bee-role="vocab-grid"] {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.18em 1.1em;
  margin: 0.5em 0;
}
[data-sc-bee-role="vocab-grid"] > [data-sc-bee-role="vocab-item"] {
  margin: 0.12em 0;
  min-width: 0;
  overflow-wrap: anywhere;
  line-height: 1.4;
}

/* Clickable words: Yomitan renders the internal ?query= link as
   a.gloss-link > span.gloss-link-text. Keep it in the card's own colour (not a
   jarring default link blue), with a restrained underline that only firms up
   on hover/focus, and a clearly visible keyboard focus ring. No custom JS -- the
   link navigation is Yomitan's supported internal-search behaviour. */
[data-sc-bee-role="vocab-word"] .gloss-link {
  color: inherit;
  text-decoration: underline;
  text-decoration-color: var(--bee-chip-border, currentColor);
  text-underline-offset: 0.15em;
  cursor: pointer;
}
[data-sc-bee-role="vocab-word"] .gloss-link:hover {
  text-decoration-color: var(--bee-accent, currentColor);
}
.gloss-link:focus-visible {
  outline: 2px solid var(--bee-accent, currentColor);
  outline-offset: 2px;
  border-radius: 0.15em;
}

/* Additional vocabulary (inside the "More vocabulary" disclosure). */
[data-sc-bee-role="vocab-group"] { margin: 0.4em 0; }
[data-sc-bee-role="vocab-label"] {
  font-size: 0.8em;
  font-weight: 700;
  color: var(--bee-muted, currentColor);
  margin: 0.2em 0 0.1em;
}
[data-sc-bee-role="vocab-list"] { margin: 0; padding-left: 1.1em; }
[data-sc-bee-role="vocab-item"] { margin: 0.1em 0; }
[data-sc-bee-role="vocab-word"] ruby rt { font-size: 0.7em; opacity: 0.8; }
[data-sc-bee-role="vocab-gloss"] { color: var(--bee-muted, currentColor); }

/* Dark theme: derive slightly stronger separators so chips/badges stay legible
   on a dark background, and never paint a solid white card. The card inherits
   the viewer's background/text; only our token separators strengthen. */
@media (prefers-color-scheme: dark) {
  [data-sc-bee-role="detail"] {
    --bee-accent: #56b4e9;
    --bee-chip-border: color-mix(in srgb, currentColor 38%, transparent);
    --bee-chip-bg: color-mix(in srgb, currentColor 16%, transparent);
    --bee-badge-bg: color-mix(in srgb, currentColor 14%, transparent);
  }
}

/* Narrow / mobile-sized popups: stack the hero, chips, and chart, and collapse
   the six-word vocabulary grid to a single column instead of overflowing a
   compact pane. The pie centres above a full-width legend. */
@media (max-width: 24em) {
  [data-sc-bee-role="hero-glyph"] { font-size: 2em; }
  [data-sc-bee-role="reading-group"] { align-items: flex-start; }
  [data-sc-bee-role="vocab-grid"] { grid-template-columns: 1fr; }
}

/* Progressive disclosure: restrained, keyboard-focusable summaries with a
   quiet separator so the collapsed sections read as a distinct secondary tier
   below the always-visible card. */
[data-sc-bee-role="section"] {
  margin: 0.25em 0;
  border-top: 1px solid var(--bee-chip-border, currentColor);
  padding-top: 0.2em;
}
[data-sc-bee-role="section"] > summary {
  cursor: pointer;
  font-size: 0.9em;
  font-weight: 600;
  padding: 0.15em 0;
  color: var(--bee-muted, currentColor);
  list-style-position: inside;
}
[data-sc-bee-role="section"][open] > summary { color: var(--bee-accent, currentColor); }
[data-sc-bee-role="section"] > summary:hover { color: var(--bee-accent, currentColor); }
[data-sc-bee-role="section"] > summary:focus-visible {
  outline: 2px solid var(--bee-accent, currentColor);
  outline-offset: 2px;
  border-radius: 0.15em;
}

/* Reading-distribution chart: a packaged raster PNG pie plus a visible text
   legend carrying the same data. The image is bounded and scoped; the caption
   and legend degrade gracefully if the image cannot load.

   IMPORTANT: real Yomitan renders a structured-content <img> as
     a.gloss-image-link > span.gloss-image-container > canvas.gloss-image
   and DISCARDS the data attributes on the <img> itself. So the chart cannot be
   sized via its own data-sc marker -- we bound the PRESERVED .gloss-image-*
   wrappers, scoped under our own reading-pie <div> (a div IS preserved with
   its data-sc marker, unlike the img). */
[data-sc-bee-role="reading-donut"] {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.2em 0.9em;
  margin: 0.5em 0;
}
[data-sc-bee-role="donut-caption"] {
  flex-basis: 100%;
  font-size: 0.82em;
  font-weight: 700;
  letter-spacing: 0.03em;
  text-transform: uppercase;
  color: var(--bee-muted, currentColor);
  margin: 0 0 0.15em;
}
[data-sc-bee-role="reading-pie"] {
  display: inline-block;
  flex: 0 0 auto;
  vertical-align: middle;
}
[data-sc-bee-role="reading-pie"] .gloss-image-link {
  display: inline-block;
  vertical-align: middle;
}
[data-sc-bee-role="reading-pie"] .gloss-image-container {
  width: 4.25em;
  max-width: 4.25em;
  vertical-align: middle;
}
[data-sc-bee-role="donut-legend"] {
  flex: 1 1 9em;
  min-width: 8em;
  list-style: none;
  margin: 0;
  padding: 0;
  vertical-align: middle;
  font-size: 0.86em;
  line-height: 1.55;
}
[data-sc-bee-role="donut-legend"] li {
  display: flex;
  align-items: baseline;
  gap: 0.1em;
}
[data-sc-bee-role="donut-swatch"] {
  display: inline-block;
  width: 0.72em;
  height: 0.72em;
  border-radius: 0.18em;
  margin-right: 0.45em;
  flex: 0 0 auto;
  overflow: hidden;
  vertical-align: middle;
}

/* Phonetic family line: quiet, wraps gracefully. */
[data-sc-bee-role="phonetic-family"] { font-size: 0.95em; opacity: 0.9; }
[data-sc-bee-role="phon-source"] { opacity: 0.65; font-size: 0.85em; }

/* Stroke-order diagram: bounded, centred, with a text fallback beneath it.
   Same rendering contract as the chart -- bound the preserved wrapper Yomitan
   builds around the img, scoped under our own stroke-order <div>. */
[data-sc-bee-role="stroke-order"] .gloss-image-container {
  max-width: 6em;
  max-height: 6em;
}
[data-sc-bee-role="stroke-text"] { font-size: 0.9em; opacity: 0.9; }

/* Honour reduced-motion for any bundled animation the viewer might run. */
@media (prefers-reduced-motion: reduce) {
  [data-sc-bee-role="stroke-order"] .gloss-image { animation: none !important; }
}

/* Forced colours (Windows High Contrast): the OS replaces our colours with its
   own system palette, and color-mix()/transparent borders can collapse to
   nothing. Pin the card's separators, chips, badges, swatches, and focus rings
   to system colour keywords so every element stays outlined and legible, and
   force the legend swatches to print their assigned colour (the one place
   colour still helps map a segment to its legend line). */
@media (forced-colors: active) {
  [data-sc-bee-role="reading-chip"],
  [data-sc-bee-role="badge"] {
    border: 1px solid CanvasText;
  }
  [data-sc-bee-role="section"] { border-top-color: CanvasText; }
  [data-sc-bee-role="donut-swatch"] {
    border: 1px solid CanvasText;
    forced-color-adjust: none;
  }
  [data-sc-bee-role="vocab-word"] .gloss-link { color: LinkText; }
  [data-sc-bee-role="vocab-word"] .gloss-link:focus-visible,
  .gloss-link:focus-visible,
  [data-sc-bee-role="section"] > summary:focus-visible {
    outline: 2px solid Highlight;
  }
}
"""

# Only the structured single-character TERM banks ship. The native
# kanji_bank/kanji_meta_bank are deliberately NOT packaged: Yomitan's
# kanji-click / "view kanji" flow routes exclusively to the native kanji
# renderer (a fixed Meaning/Readings table dictionary CSS cannot restyle), so a
# shipped kanji_bank silently supersedes the rich structured card on the user's
# ordinary lookup path. Dropping it makes the structured term entry the single
# canonical visible surface for every character (verified against real Yomitan
# 26.7.29.0 in parent task t_08284e0e). build_banks still produces the kanji
# banks in-memory for unit coverage, but this table is the shipped/hashed set,
# so nothing reintroduces a competing flat entry.
BANK_FILES = [
    ("term_bank_1.json", "term_bank"),
    ("term_meta_bank_1.json", "term_meta_bank"),
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
        "description": "Kanji dictionary from Jiten data with KANJIDIC2 fallback coverage.",
        "attribution": ATTRIBUTION,
        "sourceLanguage": "ja",
        "targetLanguage": "en",
        "frequencyMode": "rank-based",
    }


def build_manifest(revision, content_hash, date, source_counts,
                   enrichment_counts, sitemap_size, code_revision,
                   frequency_stats=None, source_snapshot=None):
    """Build the machine-readable source/revision manifest (MANIFEST.json).

    Describes exactly how this canonical release was produced so an importer or
    auditor can trace every entry to its source: the dictionary revision, the
    revision-independent content hash, the UTC acquisition date, per-source
    record counts (Jiten stays authoritative; KANJIDIC2 is a licensed fallback
    for characters Jiten does not serve), the KanjiVG-derived enrichment counts,
    the code revision that generated it, and the licences/attribution. It is
    bundled in the ZIP (offline provenance) and published as a release asset.

    Every field is derived deterministically from the build inputs, so bundling
    it inside the ZIP does not disturb reproducible rebuilds.
    """
    sc = source_counts or {}
    ec = enrichment_counts or {}
    fs = frequency_stats or {}
    sources = {
        "jiten": {
            "url": "https://jiten.moe",
            "records": sc.get("jiten", 0),
            "sitemapCharacters": sitemap_size,
            "acquisition": "once per UTC day, unauthenticated",
            "role": "authoritative (enriched entries)",
        },
        "kanjidic2": {
            "url": "https://www.edrdg.org/wiki/index.php/KANJIDIC_Project",
            "records": sc.get("kanjidic2", 0),
            "role": "licensed fallback for characters Jiten does not serve",
        },
        "kanjivg": {
            "url": "https://kanjivg.tagaini.net/",
            "revision": KANJIVG_REVISION,
            "assets": ec.get("assets", 0),
            "role": "stroke-order diagrams + phonetic-component relationships",
        },
    }
    if fs:
        sources["jitenGlobalFrequency"] = {
            "url": fs.get("url", JITEN_FREQUENCY_CSV_URL),
            "sha256": fs.get("sha256", ""),
            "byteCount": fs.get("byteCount", 0),
            "retrievedDate": fs.get("retrievedDate", ""),
            "schema": fs.get("schema", "Word,Form,Rank"),
            "sourceRows": fs.get("sourceRows", 0),
            "exactDuplicateRows": fs.get("exactDuplicateRows", 0),
            "conflictingSurfaceReadingPairs": fs.get(
                "conflictingSurfaceReadingPairs", 0
            ),
            "excludedConflictingRows": fs.get("excludedConflictingRows", 0),
            "rows": fs.get("rows", 0),
            "relevantRows": fs.get("relevantRows", 0),
            "alignedRows": fs.get("alignedRows", 0),
            "ambiguousOrUnalignedRows": fs.get("ambiguousOrUnalignedRows", 0),
            "readingAssignments": fs.get("readingAssignments", 0),
            "relevantRankWeight": fs.get("relevantRankWeight", 0.0),
            "alignedRankWeight": fs.get("alignedRankWeight", 0.0),
            "rankWeightCoverage": fs.get("rankWeightCoverage", 0.0),
            "charactersWithScores": fs.get("charactersWithScores", 0),
            "readingGroupsWithScores": fs.get("readingGroupsWithScores", 0),
            "algorithm": fs.get("algorithm", ""),
            "algorithmSource": (
                "https://github.com/Sirush/Jiten/blob/"
                "eb0f493b9bee06a21656ff9698a7ff29520277ea/"
                "Jiten.Api/Jobs/ComputationJob.cs#L569-L590"
            ),
            "alignmentSource": (
                "https://github.com/Sirush/Jiten/blob/"
                "eb0f493b9bee06a21656ff9698a7ff29520277ea/"
                "Jiten.Core/Data/JMDict/KanjiReadingDecomposer.cs"
            ),
            "metric": fs.get("metric", ""),
            "semantics": "rank-derived weight, not observed occurrence probability",
            "modifications": (
                "exact duplicate rows collapsed; conflicting Word/Form ranks excluded; "
                "unique KANJIDIC-constrained kanji-reading alignments aggregated; "
                "CSV-only calculation omits Jiten's SQL fallback rank for unranked "
                "forms and does not apply its 3% pruning/renormalization stage"
            ),
            "acquisition": "once per UTC day, unauthenticated bulk CSV",
            "licence": "CC BY-SA 4.0",
            "licenceUrl": "https://creativecommons.org/licenses/by-sa/4.0/",
        }
    snapshot = source_snapshot or {}
    if snapshot:
        sources["sourceSnapshot"] = {
            "asset": SOURCE_SNAPSHOT_NAME,
            "sha256": snapshot.get("sha256", ""),
            "byteCount": snapshot.get("byteCount", 0),
            "lockAsset": SOURCE_LOCK_NAME,
            "lockSha256": snapshot.get("lockSha256", ""),
            "fileCount": snapshot.get("fileCount", 0),
            "role": "exact dated inputs sufficient for an offline rebuild",
        }
    return {
        "title": TITLE,
        "revision": revision,
        "contentHash": content_hash,
        "buildDate": date,
        "downloadUrl": f"https://github.com/{REPO}/releases/latest/download/{ZIP_NAME}",
        "indexUrl": f"https://raw.githubusercontent.com/{REPO}/main/dist/index.json",
        "url": f"https://github.com/{REPO}",
        "codeRevision": code_revision,
        "sources": sources,
        "records": {
            "total": sc.get("jiten", 0) + sc.get("kanjidic2", 0),
            "jiten": sc.get("jiten", 0),
            "kanjidic2Fallback": sc.get("kanjidic2", 0),
        },
        "enrichment": {
            "strokeSets": ec.get("strokes", 0),
            "phoneticFamilies": ec.get("families", 0),
            "bundledSvgAssets": ec.get("assets", 0),
        },
        "attribution": ATTRIBUTION,
        "licences": [
            "Dictionary data (JMdict/KANJIDIC via Jiten, EDRDG): CC BY-SA 4.0",
            "KanjiVG stroke/phonetic data: CC BY-SA 3.0",
            "Generator code: MIT",
        ],
    }


def content_hash(banks, assets=None, source_counts=None,
                 enrichment_counts=None, sitemap_size=0, frequency_stats=None):
    """SHA-256 over all revision-independent package content.

    The revision and generated manifest fields are excluded, but banks, assets,
    updater metadata, styles, and bundled licence notices are covered. Any
    user-visible package change therefore requires a fresh release revision.
    """
    hash_frequency_stats = dict(frequency_stats or {})
    # Acquisition time is provenance, not package content. Identical source
    # bytes and generated data must hash identically on consecutive UTC days.
    hash_frequency_stats.pop("retrievedDate", None)
    material = {
        "banks": {name: banks[name] for _, name in BANK_FILES},
        "package": {
            "index": build_index(""),
            "manifest": build_manifest(
                revision="",
                content_hash="",
                date="",
                source_counts=source_counts or {},
                enrichment_counts=enrichment_counts or {},
                sitemap_size=sitemap_size,
                code_revision="",
                frequency_stats=hash_frequency_stats,
            ),
            "styles.css": STYLES_CSS,
            "LICENSE-data.txt": LICENSE_DATA_TEXT,
            "LICENSE-kanjivg.txt": LICENSE_KANJIVG_TEXT if (
                assets and any(str(p).lower().endswith(".svg") for p in assets)
            ) else None,
        },
    }
    if assets:
        material["assets"] = {
            k: (
                "sha256:" + hashlib.sha256(assets[k]).hexdigest()
                if isinstance(assets[k], (bytes, bytearray))
                else assets[k]
            )
            for k in sorted(assets)
        }
    payload = dump_json(material)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Fixed ZIP member metadata for reproducible archives.
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def _zip_member(name, data):
    info = zipfile.ZipInfo(filename=name, date_time=_ZIP_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.create_system = 3  # unix
    if isinstance(data, str):
        data = data.encode("utf-8")
    return info, data


MAX_SOURCE_SNAPSHOT_BYTES = 128 * 1024 * 1024
MAX_SOURCE_MEMBER_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class ConsumedSource:
    """Digest-bound bytes consumed by the build before release snapshotting."""

    path: pathlib.Path | None
    raw: bytes | None
    byte_count: int
    sha256: str
    max_bytes: int


def record_consumed_source(
    sources, archive_name, raw, *, path=None, max_bytes=MAX_SOURCE_MEMBER_BYTES
):
    """Record one canonical consumed input without trusting a later cache reread."""
    if not isinstance(sources, dict):
        raise MalformedPayload("consumed source registry is invalid")
    if not isinstance(archive_name, str) or "\\" in archive_name:
        raise MalformedPayload("consumed source archive name is invalid")
    pure = pathlib.PurePosixPath(archive_name)
    if (
        not archive_name
        or pure.is_absolute()
        or ".." in pure.parts
        or "." in pure.parts
        or str(pure) != archive_name
    ):
        raise MalformedPayload("consumed source archive name is unsafe")
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or max_bytes < 0
    ):
        raise MalformedPayload("consumed source byte limit is invalid")
    if not isinstance(raw, bytes) or len(raw) > max_bytes:
        raise MalformedPayload("consumed source exceeds its byte limit")
    item = ConsumedSource(
        path=pathlib.Path(path) if path is not None else None,
        raw=None if path is not None else raw,
        byte_count=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        max_bytes=max_bytes,
    )
    previous = sources.get(archive_name)
    if previous is not None and previous != item:
        raise MalformedPayload(f"conflicting consumed source: {archive_name}")
    sources[archive_name] = item


def build_source_snapshot(*, date, consumed_sources):
    """Archive only digest-bound bytes that the build actually consumed."""
    if not isinstance(consumed_sources, dict) or not consumed_sources:
        raise MalformedPayload("consumed source registry is required")
    if any(not isinstance(item, ConsumedSource) for item in consumed_sources.values()):
        raise MalformedPayload("consumed source registry contains an invalid item")
    for name, item in consumed_sources.items():
        pure = pathlib.PurePosixPath(name) if isinstance(name, str) else None
        if (
            pure is None
            or not name
            or "\\" in name
            or pure.is_absolute()
            or "." in pure.parts
            or ".." in pure.parts
            or str(pure) != name
        ):
            raise MalformedPayload("consumed source registry contains an unsafe name")
        if (
            isinstance(item.byte_count, bool)
            or not isinstance(item.byte_count, int)
            or item.byte_count < 0
            or isinstance(item.max_bytes, bool)
            or not isinstance(item.max_bytes, int)
            or item.max_bytes < item.byte_count
            or not isinstance(item.sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", item.sha256) is None
            or (item.path is None) == (item.raw is None)
        ):
            raise MalformedPayload("consumed source registry contains invalid metadata")
    declared_total = sum(item.byte_count for item in consumed_sources.values())
    if declared_total > MAX_SOURCE_SNAPSHOT_BYTES:
        raise MalformedPayload("source snapshot exceeds the total byte limit")

    members = {}
    for name, item in sorted(consumed_sources.items()):
        if item.raw is not None:
            raw = item.raw
        else:
            path = item.path
            if path is None or path.is_symlink() or not path.is_file():
                raise MalformedPayload(f"consumed source is missing or invalid: {name}")
            try:
                size = path.stat().st_size
            except OSError as exc:
                raise MalformedPayload(f"consumed source cannot be inspected: {name}") from exc
            if size != item.byte_count:
                raise MalformedPayload(f"consumed source changed after consumption: {name}")
            raw = _read_file_bytes_bounded(
                path,
                min(item.max_bytes, item.byte_count),
                "digest-bound consumed source",
            )
        if (
            len(raw) != item.byte_count
            or hashlib.sha256(raw).hexdigest() != item.sha256
        ):
            raise MalformedPayload(f"consumed source changed after consumption: {name}")
        members[name] = raw

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    uv_lock_bytes = _read_file_bytes_bounded(
        repo_root / "uv.lock", 1024 * 1024, "uv dependency lock"
    )
    package_lock_bytes = _read_file_bytes_bounded(
        repo_root / "package-lock.json", 1024 * 1024, "npm dependency lock"
    )
    lock = {
        "schemaVersion": 1,
        "acquisitionDate": date,
        "generator": {
            "repository": f"https://github.com/{REPO}",
            "codeRevision": _code_revision(),
            "container": PINNED_BUILD_CONTAINER,
            "python": ".".join(str(part) for part in sys.version_info[:3]),
            "pillow": importlib.metadata.version("Pillow"),
            "uv": "0.11.28",
            "node": "22.23.1",
            "uvLockSha256": hashlib.sha256(uv_lock_bytes).hexdigest(),
            "packageLockSha256": hashlib.sha256(package_lock_bytes).hexdigest(),
        },
        "sources": {
            "jitenKanjiApi": {"sitemapUrl": SITEMAP_URL, "apiBase": API_BASE},
            "jitenGlobalFrequency": {"url": JITEN_FREQUENCY_CSV_URL},
            "kanjidic2": {"url": KANJIDIC2_URL},
            "kanjiVg": {"revision": KANJIVG_REVISION, "baseUrl": KANJIVG_BASE},
        },
        "files": [
            {
                "path": name,
                "byteCount": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
            for name, raw in sorted(members.items())
        ],
    }
    lock_bytes = dump_json(lock).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(members.items()):
            info, payload = _zip_member(name, raw)
            archive.writestr(info, payload)
        info, payload = _zip_member(SOURCE_LOCK_NAME, lock_bytes)
        archive.writestr(info, payload)
    return buf.getvalue(), lock


def build_zip(banks, revision, assets=None, manifest=None):
    """Build a deterministic Yomitan ZIP (bytes) with all members at the root.

    `assets` is an optional {path: text} map of extra bundled files (e.g.
    sanitized KanjiVG SVGs under kanjivg/). `manifest` is an optional
    source/revision manifest dict (see build_manifest) bundled as MANIFEST.json
    so provenance travels inside the package. styles.css is always bundled; the
    KanjiVG licence notice is bundled only when KanjiVG-derived assets ship, to
    honour the share-alike obligation for exactly what is redistributed. Member
    order, timestamps, and permissions are fixed so two builds from identical
    inputs are byte-identical regardless of dict insertion order.
    """
    assets = assets or {}
    members = [("index.json", dump_json(build_index(revision)))]
    if manifest is not None:
        members.append(("MANIFEST.json", dump_json(manifest)))
    for filename, key in BANK_FILES:
        members.append((filename, dump_json(banks[key])))
    members.append(("styles.css", STYLES_CSS))
    members.append(("LICENSE-data.txt", LICENSE_DATA_TEXT))
    if any(str(path).lower().endswith(".svg") for path in assets):
        members.append(("LICENSE-kanjivg.txt", LICENSE_KANJIVG_TEXT))
    # Sort asset paths for deterministic ordering irrespective of insertion.
    for path in sorted(assets):
        members.append((path, assets[path]))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, payload in members:
            info, data = _zip_member(name, payload)
            zf.writestr(info, data)
    return buf.getvalue()


def bind_release_artifacts_to_code_revision(out_dir, code_revision, revision=None):
    """Rebind built release artifacts to the commit that will own their tag.

    The updater files are committed after the initial content build, so the
    final release commit does not exist when that build starts. Rewriting only
    the revision-independent provenance field after the updater commit avoids
    publishing a manifest that points at its parent. ZIP metadata and member
    order stay deterministic, the standalone manifest stays byte-identical to
    the bundled copy, and SHA256SUMS is regenerated for the changed bytes.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", code_revision):
        raise ValueError("code_revision must be a full 40-character git object ID")

    out_dir = pathlib.Path(out_dir)
    zip_path = out_dir / ZIP_NAME
    manifest_path = out_dir / "MANIFEST.json"
    checksum_path = out_dir / "SHA256SUMS"

    with zipfile.ZipFile(zip_path) as source:
        infos = source.infolist()
        names = [info.filename for info in infos]
        if names.count("MANIFEST.json") != 1 or names.count("index.json") != 1:
            raise ValueError("release ZIP must contain exactly one manifest and index")
        members = [(info.filename, source.read(info)) for info in infos]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["codeRevision"] = code_revision
    if revision is not None:
        if not re.fullmatch(r"[0-9]{4}\.[0-9]{2}\.[0-9]{2}(?:\.[0-9]+)?", revision):
            raise ValueError("revision must be a versioned YYYY.MM.DD release revision")
        manifest["revision"] = revision
    manifest_bytes = dump_json(manifest).encode("utf-8")
    replaced = False

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as target:
        for name, data in members:
            if name == "MANIFEST.json":
                data = manifest_bytes
                replaced = True
            elif name == "index.json" and revision is not None:
                index = json.loads(data)
                index["revision"] = revision
                data = dump_json(index).encode("utf-8")
            info, data = _zip_member(name, data)
            target.writestr(info, data)
    if not replaced:
        raise ValueError("release ZIP does not contain MANIFEST.json")

    zip_bytes = buf.getvalue()
    zip_path.write_bytes(zip_bytes)
    manifest_path.write_bytes(manifest_bytes)
    checksum_lines = [
        f"{hashlib.sha256(zip_bytes).hexdigest()}  {ZIP_NAME}",
        f"{hashlib.sha256(manifest_bytes).hexdigest()}  MANIFEST.json",
    ]
    for asset_name in (
        JITEN_FREQUENCY_ASSET_NAME,
        SOURCE_SNAPSHOT_NAME,
        SOURCE_LOCK_NAME,
    ):
        asset_path = out_dir / asset_name
        if not asset_path.exists():
            continue
        digest = hashlib.sha256()
        with asset_path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        checksum_lines.append(
            f"{digest.hexdigest()}  {asset_name}"
        )
    checksum_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")


# --- Daily acquisition with a resumable per-character cache ------------------

API_BASE = "https://api.jiten.moe/api/kanji"
SITEMAP_URL = f"{API_BASE}/sitemap-characters"
USER_AGENT = "bees-ultimate-kanji-dictionary (+https://github.com/bee-san/bees-ultimate-kanji-dictionary)"
TIMEOUT_SECONDS = 30
MAX_HTTP_TOTAL_SECONDS = 120
MAX_HTTP_RESPONSE_BYTES = 16 * 1024 * 1024
HTTP_CHUNK_BYTES = 64 * 1024
MAX_RETRIES = 2  # bounded retries for transport / 429 / 5xx only


class NotFound(Exception):
    """The Jiten API returned 404 for a character (skip it)."""


def cache_filename(character):
    """Filesystem-safe cache filename for a character (by code points)."""
    codepoints = "_".join(f"{ord(c):x}" for c in character)
    return f"{codepoints}.json"


def _read_response_bounded(response, max_bytes, deadline):
    """Stream one HTTP response without trusting its declared or actual size."""
    raw_length = response.headers.get("Content-Length")
    expected_length = None
    if raw_length is not None:
        try:
            expected_length = int(raw_length)
        except (TypeError, ValueError) as exc:
            raise MalformedPayload("HTTP response has invalid Content-Length") from exc
        if expected_length < 0 or expected_length > max_bytes:
            raise MalformedPayload("HTTP response Content-Length exceeds the byte limit")

    chunks = []
    total = 0
    while True:
        if time.monotonic() > deadline:
            raise MalformedPayload("HTTP transfer exceeded the total deadline")
        chunk = response.read(min(HTTP_CHUNK_BYTES, max_bytes + 1 - total))
        if time.monotonic() > deadline:
            raise MalformedPayload("HTTP transfer exceeded the total deadline")
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise MalformedPayload("HTTP response exceeds the byte limit")
        chunks.append(chunk)
    if expected_length is not None and total != expected_length:
        raise MalformedPayload("HTTP response ended before Content-Length bytes arrived")
    return b"".join(chunks)


def _read_file_bytes_bounded(path, max_bytes, label):
    """Read a cache/source file once with a hard size bound."""
    path = pathlib.Path(path)
    if path.stat().st_size > max_bytes:
        raise MalformedPayload(f"{label} exceeds the byte limit")
    with path.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise MalformedPayload(f"{label} exceeds the byte limit")
    return raw


def http_get_bytes(
    url, max_bytes=MAX_HTTP_RESPONSE_BYTES, total_seconds=MAX_HTTP_TOTAL_SECONDS
):
    """GET bounded raw bytes with bounded retries and a total deadline."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_err = None
    deadline = time.monotonic() + total_seconds
    for attempt in range(MAX_RETRIES + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MalformedPayload("HTTP transfer exceeded the total deadline")
        try:
            with urllib.request.urlopen(
                req, timeout=min(TIMEOUT_SECONDS, max(1.0, remaining))
            ) as response:
                return _read_response_bounded(response, max_bytes, deadline)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NotFound(url) from exc
            if exc.code == 429 or 500 <= exc.code < 600:
                last_err = exc
            else:
                raise
        except urllib.error.URLError as exc:
            last_err = exc
        if attempt < MAX_RETRIES:
            delay = 2 ** attempt
            if time.monotonic() + delay > deadline:
                raise MalformedPayload("HTTP transfer exceeded the total deadline")
            time.sleep(delay)
    raise last_err if last_err is not None else RuntimeError(
        f"failed to GET raw bytes from {url}"
    )


def http_get_json(url, max_bytes=MAX_HTTP_RESPONSE_BYTES):
    """GET and parse bounded UTF-8 JSON."""
    raw = http_get_bytes(url, max_bytes=max_bytes)
    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise MalformedPayload("HTTP JSON response is not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise MalformedPayload("HTTP response is not valid JSON") from exc


def fetch_kanji(character):
    """Fetch a single kanji payload from the live Jiten API."""
    return http_get_json(f"{API_BASE}/{urllib.parse.quote(character)}")


def fetch_sitemap():
    """Fetch the list of characters from the Jiten sitemap endpoint."""
    data = http_get_json(SITEMAP_URL)
    if not isinstance(data, list):
        raise MalformedPayload("sitemap is not a list")
    return [c for c in data if isinstance(c, str) and c]


def fetch_sitemap_cached(
    cache_dir, date, fetcher=fetch_sitemap, offline=False, consumed_sources=None
):
    """Return the daily sitemap, reusing an atomic dated cache on reruns."""
    import pathlib as _pl

    day_dir = _pl.Path(cache_dir) / date
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / "sitemap.json"
    if path.exists():
        try:
            raw = _read_file_bytes_bounded(
                path, MAX_HTTP_RESPONSE_BYTES, "cached Jiten sitemap"
            )
            data = json.loads(raw.decode("utf-8"))
            if isinstance(data, list) and all(isinstance(c, str) and c for c in data):
                if consumed_sources is not None:
                    record_consumed_source(
                        consumed_sources,
                        f"cache/{date}/sitemap.json",
                        raw,
                        path=path,
                        max_bytes=MAX_HTTP_RESPONSE_BYTES,
                    )
                return data
        except (ValueError, OSError, UnicodeDecodeError, MalformedPayload):
            pass
    if offline:
        raise FileNotFoundError(f"Jiten sitemap cache missing: {path}")
    data = fetcher()
    if not isinstance(data, list) or not all(isinstance(c, str) and c for c in data):
        raise MalformedPayload("sitemap is not a list of characters")
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    if len(raw) > MAX_HTTP_RESPONSE_BYTES:
        raise MalformedPayload("Jiten sitemap exceeds the byte limit")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_bytes(raw)
    tmp.replace(path)
    data = json.loads(raw.decode("utf-8"))
    if consumed_sources is not None:
        record_consumed_source(
            consumed_sources,
            f"cache/{date}/sitemap.json",
            raw,
            path=path,
            max_bytes=MAX_HTTP_RESPONSE_BYTES,
        )
    return data


def fetch_jiten_global_frequency_csv():
    """Fetch Jiten's official downloadable Global frequency CSV once."""
    return http_get_bytes(
        JITEN_FREQUENCY_CSV_URL,
        max_bytes=MAX_JITEN_FREQUENCY_BYTES,
    )


@dataclass(frozen=True)
class JitenFrequencySourceSnapshot:
    """One immutable source snapshot used for scoring, provenance, and release."""

    raw: bytes
    text: str
    sha256: str
    path: pathlib.Path
    from_cache: bool


def _jiten_frequency_snapshot(raw, path, from_cache):
    if not isinstance(raw, bytes) or not raw:
        raise MalformedPayload("Jiten frequency CSV download is empty")
    if len(raw) > MAX_JITEN_FREQUENCY_BYTES:
        raise MalformedPayload("Jiten frequency CSV exceeds the byte limit")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MalformedPayload("Jiten frequency CSV is not valid UTF-8") from exc
    parse_jiten_frequency_csv(text)
    return JitenFrequencySourceSnapshot(
        raw=raw,
        text=text,
        sha256=hashlib.sha256(raw).hexdigest(),
        path=pathlib.Path(path),
        from_cache=from_cache,
    )


def acquire_jiten_frequency_csv_source(
    cache_dir, date, fetcher=fetch_jiten_global_frequency_csv, offline=False
):
    """Return one validated, byte-bounded, immutable dated source snapshot."""
    day_dir = pathlib.Path(cache_dir) / date
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / "jiten_freq_global.csv"
    if path.exists():
        try:
            raw = _read_file_bytes_bounded(
                path,
                MAX_JITEN_FREQUENCY_BYTES,
                "cached Jiten frequency CSV",
            )
            return _jiten_frequency_snapshot(raw, path, True)
        except (OSError, UnicodeDecodeError, MalformedPayload) as exc:
            if offline:
                raise MalformedPayload(
                    f"cached Jiten frequency CSV is invalid: {path}"
                ) from exc
    elif offline:
        raise FileNotFoundError(f"Jiten frequency CSV cache missing: {path}")

    source = fetcher()
    if isinstance(source, str):
        raw = source.encode("utf-8")
    elif isinstance(source, bytes):
        raw = source
    else:
        raise MalformedPayload("Jiten frequency CSV fetcher returned no bytes")
    snapshot = _jiten_frequency_snapshot(raw, path, False)
    # Validate completely before atomically replacing any same-day cache entry.
    tmp = path.with_suffix(".csv.tmp")
    tmp.write_bytes(snapshot.raw)
    tmp.replace(path)
    return snapshot


def _quarantine_jiten_frequency_snapshot(snapshot):
    """Remove only the exact cached bytes that failed production quality gates."""
    try:
        current = _read_file_bytes_bounded(
            snapshot.path,
            MAX_JITEN_FREQUENCY_BYTES,
            "cached Jiten frequency CSV",
        )
    except (FileNotFoundError, OSError, MalformedPayload):
        return
    if hashlib.sha256(current).hexdigest() == snapshot.sha256:
        snapshot.path.unlink(missing_ok=True)


def fetch_jiten_frequency_csv_source(
    cache_dir, date, fetcher=fetch_jiten_global_frequency_csv, offline=False
):
    """Compatibility wrapper returning decoded text from the immutable snapshot."""
    return acquire_jiten_frequency_csv_source(
        cache_dir, date, fetcher, offline
    ).text


def jiten_frequency_csv_digest(cache_dir, date):
    """SHA-256 of a bounded exact cached source snapshot."""
    path = pathlib.Path(cache_dir) / date / "jiten_freq_global.csv"
    raw = _read_file_bytes_bounded(
        path, MAX_JITEN_FREQUENCY_BYTES, "cached Jiten frequency CSV"
    )
    return hashlib.sha256(raw).hexdigest()


def fetch_all(
    characters, cache_dir, date, fetcher=fetch_kanji, consumed_sources=None
):
    """Fetch payloads for characters, using a resumable dated on-disk cache.

    Cache layout: cache_dir/DATE/<codepoints>.json. Files already present for
    DATE are reused (no fetcher call). Only missing characters are fetched
    sequentially. 404s are negatively cached for that day and not returned. Returns an
    ordered dict {character: payload} for the characters that produced data.
    """
    import pathlib as _pl

    day_dir = _pl.Path(cache_dir) / date
    day_dir.mkdir(parents=True, exist_ok=True)

    out = {}
    for char in characters:
        path = day_dir / cache_filename(char)
        missing = path.with_suffix(".missing")
        if path.exists():
            try:
                raw = _read_file_bytes_bounded(
                    path, MAX_HTTP_RESPONSE_BYTES, "cached Jiten kanji payload"
                )
                out[char] = json.loads(raw.decode("utf-8"))
                if consumed_sources is not None:
                    record_consumed_source(
                        consumed_sources,
                        f"cache/{date}/{path.name}",
                        raw,
                        path=path,
                        max_bytes=MAX_HTTP_RESPONSE_BYTES,
                    )
                missing.unlink(missing_ok=True)
                continue
            except (ValueError, OSError, UnicodeDecodeError, MalformedPayload):
                path.unlink(missing_ok=True)
                missing.unlink(missing_ok=True)
        if missing.exists():
            if consumed_sources is not None:
                record_consumed_source(
                    consumed_sources,
                    f"cache/{date}/{missing.name}",
                    b"",
                    path=missing,
                    max_bytes=0,
                )
            continue
        try:
            payload = fetcher(char)
        except NotFound:
            missing.touch()
            if consumed_sources is not None:
                record_consumed_source(
                    consumed_sources,
                    f"cache/{date}/{missing.name}",
                    b"",
                    path=missing,
                    max_bytes=0,
                )
            continue  # skip 404s
        # atomic-ish write: temp then replace
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(raw) > MAX_HTTP_RESPONSE_BYTES:
            raise MalformedPayload("Jiten kanji payload exceeds the byte limit")
        tmp = path.with_suffix(".json.tmp")
        tmp.write_bytes(raw)
        tmp.replace(path)
        out[char] = json.loads(raw.decode("utf-8"))
        if consumed_sources is not None:
            record_consumed_source(
                consumed_sources,
                f"cache/{date}/{path.name}",
                raw,
                path=path,
                max_bytes=MAX_HTTP_RESPONSE_BYTES,
            )
    return out


# --- KanjiVG asset acquisition (same resumable dated-cache pattern) ----------

KANJIVG_REVISION = "61e39cfc29724132a6f8823b166296932985a0ff"
KANJIVG_BASE = f"https://raw.githubusercontent.com/KanjiVG/kanjivg/{KANJIVG_REVISION}/kanji"
MIN_KANJIVG_STROKE_SETS = 6_000


def validate_kanjivg_coverage(stroke_sets, *, cache_dir=None, date=None):
    """Reject partial production media and invalidate poisoned negative markers."""
    if (
        isinstance(stroke_sets, bool)
        or not isinstance(stroke_sets, int)
        or stroke_sets < MIN_KANJIVG_STROKE_SETS
    ):
        if cache_dir is not None and date is not None:
            day = pathlib.Path(cache_dir) / date
            for marker in day.glob("*.missing") if day.is_dir() else ():
                if marker.is_file() and not marker.is_symlink():
                    marker.unlink()
        raise MalformedPayload(
            "KanjiVG coverage floor failed: "
            f"strokeSets={stroke_sets!r} < {MIN_KANJIVG_STROKE_SETS}"
        )


def kanjivg_cache_filename(character):
    """Filesystem-safe KanjiVG cache filename (zero-padded codepoint .svg)."""
    return f"{ord(character):05x}.svg"


def http_get_text(url, max_bytes=MAX_HTTP_RESPONSE_BYTES):
    """GET bounded UTF-8 text through the common streaming transport."""
    try:
        return http_get_bytes(url, max_bytes=max_bytes).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MalformedPayload("HTTP text response is not valid UTF-8") from exc


def fetch_kanjivg(character):
    """Fetch a single KanjiVG SVG for a character from the live source."""
    return http_get_text(f"{KANJIVG_BASE}/{kanjivg_cache_filename(character)}")


def fetch_kanjivg_all(
    characters, cache_dir, date, fetcher=fetch_kanjivg, consumed_sources=None
):
    """Fetch KanjiVG SVGs, reusing a resumable dated on-disk cache.

    Cache layout mirrors the Jiten fetch: cache_dir/DATE/<codepoint>.svg. Files
    present for DATE are reused (no fetcher call); only missing characters are
    fetched sequentially; 404s are negatively cached for that day. Returns
    {character: svg_text}. No
    new machinery -- the same once-per-day, resumable, cache-first flow.
    """
    import pathlib as _pl

    day_dir = _pl.Path(cache_dir) / date
    day_dir.mkdir(parents=True, exist_ok=True)

    out = {}
    for char in characters:
        path = day_dir / kanjivg_cache_filename(char)
        missing = path.with_suffix(".missing")
        if path.exists():
            try:
                raw = _read_file_bytes_bounded(
                    path, MAX_HTTP_RESPONSE_BYTES, "cached KanjiVG SVG"
                )
                svg = raw.decode("utf-8")
                if parse_kanjivg(svg, char) is None:
                    raise MalformedPayload("cached KanjiVG SVG does not match the character")
                out[char] = svg
                if consumed_sources is not None:
                    record_consumed_source(
                        consumed_sources,
                        f"kanjivg-cache/{date}/{path.name}",
                        raw,
                        path=path,
                        max_bytes=MAX_HTTP_RESPONSE_BYTES,
                    )
                missing.unlink(missing_ok=True)
                continue
            except (OSError, UnicodeDecodeError, MalformedPayload):
                path.unlink(missing_ok=True)
                missing.unlink(missing_ok=True)
        if missing.exists():
            if consumed_sources is not None:
                record_consumed_source(
                    consumed_sources,
                    f"kanjivg-cache/{date}/{missing.name}",
                    b"",
                    path=missing,
                    max_bytes=0,
                )
            continue
        try:
            svg = fetcher(char)
        except NotFound:
            missing.touch()
            if consumed_sources is not None:
                record_consumed_source(
                    consumed_sources,
                    f"kanjivg-cache/{date}/{missing.name}",
                    b"",
                    path=missing,
                    max_bytes=0,
                )
            continue
        if not isinstance(svg, str) or parse_kanjivg(svg, char) is None:
            raise MalformedPayload("KanjiVG fetcher returned an invalid character SVG")
        raw = svg.encode("utf-8")
        if len(raw) > MAX_HTTP_RESPONSE_BYTES:
            raise MalformedPayload("KanjiVG SVG exceeds the byte limit")
        tmp = path.with_suffix(".svg.tmp")
        tmp.write_bytes(raw)
        tmp.replace(path)
        out[char] = raw.decode("utf-8")
        if consumed_sources is not None:
            record_consumed_source(
                consumed_sources,
                f"kanjivg-cache/{date}/{path.name}",
                raw,
                path=path,
                max_bytes=MAX_HTTP_RESPONSE_BYTES,
            )
    return out


def assemble_enrichment(svgs, ranks):
    """Assemble deterministic stroke info, phonetic families, and SVG assets.

    svgs:  {character: kanjivg_svg_text}. ranks: {character: frequency_rank}.
    Returns {
        "strokes": {char: parse_kanjivg(...)},
        "families": {component: family},
        "families_by_char": {char: family},   # convenience lookup
        "assets": {asset_path: sanitized_svg}, # only referenced assets
    }.
    """
    strokes = {}
    phon_map = {}
    assets = {}
    for char in sorted(svgs, key=ord):
        svg = svgs[char]
        info = parse_kanjivg(svg, char)
        if info is None:
            continue
        strokes[char] = info
        assets[info["asset"]] = sanitize_kanjivg_svg(svg, char)
        comp = extract_phonetic_component(svg, char)
        if comp:
            phon_map[char] = comp

    families = build_phonetic_families(phon_map, ranks)
    families_by_char = {}
    for fam in families.values():
        for member in fam["members"]:
            families_by_char[member] = fam

    return {
        "strokes": strokes,
        "families": families,
        "families_by_char": families_by_char,
        "assets": assets,
    }


# --- KANJIDIC2 static-source acquisition (once-per-day, resumable cache) ------

KANJIDIC2_URL = "https://www.edrdg.org/kanjidic/kanjidic2.xml.gz"
MAX_KANJIDIC2_COMPRESSED_BYTES = 64 * 1024 * 1024
MAX_KANJIDIC2_XML_BYTES = 128 * 1024 * 1024


def fetch_kanjidic2():
    """Fetch and gunzip KANJIDIC2 with compressed and expanded size limits."""
    import gzip

    compressed = http_get_bytes(
        KANJIDIC2_URL, max_bytes=MAX_KANJIDIC2_COMPRESSED_BYTES
    )
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as archive:
            raw = archive.read(MAX_KANJIDIC2_XML_BYTES + 1)
    except (gzip.BadGzipFile, OSError) as exc:
        raise MalformedPayload("KANJIDIC2 download is not valid gzip") from exc
    if len(raw) > MAX_KANJIDIC2_XML_BYTES:
        raise MalformedPayload("KANJIDIC2 XML exceeds the expanded byte limit")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MalformedPayload("KANJIDIC2 XML is not valid UTF-8") from exc


def fetch_kanjidic2_source(
    cache_dir, date, fetcher=fetch_kanjidic2, consumed_sources=None
):
    """Return the KANJIDIC2 XML for DATE, fetching the static source once a day.

    Mirrors the Jiten/KanjiVG resumable dated-cache flow: the decompressed XML
    is stored at cache_dir/DATE/kanjidic2.xml and reused on same-day reruns, so
    the daily workflow downloads the static source at most once per UTC day.
    """
    import pathlib as _pl

    day_dir = _pl.Path(cache_dir) / date
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / "kanjidic2.xml"
    if path.exists():
        try:
            raw = _read_file_bytes_bounded(
                path, MAX_KANJIDIC2_XML_BYTES, "cached KANJIDIC2 XML"
            )
            text = raw.decode("utf-8")
            if consumed_sources is not None:
                record_consumed_source(
                    consumed_sources,
                    f"kanjidic2-cache/{date}/kanjidic2.xml",
                    raw,
                    path=path,
                    max_bytes=MAX_KANJIDIC2_XML_BYTES,
                )
            return text
        except (OSError, UnicodeDecodeError, MalformedPayload):
            pass
    xml_text = fetcher()
    if not isinstance(xml_text, str):
        raise MalformedPayload("KANJIDIC2 fetcher returned non-text data")
    xml_bytes = xml_text.encode("utf-8")
    if len(xml_bytes) > MAX_KANJIDIC2_XML_BYTES:
        raise MalformedPayload("KANJIDIC2 XML exceeds the expanded byte limit")
    tmp = path.with_suffix(".xml.tmp")
    tmp.write_bytes(xml_bytes)
    tmp.replace(path)
    if consumed_sources is not None:
        record_consumed_source(
            consumed_sources,
            f"kanjidic2-cache/{date}/kanjidic2.xml",
            xml_bytes,
            path=path,
            max_bytes=MAX_KANJIDIC2_XML_BYTES,
        )
    return xml_bytes.decode("utf-8")


# --- Build pipeline and revision decision ------------------------------------

def run_build(characters, cache_dir, date, aliases=None, fetcher=fetch_kanji,
              kanjivg_cache_dir=None, kanjivg_fetcher=fetch_kanjivg,
              kanjidic2_cache_dir=None, kanjidic2_fetcher=fetch_kanjidic2,
              frequency_cache_dir=None,
              frequency_fetcher=fetch_jiten_global_frequency_csv,
              enforce_frequency_quality=False,
              frequency_offline=False,
              consumed_sources=None):
    """Fetch (via cache) -> normalize -> merge -> enrich -> build banks + ZIP.

    Acquires Jiten payloads AND (when kanjivg_cache_dir is given) KanjiVG SVGs,
    both through the same resumable dated cache. When kanjidic2_cache_dir is
    given, the static KANJIDIC2 XML is fetched once for the day and every
    character it covers that Jiten does NOT serve is added as a simple honest
    fallback record (Jiten stays authoritative on every duplicate). Assembles
    the visual enrichment (stroke info, phonetic families, sanitized SVG assets)
    over the merged record set and threads it into the term entries and the
    canonical ZIP. content_hash is revision-independent and covers the
    enrichment (assets), so a change in stroke/phonetic/fallback data triggers a
    new revision. Returns {records, banks, enrichment, content_hash,
    zip_bytes(placeholder revision), source_counts}.
    """
    if consumed_sources is None:
        consumed_sources = {}
    payloads = fetch_all(
        characters, cache_dir, date, fetcher, consumed_sources=consumed_sources
    )
    frequency_scores = {}
    frequency_stats = {}
    frequency_source = None
    if frequency_cache_dir is not None:
        frequency_source = acquire_jiten_frequency_csv_source(
            frequency_cache_dir, date, frequency_fetcher, offline=frequency_offline
        )
        record_consumed_source(
            consumed_sources,
            f"jiten-frequency-cache/{date}/jiten_freq_global.csv",
            frequency_source.raw,
            max_bytes=MAX_JITEN_FREQUENCY_BYTES,
        )
        frequency_rows, parse_stats = parse_jiten_frequency_csv_with_stats(
            frequency_source.text
        )
        frequency_scores, alignment_stats = (
            _calculate_reading_frequency_scores_with_stats(payloads, frequency_rows)
        )
        frequency_stats = {**parse_stats, **alignment_stats}
        frequency_stats["byteCount"] = len(frequency_source.raw)
        frequency_stats["sha256"] = frequency_source.sha256
        frequency_stats["url"] = JITEN_FREQUENCY_CSV_URL
        frequency_stats["retrievedDate"] = date
        frequency_stats["schema"] = "Word,Form,Rank"
        frequency_stats["algorithm"] = (
            "jiten-kanji-rank-weight-v1+bees-kanjidic-unique-alignment-v1"
        )
        frequency_stats["metric"] = (
            "normalized sum of inverse-square-root Jiten Global ranks; "
            "ranks above 100000 receive an additional quadratic tail penalty"
        )
        try:
            if frequency_stats.get("alignedRows", 0) <= 0 or not frequency_scores:
                raise MalformedPayload(
                    "Jiten frequency CSV produced no aligned reading scores"
                )
            if enforce_frequency_quality:
                validate_jiten_frequency_coverage(frequency_stats)
        except MalformedPayload:
            _quarantine_jiten_frequency_snapshot(frequency_source)
            raise
    records = []
    for char in characters:
        payload = payloads.get(char)
        if payload is None:
            continue
        if not isinstance(payload, dict) or payload.get("character") != char:
            actual = payload.get("character") if isinstance(payload, dict) else None
            raise MalformedPayload(f"requested {char} but received payload for {actual!r}")
        try:
            record = normalize_record(payload)
        except MalformedPayload:
            continue  # skip characters whose payload cannot be trusted
        if frequency_cache_dir is not None:
            record["reading_frequency_scores"] = frequency_scores.get(char, [])
        if record["frequency_rank"] is not None and record["frequency_rank"] <= 1000:
            example_count = sum(len(group["words"]) for group in record["examples"])
            if not record["keyword"] or not (record["on"] or record["kun"]) or example_count < 1:
                raise MalformedPayload(f"Top-1000 quality floor failed for {char}")
        records.append(record)

    jiten_count = len(records)
    if kanjidic2_cache_dir:
        xml_text = fetch_kanjidic2_source(
            kanjidic2_cache_dir,
            date,
            kanjidic2_fetcher,
            consumed_sources=consumed_sources,
        )
        kd2_index = parse_kanjidic2(xml_text)
        records = merge_kanjidic2(records, kd2_index)
    kanjidic2_count = len(records) - jiten_count

    ranks = {r["character"]: r["frequency_rank"] for r in records}
    enrichment = {"strokes": {}, "families": {}, "families_by_char": {}, "assets": {}}
    if kanjivg_cache_dir:
        present = [r["character"] for r in records]
        svgs = fetch_kanjivg_all(
            present,
            kanjivg_cache_dir,
            date,
            kanjivg_fetcher,
            consumed_sources=consumed_sources,
        )
        enrichment = assemble_enrichment(svgs, ranks)
    kanjivg_asset_count = len(enrichment["assets"])

    # Package only the rank-derived chart referenced by structured content.
    # Entry-count reading distributions are never a production fallback.
    frequency_asset_count = 0
    for r in records:
        frequency_png = build_reading_frequency_png(r)
        if frequency_png is not None:
            enrichment["assets"][reading_frequency_asset_name(r["character"])] = frequency_png
            frequency_asset_count += 1
    if frequency_stats is not None:
        frequency_stats["chartAssets"] = frequency_asset_count

    banks = build_banks(records, aliases or {}, enrichment=enrichment)
    source_counts = {"jiten": jiten_count, "kanjidic2": kanjidic2_count}
    enrichment_counts = {
        "strokes": len(enrichment["strokes"]),
        "families": len(enrichment["families"]),
        "assets": kanjivg_asset_count,
    }
    chash = content_hash(
        banks,
        enrichment.get("assets"),
        source_counts=source_counts,
        enrichment_counts=enrichment_counts,
        sitemap_size=len(characters),
        frequency_stats=frequency_stats,
    )
    return {
        "records": records,
        "banks": banks,
        "enrichment": enrichment,
        "content_hash": chash,
        "source_counts": source_counts,
        "enrichment_counts": enrichment_counts,
        "frequency_stats": frequency_stats,
        "frequency_source": frequency_source,
        "consumed_sources": consumed_sources,
        "zip_bytes": build_zip(banks, date_to_revision(date), enrichment.get("assets")),
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
        except (ValueError, OSError, UnicodeDecodeError, MalformedPayload):
            prev_rev = None
    prev_hash = None
    hpath = idx.with_name("content.sha256")
    if hpath.exists():
        try:
            prev_hash = hpath.read_text(encoding="utf-8").strip() or None
        except OSError:
            prev_hash = None
    return prev_rev, prev_hash


def _code_revision():
    """Return the full git revision of the generator, or 'unknown'.

    Deterministic within a single checkout; recorded in the manifest so a
    published release is traceable back to the exact code that produced it.
    """
    import pathlib as _pl
    import subprocess as _sp

    root = _pl.Path(__file__).resolve().parent.parent
    try:
        out = _sp.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            stderr=_sp.DEVNULL,
        )
        return out.decode("utf-8").strip() or "unknown"
    except (OSError, _sp.CalledProcessError):
        return "unknown"


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
    parser.add_argument("--kanjivg-cache", default="kanjivg-cache",
                        help="dated cache dir for KanjiVG stroke SVGs")
    parser.add_argument("--out", default="build")
    parser.add_argument("--dist", default="dist")
    parser.add_argument("--date", default=None, help="UTC date YYYY-MM-DD (default: today)")
    parser.add_argument(
        "--revision",
        default=None,
        help="fresh preflight-selected YYYY.MM.DD[.N] revision for changed content",
    )
    parser.add_argument("--limit", type=int, default=None, help="limit characters (debug)")
    parser.add_argument("--offline", action="store_true", help="use cache only; no network")
    parser.add_argument("--no-kanjivg", action="store_true",
                        help="skip KanjiVG acquisition/enrichment (data-only build)")
    parser.add_argument("--kanjidic2-cache", default="kanjidic2-cache",
                        help="dated cache dir for the static KANJIDIC2 XML source")
    parser.add_argument("--frequency-cache", default="jiten-frequency-cache",
                        help="dated cache dir for Jiten's bulk Global frequency CSV")
    parser.add_argument("--no-kanjidic2", action="store_true",
                        help="skip KANJIDIC2 fallback (Jiten-only build)")
    args = parser.parse_args(argv)

    date = args.date or datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    out_dir = _pl.Path(args.out)
    dist_dir = _pl.Path(args.dist)
    out_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)

    print(f"[build] date={date}")
    consumed_sources = {}
    characters = fetch_sitemap_cached(
        args.cache,
        date,
        offline=args.offline,
        consumed_sources=consumed_sources,
    )
    if args.limit:
        characters = characters[: args.limit]
    print(f"[build] {len(characters)} characters from sitemap")

    fetcher = _offline_fetcher(args.cache, date) if args.offline else fetch_kanji
    kanjivg_cache = None if args.no_kanjivg else args.kanjivg_cache
    kvg_fetcher = (
        _offline_kanjivg_fetcher(args.kanjivg_cache, date) if args.offline else fetch_kanjivg
    )
    kanjidic2_cache = None if args.no_kanjidic2 else args.kanjidic2_cache
    kd2_fetcher = (
        _offline_kanjidic2_fetcher(args.kanjidic2_cache, date) if args.offline else fetch_kanjidic2
    )
    frequency_fetcher = (
        _offline_jiten_frequency_fetcher(args.frequency_cache, date)
        if args.offline else fetch_jiten_global_frequency_csv
    )
    result = run_build(
        characters, args.cache, date, DEFAULT_ALIASES, fetcher,
        kanjivg_cache_dir=kanjivg_cache, kanjivg_fetcher=kvg_fetcher,
        kanjidic2_cache_dir=kanjidic2_cache, kanjidic2_fetcher=kd2_fetcher,
        frequency_cache_dir=args.frequency_cache,
        frequency_fetcher=frequency_fetcher,
        enforce_frequency_quality=args.limit is None,
        frequency_offline=args.offline,
        consumed_sources=consumed_sources,
    )
    enr = result["enrichment"]
    if args.limit is None and not args.no_kanjivg:
        validate_kanjivg_coverage(
            len(enr["strokes"]), cache_dir=args.kanjivg_cache, date=date
        )
    sc = result.get("source_counts", {"jiten": len(result["records"]), "kanjidic2": 0})
    print(
        f"[build] {len(result['records'])} clean records "
        f"(Jiten {sc['jiten']} + KANJIDIC2 fallback {sc['kanjidic2']}); "
        f"{len(enr['strokes'])} stroke sets; {len(enr['families'])} phonetic families; "
        f"hash={result['content_hash'][:12]}"
    )
    fs = result.get("frequency_stats") or {}
    if fs:
        print(
            "[frequency] "
            f"{fs.get('alignedRows', 0)}/{fs.get('relevantRows', 0)} rows aligned; "
            f"{float(fs.get('rankWeightCoverage', 0.0)):.1%} rank-weight coverage"
        )

    prev_rev, prev_hash = _load_previous(dist_dir / "index.json")
    revision = decide_revision(result["content_hash"], prev_hash, date, prev_rev)
    if revision is None:
        print("[build] content unchanged; nothing to publish")
        return 0
    if args.revision is not None:
        base = date_to_revision(date)
        if not re.fullmatch(re.escape(base) + r"(?:\.[0-9]+)?", args.revision):
            raise SystemExit("--revision must be a fresh revision for the resolved date")
        revision = args.revision

    frequency_source = result.get("frequency_source")
    if frequency_source is None:
        raise SystemExit("build did not carry its scored Jiten frequency snapshot")
    source_snapshot_bytes = None
    source_lock = None
    source_snapshot_meta = None
    if args.limit is None and not args.no_kanjivg and not args.no_kanjidic2:
        source_snapshot_bytes, source_lock = build_source_snapshot(
            date=date,
            consumed_sources=result["consumed_sources"],
        )
        source_lock_bytes = dump_json(source_lock).encode("utf-8")
        source_snapshot_meta = {
            "sha256": hashlib.sha256(source_snapshot_bytes).hexdigest(),
            "byteCount": len(source_snapshot_bytes),
            "lockSha256": hashlib.sha256(source_lock_bytes).hexdigest(),
            "fileCount": len(source_lock["files"]),
        }

    manifest = build_manifest(
        revision=revision,
        content_hash=result["content_hash"],
        date=date,
        source_counts=sc,
        enrichment_counts=result["enrichment_counts"],
        sitemap_size=len(characters),
        code_revision=_code_revision(),
        frequency_stats=result.get("frequency_stats") or None,
        source_snapshot=source_snapshot_meta,
    )

    zip_bytes = build_zip(result["banks"], revision, enr.get("assets"), manifest=manifest)
    zip_path = out_dir / ZIP_NAME
    zip_path.write_bytes(zip_bytes)

    # Emit the manifest as a standalone release asset too, byte-identical to the
    # bundled MANIFEST.json member, so its SHA256SUMS line verifies either copy.
    manifest_text = dump_json(manifest)
    (out_dir / "MANIFEST.json").write_text(manifest_text, encoding="utf-8")
    manifest_digest = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()

    frequency_source_bytes = frequency_source.raw
    frequency_digest = frequency_source.sha256
    if frequency_digest != manifest["sources"]["jitenGlobalFrequency"]["sha256"]:
        raise SystemExit("Jiten frequency source digest differs from scored snapshot")
    (out_dir / JITEN_FREQUENCY_ASSET_NAME).write_bytes(frequency_source_bytes)

    extra_checksum_lines = []
    if source_snapshot_bytes is not None and source_lock is not None:
        source_lock_bytes = dump_json(source_lock).encode("utf-8")
        (out_dir / SOURCE_SNAPSHOT_NAME).write_bytes(source_snapshot_bytes)
        (out_dir / SOURCE_LOCK_NAME).write_bytes(source_lock_bytes)
        extra_checksum_lines.extend((
            f"{hashlib.sha256(source_snapshot_bytes).hexdigest()}  {SOURCE_SNAPSHOT_NAME}",
            f"{hashlib.sha256(source_lock_bytes).hexdigest()}  {SOURCE_LOCK_NAME}",
        ))

    digest = hashlib.sha256(zip_bytes).hexdigest()
    checksum_lines = [
        f"{digest}  {ZIP_NAME}",
        f"{manifest_digest}  MANIFEST.json",
        f"{frequency_digest}  {JITEN_FREQUENCY_ASSET_NAME}",
        *extra_checksum_lines,
    ]
    (out_dir / "SHA256SUMS").write_text(
        "\n".join(checksum_lines) + "\n", encoding="utf-8"
    )

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
        return json.loads(_read_file_bytes_bounded(
            path, MAX_HTTP_RESPONSE_BYTES, "cached Jiten kanji payload"
        ).decode("utf-8"))

    return fetcher


def _offline_kanjivg_fetcher(cache_dir, date):
    """Return a KanjiVG fetcher that only reads the dated cache (else NotFound)."""
    import pathlib as _pl

    day = _pl.Path(cache_dir) / date

    def fetcher(char):
        path = day / kanjivg_cache_filename(char)
        if not path.exists():
            raise NotFound(char)
        return _read_file_bytes_bounded(
            path, MAX_HTTP_RESPONSE_BYTES, "cached KanjiVG SVG"
        ).decode("utf-8")

    return fetcher


def _offline_kanjidic2_fetcher(cache_dir, date):
    """Return a KANJIDIC2 source fetcher that only reads the dated cache."""
    import pathlib as _pl

    day = _pl.Path(cache_dir) / date

    def fetcher():
        path = day / "kanjidic2.xml"
        if not path.exists():
            raise FileNotFoundError(f"KANJIDIC2 cache missing: {path}")
        return _read_file_bytes_bounded(
            path, MAX_KANJIDIC2_XML_BYTES, "cached KANJIDIC2 XML"
        ).decode("utf-8")

    return fetcher



def _offline_jiten_frequency_fetcher(cache_dir, date):
    """Return a fetcher that fails closed outside the dated bulk CSV cache."""
    import pathlib as _pl

    path = _pl.Path(cache_dir) / date / "jiten_freq_global.csv"

    def fetcher():
        if not path.exists():
            raise FileNotFoundError(f"Jiten frequency CSV cache missing: {path}")
        return _read_file_bytes_bounded(
            path, MAX_JITEN_FREQUENCY_BYTES, "cached Jiten frequency CSV"
        )

    return fetcher


# Compatibility aliases: forms Jiten does not serve, mapped to canonical forms.
DEFAULT_ALIASES = {"髙": "高"}


if __name__ == "__main__":
    raise SystemExit(main())
