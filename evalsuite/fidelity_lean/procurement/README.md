# Procurement reference and bounded fidelity

Only `Interface.lean` is agent-facing. The reference, fixtures, emitted programs,
truth tables and keys are evaluator-only. No campaign or model call is made.
Use a distinct retained directory under the repository's ignored `tmp/` for every
material/candidate pair. Inject a runner into `validate` and `grade`; the core's
`trusted_lean_runner` is only for these generated programs and authored fixtures,
never retained agent submissions. Execution of untrusted Lean needs the campaign's
separate sandbox and staging boundary.

```python
from pathlib import Path
from evalsuite import generate, fidelity_procurement as procurement
from evalsuite.fidelity import trusted_lean_runner

key = generate.build_case('procurement', 511, 1)['key']
material = procurement.emit(key, Path('tmp/my-new-procurement-run'))
runner = trusted_lean_runner('/path/to/lean-4.24.0/bin/lean', timeout=300)
reference = procurement.validate(material, runner)
result = procurement.grade(material, procurement.fixture(material), runner, prove=True)
```

The API follows approvals: `emit`, `validate`, `fixture`, `grade`. Material includes
both report variants, distinguished by report-qualified claim IDs. Validation
compares every reference assessment field to `hard_procurement.assess`, every
single-slip assessment to that same engine with its named flag, and every claim
truth value to the original generator predicate. It also requires agreement with
the regenerated key's verdict, world count and true-world count. Every one of the
12 variants must be detected before any candidate score is trusted. Rule fidelity
counts complete input registers that agree in every assessment field and in row
order; claim fidelity counts claims that agree on every applicable input.
Malformed, short, duplicate, missing, warning-bearing or failing Lean output fails
closed. Unknown claim kinds, generator versions, changed keys, excessive input
counts and reuse of an execution directory also fail closed.

## Facts and worlds

The Interface preserves both neutrally named policy issues, their issue dates,
limits and optional exemptions. `World.inForce` is the document index's selection;
choosing an issue is part of the rule. Gregorian dates have an ordinal day and
an absolute month number; serialization establishes their consistency, without
preselecting a rule version, tier, converted amount or assessed amount. The ordinal
also allows Monday-to-Sunday grouping across month and year boundaries.

Recorded amounts, signatures and appointment ends preserve unreadable markers.
Separate typed alternative lists define the Cartesian completion; emitted sealed
worlds contain every combination. The reference does not treat an illegible
signature as missing. Recorded facts and assignments remain separate, so recorded
lookups retain their intended meaning. All sealed full registers are independently
included in rule grading as well as claim grading: a correct `Model.claim` cannot
hide a `Model.rule` that fails on large registers or open facts.

The local regeneration adapter calls `generate.family_builder` and checks the
entire regenerated key. This avoids the original core adapter's lookup through
`generate.FAMILIES`, which excludes lazily loaded hard families. Both generator
source files are hashed. The shared core `fidelity.regenerate` now does the same for any
family (it uses `family_builder` and hashes the selected builder module); this adapter keeps its
own provenance shape.

Several original predicates capture a `Data` object, directly or through a helper,
instead of reading `world['data']`. For counterfactuals, the adapter replaces only
those closure cells, recursively, with the input's `Data`. It preserves the
original function bytecode and defaults; there is no second Python rule engine or
predicate implementation. Sealed results are explicitly compared before and after
rebinding. Each modified `Data` gets fresh derived indexes and an empty rule cache.

## Unseen inputs

Rule inputs have a hard limit of 2,000 registers and claims a hard limit of 12,000
worlds each. Exceeding either raises an error; there is no truncation or sampling.
The deterministic bounded set combines compact one/two-row registers with every
sealed full register. The compact cases vary:

- Both index selections, each selected issue's limits and the amendment's limits,
  all available exemption limits, and each threshold minus one, equal, and plus one.
  These are crossed with the day before/on/after issue and effective dates and
  zero, one, two non-Finance, and two including Finance signatures.
- Every appointment's start and end (including both unreadable-end alternatives),
  on both sides and exactly on the endpoint, in all four currencies.
- Every review month boundary, a leap-day boundary and a December/January boundary,
  with days before/on/after. Rates include the current and previous months.
- Same requester/supplier groups with dates spanning Sunday/Monday, all weekdays,
  and one-component mismatches in requester or supplier. Additional pairs cross
  month and amendment dates, where those dates lie within a week.
- Whole-unit conversion and rounding before summation, with small fractional
  products and amounts around 10,000 units, in USD, GBP and CHF.
- All five staff roles crossed with all five acting roles, self/non-self requesters,
  and exemption absent/present at 200, 250 and 300 euro, including zero and all
  exemption neighbors.

