"""Independent rendered-document checks for the earlier-review family.

Reading consumes source Markdown and claim wording only. It never imports the
family's facts, predicates or constants. Finite held-out guesser measurements
are evidence against the tested shortcuts, not guarantees of blindness. Because
an accuracy measure cannot see a leak that never pushes a rate past one half,
two further tests compare sentences and rates between the groups the family
must keep alike: the masked sentences of records and of memo entries (lure
against control against dossiers without a memo), and the rate of lures and of
wrong claims across every visible claim feature.
"""
import collections
import functools
import json
import math
from pathlib import Path
import random
import re
import statistics
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
import generate
generate.HARD_FAMILIES.setdefault('earlier', 'lure_earlier')
import blindness
import run_blind
import scorecard

KINDS = ('programme_variance', 'programme_total')
RECORD_FILES = ['record-{:02d}.md'.format(i) for i in range(1, 9)]


@functools.lru_cache(maxsize=2000)
def case(seed):
    return generate.build_case('earlier', seed, 1)


def sources_of(built):
    return {name[8:]: text for name, text in built['files'].items() if name.startswith('sources/')}


def sections(text):
    parts = re.split(r'<a id="([^"]+)"></a>\n', text)
    return dict(zip(parts[1::2], parts[2::2]))


def report_claims(text):
    return re.findall(r'\*\*C\d+\.\*\* (.*?) Citations?:', text)


def shape(text):
    """The claim with its programme name and its numbers masked."""
    return re.sub(r'\d+', 'N', re.sub(r'(?<=for the ).+? programme', 'PROGRAMME programme', text))


def record_numbers(sources):
    """The record file of each programme, read from the title line of each record."""
    return {re.fullmatch(r'# (.+) programme account', text.splitlines()[0])[1]: int(filename[7:9])
            for filename, text in sources.items() if filename.startswith('record-')}


def guesser_scores(train, test):
    """Single-feature lookup and threshold, fitted exclusively on training seeds."""
    fallback = sum(label for _, label in train) > len(train) / 2
    scores = {}
    for name in train[0][0]:
        groups = collections.defaultdict(collections.Counter)
        for features, label in train:
            groups[features[name]][label] += 1
        def predict(features):
            counts = groups.get(features[name])
            return counts[True] > counts[False] if counts else fallback
        scores[name + '/lookup'] = sum(predict(features) == label for features, label in test) / len(test)
        if not isinstance(train[0][0][name], (int, float)):
            continue
        total = sum(label for _, label in train)
        left_true = left_size = 0
        best = (max(total, len(train) - total), None, fallback, fallback)
        for value in sorted(groups):
            left_true += groups[value][True]
            left_size += sum(groups[value].values())
            right_true, right_size = total - left_true, len(train) - left_size
            correct = max(left_true, left_size - left_true) + max(right_true, right_size - right_true)
            if correct > best[0]:
                best = (correct, value, left_true > left_size / 2, right_true > right_size / 2)
        _, threshold, below, above = best
        scores[name + '/threshold'] = sum(
            (below if threshold is None or features[name] <= threshold else above) == label
            for features, label in test) / len(test)
    return scores


def grouped_threshold_score(train, test, group, feature):
    """A threshold on one numeric feature, fitted separately inside each value of `group`."""
    fallback = sum(label for _, label in train) > len(train) / 2
    rules = {}
    for value in {features[group] for features, _ in train}:
        part = sorted((features[feature], label) for features, label in train if features[group] == value)
        total = sum(label for _, label in part)
        best = (max(total, len(part) - total), None, total > len(part) / 2, total > len(part) / 2)
        left_true = 0
        for index, (_, label) in enumerate(part[:-1]):
            left_true += label
            if part[index][0] == part[index + 1][0]:
                continue
            left_size, right_true, right_size = index + 1, total - left_true, len(part) - index - 1
            correct = max(left_true, left_size - left_true) + max(right_true, right_size - right_true)
            if correct > best[0]:
                best = (correct, part[index][0], left_true > left_size / 2, right_true > right_size / 2)
        rules[value] = best[1:]
    def predict(features):
        if features[group] not in rules:
            return fallback
        threshold, below, above = rules[features[group]]
        return below if threshold is None or features[feature] <= threshold else above
    return sum(predict(features) == label for features, label in test) / len(test)


# Everything in a report that is not a claim text, written out for review: the
# title, the blank lines, the anchors, the numbering, the word "Citations" and the
# form of each citation. A programme's number fixes the record it cites.
REVIEWED_REPORT_TITLE = '# Programme disbursement review\n'
STRICT_CLAIM = re.compile(
    r'The Q3 disbursement variance for the (.+?) programme is \d+ euros\.'
    r'|The authorised schedule total for the (.+?) programme is \d+ euros\.')


def reviewed_report(report, numbers):
    """The report the reviewed wording prescribes for the claims this report holds."""
    blocks = [REVIEWED_REPORT_TITLE]
    for number, match in enumerate(STRICT_CLAIM.finditer(report), 1):
        record = numbers[match[1] or match[2]]
        blocks.append('<a id="C{0:02d}"></a>\n**C{0:02d}.** {1} Citations: procedure P-02, P-03; '
                      'record-{2:02d} R{2:02d}-S, R{2:02d}-P, R{2:02d}-A.\n'.format(number, match[0], record))
    return '\n'.join(blocks)


def masked(text):
    return re.sub(r'R\d\d-', 'R-N-', re.sub(r'record-\d\d', 'record-N', text))


def claim_form(text):
    match = re.fullmatch(r'The Q3 disbursement variance for the .+? programme is (\d+) euros\.', text)
    if match:
        return 'variance-zero' if int(match[1]) == 0 else 'variance'
    return 'total'


