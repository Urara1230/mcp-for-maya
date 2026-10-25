# AGENTS.md — Maya MCP Server

MCP server for LLM agents to inspect, plan, and edit Autodesk Maya scenes through structured tools.

## Install / run / test
uv sync --frozen
python -m maya_mcp_server -vv
uv run python -m pytest tests/ -q          # stub tier
uvx ruff@0.17.1 check . && uv run python -m mypy src/ | uv run mypy-baseline filter

## Gates
A "governance gate" is any `run:` step of the `lint` job in `.github/workflows/ci.yml`; `.github/gate-registry.yaml` indexes every enforced check with its pins, and `check_gate_registry.py` fails an unregistered or unwired one, so neither list can rot quietly. The three baseline ratchets are the ruff budget, the monolith line budget and the mypy baseline — all three only ever move down.

## Don't touch
- `src/maya_mcp_server/maya_scene_module.py` is line-capped by `.github/monolith-budget.json`; new Maya-side capability goes in a separate file of the same injection unit.
- `llms.txt`, `docs/guide/error-codes.md`, and the version list (pyproject/`__init__`/server.json/CHANGELOG/llms.txt) are derived; never hand-edit.
- Baseline files (ruff/monolith/mypy) are regenerated archives; CI never writes them.

## Known quirks
- Large module writes (>10K chars) stale the Maya command port; Qt sessions use the framed channel, temp-file fallback is headless-only.
- `execute_code` with `result_type="JSON"` may return `dict`; handle both. `import X; X.func()` can fail under result capture — pre-import, then call expressions only.
- Git Bash on Windows: never `2> nul`; use `2>/dev/null`. `D-xxx` refs cite the retired decision ledger — read it with `git show '1c5befe^:docs/decision-ledger.md'`.

## Agent skills

### Issue tracker

GitHub Issues via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical roles `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout: `GLOSSARY.md` at the root. See `docs/agents/domain.md`.
