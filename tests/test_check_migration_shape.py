"""Regression tests for .github/scripts/check_migration_shape.py.

Each case is either a defect a shipped form actually had or a case #108 asked
for. PR #106 carried these checks as documentation snippets that passed while
validating nothing (unfilled placeholders), called `([)]` balanced, reported a
deleted docstring as moved code, flagged migrations for deleting a
delimiter-free comment, and double-counted the module docstring.

The count assertions here are deliberate: a test that passes under the broken
implementation pins nothing, and no gate can check that for us, so the
mis-nesting case is also asserted to be *invisible* to the aggregate-count
implementation it replaced.
"""

from __future__ import annotations

import importlib.util
import subprocess
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


def _count(text, chars):
    return sum(text.count(c) for c in chars)


def _repo(tmp_path, commits):
    """A throwaway git repo: one commit per ``{path: source}`` dict, newest last.

    A value may be bytes, which is how a file with a BOM or a non-UTF-8 coding
    cookie reaches the gate intact.
    """

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, check=True, text=True
        ).stdout

    git("init", "-q", "--initial-branch=ticket")
    git("config", "user.email", "gate@example.invalid")
    git("config", "user.name", "gate")
    for files in commits:
        for name, src in files.items():
            (tmp_path / name).write_bytes(src if isinstance(src, bytes) else src.encode())
        git("add", "-A")
        git("commit", "-q", "-m", "step")
    return git("rev-parse", "HEAD").strip()


BALANCED_DOC = '"""Module docstring that spans lines\nand stays balanced (one pair here).\n"""\n'
STRANDED_DOC = '"""Module docstring that spans lines\nand ends with a stranded closer).\n"""\n'
MISNEST_DOC = '"""Module docstring that spans lines\nand mis-nests its pairs ([)] here\n"""\n'
LITERAL = 'X = (\n    "def g(x):\\n"\n    "    return g(x  # unmatched inside a literal\\n"\n)\n'


def test_identical_source_passes():
    mod = _load()
    ok, errors, warnings = _check(mod, "m.py", BALANCED_DOC, BALANCED_DOC)
    assert ok and errors == [] and warnings == []


def test_balanced_parenthetical_removal_passes():
    """Retiring an id removes a balanced pair; that is the migration working."""
    mod = _load()
    base = BALANCED_DOC + "# contract (D-013) lives there\n"
    tip = BALANCED_DOC + "# contract lives there\n"
    ok, errors, _ = _check(mod, "m.py", base, tip)
    assert ok and errors == []


def test_stranded_closer_in_docstring_fails():
    """The PR #101 defect: the closer sits on a line the edit never touched."""
    mod = _load()
    base = BALANCED_DOC + "import os\n"
    tip = STRANDED_DOC + "import os\n"
    ok, errors, _ = _check(mod, "m.py", base, tip)
    assert not ok
    assert any("UNBALANCED" in e for e in errors)
    assert any("0 -> 1" in e for e in errors)


def test_misnested_delimiters_are_a_finding():
    mod = _load()
    ok, errors, _ = _check(mod, "m.py", BALANCED_DOC, MISNEST_DOC)
    assert not ok and any("UNBALANCED" in e for e in errors)


def test_misnesting_is_invisible_to_aggregate_counts():
    """Without this, the test above would pass under the implementation it replaced.

    #106's snippet compared opener and closer totals; `([)]` has two of each, so
    it called this text balanced while the stack calls it a finding.
    """
    body = MISNEST_DOC.splitlines()[1]
    assert _count(body, "([{") == _count(body, ")]}") == 2
    assert _load().is_misnested(body) is True


def test_one_line_docstring_is_scanned():
    """The snippets skipped single-line strings and so missed this."""
    mod = _load()
    base = 'def f():\n    """Balanced (here)."""\n    return 1\n'
    tip = 'def f():\n    """A stray closer)."""\n    return 1\n'
    ok, errors, _ = _check(mod, "m.py", base, tip)
    assert not ok and any("UNBALANCED" in e for e in errors)


def test_module_docstring_counted_once():
    """ast.walk yields the root: seeding it with the tree doubled the count."""
    mod = _load()
    src = STRANDED_DOC + "def f():\n" + '    """Fn one stray)."""\n    return 1\n'
    assert len(mod.bad_blocks(src)) == 2


