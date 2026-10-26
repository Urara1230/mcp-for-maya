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
def shape(rev, p):
    t = ast.parse(git("show", f"{rev}:{p}").stdout.decode())
    for n in ast.walk(t):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            b = n.body
            if b and isinstance(b[0], ast.Expr) and isinstance(getattr(b[0].value, "value", None), str):
                b.pop(0)          # removed, not blanked: a migration may delete one outright
    return ast.dump(t)
moved = [p for p in paths if shape(BASE, p) != shape(TIP, p)]
for p in moved:
    print("CODE MOVED:", p)
sys.exit(1 if moved else 0)
EOF

# 2. no comment/docstring block gained an unmatched delimiter
python - "$BASE" "$TIP" <<'EOF'
import io, subprocess, sys, tokenize
if len(sys.argv) < 3:
    sys.exit("usage: python - <base> <tip>")
OPEN = {"(": ")", "[": "]", "{": "}"}
BASE, TIP = sys.argv[1], sys.argv[2]
def git(*args, **kw):
    return subprocess.run(["git", *args], capture_output=True, check=True, **kw)
paths = [p for p in git("diff", "--name-only", "--diff-filter=M", f"{BASE}...{TIP}",
                        text=True).stdout.split() if p.endswith(".py")]
def unbalanced(rev, p):
    src = git("show", f"{rev}:{p}").stdout.decode()
    return sum(
        1
        for t in tokenize.generate_tokens(io.StringIO(src).readline)
        if t.type in (tokenize.COMMENT, tokenize.STRING)
        and (t.type == tokenize.COMMENT or "\n" in t.string)  # only a multi-line block straddles
        and sum(map(t.string.count, OPEN)) != sum(map(t.string.count, OPEN.values()))
    )
broken = [(p, unbalanced(BASE, p), unbalanced(TIP, p)) for p in paths]
broken = [(p, b, a) for p, b, a in broken if a > b]
for p, b, a in broken:
    print(f"UNBALANCED: {p} {b} -> {a}")
sys.exit(1 if broken else 0)
EOF
```

Check 2 exists because a per-line scan cannot find the defect it catches: deleting `(D-018: ` from
the first line of a docstring strands the `)` on the second, and that second line is never edited.
It counts blocks whose delimiters do **not** balance and flags only an *increase* — the opposite
test, "balanced blocks must not fall", fires on every migration because removing a parenthetical is
how the migration works. Measured on `pipeline.py`: `2 -> 3` unbalanced blocks on the commit that
stranded the `)`, back to `2` on every tip after it was repaired. A deletion that leaves a block
*balanced* — swapping which pair closes — is invisible to it, as to any check: read the changed
sentences. Both blocks exit 1 on a finding, and compare files **modified** in the range: a
migration adds and deletes no file (that is a ticket's own acceptance criterion), and a blob that
exists on only one side has nothing to compare against — a crash, not a verdict.
