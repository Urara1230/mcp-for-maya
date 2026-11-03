# Threat Model — mcp-for-maya

> Status: current (D-008/D-017/D-018/D-019; tool-surface rows D-075/
> D-092/D-094/D-110; CVE citation D-132, 2026-09-28). Source of truth
> for what this project's safety machinery does and does not claim.

## 1. Explicit threat model

**Trusted party**: the MCP client/agent (Codex, Claude, …) and the local
user who runs this server.

**Risk addressed**: confused-deputy — an agent that receives injected or
misunderstood instructions and drives this server's capabilities toward
outcomes the user did not intend. Also plain accidents: runaway loops,
bad filenames, oversized payloads.

**Risk explicitly NOT addressed**: a malicious client. Any MCP client can
send arbitrary tool calls; nothing here can or does try to contain a
hostile one. If the client is the attacker, the game is already over —
the controls below are a safety net for accidents and confused deputies,
not a boundary.

## 2. Boundary declaration

The following are **not** security boundaries:

- The regex pattern scan. It catches obvious accidents; it is trivially
  bypassable and we say so. Detection, not containment.
- Input validation. It rejects malformed inputs, not malicious intent.
- Rate limiting. It slows accidents, not adversaries.
- The audit log. It records what happened; it prevents nothing.
- The `userSetup.py` marker block. It protects *your* file from
  being overwritten; it does not protect Maya from the block.
- Anything Maya-side (`maya_mcp_helper.py`, `_mcp_scene`).
  Code reaching Maya runs with Maya's full privileges — __builtins__ is
  fully present in the exec namespace by design.
- The strict policy flags (`MAYA_MCP_DISABLE_EXECUTE`,
  `MAYA_MCP_DISABLE_WRITE_MODULE`, `MAYA_MCP_DISABLE_ARBITRARY`,
  D-207 1). These are **misuse guardrails, not a hostile-agent
  boundary**, and the distinction is load-bearing: an agent that can
  still reach `execute_code` is inside the trust boundary already and
  no flag moves that. What they buy is the *other* direction — an
  operator, a demo, or a CI sandbox can shrink the blast radius of a
  mistake without trusting every caller to pass sensible arguments.
  They are config, not a jail, and must never be described as one.

We deliberately avoid the words "sandbox" and "secure" for these
mechanisms. A future opt-in AST allowlist (`safe_mode`, roadmap
P2) would still be a hallucination-slowing net, not a jail —
language-level confinement of Python inside Maya is a known-won't-fix
problem.

## 3. Defense inventory (the actual safety net)

| Layer | What it does | Where |
|-------|--------------|-------|
| Input validation | size caps, session-key format, module-name rules | security.py |
| Rate limiting | per-session token buckets: ~20 writes/60s, ~100 reads/60s | security.py |
| Pattern scan | exact-match blocks (`../` traversal in filenames, `os.system`/`subprocess`/`eval(` in code); everything else warn-only into audit | security.py |
| Audit log | append-only JSONL, every tool call recorded incl. rejections | security.py |
| Error contract | host failures raise coded exceptions (isError); Maya-domain failures return `{error:{code,message,suggestion?}}` | pipeline.py, maya_scene_module.py |
| Checkpoint/rollback | exportAll memory snapshots, auto safety snapshot before rollback, S2 rebind | maya_scene_module.py |
| userSetup.py merge | marker-block upsert, confirm-gated, .bak backup, symmetric uninstall | connection_guide.py |
| Tool annotations | readOnlyHint/destructiveHint/idempotentHint/openWorldHint on all 23 tools | pipeline.py |
| Strict policy mode | `MAYA_MCP_DISABLE_*{EXECUTE,WRITE_MODULE,ARBITRARY}` remove the named tools from `tools/list` and deny the call with `[policy_disabled]`, audited as `rejected`; one choke point in the pipeline, none in the tool bodies | pipeline.py (POLICY_ENV_FLAGS, on_list_tools, on_call_tool) |

All 23 tools pass through one FastMCP middleware pipeline:
validate → rate-limit → pattern-scan → dispatch → audit.

## 4. Port and network exposure

- The MCP server speaks stdio/SSE to its client; the Maya channel is a
  localhost TCP command port (default `:7001`, sourceType=python) used for
  discovery/bootstrap, plus a Qt TCP working channel (localhost-only,
  length-prefixed frames, 16 MiB cap, ports 50000-60000) created during
  bootstrap. Headless Maya (no Qt event loop) keeps a dedicated commandPort
  as the minimal working channel.
- The command port binds localhost only; `allow_remote_connections`
  defaults to False and there is no remote path today.
- Anyone who can reach the command port can run Python inside Maya —
  that is Maya's own model, not something this server adds or can remove.
  Keep it localhost; do not port-forward it.
- The two `maya://sessions/*/info|output` MCP **resources** are read-only
  lookups served from the session registry; they do not pass through the
  tool pipeline (no rate limit, no audit row). They execute no Maya code.
- The server makes no outbound network requests: this fork removed the
  upstream Poly Haven asset tools (`asset_search`/`asset_import`), which
  were its only egress path.

