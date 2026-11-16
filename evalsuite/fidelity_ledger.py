"""Evaluator-only ledger reference emission, validation, and bounded fidelity grading.

Only Interface.lean is agent-facing. Inject a sandboxed runner for real submissions;
the direct shared runner is permitted only for generated programs/authored fixtures.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import datetime
import hashlib
import inspect
import itertools
import json
from pathlib import Path

from evalsuite import generate
from evalsuite.fidelity import (FidelityError, LeanRunner, agreement, parse_vectors,
                               regenerate, run_checked, verdict)

LEAN_DIR = Path(__file__).with_name('fidelity_lean') / 'ledger'
VARIANTS = ('includesVoided', 'supersededAmount', 'unknownAsAbsent', 'unknownAsPresent',
            'unsampledIgnored', 'extrapolatesSampleRate', 'reversedDirection',
            'arithmeticSlip', 'missingSheetAsPresent', 'wrongEntry')
SUPPORTED = {'sample_total', 'budget_gap', 'largest_payment', 'sample_missing_approvals',
             'sample_missing_approval_amount', 'recorded_approvals', 'population_missing_approvals'}
UNSUPPORTED = {
    'payment_lookup': 'Generator predicate captures the original entry and ignores the world; '
                      'world-sensitive lookup fidelity cannot be measured against that predicate.',
    'annex_scope': 'Document inclusion assertion, outside the operational ledger facts interface.',
    'annex_content': 'Assertion about annex text, not an operational ledger rule or fact test.',
}
MAX_RULE_INPUTS = 1000
MAX_CLAIM_INPUTS = 2000
PROOF_LIMIT = 1000
APPROVALS = {'yes': 'present', 'no': 'absent', 'unknown': 'unknown'}


def string(value):
    return json.dumps(value, ensure_ascii=False)


def boolean(value):
    return 'true' if value else 'false'


def lean_list(values):
    return '[' + ', '.join(values) + ']'


def integer(value):
    return f'({value})' if value < 0 else str(value)


def capture(item):
    return inspect.getclosurevars(item['pred']).nonlocals


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False,
                               default=lambda v: v.isoformat()) + '\n', encoding='utf-8')


def entry_expr(e):
    return (f'⟨{string(e["id"])}, {string(e["payee"])}, {e["date"].toordinal()}, '
            f'{e["ledger_amount"]}, {e["amount"]}, .{APPROVALS[e["approval"]]}, {boolean(e["voided"])}⟩')


def world_expr(w):
    resolutions = lean_list(f'({string(k[9:])}, {boolean(v)})'
                            for k, v in sorted(w.items()) if k.startswith('approval:'))
    return (f'⟨{lean_list(entry_expr(e) for e in w["entries"])}, {integer(w["budget"])}, '
            f'{w["population"]}, {resolutions}, {w.get("unsampled_missing", 0)}⟩')


def claim_spec(item):
    c, kind = capture(item), item['kind']
    if kind in UNSUPPORTED:
        return None
    if kind == 'sample_total':
        return f'.total {integer(c["value"])}'
    if kind == 'budget_gap':
        return f'.gap {integer(c["value"])} {boolean(c["over"])}'
    if kind == 'largest_payment':
        return f'.largest {string(c["entry"]["id"])} {c["amount"]}'
    names = {'sample_missing_approvals': 'missingCount',
             'sample_missing_approval_amount': 'missingAmount',
             'recorded_approvals': 'recorded', 'population_missing_approvals': 'population'}
    if kind not in names:
        raise FidelityError(f'Unsupported ledger claim kind: {kind}')
    return f'.{names[kind]} {integer(c["value"])}' + (
        '' if kind == 'recorded_approvals' else ' ' + boolean(c['exact']))


def oracle(reg):
    """Reuse original closures/quantities, not a second Python rule implementation."""
    originals = {good['kind']: good for good, _ in reg.case['slots']}
    total = originals['sample_total']['quantity']
    live = capture(originals['largest_payment'])['live']

    def evaluate(w):
        biggest = max(live(w), key=lambda e: e['amount'])
        return {'total': total(w), 'gap': total(w) - w['budget'],
                'largest': (biggest['id'], biggest['amount']),
                'missing_count': originals['sample_missing_approvals']['quantity'](w),
                'missing_amount': originals['sample_missing_approval_amount']['quantity'](w),
                'recorded_count': originals['recorded_approvals']['quantity'](w),
                'population_missing': originals['population_missing_approvals']['quantity'](w)}
    return evaluate


def small_world(a=1, b=2, void=False, sa=0, sb=0, unsampled=0, capacity=2, delta=0):
    # Status 0..3: present, absent, unknown resolved present, unknown resolved absent.
    es = []
    w = {'entries': es, 'budget': max(0, a + (0 if void else b) + delta),
         'population': (1 if void else 2) + capacity, 'unsampled_missing': unsampled}
    for ident, amount, status, voided in [('unseen-a', a, sa, False), ('unseen-b', b, sb, void)]:
        es.append({'id': ident, 'payee': ident, 'date': datetime.date(2031, 1, 1),
                   'ledger_amount': 2 - amount, 'amount': amount,
                   'approval': ('yes', 'no', 'unknown', 'unknown')[status], 'voided': voided})
        if status >= 2:
            w['approval:' + ident] = status == 2
    return w


def rule_inputs():
    # 3² amounts × 2 void markers × 4² approval states × 3 gap/count positions.
    result = [small_world(a, b, void, sa, sb, n, 2, n - 1)
              for a, b, void, sa, sb, n in itertools.product(
                  range(3), range(3), (False, True), range(4), range(4), range(3))]
    # Complete/incomplete population and all permitted unsampled counts; includes
    # half-integer extrapolations (population 3, two sampled, one recorded absent).
    result += [small_world(sa=1, sb=0, capacity=cap, unsampled=n)
               for cap in range(3) for n in range(cap + 1)]
    if len(result) > MAX_RULE_INPUTS:
        raise FidelityError('Ledger unseen rule input limit exceeded')
    return result


def rename(w, target, other):
    w = deepcopy(w)
    mapping = {'unseen-a': target, 'unseen-b': other}
    for e in w['entries']:
        e['id'] = mapping[e['id']]
    for old, new in mapping.items():
        if 'approval:' + old in w:
            w['approval:' + new] = w.pop('approval:' + old)
    return w


def boundary_worlds(item, first):
    """Place the stated value and both neighbors in each claim's measured quantity."""
    c, kind = capture(item), item['kind']
    target = c['entry']['id'] if kind == 'largest_payment' else first['entries'][0]['id']
    other = next(e['id'] for e in first['entries'] if e['id'] != target)
    value = c['amount'] if kind == 'largest_payment' else c['value']
    result = []
    for n in sorted({max(0, value - 1), max(0, value), max(0, value + 1)}):
        w = rename(small_world(void=True, capacity=0), target, other)
        e = w['entries'][0]
        if kind in ('sample_total', 'sample_missing_approval_amount', 'largest_payment'):
            e.update(amount=n, ledger_amount=n, approval='no')
        elif kind == 'budget_gap':
            e.update(amount=n + 1, ledger_amount=n + 1)
            w['budget'] = 1 if c['over'] else 2 * n + 1
        elif kind in ('sample_missing_approvals', 'recorded_approvals', 'population_missing_approvals'):
            # At least one nonvoid row for the max operation, even at zero.
            w['entries'] = [dict(e, id=f'count-{j}', approval=(
                'yes' if kind == 'recorded_approvals' else 'no')) for j in range(n)]
            if n == 0:
                w['entries'] = [dict(e, approval='no' if kind == 'recorded_approvals' else 'yes')]
        w['population'] = sum(not row['voided'] for row in w['entries'])
        result.append(w)
    # Signed direction at zero and ±1, independently of the positive stated gap.
    if kind == 'budget_gap':
        result += [rename(small_world(delta=d), target, other) for d in (-1, 0, 1)]
    if kind == 'largest_payment':
        # Separate identity from amount: the other row can own the stated maximum.
        # Also exercise Python max's stable tie behavior in both list orders.
        for n, reverse in itertools.product((max(0, value - 1), value, value + 1), (False, True)):
            w = rename(small_world(capacity=0), target, other)
            for e, amount in zip(w['entries'], (n, value)):
                e.update(amount=amount, ledger_amount=amount)
            if reverse:
                w['entries'].reverse()
            result.append(w)
    return result