def features_of(built, report):
    texts = report_claims(report)
    polarities = tuple(claim_form(text) for text in texts)
    # A six-digit authorised total is unique to its dossier: as a token it would only
    # inflate the vocabulary, so it is counted as one token and its digits are kept in
    # the features below.
    tokens = collections.Counter(re.findall(r'[\w-]+|[^\w\s]', re.sub(r'\d{5,}', 'AMOUNT', report)))
    features = {'bytes': len(report.encode()), 'words': len(report.split()),
                'positive count': sum(form == 'variance-zero' for form in polarities),
                'ordered polarities': polarities,
                'programme polarities': tuple(sorted((Reading.parse(text)[0], claim_form(text)) for text in texts)),
                'full report': report,
                # Everything a reviewer sees of a report, also with the claims and the
                # per-claim record numbers masked, so that a cue in the fixed text
                # (which is the same in every report) is learnable.
                'title and preamble': report.split('<a id=', 1)[0],
                'citation lines': tuple(masked(line) for line in re.findall(r'Citations?: [^\n]+', report)),
                'masked report': masked(STRICT_CLAIM.sub('<CLAIM>', report)),
                'amount endings': tuple(int(amount) % 100 for amount in re.findall(r'\d{5,}', report)),
                'amount sum': sum(int(amount) for amount in re.findall(r'\d{5,}', report))}
    # Every report token, including title, preamble, anchor and citation tokens.
    features.update({'token:' + token: count for token, count in tokens.items()})
    sources = sources_of(built)
    metadata = {'filenames': tuple(sorted(sources)), 'file count': len(sources)}
    for name in ['procedure.md', 'review.md'] + RECORD_FILES:
        body = sources.get(name, '')
        for kind, value in [('bytes', len(body.encode())), ('words', len(body.split())),
                            ('stripped', len(body.strip())), ('normalized', len(' '.join(body.split())))]:
            metadata[name + '/' + kind] = value
    metadata['total bytes'] = sum(len(body.encode()) for body in sources.values())
    metadata['total words'] = sum(len(body.split()) for body in sources.values())
    return texts, features, metadata


PROCEDURE = """# Quarterly accounts procedure

<a id="P-01"></a>
## P-01 — Administration
Merehaven Community Trust, Finance Office. Issued 2034-10-01 by Mara Venn, accounts manager. This procedure defines the quarterly comparison used in the programme accounts pack. It accompanies the payment folios and schedule authorisations for the accounts committee. Amounts in this pack are expressed in whole euros.

<a id="P-02"></a>
## P-02 — Review memoranda
A review memo records its author's conclusions. A review memo is not a record of the facts it discusses. Those facts are established only by the payment records and signed schedule authorisations.

<a id="P-03"></a>
## P-03 — Quarterly comparison
For each programme, the schedule issue named as operative in its signed authorisation is the authorised schedule for the quarter. Q3 disbursements are the sum of the three bank-settled payments recorded for that programme. Disbursements reconcile to the authorised schedule if and only if that sum equals the total in the operative schedule issue. The disbursement variance is the absolute difference between that sum and the operative schedule total. A replaced schedule issue has no effect on that comparison. No payment, reversal or schedule authorisation outside the supplied records forms part of this quarter's comparison.
"""

# The one rule sentence that changed in this round, for the record: it read
# "An earlier schedule issue has no effect on that comparison."
CHANGED_RULE_SENTENCE = 'A replaced schedule issue has no effect on that comparison.'


class Reading:
    """Read the authoritative arithmetic and, separately, memo conclusions."""

    def __init__(self, sources):
        assert sources['procedure.md'] == PROCEDURE
        self.truth, self.total, self.original, self.initial_total = {}, {}, {}, {}
        self.memo, self.memo_total, self.entries, self.texts = {}, {}, {}, {}
        self.payments, self.issues = {}, {}
        for filename, text in sources.items():
            if not filename.startswith('record-'):
                continue
            name = re.fullmatch(r'# (.+) programme account', text.splitlines()[0])[1]
            parts = sections(text)
            original = next(body for anchor, body in parts.items() if anchor.endswith('-S'))
            payment = next(body for anchor, body in parts.items() if anchor.endswith('-P'))
            authority = next(body for anchor, body in parts.items() if anchor.endswith('-A'))
            initial = re.search(r'was issue ([AB]), with a quarter total of (\d+) euros', original)
            current = re.search(r'The authorised quarter total in issue ([AB]) is (\d+) euros', authority)
            operative = re.search(r'Issue ([AB]) is operative for Q3 and replaces issue ([AB])', authority)
            assert initial and current and operative
            assert current[1] == operative[1] and initial[1] == operative[2]
            assert initial[1] != current[1]
            payments = re.findall(r'The bank settled the (July|August|September) payment of (\d+) euros', payment)
            assert [month for month, _ in payments] == ['July', 'August', 'September']
            paid = sum(int(value) for _, value in payments)
            self.texts[name] = text
            self.payments[name] = paid
            self.total[name] = int(current[2])
            self.initial_total[name] = int(initial[2])
            self.issues[name] = {initial[1]: int(initial[2]), current[1]: int(current[2])}
            self.truth[name] = abs(paid - int(current[2]))
            self.original[name] = abs(paid - int(initial[2]))
        assert len(self.truth) == 8
        if 'review.md' in sources:
            memo = sources['review.md']
            assert memo.startswith('# Accounts review\n')
            assert ("The findings below state whether each programme's Q3 disbursements reconcile to its authorised schedule." in memo)
            entries = re.findall(r'(No variance found\.|One variance seen\.) Q3 disbursements for the (.+?) programme: '
                                 r'(\d+) euros against (\d+) euros in schedule issue ([AB])\.', memo)
            assert len(entries) == 8
            for finding, name, paid, scheduled, issue in entries:
                assert int(paid) == self.payments[name]
                assert int(scheduled) == self.issues[name][issue]
                assert (finding == 'No variance found.') == (int(paid) == int(scheduled))
                self.memo[name] = abs(int(paid) - int(scheduled))
                self.memo_total[name] = int(scheduled)
            for anchor, body in sections(memo).items():
                if anchor != 'V-01':
                    self.entries[re.search(r'for the (.+?) programme:', body)[1]] = body

    @staticmethod
    def parse(text):
        match = re.fullmatch(r'The Q3 disbursement variance for the (.+) programme is (\d+) euros\.', text)
        if match:
            return match[1], 'variance', int(match[2])
        match = re.fullmatch(r'The authorised schedule total for the (.+) programme is (\d+) euros\.', text)
        assert match, text
        return match[1], 'total', int(match[2])

    def misjudged(self, name):
        return name in self.memo_total and self.memo_total[name] != self.total[name]

    def original_value(self, kind, name):
        return self.original[name] if kind == 'variance' else self.initial_total[name]

    def verdict(self, text):
        name, kind, value = self.parse(text)
        return 'supported' if (self.truth if kind == 'variance' else self.total)[name] == value else 'refuted'

    def echo(self, text):
        name, kind, value = self.parse(text)
        if name not in self.memo:
            believed_value = (self.truth if kind == 'variance' else self.total)[name]
        else:
            believed_value = (self.memo if kind == 'variance' else self.memo_total)[name]
        believed = 'supported' if believed_value == value else 'refuted'
        return believed if believed != self.verdict(text) else None


# ---------- groups that must stay alike ----------

def mask_for_comparison(text, names):
    """Names, numbers and dates masked, as an independent reviewer would mask them."""
    for name in sorted(names, key=len, reverse=True):
        text = text.replace(name, 'PROGRAMME')
    return re.sub(r'\d+', 'N', re.sub(r'\d{4}-\d\d-\d\d', 'DATE', text))


