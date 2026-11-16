"""Evaluator-only procurement fidelity, using the generator's original engine/predicates.

Only fidelity_lean/procurement/Interface.lean is safe to give an auditing agent.
The runner is injected; direct execution is for generated programs/authored fixtures.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import datetime as dt
import hashlib
import inspect
import json
from pathlib import Path
import random
import re
import types

from evalsuite import generate
from evalsuite.fidelity import (FidelityError, Regenerated, agreement, parse_vectors,
                               run_checked, verdict)

LEAN_DIR = Path(__file__).with_name('fidelity_lean') / 'procurement'
MAX_RULE_INPUTS, MAX_CLAIM_INPUTS = 2000, 12000
# Full-register rules are quadratic, unlike approvals' single-request rule.
PROOF_LIMIT = 200
ROLES = {'Finance officer': 'finance', 'Manager': 'manager', 'Director': 'director',
         'Analyst': 'analyst', 'Assistant': 'assistant'}
KINDS = {'request_compliance', 'required_tier', 'assessed_amount', 'counting_approvals',
         'failing_requests', 'failing_total', 'tier_three_requests', 'department_failures',
         'department_top', 'failure_comparison', 'request_amount', 'request_signers',
         'unreadable_signature', 'unreadable_amount'}
# None of the current kinds is documentary-only: even the nonformal kinds have a
# decidable recorded-fact or completion predicate. Unknown future kinds fail closed.
UNSUPPORTED = {}
VARIANTS = ('counts_requester_signature', 'ignores_finance_requirement', 'ignores_delegation',
            'applies_delegation_outside_dates', 'ignores_amendment', 'applies_amendment_too_early',
            'treats_limit_as_exclusive', 'uses_superseded_policy', 'uses_wrong_month_rate',
            'ignores_same_week_rule', 'ignores_conversion', 'counts_nonapproving_role')
DAY = dt.timedelta(days=1)


def hard():
    return inspect.getmodule(generate.family_builder('procurement'))


def capture(item):
    return inspect.getclosurevars(item['pred']).nonlocals


def regenerate(key):
    """Local hard-family adapter: core historically indexes only generate.FAMILIES."""
    meta = key.get('generator', {})
    if meta.get('family') != 'procurement':
        raise FidelityError('Procurement emitter requires a procurement key')
    if meta.get('version') != generate.GENERATOR_VERSION:
        raise FidelityError('Generator version mismatch')
    try:
        rebuilt = generate.build_case('procurement', meta['seed'], meta['index'])
        case = generate.family_builder('procurement')(random.Random(
            f'evalsuite:{generate.GENERATOR_VERSION}:{meta["seed"]}:{meta["index"]}:procurement'))
    except (KeyError, TypeError, generate.GenerationError) as exc:
        raise FidelityError(f'Cannot regenerate procurement key: {exc}') from exc
    if rebuilt['key'] != key:
        raise FidelityError('Key differs from regenerated key')
    choices = [c for good, bad in case['slots'] for c in [good, *bad]]
    claims = []
    for row in key['expectations']:
        matches = [c for c in choices if (c['kind'], c['text'], c['error']) ==
                   (row['claim_kind'], row['claim_text'], row['error_type'])]
        if len(matches) != 1:
            raise FidelityError('Ambiguous or missing regenerated procurement predicate')
        if matches[0]['kind'] not in KINDS:
            raise FidelityError(f'Unsupported procurement claim kind: {matches[0]["kind"]}')
        claims.append(matches[0])
    return Regenerated(key, case, claims, {**meta, 'sources': {
        name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
        for name, module in [('evalsuite/generate.py', generate), ('evalsuite/hard_procurement.py', hard())]}})


def rebind(fn, data, memo=None):
    """Retain predicate bytecode; replace captured Data, including nested helpers.

    Several generator predicates close over Data rather than reading w['data'].
    Rebinding makes their original test operate on counterfactual recorded facts.
    No rule or predicate is reimplemented in Python.
    """
    memo = {} if memo is None else memo
    if not isinstance(fn, types.FunctionType) or not fn.__closure__:
        return fn
    if id(fn) in memo:
        return memo[id(fn)]
    def cell(value):
        return (lambda: value).__closure__[0]
    cells = []
    for old in fn.__closure__:
        value = old.cell_contents
        if isinstance(value, hard().Data):
            value = data
        elif isinstance(value, types.FunctionType):
            value = rebind(value, data, memo)
        cells.append(cell(value))
    out = types.FunctionType(fn.__code__, fn.__globals__, fn.__name__, fn.__defaults__, tuple(cells))
    out.__kwdefaults__ = fn.__kwdefaults__
    memo[id(fn)] = out
    return out


def changed(world, *, requests=None, **fields):
    data = world['data']
    attributes = {k: v for k, v in vars(data).items() if k not in ('cache', 'by_id', 'department')}
    attributes.update(fields)
    if requests is not None:
        attributes['requests'] = requests
    return dict(world, data=hard().Data(**attributes))


def rule_inputs(first):
    """Deterministic targeted products; all full registers plus compact boundary cases."""
    d = first['data']
    names = sorted(d.staff)
    requester = names[0]
    fin = next(p for p in names if p != requester and d.staff[p]['role'] == 'Finance officer')
    managers = [p for p in names if p not in (requester, fin) and d.staff[p]['role'] in ('Manager', 'Director')]
    out, labels = [], []
    def add(label, rows, **fields):
        w = changed(first, requests=rows, **fields)
        # Calendar and previous-month coverage for every date, including year rollover.
        rates = dict(w['data'].rates)
        for r in rows:
            for shift in (-1, 0):
                mi = r['date'].year * 12 + r['date'].month - 1 + shift
                for cur, rate in hard().BASE_RATES.items():
                    rates.setdefault((cur, mi // 12, mi % 12 + 1), rate + (71 if shift == 0 else -83))
        w = changed(w, rates=rates)
        out.append(w)
        labels.append(label)
    def row(day=None, amount=1000, currency='EUR', signers=None, **kw):
        return dict(id='probe', date=day or d.first, requester=requester, supplier='Probe supplier',
                    amount=amount, currency=currency, signers=signers if signers is not None else [fin], **kw)
    for swap in (False, True):
        fields = (dict(limits=d.previous, previous=d.limits, exempt_in=d.exempt_out,
                       exempt_out=d.exempt_in, policy_file=d.other_file, other_file=d.policy_file) if swap else {})
        policy = changed(first, **fields)['data']
        for day in sorted({edge + DAY * s for edge in (d.issued, d.effective) for s in (-1, 0, 1)}):
            bounds = {*policy.limits, *policy.amended}
            bounds.update(x for x in (policy.exempt_in, policy.exempt_out) if x is not None)
            for bound in sorted(bounds):
                for amount in (bound - 1, bound, bound + 1):
                    for signs in ([], [managers[0]], managers[:2], [fin, managers[0]]):
                        add('threshold/index/amendment', [row(day, amount, signers=signs)], **fields)
    # Every appointment endpoint (including both open alternatives), on both sides.
    for a in d.delegations:
        ends = [a['first']] + ([a['last']] if a['last'] else a['choices'])
        for day in sorted({edge + DAY * s for edge in ends for s in (-1, 0, 1)}):
            for cur in ('EUR', 'USD', 'GBP', 'CHF'):
                add('appointment/date/currency', [row(day, max(d.limits[1], d.amended[1]) + 100,
                     cur, [a['person'], managers[0]],)])
    # Month ends/starts, leap day, year rollover; pairs straddle each edge.
    dates = [dt.date(d.first.year, m, 1) for m in range(d.first.month, d.end.month + 1)]
    dates += [dt.date(2036, 3, 1), dt.date(2035, 1, 1)]
    for edge in dates:
        for shift in (-1, 0, 1):
            for cur in ('EUR', 'USD', 'GBP', 'CHF'):
                add('month/year/rounding', [row(edge + shift * DAY, 10001, cur)])
    # Same-week grouping requires all three equalities; Sunday/Monday boundary.
    monday = hard().monday(d.effective)
    for offset in range(-1, 8):
        for mismatch in ('none', 'requester', 'supplier'):
            a, b = row(monday, 10001, 'USD'), row(monday + DAY * offset, 10001, 'GBP')
            b['id'] = 'partner'
            if mismatch == 'requester': b['requester'] = names[1]
            if mismatch == 'supplier': b['supplier'] = 'Other supplier'
            add('week/group', [a, b])
    # Same-week rows keep their OWN amendment date and round BEFORE summing.
    if hard().monday(d.effective - DAY) == hard().monday(d.effective):
        for low, high in zip(d.limits, d.amended):
            total = (low + high) // 2
            a, b = row(d.effective - DAY, total // 2), row(d.effective, total - total // 2)
            b['id'] = 'partner'
            add('group/across-amendment', [a, b])
    for cur in ('USD', 'GBP', 'CHF'):
        for amount in (1, 2, 9999, 10000, 10001):
            a, b = row(monday, amount, cur), row(monday + DAY, amount, cur)
            b['id'] = 'partner'
            add('round-before-sum', [a, b])
    for edge in dates:
        if hard().monday(edge - DAY) == hard().monday(edge):
            for cur in ('USD', 'GBP', 'CHF'):
                a, b = row(edge - DAY, 10001, cur), row(edge, 10001, cur)
                b['id'] = 'partner'
                add('group/across-month', [a, b])
    # All staff and acting role categories, requester identity, empty signatures.
    for role in ROLES:
        for acting in ROLES:
            staff = deepcopy(d.staff)
            staff[fin]['role'] = role
            delegation = dict(id='test', person=fin, role=acting, first=d.first,
                              last=d.end, choices=None)
            for self_sign in (False, True):
                r = row(d.first, max(d.limits[1], d.amended[1]) + 1, signers=[fin, managers[0]])
                if self_sign: r['requester'] = fin
                add('roles/requester', [r], staff=staff, delegations=[delegation])
    for exemption in (None, 200, 250, 300):
        for amount in (0, 199, 200, 201, 249, 250, 251, 299, 300, 301):
            add('exemption/category', [row(amount=amount, signers=[])], exempt_in=exemption)
    if len(out) > MAX_RULE_INPUTS:
        raise FidelityError(f'Procurement unseen rule input limit exceeded: {len(out)}')
    return out, labels


def json_world(w):
    d = w['data']
    return {'data': {k: ([(list(key), value) for key, value in sorted(v.items())] if k == 'rates' else v)
                     for k, v in vars(d).items() if k not in ('cache', 'by_id', 'department')},
            'resolutions': {k: v for k, v in w.items() if k != 'data'}}


def string(x): return json.dumps(x, ensure_ascii=False)
def boolean(x): return 'true' if x else 'false'
def lst(xs): return '[' + ', '.join(xs) + ']'
def opt(x, fn=str): return 'none' if x is None else f'(some {fn(x)})'
def ordinal(x): return x.toordinal()
def date(x): return f'⟨{ordinal(x)}, {x.year * 12 + x.month - 1}⟩'


def lean_fields(w):
    d = w['data']
    policies = []
    for file in sorted(hard().POLICIES):
        lim, ex = (d.limits, d.exempt_in) if file == d.policy_file else (d.previous, d.exempt_out)
        policies.append(f'⟨{string(file)}, {ordinal(d.policy_issue[file])}, {lim[0]}, {lim[1]}, {opt(ex)}⟩')
    return {
        'staff': lst(f'⟨{string(p)}, .{ROLES[v["role"]]}, {string(v["dept"])}⟩' for p, v in sorted(d.staff.items())),
        'policies': lst(policies), 'inForce': string(d.policy_file),
        'amendmentIssued': str(ordinal(d.issued)), 'effective': str(ordinal(d.effective)),
        'amendedLow': str(d.amended[0]), 'amendedHigh': str(d.amended[1]),
        'delegations': lst(f'⟨{string(a["id"])}, {string(a["person"])}, .{ROLES[a["role"]]}, '
                           f'{ordinal(a["first"])}, {opt(a["last"], ordinal)}⟩' for a in d.delegations),
        'rates': lst(f'⟨.{c.lower()}, {y * 12 + m - 1}, {v}⟩' for (c, y, m), v in sorted(d.rates.items())),
        'requests': lst(f'⟨{string(r["id"])}, {date(r["date"])}, {string(r["requester"])}, '
                        f'{string(r["supplier"])}, {opt(r["amount"])}, .{r["currency"].lower()}, '
                        f'{lst(opt(p, string) for p in r["signers"])}⟩' for r in d.requests),
        **{field: lst(f'({string(k.split(":", 1)[1])}, {fn(v)})' for k, v in sorted(w.items()) if k.startswith(prefix))
           for field, prefix, fn in [('amounts', 'amount:', str), ('signers', 'signer:', string),
                                      ('lastDays', 'last:', ordinal)]}}


def request_fields(r):
    return {'date': date(r['date']), 'requester': string(r['requester']),
            'supplier': string(r['supplier']), 'amount': opt(r['amount']),
            'currency': '.' + r['currency'].lower(), 'signers': lst(opt(p, string) for p in r['signers'])}


def lean_world(w, base=None):
    fields = lean_fields(w)
    if base is None:
        return '{ ' + ', '.join(f'{k} := {v}' for k, v in fields.items()) + ' }'
    if fields['requests'] != base['requests'] and '_world' in base:
        original = base['_world']['data'].requests
        updated = w['data'].requests
        if [r['id'] for r in original] == [r['id'] for r in updated]:
            groups = {}
            for old, new in zip(original, updated):
                before, after = request_fields(old), request_fields(new)
                delta = ', '.join(f'{k} := {v}' for k, v in after.items() if before[k] != v)
                if delta: groups.setdefault(delta, []).append(new['id'])
            expr = 'r'
            for delta, ids in reversed(list(groups.items())):
                replacement = '{ r with ' + delta + ' }'
                expr = replacement if len(ids) == len(original) else (
                    f'if {lst(string(i) for i in ids)}.contains r.id then {replacement} else {expr}')
            fields['requests'] = f'first.requests.map (fun r => {expr})'
    delta = [f'{k} := {v}' for k, v in fields.items() if v != base[k]]
    return '{ first with ' + ', '.join(delta) + ' }' if delta else 'first'


def claim_body(item, rule, *, wrong=None):
    """Translate captured claim parameters; answers still come from original predicates."""
    c, kind = capture(item), item['kind']
    if kind not in KINDS:
        raise FidelityError(f'Unsupported procurement claim kind: {kind}')
    ident = wrong or c.get('ident')
    rows = f'({rule} w)'
    get = f'({rows}.find? (fun r => r.id == {string(ident)})).any'
    comp = '==' if c.get('exact', True) else '>='
    if kind == 'request_compliance': return f'{get} (fun r => r.complies == {boolean(c["complies"])})'
    fields = {'required_tier': 'tier', 'assessed_amount': 'assessed', 'counting_approvals': 'counting'}
    if kind in fields: return f'{get} (fun r => r.{fields[kind]} {comp} {c["value"]})'
    failed = f'({rows}.filter (fun r => !r.complies))'
    if kind == 'failing_requests': return f'{failed}.length {comp} {c["value"]}'
    if kind == 'failing_total': return f'({failed}.map (fun r => r.converted)).sum {comp} {c["value"]}'
    if kind == 'tier_three_requests': return f'({rows}.filter (fun r => r.tier == 3)).length {comp} {c["value"]}'
    def dept(dep):
        return ('(w.requests.find? (fun q => q.id == r.id)).any (fun q => '
                f'(w.staff.find? (fun s => s.name == q.requester)).any (fun s => s.department == {string(dep)}))')
    if kind == 'department_failures':
        dep = inspect.getclosurevars(c['count']).nonlocals['department']
        return f'({failed}.filter (fun r => {dept(dep)})).length {comp} {c["value"]}'
    if kind == 'department_top':
        return (f'let xs := {failed}.filter (fun r => {dept(c["department"])})\n'
                f'    xs.length >= 3 && xs.any (fun r => r.id == {string(ident)} && r.assessed == {c["amount"]}) && '
                f'xs.all (fun r => r.id == {string(ident)} || r.assessed < {c["amount"]})')
    if kind == 'failure_comparison':
        # The group wording is generated from captured tests; parse only the group
        # parameters, never the conclusion or the expected answer.
        groups = re.fullmatch(r'The number of requests (.+?) that do not comply with the approval policy is '
                             r'(greater|smaller) than the number of requests (.+?) that do not comply with the approval policy\.', item['text'])
        if not groups: raise FidelityError('Unrecognized comparison groups')
        def group(text):
            if text.startswith('of the '): return dept(text.split()[2])
            if text.startswith('paid in '): return f'(w.requests.find? (fun q => q.id == r.id)).any (fun q => q.currency == .{text.split()[-1].lower()})'
            dates = re.fullmatch(r'dated from (\S+) to (\S+)', text)
            if not dates: raise FidelityError('Unrecognized comparison period')
            lo, hi = (ordinal(dt.date.fromisoformat(v)) for v in dates.groups())
            return f'(w.requests.find? (fun q => q.id == r.id)).any (fun q => q.date.ordinal >= {lo} && q.date.ordinal <= {hi})'
        op = '>' if c['relation'] > 0 else '<'
        return f'({failed}.filter (fun r => {group(groups[1])})).length {op} ({failed}.filter (fun r => {group(groups[3])})).length'
    lookup = f'(w.requests.find? (fun r => r.id == {string(ident)})).any'
    if kind == 'request_amount': return f'{lookup} (fun r => r.amount == some {c["amount"]} && r.currency == .{c["currency"].lower()})'
    if kind == 'request_signers':
        signs = lst(f'(some {string(s)})' for s in c['signers'])
        return f'{lookup} (fun r => r.signers.length == {len(c["signers"])} && {signs}.all (fun s => r.signers.count s == 1))'
    field, fn = ('signers', string) if kind == 'unreadable_signature' else ('amounts', str)
    return f'(w.{field}.find? (fun x => x.1 == {string(ident)})).any (fun x => {lst(fn(v) for v in c["shown"])}.contains x.2)'


def claim_inputs(first, item):
    """Targeted full-register perturbations, in addition to ALL completed worlds."""
    d, c = first['data'], capture(item)
    out = []
    names = sorted(d.staff)
    ident = c.get('ident')
    target = next((r for r in d.requests if r['id'] == ident), None)
    def alter(rows): out.append(changed(first, requests=rows))
    # Global extremes protect counts/totals, uniqueness and strict comparisons.
    for amount in (0, 1, max(*d.limits, *d.amended) * 2):
        for signs in ([], names):
            alter([dict(r, amount=amount, signers=signs) for r in d.requests])
    for dep in hard().DEPARTMENTS:
        alter([dict(r, signers=[] if d.staff[r['requester']]['dept'] == dep else names) for r in d.requests])
    for cur in ('EUR', 'USD', 'GBP', 'CHF'):
        alter([dict(r, signers=[] if r['currency'] == cur else names) for r in d.requests])
    # A full-register claim keeps every ID, including predicates closing over data.
    if target:
        values = {0, 1, *d.limits, *d.amended, c.get('amount', 0), c.get('value', 0)}
        values |= {max(0, v + s) for v in list(values) for s in (-1, 1)}
        for amount in sorted(values):
            alter([dict(r, amount=amount) if r['id'] == ident else r for r in d.requests])
        for signs in ([], names, [names[0]], c.get('signers', [])):
            alter([dict(r, signers=signs) if r['id'] == ident else r for r in d.requests])
        for cur in ('EUR', 'USD', 'GBP', 'CHF'):
            alter([dict(r, currency=cur, amount=c.get('amount', r['amount'])) if r['id'] == ident else r for r in d.requests])
    # The original closure reads the resolved alternatives, not a document string.
    if item['kind'] in ('unreadable_signature', 'unreadable_amount'):
        prefix = 'signer:' if item['kind'] == 'unreadable_signature' else 'amount:'
        values = names if prefix == 'signer:' else sorted({0, *c['shown'], *(v + 1 for v in c['shown'])})
        out.extend(dict(first, **{prefix + ident: v}) for v in values)
    # Every comparison-window endpoint, including outside on either side.
    if item['kind'] == 'failure_comparison':
        dates = [dt.date.fromisoformat(s) for s in re.findall(r'\d{4}-\d\d-\d\d', item['text'])]
        for edge in dates:
            for shift in (-1, 0, 1):
                day = edge + shift * DAY
                rates = dict(d.rates)
                for cur, rate in hard().BASE_RATES.items(): rates.setdefault((cur, day.year, day.month), rate)
                out.append(changed(first, requests=[dict(r, date=day) for r in d.requests], rates=rates))
    return out


@dataclass
class Material:
    directory: Path
    truth: dict
    specs: list[str]
    regenerated: Regenerated


def emit(key, directory):
    reg = regenerate(key)
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()): raise FidelityError('Output directory must be new or empty')
    directory.mkdir(parents=True, exist_ok=True)
    sealed = list(generate.worlds(reg.case['base'], reg.case['unknowns']))
    first = sealed[0]
    unseen, labels = rule_inputs(first)
    unseen += sealed
    labels += ['sealed-complete-register'] * len(sealed)
    if len(unseen) > MAX_RULE_INPUTS: raise FidelityError('Procurement rule input limit exceeded')
    baseline = lean_fields(first)
    baseline['_world'] = first
    facts = ['namespace Procurement.Material', f'def first : World := {lean_world(first)}',
             f'def recorded : World := {lean_world(reg.case["base"], baseline)}']
    alternatives = []
    for prefix, fn in [('amount:', str), ('signer:', string), ('last:', ordinal)]:
        alternatives.append(lst(f'({string(k.split(":", 1)[1])}, {lst(str(fn(v)) for v in vs)})'
                                for k, vs in sorted(reg.case['unknowns'].items()) if k.startswith(prefix)))
    facts += [f'def dossier : Dossier := ⟨recorded, {", ".join(alternatives)}⟩',
              'def sealedWorlds : List World := ' + lst(lean_world(w, baseline) for w in sealed),
              'end Procurement.Material']
    (directory / 'Facts.lean').write_text('\n'.join(facts) + '\n')
    inputs = ['namespace Procurement.Material', 'def unseen : List World := ' + lst(lean_world(w, baseline) for w in unseen)]
    truths, specs = [], []
    for i, (row, item) in enumerate(zip(key['expectations'], reg.claims)):
        body = claim_body(item, 'Reference.rule')
        specs.append(body)
        extras = claim_inputs(first, item)
        all_worlds = sealed + extras
        if len(all_worlds) > MAX_CLAIM_INPUTS: raise FidelityError('Procurement claim input limit exceeded')
        values = [bool(rebind(item['pred'], w['data'])(w)) for w in all_worlds]
        original = [bool(item['pred'](w)) for w in sealed]
        if values[:len(sealed)] != original: raise FidelityError('Predicate rebinding changed sealed truth')
        inputs.append(f'def claimWorlds{i} : List World := sealedWorlds ++ ' + lst(lean_world(w, baseline) for w in extras))
        truths.append({'id': f'{row["variant"]}/{row["claim_id"]}', 'kind': item['kind'], 'text': item['text'],
                       'supported': True, 'sealed': original, 'unseen': values[len(sealed):],
                       'input_count': len(values), 'extra_worlds': [json_world(w) for w in extras],
                       'expected_verdict': row['semantic_state']})
    inputs += ['end Procurement.Material']
    (directory / 'Inputs.lean').write_text('\n'.join(inputs) + '\n')
    expected = [hard().assess(w['data'], w).rows for w in unseen]
    truth = {'schema_version': 1, 'classification': 'evaluator_only', 'dossier_id': key['dossier_id'],
             'generator': reg.provenance, 'reference_sha256': hashlib.sha256((LEAN_DIR / 'Reference.lean').read_bytes()).hexdigest(),
             'recorded': json_world(reg.case['base']), 'unknowns': reg.case['unknowns'],
             'sealed_worlds': [json_world(w) for w in sealed], 'rule_inputs': [json_world(w) for w in unseen],
             'rule_labels': labels, 'rule_expected': expected, 'unseen_count': len(unseen),
             'variant_expected': {name: [hard().assess(w['data'], w, frozenset([flag])).rows for w in unseen]
                                  for name, flag in hard().SLIPS},
             'claims': truths, 'coverage': {'graded': len(truths), 'total': len(truths), 'rate': 1.0, 'unsupported': []},
             'bounds': {'rule': MAX_RULE_INPUTS, 'claim': MAX_CLAIM_INPUTS}}
    dump = lambda value: json.dumps(value, indent=2, ensure_ascii=False, default=lambda x: x.isoformat()) + '\n'
    (directory / 'truth.json').write_text(dump(truth))
    (directory / 'key.json').write_text(dump(key))
    return Material(directory, truth, specs, reg)


def program(material, reference=True):
    parts = [(LEAN_DIR / 'Interface.lean').read_text()]
    if reference: parts.append((LEAN_DIR / 'Reference.lean').read_text())
    parts += ['set_option maxRecDepth 1000000\nset_option maxHeartbeats 0']
    parts += [(material.directory / n).read_text() for n in ('Facts.lean', 'Inputs.lean')]
    parts += ['open Procurement Procurement.Material', '''
def emitBits (tag : String) (xs : List Bool) : String :=
  "FIDELITY|" ++ tag ++ "|" ++ String.mk (xs.map (fun b => if b then '1' else '0'))
def emitRows (tag : String) (xs : List (List Assessment)) : String :=
  "ROWS|" ++ tag ++ "|" ++ String.intercalate "/" (xs.map (fun rows =>
    String.intercalate ";" (rows.map (fun r => String.intercalate ","
      [r.id, toString r.converted, toString r.assessed, toString r.tier,
       toString r.counting, if r.finance then "1" else "0", if r.complies then "1" else "0"]))))
''']
    return '\n\n'.join(parts) + '\n'


def parse_rows(output, expected_tags, lengths):
    found = {}
    for line in output.splitlines():
        if not line.startswith('"ROWS|'): continue
        try:
            _, tag, payload = json.loads(line).split('|')
            if tag not in expected_tags or tag in found: raise ValueError('unexpected/duplicate tag')
            worlds = []
            for encoded in payload.split('/'):
                rows = []
                for encoded_row in encoded.split(';'):
                    ident, *fields = encoded_row.split(',')
                    if len(fields) != 6 or any(not s.isdecimal() for s in fields): raise ValueError('bad row')
                    if fields[4] not in ('0', '1') or fields[5] not in ('0', '1'): raise ValueError('bad boolean')
                    rows.append(dict(zip(('id', 'converted', 'assessed', 'tier', 'counting', 'finance', 'complies'),
                                         [ident, *map(int, fields[:4]), fields[4] == '1', fields[5] == '1'])))
                worlds.append(rows)
            if [len(rows) for rows in worlds] != lengths: raise ValueError('incomplete rows')
            found[tag] = worlds
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise FidelityError(f'Malformed procurement result: {exc}') from exc
    if set(found) != set(expected_tags): raise FidelityError('Missing procurement rule vectors')
    return found


def evaluate(material, runner, candidate=None):
    source = program(material, reference=candidate is None)
    rules = ['Reference.rule', *['Reference.' + v for v in VARIANTS]] if candidate is None else ['Candidate.model.rule']
    if candidate is not None: source += Path(candidate).read_text() + '\n'
    for i, rule in enumerate(rules): source += f'#eval emitRows "rule{i}" (unseen.map {rule})\n'
    lengths = {}
    for i, c in enumerate(material.truth['claims']):
        test = f'(fun w => {material.specs[i]})' if candidate is None else f'(Candidate.model.claim {string(c["id"])})'
        source += f'#eval emitBits "claim{i}" (claimWorlds{i}.map {test})\n'
        lengths[f'claim{i}'] = c['input_count']
    path = material.directory / ('ReferenceCheck.lean' if candidate is None else 'CandidateCheck.lean')
    if path.exists(): raise FidelityError('Use a distinct run directory for each reference/candidate execution')
    path.write_text(source)
    result = run_checked(path, runner)
    rows = parse_rows(result.stdout, [f'rule{i}' for i in range(len(rules))],
                      [len(x) for x in material.truth['rule_expected']])
    return {**rows, **parse_vectors(result.stdout, lengths)}, result


def row_agreement(actual, expected):
    if len(actual) != len(expected): raise FidelityError('Unequal rule vectors')
    return agreement([a == e for a, e in zip(actual, expected)], [True] * len(expected))


def validate(material, runner):
    actual, execution = evaluate(material, runner)
    rule = row_agreement(actual['rule0'], material.truth['rule_expected'])
    if rule['rate'] != 1: raise FidelityError('Lean reference disagrees with Python generator rule')
    for i, c in enumerate(material.truth['claims']):
        values = actual[f'claim{i}']
        if values != c['sealed'] + c['unseen']: raise FidelityError(f'Lean reference disagrees with generator predicate: {c["id"]}')
        row = material.regenerated.key['expectations'][i]
        sealed = values[:len(c['sealed'])]
        if (verdict(sealed) != row['semantic_state'] or len(sealed) != row['derivation']['worlds'] or
                sum(sealed) != row['derivation']['worlds_where_true']): raise FidelityError('Key cross-check failed')
    for j, name in enumerate(VARIANTS, 1):
        if actual[f'rule{j}'] != material.truth['variant_expected'][name]:
            raise FidelityError(f'Lean slip disagrees with generator engine: {name}')
    witnesses = {v: [i for i, (a, e) in enumerate(zip(actual[f'rule{j}'], material.truth['rule_expected'])) if a != e]
                 for j, v in enumerate(VARIANTS, 1)}
    missed = [v for v, indices in witnesses.items() if not indices]
    report = {'key_cross_check': True, 'reference_agreement': rule, 'coverage': material.truth['coverage'],
              'test_strength': {'detected': len(VARIANTS) - len(missed), 'total': len(VARIANTS),
                                'rate': (len(VARIANTS) - len(missed)) / len(VARIANTS), 'undetected': missed,
                                'witness_indices': witnesses}, 'seconds': execution.seconds}
    (material.directory / 'reference-result.json').write_text(json.dumps(report, indent=2) + '\n')
    if missed: raise FidelityError(f'Unseen set is too weak: {missed}')
    material._validated = True
    return report


def fixture(material, *, slip=None, wrong_request=False, constant=False):
    if slip is not None and slip not in VARIANTS: raise FidelityError(f'Unknown fixture slip: {slip}')
    source = (LEAN_DIR / 'fixtures' / 'Faithful.lean').read_text()
    if slip: source += (LEAN_DIR / 'fixtures' / 'Slips.lean').read_text()
    source += '\nnamespace Candidate\nopen Procurement\ndef claims (id : String) (' + ('_w' if constant else 'w') + ' : World) : Bool :=\n  match id with\n'
    for c, item in zip(material.truth['claims'], material.regenerated.claims):
        ident = capture(item).get('ident')
        wrong = next((r['id'] for r in material.regenerated.case['base']['data'].requests if r['id'] != ident), None) if wrong_request and ident else None
        body = boolean(c['sealed'][0]) if constant else claim_body(item, slip or 'rule', wrong=wrong)
        source += f'  | {string(c["id"])} => {body}\n'
    source += f'  | _ => false\ndef model : Model := ⟨{slip or "rule"}, claims⟩\nend Candidate\n'
    path = material.directory / 'Fixture.lean'
    path.write_text(source)
    return path


def grade(material, candidate, runner, *, prove=False, proof_limit=PROOF_LIMIT):
    if not getattr(material, '_validated', False): validate(material, runner)
    actual, execution = evaluate(material, runner, candidate)
    scored = []
    for i, c in enumerate(material.truth['claims']):
        score = agreement(actual[f'claim{i}'], c['sealed'] + c['unseen'])
        scored.append({'id': c['id'], 'agrees_everywhere': score['rate'] == 1, **score})
    correct = sum(c['agrees_everywhere'] for c in scored)
    report = {'rule_fidelity': row_agreement(actual['rule0'], material.truth['rule_expected']),
              'claim_fidelity': {'matched': correct, 'total': len(scored), 'rate': correct / len(scored)},
              'claims': scored, 'coverage': material.truth['coverage'], 'seconds': execution.seconds,
              'candidate_sha256': hashlib.sha256(Path(candidate).read_bytes()).hexdigest(),
              'proof': {'status': 'not_requested'}}
    if prove:
        if report['rule_fidelity']['rate'] != 1:
            report['proof'] = {'status': 'not_equal', 'reason': 'rule counterexample observed'}
        elif material.truth['unseen_count'] > proof_limit:
            report['proof'] = {'status': 'skipped', 'reason':
                f'{material.truth["unseen_count"]} complete-register inputs exceed proof_limit={proof_limit}; '
                'the rule sums same-week peers and resolves roles for every row. Kernel reduction of the '
                'full list is deliberately bounded separately from executable differential checks.'}
        else:
            source = program(material) + Path(candidate).read_text() + '''
theorem candidate_rule_agrees :
  unseen.all (fun w => Candidate.model.rule w == Reference.rule w) = true := by decide
#print axioms candidate_rule_agrees
'''
            path = material.directory / 'Agreement.lean'
            path.write_text(source)
            result = run_checked(path, runner)
            marker = "'candidate_rule_agrees' does not depend on any axioms"
            if marker not in result.stdout: raise FidelityError('Missing axiom-free agreement proof')
            report['proof'] = {'status': 'kernel_checked', 'axiom_report': marker, 'seconds': result.seconds}
    (material.directory / 'grade.json').write_text(json.dumps(report, indent=2) + '\n')
    return report
