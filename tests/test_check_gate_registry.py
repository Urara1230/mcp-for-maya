"""D-215 pins for .github/scripts/check_gate_registry.py.

The registry is hand-authored (D-215), which is exactly the property that makes
it rot: a hand-kept list drifts and nothing complains. These pins are the
complaint.

Every counterfactual pin here follows the D-213 2 shape -- it asserts a
NON-EMPTY red result. A pin that runs the checker and asserts nothing is not a
pin, it is a smoke test wearing a pin's name. Each red is paired with the
live-green control (`test_the_real_registry_is_clean`) so a rule that fires on
everything is as visible as one that fires on nothing.

The gate's own termination is admitted, not papered over (D-213 4): a corrupt
or empty registry is red and stops there. `test_unreadable_registry_is_the_admitted_
termination_layer` and `test_empty_registry_governs_nothing` pin that decision
rather than adding a fourth layer on top of it.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / ".github" / "scripts" / "check_gate_registry.py"
REGISTRY = REPO / ".github" / "gate-registry.yaml"


def _load():
    spec = importlib.util.spec_from_file_location("check_gate_registry", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _tree(tmp_path: Path, scripts: list[str], wired: list[str]) -> Path:
    """A miniature repo: scripts/, workflows/, and the derived files the
    registry is allowed to point at."""
    sdir = tmp_path / ".github" / "scripts"
    sdir.mkdir(parents=True)
    for name in scripts:
        (sdir / f"{name}.py").write_text("def main() -> int:\n    return 0\n", encoding="utf-8")
    wdir = tmp_path / ".github" / "workflows"
    wdir.mkdir(parents=True)
    body = "jobs:\n  lint:\n    steps:\n"
    for name in wired:
        body += f"      - run: python .github/scripts/{name}.py\n"
    (wdir / "ci.yml").write_text(body, encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "thing.py").write_text(
        "VALUE = 1\n\n\ndef helper():\n    return VALUE\n", encoding="utf-8"
    )
    (tmp_path / "spec.json").write_text(json.dumps({"cap": 3}), encoding="utf-8")
    tdir = tmp_path / "tests"
    tdir.mkdir()
    (tdir / "test_thing.py").write_text(
        "def test_pins_the_gate():\n    assert True\n", encoding="utf-8"
    )
    return tmp_path


def _write_registry(root: Path, entries: list[dict]) -> Path:
    path = root / ".github" / "gate-registry.yaml"
    path.write_text(json.dumps({"schema": "gate-registry/1", "entries": entries}), encoding="utf-8")
    return path


def _good_entry(gate_id: str = "check_demo", **over) -> dict:
    entry = {
        "gate_id": gate_id,
        "bound_class": "gate",
        "expected_sources": ["spec.json::cap"],
        "pin_node_ids": ["tests/test_thing.py::test_pins_the_gate"],
        "exemption": None,
    }
    entry.update(over)
    return entry


# --- live-green control ---------------------------------------------------


def test_the_real_registry_is_clean():
    """The positive control every red below is measured against. Without it a
    rule that fires on all inputs would look identical to a working gate."""
    mod = _load()
    findings = mod.check(REPO, REGISTRY)
    assert findings == [], findings


def test_main_is_zero_on_the_real_registry(monkeypatch):
    mod = _load()
    monkeypatch.setattr(sys, "argv", ["check_gate_registry.py"])
    assert mod.main() == 0


# --- R1 unregistered bound object ----------------------------------------


def test_unregistered_bound_object_is_red(tmp_path):
    mod = _load()
    root = _tree(
        tmp_path, scripts=["check_demo", "check_newcomer"], wired=["check_demo", "check_newcomer"]
    )
    reg = _write_registry(root, [_good_entry()])
    findings = mod.check(root, reg)
    assert findings, "a bound object with no registry entry must be reported"
    assert any("check_newcomer" in f and "not registered" in f for f in findings), findings


def test_a_gate_script_added_later_is_caught_as_unregistered(tmp_path):
    """The ratchet case (D-215 1/4): the registry was correct when written, then
    someone added a gate. This is the failure the gate exists to prevent."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(root, [_good_entry()])
    assert mod.check(root, reg) == []
    (root / ".github" / "scripts" / "check_sneaked_in.py").write_text("x = 1\n", encoding="utf-8")
    findings = mod.check(root, reg)
    assert any("check_sneaked_in" in f and "not registered" in f for f in findings), findings


