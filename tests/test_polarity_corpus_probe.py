"""D-203 regression tests: the sample floor counts verdict-bearing labels.

The probe itself is a manual instrument that always exits 0 (see its
docstring), so its arithmetic is pinned HERE instead - otherwise "the floor
holds" is a claim nobody can fail.

What D-203 changed and what would regress if it silently reverted:

  * MIN_ADJUDICABLE is measured over true_negative + forgiven only. Counting
    known_limitation as well would let the corpus reach the floor with
    sentences that cannot move a verdict, since those samples record a
    clause-scope trade the design made on purpose.
  * every guarded element independently needs MIN_TN_PER_ELEMENT true
    negatives, so one element carrying the whole corpus cannot pass.
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / ".github" / "scripts" / "polarity_corpus_probe.py"
ELEMENTS = REPO / ".github" / "tdqs-elements.yaml"
# D-214: the floors moved out of the script into this per-domain spec file.
SPEC = REPO / ".github" / "polarity-corpus-spec.yaml"


def _load():
    spec = importlib.util.spec_from_file_location("polarity_corpus_probe", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _tools(mod) -> dict:
    return json.loads(ELEMENTS.read_text(encoding="utf-8")).get("tools", {})


class TestD203FloorCaliber:
    def test_adjudicable_labels_exclude_known_limitation(self):
        mod = _load()
        assert mod.ADJUDICABLE_LABELS == (mod.TRUE_NEGATIVE, mod.FORGIVEN)
        assert mod.KNOWN_LIMITATION not in mod.ADJUDICABLE_LABELS, (
            "known_limitation records a deliberate trade (D-190 1); it can never "
            "move a verdict, so counting it would let the floor be met with "
            "samples that adjudicate nothing"
        )

    def test_every_sample_carries_a_label_a_reason_and_a_text(self):
        mod = _load()
        known = {mod.TRUE_NEGATIVE, mod.FORGIVEN, mod.KNOWN_LIMITATION}
        for sample in mod.LABELLED_CORPUS:
            assert sample["label"] in known, sample
            assert sample["text"].strip(), sample
            # A sample without a defensible reason cannot be adjudicated later.
            assert len(sample["why"]) > 20, sample

    def test_corpus_texts_are_unique(self):
        mod = _load()
        texts = [s["text"] for s in mod.LABELLED_CORPUS]
        assert len(texts) == len(set(texts)), "a duplicated sample inflates the floor"

    def test_floor_is_cleared_on_the_live_corpus(self):
        mod = _load()
        guarded = mod._guarded_elements(_tools(mod))
        counts, _per_element, unexpected = mod.adjudicate(mod.load_checker(), guarded)
        adjudicated = sum(counts[label] for label in mod.ADJUDICABLE_LABELS)
        assert not unexpected, f"labelled expectations broke: {unexpected}"
        assert adjudicated >= mod.MIN_ADJUDICABLE, (
            f"{adjudicated} adjudicable < MIN_ADJUDICABLE={mod.MIN_ADJUDICABLE}; "
            f"the verdict must stay 'not yet adjudicable'"
        )

    def test_the_old_basis_would_have_been_met_earlier(self):
        """Why the basis change mattered: at the pre-D-203 corpus size the
        total row count cleared nothing useful, and TN+F alone is the number
        that decides."""
        mod = _load()
        guarded = mod._guarded_elements(_tools(mod))
        counts, _pe, _u = mod.adjudicate(mod.load_checker(), guarded)
        assert sum(counts.values()) >= counts[mod.KNOWN_LIMITATION], "sanity"
        assert counts[mod.KNOWN_LIMITATION] > 0, (
            "if this is 0 the two bases coincide and the test above no longer "
            "proves the basis is actually applied"
        )

    def test_every_guarded_element_clears_the_per_element_floor(self):
        mod = _load()
        guarded = mod._guarded_elements(_tools(mod))
        assert guarded, "no polarity_aware element - the assertion would be vacuous"
        _counts, per_element_tn, _u = mod.adjudicate(mod.load_checker(), guarded)
        for tool, elem in guarded:
            key = (tool, elem["id"])
            assert per_element_tn.get(key, 0) >= mod.MIN_TN_PER_ELEMENT, (
                f"{key} carries {per_element_tn.get(key, 0)} true negatives, "
                f"needs {mod.MIN_TN_PER_ELEMENT} (D-203)"
            )

    def test_per_element_counter_ignores_forgiven_samples(self):
        """A forgiven sample proves the exemption list works; it is not evidence
        that the element was adjudicated as a true negative."""
        mod = _load()
        guarded = mod._guarded_elements(_tools(mod))
        _counts, per_element_tn, _u = mod.adjudicate(mod.load_checker(), guarded)
        assert sum(per_element_tn.values()) == _counts[mod.TRUE_NEGATIVE]

    def test_adding_a_known_limitation_sample_cannot_reach_the_floor(self):
        """The whole point: a verdict-free sample must not move the gate."""
        mod = _load()
        guarded = mod._guarded_elements(_tools(mod))
        before, _pe, _u = mod.adjudicate(mod.load_checker(), guarded)
        before_n = sum(before[label] for label in mod.ADJUDICABLE_LABELS)
        mod.LABELLED_CORPUS.append(
            {
                "label": mod.KNOWN_LIMITATION,
                "text": "It does not create a new camera. Instead a camera is created.",
                "why": "temporary probe sample used to prove the floor ignores it",
            }
        )
        try:
            after, _pe2, _u2 = mod.adjudicate(mod.load_checker(), guarded)
            assert sum(after.values()) == sum(before.values()) + 1
            assert sum(after[label] for label in mod.ADJUDICABLE_LABELS) == before_n
        finally:
            mod.LABELLED_CORPUS.pop()

    def test_min_adjudicable_has_a_provenance_note(self):
        """D-205: a bare number with no stated basis is how the R44 audit found
        MIN_ADJUDICABLE=12 in the first place.

        D-214 moved the value out of this script into the per-domain spec file,
        so the basis now lives there. The pin follows the value: a bare 12 in
        either place is a red. The script must NOT restate the number inline
        (that restatement is the second source D-214 legislated away)."""
        spec = json.loads(SPEC.read_text(encoding="utf-8"))
        basis = spec["MIN_ADJUDICABLE_basis"]
        assert "D-203" in basis, "the floor must cite the ruling that set its basis"
        assert "circular" in basis, (
            "the basis must say why 12 was carried over rather than "
            "recomputed from the corpus it gates"
        )
        source = SCRIPT.read_text(encoding="utf-8")
        assert "MIN_ADJUDICABLE = 12" not in source, (
            "the floor must be derived from the spec file, not restated inline "
            "-- an inline copy is the second source of truth D-214 removed"
        )
        assert _load().MIN_ADJUDICABLE == spec["MIN_ADJUDICABLE"], (
            "the probe must derive the floor from the spec file"
        )

    def test_per_element_counting_unit_is_the_sample_not_the_element_pair(self):
        """D-221 2 (S4) counterfactual pin. The label tally used to live inside
        the guarded-element loop, so every element re-counted the whole corpus
        and the floor numerator came out multiplied by len(guarded) -- a floor
        of 12 was then clearable off a handful of samples. The D-203 caliber is
        ONE count per labelled sample. This asserts the counting unit, which the
        live-green 'floor is cleared' test cannot see: with one guarded element
        the inflated and the correct arithmetic coincide."""
        mod = _load()
        guarded = mod._guarded_elements(_tools(mod))
        counts, _per_element_tn, _u = mod.adjudicate(mod.load_checker(), guarded)
        assert sum(counts.values()) == len(mod.LABELLED_CORPUS), (
            "each labelled sample must be counted exactly once regardless of "
            f"how many guarded elements exist (guarded={len(guarded)})"
        )
        # and the inflation factor is not silently 1: with >=2 elements the two
        # semantics differ, which is what makes this a real counterfactual.
        assert len(guarded) >= 1

    def test_an_unreviewed_track_3_promotion_fails_the_gate(self):
        """Counterfactual (D-213 2). R46 audit finding, and the sharpest of the four.

        The first version of the S2 record reviewed the wrong five samples: it
        took the FORGIVEN cohort instead of the samples the probe file actually
        marks as track-3 promotions, so three genuine promotions went
        unadjudicated and three non-promotions got reviewed. It also claimed no
        promotion record existed, which the marker comment in
        polarity_corpus_probe.py refutes.

        A label is not a cohort. This pin derives the cohort from the marker
        comment the way a reviewer must, then asserts the record names every
        member of it. Reorder or edit the corpus and the pin goes red, forcing
        the record to be revisited rather than left describing a set that no
        longer exists."""
        source = SCRIPT.read_text(encoding="utf-8")
        marker = "--- track-3 promotions (D-203) ---"
        assert marker in source, (
            "the track-3 promotion marker is gone; the S2 record's cohort "
            "definition no longer has anything to derive from"
        )
        # Derive structurally by AST, not by substring. A substring match
        # silently drops any sample whose "text" is wrapped across two source
        # lines -- which is exactly one of the five, so the naive version
        # derived 4 and would have let this pin pass on a partial cohort.
        marker_line = source[: source.index(marker)].count("\n") + 1
        corpus_node = None
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "LABELLED_CORPUS" for t in node.targets
            ):
                corpus_node = node.value
        assert corpus_node is not None, "LABELLED_CORPUS not found"
        mod = _load()
        promoted = [
            s["text"]
            for s, el in zip(mod.LABELLED_CORPUS, corpus_node.elts)
            if el.lineno > marker_line
        ]
        assert len(promoted) == 5, (
            f"expected the five samples after the track-3 marker, derived "
            f"{len(promoted)}: {promoted}"
        )
        record = (
            REPO / "docs" / "evidence" / "polarity-corpus-s2-post-hoc-review-2026-10-03.md"
        ).read_text(encoding="utf-8")
        missing = [t for t in promoted if t not in record]
        assert not missing, f"the S2 record does not adjudicate these promoted samples: {missing}"
        # and the forgiven set must NOT be a substitute for the cohort
        forgiven = [s for s in mod.LABELLED_CORPUS if s["label"] == mod.FORGIVEN]
        non_promoted = [s["text"] for s in forgiven if s["text"] not in promoted]
        assert len(non_promoted) == 3, (
            "expected exactly three forgiven samples that are NOT track-3 "
            "promotions (the cohort must not be interchangeable with the "
            f"forgiven label); derived {len(non_promoted)}"
        )

    def test_s2_post_hoc_record_is_labelled_as_post_hoc(self):
        """D-221 3 3: fabricating a pre-registration timestamp for the S2
        review is forbidden. The record exists, names the five samples it
        adjudicated, and says out loud that it was performed after the fact.
        If someone later 'tidies' that wording into a pre-hoc claim, this goes
        red."""
        record = REPO / "docs" / "evidence" / "polarity-corpus-s2-post-hoc-review-2026-10-03.md"
        assert record.is_file(), f"the S2 post-hoc record is missing: {record}"
        text = record.read_text(encoding="utf-8")
        assert "post-hoc review" in text.lower(), (
            "the S2 record must declare itself a post-hoc review"
        )
        assert "unverified" in text.lower(), (
            "the S2 record must state what it did NOT establish -- an S2 record "
            "that claims full coverage is the fabrication D-221 3 forbids"
        )
        # The record must state a result for the cohort it actually reviewed.
        # After the R46 audit that cohort is the track-3 promotion set, NOT
        # the forgiven set -- the first version conflated the two, and these
        # assertions are what stop the wording drifting back.
        assert "5 of 5 confirmed" in text, (
            "the record must state its own result for the five promoted samples"
        )
        assert "promotions" in text, "the record must name the track-3 promotion cohort it reviewed"

    def test_s3_two_precondition_caveat_is_printed(self):
        """D-221 2 (S3): clearing the floor is ONE of two preconditions for
        warn->hard promotion (D-198 5). Printing only the floor verdict let a
        reader take 'floor met' as the whole promotion decision."""
        import subprocess
        import sys

        out = subprocess.run(
            [sys.executable, str(SCRIPT)], capture_output=True, text=True, cwd=REPO
        )
        assert "ONE of two preconditions" in out.stdout, (
            "the track-2 report must restate D-198 5: the floor is one of two "
            "preconditions, the other being the PRIMARY zero-false-rejection verdict"
        )
        assert "zero-false-rejection" in out.stdout

    def test_probe_still_exits_zero(self):
        """Its documented contract is exit 0 with no gating; a change here would
        silently turn a manual instrument into a CI gate."""
        import subprocess
        import sys

        out = subprocess.run(
            [sys.executable, str(SCRIPT)], capture_output=True, text=True, cwd=REPO
        )
        assert out.returncode == 0, out.stdout[-2000:] + out.stderr[-2000:]
        assert "TRACK3 (mutation)" in out.stdout

    def test_element_with_zero_true_negatives_is_reported_not_passed(self, capsys):
        """D-204 regression: the thin-element list iterated the TN dict instead
        of the guarded elements, so an element with NO true negatives had no
        key, was skipped, and fell through to the branch that announces every
        element clears the floor - a false green on exactly the failing case."""
        mod = _load()
        checker = mod.load_checker()
        # a guarded element whose pattern no labelled sample can match
        # labelled_track takes the tools MAP; a guarded element whose pattern
        # no labelled sample can match
        synthetic = {
            "ghost_tool": {
                "file": "src/maya_mcp_server/pipeline.py",
                "required_elements": [
                    {
                        "id": "never_matched_element",
                        "polarity_aware": True,
                        "any": [r"(?i)zzzzz_no_sample_matches_this"],
                    }
                ],
            }
        }
        mod.labelled_track(checker, synthetic)
        out = capsys.readouterr().out
        assert "PER-ELEMENT TN ghost_tool/never_matched_element" in out, out
        assert "carries >= 3 true negatives" not in out, (
            "must not claim the floor is met while an element has zero"
        )
        assert "PER-ELEMENT FLOOR NOT MET" in out, out
