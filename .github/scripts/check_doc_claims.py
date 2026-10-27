"""Check that tracked docs claim only things the tree can still answer.

One failure domain, two surfaces: a tracked doc asserts something about the
repository, and the repository can be asked whether it is still true.

D1 -- CHANGELOG ``[Unreleased]``, ``Added``/``Changed``/``Fixed`` bullets, per
the Changelog evidence rule in CONTRIBUTING.md:

  D1a  the bullet carries at least one allowed evidence form: a pytest node ID,
       ``path::symbol``, or ``sha:path:line`` -- the three CONTRIBUTING names
  D1b  every pointer it carries resolves (pytest node, ``path::symbol``, path)
  D1c  no bare ``path:line`` is cited anywhere in the bullet
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
* ``sha:path:line`` is accepted as a form and NOT resolved: it names an
  immutable blob, so the checker would have to read git objects to confirm it,
  and that is a different gate. Its merit is exactly that immutability -- a
  claim about a config asset or a workflow step has no symbol to name, and a
  commit-anchored line cannot drift the way a worktree line does.
* "Run this command, here is what it printed" is deliberately NOT a form, even
  though the lane used it. It cannot be resolved by anything, so accepting it
  means the assertion is unfalsifiable at check time; and its weakest instance
  is a ``git grep`` whose only match is the very line citing it. The four
  bullets that relied on it now cite a node, a registry key, or a
  commit-anchored line instead.
* D1d polices a count only in the two shapes it can read -- ``(29 tests)`` and
  ``collects 29 tests``. An arrow result like ``-> exit 0, 17 passed`` is a
  dated measurement of a suite that has since grown; asserting against it
  would red a record the repo has ruled must stay as filed.
* D1d declines a file that uses the ``parametrize`` MARKER, decided from the
  decorator rather than the word, because a docstring mention must not switch
  the rule off -- and this file's own test suite mentions it.

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

# ONE extension list, interpolated everywhere. The first version carried three
# copies, and they diverged: `scripts/setup.sh:12` was existence-checked as a
# cited path yet never scanned as a bare path:line, so the drifting form passed
# precisely for the files whose line numbers move most.
DOC_EXT = r"py|md|yaml|yml|json|txt|sh|toml|cfg"

# Paths long enough to be a citation rather than a word about a directory.
CITED_PATH = re.compile(
    rf"(?<![\w/.\-])((?:\.github|docs|src|tests|skills|scripts)/[\w./\-]*?"
    rf"\.(?:{DOC_EXT}))"
)
NODE_ID = re.compile(r"(tests/[\w.\-/]+\.py(?:::[\w.\-]+)+)")
SYMBOL_REF = re.compile(rf"([\w.\-/]+\.(?:{DOC_EXT})::[\w.\-]+)")
# Group 1 is the sha anchor, OPTIONAL and adjacent -- a bare `path:line` is drift
# bait, a `sha:path:line` names an immutable blob. Anchoring is decided by the
# character right before the path, not by a lookback window: the first version
# scanned 24 preceding chars, so any sha mentioned earlier in the sentence
# quietly excused a bare citation that followed it.
PATH_LINE = re.compile(rf"([0-9a-f]{{7,}}\^?:)?([\w.\-/]+\.(?:{DOC_EXT}):[0-9]+(?:-[0-9]+)?)")
RECOVERY_CMD = re.compile(r"[0-9a-f]{7,}\^?:[\w./\-]+")

# What pytest can collect as a test function, sync or async.
TEST_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)

# An evidence form is a handle a reader can pull, not a sentence about a file.
PYTEST_NODE = re.compile(r"tests/[\w.\-/]+\.py::[\w.\-]+")
PATH_SYMBOL = re.compile(r"[\w.\-/]+\.(?:py|yaml|yml|json|md|txt)::[\w.\-]+")
SHA_PATH_LINE = re.compile(r"\b[0-9a-f]{7,}:[\w./\-]+:[0-9]+")
# D1d: a count beside a suite citation is a claim about that suite. Only these
# two shapes are policed: an arrow-prefixed result ("-> exit 0, 17 passed") is a
# dated measurement of a suite that has since grown, and asserting against it
# would red a record the repo has ruled must stay as filed.
COUNT_CLAIMS = (
    re.compile(r"\((\d+) (tests|passed|collected)\)"),
    re.compile(r"(?:collects|defines|hold[s]?) (\d+) tests"),
)
# Every ``::`` component, not one: the first version stopped after the class, so
# `tests/x.py::TestGroup::test_one` was read as a citation of the class and a
# "(2 tests)" claim beside it passed -- the count was checked against the wrong
# subject, and a wrong claim went green.
PATH_REF = re.compile(r"(tests/[\w.\-/]+\.py(?:::[\w.\-]+)*)")


# A bullet headline is echoed into the finding's location prefix, and the
# [Unreleased] lane still has ids in its headlines. This checker's job is to
# report the bullet, not to republish the id, so the prefix is stripped the same
# way the drift canary strips workflow names.
_LEDGER_TAG = re.compile(r"\s*\([^()]*D-\d+[^()]*\)")


def _strip_ledger_ids(text: str) -> str:
    return _LEDGER_TAG.sub("", text).strip()


def _unwrap_code(text: str) -> str:
    """Join identifiers that markdown wrapped inside a code span.

    A long pytest node ID breaks across lines at column 100 in this file.
    Flattening alone turns ``...::test_the_real_\\n  repo_is_clean`` into a node
    with a space in it, and the checker then reports a perfectly good pointer as
    dead: a false red aimed at whoever wrote the longest line.
    """
    out = []
    for part in re.split(r"(`[^`]*`)", text):
        out.append(re.sub(r"\n\s*", "", part) if part.startswith("`") else part)
    return "".join(out)


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
        # Same relief the rule files get: a Changed bullet may name the artifact
        # it retired, so long as it hands the reader a way to recover it.
        if any(path in cmd for cmd in RECOVERY_CMD.findall(text)):
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

    def is_test(node: ast.AST) -> bool:
        # pytest collects coroutines too, and this suite has async tests in 12
        # files: counting only def-test_x under-reports the real collected size.
        return isinstance(node, TEST_DEFS) and node.name.startswith("test_")

    def marks(node: ast.AST) -> bool:
        # A DECORATOR, not the word: the first version searched the whole source
        # as a string, and this very file's own test docstrings mention
        # parametrize -- so the count rule silently stopped applying to the
        # gate's own evidence.
        return any("parametrize" in ast.unparse(dec) for dec in node.decorator_list)

    def methods(class_node: ast.ClassDef) -> int:
        return sum(1 for sub in class_node.body if is_test(sub))

    def find_class(name: str) -> ast.ClassDef | None:
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == name:
                return node
        return None

    # Decide what the citation covers before counting anything: a parametrized
    # test collects more items than it defines, so the count must decline rather
    # than report the def-site number as a mismatch. This runs FIRST because the
    # single-test shortcut used to return 1 here, redding a correct "3 tests"
    # claim before the decline could ever run.
    def covered(nodes: list[ast.AST]) -> bool:
        return any(marks(n) for n in nodes if isinstance(n, (*TEST_DEFS, ast.ClassDef)))

    if not parts:
        if covered([n for n in ast.walk(tree)]):
            return None
    else:
        cls = find_class(parts[0])
        if cls is None:
            # one part naming a module-level test function
            if covered([n for n in tree.body if getattr(n, "name", None) == parts[-1]]):
                return None
        else:
            # `::Class`, or `::Class::test`: the class is in scope either way,
            # and a method-level marker counts as much as a class-level one.
            inside = [sub for sub in cls.body if getattr(sub, "name", None) == parts[-1]]
            if covered([cls, *cls.body] if len(parts) == 1 else [cls, *inside]):
                return None

    if parts and parts[-1].startswith("test_"):
        return 1

    if parts:
        cls = find_class(parts[0])
        return methods(cls) if cls is not None else None

    total = 0
    for node in tree.body:
        if is_test(node):
            total += 1
        elif isinstance(node, ast.ClassDef):
            total += methods(node)
    return total


def _check_counts(root: Path, text: str, where: str) -> list[str]:
    """D1d: a count beside a suite citation must match what that suite defines."""
    findings = []
    flat = re.sub(r"\s+", " ", _unwrap_code(text))
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
            # The claim names its own subject. "the suite collects 29 tests"
            # counts the file even when one node was cited just before it,
            # because reading that as "this node holds 29" invents a claim the
            # prose never made -- and it reds honest entries into the bargain.
            # Only a trailing TEST FUNCTION is dropped: "the class collects 11"
            # still means that class, not the whole file.
            context = flat[max(0, m.start() - 40) : m.end()]
            suite_level = "collect" in context.lower() or "suite" in context.lower()
            if suite_level and parts and parts[-1].startswith("test_"):
                parts = parts[:-1]
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
        headline = _strip_ledger_ids(text.splitlines()[0][2:52].strip())
        where = f"CHANGELOG.md [{section}] {headline}"
        # Soft-wrapped markdown must not cost a bullet its evidence form, so the
        # pattern match runs on flattened text. This is a loosening and is
        # disclosed below rather than passed off as precision: a command and a
        # result in the same bullet count even when they sit in different
        # sentences.
        flat = re.sub(r"\s+", " ", _unwrap_code(text))

        forms = [
            name
            for name, rx in (
                ("pytest node", PYTEST_NODE),
                ("path::symbol", PATH_SYMBOL),
                ("sha:path:line", SHA_PATH_LINE),
            )
            if rx.search(flat)
        ]
        if not forms:
            findings.append(
                f"{where}: carries no allowed evidence form "
                "(pytest node, path::symbol, or sha:path:line)"
            )

        findings.extend(_check_pointers(root, flat, where))
        findings.extend(_check_counts(root, text, where))

        # Whole bullet, not just the clause: a bare `path:line` is a drifting
        # pointer wherever it is cited, and scoping to the Evidence sentence left
        # the docstring promising more coverage than the code delivered.
        for m in PATH_LINE.finditer(flat):
            anchor, citation = m.group(1), m.group(2)
            if anchor:
                continue  # sha-anchored: names an immutable blob, cannot drift
            findings.append(
                f"{where}: cites {citation!r} as a bare path:line, which drifts on "
                "every edit -- anchor it to a commit or name a symbol"
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
