"""Evaluator-only casework references and bounded fidelity measurements.

Only the static Interface.lean is agent-facing. Python truth is obtained by replaying
and rebinding the original generator predicates to its own rebuilt Model, never by
implementing a second Python rule or claim engine. Direct runners are fixture-only.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import datetime as dt
import dis
import hashlib
import inspect
import itertools
import json
from pathlib import Path
import random
import re
from types import FunctionType

from evalsuite import generate
from evalsuite.fidelity import (FidelityError, LeanRunner, Regenerated, agreement,
                               parse_vectors, run_checked, verdict)

LEAN_DIR = Path(__file__).with_name('fidelity_lean') / 'casework'
MAX_RULE_INPUTS = 2000
MAX_CLAIM_INPUTS = 12000
PROOF_LIMIT = 2000
KINDS = frozenset(('deadline_date', 'reply_timing', 'days_late', 'late_replies',
                  'shifted_deadlines', 'extensions_in_effect', 'extension_effect',
                  'deadline_extreme', 'notice_lookup', 'receipt_date', 'late_replies_by_post'))
VARIANTS = ('extends_urgent_notice', 'counts_pending_extension', 'counts_refused_extension',
            'counts_void_extension', 'applies_amendment_too_early', 'ignores_holiday',
            'ignores_shift_after_extension', 'counts_calendar_days_for_post', 'uses_draft_register',
            'takes_neighbouring_row', 'wrong_class_period', 'counts_issue_day', 'ignores_postal_rule',
            'ignores_amendment', 'ignores_extension', 'late_on_deadline_day',
            'counts_deadline_day_in_days_late', 'ignores_nonworking_shift')
DAY = dt.timedelta(days=1)


def capture(fn):
    return inspect.getclosurevars(fn).nonlocals


def rebind(fn, replacements):
    """Keep the original predicate bytecode, substituting only captured facts/model."""
    def cell(value):
        return (lambda: value).__closure__[0]
    return FunctionType(fn.__code__, fn.__globals__, fn.__name__, fn.__defaults__,
                        tuple(cell(replacements.get(name, old.cell_contents))
                              for name, old in zip(fn.__code__.co_freevars, fn.__closure__ or ())))


def family_module():
    return inspect.getmodule(generate.family_builder('casework'))


def regenerate_casework(key):
    """Local hard-family adapter; core regenerate originally indexed FAMILIES only."""
    meta = key.get('generator', {})
    if meta.get('family') != 'casework':
        raise FidelityError('Casework emitter requires a casework key')
    if meta.get('version') != generate.GENERATOR_VERSION:
        raise FidelityError('Generator version mismatch')
    try:
        rebuilt = generate.build_case('casework', meta['seed'], meta['index'])
    except (KeyError, TypeError, generate.GenerationError) as exc:
        raise FidelityError(f'Cannot regenerate casework key: {exc}') from exc
    if rebuilt['key'] != key:
        raise FidelityError('Key differs from regenerated key')
    hard = family_module()
    if set(VARIANTS) != set(hard.RULE_OF) | {'uses_draft_register', 'takes_neighbouring_row'}:
        raise FidelityError('Generator slip definitions changed')
    rng = random.Random(f'evalsuite:{generate.GENERATOR_VERSION}:{meta["seed"]}:{meta["index"]}:casework')
    for _ in range(60):
        try:
            dossier = hard.assemble(rng)
            base, unknowns, slots = hard.claims_of(dossier, rng)
            break
        except hard.Retry:
            continue
    else:
        raise FidelityError('Cannot replay casework assembly')
    sources = hard.render(dossier)
    if any(rebuilt['files']['sources/' + name] != text for name, text in sources.items()):
        raise FidelityError('Casework assembly replay differs from source documents')
    choices = [c for good, errors in slots for c in [good, *errors]]
    claims = []
    for row in key['expectations']:
        matches = [c for c in choices if (c['text'], c['kind'], c['error']) ==
                   (row['claim_text'], row['claim_kind'], row['error_type'])]
        if len(matches) != 1:
            raise FidelityError(f'Ambiguous or missing predicate: {row["claim_id"]}')
        if matches[0]['kind'] not in KINDS:
            raise FidelityError(f'Unsupported casework claim kind: {matches[0]["kind"]}')
        claims.append(matches[0])
    provenance = dict(meta, source_sha256=hashlib.sha256(Path(hard.__file__).read_bytes()).hexdigest(),
                      source_file='evalsuite/hard_casework.py',
                      generate_sha256=hashlib.sha256(Path(generate.__file__).read_bytes()).hexdigest())
    return Regenerated(key, dict(base=base, unknowns=unknowns, slots=slots, sources=sources), claims, provenance), dossier


def recorded_world(d):
    rows = [None, None]
    rows[0 if d.register[0] == 'register-a.md' else 1] = deepcopy(d.notices)
    rows[1 if d.register[0] == 'register-a.md' else 0] = [deepcopy(d.draft[n['id']]) for n in d.notices]
    return dict(periods=dict(d.rules.periods), amended=dict(d.rules.amended), effective=d.rules.effective,
                holidays=sorted(d.rules.holidays), registers=rows,
                in_force=0 if d.register[0] == 'register-a.md' else 1,
                extensions=deepcopy(d.extensions), resolutions={}, neighbour=dict(d.neighbour))


def selected(w):
    return w['registers'][w['in_force']]


def engine(w):
    hard = family_module()
    return hard.Rules(w['periods'], w['amended'], w['effective'], w['holidays'])


def resolved_rows(w):
    rows = [dict(n) for n in selected(w)]
    exts = [dict(e) for e in w['extensions']]
    for n in rows:
        n.pop('sent_values', None)
        if n['sent'] is None and 'sent:' + n['id'] in w['resolutions']:
            n['sent'] = w['resolutions']['sent:' + n['id']]
    for e in exts:
        e.pop('date_values', None)
        if e['date'] is None and 'decided:' + e['ref'] in w['resolutions']:
            e['date'] = w['resolutions']['decided:' + e['ref']]
    return rows, exts


def oracle_rule(w, ident, slip=None):
    """Call the generator's evaluate; swaps change inputs only, never calculations."""
    hard = family_module()
    rows, exts = resolved_rows(w)
    if slip == 'takes_neighbouring_row':
        ident = w['neighbour'].get(ident, ident)
        slip = None
    n = next(n for n in rows if n['id'] == ident)
    if slip == 'uses_draft_register':
        n = deepcopy(next(n for n in w['registers'][1 - w['in_force']] if n['id'] == ident))
        if n['sent'] is None and 'sent:' + ident in w['resolutions']:
            n['sent'] = w['resolutions']['sent:' + ident]
        slip = None
    ext = next((e for e in exts if e['nid'] == ident), None)
    return hard.evaluate(engine(w), n, ext, slip)


