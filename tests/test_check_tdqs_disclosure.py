"""Regression tests for .github/scripts/check_tdqs_disclosure.py.

D-175: static ratchet asserting that tool docstrings disclose the required
elements cataloged in .github/tdqs-elements.yaml.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / ".github" / "scripts"
SCRIPT = SCRIPTS_DIR / "check_tdqs_disclosure.py"
REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("check_tdqs_disclosure", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_missing_element_fails(tmp_path):
    mod = _load()
    src_file = tmp_path / "src" / "tool.py"
    src_file.parent.mkdir(parents=True)
    src_file.write_text('def demo_tool():\n    """A simple tool."""\n    pass\n', encoding="utf-8")

    elements_file = tmp_path / "elements.yaml"
    elements_file.write_text(
        json.dumps(
            {
                "tools": {
                    "demo_tool": {
                        "file": "src/tool.py",
                        "required_elements": [
                            {"id": "must_have_foo", "why": "needs foo", "any": ["foo"]},
                            {"id": "must_have_bar", "why": "needs bar", "all": ["bar"]},
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    ok, errors, _warnings, count = mod.check(tmp_path, elements_file)
    assert not ok
    assert count == 2
    assert any("must_have_foo" in err for err in errors)
    assert any("must_have_bar" in err for err in errors)


def test_satisfied_element_passes(tmp_path):
    mod = _load()
    src_file = tmp_path / "src" / "tool.py"
    src_file.parent.mkdir(parents=True)
    src_file.write_text(
        'def demo_tool():\n    """A simple tool with foo and bar."""\n    pass\n', encoding="utf-8"
    )

    elements_file = tmp_path / "elements.yaml"
    elements_file.write_text(
        json.dumps(
            {
                "tools": {
                    "demo_tool": {
                        "file": "src/tool.py",
                        "required_elements": [
                            {"id": "must_have_foo", "why": "needs foo", "any": ["foo"]},
                            {"id": "must_have_bar", "why": "needs bar", "all": ["bar"]},
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    ok, errors, _warnings, count = mod.check(tmp_path, elements_file)
    assert ok
    assert errors == []
    assert count == 2


def test_missing_file_reports_error(tmp_path):
    mod = _load()
    elements_file = tmp_path / "elements.yaml"
    elements_file.write_text(
        json.dumps(
            {
                "tools": {
                    "ghost_tool": {
                        "file": "src/ghost.py",
                        "required_elements": [{"id": "elem", "any": ["x"]}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    ok, errors, _warnings, _ = mod.check(tmp_path, elements_file)
    assert not ok
    assert any("file not found" in err for err in errors)


def test_missing_function_or_docstring_reports_error(tmp_path):
    mod = _load()
    src_file = tmp_path / "src" / "tool.py"
    src_file.parent.mkdir(parents=True)
    src_file.write_text("def other_fn():\n    pass\n", encoding="utf-8")

    elements_file = tmp_path / "elements.yaml"
    elements_file.write_text(
        json.dumps(
            {
                "tools": {
                    "target_tool": {
                        "file": "src/tool.py",
                        "required_elements": [{"id": "elem", "any": ["x"]}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    ok, errors, _warnings, _ = mod.check(tmp_path, elements_file)
    assert not ok
    assert any("tool function 'target_tool' or docstring not found" in err for err in errors)


def test_main_cli_entrypoint(tmp_path, monkeypatch, capsys):
    mod = _load()
    src_file = tmp_path / "src" / "tool.py"
    src_file.parent.mkdir(parents=True)
    src_file.write_text('def demo():\n    """hello world"""\n', encoding="utf-8")

    elements_file = tmp_path / "elements.yaml"
    elements_file.write_text(
        json.dumps(
            {
                "tools": {
                    "demo": {
                        "file": "src/tool.py",
                        "required_elements": [{"id": "hello", "all": ["hello"]}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(mod, "REPO", tmp_path)
    monkeypatch.setattr(mod, "DEFAULT_ELEMENTS", elements_file)
    monkeypatch.setattr("sys.argv", ["check_tdqs_disclosure.py"])
    assert mod.main() == 0
    captured = capsys.readouterr().out
    assert "TDQS disclosure check OK" in captured


def test_main_prefixes_once_and_names_every_warning_class(monkeypatch, capsys):
    """Counterfactual for the release-deadline repair: coverage findings
    already carry `::warning file=`, so main must not add a second prefix
    (GitHub reads `::warning::::warning file=` as no annotation at all), and
    the summary must name the classes it counted rather than calling every
    warning a polarity finding."""
    mod = _load()
    warnings = [
        "::warning file=.github/tdqs-elements.yaml::exemption for 'a' is DUE 0.7.0",
        "file=src/tool.py::demo element 'x' matched only inside negated statements "
        "(polarity_aware guard, observation period)",
        "::warning file=.github/tdqs-elements.yaml::cannot read the project version",
    ]
    monkeypatch.setattr(mod, "check", lambda root, path: (True, [], warnings, 3))
    monkeypatch.setattr("sys.argv", ["check_tdqs_disclosure.py"])
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "::warning::::warning" not in out
    assert "::warning file=.github/tdqs-elements.yaml::exemption for 'a' is DUE" in out
    assert "::warning::file=src/tool.py::demo" in out
    assert "(3 warnings: 1 polarity, 1 ripe-exemption, 1 other)" in out


def test_polarity_negated_only_match_warns_not_fails(tmp_path):
    """D-180: a mutation assertion satisfied only by a negated statement
    produces a warning during the observation period, never an error."""
    mod = _load()
    src_file = tmp_path / "src" / "tool.py"
    src_file.parent.mkdir(parents=True)
    src_file.write_text(
        "def demo_tool():\n"
        '    """A check without auto_fix mutations \u2014 it does not modify."""\n'
        "    pass\n",
        encoding="utf-8",
    )

    elements_file = tmp_path / "elements.yaml"
    elements_file.write_text(
        json.dumps(
            {
                "tools": {
                    "demo_tool": {
                        "file": "src/tool.py",
                        "required_elements": [
                            {
                                "id": "mutates_scene",
                                "why": "mutation disclosure",
                                "polarity_aware": True,
                                "any": [r"(?i)auto_fix\s+mutations"],
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    ok, errors, warnings, _ = mod.check(tmp_path, elements_file)
    assert ok
    assert errors == []
    assert len(warnings) == 1
    assert "negated" in warnings[0]
    assert "mutates_scene" in warnings[0]


def test_polarity_positive_match_satisfies(tmp_path):
    mod = _load()
    src_file = tmp_path / "src" / "tool.py"
    src_file.parent.mkdir(parents=True)
    src_file.write_text(
        'def demo_tool():\n    """Mutates the scene: creates a camera in the scene."""\n    pass\n',
        encoding="utf-8",
    )

    elements_file = tmp_path / "elements.yaml"
    elements_file.write_text(
        json.dumps(
            {
                "tools": {
                    "demo_tool": {
                        "file": "src/tool.py",
                        "required_elements": [
                            {
                                "id": "mutates_scene",
                                "why": "mutation disclosure",
                                "polarity_aware": True,
                                "any": [r"(?i)creates?\s+a\s+camera"],
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    ok, errors, warnings, _ = mod.check(tmp_path, elements_file)
    assert ok
    assert errors == []
    assert warnings == []


def test_polarity_no_match_at_all_still_errors(tmp_path):
    mod = _load()
    src_file = tmp_path / "src" / "tool.py"
    src_file.parent.mkdir(parents=True)
    src_file.write_text('def demo_tool():\n    """A simple tool."""\n    pass\n', encoding="utf-8")

    elements_file = tmp_path / "elements.yaml"
    elements_file.write_text(
        json.dumps(
            {
                "tools": {
                    "demo_tool": {
                        "file": "src/tool.py",
                        "required_elements": [
                            {
                                "id": "mutates_scene",
                                "why": "mutation disclosure",
                                "polarity_aware": True,
                                "any": [r"(?i)creates?\s+a\s+camera"],
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    ok, errors, warnings, _ = mod.check(tmp_path, elements_file)
    assert not ok
    assert any("mutates_scene" in err for err in errors)
    assert warnings == []


def test_polarity_all_branch_negated_literal_warns(tmp_path):
    """D-180: 'all' elements are polarity-aware too \u2014 a literal surviving
    only inside a negation window warns instead of silently passing."""
    mod = _load()
    src_file = tmp_path / "src" / "tool.py"
    src_file.parent.mkdir(parents=True)
    src_file.write_text(
        'def demo_tool():\n    """Does not modify the scene at all."""\n    pass\n',
        encoding="utf-8",
    )
    elements_file = tmp_path / "elements.yaml"
    elements_file.write_text(
        json.dumps(
            {
                "tools": {
                    "demo_tool": {
                        "file": "src/tool.py",
                        "required_elements": [
                            {
                                "id": "mutation_targets",
                                "why": "mutation targets disclosed",
                                "polarity_aware": True,
                                "all": ["modify", "the scene"],
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    ok, errors, warnings, _ = mod.check(tmp_path, elements_file)
    assert ok
    assert errors == []
    assert len(warnings) == 1
    assert "mutation_targets" in warnings[0]


def test_polarity_all_branch_positive_literals_satisfy(tmp_path):
    mod = _load()
    src_file = tmp_path / "src" / "tool.py"
    src_file.parent.mkdir(parents=True)
    src_file.write_text(
        'def demo_tool():\n    """May modify the scene. It does not modify config."""\n    pass\n',
        encoding="utf-8",
    )
    elements_file = tmp_path / "elements.yaml"
    elements_file.write_text(
        json.dumps(
            {
                "tools": {
                    "demo_tool": {
                        "file": "src/tool.py",
                        "required_elements": [
                            {
                                "id": "mutation_targets",
                                "why": "mutation targets disclosed",
                                "polarity_aware": True,
                                "all": ["modify", "the scene"],
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    ok, errors, warnings, _ = mod.check(tmp_path, elements_file)
    assert ok
    assert errors == []
    assert warnings == []


# --- D-190 polarity hardening (clause-scope window + positive_exemptions) ---

_REPEAT_CALLS = r"(?i)each\s+(call|invocation)\s+(adds|creates|produces)"

# D-190(3): the polarity guard is legislated for purely-positive pattern sets
# only, with camera_orbit/mutation_side_effects as the sole sanctioned instance.
# Widening it is a decision, not an edit — so the set is pinned here.
LEGISLATED_GUARDED = ["camera_orbit/mutation_side_effects"]


def _polarity_case(tmp_path, docstring, element):
    """Write a one-tool fixture; return (module, elements_file)."""
    mod = _load()
    src_file = tmp_path / "src" / "tool.py"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text(
        'def demo_tool():\n    """' + docstring + '"""\n    pass\n', encoding="utf-8"
    )
    elements_file = tmp_path / "elements.yaml"
    elements_file.write_text(
        json.dumps(
            {
                "tools": {
                    "demo_tool": {
                        "file": "src/tool.py",
                        "required_elements": [element],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return mod, elements_file


def test_polarity_cross_clause_cue_does_not_negate(tmp_path):
    """D-190(1): a cue separated from the match by a clause boundary does not
    negate it. 'not idempotent - each call adds' discloses multiplicity, it is
    not a negated mutation claim -- the 80-char fixed window mis-flagged it."""
    mod, elements = _polarity_case(
        tmp_path,
        "Creates a camera. Not idempotent \u2014 each call adds another one.",
        {
            "id": "mutates_scene",
            "why": "mutation disclosure",
            "polarity_aware": True,
            "any": [_REPEAT_CALLS],
        },
    )
    ok, errors, warnings, _ = mod.check(tmp_path, elements)
    assert (ok, errors, warnings) == (True, [], [])


def test_polarity_same_clause_exemption_forgives_cue(tmp_path):
    """D-190(2): positive_exemptions names the legitimate negation-form
    phrases; a cue belonging to one of them is forgiven even in the same
    clause as the match."""
    mod, elements = _polarity_case(
        tmp_path,
        "Creates a camera; it is not idempotent, each call adds another one.",
        {
            "id": "mutates_scene",
            "why": "mutation disclosure",
            "polarity_aware": True,
            "any": [_REPEAT_CALLS],
            "positive_exemptions": ["not idempotent"],
        },
    )
    ok, errors, warnings, _ = mod.check(tmp_path, elements)
    assert (ok, errors, warnings) == (True, [], [])


def test_polarity_same_clause_cue_without_exemption_still_warns(tmp_path):
    """Exemptions are opt-in per element: the same clause minus the exemption
    list still reads as negated-only (warn, never error, observation period)."""
    mod, elements = _polarity_case(
        tmp_path,
        "Creates a camera; it is not idempotent, each call adds another one.",
        {
            "id": "mutates_scene",
            "why": "mutation disclosure",
            "polarity_aware": True,
            "any": [_REPEAT_CALLS],
        },
    )
    ok, errors, warnings, _ = mod.check(tmp_path, elements)
    assert ok
    assert errors == []
    assert len(warnings) == 1
    assert "mutates_scene" in warnings[0]


def test_polarity_exemption_does_not_forgive_other_cues_in_clause(tmp_path):
    """D-190(2): the exemption is cue-scoped. A listed phrase forgives the
    cue that belongs to it, not every cue in the same clause —
    otherwise one listed phrase would silently excuse a real negation
    sitting next to it."""
    mod, elements = _polarity_case(
        tmp_path,
        "Not idempotent, and it does not create a camera.",
        {
            "id": "mutates_scene",
            "why": "mutation disclosure",
            "polarity_aware": True,
            "any": [r"(?i)create\s+a\s+camera"],
            "positive_exemptions": ["not idempotent"],
        },
    )
    ok, errors, warnings, _ = mod.check(tmp_path, elements)
    assert ok
    assert errors == []
    assert len(warnings) == 1
    assert "mutates_scene" in warnings[0]


def test_polarity_empty_exemption_cannot_disable_the_guard(tmp_path):
    """A blank entry in positive_exemptions must not erase the window and
    switch the guard off; it is skipped instead."""
    mod, elements = _polarity_case(
        tmp_path,
        "A check without auto_fix mutations.",
        {
            "id": "mutates_scene",
            "why": "mutation disclosure",
            "polarity_aware": True,
            "any": [r"(?i)auto_fix\s+mutations"],
            "positive_exemptions": ["", "   "],
        },
    )
    ok, errors, warnings, _ = mod.check(tmp_path, elements)
    assert ok
    assert errors == []
    assert len(warnings) == 1
    assert "mutates_scene" in warnings[0]


def test_polarity_negation_across_line_wrap_still_detected(tmp_path):
    """D-190(1): the boundary set is clause terminators only. A line wrap is
    not a clause boundary, so the D-180 fake-green lesion (a negated sentence
    satisfying a mutation assertion) stays detectable."""
    mod, elements = _polarity_case(
        tmp_path,
        "A check without\n    auto_fix mutations.",
        {
            "id": "mutates_scene",
            "why": "mutation disclosure",
            "polarity_aware": True,
            "any": [r"(?i)auto_fix\s+mutations"],
        },
    )
    ok, errors, warnings, _ = mod.check(tmp_path, elements)
    assert ok
    assert errors == []
    assert len(warnings) == 1
    assert "mutates_scene" in warnings[0]


def test_live_corpus_has_no_polarity_warnings():
    """D-190(4): zero false rejections on the live corpus is the landing
    precondition — asserted here so it cannot silently regress.
    Also pins the guarded set to the instance D-190(3) legislates. Pinning is
    the whole enforcement: a negation-form element usually has BOTH a positive
    and a negated hit, so mounting the guard on one yields matched=True and
    zero warnings " + EM + " the assertions themselves cannot detect it."""
    mod = _load()
    data = json.loads(mod.DEFAULT_ELEMENTS.read_text(encoding="utf-8"))
    guarded = sorted(
        tool + "/" + elem["id"]
        for tool, cfg in data["tools"].items()
        for elem in cfg.get("required_elements", [])
        if elem.get("polarity_aware")
    )
    assert guarded == LEGISLATED_GUARDED, (
        f"guarded set drifted from the D-190(3) legislated instance: {guarded}"
    )

    ok, errors, warnings, checked = mod.check(mod.REPO, mod.DEFAULT_ELEMENTS)
    assert ok
    assert errors == []
    # check() returns polarity findings and coverage warnings in one list. The
    # live corpus produces no polarity finding (D-190 4); the only other class
    # it may produce is one ripe-exemption note per register entry whose due is
    # the live version (D-204 2). The tally is checked against that register —
    # a fixpoint the success state satisfies — rather than against a list of
    # known message texts, so a new warning class cannot pass unnoticed.
    assert [w for w in warnings if "polarity_aware guard" in w] == []
    version = mod.project_version(mod.REPO)
    assert version is not None, "pyproject.toml must be readable for due checks"
    live = ".".join(map(str, version))
    ripe = [t for t, e in data.get("coverage_exemptions", {}).items() if e.get("due") == live]
    assert len(warnings) == len(ripe), warnings
    assert checked == sum(len(cfg.get("required_elements", [])) for cfg in data["tools"].values())


# ---------------------------------------------------------------------------
# D-195 coverage floor + derivable minimum set
#
# Every assertion below is written as a COUNTERFACTUAL: it mutates the loaded
# elements data and asserts the gate goes red. A test that only asserts the
# live state green proves nothing about the gate -- that is the R43 lesson.
# ---------------------------------------------------------------------------


def _live_data(mod):
    return json.loads(mod.DEFAULT_ELEMENTS.read_text(encoding="utf-8"))


def _covers_err(mod, data, needle):
    errs, _warns = mod.check_coverage_and_derivation(mod.REPO, data)
    return [e for e in errs if needle in e]


def _covers_warn(mod, data, needle):
    _errs, warns = mod.check_coverage_and_derivation(mod.REPO, data)
    return [w for w in warns if needle in w]


def test_live_coverage_floor_holds():
    """All 25 annotated tools are covered or explicitly exempt."""
    mod = _load()
    data = _live_data(mod)
    annotated = mod.annotated_tools(mod.REPO)
    covered = set(data["tools"]) | set(data.get("coverage_exemptions", {}))
    assert annotated - covered == set(), (
        f"tools with neither elements nor an exemption: {sorted(annotated - covered)}"
    )
    errs, _warns = mod.check_coverage_and_derivation(mod.REPO, data)
    assert errs == []


def test_live_no_stale_elements_entries():
    mod = _load()
    data = _live_data(mod)
    assert not _covers_err(mod, data, "stale entry")


def test_live_every_exemption_has_reason_and_due():
    """D-195 1/4: an exemption without a reason is a silent skip; without a
    due it is the new-code blind spot. Neither may pass."""
    mod = _load()
    data = _live_data(mod)
    for tool, entry in data["coverage_exemptions"].items():
        assert entry.get("reason", "").strip(), tool
        assert entry.get("due", "").strip(), tool


def test_live_repo_root_is_not_skipped_by_the_synthetic_root_guard():
    """The skip above must only ever apply to synthetic roots. If it ever
    swallowed the real repo, the whole coverage floor would be silently dead."""
    mod = _load()
    assert (mod.REPO / "src" / "maya_mcp_server" / "pipeline.py").exists()
    assert mod.annotated_tools(mod.REPO), "the real repo must resolve its annotations"


def test_coverage_fires_when_a_tool_is_neither_covered_nor_exempt():
    mod = _load()
    data = _live_data(mod)
    data["coverage_exemptions"].pop("camera_create")
    hits = _covers_err(mod, data, "'camera_create' is in TOOL_ANNOTATIONS")
    assert hits, "removing an exemption must make the coverage gate fire"


def test_coverage_fires_on_a_stale_entry():
    mod = _load()
    data = _live_data(mod)
    data["tools"]["scene_nonexistent"] = {
        "file": "src/maya_mcp_server/scene_tools.py",
        "tier": "P1",
        "required_elements": [],
    }
    assert _covers_err(mod, data, "stale entry"), "an entry for a non-existent tool must fire"


def test_coverage_fires_when_a_tool_is_both_covered_and_exempt():
    mod = _load()
    data = _live_data(mod)
    data["coverage_exemptions"]["scene_measure"] = {"reason": "x", "due": "0.7.0"}
    assert _covers_err(mod, data, "both covered and exempt")


def test_coverage_fires_on_an_exemption_without_a_reason():
    mod = _load()
    data = _live_data(mod)
    data["coverage_exemptions"]["camera_create"] = {"due": "0.7.0"}
    assert _covers_err(mod, data, "needs a non-empty 'reason'")


def test_coverage_fires_on_an_exemption_without_a_due():
    mod = _load()
    data = _live_data(mod)
    data["coverage_exemptions"]["camera_create"] = {"reason": "because"}
    assert _covers_err(mod, data, "needs a 'due'")


def test_derivation_fires_on_a_new_violation():
    """Dropping a derivable element that is NOT in the frozen baseline is a
    regression and must be red immediately -- that is the 'tighten only'
    direction."""
    mod = _load()
    data = _live_data(mod)
    ids = [e["id"] for e in data["tools"]["scene_measure"]["required_elements"]]
    assert "session_prerequisite" in ids
    data["tools"]["scene_measure"]["required_elements"] = [
        e
        for e in data["tools"]["scene_measure"]["required_elements"]
        if e["id"] != "session_prerequisite"
    ]
    hits = _covers_err(mod, data, "missing 'session_prerequisite'")
    assert hits, "dropping a non-baseline derivable element must fire"
    assert any("scene_measure" in h for h in hits)


def test_derivation_fires_when_the_floor_shrinks_but_the_baseline_stays():
    """The reverse guard. Without it the baseline could silently rot into a
    permanent free pass for tools that already converged."""
    mod = _load()
    data = _live_data(mod)
    # give a baselined tool its missing element -> violation set shrinks
    data["tools"]["scene_snapshot"]["required_elements"].append(
        {"id": "session_prerequisite", "why": "test", "any": ["session_key"]}
    )
    hits = _covers_err(mod, data, "ratchet shrank")
    assert hits, "a stale baseline entry must fire"
    assert any("scene_snapshot/session_prerequisite" in h for h in hits), hits


def test_derivation_baseline_is_the_observed_legacy_set():
    """Pin the frozen baseline so an unnoticed element change cannot quietly
    reclassify a legacy gap as a fresh regression (or the reverse)."""
    mod = _load()
    data = _live_data(mod)
    sigs = mod.tool_signatures(mod.REPO)
    live = set()
    for tool, cfg in data["tools"].items():
        present = {e.get("id") for e in cfg.get("required_elements", []) if isinstance(e, dict)}
        for eid in mod.derivable_elements(tool, sigs[tool], mod.SIBLING_FAMILIES):
            if eid not in present:
                live.add((tool, eid))
    assert live == set(mod.DERIVATION_BASELINE), (
        f"baseline drifted; live={sorted(live)} baseline={sorted(mod.DERIVATION_BASELINE)}"
    )


def test_tools_without_session_key_need_no_session_prerequisite():
    """T-R44-04 dry-run, locked in. The rule abstains on tools that take no
    session_key instead of firing on them -- that abstention is what makes the
    gate safe to turn hard."""
    mod = _load()
    data = _live_data(mod)
    sigs = mod.tool_signatures(mod.REPO)
    no_key = {t for t, s in sigs.items() if not s["session_key"]}
    assert no_key, "expected at least one tool that takes no session_key"
    for tool in sorted(no_key):
        assert "session_prerequisite" not in mod.derivable_elements(
            tool, sigs[tool], mod.SIBLING_FAMILIES
        ), tool
    # and none of them may be forced to declare it
    for tool in sorted(no_key & set(data["tools"])):
        ids = {e.get("id") for e in data["tools"][tool]["required_elements"]}
        assert "session_prerequisite" not in ids, tool


def test_sibling_family_members_derive_the_boundary_pair():
    mod = _load()
    tool = "scene_measure"
    assert any(tool in fam for fam in mod.SIBLING_FAMILIES)
    derived = set(mod.derivable_elements(tool, {"session_key": True}, mod.SIBLING_FAMILIES))
    assert {"boundary_line", "boundary_targets_named"} <= derived


# ---------------------------------------------------------------------------
# D-199 firing fixtures. Every polarity_aware element must be able to prove it
# fires: a guarded element with no negation fixture could be silently inert.
# Fixture material is reused from the track-2 "true negative" samples, so the
# corpus and the firing proof cannot drift apart.
# ---------------------------------------------------------------------------


def _guarded(mod):
    data = _live_data(mod)
    return [
        (tool, elem)
        for tool, cfg in sorted(data["tools"].items())
        for elem in cfg.get("required_elements", [])
        if elem.get("polarity_aware")
    ]


# ---------------------------------------------------------------------------
# F2 rot guard. A hand-pinned corpus count in a comment goes stale silently,
# and this file already carried one that the probe contradicted. The numbers
# live in polarity_corpus_probe.py; the invariant lives here.
# ---------------------------------------------------------------------------

_ROT_PATTERNS = (
    re.compile(r"=\s*\d+\s+(?:elements|matches)"),
    re.compile(r"\d+\s+false\s+rejections"),
    re.compile(r"\d+\s+docstring-bearing\s+functions"),
    re.compile(r"=\s*\d+\s*,\s*\d+\s+guarded"),
)


def test_no_hand_pinned_corpus_counts():
    """No live-corpus count may be quoted in the checker's own prose."""
    text = SCRIPT.read_text(encoding="utf-8")
    offenders = []
    for n, line in enumerate(text.split("\n"), 1):
        stripped = line.lstrip()
        if not (stripped.startswith("#") or stripped.startswith('"')):
            continue
        for pat in _ROT_PATTERNS:
            if pat.search(stripped):
                offenders.append(f"{n}: {stripped}")
                break
    assert offenders == [], (
        "hand-pinned corpus counts reintroduced (read them off "
        "polarity_corpus_probe.py instead):\n" + "\n".join(offenders)
    )


