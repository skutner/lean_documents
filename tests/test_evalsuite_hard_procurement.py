"""Checks the harder `procurement` case family: purchase requests reviewed against an approval policy.

The generator decides each verdict with its own rule engine. The tests below decide every claim a second time
with separately written code, the class `Reading`, that reads only the rendered Markdown (tables, limits, effective
date, rates, roles, acting appointments, the document index that says which issue of the policy is in force, and the
notes about unreadable entries). `Reading` shares no code and no constant with `hard_procurement`. It enumerates the
open facts itself, so supported, refuted and undetermined are all derived again. Agreement shows that the documents say
what the generator computed from and that neither computation slipped. It cannot show that a rule sentence has no
second reading, because both derivations read the sentences the same way; that is a matter for human review of the
rule sentences in the two policy files, document-index.md and amendment.md.

What is imported from the generator, and why: `generate.family_builder` returns the slots of a case before errors are
drawn (the true claim and every wrong variant, which the key does not keep), and one test imports `hard_procurement`
to run its engine once per slip and compare the figures with `Reading` run with the same switch. No expected value
is taken from the generator in either place; both are compared with the second derivation.

Cases are built once per seed and shared between the tests, so the whole file stays under a minute. Seeds 1 to 60 are
used for the derivation checks and wherever the property is about how often something occurs (the wording check, the
injected errors, the mix of states), because 20 dossiers are too few for a rare error type to be certain to appear.
The two guessers and the polarity check use 300 seeds, because their bounds are about shares near one half.
"""
import collections
import datetime
import itertools
from pathlib import Path
import random
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
sys.path.insert(0, str(ROOT / 'skills/evidence-to-proof-review/scripts'))
import blindness  # noqa: E402
import generate  # noqa: E402
import run_blind  # noqa: E402
import scorecard  # noqa: E402

TEST_ROOT = ROOT / 'tmp'
TEST_ROOT.mkdir(exist_ok=True)
TEST_RUN = Path(tempfile.mkdtemp(prefix='evalsuite-hard-procurement-tests-', dir=TEST_ROOT))
SEEDS = range(1, 21)
MANY_SEEDS = range(1, 61)
FAMILY = 'procurement'
POLICY_FILES = ('policy-a.md', 'policy-b.md')
CASES = {}

APPROVING = {'Manager', 'Director', 'Finance officer'}
# The rules of the dossier, each with the switch of the second derivation that leaves it out.
RULES = {'conversion to euro': 'convert', 'requests assessed together in a week': 'week', 'Finance officer in tier 3': 'finance',
         'amendment from its effective date': 'amendment', 'approving roles only': 'approving',
         'acting appointments': 'delegation', 'the requester\'s own signature': 'requester'}
# Every slip of the key, with the switch of the second derivation that makes the same slip.
SLIP_SWITCHES = {'ignores_conversion': 'convert', 'uses_wrong_month_rate': 'month', 'ignores_same_week_rule': 'week',
                 'ignores_amendment': 'amendment', 'applies_amendment_too_early': 'early',
                 'uses_superseded_policy': 'previous', 'counts_nonapproving_role': 'approving',
                 'ignores_delegation': 'delegation', 'applies_delegation_outside_dates': 'dates',
                 'counts_requester_signature': 'requester', 'ignores_finance_requirement': 'finance',
                 'treats_limit_as_exclusive': 'exclusive'}
# The slip that a reader makes by leaving out each rule, as named in the key.
SLIPS_OF_RULES = {'conversion to euro': ('ignores_conversion', 'uses_wrong_month_rate'),
                  'requests assessed together in a week': ('ignores_same_week_rule',),
                  'Finance officer in tier 3': ('ignores_finance_requirement',),
                  'amendment from its effective date': ('ignores_amendment', 'applies_amendment_too_early',
                                                        'uses_superseded_policy'),
                  'approving roles only': ('counts_nonapproving_role',),
                  'acting appointments': ('ignores_delegation', 'applies_delegation_outside_dates'),
                  'the requester\'s own signature': ('counts_requester_signature',)}
KINDS = {'request_compliance', 'required_tier', 'assessed_amount', 'counting_approvals', 'failing_requests',
         'failing_total', 'tier_three_requests', 'department_failures', 'department_top', 'failure_comparison', 'request_amount',
         'request_signers', 'unreadable_signature', 'unreadable_amount'}


def case_for(seed):
    if seed not in CASES:
        CASES[seed] = generate.build_case(FAMILY, seed, 1)
    return CASES[seed]


def source_files(case):
    return {name[len('sources/'):]: text for name, text in case['files'].items() if name.startswith('sources/')}


def sections(text):
    parts = re.split(r'<a id="([^"]+)"></a>\n', text)
    return dict(zip(parts[1::2], parts[2::2]))


def rows(body):
    lines = [line for line in body.splitlines() if line.startswith('|')]
    cells = lambda line: [cell.strip() for cell in line.strip('|').split('|')]  # noqa: E731
    return [dict(zip(cells(lines[0]), cells(line))) for line in lines[2:]]


def day(text):
    return datetime.date.fromisoformat(text)


def verdict(outcomes):
    """Verdict of a statement from its truth in every possible world."""
    outcomes = list(outcomes)
    return 'supported' if all(outcomes) else 'undetermined' if any(outcomes) else 'refuted'


def split_names(text):
    return [name for name in re.split(r', | or | and ', text) if name]


def ten_thousandths(text):
    whole, _, fraction = text.partition('.')
    return int(whole) * 10000 + int(fraction.ljust(4, '0'))


