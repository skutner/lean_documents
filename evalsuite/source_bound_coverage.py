#!/usr/bin/env python3
"""Measure source-bound shape and scalar extraction coverage, without proving claims.

The reviewed table below is structural eligibility, conditional on faithful rules,
finite scope and resolved operands. In particular compare covers both exact and
lower-bound sums, and equal can compare a typed rule result or record. This is
not rule registration, claim rendering, proof execution or guard acceptance.

Generator v4 exposes neither selected claims nor all original source values.
A scoped Python return-event observer captures its unmodified builder/build_case
locals. Cases are regenerated in memory; immutable source projections are then
retained under tmp because make_fact/extract_facts require a filesystem manifest.
No keys are used to infer extraction success. Source-leaf selection is an explicit
per-kind adapter below, not a search for the expected value anywhere in a packet.
Report literals, rule semantics, derived computations and scope completeness are
outside this scalar extraction measurement. Unknown underlying values fail even
when a weaker bound might be provable. Documentary missingness labels, by contrast,
can be extracted as enums without pretending to know the missing underlying value.
"""
import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/evidence-to-proof-review/scripts'))
try:
    from . import generate
except ImportError:
    import generate
import audit
import source_bound_contract as c
import source_bound_facts as facts

# Reviewed against generate.py v4 and SPEC section 4, 2026-11-12.
# One entry per kind; None is an intentional exclusion, not an unmapped kind.
KIND_SHAPES = {
    'sample_total': ('sum', 'Sum corrected amounts of the finite nonvoid sample.'),
    'budget_gap': ('difference', 'Subtract budget and corrected sample total in the stated direction.'),
    'largest_payment': ('maximum', 'Select the maximum corrected payment and its amount; generated amounts are distinct.'),
    'sample_missing_approvals': ('count', 'Count sampled rows satisfying the missing-approval predicate.'),
    'sample_missing_approval_amount': ('compare', 'Compare a filtered sum to an exact value or lower bound.'),
    'recorded_approvals': ('count', 'Count rows whose recorded approval label is Present.'),
    'population_missing_approvals': ('count', 'Count missing approvals over a resolved finite population.'),
    'payment_lookup': ('equal', 'Compare the payment payee/date record to the reported record.'),
    'annex_scope': (None, 'Quoted index text cannot establish actual supplied-file membership.'),
    'annex_content': ('equal', 'Compare a resolved annex-content rule result to the reported state.'),
    'reply_deadline': ('date_add', 'Add the applicable period and granted extension to the notice date.'),
    'reply_timing': ('equal', 'Compare a typed timing rule result, including on-time or exact delay.'),
    'rule_version': ('equal', 'Compare the applicable version rule result to the reported version.'),
    'late_replies': ('count', 'Count replies satisfying the applicable late predicate.'),
    'unrecorded_reply': ('equal', 'Compare the recorded reply evidence-state label.'),
    'undecided_extension': ('equal', 'Compare the recorded extension decision label.'),
    'notice_lookup': ('equal', 'Compare the notice issue date to the reported date.'),
    'policy_compliance': ('equal', 'Compare the approval-policy Boolean rule result to the claim.'),
    'approval_tier': ('compare', 'Compare the request amount to the tier threshold.'),
    'policy_failures': ('count', 'Count requests failing the approval predicate.'),
    'signer_role': ('equal', 'Compare the attributed signer role to the reported role.'),
    'illegible_signature': ('equal', 'Compare the signature evidence-state label.'),
    'request_lookup': ('equal', 'Compare requester and amount as a typed record.'),
    'packet_contents': (None, 'Quoted transmittal text cannot establish actual supplied-file membership.'),
    'certificate_result': ('equal', 'Compare the certificate result enum to the reported result.'),
    'service_status': ('equal', 'Compare the service-policy Boolean rule result to the claim.'),
    'unit_location': ('equal', 'Compare the registered location enum to the reported location.'),
    'units_in_service': ('count', 'Count finite registered units satisfying the service predicate.'),
}
NOT_MEASURED = 'not measured'
NAT, DATE, EUR = c.ScalarType('nat'), c.ScalarType('date'), c.ScalarType('money', 'EUR')