def model_for(w):
    hard = family_module()
    rows, exts = resolved_rows(w)
    return hard.Model(engine(w), rows, exts, {n['id']: n for n in rows}, w['neighbour'])


def rebound_predicate(item, original_model, model):
    c = capture(item['pred'])
    replacements = {'model': model, 'by_id': model.by_id}
    if 'counter' in c:
        fn = capture(c['counter'])['fn']
        members = ([n['id'] for n in model.notices if n['channel'] == 'Post']
                   if item['kind'] == 'late_replies_by_post' else None)
        replacements['counter'] = model.tally(fn, members)
    if 'group' in c:
        cls = original_model.by_id[c['group'][0]]['cls']
        replacements['group'] = [n['id'] for n in model.notices if n['cls'] == cls]
    return rebind(item['pred'], replacements)


def exact(item):
    comparisons = [op.argval for op in dis.get_instructions(item['pred']) if op.opname == 'COMPARE_OP']
    if comparisons not in (['=='], ['>=']):
        raise FidelityError(f'Unexpected predicate comparison layout: {item["kind"]}')
    return comparisons == ['==']


def claim_spec(item, model):
    c, kind = capture(item['pred']), item['kind']
    target = c.get('ident', c.get('p2'))
    q = string(target) if target else ''
    if kind == 'deadline_date':
        spec = f'.deadlineDate {q} {ordinal(c["value"])} {boolean(exact(item))}'
    elif kind == 'receipt_date':
        spec = f'.receiptDate {q} {ordinal(c["date"])} {boolean(exact(item))}'
    elif kind == 'reply_timing':
        spec = f'.replyTiming {q} {boolean(c["late"])}'
    elif kind == 'days_late':
        spec = f'.daysLate {q} {c["days"]}'
    elif kind == 'extension_effect':
        spec = f'.extensionEffect {q} {boolean(c["flag"])}'
    elif kind == 'notice_lookup':
        spec = f'.noticeLookup {q} {ordinal(c["date"])}'
    elif kind == 'deadline_extreme':
        cls = model.by_id[c['group'][0]]['cls']
        spec = f'.deadlineExtreme .{cls} {boolean(c["pick"] is max)} {q} {ordinal(c["date"])}'
    else:
        name = {'late_replies': 'lateReplies', 'shifted_deadlines': 'shiftedDeadlines',
                'extensions_in_effect': 'extensionsInEffect', 'late_replies_by_post': 'lateRepliesByPost'}[kind]
        spec = f'.{name} {c["value"]} {boolean(exact(item))}'
    return spec, target


def ordinal(value):
    return value.toordinal()


def string(value):
    return json.dumps(value, ensure_ascii=False)


def boolean(value):
    return 'true' if value else 'false'


def lean_list(values):
    return '[' + ', '.join(values) + ']'


def option(value, emit=str):
    return 'none' if value is None else f'(some {emit(value)})'


