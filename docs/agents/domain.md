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
