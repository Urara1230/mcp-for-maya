"""MCP scene tools for spatial awareness.

Provides 4 high-level tools following the snapshot → inspect → measure → assert pattern.
Each tool auto-injects the _mcp_scene module into Maya on first call.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from maya_mcp_server.client import ensure_module_injected, exec_module_code, module_call
from maya_mcp_server.cos_formatter import (
    format_assert_cos,
    format_inspect_cos,
    format_measure_cos,
    format_scene_cos,
    format_zone_map_cos,
)
from maya_mcp_server.pipeline import TOOL_ANNOTATIONS
from maya_mcp_server.scene_cache import SceneCache
from maya_mcp_server.security import InputValidationError


logger = logging.getLogger(__name__)

# Module source paths for _mcp_scene. The introspection fragment
# (introspect_module.py) is concatenated into the SAME injection unit
# (D-095): one module name / trigger / failure domain, but independently
# maintained host-side source files. File boundary = injection boundary =
# failure-domain boundary.
_MODULE_SOURCE = Path(__file__).parent / "maya_scene_module.py"
_INTROSPECT_FRAGMENT = Path(__file__).parent / "introspect_module.py"

# Global cache per session (keyed by session_key)
_caches: dict[str, SceneCache] = {}

# Track which sessions have the module injected
_injected_sessions: set[str] = set()


def _get_cache(session_key: str | None) -> SceneCache:
    """Get or create cache for a session."""
    key = session_key or "_default"
    if key not in _caches:
        _caches[key] = SceneCache(ttl_seconds=5.0)
    return _caches[key]


def mark_dirty(session_key: str | None = None) -> None:
    """Mark cache as dirty after scene modification.

    Call this from session_manager after execute_code/write_module.
    """
    key = session_key or "_default"
    if key in _caches:
        _caches[key].mark_dirty()


async def _ensure_module_injected(client: Any, session_key: str | None) -> None:
    """Ensure _mcp_scene module is injected into Maya session.

    Delegates to client.ensure_module_injected (D-083); reads the module
    source from disk and writes it via write_module. Only injects once
    per session.
    """
    await ensure_module_injected(
        client,
        session_key,
        module_name="_mcp_scene",
        source_path=_MODULE_SOURCE,
        tmp_prefix="_mcp_scene_src_",
        injected_sessions=_injected_sessions,
        extra_source_paths=[_INTROSPECT_FRAGMENT],
    )


async def _execute_scene_code(
    client: Any,
    code: str,
    session_key: str | None,
    use_cache: bool = True,
    cache_key: str | None = None,
) -> Any:
    """Execute scene query code with optional caching.

    Args:
        client: MayaClient instance.
        code: Python code to execute.
        session_key: Session key for cache lookup.
        use_cache: Whether to use cache.
        cache_key: Cache key for this query.

    Returns:
        Parsed result.
    """

    async def fetch():
        return await exec_module_code(client, code)

    if use_cache and cache_key:
        return await _get_cache(session_key).get_or_fetch(cache_key, fetch)
    return await fetch()


# --- Token Budget Management ---


class TokenBudget:
    """Auto-adjust detail level based on scene size."""

    BUDGETS = {
        "small": {"max_tokens": 2000, "detail": "compact", "max_objects": 50},
        "medium": {"max_tokens": 5000, "detail": "compact", "max_objects": 200},
        "large": {"max_tokens": 10000, "detail": "summary", "max_objects": 500},
        "huge": {"max_tokens": 15000, "detail": "hierarchy_only", "max_objects": 1000},
    }

    @staticmethod
    def auto_select_level(object_count: int) -> str:
        if object_count <= 50:
            return "small"
        if object_count <= 200:
            return "medium"
        if object_count <= 500:
            return "large"
        return "huge"

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Rough token estimate: ~4 chars per token."""
        return len(text) // 4


# --- MCP Tool Registration ---