class Reading(object):
    """A dossier as an auditor reads it: from the rendered files only."""

    def __init__(self, sources):
        status = {r['Document']: r['Status'] for r in rows(sections(sources['document-index.md'])['DI-02'])}
        self.policy_file, self.other_file = (sorted(POLICY_FILES, key=lambda n: status[n] != 'In force'))
        assert status[self.policy_file] == 'In force' and status[self.other_file] == 'Not in force', status
        policy = sections(sources[self.policy_file])
        amendment = sections(sources['amendment.md'])['AM-01']
        self.low, self.high = (int(n) for n in re.search(r'A is (\d+) euro and B is (\d+) euro', policy['P-04']).groups())
        exempt = re.search(r'at most (\d+) euro needs no counting approval', policy['P-04'])
        self.exempt = int(exempt[1]) if exempt else None
        # the issue that is not in force, for the reader who takes it for the policy
        elsewhere = sections(sources[self.other_file])['P-04']
        self.low0, self.high0 = (int(n) for n in re.search(r'A is (\d+) euro and B is (\d+) euro', elsewhere).groups())
        exempt0 = re.search(r'at most (\d+) euro needs no counting approval', elsewhere)
        self.exempt0 = int(exempt0[1]) if exempt0 else None
        self.issued, self.effective = (day(d) for d in re.search(
            r'issued on (\S+) and takes effect on (\S+)\.', amendment).groups())
        self.low2, self.high2 = (int(n) for n in re.search(
            r'the limit A is (\d+) euro and the limit B is (\d+) euro', amendment).groups())
        self.people = {r['Person']: (r['Role'], r['Department'])
                       for r in rows(sections(sources['staff.md'])['S-01'])}
        self.rates = {(r['Currency'], r['Month']): ten_thousandths(r['Euro per 1 unit of the currency'])
                      for r in rows(sections(sources['rates.md'])['RT-01'])}
        self.appointments = []
        for r in rows(sections(sources['delegations.md'])['D-01']):
            self.appointments.append({'person': r['Person'], 'role': r['Role acted in'], 'first': day(r['First day']),
                                      'last': None if r['Last day'] == 'unreadable' else day(r['Last day'])})
        self.requests = []
        for r in rows(sections(sources['requests.md'])['RQ-01']):
            self.requests.append({
                'id': r['Request'], 'date': day(r['Date']), 'who': r['Requester'], 'supplier': r['Supplier'],
                'amount': None if r['Amount'] == 'unreadable' else int(r['Amount']), 'currency': r['Currency'],
                'signed': [] if r['Signed by'] == 'none' else r['Signed by'].split('; ')})
        self.by_id = {r['id']: r for r in self.requests}
        self.open = {}
        notes = sections(sources['requests.md'])['RQ-02'] + sections(sources['delegations.md'])['D-02']
        for ident, names in re.findall(r'On request (PR-\d+) one signature is illegible\. The signer is one of (.+?)\.', notes):
            self.open[('signer', ident)] = split_names(names)
        for ident, values, _ in re.findall(r'On request (PR-\d+) the amount is unreadable\. It is one of (.+?) ([A-Z]{3})\.', notes):
            self.open[('amount', ident)] = [int(v) for v in split_names(values)]
        for person, role, begin, one, two in re.findall(
                r'appointment of (.+?) as (.+?), which starts on (\S+), is unreadable\. It is either (\S+) or (\S+)\.', notes):
            index = next(i for i, a in enumerate(self.appointments)
                         if (a['person'], a['role'], a['first']) == (person, role, day(begin)) and a['last'] is None)
            self.open[('last', index)] = [day(one), day(two)]
        keys = sorted(self.open, key=str)
        self.worlds = [dict(zip(keys, values)) for values in itertools.product(*(self.open[k] for k in keys))]
        self.cache = {}

    def held(self, person, when, world, off):
        roles = {self.people[person][0]}
        if 'delegation' not in off:
            for index, a in enumerate(self.appointments):
                if a['person'] == person and ('dates' in off or a['first'] <= when <= (a['last'] or world[('last', index)])):
                    roles.add(a['role'])
        return roles

    def register(self, number, off=frozenset()):
        """Every request's figures in world number `number`, with the rules in `off` left out."""
        if (number, off) in self.cache:
            return self.cache[(number, off)]
        world = self.worlds[number]
        euro, weeks = {}, collections.defaultdict(int)
        for r in self.requests:
            amount = world.get(('amount', r['id']), r['amount'])
            if r['currency'] != 'EUR' and 'convert' not in off:
                month = r['date'].strftime('%Y-%m')
                if 'month' in off:
                    month = (r['date'].replace(day=1) - datetime.timedelta(days=1)).strftime('%Y-%m')
                amount = amount * self.rates[(r['currency'], month)] // 10000
            euro[r['id']] = amount
            weeks[(r['who'], r['supplier'], r['date'].isocalendar()[:2])] += amount
        table = {}
        for r in self.requests:
            assessed = euro[r['id']] if 'week' in off else weeks[(r['who'], r['supplier'], r['date'].isocalendar()[:2])]
            new = r['date'] >= (self.issued if 'early' in off else self.effective) and 'amendment' not in off
            low, high = (self.low2, self.high2) if new else (self.low0, self.high0) if 'previous' in off else (self.low, self.high)
            tier = (1 if assessed < low else 2 if assessed < high else 3) if 'exclusive' in off else \
                (1 if assessed <= low else 2 if assessed <= high else 3)
            small = self.exempt0 if 'previous' in off else self.exempt
            exempt = small is not None and assessed <= small
            counted = set()
            for signer in r['signed']:
                signer = world.get(('signer', r['id']), signer) if signer == 'illegible' else signer
                if signer == r['who'] and 'requester' not in off:
                    continue
                if 'approving' not in off and not self.held(signer, r['date'], world, off) & APPROVING:
                    continue
                counted.add(signer)
            has_finance = any('Finance officer' in self.held(s, r['date'], world, off) for s in counted)
            needed = 0 if exempt else 1 if tier == 1 else 2
            table[r['id']] = {'converted': euro[r['id']], 'assessed': assessed, 'tier': tier, 'counting': len(counted),
                              'complies': len(counted) >= needed and (tier < 3 or has_finance or 'finance' in off)}
        self.cache[(number, off)] = table
        return table

    def everywhere(self, per_world):
        return [per_world(self.register(n)) for n in range(len(self.worlds))]

    def department(self, ident):
        return self.people[self.by_id[ident]['who']][1]

    def decide(self, kind, text):
        """The verdict of one claim, from the reading alone."""
        def match(pattern):
            found = re.fullmatch(pattern, text)
            assert found, f'{kind}: unreadable claim: {text}'
            return found.groups()
        if kind == 'request_compliance':
            ident, verb = match(r'Request (\S+) (complies|does not comply) with the approval policy\.')
            return verdict(self.everywhere(lambda t: t[ident]['complies'] == (verb == 'complies')))
        if kind == 'required_tier':
            ident, value, more = match(r'Request (\S+) is in tier (\d)( or higher)?\.')
            return verdict(self.everywhere(lambda t: t[ident]['tier'] >= int(value) if more else t[ident]['tier'] == int(value)))
        if kind == 'assessed_amount':
            ident, lead, value = match(r'The assessed amount of request (\S+) is (exactly|at least) (\d+) euro\.')
            return verdict(self.everywhere(lambda t: t[ident]['assessed'] >= int(value) if lead == 'at least'
                                           else t[ident]['assessed'] == int(value)))
        if kind == 'counting_approvals':
            ident, lead, value = match(r'Request (\S+) has (exactly|at least) (\d+) counting approvals?\.')
            return verdict(self.everywhere(lambda t: t[ident]['counting'] >= int(value) if lead == 'at least'
                                           else t[ident]['counting'] == int(value)))
        if kind in ('failing_requests', 'tier_three_requests', 'failing_total'):
            pattern = {'failing_requests': r'The number of requests that do not comply with the approval policy is (exactly|at least) (\d+)\.',
                       'tier_three_requests': r'The number of requests in tier 3 is (exactly|at least) (\d+)\.',
                       'failing_total': r'The converted amounts of the requests that do not comply with the approval '
                                        r'policy add up to (exactly|at least) (\d+) euro\.'}[kind]
            lead, value = match(pattern)

            def figure(t):
                if kind == 'tier_three_requests':
                    return sum(v['tier'] == 3 for v in t.values())
                if kind == 'failing_requests':
                    return sum(not v['complies'] for v in t.values())
                return sum(v['converted'] for v in t.values() if not v['complies'])
            return verdict(self.everywhere(lambda t: figure(t) >= int(value) if lead == 'at least' else figure(t) == int(value)))
        if kind == 'department_failures':
            department, lead, value = match(r'The number of requests of the (\w+) department that do not comply with the '
                                            r'approval policy is (exactly|at least) (\d+)\.')
            count = lambda t: sum(not v['complies'] for i, v in t.items() if self.department(i) == department)  # noqa: E731
            return verdict(self.everywhere(lambda t: count(t) >= int(value) if lead == 'at least' else count(t) == int(value)))
        if kind == 'department_top':
            department, amount, ident = match(r'Among the requests of the (\w+) department that do not comply with the '
                                              r'approval policy, the largest assessed amount is (\d+) euro, on request (\S+)\.')

            def top(t):
                failing = {i: v['assessed'] for i, v in t.items() if not v['complies'] and self.department(i) == department}
                best = max(failing.values(), default=None)
                winners = [i for i, a in failing.items() if a == best]
                return len(winners) == 1 and (winners[0], best) == (ident, int(amount))
            return verdict(self.everywhere(top))
        if kind == 'failure_comparison':
            one, word, two = match(r'The number of requests (.+?) that do not comply with the approval policy is '
                                   r'(greater|smaller) than the number of requests (.+?) that do not comply with the '
                                   r'approval policy\.')

            def members(group):
                window = re.fullmatch(r'dated from (\S+) to (\S+)', group)
                department = re.fullmatch(r'of the (\w+) department', group)
                currency = re.fullmatch(r'paid in ([A-Z]{3})', group)
                if window:
                    return lambda i: day(window[1]) <= self.by_id[i]['date'] <= day(window[2])
                if currency:
                    return lambda i: self.by_id[i]['currency'] == currency[1]
                assert department, group
                return lambda i: self.department(i) == department[1]
            first, second = members(one), members(two)
            return verdict(self.everywhere(lambda t: (lambda x, y: x > y if word == 'greater' else x < y)(
                sum(not v['complies'] and first(i) for i, v in t.items()),
                sum(not v['complies'] and second(i) for i, v in t.items()))))
        if kind == 'request_amount':
            ident, amount, currency = match(r'Request (\S+) is for (\d+) ([A-Z]{3})\.')
            return 'supported' if (self.by_id[ident]['amount'], self.by_id[ident]['currency']) == (int(amount), currency) else 'refuted'
        if kind == 'request_signers':
            ident, names = match(r'Request (\S+) was signed by (.+)\.')
            return 'supported' if sorted(split_names(names)) == sorted(self.by_id[ident]['signed']) else 'refuted'
        if kind == 'unreadable_signature':
            ident, names = match(r'The illegible signature on request (\S+) is by one of (.+)\.')
            listed = split_names(names)
            return verdict(world[('signer', ident)] in listed for world in self.worlds)
        if kind == 'unreadable_amount':
            ident, values, currency = match(r'The unreadable amount of request (\S+) is one of (.+) ([A-Z]{3})\.')
            assert self.by_id[ident]['currency'] == currency
            listed = [int(v) for v in split_names(values)]
            return verdict(world[('amount', ident)] in listed for world in self.worlds)
        raise AssertionError(f'no second derivation for the claim kind {kind}')


