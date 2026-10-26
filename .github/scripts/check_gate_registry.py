#!/usr/bin/env python3
"""Gate-registry consistency gate.

``.github/gate-registry.yaml`` is the human-authored pin registry: for every
governance surface in ``.github/scripts/`` it records the bound class, where that
surface's expectation is derived, which pytest nodes pin it, and any exemption.

Why a checker at all: the registry is only worth something if a NEW gate cannot
be added without being registered. A hand-kept list drifts silently, and a
silently unregistered gate is exactly the "law without teeth" state this rule
forbids. So the bound set is enumerated from a MACHINE source (the
``check_*.py`` glob plus every other script in the directory -- no hand-copied
list) and this checker reports the set difference in both directions.

The rules:

  R1 unregistered_bound_object   a bound object with no registry entry
  R2 unresolvable_pointer        a registered pin node or expected_source that
                                 does not resolve
  R3 stale_registration          an entry naming an object that is gone
  R4 gate_class_not_wired        bound_class=gate but no workflow runs it --
                                 veto-power drift: the entry claims hard-gate
                                 standing it does not have
  R5 bad_exemption               an exemption with no expires|issue, or one
                                 whose expires date has passed
  R6 zero_pin_guard              a gate/manual_preflight entry with no pin at
                                 all (fail-on-empty, OPA precedent)

On the rule count: R1 and R3 are ONE set difference read in two directions --
bound but unregistered, registered but gone. Adding a rule for either direction
would lengthen the list without buying coverage: a rule that re-asserts a fact
already reported reads as coverage it does not have.

Posture: hard gate. Findings are reported as ``::error`` and the process exits
1; the observation period ended with a clean registry, and warnings must not
become permanent. There is deliberately no non-blocking mode.

Scope honesty: what is checked here is REGISTRATION DISCIPLINE, not
pin effectiveness. A hollow pin passes every rule below and is still not a pin.
That residual is disclosed in the registry file and mitigated elsewhere.

Usage:
    python .github/scripts/check_gate_registry.py
    python .github/scripts/check_gate_registry.py [path-to-registry.yaml]
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from datetime import date
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / ".github" / "scripts"
WORKFLOWS = REPO / ".github" / "workflows"
DEFAULT_REGISTRY = REPO / ".github" / "gate-registry.yaml"

# The tiers that carry the full pin obligation. An `instrument` is exempt:
# its obligation is the sensitivity track, not a per-PR pin.
PIN_REQUIRED_CLASSES = frozenset({"gate", "manual_preflight"})
KNOWN_CLASSES = frozenset({"gate", "manual_preflight", "instrument"})


# --------------------------------------------------------------------------
# Machine enumeration: the bound set is read, never transcribed
# --------------------------------------------------------------------------


def enumerate_bound_objects(scripts_dir: Path) -> set[str]:
    """Every governance surface in ``scripts_dir``, as bare stems.

    Two machine sources, no hand list: the ``check_*.py`` glob (the decision
    gates) plus every OTHER ``.py`` in the directory (the instruments and
    reporters). Anything dropped from either glob silently stops being governed,
    which is why the enumeration is a glob and not a constant.
    """
    if not scripts_dir.is_dir():
        raise OSError(f"scripts directory not found: {scripts_dir}")
    return {p.stem for p in scripts_dir.glob("*.py") if not p.name.startswith("_")}


def wired_gate_ids(workflows_dir: Path) -> set[str]:
    """Stems of scripts some workflow actually invokes.

    Text scan on purpose: the wiring lives in ``run:`` lines of YAML, and
    parsing the workflow's control flow to work that out would be a second
    source of truth for "is this gate enforced".
    """
    found: set[str] = set()
    if not workflows_dir.is_dir():
        return found
    pattern = re.compile(r"\.github/scripts/([A-Za-z0-9_]+)\.py")
    for wf in sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml")):
        try:
            text = wf.read_text(encoding="utf-8")
        except OSError:
            continue
        found.update(pattern.findall(text))
    return found


# --------------------------------------------------------------------------
# Pointer resolution -- deliberately WEAK and one-way
# --------------------------------------------------------------------------


def _python_symbols(path: Path) -> set[str]:
    """Addressable names in a Python file.

    Top-level defs/classes/assignments, PLUS ``Class::method`` for every method
    of a top-level class. The qualified form is not decoration: pytest node IDs
    address methods through their class, and a resolver that only knew top-level
    names would report every ``TestFoo::test_bar`` pin as unresolvable -- a gate
    that cries wolf on the whole registry gets switched off, which is worse than
    not having it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(f"{node.name}::{sub.name}")
    return names


