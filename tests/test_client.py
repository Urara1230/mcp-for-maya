"""Tests for MayaClient and MayaQtClient."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from maya_mcp_server import maya_mcp_helper as helper
from maya_mcp_server.client import (
    MayaClient,
    MayaConnectionError,
    MayaExecutionError,
    MayaQtClient,
    MayaTimeoutError,
    MayaUnavailableError,
    exec_module_code,
    module_call,
)
from maya_mcp_server.security import InputValidationError
from maya_mcp_server.types import (
    CommandResponse,
    OutputBuffer,
    PortType,
    ResultType,
    SessionInfo,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def maya_client() -> MayaClient:
    """Create a MayaClient instance for testing."""
    client = MayaClient(host="127.0.0.1", port=7001)
    # Simulate connected state with a MagicMock that has sync methods
    client._writer = MagicMock()
    client._writer.is_closing.return_value = False
    client._reader = MagicMock()
    return client


@pytest.fixture
def qt_client() -> MayaQtClient:
    """Create a MayaQtClient instance for testing."""
    client = MayaQtClient(host="127.0.0.1", port=50000)
    # Simulate connected state with a MagicMock that has sync methods
    client._writer = MagicMock()
    client._writer.is_closing.return_value = False
    client._reader = MagicMock()
    return client


# ============================================================================
# BaseMayaClient Tests (via MayaClient)
# ============================================================================


class TestBaseMayaClientProperties:
    """Test BaseMayaClient properties."""

    def test_key(self, maya_client: MayaClient) -> None:
        """Test key property returns host:port."""
        assert maya_client.key == "127.0.0.1:7001"

    def test_is_connected_true(self, maya_client: MayaClient) -> None:
        """Test is_connected returns True when writer is open."""
        maya_client._writer.is_closing.return_value = False
        assert maya_client.is_connected is True

    def test_is_connected_false_no_writer(self) -> None:
        """Test is_connected returns False when no writer."""
        client = MayaClient()
        assert client.is_connected is False

    def test_is_connected_false_writer_closing(self, maya_client: MayaClient) -> None:
        """Test is_connected returns False when writer is closing."""
        maya_client._writer.is_closing.return_value = True
        assert maya_client.is_connected is False


class TestOutputBufferMethods:
    """Test output buffer methods."""

    def test_append_output_stdout(self, maya_client: MayaClient) -> None:
        """Test appending stdout."""
        maya_client.append_output(stdout="hello")
        maya_client.append_output(stdout=" world")
        assert maya_client._stdout_buffer == "hello world"
        assert maya_client._stderr_buffer == ""

    def test_append_output_stderr(self, maya_client: MayaClient) -> None:
        """Test appending stderr."""
        maya_client.append_output(stderr="error1")
        maya_client.append_output(stderr="\nerror2")
        assert maya_client._stderr_buffer == "error1\nerror2"
        assert maya_client._stdout_buffer == ""

    def test_append_output_both(self, maya_client: MayaClient) -> None:
        """Test appending both stdout and stderr."""
        maya_client.append_output(stdout="out", stderr="err")
        assert maya_client._stdout_buffer == "out"
        assert maya_client._stderr_buffer == "err"

    def test_get_accumulated_output_clears_by_default(self, maya_client: MayaClient) -> None:
        """Test get_accumulated_output clears buffers by default."""
        maya_client._stdout_buffer = "stdout content"
        maya_client._stderr_buffer = "stderr content"

        output = maya_client.get_accumulated_output()

        assert isinstance(output, OutputBuffer)
        assert output.stdout == "stdout content"
        assert output.stderr == "stderr content"
        assert maya_client._stdout_buffer == ""
        assert maya_client._stderr_buffer == ""

    def test_get_accumulated_output_no_clear(self, maya_client: MayaClient) -> None:
        """Test get_accumulated_output with clear=False."""
        maya_client._stdout_buffer = "stdout content"
        maya_client._stderr_buffer = "stderr content"

        output = maya_client.get_accumulated_output(clear=False)

        assert output.stdout == "stdout content"
        assert output.stderr == "stderr content"
        assert maya_client._stdout_buffer == "stdout content"
        assert maya_client._stderr_buffer == "stderr content"

    def test_clear_output(self, maya_client: MayaClient) -> None:
        """Test clear_output."""
        maya_client._stdout_buffer = "stdout"
        maya_client._stderr_buffer = "stderr"

        maya_client.clear_output()

        assert maya_client._stdout_buffer == ""
        assert maya_client._stderr_buffer == ""


class TestSessionInfo:
    """Test session_info method."""

    @pytest.mark.asyncio
    async def test_session_info(self, maya_client: MayaClient, mocker) -> None:
        """Test session_info returns SessionInfo dataclass."""
        mock_send_receive = mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(
                result={
                    "pid": 12345,
                    "user": "testuser",
                    "maya_version": "2024",
                    "scene_name": "test.ma",
                    "scene_path": "/path/to/test.ma",
                },
                error=None,
            ),
        )

        info = await maya_client.session_info()

        assert isinstance(info, SessionInfo)
        assert info.host == "127.0.0.1"
        assert info.port == 7001
        assert info.pid == 12345
        assert info.user == "testuser"
        assert info.maya_version == "2024"
        assert info.scene_name == "test.ma"
        assert info.scene_path == "/path/to/test.ma"
        mock_send_receive.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_info_empty_response(self, maya_client: MayaClient, mocker) -> None:
        """Test session_info handles empty response."""
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(result=None, error=None),
        )

        info = await maya_client.session_info()

        assert info.host == "127.0.0.1"
        assert info.port == 7001
        assert info.pid == 0
        assert info.user == ""


class TestGetBufferedOutput:
    """Test get_buffered_output method."""

    @pytest.mark.asyncio
    async def test_get_buffered_output(self, maya_client: MayaClient, mocker) -> None:
        """Test get_buffered_output returns OutputBuffer."""
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(
                result={"stdout": "hello\n", "stderr": "warning\n"},
                error=None,
            ),
        )

        output = await maya_client.get_buffered_output()

        assert isinstance(output, OutputBuffer)
        assert output.stdout == "hello\n"
        assert output.stderr == "warning\n"

    @pytest.mark.asyncio
    async def test_get_buffered_output_empty(self, maya_client: MayaClient, mocker) -> None:
        """Test get_buffered_output handles empty response."""
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(result=None, error=None),
        )

        output = await maya_client.get_buffered_output()

        assert output.stdout == ""
        assert output.stderr == ""


class TestStreamCapture:
    """Test stream capture methods."""

    @pytest.mark.asyncio
    async def test_install_stream_capture(self, maya_client: MayaClient, mocker) -> None:
        """Test install_stream_capture calls _send_receive."""
        mock_send_receive = mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(result={"success": True}, error=None),
        )

        await maya_client.install_stream_capture()

        mock_send_receive.assert_called_once_with(maya_client.INSTALL_STREAM_CAPTURE)

    @pytest.mark.asyncio
    async def test_uninstall_stream_capture(self, maya_client: MayaClient, mocker) -> None:
        """Test uninstall_stream_capture calls _send_receive."""
        mock_send_receive = mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(result={"success": True}, error=None),
        )

        await maya_client.uninstall_stream_capture()

        mock_send_receive.assert_called_once_with(maya_client.UNINSTALL_STREAM_CAPTURE)


class TestExecuteCode:
    """Test execute_code method."""

    @pytest.mark.asyncio
    async def test_execute_code_none_result_type(self, maya_client: MayaClient, mocker) -> None:
        """Test execute_code with NONE result type."""
        mock_send_receive = mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(result=None, error=None),
        )

        result = await maya_client.execute_code("print('hello')", result_type=ResultType.NONE)

        assert result.result is None
        assert result.error is None
        mock_send_receive.assert_called_once_with(
            maya_client.EXECUTE_TEMPLATE,
            {"code": "print('hello')", "result_type": "NONE"},
            raise_on_error=False,
        )

    @pytest.mark.asyncio
    async def test_execute_code_raw_result_type(self, maya_client: MayaClient, mocker) -> None:
        """Test execute_code with RAW result type."""
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(result=42, error=None),
        )

        result = await maya_client.execute_code("21 * 2", result_type=ResultType.RAW)

        assert result.result == 42
        assert result.error is None

    @pytest.mark.asyncio
    async def test_execute_code_json_result_type_decodes(
        self, maya_client: MayaClient, mocker
    ) -> None:
        """Test execute_code with JSON result type decodes JSON."""
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(
                result='["item1", "item2"]',
                error=None,
            ),
        )

        result = await maya_client.execute_code("get_list()", result_type=ResultType.JSON)

        assert result.result == ["item1", "item2"]
        assert result.error is None

    @pytest.mark.asyncio
    async def test_execute_code_json_invalid_keeps_string(
        self, maya_client: MayaClient, mocker
    ) -> None:
        """Test execute_code with JSON result type keeps invalid JSON as string."""
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(result="not valid json", error=None),
        )

        result = await maya_client.execute_code("get_str()", result_type=ResultType.JSON)

        assert result.result == "not valid json"

    @pytest.mark.asyncio
    async def test_execute_code_with_error(self, maya_client: MayaClient, mocker) -> None:
        """Test execute_code returns error in response."""
        error_info = {
            "type": "builtins.NameError",
            "message": "name 'undefined' is not defined",
            "traceback": "Traceback...",
        }
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(result=None, error=error_info),
        )

        result = await maya_client.execute_code("undefined")

        assert result.result is None
        assert result.error == error_info


# ============================================================================
# MayaClient-specific Tests
# ============================================================================


class TestMayaClientWriteModule:
    """Test MayaClient write_module method."""

    @pytest.mark.asyncio
    async def test_write_module(self, maya_client: MayaClient, mocker) -> None:
        """Test write_module returns success message."""
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(
                result={"message": "Module 'mymodule' created"},
                error=None,
            ),
        )

        result = await maya_client.write_module("mymodule", "x = 1")

        assert result == "Module 'mymodule' created"

    @pytest.mark.asyncio
    async def test_write_module_default_message(self, maya_client: MayaClient, mocker) -> None:
        """Test write_module returns default message on empty response."""
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(result=None, error=None),
        )

        result = await maya_client.write_module("mymodule", "x = 1")

        assert result == "Module 'mymodule' created"


class TestMayaClientPing:
    """Test MayaClient ping method."""

    @pytest.mark.asyncio
    async def test_ping_success(self, maya_client: MayaClient, mocker) -> None:
        """Test ping returns True when Maya responds correctly."""
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(result="2", error=None),
        )

        result = await maya_client.ping()

        assert result is True

    @pytest.mark.asyncio
    async def test_ping_wrong_response(self, maya_client: MayaClient, mocker) -> None:
        """Test ping returns False when Maya responds incorrectly."""
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(result="wrong", error=None),
        )

        result = await maya_client.ping()

        assert result is False

    @pytest.mark.asyncio
    async def test_ping_exception(self, maya_client: MayaClient, mocker) -> None:
        """Test ping returns False on exception."""
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            side_effect=MayaExecutionError("Connection lost"),
        )

        result = await maya_client.ping()

        assert result is False


# ============================================================================
# MayaQtClient-specific Tests
# ============================================================================


class TestMayaQtClientWriteModule:
    """Test MayaQtClient write_module method."""

    @pytest.mark.asyncio
    async def test_write_module(self, qt_client: MayaQtClient, mocker) -> None:
        """Test write_module returns success message."""
        mocker.patch.object(
            qt_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(
                result={"message": "Module 'mymodule' created"},
                error=None,
            ),
        )

        result = await qt_client.write_module("mymodule", "x = 1", overwrite=True)

        assert result == "Module 'mymodule' created"


class TestMayaQtClientPing:
    """Test MayaQtClient ping method."""

    @pytest.mark.asyncio
    async def test_ping_success(self, qt_client: MayaQtClient, mocker) -> None:
        """Test ping returns True when server responds with pong."""
        mocker.patch.object(
            qt_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(result="pong", error=None),
        )

        result = await qt_client.ping()

        assert result is True

    @pytest.mark.asyncio
    async def test_ping_wrong_response(self, qt_client: MayaQtClient, mocker) -> None:
        """Test ping returns False on wrong response."""
        mocker.patch.object(
            qt_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(result="wrong", error=None),
        )

        result = await qt_client.ping()

        assert result is False

    @pytest.mark.asyncio
    async def test_ping_exception(self, qt_client: MayaQtClient, mocker) -> None:
        """Test ping returns False on exception."""
        mocker.patch.object(
            qt_client,
            "_send_receive",
            new_callable=AsyncMock,
            side_effect=MayaExecutionError("Connection lost"),
        )

        result = await qt_client.ping()

        assert result is False


# ============================================================================
# MayaQtClient session_info and execute_code (ensure they work same as base)
# ============================================================================


class TestMayaQtClientSessionInfo:
    """Test MayaQtClient session_info method."""

    @pytest.mark.asyncio
    async def test_session_info(self, qt_client: MayaQtClient, mocker) -> None:
        """Test session_info returns SessionInfo dataclass."""
        mocker.patch.object(
            qt_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(
                result={
                    "pid": 99999,
                    "user": "qtuser",
                    "maya_version": "2025",
                    "scene_name": "qt_scene.ma",
                    "scene_path": "/qt/path/scene.ma",
                },
                error=None,
            ),
        )

        info = await qt_client.session_info()

        assert isinstance(info, SessionInfo)
        assert info.host == "127.0.0.1"
        assert info.port == 50000
        assert info.pid == 99999
        assert info.user == "qtuser"
        assert info.maya_version == "2025"


class TestMayaQtClientExecuteCode:
    """Test MayaQtClient execute_code method."""

    @pytest.mark.asyncio
    async def test_execute_code_json(self, qt_client: MayaQtClient, mocker) -> None:
        """Test execute_code with JSON result type."""
        mock_send_receive = mocker.patch.object(
            qt_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(
                result='{"key": "value"}',
                error=None,
            ),
        )

        result = await qt_client.execute_code("get_dict()", result_type=ResultType.JSON)

        assert result.result == {"key": "value"}
        mock_send_receive.assert_called_once_with(
            qt_client.EXECUTE_TEMPLATE,
            {"code": "get_dict()", "result_type": "JSON"},
            raise_on_error=False,
        )


# ============================================================================
# T-05: typed errors + write_module dead-response fix
# ============================================================================


class TestTypedErrors:
    """D-013/D-019: typed channel errors keep [code] prefixes."""

    def test_connection_error_is_unavailable_alias(self) -> None:
        """MayaConnectionError is kept as the backwards-compat name."""
        assert MayaConnectionError is MayaUnavailableError

    def test_unavailable_code(self) -> None:
        err = MayaUnavailableError("no route")
        assert err.code == "maya_unavailable"
        assert "[maya_unavailable]" in str(err)

    def test_timeout_is_subclass_of_unavailable(self) -> None:
        assert issubclass(MayaTimeoutError, MayaUnavailableError)

    @pytest.mark.asyncio
    async def test_native_send_receive_timeout_is_typed(self, maya_client: MayaClient) -> None:
        """commandPort read timeout raises MayaTimeoutError, not a generic error."""
        maya_client.timeout = 0.05
        maya_client._writer.drain = AsyncMock()

        class _SlowReader:
            async def read(self, n: int) -> bytes:
                await asyncio.sleep(5)
                return b""

        maya_client._reader = _SlowReader()

        with pytest.raises(MayaTimeoutError) as ei:
            await maya_client._send_receive("1+1")
        assert ei.value.code == "maya_timeout"
        assert isinstance(ei.value, MayaConnectionError)

    @pytest.mark.asyncio
    async def test_native_send_receive_closed_is_unavailable(self, maya_client: MayaClient) -> None:
        """D-041: a commandPort connection that closes mid-call maps to
        MayaUnavailableError, matching the Qt channel's typed error."""
        maya_client._writer.drain = AsyncMock()

        class _ClosedReader:
            async def read(self, n: int) -> bytes:
                return b""

        maya_client._reader = _ClosedReader()

        with pytest.raises(MayaUnavailableError) as ei:
            await maya_client._send_receive("1+1")
        assert ei.value.code == "maya_unavailable"
        assert isinstance(ei.value, MayaConnectionError)

    @pytest.mark.asyncio
    async def test_native_send_appends_newline_terminator(self, maya_client: MayaClient) -> None:
        """D-041: commandPort executes on newline - every send carries the
        unconditional newline terminator (off-by-one guard, audit T-11 F1)."""
        maya_client._writer.drain = AsyncMock()

        class _OneShotReader:
            def __init__(self) -> None:
                self._done = False

            async def read(self, n: int) -> bytes:
                if self._done:
                    return b""
                self._done = True
                return b'{"result": 2}\n\x00'

        maya_client._reader = _OneShotReader()

        await maya_client._send_receive("1+1")

        maya_client._writer.write.assert_called_once_with(b"1+1\n")


