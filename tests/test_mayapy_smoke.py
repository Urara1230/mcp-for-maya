"""Real-Maya smoke tests — mayapy tier only.

Run inside a real Maya interpreter:

    set PYTHONPATH=<repo>/src;<repo>/tests
    mayapy -m pytest tests/ -m mayapy

Under vanilla python (or the stub) these skip — they exist so docs/testing.md
describes a real tier, not a hypothetical one.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.mayapy


@pytest.fixture(scope="session", autouse=True)
def _maya_standalone_init():
    """mayapy hard-crashes (Windows access violation) if maya.api.OpenMaya
    is imported before maya.standalone.initialize() — initialize once per
    tier run. Under vanilla python / the stub the import fails and this
    is a no-op; the real_maya fixture still gates the tests."""
    try:
        import maya.standalone
    except ImportError:
        return
    maya.standalone.initialize()


@pytest.fixture
def real_maya():
    cmds = pytest.importorskip("maya.cmds", reason="requires real Maya (mayapy)")
    f = getattr(cmds, "__file__", "") or ""
    if "maya_stub" in f or "tests" in f.replace("\\", "/"):
        pytest.skip("maya stub is installed — mayapy tier needs a real interpreter")
    return cmds


def test_scene_graph_runs_in_real_maya(real_maya):
    import maya_mcp_server.maya_scene_module as m

    real_maya.polyCube(name="GEO_probe")
    res = m.get_scene_graph("compact")
    assert "stats" in res
    assert res["stats"]["total"] >= 1


def test_measure_roundtrip_in_real_maya(real_maya):
    import maya_mcp_server.maya_scene_module as m

    a = real_maya.polyCube(name="GEO_a")[0]
    b = real_maya.polyCube(name="GEO_b")[0]
    real_maya.move(100, 0, 0, b)
    res = m.measure(a, b, "center")
    assert res["distance"] == pytest.approx(100, abs=0.01)


def test_checkpoint_rollback_reference_edit_roundtrip(real_maya, tmp_path):
    """D-007: checkpoint->rollback roundtrip on a true exportAll snapshot of a
    reference edit. exportAll flattens references into a self-contained
    snapshot; rollback must restore that state and rebind to the original
    scene path (S2). Manual tier — never CI.
    """
    import maya_mcp_server.maya_scene_module as m

    cmds = real_maya

    # referenced file with one cube
    ref_file = tmp_path / "ref_source.ma"
    cmds.polyCube(name="GEO_ref_cube")
    cmds.file(rename=str(ref_file))
    cmds.file(save=True, type="mayaAscii", force=True, prompt=False)
    cmds.file(new=True, force=True, prompt=False)

    # main scene references it, then a reference edit moves the node
    main_file = tmp_path / "main.ma"
    cmds.file(rename=str(main_file))
    cmds.file(str(ref_file), reference=True, namespace="t03ref", prompt=False)
    ref_node = "t03ref:GEO_ref_cube"
    assert cmds.objExists(ref_node)
    cmds.move(5, 0, 0, ref_node)  # the reference edit
    cmds.file(save=True, type="mayaAscii", force=True, prompt=False)

    res = m.save_checkpoint("with_ref")
    assert "error" not in res, res

    # mutate after checkpoint
    cmds.polyCube(name="GEO_after")

    rb = m.rollback_to_checkpoint(res["filename"])
    assert rb["success"] is True, rb
    assert rb["scene_name_after"] == str(main_file) or rb["scene_name_after"].endswith("main.ma")
    assert rb["scene_rebound_to"] is not None
    assert rb["safety_snapshot"] != "skipped_by_user"
    assert not cmds.objExists("GEO_after")
    # flattened reference content must survive the roundtrip.
    # Live-verified glob semantics: * never crosses the namespace
    # colon on real Maya — check both the flattened and the
    # still-namespaced shapes.
    survivors = (cmds.ls("*GEO_ref_cube*") or []) + (cmds.ls("*:*GEO_ref_cube*") or [])
    assert survivors, "referenced node content lost after rollback"


def test_visual_module_batch_gate_in_real_maya(real_maya):
    """D-024/D-025: mayapy is batch mode — the module must import cleanly
    inside a real interpreter and return the gui_session_required domain
    error rather than exploding on missing GUI deps.
    """
    import maya_mcp_server.visual_module as vm

    for fn in (vm.viewport_snapshot, vm.render_preview):
        res = fn()
        assert "error" in res, res
        assert res["error"]["code"] == "gui_session_required", res
        assert "suggestion" in res["error"]


# ---------------------------------------------------------------------------
# D-056② call-form bookings — viewport-independent shapes asserted on a
# healthy mayapy. Env-blocked on the current box; dormant, not claimed.
# ---------------------------------------------------------------------------


def test_callform_camera_returns_transform_shape_list(real_maya):
    """cmds.camera(name=X) -> [transform, shape] — the X1 rename
    authority contract."""
    cam = real_maya.camera(name="CAM_cf_probe")
    try:
        assert isinstance(cam, list) and len(cam) == 2
        assert real_maya.objectType(cam[1]) == "camera"
    finally:
        real_maya.delete("CAM_cf_probe")


def test_callform_ls_camera_includes_shapes(real_maya):
    """cmds.ls(type='camera') returns shape nodes, incl. perspShape."""
    cams = real_maya.ls(type="camera") or []
    assert "perspShape" in cams


def test_callform_file_rename_prompt_form(real_maya, tmp_path):
    """cmds.file(rename=X) + sceneName query — the rename+prompt form
    that hung 0.1.1 (positional form hit the prompt dialog)."""
    target = tmp_path / "cf_scene.ma"
    real_maya.file(rename=str(target))
    name = real_maya.file(q=True, sceneName=True) or ""
    assert name.replace("\\", "/").endswith("cf_scene.ma")


def test_callform_selection_star_pulls_non_dag(real_maya):
    """MSelectionList.add('*') also matches non-DAG nodes on real Maya —
    getDagPath raises TypeError('item is not a DAG path') on them
    (the live-2024 crash that killed scene_snapshot)."""
    om = pytest.importorskip("maya.api.OpenMaya")
    real_maya.polyCube(name="GEO_cf_probe")
    sel = om.MSelectionList()
    sel.add("*")
    hit_non_dag = False
    for i in range(sel.length()):
        try:
            sel.getDagPath(i)
        except TypeError:
            hit_non_dag = True
    assert hit_non_dag, "'*' must pull non-DAG entries on real Maya"


def test_callform_about_batch_is_true(real_maya):
    """Under mayapy the batch gate trigger fires: about(batch=True)."""
    assert real_maya.about(batch=True) is True


def _injection_unit():
    """Exec the real _mcp_scene payload shape in mayapy: monolith source
    + introspection fragment concatenated into one namespace - the same
    bytes the host ships via write_module (D-095). A __future__ slip or
    py-unsafe syntax in the fragment fails here exactly as in Maya."""
    import sys
    import types
    from pathlib import Path

    src_dir = Path(__file__).resolve().parents[1] / "src" / "maya_mcp_server"
    source = (src_dir / "maya_scene_module.py").read_text(encoding="utf-8")
    frag = src_dir / "introspect_module.py"
    if frag.exists():
        source += "\n\n" + frag.read_text(encoding="utf-8")
    mod = types.ModuleType("_mcp_scene")
    mod.__file__ = str(src_dir / "maya_scene_module.py")
    sys.modules.pop("_mcp_scene", None)
    sys.modules["_mcp_scene"] = mod
    exec(compile(source, mod.__file__, "exec"), mod.__dict__)
    return mod


def test_describe_node_in_real_maya(real_maya):
    m = _injection_unit()
    real_maya.polyCube(name="GEO_intro")
    res = m.describe_node("GEO_intro")
    assert res["type"] == "transform"
    names = {a["name"] for a in res["attrs"]}
    assert "translate" in names
    assert all(a["attr_type"] is not None for a in res["attrs"] if a.get("exists")), (
        "every existing attr must carry a real attr_type facet"
    )
    assert isinstance(res["connections"], list)


def test_list_nodes_in_real_maya(real_maya):
    m = _injection_unit()
    real_maya.polyCube(name="GEO_enum")
    res = m.list_nodes(pattern="GEO_*")
    assert res["count"] >= 1
    assert any(n.endswith("GEO_enum") for n in res["nodes"])
    assert res["has_more"] is False
    assert res["next_cursor"] is None
    dg = m.list_nodes(dag_only=False)
    assert dg["total_count"] >= res["total_count"]
