"""Maya-side visual capture module - injected as _mcp_visual.

GUI-only capture paths. The host never injects this module into a
headless session (framed_channel gate), but every public function
still re-checks GUI capability and returns the two-layer error
contract's domain shape {error:{code,message,suggestion}}
for extreme headless-ish GUI environments (missing omui/PySide,
batch mode, no modelPanel).

Public API:
    viewport_snapshot(max_size, format, quality) -> dict
    render_preview(camera, width, height, max_size, format, quality) -> dict

Both return {"data_b64", "camera", "panel", "width", "height",
"format", "bytes"} on success (preview adds "source": "playblast"),
or {"error": {...}} on a domain failure. Exceptions raised here are
transport errors, not domain results.
"""

from __future__ import annotations

import base64
import glob
import importlib
import logging
import os
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import maya.cmds as cmds


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Optional GUI dependencies - defensive import
# ---------------------------------------------------------------------------

_omui: Any = None
_QtGui: Any = None
_QtCore: Any = None
_IMPORT_ERROR: Exception | None = None

try:
    _omui = importlib.import_module("maya.api.OpenMayaUI")
except Exception as exc:  # pragma: no cover - real-Maya only
    _IMPORT_ERROR = exc

# MImage lives in maya.api.OpenMaya on Maya <=2024 and moved to
# maya.api.OpenMayaUI on 2025+ — resolve once, tolerate both layouts.
# (Real-Maya find: the omui-only path crashed on Maya 2024 with
# "module 'maya.api.OpenMayaUI' has no attribute 'MImage'".)
_MImage: Any = None
if _omui is not None:
    _MImage = getattr(_omui, "MImage", None)
if _MImage is None:
    try:
        _MImage = getattr(importlib.import_module("maya.api.OpenMaya"), "MImage", None)
    except Exception:
        _MImage = None

try:
    _QtCore = importlib.import_module("PySide6.QtCore")
    _QtGui = importlib.import_module("PySide6.QtGui")
except Exception:
    try:
        _QtCore = importlib.import_module("PySide2.QtCore")
        _QtGui = importlib.import_module("PySide2.QtGui")
    except Exception as exc:
        if _IMPORT_ERROR is None:
            _IMPORT_ERROR = exc


_GUI_REQUIRED_SUGGESTION = (
    "connect to a Maya GUI session; headless (mayapy/batch) sessions "
    "have no viewport or playblast surface"
)


# ---------------------------------------------------------------------------
# Error helpers + capability gate
# ---------------------------------------------------------------------------


def _err(code: str, message: str, suggestion: str | None = None) -> dict[str, Any]:
    """Domain error shape: {error:{code,message,suggestion?}}."""
    e = {"code": code, "message": str(message)}
    if suggestion:
        e["suggestion"] = suggestion
    return {"error": e}


def _gui_block() -> dict[str, Any] | None:
    """None when GUI capture is possible, else the structured error.

    Maya-side gate of the double headless check: batch
    mode OR no modelPanel -> gui_session_required. Import failure of
    omui/PySide in extreme headless-ish GUI envs maps to the same code.
    """
    if _omui is None or _QtGui is None or _QtCore is None:
        return _err(
            "gui_session_required",
            f"visual capture dependencies failed to import: {_IMPORT_ERROR}",
            _GUI_REQUIRED_SUGGESTION,
        )
    try:
        if cmds.about(batch=True):
            return _err(
                "gui_session_required",
                "Maya is running in batch mode",
                _GUI_REQUIRED_SUGGESTION,
            )
    except Exception:
        pass
    try:
        if not (cmds.getPanel(type="modelPanel") or []):
            return _err(
                "gui_session_required",
                "no modelPanel exists in this session",
                _GUI_REQUIRED_SUGGESTION,
            )
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Viewport state helpers (net-zero side effect machinery)
# ---------------------------------------------------------------------------


def _active_model_panel() -> str | None:
    """Two-level probe for the working model panel.

    1) getPanel(withFocus=True) verified as a modelEditor via objectTypeUI
    2) fallback: first lsUI(editors=True) editor whose activeView is set
    """
    try:
        panel = cmds.getPanel(withFocus=True)
        if panel and cmds.objectTypeUI(panel) == "modelEditor":
            return str(panel)
    except Exception:
        pass
    try:
        for ed in cmds.lsUI(editors=True) or []:
            try:
                if cmds.modelEditor(ed, query=True, activeView=True):
                    return str(ed)
            except Exception:
                continue
    except Exception:
        pass
    return None