WORDING = {}


def wording_shapes():
    """For every claim of seeds 1 to 60: its wording shape and citations, and whether it is right. Returns the
    shapes under this file's normalisation and under the plain one (names, ids, dates, numbers)."""
    if WORDING:
        return WORDING['full'], WORDING['plain']
    full, plain = collections.defaultdict(set), collections.defaultdict(set)
    for seed in MANY_SEEDS:
        case = case_for(seed)
        sources = source_files(case)
        staff = rows(sections(sources['staff.md'])['S-01'])
        names = sorted({r['Person'] for r in staff}, key=len, reverse=True)
        departments = {r['Department'] for r in staff}
        currencies = {r['Currency'] for r in rows(sections(sources['rates.md'])['RT-01'])} | {'EUR'}

        def shape(text, collapse):
            for name in names:
                text = text.replace(name, 'NAME')
            if collapse:
                for department in departments:
                    text = text.replace(f'the {department} department', 'the DEPT department')
                text = re.sub(r'\b(' + '|'.join(sorted(currencies)) + r')\b', 'CUR', text)
            text = re.sub(r'\bPR-\d+\b', 'ID', re.sub(r'\d{4}-\d\d-\d\d', 'DATE', text))
            text = re.sub(r'\b1 counting approval\b', 'N counting approvals', text)
            if collapse:
                text = re.sub(r'NAME(, NAME)*( (and|or) NAME)?', 'NAMES', text)
                text = re.sub(r'requests (dated from DATE to DATE|of the DEPT department|paid in CUR)', 'requests GROUP', text)
            text = re.sub(r'\d+', 'N', text)
            if collapse:
                text = re.sub(r'\bN(, N)*( or N)\b', 'NUMBERS', text)
            return text
        ledgers = {variant: run_blind.paragraphs(case['files'][f'report-{variant}.md']) for variant in ('correct', 'mixed')}
        anchors = {variant: run_blind.claim_anchors(ledger) for variant, ledger in ledgers.items()}
        for row in case['key']['expectations']:
            assert not re.search(r'(^| )-\d', row['claim_text']), 'a negative amount or count marks a slip'
            assert not re.search(r'at least 0\b|tier 1 or higher|tier 0', row['claim_text']), 'a vacuous claim'
            text = ledgers[row['variant']][anchors[row['variant']][row['claim_id']]]
            cited = text.split(row['claim_text'], 1)[1]
            assert re.search(r'^ Citations?: .+\.$', cited), cited
            right = row['semantic_state'] == 'supported'
            full[(row['claim_kind'], shape(row['claim_text'], True), cited)].add(right)
            plain[(row['claim_kind'], shape(row['claim_text'], False), cited)].add(right)
    WORDING['full'], WORDING['plain'] = full, plain
    return full, plain


def built(seed, index=1):
    """The slots of a case before `build_case` draws its errors: the true claim and every wrong variant of each."""
    return generate.family_builder(FAMILY)(random.Random(f'evalsuite:{generate.GENERATOR_VERSION}:{seed}:{index}:{FAMILY}'))


def passage(case, row):
    ledger = run_blind.paragraphs(case['files'][f'report-{row["variant"]}.md'])
    anchors = run_blind.claim_anchors(ledger)
    return ledger[anchors[row['claim_id']]]


