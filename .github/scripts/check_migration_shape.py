"""Comment-migration shape gate: no code moved, no prose broken.

`#84` retires bare `D-xxx` decision-ledger citations from comments and
docstrings (`src/` in #90, `.github/scripts/` in #92). Two failure modes are
invisible to every other gate in this repo:

- deleting `(D-018: ` from a docstring's first line strands the `)` on the
  second line, and that second line is never edited, so no per-line scan finds
  it;
- an edit that reads like prose can move code, because a docstring is data and
  injected-module source lives in string literals.

ruff budget, the monolith line budget, TDQS, `llms.txt`, version consistency,
the gate registry, the mypy baseline, `pre-commit` and the stub suite all stayed
green on the commit that did the first one (#90, caught in review, not by a
check). This gate is the response.

Two rules, per Python file modified between two revisions:

- CODE MOVED — the ASTs differ once every docstring is *removed* (not blanked:
  a migration may delete one outright). String literals stay in the AST, which
  is correct: an injected-module template is payload, not prose.
- UNBALANCED — the file gained a docstring or comment block whose delimiters are
  unmatched or mis-nested. A stack decides, not opener/closer totals, so `([)]`
  is a finding. Only an *increase* counts: the opposite metric — "balanced
  blocks must not fall" — fires on every migration, because removing a balanced
  parenthetical is how the migration works. A block with no delimiters at all is
  never counted, so deleting a plain comment cannot move the number.

Scope limits, stated so nobody reads more into this than it does:

- It compares counts, not identities: one block fixed while another breaks nets
  nothing.
- A parenthetical legitimately spanning two comment lines reads as two bad
  blocks, so every finding is read, not obeyed blindly.
- It checks *modified* files only (`--diff-filter=M`): a blob that exists on one
  side has nothing to compare. A migration adds and deletes no Python file —
  that is its own acceptance criterion, not this gate's business.
- It proves shape, not sense. Whether the surviving sentence still states the
  decision it inherited is a reader's call; see docs/agents/domain.md.

Invocation: check_migration_shape.py <base-rev> <tip-rev> [repo-root]
Exits 1 on a finding, on an unparseable side, or when a git command fails.
"""

from __future__ import annotations

import ast
import io
import subprocess
import sys
import tokenize
from collections.abc import Callable, Sequence
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent.parent
PAIRS = {"(": ")", "[": "]", "{": "}"}
CLOSERS = {v: k for k, v in PAIRS.items()}
NODES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def is_misnested(text: str) -> bool:
    """True when delimiters are unmatched or mis-nested; `([)]` counts."""
    stack: list[str] = []
    for ch in text:
        if ch in PAIRS:
            stack.append(PAIRS[ch])
        elif ch in CLOSERS:
            if not stack or ch != stack.pop():
                return True
    return bool(stack)


def prose_blocks(src: str) -> list[str]:
    """Docstrings (from the AST, so a one-line docstring is not skipped) and
    comment lines. Deliberately not other string literals."""
    tree = ast.parse(src)
    found = [
        d
        for n in ast.walk(tree)  # ast.walk yields the root: do not seed it again
        if isinstance(n, NODES) and (d := ast.get_docstring(n))
    ]
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            found.append(tok.string)
    return found


def bad_blocks(src: str) -> list[str]:
    """Docstring/comment blocks whose delimiters are unmatched or mis-nested."""
    return [b for b in prose_blocks(src) if is_misnested(b)]


def code_moved(base_src: str, tip_src: str) -> bool | None:
    """(base, tip) AST equality with docstrings removed. None = unparseable."""

    def shape(src: str) -> str:
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, NODES):
                body = node.body
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(getattr(body[0].value, "value", None), str)
                ):
                    body.pop(0)
        return ast.dump(tree)

    try:
        return shape(base_src) != shape(tip_src)
    except (SyntaxError, ValueError):
        return None


def check(pairs: Sequence[tuple[str, str, str]]) -> tuple[bool, list[str], list[str]]:
    """(ok, errors, warnings) for (path, base_src, tip_src) triples."""
    errors: list[str] = []
    for path, base_src, tip_src in pairs:
        moved = code_moved(base_src, tip_src)
        if moved is None:
            errors.append(f"::error file={path}::UNPARSEABLE: an AST will not parse in this range")
            continue
        if moved:
            errors.append(f"::error file={path}::CODE MOVED: the docstring-stripped AST differs")
            continue
        try:
            before, after = len(bad_blocks(base_src)), len(bad_blocks(tip_src))
        except (SyntaxError, ValueError) as e:
            errors.append(f"::error file={path}::UNPARSEABLE: cannot tokenize: {e}")
            continue
        if after > before:
            errors.append(
                f"::error file={path}::UNBALANCED: bad delimiter blocks {before} -> {after}"
            )
    return not errors, errors, []


def python_paths(runner: Callable[..., object], base: str, tip: str) -> list[str]:
    """Modified Python files only, and a failing git call is allowed to raise."""
    result = runner(
        ["git", "diff", "--name-only", "--diff-filter=M", f"{base}...{tip}"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    )
    return [p for p in result.stdout.split() if p.endswith(".py")]  # type: ignore[attr-defined]


def sources(root: Path, rev: str, path: str) -> str:
    return subprocess.run(
        ["git", "show", f"{rev}:{path}"],
        capture_output=True,
        check=True,
        cwd=root,
    ).stdout.decode("utf-8")


def main(argv: Sequence[str], root: Path = REPO) -> int:
    if len(argv) != 2:
        print("usage: check_migration_shape.py <base-rev> <tip-rev> [repo-root]")
        return 1
    base, tip = argv
    try:
        paths = python_paths(subprocess.run, base, tip)
    except (subprocess.CalledProcessError, OSError) as e:
        print(f"::error::git diff {base}...{tip} failed: {e}")
        return 1
    if not paths:
        print(f"no Python files modified between {base} and {tip} — nothing to compare")
        return 0
    try:
        pairs = [(p, sources(root, base, p), sources(root, tip, p)) for p in paths]
    except (subprocess.CalledProcessError, OSError) as e:
        print(f"::error::git show failed: {e}")
        return 1
    ok, errors, _warnings = check(pairs)
    for e in errors:
        print(e)
    if ok:
        print(f"{len(paths)} files: no code moved, no prose broken")
    return 0 if ok else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 3:
        sys.exit(main(args[:2], Path(args[2])))
    sys.exit(main(args))
