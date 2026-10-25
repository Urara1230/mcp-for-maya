"""Check that tracked docs claim only things the tree can still answer.

One failure domain, two surfaces: a tracked doc asserts something about the
repository, and the repository can be asked whether it is still true.

D1 -- CHANGELOG ``[Unreleased]``, ``Added``/``Changed``/``Fixed`` bullets, per
the Changelog evidence rule in CONTRIBUTING.md:

  D1a  the bullet carries at least one allowed evidence form
  D1b  every pointer it carries resolves (pytest node, ``path::symbol``, path)
  D1c  no bare ``path:line`` sits inside an ``Evidence`` clause
  D1d  a test count next to a suite citation matches the tests that suite defines

D2 -- AGENTS.md and CONTRIBUTING.md:

  D2a  every repo-relative path cited exists, unless the citation is a
       recovery command for a retired file

SCOPE LIMITS, stated so nobody reads more into this than it does:

* Released ``CHANGELOG`` sections are never checked. They are point-in-time
  records and the standing ruling is that historical evidence is annotated,
  not rewritten -- a gate that "corrected" them would be a rewriting machine.
* ``Removed`` bullets are exempt from D1: naming something that no longer
  exists is their entire subject, and the rule in CONTRIBUTING.md governs
  Added/Fixed/Changed only.
* D1b proves a pointer LANDS, not that what it lands on proves the claim. A
  pointer to a real test that asserts nothing relevant passes every rule here.
  That residual is the same one the pin registry discloses, and it is
  disclosed rather than papered over.

Invocation: check_doc_claims.py [repo-root]
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent.parent

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_gate_registry import resolve_pin_node, resolve_pointer  # noqa: E402


# Where D1 looks, and where D2 looks.
UNRELEASED_HEADING = "## [Unreleased]"
RULED_SECTIONS = ("Added", "Changed", "Fixed")
STEERING_FILES = ("AGENTS.md", "CONTRIBUTING.md")

# Paths long enough to be a citation rather than a word about a directory.
CITED_PATH = re.compile(
    r"(?<![\w/.\-])((?:\.github|docs|src|tests|skills|scripts)/[\w./\-]*?"
    r"\.(?:py|md|yaml|yml|json|txt|sh|toml|cfg))"
)
NODE_ID = re.compile(r"(tests/[\w.\-/]+\.py(?:::[\w.\-]+)+)")
SYMBOL_REF = re.compile(r"([\w.\-/]+\.(?:py|yaml|yml|json|md|txt)::[\w.\-]+)")
BARE_PATH_LINE = re.compile(r"([\w.\-/]+\.(?:py|md|yaml|yml|json|txt):[0-9]+)")
SHA_PREFIXED = re.compile(r"[0-9a-f]{7,}:\s*$")
RECOVERY_CMD = re.compile(r"[0-9a-f]{7,}\^?:[\w./\-]+")

# An evidence form is a handle a reader can pull, not a sentence about a file.
PYTEST_NODE = re.compile(r"tests/[\w.\-/]+\.py::[\w.\-]+")
PATH_SYMBOL = re.compile(r"[\w.\-/]+\.(?:py|yaml|yml|json|md|txt)::[\w.\-]+")
SHA_PATH_LINE = re.compile(r"\b[0-9a-f]{7,}:[\w./\-]+:[0-9]+")
EXEC_LINE = re.compile(
    r"(?:uv run |uvx |python3? |pre-commit |but |git )"
    r".*(?:→|->|exit [0-9]|rc[= ]|OK|Passed|passed|clean|registry OK)"
)

# D1d: a count beside a suite citation is a claim about that suite. The phrasing
# varies, so each shape is matched on its own; group 1 is always the number.
COUNT_CLAIMS = (
    re.compile(r"\((\d+) (tests|passed|collected)\)"),
    re.compile(r"(?:collects|defines) (\d+) tests"),
    re.compile(r"[→-]>\s*(\d+) passed"),
)
PATH_REF = re.compile(r"(tests/[\w.\-/]+\.py(?:::[\w.\-]+)?)")


def _evidence_clause(text: str) -> str:
    """Return the part of a bullet that starts at its evidence claim."""
    m = re.search(r"Evidence", text)
    return text[m.start() :] if m else ""


def _iter_bullets(lines: list[str], start: int, stop: int):
    """Yield (section, bullet text) for every bullet in [start, stop).

    A bullet runs until the next bullet, a heading, or a de-dent to column 0
    that is not part of the item. The last case is not hypothetical: this file
    carries a soft wrap that lands `--fix=lf` at column 0, and treating any
    column-0 line as a terminator silently truncates the bullet before its own
    Evidence clause -- a checker that reads half a claim passes it by accident.
    """
    section = ""
    current: list[str] | None = None
    for raw in lines[start:stop]:
        if raw.startswith("### "):
            if current is not None:
                yield section, "\n".join(current)
                current = None
            section = raw[4:].strip()
            continue
        if raw.startswith("## ") or raw.startswith("- ") or raw.startswith("#"):
            if current is not None:
                yield section, "\n".join(current)
                current = None
            if raw.startswith("- "):
                current = [raw]
            continue
        if current is not None:
            current.append(raw)
    if current is not None:
        yield section, "\n".join(current)


def _check_pointers(root: Path, text: str, where: str) -> list[str]:
    """D1b: every cited handle must land on something in the tree."""
    findings = []
    for node in sorted(set(NODE_ID.findall(text))):
        reason = resolve_pin_node(root, node)
        if reason:
            findings.append(f"{where}: evidence pointer {node}: {reason}")
    for ref in sorted(set(SYMBOL_REF.findall(text))):
        if ref.startswith("tests/") and ref in set(NODE_ID.findall(text)):
            continue
        reason = resolve_pointer(root, ref)
        if reason:
            findings.append(f"{where}: evidence pointer {ref}: {reason}")
    symbols = set(SYMBOL_REF.findall(text))
    for path in sorted(set(CITED_PATH.findall(text))):
        if any(s == f"{path}::{s.split('::')[-1]}" for s in symbols) or any(
            s.startswith(f"{path}::") for s in symbols
        ):
            continue
        if not (root / path).exists():
            findings.append(f"{where}: cited path {path} does not exist")
    return findings


def _ast_test_count(path: Path, parts: list[str]) -> int | None:
    """Count the tests a citation covers, the way pytest would see them.

    ``parts`` is the ``::``-tail of the citation: no parts means the whole file,
    one part is either a test class (count its methods) or a single test, and a
    deeper tail names one test. Returns None when the answer cannot be known
    statically -- a file using ``parametrize`` collects more items than it
    defines, and a checker that guesses there cries wolf on honest claims, so it
    declines instead.
    """
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, ValueError, SyntaxError):
        return None

    def methods(class_node: ast.ClassDef) -> int:
        return sum(
            1
            for sub in class_node.body
            if isinstance(sub, ast.FunctionDef) and sub.name.startswith("test_")
        )

    if parts and parts[-1].startswith("test_"):
        return 1
    if "parametrize" in source:
        return None

    if parts:
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == parts[0]:
                return methods(node)
        return None

    total = 0
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            total += 1
        elif isinstance(node, ast.ClassDef):
            total += methods(node)
    return total


def _check_counts(root: Path, text: str, where: str) -> list[str]:
    """D1d: a count beside a suite citation must match what that suite defines."""
    findings = []
    flat = re.sub(r"\s+", " ", text)
    for pattern in COUNT_CLAIMS:
        for m in pattern.finditer(flat):
            claimed = int(m.group(1))
            groups = m.groups()
            label = groups[1] if len(groups) > 1 else "tests"
            refs = PATH_REF.findall(flat[: m.start()])
            if not refs:
                continue
            rel, _, tail = refs[-1].partition("::")
            path = root / rel
            if not path.is_file():
                continue  # D1b already reports the missing file.
            parts = [p for p in tail.split("::") if p]
            actual = _ast_test_count(path, parts)
            if actual is None:
                continue
            if claimed != actual:
                findings.append(
                    f"{where}: {refs[-1]} is claimed to hold {claimed} "
                    f"({label}), it defines {actual}"
                )
    return findings


def check_changelog(root: Path) -> list[str]:
    """D1: the unreleased lane must describe only what the tree still holds."""
    changelog = root / "CHANGELOG.md"
    if not changelog.is_file():
        return [f"CHANGELOG.md is missing from {root}"]
    lines = changelog.read_text(encoding="utf-8").split("\n")

    starts = [i for i, line in enumerate(lines) if line.startswith(UNRELEASED_HEADING)]
    if not starts:
        return []
    start = starts[0]
    stops = [i for i in range(start + 1, len(lines)) if lines[i].startswith("## [")]
    stop = stops[0] if stops else len(lines)

    findings = []
    for section, text in _iter_bullets(lines, start + 1, stop):
        if section not in RULED_SECTIONS:
            continue
        where = f"CHANGELOG.md [{section}] {text.splitlines()[0][2:52].strip()}"
        # Soft-wrapped markdown must not cost a bullet its evidence form, so the
        # pattern match runs on flattened text. This is a loosening and is
        # disclosed below rather than passed off as precision: a command and a
        # result in the same bullet count even when they sit in different
        # sentences.
        flat = re.sub(r"\s+", " ", text)

        forms = [
            name
            for name, rx in (
                ("pytest node", PYTEST_NODE),
                ("path::symbol", PATH_SYMBOL),
                ("sha:path:line", SHA_PATH_LINE),
                ("executed command", EXEC_LINE),
            )
            if rx.search(flat)
        ]
        if not forms:
            findings.append(
                f"{where}: carries no allowed evidence form "
                "(pytest node, path::symbol, sha:path:line, or an executed "
                "command with its observed result)"
            )

        findings.extend(_check_pointers(root, flat, where))
        findings.extend(_check_counts(root, text, where))

        clause = _evidence_clause(flat)
        for m in BARE_PATH_LINE.finditer(clause):
            before = clause[: m.start()]
            if SHA_PREFIXED.search(before[-24:]):
                continue
            findings.append(
                f"{where}: evidence clause cites {m.group(1)!r}, a bare path:line "
                "that drifts on every edit"
            )
    return findings


def check_steering(root: Path) -> list[str]:
    """D2a: a rule file must not send a reader to a path that is gone."""
    findings = []
    for name in STEERING_FILES:
        doc = root / name
        if not doc.is_file():
            findings.append(f"{name} is missing from {root}")
            continue
        text = doc.read_text(encoding="utf-8")
        for path in sorted(set(CITED_PATH.findall(text))):
            if (root / path).exists():
                continue
            # A recovery command for a retired file is an annotation, not an
            # instruction to open it.
            if any(path in cmd for cmd in RECOVERY_CMD.findall(text)):
                continue
            findings.append(f"{name}: cites {path}, which does not exist")
    return findings


def check(root: Path) -> list[str]:
    return check_changelog(root) + check_steering(root)


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else REPO
    findings = check(root)
    for finding in findings:
        print(f"::error::{finding}")
    if findings:
        print(f"doc-claims check failed: {len(findings)} finding(s)")
        return 1
    print("doc-claims OK: [Unreleased] pointers resolve and the rule files name live paths")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
