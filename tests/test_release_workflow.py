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
