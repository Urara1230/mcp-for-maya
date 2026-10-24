"""T-05 (D-013): Qt working-channel rewrite tests.

The Qt framed channel is the primary working channel; the native command
port is bootstrap/headless fallback only.

Seams covered without a real Maya session:
- length-prefixed frame codec (encode_frame / FrameDecoder)
- dispatch_request method table (ping / health / execute / unknown)
- ClientChannel: per-connection framed command queue (fake socket)
- StreamWriter 5 MiB cap wiring
- MayaQtClient framed wire protocol against a real asyncio peer
- typed errors: MayaTimeoutError / MayaUnavailableError
- QtCommandServer real localhost roundtrip (PySide6-gated)
- scene_tools module-injection routing (framed vs native channel)
"""

from __future__ import annotations

import asyncio
import inspect
import json
import socket
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from maya_mcp_server import maya_mcp_helper as helper
from maya_mcp_server import scene_tools
from maya_mcp_server.client import (
    MayaConnectionError,
    MayaExecutionError,
    MayaQtClient,
    MayaTimeoutError,
    MayaUnavailableError,
)
from maya_mcp_server.types import CommandResponse, ResultType


# ============================================================================
# Length-prefixed frame codec (D-013: 8-16MiB cap -> we take 16 MiB)
# ============================================================================


class TestFrameCodec:
    def test_encode_roundtrip(self):
        payload = json.dumps({"a": 1}).encode("utf-8")
        frame = helper.encode_frame(payload)
        assert frame[: helper.FRAME_HEADER_SIZE] == len(payload).to_bytes(4, "big")
        assert frame[helper.FRAME_HEADER_SIZE :] == payload

    def test_encode_rejects_oversize_payload(self):
        with pytest.raises(helper.FrameTooLargeError):
            helper.encode_frame(b"x" * (helper.MAX_FRAME_SIZE + 1))

    def test_frame_cap_in_spec_band(self):
        assert 8 * 1024 * 1024 <= helper.MAX_FRAME_SIZE <= 16 * 1024 * 1024

    def test_decoder_single_frame(self):
        d = helper.FrameDecoder()
        payload = b'{"id":"r1"}'
        assert d.feed(helper.encode_frame(payload)) == [payload]

    def test_decoder_split_header_and_payload(self):
        d = helper.FrameDecoder()
        payload = b"hello world"
        frame = helper.encode_frame(payload)
        assert d.feed(frame[:2]) == []
        assert d.feed(frame[2:4]) == []
        assert d.feed(frame[4:6]) == []
        assert d.feed(frame[6:]) == [payload]

    def test_decoder_concatenated_frames(self):
        d = helper.FrameDecoder()
        blob = helper.encode_frame(b"one") + helper.encode_frame(b"two")
        assert d.feed(blob) == [b"one", b"two"]

    def test_decoder_oversize_declared_length(self):
        d = helper.FrameDecoder()
        with pytest.raises(helper.FrameTooLargeError) as ei:
            d.feed((helper.MAX_FRAME_SIZE + 10).to_bytes(4, "big"))
        assert ei.value.declared == helper.MAX_FRAME_SIZE + 10

    def test_decoder_survives_empty_feed(self):
        d = helper.FrameDecoder()
        assert d.feed(b"") == []


# ============================================================================
# dispatch_request \u2014 pure function, no Qt required
# ============================================================================


class TestDispatchRequest:
    def test_ping(self):
        resp = helper.dispatch_request({"id": "1", "method": "ping"})
        assert resp == {"id": "1", "result": "pong", "error": None}

    def test_health_minimal(self):
        resp = helper.dispatch_request({"id": "h", "method": "health"})
        assert resp["error"] is None
        assert resp["result"]["status"] == "ok"

    def test_health_with_server_stats(self):
        server = MagicMock()
        server.port = 51234
        server._channels = {"a": object(), "b": object()}
        server._started_at = time.monotonic() - 1.5
        resp = helper.dispatch_request({"id": "h", "method": "health"}, server=server)
        assert resp["result"]["clients"] == 2
        assert resp["result"]["port"] == 51234
        assert resp["result"]["uptime_s"] >= 1.0

    def test_unknown_method(self):
        resp = helper.dispatch_request({"id": "9", "method": "nope"})
        assert resp["result"] is None
        assert resp["error"]["code"] == "unknown_method"

    def test_execute_expression(self):
        resp = helper.dispatch_request(
            {
                "id": "e",
                "method": "execute",
                "params": {"code": "1 + 1", "result_type": "JSON"},
            }
        )
        assert resp["error"] is None
        assert resp["result"] == "2"

    def test_execute_error_passthrough(self):
        resp = helper.dispatch_request(
            {
                "id": "e",
                "method": "execute",
                "params": {"code": "raise ValueError('bad')", "result_type": "NONE"},
            }
        )
        assert resp["error"]["type"] == "builtins.ValueError"
        assert "bad" in resp["error"]["message"]

    def test_internal_dispatch_exception_returns_error(self):
        resp = helper.dispatch_request({"id": "x", "method": "get_session_info"})
        # no maya.cmds in CI -> handler raises -> structured error, not a crash
        assert resp["id"] == "x"
        assert resp["result"] is None
        assert resp["error"] is not None