class TestMayaClientWriteModuleDeadResponse:
    """T-05/D-014c: native create_module returns errors INSIDE the result
    payload - write_module must surface them instead of silently succeeding."""

    @pytest.mark.asyncio
    async def test_inner_error_dict_raises(self, maya_client: MayaClient, mocker) -> None:
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(
                result={
                    "error": {
                        "code": "module_exists",
                        "message": "Module 'mymodule' already exists. Use overwrite=True.",
                    }
                },
                error=None,
            ),
        )

        with pytest.raises(MayaExecutionError, match="already exists"):
            await maya_client.write_module("mymodule", "x = 1")

    @pytest.mark.asyncio
    async def test_inner_error_json_string_raises(self, maya_client: MayaClient, mocker) -> None:
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(
                result='{"error": {"code": "module_exists", "message": "Module X exists"}}',
                error=None,
            ),
        )

        with pytest.raises(MayaExecutionError, match="exists"):
            await maya_client.write_module("x", "y = 2")

    @pytest.mark.asyncio
    async def test_json_string_success_returns_message(
        self, maya_client: MayaClient, mocker
    ) -> None:
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(
                result='{"success": true, "message": "Module mymodule created"}',
                error=None,
            ),
        )

        result = await maya_client.write_module("mymodule", "x = 1")

        assert result == "Module mymodule created"


