"""_mcp_asset - Maya-side asset import.

Injected lazily via write_module on the first asset_import call (same
pattern as _mcp_visual). Receives a validated asset_descriptor of
HOST-local paths - Maya has zero network surface; all downloads already
happened host-side before this module ever runs.

Duties: idempotent fbxmaya plugin load -> FBX import with explicit
file-units (meters) -> group + naming -> polycount gate -> PH texture
auto-wiring (per-part file nodes, sRGB/Raw colorSpace, normal via
bump2d) -> dimensions sanity check -> structured report.
"""

from __future__ import annotations

import os
import re
from typing import Any

import maya.cmds as cmds


# PH texture suffix -> (file colorSpace, wiring role). nor_gl is OpenGL
# format = Maya's expectation, no G flip needed.
_MAP_KINDS = {
    "diff": ("sRGB", "color"),
    "albedo": ("sRGB", "color"),
    "roughness": ("Raw", "roughness"),
    "rough": ("Raw", "roughness"),
    "metallic": ("Raw", "metalness"),
    "metal": ("Raw", "metalness"),
    "nor_gl": ("Raw", "normal"),
    "nor_dx": ("Raw", "normal_dx"),
    "normal": ("Raw", "normal"),
    "ao": ("Raw", "ao"),
    "arm": ("Raw", "ao"),  # packed map: only the AO channel wired for now
    "disp": ("Raw", "displacement"),
    "displacement": ("Raw", "displacement"),
}

_MATERIAL_TYPES = {
    "lambert",
    "blinn",
    "phong",
    "phongE",
    "aiStandardSurface",
    "standardSurface",
    "surfaceShader",
    "StingrayPBS",
}


def _err(code: str, message: str, suggestion: str | None = None) -> dict[str, Any]:
    err = {"code": code, "message": message}
    if suggestion:
        err["suggestion"] = suggestion
    return {"error": err}


def _short(name: str) -> str:
    """Long DAG/DG path -> short name."""
    return str(name).split("|")[-1]


def _delete_if_exists(node: str) -> None:
    """Delete a node iff it still exists; failures are swallowed.

    The single objExists->delete primitive shared by the batch
    stale-DG loop and the half-wired bump2d/displacement rollback paths.
    Zero-condition, one parameter, constant semantics — a future
    different cleanup need (e.g. recursive shape deletion) inlines back
    rather than adding parameters here (Metz wrong-abstraction rule).
    """
    try:
        if cmds.objExists(node):
            cmds.delete(node)
    except Exception:
        pass


def _group_name(asset_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]", "_", str(asset_id))
    return "GRP_asset_" + safe


def _is_new_root(node: str, new_set: set[str]) -> bool:
    """A new DAG node whose parent is not also newly imported.

    DG nodes (materials, file textures, shadingEngines) are not DAG
    members - they must not be grouped (cmds.group would reject them).
    """
    try:
        if not cmds.objectType(node, isAType="dagNode"):
            return False
        par = cmds.listRelatives(node, parent=True, fullPath=True)
    except Exception:
        return False
    if not par:
        return True
    return par[0] not in new_set


def _mesh_transforms(new_nodes: list[str]) -> list[str]:
    out = []
    for n in new_nodes:
        try:
            if cmds.nodeType(n) != "transform":
                continue
            if cmds.listRelatives(n, shapes=True):
                out.append(n)
        except Exception:
            continue
    return out


def _face_count(meshes: list[str]) -> int:
    total = 0
    for m in meshes:
        try:
            total += int(cmds.polyEvaluate(m, face=True) or 0)
        except Exception:
            pass
    return total