@dataclass
class Material:
    directory: Path
    truth: dict
    specs: list[str | None]
    regenerated: object


def emit(key: dict, directory: str | Path) -> Material:
    reg = regenerate(key)
    if key['generator']['family'] != 'ledger':
        raise FidelityError('Ledger emitter requires a ledger key')
    specs = [claim_spec(item) for item in reg.claims]
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise FidelityError('Output directory must be new or empty')
    directory.mkdir(parents=True, exist_ok=True)
    base, unknowns = reg.case['base'], reg.case['unknowns']
    sealed = list(generate.worlds(base, unknowns))
    rules = rule_inputs()
    python_rule = oracle(reg)
    expected = [python_rule(w) for w in rules]
    facts = ['namespace Ledger.Material', f'def recorded : World := {world_expr(base)}',
             'def dossier : Dossier := ⟨recorded, ' + lean_list(
                 f'({string(k[9:])}, {lean_list(boolean(v) for v in vs)})'
                 for k, vs in sorted(unknowns.items()) if k.startswith('approval:')) + ', ' +
             lean_list(map(str, unknowns['unsampled_missing'])) + '⟩',
             'def sealedWorlds : List World := ' + lean_list(map(world_expr, sealed)),
             'end Ledger.Material', '']
    (directory / 'Facts.lean').write_text('\n'.join(facts), encoding='utf-8')
    inputs = ['namespace Ledger.Material',
              'def unseen : List World := ' + lean_list(map(world_expr, rules)),
              'def rename (w : World) (a b : String) : World :=',
              '  let name := fun id => if id == "unseen-a" then a else if id == "unseen-b" then b else id',
              '  { w with',
              '    entries := w.entries.map (fun e => { e with id := name e.id })',
              '    approvals := w.approvals.map (fun p => (name p.1, p.2)) }']
    claims = []
    for i, (row, item, spec) in enumerate(zip(key['expectations'], reg.claims, specs)):
        supported = item['kind'] in SUPPORTED
        sealed_values = [bool(item['pred'](w)) for w in sealed]
        # Recheck every Python key, including explicitly ungraded document claims.
        if (verdict(sealed_values) != row['semantic_state'] or
                len(sealed_values) != row['derivation']['worlds'] or
                sum(sealed_values) != row['derivation']['worlds_where_true']):
            raise FidelityError(f'Python key cross-check failed: {row["claim_id"]}')
        truth = {'id': f'{row["variant"]}/{row["claim_id"]}', 'kind': item['kind'],
                 'text': item['text'], 'spec': spec, 'supported': supported,
                 'reason': UNSUPPORTED.get(item['kind']), 'sealed': sealed_values,
                 'expected_verdict': row['semantic_state'], 'unseen': [], 'input_count': 0}
        if supported:
            c = capture(item)
            target = c['entry']['id'] if item['kind'] == 'largest_payment' else base['entries'][0]['id']
            other = next(e['id'] for e in base['entries'] if e['id'] != target)
            extra = boundary_worlds(item, sealed[0])
            applicable = [rename(w, target, other) for w in rules] + extra
            if len(sealed) + len(applicable) > MAX_CLAIM_INPUTS:
                raise FidelityError('Ledger claim input limit exceeded')
            truth.update(unseen=[bool(item['pred'](w)) for w in applicable],
                         input_count=len(sealed) + len(applicable),
                         input_recipe={'rename_rule_ids': [target, other], 'extra_worlds': extra})
            inputs.append(f'def claimWorlds{i} : List World := sealedWorlds ++ '
                          f'(unseen.map (fun w => rename w {string(target)} {string(other)})) ++ '
                          + lean_list(map(world_expr, extra)))
        claims.append(truth)
    inputs += ['end Ledger.Material', '']
    (directory / 'Inputs.lean').write_text('\n'.join(inputs), encoding='utf-8')
    graded = sum(c['supported'] for c in claims)
    truth = {'schema_version': 1, 'classification': 'evaluator_only', 'dossier_id': key['dossier_id'],
             'generator': reg.provenance, 'reference_sha256': hashlib.sha256(
                 (LEAN_DIR / 'Reference.lean').read_bytes()).hexdigest(),
             'recorded': base, 'unknowns': unknowns, 'sealed_worlds': sealed,
             'rule_inputs': rules, 'rule_expected': expected, 'unseen_count': len(rules),
             'bounds': {'rule': MAX_RULE_INPUTS, 'claim': MAX_CLAIM_INPUTS}, 'claims': claims,
             'coverage': {'graded': graded, 'total': len(claims), 'rate': graded / len(claims)},
             'limits': ['Bounded differential measurement, not universal equivalence.',
                        'Rule scores compare complete seven-field summaries per world.',
                        'Date and payee are preserved metadata; the ledger rule reads neither.',
                        'Unsupported claims never receive a positive fidelity grade.']}
    dump(directory / 'truth.json', truth)
    dump(directory / 'key.json', key)
    return Material(directory, truth, specs, reg)


