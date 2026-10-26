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

import math
from collections.abc import Callable
from typing import Any


# The severity vocabulary of the built-in audit (maya_scene_module.py emits
# error, warning and info), so one severity word means one thing across both
# sections of a report.
_VALIDATOR_SEVERITIES = ("error", "warning", "info")

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


def _safe_text(value: Any) -> str:
    """str() that cannot fail and always returns a str.

    Used on anything a predicate produced. Formatting the message inside
    _run_validator's except block is outside that block's own protection, so
    one object with a hostile __str__ would otherwise abort the review it is
    being reported in - and a non-str return would reach json.dumps.
    """
    try:
        text = str(value)
    except Exception:
        return f"<{type(value).__name__} raised in __str__>"
    return text if isinstance(text, str) else f"<{type(value).__name__} returned non-str>"


def _has_finite_total(weights: list[float]) -> bool:
    """Whether the exact sum of weights is a representable float.

    math.fsum, not sum(): the builtin is order-dependent - CPython 3.12+
    compensates, Maya 2023's 3.9 does not - so a guard and a report that both
    use it can disagree about one registry. fsum is the exact total, and it
    raises rather than returning inf, which is what lets register_validator
    promise the number custom_validator_results later prints.
    """
    try:
        math.fsum(weights)
    except OverflowError:
        return False
    return True


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
        severity: "error", "warning" or "info" - the severity its failure is
            reported with, same vocabulary as the built-in issues.
        weight: points this validator is worth in the custom score; a finite
            number > 0, and the registry total has to stay finite too, so
            weights belong in the same order of magnitude (1-100 is the
            design range).
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
    # Stored stripped: the name is the overwrite key, so " x " and "x" must
    # land on one registration rather than two that both run. A str subclass
    # free to return something else from strip() is refused here rather than
    # raising TypeError out of the dict key later.
    stripped = name.strip()
    if not isinstance(stripped, str) or not stripped:
        return _val_err("invalid_validator_name", "name must be a non-empty string")
    name = stripped
    if not callable(predicate):
        return _val_err(
            "invalid_predicate",
            f"predicate for {name!r} is not callable (got {type(predicate).__name__})",
            "pass a zero-arg callable, e.g. lambda: cmds.objExists('GRP_root')",
        )
    # isinstance first: membership alone asks __eq__'s permission, and an
    # object that claims to be "error" would be stored verbatim and then make
    # json.dumps of the whole report fail.
    if not isinstance(severity, str) or severity not in _VALIDATOR_SEVERITIES:
        return _val_err(
            "invalid_severity",
            f"severity {severity!r} not in {list(_VALIDATOR_SEVERITIES)}",
        )
    if isinstance(weight, bool) or not isinstance(weight, (int, float)):
        return _val_err("invalid_weight", f"weight must be a number > 0 (got {weight!r})")
    try:
        new_weight = float(weight)
    except (TypeError, ValueError, OverflowError):
        # An int past the float range raises OverflowError; a float or int
        # subclass is free to make __float__ raise or return something else
        # entirely. All of them are the caller's mistake, and all of them get
        # the envelope rather than escaping as a raw exception.
        return _val_err("invalid_weight", f"weight is not a usable number (got {weight!r})")
    # Checked on the converted value, never on the caller's object: comparing
    # `weight` here would let a subclass whose __float__ disagrees with its
    # __le__ store a weight the registry was never sized or signed against.
    if not math.isfinite(new_weight) or new_weight <= 0:
        return _val_err("invalid_weight", f"weight must be a finite number > 0 (got {weight!r})")
    # The total this registration lands at, not the one before it: an
    # overwrite replaces its own weight, so charging the old copy too would
    # refuse a re-registration whose result is finite. Infinity is not valid
    # JSON, and an Infinity score costs the caller the whole scene_review.
    replaced = _VALIDATORS.get(name)
    weights = [reg["weight"] for reg in _VALIDATORS.values() if reg is not replaced]
    weights.append(new_weight)
    if not _has_finite_total(weights):
        return _val_err(
            "weight_overflow",
            f"registering {weight!r} would overflow the registry weight total",
            "use weights in the same order of magnitude (1-100 is the design range)",
        )
    if not isinstance(family, str) or not family.strip():
        return _val_err("invalid_family", f"family must be a non-empty string (got {family!r})")
    stripped_family = family.strip()
    if not isinstance(stripped_family, str):
        return _val_err("invalid_family", f"family must be a non-empty string (got {family!r})")
    family = stripped_family
    if message is not None and not isinstance(message, str):
        return _val_err("invalid_message", "message must be a string or None")

    overwritten = name in _VALIDATORS
    _VALIDATORS[name] = {
        "name": name,
        "predicate": predicate,
        "severity": severity,
        # The float the guard checked, converted once. Converting again here
        # would store whatever a __float__ returns the second time, so the
        # registry could hold a weight the total was never verified against.
        "weight": new_weight,
        "family": family,
        "message": message,
    }
    return {"registered": name, "overwritten": overwritten, "count": len(_VALIDATORS)}


def unregister_validator(name: str) -> dict[str, Any]:
    """Remove one validator. Unknown names are an error, not a silent no-op."""
    # Same name contract as register_validator, so a non-string cannot raise
    # TypeError out of a dict lookup instead of returning the envelope.
    if not isinstance(name, str) or not name.strip():
        return _val_err("invalid_validator_name", "name must be a non-empty string")
    name = name.strip()
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
        # _safe_text because this line is being built inside the except: an
        # exception whose __str__ raises would otherwise escape the very
        # isolation that caught it and cost the caller the whole audit.
        status = "check_error"
        message = f"validator {_safe_text(name)!r} raised {type(e).__name__}: {_safe_text(e)}"
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
                message = _safe_text(outcome[1])
        else:
            status = "check_error"
            passed = False
            message = (
                f"validator {_safe_text(name)!r} returned {type(outcome).__name__}; "
                "expected bool or (bool, message)"
            )
        if message is None:
            # Both branches: a failing rule with nothing registered would
            # otherwise report msg as null and print "None" in the cos view.
            message = f"{name} passed" if passed else f"{name} failed"
    return {
        "name": name,
        "family": reg["family"],
        "severity": reg["severity"],
        "weight": reg["weight"],
        # passed is the fact the score filters on; status is the same fact
        # with the reason attached - a failure and a crash both fail here,
        # only one of them says the rule itself broke.
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

    # fsum, the same total register_validator tested against - and a subset of
    # positive weights can only be smaller, so both stay representable.
    # fsum returns float even for an empty selection, which sum() would report
    # as int 0 against a float max_score.
    earned = math.fsum(v["weight"] for v in validators if v["passed"])
    available = math.fsum(v["weight"] for v in validators)

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