def test_probe_is_the_single_source_for_corpus_counts():
    """The probe reports counts; this test only pins that they are derivable
    at run time, i.e. the claim above stays true."""
    out = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "polarity_corpus_probe.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert "PRIMARY:" in out.stdout and "STRESS:" in out.stdout, out.stdout[-2000:]


# ---------------------------------------------------------------------------
# F4: the 'due' is compared against the project version, not merely non-empty.
# ---------------------------------------------------------------------------


def test_live_no_exemption_is_overdue():
    """The live tree must not sit PAST an exemption deadline. Ripeness AT the
    live version is permitted by design — D-204 2 keeps the release that lands
    ON the deadline green — and that warning path is pinned by
    test_due_warns_but_does_not_bite_on_the_deadline_itself, so asserting
    "no ripe exemption" here would contradict the tiering rather than protect
    it."""
    mod = _load()
    data = _live_data(mod)
    version = mod.project_version(mod.REPO)
    assert version is not None, "pyproject.toml must be readable for due checks"
    assert _covers_err(mod, data, "EXPIRED") == [], (
        f"version {'.'.join(map(str, version))} has passed an exemption deadline"
    )


def test_due_warns_but_does_not_bite_on_the_deadline_itself(monkeypatch):
    """D-204 tiering: at 'due' the waiver is merely ripe. It warns so the
    team sees it coming, but it stays a warning so the lint job is green
    through the release that lands ON the deadline."""
    mod = _load()
    data = _live_data(mod)
    monkeypatch.setattr(mod, "project_version", lambda root: (0, 7, 0))
    hits = _covers_warn(mod, data, "is DUE 0.7.0")
    assert len(hits) == 12, hits
    assert "waiver renewal" in hits[0]
    assert _covers_err(mod, data, "EXPIRED") == [], "the deadline itself is not overdue"