def program(material, reference=True):
    parts = [(LEAN_DIR / 'Interface.lean').read_text(),
             'set_option maxRecDepth 1000000\nset_option maxHeartbeats 0']
    if reference:
        parts.append((LEAN_DIR / 'Reference.lean').read_text())
    parts += [(material.directory / name).read_text() for name in ('Facts.lean', 'Inputs.lean')]
    parts += ['open Ledger Ledger.Material',
              'def emitBits (tag : String) (xs : List Bool) : String :=\n'
              '  "FIDELITY|" ++ tag ++ "|" ++ String.mk (xs.map (fun b => if b then \'1\' else \'0\'))',
              'def summaryText (s : Summary) : String :=\n'
              '  let biggest := match s.largest with\n'
              '    | none => "-,0"\n'
              '    | some (id, amount) => id ++ "," ++ toString amount\n'
              '  String.intercalate "," [toString s.total, toString s.gap, biggest,\n'
              '    toString s.missingCount, toString s.missingAmount,\n'
              '    toString s.recordedCount, toString s.populationMissing]',
              'def emitSummaries (tag : String) (xs : List Summary) : String :=\n'
              '  "LEDGER|" ++ tag ++ "|" ++ String.intercalate ";" (xs.map summaryText)']
    return '\n\n'.join(parts) + '\n'