def test_delimiter_free_comment_deletion_does_not_flag():
    base = BALANCED_DOC + "# a plain comment with no delimiters\n# keep me\n"
    tip = BALANCED_DOC + "# keep me\n"
    mod = _load()
    ok, errors, _ = _check(mod, "m.py", base, tip)
    assert ok and errors == []


def test_fixing_a_block_is_not_a_finding():
    """Direction matters: only an increase in bad blocks is a finding."""
    mod = _load()
    ok, errors, _ = _check(mod, "m.py", STRANDED_DOC, BALANCED_DOC)
    assert ok and errors == []


def test_string_literal_change_is_code_moved():
    """A template literal is payload, not prose: the AST rule owns it.

    #106's snippet took its prose scope from `tokenize` strings, so this edit read
    as a delimiter finding — the literal's `(` and `#` are unmatched — while a
    one-line docstring escaped. The prose rule has to stay silent on it.
    """
    mod = _load()
    base = BALANCED_DOC + 'X = (\n    "def g(x):\\n    return g(x)\\n"\n)\n'
    tip = BALANCED_DOC + LITERAL
    assert mod.code_moved(base, tip) is True
    ok, errors, warnings = _check(mod, "m.py", base, tip)
    assert ok and errors == [] and len(warnings) == 1


def test_docstring_deletion_is_not_code_moved():
    """Blanking instead of removing reported this as moved code."""
    mod = _load()
    src = '"""Doc."""\ndef f():\n    """Gone next revision."""\n    return 1\n'
    tip = '"""Doc."""\ndef f():\n    return 1\n'
    assert mod.code_moved(src, tip) is False


def test_unparseable_side_is_reported_not_raised():
    """Both snippets raised a bare SyntaxError traceback here."""
    mod = _load()
    ok, errors, _ = _check(mod, "m.py", BALANCED_DOC, BALANCED_DOC + "def f(:\n    pass\n")
    assert not ok
    assert any("UNPARSEABLE" in e for e in errors)


def test_code_moved_warns_and_the_prose_rule_still_applies():
    """Moving code is what a PR is for; it buys no escape from the prose rule."""
    mod = _load()
    ok, errors, warnings = _check(mod, "m.py", BALANCED_DOC + "X = 1\n", STRANDED_DOC + "X = 2\n")
    assert not ok
    assert any("UNBALANCED" in e for e in errors)
    assert any("CODE MOVED" in w for w in warnings)
    assert len(warnings) == 1


def test_a_clean_code_change_warns_without_blocking():
    mod = _load()
    ok, errors, warnings = _check(mod, "m.py", BALANCED_DOC + "X = 1\n", BALANCED_DOC + "X = 2\n")
    assert ok and errors == []
    assert len(warnings) == 1 and "CODE MOVED" in warnings[0]


def test_blobs_are_read_from_the_merge_base_end_to_end(tmp_path):
    """The round-2 defect: main() could ignore its own merge base and stay green.

    Reading blobs from the caller's base instead of the merge base blames a stale
    branch for whatever main changed in the meantime.
    """
    mod = _load()
    calls = []

    class R:
        def __init__(self, out):
            self.stdout = out

    def runner(cmd, **kw):
        calls.append(cmd)
        if cmd[1] == "merge-base":
            return R("cafe\n")
        if cmd[1] == "diff":
            return R("m.py\0")
        if cmd[1] == "show":
            return R(b'"""Docstring (balanced)."""\nX = 1\n')
        return R("")

    assert mod.main(["base", "tip"], tmp_path, runner) == 0
    assert [c[2] for c in calls if c[1] == "show"] == ["cafe:m.py", "tip:m.py"]


def test_a_parenthetical_wrapping_two_comment_lines_is_one_block():
    """Consecutive comment lines are one prose block.

    Counting each line separately calls a legitimately wrapped pair two bad
    blocks — a false positive on honest prose, and the reason #90's range read
    2 -> 3 before grouping instead of the true 0 -> 1.
    """
    mod = _load()
    wrapped = (
        BALANCED_DOC
        + "# a rule that spans two lines (with the\n# parenthetical closing on the second)\n"
    )
    plain = BALANCED_DOC + "# a rule that spans two lines (with the parenthetical inline)\n"
    assert len(mod.bad_blocks(wrapped)) == 0
    ok, errors, _ = _check(mod, "m.py", plain, wrapped)
    assert ok and errors == []


