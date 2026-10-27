"""Compare a ruff JSON report against the frozen error budget.

Usage: check_ruff_budget.py <ruff-report.json> <ruff-baseline.json>

Budget semantics (frozen budget, ratchet-only-down, per-rule since T-10b):
- each top-level segment (src/, tests/, anything else) holds a {rule: count} map;
- count > budget for any rule -> fail (new lint debt — rules cannot subsidise
  each other: fixing 10 E501 can no longer hide 5 new F401);
- count <= budget for every rule -> pass; lower counts may be budgeted down
  in the same PR commit with the reason stated in the commit message.
The CI never writes the budget file.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def segment_of(filename: str) -> str:
    """Top-level directory of a path made relative to the repo root (cwd)."""
    name = filename
    if os.path.isabs(name):
        name = os.path.relpath(name, Path.cwd())
    parts = name.replace(chr(92), "/").split("/")
    if len(parts) <= 1 or parts[0] in ("", ".", ".."):
        return "other"
    return parts[0]


def count_by_rule(report: list[dict]) -> dict[str, dict[str, int]]:
    """{segment: {rule: count}} tallied from a ruff JSON diagnostics list."""
    counts: dict[str, dict[str, int]] = {}
    for diag in report:
        seg = segment_of(diag.get("filename", ""))
        code = diag.get("code") or "unknown"
        seg_counts = counts.setdefault(seg, {})
        seg_counts[code] = seg_counts.get(code, 0) + 1
    return counts


def main() -> int:
    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    budget = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))

    for seg, cap in budget.items():
        if not isinstance(cap, dict):
            print(
                f"::error::baseline segment {seg!r} is a flat count, not a "
                "{rule: count} map — migrate the budget file to the per-rule format"
            )
            return 2

    counts = count_by_rule(report)

    failed = False
    for seg in sorted(set(counts) | set(budget)):
        seg_counts = counts.get(seg, {})
        seg_budget = budget.get(seg, {})
        total = sum(seg_counts.values())
        cap_total = sum(seg_budget.values())
        print(f"{seg}: {total}/{cap_total}")
        for rule in sorted(set(seg_counts) | set(seg_budget)):
            count = seg_counts.get(rule, 0)
            cap = seg_budget.get(rule, 0)
            if count > cap:
                failed = True
                print(f"  {rule}: {count}/{cap} OVER")
                print(f"::error::ruff budget exceeded for {seg}/{rule}: {count} > {cap}")
            else:
                hint = (
                    " (below budget - may be lowered in the same PR commit)" if count < cap else ""
                )
                print(f"  {rule}: {count}/{cap} OK{hint}")

    if failed:
        print(
            "Frozen budget is ratchet-only-down per rule: fix the new errors, or"
            " lower the budget file in the same PR with the reason stated."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