def lean_notice(n):
    return (f'⟨{string(n["id"])}, {ordinal(n["issued"])}, .{n["cls"]}, {string(n["addressee"])}, '
            f'.{n["channel"].lower()}, {option(n["sent"], ordinal)}, {option(n["ext"], string)}⟩')


def lean_extension(e):
    return (f'⟨{string(e["ref"])}, {string(e["nid"])}, {e["days"]}, .{e["decision"]}, '
            f'{option(e["date"], ordinal)}⟩')


def lean_pairs(mapping, left=string, right=string):
    return lean_list(f'({left(k)}, {right(v)})' for k, v in sorted(mapping.items()))


def lean_world(w):
    return ('⟨' + ', '.join([
        lean_pairs(w['periods'], lambda x: '.' + x, str),
        lean_pairs(w['amended'], lambda x: '.' + x, str), str(ordinal(w['effective'])),
        lean_list(str(ordinal(d)) for d in w['holidays']),
        lean_list(lean_list(lean_notice(n) for n in rows) for rows in w['registers']),
        str(w['in_force']), lean_list(lean_extension(e) for e in w['extensions']),
        lean_pairs(w['resolutions'], right=lambda d: str(ordinal(d))), lean_pairs(w['neighbour'])]) + '⟩')


def serial(value):
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serial(v) for v in value]
    return value


def result_values(result):
    return [ordinal(result.deadline), ordinal(result.basic), int(result.took), int(result.shifted),
            ordinal(result.received), int(result.late), result.days]


def unseen_inputs(first):
    """Deterministic targeted boundaries, not a full product of all independent facts."""
    hard = family_module()
    inputs = []
    def add(w, ident='probe', label=''):
        inputs.append((w, ident, label))
    def small(cls='standard', issued=None, channel='Electronic', holidays=None, amended=None):
        issued = issued or first['effective']
        n = dict(id='probe', issued=issued, cls=cls, addressee='Probe', channel=channel,
                 sent=issued + 100 * DAY, ext=None)
        other = dict(n, id='neighbour', issued=issued - 9 * DAY)
        w = dict(periods=dict(first['periods']), amended=dict(first['amended'] if amended is None else amended),
                 effective=first['effective'], holidays=list(first['holidays'] if holidays is None else holidays),
                 registers=[[n, other], [dict(n, issued=issued - DAY), dict(other)]], in_force=0,
                 extensions=[], resolutions={}, neighbour={'probe': 'neighbour', 'neighbour': 'probe'})
        return w
    # Actual rows supplement synthetic probes, retaining all dossier-specific values.
    for n in selected(first):
        add(first, n['id'], 'recorded-row')
    for cls in hard.CLASSES:
        for amendment in (False, True):
            amendments = {cls: first['periods'][cls] + 4} if amendment else {}
            for offset in (-1, 0, 1):
                for origin in (first['effective'], first['effective'] - first['periods'][cls] * DAY):
                    add(small(cls, origin + offset * DAY, amended=amendments), label='amendment-boundary')
    # Every weekday and holiday choice at the last day; receipt on both sides of deadline.
    monday = first['effective'] + DAY * ((-first['effective'].weekday()) % 7) + 21 * DAY
    for cls, weekday, holiday, channel, delta in itertools.product(
            hard.CLASSES, range(7), (False, True), hard.CHANNELS, (-1, 0, 1)):
        last = monday + weekday * DAY
        period = first['amended'].get(cls, first['periods'][cls])
        issued = last - period * DAY
        # Keep the selected issue beyond the effective boundary for this calendar grid.
        last += 100 * 7 * DAY
        issued += 100 * 7 * DAY
        w = small(cls, issued, channel, [last] if holiday else [])
        deadline = oracle_rule(w, 'probe').deadline
        receipt = deadline + delta * DAY
        sent = receipt
        if channel == 'Post':
            # Adjacent working receipt dates, including the deadline itself.
            r = engine(w)
            receipt = r.working_before(deadline) if delta < 0 else r.working_after(deadline, 1) if delta > 0 else deadline
            sent = r.working_before(r.working_before(receipt))
        w['registers'][0][0]['sent'] = sent
        add(w, label='deadline-weekday-holiday-receipt')
    # All status/class choices, decision -1/0/+1, requested durations and shifted extension endings.
    for cls, status, delta, days, holiday in itertools.product(
            hard.CLASSES, ('granted', 'refused', 'pending'), (-1, 0, 1), range(1, 8), (False, True)):
        w = small(cls, first['effective'] + 700 * DAY, holidays=[])
        basic = oracle_rule(w, 'probe').basic
        if holiday:
            w['holidays'] = [basic + days * DAY]
            basic = oracle_rule(w, 'probe').basic
        ext = dict(ref='probe-ext', nid='probe', days=days, decision=status,
                   date=None if status == 'pending' else basic + delta * DAY)
        w['extensions'] = [ext]
        w['registers'][0][0]['ext'] = ext['ref']
        add(w, label='extension-boundary')
    # Postal counting starts after posting: every weekday; holiday at either counted day.
    for weekday, holiday_offset, channel in itertools.product(range(7), (0, 1, 2, 3, 4), hard.CHANNELS):
        sent = monday + weekday * DAY
        w = small(channel=channel, holidays=[] if holiday_offset == 0 else [sent + holiday_offset * DAY])
        w['registers'][0][0]['sent'] = sent
        add(w, label='postal-boundary')
    # Index choice is data, independent of physical issue order.
    for in_force in (0, 1):
        w = small()
        w['in_force'] = in_force
        add(w, label='index-choice')
    if len(inputs) > MAX_RULE_INPUTS:
        raise FidelityError('Casework unseen rule input limit exceeded')
    return inputs


