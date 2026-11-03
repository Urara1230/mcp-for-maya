"""T-23b tests: scene_export tool layer.

ExecClient runs the generated code against the stub-bound _mcp_export
module through the REAL result-capture transform - same harness pattern
as test_scene_tools_json.py. Covers host-side
validation, domain-error passthrough, the injection lifecycle on both
channels (framed write_module + native mkstemp fallback), and the
TOOL_ANNOTATIONS contract.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import maya_stub
import pytest

from maya_mcp_server.export_tools import (
    _ensure_export_injected,
    _export_injected,
    register_export_tools,
)
from maya_mcp_server.maya_mcp_helper import prepare_code_for_result_capture
from maya_mcp_server.pipeline import TOOL_ANNOTATIONS
from maya_mcp_server.security import InputValidationError
from maya_mcp_server.types import CommandResponse, OutputBuffer, ResultType


class ExecClient:
    """Fake client: executes generated code with _mcp_export bound."""

    def __init__(self, module):
        self.module = module
        self.calls = []
        self.written = {}
        self.framed_channel = True  # Qt channel injects via write_module

    async def execute_code(self, code, result_type=ResultType.NONE):
        self.calls.append(code)
        rt = getattr(result_type, "value", result_type)
        ctx = {"__builtins__": __builtins__, "_mcp_export": self.module}
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
        self.written[name] = code
        return name


@pytest.fixture
def tools(maya_env, monkeypatch, tmp_path):
    """Stub scene + bound _mcp_export + captured tool fns."""
    module = maya_stub.load_export_module()
    sys.modules["_mcp_export"] = module
    _export_injected.add("_default")

    maya_env.scene.add_mesh("GEO_box")

    client = ExecClient(module)
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
    register_export_tools(mock_mcp)
    yield SimpleNamespace(fns=fns, client=client, manager=manager, env=maya_env, tmp=tmp_path)
    _export_injected.discard("_default")
    sys.modules.pop("_mcp_export", None)


class TestInjectionLifecycle:
    """_mcp_export lazy-injects through the shared helper on both
    channels: framed Qt sessions go through write_module (D-013),
    native/headless sessions stage a unique mkstemp file (D-082a)."""

    async def test_framed_channel_uses_write_module(self, tools):
        client = tools.client
        key = "framed-sess"
        _export_injected.discard(key)
        try:
            await _ensure_export_injected(client, key)
            assert "_mcp_export" in client.written
            assert "def export_scene" in client.written["_mcp_export"]
            assert any("import _mcp_export" in c for c in client.calls)
            assert key in _export_injected
        finally:
            _export_injected.discard(key)

    async def test_native_channel_small_module_uses_write_module(self):
        """_mcp_export is <15000 chars, under the native tmpfile
        threshold (D-013) - even the native channel injects via
        write_module and stages no temp file."""
        from pathlib import Path

        import platformdirs

        inject_dir = Path(platformdirs.user_cache_dir("mcp-for-maya")) / "inject"
        pre = set(inject_dir.glob("_mcp_export_src_*.py")) if inject_dir.exists() else set()

        client = SimpleNamespace(framed_channel=False)
        written = {}

        async def exec_code(code, result_type=ResultType.NONE):
            return CommandResponse(result=None, error=None)

        async def write_module(name, code, overwrite=False):
            written[name] = code
            return name

        client.execute_code = exec_code
        client.write_module = write_module
        _export_injected.discard("hygiene")
        try:
            await _ensure_export_injected(client, "hygiene")
        finally:
            _export_injected.discard("hygiene")

        assert "def export_scene" in written["_mcp_export"]
        post = set(inject_dir.glob("_mcp_export_src_*.py")) if inject_dir.exists() else set()
        assert post == pre, "small module must not stage a temp file"

    async def test_injection_once_per_session(self, tools):
        client = tools.client
        try:
            await tools.fns["scene_export"](str(tools.tmp / "a.fbx"), session_key="s1")
            writes_after_first = dict(client.written)
            assert "_mcp_export" in writes_after_first
            await tools.fns["scene_export"](
                str(tools.tmp / "b.fbx"), overwrite=True, session_key="s1"
            )
            assert client.written == writes_after_first
        finally:
            _export_injected.discard("s1")


class TestSceneExportTool:
    async def test_export_happy_path(self, tools):
        out = await tools.fns["scene_export"](str(tools.tmp / "scene.fbx"))
        res = json.loads(out)
        assert "error" not in res, res
        assert res["format"] == "fbx"
        assert res["objects_exported"] == 1
        assert res["size_bytes"] > 0
        # the tool drove the real generated-code path
        assert any("_mcp_export.export_scene" in c for c in tools.client.calls)

    async def test_export_objects_list_serializes(self, tools):
        tools.env.scene.add_mesh("GEO_ball")
        out = await tools.fns["scene_export"](
            str(tools.tmp / "sel.obj"), objects=["GEO_box", "GEO_ball"]
        )
        res = json.loads(out)
        assert "error" not in res, res
        assert res["objects_exported"] == 2

    async def test_export_domain_error_passthrough(self, tools):
        target = tools.tmp / "scene.fbx"
        target.write_bytes(b"OLD")
        out = await tools.fns["scene_export"](str(target))
        res = json.loads(out)
        assert res["error"]["code"] == "invalid_path"

    async def test_export_validation_errors(self, tools):
        with pytest.raises(InputValidationError):
            await tools.fns["scene_export"](path="")
        with pytest.raises(InputValidationError):
            await tools.fns["scene_export"](path="x.fbx", objects="GEO_box")
        with pytest.raises(InputValidationError):
            await tools.fns["scene_export"](path="x.fbx", format=7)


class TestAnnotations:
    def test_scene_export_annotations(self):
        """D-092: write-class, non-destructive, non-idempotent, closed."""
        ann = TOOL_ANNOTATIONS["scene_export"]
        assert ann.read_only_hint is False
        assert ann.destructive_hint is False
        assert ann.idempotent_hint is False
        assert ann.open_world_hint is False
