"""T-03 regression tests: checkpoint = exportAll memory snapshot; rollback = S2 rebind.

Pins the true-snapshot checkpoint/rollback semantics (D-015: exportAll +
explicit adopt, never references) against the stub. The stub persists
exportAll snapshots as real files (//Maya ASCII header + JSON payload)
and restores scene state on open, so these tests exercise real disk IO.

Required hidden cases (D-015):
  1. checkpoints path is a file / not writable -> precheck + error
  2. same-name checkpoint -> default reject; overwrite=True preserves old via rename
  3. snapshot externally deleted/corrupted -> exists + //Maya ASCII header check -> "snapshot lost"
  4. rollback open failure -> structured scene_name_after + suggestion to rebuild via scene_snapshot
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def saved_scene(maya_env, tmp_path):
    """Scene bound to a (possibly not-yet-written) file under tmp_path."""
    maya_env.scene.scene_path = str(tmp_path / "shot.ma")
    return maya_env


@pytest.fixture
def untitled_scene(maya_env, tmp_path):
    """Never-saved scene; workspace points at tmp_path/ws."""
    maya_env.scene.scene_path = ""
    ws = tmp_path / "ws"
    ws.mkdir()
    maya_env.scene.workspace_dir = str(ws)
    return maya_env


# ------------------------------------------------------------
# checkpoint = real exportAll snapshot of MEMORY state (P0-3)
# ------------------------------------------------------------


class TestCheckpointSnapshot:
    def test_snapshot_captures_memory_not_stale_disk(self, saved_scene, tmp_path):
        # Disk file is stale (does not contain GEO_new); memory does.
        (tmp_path / "shot.ma").write_text("//Maya ASCII 2024 scene\nstale\n")
        saved_scene.scene.add_mesh("GEO_new")
        res = saved_scene.module.save_checkpoint("before_move")
        assert "error" not in res, res
        cp = tmp_path / "checkpoints" / res["filename"]
        assert cp.exists(), "exportAll must write a real file"
        text = cp.read_text()
        assert "//Maya ASCII" in text
        assert "GEO_new" in text, "snapshot must contain memory state, not stale disk copy"

    def test_snapshot_when_scene_file_missing_on_disk(self, saved_scene, tmp_path):
        # Scene named but never written: copy2-on-disk implementations fail
        # here; exportAll succeeds because it snapshots memory.
        assert not (tmp_path / "shot.ma").exists()
        saved_scene.scene.add_mesh("GEO_a")
        res = saved_scene.module.save_checkpoint("cp1")
        assert "error" not in res, res

    def test_checkpoint_structured_fields(self, saved_scene, tmp_path):
        res = saved_scene.module.save_checkpoint("v1")
        assert res["filename"] == "cp_v1.ma"
        assert res["original_file_status"] == "saved"
        assert res["adhoc"] is False
        assert res["scene_rebound_to"] is None
        assert res["path"].endswith(os.path.join("checkpoints", "cp_v1.ma"))

    def test_name_whitelist_rejects(self, saved_scene):
        res = saved_scene.module.save_checkpoint("a/b")
        assert res["error"]["code"] == "invalid_name"
        assert res["error"]["suggestion"] == "a_b"

    def test_name_suggestion_not_auto_applied(self, saved_scene, tmp_path):
        res = saved_scene.module.save_checkpoint("a/b")
        assert "error" in res
        cp_dir = tmp_path / "checkpoints"
        assert not cp_dir.exists() or not any(cp_dir.iterdir()), (
            "suggestion must not be silently used"
        )

    def test_name_whitelist_edge_chars(self, saved_scene):
        for bad in ["a b", "a.b", "a\\b", "a:b", "a/b", "../x", ""]:
            res = saved_scene.module.save_checkpoint(bad)
            assert "error" in res, bad
        for good in ["v1", "a-b_c", "ABC123"]:
            res = saved_scene.module.save_checkpoint(good)
            assert "error" not in res, good

    def test_same_name_rejected_by_default(self, saved_scene):
        assert "error" not in saved_scene.module.save_checkpoint("x")
        res = saved_scene.module.save_checkpoint("x")
        assert res["error"]["code"] == "checkpoint_exists"
        assert res.get("existing") == "cp_x.ma"

    def test_overwrite_preserves_old_file(self, saved_scene, tmp_path):
        saved_scene.scene.add_mesh("GEO_v1")
        assert "error" not in saved_scene.module.save_checkpoint("x")
        saved_scene.scene.add_mesh("GEO_v2")
        res = saved_scene.module.save_checkpoint("x", overwrite=True)
        assert "error" not in res, res
        assert res["preserved_as"].startswith("prev_")
        cp_dir = tmp_path / "checkpoints"
        assert (cp_dir / res["preserved_as"]).exists()
        prev_text = (cp_dir / res["preserved_as"]).read_text()
        assert "GEO_v1" in prev_text and "GEO_v2" not in prev_text
        new_text = (cp_dir / "cp_x.ma").read_text()
        assert "GEO_v2" in new_text

    def test_checkpoints_path_is_file(self, saved_scene, tmp_path):
        (tmp_path / "checkpoints").write_text("not a dir")
        res = saved_scene.module.save_checkpoint("x")
        assert res["error"]["code"] == "path_not_directory"

    def test_checkpoints_dir_unwritable(self, saved_scene, monkeypatch):
        monkeypatch.setattr(os, "access", lambda p, m: False)
        res = saved_scene.module.save_checkpoint("x")
        assert res["error"]["code"] == "dir_not_writable"

    def test_untitled_default_errors(self, untitled_scene):
        res = untitled_scene.module.save_checkpoint()
        assert res["error"]["code"] == "untitled_scene"
        # error text must be a self-healing instruction
        err_l = res["error"]["message"].lower()
        assert "name" in err_l or "ad-hoc" in err_l or "adhoc" in err_l

    def test_untitled_adhoc_snapshot(self, untitled_scene, tmp_path):
        untitled_scene.scene.add_mesh("GEO_rescue")
        res = untitled_scene.module.save_checkpoint("rescue")
        assert "error" not in res, res
        assert res["filename"] == "cp_adhoc_rescue.ma"
        assert res["original_file_status"] == "no_original_file"
        assert res["adhoc"] is True
        assert res["scene_rebound_to"] is None
        assert (tmp_path / "ws" / "checkpoints" / "cp_adhoc_rescue.ma").exists()

    def test_file_ops_use_prompt_false(self, saved_scene):
        saved_scene.scene.file_calls.clear()
        saved_scene.module.save_checkpoint("x")
        mutating = [
            c
            for c in saved_scene.scene.file_calls
            if not c["kwargs"].get("query") and not c["kwargs"].get("q")
        ]
        assert mutating, "no file calls recorded"
        for c in mutating:
            assert c["kwargs"].get("prompt") is False, c

    def test_scene_name_uses_absolute_name(self, saved_scene):
        saved_scene.scene.file_calls.clear()
        saved_scene.module.save_checkpoint("x")
        assert any(
            c["kwargs"].get("expandName") or c["kwargs"].get("absoluteName")
            for c in saved_scene.scene.file_calls
        ), "sceneName must be queried via expandName/absoluteName for disambiguation"


# ------------------------------------------------------------
# rollback = S2 (open + rebind), auto_before_rollback invariant
# ------------------------------------------------------------


class TestRollback:
    def test_rollback_restores_snapshot_state(self, saved_scene, tmp_path):
        saved_scene.scene.add_mesh("GEO_a", t=(1, 0, 0))
        res = saved_scene.module.save_checkpoint("v1")
        assert "error" not in res
        # mutate: move + add
        saved_scene.scene.resolve("GEO_a").t = [9, 9, 9]
        saved_scene.scene.add_mesh("GEO_after")

        rb = saved_scene.module.rollback_to_checkpoint("cp_v1.ma")
        assert rb["success"] is True, rb
        assert not saved_scene.scene.exists("GEO_after"), "post-checkpoint node must be gone"
        node = saved_scene.scene.resolve("GEO_a")
        assert node.t == [1, 0, 0], "transform must return to snapshot state"

    def test_rollback_s2_rebind_structured(self, saved_scene, tmp_path):
        saved_scene.scene.add_mesh("GEO_a")
        saved_scene.module.save_checkpoint("v1")
        scene_path = str(tmp_path / "shot.ma")
        rb = saved_scene.module.rollback_to_checkpoint("cp_v1.ma")
        assert rb["success"] is True, rb
        assert rb["scene_name_before"] == scene_path
        assert rb["scene_name_after"] == scene_path, "S2: rebind to original path"
        assert rb["scene_rebound_to"] == scene_path
        assert rb["original_file_status"] == "saved"
        assert rb["safety_snapshot"] != "skipped_by_user"
        assert os.path.exists(rb["safety_snapshot"]), "auto snapshot must exist on disk"

    def test_rollback_auto_snapshot_captures_current(self, saved_scene, tmp_path):
        saved_scene.scene.add_mesh("GEO_a")
        saved_scene.module.save_checkpoint("v1")
        saved_scene.scene.add_mesh("GEO_wip")  # unsaved work after checkpoint
        rb = saved_scene.module.rollback_to_checkpoint("cp_v1.ma")
        assert rb["success"] is True
        with open(rb["safety_snapshot"], encoding="utf-8") as _auto_f:
            auto_text = _auto_f.read()
        assert "GEO_wip" in auto_text, "auto_before_rollback must snapshot memory state"

    def test_rollback_rejects_traversal(self, saved_scene):
        for bad in ["../x.ma", "..\\x.ma", "sub/cp_x.ma", "cp_../../x.ma"]:
            res = saved_scene.module.rollback_to_checkpoint(bad)
            assert res["error"]["code"] == "invalid_filename", bad

    def test_rollback_rejects_non_checkpoint_name(self, saved_scene):
        res = saved_scene.module.rollback_to_checkpoint("random.ma")
        assert res["error"]["code"] == "invalid_filename"

    def test_rollback_missing_snapshot(self, saved_scene):
        res = saved_scene.module.rollback_to_checkpoint("cp_ghost.ma")
        assert res["error"]["code"] == "snapshot_lost"
        assert "快照已丢失" in res["error"]["message"]
        assert "missing" in res["error"]["message"].lower()  # missing, not corrupt
        assert "header" not in res["error"]["message"].lower()

    def test_rollback_corrupt_snapshot(self, saved_scene, tmp_path):
        cp_dir = tmp_path / "checkpoints"
        cp_dir.mkdir()
        (cp_dir / "cp_bad.ma").write_text("garbage without header")
        res = saved_scene.module.rollback_to_checkpoint("cp_bad.ma")
        assert res["error"]["code"] == "snapshot_lost"
        assert "快照已丢失" in res["error"]["message"]
        msg = res["error"]["message"].lower()
        assert "header" in msg or "corrupt" in msg

    def test_auto_snapshot_failure_aborts(self, saved_scene, monkeypatch):
        saved_scene.scene.add_mesh("GEO_a")
        saved_scene.module.save_checkpoint("v1")
        saved_scene.scene.add_mesh("GEO_wip")

        real_file = saved_scene.cmds.file

        def boom(*args, **kwargs):
            if kwargs.get("exportAll"):
                raise RuntimeError("disk full")
            return real_file(*args, **kwargs)

        monkeypatch.setattr(saved_scene.cmds, "file", boom)
        saved_scene.scene.file_calls.clear()
        rb = saved_scene.module.rollback_to_checkpoint("cp_v1.ma")
        assert rb["error"]["code"] == "auto_snapshot_failed"
        assert rb.get("aborted") is True, rb
        opened = [c for c in saved_scene.scene.file_calls if c["kwargs"].get("open")]
        assert not opened, "checkpoint must NOT be opened when auto snapshot fails"

    def test_discard_current_state_escape(self, saved_scene, monkeypatch):
        saved_scene.scene.add_mesh("GEO_a")
        saved_scene.module.save_checkpoint("v1")

        real_file = saved_scene.cmds.file

        def boom(*args, **kwargs):
            if kwargs.get("exportAll"):
                raise RuntimeError("disk full")
            return real_file(*args, **kwargs)

        monkeypatch.setattr(saved_scene.cmds, "file", boom)
        rb = saved_scene.module.rollback_to_checkpoint("cp_v1.ma", discard_current_state=True)
        assert rb["success"] is True, rb
        assert rb["safety_snapshot"] == "skipped_by_user"

    def test_discard_flag_warns_when_auto_ok(self, saved_scene):
        saved_scene.scene.add_mesh("GEO_a")
        saved_scene.module.save_checkpoint("v1")
        rb = saved_scene.module.rollback_to_checkpoint("cp_v1.ma", discard_current_state=True)
        assert rb["success"] is True
        assert "warning" in rb, "discard flag + successful auto snapshot must warn"

    def test_rollback_untitled_stays_on_cp(self, untitled_scene, tmp_path):
        untitled_scene.scene.add_mesh("GEO_a")
        res = untitled_scene.module.save_checkpoint("rescue")
        assert "error" not in res
        untitled_scene.scene.add_mesh("GEO_after")
        rb = untitled_scene.module.rollback_to_checkpoint("cp_adhoc_rescue.ma")
        assert rb["success"] is True, rb
        # S1: no original file to rebind to -> stay on the checkpoint path
        assert rb["scene_rebound_to"] is None
        assert rb["original_file_status"] == "no_original_file"
        assert rb["scene_name_after"].endswith("cp_adhoc_rescue.ma")
        assert not untitled_scene.scene.exists("GEO_after")

    def test_rollback_untitled_auto_snapshot_lands_workspace(self, untitled_scene, tmp_path):
        """D-016d: untitled + auto_before_rollback -> auto snapshot lands in
        the workspace ad-hoc checkpoints dir."""
        untitled_scene.scene.add_mesh("GEO_a")
        res = untitled_scene.module.save_checkpoint("rescue")
        assert "error" not in res
        rb = untitled_scene.module.rollback_to_checkpoint("cp_adhoc_rescue.ma")
        assert rb["success"] is True, rb
        assert rb["safety_snapshot"] != "skipped_by_user"
        assert os.path.dirname(rb["safety_snapshot"]) == str(tmp_path / "ws" / "checkpoints")
        assert os.path.exists(rb["safety_snapshot"])
        assert os.path.basename(rb["safety_snapshot"]).startswith("cp_auto_before_rollback_")

    def test_rollback_open_failure_reports_state(self, saved_scene, monkeypatch):
        saved_scene.scene.add_mesh("GEO_a")
        saved_scene.module.save_checkpoint("v1")

        real_file = saved_scene.cmds.file

        def boom(*args, **kwargs):
            if kwargs.get("open") or kwargs.get("o"):
                raise RuntimeError("open blew up")
            return real_file(*args, **kwargs)

        monkeypatch.setattr(saved_scene.cmds, "file", boom)
        rb = saved_scene.module.rollback_to_checkpoint("cp_v1.ma")
        assert rb["error"]["code"] == "open_failed"
        assert "scene_name_after" in rb, "half-state must report scene_name_after"
        assert "scene_snapshot" in rb["error"].get("suggestion", ""), (
            "must suggest scene_snapshot to rebuild context"
        )

    def test_rollback_file_ops_prompt_false(self, saved_scene):
        saved_scene.scene.add_mesh("GEO_a")
        saved_scene.module.save_checkpoint("v1")
        saved_scene.scene.file_calls.clear()
        saved_scene.module.rollback_to_checkpoint("cp_v1.ma")
        mutating = [
            c
            for c in saved_scene.scene.file_calls
            if not c["kwargs"].get("query") and not c["kwargs"].get("q")
        ]
        assert mutating
        for c in mutating:
            if c["kwargs"].get("rename"):
                # Real Maya rejects flag combos with -rename ("the
                # -rename flag must be used by itself") — live-verified
                # on Maya 2024 (artifact pending docs/evidence intake —
                # D-109). Rename carries no prompt flag at all.
                assert list(c["kwargs"]) == ["rename"], c
            else:
                assert c["kwargs"].get("prompt") is False, c

    def test_rollback_rename_is_standalone(self, saved_scene):
        """D-046-round live find: cmds.file(rename=X, prompt=False)
        raises '-rename must be used by itself' on real Maya — the S2
        rebind must call rename with no other flags."""
        saved_scene.scene.add_mesh("GEO_a")
        saved_scene.module.save_checkpoint("v1")
        saved_scene.scene.file_calls.clear()
        rb = saved_scene.module.rollback_to_checkpoint("cp_v1.ma")
        assert rb["success"] is True, rb
        renames = [c for c in saved_scene.scene.file_calls if c["kwargs"].get("rename")]
        assert renames, "saved-scene rollback must rebind via rename"
        for c in renames:
            assert list(c["kwargs"]) == ["rename"], c

    def test_rollback_to_preserved_prev_file(self, saved_scene, tmp_path):
        saved_scene.scene.add_mesh("GEO_v1")
        saved_scene.module.save_checkpoint("x")
        saved_scene.scene.add_mesh("GEO_v2")
        res = saved_scene.module.save_checkpoint("x", overwrite=True)
        assert "error" not in res
        rb = saved_scene.module.rollback_to_checkpoint(res["preserved_as"])
        assert rb["success"] is True, rb
        assert saved_scene.scene.exists("GEO_v1")
        assert not saved_scene.scene.exists("GEO_v2")


# ------------------------------------------------------------
# list_checkpoints
# ------------------------------------------------------------


class TestListCheckpoints:
    def test_list_counts_and_marks_adhoc(self, saved_scene, tmp_path):
        saved_scene.scene.add_mesh("GEO_a")
        saved_scene.module.save_checkpoint("v1")
        res = saved_scene.module.list_checkpoints()
        assert res["count"] == 1
        assert res["checkpoints"][0]["filename"] == "cp_v1.ma"
        assert res["checkpoints"][0]["adhoc"] is False

    def test_list_untitled_uses_workspace(self, untitled_scene, tmp_path):
        untitled_scene.module.save_checkpoint("rescue")
        res = untitled_scene.module.list_checkpoints()
        assert res["count"] == 1
        assert res["checkpoints"][0]["filename"] == "cp_adhoc_rescue.ma"
        assert res["checkpoints"][0].get("adhoc") is True

    def test_list_empty_when_no_dir(self, saved_scene):
        res = saved_scene.module.list_checkpoints()
        assert res == {"checkpoints": [], "count": 0}


# ------------------------------------------------------------
# D-019 error contract: {error: {code, message, suggestion?}}
# ------------------------------------------------------------


class TestDomainErrorShape:
    """Every Maya-domain error return carries the D-019 structured object."""

    def _check(self, res):
        assert isinstance(res.get("error"), dict), res
        err = res["error"]
        assert isinstance(err["code"], str) and err["code"]
        assert isinstance(err["message"], str) and err["message"]
        if "suggestion" in err:
            assert isinstance(err["suggestion"], str)
        return err

    def test_save_errors(self, saved_scene):
        for res in (saved_scene.module.save_checkpoint("a/b"),):
            self._check(res)
        saved_scene.module.save_checkpoint("x")
        self._check(saved_scene.module.save_checkpoint("x"))

    def test_rollback_errors(self, saved_scene):
        for bad in ("cp_ghost.ma", "nope.ma"):
            self._check(saved_scene.module.rollback_to_checkpoint(bad))

    def test_untitled_error(self, untitled_scene):
        self._check(untitled_scene.module.save_checkpoint())

    def test_measure_error(self, saved_scene):
        self._check(saved_scene.module.measure("no_a", "no_b"))

    def test_camera_shot_errors(self, saved_scene):
        self._check(saved_scene.module.create_camera_shot("x", shot_type="nope"))
        self._check(saved_scene.module.create_camera_shot("missing_target"))
