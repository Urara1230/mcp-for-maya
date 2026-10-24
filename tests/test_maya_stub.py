"""Self-tests for tests/maya_stub — the harness must be right
before product tests can be trusted (D-004 stub tier: maya_scene_module runs
against a fake maya.cmds, not a live session)."""

from __future__ import annotations

import math

import maya_stub
import pytest
from maya_stub.math3d import MBoundingBox, MPoint, MVector, euler_from_matrix, trs_matrix


@pytest.fixture
def scene():
    sc = maya_stub.install()
    yield sc
    maya_stub.uninstall()


class TestMatrixMath:
    def test_translation(self):
        m = trs_matrix(t=(5, 2, -3))
        p = MPoint(1, 1, 1) * m
        assert (p.x, p.y, p.z) == (6, 3, -2)

    def test_rotate_y_90(self):
        # row-vector: +90 deg about Y maps +X -> -Z
        m = trs_matrix(r=(0, 90, 0))
        p = MPoint(1, 0, 0) * m
        assert p.x == pytest.approx(0, abs=1e-9)
        assert p.z == pytest.approx(-1, abs=1e-9)

    def test_scale_then_translate(self):
        m = trs_matrix(t=(10, 0, 0), s=(2, 2, 2))
        p = MPoint(1, 0, 0) * m
        assert p.x == pytest.approx(12)

    def test_euler_roundtrip_compound(self):
        m = trs_matrix(r=(30, -45, 60))
        rx, ry, rz = euler_from_matrix(m)
        assert rx == pytest.approx(30, abs=1e-6)
        assert ry == pytest.approx(-45, abs=1e-6)
        assert rz == pytest.approx(60, abs=1e-6)

    def test_matrix_mul_is_composable(self):
        a = trs_matrix(t=(1, 0, 0))
        b = trs_matrix(t=(0, 5, 0))
        p = MPoint(0, 0, 0) * (a * b)
        assert (p.x, p.y, p.z) == (1, 5, 0)


class TestMVectorSplit:
    """D-056④: real MVector is NOT an MPoint subclass — len==3, no w,
    cross product via ^; MPoint len==4 with w. The stub's MVector(MPoint)
    inheritance made isinstance/len()/w all lie (stub-green, real-red).
    Conversion ctors mirror the real API: MVector(MPoint) drops w,
    MPoint(MVector) sets w=1.0."""

    def test_mvector_is_not_an_mpoint(self):
        mv = MVector(1, 2, 3)
        assert not isinstance(mv, MPoint)
        assert not hasattr(mv, "w")
        assert not hasattr(mv, "distanceTo")

    def test_len_contract(self):
        assert len(MVector(1, 2, 3)) == 3
        assert len(MPoint(1, 2, 3)) == 4

    def test_index_bounds(self):
        mv = MVector(1, 2, 3)
        assert (mv[0], mv[1], mv[2]) == (1.0, 2.0, 3.0)
        with pytest.raises(IndexError):
            _ = mv[3]
        mp = MPoint(1, 2, 3)
        assert mp[3] == 1.0  # w lives at index 3 on a point

    def test_cross_product(self):
        z = MVector(1, 0, 0) ^ MVector(0, 1, 0)
        assert isinstance(z, MVector)
        assert (z.x, z.y, z.z) == (0.0, 0.0, 1.0)

    def test_dot_and_scalar_mul(self):
        assert MVector(1, 0, 0) * MVector(0, 1, 0) == pytest.approx(0.0)
        assert MVector(1, 2, 3) * MVector(4, 5, 6) == pytest.approx(32.0)
        v = MVector(1, 2, 3) * 2.0
        assert isinstance(v, MVector)
        assert (v.x, v.y, v.z) == (2.0, 4.0, 6.0)

    def test_vector_matrix_transform_drops_translation(self):
        """w=0 semantics: rotation+scale apply, translation does NOT,
        and no perspective divide."""
        m = trs_matrix(t=(10, 0, 0), r=(0, 90, 0))
        v = MVector(1, 0, 0) * m
        assert isinstance(v, MVector)
        assert v.x == pytest.approx(0, abs=1e-9)
        assert v.z == pytest.approx(-1, abs=1e-9)

    def test_point_minus_point_is_vector(self):
        d = MPoint(5, 0, 0) - MPoint(1, 0, 0)
        assert isinstance(d, MVector)
        assert (d.x, d.y, d.z) == (4.0, 0.0, 0.0)

    def test_point_minus_vector_is_point(self):
        p = MPoint(5, 0, 0) - MVector(2, 0, 0)
        assert isinstance(p, MPoint)
        assert (p.x, p.y, p.z) == (3.0, 0.0, 0.0)

    def test_conversion_ctors(self):
        mv = MVector(MPoint(1, 2, 3))
        assert isinstance(mv, MVector)
        assert (mv.x, mv.y, mv.z) == (1.0, 2.0, 3.0)
        mp = MPoint(MVector(4, 5, 6))
        assert isinstance(mp, MPoint)
        assert mp.w == 1.0  # real API: MPoint(MVector) pins w to 1.0
        assert (mp.x, mp.y, mp.z) == (4.0, 5.0, 6.0)
        # copy ctors preserve source values
        assert MPoint(MPoint(1, 2, 3, 0.5)).w == 0.5
        assert MVector(MVector(7, 8, 9)).z == 9.0

    def test_point_vector_equality_edge(self):
        """Boundary pin: real API __eq__ is element-wise over xyz —
        MVector(1,2,3) == MPoint(1,2,3) is True (rhs treated as a
        sequence; w is ignored by the vector's comparator). Same
        element-wise semantics as real OpenMaya."""
        assert MVector(1, 2, 3) == MPoint(1, 2, 3)
        assert MPoint(1, 2, 3) == MVector(1, 2, 3)
        assert MVector(1, 2, 3) != MPoint(1, 2, 4)

    def test_vector_methods_surface(self):
        v = MVector(3, 4, 0)
        assert v.length() == pytest.approx(5.0)
        n = v.normal()
        assert isinstance(n, MVector)
        assert n.length() == pytest.approx(1.0)
        assert MVector(1, 0, 0).angle(MVector(0, 1, 0)) == pytest.approx(math.pi / 2)
        assert MVector(1, 0, 0).isParallel(MVector(2, 0, 0))
        assert not MVector(1, 0, 0).isParallel(MVector(0, 1, 0))
        assert MVector(1, 0, 0).isEquivalent(MVector(1 + 1e-7, 0, 0))


