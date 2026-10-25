"""FastMCP server for Maya integration."""

from __future__ import annotations

import importlib.metadata
import logging
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from maya_mcp_server.asset_tools import register_asset_tools
from maya_mcp_server.client import raise_for_error
from maya_mcp_server.connection_guide import (
    get_agent_connection_instructions,
    get_connection_diagnostics,
    get_fallback_instructions,
    install_user_setup,
    uninstall_user_setup,
)
from maya_mcp_server.export_tools import register_export_tools
from maya_mcp_server.introspect_tools import register_introspect_tools
from maya_mcp_server.pipeline import TOOL_ANNOTATIONS, SecurityPipeline
from maya_mcp_server.scene_tools import mark_dirty, register_scene_tools
from maya_mcp_server.security import (
    AuditLogger,
    InputValidationError,
    PipelineError,
    SecurityConfig,
    ServerNotReadyError,
    sanitize_error_message,
    validate_module_name,
    validate_session_key,
)
from maya_mcp_server.session_manager import SessionManager
from maya_mcp_server.types import ClientType, OutputBuffer, ResultType, SessionInfo
from maya_mcp_server.utils import is_loopback_host
from maya_mcp_server.visual_tools import register_visual_tools


logger = logging.getLogger(__name__)

# Security configuration
_security_config = SecurityConfig()


def _product_version() -> str:
    """Product version for the handshake's serverInfo.

    serverInfo.version is the implementation version per the MCP spec
    (protocolVersion travels separately) - left unset, FastMCP leaks its
    own framework version. Dist metadata is authoritative; the __version__
    literal is the source-tree fallback when the package isn't installed.
    """
    try:
        return importlib.metadata.version("mcp-for-maya")
    except importlib.metadata.PackageNotFoundError:
        from maya_mcp_server import __version__

        return __version__