def _stale_dg_nodes(grp: str, asset_id: str) -> list[str]:
    """DG nodes an import of this asset would orphan when its group is
    deleted: our file_*/MAT_*/SG_* helpers plus the shading net reached
    through the group's mesh shapes (instObjGroups -> SG -> material).
    """
    stale: set[str] = set()
    for pat in (f"file_{asset_id}_*", f"MAT_{asset_id}_*", f"SG_{asset_id}_*"):
        stale.update(cmds.ls(pat) or [])
    desc = cmds.listRelatives(grp, allDescendents=True, fullPath=True) or []
    for d in desc:
        try:
            if not cmds.objectType(d, isAType="mesh"):
                continue
        except Exception:
            continue
        for sg in cmds.listConnections(d + ".instObjGroups[0]", type="shadingEngine") or []:
            stale.add(sg)
            for src in cmds.listConnections(sg + ".surfaceShader") or []:
                stale.add(src)
    return sorted(stale)


def _delete_group_tree(grp: str, asset_id: str) -> None:
    """Delete the import group AND its shading-network DG nodes.

    cmds.delete(grp) alone leaves file/bump/p2d helpers and imported
    materials/SGs as root-level DG orphans (force re-import and
    polycount rejection both hit this path).
    """
    stale = _stale_dg_nodes(grp, asset_id)
    cmds.delete(grp)
    for n in stale:
        _delete_if_exists(n)


def _imported_materials(new_nodes: list[str]) -> list[str]:
    mats = []
    for n in new_nodes:
        try:
            if cmds.nodeType(n) in _MATERIAL_TYPES:
                mats.append(n)
        except Exception:
            continue
    return mats


def _imported_sgs(new_nodes: list[str]) -> list[str]:
    sgs = []
    for n in new_nodes:
        try:
            if cmds.nodeType(n) == "shadingEngine":
                sgs.append(n)
        except Exception:
            continue
    return sgs


def _match_material(part: str, mats: list[str]) -> list[str]:
    """PH model standard: texture prefix == material name."""
    pl = part.lower()
    exact = [m for m in mats if _short(m).lower() == pl]
    if exact:
        return exact
    return [m for m in mats if pl in _short(m).lower()]


def _new_material(asset_id: str, part: str) -> tuple[str, str]:
    """Dedicated standardSurface + shadingEngine for an unmatched part.

    standardSurface (not blinn) — Maya's VP2/Arnold-default PBR
    shader since 2020; the wiring table below is attribute-aware, so
    legacy imported materials still get their own channel names.
    """
    safe = re.sub(r"[^A-Za-z0-9_]", "_", str(part))
    mat = cmds.shadingNode("standardSurface", asShader=True, name=f"MAT_{asset_id}_{safe}")
    sg = cmds.sets(renderable=True, empty=True, name=f"SG_{asset_id}_{safe}")
    cmds.connectAttr(mat + ".outColor", sg + ".surfaceShader", force=True)
    return mat, sg


def _assign(sg: str, meshes: list[str]) -> list[str]:
    assigned = []
    for m in meshes:
        try:
            shapes = cmds.listRelatives(m, shapes=True, fullPath=True) or [m]
        except Exception:
            shapes = [m]
        for s in shapes:
            try:
                cmds.sets(s, edit=True, forceElement=sg)
                assigned.append(_short(m))
            except Exception:
                pass
    return assigned


def _connect_p2d(file_node: str) -> str | None:
    """Standard place2dTexture boilerplate for a file node."""
    try:
        p2d = cmds.shadingNode("place2dTexture", asUtility=True, name=file_node + "_p2d")
    except Exception:
        return None
    pairs = [
        ("coverage", "coverage"),
        ("translateFrame", "translateFrame"),
        ("rotateFrame", "rotateFrame"),
        ("mirrorU", "mirrorU"),
        ("mirrorV", "mirrorV"),
        ("stagger", "stagger"),
        ("wrapU", "wrapU"),
        ("wrapV", "wrapV"),
        ("repeatUV", "repeatUV"),
        ("offset", "offset"),
        ("rotateUV", "rotateUV"),
        ("noiseUV", "noiseUV"),
        ("vertexUvOne", "vertexUvOne"),
        ("vertexUvTwo", "vertexUvTwo"),
        ("vertexUvThree", "vertexUvThree"),
        ("vertexCameraOne", "vertexCameraOne"),
        ("outUV", "uvCoord"),
        ("outUvFilterSize", "uvFilterSize"),
    ]
    for src, dst in pairs:
        try:
            cmds.connectAttr(p2d + "." + src, file_node + "." + dst, force=True)
        except Exception:
            pass
    return str(p2d)