def test_past_the_deadline_is_an_error_not_a_warning(monkeypatch):
    """D-204 tiering: past 'due' the gate must bite. A warning here is a dead
    check - the exemption silently stays open-ended, which is exactly what
    D-195 4 exists to prevent."""
    mod = _load()
    data = _live_data(mod)
    monkeypatch.setattr(mod, "project_version", lambda root: (0, 7, 1))
    hits = _covers_err(mod, data, "EXPIRED at 0.7.0")
    assert len(hits) == 12, hits
    assert _covers_warn(mod, data, "is DUE") == [], "expired is an error, not a warning"
    # The five renewal elements the EXPIRED message must name - not a generic
    # five-item checklist.
    for required in (
        "gate_authority",  # countersignature distinct from debt_owner
        "expiry",  # event-anchored, not calendar
        "cap of 2",  # per-row renewal cap
        "reason",  # re-validated on each renewal
        "gate review",  # waiver lane closes after gate review
    ):
        assert required in hits[0], f"a waiver renewal must name {required}"


def test_due_boundary_is_inclusive(monkeypatch):
    mod = _load()
    data = _live_data(mod)
    monkeypatch.setattr(mod, "project_version", lambda root: (0, 6, 9))
    assert _covers_warn(mod, data, "is DUE") == [], "0.6.9 is still short of 0.7.0"
    monkeypatch.setattr(mod, "project_version", lambda root: (0, 7, 0))
    assert _covers_warn(mod, data, "is DUE"), "0.7.0 has reached the deadline"
    assert _covers_err(mod, data, "EXPIRED") == [], "0.7.0 is the deadline, not past it"


