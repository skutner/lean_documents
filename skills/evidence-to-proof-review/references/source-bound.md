# Source-bound Markdown claim audits

Use `source-bound-v1` only when explicitly selected at preparation. It retains
`Profile: markdown_claim_traces_v1` and adds the manifest-bound
`Formalization profile: source-bound-v1`. With that field absent, the existing
Markdown workflow and its outputs are unchanged. Unknown profiles fail.
This profile audits the supplied report; it does not create the original report.

The existing [Markdown-native workflow](markdown-native.md) still requires
complete passage registration, one exact report claim per finding, substantive
source and argument review, and five individual trace steps. Use ASCII claim
identifiers such as `C1` or `C_01` for generated declarations. Unsupported shapes
are explicit `not_attempted` findings with “not formalized”; never substitute a
free theorem or omit such a claim from coverage.

## Prepare and extract

```sh
SKILL=/absolute/path/to/skills/evidence-to-proof-review
python3 "$SKILL/scripts/markdown_audit.py" prepare \
  --report report.md --sources sources --temp-root tmp/my-audit \
  --lean-version 4.24.0 --formalization-profile source-bound-v1 --locale en
```

Save the printed path as `WORKSPACE`. Preparation initializes `source-bound/`
and `models/Rules.lean`. The following command is idempotent and does not replace
existing authored content:

```sh
python3 "$SKILL/scripts/source_bound_audit.py" init --workspace "$WORKSPACE"
python3 "$SKILL/scripts/source_bound_audit.py" shapes
```

`--locale en|ro` binds the source and report locale at intake. Optional repeated
`--source-locale input/sources/1/register.md=ro` selects a source-specific locale;
`input/report.md=ro` selects the report locale. These become immutable
`input/source-bound-bindings.md` entries with roles and passage anchors. Do not
edit that file or the manifest later to change interpretations. Prepare a new
workspace when intake declarations change. Mixed-locale documents requiring
per-anchor overrides are not exposed by this adapter yet; do not guess a locale.
Native PDF/DOCX/OCR inputs use the existing extraction and uncertainty review.
Facts must cite preserved readable projections and accepted hash-bound reviews
when uncertainty requires them. Missing converters or illegible evidence are
not successful extraction.

Use `add-fact` to calculate UTF-8 offsets and canonical values. Select the entire
typed token, including a required currency or count unit. A quote and token that
repeat require explicit zero-based occurrence indices.

```sh
python3 "$SKILL/scripts/source_bound_audit.py" add-fact \
  --workspace "$WORKSPACE" --id amount --type 'money(EUR)' \
  --source input/sources/1/register.md --anchor S1 \
  --quote 'Recorded amount: EUR 2.00.' --token 'EUR 2.00'
python3 "$SKILL/scripts/source_bound_audit.py" add-fact \
  --workspace "$WORKSPACE" --id claimed --type 'money(EUR)' \
  --source input/report.md --anchor R1 \
  --quote 'The amount is EUR 2.00.' --token 'EUR 2.00'
```

`--occurrence` selects a repeated token within the quote; `--quote-occurrence`
selects a repeated quote within the anchored passage. `--unit-source UNIT_ID`
references an already registered quoted unit/header fact. Source/report roles
come from intake, not the fact author's assertion. Copied reports cannot supply
independent source facts. Report-origin values cannot feed rules or predicates.
Unknown fields, duplicate IDs, wrong values, partial tokens and stale quotes
reject the record without replacing `facts.md`.

Append typed derived records in `source-bound/facts.md`; no literal values or
Lean expressions belong in these records:

```text
## Derived amounts
Type: list(money(EUR))
Operation: list
Inputs: amount_A, amount_B

## Derived total
Type: money(EUR)
Operation: sum
Inputs: amounts
```

The generated `source-bound/provenance.md` contains the fact summary, source
links, exact quote selections, hashes and inherited evidence. Generated Lean
never contains quote text. Provenance does not establish source authenticity,
complete row selection or the intended meaning of a policy.

## Register claims and pure rules

For the simple equality example, write `source-bound/claims.md`:

```text
## Claim C1
Report-anchor: R1
Shape: equal
Version: 1
Polarity: positive
Proof-relation: supports
Rules:
Scope:
Rationale: Compare the reported amount with the independently recorded amount.
Slot a: amount
Slot b: claimed
```

`shapes` lists all twelve shapes, their slots, constraints and fixed choices.
Slots accept identifiers or catalogue choices, never Lean text. `Polarity`
expresses the report claim; `Proof-relation` independently selects `supports` or
`refutes` (default `supports`). Refutation proves the complete claim's negation.
Inapplicable domains, unresolved inputs and unsupported shapes produce no target.
Use a reasoned `not_attempted` finding and preserve its documentary assessment.

For predicate/projection rules, `source-bound/registrations.md` adapts readable
registrations to the shared typed contract. It accepts only the following fixed
record kinds. Rule and scope records each contain one exact policy quotation;
multiple rules/scopes can cite different passages. No record is executable code.

````text
## Rule eligible
Parameters: x:nat
Result: bool
Dependencies: minimum
Correspondence: Each amount must meet the independently quoted minimum, inclusively.
Source: input/sources/1/policy.md
Anchor: P1
Quote-start: 0
Quote:
```
Each amount must meet the minimum of 2.
```
````

Here `minimum` must be a source fact extracted from that policy. The matching
`models/Rules.lean` is:

```lean
import SourceBound.Facts
def Rules.eligible (x : Nat) : Bool := decide (x >= SourceBound.Facts.minimum)
```