def parse_summaries(output, lengths):
    """Strict rule-result protocol; expected answers never appear in Lean inputs."""
    found = {}
    for line in output.splitlines():
        if not line.startswith('"LEDGER|'):
            continue
        try:
            _, tag, encoded = json.loads(line).split('|')
            if tag not in lengths or tag in found:
                raise ValueError('unexpected or duplicate tag')
            rows = encoded.split(';')
            if len(rows) != lengths[tag]:
                raise ValueError('incomplete vector')
            summaries = []
            for row in rows:
                fields = row.split(',')
                if len(fields) != 8 or not fields[2]:
                    raise ValueError('malformed summary')
                numbers = [int(fields[i]) for i in (0, 1, 3, 4, 5, 6, 7)]
                if any(n < 0 for j, n in enumerate(numbers) if j != 1):
                    raise ValueError('negative natural number')
                total, gap, amount, count, missing, recorded, population = numbers
                summaries.append({'total': total, 'gap': gap,
                                  'largest': None if fields[2] == '-' else (fields[2], amount),
                                  'missing_count': count, 'missing_amount': missing,
                                  'recorded_count': recorded, 'population_missing': population})
            found[tag] = summaries
        except (ValueError, TypeError) as exc:
            raise FidelityError(f'Malformed Lean ledger summary: {exc}') from exc
    if set(found) != set(lengths):
        raise FidelityError('Missing Lean ledger summary vectors')
    return found


