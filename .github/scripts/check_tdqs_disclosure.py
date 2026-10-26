"""TDQS disclosure elements static ratchet.

Asserts that required disclosure elements (cataloged in
.github/tdqs-elements.yaml) are present in the live
tool docstrings across the codebase.

Elements are defined with either:
  - any: list of regex patterns (at least one must match)
  - all: list of literal strings (every string must be present)

Mutation-class existence assertions MUST be polarity-aware \u2014
a statement like "without auto_fix mutations" must not satisfy an
assertion that a mutation exists (S-03 lesion: the old autofix_mutation
assertion was satisfied by the negated sentence and CI went fake-green).
Elements opt in via 'polarity_aware: true'; during the legislated
observation period a match occurring only inside a negation scope is
reported as a warning, not an error.

Hardening that requirement (execution-window product, deliberately not
legislated here): the negation scope is the enclosing CLAUSE instead of a fixed
80-char window, and an element may list the legitimate negation-form phrases
that forgive a cue in 'positive_exemptions'.
Direction limit: polarity_aware belongs only on mutation-class
existence assertions whose pattern set is purely positive \u2014 negation-form
pattern elements stay unguarded and rely on the exemption list instead.
The hardened form passed the live corpus with zero false rejections
(asserted in tests/test_check_tdqs_disclosure.py); warn may be promoted to
hard only after that corpus stays clean, reviewed at the 0.6.0 preflight.

Hard-gated in the CI lint job alongside other check scripts.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_ELEMENTS = REPO / ".github" / "tdqs-elements.yaml"
ELEMENTS_FILE = ".github/tdqs-elements.yaml"

# Execution-window-owned mechanism form (warn observation period):
# a match is negated when a negation cue appears in the same CLAUSE before
# the match start. Clause scope, not a fixed window — the retired 80-char
# form flagged legitimate disclosures whose cue sat in an earlier clause
# ("Not idempotent \u2014 each call adds another camera").
#
# Corpus evidence. Both tracks are reproduced by
# .github/scripts/polarity_corpus_probe.py (stdlib, manual: run it, do not
# wire it into CI; it measures, it does not gate).
#
# No corpus counts are quoted in this file on purpose. A hand-pinned count in a
# No corpus counts are quoted in this file on purpose. A hand-pinned count in a
# comment goes stale the moment the corpus moves, and this block did carry such
# a set, which the probe later contradicted. The probe is the single source
# here is the invariant, not the reading of the day. A guard test
# (tests/test_check_tdqs_disclosure.py::test_no_hand_pinned_corpus_counts)
# keeps the rot class out.
#
# PRIMARY surface, the denominator the zero-false-rejection judgement
# uses: each tool against its own docstring and its own elements. The invariant
# is asserted live rather than quoted:
# tests/test_check_tdqs_disclosure.py::test_live_corpus_has_no_polarity_warnings
# pins the guarded set AND requires zero negated-only.
# STRESS surface, diagnostic only: every docstring-bearing function x every
# element pattern. Its surviving negated clauses are correct negative-form
# disclosures on NEGATION-form elements (irreversible / read_only_disclosure /
# no_mutation_no_undo / overwrite_or_persistence_semantics), which carry no
# polarity_aware guard by the direction limit below, so they are never judged.
#
# Caliber note: the stress denominator counts every function/async
# function carrying a docstring, per OCCURRENCE -- nothing de-duplicated. The
# 264 figure quoted in the pre-hardening evidence was a probe artifact: that
# probe keyed docstrings by bare function name into a single dict and silently
# collapsed same-named functions. Do not compare the two numbers as if they
# were the same corpus.
#
# The narrowed scope trades that FP class for cross-clause false negatives —
# the legislated direction (on a fake-green-prone gate an FN hurts
# less than FP noise).
#
# The boundary set is clause TERMINATORS only. A line wrap is deliberately
# not a boundary: the S-03 lesion sentence wraps in real docstrings, and
# forgiving it would reopen the fake-green hole. A terminator counts only
# when followed by whitespace, so decimals ("0.5.0") and abbreviations
# ("e.g.") do not split a clause.
CLAUSE_BOUNDARIES = re.compile(r"[.!?;:\u2014\u2013](?=\s|$)")
# The cue list stays deliberately small \u2014 broad lists
# false-positive on correct negative-form disclosures like "cannot be
# undone" / "does not persist" (five negation-form tokens already live in
# the elements file's own patterns).
NEGATION_CUES = re.compile(
    r"(?i)\b(?:no|not|never|without|cannot|can't|won't|don't|doesn't|didn't"
    r"|nor|neither)\b|\bnon[-\s]|instead of|rather than|free of"
)


def _clause_start(docstring: str, start: int) -> int:
    """Index at which the clause containing start begins."""
    boundaries = list(CLAUSE_BOUNDARIES.finditer(docstring, 0, start))
    return boundaries[-1].end() if boundaries else 0


def _negated(docstring: str, start: int, exemptions: tuple[str, ...]) -> bool:
    """Match at start is negated if a cue sits in its clause before it.

    ``exemptions`` is the element's positive_exemptions list: a
    cue that BELONGS to one of those legitimate negation-form phrases is
    forgiven, because the phrase documents the negated property itself
    rather than negating this match. Scoped to the cue, not to the clause --
    each listed phrase is blanked out before the cue search, so one listed
    phrase cannot forgive an unrelated real negation in the same clause.
    """
    window = docstring[_clause_start(docstring, start) : start].lower()
    for phrase in exemptions:
        if phrase:
            window = window.replace(phrase.lower(), " ")
    return bool(NEGATION_CUES.search(window))


def find_tool_docstring(filepath: Path, tool_name: str) -> str | None:
    """Extract docstring of a named function or async function via AST."""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"), filename=str(filepath))
    except (OSError, SyntaxError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == tool_name:
                return ast.get_docstring(node)
    return None


def check(root: Path, elements_path: Path) -> tuple[bool, list[str], list[str], int]:
    """Validate disclosure elements against tool docstrings.

    Returns (ok, errors, warnings, checked_count). Polarity-aware
    elements whose only matches sit inside a negation scope produce
    warnings during the observation period, never errors.
    """
    try:
        data = json.loads(elements_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return False, [f"::error::cannot read elements file {elements_path}: {e}"], [], 0

    tools = data.get("tools", {})
    if not isinstance(tools, dict) or not tools:
        return False, ["::error::elements file must define a non-empty 'tools' map"], [], 0

    errors: list[str] = []
    warnings: list[str] = []
    checked_elements = 0
    cov_errors, cov_warnings = check_coverage_and_derivation(root, data)
    errors.extend(cov_errors)
    warnings.extend(cov_warnings)

    for tool_name, tool_cfg in sorted(tools.items()):
        file_rel = tool_cfg.get("file")
        if not file_rel:
            errors.append(f"::error::tool '{tool_name}' missing 'file' attribute")
            continue

        file_path = root / file_rel
        if not file_path.exists():
            errors.append(f"::error file={file_rel}::file not found: {file_rel}")
            continue

        docstring = find_tool_docstring(file_path, tool_name)
        if docstring is None:
            errors.append(
                f"::error file={file_rel}::tool function '{tool_name}' or docstring not found"
            )
            continue

        req_elements = tool_cfg.get("required_elements", [])
        for elem in req_elements:
            checked_elements += 1
            eid = elem.get("id", "<unnamed>")
            why = elem.get("why", "")
            # Direction limit: only purely-positive pattern sets
            # carry the guard; negation-form elements stay unguarded.
            polarity = bool(elem.get("polarity_aware"))
            exempt = tuple(elem.get("positive_exemptions", ()))

            matched = False
            negated_only = False
            if "any" in elem:
                patterns = elem["any"]
                if polarity:
                    positive = negated = False
                    for pat in patterns:
                        for m in re.finditer(pat, docstring):
                            if _negated(docstring, m.start(), exempt):
                                negated = True
                            else:
                                positive = True
                    matched = positive
                    negated_only = negated and not positive
                else:
                    matched = any(re.search(pat, docstring) for pat in patterns)
            elif "all" in elem:
                literals = elem["all"]
                if polarity:
                    # all-literals are polarity-aware too: each literal must
                    # occur positively at least once; a literal surviving only
                    # inside negation windows cannot satisfy the assertion.
                    matched = True
                    lit_negated = False
                    for lit in literals:
                        occ = list(re.finditer(re.escape(lit), docstring))
                        if not occ:
                            matched = False
                        elif all(_negated(docstring, m.start(), exempt) for m in occ):
                            matched = False
                            lit_negated = True
                    negated_only = lit_negated
                else:
                    matched = all(lit in docstring for lit in literals)
            else:
                errors.append(
                    f"::error file={file_rel}::element '{eid}' for tool '{tool_name}' "
                    "defines neither 'any' nor 'all'"
                )
                continue

            if not matched:
                if negated_only:
                    warnings.append(
                        f"file={file_rel}::{tool_name} element '{eid}' matched only "
                        "inside negated statements \u2014 a negated claim must not satisfy "
                        "a mutation-class existence assertion (D-180 polarity-aware, "
                        "observation period)"
                    )
                else:
                    errors.append(
                        f"::error file={file_rel}::{tool_name} missing required disclosure "
                        f"element '{eid}' (why: {why})"
                    )

    return not errors, errors, warnings, checked_elements


def main() -> int:
    elements_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ELEMENTS
    ok, errors, warnings, total_checked = check(REPO, elements_path)
    for w in warnings:
        print(f"::warning::{w}")
    for err in errors:
        print(err)
    if not ok:
        print(
            "\nTDQS disclosure check failed. Tool definitions must satisfy the required "
            "required disclosure elements."
        )
        print("See .github/tdqs-elements.yaml for required element specifications and reasons.")
        return 1

    suffix = f" ({len(warnings)} polarity warnings)" if warnings else ""
    print(
        f"TDQS disclosure check OK: {total_checked} elements verified across "
        f"{len(tools_map(elements_path))} tools{suffix}."
    )
    return 0


def tools_map(elements_path: Path) -> dict:
    try:
        return json.loads(elements_path.read_text(encoding="utf-8")).get("tools", {})
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Coverage floor + derivable minimum set
# ---------------------------------------------------------------------------

PIPELINE = REPO / "src" / "maya_mcp_server" / "pipeline.py"

# Functional sibling faces. A tool in one of these families competes
# for the agent's choice with the others, so it must say how it differs:
# boundary_line + boundary_targets_named are derived from membership alone.
#
# execute_code / write_module are deliberately absent. They form a pair, but
# they do not compete with a scene_* read for the same intent, and they already
# carry their own disambiguation element (boundary_execute_code), which is
# asserted. Adding boundary_line there would assert a second convention rather
# than a missing disclosure.
SIBLING_FAMILIES: tuple[frozenset[str], ...] = (
    frozenset({"scene_snapshot", "scene_inspect", "scene_measure", "scene_assert"}),
    frozenset({"scene_validate", "scene_review", "scene_assert"}),
    frozenset({"scene_inspect", "scene_describe", "scene_nodes"}),
    frozenset({"scene_viewport_snapshot", "scene_render_preview"}),
    frozenset({"scene_checkpoint", "scene_checkpoint_list", "scene_rollback"}),
    frozenset({"camera_create", "camera_orbit"}),
    frozenset({"asset_search", "asset_import"}),
    frozenset({"list_sessions", "add_session", "maya_setup_guide"}),
)
# Frozen legacy derivation violations. This is the ONLY record for
# derivation debt, and it is deliberately NOT coverage_exemptions: these 8 pairs
# are tools that DO carry a full 'tools' entry but miss an element derivable
# from their own signature, so by the coverage rule they cannot be exempted
# (covered-and-exempt is itself an error). They are pinned here until each
# tool gains the element. The gate fails when this set GROWS and also when it
# SHRINKS, so a converged tool cannot rot into a permanent free pass.
DERIVATION_BASELINE: frozenset[tuple[str, str]] = frozenset(
    {
        ("scene_aesthetics", "session_prerequisite"),
        ("scene_assert", "session_prerequisite"),
        ("scene_describe", "session_prerequisite"),
        ("scene_inspect", "session_prerequisite"),
        ("scene_nodes", "session_prerequisite"),
        ("scene_plan", "session_prerequisite"),
        ("scene_review", "session_prerequisite"),
        ("scene_snapshot", "session_prerequisite"),
    }
)


def annotated_tools(root: Path) -> set[str]:
    """Every key of pipeline.TOOL_ANNOTATIONS, read via AST.

    The coverage floor is anchored on this map rather than on a hand-kept
    list, so registering a new tool is enough to make the gate fire.
    """
    try:
        tree = ast.parse((root / PIPELINE.relative_to(REPO)).read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as e:
        raise ValueError(f"cannot read {PIPELINE}: {e}") from e
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "TOOL_ANNOTATIONS":
            assert isinstance(node.value, ast.Dict), "TOOL_ANNOTATIONS is not a dict literal"
            out = set()
            for k in node.value.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    out.add(k.value)
            return out
    raise ValueError("TOOL_ANNOTATIONS not found in pipeline.py")


def tool_signatures(root: Path) -> dict[str, dict]:
    """Map each @mcp.tool-decorated function to its file and parameter names.

    Used to derive the minimum set from the signature alone, so the rule
    cannot drift from the real parameter list.
    """
    out: dict[str, dict] = {}
    for path in sorted((root / "src" / "maya_mcp_server").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorated = any(
                isinstance(d, ast.Call)
                and getattr(getattr(d, "func", None), "attr", None) == "tool"
                for d in node.decorator_list
            )
            if not decorated:
                continue
            a = node.args
            names = [x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
            out[node.name] = {
                "file": path.relative_to(root).as_posix(),
                "session_key": "session_key" in names,
            }
    return out


def _version_tuple(text: str) -> tuple[int, ...] | None:
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", text):
        return None
    return tuple(int(part) for part in text.split("."))


def project_version(root: Path) -> tuple[int, ...] | None:
    """Version from pyproject.toml, as a comparable tuple.

    Regex rather than tomllib: the project still supports 3.10 and tomllib is
    3.11+. ``^version`` will not match ``requires-python``, so this picks the
    [project] field and nothing else.
    """
    try:
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'(?m)^version\s*=\s*"([0-9]+(?:\.[0-9]+)*)"', text)
    return tuple(int(part) for part in m.group(1).split(".")) if m else None


def derivable_elements(tool: str, sig: dict, families: tuple[frozenset[str], ...]) -> list[str]:
    """Elements implied by this tool's own signature."""
    derived: list[str] = []
    if sig.get("session_key"):
        derived.append("session_prerequisite")
    if any(tool in fam for fam in families):
        derived += ["boundary_line", "boundary_targets_named"]
    return derived