class TestBootstrapFallback:
    """D-013 headless fallback, pinned to REAL wire shapes.

    On the native commandPort, start_qt_server()'s JSON payload surfaces as:
      - success: CommandResponse(result={"port": N, "already_running": b})
      - failure: CommandResponse(result=None, error={...})  -- only reachable
        because bootstrap calls _send_receive(raise_on_error=False); the mock
        below asserts that contract.
    """

    @pytest.mark.asyncio
    async def test_qt_start_error_response_falls_back_to_native(
        self, maya_client: MayaClient, mocker
    ) -> None:
        calls: list[tuple[str, bool]] = []

        async def fake_send(method, params=None, raise_on_error=True):
            calls.append((method, raise_on_error))
            if method == maya_client.START_QT_SERVER:
                # real wire: {"error": {...}} JSON -> CommandResponse(error=dict)
                return CommandResponse(
                    result=None,
                    error={
                        "code": "qt_unavailable_headless",
                        "message": "Qt event loop unavailable (headless Maya)",
                    },
                )
            if method == maya_client.START_COMMAND_PORT:
                return CommandResponse(result={"success": True, "port": params["port"]}, error=None)
            return CommandResponse(result=None, error=None)

        mocker.patch.object(maya_client, "_bootstrap", new=AsyncMock())
        mocker.patch.object(maya_client, "_send_receive", side_effect=fake_send)
        mocker.patch("maya_mcp_server.client.MayaClient.connect", new=AsyncMock())
        mocker.patch("maya_mcp_server.client.asyncio.sleep", new=AsyncMock())

        new_client = await maya_client.bootstrap(client_type="qt")

        assert type(new_client) is MayaClient
        assert new_client.framed_channel is False
        # contract pin: the Qt start probe must not raise on domain errors
        assert (maya_client.START_QT_SERVER, False) in calls
        assert maya_client.START_COMMAND_PORT in [m for m, _ in calls]

    @pytest.mark.asyncio
    async def test_qt_zombie_channel_falls_back_to_native(
        self, maya_client: MayaClient, mocker
    ) -> None:
        """listen() succeeding without an event loop = zombie channel: TCP
        connect works but readyRead never fires -> ping False -> fallback."""

        async def fake_send(method, params=None, raise_on_error=True):
            if method == maya_client.START_QT_SERVER:
                return CommandResponse(
                    result={"port": 55_501, "already_running": False}, error=None
                )
            if method == maya_client.START_COMMAND_PORT:
                return CommandResponse(result={"success": True, "port": params["port"]}, error=None)
            return CommandResponse(result=None, error=None)

        mocker.patch.object(maya_client, "_bootstrap", new=AsyncMock())
        mocker.patch.object(maya_client, "_send_receive", side_effect=fake_send)
        mocker.patch("maya_mcp_server.client.MayaClient.connect", new=AsyncMock())
        mocker.patch("maya_mcp_server.client.MayaQtClient.connect", new=AsyncMock())
        # The zombie: TCP connected, protocol dead -> ping returns False
        mocker.patch(
            "maya_mcp_server.client.MayaQtClient.ping",
            new=AsyncMock(return_value=False),
        )
        mocker.patch("maya_mcp_server.client.asyncio.sleep", new=AsyncMock())

        new_client = await maya_client.bootstrap(client_type="qt")

        assert type(new_client) is MayaClient
        assert new_client.framed_channel is False

    @pytest.mark.asyncio
    async def test_qt_success_uses_framed_client(self, maya_client: MayaClient, mocker) -> None:
        qt_port = 55_555

        async def fake_send(method, params=None, raise_on_error=True):
            if method == maya_client.START_QT_SERVER:
                return CommandResponse(
                    result={"port": qt_port, "already_running": False}, error=None
                )
            return CommandResponse(result=None, error=None)

        mocker.patch.object(maya_client, "_bootstrap", new=AsyncMock())
        mocker.patch.object(maya_client, "_send_receive", side_effect=fake_send)
        mocker.patch("maya_mcp_server.client.MayaQtClient.connect", new=AsyncMock())
        mocker.patch(
            "maya_mcp_server.client.MayaQtClient.ping",
            new=AsyncMock(return_value=True),
        )
        mocker.patch("maya_mcp_server.client.asyncio.sleep", new=AsyncMock())

        new_client = await maya_client.bootstrap(client_type="qt")

        assert isinstance(new_client, MayaQtClient)
        assert new_client.port == qt_port
        assert new_client.framed_channel is True


