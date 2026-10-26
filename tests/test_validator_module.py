"""Issue #79 Maya-side contract tests: custom scene_review validators.

Runs against the stub-bound _mcp_scene injection unit (monolith + both
fragments assembled - maya_env.module), the same seam
tests/test_introspect_module.py uses. These are CONTRACT tests: the
registration surface, the score separation and the check_error isolation
must hold in real Maya, where the predicate runs against a live DG.
"""

from __future__ import annotations

import json

import maya_stub
import pytest


@pytest.fixture
def env(maya_env):
    return maya_env


class TestRegistration:
    def test_register_list_unregister(self, env):
        assert env.module.list_validators()["count"] == 0

        out = env.module.register_validator("has_root", lambda: True)
        assert out["registered"] == "has_root"
        assert out["overwritten"] is False
        assert out["count"] == 1

        listed = env.module.list_validators()
        assert listed["count"] == 1
        assert listed["validators"][0] == {
            "name": "has_root",
            "family": "custom",
            "severity": "error",
            "weight": 1.0,
            "message": None,
        }

        assert env.module.unregister_validator("has_root")["count"] == 0
        assert env.module.list_validators()["validators"] == []

    def test_same_name_overwrites(self, env):
        env.module.register_validator("n", lambda: True, severity="warning")
        out = env.module.register_validator("n", lambda: False, severity="error")

        assert out["overwritten"] is True, "re-registering must replace, not duplicate"
        assert out["count"] == 1
        assert env.module.list_validators()["validators"][0]["severity"] == "error"
        assert env.module.custom_validator_results()["score"] == 0.0

    def test_unregister_unknown_is_error(self, env):
        res = env.module.unregister_validator("ghost")
        assert res["error"]["code"] == "validator_not_found"

    @pytest.mark.parametrize(
        "kwargs,code",
        [
            ({"name": "", "predicate": lambda: True}, "invalid_validator_name"),
            ({"name": "x", "predicate": 42}, "invalid_predicate"),
            ({"name": "x", "predicate": lambda: True, "severity": "fatal"}, "invalid_severity"),
            ({"name": "x", "predicate": lambda: True, "weight": 0}, "invalid_weight"),
            ({"name": "x", "predicate": lambda: True, "weight": float("nan")}, "invalid_weight"),
            ({"name": "x", "predicate": lambda: True, "weight": float("inf")}, "invalid_weight"),
            ({"name": "x", "predicate": lambda: True, "family": " "}, "invalid_family"),
        ],
    )
    def test_rejects_bad_registration_without_storing_it(self, env, kwargs, code):
        res = env.module.register_validator(**kwargs)
        assert res["error"]["code"] == code
        assert env.module.list_validators()["count"] == 0

    def test_a_non_finite_weight_cannot_poison_the_json(self, env):
        """NaN is not valid JSON: one bad registration must not cost the whole
        scene_review answer to the caller's parser."""
        env.module.register_validator("ok", lambda: True)
        env.module.register_validator("bad", lambda: True, weight=float("nan"))

        custom = env.module.custom_validator_results()
        assert json.dumps(custom)  # the section the tool would return
        assert custom["max_score"] == 1.0

    def test_info_is_a_severity_the_builtin_already_uses(self, env):
        env.module.register_validator("note", lambda: False, severity="info")
        issue = env.module.custom_validator_results()["issues"][0]
        assert issue["severity"] == "info"

    def test_the_name_is_stripped_to_one_identity(self, env):
        """ "x" and " x " are the same overwrite key, not two rules that both
        run and then cannot be unregistered by the name they display."""
        env.module.register_validator("  x  ", lambda: True)
        assert [v["name"] for v in env.module.list_validators()["validators"]] == ["x"]
        assert env.module.register_validator("x", lambda: False)["overwritten"] is True
        assert env.module.unregister_validator("  x  ")["count"] == 0

    def test_a_registry_that_would_overflow_is_refused(self, env):
        """Two finite 1e308 weights sum to inf, and an Infinity score costs
        the caller the whole JSON answer - so the sum is checked, not just
        each input."""
        env.module.register_validator("huge", lambda: True, weight=1e308)
        res = env.module.register_validator("more", lambda: True, weight=1e308)
        assert res["error"]["code"] == "weight_overflow"
        assert env.module.list_validators()["count"] == 1
        section = json.loads(json.dumps(env.module.custom_validator_results()))
        assert section["max_score"] == 1e308

    def test_an_overwrite_is_not_charged_twice_by_the_size_guard(self, env):
        """A re-registration replaces its own weight, so the total after it is
        one 1e308 - finite - and 'same name overwrites' cannot quietly narrow
        at the top of the range."""
        env.module.register_validator("big", lambda: True, weight=1e308)
        res = env.module.register_validator("big", lambda: True, weight=1e308)
        assert res["overwritten"] is True
        assert env.module.list_validators()["count"] == 1

    def test_a_weight_past_the_float_range_is_refused_not_raised(self, env):
        for bad in (10**400, float("inf")):
            res = env.module.register_validator("x", lambda: True, weight=bad)
            assert res["error"]["code"] == "invalid_weight", repr(bad)
        assert env.module.list_validators()["count"] == 0

    def test_unregister_demands_the_same_name_contract_as_register(self, env):
        """A non-string must return the envelope, not raise TypeError out of
        a dict lookup."""
        for bad in (["x"], 42, "", None):
            res = env.module.unregister_validator(bad)
            assert res["error"]["code"] == "invalid_validator_name", repr(bad)


