"""Session manager for multiple Maya connections."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from maya_mcp_server.client import BaseMayaClient, MayaClient, MayaConnectionError
from maya_mcp_server.security import SessionLookupError
from maya_mcp_server.types import (
    COMMUNICATION_PORT_MAX,
    COMMUNICATION_PORT_MIN,
    ClientType,
    PortType,
    SessionInfo,
)
from maya_mcp_server.utils import get_maya_listening_ports, is_loopback_host


logger = logging.getLogger(__name__)

# Config-port bootstrap timeout: bootstrap + Qt start can take a while
_CONFIG_PORT_TIMEOUT = 60.0


def _parse_port_filter(raw: str | None) -> set[int] | None:
    """Parse a port filter spec: comma-separated ports or "a-b" ranges.

    "7001,7005-7010" -> {7001, 7005, ..., 7010}. Returns None for empty or
    missing input. Malformed entries raise ValueError so a typo fails loud
    at startup instead of silently scanning everything.
    """
    if not raw:
        return None
    ports: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, _, hi = part.partition("-")
            ports.update(range(int(lo), int(hi) + 1))
        else:
            ports.add(int(part))
    return ports or None


class SessionManager:
    """Manages multiple Maya session connections."""

    def __init__(
        self,
        scan_interval: float = 10.0,
        client_type: ClientType = ClientType.QT,
        failed_port_retry_after: float = 60.0,
        include_ports: set[int] | None = None,
        exclude_ports: set[int] | None = None,
    ):
        """
        Initialize the session manager.

        Args:
            scan_interval: Seconds between background scans
            client_type: Type of client to use for Maya communication
            failed_port_retry_after: Cooldown before a failed config port is
                probed again (_failed_ports dedup)
            include_ports: If set, only these config ports are probed
                (falls back to MAYA_MCP_INCLUDE_PORTS)
            exclude_ports: Config ports never probed (falls back to
                MAYA_MCP_EXCLUDE_PORTS). Env values use "7001,7005-7010" syntax
        """
        self.scan_interval = scan_interval
        self.client_type = client_type
        self.failed_port_retry_after = failed_port_retry_after
        # config ports positively identified as non-Python: the port
        # exemption set - never re-probed while they keep LISTENing
        self._non_python_ports: set[str] = set()
        self.include_ports = (
            include_ports
            if include_ports is not None
            else _parse_port_filter(os.environ.get("MAYA_MCP_INCLUDE_PORTS"))
        )
        self.exclude_ports = (
            exclude_ports
            if exclude_ports is not None
            else _parse_port_filter(os.environ.get("MAYA_MCP_EXCLUDE_PORTS"))
        )
        # key: "host:port" (communication port)
        self._sessions: dict[str, BaseMayaClient] = {}
        # map config key -> session key
        #   we use a "config" command port to bootstrap a dedicated communication port, and only
        #   the latter are considered "sessions"
        self._config_to_session: dict[str, str] = {}
        # config keys whose last probe failed -> monotonic timestamp
        self._failed_ports: dict[str, float] = {}
        # session keys with stream capture
        self._stream_capture_installed: set[str] = set()
        self._scan_task: asyncio.Task[None] | None = None
        self._running = False

    def _session_key(self, host: str, port: int) -> str:
        """Generate unique key for a session."""
        return f"{host}:{port}"

    async def start(self) -> None:
        """Start the session manager and begin background scanning."""
        if self._running:
            return

        self._running = True
        logger.info(f"Starting session manager (client_type={self.client_type.value})")

        # Do initial scan
        await self._scan_for_sessions()

        # Start background scanning task
        self._scan_task = asyncio.create_task(self._background_scan())

    async def stop(self) -> None:
        """Stop scanning and disconnect all sessions."""
        self._running = False

        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
            self._scan_task = None

        # Disconnect all sessions
        for client in self._sessions.values():
            try:
                await client.disconnect()
            except Exception as e:
                logger.debug(f"Error disconnecting client: {e}")

        self._sessions.clear()
        self._config_to_session.clear()
        self._failed_ports.clear()
        self._non_python_ports.clear()
        self._stream_capture_installed.clear()
        logger.info("Session manager stopped")

    async def _background_scan(self) -> None:
        """Periodically scan for new sessions and prune dead ones."""
        consecutive_errors = 0
        max_backoff = 60.0

        while self._running:
            try:
                await asyncio.sleep(self.scan_interval)
                await self._scan_for_sessions()
                await self._prune_dead_sessions()
                consecutive_errors = 0
            except asyncio.CancelledError:
                break
            except Exception as e:
                consecutive_errors += 1
                backoff = min(self.scan_interval * (2**consecutive_errors), max_backoff)
                logger.error(
                    f"Error in background scan (attempt {consecutive_errors}): {e}. "
                    f"Backing off {backoff:.1f}s"
                )
                await asyncio.sleep(backoff)

    async def _scan_for_sessions(self) -> None:
        """Scan for Maya sessions using actual listening ports.

        Only scans configuration ports (outside the communication port range).
        Communication ports are per-client dedicated ports created during bootstrap.
        localhost-only: non-loopback listener addresses are skipped;
        wildcard binds (0.0.0.0/::) normalize to 127.0.0.1. Failed probes are
        deduplicated via _failed_ports with a retry cooldown.
        """
        now = time.monotonic()
        listening_config_keys: set[str] = set()

        for port_info in get_maya_listening_ports():
            host = port_info["address"]
            port = port_info["port"]

            # Wildcard listeners are reachable via loopback
            if host in ("0.0.0.0", "::"):
                host = "127.0.0.1"
            elif not is_loopback_host(host):
                # localhost-only enforcement: a Maya instance bound to a LAN
                # address is out of scope for the local agent
                logger.debug(f"Skipping non-loopback listener {host}:{port}")
                continue

            config_key = self._session_key(host, port)

            # Skip communication ports (dedicated per-client ports)
            if COMMUNICATION_PORT_MIN <= port <= COMMUNICATION_PORT_MAX:
                continue

            listening_config_keys.add(config_key)

            # Skip if we've already used this configuration port to create a session
            if config_key in self._config_to_session:
                continue

            # Permanent exemption: ports that answered the probe as
            # non-Python are never re-probed while they keep LISTENing
            if config_key in self._non_python_ports:
                continue

            # Include/exclude filter (upstream #1 workaround
            # productized): bounds which listener ports get probed at all
            if self.include_ports is not None and port not in self.include_ports:
                continue
            if self.exclude_ports is not None and port in self.exclude_ports:
                continue

            # Deduplicate failed probes: retry only after the cooldown
            failed_at = self._failed_ports.get(config_key)
            if failed_at is not None and (now - failed_at) < self.failed_port_retry_after:
                continue

            # Try to connect to this configuration port
            client = await self._probe_port(host, port)
            if client:
                self._failed_ports.pop(config_key, None)
                # Store session by its communication port key
                self._sessions[client.key] = client
                # Track which config port created this session
                self._config_to_session[config_key] = client.key
                logger.info(
                    f"Discovered Maya session at {client.key} (PID {port_info['process_id']})"
                )
            else:
                self._failed_ports[config_key] = now

        # A port that stopped listening resets its failure record so a new
        # Maya instance on the same port is probed immediately
        for key in [k for k in self._failed_ports if k not in listening_config_keys]:
            del self._failed_ports[key]

        # Same lifecycle for the exemption set: a port leaves it
        # only once it disappears from LISTEN, so whatever shows up on it
        # next gets probed fresh
        for key in [k for k in self._non_python_ports if k not in listening_config_keys]:
            self._non_python_ports.discard(key)

    async def _probe_port(self, host: str, port: int) -> BaseMayaClient | None:
        """
        Probe a port to check if it's a Maya command port.

        Args:
            host: Host to connect to
            port: Port to probe

        Returns:
            MayaClient if successful, None otherwise
        """
        # Use longer timeout to support long-running operations
        client = MayaClient(host, port, timeout=_CONFIG_PORT_TIMEOUT)

        try:
            await client.connect()
            new_client = await client.bootstrap(client_type=self.client_type.value)
            await client.disconnect()
            return new_client
        except MayaConnectionError:
            # A port that answered the probe as definitively
            # non-Python (e.g. a MEL commandPort) goes on the session-level
            # exemption set - one log line per port per session, then the
            # scanner never touches it again while it keeps LISTENing
            if getattr(client, "_port_type", None) is PortType.MEL:
                config_key = self._session_key(host, port)
                self._non_python_ports.add(config_key)
                logger.info(
                    f"Port {config_key} answered as non-Python "
                    f"({PortType.MEL.value}); exempted from probing for this session"
                )
            try:
                await client.disconnect()
            except Exception:
                pass
            return None
        except Exception as e:
            logger.debug(f"Error probing {host}:{port}: {e}")
            try:
                await client.disconnect()
            except Exception:
                pass
            return None

    async def _prune_dead_sessions(self) -> None:
        """Remove sessions that are no longer responding."""
        dead_keys = []

        for key, client in self._sessions.items():
            try:
                if not await client.ping():
                    dead_keys.append(key)
            except Exception as e:
                logger.debug(f"Session {key} appears dead: {e}")
                dead_keys.append(key)

        for session_key in dead_keys:
            client = self._sessions.pop(session_key)
            logger.info(f"Pruned dead session: {session_key}")

            # Remove from config mapping
            config_key = None
            for cfg_key, sess_key in self._config_to_session.items():
                if sess_key == session_key:
                    config_key = cfg_key
                    break
            if config_key:
                del self._config_to_session[config_key]

            # Remove from stream capture tracking
            self._stream_capture_installed.discard(session_key)

            try:
                await client.disconnect()
            except Exception:
                pass

    async def list_sessions(self) -> list[SessionInfo]:
        """
        List all running Maya sessions.

        Returns:
            List of key properties about each session
        """
        results: list[SessionInfo] = []

        for key, client in list(self._sessions.items()):
            try:
                info = await client.session_info()
                results.append(info)
            except Exception as e:
                logger.debug(f"Error getting session info for {key}: {e}")
                # Session might have closed, will be pruned on next scan

        return results

    async def get_session(self, host: str, port: int) -> BaseMayaClient | None:
        """
        Get a session by host and port.

        Args:
            host: Session host
            port: Session port

        Returns:
            MayaClient if found, None otherwise
        """
        key = self._session_key(host, port)
        return self._sessions.get(key)

    async def get_client(self, session_key: str | None = None) -> BaseMayaClient:
        """
        Get a client for the specified session, with auto-selection.

        Args:
            session_key: Session key. If None and only one session exists, auto-selects it.

        Returns:
            The MayaClient for the session

        Raises:
            SessionLookupError: If no sessions exist, multiple sessions exist without
                        explicit selection, or the specified session is not found.

        Stream capture is automatically installed on first access to each session.
        """
        # Auto-select if only one session and no explicit host:port
        client: BaseMayaClient
        if session_key is None:
            if len(self._sessions) == 0:
                raise SessionLookupError(
                    "No Maya sessions available",
                    suggestion=(
                        "call add_session(host, port) first, or maya_setup_guide() for setup"
                    ),
                )
            elif len(self._sessions) == 1:
                client = next(iter(self._sessions.values()))
            else:
                session_keys = list(self._sessions.keys())
                raise SessionLookupError(
                    f"Multiple sessions available: {session_keys}",
                    suggestion="pass session_key='host:port' explicitly",
                )
        else:
            maybe_client = self._sessions.get(session_key)
            if maybe_client is None:
                raise SessionLookupError(
                    f"Session {session_key} not found",
                    suggestion=(
                        "call list_sessions() for connected sessions, or add_session() to connect"
                    ),
                )
            client = maybe_client

        # Auto-install stream capture on first access
        if client.key not in self._stream_capture_installed:
            try:
                await client.install_stream_capture()
                self._stream_capture_installed.add(client.key)
            except Exception as e:
                logger.warning(f"Failed to install stream capture for {client.key}: {e}")

        return client

    @property
    def session_count(self) -> int:
        """Get the number of connected sessions."""
        return len(self._sessions)

    async def add_session(self, host: str, port: int) -> BaseMayaClient:
        """
        Manually add a session at a specific host:port.

        Args:
            host: Session host
            port: Session port

        Returns:
            The connected MayaClient

        Raises:
            MayaConnectionError: If connection fails
        """
        config_key = self._session_key(host, port)

        # Already bootstrapped via this config port -> return its session
        existing = self._config_to_session.get(config_key)
        if existing and existing in self._sessions:
            return self._sessions[existing]
        # Or the key itself is a live session (communication port)
        if config_key in self._sessions:
            return self._sessions[config_key]

        client = MayaClient(host, port, timeout=_CONFIG_PORT_TIMEOUT)
        try:
            await client.connect()
            # bootstrap() returns a NEW client on the dedicated working
            # channel - that is the session, not the config-port client
            new_client = await client.bootstrap(client_type=self.client_type.value)
        except MayaConnectionError:
            # An explicit add on a port that turns out non-Python
            # still earns the exemption so the scanner leaves it alone
            if getattr(client, "_port_type", None) is PortType.MEL:
                self._non_python_ports.add(config_key)
            raise
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

        self._sessions[new_client.key] = new_client
        self._config_to_session[config_key] = new_client.key
        self._failed_ports.pop(config_key, None)
        logger.info(f"Added session: {config_key} -> {new_client.key}")

        return new_client

    async def check_session_health(self, session_key: str) -> bool:
        """Check if a specific session is still responsive.

        Args:
            session_key: Session key to check

        Returns:
            True if session responds to ping, False otherwise
        """
        client = self._sessions.get(session_key)
        if client is None:
            return False
        try:
            # Framed (Qt) clients expose a richer health probe;
            # native clients fall back to ping.
            if getattr(client, "framed_channel", False):
                health_fn = getattr(client, "health", None)
                if health_fn is not None:
                    health = await health_fn()
                    return isinstance(health, dict) and health.get("status") == "ok"
            return await client.ping()
        except Exception as e:
            logger.debug(f"Health check failed for {session_key}: {e}")
            return False

    async def get_session_stats(self) -> dict[str, Any]:
        """Get statistics about current sessions.

        Returns:
            Dict with session count, health status, and uptime info
        """
        healthy = 0
        unhealthy = 0
        for key, client in self._sessions.items():
            try:
                if await client.ping():
                    healthy += 1
                else:
                    unhealthy += 1
            except Exception:
                unhealthy += 1
        return {
            "total_sessions": len(self._sessions),
            "healthy": healthy,
            "unhealthy": unhealthy,
            "scan_interval": self.scan_interval,
            "client_type": self.client_type.value,
        }
