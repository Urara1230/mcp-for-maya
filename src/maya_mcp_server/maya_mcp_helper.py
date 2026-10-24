"""MCP helper"""

from __future__ import annotations

import ast
import json
import sys
import time
import traceback
from collections import deque
from typing import Any


# Dual-runtime floor (D-060 / upstream #3): this module is injected into
# Maya's own interpreter - Maya >= 2023 ships Python >= 3.9. Refuse early
# with an actionable message instead of dying later inside ast.unparse.
if sys.version_info < (3, 9):  # noqa: UP036 - injected side runs Maya's own interpreter, not the host's
    raise RuntimeError(
        "mcp-for-maya requires Maya 2023+ (Python >= 3.9) on the Maya side; "
        f"detected Python {sys.version.split()[0]}"
    )


# Try importing Qt from PySide2 (Maya 2022-2023) or PySide6 (Maya 2024+)
try:
    from PySide2.QtCore import QCoreApplication  # type: ignore[import-not-found]
    from PySide2.QtNetwork import (  # type: ignore[import-not-found]
        QHostAddress,
        QTcpServer,
    )
except ImportError:
    try:
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtNetwork import QHostAddress, QTcpServer
    except ImportError:
        # Qt not available - Qt server functions will fail gracefully
        QTcpServer = None
        QHostAddress = None
        QCoreApplication = None

CAPTURE_VARIABLE = "_mcp_result"

# StreamWriter buffer size limit (5MB) to prevent memory exhaustion
_MAX_STREAM_BUFFER_SIZE = 5_242_880


def prepare_code_for_result_capture(
    code: str, capture_variable: str = CAPTURE_VARIABLE
) -> tuple[str, bool]:
    """
    Transform Python code to capture the result of the final expression.

    If the final statement is a standalone expression at the module level
    (not nested in a loop, function, or class, and not an assignment),
    prepends `_mcp_result = ` to capture its value.

    Args:
        code: Python source code string

    Returns:
        A tuple of (transformed_code, was_transformed).
        If no transformation was needed, returns (original_code, False).
    """
    code = code.rstrip()

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code, False

    if not tree.body:
        return code, False

    # ast.unparse is 3.9+; on patched/forked interpreters where
    # sys.version_info lies, degrade to no-capture rather than crashing on
    # AttributeError (D-060, second layer of the floor guard).
    if not hasattr(ast, "unparse"):
        return code, False

    last_stmt = tree.body[-1]

    # Only transform standalone expressions
    # ast.Expr is a statement node that wraps an expression value
    # This excludes: assignments, augmented assignments, annotated assignments,
    # function/class definitions, control flow (if/for/while/try), imports, etc.
    if not isinstance(last_stmt, ast.Expr):
        return code, False

    # Get the expression as a string using ast.unparse (Python 3.9+)
    expr_str = ast.unparse(last_stmt.value)

    # Build the new code: all statements except the last + new assignment
    # We use ast.unparse for preceding statements to handle edge cases like
    # semicolon-separated statements on the same line (e.g., "x = 1; 2")
    preceding_stmts = tree.body[:-1]
    if preceding_stmts:
        before = "\n".join(ast.unparse(stmt) for stmt in preceding_stmts) + "\n"
    else:
        before = ""

    new_stmt = f"{capture_variable} = {expr_str}\n"

    return before + new_stmt, True


# ---------------------------------------------------------------------------
# Length-prefixed wire framing for the Qt framed channel (D-013)
#
# The Qt channel uses uint32-BE length-prefixed frames instead of
# line-delimited JSON so multi-MB module sources and result payloads survive
# partial reads and coalesced writes. Cap: 16 MiB per frame.
# ---------------------------------------------------------------------------

FRAME_HEADER_SIZE = 4
MAX_FRAME_SIZE = 16 * 1024 * 1024  # 16 MiB (D-013 band: 8-16 MiB)


class FrameTooLargeError(Exception):
    """A declared frame length exceeds the negotiated cap."""

    def __init__(self, declared: int, max_size: int) -> None:
        self.declared = declared
        self.max_size = max_size
        super().__init__(f"declared frame length {declared} exceeds cap {max_size}")


