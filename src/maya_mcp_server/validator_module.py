# --- _mcp_scene custom validator fragment (issue #79): own file, same injection unit ---
#
# NOT a standalone injected module: scene_tools._ensure_module_injected
# concatenates this file onto maya_scene_module.py - after
# introspect_module.py - and injects the assembly as _mcp_scene.
#
# Why the scene_review composition lives here: maya_scene_module.py sits at
# its .github/monolith-budget.json cap, so the audit function cannot grow.
# This fragment adds scene_review_with_validators(), the entry the
# scene_review MCP tool calls; the monolith's own scene_review keeps
# returning the built-in audit alone, so the built-in 0-100 score is
# unchanged by construction.
#
# Runtime floor: Maya >=2023 (Python 3.9). This file carries NO
# __future__ import of its own (it would sit mid-file in the concatenated
# payload = SyntaxError) - the unquoted PEP-604 annotations below are
# py3.9-safe ONLY because the monolith's `from __future__ import annotations`
# governs the assembled unit. Do NOT exec/import this file standalone on py3.9.

from collections.abc import Callable
from typing import Any


# The built-in issue vocabulary: custom issues sit in their own section, but
# reusing these two spellings keeps one severity word meaning one thing.
_VALIDATOR_SEVERITIES = ("error", "warning")

# name -> registration. Session-scoped by construction: this dict lives in the
# injected module namespace, so replacing the module or restarting Maya clears
# it. Re-registering after either event is the documented contract, which is
# also why same-name registration overwrites instead of duplicating.
_VALIDATORS: dict[str, dict[str, Any]] = {}


def _val_err(code: str, message: str, suggestion: str | None = None) -> dict[str, Any]:
    """Domain error result; same envelope as the monolith's _err, kept
    separate so this fragment stays self-contained."""
    err: dict[str, Any] = {"code": code, "message": message}
    if suggestion:
        err["suggestion"] = suggestion
    return {"error": err}


def register_validator(
    name: str,
    predicate: Callable[[], Any],
    severity: str = "error",
    weight: float = 1,
    family: str = "custom",
    message: str | None = None,
) -> dict[str, Any]:
    """Register a studio validator that scene_review_with_validators reports.

    Args:
        name: unique id; re-registering the same name replaces the old rule.
        predicate: zero-arg callable returning bool, or (passed, message).
            It runs inside Maya against live scene data. Any other return is
            recorded as check_error, exactly like a raising predicate.
        severity: "error" or "warning" - the severity its failure is
            reported with.
        weight: points this validator is worth in the custom score; must be > 0.
        family: label carried on the result so a studio can group its own
            rules (e.g. "rigid", "lighting") when it reads `custom`.
        message: report text for the rule, used whenever the predicate
            returns none of its own - on a failure and on a pass alike.

    Returns:
        {"registered": name, "overwritten": bool, "count": int}, or an error
        envelope. Nothing runs at registration time - a bad predicate is only
        discovered when the review runs, and is then isolated as check_error.
    """
    if not isinstance(name, str) or not name.strip():
        return _val_err("invalid_validator_name", "name must be a non-empty string")
    if not callable(predicate):
        return _val_err(
            "invalid_predicate",
            f"predicate for {name!r} is not callable (got {type(predicate).__name__})",
            "pass a zero-arg callable, e.g. lambda: cmds.objExists('GRP_root')",
        )
    if severity not in _VALIDATOR_SEVERITIES:
        return _val_err(
            "invalid_severity",
            f"severity {severity!r} not in {list(_VALIDATOR_SEVERITIES)}",
        )
    if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0:
        return _val_err("invalid_weight", f"weight must be a number > 0 (got {weight!r})")
    if not isinstance(family, str) or not family.strip():
        return _val_err("invalid_family", f"family must be a non-empty string (got {family!r})")
    if message is not None and not isinstance(message, str):
        return _val_err("invalid_message", "message must be a string or None")

    overwritten = name in _VALIDATORS
    _VALIDATORS[name] = {
        "name": name,
        "predicate": predicate,
        "severity": severity,
        "weight": float(weight),
        "family": family,
        "message": message,
    }
    return {"registered": name, "overwritten": overwritten, "count": len(_VALIDATORS)}