# ============================================================================
# D-046: result_type coercion at the transport seam
# ============================================================================


class _CaptureWriter:
    """Minimal StreamWriter stand-in: captures every written byte string."""

    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None

    def is_closing(self) -> bool:
        return False


class _ScriptedReader:
    """Minimal StreamReader stand-in serving one pre-canned byte blob."""

    def __init__(self, blob: bytes) -> None:
        self._buf = blob

    async def readexactly(self, n: int) -> bytes:
        chunk, self._buf = self._buf[:n], self._buf[n:]
        if len(chunk) < n:
            raise asyncio.IncompleteReadError(chunk, n)
        return chunk

    async def read(self, n: int = -1) -> bytes:
        if n < 0 or n > len(self._buf):
            n = len(self._buf)
        chunk, self._buf = self._buf[:n], self._buf[n:]
        return chunk


class TestExecuteCodeResultTypeCoercion:
    """D-046 regression net: drive the REAL _send_receive over a fake
    transport and assert the wire payload.

    0.1.1 shipped a ship-stopper: str-typed callers (scene_tools.py:124,
    visual_tools.py:86 pass result_type="JSON") crashed inside
    execute_code on ``result_type.value``. Every pre-existing test mocked
    _send_receive, so the crash never surfaced in CI. These tests pin the
    wire-level contract for BOTH channels (framed Qt + native commandPort).
    """

    @pytest.mark.asyncio
    async def test_qt_execute_code_accepts_str_result_type(self) -> None:
        """Qt channel: str "JSON" must serialize to params.result_type."""
        client = MayaQtClient(host="127.0.0.1", port=1)
        writer = _CaptureWriter()
        response = helper.encode_frame(
            json.dumps({"id": "req-1", "result": "{}", "error": None}).encode()
        )
        client._writer = writer  # type: ignore[assignment]
        client._reader = _ScriptedReader(response)  # type: ignore[assignment]

        result = await client.execute_code("{}", result_type="JSON")

        assert result.error is None
        frame = writer.writes[0]
        request = json.loads(frame[helper.FRAME_HEADER_SIZE :].decode())
        assert request["method"] == "execute"
        assert request["params"]["result_type"] == "JSON"

    @pytest.mark.asyncio
    async def test_native_execute_code_accepts_str_result_type(self) -> None:
        """Native channel: str "JSON" must format into the command template."""
        client = MayaClient(host="127.0.0.1", port=1)
        writer = _CaptureWriter()
        client._writer = writer  # type: ignore[assignment]
        client._reader = _ScriptedReader(  # type: ignore[assignment]
            b'{"result": 1, "error": null}\x00'
        )

        result = await client.execute_code("1+1", result_type="JSON")

        assert result.error is None
        command = writer.writes[0].decode()
        assert command.endswith("\n")
        assert "'JSON'" in command  # execute('1+1', 'JSON')

    @pytest.mark.asyncio
    async def test_qt_execute_code_enum_still_works(self) -> None:
        """Enum input keeps working through the real transport path."""
        client = MayaQtClient(host="127.0.0.1", port=1)
        writer = _CaptureWriter()
        response = helper.encode_frame(
            json.dumps({"id": "req-1", "result": None, "error": None}).encode()
        )
        client._writer = writer  # type: ignore[assignment]
        client._reader = _ScriptedReader(response)  # type: ignore[assignment]

        result = await client.execute_code("pass", result_type=ResultType.NONE)

        assert result.error is None
        request = json.loads(writer.writes[0][helper.FRAME_HEADER_SIZE :].decode())
        assert request["params"]["result_type"] == "NONE"

    @pytest.mark.asyncio
    async def test_execute_code_rejects_invalid_str(self, maya_client: MayaClient) -> None:
        """Invalid result_type strings surface as InputValidationError."""
        with pytest.raises(InputValidationError, match="Invalid result_type"):
            await maya_client.execute_code("pass", result_type="BOGUS")


