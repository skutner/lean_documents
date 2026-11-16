"""Approvals reference material, differential checks, and bounded candidate grading.

Only Interface.lean is agent-facing. Everything emitted here is evaluator-only.
No existing suite files are edited; callers explicitly provide a new output folder
and an execution function (sandboxed for future agent submissions).
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import inspect
import itertools
import json
from pathlib import Path

from evalsuite import generate
from evalsuite.fidelity import (FidelityError, LeanRunner, agreement, parse_vectors,
                               regenerate, run_checked, verdict)

LEAN_DIR = Path(__file__).with_name('fidelity_lean') / 'approvals'
VARIANTS = ('boundaryInclusive', 'requesterCounts', 'financeDropped', 'distinctDropped')
ROLES = {'Finance officer': 'finance', 'Manager': 'manager', 'Director': 'director', 'Analyst': 'analyst'}
MAX_RULE_INPUTS = 2000
MAX_CLAIM_INPUTS = 12000
PROOF_LIMIT = 2000


def string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def boolean(value: bool) -> str:
    return 'true' if value else 'false'


def lean_list(values) -> str:
    return '[' + ', '.join(values) + ']'


def capture(item: dict) -> dict:
    """Version-pinned adapter to generator closures, never inferred from verdicts."""
    return inspect.getclosurevars(item['pred']).nonlocals


def claim_spec(item: dict, people: list[str]) -> tuple[str, str | None]:
    c, kind = capture(item), item['kind']
    target = c['r']['id'] if 'r' in c else None
    q = string(target) if target else ''
    if kind == 'policy_compliance':
        spec = f'.compliance {q} {boolean(c["met"])}'
    elif kind == 'approval_tier':
        spec = f'.tier {q} {boolean(c["two"])}'
    elif kind == 'policy_failures':
        spec = f'.failures {c["value"]} {boolean(c["exact"])}'
    elif kind == 'signer_role':
        spec = f'.signerRole {people.index(c["person"])} .{ROLES[c["role"]]}'
    elif kind == 'illegible_signature':
        spec = f'.illegible {q}'
    elif kind == 'request_lookup':
        spec = f'.lookup {q} {people.index(c["requester"])} {c["amount"]}'
    else:
        raise FidelityError(f'Unsupported approvals claim: {kind}')
    return spec, target


def rule_inputs(world: dict) -> list[tuple[dict, dict]]:
    """1197 full boundary/requester/signature cases + 84 targeted role changes.

    Seven people, three amounts, all ordered signature lists of length 0..2.
    Additionally each person's role takes all four values, at all three amounts,
    with a second signer and a distinct requester. No random sampling.
    """
    people, limit = sorted(world['roles']), world['limit']
    signatures = [[]] + [[p] for p in people] + [list(pair) for pair in itertools.product(people, repeat=2)]
    result = []
    for requester, amount, signers in itertools.product(people, [limit - 1, limit, limit + 1], signatures):
        result.append((world, {'id': 'unseen', 'requester': requester, 'amount': amount, 'signers': signers}))
    for person, role, amount in itertools.product(people, ROLES, [limit - 1, limit, limit + 1]):
        others = [p for p in people if p != person]
        w = dict(world, roles=dict(world['roles'], **{person: role}))
        result.append((w, {'id': 'unseen', 'requester': others[0], 'amount': amount,
                           'signers': [person, others[1]]}))
    if len(result) > MAX_RULE_INPUTS:
        raise FidelityError('Approvals unseen rule input limit exceeded')
    return result


@dataclass
class Material:
    directory: Path
    truth: dict
    specs: list[str]
    regenerated: object


def emit(key: dict, directory: str | Path) -> Material:
    """Regenerate, emit facts/worlds/inputs/truth, retaining all input order in JSON."""
    reg = regenerate(key)
    if key['generator']['family'] != 'approvals':
        raise FidelityError('Approvals emitter requires an approvals key')
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise FidelityError('Output directory must be new or empty')
    directory.mkdir(parents=True, exist_ok=True)
    base, unknowns = reg.case['base'], reg.case['unknowns']
    people = sorted(base['roles'])
    sealed = list(generate.worlds(base, unknowns))
    first = sealed[0]
    rules = rule_inputs(first)
    policy = next(c for c in reg.claims if c['kind'] == 'policy_compliance')
    python_rule = capture(policy)['meets']

    def request(r):
        signs = [('.missing' if s is None else '.illegible' if s == 'illegible'
                  else f'(.named {people.index(s)})') for s in r['signers']]
        return f'⟨{string(r["id"])}, {people.index(r["requester"])}, {r["amount"]}, {lean_list(signs)}⟩'

    def roles(w):
        return lean_list('.' + ROLES[w['roles'][p]] for p in people)

    def world(w):
        resolutions = lean_list(f'({string(name[7:])}, {people.index(w[name])})'
                                for name in sorted(w) if name.startswith('signer:'))
        return (f'⟨{lean_list(map(string, people))}, {roles(w)}, {w["limit"]}, '
                f'{lean_list(request(r) for r in w["requests"])}, {resolutions}⟩')

    facts = '\n'.join([
        'namespace Approvals.Material',
        f'def recorded : World := {world(base)}',
        'def dossier : Dossier := ⟨recorded, ' + lean_list(
            f'({string(k[7:])}, {lean_list(str(people.index(p)) for p in v)})'
            for k, v in sorted(unknowns.items())) + '⟩',
        'def sealedWorlds : List World := ' + lean_list(world(w) for w in sealed),
        'def first : World := sealedWorlds.head!',
        'end Approvals.Material', ''])
    (directory / 'Facts.lean').write_text(facts)
    inputs = ['namespace Approvals.Material',
              'def personIds : List Nat := List.range first.people.length',
              'def amounts : List Nat := [first.limit - 1, first.limit, first.limit + 1]',
              'def signatureLists : List (List Signature) := [[]] ++ personIds.map (fun p => [.named p]) ++',
              '  personIds.flatMap (fun a => personIds.map (fun b => [.named a, .named b]))',
              'def unseen : List (World × Request) :=',
              '  (personIds.flatMap (fun p => amounts.flatMap (fun a => signatureLists.map',
              '    (fun ss => (first, ⟨"unseen", p, a, ss⟩))))) ++',
              '  (personIds.flatMap (fun p => ([Role.finance, .manager, .director, .analyst]).flatMap (fun role =>',
              '    amounts.map (fun a =>',
              '      let others := personIds.filter (fun q => q != p)',
              '      ({ first with roles := first.roles.zipIdx.map (fun x => if x.2 == p then role else x.1) },',
              '       ⟨"unseen", others[0]!, a, [.named p, .named others[1]!]⟩)))))',
              'def replace (w : World) (id : String) (r : Request) : World :=',
              '  { w with requests := w.requests.map (fun old => if old.id == id then { r with id := id } else old) }']
    truths, specs = [], []
    for i, (row, item) in enumerate(zip(key['expectations'], reg.claims)):
        spec, target = claim_spec(item, people)
        specs.append(spec)
        extra = []
        expressions = ['sealedWorlds']
        applicable = []
        if target and item['kind'] != 'illegible_signature':
            targets = [target]
        elif item['kind'] == 'policy_failures':
            targets = [r['id'] for r in base['requests']]
        else:
            targets = []
        for ident in targets:
            expressions.append(f'unseen.map (fun x => replace x.1 {string(ident)} x.2)')
            for w, r in rules:
                applicable.append(dict(w, requests=[dict(r, id=ident) if old['id'] == ident else old
                                                   for old in w['requests']]))
        # All role changes also apply to source-role claims and global counts.
        for person, role in itertools.product(people, ROLES):
            extra.append(dict(first, roles=dict(first['roles'], **{person: role})))
        if item['kind'] == 'request_lookup':
            c = capture(item)
            for requester, amount in itertools.product(
                    people, sorted({max(0, c['amount'] - 1), c['amount'], c['amount'] + 1})):
                w = deepcopy(first)
                r = next(r for r in w['requests'] if r['id'] == target)
                r.update(requester=requester, amount=amount)
                extra.append(w)
        if item['kind'] == 'illegible_signature':
            for signer in [None, people[0], 'illegible']:
                w = deepcopy(first)
                r = next(r for r in w['requests'] if r['id'] == target)
                r['signers'][1] = signer
                if signer == 'illegible':
                    w['signer:' + target] = people[0]
                extra.append(w)
        if item['kind'] == 'policy_failures':
            # Reach every possible count, so a constant lower-bound/exact test is
            # detected whenever its truth can vary on well-formed request sets.
            for passing in range(len(first['requests']) + 1):
                w = deepcopy(first)
                for j, r in enumerate(w['requests']):
                    r['amount'] = w['limit']
                    r['signers'] = ([next(p for p in people if p != r['requester']
                                         and w['roles'][p] != 'Analyst')] if j < passing else [])
                extra.append(w)
        expressions.append(lean_list(world(w) for w in extra))
        all_worlds = sealed + applicable + extra
        if len(all_worlds) > MAX_CLAIM_INPUTS:
            raise FidelityError('Approvals claim input limit exceeded')
        inputs.append(f'def claimWorlds{i} : List World := ' + ' ++ '.join(f'({e})' for e in expressions))
        values = [bool(item['pred'](w)) for w in all_worlds]
        truths.append({'id': f'{row["variant"]}/{row["claim_id"]}', 'kind': item['kind'],
                       'text': item['text'], 'spec': spec, 'sealed': values[:len(sealed)],
                       'unseen': values[len(sealed):], 'input_count': len(all_worlds),
                       'input_recipe': {'replace_targets_with_rule_inputs': targets,
                                        'extra_worlds': extra},
                       'expected_verdict': row['semantic_state']})
    inputs += ['end Approvals.Material', '']
    (directory / 'Inputs.lean').write_text('\n'.join(inputs))
    truth = {'schema_version': 1, 'classification': 'evaluator_only', 'dossier_id': key['dossier_id'],
             'generator': reg.provenance, 'reference_sha256': hashlib.sha256(
                 (LEAN_DIR / 'Reference.lean').read_bytes()).hexdigest(),
             'recorded': base, 'unknowns': unknowns, 'sealed_worlds': sealed,
             'rule_inputs': [{'roles': w['roles'], 'limit': w['limit'], 'request': r} for w, r in rules],
             'rule_expected': [bool(python_rule(w, r)) for w, r in rules],
             'unseen_count': len(rules), 'bounds': {'rule': MAX_RULE_INPUTS, 'claim': MAX_CLAIM_INPUTS},
             'claims': truths,
             'limits': ['Bounded differential measurement, not universal equivalence.',
                        'signer_role matches the generator predicate: role only; the signing presupposition is not tested.',
                        'illegible_signature tests the recorded scan marker, not the resolved identity.',
                        'Changing facts tests predicates; it is not an assertion that original source documents changed.']}
    (directory / 'truth.json').write_text(json.dumps(truth, indent=2, ensure_ascii=False) + '\n')
    (directory / 'key.json').write_text(json.dumps(key, indent=2) + '\n')
    return Material(directory, truth, specs, reg)


def program(material: Material, *, reference: bool = True) -> str:
    parts = [(LEAN_DIR / 'Interface.lean').read_text()]
    if reference:
        parts.append((LEAN_DIR / 'Reference.lean').read_text())
    parts += [(material.directory / name).read_text() for name in ('Facts.lean', 'Inputs.lean')]
    parts += ['set_option maxRecDepth 1000000', 'set_option maxHeartbeats 0',
              'open Approvals Approvals.Material',
              'def emitBits (tag : String) (xs : List Bool) : String :=',
              '  "FIDELITY|" ++ tag ++ "|" ++ String.mk (xs.map (fun b => if b then \'1\' else \'0\'))']
    return '\n\n'.join(parts) + '\n'


def evaluate(material: Material, runner: LeanRunner, candidate: Path | None = None) -> tuple[dict, object]:
    # Candidate scoring is a separate program that contains no reference engine.
    source = program(material, reference=candidate is None)
    lengths = {}
    rules = ['Reference.meets', *['Reference.' + v for v in VARIANTS]] if candidate is None else ['Candidate.model.rule']
    if candidate is not None:
        source += '\n' + candidate.read_text() + '\n'
    for j, rule in enumerate(rules):
        tag = f'rule{j}'
        source += f'#eval emitBits "{tag}" (unseen.map (fun x => {rule} x.1 x.2))\n'
        lengths[tag] = material.truth['unseen_count']
    for i, claim in enumerate(material.truth['claims']):
        test = (f'Reference.test Reference.meets ({material.specs[i]})' if candidate is None
                else f'Candidate.model.claim {string(claim["id"])}')
        source += f'#eval emitBits "claim{i}" (claimWorlds{i}.map ({test}))\n'
        lengths[f'claim{i}'] = claim['input_count']
    path = material.directory / ('ReferenceCheck.lean' if candidate is None else 'CandidateCheck.lean')
    if path.exists():
        raise FidelityError('Use a distinct run directory for each reference/candidate execution')
    path.write_text(source)
    result = run_checked(path, runner)
    return parse_vectors(result.stdout, lengths), result


def check(material: Material, runner: LeanRunner) -> dict:
    """Require agreement on individual truth values, key verdicts AND key counts."""
    actual, result = evaluate(material, runner)
    expected = material.truth['rule_expected']
    rule = agreement(actual['rule0'], expected)
    if rule['rate'] != 1:
        raise FidelityError('Lean reference disagrees with Python generator rule')
    for i, c in enumerate(material.truth['claims']):
        values = actual[f'claim{i}']
        if values != c['sealed'] + c['unseen']:
            raise FidelityError(f'Lean reference disagrees with generator predicate: {c["id"]}')
        sealed = values[:len(c['sealed'])]
        row = material.regenerated.key['expectations'][i]
        if (verdict(sealed) != row['semantic_state'] or
                len(sealed) != row['derivation']['worlds'] or
                sum(sealed) != row['derivation']['worlds_where_true']):
            raise FidelityError(f'Key cross-check failed: {c["id"]}')
    witnesses = {v: [i for i, (a, e) in enumerate(zip(actual[f'rule{j}'], expected)) if a != e]
                 for j, v in enumerate(VARIANTS, 1)}
    missed = [v for v, indices in witnesses.items() if not indices]
    report = {'key_cross_check': True, 'reference_agreement': rule,
              'test_strength': {'detected': len(VARIANTS) - len(missed), 'total': len(VARIANTS),
                                'rate': (len(VARIANTS) - len(missed)) / len(VARIANTS),
                                'undetected': missed, 'witness_indices': witnesses},
              'seconds': result.seconds}
    (material.directory / 'reference-result.json').write_text(json.dumps(report, indent=2) + '\n')
    if missed:
        raise FidelityError(f'Unseen set is too weak; undetected variants: {missed}')
    return report


def grade(material: Material, candidate: str | Path, runner: LeanRunner, *, prove: bool = False,
          proof_limit: int = PROOF_LIMIT) -> dict:
    """Grade a complete Candidate.model. The runner MUST isolate real submissions.

    Reference validation is executed here if absent; a caller cannot trust an old
    JSON success flag. Each material supports exactly one candidate evaluation.
    """
    candidate = Path(candidate)
    # Validate each material once in this process; results are not loaded from disk.
    if not getattr(material, '_validated', False):
        if (material.directory / 'ReferenceCheck.lean').exists():
            raise FidelityError('Use validate() or fresh material before grading')
        validate(material, runner)
    actual, execution = evaluate(material, runner, candidate)
    per_claim = []
    for i, c in enumerate(material.truth['claims']):
        score = agreement(actual[f'claim{i}'], c['sealed'] + c['unseen'])
        per_claim.append({'id': c['id'], 'agrees_everywhere': score['rate'] == 1, **score})
    correct = sum(c['agrees_everywhere'] for c in per_claim)
    report = {'rule_fidelity': agreement(actual['rule0'], material.truth['rule_expected']),
              'claim_fidelity': {'matched': correct, 'total': len(per_claim), 'rate': correct / len(per_claim)},
              'claims': per_claim, 'seconds': execution.seconds,
              'candidate_sha256': hashlib.sha256(candidate.read_bytes()).hexdigest(),
              'proof': {'status': 'not_requested'}}
    if prove:
        if material.truth['unseen_count'] > proof_limit:
            report['proof'] = {'status': 'skipped', 'reason': 'bounded set exceeds proof_limit'}
        elif report['rule_fidelity']['rate'] != 1:
            report['proof'] = {'status': 'not_equal', 'reason': 'rule counterexample observed'}
        else:
            source = program(material) + '\n' + candidate.read_text() + '\n'
            source += ('theorem candidate_rule_agrees :\n'
                       '  unseen.all (fun x => Candidate.model.rule x.1 x.2 == Reference.meets x.1 x.2) = true := by decide\n'
                       '#print axioms candidate_rule_agrees\n')
            proof = material.directory / 'Agreement.lean'
            proof.write_text(source)
            result = run_checked(proof, runner)
            marker = "'candidate_rule_agrees' does not depend on any axioms"
            if marker not in result.stdout:
                raise FidelityError('Agreement proof missing or depends on axioms; inspect proof output')
            report['proof'] = {'status': 'kernel_checked', 'scope': 'rule on bounded unseen set',
                               'axiom_report': marker, 'seconds': result.seconds}
    (material.directory / 'grade.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def validate(material: Material, runner: LeanRunner) -> dict:
    report = check(material, runner)
    material._validated = True
    return report


def fixture(material: Material, *, slip: str | None = None, wrong_request: bool = False,
            constant: bool = False, omit_requester: bool = False) -> Path:
    """Write only locally authored test candidates, never read retained agent files.

    Faithful rule preserves the probe's existential pair computation. Fixture claim
    dispatch translates closure arguments independently from Reference.test.
    """
    if slip is not None and slip not in VARIANTS:
        raise FidelityError(f'Unknown fixture slip: {slip}')
    source = (LEAN_DIR / 'fixtures' / 'Faithful.lean').read_text()
    if slip:
        # Separate fixture implementations; not aliases of the reference variants.
        source += (LEAN_DIR / 'fixtures' / 'Slips.lean').read_text()
    world_parameter = '_w' if constant else 'w'
    source += ('\nnamespace Candidate\nopen Approvals\ndef claims (id : String) '
               f'({world_parameter} : World) : Bool :=\n  match id with\n')
    people = sorted(material.regenerated.case['base']['roles'])
    for row, item in zip(material.truth['claims'], material.regenerated.claims):
        c, kind = capture(item), item['kind']
        ident = c['r']['id'] if 'r' in c else None
        if wrong_request and ident:
            ident = next(r['id'] for r in material.regenerated.case['base']['requests'] if r['id'] != ident)
        get = f'(w.requests.find? (fun r => r.id == {string(ident)})).any'
        rule = 'meets' if not slip else slip
        if constant:
            body = boolean(row['sealed'][0])
        elif kind == 'policy_compliance':
            body = f'{get} (fun r => {rule} w r == {boolean(c["met"])})'
        elif kind == 'approval_tier':
            body = f'{get} (fun r => decide (r.amount > w.limit) == {boolean(c["two"])})'
        elif kind == 'policy_failures':
            op = '==' if c['exact'] else '>='
            body = f'(w.requests.filter (fun r => !{rule} w r)).length {op} {c["value"]}'
        elif kind == 'signer_role':
            body = f'w.roles[{people.index(c["person"])}]! == .{ROLES[c["role"]]}'
        elif kind == 'illegible_signature':
            body = f'{get} (fun r => r.signatures[1]? == some .illegible)'
        elif kind == 'request_lookup':
            body = (f'{get} (fun r => r.amount == {c["amount"]})' if omit_requester else
                    f'{get} (fun r => r.requester == {people.index(c["requester"])} && r.amount == {c["amount"]})')
        else:
            raise FidelityError(f'Unsupported fixture kind: {kind}')
        source += f'  | {string(row["id"])} => {body}\n'
    source += f'  | _ => false\ndef model : Model := ⟨{slip or "meets"}, claims⟩\nend Candidate\n'
    path = material.directory / 'Fixture.lean'
    path.write_text(source)
    return path