Each claim receives every sealed world plus full-register perturbations. Global
amount/signature extremes exercise counts and totals; selective department and
currency failures exercise groups and comparisons; targeted amounts, currencies
and signatures exercise named rows; unreadable-value membership receives both
listed and outside alternatives. Date comparisons additionally receive each
period endpoint and its two neighbors. These are targeted products, not the full
Cartesian product of all facts and unknowns. `truth.json` retains every rule input,
label, expected assessment and per-claim extra world in order. `Inputs.lean` uses
shared facts and compact request updates to bound elaboration cost.

## Variants and fixtures

The variants are exactly `hard_procurement.SLIPS`; each switches one engine flag:

| Candidate name | Single slip |
| --- | --- |
| counts_requester_signature | Count the requester's own signature |
| ignores_finance_requirement | Omit Finance in tier 3 |
| ignores_delegation | Omit acting roles |
| applies_delegation_outside_dates | Ignore acting appointment date limits |
| ignores_amendment | Always use the in-force issue's original limits |
| applies_amendment_too_early | Apply the amendment from its issue date |
| treats_limit_as_exclusive | Use strict comparisons at both tier limits |
| uses_superseded_policy | Select the other issue's limits before amendment and its exemption throughout |
| uses_wrong_month_rate | Use the previous month's rate |
| ignores_same_week_rule | Assess each request alone |
| ignores_conversion | Use the raw amount as euro |
| counts_nonapproving_role | Count nonapproving roles |

`fixtures/Faithful.lean` is deliberately a structurally matching, separately
executable fixture engine. `fixtures/Slips.lean` supplies the 12 candidate rule
functions; `fixture` appends dossier-specific claim dispatch and `Candidate.model`.
Fixtures contain no reference-engine dependency. Faithful claim dispatch and
reference claim expressions share the adapter, so independent evidence for claim
semantics is their agreement against original Python predicate truth, not a claim
that two Lean claim implementations were independently authored. Wrong-request
and constant fixtures test failures of claim fidelity while retaining a faithful
rule. They are test subjects, never model outputs.

Other planted error labels (wrong values, overstated bounds, reversed direction,
neighboring rows, invented alternatives and treating unknowns as settled) change
claim parameters; they are not additional rule-engine variants. Both correct and
altered claims are checked by their own predicate and parameters.

## Claim coverage and limits

All 14 current kinds are graded: `request_compliance`, `required_tier`,
`assessed_amount`, `counting_approvals`, `failing_requests`, `failing_total`,
`tier_three_requests`, `department_failures`, `department_top`,
`failure_comparison`, `request_amount`, `request_signers`, `unreadable_signature`
and `unreadable_amount`. There are no excluded current kinds. A new kind without
an adapter is unsupported and raises `FidelityError`; it cannot earn fidelity.
The ten-dossier test measures the share, instead of assuming future coverage.

Nonformal lookups are tests of recorded facts, and unreadable-alternative claims
are membership tests over completed values. They do not establish that Lean has
read an original scan. In particular `unreadable_amount` follows the generator's
membership predicate, which does not separately test the currency wording.
`department_top` follows the generator's requirement of at least three failing
requests and a unique maximum; the prose by itself does not state the three-row
minimum. This is an explicit limitation on interpreting counterfactual fidelity
as equivalence to unrestricted English. The original dossiers satisfy the minimum.

Reference agreement establishes parity with the generator's operational reading,
not a proof that its English policy has no other reading. Counterfactuals are
synthetic facts, not an assertion that source documents were altered. Missing
references and incomplete resolutions are outside the well-formed input domain;
the evaluator supplies complete, consistent records.

Kernel rule agreement is optional and separate from executable comparison. The
default `proof_limit` is 200 complete-register inputs: these rules scan same-week
peers and resolve dated roles for every row, substantially more reduction than
one approvals request. The full set exceeds that limit, so `prove=True` retains an
explicit reason rather than claiming a kernel proof. A caller may deliberately
raise the limit; if a proof runs, it uses `by decide`, prints its axiom report and
must have no axioms. No Mathlib, Lake or retained agent file is used.

## Executed validation

The test suite retains source, output, hashes and timing JSON in its distinct
`tmp/fidelity-procurement-*` run directories. Linux execution uses the coordinator's
`remote-test.sh step3-procurement` stream. Measured sizes, scores and timing
are recorded below. All ten dossiers passed key cross-checks, reference agreement
and 12/12 mutation detection. All 280/280 generated claims were graded (100%).

Observed fixture rule scores for seed 511/index 1 (1,151 registers):

