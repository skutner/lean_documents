# Generated formalization fidelity

This is an evaluator-only extension; existing generation and scoring modules are
unchanged. `approvals/Interface.lean` alone is safe to hand to an auditing agent.
Never stage Reference, fixtures, generated material, or this evaluator's truth
with agent inputs. No campaign or model is run by this module.

## Approvals API

```python
from pathlib import Path
from evalsuite import generate
from evalsuite import fidelity_approvals as approvals
from evalsuite.fidelity import trusted_lean_runner

key = generate.build_case('approvals', 511, 3)['key']
material = approvals.emit(key, Path('tmp/my-distinct-evaluator-run'))
# Explicitly only for our generated programs and authored fixtures, NOT agents.
runner = trusted_lean_runner('/path/to/lean-4.24.0/bin/lean')
reference_report = approvals.validate(material, runner)
candidate = approvals.fixture(material)
report = approvals.grade(material, candidate, runner, prove=True)
```

A supplied key must equal the regenerated key, including the generator version.
The emitter replays the family builder's RNG, matches selected claim rows to slot
closures, and calls the generator's original predicates. The approvals Python
rule oracle is the `meets` closure captured by `policy_compliance`. This deliberately
version-sensitive adapter avoids a second Python policy implementation. The
material records the generator version and SHA-256 of `generate.py`; changed keys
and unsupported claim kinds fail closed.

`Facts.lean` preserves recorded signatures and separately lists every completed
world. `Inputs.lean` defines counterfactual input recipes. `truth.json` contains the
Python answers, all rule inputs, all sealed worlds, and ordered per-claim recipes
and extra worlds, so input indices are reproducible. Concatenated standalone Lean
programs need only `import Std`, with no `.olean` build or Lake invocation.

There are **1,281 rule inputs** per dossier: 7 requesters × 3 boundary amounts × 57
ordered signature lists of length 0–2 = 1,197; plus 7 people × 4 roles × 3 amounts
= 84 targeted role variations. Duplicate and self signatures are included. The
amounts are `limit - 1`, `limit`, and `limit + 1` for the dossier's own threshold
(2,000, 5,000, or 10,000), not a hard-coded 5,000.

Each claim is tested on every sealed world and applicable counterfactual worlds:
replace its target request with each rule input; for failure-count claims, replace
each request in turn and also exercise every attainable count. All claims receive
all single-person role changes. Lookup claims also get their stated amount and its
two neighbors crossed with every requester; illegibility claims get missing, named,
and illegible recorded signatures. Other facts use the first completed world.
This is bounded testing, not the full Cartesian product of all counterfactuals
and all unknown resolutions. Limits are 2,000 rule inputs and 12,000 inputs per
claim; exceeding them is an error, never silent truncation.

A claim passes only if **every** applicable input matches the generator predicate.
Both report variants are separate, report-qualified claim IDs. Rule fidelity is
per input. Test strength must distinguish all four variants: `boundaryInclusive`,
`requesterCounts`, `financeDropped`, `distinctDropped`. Reference checking compares
individual truth values as well as key verdicts and world counts. The optional
kernel proof uses `by decide` for rule agreement on the entire bounded unseen set,
prints its axiom report, and requires no axioms. It is not a universal equivalence
proof or a proof of claim agreement. Sets larger than `proof_limit` get an explicit
skip reason. Source, output, source hash, timing and result JSON remain in the run
folder. Use one distinct run directory per candidate; do not delete evidence.

The injected runner has signature `Callable[[Path], LeanResult]`. Future campaign
integration must supply the sandboxed runner from its own stream. The direct
runner is explicitly trusted-fixture-only; it is not a security boundary. Candidate
source is executable Lean and the output protocol is not protection from hostile
output. Candidate scoring programs omit the reference implementation. The proof
program necessarily includes the reference; future sandbox staging must also
isolate evaluator artifacts from the submission's read access.

## Semantic limits

