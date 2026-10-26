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

`docs/decision-ledger.md` was deleted in the governance teardown — read it with `git show '1c5befe^:docs/decision-ledger.md'`, the command `AGENTS.md` names too. Anything citing a `D-xxx` points into that table, so:

- One row per decision: `ID | 原问题 | 我的原回答原文 | 规范化需求 | 显式约束/负向需求 | 状态`. `规范化需求` holds what was decided, `显式约束/负向需求` what was ruled out — a bare id in code usually means the first, a 不要 clause the second.
- A citation like `D-082⑥`, `D-110⑧` or `D-145β` names a clause **inside** the parent row: the table marks each row's items `①②…` and letters like `β`, so search the parent id and read the marker in place. The forms code used are sometimes shorthand rather than table tokens — `D-082a` and `D-207 1` occur zero times in the ledger (they mean `①` of their rows, and `D-207` runs `①…⑦`), while `D-082⑥` occurs 3 times and `D-145β` 6. Grep the parent id, not the citation.
- `状态` grades the row: `current`, `revised`, or `revised→D-nnn` (that later row supersedes it). Debt rows carry their own stamp inline, e.g. `触发态（2026-09-27 核验）=否-未实测`.

## Checks a ledger migration owes

Retiring ids from comments and docstrings is invisible to the rest of the gate set. On the commit where `(D-018: ` was deleted from a docstring's first line, stranding its `)` on a second line the edit never touched, ruff budget, the monolith line budget, TDQS, `llms.txt`, version consistency, gate-registry, mypy, `pre-commit` and the whole stub suite were green.

`.github/scripts/check_migration_shape.py <base-rev> <tip-rev>` enforces two rules per Python file modified in the range, and runs in the `lint` job:

- **CODE MOVED** — the AST differs once every docstring is *removed* (not blanked: a migration may delete one outright). String literals stay inside the comparison, which is right — an injected-module template is payload, not prose.
- **UNBALANCED** — the file gained a docstring or comment block whose delimiters are unmatched or mis-nested. A stack decides, so `([)]` is a finding, and only an *increase* counts: the opposite test, "balanced blocks must not fall", fires on every migration because removing a parenthetical is how the migration works.

Revisions are arguments and both git calls check their return code; pasted with placeholders unfilled, the earlier form reported success on zero files. The numbers are pinned in `tests/test_check_migration_shape.py` against the real commits, not carried here as prose to rot.

Residual limits, stated so a green run is not over-read: it compares counts, not identities, so one block fixed while another breaks nets nothing; a parenthetical legitimately spanning two comment lines reads as two bad blocks, so each finding is read rather than obeyed blindly; and shape is not sense — whether the surviving sentence still states the decision it inherited is a reader's call.