@contextmanager
def _camera_switch_restored(panel: str, camera: str | None) -> Iterator[None]:
    """Point *panel* at *camera*, restoring the prior camera afterwards.

    Switching a modelPanel's camera is instantaneous, visible, and NOT
    undoable - so the restore is what makes this net-zero. The finally
    block guards on panel survival; a failed restore is logged, never
    raised over the business result. Camera switching uses the
    unambiguous modelPanel named-argument API.
    """
    prev_camera = None
    if camera:
        try:
            prev_camera = cmds.modelPanel(panel, query=True, camera=True)
        except Exception:
            prev_camera = None
        cmds.modelPanel(panel, edit=True, camera=camera)
    try:
        yield
    finally:
        if camera:
            try:
                if cmds.modelPanel(panel, exists=True):
                    if prev_camera:
                        cmds.modelPanel(panel, edit=True, camera=prev_camera)
                    else:
                        logger.warning(
                            "camera restore skipped for %s: prior camera unreadable",
                            panel,
                        )
            except Exception:
                logger.warning("camera restore failed for panel %s", panel)


# ---------------------------------------------------------------------------
# Encode pipeline: MImage/file -> QImage -> downsample -> bytes
# ---------------------------------------------------------------------------


def _downscale(qimg: Any, max_size: int) -> Any:
    """Fit qimg inside a max_size box, preserving aspect ratio."""
    if max(qimg.width(), qimg.height()) > int(max_size):
        qimg = qimg.scaled(
            int(max_size),
            int(max_size),
            _QtCore.Qt.KeepAspectRatio,
            _QtCore.Qt.SmoothTransformation,
        )
    return qimg


def _encode_qimage(qimg: Any, out_format: str, quality: int) -> tuple[bytes, int, int]:
    """QImage -> encoded bytes via QBuffer. Returns (data, w, h)."""
    buf = _QtCore.QBuffer()
    # PySide6 exposes OpenModeFlag; PySide2 has the flag on QIODevice itself.
    iodev = getattr(_QtCore.QIODevice, "OpenModeFlag", _QtCore.QIODevice)
    buf.open(iodev.WriteOnly)
    if out_format == "png":
        qimg.save(buf, "PNG")
    else:
        qimg.save(buf, "JPEG", int(quality))
    data = bytes(buf.data())
    buf.close()
    return data, qimg.width(), qimg.height()


def _encode_file(path: str, max_size: int, out_format: str, quality: int) -> tuple[bytes, int, int]:
    """Load an image file, downsample, encode. Returns (data, w, h)."""
    qimg = _QtGui.QImage(path)
    if qimg.isNull():
        raise RuntimeError("QImage failed to decode capture output")
    qimg = _downscale(qimg, max_size)
    return _encode_qimage(qimg, out_format, quality)


# Whether the VP2 float readback buffer arrives bottom-up — now a
# FALLBACK DEFAULT, not the arbiter: the per-session runtime
# probe (_probe_vp2_direction) answers first, the MAYA_MCP_VP2_BOTTOM_UP
# env override answers before that, and this constant only covers the
# case where the probe itself could not resolve a direction.
# T-18a pin (Maya 2024.0.0.4640, VP2): readColorBuffer hands back
# TOP-DOWN rows on this box - the GL bottom-up assumption did not hold,
# and the flip put the probe block bottom-left.
_VP2_READBACK_BOTTOM_UP = False

# Per-session probe result cache: None = unprobed (the probe runs on the
# first capture call), True/False = resolved direction. The injected
# module lives exactly as long as the Maya session that probed it.
_VP2_DIRECTION: bool | None = None

# MAYA_MCP_VP2_BOTTOM_UP=1/0 — documented escape hatch for drivers that
# disagree with the probe (README Requirements section).
_VP2_ENV = "MAYA_MCP_VP2_BOTTOM_UP"

_PROBE_NODES = ("_mcp_vp2_cam", "_mcp_vp2_probe", "_mcp_vp2_red", "_mcp_vp2_redSG")


