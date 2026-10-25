"""Pins for .github/scripts/check_doc_claims.py.

The rule being pinned is a prose rule in CONTRIBUTING.md that had no checker:
every Added/Fixed/Changed bullet in the unreleased lane must carry an
executable evidence pointer. A prose rule with no enforcement drifts, and it
drifted -- three `[Unreleased]` entries cited counts that their suites no
longer collected, and one proved its claim with a `git grep` whose only match
was that grep command printed inside the changelog.

Every counterfactual pin here asserts a NON-EMPTY red result. Each is paired
with the green control that shows the same rule leaves the real repo alone, so
a rule that fires on everything is as visible as one that fires on nothing.

The gate's admitted termination layer, stated rather than stacked: a CHANGELOG
with no `[Unreleased]` heading governs nothing and returns clean, because the
checker cannot tell "nothing is claimed yet" from "the heading was renamed".
`test_an_absent_unreleased_heading_governs_nothing` pins that decision.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / ".github" / "scripts" / "check_doc_claims.py"

_HEADER = "# Changelog\n\nAll notable changes.\n\n## [Unreleased]\n\n### Added\n\n"
_FROZEN = "\n## [0.6.0] - 2026-10-05\n\n- **A frozen entry** — cites nothing at all.\n"


def _load():
    spec = importlib.util.spec_from_file_location("check_doc_claims", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


M = _load()


def _repo(tmp_path: Path, added: str, *, section: str = "Added", agents: str = "") -> Path:
    """A miniature repo: one changelog bullet plus the files D2 reads."""
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n### " + section + "\n\n" + added + _FROZEN,
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text(agents or "# Agent notes\n", encoding="utf-8")
    (tmp_path / "CONTRIBUTING.md").write_text("# Contributing\n", encoding="utf-8")
    return tmp_path


def _add_test_file(root: Path, name: str, funcs: list[str]) -> None:
    tests = root / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"def {f}():\n    assert True" for f in funcs)
    (tests / name).write_text(f"{body}\n", encoding="utf-8")


def test_a_bullet_without_evidence_fails_the_gate(tmp_path):
    """Counterfactual: a claim with no handle a reader can pull is red."""
    root = _repo(tmp_path, "- **A gate landed** — it checks a thing.\n")
    findings = M.check_changelog(root)
    assert any("no allowed evidence form" in f for f in findings), findings


def test_a_dead_pytest_node_fails_the_gate(tmp_path):
    """Counterfactual: a pointer must land on a collectible test."""
    root = _repo(
        tmp_path,
        "- **A gate landed** — Evidence: `tests/test_real.py::test_ghost`.\n",
    )
    _add_test_file(root, "test_real.py", ["test_real"])
    findings = M.check_changelog(root)
    assert any("test_ghost" in f for f in findings), findings


def test_a_bare_path_line_in_an_evidence_clause_fails_the_gate(tmp_path):
    """Counterfactual: the form that drifts on every edit stays red."""
    root = _repo(
        tmp_path,
        "- **A gate landed** — Evidence: `src/maya_mcp_server/server.py:345-360`.\n",
    )
    src = root / "src" / "maya_mcp_server"
    src.mkdir(parents=True)
    (src / "server.py").write_text("x = 1\n", encoding="utf-8")
    findings = M.check_changelog(root)
    assert any("bare path:line" in f for f in findings), findings


def test_a_dead_path_in_the_agents_file_fails_the_gate(tmp_path):
    """Counterfactual: a rule file may not send a reader to a gone path."""
    root = _repo(tmp_path, "- **A gate landed** — `tests/test_real.py::test_real`.\n")
    _add_test_file(root, "test_real.py", ["test_real"])
    root_agents = root / "AGENTS.md"
    root_agents.write_text("# Notes\n\nRead docs/guide/gone.md before editing.\n", encoding="utf-8")
    findings = M.check_steering(root)
    assert any("docs/guide/gone.md" in f for f in findings), findings


def test_a_recovery_pointer_is_not_a_dead_path(tmp_path):
    """Green control: annotating a retired file is legal, citing it is not."""
    root = _repo(tmp_path, "- **A gate landed** — `tests/test_real.py::test_real`.\n")
    _add_test_file(root, "test_real.py", ["test_real"])
    (root / "AGENTS.md").write_text(
        "# Notes\n\nThe retired ledger: `git show '1c5befe^:docs/guide/gone.md'`.\n",
        encoding="utf-8",
    )
    assert M.check_steering(root) == []


def test_an_executed_command_with_a_result_is_an_evidence_form(tmp_path):
    """Green control: the command form counts, so config assets stay honest."""
    root = _repo(
        tmp_path,
        "- **A hook landed** — Evidence: `uv run pre-commit run trailing-whitespace "
        "--all-files` → Passed.\n",
    )
    assert M.check_changelog(root) == []


def test_a_removed_bullet_may_name_a_deleted_artifact(tmp_path):
    """Green control: the exemption for Removed is the bullet's whole subject."""
    root = _repo(
        tmp_path,
        "- **Governance teardown** — `docs/guide/gone.md` was retired in `1c5befe`.\n",
        section="Removed",
    )
    assert M.check_changelog(root) == []


def test_a_frozen_release_section_is_not_checked(tmp_path):
    """Green control: released sections are point-in-time records, not claims."""
    root = _repo(tmp_path, "- **A gate landed** — `tests/test_real.py::test_real`.\n")
    _add_test_file(root, "test_real.py", ["test_real"])
    body = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "A frozen entry" in body
    assert M.check_changelog(root) == []


def test_an_absent_unreleased_heading_governs_nothing(tmp_path):
    """Admitted termination: no unreleased lane means nothing to check."""
    root = _repo(tmp_path, "- **A gate landed** — it checks a thing.\n")
    (root / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [0.6.0] - 2026-10-05\n\n- **Old** — fine.\n", encoding="utf-8"
    )
    assert M.check_changelog(root) == []


def test_a_stale_count_claim_fails_the_gate(tmp_path):
    """Counterfactual: the count beside a suite is a claim about that suite.

    This is the rule that would have caught the three drifts found by hand
    while landing #87 -- a bullet asserting 24 tests in a suite collecting 29.
    """
    root = _repo(
        tmp_path,
        "- **A gate landed** — Evidence: `tests/test_counted.py` (7 tests).\n",
    )
    _add_test_file(root, "test_counted.py", ["test_one"])
    findings = M.check_changelog(root)
    assert any("it defines 1" in f for f in findings), findings


def test_a_parametrised_suite_is_left_to_pytest(tmp_path):
    """Admitted limit: a def-site count is not a collected count under
    parametrize, so the gate declines the assertion instead of guessing."""
    root = _repo(
        tmp_path,
        "- **A gate landed** — Evidence: `uv run python -m pytest "
        "tests/test_param.py -q` → 99 passed.\n",
    )
    tests = root / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_param.py").write_text(
        "import pytest\n\n\n"
        "@pytest.mark.parametrize('n', [1, 2, 3])\n"
        "def test_multi(n):\n    assert n\n",
        encoding="utf-8",
    )
    assert M.check_changelog(root) == []


def test_the_real_repo_is_clean():
    """Live green control: the shipped tree passes every rule above."""
    assert M.check(REPO) == []