def _file_node(path: str, name: str, colorspace: str) -> str:
    f = cmds.shadingNode("file", asTexture=True, name=name)
    cmds.setAttr(f + ".fileTextureName", path.replace("\\", "/"), type="string")
    try:
        cmds.setAttr(f + ".colorSpace", colorspace, type="string")
    except Exception:
        pass
    try:
        cmds.setAttr(f + ".ignoreFileRules", 1)  # pin our colorSpace over OCIO rules
    except Exception:
        pass
    _connect_p2d(f)
    return str(f)


def _wire_map(file_node: str, role: str, mat: str, sg: str | None) -> bool:
    """Connect one file node into a material channel. Returns True if wired."""
    if role == "color":
        # standardSurface/aiStandardSurface: baseColor; legacy shaders: color.
        for attr in ("color", "baseColor"):
            if cmds.objExists(mat + "." + attr):
                cmds.connectAttr(file_node + ".outColor", mat + "." + attr, force=True)
                return True
        return False
    if role == "roughness":
        for attr in ("specularRoughness", "roughness"):
            if cmds.objExists(mat + "." + attr):
                cmds.connectAttr(file_node + ".outColorR", mat + "." + attr, force=True)
                return True
        return False
    if role == "metalness":
        # standardSurface/aiStandardSurface: metalness; StingrayPBS: metallic.
        # The old reflectivity fallback is dropped — specular strength is
        # not metalness; an honest unwired beats a confident wrong wire.
        for attr in ("metalness", "metallic"):
            if cmds.objExists(mat + "." + attr):
                cmds.connectAttr(file_node + ".outColorR", mat + "." + attr, force=True)
                return True
        return False
    if role == "normal" or role == "normal_dx":
        if not cmds.objExists(mat + ".normalCamera"):
            return False
        # Canonical network per fbxmaya itself (live-verified on Maya 2024;
        # probe artifact pending docs/evidence intake at the next
        # real-Maya window):
        # file.outAlpha -> bump2d.bumpValue; bump2d.outNormal -> mat.normalCamera;
        # bumpInterp=1 selects tangent-space normals. outColor -> bumpValue is
        # rejected by real Maya (color -> float is not connectable).
        bump = None
        try:
            bump = cmds.shadingNode("bump2d", asUtility=True, name=file_node + "_bump")
            cmds.setAttr(bump + ".bumpInterp", 1)
            cmds.connectAttr(file_node + ".outAlpha", bump + ".bumpValue", force=True)
            cmds.connectAttr(bump + ".outNormal", mat + ".normalCamera", force=True)
            return True
        except Exception:
            # A half-wired bump2d node must not be left
            # orphaned in the scene on failure.
            if bump is not None:
                _delete_if_exists(bump)
            return False
    if role == "ao":
        # AO darkens, so it must drive a multiplier - ambientColor *adds*
        # light (live dogfood finding: feeding AO there washes the model
        # flat white). standardSurface: base (scalar weight over
        # baseColor); legacy shaders: diffuse.
        for attr in ("base", "diffuse"):
            if cmds.objExists(mat + "." + attr):
                cmds.connectAttr(file_node + ".outColorR", mat + "." + attr, force=True)
                return True
        return False
    if role == "displacement":
        if not sg or not cmds.objExists(sg + ".displacementShader"):
            return False
        # disp.displacement -> sg.displacementShader is the live-verified
        # shape (Maya 2024 probe, docs/evidence/probes/probe-displacement.json)
        # —
        # but "connection established" is not render semantics; visual
        # proof stays on the gui/human_verify tier.
        disp = None
        try:
            disp = cmds.shadingNode("displacementShader", asUtility=True, name=file_node + "_disp")
            cmds.connectAttr(file_node + ".outAlpha", disp + ".displacement", force=True)
            cmds.connectAttr(disp + ".displacement", sg + ".displacementShader", force=True)
            return True
        except Exception:
            # A half-wired displacement node must not be left
            # orphaned in the scene on failure.
            if disp is not None:
                _delete_if_exists(disp)
            return False
    return False