def profile(text, names):
    """The masked sentences, the line count and the byte length of one document or entry."""
    sentences = []
    for line in text.split('\n'):
        if line.strip():
            sentences.extend(re.split(r'(?<=\.)\s+', mask_for_comparison(line.strip(), names)))
    return {'sentences': collections.Counter(sentences), 'lines': len(text.split('\n')),
            'count': len(sentences), 'bytes': len(text.encode())}


def compare_groups(groups, label, limit=4.5):
    """Fail where a masked sentence occurs in one group and not another, where a line or
    sentence count differs, or where a sentence rate or the mean length differs by more
    than `limit` standard errors. Return the largest standardised difference."""
    largest = 0.0
    sizes = {name: len(items) for name, items in groups.items()}
    assert min(sizes.values()) >= 300, (label, sizes)
    for what in ('lines', 'count'):
        observed = {name: set(item[what] for item in items) for name, items in groups.items()}
        assert len({frozenset(values) for values in observed.values()}) == 1, (label, what, observed)
    presence = {name: collections.Counter(s for item in items for s in item['sentences'])
                for name, items in groups.items()}
    every = set().union(*[set(counter) for counter in presence.values()])
    for sentence in sorted(every):
        missing = [name for name in groups if presence[name][sentence] == 0]
        assert not missing, (label, 'sentence absent from group', missing, sentence)
    names = sorted(groups)
    for first in range(len(names)):
        for second in range(first + 1, len(names)):
            a, b = names[first], names[second]
            for sentence in sorted(every):
                p = (presence[a][sentence] + presence[b][sentence]) / (sizes[a] + sizes[b])
                if 0 < p < 1:
                    spread = math.sqrt(p * (1 - p) * (1 / sizes[a] + 1 / sizes[b]))
                    z = abs(presence[a][sentence] / sizes[a] - presence[b][sentence] / sizes[b]) / spread
                    largest = max(largest, z)
                    assert z <= limit, (label, a, b, sentence, z)
            lengths = {name: [item['bytes'] for item in groups[name]] for name in (a, b)}
            spread = math.sqrt(sum(statistics.variance(values) / len(values) for values in lengths.values()))
            if spread:
                z = abs(statistics.mean(lengths[a]) - statistics.mean(lengths[b])) / spread
                largest = max(largest, z)
                assert z <= limit, (label, a, b, 'mean length', z)
    return largest


def standardised_rate_differences(reports):
    """Compare, for each value of each visible claim feature, the number of flagged claims
    with the number expected when the flagged claims are a random subset of the report.
    `reports` holds (flags, features) for each report, features being lists in claim order.
    The expected count and variance are the hypergeometric ones, summed over reports, so
    that a dossier holding several flagged claims and a dossier holding none are both
    handled and the two reports of one dossier are never pooled."""
    sums = {}
    for flags, features in reports:
        size, flagged = len(flags), sum(flags)
        if flagged in (0, size):
            continue
        for feature, values in features.items():
            for value, count in collections.Counter(values).items():
                entry = sums.setdefault((feature, value), [0, 0.0, 0.0])
                entry[0] += sum(1 for flag, other in zip(flags, values) if flag and other == value)
                entry[1] += flagged * count / size
                entry[2] += flagged * (count / size) * (1 - count / size) * (size - flagged) / (size - 1)
    return {key: (seen - expected) / math.sqrt(variance)
            for key, (seen, expected, variance) in sums.items() if variance > 0}


def claim_features(built, variant):
    """The visible features of each claim of one report, and the hidden flags to compare them with."""
    rows = [row for row in built['key']['expectations'] if row['variant'] == variant]
    parsed = [Reading.parse(row['claim_text']) for row in rows]
    numbers = record_numbers(sources_of(built))
    features = {'programme': [name for name, _, _ in parsed],
                # The citation shows the record number, so it is a visible feature of its own.
                'record': [numbers[name] for name, _, _ in parsed],
                'position': list(range(len(rows))),
                'kind': [kind for _, kind, _ in parsed],
                'stated value': [value if kind == 'variance' else 'total' for _, kind, value in parsed],
                'zero': [('zero' if value == 0 else 'non-zero') if kind == 'variance' else 'total'
                         for _, kind, value in parsed]}
    flags = {'lure': [row['echo_state'] is not None for row in rows],
             'wrong': [row['semantic_state'] != 'supported' for row in rows]}
    return features, flags


