---
name: scene-review-playbook
description: "Run and act on the mcp-for-maya scene_review audit: pick the right checks, interpret the 0-100 score and per-check results, and map common findings (orphans, naming violations, overlaps, weak aesthetics, lighting gaps) to concrete fixes. Use after structural scene changes, before reporting a scene as done, or when a scene feels disorganized."
compatibility: "Tested on Claude Code only; requires mcp-for-maya MCP server"
---

# scene_review Playbook

> **Experimental.** Evaluated on Claude Code only; untested on Codex/Gemini CLI/Cursor. Tracked in issue #3.

`scene_review()` runs up to 11 deterministic checks and returns `{checks, issues, score}` — score is 0-100 (weighted per-check, normalized). Studio validators registered in the session add a `custom` section with its own score.

## Checks

| check | what it looks at |
|---|---|
| spatial | object/camera/light presence vs expectations |
| overlaps | bbox collisions (parent-child excluded) |
| conflicts | penetration between unrelated objects |
| zones | zone coverage vs naming-rule zones |
| naming | GRP_/GEO_/MAT_/CAM_/LGT_/LOC_ prefix discipline |
| components | GRP_ grouping, nesting depth ≤ 4 |
| orphans | empty groups, default Maya names |
| aesthetics | 5-dimension aesthetic score (color/composition/scale/lighting/flow) |
| lighting | three-point setup, fill ratio, decay |
| organization | overall hierarchy health |
| constraints | custom rule violations |

Per-check weights are defined in the review engine — see the audit table in README/AGENTS.md for exact max points; this card intentionally does not duplicate them.

## How to run

- Full audit: `scene_review()` — after structural changes.
- Subset: `scene_review(checks=["overlaps","conflicts"])` — cheaper re-verify after a targeted fix.
- Interpret: each entry in `issues[]` carries `severity` (error > warning), `check`, and a `msg` summary (counts + advice). Per-object detail lives under `checks[]` — e.g. `checks.overlaps.details` names the colliding pairs (sampled list). Fix errors first; warnings are judgment calls.

## Findings → actions

- `orphans`: root-level meshes or empty groups → reparent under a `GRP_*` group; delete empty groups.
- `naming`: rename to the prefix convention; never ship `pCube1`-style names.
- `overlaps` / `conflicts`: `scene_measure(obj_a, obj_b, mode="clearance")`, then move or resize; re-run those checks.
- `zones` low coverage: `scene_plan` for zone mapping and auto-grouping suggestions.
- `aesthetics` / `lighting` weak: `scene_aesthetics()` for the 5-dimension breakdown; lighting fixes follow three-point discipline (key/fill/rim).
- `constraints` violations: read the rule type in the issue, then satisfy it or re-validate via `scene_validate`.

## Studio validators

Register rules in the Maya session via `execute_code` and `scene_review()` reports them in a separate `custom` section — `score`/`max_score` in weight points plus its own `issues`; the built-in 0-100 never moves and the section is absent when nothing is registered.

```python
import _mcp_scene

_mcp_scene.register_validator(
    "roots_named",
    lambda: (False, "3 root objects are unnamed"),  # or just True/False
    severity="warning",
    weight=2,
    family="studio",
)
_mcp_scene.list_validators()  # _mcp_scene.unregister_validator("roots_named") drops it
```

A predicate returns `bool` or `(bool, message)`; anything else, like an exception, is recorded as `check_error` rather than guessed at. Same name overwrites, so re-registering after a reconnect is safe — and it is required: replacing the module or restarting Maya clears the registry, so a `custom` section that vanished means re-register, not "passed". The review always finishes, whatever one rule does. Pre-1.0 API: it may break before 1.0, with a changelog note.

## Done means

- errors: 0; warnings reviewed (not necessarily zero — judgment).
- after fixes, re-run the affected checks; never re-report a stale score.