def test_unparseable_due_is_an_error_not_a_silent_pass():
    mod = _load()
    data = _live_data(mod)
    data["coverage_exemptions"]["camera_create"] = {"reason": "x", "due": "next release"}
    assert _covers_err(mod, data, "not a dotted numeric version")


def test_unreadable_version_warns_instead_of_silently_passing(monkeypatch):
    """If the version cannot be read, an overdue exemption would pass
    unnoticed. That must be loud, not silent."""
    mod = _load()
    data = _live_data(mod)
    monkeypatch.setattr(mod, "project_version", lambda root: None)
    hits = _covers_warn(mod, data, "was NOT checked")
    assert len(hits) == 12, hits


def test_project_version_reads_the_project_field():
    mod = _load()
    text = (mod.REPO / "pyproject.toml").read_text(encoding="utf-8")
    expected = tuple(
        int(x) for x in re.search(r'(?m)^version\s*=\s*"([0-9.]+)"', text).group(1).split(".")
    )
    assert mod.project_version(mod.REPO) == expected
    assert mod._version_tuple("0.7.0") == (0, 7, 0)
    assert mod._version_tuple("v0.7.0") is None
    assert mod._version_tuple("") is None


# ---------------------------------------------------------------------------
# D-199: fixtures are BOUND to the element they exercise, so the subset
# assertion can actually fail. An unbound list of strings cannot contradict
# anything, which is what the audit caught.
# ---------------------------------------------------------------------------

