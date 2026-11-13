"""Independent rendered-document checks for repair lures.

Reading accepts Markdown and claim sentences only. Builder objects are used on
comparison sides, never as premises for the second derivation. Finite leakage
probes test learned visible features; they cannot establish classifier immunity.
"""
import collections
import functools
import math
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
generate.HARD_FAMILIES.setdefault('repair', 'lure_repair')
import blindness
import run_blind
import scorecard


@functools.lru_cache(maxsize=1100)
def case(seed, index=1):
    return generate.build_case('repair', seed, index)


def sources_of(built):
    return {name[8:]: body for name, body in built['files'].items() if name.startswith('sources/')}


def raw_case(seed, index=1):
    return generate.family_builder('repair')(random.Random(
        'evalsuite:{}:{}:{}:repair'.format(generate.GENERATOR_VERSION, seed, index)))


def claims(report):
    return re.findall(r'\*\*C\d+\.\*\* (.*?) Citations?: ', report)


def sections(text):
    parts = re.split(r'<a id="([^"]+)"></a>\n', text)
    return dict(zip(parts[1::2], parts[2::2]))


KINDS = {'compound_amount', 'compound_date', 'compound_name', 'compound_reference',
         'budget_tolerance', 'allocation_balance'}
DETAIL_KINDS = ('compound_amount', 'compound_date', 'compound_name', 'compound_reference')
SLOTS = 28
READER = 'repairs_the_claim'
DOCUMENTS = {'funds.md', 'procedure.md', 'settlement.md'}


def shape(text):
    text = re.sub(r'Invoice \d+', 'Invoice <id>', text)
    text = re.sub(r'\(EUR \d+\)', '(EUR <amount>)', text)
    text = re.sub(r'\(issued [^)]+\)', '(issued <date>)', text)
    text = re.sub(r'\(supplier [^)]+\)', '(supplier <name>)', text)
    text = re.sub(r'\(order [^)]+\)', '(order <reference>)', text)
    return re.sub(r'(the |from the )[A-Z][a-z]+ (programme|fund)', r'\1<programme> \2', text)


class Reading:
    """Use settlement prose and programme instructions as the sole premises."""

    def __init__(self, sources):
        assert set(sources) == DOCUMENTS, sorted(sources)
        for rule in (
            'A report statement is correct if and only if every assertion in it is correct, including amounts, dates, names and references given in parentheses.',
            'For this review, the settlement record establishes invoice details and payment status at the checkpoint; the dispatch history does not replace that record.',
            'An invoice was paid by the checkpoint if and only if the settlement record gives its checkpoint status as paid; a status of open means that it was unpaid at the checkpoint.',
            'Differences retain their sign: the budget margin is the approved budget minus actual spending, including a negative margin when spending exceeds the budget.',
            'Spending is within its budget tolerance if and only if the absolute value of the budget margin is at most the tolerance; otherwise it is outside its budget tolerance.',
            'Equal allocations are exact rational shares unless the fund instruction explicitly requires each share to be rounded down to whole euros.',
            'Allocations in this review are internal accounting entitlements, not cash transfers.',
            'No rounding to cents is applied.',
            'Allocations exhaust a fund if and only if their sum equals the fund total; they leave a balance if and only if their sum is less than that total.'):
            assert rule in sources['procedure.md'], rule
        self.invoices = {}
        for ident, name, date, amount, reference, dispatch, status in re.findall(
                r'The signed voucher for invoice (\d+) names supplier ([^;]+); its issue date is ([^,]+), '
                r'its amount is EUR (\d+), and its purchase order is ([^.]+)\. '
                r'Dispatch history: (paid|open)\. Checkpoint status: (paid|open)\. ', sources['settlement.md']):
            self.invoices[ident] = dict(name=name, date=date, amount=amount, reference=reference,
                                       paid=status == 'paid', dispatch=dispatch == 'paid')
        assert len(self.invoices) == 24
        self.budgets, self.funds = {}, {}
        for body in sections(sources['funds.md']).values():
            found = re.search(r'The (\w+) programme operated', body)
            if found:
                amounts = [int(re.search(label + r': EUR (\d+)\.', body)[1])
                           for label in ('Approved budget', 'Actual spending', 'Budget tolerance')]
                self.budgets[found[1]] = tuple(amounts)
            found = re.search(r'The (\w+) fund finances', body)
            if found:
                total = int(re.search(r'Fund total: EUR (\d+)\.', body)[1])
                recipients = int(re.search(r'Recipient teams: (\d+)\.', body)[1])
                instruction = re.search(r'Fund instruction: ([^.]+)\.', body)[1]
                assert instruction in ('Round shares down, with fractional euros withheld for every team',
                                       'Keep shares exact, with fractional euros retained for every team')
                self.funds[found[1]] = (total, recipients, instruction.startswith('Round'))
        assert len(self.budgets) == len(self.funds) == 10

    @staticmethod
    def parse(text):
        m = re.fullmatch(r'Invoice (\d+) \((EUR|issued|supplier|order) ([^)]+)\) '
                         r'was paid by the checkpoint of 2034-10-31\.', text)
        if m:
            kind = {'EUR': 'amount', 'issued': 'date', 'supplier': 'name', 'order': 'reference'}[m[2]]
            return 'compound_' + kind, m[1], (True, m[3])
        m = re.fullmatch(r'Spending on the (\w+) programme was (within|outside) its budget tolerance\.', text)
        if m:
            return 'budget_tolerance', m[1], m[2] == 'within'
        m = re.fullmatch(r'The equal allocations from the (\w+) fund (exhausted the fund|left a balance in the fund)\.', text)
        assert m, text
        return 'allocation_balance', m[1], m[2] == 'exhausted the fund'

    def verdict(self, text, repair=False, ignore_detail=False, natural_arithmetic=False):
        kind, ident, assertion = self.parse(text)
        ignore_detail = ignore_detail or repair
        natural_arithmetic = natural_arithmetic or repair
        if kind.startswith('compound_'):
            paid, detail = assertion
            row = self.invoices[ident]
            holds = paid == row['paid'] and (ignore_detail or detail == row[kind[len('compound_'):]])
        elif kind == 'budget_tolerance':
            budget, spent, tolerance = self.budgets[ident]
            # Compare the overspend or underspend directly; no shared engine.
            distance = (max(budget - spent, 0) if natural_arithmetic else abs(spent - budget))
            holds = (distance <= tolerance) == assertion
        else:
            total, teams, round_down = self.funds[ident]
            # Exact equal shares sum to the total; whole-euro shares do so iff divisible.
            exhausted = (total % teams == 0) if natural_arithmetic or round_down else True
            holds = exhausted == assertion
        return 'supported' if holds else 'refuted'

    def echo(self, text):
        actual, repaired = self.verdict(text), self.verdict(text, repair=True)
        return repaired if actual != repaired else None


# Reviewed wording of everything in a report that is not a claim sentence. The
# citation of a claim has one of three forms, fixed by the kind of claim.
REVIEWED_REPORT_TITLE = '# Mereford Community Trust — October finance review'
REVIEWED_CITATION_FORMS = {
    'invoice': 'Citations: settlement S-<ID>; procedure P-01.',
    'budget': 'Citations: funds F-<ID>; procedure P-02.',
    'fund': 'Citations: funds F-<ID>; procedure P-03.',
}
REVIEWED_CLAIM_FORMS = {
    'invoice': r'Invoice [0-9]{4} \((?:EUR [0-9]+|issued 2034-[0-9]{2}-[0-9]{2}|supplier [A-Z][a-z]+ [A-Z][a-z]+|order PO-[0-9]{4})\) '
               r'was paid by the checkpoint of 2034-10-31\.',
    'budget': r'Spending on the [A-Z][a-z]+ programme was (?:within|outside) its budget tolerance\.',
    'fund': r'The equal allocations from the [A-Z][a-z]+ fund (?:exhausted the fund|left a balance in the fund)\.',
}
# The whole procedure, section by section and sentence by sentence.
REVIEWED_PROCEDURE_TITLE = 'Mereford Community Trust — finance review procedure'
REVIEWED_PROCEDURE_SECTIONS = (
    ('P-01', 'Authority and purpose', (
        'Finance Office, issued 1 October 2034.',
        'This procedure governs the monthly review sent to the trustees.',
        'It keeps the assessment of a written statement separate from the decision to release money.',
        'A reviewer records what the retained documents establish at the checkpoint, rather than predicting the next payment run.',
        'A report statement is correct if and only if every assertion in it is correct, including amounts, dates, names and references given in parentheses.',
        'For this review, the settlement record establishes invoice details and payment status at the checkpoint; the dispatch history does not replace that record.',
        'An invoice was paid by the checkpoint if and only if the settlement record gives its checkpoint status as paid; a status of open means that it was unpaid at the checkpoint.')),
    ('P-02', 'Budget comparisons', (
        'Differences retain their sign: the budget margin is the approved budget minus actual spending, including a negative margin when spending exceeds the budget.',
        'Spending is within its budget tolerance if and only if the absolute value of the budget margin is at most the tolerance; otherwise it is outside its budget tolerance.')),
    ('P-03', 'Allocation accounting', (
        'Equal allocations are exact rational shares unless the fund instruction explicitly requires each share to be rounded down to whole euros.',
        'Allocations exhaust a fund if and only if their sum equals the fund total; they leave a balance if and only if their sum is less than that total.',
        'Allocations in this review are internal accounting entitlements, not cash transfers.',
        'No rounding to cents is applied.',
        'The fund instruction in the programme accounts states whether whole-euro rounding is required.',
        'A later cash payment is a separate transaction and is outside the present review.')),
)
# Inverted premises: the same sentence with its meaning reversed.
REVIEWED_INVERSIONS = {
    'A report statement is correct if and only if every assertion in it is correct, including amounts, dates, names and references given in parentheses.':
        'A report statement is correct if its main assertion is correct, whatever amounts, dates, names and references are given in parentheses.',
    'For this review, the settlement record establishes invoice details and payment status at the checkpoint; the dispatch history does not replace that record.':
        'For this review, the dispatch history establishes invoice details and payment status at the checkpoint; the settlement record does not replace it.',
    'An invoice was paid by the checkpoint if and only if the settlement record gives its checkpoint status as paid; a status of open means that it was unpaid at the checkpoint.':
        'An invoice was paid by the checkpoint if and only if the settlement record gives its checkpoint status as open; a status of paid means that it was unpaid at the checkpoint.',
    'Differences retain their sign: the budget margin is the approved budget minus actual spending, including a negative margin when spending exceeds the budget.':
        'Differences are floored at zero: the budget margin is the approved budget minus actual spending, and a margin is never negative.',
    'Spending is within its budget tolerance if and only if the absolute value of the budget margin is at most the tolerance; otherwise it is outside its budget tolerance.':
        'Spending is within its budget tolerance if and only if the absolute value of the budget margin is more than the tolerance; otherwise it is outside its budget tolerance.',
    'Equal allocations are exact rational shares unless the fund instruction explicitly requires each share to be rounded down to whole euros.':
        'Equal allocations are rounded down to whole euros unless the fund instruction explicitly requires each share to be an exact rational share.',
    'Allocations exhaust a fund if and only if their sum equals the fund total; they leave a balance if and only if their sum is less than that total.':
        'Allocations exhaust a fund if and only if their sum is less than the fund total; they leave a balance if and only if their sum equals that total.',
    'Allocations in this review are internal accounting entitlements, not cash transfers.':
        'Allocations in this review are cash transfers, not internal accounting entitlements.',
    'No rounding to cents is applied.': 'Rounding to cents is applied.',
}


