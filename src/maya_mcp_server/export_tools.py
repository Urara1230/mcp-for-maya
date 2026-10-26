"""MCP export tool: scene_export.

scene_export writes Maya scene content to a host-local file (FBX/OBJ/
USD) through the lazily injected _mcp_export module. It is
channel-agnostic (native + Qt sessions) and does not mutate the scene
- the selection set is saved and restored Maya-side - so mark_dirty is
deliberately NOT called: the only effect is the file on disk.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from maya_mcp_server.client import ensure_module_injected, exec_module_code, module_call
from maya_mcp_server.pipeline import TOOL_ANNOTATIONS
from maya_mcp_server.security import InputValidationError


logger = logging.getLogger(__name__)

# Module source path for _mcp_export
_MODULE_SOURCE = Path(__file__).parent / "export_module.py"

# Sessions that already carry the injected module
_export_injected: set[str] = set()


async def _ensure_export_injected(client: Any, session_key: str | None) -> None:
    """Inject _mcp_export once per session (mirrors _mcp_asset).

    Delegates to client.ensure_module_injected — the channel
    contract (framed write_module vs native mkstemp fallback)
    lives there.
    """
    await ensure_module_injected(
        client,
        session_key,
        module_name="_mcp_export",
        source_path=_MODULE_SOURCE,
        tmp_prefix="_mcp_export_src_",
        injected_sessions=_export_injected,
    )


def register_export_tools(mcp: Any) -> None:
    """Register the export tool on the FastMCP instance.

    Args:
        mcp: FastMCP server instance.
    """

    @mcp.tool(annotations=TOOL_ANNOTATIONS["scene_export"])  # type: ignore[untyped-decorator]
    async def scene_export(
        path: str,
        format: str | None = None,
        objects: list[str] | None = None,
        overwrite: bool = False,
        session_key: str | None = None,
    ) -> str:
        """Export the Maya scene (or named objects) to a local file.

        Writes through cmds.file with prompt=False forced (a modal
        dialog would hang the command channel). Formats: fbx, obj, usd
        (Alembic is not supported - its AbcExport API is not a
        cmds.file surface). The selection set is restored after every
        objects= export and the scene's modified flag is untouched.

        destructive_hint is False but note: overwrite=True permanently
        replaces the target file - that path is not recoverable.

        Args:
            path: Target file path on the Maya host. Normalized and
                auto-parented (missing directories are created). An
                existing file is rejected unless overwrite=True.
            format: "fbx", "obj", or "usd". Optional when the path
                carries a recognizable extension; a missing extension
                is appended for an explicit format, and a conflicting
                extension+format pair is an error, never a guess.
            objects: Node names to export. None (default) exports the
                whole scene via exportAll; a list is pre-validated
                (any missing name aborts - no partial export) and
                drives exportSelected.
            overwrite: Replace an existing target file (default False).
            session_key: Maya session key.

        Returns:
            JSON {path, format, objects_exported, size_bytes,
            duration_ms, warnings}. Domain failures return
            {"error": {code, message}} - invalid_format, invalid_path,
            empty_objects, missing_objects, plugin_missing,
            export_failed.
        """
        # Host-side type validation; the domain contract (format
        # inference, existence, selection restore) lives Maya-side.
        if not isinstance(path, str) or not path.strip():
            raise InputValidationError("scene_export requires a non-empty path string")
        if format is not None and not isinstance(format, str):
            raise InputValidationError("format must be a string (fbx/obj/usd)")
        if objects is not None and (
            not isinstance(objects, list) or not all(isinstance(o, str) for o in objects)
        ):
            raise InputValidationError("objects must be a list of node names")

        from maya_mcp_server.server import get_session_manager

        manager = get_session_manager()
        client = await manager.get_client(session_key)
        await _ensure_export_injected(client, session_key)

        result = await exec_module_code(
            client,
            module_call(
                "_mcp_export",
                "export_scene",
                path,
                format=format,
                objects=objects,
                overwrite=bool(overwrite),
            ),
        )
        return json.dumps(result, indent=2)