class EarlierTests(unittest.TestCase):
    def test_pinned_whole_procedure_and_missing_rule_rejected(self):
        self.assertEqual(sources_of(case(1))['procedure.md'], PROCEDURE)
        changed = dict(sources_of(case(1)))
        changed['procedure.md'] = changed['procedure.md'].replace('only', 'mainly', 1)
        with self.assertRaises(AssertionError):
            Reading(changed)

    def test_reworded_rule_sentence_cannot_be_read_as_the_earlier_letter(self):
        procedure = sources_of(case(1))['procedure.md']
        self.assertIn(CHANGED_RULE_SENTENCE, procedure)
        self.assertNotIn('earlier', procedure.lower())

    def test_fixed_full_report_title_and_preamble(self):
        for seed in range(1, 301):
            for variant in ('correct', 'mixed'):
                report = case(seed)['files']['report-' + variant + '.md']
                self.assertEqual(report.split('<a id=', 1)[0], '# Programme disbursement review\n\n')

    def test_whole_visible_report_is_the_reviewed_title_numbering_and_citation_form(self):
        for seed in range(1, 301):
            numbers = record_numbers(sources_of(case(seed)))
            for variant in ('correct', 'mixed'):
                report = case(seed)['files']['report-' + variant + '.md']
                self.assertEqual(report, reviewed_report(report, numbers), (seed, variant))
                self.assertEqual(len(STRICT_CLAIM.findall(report)), 8, (seed, variant))

    def test_report_frame_rejects_title_numbering_citation_and_preamble_changes(self):
        numbers = record_numbers(sources_of(case(1)))
        report = reviewed_report(case(1)['files']['report-correct.md'], numbers)
        title = '# Programme disbursement review'
        first = re.search(r'Citations: [^\n]+', report)[0]
        number = re.search(r'record-(\d\d)', first)[1]
        changes = {
            'letter appended to the title': report.replace(title, title + 's', 1),
            'title with the same length': report.replace(title, title[:-1] + 'x', 1),
            'preamble line': report.replace('\n\n', '\n\nInternal note.\n\n', 1),
            'citation heading': report.replace('Citations:', 'Citation:', 1),
            'procedure anchors in the other order': report.replace('procedure P-02, P-03', 'procedure P-03, P-02', 1),
            'procedure anchors separated by a semicolon': report.replace('P-02, P-03', 'P-02; P-03', 1),
            'record anchors in the other order': report.replace('R{0}-S, R{0}-P'.format(number), 'R{0}-P, R{0}-S'.format(number), 1),
            'record anchors listed without the signed schedule': report.replace(', R{0}-A'.format(number), '', 1),
            'record of another programme': report.replace('record-' + number, 'record-99', 1),
            'review memo cited': report.replace(first, first[:-1] + '; review V-01.', 1),
            'claim numbering': report.replace('**C02.**', '**C2.**', 1),
            'text glued to a claim': report.replace(' Citations:', ' Noted. Citations:', 1),
        }
        for label, changed in changes.items():
            with self.subTest(label):
                self.assertNotEqual(changed, report)
                self.assertNotEqual(reviewed_report(changed, numbers), changed)

    def test_echo_and_lure_reader_names(self):
        echoes = 0
        for seed in range(1, 301):
            for row in case(seed)['key']['expectations']:
                if row['echo_state'] is not None:
                    echoes += 1
                    self.assertEqual(row.get('echo_reader'), 'follows_earlier_review', (seed, row['claim_id']))
                else:
                    self.assertNotIn('echo_reader', row)
                self.assertLessEqual(set(row.get('lure_states', {})), {'follows_earlier_review'}, (seed, row['claim_id']))
        self.assertGreater(echoes, 0)

    def test_rendered_numeric_change_moves_truth_and_echo(self):
        # Change an operative total that no memo repeats. Reading must derive a
        # new truth and new disagreement without consulting a key or world.
        for seed in range(1, 301):
            sources = sources_of(case(seed))
            reading = Reading(sources)
            choices = [name for name in reading.truth if name in reading.memo and
                       reading.memo[name] != reading.truth[name]]
            if choices:
                name = choices[0]
                filename = 'record-{:02d}.md'.format(record_numbers(sources)[name])
                replacement = reading.payments[name] + reading.memo[name]
                sources[filename], count = re.subn(
                    r'(The authorised quarter total in issue [AB] is )\d+',
                    lambda match: match[1] + str(replacement), sources[filename])
                self.assertEqual(count, 1)
                after = Reading(sources)
                claim = 'The Q3 disbursement variance for the ' + name + ' programme is ' + str(reading.truth[name]) + ' euros.'
                self.assertNotEqual(reading.verdict(claim), after.verdict(claim))
                self.assertIsNotNone(reading.echo(claim))
                self.assertIsNone(after.echo(claim))
                # The authorised total moves the verdict of a total claim in the same way.
                total_claim = 'The authorised schedule total for the ' + name + ' programme is ' + str(reading.total[name]) + ' euros.'
                self.assertNotEqual(reading.verdict(total_claim), after.verdict(total_claim))
                break
        else:
            self.fail('No conflicting memo found')

    def test_rendered_memo_arithmetic_changes_echo_without_changing_truth(self):
        for seed in range(1, 301):
            sources = sources_of(case(seed))
            before = Reading(sources)
            if not before.memo:
                continue
            name = sorted(before.truth)[0]
            pattern = (r'(No variance found\.|One variance seen\.) Q3 disbursements for the ' +
                       re.escape(name) + r' programme: (\d+) euros against (\d+) euros in schedule issue ([AB])\.')
            entry = re.search(pattern, sources['review.md'])
            self.assertIsNotNone(entry)
            other = 'B' if entry[4] == 'A' else 'A'
            amount = before.issues[name][other]
            finding = 'No variance found.' if before.payments[name] == amount else 'One variance seen.'
            replacement = (finding + ' Q3 disbursements for the ' + name + ' programme: ' + entry[2] +
                           ' euros against ' + str(amount) + ' euros in schedule issue ' + other + '.')
            sources['review.md'], count = re.subn(pattern, replacement, sources['review.md'])
            self.assertEqual(count, 1)
            after = Reading(sources)
            for text in ('The Q3 disbursement variance for the ' + name + ' programme is ' + str(before.truth[name]) + ' euros.',
                         'The authorised schedule total for the ' + name + ' programme is ' + str(before.total[name]) + ' euros.'):
                self.assertEqual(before.verdict(text), after.verdict(text))
                self.assertNotEqual(before.echo(text), after.echo(text))
            break
        else:
            self.fail('No memo found')

    def test_audit_helper_paragraphs_agree(self):
        sys.path.insert(0, str(ROOT / 'skills/evidence-to-proof-review/scripts'))
        import audit
        for seed in range(1, 61):
            for variant in ('correct', 'mixed'):
                report = case(seed)['files']['report-' + variant + '.md']
                self.assertEqual(run_blind.paragraphs(report), audit.paragraphs(report))

    def test_generation_does_not_change_global_random_state(self):
        state = random.getstate()
        generate.build_case('earlier', 38, 9)
        self.assertEqual(state, random.getstate())

    def test_source_dates_do_not_follow_memo_date(self):
        for seed in range(1, 301):
            sources = sources_of(case(seed))
            if 'review.md' in sources:
                date = re.search(r'\d{4}-\d\d-\d\d', sources['review.md'])[0]
                self.assertGreaterEqual(date, max(day for name, text in sources.items() if name != 'review.md'
                                                  for day in re.findall(r'\d{4}-\d\d-\d\d', text)))

    def test_keys_and_echo_from_rendered_sources(self):
        checked = 0
        for seed in range(1, 61):
            built = case(seed)
            reading = Reading(sources_of(built))
            for row in built['key']['expectations']:
                self.assertEqual(reading.verdict(row['claim_text']), row['semantic_state'], (seed, row))
                self.assertEqual(reading.echo(row['claim_text']), row['echo_state'], (seed, row))
                if row['echo_state']:
                    self.assertEqual(row['echo_reader'], 'follows_earlier_review')
                checked += 1
        self.assertEqual(checked, 960)

    def test_every_raw_slot_variant_with_builder_spy(self):
        for seed in range(1, 61):
            captured = []
            real = generate.family_builder
            def spy(name):
                builder = real(name)
                def build(rng):
                    result = builder(rng)
                    captured.append(result)
                    return result
                return build
            with mock.patch.object(generate, 'family_builder', spy):
                built = generate.build_case('earlier', seed, 1)
            self.assertEqual(built, case(seed))
            self.assertEqual(len(captured), 1)
            raw = captured[0]
            reading = Reading(raw['sources'])
            self.assertEqual(raw['unknowns'], {})
            conflicts = any(reading.misjudged(name) for name in reading.memo)
            self.assertEqual(raw.get('told') is not None, conflicts)
            for true, mutations in raw['slots']:
                self.assertTrue(mutations)
                for item in [true] + mutations:
                    actual = generate.judge(item, raw['base'], raw['unknowns'])[0]
                    self.assertEqual(actual, reading.verdict(item['text']))
                    echo = None
                    if raw.get('told'):
                        believed = generate.judge(item, raw['told']['base'], raw['told']['unknowns'])[0]
                        echo = believed if believed != actual else None
                    self.assertEqual(echo, reading.echo(item['text']))
                    if item['error']:
                        self.assertEqual(item['kind'], true['kind'])
                        self.assertEqual(item['cites'], true['cites'])
                        name, kind, value = Reading.parse(item['text'])
                        self.assertEqual((name, kind), Reading.parse(true['text'])[:2])
                        self.assertEqual(item['kind'], 'programme_' + kind)
                        self.assertNotEqual(value, Reading.parse(true['text'])[2])
                        self.assertEqual(value, reading.original_value(kind, name))
                        self.assertEqual(item['error'], 'follows_earlier_review' if reading.misjudged(name)
                                         else 'uses_superseded_schedule')

    def test_same_seed_deterministic_and_300_seeds_build(self):
        for seed in range(1, 301):
            self.assertEqual(case(seed), generate.build_case('earlier', seed, 1))
            self.assertEqual(len(case(seed)['key']['expectations']), 16)
        self.assertNotEqual(case(1)['files'], case(2)['files'])

    def test_correct_reports_supported_and_mixed_reports_have_typed_errors(self):
        for seed in range(1, 301):
            for variant in ('correct', 'mixed'):
                rows = [row for row in case(seed)['key']['expectations'] if row['variant'] == variant]
                if variant == 'mixed':
                    self.assertGreaterEqual(sum(row['error_type'] is not None for row in rows), 2)
                for row in rows:
                    self.assertEqual(row['semantic_state'] != 'supported', row['error_type'] is not None)
                    if variant == 'correct':
                        self.assertEqual(row['semantic_state'], 'supported')
                    self.assertIn(row['error_type'], (None, 'follows_earlier_review', 'uses_superseded_schedule'))

    def test_neutral_names_and_same_claim_kinds_and_programmes(self):
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            for variant in ('correct', 'mixed'):
                rows = [row for row in built['key']['expectations'] if row['variant'] == variant]
                self.assertEqual(len({Reading.parse(row['claim_text'])[0] for row in rows}), 8)
                self.assertEqual({Reading.parse(row['claim_text'])[0] for row in rows}, set(reading.truth))
                self.assertEqual(collections.Counter(row['claim_kind'] for row in rows),
                                 collections.Counter({'programme_variance': 4, 'programme_total': 4}))
            for filename, body in built['files'].items():
                self.assertNotRegex(body.lower(), r'\blure\b|\bmixed\b|error_type|\brefuted\b|\bundetermined\b|correct report')
                if filename.startswith('sources/'):
                    self.assertRegex(filename, r'^sources/(procedure|review|record-0[1-8])\.md$')

    def test_both_reports_hold_the_same_kind_and_programme_in_every_slot(self):
        for seed in range(1, 301):
            rows = case(seed)['key']['expectations']
            correct = [row for row in rows if row['variant'] == 'correct']
            mixed = [row for row in rows if row['variant'] == 'mixed']
            self.assertEqual([row['claim_id'] for row in correct], [row['claim_id'] for row in mixed])
            for first, second in zip(correct, mixed):
                self.assertEqual(first['claim_kind'], second['claim_kind'], (seed, first['claim_id']))
                self.assertEqual(Reading.parse(first['claim_text'])[:2], Reading.parse(second['claim_text'])[:2],
                                 (seed, first['claim_id']))
                self.assertEqual(first['source_anchors'], second['source_anchors'])

    def test_every_programme_and_numeric_wording_sometimes_right_and_wrong(self):
        seen = collections.defaultdict(set)
        names = set()
        for seed in range(1, 301):
            names |= set(Reading(sources_of(case(seed))).truth)
            for row in case(seed)['key']['expectations']:
                name, kind, value = Reading.parse(row['claim_text'])
                if kind == 'variance':
                    self.assertIn(value, (0, 1200, 3600, 7500))
                seen[row['claim_text']].add(row['semantic_state'])
        self.assertEqual(len(names), 12)
        # Every wording shape with the numbers left in: the four variances and the total.
        shapes = collections.defaultdict(set)
        for text, states in seen.items():
            name, kind, value = Reading.parse(text)
            shapes[(kind, value if kind == 'variance' else 'total')] |= states
        self.assertEqual(set(shapes), {('variance', 0), ('variance', 1200), ('variance', 3600),
                                       ('variance', 7500), ('total', 'total')})
        self.assertTrue(all(states == {'supported', 'refuted'} for states in shapes.values()), shapes)
        # And every claim text of a programme.
        plain = [text for text, states in seen.items() if Reading.parse(text)[1] == 'variance']
        self.assertGreaterEqual(len(plain), 12 * 4)
        self.assertTrue(all(seen[text] == {'supported', 'refuted'} for text in plain),
                        {text: states for text, states in seen.items() if states != {'supported', 'refuted'} and text in plain})

    def test_no_none_text_and_report_paragraphs_contain_exact_claims(self):
        for seed in range(1, 301):
            built = case(seed)
            for variant in ('correct', 'mixed'):
                report = built['files']['report-' + variant + '.md']
                self.assertNotIn('None', report)
                ledger = run_blind.paragraphs(report)
                self.assertEqual(len(run_blind.claim_anchors(ledger)), 8)
                for row in built['key']['expectations']:
                    if row['variant'] == variant:
                        self.assertIn(row['claim_text'], ledger[row['claim_id']])

    def test_citations_never_include_memo_and_reference_existing_anchors(self):
        for seed in range(1, 301):
            built = case(seed)
            for row in built['key']['expectations']:
                ledger = run_blind.paragraphs(built['files']['report-' + row['variant'] + '.md'])
                self.assertNotIn('review', ledger[row['claim_id']].split('Citations:')[1])
                self.assertEqual(len(row['source_anchors']), 5)
                for source in row['source_anchors']:
                    filename, anchor = source.split('#')
                    self.assertIn('<a id="' + anchor + '"></a>', built['files'][filename])

    def test_sources_exceed_fifteen_hundred_words(self):
        for seed in range(1, 301):
            self.assertGreaterEqual(sum(len(text.split()) for text in sources_of(case(seed)).values()), 1500)

    def test_rule_sentences_are_reproduced_verbatim_in_module_docstring(self):
        # Read the module's docstring only; Reading still uses no family constants.
        module = sys.modules[generate.family_builder('earlier').__module__]
        rules = sections(PROCEDURE)
        for anchor in ('P-02', 'P-03'):
            body = rules[anchor].split('\n', 1)[1].strip()
            for sentence in re.split(r'(?<=\.) ', body):
                self.assertIn(sentence, module.__doc__)

    def test_records_give_the_same_ordinary_reason_for_a_right_and_a_wrong_memo_and_never_mention_a_memo(self):
        # Both schedule copies reached the review office, whatever the memo did; the
        # records are the same kind of text with a memo, with a wrong one and with none.
        for seed in range(1, 301):
            sources = sources_of(case(seed))
            for filename, text in sources.items():
                if not filename.startswith('record-'):
                    continue
                parts = sections(text)
                self.assertIn('accounts review office', next(b for a, b in parts.items() if a.endswith('-S')))
                self.assertIn('accounts review office', next(b for a, b in parts.items() if a.endswith('-A')))
                self.assertNotRegex(text.lower(), r'memo|working paper|finding|review tray|conclusion')
            if 'review.md' not in sources:
                for filename, text in sources.items():
                    if filename != 'procedure.md':
                        self.assertNotRegex(text.lower(), r'memo|working paper|finding|review tray|conclusion', filename)

    def test_records_vary_in_dates_names_and_ordinary_sentences(self):
        days = collections.defaultdict(set)
        names, staff = set(), set()
        shared = []
        for seed in range(1, 301):
            sources = sources_of(case(seed))
            reading = Reading(sources)
            names |= set(reading.truth)
            for text in reading.texts.values():
                for month, day in re.findall(r'The bank settled the (July|August|September) payment of \d+ euros on 2034-\d\d-(\d\d)', text):
                    days[month].add(day)
                staff |= set(re.findall(r'by ([A-Z][a-z]+ [A-Z][a-z]+)(?:, programme accountant| in Treasury)', text))
            if seed <= 100:
                sets = [set(profile(text, reading.truth)['sentences']) for text in reading.texts.values()]
                shared += [len(a & b) / len(a | b) for i, a in enumerate(sets) for b in sets[i + 1:]]
        print('Earlier realism: programme names={} payment days={} staff names={} mean shared sentences between two records={:.3f}'.format(
            len(names), {month: len(values) for month, values in days.items()}, len(staff), statistics.mean(shared)))
        self.assertEqual(len(names), 12)
        self.assertTrue(all(len(values) >= 8 for values in days.values()), days)
        self.assertGreaterEqual(len(staff), 6)
        self.assertLess(statistics.mean(shared), 0.5)

    def test_masked_record_sentences_alike_for_misjudged_rightly_judged_and_memo_less(self):
        groups = {'misjudged': [], 'rightly judged': [], 'no memo': []}
        for seed in range(1, 1001):
            reading = Reading(sources_of(case(seed)))
            for name, text in reading.texts.items():
                group = 'no memo' if not reading.memo else 'misjudged' if reading.misjudged(name) else 'rightly judged'
                groups[group].append(profile(text, reading.truth))
        largest = compare_groups(groups, 'records')
        print('Earlier records: items={} largest standardised difference={:.2f}'.format(
            {name: len(items) for name, items in groups.items()}, largest))

    def test_masked_memo_entry_sentences_alike_for_wrong_and_right_points(self):
        groups = {'wrong': [], 'right': []}
        for seed in range(1, 1001):
            reading = Reading(sources_of(case(seed)))
            for name, body in reading.entries.items():
                groups['wrong' if reading.misjudged(name) else 'right'].append(profile(body, reading.truth))
        largest = compare_groups(groups, 'memo entries')
        print('Earlier memo entries: items={} largest standardised difference={:.2f}'.format(
            {name: len(items) for name, items in groups.items()}, largest))

    def test_group_comparison_catches_a_changed_sentence_a_missing_sentence_and_a_length_shift(self):
        groups = {'lure': [], 'control': []}
        for seed in range(1, 601):
            reading = Reading(sources_of(case(seed)))
            for name, text in reading.texts.items():
                if reading.memo:
                    groups['lure' if reading.misjudged(name) else 'control'].append(profile(text, reading.truth))
        self.assertGreaterEqual(min(len(items) for items in groups.values()), 300)
        compare_groups(groups, 'unchanged')
        def altered(change):
            result = {}
            for name, items in groups.items():
                result[name] = [change(item) if name == 'lure' else item for item in items]
            return result
        def swap_word(item):
            sentences = collections.Counter()
            for sentence, count in item['sentences'].items():
                sentences[sentence.replace('Treasury returned', 'Treasury retained')] += count
            return dict(item, sentences=sentences)
        def six_bytes(item):
            return dict(item, bytes=item['bytes'] + 6)
        def extra_line(item):
            return dict(item, lines=item['lines'] + 1)
        def extra_sentence(item):
            return dict(item, count=item['count'] + 1)
        for label, change in (('same-length word change', swap_word), ('six-byte shift', six_bytes),
                              ('extra blank line', extra_line), ('extra sentence', extra_sentence)):
            with self.subTest(label):
                with self.assertRaises(AssertionError):
                    compare_groups(altered(change), label)

    def test_lure_and_wrong_rates_do_not_depend_on_any_visible_claim_feature(self):
        collected = {('lure', 'correct'): [], ('lure', 'mixed'): [], ('wrong', 'mixed'): [], ('lure given wrong', 'mixed'): []}
        for seed in range(1, 1001):
            built = case(seed)
            for variant in ('correct', 'mixed'):
                features, flags = claim_features(built, variant)
                collected[('lure', variant)].append((flags['lure'], features))
                if variant == 'mixed':
                    collected[('wrong', variant)].append((flags['wrong'], features))
                    collected[('lure given wrong', variant)].append((flags['lure'], dict(features, wrong=flags['wrong'])))
        for key, reports in collected.items():
            scores = standardised_rate_differences(reports)
            worst = max(scores, key=lambda item: abs(scores[item]))
            print('Earlier rates {} in {} reports: values compared={} largest |z|={:.2f} at {}'.format(
                key[0], key[1], len(scores), abs(scores[worst]), worst))
            self.assertGreater(len(scores), 25)
            self.assertLess(abs(scores[worst]), 4.5, (key, worst))

    def test_rate_comparison_catches_a_partial_leak_and_stays_quiet_on_a_random_subset(self):
        reports = []
        for seed in range(1, 301):
            features, flags = claim_features(case(seed), 'correct')
            reports.append((flags['lure'], features))
        self.assertLess(max(abs(z) for z in standardised_rate_differences(reports).values()), 4.5)
        # A leak that makes a lure likelier on zero-variance claims, yet never pushes the
        # chance of a lure past one half, passes any accuracy test. Plant one.
        planted, rng = [], random.Random(5)
        for flags, features in reports:
            count = sum(flags)
            order = sorted(range(8), key=lambda i: (features['zero'][i] != 'zero', rng.random()))
            planted.append(([i in order[:count] for i in range(8)], features))
        scores = standardised_rate_differences(planted)
        self.assertGreater(abs(scores[('zero', 'zero')]), 6)
        # And a random subset of the same size shows nothing.
        shuffled = []
        for flags, features in reports:
            order = list(range(8))
            rng.shuffle(order)
            shuffled.append(([i in order[:sum(flags)] for i in range(8)], features))
        self.assertLess(max(abs(z) for z in standardised_rate_differences(shuffled).values()), 4.5)

    def test_matched_controls_and_frequencies(self):
        directions = collections.Counter()
        kinds = collections.Counter()
        present = conflict = no_lure = 0
        counts = collections.Counter()
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            present += bool(reading.memo)
            disagreement = sum(reading.misjudged(name) for name in reading.memo)
            conflict += disagreement > 0
            counts[disagreement] += 1
            no_lure += not any(row['echo_state'] for row in built['key']['expectations'])
            for variant in ('correct', 'mixed'):
                rows = [row for row in built['key']['expectations'] if row['variant'] == variant]
                self.assertLessEqual(sum(row['echo_state'] is not None for row in rows), 2)
                for row in rows:
                    if row['echo_state']:
                        self.assertTrue(any(other['echo_state'] is None and other['claim_kind'] == row['claim_kind']
                                            and shape(other['claim_text']) == shape(row['claim_text']) for other in rows), (seed, variant, row))
                        directions[row['semantic_state'], row['echo_state']] += 1
                        kinds[row['claim_kind'], row['semantic_state']] += 1
        print('Earlier frequency: memo={}/300 conflicts={}/{} no-lure={}/300 counts={} lures by kind and key={}'.format(
            present, conflict, present, no_lure, dict(counts), dict(kinds)))
        self.assertTrue(0.78 <= present / 300 <= 0.92)
        self.assertTrue(0.72 <= conflict / present <= 0.88)
        self.assertTrue(0.25 <= no_lure / 300 <= 0.37)
        self.assertEqual(set(directions), {('supported', 'refuted'), ('refuted', 'supported')})
        self.assertEqual({key for key, _ in kinds}, set(KINDS))
        self.assertEqual(set(kinds), {(kind, state) for kind in KINDS for state in ('supported', 'refuted')})

    def test_r9_three_hundred_seeds_twelve_dossiers(self):
        counts, directions, definite = [], collections.Counter(), 0
        for seed in range(1, 301):
            count = 0
            for index in range(1, 13):
                built = generate.build_case('earlier', seed, index)
                for row in built['key']['expectations']:
                    if row['echo_state']:
                        count += 1
                        directions[row['semantic_state'], row['echo_state']] += 1
            counts.append(count)
        measured = min(counts), statistics.median(counts), max(counts)
        print('Earlier R9 min/median/max={} directions={} total={} undetermined={}'.format(
            measured, dict(directions), sum(counts), sum(n for (_, echo), n in directions.items() if echo == 'undetermined')))
        # A campaign of twelve dossiers must reliably hold lures: the minimum is far above zero.
        self.assertGreaterEqual(measured[0], 10)
        self.assertGreaterEqual(measured[1], 15)
        self.assertEqual(measured, (14, 32.0, 48))
        self.assertEqual(dict(directions), {('supported', 'refuted'): 7473, ('refuted', 'supported'): 1995})
        self.assertEqual(sum(counts), sum(directions.values()))
        self.assertEqual(set(directions), {('supported', 'refuted'), ('refuted', 'supported')})

    def run_guessers(self, label, data, with_metadata, allowed=None):
        """Return a failure tuple, or None. `allowed` is a named cue the view is allowed to use."""
        def convert(row):
            features = {name: row[1].get(name, 0) for name in self.vocabulary}
            features.update(row[1])
            if with_metadata:
                features.update(row[2])
            return features, bool(row[3])
        train = [convert(row) for row in data if row[0] <= 300]
        test = [convert(row) for row in data if row[0] > 300]
        base = max(sum(label for _, label in test), sum(not label for _, label in test)) / len(test)
        scores = guesser_scores(train, test)
        best = max(scores.values())
        reference = base if allowed is None else max(base, allowed(data))
        print('Earlier guessers {} {}: best={:.4f} base={:.4f}{} held-out={} winner={}'.format(
            label, 'report+filenames+lengths' if with_metadata else 'report-only', best, base,
            '' if allowed is None else ' named-cue={:.4f}'.format(reference), len(test), max(scores, key=scores.get)))
        return (label, with_metadata, best, reference) if best > reference + 0.03 else None

    def test_report_and_metadata_guessers(self):
        # The earlier Boolean-claim version gave 0.5433 over 300 held-out seeds.
        # Keep the original 300 training seeds and 0.03 bound, but evaluate an
        # additional 1,200 held-out seeds to reduce multiple-probe sampling noise.
        # This is a finite-sample diagnostic, not an information-theoretic proof.
        samples = collections.defaultdict(list)
        self.vocabulary = set()
        for seed in range(1, 1801):
            built = case(seed)
            dossier_lure = any(row['echo_state'] for row in built['key']['expectations'])
            for variant in ('correct', 'mixed'):
                texts, features, metadata = features_of(built, built['files']['report-' + variant + '.md'])
                self.vocabulary.update(features)
                rows = {row['claim_text']: row for row in built['key']['expectations'] if row['variant'] == variant}
                samples['report variant'].append((seed, features, metadata, variant == 'mixed'))
                samples['dossier lure'].append((seed, features, metadata, dossier_lure))
                for position, text in enumerate(texts):
                    name, kind, value = Reading.parse(text)
                    claim_features = dict(features, programme=name, zero_variance=kind == 'variance' and value == 0,
                                          value=value if kind == 'variance' else -1, claim_kind=kind,
                                          position=position, wording=shape(text), claim_bytes=len(text))
                    samples['claim wrong'].append((seed, claim_features, metadata, rows[text]['semantic_state'] != 'supported'))
                    samples['claim lure'].append((seed, claim_features, metadata, rows[text]['echo_state'] is not None))
        failures = []
        for target, data in samples.items():
            for with_metadata in (False, True):
                # A review memo among the sources is the one cue the family cannot remove: with
                # the file list in view, "memo present" predicts "lure" far better than the base
                # rate. The bound is therefore the accuracy of that named rule, and nothing may
                # beat it by more than 0.03.
                allowed = None
                if target == 'dossier lure' and with_metadata:
                    def allowed(rows):
                        held = [row for row in rows if row[0] > 300]
                        return sum(('review.md' in row[2]['filenames']) == bool(row[3]) for row in held) / len(held)
                failure = self.run_guessers(target, data, with_metadata, allowed)
                if failure:
                    failures.append(failure)
        # Inside the dossiers that hold a memo nothing may beat the base rate.
        within = [row for row in samples['dossier lure'] if 'review.md' in row[2]['filenames']]
        for with_metadata in (False, True):
            failure = self.run_guessers('dossier lure given a memo', within, with_metadata)
            if failure:
                failures.append(failure)
        self.assertEqual(failures, [])

    def test_guessers_detect_intentional_length_leak(self):
        train = [({'length': n}, n >= 50) for n in range(100)]
        test = [({'length': n + 0.1}, n >= 50) for n in range(100)]
        self.assertGreaterEqual(guesser_scores(train, test)['length/threshold'], 0.99)

    def test_threshold_within_a_polarity_detects_a_leak_that_only_shows_within_it(self):
        # In one polarity a long record means "lure", in the other a short one does, so a
        # single threshold sees nothing and a threshold fitted per polarity sees everything.
        rng = random.Random(3)
        def sample(count):
            rows = []
            for _ in range(count):
                form, length = rng.choice((('a', 'b'))), rng.randrange(100)
                rows.append(({'form': form, 'own': length}, (length >= 50) == (form == 'a')))
            return rows
        train, test = sample(2000), sample(2000)
        self.assertGreaterEqual(grouped_threshold_score(train, test, 'form', 'own'), 0.95)
        self.assertLess(guesser_scores(train, test)['own/threshold'], 0.6)
        # A group never seen in training falls back to the majority label.
        unseen = [({'form': 'c', 'own': 10}, True)] * 10
        self.assertEqual(grouped_threshold_score(train, unseen, 'form', 'own'),
                         float(sum(label for _, label in train) > len(train) / 2))

    def test_normalised_file_lengths_with_claim_polarity_are_at_base_rate(self):
        # The review's rule: per-file length minus the shortest length that file name
        # shows in the training dossiers, with the claim's polarity; trained on seeds 1
        # to 500, scored on 501 to 1000, with a threshold fitted inside each polarity.
        names = ['procedure.md', 'review.md'] + RECORD_FILES
        rows = []
        for seed in range(1, 1001):
            built = case(seed)
            sources = sources_of(built)
            numbers = record_numbers(sources)
            lengths = {name: len(sources[name].encode()) if name in sources else None for name in names}
            dossier_lure = any(row['echo_state'] for row in built['key']['expectations'])
            for variant in ('correct', 'mixed'):
                for row in (r for r in built['key']['expectations'] if r['variant'] == variant):
                    name, kind, value = Reading.parse(row['claim_text'])
                    rows.append({'seed': seed, 'variant': variant, 'lengths': lengths, 'own': 'record-{:02d}.md'.format(numbers[name]),
                                 'form': (kind, value == 0), 'wrong': row['semantic_state'] != 'supported',
                                 'lure': row['echo_state'] is not None, 'dossier lure': dossier_lure})
        minimum = {name: min(row['lengths'][name] for row in rows if row['seed'] <= 500 and row['lengths'][name] is not None)
                   for name in names}
        def normalised(row, name):
            return -1 if row['lengths'][name] is None else row['lengths'][name] - minimum[name]
        failures = []
        for target in ('wrong', 'lure', 'dossier lure', 'altered report'):
            data = [dict(row, features={name: normalised(row, name) for name in names},
                         own_length=normalised(row, row['own']),
                         label=row['variant'] == 'mixed' if target == 'altered report' else row[target])
                    for row in rows]
            train = [(dict(row['features'], form=row['form'], own=row['own_length']), row['label']) for row in data if row['seed'] <= 500]
            test = [(dict(row['features'], form=row['form'], own=row['own_length']), row['label']) for row in data if row['seed'] > 500]
            base = max(sum(label for _, label in test), sum(not label for _, label in test)) / len(test)
            plain = max(guesser_scores(train, test).values())
            grouped = max(grouped_threshold_score(train, test, 'form', name) for name in list(names) + ['own'])
            reference = base
            if target == 'dossier lure':
                held = [row for row in data if row['seed'] > 500]
                reference = max(base, sum((row['lengths']['review.md'] is not None) == row['label'] for row in held) / len(held))
            print('Earlier normalised-length guessers {}: best plain={:.4f} best within polarity={:.4f} base={:.4f} reference={:.4f} held-out={}'.format(
                target, plain, grouped, base, reference, len(test)))
            if max(plain, grouped) > reference + 0.03:
                failures.append((target, plain, grouped, reference))
        self.assertEqual(failures, [])

    def test_suite_scorer_seal_stage_and_contamination(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='earlier-tests-', dir=ROOT / 'tmp') as temporary:
            root = Path(temporary)
            fixtures, keys, arena, sealed = [root / name for name in ('fixtures', 'keys', 'arena', 'sealed')]
            summary = generate.write_suite(fixtures, keys, 9, 2, ['earlier'])
            loaded = scorecard.load_keys(keys)
            self.assertEqual(set(loaded), set(summary['dossiers']))
            self.assertEqual(summary['claims'], 32)
            self.assertEqual(sum(summary['expected_states'].values()), 32)
            self.assertEqual({key['status'] for key in loaded.values()}, {'computed_by_generator'})
            arena.mkdir()
            blindness.seal_keys(keys, sealed, [arena])
            for dossier in summary['dossiers']:
                for variant, other in (('correct', 'mixed'), ('mixed', 'correct')):
                    original = fixtures / dossier / ('report-' + variant + '.md')
                    record = blindness.stage_case(original, fixtures / dossier / 'sources', arena, root / 'records',
                        {'dossier': dossier, 'variant': variant},
                        withheld=[sealed, fixtures / dossier / ('report-' + other + '.md')])
                    run = Path(record['arena_run'])
                    self.assertEqual((run / 'case/report.md').read_bytes(), original.read_bytes())
                    self.assertNotIn(dossier, record['arena_run'])
                    self.assertEqual({p.name: p.read_bytes() for p in (run / 'case/sources').iterdir()},
                                     {p.name: p.read_bytes() for p in (fixtures / dossier / 'sources').iterdir()})
                    rows = [row for row in json.loads((keys / (dossier + '.json')).read_text())['expectations']
                            if row['variant'] == variant]
                    (run / 'notes.md').write_text('\n'.join(row['claim_text'] + ' ' + row['semantic_state'] for row in rows))
                    self.assertEqual(blindness.scan_run(run, rows)['verbatim_overlaps'], [])
                    (run / 'copied.md').write_text(rows[-1]['rationale'])
                    self.assertTrue(blindness.scan_run(run, rows)['verbatim_overlaps'])
            self.assertFalse(list(fixtures.rglob('*.json')))


if __name__ == '__main__':
    unittest.main()