def report_kind(claim_text):
    return ('invoice' if claim_text.startswith('Invoice ') else 'budget' if claim_text.startswith('Spending ')
            else 'fund')


def report_frame(report):
    """The report with every claim sentence and every cited identifier masked."""
    for text in claims(report):
        report = report.replace(text, '<CLAIM>', 1)
    return re.sub(r'\b([BSF])-(?:[0-9]{4}|[A-Z][a-z]+)\b', r'\1-<ID>', report)


def reviewed_frame(report):
    """The frame this report must have, fixed by the kind of each claim and nothing else."""
    blocks = ['<a id="C{0:02d}"></a>\n**C{0:02d}.** <CLAIM> {1}\n'.format(number, REVIEWED_CITATION_FORMS[report_kind(text)])
              for number, text in enumerate(claims(report), 1)]
    return REVIEWED_REPORT_TITLE + '\n\n' + '\n'.join(blocks)


def procedure_parts(text):
    """Title, then (anchor, heading, sentences) per section, read from the Markdown."""
    title, *chunks = re.split(r'\n(?=<a id=)', text.strip())
    sections = []
    for chunk in chunks:
        match = re.fullmatch(r'<a id="([^"]+)"></a>\n## \1 — ([^\n]+)\n(.*)', chunk, re.S)
        assert match, 'Malformed section: ' + chunk[:60]
        anchor, heading, body = match.groups()
        sections.append((anchor, heading, tuple(re.split(r'(?<=[.])\s+(?=[A-Z])', ' '.join(body.split())))))
    return title.strip(), tuple(sections)


def check_procedure(test, text):
    """Fail on any missing, changed or added sentence, section, heading or title."""
    title, sections = procedure_parts(text)
    test.assertEqual(title, '# ' + REVIEWED_PROCEDURE_TITLE)
    test.assertEqual(sections, REVIEWED_PROCEDURE_SECTIONS)


# ---------- Source-side helpers: records, roles, lure labels, masking ----------

def mask_sentences(body):
    """Names, numbers, dates and references masked, then split into sentences (by this file, not the module)."""
    body = re.sub(r'supplier [A-Z][a-z]+ [A-Z][a-z]+', 'supplier <name>', body)
    body = re.sub(r'\b[A-Z][a-z]+ (programme|fund)\b', r'<programme> \1', body)
    body = re.sub(r'\b(F|S)-[A-Za-z0-9]+', r'\1-<id>', body)
    body = re.sub(r'PO-\d+', '<reference>', body)
    body = re.sub(r'\d{4}-\d\d-\d\d', '<date>', body)
    body = re.sub(r'\d+', '<n>', body)
    return [s for s in re.split(r'(?<=[.;])\s+', ' '.join(body.split())) if s]


def role_of(anchor, body):
    if anchor.endswith('-00'):
        return 'introduction'
    if anchor.startswith('S-'):
        return 'voucher'
    return 'budget record' if 'programme operated' in body else 'fund record'


def lure_anchors(built):
    """Records whose claim, in either report, carries a lure."""
    return {row['source_anchors'][0].split('#')[1] for row in built['key']['expectations']
            if row['echo_state'] is not None}


def records_of(built):
    """(file, position, anchor, role, is a lure record, masked sentences, text) for every record in both files."""
    lured, out = lure_anchors(built), []
    for name in ('settlement.md', 'funds.md'):
        for position, (anchor, body) in enumerate(sections(sources_of(built)[name]).items()):
            out.append((name, position, anchor, role_of(anchor, body), anchor in lured,
                        mask_sentences(body), body))
    return out


def dossier_has_lure(built):
    return any(r['echo_state'] is not None for r in built['key']['expectations'])


def stump_fit(pairs):
    """Best one-sided threshold on one number: (cut, sign, label above) maximising training hits."""
    pairs = sorted(pairs)
    total = collections.Counter(label for _, label in pairs)
    best = (max(total.values()), None, None)
    below = collections.Counter()
    for k, (value, label) in enumerate(pairs):
        below[label] += 1
        if k + 1 < len(pairs) and pairs[k + 1][0] == value:
            continue
        for low, high in ((False, True), (True, False)):
            hits = below[low] + (total[high] - below[high])
            if hits > best[0]:
                best = (hits, value, (low, high))
    return best[1:], collections.Counter(label for _, label in pairs).most_common(1)[0][0]


def stump_predict(fit, fallback, value):
    cut, sides = fit
    if cut is None:
        return fallback
    return sides[0] if value <= cut else sides[1]


def documented():
    """The module docstring with its line breaks removed, to check the numbers it states against measurement."""
    return ' '.join(sys.modules['lure_repair'].__doc__.split())


def described(name, widest):
    excess, margin = widest
    return ('{} (largest excess {:.1f} points, inside its margin of {:.1f})'.format(name, 100 * excess, 100 * margin)
            if excess > 0 else '{} (none exceeds its base rate)'.format(name))


def clustered_margin(per_seed):
    return max(0.01, 3.5 * statistics.stdev(per_seed) / len(per_seed) ** 0.5)


ATTACK_TARGETS = {
    'altered report': lambda r, x: r['variant'] == 'mixed',
    'dossier holds a lure': lambda r, x: r['lure'],
    'wrong claim': lambda r, x: x['semantic_state'] != 'supported',
    'lure claim': lambda r, x: x['echo_state'] is not None}


def own_anchor(text):
    kind, subject, _ = Reading.parse(text)
    return ('S-' if kind.startswith('compound_') else 'F-') + subject


def claim_polarity(text):
    return next(w for w in ('was paid by', 'within', 'outside', 'exhausted', 'left a balance') if w in text)


@functools.lru_cache(maxsize=1)
def attack_rows():
    """One row per report of seeds 1 to 1,000: lengths, labelled claims, records (role, bytes, masked sentences)."""
    rows = []
    for seed in range(1, 1001):
        built = case(seed)
        sources = sources_of(built)
        recs = records_of(built)
        records = {anchor: (role, len(text.encode('utf-8')), masked) for _, _, anchor, role, _, masked, text in recs}
        bag = collections.Counter(s for *_, masked, _ in recs for s in masked)
        for variant in ('correct', 'mixed'):
            rows.append(dict(seed=seed, variant=variant,
                             lengths={n: len(b.encode('utf-8')) for n, b in sources.items()},
                             report=len(built['files']['report-' + variant + '.md'].encode('utf-8')),
                             claims=[r for r in built['key']['expectations'] if r['variant'] == variant],
                             records=records, bag=bag, lure=dossier_has_lure(built)))
    return rows


def sentence_attack(items, sentences):
    """items: (seed, polarity, set of masked sentences, label). For each sentence, the bucket rule on (polarity,
    sentence present) trained on seeds 1 to 500 and scored on the rest: {sentence: (accuracy, base rate, margin)}."""
    inverse = collections.defaultdict(list)
    for index, (_, _, found, _) in enumerate(items):
        for sentence in found:
            inverse[sentence].append(index)
    train_totals, seed_totals = collections.Counter(), collections.defaultdict(collections.Counter)
    for seed, polarity, _, label in items:
        if seed <= 500:
            train_totals[(polarity, label)] += 1
        else:
            seed_totals[seed][(polarity, label)] += 1
    fallback = collections.Counter(l for s, _, _, l in items if s <= 500).most_common(1)[0][0]
    polarities = {key[0] for key in train_totals}
    results = {}
    for sentence in sentences:
        train_present, seed_present = collections.Counter(), collections.defaultdict(collections.Counter)
        for index in inverse.get(sentence, ()):
            seed, polarity, _, label = items[index]
            (train_present if seed <= 500 else seed_present[seed])[(polarity, label)] += 1
        guess = {}
        for polarity in polarities:
            for present in (True, False):
                count = {label: train_present[(polarity, label)] if present else
                         train_totals[(polarity, label)] - train_present[(polarity, label)] for label in (False, True)}
                guess[(polarity, present)] = (fallback if count[True] == count[False] else count[True] > count[False])
        hits = base_hits = tested = 0
        per_seed = []
        for seed, totals in seed_totals.items():
            seed_hits = seed_base = seed_n = 0
            for (polarity, label), count in totals.items():
                present = seed_present[seed][(polarity, label)]
                seed_hits += (guess.get((polarity, True), fallback) == label) * present + (
                    guess.get((polarity, False), fallback) == label) * (count - present)
                seed_base += (fallback == label) * count
                seed_n += count
            hits, base_hits, tested = hits + seed_hits, base_hits + seed_base, tested + seed_n
            per_seed.append((seed_hits - seed_base) / seed_n)
        results[sentence] = (hits / tested, base_hits / tested, clustered_margin(per_seed))
    return results


def lookup_accuracy(data):
    """Train on seeds 1 to 500: a bucket-majority rule on one feature value, unseen values given the majority label."""
    learned = collections.defaultdict(collections.Counter)
    for seed, value, label in data:
        if seed <= 500:
            learned[value][label] += 1
    fallback = collections.Counter(l for s, _, l in data if s <= 500).most_common(1)[0][0]
    hits, base_hits, per_seed = 0, 0, collections.defaultdict(list)
    for seed, value, label in data:
        if seed > 500:
            bucket = learned.get(value)
            guess = bucket.most_common(1)[0][0] if bucket else fallback
            hits += guess == label
            base_hits += fallback == label
            per_seed[seed].append(int(guess == label) - int(fallback == label))
    tested = sum(len(v) for v in per_seed.values())
    return hits / tested, base_hits / tested, clustered_margin([statistics.mean(v) for v in per_seed.values()])