class TestSceneGraph:
    def test_hierarchy_world_position(self, scene):
        g = scene.add_transform("GRP_a", t=(10, 0, 0))
        m = scene.add_mesh("GEO_box", t=(0, 0, 5), parent=g)
        assert scene.world_position(m) == (10, 0, 5)

    def test_long_name_and_resolve(self, scene):
        g = scene.add_transform("GRP_a")
        m = scene.add_mesh("GEO_box", parent=g)
        assert scene.long_name(m) == "|GRP_a|GEO_box"
        assert scene.resolve("|GRP_a|GEO_box") is m
        assert scene.resolve("GEO_box") is m

    def test_resolve_missing_raises(self, scene):
        with pytest.raises(RuntimeError):
            scene.resolve("nope")

    def test_duplicate_names_auto_uniquified(self, scene):
        # Maya-style: creating a second node with the same name gets numbered
        scene.add_mesh("GEO_same")
        scene.add_mesh("GEO_same")
        assert scene.resolve("GEO_same1") is not None

    def test_ambiguous_short_name_raises(self, scene):
        # Same short name under different parents is legal in Maya
        g1 = scene.add_transform("GRP_a")
        g2 = scene.add_transform("GRP_b")
        scene.add_node("dup", "transform", parent=g1, exists_ok=True)
        scene.add_node("dup", "transform", parent=g2, exists_ok=True)
        with pytest.raises(RuntimeError):
            scene.resolve("dup")
        # long names disambiguate
        assert scene.resolve("|GRP_a|dup").parent is g1

    def test_world_bbox_rotated_8_corners(self, scene):
        # 2x1x1 box rotated 45 deg about Y: world XZ extent = 3*sqrt(2)/2
        box = scene.add_mesh(
            "GEO_rot", bbox_min=(-1, -0.5, -0.5), bbox_max=(1, 0.5, 0.5), r=(0, 45, 0)
        )
        wb = scene.object_bbox(box)  # local space bbox
        wm = scene.inclusive_matrix(box)
        out = MBoundingBox()
        for c in wb._corners():
            out.expand(c * wm)
        ext = 3 * math.sqrt(2) / 2  # (|x|+|z|) extents of a 2x1 box at 45deg
        assert (out.max.x - out.min.x) == pytest.approx(ext, abs=1e-9)
        assert (out.max.z - out.min.z) == pytest.approx(ext, abs=1e-9)
        assert (out.max.y - out.min.y) == pytest.approx(1.0)

    def test_naive_minmax_transform_is_wrong(self, scene):
        # Corner-at-origin box rotated 45 deg: min*M/max*M shortcut gives a
        # DIFFERENT (wrong) world AABB than the 8-corner transform.
        box = scene.add_mesh("GEO_off", bbox_min=(0, 0, 0), bbox_max=(2, 1, 1), r=(0, 45, 0))
        wb = scene.object_bbox(box)
        wm = scene.inclusive_matrix(box)
        out = MBoundingBox()
        for c in wb._corners():
            out.expand(c * wm)
        naive_min = wb.min * wm
        naive_max = wb.max * wm
        # 8-corner truth: x in [0, 3*sqrt(2)/2], z in [-sqrt(2), sqrt(2)/2]
        assert out.min.x == pytest.approx(0.0, abs=1e-9)
        assert out.max.x == pytest.approx(3 * math.sqrt(2) / 2, abs=1e-9)
        assert out.min.z == pytest.approx(-math.sqrt(2), abs=1e-9)
        assert out.max.z == pytest.approx(math.sqrt(2) / 2, abs=1e-9)
        # naive min*M/max*M reports z=[0, -0.707] — both bounds wrong
        assert not math.isclose(naive_min.z, out.min.z, abs_tol=1e-6)
        assert not math.isclose(naive_max.z, out.max.z, abs_tol=1e-6)


