#!/usr/bin/env python3
"""Generate synthetic audit cases whose answer keys are computed, not written by hand.

Each case starts as structured facts. Some facts are deliberately left out of the
rendered sources and become unknowns with a finite set of possible values. A claim is
a statement that is true or false once every unknown has a value, and its verdict is
computed by trying every combination the sources allow:

    true in every combination   -> supported
    false in every combination  -> refuted
    true in some, false in some -> undetermined

A correct report holds only supported claims. The mixed report replaces some of them
with typed errors, and the same computation decides what each error deserves.

A family may also describe a "told" world: what a reader would hold true who took a
statement in the packet about the packet itself at its word, for example a transmittal
sheet that lists an annex as not included although its file was supplied. Where that
world gives a claim another verdict than the documents do, the key records it as
`echo_state`, so the scorer can count the answers that follow the statement.

The generator calls no model and reads nothing but its arguments. Keep the keys folder
away from every folder an audited agent can read, and seal it before a campaign.
"""
import argparse
import datetime
import hashlib
import itertools
import json
from pathlib import Path
import platform
import random

GENERATOR_VERSION = 4
MAX_WORLDS = 200_000
DAY = datetime.timedelta(days=1)


class GenerationError(Exception):
    """A case could not be built as requested."""


# ---------- Claims and their verdicts ----------

def claim(kind, text, pred, cites, quantity=None, stated=None, formal=True, error=None):
    """One statement of a report. `pred` decides it in a world where every unknown has a value;
    `quantity` names the value the statement is about, for the derivation kept in the key."""
    return {'kind': kind, 'text': text, 'pred': pred, 'cites': cites, 'quantity': quantity,
            'stated': stated, 'formal': formal, 'error': error}


def plain(value):
    return value.isoformat() if isinstance(value, datetime.date) else value


def worlds(base, unknowns):
    """Every completed world the sources allow: the known facts plus one value for each unknown."""
    names = sorted(unknowns)
    size = 1
    for name in names:
        size *= len(unknowns[name])
    if size > MAX_WORLDS:
        raise GenerationError(f'{size} possible worlds exceed the limit of {MAX_WORLDS}')
    for combination in itertools.product(*(unknowns[name] for name in names)):
        yield dict(base, **dict(zip(names, combination)))


def judge(item, base, unknowns):
    """Evaluate one claim in every world the sources allow and return its verdict and derivation."""
    true, size, values = 0, 0, set()
    for world in worlds(base, unknowns):
        size += 1
        true += bool(item['pred'](world))
        if item['quantity']:
            values.add(item['quantity'](world))
    state = 'supported' if true == size else 'refuted' if true == 0 else 'undetermined'
    derivation = {'worlds': size, 'worlds_where_true': true}
    if item['stated'] is not None:
        derivation['stated'] = plain(item['stated'])
    if values:
        ordered = sorted(values, key=lambda v: (v is None, v if v is not None else 0))
        derivation.update(feasible_values=len(ordered), feasible_min=plain(ordered[0]), feasible_max=plain(ordered[-1]))
    return state, derivation


def span(quantity, base, unknowns):
    """Smallest and largest value of a quantity over every world the sources allow."""
    values = [quantity(world) for world in worlds(base, unknowns)]
    return min(values), max(values)


def bound_slot(make, low, high, rng, steps=(1,), floor=1):
    """A statement about a count or an amount, as "exactly" or as "at least". `make(value, exact, error)`
    builds the claim. Where the sources settle the value, the true statement is usually exact; where an
    unknown leaves a range, only a lower bound is true. Each wording is also used for errors, and each is
    sometimes true, so the wording alone never gives the verdict away."""
    step = lambda: rng.choice(steps)  # noqa: E731
    weaker = low - step()
    if low == high:
        true = make(low, True, None) if weaker < floor or rng.random() < 0.7 else make(weaker, False, None)
        wrong = [make(low + step(), True, 'wrong_value'), make(high + step(), False, 'overstated')]
        if weaker >= floor:
            wrong.append(make(weaker, True, 'wrong_value'))
        return true, wrong
    true = make(low if weaker < floor or rng.random() < 0.7 else weaker, False, None)
    return true, [make(high + step(), False, 'overstated'), make(low, True, 'asserts_unknown_as_fact'),
                  make(high, True, 'treats_unknown_as_settled')]


def rationale(item, derivation, mark):
    """A formula, not a sentence: the leak scan looks for key wording in an agent's files, and
    ordinary prose about the same numbers would match by chance. The mark makes even the shortest
    rationale long enough for that scan and unlike anything an auditor would write."""
    parts = [f'kind={item["kind"]}']
    parts += [f'{name}={derivation[name]}' for name in ('stated', 'feasible_min', 'feasible_max') if name in derivation]
    parts.append(f'true_in={derivation["worlds_where_true"]}/{derivation["worlds"]} mark={mark}')
    return ' '.join(parts)


# ---------- Rendering ----------

def section(anchor, title, body):
    return f'<a id="{anchor}"></a>\n## {anchor} — {title}\n{body.strip()}\n'


def document(title, sections):
    return f'# {title}\n\n' + '\n'.join(sections)


def table(headers, rows):
    lines = ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |']
    lines += ['| ' + ' | '.join(str(cell) for cell in row) + ' |' for row in rows]
    return '\n'.join(lines)


def citation(cites):
    grouped = {}
    for name, anchor in cites:
        grouped.setdefault(name[:-3], []).append(anchor)
    return ('Citations: ' if len(cites) > 1 else 'Citation: ') + '; '.join(
        f'{name} {", ".join(anchors)}' for name, anchors in grouped.items()) + '.'


def report(title, items):
    blocks = [f'# {title}\n']
    for number, item in enumerate(items, 1):
        ident = f'C{number:02d}'
        blocks.append(f'<a id="{ident}"></a>\n**{ident}.** {item["text"]} {citation(item["cites"])}\n')
    return '\n'.join(blocks)


def plural(count, word):
    return f'{count} {word}' + ('' if count == 1 else 's')


# ---------- Family: a sampled payment ledger (sums, counts, bounds) ----------

PAYEES = ('Alder Supply', 'Birch Logistics', 'Cobalt Print', 'Dune Catering', 'Elm Software', 'Fjord Travel',
          'Garnet Office', 'Heron Security', 'Iris Cleaning', 'Juniper Legal', 'Kestrel Freight', 'Larch Design')


