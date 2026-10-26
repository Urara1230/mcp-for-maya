"""MCP visual tools: GUI-only viewport/render capture.

scene_viewport_snapshot and scene_render_preview close the visual
loop for the ICEV VERIFY step. They live behind a double headless
gate (host-side framed_channel short-circuit + Maya-side capability
check) and return the frozen v1.0 mixed contract
[ImageContent, TextContent(JSON metadata)].

Each tool lazy-injects the _mcp_visual module on first call, per
session, mirroring the _mcp_scene idempotent pattern.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from pathlib import Path
from typing import Any

import mcp.types as mt

from maya_mcp_server.client import exec_module_code, module_call
from maya_mcp_server.pipeline import TOOL_ANNOTATIONS
from maya_mcp_server.security import (
    CaptureEmptyError,
    GuiSessionRequiredError,
    InputValidationError,
    InvalidCaptureError,
)
from maya_mcp_server.types import ResultType


logger = logging.getLogger(__name__)

# Module source path for _mcp_visual
_MODULE_SOURCE = Path(__file__).parent / "visual_module.py"

# Sessions that already carry the injected module
_visual_injected: set[str] = set()

_MAGIC = {"jpeg": b"\xff\xd8\xff", "png": b"\x89PNG\r\n\x1a\n"}


async def _ensure_visual_injected(client: Any, session_key: str | None) -> None:
    """Inject _mcp_visual once per session (mirrors _mcp_scene).

    Never reached on headless sessions: callers gate on framed_channel
    first, so the omui/PySide imports inside the module can never fire
    where they would explode.
    """
    key = session_key or "_default"
    if key in _visual_injected:
        return
    source = _MODULE_SOURCE.read_text(encoding="utf-8")
    await client.write_module("_mcp_visual", source, overwrite=True)
    await client.execute_code("import _mcp_visual", ResultType.NONE)
    _visual_injected.add(key)
    logger.info("Injected _mcp_visual module into session %s", key)


def _require_gui_channel(client: Any) -> None:
    """Host-side headless gate: zero round-trip short-circuit."""
    if not getattr(client, "framed_channel", False):
        raise GuiSessionRequiredError(
            "visual tools require a Maya GUI session (Qt framed channel)",
            suggestion=(
                "connect to a Maya GUI session; headless/native sessions "
                "have no viewport or playblast surface"
            ),
        )


def _validate_format(format: str) -> str:
    fmt = str(format).lower()
    if fmt not in ("jpeg", "png"):
        raise InputValidationError(f"invalid format {format!r}: must be 'jpeg' or 'png'")
    return fmt


def _validate_quality(quality: int) -> int:
    q = int(quality)
    if not 1 <= q <= 100:
        raise InputValidationError(f"invalid quality {quality!r}: must be 1-100")
    return q


def _validate_max_size(max_size: int) -> int:
    m = int(max_size)
    if m < 16:
        raise InputValidationError(f"invalid max_size {max_size!r}: must be >= 16 pixels")
    return m


def _round4(n: int) -> int:
    """Round up to a multiple of 4 - playblast widthHeight constraint."""
    n = int(n)
    if n <= 0:
        raise InputValidationError("width/height must be positive")
    return (n + 3) // 4 * 4


def _check_payload(result: dict[str, Any], fmt: str) -> bytes:
    """Server-side artifact validation: non-empty + magic match.

    Headless playblast empirically writes zero-byte files without
    erroring; the domain module guards too, but the server never
    passes an empty or magic-mismatched payload through as success.
    """
    b64 = result.get("data_b64")
    if not isinstance(b64, str) or not b64:
        raise CaptureEmptyError("visual capture returned no image payload")
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise InvalidCaptureError(f"capture payload is not valid base64: {e}") from e
    if not raw:
        raise CaptureEmptyError(
            "visual capture produced zero bytes",
            suggestion=("headless playblast silently writes empty files; retry on a GUI session"),
        )
    magic = _MAGIC.get(fmt)
    if magic and not raw.startswith(magic):
        raise InvalidCaptureError(f"capture payload magic mismatch for format {fmt!r}")
    return raw


def _mixed_result(result: dict[str, Any], raw: bytes, session_key: str, fmt: str) -> list[Any]:
    """Frozen v1.0 return contract: [ImageContent, TextContent]."""
    meta: dict[str, Any] = {
        "session": session_key,
        "camera": result.get("camera"),
        "width": result.get("width"),
        "height": result.get("height"),
        "format": fmt,
        "bytes": len(raw),
        "panel": result.get("panel"),
    }
    if result.get("source") is not None:
        meta["source"] = result["source"]
    image = mt.ImageContent(
        type="image",
        data=result["data_b64"],
        mime_type=f"image/{fmt}",
        annotations=mt.Annotations(audience=["assistant", "user"]),
    )
    text = mt.TextContent(type="text", text=json.dumps(meta))
    return [image, text]


def register_visual_tools(mcp: Any) -> None:
    """Register GUI-only visual tools on the FastMCP instance.

    Args:
        mcp: FastMCP server instance.
    """

    @mcp.tool(annotations=TOOL_ANNOTATIONS["scene_viewport_snapshot"])  # type: ignore[untyped-decorator]
    async def scene_viewport_snapshot(
        max_size: int = 800,
        format: str = "jpeg",
        quality: int = 80,
        session_key: str | None = None,
    ) -> Any:
        """Capture the active viewport as an image (WYSIWYG).

        GUI sessions only - headless (mayapy/batch/native-channel)
        sessions get a structured gui_session_required error.

        The capture is what the artist sees: viewport HUD, selection
        highlights and ornaments included. That is a feature - the
        agent verifies exactly what the user is looking at. A
        cmds.refresh(force=True) runs first so the frame is current.

        Args:
            max_size: Longest-side pixel cap for the returned image
                (default 800 - keeps base64 well under client image
                token limits).
            format: "jpeg" (default, small) or "png".
                png recommended for wireframe/line-art review.
            quality: JPEG quality 1-100 (ignored for png).
            session_key: Maya session key.

        Returns:
            [ImageContent, TextContent] - the image plus JSON metadata
            {session, camera, panel, width, height, format, bytes}.
        """
        from maya_mcp_server.server import get_session_manager

        manager = get_session_manager()
        client = await manager.get_client(session_key)
        _require_gui_channel(client)

        fmt = _validate_format(format)
        q = _validate_quality(quality)
        m = _validate_max_size(max_size)

        await _ensure_visual_injected(client, session_key)
        result = await exec_module_code(
            client,
            module_call("_mcp_visual", "viewport_snapshot", max_size=m, format=fmt, quality=q),
        )
        if isinstance(result, dict) and "error" in result:
            return json.dumps(result, indent=2)
        raw = _check_payload(result, fmt)
        return _mixed_result(result, raw, client.key, fmt)

    @mcp.tool(annotations=TOOL_ANNOTATIONS["scene_render_preview"])  # type: ignore[untyped-decorator]
    async def scene_render_preview(
        camera: str | None = None,
        width: int = 640,
        height: int = 360,
        max_size: int = 800,
        format: str = "jpeg",
        quality: int = 80,
        session_key: str | None = None,
    ) -> Any:
        """Single-frame playblast preview from a camera (clean, no HUD).

        GUI sessions only - headless sessions get a structured
        gui_session_required error.

        Side-effect disclosures (read_only_hint is honest):
        1. Switching the panel camera via the modelPanel camera flag
           is instantaneous, visible, and NOT undoable in Maya.
        2. Net-zero side effect: the panel camera is restored on every
           path - success or failure - so terminal state equals entry.
        3. The current time may visibly jump during capture; it is
           restored afterwards (playblast timeline quirk).
        4. viewer=False: no playblast window pops up.
        5. On failure the restore is still attempted; a failed restore
           is logged, never raised over the error result.
        6. Only a killed Maya process can skip the restore.

        Args:
            camera: Camera to look through (default: the panel's
                current camera).
            width: Frame width in pixels (default 640). Rounded UP to a
                multiple of 4 server-side (playblast constraint); the
                actual size may also be clamped by the viewport -
                trust the returned metadata, not the request.
            height: Frame height (default 360). Same /4 rule.
            max_size: Longest-side cap for the returned image.
            format: "jpeg" (default) or "png".
                png recommended for wireframe/line-art review.
            quality: JPEG quality 1-100.
            session_key: Maya session key.

        Returns:
            [ImageContent, TextContent] - image plus JSON metadata
            {session, camera, panel, width, height, format, bytes,
            source: "playblast"}.

        Roadmap note: a silent renderer fallback (mayaHardware2) is
        deliberately NOT done in v1.0 - if added it must be opt-in and
        method-labelled, since its output is not viewport state.
        """
        from maya_mcp_server.server import get_session_manager

        manager = get_session_manager()
        client = await manager.get_client(session_key)
        _require_gui_channel(client)

        fmt = _validate_format(format)
        q = _validate_quality(quality)
        m = _validate_max_size(max_size)
        w = _round4(width)
        h = _round4(height)

        await _ensure_visual_injected(client, session_key)
        result = await exec_module_code(
            client,
            module_call(
                "_mcp_visual",
                "render_preview",
                camera=camera,
                width=w,
                height=h,
                max_size=m,
                format=fmt,
                quality=q,
            ),
        )
        if isinstance(result, dict) and "error" in result:
            return json.dumps(result, indent=2)
        raw = _check_payload(result, fmt)
        return _mixed_result(result, raw, client.key, fmt)
