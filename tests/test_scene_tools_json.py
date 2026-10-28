"""T-02 regression tests: tool-layer JSON serialization + error passthrough.

The ExecClient executes the code scene_tools generates against the stub-bound
_mcp_scene module using the REAL helper result-capture transform — so these
tests exercise the full chain: arg serialization -> generated code ->
Maya-side module -> returned dict.
"""

from __future__ import annotations

import ast
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from maya_mcp_server.maya_mcp_helper import prepare_code_for_result_capture
from maya_mcp_server.scene_tools import (
    _caches,
    _injected_sessions,
    register_scene_tools,
)
from maya_mcp_server.types import CommandResponse, OutputBuffer, ResultType


class ExecClient:
    """Fake client: runs generated code on the stub scene module."""

    def __init__(self, module):
        self.module = module
        self.calls = []

    async def execute_code(self, code, result_type=ResultType.NONE):
        self.calls.append(code)
        rt = getattr(result_type, "value", result_type)
        ctx = {"__builtins__": __builtins__, "_mcp_scene": self.module}
        if rt == "NONE":
            exec(compile(code, "<mcp>", "exec"), ctx)
            return CommandResponse(result=None, error=None)
        modified, was = prepare_code_for_result_capture(code)
        if not was:
            raise RuntimeError(f"result not capturable: {code}")
        ctx["_mcp_result"] = None
        exec(compile(modified, "<mcp>", "exec"), ctx)
        return CommandResponse(result=ctx["_mcp_result"], error=None)

    async def get_buffered_output(self):
        return OutputBuffer()

    def append_output(self, stdout, stderr):
        pass

    async def write_module(self, name, code, overwrite=False):
        return name


class ErrorClient(ExecClient):
    """Client whose Maya side raises — error must propagate, not vanish."""

    async def execute_code(self, code, result_type=ResultType.NONE):
        self.calls.append(code)
        return CommandResponse(
            result=None,
            error={
                "type": "builtins.RuntimeError",
                "message": "kaboom in maya",
                "traceback": "Traceback...\nRuntimeError: kaboom in maya",
            },
        )


@pytest.fixture
def tools(maya_env, monkeypatch):
    sys.modules["_mcp_scene"] = maya_env.module
    _injected_sessions.add("_default")
    _caches.clear()
    client = ExecClient(maya_env.module)
    manager = MagicMock()
    manager.get_client = AsyncMock(return_value=client)
    monkeypatch.setattr("maya_mcp_server.server.get_session_manager", lambda: manager)
    mock_mcp = MagicMock()
    fns = {}

    def capture(fn=None, **_kwargs):
        if fn is None:
            return lambda f: capture(f)
        fns[fn.__name__] = fn
        return fn

    mock_mcp.tool = capture
    register_scene_tools(mock_mcp)
    yield SimpleNamespace(fns=fns, client=client, manager=manager, env=maya_env)
    _injected_sessions.discard("_default")
    _caches.clear()
    sys.modules.pop("_mcp_scene", None)


# ------------------------------------------------------------
# JSON-serialized arguments (P0-2: injection surface removal)
# ------------------------------------------------------------