class TestCmdsFacade:
    def test_ls_type_long(self, scene):
        import sys

        cmds = sys.modules["maya.cmds"]
        scene.add_mesh("GEO_a")
        trs = cmds.ls(type="transform", long=True)
        assert "|GEO_a" in trs
        meshes = cmds.ls(type="mesh")
        assert "GEO_aShape" in meshes

    def test_ls_empty_returns_none(self, scene):
        cmds = __import__("maya.cmds", fromlist=["x"])
        assert cmds.ls(type="transform") is None

    def test_ls_glob_patterns(self, scene):
        """Real-Maya glob semantics (live-verified on 2024; probe artifact
        pending docs/evidence intake — D-109): string
        patterns filter by name, and * never crosses the namespace ':'."""
        cmds = __import__("maya.cmds", fromlist=["x"])
        scene.add_mesh("GEO_box")
        scene.add_mesh("v7ref:GEO_ref_cube")
        scene.add_mesh("LGT_key")
        assert set(cmds.ls("GEO_*") or []) == {"GEO_box", "GEO_boxShape"}
        assert cmds.ls("*GEO_ref_cube*") is None  # * does not cross ':'
        assert set(cmds.ls("*:*GEO_ref_cube*") or []) == {
            "v7ref:GEO_ref_cube",
            "v7ref:GEO_ref_cubeShape",
        }
        assert set(cmds.ls("v7ref:*") or []) == {
            "v7ref:GEO_ref_cube",
            "v7ref:GEO_ref_cubeShape",
        }
        assert set(cmds.ls("GEO_*", "LGT_*") or []) == {
            "GEO_box",
            "GEO_boxShape",
            "LGT_key",
            "LGT_keyShape",
        }

    def test_selection_list_wildcard_includes_non_dag(self, scene):
        """Real Maya: sel.add('*') also matches non-DAG nodes, and
        getDagPath on those raises TypeError('item is not a DAG path')
        — the live-2024 crash that killed scene_snapshot."""
        om = __import__("maya.api.OpenMaya", fromlist=["x"])
        scene.add_mesh("GEO_a")
        sel = om.MSelectionList()
        sel.add("*")
        dag = sel.getDagPath(0)  # first entry is a real DAG node
        assert dag.node() is not None
        with pytest.raises(TypeError, match="not a DAG path"):
            sel.getDagPath(sel.length() - 1)  # trailing non-DAG sentinel

    def test_getattr_tuple(self, scene):
        cmds = __import__("maya.cmds", fromlist=["x"])
        scene.add_light("LGT_key", ltype="spotLight")
        assert cmds.getAttr("LGT_keyShape.intensity") == 1.0
        assert cmds.getAttr("LGT_keyShape.color") == [(1.0, 1.0, 1.0)]
        assert cmds.objExists("LGT_keyShape.coneAngle")
        assert not cmds.objExists("LGT_keyShape.bogusAttr")

    def test_camera_and_aim(self, scene):
        cmds = __import__("maya.cmds", fromlist=["x"])
        target = scene.add_mesh("GEO_t", t=(100, 0, 0))
        cam_t, cam_s = cmds.camera(name="CAM_x")
        cmds.move(0, 0, 0, cam_t)
        cmds.aimConstraint(target.name, cam_t, aimVector=[0, 0, -1])
        node = scene.resolve(cam_t)
        # camera looks down -Z; aiming at +X target => ry = -90
        assert node.r[1] == pytest.approx(-90, abs=1e-6)
        assert scene.constraints[-1]["target"] == "GEO_t"

    def test_xform_world_space(self, scene):
        cmds = __import__("maya.cmds", fromlist=["x"])
        g = scene.add_transform("GRP_p", t=(5, 0, 0))
        scene.add_mesh("GEO_c", t=(0, 0, 7), parent=g)
        assert cmds.xform("GEO_c", q=True, ws=True, t=True) == [5, 0, 7]

    def test_rename_uniquifies(self, scene):
        cmds = __import__("maya.cmds", fromlist=["x"])
        scene.add_mesh("GEO_a")
        scene.add_mesh("GEO_b")
        out = cmds.rename("GEO_b", "GEO_a")
        assert out == "GEO_a1"