The four policy sentences have an unambiguous operational translation for this
finite domain (listed staff, nonnegative euro amounts, at most one illegible
signature per request). No discrepancy with the Python rule was found in the
fixture checks. `signer_role` is marked non-formal by the generator and tests only
the role, although its wording also says the person signed a particular request.
This implementation faithfully measures that narrower generator predicate; it
does not establish the signing presupposition. `illegible_signature`, also marked
non-formal, is a decidable test over the **recorded scan marker**, not the signer
identity in a completed world. Request lookups likewise test recorded facts.
These are representation tests, not claims that Lean verified an original scan.
Seed 511/index 3 reproduces every request and role in the worked probe with
generator version 4.

## Add another family without changing approvals

1. Add `evalsuite/fidelity_FAMILY.py` and `fidelity_lean/FAMILY/Interface.lean`,
   `Reference.lean`, and authored `fixtures/`. Add `tests/test_evalsuite_fidelity_FAMILY.py`.
   Import shared `evalsuite.fidelity` utilities and the unmodified generator.
2. Implement `emit(key, directory)` with a family-specific closure adapter and
   serialized facts, unknowns and worlds. Recover the actual builder using
   `regenerate`; validate claim kinds and never reconstruct truth from counts.
3. Define family-specific bounded unseen inputs with all rule boundaries, plus
   named one-rule variants. Explain what each input covers and what is omitted.
   Compute rule expectations using the generator's Python rule logic and claim
   expectations using each original `pred`. Keep signatures/layout alone in the
   interface; claim constructors or answer-bearing code belong evaluator-side.
4. Implement `validate(material, runner)` and `grade(material, candidate, runner)`.
   Reuse `run_checked`, `parse_vectors`, `agreement`, `verdict`, and `LeanResult`.
   Require key counts/verdicts, reference agreement, and 100% mutation detection
   before trusting a grade. Keep runner injection mandatory. Do not execute
   retained agent files through `trusted_lean_runner`.
5. Copy the approvals tests: regeneration/version drift; all unknown combinations;
   input boundary coverage; malformed/short output; ten dossiers/two seeds; faithful
   fixture; every slip below 100%; wrong-row and constant-claim failures; and one
   kernel proof with axiom report. Use distinct retained directories under `tmp/`.
   Lean tests must skip explicitly unless `AUDIT_TEST_LEAN` names a real binary.

Run locally with Python 3.12 or newer:

```sh
/opt/homebrew/bin/python3.12 -m unittest tests.test_evalsuite_fidelity_core tests.test_evalsuite_fidelity_approvals -v
```

For the approved host, use the coordinator's `remote-test.sh step3` command; it
sets `AUDIT_TEST_LEAN` to Lean 4.24.0. No campaigns or coding-agent CLIs are involved.

## Interface scoring and planted errors for casework and procurement

`fidelity_score.py` grades `work/fidelity/Candidate.lean` for all six families. For casework
and procurement the recorded fact cells are compared like the others, except that
`Casework.World.neighbour` (evaluator-supplied row-confusion pairs that no source states) is
not a fact cell and the prompt asks for `[]`. The interface arm stages `Interface.lean` for all
six families, and the runner's leak markers and fixture/Reference guard cover them.

Their fixtures (`fixtures/Faithful.lean`) keep every slip behind a string switch, so a blind
planted-error file cannot be cut from them. `fixtures/Neutral.lean` is the same engine with every
switch off, and `evalsuite/fidelity_plants.py` holds one named edit per generator slip (18
casework, 12 procurement). `fidelity_mutants.py` builds each blind file from that engine and
refuses to keep one whose rule output differs from the generator's output for the named slip.

Planted fact errors are measured by fact accuracy: the recorded cells of the submitted table are
compared with the generator's, using the scorer's own serializer and comparison. A fact plant
is effective when that rate falls below 100%. The unseen-input graders still ignore submitted
facts, so the rule and claim rates of a fact plant stay at 100%.