| Variant | Matching registers | Rule fidelity |
| --- | ---: | ---: |
| counts_requester_signature | 1076 / 1151 | 93.48% |
| ignores_finance_requirement | 1024 / 1151 | 88.97% |
| ignores_delegation | 993 / 1151 | 86.27% |
| applies_delegation_outside_dates | 1037 / 1151 | 90.10% |
| ignores_amendment | 986 / 1151 | 85.66% |
| applies_amendment_too_early | 937 / 1151 | 81.41% |
| treats_limit_as_exclusive | 1001 / 1151 | 86.97% |
| uses_superseded_policy | 635 / 1151 | 55.17% |
| uses_wrong_month_rate | 875 / 1151 | 76.02% |
| ignores_same_week_rule | 1059 / 1151 | 92.01% |
| ignores_conversion | 873 / 1151 | 75.85% |
| counts_nonapproving_role | 1049 / 1151 | 91.14% |

A separate executed kernel check on the authored faithful fixture proved the
stronger statement below by definitional equality in **2.306 seconds**, with output
`'fixture_rule_agrees' does not depend on any axioms`:

```lean
theorem fixture_rule_agrees (w : Procurement.World) :
    Candidate.model.rule w = Procurement.Reference.rule w := by rfl
#print axioms fixture_rule_agrees
```

This is possible because the authored fixture structurally mirrors the reference;
it is not a generic proof strategy for arbitrary agent submissions. The general
`grade(..., prove=True)` bounded reduction still reports its explicit limit.
The standalone proof, output and execution hash are retained on the host in
`tmp/procurement-kernel-a6ixs82m/FixtureAgreement.*` and copied locally into
`tmp/procurement-kernel-evidence-a6ixs82m/`. No source facts are needed by this
universal fixture-equivalence proof.

Reference execution on Linux (seconds include Lean elaboration and evaluation):

| Seed/index | Counterfactual registers | Completed source registers | Total rule inputs | Lean seconds |
| --- | ---: | ---: | ---: | ---: |
| 511/1 | 1097 | 54 | 1151 | 88.844 |
| 511/2 | 1094 | 36 | 1130 | 87.627 |
| 511/3 | 1236 | 54 | 1290 | 99.806 |
| 511/4 | 1094 | 54 | 1148 | 69.850 |
| 511/5 | 1241 | 36 | 1277 | 80.776 |
| 512/1 | 1238 | 36 | 1274 | 89.139 |
| 512/2 | 1094 | 54 | 1148 | 74.921 |
| 512/3 | 1097 | 54 | 1151 | 77.565 |
| 512/4 | 1238 | 36 | 1274 | 79.165 |
| 512/5 | 1236 | 24 | 1260 | 65.948 |

The faithful fixture scored 1151/1151 on rules and 28/28 on claims. The
wrong-item fixture scored 12/28 on claims (42.86%), with a faithful rule. The
constant fixture scored 0/28 on claims; every claim varied over its inputs.

Final required commands, observed on 12 November 2026:

- Mac: `/opt/homebrew/bin/python3.12 -m unittest tests.test_evalsuite_fidelity_procurement -v`
  — 11 tests in 61.354 seconds; OK, six Lean tests skipped because `AUDIT_TEST_LEAN`
  is unset.
- Linux: `sh /Users/danielsava/work/deep-proof/deep-proof-testing-docs/research_notes/lean-formalization-fidelity/remote-test.sh step3-procurement 'nice -n 10 python3 -m unittest tests.test_evalsuite_fidelity_procurement -v'`
  — 11 tests in 1321.535 seconds; OK, zero skips and zero failures.
- Mac: `/opt/homebrew/bin/python3.12 -m unittest tests.test_evalsuite_generate tests.test_evalsuite_scorecard tests.test_evalsuite_fidelity_core tests.test_evalsuite_fidelity_approvals`
  — 55 tests in 21.220 seconds; OK, seven approvals Lean tests skipped.

The final Linux run is retained at
`~/work/lean-fidelity-20261112/step3-procurement/src/tmp/fidelity-procurement-kjry6ro5/`.
Reference timing ranges from 65.948 to 99.806 seconds per dossier (mean 81.364).
Sources, execution hashes, outputs and result summaries are copied to local
`tmp/procurement-linux-evidence-kjry6ro5/`; full truth JSON remains in the original
host run and the separately emitted Mac materials. The combined slip test compiles
all twelve separately retained candidates once, with distinct namespaces; baseline
validation is reused only for identical in-process material copied to fresh run
directories. No final test result is taken from the earlier exploratory runs.

## Slip-free engine for planted errors

`fixtures/Neutral.lean` is `Faithful.lean` with every `flag` switch off and the switch
plumbing removed. `evalsuite/fidelity_plants.py` applies one named edit per entry of
`VARIANTS`; the planted-error generator checks each edited engine against
`truth['variant_expected']` on every unseen input.