# --- R2 unresolvable pointers --------------------------------------------


def test_pin_node_pointing_at_a_deleted_test_is_red(tmp_path):
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(root, [_good_entry(pin_node_ids=["tests/test_gone.py::test_deleted"])])
    findings = mod.check(root, reg)
    assert findings, "a pin node that no longer resolves must be reported"
    assert any("test_gone.py" in f for f in findings), findings


def test_pin_node_with_a_missing_test_function_is_red(tmp_path):
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(
        root, [_good_entry(pin_node_ids=["tests/test_thing.py::test_renamed_away"])]
    )
    findings = mod.check(root, reg)
    assert any("test_renamed_away" in f for f in findings), findings


def test_expected_source_with_a_missing_symbol_is_red(tmp_path):
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(root, [_good_entry(expected_sources=["spec.json::no_such_key"])])
    findings = mod.check(root, reg)
    assert any("no_such_key" in f for f in findings), findings


def test_expected_source_pointing_at_a_missing_file_is_red(tmp_path):
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(root, [_good_entry(expected_sources=["gone/spec.yaml::cap"])])
    findings = mod.check(root, reg)
    assert any("gone/spec.yaml" in f for f in findings), findings


def test_python_expected_source_symbol_resolves_via_ast(tmp_path):
    """Positive half of the pointer check: a real symbol must NOT be reported,
    or the weak check would be firing on everything."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(root, [_good_entry(expected_sources=["src/thing.py::helper"])])
    assert mod.check(root, reg) == []


def test_a_pytest_node_id_addressing_a_class_method_resolves(tmp_path):
    """pytest node IDs address methods through their class. A resolver that only
    knew top-level names would call every ``TestFoo::test_bar`` pin unresolvable,
    and a gate that flags the entire registry gets switched off -- worse than
    having no gate at all."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    tdir = root / "tests"
    (tdir / "test_cls.py").write_text(
        "class TestThing:\n    def test_inside_a_class(self):\n        assert True\n",
        encoding="utf-8",
    )
    reg = _write_registry(
        root, [_good_entry(pin_node_ids=["tests/test_cls.py::TestThing::test_inside_a_class"])]
    )
    assert mod.check(root, reg) == []


def test_a_method_missing_from_an_existing_class_is_still_red(tmp_path):
    """The positive half above must not have widened the check into a pass-all."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    (root / "tests" / "test_cls.py").write_text(
        "class TestThing:\n    def test_inside_a_class(self):\n        assert True\n",
        encoding="utf-8",
    )
    reg = _write_registry(
        root, [_good_entry(pin_node_ids=["tests/test_cls.py::TestThing::test_never_written"])]
    )
    assert any("test_never_written" in f for f in mod.check(root, reg))


def test_a_helper_function_named_as_a_pin_fails_the_gate(tmp_path):
    """Counterfactual (D-213 2). R46 audit finding. The first resolver returned
    every top-level name, so a private helper registered as a "pin" resolved
    fine. A helper asserts nothing about the gate, so accepting one makes the
    registry look covered while pinning nothing -- the exact hollow-pin shape
    D-213 2 legislates against. Non-empty red asserted below."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    (root / "tests" / "test_helper.py").write_text(
        "def _build_tree():\n    return 1\n\n\ndef test_real():\n    assert True\n",
        encoding="utf-8",
    )
    reg = _write_registry(root, [_good_entry(pin_node_ids=["tests/test_helper.py::_build_tree"])])
    findings = mod.check(root, reg)
    assert findings, "a helper function must not satisfy the pin obligation"
    assert any("_build_tree" in f and "collectible test" in f for f in findings), findings


