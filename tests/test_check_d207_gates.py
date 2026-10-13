"""Regression tests for the D-207 checker family.

Three gates added together because they share one discipline: each asserts a
derived file against the thing it is derived from, so none of them can hold a
hand-maintained copy:

    check_error_codes.py         docs/guide/error-codes.md   <- security.py
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


# ---------------------------------------------------------------------------
# D-207 4: error codes
# ---------------------------------------------------------------------------


class TestErrorCodesChecker:
    def test_live_gate_is_green(self):
        out = _run("check_error_codes.py")
        assert out.returncode == 0, out.stdout + out.stderr

    def test_code_set_reads_every_pipeline_error_subclass(self):
        mod = _load("check_error_codes")
        codes = mod.code_set()
        # declared, not counted from prose: one entry per subclass
        assert "pipeline_error" in codes
        assert "policy_disabled" in codes, "D-207 1 added a code; the gate must see it"
        assert len(codes) == len(set(codes)), "codes are dict keys, so this is the count"

    def test_missing_doc_page_is_an_error_not_a_pass(self, tmp_path, monkeypatch):
        mod = _load("check_error_codes")
        monkeypatch.setattr(mod, "DOC", tmp_path / "nope.md")
        assert mod.main([]) == 1

    def test_undocumented_code_is_reported(self, tmp_path, monkeypatch):
        mod = _load("check_error_codes")
        page = tmp_path / "error-codes.md"
        page.write_text(
            "| Code |\n|------|\n| `pipeline_error` |\n| `invalid_input` |\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(mod, "DOC", page)
        assert mod.main([]) == 1, "a code in code but not in the doc must fail"

    def test_phantom_documented_code_is_reported(self, tmp_path, monkeypatch):
        mod = _load("check_error_codes")
        page = tmp_path / "error-codes.md"
        page.write_text(
            "| Code |\n|------|\n| `pipeline_error` |\n| `code_that_was_removed` |\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(mod, "DOC", page)
        assert mod.main([]) == 1, "a doc row with no class behind it must fail"

    def test_the_two_tables_are_read_as_separate_sets(self):
        """Regression: the page carries every code twice (body table + anchor
        table) and the set was extracted from the whole file, so deleting a BODY
        row left the anchor row to satisfy it - the gate printed "doc set == code
        set" while the page documented nothing about that code. That is the
        empty-claim state D-207 4 forbids."""
        mod = _load("check_error_codes")
        text = (REPO / "docs" / "guide" / "error-codes.md").read_text(encoding="utf-8")
        codes = set(mod.code_set())
        assert mod.doc_set() == codes, "the body table must cover every code"
        assert mod.anchor_set() == codes, "the anchor table must cover every code"
        body, anchors = mod._split_doc(text)
        assert anchors.strip(), "the anchors heading must exist"
        assert mod.doc_set() <= set(mod._DOC_ROW.findall(body))
        assert "capture_invalid" in mod.doc_set()

    def test_a_deleted_body_row_fails_the_gate(self, tmp_path, monkeypatch):
        """The exact counterfactual: remove one body row, keep its anchor row."""
        mod = _load("check_error_codes")
        real = (REPO / "docs" / "guide" / "error-codes.md").read_text(encoding="utf-8")
        lines = real.split("\n")
        cut = lines.index(next(x for x in lines if "## Test anchors" in x))
        cut = next(
            i
            for i, ln in enumerate(lines)
            if i < cut and re.match(r"^\|\s*`capture_invalid`\s*\|", ln)
        )
        lines.pop(cut)
        page = tmp_path / "error-codes.md"
        page.write_text("\n".join(lines), encoding="utf-8")
        monkeypatch.setattr(mod, "DOC", page)
        assert "capture_invalid" in mod.anchor_set(), "precondition: anchor row survives"
        assert mod.main([]) == 1, "a code with no body row must fail the gate"

    def test_a_deleted_anchor_row_fails_the_gate(self, tmp_path, monkeypatch):
        mod = _load("check_error_codes")
        real = (REPO / "docs" / "guide" / "error-codes.md").read_text(encoding="utf-8")
        lines = real.split("\n")
        after = lines.index(next(x for x in lines if "## Test anchors" in x))
        cut = next(
            i
            for i, ln in enumerate(lines)
            if i > after and re.match(r"^\|\s*`capture_invalid`\s*\|", ln)
        )
        lines.pop(cut)
        page = tmp_path / "error-codes.md"
        page.write_text("\n".join(lines), encoding="utf-8")
        monkeypatch.setattr(mod, "DOC", page)
        assert "capture_invalid" in mod.doc_set(), "precondition: body row survives"
        assert mod.main([]) == 1, "a code with no anchor row must fail the gate"

    def test_exact_match_passes(self, tmp_path, monkeypatch):
        mod = _load("check_error_codes")
        codes = sorted(mod.code_set())
        body = "".join(f"| `{c}` |\n" for c in codes)
        anchors = "".join(f"| `{c}` |\n" for c in codes)
        page = tmp_path / "error-codes.md"
        page.write_text(
            f"| Code |\n|------|\n{body}\n## Test anchors\n\n| Code |\n|------|\n{anchors}",
            encoding="utf-8",
        )
        monkeypatch.setattr(mod, "DOC", page)
        assert mod.main([]) == 0

    def test_every_cited_node_id_resolves_in_the_tree(self):
        """The anchor table is only worth something if its node IDs point at
        tests that exist. Resolved statically (file exists + symbol present) so
        this stays a cheap gate rather than a pytest-in-pytest run."""
        doc = (REPO / "docs" / "guide" / "error-codes.md").read_text(encoding="utf-8")
        table = doc.split("## Test anchors", 1)[1]
        ids = re.findall(r"`(tests/[\w./]+\.py(?:::[A-Za-z_]\w*)+)`", table)
        assert ids, "no node IDs found in the anchor table"
        for node_id in ids:
            path, _, symbols = node_id.partition("::")
            assert (REPO / path).is_file(), f"{path} does not exist"
            source = (REPO / path).read_text(encoding="utf-8")
            for symbol in symbols.split("::"):
                # a class segment is `class Foo`, a test segment is `def foo`
                pattern = rf"(?:class|def) {re.escape(symbol)}\b"
                assert re.search(pattern, source), (
                    f"{node_id} is cited but {symbol} is not defined in {path}"
                )

    def test_session_lifecycle_anchor_composition_is_what_the_changelog_claims(self):
        """A count in a versioned doc is a machine-checked carrier (D-205), not
        prose. An earlier revision of this window wrote "9 path::symbol anchors"
        when the real composition was 8 plus one ADR file path, and then
        overrode a correct review that said so. The numbers are derived here."""
        doc = (REPO / "docs" / "guide" / "session-lifecycle.md").read_text(encoding="utf-8")
        section = doc[doc.index("## Anchors") :]
        # Only the LIST ITEMS are anchors. Counting every backticked token in the
        # section would sweep up the prose that describes the anchors - including
        # this test's own name, which is cited there - and inflate the count the
        # test is asserting. That is the same class of error as the one it pins.
        items = [ln for ln in section.split("\n") if ln.startswith("- ")]
        refs = [
            m
            for ln in items
            for m in re.findall(r"`([^`]+)`", ln)
            if "::" in m or re.search(r"\.(py|md|yaml)$", m)
        ]
        path_symbol = [
            r
            for r in refs
            if ".py::" in r and re.match(r"^(src|tests|docs|\.github)/", r.split("::")[0])
        ]
        doc_paths = [r for r in refs if "::" not in r]
        assert len(refs) == 9, f"expected 9 references, found {len(refs)}: {refs}"
        assert len(path_symbol) == 8, f"expected 8 path::symbol, got {len(path_symbol)}"
        assert len(doc_paths) == 1, f"expected 1 file path, got {doc_paths}"
        for ref in path_symbol:
            path, _, symbols = ref.partition("::")
            assert (REPO / path).is_file(), ref
            source = (REPO / path).read_text(encoding="utf-8")
            assert re.search(rf"(?:class|def) {re.escape(symbols)}\b", source), ref
        # and the CHANGELOG must quote these measured numbers, not its own
        changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
        entry = changelog[changelog.index("Session-lifecycle matrix") :][:900]
        assert "eight `path::symbol`" in entry, entry[:400]
        assert "nine anchor references" in entry, entry[:400]

    def test_every_documented_code_cites_a_pytest_node_id(self):
        """D-207 4: each code row must carry an anchor, so 'documented' does not
        degrade into 'believed'."""
        doc = (REPO / "docs" / "guide" / "error-codes.md").read_text(encoding="utf-8")
        anchors = doc.split("## Test anchors", 1)[1]
        rows = [ln for ln in anchors.splitlines() if ln.startswith("| `")]
        assert rows, "the anchor table is empty"
        for row in rows:
            assert "::" in row, f"no pytest node ID in: {row}"


# ---------------------------------------------------------------------------
# D-207 2: llms.txt
# ---------------------------------------------------------------------------


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
        assert len(matrix) == 25, f"expected the 25-tool registry, got {len(matrix)}"
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
        assert matrix["asset_search"].get("open_world_hint") is True

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

        The corrupted copy lives in pytest's tmp_path (ADR-0029 section 3), never
        in the repo root - a fixture written into the worktree would leave a dirty
        file behind if the test aborted before cleanup."""
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


