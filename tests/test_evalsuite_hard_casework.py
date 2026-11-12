"""Checks for the harder `casework` family of generated cases.

The generator decides every verdict with one rule engine. The code below decides the same verdicts a second
time without importing anything from the family: it parses the rendered Markdown (periods, amendment, holidays,
register, cover notes, extension decisions), steps through the calendar day by day, enumerates the open facts
itself and reads each claim from its wording. Agreement shows that the documents say what the generator
computed from and that neither computation slipped. It cannot show that a rule sentence has only one reading:
both derivations read the rules the same way, so those sentences need human review.

Words in a source are counted by splitting its text on whitespace.
"""
import collections
import datetime
import itertools
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
sys.path.insert(0, str(ROOT / 'skills/evidence-to-proof-review/scripts'))
import blindness  # noqa: E402
import generate  # noqa: E402
import run_blind  # noqa: E402
import scorecard  # noqa: E402

TEST_ROOT = ROOT / 'tmp'
TEST_ROOT.mkdir(exist_ok=True)
TEST_RUN = Path(tempfile.mkdtemp(prefix='evalsuite-casework-tests-', dir=TEST_ROOT))
SEEDS = range(1, 21)
WIDE = range(1, 61)     # the second derivation and the wording checks run on sixty seeds
ONE = datetime.timedelta(days=1)
CLASSES = ('standard', 'complex', 'urgent')

# The slips the family must be able to inject, and every other name an error may carry.
RULE_SLIPS = {
    1: ('wrong_class_period',),
    2: ('ignores_amendment', 'applies_amendment_too_early'),
    3: ('counts_issue_day',),
    4: ('ignores_nonworking_shift', 'ignores_holiday'),
    5: ('counts_refused_extension', 'counts_pending_extension', 'counts_void_extension', 'extends_urgent_notice',
        'ignores_extension'),
    6: ('ignores_postal_rule',),
    7: ('late_on_deadline_day',)}
DATA_SLIPS = ('uses_draft_register', 'takes_neighbouring_row')
BOUND_ERRORS = ('wrong_value', 'overstated', 'asserts_unknown_as_fact', 'treats_unknown_as_settled')
OTHER_ERRORS = ('ignores_shift_after_extension', 'counts_calendar_days_for_post', 'counts_deadline_day_in_days_late',
                'names_runner_up')
KNOWN_ERRORS = set(itertools.chain.from_iterable(RULE_SLIPS.values())) | set(DATA_SLIPS) | set(BOUND_ERRORS) | set(OTHER_ERRORS)

_CASES, _READINGS = {}, {}


def reading_of(seed):
    if seed not in _READINGS:
        _READINGS[seed] = Reading(sources_of(case(seed)))
    return _READINGS[seed]


def case(seed, index=1):
    if (seed, index) not in _CASES:
        _CASES[(seed, index)] = generate.build_case('casework', seed, index)
    return _CASES[(seed, index)]


def sources_of(built):
    return {name[len('sources/'):]: text for name, text in built['files'].items() if name.startswith('sources/')}


def sections(text):
    parts = re.split(r'<a id="([^"]+)"></a>\n', text)
    return dict(zip(parts[1::2], parts[2::2]))


def rows(body):
    lines = [line for line in body.splitlines() if line.startswith('|')]
    cells = lambda line: [cell.strip() for cell in line.strip('|').split('|')]  # noqa: E731
    return [dict(zip(cells(lines[0]), cells(line))) for line in lines[2:]] if lines else []


def day(text):
    return datetime.date.fromisoformat(text)


def over_all(outcomes):
    outcomes = list(outcomes)
    return 'supported' if all(outcomes) else 'undetermined' if any(outcomes) else 'refuted'


# ---------- The second derivation ----------