def register_scene_tools(mcp: Any) -> None:
    """Register scene tools on the FastMCP instance.

    Args:
        mcp: FastMCP server instance.
    """

    @mcp.tool(annotations=TOOL_ANNOTATIONS["scene_snapshot"])
    async def scene_snapshot(
        detail: str = "compact",
        format: str = "cos",
        session_key: str | None = None,
    ) -> str:
        """Get a complete spatial overview of the current Maya scene.

        Returns a structured description of ALL objects with positions, sizes, and types.
        Call this once at the start of each workflow to establish spatial awareness.

        Boundary: Disambiguated from scene_inspect (deep-dive inspection of
        specific objects or zones) and scene_nodes (granular DAG/DG node
        name discovery with pagination).

        Args:
            detail: Detail level - "compact" (fast),
                "standard" (+materials/mesh stats), "full" (+vertices).
            format: Output format - "cos" (Chain-of-Symbol, compact), "json" (full JSON).
            session_key: Maya session key (auto-selected if only one session).

        Returns:
            CoS-formatted scene description (default) or JSON.

        Performance: 100 objects < 50ms, 1000 objects < 200ms.

        Example:
            scene_snapshot() → SCENE[100obj, 3zones] UNIT=cm UP=y\\nENTRANCE: ...
            scene_snapshot(detail="standard", format="json") → {"objects": [...]}
        """
        from maya_mcp_server.server import get_session_manager

        manager = get_session_manager()
        client = await manager.get_client(session_key)

        # Ensure module is injected
        await _ensure_module_injected(client, session_key)

        # Get scene graph
        scene_data = await _execute_scene_code(
            client,
            module_call("_mcp_scene", "get_scene_graph", detail),
            session_key,
            use_cache=True,
            cache_key=f"scene_graph_{detail}",
        )

        # Also get zone map
        zone_data = await _execute_scene_code(
            client,
            module_call("_mcp_scene", "get_zone_map"),
            session_key,
            use_cache=True,
            cache_key="zone_map",
        )

        # Auto-adjust token budget
        obj_count = scene_data.get("stats", {}).get("total", 0)
        budget_level = TokenBudget.auto_select_level(obj_count)
        budget = TokenBudget.BUDGETS[budget_level]
        max_objects = budget["max_objects"]

        if format == "json":
            result = {
                "scene": scene_data,
                "zones": zone_data.get("zones", []),
                "budget": budget_level,
                "object_count": obj_count,
            }
            output = json.dumps(result, indent=2)
        else:
            output = format_scene_cos(
                scene_data,
                zones=zone_data.get("zones", []),
                max_objects=max_objects,
            )

        return output

    @mcp.tool(annotations=TOOL_ANNOTATIONS["scene_inspect"])
    async def scene_inspect(
        target: str,
        include_neighbors: bool = True,
        format: str = "cos",
        session_key: str | None = None,
    ) -> str:
        """Deep inspection of a specific object or zone.

        Returns detailed properties including transform, BBox, material, mesh stats,
        and optionally nearby objects with distances.

        Boundary: Disambiguated from scene_snapshot (broad whole-scene
        spatial overview) and scene_describe (API-level attribute metadata,
        types, and connection wiring).

        Args:
            target: Object name or zone name to inspect.
            include_neighbors: Whether to include nearby objects (default True).
            format: Output format - "cos" or "json".
            session_key: Maya session key.

        Returns:
            Detailed inspection in CoS or JSON format.

        Example:
            scene_inspect("wall_north") → INSPECT: wall_north[mesh]@(0,0,500) ...
            scene_inspect("entrance", include_neighbors=True) → zone details + neighbors
        """
        from maya_mcp_server.server import get_session_manager

        manager = get_session_manager()
        client = await manager.get_client(session_key)

        # Ensure module is injected
        await _ensure_module_injected(client, session_key)

        # First try as a zone name
        zone_data = await _execute_scene_code(
            client,
            module_call("_mcp_scene", "get_zone_map"),
            session_key,
            use_cache=True,
            cache_key="zone_map",
        )

        zones = zone_data.get("zones", [])
        matched_zone = None
        for zone in zones:
            if zone.get("name", "").lower() == target.lower():
                matched_zone = zone
                break

        if matched_zone:
            # Zone inspection
            if format == "json":
                return json.dumps({"zone": matched_zone}, indent=2)
            else:
                return format_zone_map_cos([matched_zone])

        # Object inspection
        code = f"""import _mcp_scene, json
result = {{}}
try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om2

    sel = om2.MSelectionList()
    sel.add(json.loads({json.dumps(json.dumps(target))}))
    dag = sel.getDagPath(0)
    fn = om2.MFnDagNode(dag)
    bbox = fn.boundingBox
    world = dag.inclusiveMatrix()

    result = {{
        "name": fn.name(),
        "type": _mcp_scene._classify_node(dag),
        "position": _mcp_scene._matrix_to_pos(world),
        "bbox": {{
            "min": _mcp_scene._vec3_to_list(bbox.min),
            "max": _mcp_scene._vec3_to_list(bbox.max),
        }},
        "size": _mcp_scene._get_bbox_size(bbox),
        "material": _mcp_scene._get_material_for_dag(dag),
        "unit": cmds.currentUnit(query=True, linear=True),
        "parent": None,
    }}

    # Parent
    if dag.length() > 1:
        parent_dag = om2.MDagPath(dag)
        parent_dag.pop()
        result["parent"] = om2.MFnDagNode(parent_dag).name()

    # Children
    children = []
    for i in range(dag.childCount()):
        child = dag.child(i)
        children.append(om2.MFnDagNode(child).name())
    result["children"] = children

    # Mesh stats
    if result["type"] == "mesh":
        dag_path = om2.MDagPath.getAPathTo(dag)
        mesh_fn = om2.MFnMesh(dag_path)
        result["vertex_count"] = mesh_fn.numVertices
        result["face_count"] = mesh_fn.numPolygons
except Exception as e:
    result = {{"error": {{"code": "inspect_failed", "message": str(e)}}}}

result
"""
        obj_data = await _execute_scene_code(client, code, session_key, use_cache=False)

        # Neighbors
        neighbors = None
        if include_neighbors and "error" not in obj_data:
            spatial_code = module_call("_mcp_scene", "get_spatial_index")
            spatial_data = await _execute_scene_code(
                client,
                spatial_code,
                session_key,
                use_cache=True,
                cache_key="spatial_index",
            )

            # Find neighbors for this object
            obj_names = spatial_data.get("objects", [])
            pairs = spatial_data.get("pairs", [])
            try:
                obj_idx = obj_names.index(target)
                neighbor_indices = set()
                for pair in pairs:
                    if pair[0] == obj_idx:
                        neighbor_indices.add((pair[1], pair[2]))
                    elif pair[1] == obj_idx:
                        neighbor_indices.add((pair[0], pair[2]))

                neighbors = []
                for idx, dist in sorted(neighbor_indices, key=lambda x: x[1]):
                    neighbors.append({"name": obj_names[idx], "distance": dist})
            except (ValueError, IndexError):
                pass

        if format == "json":
            result = {"object": obj_data, "neighbors": neighbors}
            return json.dumps(result, indent=2)
        else:
            return format_inspect_cos(obj_data, neighbors=neighbors)

    @mcp.tool(annotations=TOOL_ANNOTATIONS["scene_measure"])
    async def scene_measure(
        obj_a: str,
        obj_b: str,
        mode: str = "center",
        format: str = "cos",
        session_key: str | None = None,
    ) -> str:
        """Measure the spatial relationship between two objects.

        Call this in the COMPUTE step of the ICEV workflow, to turn a spatial
        question into a number before you execute a change: size a clearance
        before moving something, prove two objects interpenetrate before you
        separate them, or get the axis detail behind an overlap that
        scene_validate reported.

        Read-only and idempotent: it queries world-space bounding boxes and
        never modifies the scene, so no undo checkpoint is created.
        Requires an active Maya session.

        Every mode measures world-space AABBs, not the mesh surface and not
        the object origin. The modes differ in what the number means:
          center     distance between the AABB midpoints.
          surface    distance between the closest facing AABB corners — an
                     axis-aligned approximation, so it over-reports clearance
                     for rotated or concave geometry.
          clearance  SIGNED, and 0 is NOT a "merely touching" signal. Per
                     axis the gap is +separated / 0=touching / -penetrating:
                     > 0  euclidean distance across the STRICTLY SEPARATED
                          axes only. Penetrating axes contribute 0, so they
                          are invisible in this number.
                     = 0  no axis is strictly separated and at least one axis
                          is exactly touching. The other axes may be deeply
                          penetrating, so 0 does NOT mean the boxes just touch.
                     < 0  all three axes penetrate; the value is the
                          SHALLOWEST penetration depth, the minimum
                          translation needed to pull them apart.
                     bbox_overlap is true if and only if all three axes
                     strictly penetrate. A single penetrating axis already
                     makes it read false, so it can never be read as "no
                     penetration".
                     Read details.clearances_xyz for the signed per-axis truth
                     before concluding anything from distance alone.
          bbox       overlap test. On overlap, distance is 0 and overlap_dims
                     holds the per-axis penetration extents; otherwise
                     distance is euclidean and gaps_xyz holds the per-axis gaps.

        Distances are in the scene's current linear unit (cm, m or mm), not
        always centimetres.

        Boundary: Disambiguated from scene_inspect (per-object property
        deep-dive), scene_snapshot (whole-scene spatial overview) and
        scene_assert (checks the scene against expected values rather than
        measuring one pair).

        Args:
            obj_a: First object name.
            obj_b: Second object name.
            mode: What the returned number means — see the list above.
            format: Output format - "cos" or "json".
            session_key: Maya session key.

        Returns:
            Measurement result with the mode's distance, per-axis details,
            and bbox_overlap flag.

        Example:
            scene_measure("wall_north", "counter_A", mode="clearance")
            → MEASURE[clearance]: wall_north ↔ counter_A = 45cm
        """
        from maya_mcp_server.server import get_session_manager

        manager = get_session_manager()
        client = await manager.get_client(session_key)

        # Ensure module is injected
        await _ensure_module_injected(client, session_key)

        code = module_call("_mcp_scene", "measure", obj_a, obj_b, mode)
        result = await _execute_scene_code(client, code, session_key, use_cache=False)

        if format == "json":
            return json.dumps(result, indent=2)
        else:
            return format_measure_cos(result)

    @mcp.tool(annotations=TOOL_ANNOTATIONS["scene_assert"])
    async def scene_assert(
        expectations: str,
        format: str = "cos",
        session_key: str | None = None,
    ) -> str:
        """Verify scene state matches expected values.

        Use this after modifications to confirm the scene is in the desired state.
        Enforces the VERIFY step of the ICEV workflow.

        Boundary: Disambiguated from scene_validate (checks relational
        spatial constraints across zones rather than exact expected values
        for named objects).

        Args:
            expectations: JSON string defining expected state.
                Format: {"obj_name": {"position": [x,y,z], "bbox_max": [x,y,z], ...}}
                Supported properties: position, bbox_max, bbox_min, exists, material.
            format: Output format - "cos" or "json".
            session_key: Maya session key.

        Returns:
            Pass/fail result with mismatches.

        Example:
            scene_assert('{"wall_north": {"position": [0,0,500], "exists": true}}')
            → ASSERT[PASS]: 2 checks
        """
        from maya_mcp_server.server import get_session_manager

        manager = get_session_manager()
        client = await manager.get_client(session_key)

        # Ensure module is injected
        await _ensure_module_injected(client, session_key)

        try:
            json.loads(expectations)
        except json.JSONDecodeError as e:
            raise InputValidationError(f"expectations must be valid JSON: {e}") from e
        code = module_call("_mcp_scene", "assert_scene_state", expectations)
        result = await _execute_scene_code(client, code, session_key, use_cache=False)

        if format == "json":
            return json.dumps(result, indent=2)
        else:
            return format_assert_cos(result)

    # ============================================================
    # P0: Spatial Constraint Validation
    # ============================================================

    @mcp.tool(annotations=TOOL_ANNOTATIONS["scene_validate"])
    async def scene_validate(
        rules: str,
        format: str = "cos",
        session_key: str | None = None,
    ) -> str:
        """Validate scene against spatial constraints.

        Checks the scene for constraint violations like minimum clearance,
        object limits, overlaps, and height requirements. This is a read-only
        check without auto_fix mutations (it cannot modify or repair objects;
        for automated fixes, see scene_plan with auto_fix=True). Because it is
        non-destructive, no undo rollback checkpoint is created.
        Requires an active Maya session.

        Boundary: Disambiguated from scene_review (full scene quality audit
        across 11 checks) and scene_assert (verifies specific numeric
        values or object existence).

        Args:
            rules: JSON string with constraint rules. Format:
                [{"type": "min_clearance", "zone": "entrance", "value": 180},
                 {"type": "max_objects", "value": 5000},
                 {"type": "no_overlap", "objects": ["wall_a", "shelf_b"]},
                 {"type": "min_height", "objects": ["door"], "value": 250}]
                Types: min_clearance, max_objects, no_overlap, min_height, max_height
            format: Output format - "cos" or "json".
            session_key: Maya session key.

        Returns:
            Validation result with violations list.
        """
        from maya_mcp_server.server import get_session_manager

        manager = get_session_manager()
        client = await manager.get_client(session_key)
        await _ensure_module_injected(client, session_key)

        try:
            rules_obj = json.loads(rules)
        except json.JSONDecodeError as e:
            raise InputValidationError(f"rules must be valid JSON: {e}") from e
        code = module_call("_mcp_scene", "check_constraints", rules_obj)
        result = await _execute_scene_code(client, code, session_key, use_cache=False)

        if format == "json":
            return json.dumps(result, indent=2)
        else:
            passed = "PASS" if result.get("passed") else "FAIL"
            lines = [
                f"VALIDATE[{passed}]: {result.get('checked', 0)} checks, {result.get('violation_count', 0)} violations"
            ]
            for v in result.get("violations", [])[:10]:
                lines.append(f"  VIOLATION: {v.get('type')} - {v}")
            return "\n".join(lines)

    # ============================================================
    # P0: Scene Checkpoint / Rollback
    # ============================================================

    @mcp.tool(annotations=TOOL_ANNOTATIONS["scene_checkpoint"])
    async def scene_checkpoint(
        name: str | None = None,
        overwrite: bool = False,
        session_key: str | None = None,
    ) -> str:
        """Save a real scene snapshot (exportAll of in-memory state).

        Writes a self-contained .ma into a checkpoints/ directory next to
        the scene file. On untitled (never-saved) scenes an explicit name
        is required and produces an ad-hoc snapshot under the Maya
        workspace \u2014 returned with original_file_status="no_original_file"
        and scene_rebound_to=null. Same-name checkpoints are rejected
        unless overwrite=True (the old file is preserved via rename).

        Honest bounds: the snapshot contains no undo history \u2014 after
        scene_rollback, call scene_snapshot to rebuild context.
        References are flattened (self-contained, no write-back).
        Assumes one scene file per session.

        Args:
            name: Checkpoint name (^[A-Za-z0-9_-]+$). Required on untitled scenes.
            overwrite: Replace a same-name checkpoint, preserving the old file.
            session_key: Maya session key.

        Returns:
            Checkpoint info: filename, path, counts, original_file_status,
            adhoc flag.
        """
        from maya_mcp_server.server import get_session_manager

        manager = get_session_manager()
        client = await manager.get_client(session_key)
        await _ensure_module_injected(client, session_key)

        code = module_call("_mcp_scene", "save_checkpoint", name=name, overwrite=overwrite)
        result = await _execute_scene_code(client, code, session_key, use_cache=False)
        return json.dumps(result, indent=2)

    @mcp.tool(annotations=TOOL_ANNOTATIONS["scene_rollback"])
    async def scene_rollback(
        filename: str,
        discard_current_state: bool = False,
        session_key: str | None = None,
    ) -> str:
        """Open a checkpoint and rebind the scene to its original path (S2).

        Before opening, the current in-memory state is exported to an
        auto_before_rollback safety snapshot; if that fails, the rollback
        aborts \u2014 pass discard_current_state=True to escape (the result
        reports safety_snapshot="skipped_by_user"). Untitled scenes and
        ad-hoc snapshots have no original path, so the scene stays on the
        checkpoint path (S1) and scene_rebound_to is null.

        The snapshot carries no undo history \u2014 call scene_snapshot
        afterwards to rebuild context.

        Args:
            filename: Checkpoint filename from scene_checkpoint_list
                (cp_*.ma or prev_*.ma).
            discard_current_state: Escape hatch \u2014 proceed even if the
                safety snapshot fails.
            session_key: Maya session key.

        Returns:
            Structured result: success, scene_name_before/after,
            original_file_status, scene_rebound_to, safety_snapshot.
        """
        from maya_mcp_server.server import get_session_manager

        manager = get_session_manager()
        client = await manager.get_client(session_key)
        await _ensure_module_injected(client, session_key)

        code = module_call(
            "_mcp_scene",
            "rollback_to_checkpoint",
            filename,
            discard_current_state=discard_current_state,
        )
        result = await _execute_scene_code(client, code, session_key, use_cache=False)
        mark_dirty(session_key)
        return json.dumps(result, indent=2)

    @mcp.tool(annotations=TOOL_ANNOTATIONS["scene_checkpoint_list"])
    async def scene_checkpoint_list(
        session_key: str | None = None,
    ) -> str:
        """List all saved checkpoints for the current scene state.

        Saved scenes list <scene_dir>/checkpoints; untitled scenes list
        the workspace ad-hoc snapshots (adhoc flag per entry).

        Args:
            session_key: Maya session key.

        Returns:
            List of checkpoints with filenames, sizes, and timestamps.
        """
        from maya_mcp_server.server import get_session_manager

        manager = get_session_manager()
        client = await manager.get_client(session_key)
        await _ensure_module_injected(client, session_key)

        code = module_call("_mcp_scene", "list_checkpoints")
        result = await _execute_scene_code(client, code, session_key, use_cache=False)
        return json.dumps(result, indent=2)

    # ============================================================
    # P1: Camera / Shot Planning
    # ============================================================

    @mcp.tool(annotations=TOOL_ANNOTATIONS["camera_create"])
    async def camera_create(
        target: str,
        shot_type: str = "medium",
        name: str = "CAM_shot",
        azimuth: float = 30,
        elevation: float = 15,
        session_key: str | None = None,
    ) -> str:
        """Create a camera positioned for a specific shot type.

        Supports industry-standard shot types: extreme_wide, wide, medium,
        close, extreme_close, over_shoulder, bird_eye, low_angle.

        Args:
            target: Target object name to look at.
            shot_type: Shot type (default "medium").
            name: Camera name.
            azimuth: Horizontal angle in degrees (default 30).
            elevation: Vertical angle in degrees (default 15).
            session_key: Maya session key.

        Returns:
            Camera info with position, focal length, and distance.
        """
        from maya_mcp_server.server import get_session_manager

        manager = get_session_manager()
        client = await manager.get_client(session_key)
        await _ensure_module_injected(client, session_key)

        code = module_call(
            "_mcp_scene",
            "create_camera_shot",
            target,
            shot_type,
            name,
            {"azimuth": azimuth, "elevation": elevation},
        )
        result = await _execute_scene_code(client, code, session_key, use_cache=False)
        mark_dirty(session_key)
        return json.dumps(result, indent=2)

    @mcp.tool(annotations=TOOL_ANNOTATIONS["camera_orbit"])
    async def camera_orbit(
        center: str,
        radius: float = 500,
        frames: int = 120,
        name: str = "CAM_orbit",
        session_key: str | None = None,
    ) -> str:
        """Create an orbiting camera with turntable animation around a point.

        Side effects: creates a new camera and animation curves in the
        scene and marks the scene dirty. Not idempotent — each call adds
        another camera. Requires an active Maya session.

        Boundary: Disambiguated from camera_create (static shot-type
        framing; use camera_orbit only when an animated orbit is needed).

        Args:
            center: Orbit center as a JSON string "[x, y, z]".
            radius: Orbit radius in scene units.
            frames: Number of frames for one full orbit.
            name: Camera name.
            session_key: Maya session key.

        Returns:
            Camera and animation info.
        """
        from maya_mcp_server.server import get_session_manager

        manager = get_session_manager()
        client = await manager.get_client(session_key)
        await _ensure_module_injected(client, session_key)

        try:
            center_obj = json.loads(center)
        except json.JSONDecodeError as e:
            raise InputValidationError(f"center must be a JSON array [x, y, z]: {e}") from e
        code = module_call("_mcp_scene", "create_orbit_camera", center_obj, radius, frames, name)
        result = await _execute_scene_code(client, code, session_key, use_cache=False)
        mark_dirty(session_key)
        return json.dumps(result, indent=2)

    # ============================================================
    # P1: Aesthetic Analysis
    # ============================================================

    @mcp.tool(annotations=TOOL_ANNOTATIONS["scene_aesthetics"])
    async def scene_aesthetics(
        format: str = "json",
        session_key: str | None = None,
    ) -> str:
        """Professional-grade aesthetic analysis with 5 design dimensions.

        Analyzes scene aesthetics across 5 professional design dimensions:
        1. Color Theory: 60-30-10 rule, temperature balance, harmony type, saturation variety, contrast
        2. Spatial Composition: golden ratio proportions, rule-of-thirds alignment, visual weight balance
        3. Proportion & Scale: human ergonomic reference, size hierarchy (hero/secondary/tertiary)
        4. Lighting Quality: layer composition (key/fill/rim/accent), color temperature consistency
        5. Visual Flow: sight line clarity, circulation paths, visual rhythm patterns

        Returns an overall score (0-100) with grade (S/A/B/C/D/F) and improvement suggestions.

        Boundary: Disambiguated from scene_review (scene_aesthetics evaluates
        visual design theory and artistic harmony in depth, while
        scene_review is a broad multi-system scene health audit).

        Args:
            format: Output format - "json" (default) or "cos" (chain-of-symbol, token-efficient).
            session_key: Maya session key.

        Returns:
            Comprehensive aesthetic analysis report with scores per dimension.
        """
        from maya_mcp_server.server import get_session_manager

        manager = get_session_manager()
        client = await manager.get_client(session_key)
        await _ensure_module_injected(client, session_key)

        code = module_call("_mcp_scene", "analyze_aesthetics")
        result = await _execute_scene_code(client, code, session_key, use_cache=False)

        if format == "json":
            return json.dumps(result, indent=2)
        else:
            # COS format: compact token-efficient output
            overall = result.get("overall_score", 0)
            grade = result.get("grade", "?")
            dims = result.get("dimensions", {})
            suggestions = result.get("improvement_suggestions", [])

            out = [
                "AESTHETIC_SCORE[%s/100] GRADE=%s" % (overall, grade),
                "  COLOR[%s] SPATIAL[%s] SCALE[%s]"
                % (
                    dims.get("color_theory", "?"),
                    dims.get("spatial_composition", "?"),
                    dims.get("proportion_scale", "?"),
                ),
                "  LIGHT[%s] FLOW[%s]"
                % (dims.get("lighting_quality", "?"), dims.get("visual_flow", "?")),
            ]

            # Detail sub-scores
            detail = result.get("detail", {})
            ct = detail.get("color_theory", {})
            if ct.get("sub_scores"):
                ss = ct["sub_scores"]
                out.append(
                    "  COLOR_DETAIL: 60-30-10=%s temp=%s sat=%s harmony=%s contrast=%s"
                    % (
                        ss.get("60_30_10", "?"),
                        ss.get("temperature", "?"),
                        ss.get("saturation", "?"),
                        ss.get("harmony", "?"),
                        ss.get("contrast", "?"),
                    )
                )

            sc = detail.get("spatial_composition", {})
            if sc.get("sub_scores"):
                ss = sc["sub_scores"]
                out.append(
                    "  SPATIAL_DETAIL: golden=%s thirds=%s balance=%s"
                    % (
                        ss.get("golden_ratio", "?"),
                        ss.get("rule_of_thirds", "?"),
                        ss.get("visual_balance", "?"),
                    )
                )

            if suggestions:
                improve_parts = []
                for s in suggestions:
                    improve_parts.append("%s(%s,%s)" % (s["dimension"], s["score"], s["priority"]))
                out.append("  IMPROVE: " + " | ".join(improve_parts))

            return "\n".join(out)

    @mcp.tool(annotations=TOOL_ANNOTATIONS["scene_review"])
    async def scene_review(
        checks: str = "all",
        format: str = "json",
        session_key: str | None = None,
    ) -> str:
        """Comprehensive scene audit - reviews all aspects after operations.

        Runs spatial integrity, overlap detection, zone coverage, aesthetics,
        constraint validation, orphan detection, naming, componentization, conflicts, lighting quality, and scene organization. Returns a score (0-100)
        and detailed issue list.

        Boundary: Disambiguated from scene_validate (narrow rule-based
        spatial constraint checking) and scene_aesthetics (in-depth
        5-dimension aesthetic theory scoring).

        Use this after any major scene modification to verify quality.

        Args:
            checks: Comma-separated check names or "all".
                Options: spatial, overlaps, zones, aesthetics, constraints, orphans, naming, components, conflicts, lighting, organization
            format: Output format - "json" or "cos".
            session_key: Maya session key.

        Returns:
            Audit report with score, issues, and per-check details.
        """
        from maya_mcp_server.server import get_session_manager

        manager = get_session_manager()
        client = await manager.get_client(session_key)
        await _ensure_module_injected(client, session_key)

        checks_obj = None if checks == "all" else [c.strip() for c in checks.split(",")]
        code = module_call("_mcp_scene", "scene_review", checks_obj)
        result = await _execute_scene_code(client, code, session_key, use_cache=False)

        if format == "json":
            return json.dumps(result, indent=2)
        else:
            score = result.get("score", 0)
            errors = result.get("errors", 0)
            warnings = result.get("warnings", 0)
            issues = result.get("issues", [])
            lines = [f"REVIEW: score={score}/100 | errors={errors} | warnings={warnings}"]
            for issue in issues[:10]:
                sev = issue.get("severity", "?").upper()
                lines.append(f"  [{sev}] {issue.get('check')}: {issue.get('msg')}")
            return "\n".join(lines)

    @mcp.tool(annotations=TOOL_ANNOTATIONS["scene_plan"])
    async def scene_plan(
        objective: str = "",
        auto_fix: bool = False,
        format: str = "json",
        session_key: str | None = None,
    ) -> str:
        """Holistic scene planning with organization validation and layout optimization.

        Performs comprehensive scene health check and generates actionable plans:
        - Organization validation: detects orphan meshes, empty groups, default names
        - Zone analysis: coverage and spatial balance across functional zones
        - Layout suggestions: spacing, overlap, and clustering detection
        - Conflict prevention: near-miss collision prediction
        - Action plan: prioritized step-by-step execution guide

        Based on blockout-first methodology: validate organization and proportions
        before committing to detailed modeling. Supports natural language objectives.

        Boundary: Disambiguated from scene_review (scene_plan focuses on
        forward-looking layout planning and actionable auto_fix
        reorganization, whereas scene_review provides an overall
        diagnostic audit score).

        Args:
            objective: Natural language goal description.
                e.g., "set up entrance area with display window and clear circulation"
            auto_fix: If True, auto-fix safe issues (remove empty groups, reparent orphans).
            format: Output format - "json" (default) or "cos".
            session_key: Maya session key.

        Returns:
            Scene health report with organization status, zone analysis,
            layout suggestions, conflict predictions, and action plan.
        """
        from maya_mcp_server.server import get_session_manager

        manager = get_session_manager()
        client = await manager.get_client(session_key)
        await _ensure_module_injected(client, session_key)

        code = module_call(
            "_mcp_scene", "scene_plan", objective=objective or None, auto_fix=auto_fix
        )
        result = await _execute_scene_code(client, code, session_key, use_cache=False)

        if format == "json":
            return json.dumps(result, indent=2)
        else:
            # COS format
            health = result.get("overall_health", 0)
            grade = result.get("grade", "?")
            org = result.get("organization_status", {})
            zone = result.get("zone_analysis", {})
            actions = result.get("action_plan", [])

            org_stats = org.get("stats", {})
            out = [
                f"SCENE_PLAN[{health}/100] GRADE={grade}",
                f"  ORG: score={org.get('health_score', '?')} "
                f"orphans={org_stats.get('orphan_meshes', 0)} "
                f"defaults={org_stats.get('default_names', 0)}",
                f"  ZONE: coverage={zone.get('coverage', '?')} "
                f"balance={zone.get('balance_score', '?')}",
            ]

            if actions:
                out.append("  ACTIONS:")
                for a in actions[:5]:
                    pri = a.get("priority", "?").upper()
                    out.append(f"    {a['step']}. [{pri}] {a.get('description', '')}")

            return "\n".join(out)
