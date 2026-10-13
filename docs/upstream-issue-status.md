# Upstream issue status — chadrik/maya-mcp-server

This page maps each open issue on the upstream repository
([chadrik/maya-mcp-server](https://github.com/chadrik/maya-mcp-server)) to its
disposition in this fork ([Xxx91n/mcp-for-maya](https://pypi.org/project/mcp-for-maya/),
dist name `mcp-for-maya`). It exists so that issue comments can point at a
stable, honest anchor instead of re-stating per-release details inline.

Source of truth: `CHANGELOG.md`（原账本 D-057..D-065 行已随账本退役删除，见 git 历史）
— this table is regenerated from the same facts, never ahead of them.

Version pointers are **durable claims**: `>= X.Y.Z` names the first release
that contains the change, which stays true even if later releases supersede it
or an intermediate release is yanked.

| Issue | Symptom (upstream title) | Disposition in this fork | Ships in | Verification status |
|-------|--------------------------|--------------------------|----------|---------------------|
| [#1](https://github.com/chadrik/maya-mcp-server/issues/1) | `_scan_for_sessions` repeatedly probes MEL commandPorts -> Script Editor error spam | **Fixed**: bilingual `eval("1/2")` probe — Python 3 answers `0.5`, anything else classifies MEL (live-verified: a real MEL port answers with a syntax-error body, which still classifies correctly; the Script Editor logs one line per probe); non-Python ports join a session-level permanent exemption set (lifted only when the port leaves LISTEN) so the symptom is bounded to one probe per port per session; `MAYA_MCP_INCLUDE_PORTS` / `MAYA_MCP_EXCLUDE_PORTS` env + ctor filters | >= 0.1.2 | **Real-Maya verified** (T-18a, Maya 2024.0.0.4640, zh locale): MEL `:7002` socket reply shape pinned `— `eval("1/2")` returns a syntax-error body + `\x00` terminator; classification correct; stub tier green (15 probe/exemption/filter tests) |
| [#2](https://github.com/chadrik/maya-mcp-server/issues/2) | `ImportError` on startup: `FakeConnection` removed in newer fakeredis | **Already resolved** — this fork's dependency surface contains no `fakeredis` at all (the test suite runs against an in-repo Maya stub, not a fakeredis-backed fake) | >= 0.1.0 (true of every released version, yank-independent) | Verified by inspecting released package metadata (`requires_dist` of 0.1.0/0.1.1) — no code change was needed |
| [#3](https://github.com/chadrik/maya-mcp-server/issues/3) | `prepare_code_for_result_capture` fails on Python 3.7 (Maya 2022) due to `ast.unparse` | **Boundary declaration, not a fix** — dual-runtime support floor: host Python >= 3.10; injected helper requires Maya >= 2023 (Python >= 3.9, the `ast.unparse` boundary). Maya <= 2022 is now refused at injection time with an actionable message instead of a bare `AttributeError`; a `hasattr(ast, 'unparse')` fallback degrades result capture instead of crashing on patched interpreters | >= 0.1.2 | Stub tier green (floor-guard tests); behavior verified against the Maya stub — support matrix documented in README + `docs/testing.md` |
| [#4](https://github.com/chadrik/maya-mcp-server/issues/4) | `create_module(overwrite=True)` discards running `_qt_server`, leaking orphan listeners | **Fixed**: `create_module` invokes the evicted module's `_mcp_teardown` hook before replacing the `sys.modules` entry; the helper ships an idempotent teardown (per-item try/except, `deleteLater`, disconnects `newConnection` so a reloaded helper does not double-fire). A failing teardown surfaces as a `warning` field instead of blocking the reload | >= 0.1.2 | Stub tier green (9 teardown-protocol tests incl. Qt signal semantics); **real-Maya verified** (T-18a): instrumented `_mcp_teardown` fired exactly once on `create_module(overwrite)` reconnect, old Qt listener port closed, new server bound + framed channel live |
| [#5](https://github.com/chadrik/maya-mcp-server/issues/5) | README should explain commandPort setup for multiple Maya instances | **Documented**: bilingual README section + `docs/testing.md` — the mechanics (commandPort = one bound host:port listener, second instance on the same port fails to bind), per-instance port topology, auto-scan as the primary path with `add_session` as manual fallback, symptom-based troubleshooting, and why commandPort does not persist across sessions (`userSetup.py` is the recommended persistence mechanism) | >= 0.1.2 | Docs landed; content reviewed in T-16 audit (PASS-WITH-NITS) |
| [#7](https://github.com/chadrik/maya-mcp-server/issues/7) | `execute_code` silently swallows exceptions: returns `result.result`, drops `result.error` | **Already resolved** — `server.py` calls `raise_for_error` on the execution response and raises `MayaExecutionError` with the remote traceback; true of every released version | >= 0.1.0 | Verified by `git show` on release tags (0.1.0/0.1.1 both contain the raise path). Wider transport-path hardening (call-form handling etc.) ships with 0.1.2 |

## Response plan — executed 2026-09-23

Each issue got exactly one technical comment (root cause -> fix path ->
durable version pointer -> fork disclosure -> offer to upstream the fix);
drafts lived in `docs/upstream-issue-response-drafts.md`. All six comments
were posted after user authorization once 0.1.2 was public:

| Issue | Comment |
|-------|---------|
| #1 | [issuecomment-5780348679](https://github.com/chadrik/maya-mcp-server/issues/1#issuecomment-5780348679) |
| #2 | [issuecomment-5780144265](https://github.com/chadrik/maya-mcp-server/issues/2#issuecomment-5780144265) |
| #3 | [issuecomment-5780349383](https://github.com/chadrik/maya-mcp-server/issues/3#issuecomment-5780349383) |
| #4 | [issuecomment-5780349960](https://github.com/chadrik/maya-mcp-server/issues/4#issuecomment-5780349960) |
| #5 | [issuecomment-5780350777](https://github.com/chadrik/maya-mcp-server/issues/5#issuecomment-5780350777) |
| #7 | [issuecomment-5780145054](https://github.com/chadrik/maya-mcp-server/issues/7#issuecomment-5780145054) |

Release state at posting time: `v0.1.2` tagged (merge `960c58b`), published
to PyPI via OIDC release workflow; 0.1.0 and 0.1.1 yanked on PyPI
(per-version reasons recorded in `docs/reference/release-prep.md`).