def _wire_textures(
    texture_parts: dict[str, Any],
    new_nodes: list[str],
    meshes: list[str],
    asset_id: str,
) -> dict[str, Any]:
    """Wire PH texture parts to imported (or created) materials."""
    mats = _imported_materials(new_nodes)
    sgs = _imported_sgs(new_nodes)
    report = []
    single = len(texture_parts) == 1
    for part, maps in sorted(texture_parts.items()):
        rec: dict[str, Any] = {
            "part": part,
            "materials": [],
            "wired": [],
            "unwired": [],
            "assigned": [],
        }
        targets = _match_material(part, mats)
        sg = None
        if not targets:
            # No imported material matches: build a dedicated one and
            # assign part-named meshes (all meshes when single-part).
            mat, sg = _new_material(asset_id, part)
            targets = [mat]
            sel = meshes if single else [m for m in meshes if part.lower() in _short(m).lower()]
            rec["assigned"] = _assign(sg, sel)
        elif sgs:
            sg = sgs[0]
        # Prefer OpenGL normals when PH ships both gl and dx variants —
        # one bump input per material; dx is redundant for standard flow.
        if "nor_gl" in maps and "nor_dx" in maps:
            rec["unwired"].append(part + "_nor_dx (nor_gl preferred)")
        for suffix, path in sorted(maps.items()):
            if suffix == "nor_dx" and "nor_gl" in maps:
                continue
            kind = _MAP_KINDS.get(suffix)
            if kind is None or not isinstance(path, str) or not os.path.isfile(path):
                rec["unwired"].append(part + "_" + suffix)
                continue
            colorspace, role = kind
            fn = _file_node(path, f"file_{asset_id}_{part}_{suffix}", colorspace)
            for mat in targets:
                ok = _wire_map(fn, role, mat, sg)
                bucket = rec["wired"] if ok else rec["unwired"]
                bucket.append(part + "_" + suffix + "->" + _short(mat))
        rec["materials"] = [_short(m) for m in targets]
        report.append(rec)
    return {"parts": report, "materials_found": [_short(m) for m in mats]}


def _world_bbox(group: str) -> list[float] | None:
    """Union world bbox of a group -> [xmin,ymin,zmin,xmax,ymax,zmax]."""
    try:
        return list(cmds.exactWorldBoundingBox(group))
    except Exception:
        pass
    # Fallback via OpenMaya (kept minimal: no _mcp_scene dependency).
    try:
        import maya.api.OpenMaya as om2  # noqa: N813

        sel = om2.MSelectionList()
        sel.add(group)
        dag = sel.getDagPath(0)
        fn = om2.MFnDagNode(dag)
        b = fn.boundingBox
        m = dag.inclusiveMatrix()
        box = om2.MBoundingBox()
        for x in (b.min.x, b.max.x):
            for y in (b.min.y, b.max.y):
                for z in (b.min.z, b.max.z):
                    box.expand(om2.MPoint(x, y, z) * m)
        return [box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z]
    except Exception:
        return None


