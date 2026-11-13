"""Independent readings of rendered payment tables, including a plausible naive script.

No expected figures or rule constants are imported from the family. Statistical
checks concern these measured simple guessers, not impossibility of inference.
"""
import collections
import datetime
import functools
from pathlib import Path
import random
import re
import statistics
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
sys.path.insert(0, str(ROOT / 'skills/evidence-to-proof-review/scripts'))
import generate
generate.HARD_FAMILIES.setdefault('tables', 'lure_tables')
import blindness
import run_blind
import scorecard


REVIEWED_CONVENTIONS = """# Marshwick Community Trust — Register conventions

<a id="C-01"></a>
## C-01 — Accounting basis
Finance Office. Issued 15 January <year>. Owner: Register Services Manager.

Net disbursements for an account are the sum of its entry amounts dated within the reporting period, including both endpoints.

The reporting period is the interval stated in the covering note.

Rows whose Record cell is Subtotal or Carried forward are display totals and are not entries.

An amount enclosed in brackets is negative; a leading minus sign also denotes a negative amount.

Commas in an amount separate thousands, and every amount is in whole euros.

Dates containing slashes are written day/month/year; dates containing hyphens are written year-month-day.

A row whose Record cell says Reissue of followed by an entry reference replaces that entry in full, using the reissue row's own date, account and amount.

Remove replaced entries before selecting dates for the reporting period, and count each remaining entry once.

<a id="C-02"></a>
## C-02 — Purpose and custody
This instruction governs the quarterly disbursement schedule prepared for the trustees by the
Finance Office. It defines the figure reported from the register, rather than a bank balance or an
expense recognition policy. A payment may relate to work completed in another quarter. That does
not move its register date, and the schedule does not attempt to allocate the cost over the useful
life of the item purchased. The trust keeps its management accounts separately. Reviewers should
use those accounts for questions about depreciation, commitments or the value of goods held in
store. None of those balances forms part of this schedule.

The office retains the register in the order in which the clerks posted the documents. Posting
order can differ from the dates shown in the rows because a site may send its paperwork after
another site has already submitted a later transaction. Entry references are permanent filing
references. They do not encode the month, the account or the value of a payment. Payee names identify
the recipient recorded by the clerk; they are not account names. An account can therefore contain
payments to several suppliers, and a supplier can appear under more than one account. The report
uses the account classification printed on each entry without reallocating expenses by payee.

The paper register was designed for checking vouchers at a desk. Its export preserves features of
that register so that staff can compare the export with a retained printout. Display totals helped
clerks move between pages. Credit notes used the notation selected by the originating office.
Returned payment instructions remained visible after a replacement was issued, allowing a clerk
to find the original voucher when answering a supplier enquiry. These practices explain why a
display line and a payment instruction can occupy similar amounts of space in the exported table.
They do not change the accounting basis above.

Each entry belongs to the account stated on that row. A reissue reference identifies one earlier
entry; it is not a separate credit or an additional settlement of the same instruction. The
retained originals help reconcile correspondence about returned bank mandates. The register
describes the completed internal processing of those instructions. It does not certify the date
on which a recipient's bank made funds available. Requests for bank confirmation go to Treasury
with the entry reference and payee name, rather than with a total copied from this schedule.

The manager keeps the signed instruction with the quarterly working papers. Staff who find a
disagreement between a voucher and the register should record both references in the query book.
They should not erase an old entry or silently change a supplier's name in a circulated copy.
Corrections are posted through the register so that the next person can follow the document trail.
This copy is for the trustee review file and includes the complete register needed for the stated
period. The schedule is confined to the entries supplied here, with no estimated payments or
unposted instructions to be added by the reviewer.
"""

def sources_of(case):
    return {name[8:]: text for name, text in case['files'].items() if name.startswith('sources/')}


def rows(text):
    body = text.split('<a id="R-02"></a>', 1)[1]
    lines = [line for line in body.splitlines() if line.startswith('|')]
    cells = lambda line: [cell.strip() for cell in line.strip('|').split('|')]
    assert cells(lines[0]) == ['Entry', 'Date', 'Account', 'Payee', 'Amount EUR', 'Record']
    return [dict(zip(cells(lines[0]), cells(line))) for line in lines[2:]]


@functools.lru_cache(maxsize=1100)
def case(seed, index=1):
    return generate.build_case('tables', seed, index)


def raw(seed):
    return generate.family_builder('tables')(random.Random(
        'evalsuite:{}:{}:1:tables'.format(generate.GENERATOR_VERSION, seed)))