# (tool, element_id, text). The binding is load-bearing: it is what lets
# test_negation_fixtures_are_a_subset_of_guarded_elements fail when a fixture
# targets something that is not guarded, and what makes a newly guarded element
# demand its own fixture.
NEGATION_FIXTURES = [
    ("camera_orbit", "mutation_side_effects", "This call does not create a new camera."),
    ("camera_orbit", "mutation_side_effects", "It never adds animation curves to the scene."),
    (
        "camera_orbit",
        "mutation_side_effects",
        "Calling this twice will not create a new camera.",
    ),
    ("camera_orbit", "mutation_side_effects", "This call does not mark the scene dirty."),
]


def test_guarded_set_is_legislated():
    mod = _load()
    got = sorted(f"{t}/{e['id']}" for t, e in _guarded(mod))
    assert got == LEGISLATED_GUARDED


def test_every_guarded_element_has_a_firing_negation_fixture():
    """D-199: each guarded element must produce negated_only on a genuine
    negation. An element that cannot fire is an untested gate."""
    mod = _load()
    import re

    for tool, elem in _guarded(mod):
        bound = [f for f in NEGATION_FIXTURES if f[0] == tool and f[1] == elem["id"]]
        assert bound, f"{tool}/{elem['id']} has no bound negation fixture"
        patterns = elem.get("any", [])
        exempt = tuple(elem.get("positive_exemptions", ()))
        fired = False
        for _t, _e, text in bound:
            pos = neg = 0
            for pat in patterns:
                for m in re.finditer(pat, text):
                    if mod._negated(text, m.start(), exempt):
                        neg += 1
                    else:
                        pos += 1
            if neg and not pos:
                fired = True
                break
        assert fired, f"{tool}/{elem['id']} has no fixture that makes it report negated_only"


def test_negation_fixtures_are_a_subset_of_guarded_elements():
    """Cross-assertion (D-199): fixtures may only exist for guarded elements,
    otherwise the fixture list grows a second, unpinned notion of guarded."""
    mod = _load()
    guarded = {f"{t}/{e['id']}" for t, e in _guarded(mod)}
    targets = {f"{t}/{e}" for t, e, _text in NEGATION_FIXTURES}
    assert targets, "the fixture list must not be emptied"
    assert targets <= guarded, f"fixtures target non-guarded elements: {sorted(targets - guarded)}"
    # every guarded element is covered by the fixtures, not just the reverse
    assert guarded <= targets, f"guarded elements without a fixture: {sorted(guarded - targets)}"
