# Contributing

## Development setup

```bash
uv sync --frozen                   # installs project + dev group from uv.lock (D-112)
# alternatives: uv sync (re-resolve) | pip install -e . --group dev (pip >=25.1)
pre-commit install                  # one-time: wire git hooks (.pre-commit-config.yaml)
pre-commit run --all-files          # hook sweep — CI runs this identical step as its lint fallback
python -m pytest tests/ -q         # stub-layer suite (contract tests vs the maya.cmds stub)
ruff check .                        # lint — frozen budget gate (.github/ruff-baseline.json), ratchet only goes down
python -m mypy src | mypy-baseline filter   # typecheck gate — fails only on NEW errors vs mypy-baseline.txt
python -m maya_mcp_server -vv      # run with DEBUG logs (-v=INFO, -vv=DEBUG)
```

House rules:

- Every fix ships with a regression test on the maya stub layer.
- Real-Maya tests are a separate local manual tier (`pytest -m mayapy`, see docs/testing.md) — never mixed with the stub tier.
- If your change makes a doc line stale, fix that line in the same commit (「文档传播矩阵」词条 in GLOSSARY.md).
- When folding `[Unreleased]` into a release section, rewrite each Fixed entry's
  `fixed version: [Unreleased]` literal to that release's version number by hand
  (D-230 fold-time rule; human step).

## Cross-module dependency rules (联动规范)

When modifying a module, the dependent files below MUST be updated together —
stale companions cause integration failures and gate reds.

| Module modified | Must also update | Reason |
|-----------------|------------------|--------|
| `maya_scene_module.py` (scene_plan) | `scene_tools.py`, `server.py`, `README.md`, `README.zh-CN.md` | Scene planning tool needs MCP tool + docs |
| `maya_scene_module.py` (aesthetic/lighting/scene_review functions) | `scene_tools.py`, `tests/test_maya_scene_module.py` | Inline `_score_*` functions are the sole implementation; tool descriptions + stub-tier coverage must match |
| `maya_scene_module.py` (new function) | `scene_tools.py`, `server.py`, `README.md`, `README.zh-CN.md`, `tests/` | Every new Maya function needs an MCP tool, docs, and tests |
| `maya_scene_module.py` (line-count growth) | `.github/monolith-budget.json` | Ratchet: growth past the cap fails lint; legitimate additions need a reasoned budget edit in the same PR; new capability routes to a separate file in the same injection unit, never appended to the monolith |
| `visual_module.py` / `visual_tools.py` | `server.py` instructions, `pipeline.py` annotations, `tests/test_visual_tools.py`, READMEs, `docs/threat-model.md` | Capture contract, annotations, and docs must stay in sync |
| `asset_tools.py` / `polyhaven.py` / `asset_module.py` | `pipeline.py`, `server.py`, `docs/threat-model.md`, asset tests, `tests/maya_stub/` (new cmds surface), READMEs | Host-side download/cache vs Maya-side import — every piece in sync |
| `export_tools.py` / `export_module.py` | `pipeline.py`, `server.py`, `docs/threat-model.md`, export tests, `tests/maya_stub/` (export surface), READMEs | Host validation/registration vs Maya-side export contract |
| `introspect_tools.py` / `introspect_module.py` | `pipeline.py`, `server.py`, `docs/threat-model.md`, `docs/visual-callform-matrix.md`, introspect tests, `tests/maya_stub/`, READMEs | Fragment concatenated into the `_mcp_scene` payload (same-unit file); the D-110 response budget (`limit`, `*_truncated`, `total_*` fields) is part of the contract |
| `scene_tools.py` (new tool) | `server.py` instructions, READMEs, `AGENTS.md` | Tool surface changes require documentation sync |
| `scene_cache.py` | `scene_tools.py`, `session_manager.py` | Cache invalidation consistency |
| `security.py` | `pipeline.py`, `tests/test_security.py`, `tests/test_pipeline.py` | Rules enforced by the middleware pipeline |
| `pipeline.py` strict policy flags (`MAYA_MCP_DISABLE_*`) | `docs/threat-model.md` §2/§3, `docs/guide/error-codes.md`, `llms.txt`, `tests/test_pipeline.py::TestStrictPolicyMode` | One choke point; uncovered dangerous tools or undocumented error codes fail their gates |
| `connection_guide.py` | `server.py` (maya_setup_guide params), `tests/test_connection_guide.py` | Marker-block semantics + flags |
| `cos_formatter.py` | `scene_tools.py` | Formatter changes affect all tool COS outputs |
| Derived surfaces | — | `llms.txt` (from TOOL_ANNOTATIONS), `docs/guide/error-codes.md` (from `security.py`), and the version list (pyproject/`__init__`/server.json/CHANGELOG/llms.txt) are derived and gated — never hand-edit; `docs/guide/session-lifecycle.md` is prose with AST-verified anchors, NOT derived |
| `.github/gate-registry.yaml` / `check_gate_registry.py` / `*-spec.yaml` | `tests/`, `ci.yml`, `AGENTS.md`, `CHANGELOG.md` | Gates register pins (human-authored, never generated); exemptions carry `expires\|issue`; governance constants live in per-domain spec files, not code; spec files must not mirror object state (second source of truth) |

