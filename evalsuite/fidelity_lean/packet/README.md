# Packet formalization fidelity

Only `Interface.lean` is agent-facing. Reference, fixtures, emitted facts, unseen
inputs and truth are evaluator-only. This module neither runs models nor grades
retained agent files with the direct Lean runner.

```python
from pathlib import Path
from evalsuite import generate, fidelity_packet as packet
from evalsuite.fidelity import trusted_lean_runner

key = generate.build_case('packet', 511, 1)['key']
material = packet.emit(key, Path('tmp/a-distinct-packet-run'))
runner = trusted_lean_runner('/path/to/lean-4.24.0/bin/lean')
reference = packet.validate(material, runner)
candidate = packet.fixture(material)
grade = packet.grade(material, candidate, runner, prove=True)
```

Use a distinct retained directory for each candidate. The injected runner is the
execution boundary; the direct runner is only for these generated programs and
authored fixtures. Lean is invoked directly with core `Std`, without Lake or
Mathlib. Candidate scoring programs do not contain the reference. The optional
agreement proof necessarily does. The output protocol does not defend against
hostile executable Lean, and this is not campaign sandbox integration.

## Facts, dates and possible worlds

The adapter regenerates and compares the entire key before recovering the
generator's original `may_stay`, `staying` and claim predicates. It records the
generator version/source hash and the reference hash. The review date and age
limit are recovered from the fixed source sentences, with fail-closed layout
checks. A changed key, new claim kind, unknown-certificate domain, unsupported
period or oversized input set is an error.

Certificates retain Pass/Fail and a calendar ordinal day (0001-01-01 = 1).
Supplied certificates keep their actual generated dates. An absent certificate
is `none` in the recorded facts. Its hidden sampled result/date are never copied
into the emitted facts. The generator permits four combinations: Pass/Fail ×
in-time/out-of-time. We preserve precisely those four classes by using age
`limit` and `limit + 1` as representative dates. These dates are not factual
assertions about a missing document. Lean's `possibleWorlds` computes the
Cartesian product; validation compares it with a separate Python enumeration.
The tested ten dossiers contain 1, 4, 16, 64 or 256 completed worlds.

The Python oracle projects each counterfactual date to `in_time` using Python
calendar-date subtraction, as the generator does, then invokes the **original**
generator predicate. Lean independently subtracts ordinal days and applies M-02:
Pass and age at most the dossier's limit. Unknowns remain separate from the
transmittal sheet's assertions. The generator's alternate `told` worlds are not
the source of truth.

## Unseen set and its limits

There are **264 rule inputs** per dossier:

- Every age limit: 180, 365 and 540 days.
- Four review anchors: 2032-02-29, 2033-03-01, 2033-01-01, and the dossier's review
  date. The last anchor can duplicate another anchor without removing a case.
- Eleven ages: 0, 1, 179, 180, 181, 364, 365, 366, 539, 540 and 541 days.
- Both categorical results: Pass and Fail.

Thus every threshold is crossed at `limit - 1`, `limit`, and `limit + 1`, for
both results and each calendar anchor. Month, year and leap-day subtraction are
included. The generator only samples test dates before the review; future dates
are outside this domain, and no unsupported lower-age rule is invented.

Each supported claim receives every sealed world. A unit-specific claim also
receives all 264 inputs substituted for its target certificate. A count claim
receives these substitutions at every unit and additional worlds realizing
every count from zero to the number of units. Other units use the first completed
world, shifting all test dates with the review anchor to preserve their ages.
Changing the limit keeps these ages fixed. This is not the
full Cartesian product of all counterfactuals and unknown resolutions.
The maximum permitted sizes are 300 rule inputs and 5,000 worlds per claim;
exceeding either fails rather than silently truncating. `truth.json` retains all
rule inputs, sealed worlds, original generator worlds and claim input recipes.

## One-rule variants

Each variant is separately implemented in Reference and the candidate fixtures.
They are derived from M-02 and the generator's planted `ignores_failed_result`,
`ignores_age_limit` and `flips_outcome` errors. Packet has no superseded policy
version. A wrong period and a day-count arithmetic slip exercise the numeric
parts of M-02. Document-inventory errors and `wrong_location` are outside this
interface and are not presented as rule mutations.

