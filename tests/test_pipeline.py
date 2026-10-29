"""T-04 regression tests: unified security pipeline middleware (D-018/D-019).

The SecurityPipeline is exercised directly (no running server): a fake
MiddlewareContext carries CallToolRequestParams; call_next is a stub.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp.server.middleware import MiddlewareContext


try:
    from fastmcp.tools.base import ToolResult  # fastmcp 4.x
except ImportError:
    try:
        from fastmcp.tools.tool import ToolResult  # fastmcp 2.10..2.x
    except ImportError:
        from fastmcp.tools import ToolResult  # fastmcp <2.10
from mcp.types import CallToolRequestParams

from maya_mcp_server.pipeline import (
    POLICY_ENV_FLAGS,
    TOOL_ANNOTATIONS,
    SecurityPipeline,
    policy_disabled_tools,
    tool_annotations,
)
from maya_mcp_server.security import (
    AuditLogger,
    InputValidationError,
    PatternBlockedError,
    PolicyDisabledError,
    RateLimitExceededError,
    SecurityConfig,
    SessionLookupError,
)


def _ctx(name: str, arguments: dict | None = None) -> MiddlewareContext:
    return MiddlewareContext(
        message=CallToolRequestParams(name=name, arguments=arguments or {}),
        method="tools/call",
    )


async def _ok_next(context):
    return ToolResult(content="ok")


async def _boom_next(context):
    raise RuntimeError("backend exploded")


def _domain_error_next(payload_error=None):
    async def _next(context):
        return ToolResult(
            structured_content={"error": payload_error or {"code": "x", "message": "boom"}}
        )

    return _next


def _pipeline(tmp_path: Path, **cfg) -> tuple[SecurityPipeline, AuditLogger, Path]:
    log_path = tmp_path / "audit" / "audit.jsonl"
    audit = AuditLogger(log_path)
    config = SecurityConfig(**cfg)
    return SecurityPipeline(config=config, audit=audit), audit, log_path


def _read_events(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


class TestToolAnnotations:
    """All 23 tools carry the four hints (D-018 + D-075/D-092/D-094 matrix)."""

    EXPECTED = {
        "list_sessions",
        "maya_setup_guide",
        "write_module",
        "execute_code",
        "add_session",
        "scene_snapshot",
        "scene_inspect",
        "scene_measure",
        "scene_assert",
        "scene_validate",
        "scene_checkpoint",
        "scene_rollback",
        "scene_checkpoint_list",
        "camera_create",
        "camera_orbit",
        "scene_aesthetics",
        "scene_review",
        "scene_plan",
        "scene_viewport_snapshot",
        "scene_render_preview",
        "scene_export",
        "scene_describe",
        "scene_nodes",
    }

    def test_all_23_tools_covered(self):
        assert set(TOOL_ANNOTATIONS) == self.EXPECTED

    def test_four_hints_all_set(self):
        for name, ann in TOOL_ANNOTATIONS.items():
            assert ann.read_only_hint is not None, name
            assert ann.destructive_hint is not None, name
            assert ann.idempotent_hint is not None, name
            assert ann.open_world_hint is False, name

    def test_dangerous_tools_marked_destructive(self):
        for name in ("execute_code", "write_module", "maya_setup_guide"):
            assert TOOL_ANNOTATIONS[name].destructive_hint is True
            assert TOOL_ANNOTATIONS[name].read_only_hint is False

    def test_read_tools_readonly_idempotent(self):
        for name in (
            "list_sessions",
            "scene_snapshot",
            "scene_inspect",
            "scene_measure",
            "scene_assert",
            "scene_validate",
            "scene_checkpoint_list",
            "scene_aesthetics",
            "scene_review",
            "scene_viewport_snapshot",
            "scene_render_preview",
            "scene_describe",
            "scene_nodes",
        ):
            assert TOOL_ANNOTATIONS[name].read_only_hint is True, name
            assert TOOL_ANNOTATIONS[name].idempotent_hint is True, name

    def test_rollback_not_destructive_not_idempotent(self):
        ann = TOOL_ANNOTATIONS["scene_rollback"]
        assert ann.destructive_hint is False
        assert ann.idempotent_hint is False
        assert ann.read_only_hint is False

    def test_unknown_tool_defaults_write_class(self):
        assert tool_annotations("nope").read_only_hint is False


class TestPipelineDispatch:
    async def test_success_call_audited(self, tmp_path):
        pipe, _audit, log = _pipeline(tmp_path)
        result = await pipe.on_call_tool(
            _ctx("scene_snapshot", {"session_key": "127.0.0.1:50000"}),
            _ok_next,
        )
        assert result is not None
        events = _read_events(log)
        assert len(events) == 1
        ev = events[0]
        assert ev["tool_name"] == "scene_snapshot"
        assert ev["outcome"] == "success"
        assert ev["session_id"] == "127.0.0.1:50000"
        assert ev["event_id"]
        assert ev["timestamp"].endswith("Z")
        assert isinstance(ev["duration_ms"], (int, float))
        assert ev["warnings"] == []
        assert ev["input_summary"]

    async def test_backend_error_recorded_as_error(self, tmp_path):
        pipe, _audit, log = _pipeline(tmp_path)
        with pytest.raises(RuntimeError):
            await pipe.on_call_tool(_ctx("execute_code", {"code": "1+1"}), _boom_next)
        ev = _read_events(log)[-1]
        assert ev["outcome"] == "error"
        assert ev["tool_name"] == "execute_code"

    async def test_domain_error_payload_counts_as_error(self, tmp_path):
        pipe, _audit, log = _pipeline(tmp_path)
        result = await pipe.on_call_tool(
            _ctx("scene_rollback", {"filename": "cp_x.ma"}),
            _domain_error_next(),
        )
        assert result is not None
        ev = _read_events(log)[-1]
        assert ev["outcome"] == "error"

    async def test_audit_disabled_no_file(self, tmp_path):
        log = tmp_path / "audit" / "audit.jsonl"
        pipe = SecurityPipeline(
            config=SecurityConfig(audit_enabled=False),
            audit=None,
        )
        assert pipe.audit is None
        await pipe.on_call_tool(_ctx("list_sessions"), _ok_next)
        assert not log.exists()


class TestPipelineValidation:
    async def test_bad_session_key_rejected(self, tmp_path):
        pipe, _audit, log = _pipeline(tmp_path)
        with pytest.raises(InputValidationError, match="invalid_input"):
            await pipe.on_call_tool(_ctx("scene_snapshot", {"session_key": "no-port"}), _ok_next)
        ev = _read_events(log)[-1]
        assert ev["outcome"] == "rejected"

    async def test_oversize_code_rejected(self, tmp_path):
        pipe, _audit, log = _pipeline(tmp_path)
        big = "x" * (SecurityConfig().max_code_size + 1)
        with pytest.raises(InputValidationError):
            await pipe.on_call_tool(_ctx("execute_code", {"code": big}), _ok_next)
        assert _read_events(log)[-1]["outcome"] == "rejected"


class TestPipelineRateLimit:
    async def test_write_bucket_smaller_than_read(self, tmp_path):
        cfg = SecurityConfig(rate_limit_write_max_calls=3, rate_limit_read_max_calls=50)
        pipe = SecurityPipeline(config=cfg, audit=AuditLogger(tmp_path / "a.jsonl"))
        for _ in range(3):
            await pipe.on_call_tool(_ctx("execute_code", {"code": "1"}), _ok_next)
        with pytest.raises(RateLimitExceededError, match="rate_limited"):
            await pipe.on_call_tool(_ctx("execute_code", {"code": "1"}), _ok_next)

    async def test_read_bucket_independent(self, tmp_path):
        cfg = SecurityConfig(rate_limit_write_max_calls=2, rate_limit_read_max_calls=3)
        pipe = SecurityPipeline(config=cfg, audit=AuditLogger(tmp_path / "a.jsonl"))
        # exhaust write bucket
        for _ in range(2):
            await pipe.on_call_tool(_ctx("execute_code", {"code": "1"}), _ok_next)
        # read still allowed
        for _ in range(3):
            await pipe.on_call_tool(_ctx("list_sessions"), _ok_next)
        with pytest.raises(RateLimitExceededError):
            await pipe.on_call_tool(_ctx("list_sessions"), _ok_next)

    async def test_per_session_isolation(self, tmp_path):
        cfg = SecurityConfig(rate_limit_write_max_calls=1)
        pipe = SecurityPipeline(config=cfg, audit=AuditLogger(tmp_path / "a.jsonl"))
        args_a = {"code": "1", "session_key": "127.0.0.1:1"}
        args_b = {"code": "1", "session_key": "127.0.0.1:2"}
        await pipe.on_call_tool(_ctx("execute_code", args_a), _ok_next)
        # session B unaffected
        await pipe.on_call_tool(_ctx("execute_code", args_b), _ok_next)
        with pytest.raises(RateLimitExceededError):
            await pipe.on_call_tool(_ctx("execute_code", args_a), _ok_next)

    async def test_rate_limit_rejection_audited(self, tmp_path):
        cfg = SecurityConfig(rate_limit_read_max_calls=1)
        pipe = SecurityPipeline(config=cfg, audit=AuditLogger(tmp_path / "a.jsonl"))
        await pipe.on_call_tool(_ctx("list_sessions"), _ok_next)
        with pytest.raises(RateLimitExceededError):
            await pipe.on_call_tool(_ctx("list_sessions"), _ok_next)
        events = _read_events(tmp_path / "a.jsonl")
        assert [e["outcome"] for e in events] == ["success", "rejected"]

    async def test_rate_limit_disabled(self, tmp_path):
        cfg = SecurityConfig(rate_limit_enabled=False, rate_limit_read_max_calls=1)
        pipe = SecurityPipeline(config=cfg, audit=AuditLogger(tmp_path / "a.jsonl"))
        for _ in range(5):
            await pipe.on_call_tool(_ctx("list_sessions"), _ok_next)


class TestPipelinePatternScan:
    async def test_code_os_system_blocked(self, tmp_path):
        pipe, _audit, log = _pipeline(tmp_path)
        with pytest.raises(PatternBlockedError, match="blocked_pattern"):
            await pipe.on_call_tool(
                _ctx("execute_code", {"code": "import os; os.system('calc')"}),
                _ok_next,
            )
        ev = _read_events(log)[-1]
        assert ev["outcome"] == "rejected"

    async def test_code_subprocess_blocked(self, tmp_path):
        pipe, _audit, _log = _pipeline(tmp_path)
        with pytest.raises(PatternBlockedError):
            await pipe.on_call_tool(
                _ctx("write_module", {"name": "m", "code": "import subprocess"}),
                _ok_next,
            )

    async def test_code_eval_blocked(self, tmp_path):
        pipe, _audit, _log = _pipeline(tmp_path)
        with pytest.raises(PatternBlockedError):
            await pipe.on_call_tool(_ctx("execute_code", {"code": "eval('1+1')"}), _ok_next)

    async def test_filename_traversal_blocked(self, tmp_path):
        pipe, _audit, _log = _pipeline(tmp_path)
        with pytest.raises(PatternBlockedError, match="file-traversal"):
            await pipe.on_call_tool(
                _ctx("scene_rollback", {"filename": "../../etc/x.ma"}), _ok_next
            )

    async def test_non_code_param_warn_only(self, tmp_path):
        """Dangerous text in a non-code param warns but does not block."""
        pipe, _audit, log = _pipeline(tmp_path)
        await pipe.on_call_tool(
            _ctx("scene_inspect", {"target": "eval(1) os.system thing"}),
            _ok_next,
        )
        ev = _read_events(log)[-1]
        assert ev["outcome"] == "success"
        assert any(w["rule_id"] == "warn-os-exec" for w in ev["warnings"])

    async def test_checkpoint_name_not_blocked_by_dotdot(self, tmp_path):
        """name is not a filename-kind param -> traversal rule doesn't apply."""
        pipe, _audit, _log = _pipeline(tmp_path)
        # '..' in a name warns nothing and does not block (Maya-side
        # whitelist validation still applies downstream)
        await pipe.on_call_tool(_ctx("scene_checkpoint", {"name": ".."}), _ok_next)

    async def test_exclusion_suppresses_block(self, tmp_path):
        cfg = SecurityConfig(pattern_exclusions=frozenset({("code-eval", "execute_code", "code")}))
        pipe = SecurityPipeline(config=cfg, audit=AuditLogger(tmp_path / "a.jsonl"))
        # eval( is excluded; still warns via warn-eval
        await pipe.on_call_tool(_ctx("execute_code", {"code": "eval('1+1')"}), _ok_next)

    async def test_benign_code_warns_not_blocks(self, tmp_path):
        """Patterns outside the precise block list are warn-only on code."""
        pipe, _audit, log = _pipeline(tmp_path)
        await pipe.on_call_tool(_ctx("execute_code", {"code": "__builtins__"}), _ok_next)
        ev = _read_events(log)[-1]
        assert ev["outcome"] == "success"
        assert any(w["rule_id"] == "warn-builtins" for w in ev["warnings"])

    async def test_block_disabled_downgrades_to_warn(self, tmp_path):
        cfg = SecurityConfig(block_dangerous_patterns=False)
        pipe = SecurityPipeline(config=cfg, audit=AuditLogger(tmp_path / "a.jsonl"))
        await pipe.on_call_tool(_ctx("execute_code", {"code": "os.system('x')"}), _ok_next)