class RepairTests(unittest.TestCase):
    def test_seed_1_through_300_robustness(self):
        for seed in range(1, 301):
            built = case(seed)
            self.assertEqual(len(built['key']['expectations']), 2 * SLOTS)
            self.assertEqual(set(built['files']) - {'report-correct.md', 'report-mixed.md'},
                             {'sources/' + name for name in DOCUMENTS})

    def test_generation_is_deterministic(self):
        for seed in range(1, 301):
            self.assertEqual(case(seed), generate.build_case('repair', seed, 1))
            if seed < 300:
                self.assertNotEqual(sources_of(case(seed)), sources_of(case(seed + 1)))
                self.assertNotEqual(case(seed)['files']['report-correct.md'],
                                    case(seed + 1)['files']['report-correct.md'])

    def test_correct_claims_and_mixed_errors_are_closed_and_typed(self):
        errors = set()
        for seed in range(1, 301):
            expected = case(seed)['key']['expectations']
            self.assertEqual(collections.Counter(row['variant'] for row in expected),
                             {'correct': SLOTS, 'mixed': SLOTS})
            self.assertGreaterEqual(sum(row['semantic_state'] == 'refuted' for row in expected), 2)
            for row in expected:
                self.assertIn(row['semantic_state'], ('supported', 'refuted'))
                self.assertEqual(row['error_type'] is None, row['semantic_state'] == 'supported')
                if row['variant'] == 'correct':
                    self.assertEqual(row['semantic_state'], 'supported')
                if row['error_type']:
                    errors.add(row['error_type'])
        self.assertEqual(errors, {
            'repairs_parenthetical_detail', 'copies_companion_voucher', 'copies_companion_number',
            'copies_companion_programme', 'repairs_signed_arithmetic', 'reverses_budget_comparison',
            'repairs_share_arithmetic', 'reverses_allocation_comparison'})

    def test_no_evaluator_or_variant_markers_in_auditor_text(self):
        for seed in range(1, 301):
            built = case(seed)
            for body in built['files'].values():
                self.assertNotRegex(body, r'evaluator_only|echo_state|lure_state|answer key|repairs_the_claim|'
                                         r'report-correct|report-mixed|ignore_detail|natural_arithmetic')
                self.assertNotRegex(body, r'briefing|meeting (?:brief|card|paper|file)|calculator')
            self.assertEqual(list(sources_of(built)), sorted(sources_of(built)))

    def test_no_claim_text_contains_none(self):
        for seed in range(1, 301):
            for variant in ('correct', 'mixed'):
                for text in claims(case(seed)['files']['report-' + variant + '.md']):
                    self.assertNotRegex(text, r'\bNone\b')

    def test_rendered_keys_match_independent_derivation(self):
        kinds = set()
        for seed in range(1, 61):
            built = case(seed)
            independent = Reading(sources_of(built))
            for row in built['key']['expectations']:
                text = row['claim_text']
                self.assertEqual(row['semantic_state'], independent.verdict(text), (seed, row))
                self.assertEqual(row['echo_state'], independent.echo(text), (seed, row))
                self.assertEqual(row['claim_kind'], independent.parse(text)[0])
                if row['echo_state'] is not None:
                    self.assertEqual(row['echo_reader'], READER)
                kinds.add(row['claim_kind'])
        self.assertEqual(kinds, KINDS)

    def test_all_raw_slot_variants_match_independent_derivation(self):
        offered = collections.Counter()
        for seed in range(1, 61):
            raw = raw_case(seed)
            independent = Reading(raw['sources'])
            self.assertFalse(raw['unknowns'])
            self.assertEqual(len(raw['slots']), SLOTS)
            alternate = raw.get('told') or raw
            for true, wrong in raw['slots']:
                kind, subject, _ = independent.parse(true['text'])
                if not wrong:
                    # Only a statement about a voucher whose companion is paid has no slip that keeps its wording.
                    self.assertTrue(kind.startswith('compound_'))
                    self.assertTrue(raw['base']['invoices'][raw['base']['companion'][int(subject)]]['paid'])
                self.assertEqual(independent.verdict(true['text']), 'supported')
                for item in [true] + wrong:
                    self.assertEqual(independent.verdict(item['text']),
                                     generate.judge(item, raw['base'], raw['unknowns'])[0], (seed, item['text']))
                    self.assertEqual(independent.verdict(item['text'], repair=True),
                                     generate.judge(item, alternate['base'], alternate['unknowns'])[0],
                                     (seed, item['text']))
                    self.assertEqual(item['kind'], true['kind'])
                    # The claim cites its own record and the procedure section of its kind, and nothing else.
                    kind, subject, _ = independent.parse(item['text'])
                    self.assertEqual(item['cites'][0], ('settlement.md' if kind.startswith('compound_') else 'funds.md',
                                                        ('S-' if kind.startswith('compound_') else 'F-') + subject))
                    self.assertEqual(item['cites'][1:], true['cites'][1:])
                    self.assertEqual(len(item['cites']), 2)
                offered[bool(wrong)] += 1
        self.assertEqual(set(offered), {True, False}, offered)

    def test_wording_shapes_are_both_right_and_wrong(self):
        shapes = collections.defaultdict(set)
        for seed in range(1, 301):
            for row in case(seed)['key']['expectations']:
                anchors = tuple(re.sub(r'#(?:S-\d+|F-[A-Z][a-z]+)', '#<item>', a)
                                for a in row['source_anchors'])
                shapes[(shape(row['claim_text']), anchors)].add(row['semantic_state'])
        print('Repair wording shapes:', len(shapes))
        for frame, states in shapes.items():
            self.assertEqual(states, {'supported', 'refuted'}, frame)

    def test_matched_controls_for_every_kind_in_both_reports(self):
        controls, active = set(), set()
        for seed in range(1, 301):
            expected = case(seed)['key']['expectations']
            for variant in ('correct', 'mixed'):
                selected = [r for r in expected if r['variant'] == variant]
                lured_shapes = {(r['claim_kind'], shape(r['claim_text'])) for r in selected if r['echo_state'] is not None}
                control_shapes = {(r['claim_kind'], shape(r['claim_text'])) for r in selected if r['echo_state'] is None}
                self.assertLessEqual(lured_shapes, control_shapes, (seed, variant))
            for row in expected:
                if row['echo_state'] is None:
                    controls.add((row['claim_kind'], row['variant'], row['semantic_state']))
                else:
                    active.add(row['claim_kind'])
        self.assertEqual(active, KINDS)
        for kind in KINDS:
            for variant in ('correct', 'mixed'):
                self.assertIn((kind, variant, 'supported'), controls)
            self.assertIn((kind, 'mixed', 'refuted'), controls)

    def test_matched_controls_hold_in_all_twelve_indexes_of_a_hundred_seeds(self):
        for seed in range(1, 101):
            for index in range(1, 13):
                expected = case(seed, index)['key']['expectations']
                for variant in ('correct', 'mixed'):
                    selected = [r for r in expected if r['variant'] == variant]
                    lured = {(r['claim_kind'], shape(r['claim_text'])) for r in selected if r['echo_state'] is not None}
                    control = {(r['claim_kind'], shape(r['claim_text'])) for r in selected if r['echo_state'] is None}
                    self.assertLessEqual(lured, control, (seed, index, variant))

    def test_same_kinds_occupy_same_slots_in_both_reports(self):
        for seed in range(1, 301):
            expected = case(seed)['key']['expectations']
            by_variant = [[(r['claim_id'], r['claim_kind']) for r in expected if r['variant'] == variant]
                          for variant in ('correct', 'mixed')]
            self.assertEqual(by_variant[0], by_variant[1], seed)
            for variant in ('correct', 'mixed'):
                rendered = claims(case(seed)['files']['report-' + variant + '.md'])
                self.assertEqual([Reading.parse(text)[0] for text in rendered],
                                 [kind for _, kind in by_variant[0]])

    def test_subjects_are_unique_within_each_report(self):
        for seed in range(1, 301):
            for variant in ('correct', 'mixed'):
                subjects = [Reading.parse(text)[1] for text in claims(case(seed)['files']['report-' + variant + '.md'])]
                self.assertEqual(len(subjects), SLOTS)
                self.assertEqual(len(set(subjects)), len(subjects), (seed, variant))

    def test_every_claim_cites_its_own_record_and_one_procedure_section(self):
        for seed in range(1, 301):
            for variant in ('correct', 'mixed'):
                report = case(seed)['files']['report-' + variant + '.md']
                seen = set()
                for text, cited in zip(claims(report), re.findall(r'\*\*C\d+\.\*\* .*? (Citations?: [^\n]+)', report)):
                    kind, subject, _ = Reading.parse(text)
                    own = ('settlement S-' if kind.startswith('compound_') else 'funds F-') + subject
                    section_id = ('P-01' if kind.startswith('compound_') else
                                  'P-02' if kind == 'budget_tolerance' else 'P-03')
                    self.assertEqual(cited, 'Citations: {}; procedure {}.'.format(own, section_id), (seed, variant))
                    self.assertNotIn(own, seen)
                    seen.add(own)

    def test_both_lure_directions_occur(self):
        directions = collections.defaultdict(set)
        for seed in range(1, 301):
            for row in case(seed)['key']['expectations']:
                if row['echo_state'] is not None:
                    directions[row['claim_kind']].add((row['semantic_state'], row['echo_state']))
        self.assertEqual(set(directions), KINDS)
        for kind in KINDS:
            expected = ({('refuted', 'supported')} if kind.startswith('compound_') else
                        {('supported', 'refuted'), ('refuted', 'supported')})
            self.assertEqual(directions[kind], expected, kind)

    def test_clean_dossiers_have_a_nontrivial_share(self):
        clean = sum(raw_case(seed)['told'] is None for seed in range(1, 301))
        # 30 percent draw, with a predeclared +/- 10-point finite-sample band.
        self.assertGreaterEqual(clean, 60)
        self.assertLessEqual(clean, 120)
        self.assertEqual(clean, sum(not dossier_has_lure(case(seed)) for seed in range(1, 301)))
        clean_1000 = sum(not dossier_has_lure(case(seed)) for seed in range(1, 1001))
        print('Repair clean source dossiers: {} / 300, {} / 1000'.format(clean, clean_1000))
        self.assertIn('{} dossiers ({:.1f} percent) hold no lure (seeds 1..1000: {}, {:.1f} percent)'.format(
            clean, clean / 3, clean_1000, clean_1000 / 10), documented())
        per_report = {variant: [] for variant in ('correct', 'mixed')}
        for seed in range(1, 301):
            rows = case(seed)['key']['expectations']
            if dossier_has_lure(case(seed)):
                for variant in per_report:
                    per_report[variant].append(sum(r['echo_state'] is not None for r in rows if r['variant'] == variant))
        self.assertIn('the correct report holds {} or {} echo rows (mean {:.2f}) and the altered report {} to {} (mean {:.2f})'.format(
            min(per_report['correct']), max(per_report['correct']), statistics.mean(per_report['correct']),
            min(per_report['mixed']), max(per_report['mixed']), statistics.mean(per_report['mixed'])), documented())

    def test_r9_counts_by_habit_direction_kind_and_combination(self):
        counts, compound, arithmetic = [], [], []
        directions, kinds, combos = collections.Counter(), collections.Counter(), collections.Counter()
        habit_b, both_right = collections.Counter(), collections.Counter()
        for seed in range(1, 101):
            total = detail = arith = 0
            for index in range(1, 13):
                built = case(seed, index)
                independent = Reading(sources_of(built))
                for row in built['key']['expectations']:
                    actual = independent.verdict(row['claim_text'])
                    echo = independent.echo(row['claim_text'])
                    self.assertEqual(row['semantic_state'], actual, (seed, index, row))
                    self.assertEqual(row['echo_state'], echo, (seed, index, row))
                    kind, ident, assertion = independent.parse(row['claim_text'])
                    if kind.startswith('compound_'):
                        paid, value = assertion
                        record = independent.invoices[ident]
                        combination = (paid == record['paid'], value == record[kind[len('compound_'):]])
                        if row['variant'] == 'mixed' and row['error_type']:
                            combos[combination] += 1
                        elif combination == (True, True):
                            both_right[row['variant']] += 1
                    if echo is not None:
                        total += 1
                        directions[(actual, echo)] += 1
                        kinds[kind] += 1
                        if kind.startswith('compound_'):
                            detail += 1
                        else:
                            arith += 1
                            habit_b[(kind, actual)] += 1
            counts.append(total)
            compound.append(detail)
            arithmetic.append(arith)
        altered = sum(combos.values())
        shares = {key: round(n / altered, 3) for key, n in sorted(combos.items())}
        print('Repair R9 echo rows per twelve dossiers (min/median/max): total {}/{}/{}; compound {}/{}/{}; '
              'arithmetic {}/{}/{}; all {}; directions {}; kinds {}; altered compound claims by (main right, '
              'detail right) {}'.format(
                  min(counts), statistics.median(counts), max(counts), min(compound), statistics.median(compound),
                  max(compound), min(arithmetic), statistics.median(arithmetic), max(arithmetic), sum(counts),
                  dict(directions), dict(kinds), shares))
        text = documented()
        self.assertIn('total minimum {}, median {} and maximum {} ({:,} rows, all definite: {:,} supported-to-refuted and '
                      '{:,} refuted-to-supported; none is undetermined)'.format(
                          min(counts), int(statistics.median(counts)), max(counts), sum(counts),
                          directions[('supported', 'refuted')], directions[('refuted', 'supported')]), text)
        self.assertIn('minimum {}, median {} and maximum {} ({:,} rows, all refuted-to-supported)'.format(
            min(compound), int(statistics.median(compound)), max(compound), sum(compound)), text)
        self.assertIn('minimum {}, median {} and maximum {} ({:,} rows; budgets {:,} supported-to-refuted and {:,} '
                      'refuted-to-supported, funds {:,} and {:,})'.format(
                          min(arithmetic), int(statistics.median(arithmetic)), max(arithmetic), sum(arithmetic),
                          habit_b[('budget_tolerance', 'supported')], habit_b[('budget_tolerance', 'refuted')],
                          habit_b[('allocation_balance', 'supported')], habit_b[('allocation_balance', 'refuted')]), text)
        self.assertIn('Of {:,} altered voucher claims (seeds 1..100, indexes 1..12) {:,} ({:.1f} percent) are main right and '
                      'detail wrong, {:,} ({:.1f} percent) main wrong and detail right, {:,} ({:.1f} percent) both wrong; '
                      '{:,} claims in the correct reports and {:,} in the altered reports are both right. The {:,} lures '
                      'are {} amount, {} date, {} name and {} reference.'.format(
                          altered, combos[(True, False)], 100 * combos[(True, False)] / altered,
                          combos[(False, True)], 100 * combos[(False, True)] / altered,
                          combos[(False, False)], 100 * combos[(False, False)] / altered,
                          both_right['correct'], both_right['mixed'], sum(compound), kinds['compound_amount'],
                          kinds['compound_date'], kinds['compound_name'], kinds['compound_reference']), text)
        self.assertGreaterEqual(statistics.median(counts), 15)
        self.assertGreaterEqual(statistics.median(compound), 8)
        self.assertEqual(set(directions), {('supported', 'refuted'), ('refuted', 'supported')})
        # Measured on the final code: total 18-62, median 36; compound 5-28, median 13 (bands beside the numbers).
        self.assertTrue(28 <= statistics.median(counts) <= 46, statistics.median(counts))
        self.assertTrue(9 <= statistics.median(compound) <= 18, statistics.median(compound))
        self.assertTrue(15 <= statistics.median(arithmetic) <= 30, statistics.median(arithmetic))
        # The three altered combinations (the fourth, both right, is the unaltered claim) occur at similar rates.
        self.assertEqual(set(combos), {(False, True), (True, False), (False, False)})
        for key, share in shares.items():
            self.assertTrue(0.25 <= share <= 0.42, (key, share))
        # Every detail kind carries a similar share of the compound lures.
        detail_total = sum(kinds[k] for k in DETAIL_KINDS)
        for kind in DETAIL_KINDS:
            self.assertTrue(0.20 <= kinds[kind] / detail_total <= 0.30, (kind, kinds[kind] / detail_total))

    def test_rendered_fact_tampering_changes_verdicts(self):
        exercised = set()
        for seed in range(1, 61):
            built = case(seed)
            sources = sources_of(built)
            reading = Reading(sources)
            for row in built['key']['expectations']:
                if row['variant'] != 'correct':
                    continue
                text = row['claim_text']
                kind, ident, assertion = reading.parse(text)
                if kind in exercised:
                    continue
                changed = dict(sources)
                if kind.startswith('compound_'):
                    old = reading.invoices[ident]
                    block = sections(sources['settlement.md'])['S-' + ident]
                    field = kind[len('compound_'):]
                    replacement = {'amount': str(int(old['amount']) + 1), 'date': '2034-11-01',
                                   'name': 'A Different Supplier', 'reference': 'PO-0000'}[field]
                    new = block.replace(old[field], replacement)
                    self.assertNotEqual(new, block)
                    changed['settlement.md'] = changed['settlement.md'].replace(block, new)
                elif kind == 'budget_tolerance':
                    budget, actual, tolerance = reading.budgets[ident]
                    replacement = budget + tolerance + 1 if assertion else budget
                    block = sections(sources['funds.md'])['F-' + ident]
                    new = block.replace('Actual spending: EUR {}.'.format(actual),
                                        'Actual spending: EUR {}.'.format(replacement))
                    changed['funds.md'] = changed['funds.md'].replace(block, new)
                else:
                    total, teams, rounded = reading.funds[ident]
                    block = sections(sources['funds.md'])['F-' + ident]
                    replacement = (total // teams) * teams + (1 if assertion else 0)
                    new = block.replace('Fund total: EUR {}.'.format(total),
                                        'Fund total: EUR {}.'.format(replacement))
                    new = new.replace('Keep shares exact, with fractional euros retained for every team.',
                                      'Round shares down, with fractional euros withheld for every team.')
                    changed['funds.md'] = changed['funds.md'].replace(block, new)
                self.assertEqual(reading.verdict(text), 'supported')
                self.assertEqual(Reading(changed).verdict(text), 'refuted', (seed, text))
                exercised.add(kind)
        self.assertEqual(exercised, KINDS)

    def test_both_habits_apply_globally_and_errors_have_source_causes(self):
        combinations, errors, changes = set(), set(), collections.Counter()
        for seed in range(1, 301):
            raw = raw_case(seed)
            reading = Reading(raw['sources'])
            companion = {str(k): str(v) for k, v in raw['base']['companion'].items()}
            if raw['told']:
                self.assertTrue(raw['told']['base']['ignore_detail'])
                self.assertTrue(raw['told']['base']['natural_arithmetic'])
            detail_offered, arithmetic_items = False, set()
            for true, wrong in raw['slots']:
                true_kind, true_subject, _ = reading.parse(true['text'])
                for item in [true] + wrong:
                    text = item['text']
                    kind, ident, assertion = reading.parse(text)
                    actual = reading.verdict(text)
                    ignored = reading.verdict(text, ignore_detail=True)
                    natural = reading.verdict(text, natural_arithmetic=True)
                    combined = reading.verdict(text, repair=True)
                    self.assertEqual(combined, ignored if kind.startswith('compound_') else natural)
                    if kind.startswith('compound_'):
                        self.assertEqual(natural, actual)
                        paid, detail = assertion
                        record = reading.invoices[ident]
                        field = kind[len('compound_'):]
                        main_right, detail_right = paid == record['paid'], detail == record[field]
                        combinations.add((main_right, detail_right))
                        if ignored != actual:
                            detail_offered = True
                        if not detail_right:
                            # A wrong detail is what a neighbouring voucher really says (the reported voucher or its
                            # companion), and nothing else.
                            self.assertIn(detail, (reading.invoices[true_subject][field],
                                                   reading.invoices[companion[true_subject]][field]))
                        if ident != true_subject:
                            # A slip about the neighbour: same wording, wrong because the neighbour is open.
                            self.assertEqual(ident, companion[true_subject])
                            self.assertFalse(main_right)
                        expected_error = (None if main_right and detail_right else
                                          'repairs_parenthetical_detail' if main_right else
                                          'copies_companion_voucher' if detail_right else 'copies_companion_number')
                    else:
                        self.assertEqual(ignored, actual)
                        if natural != actual:
                            arithmetic_items.add(ident)
                        if ident != true_subject:
                            expected_error = 'copies_companion_programme'
                            self.assertEqual(natural, actual)
                        elif item is true:
                            expected_error = None
                        else:
                            expected_error = (('repairs_signed_arithmetic' if natural != actual else 'reverses_budget_comparison')
                                              if kind == 'budget_tolerance' else
                                              ('repairs_share_arithmetic' if natural != actual else 'reverses_allocation_comparison'))
                    self.assertEqual(item['error'], expected_error, (seed, text))
                    if item['error']:
                        errors.add(item['error'])
            self.assertEqual(detail_offered, bool(raw['told']))
            self.assertEqual(bool(arithmetic_items), bool(raw['told']))
            self.assertLessEqual(len(arithmetic_items), 2)
            changes[(int(detail_offered), len(arithmetic_items))] += 1
        self.assertEqual(combinations, {(True, True), (True, False), (False, True), (False, False)})
        self.assertEqual(set(changes), {(0, 0), (1, 1), (1, 2)})
        self.assertEqual(len(errors), 8)
        print('Repair dossiers by (right-voucher wrong-detail offered, arithmetic lure items):', dict(changes))

    def test_rule_omission_and_unrecognized_claim_are_rejected(self):
        sources = sources_of(case(1))
        changed = dict(sources)
        changed['procedure.md'] = changed['procedure.md'].replace(
            'A report statement is correct if and only if every assertion in it is correct, including amounts, dates, names and references given in parentheses.', '')
        with self.assertRaises(AssertionError):
            Reading(changed)
        with self.assertRaises(AssertionError):
            Reading(sources).verdict('')
        with self.assertRaises(AssertionError):
            Reading(sources).verdict('The invoice was probably paid.')

    def test_source_prose_length(self):
        # Exclude every table, including the booking registers, from this count.
        for seed in range(1, 301):
            words = sum(len(line.split()) for text in sources_of(case(seed)).values()
                        for line in text.splitlines() if not line.startswith('|'))
            self.assertGreaterEqual(words, 1500, seed)

    def test_report_only_and_metadata_guessers(self):
        """Fit feature-majority classifiers on 150 dossiers; test on 150 new ones.

        The error margin uses dossier-clustered paired accuracy differences,
        not an assumption that the 56 claims within a dossier are independent.
        Each feature and baseline is printed. A 3.5-SE margin covers the small
        family of probes, with a one-point practical floor. File lengths now
        differ between dossiers; the stump attacks below handle unseen lengths.
        """
        samples = collections.defaultdict(list)
        widest = (0.0, 0.0)
        all_metadata = set()
        for seed in range(1, 301):
            built = case(seed)
            metadata = tuple((name, len(body.encode('utf-8')), len(body.split()))
                             for name, body in sorted(sources_of(built).items()))
            all_metadata.add(metadata)
            dossier_lure = dossier_has_lure(built)
            for variant in ('correct', 'mixed'):
                report = built['files']['report-' + variant + '.md']
                texts = claims(report)
                self.assertEqual(len(texts), SLOTS)
                citation_texts = re.findall(r'\*\*C\d+\.\*\* .*? (Citations?: [^\n]+)', report)
                self.assertEqual(len(citation_texts), len(texts))
                citations = [set(re.findall(r'\b[BSF]-[A-Za-z0-9]+', cited)) for cited in citation_texts]
                overlaps = [sum(bool(anchors & other) for j, other in enumerate(citations) if j != i)
                            for i, anchors in enumerate(citations)]
                shared = [sum(len(anchors & other) for j, other in enumerate(citations) if j != i)
                          for i, anchors in enumerate(citations)]
                shape_counts = collections.Counter(shape(text) for text in texts)
                features = (len(report), len(report.split()),
                            tuple(sorted(collections.Counter(shape(t) for t in texts).items())),
                            sum(' not ' in t for t in texts),
                            tuple(sorted(len(t) for t in texts)),
                            sum(int(n) % 10 for n in re.findall(r'\d+', report)) % 10,
                            tuple(sorted(collections.Counter(overlaps).items())),
                            tuple(sorted(collections.Counter(shared).items())),
                            tuple(sorted(collections.Counter((shape(t), degree) for t, degree in zip(texts, overlaps)).items())))
                samples['altered report'].append((seed, features, metadata, variant == 'mixed'))
                samples['lure dossier'].append((seed, features, metadata, dossier_lure))
                labels = {r['claim_text']: r for r in built['key']['expectations'] if r['variant'] == variant}
                for position, text in enumerate(texts):
                    row = labels[text]
                    claim_features = (shape(text), position, len(text), len(text.split()),
                                      ' not ' in text, tuple(re.findall(r'\d+', text)[-1:]),
                                      features[2], features[0], overlaps[position], shared[position],
                                      shape_counts[shape(text)], (shape(text), overlaps[position]),
                                      (shape(text), shape_counts[shape(text)]))
                    samples['wrong claim'].append((seed, claim_features, metadata,
                                                   row['semantic_state'] != 'supported'))
                    samples['lure claim'].append((seed, claim_features, metadata, row['echo_state'] is not None))
        # Source lengths vary now, so a lookup on exact lengths mostly meets unseen values; the next test
        # replaces it with threshold rules that do not depend on having seen a length.
        self.assertGreater(len(all_metadata), 100)
        for target, data in samples.items():
            train = [r for r in data if r[0] <= 150]
            test = [r for r in data if r[0] > 150]
            fallback = collections.Counter(r[3] for r in train).most_common(1)[0][0]
            baseline = sum(r[3] == fallback for r in test) / len(test)
            majority = max(collections.Counter(r[3] for r in test).values()) / len(test)
            for mode in ('report-only', 'filenames/lengths'):
                values = (range(len(train[0][1])) if mode == 'report-only'
                          else range(len(train[0][2]) * 3))
                for feature in values:
                    def value(row):
                        if mode == 'report-only':
                            return row[1][feature]
                        return row[2][feature // 3][feature % 3]
                    learned = collections.defaultdict(collections.Counter)
                    for row in train:
                        learned[value(row)][row[3]] += 1
                    differences = collections.defaultdict(list)
                    hits = 0
                    balanced_hits = collections.Counter()
                    label_totals = collections.Counter(r[3] for r in train)
                    for row in test:
                        bucket = learned.get(value(row))
                        guess = bucket.most_common(1)[0][0] if bucket else fallback
                        balanced_guess = (max((False, True), key=lambda label: bucket[label] / label_totals[label])
                                          if bucket else fallback)
                        balanced_hits[row[3]] += balanced_guess == row[3]
                        hit = guess == row[3]
                        hits += hit
                        differences[row[0]].append(int(hit) - int(row[3] == fallback))
                    grouped = [statistics.mean(ds) for ds in differences.values()]
                    margin = max(0.01, 3.5 * statistics.stdev(grouped) / len(grouped) ** 0.5)
                    accuracy = hits / len(test)
                    test_totals = collections.Counter(r[3] for r in test)
                    balanced_accuracy = statistics.mean(balanced_hits[label] / count
                                                        for label, count in test_totals.items())
                    print('Repair {} {} feature {}: accuracy={:.3f}; trained-majority={:.3f}; '
                          'test-majority={:.3f}; margin={:.3f}; train-values={}; balanced={:.3f}'.format(
                              target, mode, feature, accuracy, baseline, majority, margin, len(learned), balanced_accuracy))
                    self.assertLessEqual(accuracy - baseline, margin,
                                         (target, mode, feature, accuracy, baseline, margin))
                    if accuracy - baseline > widest[0]:
                        widest = (accuracy - baseline, margin)
        self.assertIn(described('the feature guessers of the tests', widest), documented())

    def test_report_paragraphs_match_the_audit_helper(self):
        import audit
        for seed in (1, 2, 3):
            for variant in ('correct', 'mixed'):
                report = case(seed)['files']['report-' + variant + '.md']
                self.assertEqual(run_blind.paragraphs(report), audit.paragraphs(report))

    def test_written_suite_loads_in_scorer_seals_and_stages(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='repair-tests-', dir=ROOT / 'tmp') as temporary:
            root = Path(temporary)
            fixtures, keys, arena, sealed = [root / name for name in ('fixtures', 'keys', 'arena', 'sealed')]
            summary = generate.write_suite(fixtures, keys, 9, 4, ['repair'])
            loaded = scorecard.load_keys(keys)
            self.assertEqual(set(loaded), set(summary['dossiers']))
            self.assertEqual(summary['claims'], 4 * 2 * SLOTS)
            arena.mkdir()
            blindness.seal_keys(keys, sealed, [arena])
            for dossier in summary['dossiers']:
                for variant, other in (('correct', 'mixed'), ('mixed', 'correct')):
                    report_path = fixtures / dossier / ('report-' + variant + '.md')
                    record = blindness.stage_case(report_path, fixtures / dossier / 'sources', arena,
                        root / 'records', {'dossier': dossier, 'variant': variant},
                        withheld=[sealed, fixtures / dossier / ('report-' + other + '.md')])
                    run = Path(record['arena_run'])
                    self.assertEqual((run / 'case/report.md').read_bytes(), report_path.read_bytes())
                    self.assertNotIn(dossier, record['arena_run'])
                    self.assertEqual({p.name: p.read_bytes() for p in (run / 'case/sources').iterdir()},
                                     {p.name: p.read_bytes() for p in (fixtures / dossier / 'sources').iterdir()})
            self.assertFalse(list(fixtures.rglob('*.json')))

    def test_leak_scan_allows_restatement_but_detects_copied_rationale(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='repair-leak-tests-', dir=ROOT / 'tmp') as temporary:
            root = Path(temporary)
            for seed in (1, 2, 3):
                built = case(seed)
                run = root / str(seed)
                (run / 'case').mkdir(parents=True)
                (run / 'case/report.md').write_text(built['files']['report-mixed.md'], encoding='utf-8')
                selected = [row for row in built['key']['expectations'] if row['variant'] == 'mixed']
                (run / 'notes.md').write_text('\n'.join(row['claim_text'] + ' Verdict: ' + row['semantic_state']
                                                      for row in selected), encoding='utf-8')
                self.assertEqual(blindness.scan_run(run, built['key']['expectations'])['verbatim_overlaps'], [])
                (run / 'copied.md').write_text(selected[0]['rationale'], encoding='utf-8')
                self.assertTrue(blindness.scan_run(run, built['key']['expectations'])['verbatim_overlaps'])

    def test_whole_visible_report_is_the_reviewed_frame_in_every_dossier(self):
        """Title, anchors and the form of every citation depend on the kind of claim and on nothing else."""
        for seed in range(1, 301):
            for variant in ('correct', 'mixed'):
                report = case(seed)['files']['report-' + variant + '.md']
                self.assertEqual(report_frame(report), reviewed_frame(report), (seed, variant))
                texts = claims(report)
                self.assertEqual(len(texts), SLOTS)
                for text in texts:
                    self.assertRegex(text, REVIEWED_CLAIM_FORMS[report_kind(text)])
                # Within the reviewed form, the cited record is the claim's own subject.
                for text, cited in zip(texts, re.findall(r'\*\*C\d+\.\*\* .*? (Citations: [^\n]+)', report)):
                    ids = re.findall(r'\b[BSF]-([0-9]{4}|[A-Z][a-z]+)\b', cited)
                    self.assertEqual(ids, [Reading.parse(text)[1]])

    def test_report_frame_rejects_title_citation_and_anchor_leaks(self):
        report = case(1)['files']['report-correct.md']
        self.assertEqual(report_frame(report), reviewed_frame(report))
        for name, changed in (
                ('one letter added to the title', report.replace('finance review', 'finance reviews', 1)),
                ('a preamble line', report.replace('\n\n', '\n\nInternal note A.\n\n', 1)),
                ('a reordered citation', re.sub(r'(settlement S-\d+); (procedure P-\d+)', r'\2; \1', report, count=1)),
                ('a changed procedure anchor', report.replace('procedure P-01', 'procedure P-02', 1)),
                ('a citation to one file only', re.sub(r'; procedure P-\d+', '', report, count=1)),
                ('a second record cited', re.sub(r'settlement (S-\d+)', r'settlement \1, S-1234', report, count=1)),
                ('a dropped claim anchor', report.replace('<a id="C03"></a>\n', '', 1))):
            with self.subTest(name):
                self.assertNotEqual(report_frame(changed), reviewed_frame(changed))
        # A frame that changed with the lure would separate the dossiers completely; the real one is constant.
        marked = [(report_frame(case(seed)['files']['report-correct.md']).replace(
                       'finance review', 'finance reviews' if dossier_has_lure(case(seed)) else 'finance review', 1),
                   dossier_has_lure(case(seed))) for seed in range(1, 41)]
        self.assertTrue({f for f, lure in marked if lure}.isdisjoint({f for f, lure in marked if not lure}))
        self.assertGreater(len({lure for _, lure in marked}), 1)

    def test_guessers_that_read_the_whole_report_text_do_not_beat_the_base_rates(self):
        """Held out by seed: title, citations, claim-free frame and every word of the report, then the same with a sentinel."""
        def features(report):
            words = collections.Counter(re.findall(r'\w+', report.lower()))
            return {'whole report': report, 'title': report.splitlines()[0], 'claim-free frame': report_frame(report),
                    'citation lines': tuple(re.findall(r'Citations?: [^\n]+', report)),
                    'citation forms': tuple(re.sub(r'\b([BSF])-(?:[0-9]{4}|[A-Z][a-z]+)\b', r'\1-<ID>', c)
                                          for c in re.findall(r'Citations?: [^\n]+', report))}, words

        def evaluate(samples):
            train = [r for r in samples if r[0] <= 150]
            test = [r for r in samples if r[0] > 150]
            fallback = collections.Counter(r[3] for r in train).most_common(1)[0][0]
            baseline = sum(r[3] == fallback for r in test) / len(test)
            results = {}
            for name in train[0][1]:
                learned = collections.defaultdict(collections.Counter)
                for seed, values, _, label in train:
                    learned[values[name]][label] += 1
                hits = collections.defaultdict(list)
                for seed, values, _, label in test:
                    bucket = learned.get(values[name])
                    guess = bucket.most_common(1)[0][0] if bucket else fallback
                    hits[seed].append(int(guess == label) - int(fallback == label))
                results[name] = hits
            counts = {label: collections.Counter() for label in (False, True)}
            sizes = collections.Counter(r[3] for r in train)
            for _, _, words, label in train:
                counts[label].update(words)
            vocabulary = set(counts[False]) | set(counts[True])
            totals = {label: sum(counts[label].values()) + len(vocabulary) for label in counts}
            hits = collections.defaultdict(list)
            for seed, _, words, label in test:
                score = {k: math.log((sizes[k] + 1) / (len(train) + 2)) + sum(
                    n * math.log((counts[k][w] + 1) / totals[k]) for w, n in words.items() if w in vocabulary)
                    for k in counts}
                hits[seed].append(int((score[True] > score[False]) == label) - int(fallback == label))
            results['every word of the report (Bayes)'] = hits
            out = {}
            for name, hits in results.items():
                per_seed = [statistics.mean(values) for values in hits.values()]
                margin = max(0.01, 3.5 * statistics.stdev(per_seed) / len(per_seed) ** 0.5)
                accuracy = baseline + sum(sum(v) for v in hits.values()) / len(test)
                out[name] = (accuracy, margin)
            return baseline, out

        samples = collections.defaultdict(list)
        widest = (0.0, 0.0)
        for seed in range(1, 301):
            built = case(seed)
            dossier_lure = dossier_has_lure(built)
            for variant in ('correct', 'mixed'):
                report = built['files']['report-' + variant + '.md']
                values, words = features(report)
                samples['altered report'].append((seed, values, words, variant == 'mixed'))
                samples['dossier has lure'].append((seed, values, words, dossier_lure))
                labels = {r['claim_text']: r for r in built['key']['expectations'] if r['variant'] == variant}
                for text, line in zip(claims(report), re.findall(r'\*\*C\d+\.\*\* [^\n]+', report)):
                    own = dict(values, **{'own claim and citation': line,
                                         'own citation form': REVIEWED_CITATION_FORMS[report_kind(text)]})
                    samples['wrong claim'].append((seed, own, words, labels[text]['semantic_state'] != 'supported'))
                    samples['lure claim'].append((seed, own, words, labels[text]['echo_state'] is not None))
        for target, data in samples.items():
            baseline, results = evaluate(data)
            print('Repair whole-report guessers {}: base rate {:.3f}; '.format(target, baseline) + '; '.join(
                '{} {:.3f} (margin {:.3f})'.format(n, a, m) for n, (a, m) in results.items()))
            for name, (accuracy, margin) in results.items():
                with self.subTest(target=target, feature=name):
                    self.assertLessEqual(accuracy - baseline, margin, (target, name, accuracy, baseline, margin))
                if accuracy - baseline > widest[0]:
                    widest = (accuracy - baseline, margin)
        self.assertIn(described('the whole-report guessers', widest), documented())
        # Positive control: the same guessers do learn a title that changes with the lure.
        planted = []
        for seed, values, words, label in samples['dossier has lure']:
            values = dict(values, title=values['title'] + ('s' if label else ''))
            planted.append((seed, values, words, label))
        baseline, results = evaluate(planted)
        self.assertGreater(results['title'][0] - baseline, results['title'][1])
        self.assertGreater(results['title'][0], 0.99)

    def test_procedure_matches_the_reviewed_sentences_in_every_dossier(self):
        """Every sentence of every section, in order, is the reviewed wording: none missing, changed or added."""
        for seed in range(1, 301):
            check_procedure(self, sources_of(case(seed))['procedure.md'])

    def test_procedure_rejects_missing_changed_and_added_sentences_in_every_section(self):
        original = sources_of(case(1))['procedure.md']
        override = 'Despite the preceding rules, a displayed margin or a displayed share settles the comparison.'
        sentences = [sentence for _, _, group in REVIEWED_PROCEDURE_SECTIONS for sentence in group]
        self.assertEqual(len(sentences), 15)
        check_procedure(self, original)
        mutations = 0
        for sentence in sentences:
            changes = {'missing': '', 'changed': sentence.replace(' ', ' never ', 1),
                       'added': sentence + ' ' + override, 'added before': override + ' ' + sentence}
            if sentence in REVIEWED_INVERSIONS:
                changes['inverted'] = REVIEWED_INVERSIONS[sentence]
            for name, replacement in changes.items():
                with self.subTest(sentence=sentence, mutation=name):
                    self.assertEqual(original.count(sentence), 1)
                    changed = original.replace(sentence, replacement, 1)
                    self.assertNotEqual(changed, original)
                    with self.assertRaises(AssertionError):
                        check_procedure(self, changed)
                    mutations += 1
        for name, changed in (
                ('new section', original + '\n<a id="P-04"></a>\n## P-04 — Exceptions\nA displayed margin overrides the settlement record.\n'),
                ('changed heading', original.replace('Budget comparisons', 'Budget comparisons and displays', 1)),
                ('changed title', original.replace('finance review procedure', 'finance review procedures', 1)),
                ('dropped section', original.split('<a id="P-03">', 1)[0]),
                ('sentence moved to another section', original.replace(
                    ' No rounding to cents is applied.', '', 1).replace(
                        'Differences retain', 'No rounding to cents is applied. Differences retain', 1))):
            with self.subTest(name):
                self.assertNotEqual(changed, original)
                with self.assertRaises(AssertionError):
                    check_procedure(self, changed)
        self.assertEqual(mutations, 4 * len(sentences) + len(REVIEWED_INVERSIONS))

    def test_every_echo_row_names_the_flawed_reader(self):
        echoes = 0
        for seed in range(1, 301):
            for row in case(seed)['key']['expectations']:
                self.assertNotIn('lure_states', row)
                if row['echo_state'] is not None:
                    echoes += 1
                    self.assertEqual(row.get('echo_reader'), READER, (seed, row['claim_id']))
                else:
                    self.assertNotIn('echo_reader', row)
        self.assertGreater(echoes, 300)
        self.assertEqual(raw_case(1)['reader'], READER)

    # ---------- No convenient route: nothing outside the deciding record restates a fact ----------

    def test_a_payment_run_changes_the_status_of_a_minority_of_vouchers(self):
        differ = total = 0
        for seed in range(1, 301):
            for record in Reading(sources_of(case(seed))).invoices.values():
                total += 1
                differ += record['dispatch'] != record['paid']
        print('Repair vouchers whose dispatch history differs from the checkpoint status: {} of {}'.format(differ, total))
        self.assertIn('{:,} of {:,} vouchers ({:.1f} percent, seeds 1..300)'.format(differ, total, 100 * differ / total),
                      documented())
        self.assertTrue(0.18 <= differ / total <= 0.32, differ / total)

    def test_documents_are_the_procedure_and_two_records_with_no_covering_document(self):
        for seed in range(1, 301):
            self.assertEqual(set(sources_of(case(seed))), DOCUMENTS)
            titles = {name: body.splitlines()[0] for name, body in sources_of(case(seed)).items()}
            self.assertEqual(titles, {
                'funds.md': '# Mereford Community Trust — programme accounts',
                'procedure.md': '# Mereford Community Trust — finance review procedure',
                'settlement.md': '# Mereford Community Trust — settlement record'})

    def test_no_document_outside_the_deciding_record_restates_a_fact_the_claims_depend_on(self):
        """Identifiers, names, dates, references and figures stand in one record only (a claim's own)."""
        def index(body):
            body = re.sub(r'2034-\d\d-\d\d|October 2034', '', body)
            return dict(numbers=set(re.findall(r'(?<![\w-])\d+(?![\w-])', body)), references=set(re.findall(r'PO-\d+', body)),
                        euros=set(re.findall(r'EUR (\d+)', body)), text=body)

        def found(probe, indexed):
            if probe.startswith('EUR '):
                return probe[4:] in indexed['euros']
            if probe.startswith('PO-'):
                return probe in indexed['references']
            return probe in (indexed['numbers'] if probe.isdigit() else indexed['text'])

        def holders(probe, indexes):
            return {name for name, indexed in indexes.items() if found(probe, indexed)}
        for seed in range(1, 301):
            built = case(seed)
            sources = sources_of(built)
            reading = Reading(sources)
            indexes = {name: index(body) for name, body in sources.items()}
            for ident, record in reading.invoices.items():
                for probe in (ident, record['name'], record['reference'], 'EUR ' + record['amount']):
                    self.assertEqual(holders(probe, indexes), {'settlement.md'}, (seed, probe))
                self.assertEqual({n for n, body in sources.items() if record['date'] in body}, {'settlement.md'})
            for name in list(reading.budgets) + list(reading.funds):
                self.assertEqual(holders(name, indexes), {'funds.md'}, (seed, name))
            for name, (budget, spent, tolerance) in reading.budgets.items():
                for figure in (budget, spent, tolerance):
                    self.assertEqual(holders('EUR {}'.format(figure), indexes), {'funds.md'}, (seed, figure))
            for name, (total, teams, rounded) in reading.funds.items():
                self.assertEqual(holders('EUR {}'.format(total), indexes), {'funds.md'}, (seed, total))
            # Per claim: what a reader who opens only the documents that do not decide it would find.
            for row in built['key']['expectations']:
                kind, subject, assertion = reading.parse(row['claim_text'])
                deciding = 'settlement.md' if kind.startswith('compound_') else 'funds.md'
                others = {name: indexed for name, indexed in indexes.items() if name != deciding}
                probes = [subject] + ([assertion[1]] if kind.startswith('compound_') else [])
                for probe in probes:
                    self.assertEqual(holders(probe, others), set(), (seed, row['claim_text'], probe))

    def test_one_name_for_each_document_throughout_and_no_entry_repeats_its_opening_sentence(self):
        for seed in range(1, 301):
            sources = sources_of(case(seed))
            everything = ' '.join(sources.values())
            for stale in ('meeting', 'briefing', 'settlement notes', 'fund file', 'calculator', 'paired envelope',
                          'Programme files', 'filed beside'):
                self.assertNotIn(stale, everything.replace('outreach meeting', ''), (seed, stale))
            self.assertIn('the settlement record', everything)
            self.assertIn('the programme accounts', everything)
            openings = [body.split('\n')[1].split('. ')[0] for anchor, body in sections(sources['settlement.md']).items()
                        if anchor != 'S-00']
            self.assertEqual(len(openings), 24)
            self.assertEqual(len(set(openings)), 24, seed)

    # ---------- Quantities: lures stand only in the pairing of otherwise ordinary values ----------

    def test_arithmetic_quantities_cover_agreeing_and_disagreeing_items_in_every_class(self):
        classes = collections.Counter()
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            for name, (budget, spent, tolerance) in reading.budgets.items():
                over, outside = spent > budget, abs(spent - budget) > tolerance
                classes[('budget', 'overspend' if over else 'underspend', 'outside' if outside else 'within',
                         'disagree' if over and outside else 'agree')] += 1
            for name, (total, teams, round_down) in reading.funds.items():
                divisible = total % teams == 0
                classes[('fund', 'round down' if round_down else 'exact', 'divides' if divisible else 'does not divide',
                         'disagree' if not round_down and not divisible else 'agree')] += 1
        print('Repair arithmetic classes:', dict(sorted(classes.items())))
        self.assertEqual(len(classes), 8)
        for key, count in classes.items():
            self.assertGreater(count, 100, key)
        # Exactly one class per kind of claim disagrees; the others agree and are far more common together.
        self.assertEqual({key for key in classes if key[-1] == 'disagree'},
                         {('budget', 'overspend', 'outside', 'disagree'), ('fund', 'exact', 'does not divide', 'disagree')})

    def test_counts_that_a_reader_can_see_without_the_pairing_do_not_depend_on_the_lure(self):
        """The draws of overspends, items outside tolerance, exact instructions and exhausting funds are the same with or without a lure."""
        tables = {True: collections.defaultdict(collections.Counter), False: collections.defaultdict(collections.Counter)}
        sizes = collections.Counter()
        for seed in range(1, 1001):
            built = case(seed)
            reading = Reading(sources_of(built))
            lure = dossier_has_lure(built)
            sizes[lure] += 1
            budgets = list(reading.budgets.values())
            funds = list(reading.funds.values())
            tables[lure]['outside tolerance'][sum(abs(s - b) > t for b, s, t in budgets)] += 1
            tables[lure]['exact instructions'][sum(not r for _, _, r in funds)] += 1
            tables[lure]['exhausted by the report'][
                sum(Reading.parse(t)[2] for t in claims(built['files']['report-correct.md'])
                    if Reading.parse(t)[0] == 'allocation_balance')] += 1
            tables[lure]['within claims in the report'][
                sum(Reading.parse(t)[2] for t in claims(built['files']['report-correct.md'])
                    if Reading.parse(t)[0] == 'budget_tolerance')] += 1
            # What does depend on the lure is the pairing, which only the figures show.
            tables[lure]['overspends'][sum(s > b for b, s, t in budgets)] += 1
            tables[lure]['overspends outside tolerance'][sum(s > b and s - b > t for b, s, t in budgets)] += 1
            tables[lure]['exact shares that do not divide'][sum((not r) and T % n != 0 for T, n, r in funds)] += 1
        self.assertGreater(min(sizes.values()), 250)
        for feature in ('outside tolerance', 'exact instructions', 'exhausted by the report',
                        'within claims in the report'):
            values = set(tables[True][feature]) | set(tables[False][feature])
            for value in values:
                p_lure = tables[True][feature][value] / sizes[True]
                p_clean = tables[False][feature][value] / sizes[False]
                pooled = (tables[True][feature][value] + tables[False][feature][value]) / (sizes[True] + sizes[False])
                error = math.sqrt(max(pooled * (1 - pooled), 1e-9) * (1 / sizes[True] + 1 / sizes[False]))
                print('Repair {} = {}: with lure {:.3f}, without {:.3f}'.format(feature, value, p_lure, p_clean))
                self.assertLessEqual(abs(p_lure - p_clean), 4 * error, (feature, value, p_lure, p_clean))
        # Positive control: the pairing is what the lure changes.
        print('Repair overspends with lure {} without {}'.format(dict(tables[True]['overspends']), dict(tables[False]['overspends'])))
        self.assertEqual(set(tables[False]['overspends outside tolerance']), {0})
        self.assertEqual(set(tables[False]['exact shares that do not divide']), {0})
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            paired = (sum(s > b and s - b > t for b, s, t in reading.budgets.values())
                      + sum((not r) and T % n != 0 for T, n, r in reading.funds.values()))
            self.assertEqual(paired > 0, dossier_has_lure(built), seed)

    def test_wording_counts_have_the_same_distribution_in_the_correct_and_the_altered_report(self):
        """Altering claims moves the number of each wording by chance only, so no count of wordings shows the altered report."""
        tables = {v: collections.defaultdict(collections.Counter) for v in ('correct', 'mixed')}
        for seed in range(1, 1001):
            built = case(seed)
            for variant in ('correct', 'mixed'):
                texts = claims(built['files']['report-' + variant + '.md'])
                tables[variant]['paid claims'][sum('was paid by' in t for t in texts)] += 1
                tables[variant]['within claims'][sum(' within ' in t for t in texts)] += 1
                tables[variant]['exhausted claims'][sum('exhausted' in t for t in texts)] += 1
                tables[variant]['report length'][len(built['files']['report-' + variant + '.md']) // 8] += 1
        for feature in tables['correct']:
            for value in set(tables['correct'][feature]) | set(tables['mixed'][feature]):
                a, b = tables['correct'][feature][value] / 1000, tables['mixed'][feature][value] / 1000
                error = math.sqrt(max((a + b) / 2 * (1 - (a + b) / 2), 1e-9) * 2 / 1000)
                self.assertLessEqual(abs(a - b), 4 * error, (feature, value, a, b))

    def test_no_claim_is_always_right_given_its_wording_and_how_often_that_wording_occurs_in_its_report(self):
        """The report alone shows no class of claims that is never wrong, for example a wording that stands alone."""
        def buckets(never_wrong_kind=None):
            table = collections.defaultdict(collections.Counter)
            for seed in range(1, 1001):
                rows = [r for r in case(seed)['key']['expectations'] if r['variant'] == 'mixed']
                counts = collections.Counter(shape(r['claim_text']) for r in rows)
                for r in rows:
                    wrong = r['semantic_state'] != 'supported' and r['claim_kind'] != never_wrong_kind
                    table[(r['claim_kind'], shape(r['claim_text']), counts[shape(r['claim_text'])])][wrong] += 1
            return table

        def one_sided(table):
            return {key: dict(c) for key, c in table.items() if sum(c.values()) >= 40 and len(c) == 1}
        self.assertEqual(one_sided(buckets()), {})
        # Positive control: controls that were never altered would form buckets like these.
        self.assertTrue(one_sided(buckets(never_wrong_kind='compound_name')))

    def test_every_value_class_on_a_lure_record_also_stands_on_ordinary_records_of_the_same_dossier(self):
        voucher_classes = {True: set(), False: set()}
        for seed in range(1, 301):
            built = case(seed)
            if not dossier_has_lure(built):
                continue
            reading = Reading(sources_of(built))
            lured = lure_anchors(built)
            classes = {True: set(), False: set()}
            for name, (budget, spent, tolerance) in reading.budgets.items():
                lure = 'F-' + name in lured
                classes[lure].add(('budget', 'over' if spent > budget else 'under'))
                classes[lure].add(('budget', 'outside' if abs(spent - budget) > tolerance else 'within'))
            for name, (total, teams, round_down) in reading.funds.items():
                lure = 'F-' + name in lured
                classes[lure].add(('fund', 'round' if round_down else 'exact'))
                classes[lure].add(('fund', 'divides' if total % teams == 0 else 'does not divide'))
            self.assertLessEqual(classes[True], classes[False], seed)
            for ident, record in reading.invoices.items():
                lure = 'S-' + ident in lured
                voucher_classes[lure].add(('paid' if record['paid'] else 'open',
                                           'dispatch differs' if record['dispatch'] != record['paid'] else 'dispatch agrees'))
        # Vouchers are compared over all dossiers: a status or a dispatch history is not found on lure vouchers only.
        self.assertLessEqual(voucher_classes[True], voucher_classes[False])
        self.assertEqual(len(voucher_classes[True]), 2)
        self.assertEqual(len(voucher_classes[False]), 4)

    def test_no_sentence_occurs_only_on_lure_records_and_the_one_on_ordinary_records_is_disclosed(self):
        """Masked sentences, per role, over 1,000 dossiers: lure records and ordinary records say the same things."""
        sentences = collections.defaultdict(lambda: {True: collections.Counter(), False: collections.Counter()})
        shapes = collections.defaultdict(lambda: {True: set(), False: set()})
        for seed in range(1, 1001):
            for name, position, anchor, role, lure, masked, text in records_of(case(seed)):
                sentences[role][lure].update(masked)
                shapes[role][lure].add((len(masked), text.count('\n')))
        lure_roles = {role for role, sides in sentences.items() if sides[True]}
        self.assertEqual(lure_roles, {'voucher', 'budget record', 'fund record'})
        for role in lure_roles:
            only_lure = set(sentences[role][True]) - set(sentences[role][False])
            only_ordinary = set(sentences[role][False]) - set(sentences[role][True])
            print('Repair role {}: masked sentences on lure records {}, ordinary {}; only on lure {}; only on ordinary {}'.format(
                role, len(sentences[role][True]), len(sentences[role][False]), sorted(only_lure), sorted(only_ordinary)))
            self.assertEqual(only_lure, set(), role)
            # A lure fund has an exact instruction, and the voucher a lure is about is paid, so the round-down
            # instruction and an open status are the sentences that only ordinary records carry; nothing else may differ.
            self.assertEqual(only_ordinary,
                             {'Fund instruction: Round shares down, with fractional euros withheld for every team.'}
                             if role == 'fund record' else {'Checkpoint status: open.'} if role == 'voucher' else set(), role)
            self.assertEqual(shapes[role][True], shapes[role][False], role)

    def test_position_in_a_file_is_independent_of_lure_status(self):
        """Over 1,000 dossiers, the share of lure records is the same in every part of each file."""
        parts = {'voucher': 4, 'budget record': 4, 'fund record': 4}
        rates = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
        for seed in range(1, 1001):
            built = case(seed)
            by_file = collections.defaultdict(list)
            for name, position, anchor, role, lure, masked, text in records_of(built):
                if role != 'introduction':
                    by_file[name].append((position, role, lure))
            for name, entries in by_file.items():
                for index, (position, role, lure) in enumerate(entries):
                    part = index * parts[role] // len(entries)
                    cell = rates[role][part]
                    cell[0] += lure
                    cell[1] += 1
        for role, cells in rates.items():
            overall = sum(c[0] for c in cells.values()) / sum(c[1] for c in cells.values())
            for part, (lures, total) in sorted(cells.items()):
                error = math.sqrt(overall * (1 - overall) / total)
                print('Repair {} part {}: lure share {:.3f} (overall {:.3f})'.format(role, part, lures / total, overall))
                self.assertLessEqual(abs(lures / total - overall), 4 * error, (role, part))

    def test_a_claimed_detail_stands_on_the_same_number_of_vouchers_whether_it_is_right_or_wrong(self):
        """Counted over all vouchers, the cited one included; the count that leaves the cited one out is measured next."""
        elsewhere = collections.Counter()
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            for row in built['key']['expectations']:
                kind, ident, assertion = reading.parse(row['claim_text'])
                if not kind.startswith('compound_'):
                    continue
                field, value = kind[len('compound_'):], assertion[1]
                holders = [other for other, record in reading.invoices.items() if record[field] == value]
                right = reading.invoices[ident][field] == value
                elsewhere[(field, right, len(holders))] += 1
                # The same number of vouchers carries the value whether the claim is right or wrong.
                self.assertEqual(len(holders), 3 if field == 'name' else 2, (seed, row['claim_text']))
                self.assertEqual(ident in holders, right)
        print('Repair claimed details by (kind, right, other vouchers holding the value):', dict(sorted(elsewhere.items())))
        self.assertEqual({k[1] for k in elsewhere}, {True, False})

    def test_source_side_attack_on_normalised_lengths_trained_on_seeds_1_to_500_and_scored_on_501_to_1000(self):
        """Per-file lengths normalised per file name, then a threshold rule, with claim polarity for claim targets."""
        rows = attack_rows()
        shortest = {}
        for row in rows:
            if row['seed'] <= 500:
                for name, length in row['lengths'].items():
                    shortest[name] = min(shortest.get(name, length), length)
        role_shortest = {}
        for row in rows:
            if row['seed'] <= 500:
                for role, length, _ in row['records'].values():
                    role_shortest[role] = min(role_shortest.get(role, length), length)
        numeric = {name + ' length': (lambda name: lambda r, x: r['lengths'][name] - shortest[name])(name)
                   for name in sorted(shortest)}
        numeric['report length'] = lambda r, x: r['report']
        numeric['own record length'] = lambda r, x: r['records'][own_anchor(x['claim_text'])][1] - role_shortest[
            r['records'][own_anchor(x['claim_text'])][0]]
        reported, widest = 0, 0.0
        for target, label in ATTACK_TARGETS.items():
            claim_level = target in ('wrong claim', 'lure claim')
            for feature, value in numeric.items():
                if feature == 'own record length' and not claim_level:
                    continue
                units = ([(r, x) for r in rows for x in r['claims']] if claim_level else [(r, None) for r in rows])
                groups = collections.defaultdict(list)
                for r, x in units:
                    groups[claim_polarity(x['claim_text']) if claim_level else None].append(
                        (r['seed'], value(r, x), label(r, x)))
                fallback_all = collections.Counter(l for g in groups.values() for s, v, l in g if s <= 500).most_common(1)[0][0]
                hits, base_hits, per_seed = 0, 0, collections.defaultdict(list)
                for group in groups.values():
                    fit, fallback = stump_fit([(v, l) for s, v, l in group if s <= 500])
                    for s, v, l in group:
                        if s > 500:
                            guess = stump_predict(fit, fallback, v)
                            hits += guess == l
                            base_hits += fallback_all == l
                            per_seed[s].append(int(guess == l) - int(fallback_all == l))
                tested = sum(len(v) for v in per_seed.values())
                accuracy, base = hits / tested, base_hits / tested
                margin = clustered_margin([statistics.mean(v) for v in per_seed.values()])
                reported += 1
                widest = max(widest, margin)
                print('Repair source attack (i) {} / {}: accuracy {:.3f}, base rate {:.3f}, margin {:.3f}'.format(
                    target, feature, accuracy, base, margin))
                self.assertLessEqual(accuracy - base, margin, (target, feature, accuracy, base, margin))
        self.assertEqual(reported, 18)
        # An exact lookup on a normalised length meets a length it has not seen, and then guesses the majority.
        seen = {tuple(r['lengths'][n] - shortest[n] for n in sorted(shortest)) for r in rows if r['seed'] <= 500}
        unseen = sum(tuple(r['lengths'][n] - shortest[n] for n in sorted(shortest)) not in seen
                     for r in rows if r['seed'] > 500) / sum(r['seed'] > 500 for r in rows)
        text = documented()
        self.assertIn('eighteen length rules', text)
        self.assertIn('the largest margin is {:.1f} points'.format(100 * widest), text)
        self.assertIn('meets an unseen length in {:.0f} percent of cases'.format(100 * unseen), text)

    def test_source_side_attack_on_masked_sentences_trained_on_seeds_1_to_500_and_scored_on_501_to_1000(self):
        """Each masked source sentence, alone, as a bucket rule (count in the dossier; presence in the claim's own record)."""
        rows = attack_rows()
        decided = ('Dispatch history:', 'Checkpoint status:', 'Fund instruction:')
        vocabulary = collections.Counter(s for r in rows if r['seed'] <= 500 for s in r['bag'])
        sentences = sorted(s for s, n in vocabulary.items() if n >= 100)
        self.assertGreater(len(sentences), 40)
        tried = {}
        for target, label in ATTACK_TARGETS.items():
            if target in ('wrong claim', 'lure claim'):
                # For "wrong claim" the sentences that state the deciding fact are left out: reading them is
                # reading the deciding record, measured separately below.
                left_out = decided if target == 'wrong claim' else ()
                items = [(r['seed'], claim_polarity(x['claim_text']),
                          frozenset(s for s in r['records'][own_anchor(x['claim_text'])][2] if not s.startswith(left_out)),
                          label(r, x)) for r in rows for x in r['claims']]
                results = sentence_attack(items, [s for s in sentences if not s.startswith(left_out)])
            else:
                results = {}
                for sentence in sentences:
                    results[sentence] = lookup_accuracy([(r['seed'], r['bag'][sentence], label(r, None)) for r in rows])
            tried[target] = len(results)
            closest = max(accuracy - base - margin for accuracy, base, margin in results.values())
            print('Repair source attack (ii) masked sentences / {}: {} sentences tried, closest to its margin by {:.3f}'.format(
                target, len(results), -closest))
            for sentence, (accuracy, base, margin) in results.items():
                self.assertLessEqual(accuracy - base, margin, (target, sentence, accuracy, base, margin))
        # Positive control: a sentence found only on lure records is learned at once, by the same rule.
        items = [(r['seed'], claim_polarity(x['claim_text']),
                  frozenset(r['records'][own_anchor(x['claim_text'])][2]) | ({'<planted>'} if x['echo_state'] else set()),
                  x['echo_state'] is not None) for r in rows for x in r['claims']]
        accuracy, base, margin = sentence_attack(items, ['<planted>'])['<planted>']
        self.assertGreater(accuracy - base, margin)
        # The sentences that state the deciding fact are not an attack: reading them is reading the deciding record.
        data = [(r['seed'], (claim_polarity(x['claim_text']), tuple(sorted(
            s for s in r['records'][own_anchor(x['claim_text'])][2] if s.startswith(decided)))),
                 x['semantic_state'] != 'supported') for r in rows for x in r['claims']]
        accuracy, base, margin = lookup_accuracy(data)
        print('Repair deciding-record sentences with polarity, "wrong claim": accuracy {:.3f}, base rate {:.3f}'.format(
            accuracy, base))
        self.assertGreater(accuracy - base, margin)
        text = documented()
        self.assertIn('give "wrong claim" at {:.1f} percent against a base rate of {:.1f}'.format(100 * accuracy, 100 * base), text)
        self.assertIn('one rule per masked sentence ({} sentences for dossier targets and "lure claim", {} for "wrong claim")'.format(
            tried['lure claim'], tried['wrong claim']), text)
        self.assertEqual(tried['altered report'], tried['lure claim'])

    def test_which_wordings_can_be_lures_and_which_sentences_carry_the_verdict(self):
        """Measured cues of the family, stated in the module docstring."""
        lure, total = collections.Counter(), collections.Counter()
        open_wrong = open_total = balance_wrong = balance_total = 0
        for seed in range(1, 1001):
            built = case(seed)
            reading = Reading(sources_of(built))
            for row in built['key']['expectations']:
                if row['variant'] == 'correct':
                    word = claim_polarity(row['claim_text'])
                    total[word] += 1
                    lure[word] += row['echo_state'] is not None
                kind, ident, assertion = reading.parse(row['claim_text'])
                if kind.startswith('compound_') and not reading.invoices[ident]['paid']:
                    open_total += 1
                    open_wrong += row['semantic_state'] == 'refuted'
                if kind == 'allocation_balance' and not assertion and not reading.funds[ident][2]:
                    balance_total += 1
                    balance_wrong += row['semantic_state'] == 'refuted'
        self.assertEqual((lure['within'], lure['left a balance'], lure['was paid by']), (0, 0, 0))
        self.assertEqual((open_wrong, balance_wrong), (open_total, balance_total))
        text = documented()
        self.assertIn('(0 of {:,} and 0 of {:,}), "outside" and "exhausted" are {:.1f} and {:.1f} percent'.format(
            total['within'], total['left a balance'], 100 * lure['outside'] / total['outside'],
            100 * lure['exhausted'] / total['exhausted']), text)
        self.assertIn('always wrong ({:,} of {:,} in 1,000 dossiers) and an exact-shares fund never "left a balance" '
                      '({:,} of {:,} wrong)'.format(open_wrong, open_total, balance_wrong, balance_total), text)

    def test_the_counts_of_overspends_and_indivisible_totals_are_the_one_numeric_cue_and_it_stays_small(self):
        """Reading every figure of both record files and counting shows a lure a little; this bounds how far."""
        rows = []
        for seed in range(1, 1001):
            built = case(seed)
            reading = Reading(sources_of(built))
            overspends = sum(spent > budget for budget, spent, _ in reading.budgets.values())
            indivisible = sum(total % teams != 0 for total, teams, _ in reading.funds.values())
            exact = sum(not rounded for _, _, rounded in reading.funds.values())
            rows.append((seed, {'overspends': overspends, 'indivisible totals': indivisible,
                                'overspends plus indivisible totals': overspends + indivisible,
                                'exact instructions': exact}, dossier_has_lure(built)))
        results = {}
        for feature in rows[0][1]:
            data = [(seed, values[feature], lure) for seed, values, lure in rows]
            fit, fallback = stump_fit([(v, l) for s, v, l in data if s <= 500])
            hits = sum(stump_predict(fit, fallback, v) == l for s, v, l in data if s > 500)
            base = max(collections.Counter(l for s, _, l in data if s > 500).values())
            results[feature] = (hits / 500, base / 500)
            print('Repair numeric count rule for "dossier holds a lure" from {}: accuracy {:.3f}, base rate {:.3f}'.format(
                feature, hits / 500, base / 500))
        combined = results['overspends plus indivisible totals']
        self.assertIn('give "dossier holds a lure" at {:.1f} percent against {:.1f}'.format(100 * combined[0], 100 * combined[1]),
                      documented())
        # The wording-visible count is clean; the two figure-only counts carry a few points, bounded here.
        self.assertLessEqual(results['exact instructions'][0] - results['exact instructions'][1], 0.01)
        self.assertGreater(results['overspends plus indivisible totals'][0] - results['overspends plus indivisible totals'][1], 0.0)
        self.assertLessEqual(results['overspends plus indivisible totals'][0] - results['overspends plus indivisible totals'][1], 0.07)

    def test_a_reader_of_every_entry_except_the_cited_one_can_count_who_else_holds_the_detail(self):
        """The one entry-level cue of the vouchers: a right detail stands on one other voucher, a wrong one on two."""
        data = {'wrong claim': [], 'lure claim': []}
        for seed in range(1, 1001):
            built = case(seed)
            reading = Reading(sources_of(built))
            for row in built['key']['expectations']:
                kind, subject, assertion = reading.parse(row['claim_text'])
                if not kind.startswith('compound_'):
                    continue
                field = kind[len('compound_'):]
                others = sum(record[field] == assertion[1] for ident, record in reading.invoices.items() if ident != subject)
                right = reading.invoices[subject][field] == assertion[1]
                self.assertEqual(others, (2 if field == 'name' else 1) + (not right), (seed, row['claim_text']))
                data['wrong claim'].append((seed, others - (field == 'name'), row['semantic_state'] != 'supported'))
                data['lure claim'].append((seed, others - (field == 'name'), row['echo_state'] is not None))
        text, scores = documented(), {}
        for target, rows in data.items():
            accuracy, base, margin = lookup_accuracy(rows)
            scores[target] = (accuracy, base)
            print('Repair reader of the other entries, counting who holds the detail, "{}" among voucher claims: '
                  'accuracy {:.3f}, base rate {:.3f}, margin {:.3f}'.format(target, accuracy, base, margin))
            if target == 'wrong claim':
                self.assertGreater(accuracy - base, margin)
            else:
                self.assertLessEqual(accuracy - base, margin)
        self.assertIn('"wrong claim" at {:.1f} percent against {:.1f} and "lure claim" at {:.1f} against {:.1f}'.format(
            100 * scores['wrong claim'][0], 100 * scores['wrong claim'][1],
            100 * scores['lure claim'][0], 100 * scores['lure claim'][1]), text)

    def test_no_entry_names_another_voucher_and_companion_status_is_a_fair_coin(self):
        """Nothing in an entry points to another voucher, so no other entry shows what a claimed voucher says."""
        paid_companions = total = 0
        for seed in range(1, 1001):
            built = case(seed)
            body = sources_of(built)['settlement.md']
            for anchor, text in sections(body).items():
                if anchor != 'S-00':
                    mentioned = set(re.findall(r'(?<![\w-])\d{4}(?![\w-])', re.sub(r'2034-\d\d-\d\d|EUR \d+|PO-\d+', '', text)))
                    self.assertEqual(mentioned, {anchor[2:]}, (seed, anchor, mentioned))
            raw = raw_case(seed)
            for ident, other in raw['base']['companion'].items():
                total += 1
                paid_companions += raw['base']['invoices'][other]['paid']
        self.assertLessEqual(abs(paid_companions / total - 0.5), 4 * math.sqrt(0.25 / total), (paid_companions, total))

    def test_a_reader_who_opens_only_documents_that_do_not_decide_the_claim_scores_the_base_rate(self):
        """For each claim, only the procedure and the other record are opened: they hold no fact about the claim."""
        shape_labels = collections.defaultdict(collections.Counter)
        rows = []
        for seed in range(1, 1001):
            built = case(seed)
            for row in built['key']['expectations']:
                rows.append((seed, shape(row['claim_text']), row['semantic_state']))
        for seed, key, state in rows:
            if seed <= 500:
                shape_labels[key][state] += 1
        fallback = collections.Counter(s for seed, _, s in rows if seed <= 500).most_common(1)[0][0]
        test = [(key, state) for seed, key, state in rows if seed > 500]
        hits = sum((shape_labels[key].most_common(1)[0][0] if key in shape_labels else fallback) == state
                   for key, state in test)
        base = sum(fallback == state for _, state in test)
        print('Repair reader of non-deciding documents only (shape majority): accuracy {:.3f}, base rate {:.3f}'.format(
            hits / len(test), base / len(test)))
        self.assertLessEqual(hits / len(test) - base / len(test), 0.01)
        self.assertIn('scores {:.1f} percent against {:.1f} with the best rule on wording shape'.format(
            100 * hits / len(test), 100 * base / len(test)), documented())


if __name__ == '__main__':
    unittest.main()