## 5. Tool annotations (MCP hints)

Every tool carries four hints as cooperation signals (the server-side
pipeline enforces regardless):

The matrix below is verbatim `pipeline.TOOL_ANNOTATIONS` — the code is
authoritative and this table must match it row for row:

| Hints | Tools |
|-------|-------|
| readOnly=T, destructive=F, idempotent=T | `list_sessions`, `scene_snapshot`, `scene_inspect`, `scene_measure`, `scene_assert`, `scene_validate`, `scene_checkpoint_list`, `scene_aesthetics`, `scene_review`, `scene_describe`, `scene_nodes`, `scene_viewport_snapshot`, `scene_render_preview` |
| readOnly=F, destructive=F, idempotent=F | `scene_checkpoint`, `scene_rollback`, `scene_plan`, `camera_create`, `camera_orbit`, `add_session`, `scene_export` |
| readOnly=F, destructive=T, idempotent=F | `execute_code`, `write_module`, `maya_setup_guide` |

- scene_review keeps readOnly=T: reading the scene is what it does, and a
  custom validator (#79) inherits no privilege it did not already have —
  registering one goes through `execute_code`, which is already
  destructive=T. What does change is repeatability of its **output**: the
  `custom` section reflects the session registry at call time, so a rule
  that unregisters itself, or a re-registration between two calls, moves
  the section while the scene stays untouched. The hints describe the tool's
  own effect on the scene, not the determinism of user-registered code.
- openWorldHint=false on every tool: none reaches the network.
- scene_export performs a **local file write** to an arbitrary
  host-absolute path via Maya (D-092). That is informed acceptance,
  not a boundary: under the execute_code ceiling any client can already
  write files, so pretending to confine the export path would be
  theater. What is real: `../` sequences in the `path` param are still
  pattern-blocked host-side, the Maya side normalizes
  (expanduser + abspath) and auto-creates parents, existing files are
  rejected unless overwrite=True, and prompt=False is forced so no
  modal dialog can hang the channel. Residuals registered honestly: no
  realpath/symlink resolution (a symlinked parent is followed silently)
  and no per-directory allowlist (any path the Maya process can write
  is writable).
- `scene_viewport_snapshot` / `scene_render_preview` are readOnly
  under the net-zero side-effect discipline (camera/current-time
  restored on every path, D-026) - the visible transient is documented
  in the tool docstrings.
- `scene_describe` / `scene_nodes` (D-094) are pure read queries and
  add no new threat surface: no file I/O, no network, no writes.
  Honest registration: `scene_describe(include_values=True)` can
  trigger DG evaluation inside Maya (cost, not a side effect - the
  eval is Maya's normal dependency resolution) and `scene_nodes`
  paginates positionally, so a stale `cursor` is answered with
  `invalid_cursor`, never silently re-applied. Responses are
  size-bounded (D-110): attrs/connections faces cap at 200 items by
  default (caller-relaxable to 1000 via limit=); over-budget output is
  truncated and disclosed via *_truncated flags + totals, never
  silently cut.
- destructive=true is reserved for arbitrary code execution and
  startup-file writes. `scene_rollback` recovers state (not destructive)
  but is not idempotent; `camera_create`/`camera_orbit`/`scene_checkpoint`
  mutate the scene/filesystem but are recoverable, so destructive=false.
- `execute_code` executes arbitrary Python or MEL code with full session
  privileges in the active Maya session; execution is irreversible and
  cannot be automatically undone (TDQS behavior disclosure, D-174).
  Similarly, `scene_validate` is read-only (destructive=false) and does
  not perform auto_fix mutations (unlike `scene_plan` which can mutate
  scene organization).

## 6. Audit log schema and replay

One JSON object per line (JSONL), at
`platformdirs.user_log_dir("mcp-for-maya")/audit.jsonl`:

```json
{"event_id":"uuid","timestamp":"ISO8601Z","session_id":"host:port",
 "tool_name":"execute_code","input_summary":"code=\"print…\"(12B sha:ab12)",
 "outcome":"success|error|rejected","duration_ms":1.2,
 "warnings":[{"rule_id":"…","match":"…"}]}
```

- File mode 0600, size-based rotation (audit.jsonl.N), write failures
  never block tool execution; dual-written to the app logger.
- `input_summary` is an allowlist preview (≤200 chars): param
  names, short values, size+sha for large strings; credential-shaped
  param names (token/secret/password/api_key) are redacted.
- `outcome=rejected` is always recorded — probing is the first
  signal of a confused deputy.

Replay:

```bash
jq -c 'select(.tool_name=="execute_code")' audit.jsonl
jq -c 'select(.outcome=="rejected")' audit.jsonl
jq -c 'select(.session_id=="127.0.0.1:7001")' audit.jsonl
```

## 7. Vulnerability reporting

See [SECURITY.md](../SECURITY.md).

## 8. What would change this model

- Multi-user or remote access → the model breaks; don't do it without a
  redesign.
- `safe_mode` AST allowlist (roadmap P2, opt-in, default off,
  responses carry a `mode` field, docs say "safety net, not a
  boundary").