class TestAuditJsonlContract:
    """D-018: JSONL lands on disk, one JSON object per line, jq-replayable.

    jq -c 'select(.tool_name=="execute_code")' must be able to replay a
    session \u2014 equivalently, every line is a self-contained JSON object
    carrying the full schema.
    """

    REQUIRED_FIELDS = {
        "event_id",
        "timestamp",
        "session_id",
        "tool_name",
        "input_summary",
        "outcome",
        "duration_ms",
        "warnings",
    }

    async def test_every_line_is_valid_json_object(self, tmp_path):
        pipe, _audit, log = _pipeline(tmp_path)
        for tool in ("scene_snapshot", "execute_code", "scene_rollback"):
            await pipe.on_call_tool(_ctx(tool, {}), _ok_next)
        lines = log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        for line in lines:
            ev = json.loads(line)  # jq-parseable: exactly one object per line
            assert self.REQUIRED_FIELDS <= set(ev), ev
            assert ev["outcome"] == "success"
            assert ev["tool_name"] in ("scene_snapshot", "execute_code", "scene_rollback")
            assert isinstance(ev["input_summary"], str)

    async def test_replay_filter_by_tool(self, tmp_path):
        pipe, _audit, log = _pipeline(tmp_path)
        await pipe.on_call_tool(_ctx("scene_snapshot", {}), _ok_next)
        await pipe.on_call_tool(_ctx("execute_code", {"code": "print(1)"}), _ok_next)
        await pipe.on_call_tool(_ctx("scene_snapshot", {}), _ok_next)
        events = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
        by_tool = [e for e in events if e["tool_name"] == "scene_snapshot"]
        assert len(by_tool) == 2  # jq 'select(.tool_name==...)' equivalent

    async def test_session_id_from_context(self, tmp_path):
        pipe, _audit, log = _pipeline(tmp_path)
        ctx = _ctx("scene_snapshot", {"session_key": "127.0.0.1:7001"})
        await pipe.on_call_tool(ctx, _ok_next)
        ev = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
        assert ev["session_id"] == "127.0.0.1:7001"

    def test_rotation_preserves_jsonl(self, tmp_path):
        log = tmp_path / "audit.jsonl"
        audit = AuditLogger(log, max_bytes=200, backup_count=2)
        for i in range(20):
            audit.record({"event_id": str(i), "tool_name": "t"})
        # rotated files exist and each line still parses
        rotated = sorted(tmp_path.glob("audit.jsonl.*"))
        assert rotated, "rotation must produce audit.jsonl.N files"
        for f in [log, *rotated]:
            for line in f.read_text(encoding="utf-8").splitlines():
                json.loads(line)

    def test_write_failure_does_not_raise(self, tmp_path):
        log = tmp_path / "nonexistent_dir" / "x" / "audit.jsonl"
        log.parent.mkdir(parents=True)
        audit = AuditLogger(log)
        audit.record({"event_id": "1"})
        # read-only file forces append failure
        import os
        import stat

        os.chmod(log, stat.S_IREAD)
        try:
            audit.record({"event_id": "2"})  # must not raise
        finally:
            os.chmod(log, stat.S_IWRITE)
        assert audit._warned is True  # warn-once flag set, no permanent latch

    def test_input_summary_no_raw_code(self, tmp_path):
        """input_summary must not carry full code/credentials (D-018)."""
        from maya_mcp_server.security import build_audit_event

        ev = build_audit_event(
            tool_name="execute_code",
            params={"code": "import os; os.system('rm -rf /') " * 50},
            session_id="s",
            outcome="success",
            duration_ms=1.0,
            warnings=[],
        )
        # allowlist summary: short head preview + size + sha256, never the
        # full payload (forensics still sees the attempt + rule hit)
        assert len(ev["input_summary"]) <= 200
        assert "sha:" in ev["input_summary"]
        assert "1650B" in ev["input_summary"]
        assert ev["input_summary"].count("os.system") == 1