class TestDetectPortType:
    """D-059: eval("1/2") is a bilingual probe - Python answers 0.5 while
    MEL eval() integer-divides to 0, so a MEL commandPort replies without
    a Script Editor error (upstream issue #1)."""

    @pytest.mark.asyncio
    async def test_python_port_answers_05(self, maya_client: MayaClient, mocker) -> None:
        spy = mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(result="0.5", error=None),
        )
        assert await maya_client._detect_port_type() is PortType.PYTHON
        spy.assert_awaited_once_with('eval("1/2")')

    @pytest.mark.asyncio
    async def test_embedded_05_still_python(self, maya_client: MayaClient, mocker) -> None:
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(result="Result: 0.5", error=None),
        )
        assert await maya_client._detect_port_type() is PortType.PYTHON

    @pytest.mark.asyncio
    async def test_mel_port_answers_0(self, maya_client: MayaClient, mocker) -> None:
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(result="0", error=None),
        )
        assert await maya_client._detect_port_type() is PortType.MEL

    @pytest.mark.asyncio
    async def test_silent_answer_is_mel(self, maya_client: MayaClient, mocker) -> None:
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            return_value=CommandResponse(result="", error=None),
        )
        assert await maya_client._detect_port_type() is PortType.MEL

    @pytest.mark.asyncio
    async def test_probe_exception_is_unknown(self, maya_client: MayaClient, mocker) -> None:
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            side_effect=MayaTimeoutError("timeout"),
        )
        assert await maya_client._detect_port_type() is PortType.UNKNOWN