def encode_frame(payload: bytes) -> bytes:
    """Encode one length-prefixed frame (raises FrameTooLargeError over cap)."""

    if len(payload) > MAX_FRAME_SIZE:
        raise FrameTooLargeError(len(payload), MAX_FRAME_SIZE)
    return len(payload).to_bytes(FRAME_HEADER_SIZE, "big") + payload


class FrameDecoder:
    """Incremental decoder for the length-prefixed protocol.

    feed() accepts arbitrary byte chunks (partial headers, split payloads,
    coalesced frames) and returns complete frame payloads in order.
    """

    def __init__(self, max_frame_size: int = MAX_FRAME_SIZE) -> None:
        self.max_frame_size = max_frame_size
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data
        frames = []
        while len(self._buf) >= FRAME_HEADER_SIZE:
            n = int.from_bytes(self._buf[:FRAME_HEADER_SIZE], "big")
            if n > self.max_frame_size:
                del self._buf[:FRAME_HEADER_SIZE]
                raise FrameTooLargeError(n, self.max_frame_size)
            if len(self._buf) < FRAME_HEADER_SIZE + n:
                break
            frames.append(bytes(self._buf[FRAME_HEADER_SIZE : FRAME_HEADER_SIZE + n]))
            del self._buf[: FRAME_HEADER_SIZE + n]
        return frames


class StreamWriter:
    """Custom writer that captures output for MCP Resource streaming."""

    def __init__(self, stream_type: str, original: Any) -> None:
        self.stream_type = stream_type
        self._wrapped = original
        self._buffer: list[str] = []
        self._size = 0

    def write(self, text: str) -> None:
        if text:
            self._buffer.append(text)
            self._size += len(text)
            # Bound the capture buffer: drop oldest chunks past the cap.
            while self._size > _MAX_STREAM_BUFFER_SIZE and len(self._buffer) > 1:
                self._size -= len(self._buffer.pop(0))
            if self._size > _MAX_STREAM_BUFFER_SIZE:
                self._buffer[-1] = self._buffer[-1][-_MAX_STREAM_BUFFER_SIZE:]
                self._size = _MAX_STREAM_BUFFER_SIZE
        if self._wrapped:
            self._wrapped.write(text)

    def flush(self) -> None:
        if self._wrapped:
            self._wrapped.flush()

    def get_buffer(self) -> str:
        result = "".join(self._buffer)
        self._buffer.clear()
        self._size = 0
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


_stdout_writer: StreamWriter | None = None
_stderr_writer: StreamWriter | None = None


def install_stream_capture() -> str:
    """Install stream capture for stdout/stderr."""
    global _stdout_writer
    global _stderr_writer

    if _stdout_writer is None:
        _stdout_writer = StreamWriter("stdout", sys.stdout)
        sys.stdout = _stdout_writer
    if _stderr_writer is None:
        _stderr_writer = StreamWriter("stderr", sys.stderr)
        sys.stderr = _stderr_writer
    return json.dumps({"success": True})


def uninstall_stream_capture() -> str:
    """Remove stream capture and restore original streams."""
    global _stdout_writer
    global _stderr_writer

    if _stdout_writer is not None:
        sys.stdout = _stdout_writer._wrapped
        _stdout_writer = None
    if _stderr_writer is not None:
        sys.stderr = _stderr_writer._wrapped
        _stderr_writer = None
    return json.dumps({"success": True})


def get_buffered_output() -> str:
    """Get buffered stdout/stderr and clear buffers."""
    stdout = _stdout_writer.get_buffer() if _stdout_writer else ""
    stderr = _stderr_writer.get_buffer() if _stderr_writer else ""
    return json.dumps({"stdout": stdout, "stderr": stderr})


def get_session_info() -> str:
    """Return session information as JSON."""
    import getpass
    import os

    import maya.cmds as cmds

    scene_path = cmds.file(query=True, sceneName=True) or ""
    scene_name = os.path.basename(scene_path) if scene_path else "untitled"
    return json.dumps(
        {
            "pid": os.getpid(),
            "user": getpass.getuser(),
            "maya_version": cmds.about(version=True),
            "scene_name": scene_name,
            "scene_path": scene_path,
        }
    )