def ledger(rng):
    count = rng.randint(5, 8)
    start = datetime.date(2031, rng.choice((1, 4, 7, 10)), 1)
    while True:
        entries = [{'id': f'P{i + 1:02d}', 'payee': payee, 'date': start + DAY * rng.randrange(85),
                    'ledger_amount': rng.randrange(40, 500) * 500, 'approval': 'yes', 'voided': False}
                   for i, payee in enumerate(rng.sample(PAYEES, count + 1))]
        if len({e['ledger_amount'] for e in entries}) == len(entries) and len({e['date'] for e in entries}) == len(entries):
            break
    void = rng.choice(entries) if rng.random() < 0.8 else None
    if void:
        void['voided'] = True
    else:
        entries.pop()
    live_rows = [e for e in entries if not e['voided']]
    for e in entries:
        e['amount'] = e['ledger_amount']
    fixed = rng.choice(live_rows) if rng.random() < 0.8 else None
    if fixed:
        while True:
            fixed['amount'] = fixed['ledger_amount'] + rng.choice((-1, 1)) * rng.randrange(1, 30) * 500
            if fixed['amount'] > 0 and len({e['amount'] for e in live_rows}) == len(live_rows):
                break
    absent = rng.randint(1, 2)
    marked = rng.sample(live_rows, absent + rng.choice((0, 0, 1, 2)))     # half the cases have no missing sheet
    for e in marked[:absent]:
        e['approval'] = 'no'
    for e in marked[absent:]:
        e['approval'] = 'unknown'
    complete = rng.random() < 0.3                                         # the sample is sometimes the whole population
    population = len(live_rows) + (0 if complete else rng.randint(6, 14))
    cutoff = start + DAY * 90
    lacking = rng.choice([e for e in live_rows if e['approval'] == 'no'])
    annex = rng.choice(('absent', 'absent', 'obtained', 'not_obtained'))
    obtained_on = cutoff + DAY * rng.randint(5, 30)
    closed = obtained_on + DAY * rng.randint(5, 30)

    def live(w):
        return [e for e in w['entries'] if not e['voided']]

    def lacks(w, e):
        return e['approval'] == 'no' or (e['approval'] == 'unknown' and not w['approval:' + e['id']])

    def total(w):
        return sum(e['amount'] for e in live(w))

    def missing(w):
        return [e for e in live(w) if lacks(w, e)]

    def missing_count(w):
        return len(missing(w))

    def missing_total(w):
        return sum(e['amount'] for e in missing(w))

    def population_missing(w):
        return len(missing(w)) + w['unsampled_missing']

    true_total = sum(e['amount'] for e in live_rows)
    ledger_total = sum(e['ledger_amount'] for e in live_rows)
    budget = true_total + rng.choice((-1, 1, 1)) * rng.randrange(4, 80) * 500
    base = {'entries': entries, 'budget': budget, 'population': population, 'annex_included': annex != 'absent'}
    unknown_rows = [e for e in live_rows if e['approval'] == 'unknown']
    unknowns = {'approval:' + e['id']: [True, False] for e in unknown_rows}
    unknowns['unsampled_missing'] = list(range(population - len(live_rows) + 1))
    if annex == 'absent':
        unknowns['annex_says'] = ['obtained', 'not_obtained', 'nothing']
    else:
        base['annex_says'] = annex

    correction_rows = [(fixed['id'], fixed['ledger_amount'], fixed['amount'])] if fixed else []
    status = {'yes': 'Present', 'no': 'Absent', 'unknown': 'Approval sheet not in packet'}
    aside = ', and one voided entry that is not part of the population' if void else ''
    scope = (f'The table in L-02 lists every one of them{aside}.' if complete else
             f'The table in L-02 lists the complete selected sample: {len(live_rows)} population entries{aside}. '
             f'The packet contains no data on the other {population - len(live_rows)} population entries.')
    annex_text = {
        'absent': 'Annex B is not included in the supplied packet. Nothing in the packet states what Annex B contains.',
        'obtained': 'Annex B is included in the supplied packet and reads in full: “This annex was closed on '
                    f'{closed.isoformat()}. By then the approval that was absent at the cutoff for entry {lacking["id"]} '
                    f'had been obtained, on {obtained_on.isoformat()}.”',
        'not_obtained': 'Annex B is included in the supplied packet and reads in full: “This annex was closed on '
                        f'{closed.isoformat()}. By then the approval that was absent at the cutoff for entry '
                        f'{lacking["id"]} had still not been obtained.”'}[annex]
    sources = {
        'ledger.md': document('Synthetic sampled payment ledger', [
            section('L-01', 'Cutoff, population and sample',
                    f'At the {cutoff.isoformat()} audit cutoff, the target population contained exactly {population} paid '
                    f'entries that were not voided. {scope}'),
            section('L-02', 'Sampled entries', table(
                ('Entry', 'Payee', 'Payment date', 'Ledger amount, euro cents', 'Required approval at cutoff', 'Status'),
                [(e['id'], e['payee'], e['date'].isoformat(), e['ledger_amount'], status[e['approval']],
                  'Voided' if e['voided'] else 'Paid') for e in entries])),
            section('L-03', 'Comparison budget',
                    f'The comparison budget for the sampled payments that are not voided is exactly {budget} euro cents.')]),
        'method.md': document('Synthetic review method', [
            section('M-01', 'Units and scope',
                    'All amounts are whole euro cents. Totals, counts and comparisons cover only sampled entries that '
                    'are not voided, unless a statement names the whole population.'),
            section('M-02', 'Corrections',
                    'A correction listed in the correction notice replaces the ledger amount of that entry for every '
                    'purpose: totals, comparisons and rankings.'),
            section('M-03', 'Approvals',
                    'An entry lacks the required approval exactly when its approval was absent at the cutoff. Where the '
                    'table in L-02 shows “Approval sheet not in packet”, the packet establishes neither that the '
                    'approval was present nor that it was absent.')]),
        'corrections.md': document('Synthetic correction notice', [
            section('K-01', 'Corrected amounts',
                    table(('Entry', 'Ledger amount, euro cents', 'Corrected amount, euro cents'), correction_rows)
                    if correction_rows else 'No correction was issued for any sampled entry.')]),
        'annex.md': document('Synthetic annex register', [
            section('A-01', 'Annex B',
                    f'The packet index lists Annex B, titled “Post-cutoff approval follow-up”. {annex_text}')])}

    L1, L2, L3 = ('ledger.md', 'L-01'), ('ledger.md', 'L-02'), ('ledger.md', 'L-03')
    M1, M2, M3 = ('method.md', 'M-01'), ('method.md', 'M-02'), ('method.md', 'M-03')
    K1, A1 = ('corrections.md', 'K-01'), ('annex.md', 'A-01')
    slip = lambda: rng.choice((-1, 1)) * rng.choice((500, 1000, 5000, 10000))  # noqa: E731

    def slipped(value):
        while True:
            changed = value + slip()
            if changed > 0:
                return changed
    slots = []

    def total_claim(value, error=None):
        return claim('sample_total', f'The sampled payments that are not voided total exactly {value} euro cents.',
                     lambda w: total(w) == value, [L2, M1, M2, K1], total, value, error=error)
    wrong = [total_claim(slipped(true_total), 'arithmetic_slip')]
    if fixed:
        wrong.append(total_claim(ledger_total, 'uses_superseded_amount'))
    if void:
        wrong.append(total_claim(true_total + void['amount'], 'includes_voided_entry'))
    slots.append((total_claim(true_total), wrong))

    def gap_claim(value, over, error=None):
        text = (f'The sampled payments that are not voided exceed the comparison budget by exactly {value} euro cents.'
                if over else f'The comparison budget exceeds the sampled payments that are not voided by exactly {value} euro cents.')
        return claim('budget_gap', text, lambda w: (total(w) - w['budget'] if over else w['budget'] - total(w)) == value,
                     [L2, L3, M2, K1], lambda w: total(w) - w['budget'] if over else w['budget'] - total(w), value, error=error)
    over = true_total > budget
    gap = abs(true_total - budget)
    wrong = [gap_claim(slipped(gap), over, 'arithmetic_slip'), gap_claim(gap, not over, 'reversed_direction')]
    if fixed and ledger_total != budget:
        wrong.append(gap_claim(abs(ledger_total - budget), ledger_total > budget, 'uses_superseded_amount'))
    slots.append((gap_claim(gap, over), wrong))

    def largest_claim(entry, amount, error=None):
        return claim('largest_payment',
                     f'The largest sampled payment that is not voided is entry {entry["id"]}, at {amount} euro cents.',
                     lambda w: max(live(w), key=lambda e: e['amount'])['id'] == entry['id']
                     and max(e['amount'] for e in live(w)) == amount,
                     [L2, M1, M2, K1], lambda w: max(e['amount'] for e in live(w)), amount, error=error)
    ranked = sorted(live_rows, key=lambda e: -e['amount'])
    wrong = [largest_claim(ranked[1], ranked[1]['amount'], 'wrong_entry')]
    if void and void['amount'] > ranked[0]['amount']:
        wrong.append(largest_claim(void, void['amount'], 'includes_voided_entry'))
    if fixed is ranked[0]:
        wrong.append(largest_claim(ranked[0], ranked[0]['ledger_amount'], 'uses_superseded_amount'))
    slots.append((largest_claim(ranked[0], ranked[0]['amount']), wrong))

    def lacking_claim(value, exact, error=None):
        lead = 'Exactly' if exact else 'At least'
        return claim('sample_missing_approvals',
                     f'{lead} {value} of the sampled entries that are not voided lacked the required approval at the cutoff.',
                     lambda w: missing_count(w) == value if exact else missing_count(w) >= value,
                     [L2, M3], missing_count, value, error=error)
    slots.append(bound_slot(lacking_claim, *span(missing_count, base, unknowns), rng))

    def lacking_amount_claim(value, exact, error=None):
        lead = 'exactly' if exact else 'at least'
        return claim('sample_missing_approval_amount',
                     f'The sampled payments that are not voided and lacked the required approval total {lead} {value} euro cents.',
                     lambda w: missing_total(w) == value if exact else missing_total(w) >= value,
                     [L2, M2, M3, K1], missing_total, value, error=error)
    slots.append(bound_slot(lacking_amount_claim, *span(missing_total, base, unknowns), rng, (500, 1000, 5000), 500))

    def recorded_claim(value, error=None):
        return claim('recorded_approvals',
                     f'The table in L-02 shows the required approval as “Present” for exactly {value} of the '
                     f'{len(live_rows)} sampled entries that are not voided.',
                     lambda w: sum(e['approval'] == 'yes' for e in live(w)) == value, [L2, M1],
                     lambda w: sum(e['approval'] == 'yes' for e in live(w)), value, formal=False, error=error)
    present = sum(e['approval'] == 'yes' for e in live_rows)
    wrong = [recorded_claim(present + len(unknown_rows), 'treats_missing_sheet_as_present'),
             recorded_claim(present - 1, 'wrong_value')]
    if void:
        wrong.append(recorded_claim(present + 1, 'includes_voided_entry'))
    slots.append((recorded_claim(present), wrong))

    def population_claim(value, exact, error=None):
        lead = 'Exactly' if exact else 'At least'
        return claim('population_missing_approvals',
                     f'{lead} {value} of the {population} population entries lacked the required approval at the cutoff.',
                     lambda w: population_missing(w) == value if exact else population_missing(w) >= value,
                     [L1, L2, M3], population_missing, value, error=error)
    true, wrong = bound_slot(population_claim, *span(population_missing, base, unknowns), rng)
    wrong.append(population_claim(round(absent * population / len(live_rows)), True, 'extrapolates_sample_rate'))
    slots.append((true, wrong))

    def paid_claim(entry, payee, date, error=None):
        return claim('payment_lookup', f'Entry {entry["id"]} was paid to {payee} on {date.isoformat()}.',
                     lambda w: (entry['payee'], entry['date']) == (payee, date), [L2], formal=False, error=error)
    one, other = rng.sample(live_rows, 2)
    slots.append((paid_claim(one, one['payee'], one['date']),
                  [paid_claim(one, other['payee'], one['date'], 'wrong_attribution'),
                   paid_claim(one, one['payee'], other['date'], 'wrong_date')]))

    # Three statements about Annex B. Which of them is true depends on whether the annex was supplied and on what it
    # says, so each wording is right in some cases and wrong or unestablished in others.
    stray = 'asserts_missing_document_content' if annex == 'absent' else 'misreads_document'
    annex_claims = {
        'absent': claim('annex_scope', 'Annex B is listed in the packet index but is not included in the supplied packet.',
                        lambda w: not w['annex_included'], [A1], formal=False,
                        error=None if annex == 'absent' else 'overlooks_supplied_document'),
        'obtained': claim('annex_content', f'Annex B confirms that the approval missing for entry {lacking["id"]} was '
                          'obtained after the cutoff.', lambda w: w['annex_says'] == 'obtained', [A1, L2], formal=False,
                          error=None if annex == 'obtained' else stray),
        'not_obtained': claim('annex_content', f'Annex B records that the approval missing for entry {lacking["id"]} had '
                              'still not been obtained when the annex was closed.', lambda w: w['annex_says'] == 'not_obtained',
                              [A1, L2], formal=False, error=None if annex == 'not_obtained' else stray)}
    slots.append((annex_claims[annex], [item for name, item in annex_claims.items() if name != annex]))
    return {'title': 'Synthetic payment sample review', 'sources': sources, 'base': base, 'unknowns': unknowns, 'slots': slots}


