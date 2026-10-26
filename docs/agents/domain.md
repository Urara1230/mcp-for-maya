# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`GLOSSARY.md`** at the repo root: the domain vocabulary.
- Decisions live where they are used: read the comments at the code location and the linked GitHub issue. There is no ADR directory.

## File structure

Single-context repo (this repo):

```
/
├── GLOSSARY.md
├── docs/agents/
└── src/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `GLOSSARY.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Record a decision

Open decisions are GitHub issues labelled `needs-triage`, not new documents. A decision that is already implemented belongs in a comment at the code that enforces it.

## Reading the retired decision ledger

`docs/decision-ledger.md` was deleted in the governance teardown; `AGENTS.md` carries the recovery command. Anything citing a `D-xxx` points into that table, so:

- One row per decision: `ID | 原问题 | 我的原回答原文 | 规范化需求 | 显式约束/负向需求 | 状态`. `规范化需求` holds what was decided, `显式约束/负向需求` what was ruled out — a bare id in code usually means the first, a 不要 clause the second.
- `D-082⑥`, `D-082a`, `D-110⑦/⑧`, `D-207 1`, `D-145β` are clauses **inside** the parent row, not rows of their own: search for the parent id.
- `状态` grades the row: `current`, `revised`, or `revised→D-nnn` (that later row supersedes it). Debt rows carry their own stamp inline, e.g. `触发态（2026-09-27 核验）=否-未实测`.

## Checks a ledger migration owes

Retiring ids from comments and docstrings is invisible to the whole gate set — ruff, monolith, TDQS, `llms.txt`, mypy, `pre-commit` and pytest all pass on a migration that silently broke a sentence or moved code. Set the two revisions, then run each block; both take seconds, and both exit loudly on a bad revision rather than validating nothing.

```bash
BASE=$(git rev-parse origin/main) TIP=$(git rev-parse HEAD)

# 1. no executable token moved: drop every docstring, compare the AST per file
python - "$BASE" "$TIP" <<'EOF'
import ast, subprocess, sys
if len(sys.argv) < 3:
    sys.exit("usage: python - <base> <tip>")
BASE, TIP = sys.argv[1], sys.argv[2]
def git(*args, **kw):
    return subprocess.run(["git", *args], capture_output=True, check=True, **kw)
# Modified files only: a migration adds and deletes none, and a blob that does not
# exist on both sides has nothing to compare.
paths = [p for p in git("diff", "--name-only", "--diff-filter=M", f"{BASE}...{TIP}",
                        text=True).stdout.split() if p.endswith(".py")]
def git_text(rev, p):
    return git("show", f"{rev}:{p}").stdout.decode()
def shape(src):
    t = ast.parse(src)
    for n in ast.walk(t):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            b = n.body
            if b and isinstance(b[0], ast.Expr) and isinstance(getattr(b[0].value, "value", None), str):
                b.pop(0)          # removed, not blanked: a migration may delete one outright
    return ast.dump(t)
def code_moved(p):
    try:
        return shape(git_text(BASE, p)) != shape(git_text(TIP, p))
    except SyntaxError:
        return None               # an unparseable side is reported, not guessed at
flagged = {p: code_moved(p) for p in paths}
bad = [p for p, v in flagged.items() if v is None]
moved = [p for p, v in flagged.items() if v is True]
for p in sorted(bad):
    print("UNPARSEABLE:", p)
for p in sorted(moved):
    print("CODE MOVED:", p)
sys.exit(1 if bad or moved else 0)
EOF

# 2. no comment/docstring block gained an unmatched or mis-nested delimiter
python - "$BASE" "$TIP" <<'EOF'
import ast, io, subprocess, sys, tokenize
if len(sys.argv) < 3:
    sys.exit("usage: python - <base> <tip>")
PAIRS = {"(": ")", "[": "]", "{": "}"}
BASE, TIP = sys.argv[1], sys.argv[2]
def git(*args, **kw):
    return subprocess.run(["git", *args], capture_output=True, check=True, **kw)
paths = [p for p in git("diff", "--name-only", "--diff-filter=M", f"{BASE}...{TIP}",
                        text=True).stdout.split() if p.endswith(".py")]
def broken(text):
    """True when delimiters are unmatched or mis-nested -- `([)]` counts, counts alone miss it."""
    stack = []
    for ch in text:
        if ch in PAIRS:
            stack.append(PAIRS[ch])
        elif ch in PAIRS.values():
            if not stack or ch != stack.pop():
                return True
    return bool(stack)
def blocks(src):
    """Docstrings (from the AST, any length) and comment lines. Not string literals:
    an injected-module template is code, not prose, and belongs to check 1."""
    tree = ast.parse(src)
    found = [d for n in [tree, *ast.walk(tree)]
             if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
             and (d := ast.get_docstring(n))]
    for t in tokenize.generate_tokens(io.StringIO(src).readline):
        if t.type == tokenize.COMMENT:
            found.append(t.string)
    return found
def unbalanced(rev, p):
    src = git("show", f"{rev}:{p}").stdout.decode()
    return sum(1 for b in blocks(src) if broken(b))
scored, bad = [], []
for p in paths:
    try:
        scored.append((p, unbalanced(BASE, p), unbalanced(TIP, p)))
    except SyntaxError:
        bad.append(p)
for p in sorted(bad):
    print("UNPARSEABLE:", p)
broken_files = [(p, b, a) for p, b, a in scored if a > b]
for p, b, a in broken_files:
    print(f"UNBALANCED: {p} {b} -> {a}")
sys.exit(1 if broken_files or bad else 0)
EOF
```

Check 2 exists because a per-line scan cannot find the defect it catches: deleting `(D-018: ` from
the first line of a docstring strands the `)` on the second, and that second line is never edited.
It counts docstrings and comment lines whose delimiters are **unmatched or mis-nested** — a stack,
so `([)]` is a finding rather than a balance — and flags only an *increase*: the opposite test,
"balanced blocks must not fall", fires on every migration because removing a parenthetical is
how the migration works. Measured on `pipeline.py`: `2 -> 4` bad blocks on the commit that
stranded the `)`, back to `2` on every tip after it was repaired.

What it cannot see: it compares counts, not identities, so one block fixed while another breaks
nets nothing; a parenthetical legitimately spanning two comment lines reads as two bad blocks, so
every hit is read, not obeyed; and delimiters inside string literals are check 1's territory, since
those are code. Both blocks exit 1 on a finding, and compare files **modified** in the range: a
migration adds and deletes no file (that is a ticket's own acceptance criterion), and a blob that
exists on only one side has nothing to compare against — a crash, not a verdict.
