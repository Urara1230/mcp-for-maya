# S2 post-hoc review — the five track-3 promotion samples

- **Date of this review:** 2026-10-03
- **Round:** R46 (execution lane), second pass after audit
- **Ledger:** D-221 ③ (S2), D-198 ② (track-3 → track-2 promotion channel), D-203 ⑥
- **Status:** **post-hoc review** — performed AFTER the samples were already in
  the corpus. This is explicitly NOT a pre-registered verification and carries
  no pre-tense of one. D-221 ③ ③ forbids back-dating it.

> ## Correction notice — read this before the table
>
> The first version of this file (committed in this round, then audited) reviewed
> the **wrong cohort** and made a **false claim about the repo**. Both errors are
> recorded here rather than quietly corrected, because a review file that
> silently changes its own conclusions is worth nothing as evidence.
>
> - **What it claimed:** that "no promotion record exists anywhere in the repo",
>   and that the five samples needing adjudication were the `forgiven` cohort.
> - **What is true:** a promotion record **does** exist, in
>   `.github/scripts/polarity_corpus_probe.py::adjudicate` — in the
>   `LABELLED_CORPUS` literal it consumes, immediately after an explicit
>   `# --- track-3 promotions (D-203) ---` marker comment stating why each of
>   the five was promoted. The first version reviewed `LABELLED_CORPUS[4,5,6,12,13]`
>   (all `forgiven`); the real cohort is `[9,10,11,12,13]`. **Overlap: two.**
>   Three genuine promotions — the three `true_negative` samples at `[9]`,
>   `[10]`, `[11]` — were never adjudicated at all, and three samples the first
>   version _did_ review (`[4]`, `[5]`, `[6]`) are not track-3 promotions.
> - **What survives:** the underlying labels were never in question —
>   `disagreements with the hand labels = 0` covers all 14 samples. Only the
>   provenance claim and the cohort selection were wrong.

---

## The cohort, identified mechanically

Do not take the cohort from a label — identify it by the marker comment:

```
uv run python .github/scripts/polarity_corpus_probe.py
```

The five promoted samples are the dict literals **after** the
`# --- track-3 promotions (D-203) ---` comment, i.e. `LABELLED_CORPUS`
indices 9–13. The promotion block records their selection rationale: `'cannot'`
and `'without'` had never been exercised as cue prefixes, and no sample yet put
the negated match **before** a clause split while the second clause carried no
match.

> **Marker text moved (2026-10-27).** The ledger-id migration retired the id
> from that comment, so it now reads `# --- track-3 promotions ---`. Both
> places in this file that quote the old literal — in the correction notice and
> in the paragraph above — are pre-migration text. Same marker, same five
> samples: `tests/test_polarity_corpus_probe.py` derives the cohort from the
> marker line and goes red if the text moves again, so the pointer stays
> followable.

Track-2 arithmetic at the time of this review (unchanged by the correction — the
corpus is the same 14 samples either way):

| quantity                                   | value                                    |
| ------------------------------------------ | ---------------------------------------- |
| labelled samples                           | 14                                       |
| `true_negative`                            | 7                                        |
| `forgiven`                                 | 5                                        |
| `known_limitation`                         | 2                                        |
| adjudicable (TN + forgiven, D-203 caliber) | 12                                       |
| `MIN_ADJUDICABLE`                          | 12                                       |
| guarded elements                           | 1 (`camera_orbit/mutation_side_effects`) |
| per-element true negatives                 | 7 (floor is 3)                           |
| label/guard disagreements                  | **0**                                    |
| promotions in cohort                       | 5 → 3 `true_negative` + 2 `forgiven`     |

## Adjudication

D-198 ④ defines `true_negative` as _"the guard must answer `negated_only` for
this sentence"_ and `forgiven` as _"a correct negation-FORM disclosure
('not idempotent') that the exemption list must forgive"_.

| #   | label           | sample text                                                                           | guard           | agrees | adjudication                                                                                                                                                                                                                                                                             |
| --- | --------------- | ------------------------------------------------------------------------------------- | --------------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 9   | `true_negative` | `This tool cannot create a new camera.`                                               | `true_negative` | yes    | **confirmed.** `camera_orbit` does create a camera on every call, so "cannot" is a true denial, with the `cannot` cue and the match inside one clause. First corpus coverage of this cue as a prefix.                                                                                    |
| 10  | `true_negative` | `Returns success without creating a new camera.`                                      | `true_negative` | yes    | **confirmed.** The `without` cue precedes the match while not being the sentence subject — the shape this promotion was chosen to cover. True denial.                                                                                                                                    |
| 11  | `true_negative` | `This tool does not create a new camera; the scene is untouched.`                     | `true_negative` | yes    | **confirmed.** Negated match sits **before** the `; ` split and the second clause carries no match, making this the mirror image of the `known_limitation` sample at `[7]`. A clause-position regression in either direction now shows up as a disagreement instead of passing silently. |
| 12  | `forgiven`      | `Non-idempotent by design; each call creates a new camera and animation curves.`      | `forgiven`      | yes    | **confirmed.** The exempt phrase is followed by a true positive claim across a `; ` split. The exemption is phrase-scoped, so the later positive still counts rather than being swallowed.                                                                                               |
| 13  | `forgiven`      | `Not idempotent - calling this twice creates a new camera and marks the scene dirty.` | `forgiven`      | yes    | **confirmed.** Two positive matches follow one exempt phrase; forgiveness applies to the sentence, not only to its first match.                                                                                                                                                          |

**Result: 5 of 5 confirmed.** Each carries a one-sentence `why` that stands
independently (D-198 ①), and each states `camera_orbit`'s real behaviour
truthfully.

## What is NOT established here — recorded as unverified, not guessed

D-205 ③: anything not reproducible is recorded as unverified; an invalid
experiment is not evidence.

1. **No real-Maya or live-tool verification.** These are text fixtures judged
   against the tool's documented behaviour. Nothing here observed
   `camera_orbit` running.
2. **The floor has zero headroom.** Adjudicable = 12 against
   `MIN_ADJUDICABLE` = 12. Removing or re-labelling any single
   verdict-carrying sample returns the run to "not yet adjudicable". One
   guarded element carries the entire corpus. Both are properties of the current
   corpus, recorded so a future reader does not mistake a cleared floor for
   margin.
3. **D-198 ⑤'s second precondition is still open.** This review speaks only to
   the track-2 floor. Promotion of the TDQS gate from warn to hard additionally
   requires the primary track's zero-false-rejection verdict, and the FN-rate
   threshold D-198 ⑤ deferred is still unlegislated.
4. **The promotion block's own selection claim is not re-derived here.** It
   asserts `'cannot'` and `'without'` were previously uncovered and that no
   sample covered negated-before-split. That is a claim about corpus _history_;
   confirming it needs the pre-promotion corpus, which is not in the repo. Taken
   on the block's word, not independently verified.

## Reproduction

| claim                               | command                                                          | observed                                                                                                           |
| ----------------------------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| the five-sample cohort + arithmetic | `uv run python .github/scripts/polarity_corpus_probe.py`         | `samples=14`, `true_negative=7`, `forgiven=5`, `known_limitation=2`, `adjudicable (true_negative + forgiven) = 12` |
| guard agrees with every hand label  | same command, TRACK2 block                                       | `disagreements with the hand labels = 0`                                                                           |
| this record stays labelled post-hoc | `uv run python -m pytest tests/test_polarity_corpus_probe.py -q` | includes `test_s2_post_hoc_record_is_labelled_as_post_hoc` and `test_an_unreviewed_track_3_promotion_fails_the_gate`   |