class Reading:
    def __init__(self, sources):
        # Pin the whole reviewed document, including scope rules in C-02.
        normalized = re.sub(r'(Issued 15 January )\d{4}(\.)', r'\1<year>\2',
                            sources['conventions.md'])
        assert normalized == REVIEWED_CONVENTIONS, 'Conventions differ from reviewed wording'
        found = re.search(r'The reporting period runs from (\d{4}-\d\d-\d\d) through '
                          r'(\d{4}-\d\d-\d\d), inclusive\.', sources['covering.md'])
        assert found
        self.start, self.end = map(datetime.date.fromisoformat, found.groups())
        self.rows = rows(sources['register.md'])
        self.replaced = {r['Record'][11:] for r in self.rows if r['Record'].startswith('Reissue of ')}
        self.true = self.total(False)
        self.naive = self.total(True)
        self.affected = {a for a in self.true if self.true[a] != self.naive[a]}

    @staticmethod
    def date(text, naive=False):
        if '-' in text:
            return datetime.date.fromisoformat(text)
        d, m, y = map(int, text.split('/'))
        if naive and d <= 12:
            d, m = m, d
        return datetime.date(y, m, d)

    @staticmethod
    def amount(text, naive=False):
        number = int(text.replace(',', '').strip('()[]'))
        return -number if not naive and text[0] in '([' else number

    def total(self, naive):
        result = {r['Account']: 0 for r in self.rows}
        for r in self.rows:
            if not naive and (r['Record'] in ('Subtotal', 'Carried forward') or r['Entry'] in self.replaced):
                continue
            if self.start <= self.date(r['Date'], naive) <= self.end:
                result[r['Account']] += self.amount(r['Amount EUR'], naive)
        return result

    @staticmethod
    def parse(text):
        m = re.fullmatch(r'Net disbursements for the (.+) account during the reporting period '
                         r'(totalled|did not total) EUR (-?[\d,]+)\.', text)
        assert m, text
        return m[1], m[2] == 'totalled', int(m[3].replace(',', ''))

    def verdict(self, text, naive=False):
        account, equal, value = self.parse(text)
        holds = ((self.naive if naive else self.true)[account] == value) == equal
        return 'supported' if holds else 'refuted'

    def echo(self, text):
        actual, naive = self.verdict(text), self.verdict(text, True)
        return naive if naive != actual else None

    def prior(self):
        """The second simple sum: the same plain reading, for dates before the period."""
        result = {r['Account']: 0 for r in self.rows}
        for r in self.rows:
            if r['Record'] not in ('Subtotal', 'Carried forward') and self.date(r['Date'], True) < self.start:
                result[r['Account']] += self.amount(r['Amount EUR'], True)
        return result

    def slip_values(self, account):
        """What each named slip prints for an account, from the entries as the conventions read them."""
        true = self.true[account]
        mine = [r for r in self.rows if r['Account'] == account and r['Entry'] not in self.replaced
                and r['Record'] not in ('Subtotal', 'Carried forward')]
        day = lambda d: sum(self.amount(r['Amount EUR']) for r in mine if self.date(r['Date']) == d)
        digits = str(true)
        return {'transposed_digits': {int(digits[:i] + digits[i + 1] + digits[i] + digits[i + 2:])
                                      for i in range(len(digits) - 1)
                                      if digits[i] != digits[i + 1] and not (i == 0 and digits[1] == '0')},
                'boundary_day_included': {true + day(self.start - datetime.timedelta(days=1))},
                'boundary_day_excluded': {true - day(self.end)}}

    def quirks(self):
        found = collections.defaultdict(set)
        for r in self.rows:
            a = r['Account']
            if r['Record'] in ('Subtotal', 'Carried forward'):
                found['subtotals'].add(a)
            if r['Amount EUR'][0] in '([':
                found['brackets'].add(a)
            if r['Record'].startswith('Reissue of '):
                found['reissue'].add(a)
            if (self.start <= self.date(r['Date']) <= self.end) != (
                    self.start <= self.date(r['Date'], True) <= self.end):
                found['dates'].add(a)
        return dict(found)


def shape(text):
    account, equal, number = Reading.parse(text)
    return text.replace(account, '<account>').replace(format(number, ','), '#').replace(str(number), '#')


def plain_twin(sources):
    """Re-express the same facts with unambiguous dates, signs and active rows."""
    reading = Reading(sources)
    result = dict(sources)
    identifiers = {row['Entry'] for row in reading.rows}
    lines = []
    for line in sources['register.md'].splitlines():
        if line.startswith('|') and line.split('|')[1].strip() in identifiers:
            values = [cell.strip() for cell in line.strip('|').split('|')]
            ident, date, account, payee, amount, record = values
            if ident in reading.replaced or record in ('Subtotal', 'Carried forward'):
                continue
            values[1] = reading.date(date).isoformat()
            values[4] = str(reading.amount(amount))
            if record.startswith('Reissue of '):
                values[5] = 'Payment'
            line = '| ' + ' | '.join(values) + ' |'
        lines.append(line)
    result['register.md'] = '\n'.join(lines) + '\n'
    return result


def features(text):
    numbers = [int(n.replace(',', '')) for n in re.findall(r'(?<!\w)\d[\d,]*', text)]
    return [len(text), len(text.split()), text.count('did not total'), text.count('totalled'),
            sum(numbers), min(numbers or [0]), max(numbers or [0]),
            statistics.mean(numbers or [0]), text.count(','),
            len(numbers) - len(set(numbers)), max(collections.Counter(numbers).values(), default=0),
            text.count('EUR -')]


