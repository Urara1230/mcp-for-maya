#!/usr/bin/env python3
"""Version-consistency gate.

Four places carry the product version and they drift apart silently:

    pyproject.toml            [project] version
    src/maya_mcp_server/__init__.py   __version__
    CHANGELOG.md              newest RELEASED section header
    llms.txt                  the "generated ... (vX.Y.Z)" field

The failure mode this buys down is not cosmetic. A tag is cut from a commit
whose pyproject says 0.6.0 while the wheel metadata and the __init__ say
something else, or the shipped CHANGELOG names a release that never happened.
The release checklist's changelog-vs-tag-diff audit checks the tag against
the tree; this checks the tree against itself.

THE SERVER.JSON TRIPLE
----------------------
server.json sits at the repo root, which is mcp-publisher's own default read
path rather than a placement we chose; its top-level "version" field must equal
pyproject EXACTLY - it is an in-repo manifest, so asserting it is a legal
push-CI check. What we deliberately do NOT assert is registry-active state or
that PyPI actually has the version (the empty-spin prohibition): those
are external facts no push-CI gate may claim. The assertion object is the
manifest text in the tree, nothing beyond it.

THE UNRELEASED TOLERANCE (why this is not a naive equality check)
----------------------------------------------------------------
During normal development pyproject legitimately runs AHEAD of the newest
released CHANGELOG section: you bump the version, then accumulate the work
under [Unreleased] until the release. That state is CORRECT and must not turn
the lint job red - a gate that is always red gets deleted, and a gate that is
deleted is worse than no gate.

So the rule is directional, not symmetric:

    newest released CHANGELOG == pyproject   -> consistent (released state)
    newest released CHANGELOG <  pyproject   -> TOLERATED, warn, and only if
                                                [Unreleased] actually has
                                                content to release
    newest released CHANGELOG >  pyproject   -> ERROR: a released version is
                                                ahead of the package

__init__, llms.txt, and server.json must always equal pyproject exactly. There
is no development state in which those three legitimately differ.

Usage:
    python .github/scripts/check_version_consistency.py
Exit codes: 0 = consistent (possibly in the tolerated unreleased state);
1 = drift.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
PYPROJECT = REPO / "pyproject.toml"
INIT = REPO / "src" / "maya_mcp_server" / "__init__.py"
CHANGELOG = REPO / "CHANGELOG.md"
LLMS = REPO / "llms.txt"
SERVER_JSON = REPO / "server.json"

_SECTION = re.compile(r"(?m)^## \[(\d+(?:\.\d+)*)\]")
_INIT_VERSION = re.compile(r'(?m)^__version__\s*=\s*"([^"]+)"')
_LLMS_VERSION = re.compile(r"tool registry \(v(\d+(?:\.\d+)*)\)")


def _v(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split("."))


def server_json_version(path: Path) -> tuple[int, ...] | None:
    """Top-level "version" field of the mcp-publisher manifest."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    raw = doc.get("version")
    if not isinstance(raw, str):
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)*)", raw)
    return _v(m.group(1)) if m else None


def pyproject_version(text: str) -> tuple[int, ...] | None:
    m = re.search(r'(?m)^version\s*=\s*"(\d+(?:\.\d+)*)"', text)
    return _v(m.group(1)) if m else None


def newest_released(text: str) -> tuple[int, ...] | None:
    for m in _SECTION.finditer(text):
        return _v(m.group(1))
    return None


def unreleased_body(text: str) -> str:
    start = text.find("## [Unreleased]")
    if start < 0:
        return ""
    rest = text[start + len("## [Unreleased]") :]
    nxt = _SECTION.search(rest)
    return (rest[: nxt.start()] if nxt else rest).strip()


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    try:
        pp_text = PYPROJECT.read_text(encoding="utf-8")
    except OSError as e:
        print(f"::error file=pyproject.toml::unreadable: {e}")
        return 1
    pp = pyproject_version(pp_text)
    if pp is None:
        print("::error file=pyproject.toml::no [project] version found")
        return 1
    pp_s = ".".join(map(str, pp))

    init_text = INIT.read_text(encoding="utf-8")
    m = _INIT_VERSION.search(init_text)
    if not m:
        errors.append("::error file=__init__.py::no __version__ assignment found")
    elif _v(m.group(1)) != pp:
        errors.append(
            f"::error file=src/maya_mcp_server/__init__.py::__version__ is "
            f"{m.group(1)} but pyproject.toml is {pp_s} (D-207 9)"
        )

    # The registry manifest at the repo root must match pyproject.
    # Manifest equality only - never assert registry-active or PyPI-has-version
    # (the empty-spin prohibition).
    if not SERVER_JSON.exists():
        errors.append(
            "::error file=server.json::missing at the repo root - it is the "
            "mcp-publisher default read path and must be version-controlled "
            "(D-229 1)"
        )
    else:
        sj = server_json_version(SERVER_JSON)
        if sj is None:
            errors.append(
                "::error file=server.json::top-level 'version' field missing or not a "
                "dotted number - the mcp-publisher manifest cannot be compared to "
                "pyproject.toml (D-229 2)"
            )
        elif sj != pp:
            errors.append(
                f"::error file=server.json::version is {'.'.join(map(str, sj))} but "
                f"pyproject.toml is {pp_s} - the registry manifest drifted from the "
                f"package (D-229 2)"
            )

    log_text = CHANGELOG.read_text(encoding="utf-8")
    newest = newest_released(log_text)
    if newest is None:
        errors.append(
            "::error file=CHANGELOG.md::no released '## [x.y.z]' section found - the "
            "changelog cannot be compared against pyproject.toml (D-207 9)"
        )
    elif newest == pp:
        pass  # released state: everything agrees
    elif newest > pp:
        errors.append(
            f"::error file=pyproject.toml::CHANGELOG has released {newest[0]}."
            f"{'.'.join(map(str, newest[1:]))} but pyproject is {pp_s} - a released "
            f"version is ahead of the package (D-207 9)"
        )
    else:
        body = unreleased_body(log_text)
        if not body:
            errors.append(
                f"::error file=CHANGELOG.md::pyproject is {pp_s} but the newest "
                f"released section is {'.'.join(map(str, newest))} and [Unreleased] "
                f"is empty - nothing to release and the version was bumped anyway "
                f"(D-207 9)"
            )
        else:
            warnings.append(
                f"::warning file=CHANGELOG.md::unreleased state: pyproject is {pp_s}, "
                f"newest released is {'.'.join(map(str, newest))}, work sits under "
                f"[Unreleased] - tolerated (D-207 9)"
            )

    if LLMS.exists():
        llms_text = LLMS.read_text(encoding="utf-8")
        m = _LLMS_VERSION.search(llms_text)
        if not m:
            errors.append(
                "::error file=llms.txt::no 'tool registry (vX.Y.Z)' field - run "
                "check_llms_txt.py --write (D-207 9)"
            )
        elif _v(m.group(1)) != pp:
            errors.append(
                f"::error file=llms.txt::version field is v{m.group(1)} but "
                f"pyproject.toml is {pp_s} - run check_llms_txt.py --write (D-207 9)"
            )
    else:
        errors.append("::error file=llms.txt::missing - run check_llms_txt.py --write (D-207 9)")

    for w in warnings:
        print(w)
    for e in errors:
        print(e)
    if errors:
        return 1
    print(
        f"Version consistency OK: pyproject == __init__ == server.json == llms.txt == {pp_s}"
        + ("; CHANGELOG behind in the tolerated unreleased state." if warnings else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