class TestGuiSurface:
    """D-039: stub GUI surface fidelity - modelPanel camera API and a
    type-disambiguated lookThru with warn/strict anomaly policy."""

    def test_model_panel_camera_roundtrip(self, scene):
        cmds = __import__("maya.cmds", fromlist=["x"])
        scene.setup_gui()
        assert cmds.modelPanel("modelPanel1", q=True, camera=True) == "persp"
        cmds.modelPanel("modelPanel1", e=True, camera="top")
        assert cmds.modelPanel("modelPanel1", q=True, camera=True) == "top"
        assert ("modelPanel1", "top") in scene.model_panel_calls

    def test_model_panel_exists_and_bad_panel(self, scene):
        cmds = __import__("maya.cmds", fromlist=["x"])
        scene.setup_gui()
        assert cmds.modelPanel("modelPanel1", ex=True) is True
        assert cmds.modelPanel("nope", ex=True) is False
        with pytest.raises(RuntimeError):
            cmds.modelPanel("nope", q=True, camera=True)

    def test_model_editor_camera_edit_resolves(self, scene):
        cmds = __import__("maya.cmds", fromlist=["x"])
        scene.setup_gui()
        cmds.modelEditor("modelPanel1", e=True, camera="top")
        assert scene.panels["modelPanel1"]["camera"] == "top"
        with pytest.raises(RuntimeError):
            cmds.modelEditor("modelPanel1", e=True, camera="not_a_node")

    def test_look_thru_type_disambiguation_both_orders(self, scene):
        """Both positional orders work - classified by arg type, like
        the real command (official examples show both)."""
        cmds = __import__("maya.cmds", fromlist=["x"])
        scene.setup_gui()
        cmds.lookThru("top", "modelPanel1")  # object-first
        assert scene.panels["modelPanel1"]["camera"] == "top"
        cmds.lookThru("modelPanel2", "front")  # editor-first
        assert scene.panels["modelPanel2"]["camera"] == "front"
        assert scene.look_thru_calls[-2:] == [
            ("modelPanel1", "top"),
            ("modelPanel2", "front"),
        ]

    def test_look_thru_unclassifiable_warns_in_default_mode(self, scene):
        cmds = __import__("maya.cmds", fromlist=["x"])
        scene.setup_gui()
        cmds.lookThru("modelPanel1", "not_a_camera")
        assert scene.stub_notes, "anomaly must be recorded"
        assert scene.panels["modelPanel1"]["camera"] == "persp"  # unchanged

    def test_look_thru_unclassifiable_raises_in_strict_mode(self, scene):
        cmds = __import__("maya.cmds", fromlist=["x"])
        scene.setup_gui()
        scene.stub_strict = True
        with pytest.raises(RuntimeError):
            cmds.lookThru("modelPanel1", "not_a_camera")