def _vp2_color_image(view: Any) -> Any:
    """VP2 readback -> float MImage, channel order RGBA at the source.

    VP2 requires a kFloat target or readColorBuffer returns all-black
    (official patch path). readColorBuffer's readRGBA flag hands back
    RGBA-ordered floats, so writeToFile's built-in float->byte
    conversion emits a correct PNG with no Python-side pixel copy —
    MScriptUtil was removed in Maya 2024 and the float* pointer-wrap
    path is gone for good (live-verified on 2024.0.0.4640;
    probe artifact pending docs/evidence intake).
    Versions lacking the flag fall back to the default BGRA read;
    writeToFile still honors the image's channel-order marker on write.
    """
    img = _MImage()
    img.create(view.portWidth(), view.portHeight(), 4, _MImage.kFloat)
    try:
        view.readColorBuffer(img, readRGBA=True)
    except TypeError:
        view.readColorBuffer(img)
    return img


# ---------------------------------------------------------------------------
# VP2 readback-direction runtime probe
# ---------------------------------------------------------------------------


def _vp2_direction(view: Any, panel: str | None) -> bool:
    """Resolve the readback row order for this session.

    Priority: MAYA_MCP_VP2_BOTTOM_UP env override > one-shot asymmetric
    pure-color probe (per-session cached) > _VP2_READBACK_BOTTOM_UP
    fallback default. A probe that fails or reads ambiguous resolves to
    the fallback — never flip on a guess.
    """
    global _VP2_DIRECTION
    env = os.environ.get(_VP2_ENV, "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    if _VP2_DIRECTION is None:
        _VP2_DIRECTION = _probe_vp2_direction(view, panel)
        if _VP2_DIRECTION is None:
            _VP2_DIRECTION = _VP2_READBACK_BOTTOM_UP
    return bool(_VP2_DIRECTION)


def _vp2_probe_verdict(img: Any) -> bool | None:
    """Red-block cell test on the probe capture -> True iff bottom-up.

    The marker sits in the probe frame's top-left quarter. A top-down
    readback carries it in the top-left cell; a bottom-up readback
    mirrors it into bottom-left. Same cells + tolerance band as the
    gui-tier anchor (test_gui_session.py VP2 orientation probe).
    Anything else is ambiguous — occlusion, a failed draw, HUD
    collision — and returns None so the caller falls back.
    """
    fd, tmp = tempfile.mkstemp(prefix="_mcp_vp2_", suffix=".png")
    os.close(fd)
    try:
        img.writeToFile(tmp, "png")
        qimg = _QtGui.QImage(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if qimg.isNull():
        return None
    w, h = qimg.width(), qimg.height()

    def _cell_mean(x0: int, y0: int, x1: int, y1: int) -> tuple[float, float, float]:
        rs = gs = bs = n = 0
        for y in range(y0, y1):
            for x in range(x0, x1):
                c = qimg.pixelColor(x, y)
                rs += c.red()
                gs += c.green()
                bs += c.blue()
                n += 1
        return (rs / n, gs / n, bs / n)

    tr, tg, tb = _cell_mean(w // 16, h // 16, w // 8, h // 8)  # inside marker
    br, bg, bb = _cell_mean(w // 16, h * 13 // 16, w // 8, h * 7 // 8)  # mirrored spot
    tl_red = tr > 120 and tr > tg + 40 and tr > tb + 40
    bl_red = br > 120 and br > bg + 40 and br > bb + 40
    if tl_red and not bl_red:
        return False  # marker top-left -> rows arrive top-down
    if bl_red and not tl_red:
        return True  # marker mirrored to bottom-left -> bottom-up
    return None


def _probe_vp2_direction(view: Any, panel: str | None) -> bool | None:
    """Asymmetric pure-color probe -> True iff readback is bottom-up.

    Builds a disposable ortho camera + red surfaceShader cube far below
    the scene (user content stays out of frame), points the active model
    panel at it for one refresh, and checks which vertical half of the
    readback carries the red block. Net-zero, extended to this probe:
    undo recording is suspended WITHOUT flushing the user's queue, and
    selection / panel camera / scene-dirty flag are all restored — every
    created node is deleted on every path. Returns None on any failure
    so the caller falls back rather than flip on a guess.
    """
    if panel is None:
        return None
    prev_dirty = None
    try:
        prev_dirty = cmds.file(query=True, modified=True)
    except Exception:
        prev_dirty = None
    try:
        prev_sel = cmds.ls(selection=True) or []
    except Exception:
        prev_sel = []
    created: list[str] = []
    undo_off = False
    try:
        for n in _PROBE_NODES:  # clear leftovers of a killed probe
            if cmds.objExists(n):
                cmds.delete(n)
        cmds.undoInfo(stateWithoutFlush=False)
        undo_off = True
        cam_tr, cam_shape = cmds.camera(name=_PROBE_NODES[0])
        created.append(cam_tr)
        for attr, val in (
            ("orthographic", True),
            ("orthoWidth", 20.0),
            ("nearClipPlane", 1.0),
            ("farClipPlane", 1000.0),
        ):
            cmds.setAttr(cam_shape + "." + attr, val)
        base_y = -100000.0  # far below any plausible user content
        cmds.setAttr(cam_tr + ".translate", 0.0, base_y, 100.0, type="double3")
        cube = cmds.polyCube(
            width=8.0, height=8.0, depth=8.0, name=_PROBE_NODES[1], constructionHistory=False
        )[0]
        created.append(cube)
        # Default camera looks down -Z with +Y up -> -X/+Y is its
        # top-left; ortho width 20 frames x in [-10,10].
        cmds.move(-5.0, base_y + 3.0, 0.0, cube, absolute=True)
        mat = cmds.shadingNode("surfaceShader", asShader=True, name=_PROBE_NODES[2])
        created.append(mat)
        cmds.setAttr(mat + ".outColor", 1.0, 0.0, 0.0, type="double3")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=_PROBE_NODES[3])
        created.append(sg)
        cmds.connectAttr(mat + ".outColor", sg + ".surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)
        with _camera_switch_restored(panel, cam_tr):
            cmds.refresh(force=True)
            img = _vp2_color_image(view)
        return _vp2_probe_verdict(img)
    except Exception:
        return None
    finally:
        for n in reversed(created):
            try:
                for sh in cmds.listRelatives(n, shapes=True, fullPath=True) or []:
                    if cmds.objExists(sh):
                        cmds.delete(sh)
            except Exception:
                pass
            try:
                if cmds.objExists(n):
                    cmds.delete(n)
            except Exception:
                pass
        if undo_off:
            try:
                cmds.undoInfo(stateWithoutFlush=True)
            except Exception:
                pass
        try:
            if prev_sel:
                cmds.select(prev_sel)
            else:
                cmds.select(clear=True)
        except Exception:
            pass
        try:
            # Repaint the user's real view — the probe frame must not
            # leak into the caller's own readback.
            cmds.refresh(force=True)
        except Exception:
            pass
        if prev_dirty is not None:
            try:
                cmds.file(modified=prev_dirty)
            except Exception:
                logger.warning("vp2 probe: scene modified flag not restored")


# ---------------------------------------------------------------------------
# Public: viewport snapshot (readColorBuffer path)
# ---------------------------------------------------------------------------


def viewport_snapshot(
    max_size: int = 800, format: str = "jpeg", quality: int = 80
) -> dict[str, Any]:
    """Capture the active viewport (WYSIWYG: HUD/selection included).

    VP2 path per the official Autodesk patch: the image must be a
    float buffer or readColorBuffer returns an all-black image.
    """
    blocked = _gui_block()
    if blocked:
        return blocked
    if _MImage is None:
        return _err(
            "capture_unsupported",
            "MImage not found in maya.api.OpenMayaUI or maya.api.OpenMaya",
            "report the Maya version — MImage moved modules in Maya 2025",
        )
    try:
        cmds.refresh(force=True)
    except Exception:
        pass
    try:
        view = _omui.M3dView.active3dView()
        panel = _active_model_panel()
        # Row order is probed per-session at runtime; the env
        # override MAYA_MCP_VP2_BOTTOM_UP and the T-18a constant are the
        # documented fallbacks — the constant no longer arbitrates alone.
        bottom_up = _vp2_direction(view, panel)
        if view.getRendererName() == view.kViewport2Renderer:
            img = _vp2_color_image(view)
        else:
            img = _MImage()
            view.readColorBuffer(img)
        # Same direction answer governs both readback flavors (F4).
        if bottom_up:
            img.verticalFlip()

        fd, tmp = tempfile.mkstemp(prefix="_mcp_visual_", suffix=".png")
        os.close(fd)
        try:
            img.writeToFile(tmp, "png")
            data, w, h = _encode_file(tmp, max_size, format, quality)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

        camera = None
        if panel:
            try:
                camera = cmds.modelEditor(panel, query=True, camera=True)
            except Exception:
                camera = None
        return {
            "data_b64": base64.b64encode(data).decode("ascii"),
            "camera": camera,
            "panel": panel,
            "width": w,
            "height": h,
            "format": format,
            "bytes": len(data),
        }
    except Exception as e:
        return _err("capture_failed", f"viewport snapshot failed: {e}")


# ---------------------------------------------------------------------------
# Public: render preview (playblast path + side-effect discipline)
# ---------------------------------------------------------------------------


def _find_playblast_output(tmp_base: str, expected: str) -> str | None:
    """Resolve the file playblast actually wrote.

    completeFilename is honored verbatim for a single-frame still, but
    Maya may still suffix a frame number on some paths - glob the stem.
    """
    if os.path.exists(expected):
        return expected
    hits = sorted(glob.glob(tmp_base + "*.png"))
    return hits[0] if hits else None


def render_preview(
    camera: str | None = None,
    width: int = 640,
    height: int = 360,
    max_size: int = 800,
    format: str = "jpeg",
    quality: int = 80,
) -> dict[str, Any]:
    """Single-frame playblast from *camera* (or the panel's current camera).

    Net-zero side effect: the panel camera and the timeline are
    restored on every path - success or failure - unless the process is
    killed mid-call. width/height arrive already rounded up to /4 by
    the host (playblast Windows constraint).
    """
    blocked = _gui_block()
    if blocked:
        return blocked
    if camera is not None and not cmds.objExists(camera):
        return _err(
            "camera_not_found",
            f"camera {camera!r} does not exist",
            "pass a camera name from scene_snapshot, or omit camera to capture the current view",
        )
    panel = _active_model_panel()
    if not panel:
        return _err(
            "gui_session_required",
            "no active model panel to playblast",
            _GUI_REQUIRED_SUGGESTION,
        )

    prev_time = cmds.currentTime(query=True)
    tmp_base = os.path.join(tempfile.gettempdir(), f"_mcp_visual_{uuid.uuid4().hex}")
    png_path = tmp_base + ".png"
    try:
        with _camera_switch_restored(panel, camera):
            cmds.playblast(
                frame=int(cmds.currentTime(query=True)),
                format="image",
                compression="png",
                offScreen=True,
                viewer=False,
                showOrnaments=False,
                widthHeight=(int(width), int(height)),
                percent=100,
                forceOverwrite=True,
                editorPanelName=panel,
                completeFilename=png_path,
            )
            produced = _find_playblast_output(tmp_base, png_path)
            # Headless playblast silently writes zero-byte files -
            # never let that masquerade as success.
            if produced is None or os.path.getsize(produced) == 0:
                return _err(
                    "capture_empty",
                    "playblast produced no image data",
                    "retry on a GUI session; batch-mode playblast silently writes empty files",
                )
            data, w, h = _encode_file(produced, max_size, format, quality)

        used_cam = camera
        if used_cam is None:
            try:
                used_cam = cmds.modelEditor(panel, query=True, camera=True)
            except Exception:
                pass
        return {
            "data_b64": base64.b64encode(data).decode("ascii"),
            "camera": used_cam,
            "panel": panel,
            "width": w,
            "height": h,
            "format": format,
            "bytes": len(data),
            "source": "playblast",
        }
    except Exception as e:
        return _err("capture_failed", f"render preview failed: {e}")
    finally:
        # playblast slams the timeline to the start (undo bug #21) -
        # restore regardless of outcome.
        try:
            cmds.currentTime(prev_time, edit=True)
        except Exception:
            pass
        for p in glob.glob(tmp_base + "*"):
            try:
                os.remove(p)
            except OSError:
                pass
