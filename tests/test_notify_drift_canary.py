"""Regression tests for .github/scripts/notify_drift_canary.py.

D-123: the drift canary's red must land as a persistent issue —
dedup create on first red, comment on continued reds, comment+close
on recovery; notify failures exit non-zero but never mask the drift
red (the job carries continue-on-error).
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "notify_drift_canary.py"


def _load():
    spec = importlib.util.spec_from_file_location("notify_drift_canary", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class FakeGh:
    """Records gh argv calls; serves canned `issue list` output."""

    def __init__(self, issues=None):
        self.issues = issues or []
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        if "list" in argv:
            return json.dumps(self.issues)
        return ""


def _env(result, **kw):
    env = {
        "DRIFT_RESULT": result,
        "REPO": "o/r",
        "DRIFT_WORKFLOW": "CI",
        "BRANCH": "main",
        "RUN_URL": "https://gh/run/1",
        "RUN_ATTEMPT": "1",
        "GITHUB_SHA": "abc123",
    }
    env.update(kw)
    return env


def test_red_creates_issue_when_none_open():
    mod = _load()
    gh = FakeGh()
    rc = mod.notify(_env("failure"), runner=gh)
    assert rc == 0
    creates = [c for c in gh.calls if "create" in c]
    assert len(creates) == 1
    assert mod.MARKER in creates[0][creates[0].index("--title") + 1]


def test_second_red_comments_not_creates():
    mod = _load()
    gh = FakeGh(issues=[{"number": 7, "title": mod.title_for("CI", "main")}])
    rc = mod.notify(_env("failure"), runner=gh)
    assert rc == 0
    assert not any("create" in c for c in gh.calls)
    comments = [c for c in gh.calls if "comment" in c]
    assert len(comments) == 1 and "7" in comments[0]


def test_dedup_scoped_to_lane():
    """An open canary issue for ANOTHER workflow/branch does not dedup."""
    mod = _load()
    gh = FakeGh(issues=[{"number": 9, "title": mod.title_for("CI", "dev")}])
    rc = mod.notify(_env("failure", BRANCH="main"), runner=gh)
    assert rc == 0
    assert any("create" in c for c in gh.calls)


def test_green_closes_open_issue():
    mod = _load()
    gh = FakeGh(issues=[{"number": 5, "title": mod.title_for("CI", "main")}])
    rc = mod.notify(_env("success"), runner=gh)
    assert rc == 0
    verbs = [v for c in gh.calls for v in c if v in ("comment", "close")]
    assert "comment" in verbs and "close" in verbs


def test_green_no_issue_is_clean_noop():
    mod = _load()
    gh = FakeGh()
    rc = mod.notify(_env("success"), runner=gh)
    assert rc == 0
    assert all("close" not in c and "create" not in c for c in gh.calls)


def test_skipped_result_is_noop():
    mod = _load()
    gh = FakeGh()
    assert mod.notify(_env("skipped"), runner=gh) == 0
    assert gh.calls == []


def test_missing_repo_errors():
    mod = _load()
    env = _env("failure")
    del env["REPO"]
    rc = mod.notify(env, runner=FakeGh())
    assert rc == 2


def test_dry_run_mutates_nothing():
    mod = _load()
    gh = FakeGh()
    rc = mod.notify(_env("failure", DRY_RUN="1"), runner=gh)
    assert rc == 0
    assert not any("create" in c for c in gh.calls)


def test_issue_body_carries_obligation_pointer():
    mod = _load()
    body = mod._issue_body(_env("failure"), FakeGh())
    assert "human review record" in body and "v1.0.0 release checklist" in body


def test_red_body_lists_failed_steps():
    """RUN_ID set → issue body carries job:step failure attribution."""
    mod = _load()
    jobs = {
        "jobs": [
            {
                "name": "resolution-drift canary (D-112)",
                "steps": [
                    {"name": "pytest", "conclusion": "success"},
                    {
                        "name": "PyPI index dual-source probe",
                        "conclusion": "failure",
                    },
                ],
            }
        ]
    }
    gh = FakeGh()
    orig = gh.__call__

    def runner(argv):
        if "api" in argv:
            gh.calls.append(argv)
            return json.dumps(jobs)
        return orig(argv)

    rc = mod.notify(_env("failure", RUN_ID="4242"), runner=runner)
    assert rc == 0
    creates = [c for c in gh.calls if "create" in c]
    assert len(creates) == 1
    body = creates[0][creates[0].index("--body") + 1]
    assert "PyPI index dual-source probe" in body


def test_forwarded_step_names_never_carry_a_ledger_id():
    """The workflow's own job and step names still hold ledger ids, and this
    body republishes them into a public issue. The worktree fix is the strip in
    failed_steps; this pins that the issue never shows one, without requiring
    the workflow file to be edited here."""
    mod = _load()
    jobs = {
        "jobs": [
            {
                "name": "resolution-drift canary (D-112)",
                "steps": [
                    {"name": "gate-registry consistency (D-215)", "conclusion": "failure"},
                    {"name": "resolve latest tag (D-142γ probe input)", "conclusion": "failure"},
                ],
            }
        ]
    }

    def runner(argv):
        if "api" in argv:
            return json.dumps(jobs)
        return ""

    body = mod._issue_body(_env("failure", RUN_ID="4242"), runner)
    assert "resolution-drift canary" in body, "the name survives, only the tag goes"
    assert "gate-registry consistency" in body
    assert "resolve latest tag" in body
    assert not re.search(r"D-\d+", body), body


def test_branch_and_workflow_labels_are_stripped_too():
    """The issue title and body carry the branch and workflow labels, and a
    branch name is free-form text a contributor controls. Both are stripped, and
    an id with no parentheses around it must go as well."""
    mod = _load()
    env = _env("failure", DRIFT_WORKFLOW="CI (D-112)", BRANCH="fix/D-215-pins")
    body = mod._issue_body(env, FakeGh())
    assert not re.search(r"D-\d+", body), body
    assert "fix/" in body, "the branch keeps its shape, only the id goes"
    title = mod.title_for("CI (D-112)", "fix/D-215-pins")
    assert not re.search(r"D-\d+", title), title
    assert "resolution-drift red" in title


def test_failed_step_lookup_failure_does_not_break_notify():
    """Attribution is best-effort: a broken api call still files the issue."""
    mod = _load()

    def runner(argv):
        if "api" in argv:
            raise subprocess.CalledProcessError(1, argv, stderr="boom")
        if "list" in argv:
            return "[]"
        return ""

    rc = mod.notify(_env("failure", RUN_ID="4242"), runner=runner)
    assert rc == 0


def test_no_run_id_skips_attribution():
    mod = _load()
    gh = FakeGh()
    rc = mod.notify(_env("failure"), runner=gh)
    assert rc == 0
    assert not any("api" in c for c in gh.calls)
