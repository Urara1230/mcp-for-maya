# Session lifecycle

One page, four columns: how a session is lost, what triggers teardown, what is
left behind, and how long it can stay behind.

> **Semantics of this summary view are governed by the ADR.** This table is a
> navigation aid over the exit paths. Where it appears to conflict with an ADR,
> the ADR wins and this page is the bug — not the other way round. It is
> derived from code, not maintained as a parallel specification: every cell
> names the mechanism it describes.

Paths are grouped by _what dies_, because the teardown guarantee comes from the
process that owned the resource, not from the code that created it.

## Exit paths

| Exit path                                                                                    | Teardown trigger                                                                                                                           | Residual state                                                                                                                                                                                                                      | Worst-case residual window                                                                                                                                                         |
| -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Maya exits** (quit, crash, `kill`)                                                         | None. No hook can run: the resource owner is the process, and the process is gone.                                                         | Nothing in Maya. The host sees EOF/reset on the next frame. The dedicated bootstrap commandPort dies with Maya.                                                                                                                     | Maya's own lifetime — the host notices within one frame                                                                                                                            |
| **Channel dies, Maya still running** (Qt server stopped inside Maya, port closed, Maya hung) | `SessionManager._background_scan` → `_prune_dead_sessions` on the next tick: `client.ping()` returns false or raises                       | The session is popped from `_sessions`, its entry in `_config_to_session` is deleted, its key is discarded from `_stream_capture_installed`, then `await client.disconnect()` runs best-effort (exceptions swallowed)               | Normal: `scan_interval` (default 10s) + one ping RTO. **If scans keep erroring the loop backs off exponentially to 60s** (`max_backoff`), so the ceiling is 60s + one RTT, not 10s |
| **Session abandoned without a close** (tool stops using a session)                           | None per call. There is no per-call teardown; only the prune path above reclaims it                                                        | The connection object and its stream-capture marker live until the next prune decides the peer is dead                                                                                                                              | Same as the row above: ≤ 60s + one RTT                                                                                                                                             |
| **Injected module overwritten** (`write_module` re-injects the same name, D-058)             | `create_module(overwrite=True)` invokes the OLD module object's `_mcp_teardown()` **before** evicting it from `sys.modules`                | `_mcp_teardown()` releases `_qt_server.stop()` and `uninstall_stream_capture()`, each inside its own `try/except` so one failure cannot skip the rest. Failures come back as a `warning` field on the result — never a hard failure | Synchronous: the new module is not visible until teardown has returned. A caller that ignores the `warning` can be left with the old Qt socket still bound                         |
| **MCP server exits cleanly** (`mcp.run_async()` returns - stdio client closed the pipe, normal Ctrl-C) | The `finally:` in `run_server()` calls `shutdown_session_manager()` -> `SessionManager.stop()`: cancels the scan task, disconnects every client, clears all five bookkeeping sets | Host-side state only; the dedicated bootstrap commandPort per client is closed by `disconnect()`. **Maya-side residue is untouched** - the `_mcp` module, the Qt socket and the stream-capture hook survive | Maya-side residue lasts until Maya exits or the next injection overwrites the module |
| **MCP server killed / crashes** (SIGKILL, `kill -9`, power loss) | **None.** That `finally:` lives in `__main__.py::run_server` and a hard kill never reaches it; there is no `atexit` handler and no signal handler | Same as the row above, and nothing on the host side is cleaned either | Same as the row above. This is the longest-lived residue in the system |
| **Server embedded in another host** (imported as a module instead of run as `__main__`) | **None** - `run_server()` is only reached through `__main__.py::main`, so an embedding host that does not call it gets no teardown | Same as above | Same as above |
| **Host restarts the MCP server against a live Maya**                                         | None needed from the old process — the next `write_module` takes the overwrite path above                                                  | Same as the row above, cleared on next injection                                                                                                                                                                                    | One injection                                                                                                                                                                      |

## Discovery and establishment

A session appears without any tool call: `SessionManager.start()` does one
immediate scan and then loops every `scan_interval`, so discovery latency is
bounded by the same interval as pruning. Each scan probes only configured
communication ports, localhost-only. A port that fails to probe is cooled down
for `failed_port_retry_after` (default 60s) via `_failed_ports`, and a port
identified as listening but non-Python is never re-probed while it stays
LISTENing.

## Reading this table honestly

- "Worst case" here means the longest the code can be shown to leave something
  behind, not a probabilistic estimate. For the backoff row it is the
  configured ceiling, not the typical value.
- Three rows have no host-side teardown at all. That is a statement about the
  architecture - Maya owns the resource, and a `finally:` does not survive
  `SIGKILL` - not an omission in the table.
- If you change a teardown path, this page and its ADR are both in scope — the
  table is not allowed to be the only place that knows.

## Anchors

All eight anchors resolve in the tree; the composition is machine-checked by
`tests/test_check_d207_gates.py::TestErrorCodesChecker::test_session_lifecycle_anchor_composition_is_what_the_changelog_claims`
rather than by reading this sentence.
Nine references: **eight `path::symbol` anchors and one ADR file path** (a document
rather than by reading this sentence.

- Scan loop and backoff ceiling: `src/maya_mcp_server/session_manager.py::_background_scan`
- Prune path: `src/maya_mcp_server/session_manager.py::_prune_dead_sessions`
- Manager stop: `src/maya_mcp_server/session_manager.py::stop`
- Module overwrite teardown: `src/maya_mcp_server/maya_mcp_helper.py::_mcp_teardown`
- Dedicated commandPort close: `src/maya_mcp_server/client.py::disconnect`
- Shutdown wiring (the `finally:` a hard kill skips): `src/maya_mcp_server/__main__.py::run_server`
- Session-manager teardown body: `src/maya_mcp_server/server.py::shutdown_session_manager`
- Qt primary channel: the Qt framed channel is the working channel; the native command port is bootstrap/headless fallback only
- Teardown protocol behaviour: `tests/test_module_teardown.py::TestCreateModuleTeardown`
