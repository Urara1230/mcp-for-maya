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
    assert any("check_newcomer" in f and "R1 unregistered_bound_object" in f for f in findings), (
        findings
    )


def test_a_gate_script_added_later_is_caught_as_unregistered(tmp_path):
    """The ratchet case (D-215 1/4): the registry was correct when written, then
    someone added a gate. This is the failure the gate exists to prevent."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(root, [_good_entry()])
    assert mod.check(root, reg) == []
    (root / ".github" / "scripts" / "check_sneaked_in.py").write_text("x = 1\n", encoding="utf-8")
    findings = mod.check(root, reg)
    assert any("check_sneaked_in" in f and "R1 unregistered_bound_object" in f for f in findings), (
        findings
    )


# --- R2 unresolvable pointers --------------------------------------------


def test_pin_node_pointing_at_a_deleted_test_is_red(tmp_path):
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(root, [_good_entry(pin_node_ids=["tests/test_gone.py::test_deleted"])])
    findings = mod.check(root, reg)
    assert findings, "a pin node that no longer resolves must be reported"
    assert any("test_gone.py" in f and "R2 unresolvable_pointer" in f for f in findings), findings


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
    assert any("check_retired" in f and "R3 stale_registration" in f for f in findings), findings


# --- R4 veto-power drift --------------------------------------------------


def test_gate_class_with_no_workflow_wiring_is_red(tmp_path):
    """D-212 2 verdict drift: claiming hard-gate standing you do not have is
    worse than not registering, because a reader trusts the registry."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo", "check_unwired"], wired=["check_demo"])
    reg = _write_registry(root, [_good_entry(), _good_entry("check_unwired")])
    findings = mod.check(root, reg)
    assert findings, "an unwired bound_class=gate must be reported"
    assert any("check_unwired" in f and "R4 gate_class_not_wired" in f for f in findings), findings


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
    assert any("R5 bad_exemption" in f and "EXPIRED" in f for f in findings), findings


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
    assert any("R5 bad_exemption" in f and "expires or issue" in f for f in findings), findings


def test_exemption_with_an_issue_instead_of_a_date_is_accepted(tmp_path):
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(root, [_good_entry(exemption={"reason": "tracked", "issue": 123})])
    assert mod.check(root, reg) == []


def test_unparseable_expiry_is_red_not_silently_accepted(tmp_path):
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(root, [_good_entry(exemption={"reason": "temporary", "expires": "soon"})])
    assert any(
        "R5 bad_exemption" in f and "not an ISO date" in f for f in findings_of(mod, root, reg)
    ), findings_of(mod, root, reg)


def findings_of(mod, root, reg):
    return mod.check(root, reg)


# --- R6 zero-pin guard ----------------------------------------------------


def test_gate_with_no_pins_is_red(tmp_path):
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(root, [_good_entry(pin_node_ids=[])])
    findings = mod.check(root, reg)
    assert findings, "a gate with zero pins must be reported (fail-on-empty)"
    assert any("R6 zero_pin_guard" in f for f in findings), findings


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


# --- every finding is mappable to a rule ---------------------------------


RULE_TAGS = (
    "R1 unregistered_bound_object",
    "R2 unresolvable_pointer",
    "R3 stale_registration",
    "R4 gate_class_not_wired",
    "R5 bad_exemption",
    "R6 zero_pin_guard",
    "malformed_entry",
    "bad_bound_class",
    "registry_unusable",
)


def test_every_finding_names_the_rule_it_fired(tmp_path):
    """The docstring's id column is gone, so the printed tag IS the mapping from
    a CI line to a rule. A finding with no tag is unmappable, and the property
    regressed once by being claimed in a commit message instead of pinned here.

    A loop rather than ``parametrize``: ``check_doc_claims`` declines to verify
    a suite count for any file carrying that marker, and this file's count is
    cited in the changelog."""
    mod = _load()
    demo = ["check_demo"]
    cases = [
        # case id, expected tag, entries, scripts, wired
        (
            "R1",
            "R1 unregistered_bound_object",
            [_good_entry()],
            ["check_demo", "check_extra"],
            demo,
        ),
        (
            "R2-pin",
            "R2 unresolvable_pointer",
            [_good_entry(pin_node_ids=["tests/test_gone.py::test_deleted"])],
            demo,
            demo,
        ),
        (
            "R2-source",
            "R2 unresolvable_pointer",
            [_good_entry(expected_sources=["gone/spec.yaml::cap"])],
            demo,
            demo,
        ),
        ("R2-not-a-list", "R2 unresolvable_pointer", [_good_entry(pin_node_ids="x")], demo, demo),
        (
            "R3",
            "R3 stale_registration",
            [_good_entry(), _good_entry("check_retired")],
            demo,
            demo,
        ),
        ("R4", "R4 gate_class_not_wired", [_good_entry()], demo, []),
        ("R6", "R6 zero_pin_guard", [_good_entry(pin_node_ids=[])], demo, demo),
        (
            "bad_bound_class",
            "bad_bound_class",
            [_good_entry(bound_class="nonsense")],
            demo,
            demo,
        ),
        (
            "R5-noend",
            "R5 bad_exemption",
            [_good_entry(exemption={"reason": "we meant to"})],
            demo,
            demo,
        ),
        (
            "R5-expired",
            "R5 bad_exemption",
            [_good_entry(exemption={"reason": "temporary", "expires": "2020-01-01"})],
            demo,
            demo,
        ),
        (
            "R5-noreason",
            "R5 bad_exemption",
            [_good_entry(exemption={"reason": ""})],
            demo,
            demo,
        ),
        (
            "R5-not-an-object",
            "R5 bad_exemption",
            [_good_entry(exemption="waived")],
            demo,
            demo,
        ),
        (
            "R5-baddate",
            "R5 bad_exemption",
            [_good_entry(exemption={"reason": "x", "expires": "soon"})],
            demo,
            demo,
        ),
        (
            "malformed-duplicate",
            "malformed_entry",
            [_good_entry(), _good_entry()],
            demo,
            demo,
        ),
        (
            "malformed-no-id",
            "malformed_entry",
            [{"bound_class": "gate"}],
            demo,
            demo,
        ),
    ]
    for case_id, expected, entries, scripts, wired in cases:
        root = _tree(tmp_path / case_id, scripts=scripts, wired=wired)
        findings = mod.check(root, _write_registry(root, entries))
        assert findings, f"{case_id}: the fixture must produce at least one finding"
        untagged = [f for f in findings if not any(tag in f for tag in RULE_TAGS)]
        assert untagged == [], f"{case_id} produced an unmappable finding: {untagged}"
        # The tag that SHOULD fire is the one that fires. Asserting only "some
        # tag is present" let a case drift onto a neighbouring rule and stay
        # green -- the defect this test exists to catch.
        assert any(expected in f for f in findings), (
            f"{case_id}: expected {expected!r}, got {findings}"
        )


