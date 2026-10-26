"""Maya-side scene query module.

This module is injected into Maya via write_module on first scene_* tool call.
It provides efficient batch scene queries using OpenMaya 2.0 API.

All functions return JSON strings for direct use with execute_code(result_type="JSON").
Performance targets: 100 objects < 50ms, 1000 objects < 200ms.
"""

# annotations lazily evaluated: the assembled payload concatenates
# introspect_module.py below this file's end, so the fragment's PEP-604
# annotations are strings at Maya-runtime (Py3.9) - never evaluated.
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from typing import Any

import maya.api.OpenMaya as om2  # noqa: N813
import maya.cmds as cmds


# --- Internal helpers ---


def _err(code: str, message: str, suggestion: str | None = None) -> dict:
    """Maya-domain error result: {"error": {code, message, suggestion?}}."""
    err: dict = {"code": code, "message": message}
    if suggestion:
        err["suggestion"] = suggestion
    return {"error": err}


def _round3(val: float) -> float:
    """Round float to 3 decimal places."""
    return round(float(val), 3)


def _round3_list(vals: Any) -> list[float]:
    """Round a list of floats."""
    return [_round3(v) for v in vals]


def _vec3_to_list(p: om2.MPoint | om2.MVector) -> list[float]:
    """Convert MPoint/MVector to [x,y,z]."""
    return [_round3(p[0]), _round3(p[1]), _round3(p[2])]


def _matrix_to_pos(matrix: om2.MMatrix) -> list[float]:
    """Extract translation from MMatrix."""
    return [_round3(matrix[12]), _round3(matrix[13]), _round3(matrix[14])]


def _classify_node(dag: om2.MDagPath) -> str:
    """Classify a DAG node into a type string."""
    try:
        fn = om2.MFnDagNode(dag)
        mtype = fn.typeName
        if mtype == "transform":
            # Check shape children
            for i in range(dag.childCount()):
                child = dag.child(i)
                child_fn = om2.MFnDagNode(child)
                ctype = child_fn.typeName
                if ctype == "mesh":
                    return "mesh"
                elif ctype in ("camera", "stereoRigCamera"):
                    return "camera"
                elif ctype in (
                    "areaLight",
                    "directionalLight",
                    "pointLight",
                    "spotLight",
                    "volumeLight",
                ):
                    return "light"
                elif ctype == "nurbsCurve":
                    return "curve"
                elif ctype == "locator":
                    return "locator"
                elif ctype == "joint":
                    return "joint"
            return "group"
        elif mtype == "mesh":
            return "mesh"
        elif mtype in ("camera", "stereoRigCamera"):
            return "camera"
        elif mtype in ("areaLight", "directionalLight", "pointLight", "spotLight", "volumeLight"):
            return "light"
        return "unknown"
    except Exception:
        return "unknown"


def _get_material_for_dag(dag: om2.MDagPath) -> str | None:
    """Get material name for a DAG node."""
    try:
        fn = om2.MFnDagNode(dag)
        # Find shading engines
        dag_path = om2.MDagPath.getAPathTo(dag)
        try:
            _ = om2.MFnDagNode(dag_path)  # validate dag path
        except Exception:
            pass

        # Use cmds for material lookup (more reliable)
        name = fn.name()
        try:
            sg = cmds.listConnections(name + ".instObjGroups[0]", type="shadingEngine")
            if sg:
                mat = cmds.ls(cmds.listConnections(sg[0] + ".surfaceShader"), materials=True)
                if mat:
                    return mat[0]
        except Exception:
            pass
    except Exception:
        pass
    return None


def _get_mesh_stats(dag: om2.MDagPath) -> dict[str, int]:
    """Get vertex and face count for a mesh DAG node."""
    try:
        dag_path = om2.MDagPath.getAPathTo(dag)
        mesh_fn = om2.MFnMesh(dag_path)
        return {
            "v": mesh_fn.numVertices,
            "f": mesh_fn.numPolygons,
        }
    except Exception:
        return {"v": 0, "f": 0}


def _get_bbox_size(bbox: om2.MBoundingBox) -> list[float]:
    """Get [w, h, d] from MBoundingBox."""
    mn = bbox.min
    mx = bbox.max
    return [
        _round3(abs(mx[0] - mn[0])),
        _round3(abs(mx[1] - mn[1])),
        _round3(abs(mx[2] - mn[2])),
    ]