class TestJsonArgSerialization:
    async def test_validate_rules_via_json(self, tools):
        tools.env.scene.add_mesh("GEO_a")
        out = await tools.fns["scene_validate"](
            rules=json.dumps([{"type": "max_objects", "value": 100}]),
            format="json",
        )
        res = json.loads(out)
        assert res["passed"] is True
        code = tools.client.calls[-1]
        assert "json.loads" in code, "rules must go through JSON"
        assert "rules_escaped" not in code
        ast.parse(code)  # generated code must always be valid python

    async def test_validate_rejects_bad_json(self, tools):
        from maya_mcp_server.security import InputValidationError

        with pytest.raises(InputValidationError):
            await tools.fns["scene_validate"](rules="not json {{{")

    async def test_injection_in_measure_args_is_inert(self, tools):
        tools.env.scene.add_mesh("GEO_a", t=(5, 0, 0))
        evil = "x'); import os; os.system('echo PWNED') #"
        out = await tools.fns["scene_measure"](
            obj_a=evil, obj_b="GEO_a", mode="center", format="json"
        )
        res = json.loads(out)
        # the hostile string arrived as DATA (object doesn't exist -> error)
        assert res["obj_a"] == evil
        assert "error" in res

    async def test_quotes_in_checkpoint_name_verbatim(self, tools):
        seen = {}

        def spy(name=None, overwrite=False):
            seen["name"] = name
            return {"ok": True, "name": name}

        tools.env.module.save_checkpoint = spy
        name = 'cp "quoted" \\ ; import os'
        await tools.fns["scene_checkpoint"](name=name)
        assert seen["name"] == name

    async def test_rollback_filename_verbatim(self, tools):
        seen = {}

        def spy(filename, discard_current_state=False):
            seen["fn"] = filename
            return {"success": True, "rolled_back_to": filename}

        tools.env.module.rollback_to_checkpoint = spy
        fn = 'cp_20250101_x"); pass #.ma'
        await tools.fns["scene_rollback"](filename=fn)
        assert seen["fn"] == fn

    async def test_checkpoint_passes_overwrite_kwarg(self, tools):
        seen = {}

        def spy(name=None, overwrite=False):
            seen["name"] = name
            seen["overwrite"] = overwrite
            return {"ok": True}

        tools.env.module.save_checkpoint = spy
        await tools.fns["scene_checkpoint"](name="v1", overwrite=True)
        assert seen == {"name": "v1", "overwrite": True}

    async def test_checkpoint_default_name_is_none(self, tools):
        seen = {}

        def spy(name=None, overwrite=False):
            seen["name"] = name
            return {"ok": True}

        tools.env.module.save_checkpoint = spy
        await tools.fns["scene_checkpoint"]()
        assert seen["name"] is None, (
            "default must reach the module as None so untitled scenes can error"
        )

    async def test_rollback_passes_discard_flag(self, tools):
        seen = {}

        def spy(filename, discard_current_state=False):
            seen["filename"] = filename
            seen["discard"] = discard_current_state
            return {"success": True}

        tools.env.module.rollback_to_checkpoint = spy
        await tools.fns["scene_rollback"](filename="cp_x.ma", discard_current_state=True)
        assert seen == {"filename": "cp_x.ma", "discard": True}

    async def test_scene_assert_expectations_json(self, tools):
        tools.env.scene.add_mesh("GEO_box", t=(1, 2, 3))
        expectations = json.dumps({"GEO_box": {"position": [1, 2, 3]}})
        out = await tools.fns["scene_assert"](expectations=expectations, format="json")
        res = json.loads(out)
        assert res["passed"] is True

    async def test_scene_inspect_target_serialized(self, tools):
        tools.env.scene.add_mesh("GEO_a", t=(7, 8, 9))
        out = await tools.fns["scene_inspect"](target="GEO_a", format="json")
        res = json.loads(out)
        assert res["object"]["name"] == "GEO_a"
        assert res["object"]["position"] == [7, 8, 9]  # not just name echo
        code = tools.client.calls[-1]
        ast.parse(code)

    async def test_camera_orbit_center_json(self, tools):
        out = await tools.fns["camera_orbit"](
            center="[10, 5, 0]", radius=100, frames=24, name="CAM_orb"
        )
        res = json.loads(out)
        assert "error" not in res
        assert res["center"] == [10, 5, 0]


# ------------------------------------------------------------
# Error passthrough (P0-1: result.error must not be dropped)
# ------------------------------------------------------------


class TestErrorPassthrough:
    async def test_scene_tool_raises_on_maya_error(self, tools, monkeypatch):
        err_client = ErrorClient(tools.env.module)
        tools.manager.get_client = AsyncMock(return_value=err_client)
        from maya_mcp_server.client import MayaExecutionError

        with pytest.raises(MayaExecutionError, match="kaboom"):
            await tools.fns["scene_snapshot"](format="json")

    async def test_server_execute_code_raises_on_error(self, monkeypatch):
        from maya_mcp_server import server
        from maya_mcp_server.client import MayaExecutionError

        client = AsyncMock()
        client.execute_code.return_value = CommandResponse(
            result=None,
            error={
                "type": "builtins.ValueError",
                "message": "bad thing happened",
                "traceback": "tb",
            },
        )
        client.get_buffered_output.return_value = OutputBuffer()
        client.append_output = MagicMock()
        manager = MagicMock()
        manager.get_client = AsyncMock(return_value=client)
        monkeypatch.setattr(server, "get_session_manager", lambda: manager)

        execute_fn = getattr(server.execute_code, "fn", server.execute_code)
        with pytest.raises(MayaExecutionError, match="bad thing happened"):
            await execute_fn(code="1+1", result_type="JSON")

    async def test_server_execute_code_ok(self, monkeypatch):
        from maya_mcp_server import server

        client = AsyncMock()
        client.execute_code.return_value = CommandResponse(result={"ok": 1}, error=None)
        client.get_buffered_output.return_value = OutputBuffer()
        client.append_output = MagicMock()  # sync def (client.py:342) — not AsyncMock
        manager = MagicMock()
        manager.get_client = AsyncMock(return_value=client)
        monkeypatch.setattr(server, "get_session_manager", lambda: manager)

        execute_fn = getattr(server.execute_code, "fn", server.execute_code)
        out = await execute_fn(code='{"a":1}', result_type="JSON")
        assert out == {"ok": 1}