def evaluate(material, runner, candidate=None):
    source = program(material, reference=candidate is None)
    if candidate is not None:
        source += '\n' + candidate.read_text() + '\n'
    rules = ['Reference.rules', *['Reference.' + v for v in VARIANTS]] if candidate is None else ['Candidate.model.rule']
    lengths, rule_lengths = {}, {}
    for j, rule in enumerate(rules):
        source += f'#eval emitSummaries "rule{j}" (unseen.map {rule})\n'
        rule_lengths[f'rule{j}'] = material.truth['unseen_count']
    for i, c in enumerate(material.truth['claims']):
        if not c['supported']:
            continue
        test = (f'Reference.test Reference.rules ({material.specs[i]})' if candidate is None else
                f'Candidate.model.claim {string(c["id"])}')
        source += f'#eval emitBits "claim{i}" (claimWorlds{i}.map ({test}))\n'
        lengths[f'claim{i}'] = c['input_count']
    path = material.directory / ('ReferenceCheck.lean' if candidate is None else 'CandidateCheck.lean')
    if path.exists():
        raise FidelityError('Use a distinct run directory for each reference/candidate execution')
    path.write_text(source, encoding='utf-8')
    result = run_checked(path, runner)
    vectors = parse_vectors(result.stdout, lengths)
    summaries = parse_summaries(result.stdout, rule_lengths)
    for tag, values in summaries.items():
        vectors[tag] = [a == e for a, e in zip(values, material.truth['rule_expected'])]
    return vectors, result


def validate(material: Material, runner: LeanRunner) -> dict:
    actual, execution = evaluate(material, runner)
    expected = [True] * material.truth['unseen_count']
    rule = agreement(actual['rule0'], expected)
    if rule['rate'] != 1:
        raise FidelityError('Lean reference disagrees with Python generator rule')
    checked = 0
    for i, c in enumerate(material.truth['claims']):
        if not c['supported']:
            continue
        values = actual[f'claim{i}']
        if values != c['sealed'] + c['unseen']:
            raise FidelityError(f'Lean reference disagrees with generator predicate: {c["id"]}')
        sealed = values[:len(c['sealed'])]
        row = material.regenerated.key['expectations'][i]
        if (verdict(sealed) != row['semantic_state'] or len(sealed) != row['derivation']['worlds'] or
                sum(sealed) != row['derivation']['worlds_where_true']):
            raise FidelityError(f'Key cross-check failed: {c["id"]}')
        checked += 1
    witnesses = {v: [i for i, matched in enumerate(actual[f'rule{j}']) if not matched]
                 for j, v in enumerate(VARIANTS, 1)}
    missed = [v for v, indices in witnesses.items() if not indices]
    report = {'key_cross_check': True, 'lean_checked_claims': checked,
              'python_checked_claims': len(material.truth['claims']),
              'unsupported_claims': len(material.truth['claims']) - checked,
              'reference_agreement': rule, 'coverage': material.truth['coverage'],
              'test_strength': {'detected': len(VARIANTS) - len(missed), 'total': len(VARIANTS),
                                'rate': (len(VARIANTS) - len(missed)) / len(VARIANTS),
                                'undetected': missed, 'witness_indices': witnesses},
              'seconds': execution.seconds}
    dump(material.directory / 'reference-result.json', report)
    if missed:
        raise FidelityError(f'Unseen set is too weak; undetected variants: {missed}')
    material._validated = True
    return report


