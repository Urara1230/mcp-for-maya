# Testing Tiers

Two test tiers exist. They are deliberately separate.

## Environment floor

| Side | Requirement |
|------|-------------|
| Host (this package) | Python >= 3.10 (`requires-python`) |
| Injected helper (inside Maya) | Maya >= 2023 i.e. Python >= 3.9 — the `ast.unparse` floor is enforced with a friendly refusal at injection time |
| Empirically verified | Maya 2024 GUI (Tier 3) |

Multi-instance note (D-060): each Maya instance binds its own `commandPort` on a distinct port — a second instance on the same port fails to bind. The scanner probes all listening ports; MEL commandPorts are positively identified by the bilingual `eval("1/2")` probe and permanently exempted while they keep LISTENing (upstream issue #1). Bound or exclude probe targets via `MAYA_MCP_INCLUDE_PORTS` / `MAYA_MCP_EXCLUDE_PORTS` (comma-separated ports or `a-b` ranges).

## Tier 1 — stub tests (CI-safe, no Maya needed)

```bash
python -m pytest tests/ -q
```

`tests/maya_stub/` provides semantic fakes for `maya.cmds` and
`maya.api.OpenMaya`: a real DAG scene graph, row-major matrix math,
and correct 8-corner world-bbox transforms. The `maya_env` fixture
(conftest.py) installs the stub into `sys.modules` and imports
`maya_scene_module` bound to it.

The stub is NOT a Maya replacement — it models only the cmds/OpenMaya
surface the scene module uses. Its math is self-verified in
`test_maya_stub.py`; if the stub lies, product tests lie, so keep
`math3d.py` honest (row-vector convention, translation in M[12..14]).

## Tier 2 — real Maya via mayapy (manual, local-only, NEVER in CI)

Run the scene module inside a real Maya interpreter to catch stub drift
and real API behavior differences:

```bash
# Point mayapy at the repo so maya_mcp_server + tests import cleanly
set PYTHONPATH=D:\Aworker\maya\maya-mcp-server\src;D:\Aworker\maya\maya-mcp-server\tests
"C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe" -m pytest tests/ -q -m mayapy
```

Tests marked `@pytest.mark.mayapy` exercise real `maya.cmds`/
`maya.api.OpenMaya` against `maya_scene_module` — they are skipped
unless the `mayapy` marker is explicitly selected. They are the
reference tier: if a stub test and a mayapy test disagree, the stub is
wrong.

The tier auto-initializes `maya.standalone` once per session
(`tests/test_mayapy_smoke.py::_maya_standalone_init`) — bare mayapy
crashes hard on `import maya.api.OpenMaya` without it.

**Prerequisite**: `maya.standalone` must import cleanly under mayapy.
Verified blocked on at least one dev box (Maya 2024, Windows):
`import maya.standalone` raises a native access violation
(0xC0000005) inside module creation and `maya.exe -batch` hangs — the
whole tier is unrunnable there. On such a box, run the Tier-2
substance (checkpoint/rollback roundtrip, reference-edit case,
signature collection) through a live GUI session instead — see
`docs/reference/gui_verify.py` for the reference
implementation. Launch mayapy from PowerShell/cmd, never Git Bash
(MSYS environment makes mayapy SIGSEGV at startup).

Pending: the mayapy/batch run must also cover the `gui_session_required`
headless gate (visual tools must return the structured error, not
crash) — queued until a healthy headless Maya env exists.

### Call-form items booked to mayapy (env-blocked on this box)

Viewport-independent call forms whose real-return shape must be
asserted on a healthy mayapy (skeleton tests in
`tests/test_mayapy_smoke.py` — they run when the tier is available,
skip honestly otherwise). Evidence table:
`docs/visual-callform-matrix.md`.

- `cmds.camera(name=X)` -> `[transform, shape]` list (X1 rename authority)
- `cmds.ls(type='camera')` -> list including `perspShape` shape nodes
- `cmds.file(rename=X)` + `sceneName` query — the rename+prompt form
  (the 0.1.1 hang came from a positional form hitting the prompt dialog)
- `MSelectionList.add('*')` includes non-DAG nodes -> `getDagPath`
  raises `TypeError('item is not a DAG path')` on them
- `cmds.about(batch=True)` -> True under mayapy (the batch gate trigger)

For a quick smoke test of the module inside Maya's Script Editor:

```python
import sys

sys.path.insert(0, r"D:\Aworker\maya\maya-mcp-server\src")
import maya_mcp_server.maya_scene_module as _mcp_scene

_mcp_scene.get_scene_graph()
```

## Tier 3 — live Maya GUI session (`gui` + `human_verify` markers, D-049a)

Two markers split the live-GUI tier by assertability — the HIL layer of
the SIL(stub)/PIL(mayapy)/HIL(live GUI) mapping:

```bash
# machine-checkable items — real asserts against a live session
# (probes MAYA_MCP_GUI_ADDR=host:port, default 127.0.0.1:7001 bootstrap
# commandPort; skips cleanly when no session is up)
python -m pytest tests/ -m gui

# human-eye checklist — tests exist, print manual steps, always skip
# (never fake a green on viewport state)
python -m pytest tests/ -m human_verify -s
```

`gui` covers: framed-channel bootstrap (PySide2/Qt real session),
str `result_type` over the real wire (D-046), viewport_snapshot PNG
contract, render_preview net-zero side effects (panel camera +
currentTime restored), returned-metadata `width/height` matching
reality, and the camera_not_found domain error.

`human_verify` versioned checklist (was a docs-only list): snapshot
orientation (verticalFlip), WYSIWYG HUD/selection match, VP2 non-black
(2025/2026 = partial), render_preview framing vs requested camera,
multi-client ImageContent rendering (non-Devin seats blocked on user
env), modelPanel -camera call form on 2025/2026 (partial — only 2024
verified locally).

### Call-form checklist (D-056②)

Every call form below is verified against its official signature —
the evidence layer is `docs/visual-callform-matrix.md` (single source
of truth); this list is the tier assignment. `pytest -m gui`
machine-asserts the first group on the live wire
(`test_callform_surface_probe`); the asymmetric pure-color pin has its
own test (`test_vp2_pure_color_orientation_and_channels`).

Machine-assertable (gui):

- `cmds.getPanel(withFocus=True)` -> `str|None`; may return a
  non-modelPanel — `objectTypeUI` must confirm `modelEditor` first
- `cmds.getPanel(type='modelPanel')` -> `string[]`
- `cmds.modelPanel(p, q=True, camera=True)` -> `str` camera name;
  `modelPanel(p, e=True, camera=X)` -> None; `modelPanel(p, ex=True)`
  -> bool (D-039)
- `cmds.modelEditor(ed, q=True, camera=True)` -> `str`;
  `modelEditor(ed, q=True, activeView=True)` -> bool
- `cmds.lsUI(editors=True)` -> `string[]`; `objectTypeUI(name)` -> `str`
- `cmds.currentTime(q=True)` -> float; `currentTime(v, edit=True)` -> None
- `cmds.refresh(force=True)` -> None
- `omui.M3dView.active3dView().getRendererName()` -> `'vp2Renderer'`
  under VP2
- `view.readColorBuffer(img, readRGBA=True)` -> RGBA-ordered float
  MImage on 2024 (`isRGBA()` -> True); `writeToFile` does the
  float->byte conversion — no Python pointer read (`MScriptUtil` is
  gone in Maya 2024, `floatPixels()` returns a bare `int` address)
- `hasattr(om.MImage, 'convertPixelFormat')` -> False on 2024 Python
  (C++-only API — evidence print in the VP2 gui test, not a gate)

Human-eye (human_verify skeleton):

- asymmetric pure-color probe (top-left red block) lands top-left AND
  red — the flip + channel-order pin on real VP2; also machine-asserted
  by `test_vp2_pure_color_orientation_and_channels`
- `cmds.playblast(completeFilename=F, frame=[t])` writes F verbatim —
  no frame suffix, no extension rewriting

For ad-hoc Script Editor checks inside Maya:

```python
import sys

sys.path.insert(0, r"D:\Aworker\maya\maya-mcp-server\src")
import maya_mcp_server.visual_module as _mcp_visual
```