def import_asset(
    descriptor: dict[str, Any],
    max_polycount: int = 100000,
    allow_high_polycount: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Import a validated asset_descriptor (host-local paths) into Maya.

    Same-name dedup makes repeat calls idempotent: if GRP_asset_<id>
    exists and force=False, the existing group is reported instead of a
    second import.
    """
    try:
        if not isinstance(descriptor, dict):
            return _err("bad_descriptor", "descriptor must be a dict")
        asset_id = descriptor.get("asset_id") or "asset"
        grp = _group_name(asset_id)

        if cmds.objExists(grp) and not force:
            return {
                "deduped": True,
                "group": grp,
                "asset_id": asset_id,
                "message": "already imported; pass force=True to re-import",
            }
        if cmds.objExists(grp):
            _delete_group_tree(grp, asset_id)

        fbx_path = descriptor.get("fbx_path")
        if not isinstance(fbx_path, str) or not fbx_path:
            return _err("bad_descriptor", "descriptor missing fbx_path")
        if not os.path.isfile(fbx_path):
            return _err(
                "files_missing",
                "FBX not reachable from this Maya host: " + fbx_path,
                "files were downloaded on the MCP host; a remote Maya cannot see them",
            )

        try:
            if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
                cmds.loadPlugin("fbxmaya", quiet=True)
            if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
                return _err(
                    "plugin_unavailable",
                    "fbxmaya plug-in is not loaded and cannot be loaded",
                    "enable it in Maya's Plug-in Manager (fbxmaya.mll)",
                )
        except Exception as e:
            return _err("plugin_unavailable", f"fbxmaya load failed: {e}")

        # FBX importer globals are plug-in state, not per-call flags.
        # ConvertUnitString 'm' = declare the file's units as meters so a
        # cm working-unit scene lands the correct physical size.
        try:
            import maya.mel as mel

            for cmd in (
                "FBXImportMode -v add;",
                "FBXImportAxisConversionEnable -v true;",
                "FBXImportCameras -v false;",
                "FBXImportLights -v false;",
                "FBXImportConvertUnitString m;",
            ):
                try:
                    mel.eval(cmd)  # type: ignore[no-untyped-call]
                except Exception:
                    pass
        except Exception:
            pass

        before = set(cmds.ls(long=True) or [])
        try:
            cmds.file(
                fbx_path,
                i=True,
                type="FBX",
                ignoreVersion=True,
                mergeNamespacesOnClash=True,
                prompt=False,
            )
        except Exception as e:
            return _err(
                "import_failed",
                f"FBX import failed: {e}",
                "some Poly Haven FBX are damaged; try another resolution or asset",
            )
        new_nodes = sorted(set(cmds.ls(long=True) or []) - before)
        if not new_nodes:
            return _err(
                "import_empty",
                "FBX import produced no nodes",
                "corrupt or empty FBX file",
            )

        new_set = set(new_nodes)
        new_roots = [n for n in new_nodes if _is_new_root(n, new_set)]
        if new_roots:
            grp = cmds.group(*new_roots, name=grp)
        else:
            grp = cmds.group(name=grp, empty=True)
        # grouping reparents -> long names change; re-diff so the
        # report/wiring steps see valid paths
        new_nodes = sorted(set(cmds.ls(long=True) or []) - before)

        meshes = _mesh_transforms(new_nodes)
        faces = _face_count(meshes)
        if faces > int(max_polycount) and not allow_high_polycount:
            try:
                _delete_group_tree(grp, asset_id)
            except Exception:
                pass
            return _err(
                "polycount_exceeded",
                f"{faces} faces exceeds the {int(max_polycount)} default limit",
                "pass allow_high_polycount=True to keep the import (decision is audited)",
            )

        wiring = _wire_textures(descriptor.get("texture_parts") or {}, new_nodes, meshes, asset_id)

        bbox = _world_bbox(grp)
        dims = []
        if bbox:
            dims = [
                round(bbox[3] - bbox[0], 3),
                round(bbox[4] - bbox[1], 3),
                round(bbox[5] - bbox[2], 3),
            ]
        unit = cmds.currentUnit(query=True, linear=True)
        warnings = []
        mx = max(dims) if dims else 0.0
        if dims and mx < 0.5:
            warnings.append(
                f"suspicious_tiny: max dim {mx}{unit} - "
                "possible unit mismatch (asset authored in meters)"
            )
        if mx > 100000:
            warnings.append(f"suspicious_huge: max dim {mx}{unit} - check scale")

        return {
            "asset_id": asset_id,
            "group": grp,
            "nodes": len(new_nodes),
            "meshes": len(meshes),
            "polycount": faces,
            "texture_wiring": wiring,
            "bbox": {"min": bbox[:3], "max": bbox[3:]} if bbox else None,
            "dims": dims,
            "unit": unit,
            "license": descriptor.get("license", "CC0-1.0"),
            "source": descriptor.get("source"),
            "warnings": warnings,
        }
    except Exception as e:
        return _err("import_failed", f"{type(e).__name__}: {e}")
