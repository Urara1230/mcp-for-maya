# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Version-consistency gate (D-207 ⑨)** —
  `check_version_consistency.py` asserts pyproject == `__init__.__version__` ==
  CHANGELOG newest released section == the `llms.txt` version field, and
  deliberately TOLERATES pyproject running ahead of the CHANGELOG while real
  work sits under `[Unreleased]` — a gate that is always red during development
  gets deleted, and a deleted gate is worse than none.
  Evidence: `tests/test_check_d207_gates.py::TestVersionConsistency` (11 tests).

- **Pin registry + consistency gate (D-215)** —
  `.github/gate-registry.yaml` records, for every governance surface in
  `.github/scripts/`, its bound class, where its expectation is derived, which
  pytest nodes pin it, and any exemption. `check_gate_registry.py` enumerates
  the bound set from the filesystem and reports the set difference in both
  directions, so a gate added without being registered goes red. Six rules:
  unregistered bound object, unresolvable pin node or expectation pointer,
  stale registration, `bound_class: gate` with no workflow invoking it
  (veto-power drift), exemption with no `expires|issue` or an expired one, and
  a zero-pin guard for `gate`/`manual_preflight`. Findings are blocking
  (`::error::`, exit 1); the one-observation-period warning phase ended with a
  clean registry. The scope limit lives in `check_gate_registry.py`, which the
  gate-registry step comment in `.github/workflows/ci.yml` points at; it is
  deliberately not mirrored into the registry data file, which is a
  machine-read index. (An earlier version of this entry pointed at a registry
  key that does not exist — the R46 audit caught the phantom pointer.)
  What is machine-checked is **registration discipline, not pin effectiveness** —
  a hollow pin passes every rule, and that residual is disclosed rather than
  papered over.
  Evidence: `tests/test_check_gate_registry.py` (29 tests),
  `python .github/scripts/check_gate_registry.py` → registry OK.

- **Governance constants moved to per-domain spec files (D-214)** —
  `MIN_ADJUDICABLE`/`MIN_TN_PER_ELEMENT`, `llms.txt`'s `LINE_CAP`, the
  append-only `GUARDED` prefixes, the release-appendix `ISSUE` default and the
  liveness probe's `PROTOCOL` revision now live in five `.github/*-spec.yaml`
  files and are derived at load time. Each constant's basis, caliber and
  reversal-test reasoning travels with it. Object-derivable values were
  deliberately left alone — a spec file mirroring object state would just be a
  second source of truth.
  Evidence: `tests/test_polarity_corpus_probe.py::TestD203FloorCaliber::test_min_adjudicable_has_a_provenance_note`,
  `tests/test_liveness_probe_sensitivity.py::test_the_protocol_revision_comes_from_the_spec_file`.

