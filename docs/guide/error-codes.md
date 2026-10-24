# Error codes

Every host-side failure surfaces through MCP `isError` with a `[code]` prefix,
so an agent can classify a failure from the text alone without parsing prose
(the D-019 host layer; see `docs/threat-model.md` §3). This page is the
lookup table for those prefixes.

**Keep this page in sync with the code.** The host code set is defined by
`src/maya_mcp_server/security.py`; adding a `PipelineError` subclass without
adding a row here is a documentation bug. There is no machine gate for this
page.

## Host-layer codes

Raised on the host side by `maya_mcp_server.security.PipelineError` subclasses.
The exception type is what carries the code; the message text is free-form and
not part of the contract.

| Code                   | Class                     | Meaning                                                                                                                                                                                         | What the agent should do                                                                                                                                                  |
| ---------------------- | ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pipeline_error`       | `PipelineError`           | Base class; raised directly only where no narrower code applies. Every subclass inherits this code unless it declares its own.                                                                  | Read the message; treat as an unclassified host failure.                                                                                                                  |
| `invalid_input`        | `InputValidationError`    | Input failed a host-side format or size rule: code over `MAX_CODE_SIZE`, malformed `session_key`, empty or invalid module name, unsupported export format, out-of-range port.                   | Fix the argument. This is deterministic, not transient — retrying the same call cannot help.                                                                              |
| `rate_limited`         | `RateLimitExceededError`  | The per-session token bucket for this tool's read/write class is empty. Read class ~100 calls/60s, write class ~20 calls/60s, per session.                                                      | Wait `retry_after` seconds, then retry. Slow down rather than switching tools.                                                                                            |
| `blocked_pattern`      | `PatternBlockedError`     | A parameter hit a zero-false-positive block rule (path traversal in a filename, `os.system`/`subprocess`/`eval(` in code).                                                                      | Rephrase the input, or tune the rule via a `rule_id` x tool x param exclusion.                                                                                            |
| `session_unavailable`  | `SessionLookupError`      | No Maya session could be resolved: none discovered, the named `session_key` is absent, or the match is ambiguous and needs an explicit key.                                                     | Call `list_sessions`, then pass an explicit `session_key`.                                                                                                                |
| `server_not_started`   | `ServerNotReadyError`     | The global session manager was requested before lifespan startup finished.                                                                                                                      | Retry after startup; it is not a scene problem.                                                                                                                           |
| `gui_session_required` | `GuiSessionRequiredError` | A GUI-only tool was called on a headless session. Host-side gate of the double headless check (D-024/D-025); the Maya-side layer returns the same code as a domain error.                       | Use the headless-capable tool, or attach to a GUI session.                                                                                                                |
| `capture_empty`        | `CaptureEmptyError`       | A visual capture produced a zero-byte artifact. Headless playblast empirically writes empty files without erroring, so this never passes through as success.                                    | Retry the capture; if it persists, the GUI session is unhealthy.                                                                                                          |
| `capture_invalid`      | `InvalidCaptureError`     | A capture payload failed an integrity check (bad base64, truncated frame).                                                                                                                      | Retry; a partial frame is not usable.                                                                                                                                     |
| `policy_disabled`      | `PolicyDisabledError`     | An operator policy flag disabled the tool — `MAYA_MCP_DISABLE_EXECUTE`, `MAYA_MCP_DISABLE_WRITE_MODULE`, or `MAYA_MCP_DISABLE_ARBITRARY` (D-207 ①⑦). The tool is also absent from `tools/list`. | Do not retry. Use a read-class tool, or ask the operator to unset the flag. This is a **misuse guardrail, not a hostile-agent boundary** — see `docs/threat-model.md` §2. |

## Maya-side domain errors

Maya-domain failures do **not** raise. They return a successful tool result
whose payload is `{"error": {"code", "message", "suggestion"?}}` (the two-layer
contract: host failures return `isError`; Maya domain failures return this
structured object). The code set there is owned by
`src/maya_mcp_server/maya_scene_module.py` and its injected siblings, and is
**not** part of the host set this table enumerates. Domain codes are documented at their
definition site and are deliberately not mirrored here: a second copy of that
list would be the third source of truth that D-183 exists to prevent.

Distinguishing the two in an agent:

```python
result = await call_tool(name, args)  # transport-level
if result.isError:  # host layer -> "[code] ..." text
    ...  # codes above
else:  # domain layer
    payload = result.structured_content
    if "error" in payload:  # {"code", "message", "suggestion"?}
        ...
```

## Test anchors

Each code is pinned by a test, so "documented" never means "believed". The node
IDs below are resolved against the tree by
`tests/test_check_d207_gates.py::TestErrorCodesChecker::test_every_cited_node_id_resolves_in_the_tree`
(file exists + symbol defined). They are not verified by running pytest here —
that would be a pytest inside a pytest — so the claim is deliberately scoped to
"resolves", not "passes".

| Code                   | pytest node ID                                                                                   |
| ---------------------- | ------------------------------------------------------------------------------------------------ |
| `pipeline_error`       | `tests/test_security.py::TestErrorCodeContract::test_base_pipeline_error_carries_pipeline_error` |
| `invalid_input`        | `tests/test_pipeline.py::TestPipelineValidation::test_bad_session_key_rejected`                  |
| `rate_limited`         | `tests/test_pipeline.py::TestPipelineRateLimit::test_write_bucket_smaller_than_read`             |
| `blocked_pattern`      | `tests/test_pipeline.py::TestPipelinePatternScan::test_code_os_system_blocked`                   |
| `session_unavailable`  | `tests/test_session_manager.py::TestCodedSessionErrors::test_no_sessions_raises_coded`           |
| `server_not_started`   | `tests/test_security.py::TestErrorCodeContract::test_server_not_ready_before_lifespan_init`      |
| `gui_session_required` | `tests/test_visual_tools.py::TestInjectionAndGates::test_headless_channel_short_circuits`        |
| `capture_empty`        | `tests/test_visual_tools.py::TestPreviewContract::test_host_nonempty_gate`                       |
| `capture_invalid`      | `tests/test_visual_tools.py::TestPreviewContract::test_bad_base64_raises_capture_invalid`        |
| `policy_disabled`      | `tests/test_pipeline.py::TestStrictPolicyMode::test_arbitrary_flag_denies_every_dangerous_tool`  |

Two codes are pinned by the generic contract test rather than a per-code raise,
because the base class and `server_not_started` have only one raise site each:
`tests/test_security.py::TestErrorCodeContract::test_every_error_class_renders_its_own_code_prefix`
iterates every `PipelineError` subclass and asserts each renders its own
`[code] ` prefix, so a new code is covered the day it is declared rather than
the day someone remembers its test.

Verify the whole column at once:

```bash
python -m pytest tests/test_security.py::TestErrorCodeContract -q
```