class _ExplodingSessionContext:
    """fastmcp_context stand-in: its session_id property raises RuntimeError,
    matching real Context.session_id behavior without a request context."""

    @property
    def session_id(self):
        raise RuntimeError("request context is not set")


async def test_session_resolution_failure_still_audited(tmp_path):
    """F-3: session-id resolution runs inside the pipeline try; a failing
    Context.session_id falls back to '_default' and the call is audited."""
    pipe, _audit, log_path = _pipeline(tmp_path)
    ctx = MiddlewareContext(
        message=CallToolRequestParams(name="list_sessions", arguments={}),
        method="tools/call",
        fastmcp_context=_ExplodingSessionContext(),
    )
    await pipe.on_call_tool(ctx, _ok_next)
    events = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    assert len(events) == 1
    assert events[0]["session_id"] == "_default"
    assert events[0]["outcome"] == "success"


async def test_in_tool_pipeline_error_counts_as_error(tmp_path):
    """Coded errors raised inside the tool body are outcome='error';
    'rejected' is reserved for pipeline denials (pre-dispatch)."""

    async def _no_session(context):
        raise SessionLookupError(
            "No Maya sessions available",
            suggestion="call add_session(host, port) first",
        )

    pipe, _audit, log_path = _pipeline(tmp_path)
    with pytest.raises(SessionLookupError) as ei:
        await pipe.on_call_tool(_ctx("scene_snapshot"), _no_session)
    assert "[session_unavailable]" in str(ei.value)
    assert "call add_session" in str(ei.value)
    events = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    assert events[-1]["outcome"] == "error"