def unregister_validator(name: str) -> dict[str, Any]:
    """Remove one validator. Unknown names are an error, not a silent no-op."""
    if name not in _VALIDATORS:
        return _val_err(
            "validator_not_found",
            f"no validator registered as {name!r}",
            "list_validators() shows the active names",
        )
    del _VALIDATORS[name]
    return {"unregistered": name, "count": len(_VALIDATORS)}


def list_validators() -> dict[str, Any]:
    """Show the active registrations, in run order.

    The predicate itself is never echoed - it is a live callable and would not
    survive the JSON trip back to the host.
    """
    return {
        "count": len(_VALIDATORS),
        "validators": [
            {
                "name": reg["name"],
                "family": reg["family"],
                "severity": reg["severity"],
                "weight": reg["weight"],
                "message": reg["message"],
            }
            for reg in _VALIDATORS.values()
        ],
    }


def _run_validator(reg: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one predicate; never raises over its own failure."""
    name = reg["name"]
    status = "failed"
    message = reg["message"]
    try:
        outcome = reg["predicate"]()
    except Exception as e:
        # One broken rule must not hide the rest of the audit (issue #79).
        # Exception, not BaseException: a predicate's own failure is isolated,
        # a KeyboardInterrupt from the human holding the machine is not.
        status = "check_error"
        message = f"validator {name!r} raised {type(e).__name__}: {e}"
        passed = False
    else:
        # Strict on both shapes: (1, "note") must not pass where 1 alone fails.
        if isinstance(outcome, bool):
            passed = outcome
        elif (
            isinstance(outcome, (tuple, list))
            and len(outcome) == 2
            and isinstance(outcome[0], bool)
        ):
            passed = outcome[0]
            if outcome[1] is not None:
                message = str(outcome[1])
        else:
            status = "check_error"
            passed = False
            message = (
                f"validator {name!r} returned {type(outcome).__name__}; "
                "expected bool or (bool, message)"
            )
        if passed and message is None:
            message = f"{name} passed"
    return {
        "name": name,
        "family": reg["family"],
        "severity": reg["severity"],
        "weight": reg["weight"],
        "passed": passed,
        "status": "passed" if passed else status,
        "message": message,
    }


def custom_validator_results() -> dict[str, Any] | None:
    """Run every registered validator and return the `custom` section.

    `score` and `max_score` are weight points from the same denominator -
    weight is what a validator is worth, so 3.0/4.0 says three of four
    registered points passed and all-pass means score == max_score. Keeping
    both in one unit is what stops the built-in 0-100 and this score from
    being mistaken for each other.

    Returns None when no validator is registered, so the section is omitted
    rather than reported as a full-mark pass - a studio that registered
    nothing sees exactly the shape it saw before issue #79.
    """
    if not _VALIDATORS:
        return None

    # Snapshot, not a live view: a predicate may unregister itself (a one-shot
    # rule is the obvious shape), and mutating the dict mid-iteration raises
    # outside _run_validator's try - which would abort the whole review, the
    # exact failure issue #79 exists to prevent.
    validators = [_run_validator(reg) for reg in list(_VALIDATORS.values())]
    issues = [
        {
            "severity": v["severity"],
            "check": "custom",
            "name": v["name"],
            "family": v["family"],
            "msg": v["message"],
        }
        for v in validators
        if not v["passed"]
    ]

    # 0.0 seed: with nothing passed, sum() would return int 0 and the section
    # would read "score": 0 against a float "max_score".
    earned = sum((v["weight"] for v in validators if v["passed"]), 0.0)
    available = sum((v["weight"] for v in validators), 0.0)

    return {
        "validators": validators,
        "score": round(earned, 3),
        "max_score": round(available, 3),
        "issues": issues,
    }


def scene_review_with_validators(checks: list[str] | None = None) -> dict[str, Any]:
    """Built-in scene_review audit plus the studio `custom` section.

    Same argument and result shape as scene_review, plus `custom` when at
    least one validator is registered:
        custom = {"validators": [...], "score": points earned,
                  "max_score": points available, "issues": [...]}
    The built-in `score` never sees a custom weight, so a report from before
    this feature keeps its exact shape.
    """
    # Indirect on purpose: the monolith's scene_review is a name this file
    # only has once the payload is assembled, and a bare reference would be
    # an undefined-name error in the standalone lint pass over this file.
    result: dict[str, Any] = globals()["scene_review"](checks)
    custom = custom_validator_results()
    if custom is not None:
        result["custom"] = custom
    return result