class Reading:
    """The dossier as an auditor reads it from the rendered documents alone."""

    def __init__(self, sources):
        procedure = sections(sources['procedure.md'])
        found = re.search(r'is (\d+) days for a standard notice, (\d+) days for a complex notice and (\d+) days for an '
                          r'urgent notice', procedure['P-02'])
        self.periods = dict(zip(CLASSES, map(int, found.groups())))
        amendment = sections(sources['amendment.md'])
        self.effective = day(re.search(r'on or after (\d{4}-\d\d-\d\d)', amendment['A-01'])[1])
        self.amended = {cls: int(days) for cls, days in re.findall(
            r'The reply period of an? (standard|complex|urgent) notice is (\d+) days\.', amendment['A-01'])}
        self.holidays = {day(r['Date']) for r in rows(sections(sources['calendar.md'])['C-02'])}
        # The register in force is the one the document index lists as in force; nothing else says which it is.
        listed = rows(sections(sources['document-index.md'])['D-02'])
        in_force = [r['Document'] for r in listed if r['Document'].startswith('register-') and r['Status'] == 'In force']
        assert len(in_force) == 1 and sum(r['Document'].startswith('register-') for r in listed) == 2, listed
        self.register_file = in_force[0]
        self.other_file = ({'register-a.md', 'register-b.md'} - {self.register_file}).pop()
        self.other_register = rows(sections(sources[self.other_file])['R-01'])
        register = sections(sources[self.register_file])
        self.register = rows(register['R-01'])
        self.by_id = {r['Notice']: r for r in self.register}
        self.total = int(re.search(r': (\d+) notices\.', register['R-01'])[1])
        self.posting = {nid: self.days(listed) for nid, listed in re.findall(
            r'The register records no reply date for notice (N\d+)\. The cover note filed with that reply states that it '
            r'was posted on one of these days: ([^.]+)\. Each of these days is possible, whether or not it is a '
            r'working day, and nothing else about the posting date is known\.', register['R-03'])}
        extensions = sections(sources['extensions.md'])
        self.requests = {r['Notice']: r for r in rows(extensions['X-01'])}
        self.decision_days = {ref: self.days(listed) for ref, listed in re.findall(
            r'The register records no date of decision for request (X\d+)\. The minute of the extension panel for that '
            r'request states that the decision was taken on one of these days: ([^.]+)\. Each of these days is possible, '
            r'whether or not it is a working day, and nothing else about the date of the decision is known\.', extensions['X-02'])}
        self.cache = {}
        self.snapshots = [self.snapshot(world) for world in self.worlds()]

    @staticmethod
    def days(listed):
        return [day(text) for text in re.findall(r'\d{4}-\d\d-\d\d', listed)]

    def closed(self, date):
        return date.weekday() in (5, 6) or date in self.holidays

    def move(self, date, off):
        if 4 not in off:
            while self.closed(date):
                date += ONE
        return date

    def decide(self, row, sent, decided, off=frozenset()):
        """The result for one notice with some rules switched off: 1 the class sets the period, 2 the amendment,
        3 the issue day is not counted, 4 the shift, 5 the extension, 6 the postal rule, 7 on time means on or
        before the deadline. Rule 5 is also switched off by `all_requests`, which lets every request count."""
        cls = row['Class']
        if 1 in off:
            cls = CLASSES[(CLASSES.index(cls) + 1) % 3]
        issued = day(row['Date issued'])
        period = self.periods[cls]
        if 2 not in off and cls in self.amended and issued >= self.effective:
            period = self.amended[cls]
        last = issued
        for _ in range(period - (1 if 3 in off else 0)):
            last += ONE
        basic = self.move(last, off)
        deadline, took = basic, False
        request = self.requests.get(row['Notice'])
        if request and 5 not in off:
            granted = request['Decision'] == 'Granted'
            decision = decided if decided else None
            valid = granted and cls != 'urgent' and decision <= basic
            if valid or 'all_requests' in off:
                extended = basic
                for _ in range(int(request['Days requested'])):
                    extended += ONE
                deadline, took = self.move(extended, off), True
        # P-08: later than the last day, or later than the last day plus the days requested when an extension takes effect.
        reference = last
        for _ in range(int(request['Days requested']) if took else 0):
            reference += ONE
        shifted = deadline > reference
        received = sent
        if row['Reply channel'] == 'Post' and 6 not in off:
            found = 0
            while found < 2:
                received += ONE
                found += not self.closed(received)
        late = received >= deadline if 7 in off else received > deadline
        return {'deadline': deadline, 'took': took, 'shifted': shifted, 'received': received, 'late': late,
                'days': (received - deadline).days if late else 0}

    def worlds(self):
        names = sorted(self.posting) + sorted(self.decision_days)
        axes = [self.posting.get(n) or self.decision_days[n] for n in names]
        for combination in itertools.product(*axes):
            yield dict(zip(names, combination))

    def option(self, row, world, off=frozenset()):
        nid = row['Notice']
        sent = world[nid] if nid in self.posting else day(row['Reply date'])
        ref = row['Extension request']
        request = self.requests.get(nid)
        decided = None
        if request and request['Decision'] == 'Granted':
            decided = world[ref] if ref in self.decision_days else day(request['Date of the decision'])
        key = (nid, sent, decided, off)
        if key not in self.cache:
            self.cache[key] = self.decide(row, sent, decided, off)
        return self.cache[key]

    def snapshot(self, world, off=frozenset()):
        return {r['Notice']: self.option(r, world, off) for r in self.register}

    # ---- claims ----

    def counts(self, test, members=None):
        members = members if members is not None else [r['Notice'] for r in self.register]
        return [sum(test(snap[n]) for n in members) for snap in self.snapshots]

    def quantified(self, lead, value, counts):
        return over_all([c == value if lead == 'Exactly' else c >= value for c in counts])

    def verdict(self, text):
        """(kind, verdict) of one claim, read from its wording."""
        date = r'(\d{4}-\d\d-\d\d)'
        if m := re.fullmatch(r'The deadline of notice (N\d+) is (not earlier than )?' + date + r'\.', text):
            want = day(m[3])
            return 'deadline_date', over_all((s[m[1]]['deadline'] >= want) if m[2] else (s[m[1]]['deadline'] == want)
                                             for s in self.snapshots)
        if m := re.fullmatch(r'The reply to notice (N\d+) was received (after|on or before) its deadline\.', text):
            return 'reply_timing', over_all(s[m[1]]['late'] == (m[2] == 'after') for s in self.snapshots)
        if m := re.fullmatch(r'The reply to notice (N\d+) was received (\d+) days? after its deadline\.', text):
            return 'days_late', over_all(s[m[1]]['late'] and s[m[1]]['days'] == int(m[2]) for s in self.snapshots)
        if m := re.fullmatch(r'The reply to notice (N\d+) was received on (or after )?' + date + r'\.', text):
            want = day(m[3])
            return 'receipt_date', over_all((s[m[1]]['received'] >= want) if m[2] else (s[m[1]]['received'] == want)
                                            for s in self.snapshots)
        if m := re.fullmatch(r'(Exactly|At least) (\d+) of the (\d+) notices in the register had a reply received '
                             r'after the deadline\.', text):
            assert int(m[3]) == len(self.register) == self.total
            return 'late_replies', self.quantified(m[1], int(m[2]), self.counts(lambda r: r['late']))
        if m := re.fullmatch(r'(Exactly|At least) (\d+) of the (\d+) notices in the register have a shifted deadline\.', text):
            assert int(m[3]) == len(self.register)
            return 'shifted_deadlines', self.quantified(m[1], int(m[2]), self.counts(lambda r: r['shifted']))
        if m := re.fullmatch(r'(Exactly|At least) (\d+) of the (\d+) extension requests in the extension register '
                             r'took effect\.', text):
            assert int(m[3]) == len(self.requests)
            return 'extensions_in_effect', self.quantified(m[1], int(m[2]), self.counts(lambda r: r['took']))
        if m := re.fullmatch(r'(Exactly|At least) (\d+) of the (\d+) notices with the reply channel Post had a reply '
                             r'received after the deadline\.', text):
            post = [r['Notice'] for r in self.register if r['Reply channel'] == 'Post']
            assert int(m[3]) == len(post)
            return 'late_replies_by_post', self.quantified(m[1], int(m[2]), self.counts(lambda r: r['late'], post))
        if m := re.fullmatch(r'Among the (standard|complex|urgent) notices, notice (N\d+) has the (latest|earliest) '
                             r'deadline, ' + date + r'\.', text):
            members, want = [r['Notice'] for r in self.register if r['Class'] == m[1]], day(m[4])
            pick = max if m[3] == 'latest' else min
            outcomes = []
            for snap in self.snapshots:
                top = pick(snap[n]['deadline'] for n in members)
                outcomes.append([n for n in members if snap[n]['deadline'] == top] == [m[2]] and top == want)
            return 'deadline_extreme', over_all(outcomes)
        if m := re.fullmatch(r'The extension requested for notice (N\d+) (took|did not take) effect\.', text):
            return 'extension_effect', over_all(s[m[1]]['took'] == (m[2] == 'took') for s in self.snapshots)
        if m := re.fullmatch(r'Notice (N\d+) was issued on ' + date + r'\.', text):
            return 'notice_lookup', 'supported' if self.by_id[m[1]]['Date issued'] == m[2] else 'refuted'
        return None, None


