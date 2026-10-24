"""Regression tests for .github/scripts/check_monolith_budget.py.

D-125: the monolith may only shrink — over budget fails, under
budget warns "may be lowered" (ratchet pawl, non-fatal), at budget
passes.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / ".github" / "scripts"
SCRIPT = SCRIPTS_DIR / "check_monolith_budget.py"
REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("check_monolith_budget", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _tree(tmp_path: Path, lines: int) -> Path:
    f = tmp_path / "src/maya_mcp_server/maya_scene_module.py"
    f.parent.mkdir(parents=True)
    f.write_text("\n".join(f"# line {i}" for i in range(lines)) + "\n", encoding="utf-8")
    budget = tmp_path / "budget.json"
    budget.write_text(
        json.dumps({"src/maya_mcp_server/maya_scene_module.py": 100}), encoding="utf-8"
    )
    return budget


def test_over_budget_fails_with_error_annotation(tmp_path, capsys):
    mod = _load()
    budget = _tree(tmp_path, 101)
    ok, errors, warnings = mod.check(tmp_path, budget)
    assert not ok
    assert errors and "line=101" in errors[0]
    assert warnings == []


def test_at_budget_passes(tmp_path):
    mod = _load()
    budget = _tree(tmp_path, 100)
    ok, errors, warnings = mod.check(tmp_path, budget)
    assert ok and errors == [] and warnings == []


def test_under_budget_warns_ratchet_hint_not_fail(tmp_path):
    mod = _load()
    budget = _tree(tmp_path, 90)
    ok, errors, warnings = mod.check(tmp_path, budget)
    assert ok and errors == []
    assert warnings and "may be lowered" in warnings[0]


def test_missing_file_is_an_error(tmp_path):
    mod = _load()
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps({"ghost/file.py": 10}), encoding="utf-8")
    ok, errors, _ = mod.check(tmp_path, budget)
    assert not ok and "ghost/file.py" in errors[0]


def test_bad_budget_shape_rejected(tmp_path):
    mod = _load()
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps({"a.py": "soon"}), encoding="utf-8")
    ok, errors, _ = mod.check(tmp_path, budget)
    assert not ok and "map of positive ints" in errors[0]


def test_main_fail_message_carries_exemption_and_routing(tmp_path, monkeypatch, capsys):
    mod = _load()
    budget = _tree(tmp_path, 150)
    monkeypatch.setattr(mod, "REPO", tmp_path)
    monkeypatch.setattr(mod, "DEFAULT_BUDGET", budget)
    monkeypatch.setattr("sys.argv", ["check_monolith_budget.py"])
    assert mod.main() == 1
    out = capsys.readouterr().out
    assert "line shuffling" in out and "same injection unit" in out


def test_repo_monolith_within_budget():
    """The live monolith must sit at-or-under its frozen budget."""
    mod = _load()
    ok, errors, _ = mod.check(REPO, REPO / ".github" / "monolith-budget.json")
    assert ok, errors