class TestHandleFrame:
    def test_bad_json_frame(self):
        resp = helper.handle_frame(b"not json {{{")
        assert resp["id"] is None
        assert resp["result"] is None
        assert resp["error"] is not None

    def test_non_dict_request(self):
        resp = helper.handle_frame(b"[1, 2, 3]")
        assert resp["id"] is None
        assert resp["error"] is not None


# ============================================================================
# ClientChannel \u2014 per-connection command queue over framed protocol
# ============================================================================


class FakeSocket:
    """Duck-typed QTcpSocket stand-in for CI tests."""

    def __init__(self):
        self._incoming = bytearray()
        self.written = bytearray()
        self.closed = False

    def readAll(self):  # noqa: N802 - mirrors QTcpSocket API
        data = bytes(self._incoming)
        self._incoming.clear()
        return data

    def feed(self, data: bytes):
        self._incoming += data

    def write(self, data: bytes):
        self.written += data
        return len(data)

    def flush(self):
        pass

    def disconnectFromHost(self):  # noqa: N802 - mirrors QTcpSocket API
        self.closed = True


def _decode_all(blob: bytes) -> list:
    d = helper.FrameDecoder()
    return d.feed(bytes(blob))


class TestClientChannel:
    def test_two_frames_answered_in_order(self):
        sock = FakeSocket()
        ch = helper.ClientChannel(sock)
        sock.feed(helper.encode_frame(json.dumps({"id": "1", "method": "ping"}).encode()))
        sock.feed(helper.encode_frame(json.dumps({"id": "2", "method": "ping"}).encode()))
        ch.feed_socket()
        frames = _decode_all(bytes(sock.written))
        assert len(frames) == 2
        r1, r2 = (json.loads(f) for f in frames)
        assert r1["id"] == "1" and r1["result"] == "pong"
        assert r2["id"] == "2" and r2["result"] == "pong"

    def test_burst_write_drains_fifo(self):
        sock = FakeSocket()
        ch = helper.ClientChannel(sock)
        blob = b"".join(
            helper.encode_frame(json.dumps({"id": str(i), "method": "ping"}).encode())
            for i in range(3)
        )
        sock.feed(blob)
        ch.feed_socket()
        frames = _decode_all(bytes(sock.written))
        assert [json.loads(f)["id"] for f in frames] == ["0", "1", "2"]

    def test_oversize_frame_errors_and_closes(self):
        sock = FakeSocket()
        ch = helper.ClientChannel(sock)
        sock.feed((helper.MAX_FRAME_SIZE + 1).to_bytes(4, "big") + b"x" * 64)
        ch.feed_socket()
        frames = _decode_all(bytes(sock.written))
        resp = json.loads(frames[0])
        assert resp["error"]["code"] == "frame_too_large"
        assert sock.closed is True
        assert ch.closed is True

    def test_bad_json_frame_gets_error_and_stays_open(self):
        sock = FakeSocket()
        ch = helper.ClientChannel(sock)
        sock.feed(helper.encode_frame(b"{oops"))
        ch.feed_socket()
        resp = json.loads(_decode_all(bytes(sock.written))[0])
        assert resp["error"] is not None
        assert sock.closed is False

    def test_oversize_response_collapses_to_error_frame(self):
        sock = FakeSocket()
        ch = helper.ClientChannel(sock, max_frame_size=256)
        # small request in, big result out -> response collapses to error frame
        req = {
            "id": "p",
            "method": "execute",
            "params": {"code": "'y' * 500", "result_type": "RAW"},
        }
        sock.feed(helper.encode_frame(json.dumps(req).encode()))
        ch.feed_socket()
        resp = json.loads(_decode_all(bytes(sock.written))[0])
        assert resp["id"] == "p"
        assert resp["error"]["code"] == "response_too_large"


# ============================================================================
# StreamWriter 5 MiB cap (helper :35 orphan constant, wired by T-05)
# ============================================================================