def execute(code: str, result_type: str = "NONE") -> str:
    """Execute code and return result as JSON."""
    result = None
    error = None
    # Dedicated exec namespace for result capture. NOT a security boundary:
    # __builtins__ is fully present and code runs with Maya's privileges —
    # the safety net is host-side (pattern scan, rate limit, audit).
    import sys

    context = {"__builtins__": __builtins__, "__name__": "__mcp_exec__", "__doc__": None}
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith(("maya", "mcp", "_mcp")):
            context[mod_name] = sys.modules[mod_name]
    context[CAPTURE_VARIABLE] = None
    try:
        modified_code, was_modified = prepare_code_for_result_capture(code)
        if result_type != "NONE" and was_modified is False:
            raise RuntimeError(
                "Results were requested but the code cannot be modified to capture a result."
                "If you want to capture a result, make sure that the last line of code is in "
                "the module scope (i.e. not in a function or loop)"
            )

        exec(compile(modified_code, "<mcp>", "exec"), context)
        if was_modified:
            result = context[CAPTURE_VARIABLE]
            if result_type == "JSON":
                result = json.dumps(result)

    except Exception as e:
        error = {
            "type": f"{type(e).__module__}.{type(e).__name__}",
            "message": str(e),
            "traceback": traceback.format_exc(),
        }
    return json.dumps({"result": result, "error": error})


def start_command_port(port: int) -> str:
    import maya.cmds as cmds

    cmds.commandPort(name=f":{port}", sourceType="python")
    return json.dumps({"success": True, "port": port})


def dispatch_request(request: dict[str, Any], server: Any = None) -> dict[str, Any]:
    """Dispatch one decoded request to the helper method table.

    Transport-agnostic: used by ClientChannel (Qt framed channel) and
    testable without Qt. Returns the response dict - never raises.

    Error schema is dual (D-019): known domain errors use
    {"code": ..., "message": ..., "suggestion"?}; unexpected exceptions use
    {"type", "message", "traceback"}. Clients must handle both.
    """

    method = request.get("method")
    params = request.get("params", {})
    req_id = request.get("id")

    try:
        # Dispatch to existing helper functions
        if method == "execute":
            result_str = execute(params.get("code", ""), params.get("result_type", "NONE"))
            result_obj = json.loads(result_str)
            return {
                "id": req_id,
                "result": result_obj.get("result"),
                "error": result_obj.get("error"),
            }

        elif method == "get_session_info":
            result_str = get_session_info()
            return {"id": req_id, "result": json.loads(result_str), "error": None}

        elif method == "install_stream_capture":
            install_stream_capture()
            return {"id": req_id, "result": {"success": True}, "error": None}

        elif method == "uninstall_stream_capture":
            uninstall_stream_capture()
            return {"id": req_id, "result": {"success": True}, "error": None}

        elif method == "get_buffered_output":
            result_str = get_buffered_output()
            return {"id": req_id, "result": json.loads(result_str), "error": None}

        elif method == "create_module":
            # create_module is defined in maya_bootstrap.py which is
            # exec'd into Maya's global namespace, so it's available via globals()
            create_module_func = globals().get("create_module")
            if create_module_func is None:
                return {
                    "id": req_id,
                    "result": None,
                    "error": {
                        "code": "unavailable",
                        "message": "create_module function not available",
                    },
                }
            result_str = create_module_func(
                params.get("name", ""), params.get("code", ""), params.get("overwrite", False)
            )
            result_obj = json.loads(result_str)
            if "error" in result_obj:
                err = result_obj["error"]
                if not isinstance(err, dict):
                    err = {"message": str(err)}
                return {"id": req_id, "result": None, "error": err}
            return {"id": req_id, "result": result_obj, "error": None}

        elif method == "ping":
            return {"id": req_id, "result": "pong", "error": None}

        elif method == "health":
            result: dict[str, Any] = {"status": "ok"}
            if server is not None:
                result["port"] = getattr(server, "port", None)
                result["clients"] = len(getattr(server, "_channels", {}))
                started = getattr(server, "_started_at", None)
                if started:
                    result["uptime_s"] = round(time.monotonic() - started, 3)
            return {"id": req_id, "result": result, "error": None}

        else:
            return {
                "id": req_id,
                "result": None,
                "error": {"code": "unknown_method", "message": f"Unknown method: {method}"},
            }
    except Exception as e:
        return {
            "id": req_id,
            "result": None,
            "error": {
                "type": f"{type(e).__module__}.{type(e).__name__}",
                "message": str(e),
                "traceback": traceback.format_exc(),
            },
        }


