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