def check_coverage_and_derivation(root: Path, data: dict) -> tuple[list[str], list[str]]:
    """Enumeration-completeness + derivable-minimum-set gate.

    Coverage is absolute: every annotated tool must be covered or explicitly
    exempt with a reason, and the reverse direction is asserted too so a stale
    entry cannot rot unnoticed.

    Derivation is a ratchet, not an absolute floor. Tools that predate the
    floor are listed in DERIVATION_BASELINE; the gate fails only when the
    violation set GROWS, and shrinks as each tool converges. That is the same
    shape as check_ruff_budget.py / check_monolith_budget.py, and it is what
    makes "tighten only" enforceable rather than aspirational.
    """
    # Both halves of this gate are anchored on the live source tree. A caller
    # pointing at a synthetic root (the tmp_path fixtures in the test suite)
    # has no pipeline to read, so there is nothing to anchor on and nothing to
    # assert -- return clean instead of inventing a failure. The real repo root
    # always has this file; test_live_coverage_floor_holds pins that.
    if not (root / "src" / "maya_mcp_server" / "pipeline.py").exists():
        return [], []

    errors: list[str] = []
    warnings: list[str] = []
    current = project_version(root)
    try:
        annotated = annotated_tools(root)
    except ValueError as e:
        return [f"::error file={PIPELINE.name}::{e}"], []
    if not annotated:
        return [f"::error file={PIPELINE.name}::TOOL_ANNOTATIONS resolved to zero tools"], []

    tools = data.get("tools", {}) or {}
    exempt = data.get("coverage_exemptions", {}) or {}
    sigs = tool_signatures(root)

    # -- coverage, forward ------------------------------------------------
    for tool in sorted(annotated - set(tools) - set(exempt)):
        errors.append(
            f"::error file=.github/tdqs-elements.yaml::tool '{tool}' is in TOOL_ANNOTATIONS but "
            f"neither 'tools' nor 'coverage_exemptions' — add disclosure elements or an "
            f"explicit exemption with a reason (D-195 1)"
        )
    # -- coverage, reverse (no rot) --------------------------------------
    for tool in sorted(set(tools) - annotated):
        errors.append(
            f"::error file=.github/tdqs-elements.yaml::tool '{tool}' has elements but is not in "
            f"TOOL_ANNOTATIONS — stale entry, the gate would never check it (D-195 1)"
        )
    # -- ambiguity: covered AND exempt ------------------------------------
    for tool in sorted(set(tools) & set(exempt)):
        errors.append(
            f"::error file=.github/tdqs-elements.yaml::tool '{tool}' is both covered and exempt — "
            f"pick one, or the exemption silently rots (D-195 1)"
        )
    # -- every exemption must justify itself ------------------------------
    for tool, entry in sorted(exempt.items()):
        if not isinstance(entry, dict) or not str(entry.get("reason", "")).strip():
            errors.append(
                f"::error file={ELEMENTS_FILE}::exemption for '{tool}' needs a non-empty "
                f"'reason' — absence is a CI failure, not a silent skip (D-195 1)"
            )
            continue
        due_raw = str(entry.get("due", "")).strip()
        if not due_raw:
            errors.append(
                f"::error file={ELEMENTS_FILE}::exemption for '{tool}' needs a 'due' — an "
                f"open-ended exemption is the new-code blind spot (D-195 4)"
            )
            continue
        due = _version_tuple(due_raw)
        if due is None:
            errors.append(
                f"::error file={ELEMENTS_FILE}::exemption for '{tool}' has due '{due_raw}', "
                f"which is not a dotted numeric version — the deadline cannot be compared"
            )
        elif current is None:
            warnings.append(
                f"::warning file=.github/tdqs-elements.yaml::cannot read the project version from "
                f"pyproject.toml, so the '{tool}' due ({due_raw}) was NOT checked — an "
                f"overdue exemption would pass unnoticed on a tree without pyproject.toml"
            )
        elif current == due:
            # Exemption tiering: at the deadline the exemption is merely ripe —
            # warn. Past it, the waiver has expired and the gate must bite.
            warnings.append(
                f"::warning file={ELEMENTS_FILE}::exemption for '{tool}' is DUE {due_raw} "
                f"(project version {'.'.join(str(v) for v in current)}) — cover the tool this "
                f"release or file a D-168 waiver renewal; the next version bump turns this "
                f"into an error"
            )
        elif current > due:
            # Exemption tiering: past the deadline. A warning here would be a
            # dead check — "overdue passes unnoticed" was closed only for
            # the un-readable-version branch, not for a real overdue waiver.
            errors.append(
                f"::error file={ELEMENTS_FILE}::exemption for '{tool}' EXPIRED at {due_raw} "
                f"(project version {'.'.join(str(v) for v in current)}) — cover the tool, or "
                f"renew via a D-168§4 waiver renewal carrying all five of its "
                f"elements: a gate_authority countersignature distinct from the "
                f"debt_owner (D-176), a new expiry anchored to an event rather than "
                f"the calendar, the per-row renewal cap of 2, a re-validated "
                f"reason, and the waiver lane closing after gate review"
            )

    # -- derivable minimum set, as a shrink-only ratchet ------------------
    violations: set[tuple[str, str]] = set()
    for tool, cfg in sorted(tools.items()):
        if not isinstance(cfg, dict):
            continue
        present = {e.get("id") for e in cfg.get("required_elements", []) if isinstance(e, dict)}
        sig = sigs.get(tool)
        if sig is None:
            errors.append(
                f"::error file=.github/tdqs-elements.yaml::tool '{tool}' has elements but no "
                f"@mcp.tool function was found — the signature cannot be derived"
            )
            continue
        for eid in derivable_elements(tool, sig, SIBLING_FAMILIES):
            if eid not in present:
                violations.add((tool, eid))

    new = violations - DERIVATION_BASELINE
    for tool, eid in sorted(new):
        errors.append(
            f"::error file=.github/tdqs-elements.yaml::tool '{tool}' is missing '{eid}', which is "
            f"derivable from its own signature — the floor tightens, add it (D-195 2)"
        )
    if violations != DERIVATION_BASELINE:
        stale = DERIVATION_BASELINE - violations
        if stale:
            errors.append(
                f"::error file={ELEMENTS_FILE}::derivable-minimum-set ratchet shrank but the "
                f"baseline still lists {len(stale)} violation(s) "
                f"({', '.join(f'{t}/{e}' for t, e in sorted(stale))}) — drop them from "
                f"DERIVATION_BASELINE in .github/scripts/check_tdqs_disclosure.py "
                f"(D-195 4, tighten only)"
            )
    return errors, warnings


if __name__ == "__main__":
    sys.exit(main())
