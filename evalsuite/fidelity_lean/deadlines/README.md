# Deadlines fidelity (evaluator only)

`Interface.lean` is the only agent-facing file. It describes facts and function
signatures without giving the rules, claim constructors, or answers. The remaining
files and everything emitted by `evalsuite.fidelity_deadlines` are evaluator-only.
Use `emit(key, new_directory)`, `validate(material, runner)`,
`fixture(material, slip=...)`, and `grade(material, candidate, runner, prove=True)`.
The injected runner is mandatory. Direct Lean execution is authorized only for
these generated programs and authored fixtures, never retained agent code.

## Representation and reference

Dates use Gregorian ordinals (`0001-01-01 = 1`), so calendar-day addition crosses
month, year and leap-day boundaries without a calendar library. Signed `Int`
differences preserve early replies. A recorded missing reply date is distinct
from the completed world's optional reply: `none` there means no reply received.
Undecided grants resolve independently to both Boolean values. All sealed worlds
are emitted: 1, 2, 152, or 304 depending on which facts are unknown. Missing replies
use the generator's bounded domain: none or each date from issue through issue +
150 days. This is a generator assumption, not a bound established by the prose.

The generator precomputes notice `version` and `period`, then its closures use
those fields. The emitter recovers the change date and both periods from the
**exact** generated policy sentences and checks every recorded derived field.
Counterfactual notices receive consistent derived fields before the original
Python closures are called. Lean instead computes version and period from the
policy and issue date. This makes wrong-version errors measurable. The small
Python policy bridge is an explicit trusted adapter; changed sentence shapes,
period domains, keys, versions, or unsupported claim kinds fail closed. It does
not infer truth from key verdict counts.

`Rule` returns an `Outcome`: version, ordinal deadline, signed optional days late,
late status, and on-time status. Rule fidelity counts an input as matched only if
**all five** fields match the generator oracle. A strict family-local protocol
parses outcome vectors; the shared core handles Boolean claim vectors. Numeric
outputs are not replaced by a small set of equality probes. Candidate scoring
programs contain no reference implementation. The optional agreement program
includes the reference and proves agreement on the entire finite set with
`by decide`, then requires an axiom-free report. This is not universal equivalence
or a proof that an original document was transcribed correctly.

## Bounded unseen set: 1,245 rule inputs per dossier

* 360: issue dates change − 1, change, change + 1; five extension/resolution states
  (absent, granted, refused, undecided/false, undecided/true); requested days
  0, 1, 5, 7, 10, 14; replies absent, deadline − 1, deadline, deadline + 1.
* 240: all 20 ordered distinct period pairs from 14, 21, 30, 45, 60, crossed with
  the three change-date positions and four reply positions; granted seven days.
* 600: 2032-02-28, 2032-02-29, 2032-03-01, 2032-12-31 and 2033-01-01,
  crossed with all five extension states, six requested-day values and four
  reply positions.
* 45: three change-date positions × five extension states × three resolved reply
  dates (deadline − 1, deadline, deadline + 1), keeping the recorded reply missing.

All categorical rule inputs occur. Every comparison boundary has witnesses
below, at, and above it. Extension duration is an additive value, not a threshold;
zero, one, and every duration the generator selects are included. Some inputs
intentionally repeat; their order and multiplicity are retained in `truth.json`.
Hard limits are 2,000 rule inputs and 12,000 worlds per claim; no silent truncation.

Each claim sees every sealed world and substitutions of all rule inputs into its
target notice. Count claims receive each notice substitution separately and
additional worlds realizing every count from zero to the number of notices.
Date equality and timing equality claims also receive their stated value and
adjacent values where realizable. Claim fidelity is the fraction of claims that
agree on **every** applicable world. This is bounded testing, not the Cartesian
product of all counterfactual rows and all unknown resolutions. `truth.json`
retains the ordered input recipes and extra worlds so indices are reproducible.

## One-rule variants and fixtures

Each variant has a separate authored candidate implementation in `fixtures/`;
none aliases the reference. The complete fixture is assembled with independently
emitted claim dispatch, using report-qualified IDs.

| Variant | Single slip | Source or generated error |
| --- | --- | --- |
| `wrongRuleVersion` | Swap applicable old/new version and its period | R-01/R-02; `wrong_rule_version` |
| `changeDayUsesOld` | Change day belongs to the old rule | R-02 inclusive boundary |
| `wrongPeriod` | Add one day to the selected period | R-01/R-02 numeric period |
| `countsNoticeDay` | Subtract one day from deadline | R-03; `counts_notice_day` |
| `ignoresExtension` | Granted extensions add no days | R-04; `ignores_extension` |
| `refusedGranted` | Refused requests extend deadline | R-04; `assumes_extension_granted` |
| `undecidedGranted` | Undecided requests extend even in false resolutions | R-04; `assumes_extension_granted` |
| `deadlineIsLate` | Equality counts as late instead of on time | R-03; `flips_outcome` |
| `wrongDayCount` | Signed days-late arithmetic is one too high | R-03; `wrong_day_count` |
| `missingReplyIsLate` | No received reply counts as a late received reply | G-02; `asserts_unknown_as_fact` |

Wrong count, overstated count, unknown-as-fact, wrong date, and overlooked recorded
value errors concern individual claim statements. They are covered by original
predicate comparison, both report variants, count witnesses, and equality/status
counterfactuals rather than inventing additional policy mutations. A wrong-notice
fixture and a world-ignoring constant fixture separately exercise claim fidelity.
Test strength must distinguish every rule variant before grades are trusted.

## Claim coverage and limits

All seven generated kinds are fact tests and are graded: `reply_deadline`,
`reply_timing`, `rule_version`, `late_replies`, `unrecorded_reply`,
`undecided_extension`, and `notice_lookup`. The last three are marked non-formal
by the generator but still have predicates over recorded structured facts.
`unrecorded_reply` and `undecided_extension` test recorded markers, not resolved
values; they do not claim that Lean read the source documents. There are no
unsupported current kinds. Any new kind without an explicit adapter raises
`FidelityError` before emission and receives no grade.

Over seeds 511 and 512, indices 1–5 each, both reports contain **186 claims;
186/186 (100%) are graded**. All seven kinds and all generated error types occur
in those ten dossiers' full slot pools. Both selected reports are cross-checked
against every individual Python truth value, then key verdict and world counts.

The operational reference is faithful to the generator within the stated finite
world domain. The principal semantic limit is that the documents do not bound
unrecorded reply dates to 150 days: that bound belongs to the synthetic oracle.
Policy selection also requires the explicit bridge described above because the
generator does not retain policy parameters in its base world.

## Execution and retained evidence

On macOS use Python 3.12. Tests requiring Lean skip explicitly unless
`AUDIT_TEST_LEAN` names a binary. The authorized host command is:

```sh
sh /Users/danielsava/work/deep-proof/deep-proof-testing-docs/research_notes/lean-formalization-fidelity/remote-test.sh step3-deadlines 'nice -n 10 python3 -m unittest tests.test_evalsuite_fidelity_deadlines -v'
```

No Lake, Mathlib, campaigns or model calls are used. Every run has a distinct
retained directory under `tmp/fidelity-deadlines-*`; generated sources, truth,
execution hashes/timings, stdout/stderr, and grade reports remain there. Reusing
an output directory, reference execution, or fixture destination fails closed.
The trusted-fixture runner and tagged output protocol are not a security boundary
against hostile Lean. Future real submissions require a sandbox and evaluator
artifact isolation. Like approvals, this module grades rules and claims; it does
not add fact-table accuracy scoring.