# ---------------------------------------------------------------------------
# D-230: CHANGELOG Fixed-entry field gate
# ---------------------------------------------------------------------------


class TestChangelogFixedFields:
    def test_live_gate_is_green(self):
        out = _run("check_changelog_fixed.py")
        assert out.returncode == 0, out.stdout + out.stderr
        # the disclosure line names both the in-scope and grandfathered counts
        assert "in-scope entries" in out.stdout, out.stdout
        assert "grandfathered" in out.stdout, out.stdout

    def test_field_value_reads_the_tail_line_not_just_the_last_line(self, tmp_path):
        """The fields sit on their own line before a trailing Evidence: line, so
        a last-line-only scan would miss them - the whole block is searched."""
        mod = _load("check_changelog_fixed")
        entry = (
            "- **A thing was fixed** — description.\n"
            "  Broken version: unreleased; fixed version: 0.6.0.\n"
            "  Evidence: `tests/test_x.py::test_y`.\n"
        )
        # each value runs to the first ';' or end-of-line, so a same-line pair
        # does not leak one field into the other's value (R48 audit fix)
        assert mod._field_value(entry, "Broken version:") == "unreleased"
        assert mod._field_value(entry, "fixed version:") == "0.6.0."
        assert mod._field_value(entry, "Missing version:") is None

    def test_a_same_line_empty_broken_version_fails_the_gate(self, tmp_path, monkeypatch):
        """Counterfactual red for the R48 audit's exact escape: both fields share
        one line and the broken-version value is empty. The old end-of-line slice
        swallowed '; fixed version: 0.6.0.' as the broken value and reported it
        non-empty (a false green); splitting on ';' must turn this red."""
        mod = _load("check_changelog_fixed")
        bad = tmp_path / "CHANGELOG.md"
        bad.write_text(
            "## [0.6.0] - 2026-10-05\n\n### Fixed\n\n"
            "- **A fix** — text.\n"
            "  Broken version: ; fixed version: 0.6.0.\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(mod, "CHANGELOG", bad)
        assert mod.main() == 1, "an empty same-line Broken version: must fail (D-230)"

    def test_a_corrupted_entry_missing_a_field_fails_the_gate(self, tmp_path, monkeypatch):
        """Counterfactual red: an in-scope entry that loses its broken-version
        field must turn the gate red."""
        mod = _load("check_changelog_fixed")
        bad = tmp_path / "CHANGELOG.md"
        bad.write_text(
            "## [Unreleased]\n\n### Fixed\n\n"
            "- **A fix** — text.\n"
            "  fixed version: 0.6.0.\n"  # Broken version: deliberately absent\n"
            "\n## [0.5.0] - 2026-09-30\n\n### Fixed\n\n"
            "- **Old fix** — predates the rule, no fields, grandfathered.\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(mod, "CHANGELOG", bad)
        rc = mod.main()
        assert rc == 1, "an in-scope entry missing Broken version: must fail (D-230)"

    def test_an_empty_field_value_fails_the_gate(self, tmp_path, monkeypatch):
        """Counterfactual red: a present-but-empty value is not compliance."""
        mod = _load("check_changelog_fixed")
        bad = tmp_path / "CHANGELOG.md"
        bad.write_text(
            "## [0.6.0] - 2026-10-05\n\n### Fixed\n\n"
            "- **A fix** — text.\n"
            "  Broken version: unreleased;\n"
            "  fixed version: \n",
            encoding="utf-8",
        )
        monkeypatch.setattr(mod, "CHANGELOG", bad)
        assert mod.main() == 1, "an empty fixed version: value must fail (D-230)"

    def test_pre_boundary_sections_are_grandfathered_not_asserted(self, tmp_path, monkeypatch):
        """The frozen boundary exempts released sections below ENFORCED_FROM even
        though they lack the fields - backfilling them would invent facts (D-205)."""
        mod = _load("check_changelog_fixed")
        doc = tmp_path / "CHANGELOG.md"
        doc.write_text(
            "## [Unreleased]\n\n### Fixed\n\n"
            "- **New fix** — text.\n"
            "  Broken version: unreleased; fixed version: [Unreleased].\n"
            "\n## [0.5.0] - 2026-09-30\n\n### Fixed\n\n"
            "- **Old fix** — no fields at all, but below the boundary.\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(mod, "CHANGELOG", doc)
        assert mod.main() == 0, "pre-boundary sections are exempt, not errors (D-230)"

    def test_unreleased_corrupted_without_fields_fails_the_gate(self, tmp_path, monkeypatch):
        """[Unreleased] is always in scope even though it is not a dotted version."""
        mod = _load("check_changelog_fixed")
        bad = tmp_path / "CHANGELOG.md"
        bad.write_text(
            "## [Unreleased]\n\n### Fixed\n\n- **New fix** — text with neither field.\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(mod, "CHANGELOG", bad)
        assert mod.main() == 1, "[Unreleased] is enforced even though non-dotted (D-230)"