class TestPredicateResults:
    def test_bool_result(self, env):
        env.module.register_validator("passing", lambda: True)
        env.module.register_validator("failing", lambda: False)

        custom = env.module.custom_validator_results()
        by_name = {v["name"]: v for v in custom["validators"]}
        assert by_name["passing"]["status"] == "passed"
        assert by_name["failing"]["status"] == "failed"
        assert [i["name"] for i in custom["issues"]] == ["failing"]

    def test_a_failing_rule_with_no_message_still_reports_one(self, env):
        """The registered message applies to failures too; a None msg would
        reach JSON as null and print "None" in the compact view."""
        env.module.register_validator("silent", lambda: False)
        custom = env.module.custom_validator_results()
        assert custom["validators"][0]["message"] == "silent failed"
        assert custom["issues"][0]["msg"] == "silent failed"

    def test_tuple_result_carries_its_own_message(self, env):
        env.module.register_validator(
            "tuple", lambda: (False, "3 objects over the budget"), message="fallback"
        )
        issue = env.module.custom_validator_results()["issues"][0]
        assert issue["msg"] == "3 objects over the budget"

    def test_registration_message_is_the_fallback(self, env):
        env.module.register_validator("bare", lambda: False, message="studio rule broken")
        issue = env.module.custom_validator_results()["issues"][0]
        assert issue["msg"] == "studio rule broken"

    def test_uninterpretable_result_is_check_error(self, env):
        env.module.register_validator("wrong_shape", lambda: "yes")
        result = env.module.custom_validator_results()["validators"][0]
        assert result["status"] == "check_error"
        assert result["passed"] is False
        assert "expected bool or (bool, message)" in result["message"]

    def test_a_pair_flag_must_be_a_bool_too(self, env):
        """(1, msg) must not pass where a bare 1 is refused - one rule, both
        shapes."""
        env.module.register_validator("truthy_pair", lambda: (1, "not a bool"))
        result = env.module.custom_validator_results()["validators"][0]
        assert result["status"] == "check_error"
        assert result["passed"] is False