def regenerate(family, seed, index):
    """Observe only two return frames; restore any prior profiler even on errors."""
    if generate.GENERATOR_VERSION != 4:
        raise ValueError('Coverage dependency adapter requires reviewed generator version 4')
    captured = {}
    builder = generate.FAMILIES[family]
    previous = sys.getprofile()

    def observe(frame, event, arg):
        if event == 'return':
            if frame.f_code is builder.__code__:
                captured['builder'] = dict(frame.f_locals)
            elif frame.f_code is generate.build_case.__code__:
                captured['reports'] = {v: frame.f_locals[v] for v in ('correct', 'mixed')}
        if previous:
            previous(frame, event, arg)

    sys.setprofile(observe)
    try:
        result = generate.build_case(family, seed, index)
    finally:
        sys.setprofile(previous)
    return result, captured['builder'], captured['reports']


@dataclass(frozen=True)
class Need:
    name: str
    source: str = ''
    anchor: str = ''
    expected: object = None
    type: object = NAT
    row: str | None = None
    column: int | None = None
    pattern: str | None = None
    labels: tuple = ()
    unit: str | None = None
    failure: str | None = None


def requirements(family, item, data):
    """Source leaves needed by each kind; derived values are never quoted facts."""
    kind, text = item['kind'], item['text']
    needs = []

    def cell(name, source, anchor, row, col, expected, type_=None, labels=(), unit=None):
        needs.append(Need(name, source, anchor, expected, type_ or c.ScalarType('enum', 'labels'),
                          row, col, labels=tuple(labels), unit=unit))

    def scalar(name, source, anchor, pattern, expected, type_=NAT):
        needs.append(Need(name, source, anchor, expected, type_, pattern=pattern))

    def fail(name, reason):
        needs.append(Need(name, failure=reason))

    def entity(prefix, rows):
        match = re.search(r'\b' + prefix + r'\d{2}\b', text)
        if not match:
            raise ValueError(f'Missing entity in {kind}: {text}')
        return next(r for r in rows if r['id'] == match[0])

    if kind not in KIND_SHAPES:
        return [Need('kind', failure='unmapped_kind')]
    if kind in {'annex_scope', 'packet_contents'}:
        return [Need('actual supplied-file membership', failure='document_membership_not_scalar')]
    if family == 'ledger':
        rows = data['entries']
        if kind == 'annex_content':
            fail('annex assertion', 'missing_underlying_value' if data['annex'] == 'absent' else 'prose_semantics_not_scalar')
            return needs
        if kind == 'payment_lookup':
            e = entity('P', rows)
            cell('payee', 'ledger.md', 'L-02', e['id'], 1, e['payee'], labels=generate.PAYEES)
            cell('payment date', 'ledger.md', 'L-02', e['id'], 2, e['date'], DATE)
            return needs
        for e in rows:
            cell('row status', 'ledger.md', 'L-02', e['id'], 5,
                 'Voided' if e['voided'] else 'Paid', labels=('Voided', 'Paid'))
            if e['voided']:
                continue
            if kind in {'sample_total', 'budget_gap', 'largest_payment', 'sample_missing_approval_amount'}:
                cell('ledger amount', 'ledger.md', 'L-02', e['id'], 3, e['ledger_amount'], EUR, unit='euro cents')
                if e['amount'] != e['ledger_amount']:
                    cell('corrected amount', 'corrections.md', 'K-01', e['id'], 2, e['amount'], EUR, unit='euro cents')
            if kind in {'sample_missing_approvals', 'sample_missing_approval_amount', 'recorded_approvals', 'population_missing_approvals'}:
                label = {'yes': 'Present', 'no': 'Absent', 'unknown': 'Approval sheet not in packet'}[e['approval']]
                cell('approval state', 'ledger.md', 'L-02', e['id'], 4, label,
                     labels=('Present', 'Absent', 'Approval sheet not in packet'))
                if e['approval'] == 'unknown' and kind != 'recorded_approvals':
                    fail(e['id'] + ' underlying approval', 'missing_underlying_value')
        if kind == 'budget_gap':
            scalar('budget', 'ledger.md', 'L-03', r'exactly (\d+ euro cents)', data['budget'], EUR)
        if kind == 'population_missing_approvals':
            scalar('population size', 'ledger.md', 'L-01', r'exactly (\d+) paid', data['population'])
            if data['population'] > len(data['live_rows']):
                fail('unsampled approvals', 'unsampled_population')
    elif family == 'deadlines':
        rows = data['notices'] if kind == 'late_replies' else [entity('N', data['notices'])]
        decisions = {'granted': 'Granted', 'refused': 'Refused', 'undecided': 'No decision recorded'}
        for n in rows:
            if kind == 'undecided_extension':
                cell('decision', 'extensions.md', 'X-01', n['id'], 2, decisions[n['extension']], labels=decisions.values())
                continue
            if kind == 'unrecorded_reply':
                if n['reply'] is None:
                    cell('reply state', 'register.md', 'G-01', n['id'], 2, 'Not recorded', labels=('Not recorded',))
                else:
                    cell('reply date', 'register.md', 'G-01', n['id'], 2, n['reply'], DATE)
                continue
            cell('notice date', 'register.md', 'G-01', n['id'], 1, n['issued'], DATE)
            if kind == 'notice_lookup':
                continue
            versions = (1, 2) if kind in {'rule_version', 'late_replies'} else (n['version'],)
            for version in versions:
                a = f'R-0{version}'
                scalar('change date', 'policy.md', a, r'(\d{4}-\d{2}-\d{2})', data['change'], DATE)
                scalar('version', 'policy.md', a, r'Version (\d+) of', version)
                if kind != 'rule_version':
                    scalar('period', 'policy.md', a, r'period is (\d+ days)', data['first'] if version == 1 else data['second'])
            if kind == 'rule_version':
                continue
            if n['extension']:
                cell('extension days', 'extensions.md', 'X-01', n['id'], 1, n['days'], NAT)
                cell('decision', 'extensions.md', 'X-01', n['id'], 2, decisions[n['extension']], labels=decisions.values())
                if n['extension'] == 'undecided':
                    fail(n['id'] + ' extension decision', 'missing_underlying_value')
            if kind in {'reply_timing', 'late_replies'}:
                if n['reply'] is None:
                    fail(n['id'] + ' reply date', 'missing_underlying_value')
                else:
                    cell('reply date', 'register.md', 'G-01', n['id'], 2, n['reply'], DATE)
    elif family == 'approvals':
        rows = data['requests'] if kind == 'policy_failures' else [entity('Q', data['requests'])]
        names = (*generate.STAFF, 'None', 'Illegible in the supplied scan')
        for r in rows:
            if kind == 'illegible_signature':
                s = r['signers'][1]
                cell('second signature state', 'requests.md', 'B-01', r['id'], 4, data['shown'](s), labels=names)
                continue
            if kind != 'signer_role':
                cell('amount', 'requests.md', 'B-01', r['id'], 2, r['amount'] * 100, EUR, unit='euro')
            if kind in {'request_lookup', 'policy_compliance', 'policy_failures'}:
                cell('requester', 'requests.md', 'B-01', r['id'], 1, r['requester'], labels=names)
            if kind in {'policy_compliance', 'policy_failures', 'signer_role'}:
                signers = list(enumerate(r['signers'], 3))
                if kind == 'signer_role':
                    person = text.split(', who signed')[0]
                    signers = [(i, s) for i, s in signers if s == person]
                    if not signers:
                        fail('signer attribution', 'missing_underlying_value')
                for col, s in signers:
                    cell('signature', 'requests.md', 'B-01', r['id'], col, data['shown'](s), labels=names)
                    if s == 'illegible':
                        fail(r['id'] + ' signer identity', 'illegible_identity')
                    elif s:
                        cell('signer role', 'staff.md', 'S-01', s, 1, data['roles'][s], labels=set(generate.ROLES))
        if kind in {'policy_compliance', 'policy_failures', 'approval_tier'}:
            for a in ('Q-01', 'Q-02'):
                scalar('tier limit', 'policy.md', a, r'(\d+ euro)', data['limit'] * 100, EUR)
            scalar('lower count', 'policy.md', 'Q-01', r'needs (one)', 1)
            scalar('upper count', 'policy.md', 'Q-02', r'needs (two)', 2)
            if kind != 'approval_tier':
                scalar('distinct people count', 'policy.md', 'Q-02', r'from (two)', 2)
                scalar('finance count', 'policy.md', 'Q-02', r'at least (one)', 1)
    elif family == 'packet':
        rows = data['units'] if kind == 'units_in_service' else [entity('U', data['units'])]
        for u in rows:
            if kind == 'unit_location':
                cell('location', 'register.md', 'E-01', u['id'], 1, u['location'], labels=generate.SITES)
                continue
            cell('certificate association', 'register.md', 'E-01', u['id'], 2, 'Annex ' + u['annex'],
                 labels=tuple('Annex ' + a for a in 'ABCDEFG'))
            source, anchor = f'annex-{u["annex"].lower()}.md', f'C{u["annex"]}-01'
            scalar('certificate result', source, anchor, r'Result: (Pass|Fail)', u['result'], c.ScalarType('enum', 'labels'))
            needs[-1] = Need(**{**needs[-1].__dict__, 'labels': ('Pass', 'Fail')})
            if kind in {'service_status', 'units_in_service'}:
                scalar('test date', source, anchor, r'tested on (\d{4}-\d{2}-\d{2})', u['tested'], DATE)
        if kind in {'service_status', 'units_in_service'}:
            scalar('review date', 'register.md', 'E-02', r'(\d{4}-\d{2}-\d{2})', data['review'], DATE)
            scalar('age limit', 'method.md', 'M-02', r'at most (\d+ days)', data['limit'])
    if not needs:
        raise ValueError(f'No dependency adapter for {family}/{kind}')
    return needs


