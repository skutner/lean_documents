#!/usr/bin/env python3
"""Score finished interface-arm reviews, without launching agents.

The runner is injected and MUST declare ``sandboxed = True``. This declaration is
an integration contract, not a sandbox implementation or a CLI escape hatch.
A runner must isolate untrusted Lean from the network and evaluator files. The
product lexical filter runs first. No direct Lean executable option is offered.

Only measured facts/rules enter their aggregate denominators; unavailable reviews
are counted separately. Missing registered claims score zero among supported
claims. Unsupported kinds never enter claim-fidelity denominators. Wilson bounds
are descriptive binomial intervals on the bounded checks, not independent-sample
confidence claims about arbitrary documents. Facts include scalar fields, option
markers and list lengths in the recorded Interface World. Synthetic uncertainty
domains are excluded: their finite representatives need not be source facts.
List order follows the Interface, particularly approvals' person indices.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib
import importlib.util
import json
import math
from pathlib import Path
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from evalsuite import (fidelity_approvals, fidelity_casework, fidelity_deadlines, fidelity_ledger,
                       fidelity_packet, fidelity_procurement)
from evalsuite.fidelity import FidelityError, run_checked

FAMILIES = {'approvals': fidelity_approvals, 'ledger': fidelity_ledger,
            'deadlines': fidelity_deadlines, 'packet': fidelity_packet,
            'casework': fidelity_casework, 'procurement': fidelity_procurement}
ARM = 'skill-lean-interface'
SUBMISSION = Path('work/fidelity/Candidate.lean')
METRICS = ('fact_accuracy', 'rule_fidelity', 'claim_fidelity')
MAX_SOURCE = 2_000_000
SAFE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}')


def require_sandbox(runner):
    if not callable(runner) or getattr(runner, 'sandboxed', False) is not True:
        raise FidelityError('Refusing agent-written Lean: runner must declare sandboxed = True; '
                            'trusted_lean_runner is for authored fixtures only')


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def product_filter():
    scripts = ROOT / 'skills/evidence-to-proof-review/scripts'
    spec = importlib.util.spec_from_file_location('_fidelity_product_audit', scripts / 'audit.py')
    module = importlib.util.module_from_spec(spec)
    # audit.py itself puts its sibling scripts on sys.path.
    spec.loader.exec_module(module)
    return module


def inspect_submission(audit, path, family):
    """Concrete Interface profile: no syntax/output/projection-name overrides.

    The product filter is still mandatory. This additional convention protects
    family graders' fixed names; it is not an execution sandbox. In particular,
    Lean permits a separate `Candidate.model.rule` constant which shadows field
    projection syntax. Nested `where` declarations can create that name too.
    """
    code = audit.lean_code(path.read_text())
    forbidden = (r'\b(instance|attribute|notation|infix|infixl|infixr|prefix|postfix|'
                 r'where|section|mutual|export|import)\b|[«»]')
    if re.search(forbidden, code):
        raise audit.AuditError('Unsupported declaration/scope/syntax registration '
                               '(fidelity result-integrity contract)')
    active, scopes, declarations, opened = False, 0, 0, 0
    for line in code.splitlines():
        line = line.strip()
        if re.match(r'(namespace|end)\b', line):
            scopes += 1
            if line == 'namespace Candidate' and not active:
                active = True
            elif line == 'end Candidate' and active:
                active = False
            else:
                raise audit.AuditError('Use balanced, unnested namespace Candidate / end Candidate blocks')
        if re.match(r'open\b', line):
            opened += 1
            if not active or line != 'open ' + family.title():
                raise audit.AuditError('Only open the selected family inside namespace Candidate')
        matches = list(re.finditer(r"\b(def|abbrev|theorem|lemma|structure|class|inductive)\s+([\w.']+)", line))
        for match in matches:
            declarations += 1
            if not active or '.' in match[2]:
                raise audit.AuditError('Use bare declaration names inside namespace Candidate '
                                       '(fidelity result-integrity contract)')
    if opened != len(re.findall(r'\bopen\b', code)):
        raise audit.AuditError('Use a conventional family open declaration')
    if active or scopes != len(re.findall(r'\b(namespace|end)\b', code)):
        raise audit.AuditError('Candidate namespaces must be conventional, balanced standalone lines')
    if declarations != len(re.findall(r'\b(def|abbrev|theorem|lemma|structure|class|inductive)\b', code)):
        raise audit.AuditError('Candidate declarations require conventional bare names')


def read_regular(root, relative, limit=MAX_SOURCE):
    """Reject symlinked components and special/oversize files in agent-owned trees."""
    root, relative = Path(root), Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise FidelityError('Unsafe retained-file path')
    current = root
    for part in ('.', *relative.parts):
        current = current / part
        mode = current.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise FidelityError(f'Symlink in retained-file path: {current}')
    if not stat.S_ISREG(mode) or current.stat().st_size > limit:
        raise FidelityError(f'Not a bounded regular file: {current}')
    return current.read_bytes()


# Small explicit serializers mirror only Interface fields, never operational rules.
# Scalar repr strings are escaped by Lean, then the outer string is JSON decoded.
SCHEMAS = {
    'approvals': {
        'Request': [('id', 'String'), ('requester', 'Nat'), ('amount', 'Nat'), ('signatures', ['Signature'])],
        'World': [('people', ['String']), ('roles', ['Role']), ('limit', 'Nat'),
                  ('requests', ['Request']), ('resolutions', ('String', 'Nat'))],
        'Dossier': [('recorded', 'World'), ('unknowns', ('String', ['Nat']))]},
    'ledger': {
        'Entry': [('id', 'String'), ('payee', 'String'), ('date', 'Nat'), ('ledgerAmount', 'Nat'),
                  ('amount', 'Nat'), ('approval', 'Approval'), ('voided', 'Bool')],
        'World': [('entries', ['Entry']), ('budget', 'Int'), ('population', 'Nat'),
                  ('approvals', ('String', 'Bool')), ('unsampledMissing', 'Nat')],
        'Dossier': [('recorded', 'World'), ('approvalUnknowns', ('String', ['Bool'])),
                    ('unsampledChoices', ['Nat'])]},
    'deadlines': {
        'Notice': [('id', 'String'), ('issued', 'Int'), ('reply', {'option': 'Int'}),
                   ('extension', 'Extension'), ('days', 'Nat')],
        'World': [('change', 'Int'), ('firstPeriod', 'Nat'), ('secondPeriod', 'Nat'),
                  ('notices', ['Notice']), ('grants', ('String', 'Bool')),
                  ('replies', ('String', {'option': 'Int'}))],
        'Dossier': [('recorded', 'World'), ('grantChoices', ('String', ['Bool'])),
                    ('replyChoices', ('String', [{'option': 'Int'}]))]},
    'packet': {
        'Certificate': [('result', 'Result'), ('testedDay', 'Int')],
        'Unit': [('id', 'String'), ('certificate', {'option': 'Certificate'})],
        'World': [('reviewDay', 'Int'), ('maxAge', 'Nat'), ('units', ['Unit'])],
        'Dossier': [('recorded', 'World'), ('unknowns', ('String', ['Certificate']))]},
    # World.neighbour is evaluator-supplied row-confusion data that no source states,
    # so it is not a transcribed fact and is not compared; the prompt asks for [].
    'casework': {
        'Notice': [('id', 'String'), ('issued', 'Nat'), ('cls', 'Class'), ('addressee', 'String'),
                   ('channel', 'Channel'), ('sent', {'option': 'Nat'}), ('extension', {'option': 'String'})],
        'Extension': [('ref', 'String'), ('nid', 'String'), ('days', 'Nat'), ('decision', 'Decision'),
                      ('date', {'option': 'Nat'})],
        'World': [('periods', ('Class', 'Nat')), ('amended', ('Class', 'Nat')), ('effective', 'Nat'),
                  ('holidays', ['Nat']), ('registers', [['Notice']]), ('inForce', 'Nat'),
                  ('extensions', ['Extension']), ('resolutions', ('String', 'Nat'))],
        'Dossier': [('recorded', 'World'), ('unknowns', ('String', ['Nat']))]},
    'procurement': {
        'Date': [('ordinal', 'Nat'), ('month', 'Nat')],
        'Staff': [('name', 'String'), ('role', 'Role'), ('department', 'String')],
        'Request': [('id', 'String'), ('date', 'Date'), ('requester', 'String'), ('supplier', 'String'),
                    ('amount', {'option': 'Nat'}), ('currency', 'Currency'), ('signers', [{'option': 'String'}])],
        'Delegation': [('id', 'String'), ('person', 'String'), ('role', 'Role'), ('first', 'Nat'),
                       ('last', {'option': 'Nat'})],
        'PolicyIssue': [('file', 'String'), ('issued', 'Nat'), ('low', 'Nat'), ('high', 'Nat'),
                        ('exempt', {'option': 'Nat'})],
        'Rate': [('currency', 'Currency'), ('month', 'Nat'), ('value', 'Nat')],
        'World': [('staff', ['Staff']), ('policies', ['PolicyIssue']), ('inForce', 'String'),
                  ('amendmentIssued', 'Nat'), ('effective', 'Nat'), ('amendedLow', 'Nat'),
                  ('amendedHigh', 'Nat'), ('delegations', ['Delegation']), ('rates', ['Rate']),
                  ('requests', ['Request']), ('amounts', ('String', 'Nat')),
                  ('signers', ('String', 'String')), ('lastDays', ('String', 'Nat'))],
        'Dossier': [('recorded', 'World'), ('amountAlternatives', ('String', ['Nat'])),
                    ('signerAlternatives', ('String', ['String'])), ('lastDayAlternatives', ('String', ['Nat']))]},
}


def fact_serializer(family):
    schema, namespace = SCHEMAS[family], family.title()

    def expr(kind):
        if isinstance(kind, list):
            return f'(many {expr(kind[0])})'
        if isinstance(kind, tuple):
            return f'(many (pair {expr(kind[0])} {expr(kind[1])}))'
        if isinstance(kind, dict):
            return f'(optional {expr(kind["option"])})'
        return 'fields' + kind if kind in schema else 'scalar'

    source = f'''namespace FidelityFacts
open {namespace}
abbrev Cells := List (String × String)
def scalar {{α : Type}} [Repr α] (p : String) (v : α) : Cells := [(p, reprStr v)]
def many {{α : Type}} (f : String → α → Cells) (p : String) (xs : List α) : Cells :=
  [(p ++ ".length", toString xs.length)] ++
  (xs.zipIdx.flatMap (fun (v, i) => f (p ++ "[" ++ toString i ++ "]") v))
def pair {{α β : Type}} (f : String → α → Cells) (g : String → β → Cells)
    (p : String) (v : α × β) : Cells := f (p ++ ".key") v.1 ++ g (p ++ ".value") v.2
def optional {{α : Type}} (f : String → α → Cells) (p : String) (v : Option α) : Cells :=
  match v with
  | none => [(p ++ ".present", "false")]
  | some x => [(p ++ ".present", "true")] ++ f p x
'''
    for name, fields in schema.items():
        # Packet.Unit conflicts with the built-in Unit; qualify every type.
        source += f'def fields{name} (p : String) (v : {namespace}.{name}) : Cells :=\n  '
        source += ' ++\n  '.join(f'{expr(kind)} (p ++ ".{field}") v.{field}' for field, kind in fields) + '\n'
    source += '''def encode (xs : Cells) : String :=
  String.intercalate "\\n" (xs.map (fun (k, v) => k ++ "\\t" ++ v))
def printFacts (w : World) : IO _root_.Unit :=
  IO.println (reprStr ("FACTS|" ++ encode (fieldsWorld "dossier.recorded" w)))
def printClaims (ids : List String) : IO _root_.Unit :=
  IO.println (reprStr ("CLAIMS|" ++ String.intercalate "\\n" ids))
end FidelityFacts
'''
    return source


def tagged(output, marker):
    values = []
    for line in output.splitlines():
        if line.startswith('"' + marker):
            try:
                value = json.loads(line)
            except ValueError as exc:
                raise FidelityError(f'Malformed {marker} output') from exc
            if not isinstance(value, str) or not value.startswith(marker):
                raise FidelityError(f'Malformed {marker} output')
            values.append(value[len(marker):])
    if len(values) != 1:
        raise FidelityError(f'Expected exactly one {marker} result')
    return values[0]


def parse_cells(output):
    cells = {}
    for line in tagged(output, 'FACTS|').splitlines():
        path, sep, value = line.partition('\t')
        if not sep or not path or path in cells:
            raise FidelityError('Malformed or duplicate fact field')
        cells[path] = value
    if not cells:
        raise FidelityError('Empty fact table')
    return cells


def ratio(matched=0, total=0):
    return {'matched': matched, 'total': total, 'rate': matched / total if total else None}


def compare_facts(actual, expected):
    fields = sorted(actual.keys() | expected.keys())
    mismatches = [{'field': field, 'expected': expected.get(field), 'actual': actual.get(field),
                   'status': 'extra' if field not in expected else 'missing' if field not in actual else 'different'}
                  for field in fields if field not in actual or field not in expected or actual[field] != expected[field]]
    return {**ratio(len(fields) - len(mismatches), len(fields)), 'mismatches': mismatches,
            'counting': 'scalar fields, option markers and list lengths; ordered recorded World only'}


def candidate_facts(module, family, candidate, directory, runner):
    source = (module.LEAN_DIR / 'Interface.lean').read_text() + '\n'
    # Capture trusted serializers/instances before candidate declarations. IO output
    # also avoids candidate Repr/ToString instances spoofing the measured facts.
    source += fact_serializer(family) + '\n' + candidate.read_text() + '\n'
    # This compilation precedes reference material: candidate cannot depend on it.
    source += f'\ndef fidelityModelContract : _root_.{family.title()}.Model := _root_.Candidate.model\n'
    source += f'def fidelityDossierContract : _root_.{family.title()}.Dossier := _root_.Candidate.dossier\n'
    source += f'#eval FidelityFacts.printFacts (_root_.{family.title()}.Dossier.recorded _root_.fidelityDossierContract)\n'
    source += '#eval FidelityFacts.printClaims Candidate.claimIds\n'
    path = directory / 'SubmissionCheck.lean'
    path.write_text(source, encoding='utf-8')
    result = run_checked(path, runner)
    claims = tagged(result.stdout, 'CLAIMS|').splitlines()
    if len(claims) != len(set(claims)) or any(not re.fullmatch(r'(correct|mixed)/[A-Za-z0-9-]+', c) for c in claims):
        raise FidelityError('Invalid or duplicate Candidate.claimIds')
    return parse_cells(result.stdout), claims


def expected_facts(module, family, material, runner):
    source = (module.LEAN_DIR / 'Interface.lean').read_text() + '\n'
    source += (material.directory / 'Facts.lean').read_text() + '\n' + fact_serializer(family)
    source += f'#eval FidelityFacts.printFacts {family.title()}.Material.dossier.recorded\n'
    path = material.directory / 'ExpectedFacts.lean'
    path.write_text(source, encoding='utf-8')
    return parse_cells(run_checked(path, runner).stdout)


def score_review(campaign, keys, review, directory, runner, *, prove=False):
    """One terminal record, including failed/missing sheets; exactly one outcome."""
    require_sandbox(runner)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    record = {'schema_version': 1, **{k: review.get(k) for k in
              ('unit', 'arm', 'dossier', 'variant', 'repeat', 'opaque_id', 'status', 'ended')},
              'family': None, 'outcome': 'grading_error', 'detail': None,
              **{metric: ratio() for metric in METRICS}, 'missing_claims': [],
              'unsupported_claims': [], 'claims': [], 'proof': {'status': 'not_requested'}}
    try:
        for field in ('arm', 'dossier', 'opaque_id'):
            if not isinstance(review.get(field), str) or not SAFE_NAME.fullmatch(review[field]):
                raise FidelityError(f'Invalid review {field}')
        if review.get('variant') not in ('correct', 'mixed'):
            raise FidelityError('Unknown report variant')
        key = json.loads(read_regular(keys, review['dossier'] + '.json'))
        key.pop('canary', None)  # sealing adds only this field; regenerate checks everything else
        family = key.get('generator', {}).get('family')
        record['family'] = family
        if family not in FAMILIES:
            record.update(outcome='unsupported_family', detail=f'No fidelity grader for {family}')
            return record
        module = FAMILIES[family]
        material = module.emit(key, directory / 'material')
        selected = [c for c in material.truth['claims'] if c['id'].startswith(review['variant'] + '/')]
        if not selected:
            raise FidelityError('No key claims for review variant')
        record['unsupported_claims'] = [{'id': c['id'], 'kind': c['kind'], 'reason': c.get('reason')}
                                        for c in selected if not c.get('supported', True)]
        record['expected_claims'] = len(selected)
        agent = Path(campaign) / review['arm'] / 'runs' / review['opaque_id'] / 'agent'
        try:
            data = read_regular(agent, SUBMISSION)
        except FileNotFoundError:
            record.update(outcome='no_submission', detail=f'No regular file at {SUBMISSION}')
            return record
        except FidelityError as exc:
            record.update(outcome='invalid_submission', detail=str(exc))
            return record
        candidate = directory / 'Candidate.lean'
        candidate.write_bytes(data)  # immutable-for-this-run evaluator snapshot, never edit hand-in
        record['candidate_sha256'] = hashlib.sha256(data).hexdigest()
        audit = product_filter()
        try:
            audit.inspect_model(candidate)
            inspect_submission(audit, candidate, family)
        except (audit.AuditError, UnicodeError) as exc:
            record.update(outcome='rejected_filter', detail=str(exc))
            return record
        record['filter'] = 'accepted'
        try:
            actual, registered = candidate_facts(module, family, candidate, directory, runner)
        except FidelityError as exc:
            record.update(outcome='does_not_compile', detail=str(exc))
            return record
        record['fact_accuracy'] = compare_facts(actual, expected_facts(module, family, material, runner))
        missing = [c['id'] for c in selected if c.get('supported', True) and c['id'] not in registered]
        record['missing_claims'] = missing
        record['registered_claims'] = registered
        grade = module.grade(material, candidate, runner, prove=prove)
        record['rule_fidelity'] = grade['rule_fidelity']
        by_id = {c['id']: c for c in grade['claims']}
        for c in selected:
            graded = dict(by_id[c['id']], kind=c['kind'])
            if not c.get('supported', True):
                graded.update(status='unsupported')
            elif c['id'] in missing:
                graded.update(status='missing', agrees_everywhere=False)
            else:
                graded['status'] = 'graded'
            record['claims'].append(graded)
        supported = [c for c in record['claims'] if c['status'] != 'unsupported']
        record['claim_fidelity'] = ratio(sum(c['agrees_everywhere'] for c in supported), len(supported))
        record['proof'] = grade['proof']
        record['outcome'] = 'missing_claims' if missing else 'graded'
    except Exception as exc:
        # An injected sandbox may raise its own execution exception. Preserve a
        # terminal error record and continue other reviews; never award a score.
        # KeyboardInterrupt/SystemExit intentionally remain interruptible.
        record.update(outcome='grading_error', detail=f'{type(exc).__name__}: {exc}')
    finally:
        dump(directory / 'review.json', record)
    return record


def wilson(matched, total):
    if not total:
        return None
    z = 1.959963984540054
    p, z2 = matched / total, z * z
    center = (p + z2 / (2 * total)) / (1 + z2 / total)
    half = z * math.sqrt(p * (1 - p) / total + z2 / (4 * total * total)) / (1 + z2 / total)
    return [max(0.0, center - half), min(1.0, center + half)]


def aggregate(records):
    result = {'reviews': len(records), 'outcomes': dict(sorted(Counter(r['outcome'] for r in records).items())),
              'unsupported_claims': sum(len(r['unsupported_claims']) for r in records),
              'missing_claims': sum(len(r['missing_claims']) for r in records)}
    for name in METRICS:
        values = [r[name] for r in records]
        matched, total = sum(v['matched'] for v in values), sum(v['total'] for v in values)
        result[name] = {**ratio(matched, total), 'wilson_95': wilson(matched, total),
                        'measured_reviews': sum(v['total'] > 0 for v in values),
                        'unavailable_reviews': sum(v['total'] == 0 for v in values)}
    return result


def summary_markdown(summary):
    lines = ['# Formalization fidelity', '',
             'Scores measure bounded generator fidelity. Unsupported claim kinds are excluded; '
             'unavailable measurements are counted separately. Wilson 95% intervals describe the '
             'pooled checks, which are correlated within dossiers and repeats.', '',
             '| Arm / family / report | Reviews | Fact accuracy | Rule fidelity | Claim fidelity | Unsupported |',
             '| --- | ---: | --- | --- | --- | ---: |']
    def cell(metric):
        if not metric['total']:
            return f'unavailable ({metric["unavailable_reviews"]} reviews)'
        lo, hi = metric['wilson_95']
        return (f'{metric["matched"]}/{metric["total"]} ({metric["rate"]:.2%}; '
                f'95% {lo:.2%}–{hi:.2%}; unavailable {metric["unavailable_reviews"]})')
    for row in summary['cells']:
        label = ' / '.join(str(row[k]) for k in ('arm', 'family', 'variant'))
        lines.append(f'| {label} | {row["reviews"]} | ' + ' | '.join(cell(row[m]) for m in METRICS) +
                     f' | {row["unsupported_claims"]} |')
    lines += ['', 'Outcomes: ' + json.dumps(summary['overall']['outcomes'], sort_keys=True), '',
              'Fact counts include Interface scalar fields, option markers and list lengths, '
              'in the recorded World; synthetic uncertainty domains are excluded. Claim agreement requires every applicable world.', '']
    return '\n'.join(lines)


def score_campaign(campaign, keys, output, runner, *, arms=(ARM,), prove=False):
    require_sandbox(runner)  # Refuse even empty campaigns; never fall back to direct Lean.
    campaign, keys, output = Path(campaign), Path(keys), Path(output)
    if not (campaign / 'units').is_dir():
        raise FidelityError('Campaign has no finished-unit records directory')
    output.mkdir(parents=True, exist_ok=False)
    records, seen = [], set()
    for path in sorted((campaign / 'units').glob('*.json')):
        review = json.loads(read_regular(campaign, path.relative_to(campaign)))
        if review.get('arm') not in arms:
            continue
        identity = (review.get('arm'), review.get('opaque_id'))
        if identity in seen:
            raise FidelityError(f'Duplicate finished review: {identity}')
        seen.add(identity)
        record = score_review(campaign, keys, review, output / 'reviews' / path.stem, runner, prove=prove)
        records.append(record)
    cells = []
    for key in sorted({tuple(str(r.get(k)) for k in ('arm', 'family', 'variant')) for r in records}):
        selected = [r for r in records if tuple(str(r.get(k)) for k in ('arm', 'family', 'variant')) == key]
        cells.append({**dict(zip(('arm', 'family', 'variant'), key)), **aggregate(selected)})
    summary = {'schema_version': 1, 'campaign': str(campaign.resolve()), 'overall': aggregate(records),
               'cells': cells, 'by_arm': {}, 'by_family': {}, 'by_variant': {},
               'records': [str((Path('reviews') / r['unit'] / 'review.json')) for r in records]}
    for dimension in ('arm', 'family', 'variant'):
        for value in sorted({str(r.get(dimension)) for r in records}):
            summary['by_' + dimension][value] = aggregate([r for r in records if str(r.get(dimension)) == value])
    dump(output / 'summary.json', summary)
    (output / 'summary.md').write_text(summary_markdown(summary), encoding='utf-8')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--keys', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New evaluator-only output directory')
    parser.add_argument('--runner', required=True, help='Trusted Python module:callable declaring sandboxed=True')
    parser.add_argument('--arms', default=ARM)
    parser.add_argument('--prove', action='store_true', help='Request bounded rule agreement proof where supported')
    args = parser.parse_args(argv)
    try:
        module, name = args.runner.split(':', 1)
        runner = getattr(importlib.import_module(module), name)
        result = score_campaign(args.campaign, args.keys, args.output, runner,
                                arms=tuple(args.arms.split(',')), prove=args.prove)
    except (FidelityError, OSError, ValueError, AttributeError, ImportError) as exc:
        print(f'Cannot score fidelity: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(result['overall'], indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
