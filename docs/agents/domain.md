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

Retiring ids from comments and docstrings is invisible to the whole gate set — ruff, monolith, TDQS, `llms.txt`, mypy, `pre-commit` and pytest all pass on a migration that silently broke a sentence or moved code. Run these two against the ticket's baseline before the suite; both take seconds.

```bash
# 1. no executable token moved: blank every docstring, compare the AST per file
python - <<'EOF'
import ast, subprocess
BASE, TIP = "<baseline>", "<tip>"
paths = [p for p in subprocess.run(["git", "diff", "--name-only", f"{BASE}...{TIP}"],
           capture_output=True, text=True).stdout.split() if p.endswith(".py")]
def shape(rev, p):
    t = ast.parse(subprocess.run(["git", "show", f"{rev}:{p}"], capture_output=True).stdout.decode())
    for n in ast.walk(t):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            b = n.body
            if b and isinstance(b[0], ast.Expr) and isinstance(getattr(b[0].value, "value", None), str):
                b[0].value.value = ""
    return ast.dump(t)
for p in paths:
    if shape(BASE, p) != shape(TIP, p):
        print("CODE MOVED:", p)
EOF

# 2. every comment/docstring block that was balanced still is
python - <<'EOF'
import io, subprocess, tokenize
OPEN = {"(": ")", "[": "]", "{": "}"}
BASE, TIP = "<baseline>", "<tip>"
paths = [p for p in subprocess.run(["git", "diff", "--name-only", f"{BASE}...{TIP}"],
           capture_output=True, text=True).stdout.split() if p.endswith(".py")]
def balanced(rev, p):
    src = subprocess.run(["git", "show", f"{rev}:{p}"], capture_output=True).stdout.decode()
    n = 0
    for t in tokenize.generate_tokens(io.StringIO(src).readline):
        if t.type not in (tokenize.COMMENT, tokenize.STRING):
            continue
        if t.type == tokenize.STRING and "\n" not in t.string:
            continue                      # only a multi-line block can straddle an edit
        s = t.string
        if sum(map(s.count, OPEN)) == sum(map(s.count, OPEN.values())):
            n += 1
    return n
for p in paths:
    b, a = balanced(BASE, p), balanced(TIP, p)
    if a < b:
        print(f"UNBALANCED: {p} {b} -> {a}")
EOF
```

Check 2 exists because a per-line scan cannot find the defect it catches: deleting `(D-018: ` from
the first line of a docstring strands the `)` on the second, and that line was never edited. It
flagged `src/maya_mcp_server/pipeline.py` (`48 -> 47`) on the commit that did exactly that, and
stayed silent on every tip after it was repaired. A deletion that leaves a block *balanced* —
swapping which pair closes — is invisible to it and to any check: read the changed sentences.
