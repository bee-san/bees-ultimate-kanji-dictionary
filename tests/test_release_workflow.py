import pathlib
import re


ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_write_enabled_workflow_pins_actions_to_commit_shas():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    uses = re.findall(r"^\s*- uses: ([^\s]+)", workflow, re.MULTILINE)
    assert uses
    for action in uses:
        assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", action), action


def test_python_build_dependencies_are_exactly_pinned():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"jsonschema==4.26.0"' in pyproject
    assert '"pytest==9.1.1"' in pyproject


def test_releases_use_fresh_versioned_tags_and_require_immutability():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert 'release_tag="v${{ steps.check.outputs.revision }}"' in workflow
    assert 'gh release delete "latest"' not in workflow
    assert 'git ls-remote --exit-code --tags origin "refs/tags/$release_tag"' in workflow
    assert 'test "$tag_status" -eq 2' in workflow
    assert 'git push origin "refs/tags/$release_tag:refs/tags/$release_tag"' in workflow
    assert '--verify-tag' in workflow
    assert '--draft' in workflow
    assert 'test "$tag_commit" = "$expected_commit"' in workflow
    assert 'gh release edit "$release_tag" --draft=false --latest' in workflow
    assert 'immutable="$(gh api' in workflow
    assert 'test "$immutable" = "true"' in workflow
    assert 'test "$latest_tag" = "$release_tag"' in workflow
    assert 'test "$asset_names" = "MANIFEST.json SHA256SUMS bees-ultimate-kanji-dictionary.zip"' in workflow
    assert 'cmp "build/$asset" "verify-published/$asset"' in workflow


def test_daily_workflow_persists_and_reuses_same_day_acquisition_cache():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "actions/cache/restore@" in workflow
    assert "actions/cache/save@" in workflow
    assert "cache/" in workflow
    assert "kanjivg-cache/" in workflow
    assert "kanjidic2-cache/" in workflow
    assert "daily-cache-v1-${{ steps.date.outputs.date }}" in workflow


def test_verified_draft_and_updater_commit_precede_public_immutable_release():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    verify_draft = workflow.index("(cd verify && sha256sum -c SHA256SUMS)")
    publish = workflow.index('gh release edit "$release_tag" --draft=false --latest')
    push_main = workflow.index('git push origin "HEAD:main"')
    assert verify_draft < push_main < publish
    assert 'git push origin "refs/tags/$release_tag:refs/tags/$release_tag"' in workflow[:publish]
    pre_push = workflow[:push_main]
    assert 'test "$asset_names" = "MANIFEST.json SHA256SUMS bees-ultimate-kanji-dictionary.zip"' in pre_push
    assert 'cmp "build/$asset" "verify/$asset"' in pre_push


def test_release_assets_are_rebound_and_verified_against_final_tag_commit():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    prepare = workflow.index("Prepare updater commit")
    bind = workflow.index("Bind artifacts to final commit")
    publish = workflow.index("Publish release")
    assert prepare < bind < publish
    assert "bind_release_artifacts_to_code_revision" in workflow[bind:publish]
    assert "git rev-parse HEAD" in workflow[bind:publish]
    assert "manifest['codeRevision'] == expected" in workflow


def test_release_retry_rebuilds_trusted_bytes_under_a_fresh_revision():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert 'test "$remote_tag_commit" = "$expected_commit"' in workflow
    assert 'gh release view "$release_tag"' in workflow
    assert 'gh release create "$release_tag"' in workflow
    assert "release_immutable" in workflow
    assert 'rm -f dist/content.sha256' in workflow
    assert 'gh release download "$release_tag" --dir build' not in workflow
    assert "Never trust bytes or checksums from a mutable/draft release" in workflow
    assert 'gh release delete "$release_tag" --yes --cleanup-tag' in workflow
    assert 'if [ -z "$release_draft" ]; then' in workflow
    assert 'steps.check.outputs.recovery }}" != "true"' in workflow
    assert 'gh release delete "$release_tag" --yes --cleanup-tag' in workflow


def test_public_mutable_incomplete_release_is_recreated_as_a_draft():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert 'elif [ "$release_immutable" != "true" ]; then' in workflow
    assert 'gh release delete "$release_tag" --yes' in workflow
    assert "create_draft_release" in workflow
    assert "Cannot replace incomplete immutable release" in workflow


def test_no_build_requires_a_complete_release_bound_to_its_tag():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert 'test "$release_draft" = "false"' in workflow
    assert 'test "$release_immutable" = "true"' in workflow
    assert 'test "$latest_tag" = "$release_tag"' in workflow
    assert 'gh release download "$release_tag" --pattern MANIFEST.json' in workflow
    assert 'test "$remote_tag_commit" = "$manifest_code_revision"' in workflow
    assert 'test "$manifest_revision" = "$revision"' in workflow
    assert "manifest['revision'] == expected_revision" in workflow
    assert "index['revision'] == expected_revision" in workflow