class ProcurementTests(unittest.TestCase):
    def test_keys_agree_with_a_second_derivation_from_the_rendered_documents(self):
        seen, total, settled_with_unknown = collections.Counter(), 0, collections.Counter()
        for seed in MANY_SEEDS:
            case = case_for(seed)
            reading = Reading(source_files(case))
            self.assertGreaterEqual(len(reading.open), 2, case['dossier'])
            self.assertLessEqual(len(reading.worlds), 500, case['dossier'])
            for row in case['key']['expectations']:
                total += 1
                seen[row['claim_kind']] += 1
                derived = reading.decide(row['claim_kind'], row['claim_text'])
                self.assertEqual(derived, row['semantic_state'],
                                 f'{case["dossier"]} {row["variant"]} {row["claim_id"]}: {row["claim_text"]} ({row["rationale"]})')
                # a claim about a request with an unreadable signature, settled all the same
                about = re.search(r'\bPR-\d+\b', row['claim_text'])
                if about and ('signer', about[0]) in reading.open and derived == 'supported' \
                        and row['claim_kind'] == 'request_compliance':
                    settled_with_unknown[case['dossier']] += 1
        self.assertEqual(sum(seen.values()), total)
        self.assertEqual(set(seen), KINDS, 'every claim kind must be derived again, and none may be skipped')
        self.assertEqual(len(settled_with_unknown), len(MANY_SEEDS), 'every dossier settles a claim although an unknown touches it')

    def test_the_second_derivation_reads_the_documents_and_not_the_generators_numbers(self):
        """A guard on the test itself: the limits, the effective date and the unreadable entries come from the files."""
        case = case_for(3)
        sources = source_files(case)
        reading = Reading(sources)
        self.assertLess(reading.issued, reading.effective)
        self.assertEqual(len(reading.requests), len(rows(sections(sources['requests.md'])['RQ-01'])))
        self.assertEqual(len(reading.worlds), len(list(itertools.product(*(reading.open[k] for k in reading.open)))))
        # changing a rate in the text changes the second derivation, so it does read the text
        tampered = dict(sources)
        tampered['rates.md'] = re.sub(r'\| (USD|GBP|CHF) \| (\S+) \| \d+\.\d+ \|', r'| \1 | \2 | 5.0000 |', tampered['rates.md'])
        other = Reading(tampered)
        mine = [reading.register(0)[r['id']]['assessed'] for r in reading.requests]
        changed = [other.register(0)[r['id']]['assessed'] for r in reading.requests]
        self.assertNotEqual(mine, changed)

    def test_correct_reports_hold_only_supported_claims_and_altered_reports_hold_typed_errors(self):
        for seed in SEEDS:
            case = case_for(seed)
            by_variant = {variant: [r for r in case['key']['expectations'] if r['variant'] == variant]
                          for variant in ('correct', 'mixed')}
            self.assertEqual([r['claim_id'] for r in by_variant['correct']], [r['claim_id'] for r in by_variant['mixed']])
            self.assertEqual(len({r['claim_id'] for r in by_variant['correct']}), len(by_variant['correct']))
            for row in by_variant['correct']:
                self.assertEqual((row['semantic_state'], row['error_type']), ('supported', None), case['dossier'])
            typed = [r for r in by_variant['mixed'] if r['error_type']]
            self.assertGreaterEqual(len(typed), 2, case['dossier'])
            self.assertLess(len(typed), len(by_variant['mixed']), case['dossier'])
            for row in by_variant['mixed']:
                self.assertEqual(row['error_type'] is not None, row['semantic_state'] != 'supported', row)
            self.assertNotEqual(case['files']['report-correct.md'], case['files']['report-mixed.md'])
            for variant in ('correct', 'mixed'):
                ledger = run_blind.paragraphs(case['files'][f'report-{variant}.md'])
                anchors = run_blind.claim_anchors(ledger)
                keyed = by_variant[variant]
                self.assertEqual(sorted(anchors), sorted(r['claim_id'] for r in keyed))
                for row in keyed:
                    self.assertIn(row['claim_text'], ledger[anchors[row['claim_id']]])
                    self.assertTrue(row['source_anchors'], row)
                    for anchor in row['source_anchors']:
                        name, _, mark = anchor.partition('#')
                        self.assertIn(f'<a id="{mark}"></a>', case['files'][name], anchor)

    def test_the_same_seed_gives_the_same_case_and_another_seed_another(self):
        first, again, other = (generate.build_case(FAMILY, seed, 3) for seed in (5, 5, 6))
        self.assertEqual(first, again)
        self.assertNotEqual(first['files'], other['files'])
        self.assertNotEqual(generate.build_case(FAMILY, 5, 3)['files'], generate.build_case(FAMILY, 5, 4)['files'])

    def test_no_generation_error_and_every_seed_builds(self):
        for seed in MANY_SEEDS:
            try:
                case_for(seed)
            except generate.GenerationError as error:       # pragma: no cover - only on failure
                self.fail(f'seed {seed}: {error}')
        self.assertTrue(set(MANY_SEEDS) <= set(CASES))

    def test_neither_report_nor_sources_reveal_the_variant_or_the_errors(self):
        for seed in SEEDS:
            case = case_for(seed)
            for name, text in case['files'].items():
                for word in ('mixed', 'correct report', 'error_type', 'refuted', 'undetermined', 'supported'):
                    self.assertNotIn(word, text.lower() if word != 'error_type' else text, f'{case["dossier"]}/{name}')
            self.assertEqual(case['files']['report-correct.md'].count('**C'), case['files']['report-mixed.md'].count('**C'))

    def test_no_wording_is_only_ever_right_or_only_ever_wrong(self):
        """A wording, or a wording with its citations, that occurs only in altered claims would give the verdict
        away without reading a source, and one that is always right would do the same. Names are taken from each
        case's own staff list, so the check does not depend on the generator's constants. Besides the repository's
        normalisation of names, ids, dates and numbers, this check collapses departments, currencies, lists of
        names or numbers, and the groups of a comparison. Why that is justified: a wrong list always has the true
        list's length, a wrong set of alternatives has the true set's size, and the groups of a comparison are
        exchangeable (random order, random departments and currencies, windows of equal length); that their
        direction is not skewed is asserted in test_comparisons_are_not_skewed. The plain check follows below."""
        seen, _ = wording_shapes()
        one_sided = sorted(key[:2] for key, sides in seen.items() if sides != {True, False})
        self.assertEqual(one_sided, [])
        self.assertGreater(len(seen), 20)
        self.assertEqual({key[0] for key in seen}, KINDS)

    def test_the_plain_wording_check_has_a_known_set_of_one_sided_shapes(self):
        """Only names, ids, dates and numbers are normalised, as the repository's own check does. The one-sided
        shapes it finds are the limit of the family; the count is printed with the failure message and bounded."""
        _, plain = wording_shapes()
        one_sided = sorted((key[0], key[1]) for key, sides in plain.items() if sides != {True, False})
        print(f'\nplain wording check: {len(one_sided)} of {len(plain)} shapes are one-sided')
        for kind, shape in one_sided:
            print(f'  {kind}: {shape}')
        # Pinned: 29 of 71 shapes. 27 are comparisons that name a pair of departments or currencies with a direction,
        # one is a department count, one a signer list of one name. Departments, currencies and list lengths are
        # collapsed in the main check, with the reasons given there.
        self.assertEqual(collections.Counter(kind for kind, _ in one_sided),
                         {'failure_comparison': 27, 'department_failures': 1, 'request_signers': 1})
        self.assertEqual(len(plain), 71)

    def test_every_rule_is_exercised_by_a_request_that_changes_and_by_an_injected_slip(self):
        flips = collections.defaultdict(int)
        injected = collections.Counter()
        for seed in MANY_SEEDS:
            case = case_for(seed)
            reading = Reading(source_files(case))
            base = reading.register(0)
            for rule, switch in RULES.items():
                other = reading.register(0, frozenset([switch]))
                if any((base[i]['complies'], base[i]['tier']) != (other[i]['complies'], other[i]['tier']) for i in base):
                    flips[rule] += 1
            for row in case['key']['expectations']:
                if row['variant'] == 'mixed' and row['error_type']:
                    injected[row['error_type']] += 1
        self.assertEqual(set(flips), set(RULES), 'a rule that never changes any request is not exercised')
        for rule in RULES:
            self.assertGreaterEqual(flips[rule], len(MANY_SEEDS) * 0.9, f'{rule}: {flips[rule]} of {len(MANY_SEEDS)} dossiers')
            for slip in SLIPS_OF_RULES[rule]:
                self.assertGreaterEqual(injected[slip], 1, f'the slip {slip} (leaving out: {rule}) is never injected')
        for slip in ('takes_neighbouring_row', 'asserts_unknown_as_fact', 'treats_unknown_as_settled', 'overstated',
                     'reversed_direction', 'assumes_unknown_signature_counts', 'treats_limit_as_exclusive'):
            self.assertGreaterEqual(injected[slip], 1, slip)

    def test_per_request_claims_are_decided_by_at_least_two_rules(self):
        """At least three claims per dossier are about a single request that no unknown touches and whose figure
        changes when either of two different rules is left out. Counted with the toggles of the second derivation,
        which are the seven rules and nothing else (no slip that merely swaps an input)."""
        figure = {'request_compliance': 'complies', 'required_tier': 'tier', 'assessed_amount': 'assessed',
                  'counting_approvals': 'counting'}
        for seed in MANY_SEEDS:
            case = case_for(seed)
            reading = Reading(source_files(case))
            hard = 0
            for row in case['key']['expectations']:
                if row['variant'] != 'correct' or row['claim_kind'] not in figure:
                    continue
                ident = re.search(r'PR-\d+', row['claim_text'])[0]
                key = figure[row['claim_kind']]
                if any(reading.register(n)[ident][key] != reading.register(0)[ident][key] for n in range(len(reading.worlds))):
                    continue                # an unknown touches this request
                rules = sum(reading.register(0, frozenset([switch]))[ident][key] != reading.register(0)[ident][key]
                            for switch in RULES.values())
                hard += rules >= 2
            self.assertGreaterEqual(hard, 3, case['dossier'])

    def test_size_mix_and_unknowns(self):
        states, settled = set(), 0
        for seed in MANY_SEEDS:
            case = case_for(seed)
            sources = source_files(case)
            reading = Reading(sources)
            self.assertTrue(40 <= len(reading.requests) <= 80, (case['dossier'], len(reading.requests)))
            words = sum(len(text.split()) for text in sources.values())
            self.assertTrue(2500 <= words <= 7000, (case['dossier'], words))
            counts = {variant: sum(r['variant'] == variant for r in case['key']['expectations']) for variant in ('correct', 'mixed')}
            self.assertEqual(counts['correct'], counts['mixed'])
            self.assertTrue(10 <= counts['correct'] <= 14, (case['dossier'], counts))
            self.assertTrue(2 <= len(reading.open) <= 4 and len(reading.worlds) <= 500, (case['dossier'], len(reading.worlds)))
            mixed = [r['semantic_state'] for r in case['key']['expectations'] if r['variant'] == 'mixed']
            states.update(mixed)
            kinds_in_correct = collections.Counter(r['claim_kind'] for r in case['key']['expectations'] if r['variant'] == 'correct')
            self.assertGreaterEqual(sum(kinds_in_correct[k] for k in ('request_compliance', 'required_tier', 'assessed_amount',
                                                                      'counting_approvals')), 3)
            self.assertGreaterEqual(sum(kinds_in_correct[k] for k in ('failing_requests', 'failing_total', 'tier_three_requests')), 3)
            self.assertGreaterEqual(kinds_in_correct['department_top'] + kinds_in_correct['failure_comparison'], 1)
            self.assertGreaterEqual(kinds_in_correct['request_amount'] + kinds_in_correct['request_signers'], 1)
            # at least one claim about an unreadable entry that the sources settle all the same
            for row in case['key']['expectations']:
                if row['variant'] == 'correct' and row['claim_kind'] == 'request_compliance':
                    ident = re.search(r'PR-\d+', row['claim_text'])[0]
                    if ('signer', ident) in reading.open and row['derivation']['worlds'] > 1:
                        settled += 1
                        break
        self.assertEqual(states, {'supported', 'refuted', 'undetermined'})
        self.assertEqual(settled, len(MANY_SEEDS), 'every dossier has a settled claim about an unreadable signature')

    def test_altered_reports_hold_open_claims_and_the_unreadable_slot_does_not_give_itself_away(self):
        """The case asks `build_case` for 2 open claims; every altered report must hold min(2, slots that offer an
        open variant) of them, and at least 90 percent of the dossiers have two such slots. The slot about an
        unreadable entry keeps its kind, wording frame and citations in its wrong variants, which are refuted lists in
        which every alternative is swapped; the open variants are in other slots, about requests that nothing else names."""
        distribution, offering, mild = collections.Counter(), collections.Counter(), 0
        unreadable = collections.Counter()
        for seed in MANY_SEEDS:
            case = case_for(seed)
            built = generate.family_builder(FAMILY)(random.Random(f'evalsuite:{generate.GENERATOR_VERSION}:{seed}:1:{FAMILY}'))
            self.assertEqual(built['open_claims'], 2)
            slots = sum(any(generate.judge(w, built['base'], built['unknowns'])[0] == 'undetermined' for w in wrong)
                        for _, wrong in built['slots'])
            offering[min(slots, 3)] += 1
            mixed = [r for r in case['key']['expectations'] if r['variant'] == 'mixed']
            undetermined = sum(r['semantic_state'] == 'undetermined' for r in mixed)
            distribution[min(undetermined, 3)] += 1
            self.assertGreaterEqual(undetermined, min(2, slots), case['dossier'])
            for variant in ('correct', 'mixed'):
                held = [r for r in case['key']['expectations'] if r['variant'] == variant
                        and r['claim_kind'] in ('unreadable_signature', 'unreadable_amount')]
                self.assertEqual(len(held), 1, f'{case["dossier"]} {variant}: the report must hold the unreadable-entry claim')
                if variant == 'mixed':
                    unreadable[(held[0]['claim_kind'], held[0]['semantic_state'])] += 1
            reading = Reading(source_files(case))
            mild += not any(len({reading.register(n)[r['id']]['complies'] for n in range(len(reading.worlds))}) > 1
                            for r in reading.requests)
        print(f'\nslots that offer an open variant (0, 1, 2, 3 or more dossiers): {[offering[i] for i in range(4)]}')
        print(f'undetermined claims per altered report (0, 1, 2, 3 or more dossiers): {[distribution[i] for i in range(4)]}')
        print(f'unreadable-entry claims in altered reports by kind and state: {sorted(unreadable.items())}; mild dossiers: {mild} of 60')
        self.assertGreaterEqual(sum(offering[i] for i in (2, 3)), 0.9 * len(MANY_SEEDS))
        self.assertEqual(distribution[0] + distribution[1], 0)
        total = sum(unreadable.values())
        self.assertEqual(total, len(MANY_SEEDS))
        # The slot has one true form and wrong lists in which every alternative is swapped; a wrong list is refuted.
        refuted = sum(n for (kind, state), n in unreadable.items() if state == 'refuted')
        self.assertEqual(refuted + sum(n for (kind, state), n in unreadable.items() if state == 'supported'), total)
        self.assertGreaterEqual(refuted, 0.2 * total, unreadable)


    def test_every_slot_variant_agrees_with_the_second_derivation(self):
        """Not only the claims that were drawn into a report: the true claim and every wrong variant of every slot, so
        that a rule slip that no drawn claim shows is still caught (the wrong variants are the ones the engine runs
        with a rule left out)."""
        checked = collections.Counter()
        for seed in MANY_SEEDS:
            case = built(seed)
            reading = Reading(case['sources'])
            for true, wrong in case['slots']:
                texts = [item['text'] for item in wrong]
                self.assertEqual(len(set(texts)), len(texts), f'seed {seed}: two wrong variants share a sentence')
                self.assertNotIn(true['text'], texts)
                for item in [true] + wrong:
                    state = generate.judge(item, case['base'], case['unknowns'])[0]
                    self.assertEqual(reading.decide(item['kind'], item['text']), state, f'seed {seed}: {item["text"]}')
                    checked[item['kind']] += 1
                kinds = {item['kind'] for item in [true] + wrong}
                self.assertEqual(kinds, {true['kind']}, f'seed {seed}: a wrong variant must keep the kind of the claim it replaces')
        self.assertEqual(set(checked), KINDS)
        self.assertGreater(sum(checked.values()), 3000)

    def test_each_slip_of_the_engine_gives_the_figures_of_the_second_derivation_with_the_same_switch(self):
        """The wrong variants are the engine run with one rule left out or one input swapped. Here every one of the
        twelve runs is compared, request by request and world by world, with the second derivation's own switch."""
        import hard_procurement as engine
        compared = collections.Counter()
        for seed in MANY_SEEDS:
            case = built(seed)
            reading = Reading(case['sources'])
            data = case['base']['data']
            worlds = list(generate.worlds(case['base'], case['unknowns']))
            self.assertEqual(len(worlds), len(reading.worlds))
            for error, flag in engine.SLIPS:
                self.assertEqual(SLIP_SWITCHES[error], {'conversion': 'convert'}.get(flag, flag), error)
                for number in (0, len(worlds) // 2, len(worlds) - 1):
                    run = engine.assess(data, worlds[number], frozenset([flag]))
                    # the worlds are enumerated in the same order by both: the unknowns sorted by name, last value fastest
                    mine = reading.register(number, frozenset([SLIP_SWITCHES[error]]))
                    for row in run.rows:
                        self.assertEqual((row['assessed'], row['tier'], row['counting'], row['complies']),
                                         tuple(mine[row['id']][k] for k in ('assessed', 'tier', 'counting', 'complies')),
                                         f'seed {seed} {error} {row["id"]} world {number}')
                    compared[error] += 1
        self.assertEqual(len(compared), 12)

    def test_both_reports_hold_the_same_claim_kinds(self):
        for seed in MANY_SEEDS:
            case = case_for(seed)
            count = {variant: collections.Counter(r['claim_kind'] for r in case['key']['expectations'] if r['variant'] == variant)
                     for variant in ('correct', 'mixed')}
            self.assertEqual(count['correct'], count['mixed'], case['dossier'])

    def test_no_claim_text_contains_none(self):
        for seed, index in [(410, 3), (438, 2), (871, 2), (1129, 3)]:
            for row in generate.build_case(FAMILY, seed, index)['key']['expectations']:
                self.assertNotIn('None', row['claim_text'], (seed, index))
        for seed in MANY_SEEDS:
            for index in (1, 2, 3):
                for true, wrong in built(seed, index)['slots']:
                    for item in [true] + wrong:
                        self.assertNotIn('None', item['text'], (seed, index))

    def test_either_outcome_of_a_compliance_claim_is_as_often_right_as_wrong(self):
        """Each wording of a compliance claim is right in a share of its appearances in altered reports that is
        neither small nor large, so the wording alone says little. Measured over seeds 1 to 300: about 66 percent of
        each wording's appearances are right (210 of 311 and 189 of 289; 407 of 608 and 388 of 592 over seeds 1 to
        600), which is one minus the share of its slots that are altered. The band of 35 to 72 percent holds with
        about two standard deviations of margin; the 65 percent hoped for is not reached."""
        right, total, correct = collections.Counter(), collections.Counter(), collections.Counter()
        for seed in range(1, 301):
            for row in case_for(seed)['key']['expectations']:
                if row['claim_kind'] != 'request_compliance':
                    continue
                wording = 'complies' if ' complies ' in row['claim_text'] else 'does not comply'
                if row['variant'] == 'correct':
                    correct[wording] += 1
                else:
                    total[wording] += 1
                    right[wording] += row['semantic_state'] == 'supported'
        print(f'\ncompliance claims in correct reports: {dict(correct)}; right/appearing in altered reports: '
              f'{ {w: (right[w], total[w]) for w in total} }')
        for wording in ('complies', 'does not comply'):
            self.assertGreaterEqual(right[wording] / total[wording], 0.35, wording)
            self.assertLessEqual(right[wording] / total[wording], 0.72, wording)
            self.assertGreaterEqual(correct[wording] / sum(correct.values()), 0.4, wording)

    def test_the_policies_are_two_well_formed_issues_and_the_index_alone_says_which_is_in_force(self):
        in_force, later = collections.Counter(), collections.Counter()
        for seed in MANY_SEEDS:
            case = case_for(seed)
            sources = source_files(case)
            self.assertEqual(list(sources), sorted(sources), 'the files are in sorted order')
            reading = Reading(sources)
            in_force[reading.policy_file] += 1
            issue = lambda name: day(re.search(r'issue of (\S+)$', sources[name].splitlines()[0])[1])  # noqa: E731
            later[issue(reading.policy_file) > issue(reading.other_file)] += 1
            one, other = sources[POLICY_FILES[0]], sources[POLICY_FILES[1]]
            outline = lambda text: re.findall(r'<a id="([^"]+)"></a>\n## \1 — (.+)', text)  # noqa: E731
            self.assertEqual(outline(one), outline(other), 'same section ids and headings')
            self.assertEqual(len(outline(one)), 8)
            self.assertLessEqual(abs(len(one.split()) - len(other.split())), 0.06 * max(len(one.split()), len(other.split())))
            for text in (one, other):
                self.assertNotRegex(text.lower(), r'previous|supersed|replace|force|earlier|later|proposed|revision|adopted|draft|current|obsolete',
                                    'neither issue says which is in force')
                self.assertFalse(any(name[:-3] in text for name in POLICY_FILES), 'neither names the other')
            self.assertEqual(sorted(set(re.findall(r'[\w-]+\.md', one))), sorted(set(re.findall(r'[\w-]+\.md', other))))
            # what settles which one counts is in the index, with its reason
            index = sections(sources['document-index.md'])['DI-03']
            self.assertIn(reading.policy_file, index)
            self.assertRegex(index, r'replaced it|was not adopted')
            for variant in ('correct', 'mixed'):
                ledger = run_blind.paragraphs(case['files'][f'report-{variant}.md'])
                anchors = run_blind.claim_anchors(ledger)
                for row in case['key']['expectations']:
                    if row['variant'] != variant:
                        continue
                    cited = ledger[anchors[row['claim_id']]].split(row['claim_text'], 1)[1]
                    named = re.findall(r'policy-[ab]', cited)
                    if named:
                        # the same two issues in every claim that cites a policy, in the same order, with the status entry
                        self.assertEqual(sorted(set(named)), ['policy-a', 'policy-b'], cited)
                        self.assertLess(cited.index('policy-a'), cited.index('policy-b'))
                        self.assertIn('document-index DI-02', cited)
                    elif row['claim_kind'] not in ('request_amount', 'request_signers', 'unreadable_signature', 'unreadable_amount'):
                        self.fail(f'{case["dossier"]}: a claim that depends on the policy cites none: {cited}')
                    # the key names the passages that decide, and none of the issue that settles nothing
                    for anchor in row['source_anchors']:
                        self.assertNotIn(reading.other_file, anchor)
                    if named:
                        self.assertTrue(any(a.startswith(f'sources/{reading.policy_file}#') for a in row['source_anchors']))
        self.assertGreaterEqual(min(in_force.values()), len(MANY_SEEDS) * 0.35, in_force)
        self.assertEqual(set(in_force), set(POLICY_FILES))
        self.assertGreaterEqual(min(later.values()), len(MANY_SEEDS) * 0.35, later)
        self.assertEqual(set(later), {True, False}, 'both date orders occur')

    def test_a_week_can_straddle_the_effective_date_and_each_request_keeps_its_own_limits(self):
        dossiers, claims = 0, 0
        for seed in MANY_SEEDS:
            case = case_for(seed)
            reading = Reading(source_files(case))
            groups = collections.defaultdict(list)
            for r in reading.requests:
                groups[(r['who'], r['supplier'], r['date'].isocalendar()[:2])].append(r['id'])
            register = reading.register(0)
            straddling = [ids for ids in groups.values()
                          if len({reading.by_id[i]['date'] >= reading.effective for i in ids}) == 2
                          and len({register[i]['tier'] for i in ids}) > 1]
            dossiers += bool(straddling)
            members = {i for ids in straddling for i in ids}
            for row in case['key']['expectations']:
                if row['variant'] == 'correct' and re.search(r'PR-\d+', row['claim_text']) and \
                        row['claim_kind'] in ('request_compliance', 'required_tier', 'assessed_amount', 'counting_approvals'):
                    claims += re.search(r'PR-\d+', row['claim_text'])[0] in members
        print(f'\nweeks that straddle the effective date with members in different tiers: {dossiers} of 60 dossiers; '
              f'per-request claims about such a member: {claims}')
        self.assertGreaterEqual(dossiers, 45)
        self.assertGreaterEqual(claims, 15)
    def test_no_request_is_the_subject_of_two_claims_in_either_report(self):
        """One rule, held in both reports and in every slot: a request is named by one claim only. A second claim
        about the request of the unreadable-entry claim, or two claims about one request, would be found far more often
        in altered reports. The wrong variants about an open fact name requests that nothing else names."""
        for seed in MANY_SEEDS:
            case = case_for(seed)
            for variant in ('correct', 'mixed'):
                named = collections.Counter(i for r in case['key']['expectations'] if r['variant'] == variant
                                            for i in set(re.findall(r'PR-\d+', r['claim_text'])))
                self.assertEqual([i for i, n in named.items() if n > 1], [], f'{case["dossier"]} {variant}')
            slots = built(seed)['slots']
            seen = set()
            for true, wrong in slots:
                ids = {i for item in [true] + wrong for i in re.findall(r'PR-\d+', item['text'])}
                self.assertFalse(ids & seen, f'seed {seed}: two slots name {ids & seen}')
                seen |= ids

    def test_lists_of_alternatives_are_written_in_one_order_everywhere(self):
        """Names in alphabetical order and amounts in increasing order, in the notes, in the true claim and in the wrong
        ones, so that the order of a list says nothing about it. (Before, the notes kept the drawn order and every wrong
        list was sorted: an unsorted list was never wrong.)"""
        lists = 0
        for seed in MANY_SEEDS:
            case = built(seed)
            texts = [item['text'] for true, wrong in case['slots'] for item in [true] + wrong]
            texts += [sections(case['sources']['requests.md'])['RQ-02'], sections(case['sources']['delegations.md'])['D-02']]
            for text in texts:
                for names in re.findall(r'(?:signature on request \S+ is by one of|The signer is one of) ([^.]+)\.', text):
                    lists += 1
                    listed = split_names(names)
                    self.assertEqual(listed, sorted(listed), (seed, names))
                for values, _ in re.findall(r'(?:amount of request \S+ is one of|It is one of) ([\d, or]+) ([A-Z]{3})\.', text):
                    lists += 1
                    listed = [int(v) for v in split_names(values)]
                    self.assertEqual(listed, sorted(listed), (seed, values))
        self.assertGreater(lists, 300)

    def test_the_register_does_not_show_which_issue_of_the_policy_is_in_force(self):
        """Two guessers a reader could use, each right about half the time. One picks the issue whose A or B is the exact
        amount of a euro request. The other picks the issue whose limits have more requests within 4 percent, by the
        amount as written and by the converted amount. B is four times A for the issue in force, for the other issue and
        for the amendment alike, so that relation says nothing either."""
        score, structure = collections.Counter(), collections.Counter()
        for seed in range(1, 301):
            reading = Reading(source_files(case_for(seed)))
            issues = {'in': (reading.low, reading.high), 'out': (reading.low0, reading.high0)}
            printed = [(r['amount'], r['currency']) for r in reading.requests if r['amount'] is not None]
            converted = [v['converted'] for v in reading.register(0).values()]
            counts = {
                'exact': {k: sum(x in (a, b) for x, c in printed if c == 'EUR') for k, (a, b) in issues.items()},
                'near': {k: sum(abs(x - a) <= 0.04 * a or abs(x - b) <= 0.04 * b for x, c in printed) for k, (a, b) in issues.items()},
                'near_converted': {k: sum(abs(x - a) <= 0.04 * a or abs(x - b) <= 0.04 * b for x in converted)
                                   for k, (a, b) in issues.items()}}
            for guess, got in counts.items():
                score[guess] += 0.5 if got['in'] == got['out'] else got['in'] > got['out']
            structure['in'] += reading.high == 4 * reading.low
            structure['out'] += reading.high0 == 4 * reading.low0
            structure['amendment'] += reading.high2 == 4 * reading.low2
        print(f'\nshare of dossiers in which each guesser picks the issue in force: '
              f'{ {k: round(v / 300, 3) for k, v in score.items()} }')
        for guess, hits in score.items():
            self.assertTrue(0.42 <= hits / 300 <= 0.58, (guess, hits / 300))
        self.assertEqual(dict(structure), {'in': 300, 'out': 300, 'amendment': 300})

    def test_the_exemption_is_written_as_an_exception_and_decided_by_the_assessed_amount(self):
        """P-04 states the exemption inside the tier 1 sentence and P-08 measures compliance by what P-04 says is
        needed. A pair of one week whose converted amounts are each within the exemption and whose sum is not is in the
        register, so that applying the exemption to the converted amount gives another verdict."""
        with_exemption = decided = 0
        for seed in MANY_SEEDS:
            sources = source_files(case_for(seed))
            reading = Reading(sources)
            self.assertIn('that P-04 says the request needs', sections(sources[reading.policy_file])['P-08'])
            self.assertIn('that P-04 says the request needs', sections(sources[reading.other_file])['P-08'])
            if reading.exempt is None:
                self.assertNotIn('except that', sections(sources[reading.policy_file])['P-04'])
                continue
            with_exemption += 1
            self.assertRegex(sections(sources[reading.policy_file])['P-04'],
                             rf'in tier 1 needs at least 1 counting approval, except that a request whose assessed amount is '
                             rf'at most {reading.exempt} euro needs no counting approval\. A request in tier 2')
            weeks = collections.defaultdict(list)
            for r in reading.requests:
                weeks[(r['who'], r['supplier'], r['date'].isocalendar()[:2])].append(r)
            register = reading.register(0)
            for members in weeks.values():
                if len(members) == 2 and all(m['currency'] == 'EUR' and not m['signed'] and m['amount'] <= reading.exempt for m in members) \
                        and sum(m['amount'] for m in members) > reading.exempt:
                    decided += 1
                    self.assertEqual([register[m['id']]['complies'] for m in members], [False, False])
                    break
        self.assertGreaterEqual(with_exemption, 0.4 * len(MANY_SEEDS))
        self.assertEqual(decided, with_exemption)

    def test_lookups_name_a_row_that_has_a_near_duplicate_next_to_it(self):
        for seed in MANY_SEEDS:
            case = case_for(seed)
            reading = Reading(source_files(case))
            twins = {}
            for a, b in zip(reading.requests, reading.requests[1:]):
                if None not in (a['amount'], b['amount']) and (a['who'], a['supplier'], a['date'], a['currency']) == (
                        b['who'], b['supplier'], b['date'], b['currency']) and 0 < abs(a['amount'] - b['amount']) < 10:
                    twins[a['id']] = twins[b['id']] = (a, b)
            self.assertEqual(len(twins), 2, case['dossier'])
            lookups = [r for r in case['key']['expectations'] if r['variant'] == 'correct'
                       and r['claim_kind'] in ('request_amount', 'request_signers')]
            self.assertEqual(len(lookups), 1, case['dossier'])
            self.assertIn(re.search(r'PR-\d+', lookups[0]['claim_text'])[0], twins)
            wrong = [r for r in case['key']['expectations'] if r['variant'] == 'mixed' and r['claim_kind'] in ('request_amount', 'request_signers')
                     and r['error_type']]
            for row in wrong:
                self.assertEqual(row['error_type'], 'takes_neighbouring_row')
                self.assertEqual(row['semantic_state'], 'refuted')

    def test_comparisons_are_not_skewed(self):
        """Each kind of group is compared in both directions, so that neither "greater" nor "smaller" is the likely truth."""
        seen = collections.defaultdict(collections.Counter)
        for seed in MANY_SEEDS:
            for row in case_for(seed)['key']['expectations']:
                if row['variant'] == 'correct' and row['claim_kind'] == 'failure_comparison':
                    group = 'window' if 'dated from' in row['claim_text'] else 'currency' if 'paid in' in row['claim_text'] else 'department'
                    seen[group]['greater' if ' is greater than ' in row['claim_text'] else 'smaller'] += 1
        self.assertEqual(set(seen), {'window', 'currency', 'department'})
        for group, counts in seen.items():
            self.assertGreaterEqual(min(counts['greater'], counts['smaller']), 3, (group, counts))

    def test_ties_never_decide_a_claim(self):
        """A superlative or a comparison needs a strict answer in every world, so the generator never uses a tie."""
        tops = comparisons = 0
        for seed in MANY_SEEDS:
            case = case_for(seed)
            reading = Reading(source_files(case))
            for row in case['key']['expectations']:
                if row['variant'] != 'correct' or row['claim_kind'] not in ('department_top', 'failure_comparison'):
                    continue
                self.assertEqual(row['semantic_state'], 'supported')
                if row['claim_kind'] == 'department_top':
                    tops += 1
                    department = re.search(r'the (\w+) department', row['claim_text'])[1]
                    for n in range(len(reading.worlds)):
                        failing = sorted((v['assessed'] for i, v in reading.register(n).items()
                                          if not v['complies'] and reading.department(i) == department), reverse=True)
                        self.assertGreater(failing[0], failing[1], f'{case["dossier"]}: the largest amount is tied')
                else:
                    comparisons += 1
                    sentence = row['claim_text']
                    # the counts of the two groups differ in every world, so neither "greater" nor "smaller" is a tie
                    self.assertEqual(reading.decide('failure_comparison', sentence), 'supported')
                    opposite = sentence.replace(' is greater than ', ' is smaller than ') if ' is greater than ' in sentence \
                        else sentence.replace(' is smaller than ', ' is greater than ')
                    self.assertEqual(reading.decide('failure_comparison', opposite), 'refuted')
        self.assertGreaterEqual(tops, 0.9 * len(MANY_SEEDS))
        self.assertGreaterEqual(comparisons, 0.9 * len(MANY_SEEDS))

    def test_report_passages_are_split_as_the_audit_helper_splits_them(self):
        import audit  # the skill's own helper
        for seed in (1, 2, 3):
            for variant in ('correct', 'mixed'):
                text = case_for(seed)['files'][f'report-{variant}.md']
                self.assertEqual(run_blind.paragraphs(text), audit.paragraphs(text))


class ProcurementSuiteTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + '-', dir=TEST_RUN))

    def test_a_written_suite_loads_in_the_scorer_seals_and_stages(self):
        fixtures, keys = self.root / 'fixtures', self.root / 'keys'
        summary = generate.write_suite(fixtures, keys, 9, 3, [FAMILY])
        self.assertEqual(len(summary['dossiers']), 3)
        self.assertEqual(sum(summary['expected_states'].values()), summary['claims'])
        self.assertEqual({entry['family'] for entry in summary['dossiers'].values()}, {FAMILY})
        loaded = scorecard.load_keys(keys)
        self.assertEqual(sorted(loaded), sorted(summary['dossiers']))
        self.assertEqual({key['status'] for key in loaded.values()}, {'computed_by_generator'})
        arena, sealed = self.root / 'arena', self.root / 'sealed'
        arena.mkdir()
        blindness.seal_keys(keys, sealed, [arena])
        for dossier in summary['dossiers']:
            for variant, other in (('correct', 'mixed'), ('mixed', 'correct')):
                record = blindness.stage_case(fixtures / dossier / f'report-{variant}.md', fixtures / dossier / 'sources',
                                              arena, self.root / 'records', {'dossier': dossier, 'variant': variant},
                                              withheld=[sealed, fixtures / dossier / f'report-{other}.md'])
                staged = Path(record['arena_run'])
                self.assertEqual((staged / 'case/report.md').read_bytes(),
                                 (fixtures / dossier / f'report-{variant}.md').read_bytes())
                self.assertNotIn(dossier, record['arena_run'])
                self.assertEqual(sorted(p.name for p in (staged / 'case/sources').iterdir()),
                                 sorted(p.name for p in (fixtures / dossier / 'sources').iterdir()))
        for folder in fixtures.rglob('*'):
            self.assertFalse(folder.name.endswith('.json'), 'no answer material belongs in the fixtures')

    def test_key_wording_is_not_flagged_when_an_auditor_restates_the_claims(self):
        for seed in (1, 2, 3):
            case = case_for(seed)
            run = self.root / case['dossier']
            (run / 'case').mkdir(parents=True)
            (run / 'case/report.md').write_text(case['files']['report-mixed.md'], encoding='utf-8')
            notes = '\n'.join(f'{r["claim_id"]}: {r["claim_text"]} I recomputed this from the cited tables and it is '
                              f'{r["semantic_state"]}. Stated {r["derivation"].get("stated")}, found '
                              f'{r["derivation"].get("feasible_min")} to {r["derivation"].get("feasible_max")}.'
                              for r in case['key']['expectations'] if r['variant'] == 'mixed')
            (run / 'notes.md').write_text(notes, encoding='utf-8')
            scan = blindness.scan_run(run, case['key']['expectations'])
            self.assertEqual(scan['verbatim_overlaps'], [], case['dossier'])
            (run / 'copied.md').write_text(case['key']['expectations'][-1]['rationale'], encoding='utf-8')
            self.assertTrue(blindness.scan_run(run, case['key']['expectations'])['verbatim_overlaps'])


if __name__ == '__main__':
    unittest.main()