def test_a_stranded_closer_inside_a_comment_block_still_fails():
    """Grouping must not hide the defect: the closer is still unmatched."""
    mod = _load()
    base = BALANCED_DOC + "# rule text (D-013: cited here\n# and the sentence ends)\n"
    tip = BALANCED_DOC + "# rule text\n# and the sentence ends)\n"
    ok, errors, _ = _check(mod, "m.py", base, tip)
    assert not ok and any("UNBALANCED" in e for e in errors)


def test_a_comment_on_the_first_line_is_never_a_continuation():
    """The sentinel bug: line 1 compares equal to 0 + 1.

    Docstrings are collected before the comment pass, so a file opening with a
    comment merged it onto the module docstring instead of standing alone —
    reachable here, since introspect_module.py line 1 is a comment.
    """
    mod = _load()
    src = "# a stray closer)\n" + BALANCED_DOC + "X = 1\n"
    assert mod.prose_blocks(src)[-1] == "# a stray closer)"
    assert len(mod.bad_blocks(src)) == 1


def test_a_trailing_comment_never_shares_a_block_with_its_neighbour():
    """One rule, three adjacencies: a run is made of standalone lines only.

    Two trailing lines, a standalone line under a trailing one, and a trailing
    line under a standalone one — adjacency alone would group each pair and let
    two unrelated fragments cancel out a real stray delimiter.
    """
    mod = _load()
    for src in (
        '"""Doc."""\nA = 1  # opens ( here\nB = 2  # closes ) here\n',
        '"""Doc."""\nA = 1  # opens ( here\n# closes ) here\n',
        '"""Doc."""\n# opens ( here\nB = 2  # closes ) here\n',
    ):
        assert len(mod.bad_blocks(src)) == 2


def test_an_ordinary_code_change_does_not_block(tmp_path, capsys):
    """A feature commit moves code and adds files; neither may redden prose.

    Built as a throwaway repo rather than pinned to a historical commit: the
    `test` job's checkout is not `fetch-depth: 0`, so a history-backed registry
    pin skips exactly where CI needs it to run. `new.py` arrives with an
    unbalanced delimiter on purpose — an added file is out of scope
    (`--diff-filter=M`), and dropping that filter turns this into exit 1 on a git
    error instead.
    """
    mod = _load()
    head = _repo(
        tmp_path,
        [
            {"m.py": '"""Doc."""\nX = 1\n'},
            {
                "m.py": '"""Doc."""\nX = 1\nY = 2  # a plain trailing comment\n',
                "new.py": '"""New."""\nZ = (\n',
            },
        ],
    )
    assert mod.main([f"{head}~1", head], tmp_path) == 0
    out = capsys.readouterr().out
    assert "CODE MOVED" in out and "prose intact" in out and "::error" not in out


def test_a_real_feature_commit_warns_but_does_not_block(capsys):
    """The same shape as the commit that first proved it: #104's first validator.

    Skips where the object is absent, which is why the registry pins the
    throwaway-repo test above rather than this one.
    """
    tip = "61476bda1dd1e54ef58280e00a48ffab859ea069"
    if subprocess.run(["git", "cat-file", "-e", tip], cwd=REPO).returncode != 0:
        import pytest

        pytest.skip(f"{tip} is not in this clone")
    mod = _load()
    assert mod.main([f"{tip}~1", tip], REPO) == 0
    out = capsys.readouterr().out
    assert "CODE MOVED" in out and "::error" not in out
    assert "prose intact" in out


def test_paths_are_filtered_to_modified_python_files():
    """A blob existing on one side only has nothing to compare: it crashed on a
    merged new module rather than being skipped."""
    mod = _load()
    calls = []

    def runner(cmd, **kw):
        calls.append((cmd, kw))

        class R:
            stdout = "a.py\0docs/note.md\0b.py\0"

        return R()

    paths = mod.python_paths(runner, "base", "tip", REPO)
    assert paths == ["a.py", "b.py"]
    assert "--diff-filter=M" in calls[0][0]
    assert "-z" in calls[0][0]
    assert calls[0][1]["check"] is True
    assert calls[0][1]["cwd"] == REPO


