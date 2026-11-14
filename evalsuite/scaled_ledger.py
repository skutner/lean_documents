"""The sampled payment ledger at `--scale N`: N batches of the scale-1 dossier in one set of documents.

`generate.ledger` stays as it is and builds the dossier of scale 1. This module copies its body and
changes four things. A batch (`B01`, `B02`, ...) is the unit that repeats; each batch is drawn by the
scale-1 recipe, from its own random source, so its sums, counts and bounds are judged over its own
entries and its own open facts alone. The audit cutoff is one fact of the whole dossier. The entries
of every batch stand in one table, interleaved, and are numbered after the merge. Every aggregate
claim names its batch, and the method says that a statement naming a batch covers only that batch's
sampled entries.

The contract is in the size-ladder specification, section 4: `build_scaled(rng, scale)` returns the
merged sources and `scale` blocks, each with its facts, its unknowns, its slots and the random source
it drew from last. Payees are drawn per batch, so one payee recurs in several batches; entry
identifiers, batch names and annex names never do.
"""
import datetime
import random

from generate import DAY, PAYEES, GenerationError, bound_slot, claim, document, interleave, section, span, table

MAX_SCALE = 30


def draw_batch(rng, start):
    """Lines 167-201 of `generate.ledger`, for one batch, in the same order. `start` is the dossier's.
    The entries have no identifier yet: numbering follows the merge of all batches."""
    count = rng.randint(5, 8)
    while True:
        entries = [{'id': None, 'payee': payee, 'date': start + DAY * rng.randrange(85),
                    'ledger_amount': rng.randrange(40, 500) * 500, 'approval': 'yes', 'voided': False}
                   for payee in rng.sample(PAYEES, count + 1)]
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
    marked = rng.sample(live_rows, absent + rng.choice((0, 0, 1, 2)))     # half the batches have no missing sheet
    for e in marked[:absent]:
        e['approval'] = 'no'
    for e in marked[absent:]:
        e['approval'] = 'unknown'
    complete = rng.random() < 0.3                                         # the sample is sometimes the whole population
    population = len(live_rows) + (0 if complete else rng.randint(6, 14))
    lacking = rng.choice([e for e in live_rows if e['approval'] == 'no'])
    annex = rng.choice(('absent', 'absent', 'obtained', 'not_obtained'))
    offset_obtained, offset_closed = rng.randint(5, 30), rng.randint(5, 30)
    return {'entries': entries, 'void': void, 'live_rows': live_rows, 'fixed': fixed, 'absent': absent,
            'complete': complete, 'population': population, 'lacking': lacking, 'annex': annex,
            'offsets': (offset_obtained, offset_closed)}


