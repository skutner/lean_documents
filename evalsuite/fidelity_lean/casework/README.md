# Casework fidelity (evaluator only)

Only `Interface.lean` is handed to an auditor. The reference, fixtures, emitted
truth and unseen inputs remain evaluator-only. No model or campaign is run here.

```python
from pathlib import Path
from evalsuite import generate, fidelity_casework as casework
from evalsuite.fidelity import trusted_lean_runner

key = generate.build_case('casework', 511, 1)['key']
material = casework.emit(key, Path('tmp/a-distinct-casework-run'))
runner = trusted_lean_runner('/path/to/lean-4.24.0/bin/lean')
reference = casework.validate(material, runner)
score = casework.grade(material, casework.fixture(material), runner, prove=True)
```

The direct runner is for generated programs and authored fixtures only. Future
agent integration must inject its isolated runner. Candidate scoring programs
contain no reference engine or expected result vectors. The optional proof
program necessarily includes the reference. The output protocol is strict about
missing, duplicate and malformed vectors; it is not a sandbox against executable
Lean that deliberately forges output.

## Facts, truth and rules

Dates are positive Python Gregorian date ordinals (0001-01-01 is 1, a Monday).
The interface carries both neutral register issues, in fixed a/b order, and the
index of the issue in force. It does not preselect a register. Recorded missing
dates remain `none`; the world has separate completions keyed by `sent:NOTICE`
and `decided:REQUEST`. Every combination allowed by the cover notes and minutes
is emitted, separately from the recorded facts. No unknown is treated as settled.

A local adapter replays `assemble` and `claims_of`, verifies the complete regenerated
key and rendered sources, and records hashes of both generator modules. This is
needed because it must also return the replayed dossier object that `emit` reads. The shared
core `fidelity.regenerate` now also finds the harder families through `generate.family_builder`
and hashes the selected builder module (the four original families are unchanged); this adapter
keeps its own checks and provenance.
The Python rule expectations call the generator's `evaluate` with its `Rules`.
Every variant is cross-checked against that same engine, including all seven result
fields, not merely lateness. The two input swaps select the other issue or the
neighbour's row and extension before calling that engine.

Sealed-world claim expectations call the original generator predicates directly.
Those predicates capture cached results, so ordinary world-dictionary edits would
not test new facts. For counterfactuals the adapter rebuilds the generator's `Model`
and rebinds the original predicate bytecode to it. Count closures are rebuilt by
`Model.tally` with their original field selector; Post and class membership are
recomputed from the counterfactual register. No second Python rule or claim engine
supplies expected answers. Known fixed-layout closure assumptions fail closed.

The Lean reference implements all eight procedure paragraphs and amendment/index
selection. Calendar search has a finite bound of `7 * (number of holidays + 2)`:
that interval contains at least H+2 distinct Mondays and H holidays cannot block
all of them. Exhaustion and absent required completions are explicit invariant
errors, not alternative deadlines. Generated inputs use valid IDs, complete date
resolutions, exactly two register issues, positive periods and ordinary dates.

## Bounded unseen inputs

There are **899 synthetic rule inputs plus one per original register row**:
945–967 for the generator's 46–68 rows, below the 2,000 rule-input cap.

| Inputs | Coverage |
| --- | --- |
| 36 | All three classes, amendment absent/present, issued one day before/on/after both the effective date and effective date minus the old period. |
| 378 | Three classes × seven last-day weekdays × holiday absent/present × three reply channels × receipt before/on/after the deadline. Postal neighbours are adjacent working receipt dates. |
| 378 | Three classes × granted/refused/pending × decision before/on/after basic deadline × requested days 1–7 × holiday absent/present at the extended end. This covers each weekday at the extended end, extension exclusion for urgent notices and all decision states. |
| 105 | Every posting weekday × no holiday or a holiday 1–4 days after posting × each channel. |
| 2 | Both document-index choices, with different facts in the two issues. |
| 46–68 | Every original row with the first allowed completion, retaining dossier-specific periods, requested durations, holidays and input-swap witnesses. |