# ------------------------------------------------------------
# Issue #79: a registered validator reaches the scene_review MCP tool
# ------------------------------------------------------------


class TestSceneReviewCustomValidators:
    async def test_builtin_shape_and_score_when_nothing_registered(self, tools):
        tools.env.scene.add_mesh("GEO_box")
        res = json.loads(await tools.fns["scene_review"](format="json"))

        assert "custom" not in res, "the section is omitted, not empty"
        assert set(res) == {
            "checks",
            "issues",
            "score",
            "total_issues",
            "errors",
            "warnings",
            "standards",
        }
        assert res == json.loads(json.dumps(tools.env.module.scene_review()))

    async def test_registered_validator_reaches_the_tool_answer(self, tools):
        tools.env.scene.add_mesh("GEO_box")
        before = json.loads(await tools.fns["scene_review"](format="json"))

        tools.env.module.register_validator(
            "no_free_roots",
            lambda: (False, "GEO_box sits at the scene root"),
            severity="warning",
            family="studio",
        )
        res = json.loads(await tools.fns["scene_review"](format="json"))

        assert res["custom"]["issues"][0]["name"] == "no_free_roots"
        assert res["custom"]["score"] == 0.0
        for key in ("score", "errors", "warnings", "total_issues", "checks"):
            assert res[key] == before[key], f"built-in {key} moved with a validator registered"
        assert "scene_review_with_validators" in tools.client.calls[-1]

    async def test_cos_view_keeps_its_own_line(self, tools):
        tools.env.module.register_validator("harsh", lambda: False, message="rule broken")
        out = await tools.fns["scene_review"](format="cos")

        assert "CUSTOM: score=0.0/1.0 | validators=1 | failing=1 | check_error=0" in out
        assert "[ERROR] harsh: rule broken" in out

    async def test_cos_separates_a_broken_rule_from_a_violated_one(self, tools):
        tools.env.module.register_validator("broken", lambda: 1 / 0)
        tools.env.module.register_validator("violated", lambda: False)
        out = await tools.fns["scene_review"](format="cos")

        assert "failing=1 | check_error=1" in out, "one number must not mix the two"

    async def test_no_issue_row_prints_none(self, tools):
        tools.env.module.register_validator("silent", lambda: False)
        out = await tools.fns["scene_review"](format="cos")

        assert "silent: None" not in out
        assert "[ERROR] silent: silent failed" in out


# ------------------------------------------------------------
# Issue #120: the `checks` argument shape the card documents
# ------------------------------------------------------------


class TestChecksArgumentShape:
    """`checks` is a comma-separated string at the MCP boundary and a Python
    list at the Maya side; the tool splits it itself. The scene-review card
    carries the call shape and `skills/` sits outside check_doc_claims.py's
    surfaces, so this is the only assertion that goes red when the two drift.

    One bound, stated because a pin that cannot fail is worse than none: the
    spy reads what the generated code hands to `scene_review_with_validators`,
    so a tool that stopped splitting would still be pinned here — but a tool
    that renamed the Maya-side entry point would fail to find its call at all
    rather than report a shape.
    """

    async def test_two_names_reach_the_maya_side_call_as_a_list(self, tools):
        seen = {}

        def spy(checks=None):
            seen["checks"] = checks
            return {"checks": {}, "issues": [], "score": 0}

        tools.env.module.scene_review_with_validators = spy
        await tools.fns["scene_review"](checks="overlaps,conflicts", format="json")

        assert "checks" in seen, "the Maya-side entry point was never called - re-aim this pin"
        assert seen["checks"] == ["overlaps", "conflicts"]