def replacement_world(first, probe, target):
    """Install the synthetic probe at a report's target, preserving its row ID."""
    w = dict(first, resolutions=dict(first['resolutions']))
    w['registers'] = [list(rows) for rows in first['registers']]
    n = dict(next(n for n in selected(probe) if n['id'] == 'probe'))
    n['id'] = target
    n['ext'] = 'counterfactual-ext' if probe['extensions'] else None
    for rows in w['registers']:
        rows[:] = [dict(n) if old['id'] == target else old for old in rows]
    w['extensions'] = [e for e in w['extensions'] if e['nid'] != target]
    if probe['extensions']:
        w['extensions'].append(dict(probe['extensions'][0], nid=target, ref='counterfactual-ext'))
    w['resolutions'].pop('sent:' + target, None)
    for k in ('periods', 'amended', 'effective', 'holidays'):
        w[k] = deepcopy(probe[k])
    return w


def count_world(first, mode, passing):
    """Attain every aggregate count, using ordinary well-formed completed facts."""
    w = deepcopy(first)
    w.update(amended={}, holidays=[], extensions=[])
    duration = w['periods']['standard']
    monday = w['effective'] + DAY * ((-w['effective'].weekday()) % 7) + 700 * DAY
    for i, n in enumerate(selected(w)):
        n.pop('sent_values', None)
        n['cls'] = 'standard'
        n['issued'] = monday - duration * DAY + (5 * DAY if mode == 1 and i < passing else dt.timedelta())
        n['channel'] = 'Post' if mode == 3 else 'Electronic'
        n['sent'] = monday + (100 if mode in (0, 3) and i < passing else -10) * DAY
        n['ext'] = 'count-' + n['id'] if mode == 2 and i < passing else None
        if n['ext']:
            w['extensions'].append(dict(ref=n['ext'], nid=n['id'], days=7, decision='granted', date=n['issued']))
    return w


def threshold_worlds(first, item, original_model):
    """Stated-value neighbours plus extreme winners/ties, beyond rule boundaries."""
    c, kind = capture(item['pred']), item['kind']
    target = c.get('ident', c.get('p2'))
    result = []
    for delta in (-1, 0, 1):
        w = deepcopy(first)
        n = next(n for n in selected(w) if n['id'] == target)
        if kind == 'notice_lookup':
            n['issued'] = c['date'] + delta * DAY
        elif kind == 'days_late':
            n['channel'] = 'Electronic'
            n['sent'] = oracle_rule(w, target).deadline + (c['days'] + delta) * DAY
        elif kind in ('deadline_date', 'receipt_date'):
            wanted = c.get('date', c.get('value')) + delta * DAY
            if kind == 'deadline_date':
                w['amended'], w['holidays'] = {}, []
                w['extensions'] = [e for e in w['extensions'] if e['nid'] != target]
                n['ext'] = None
                n['issued'] = wanted - w['periods'][n['cls']] * DAY
            else:
                n['channel'], n['sent'] = 'Electronic', wanted
        elif kind == 'deadline_extreme':
            w['amended'], w['holidays'], w['extensions'] = {}, [], []
            cls = original_model.by_id[c['group'][0]]['cls']
            for row in selected(w):
                row['ext'] = None
                if row['cls'] == cls:
                    wanted = c['date'] + (delta if row['id'] == target else (-14 if c['pick'] is max else 14)) * DAY
                    row['issued'] = wanted - w['periods'][cls] * DAY
        else:
            continue
        result.append(w)
    if kind == 'deadline_extreme':
        # Equal deadlines are not a unique maximum/minimum.
        w = deepcopy(result[1])
        cls = original_model.by_id[c['group'][0]]['cls']
        other = next(n for n in selected(w) if n['cls'] == cls and n['id'] != target)
        other['issued'] = next(n['issued'] for n in selected(w) if n['id'] == target)
        result.append(w)
    return result


@dataclass
class Material:
    directory: Path
    truth: dict
    specs: list[str]
    regenerated: Regenerated


