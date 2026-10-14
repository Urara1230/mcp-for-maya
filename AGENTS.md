# AGENTS.md — Maya MCP Server

## Project Overview

This is an MCP (Model Context Protocol) server for Autodesk Maya. It enables LLM agents to interact with Maya sessions through structured tools for spatial awareness, camera planning, aesthetic analysis, and scene auditing.

## Tech stack

Python 3.10+ (host) / Maya ≥ 2023 embedded Python (injected side) · FastMCP · uv (deps + build) · pytest (stub tier on CI; mayapy/gui/human_verify manual tiers) · ruff + mypy ratchet baselines · GitButler (`but`) for VCS · GitHub issues via `gh` CLI.

## Repository structure

```
src/maya_mcp_server/
├── server.py              # FastMCP server entry point, tool registration
├── client.py              # Maya connection client (native + Qt framed channel)
├── session_manager.py     # Multi-session management
├── maya_mcp_helper.py     # Maya-side helper (executed inside Maya)
├── maya_bootstrap.py      # Maya bootstrap code (create_module)
├── scene_tools.py         # 13 MCP scene tool definitions
├── visual_tools.py        # 2 GUI-only visual tools
├── scene_cache.py         # TTL + dirty-detection cache
├── cos_formatter.py       # Chain-of-Symbol notation formatter
├── maya_scene_module.py   # Maya-side _mcp_scene (injected via write_module)
├── visual_module.py       # Maya-side _mcp_visual (lazy inject, GUI only)
├── asset_tools.py / asset_module.py    # asset_search/asset_import + _mcp_asset importer
├── export_tools.py / export_module.py  # scene_export + _mcp_export exporter
├── introspect_tools.py / introspect_module.py  # scene_describe/scene_nodes, _mcp_scene fragment
├── polyhaven.py           # Poly Haven host client: whitelist/md5/cache
├── spatial_types.py       # Data type definitions
├── connection_guide.py    # Connection bootstrap; userSetup.py managed marker-block
├── security.py            # Validation, rate limits, pattern scan, JSONL audit
├── pipeline.py            # FastMCP middleware: all tools -> validate/rate-limit/scan/audit
└── types.py / bootstrap.py / utils.py / __main__.py

tests/
├── maya_stub/             # stub maya.cmds + OpenMaya + scene graph (+ presence-baseline.json)
├── conftest.py            # maya_env fixture installs stub, binds maya_scene_module
└── test_*.py              # one file per surface: tools, pipeline, security, modules,
                           # gate checkers (check_*.py), manual tiers (mayapy/gui/human_verify)

docs/
├── adr/                   # ADR-0001..0030 architecture decision records (+ 0028-elements.yaml)
├── reference/             # real-maya-runbook, gui_verify.py harness, audit originals
├── evidence/              # decision-grade evidence (probes/, waiver list, window reports)
├── guide/                 # error-codes.md (derived+gated), session-lifecycle.md (prose)
└── distribution-surfaces.md / upstream-issue-status.md / testing.md / threat-model.md
    / visual-callform-matrix.md

.github/
├── workflows/ci.yml       # lint + test matrix + weekly drift canary -> issue notify
├── workflows/release.yml  # tag v* -> test -> build -> publish (trusted publisher)
├── scripts/check_*.py     # budget/ratchet/doc-truth/release gates run in the lint job
├── gate-registry.yaml     # pin registry: every blocking gate registers pins (ADR-0029/0030)
├── *-spec.yaml            # per-domain governance constants (externally-derived, D-214)
├── ruff-baseline.json / monolith-budget.json + root mypy-baseline.txt  # ratchet archives
└── assets-src/ + assets/  # README imagery (append-only published dir)
```

## How to run

```bash
python -m maya_mcp_server -vv        # run with debug logging
python -m pytest tests/ -q           # stub-tier suite (CI green bar)
ruff check .                         # lint vs frozen per-rule budget
python -m mypy src/ | mypy-baseline filter   # typecheck vs frozen baseline
uv run pre-commit run --all-files    # hook surface the lint job runs verbatim
```

## Key architecture decisions

1. **No Maya client modification** — everything works through existing `execute_code` + `write_module` channels
2. **Maya-side module injection** — `_mcp_scene` injected once via `write_module` on first scene tool call
3. **Large module handling** — GUI/Qt sessions inject any size via the framed channel (16 MiB cap); temp-file injection retained only for headless/native sessions
4. **Response alignment** — `execute_code` handles both `str` and `dict` results
5. **CoS notation** — compact scene format (~65% token savings per the CoS paper, arXiv 2305.10276 — a paper figure, not a local benchmark)