# Initialize FastMCP server with instructions for cross-tool workflows
mcp = FastMCP(
    "Maya MCP Server",
    version=_product_version(),
    instructions=(
        "This server provides tools to interact with Autodesk Maya 3D sessions.\n\n"
        "## Connection & Setup\n"
        "- maya_setup_guide: Diagnose connection, install userSetup.py, get fallback instructions\n"
        "  Actions: diagnose | install | guide | uninstall\n"
        "  Use when list_sessions returns empty or on first-time setup.\n\n"
        "## Spatial Awareness (ICEV Workflow)\n"
        "1. INSPECT: scene_snapshot() for spatial overview\n"
        "2. COMPUTE: Use spatial data to plan changes\n"
        "3. EXECUTE: execute_code() to apply changes\n"
        "4. VERIFY: scene_assert() to confirm results;\n"
        "   scene_viewport_snapshot() for visual confirmation (GUI)\n\n"
        "## Scene Tools\n"
        "- scene_snapshot: Full scene spatial overview\n"
        "- scene_inspect: Deep inspection of object/zone\n"
        "- scene_measure: Distance/clearance between objects\n"
        "- scene_assert: Verify scene state\n\n"
        "## Scene-Graph Introspection\n"
        "- scene_describe: API-level node self-description - exact type,\n"
        "  attribute metadata (keyable/connectable/enum/ranges) and\n"
        "  connection wiring; use before composing execute_code\n"
        "  setAttr/connectAttr calls (spatial questions -> scene_inspect);\n"
        "  responses are bounded - 200 items per face by default,\n"
        "  relaxable to 1000 via limit=; *_truncated flags + totals\n"
        "  disclose any cut (narrow via attrs=[...] and re-query)\n"
        "- scene_nodes: bounded enumeration incl. non-DAG nodes\n"
        "  (materials/tool nodes); type/pattern/dag_only filters +\n"
        "  has_more/next_cursor pagination\n\n"
        "## Constraint & Safety Tools\n"
        "- scene_validate: Read-only spatial constraint validation (clearance,\n"
        "  overlap, height); without auto_fix mutations (automated repair belongs\n"
        "  to scene_plan); disambiguated from scene_review (heuristic audit) and\n"
        "  scene_assert (boolean gate checks)\n"
        "- scene_checkpoint: exportAll snapshot of in-memory state into checkpoints/\n"
        "  (name whitelist, ad-hoc for untitled scenes)\n"
        "- scene_checkpoint_list: List saved checkpoints\n"
        "- scene_rollback: Open a checkpoint, rebind scene to original path\n"
        "  (auto safety snapshot first)\n\n"
        "## Camera & Shot Tools\n"
        "- camera_create: Create camera with shot type (wide/medium/close/etc)\n"
        "- camera_orbit: Create orbiting camera with animation (orbit/turntable;\n"
        "  static shot-type framing belongs to camera_create)\n\n"
        "## Visual Loop (GUI sessions only)\n"
        "- scene_viewport_snapshot: WYSIWYG viewport capture\n"
        "  (HUD/selection included - what the artist sees)\n"
        "- scene_render_preview: clean single-frame playblast\n"
        "  (camera optional; net-zero side effect)\n\n"
        "## Aesthetic Analysis (5 Professional Dimensions)\n"
        "- scene_aesthetics: Professional-grade analysis with 5 dimensions:\n"
        "  1. Color Theory: 60-30-10 rule, temperature, harmony, saturation, contrast\n"
        "  2. Spatial Composition: golden ratio, rule-of-thirds, visual weight balance\n"
        "  3. Proportion & Scale: human ergonomics, size hierarchy (hero/secondary/tertiary)\n"
        "  4. Lighting Quality: layer composition, color temperature consistency\n"
        "  5. Visual Flow: sight lines, circulation clarity, visual rhythm\n"
        "  Returns: overall score (0-100), grade (S/A/B/C/D/F), improvement suggestions\n"
        "- scene_review: Comprehensive audit after operations (score 0-100)\n"
        "  Checks: spatial, overlaps, zones, aesthetics, constraints, orphans, naming,\n"
        "  components, conflicts, lighting, organization\n\n"
        "## Scene Planning\n"
        "- scene_plan: Holistic scene planning with organization validation,\n"
        "  layout optimization, conflict prevention\n\n"
        "## Asset Tools (Poly Haven CC0)\n"
        "- asset_search: Search the Poly Haven index (host-side; no session)\n"
        "- asset_import: Download (https whitelist + md5 + cache) and\n"
        "  import FBX into Maya with texture auto-wiring; idempotent via\n"
        "  GRP_asset_<id> dedup\n\n"
        "## Scene Export\n"
        "- scene_export: Export the scene (or named objects) to FBX/OBJ/USD\n"
        "  (path normalized, parents auto-created, overwrite opt-in,\n"
        "  selection restored; Alembic is not supported)\n\n"
        "## General Tools\n"
        "- list_sessions: Discover active Maya sessions\n"
        "  (if empty, call maya_setup_guide for connection help)\n"
        "- write_module: Define in-memory virtual Python modules in an active session\n"
        "  with overwrite semantics; disambiguated from execute_code (use execute_code\n"
        "  for one-off snippets, queries, or expression evaluation)\n"
        "- execute_code: Execute arbitrary Python or MEL code with full session\n"
        "  privileges in an active session; irreversible (cannot be undone);\n"
        "  returns structured response envelopes via result_type; disambiguated from\n"
        "  write_module (module definition) and domain-specific scene tools\n\n"
        "Best practices:\n"
        "- Call scene_snapshot() before modifications\n"
        "- Use scene_checkpoint() before risky operations\n"
        "- Snapshots carry no undo history; after scene_rollback call "
        "scene_snapshot to rebuild context\n"
        "- Snapshots are self-contained (references flattened, no write-back)\n"
        "  one scene file per session assumed\n"
        "- Use scene_validate() to check constraints after changes\n"
        "- Cache auto-invalidates after execute_code/write_module"
    ),
)

