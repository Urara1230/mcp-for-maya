"""Maya client for communicating with Maya command ports."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from typing_extensions import Self

from maya_mcp_server.bootstrap import (
    get_bootstrap_code,
    get_helper_module_code,
)
from maya_mcp_server.maya_mcp_helper import (
    FRAME_HEADER_SIZE,
    MAX_FRAME_SIZE,
    encode_frame,
)
from maya_mcp_server.security import (
    InputValidationError,
    PipelineError,
    sanitize_error_message,
)
from maya_mcp_server.types import (
    COMMUNICATION_PORT_MAX,
    COMMUNICATION_PORT_MIN,
    CommandResponse,
    OutputBuffer,
    PortType,
    ResultType,
    SessionInfo,
)


if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _result_str(response: CommandResponse) -> str:
    """Response payload as stripped text ('' for missing/None)."""
    return str(response.result if response.result is not None else "").strip()


# Buffer size limit to prevent unbounded memory growth (10MB)
MAX_BUFFER_SIZE = 10_485_760

# Connect retry schedule (D-013): exponential backoff 0.5s / 1s / 2s
CONNECT_RETRY_DELAYS = (0.5, 1.0, 2.0)

# Post-connect liveness gate: a Qt server in headless Maya can accept TCP
# while readyRead never fires; bound the probe so bootstrap fails fast.
QT_PROBE_TIMEOUT = 5.0  # seconds
QT_CONNECT_ATTEMPTS = 3  # connect+ping retries before native fallback


class MayaUnavailableError(PipelineError):
    """Maya channel unreachable (host-side failure -> isError + code)."""

    code = "maya_unavailable"


# Backwards-compatible name: D-019 pins the code, existing code the class name.
MayaConnectionError = MayaUnavailableError


class MayaTimeoutError(MayaUnavailableError):
    """Timed out waiting on the Maya channel."""

    code = "maya_timeout"


class MayaExecutionError(PipelineError):
    """Error executing code in Maya (host-side failure -> isError + code)."""

    code = "maya_execution"


def _domain_error_text(error: Any) -> str:
    """Render a wire error as "code: message" (D-019).

    Wire errors are dual-schema: known domain errors carry {"code": ...}
    and exceptions carry {"type", "traceback"}. The code must survive into
    the exception text so both channels match native write_module's
    "code: message" format.
    """
    if isinstance(error, dict):
        code = error.get("code")
        msg = error.get("message", "Unknown error")
        if code:
            return f"{code}: {msg}"
        return str(msg)
    return str(error)


def raise_for_error(response: CommandResponse) -> None:
    """Raise MayaExecutionError if the response carries a Maya-side error.

    Single choke point so both server.execute_code and the scene-tool layer
    surface errors identically (message sanitized, code/type + traceback kept).
    """
    err = getattr(response, "error", None)
    if not err:
        return
    if isinstance(err, dict) and err.get("code"):
        detail = f"{err['code']}: {err.get('message', 'Unknown error')}"
        etb = err.get("traceback", "")
    else:
        etype = err.get("type", "Error") if isinstance(err, dict) else "Error"
        emsg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        etb = err.get("traceback", "") if isinstance(err, dict) else ""
        detail = f"Maya execution error ({etype}): {emsg}"
    if etb:
        detail += "\n" + etb
    raise MayaExecutionError(sanitize_error_message(detail))


async def ensure_module_injected(
    client: BaseMayaClient,
    session_key: str | None,
    *,
    module_name: str,
    source_path: Path,
    tmp_prefix: str,
    injected_sessions: set[str],
    extra_source_paths: list[Path] | None = None,
) -> None:
    """Ensure a Maya-side helper module is injected into the session.

    Shared by _mcp_scene/_mcp_asset (D-083): reads the module source
    from disk and writes it via write_module, once per session. For
    large modules on the NATIVE (headless/bootstrap) channel the source
    is staged through a unique mkstemp file to avoid command-port
    buffer issues; the Qt framed channel carries length-prefixed frames
    up to 16 MiB, so GUI sessions inject directly via write_module
    (D-013).

    extra_source_paths (D-095): additional source files concatenated
    into the SAME module payload before writing - one module name, one
    namespace, one failure domain. Fragment files
    must carry no __future__ import (it lands mid-file = SyntaxError).
    """
    key = session_key or "_default"
    if key in injected_sessions:
        return

    source = source_path.read_text(encoding="utf-8")
    for extra in extra_source_paths or ():
        source += "\n\n" + extra.read_text(encoding="utf-8")

    if len(source) > 15000 and not getattr(client, "framed_channel", False):
        import os as _os
        import tempfile as _tf

        import platformdirs as _pd

        # D-082a hygiene: mkstemp under the platformdirs user cache (no
        # predictable shared-temp path), 0600 perms, try/finally unlink.
        _dir = _os.path.join(_pd.user_cache_dir("mcp-for-maya"), "inject")
        _os.makedirs(_dir, exist_ok=True)
        _fd, _tmp = _tf.mkstemp(prefix=tmp_prefix, suffix=".py", dir=_dir)
        try:
            with _os.fdopen(_fd, "w", encoding="utf-8") as f:
                f.write(source)
            _os.chmod(_tmp, 0o600)
            _tmp_safe = _tmp.replace("\\", "/")
            await client.execute_code(
                "import types, sys, json; _f=open(json.loads("
                + json.dumps(json.dumps(_tmp_safe))
                + "), encoding='utf-8'); _c=_f.read(); _f.close();"
                f" _m=types.ModuleType('{module_name}');"
                f" _m.__file__='<mcp:{module_name}>';"
                f" exec(compile(_c,'{module_name}.py','exec'),_m.__dict__);"
                f" sys.modules['{module_name}']=_m",
                ResultType.NONE,
            )
        finally:
            try:
                _os.remove(_tmp)
            except OSError:
                pass
    else:
        await client.write_module(module_name, source, overwrite=True)
    # Pre-import the module so subsequent calls use expression-only syntax
    await client.execute_code(f"import {module_name}", ResultType.NONE)
    injected_sessions.add(key)
    logger.info("Injected %s module into session %s", module_name, key)


def module_call(module: str, fn_name: str, *args: Any, **kwargs: Any) -> str:
    """Build Maya-side module call code with all arguments JSON-serialized.

    Arguments are embedded as a JSON document inside a Python string
    literal and reconstructed via json.loads inside Maya. This removes
    the entire string-interpolation injection surface — user input never
    becomes executable source text (P0-2).

    Shared call-construction primitive for every injected domain
    (_mcp_scene/_mcp_asset/_mcp_export/_mcp_visual), generalized from
    the per-domain _X_call copies (D-098). Only domain-agnostic call
    construction lives here; per-domain binding stays in each
    _ensure_X_injected.
    """
    payload = json.dumps({"args": list(args), "kwargs": kwargs})
    return (
        f"import json, {module}; _a = json.loads({json.dumps(payload)}); "
        f"{module}.{fn_name}(*_a['args'], **_a['kwargs'])"
    )


async def exec_module_code(client: BaseMayaClient, code: str) -> Any:
    """Execute injected-module call code and decode the JSON result.

    Shared executor for the module_call family (D-098): execute with
    result_type=JSON, raise on wire/domain errors, decode a string
    payload. Caching, when a domain wants it, wraps this no-cache core
    (scene_tools._execute_scene_code).
    """
    response = await client.execute_code(code, result_type="JSON")
    raise_for_error(response)
    data = response.result
    if isinstance(data, str):
        return json.loads(data)
    return data


@dataclass
class BaseMayaClient(ABC):
    """Abstract base class for Maya clients."""

    GET_SESSION_INFO: ClassVar[str]
    EXECUTE_TEMPLATE: ClassVar[str]
    CREATE_MODULE_TEMPLATE: ClassVar[str]
    INSTALL_STREAM_CAPTURE: ClassVar[str]
    UNINSTALL_STREAM_CAPTURE: ClassVar[str]
    GET_BUFFERED_OUTPUT: ClassVar[str]

    # True when the transport carries length-prefixed frames and can move
    # multi-MB payloads safely (D-013). False = native commandPort, whose
    # line protocol is bootstrap/headless-only and never promised large loads.
    framed_channel: ClassVar[bool] = False

    host: str = "127.0.0.1"
    port: int = 7001
    timeout: float = 30.0
    buffer_size: int = 65536

    def __post_init__(self) -> None:
        """
        Initialize a Maya client.

        Args:
            host: Maya host address
            port: Maya command port number
            timeout: Default timeout for operations in seconds
            buffer_size: Socket buffer size
        """
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        # Set by bootstrap()'s native-fallback path: a commandPort we opened
        # for this client and must close again on disconnect (T-18a).
        self._dedicated_port: int | None = None
        # Output buffers for stdout/stderr capture
        self._stdout_buffer: str = ""
        self._stderr_buffer: str = ""

    @property
    def key(self) -> str:
        """Unique key for the session."""
        return f"{self.host}:{self.port}"

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        """
        Establish connection to Maya command port.

        Raises:
            MayaConnectionError: If connection fails
        """
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.timeout,
            )
            logger.info(f"Connected to Maya at {self.host}:{self.port}")
        except asyncio.TimeoutError as e:
            raise MayaTimeoutError(f"Timeout connecting to Maya at {self.host}:{self.port}") from e
        except OSError as e:
            raise MayaConnectionError(
                f"Failed to connect to Maya at {self.host}:{self.port}: {e}"
            ) from e

    async def disconnect(self) -> None:
        """Close connection to Maya."""
        dedicated = self._dedicated_port
        if dedicated is not None and self._writer:
            # T-18a: close the dedicated commandPort bootstrap() opened for
            # this client - it previously stayed open forever (the FIXME).
            # Fire-and-forget: Maya drops this socket when the port dies.
            try:
                self._writer.write(
                    (
                        "import maya.cmds as cmds; "
                        f'cmds.commandPort(name=":{dedicated}", close=True)\n'
                    ).encode()
                )
                await self._writer.drain()
            except Exception:
                pass
            self._dedicated_port = None
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    async def __aenter__(self) -> Self:
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        """Async context manager exit."""
        await self.disconnect()

    def append_output(self, stdout: str = "", stderr: str = "") -> None:
        """
        Append output to the client's buffers.

        This is called after execute_code to accumulate output.

        Args:
            stdout: Stdout content to append
            stderr: Stderr content to append
        """
        if stdout:
            self._stdout_buffer += stdout
            if len(self._stdout_buffer) > MAX_BUFFER_SIZE:
                excess = len(self._stdout_buffer) - MAX_BUFFER_SIZE
                self._stdout_buffer = self._stdout_buffer[excess:]
                logger.warning(
                    f"Stdout buffer exceeded {MAX_BUFFER_SIZE} bytes, "
                    f"trimmed {excess} bytes from the beginning"
                )
        if stderr:
            self._stderr_buffer += stderr
            if len(self._stderr_buffer) > MAX_BUFFER_SIZE:
                excess = len(self._stderr_buffer) - MAX_BUFFER_SIZE
                self._stderr_buffer = self._stderr_buffer[excess:]
                logger.warning(
                    f"Stderr buffer exceeded {MAX_BUFFER_SIZE} bytes, "
                    f"trimmed {excess} bytes from the beginning"
                )

    def get_accumulated_output(self, clear: bool = True) -> OutputBuffer:
        """
        Get accumulated stdout/stderr output.

        Args:
            clear: If True, clear the buffers after reading

        Returns:
            OutputBuffer with stdout and stderr fields
        """
        output = OutputBuffer(stdout=self._stdout_buffer, stderr=self._stderr_buffer)

        if clear:
            self._stdout_buffer = ""
            self._stderr_buffer = ""

        return output

    def clear_output(self) -> None:
        """Clear the accumulated output buffers."""
        self._stdout_buffer = ""
        self._stderr_buffer = ""

    async def install_stream_capture(self) -> None:
        """
        Install stream capture for stdout/stderr in Maya.

        This redirects stdout/stderr through custom writers that buffer
        output for retrieval via get_buffered_output().
        """
        await self._send_receive(self.INSTALL_STREAM_CAPTURE)
        logger.debug(f"Stream capture installed for {self.host}:{self.port}")

    async def uninstall_stream_capture(self) -> None:
        """
        Remove stream capture and restore original stdout/stderr in Maya.
        """
        await self._send_receive(self.UNINSTALL_STREAM_CAPTURE)
        logger.debug(f"Stream capture uninstalled for {self.host}:{self.port}")

    async def get_buffered_output(self) -> OutputBuffer:
        """
        Get buffered stdout/stderr content and clear the buffers.

        Returns:
            OutputBuffer with stdout and stderr fields containing captured output.
        """
        response = await self._send_receive(self.GET_BUFFERED_OUTPUT)
        result = response.result if response.result is not None else {}
        return OutputBuffer(
            stdout=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
        )

    async def session_info(self) -> SessionInfo:
        """
        Get information about the connected Maya session.

        Returns:
            SessionInfo with pid, user, maya version, current scene, etc.
        """
        response = await self._send_receive(self.GET_SESSION_INFO)
        # get_session_info() returns a JSON string, parse it
        raw = response.result
        if isinstance(raw, str):
            try:
                info = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                info = {}
        elif isinstance(raw, dict):
            info = raw
        else:
            info = {}

        return SessionInfo(
            session_key=self.key,
            host=self.host,
            port=self.port,
            pid=info.get("pid", 0),
            user=info.get("user", ""),
            maya_version=info.get("maya_version", ""),
            scene_name=info.get("scene_name", ""),
            scene_path=info.get("scene_path", ""),
        )

    async def execute_code(
        self,
        code: str,
        result_type: ResultType | str = ResultType.NONE,
    ) -> CommandResponse:
        """
        Execute Python code in Maya and return the result.

        Args:
            code: Python code to execute
            result_type: How to handle the result (enum or plain string —
                both are normalized at this boundary)
                - NONE: Execute statements, don't capture result
                - JSON: Evaluate expression, JSON encode result
                - RAW: Evaluate expression, return string representation

        Returns:
            CommandResponse with result and error info.
            Note: stdout/stderr are delivered via MCP Resources, not returned here.
        """
        # Normalize at the boundary (D-046): enum members pass through,
        # plain strings like "JSON" are coerced. 0.1.1 crashed here on
        # result_type.value when str-typed callers (scene_tools,
        # visual_tools) reached this shared method.
        try:
            result_type = ResultType(result_type)
        except ValueError:
            raise InputValidationError(
                f"Invalid result_type {result_type!r}: must be NONE, JSON, or RAW"
            ) from None

        # Qt server JSON protocol over the framed channel. There is no
        # retry: execution errors are returned in the response (never
        # raised here) and surface to callers via the two-layer contract.
        response = await self._send_receive(
            self.EXECUTE_TEMPLATE,
            {"code": code, "result_type": result_type.value},
            raise_on_error=False,
        )

        # Decode JSON result if needed (same as commandPort path)
        if result_type == ResultType.JSON and response.result is not None:
            try:
                if isinstance(response.result, dict):
                    decoded_result = response.result
                elif isinstance(response.result, str):
                    decoded_result = json.loads(response.result)
                else:
                    decoded_result = response.result
                return CommandResponse(result=decoded_result, error=response.error)
            except json.JSONDecodeError:
                pass  # Keep as string if not valid JSON

        return response

    # Abstract methods that must be implemented by subclasses

    @abstractmethod
    async def _send_receive(
        self, method: str, params: dict[str, Any] | None = None, raise_on_error: bool = True
    ) -> CommandResponse:
        """
        Send command to Maya and receive response.

        Args:
            method: Command string or method name (may contain {param} placeholders)
            params: Parameters to format into the command or pass to the method
            raise_on_error: If True, raise exception on error.
                If False, return full response with error.

        Returns:
            CommandResponse with result and error attributes

        Raises:
            MayaConnectionError: If not connected
            MayaTimeoutError: If the response deadline is exceeded
            MayaExecutionError: If communication fails or raise_on_error is True and command errors
        """
        ...

    @abstractmethod
    async def write_module(self, name: str, code: str, overwrite: bool = False) -> str:
        """Create a virtual Python module in the Maya session."""
        ...

    @abstractmethod
    async def ping(self) -> bool:
        """Check if the Maya session is still responsive."""
        ...


class MayaClient(BaseMayaClient):
    """Async client for communicating with a Maya command port."""

    # Access the helper via __import__ (works in single expressions)
    _MCP_HELPER = "__import__('maya_mcp')"

    # Command templates that use __import__ to access the persistent _mcp module
    GET_SESSION_INFO = f"{_MCP_HELPER}.get_session_info()"
    EXECUTE_TEMPLATE = f"{_MCP_HELPER}.execute({{code!r}}, {{result_type!r}})"
    CREATE_MODULE_TEMPLATE = f"{_MCP_HELPER}.create_module({{name!r}}, {{code!r}}, {{overwrite!r}})"
    INSTALL_STREAM_CAPTURE = f"{_MCP_HELPER}.install_stream_capture()"
    UNINSTALL_STREAM_CAPTURE = f"{_MCP_HELPER}.uninstall_stream_capture()"
    GET_BUFFERED_OUTPUT = f"{_MCP_HELPER}.get_buffered_output()"
    START_COMMAND_PORT = f"{_MCP_HELPER}.start_command_port({{port!r}})"

    # Qt server command templates
    START_QT_SERVER = f"{_MCP_HELPER}.start_qt_server({{port!r}})"
    STOP_QT_SERVER = f"{_MCP_HELPER}.stop_qt_server()"
    GET_QT_SERVER_PORT = f"{_MCP_HELPER}.get_qt_server_port()"

    # Check if bootstrap has been done
    CHECK_BOOTSTRAP = "'maya_mcp' in __import__('sys').modules"
    CHECK_MODULE_SHA = "getattr(__import__('maya_mcp'), '_mcp_source_sha', None)"

    def __post_init__(self) -> None:
        super().__post_init__()
        self._port_type: PortType = PortType.UNKNOWN

    async def _send_receive(
        self, method: str, params: dict[str, Any] | None = None, raise_on_error: bool = True
    ) -> CommandResponse:
        """
        Send command to Maya commandPort and receive response.

        Args:
            method: Command template string (may contain {param} placeholders)
            params: Parameters to format into the template
            raise_on_error: If True, raise exception on error.
                If False, return full response with error.

        Returns:
            CommandResponse with result and error attributes

        Raises:
            MayaConnectionError: If not connected
            MayaUnavailableError: If the connection is lost mid-request
            MayaExecutionError: If communication fails or raise_on_error is True and command errors
        """
        if not self._writer or not self._reader:
            raise MayaConnectionError("Not connected to Maya")

        # Format the command template with params
        if params:
            command = method.format(**params)
        else:
            command = method

        async with self._lock:
            try:
                # Send command - commandPort executes on newline, so the
                # terminator is unconditional (D-041): without it the
                # command parks in the receive buffer until the next send
                # flushes it through (off-by-one).
                self._writer.write(command.encode("utf-8") + b"\n")
                await self._writer.drain()

                # Read response until null terminator
                response_bytes = b""
                while True:
                    chunk = await asyncio.wait_for(
                        self._reader.read(self.buffer_size),
                        timeout=self.timeout,
                    )
                    if not chunk:
                        raise MayaUnavailableError(
                            f"Connection closed on {self.host}:{self.port} "
                            "before response terminator"
                        )
                    response_bytes += chunk
                    # Maya terminates responses with \n\x00
                    if response_bytes.endswith(b"\x00"):
                        break

                # Decode and strip terminator
                response_str = response_bytes.decode("utf-8").rstrip("\x00").rstrip("\n")

                # Parse JSON response if the command returns JSON
                # Most helper functions return JSON with {"result": ..., "error": ...}
                try:
                    response = json.loads(response_str)
                    # Check if it's a structured response
                    if isinstance(response, dict) and ("result" in response or "error" in response):
                        if raise_on_error and response.get("error"):
                            error = response["error"]
                            if isinstance(error, dict):
                                raise MayaExecutionError(f"{error.get('message', 'Unknown error')}")
                            else:
                                raise MayaExecutionError(str(error))
                        return CommandResponse(
                            result=response.get("result"), error=response.get("error")
                        )
                    else:
                        # It's JSON but not our structured format, treat as raw result
                        return CommandResponse(result=response, error=None)
                except json.JSONDecodeError:
                    # Not JSON, treat as raw string result
                    return CommandResponse(result=response_str, error=None)

            except asyncio.TimeoutError as e:
                raise MayaTimeoutError("Timeout waiting for Maya response") from e
            except (ConnectionError, asyncio.IncompleteReadError) as e:
                raise MayaUnavailableError(
                    f"Connection lost on {self.host}:{self.port}: {e}"
                ) from e
            except (MayaExecutionError, MayaUnavailableError):
                raise
            except Exception as e:
                raise MayaExecutionError(f"Error communicating with Maya: {e}") from e

    async def _detect_port_type(self) -> PortType:
        """
        Detect whether the command port speaks MEL or Python.

        Returns:
            PortType indicating the port's language
        """
        if self._port_type != PortType.UNKNOWN:
            return self._port_type

        try:
            # Bilingual probe (D-059 / upstream #1): Python 3 answers 0.5
            # (true division); everything else classifies MEL. T-18a live
            # pin (Maya 2024.0.0.4640, zh locale): a real MEL port answers
            # eval("1/2") with a syntax-error body, not silent 0 - the
            # discriminant is "0.5 vs anything else", and the Script
            # Editor does log the error. Operands must stay int/int:
            # 1.0/2 would collapse the discriminant.
            response = await self._send_receive('eval("1/2")')

            result_str = _result_str(response)
            if "0.5" in result_str:
                self._port_type = PortType.PYTHON
            else:
                # Answered but not 0.5 ("0", silence, other shapes): MEL
                self._port_type = PortType.MEL

        except Exception:
            self._port_type = PortType.UNKNOWN

        logger.info(f"Detected port type: {self._port_type.value}")
        return self._port_type

    async def _bootstrap(self, overwrite: bool = False) -> None:
        """
        Bootstrap the Maya session with helper functions.

        This creates the _mcp module in Maya's sys.modules,
        providing utilities for code execution with capture.
        """
        port_type = await self._detect_port_type()

        if port_type != PortType.PYTHON:
            # TODO: support bootstrapping from a MEL command port
            raise MayaConnectionError(
                f"Cannot bootstrap non-Python port (detected: {port_type.value})"
            )

        helper_code = get_helper_module_code()
        # Content-hash stamp (audit F3): the injected module carries a sha of
        # the exact source text, appended post-hash so it self-describes the
        # payload it rides on. A manual __build__ date-stamp can be forgotten
        # and would silently skip the rewrite - the sha cannot drift.
        helper_sha = hashlib.sha256(helper_code.encode()).hexdigest()[:16]
        inject_code = f"{helper_code}\n_mcp_source_sha = {helper_sha!r}\n"

        if not overwrite:
            # Check if already bootstrapped (e.g., from previous connection)
            check_result = await self._send_receive(self.CHECK_BOOTSTRAP)
            if _result_str(check_result) == "True":
                # Content-hash gate (T-18a live find): the hot-update write
                # pushes >10K through the native commandPort, tripping its
                # stale-response quirk and intermittently stranding the
                # just-started Qt server (connect refused on a live bind).
                # When the injected helper already matches this source the
                # rewrite is a no-op - skip it instead.
                sha_resp = await self._send_receive(self.CHECK_MODULE_SHA)
                if _result_str(sha_resp) == helper_sha:
                    logger.info("Maya mcp module already current")
                    return
                logger.info("Maya session already bootstrapped, updating module...")
                # Re-execute bootstrap code to get latest create_module function
                bootstrap_code = get_bootstrap_code()
                bootstrap_cmd = f"exec({bootstrap_code!r}, globals())"
                await self._send_receive(bootstrap_cmd)
                # Now use the fresh create_module to update the helper module
                cmd = "create_module({name!r}, {code!r}, {overwrite!r})"
                update_response = await self._send_receive(
                    cmd, {"name": "maya_mcp", "code": inject_code, "overwrite": True}
                )
                # D-058: the overwrite above is the one path that triggers
                # _mcp_teardown - a failed cleanup surfaces as a "warning"
                # key in create_module's payload; log it instead of
                # dropping the response (N1).
                update_result = update_response.result
                if isinstance(update_result, str):
                    try:
                        update_result = json.loads(update_result)
                    except (json.JSONDecodeError, TypeError):
                        update_result = None
                if isinstance(update_result, dict):
                    # N7: a failed create_module carries its error inside
                    # the result payload (same wrap write_module unpacks)
                    # - surface it instead of logging a false "updated".
                    inner_error = update_result.get("error")
                    if inner_error:
                        if isinstance(inner_error, dict):
                            raise MayaExecutionError(
                                f"{inner_error.get('code', 'module_error')}: "
                                f"{inner_error.get('message', 'module update failed')}"
                            )
                        raise MayaExecutionError(str(inner_error))
                    if update_result.get("warning"):
                        logger.warning(f"Module 'maya_mcp': {update_result['warning']}")
                    logger.info("Maya mcp module updated")
                else:
                    # F6: a non-dict payload cannot be confirmed - do not
                    # claim success; surface what came back instead.
                    logger.warning(
                        f"Module 'maya_mcp' update returned unexpected payload: {update_result!r}"
                    )
                return

        # Execute bootstrap code using exec() with globals() to persist definitions
        # Maya command port runs each command in isolated scope, so we must use
        # exec(..., globals()) to make the code affect the global namespace
        bootstrap_code = get_bootstrap_code()
        bootstrap_cmd = f"exec({bootstrap_code!r}, globals())"
        await self._send_receive(bootstrap_cmd)
        # We've now bootstrapped the create_module function, which we use to create
        # the helper module (inject_code carries the content-hash stamp):
        # Remove the module name prefix since create_module doesn't exist yet
        cmd = "create_module({name!r}, {code!r}, {overwrite!r})"
        await self._send_receive(
            cmd, {"name": "maya_mcp", "code": inject_code, "overwrite": overwrite}
        )
        logger.info("Maya session bootstrapped")

    async def bootstrap(self, client_type: Literal["native", "qt"] = "native") -> BaseMayaClient:
        """
        Boostrap remote session and return a new client
        """
        await self._bootstrap()

        # Create a dedicated communication port for this client
        new_port = random.randint(COMMUNICATION_PORT_MIN, COMMUNICATION_PORT_MAX)
        new_client: BaseMayaClient

        if client_type == "native":
            logger.info(f"Creating dedicated commandPort on port {new_port}")
            result = await self._send_receive(self.START_COMMAND_PORT, {"port": new_port})
            logger.info(f"Dedicated commandPort created: {result}")

            # Wait a moment for the port to start listening
            await asyncio.sleep(0.5)

            logger.info(f"Connecting to dedicated port {new_port}...")
            new_client = MayaClient(
                host=self.host, port=new_port, timeout=self.timeout, buffer_size=self.buffer_size
            )
            # F1 (audit): the explicit native branch leaked its dedicated
            # port just like the fallback did - tag it so disconnect()
            # closes it.
            new_client._dedicated_port = new_port
        elif client_type == "qt":
            # Unlike the commandPort, the qt server can handle multiple clients on the same port.
            # Therefore, the port returned by _send_receive might be different than requested.
            logger.info(f"Starting Qt server on port {new_port}")
            # raise_on_error=False: start_qt_server reports failures as a JSON
            # {"error": ...} payload on the wire, not an exception (F-1 fix).
            result = await self._send_receive(
                self.START_QT_SERVER, {"port": new_port}, raise_on_error=False
            )
            qt_error: Any = result.error
            qt_port = None
            qt_started_by_us = False
            # The native channel may wrap the JSON payload in result (dict or str)
            payload = result.result
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except (json.JSONDecodeError, TypeError):
                    payload = None
            if isinstance(payload, dict):
                qt_error = qt_error or payload.get("error")
                qt_port = payload.get("port")
                qt_started_by_us = payload.get("already_running") is False

            qt_client: MayaQtClient | None = None
            if not qt_error:
                if qt_port:
                    new_port = qt_port
                logger.info(f"Qt server started on port {new_port}")

                # Wait a moment for the port to start listening
                await asyncio.sleep(0.5)

                logger.info(f"Connecting to dedicated port {new_port}...")
                qt_client = MayaQtClient(
                    host=self.host,
                    port=new_port,
                    timeout=self.timeout,
                    buffer_size=self.buffer_size,
                )
                # Maya's main thread can lag the Qt event loop during busy
                # windows (file ops, VP2 redraws); the T-18a live batch showed
                # a single-shot probe falling back to native intermittently.
                # Bounded retry keeps the framed channel for a slow-but-alive
                # server without masking a truly dead one.
                last_err: Exception | None = None
                for _attempt in range(QT_CONNECT_ATTEMPTS):
                    try:
                        if not qt_client.is_connected:
                            await qt_client.connect()
                        # Liveness gate (D-013): in headless Maya a Qt listener
                        # can accept TCP while readyRead never fires - a zombie
                        # channel. Probe the framed protocol before registering.
                        alive = await asyncio.wait_for(qt_client.ping(), timeout=QT_PROBE_TIMEOUT)
                        if not alive:
                            raise MayaUnavailableError(
                                "Qt server accepted the connection but does not respond"
                            )
                        last_err = None
                        break
                    except Exception as e:
                        last_err = e
                        try:
                            await qt_client.disconnect()
                        except Exception:
                            pass
                        await asyncio.sleep(0.5)
                if last_err is not None:
                    logger.warning(f"Qt channel unusable ({last_err}); falling back")
                    qt_error = last_err
                    qt_client = None

            if qt_client is not None:
                new_client = qt_client
            else:
                # Headless Maya (mayapy) has no Qt event loop. Per D-013 the
                # native commandPort is the minimal fallback working channel
                # there - it never promises large payloads.
                logger.warning(
                    f"Qt server unavailable ({qt_error}); "
                    "falling back to a dedicated native commandPort"
                )
                # T-18a live find: a Qt server that was started but never
                # connected stays listening forever (orphan). Reap it before
                # opening the fallback port - but only the one this call
                # started: a pre-existing listener may still serve other
                # clients, so stopping it unconditionally could kill live
                # channels (audit F7: reap only the orphan this call started,
                # never a pre-existing listener that may serve other clients).
                if qt_started_by_us:
                    try:
                        await self._send_receive(self.STOP_QT_SERVER, raise_on_error=False)
                    except Exception:
                        pass
                result = await self._send_receive(self.START_COMMAND_PORT, {"port": new_port})
                logger.info(f"Dedicated commandPort created: {result}")
                await asyncio.sleep(0.5)
                new_client = MayaClient(
                    host=self.host,
                    port=new_port,
                    timeout=self.timeout,
                    buffer_size=self.buffer_size,
                )
                # T-18a: remember the dedicated port so disconnect() can
                # close it - fallback commandPorts stayed open forever.
                new_client._dedicated_port = new_port
        else:
            raise InputValidationError(f"Unknown client_type {client_type!r}")
        # Connect to the new dedicated port (the Qt branch already probed it)
        if not new_client.is_connected:
            await new_client.connect()
        logger.info(f"Connected to dedicated port {new_port}")
        return new_client

    async def write_module(
        self,
        name: str,
        code: str,
        overwrite: bool = False,
    ) -> str:
        """
        Create a virtual Python module in the Maya session.

        Args:
            name: Module name (can be dotted path like 'mypackage.mymodule')
            code: Python source code for the module
            overwrite: If True, replace existing module; else raise error

        Returns:
            Success message

        Raises:
            MayaExecutionError: If module creation fails
        """
        response = await self._send_receive(
            self.CREATE_MODULE_TEMPLATE,
            {"name": name, "code": code, "overwrite": overwrite},
        )
        raise_for_error(response)

        # The native commandPort wraps create_module()'s JSON in the raw result
        # payload: errors arrive inside result, not in response.error. Surface
        # them instead of reporting success on a dead response.
        result = response.result
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                pass
        if isinstance(result, dict):
            inner_error = result.get("error")
            if inner_error:
                if isinstance(inner_error, dict):
                    raise MayaExecutionError(
                        f"{inner_error.get('code', 'module_error')}: "
                        f"{inner_error.get('message', 'module creation failed')}"
                    )
                raise MayaExecutionError(str(inner_error))
            # D-058: an overwrite whose old-module teardown failed still
            # succeeded - surface the warning instead of dropping it
            warning = result.get("warning")
            if warning:
                logger.warning(f"Module '{name}': {warning}")
            message = result.get("message")
            if message:
                return str(message)
        return f"Module '{name}' created"

    async def ping(self) -> bool:
        """
        Check if the Maya connection is alive.

        Returns:
            True if Maya responds, False otherwise
        """
        try:
            response = await self._send_receive("1+1")
            return str(response.result if response.result is not None else "").strip() == "2"
        except Exception:
            return False


class MayaQtClient(BaseMayaClient):
    """Async client for the custom Maya Qt command server (D-013).

    Primary working channel: length-prefixed frames (16 MiB cap) over a
    localhost-only QTcpServer; per-connection FIFO queue Maya-side;
    requests here are serialized by self._lock and correlated by id.
    """

    framed_channel: ClassVar[bool] = True

    GET_SESSION_INFO = "get_session_info"
    EXECUTE_TEMPLATE = "execute"
    CREATE_MODULE_TEMPLATE = "create_module"
    INSTALL_STREAM_CAPTURE = "install_stream_capture"
    UNINSTALL_STREAM_CAPTURE = "uninstall_stream_capture"
    GET_BUFFERED_OUTPUT = "get_buffered_output"

    def __post_init__(self) -> None:
        super().__post_init__()
        self._request_counter: int = 0

    async def connect(self) -> None:
        """Connect to the Qt server, retrying on the 0.5/1/2s backoff schedule."""
        last_error: Exception | None = None
        attempts = len(CONNECT_RETRY_DELAYS) + 1

        for attempt in range(attempts):
            try:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=self.timeout,
                )
                logger.info(f"Connected to Qt server at {self.host}:{self.port}")
                return
            except (OSError, asyncio.TimeoutError) as e:
                last_error = e
                if attempt < attempts - 1:
                    delay = CONNECT_RETRY_DELAYS[attempt]
                    logger.warning(
                        f"Failed to connect to Qt server "
                        f"(attempt {attempt + 1}/{attempts}): {e}; retry in {delay}s"
                    )
                    await asyncio.sleep(delay)

        if isinstance(last_error, asyncio.TimeoutError):
            raise MayaTimeoutError(
                f"Timeout connecting to Qt server at {self.host}:{self.port} "
                f"after {attempts} attempts"
            ) from last_error
        raise MayaUnavailableError(
            f"Failed to connect to Qt server at {self.host}:{self.port} "
            f"after {attempts} attempts: {last_error}"
        ) from last_error

    async def write_module(
        self,
        name: str,
        code: str,
        overwrite: bool = False,
    ) -> str:
        """
        Create a virtual Python module in the Maya session.

        Args:
            name: Module name (can be dotted path like 'mypackage.mymodule')
            code: Python source code for the module
            overwrite: If True, replace existing module; else raise error

        Returns:
            Success message

        Raises:
            MayaExecutionError: If module creation fails
        """
        response = await self._send_receive(
            self.CREATE_MODULE_TEMPLATE, {"name": name, "code": code, "overwrite": overwrite}
        )
        result = response.result if response.result is not None else {}
        if isinstance(result, dict):
            warning = result.get("warning")
            if warning:
                logger.warning(f"Module '{name}': {warning}")
            return str(result.get("message", f"Module '{name}' created"))
        return f"Module '{name}' created"

    async def _send_receive(
        self, method: str, params: dict[str, Any] | None = None, raise_on_error: bool = True
    ) -> CommandResponse:
        """Send JSON request to Qt server and receive response.

        Args:
            method: Method name to call
            params: Parameters for the method
            raise_on_error: If True, raise exception on error.
                If False, return full response with error.

        Returns:
            CommandResponse with result and error attributes
        """
        if not self._writer or not self._reader:
            raise MayaUnavailableError("Not connected to Qt server")

        async with self._lock:
            # Generate request ID
            self._request_counter += 1
            request_id = f"req-{self._request_counter}"

            # Build + frame the request
            request = {"id": request_id, "method": method, "params": params or {}}
            payload = json.dumps(request).encode("utf-8")
            if len(payload) > MAX_FRAME_SIZE:
                raise InputValidationError(
                    f"request {len(payload)}B exceeds frame cap {MAX_FRAME_SIZE}B"
                )

            try:
                self._writer.write(encode_frame(payload))
                await self._writer.drain()

                # Read one length-prefixed response frame
                header = await asyncio.wait_for(
                    self._reader.readexactly(FRAME_HEADER_SIZE), timeout=self.timeout
                )
                frame_len = int.from_bytes(header, "big")
                if frame_len > MAX_FRAME_SIZE:
                    raise MayaExecutionError(
                        f"response frame {frame_len}B exceeds cap {MAX_FRAME_SIZE}B"
                    )
                body = await asyncio.wait_for(
                    self._reader.readexactly(frame_len), timeout=self.timeout
                )

                response = json.loads(body.decode("utf-8"))

                # Validate response ID
                if response.get("id") != request_id:
                    raise MayaExecutionError(
                        f"Response ID mismatch: expected {request_id}, got {response.get('id')}"
                    )

                # Check for error - keep the wire code in the message
                # ("code: message", same as the native write_module path, D-019)
                if raise_on_error and response.get("error"):
                    raise MayaExecutionError(_domain_error_text(response["error"]))

                # Return CommandResponse
                return CommandResponse(result=response.get("result"), error=response.get("error"))

            except asyncio.TimeoutError as e:
                raise MayaTimeoutError("Timeout waiting for Qt server response") from e
            except (MayaUnavailableError, MayaExecutionError):
                raise
            except (ConnectionError, asyncio.IncompleteReadError) as e:
                raise MayaUnavailableError("Connection closed by Qt server") from e
            except json.JSONDecodeError as e:
                raise MayaExecutionError(f"Invalid JSON response from Qt server: {e}") from e
            except Exception as e:
                raise MayaExecutionError(f"Error communicating with Qt server: {e}") from e

    async def ping(self) -> bool:
        """
        Check if the Maya connection is alive.

        Returns:
            True if Maya responds, False otherwise
        """
        try:
            response = await self._send_receive("ping")
            return bool(response.result == "pong")
        except Exception:
            return False

    async def health(self) -> dict[str, Any]:
        """Deeper health probe: server status, port, client count, uptime.

        Returns {"status": ...} even on a degraded channel.
        """
        try:
            response = await self._send_receive("health")
            if isinstance(response.result, dict):
                return response.result
        except Exception:
            pass
        return {"status": "unavailable"}