# ---------------------------------------------------------------------------
# D-207 7: strict policy mode. Stub tier, in CI: assert the flag bites and
# assert the read-only surface is untouched. A guardrail with no acceptance
# test is an adoption without acceptance.
# ---------------------------------------------------------------------------


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


def _list_ctx() -> MiddlewareContext:
    return MiddlewareContext(message=None, method="tools/list")


def _listing(*names: str):
    async def _next(context):
        return [_FakeTool(n) for n in names]

    return _next


ALL_TOOLS = tuple(TOOL_ANNOTATIONS)


class TestStrictPolicyMode:
    """operator-side misuse guardrails (D-207 1) - not a hostile-agent boundary."""

    @pytest.mark.parametrize(
        "flag,tool",
        [
            ("MAYA_MCP_DISABLE_EXECUTE", "execute_code"),
            ("MAYA_MCP_DISABLE_WRITE_MODULE", "write_module"),
        ],
    )
    async def test_flag_denies_its_tool(self, tmp_path, monkeypatch, flag, tool):
        monkeypatch.setenv(flag, "1")
        pipe, _audit, log = _pipeline(tmp_path)
        with pytest.raises(PolicyDisabledError) as exc:
            await pipe.on_call_tool(_ctx(tool, {"code": "print(1)"}), _ok_next)
        assert exc.value.code == "policy_disabled"
        assert flag in str(exc.value) or "MAYA_MCP_DISABLE" in str(exc.value)
        events = _read_events(log)
        assert events[-1]["outcome"] == "rejected", "a policy denial must be audited"
        assert events[-1]["tool_name"] == tool

    async def test_arbitrary_flag_denies_every_dangerous_tool(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAYA_MCP_DISABLE_ARBITRARY", "1")
        pipe, _audit, _log = _pipeline(tmp_path)
        for tool in POLICY_ENV_FLAGS["MAYA_MCP_DISABLE_ARBITRARY"]:
            with pytest.raises(PolicyDisabledError):
                await pipe.on_call_tool(_ctx(tool), _ok_next)

    async def test_read_only_surface_is_unaffected(self, tmp_path, monkeypatch):
        """The guard narrows the dangerous tools only. A read-class tool still
        dispatches with every flag on - otherwise the flag would be a mute
        button rather than a guardrail."""
        for flag in POLICY_ENV_FLAGS:
            monkeypatch.setenv(flag, "1")
        pipe, _audit, log = _pipeline(tmp_path)
        for tool in ("scene_snapshot", "scene_measure", "list_sessions"):
            result = await pipe.on_call_tool(_ctx(tool), _ok_next)
            assert result.content[0].text == "ok", f"{tool} must still dispatch"
        outcomes = {(e["tool_name"], e["outcome"]) for e in _read_events(log)}
        assert outcomes == {
            ("scene_snapshot", "success"),
            ("scene_measure", "success"),
            ("list_sessions", "success"),
        }

    async def test_disabled_tool_is_hidden_from_tools_list(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAYA_MCP_DISABLE_ARBITRARY", "1")
        pipe, _audit, _log = _pipeline(tmp_path)
        listed = await pipe.on_list_tools(_list_ctx(), _listing(*ALL_TOOLS))
        names = {t.name for t in listed}
        assert "execute_code" not in names
        assert "scene_snapshot" in names

    async def test_tools_list_untouched_when_no_flag_is_set(self, tmp_path, monkeypatch):
        for flag in POLICY_ENV_FLAGS:
            monkeypatch.delenv(flag, raising=False)
        pipe, _audit, _log = _pipeline(tmp_path)
        listed = await pipe.on_list_tools(_list_ctx(), _listing(*ALL_TOOLS))
        assert {t.name for t in listed} == set(ALL_TOOLS)

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "  "])
    def test_falsy_values_do_not_disable(self, value):
        assert policy_disabled_tools({flag: value for flag in POLICY_ENV_FLAGS}) == frozenset()

    @pytest.mark.parametrize("value", ["1", "TRUE", "yes", " On "])
    def test_truthy_values_disable(self, value):
        disabled = policy_disabled_tools({flag: value for flag in POLICY_ENV_FLAGS})
        assert disabled == frozenset({"execute_code", "write_module", "maya_setup_guide"})

    def test_flags_are_read_per_call_not_frozen_at_import(self, monkeypatch):
        monkeypatch.delenv("MAYA_MCP_DISABLE_EXECUTE", raising=False)
        assert "execute_code" not in policy_disabled_tools()
        monkeypatch.setenv("MAYA_MCP_DISABLE_EXECUTE", "1")
        assert "execute_code" in policy_disabled_tools()

    def test_every_flag_target_is_an_annotated_tool(self):
        """A typo in a flag's target would silently guard nothing."""
        for flag, tools in POLICY_ENV_FLAGS.items():
            unknown = set(tools) - set(TOOL_ANNOTATIONS)
            assert not unknown, f"{flag} names unannotated tool(s): {unknown}"

    def test_the_three_dangerous_tools_are_exactly_the_covered_set(self):
        """MAYA_MCP_DISABLE_ARBITRARY is the umbrella over the destructive
        class - if a fourth destructive tool appears and is not added, this
        fails rather than letting the umbrella quietly mean less than it says."""
        destructive = {n for n, a in TOOL_ANNOTATIONS.items() if a.destructive_hint}
        assert destructive == POLICY_ENV_FLAGS["MAYA_MCP_DISABLE_ARBITRARY"]