class TestBootstrapHotUpdateWarning:
    """N1 (D-058 tail): the _bootstrap hot-update overwrite path must
    surface a _mcp_teardown warning from create_module - the module is
    still replaced, but a failed cleanup must not pass silently."""

    @staticmethod
    def _hot_update_responses(warning, build=None):
        """CHECK_BOOTSTRAP -> build probe -> exec(bootstrap) -> create_module.

        build=None makes the installed helper look older/absent so the
        update path always reaches create_module in these tests."""
        create_result = {"success": True, "message": "Module 'maya_mcp' created"}
        if warning is not None:
            create_result["warning"] = warning
        return [
            CommandResponse(result="True", error=None),
            CommandResponse(result=build, error=None),
            CommandResponse(result=None, error=None),
            CommandResponse(result=create_result, error=None),
        ]

    @pytest.mark.asyncio
    async def test_teardown_warning_is_logged(
        self, maya_client: MayaClient, mocker, caplog: pytest.LogCaptureFixture
    ) -> None:
        maya_client._port_type = PortType.PYTHON
        warning = "_mcp_teardown of 'maya_mcp' raised RuntimeError: boom"
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            side_effect=self._hot_update_responses(warning),
        )
        with caplog.at_level(logging.WARNING, logger="maya_mcp_server.client"):
            await maya_client._bootstrap()
        assert any(warning in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_clean_update_logs_no_module_warning(
        self, maya_client: MayaClient, mocker, caplog: pytest.LogCaptureFixture
    ) -> None:
        maya_client._port_type = PortType.PYTHON
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            side_effect=self._hot_update_responses(None),
        )
        with caplog.at_level(logging.WARNING, logger="maya_mcp_server.client"):
            await maya_client._bootstrap()
        assert not any("Module 'maya_mcp'" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_module_create_failed_raises_not_swallowed(
        self, maya_client: MayaClient, mocker, caplog: pytest.LogCaptureFixture
    ) -> None:
        """N7: a module_create_failed payload on the hot-update path must
        raise - it arrives inside result (the native commandPort wrap),
        not response.error - instead of logging a false "updated"."""
        maya_client._port_type = PortType.PYTHON
        payload = {
            "error": {
                "code": "module_create_failed",
                "message": "Failed to compile/execute module 'maya_mcp': SyntaxError: x",
            }
        }
        responses = [
            CommandResponse(result="True", error=None),
            CommandResponse(result=None, error=None),  # build probe: absent
            CommandResponse(result=None, error=None),
            CommandResponse(result=payload, error=None),
        ]
        mocker.patch.object(
            maya_client, "_send_receive", new_callable=AsyncMock, side_effect=responses
        )
        with caplog.at_level(logging.INFO, logger="maya_mcp_server.client"):
            with pytest.raises(MayaExecutionError, match="module_create_failed"):
                await maya_client._bootstrap()
        assert not any("module updated" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_matching_source_sha_skips_rewrite(self, maya_client: MayaClient, mocker) -> None:
        """T-18a/F3: when the injected helper's _mcp_source_sha already
        matches sha256 of the current source, the >10K hot-update write is
        skipped - that write is what trips the commandPort stale-response
        quirk (orphaned Qt servers). The stamp is a content hash, so a
        forgotten manual bump cannot silently mask a stale helper."""
        from maya_mcp_server.bootstrap import get_helper_module_code

        expected = hashlib.sha256(get_helper_module_code().encode()).hexdigest()[:16]
        maya_client._port_type = PortType.PYTHON
        spy = mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            side_effect=[
                CommandResponse(result="True", error=None),
                CommandResponse(result=expected, error=None),
            ],
        )
        await maya_client._bootstrap()
        assert spy.await_count == 2  # CHECK_BOOTSTRAP + sha probe only

    @pytest.mark.asyncio
    async def test_bootstrap_stamps_source_sha(self, maya_client: MayaClient, mocker) -> None:
        """The injected module carries _mcp_source_sha = sha256(source)[:16]
        appended post-hash - self-describing, nothing to bump manually."""
        from maya_mcp_server.bootstrap import get_helper_module_code

        maya_client._port_type = PortType.PYTHON
        spy = mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            side_effect=[
                CommandResponse(result="False", error=None),
                CommandResponse(result=None, error=None),
                CommandResponse(result=None, error=None),
            ],
        )
        await maya_client._bootstrap()
        create_call = spy.await_args_list[-1]
        sent_code = create_call.args[1]["code"]  # _send_receive(cmd, params)
        src = get_helper_module_code()
        sha = hashlib.sha256(src.encode()).hexdigest()[:16]
        assert sent_code.startswith(src)
        assert f"_mcp_source_sha = '{sha}'" in sent_code

    @pytest.mark.asyncio
    async def test_native_bootstrap_tags_dedicated_port(
        self, maya_client: MayaClient, mocker
    ) -> None:
        """F1 (audit): the explicit client_type=native branch must tag the
        dedicated commandPort so disconnect() closes it - the leak was
        only fixed on the Qt-fallback branch."""
        maya_client._port_type = PortType.PYTHON
        mocker.patch.object(
            maya_client,
            "_send_receive",
            new_callable=AsyncMock,
            side_effect=[
                CommandResponse(result="True", error=None),  # CHECK_BOOTSTRAP
                CommandResponse(result="x" * 16, error=None),  # sha mismatch -> rewrite
                CommandResponse(result=None, error=None),  # exec bootstrap
                CommandResponse(result={}, error=None),  # create_module
                CommandResponse(result=None, error=None),  # START_COMMAND_PORT
            ],
        )
        mocker.patch.object(MayaClient, "connect", new_callable=AsyncMock)
        new_client = await maya_client.bootstrap(client_type="native")
        assert getattr(new_client, "_dedicated_port", None) is not None