class TestStreamWriterCap:
    def test_buffer_bounded_by_cap(self):
        w = helper.StreamWriter("stdout", None)
        w.write("x" * helper._MAX_STREAM_BUFFER_SIZE)
        w.write("y" * 1000)
        buf = w.get_buffer()
        assert len(buf) <= helper._MAX_STREAM_BUFFER_SIZE
        assert buf.endswith("y" * 1000)

    def test_get_buffer_resets(self):
        w = helper.StreamWriter("stdout", None)
        w.write("abc")
        assert w.get_buffer() == "abc"
        assert w.get_buffer() == ""

    def test_wrapped_stream_still_receives_text(self):
        out = []
        wrapped = SimpleNamespace(write=out.append, flush=lambda: None)
        w = helper.StreamWriter("stdout", wrapped)
        w.write("hi")
        assert out == ["hi"]


# ============================================================================
# MayaQtClient wire protocol vs a real asyncio framed peer
# ============================================================================


class _Peer:
    """asyncio server speaking the length-prefixed protocol."""

    def __init__(self, handler=None, conn=None):
        self.handler = handler or self._default_handler
        self.conn_override = conn
        self.received = []
        self._server = None
        self.port = 0

    async def start(self):
        self._server = await asyncio.start_server(self.conn_override or self._conn, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def _default_handler(self, req):
        method = req.get("method")
        if method == "ping":
            return {"id": req["id"], "result": "pong", "error": None}
        if method == "health":
            return {"id": req["id"], "result": {"status": "ok"}, "error": None}
        if method == "execute":
            return {
                "id": req["id"],
                "result": {"echo": req["params"]["code"]},
                "error": None,
            }
        return {
            "id": req["id"],
            "result": None,
            "error": {"code": "unknown_method", "message": "nope"},
        }

    async def _conn(self, reader, writer):
        try:
            while True:
                header = await reader.readexactly(helper.FRAME_HEADER_SIZE)
                n = int.from_bytes(header, "big")
                payload = await reader.readexactly(n)
                req = json.loads(payload)
                self.received.append(req)
                resp = self.handler(req)
                if inspect.iscoroutine(resp):
                    resp = await resp
                if resp is None:
                    continue
                writer.write(helper.encode_frame(json.dumps(resp).encode("utf-8")))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


@pytest.fixture
async def peer():
    p = _Peer()
    await p.start()
    yield p
    await p.stop()


class TestQtClientWire:
    async def test_execute_roundtrip(self, peer):
        client = MayaQtClient(host="127.0.0.1", port=peer.port, timeout=5)
        await client.connect()
        try:
            resp = await client.execute_code("print(1)", result_type=ResultType.JSON)
            assert resp.error is None
            assert resp.result == {"echo": "print(1)"}
        finally:
            await client.disconnect()
        req = peer.received[0]
        assert req["method"] == "execute"
        assert req["params"] == {"code": "print(1)", "result_type": "JSON"}
        assert req["id"].startswith("req-")

    async def test_ping_pong(self, peer):
        client = MayaQtClient(host="127.0.0.1", port=peer.port, timeout=5)
        await client.connect()
        try:
            assert await client.ping() is True
        finally:
            await client.disconnect()

    async def test_health_probe(self, peer):
        client = MayaQtClient(host="127.0.0.1", port=peer.port, timeout=5)
        await client.connect()
        try:
            health = await client.health()
            assert health["status"] == "ok"
        finally:
            await client.disconnect()

    async def test_error_passthrough_no_raise(self, peer):
        peer.handler = lambda req: {
            "id": req["id"],
            "result": None,
            "error": {"message": "kaboom"},
        }
        client = MayaQtClient(host="127.0.0.1", port=peer.port, timeout=5)
        await client.connect()
        try:
            resp = await client._send_receive("x", raise_on_error=False)
            assert resp.error["message"] == "kaboom"
            with pytest.raises(MayaExecutionError, match="kaboom"):
                await client._send_receive("x")
        finally:
            await client.disconnect()

    async def test_response_id_mismatch(self, peer):
        peer.handler = lambda req: {"id": "bogus", "result": None, "error": None}
        client = MayaQtClient(host="127.0.0.1", port=peer.port, timeout=5)
        await client.connect()
        try:
            with pytest.raises(MayaExecutionError, match="mismatch"):
                await client._send_receive("ping")
        finally:
            await client.disconnect()

    async def test_oversize_response_frame_rejected(self):
        async def conn(reader, writer):
            try:
                header = await reader.readexactly(helper.FRAME_HEADER_SIZE)
                n = int.from_bytes(header, "big")
                await reader.readexactly(n)
                # Declare an over-cap frame, send only a tiny body
                writer.write((helper.MAX_FRAME_SIZE + 1).to_bytes(4, "big") + b"{}")
                await writer.drain()
            finally:
                writer.close()

        p = _Peer(conn=conn)
        await p.start()
        client = MayaQtClient(host="127.0.0.1", port=p.port, timeout=5)
        await client.connect()
        try:
            with pytest.raises(MayaExecutionError, match="frame"):
                await client._send_receive("ping")
        finally:
            await client.disconnect()
            await p.stop()

    async def test_timeout_raises_typed_error(self, peer):
        async def slow(req):
            await asyncio.sleep(5)
            return {"id": req["id"], "result": "pong", "error": None}

        peer.handler = slow
        client = MayaQtClient(host="127.0.0.1", port=peer.port, timeout=0.2)
        await client.connect()
        try:
            with pytest.raises(MayaTimeoutError) as ei:
                await client._send_receive("ping")
            assert ei.value.code == "maya_timeout"
            # stays catchable as the legacy connection-error type
            assert isinstance(ei.value, MayaConnectionError)
        finally:
            await client.disconnect()

    async def test_connection_closed_raises_unavailable(self):
        async def conn(reader, writer):
            writer.close()

        p = _Peer(conn=conn)
        await p.start()
        client = MayaQtClient(host="127.0.0.1", port=p.port, timeout=5)
        await client.connect()
        try:
            with pytest.raises(MayaUnavailableError) as ei:
                await client._send_receive("ping")
            assert ei.value.code == "maya_unavailable"
        finally:
            await client.disconnect()
            await p.stop()

    async def test_connect_retries_then_raises(self, monkeypatch):
        calls = 0

        async def failing(*a, **k):
            nonlocal calls
            calls += 1
            raise OSError("refused")

        monkeypatch.setattr(asyncio, "open_connection", failing)
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        client = MayaQtClient(port=9, timeout=1)
        with pytest.raises(MayaUnavailableError):
            await client.connect()
        assert calls == 4  # 3 retries on the 0.5/1/2s backoff schedule

    async def test_large_framed_payload_roundtrip(self, peer):
        big = "x" * 2_000_000  # ~2MB \u2014 over the old line protocol's pain point
        client = MayaQtClient(host="127.0.0.1", port=peer.port, timeout=10)
        await client.connect()
        try:
            resp = await client.execute_code(big)
            assert resp.error is None
            assert peer.received[0]["params"]["code"] == big
        finally:
            await client.disconnect()


# ============================================================================
# QtCommandServer \u2014 real localhost roundtrip (PySide6-gated)
# ============================================================================


class TestQtServerIntegration:
    def test_real_socket_roundtrip(self):
        QtCore = pytest.importorskip("PySide6.QtCore")  # noqa: N806
        pytest.importorskip("PySide6.QtNetwork")

        app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
        server = helper.QtCommandServer(0)
        server.start()
        try:
            sock = socket.create_connection(("127.0.0.1", server.port), timeout=5)
            sock.setblocking(False)
            try:
                reqs = [{"id": f"r{i}", "method": "ping"} for i in range(3)]
                reqs.append({"id": "h", "method": "health"})
                blob = b"".join(helper.encode_frame(json.dumps(r).encode()) for r in reqs)
                sock.sendall(blob)
                decoder = helper.FrameDecoder()
                out = []
                deadline = time.time() + 5
                while time.time() < deadline and len(out) < 4:
                    app.processEvents()
                    try:
                        data = sock.recv(65536)
                        if data:
                            out.extend(decoder.feed(data))
                    except BlockingIOError:
                        pass
                    time.sleep(0.005)
                assert len(out) == 4
                resps = [json.loads(f) for f in out]
                assert [r["id"] for r in resps] == ["r0", "r1", "r2", "h"]
                assert [r["result"] for r in resps[:3]] == ["pong"] * 3
                assert resps[3]["result"]["status"] == "ok"
                assert resps[3]["result"]["port"] == server.port
            finally:
                sock.close()
        finally:
            server.stop()

    def test_start_qt_server_with_event_loop(self):
        """Inverse pin for the headless guard: with a live QCoreApplication,
        start_qt_server must actually bind (not report qt_unavailable)."""
        QtCore = pytest.importorskip("PySide6.QtCore")  # noqa: N806

        app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
        assert app is not None
        try:
            out = json.loads(helper.start_qt_server(0))
            assert "error" not in out
            assert out["port"] > 0
        finally:
            json.loads(helper.stop_qt_server())
        assert helper._qt_server is None


# ============================================================================
# scene_tools injection routing: framed GUI channel vs native headless
# ============================================================================


class TestModuleInjectionRouting:
    async def test_framed_client_uses_write_module_for_large(self, monkeypatch, tmp_path):
        scene_tools._injected_sessions.discard("k-f")
        client = MagicMock()
        client.framed_channel = True
        client.write_module = AsyncMock(return_value="ok")
        client.execute_code = AsyncMock(return_value=CommandResponse(result=None, error=None))
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        await scene_tools._ensure_module_injected(client, "k-f")

        client.write_module.assert_awaited_once()
        args, kwargs = client.write_module.await_args
        assert args[0] == "_mcp_scene"
        assert len(args[1]) > 15000
        assert kwargs.get("overwrite") is True
        assert not (tmp_path / "_mcp_scene_src.py").exists()
        scene_tools._injected_sessions.discard("k-f")

    async def test_native_client_keeps_tempfile_fallback(self, monkeypatch, tmp_path):
        """D-082a: native fallback still routes through a staged temp
        file — but now it is a unique mkstemp name inside the
        platformdirs cache, permission-locked, and unlinked after use."""
        import os
        from pathlib import Path

        import platformdirs

        scene_tools._injected_sessions.discard("k-n")
        client = MagicMock()
        client.framed_channel = False
        client.write_module = AsyncMock()

        inject_dir = Path(platformdirs.user_cache_dir("mcp-for-maya")) / "inject"
        pre = set(inject_dir.glob("_mcp_scene_src_*.py")) if inject_dir.exists() else set()
        staged = []

        async def exec_code(code, result_type=None):
            # The follow-up "import _mcp_scene" call runs post-unlink —
            # only record while the staged file actually exists.
            now = sorted(inject_dir.glob("_mcp_scene_src_*.py"))
            if now:
                staged.extend(p.name for p in now)
            return CommandResponse(result=None, error=None)

        client.execute_code = AsyncMock(side_effect=exec_code)

        await scene_tools._ensure_module_injected(client, "k-n")

        client.write_module.assert_not_called()
        assert staged, "native path must stage a mkstemp file during execute_code"
        assert all(n != "_mcp_scene_src.py" for n in staged), "unique name, not shared"
        if os.name != "nt":
            assert (inject_dir / staged[0]).exists() is False  # unlinked
        post = set(inject_dir.glob("_mcp_scene_src_*.py")) if inject_dir.exists() else set()
        assert post == pre, "temp file must be unlinked after injection"
        assert client.execute_code.await_count == 2  # temp-read exec + pre-import
        scene_tools._injected_sessions.discard("k-n")


# ============================================================================
# add_session default port aligns with docs (server.py :337 debt item)
# ============================================================================


def test_add_session_default_port_is_7001():
    from maya_mcp_server import server

    sig = inspect.signature(getattr(server.add_session, "fn", server.add_session))
    assert sig.parameters["port"].default == 7001


class TestHeadlessGuard:
    """F-1B: start_qt_server must refuse when no Qt event loop exists
    (mayapy ships PySide6 and listen() would succeed, yielding a zombie
    channel that accepts TCP but never dispatches readyRead)."""

    def test_no_event_loop_returns_domain_error(self, monkeypatch):
        monkeypatch.setattr(
            helper,
            "QCoreApplication",
            SimpleNamespace(instance=staticmethod(lambda: None)),
        )
        monkeypatch.setattr(helper, "_qt_server", None)
        out = json.loads(helper.start_qt_server(0))
        assert "error" in out
        assert out["error"]["code"] == "qt_unavailable_headless"
        assert "suggestion" in out["error"]
        assert helper._qt_server is None  # nothing was bound


class TestQtSendReceiveDomainCode:
    """F-3: wire domain codes must survive into MayaExecutionError text."""

    @pytest.mark.asyncio
    async def test_unknown_method_raises_with_code(self):
        client = MayaQtClient(port=0)

        async def peer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            hdr = await reader.readexactly(helper.FRAME_HEADER_SIZE)
            n = int.from_bytes(hdr, "big")
            await reader.readexactly(n)
            body = json.dumps(
                {
                    "id": "req-1",
                    "result": None,
                    "error": {"code": "unknown_method", "message": "Unknown method: nope"},
                }
            ).encode()
            writer.write(helper.encode_frame(body))
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(peer, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        reader = writer = None
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            client._reader, client._writer = reader, writer
            with pytest.raises(MayaExecutionError, match="unknown_method"):
                await client._send_receive("nope")
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            server.close()
            await server.wait_closed()
