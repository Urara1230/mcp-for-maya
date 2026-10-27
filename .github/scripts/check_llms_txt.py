#!/usr/bin/env python3
"""llms.txt generator + gate.

Why this is a checker and not a document: a hand-written tool inventory is the
third source of truth. The tool set already has two - the @mcp.tool
registrations and pipeline.TOOL_ANNOTATIONS - and a third copy in prose is how a
consumer ends up told a tool exists that was deleted (or the reverse). So the
page is DERIVED from those two and this script is the gate, in the same shape
as the rest of the .github/scripts family.

llms.txt follows the llmstxt.org convention: an H1, a blockquote summary, then
`## section` headings with `- [name](url): description` entries. It is capped at
100 lines: a discovery file that must be paged through has stopped
being a discovery file. `llms-full.txt` is explicitly NOT produced - one file,
one job.

Usage:
    python .github/scripts/check_llms_txt.py --write   # (re)generate llms.txt
    python .github/scripts/check_llms_txt.py            # gate; exit 1 on drift

Drift in either direction is an error: a tool in the code but missing from the
file, an entry in the file that is not a tool, a wrong class label, or a wrong
summary line. The 100-line cap is an error too, not a warning.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / ".github" / "scripts"
LLMS = REPO / "llms.txt"
PIPELINE = Path("src/maya_mcp_server/pipeline.py")

# LINE_CAP is a GOVERNANCE constant, so its single source is
# the per-domain spec file -- derived here at runtime, never restated inline.
# The tool COUNT beside it is object-derivable, so it stays derived from
# pipeline.TOOL_ANNOTATIONS and is deliberately NOT mirrored into the spec
# (spec files must not mirror object state into a second source of truth).
LINE_CAP = json.loads((REPO / ".github" / "llms-txt-spec.yaml").read_text(encoding="utf-8"))[
    "LINE_CAP"
]
_SEC_SOURCE = __file__.rsplit("/", 1)[-1]
_LLMS_NAME = "llms.txt"


def repo_url(root: Path) -> str:
    """Project URL, read from pyproject [project.urls].

    Hard-coding the owner here was a fabrication bug: the generator emitted
    links to a repository this project has never occupied, and because the gate
    compared the file against its own constants it could never notice. A
    generated page must have every URL traceable to a single source, exactly
    like the tool list does.
    """
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^Repository\s*=\s*"([^"]+)"', text)
    if not m:
        raise ValueError("pyproject.toml has no [project.urls] Repository to derive from")
    return m.group(1).rstrip("/")


def doc_url(root: Path, path: str) -> str:
    return f"{repo_url(root)}/blob/main/{path}"


# Reading pipeline.TOOL_ANNOTATIONS through the sibling checker keeps ONE
# definition of "which tools exist" and "what class is each" instead of a
# fourth copy of the matrix in this file.
sys.path.insert(0, str(SCRIPTS))
import check_tdqs_disclosure as tdqs  # noqa: E402


# Editorial grouping only - which section heading a tool sits under. It is NOT
# a tool inventory: adding a tool must NOT require touching this map, and a tool
# missing from it must never be silently dropped from the page. unsectioned()
# below turns that into a gate failure instead.
_SEC = {
    "list_sessions": "Sessions",
    "scene_snapshot": "Spatial awareness",
    "scene_inspect": "Spatial awareness",
    "scene_measure": "Spatial awareness",
    "scene_assert": "Spatial awareness",
    "scene_validate": "Spatial awareness",
    "scene_checkpoint_list": "Transactions",
    "scene_checkpoint": "Transactions",
    "scene_rollback": "Transactions",
    "scene_aesthetics": "Analysis",
    "scene_review": "Analysis",
    "scene_describe": "Introspection",
    "scene_nodes": "Introspection",
    "scene_viewport_snapshot": "Visual loop (GUI sessions only)",
    "scene_render_preview": "Visual loop (GUI sessions only)",
    "scene_plan": "Scene planning",
    "camera_create": "Cameras",
    "camera_orbit": "Cameras",
    "scene_export": "Export",
    "asset_search": "Assets (Poly Haven, CC0)",
    "asset_import": "Assets (Poly Haven, CC0)",
    "execute_code": "Escapes (policy-disableable)",
    "write_module": "Escapes (policy-disableable)",
    "maya_setup_guide": "Escapes (policy-disableable)",
    "add_session": "Sessions",
}

_ORDER = (
    "Sessions",
    "Spatial awareness",
    "Introspection",
    "Analysis",
    "Transactions",
    "Scene planning",
    "Cameras",
    "Export",
    "Assets (Poly Haven, CC0)",
    "Visual loop (GUI sessions only)",
    "Escapes (policy-disableable)",
)


def annotation_matrix(root: Path) -> dict[str, dict[str, bool]]:
    """{tool: {read_only_hint, destructive_hint, idempotent_hint, open_world_hint}}.

    AST, not import: this runs in the lint job before any dependency install
    and must not be defeatable by import side effects.
    """
    path = root / PIPELINE
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def is_annotations(node: ast.AST) -> bool:
        """ToolAnnotations is spelled both bare and as mt.ToolAnnotations."""
        fn = getattr(node, "func", node)
        return (
            getattr(fn, "id", None) == "ToolAnnotations"
            or getattr(fn, "attr", None) == "ToolAnnotations"
        )

    def hints_of(call: ast.Call) -> dict[str, bool]:
        return {kw.arg: bool(getattr(kw.value, "value", True)) for kw in call.keywords if kw.arg}

    # TOOL_ANNOTATIONS reuses five shared constants (_READ, _WRITE_SAFE, ...),
    # so bind constant name -> hints first, then resolve each dict value.
    name_to_hints: dict[str, dict[str, bool]] = {}
    matrix: dict[str, dict[str, bool]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and isinstance(node.value, ast.Call)
            and is_annotations(node.value)
        ):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    name_to_hints[target.id] = hints_of(node.value)
        if (
            isinstance(node, ast.AnnAssign)
            and getattr(node.target, "id", "") == "TOOL_ANNOTATIONS"
            and isinstance(node.value, ast.Dict)
        ):
            for key, val in zip(node.value.keys, node.value.values):
                if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                    continue
                if isinstance(val, ast.Name):
                    matrix[key.value] = dict(name_to_hints.get(val.id, {}))
                elif isinstance(val, ast.Call) and is_annotations(val):
                    matrix[key.value] = hints_of(val)
    return matrix


def _first_sentence(doc: str | None) -> str:
    """One-line summary from the docstring's first prose paragraph.

    Docstrings here open with a bare sentence before any Args/Returns block, so
    the first sentence is the honest one-liner; anything longer bloats the cap.
    """
    if not doc:
        return ""
    flat = " ".join(line.strip() for line in doc.strip().splitlines()).strip()
    flat = re.sub(r"\s+", " ", flat)
    m = re.search(r"^(.*?[.!?])(\s|$)", flat)
    text = (m.group(1) if m else flat).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def summaries(root: Path) -> dict[str, str]:
    """{tool: one-line summary} from each tool's own docstring.

    tool_signatures scans src/ for @mcp.tool, so it also covers the
    coverage-exempt tools; the elements register only carries the 13 already
    disclosed ones, and a missing summary is exactly how a tool goes stale in
    a discovery file.
    """
    out: dict[str, str] = {}
    for tool, sig in sorted(tdqs.tool_signatures(root).items()):
        rel = sig.get("file")
        if not isinstance(rel, str):
            continue
        text = _first_sentence(tdqs.find_tool_docstring(root / rel, tool))
        if text:
            out[tool] = text
    return out


def unsectioned(matrix: dict) -> list:
    """Tools in the registry with no section heading.

    This is the completeness check that makes _SEC safe to hand-maintain: a new
    tool with no section is an ERROR, not a tool that quietly vanishes from the
    generated page while the gate still reports green.
    """
    return sorted(name for name in matrix if name not in _SEC)


def _class_label(hints: dict[str, bool]) -> str:
    if hints.get("destructive_hint"):
        return "destructive"
    if hints.get("read_only_hint"):
        return "read-only"
    return "mutation"


def render(root: Path, matrix: dict[str, dict[str, bool]]) -> str:
    version = ".".join(str(v) for v in (tdqs.project_version(root) or ()))
    summ = summaries(root)
    docs = {
        name: doc_url(root, name)
        for name in (
            "README.md",
            "docs/guide/error-codes.md",
            "docs/guide/session-lifecycle.md",
            "docs/threat-model.md",
        )
    }
    lines: list[str] = [
        "# Maya MCP Server",
        "",
        "> MCP server for Autodesk Maya: spatial awareness, scene audit, camera",
        "> planning, checkpoint/rollback, and asset import. Every scene change",
        "> follows INSPECT -> COMPUTE -> EXECUTE -> VERIFY. This file is",
        f"> generated from the tool registry (v{version}); do not hand-edit it.",
        "",
        "## Docs",
        "",
        f"- [README]({docs['README.md']}): install, transport, and full tool reference",
        f"- [Error codes]({docs['docs/guide/error-codes.md']}): every `[code]`"
        " an agent can see in an isError body",
        f"- [Session lifecycle]({docs['docs/guide/session-lifecycle.md']}): how a"
        " session is established, lost, and cleaned up",
        f"- [Threat model]({docs['docs/threat-model.md']}): what is and is not a security boundary",
        "",
    ]
    for section in _ORDER:
        names = [n for n in matrix if _SEC.get(n) == section]
        if not names:
            continue
        lines.append(f"## {section}")
        lines.append("")
        for name in sorted(names):
            hint = _class_label(matrix[name])
            if matrix[name].get("open_world_hint"):
                hint += ", open-world"
            desc = summ.get(name) or "no docstring summary"
            lines.append(f"- [{name}](#{name}): {hint} - {desc}")
        lines.append("")
    lines.append("## Scope note")
    lines.append("")
    lines.append(
        "- `execute_code`, `write_module` and `maya_setup_guide` are escape"
        " hatches: an agent holding one is inside the trust boundary. Operators"
        " can remove them with `MAYA_MCP_DISABLE_ARBITRARY=1`."
    )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="regenerate llms.txt")
    args = parser.parse_args(argv)

    matrix = annotation_matrix(REPO)
    if not matrix:
        print("::error file=pipeline.py::TOOL_ANNOTATIONS is empty - cannot derive llms.txt")
        return 1

    orphans = unsectioned(matrix)
    if orphans:
        for name in orphans:
            print(
                f"::error file={_SEC_SOURCE}::tool '{name}' is in TOOL_ANNOTATIONS "
                f"but has no section heading - add it to _SEC, otherwise it is "
                f"silently dropped from {_LLMS_NAME}"
            )
        return 1

    expected = render(REPO, matrix)

    if args.write:
        LLMS.write_text(expected, encoding="utf-8", newline="\n")
        n = len(expected.splitlines())
        print(f"wrote {LLMS.name}: {n} lines, {len(matrix)} tools (cap {LINE_CAP})")
        if n > LINE_CAP:
            print(f"::error file={LLMS.name}::{n} lines exceeds the {LINE_CAP}-line cap")
            return 1
        return 0

    if not LLMS.exists():
        print(f"::error file={LLMS.name}::missing - run check_llms_txt.py --write")
        return 1

    actual = LLMS.read_text(encoding="utf-8")
    problems: list[str] = []

    # Coverage is asserted BEFORE and independently of the byte comparison.
    # Nesting it inside the equality check made it unreachable in the one case
    # that matters most - a tool added to the registry but to no heading, where
    # render() drops it and therefore produces no diff to detect.
    # Tool rows are "- [name](#name): ..."; the doc links above them use an
    # absolute URL, so requiring the fragment anchor keeps the two apart.
    listed = set(re.findall(r"^- \[(\w+)\]\(#", actual, re.M))
    for name in sorted(set(matrix) - listed):
        problems.append(
            f"::error file={LLMS.name}::tool '{name}' is in TOOL_ANNOTATIONS but not "
            f"listed - run check_llms_txt.py --write"
        )
    for name in sorted(listed - set(matrix)):
        problems.append(
            f"::error file={LLMS.name}::entry '{name}' is listed but not in "
            f"TOOL_ANNOTATIONS - stale row"
        )

    n_lines = len(actual.splitlines())
    if n_lines > LINE_CAP:
        problems.append(
            f"::error file={LLMS.name}::{n_lines} lines exceeds the {LINE_CAP}-line cap "
            f"- shorten summaries rather than shipping a second file"
        )
    if actual != expected and not problems:
        problems.append(
            f"::error file={LLMS.name}::content differs from the derived form "
            f"(class label or summary drifted) - run check_llms_txt.py --write"
        )
    if problems:
        print("\n".join(problems))
        return 1
    print(f"llms.txt OK: {n_lines} lines, {len(matrix)} tools, matches the tool registry.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
