"""D-142beta release-body disclosure-appendix assertion.

Release-window preflight item 6 (release-body disclosure appendix) — run
manually during the release window, after ``gh release create``. This
script is deliberately NOT part of push CI.

Weak assertions (deliberately NOT checkbox counting — issue #7's body is
an operational copy of the gate list per D-130, and hardcoding a count
like ``0/10`` would freeze a snapshot into an assertion):

1. the release body contains a disclosure-appendix section heading
   (``Disclosure appendix`` or ``Known-unverified``, case-insensitive,
   ``#``-``######`` level — the appendix may sit anywhere in the body);
2. that section is non-empty;
3. the body references issue #7 (the v1.0.0 gate tracker);
4. issue #7 is OPEN — the disclosure obligation is active. A closed
   tracker discharges the obligation and the appendix becomes optional.
   The encoded condition is "#7 unchecked items > 0 OR unfulfilled
   claims exist => appendix stays non-empty" — never a frozen count.

Fail-loud (exit 2, distinct ``::error::`` messages — a fail-open gate is
ceremony decay):

- gh CLI not on PATH;
- gh call failed with HTTP 403 / rate-limit;
- gh call failed otherwise (missing release, HTTP non-200, ...).

Usage:
    check_release_appendix.py <tag>     # e.g. v0.4.0

Env:
    ISSUE  — gate-tracker issue number (default 7)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
Runner = Callable[[list[str]], str]
# D-214: ISSUE is a GOVERNANCE constant (D-142b), so its single source is the
# per-domain spec file -- derived here at runtime, never restated inline. The
# ISSUE env var still overrides it per run; this is only the default.
ISSUE = json.loads((REPO / ".github" / "release-appendix-spec.yaml").read_text(encoding="utf-8"))[
    "ISSUE"
]

_APPENDIX_HEADING = re.compile(
    r"^#{1,6}[ \t]+.*?(disclosure[ \t]+appendix|known[- \t]unverified)",
    re.I | re.M,
)
_NEXT_HEADING = re.compile(r"^#{1,6}[ \t]", re.M)


def _issue_ref(issue: int) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w/])#{issue}\b|/issues/{issue}\b")


class GhError(Exception):
    """args: (kind, detail) — kind in {unavailable, rate_limit, http}."""


def _subprocess_runner(argv: list[str]) -> str:
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout


def _gh(args: list[str], runner: Runner) -> str:
    try:
        return runner(["gh", *args])
    except FileNotFoundError as e:
        raise GhError("unavailable", "gh CLI not on PATH") from e
    except subprocess.CalledProcessError as e:
        detail = ((e.stderr or "") + (e.stdout or "")).strip() or str(e)
        # Rate-limit is its own lane only when the API says so — a bare
        # HTTP 403 (permissions, private repo, suspended account) is an
        # ordinary http-lane failure, not a rate limit.
        if "rate limit" in detail.lower():
            raise GhError("rate_limit", detail) from e
        raise GhError("http", detail) from e


def appendix_section(body: str) -> tuple[int, str] | None:
    """(heading_line, section_text) of the disclosure appendix, or None.

    The section runs from the appendix heading to the next heading of
    any level (or EOF) — position-free per D-142beta.
    """
    m = _APPENDIX_HEADING.search(body)
    if m is None:
        return None
    # The section starts at the END OF THE HEADING LINE — the heading's
    # own trailing text must not count toward "non-empty".
    line_end = body.find("\n", m.end())
    start = line_end + 1 if line_end != -1 else len(body)
    nxt = _NEXT_HEADING.search(body, start)
    end = nxt.start() if nxt else len(body)
    return body.count("\n", 0, m.start()) + 1, body[start:end]


def violations(body: str, issue: int) -> list[str]:
    """Weak-assertion violations on a release body (issue-open case)."""
    out: list[str] = []
    sec = appendix_section(body)
    if sec is None:
        out.append(
            "no disclosure-appendix section heading "
            "(expected e.g. '## Disclosure appendix' / '## Known-unverified')"
        )
    elif not sec[1].strip():
        out.append(f"disclosure appendix (heading line {sec[0]}) is empty")
    if not _issue_ref(issue).search(body):
        out.append(f"release body does not reference issue #{issue}")
    return out


def check(tag: str, issue: int, runner: Runner) -> int:
    try:
        body = _gh(["release", "view", tag, "--json", "body", "--jq", ".body"], runner)
        state = (
            _gh(["issue", "view", str(issue), "--json", "state", "--jq", ".state"], runner)
            .strip()
            .upper()
        )
    except GhError as e:
        kind, detail = e.args
        label = {
            "unavailable": "gh CLI unavailable",
            "rate_limit": "GitHub API rate-limit/403",
            "http": "gh call failed (HTTP non-200 or missing release/issue)",
        }[kind]
        print(f"::error::check_release_appendix: {label} — {detail}")
        return 2

    if state != "OPEN":
        print(
            f"check_release_appendix: issue #{issue} state={state} — tracker "
            "closed, disclosure obligation discharged; appendix optional. PASS"
        )
        return 0

    problems = violations(body, issue)
    for msg in problems:
        print(f"::error::check_release_appendix({tag}): {msg}")
    if problems:
        print(
            "Appendix obligation is active while the gate tracker is open: "
            "ship-time snapshot semantics only — declarative pointers, no "
            "counts, no tracker dump (D-142alpha)."
        )
        return 1
    print(
        f"check_release_appendix({tag}): appendix present, non-empty, "
        f"references #{issue}; issue open — PASS"
    )
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_release_appendix.py <tag>", file=sys.stderr)
        return 2
    try:
        issue = int(os.environ.get("ISSUE", str(ISSUE)))
    except ValueError:
        print(
            f"::error::check_release_appendix: ISSUE env is not an integer: {os.environ['ISSUE']!r}"
        )
        return 2
    return check(sys.argv[1], issue, _subprocess_runner)


if __name__ == "__main__":
    sys.exit(main())