class Extraction:
    def __init__(self, root, sources):
        self.root, self.sources, self.bindings, self.passages = root, sources, [], {}
        manifest = []
        for name, text in sorted(sources.items()):
            path = 'input/sources/' + name
            dest = root / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding='utf-8')
            passages = audit.paragraphs(text)
            self.passages[name] = passages
            self.bindings.append(c.SourceBinding(path, c.Origin.SOURCE, 'en', frozenset(passages)))
            manifest.append((path, sha256(dest.read_bytes()).hexdigest()))
        (root / 'manifest.md').write_text(generate.table(('Path', 'SHA256'), manifest) + '\n')
        self.bindings = tuple(self.bindings)
        self.cache = {}

    def read(self, need, cites):
        result = {'value': need.name, 'source': need.source, 'anchor': need.anchor,
                  'row': need.row, 'column': need.column, 'expected': plain(need.expected)}
        if need.failure:
            return dict(result, ok=False, reason=need.failure)
        if (need.source, need.anchor) not in cites:
            return dict(result, ok=False, reason='uncited_dependency')
        if need.source not in self.sources:
            return dict(result, ok=False, reason='missing_source')
        if need.anchor not in self.passages[need.source]:
            return dict(result, ok=False, reason='missing_anchor')
        if need in self.cache:
            return self.cache[need].copy()
        passage = self.passages[need.source][need.anchor]
        try:
            if need.row is not None:
                lines = [line for line in passage.splitlines() if line.startswith('|')]
                matches = [line for line in lines[2:] if line.split('|')[1].strip() == need.row]
                if len(matches) != 1:
                    return dict(result, ok=False, reason='missing_or_ambiguous_row')
                quote = matches[0]
                cells = quote.split('|')
                token = cells[need.column + 1].strip()
                # Explicit occurrence in the row, derived from cell position, not token value.
                start = sum(len(cell) + 1 for cell in cells[:need.column + 1])
                start += len(cells[need.column + 1]) - len(cells[need.column + 1].lstrip())
            else:
                matches = list(re.finditer(need.pattern, passage))
                if len(matches) != 1:
                    return dict(result, ok=False, reason='missing_or_ambiguous_token')
                quote, token, start = passage, matches[0][1], matches[0].start(1)
            occurrence = len([m for m in re.finditer('(?=' + re.escape(token) + ')', quote[:start])])
            domains = (c.EnumDomain('labels', 'generator-v4', tuple(sorted(set(need.labels)))),) if need.labels else ()
            context = c.FactsDocument((), (), domains)
            unit_ref = None
            if need.unit:
                header = next(line for line in passage.splitlines() if line.startswith('|'))
                # Use the unit from this column's header, not another amount column.
                header_cell = header.split('|')[need.column + 1].strip()
                unit_domain = c.EnumDomain('unit_label', 'generator-v4', (need.unit,))
                unit_context = c.FactsDocument((), (), (unit_domain,))
                unit_fact = facts.make_fact(self.root, id='unit', type=c.ScalarType('enum', 'unit_label'),
                    source_path='input/sources/' + need.source, anchor=need.anchor,
                    quote=header_cell, selected_token=need.unit, bindings=self.bindings, context=unit_context)
                context = c.FactsDocument((unit_fact,), (), (*domains, unit_domain))
                unit_ref = c.Reference('unit')
            record = facts.make_fact(self.root, id='value', type=need.type,
                source_path='input/sources/' + need.source, anchor=need.anchor, quote=quote,
                selected_token=token, occurrence=occurrence, bindings=self.bindings,
                unit_source=unit_ref, context=context)
            resolved = facts.extract_facts(self.root, c.FactsDocument((*context.facts, record), (), context.enum_domains), self.bindings)
            actual = next(v.value for v in resolved.values if v.id == 'value')
            result.update(ok=actual == need.expected, actual=plain(actual), token=token,
                          quote=quote, quote_start=record.quote_start,
                          selection=[record.selection.start, record.selection.end])
            if not result['ok']:
                result['reason'] = 'value_mismatch'
        except c.ContractError as exc:
            result.update(ok=False, reason='parser_rejection', detail=str(exc))
        self.cache[need] = result
        return result.copy()