# Register scene tools on the MCP instance
register_scene_tools(mcp)
register_visual_tools(mcp)

# One AuditLogger shared by the pipeline (per-call events) and the
# asset tools (result-side download detail: URL/size/files_hash).
_audit_logger = (
    AuditLogger(Path(_security_config.audit_log_path) if _security_config.audit_log_path else None)
    if _security_config.audit_enabled
    else None
)
register_asset_tools(mcp, audit=_audit_logger)
register_export_tools(mcp)
register_introspect_tools(mcp)

# Unified security pipeline: every tool call passes through validation,
# rate limiting, pattern scanning, and audit logging.
mcp.add_middleware(SecurityPipeline(config=_security_config, audit=_audit_logger))

# Global session manager - initialized when server starts
_session_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """Get the global session manager."""
    if _session_manager is None:
        raise ServerNotReadyError(
            "Session manager not initialized",
            suggestion="the server has not finished startup; retry after lifespan init",
        )
    return _session_manager


# MCP Tools


@mcp.tool(annotations=TOOL_ANNOTATIONS["list_sessions"])
async def list_sessions() -> list[SessionInfo]:
    """
    List all active Maya sessions.

    Returns a list of session information including:
    - session_key: Session key used to interact with tools and resources
    - host: Session host address
    - port: Session port number
    - pid: Maya process ID
    - user: Logged-in user
    - maya_version: Maya version string
    - scene_name: Current scene filename
    - scene_path: Full path to current scene

    Note: To detect new or removed sessions, clients should call this tool
    periodically (e.g., every 10-30 seconds) and compare results. The SessionManager
    automatically scans for new Maya sessions in the background.

    If this returns an empty list, call maya_setup_guide() for connection help.
    """
    manager = get_session_manager()
    sessions = await manager.list_sessions()
    if not sessions:
        logger.info("No Maya sessions found. Call maya_setup_guide() for connection help.")
    return sessions