def grade(material: Material, candidate: str | Path, runner: LeanRunner, *, prove=False,
          proof_limit=PROOF_LIMIT) -> dict:
    candidate = Path(candidate)
    if not getattr(material, '_validated', False):
        if (material.directory / 'ReferenceCheck.lean').exists():
            raise FidelityError('Use validate() or fresh material before grading')
        validate(material, runner)
    actual, execution = evaluate(material, runner, candidate)
    per_claim = []
    for i, c in enumerate(material.truth['claims']):
        if not c['supported']:
            per_claim.append({'id': c['id'], 'status': 'unsupported', 'reason': c['reason'],
                              'agrees_everywhere': False, 'rate': None})
            continue
        score = agreement(actual[f'claim{i}'], c['sealed'] + c['unseen'])
        per_claim.append({'id': c['id'], 'status': 'graded', 'agrees_everywhere': score['rate'] == 1, **score})
    correct = sum(c['agrees_everywhere'] for c in per_claim)
    graded = sum(c['status'] == 'graded' for c in per_claim)
    report = {'rule_fidelity': agreement(actual['rule0'], [True] * material.truth['unseen_count']),
              'claim_fidelity': {'matched': correct, 'total': graded, 'rate': correct / graded,
                                 'scope': 'supported claims only'},
              'all_claims_fidelity': {'matched': correct, 'total': len(per_claim),
                                      'rate': correct / len(per_claim)},
              'coverage': material.truth['coverage'], 'claims': per_claim, 'seconds': execution.seconds,
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
                       '  unseen.all (fun w => Candidate.model.rule w == Reference.rules w) = true := by decide\n'
                       '#print axioms candidate_rule_agrees\n')
            path = material.directory / 'Agreement.lean'
            path.write_text(source, encoding='utf-8')
            result = run_checked(path, runner)
            marker = "'candidate_rule_agrees' does not depend on any axioms"
            if marker not in result.stdout:
                raise FidelityError('Agreement proof missing or depends on axioms')
            report['proof'] = {'status': 'kernel_checked', 'scope': 'rule on bounded unseen set',
                               'axiom_report': marker, 'seconds': result.seconds}
    dump(material.directory / 'grade.json', report)
    return report


def fixture(material: Material, *, slip=None, wrong_item=False, constant=False, omit_item=False) -> Path:
    if slip is not None and slip not in VARIANTS:
        raise FidelityError(f'Unknown fixture slip: {slip}')
    source = (LEAN_DIR / 'fixtures' / 'Faithful.lean').read_text()
    if slip:
        source += (LEAN_DIR / 'fixtures' / 'Slips.lean').read_text()
    rule = slip or 'rules'
    source += ('\nnamespace Candidate\nopen Ledger\ndef claims (id : String) '
               f'({"_w" if constant else "w"} : World) : Bool :=\n')
    if not constant:
        source += f'  let r := {rule} w\n'
    source += '  match id with\n'
    for row, item in zip(material.truth['claims'], material.regenerated.claims):
        if not row['supported']:
            continue
        c, kind = capture(item), item['kind']
        if constant:
            body = boolean(row['sealed'][0])
        elif kind == 'largest_payment':
            ident = c['entry']['id']
            if wrong_item:
                ident = next(e['id'] for e in material.regenerated.case['base']['entries'] if e['id'] != ident)
            body = f'r.largest == some ({string(ident)}, {c["amount"]})'
            if omit_item:
                body = f'r.largest.any (fun p => p.2 == {c["amount"]})'
        elif kind == 'budget_gap':
            body = f'{"r.gap" if c["over"] else "(-r.gap)"} == {integer(c["value"])}'
        else:
            field = {'sample_total': 'total', 'sample_missing_approvals': 'missingCount',
                     'sample_missing_approval_amount': 'missingAmount', 'recorded_approvals': 'recordedCount',
                     'population_missing_approvals': 'populationMissing'}[kind]
            op = '==' if c.get('exact', True) else '>='
            body = f'(r.{field} : Int) {op} {integer(c["value"])}'
        source += f'  | {string(row["id"])} => {body}\n'
    source += f'  | _ => false\ndef model : Model := ⟨{rule}, claims⟩\nend Candidate\n'
    path = material.directory / 'Fixture.lean'
    path.write_text(source, encoding='utf-8')
    return path