def handle_frame(payload: bytes, server: Any = None) -> dict[str, Any]:
    """Decode one frame payload (JSON request) and return the response dict.

    Never raises: a malformed payload yields a structured error response.
    """

    request = None
    try:
        request = json.loads(payload.decode("utf-8"))
        return dispatch_request(request, server)
    except Exception as e:
        req_id = request.get("id") if isinstance(request, dict) else None
        return {
            "id": req_id,
            "result": None,
            "error": {
                "type": f"{type(e).__module__}.{type(e).__name__}",
                "message": str(e),
                "traceback": traceback.format_exc(),
            },
        }


class ClientChannel:
    """Per-connection framed command queue (transport-agnostic core).

    QtCommandServer wires QTcpSocket.readyRead to feed_socket(); decoded
    request frames queue FIFO per connection and are dispatched in order.
    Kept Qt-free so the protocol path is testable without an event loop.
    """

    def __init__(self, sock: Any, server: Any = None, max_frame_size: int = MAX_FRAME_SIZE) -> None:
        self.socket = sock
        self.server = server
        self.max_frame_size = max_frame_size
        self.decoder = FrameDecoder(max_frame_size)
        self._queue: deque[bytes] = deque()
        self.closed = False

    def feed_socket(self) -> None:
        """Drain available socket bytes -> frames -> FIFO dispatch."""
        if self.closed:
            return
        try:
            frames = self.decoder.feed(bytes(self.socket.readAll()))
        except FrameTooLargeError as e:
            self._write_obj(
                {
                    "id": None,
                    "result": None,
                    "error": {"code": "frame_too_large", "message": str(e)},
                }
            )
            self.close()
            return
        self._queue.extend(frames)
        self._drain()

    def _drain(self) -> None:
        while self._queue and not self.closed:
            payload = self._queue.popleft()
            self._write_obj(handle_frame(payload, self.server))

    def _write_obj(self, obj: dict[str, Any]) -> None:
        data = json.dumps(obj).encode("utf-8")
        if len(data) > self.max_frame_size:
            obj = {
                "id": obj.get("id") if isinstance(obj, dict) else None,
                "result": None,
                "error": {
                    "code": "response_too_large",
                    "message": (f"response {len(data)}B exceeds frame cap {self.max_frame_size}B"),
                },
            }
            data = json.dumps(obj).encode("utf-8")
        frame = len(data).to_bytes(FRAME_HEADER_SIZE, "big") + data
        self.socket.write(frame)
        self.socket.flush()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.socket.disconnectFromHost()
        except Exception:
            pass
        try:
            self.socket.deleteLater()
        except Exception:
            pass


class QtCommandServer:
    """Qt-based TCP command server for Maya MCP (D-013: the primary working
    channel; the native command port is bootstrap/headless fallback only).

    Runs on Maya's main thread inside the Qt event loop. readyRead is
    event-driven (no polling timer); each connection owns a ClientChannel
    with a framed FIFO command queue. Binds localhost only and rejects
    non-loopback peers.
    """

    def __init__(self, port: int = 0, max_frame_size: int = MAX_FRAME_SIZE):
        if QTcpServer is None:
            raise RuntimeError("Qt not available - cannot create command server")

        self._server = QTcpServer()
        self._channels: dict[int, ClientChannel] = {}
        self._max_frame_size = max_frame_size
        self._port = port
        self._running = False
        self._next_client_id = 0
        self._started_at: float | None = None

        self._server.newConnection.connect(self._on_new_connection)

    def start(self) -> None:
        """Start listening on localhost (port 0 -> OS-assigned)."""
        if not self._server.listen(QHostAddress.LocalHost, self._port):
            raise RuntimeError(f"Failed to start server: {self._server.errorString()}")
        self._port = self._server.serverPort()
        self._running = True
        self._started_at = time.monotonic()

    def stop(self) -> None:
        """Stop the server and close all client channels."""
        self._running = False
        # Unwire the signal, not just the sockets: after a module reload the
        # new copy must not see this orphaned instance still firing (D-058).
        try:
            self._server.newConnection.disconnect(self._on_new_connection)
        except (TypeError, RuntimeError):
            pass
        for channel in list(self._channels.values()):
            channel.close()
        self._channels.clear()
        self._server.close()
        try:
            self._server.deleteLater()
        except Exception:
            pass

    @property
    def port(self) -> int:
        """Get the actual port the server is listening on."""
        return self._port

    @property
    def client_count(self) -> int:
        return len(self._channels)

    def _on_new_connection(self) -> None:
        """Handle new client connection (Qt signal)."""
        sock = self._server.nextPendingConnection()
        if sock is None:
            return

        # localhost-only: belt-and-suspenders on top of the LocalHost bind.
        # Fail-closed: if the peer address cannot be determined, reject.
        try:
            if not sock.peerAddress().isLoopback():
                sock.disconnectFromHost()
                return
        except Exception:
            sock.disconnectFromHost()
            return

        client_id = self._next_client_id
        self._next_client_id += 1
        self._channels[client_id] = ClientChannel(
            sock, server=self, max_frame_size=self._max_frame_size
        )
        sock.readyRead.connect(lambda cid=client_id: self._on_ready_read(cid))
        sock.disconnected.connect(lambda cid=client_id: self._on_disconnected(cid))

    def _on_ready_read(self, client_id: int) -> None:
        """Socket has data ready (Qt signal): feed its channel."""
        channel = self._channels.get(client_id)
        if channel is not None:
            channel.feed_socket()

    def _on_disconnected(self, client_id: int) -> None:
        """Handle client disconnect."""
        self._channels.pop(client_id, None)