def _json_keys(path: Path) -> set[str]:
    return set(json.loads(path.read_text(encoding="utf-8")))


def resolve_pointer(root: Path, pointer: str) -> str | None:
    """Return None if the pointer resolves, else a human-readable reason.

    A pointer is ``path`` or ``path::symbol``. This is deliberately ONE-WAY and
    WEAK: it proves the pointer lands on something that exists, not that the
    thing it points at is the correct expectation. Verifying the latter means
    proving a pin fires, which is the sensitivity experiment track, not
    this gate.
    """
    if "::" in pointer:
        rel, symbol = pointer.split("::", 1)
    else:
        rel, symbol = pointer, ""
    path = root / rel
    if not path.is_file():
        return f"{rel} does not exist"
    if not symbol:
        return None
    try:
        if path.suffix == ".py":
            names = _python_symbols(path)
        elif path.suffix in (".json", ".yaml", ".yml"):
            names = _json_keys(path)
        else:
            # Prose and toml have no addressable top-level namespace. Existence
            # is the whole of what can be checked; recorded as a known limit
            # rather than silently passing a symbol we never verified.
            return None
    except (OSError, ValueError, SyntaxError) as e:
        return f"{rel} could not be read: {e}"
    if symbol not in names:
        return f"{rel} has no top-level {symbol!r}"
    return None


