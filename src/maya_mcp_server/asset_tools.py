"""MCP asset tools: Poly Haven search + import.

asset_search is host-side read-only - no Maya session needed.
asset_import chains the host-side downloader (whitelist + md5 + size
guards, platformdirs cache) into the Maya-side _mcp_asset importer.
Both tools carry open_world_hint=True: they are the first tools that
reach the open network, restricted to the Poly Haven whitelist
(threat-model.md section 5).
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from maya_mcp_server import polyhaven
from maya_mcp_server.client import ensure_module_injected, exec_module_code, module_call
from maya_mcp_server.pipeline import TOOL_ANNOTATIONS
from maya_mcp_server.scene_tools import mark_dirty
from maya_mcp_server.security import (
    AuditLogger,
    InputValidationError,
    build_audit_event,
)


logger = logging.getLogger(__name__)

# Module source path for _mcp_asset
_MODULE_SOURCE = Path(__file__).parent / "asset_module.py"

# Sessions that already carry the injected module
_asset_injected: set[str] = set()

_ASSET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_ASSET_TYPE_RE = re.compile(r"^[a-z]+$")


async def _ensure_asset_injected(client: Any, session_key: str | None) -> None:
    """Inject _mcp_asset once per session (mirrors _mcp_scene/_mcp_visual).

    Delegates to client.ensure_module_injected — the channel
    contract (framed write_module vs native mkstemp fallback)
    lives there.
    """
    await ensure_module_injected(
        client,
        session_key,
        module_name="_mcp_asset",
        source_path=_MODULE_SOURCE,
        tmp_prefix="_mcp_asset_src_",
        injected_sessions=_asset_injected,
    )


def _audit_asset_download(
    audit: AuditLogger | None,
    session_key: str | None,
    descriptor: dict[str, Any],
    import_result: dict[str, Any],
    duration_ms: float,
) -> None:
    """Record the download+import halves as a supplementary event.

    URL / size / files_hash live result-side, so the generic per-call
    event cannot see them; this writes a second JSONL row through the
    shared AuditLogger (never blocks tool execution). Emitted after the
    Maya import so the row can carry the real import outcome.
    """
    if audit is None:
        return
    try:
        import_ok = isinstance(import_result, dict) and "error" not in import_result
        event = build_audit_event(
            session_id=session_key or "_default",
            tool_name="asset_import",
            params={
                "asset_id": descriptor.get("asset_id"),
                "resolution": descriptor.get("resolution"),
            },
            outcome="success" if import_ok else "error",
            duration_ms=duration_ms,
        )
        event["asset_download"] = {
            "urls": [f.get("url") for f in descriptor.get("files_hash", {}).values()],
            "size_bytes": descriptor.get("size_bytes"),
            "files_hash": descriptor.get("files_hash"),
            "source": descriptor.get("source"),
            "import_group": import_result.get("group"),
            "import_error": import_result.get("error"),
        }
        audit.record(event)
    except Exception as e:
        logger.warning("asset audit record failed: %s", e)


def register_asset_tools(mcp: Any, audit: AuditLogger | None = None) -> None:
    """Register asset tools on the FastMCP instance.

    Args:
        mcp: FastMCP server instance.
        audit: Shared AuditLogger for the supplementary download event.
    """

    @mcp.tool(annotations=TOOL_ANNOTATIONS["asset_search"])  # type: ignore[untyped-decorator]
    async def asset_search(
        query: str = "",
        asset_type: str = "models",
        limit: int = 20,
    ) -> str:
        """Search the Poly Haven CC0 asset index (host-side, no Maya needed).

        Queries https://api.polyhaven.com/assets and returns matching
        asset metadata. Read-only and idempotent - pure lookup.

        Args:
            query: Free-text search (matches id, name, categories, tags).
                Empty returns the unfiltered index page (up to limit).
            asset_type: "models" (default), "hdris", or "textures".
                Note: asset_import handles models only.
            limit: Result cap, 1-20 (default 20). total_count always
                reports the full match count.

        Returns:
            JSON {results: [{id, name, categories, tags}], total_count,
            query, asset_type}. On failure: {"error": {code, message}}
            (e.g. network_unavailable when the host is offline).
        """
        if not isinstance(asset_type, str) or not _ASSET_TYPE_RE.match(asset_type):
            raise InputValidationError(
                f"invalid asset_type {asset_type!r}: expected 'models', 'hdris', or 'textures'"
            )
        try:
            result = polyhaven.search_assets(query=query, asset_type=asset_type, limit=limit)
        except polyhaven.AssetError as e:
            return json.dumps(e.to_dict(), indent=2)
        return json.dumps(result, indent=2)

    @mcp.tool(annotations=TOOL_ANNOTATIONS["asset_import"])  # type: ignore[untyped-decorator]
    async def asset_import(
        asset_id: str,
        resolution: str = "1k",
        max_polycount: int = 100000,
        allow_high_polycount: bool = False,
        force: bool = False,
        session_key: str | None = None,
    ) -> str:
        """Import a Poly Haven CC0 model into Maya (find ids via asset_search).

        Pipeline: host downloads FBX + textures from the Poly Haven
        whitelist (https-only, size caps, per-file md5 verify) into the
        platformdirs cache -> Maya imports the local FBX (explicit
        meters unit handling) -> texture maps auto-wire by PH naming
        convention (diff/albedo -> color sRGB; rough/metal -> Raw
        single-channel; nor_gl -> bump2d normal) -> polycount gate ->
        group as GRP_asset_<id> -> bbox/metadata report.

        Idempotent by default: a second call with the same asset_id
        reports the existing group instead of duplicating (force=True
        re-imports).

        Args:
            asset_id: Poly Haven asset id (case-sensitive, e.g.
                'Camera_01').
            resolution: Texture tier '1k' (default), '2k', '4k', '8k'.
            max_polycount: Face-count guard (default 100000); imports
                over the limit are deleted and rejected.
            allow_high_polycount: Override the polycount guard (the
                override is disclosed in the audit log).
            force: Re-import even when GRP_asset_<id> already exists.
            session_key: Maya session key.

        Returns:
            JSON import report {group, nodes, meshes, polycount,
            texture_wiring, bbox, dims, license, source, warnings}.
            Domain failures return {"error": {code, message}} - e.g.
            network_unavailable, not_a_model, plugin_unavailable,
            import_failed, polycount_exceeded.
        """
        if not isinstance(asset_id, str) or not _ASSET_ID_RE.match(asset_id):
            raise InputValidationError(
                f"invalid asset_id {asset_id!r}: expected a slug like 'Camera_01'"
            )
        if resolution not in polyhaven.RESOLUTIONS:
            raise InputValidationError(
                f"invalid resolution {resolution!r}: expected one of {polyhaven.RESOLUTIONS}"
            )

        from maya_mcp_server.server import get_session_manager

        manager = get_session_manager()
        client = await manager.get_client(session_key)
        await _ensure_asset_injected(client, session_key)

        t0 = time.monotonic()
        try:
            descriptor = polyhaven.download_asset(asset_id, resolution=resolution)
        except polyhaven.AssetError as e:
            return json.dumps(e.to_dict(), indent=2)

        result = await exec_module_code(
            client,
            module_call(
                "_mcp_asset",
                "import_asset",
                descriptor,
                max_polycount=int(max_polycount),
                allow_high_polycount=bool(allow_high_polycount),
                force=bool(force),
            ),
        )
        # Real download+import wall time — duration_ms=0.0 was
        # a placeholder that lied in every supplementary audit row.
        _audit_asset_download(
            audit, session_key, descriptor, result, (time.monotonic() - t0) * 1000.0
        )
        mark_dirty(session_key)
        if isinstance(result, dict) and "error" not in result:
            result["download"] = {
                "source": descriptor.get("source"),
                "size_bytes": descriptor.get("size_bytes"),
                "cache_dir": descriptor.get("cache_dir"),
                "asset_page": descriptor.get("asset_page"),
            }
        return json.dumps(result, indent=2)