# ---------- Family: reply deadlines under two rule versions (dates, rule choice) ----------

def deadlines(rng):
    first, second = rng.sample((14, 21, 30, 45, 60), 2)
    change = datetime.date(2032, rng.randint(3, 10), rng.randint(1, 28))
    count = rng.randint(6, 8)
    earlier = count // 2 - 1
    # One notice on the day the rule changes and one on the day before, so the boundary is tested from both sides.
    offsets = [0, -1] + rng.sample(range(-60, -1), earlier) + rng.sample(range(1, 61), count - 2 - earlier)
    rng.shuffle(offsets)
    notices = [{'id': f'N{i + 1:02d}', 'issued': change + DAY * offset, 'extension': None, 'days': 0}
               for i, offset in enumerate(offsets)]
    for n in notices:
        n['period'] = first if n['issued'] < change else second
        n['version'] = 1 if n['issued'] < change else 2
    order = rng.sample(notices, len(notices))
    granted = order[0]
    undecided = order[1] if rng.random() < 0.6 else None      # without these two, every count below is settled
    unrecorded = order[2] if rng.random() < 0.6 else None
    refused = order[3] if rng.random() < 0.6 else None
    granted.update(extension='granted', days=rng.choice((5, 7, 10, 14)))
    if undecided:
        undecided.update(extension='undecided', days=rng.choice((5, 7, 10, 14)))
    if refused:
        refused.update(extension='refused', days=rng.choice((5, 7, 10)))
    plain_rows = [n for n in notices if n is not granted and n is not undecided and n is not unrecorded]
    for n in notices:
        base_deadline = n['issued'] + DAY * n['period']
        if n is undecided:      # on time only if the extension was granted
            n['reply'] = base_deadline + DAY * rng.randint(1, n['days'])
        elif n is unrecorded:
            n['reply'] = None
        elif n is granted:      # late under the base period, on time with the extension, or late either way
            n['reply'] = base_deadline + DAY * rng.choice((rng.randint(1, n['days']), n['days'] + rng.randint(1, 6)))
        else:
            n['reply'] = base_deadline + DAY * rng.choice((-rng.randint(0, 9), -rng.randint(0, 9), rng.randint(1, 12)))
    if not any(n['reply'] > n['issued'] + DAY * n['period'] for n in plain_rows):
        late_row = rng.choice(plain_rows)
        late_row['reply'] = late_row['issued'] + DAY * (late_row['period'] + rng.randint(1, 12))
    if not any(n['reply'] <= n['issued'] + DAY * n['period'] for n in plain_rows):
        early = rng.choice([n for n in plain_rows if n['reply'] > n['issued'] + DAY * n['period']][1:] or plain_rows)
        early['reply'] = early['issued'] + DAY * (early['period'] - rng.randint(0, 9))

    def find(w, ident):
        return next(n for n in w['notices'] if n['id'] == ident)

    def deadline(w, n):
        extended = n['extension'] == 'granted' or (n['extension'] == 'undecided' and w['granted:' + n['id']])
        return n['issued'] + DAY * (n['period'] + (n['days'] if extended else 0))

    def reply(w, n):
        return n['reply'] if n['reply'] is not None else w['reply:' + n['id']]

    def late(w, n):
        return reply(w, n) is not None and reply(w, n) > deadline(w, n)

    def days_late(w, n):
        return None if reply(w, n) is None else (reply(w, n) - deadline(w, n)).days

    def late_count(w):
        return sum(late(w, n) for n in w['notices'])

    base, unknowns = {'notices': notices}, {}
    if undecided:
        unknowns['granted:' + undecided['id']] = [True, False]
    if unrecorded:
        unknowns['reply:' + unrecorded['id']] = [None] + [unrecorded['issued'] + DAY * d for d in range(151)]
    sample = next(worlds(base, unknowns))       # any world serves for notices that depend on no unknown
    decision = {'granted': 'Granted', 'refused': 'Refused', 'undecided': 'No decision recorded'}
    sources = {
        'policy.md': document('Synthetic reply-period policy', [
            section('R-01', 'Version 1', f'Version 1 of the reply rule applies to every notice issued before '
                    f'{change.isoformat()}. Its reply period is {first} days.'),
            section('R-02', 'Version 2', f'Version 2 of the reply rule applies to every notice issued on or after '
                    f'{change.isoformat()}. Its reply period is {second} days.'),
            section('R-03', 'Counting', 'The reply period is counted in calendar days. Day 1 is the day after the '
                    'notice date, and the reply deadline is the last day of the period. A reply received on the deadline '
                    'date is on time. A reply received on any later date is late.'),
            section('R-04', 'Extensions', 'An extension that the extension register records as granted moves the reply '
                    'deadline later by the number of days requested. A refused extension changes nothing. Where the '
                    'register records no decision, the packet does not establish whether the deadline was extended.')]),
        'register.md': document('Synthetic notice register', [
            section('G-01', 'Notices and replies', table(
                ('Notice', 'Notice date', 'Reply received'),
                [(n['id'], n['issued'].isoformat(), n['reply'].isoformat() if n['reply'] else 'Not recorded') for n in notices])),
            section('G-02', 'Unrecorded replies', 'Where the reply date is not recorded, the packet does not establish '
                    'whether or when a reply was received.')]),
        'extensions.md': document('Synthetic extension register', [
            section('X-01', 'Extension requests', 'This register lists every extension request made for the notices in '
                    'G-01.\n\n' + table(('Notice', 'Days requested', 'Decision'),
                                        [(n['id'], n['days'], decision[n['extension']]) for n in notices if n['extension']]))])}

    R1, R2, R3, R4 = (('policy.md', a) for a in ('R-01', 'R-02', 'R-03', 'R-04'))
    G1, G2, X1 = ('register.md', 'G-01'), ('register.md', 'G-02'), ('extensions.md', 'X-01')
    rule = lambda n: R1 if n['version'] == 1 else R2  # noqa: E731
    slots = []

    def deadline_claim(n, date, error=None):
        # A notice with an extension request always gets the same lead, whatever the claim does with the request.
        lead = 'Taking the extension register into account, the' if n['extension'] else 'The'
        return claim('reply_deadline', f'{lead} reply deadline for notice {n["id"]} is {date.isoformat()}.',
                     lambda w: deadline(w, find(w, n['id'])) == date,
                     [rule(n), R3, G1] + ([R4, X1] if n['extension'] else []),
                     lambda w: deadline(w, find(w, n['id'])), date, error=error)
    pick = rng.choice([n for n in plain_rows if not n['extension']])
    true_date = pick['issued'] + DAY * pick['period']
    slots.append((deadline_claim(pick, true_date),
                  [deadline_claim(pick, pick['issued'] + DAY * (second if pick['version'] == 1 else first), 'wrong_rule_version'),
                   deadline_claim(pick, true_date - DAY, 'counts_notice_day')]))

    asked = refused if refused and rng.random() < 0.4 else granted
    plain_date = asked['issued'] + DAY * asked['period']
    longer = plain_date + DAY * asked['days']
    if asked is granted:
        slots.append((deadline_claim(asked, longer), [deadline_claim(asked, plain_date, 'ignores_extension'),
                                                      deadline_claim(asked, longer - DAY, 'counts_notice_day')]))
    else:
        slots.append((deadline_claim(asked, plain_date), [deadline_claim(asked, longer, 'assumes_extension_granted'),
                                                          deadline_claim(asked, plain_date - DAY, 'counts_notice_day')]))

    def timing_claim(n, days, error=None):
        cites = [rule(n), R3, G1] + ([R4, X1] if n['extension'] else [])
        if days is None:
            return claim('reply_timing', f'The reply to notice {n["id"]} was received on time.',
                         lambda w: reply(w, find(w, n['id'])) is not None and not late(w, find(w, n['id'])),
                         cites, lambda w: days_late(w, find(w, n['id'])), error=error)
        return claim('reply_timing', f'The reply to notice {n["id"]} was received {plural(days, "day")} after its deadline.',
                     lambda w: days_late(w, find(w, n['id'])) == days,
                     cites, lambda w: days_late(w, find(w, n['id'])), days, error=error)
    for n in [next(n for n in plain_rows if late(sample, n)), next(n for n in plain_rows if not late(sample, n)), granted]:
        gap = days_late(sample, n)
        if gap > 0:
            slots.append((timing_claim(n, gap), [timing_claim(n, None, 'flips_outcome'),
                                                 timing_claim(n, gap + rng.choice((-1, 1, 2)) or 2, 'wrong_day_count')]))
        else:
            slots.append((timing_claim(n, None), [timing_claim(n, rng.randint(1, 5), 'flips_outcome')]))

    def version_claim(n, version, error=None):
        return claim('rule_version', f'Notice {n["id"]} is governed by version {version} of the reply rule.',
                     lambda w: find(w, n['id'])['version'] == version, [R1, R2, G1], error=error)
    on_the_day = next(n for n in notices if n['issued'] == change)
    day_before = next(n for n in notices if n['issued'] == change - DAY)
    ruled = rng.choice((on_the_day, day_before, rng.choice(notices)))
    slots.append((version_claim(ruled, ruled['version']), [version_claim(ruled, 3 - ruled['version'], 'wrong_rule_version')]))

    def late_claim(value, exact, error=None):
        lead = 'Exactly' if exact else 'At least'
        return claim('late_replies', f'{lead} {value} of the {len(notices)} notices had a reply received after the deadline.',
                     lambda w: late_count(w) == value if exact else late_count(w) >= value,
                     [R1, R2, R3, R4, G1, G2, X1], late_count, value, error=error)
    slots.append(bound_slot(late_claim, *span(late_count, base, unknowns), rng))

    if unrecorded:
        def unrecorded_claim(n, error=None):
            return claim('unrecorded_reply', f'The register records no reply date for notice {n["id"]}.',
                         lambda w: find(w, n['id'])['reply'] is None, [G1, G2], formal=False, error=error)
        slots.append((unrecorded_claim(unrecorded), [unrecorded_claim(rng.choice(plain_rows), 'overlooks_recorded_value'),
                                                     timing_claim(unrecorded, None, 'asserts_unknown_as_fact')]))

    if undecided:
        def undecided_claim(n, error=None):
            return claim('undecided_extension', 'The extension register records no decision on the extension requested '
                         f'for notice {n["id"]}.', lambda w: find(w, n['id'])['extension'] == 'undecided', [X1, R4],
                         formal=False, error=error)
        maybe = undecided['issued'] + DAY * (undecided['period'] + undecided['days'])
        slots.append((undecided_claim(undecided), [undecided_claim(granted, 'overlooks_recorded_value'),
                                                   timing_claim(undecided, None, 'assumes_extension_granted'),
                                                   deadline_claim(undecided, maybe, 'assumes_extension_granted')]))

    def issued_claim(n, date, error=None):
        return claim('notice_lookup', f'Notice {n["id"]} was issued on {date.isoformat()}.',
                     lambda w: find(w, n['id'])['issued'] == date, [G1], formal=False, error=error)
    one, other = rng.sample(notices, 2)
    slots.append((issued_claim(one, one['issued']), [issued_claim(one, other['issued'], 'wrong_date')]))
    return {'title': 'Synthetic reply-deadline review', 'sources': sources, 'base': base, 'unknowns': unknowns, 'slots': slots}