# Global Qt server instance
_qt_server: QtCommandServer | None = None


def start_qt_server(port: int) -> str:
    """Start the Qt command server and return the port number.

    Headless guard (D-013): in mayapy, PySide6 imports fine and QTcpServer
    .listen() even succeeds, but no QCoreApplication event loop exists so
    readyRead is never dispatched - a zombie channel that accepts TCP but
    answers nothing. Detect before binding and report a domain error so the
    client falls back to a dedicated native commandPort.
    """
    global _qt_server

    if _qt_server is not None:
        return json.dumps({"port": _qt_server.port, "already_running": True})

    headless = False
    try:
        import maya.cmds as cmds

        headless = bool(cmds.about(batch=True))
    except Exception:
        pass
    if not headless:
        # Without a live QCoreApplication, Qt signals never fire - same zombie.
        headless = QCoreApplication is None or QCoreApplication.instance() is None
    if headless:
        return json.dumps(
            {
                "error": {
                    "code": "qt_unavailable_headless",
                    "message": "Qt event loop unavailable (headless Maya)",
                    "suggestion": "run Maya GUI, or fall back to the native commandPort channel",
                }
            }
        )

    try:
        _qt_server = QtCommandServer(port)
        _qt_server.start()
        return json.dumps({"port": _qt_server.port, "already_running": False})
    except Exception as e:
        return json.dumps(
            {
                "error": {
                    "type": f"{type(e).__module__}.{type(e).__name__}",
                    "message": str(e),
                    "traceback": traceback.format_exc(),
                }
            }
        )


def stop_qt_server() -> str:
    """Stop the Qt command server."""
    global _qt_server
    if _qt_server:
        _qt_server.stop()
        _qt_server = None
    return json.dumps({"success": True})


def get_qt_server_port() -> str:
    """Get the port of the running Qt server."""
    if _qt_server is None:
        return json.dumps(
            {"error": {"code": "server_not_running", "message": "Server not running"}}
        )
    return json.dumps({"port": _qt_server.port})


def _mcp_teardown() -> str:
    """Idempotent module teardown (D-058 / upstream issue #4).

    create_module(overwrite=True) invokes this hook on the OLD module
    object before evicting it from sys.modules. Every resource is released
    inside its own try/except so one failure cannot skip the rest; calling
    it twice is a no-op.
    """
    global _qt_server
    errors: list[str] = []

    try:
        if _qt_server is not None:
            _qt_server.stop()
    except Exception as e:
        errors.append(f"qt_server: {type(e).__name__}: {e}")
    finally:
        _qt_server = None

    try:
        uninstall_stream_capture()
    except Exception as e:
        errors.append(f"stream_capture: {type(e).__name__}: {e}")

    result: dict[str, Any] = {"success": not errors}
    if errors:
        result["errors"] = errors
    return json.dumps(result)