def finish_batch(rng, drawn, batch, number, cutoff):
    """Lines 203-389 of `generate.ledger` for one batch, after its entries have identifiers: the facts,
    the unknowns, the nine slots and the passages the batch contributes to the merged documents."""
    entries, void, live_rows, fixed = drawn['entries'], drawn['void'], drawn['live_rows'], drawn['fixed']
    absent, complete, population = drawn['absent'], drawn['complete'], drawn['population']
    lacking, annex = drawn['lacking'], drawn['annex']
    obtained_on = cutoff + DAY * drawn['offsets'][0]
    closed = obtained_on + DAY * drawn['offsets'][1]

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

    corrections = [(fixed['id'], fixed['ledger_amount'], fixed['amount'])] if fixed else []
    aside = ', and one voided entry that is not part of the population' if void else ''
    scope = (f'The table in L-02 lists every one of them{aside}.' if complete else
             f'The table in L-02 lists the complete selected sample: {len(live_rows)} population entries{aside}. '
             f'The packet contains no data on the other {population - len(live_rows)} population entries.')
    # The annex is called by the batch's name, so that no two batches share one.
    annex_name = f'Annex {batch}'
    annex_text = {
        'absent': f'{annex_name} is not included in the supplied packet. Nothing in the packet states what {annex_name} contains.',
        'obtained': f'{annex_name} is included in the supplied packet and reads in full: “This annex was closed on '
                    f'{closed.isoformat()}. By then the approval that was absent at the cutoff for entry {lacking["id"]} '
                    f'had been obtained, on {obtained_on.isoformat()}.”',
        'not_obtained': f'{annex_name} is included in the supplied packet and reads in full: “This annex was closed on '
                        f'{closed.isoformat()}. By then the approval that was absent at the cutoff for entry '
                        f'{lacking["id"]} had still not been obtained.”'}[annex]
    annex_section = section(f'A-{number}', annex_name,
                            f'The packet index lists {annex_name}, titled “Post-cutoff approval follow-up”. {annex_text}')

    L1, L2, L3 = ('ledger.md', 'L-01'), ('ledger.md', 'L-02'), ('ledger.md', 'L-03')
    M1, M2, M3 = ('method.md', 'M-01'), ('method.md', 'M-02'), ('method.md', 'M-03')
    K1, A1 = ('corrections.md', 'K-01'), ('annex.md', f'A-{number}')
    slip = lambda: rng.choice((-1, 1)) * rng.choice((500, 1000, 5000, 10000))  # noqa: E731

    def slipped(value):
        while True:
            changed = value + slip()
            if changed > 0:
                return changed
    slots = []

    def total_claim(value, error=None):
        return claim('sample_total',
                     f'The sampled payments of batch {batch} that are not voided total exactly {value} euro cents.',
                     lambda w: total(w) == value, [L2, M1, M2, K1], total, value, error=error)
    wrong = [total_claim(slipped(true_total), 'arithmetic_slip')]
    if fixed:
        wrong.append(total_claim(ledger_total, 'uses_superseded_amount'))
    if void:
        wrong.append(total_claim(true_total + void['amount'], 'includes_voided_entry'))
    slots.append((total_claim(true_total), wrong))

    def gap_claim(value, over, error=None):
        text = (f'The sampled payments of batch {batch} that are not voided exceed the comparison budget of batch {batch} '
                f'by exactly {value} euro cents.' if over else
                f'The comparison budget of batch {batch} exceeds the sampled payments of batch {batch} that are not voided '
                f'by exactly {value} euro cents.')
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
                     f'The largest sampled payment of batch {batch} that is not voided is entry {entry["id"]}, '
                     f'at {amount} euro cents.',
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
                     f'{lead} {value} of the sampled entries of batch {batch} that are not voided lacked the required '
                     'approval at the cutoff.',
                     lambda w: missing_count(w) == value if exact else missing_count(w) >= value,
                     [L2, M3], missing_count, value, error=error)
    slots.append(bound_slot(lacking_claim, *span(missing_count, base, unknowns), rng))

    def lacking_amount_claim(value, exact, error=None):
        lead = 'exactly' if exact else 'at least'
        return claim('sample_missing_approval_amount',
                     f'The sampled payments of batch {batch} that are not voided and lacked the required approval '
                     f'total {lead} {value} euro cents.',
                     lambda w: missing_total(w) == value if exact else missing_total(w) >= value,
                     [L2, M2, M3, K1], missing_total, value, error=error)
    slots.append(bound_slot(lacking_amount_claim, *span(missing_total, base, unknowns), rng, (500, 1000, 5000), 500))

    def recorded_claim(value, error=None):
        return claim('recorded_approvals',
                     f'The table in L-02 shows the required approval as “Present” for exactly {value} of the '
                     f'{len(live_rows)} sampled entries of batch {batch} that are not voided.',
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
                     f'{lead} {value} of the {population} population entries of batch {batch} lacked the required '
                     'approval at the cutoff.',
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

    # Three statements about the batch's annex. Which of them is true depends on whether the annex was supplied and on
    # what it says, so each wording is right in some batches and wrong or unestablished in others.
    stray = 'asserts_missing_document_content' if annex == 'absent' else 'misreads_document'
    annex_claims = {
        'absent': claim('annex_scope', f'{annex_name} is listed in the packet index but is not included in the supplied packet.',
                        lambda w: not w['annex_included'], [A1], formal=False,
                        error=None if annex == 'absent' else 'overlooks_supplied_document'),
        'obtained': claim('annex_content', f'{annex_name} confirms that the approval missing for entry {lacking["id"]} was '
                          'obtained after the cutoff.', lambda w: w['annex_says'] == 'obtained', [A1, L2], formal=False,
                          error=None if annex == 'obtained' else stray),
        'not_obtained': claim('annex_content', f'{annex_name} records that the approval missing for entry {lacking["id"]} had '
                              'still not been obtained when the annex was closed.', lambda w: w['annex_says'] == 'not_obtained',
                              [A1, L2], formal=False, error=None if annex == 'not_obtained' else stray)}
    slots.append((annex_claims[annex], [item for name, item in annex_claims.items() if name != annex]))
    return {'base': base, 'unknowns': unknowns, 'slots': slots, 'population': population, 'scope': scope,
            'budget': budget, 'corrections': corrections, 'annex_section': annex_section}


def build_scaled(rng, scale):
    """`scale` batches of the ledger dossier, merged into one set of documents."""
    if not isinstance(scale, int) or not 1 <= scale <= MAX_SCALE:
        raise GenerationError(f'--scale for the ledger family must be between 1 and {MAX_SCALE}')
    # Once per dossier: the quarter, and so the audit cutoff that every batch is judged at.
    start = datetime.date(2031, rng.choice((1, 4, 7, 10)), 1)
    cutoff = start + DAY * 90
    batch_width = max(2, len(str(scale)))
    names = [f'B{number:0{batch_width}d}' for number in range(1, scale + 1)]
    rngs = [random.Random(rng.getrandbits(64)) for _ in names]
    drawn = [draw_batch(block_rng, start) for block_rng in rngs]
    for name, batch in zip(names, drawn):
        for e in batch['entries']:
            e['batch'] = name
    merged = interleave(rng, [batch['entries'] for batch in drawn])
    width = max(2, len(str(len(merged))))
    for number, e in enumerate(merged, 1):
        e['id'] = f'P{number:0{width}d}'
    finished = [finish_batch(block_rng, batch, name, name[1:], cutoff)
                for block_rng, batch, name in zip(rngs, drawn, names)]
    corrections = [row for e in merged for batch in finished for row in batch['corrections'] if row[0] == e['id']]
    status = {'yes': 'Present', 'no': 'Absent', 'unknown': 'Approval sheet not in packet'}
    sources = {
        'ledger.md': document('Synthetic sampled payment ledger', [
            section('L-01', 'Cutoff, population and sample',
                    f'At the {cutoff.isoformat()} audit cutoff, the target population of each batch contained exactly '
                    'the number of paid entries that were not voided given in the table below.\n\n' + table(
                        ('Batch', 'Population entries', 'Sample'),
                        [(name, batch['population'], batch['scope']) for name, batch in zip(names, finished)])),
            section('L-02', 'Sampled entries', table(
                ('Entry', 'Batch', 'Payee', 'Payment date', 'Ledger amount, euro cents', 'Required approval at cutoff',
                 'Status'),
                [(e['id'], e['batch'], e['payee'], e['date'].isoformat(), e['ledger_amount'], status[e['approval']],
                  'Voided' if e['voided'] else 'Paid') for e in merged])),
            section('L-03', 'Comparison budget',
                    'The comparison budget of a batch, for its sampled payments that are not voided, is exactly the '
                    'amount given in the table below.\n\n' + table(
                        ('Batch', 'Comparison budget, euro cents'),
                        [(name, batch['budget']) for name, batch in zip(names, finished)]))]),
        'method.md': document('Synthetic review method', [
            section('M-01', 'Units and scope',
                    'All amounts are whole euro cents. Totals, counts and comparisons cover only sampled entries that '
                    'are not voided, unless a statement names the whole population. A statement that names a batch '
                    'covers only that batch: its sampled entries that are not voided, or its whole population where '
                    'the statement names the population.'),
            section('M-02', 'Corrections',
                    'A correction listed in the correction notice replaces the ledger amount of that entry for every '
                    'purpose: totals, comparisons and rankings.'),
            section('M-03', 'Approvals',
                    'An entry lacks the required approval exactly when its approval was absent at the cutoff. Where the '
                    'table in L-02 shows “Approval sheet not in packet”, the packet establishes neither that the '
                    'approval was present nor that it was absent.')]),
        'corrections.md': document('Synthetic correction notice', [
            section('K-01', 'Corrected amounts',
                    table(('Entry', 'Ledger amount, euro cents', 'Corrected amount, euro cents'), corrections)
                    if corrections else 'No correction was issued for any sampled entry.')]),
        'annex.md': document('Synthetic annex register', [batch['annex_section'] for batch in finished])}
    blocks = [{'base': batch['base'], 'unknowns': batch['unknowns'], 'slots': batch['slots'], 'rng': block_rng}
              for batch, block_rng in zip(finished, rngs)]
    return {'title': 'Synthetic payment sample review', 'sources': sources, 'blocks': blocks}
