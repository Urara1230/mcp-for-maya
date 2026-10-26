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
            It runs inside Maya against live scene data.
        severity: "error" or "warning" - the severity its failure is
            reported with.
        weight: points this validator is worth in the custom score; must be > 0.
        family: groups results (e.g. "rigid", "lighting") for filtering downstream.
        message: fallback text when the predicate returns no message of its own.

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
    """Evaluate one predicate; never raises, whatever it does."""
    name = reg["name"]
    status = "failed"
    message = reg["message"]
    try:
        outcome = reg["predicate"]()
    except Exception as e:
        # One broken rule must not hide the rest of the audit (issue #79).
        status = "check_error"
        message = f"validator {name!r} raised {type(e).__name__}: {e}"
        passed = False
    else:
        if isinstance(outcome, bool):
            passed = outcome
        elif isinstance(outcome, (tuple, list)) and len(outcome) == 2:
            passed = bool(outcome[0])
            if outcome[1] is not None:
                message = str(outcome[1])
        else:
            status = "check_error"
            passed = False
            message = (
                f"validator {name!r} returned {type(outcome).__name__}, "
                "expected bool or (passed, message)"
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

    Returns None when no validator is registered, so the section is omitted
    rather than reported as an empty 100/100 - a studio that registered
    nothing sees exactly the pre-#79 shape.
    """
    if not _VALIDATORS:
        return None

    validators = [_run_validator(reg) for reg in _VALIDATORS.values()]
    issues = [
        {
            # A rule that crashed is an error whatever severity it asked for.
            "severity": "error" if v["status"] == "check_error" else v["severity"],
            "check": "custom",
            "name": v["name"],
            "family": v["family"],
            "msg": v["message"],
        }
        for v in validators
        if not v["passed"]
    ]
    max_score = sum(v["weight"] for v in validators)
    earned = sum(v["weight"] for v in validators if v["passed"])

    return {
        "validators": validators,
        "score": round(earned / max_score * 100, 1) if max_score > 0 else 0.0,
        "max_score": max_score,
        "issues": issues,
    }


def scene_review_with_validators(checks: list[str] | None = None) -> dict[str, Any]:
    """Built-in scene_review audit plus the studio `custom` section.

    Same argument and result shape as scene_review, plus `custom` when at
    least one validator is registered:
        custom = {"validators": [...], "score": 0-100, "max_score": weights sum,
                  "issues": [...]}
    The built-in `score` never sees a custom weight, so 0.7.0 reports stay
    comparable with pre-0.7.0 ones.
    """
    review = globals().get("scene_review")
    if not callable(review):
        return _val_err(
            "scene_review_unavailable",
            "the monolith's scene_review is not in this injection unit",
            "re-inject _mcp_scene; this fragment is only valid as part of the assembly",
        )
    result: dict[str, Any] = review(checks)
    custom = custom_validator_results()
    if custom is not None:
        result["custom"] = custom
    return result