def stump(train, test):
    """Fit one threshold on training seeds only, including majority prediction."""
    majority = sum(y for x, y in train) * 2 >= len(train)
    best = (sum(y == majority for x, y in train), -1, 0, majority)
    for column in range(len(train[0][0])):
        values = sorted(set(x[column] for x, y in train))
        # Fixed quantiles keep this diagnostic bounded even with thousands of claims.
        cuts = sorted(set(values[min(len(values) - 1, i * len(values) // 32)] for i in range(33)))
        for cut in cuts:
            for polarity in (False, True):
                hits = sum(((x[column] <= cut) == polarity) == y for x, y in train)
                if hits > best[0]:
                    best = hits, column, cut, polarity
    _, column, cut, polarity = best
    hits = sum((polarity if column == -1 else (x[column] <= cut) == polarity) == y for x, y in test)
    baseline = max(sum(y for x, y in test), sum(not y for x, y in test)) / len(test)
    return hits / len(test), baseline, best[1:]


def auc(positives, negatives):
    """Chance that a random positive outscores a random negative, ties counting half."""
    ordered = sorted([(v, 1) for v in positives] + [(v, 0) for v in negatives])
    ranks, i = [0.0] * len(ordered), 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1][0] == ordered[i][0]:
            j += 1
        for k in range(i, j + 1):
            ranks[k] = (i + j) / 2 + 1
        i = j + 1
    total = sum(rank for rank, (_, label) in zip(ranks, ordered) if label)
    return (total - len(positives) * (len(positives) + 1) / 2) / (len(positives) * len(negatives))


@functools.lru_cache(maxsize=None)
def claim_records(first, last):
    """Each claim with what a reader can see of its figure after computing the two simple sums.

    The reader applies no convention: it adds the rows dated in the period (brackets read as
    positive, month/day first) and, separately, the rows dated before it.
    """
    records = []
    for seed in range(first, last + 1):
        built = case(seed)
        reading = Reading(sources_of(built))
        prior = reading.prior()
        for row in built['key']['expectations']:
            account, positive, figure = reading.parse(row['claim_text'])
            others = {v for a, v in reading.naive.items() if a != account}
            relation = ('naive' if figure == reading.naive[account] else 'prior' if figure == prior[account]
                        else 'other' if figure in others else 'neither')
            records.append({'seed': seed, 'positive': positive, 'relation': relation,
                            'lure': row['echo_state'] is not None, 'wrong': row['error_type'] is not None})
    return tuple(records)


def cue_scores(records, target, split):
    """Fit on seeds up to `split`, score on the rest: what the two simple sums give away of `target`.

    The cells are polarity (totalled / did not total) crossed with the figure's relation to the
    plain sum, to the prior-quarter sum, or to neither. Reports the best rule's accuracy against
    the majority guess, and, because accuracy cannot see a partial cue, the precision and recall
    of the rule "the figure equals neither sum" and of the best single cell, and the AUC of the
    cell rates.
    """
    train = [r for r in records if r['seed'] <= split]
    test = [r for r in records if r['seed'] > split]
    cells = collections.defaultdict(lambda: [0, 0])
    for r in train:
        cell = cells[(r['positive'], r['relation'])]
        cell[0] += r[target]
        cell[1] += 1
    rate = lambda r: cells[(r['positive'], r['relation'])][0] / max(1, cells[(r['positive'], r['relation'])][1])
    base = sum(r[target] for r in test) / len(test)
    top = max(cells, key=lambda k: cells[k][0] / max(1, cells[k][1]))
    flagged = lambda r: r['relation'] in ('neither', 'other')
    in_top = lambda r: (r['positive'], r['relation']) == top
    hits = lambda rule: sum(rule(r) and r[target] for r in test)
    count = lambda rule: sum(bool(rule(r)) for r in test)
    return {'base': base, 'majority': max(base, 1 - base),
            'accuracy': sum((rate(r) > .5) == r[target] for r in test) / len(test),
            'neither_precision': hits(flagged) / max(1, count(flagged)),
            'neither_recall': hits(flagged) / max(1, sum(r[target] for r in test)),
            'top_cell': top, 'top_precision': hits(in_top) / max(1, count(in_top)),
            'top_recall': hits(in_top) / max(1, sum(r[target] for r in test)),
            'auc': auc([rate(r) for r in test if r[target]], [rate(r) for r in test if not r[target]]),
            'largest_cell_rate': max(c[0] / c[1] for c in cells.values() if c[1] >= 50)}


def marker_rows(reading):
    """Indices of rows that carry a quirk's visible marker: the marker is the fact a reader must read."""
    found = set()
    for i, r in enumerate(reading.rows):
        in_period = lambda naive: reading.start <= reading.date(r['Date'], naive) <= reading.end
        if (r['Record'] in ('Subtotal', 'Carried forward') or r['Record'].startswith('Reissue of ')
                or r['Amount EUR'][0] in '([' or in_period(False) != in_period(True)):
            found.add(i)
    return found


def account_features(reading):
    """Dates, positions and counts of an account's rows, as a reader sees them in the rendered register."""
    n, marked = len(reading.rows), marker_rows(reading)
    features = {}
    for account in sorted(reading.true):
        mine = [(i, r) for i, r in enumerate(reading.rows) if r['Account'] == account]
        plain = [(i, r) for i, r in mine if i not in marked]
        dates = lambda rows: [reading.date(r['Date']) for _, r in rows]
        mark = lambda rows, day, month: sum(d.day == day and d.month == month for d in dates(rows))
        features[account] = {
            'rows, all': len(mine), 'rows, marker rows removed': len(plain),
            'rows inside the period': sum(reading.start <= d <= reading.end for d in dates(plain)),
            'rows dated 21 May': mark(plain, 21, 5), 'rows dated 21 May, all': mark(mine, 21, 5),
            'rows dated 23 June, all': mark(mine, 23, 6),
            'most rows on one date': max(collections.Counter(dates(plain)).values()),
            'distinct dates': len(set(dates(plain))),
            'position of last row': plain[-1][0] / n, 'position of last row, all': mine[-1][0] / n,
            'mean position of rows': statistics.mean(i for i, _ in plain) / n,
            'holds the final row of the table': int(mine[-1][0] == n - 1),
            'date of the account\'s last row': dates(mine)[-1].toordinal() % 366,
            'latest date, all': max(dates(mine)).toordinal() % 366}
    return features


def feature_aucs(first, last, transform=lambda sources: sources):
    """AUC of each feature for "the quirk affects this account", over seeds first..last."""
    pairs = collections.defaultdict(lambda: ([], []))
    for seed in range(first, last + 1):
        reading = Reading(transform(sources_of(case(seed))))
        for account, values in account_features(reading).items():
            for name, value in values.items():
                pairs[name][account not in reading.affected].append(value)
    return {name: auc(*sides) for name, sides in pairs.items()}


PINNED_R9 = (12, 24.0, 36)


@functools.lru_cache(maxsize=None)
def r9_counts():
    definite, opened = [], []
    for seed in range(1, 101):
        counts = collections.Counter()
        for index in range(1, 13):
            built = generate.build_case('tables', seed, index)
            reading = Reading(sources_of(built))
            for row in built['key']['expectations']:
                if (row['semantic_state'], row['echo_state']) != (
                        reading.verdict(row['claim_text']), reading.echo(row['claim_text'])):
                    raise AssertionError((seed, index, row['claim_text']))
                if row['echo_state'] is not None:
                    counts['open' if row['semantic_state'] == 'undetermined' else 'definite'] += 1
        definite.append(counts['definite'])
        opened.append(counts['open'])
    return tuple(definite), tuple(opened)


def force_dates_of_marked_rows(sources):
    """The fault this suite once had: a quirk row dated 21 May, a reissue row last and dated 23 June."""
    reading = Reading(sources)
    marked = marker_rows(reading)
    rows = [dict(r) for r in reading.rows]
    year = reading.start.year
    for i in sorted(marked):
        row = rows[i]
        if row['Record'].startswith('Reissue of '):
            original = next(r for r in rows if r['Entry'] == row['Record'][11:])
            original['Date'] = '21/05/{}'.format(year)
            row['Date'] = '23/06/{}'.format(year)
        elif row['Record'] in ('Subtotal', 'Carried forward'):
            row['Date'] = '21/05/{}'.format(year)
            rows[i - 1]['Date'] = '21/05/{}'.format(year)
        elif row['Amount EUR'][0] in '([':
            row['Date'] = '21/05/{}'.format(year)
    replacements = [r for r in rows if r['Record'].startswith('Reissue of ')]
    rows = [r for r in rows if r not in replacements] + replacements
    head = sources['register.md'].split('| Entry |')[0]
    lines = ['| ' + ' | '.join(HEADER_NAMES) + ' |', '| ' + ' | '.join('---' for _ in HEADER_NAMES) + ' |']
    lines += ['| ' + ' | '.join(r[name] for name in HEADER_NAMES) + ' |' for r in rows]
    return dict(sources, **{'register.md': head + '\n'.join(lines) + '\n'})


HEADER_NAMES = ['Entry', 'Date', 'Account', 'Payee', 'Amount EUR', 'Record']


class TablesTests(unittest.TestCase):
    def test_independent_selected_keys_and_echoes(self):
        for seed in range(1, 61):
            built, family = case(seed), raw(seed)
            reading = Reading(sources_of(built))
            self.assertEqual(family['base'], reading.true)
            self.assertEqual(family['unknowns'], {})
            if reading.affected:
                self.assertEqual(family['told']['base'], reading.naive)
                self.assertEqual(family['told']['unknowns'], {})
                self.assertEqual(family['reader'], 'runs_naive_script')
            for row in built['key']['expectations']:
                self.assertEqual(row['semantic_state'], reading.verdict(row['claim_text']), (seed, row))
                self.assertEqual(row['echo_state'], reading.echo(row['claim_text']), (seed, row))
                self.assertEqual(row['claim_kind'], 'account_disbursements')

    def test_every_slot_variant_independent(self):
        slip_names = {'transposed_digits', 'boundary_day_included', 'boundary_day_excluded'}
        for seed in range(1, 61):
            family = raw(seed)
            reading = Reading(family['sources'])
            self.assertEqual(len(family['slots']), 8)
            fixed = [true for true, wrongs in family['slots'] if not wrongs]
            # Two matched controls and two more claims that no report alters.
            self.assertEqual(len(fixed), 4)
            self.assertEqual({reading.parse(item['text'])[1] for item in fixed}, {True, False})
            self.assertTrue(all(reading.parse(item['text'])[0] not in reading.affected for item in fixed))
            for true, wrongs in family['slots']:
                account, equal, value = reading.parse(true['text'])
                slips = reading.slip_values(account)
                everything = set().union(*slips.values()) - {reading.true[account]}
                self.assertEqual(reading.verdict(true['text']), 'supported')
                if equal:
                    self.assertEqual(value, reading.true[account])
                elif account in reading.affected:
                    self.assertEqual(value, reading.naive[account])
                else:
                    self.assertIn(value, everything)
                for wrong in wrongs:
                    self.assertEqual(wrong['kind'], true['kind'])
                    self.assertEqual(reading.verdict(wrong['text']), 'refuted')
                    wrong_a, wrong_equal, wrong_value = reading.parse(wrong['text'])
                    self.assertEqual((wrong_a, wrong_equal), (account, equal))
                    self.assertNotEqual(wrong_value, value)
                    if account in reading.affected:
                        # The script's own figure, or its denial of the true one.
                        self.assertEqual(wrong['error'], 'runs_naive_script')
                        self.assertEqual(wrong_value, reading.naive[account] if equal else reading.true[account])
                    elif equal:
                        # Each ordinary slip is exactly the figure its name says.
                        self.assertIn(wrong['error'], slip_names)
                        self.assertIn(wrong_value, slips[wrong['error']])
                    else:
                        self.assertEqual(wrong['error'], 'denies_true_total')
                        self.assertEqual(wrong_value, reading.true[account])
                    for item in (true, wrong):
                        self.assertEqual(generate.judge(item, family['base'], {})[0], reading.verdict(item['text']))
                        self.assertEqual(generate.judge(item, reading.naive, {})[0], reading.verdict(item['text'], True))

    def test_every_named_slip_occurs_and_wrong_figures_are_exact(self):
        counts = collections.Counter()
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            for row in built['key']['expectations']:
                if row['error_type'] is None:
                    continue
                account, equal, value = reading.parse(row['claim_text'])
                counts[row['error_type']] += 1
                if row['error_type'] == 'runs_naive_script':
                    self.assertEqual(value, reading.naive[account] if equal else reading.true[account])
                elif row['error_type'] == 'denies_true_total':
                    self.assertFalse(equal)
                    self.assertEqual(value, reading.true[account])
                else:
                    self.assertTrue(equal)
                    self.assertIn(value, reading.slip_values(account)[row['error_type']])
        print('Tables wrong-figure kinds over seeds 1..300:', dict(sorted(counts.items())))
        self.assertEqual(set(counts), {'runs_naive_script', 'denies_true_total', 'transposed_digits',
                                       'boundary_day_included', 'boundary_day_excluded'})
        self.assertGreaterEqual(min(counts.values()), 20)

    def test_same_seed_same_case_another_seed_another(self):
        for seed in range(1, 301):
            self.assertEqual(case(seed), generate.build_case('tables', seed, 1))
            if seed > 1:
                self.assertNotEqual(case(seed)['files'], case(seed - 1)['files'])

    def test_correct_reports_hold_only_supported_and_altered_reports_typed_errors(self):
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            for row in built['key']['expectations']:
                if row['variant'] == 'correct':
                    self.assertEqual(row['semantic_state'], 'supported')
                if row['semantic_state'] == 'supported':
                    self.assertIsNone(row['error_type'])
                else:
                    self.assertEqual(row['semantic_state'], 'refuted')
                    account, equal, _ = reading.parse(row['claim_text'])
                    if account in reading.affected:
                        self.assertEqual(row['error_type'], 'runs_naive_script')
                    else:
                        self.assertIn(row['error_type'], ('denies_true_total', 'transposed_digits',
                                                          'boundary_day_included', 'boundary_day_excluded'))
                        self.assertEqual(row['error_type'] == 'denies_true_total', not equal)

    def test_both_reports_same_claim_kinds_and_slots(self):
        for seed in range(1, 301):
            expectations = case(seed)['key']['expectations']
            by_variant = [[r for r in expectations if r['variant'] == v] for v in ('correct', 'mixed')]
            self.assertEqual(len(by_variant[0]), len(by_variant[1]))
            for correct, mixed in zip(*by_variant):
                self.assertEqual(correct['claim_id'], mixed['claim_id'])
                self.assertEqual(correct['claim_kind'], mixed['claim_kind'])
                self.assertEqual(Reading.parse(correct['claim_text'])[0], Reading.parse(mixed['claim_text'])[0])

    def test_report_and_sources_have_no_evaluator_leaks_or_none(self):
        for seed in range(1, 301):
            for name, text in case(seed)['files'].items():
                if name.startswith('report-'):
                    self.assertNotIn('None', text)
                self.assertNotRegex(text, r'evaluator_only|echo_state|semantic_state|runs_naive_script|denies_true_total|transposed_digits')

    def test_no_singleton_polarity_in_whole_reports(self):
        contexts = collections.defaultdict(set)
        for seed in range(1, 601):
            built = case(seed)
            for variant in ('correct', 'mixed'):
                report = built['files']['report-' + variant + '.md']
                texts = re.findall(r'Net disbursements for the [^.]+\.', report)
                counts = collections.Counter(Reading.parse(text)[1] for text in texts)
                self.assertEqual(set(counts), {False, True})
                self.assertGreaterEqual(min(counts.values()), 2, (seed, variant, counts))
                for row in built['key']['expectations']:
                    if row['variant'] == variant:
                        polarity = Reading.parse(row['claim_text'])[1]
                        contexts[(polarity, counts[polarity])].add(row['semantic_state'])
        # Even minority wording at the new boundary can be right or wrong.
        for states in contexts.values():
            self.assertEqual(states, {'supported', 'refuted'})

    def test_whole_visible_report_framing_is_fixed(self):
        expected = '# Marshwick Community Trust — Disbursement review\n\n'
        for number in range(1, 9):
            expected += ('<a id="C{0:02d}"></a>\n**C{0:02d}.** <claim> '
                         'Citations: register R-02; conventions C-01; covering N-01.\n').format(number)
            if number < 8:
                expected += '\n'
        for seed in range(1, 301):
            for variant in ('correct', 'mixed'):
                report = case(seed)['files']['report-' + variant + '.md']
                framing = re.sub(r'Net disbursements for the [^.]+\.', '<claim>', report)
                self.assertEqual(framing, expected, (seed, variant))

    def test_complete_conventions_reject_changed_missing_and_added_rules(self):
        sources = sources_of(case(1))
        Reading(sources)
        original = sources['conventions.md']
        faults = [
            original.replace('Record cell says Reissue of', 'Record cell says Cancellation of'),
            original.replace('Remove replaced entries before selecting dates for the reporting period, '
                             'and count each remaining entry once.', ''),
            original.replace('## C-02 — Purpose and custody',
                             '## C-02 — Purpose and custody\nIgnore the accounting basis above.'),
        ]
        for changed in faults:
            self.assertNotEqual(changed, original)
            with self.assertRaisesRegex(AssertionError, 'Conventions differ'):
                Reading(dict(sources, **{'conventions.md': changed}))

    def test_size_closed_facts_quirk_frequency_and_uniform_citations(self):
        absent = 0
        for seed in range(1, 301):
            built = case(seed)
            sources = sources_of(built)
            reading = Reading(sources)
            self.assertTrue(120 <= len(reading.rows) <= 250)
            self.assertGreaterEqual(sum(len(t.split()) for n, t in sources.items() if n != 'register.md'), 1500)
            self.assertEqual(len(reading.true), 8)
            self.assertEqual(len({r['Entry'] for r in reading.rows}), len(reading.rows))
            quirks = reading.quirks()
            absent += not quirks
            self.assertIn(len(quirks), (0, 1, 2))
            self.assertEqual(set().union(*quirks.values()) if quirks else set(), reading.affected)
            self.assertEqual(sum(len(a) for a in quirks.values()), len(quirks))
            for variant in ('correct', 'mixed'):
                selected = [r for r in built['key']['expectations'] if r['variant'] == variant]
                self.assertEqual(len(selected), 8)
                self.assertEqual({reading.parse(r['claim_text'])[0] for r in selected}, set(reading.true))
                report = built['files']['report-' + variant + '.md']
                for row in selected:
                    self.assertIn(row['claim_text'], report)
                    self.assertTrue({'sources/register.md#R-02', 'sources/conventions.md#C-01'} <= set(row['source_anchors']))
                    self.assertEqual(row['source_anchors'], selected[0]['source_anchors'])
                    for anchor in row['source_anchors']:
                        name, section = anchor.split('#')
                        self.assertIn('<a id="' + section + '"></a>', built['files'][name])
                    self.assertEqual(row['semantic_state'], reading.verdict(row['claim_text']))
                    self.assertEqual(row['echo_state'], reading.echo(row['claim_text']))
                self.assertGreaterEqual(sum(r['semantic_state'] == 'refuted' for r in selected), 2 if variant == 'mixed' else 0)
        print('Tables no-lure dossiers:', absent, '/ 300')
        # A prespecified three-standard-error binomial interval for p=.30.
        self.assertTrue(.22 <= absent / 300 <= .38, absent)

    def test_no_wording_one_sided(self):
        shapes = collections.defaultdict(set)
        for seed in range(1, 301):
            reading = Reading(sources_of(case(seed)))
            for row in case(seed)['key']['expectations']:
                shapes[shape(row['claim_text'])].add(reading.verdict(row['claim_text']))
        self.assertEqual(len(shapes), 2)
        self.assertTrue(all(states == {'supported', 'refuted'} for states in shapes.values()))

    def test_matched_controls_and_all_quirk_directions(self):
        directions = collections.defaultdict(set)
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            account_quirk = {a: q for q, accounts in reading.quirks().items() for a in accounts}
            for variant in ('correct', 'mixed'):
                selected = [r for r in built['key']['expectations'] if r['variant'] == variant]
                for row in selected:
                    text = row['claim_text']
                    actual, echo = reading.verdict(text), reading.echo(text)
                    if echo:
                        account = reading.parse(text)[0]
                        directions[account_quirk[account]].add((actual, echo))
                        self.assertTrue(any(shape(other['claim_text']) == shape(text)
                                            and reading.echo(other['claim_text']) is None for other in selected),
                                        (seed, variant, text))
        self.assertEqual(set(directions), {'subtotals', 'brackets', 'dates', 'reissue'})
        for seen in directions.values():
            self.assertEqual(seen, {('supported', 'refuted'), ('refuted', 'supported')})

    def test_plain_twins_remove_lures(self):
        from lure_tables import naive_figures
        for seed in range(1, 61):
            sources = sources_of(case(seed))
            reading = Reading(sources)
            self.assertEqual(naive_figures(sources['register.md'], reading.start, reading.end), reading.naive)
            twin = plain_twin(sources)
            canonical = Reading(twin)
            self.assertEqual(canonical.true, reading.true)
            self.assertEqual(canonical.naive, reading.true)
            self.assertEqual(naive_figures(twin['register.md'], reading.start, reading.end), reading.true)

    def test_rendered_mutation_changes_both_readers_verdicts(self):
        from lure_tables import naive_figures
        for seed in range(1, 61):
            sources = sources_of(case(seed))
            reading = Reading(sources)
            twin = plain_twin(sources)
            canonical = Reading(twin)
            # Change a live, in-period payment amount, leaving the old claims
            # untouched. Both interpretations must now see the rendered change.
            pick = next(r for r in canonical.rows if r['Record'] == 'Payment'
                        and reading.start <= canonical.date(r['Date']) <= reading.end)
            altered = dict(twin)
            old = '| ' + ' | '.join(pick.values()) + ' |'
            new_row = dict(pick)
            delta = 1000003
            new_row['Amount EUR'] = str(int(pick['Amount EUR']) + delta)
            new = '| ' + ' | '.join(new_row.values()) + ' |'
            self.assertEqual(altered['register.md'].count(old), 1)
            altered['register.md'] = altered['register.md'].replace(old, new, 1)
            changed = Reading(altered)
            want = dict(canonical.true)
            want[pick['Account']] += delta
            self.assertEqual(changed.true, want)
            self.assertEqual(changed.naive, want)
            self.assertEqual(naive_figures(altered['register.md'], reading.start, reading.end), want)
            text = 'Net disbursements for the {} account during the reporting period totalled EUR {}.'.format(
                pick['Account'], canonical.true[pick['Account']])
            for naive in (False, True):
                self.assertEqual(canonical.verdict(text, naive), 'supported')
                self.assertEqual(changed.verdict(text, naive), 'refuted')

    def test_r9_100_seeds_twelve_indices_definite_and_open(self):
        definite, opened = r9_counts()
        stats = lambda values: (min(values), statistics.median(values), max(values))
        print('Tables R9 definite min/median/max:', stats(definite), '; open:', stats(opened))
        self.assertGreaterEqual(statistics.median(definite), 15)
        # Pinned to the measured numbers; the band test below says how far they may move.
        self.assertEqual(stats(definite), PINNED_R9)
        self.assertEqual(stats(opened), (0, 0, 0))

    def test_r9_counts_stay_in_band(self):
        definite, opened = r9_counts()
        self.assertTrue(18 <= statistics.median(definite) <= 30, statistics.median(definite))
        self.assertGreaterEqual(min(definite), 6)
        self.assertLessEqual(max(definite), 40)
        self.assertEqual(sum(opened), 0)

    def test_heldout_report_and_metadata_guessers(self):
        datasets = collections.defaultdict(lambda: ([], []))
        source_names = sorted(sources_of(case(1)))
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            # Staging supplies report.md in both variants. Source names are
            # checked constant rather than encoded as an arbitrary numeric ID.
            self.assertEqual(sorted(sources_of(built)), source_names)
            metadata = [len(sources_of(built))]
            for name, text in sorted(sources_of(built).items()):
                metadata += [len(name), len(text), len(text.split())]
            for variant in ('correct', 'mixed'):
                report = built['files']['report-' + variant + '.md']
                rf = features(report)
                visible_claims = re.findall(r'Net disbursements for the [^.]+\.', report)
                accounts = sorted(Reading.parse(text)[0] for text in visible_claims)
                for mode in ('report', 'report+metadata'):
                    extra = metadata if mode == 'report+metadata' else []
                    for target, label in [('altered_report', variant == 'mixed'), ('dossier_lure', bool(reading.affected))]:
                        datasets[(mode, target)][seed > 200].append((rf + extra, label))
                    for row in [r for r in built['key']['expectations'] if r['variant'] == variant]:
                        account = Reading.parse(row['claim_text'])[0]
                        # Visible claim position, account rank and one-hot names
                        # are allowed cues; hidden slot designation is not.
                        position = visible_claims.index(row['claim_text'])
                        identity = [position, accounts.index(account)] + [int(account == a) for a in accounts]
                        for target, label in [('wrong_claim', reading.verdict(row['claim_text']) == 'refuted'),
                                              ('lure_claim', reading.echo(row['claim_text']) is not None)]:
                            datasets[(mode, target)][seed > 200].append((features(row['claim_text']) + rf + identity + extra, label))
        for label, (train, test) in sorted(datasets.items()):
            accuracy, base, rule = stump(train, test)
            print('Tables heldout', label, 'accuracy/base:', round(accuracy, 3), round(base, 3), 'stump:', rule)
            # Diagnostic tolerance, not proof of no predictive advantage:
            # 100 heldout seed clusters, not 1600 independent claim observations.
            self.assertLessEqual(accuracy, base + .12, (label, accuracy, base, rule))

    def test_figure_relation_to_the_two_simple_sums_leaves_only_the_stated_partial_cue(self):
        """A reader that adds the register twice, without a convention, learns little about "which claim is a lure".

        Sums used: the plain sum of the period and the plain sum before it. Fitted on seeds 1..200,
        scored on 201..400. The old design failed this: every figure equal to neither sum was a lure.
        """
        records = claim_records(1, 400)
        lure, wrong = cue_scores(records, 'lure', 200), cue_scores(records, 'wrong', 200)
        show = lambda scores: {k: round(v, 3) if isinstance(v, float) else v for k, v in scores.items()}
        print('Tables simple-sums reader, which claim is a lure:', show(lure))
        print('Tables simple-sums reader, which claim is wrong:', show(wrong))
        self.assertLessEqual(lure['accuracy'], lure['majority'] + .01)
        self.assertLessEqual(lure['neither_precision'], 1.25 * lure['base'])
        self.assertLessEqual(lure['top_precision'], .5)
        self.assertLessEqual(lure['largest_cell_rate'], .5)
        # A partial cue remains and is stated in the module docstring: a claim the plain sum refutes is more
        # often a lure than one it supports, because the correct report holds no wrong claim to dilute it.
        self.assertLessEqual(lure['auc'], .76)
        # A figure equal to neither sum is ordinary on claims that are not lures.
        ordinary = [r for r in records if not r['lure']]
        self.assertGreaterEqual(sum(r['relation'] == 'neither' for r in ordinary) / len(ordinary), .3)
        # No figure is the prior-quarter sum or another account's plain sum: either would be a free
        # "not a lure" flag, because no lure claim states one.
        self.assertLess(sum(r['relation'] in ('prior', 'other') for r in records) / len(records), .01)

    def test_simple_sums_measure_sees_the_old_prior_quarter_fingerprint(self):
        records = claim_records(1, 400)
        # The old rule: every wrong figure that was not a lure was the prior-quarter sum.
        faulted = [dict(r, relation='prior') if r['relation'] == 'neither' and not r['lure'] else r for r in records]
        scores = cue_scores(faulted, 'lure', 200)
        self.assertGreater(scores['neither_precision'], .9)
        self.assertGreater(scores['auc'], .9)
        self.assertGreater(scores['accuracy'], scores['majority'] + .03)
        self.assertGreater(scores['neither_precision'], 1.5 * scores['base'])

    def test_number_of_negative_claims_in_a_report_does_not_show_a_quirk(self):
        # A polarity mix that depends on whether an account is affected would show a quirk in the report alone.
        negatives, quirks = [], []
        for seed in range(1, 401):
            built = case(seed)
            reading = Reading(sources_of(built))
            for variant in ('correct', 'mixed'):
                texts = [r['claim_text'] for r in built['key']['expectations'] if r['variant'] == variant]
                negatives.append(sum(not Reading.parse(t)[1] for t in texts))
                quirks.append(len(reading.affected))
        presence = auc([n for n, q in zip(negatives, quirks) if q], [n for n, q in zip(negatives, quirks) if not q])
        two = auc([n for n, q in zip(negatives, quirks) if q == 2], [n for n, q in zip(negatives, quirks) if q < 2])
        print('Tables AUC of the number of "did not total" claims, quirk present / two quirks:',
              round(presence, 3), round(two, 3))
        self.assertLessEqual(abs(presence - .5), .05)
        self.assertLessEqual(abs(two - .5), .05)

    def test_slip_menu_reader_limit_is_the_documented_one(self):
        """A reader that knows the four slips and applies them to the plain sum explains the ordinary wrong figures."""
        cells = collections.defaultdict(lambda: [0, 0])
        for seed in range(1, 401):
            built = case(seed)
            reading = Reading(sources_of(built))
            for row in built['key']['expectations']:
                account, _, figure = reading.parse(row['claim_text'])
                plain = reading.naive[account]
                digits = str(plain)
                day = lambda d: sum(reading.amount(r['Amount EUR'], True) for r in reading.rows
                                    if r['Account'] == account and reading.date(r['Date'], True) == d)
                menu = ({reading.naive[a] for a in reading.naive if a != account}
                        | {int(digits[:i] + digits[i + 1] + digits[i] + digits[i + 2:]) for i in range(len(digits) - 1)}
                        | {plain + day(reading.start - datetime.timedelta(days=1)), plain - day(reading.end)})
                cell = cells['plain' if figure == plain else 'slip of the plain sum' if figure in menu else 'unexplained']
                cell[0] += row['echo_state'] is not None
                cell[1] += 1
        total = sum(c[1] for c in cells.values())
        print('Tables slip-menu reader, lure rate by cell:', {k: (v[1], round(v[0] / v[1], 3)) for k, v in cells.items()},
              'base', round(sum(c[0] for c in cells.values()) / total, 3))
        # Documented limit (module docstring): a figure that is a slip of the plain sum is never a lure claim,
        # so a reader that knows the exact list of slips rules out about 40% of the claims, and what it cannot
        # explain is almost all lures. An ordinary reader does not know the list; the test pins the limit so
        # that a change in its size is noticed.
        self.assertEqual(cells['slip of the plain sum'][0], 0)
        self.assertGreaterEqual(cells['unexplained'][0] / cells['unexplained'][1], .95)
        self.assertTrue(.04 <= cells["unexplained"][1] / total <= .10, cells)

    def test_quirk_row_dates_positions_and_counts_are_alike_for_affected_and_other_accounts(self):
        aucs = feature_aucs(1, 300)
        print('Tables feature AUCs for "the quirk affects this account":', {k: round(v, 3) for k, v in aucs.items()})
        # Counting only rows that carry no quirk marker removes one row from an affected account, which shows
        # in the counts below. The marker is the fact a reader has to read, so those counts are bounded more
        # loosely than dates and positions.
        removal = {'rows, marker rows removed', 'rows inside the period', 'distinct dates'}
        for name, value in aucs.items():
            self.assertLessEqual(abs(value - .5), .12 if name in removal else .07, (name, value))

    def test_neutrality_measure_sees_the_old_forced_dates(self):
        aucs = feature_aucs(1, 300, force_dates_of_marked_rows)
        self.assertGreater(aucs['rows dated 21 May, all'], .7)
        self.assertGreater(aucs['rows dated 21 May'], .6)
        self.assertGreater(abs(aucs['holds the final row of the table'] - .5), .07)
        self.assertGreater(max(abs(v - .5) for v in aucs.values()), .2)

    def test_quirk_rows_keep_their_own_dates_and_reissues_sit_anywhere_after_their_entries(self):
        reissues, final, brackets, subtotals = [], [], [], []
        for seed in range(1, 301):
            reading = Reading(sources_of(case(seed)))
            for i, r in enumerate(reading.rows):
                when = reading.date(r['Date'])
                if r['Record'].startswith('Reissue of '):
                    original = next(j for j, x in enumerate(reading.rows) if x['Entry'] == r['Record'][11:])
                    self.assertGreater(i, original)
                    self.assertTrue(reading.start <= reading.date(reading.rows[original]['Date']) <= reading.end)
                    reissues.append((when.month, when.day))
                    final.append(i == len(reading.rows) - 1)
                elif r['Amount EUR'][0] in '([':
                    self.assertTrue(reading.start <= when <= reading.end)
                    brackets.append((when.month, when.day))
                elif r['Record'] in ('Subtotal', 'Carried forward'):
                    self.assertTrue(reading.start <= when <= reading.end)
                    subtotals.append((when.month, when.day))
        for dates in (reissues, brackets, subtotals):
            self.assertGreaterEqual(len(dates), 50)
            self.assertGreaterEqual(len(set(dates)), 30)
            self.assertLess(max(collections.Counter(dates).values()) / len(dates), .08)
        self.assertLess(sum(final) / len(final), .25)

    def test_conventions_covering_note_and_narrative_are_alike_in_every_dossier(self):
        mask = lambda text: re.sub(r'\d{4}-\d{2}-\d{2}|\b20\d\d\b', '<date>', text)
        seen = collections.defaultdict(set)
        for seed in range(1, 301):
            for name, text in sources_of(case(seed)).items():
                seen[name].add(mask(text.split('<a id="R-02"></a>')[0] if name == 'register.md' else text))
        self.assertEqual({name: len(texts) for name, texts in seen.items()},
                         {'conventions.md': 1, 'covering.md': 1, 'period.md': 1, 'register.md': 1})

    def test_polarity_count_cue_is_the_size_stated_in_the_docstring(self):
        counts = collections.defaultdict(lambda: [0, 0])
        for seed in range(1, 401):
            built = case(seed)
            mixed = [r for r in built['key']['expectations'] if r['variant'] == 'mixed']
            polarities = [Reading.parse(r['claim_text'])[1] for r in mixed]
            for row, polarity in zip(mixed, polarities):
                cell = counts[polarities.count(polarity)]
                cell[0] += row['error_type'] is not None
                cell[1] += 1
        rates = {k: v[0] / v[1] for k, v in counts.items()}
        print('Tables wrong rate in the mixed report by number of claims sharing the polarity:',
              {k: round(v, 3) for k, v in sorted(rates.items())})
        self.assertTrue(.18 <= rates[2] <= .33, rates)
        self.assertTrue(.38 <= rates[6] <= .53, rates)

    def test_every_echo_row_names_the_family_reader(self):
        for seed in range(1, 301):
            for row in case(seed)['key']['expectations']:
                if row['echo_state'] is not None:
                    self.assertEqual(row['echo_reader'], 'runs_naive_script')
                else:
                    self.assertNotIn('echo_reader', row)
                self.assertLessEqual(set(row.get('lure_states', {})), {'runs_naive_script'})

    def test_report_paragraph_splitting_matches_audit_helper(self):
        import audit
        for seed in range(1, 61):
            for variant in ('correct', 'mixed'):
                report = case(seed)['files']['report-' + variant + '.md']
                self.assertEqual(run_blind.paragraphs(report), audit.paragraphs(report))

    def test_restatement_of_claims_does_not_trigger_contamination(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='tables-restatement-', dir=ROOT / 'tmp') as temp:
            root = Path(temp)
            for seed in (1, 2, 3):
                built = case(seed)
                run = root / str(seed)
                (run / 'case').mkdir(parents=True)
                (run / 'case/report.md').write_text(built['files']['report-mixed.md'])
                selected = [r for r in built['key']['expectations'] if r['variant'] == 'mixed']
                (run / 'notes.md').write_text('\n'.join(r['claim_text'] + ' Verdict: ' + r['semantic_state'] for r in selected))
                self.assertEqual(blindness.scan_run(run, built['key']['expectations'])['verbatim_overlaps'], [])
                (run / 'copied.md').write_text(selected[0]['rationale'])
                self.assertTrue(blindness.scan_run(run, built['key']['expectations'])['verbatim_overlaps'])

    def test_scorecard_load_seal_and_stage(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='tables-tests-', dir=ROOT / 'tmp') as temp:
            root = Path(temp)
            fixtures, keys, arena, sealed = [root / n for n in ('fixtures', 'keys', 'arena', 'sealed')]
            summary = generate.write_suite(fixtures, keys, 9, 3, ['tables'])
            loaded = scorecard.load_keys(keys)
            self.assertEqual(set(loaded), set(summary['dossiers']))
            self.assertEqual(summary['claims'], 48)
            arena.mkdir()
            blindness.seal_keys(keys, sealed, [arena])
            for dossier in summary['dossiers']:
                for variant, other in (('correct', 'mixed'), ('mixed', 'correct')):
                    report = fixtures / dossier / ('report-' + variant + '.md')
                    record = blindness.stage_case(report, fixtures / dossier / 'sources', arena, root / 'records',
                        {'dossier': dossier, 'variant': variant},
                        withheld=[sealed, fixtures / dossier / ('report-' + other + '.md')])
                    run = Path(record['arena_run'])
                    self.assertEqual((run / 'case/report.md').read_bytes(), report.read_bytes())
                    self.assertNotIn(dossier, record['arena_run'])
                    self.assertEqual(sorted(p.name for p in (run / 'case/sources').iterdir()),
                                     sorted(p.name for p in (fixtures / dossier / 'sources').iterdir()))
            self.assertFalse(list(fixtures.rglob('*.json')))


if __name__ == '__main__':
    unittest.main()