### Injected-module admission criteria

New Maya-side capability defaults to a NEW per-domain injected module — never
into the `maya_scene_module.py` monolith. A new module is allowed only when all
criteria hold: (1) independent injection timing — the capability is needed in
session shapes of its own; (2) independent Maya-side API surface; (3)
independent failure domain; (4) target-module health — when criteria FAIL and
the destination is the registered monolith, land the code as a separate
host-side file assembled into the same injection unit (first case:
introspection → `_mcp_scene` unit). Cross-module calls must be declared
explicitly — injected modules are self-contained by architecture.

Host-side calls into injected units go through `client.py` primitives:
`module_call(module, fn, *args, **kw)` (JSON-payload builder — user input never
becomes executable source) and `exec_module_code(client, code)`. Per-domain
`_ensure_X_injected` binders pin module_name/source_path/session-set = domain
config, not duplication.

### Changelog evidence rule

Every `Added`/`Fixed`/`Changed` bullet in CHANGELOG.md's `[Unreleased]` lane
MUST carry an executable evidence pointer: a pytest node ID, `path::symbol`, or
`sha:path:line`. "Run this command and here is its output" is not one of them —
it cannot be resolved after the fact, so it asserts nothing a checker can test.
Released sections are point-in-time records and are never re-checked. Enforced by
`.github/scripts/check_doc_claims.py`, which also fails a pointer that no longer
resolves and a test count that no longer matches the suite it cites. Every
`Fixed` entry records `Broken version:` → `fixed version:` fields.

### Assertion & evidence discipline

- Enumeration/count assertions declare their domain; recompute before restating a count.
- Anything not reproducible is recorded "unverified" — never guess a cause.

### Asset directory discipline

`.github/assets/` is append-only: README imagery is referenced by absolute
`main` URLs and published PyPI pages render `main` forever — deleting or
renaming a published asset permanently breaks historical release pages. New
captures get NEW filenames.

## Pull requests

- One branch per change; keep diffs small and reviewable.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) (feat / fix / docs / refactor / test / chore).
- CI gates (`.github/workflows/ci.yml`): `pytest` must be green on ubuntu + windows; the ruff budget
  (`.github/ruff-baseline.json`) is ratchet-only-down — above budget fails, and a lower count may be
  budgeted down in the same PR commit with the reason stated. `mypy` is baseline-gated in CI
  (`mypy-baseline.txt`; 184 errors in 2 files as of the D-124 dormant-module deletion —
  baseline counts are stub-env sensitive; drift trail 221→223→212→223→184 across formatter,
  stub-environment changes and dormant-code removal): `python -m mypy src | mypy-baseline filter`
  fails only on NEW errors.
  After resolving errors, run `python -m mypy src | mypy-baseline sync` and commit
  the refreshed baseline in the same PR. The ruff budget is per-rule
  (`{segment:{rule:count}}`) since T-10b — rules cannot subsidise each other.
  Budget gates are judged on the checked commit's tip (the PR's end state); a
  mid-PR commit that transiently exceeds a budget does not constitute a gate
  failure.

## Conduct

This project follows the [Contributor Covenant](https://www.contributor-covenant.org/version/2/1/code_of_conduct/) v2.1. Be respectful, assume good faith, keep discussion technical.