def test_a_quoted_path_reaches_the_check(tmp_path, capsys):
    """Git quotes any path with non-ASCII bytes; whitespace splitting dropped it.

    A dropped file is a silent pass, which is the failure this ticket exists to
    close, so this runs on a real repo instead of a mocked runner: the old
    ``split()`` returns no paths here and the gate exits 0 saying it had nothing
    to compare.
    """
    mod = _load()
    head = _repo(
        tmp_path,
        [
            {"clean.py": '"""Doc."""\nX = 1\n', "咖啡.py": '"""Doc."""\nX = 1\n'},
            {"clean.py": '"""Doc."""\nX = 2\n', "咖啡.py": '"""Doc (unmatched.\n"""\nX = 2\n'},
        ],
    )
    assert mod.main([f"{head}~1", head], tmp_path) == 1
    out = capsys.readouterr().out
    assert "咖啡.py" in out and "UNBALANCED" in out
    assert "file=clean.py::UNBALANCED" not in out


def test_a_bom_and_a_coding_cookie_are_not_findings(tmp_path, capsys):
    """Two inputs Python accepts, both of which the strict UTF-8 decode rejected.

    A BOM decodes fine and then dies in `ast.parse` on U+FEFF, which the gate
    would report as UNPARSEABLE; a PEP 263 cookie with non-UTF-8 bytes raised
    UnicodeDecodeError straight out of `main`. Either file can be edited by a
    migration that touches no delimiter at all.
    """
    mod = _load()
    cookie = b"# -*- coding: latin-1 -*-\nX = 'caf\xe9'"
    head = _repo(
        tmp_path,
        [
            {"bom.py": "\ufeff" + BALANCED_DOC + "X = 1\n", "latin.py": cookie + b"\n"},
            {
                "bom.py": "\ufeff" + BALANCED_DOC + "X = 2\n",
                "latin.py": cookie + b"\nY = 2\n",
            },
        ],
    )
    assert mod.main([f"{head}~1", head], tmp_path) == 0
    out = capsys.readouterr().out
    assert "UNPARSEABLE" not in out and "::error" not in out


def test_blobs_come_from_the_merge_base():
    """A stale branch must not be blamed for what main changed meanwhile."""
    mod = _load()
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)

        class R:
            stdout = "deadbeef\n"

        return R()

    assert mod.merge_base(runner, "base", "tip", REPO) == "deadbeef"
    assert calls[0][:3] == ["git", "merge-base", "base"]


def test_main_requires_two_revisions(capsys):
    mod = _load()
    assert mod.main([], REPO) == 1
    assert "usage" in capsys.readouterr().out.lower()


def test_main_fails_loud_on_a_bad_revision(capsys):
    """Unchecked return codes made a typo'd revision validate nothing."""
    mod = _load()
    assert mod.main(["definitely-not-a-rev-1", "definitely-not-a-rev-2"], REPO) == 1
    assert "::error::" in capsys.readouterr().out


def test_main_skips_an_all_zero_base(capsys):
    """First push of a branch: no prior state exists to compare, as in the guard."""
    mod = _load()
    assert mod.main(["0" * 40, "HEAD"], REPO) == 0
    assert "skipped" in capsys.readouterr().out


def test_main_passes_a_range_that_touches_no_python(capsys):
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=REPO
    ).stdout.strip()
    mod = _load()
    assert mod.main([head, head], REPO) == 0
    assert "no Python files modified" in capsys.readouterr().out


def test_main_reports_a_real_defect_and_a_real_repair(capsys):
    """The numbers the prose quoted drifted twice, so they live here instead.

    c65e457..1d785a4 is #90's commit that stranded a ")" in pipeline.py;
    c65e457..feb6317 is #90's repaired tip. pytest runs in the `test` job, whose
    checkout is not fetch-depth: 0 — the `lint` job that runs the gate itself is —
    so on a shallow runner this skips and says so, instead of pretending the
    historical evidence ran.
    """
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
    assert "pipeline.py" in out and "0 -> 1" in out
    assert mod.main([base, repaired], REPO) == 0
    assert "UNBALANCED" not in capsys.readouterr().out