# ---------- Family: approval policy (rule entailment) ----------

STAFF = ('Ada Brandt', 'Bo Castell', 'Cy Dorn', 'Di Ebner', 'Ed Farrow', 'Fay Gould', 'Gus Hale', 'Ivy Jansen')
ROLES = ('Finance officer', 'Finance officer', 'Manager', 'Manager', 'Director', 'Analyst', 'Analyst')


def approvals(rng):
    people = rng.sample(STAFF, 7)
    roles = dict(zip(people, rng.sample(ROLES, 7)))
    limit = rng.choice((2000, 5000, 10000))
    by_role = lambda *wanted: [p for p in people if roles[p] in wanted]  # noqa: E731
    finance, analysts = by_role('Finance officer'), by_role('Analyst')
    counting = by_role('Finance officer', 'Manager', 'Director')
    small = lambda: rng.randrange(2, limit // 100) * 100 - 50  # noqa: E731
    large = lambda: limit + rng.randrange(1, 60) * 100  # noqa: E731

    def build(kind):
        requester = rng.choice(people)
        others = [p for p in counting if p != requester]
        clerks = [p for p in others if roles[p] != 'Finance officer']
        if kind == 'small_ok':
            return requester, small(), [rng.choice(others), None]
        if kind == 'boundary_ok':
            return requester, limit, [rng.choice(others), None]
        if kind == 'small_analyst':
            return requester, small(), [rng.choice([p for p in analysts if p != requester]), None]
        if kind == 'large_ok':
            signer = rng.choice([p for p in finance if p != requester])
            return requester, large(), rng.sample([signer, rng.choice([p for p in others if p != signer])], 2)
        if kind == 'large_no_finance':
            return requester, large(), rng.sample(clerks, 2)
        if kind == 'large_self':
            requester = rng.choice(clerks)
            return requester, large(), [rng.choice([p for p in finance if p != requester]), requester]
        return requester, large(), [rng.choice(clerks), 'illegible']
    chosen = ['large_self', 'large_no_finance', 'large_ok', 'boundary_ok']
    chosen += ['large_illegible'] if rng.random() < 0.6 else []       # without it, every count below is settled
    chosen += rng.sample(['small_analyst', 'small_ok'], rng.randint(1, 2))
    rng.shuffle(chosen)
    requests = []
    for number, kind in enumerate(chosen, 1):
        requester, amount, signers = build(kind)
        requests.append({'id': f'Q{number:02d}', 'kind': kind, 'requester': requester, 'amount': amount, 'signers': signers})

    def find(w, ident):
        return next(r for r in w['requests'] if r['id'] == ident)

    def meets(w, r):
        signers = [w['signer:' + r['id']] if s == 'illegible' else s for s in r['signers'] if s]
        valid = {s for s in signers if s != r['requester'] and w['roles'][s] != 'Analyst'}
        if r['amount'] <= w['limit']:
            return len(valid) >= 1
        return len(valid) >= 2 and any(w['roles'][s] == 'Finance officer' for s in valid)

    def failing(w):
        return sum(not meets(w, r) for r in w['requests'])

    base = {'requests': requests, 'roles': roles, 'limit': limit}
    unclear = next((r for r in requests if r['kind'] == 'large_illegible'), None)
    unknowns = {'signer:' + unclear['id']: list(people)} if unclear else {}
    shown = lambda s: 'None' if s is None else 'Illegible in the supplied scan' if s == 'illegible' else s  # noqa: E731
    sources = {
        'policy.md': document('Synthetic approval policy', [
            section('Q-01', 'Lower tier', f'A request of at most {limit} euro needs one counting approval.'),
            section('Q-02', 'Upper tier', f'A request above {limit} euro needs two counting approvals from two different '
                    'people, and at least one of them must be a Finance officer.'),
            section('Q-03', 'Counting approvals', 'A signature is a counting approval when, and only when, the signer is '
                    'a Manager, a Finance officer or a Director and is not the requester of that request. A signature by '
                    'an Analyst never counts.'),
            section('Q-04', 'Meeting the policy', 'A request meets the approval policy when, and only when, it has the '
                    'counting approvals that Q-01 or Q-02 requires for its amount.')]),
        'staff.md': document('Synthetic staff list', [
            section('S-01', 'Roles', table(('Person', 'Role'), [(p, roles[p]) for p in sorted(people)])),
            section('S-02', 'Signatures', 'Every signature on an approval sheet in this packet belongs to a person '
                    'listed in S-01.')]),
        'requests.md': document('Synthetic request register', [
            section('B-01', 'Requests and signatures', table(
                ('Request', 'Requester', 'Amount, euro', 'First signature', 'Second signature'),
                [(r['id'], r['requester'], r['amount'], shown(r['signers'][0]), shown(r['signers'][1])) for r in requests])),
            section('B-02', 'Illegible signatures', 'Where a signature is illegible, the packet does not establish '
                    'who signed.')])}

    Q1, Q2, Q3, Q4 = (('policy.md', a) for a in ('Q-01', 'Q-02', 'Q-03', 'Q-04'))
    S1, S2, B1, B2 = ('staff.md', 'S-01'), ('staff.md', 'S-02'), ('requests.md', 'B-01'), ('requests.md', 'B-02')
    slots = []

    def policy_claim(r, met, error=None):
        text = f'Request {r["id"]} met the approval policy.' if met else f'Request {r["id"]} did not meet the approval policy.'
        return claim('policy_compliance', text, lambda w: meets(w, find(w, r['id'])) == met,
                     [Q1, Q2, Q3, Q4, S1, S2, B1, B2], error=error)
    for kind in ('large_self', 'large_no_finance', 'large_ok', 'boundary_ok'):
        r = next(r for r in requests if r['kind'] == kind)
        met = meets(base, r)
        errors = {'large_self': 'counts_requester_signature', 'large_no_finance': 'ignores_finance_requirement'}
        slots.append((policy_claim(r, met), [policy_claim(r, not met, errors.get(kind, 'flips_outcome'))]))

    def tier_claim(r, two, error=None):
        need = 'two counting approvals' if two else 'one counting approval'
        return claim('approval_tier', f'Request {r["id"]} needed {need}.',
                     lambda w: (find(w, r['id'])['amount'] > w['limit']) == two, [Q1, Q2, B1], error=error)
    edge = next(r for r in requests if r['kind'] == 'boundary_ok')
    tiered = edge if rng.random() < 0.4 else rng.choice(requests)
    slots.append((tier_claim(tiered, tiered['amount'] > limit), [tier_claim(tiered, tiered['amount'] <= limit, 'applies_wrong_tier')]))

    def failing_claim(value, exact, error=None):
        lead = 'Exactly' if exact else 'At least'
        return claim('policy_failures', f'{lead} {value} of the {len(requests)} requests did not meet the approval policy.',
                     lambda w: failing(w) == value if exact else failing(w) >= value,
                     [Q1, Q2, Q3, Q4, S1, S2, B1, B2], failing, value, error=error)
    slots.append(bound_slot(failing_claim, *span(failing, base, unknowns), rng))

    def role_claim(person, r, role, error=None):
        article = 'an' if role[0] in 'AEIOU' else 'a'
        return claim('signer_role', f'{person}, who signed request {r["id"]}, is {article} {role}.',
                     lambda w: w['roles'][person] == role, [S1, B1], formal=False, error=error)
    signed = rng.choice([r for r in requests if r['kind'] in ('large_ok', 'large_no_finance')])
    signer = signed['signers'][0]
    slots.append((role_claim(signer, signed, roles[signer]),
                  [role_claim(signer, signed, rng.choice([name for name in sorted(set(ROLES)) if name != roles[signer]]),
                              'wrong_role')]))

    if unclear:
        def illegible_claim(r, error=None):
            return claim('illegible_signature', f'The second signature on request {r["id"]} is illegible in the supplied scan.',
                         lambda w: find(w, r['id'])['signers'][1] == 'illegible', [B1, B2], formal=False, error=error)
        slots.append((illegible_claim(unclear),
                      [illegible_claim(rng.choice([r for r in requests if r is not unclear]), 'overlooks_recorded_value'),
                       policy_claim(unclear, True, 'asserts_unknown_as_fact'),
                       policy_claim(unclear, False, 'treats_illegible_as_missing')]))

    def request_claim(r, requester, amount, error=None):
        return claim('request_lookup', f'Request {r["id"]} was submitted by {requester} for {amount} euro.',
                     lambda w: (find(w, r['id'])['requester'], find(w, r['id'])['amount']) == (requester, amount),
                     [B1], formal=False, error=error)
    one = rng.choice(requests)
    slots.append((request_claim(one, one['requester'], one['amount']),
                  [request_claim(one, one['requester'], one['amount'] + rng.choice((-100, 100, 1000)), 'wrong_amount'),
                   request_claim(one, rng.choice([p for p in people if p != one['requester']]), one['amount'], 'wrong_attribution')]))
    return {'title': 'Synthetic approval-policy review', 'sources': sources, 'base': base, 'unknowns': unknowns, 'slots': slots}


# ---------- Family: a packet and its transmittal sheet (what the packet says it holds, and what it holds) ----------

SITES = ('Hall 1', 'Hall 2', 'Hall 3', 'Dock 4', 'Dock 5', 'Lab 6', 'Lab 7', 'Yard 8')
LABS = ('Northgate Metrology', 'Orrin Calibration', 'Pell Instruments', 'Quay Testing')
OPEN_CERTIFICATE = [(result, in_time) for result in ('Pass', 'Fail') for in_time in (True, False)]


def packet(rng):
    """Each unit has one calibration certificate, an annex that is a file of its own. The transmittal sheet
    states for every annex whether it is included. In most cases the sheet is wrong about one or two annexes,
    in either direction, and in the others it is right, so it can be neither trusted nor dismissed unread.

    A report makes one statement per unit, either about its annex or about the unit, and which of the two is
    drawn without regard to what was supplied. No two claims concern the same unit, so a report cannot show
    which files exist, nor contradict itself in a way that marks it as altered."""
    count = rng.randint(5, 7)
    review = datetime.date(2033, rng.randint(3, 11), rng.randint(1, 28))
    limit = rng.choice((180, 365, 540))
    reference = f'PKT-{rng.randrange(1000, 10000)}'
    ages = {'fresh': lambda: rng.randint(5, limit - 5), 'failed': lambda: rng.randint(5, limit - 5),
            'stale': lambda: limit + rng.randint(10, 200), 'last_day': lambda: limit, 'day_late': lambda: limit + 1}
    while True:
        units = []
        for number, (letter, place) in enumerate(zip('ABCDEFG'[:count], rng.sample(SITES, count)), 1):
            kind = rng.choice(('fresh', 'fresh', 'fresh', 'failed', 'failed', 'stale', 'last_day', 'day_late'))
            units.append({'id': f'U{number:02d}', 'annex': letter, 'location': place, 'kind': kind,
                          'result': 'Fail' if kind == 'failed' else 'Pass', 'tested': review - DAY * ages[kind](),
                          'lab': rng.choice(LABS), 'supplied': rng.random() < 0.65})
        for u in units:
            u['in_time'] = (review - u['tested']).days <= limit
        fit = [u['result'] == 'Pass' and u['in_time'] for u in units if u['supplied']]
        if any(fit) and not all(fit):       # so the count of units that may stay is never zero and never all of them
            break
    # What the sheet says. Three cases in ten have no conflict; the others have one or two, in either direction.
    for u in units:
        u['listed'] = u['supplied']
    for u in rng.sample(units, rng.choice((0, 0, 0, 1, 1, 1, 1, 2, 2, 2))):
        u['listed'] = not u['supplied']

    def certificate(w, u):
        return w['certificate:' + u['id']]

    def may_stay(w, u):
        result, in_time = certificate(w, u)
        return result == 'Pass' and in_time

    def staying(w):
        return sum(may_stay(w, u) for u in units)

    base, unknowns = {}, {}
    for u in units:
        base['included:' + u['annex']] = u['supplied']
        if u['supplied']:
            base['certificate:' + u['id']] = (u['result'], u['in_time'])
        else:
            unknowns['certificate:' + u['id']] = OPEN_CERTIFICATE
    # The world of a reader who takes the sheet at its word: an annex is included when the sheet says so, and a
    # certificate is read only when its file is there and the sheet lists it as included.
    told = {'base': dict(base), 'unknowns': dict(unknowns)}
    for u in units:
        told['base']['included:' + u['annex']] = u['listed']
        if u['supplied'] and not u['listed']:
            del told['base']['certificate:' + u['id']]
            told['unknowns']['certificate:' + u['id']] = OPEN_CERTIFICATE

    name = lambda u: f'annex-{u["annex"].lower()}.md'  # noqa: E731
    sources = {
        'transmittal.md': document('Synthetic transmittal sheet', [
            section('T-01', 'Packet', f'This sheet accompanies packet {reference}, sent for the calibration review of '
                    f'{review.isoformat()}. It lists every annex of the packet and states, for each, whether it is included.'),
            section('T-02', 'Annexes', table(
                ('Annex', 'Title', 'Status in this packet'),
                [(u['annex'], f'Calibration certificate for unit {u["id"]}', 'Included' if u['listed'] else 'Not included')
                 for u in units]))]),
        'register.md': document('Synthetic equipment register', [
            section('E-01', 'Units', table(('Unit', 'Location', 'Calibration certificate'),
                                           [(u['id'], u['location'], f'Annex {u["annex"]}') for u in units])),
            section('E-02', 'Review date', f'The review date is {review.isoformat()}.')]),
        'method.md': document('Synthetic review method', [
            section('M-01', 'Certificates', 'Each unit has exactly one calibration certificate. The result and the test '
                    'date of a unit are established only by its calibration certificate. The register and the '
                    'transmittal sheet do not record them. Where a certificate is not among the source documents '
                    'supplied for this review, the packet establishes neither its result nor its test date.'),
            section('M-02', 'Staying in service', 'A unit may stay in service when, and only when, its calibration '
                    f'certificate records the result “Pass” and the test date is at most {limit} days before the review '
                    'date. The days are the difference between the two calendar dates: a test on the day before the '
                    'review date is 1 day before it.'),
            section('M-03', 'Supplied packet', 'An annex is included in the supplied packet when, and only when, it is '
                    'among the source documents supplied for this review.')])}
    for u in units:
        if u['supplied']:
            sources[name(u)] = document(f'Annex {u["annex"]} — Calibration certificate for unit {u["id"]}', [
                section(f'C{u["annex"]}-01', 'Certificate',
                        f'Packet {reference}, Annex {u["annex"]}. Unit {u["id"]} was tested on {u["tested"].isoformat()} '
                        f'by {u["lab"]}. Result: {u["result"]}.')])

    T2, E1, E2 = ('transmittal.md', 'T-02'), ('register.md', 'E-01'), ('register.md', 'E-02')
    M1, M2, M3 = ('method.md', 'M-01'), ('method.md', 'M-02'), ('method.md', 'M-03')
    # A report cites the certificate whether or not its file was supplied; the key lists only passages that exist.
    sheet = lambda u: (name(u), f'C{u["annex"]}-01')  # noqa: E731
    slots = []

    def contents_claim(u, included, error=None):
        text = (f'Annex {u["annex"]} is included in the supplied packet.' if included else
                f'Annex {u["annex"]} is not included in the supplied packet.')
        return claim('packet_contents', text, lambda w: w['included:' + u['annex']] == included, [T2, M3],
                     formal=False, error=error)

    def result_claim(u, result, error=None):
        return claim('certificate_result', f'The calibration certificate for unit {u["id"]} records the result “{result}”.',
                     lambda w: certificate(w, u)[0] == result, [E1, M1, sheet(u)], formal=False, error=error)

    def service_claim(u, stays, error=None):
        text = f'Unit {u["id"]} may stay in service.' if stays else f'Unit {u["id"]} may not stay in service.'
        return claim('service_status', text, lambda w: may_stay(w, u) == stays, [E1, E2, M1, M2, sheet(u)], error=error)

    def location_claim(u, place, error=None):
        return claim('unit_location', f'Unit {u["id"]} is installed in {place}.', lambda w: u['location'] == place,
                     [E1], formal=False, error=error)

    for u in units:
        if rng.random() < 0.45:         # the statement is about the annex; the draw does not look at what was supplied
            if u['supplied']:
                slots.append((contents_claim(u, True), [contents_claim(
                    u, False, 'overlooks_supplied_document' if u['listed'] else 'echoes_transmittal_sheet')]))
            else:
                slots.append((contents_claim(u, False), [contents_claim(
                    u, True, 'echoes_transmittal_sheet' if u['listed'] else 'asserts_missing_document')]))
            continue
        # A true statement about a certificate needs the certificate. Without it the true statement is the unit's
        # location, which a third of the units with a certificate get as well, and the altered report may put a
        # statement about the missing certificate in its place.
        topic = rng.choice(('result', 'service', 'location')) if u['supplied'] else 'location'
        elsewhere = rng.choice([other for other in units if other is not u])['location']
        if topic == 'result':
            slots.append((result_claim(u, u['result']),
                          [result_claim(u, 'Fail' if u['result'] == 'Pass' else 'Pass', 'misreads_document')]))
        elif topic == 'service':
            stays = may_stay(base, u)
            slip = 'flips_outcome' if stays else 'ignores_failed_result' if u['result'] == 'Fail' else 'ignores_age_limit'
            slots.append((service_claim(u, stays), [service_claim(u, not stays, slip)]))
        else:
            wrong = [location_claim(u, elsewhere, 'wrong_location')]
            if not u['supplied']:
                stray = 'asserts_missing_document_content'
                wrong += [result_claim(u, rng.choice(('Pass', 'Fail')), stray), service_claim(u, rng.random() < 0.5, stray)]
            slots.append((location_claim(u, u['location']), wrong))

    def staying_claim(value, exact, error=None):
        lead = 'Exactly' if exact else 'At least'
        return claim('units_in_service', f'{lead} {value} of the {count} units may stay in service.',
                     lambda w: staying(w) == value if exact else staying(w) >= value,
                     [E1, E2, M1, M2] + [sheet(u) for u in units], staying, value, error=error)
    slots.append(bound_slot(staying_claim, *span(staying, base, unknowns), rng))
    return {'title': 'Synthetic calibration packet review', 'sources': sources, 'base': base, 'unknowns': unknowns,
            'slots': slots, 'told': told if told['base'] != base else None}


FAMILIES = {'ledger': ledger, 'deadlines': deadlines, 'approvals': approvals, 'packet': packet}


# ---------- Cases, keys and files ----------

def expectation(dossier, ident, variant, item, case):
    state, derivation = judge(item, case['base'], case['unknowns'])
    mark = hashlib.sha256(f'{dossier}:{variant}:{ident}:{state}'.encode()).hexdigest()[:12]
    disposition = 'blocked' if state == 'undetermined' else 'nonformal' if not item['formal'] else (
        'proof' if state == 'supported' else 'refutation')
    # The verdict of a reader who believes what the packet says about itself, kept only where it differs.
    echo, echo_derivation = None, None
    if case.get('told'):
        believed, told_derivation = judge(item, case['told']['base'], case['told']['unknowns'])
        if believed != state:
            echo = believed
            echo_derivation = {name: told_derivation[name] for name in ('worlds', 'worlds_where_true')}
    return {'claim_id': ident, 'variant': variant, 'semantic_state': state, 'prospective_disposition': disposition,
            'source_anchors': [f'sources/{name}#{anchor}' for name, anchor in item['cites'] if name in case['sources']],
            'rationale': rationale(item, derivation, mark), 'claim_kind': item['kind'], 'error_type': item['error'],
            'derivation': derivation, 'echo_state': echo, 'echo_derivation': echo_derivation,
            'claim_text': item['text']}


def build_case(family, seed, index):
    """One dossier: sources, a correct report, a mixed report, and the computed key for both."""
    if family not in FAMILIES:
        raise GenerationError(f'Unknown family: {family}')
    rng = random.Random(f'evalsuite:{GENERATOR_VERSION}:{seed}:{index}:{family}')
    case = FAMILIES[family](rng)
    slots = case['slots'][:]
    rng.shuffle(slots)
    correct = [true for true, _ in slots]
    for item in correct:
        if judge(item, case['base'], case['unknowns'])[0] != 'supported':
            raise GenerationError(f'A claim meant to be correct is not supported: {item["text"]}')
    # A mutation is an error only if the computation says so; one that happens to stay true is dropped.
    usable = {}
    for position, (_, mutations) in enumerate(slots):
        options = [m for m in mutations if judge(m, case['base'], case['unknowns'])[0] != 'supported']
        if options:
            usable[position] = options
    wanted = max(2, round(len(slots) * rng.uniform(0.3, 0.5)))
    if len(usable) < wanted:
        raise GenerationError(f'Only {len(usable)} of the {wanted} claims to alter have a usable error')
    mixed = correct[:]
    for position in rng.sample(sorted(usable), wanted):
        mixed[position] = rng.choice(usable[position])
    dossier = f'g{seed}-{index:03d}-{family}'
    rows = [expectation(dossier, f'C{n:02d}', variant, item, case)
            for variant, items in (('correct', correct), ('mixed', mixed)) for n, item in enumerate(items, 1)]
    key = {'schema_version': 1, 'dossier_id': dossier, 'classification': 'evaluator_only',
           'status': 'computed_by_generator',
           'warning': 'Computed answer key. Do not provide this file to an auditing agent.',
           'generator': {'version': GENERATOR_VERSION, 'seed': seed, 'index': index, 'family': family},
           'expectations': rows}
    files = {f'sources/{name}': text for name, text in case['sources'].items()}
    files['report-correct.md'] = report(case['title'], correct)
    files['report-mixed.md'] = report(case['title'], mixed)
    return {'dossier': dossier, 'family': family, 'files': files, 'key': key}


def inside(child, parent):
    child, parent = Path(child).resolve(), Path(parent).resolve()
    return child == parent or parent in child.parents


def write_suite(fixtures, keys, seed, dossiers, families):
    """Write `dossiers` cases, cycling through the families, into two new folders that do not contain each other."""
    fixtures, keys = Path(fixtures), Path(keys)
    if inside(fixtures, keys) or inside(keys, fixtures):
        raise GenerationError('The keys folder and the fixtures folder must not contain each other')
    for folder in (fixtures, keys):
        if folder.exists() and any(folder.iterdir()):
            raise GenerationError(f'Folder must be new or empty: {folder}')
    summary = {'schema_version': 1, 'generator_version': GENERATOR_VERSION, 'seed': seed,
               'python': platform.python_version(), 'families': list(families), 'dossiers': {}}
    states, errors, echoes = {}, {}, 0
    for index in range(1, dossiers + 1):
        case = build_case(families[(index - 1) % len(families)], seed, index)
        hashes = {}
        for name, text in sorted(case['files'].items()):
            target = fixtures / case['dossier'] / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding='utf-8')
            hashes[name] = hashlib.sha256(text.encode('utf-8')).hexdigest()
        keys.mkdir(parents=True, exist_ok=True)
        key_text = json.dumps(case['key'], indent=2, ensure_ascii=False) + '\n'
        (keys / f'{case["dossier"]}.json').write_text(key_text, encoding='utf-8')
        for row in case['key']['expectations']:
            states[row['semantic_state']] = states.get(row['semantic_state'], 0) + 1
            if row['error_type']:
                errors[row['error_type']] = errors.get(row['error_type'], 0) + 1
            echoes += row['echo_state'] is not None
        summary['dossiers'][case['dossier']] = {'family': case['family'], 'files': hashes,
                                                'claims_per_report': len(case['key']['expectations']) // 2}
    summary.update(claims=sum(states.values()), expected_states=dict(sorted(states.items())),
                   injected_errors=dict(sorted(errors.items())), echo_claims=echoes)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--fixtures', type=Path, required=True, help='New folder for the reports and sources')
    parser.add_argument('--keys', type=Path, required=True, help='New folder for the answer keys, outside the fixtures')
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--dossiers', type=int, default=12)
    parser.add_argument('--families', default=','.join(FAMILIES), help='Comma-separated: ' + ', '.join(FAMILIES))
    args = parser.parse_args()
    try:
        families = [name for name in args.families.split(',') if name]
        if not families or any(name not in FAMILIES for name in families):
            raise GenerationError(f'Choose families from: {", ".join(FAMILIES)}')
        if args.dossiers < 1:
            raise GenerationError('--dossiers needs a positive number')
        summary = write_suite(args.fixtures, args.keys, args.seed, args.dossiers, families)
    except GenerationError as error:
        raise SystemExit(f'Cannot generate: {error}')
    # The summary counts the injected errors, so it is answer material: it goes beside the keys, never into the fixtures.
    record = Path(str(args.keys.resolve()) + '.generation.json')
    record.write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: summary[key] for key in ('claims', 'expected_states', 'injected_errors', 'echo_claims')},
                     indent=2))
    print(f'{len(summary["dossiers"])} dossiers in {args.fixtures}; keys in {args.keys}; summary in {record}')


if __name__ == '__main__':
    main()