# ============================================================================
# module_call / exec_module_code (D-098)
# ============================================================================


class TestModuleCall:
    """Shared call-construction primitives (D-098).

    Payload JSON shape + module-name embedding are the P0-2
    injection-safety regression anchor: user input must ride inside a
    JSON document, never become executable source text.
    """

    @staticmethod
    def _embedded_literal(code: str) -> tuple[str, int, int]:
        """Locate the embedded JSON string literal -> (literal, start, end)."""
        start = code.index("json.loads(") + len("json.loads(")
        assert code[start] == '"'
        i = start + 1
        while True:
            if code[i] == '"':
                backslashes = 0
                while code[i - 1 - backslashes] == "\\":
                    backslashes += 1
                if backslashes % 2 == 0:
                    break
            i += 1
        return code[start : i + 1], start, i + 1

    @classmethod
    def _embedded_payload(cls, code: str) -> dict:
        literal, _, _ = cls._embedded_literal(code)
        return json.loads(json.loads(literal))

    def test_payload_json_shape_and_module_embedding(self) -> None:
        code = module_call("_mcp_scene", "get_scene_graph", "detailed", flag=True)
        assert code.startswith("import json, _mcp_scene; _a = json.loads(")
        assert code.endswith("_mcp_scene.get_scene_graph(*_a['args'], **_a['kwargs'])")
        assert self._embedded_payload(code) == {
            "args": ["detailed"],
            "kwargs": {"flag": True},
        }

    def test_module_name_varies_per_domain(self) -> None:
        assert "import json, _mcp_visual" in module_call("_mcp_visual", "capture")
        assert "_mcp_visual.capture" in module_call("_mcp_visual", "capture")
        assert "_mcp_export.export_scene" in module_call("_mcp_export", "export_scene")

    def test_none_and_bool_survive_json_roundtrip(self) -> None:
        code = module_call("_mcp_scene", "scene_plan", objective=None, auto_fix=True)
        assert self._embedded_payload(code)["kwargs"] == {
            "objective": None,
            "auto_fix": True,
        }

    def test_user_input_roundtrips_inside_json_only(self) -> None:
        hostile = "'); import os; os.system('x'); #"
        code = module_call("_mcp_scene", "scene_plan", objective=hostile)
        payload = self._embedded_payload(code)
        assert payload["kwargs"]["objective"] == hostile
        literal, start, end = self._embedded_literal(code)
        template = code[:start] + code[end:]
        assert "os.system" not in template
        assert len(literal) > 0

    @pytest.mark.asyncio
    async def test_exec_decodes_json_string_result(self) -> None:
        client = AsyncMock()
        client.execute_code.return_value = CommandResponse(result='{"a": 1}', error=None)
        assert await exec_module_code(client, "c") == {"a": 1}
        client.execute_code.assert_awaited_once_with("c", result_type="JSON")

    @pytest.mark.asyncio
    async def test_exec_passthrough_dict_result(self) -> None:
        client = AsyncMock()
        client.execute_code.return_value = CommandResponse(result={"ok": 2}, error=None)
        assert await exec_module_code(client, "c") == {"ok": 2}

    @pytest.mark.asyncio
    async def test_exec_raises_on_domain_error(self) -> None:
        client = AsyncMock()
        client.execute_code.return_value = CommandResponse(
            result=None,
            error={"type": "builtins.ValueError", "message": "bad thing"},
        )
        with pytest.raises(MayaExecutionError):
            await exec_module_code(client, "c")
