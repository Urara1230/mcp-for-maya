"""Regression tests for .github/scripts/check_migration_shape.py.

Every case here is a defect a shipped form actually had: the two snippets that
PR #106 carried as documentation passed while validating nothing, called `([)]`
balanced, reported a deleted docstring as moved code, and flagged migrations
for deleting a delimiter-free comment. The metric's direction is pinned too —
counting *balanced* blocks and firing on a decrease was the worst of them.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / ".github" / "scripts"
SCRIPT = SCRIPTS_DIR / "check_migration_shape.py"
REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("check_migration_shape", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _check(mod, path, base, tip):
    return mod.check([(path, base, tip)])


BALANCED_DOC = '"""Module docstring that spans lines\nand stays balanced (one pair here).\n"""\n'
STRANDED_DOC = '"""Module docstring that spans lines\nand ends with a stranded closer).\n"""\n'
MISNEST_DOC = '"""Module docstring that spans lines\nand mis-nests its pairs ([)] here).\n"""\n'
LITERAL = 'X = (\n    "def g(x):\\n"\n    "    return g(x  # unmatched inside a literal\\n"\n)\n'


def test_identical_source_passes(tmp_path):
    mod = _load()
    ok, errors, warnings = _check(mod, "m.py", BALANCED_DOC, BALANCED_DOC)
    assert ok and errors == [] and warnings == []


def test_balanced_parenthetical_removal_passes(tmp_path):
    """Retiring an id removes a balanced pair; that is the migration working."""
    mod = _load()
    base = BALANCED_DOC + "# contract (D-013) lives there\n"
    tip = BALANCED_DOC + "# contract lives there\n"
    ok, errors, _ = _check(mod, "m.py", base, tip)
    assert ok and errors == []


def test_stranded_closer_in_docstring_fails(tmp_path):
    """The PR #101 defect: the closer sits on a line the edit never touched."""
    mod = _load()
    base = BALANCED_DOC + "import os\n"
    tip = STRANDED_DOC + "import os\n"
    ok, errors, _ = _check(mod, "m.py", base, tip)
    assert not ok
    assert any("UNBALANCED" in e for e in errors)
    assert any("0 -> 1" in e for e in errors)


def test_misnested_delimiters_are_a_finding(tmp_path):
    """Aggregate opener/closer counts called this balanced."""
    mod = _load()
    ok, errors, _ = _check(mod, "m.py", BALANCED_DOC, MISNEST_DOC)
    assert not ok and any("UNBALANCED" in e for e in errors)


def test_one_line_docstring_is_scanned(tmp_path):
    """The snippets skipped single-line strings and so missed this."""
    mod = _load()
    base = 'def f():\n    """Balanced (here)."""\n    return 1\n'
    tip = 'def f():\n    """A stray closer)."""\n    return 1\n'
    ok, errors, _ = _check(mod, "m.py", base, tip)
    assert not ok and any("UNBALANCED" in e for e in errors)


def test_module_docstring_counted_once(tmp_path):
    """ast.walk yields the root: seeding it with the tree doubled the count."""
    mod = _load()
    src = STRANDED_DOC + "def f():\n" + '    """Fn one stray)."""\n    return 1\n'
    assert len(mod.bad_blocks(src)) == 2


def test_delimiter_free_comment_deletion_does_not_flag(tmp_path):
    base = BALANCED_DOC + "# a plain comment with no delimiters\n# keep me\n"
    tip = BALANCED_DOC + "# keep me\n"
    mod = _load()
    ok, errors, _ = _check(mod, "m.py", base, tip)
    assert ok and errors == []


def test_fixing_a_block_is_not_a_finding(tmp_path):
    """Direction matters: only an increase in bad blocks is a finding."""
    mod = _load()
    ok, errors, _ = _check(mod, "m.py", STRANDED_DOC, BALANCED_DOC)
    assert ok and errors == []


def test_string_literal_change_is_code_moved(tmp_path):
    """A template literal is payload, not prose: check 1 owns it."""
    mod = _load()
    base = BALANCED_DOC + 'X = (\n    "def g(x):\\n    return g(x)\\n"\n)\n'
    assert mod.code_moved(base, BALANCED_DOC + LITERAL) is True


def test_docstring_deletion_is_not_code_moved(tmp_path):
    """Blanking instead of removing reported this as moved code."""
    mod = _load()
    src = '"""Doc."""\ndef f():\n    """Gone next revision."""\n    return 1\n'
    tip = '"""Doc."""\ndef f():\n    return 1\n'
    assert mod.code_moved(src, tip) is False


def test_unparseable_side_is_reported_not_raised(tmp_path):
    """Both snippets raised a bare SyntaxError traceback here."""
    mod = _load()
    ok, errors, _ = _check(mod, "m.py", BALANCED_DOC, BALANCED_DOC + "def f(:\n    pass\n")
    assert not ok
    assert any("UNPARSEABLE" in e for e in errors)


def test_paths_are_filtered_to_modified_python_files(tmp_path):
    """A blob existing on one side only has nothing to compare: it crashed on a
    merged new module rather than being skipped."""
    mod = _load()
    calls = []

    def runner(cmd, **kw):
        calls.append((cmd, kw))

        class R:
            stdout = "a.py\ndocs/note.md\nb.py\n"

        return R()

    paths = mod.python_paths(runner, "base", "tip")
    assert paths == ["a.py", "b.py"]
    assert "--diff-filter=M" in calls[0][0]
    assert calls[0][1]["check"] is True


def test_main_requires_two_revisions(tmp_path, capsys):
    mod = _load()
    assert mod.main([], REPO) == 1
    assert "usage" in capsys.readouterr().out.lower()


def test_main_fails_loud_on_a_bad_revision(tmp_path, capsys):
    """Unchecked return codes made a typo'd revision validate nothing."""
    mod = _load()
    assert mod.main(["definitely-not-a-rev-1", "definitely-not-a-rev-2"], REPO) == 1
    assert "::error::" in capsys.readouterr().out


def test_main_reports_a_real_defect_and_a_real_repair(tmp_path, capsys):
    """The numbers the docs quoted drifted twice; they belong in an assertion.

    c65e457..1d785a4 is the migration commit that stranded a ")" in pipeline.py;
    c65e457..feb6317 is #90's repaired tip. Both revisions exist only in history,
    so this skips rather than fails where the object is absent.
    """
    import subprocess

    base, defect, repaired = (
        "c65e4576d5458907270859eaa84c06d683931533",
        "1d785a434a22e1ad2120e8733931447b6f231e85",
        "feb6317bfc37b1adf050b3245cabbc4080c10bf2",
    )
    for rev in (base, defect, repaired):
        if subprocess.run(["git", "cat-file", "-e", rev], cwd=REPO).returncode != 0:
            import pytest

            pytest.skip(f"{rev} is not in this clone")
    mod = _load()
    assert mod.main([base, defect], REPO) == 1
    out = capsys.readouterr().out
    assert "pipeline.py" in out and "2 -> 3" in out
    mod.main([base, repaired], REPO)
    assert "UNBALANCED" not in capsys.readouterr().out
