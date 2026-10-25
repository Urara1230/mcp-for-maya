"""_mcp_export - Maya-side scene export.

Injected lazily via write_module on the first scene_export call (same
pattern as _mcp_asset/_mcp_visual, shared ensure_module_injected
helper). Writes scene content to a file on the MAYA host through
cmds.file exportAll/exportSelected - no network surface.

Contract (pinned by a live Maya 2024 probe):
- path: required, normalized (expanduser + abspath), parent dir
  auto-created, existing target is a domain error unless overwrite=True
- format: enum {fbx, obj, usd}; falls back to the path extension;
  missing extension + explicit format appends the extension; an
  explicit format that disagrees with the extension is an error, never
  a guess. Real Maya type= tokens: fbx="FBX export", obj="OBJexport",
  usd="USD Export" (abc/Alembic deliberately out - AbcExport is a
  heterogeneous API, not a cmds.file surface)
- objects=None -> exportAll; a list is objExists-prevalidated (any
  miss aborts the whole export, no partial output) and drives
  exportSelected with the previous selection restored in a finally
- prompt=False is forced on every cmds.file call - a modal dialog can
  hang the command channel (iron rule)
- the scene's modified flag is never touched (export is read-through,
  not a scene mutation)
"""

from __future__ import annotations

import os
import time
from typing import Any

import maya.cmds as cmds


# format -> (cmds.file type= token, plug-in that must be loaded).
_FORMATS: dict[str, tuple[str, str]] = {
    "fbx": ("FBX export", "fbxmaya"),
    "obj": ("OBJexport", "objExport"),
    "usd": ("USD Export", "mayaUsdPlugin"),
}


def _err(code: str, message: str, suggestion: str | None = None) -> dict[str, Any]:
    err = {"code": code, "message": message}
    if suggestion:
        err["suggestion"] = suggestion
    return {"error": err}


def _resolve_format(path: str, format: str | None) -> tuple[str, str] | dict[str, Any]:
    """Resolve the export format and final path.

    Returns (path, format) on success or an error dict. An explicit
    format that disagrees with a present extension errors out - the
    tool never guesses which one the caller meant.
    """
    fmt = str(format).lower() if format is not None else None
    if fmt is not None and fmt not in _FORMATS:
        return _err(
            "invalid_format",
            f"unknown format {format!r}: expected one of {sorted(_FORMATS)}",
        )
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if fmt is not None:
        if ext and ext != fmt:
            return _err(
                "invalid_format",
                f"extension .{ext} conflicts with format={fmt!r}",
                "rename the file or drop the explicit format",
            )
        if not ext:
            path = path + "." + fmt
    elif ext in _FORMATS:
        fmt = ext
    else:
        return _err(
            "invalid_format",
            f"no format given and the path extension does not resolve to one of {sorted(_FORMATS)}",
            "pass format= or use a .fbx/.obj/.usd filename",
        )
    return path, fmt


def export_scene(
    path: str | None = None,
    format: str | None = None,
    objects: list[str] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Export the scene (or named objects) to a host-local file."""
    try:
        if not isinstance(path, str) or not path.strip():
            return _err("invalid_path", "path is required")
        norm = os.path.abspath(os.path.expanduser(path))

        resolved = _resolve_format(norm, format)
        if isinstance(resolved, dict):
            return resolved
        norm, fmt = resolved
        maya_type, plugin = _FORMATS[fmt]

        # --- objects: None exports all; a list is pre-validated ------
        if objects is not None:
            if not isinstance(objects, (list, tuple)) or not all(
                isinstance(o, str) for o in objects
            ):
                return _err(
                    "empty_objects",
                    "objects must be a list of node names; "
                    "pass objects=None to export the whole scene",
                )
            if not objects:
                return _err(
                    "empty_objects",
                    "objects=[] exports nothing; pass objects=None for exportAll",
                )
            missing = [o for o in objects if not cmds.objExists(o)]
            if missing:
                return _err(
                    "missing_objects",
                    "objects not in the scene: " + ", ".join(str(m) for m in missing),
                    "check names via scene_snapshot; nothing was exported",
                )

        # --- target file ---------------------------------------------
        if os.path.exists(norm) and not overwrite:
            return _err(
                "invalid_path",
                f"target exists: {norm}",
                "pass overwrite=True to replace it",
            )
        parent = os.path.dirname(norm)
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as e:
            return _err("invalid_path", f"cannot create parent dir {parent}: {e}")

        # --- plug-in --------------------------------------------------
        warnings: list[str] = []
        try:
            if not cmds.pluginInfo(plugin, query=True, loaded=True):
                cmds.loadPlugin(plugin, quiet=True)
                if cmds.pluginInfo(plugin, query=True, loaded=True):
                    warnings.append(f"auto-loaded plug-in {plugin}")
            if not cmds.pluginInfo(plugin, query=True, loaded=True):
                return _err(
                    "plugin_missing",
                    f"{plugin} plug-in is not loaded and cannot be loaded",
                    f"enable it in Maya's Plug-in Manager ({plugin}.mll)",
                )
        except Exception as e:
            return _err("plugin_missing", f"{plugin} load failed: {e}")

        # --- export ----------------------------------------------------
        t0 = time.monotonic()
        if objects is None:
            cmds.file(norm, exportAll=True, type=maya_type, force=True, prompt=False)
            count = len(cmds.ls(assemblies=True) or [])
        else:
            prev_selection = cmds.ls(selection=True) or []
            try:
                cmds.select(list(objects), replace=True)
                cmds.file(
                    norm,
                    exportSelected=True,
                    type=maya_type,
                    force=True,
                    prompt=False,
                )
            finally:
                if prev_selection:
                    cmds.select(prev_selection, replace=True)
                else:
                    cmds.select(clear=True)
            count = len(objects)
        duration_ms = (time.monotonic() - t0) * 1000.0

        try:
            size_bytes = os.path.getsize(norm)
        except OSError:
            return _err(
                "export_failed",
                f"exporter returned without error but no file exists at {norm}",
            )
        if size_bytes == 0:
            warnings.append("exporter wrote a 0-byte file")
        if objects is None and count == 0:
            warnings.append("scene has no top-level objects; the file may be empty")

        return {
            "path": norm,
            "format": fmt,
            "objects_exported": int(count),
            "size_bytes": int(size_bytes),
            "duration_ms": duration_ms,
            "warnings": warnings,
        }
    except Exception as e:
        return _err("export_failed", f"{type(e).__name__}: {e}")