def emit(key, directory):
    reg, d = regenerate_casework(key)
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise FidelityError('Output directory must be new or empty')
    directory.mkdir(parents=True, exist_ok=True)
    recorded = recorded_world(d)
    sealed = [dict(recorded, resolutions={k: v for k, v in w.items() if k != 'notices'})
              for w in generate.worlds(reg.case['base'], reg.case['unknowns'])]
    first = sealed[0]
    inputs = unseen_inputs(first)
    specs, truths = [], []
    facts = ['namespace Casework.Material', f'def recorded : World := {lean_world(recorded)}',
             'def dossier : Dossier := ⟨recorded, ' + lean_pairs(reg.case['unknowns'], right=lambda dates:
                 lean_list(str(ordinal(v)) for v in dates)) + '⟩',
             'def sealedWorlds : List World := ' + lean_list(
                 '{ recorded with resolutions := ' + lean_pairs(w['resolutions'], right=lambda v: str(ordinal(v))) + ' }'
                 for w in sealed), 'def first : World := sealedWorlds.head!', 'end Casework.Material']
    (directory / 'Facts.lean').write_text('\n'.join(facts) + '\n')
    source = ['namespace Casework.Material']
    # Separate declarations keep elaborator recursion bounded and allow compact references in claims.
    for i, (w, ident, _) in enumerate(inputs):
        source.append(f'def input{i} : World := ' + ('first' if w is first else lean_world(w)))
    source.append('def unseen : List (World × String) := ' + lean_list(
        f'(input{i}, {string(ident)})' for i, (_, ident, _) in enumerate(inputs)))
    source += ['def replace (w p : World) (id : String) : World :=',
               '  let row := (p.registers[p.inForce]!).find? (fun n => n.id == "probe")',
               '  match row with', '  | none => w', '  | some n =>',
               '    let r := { n with id := id, extension := if p.extensions.isEmpty then none else some "counterfactual-ext" }',
               '    { w with',
               '      periods := p.periods, amended := p.amended, effective := p.effective, holidays := p.holidays,',
               '      registers := w.registers.map (fun rows => rows.map (fun old => if old.id == id then r else old)),',
               '      extensions := (w.extensions.filter (fun e => e.nid != id)) ++',
               '        p.extensions.map (fun e => { e with nid := id, ref := "counterfactual-ext" }),',
               '      resolutions := w.resolutions.filter (fun x => x.1 != "sent:" ++ id) }']
    count_monday = first['effective'] + DAY * ((-first['effective'].weekday()) % 7) + 700 * DAY
    count_duration = first['periods']['standard']
    source += ['def countWorld (mode passing : Nat) : World :=',
               f'  let monday : Nat := {ordinal(count_monday)}',
               f'  let duration : Nat := {count_duration}',
               '  let rows := (first.registers[first.inForce]!).zipIdx.map (fun p =>',
               '    { p.1 with',
               '      cls := .standard,',
               '      issued := monday - duration + (if mode == 1 && p.2 < passing then 5 else 0),',
               '      channel := if mode == 3 then .post else .electronic,',
               '      sent := some (if (mode == 0 || mode == 3) && p.2 < passing then monday + 100 else monday - 10),',
               '      extension := if mode == 2 && p.2 < passing then some ("count-" ++ p.1.id) else none })',
               '  { first with',
               '    amended := [], holidays := [],',
               '    registers := first.registers.zipIdx.map (fun p => if p.2 == first.inForce then rows else p.1),',
               '    extensions := rows.filterMap (fun n => n.extension.map (fun ref =>',
               '      ⟨ref, n.id, 7, .granted, some n.issued⟩)) }']
    # Global counterfactuals vary each input-sensitive quantity in every row together.
    global_worlds = []
    for mode in ('early', 'late', 'shifted', 'unshifted', 'extensions', 'no_extensions', 'index'):
        w = deepcopy(first)
        if mode == 'index':
            w['in_force'] = 1 - w['in_force']
        else:
            w['holidays'] = []
            w['extensions'] = []
            for n in selected(w):
                n.pop('sent_values', None)
                n['channel'] = 'Post' if mode == 'late' else 'Electronic'
                n['issued'] = first['effective'] + 700 * DAY
                n['ext'] = None
                n['sent'] = n['issued'] + (200 if mode == 'late' else 1) * DAY
                if mode in ('shifted', 'unshifted'):
                    # Choose a raw last day of Saturday/Monday with the generator's period.
                    _, period = engine(w).period(n)
                    last = n['issued'] + period * DAY
                    n['issued'] += ((5 if mode == 'shifted' else 0) - last.weekday()) % 7 * DAY
                if mode == 'extensions':
                    n['ext'] = 'cf-' + n['id']
                    w['extensions'].append(dict(ref=n['ext'], nid=n['id'], days=7, decision='granted', date=n['issued']))
        global_worlds.append(w)
    for j, w in enumerate(global_worlds):
        source.append(f'def global{j} : World := {lean_world(w)}')
    # Share compact global worlds; Python snapshots rebuilt exactly once per world.
    globals_models = [model_for(w) for w in global_worlds]
    for i, (row, item) in enumerate(zip(key['expectations'], reg.claims)):
        spec, target = claim_spec(item, d.model)
        specs.append(spec)
        applicable, expressions, recipes = [], ['sealedWorlds'], []
        if target:
            # Every rule probe applies to the named item; other rows remain present.
            for j, (probe, ident, _) in enumerate(inputs):
                if ident != 'probe':
                    continue
                w = replacement_world(first, probe, target)
                applicable.append(w)
                # Lean emits the same ordered filtered map below.
                recipes.append(j)
            expressions.append(f'(unseen.filter (fun x => x.2 == \"probe\")).map (fun x => replace first x.1 {string(target)})')
            for w in threshold_worlds(first, item, d.model):
                applicable.append(w)
                expressions.append('[' + lean_world(w) + ']')
        else:
            mode = {'late_replies': 0, 'shifted_deadlines': 1, 'extensions_in_effect': 2,
                    'late_replies_by_post': 3}[item['kind']]
            for passing in range(len(selected(first)) + 1):
                applicable.append(count_world(first, mode, passing))
            expressions.append(f'(List.range {len(selected(first)) + 1}).map (countWorld {mode})')
        expressions += [f'[global{j}]' for j in range(len(global_worlds))]
        all_count = len(sealed) + len(applicable) + len(global_worlds)
        if all_count > MAX_CLAIM_INPUTS:
            raise FidelityError('Casework claim input limit exceeded')
        # Original sealed predicate; no adapter involved in the key cross-check.
        sealed_truth = [bool(item['pred'](dict(reg.case['base'], **w['resolutions']))) for w in sealed]
        extra_truth = [bool(rebound_predicate(item, d.model, model_for(w))({})) for w in applicable]
        extra_truth += [bool(rebound_predicate(item, d.model, m)({})) for m in globals_models]
        source.append(f'def claimWorlds{i} : List World := ' + ' ++ '.join(expressions))
        truths.append(dict(id=f'{row["variant"]}/{row["claim_id"]}', kind=item['kind'], text=item['text'],
                           supported=True, spec=spec, sealed=sealed_truth, unseen=extra_truth,
                           input_count=all_count, input_recipe=dict(target=target, rule_indices=recipes,
                           extra_count=len(applicable)-len(recipes), global_modes=['early', 'late', 'shifted',
                           'unshifted', 'extensions', 'no_extensions', 'index']), expected_verdict=row['semantic_state']))
    source += ['end Casework.Material', '']
    (directory / 'Inputs.lean').write_text('\n'.join(source))
    truth = dict(schema_version=1, classification='evaluator_only', dossier_id=key['dossier_id'],
                 generator=reg.provenance, reference_sha256=hashlib.sha256((LEAN_DIR/'Reference.lean').read_bytes()).hexdigest(),
                 recorded=serial(recorded), unknowns=serial(reg.case['unknowns']), sealed_worlds=serial(sealed),
                 rule_inputs=[dict(world=serial(w), target=ident, boundary=label) for w, ident, label in inputs],
                 rule_expected=[result_values(oracle_rule(w, ident)) for w, ident, _ in inputs],
                 variant_expected={v: [result_values(oracle_rule(w, ident, v)) for w, ident, _ in inputs] for v in VARIANTS},
                 unseen_count=len(inputs), bounds=dict(rule=MAX_RULE_INPUTS, claim=MAX_CLAIM_INPUTS),
                 claims=truths, coverage=dict(graded=len(truths), total=len(truths), rate=1.0, unsupported=[]),
                 limits=['Bounded tests, not universal equivalence.',
                         'Counterfactual predicates rebind the generator model, tally and class/post membership.',
                         'Neighbour slip is identity on rows without a designated neighbour; generator only applies that swap where defined.',
                         'Lookup is a test over recorded structured facts, not verification of an original document.'])
    (directory/'truth.json').write_text(json.dumps(truth, indent=2) + '\n')
    (directory/'key.json').write_text(json.dumps(key, indent=2) + '\n')
    return Material(directory, truth, specs, reg)


