"""Issue #79 Maya-side contract tests: custom scene_review validators.

Most run against the stub-bound _mcp_scene injection unit (monolith + both
fragments assembled - maya_env.module), the same seam
tests/test_introspect_module.py uses. Those are CONTRACT tests: the
registration surface, the score separation and the check_error isolation must
hold in real Maya, where the predicate runs against a live DG. The last class
pins the scene-review skill card against this fragment - mostly through the
same stub seam, and never against a real Maya.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
from pathlib import Path

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

    def test_the_stored_weight_is_the_one_the_guard_checked(self, env):
        """float() runs once and that value is what the registry holds. A
        weight object free to return a different number per call would let the
        size guard approve one value and the section print another."""

        class Flipping(int):
            turns = 0

            def __float__(self):
                Flipping.turns += 1
                return 1.0 if Flipping.turns == 1 else 1e308

        res = env.module.register_validator("f", lambda: True, weight=Flipping(1))
        assert "registered" in res
        assert env.module.list_validators()["validators"][0]["weight"] == 1.0
        assert Flipping.turns == 1, "converted once - the checked value is stored"

    def test_the_sign_check_runs_on_the_stored_value(self, env):
        """A float subclass can compare positive and convert negative. The
        registry holds floats, so every rule - sign, finiteness, size - is
        decided on the converted number, not on whatever the caller passed."""

        class Neg(float):
            def __float__(self):
                return -1.7e308

        res = env.module.register_validator("n", lambda: False, weight=Neg(1.7e308))
        assert res["error"]["code"] == "invalid_weight"
        assert env.module.list_validators()["count"] == 0

    def test_a_weight_that_refuses_to_convert_returns_the_envelope(self, env):
        class Loud(float):
            def __float__(self):
                raise TypeError("no number here")

        for bad in (Loud(1.0),):
            res = env.module.register_validator("l", lambda: True, weight=bad)
            assert res["error"]["code"] == "invalid_weight"
        assert env.module.list_validators()["count"] == 0

    def test_severity_membership_does_not_ask_its_own_permission(self, env):
        """`severity in _VALIDATOR_SEVERITIES` alone calls the object's
        __eq__, so anything claiming to be "error" would be stored verbatim and
        then make json.dumps of the whole report fail."""

        class EqError:
            def __eq__(self, other):
                return other in ("error", "warning", "info")

            def __hash__(self):
                return 1

        res = env.module.register_validator("evil", lambda: False, severity=EqError())
        assert res["error"]["code"] == "invalid_severity"
        assert env.module.list_validators()["count"] == 0

    def test_a_lying_strip_cannot_poison_the_registry_key(self, env):
        class BadStrip(str):
            def strip(self, *_args):
                return ["not a string"]

        res = env.module.register_validator(BadStrip("x"), lambda: True)
        assert res["error"]["code"] == "invalid_validator_name"
        assert env.module.list_validators()["count"] == 0

    @pytest.mark.parametrize(
        "order",
        [
            (7.98336123813888e291, 1.7976931348623157e308, 7.98336123813888e291),
            (1.7976931348623157e308, 7.98336123813888e291, 7.98336123813888e291),
            (7.98336123813888e291, 7.98336123813888e291, 1.7976931348623157e308),
        ],
    )
    def test_the_guard_and_the_report_agree_on_the_total(self, env, order):
        """sum() is order-dependent (the builtin compensates on CPython 3.12+,
        Maya 2023's 3.9 does not), so a guard using it accepted this registry
        while the report printed Infinity - invalid JSON. The exact fsum total
        is what both sides use, so the third weight is refused in every order
        and no accepted registry can emit an unparsable section."""
        accepted = []
        for index, weight in enumerate(order):
            res = env.module.register_validator(f"v{index}", lambda: True, weight=weight)
            if "error" in res:
                assert res["error"]["code"] == "weight_overflow", res
                break
            accepted.append(index)
        assert accepted == [0, 1], "two of these weights fit, all three do not"
        json.dumps(env.module.custom_validator_results(), allow_nan=False)

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

    def test_an_exception_that_cannot_be_stringified_stays_isolated(self, env):
        """Building the check_error line calls str() on the exception inside
        the except block - outside that block's own protection. An exception
        with a hostile __str__ used to escape and cost the caller the whole
        audit, which is the one thing criterion 4 promises cannot happen."""

        class Unstringable:
            def __str__(self):
                raise ValueError("cannot stringify")

        def loud():
            raise RuntimeError(Unstringable())

        env.scene.add_mesh("GEO_box")
        env.module.register_validator("loud", loud)
        env.module.register_validator("sibling", lambda: True)

        res = env.module.scene_review_with_validators()
        by_name = {v["name"]: v for v in res["custom"]["validators"]}
        assert by_name["loud"]["status"] == "check_error"
        assert by_name["sibling"]["status"] == "passed", "the run continued"
        assert res["checks"], "the built-in audit was not lost"
        json.dumps(res, allow_nan=False)

    def test_a_message_that_cannot_be_stringified_stays_isolated(self, env):
        class Unstringable:
            def __str__(self):
                raise TypeError("no")

        env.module.register_validator("odd", lambda: (False, Unstringable()))
        result = env.module.custom_validator_results()["validators"][0]
        assert result["status"] == "failed"
        assert "raised in __str__" in result["message"]
        json.dumps(env.module.custom_validator_results(), allow_nan=False)

    def test_reading_the_result_is_part_of_the_isolation(self, env):
        """len() and [] on what a predicate returned are caller code too. A
        sequence free to lie about its shape used to escape the except block
        and break every later review, registry left intact."""

        class LyingLen(tuple):
            __len__ = lambda self: 2  # noqa: E731 - the lie is the point

        class RaisingGet(tuple):
            def __getitem__(self, i):
                if i == 0:
                    return True
                raise RuntimeError("no second half")

        class RaisingLen(tuple):
            def __len__(self):
                raise RuntimeError("no length")

        env.scene.add_mesh("GEO_box")
        env.module.register_validator("empty_pair", lambda: LyingLen(()))
        env.module.register_validator("no_second", lambda: RaisingGet((True, "note")))
        env.module.register_validator("no_length", lambda: RaisingLen((True, "note")))
        env.module.register_validator("sibling", lambda: True)

        res = env.module.scene_review_with_validators()
        by_name = {v["name"]: v for v in res["custom"]["validators"]}
        assert [by_name[n]["status"] for n in ("empty_pair", "no_second", "no_length")] == [
            "check_error"
        ] * 3
        assert by_name["sibling"]["status"] == "passed"
        assert res["checks"], "the built-in audit was not lost"
        json.dumps(res, allow_nan=False)

    def test_a_refusal_needs_no_cooperation_from_the_value_it_refuses(self, env):
        """The envelope message names what was rejected, so the formatting of
        that object cannot be allowed to raise out of the rejection."""

        class NoRepr:
            def __repr__(self):
                raise RuntimeError("no repr")

            def __str__(self):
                raise RuntimeError("no str")

        for field in ("severity", "family"):
            res = env.module.register_validator("n", lambda: True, **{field: NoRepr()})
            assert res["error"]["code"] == f"invalid_{field}", field
        res = env.module.register_validator("n", lambda: True, weight=NoRepr())
        assert res["error"]["code"] == "invalid_weight"
        assert env.module.list_validators()["count"] == 0


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


class TestCardPinsTheShippedApi:
    """The scene-review card carries the validator recipe so nobody has to open
    this fragment to use it, and `skills/` sits outside check_doc_claims.py's
    surfaces - so these are the only assertions that go red when card and code
    drift apart.

    Bounds, stated because a pin that cannot fail is worse than none.
    Codes are compared inside the shapes this API names refusals with
    (`invalid_*`, `weight_overflow`, `validator_not_found`), so a code named
    otherwise slips past. The two vocabularies demand an *enumeration* - a
    `|`-separated run whose membership equals the shipped set - so a stale line
    fails even while the words still appear in prose somewhere else, but a
    second passing mention of one of those words is not itself checked. And two
    claims stay one-way, that the card names what the API returns: the section's
    own `score`/`max_score`, which the card writes as a fraction in running
    prose, and the refusal's `suggestion`, which it names outside the printed
    envelope. Two more, for completeness: each anchor reads the *first* place it
    matches, so a contradictory bullet appended below the pinned ones would not
    be seen; and defaults are compared by value, so a `1` and a `1.0` are the
    same default to this class. Restyled prose is a loud failure, not a silent
    pass: every anchor this class reads asserts it was found.
    """

    ROOT = Path(__file__).resolve().parents[1]
    CARD = ROOT / "skills" / "scene-review-playbook" / "SKILL.md"
    FRAGMENT = ROOT / "src" / "maya_mcp_server" / "validator_module.py"
    CODE_SHAPE = re.compile(r"\b(invalid_[a-z_]+|weight_overflow|validator_not_found)\b")
    QUOTED_RUN = re.compile(r"(?:[\"'`][\w_]+[\"'`]\s*\|\s*)+[\"'`][\w_]+[\"'`]")
    QUOTED_WORD = re.compile(r"[\"'`]([\w_]+)[\"'`]")
    ENVELOPE_KEY = re.compile(r'["`]([\w_]+)["`]\s*:')
    ROW_SHAPE = re.compile(r"`(\w+)` \(each with([^)]*)\)")
    ROW_KEY = re.compile(r"`(\w+)")
    SIGNATURE = re.compile(r"`(\w+)\(([^)]*)\)`")

    def _card(self) -> str:
        """The card, read once per test: nothing here writes to it."""
        return self.CARD.read_text(encoding="utf-8")

    def _names(self, card: str, word: str) -> bool:
        """A word counts as documented when the card quotes it, any style."""
        return f"`{word}`" in card or f'"{word}"' in card or f"'{word}'" in card

    def _enumerations(self, card: str) -> list[set[str]]:
        """Every `|`-separated run of quoted words the card carries, as sets."""
        runs = self.QUOTED_RUN.finditer(card)
        return [set(self.QUOTED_WORD.findall(run.group())) for run in runs]

    def _printed_envelope(self, card: str, marker: str) -> tuple[set[str], set[str]]:
        """Keys the card prints outside and inside the first `[...]` of the
        envelope introducing `marker`, so a key moved between levels fails."""
        at = card.find(marker)
        assert at != -1, f"the card no longer introduces {marker!r} - re-aim this pin"
        line = card[at : card.find("\n", at)]
        head, bracket, rest = line.partition("[")
        inner = rest.partition("]")[0] if bracket else ""
        printed = (set(self.ENVELOPE_KEY.findall(head)), set(self.ENVELOPE_KEY.findall(inner)))
        assert printed[0], f"no envelope printed for {marker!r} - re-aim this pin"
        return printed

    def _codes_emitted(self) -> set[str]:
        tree = ast.parse(self.FRAGMENT.read_text(encoding="utf-8"))
        codes = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_val_err"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                codes.add(str(node.args[0].value))
        return codes

    def test_the_card_names_every_code_and_invents_no_other(self):
        emitted = self._codes_emitted()
        documented = set(self.CODE_SHAPE.findall(self._card()))
        assert documented == emitted, (
            f"card omits {sorted(emitted - documented)}; "
            f"card names codes nothing emits: {sorted(documented - emitted)}"
        )

    def test_the_card_prints_the_shipped_signatures(self, env):
        """The one thing a caller copies verbatim: parameter names and defaults."""
        card = self._card()
        for name in ("register_validator", "list_validators", "unregister_validator"):
            printed = next((body for fn, body in self.SIGNATURE.findall(card) if fn == name), None)
            assert printed is not None, f"the card no longer prints {name}() - re-aim this pin"
            parts = [part.strip() for part in printed.split(",") if part.strip()]
            signature = inspect.signature(getattr(env.module, name))
            names = [part.split("=")[0] for part in parts]
            assert names == list(signature.parameters), f"{name}: params {names}"
            shown = {p.split("=")[0]: p.split("=", 1)[1].strip(" \"'") for p in parts if "=" in p}
            shipped = {
                p.name: str(p.default)
                for p in signature.parameters.values()
                if p.default is not inspect.Parameter.empty
            }
            assert shown == shipped, f"{name}: defaults {shown} != shipped {shipped}"

    def test_the_card_names_every_key_the_api_returns(self, env):
        card = self._card()
        env.module.register_validator("ok", lambda: True)
        env.module.register_validator("broken", lambda: 1 / 0)
        custom = env.module.custom_validator_results()
        registered = env.module.register_validator("again", lambda: True)
        listed = env.module.list_validators()
        unregistered = env.module.unregister_validator("again")
        refused = env.module.unregister_validator("never_registered")

        returned = (
            set(registered)
            | set(listed)
            | set(unregistered)
            | set(listed["validators"][0])
            | set(refused)
            | set(refused["error"])
            | set(custom)
            | set(custom["validators"][0])
            | set(custom["issues"][0])
        )
        undocumented = {key for key in returned if not self._names(card, key)}
        assert not undocumented, f"the API returns, the card never names: {sorted(undocumented)}"

    def test_the_card_prints_no_key_the_api_does_not_return(self, env):
        """The direction a reader acts on: an envelope the card promises and
        the API no longer hands back is the lie that costs the whole call."""
        card = self._card()
        env.module.register_validator("card_pin", lambda: True)
        listed = env.module.list_validators()
        registered = env.module.register_validator("card_pin", lambda: False)
        unregistered = env.module.unregister_validator("card_pin")

        returned = {
            "- `register_validator(name": (set(registered), set()),
            "- `list_validators()`": (set(listed), set(listed["validators"][0])),
            "- `unregister_validator(name": (set(unregistered), set()),
        }
        for marker, keys in returned.items():
            assert self._printed_envelope(card, marker) == keys, f"{marker} drifted from the API"

    def test_the_card_prints_no_key_the_refusal_does_not_return(self, env):
        refused = env.module.unregister_validator("never_registered")
        returned = set(refused) | set(refused["error"])
        printed, _ = self._printed_envelope(self._card(), "A bad argument is refused")
        assert printed <= returned, (
            f"envelope keys the card promises and no refusal returns: {sorted(printed - returned)}"
        )

    def test_the_card_lists_exactly_the_keys_the_custom_rows_carry(self, env):
        env.module.register_validator("ok", lambda: True)
        env.module.register_validator("broken", lambda: 1 / 0)
        custom = env.module.custom_validator_results()
        rows = {"validators": set(custom["validators"][0]), "issues": set(custom["issues"][0])}

        shapes = dict(self.ROW_SHAPE.findall(self._card()))
        assert set(rows) <= set(shapes), (
            "the card no longer writes `custom`'s rows as `(each with …)` - re-aim this pin"
        )

        for label, body in shapes.items():
            if label not in rows:
                continue
            # The status enumeration sits inside the validators run, so it is
            # dropped before the keys are read; `check: "custom"` still yields
            # its key because a key is whatever opens a backtick.
            keys = set(self.ROW_KEY.findall(self.QUOTED_RUN.sub("", body)))
            assert keys == rows[label], f"`custom.{label}` drifted: {sorted(keys ^ rows[label])}"

    def test_the_card_enumerates_the_severities_the_api_accepts(self, env):
        accepted = set(env.module._VALIDATOR_SEVERITIES)
        assert accepted in self._enumerations(self._card()), (
            f"the card never lists exactly {sorted(accepted)} as the severity vocabulary"
        )

    def test_the_card_enumerates_the_statuses_the_report_produces(self, env):
        env.module.register_validator("ok", lambda: True)
        env.module.register_validator("bad", lambda: False)
        env.module.register_validator("broken", lambda: 1 / 0)
        produced = {row["status"] for row in env.module.custom_validator_results()["validators"]}

        assert produced == {"passed", "failed", "check_error"}
        assert produced in self._enumerations(self._card()), (
            f"the card never lists exactly {sorted(produced)} as the status vocabulary"
        )