def plain(value):
    return value.isoformat() if isinstance(value, date) else value


def measure(seed, dossiers, families, variants, out_root):
    if dossiers < 1 or not families or len(set(families)) != len(families) or any(f not in generate.FAMILIES for f in families):
        raise ValueError('Require positive dossiers and distinct known families')
    if not variants or len(set(variants)) != len(variants) or any(v not in ('correct', 'mixed') for v in variants):
        raise ValueError('Require distinct correct/mixed variants')
    (ROOT / 'tmp').mkdir(exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix='sb-coverage-', dir=ROOT / 'tmp'))
    claims = []
    for index in range(1, dossiers + 1):
        family = families[(index - 1) % len(families)]
        case, data, reports = regenerate(family, seed, index)
        extractor = Extraction(run / case['dossier'], data['sources'])
        for variant in variants:
            for number, item in enumerate(reports[variant], 1):
                mapping = KIND_SHAPES.get(item['kind'])
                values = [extractor.read(n, item['cites']) for n in requirements(family, item, data)]
                claims.append({'dossier': case['dossier'], 'family': family, 'variant': variant,
                    'claim': f'C{number:02d}', 'kind': item['kind'], 'text': item['text'],
                    'shape': mapping[0] if mapping else 'unmapped',
                    'shape_reason': mapping[1] if mapping else 'No reviewed kind mapping',
                    'shape_eligible': bool(mapping and mapping[0]),
                    'extractable': bool(values) and all(v['ok'] for v in values), 'values': values,
                    **{k: NOT_MEASURED for k in ('rule_eligibility', 'execution', 'guard_acceptance')}})
    groups = defaultdict(list)
    for claim in claims:
        groups[(claim['family'], claim['variant'], claim['kind'])].append(claim)
    rows = []
    for (family, variant, kind), members in sorted(groups.items()):
        rows.append(dict(family=family, variant=variant, kind=kind, shape=members[0]['shape'], **counts(members)))
    failures = defaultdict(list)
    for claim in claims:
        for reason in sorted({v['reason'] for v in claim['values'] if not v['ok']}):
            failures[reason].append({k: claim[k] for k in ('dossier', 'variant', 'claim', 'kind')})
    result = {'schema_version': 1, 'generator_version': generate.GENERATOR_VERSION,
        'seed': seed, 'dossiers': dossiers, 'families': families, 'variants': variants,
        'retained_run': str(run.relative_to(ROOT)), 'mapping': KIND_SHAPES,
        'totals': counts(claims), 'rows': rows, 'claims': claims,
        'failure_counts': {k: len(v) for k, v in sorted(failures.items())},
        'failure_examples': {k: v[0] for k, v in sorted(failures.items())}}
    kinds = {q['kind'] for q in claims}
    result['observed_kinds'] = len(kinds)
    result['shape_eligible_kinds'] = sum(bool(KIND_SHAPES.get(k, (None,))[0]) for k in kinds)
    canonical = seed == 511 and dossiers == 40 and families == list(generate.FAMILIES) and set(variants) == {'correct', 'mixed'}
    result['spec_estimate'] = ('reproduced' if (len(claims), result['totals']['shape_eligible'], len(kinds), result['shape_eligible_kinds']) == (676, 605, 28, 26) else 'corrected') if canonical else 'not compared: different enumeration'
    markdown = render(result)
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / 'coverage.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    (out_root / 'coverage.md').write_text(markdown, encoding='utf-8')
    return result, markdown


