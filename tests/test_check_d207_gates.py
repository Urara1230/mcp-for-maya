"""Regression tests for the llms.txt and version-consistency checkers.

Both assert a derived file against the thing it is derived from, so neither can
hold a hand-maintained copy:

    check_llms_txt.py            llms.txt                    <- pipeline.TOOL_ANNOTATIONS
    check_version_consistency.py pyproject / __init__ / CHANGELOG / llms.txt

Each test asserts the failure direction matters: a gate that passes on drift is
worse than no gate, because the repo then carries a checkmark that means
nothing.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".github" / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        capture_output=True,
        text=True,
        cwd=REPO,
    )


class TestLlmsTxtChecker:
    def test_live_gate_is_green(self):
        out = _run("check_llms_txt.py")
        assert out.returncode == 0, out.stdout + out.stderr

    def test_file_exists_and_respects_the_100_line_cap(self):
        lines = (REPO / "llms.txt").read_text(encoding="utf-8").splitlines()
        assert 0 < len(lines) <= 100, f"{len(lines)} lines"

    def test_llms_full_was_not_produced(self):
        assert not (REPO / "llms-full.txt").exists(), (
            "D-207 2 adopted the single-file form explicitly; a second file is scope drift"
        )

    def test_every_annotated_tool_is_listed_with_a_correct_class(self):
        mod = _load("check_llms_txt")
        matrix = mod.annotation_matrix(REPO)
        assert len(matrix) == 23, f"expected the 23-tool registry, got {len(matrix)}"
        text = (REPO / "llms.txt").read_text(encoding="utf-8")
        for name, hints in matrix.items():
            assert f"[{name}]" in text, f"{name} missing from llms.txt"
            label = mod._class_label(hints)
            row = next(ln for ln in text.splitlines() if f"[{name}]" in ln)
            assert f"): {label}" in row, f"{name} should be labelled {label}: {row}"

    def test_shared_annotation_constants_resolve(self):
        """Regression: matching only ast.Name for ToolAnnotations made every
        tool look like a mutation, because the matrix spells it mt.ToolAnnotations."""
        mod = _load("check_llms_txt")
        matrix = mod.annotation_matrix(REPO)
        assert mod._class_label(matrix["scene_snapshot"]) == "read-only"
        assert mod._class_label(matrix["execute_code"]) == "destructive"
        assert mod._class_label(matrix["scene_export"]) == "mutation"

    def test_no_tool_is_left_without_a_summary(self):
        text = (REPO / "llms.txt").read_text(encoding="utf-8")
        assert "no docstring summary" not in text

    def test_drift_fails(self, tmp_path, monkeypatch):
        mod = _load("check_llms_txt")
        bad = tmp_path / "llms.txt"
        bad.write_text("# Maya MCP Server\n\n- [ghost_tool](#ghost_tool): read-only\n")
        monkeypatch.setattr(mod, "LLMS", bad)
        assert mod.main([]) == 1

    def test_doc_urls_are_derived_from_pyproject_not_hardcoded(self):
        """Regression: the generator once hardcoded an owner name this project
        has never occupied, and because the gate compared the file against its
        own constants it reported green throughout. Every URL in the generated
        page must trace back to [project.urls]."""
        mod = _load("check_llms_txt")
        text = (REPO / "llms.txt").read_text(encoding="utf-8")
        expected_base = mod.repo_url(REPO)
        assert "github.com" in expected_base
        urls = re.findall(r"\]\((https://github\.com/[^)]+)\)", text)
        assert urls, "the page must link out somewhere"
        for url in urls:
            assert url.startswith(expected_base + "/blob/main/"), (
                f"{url} does not derive from {expected_base}"
            )
        source = (SCRIPTS / "check_llms_txt.py").read_text(encoding="utf-8")
        assert "Sirsha04" not in source, "the invented owner is gone"

    def test_a_tool_with_no_section_heading_is_an_error(self, monkeypatch):
        """_SEC is an editorial map, so an incomplete one used to make a tool
        vanish from the page silently. It must fail the gate instead."""
        mod = _load("check_llms_txt")
        matrix = mod.annotation_matrix(REPO)
        assert mod.unsectioned(matrix) == [], "the live _SEC map is complete"
        monkeypatch.delitem(mod._SEC, "camera_orbit")
        assert mod.unsectioned(matrix) == ["camera_orbit"]

    def test_a_tool_absent_from_the_page_is_reported(self, tmp_path, monkeypatch):
        """The coverage check used to live inside the byte-equality branch, so
        it could not fire when render() and the file agreed on an omission."""
        mod = _load("check_llms_txt")
        page = tmp_path / "llms.txt"
        page.write_text((REPO / "llms.txt").read_text(encoding="utf-8"), encoding="utf-8")
        monkeypatch.setattr(mod, "LLMS", page)
        assert mod.main([]) == 0
        page.write_text("# stripped\n", encoding="utf-8")
        assert mod.main([]) == 1, "every tool missing at once must not pass"

    def test_doc_links_are_not_mistaken_for_tool_entries(self):
        """Regression: the coverage regex matched the absolute-URL doc links at
        the top of the page and reported them as stale tools."""
        mod = _load("check_llms_txt")
        matrix = mod.annotation_matrix(REPO)
        text = (REPO / "llms.txt").read_text(encoding="utf-8")
        listed = set(re.findall(r"^- \[(\w+)\]\(#", text, re.M))
        assert set(matrix) - listed == set(), "all tools are listed"
        assert "README" not in listed, "the doc link is not a tool entry"

    def test_write_is_idempotent(self):
        before = (REPO / "llms.txt").read_bytes()
        out = _run("check_llms_txt.py", "--write")
        assert out.returncode == 0, out.stdout + out.stderr
        assert (REPO / "llms.txt").read_bytes() == before, "regeneration is not stable"


# ---------------------------------------------------------------------------
# D-207 9: version consistency
# ---------------------------------------------------------------------------


class TestVersionConsistency:
    def test_live_gate_is_green(self):
        out = _run("check_version_consistency.py")
        assert out.returncode == 0, out.stdout + out.stderr

    def test_all_four_surfaces_agree_today(self):
        out = _run("check_version_consistency.py")
        assert "==" in out.stdout, out.stdout

    def test_unreleased_state_is_tolerated_not_an_error(self):
        """The gate must stay green while pyproject runs ahead of the newest
        released CHANGELOG section with real work under [Unreleased] - otherwise
        it gets deleted, and a deleted gate is worse than no gate."""
        mod = _load("check_version_consistency")
        log = (
            "## [Unreleased]\n\n### Added\n\n- something new\n\n"
            "## [0.5.0] - 2026-09-30\n\n- released stuff\n"
        )
        assert mod.newest_released(log) == (0, 5, 0)
        assert mod.unreleased_body(log).startswith("### Added")

    def test_unreleased_tolerance_requires_actual_content(self, tmp_path):
        mod = _load("check_version_consistency")
        empty = "## [Unreleased]\n\n## [0.5.0] - 2026-09-30\n\n- x\n"
        assert mod.unreleased_body(empty) == "", "an empty Unreleased carries nothing"

    def test_newest_released_ignores_the_unreleased_header(self):
        mod = _load("check_version_consistency")
        log = "## [Unreleased]\n\n- x\n\n## [0.4.1] - 2026-01-01\n"
        assert mod.newest_released(log) == (0, 4, 1)

    def test_section_regex_only_matches_dotted_versions(self):
        mod = _load("check_version_consistency")
        assert mod._SECTION.search("## [Unreleased]") is None

    # --- D-229 2: server.json triple (counterfactual reds + live green) ----

    def test_server_json_version_parser_reads_the_top_level_field(self, tmp_path):
        mod = _load("check_version_consistency")
        p = tmp_path / "server.json"
        p.write_text('{"version": "0.6.0", "packages": [{"version": "0.5.0"}]}', encoding="utf-8")
        assert mod.server_json_version(p) == (0, 6, 0)

    def test_server_json_version_rejects_non_dotted_and_missing(self, tmp_path):
        mod = _load("check_version_consistency")
        bad = tmp_path / "bad.json"
        bad.write_text('{"version": "next"}', encoding="utf-8")
        assert mod.server_json_version(bad) is None
        absent = tmp_path / "nope.json"
        assert mod.server_json_version(absent) is None

    def test_live_gate_names_server_json_in_the_triple(self):
        out = _run("check_version_consistency.py")
        assert out.returncode == 0, out.stdout + out.stderr
        # the triple now names server.json explicitly (D-229 2)
        assert "server.json" in out.stdout, out.stdout

    def test_a_drifted_server_json_fails_the_gate(self, tmp_path, monkeypatch):
        """Counterfactual red: a manifest whose version lags pyproject must go red.

        The corrupted copy lives in pytest's tmp_path (counterfactual pins never
        touch the real object), never in the repo root - a fixture written into
        the worktree would leave a dirty file behind if the test aborted before
        cleanup."""
        mod = _load("check_version_consistency")
        import json as _json

        doc = _json.loads((REPO / "server.json").read_text(encoding="utf-8"))
        doc["version"] = "0.5.0"
        drifted = tmp_path / "server.json"
        drifted.write_text(_json.dumps(doc), encoding="utf-8")
        monkeypatch.setattr(mod, "SERVER_JSON", drifted)
        assert mod.main() == 1, "a drifted registry manifest must fail the gate (D-229 2)"

    def test_a_missing_server_json_fails_the_gate(self, monkeypatch):
        """Counterfactual red: the manifest must be version-controlled at the root."""
        mod = _load("check_version_consistency")
        monkeypatch.setattr(mod, "SERVER_JSON", REPO / "definitely-not-here.json")
        assert mod.main() == 1, "a missing server.json must fail the gate (D-229 1)"
