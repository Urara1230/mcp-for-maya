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

- UNBALANCED (blocks the merge) — the file gained a docstring or comment block
  whose delimiters are unmatched or mis-nested. A stack decides, not
  opener/closer totals, so `([)]` is a finding. Only an *increase* counts: the
  opposite metric — "balanced blocks must not fall" — fires on every migration,
  because removing a balanced parenthetical is how the migration works. A block
  with no delimiters at all is never counted, so deleting a plain comment cannot
  move the number.
- CODE MOVED (warning) — the ASTs differ once every docstring is *removed* (not
  blanked: a migration may delete one outright). String literals stay in the AST,
  which is correct: an injected-module template is payload, not prose. Most PRs
  exist to move code, so this one informs rather than blocks; a migration ticket
  carries "no code moved" as its own acceptance criterion, and the warning line
  is where a reviewer reads it. Making it a hard gate was tried first and would
  have reddened every feature branch — including the PR that added this file.

Scope limits, stated so nobody reads more into this than it does:

- It compares counts, not identities: one block fixed while another breaks nets
  nothing, so a finding is read, not obeyed blindly.
- A parenthetical legitimately spanning two comment lines reads as two bad blocks.
- It checks *modified* files only (`--diff-filter=M`): a blob that exists on one
  side has nothing to compare. A migration adds and deletes no Python file — that
  is its own acceptance criterion — so a PR adding a module is unchecked here.
- Both the file list and the blobs come from the merge base of the two revisions,
  so a branch that has fallen behind its base cannot be blamed for main's edits.
  (The assets guard diffs a push two-dot instead; the difference matters here
  because a merge to main brings unrelated file edits into a naive range.)
- An all-zero or empty base (first push of a branch) skips the check: there is no
  prior state to compare. A shallow clone reports a warning rather than passing
  silently, because "nothing modified" and "history absent" print the same way.
- It proves shape, not sense. Whether the surviving sentence still states the
  decision it inherited is a reader's call; see docs/agents/domain.md.

Invocation: check_migration_shape.py <base-rev> <tip-rev> [repo-root]
Exits 1 on a prose finding, an unparseable side, or a git command that fails;
CODE MOVED prints as ::warning:: and does not affect the exit code.
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
    """(ok, errors, warnings) for (path, base_src, tip_src) triples.

    Only the prose rules block: an unparseable side, or more docstring/comment
    blocks with bad delimiters than the range started with. CODE MOVED is a
    warning, because moving code is what most PRs exist to do — the gate that
    made it an error would have reddened every feature branch, including the one
    that introduced it. A migration ticket still owes "no code moved" as its own
    acceptance criterion, and the warning is where a reviewer sees it.

    A file whose code moved is still checked for prose: new code often arrives
    with new sentences, and a stranded delimiter is just as broken there.
    """
    errors: list[str] = []
    warnings: list[str] = []
    for path, base_src, tip_src in pairs:
        moved = code_moved(base_src, tip_src)
        if moved is None:
            errors.append(f"::error file={path}::UNPARSEABLE: an AST will not parse in this range")
            continue
        if moved:
            warnings.append(
                f"::warning file={path}::CODE MOVED: the docstring-stripped AST differs"
            )
        before, after = len(bad_blocks(base_src)), len(bad_blocks(tip_src))
        if after > before:
            errors.append(
                f"::error file={path}::UNBALANCED: bad delimiter blocks {before} -> {after}"
            )
    return not errors, errors, warnings


def python_paths(runner: Callable[..., object], base: str, tip: str, root: Path) -> list[str]:
    """Modified Python files only, and a failing git call is allowed to raise."""
    result = runner(
        ["git", "diff", "--name-only", "--diff-filter=M", f"{base}...{tip}"],
        capture_output=True,
        text=True,
        check=True,
        cwd=root,
    )
    return [p for p in result.stdout.split() if p.endswith(".py")]  # type: ignore[attr-defined]


def merge_base(runner: Callable[..., object], base: str, tip: str, root: Path) -> str:
    """The left side for BOTH the file list and the blobs.

    ``git diff base...tip`` selects files from the merge base, so reading the
    blobs from ``base`` itself would compare against a commit the file list never
    referred to: on a PR branch that has fallen behind, files that main changed
    meanwhile would read as CODE MOVED in a PR that never touched them.
    """
    result = runner(
        ["git", "merge-base", base, tip],
        capture_output=True,
        text=True,
        check=True,
        cwd=root,
    )
    return result.stdout.strip()  # type: ignore[attr-defined]


def blob(runner: Callable[..., object], root: Path, rev: str, path: str) -> str:
    return runner(
        ["git", "show", f"{rev}:{path}"],
        capture_output=True,
        check=True,
        cwd=root,
    ).stdout.decode("utf-8")


def is_shallow(runner: Callable[..., object], root: Path) -> bool:
    result = runner(
        ["git", "rev-parse", "--is-shallow-repository"],
        capture_output=True,
        text=True,
        check=True,
        cwd=root,
    )
    return result.stdout.strip() == "true"  # type: ignore[attr-defined]


def main(argv: Sequence[str], root: Path = REPO, runner: Callable = subprocess.run) -> int:
    if len(argv) != 2:
        print("usage: check_migration_shape.py <base-rev> <tip-rev> [repo-root]")
        return 1
    base, tip = argv
    if not base or set(base) == {"0"}:
        # First push of a branch: the caller's base is an all-zero SHA, and there
        # is no prior state to compare.
        print(f"no base ref ({base or 'empty'}) — skipped")
        return 0
    try:
        left = merge_base(runner, base, tip, root)
        paths = python_paths(runner, left, tip, root)
    except (subprocess.CalledProcessError, OSError) as e:
        print(f"::error::git could not resolve {base}...{tip}: {e}")
        return 1
    if not paths:
        print(f"no Python files modified between {left[:12]} and {tip[:12]} — nothing to compare")
        try:
            if is_shallow(runner, root):
                print(
                    "::warning::this clone is shallow, so the empty range may be history, not peace"
                )
        except (subprocess.CalledProcessError, OSError):
            pass
        return 0
    try:
        pairs = [(p, blob(runner, root, left, p), blob(runner, root, tip, p)) for p in paths]
    except (subprocess.CalledProcessError, OSError) as e:
        print(f"::error::git show failed: {e}")
        return 1
    ok, errors, warnings = check(pairs)
    for w in warnings:
        print(w)
    for e in errors:
        print(e)
    if ok:
        moved = sum(1 for w in warnings if "CODE MOVED" in w)
        print(
            f"{len(paths)} files: prose intact"
            + (f", {moved} with code moved (advisory)" if moved else "")
        )
    return 0 if ok else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 3:
        sys.exit(main(args[:2], Path(args[2])))
    sys.exit(main(args))