def counts(claims):
    return {'claims': len(claims), 'shape_eligible': sum(q['shape_eligible'] for q in claims),
            'extractable': sum(q['extractable'] for q in claims),
            'unmapped': sum(q['shape'] == 'unmapped' for q in claims),
            **{k: NOT_MEASURED for k in ('rule_eligibility', 'execution', 'guard_acceptance')},
            'failures': dict(sorted(Counter(reason for q in claims for reason in
                {v['reason'] for v in q['values'] if not v['ok']}).items()))}


def render(result):
    def rate(n, d):
        return f'{n}/{d} ({100 * n / d:.1f}%)' if d else '0/0 (undefined)'
    lines = ['Measured: structural shape eligibility and cited-source scalar extraction agreement; not measured: rule eligibility, execution, guard acceptance.', '',
        'All rates use every claim in the row as denominator, including excluded and unmapped kinds. Extraction is independent of shape eligibility and requires every specified source leaf. It does not establish rule fidelity, derived computations, report-operand binding or scope completeness. Unknown underlying values fail even for potentially provable bounds. Exact enums preserve evidence-state labels; prose is not converted into invented enums.', '',
        f'SPEC estimate: **{result["spec_estimate"]}**; observed {result["shape_eligible_kinds"]}/{result["observed_kinds"]} shape-eligible kinds.', '',
        '| Family | Variant | Claim kind | Shape | Claims | Shape eligible | Extractable | Rule eligibility | Execution | Guard acceptance | Extraction failures (claims, overlapping) |',
        '|---|---|---|---|---:|---:|---:|---|---|---|---|']
    for row in [*result['rows'], dict(family='TOTAL', variant='all', kind='all', shape='—', **result['totals'])]:
        lines.append('| ' + ' | '.join([row['family'], row['variant'], row['kind'], row['shape'] or 'none', str(row['claims']),
            rate(row['shape_eligible'], row['claims']), rate(row['extractable'], row['claims']),
            NOT_MEASURED, NOT_MEASURED, NOT_MEASURED,
            '; '.join(f'{k}: {v}' for k, v in row['failures'].items()) or 'none']) + ' |')
    lines += ['', 'Extraction failures count claims once per reason; a claim can have several reasons. Full per-value evidence and parser errors are in coverage.json.', '',
              f'Retained source projections: `{result["retained_run"]}`.', '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--dossiers', type=int, required=True)
    parser.add_argument('--families', default=','.join(generate.FAMILIES))
    parser.add_argument('--variants', choices=('correct', 'mixed', 'both'), default='both')
    parser.add_argument('--out-root', type=Path, required=True)
    parser.add_argument('--allow-unmapped', action='store_true')
    args = parser.parse_args(argv)
    try:
        result, markdown = measure(args.seed, args.dossiers, args.families.split(','),
                                  ['correct', 'mixed'] if args.variants == 'both' else [args.variants], args.out_root)
    except (ValueError, generate.GenerationError) as exc:
        parser.error(str(exc))
    print(markdown, end='')
    return 1 if result['totals']['unmapped'] and not args.allow_unmapped else 0


if __name__ == '__main__':
    raise SystemExit(main())