def program(material, *, reference=True):
    parts = [(LEAN_DIR/'Interface.lean').read_text(), 'set_option maxRecDepth 1000000',
             'set_option maxHeartbeats 0']
    if reference:
        parts.append((LEAN_DIR/'Reference.lean').read_text())
    parts += [(material.directory/name).read_text() for name in ('Facts.lean', 'Inputs.lean')]
    parts += ['open Casework Casework.Material',
              'def emitBits (tag : String) (xs : List Bool) : String :=',
              '  "FIDELITY|" ++ tag ++ "|" ++ String.mk (xs.map (fun b => if b then \'1\' else \'0\'))',
              'def emitResults (tag : String) (xs : List Result) : String :=',
              '  "CASEWORK|" ++ tag ++ "|" ++ String.intercalate ";" (xs.map (fun r =>',
              '    String.intercalate "," ([r.deadline, r.basic, if r.took then 1 else 0,',
              '      if r.shifted then 1 else 0, r.received, if r.late then 1 else 0, r.days].map toString)))']
    return '\n\n'.join(parts) + '\n'


def parse_results(output, lengths):
    found = {}
    for line in output.splitlines():
        if not line.startswith('"CASEWORK|'):
            continue
        try:
            _, tag, payload = json.loads(line).split('|')
            rows = [[int(cell) for cell in row.split(',')] for row in payload.split(';')]
        except (ValueError, TypeError) as exc:
            raise FidelityError('Malformed casework result vector') from exc
        if (tag not in lengths or tag in found or len(rows) != lengths[tag] or
                any(len(r) != 7 or any(n < 0 for n in r) or any(r[j] not in (0, 1) for j in (2, 3, 5)) for r in rows)):
            raise FidelityError(f'Unexpected, duplicate, or incomplete casework vector: {tag}')
        found[tag] = rows
    if set(found) != set(lengths):
        raise FidelityError('Missing casework result vectors')
    return found