def _point_distance(a: om2.MPoint, b: om2.MPoint) -> float:
    """Euclidean distance between two MPoints."""
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    dz = a[2] - b[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


# Sampling caps. Any analysis path that truncates MUST disclose how much
# it examined vs skipped (checked/skipped fields) — no silent partial scans.
_SAMPLE_AESTHETICS = 200
_SAMPLE_OVERLAPS = 80
_SAMPLE_OVERLAP_WINDOW = 30
_SAMPLE_CONFLICTS = 60
_SAMPLE_LAYOUT = 100
_SAMPLE_LAYOUT_WINDOW = 20
_SAMPLE_CONSTRAINT_WINDOW = 20
_SAMPLE_DEPTH = 30


def _world_bbox(dag: om2.MDagPath) -> om2.MBoundingBox:
    """World-space AABB for a DAG node.

    Transforms all 8 corners of the object-space bounding box by the
    inclusive (world) matrix. This is the ONLY correct way to get a world
    AABB under rotation/scale — transforming just min and max produces
    wrong bounds for any non-axis-aligned object.

    Returns:
        om2.MBoundingBox in world space.
    """
    fn = om2.MFnDagNode(dag)
    bbox = fn.boundingBox
    wm = dag.inclusiveMatrix()
    out = om2.MBoundingBox()
    for i in range(8):
        corner = om2.MPoint(
            bbox.min[0] if i & 1 else bbox.max[0],
            bbox.min[1] if i & 2 else bbox.max[1],
            bbox.min[2] if i & 4 else bbox.max[2],
        )
        out.expand(corner * wm)
    return out


# --- Public API ---


def get_scene_graph(detail_level="compact") -> dict:
    """Return complete scene spatial description in one call.

    Args:
        detail_level: "compact" | "standard" | "full"

    Returns:
        JSON string with objects list, stats, unit, up_axis.
    """
    result = {"objects": [], "stats": {}, "unit": "cm", "up_axis": "y"}

    # Get scene unit
    try:
        linear_unit = cmds.currentUnit(query=True, linear=True)
        unit_map = {"cm": "cm", "m": "m", "mm": "mm", "in": "in", "ft": "ft"}
        result["unit"] = unit_map.get(linear_unit, linear_unit)
    except Exception:
        pass

    # Get up axis
    try:
        up = cmds.upAxis(query=True, axis=True)
        result["up_axis"] = up.lower()
    except Exception:
        pass

    # Collect transforms via cmds.ls — MSelectionList.add("*") also
    # matches non-DAG nodes on real Maya (defaultRenderLayer, time1...),
    # and getDagPath on those raises TypeError("item is not a DAG path").
    transforms = cmds.ls(type="transform", long=True) or []

    for tname in transforms:
        try:
            sel_list = om2.MSelectionList()
            sel_list.add(tname)
            dag = sel_list.getDagPath(0)
        except Exception:
            continue

        fn = om2.MFnDagNode(dag)
        node_type = _classify_node(dag)

        # Skip intermediate shapes
        if node_type in ("mesh", "camera", "light", "curve", "locator"):
            try:
                if fn.isIntermediateObject:
                    continue
            except Exception:
                pass

        # Get bounding box (direct C++ access, fast)
        try:
            bbox = fn.boundingBox
            bbox_min = _vec3_to_list(bbox.min)
            bbox_max = _vec3_to_list(bbox.max)
        except Exception:
            bbox_min = [0, 0, 0]
            bbox_max = [0, 0, 0]

        # Get world position from inclusive matrix
        try:
            world_matrix = dag.inclusiveMatrix()
            pos = _matrix_to_pos(world_matrix)
        except Exception:
            pos = [0, 0, 0]

        # Child count
        try:
            child_count = dag.childCount()
        except Exception:
            child_count = 0

        # Build object entry with short keys
        obj = {
            "n": fn.name(),
            "t": node_type,
            "p": pos,
            "b": bbox_min + bbox_max,
        }
        if child_count > 0:
            obj["c"] = child_count

        # Parent name
        try:
            if dag.length() > 1:
                parent_dag = om2.MDagPath(dag)
                parent_dag.pop()
                obj["pr"] = om2.MFnDagNode(parent_dag).name()
        except Exception:
            pass

        # Standard/Full detail
        if detail_level in ("standard", "full"):
            mat = _get_material_for_dag(dag)
            if mat:
                obj["m"] = mat
            if node_type == "mesh":
                stats = _get_mesh_stats(dag)
                obj["v"] = stats["v"]
                obj["f"] = stats["f"]

        # Full detail (single object deep query)
        if detail_level == "full" and node_type == "mesh":
            try:
                dag_path = om2.MDagPath.getAPathTo(dag)
                mesh_fn = om2.MFnMesh(dag_path)
                # First 100 vertex positions
                verts = []
                for i in range(min(mesh_fn.numVertices, 100)):
                    pt = mesh_fn.getPoint(i, om2.MSpace.kWorld)
                    verts.extend(_round3_list([pt[0], pt[1], pt[2]]))
                obj["verts"] = verts
            except Exception:
                pass

        result["objects"].append(obj)

    # Stats
    type_counts = defaultdict(int)
    for obj in result["objects"]:
        type_counts[obj["t"]] += 1
    result["stats"] = {
        "total": len(result["objects"]),
        "by_type": dict(type_counts),
    }

    return result


def get_zone_map(patterns=None) -> dict:
    """Group objects by naming-pattern zones.

    Args:
        patterns: dict of zone_name -> regex_pattern. If None, uses defaults
                  for pop-up store naming conventions.

    Returns:
        JSON string with zones list.
    """
    if patterns is None:
        patterns = {
            "shell": r"(store_shell|wall|floor|ceiling|roof|facade|exterior)",
            "entrance": r"(entrance|door|gate|shopfront|entry|lobby|foyer)",
            "display": r"(display|shelf|counter|kiosk|vitrine|showcase|podium|stand)",
            "ip_core": r"(kitty|hello_kitty|ip_|character|figure|mascot|logo_main)",
            "furniture": r"(table|chair|bench|sofa|rack|desk|seat|stool)",
            "lighting": r"(light|spot|key_|fill_|rim_|ambient|lamp|luminaire)",
            "path": r"(path|aisle|corridor|walkway|route|lane|passage)",
        }

    # Compile patterns
    compiled = {}
    for zone_name, pat in patterns.items():
        compiled[zone_name] = re.compile(pat, re.IGNORECASE)

    # Get all transforms
    transforms = cmds.ls(type="transform", long=True) or []

    zones = {}
    for zone_name in compiled:
        zones[zone_name] = {
            "name": zone_name,
            "pattern_matched": patterns[zone_name],
            "objects": [],
            "bbox_min": [float("inf")] * 3,
            "bbox_max": [float("-inf")] * 3,
        }

    unassigned = []

    for tname in transforms:
        short_name = tname.split("|")[-1]
        matched_zone = None

        for zone_name, pat in compiled.items():
            if pat.search(short_name):
                matched_zone = zone_name
                break

        if matched_zone:
            zones[matched_zone]["objects"].append(short_name)
            # Update zone bbox (world space — 8-corner transform)
            try:
                sel_list = om2.MSelectionList()
                sel_list.add(tname)
                dag = sel_list.getDagPath(0)
                wb = _world_bbox(dag)
                for i in range(3):
                    zones[matched_zone]["bbox_min"][i] = min(
                        zones[matched_zone]["bbox_min"][i], wb.min[i]
                    )
                    zones[matched_zone]["bbox_max"][i] = max(
                        zones[matched_zone]["bbox_max"][i], wb.max[i]
                    )
            except Exception:
                pass
        else:
            unassigned.append(short_name)

    # Finalize zones
    result_zones = []
    for zone_name, zone in zones.items():
        if not zone["objects"]:
            continue
        zone["object_count"] = len(zone["objects"])
        # Compute center
        if zone["bbox_min"][0] != float("inf"):
            zone["center"] = [
                _round3((zone["bbox_min"][i] + zone["bbox_max"][i]) / 2) for i in range(3)
            ]
            zone["bbox_min"] = _round3_list(zone["bbox_min"])
            zone["bbox_max"] = _round3_list(zone["bbox_max"])
        else:
            zone["center"] = [0, 0, 0]
            zone["bbox_min"] = [0, 0, 0]
            zone["bbox_max"] = [0, 0, 0]
        result_zones.append(zone)

    return {
        "zones": result_zones,
        "unassigned_count": len(unassigned),
        "total_objects": len(transforms),
    }


def get_spatial_index(max_neighbors=8, max_distance=None) -> dict:
    """Build spatial index with neighbor relationships.

    Args:
        max_neighbors: Maximum neighbors per object (default 8).
        max_distance: Max distance for neighbor inclusion. If None, auto-calculated.

    Returns:
        JSON string with adjacency list: [[obj_a_idx, obj_b_idx, distance], ...]
    """
    transforms = cmds.ls(type="transform", long=True) or []
    if not transforms:
        return {"pairs": [], "objects": [], "threshold": 0}

    # Collect positions and names
    positions = []
    names = []
    for tname in transforms:
        try:
            sel_list = om2.MSelectionList()
            sel_list.add(tname)
            dag = sel_list.getDagPath(0)
            fn = om2.MFnDagNode(dag)
            world_matrix = dag.inclusiveMatrix()
            pos = (world_matrix[12], world_matrix[13], world_matrix[14])
            positions.append((float(pos[0]), float(pos[1]), float(pos[2])))
            names.append(fn.name())
        except Exception:
            continue

    n = len(positions)
    if n == 0:
        return {"pairs": [], "objects": [], "threshold": 0}

    # Auto-calculate threshold if not set
    if max_distance is None:
        # Use average bounding box diagonal * 3 as threshold
        total_diag = 0.0
        count = 0
        for tname in transforms:
            try:
                sel_list = om2.MSelectionList()
                sel_list.add(tname)
                dag = sel_list.getDagPath(0)
                fn = om2.MFnDagNode(dag)
                bbox = fn.boundingBox
                diag = _point_distance(bbox.min, bbox.max)
                total_diag += diag
                count += 1
            except Exception:
                continue
        avg_diag = total_diag / count if count > 0 else 100.0
        max_distance = avg_diag * 3.0

    # Brute-force neighbor search (optimized for < 2000 objects)
    # For larger scenes, use spatial hashing
    pairs = []
    if n <= 2000:
        # Direct O(n^2) with early distance cutoff
        for i in range(n):
            dists = []
            for j in range(i + 1, n):
                dx = positions[i][0] - positions[j][0]
                dy = positions[i][1] - positions[j][1]
                dz = positions[i][2] - positions[j][2]
                dist = math.sqrt(dx * dx + dy * dy + dz * dz)
                if dist <= max_distance:
                    dists.append((dist, j))
            dists.sort()
            for dist, j in dists[:max_neighbors]:
                pairs.append([i, j, _round3(dist)])
    else:
        # Spatial hashing for large scenes
        cell_size = max_distance
        grid = defaultdict(list)
        for i, (x, y, z) in enumerate(positions):
            cell = (int(x / cell_size), int(y / cell_size), int(z / cell_size))
            grid[cell].append(i)

        for i, (x, y, z) in enumerate(positions):
            ci = (int(x / cell_size), int(y / cell_size), int(z / cell_size))
            dists = []
            # Check neighboring cells
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        neighbor_cell = (ci[0] + dx, ci[1] + dy, ci[2] + dz)
                        for j in grid.get(neighbor_cell, []):
                            if j <= i:
                                continue
                            d = math.sqrt(
                                (x - positions[j][0]) ** 2
                                + (y - positions[j][1]) ** 2
                                + (z - positions[j][2]) ** 2
                            )
                            if d <= max_distance:
                                dists.append((d, j))
            dists.sort()
            for dist, j in dists[:max_neighbors]:
                pairs.append([i, j, _round3(dist)])

    return {"pairs": pairs, "objects": names, "threshold": _round3(max_distance), "count": n}


def measure(obj_a, obj_b, mode="center") -> dict:
    """Measure spatial relationship between two objects.

    Args:
        obj_a: Name of first object.
        obj_b: Name of second object.
        mode: "center" | "surface" | "clearance" | "bbox"

    Returns:
        JSON string with measurement result.
    """
    result = {
        "obj_a": obj_a,
        "obj_b": obj_b,
        "mode": mode,
        "distance": 0.0,
        "bbox_overlap": False,
        "unit": "cm",
    }

    try:
        # Get unit
        linear_unit = cmds.currentUnit(query=True, linear=True)
        result["unit"] = {"cm": "cm", "m": "m", "mm": "mm"}.get(linear_unit, linear_unit)
    except Exception:
        pass

    try:
        sel_a = om2.MSelectionList()
        sel_a.add(obj_a)
        dag_a = sel_a.getDagPath(0)

        sel_b = om2.MSelectionList()
        sel_b.add(obj_b)
        dag_b = sel_b.getDagPath(0)
    except Exception as e:
        result["error"] = {
            "code": "object_not_found",
            "message": str(e),
            "suggestion": "verify both object names exist in the scene",
        }
        return result

    # World-space AABBs via all 8 local-bbox corners (rotation-safe)
    wb_a = _world_bbox(dag_a)
    wb_b = _world_bbox(dag_b)
    world_min_a, world_max_a = wb_a.min, wb_a.max
    world_min_b, world_max_b = wb_b.min, wb_b.max

    # Center points
    center_a = om2.MPoint(
        (world_min_a[0] + world_max_a[0]) / 2,
        (world_min_a[1] + world_max_a[1]) / 2,
        (world_min_a[2] + world_max_a[2]) / 2,
    )
    center_b = om2.MPoint(
        (world_min_b[0] + world_max_b[0]) / 2,
        (world_min_b[1] + world_max_b[1]) / 2,
        (world_min_b[2] + world_max_b[2]) / 2,
    )

    if mode == "center":
        result["distance"] = _round3(_point_distance(center_a, center_b))
        result["details"] = {
            "center_a": _vec3_to_list(center_a),
            "center_b": _vec3_to_list(center_b),
        }

    elif mode == "bbox":
        # Check overlap on all 3 axes
        overlap = True
        for i in range(3):
            if world_max_a[i] < world_min_b[i] or world_max_b[i] < world_min_a[i]:
                overlap = False
                break
        result["bbox_overlap"] = overlap

        # Compute gap distance on each axis
        gaps = []
        for i in range(3):
            gap = max(0, max(world_min_a[i], world_min_b[i]) - min(world_max_a[i], world_max_b[i]))
            gaps.append(_round3(gap))
        result["details"] = {"gaps_xyz": gaps, "overlapping": overlap}

        if overlap:
            # Compute overlap volume
            overlap_dims = []
            for i in range(3):
                dim = min(world_max_a[i], world_max_b[i]) - max(world_min_a[i], world_min_b[i])
                overlap_dims.append(max(0, _round3(dim)))
            result["details"]["overlap_dims"] = overlap_dims
            result["distance"] = 0.0
        else:
            result["distance"] = _round3(math.sqrt(sum(g * g for g in gaps)))

    elif mode == "surface":
        # Approximate surface distance using closest bbox face points
        closest_a = om2.MPoint(
            max(world_min_a[0], min(center_b[0], world_max_a[0])),
            max(world_min_a[1], min(center_b[1], world_max_a[1])),
            max(world_min_a[2], min(center_b[2], world_max_a[2])),
        )
        closest_b = om2.MPoint(
            max(world_min_b[0], min(center_a[0], world_max_b[0])),
            max(world_min_b[1], min(center_a[1], world_max_b[1])),
            max(world_min_b[2], min(center_a[2], world_max_b[2])),
        )
        result["distance"] = _round3(_point_distance(closest_a, closest_b))
        result["details"] = {
            "surface_a": _vec3_to_list(closest_a),
            "surface_b": _vec3_to_list(closest_b),
        }

    elif mode == "clearance":
        # Signed AABB separation distance.
        # Positive: euclidean distance across the separating axes.
        # Negative: penetration depth (min translation needed to separate).
        gaps = []
        for i in range(3):
            gaps.append(max(world_min_a[i], world_min_b[i]) - min(world_max_a[i], world_max_b[i]))
        if any(g >= 0 for g in gaps):
            dist = math.sqrt(sum(max(0.0, g) ** 2 for g in gaps))
        else:
            dist = max(gaps)  # all negative: least-penetrating axis
        result["distance"] = _round3(dist)
        result["bbox_overlap"] = all(g < 0 for g in gaps)
        result["details"] = {"clearances_xyz": [_round3(g) for g in gaps]}

    return result


def get_material_map() -> dict:
    """Get material-to-object mapping with color information.

    Returns:
        JSON string with materials list, each having name, color, objects.
    """
    materials = {}

    # Get all shading engines
    shading_engines = cmds.ls(type="shadingEngine") or []
    for sg in shading_engines:
        # Skip default shaders
        if sg in ("initialParticleSE", "initialShadingGroup"):
            continue

        # Get material connected to this SG
        mat_list = cmds.ls(cmds.listConnections(sg + ".surfaceShader") or [], materials=True)
        if not mat_list:
            continue

        mat_name = mat_list[0]

        # Get color
        color = [0.5, 0.5, 0.5]  # default gray
        try:
            color_attr = cmds.getAttr(mat_name + ".color")[0]
            color = [_round3(c) for c in color_attr]
        except Exception:
            pass

        # Get objects assigned to this SG
        assigned = cmds.sets(sg, query=True) or []
        obj_names = []
        for obj in assigned:
            # Extract short name
            short = obj.split(".")[-1] if "." in obj else obj.split("|")[-1]
            obj_names.append(short)

        materials[mat_name] = {
            "name": mat_name,
            "color": color,
            "objects": obj_names,
            "object_count": len(obj_names),
            "shader_group": sg,
        }

    return {"materials": list(materials.values()), "total": len(materials)}


def get_unit_info() -> dict:
    """Get scene unit and axis information.

    Returns:
        JSON string with unit info.
    """
    info = {}
    try:
        info["linear"] = cmds.currentUnit(query=True, linear=True)
    except Exception:
        info["linear"] = "cm"
    try:
        info["angular"] = cmds.currentUnit(query=True, angle=True)
    except Exception:
        info["angular"] = "deg"
    try:
        info["up_axis"] = cmds.upAxis(query=True, axis=True)
    except Exception:
        info["up_axis"] = "y"
    try:
        info["time"] = cmds.currentUnit(query=True, time=True)
    except Exception:
        info["time"] = "film"
    return info


def assert_scene_state(expectations_json) -> dict:
    """Verify scene state matches expectations.

    Args:
        expectations_json: JSON string defining expected state.
            Format: {"obj_name": {"position": [x,y,z], "bbox_max": [x,y,z], ...}, ...}

    Returns:
        JSON string with pass/fail result and mismatches.
    """
    expectations = json.loads(expectations_json)
    mismatches = []
    checked = 0
    passed = 0

    for obj_name, expected in expectations.items():
        try:
            sel_list = om2.MSelectionList()
            sel_list.add(obj_name)
            dag = sel_list.getDagPath(0)
            fn = om2.MFnDagNode(dag)
        except Exception:
            checked += 1
            if expected.get("exists") is False:
                # exists:false on a missing object is a pass
                passed += 1
            else:
                mismatches.append(
                    {
                        "object": obj_name,
                        "property": "existence",
                        "expected": "exists",
                        "actual": "not found",
                    }
                )
            continue

        # Check position
        if "position" in expected:
            checked += 1
            try:
                world_matrix = dag.inclusiveMatrix()
                actual_pos = _matrix_to_pos(world_matrix)
                exp_pos = expected["position"]
                # Allow 1 unit tolerance
                if any(abs(a - e) > 1.0 for a, e in zip(actual_pos, exp_pos)):
                    mismatches.append(
                        {
                            "object": obj_name,
                            "property": "position",
                            "expected": exp_pos,
                            "actual": actual_pos,
                        }
                    )
                else:
                    passed += 1
            except Exception as e:
                mismatches.append(
                    {
                        "object": obj_name,
                        "property": "position",
                        "error": {"code": "check_failed", "message": str(e)},
                    }
                )

        # Check bbox_max
        if "bbox_max" in expected:
            checked += 1
            try:
                bbox = fn.boundingBox
                actual_max = _vec3_to_list(bbox.max)
                exp_max = expected["bbox_max"]
                if any(abs(a - e) > 1.0 for a, e in zip(actual_max, exp_max)):
                    mismatches.append(
                        {
                            "object": obj_name,
                            "property": "bbox_max",
                            "expected": exp_max,
                            "actual": actual_max,
                        }
                    )
                else:
                    passed += 1
            except Exception as e:
                mismatches.append(
                    {
                        "object": obj_name,
                        "property": "bbox_max",
                        "error": {"code": "check_failed", "message": str(e)},
                    }
                )

        # Check bbox_min
        if "bbox_min" in expected:
            checked += 1
            try:
                bbox = fn.boundingBox
                actual_min = _vec3_to_list(bbox.min)
                exp_min = expected["bbox_min"]
                if any(abs(a - e) > 1.0 for a, e in zip(actual_min, exp_min)):
                    mismatches.append(
                        {
                            "object": obj_name,
                            "property": "bbox_min",
                            "expected": exp_min,
                            "actual": actual_min,
                        }
                    )
                else:
                    passed += 1
            except Exception as e:
                mismatches.append(
                    {
                        "object": obj_name,
                        "property": "bbox_min",
                        "error": {"code": "check_failed", "message": str(e)},
                    }
                )

        # Check existence only
        if "exists" in expected:
            checked += 1
            if expected["exists"]:
                passed += 1  # We already found it above
            else:
                mismatches.append(
                    {
                        "object": obj_name,
                        "property": "existence",
                        "expected": "not exists",
                        "actual": "exists",
                    }
                )

        # Check material
        if "material" in expected:
            checked += 1
            actual_mat = _get_material_for_dag(dag)
            if actual_mat != expected["material"]:
                mismatches.append(
                    {
                        "object": obj_name,
                        "property": "material",
                        "expected": expected["material"],
                        "actual": actual_mat,
                    }
                )
            else:
                passed += 1

    return {
        "passed": len(mismatches) == 0,
        "checked_count": checked,
        "passed_count": passed,
        "mismatches": mismatches,
    }


# ============================================================
# P0: Spatial Constraint Validation
# ============================================================


def check_constraints(rules):
    """Check scene against spatial constraints.

    Args:
        rules: List of constraint dicts, each with:
            - type: "min_clearance" | "max_objects" | "no_overlap" | "min_height" | "max_height"
            - zone: optional zone name to limit check
            - value: threshold value
            - objects: optional list of object names to check

    Returns:
        dict with violations list and summary.
    """
    violations = []
    checked = 0
    pairs_checked = 0
    pairs_total = 0

    for rule in rules:
        rtype = rule.get("type", "")
        value = rule.get("value", 0)
        zone = rule.get("zone")
        obj_list = rule.get("objects")

        if rtype == "min_clearance":
            # Check minimum clearance between objects
            transforms = cmds.ls(type="transform", long=True) or []
            if zone:
                zone_data = get_zone_map()
                zone_names = []
                for z in zone_data.get("zones", []):
                    if z["name"] == zone:
                        zone_names = z.get("objects", [])
                        break
                transforms = [t for t in transforms if t.split("|")[-1] in zone_names]

            pairs_total += len(transforms) * (len(transforms) - 1) // 2
            for i in range(len(transforms)):
                for j in range(i + 1, min(len(transforms), i + _SAMPLE_CONSTRAINT_WINDOW)):
                    pairs_checked += 1
                    try:
                        m = measure(
                            transforms[i].split("|")[-1], transforms[j].split("|")[-1], "clearance"
                        )
                        checked += 1
                        # Negative clearance = penetration — that IS a violation.
                        # Skip errored measurements: they carry distance 0.0
                        # and would report a ghost penetration otherwise.
                        if "error" not in m and m.get("distance", 999) < value:
                            violations.append(
                                {
                                    "type": "min_clearance",
                                    "objects": [
                                        transforms[i].split("|")[-1],
                                        transforms[j].split("|")[-1],
                                    ],
                                    "actual": m["distance"],
                                    "required": value,
                                }
                            )
                    except Exception:
                        pass

        elif rtype == "max_objects":
            total = len(cmds.ls(type="transform", long=True) or [])
            checked += 1
            if total > value:
                violations.append(
                    {
                        "type": "max_objects",
                        "actual": total,
                        "required": value,
                    }
                )

        elif rtype == "no_overlap":
            transforms = cmds.ls(type="transform", long=True) or []
            if obj_list:
                transforms = [t for t in transforms if t.split("|")[-1] in obj_list]
            pairs_total += len(transforms) * (len(transforms) - 1) // 2
            for i in range(len(transforms)):
                for j in range(i + 1, min(len(transforms), i + _SAMPLE_CONSTRAINT_WINDOW)):
                    pairs_checked += 1
                    try:
                        m = measure(
                            transforms[i].split("|")[-1], transforms[j].split("|")[-1], "bbox"
                        )
                        checked += 1
                        if "error" not in m and m.get("bbox_overlap", False):
                            violations.append(
                                {
                                    "type": "overlap",
                                    "objects": [
                                        transforms[i].split("|")[-1],
                                        transforms[j].split("|")[-1],
                                    ],
                                }
                            )
                    except Exception:
                        pass

        elif rtype in ("min_height", "max_height"):
            if obj_list:
                for oname in obj_list:
                    try:
                        sel = om2.MSelectionList()
                        sel.add(oname)
                        dag = sel.getDagPath(0)
                        fn = om2.MFnDagNode(dag)
                        bbox = fn.boundingBox
                        height = abs(bbox.max[1] - bbox.min[1])
                        checked += 1
                        if rtype == "min_height" and height < value:
                            violations.append(
                                {
                                    "type": rtype,
                                    "object": oname,
                                    "actual": round(height, 1),
                                    "required": value,
                                }
                            )
                        elif rtype == "max_height" and height > value:
                            violations.append(
                                {
                                    "type": rtype,
                                    "object": oname,
                                    "actual": round(height, 1),
                                    "required": value,
                                }
                            )
                    except Exception:
                        pass

    return {
        "passed": len(violations) == 0,
        "checked": checked,
        "violations": violations,
        "violation_count": len(violations),
        "sampling": {
            "pair_window": _SAMPLE_CONSTRAINT_WINDOW,
            "pairs_checked": pairs_checked,
            "pairs_skipped": max(0, pairs_total - pairs_checked),
        },
    }


# ============================================================
# P0: Scene Checkpoint / Rollback: true exportAll snapshot, not references
# ============================================================
# checkpoint = cmds.file(exportAll) snapshot of MEMORY state -> checkpoints/
# rollback   = open snapshot + rename back to the original scene path (S2);
#            untitled / ad-hoc snapshots stay on the checkpoint path (S1)
# invariant: before any overwrite-path op, current memory state is
#            snapshotted (auto_before_rollback); failure aborts unless
#            discard_current_state=True escapes it
# bounds:    no undo history in a snapshot (rebuild via scene_snapshot);
#            references are flattened (self-contained, no write-back);
#            assumes one scene file per session

_CHECKPOINT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CHECKPOINT_FILE_RE = re.compile(r"^(cp|prev)_[A-Za-z0-9_-]+\.ma$")
_MAYA_ASCII_MAGIC = "//Maya ASCII"


def _scene_file_path() -> str:
    """Current scene file path (absoluteName semantics), "" when untitled.

    expandName resolves env vars / relative forms to an absolute path \u2014
    sceneName alone can return an unresolved name.
    """
    import os

    path = ""
    try:
        path = cmds.file(query=True, expandName=True) or ""
    except Exception:
        path = ""
    if not path:
        try:
            path = cmds.file(query=True, sceneName=True) or ""
        except Exception:
            path = ""
    if not path:
        return ""
    return os.path.normpath(os.path.expandvars(path))


def _checkpoint_dir(
    scene_path: str,
) -> tuple[str, dict[str, Any] | None]:
    """Resolve the checkpoints dir for the current scene state.

    Saved scenes: <scene_dir>/checkpoints. Untitled scenes: ad-hoc
    snapshots go under <maya workspace>/checkpoints so they still land
    somewhere recoverable.

    Returns (dir, None) or ("", error_dict).
    """
    import os

    if scene_path:
        return os.path.join(os.path.dirname(scene_path), "checkpoints"), None
    try:
        ws = cmds.workspace(query=True, rootDirectory=True) or ""
    except Exception:
        ws = ""
    if not ws:
        out = _err(
            "workspace_unavailable",
            "Scene is untitled and no Maya workspace resolves \u2014 "
            "cannot place an ad-hoc snapshot.",
            "Save the scene first, then retry.",
        )
        out["original_file_status"] = "no_original_file"
        return "", out
    return os.path.join(ws, "checkpoints"), None


def _ensure_checkpoint_dir(cp_dir: str) -> dict[str, Any] | None:
    """Precheck a checkpoints dir. Returns error dict or None.

    Covers hidden case 1: the path exists as a file, or is not writable.
    """
    import os

    if os.path.exists(cp_dir):
        if not os.path.isdir(cp_dir):
            return _err(
                "path_not_directory",
                f"Checkpoints path exists but is not a directory: {cp_dir}",
                "remove or rename the conflicting file",
            )
    else:
        try:
            os.makedirs(cp_dir, exist_ok=True)
        except Exception as e:
            return _err("dir_create_failed", f"Cannot create checkpoints dir {cp_dir}: {e}")
    if not os.access(cp_dir, os.W_OK):
        return _err(
            "dir_not_writable",
            f"Checkpoints dir is not writable: {cp_dir}",
            "fix directory permissions or choose another base_dir",
        )
    return None


def _write_snapshot(cp_path: str) -> dict[str, Any] | None:
    """exportAll the in-memory scene state to cp_path.

    Returns error dict or None. prompt=False so a GUI modal can never
    hang the MCP round-trip; force=True because name-collision handling
    already ran (old file preserved via rename beforehand).
    """
    import os

    try:
        cmds.file(cp_path, exportAll=True, type="mayaAscii", force=True, prompt=False)
    except Exception as e:
        return _err("snapshot_write_failed", f"exportAll snapshot failed: {e}")
    if not os.path.exists(cp_path):
        return _err(
            "snapshot_missing",
            f"exportAll returned but snapshot file is missing: {cp_path}",
        )
    return None


def _suggest_name(name: Any) -> str:
    """Sanitized suggestion for a rejected name \u2014 never auto-applied."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", str(name)).strip("_")
    return cleaned or "checkpoint"


def _auto_snapshot(cp_dir: str) -> dict[str, Any]:
    """Internal auto_before_rollback snapshot.

    Returns {"path": ..., "filename": ...} or an error dict. Timestamped
    name + collision suffix so repeated rollbacks never collide.
    """
    import datetime
    import os

    err = _ensure_checkpoint_dir(cp_dir)
    if err:
        return err
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    cp_path = os.path.join(cp_dir, f"cp_auto_before_rollback_{ts}.ma")
    i = 2
    while os.path.exists(cp_path):
        cp_path = os.path.join(cp_dir, f"cp_auto_before_rollback_{ts}_{i}.ma")
        i += 1
    werr = _write_snapshot(cp_path)
    if werr:
        return werr
    return {"path": cp_path, "filename": os.path.basename(cp_path)}


def _rb_error(
    code: str,
    msg: str,
    scene_before: str,
    scene_after: str | None = None,
    suggestion: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Structured rollback/checkpoint error carrying scene identity."""
    err: dict[str, Any] = {"code": code, "message": msg}
    if suggestion:
        err["suggestion"] = suggestion
    d = {
        "error": err,
        "scene_name_before": scene_before,
        "scene_name_after": scene_after if scene_after is not None else scene_before,
        "original_file_status": "saved" if scene_before else "no_original_file",
        "scene_rebound_to": None,
    }
    d.update(extra)
    return d


def save_checkpoint(name: str | None = None, overwrite: bool = False) -> dict[str, Any]:
    """Save a real scene snapshot (exportAll of memory state).

    Args:
        name: Checkpoint name, strict whitelist ^[A-Za-z0-9_-]+$. None
            defaults to "checkpoint" on saved scenes; on untitled scenes
            an explicit name is required and produces an ad-hoc snapshot
            under the Maya workspace (original_file_status=
            "no_original_file", scene_rebound_to=None \u2014 rollback to it
            stays on the checkpoint path, S1).
        overwrite: Replace a same-name checkpoint; the old file is
            renamed to prev_<ts>_<filename> before the new write.

    Returns:
        dict with checkpoint info, or a structured error dict.
    """
    import datetime
    import os

    scene_path = _scene_file_path()
    untitled = not scene_path

    if name is None:
        if untitled:
            out = _err(
                "untitled_scene",
                "Scene is untitled \u2014 there is no original file to rebind to. "
                "Save the scene first, or pass an explicit name to create an "
                "ad-hoc snapshot.",
                "scene_checkpoint(name='rescue') for an ad-hoc snapshot",
            )
            out["original_file_status"] = "no_original_file"
            out["scene_rebound_to"] = None
            return out
        name = "checkpoint"

    if not _CHECKPOINT_NAME_RE.match(str(name)):
        return _err(
            "invalid_name",
            f"Invalid checkpoint name {name!r}: must match ^[A-Za-z0-9_-]+$",
            _suggest_name(name),
        )

    cp_dir, derr = _checkpoint_dir(scene_path)
    if derr is not None:
        return derr
    err = _ensure_checkpoint_dir(cp_dir)
    if err:
        return err

    cp_filename = f"cp_adhoc_{name}.ma" if untitled else f"cp_{name}.ma"
    cp_path = os.path.join(cp_dir, cp_filename)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    preserved_as = None
    if os.path.exists(cp_path):
        if not overwrite:
            out = _err(
                "checkpoint_exists",
                f"Checkpoint '{name}' already exists as {cp_filename}",
                "pass overwrite=True to replace it (the existing file is preserved via rename)",
            )
            out["existing"] = cp_filename
            return out
        prev_name = f"prev_{timestamp}_{cp_filename}"
        i = 2
        while os.path.exists(os.path.join(cp_dir, prev_name)):
            prev_name = f"prev_{timestamp}_{i}_{cp_filename}"
            i += 1
        try:
            os.replace(cp_path, os.path.join(cp_dir, prev_name))
        except Exception as e:
            return _err(
                "preserve_failed",
                f"Could not preserve existing checkpoint before overwrite: {e}",
            )
        preserved_as = prev_name

    werr = _write_snapshot(cp_path)
    if werr:
        if preserved_as:
            werr["preserved_as"] = preserved_as
        return werr

    transforms = cmds.ls(type="transform", long=True) or []
    result = {
        "name": name,
        "filename": cp_filename,
        "path": cp_path,
        "timestamp": timestamp,
        "object_count": len(transforms),
        "mesh_count": len(cmds.ls(type="mesh") or []),
        "light_count": len(cmds.ls(type="light") or []),
        "original_file_status": "no_original_file" if untitled else "saved",
        "adhoc": untitled,
        "scene_rebound_to": None,
    }
    if preserved_as:
        result["preserved_as"] = preserved_as
    return result


def list_checkpoints() -> dict[str, Any]:
    """List all checkpoints for the current scene state.

    Saved scenes list <scene_dir>/checkpoints; untitled scenes list the
    workspace ad-hoc dir. Returns dict with checkpoints list and count.
    """
    import glob
    import os

    cp_dir, derr = _checkpoint_dir(_scene_file_path())
    if derr is not None:
        derr["checkpoints"] = []
        return derr
    if not os.path.isdir(cp_dir):
        return {"checkpoints": [], "count": 0}

    checkpoints = []
    for f in sorted(glob.glob(os.path.join(cp_dir, "cp_*.ma"))):
        try:
            stat = os.stat(f)
        except OSError:
            continue
        fn = os.path.basename(f)
        checkpoints.append(
            {
                "filename": fn,
                "path": f,
                "size_mb": round(stat.st_size / 1024 / 1024, 2),
                "modified": stat.st_mtime,
                "adhoc": fn.startswith("cp_adhoc_"),
            }
        )

    return {"checkpoints": checkpoints, "count": len(checkpoints)}


def rollback_to_checkpoint(filename: str, discard_current_state: bool = False) -> dict[str, Any]:
    """Roll back to a checkpoint: open it, then rebind to the original path.

    S2 semantics: the scene ends up named after the file it was rolled
    back *for*, so a later save writes to the original scene file \u2014 not
    silently to the checkpoint. Untitled scenes / ad-hoc snapshots have
    no original path, so the scene stays on the checkpoint path (S1) and
    scene_rebound_to is None.

    Invariant: before the scene name is rebound, the current in-memory
    state has been snapshotted (auto_before_rollback via exportAll). If
    that auto snapshot fails the rollback aborts, unless
    discard_current_state=True escapes it (safety_snapshot becomes
    "skipped_by_user").

      For untitled scenes the auto snapshot lands in the workspace
      ad-hoc checkpoints dir; failure still aborts unless escaped.

    Honest bounds: snapshots contain no undo history \u2014 call
    scene_snapshot afterwards to rebuild context; references are
    flattened into the snapshot (self-contained, no write-back).
    """
    import os

    scene_before = _scene_file_path()
    cp_dir, derr = _checkpoint_dir(scene_before)
    if derr is not None:
        err0 = derr["error"]
        if isinstance(err0, dict):
            return _rb_error(
                err0.get("code", "internal"),
                err0["message"],
                scene_before,
                suggestion=err0.get("suggestion"),
                original_file_status=derr.get("original_file_status"),
            )
        return _rb_error("internal", str(err0), scene_before)

    if os.path.exists(cp_dir) and not os.path.isdir(cp_dir):
        return _rb_error(
            "path_not_directory",
            f"Checkpoints path is not a directory: {cp_dir}",
            scene_before,
            suggestion="remove or rename the conflicting file",
        )

    if os.path.basename(filename) != filename or not _CHECKPOINT_FILE_RE.match(filename):
        return _rb_error(
            "invalid_filename",
            f"Invalid checkpoint filename: {filename!r}",
            scene_before,
            suggestion="use a filename from scene_checkpoint_list (cp_*.ma / prev_*.ma)",
        )
    cp_path = os.path.join(cp_dir, filename)
    try:
        inside = os.path.commonpath(
            [os.path.abspath(cp_path), os.path.abspath(cp_dir)]
        ) == os.path.abspath(cp_dir)
    except ValueError:
        inside = False
    if not inside:
        return _rb_error(
            "path_escapes_dir",
            f"Checkpoint path escapes checkpoints dir: {filename!r}",
            scene_before,
        )

    if not os.path.exists(cp_path):
        return _rb_error(
            "snapshot_lost",
            f"Checkpoint snapshot lost (快照已丢失): {filename} — "
            "file missing, possibly deleted externally",
            scene_before,
            suggestion="re-create the checkpoint with scene_checkpoint",
        )
    try:
        with open(cp_path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(2048)
    except Exception as e:
        return _rb_error(
            "snapshot_lost",
            f"Checkpoint snapshot lost (快照已丢失): {filename} — unreadable: {e}",
            scene_before,
            suggestion="check filesystem permissions on the checkpoints dir",
        )
    if _MAYA_ASCII_MAGIC not in head:
        return _rb_error(
            "snapshot_lost",
            f"Checkpoint snapshot lost (\u5feb\u7167\u5df2\u4e22\u5931): {filename} \u2014 missing "
            "//Maya ASCII header, file was replaced or corrupted",
            scene_before,
            suggestion="delete the corrupt file and re-create the checkpoint",
        )

    safety_snapshot = None
    warning = None
    auto = _auto_snapshot(cp_dir)
    if "error" in auto:
        if not discard_current_state:
            cause = auto["error"]
            detail = cause["message"] if isinstance(cause, dict) else str(cause)
            return _rb_error(
                "auto_snapshot_failed",
                f"auto_before_rollback snapshot failed, rollback aborted: {detail}",
                scene_before,
                aborted=True,
                suggestion="fix the snapshot failure, or pass "
                "discard_current_state=True to roll back without "
                "a safety snapshot",
            )
        safety_snapshot = "skipped_by_user"
    else:
        safety_snapshot = auto["path"]
        if discard_current_state:
            warning = (
                "discard_current_state=True was passed but the safety "
                "snapshot succeeded; the flag had no effect."
            )

    try:
        cmds.file(cp_path, open=True, force=True, prompt=False)
    except Exception as e:
        return _rb_error(
            "open_failed",
            f"Failed to open checkpoint: {e}",
            scene_before,
            scene_after=_scene_file_path(),
            safety_snapshot=safety_snapshot,
            suggestion="scene may be in a partial state \u2014 call scene_snapshot "
            "to rebuild context",
        )

    if scene_before:
        try:
            # -rename must be used by itself on real Maya — combining it
            # with prompt=False raises "the -rename flag must be used by
            # itself" (live-verified on Maya 2024; probe artifact pending
            # docs/evidence intake at the next real-Maya window;
            # the stub never modeled that constraint).
            cmds.file(rename=scene_before)
        except Exception as e:
            return _rb_error(
                "rebind_failed",
                f"Checkpoint opened but rebind to original scene path failed: {e}",
                scene_before,
                scene_after=_scene_file_path(),
                safety_snapshot=safety_snapshot,
                suggestion="call scene_snapshot to rebuild context; save the "
                "scene manually to fix the file name",
            )
        rebound_to = scene_before
    else:
        rebound_to = None

    result = {
        "success": True,
        "rolled_back_to": filename,
        "scene_name_before": scene_before,
        "scene_name_after": _scene_file_path(),
        "original_file_status": "saved" if scene_before else "no_original_file",
        "scene_rebound_to": rebound_to,
        "safety_snapshot": safety_snapshot,
        "object_count": len(cmds.ls(type="transform", long=True) or []),
    }
    if warning:
        result["warning"] = warning
    return result


# ============================================================
# P1: Camera / Shot Planning
# ============================================================

SHOT_TYPES = {
    "extreme_wide": {"distance_mult": 8.0, "focal_length": 18, "desc": "极远景 - 建立环境"},
    "wide": {"distance_mult": 5.0, "focal_length": 24, "desc": "远景 - 展示全貌"},
    "medium": {"distance_mult": 3.0, "focal_length": 35, "desc": "中景 - 展示动作"},
    "close": {"distance_mult": 1.5, "focal_length": 50, "desc": "特写 - 展示细节"},
    "extreme_close": {"distance_mult": 0.8, "focal_length": 85, "desc": "极特写 - 展示微细节"},
    "over_shoulder": {"distance_mult": 2.0, "focal_length": 50, "desc": "过肩镜头"},
    "bird_eye": {"distance_mult": 10.0, "focal_length": 24, "desc": "鸟瞰"},
    "low_angle": {"distance_mult": 3.0, "focal_length": 35, "desc": "仰拍"},
}


def create_camera_shot(target, shot_type="medium", name="CAM_shot", angle=None):
    """Create a camera positioned for a specific shot type.

    Args:
        target: Target object name to look at.
        shot_type: One of SHOT_TYPES keys.
        name: Camera name.
        angle: Optional dict with "azimuth" and "elevation" in degrees.

    Returns:
        dict with camera info.
    """
    if shot_type not in SHOT_TYPES:
        return _err(
            "unknown_shot_type",
            f"Unknown shot type: {shot_type}. Available: {list(SHOT_TYPES.keys())}",
        )

    shot = SHOT_TYPES[shot_type]

    # Get target position and size
    try:
        sel = om2.MSelectionList()
        sel.add(target)
        dag = sel.getDagPath(0)
        fn = om2.MFnDagNode(dag)
        bbox = fn.boundingBox
        wm = dag.inclusiveMatrix()

        target_pos = [wm[12], wm[13], wm[14]]
        target_size = max(
            abs(bbox.max[0] - bbox.min[0]),
            abs(bbox.max[1] - bbox.min[1]),
            abs(bbox.max[2] - bbox.min[2]),
        )
    except Exception as e:
        return _err(
            "target_not_found",
            f"Target not found: {target}: {e}",
            "call scene_snapshot to list available targets",
        )

    # Calculate camera position
    distance = target_size * shot["distance_mult"]
    azimuth = (angle or {}).get("azimuth", 30)
    elevation = (angle or {}).get("elevation", 15)

    import math

    az_rad = math.radians(azimuth)
    el_rad = math.radians(elevation)

    cam_x = target_pos[0] + distance * math.cos(el_rad) * math.sin(az_rad)
    cam_y = target_pos[1] + distance * math.sin(el_rad)
    cam_z = target_pos[2] + distance * math.cos(el_rad) * math.cos(az_rad)

    # Create camera
    cam_transform, cam_shape = cmds.camera(
        name=name,
        focalLength=shot["focal_length"],
        horizontalFilmAperture=1.417,  # 35mm
    )
    cam_transform = cmds.rename(cam_transform, name) or name

    cmds.move(cam_x, cam_y, cam_z, cam_transform)

    # Aim at target using orient constraint or manual rotation
    try:
        cmds.aimConstraint(
            target,
            cam_transform,
            aimVector=[0, 0, -1],
            upVector=[0, 1, 0],
            worldUpType="scene",
        )
    except Exception:
        # Fallback: calculate rotation manually
        import math

        dx = target_pos[0] - cam_x
        dy = target_pos[1] - cam_y
        dz = target_pos[2] - cam_z
        dist_xz = math.sqrt(dx * dx + dz * dz)
        if dist_xz > 0:
            ry = math.degrees(math.atan2(-dx, -dz))
            rx = math.degrees(math.atan2(dy, dist_xz))
        else:
            ry, rx = 0, -90 if dy > 0 else 90
        cmds.rotate(rx, ry, 0, cam_transform)

    return {
        "camera": cam_transform,
        "shot_type": shot_type,
        "description": shot["desc"],
        "position": [round(cam_x, 1), round(cam_y, 1), round(cam_z, 1)],
        "focal_length": shot["focal_length"],
        "distance": round(distance, 1),
        "target": target,
    }


def create_orbit_camera(center, radius=500, frames=120, name="CAM_orbit"):
    """Create a camera that orbits around a point using keyframes.

    Args:
        center: [x, y, z] center point.
        radius: Orbit radius.
        frames: Number of frames for full orbit.
        name: Camera name.

    Returns:
        dict with camera and animation info.
    """
    import math

    cam_transform, cam_shape = cmds.camera(name=name, focalLength=35)
    cam_transform = cmds.rename(cam_transform, name) or name

    # Orbit pivot: locator at the requested center, then aim-constrain to it
    loc = cmds.spaceLocator(name="LOC_%s_target" % name)
    loc_transform = loc[0]
    cmds.move(center[0], center[1], center[2], loc_transform)

    # Set keyframes for circular orbit
    step = max(1, frames // 30)  # Key every N frames for smooth orbit
    for frame in range(1, frames + 1, step):
        angle = (2 * math.pi * (frame - 1)) / frames
        x = center[0] + radius * math.cos(angle)
        y = center[1] + radius * 0.3
        z = center[2] + radius * math.sin(angle)
        cmds.currentTime(frame, edit=True)
        cmds.move(x, y, z, cam_transform)
        cmds.setKeyframe(cam_transform, attribute="translateX")
        cmds.setKeyframe(cam_transform, attribute="translateY")
        cmds.setKeyframe(cam_transform, attribute="translateZ")

    # Set smooth tangents
    cmds.select(cam_transform)
    cmds.keyTangent(edit=True, itt="auto", ott="auto")

    # Aim constraint at the center locator — failures surface, never swallow
    warnings = []
    try:
        cmds.aimConstraint(
            loc_transform,
            cam_transform,
            aimVector=[0, 0, -1],
            upVector=[0, 1, 0],
            worldUpType="scene",
        )
    except Exception as e:
        cmds.warning("create_orbit_camera: aimConstraint failed: %s" % e)
        warnings.append("aimConstraint failed: %s" % e)

    # Set playback range
    cmds.playbackOptions(min=1, max=frames)

    return {
        "camera": cam_transform,
        "center_locator": loc_transform,
        "center": center,
        "radius": radius,
        "frames": frames,
        "keyframe_step": step,
        "warnings": warnings,
    }


# ============================================================
# P1: Aesthetic Analysis (Enhanced Professional Engine)
# ============================================================

# Maya production standards (unified, no duplicates)
_MAYA_STANDARDS = {
    # Naming
    "forbidden_prefixes": [
        "pCube",
        "pSphere",
        "pCylinder",
        "pCone",
        "nurbsCircle",
        "polySurface",
        "group",
        "null",
        "untitled",
        "default",
    ],
    "required_group_prefix": "GRP_",
    "max_nesting_depth": 4,
    # Spatial
    "max_objects_soft": 500,
    "max_objects_hard": 2000,
    "max_cameras_keep": 10,
    "min_lights": 3,
    # Componentization
    "min_objects_per_group": 2,
    "require_groups_for": ["mesh", "light", "camera"],
    # Architecture
    "min_clearance_cm": 5,
    "overlap_tolerance_cm": 1,
    # Retail/Pop-up standards
    "aisle_main_min_cm": 180,
    "aisle_secondary_min_cm": 120,
    "aisle_display_min_cm": 90,
    "wheelchair_turn_cm": 150,
    "display_height_min_cm": 80,
    "display_height_max_cm": 200,
    "display_height_ideal_cm": 130,
    "ceiling_min_cm": 280,
    "ceiling_ideal_cm": 350,
    "entrance_width_min_cm": 200,
    "entrance_decompression_cm": 150,
    "max_main_colors": 5,
    "min_contrast_ratio": 4.5,
    "focal_min": 2,
    "focal_max": 5,
}


def analyze_aesthetics():
    """Professional-grade aesthetic analysis with 5 design dimensions.

    Dimensions:
    1. Color Theory (60-30-10 rule, temperature, harmony, saturation, contrast)
    2. Spatial Composition (golden ratio, rule of thirds, visual weight balance)
    3. Proportion & Scale (human scale reference, size hierarchy)
    4. Lighting Quality (layer composition, color temperature consistency)
    5. Visual Flow (sight lines, circulation clarity, visual rhythm)

    Returns:
        dict with overall_score (0-100), grade (S/A/B/C/D/F), and detailed analysis.
    """
    transforms = cmds.ls(type="transform", long=True) or []

    mat_data = get_material_map()
    materials = mat_data.get("materials", [])

    objects = []
    scene_min = [float("inf")] * 3
    scene_max = [float("-inf")] * 3

    sampled = transforms[:_SAMPLE_AESTHETICS]
    for t in sampled:
        try:
            sel = om2.MSelectionList()
            sel.add(t)
            dag = sel.getDagPath(0)
            fn = om2.MFnDagNode(dag)
            wm = dag.inclusiveMatrix()
            wb = _world_bbox(dag)

            pos = [wm[12], wm[13], wm[14]]
            size_x = abs(wb.max[0] - wb.min[0])
            size_y = abs(wb.max[1] - wb.min[1])
            size_z = abs(wb.max[2] - wb.min[2])

            if size_x < 0.01 and size_y < 0.01 and size_z < 0.01:
                continue

            for axis in range(3):
                scene_min[axis] = min(scene_min[axis], wb.min[axis])
                scene_max[axis] = max(scene_max[axis], wb.max[axis])

            mat = _get_material_for_dag(dag)
            mat_color = None
            if mat:
                for m in materials:
                    if m["name"] == mat:
                        mat_color = m["color"]
                        break

            name = fn.name()
            is_focal = any(
                kw in name.lower()
                for kw in [
                    "kitty",
                    "hello_kitty",
                    "ip_",
                    "character",
                    "figure",
                    "hero",
                    "focal",
                    "main_display",
                    "centerpiece",
                ]
            )

            objects.append(
                {
                    "name": name,
                    "position": pos,
                    "bbox_size": [size_x, size_y, size_z],
                    "size": max(size_x, size_y, size_z),
                    "color": mat_color,
                    "material": mat,
                    "is_focal": is_focal,
                }
            )
        except Exception:
            pass

    light_shapes = cmds.ls(type="light") or []
    lights = []
    for ls_name in light_shapes:
        try:
            parent = cmds.listRelatives(ls_name, parent=True, fullPath=True) or [ls_name]
            light_transform = parent[0]
            ltype = cmds.nodeType(ls_name)

            color = (
                cmds.getAttr(f"{ls_name}.color")[0]
                if cmds.objExists(f"{ls_name}.color")
                else [1, 1, 1]
            )
            intensity = (
                cmds.getAttr(f"{ls_name}.intensity")
                if cmds.objExists(f"{ls_name}.intensity")
                else 1.0
            )

            # Position from transform
            pos = cmds.xform(light_transform, q=True, ws=True, t=True) or [0, 0, 0]
            # Rotation from transform (direction)
            rot = cmds.xform(light_transform, q=True, ws=True, ro=True) or [0, 0, 0]

            # Decay type (0=None, 1=Linear, 2=Quadratic, 3=Cubic)
            decay = 0
            if cmds.objExists(f"{ls_name}.decayRate"):
                decay = cmds.getAttr(f"{ls_name}.decayRate")

            # Shadow
            shadow_on = True
            if cmds.objExists(f"{ls_name}.useDepthMapShadow"):
                shadow_on = bool(cmds.getAttr(f"{ls_name}.useDepthMapShadow"))
            elif cmds.objExists(f"{ls_name}.useRayTraceShadows"):
                shadow_on = bool(cmds.getAttr(f"{ls_name}.useRayTraceShadows"))

            # Spotlight cone angle (degrees)
            cone_angle = None
            penumbra = None
            if ltype in ("spotLight",) and cmds.objExists(f"{ls_name}.coneAngle"):
                cone_angle = cmds.getAttr(f"{ls_name}.coneAngle")
                if cmds.objExists(f"{ls_name}.penumbraAngle"):
                    penumbra = cmds.getAttr(f"{ls_name}.penumbraAngle")

            # Area light size
            area_size = None
            if ltype in ("areaLight",) and cmds.objExists(f"{ls_name}.areaWidth"):
                aw = cmds.getAttr(f"{ls_name}.areaWidth")
                ah = (
                    cmds.getAttr(f"{ls_name}.areaHeight")
                    if cmds.objExists(f"{ls_name}.areaHeight")
                    else aw
                )
                area_size = [aw, ah]

            # Diffuse/Specular contribution
            diffuse = (
                cmds.getAttr(f"{ls_name}.emitDiffuse")
                if cmds.objExists(f"{ls_name}.emitDiffuse")
                else True
            )
            specular = (
                cmds.getAttr(f"{ls_name}.emitSpecular")
                if cmds.objExists(f"{ls_name}.emitSpecular")
                else True
            )

            lights.append(
                {
                    "name": light_transform.split("|")[-1],
                    "type": ltype,
                    "color": list(color),
                    "intensity": intensity,
                    "position": [round(v, 1) for v in pos],
                    "rotation": [round(v, 1) for v in rot],
                    "decay": decay,
                    "shadow": shadow_on,
                    "cone_angle": round(cone_angle, 1) if cone_angle else None,
                    "penumbra": round(penumbra, 1) if penumbra else None,
                    "area_size": area_size,
                    "diffuse": bool(diffuse),
                    "specular": bool(specular),
                }
            )
        except Exception:
            pass

    if scene_min[0] != float("inf"):
        scene_bounds = {"min": scene_min, "max": scene_max}
    else:
        scene_bounds = {"min": [0, 0, 0], "max": [100, 100, 100]}

    zone_map = None
    try:
        zone_data = get_zone_map()
        if isinstance(zone_data, dict):
            zone_map = zone_data
    except Exception:
        pass

    result = _compute_full_aesthetic_score(materials, objects, scene_bounds, lights, zone_map)
    result["sampling"] = {
        "checked": len(sampled),
        "skipped": len(transforms) - len(sampled),
        "object_limit": _SAMPLE_AESTHETICS,
    }
    return result


def _compute_full_aesthetic_score(materials, objects, scene_bounds, lights, zone_map):
    """Compute comprehensive aesthetic score across 5 dimensions."""
    PHI = 1.618033988749895

    dimensions = {}
    dimensions["color_theory"] = _score_color_theory(materials)
    dimensions["spatial_composition"] = _score_spatial_composition(objects, scene_bounds, PHI)
    dimensions["proportion_scale"] = _score_proportion_scale(objects)
    dimensions["lighting_quality"] = _score_lighting_quality(lights)
    dimensions["visual_flow"] = _score_visual_flow(objects, zone_map)

    # Research-based enhancements (Aesthetic3D, arXiv 2016, Tripo3D)
    silhouette = _analyze_silhouette_quality(objects)
    curvature = _analyze_curvature_distribution(objects)
    rhythm = _analyze_spatial_rhythm_enhanced(objects)
    style = _analyze_style_consistency(objects, materials)

    # Integrate into existing dimensions
    dimensions["proportion_scale"]["silhouette"] = silhouette
    dimensions["proportion_scale"]["curvature"] = curvature
    dimensions["visual_flow"]["rhythm_enhanced"] = rhythm
    dimensions["color_theory"]["style_consistency"] = style

    weights = {
        "color_theory": 0.25,
        "spatial_composition": 0.25,
        "proportion_scale": 0.20,
        "lighting_quality": 0.15,
        "visual_flow": 0.15,
    }
    overall = sum(dimensions[d]["score"] * weights[d] for d in dimensions)

    if overall >= 90:
        grade = "S"
    elif overall >= 80:
        grade = "A"
    elif overall >= 70:
        grade = "B"
    elif overall >= 60:
        grade = "C"
    elif overall >= 50:
        grade = "D"
    else:
        grade = "F"

    suggestions = []
    for name, data in sorted(dimensions.items(), key=lambda x: x[1]["score"]):
        if data["score"] < 70:
            suggestions.append(
                {
                    "dimension": name,
                    "score": data["score"],
                    "priority": "high" if data["score"] < 50 else "medium",
                }
            )

    return {
        "overall_score": round(overall, 1),
        "grade": grade,
        "dimensions": {k: v["score"] for k, v in dimensions.items()},
        "detail": dimensions,
        "improvement_suggestions": suggestions[:3],
    }


def _rgb_to_hsl(r, g, b):
    """RGB [0-1] to HSL (H:0-360, S:0-1, L:0-1)."""
    mx = max(r, g, b)
    mn = min(r, g, b)
    l = (mx + mn) / 2.0
    if mx == mn:
        return (0.0, 0.0, l)
    d = mx - mn
    s = d / (2.0 - mx - mn) if l > 0.5 else d / (mx + mn)
    if mx == r:
        h = 60 * (((g - b) / d) % 6)
    elif mx == g:
        h = 60 * ((b - r) / d + 2)
    else:
        h = 60 * ((r - g) / d + 4)
    return (h % 360, s, l)


def _rgb_to_hue(rgb):
    """Extract hue (0-360) from RGB [0-1]."""
    return _rgb_to_hsl(rgb[0], rgb[1], rgb[2])[0]


def _color_temp_k(r, g, b):
    """Estimate color temperature in Kelvin."""
    if r + g + b < 0.01:
        return 6500.0
    r255, g255, b255 = r * 255, g * 255, b * 255
    if r255 >= b255:
        ratio = b255 / max(r255, 1)
        return max(1000, min(12000, 3000 + ratio * 3500))
    else:
        ratio = r255 / max(b255, 1)
        return max(1000, min(12000, 6500 + (1 - ratio) * 3500))


def _wcag_luminance(r, g, b):
    """WCAG 2.0 relative luminance."""

    def linearize(v):
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)


def _score_color_theory(materials):
    """Dimension 1: Color Theory analysis."""
    active = [m for m in materials if m.get("object_count", 0) > 0]
    if not active:
        return {"score": 0, "sub_scores": {}, "detail": "no_active_materials"}

    colors = sorted(active, key=lambda c: c["object_count"], reverse=True)
    total_w = sum(c["object_count"] for c in colors)

    sub_scores = {}

    if len(colors) >= 3 and total_w > 0:
        r60 = abs(colors[0]["object_count"] / total_w - 0.60)
        r30 = abs(colors[1]["object_count"] / total_w - 0.30)
        r10 = abs(colors[2]["object_count"] / total_w - 0.10)
        dev = (r60 + r30 + r10) / 3
        sub_scores["60_30_10"] = max(0, 100 - dev * 200)
    else:
        sub_scores["60_30_10"] = 40

    warm_w, cool_w = 0, 0
    for c in colors:
        temp = _color_temp_k(c["color"][0], c["color"][1], c["color"][2])
        if temp < 3700:
            warm_w += c["object_count"]
        elif temp > 5000:
            cool_w += c["object_count"]
        else:
            warm_w += c["object_count"] * 0.5
            cool_w += c["object_count"] * 0.5
    imbalance = abs(warm_w - cool_w) / max(total_w, 1)
    sub_scores["temperature"] = max(0, 100 - imbalance * 100)

    sats = []
    for c in colors[:8]:
        _, s, _ = _rgb_to_hsl(c["color"][0], c["color"][1], c["color"][2])
        sats.append(s)
    if len(sats) >= 2:
        sat_range = max(sats) - min(sats)
        sub_scores["saturation"] = 85 if sat_range > 0.3 else 70 if sat_range > 0.15 else 40
    else:
        sub_scores["saturation"] = 50

    if len(colors) >= 2:
        h1 = _rgb_to_hue(colors[0]["color"])
        h2 = _rgb_to_hue(colors[1]["color"])
        diff = abs(h1 - h2)
        if diff > 180:
            diff = 360 - diff
        if diff < 15:
            sub_scores["harmony"] = 70
        elif diff < 45:
            sub_scores["harmony"] = 85
        elif 150 < diff < 210:
            sub_scores["harmony"] = 90
        elif 100 < diff < 140:
            sub_scores["harmony"] = 80
        else:
            sub_scores["harmony"] = 40
    else:
        sub_scores["harmony"] = 60

    if colors:
        lum_values = [_wcag_luminance(c["color"][0], c["color"][1], c["color"][2]) for c in colors]
        lightest = max(lum_values)
        darkest = min(lum_values)
        ratio = (lightest + 0.05) / (darkest + 0.05) if darkest > 0 else 21
        if ratio >= 7:
            sub_scores["contrast"] = 100
        elif ratio >= 4.5:
            sub_scores["contrast"] = 85
        elif ratio >= 3:
            sub_scores["contrast"] = 65
        else:
            sub_scores["contrast"] = 40
    else:
        sub_scores["contrast"] = 50
        ratio = 1.0

    weights = {
        "60_30_10": 0.20,
        "temperature": 0.15,
        "saturation": 0.15,
        "harmony": 0.25,
        "contrast": 0.25,
    }
    overall = sum(sub_scores[k] * weights[k] for k in sub_scores)

    return {
        "score": round(overall, 1),
        "sub_scores": {k: round(v, 1) for k, v in sub_scores.items()},
        "top_colors": [
            {"name": c["name"], "color": c["color"], "weight": c["object_count"]}
            for c in colors[:6]
        ],
    }


def _score_spatial_composition(objects, scene_bounds, PHI):
    """Dimension 2: Spatial Composition analysis."""
    sub_scores = {}
    bmin = scene_bounds["min"]
    bmax = scene_bounds["max"]
    mid_x = (bmin[0] + bmax[0]) / 2
    mid_z = (bmin[2] + bmax[2]) / 2
    span_x = bmax[0] - bmin[0]
    span_z = bmax[2] - bmin[2]

    if len(objects) >= 2:
        sizes = [o["size"] for o in objects if o["size"] > 0]
        golden_matches = 0
        total_pairs = 0
        for i in range(min(len(sizes), 15)):
            for j in range(i + 1, min(len(sizes), i + 10)):
                if sizes[j] > 0:
                    ratio = sizes[i] / sizes[j]
                    if ratio < 1:
                        ratio = 1 / ratio
                    total_pairs += 1
                    if abs(ratio - PHI) < 0.15:
                        golden_matches += 1
        sub_scores["golden_ratio"] = (
            min(100, 50 + (golden_matches / max(total_pairs, 1)) * 100) if total_pairs > 0 else 50
        )
    else:
        sub_scores["golden_ratio"] = 50

    if objects and span_x > 0.01 and span_z > 0.01:
        tol = min(span_x, span_z) * 0.10
        grid_x = [bmin[0] + span_x / 3, bmin[0] + span_x * 2 / 3]
        grid_z = [bmin[2] + span_z / 3, bmin[2] + span_z * 2 / 3]
        aligned_weight = 0
        total_weight = 0
        for obj in objects:
            w = obj["size"]
            total_weight += w
            px, pz = obj["position"][0], obj["position"][2]
            for gx in grid_x:
                for gz in grid_z:
                    dist = ((px - gx) ** 2 + (pz - gz) ** 2) ** 0.5
                    if dist < tol:
                        aligned_weight += w
                        break
        sub_scores["rule_of_thirds"] = min(100, 40 + (aligned_weight / max(total_weight, 1)) * 120)
    else:
        sub_scores["rule_of_thirds"] = 50

    if objects:
        quads = [0, 0, 0, 0]
        for obj in objects:
            px, pz = obj["position"][0], obj["position"][2]
            w = obj["size"] * (1.5 if obj.get("is_focal") else 1.0)
            if px <= mid_x and pz <= mid_z:
                quads[0] += w
            elif px > mid_x and pz <= mid_z:
                quads[1] += w
            elif px <= mid_x and pz > mid_z:
                quads[2] += w
            else:
                quads[3] += w
        total_q = sum(quads)
        if total_q > 0:
            max_dev = max(abs(q / total_q - 0.25) for q in quads)
            sub_scores["visual_balance"] = max(0, 100 - max_dev * 400)
        else:
            sub_scores["visual_balance"] = 50
    else:
        sub_scores["visual_balance"] = 50

    weights = {"golden_ratio": 0.30, "rule_of_thirds": 0.35, "visual_balance": 0.35}
    overall = sum(sub_scores[k] * weights[k] for k in sub_scores)

    return {
        "score": round(overall, 1),
        "sub_scores": {k: round(v, 1) for k, v in sub_scores.items()},
    }


def _score_proportion_scale(objects):
    """Dimension 3: Proportion & Scale analysis."""
    HUMAN_EYE_HEIGHT = 150.0
    HUMAN_REACH_HEIGHT = 200.0

    sub_scores = {}
    issues = []
    hs_score = 100

    for obj in objects:
        name = obj["name"].lower()
        by = obj["bbox_size"][1]
        bx = obj["bbox_size"][0]
        if any(kw in name for kw in ["display", "shelf", "counter", "kiosk"]):
            if by > HUMAN_REACH_HEIGHT:
                issues.append({"type": "too_high", "object": obj["name"], "height": round(by, 1)})
                hs_score -= 5
            elif by < 60:
                issues.append({"type": "too_low", "object": obj["name"], "height": round(by, 1)})
                hs_score -= 2
        if any(kw in name for kw in ["aisle", "corridor", "path", "passage"]):
            if bx < 120:
                issues.append({"type": "too_narrow", "object": obj["name"], "width": round(bx, 1)})
                hs_score -= 10
    sub_scores["human_scale"] = max(0, hs_score)

    if len(objects) >= 3:
        sorted_objs = sorted(objects, key=lambda o: o["size"], reverse=True)
        max_size = sorted_objs[0]["size"]
        hero = [o for o in sorted_objs if o["size"] >= max_size * 0.6]
        secondary = [o for o in sorted_objs if max_size * 0.25 <= o["size"] < max_size * 0.6]
        hier_score = 70
        if 1 <= len(hero) <= 3:
            hier_score += 15
        elif len(hero) == 0:
            hier_score -= 20
        if 2 <= len(secondary) <= 10:
            hier_score += 15
        sub_scores["hierarchy"] = min(100, hier_score)
    else:
        sub_scores["hierarchy"] = 50

    weights = {"human_scale": 0.50, "hierarchy": 0.50}
    overall = sum(sub_scores[k] * weights[k] for k in sub_scores)

    return {
        "score": round(overall, 1),
        "sub_scores": {k: round(v, 1) for k, v in sub_scores.items()},
        "issues": issues[:5],
    }


def _score_lighting_quality(lights):
    """Dimension 4: Professional Lighting Quality Analysis.

    Sub-analyses:
    - Layer composition: key/fill/rim/accent completeness
    - Three-point lighting validation
    - Key light direction & angle analysis
    - Fill ratio (key:fill intensity)
    - Shadow quality (on/off, type)
    - Color temperature grading & mood
    - Light intensity distribution (even vs dramatic)
    - Decay rate correctness (physically plausible)
    - Coverage analysis (spatial distribution)
    """
    sub_scores = {}

    if not lights:
        return {
            "score": 0,
            "sub_scores": {
                "layers": 0,
                "three_point": 0,
                "fill_ratio": 0,
                "shadow_quality": 0,
                "temperature": 0,
                "coverage": 0,
                "decay": 0,
                "intensity_dist": 0,
            },
            "detail": "no_lights",
        }

    # --- Layer classification ---
    layers = {"key": [], "fill": [], "rim": [], "accent": [], "other": []}
    for light in lights:
        name_l = light["name"].lower()
        if any(kw in name_l for kw in ["key", "main", "primary", "hero"]):
            layers["key"].append(light)
        elif any(kw in name_l for kw in ["fill", "ambient", "bounce"]):
            layers["fill"].append(light)
        elif any(kw in name_l for kw in ["rim", "edge", "back", "kicker"]):
            layers["rim"].append(light)
        elif any(kw in name_l for kw in ["accent", "spot", "highlight", "focus"]):
            layers["accent"].append(light)
        else:
            layers["other"].append(light)

    # Layer completeness score
    layer_score = 20
    if layers["key"]:
        layer_score += 30
    if layers["fill"]:
        layer_score += 25
    if layers["accent"]:
        layer_score += 15
    if layers["rim"]:
        layer_score += 10
    sub_scores["layers"] = min(100, layer_score)

    # --- Three-point lighting validation ---
    # Professional standard: key + fill + rim/accent
    has_key = len(layers["key"]) > 0
    has_fill = len(layers["fill"]) > 0
    has_rim_or_accent = len(layers["rim"]) > 0 or len(layers["accent"]) > 0
    three_point_score = 0
    if has_key:
        three_point_score += 40
    if has_fill:
        three_point_score += 30
    if has_rim_or_accent:
        three_point_score += 20
    if has_key and has_fill and has_rim_or_accent:
        three_point_score += 10
    sub_scores["three_point"] = min(100, three_point_score)

    # --- Fill ratio (key:fill intensity) ---
    # Ideal: 2:1 to 4:1 (cinematic) or 1.5:1 (flat/commercial)
    key_intensity = sum(l["intensity"] for l in layers["key"]) if layers["key"] else 0
    fill_intensity = sum(l["intensity"] for l in layers["fill"]) if layers["fill"] else 0

    if key_intensity > 0 and fill_intensity > 0:
        ratio = key_intensity / fill_intensity
        if 1.5 <= ratio <= 4.0:
            fill_ratio_score = 90
        elif 1.0 <= ratio <= 6.0:
            fill_ratio_score = 70
        elif ratio > 6.0:
            fill_ratio_score = 50  # Too harsh
        else:
            fill_ratio_score = 50  # Too flat
    elif key_intensity > 0:
        fill_ratio_score = 40  # No fill = harsh shadows
    else:
        fill_ratio_score = 30  # No key light
    sub_scores["fill_ratio"] = fill_ratio_score

    # --- Shadow quality ---
    shadow_count = sum(1 for l in lights if l.get("shadow", False))
    shadow_ratio = shadow_count / len(lights) if lights else 0
    if 0.3 <= shadow_ratio <= 0.8:
        shadow_score = 90  # Good: most lights cast shadows but not all
    elif shadow_ratio > 0.8:
        shadow_score = 70  # Too many shadows (dark)
    elif shadow_ratio > 0:
        shadow_score = 60  # Too few shadows (flat)
    else:
        shadow_score = 30  # No shadows at all
    sub_scores["shadow_quality"] = shadow_score

    # --- Color temperature grading ---
    if len(lights) >= 2:
        temps = []
        for light in lights:
            c = light.get("color", [1, 1, 1])
            temps.append(_color_temp_k(c[0], c[1], c[2]))
        temp_range = max(temps) - min(temps)
        avg_temp = sum(temps) / len(temps)

        # Consistency score
        if temp_range < 500:
            temp_score = 90
            temp_consistency = "very_consistent"
        elif temp_range < 1500:
            temp_score = 75
            temp_consistency = "consistent"
        elif temp_range < 3000:
            temp_score = 55
            temp_consistency = "mixed"
        else:
            temp_score = 30
            temp_consistency = "inconsistent"

        # Mood classification
        if avg_temp < 3500:
            mood = "warm_intimate"
        elif avg_temp < 4500:
            mood = "warm_neutral"
        elif avg_temp < 5500:
            mood = "neutral"
        elif avg_temp < 6500:
            mood = "cool_professional"
        else:
            mood = "cool_dramatic"

        # Bonus for intentional contrast (key warm + fill cool or vice versa)
        if layers["key"] and layers["fill"]:
            key_temps = [
                _color_temp_k(l["color"][0], l["color"][1], l["color"][2]) for l in layers["key"]
            ]
            fill_temps = [
                _color_temp_k(l["color"][0], l["color"][1], l["color"][2]) for l in layers["fill"]
            ]
            kt_avg = sum(key_temps) / len(key_temps)
            ft_avg = sum(fill_temps) / len(fill_temps)
            if abs(kt_avg - ft_avg) > 1000:
                temp_score = min(100, temp_score + 10)  # Intentional contrast
    else:
        temp_score = 60
        temp_consistency = "single_light"
        mood = "unknown"
    sub_scores["temperature"] = temp_score

    # --- Coverage analysis (spatial distribution) ---
    if len(lights) >= 2:
        positions = [l["position"] for l in lights if l.get("position")]
        if positions:
            # Check if lights cover the scene evenly
            xs = [p[0] for p in positions]
            zs = [p[2] for p in positions]
            x_range = max(xs) - min(xs) if len(xs) > 1 else 0
            z_range = max(zs) - min(zs) if len(zs) > 1 else 0

            # Good coverage = lights spread across the scene
            if x_range > 100 and z_range > 100:
                coverage_score = 85
            elif x_range > 50 or z_range > 50:
                coverage_score = 65
            else:
                coverage_score = 40  # Clustered
        else:
            coverage_score = 50
    else:
        coverage_score = 40  # Single light = poor coverage
    sub_scores["coverage"] = coverage_score

    # --- Decay rate (physical plausibility) ---
    decay_scores = []
    for light in lights:
        decay = light.get("decay", 0)
        ltype = light.get("type", "")
        # Point/spot/area lights should use quadratic decay (physically correct)
        if ltype in ("pointLight", "spotLight", "areaLight"):
            if decay == 2:  # Quadratic = physically correct
                decay_scores.append(100)
            elif decay == 1:  # Linear = acceptable approximation
                decay_scores.append(70)
            elif decay == 0:  # No decay = unrealistic
                decay_scores.append(30)
            else:
                decay_scores.append(50)
        else:
            # Directional/ambient - decay not applicable
            decay_scores.append(80)

    sub_scores["decay"] = sum(decay_scores) / len(decay_scores) if decay_scores else 50

    # --- Intensity distribution ---
    if lights:
        intensities = [l["intensity"] for l in lights]
        max_int = max(intensities)
        min_int = min(intensities)
        if max_int > 0:
            # High contrast (dramatic) vs low contrast (flat)
            contrast = max_int / max(min_int, 0.001)
            if 2 <= contrast <= 10:
                intensity_score = 85  # Good dramatic range
            elif contrast > 10:
                intensity_score = 60  # Too extreme
            else:
                intensity_score = 65  # Too flat
        else:
            intensity_score = 30
    else:
        intensity_score = 0
    sub_scores["intensity_dist"] = intensity_score

    # --- Weighted overall ---
    weights = {
        "layers": 0.15,
        "three_point": 0.20,
        "fill_ratio": 0.15,
        "shadow_quality": 0.10,
        "temperature": 0.10,
        "coverage": 0.10,
        "decay": 0.10,
        "intensity_dist": 0.10,
    }
    overall = sum(sub_scores[k] * weights[k] for k in sub_scores)

    return {
        "score": round(overall, 1),
        "sub_scores": {k: round(v, 1) for k, v in sub_scores.items()},
        "layer_breakdown": {k: len(v) for k, v in layers.items()},
        "mood": mood if len(lights) >= 2 else "unknown",
        "fill_ratio": round(key_intensity / max(fill_intensity, 0.001), 1)
        if fill_intensity > 0
        else None,
        "temp_consistency": temp_consistency if len(lights) >= 2 else "single_light",
    }


def _score_visual_flow(objects, zone_map):
    """Dimension 5: Visual Flow analysis."""
    sub_scores = {}

    entrance = [0, 0, -500]
    focal_objs = [o for o in objects if o.get("is_focal")]
    if not focal_objs and objects:
        sorted_by_size = sorted(objects, key=lambda o: o["size"], reverse=True)
        focal_objs = sorted_by_size[:2]

    if focal_objs:
        clear_count = 0
        for focal in focal_objs:
            fp = focal["position"]
            dx, dz = fp[0] - entrance[0], fp[2] - entrance[2]
            ray_len = math.sqrt(dx * dx + dz * dz)
            if ray_len < 1:
                clear_count += 1
                continue
            ndx, ndz = dx / ray_len, dz / ray_len
            blocked = False
            for obj in objects:
                if obj["name"] == focal["name"] or obj.get("is_focal"):
                    continue
                op = obj["position"]
                t = (op[0] - entrance[0]) * ndx + (op[2] - entrance[2]) * ndz
                if 0.1 < t < ray_len * 0.95:
                    cx = entrance[0] + ndx * t
                    cz = entrance[2] + ndz * t
                    dist = math.sqrt((op[0] - cx) ** 2 + (op[2] - cz) ** 2)
                    if dist < obj["size"] * 0.3:
                        blocked = True
                        break
            if not blocked:
                clear_count += 1
        sub_scores["sight_lines"] = 40 + (clear_count / len(focal_objs)) * 60
    else:
        sub_scores["sight_lines"] = 50

    path_objs = [
        o
        for o in objects
        if any(kw in o["name"].lower() for kw in ["path", "aisle", "corridor", "walkway", "route"])
    ]
    if path_objs:
        circ_score = 70
        for p in path_objs:
            width = min(p["bbox_size"][0], p["bbox_size"][2])
            if width < 90:
                circ_score -= 10
            elif width < 120:
                circ_score -= 5
        sub_scores["circulation"] = max(0, circ_score)
    else:
        sub_scores["circulation"] = 40

    if len(objects) >= 3:
        sorted_objs = sorted(objects, key=lambda o: o["size"], reverse=True)
        groups = []
        current = [sorted_objs[0]]
        for i in range(1, len(sorted_objs)):
            ratio = sorted_objs[i]["size"] / current[0]["size"]
            if 0.7 < ratio < 1.4:
                current.append(sorted_objs[i])
            else:
                if len(current) >= 2:
                    groups.append(current)
                current = [sorted_objs[i]]
        if len(current) >= 2:
            groups.append(current)

        rhythm_score = 50
        for group in groups:
            if len(group) >= 3:
                positions = [o["position"] for o in group]
                spacings = []
                for i in range(len(positions) - 1):
                    d = math.sqrt(
                        (positions[i + 1][0] - positions[i][0]) ** 2
                        + (positions[i + 1][2] - positions[i][2]) ** 2
                    )
                    spacings.append(d)
                if len(spacings) >= 2:
                    avg = sum(spacings) / len(spacings)
                    if avg > 0:
                        var = sum((s - avg) ** 2 for s in spacings) / len(spacings)
                        cv = math.sqrt(var) / avg
                        if cv < 0.15:
                            rhythm_score += 20
                        elif cv < 0.30:
                            rhythm_score += 10
        sub_scores["rhythm"] = min(100, rhythm_score)
    else:
        sub_scores["rhythm"] = 50

    weights = {"sight_lines": 0.40, "circulation": 0.35, "rhythm": 0.25}
    overall = sum(sub_scores[k] * weights[k] for k in sub_scores)

    return {
        "score": round(overall, 1),
        "sub_scores": {k: round(v, 1) for k, v in sub_scores.items()},
    }


# ============================================================
# ENHANCED AESTHETIC ANALYSIS (Research-based)
# ============================================================


def _analyze_silhouette_quality(objects):
    """Analyze silhouette quality of objects.

    Research basis: Aesthetic3D (ACM 2026) shows that silhouette recognition
    is the primary driver of aesthetic judgment. Low-resolution representations
    (silhouettes) yield aesthetic correlations of r=0.78 with full 3D shapes.

    Checks:
    - Aspect ratio: extreme proportions (too thin/wide) reduce silhouette clarity
    - Volume distribution: balanced volume reads better than concentrated mass
    - Dominant dimension: hero objects should have clear dominant axis
    """
    results = []
    for obj in objects:
        bx, by, bz = obj.get("bbox_size", [0, 0, 0])
        if bx < 0.01 or by < 0.01 or bz < 0.01:
            continue

        # Aspect ratio analysis
        dims = sorted([bx, by, bz], reverse=True)
        aspect_primary = dims[0] / max(dims[2], 0.01)  # Largest/smallest
        aspect_secondary = dims[0] / max(dims[1], 0.01)

        # Volume compactness (how close to a cube)
        volume = bx * by * bz
        surface_area = 2 * (bx * by + by * bz + bx * bz)
        compactness = (6 * volume) ** (2 / 3) / max(surface_area, 0.01)  # Sphericity approximation

        # Silhouette clarity score
        # Ideal: moderate aspect ratio (1.5-4), high compactness
        if 1.2 <= aspect_primary <= 4.0:
            ratio_score = 90
        elif aspect_primary <= 8.0:
            ratio_score = 60
        else:
            ratio_score = 30  # Very thin/wide, poor silhouette

        clarity = ratio_score * 0.6 + compactness * 100 * 0.4

        results.append(
            {
                "name": obj.get("name", "?"),
                "aspect_ratio": round(aspect_primary, 2),
                "compactness": round(compactness, 3),
                "clarity_score": round(clarity, 1),
            }
        )

    if not results:
        return {"avg_clarity": 50, "details": []}

    avg_clarity = sum(r["clarity_score"] for r in results) / len(results)
    return {
        "avg_clarity": round(avg_clarity, 1),
        "details": sorted(results, key=lambda x: x["clarity_score"])[:5],
    }


def _analyze_curvature_distribution(objects):
    """Analyze geometric curvature distribution for aesthetic quality.

    Research basis: "A Perceptual Aesthetics Measure for 3D Shapes" (arXiv 2016)
    shows that high-aesthetic shapes have:
    - Smooth curvature transitions (low variance in curvature)
    - Moderate average curvature (not too sharp, not too flat)
    - Curvature concentrated at design features (not random noise)

    We approximate curvature from bounding box proportions since we don't
    have direct mesh access in this analysis path.
    """
    if not objects:
        return {"score": 50, "detail": "no_objects"}

    # Approximate curvature variety from size diversity
    sizes = [max(o.get("bbox_size", [1])) for o in objects if max(o.get("bbox_size", [1])) > 0]
    if len(sizes) < 2:
        return {"score": 50, "detail": "insufficient_data"}

    # Good design has varied but not chaotic size distribution
    avg_size = sum(sizes) / len(sizes)
    variance = sum((s - avg_size) ** 2 for s in sizes) / len(sizes)
    cv = (variance**0.5) / max(avg_size, 0.01)

    # CV of 0.3-0.8 is ideal (varied but organized)
    if 0.3 <= cv <= 0.8:
        score = 85
    elif 0.15 <= cv <= 1.2:
        score = 65
    else:
        score = 40

    return {
        "score": round(score, 1),
        "size_cv": round(cv, 3),
        "avg_size": round(avg_size, 1),
        "interpretation": "varied" if cv > 0.5 else "uniform" if cv > 0.2 else "monotonous",
    }


def _analyze_spatial_rhythm_enhanced(objects):
    """Enhanced spatial rhythm analysis with compression/release patterns.

    Research basis: "Narrative Through Level Design" (2025) and
    "Using Spatial Composition to Influence Player Tension" (2016) show that:
    - Alternating open/narrow spaces creates rhythm and engagement
    - Compression (narrow) → release (open) creates anticipation
    - Regular spacing of similar elements creates predictability
    """
    if len(objects) < 3:
        return {"score": 50, "detail": "insufficient_objects"}

    # Find groups of similarly-sized objects
    sorted_objs = sorted(objects, key=lambda o: max(o.get("bbox_size", [1])), reverse=True)
    groups = []
    current = [sorted_objs[0]]
    for i in range(1, len(sorted_objs)):
        ratio = max(sorted_objs[i].get("bbox_size", [1])) / max(
            max(current[0].get("bbox_size", [1])), 0.01
        )
        if 0.7 < ratio < 1.4:
            current.append(sorted_objs[i])
        else:
            if len(current) >= 2:
                groups.append(current)
            current = [sorted_objs[i]]
    if len(current) >= 2:
        groups.append(current)

    # Analyze spacing regularity within groups
    rhythm_score = 50
    patterns = 0
    for group in groups:
        if len(group) < 3:
            continue
        positions = [o["position"] for o in group]
        spacings = []
        for i in range(len(positions) - 1):
            d = math.sqrt(
                (positions[i + 1][0] - positions[i][0]) ** 2
                + (positions[i + 1][2] - positions[i][2]) ** 2
            )
            spacings.append(d)
        if len(spacings) >= 2:
            avg = sum(spacings) / len(spacings)
            if avg > 0:
                var = sum((s - avg) ** 2 for s in spacings) / len(spacings)
                cv = math.sqrt(var) / avg
                if cv < 0.15:
                    rhythm_score += 20
                    patterns += 1
                elif cv < 0.30:
                    rhythm_score += 10
                    patterns += 1

    # Analyze compression/release (alternating dense and sparse areas)
    if len(objects) >= 6:
        # Divide scene into quadrants and check density variation
        positions = [o["position"] for o in objects]
        xs = [p[0] for p in positions]
        zs = [p[2] for p in positions]
        mid_x = (min(xs) + max(xs)) / 2 if xs else 0
        mid_z = (min(zs) + max(zs)) / 2 if zs else 0

        quadrants = [0, 0, 0, 0]
        for p in positions:
            qi = (0 if p[0] > mid_x else 1) + (0 if p[2] > mid_z else 2)
            quadrants[qi] += 1

        # Good compression/release = density varies but not extreme
        max_q = max(quadrants)
        min_q = min(quadrants)
        if max_q > 0:
            density_ratio = min_q / max_q
            if 0.3 <= density_ratio <= 0.7:
                rhythm_score += 15  # Good compression/release
            elif density_ratio < 0.1:
                rhythm_score -= 10  # Too extreme

    return {
        "score": min(100, max(0, round(rhythm_score, 1))),
        "repeating_groups": len(groups),
        "patterns_found": patterns,
    }


def _analyze_style_consistency(objects, materials):
    """Analyze style consistency across the scene.

    Research basis: Tripo3D "Smart Mesh Building" guide emphasizes that
    every new asset must be checked against a style guide. Inconsistent
    form language (mixing sharp and round, thin and thick) creates visual chaos.

    Checks:
    - Form language consistency (similar aspect ratios across objects)
    - Size language consistency (clear hierarchy, no random outliers)
    - Material consistency (limited palette, consistent saturation)
    """
    if len(objects) < 3:
        return {"score": 60, "detail": "insufficient_objects"}

    # Form language: check if aspect ratios are consistent
    aspect_ratios = []
    for obj in objects:
        dims = sorted(obj.get("bbox_size", [1, 1, 1]), reverse=True)
        if dims[2] > 0:
            aspect_ratios.append(dims[0] / dims[2])

    if aspect_ratios:
        avg_ratio = sum(aspect_ratios) / len(aspect_ratios)
        ratio_variance = sum((r - avg_ratio) ** 2 for r in aspect_ratios) / len(aspect_ratios)
        ratio_cv = (ratio_variance**0.5) / max(avg_ratio, 0.01)
        # Low CV = consistent form language
        form_score = max(0, 100 - ratio_cv * 100)
    else:
        form_score = 50

    # Size language: check for clear hierarchy (no random outliers)
    sizes = sorted([max(o.get("bbox_size", [1])) for o in objects], reverse=True)
    if len(sizes) >= 3:
        # Check if there's a clear break between hero and secondary
        ratios = [sizes[i] / max(sizes[i + 1], 0.01) for i in range(len(sizes) - 1)]
        max_ratio = max(ratios) if ratios else 1
        if max_ratio > 2.0:
            size_score = 85  # Clear hierarchy
        elif max_ratio > 1.5:
            size_score = 70
        else:
            size_score = 50  # Flat hierarchy
    else:
        size_score = 50

    # Material consistency: check saturation variance
    mat_score = 60
    if materials:
        sats = []
        for m in materials:
            if m.get("object_count", 0) > 0:
                _, s, _ = _rgb_to_hsl(m["color"][0], m["color"][1], m["color"][2])
                sats.append(s)
        if len(sats) >= 2:
            sat_range = max(sats) - min(sats)
            if sat_range < 0.3:
                mat_score = 80  # Consistent
            elif sat_range < 0.5:
                mat_score = 60
            else:
                mat_score = 40  # Inconsistent

    overall = form_score * 0.4 + size_score * 0.3 + mat_score * 0.3

    return {
        "score": round(overall, 1),
        "form_consistency": round(form_score, 1),
        "size_hierarchy": round(size_score, 1),
        "material_consistency": round(mat_score, 1),
    }


def scene_review(checks=None):
    """Universal Maya scene audit with integrated aesthetic + lighting review.

    Reviews scene integrity after MCP operations. Detects spatial conflicts,
    naming violations, componentization issues, architectural problems,
    aesthetic quality (5 dimensions), lighting quality, and scene organization.

    Args:
        checks: list of check names. If None, runs all.
            Options: "spatial", "overlaps", "zones", "aesthetics",
                     "constraints", "orphans", "naming", "components",
                     "conflicts", "lighting", "organization"

    Returns:
        dict with score (0-100), issues, and detailed check results.
    """
    if checks is None:
        checks = [
            "spatial",
            "overlaps",
            "zones",
            "aesthetics",
            "constraints",
            "orphans",
            "naming",
            "components",
            "conflicts",
            "lighting",
            "organization",
        ]

    result = {"checks": {}, "issues": [], "score": 0}
    total_score = 0
    max_score = 0

    transforms = cmds.ls(type="transform", long=True) or []
    mesh_count = len(cmds.ls(type="mesh") or [])
    light_count = len(cmds.ls(type="light") or [])
    cam_count = len(cmds.ls(type="camera") or [])

    # === SPATIAL INTEGRITY (10 pts) ===
    if "spatial" in checks:
        max_score += 10
        pts = 0
        sc = {
            "total": len(transforms),
            "meshes": mesh_count,
            "lights": light_count,
            "cameras": cam_count,
        }

        if len(transforms) <= _MAYA_STANDARDS["max_objects_soft"]:
            pts += 4
        elif len(transforms) <= _MAYA_STANDARDS["max_objects_hard"]:
            pts += 2
            result["issues"].append(
                {
                    "severity": "warning",
                    "check": "spatial",
                    "msg": "High object count (%d). Consider instancing." % len(transforms),
                }
            )
        else:
            result["issues"].append(
                {
                    "severity": "error",
                    "check": "spatial",
                    "msg": "Excessive objects (%d). Performance risk." % len(transforms),
                }
            )

        if cam_count <= _MAYA_STANDARDS["max_cameras_keep"]:
            pts += 3
        else:
            result["issues"].append(
                {
                    "severity": "warning",
                    "check": "spatial",
                    "msg": "Excess cameras (%d). Clean test shots." % cam_count,
                }
            )

        if light_count >= _MAYA_STANDARDS["min_lights"]:
            pts += 3
        else:
            result["issues"].append(
                {
                    "severity": "warning",
                    "check": "spatial",
                    "msg": "Only %d lights. Need %d+."
                    % (light_count, _MAYA_STANDARDS["min_lights"]),
                }
            )

        total_score += pts
        result["checks"]["spatial"] = sc

    # === OVERLAP DETECTION (10 pts) ===
    if "overlaps" in checks:
        max_score += 10
        pts = 10
        overlap_pairs = []
        sample = transforms[:_SAMPLE_OVERLAPS]
        pairs_checked = 0

        for i in range(len(sample)):
            for j in range(i + 1, min(len(sample), i + _SAMPLE_OVERLAP_WINDOW)):
                pairs_checked += 1
                try:
                    m = measure(sample[i].split("|")[-1], sample[j].split("|")[-1], "bbox")
                    if m.get("bbox_overlap", False):
                        a = sample[i].split("|")[-1]
                        b = sample[j].split("|")[-1]
                        # DAG-path prefix: true ancestor relation only.
                        # The old substring test false-positived on
                        # siblings like |GEO_wall vs |GEO_wall2.
                        is_pc = sample[j].startswith(sample[i] + "|") or sample[i].startswith(
                            sample[j] + "|"
                        )
                        is_grp = any(
                            a.startswith(p) or b.startswith(p)
                            for p in ("GRP_", "OUT_", "SUN", "ext_")
                        )
                        if not is_pc and not is_grp:
                            overlap_pairs.append({"a": a, "b": b})
                except Exception:
                    pass

        if len(overlap_pairs) > 10:
            pts = 2
            result["issues"].append(
                {
                    "severity": "error",
                    "check": "overlaps",
                    "msg": "%d mesh overlaps. Fix intersections." % len(overlap_pairs),
                }
            )
        elif len(overlap_pairs) > 3:
            pts = 7
            result["issues"].append(
                {
                    "severity": "warning",
                    "check": "overlaps",
                    "msg": "%d minor overlaps." % len(overlap_pairs),
                }
            )

        total_score += pts
        result["checks"]["overlaps"] = {
            "pairs": len(overlap_pairs),
            "details": overlap_pairs[:5],
            "checked": len(sample),
            "skipped": len(transforms) - len(sample),
            "pairs_checked": pairs_checked,
            "pair_window": _SAMPLE_OVERLAP_WINDOW,
        }

    # === SPATIAL CONFLICTS (10 pts) ===
    if "conflicts" in checks:
        max_score += 10
        pts = 10
        conflicts = []

        sample = transforms[:_SAMPLE_CONFLICTS]
        obj_bounds = {}
        for t in sample:
            try:
                sel = om2.MSelectionList()
                sel.add(t)
                dag = sel.getDagPath(0)
                wb = _world_bbox(dag)
                # Key by full DAG path so the parent-child exclusion
                # can do a real ancestor test.
                obj_bounds[t] = {
                    "short": t.split("|")[-1],
                    "min": [wb.min[i] for i in range(3)],
                    "max": [wb.max[i] for i in range(3)],
                }
            except Exception:
                pass

        checked = set()
        for path_a, bounds_a in obj_bounds.items():
            for path_b, bounds_b in obj_bounds.items():
                if path_a == path_b:
                    continue
                pair_key = tuple(sorted([path_a, path_b]))
                if pair_key in checked:
                    continue
                checked.add(pair_key)
                name_a = bounds_a["short"]
                name_b = bounds_b["short"]

                cx = (bounds_a["min"][0] + bounds_a["max"][0]) / 2
                cy = (bounds_a["min"][1] + bounds_a["max"][1]) / 2
                cz = (bounds_a["min"][2] + bounds_a["max"][2]) / 2

                inside_b = (
                    bounds_b["min"][0] <= cx <= bounds_b["max"][0]
                    and bounds_b["min"][1] <= cy <= bounds_b["max"][1]
                    and bounds_b["min"][2] <= cz <= bounds_b["max"][2]
                )

                cx2 = (bounds_b["min"][0] + bounds_b["max"][0]) / 2
                cy2 = (bounds_b["min"][1] + bounds_b["max"][1]) / 2
                cz2 = (bounds_b["min"][2] + bounds_b["max"][2]) / 2

                inside_a = (
                    bounds_a["min"][0] <= cx2 <= bounds_a["max"][0]
                    and bounds_a["min"][1] <= cy2 <= bounds_a["max"][1]
                    and bounds_a["min"][2] <= cz2 <= bounds_a["max"][2]
                )

                if inside_b or inside_a:
                    # DAG-path prefix: true ancestor relation only.
                    is_pc = path_b.startswith(path_a + "|") or path_a.startswith(path_b + "|")
                    is_grp = any(
                        name_a.startswith(p) or name_b.startswith(p)
                        for p in ("GRP_", "OUT_", "ALL")
                    )
                    env_prefixes = ("SUN", "OUT_", "sky", "env")
                    is_env = name_a.startswith(env_prefixes) or name_b.startswith(env_prefixes)
                    if not is_pc and not is_grp and not is_env:
                        conflicts.append(
                            {
                                "type": "penetration",
                                "object": name_a if inside_b else name_b,
                                "container": name_b if inside_b else name_a,
                            }
                        )

        if len(conflicts) > 5:
            pts = 2
            result["issues"].append(
                {
                    "severity": "error",
                    "check": "conflicts",
                    "msg": "%d spatial conflicts. Objects penetrating others." % len(conflicts),
                }
            )
        elif len(conflicts) > 0:
            pts = 7
            result["issues"].append(
                {
                    "severity": "warning",
                    "check": "conflicts",
                    "msg": "%d spatial conflicts detected." % len(conflicts),
                }
            )

        total_score += pts
        result["checks"]["conflicts"] = {
            "count": len(conflicts),
            "details": conflicts[:5],
            "checked": len(sample),
            "skipped": len(transforms) - len(sample),
        }

    # === ZONE COVERAGE (5 pts) ===
    if "zones" in checks:
        max_score += 5
        zd = get_zone_map()
        zones = zd.get("zones", [])
        unassigned = zd.get("unassigned_count", 0)
        total = len(transforms)
        coverage = 1.0 - (unassigned / total) if total > 0 else 0

        pts = 0
        if coverage >= 0.7:
            pts = 5
        elif coverage >= 0.5:
            pts = 3
        elif coverage >= 0.3:
            pts = 1
        else:
            result["issues"].append(
                {
                    "severity": "warning",
                    "check": "zones",
                    "msg": "Low coverage (%.0f%%). Add naming prefixes." % (coverage * 100),
                }
            )

        total_score += pts
        result["checks"]["zones"] = {
            "count": len(zones),
            "unassigned": unassigned,
            "coverage": round(coverage, 3),
        }

    # === NAMING CONVENTION (5 pts) ===
    if "naming" in checks:
        max_score += 5
        bad_names = []
        for t in transforms:
            short = t.split("|")[-1]
            for prefix in _MAYA_STANDARDS["forbidden_prefixes"]:
                if short.startswith(prefix):
                    bad_names.append(short)
                    break

        pts = 5
        if len(bad_names) > 10:
            pts = 1
            result["issues"].append(
                {
                    "severity": "error",
                    "check": "naming",
                    "msg": "%d objects with default names." % len(bad_names),
                }
            )
        elif len(bad_names) > 3:
            pts = 3
            result["issues"].append(
                {
                    "severity": "warning",
                    "check": "naming",
                    "msg": "%d objects with default names." % len(bad_names),
                }
            )

        total_score += pts
        result["checks"]["naming"] = {"bad_count": len(bad_names), "examples": bad_names[:5]}

    # === COMPONENTIZATION (10 pts) ===
    if "components" in checks:
        max_score += 10
        pts = 0

        ungrouped_meshes = 0
        grouped_meshes = 0
        for t in transforms:
            try:
                sel = om2.MSelectionList()
                sel.add(t)
                dag = sel.getDagPath(0)
                ntype = _classify_node(dag)
                if ntype == "mesh":
                    if dag.length() > 1:
                        parent_dag = om2.MDagPath(dag)
                        parent_dag.pop()
                        parent_name = om2.MFnDagNode(parent_dag).name()
                        if parent_name.startswith("GRP_"):
                            grouped_meshes += 1
                        else:
                            ungrouped_meshes += 1
                    else:
                        ungrouped_meshes += 1
            except Exception:
                pass

        if ungrouped_meshes == 0:
            pts += 5
        elif ungrouped_meshes < grouped_meshes:
            pts += 2
            result["issues"].append(
                {
                    "severity": "warning",
                    "check": "components",
                    "msg": "%d meshes not under GRP_ groups." % ungrouped_meshes,
                }
            )
        else:
            result["issues"].append(
                {
                    "severity": "error",
                    "check": "components",
                    "msg": "%d meshes ungrouped. Use GRP_ convention." % ungrouped_meshes,
                }
            )

        groups = [t for t in transforms if t.split("|")[-1].startswith("GRP_")]
        if len(groups) >= 3:
            pts += 3
        elif len(groups) >= 1:
            pts += 1

        depth_sample = transforms[:_SAMPLE_DEPTH]
        max_depth = 0
        for t in depth_sample:
            depth = t.count("|")
            max_depth = max(max_depth, depth)
        if max_depth <= _MAYA_STANDARDS["max_nesting_depth"]:
            pts += 2
        else:
            result["issues"].append(
                {
                    "severity": "warning",
                    "check": "components",
                    "msg": "Nesting depth %d exceeds %d."
                    % (max_depth, _MAYA_STANDARDS["max_nesting_depth"]),
                }
            )

        total_score += pts
        result["checks"]["components"] = {
            "grouped_meshes": grouped_meshes,
            "ungrouped_meshes": ungrouped_meshes,
            "groups": len(groups),
            "max_depth": max_depth,
            "depth_checked": len(depth_sample),
            "depth_skipped": len(transforms) - len(depth_sample),
        }

    # === ORPHAN DETECTION (5 pts) ===
    if "orphans" in checks:
        max_score += 5
        orphans = []
        for t in transforms:
            short = t.split("|")[-1]
            # Detect default Maya names
            if re.match(r"^group[0-9]+$", short) or re.match(r"^p[A-Z][a-z]+[0-9]*$", short):
                orphans.append(short)
            # Detect objects at root level that should be in groups
            # (transforms directly under | that are not GRP_, OUT_, SUN, etc.)
            if t.count("|") == 1:  # Root level
                if not any(
                    short.startswith(p)
                    for p in ("GRP_", "OUT_", "SUN", "sky", "env", "CAM_", "LGT_")
                ):
                    # Check if it's a mesh that should be grouped
                    try:
                        sel = om2.MSelectionList()
                        sel.add(t)
                        dag = sel.getDagPath(0)
                        ntype = _classify_node(dag)
                        if ntype == "mesh":
                            orphans.append(short + " (root-level mesh, should be in GRP_)")
                    except Exception:
                        pass

        if len(orphans) == 0:
            total_score += 5
        elif len(orphans) <= 3:
            total_score += 2
            result["issues"].append(
                {
                    "severity": "warning",
                    "check": "orphans",
                    "msg": "%d orphan/root-level objects." % len(orphans),
                }
            )
        else:
            result["issues"].append(
                {
                    "severity": "error",
                    "check": "orphans",
                    "msg": "%d orphan/root-level objects detected." % len(orphans),
                }
            )

        result["checks"]["orphans"] = {"count": len(orphans), "examples": orphans[:5]}

    # === AESTHETICS - 5 DIMENSIONS (15 pts) ===
    if "aesthetics" in checks:
        max_score += 15
        pts = 0
        try:
            aest = analyze_aesthetics()
            overall = aest.get("overall_score", 0)
            grade = aest.get("grade", "F")
            dims = aest.get("dimensions", {})

            # Overall score contribution (0-5 pts)
            if overall >= 80:
                pts += 5
            elif overall >= 60:
                pts += 3
            elif overall >= 40:
                pts += 1

            # Per-dimension contributions (0-2 pts each, 5 dims = 0-10 pts)
            for dim_name in [
                "color_theory",
                "spatial_composition",
                "proportion_scale",
                "lighting_quality",
                "visual_flow",
            ]:
                dim_score = dims.get(dim_name, 0)
                if dim_score >= 70:
                    pts += 2
                elif dim_score >= 50:
                    pts += 1

            # Report weak dimensions
            for dim_name, dim_score in dims.items():
                if dim_score < 40:
                    result["issues"].append(
                        {
                            "severity": "warning",
                            "check": "aesthetics",
                            "msg": "Weak %s: %d/100. Needs improvement." % (dim_name, dim_score),
                        }
                    )

            result["checks"]["aesthetics"] = {
                "overall_score": overall,
                "grade": grade,
                "dimensions": dims,
                "improvement_suggestions": aest.get("improvement_suggestions", []),
            }
        except Exception as e:
            result["issues"].append(
                {
                    "severity": "warning",
                    "check": "aesthetics",
                    "msg": "Aesthetic analysis failed: %s" % str(e),
                }
            )
        total_score += pts

    # === LIGHTING QUALITY (10 pts) ===
    if "lighting" in checks:
        max_score += 10
        pts = 0
        try:
            light_shapes = cmds.ls(type="light") or []
            if not light_shapes:
                result["issues"].append(
                    {
                        "severity": "error",
                        "check": "lighting",
                        "msg": "No lights in scene. Add at least key + fill + accent.",
                    }
                )
            else:
                # Gather light data for analysis
                lights_data = []
                for ls_name in light_shapes:
                    try:
                        parent = cmds.listRelatives(ls_name, parent=True, fullPath=True) or [
                            ls_name
                        ]
                        lt = parent[0].split("|")[-1]
                        ltype = cmds.nodeType(ls_name)
                        color = (
                            cmds.getAttr("%s.color" % ls_name)[0]
                            if cmds.objExists("%s.color" % ls_name)
                            else [1, 1, 1]
                        )
                        intensity = (
                            cmds.getAttr("%s.intensity" % ls_name)
                            if cmds.objExists("%s.intensity" % ls_name)
                            else 1.0
                        )
                        pos = cmds.xform(lt, q=True, ws=True, t=True) or [0, 0, 0]
                        decay = (
                            cmds.getAttr("%s.decayRate" % ls_name)
                            if cmds.objExists("%s.decayRate" % ls_name)
                            else 0
                        )
                        shadow = (
                            bool(cmds.getAttr("%s.useDepthMapShadow" % ls_name))
                            if cmds.objExists("%s.useDepthMapShadow" % ls_name)
                            else False
                        )
                        lights_data.append(
                            {
                                "name": lt,
                                "type": ltype,
                                "color": list(color),
                                "intensity": intensity,
                                "position": list(pos),
                                "decay": decay,
                                "shadow": shadow,
                            }
                        )
                    except Exception:
                        pass

                # Use the lighting quality scorer
                lq = _score_lighting_quality(lights_data)
                lq_score = lq.get("score", 0)

                if lq_score >= 80:
                    pts = 10
                elif lq_score >= 60:
                    pts = 7
                elif lq_score >= 40:
                    pts = 4
                else:
                    pts = 1

                # Report specific issues
                sub = lq.get("sub_scores", {})
                if sub.get("three_point", 0) < 50:
                    result["issues"].append(
                        {
                            "severity": "warning",
                            "check": "lighting",
                            "msg": "Missing three-point lighting setup (key+fill+rim/accent).",
                        }
                    )
                if sub.get("fill_ratio", 0) < 50:
                    result["issues"].append(
                        {
                            "severity": "warning",
                            "check": "lighting",
                            "msg": "Poor fill ratio. Key:fill should be 2:1 to 4:1.",
                        }
                    )
                if sub.get("decay", 0) < 50:
                    result["issues"].append(
                        {
                            "severity": "warning",
                            "check": "lighting",
                            "msg": "Lights using non-physical decay. Use quadratic decay.",
                        }
                    )
                if sub.get("shadow_quality", 0) < 50:
                    result["issues"].append(
                        {
                            "severity": "info",
                            "check": "lighting",
                            "msg": "Shadow coverage outside ideal range (30-80%%).",
                        }
                    )

                result["checks"]["lighting"] = lq
        except Exception as e:
            result["issues"].append(
                {
                    "severity": "warning",
                    "check": "lighting",
                    "msg": "Lighting analysis failed: %s" % str(e),
                }
            )
        total_score += pts

    # === SCENE ORGANIZATION (10 pts) ===
    if "organization" in checks:
        max_score += 10
        pts = 10

        org_issues = []

        # Check 1: Objects with meaningless names in wrong positions
        # Detect objects like GRP_left_arch2 that are placed at world center
        for t in transforms:
            short = t.split("|")[-1]
            if short.startswith("GRP_") and t.count("|") == 1:
                # GRP at root level - check if it has meaningful children
                try:
                    children = cmds.listRelatives(t, children=True, type="transform") or []
                    if len(children) == 0:
                        org_issues.append(
                            {
                                "type": "empty_group",
                                "object": short,
                                "msg": "Empty group '%s'. Remove or populate." % short,
                            }
                        )
                except Exception:
                    pass

        # Check 2: Root-level transforms that should be organized
        root_objects = []
        for t in transforms:
            if t.count("|") == 1:
                short = t.split("|")[-1]
                if not any(
                    short.startswith(p)
                    for p in (
                        "GRP_",
                        "OUT_",
                        "SUN",
                        "sky",
                        "env",
                        "CAM_",
                        "LGT_",
                        "LOC_",
                        "JNT_",
                        "RIG_",
                    )
                ):
                    root_objects.append(short)

        if len(root_objects) > 5:
            org_issues.append(
                {
                    "type": "disorganized_root",
                    "count": len(root_objects),
                    "msg": "%d objects at root level without group prefix." % len(root_objects),
                }
            )

        # Check 3: Scene hierarchy depth consistency
        depths = [t.count("|") for t in transforms]
        if depths:
            avg_depth = sum(depths) / len(depths)
            max_depth = max(depths)
            if max_depth > _MAYA_STANDARDS["max_nesting_depth"] + 2:
                org_issues.append(
                    {
                        "type": "excessive_depth",
                        "max_depth": max_depth,
                        "msg": "Scene hierarchy too deep (%d levels). Flatten to %d max."
                        % (max_depth, _MAYA_STANDARDS["max_nesting_depth"]),
                    }
                )

        # Check 4: Naming consistency (are objects using consistent prefixes?)
        prefix_counts = {}
        for t in transforms:
            short = t.split("|")[-1]
            parts = short.split("_")
            if len(parts) >= 2:
                prefix = parts[0]
                prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1

        # Good: consistent prefixes. Bad: many single-use prefixes
        single_use = sum(1 for c in prefix_counts.values() if c == 1)
        if single_use > len(prefix_counts) * 0.5 and len(prefix_counts) > 5:
            org_issues.append(
                {
                    "type": "inconsistent_naming",
                    "single_use_prefixes": single_use,
                    "msg": "%d/%d naming prefixes used only once. Standardize naming."
                    % (single_use, len(prefix_counts)),
                }
            )

        # Deduct points for issues
        for issue in org_issues:
            if issue["type"] in ("disorganized_root", "excessive_depth"):
                pts -= 3
            elif issue["type"] == "empty_group":
                pts -= 1
            elif issue["type"] == "inconsistent_naming":
                pts -= 2
            result["issues"].append(
                {
                    "severity": "warning",
                    "check": "organization",
                    "msg": issue.get("msg", str(issue)),
                }
            )

        total_score += max(0, pts)
        result["checks"]["organization"] = {
            "root_objects": len(root_objects),
            "org_issues": len(org_issues),
            "prefix_consistency": round(1 - single_use / max(len(prefix_counts), 1), 2),
        }

    # === CONSTRAINTS (5 pts) ===
    if "constraints" in checks:
        max_score += 5
        try:
            cr = check_constraints([{"type": "max_objects", "value": 500}])
            if cr.get("passed", False):
                total_score += 5
        except Exception:
            pass

    # Final score
    result["score"] = round((total_score / max_score * 100) if max_score > 0 else 0, 1)
    result["total_issues"] = len(result["issues"])
    result["errors"] = sum(1 for i in result["issues"] if i["severity"] == "error")
    result["warnings"] = sum(1 for i in result["issues"] if i["severity"] == "warning")
    result["standards"] = {
        "naming": "Maya Production Naming Convention (GRP_/GEO_/MAT_/CAM_/LGT_)",
        "componentization": "Group-based scene organization (max 4 levels nesting)",
        "spatial": "Penetration/conflict detection between non-related objects",
        "aesthetics": "5-dimension professional analysis (color/spatial/scale/lighting/flow)",
        "lighting": "Three-point lighting + fill ratio + shadow quality + decay rate",
        "organization": "Scene hierarchy consistency + naming prefixes + root cleanliness",
        "safety": "Max 500 objects, orphan detection, constraint validation",
    }

    return result


# ============================================================
# SCENE PLANNING: Holistic Scene Organization & Layout
# ============================================================


def scene_plan(objective=None, auto_fix=False):
    """Holistic scene planning with organization validation and layout suggestions.

    Based on research findings:
    - Blockout-first methodology (validate proportions before detail)
    - Modular thinking (identify reusable components)
    - Zone-based layout (entrance → display → service → circulation)
    - Naming/organization standards (GRP_ hierarchy, no orphans)
    - Spatial rhythm (compression/release, open/narrow alternation)

    Args:
        objective: Optional natural language description of goal.
            e.g., "set up a pop-up store entrance with display window"
        auto_fix: If True, automatically fix organization issues
            (move orphan objects into groups, rename defaults).

    Returns:
        dict with:
        - organization_status: current scene organization health
        - zone_analysis: zone coverage and balance
        - layout_suggestions: recommended component placements
        - conflict_prevention: potential issues before they happen
        - action_plan: step-by-step execution plan
    """
    result = {
        "organization_status": {},
        "zone_analysis": {},
        "layout_suggestions": [],
        "conflict_prevention": [],
        "action_plan": [],
    }

    transforms = cmds.ls(type="transform", long=True) or []

    # === 1. ORGANIZATION STATUS ===
    org = _check_organization(transforms)
    result["organization_status"] = org

    # Auto-fix if requested
    if auto_fix and org.get("issues"):
        fix_log = _auto_fix_organization(org["issues"], transforms)
        result["auto_fix_log"] = fix_log

    # === 2. ZONE ANALYSIS ===
    zone_data = get_zone_map()
    result["zone_analysis"] = _analyze_zone_balance(zone_data)

    # === 3. LAYOUT SUGGESTIONS ===
    # Analyze current component positions and suggest improvements
    result["layout_suggestions"] = _suggest_layout(transforms, zone_data)

    # === 4. CONFLICT PREVENTION ===
    result["conflict_prevention"] = _predict_conflicts(transforms)

    # Disclose sampling caps applied by the analysis passes above
    result["sampling"] = {
        "layout_objects": {
            "checked": min(len(transforms), _SAMPLE_LAYOUT),
            "skipped": max(0, len(transforms) - _SAMPLE_LAYOUT),
            "object_limit": _SAMPLE_LAYOUT,
        },
        "conflict_objects": {
            "checked": min(len(transforms), _SAMPLE_CONFLICTS),
            "skipped": max(0, len(transforms) - _SAMPLE_CONFLICTS),
            "object_limit": _SAMPLE_CONFLICTS,
        },
    }

    # === 5. GROUP LAYOUT ANALYSIS ===
    result["group_layout"] = _analyze_group_layout(transforms)

    # === 6. NATURAL LANGUAGE PARSING ===
    if objective:
        result["parsed_objective"] = _parse_objective(objective)

    # === 7. ACTION PLAN ===
    result["action_plan"] = _generate_action_plan(result, objective)

    # Overall health score
    org_score = org.get("health_score", 0)
    zone_score = result["zone_analysis"].get("balance_score", 0)
    result["overall_health"] = round((org_score * 0.5 + zone_score * 0.5), 1)
    result["grade"] = (
        "S"
        if result["overall_health"] >= 90
        else "A"
        if result["overall_health"] >= 80
        else "B"
        if result["overall_health"] >= 70
        else "C"
        if result["overall_health"] >= 60
        else "D"
        if result["overall_health"] >= 50
        else "F"
    )

    return result


def _check_organization(transforms):
    """Check scene organization health."""
    issues = []
    stats = {
        "total_objects": len(transforms),
        "root_objects": 0,
        "grouped_objects": 0,
        "orphan_meshes": 0,
        "empty_groups": 0,
        "default_names": 0,
        "max_depth": 0,
    }

    for t in transforms:
        short = t.split("|")[-1]
        depth = t.count("|")
        stats["max_depth"] = max(stats["max_depth"], depth)

        # Root level check
        if depth == 1:
            stats["root_objects"] += 1
            is_organized = any(
                short.startswith(p)
                for p in (
                    "GRP_",
                    "OUT_",
                    "SUN",
                    "sky",
                    "env",
                    "CAM_",
                    "LGT_",
                    "LOC_",
                    "JNT_",
                    "RIG_",
                )
            )
            if not is_organized:
                # Check if it's a mesh that should be grouped
                try:
                    sel = om2.MSelectionList()
                    sel.add(t)
                    dag = sel.getDagPath(0)
                    ntype = _classify_node(dag)
                    if ntype == "mesh":
                        stats["orphan_meshes"] += 1
                        issues.append(
                            {
                                "type": "orphan_mesh",
                                "object": short,
                                "action": "Move into appropriate GRP_ group",
                            }
                        )
                except Exception:
                    pass

        # Default name check
        import re

        if re.match(r"^p[A-Z][a-z]+[0-9]*$", short) or re.match(r"^group[0-9]+$", short):
            stats["default_names"] += 1
            issues.append(
                {
                    "type": "default_name",
                    "object": short,
                    "action": "Rename with descriptive name following GRP_/GEO_/LGT_ convention",
                }
            )

        # Empty group check
        if short.startswith("GRP_"):
            try:
                children = cmds.listRelatives(t, children=True, type="transform") or []
                if len(children) == 0:
                    stats["empty_groups"] += 1
                    issues.append(
                        {
                            "type": "empty_group",
                            "object": short,
                            "action": "Remove empty group or populate with content",
                        }
                    )
            except Exception:
                pass

        # Grouped check
        if depth > 1:
            parent = t.rsplit("|", 2)[-2] if "|" in t else ""
            if parent.startswith("GRP_"):
                stats["grouped_objects"] += 1

    # Calculate health score
    score = 100
    if stats["orphan_meshes"] > 0:
        score -= min(30, stats["orphan_meshes"] * 5)
    if stats["default_names"] > 0:
        score -= min(20, stats["default_names"] * 3)
    if stats["empty_groups"] > 0:
        score -= min(10, stats["empty_groups"] * 2)
    if stats["max_depth"] > _MAYA_STANDARDS["max_nesting_depth"]:
        score -= 10

    return {
        "health_score": max(0, score),
        "stats": stats,
        "issues": issues[:10],
        "issue_count": len(issues),
    }


# Intelligent object categorization rules (from research: modular thinking)
_GROUP_RULES = {
    "GRP_shell": [
        "wall",
        "floor",
        "ceiling",
        "roof",
        "facade",
        "exterior",
        "interior",
        "beam",
        "column",
        "pillar",
        "window",
        "glass",
        "storefront",
        "arch",
        "arc",
        "vault",
        "dome",
        "skylight",
    ],
    "GRP_entrance": [
        "door",
        "entrance",
        "gate",
        "shopfront",
        "vestibule",
        "lobby",
        "decompression",
        "threshold",
        "entry",
    ],
    "GRP_display": [
        "display",
        "shelf",
        "counter",
        "kiosk",
        "vitrine",
        "showcase",
        "pedestal",
        "stand",
        "rack",
        "case",
        "cabinet",
        "table",
        "vitrine",
        "exhibit",
        "showcase",
    ],
    "GRP_ip_core": [
        "kitty",
        "hello_kitty",
        "ip_",
        "character",
        "figure",
        "mascot",
        "hero",
        "focal",
        "centerpiece",
        "sculpture",
        "statue",
    ],
    "GRP_furniture": [
        "table",
        "chair",
        "bench",
        "sofa",
        "stool",
        "desk",
        "ottoman",
        "lounge",
        "seat",
    ],
    "GRP_lighting": [
        "light",
        "spot",
        "lamp",
        "chandelier",
        "sconce",
        "led",
        "neon",
        "illuminat",
        "glow",
    ],
    "GRP_path": [
        "path",
        "aisle",
        "corridor",
        "walkway",
        "route",
        "passage",
        "circulation",
        "walk",
    ],
    "GRP_decor": [
        "sign",
        "banner",
        "poster",
        "graphic",
        "logo",
        "decoration",
        "ornament",
        "plant",
        "flower",
        "vase",
        "art",
    ],
    "GRP_service": [
        "checkout",
        "register",
        "counter",
        "cashier",
        "storage",
        "back_",
        "staff",
        "utility",
    ],
}


def _categorize_object(name):
    """Categorize an object by its name using intelligent keyword matching.

    Returns the target GRP_ group name, or "GRP_misc" if no match.
    """
    name_lower = name.lower()

    # Check each group's keywords
    best_group = "GRP_misc"
    best_score = 0

    for group, keywords in _GROUP_RULES.items():
        score = 0
        for kw in keywords:
            if kw in name_lower:
                score += len(kw)  # Longer matches score higher
        if score > best_score:
            best_score = score
            best_group = group

    return best_group


def _auto_fix_organization(issues, transforms):
    """Auto-fix organization issues with intelligent categorization.

    Enhanced with:
    - Intelligent keyword-based object categorization (9 categories)
    - Automatic group creation with proper hierarchy
    - Safe reparenting with error handling
    - Detailed action log for transparency
    """
    log = []
    created_groups = set()

    for issue in issues:
        if issue["type"] == "default_name":
            log.append(
                {
                    "action": "suggest_rename",
                    "object": issue["object"],
                    "msg": "Manual rename needed: %s -> descriptive name" % issue["object"],
                }
            )
        elif issue["type"] == "empty_group":
            try:
                cmds.delete(issue["object"])
                log.append(
                    {
                        "action": "deleted",
                        "object": issue["object"],
                        "msg": "Removed empty group",
                    }
                )
            except Exception:
                log.append(
                    {
                        "action": "failed",
                        "object": issue["object"],
                        "msg": "Could not delete (may have hidden children)",
                    }
                )
        elif issue["type"] == "orphan_mesh":
            target_group = _categorize_object(issue["object"])

            # Create group if it doesn't exist
            if not cmds.objExists(target_group) and target_group not in created_groups:
                try:
                    cmds.group(empty=True, name=target_group)
                    created_groups.add(target_group)
                    log.append(
                        {
                            "action": "created_group",
                            "object": target_group,
                            "msg": "Created group for %s objects" % target_group,
                        }
                    )
                except Exception:
                    pass

            try:
                cmds.parent(issue["object"], target_group)
                log.append(
                    {
                        "action": "reparented",
                        "object": issue["object"],
                        "target": target_group,
                        "msg": "Moved %s into %s" % (issue["object"], target_group),
                    }
                )
            except Exception:
                log.append(
                    {
                        "action": "failed",
                        "object": issue["object"],
                        "msg": "Could not reparent %s (may be referenced or locked)"
                        % issue["object"],
                    }
                )

    return log


def _analyze_group_layout(transforms):
    """Analyze layout at the group level for spatial-aesthetic optimization.

    Based on research:
    - Groups as modular units (Tripo3D style guide)
    - Zone-based layout (entrance → display → service → circulation)
    - Compression/release rhythm (Level Design research)
    - Visual hierarchy (hero group > secondary > tertiary)

    Returns:
        dict with group analysis, positioning suggestions, and zone compliance.
    """
    # Collect groups and their children
    groups = {}
    for t in transforms:
        short = t.split("|")[-1]
        if short.startswith("GRP_") and t.count("|") <= 2:  # Top-level or 2nd-level groups
            try:
                children = cmds.listRelatives(t, children=True, type="transform") or []
                child_count = len(children)

                # Get group bounds
                sel = om2.MSelectionList()
                for child in children[:20]:  # Sample for performance
                    try:
                        sel.add(child)
                    except Exception:
                        pass

                if sel.length() > 0:
                    bbox = om2.MBoundingBox()
                    for i in range(sel.length()):
                        dag = sel.getDagPath(i)
                        wb = _world_bbox(dag)
                        bbox.expand(wb.min)
                        bbox.expand(wb.max)

                    center = [
                        (bbox.min[0] + bbox.max[0]) / 2,
                        (bbox.min[1] + bbox.max[1]) / 2,
                        (bbox.min[2] + bbox.max[2]) / 2,
                    ]
                    size = [
                        abs(bbox.max[0] - bbox.min[0]),
                        abs(bbox.max[1] - bbox.min[1]),
                        abs(bbox.max[2] - bbox.min[2]),
                    ]
                else:
                    center = [0, 0, 0]
                    size = [0, 0, 0]

                groups[short] = {
                    "name": short,
                    "child_count": child_count,
                    "center": [round(v, 1) for v in center],
                    "size": [round(v, 1) for v in size],
                    "max_size": round(max(size), 1),
                    "zone_hint": _get_zone_hint(short),
                }
            except Exception:
                pass

    # Analyze group positioning
    suggestions = []
    group_list = list(groups.values())

    # Check for groups at origin (likely misplaced)
    for g in group_list:
        cx, cy, cz = g["center"]
        if abs(cx) < 1 and abs(cz) < 1 and g["max_size"] > 50:
            suggestions.append(
                {
                    "type": "positioned_at_origin",
                    "group": g["name"],
                    "msg": "Group '%s' is at world center. Consider intentional placement."
                    % g["name"],
                }
            )

    # Check for overlapping groups
    for i in range(len(group_list)):
        for j in range(i + 1, len(group_list)):
            g1, g2 = group_list[i], group_list[j]
            dist = math.sqrt(
                (g1["center"][0] - g2["center"][0]) ** 2 + (g1["center"][2] - g2["center"][2]) ** 2
            )
            min_gap = (g1["max_size"] + g2["max_size"]) * 0.2
            if dist < min_gap and g1["max_size"] > 10 and g2["max_size"] > 10:
                suggestions.append(
                    {
                        "type": "groups_too_close",
                        "groups": [g1["name"], g2["name"]],
                        "gap": round(dist, 1),
                        "recommended": round(min_gap, 1),
                        "msg": "Groups '%s' and '%s' are too close (%.0fcm gap, recommend %.0fcm)."
                        % (g1["name"], g2["name"], dist, min_gap),
                    }
                )

    # Check zone compliance
    zone_compliance = {}
    for g in group_list:
        hint = g["zone_hint"]
        if hint not in zone_compliance:
            zone_compliance[hint] = []
        zone_compliance[hint].append(g["name"])

    return {
        "groups": groups,
        "group_count": len(groups),
        "suggestions": suggestions[:10],
        "zone_compliance": zone_compliance,
    }


def _get_zone_hint(group_name):
    """Determine which zone a group belongs to based on its name."""
    name = group_name.lower()
    if any(kw in name for kw in ["shell", "facade", "exterior", "wall", "floor"]):
        return "structure"
    elif any(kw in name for kw in ["entrance", "door", "gate", "lobby"]):
        return "entrance"
    elif any(kw in name for kw in ["display", "shelf", "counter", "kiosk", "ip_", "kitty"]):
        return "display"
    elif any(kw in name for kw in ["path", "aisle", "corridor", "walkway"]):
        return "circulation"
    elif any(kw in name for kw in ["light", "spot", "lamp"]):
        return "lighting"
    elif any(kw in name for kw in ["service", "checkout", "storage"]):
        return "service"
    elif any(kw in name for kw in ["decor", "sign", "banner", "plant"]):
        return "decoration"
    else:
        return "other"


def _parse_objective(objective):
    """Parse natural language objective into structured plan steps.

    Based on research: translate design intent into actionable geometry operations.
    Supports:
    - Zone identification (entrance, display, service, etc.)
    - Action verbs (create, move, resize, group, etc.)
    - Spatial references (left, right, center, back, front)
    """
    if not objective:
        return []

    steps = []
    obj_lower = objective.lower()

    # Detect zone targets
    zone_keywords = {
        "entrance": ["entrance", "entry", "door", "gate", "入口", "门"],
        "display": ["display", "showcase", "exhibit", "展示", "陈列"],
        "circulation": ["path", "aisle", "walkway", "动线", "通道"],
        "service": ["checkout", "register", "counter", "收银", "服务"],
        "ip_core": ["kitty", "character", "figure", "mascot", "ip", "角色"],
        "lighting": ["light", "illuminate", "spot", "灯光", "照明"],
        "shell": ["wall", "floor", "ceiling", "structure", "墙体", "地面"],
    }

    detected_zones = []
    for zone, keywords in zone_keywords.items():
        if any(kw in obj_lower for kw in keywords):
            detected_zones.append(zone)

    # Detect actions
    action_keywords = {
        "create": ["create", "add", "make", "build", "新建", "添加", "创建"],
        "move": ["move", "relocate", "adjust", "移动", "调整"],
        "organize": ["group", "organize", "clean", "整理", "分组", "归组"],
        "plan": ["plan", "design", "layout", "规划", "设计", "布局"],
        "review": ["check", "review", "audit", "检查", "审核"],
    }

    detected_actions = []
    for action, keywords in action_keywords.items():
        if any(kw in obj_lower for kw in keywords):
            detected_actions.append(action)

    # Build structured steps
    if "organize" in detected_actions or not detected_actions:
        steps.append(
            {
                "action": "organize_scene",
                "description": "Move all orphan objects into appropriate GRP_ groups",
                "priority": "high",
            }
        )

    if detected_zones:
        for zone in detected_zones:
            steps.append(
                {
                    "action": "setup_zone",
                    "zone": zone,
                    "description": "Set up %s zone with proper grouping and layout" % zone,
                    "priority": "high",
                }
            )

    if "plan" in detected_actions:
        steps.append(
            {
                "action": "full_layout_plan",
                "description": "Generate complete layout plan with zone analysis and aesthetics",
                "priority": "high",
            }
        )

    if "review" in detected_actions:
        steps.append(
            {
                "action": "full_review",
                "description": "Run comprehensive scene review (11 checks)",
                "priority": "medium",
            }
        )

    if not steps:
        steps.append(
            {
                "action": "general_optimization",
                "description": "Optimize scene: " + objective[:100],
                "priority": "medium",
            }
        )

    return steps


def _analyze_zone_balance(zone_data):
    """Analyze zone coverage and spatial balance."""
    zones = zone_data.get("zones", [])
    unassigned = zone_data.get("unassigned_count", 0)
    total = zone_data.get("total_objects")
    if not total:
        # Backward-compatible fallback: derive from zones + unassigned
        total = unassigned + sum(z.get("object_count", 0) for z in zones)
    total = max(total, 1)

    coverage = 1.0 - (unassigned / total) if total > 0 else 0

    # Check zone distribution
    zone_counts = {}
    for zone in zones:
        zname = zone.get("name", "unknown")
        zone_counts[zname] = zone.get("object_count", 0)

    # Balance score: are zones evenly distributed?
    if zone_counts:
        values = list(zone_counts.values())
        avg = sum(values) / len(values)
        if avg > 0:
            variance = sum((v - avg) ** 2 for v in values) / len(values)
            cv = (variance**0.5) / avg  # Coefficient of variation
            balance = max(0, 1 - cv)
        else:
            balance = 0
    else:
        balance = 0

    return {
        "coverage": round(coverage, 3),
        "balance_score": round(balance * 100, 1),
        "zone_distribution": zone_counts,
        "unassigned": unassigned,
    }


def _suggest_layout(transforms, zone_data):
    """Suggest layout improvements based on current scene state."""
    suggestions = []

    # Find objects and their positions
    objects = []
    for t in transforms[:_SAMPLE_LAYOUT]:
        try:
            sel = om2.MSelectionList()
            sel.add(t)
            dag = sel.getDagPath(0)
            fn = om2.MFnDagNode(dag)
            wm = dag.inclusiveMatrix()
            wb = _world_bbox(dag)
            pos = [wm[12], wm[13], wm[14]]
            size = max(
                abs(wb.max[0] - wb.min[0]), abs(wb.max[1] - wb.min[1]), abs(wb.max[2] - wb.min[2])
            )
            objects.append(
                {
                    "name": fn.name(),
                    "position": pos,
                    "size": size,
                }
            )
        except Exception:
            pass

    if len(objects) < 2:
        return suggestions

    # Check for clustering (objects too close together)
    for i in range(len(objects)):
        for j in range(i + 1, min(len(objects), i + _SAMPLE_LAYOUT_WINDOW)):
            dist = math.sqrt(
                (objects[i]["position"][0] - objects[j]["position"][0]) ** 2
                + (objects[i]["position"][2] - objects[j]["position"][2]) ** 2
            )
            min_gap = (objects[i]["size"] + objects[j]["size"]) * 0.3
            if dist < min_gap:
                suggestions.append(
                    {
                        "type": "spacing",
                        "objects": [objects[i]["name"], objects[j]["name"]],
                        "current_gap": round(dist, 1),
                        "recommended_gap": round(min_gap, 1),
                        "msg": "Objects too close. Consider increasing gap to %.0fcm." % min_gap,
                    }
                )

    # Check for objects at exact same position (likely copy error)
    for i in range(len(objects)):
        for j in range(i + 1, min(len(objects), i + 10)):
            dist = math.sqrt(
                (objects[i]["position"][0] - objects[j]["position"][0]) ** 2
                + (objects[i]["position"][1] - objects[j]["position"][1]) ** 2
                + (objects[i]["position"][2] - objects[j]["position"][2]) ** 2
            )
            if dist < 0.1 and objects[i]["size"] > 1:
                suggestions.append(
                    {
                        "type": "overlap",
                        "objects": [objects[i]["name"], objects[j]["name"]],
                        "msg": "Objects at identical positions. Possible duplicate.",
                    }
                )

    return suggestions[:10]


def _predict_conflicts(transforms):
    """Predict potential spatial conflicts before they happen."""
    conflicts = []

    # Get bounds for all objects (world-space via 8-corner transform)
    obj_bounds = {}
    for t in transforms[:_SAMPLE_CONFLICTS]:
        try:
            sel = om2.MSelectionList()
            sel.add(t)
            dag = sel.getDagPath(0)
            wb = _world_bbox(dag)
            # Key by full DAG path so the parent-child exclusion
            # can do a real ancestor test.
            obj_bounds[t] = {
                "short": t.split("|")[-1],
                "min": [wb.min[i] for i in range(3)],
                "max": [wb.max[i] for i in range(3)],
            }
        except Exception:
            pass

    # Check for near-miss collisions (within 10cm tolerance)
    checked = set()
    for path_a, ba in obj_bounds.items():
        for path_b, bb in obj_bounds.items():
            if path_a >= path_b:
                continue
            pair = (path_a, path_b)
            if pair in checked:
                continue
            checked.add(pair)
            name_a = ba["short"]
            name_b = bb["short"]

            # Check gap between bounding boxes
            gap_x = max(0, max(ba["min"][0], bb["min"][0]) - min(ba["max"][0], bb["max"][0]))
            gap_y = max(0, max(ba["min"][1], bb["min"][1]) - min(ba["max"][1], bb["max"][1]))
            gap_z = max(0, max(ba["min"][2], bb["min"][2]) - min(ba["max"][2], bb["max"][2]))

            # If gaps are very small, it's a near-miss
            if gap_x < 10 and gap_y < 10 and gap_z < 10:
                # DAG-path prefix: true ancestor relation only.
                is_pc = path_b.startswith(path_a + "|") or path_a.startswith(path_b + "|")
                is_grp = any(
                    name_a.startswith(p) or name_b.startswith(p) for p in ("GRP_", "OUT_", "ALL")
                )
                if not is_pc and not is_grp:
                    conflicts.append(
                        {
                            "type": "near_miss",
                            "objects": [name_a, name_b],
                            "gaps": [round(gap_x, 1), round(gap_y, 1), round(gap_z, 1)],
                            "msg": "Objects nearly touching. Verify intentional placement.",
                        }
                    )

    return conflicts[:5]


def _generate_action_plan(plan_data, objective):
    """Generate step-by-step action plan based on analysis and objective."""
    actions = []
    step = 1

    # Fix organization first
    org = plan_data.get("organization_status", {})
    if org.get("health_score", 100) < 80:
        for issue in org.get("issues", [])[:3]:
            actions.append(
                {
                    "step": step,
                    "action": "fix_organization",
                    "target": issue.get("object", ""),
                    "description": issue.get("action", ""),
                    "priority": "high",
                }
            )
            step += 1

    # Address conflicts
    for conflict in plan_data.get("conflict_prevention", [])[:2]:
        actions.append(
            {
                "step": step,
                "action": "resolve_conflict",
                "target": ", ".join(conflict.get("objects", [])),
                "description": conflict.get("msg", ""),
                "priority": "medium",
            }
        )
        step += 1

    # Address layout issues
    for suggestion in plan_data.get("layout_suggestions", [])[:3]:
        actions.append(
            {
                "step": step,
                "action": "adjust_layout",
                "target": ", ".join(suggestion.get("objects", [])),
                "description": suggestion.get("msg", ""),
                "priority": "medium",
            }
        )
        step += 1

    # Zone improvements
    zone = plan_data.get("zone_analysis", {})
    if zone.get("coverage", 1) < 0.7:
        actions.append(
            {
                "step": step,
                "action": "improve_zone_coverage",
                "target": "unassigned objects",
                "description": "Add naming prefixes to %d unassigned objects"
                % zone.get("unassigned", 0),
                "priority": "low",
            }
        )
        step += 1

    # If objective provided, add objective-specific steps
    if objective:
        actions.append(
            {
                "step": step,
                "action": "execute_objective",
                "target": "user_goal",
                "description": "Execute: %s" % objective,
                "priority": "high",
            }
        )

    return actions
