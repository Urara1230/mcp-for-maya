#!/usr/bin/env python3
"""CHANGELOG Fixed-entry field gate (D-230).

Why this exists: every `### Fixed` bullet in scope must carry the two
structured fields `Broken version:` and `fixed version:` with non-empty values
(D-082 7 / D-183 evidence discipline). A Fixed entry that names what it fixed
but not *which version was broken* or *which version ships the fix* is a claim
an auditor cannot verify - the exact wording-drift class the 0.2.0
"manifest-based cache" line was about, applied to the version axis.

SCOPE - existence only, deliberately no value-domain regex:
  * we assert each field is PRESENT on the entry's last line and its value is
    NON-EMPTY;
  * we do NOT validate the value's shape (semver vs `[Unreleased]` literal vs
    prose). That is a semantic judgment for human review, not a machine gate.
    Over-restricting here would make the gate reject legitimate phrasings and
    get deleted - a deleted gate is worse than none.

ENFORCEMENT BOUNDARY (the grandfather register):
  The version-field requirement (D-183) landed at 0.6.0. Releases 0.1.0..0.3.0
  predate it and their Fixed entries carry no such fields - backfilling them
  would mean inventing broken-version facts for fixes made before the rule
  existed, which D-205 forbids (never guess a cause). So the gate enforces
  `[Unreleased]` plus every RELEASED section >= ENFORCED_FROM, and exempts
  earlier sections as a documented baseline boundary - the same pattern as the
  "[0.2.1] is the first compliant section" note for the evidence-pointer rule
  (AGENTS.md, D-082 7). The exemption is a frozen constant, not a per-entry
  waiver list, so it cannot silently grow.

Fold-time note (runbook/CONTRIBUTING): when `[Unreleased]` folds into a release
section, the literal `fixed version: [Unreleased]` is rewritten to that version
number by hand. This gate does not police that rewrite - it is a human step.

Usage:
    python .github/scripts/check_changelog_fixed.py
Exit codes: 0 = every in-scope Fixed entry carries both non-empty fields;
1 = at least one in-scope entry is missing a field or has an empty value.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
CHANGELOG = REPO / "CHANGELOG.md"

# D-214: ENFORCED_FROM is a GOVERNANCE constant (the grandfather boundary), so its
# single source is the per-domain spec file -- derived here at runtime, never
# restated inline. Released sections strictly below this predate the field rule;
# [Unreleased] is always enforced regardless of this value.
_SPEC = json.loads((REPO / ".github" / "changelog-fixed-spec.yaml").read_text(encoding="utf-8"))
ENFORCED_FROM = tuple(int(p) for p in _SPEC["ENFORCED_FROM"])

_VERSION = re.compile(r"(?m)^## \[(\d+(?:\.\d+)*)\]")


def _v(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split("."))


def _entry_blocks(text: str) -> list[tuple[str, str]]:
    """Return [(version_label, entry_text)] for every bullet under a ### Fixed.

    Version chunks are delimited by `## [x.y.z]` headers; the leading
    `## [Unreleased]` chunk is included as label "Unreleased" (it precedes the
    first dotted header and is always in scope).
    """
    version_heads = list(_VERSION.finditer(text))
    blocks: list[tuple[str, str]] = []

    def scan(label: str, start: int, end: int) -> None:
        body = text[start:end]
        m = re.search(r"(?m)^### Fixed\s*$", body)
        if not m:
            return
        fixed_body = body[m.end() :]
        nxt = re.search(r"(?m)^### |^## ", fixed_body)
        if nxt:
            fixed_body = fixed_body[: nxt.start()]
        starts = [mm.start() for mm in re.finditer(r"(?m)^- ", fixed_body)]
        for j, s in enumerate(starts):
            e = starts[j + 1] if j + 1 < len(starts) else len(fixed_body)
            blocks.append((label, fixed_body[s:e]))

    # The [Unreleased] chunk: from its header to the first dotted version header.
    unrel = re.search(r"(?m)^## \[Unreleased\]", text)
    if unrel:
        end = version_heads[0].start() if version_heads else len(text)
        scan("Unreleased", unrel.end(), end)

    for i, vh in enumerate(version_heads):
        start = vh.end()
        end = version_heads[i + 1].start() if i + 1 < len(version_heads) else len(text)
        scan(vh.group(1), start, end)
    return blocks


def _field_value(block: str, field: str) -> str | None:
    """Return the value after `field` on its (unique) line, or None if absent.

    The fields live in the entry's tail region - typically a dedicated line just
    before the trailing `Evidence:` line, not necessarily the final line. Each
    in-scope entry carries each field exactly once, so searching the whole block
    is unambiguous.

    The value runs to the FIRST ';' or end-of-line. That boundary is what makes
    an empty value detectable: when both fields share one line (`Broken version:
    X; fixed version: Y.`), slicing to end-of-line would swallow the next field
    into this one's value and report a present-but-empty field as non-empty -
    the exact escape the R48 audit caught. Splitting on ';' (and the fields never
    contain a semicolon themselves) keeps each value bounded.
    """
    for line in block.splitlines():
        idx = line.find(field)
        if idx >= 0:
            rest = line[idx + len(field) :]
            return rest.split(";", 1)[0].strip()
    return None


def check(path: Path) -> int:
    if not path.exists():
        print(f"::error file={path.name}::missing")
        return 1
    text = path.read_text(encoding="utf-8")

    errors: list[str] = []
    checked = 0
    exempted = 0
    for label, block in _entry_blocks(text):
        # Grandfather register: released sections below the boundary predate the
        # field rule. [Unreleased] is always enforced; only dotted released
        # versions can fall below the boundary. Counted for disclosure, never
        # asserted.
        if label != "Unreleased" and _v(label) < ENFORCED_FROM:
            exempted += 1
            continue
        checked += 1
        for field in ("Broken version:", "fixed version:"):
            value = _field_value(block, field)
            if value is None:
                errors.append(
                    f"::error file={path.name}::[{label}] Fixed entry lacks {field!r} (D-230)"
                )
            elif not value:
                errors.append(
                    f"::error file={path.name}::[{label}] Fixed entry has an "
                    f"empty {field!r} value (D-230)"
                )

    for e in errors:
        print(e)
    if errors:
        return 1
    print(
        f"CHANGELOG Fixed-entry fields OK: {checked} in-scope entries all carry "
        f"both non-empty fields; {exempted} pre-{ENFORCED_FROM[0]}."
        f"{'.'.join(map(str, ENFORCED_FROM[1:]))} entries grandfathered (D-230)"
    )
    return 0


def main() -> int:
    return check(CHANGELOG)


if __name__ == "__main__":
    sys.exit(main())
