import importlib.util
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github/workflows/release.yml"


def _workflow():
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _preflight_module():
    path = ROOT / "scripts/release_preflight.py"
    spec = importlib.util.spec_from_file_location("release_preflight", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_action_is_pinned_to_a_full_commit_sha():
    uses = re.findall(r"^\s*- uses: ([^\s]+)", _workflow(), re.MULTILINE)
    assert uses
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", action) for action in uses)


def test_build_environment_and_dependencies_are_exactly_locked():
    workflow = _workflow()
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    assert "python:3.11.15-bookworm@sha256:" in workflow
    assert re.search(r"python:3\.11\.15-bookworm@sha256:[0-9a-f]{64}", workflow)
    assert 'version: "0.11.28"' in workflow
    assert 'node-version: "22.23.1"' in workflow
    assert "uv sync --frozen --all-groups" in workflow
    assert "PYTHONPATH: src" in workflow
    assert "npm ci" in workflow
    assert "npm audit --omit=dev" in workflow
    assert package["dependencies"]["adm-zip"] == "0.6.0"
    assert package_lock["packages"]["node_modules/adm-zip"]["version"] == "0.6.0"
    assert '"jsonschema==4.26.0"' in pyproject
    assert '"Pillow==12.3.0"' in pyproject
    assert '"pytest==9.1.1"' in pyproject
    assert 'name = "pillow"' in lock
    assert "hash = \"sha256:" in lock


def test_build_is_read_only_and_write_credentials_are_isolated():
    workflow = _workflow()
    assert workflow.startswith("name: release")
    assert re.search(r"^permissions:\n  contents: read$", workflow, re.MULTILINE)
    build = workflow[workflow.index("  build:"):workflow.index("  publish:")]
    publish = workflow[workflow.index("  publish:"):workflow.index("  recover_updater:")]
    recovery = workflow[workflow.index("  recover_updater:"):workflow.index("  verify_no_change:")]
    assert "contents: read" in build
    assert "persist-credentials: false" in build
    assert "contents: write" not in build
    assert "GH_TOKEN:" not in build
    assert "GITHUB_TOKEN: ${{ github.token }}" in build  # scoped to preflight only
    assert "contents: write" in publish
    assert "persist-credentials: false" in publish
    assert ". candidate/METADATA" not in publish
    assert "release_metadata.py verify candidate/METADATA.json" in publish
    assert "contents: write" in recovery
    assert "Install the locked" not in publish
    assert "Install the locked" not in recovery


def test_manual_runs_are_rejected_outside_main():
    workflow = _workflow()
    assert "github.event_name == 'workflow_dispatch' && github.ref != 'refs/heads/main'" in workflow
    assert "workflow_dispatch releases may run only from refs/heads/main" in workflow
    assert "if: github.ref == 'refs/heads/main'" in workflow


def test_one_resolved_date_drives_every_cache_and_build_invocation():
    workflow = _workflow()
    assert workflow.count("date -u +%F") == 1
    assert "daily-cache-v3-${{ needs.guard.outputs.date }}" in workflow
    invocations = [
        line for line in workflow.splitlines()
        if ".venv/bin/python -m bees_kanji" in line
    ]
    assert len(invocations) == 2
    # Once in read-only remote preflight, then in online and offline builds.
    assert workflow.count('--date "$BUILD_DATE"') == 6
    assert workflow.count('--revision "$CANDIDATE_REVISION"') == 4


def test_release_retains_every_exact_source_and_checksums_six_assets():
    workflow = _workflow()
    expected = (
        "MANIFEST.json SHA256SUMS SOURCE-LOCK.json "
        "bees-ultimate-kanji-dictionary.zip jiten-global-frequency.csv "
        "source-snapshot.zip"
    )
    assert expected in workflow
    for name in (
        "bees-ultimate-kanji-dictionary.zip",
        "MANIFEST.json",
        "SHA256SUMS",
        "jiten-global-frequency.csv",
        "source-snapshot.zip",
        "SOURCE-LOCK.json",
    ):
        assert name in workflow
    assert 'lock["files"]' in workflow
    assert "scripts/verify_source_snapshot.py" in workflow


def test_offline_replay_comes_only_from_released_snapshot_then_binds_both_outputs():
    workflow = _workflow()
    extract = workflow.index("scripts/verify_source_snapshot.py")
    offline = workflow.index("--offline", extract)
    commit = workflow.index("Prepare deterministic updater commit")
    first_bind = workflow.index('bind("build"', commit)
    replay_bind = workflow.index('bind("replay-build"', first_bind)
    final_compare = workflow.index('cmp "build/$asset" "replay-build/$asset"', replay_bind)
    assert extract < offline < commit < first_bind < replay_bind < final_compare
    assert "replay-source/cache" in workflow
    assert "replay-source/kanjivg-cache" in workflow
    assert "replay-source/kanjidic2-cache" in workflow
    assert "replay-source/jiten-frequency-cache" in workflow


def test_production_shaped_final_zip_is_validated_after_rebinding():
    workflow = _workflow()
    bind = workflow.index("Independently bind both candidates")
    validate = workflow.index("Validate exact final publishable bytes")
    upload = workflow.index("actions/upload-artifact@")
    assert bind < validate < upload
    assert "node scripts/validate_yomitan.mjs build/bees-ultimate-kanji-dictionary.zip" in workflow
    assert "sha256sum -c SHA256SUMS" in workflow
    assert 'package.read("MANIFEST.json") == manifest_bytes' in workflow


def test_remote_coordinates_are_never_deleted_moved_or_reused():
    workflow = _workflow()
    assert "gh release delete" not in workflow
    assert "--cleanup-tag" not in workflow
    assert "git push --force" not in workflow
    assert "git tag -f" not in workflow
    assert 'test "$tag_status" -eq 2' in workflow
    assert '"refs/tags/$release_tag:refs/tags/$release_tag"' in workflow
    assert "--assert-revision \"$REVISION\"" in workflow
    assert "--verify-tag" in workflow


def test_revision_selection_is_fresh_and_monotonic_over_all_exposed_tags():
    module = _preflight_module()
    refs = [
        "refs/tags/v2026.08.19",
        "refs/tags/v2026.08.19.2",
        "refs/tags/v2026.08.19.7^{}",
        "refs/tags/v2026.08.18.99",
    ]
    assert module.next_revision("2026-08-19", refs) == "2026.08.19.8"
    assert module.next_revision("2026-08-20", refs) == "2026.08.20"
    with pytest.raises(RuntimeError, match="later than the acquisition date"):
        module.next_revision("2026-08-19", refs + ["refs/tags/v2026.08.20"])
    module.assert_fresh_monotonic_revision("2026.08.19.8", refs)
    with pytest.raises(RuntimeError, match="not globally monotonic"):
        module.assert_fresh_monotonic_revision("2026.08.19.1", refs)
    assert module.has_newer_exposed_coordinate("2026.08.19.2", refs)
    assert not module.has_newer_exposed_coordinate("2026.08.19.7", refs)


def test_publication_is_draft_verified_immutable_and_public_bytes_are_checked_before_main():
    workflow = _workflow()
    tag_push = workflow.index("Push the fresh tag without persisting credentials")
    draft = workflow.index("gh release create", tag_push)
    draft_verify = workflow.index("verify-draft", draft)
    publish = workflow.index('gh release edit "$release_tag" --draft=false --latest', draft_verify)
    immutable = workflow.index('test "$immutable" = true', publish)
    public = workflow.index("verify-public", immutable)
    latest = workflow.index("releases/latest/download/$asset", public)
    push_main = workflow.index("Expose updater only after immutable public verification", latest)
    assert tag_push < draft < draft_verify < publish < immutable < public < latest < push_main
    assert "immutable-releases" not in workflow
    assert ".digest" in workflow
    assert "cmp \"candidate/build/$asset\" \"verify-public/$asset\"" in workflow
    assert "cmp \"candidate/build/$asset\" \"verify-latest/$asset\"" in workflow


def test_post_publication_failure_has_exact_safe_recovery_or_fresh_fix_forward():
    workflow = _workflow()
    script = (ROOT / "scripts/release_preflight.py").read_text(encoding="utf-8")
    assert "_safe_recovery" in script
    assert "parents[1] != current_sha" in script
    assert '{"dist/index.json", "dist/content.sha256"}' in script
    assert "release/updater divergence requires a fresh monotonic revision" in script
    recovery = workflow[workflow.index("  recover_updater:"):workflow.index("  verify_no_change:")]
    assert "sha256sum -c SHA256SUMS" in recovery
    assert "releases/latest/download/$asset" in recovery
    assert ".digest" in recovery
    assert '"$recovery_commit:refs/heads/main"' in recovery


def test_no_change_path_reverifies_release_tag_manifest_checksums_and_latest_bytes():
    workflow = _workflow()
    no_change = workflow[workflow.index("  verify_no_change:"):]
    assert "contents: read" in no_change
    assert "persist-credentials: false" in no_change
    assert "immutable" in no_change
    assert "sha256sum -c SHA256SUMS" in no_change
    assert "releases/latest/download/$asset" in no_change
    assert ".digest" in no_change
    assert 'manifest["codeRevision"] == commit' in no_change
    assert 'manifest["contentHash"] == pathlib.Path("dist/content.sha256")' in no_change