def result_agreement(actual, expected):
    if len(actual) != len(expected) or not expected:
        raise FidelityError('Empty or unequal result vectors')
    return agreement([a == e for a, e in zip(actual, expected)], [True] * len(expected))


def evaluate(material, runner, candidate=None):
    source = program(material, reference=candidate is None)
    if candidate is not None:
        source += '\n' + Path(candidate).read_text() + '\n'
    rules = (['Reference.applyRule'] + [f'(Reference.withSlip {string(v)})' for v in VARIANTS]
             if candidate is None else ['Candidate.model.rule'])
    for j, rule in enumerate(rules):
        source += f'#eval emitResults "rule{j}" (unseen.map (fun x => {rule} x.1 x.2))\n'
    lengths = {}
    for i, claim in enumerate(material.truth['claims']):
        test = (f'Reference.test Reference.applyRule ({material.specs[i]})' if candidate is None
                else f'Candidate.model.claim {string(claim["id"])}')
        source += f'#eval emitBits "claim{i}" (claimWorlds{i}.map ({test}))\n'
        lengths[f'claim{i}'] = claim['input_count']
    path = material.directory / ('ReferenceCheck.lean' if candidate is None else 'CandidateCheck.lean')
    if path.exists():
        raise FidelityError('Use a distinct run directory for each reference/candidate execution')
    path.write_text(source)
    result = run_checked(path, runner)
    vectors = parse_vectors(result.stdout, lengths)
    vectors.update(parse_results(result.stdout, {f'rule{j}': material.truth['unseen_count'] for j in range(len(rules))}))
    return vectors, result


def validate(material, runner):
    actual, execution = evaluate(material, runner)
    expected = material.truth['rule_expected']
    rule = result_agreement(actual['rule0'], expected)
    if rule['rate'] != 1:
        raise FidelityError('Lean reference disagrees with Python generator rule')
    for j, v in enumerate(VARIANTS, 1):
        if actual[f'rule{j}'] != material.truth['variant_expected'][v]:
            raise FidelityError(f'Lean variant disagrees with generator slip: {v}')
    for i, c in enumerate(material.truth['claims']):
        values = actual[f'claim{i}']
        if values != c['sealed'] + c['unseen']:
            bad = [j for j, (a, b) in enumerate(zip(values, c['sealed'] + c['unseen'])) if a != b]
            raise FidelityError(f'Lean reference disagrees with generator predicate: {c["id"]}, indices {bad[:10]}')
        row = material.regenerated.key['expectations'][i]
        sealed = values[:len(c['sealed'])]
        if (verdict(sealed) != row['semantic_state'] or len(sealed) != row['derivation']['worlds'] or
                sum(sealed) != row['derivation']['worlds_where_true']):
            raise FidelityError(f'Key cross-check failed: {c["id"]}')
    witnesses = {v: [i for i, (a, e) in enumerate(zip(actual[f'rule{j}'], expected)) if a != e]
                 for j, v in enumerate(VARIANTS, 1)}
    missed = [v for v, indices in witnesses.items() if not indices]
    report = dict(key_cross_check=True, reference_agreement=rule,
                  test_strength=dict(detected=len(VARIANTS)-len(missed), total=len(VARIANTS),
                                     rate=(len(VARIANTS)-len(missed))/len(VARIANTS),
                                     undetected=missed, witness_indices=witnesses),
                  coverage=material.truth['coverage'], seconds=execution.seconds)
    (material.directory/'reference-result.json').write_text(json.dumps(report, indent=2) + '\n')
    if missed:
        raise FidelityError(f'Unseen set is too weak; undetected variants: {missed}')
    material._validated = True
    return report