# ---------- Tests ----------

class CaseworkTests(unittest.TestCase):
    def test_keys_agree_with_a_second_derivation_from_the_rendered_documents(self):
        checked, total = [], 0
        kinds = collections.Counter()
        for seed in WIDE:
            built = case(seed)
            reading = reading_of(seed)
            # The number of claims counted in the report files themselves, not in the key.
            total += sum(built['files'][f'report-{v}.md'].count('<a id="C') for v in ('correct', 'mixed'))
            for row in built['key']['expectations']:
                kind, verdict = reading.verdict(row['claim_text'])
                where = f'{built["dossier"]} {row["variant"]} {row["claim_id"]}: {row["claim_text"]} ({row["rationale"]})'
                self.assertIsNotNone(kind, where)
                self.assertEqual(kind, row['claim_kind'], where)
                self.assertEqual(verdict, row['semantic_state'], where)
                kinds[kind] += 1
                checked.append((seed, row['variant'], row['claim_id']))
        self.assertEqual(len(checked), total)
        self.assertEqual(len(set(checked)), total)
        self.assertEqual(set(kinds), {'deadline_date', 'reply_timing', 'days_late', 'receipt_date', 'late_replies',
                                      'shifted_deadlines', 'extensions_in_effect', 'late_replies_by_post',
                                      'deadline_extreme', 'extension_effect', 'notice_lookup'})

    def test_correct_reports_hold_only_supported_claims_and_altered_reports_hold_typed_errors(self):
        for seed in SEEDS:
            built = case(seed)
            by_variant = {variant: [r for r in built['key']['expectations'] if r['variant'] == variant]
                          for variant in ('correct', 'mixed')}
            self.assertEqual([r['claim_id'] for r in by_variant['correct']], [r['claim_id'] for r in by_variant['mixed']])
            for row in by_variant['correct']:
                self.assertEqual((row['semantic_state'], row['error_type']), ('supported', None), built['dossier'])
            altered = [r for r in by_variant['mixed'] if r['semantic_state'] != 'supported']
            self.assertGreaterEqual(len(altered), 2, built['dossier'])
            for row in by_variant['mixed']:
                self.assertEqual(row['error_type'] is not None, row['semantic_state'] != 'supported', row)
                if row['error_type']:
                    self.assertIn(row['error_type'], KNOWN_ERRORS)
            for variant in ('correct', 'mixed'):
                ledger = run_blind.paragraphs(built['files'][f'report-{variant}.md'])
                anchors = run_blind.claim_anchors(ledger)
                self.assertEqual(sorted(anchors), sorted(r['claim_id'] for r in by_variant[variant]))
                for row in by_variant[variant]:
                    self.assertIn(row['claim_text'], ledger[anchors[row['claim_id']]])
                    for anchor in row['source_anchors']:
                        name, _, mark = anchor.partition('#')
                        self.assertIn(f'<a id="{mark}"></a>', built['files'][name], anchor)

    def test_the_same_seed_gives_the_same_case_and_another_seed_another(self):
        first, again, other = (generate.build_case('casework', seed, 3) for seed in (5, 5, 6))
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(again, sort_keys=True))
        self.assertNotEqual(first['files'], other['files'])

    def test_a_case_does_not_depend_on_the_hash_seed_of_the_interpreter(self):
        code = ('import json, sys; sys.path.insert(0, %r); import generate; '
                'print(json.dumps([generate.build_case("casework", s, 1) for s in (3, 11)], sort_keys=True))'
                % str(ROOT / 'evalsuite'))
        outputs = []
        for hash_seed in ('0', '4242'):
            run = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True,
                                 env=dict(os.environ, PYTHONHASHSEED=hash_seed))
            outputs.append(run.stdout.strip())
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0], json.dumps([case(3), case(11)], sort_keys=True))

    def test_neither_report_nor_sources_reveal_the_variant_or_the_errors(self):
        for seed in SEEDS:
            built = case(seed)
            for name, text in built['files'].items():
                for word in ('mixed', 'correct report', 'error_type', 'refuted', 'undetermined', 'supported'):
                    self.assertNotIn(word, text.lower() if word != 'error_type' else text, f'{built["dossier"]}/{name}')
            self.assertEqual(built['files']['report-correct.md'].count('**C'), built['files']['report-mixed.md'].count('**C'))

    def test_no_wording_is_only_ever_right_or_only_ever_wrong(self):
        """A wording with its citations that occurs only in altered claims, or only in unaltered ones, would give
        the verdict away without reading a source. Only entity values are normalised: ids, dates, numbers, class
        names and the verb of a reply channel."""
        def shape(text):
            text = re.sub(r'\d{4}-\d\d-\d\d', 'DATE', text)
            text = re.sub(r'\bN\d{3}\b', 'ID', text)
            text = re.sub(r'\b(standard|complex|urgent)\b', 'CLASS', text)
            text = re.sub(r'\b1 day\b', 'N days', text)
            return re.sub(r'\d+', 'N', text)
        seen = collections.defaultdict(set)
        for seed in WIDE:
            built = case(seed)
            ledgers = {variant: run_blind.paragraphs(built['files'][f'report-{variant}.md']) for variant in ('correct', 'mixed')}
            anchors = {variant: run_blind.claim_anchors(ledger) for variant, ledger in ledgers.items()}
            for row in built['key']['expectations']:
                self.assertNotRegex(row['claim_text'], r'(^| )-\d', 'a negative amount or count marks a slip')
                passage = ledgers[row['variant']][anchors[row['variant']][row['claim_id']]]
                cited = passage.split(row['claim_text'], 1)[1]
                # Which register is in force varies by dossier; the citations must not say which: both issues are cited.
                self.assertEqual(set(re.findall(r'register-[ab]', cited)), {'register-a', 'register-b'})
                self.assertIn('document-index D-02', cited)
                self.assertRegex(cited, r'^ Citations?: .+\.$')
                seen[(row['claim_kind'], shape(row['claim_text']), cited)].add(row['semantic_state'] == 'supported')
        one_sided = sorted(key[:2] for key, sides in seen.items() if sides != {True, False})
        self.assertEqual(one_sided, [])
        self.assertGreater(len(seen), 15)

    def test_both_reports_of_a_dossier_have_the_same_claim_kinds(self):
        """A wrong variant has the kind of the true claim it replaces, so the kinds of an altered report cannot tell it
        from the correct one."""
        for seed in WIDE:
            built = case(seed)
            kinds = {variant: collections.Counter(r['claim_kind'] for r in built['key']['expectations'] if r['variant'] == variant)
                     for variant in ('correct', 'mixed')}
            self.assertEqual(kinds['correct'], kinds['mixed'], built['dossier'])

    def test_the_two_registers_look_alike_and_only_the_document_index_says_which_is_in_force(self):
        which, later_in_force = collections.Counter(), collections.Counter()
        for seed in WIDE:
            sources, reading = sources_of(case(seed)), reading_of(seed)
            first, second = (sources[name] for name in ('register-a.md', 'register-b.md'))
            headings = lambda text: re.sub(r'issue of \d{4}-\d\d-\d\d', 'issue of DATE', '\n'.join(re.findall(r'^#.*$', text, re.M)))  # noqa: E731
            self.assertEqual(headings(first), headings(second))
            self.assertEqual(sections(first).keys(), sections(second).keys())
            for text in (first, second):
                for word in ('draft', 'supersed', 'replace', 'force', 'revision', 'adopted', 'earlier', 'later', 'settle'):
                    self.assertNotIn(word, text.lower(), f'seed {seed}: {word}')
            issues = {name: day(re.search(r'issue of (\d{4}-\d\d-\d\d)', sources[name])[1]) for name in ('register-a.md', 'register-b.md')}
            other = ({'register-a.md', 'register-b.md'} - {reading.register_file}).pop()
            reasons = [r['Contents and reason'] for r in rows(sections(sources['document-index.md'])['D-02']) if r['Document'] == other]
            later = issues[reading.register_file] > issues[other]
            self.assertIn('replaced by a later issue' if issues[other] < issues[reading.register_file] else
                          'a proposed revision that was not adopted', reasons[0])
            which[reading.register_file] += 1
            later_in_force[later] += 1
        self.assertEqual(set(which), {'register-a.md', 'register-b.md'})
        self.assertEqual(set(later_in_force), {True, False}, 'the register in force is not always the later one')
        self.assertTrue(all(count >= 15 for count in later_in_force.values()), later_in_force)

    def test_no_key_row_names_the_register_that_settles_nothing(self):
        for seed in WIDE:
            built, reading = case(seed), reading_of(seed)
            for row in built['key']['expectations']:
                anchors = row['source_anchors']
                self.assertFalse([a for a in anchors if reading.other_file in a], f'{built["dossier"]} {row["claim_id"]}')
                if any('register-' in a for a in anchors):
                    self.assertIn(f'sources/{reading.register_file}#R-01', anchors)
                    self.assertIn('sources/document-index.md#D-02', anchors)

    def test_the_two_registers_are_equally_consistent(self):
        """The register that settles nothing must not be recognisable as the less consistent one. Four properties, counted
        in rows for each register: issue dates out of order of the rows, an issue date on a weekend or a listed holiday,
        a reply dated on or before the issue date, and an issue date on or after the decision on an extension."""
        counts = {'in force': collections.Counter(), 'settles nothing': collections.Counter()}
        for seed in WIDE:
            reading = reading_of(seed)
            holidays = reading.holidays
            decisions = {r['Request']: r['Date of the decision'] for r in reading.requests.values()}
            for name, register in (('in force', reading.register), ('settles nothing', reading.other_register)):
                previous = None
                for row in register:
                    issued = day(row['Date issued'])
                    counts[name]['out of order'] += previous is not None and issued < previous
                    counts[name]['non-working issue date'] += issued.weekday() >= 5 or issued in holidays
                    counts[name]['reply on or before issue'] += (row['Reply date'] != 'Not recorded'
                                                                 and day(row['Reply date']) <= issued)
                    dated = decisions.get(row['Extension request'], '')
                    counts[name]['decision on or before issue'] += bool(re.fullmatch(r'\d{4}-\d\d-\d\d', dated)) and day(dated) <= issued
                    previous = issued
        self.assertEqual(counts['in force'], counts['settles nothing'])
        self.assertEqual(sum(counts['in force'].values()), 0, counts)

    def test_no_count_claim_states_exactly_zero(self):
        for seed in WIDE:
            for row in case(seed)['key']['expectations']:
                self.assertNotRegex(row['claim_text'], r'^Exactly 0 of ')

    def test_the_sources_are_returned_in_sorted_order(self):
        for seed in SEEDS:
            names = [name for name in case(seed)['files'] if name.startswith('sources/')]
            self.assertEqual(names, sorted(names))

    def test_every_rule_is_exercised(self):
        injected = collections.Counter()
        changed = collections.defaultdict(int)
        modes = {1: {1}, 2: {2}, 3: {3}, 4: {4}, 5: {5}, 6: {6}, 7: {7}}
        # Over sixty seeds, not twenty: the rarest slips are injected in a few percent of the dossiers.
        for seed in range(1, 61):
            injected.update(r['error_type'] for r in case(seed)['key']['expectations'] if r['error_type'])
        for seed in SEEDS:
            reading = reading_of(seed)
            world = next(reading.worlds())
            truth = reading.snapshot(world)
            for rule, off in modes.items():
                off = frozenset(off)
                other = reading.snapshot(world, off)
                if any((truth[n]['deadline'], truth[n]['late']) != (other[n]['deadline'], other[n]['late']) for n in truth):
                    changed[rule] += 1
            # a request that counts although it is refused, pending, void or for an urgent notice
            other = reading.snapshot(world, frozenset({'all_requests'}))
            if any(truth[n]['deadline'] != other[n]['deadline'] for n in truth):
                changed['all_requests'] += 1
        for rule, slips in RULE_SLIPS.items():
            for slip in slips:
                self.assertGreater(injected[slip], 0, f'{slip} (rule {rule}) was never injected as an error')
            self.assertGreater(changed[rule], 0, f'rule {rule}: no notice changes when it is switched off')
        for slip in DATA_SLIPS:
            self.assertGreater(injected[slip], 0, slip)
        self.assertGreater(changed['all_requests'], 0)

    def test_claims_about_single_notices_hinge_on_several_rules(self):
        """At least three claims per dossier concern a notice on which two or more of the interacting rules (the
        amendment, the shift, the extension, the postal rule, on time) change the answer when switched off."""
        interacting = (2, 4, 5, 6, 7)
        answer = {'deadline_date': lambda r: r['deadline'], 'reply_timing': lambda r: r['late'],
                  'days_late': lambda r: (r['late'], r['days'])}
        for seed in SEEDS:
            built, reading = case(seed), reading_of(seed)
            world = next(reading.worlds())
            hinged = 0
            for row in built['key']['expectations']:
                if row['variant'] != 'correct' or row['claim_kind'] not in answer:
                    continue
                if ' not earlier than ' in row['claim_text']:
                    continue
                nid = re.search(r'notice (N\d+)', row['claim_text'])[1]
                if nid in reading.posting or reading.by_id[nid]['Extension request'] in reading.decision_days:
                    continue
                truth = answer[row['claim_kind']](reading.option(reading.by_id[nid], world))
                count = 0
                for rule in interacting:
                    switches = [{rule}] + ([{'all_requests'}] if rule == 5 else [])
                    count += any(answer[row['claim_kind']](reading.option(reading.by_id[nid], world, frozenset(off))) != truth
                                 for off in switches)
                hinged += count >= 2
            self.assertGreaterEqual(hinged, 3, built['dossier'])

    def test_no_notice_is_the_subject_of_two_claims_in_one_report(self):
        """An altered report that changed one of two claims about the same notice would contradict itself, and the
        pair could be flagged without a source."""
        single = {'deadline_date', 'reply_timing', 'days_late', 'receipt_date', 'extension_effect', 'deadline_extreme',
                  'notice_lookup'}
        for seed in range(1, 61):
            built = case(seed)
            for variant in ('correct', 'mixed'):
                subjects = [re.search(r'[Nn]otice (N\d+)', r['claim_text'])[1] for r in built['key']['expectations']
                            if r['variant'] == variant and r['claim_kind'] in single]
                self.assertEqual(sorted(subjects), sorted(set(subjects)), f'{built["dossier"]} {variant}')

    def test_exact_dates_in_the_open_fact_claims_are_working_days_unless_a_slip_made_them(self):
        """A deadline or a receipt by post is a working day under the rules, so a stated date on a Saturday or Sunday
        would be refutable from the weekday alone. Only a variant named after a slip may state one."""
        checked = 0
        for seed in range(1, 61):
            built = case(seed)
            for row in built['key']['expectations']:
                exact = re.fullmatch(r'The (?:deadline of notice N\d+ is|reply to notice N\d+ was received on) '
                                     r'(\d{4}-\d\d-\d\d)\.', row['claim_text'])
                if exact and row['error_type'] in (None, 'wrong_value', 'asserts_unknown_as_fact', 'treats_unknown_as_settled'):
                    checked += 1
                    self.assertLess(day(exact[1]).weekday(), 5, f'{built["dossier"]} {row["claim_id"]}: {row["claim_text"]}')
        self.assertGreater(checked, 100)

    def test_altered_reports_hold_the_claims_the_sources_leave_open(self):
        """The family asks `build_case` for two altered claims that the sources leave open. Every altered report must
        hold at least as many undetermined claims as that, or as many as there are slots offering one."""
        distribution, short = collections.Counter(), []
        for seed in range(1, 61):
            # The family's own slots, taken from the very call that builds the case.
            seen = []
            real = generate.family_builder

            def spy(name, real=real, seen=seen):
                builder = real(name)
                return lambda rng: seen.append(builder(rng)) or seen[-1]
            with mock.patch.object(generate, 'family_builder', spy):
                built = generate.build_case('casework', seed, 1)
            self.assertEqual(built['files'], case(seed)['files'])
            family = seen[0]
            self.assertEqual(family['open_claims'], 2)
            offering = sum(any(generate.judge(m, family['base'], family['unknowns'])[0] == 'undetermined' for m in mutations)
                           for _, mutations in family['slots'])
            held = sum(r['semantic_state'] == 'undetermined' for r in case(seed)['key']['expectations'] if r['variant'] == 'mixed')
            self.assertGreaterEqual(held, min(2, offering), f'seed {seed}: {held} undetermined, {offering} slots offer one')
            distribution[min(offering, 3)] += 1
            short.append(offering < 2)
        self.assertLessEqual(sum(short), 0.1 * 60, f'dossiers by number of slots offering an open claim (3 means 3 or more): {sorted(distribution.items())}')

    def test_size_and_mix(self):
        undetermined_dossiers = settled_open_dossiers = settled_timing = 0
        all_states = set()
        for seed in SEEDS:
            started = time.time()
            built = generate.build_case('casework', seed, 1)
            self.assertLess(time.time() - started, 15.0, built['dossier'])   # about 0.1 s here; a bound that only a hang breaks
            sources = sources_of(built)
            self.assertEqual(set(sources), {'document-index.md', 'procedure.md', 'amendment.md', 'calendar.md', 'register-a.md',
                                            'register-b.md', 'extensions.md'})
            register = reading_of(seed).register
            self.assertTrue(40 <= len(register) <= 80, len(register))
            other = ({'register-a.md', 'register-b.md'} - {reading_of(seed).register_file}).pop()
            self.assertEqual(len(rows(sections(sources[other])['R-01'])), len(register))
            words = sum(len(text.split()) for text in sources.values())
            self.assertTrue(2500 <= words <= 7000, (built['dossier'], words))
            expectations = built['key']['expectations']
            per_report = len(expectations) // 2
            self.assertTrue(10 <= per_report <= 14, per_report)
            mixed = [r for r in expectations if r['variant'] == 'mixed']
            all_states |= {r['semantic_state'] for r in mixed}
            undetermined_dossiers += any(r['semantic_state'] == 'undetermined' for r in mixed)
            correct = [r for r in expectations if r['variant'] == 'correct']
            # A claim that depends on an open fact (more than one value across the possible worlds) and is still settled.
            open_settled = [r for r in correct if r['derivation'].get('feasible_values', 1) > 1]
            settled_open_dossiers += bool(open_settled)
            settled_timing += any(r['claim_kind'] == 'reply_timing' and r['derivation'].get('feasible_values', 1) > 1
                                  for r in correct)
            for row in mixed:
                if row['semantic_state'] != 'supported':
                    self.assertIn(row['semantic_state'], ('refuted', 'undetermined'))
        self.assertEqual(all_states, {'supported', 'refuted', 'undetermined'})
        count = len(SEEDS)
        self.assertGreaterEqual(undetermined_dossiers, 0.6 * count, 'altered reports need an undetermined claim in most dossiers')
        self.assertEqual(settled_open_dossiers, count, 'every dossier needs a settled claim that an open fact touches')
        self.assertGreaterEqual(settled_timing, 0.5 * count)

    def test_open_facts_stay_few_and_small(self):
        for seed in SEEDS:
            reading = reading_of(seed)
            self.assertTrue(2 <= len(reading.posting) + len(reading.decision_days) <= 4)
            self.assertLessEqual(len(reading.snapshots), 500)
            for values in itertools.chain(reading.posting.values(), reading.decision_days.values()):
                self.assertTrue(3 <= len(values) <= 4)

    def test_no_generation_error_for_sixty_seeds_and_several_indexes(self):
        for seed in range(1, 61):
            case(seed)
        for seed in (1, 2, 3):
            for index in (2, 3, 7):
                generate.build_case('casework', seed, index)


class CaseworkSuiteTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + '-', dir=TEST_RUN))

    def test_a_written_suite_loads_in_the_scorer_seals_and_stages(self):
        fixtures, keys = self.root / 'fixtures', self.root / 'keys'
        summary = generate.write_suite(fixtures, keys, 9, 4, ['casework'])
        self.assertEqual(len(summary['dossiers']), 4)
        self.assertEqual(sum(summary['expected_states'].values()), summary['claims'])
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
        for folder in fixtures.rglob('*'):
            self.assertFalse(folder.name.endswith('.json'), 'no answer material belongs in the fixtures')

    def test_key_wording_is_not_flagged_when_an_auditor_restates_the_claims(self):
        for seed in (2, 5, 8):
            built = case(seed)
            run = self.root / built['dossier']
            (run / 'case').mkdir(parents=True)
            (run / 'case/report.md').write_text(built['files']['report-mixed.md'], encoding='utf-8')
            notes = '\n'.join(f'{r["claim_id"]}: {r["claim_text"]} I recomputed this from the cited tables and it is '
                              f'{r["semantic_state"]}. Stated {r["derivation"].get("stated")}, found '
                              f'{r["derivation"].get("feasible_min")} to {r["derivation"].get("feasible_max")}.'
                              for r in built['key']['expectations'] if r['variant'] == 'mixed')
            (run / 'notes.md').write_text(notes, encoding='utf-8')
            self.assertEqual(blindness.scan_run(run, built['key']['expectations'])['verbatim_overlaps'], [], built['dossier'])

    def test_the_family_is_registered_as_a_harder_family(self):
        self.assertIn('casework', generate.family_names())
        self.assertNotIn('casework', generate.FAMILIES)


if __name__ == '__main__':
    unittest.main()