| Variant | Single change | Rule agreements out of 264 |
| --- | --- | --- |
| Faithful | None | 264 (100%) |
| `ageExclusive` | Uses strictly less than the age limit | 252 (95.4545%) |
| `ignoresFailedResult` | Drops the Pass requirement | 180 (68.1818%) |
| `ignoresAgeLimit` | Drops the age requirement | 216 (81.8182%) |
| `flipsOutcome` | Negates the service result | 0 (0%) |
| `wrongPeriod365` | Always uses 365 days | 240 (90.9091%) |
| `dayCountMinusOne` | Subtracts one from the elapsed-day count | 252 (95.4545%) |

These counts follow from the grid; executed fixture results are retained in
`grade.json`. Reference validation must distinguish all six mutations (100%
test strength) before any grade is trusted. Faithful rule agreement on the
entire 264-input set is small enough for a kernel-checked `by decide` theorem;
its axiom report must say it depends on no axioms. This proves only bounded
rule agreement, not universal equivalence or claim agreement. A caller-specified
smaller `proof_limit` yields an explicit skip reason.

## Claim coverage and fail-closed scoring

Both reports are separately graded with report-qualified claim IDs. A claim
passes only if it agrees on **every** applicable world. There are three supported
kinds:

- `service_status`: the rule's result for the specified unit, including negation.
- `units_in_service`: exact counts and lower bounds.
- `certificate_result`: the categorical value in a completed certificate. This
  is a representation test; it does not prove that a physical document records
  that result. For an absent document the generator's uncertainty is preserved.

Two kinds are explicitly unsupported:

- `packet_contents`: supplied-file membership is outside the date/result
  interface. No Lean result is claimed for a missing or overlooked document or
  an echoed transmittal-sheet assertion.
- `unit_location`: the original Python predicate ignores the world and closes
  over the original unit's location. Treating it as a changing-world test would
  silently replace the generator's predicate. Constants are not awarded credit.

For seeds **511 and 512**, indices **1–5**, both variants total **140 claims**:
19 certificate results, 8 service statuses, 20 service counts, 48 packet contents,
and 45 locations. **47/140 = 33.571429% are graded**. All counts are computed by
tests from regenerated dossiers, not sampled from retained audits.

`claim_fidelity` includes every generated claim in its denominator and grants
unsupported claims no credit. `graded_claim_fidelity` separately reports fidelity
within the supported subset, and `claim_coverage` always exposes the subset size.
Every unsupported result has a reason and `agrees_everywhere: false`. Thus a
faithful candidate can score 100% on rules and supported claims without falsely
claiming 100% coverage. `key_cross_check_scope` explicitly lists the three Lean
checked kinds; unsupported Python key counts are checked during emission but
are not advertised as Lean validation.

## Verification

```sh
/opt/homebrew/bin/python3.12 -m unittest tests.test_evalsuite_fidelity_packet -v
sh /Users/danielsava/work/deep-proof/deep-proof-testing-docs/research_notes/lean-formalization-fidelity/remote-test.sh step3-packet 'nice -n 10 python3 -m unittest tests.test_evalsuite_fidelity_packet -v'
```

The Mac explicitly skips the six Lean tests unless `AUDIT_TEST_LEAN` names a real
binary. The host must execute all tests without skips. Tests cover ten regenerated
dossiers, all unknown combinations, every input boundary/category, original key
counts/verdicts, reference agreement, mutation strength, faithful and slip
fixtures, wrong-unit claims, constant claims, malformed/truncated output,
corrupted oracle expectations, incomplete candidates, and the kernel proof.
Sources, output, hashes, timings and reports remain under each run's `tmp/`
directory. No existing generator, approvals or shared core files are changed.

The reference is faithful to the generator on its finite date/result domain;
document membership, location semantics, extraction accuracy, and interpretation
of future dates remain outside this result. A shared core coverage/status schema
would be useful for families with unsupported claims; this module implements it
locally without requiring a core change.