def grade(material, candidate, runner, *, prove=False, proof_limit=PROOF_LIMIT):
    candidate = Path(candidate)
    if not getattr(material, '_validated', False):
        validate(material, runner)
    actual, execution = evaluate(material, runner, candidate)
    per_claim = []
    for i, c in enumerate(material.truth['claims']):
        score = agreement(actual[f'claim{i}'], c['sealed'] + c['unseen'])
        per_claim.append(dict(id=c['id'], agrees_everywhere=score['rate'] == 1, **score))
    correct = sum(c['agrees_everywhere'] for c in per_claim)
    report = dict(rule_fidelity=result_agreement(actual['rule0'], material.truth['rule_expected']),
                  claim_fidelity=dict(matched=correct, total=len(per_claim), rate=correct/len(per_claim)),
                  claims=per_claim, coverage=material.truth['coverage'], seconds=execution.seconds,
                  candidate_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(), proof=dict(status='not_requested'))
    if prove:
        if material.truth['unseen_count'] > proof_limit:
            report['proof'] = dict(status='skipped', reason='bounded set exceeds proof_limit')
        elif report['rule_fidelity']['rate'] != 1:
            report['proof'] = dict(status='not_equal', reason='rule counterexample observed')
        else:
            source = program(material) + '\n' + candidate.read_text() + '\n'
            source += ('theorem candidate_rule_agrees :\n'
                       '  unseen.all (fun x => Candidate.model.rule x.1 x.2 == Reference.applyRule x.1 x.2) = true := by decide\n'
                       '#print axioms candidate_rule_agrees\n')
            path = material.directory/'Agreement.lean'
            path.write_text(source)
            result = run_checked(path, runner)
            marker = "'candidate_rule_agrees' does not depend on any axioms"
            if marker not in result.stdout:
                raise FidelityError('Agreement proof missing or depends on axioms')
            report['proof'] = dict(status='kernel_checked', scope='rule on entire bounded unseen set',
                                   axiom_report=marker, seconds=result.seconds)
    (material.directory/'grade.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def fixture(material, *, slip=None, wrong_request=False, constant=False):
    if slip is not None and slip not in VARIANTS:
        raise FidelityError(f'Unknown fixture slip: {slip}')
    source = (LEAN_DIR/'fixtures/Faithful.lean').read_text()
    if slip:
        source += (LEAN_DIR/'fixtures/Slips.lean').read_text()
    rule = slip or 'applyRule'
    source += '\nnamespace Candidate\nopen Casework\n'
    source += 'def claims (id : String) (' + ('_w' if constant else 'w') + ' : World) : Bool :=\n  match id with\n'
    original = next(capture(c['pred'])['model'] for c in material.regenerated.claims if 'model' in capture(c['pred']))
    for row, item in zip(material.truth['claims'], material.regenerated.claims):
        c, kind = capture(item['pred']), item['kind']
        target = c.get('ident', c.get('p2'))
        if wrong_request and target:
            target = next(n['id'] for n in original.notices if n['id'] != target)
        outcome = f'({rule} w {string(target)})'
        rows = '(w.registers[w.inForce]!)'
        op = '==' if kind not in ('deadline_date', 'receipt_date', 'late_replies', 'shifted_deadlines',
                                  'extensions_in_effect', 'late_replies_by_post') or exact(item) else '>='
        if constant:
            body = boolean(row['sealed'][0])
        elif kind == 'deadline_date':
            body = f'{outcome}.deadline {op} {ordinal(c["value"])}'
        elif kind == 'receipt_date':
            body = f'{outcome}.received {op} {ordinal(c["date"])}'
        elif kind == 'reply_timing':
            body = f'{outcome}.late == {boolean(c["late"])}'
        elif kind == 'days_late':
            body = f'{outcome}.late && {outcome}.days == {c["days"]}'
        elif kind == 'extension_effect':
            body = f'{outcome}.took == {boolean(c["flag"])}'
        elif kind == 'notice_lookup':
            body = f'({rows}.find? (fun n => n.id == {string(target)})).any (fun n => n.issued == {ordinal(c["date"])})'
        elif kind == 'deadline_extreme':
            cls = original.by_id[c['group'][0]]['cls']
            cmp = '<=' if c['pick'] is max else '>='
            body = (f'let group := {rows}.filter (fun n => n.cls == .{cls})\n'
                    f'    let value := {ordinal(c["date"])}\n'
                    f'    group.any (fun n => n.id == {string(target)} && ({rule} w n.id).deadline == value) &&\n'
                    f'      group.all (fun n => if n.id == {string(target)} then true else\n'
                    f'        ({rule} w n.id).deadline {cmp[:-1]} value)')
        else:
            field = {'late_replies': 'late', 'late_replies_by_post': 'late', 'shifted_deadlines': 'shifted',
                     'extensions_in_effect': 'took'}[kind]
            selector = 'n.channel == .post && ' if kind == 'late_replies_by_post' else ''
            body = f'({rows}.filter (fun n => {selector}({rule} w n.id).{field})).length {op} {c["value"]}'
        source += f'  | {string(row["id"])} => {body}\n'
    source += f'  | _ => false\ndef model : Model := ⟨{rule}, claims⟩\nend Candidate\n'
    path = material.directory/'Fixture.lean'
    path.write_text(source)
    return path