class TestErrorIsolation:
    def test_raising_validator_does_not_abort_the_review(self, env):
        env.scene.add_mesh("GEO_box")

        def boom():
            raise RuntimeError("kaboom in maya")

        env.module.register_validator("boom", boom, severity="warning")
        env.module.register_validator("ok", lambda: True)

        res = env.module.scene_review_with_validators()

        custom = res["custom"]
        by_name = {v["name"]: v for v in custom["validators"]}
        assert by_name["boom"]["status"] == "check_error", "an exception is a check_error"
        assert by_name["boom"]["passed"] is False
        assert "RuntimeError: kaboom in maya" in by_name["boom"]["message"]
        assert by_name["ok"]["status"] == "passed", "the run continued past the raiser"

        # The severity a rule asked for is the severity it reports with, crash
        # included - status is what says it broke rather than merely failed.
        issue = next(i for i in custom["issues"] if i["name"] == "boom")
        assert issue["severity"] == "warning"
        assert issue["check"] == "custom"

        # The built-in audit ran to completion regardless.
        assert "spatial" in res["checks"]
        assert res["score"] > 0

    def test_a_predicate_that_unregisters_itself_does_not_abort_the_run(self, env):
        """The registry is snapshotted before the run. Iterating the live dict
        let a one-shot rule raise RuntimeError mid-iteration - outside the
        per-validator isolation - and lose the whole audit."""

        def one_shot():
            env.module.unregister_validator("one_shot")
            return True

        env.module.register_validator("one_shot", one_shot)
        env.module.register_validator("later", lambda: True)

        res = env.module.scene_review_with_validators()
        assert [v["name"] for v in res["custom"]["validators"]] == ["one_shot", "later"]
        assert res["custom"]["score"] == res["custom"]["max_score"] == 2.0
        assert res["checks"], "the built-in audit survived the mutation"
        assert env.module.list_validators()["count"] == 1, "the one-shot rule is gone"


class TestScoreSeparation:
    def test_custom_section_shape_and_own_score(self, env):
        env.module.register_validator("a", lambda: True, weight=3)
        env.module.register_validator("b", lambda: False, weight=1)

        custom = env.module.custom_validator_results()
        assert custom["max_score"] == 4.0
        assert custom["score"] == 3.0, "3 of the 4 registered weight points earned"
        assert custom["score"] <= custom["max_score"], "one denominator, readable as 3/4"
        assert set(custom) == {"validators", "score", "max_score", "issues"}

    def test_all_pass_scores_at_the_ceiling(self, env):
        env.module.register_validator("a", lambda: True, weight=2.5)
        env.module.register_validator("b", lambda: (True, None))
        custom = env.module.custom_validator_results()
        assert custom["score"] == custom["max_score"] == 3.5
        assert custom["issues"] == []

    def test_builtin_score_is_unchanged(self, env):
        env.scene.add_mesh("GEO_box")
        baseline = env.module.scene_review()

        env.module.register_validator("harsh", lambda: False, weight=100)
        res = env.module.scene_review_with_validators()

        assert res["score"] == baseline["score"], "the built-in 0-100 must not move"
        assert res["checks"] == baseline["checks"]
        assert res["total_issues"] == baseline["total_issues"]
        assert res["custom"]["score"] == 0.0

    def test_custom_section_omitted_when_nothing_registered(self, env):
        assert env.module.custom_validator_results() is None
        res = env.module.scene_review_with_validators()
        assert "custom" not in res
        assert "score" in res

    def test_checks_argument_passes_through(self, env):
        env.module.register_validator("v", lambda: True)
        res = env.module.scene_review_with_validators(["spatial"])
        assert list(res["checks"]) == ["spatial"]
        assert res["custom"]["score"] == res["custom"]["max_score"] == 1.0


class TestSessionScope:
    def test_registry_clears_on_reinjection(self, env):
        """Replacing the module gives a fresh namespace, so the registry is
        gone - the contract behind "re-register after a reconnect"."""
        env.module.register_validator("v", lambda: True)
        assert env.module.list_validators()["count"] == 1

        reinjected = maya_stub.load_scene_module()
        assert reinjected.list_validators()["count"] == 0
        assert reinjected.custom_validator_results() is None
