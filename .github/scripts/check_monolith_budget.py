"""D-125 monolith line-count ratchet.

``maya_scene_module.py`` is a registered monolith under T-07
strangler discipline: incremental splits happen where fixes touch,
never a big-bang rewrite — and the file must not keep growing
("ratchet": frozen baseline, only downward edits). The frozen budget
file caps each registered file's physical line count:

- count > budget  -> fail the lint job
- count < budget  -> pass, but print a "may be lowered" hint so the
  ratchet keeps closing instead of decaying into a high-water mark
  (the OmniRoute #8584 failure mode: a cap that only blocks but
  never tightens silently licenses growth back up to the line)
- count == budget -> pass

Invocation: check_monolith_budget.py [path-to-budget.json]
  Default: .github/monolith-budget.json relative to the repo root.
Budget file shape: {"<repo-relative path>": <max physical lines>}.
The CI never writes the budget file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_BUDGET = REPO / ".github" / "monolith-budget.json"


def physical_lines(path: Path) -> int:
    """Physical line count (newline-delimited records read as text)."""
    with open(path, encoding="utf-8") as fh:
        return sum(1 for _ in fh)


def check(root: Path, budget_path: Path) -> tuple[bool, list[str], list[str]]:
    """(ok, errors, warnings) — warnings are the non-fatal pawl hints."""
    try:
        budget = json.loads(budget_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return False, [f"::error::cannot read budget file {budget_path}: {e}"], []
    if not isinstance(budget, dict) or not all(
        isinstance(k, str) and isinstance(v, int) and v > 0 for k, v in budget.items()
    ):
        return (
            False,
            [
                '::error::budget file must be a {"repo-relative path": max-lines} '
                "map of positive ints"
            ],
            [],
        )

    errors: list[str] = []
    warnings: list[str] = []
    for rel, cap in sorted(budget.items()):
        path = root / rel
        try:
            n = physical_lines(path)
        except OSError as e:
            errors.append(f"::error file={rel}::cannot count lines: {e}")
            continue
        if n > cap:
            errors.append(
                f"::error file={rel},line={cap + 1}::{rel} is {n} physical lines, "
                f"over the frozen monolith budget of {cap}"
            )
        elif n < cap:
            warnings.append(
                f"{rel}: {n}/{cap} — below budget; the budget may be lowered "
                "in the same PR commit (ratchet pawl)"
            )
        else:
            print(f"{rel}: {n}/{cap} at budget — OK")
    return not errors, errors, warnings


def main() -> int:
    budget_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BUDGET
    ok, errors, warnings = check(REPO, budget_path)
    for w in warnings:
        print(f"::warning::{w}")
        print(w)
    for e in errors:
        print(e)
    if not ok:
        print(
            "Exemption channel: do NOT game the metric — no line shuffling "
            "or comment deletion to squeeze under the cap (worse design "
            "under budget is not the goal). Legitimate additions need a "
            "human-approved budget edit in the same PR commit with the "
            "reason stated; the CI never writes the budget."
        )
        print(
            "Where new code goes instead: new Maya-side capability lands in "
            "a separate file in the same injection unit (file boundary = "
            "injection boundary = failure-domain boundary) "
            "(first case: introspect_module.py) — not appended to the "
            "monolith body."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