def test_a_module_constant_named_as_a_pin_fails_the_gate(tmp_path):
    """Counterfactual (D-213 2). A module-level constant resolved as a pin for
    the same reason a helper did -- nothing about it asserts anything. Non-empty
    red asserted below."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    (root / "tests" / "test_const.py").write_text(
        'TOOLS = {"a", "b"}\n\n\ndef test_real():\n    assert True\n', encoding="utf-8"
    )
    reg = _write_registry(root, [_good_entry(pin_node_ids=["tests/test_const.py::TOOLS"])])
    findings = mod.check(root, reg)
    assert any("TOOLS" in f and "collectible test" in f for f in findings), findings


def test_a_non_test_class_method_named_as_a_pin_fails_the_gate(tmp_path):
    """Counterfactual (D-213 2). A ``Test*`` class still collects only its
    ``test*`` methods; ``_setup`` is not one, so it is not a pin. Non-empty red
    asserted below."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    (root / "tests" / "test_cls2.py").write_text(
        "class TestThing:\n    def _setup(self):\n        return 1\n",
        encoding="utf-8",
    )
    reg = _write_registry(
        root, [_good_entry(pin_node_ids=["tests/test_cls2.py::TestThing::_setup"])]
    )
    assert any("_setup" in f for f in mod.check(root, reg))


def test_a_class_not_named_test_prefix_fails_the_gate(tmp_path):
    """Counterfactual (D-213 2). pytest's default ``python_classes = Test*`` is
    the rule; a helper class that happens to live in a test file is still not
    collectible, so it cannot be a pin. Non-empty red asserted below."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    (root / "tests" / "test_cls3.py").write_text(
        "class Helper:\n    def test_looks_like_a_test(self):\n        assert True\n",
        encoding="utf-8",
    )
    reg = _write_registry(
        root, [_good_entry(pin_node_ids=["tests/test_cls3.py::Helper::test_looks_like_a_test"])]
    )
    assert any("Helper" in f for f in mod.check(root, reg))


def test_every_registered_pin_in_the_real_registry_is_collectible():
    """Live control for the four reds above: if the tightening were too strict,
    the real registry would start failing and this would say so."""
    mod = _load()
    import json

    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    offenders = [
        node
        for entry in data["entries"]
        for node in entry["pin_node_ids"]
        if mod.resolve_pin_node(REPO, node) is not None
    ]
    assert offenders == [], f"registered pins that are not collectible: {offenders}"


# --- R3 stale registration ------------------------------------------------


def test_stale_registration_for_a_deleted_gate_is_red(tmp_path):
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(root, [_good_entry(), _good_entry("check_retired")])
    findings = mod.check(root, reg)
    assert findings, "an entry for a deleted script must be reported"
    assert any("check_retired" in f and "stale" in f for f in findings), findings


# --- R4 veto-power drift --------------------------------------------------


def test_gate_class_with_no_workflow_wiring_is_red(tmp_path):
    """D-212 2 verdict drift: claiming hard-gate standing you do not have is
    worse than not registering, because a reader trusts the registry."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo", "check_unwired"], wired=["check_demo"])
    reg = _write_registry(root, [_good_entry(), _good_entry("check_unwired")])
    findings = mod.check(root, reg)
    assert findings, "an unwired bound_class=gate must be reported"
    assert any("check_unwired" in f and "no workflow" in f for f in findings), findings


def test_an_instrument_is_allowed_to_be_unwired(tmp_path):
    """D-212 tier 2: an instrument has no veto power, so demanding CI wiring
    would be the shape mismatch the five tiers exist to prevent."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo", "a_probe"], wired=["check_demo"])
    reg = _write_registry(
        root,
        [_good_entry(), _good_entry("a_probe", bound_class="instrument", pin_node_ids=[])],
    )
    assert mod.check(root, reg) == []


# --- R5 exemption discipline ---------------------------------------------


def test_expired_exemption_is_red(tmp_path):
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(
        root,
        [_good_entry(exemption={"reason": "temporary", "expires": "2020-01-01"})],
    )
    findings = mod.check(root, reg, today=date(2026, 10, 3))
    assert findings, "an expired exemption must be reported"
    assert any("EXPIRED" in f for f in findings), findings


def test_exemption_expiring_today_is_not_yet_expired(tmp_path):
    """Boundary is inclusive on the deadline itself, matching the D-175 `due`
    convention already in this repo rather than inventing a second one."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(
        root, [_good_entry(exemption={"reason": "temporary", "expires": "2026-10-03"})]
    )
    assert mod.check(root, reg, today=date(2026, 10, 3)) == []
    assert mod.check(root, reg, today=date(2026, 10, 4)) != []


