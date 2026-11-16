"""Evaluator-only deadlines reference emission and bounded fidelity grading.

Only Interface.lean is agent-facing. The injected runner must isolate future real
submissions; the direct runner is for generated programs and authored fixtures.
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

LEAN_DIR = Path(__file__).with_name('fidelity_lean') / 'deadlines'
VARIANTS = ('wrongRuleVersion', 'changeDayUsesOld', 'wrongPeriod', 'countsNoticeDay',
            'ignoresExtension', 'refusedGranted', 'undecidedGranted', 'deadlineIsLate',
            'wrongDayCount', 'missingReplyIsLate')
KINDS = {'reply_deadline', 'reply_timing', 'rule_version', 'late_replies',
         'unrecorded_reply', 'undecided_extension', 'notice_lookup'}
PERIODS = (14, 21, 30, 45, 60)
EXTENSIONS = ((None, False), ('granted', False), ('refused', False),
              ('undecided', False), ('undecided', True))
DAYS = (0, 1, 5, 7, 10, 14)
MAX_RULE_INPUTS = 2000
MAX_CLAIM_INPUTS = 12000
PROOF_LIMIT = 2000


def string(value):
    return json.dumps(value, ensure_ascii=False)


def boolean(value):
    return 'true' if value else 'false'


def lean_list(values):
    return '[' + ', '.join(values) + ']'


def capture(item):
    return inspect.getclosurevars(item['pred']).nonlocals


def date(value):
    return str(value.toordinal())


def optional(value):
    return 'none' if value is None else f'(some {date(value)})'


def jsonable(value):
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def write_json(path, value):
    path.write_text(json.dumps(jsonable(value), indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def policy(case):
    """Recover the discarded builder locals from exact, version-pinned rule sentences.

    The generator precomputes notice.version/period; its closures do not select the
    version. This small bridge is checked against EVERY recorded row before use.
    """
    text = case['sources']['policy.md']
    a = re.findall(r'Version 1 of the reply rule applies to every notice issued before (\d{4}-\d\d-\d\d)\. Its reply period is (\d+) days\.', text)
    b = re.findall(r'Version 2 of the reply rule applies to every notice issued on or after (\d{4}-\d\d-\d\d)\. Its reply period is (\d+) days\.', text)
    if len(a) != 1 or len(b) != 1 or a[0][0] != b[0][0]:
        raise FidelityError('Unsupported deadlines policy sentences')
    p = {'change': dt.date.fromisoformat(a[0][0]), 'first': int(a[0][1]), 'second': int(b[0][1])}
    if p['first'] not in PERIODS or p['second'] not in PERIODS or p['first'] == p['second']:
        raise FidelityError('Unsupported deadlines periods')
    for n in case['base']['notices']:
        v = 1 if n['issued'] < p['change'] else 2
        if n['version'] != v or n['period'] != p['first' if v == 1 else 'second']:
            raise FidelityError('Recorded version/period disagrees with policy')
    return p


def helpers(reg):
    """Use original generator functions, including its integer date arithmetic."""
    found = {}
    def visit(fn):
        if not inspect.isfunction(fn) or id(fn) in seen:
            return
        seen.add(id(fn))
        for name, value in inspect.getclosurevars(fn).nonlocals.items():
            if inspect.isfunction(value):
                found[name] = value
                visit(value)
    seen = set()
    for good, errors in reg.case['slots']:
        for item in [good, *errors]:
            visit(item['pred'])
    if not {'deadline', 'reply', 'late', 'days_late'} <= found.keys():
        raise FidelityError('Generator deadline closure layout changed')
    return found


def outcome(oracle, w, n):
    r = oracle['reply'](w, n)
    return [n['version'], oracle['deadline'](w, n).toordinal(),
            oracle['days_late'](w, n), bool(oracle['late'](w, n)),
            r is not None and not oracle['late'](w, n)]


def notice(n):
    extension = 'absent' if n['extension'] is None else n['extension']
    return f'⟨{string(n["id"])}, {date(n["issued"])}, {optional(n["reply"])}, .{extension}, {n["days"]}⟩'


def world(w):
    p = w['_policy']
    grants = lean_list(f'({string(k[8:])}, {boolean(v)})' for k, v in sorted(w.items()) if k.startswith('granted:'))
    replies = lean_list(f'({string(k[6:])}, {optional(v)})' for k, v in sorted(w.items()) if k.startswith('reply:'))
    return f'⟨{date(p["change"])}, {p["first"]}, {p["second"]}, {lean_list(notice(n) for n in w["notices"])}, {grants}, {replies}⟩'


def replace(w, ident, n):
    result = dict(w, notices=[dict(n, id=ident) if old['id'] == ident else old for old in w['notices']])
    # Counterfactual resolutions travel with the replaced notice, not its old ID.
    result['granted:' + ident] = w.get('granted:' + n['id'], False)
    result['reply:' + ident] = w.get('reply:' + n['id'])
    return result


def rule_inputs(first, oracle):
    p = first['_policy']
    result = []
    def add(p, issued, extension, grant, days, offset, unrecorded=False):
        v = 1 if issued < p['change'] else 2
        n = {'id': 'unseen', 'issued': issued, 'extension': extension, 'days': days,
             'version': v, 'period': p['first' if v == 1 else 'second'], 'reply': None}
        w = dict(first, _policy=p, notices=[dict(old,
                 version=1 if old['issued'] < p['change'] else 2,
                 period=p['first' if old['issued'] < p['change'] else 'second'])
                 for old in first['notices']],
                 **{'granted:unseen': grant, 'reply:unseen': None})
        deadline = oracle['deadline'](w, n)
        received = None if offset is None else deadline + generate.DAY * offset
        if unrecorded or offset is None:
            w['reply:unseen'] = received
        else:
            n['reply'] = received
        result.append((w, n))
    for offset, (extension, grant), days, gap in itertools.product((-1, 0, 1), EXTENSIONS, DAYS, (None, -1, 0, 1)):
        add(p, p['change'] + generate.DAY * offset, extension, grant, days, gap)
    for a, b in itertools.permutations(PERIODS, 2):
        q = dict(p, first=a, second=b)
        for offset, gap in itertools.product((-1, 0, 1), (None, -1, 0, 1)):
            add(q, p['change'] + generate.DAY * offset, 'granted', False, 7, gap)
    # Calendar transitions, including leap day, use ordinal arithmetic, not date strings.
    for issued, (extension, grant), days, gap in itertools.product(
            [dt.date(2032, 2, 28), dt.date(2032, 2, 29), dt.date(2032, 3, 1),
             dt.date(2032, 12, 31), dt.date(2033, 1, 1)], EXTENSIONS, DAYS, (None, -1, 0, 1)):
        add(p, issued, extension, grant, days, gap)
    for offset, (extension, grant), gap in itertools.product((-1, 0, 1), EXTENSIONS, (-1, 0, 1)):
        add(p, p['change'] + generate.DAY * offset, extension, grant, 7, gap, unrecorded=True)
    if len(result) > MAX_RULE_INPUTS:
        raise FidelityError('Deadlines unseen rule input limit exceeded')
    return result


def claim_spec(item):
    c, kind = capture(item), item['kind']
    target = c['n']['id'] if 'n' in c else None
    q = string(target)
    if kind == 'reply_deadline':
        spec = f'.deadline {q} {date(c["date"])}'
    elif kind == 'reply_timing':
        spec = f'.daysLate {q} ({c["days"]})' if 'days' in c else f'.onTime {q}'
    elif kind == 'rule_version':
        spec = f'.version {q} {c["version"]}'
    elif kind == 'late_replies':
        spec = f'.lateCount {c["value"]} {boolean(c["exact"])}'
    elif kind == 'unrecorded_reply':
        spec = f'.unrecorded {q}'
    elif kind == 'undecided_extension':
        spec = f'.undecided {q}'
    elif kind == 'notice_lookup':
        spec = f'.issued {q} {date(c["date"])}'
    else:
        raise FidelityError(f'Unsupported deadlines claim: {kind}; no fact-test adapter')
    return spec, target


@dataclass
class Material:
    directory: Path
    truth: dict
    specs: list[str]
    regenerated: object


def emit(key, directory):
    reg = regenerate(key)
    if key['generator']['family'] != 'deadlines':
        raise FidelityError('Deadlines emitter requires a deadlines key')
    specs_targets = [claim_spec(c) for c in reg.claims]  # Fail before emitting unsupported claims.
    p, oracle = policy(reg.case), helpers(reg)
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise FidelityError('Output directory must be new or empty')
    directory.mkdir(parents=True, exist_ok=True)
    base = dict(reg.case['base'], _policy=p)
    unknowns = reg.case['unknowns']
    sealed = list(generate.worlds(base, unknowns))
    first = sealed[0]
    rules = rule_inputs(first, oracle)
    facts = ['namespace Deadlines.Material', f'def recorded : World := {world(base)}',
             'def dossier : Dossier := ⟨recorded, ' + lean_list(
                 f'({string(k[8:])}, {lean_list(boolean(v) for v in vs)})'
                 for k, vs in sorted(unknowns.items()) if k.startswith('granted:')) + ', ' + lean_list(
                 f'({string(k[6:])}, {lean_list(optional(v) for v in vs)})'
                 for k, vs in sorted(unknowns.items()) if k.startswith('reply:')) + '⟩',
             'def sealedWorlds : List World := ' + lean_list(
                 '{ recorded with grants := ' + lean_list(
                     f'({string(k[8:])}, {boolean(v)})' for k, v in sorted(w.items()) if k.startswith('granted:')) +
                 ', replies := ' + lean_list(
                     f'({string(k[6:])}, {optional(v)})' for k, v in sorted(w.items()) if k.startswith('reply:')) + ' }'
                 for w in sealed),
             'def first : World := sealedWorlds.head!', 'end Deadlines.Material', '']
    (directory / 'Facts.lean').write_text('\n'.join(facts), encoding='utf-8')
    inputs = ['namespace Deadlines.Material',
        'def input (a b : Nat) (issued : Date) (r : Option Date) (e : Extension)',
        '    (days : Nat) (g : Bool) (resolved : Option Date) : World × Notice :=',
        '  ({ first with firstPeriod := a, secondPeriod := b, grants := ("unseen", g) :: first.grants, replies := ("unseen", resolved) :: first.replies },',
        '   ⟨"unseen", issued, r, e, days⟩)',
        'def unseen : List (World × Notice) := ' + lean_list(
            f'input {w["_policy"]["first"]} {w["_policy"]["second"]} {date(n["issued"])} '
            f'{optional(n["reply"])} .{n["extension"] or "absent"} {n["days"]} '
            f'{boolean(w["granted:unseen"])} {optional(w["reply:unseen"])}'
            for w, n in rules),
        'def replace (w : World) (id : String) (n : Notice) : World :=',
        '  { w with notices := w.notices.map (fun old => if old.id == id then { n with id := id } else old), grants := (id, ((w.grants.find? (fun x => x.1 == n.id)).map Prod.snd).getD false) :: w.grants.filter (fun x => x.1 != id), replies := (id, ((w.replies.find? (fun x => x.1 == n.id)).map Prod.snd).getD none) :: w.replies.filter (fun x => x.1 != id) }']
    truths = []
    for i, (row, item, (spec, target)) in enumerate(zip(key['expectations'], reg.claims, specs_targets)):
        targets = [target] if target else [n['id'] for n in base['notices']]
        applicable = [replace(w, ident, n) for ident in targets for w, n in rules]
        extra, c = [], capture(item)
        if item['kind'] == 'late_replies':
            for count in range(len(first['notices']) + 1):
                w = deepcopy(first)
                for j, n in enumerate(w['notices']):
                    n['reply'] = oracle['deadline'](w, n) + generate.DAY * (1 if j < count else 0)
                extra.append(w)
        if item['kind'] in ('reply_timing', 'notice_lookup', 'reply_deadline'):
            for offset in (-1, 0, 1):
                w = deepcopy(first)
                n = next(n for n in w['notices'] if n['id'] == target)
                if item['kind'] == 'reply_timing':
                    n['reply'] = oracle['deadline'](w, n) + generate.DAY * (c.get('days', 0) + offset)
                else:
                    # Equality claims must have positive as well as negative counterfactual witnesses.
                    n['issued'] = c['date'] + generate.DAY * offset
                    if item['kind'] == 'reply_deadline':
                        n['extension'], n['days'] = None, 0
                        # Choose a consistent branch even when subtraction crosses the change date.
                        options = [c['date'] - generate.DAY * p[k] for k in ('first', 'second')]
                        valid = [d for d in options if d + generate.DAY * p['first' if d < p['change'] else 'second'] == c['date']]
                        n['issued'] = (valid or options)[0] + generate.DAY * offset
                    n['version'] = 1 if n['issued'] < p['change'] else 2
                    n['period'] = p['first' if n['version'] == 1 else 'second']
                extra.append(w)
        all_worlds = sealed + applicable + extra
        if len(all_worlds) > MAX_CLAIM_INPUTS:
            raise FidelityError('Deadlines claim input limit exceeded')
        expressions = ['sealedWorlds'] + [f'unseen.map (fun x => replace x.1 {string(t)} x.2)' for t in targets]
        expressions.append(lean_list(world(w) for w in extra))
        inputs.append(f'def claimWorlds{i} : List World := ' + ' ++ '.join(f'({e})' for e in expressions))
        values = [bool(item['pred'](w)) for w in all_worlds]
        truths.append({'id': f'{row["variant"]}/{row["claim_id"]}', 'kind': item['kind'], 'text': item['text'],
                       'supported': True, 'spec': spec, 'sealed': values[:len(sealed)], 'unseen': values[len(sealed):],
                       'input_count': len(values), 'input_recipe': {'replace_targets_with_rule_inputs': targets,
                       'extra_worlds': extra}, 'expected_verdict': row['semantic_state']})
    inputs += ['end Deadlines.Material', '']
    (directory / 'Inputs.lean').write_text('\n'.join(inputs), encoding='utf-8')
    truth = {'schema_version': 1, 'classification': 'evaluator_only', 'dossier_id': key['dossier_id'],
             'generator': reg.provenance, 'reference_sha256': hashlib.sha256((LEAN_DIR / 'Reference.lean').read_bytes()).hexdigest(),
             'recorded': base, 'unknowns': unknowns, 'sealed_worlds': sealed,
             'rule_inputs': [{'policy': w['_policy'], 'notice': n, 'grant': w['granted:unseen'], 'reply': w['reply:unseen']} for w, n in rules],
             'rule_expected': [outcome(oracle, w, n) for w, n in rules], 'unseen_count': len(rules),
             'bounds': {'rule': MAX_RULE_INPUTS, 'claim': MAX_CLAIM_INPUTS}, 'claims': truths,
             'coverage': {'graded': len(truths), 'total': len(truths), 'rate': 1.0, 'unsupported': []},
             'limits': ['Bounded differential measurement, not universal equivalence.',
                        'Register-status claims test recorded structured fields, not original document ingestion.',
                        'Version/period selection is bridged from exact policy sentences and checked against all recorded rows.',
                        'All sealed worlds are tested; counterfactuals are not the full Cartesian product.',
                        'Unrecorded replies use the generator domain: none or issue date through issue date + 150 days.']}
    write_json(directory / 'truth.json', truth)
    write_json(directory / 'key.json', key)
    return Material(directory, truth, [s for s, _ in specs_targets], reg)


def program(material, *, reference=True):
    parts = [(LEAN_DIR / 'Interface.lean').read_text(),
             'set_option maxRecDepth 1000000\nset_option maxHeartbeats 0']
    if reference:
        parts.append((LEAN_DIR / 'Reference.lean').read_text())
    parts += [(material.directory / name).read_text() for name in ('Facts.lean', 'Inputs.lean')]
    parts += ['open Deadlines Deadlines.Material',
              'def emitBits (tag : String) (xs : List Bool) : String :=',
              '  "FIDELITY|" ++ tag ++ "|" ++ String.mk (xs.map (fun b => if b then \'1\' else \'0\'))',
              'def encodeOutcome (o : Outcome) : String :=',
              '  String.intercalate "," [toString o.version, toString o.deadline,',
              '    o.daysLate.elim "N" toString, if o.late then "1" else "0", if o.onTime then "1" else "0"]',
              'def emitOutcomes (tag : String) (xs : List Outcome) : String :=',
              '  "DEADLINES|" ++ tag ++ "|" ++ String.intercalate ";" (xs.map encodeOutcome)']
    return '\n\n'.join(parts) + '\n'


def parse_outcomes(output, tags, count):
    found = {}
    for line in output.splitlines():
        if not line.startswith('"DEADLINES|'):
            continue
        try:
            _, tag, payload = json.loads(line).split('|')
            if tag not in tags or tag in found:
                raise ValueError('unknown or duplicate tag')
            rows = payload.split(';')
            if len(rows) != count:
                raise ValueError('incomplete vector')
            values = []
            for row in rows:
                if not re.fullmatch(r'[12],[1-9][0-9]*,(?:N|-?[0-9]+),[01],[01]', row):
                    raise ValueError('invalid outcome')
                v, d, gap, late, ontime = row.split(',')
                values.append([int(v), int(d), None if gap == 'N' else int(gap), late == '1', ontime == '1'])
            found[tag] = values
        except (ValueError, TypeError) as exc:
            raise FidelityError(f'Malformed deadlines outcomes: {exc}') from exc
    if set(found) != set(tags):
        raise FidelityError('Missing deadlines outcome vectors')
    return found


def evaluate(material, runner, candidate=None):
    source = program(material, reference=candidate is None)
    rules = ['Reference.applyRule', *['Reference.' + v for v in VARIANTS]] if candidate is None else ['Candidate.model.rule']
    if candidate is not None:
        source += '\n' + candidate.read_text() + '\n'
    tags, lengths = [], {}
    for j, rule in enumerate(rules):
        tag = f'rule{j}'
        tags.append(tag)
        source += f'#eval emitOutcomes "{tag}" (unseen.map (fun x => {rule} x.1 x.2))\n'
    for i, c in enumerate(material.truth['claims']):
        test = f'Reference.test Reference.applyRule ({material.specs[i]})' if candidate is None else f'Candidate.model.claim {string(c["id"])}'
        source += f'#eval emitBits "claim{i}" (claimWorlds{i}.map ({test}))\n'
        lengths[f'claim{i}'] = c['input_count']
    path = material.directory / ('ReferenceCheck.lean' if candidate is None else 'CandidateCheck.lean')
    if path.exists():
        raise FidelityError('Use a distinct run directory for each reference/candidate execution')
    path.write_text(source, encoding='utf-8')
    result = run_checked(path, runner)
    return {**parse_outcomes(result.stdout, tags, material.truth['unseen_count']),
            **parse_vectors(result.stdout, lengths)}, result


def outcome_agreement(actual, expected):
    if len(actual) != len(expected):
        raise FidelityError('Unequal outcome vectors')
    return agreement([a == e for a, e in zip(actual, expected)], [True] * len(expected))


def validate(material, runner):
    actual, execution = evaluate(material, runner)
    expected = material.truth['rule_expected']
    score = outcome_agreement(actual['rule0'], expected)
    if score['rate'] != 1:
        raise FidelityError('Lean reference disagrees with Python generator rule')
    for i, c in enumerate(material.truth['claims']):
        values = actual[f'claim{i}']
        if values != c['sealed'] + c['unseen']:
            raise FidelityError(f'Lean reference disagrees with generator predicate: {c["id"]}')
        sealed = values[:len(c['sealed'])]
        row = material.regenerated.key['expectations'][i]
        if (verdict(sealed) != row['semantic_state'] or len(sealed) != row['derivation']['worlds'] or
                sum(sealed) != row['derivation']['worlds_where_true']):
            raise FidelityError(f'Key cross-check failed: {c["id"]}')
    witnesses = {v: [i for i, (a, e) in enumerate(zip(actual[f'rule{j}'], expected)) if a != e]
                 for j, v in enumerate(VARIANTS, 1)}
    missed = [v for v, indices in witnesses.items() if not indices]
    report = {'key_cross_check': True, 'reference_agreement': score, 'coverage': material.truth['coverage'],
              'test_strength': {'detected': len(VARIANTS) - len(missed), 'total': len(VARIANTS),
                                'rate': (len(VARIANTS) - len(missed)) / len(VARIANTS),
                                'undetected': missed, 'witness_indices': witnesses}, 'seconds': execution.seconds}
    write_json(material.directory / 'reference-result.json', report)
    if missed:
        raise FidelityError(f'Unseen set is too weak; undetected variants: {missed}')
    material._validated = True
    return report


def grade(material, candidate, runner, *, prove=False, proof_limit=PROOF_LIMIT):
    candidate = Path(candidate)
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
    report = {'rule_fidelity': outcome_agreement(actual['rule0'], material.truth['rule_expected']),
              'claim_fidelity': {'matched': correct, 'total': len(per_claim), 'rate': correct / len(per_claim)},
              'coverage': material.truth['coverage'], 'claims': per_claim, 'seconds': execution.seconds,
              'candidate_sha256': hashlib.sha256(candidate.read_bytes()).hexdigest(), 'proof': {'status': 'not_requested'}}
    if prove:
        if material.truth['unseen_count'] > proof_limit:
            report['proof'] = {'status': 'skipped', 'reason': 'bounded set exceeds proof_limit'}
        elif report['rule_fidelity']['rate'] != 1:
            report['proof'] = {'status': 'not_equal', 'reason': 'rule counterexample observed'}
        else:
            source = program(material) + '\n' + candidate.read_text() + '\n'
            source += ('theorem candidate_rule_agrees :\n'
                       '  unseen.all (fun x => Candidate.model.rule x.1 x.2 == Reference.applyRule x.1 x.2) = true := by decide\n'
                       '#print axioms candidate_rule_agrees\n')
            proof = material.directory / 'Agreement.lean'
            proof.write_text(source, encoding='utf-8')
            result = run_checked(proof, runner)
            marker = "'candidate_rule_agrees' does not depend on any axioms"
            if marker not in result.stdout:
                raise FidelityError('Agreement proof missing or depends on axioms')
            report['proof'] = {'status': 'kernel_checked', 'scope': 'rule on bounded unseen set',
                               'axiom_report': marker, 'seconds': result.seconds}
    write_json(material.directory / 'grade.json', report)
    return report


def fixture(material, *, slip=None, wrong_notice=False, constant=False):
    if slip is not None and slip not in VARIANTS:
        raise FidelityError(f'Unknown fixture slip: {slip}')
    source = (LEAN_DIR / 'fixtures' / 'Faithful.lean').read_text()
    if slip:
        source += (LEAN_DIR / 'fixtures' / 'Slips.lean').read_text()
    source += ('\nnamespace Candidate\nopen Deadlines\ndef claims (id : String) '
               f'({"_w" if constant else "w"} : World) : Bool :=\n  match id with\n')
    rule = slip or 'applyRule'
    for row, item in zip(material.truth['claims'], material.regenerated.claims):
        c, kind = capture(item), item['kind']
        ident = c['n']['id'] if 'n' in c else None
        if wrong_notice and ident:
            ident = next(n['id'] for n in material.regenerated.case['base']['notices'] if n['id'] != ident)
        get = f'(w.notices.find? (fun n => n.id == {string(ident)})).any'
        if constant:
            body = boolean(row['sealed'][0])
        elif kind == 'reply_deadline':
            body = f'{get} (fun n => ({rule} w n).deadline == {date(c["date"])})'
        elif kind == 'reply_timing':
            body = f'{get} (fun n => ({rule} w n).daysLate == some ({c["days"]}))' if 'days' in c else f'{get} (fun n => ({rule} w n).onTime)'
        elif kind == 'rule_version':
            body = f'{get} (fun n => ({rule} w n).version == {c["version"]})'
        elif kind == 'late_replies':
            body = f'(w.notices.filter (fun n => ({rule} w n).late)).length {"==" if c["exact"] else ">="} {c["value"]}'
        elif kind == 'unrecorded_reply':
            body = f'{get} (fun n => n.reply == none)'
        elif kind == 'undecided_extension':
            body = f'{get} (fun n => n.extension == .undecided)'
        elif kind == 'notice_lookup':
            body = f'{get} (fun n => n.issued == {date(c["date"])})'
        else:
            raise FidelityError(f'Unsupported fixture kind: {kind}')
        source += f'  | {string(row["id"])} => {body}\n'
    source += f'  | _ => false\ndef model : Model := ⟨{rule}, claims⟩\nend Candidate\n'
    path = material.directory / 'Fixture.lean'
    if path.exists():
        raise FidelityError('Use a distinct run directory for each fixture')
    path.write_text(source, encoding='utf-8')
    return path