@mcp.tool(annotations=TOOL_ANNOTATIONS["maya_setup_guide"])
async def maya_setup_guide(
    action: str = "diagnose",
    port: int = 7001,
    target_version: str | None = None,
    confirm: bool = False,
    remove_empty_file: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Maya connection setup guide and diagnostics.

    Use this tool when list_sessions returns empty or when setting up
    Maya MCP for the first time. Provides platform-aware diagnostics,
    auto-installation of userSetup.py, and step-by-step fallback instructions.

    Args:
        action: What to do:
            - "diagnose": Run connection diagnostics and return status
            - "install": Merge the managed marker block into userSetup.py.
              Existing files without the block require a second call with
              confirm=True (the first call returns the proposed block).
            - "guide": Get full step-by-step connection guide
            - "uninstall": Remove the managed marker block from userSetup.py
        port: Maya command port number (default: 7001)
        target_version: Specific Maya version (e.g., "2024").
            If None, targets all detected versions.
        confirm: Required to write into an existing userSetup.py that has
            no mcp-for-maya marker block (first install call is dry-run).
        dry_run: Preview the install: return the proposed block without
            writing anything.
        remove_empty_file: On uninstall, delete the file when it only
            contained the marker block.

    Returns:
        Dict with diagnostics, installation results, or guide text

    Typical workflow:
        1. Call maya_setup_guide(action="diagnose") to check status
        2. If port not open, call maya_setup_guide(action="install")
        3. Restart Maya, then call list_sessions() again
        4. If still failing, follow the guide from action="guide"
    """
    if action == "diagnose":
        return get_connection_diagnostics(port)
    elif action == "install":
        return install_user_setup(
            port=port,
            target_version=target_version,
            dry_run=dry_run,
            confirm=confirm,
        )
    elif action == "guide":
        return {
            "guide": get_fallback_instructions(port),
            "diagnostics": get_connection_diagnostics(port),
            "agent_instructions": get_agent_connection_instructions(port),
        }
    elif action == "uninstall":
        return uninstall_user_setup(
            target_version=target_version,
            remove_empty_file=remove_empty_file,
        )
    else:
        raise InputValidationError(
            f"Invalid action '{action}': must be diagnose, install, guide, or uninstall"
        )


@mcp.tool(annotations=TOOL_ANNOTATIONS["write_module"])
async def write_module(
    name: str,
    code: str,
    overwrite: bool = False,
    session_key: str | None = None,
) -> str:
    """Create a virtual in-memory Python module in an active Maya session.

    Injects reusable Python functions into the target Maya session's sys.modules.
    Modules are virtual and in-memory: they do not persist across Maya restarts,
    and existing modules are protected unless overwrite=True is explicitly set.
    Requires an active Maya session.

    Boundary: Use write_module to define reusable libraries and helper functions;
    use execute_code for one-shot imperative execution or expression evaluation.

    Args:
        name: Module name. Can be a dotted path (e.g., 'mypackage.utils')
              in which case parent packages are created automatically.
        code: Python source code for the module.
        overwrite: If True, overwrite and replace existing module. If False,
                   raise error if module already exists.
        session_key: Maya session key (optional if only one active session exists).

    Returns:
        Success message confirming module registration.

    Example:
        write_module("mytools", '''
        import maya.cmds as cmds

        def create_cube(name="GEO_cube1"):
            return cmds.polyCube(name=name)[0]
        ''')

        # Then use it via execute_code:
        execute_code("import mytools; mytools.create_cube('GEO_myCube')")
    """
    # Semantic validation (generic checks run in the SecurityPipeline)
    validate_module_name(name)

    manager = get_session_manager()
    client = await manager.get_client(session_key)

    result = await client.write_module(name, code, overwrite)

    # Mark scene cache dirty after module write
    mark_dirty(session_key)

    return result


@mcp.tool(annotations=TOOL_ANNOTATIONS["execute_code"])
async def execute_code(
    code: str,
    result_type: str = "NONE",
    session_key: str | None = None,
) -> Any:
    """Execute arbitrary Python or MEL code in an active Maya session.

    Runs arbitrary code with full session privileges of the connected Maya
    process. Execution is irreversible and cannot be undone (modifications
    do not register an automatic undo chunk). The return value shape is
    governed by the result_type envelope parameter.
    Requires an established Maya session (discoverable via list_sessions).

    Args:
        code: Python or MEL code to execute in Maya.
        result_type: Return envelope handling for the execution result:
            - "NONE": Execute statements, don't capture result (returns None)
            - "JSON": Evaluate expression, JSON-encode result
            - "RAW": Evaluate expression, return string representation
        session_key: Maya session key (optional if only one session exists).

    Returns:
        Captured result formatted according to result_type (None if result_type is "NONE").

    Note:
        stdout and stderr are captured and exposed via the
        maya://sessions/{session_key}/output MCP Resource.

    Example:
        # Execute statements (irreversible full privilege execution)
        execute_code("import maya.cmds as cmds; cmds.polyCube(name='GEO_cube')")

        # Get JSON result
        execute_code("cmds.ls(type='mesh')", result_type="JSON")
    """
    # Semantic validation (size/session checks run in the SecurityPipeline)
    try:
        rt = ResultType(result_type)
    except ValueError:
        raise InputValidationError(
            f"Invalid result_type '{result_type}': must be NONE, JSON, or RAW"
        )

    manager = get_session_manager()
    client = await manager.get_client(session_key)

    try:
        result = await client.execute_code(code, rt)
    except Exception as e:
        # Sanitize error messages to avoid leaking internal paths.
        # Coded exceptions keep their [code] prefix via .message.
        raw = getattr(e, "message", str(e))
        if isinstance(e, PipelineError):
            raise type(e)(sanitize_error_message(raw), suggestion=e.suggestion) from e
        raise type(e)(sanitize_error_message(raw)) from e

    # Fetch any buffered output and store it in the client
    try:
        output = await client.get_buffered_output()
        client.append_output(output.stdout, output.stderr)
    except Exception as e:
        logger.debug(f"Failed to get buffered output: {e}")

    # Mark scene cache dirty after code execution
    mark_dirty(session_key)

    # Surface Maya-side execution errors instead of silently dropping them
    raise_for_error(result)

    return result.result


@mcp.tool(annotations=TOOL_ANNOTATIONS["add_session"])
async def add_session(host: str = "127.0.0.1", port: int = 7001) -> SessionInfo:
    """
    Manually add a Maya session at a specific host and port.

    Use this when auto-discovery doesn't find your Maya session,
    or to connect to a Maya instance on a specific port.

    Args:
        host: The session host (default: "127.0.0.1")
        port: The session port number (default: 7001)

    Returns:
        Session information for the added session

    Before using this, ensure Maya has a Python command port open.
    In Maya's Script Editor (Python), run:
        import maya.cmds as cmds
        cmds.commandPort(name=':7001', sourceType='python')
    """
    # Validate port range (aligned with security.validate_session_key)
    if not (0 < port < 65536):
        raise InputValidationError(f"Invalid port {port}: must be 1-65535")

    # Block non-localhost connections by default (single loopback source)
    if not is_loopback_host(host):
        if not _security_config.allow_remote_connections:
            raise InputValidationError(
                f"Remote connection to {host} blocked for security. "
                "Set SecurityConfig.allow_remote_connections=True to enable."
            )
        logger.warning(f"Adding non-localhost session: {host}:{port}")

    manager = get_session_manager()
    client = await manager.add_session(host, port)
    return await client.session_info()


# MCP Resources


@mcp.resource("maya://sessions/{session_key}/info")
async def session_info(session_key: str) -> SessionInfo:
    """
    Get information about a Maya session.

    Args:
        session_key: Session key

    Returns:
        SessionInfo with session details (pid, user, maya_version, scene)
    """
    validate_session_key(session_key)
    manager = get_session_manager()
    client = await manager.get_client(session_key)
    return await client.session_info()


@mcp.resource("maya://sessions/{session_key}/output")
async def session_output(session_key: str, clear: bool = True) -> OutputBuffer:
    """
    Get captured stdout/stderr output from a Maya session.

    Args:
        clear: If True (default), clear the buffer after reading.
               If False, keep the buffer contents.
        session_key: Session key

    Returns:
        OutputBuffer with stdout and stderr fields containing captured output
        since the last call (or since stream capture was installed).

    This provides access to stdout/stderr that was captured during
    execute_code calls. For real-time streaming, subscribe to the
    MCP Resources instead.
    """
    validate_session_key(session_key)
    manager = get_session_manager()
    client = await manager.get_client(session_key)
    return client.get_accumulated_output(clear=clear)


async def initialize_session_manager(
    scan_interval: float = 10.0,
    client_type: str = "qt",
) -> SessionManager:
    """
    Initialize the global session manager.

    Args:
        scan_interval: Seconds between background scans
        client_type: Type of client to use ("native" or "qt")

    Returns:
        The initialized SessionManager
    """
    global _session_manager

    try:
        resolved_type = ClientType(client_type)
    except ValueError:
        raise InputValidationError(
            f"Invalid client_type {client_type!r}: must be one of {[t.value for t in ClientType]}"
        )
    _session_manager = SessionManager(
        scan_interval=scan_interval,
        client_type=resolved_type,
    )
    await _session_manager.start()

    return _session_manager


async def shutdown_session_manager() -> None:
    """Shutdown the global session manager."""
    global _session_manager

    if _session_manager is not None:
        await _session_manager.stop()
        _session_manager = None
