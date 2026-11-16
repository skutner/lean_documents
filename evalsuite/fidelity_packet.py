"""Evaluator-only packet fidelity; generated programs and authored fixtures only.

Only Interface.lean is agent-facing. Unknown certificate dates use representatives
of the generator's four result/age classes, never the hidden dates it sampled.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import datetime as dt
import hashlib
import inspect
import itertools
import json
from pathlib import Path
import re

from evalsuite import generate
from evalsuite.fidelity import (FidelityError, LeanRunner, agreement, parse_vectors,
                               regenerate, run_checked, verdict)

LEAN_DIR = Path(__file__).with_name('fidelity_lean') / 'packet'
VARIANTS = ('ageExclusive', 'ignoresFailedResult', 'ignoresAgeLimit',
            'flipsOutcome', 'wrongPeriod365', 'dayCountMinusOne')
SUPPORTED = {'service_status', 'units_in_service', 'certificate_result'}
UNSUPPORTED = {
    'packet_contents': 'Document membership requires the supplied-file inventory; this date/result interface does not verify it.',
    'unit_location': 'The generator predicate closes over the original unit location and ignores its world; counterfactual fidelity is not defined.',
}
PERIODS = (180, 365, 540)
MAX_RULE_INPUTS = 300
MAX_CLAIM_INPUTS = 5000
PROOF_LIMIT = 300


def capture(fn):
    return inspect.getclosurevars(fn).nonlocals


def string(s):
    return json.dumps(s, ensure_ascii=False)


def boolean(b):
    return 'true' if b else 'false'


def lean_list(xs):
    return '[' + ', '.join(xs) + ']'


def certificate(c):
    return f'⟨.{c["result"].lower()}, {c["tested_day"]}⟩'


def unit(u):
    c = 'none' if u['certificate'] is None else f'(some {certificate(u["certificate"])})'
    return f'⟨{string(u["id"])}, {c}⟩'


def world(w):
    return f'⟨{w["review_day"]}, {w["max_age"]}, {lean_list(unit(u) for u in w["units"])}⟩'


def replace(w, ident, c):
    return dict(w, units=[dict(u, certificate=c) if u['id'] == ident else u for u in w['units']])


def rule_inputs(first):
    """264 inputs: 3 periods × 4 calendar anchors × 11 ages × 2 results."""
    ages = sorted({0, 1, *(p + d for p in PERIODS for d in (-1, 0, 1))})
    # Leap day, following year, year boundary, plus the actual review date.
    days = [dt.date(2032, 2, 29).toordinal(), dt.date(2033, 3, 1).toordinal(),
            dt.date(2033, 1, 1).toordinal(), first['review_day']]
    rows = []
    for limit, day, age, result in itertools.product(PERIODS, days, ages, ('Pass', 'Fail')):
        # Move the entire calendar together: untouched units must remain within
        # the declared non-future-date domain when the review anchor changes.
        shift = day - first['review_day']
        shifted = [dict(u, certificate=dict(u['certificate'],
                   tested_day=u['certificate']['tested_day'] + shift)) for u in first['units']]
        w = dict(first, review_day=day, max_age=limit, units=shifted)
        rows.append((w, {'id': 'unseen', 'certificate': {'result': result, 'tested_day': day - age}}))
    if len(rows) > MAX_RULE_INPUTS:
        raise FidelityError('Packet unseen rule input limit exceeded')
    return rows


@dataclass
class Material:
    directory: Path
    truth: dict
    specs: list[str | None]
    regenerated: object


def emit(key: dict, directory: str | Path) -> Material:
    reg = regenerate(key)
    if key['generator']['family'] != 'packet':
        raise FidelityError('Packet emitter requires a packet key')
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise FidelityError('Output directory must be new or empty')
    directory.mkdir(parents=True, exist_ok=True)
    count_claim = next(c for c in reg.claims if c['kind'] == 'units_in_service')
    closure = capture(capture(count_claim['pred'])['staying'])
    units, python_rule = closure['units'], closure['may_stay']
    # These parameters are in source prose, not retained in the generator's base.
    review_match = re.findall(r'The review date is (\d{4}-\d{2}-\d{2})\.', reg.case['sources']['register.md'])
    limit_match = re.findall(r'test date is at most (\d+) days before the review', reg.case['sources']['method.md'])
    if len(review_match) != 1 or len(limit_match) != 1:
        raise FidelityError('Packet source parameter layout changed')
    review = dt.date.fromisoformat(review_match[0]).toordinal()
    limit = int(limit_match[0])
    if limit not in PERIODS:
        raise FidelityError('Unsupported packet age period')
    base, unknowns = reg.case['base'], reg.case['unknowns']
    sealed = list(generate.worlds(base, unknowns))

    def normalized(w):
        rows = []
        for u in units:
            pair = w.get('certificate:' + u['id'])
            c = None
            if pair is not None:
                result, in_time = pair
                # Supplied dates are known facts. Absent dates stay hidden; choose
                # one representative per Boolean age class, preserving all worlds.
                day = u['tested'].toordinal() if u['supplied'] else review - limit - (not in_time)
                if (review - day <= limit) != in_time or (u['supplied'] and result != u['result']):
                    raise FidelityError('Packet certificate abstraction disagrees with generated facts')
                c = {'result': result, 'tested_day': day}
            rows.append({'id': u['id'], 'certificate': c})
        return {'review_day': review, 'max_age': limit, 'units': rows}

    def python_world(w):
        # Use the original predicates after projecting the raw dates in exactly
        # the way generate.packet computes in_time (calendar-date difference).
        out = dict(base)
        review_date = dt.date.fromordinal(w['review_day'])
        for u in w['units']:
            c = u['certificate']
            if c is None:
                raise FidelityError('Cannot evaluate an uncompleted certificate')
            in_time = (review_date - dt.date.fromordinal(c['tested_day'])).days <= w['max_age']
            out['certificate:' + u['id']] = (c['result'], in_time)
        return out

    recorded = normalized(base)
    completed = [normalized(w) for w in sealed]
    first = completed[0]
    alternatives = []
    for name, pairs in sorted(unknowns.items()):
        if pairs != generate.OPEN_CERTIFICATE:
            raise FidelityError('Unknown certificate domain changed')
        alternatives.append((name.removeprefix('certificate:'), [
            {'result': r, 'tested_day': review - limit - (not t)} for r, t in pairs]))
    facts = ['namespace Packet.Material', f'def recorded : World := {world(recorded)}',
             'def dossier : Dossier := ⟨recorded, ' + lean_list(
                 f'({string(ident)}, {lean_list(certificate(c) for c in choices)})'
                 for ident, choices in alternatives) + '⟩',
             'def sealedWorlds : List World := possibleWorlds dossier',
             # Separate emitter enumeration, checked against Lean Cartesian completion.
             'def expectedWorlds : List World := ' + lean_list(world(w) for w in completed),
             'def first : World := sealedWorlds.head!', 'end Packet.Material', '']
    (directory / 'Facts.lean').write_text('\n'.join(facts))
    rules = rule_inputs(first)
    inputs = ['namespace Packet.Material', 'def unseen : List (World × Unit) := ' + lean_list(
        f'({world(w)}, {unit(u)})' for w, u in rules)]
    specs, truths = [], []
    for i, (row, item) in enumerate(zip(key['expectations'], reg.claims)):
        kind, c = item['kind'], capture(item['pred'])
        if kind not in SUPPORTED | UNSUPPORTED.keys():
            raise FidelityError(f'Unsupported new packet claim kind: {kind}')
        sealed_values = [bool(item['pred'](w)) for w in sealed]
        if (verdict(sealed_values) != row['semantic_state'] or len(sealed_values) != row['derivation']['worlds']
                or sum(sealed_values) != row['derivation']['worlds_where_true']):
            raise FidelityError(f'Python key cross-check failed: {row["claim_id"]}')
        entry = {'id': f'{row["variant"]}/{row["claim_id"]}', 'kind': kind, 'text': item['text'],
                 'supported': kind in SUPPORTED, 'sealed': sealed_values,
                 'expected_verdict': row['semantic_state']}
        if kind in UNSUPPORTED:
            entry.update(reason=UNSUPPORTED[kind], input_count=0, unseen=[])
            specs.append(None)
            truths.append(entry)
            continue
        target = c['u']['id'] if 'u' in c else None
        if kind == 'service_status':
            spec = f'.service {string(target)} {boolean(c["stays"])}'
        elif kind == 'certificate_result':
            spec = f'.result {string(target)} .{c["result"].lower()}'
        else:
            spec = f'.count {c["value"]} {boolean(c["exact"])}'
        targets = [target] if target else [u['id'] for u in units]
        counterfactuals = [replace(w, ident, u['certificate']) for ident in targets for w, u in rules]
        extras = []
        if kind == 'units_in_service':
            for passing in range(len(units) + 1):
                w = deepcopy(first)
                for j, u in enumerate(w['units']):
                    u['certificate'] = {'result': 'Pass' if j < passing else 'Fail', 'tested_day': review}
                extras.append(w)
        all_worlds = completed + counterfactuals + extras
        if len(all_worlds) > MAX_CLAIM_INPUTS:
            raise FidelityError('Packet claim input limit exceeded')
        values = [bool(item['pred'](python_world(w))) for w in all_worlds]
        if values[:len(sealed)] != sealed_values:
            raise FidelityError('Normalized worlds changed the generator predicate')
        expressions = ['sealedWorlds'] + [
            f'unseen.map (fun x => replaceCertificate x.1 {string(ident)} x.2.certificate.get!)'
            for ident in targets] + [lean_list(world(w) for w in extras)]
        inputs.append(f'def claimWorlds{i} : List World := ' + ' ++ '.join(f'({e})' for e in expressions))
        entry.update(spec=spec, input_count=len(values), unseen=values[len(sealed):],
                     input_recipe={'replace_targets_with_rule_inputs': targets, 'extra_worlds': extras})
        specs.append(spec)
        truths.append(entry)
    inputs += ['end Packet.Material', '']
    (directory / 'Inputs.lean').write_text('\n'.join(inputs))
    expected = []
    for w, u in rules:
        changed = dict(w, units=[u])
        expected.append(bool(python_rule(python_world(changed), u)))
    supported = sum(c['supported'] for c in truths)
    truth = {'schema_version': 1, 'classification': 'evaluator_only', 'dossier_id': key['dossier_id'],
             'generator': reg.provenance, 'reference_sha256': hashlib.sha256(
                 (LEAN_DIR / 'Reference.lean').read_bytes()).hexdigest(),
             'recorded': recorded, 'unknowns': alternatives, 'sealed_worlds': completed,
             'generator_worlds': sealed,
             'rule_inputs': [{'world': w, 'unit': u} for w, u in rules], 'rule_expected': expected,
             'unseen_count': len(rules), 'bounds': {'rule': MAX_RULE_INPUTS, 'claim': MAX_CLAIM_INPUTS},
             'claims': truths, 'claim_coverage': {'graded': supported, 'total': len(truths), 'rate': supported / len(truths)},
             'limits': ['Bounded measurement, not universal equivalence or document extraction verification.',
                        'Absent certificate dates are representatives of the generator age classes, not known dates.',
                        'Unsupported claims never receive fidelity credit; key agreement is scoped to graded kinds.',
                        'Future test dates are outside the generated domain; no lower-age rule is invented.']}
    (directory / 'truth.json').write_text(json.dumps(truth, indent=2) + '\n')
    (directory / 'key.json').write_text(json.dumps(key, indent=2) + '\n')
    return Material(directory, truth, specs, reg)


def program(material, *, reference=True):
    parts = [(LEAN_DIR / 'Interface.lean').read_text()]
    if reference:
        parts.append((LEAN_DIR / 'Reference.lean').read_text())
    parts += [(material.directory / name).read_text() for name in ('Facts.lean', 'Inputs.lean')]
    parts += ['set_option maxRecDepth 1000000', 'set_option maxHeartbeats 0',
              'open Packet Packet.Material',
              'def emitBits (tag : String) (xs : List Bool) : String :=',
              '  "FIDELITY|" ++ tag ++ "|" ++ String.mk (xs.map (fun b => if b then \'1\' else \'0\'))']
    return '\n\n'.join(parts) + '\n'


def evaluate(material, runner, candidate=None):
    source = program(material, reference=candidate is None)
    lengths = {'worlds': 1}
    source += '#eval emitBits "worlds" [sealedWorlds == expectedWorlds]\n'
    rules = ['Reference.meets', *('Reference.' + v for v in VARIANTS)]
    if candidate is not None:
        source += candidate.read_text() + '\n'
        rules = ['Candidate.model.rule']
    for j, rule in enumerate(rules):
        tag = f'rule{j}'
        source += f'#eval emitBits "{tag}" (unseen.map (fun x => {rule} x.1 x.2))\n'
        lengths[tag] = material.truth['unseen_count']
    for i, c in enumerate(material.truth['claims']):
        if not c['supported']:
            continue
        test = f'Reference.test Reference.meets ({material.specs[i]})' if candidate is None else f'Candidate.model.claim {string(c["id"])}'
        source += f'#eval emitBits "claim{i}" (claimWorlds{i}.map ({test}))\n'
        lengths[f'claim{i}'] = c['input_count']
    path = material.directory / ('ReferenceCheck.lean' if candidate is None else 'CandidateCheck.lean')
    if path.exists():
        raise FidelityError('Use a distinct run directory for each reference/candidate execution')
    path.write_text(source)
    result = run_checked(path, runner)
    actual = parse_vectors(result.stdout, lengths)
    if actual['worlds'] != [True]:
        raise FidelityError('Lean possible worlds differ from Python enumeration')
    return actual, result


def validate(material: Material, runner: LeanRunner) -> dict:
    actual, execution = evaluate(material, runner)
    expected = material.truth['rule_expected']
    score = agreement(actual['rule0'], expected)
    if score['rate'] != 1:
        raise FidelityError('Lean reference disagrees with Python generator rule')
    for i, c in enumerate(material.truth['claims']):
        if not c['supported']:
            continue
        values = actual[f'claim{i}']
        if values != c['sealed'] + c['unseen']:
            raise FidelityError(f'Lean reference disagrees with generator predicate: {c["id"]}')
        sealed = values[:len(c['sealed'])]
        row = material.regenerated.key['expectations'][i]
        if (verdict(sealed) != row['semantic_state'] or len(sealed) != row['derivation']['worlds']
                or sum(sealed) != row['derivation']['worlds_where_true']):
            raise FidelityError(f'Key cross-check failed: {c["id"]}')
    witnesses = {v: [i for i, (a, e) in enumerate(zip(actual[f'rule{j}'], expected)) if a != e]
                 for j, v in enumerate(VARIANTS, 1)}
    missed = [v for v, indices in witnesses.items() if not indices]
    report = {'key_cross_check': True, 'key_cross_check_scope': sorted(SUPPORTED),
              'reference_agreement': score, 'claim_coverage': material.truth['claim_coverage'],
              'unsupported_claims': [{'id': c['id'], 'reason': c['reason']} for c in material.truth['claims'] if not c['supported']],
              'test_strength': {'detected': len(VARIANTS) - len(missed), 'total': len(VARIANTS),
                                'rate': (len(VARIANTS) - len(missed)) / len(VARIANTS),
                                'undetected': missed, 'witness_indices': witnesses}, 'seconds': execution.seconds}
    (material.directory / 'reference-result.json').write_text(json.dumps(report, indent=2) + '\n')
    if missed:
        raise FidelityError(f'Unseen set is too weak; undetected variants: {missed}')
    material._validated = True
    return report


def grade(material: Material, candidate: str | Path, runner: LeanRunner, *, prove=False,
          proof_limit=PROOF_LIMIT) -> dict:
    if not getattr(material, '_validated', False):
        validate(material, runner)
    candidate = Path(candidate)
    actual, execution = evaluate(material, runner, candidate)
    claims = []
    for i, c in enumerate(material.truth['claims']):
        if not c['supported']:
            claims.append({'id': c['id'], 'status': 'unsupported', 'reason': c['reason'], 'agrees_everywhere': False})
            continue
        score = agreement(actual[f'claim{i}'], c['sealed'] + c['unseen'])
        claims.append({'id': c['id'], 'status': 'graded', 'agrees_everywhere': score['rate'] == 1, **score})
    correct = sum(c['agrees_everywhere'] for c in claims)
    graded = sum(c['supported'] for c in material.truth['claims'])
    report = {'rule_fidelity': agreement(actual['rule0'], material.truth['rule_expected']),
              'claim_fidelity': {'matched': correct, 'total': len(claims), 'rate': correct / len(claims)},
              'graded_claim_fidelity': {'matched': correct, 'total': graded, 'rate': correct / graded},
              'claim_coverage': material.truth['claim_coverage'], 'claims': claims, 'seconds': execution.seconds,
              'candidate_sha256': hashlib.sha256(candidate.read_bytes()).hexdigest(),
              'proof': {'status': 'not_requested'}}
    if prove:
        if material.truth['unseen_count'] > proof_limit:
            report['proof'] = {'status': 'skipped', 'reason': 'bounded set exceeds proof_limit'}
        elif report['rule_fidelity']['rate'] != 1:
            report['proof'] = {'status': 'not_equal', 'reason': 'rule counterexample observed'}
        else:
            source = program(material) + candidate.read_text() + '\n'
            source += ('theorem candidate_rule_agrees :\n'
                       '  unseen.all (fun x => Candidate.model.rule x.1 x.2 == Reference.meets x.1 x.2) = true := by decide\n'
                       '#print axioms candidate_rule_agrees\n')
            path = material.directory / 'Agreement.lean'
            path.write_text(source)
            result = run_checked(path, runner)
            marker = "'candidate_rule_agrees' does not depend on any axioms"
            if marker not in result.stdout:
                raise FidelityError('Agreement proof missing or depends on axioms; inspect proof output')
            report['proof'] = {'status': 'kernel_checked', 'scope': 'rule on bounded unseen set',
                               'axiom_report': marker, 'seconds': result.seconds}
    (material.directory / 'grade.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def fixture(material: Material, *, slip: str | None = None, wrong_item=False, constant=False) -> Path:
    if slip is not None and slip not in VARIANTS:
        raise FidelityError(f'Unknown fixture slip: {slip}')
    source = (LEAN_DIR / 'fixtures' / 'Faithful.lean').read_text()
    if slip:
        source += (LEAN_DIR / 'fixtures' / 'Slips.lean').read_text()
    source += ('\nnamespace Candidate\nopen Packet\ndef claims (id : String) '
               f'({"_w" if constant else "w"} : World) : Bool :=\n  match id with\n')
    for row, item in zip(material.truth['claims'], material.regenerated.claims):
        if not row['supported']:
            continue
        c = capture(item['pred'])
        ident = c['u']['id'] if 'u' in c else None
        if wrong_item and ident:
            ident = next(u['id'] for u in material.truth['recorded']['units'] if u['id'] != ident)
        get = f'(w.units.find? (fun u => u.id == {string(ident)})).any'
        rule = slip or 'meets'
        if constant:
            body = boolean(row['sealed'][0])
        elif item['kind'] == 'service_status':
            body = f'{get} (fun u => {rule} w u == {boolean(c["stays"])})'
        elif item['kind'] == 'certificate_result':
            body = f'{get} (fun u => u.certificate.any (fun cert => cert.result == .{c["result"].lower()}))'
        else:
            op = '==' if c['exact'] else '>='
            body = f'(w.units.filter ({rule} w)).length {op} {c["value"]}'
        source += f'  | {string(row["id"])} => {body}\n'
    source += f'  | _ => false\ndef model : Model := ⟨{slip or "meets"}, claims⟩\nend Candidate\n'
    path = material.directory / 'Fixture.lean'
    path.write_text(source)
    return path