Each named-item claim receives every synthetic rule input installed at its target
row, with other rows retained, and its applicable stated date/day-count neighbours.
Extreme-date claims also receive winner and tie cases. Aggregate claims receive
every attainable count from zero through the register size, plus the sealed
worlds and global early/late, shifted/unshifted, extension/no-extension and index
changes. All claims receive those seven global changes. This is bounded targeted
coverage, not the Cartesian product of every rule input, every row and every
unknown completion. The cap is 12,000 worlds per claim; exceeding either cap raises
an error, never truncates inputs. Input order and recipes are retained in
`truth.json`, `Facts.lean` and `Inputs.lean`.

## One-rule and input slips

These are exactly `hard_casework.RULE_OF` plus its two input swaps (the module has
no separate `SLIPS` constant). A wrong report's generic `wrong_value`, `overstated`,
`asserts_unknown_as_fact`, `treats_unknown_as_settled`, and `names_runner_up` errors
are claim mutations, not extra rule-engine variants.

- `wrong_class_period`
- `ignores_amendment`, `applies_amendment_too_early`
- `counts_issue_day`
- `ignores_nonworking_shift`, `ignores_holiday`, `ignores_shift_after_extension`
- `counts_refused_extension`, `counts_pending_extension`, `counts_void_extension`
- `extends_urgent_notice`, `ignores_extension`
- `ignores_postal_rule`, `counts_calendar_days_for_post`
- `late_on_deadline_day`, `counts_deadline_day_in_days_late`
- `uses_draft_register`, `takes_neighbouring_row`

The generator supplies no neighbouring-row result for unmapped rows; a total Lean
rule needs an answer there. Both adapters use identity on unmapped IDs, and test
the actual swap on mapped IDs. This documented extension does not affect any
original generator claim. The other issue may be an older superseded issue or a
newer proposed issue that was never adopted; `uses_draft_register` retains the
generator's name in both cases.

## Claim coverage and limits

All 11 current kinds are graded: `deadline_date`, `reply_timing`, `days_late`,
`late_replies`, `shifted_deadlines`, `extensions_in_effect`, `extension_effect`,
`deadline_extreme`, `notice_lookup`, `receipt_date`, and `late_replies_by_post`.
`notice_lookup` is nonformal in the original key but is a decidable comparison
with the recorded issue date, so it can be graded. This does not assert that Lean
read or authenticated a source document. The coverage sample of seeds 511 and 512, indices 1–5, contains **280/280
graded claims (100%)**, counting both reports separately. No current kind is
excluded. Any newly introduced kind raises `FidelityError` rather than receiving a successful score.

Each claim counts as faithful only if its predicate agrees on every assigned
world. Report-qualified IDs keep both report variants separate. Before grading,
validation requires individual reference/variant agreement, every key verdict and
world count, and all 18 slips detected. The faithful fixture implements the rule
independently of the reference, and emitted fixture claim dispatch independently
expresses the closure's arguments. A kernel proof, when requested within the size
cap, checks agreement on the entire bounded rule-input set and prints its axiom
report. This is neither universal rule equivalence nor a claim-equivalence proof.

The source sentences still need human semantic review: cross-language agreement
establishes agreement with the generator's operational reading. No semantic
mismatch is presently known. The larger family's generator biases and wording
limits described in `evalsuite/README.md` remain; this extension does not fix them.

## Validation

Mac: `/opt/homebrew/bin/python3.12 -m unittest tests.test_evalsuite_fidelity_casework -v`.
Lean tests explicitly skip unless `AUDIT_TEST_LEAN` names a real executable.

Host: use `remote-test.sh step3-casework` with
`nice -n 10 python3 -m unittest tests.test_evalsuite_fidelity_casework -v`.
Tests regenerate ten dossiers (seeds 511 and 512, indices 1–5), grade both report
variants, check all slips, wrong-item and constant candidates, malformed output,
key drift and one axiom-free kernel agreement proof. Test output reports actual
coverage, input counts, fixture scores and Lean seconds; generated programs,
outputs and execution hashes remain in distinct ignored `tmp/` directories.

## Slip-free engine for planted errors

`fixtures/Neutral.lean` is `Faithful.lean` with every `slip` switch off and the switch
plumbing removed (the public rule is `rule`). `evalsuite/fidelity_plants.py` applies one named
edit per entry of `VARIANTS`; the planted-error generator checks each edited engine against
`truth['variant_expected']` on every unseen input. `Casework.World.neighbour` is not compared as a fact.