Parameters use the contract type grammar (`nat`, `money(EUR)`, `list(nat)`, etc.);
Lean definitions use the generated type ABI. Dependencies must match every
referenced source value and registered rule. Rules require fixed imports and
nonrecursive typed definitions. No authored theorem, macro, instance, notation,
command, numeric literal (even zero/one), string-derived number or closed numeric
computation is accepted. Thresholds come from independent facts. Syntax checking
does not establish faithful policy interpretation; disclose the correspondence.

Domains and explicit subset scopes use these records:

````text
## Enum evidence_state
Version: 1
Labels: Absent | Not recorded | Present

## Scope selected_period
Source: input/sources/1/policy.md
Anchor: P2
Quote-start: 0
Quote:
```
The table covers the selected reporting period.
```
Rationale: The cited scope limits the selected rows to this reporting period.
````

Enum labels remain distinct. Neither “Not recorded” nor missing evidence implies
false. Scope evidence does not itself prove completeness. The parser rejects
unknown/duplicate fields, IDs and malformed references. Each fact, derived node,
rule and scope shares one identifier namespace. Labels are separated by `|`.

## Regenerate and execute

```sh
python3 "$SKILL/scripts/source_bound_audit.py" regenerate --workspace "$WORKSPACE"
```

Inspect `source-bound/rendered.md`: fixed English, exact target, polarity,
relation and any nonformalization reason are shown for every registered claim.
Inspect `models/Rules.lean` and its policy correspondence. Generation writes
`models/SourceBound/{Types,Facts,Derived,Claims}.lean`, provenance and renderings.
Only `Rules.lean` is authored. Extra model files reject the profile. Rechecking
regenerates everything in memory and requires byte-for-byte agreement; it does
not repair a tampered file. Explicit `regenerate` replaces generated artifacts.

Run in the agent's reviewed sandbox. The launcher convention is the same as the
existing proof helper: a command prefix followed by the Lean executable, then
`--version` or the generated file. Supply a reviewed no-network Bubblewrap prefix
as one shell-quoted argument. The helper splits it into argv and never invokes a
shell. It does not provision a sandbox or infer permission to execute elsewhere.
The coordinator supplies `LEAN` and `REVIEWED_PREFIX`. Missing sandbox blocks
formal execution. The profile runs only regenerated compilation units, not an
agent-authored theorem file or a historical validation model.

```sh
python3 "$SKILL/scripts/source_bound_audit.py" check --workspace "$WORKSPACE" \
  --lean "$LEAN" --command-prefix "$REVIEWED_PREFIX"
```

This individual check can run before findings and traces are complete. It retains
`source-bound/results.md` plus a distinct `tmp/source-bound-checks/run-*/` with
per-claim baseline/mutant/confirming-negation units, logs, status and module maps.
Exit code 1 means at least one blocked/rejected claim or an operation failure;
exit code 0 can include explicitly not-formalized claims and is not blanket proof
acceptance. Read each status and reason. The aggregate `Claims.lean` is retained
and checked for byte agreement; each actual process contains one selected target,
its exact-target assertion and its axiom inspection. An unrelated false target
cannot poison a different claim's check.

Default budgets are 64 mutants, 10 seconds/process and 120 seconds/theorem.
The shared Lean-version check is additional. Every scheduled independent source
leaf, including policy thresholds, is perturbed. Mutants use resolved values and
dimensions; quotes are never re-extracted or rewritten. A mutation counts only
when applicability holds, the selected target fails and its negation checks.
Timeouts, build failures and incomplete schedules are inconclusive. At least one
confirmed mutation is required overall. No confirmed mutation after a complete
schedule means “not depending on sources under these perturbations”, not a proof
of mathematical independence. Checked transcription is auxiliary even when other
flags overlap. Only accepted proof/auxiliary decisions retain a proof reference.

## Complete the existing claim audit

Register `C1` and `R1` in the top-level `claims.md`, write the complete
`findings/C1.md`, and retain registration, evidence, analysis, verification and
verdict traces using `markdown_audit.py trace` as each step is performed. These
remain separate from `source-bound/claims.md`, which records the formal shape.

Copy the generated target exactly into the finding's `Proofs` block. The module
is `SourceBound.Claims`, the theorem is `SourceBound.Claims.C1`, and the relation
is the selected support/refutation relation or `auxiliary` for a checked
transcription. Use the actual guard decision as the authored formal status.
Blocked/not-attempted findings have no formal proof reference. The checker
rejects both upgrades and downgrades inconsistent with the guards; it never
rewrites a finding. A positive/negative semantic verdict still needs independent
evidence, and argument adequacy remains a separate review.

The individual verification trace must retain the actual command and its
readable compiler/guard logs. Do not fabricate successful execution or describe
an incomplete attempt as checked. Then run, in order:

```sh
python3 "$SKILL/scripts/markdown_audit.py" check --workspace "$WORKSPACE" \
  --lean "$LEAN" --command-prefix "$REVIEWED_PREFIX"
python3 "$SKILL/scripts/markdown_audit.py" render --workspace "$WORKSPACE"
python3 "$SKILL/scripts/markdown_audit.py" inspect --workspace "$WORKSPACE"
```

The final check reruns guards and checks authored statuses. Validation fingerprints
bind source-bound records, all generated models, authored rules, retained proof
attempts, parser/catalogue versions and implementation bytes. Changing any bound
dependency makes delivery stale and requires rechecking. `inspect --machine`
adds `formalization_profile` and per-finding source-bound renderings only for an
opted-in workspace; JSON is stdout transport only. Never persist JSON/JSONL.
Retain the whole workspace for inspection. Formal results establish only the
encoded finite claim, not source truth, causal inference, completeness or prose
fidelity.