Blocking CI gates must derive expected values externally, carry a counterfactual pin, and register in `.github/gate-registry.yaml` — see ADR-0029/0030. Handoffs (grill/audit/exec) land in `.scratch/{slug}/handoffs/` — see ADR-0030 §6.

## ICEV workflow (mandatory for scene edits)

1. **INSPECT**: `scene_snapshot()` — understand current state
2. **COMPUTE**: use spatial data to calculate changes
3. **EXECUTE**: `execute_code()` — apply changes
4. **VERIFY**: `scene_assert()` + `scene_review()` — confirm results

## scene_review dimensions

| Dimension | Score | What it checks |
|-----------|-------|----------------|
| spatial | 10 | Object count, cameras, lights |
| overlaps | 10 | BBox collision (excludes parent-child) |
| conflicts | 10 | Penetration detection (excludes env objects like SUN) |
| zones | 5 | Naming-rule zone coverage |
| naming | 5 | Maya production naming convention |
| components | 10 | GRP_ grouping + nesting depth ≤ 4 |
| orphans | 5 | Empty groups, default names |
| aesthetics | 15 | 5-dim score: color/composition/scale/lighting/flow |
| lighting | 10 | Three-point setup, fill ratio, decay |
| organization | 10 | Hierarchy health |
| constraints | 5 | Max objects, custom rules |

## Maya connection setup

When `list_sessions` returns empty, use `maya_setup_guide()`:

1. `action="diagnose"` — checks Maya process, port, userSetup.py
2. `action="install"` — auto-generates userSetup.py (marker-block managed)
3. `action="guide"` — step-by-step fallback instructions
4. `action="uninstall"` — removes installed userSetup.py

userSetup.py paths: Windows `%MAYA_APP_DIR%\<version>\scripts\userSetup.py`; Linux `~/maya/<version>/scripts/`; macOS `~/Library/Preferences/Autodesk/maya/<version>/scripts/`.

Manual fallback: Script Editor → Python mode → `import maya.cmds as cmds; cmds.commandPort(name=":7001", sourceType="python")`.

## Maya naming convention

- `GRP_` groups, `GEO_` geometry, `MAT_` materials, `CAM_` cameras, `LGT_` lights (key/fill/rim/accent roles), `LOC_` locators
- Never default Maya names (pCube1, group1); max nesting depth 4; every mesh under a `GRP_` parent

## Known quirks

- Maya command port goes stale on large module writes (>10K chars); Qt sessions use the framed channel, temp-file fallback is headless-only.
- `execute_code` with `result_type="JSON"` may return `dict` directly — client handles both.
- `import X; X.func()` can fail under `prepare_code_for_result_capture`; pre-import with `execute_code("import X", NONE)` then use expression-only calls.
- Git Bash on Windows: never `2> nul` — `nul` is a device name bash treats as a filename, and a stray `nul` file crashes `but status`. Use `2>/dev/null`.

## Cross-module dependency rules (联动规范)

When modifying a module, its dependent files MUST be updated together — the full dependency map lives in CONTRIBUTING.md. Headline rules:

- `maya_scene_module.py` → `scene_tools.py` + `server.py` + READMEs + matching tests; growth past `monolith-budget.json` fails lint — new Maya-side capability routes to a same-unit separate file (ADR-0027), never appended to the monolith
- Any new tool surface → `pipeline.py` (TOOL_ANNOTATIONS) + `server.py` instructions + threat-model + READMEs + AGENTS
- `llms.txt`, `docs/guide/error-codes.md`, and the version list (pyproject/`__init__`/server.json/CHANGELOG/llms.txt) are derived/gated — never hand-edit
- New `check_*.py` gates → register in `gate-registry.yaml` (human-authored, never generated) with counterfactual + live pins
- Baseline files (ruff/monolith/mypy) are regenerated archives — CI never writes them; lowering a budget needs the reason in the same PR

## Pointers

- **Issue tracker**: GitHub issues via `gh` CLI — conventions in `docs/agents/issue-tracker.md`
- **Domain vocabulary**: `CONTEXT.md`; **decisions**: `docs/adr/` (read the ADRs touching an area before working in it)
- **VCS discipline**: GitButler (`but`) — never force-push or rewrite history. `D-xxx` references cite the retired decision ledger (deleted 2026-10-14) — resolve via `git log -- docs/decision-ledger.md`
- **Reference material**: `docs/reference/` — real-Maya verification runbook, `gui_verify.py` harness, archived external reviews
- **Session work**: `.scratch/` is local-only (gitignored); handoffs go to `.scratch/{slug}/handoffs/`