def test_exemption_with_neither_expires_nor_issue_is_red(tmp_path):
    """D-215 5: an exemption with no end and no tracker is a Graveyard entry."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(root, [_good_entry(exemption={"reason": "we meant to"})])
    findings = mod.check(root, reg)
    assert any("expires or issue" in f for f in findings), findings


def test_exemption_with_an_issue_instead_of_a_date_is_accepted(tmp_path):
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(root, [_good_entry(exemption={"reason": "tracked", "issue": 123})])
    assert mod.check(root, reg) == []


def test_unparseable_expiry_is_red_not_silently_accepted(tmp_path):
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(root, [_good_entry(exemption={"reason": "temporary", "expires": "soon"})])
    assert any("not an ISO date" in f for f in findings_of(mod, root, reg)), findings_of(
        mod, root, reg
    )


def findings_of(mod, root, reg):
    return mod.check(root, reg)


# --- R6 zero-pin guard ----------------------------------------------------


def test_gate_with_no_pins_is_red(tmp_path):
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(root, [_good_entry(pin_node_ids=[])])
    findings = mod.check(root, reg)
    assert findings, "a gate with zero pins must be reported (fail-on-empty)"
    assert any("zero-pin guard" in f for f in findings), findings


# --- the admitted termination layer ---------------------------------------


def test_empty_registry_governs_nothing(tmp_path):
    """OPA --fail-on-empty precedent. Zero entries is not 'nothing to report',
    it is a registry that governs nothing -- the exact state this gate exists
    to make impossible."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(root, [])
    findings = mod.check(root, reg)
    assert findings, "an empty registry must be red"
    assert any("governs nothing" in f for f in findings), findings


def test_unreadable_registry_is_the_admitted_termination_layer(tmp_path):
    """D-213 4: recursion terminates here by decision. This pin records the
    decision; adding a fourth layer on top of this is the anti-pattern the
    ruling named."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    bad = root / ".github" / "gate-registry.yaml"
    bad.write_text("{not json at all", encoding="utf-8")
    findings = mod.check(root, bad)
    assert findings, "a corrupt registry must be red, not a silent pass"
    assert any("unreadable" in f for f in findings), findings


# --- blocking posture (D-215 4 flip) -------------------------------------


def test_findings_are_blocking_with_no_opt_out(tmp_path):
    """The observation period is over: an unregistered bound object must be
    red by default. A warning-only mode would make the gate permanent."""
    root = _tree(tmp_path, scripts=["check_demo", "check_orphan"], wired=["check_demo"])
    reg = _write_registry(root, [_good_entry()])
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(reg)],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "::error" in proc.stdout, proc.stdout
    assert "::warning" not in proc.stdout, proc.stdout


# --- enumeration honesty (D-215 7) ---------------------------------------


def test_bound_objects_come_from_the_filesystem_not_a_constant(tmp_path):
    """The enumeration must be a glob. A hand-copied list is the thing D-215 7
    legislated against, and it would pass every other test here."""
    mod = _load()
    sdir = tmp_path / "scripts"
    sdir.mkdir()
    (sdir / "check_alpha.py").write_text("x=1\n", encoding="utf-8")
    (sdir / "beta_probe.py").write_text("x=1\n", encoding="utf-8")
    (sdir / "_private.py").write_text("x=1\n", encoding="utf-8")
    found = mod.enumerate_bound_objects(sdir)
    assert found == {"check_alpha", "beta_probe"}, found