- **Custom `scene_review` validators (#79)** — a studio registers a
  session-scoped rule Maya-side with
  `register_validator(name, predicate, severity="error", weight=1, family="custom", message=None)`
  (with `list_validators()` and `unregister_validator(name)` alongside), and
  the `scene_review` tool reports the run in a separate `custom` section —
  `validators`, `score`, `max_score`, `issues` — with `score` and `max_score`
  in weight points from one denominator. A
  predicate returns `bool` or `(passed, message)`; one that raises is
  recorded as `check_error` and the rest of the review still runs. Same name
  overwrites, so re-registering after a reconnect is the contract: replacing
  the module or restarting Maya clears the registry. The built-in score
  cannot move because the composition lives in the new `validator_module.py`
  fragment as `scene_review_with_validators`, not in the line-capped
  monolith, and `custom` is omitted when nothing is registered, so a report
  from before this feature keeps its exact shape. Public, documented and
  tested, but explicitly pre-1.0: it may break before 1.0, with a changelog
  note. No registration MCP tool and no host-side persistence. The compact
  `cos` view carries its own `CUSTOM:` line for the same reason dropping it
  would give the opposite answer: a summary that omits the studio rules reads
  as those rules having passed.
  Evidence — environment: Python 3.13.13 on Windows;
  `python -m pytest tests/test_validator_module.py tests/test_scene_tools_json.py -q`
  -> exit 0, 54 passed;
  `tests/test_scene_tools_json.py::TestSceneReviewCustomValidators`,
  `src/maya_mcp_server/validator_module.py::scene_review_with_validators`.

### Changed

- **Sample-size floor counts verdict-bearing labels only (D-203)** —
  `MIN_ADJUDICABLE` in the polarity corpus probe measured `len(LABELLED_CORPUS)`,
  so `known_limitation` samples — which record a clause-scope trade the design
  made on purpose and can never move a verdict — counted toward the floor that
  gates a verdict. The floor now counts `true_negative + forgiven` only, and each
  guarded element independently needs ≥ 3 true negatives, so one element
  carrying the whole corpus cannot pass. The corpus gained 5 track-3 promotions
  (3 TN, 2 F) covering the `cannot`/`without` cue prefixes and the mirror-image
  clause position. Measured: 14 samples, adjudicable 12 == the floor,
  per-element TN 7 ≥ 3, 0 disagreements with the hand labels.
  Evidence: `tests/test_polarity_corpus_probe.py` (15 collected),
  `python .github/scripts/polarity_corpus_probe.py` → `TRACK2 (labelled)`.

- **Line endings land in pre-commit, not CI (D-205 ③)** — `mixed-line-ending
--fix=lf` added to `.pre-commit-config.yaml`. The justification is measured,
  not asserted: before this hook landed, on this Windows host, 29 tracked files
  carried CRLF in the worktree while 0 of those carried CRLF in their HEAD blob.
  So `.gitattributes` already normalises at commit, and a CI line-ending scan
  would be a permanently green dead check. The pre-commit layer is where worktree
  bytes actually get fixed — and having now fixed them, the same scan returns 0
  for this worktree. Evidence: the `mixed-line-ending` hook in
  `.pre-commit-config.yaml`.

- **TDQS disclosure ratchet CI gate (D-175)** — static AST and JSON
  validation script `.github/scripts/check_tdqs_disclosure.py` hard-gated in
  the CI lint workflow's `tdqs disclosure elements gate` step
  (`.github/workflows/ci.yml`), enforcing the required
  disclosure elements across the gated tool set against single-source
  specification `.github/tdqs-elements.yaml`, backed by unit test suite
  `tests/test_check_tdqs_disclosure.py` (43 passed).

- **TDQS polarity mechanism hardened to clause scope (D-190)** — the
  mutation-class existence guard in
  `.github/scripts/check_tdqs_disclosure.py::_negated` scopes the negation
  window to the enclosing clause instead of a fixed 80-char window, and
  `.github/tdqs-elements.yaml` gains the element-level `positive_exemptions`
  field plus the polarity direction limit (the guard applies only to purely
  positive pattern sets; negation-form elements stay unguarded). Negated-only
  matches stay warn-level. The two-track corpus measurement D-191①c requires is
  now a committed instrument instead of a per-round scratch probe:
  `.github/scripts/polarity_corpus_probe.py` (manual, not wired into CI).
  Evidence — environment: Python 3.11.9 on Windows;
  `python -m pytest tests/test_check_tdqs_disclosure.py -q` -> exit 0,
  17 passed, artifact in-repo
  (`tests/test_check_tdqs_disclosure.py::test_polarity_cross_clause_cue_does_not_negate`,
  `tests/test_check_tdqs_disclosure.py::test_polarity_same_clause_exemption_forgives_cue`,
  `tests/test_check_tdqs_disclosure.py::test_live_corpus_has_no_polarity_warnings`);
  `python .github/scripts/check_tdqs_disclosure.py` -> exit 0, 39 elements
  across 13 tools, artifact `.github/scripts/check_tdqs_disclosure.py`.

### Removed

- **Governance layer teardown** — `docs/decision-ledger.md` and
  `docs/handoffs/` were retired in `1c5befe`; `docs/adr/`, `CONTEXT.md`, six
  CI gate scripts (`check_changelog_fixed.py`, `check_count_claims.py`,
  `check_error_codes.py`, `check_evidence_anchors.py`,
  `check_readme_skeleton.py`, `check_tool_count_claims.py`) and their four
  test files were removed in `716ba79`. Tracker moved to GitHub Issues;
  load-bearing decisions moved to comments at the code they constrain.

### Fixed

- **Counterfactual pins did not follow the pin-form canon they were written
  under (D-213 ②)** — the five S1/S2 counterfactual pins landed in
  the R46 lane under descriptive names (`test_a_helper_function_is_not_accepted_as_a_pin`
  and siblings) with no `counterfactual` docstring marker, so the clause that
  says a pin must be named `test_*_fails_the_gate` and declare itself a
  counterfactual was satisfied by prose in the module header rather than by the
  pins themselves. Same failure class as the phantom-pointer and false-count
  findings of this round: the rule existed, nothing checked the new code against
  it. Renamed to the canon form and gave each an explicit counterfactual
  docstring; the live-green control keeps its descriptive name because it is a
  positive control, not a counterfactual.
  Broken version: through 0.5.0 (unreleased lane, never shipped); fixed version: [Unreleased] (next release).
  Evidence: `tests/test_check_gate_registry.py::test_a_helper_function_named_as_a_pin_fails_the_gate`,
  `tests/test_check_gate_registry.py::test_a_module_constant_named_as_a_pin_fails_the_gate`,
  `tests/test_check_gate_registry.py::test_a_non_test_class_method_named_as_a_pin_fails_the_gate`,
  `tests/test_check_gate_registry.py::test_a_class_not_named_test_prefix_fails_the_gate`,
  `tests/test_polarity_corpus_probe.py::TestD203FloorCaliber::test_an_unreviewed_track_3_promotion_fails_the_gate`;
  `python .github/scripts/check_gate_registry.py` → registry OK (the renames
  touch no registered pin node id).

- **The S2 review record pointed at a bare `path:line` for its central claim** —
  the correction notice cites where the promotion record lives using
  `polarity_corpus_probe.py:206-215`, a form that drifts on every edit. It also
  cited a line range that the same record's own derivation pin makes redundant.
  Now `path::symbol`.
  Broken version: through 0.5.0 (unreleased lane, never shipped); fixed version: [Unreleased] (next release).
  Evidence: the correction notice in
  `docs/evidence/polarity-corpus-s2-post-hoc-review-2026-10-03.md` now cites
  `.github/scripts/polarity_corpus_probe.py::adjudicate`.

- **The polarity sample floor was counting each sample once per guarded element
  (D-221 ② / S4)** — the label tally sat inside the per-element loop, so the
  floor numerator came out multiplied by the number of guarded elements. With
  one guarded element the arithmetic coincides with the correct reading, which
  is exactly why the live-green test could not see it.
  Broken version: through 0.5.0 (unreleased lane, never shipped); fixed version: [Unreleased] (next release).
  Evidence: `tests/test_polarity_corpus_probe.py::TestD203FloorCaliber::test_per_element_counting_unit_is_the_sample_not_the_element_pair`;
  `python .github/scripts/polarity_corpus_probe.py` → `samples=14`,
  `adjudicable (true_negative + forgiven) = 12`, per D-203's arithmetic.

- **`scene_review` was described as having 11 "dimensions" in two places** —
  the 11 are checks; the aesthetic dimensions are 5
  (`color_theory`, `spatial_composition`, `proportion_scale`,
  `lighting_quality`, `visual_flow`). A reader following the old wording would
  have looked for an 11-dimension breakdown that does not exist. Both strings
  were agent-facing; the count-claims gate that had read docs rather than
  source was removed in the same teardown, so this entry records the fix.
  Broken version: through 0.5.0 (unreleased lane, never shipped); fixed version: [Unreleased] (next release).
  Evidence: the five keys assigned in
  `src/maya_mcp_server/maya_scene_module.py::_compute_full_aesthetic_score`;
  `python .github/scripts/check_tdqs_disclosure.py` → exit 0 over the
  agent-facing description surface.

- **The track-2 floor report omitted D-198 ⑤'s second precondition
  (D-221 ② / S3)** — hard admission to the TDQS gate needs the floor AND the
  primary track's zero-false-rejection verdict, but printing only the floor
  verdict let "floor met" read as the whole promotion decision.
  Broken version: through 0.5.0 (unreleased lane, never shipped); fixed version: [Unreleased] (next release).
  Evidence: `tests/test_polarity_corpus_probe.py::TestD203FloorCaliber::test_s3_two_precondition_caveat_is_printed`.

- **An overdue TDQS exemption could not fail CI (D-204)** — the `due` field was
  compared with `current >= due` and always warned, so a waiver left past its
  deadline for a release stayed open-ended for free: the check could not fail,
  which is the blind spot D-195 4 exists to close. Now `== due` warns (the
  release landing ON the deadline stays green) and `> due` errors, naming the
  five D-168 renewal elements. All 12 live exemptions are due 0.7.0 against
  version 0.5.0, so the 0.6.0 release stays green.
  Broken version: from D-195 4 through 0.5.0 (unreleased lane, never shipped); fixed version: [Unreleased] (next release).
  Evidence: `tests/test_check_tdqs_disclosure.py::test_past_the_deadline_is_an_error_not_a_warning`,
  `tests/test_check_tdqs_disclosure.py::test_due_warns_but_does_not_bite_on_the_deadline_itself`,
  `python .github/scripts/check_tdqs_disclosure.py` → exit 0.

## [0.6.0] - 2026-10-05

Release-bearing floor update. Tag 语义基点历史锚：`f97857f`（exec-A: R44 description floor, #67）。本版本依据 D-224 改锚至 HEAD bump commit 发布，包含描述地板重写、严格策略模式及用户面发现治理；R46 纯 CI 治理件保留在 [Unreleased]。

### Added

- **Strict policy mode (D-207 ①⑦)** — three operator flags
  (`MAYA_MCP_DISABLE_EXECUTE`, `MAYA_MCP_DISABLE_WRITE_MODULE`,
  `MAYA_MCP_DISABLE_ARBITRARY`) remove escape-hatch tools from `tools/list` and
  deny the call with `[policy_disabled]`, audited as `rejected`. One choke
  point in the pipeline, none in the tool bodies; read-only tools unaffected.
  Evidence: `tests/test_pipeline.py::TestStrictPolicyMode` (19 collected: 5 functions,
  parametrised), plus `.github/scripts/liveness_probe.py` — a committed probe that
  spawns the real server over stdio and observes 25 tools by default and 22 under
  `MAYA_MCP_DISABLE_ARBITRARY=1`, with 0 escapes leaked and the read-class surface
  retained. It reads its expectation from `POLICY_ENV_FLAGS`, so it cannot assert
  a stale set.
  `src/maya_mcp_server/pipeline.py::policy_disabled_tools`,
  `src/maya_mcp_server/security.py::PolicyDisabledError`.

- **`llms.txt` discovery file + generator gate (D-207 ②)** — 75 lines, under
  the 100-line cap, DERIVED from `pipeline.TOOL_ANNOTATIONS` and each tool’s own
  docstring. Hand-editing it fails the lint job.
  Evidence: `tests/test_check_d207_gates.py::TestLlmsTxtChecker` (12 collected),
  `python .github/scripts/check_llms_txt.py` → `llms.txt OK: 75 lines, 25 tools`.

- **Error-code reference page + code-set gate (D-207 ④)** —
  `docs/guide/error-codes.md` is derived from `security.py`; the gate fails on
  drift in EITHER direction (a code with no row, a row with no code). The
  precondition was verified first: the D-019 `[code]` prefix contract already
  existed, so the page is not a standalone promise.
  Evidence: `tests/test_check_d207_gates.py::TestErrorCodesChecker` (8 collected),
  `python .github/scripts/check_error_codes.py` → `10 codes, doc set == code set`.

- **Session-lifecycle matrix (D-207 ③)** — `docs/guide/session-lifecycle.md`,
  one page, four columns (exit path × teardown trigger × residual state ×
  worst-case residual window), marked as a summary view subordinate to the ADR.
  Evidence: its nine anchor references — eight `path::symbol` plus one ADR file
  path — each verified to resolve, with the composition itself machine-checked by
  `tests/test_check_d207_gates.py::TestErrorCodesChecker::test_session_lifecycle_anchor_composition_is_what_the_changelog_claims`;
  `tests/test_session_manager.py::TestCodedSessionErrors::test_no_sessions_raises_coded`,
  `tests/test_module_teardown.py::TestCreateModuleTeardown::test_teardown_called_and_resources_closed`.

### Changed

- **TDQS description quality specification (D-174)** —
  machine-readable specification `.github/tdqs-elements.yaml:1` establishing P0
  required disclosure elements and single-direction `Boundary:` conventions.

- **P0 core tool descriptions rewritten (D-172)** —
  `execute_code` (`src/maya_mcp_server/server.py:345`) rewritten to disclose
  arbitrary code scope, full session privileges, irreversible execution,
  `result_type` envelope, and session prerequisites; `write_module`
  (`src/maya_mcp_server/server.py:292`) rewritten to disclose session
  prerequisites, in-memory overwrite semantics, and `execute_code` boundary;
  `scene_validate` (`src/maya_mcp_server/scene_tools.py:473`) rewritten to
  disclose read-only spatial constraints without `auto_fix` mutations, session
  prerequisites, and disambiguation boundaries.

- **P1 scene analysis tool disambiguation boundaries (D-173)** —
  added single-direction `Boundary:` lines across 9 scene tools in
  `src/maya_mcp_server/scene_tools.py:147` and
  `src/maya_mcp_server/introspect_tools.py:35` to eliminate conceptual
  overlap across spatial inspection and auditing tools.

- **Tool instructions and threat model alignment (D-173④)** — synchronized
  FastMCP server instructions (`src/maya_mcp_server/server.py:67`) and
  security threat model (`docs/threat-model.md:162`) to reflect `execute_code`
  arbitrary execution privilege and `scene_validate` read-only constraint semantics.

- **Description floor rewrite — `scene_measure` measurement contract (R44, D-195/D-196)** —
  the scene-measurement descriptions were rewritten to the disclosure floor
  (explicit units, frame/session prerequisites, read-only boundary), landing as
  the release-bearing `exec-A` batch `f97857f` recorded as the tag semantic
  basepoint above.
  Evidence: `f97857f:src/maya_mcp_server/scene_tools.py:376`,
  `tests/test_scene_tools.py::TestCosFormatterIntegration::test_format_measure_cos_roundtrip`.

### Fixed

- **`check_error_codes.py` crashed on a doc path outside the repo** — the gate
  called `Path.relative_to(REPO)` unguarded, so a test pointing it at a tmp
  path raised `ValueError` instead of reporting drift. Caught by
  `tests/test_check_d207_gates.py::TestErrorCodesChecker::test_undocumented_code_is_reported`
  and fixed
  in the checker rather than by widening the test.
  Broken version: unreleased (introduced and fixed within the 0.6.0 development cycle, never shipped); fixed version: 0.6.0.
  `.github/scripts/check_error_codes.py::_rel`.

- **`llms.txt` linked to a repository this project has never occupied** — the
  generator hardcoded an owner name, and because the gate compared the generated
  file against its own constants the wrong links reported green indefinitely. That
  was a fabricated identifier, not a typo. Every URL now derives from
  `[project.urls] Repository` in `pyproject.toml`, so a fork or a rename moves
  the links with the project.
  Broken version: unreleased (introduced and fixed within the 0.6.0 development cycle, never shipped); fixed version: 0.6.0.
  Evidence: `tests/test_check_d207_gates.py::TestLlmsTxtChecker::test_doc_urls_are_derived_from_pyproject_not_hardcoded`,
  `.github/scripts/check_llms_txt.py::repo_url`.

- **A tool with no section heading vanished from `llms.txt` silently** — the
  coverage check lived inside the byte-equality branch, so in the one case that
  matters (a tool added to the registry and to no heading, where the renderer
  drops it and therefore produces no diff) it could not fire. Coverage is now
  asserted independently of the comparison, and an incomplete section map is
  itself an error.
  Broken version: unreleased (introduced and fixed within the 0.6.0 development cycle, never shipped); fixed version: 0.6.0.
  Evidence: `tests/test_check_d207_gates.py::TestLlmsTxtChecker::test_a_tool_absent_from_the_page_is_reported`.

- **MCP ImageContent snake_case attribute alignment** — align `mt.ImageContent`
  instantiation to use standard snake_case `mime_type` instead of deprecated
  `mimeType` (`src/maya_mcp_server/visual_tools.py:145`), resolving mypy
  `call-arg` violation and `FastMCPDeprecationWarning` during viewport
  snapshot processing with MCP SDK v2 (`tests/test_visual_tools.py:245`,
  `tests/test_visual_tools.py:259`).
  Broken version: unreleased (introduced and fixed within the 0.6.0 development cycle, never shipped); fixed version: 0.6.0.

- **FastMCP 4.x / 2.14.x test harness compatibility** — adapt test suite for
  library upgrades: import `ToolResult` with fallback order from
  `fastmcp.tools.base` to `fastmcp.tools.tool` to `mcp.types.CallToolResult`
  (`tests/test_pipeline.py:17`), support both raw decorated functions in
  FastMCP 4.x and legacy `.fn` attributes via `getattr` in session and scene
  tool tests (`tests/test_qt_channel.py:636`,
  `tests/test_scene_tools_json.py:255`), and filter warnings ahead of imports
  in conftest (`tests/conftest.py:33`).
  Broken version: unreleased (introduced and fixed within the 0.6.0 development cycle, never shipped); fixed version: 0.6.0.

- **The per-element sample floor could report a false green** — the thin-element
  list iterated the true-negative dict instead of the guarded elements, so an
  element with zero true negatives had no key, was skipped, and fell through to
  the branch announcing that every element cleared the floor. The floor verdict is
  now reported independently of the label-agreement verdict, so a disagreement
  can no longer swallow a floor violation.
  Broken version: unreleased (introduced and fixed within the 0.6.0 development cycle, never shipped); fixed version: 0.6.0.
  Evidence: `tests/test_polarity_corpus_probe.py::TestD203FloorCaliber::test_element_with_zero_true_negatives_is_reported_not_passed`.

- **The overdue-exemption message misdescribed the D-168 ④ renewal requirements** —
  it listed five items of our own invention. The real five (D-168 ④ as revised by
  D-176) are a `gate_authority` countersignature distinct from the `debt_owner`, an
  event-anchored rather than calendar expiry, the per-row renewal cap of 2, a
  re-validated reason, and the waiver lane closing after gate review.
  Broken version: unreleased (introduced and fixed within the 0.6.0 development cycle, never shipped); fixed version: 0.6.0.
  Evidence: `tests/test_check_tdqs_disclosure.py::test_past_the_deadline_is_an_error_not_a_warning`.

## [0.5.0] - 2026-09-30

Verification-milestone release — **no runtime code changes since 0.4.1**.
The delta is evidence and governance: the real-Maya verification window
executed and its results are now in-repo, machine-readable form.

### Added

- **Real-Maya verification evidence pack** — the ten-frame live window ran
  on Maya 2024 (Windows, en_US, published artifact `uvx mcp-for-maya@0.4.1`)
  and landed as `docs/evidence/real-maya-window-2026-09-30.md` plus 20
  probe artifacts under `docs/evidence/probes/` (D-153 three-layer closeout).

- **Linux server-side verification** — the published artifact installs and
  serves over stdio on Linux (WSL2/Kali, Python 3.13), and the Qt-channel
  socket test suite passes 42/42 including RST/FIN semantics
  (`probe-box06-linux-wsl.json`). Residual: the in-Maya channel under a
  Linux-hosted Maya remains unverified (no Linux install on host).

- **Machine-checkable v1.0.0 gate waiver list** —
  `docs/evidence/gate-waiver-list-1.0.0.json` records the three-state gate
  (Pass / Waived with owner+expiry+reason, fail-closed / Blocked=empty)
  adopted in D-163.

- **MCP Registry listing** — `io.github.Xxx91n/mcp-for-maya` v0.4.1 is
  active on the official registry (published 2026-09-30).

### Known limitations (unchanged disclosure discipline)

- `scene_viewport_snapshot` fails deterministically on the test station
  (VP2 `capture_failed` — Issue #53; the playblast-based
  `scene_render_preview` is verified working).
- mayapy on the verification host is env-broken; Tier-2 suite deferred.
- Maya 2025/2026 and in-Maya Linux lanes are explicitly not-verified.
- Client-UI image rendering verified at protocol level only (Inspector
  --cli); Claude Code/Codex seats were blocked by client-side model/proxy
  configuration, not by this server.

## [0.4.1] - 2026-09-29

### Added

- **MCP Registry ownership marker (D-143)** — added
  `<!-- mcp-name: io.github.Xxx91n/mcp-for-maya -->` ownership marker to
  `README.md` and `README.zh-CN.md` to satisfy the official MCP Registry
  publishing authentication prerequisite for package name ownership.
  Evidence: `README.md:1` + `README.zh-CN.md:1`.

- **PyPI simple+JSON dual-source probe lane (D-142γ)** — added
  `.github/scripts/check_pypi_index.py` probe lane integrated into the
  weekly resolution-drift canary in `.github/workflows/ci.yml` (reading both
  PEP 691 JSON / HTML simple index and project JSON APIs) to catch CDN split
  and index desynchronization regressions before release verification.
  Evidence: `.github/scripts/check_pypi_index.py:1` +
  `tests/test_check_pypi_index.py::test_both_green_is_clean`.

- **Release-body disclosure appendix assertion script (D-142β)** — added
  `.github/scripts/check_release_appendix.py` to enforce preflight item 6
  weak assertions against GitHub Release notes, validating disclosure
  appendix existence and issue reference formatting. Evidence:
  `.github/scripts/check_release_appendix.py:1` +
  `tests/test_check_release_appendix.py::test_pass_when_appendix_present_and_issue_open`.

- **Distribution surfaces registry table (D-144, D-145)** — created
  `docs/distribution-surfaces.md` tracking listing-surface three-state
  registrations (actionable, passive-observation-with-trigger, structural-rejection)
  alongside 30-day post-publish verification obligations.
  Evidence: `docs/distribution-surfaces.md:1`.

### Changed

- **Release tag and baseline location hygiene governance (D-150②③)** —
  standardized forward release tagging to annotated tags (`git tag -a`) and
  bare `vX.Y.Z` release titles, while confirming `mypy-baseline.txt`
  legitimacy at repository root per upstream tooling defaults. Evidence:
  `docs/decision-ledger.md:160`.

- **Contributing guide PR tip budget policy clarification (D-142)** —
  clarified in `CONTRIBUTING.md` that budget gates evaluate PR tip state
  rather than mid-PR transients. Evidence: `CONTRIBUTING.md:36`.

## [0.4.0] - 2026-09-28

### Added

- **CI tool-count claims gate (D-121)** —
  `.github/scripts/check_tool_count_claims.py` runs in the lint job:
  `N tools total` / `all N tools` claims (plus the zh-CN mirror
  shapes) in live docs — README.md, README.zh-CN.md, AGENTS.md,
  docs/threat-model.md — and the latest CHANGELOG release section are
  asserted against the count derived statically from
  `pipeline.TOOL_ANNOTATIONS` (ast, no project deps). Historical
  release sections are exempt by construction — they record the
  artifact's count at its own release time. The stale landing-time
  count in the [0.3.0] `scene_export` bullet is corrected in the same
  commit; new bullets avoid absolute tool totals (relative wording
  only) so the gate stays quiet. Evidence:
  `.github/scripts/check_tool_count_claims.py:115` +
  `tests/test_check_tool_count_claims.py::test_repo_surface_currently_consistent`.

- **Drift-canary red -> issue artifact (D-123)** — the weekly
  resolution-drift job gains an independent trailing `drift-notify`
  job in `.github/workflows/ci.yml` driven by
  `.github/scripts/notify_drift_canary.py`: first red opens one
  dedup issue per workflow+branch, continued reds comment on it,
  recovery comments and auto-closes. Job-level `issues: write`
  escalation only (workflow stays read-only) and the job is
  `continue-on-error` so its own failure never masks the drift red.
  The v1.0.0 release checklist gains a hard item — at least
  one human canary-review record (result + attribution in the
  decision ledger, red or green) must exist before v1.0.0 ships
  (obligation registered as D-127). Evidence:
  `.github/workflows/ci.yml:120` (drift-notify job) +
  `.github/scripts/notify_drift_canary.py:109` +
  `tests/test_notify_drift_canary.py::test_red_creates_issue_when_none_open` /
  `::test_green_closes_open_issue`.

- **Monolith line-count ratchet (D-125)** —
  `src/maya_mcp_server/maya_scene_module.py` is capped by
  `.github/monolith-budget.json` (frozen at its landing-time count)
  enforced by `.github/scripts/check_monolith_budget.py` in the lint
  job: over budget fails, under budget warns "may be lowered" so the
  ratchet keeps closing instead of decaying into a high-water mark.
  Growth channel: human-approved budget edit in the same PR with the
  reason stated; new Maya-side capability routes to a
  separate file in the same injection unit
  (`introspect_module.py` is the first case). Evidence:
  `.github/monolith-budget.json:2` +
  `.github/scripts/check_monolith_budget.py:39` +
  `tests/test_check_monolith_budget.py::test_repo_monolith_within_budget`.

- **CI append-only guard for published assets (D-113, D-081)** —
  `.github/scripts/check_assets_append_only.py:68` runs in the lint job:
  pure additions under `.github/assets/` pass, while
  delete/modify/rename fail the job (`git diff --no-renames
--diff-filter=DMRT`; merge-base three-dot on pull_request,
  `before..after` on main pushes, all-zero `before` skips). Escape
  hatch: maintainer-aware merge with the removal stated in the PR body
  plus a ledger note. Tests: tests/test_check_assets_append_only.py.

### Changed

- **Install path migrated to uv.lock dual-track (D-112)** — `uv.lock`
  committed (113 packages); CI test/mypy and release-test jobs now run
  `uv sync --frozen` + `uv run` on the locked resolution, dev deps moved
  to PEP 735 `[dependency-groups]`; floating-resolution coverage moved
  to a weekly drift canary job (`uv lock --upgrade`, Mon 06:37 UTC).
  Dev setup: `uv sync --frozen` (pip >=25.1 can still
  `pip install -e . --group dev`). Evidence: uv.lock, pyproject.toml:38
  `[dependency-groups]`, .github/workflows/ci.yml:102 resolution-drift
  canary job.

- **fastmcp 4.x migration (D-105, D-099 trigger gates satisfied)** —
  dependency pin narrowed from `>=2.14,<3.0.0` to `>=4.0.0,<5.0.0`
  (dual anchors in pyproject.toml: corrected #18/#36 red history +
  upstream minor-may-break releases policy). Code deltas: MCP SDK v2
  snake_case field renames (`read_only_hint`/`mime_type`/`ToolResult`
  import path — the earlier camelCase-breakage attribution was
  root-cause-corrected by the R25 spike). Both AuthlibDeprecationWarning
  pytest exemptions removed — the registered trigger fired (auth
  providers moved to joserfc). `scene_describe` gains the D-110
  response budget (`limit` param, 200 soft / 1000 hard per face,
  `*_truncated` + totals disclosure). Evidence:
  pyproject.toml (fastmcp >=4.0.0,<5.0.0 pin),
  src/maya_mcp_server/introspect_module.py:82-83
  (`_DESCRIBE_SOFT_CAP`/`_DESCRIBE_HARD_CAP`),
  tests/test_introspect_tools.py, tests/test_introspect_module.py.

### Removed

- **Dormant aesthetic engine retired (D-124)** — deleted
  `src/maya_mcp_server/aesthetic_engine.py` (1,420 lines, zero
  production references across nine rounds) and its dedicated
  `tests/test_aesthetic_engine.py` (58 tests exercising only the
  dormant module) in the same commit. The Maya-side inline
  `_score_*` functions in `maya_scene_module.py` remain the sole,
  live implementation — the single-source goal once
  vetoed now achieved in the opposite direction; the ADR carries
  errata + status notes, not a reopen. Coverage note: any numeric
  coverage delta reflects deleting dormant-only tests; production
  coverage is unchanged (`tests/test_maya_scene_module.py` exercises
  `_score_*` through the stub). Baselines lowered with the reason
  stated: `.github/ruff-baseline.json` (src E741 14→8 / F841 5→4;
  tests E741 10→3 / F401 16→9 / N801 1→0), `mypy-baseline.txt`
  (38 stale entries removed, 237→199), `.pre-commit-config.yaml`
  exclude updated. Evidence: `docs/decision-ledger.md` D-038/D-124 rows
  - `tests/test_maya_scene_module.py::test_aesthetics_sampling`
    (production-path coverage). Recall path: `git log -G aesthetic_engine`.

## [0.3.0] - 2026-09-27

### Added

- **`scene_export` — FBX/OBJ/USD scene export (D-091/D-092)** — new
  per-domain injected module `_mcp_export`
  (`src/maya_mcp_server/export_module.py::export_scene`) + host tool
  (`src/maya_mcp_server/export_tools.py::register_export_tools`,
  registered in `src/maya_mcp_server/server.py`), 25 tools total.
  Contract: path normalized (expanduser+abspath), parent dirs
  auto-created, existing target rejected unless `overwrite=True`;
  format enum {fbx,obj,usd} with extension inference, missing-extension
  append, and conflict-is-an-error mirror rules (live-pinned type
  tokens `FBX export`/`OBJexport`/`USD Export`); `objects=None` ->
  exportAll, a list is `objExists`-prevalidated (no partial export) and
  drives exportSelected with the prior selection restored in a finally;
  `prompt=False`/`force=True` forced; scene modified flag untouched.
  Returns `{path, format, objects_exported, size_bytes, duration_ms,
warnings}` with a structured domain-error family
  (invalid_format/invalid_path/empty_objects/missing_objects/
  plugin_missing/export_failed). Alembic deliberately excluded —
  AbcExport is not a cmds.file surface.
  (`tests/test_export_module.py`, `tests/test_export_tools.py`,
  `tests/test_pipeline.py::TestToolAnnotations`,
  `docs/threat-model.md` §5 local-file-write row)

- **`scene_describe` / `scene_nodes` — scene-graph introspection
  (D-094/D-095)** — first consumer of the same-injection-unit
  assembly: the Maya side lives in a separate host-maintained
  file (`src/maya_mcp_server/introspect_module.py`) concatenated into
  the `_mcp_scene` injection payload by `scene_tools` /
  `client.ensure_module_injected(extra_source_paths=...)` — one module
  name, one trigger, one failure domain; no `_mcp_introspect` module
  exists. Host tool layer: `src/maya_mcp_server/introspect_tools.py`,
  25 tools total. `scene_describe(node, attrs=None,
include_values=False, include_connections=True)` returns per-attribute
  metadata (attr_type/readable/writable/connectable/keyable/multi/
  hidden/locked/storable/children/index_matters/enum/listEnum/min/max,
  values+soft ranges behind include_values) plus directional plug-level
  connections {src_plug, dst_plug, direction}; domain errors
  node_not_found/attr_not_found (named-attr aborts the whole call, no
  partial metadata) /query_failed. `scene_nodes(type=None, pattern=None,
dag_only=False, inherited=True, limit=50, cursor=None,
include_type_counts=False)` enumerates DAG + dependency nodes with
  honest pagination (total_count/has_more/next_cursor, hard cap 100,
  invalid_cursor on stale tokens). Boundaries are explicit:
  scene_describe is API-level self-description (spatial stays with
  scene_inspect); scene_nodes is graph inventory (overview stays with
  scene_snapshot). Evidence: tests/test_introspect_module.py,
  tests/test_introspect_tools.py (dual-channel injection covered),
  docs/visual-callform-matrix.md “Introspection call sites”.

### Changed

- **Ruff pin bump 0.15.20 -> 0.16.7 (D-089)** — the two pinned sites
  moved together: `.pre-commit-config.yaml` rev and
  `.github/workflows/ci.yml` `uvx ruff@` pin; dependabot.yml now ignores
  fastmcp semver-major (PR #18 CI red: ToolAnnotations kwargs
  camelCase->snake_case is an API migration, not a pin bump).
- **`_delete_if_exists` primitive (D-090)** — the three
  `objExists->delete` call sites in `asset_module.py` (stale-DG batch
  loop + bump2d/displacement half-wire rollbacks) converge on one
  zero-condition helper; rollback block bodies unchanged
  (`src/maya_mcp_server/asset_module.py::_delete_if_exists`,
  regression net `tests/test_asset_module.py` incl. `TestBumpOrphan`).

- **Host-side injected-module calls unified (`module_call` /
  `exec_module_code`, D-098)** — four verbatim-isomorphic
  `_X_call`/`_exec_X` copies (scene/asset/export/visual) generalized
  into domain-agnostic call-construction primitives in
  `src/maya_mcp_server/client.py`; the P0-2 "user input never becomes
  executable source text" contract moved with `module_call`.
  `scene_tools._execute_scene_code` keeps the cache branch over the
  shared no-cache core — its cached value is now the already-decoded
  object (the same dict/list is returned on every cache hit instead of
  a fresh decode of a cached string; call sites only read it, and a
  decode failure no longer enters the cache). Per-domain
  `_ensure_X_injected` binders unchanged. Evidence:
  tests/test_client.py::TestModuleCall.

- **Debt accounting — T-06 / T-07 (registered debts,
  not shipped)** — T-06 (aesthetic-engine single-sourcing:
  `aesthetic_engine.py` stays dormant, the Maya-side inline
  `_score_*` functions remain the live implementation; consolidation
  decision per D-038, trigger = T-06 scheduling) and T-07
  (`maya_scene_module` monolith split — the D-095 introspection unit
  above is its first migrated slice; further slices follow the
  injection-module admission criteria). Ledger: `docs/decision-ledger.md`.

### Fixed

- **bump2d orphan cleanup symmetry (D-083)** — a `setAttr`/`connectAttr`
  failure mid-wiring in `_wire_map`’s `normal`/`normal_dx` branch left a
  half-created `bump2d` node orphaned in the scene; it is now deleted on
  every failure path, matching the `displacement` branch’s D-082b
  contract (`src/maya_mcp_server/asset_module.py::_wire_map`,
  `tests/test_asset_module.py::TestBumpOrphan` — both connect paths, the
  `bumpInterp` setAttr path, and the no-`normalCamera` early return).

## [0.2.1] - 2026-09-24

### Fixed

- **Poly Haven single-asset texture wiring** — `split_texture_key()`
  only recognised compound `<part>_<suffix>` keys, so single-piece
  assets (e.g. `vintage_pocket_watch`, `metal_tool_chest`) silently
  downloaded zero textures and imported as untextured shells. Bare
  keys (`Diffuse`, `nor_gl`, `Metal`, `Rough`, `AO`, `ARM`, ...) now
  map to the asset's single unnamed part; compound matching is
  case-insensitive (`Body_Diff` no longer drops). Regression tests
  cover the bare-key and case-variant forms in `select_files()`
  (`tests/test_polyhaven.py::TestSelectFiles`).
- **Temp-file injection hygiene (D-082a)** — the native-channel
  fallback in `asset_tools.py::_ensure_asset_injected` and
  `scene_tools.py::_ensure_module_injected` staged module source at a
  predictable shared temp path with default perms and no cleanup; it
  now uses `mkstemp` under `platformdirs.user_cache_dir("mcp-for-maya")
/inject`, chmod `0600`, try/finally unlink on success and failure
  (`tests/test_scene_tools.py::TestInjectionHygiene`,
  `tests/test_asset_tools.py::TestInjectionHygiene`,
  `tests/test_qt_channel.py::test_native_client_keeps_tempfile_fallback`).
- **Displacement wiring cleanup (D-082b)** — a `connectAttr` failure
  mid-wiring left a half-created `displacementShader` node orphaned in
  the scene; `_wire_map` now deletes it on every failure path
  (`src/maya_mcp_server/asset_module.py::_wire_map`,
  `tests/test_asset_module.py::TestDisplacement` — positive +
  negative + orphan-cleanup cases). Note: the R3 audit's "connection
  impossible" claim was refuted by a live Maya 2024 probe
  (`disp.displacement -> sg.displacementShader` connects —
  `docs/evidence/probes/probe-displacement.json`); "connected" is still not
  "renders correctly" — visual proof stays on the gui/human_verify tier.
- **Poly Haven surface hardening (D-082f)** — bare `int()` on
  API/HTTP-controlled fields could escape the `AssetError` domain as a
  raw `ValueError`; now `_as_int` maps them to `bad_response`
  (`src/maya_mcp_server/polyhaven.py`). The multi-MB `/assets` index
  carries a 300 s TTL cache (`SEARCH_INDEX_TTL_S`) so repeated
  `asset_search` calls don't re-pull the listing — unrelated to the
  download path's fresh-metadata revalidation rule. The supplementary
  `asset_import` audit row now records real download+import wall time
  instead of the `duration_ms: 0.0` placeholder
  (`src/maya_mcp_server/asset_tools.py::_audit_asset_download`,
  `tests/test_polyhaven.py::test_index_ttl_cache_bounds_calls`,
  `test_dirty_content_length_is_domain_error`,
  `test_dirty_size_in_payload_is_domain_error`,
  `tests/test_asset_tools.py::test_audit_duration_ms_is_measured`).
- **VP2 readback direction probed at runtime (D-082d)** —
  `_VP2_READBACK_BOTTOM_UP` was a compile-time constant arbitrating a
  runtime variable (cross-GPU differences were a known blind spot);
  `visual_module._vp2_direction` now resolves it per-session on first
  capture via an asymmetric pure-color probe — disposable ortho camera
  - red `surfaceShader` cube, net-zero (undo recording suspended
    without flushing, selection / panel camera / scene-dirty flag
    restored, every node deleted on every path). The constant is demoted
    to fallback default; `MAYA_MCP_VP2_BOTTOM_UP=0|1` is the documented
    override (`src/maya_mcp_server/visual_module.py::_probe_vp2_direction`,
    `tests/test_visual_tools.py::TestVp2DirectionProbe`).
- **0.2.0 changelog wording correction (D-082c)** — the cache
  mechanism was described as "manifest-based"; the actual mechanism is
  fresh-metadata revalidation + per-file size/md5 re-verification
  (`src/maya_mcp_server/polyhaven.py::_verify_local`), with
  `manifest.json` a write-only audit artifact.

### Changed

- **Camera default names use the `CAM_` prefix (D-082f)** —
  `create_camera_shot`/`create_orbit_camera` and their `camera_create`/
  `camera_orbit` tool wrappers default to `CAM_shot`/`CAM_orbit`
  instead of `shot_cam`/`orbit_cam`, matching the project's own naming
  audit (`maya_scene_module.py::_MAYA_STANDARDS`,
  `src/maya_mcp_server/scene_tools.py:649`). Externally visible:
  unnamed camera calls now produce `CAM_*` nodes.
- **Generated materials are `standardSurface` (D-082e)** —
  `_new_material` emits Maya's PBR default instead of `blinn`; the
  texture wiring table is attribute-aware: color -> `color|baseColor`,
  roughness -> `specularRoughness|roughness`, metalness ->
  `metalness|metallic` (the old `reflectivity` fallback is dropped —
  specular strength is not metalness), normal -> `normalCamera`,
  AO -> `base|diffuse` (`src/maya_mcp_server/asset_module.py`,
  `tests/test_asset_module.py::TestStandardSurface`). The stub material
  attribute model was re-pinned to live-probe evidence: blinn carries
  `reflectivity`/`specularRollOff` but no `specularRoughness`;
  standardSurface has no `color`/`diffuse`/`ambientColor` and gains
  `base` (`tests/maya_stub/scene.py`).
- **README facade rework (T-20, D-078~D-081)** — design banner restored
  at top (`hero.svg` embeds a real `scene_viewport_snapshot` HUD capture
  in its viewport slot); all demo imagery consolidated into one
  `Prompt | Result` showcase table (involute watch movement, low-poly
  L-system bonsai, Poly Haven workbench, low-poly street block,
  Utah teapot recreation, `scene_review` before/after pair at 53.7→64.2)
  plus a full-width 20-frame orbit GIF; every image reference is an
  absolute `raw.githubusercontent.com` URL on `main` so assets render
  on PyPI (a living-branch reference by design — `.github/assets/` is
  append-only so historical release pages keep rendering); generation
  scripts checked in under
  `.github/assets-src/t20/`; `README.zh-CN.md` mirrored (anchor
  `f8e6869`). Retired: `hero.png`, `row1-4`, `shot-hero`, `shot-alt`.

## [0.2.0] - 2026-09-23

### Added

- **Poly Haven asset library (T-19a, D-074/D-075)** — two new tools
  bringing the count to 22:
  - `asset_search` — host-side Poly Haven index query
    (`api.polyhaven.com/assets`); read-only, needs no Maya session.
  - `asset_import` — downloads the model (FBX + textures) via
    HTTPS into a platformdirs cache, then imports into Maya.
  - Host-side guardrails in `polyhaven.py`: HTTPS + host whitelist
    (`api.polyhaven.com`, `dl.polyhaven.org`, `dl.polyhaven.com`),
    mandatory User-Agent, timeout, per-file 256 MiB / per-call 512 MiB
    caps, official per-file md5 verification + sha256 audit records,
    cache reuse gated on fresh /files metadata revalidation plus
    per-file size and md5 re-verification (`manifest.json` is a
    write-only audit artifact, never a cache-validity source —
    no silent stale-cache serving; offline yields the structured
    `network_unavailable` error).
  - Maya-side `_mcp_asset` (lazy-injected, zero network by design):
    fbxmaya plug-in gate, `FBXImportConvertUnitString=m` unit lock,
    `GRP_asset_<asset_id>` grouping with same-name dedup (repeat
    imports report the existing group; `force=True` opts out),
    default 100k-face polycount gate (`allow_high_polycount`
    override, audited), post-import dims sanity warnings, texture
    auto-wiring by filename suffix (`diff/rough/metal/nor_gl/ao/disp/
arm`) with sRGB/Raw color spaces and bump2d normal maps.
  - Both tools carry audit rows incl. download URL/size/hash detail,
    and are the first tools annotated `openWorldHint=True`
    (threat-model §4/§5 updated accordingly).
- **README restructure (T-19b, D-072)** — `README.md` is now the
  English source-of-truth; the Chinese text moved to
  `README.zh-CN.md` (convenience mirror carrying a `synced-with`
  commit anchor); `README_en.md` deleted. Language selectors sit
  above all content, and CI now runs a heading-skeleton parity check
  (.github/scripts/check_readme_skeleton.py) in the lint job.

### Fixed

- Stub/`maya.cmds` surface extended for the asset path
  (`maya.mel.eval`, `pluginInfo`, `loadPlugin`, `shadingNode`,
  `createNode`, `setAttr`, `connectAttr`, `disconnectAttr`,
  `polyEvaluate`, `exactWorldBoundingBox`, `sets` create/edit,
  `objectType isAType=dagNode`, FBX-import fixture via `cmds.file`)
  — all verified present on real Maya 2024 and recorded in
  `presence-baseline.json`.
- `ruff --fix` import-sort cleanups in `scene_tools.py` /
  `aesthetic_engine.py`; frozen budget ratcheted down
  (src E401/I001 -> 0).

## [0.1.2] - 2026-09-22

> First real-Maya verification round: everything below was found by
> running the shipped 0.1.1 code against a live Maya 2024 GUI session.
> The stub suite was green on all of it — each fix ships with the stub
> modeling the real semantic that was missing. A second live pass (T-18a)
> re-verified the whole gui tier on Maya 2024.0.0.4640.

### Fixed

- `create_module(overwrite=True)` now invokes the old module's
  `_mcp_teardown` hook before evicting it from `sys.modules`; the
  injected helper ships an idempotent teardown that disconnects the Qt
  `newConnection` signal, deleteLater's the listener and sockets, and
  restores stream capture. Reconnect-time helper reloads no longer orphan
  a listening `QtCommandServer` (upstream #4, D-058).
- Port-type probing switched from `1+1` to the bilingual
  `eval("1/2")` payload — Python 3 answers `0.5`, anything else
  classifies MEL. Live-verified on Maya 2024: a real MEL port replies
  with a syntax-error body (MEL `eval` wants a command string), which
  still classifies correctly — the Script Editor sees at most one
  error line per probe. Ports that answer as non-Python join a
  session-level permanent exemption set (lifted only when the port
  leaves LISTEN), ending the periodic re-probe spam (upstream #1, D-059).
- Probe filtering productized: `MAYA_MCP_INCLUDE_PORTS` /
  `MAYA_MCP_EXCLUDE_PORTS` env vars (or the `SessionManager`
  `include_ports` / `exclude_ports` ctor args) bound which discovered
  ports are probed at all.
- `maya_mcp_helper` refuses injection on Maya < 2023 (Python < 3.9)
  with an actionable message, and missing `ast.unparse` degrades to
  no-result-capture instead of `AttributeError` (upstream #3, D-060).
- `serverInfo.version` now reports the product version
  (`importlib.metadata.version("mcp-for-maya")` with a `__version__`
  source-tree fallback) instead of leaking the FastMCP framework
  version (D-060).
- The `_bootstrap` hot-update path now logs a failing `_mcp_teardown`
  carried in `create_module`'s `warning` field instead of dropping the
  response on the one path that triggers the hook (D-065/N1).
- `_bootstrap` hot-update now surfaces a failed `create_module` — the
  `module_create_failed` error arrives inside the result payload (the
  native commandPort wrap), and it was swallowed behind a false "module
  updated" log (N7). It now raises `MayaExecutionError` like
  `write_module` does.
- Bootstrap no longer rewrites a >10K helper module on every connect:
  the injected module's `_mcp_source_sha` stamp (a content hash of the
  helper source, self-stamped at inject time — no manual marker to
  forget) is compared first and the hot-update write is skipped when it
  matches. The big write is what tripped the commandPort stale-response
  quirk and intermittently stranded the just-started Qt server (connect
  refused on a live bind); skipping it removes the flake. A bounded
  connect+ping retry still covers genuinely slow Qt servers (T-18a,
  live-verified).
- Fallback hygiene: when the Qt channel is unavailable, the bootstrap
  reaps the orphan Qt listener it just started (a pre-existing listener
  is left alone — it may serve other clients), and the dedicated
  commandPort — on either the explicit native branch or the fallback
  path — is closed on `disconnect()` instead of leaking (T-18a).

- `execute_code` coerces `result_type` to `ResultType` at the client
  entry — bare `"JSON"` strings passed by `scene_tools`/`visual_tools`
  crashed with `'str' object has no attribute 'value'`, taking down the
  entire `scene_*` surface and both visual tools on real transports
  while the stub suite stayed green (D-046). Invalid values now raise
  `InputValidationError` instead of `AttributeError`.
- `scene_snapshot`/`scene_review`: removed a dead MItDag preamble that
  ran `MSelectionList.add("*")` + `getDagPath` — the wildcard matches
  non-DAG nodes on real Maya and `getDagPath` raises
  `TypeError: item is not a DAG path`.
- `scene_rollback`: the S2 scene rebind calls `cmds.file(rename=...)`
  alone — real Maya rejects flag combos with `-rename` ("the -rename
  flag must be used by itself"), which previously left the scene bound
  to `cp_*.ma` with `rebind_failed`.
- Visual capture: `MImage` resolves across Maya API layouts
  (`maya.api.OpenMaya` on ≤2024, `maya.api.OpenMayaUI` on 2025+). The VP2
  kFloat readback now asks for RGBA at the source
  (`readColorBuffer(img, readRGBA=True)`) and lets `writeToFile` do the
  float→byte conversion — the `floatPixels()` pointer-wrap path was
  removed entirely because `MScriptUtil` no longer exists in Maya 2024
  (D-056⑤; `convertPixelFormat` is a C++-only API and is never called).
  Row orientation stays under the assertion-pinned
  `_VP2_READBACK_BOTTOM_UP` constant — re-pinned to `False` on
  2024.0.0.4640, where readColorBuffer hands back top-down rows (T-18a).
  When neither module provides `MImage`, tools return a structured
  `capture_unsupported` error instead of `AttributeError`.
- Runtime `__version__` matches `pyproject.toml` (was 0.1.0 vs 0.1.1).

### Docs

- README (bilingual) + `docs/testing.md`: dual-runtime support matrix
  (host Python >= 3.10; injected helper needs Maya >= 2023 / Python >=
  3.9; verified on Maya 2024) and a multi-instance commandPort section
  covering per-instance topology, auto-scan vs `add_session`, and
  userSetup.py persistence (upstream #5, D-060).
- `docs/upstream-issue-status.md` maps the six upstream open issues to
  this fork's disposition, fix version, and verification status;
  human-gated comment drafts live in
  `docs/upstream-issue-response-drafts.md` (D-064).

### Testing

- New `gui` + `human_verify` markers (`--strict-markers` now enforced):
  the live-GUI tier env-probes for a reachable framed session and skips
  cleanly without one; `human_verify` holds explicit manual-confirmation
  skeletons — agents execute and leave evidence, sign-off is human
  (D-049a).
- Presence-baseline ratchet (D-049b/D-056①): `tests/maya_stub/
presence-baseline.json` collected on live Maya 2024 + allowlist +
  auto-diff tests — a stub symbol missing from real Maya now fails the
  build unless explicitly allowlisted with a reason. (Renamed from
  signature-baseline: cmds builtins carry no inspectable signature, so
  presence + method lists are the auditable contract.)
- Stub honesty: `ls` string patterns filter by name and `*` never
  crosses the namespace `:`; `cmds.file(rename=...)` enforces the
  standalone-flag constraint; `MSelectionList.add("*")` appends a
  non-DAG sentinel so `getDagPath` raises like real Maya.
- `execute_code` regression tests cross the real transport seam
  (fake reader/writer, wire-payload assertions) on both channels —
  closing the mock-the-transport gap that shipped the 0.1.1 defect.
- The `mayapy` tier initializes `maya.standalone` once per session;
  bare mayapy hard-crashes on `import maya.api.OpenMaya` without it.

### Known issues

- mayapy / `maya -batch` are environment-blocked on at least one dev
  box (native access violation / hang) — Tier-2 substance was verified
  through the live GUI session instead; see docs/testing.md.
- Multi-client rendering verification (Inspector/Claude Code/Codex
  seats) remains pending user environments.
- Maya 2025/2026 coverage absent — the `MImage` dual-layout fix is
  written for them but untested there.

## [0.1.1] - 2026-09-19

### Fixed

- `scene_render_preview`/`scene_viewport_snapshot` camera switching uses the `modelPanel`
  named-argument API instead of `cmds.lookThru`'s ambiguous positional forms; the
  single-argument fallback path is removed (D-039).
- `compute_lighting_quality_score` no longer raises `KeyError` on lights whose names
  match no role keyword (D-038; module stays dormant — zero production references —
  pending the T-06 consolidation decision).
- `scene_review` overlap and conflict checks, and `scene_plan` near-miss: the parent-child
  exclusion now uses a DAG-path prefix test instead of basename substring matching —
  sibling names like `GEO_wall`/`GEO_wall2` are correctly flagged again in all three
  dimensions (D-037).
- Native commandPort client: commands now carry the mandatory `\n` terminator (the
  channel executes on newline — without it a command parked in Maya's buffer until the
  next send); a dropped/closed connection now raises `MayaUnavailableError` instead of an
  execution error or a silent empty result, matching the Qt channel's typed errors (D-041).

### Testing

- Stub GUI surface: `modelPanel` added; `modelEditor`/`modelPanel` camera edits resolve
  node names; `lookThru` rewritten type-disambiguated (both arg orders, like the real
  command) with a warn-by-default / strict-on-demand policy for unclassifiable args
  (D-039).

## [0.1.0] - 2026-09-19

### Added

- Scene-intelligence layer on top of the upstream connection stack: 13 scene tools (snapshot / inspect / measure / assert / validate / checkpoint / rollback / checkpoint_list / camera_create / camera_orbit / aesthetics / review / plan) plus 2 GUI-only visual tools (scene_viewport_snapshot, scene_render_preview) — 20 MCP tools total.
- ICEV (Inspect-Compute-Execute-Verify) workflow enforced in server instructions; two experimental agent process cards under `skills/` (icev-workflow, scene-review-playbook).
- Unified pipeline across all tools: argument validation, per-session token-bucket rate limits, warn-only pattern scan, independent JSONL audit log.
- Transactional safety: exportAll in-memory checkpoints with whitelisted filenames, automatic safety snapshot before rollback, explicit scene rebind.
- Qt framed working channel (length-prefixed frames, up to 16 MiB) with a minimal native commandPort fallback for headless sessions.
- `maya_setup_guide` (diagnose / install / guide / uninstall) with idempotent marker-block merge into userSetup.py and timestamped backups.
- Project docs: CHANGELOG.md, CONTRIBUTING.md, SECURITY.md, docs/threat-model.md, docs/testing.md, dual README (中文 + English).
- GitHub Actions CI: a lint job enforcing the frozen ruff budget (`.github/ruff-baseline.json`, ratchet-down-only) and a pytest matrix over ubuntu/windows x CPython 3.10/3.x; a release workflow on `v*` tags (test -> build -> publish via Trusted Publisher, `pypi` environment, PEP 740 attestations); Dependabot for GitHub Actions.

### Changed

- Distribution renamed to `mcp-for-maya` (import package stays `maya_mcp_server`); console scripts are now `mcp-for-maya` plus `maya-mcp-server` as a compatibility alias; project URLs point to Xxx91n/mcp-for-maya.
- README rewritten: honest three-tier blender-mcp comparison, code-sourced numbers (20 tools, 11 audit checks with real weights, CoS figure attributed to its paper), a real `skills/` section, zero-telemetry statement, and a versioning policy.
- Two-layer error contract: host-side failures surface via MCP isError with a code prefix; Maya-side results return `{error:{code,message,suggestion}}`.

### Fixed

- JSON-serialized argument passing across scene tools (closes the string-interpolation injection surface in scene_validate / scene_measure / scene_assert).
- scene_checkpoint now snapshots in-memory state via exportAll instead of copying the last saved file; rollback whitelists basenames and rebinds the scene path explicitly.
- World-bbox math unified on the 8-corner transform for rotated objects.
- scene_review check list, weights, and sampling limits aligned to the implementation.
- add_session now uses the post-bootstrap dedicated-port client; Qt channel rewritten to event-driven reads with per-connection queues.
- Qt working channel maps the whole `ConnectionError` family (RST/FIN/aborted/refused) to `MayaUnavailableError`, fixing the Windows-only `test_connection_closed_raises_unavailable` failure (WinError 64).

### Removed

- Phantom references: LOGLEVEL env var, scripts/secrets.py, scripts/dependency.py, and the "4 Codex Skills" README promise (replaced by the real `skills/` cards).