def test_the_termination_layer_is_tagged(tmp_path):
    """An unreadable or empty registry returns before the rule loop, so its
    findings are the easiest ones to leave untagged. Each case asserts a
    non-empty result first: ``all()`` over an empty list is vacuously true, so
    without that line a silent pass would satisfy this test."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    bad = root / ".github" / "gate-registry.yaml"
    bad.write_text("{not json at all", encoding="utf-8")
    unreadable = mod.check(root, bad)
    assert unreadable, "a corrupt registry must not return an empty finding list"
    assert all("registry_unusable" in f for f in unreadable), unreadable
    empty = mod.check(root, _write_registry(root, []))
    assert empty, "an empty registry must not return an empty finding list"
    assert all("registry_unusable" in f for f in empty), empty
    # The third branch: the bound set cannot be enumerated at all. The registry
    # must be VALID here, or check() returns at the unreadable branch above and
    # this asserts on a finding it never produced.
    good = _write_registry(root, [_good_entry()])
    unenumerable = mod.check(root, good, scripts_dir=root / "no-such-scripts-dir")
    assert unenumerable, "an unlistable scripts dir must not pass silently"
    assert all("registry_unusable" in f for f in unenumerable), unenumerable
    assert any("cannot enumerate" in f for f in unenumerable), unenumerable


def test_a_shape_valid_registry_that_is_not_an_object_is_tagged_not_a_traceback(tmp_path):
    """Parseable is not the same as shaped. A JSON list, string or number reads
    fine and then dies on ``data.get``, which exits through an untagged
    traceback -- the one output this gate's "every finding names its rule"
    property cannot survive, and the one place calling it "unreadable" would be
    false. Each case asserts a non-empty, tagged finding rather than merely
    "no exception": a bare try/except here would pass on any silent return."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = root / ".github" / "gate-registry.yaml"
    for payload in ('[{"gate_id": "check_demo"}]', '"just a string"', "42", "null"):
        reg.write_text(payload, encoding="utf-8")
        findings = mod.check(root, reg)
        assert findings, f"{payload}: a non-object root must not pass silently"
        assert all("registry_unusable" in f for f in findings), findings
        assert any("not an object" in f for f in findings), findings


def test_an_entry_that_is_not_an_object_is_a_malformed_entry(tmp_path):
    """R2 already guards a pointer list of the wrong type; the LIST ITSELF has
    the same failure mode one level up. A registry whose ``entries`` holds a
    number must be a tagged row an operator can fix, not an ``AttributeError``
    that looks like a broken gate."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(root, [7, _good_entry()])
    findings = mod.check(root, reg)
    assert findings, "a non-object entry must not pass silently"
    assert all(any(tag in f for tag in RULE_TAGS) for f in findings), findings
    assert any("malformed_entry" in f for f in findings), findings
    # The good entry beside it is still adjudicated: one bad row must not
    # swallow the rest of the registry.
    assert not any("check_demo:" in f for f in findings), findings


# --- hostile registry values ---------------------------------------------


def test_an_unhashable_bound_class_is_a_finding_not_a_traceback(tmp_path):
    """Membership in a frozenset hashes the value, so a list raises TypeError
    and the run dies without a finding. Type is checked before membership."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    for bad in ([], {}, ["gate"]):
        reg = _write_registry(root, [_good_entry(bound_class=bad)])
        findings = mod.check(root, reg)
        assert findings, f"{bad!r}: must be reported, not raise"
        assert any("bad_bound_class" in f for f in findings), findings


def test_a_ledger_id_in_the_registry_is_not_echoed_to_ci(tmp_path, capsys):
    """Findings echo values read from the registry, and the registry is a file
    an author edits. main() strips at the print boundary, so an id in any of
    those fields never reaches a CI line."""
    mod = _load()
    root = _tree(tmp_path, scripts=["check_demo"], wired=["check_demo"])
    reg = _write_registry(root, [_good_entry(bound_class="D-215")])
    argv = ["check_gate_registry.py", str(reg)]
    import sys as _sys

    old = _sys.argv
    _sys.argv = argv
    try:
        rc = mod.main()
    finally:
        _sys.argv = old
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "D-215" not in out, out
    assert "bad_bound_class" in out, out


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