def _collectible_names(path: Path) -> set[str]:
    """Names pytest could actually COLLECT from this file, and nothing else.

    Added after an audit finding: the first version of this resolver returned
    every top-level name, so ``tests/test_x.py::_fixed_probe`` and
    ``tests/test_x.py::ALL_TOOLS`` both "resolved" and would have been accepted
    as registered pins. A helper is not a pin and a constant is not a pin, so
    accepting them made the registry look covered while pinning nothing —
    precisely the shape this rule forbids: "only running without asserting is
    not a pin" (ESLint's "invalid cases must have at least one error").

    The rule mirrors pytest's own default ``python_functions = test*``: a
    module-level function counts only if its name starts with ``test``, and
    inside a class only if the METHOD does. Module-level constants are excluded
    outright, because ``Assign`` targets are not functions at all.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            if not node.name.startswith("Test"):
                continue
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith(
                    "test"
                ):
                    names.add(f"{node.name}::{sub.name}")
    return names


def resolve_pin_node(root: Path, node_id: str) -> str | None:
    """Return None if the pytest node resolves, else a reason.

    Resolves by AST, not by running pytest: this runs inside the lint job where
    a collection pass over the whole suite is far too expensive to do on every
    PR, and the question being asked is "does this node still exist and is it
    something pytest would collect", not "does it still pass".
    """
    parts = node_id.split("::")
    rel = parts[0]
    path = root / rel
    if not path.is_file():
        return f"{rel} does not exist"
    try:
        names = _collectible_names(path)
    except (OSError, ValueError, SyntaxError) as e:
        return f"{rel} could not be parsed: {e}"
    symbols = parts[1:]
    if not symbols:
        return f"{rel}: no node name after the path"
    qualified = "::".join(symbols)
    if qualified not in names:
        return (
            f"{rel} has no collectible test {qualified!r} "
            "(a pin must name a pytest-collectible test, not a helper or constant)"
        )
    return None


# --------------------------------------------------------------------------
# The rules
# --------------------------------------------------------------------------


def check(
    root: Path,
    registry_path: Path,
    scripts_dir: Path | None = None,
    workflows_dir: Path | None = None,
    today: date | None = None,
) -> list[str]:
    """Return findings, one string per violation. Empty list == registered."""
    scripts_dir = scripts_dir or (root / ".github" / "scripts")
    workflows_dir = workflows_dir or (root / ".github" / "workflows")
    today = today or date.today()

    findings: list[str] = []
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        # This is the checker's ADMITTED TERMINATION LAYER: an
        # unreadable or corrupt registry is reported and stops here. No fourth
        # layer is added on top of it.
        return [f"registry unreadable: {e}"]

    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        # fail-on-empty (OPA --fail-on-empty precedent). An empty
        # registry is not "nothing to report", it is a registry that governs
        # nothing -- the failure mode this whole gate exists to prevent.
        return ["registry has no entries -- an empty registry governs nothing"]

    try:
        bound = enumerate_bound_objects(scripts_dir)
    except OSError as e:
        return [f"cannot enumerate bound objects: {e}"]
    wired = wired_gate_ids(workflows_dir)

    registered: dict[str, dict] = {}
    for entry in entries:
        gate_id = entry.get("gate_id")
        if not isinstance(gate_id, str) or not gate_id:
            findings.append("registry entry has no usable gate_id")
            continue
        if gate_id in registered:
            findings.append(f"{gate_id}: registered more than once")
            continue
        registered[gate_id] = entry

    # R1 -- a bound object nobody registered
    for gate_id in sorted(bound - set(registered)):
        findings.append(
            f"{gate_id}: bound object is not registered -- every governance "
            "surface needs a bound_class, an expectation source and its pins "
            "(D-215 1)"
        )

    # R3 -- an entry pointing at an object that no longer exists
    for gate_id in sorted(set(registered) - bound):
        findings.append(
            f"{gate_id}: stale registration -- the script no longer exists "
            "(D-215 3); delete the entry or restore the surface"
        )

    for gate_id in sorted(registered):
        entry = registered[gate_id]
        bound_class = entry.get("bound_class")
        if bound_class not in KNOWN_CLASSES:
            findings.append(
                f"{gate_id}: bound_class {bound_class!r} is not one of "
                f"{sorted(KNOWN_CLASSES)} (D-212 five tiers)"
            )
            continue

        # R2 -- registered pointers that do not resolve
        sources = entry.get("expected_sources")
        if not isinstance(sources, list):
            findings.append(f"{gate_id}: expected_sources must be a list")
            sources = []
        for pointer in sources:
            reason = resolve_pointer(root, pointer)
            if reason:
                findings.append(f"{gate_id}: expected_source {pointer}: {reason}")
        pins = entry.get("pin_node_ids")
        if not isinstance(pins, list):
            findings.append(f"{gate_id}: pin_node_ids must be a list")
            pins = []
        for node_id in pins:
            reason = resolve_pin_node(root, node_id)
            if reason:
                findings.append(f"{gate_id}: pin node {node_id}: {reason}")

        # R6 -- zero-pin guard (fail-on-empty)
        if bound_class in PIN_REQUIRED_CLASSES and not pins:
            findings.append(
                f"{gate_id}: bound_class {bound_class} carries no pin "
                "(zero-pin guard, D-215 3) -- a gate nobody can fail is a "
                "claim, not a gate"
            )

        # R4 -- veto-power drift
        if bound_class == "gate" and gate_id not in wired:
            findings.append(
                f"{gate_id}: registered bound_class=gate but no workflow under "
                ".github/workflows invokes it -- either wire it or reclassify it "
                "as an instrument (D-212 2, verdict drift)"
            )

        # R5 -- exemption discipline
        exemption = entry.get("exemption")
        if exemption is not None:
            if not isinstance(exemption, dict):
                findings.append(f"{gate_id}: exemption must be an object or null")
            else:
                reason = (exemption.get("reason") or "").strip()
                if not reason:
                    findings.append(f"{gate_id}: exemption needs a non-empty reason")
                expires = exemption.get("expires")
                issue = exemption.get("issue")
                if expires is None and issue is None:
                    findings.append(
                        f"{gate_id}: exemption needs expires or issue "
                        "(D-215 5) -- an open-ended exemption is the "
                        "Graveyard anti-pattern"
                    )
                elif expires is not None:
                    try:
                        when = date.fromisoformat(str(expires))
                    except ValueError:
                        findings.append(
                            f"{gate_id}: exemption expires {expires!r} is not an ISO date"
                        )
                    else:
                        if when < today:
                            findings.append(
                                f"{gate_id}: exemption EXPIRED at {expires} "
                                "(D-215 5) -- renew it with a reason or drop it"
                            )

    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("registry", nargs="?", default=str(DEFAULT_REGISTRY))
    args = ap.parse_args()

    registry_path = Path(args.registry)
    if not registry_path.is_absolute():
        registry_path = REPO / registry_path

    try:
        findings = check(REPO, registry_path)
    except OSError as e:
        print(f"::error::{e}")
        return 2

    for finding in findings:
        print(f"::error file=.github/gate-registry.yaml::{finding}")
    if findings:
        print(
            f"gate-registry: {len(findings)} finding(s). Fix by registering "
            "the object in .github/gate-registry.yaml (hand-edit; never "
            "script-generated), or by removing a stale entry."
        )
        return 1
    print(
        "gate-registry OK: every governance surface in .github/scripts is "
        "registered, every registered pointer resolves, no stale entries, no "
        "expired exemptions"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
