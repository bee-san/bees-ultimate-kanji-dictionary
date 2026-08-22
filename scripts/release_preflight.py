#!/usr/bin/env python3
"""Read-only GitHub release-state preflight for the daily build.

It never deletes, mutates, or reuses a remote coordinate. It selects a fresh
monotonic revision from every exposed tag and identifies the one safe recovery
case: an immutable complete latest release whose updater commit is a direct
child of the current default-branch head.
"""

import argparse
import json
import os
import pathlib
import re
import subprocess
import urllib.error
import urllib.request

MAX_API_BYTES = 8 * 1024 * 1024
EXPECTED_ASSETS = {
    "bees-ultimate-kanji-dictionary.zip",
    "MANIFEST.json",
    "SHA256SUMS",
    "jiten-global-frequency.csv",
    "source-snapshot.zip",
    "SOURCE-LOCK.json",
}
REVISION_RE = re.compile(r"^(\d{4}\.\d{2}\.\d{2})(?:\.(\d+))?$")


def _run(*args):
    return subprocess.check_output(args, text=True).strip()


def _api_bytes(repo, path, accept="application/vnd.github+json"):
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/{path}",
        headers={
            "Accept": accept,
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "User-Agent": "bees-release-preflight",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        declared = response.headers.get("Content-Length")
        if declared is not None and int(declared) > MAX_API_BYTES:
            raise RuntimeError("GitHub API response exceeds preflight limit")
        raw = response.read(MAX_API_BYTES + 1)
    if len(raw) > MAX_API_BYTES:
        raise RuntimeError("GitHub API response exceeds preflight limit")
    return raw


def _api_json(repo, path):
    return json.loads(_api_bytes(repo, path))


def _latest_release(repo):
    try:
        return _api_json(repo, "releases/latest")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def revision_key(revision):
    match = REVISION_RE.fullmatch(revision or "")
    if not match:
        raise ValueError(f"invalid release revision: {revision!r}")
    year, month, day = (int(part) for part in match.group(1).split("."))
    return year, month, day, int(match.group(2) or 0)


def next_revision(date, remote_refs):
    base = date.replace("-", ".")
    base_key = revision_key(base)
    revisions = _exposed_revision_keys(remote_refs)
    if any(key[:3] > base_key[:3] for key in revisions):
        raise RuntimeError("an exposed release coordinate is later than the acquisition date")
    suffixes = [key[3] for key in revisions if key[:3] == base_key[:3]]
    if not suffixes:
        return base
    return f"{base}.{max(suffixes) + 1}"


def _exposed_revision_keys(remote_refs):
    keys = set()
    for ref in remote_refs:
        name = ref.removeprefix("refs/tags/v").removesuffix("^{}")
        if REVISION_RE.fullmatch(name):
            keys.add(revision_key(name))
    return keys


def assert_fresh_monotonic_revision(revision, remote_refs):
    candidate = revision_key(revision)
    exposed = _exposed_revision_keys(remote_refs)
    if exposed and candidate <= max(exposed):
        raise RuntimeError("candidate release coordinate is not globally monotonic")


def has_newer_exposed_coordinate(revision, remote_refs):
    exposed = _exposed_revision_keys(remote_refs)
    return bool(exposed and max(exposed) > revision_key(revision))


def _remote_refs():
    output = _run("git", "ls-remote", "--tags", "origin")
    refs = []
    for line in output.splitlines():
        fields = line.split("\t", 1)
        if len(fields) == 2:
            refs.append(fields[1])
    return refs


def _remote_main_sha():
    output = _run("git", "ls-remote", "origin", "refs/heads/main")
    fields = output.split("\t", 1)
    if len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{40}", fields[0]):
        raise RuntimeError("origin/main cannot be resolved exactly")
    return fields[0]


def _tag_commit(tag):
    _run("git", "fetch", "--no-tags", "origin", f"refs/tags/{tag}")
    return _run("git", "rev-parse", "FETCH_HEAD^{}")


def _release_manifest(repo, release):
    matches = [asset for asset in release.get("assets", []) if asset.get("name") == "MANIFEST.json"]
    if len(matches) != 1:
        raise RuntimeError("latest release must have exactly one MANIFEST.json")
    return json.loads(_api_bytes(
        repo,
        f"releases/assets/{matches[0]['id']}",
        accept="application/octet-stream",
    ))


def _complete_release(repo, release):
    if release is None:
        return None
    if release.get("draft") or release.get("prerelease") or release.get("immutable") is not True:
        return None
    if {asset.get("name") for asset in release.get("assets", [])} != EXPECTED_ASSETS:
        return None
    tag = release.get("tag_name", "")
    if not tag.startswith("v"):
        return None
    revision = tag[1:]
    revision_key(revision)
    commit = _tag_commit(tag)
    manifest = _release_manifest(repo, release)
    if manifest.get("revision") != revision or manifest.get("codeRevision") != commit:
        return None
    return {"tag": tag, "revision": revision, "commit": commit, "manifest": manifest}


def _local_revision(dist):
    path = pathlib.Path(dist) / "index.json"
    if not path.exists():
        return None
    if path.stat().st_size > 1024 * 1024:
        raise RuntimeError("dist/index.json is oversized")
    value = json.loads(path.read_text(encoding="utf-8")).get("revision")
    revision_key(value)
    return value


def _safe_recovery(release_state, current_sha):
    commit = release_state["commit"]
    parents = _run("git", "rev-list", "--parents", "-n", "1", commit).split()
    if len(parents) != 2 or parents[1] != current_sha:
        return False
    changed = set(
        _run("git", "diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines()
    )
    return changed == {"dist/index.json", "dist/content.sha256"}


def write_outputs(path, values):
    with pathlib.Path(path).open("a", encoding="utf-8") as handle:
        handle.writelines(f"{key}={value}\n" for key, value in values.items())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--dist")
    parser.add_argument("--output")
    parser.add_argument("--assert-revision")
    args = parser.parse_args()

    refs = _remote_refs()
    if _remote_main_sha() != args.sha:
        raise RuntimeError("origin/main advanced after the workflow started")
    if args.assert_revision is not None:
        if revision_key(args.assert_revision)[:3] != revision_key(
            args.date.replace("-", ".")
        )[:3]:
            raise RuntimeError("candidate release coordinate does not match the resolved date")
        assert_fresh_monotonic_revision(args.assert_revision, refs)
        print(json.dumps({"revision": args.assert_revision, "fresh": True}))
        return
    if args.dist is None or args.output is None:
        parser.error("--dist and --output are required for candidate selection")
    candidate = next_revision(args.date, refs)
    local_revision = _local_revision(args.dist)
    raw_latest = _latest_release(args.repo)
    latest = _complete_release(args.repo, raw_latest)
    force = False
    recovery_tag = ""
    reason = "release state and updater agree"

    if raw_latest is None:
        if local_revision is not None:
            force = True
            reason = "updater exists but no complete latest release exists"
    elif latest is None:
        force = True
        reason = "latest release is mutable, incomplete, or provenance-invalid"
    elif local_revision is None:
        force = True
        reason = "latest release exists but updater metadata is absent"
    else:
        latest_key = revision_key(latest["revision"])
        local_key = revision_key(local_revision)
        if latest_key == local_key:
            if has_newer_exposed_coordinate(latest["revision"], refs):
                force = True
                reason = "unfinished exposed coordinate requires a fresh monotonic revision"
        elif latest_key > local_key and _safe_recovery(latest, args.sha):
            recovery_tag = latest["tag"]
            reason = "immutable release awaits its exact updater fast-forward"
        else:
            force = True
            reason = "release/updater divergence requires a fresh monotonic revision"

    write_outputs(
        args.output,
        {
            "candidate_revision": candidate,
            "force_fresh": str(force).lower(),
            "recovery_tag": recovery_tag,
            "reason": reason,
        },
    )
    print(json.dumps({"candidate": candidate, "force": force, "recoveryTag": recovery_tag, "reason": reason}))


if __name__ == "__main__":
    main()
