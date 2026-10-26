"""Append-only guard for published README/asset dirs.

.github/assets/ is referenced by absolute URLs pinned to main — old
PyPI release descriptions embed them permanently, so a delete, modify,
or rename there degrades historical release pages. This is a
prevent-accident mechanism: pure additions pass; any D/M/R/T change
under the guarded prefix fails the job.

Invocation: check_assets_append_only.py (no args needed in CI)
  Reads environment set by Actions:
    GITHUB_EVENT_NAME  — pull_request | push | anything else
    GITHUB_BASE_REF    — PR target branch (e.g. main)
    GITHUB_SHA         — head sha (falls back to HEAD)
    GUARD_BEFORE       — github.event.before, mapped explicitly in the
                         workflow step (push events only)
  Local/manual default: origin/main...HEAD (three-dot merge-base).

Base resolution: PR events use merge-base..HEAD three-dot
semantics — checkout needs fetch-depth: 0. push(main) uses
event.before..event.after two-dot (correct for merges and squashes);
an all-zero before (first push of a branch) skips the check.
--no-renames is deliberate: a rename expands to delete+add, so the D
side catches it deterministically instead of relying on the rename
heuristic; the filter still lists M/R/T explicitly.

Escape hatch: a maintainer-aware merge can still land a removal — the
PR body must state the removal and a ledger note records it. The guard
only forces the decision to be visible; it cannot "guarantee" anything
about rendered PyPI pages.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
# GUARDED is a GOVERNANCE constant (append-only), so its
# single source is the per-domain spec file -- derived here at runtime, never
# restated inline.
GUARDED = json.loads(
    (REPO / ".github" / "assets-append-only-spec.yaml").read_text(encoding="utf-8")
)["GUARDED"]


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def _resolve_range(env: dict[str, str]) -> str | None:
    """Return the diff range spec, or None when the check must skip."""
    event = env.get("GITHUB_EVENT_NAME", "local")
    head = env.get("GITHUB_SHA") or "HEAD"
    if event == "pull_request":
        base_ref = env.get("GITHUB_BASE_REF") or "main"
        mb = _git("merge-base", f"origin/{base_ref}", head, cwd=Path.cwd()).strip()
        return f"{mb}...{head}"
    if event == "push":
        before = env.get("GUARD_BEFORE", "")
        if not before or set(before) == {"0"}:
            return None  # first push of a new branch — nothing to diff
        return f"{before}..{head}"
    return f"origin/main...{head}"


def _violations(rng: str, cwd: Path) -> list[str]:
    out = _git(
        "diff",
        "--no-renames",
        "--diff-filter=DMRT",
        "--name-only",
        rng,
        "--",
        *GUARDED,
        cwd=cwd,
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def main() -> int:
    rng = _resolve_range(dict(os.environ))
    if rng is None:
        print("assets-guard: no base ref (first push) — skipped")
        return 0
    try:
        bad = _violations(rng, Path.cwd())
    except subprocess.CalledProcessError as e:
        print(f"::error::assets-guard could not resolve diff range {rng!r}: {e.stderr.strip()}")
        return 2
    if not bad:
        print(f"assets-guard: clean ({rng})")
        return 0
    for f in bad:
        print(
            f"::error file={f}::.github/assets/ is append-only —"
            " delete/modify/rename blocked (D-113). Escape hatch:"
            " maintainer-aware merge with the removal stated in the PR"
            " body + a ledger note."
        )
    print(f"assets-guard: {len(bad)} violation(s) under {GUARDED} in {rng}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
