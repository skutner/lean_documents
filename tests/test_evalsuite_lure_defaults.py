"""Rendered-document derivation and held-out leakage probes for defaults.

Reading consumes only source prose/tables and claim wording; it imports no family
constants or rule functions. Agreement cannot establish unique English meaning.
Leakage probes split by seed, never by individual claims or report variant.
Source metadata includes UTF-8 byte lengths, character counts after stripping
line-end padding and outer whitespace, character counts after collapsing all
whitespace to single spaces, and whitespace-delimited word counts. Each measure
must remain constant across 600 seeds; both classifiers receive the individual
numeric measures so padding cannot conceal a length-based label cue.
Report features also expose per-kind/per-polarity bounds and monetary-digit or
calendar summaries as individual numeric fields. Claim features retain exact
bounds, pennies or weekdays, and calendar month/day where applicable.
Position probes read the two listed alternatives of every booking and notice from
the rendered text (never the selection sentences) and ask, per kind of claim,
whether the position of a claim's threshold against them says which stated claim is
wrong, which claim is a lure, which report is altered or whether a dossier holds a
lure. They score by AUC and by precision and recall, which see a partial cue that
accuracy cannot, and they are trained on seeds 1 to 500 and scored on 501 to 1000.
"""
import collections
import datetime
import functools
import itertools
import json
import math
import os
from pathlib import Path
import random
import re
import statistics
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
sys.path.insert(0, str(ROOT / 'skills/evidence-to-proof-review/scripts'))
import generate
generate.HARD_FAMILIES.setdefault('defaults', 'lure_defaults')
import blindness
import run_blind
import scorecard


# Independently reviewed wording: pin the entire document, including P-00 and
# any material outside P-01. Ignore export whitespace, never words or sentences.
REVIEWED_PROCEDURE = """# Merevale Rooms Trust | account review procedure
<a id="P-00"></a>
## P-00 — Issue and purpose
Records Office. Issued by Iona Marsh, records officer, 2 January 2034. This procedure
governs review of room charges and notice response deadlines within the Trust. The review
uses the supplied monthly instructions, booking records, delivery acknowledgements and
complete response clauses. Its purpose is to distinguish what those records establish
from what a member of staff may have used for planning. All prices are in pounds and
pence; a penny is the smallest monetary unit in these accounts. The procedure remains
in force for the records in this packet.
<a id="P-01"></a>
## P-01 — Rates and response periods
A hire rate applies only to the period for which a signed rate instruction states it.
Only a notice's response clause fixes whether its receipt day is counted.
If the supplied documents do not state a rate for a period or a counting convention for a notice, the packet does not establish that item.
The alternatives listed in each record are exhaustive, and each listed alternative remains possible unless that record selects one.
Each booking covers one session, and its hire charge equals the rate for its booking period.
The response deadline is the last counted day of the notice's response period.
Under inclusive counting the receipt date is day one.
Under exclusive counting the date following receipt is day one.
Every day is counted, including weekends and holidays, and no deadline is extended.
Desk estimates and reminder dates do not select a rate or a counting convention.
"""


def report_context(report):
    """Retain title, preamble, citations and every other non-claim character."""
    for text in report_claims(report):
        report = report.replace(text, '<CLAIM>')
    return report


def visible_report_features(report):
    # Raw text exposes every character. Separate fixed text and citations keep
    # a repeatable title/preamble cue learnable despite variable claim values.
    return (report, report_context(report), report.split('<a id=', 1)[0],
            tuple(re.findall(r'Citations?: [^\n]+', report)))


def rows(text):
    lines = [line for line in text.splitlines() if line.startswith('|')]
    cells = lambda line: [cell.strip() for cell in line.strip('|').split('|')]
    return [dict(zip(cells(lines[0]), cells(line))) for line in lines[2:]]


def sources_of(built):
    return {name[8:]: text for name, text in built['files'].items() if name.startswith('sources/')}


def source_lengths(text):
    """Raw export size and three measures that ignore its padding."""
    return (len(text.encode('utf-8')),
            len('\n'.join(line.rstrip() for line in text.splitlines()).strip()),
            len(' '.join(text.split())), len(text.split()))


@functools.lru_cache(maxsize=1000)
def case(seed, index=1):
    return generate.build_case('defaults', seed, index)


def shape(text):
    text = re.sub(r'for the \w+ room', 'for the ROOM room', text)
    text = re.sub(r'notice N\d+', 'notice NOTICE', text)
    return re.sub(r'\d+', '#', text)


def report_claims(text):
    return re.findall(r'The September hire charge for the \w+ room (?:is at most|exceeds) \d+\.\d{2} pounds\.'
                      r'|The response deadline for notice N\d+ falls (?:on or before|after) \d{4}-\d\d-\d\d\.', text)


def aggregate(values):
    values = set(values)
    return 'undetermined' if len(values) == 2 else 'supported' if True in values else 'refuted'


def fit_stump(train):
    """Select a numeric feature and threshold using training rows only."""
    counts = collections.Counter(label for _, label in train)
    numeric = [i for i in range(len(train[0][0])) if isinstance(train[0][0][i], (int, float))]
    best = (max(counts.values()), None, None, counts[True] > counts[False])
    for i in numeric:
        ordered = sorted((features[i], label) for features, label in train)
        left = collections.Counter()
        for n, (value, label) in enumerate(ordered[:-1]):
            left[label] += 1
            following = ordered[n + 1][0]
            if following == value:
                continue
            for lower_label in (False, True):
                correct = left[lower_label] + counts[not lower_label] - left[not lower_label]
                if correct > best[0]:
                    best = (correct, i, (value + following) / 2, lower_label)
    def predict(features):
        _, index, threshold, lower = best
        return lower if index is None or features[index] <= threshold else not lower
    return predict


def auc(positives, negatives):
    """Chance that a random positive outscores a random negative (ties count half)."""
    ordered = sorted([(value, 1) for value in positives] + [(value, 0) for value in negatives])
    ranks, i = [0.0] * len(ordered), 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1][0] == ordered[i][0]:
            j += 1
        for k in range(i, j + 1):
            ranks[k] = (i + j) / 2 + 1
        i = j + 1
    rank_sum = sum(rank for rank, (_, label) in zip(ranks, ordered) if label)
    return (rank_sum - len(positives) * (len(positives) + 1) / 2) / (len(positives) * len(negatives))


def listed_alternatives(sources):
    """Item -> (kind, lower, higher) as a reader gets them from the bookings and the notices alone:
    the two permitted rates, or the deadlines under the two counting conventions. The sentences that
    select one of them are never read here."""
    found = {}
    for room, body in re.findall(r'### (\w+) room\n(.*?)(?=### |\Z)', sources['bookings.md'], re.S):
        text = body.split('approved exactly two possible', 1)[1].split('A separate monthly', 1)[0]
        values = sorted(100 * int(p) + int(c) for p, c in re.findall(r'(\d+)\.(\d\d) pounds', text))
        found[room] = ('hire_charge', values[0], values[1])
    for ident, body in re.findall(r'### Notice (N\d+)\n(.*?)(?=### |\Z)', sources['notices.md'], re.S):
        received = datetime.date.fromisoformat(re.search(r'records receipt on (\d{4}-\d\d-\d\d)', body)[1])
        last = received.toordinal() + int(re.search(r'The response period is (\d+) days\.', body)[1])
        found[ident] = ('response_deadline', last - 1, last)
    return found


def relation(value, low, high, kind):
    """Position of a threshold against the two alternatives, coarse and fine (exact offsets of one or two)."""
    coarse = ('below', 'equals lower', 'between', 'equals higher', 'above')[
        (value >= low) + (value > low) + (value >= high) + (value > high)]
    clip = lambda d: d if abs(d) <= 2 else (9 if d > 0 else -9)  # noqa: E731
    return coarse, (max(-4, min(4, value - high)) if kind == 'response_deadline' else (clip(value - low), clip(value - high)))


def claim_rows(first=1, last=1000):
    """One row per claim of both reports, with the relation of its threshold to the listed alternatives."""
    rows = []
    for seed in range(first, last + 1):
        first_row = len(rows)
        built = case(seed)
        alternatives = listed_alternatives(sources_of(built))
        selected = Reading(sources_of(built)).actual
        has_lure = any(row['echo_state'] for row in built['key']['expectations'])
        for row in built['key']['expectations']:
            kind, ident, at_most, value = Reading.parse(row['claim_text'])
            _, low, high = alternatives[ident]
            value = value.toordinal() if isinstance(value, datetime.date) else value
            coarse, fine = relation(value, low, high, kind)
            rows.append({'seed': seed, 'kind': kind, 'at_most': at_most, 'coarse': coarse, 'fine': fine,
                         'listed': value in (low, high), 'altered': row['variant'] == 'mixed',
                         'stated': len(selected[kind, ident]) == 1, 'wrong': row['error_type'] is not None,
                         'lure': row['echo_state'] is not None, 'dossier_lure': has_lure})
        # How many claims of the same kind and wording the report holds, this one included.
        sizes = collections.Counter((row['altered'], row['kind'], row['at_most']) for row in rows[first_row:])
        for row in rows[first_row:]:
            row['same_wording'] = sizes[row['altered'], row['kind'], row['at_most']]
    return rows


def cell_score(rows, target, key, train_seeds=500):
    """Fit per-cell rates of `target` on the seeds up to `train_seeds` and score the later seeds.
    Returns accuracy of the majority rule, its baseline, the AUC of the cell rate and that AUC's z against
    chance, and precision and recall of the rule "cell rate above the training base rate"."""
    train = [row for row in rows if row['seed'] <= train_seeds]
    test = [row for row in rows if row['seed'] > train_seeds]
    cells = collections.defaultdict(lambda: [0, 0])
    for row in train:
        cells[key(row)][0] += row[target]
        cells[key(row)][1] += 1
    prior = sum(row[target] for row in train) / len(train)
    rate = lambda row: cells[key(row)][0] / cells[key(row)][1] if key(row) in cells else prior  # noqa: E731
    positives = [rate(row) for row in test if row[target]]
    negatives = [rate(row) for row in test if not row[target]]
    share = len(positives) / len(test)
    accuracy = sum((rate(row) > .5) == row[target] for row in test) / len(test)
    chance = math.sqrt((len(test) + 1) / (12 * len(positives) * len(negatives)))
    area = auc(positives, negatives)
    flagged = [row for row in test if rate(row) > prior + 1e-9]
    hits = sum(row[target] for row in flagged)
    return {'accuracy': accuracy, 'baseline': max(share, 1 - share), 'auc': area, 'z': (area - .5) / chance,
            'precision': hits / len(flagged) if flagged else float('nan'), 'recall': hits / len(positives), 'n': len(test)}


class Reading:
    """Enumerate the alternatives stated in the files, then the desk defaults."""

    def __init__(self, sources):
        assert ' '.join(sources['procedure.md'].split()) == ' '.join(REVIEWED_PROCEDURE.split()), \
            'Procedure differs from reviewed wording'
        self.actual, self.default, self.pair = {}, {}, {}
        august = dict((room, self.pence(value)) for room, value in re.findall(
            r'(\w+) room: August rate (\d+\.\d\d) pounds per session\.', sources['briefing.md']))
        assert 'starts counting on the day after receipt' in sources['briefing.md']
        for room, body in re.findall(r'### (\w+) room\n(.*?)(?=### |\Z)', sources['bookings.md'], re.S):
            rates = re.search(r'exactly two possible September rates for this room: (\d+\.\d\d) pounds or '
                              r'(\d+\.\d\d) pounds per session', body)
            assert rates, room
            possibilities = tuple(self.pence(value) for value in rates.groups())
            chosen = re.search(r'For September the rate is (\d+\.\d\d) pounds per session\.', body)
            if chosen:
                values = (self.pence(chosen[1]),)
                assert values[0] in possibilities
            else:
                assert 'No supplied document selects either September rate.' in body
                values = possibilities
            key = ('hire_charge', room)
            self.actual[key] = values
            self.pair[key] = tuple(sorted(possibilities))
            self.default[key] = values if chosen else (august[room],)
        for ident, body in re.findall(r'### Notice (N\d+)\n(.*?)(?=### |\Z)', sources['notices.md'], re.S):
            received = datetime.date.fromisoformat(re.search(r'records receipt on (\d{4}-\d\d-\d\d)', body)[1])
            period = int(re.search(r'The response period is (\d+) days\.', body)[1])
            chosen = re.search(r'using (inclusive|exclusive) counting', body)
            conventions = (chosen[1],) if chosen else ('inclusive', 'exclusive')
            if not chosen:
                assert 'does not select inclusive or exclusive counting.' in body
            def count(convention):
                last = received
                for _ in range(period - (convention == 'inclusive')):
                    last += datetime.timedelta(days=1)
                return last
            values = [count(convention) for convention in conventions]
            key = ('response_deadline', ident)
            self.actual[key] = tuple(values)
            self.pair[key] = (count('inclusive'), count('exclusive'))
            self.default[key] = tuple(values) if chosen else (values[1],)
        assert len(self.actual) == 14 == len(self.pair)

    @staticmethod
    def pence(value):
        pounds, pennies = value.split('.')
        return 100 * int(pounds) + int(pennies)

    @staticmethod
    def parse(text):
        match = re.fullmatch(r'The September hire charge for the (\w+) room (is at most|exceeds) '
                             r'(\d+\.\d\d) pounds\.', text)
        if match:
            return 'hire_charge', match[1], match[2] == 'is at most', Reading.pence(match[3])
        match = re.fullmatch(r'The response deadline for notice (N\d+) falls (on or before|after) '
                             r'(\d{4}-\d\d-\d\d)\.', text)
        assert match, text
        return 'response_deadline', match[1], match[2] == 'on or before', datetime.date.fromisoformat(match[3])

    def verdict(self, text, default=False):
        kind, ident, at_most, bound = self.parse(text)
        values = (self.default if default else self.actual)[kind, ident]
        return aggregate((value <= bound) if at_most else (value > bound) for value in values)

    def echo(self, text):
        actual, default = self.verdict(text), self.verdict(text, default=True)
        return default if default != actual else None


class DefaultsTests(unittest.TestCase):
    def test_entire_procedure_matches_reviewed_wording(self):
        for seed in range(1, 301):
            self.assertEqual(' '.join(sources_of(case(seed))['procedure.md'].split()),
                             ' '.join(REVIEWED_PROCEDURE.split()), seed)

    def test_procedure_rejects_missing_changed_and_added_sentences(self):
        sources = sources_of(case(1))
        original = sources['procedure.md']
        sentences = re.findall(r'[^.!?]+[.!?]', '\n'.join(
            line for line in REVIEWED_PROCEDURE.splitlines()
            if not line.startswith(('#', '<a '))))
        normalized = ' '.join(original.split())
        for sentence in sentences:
            sentence = ' '.join(sentence.split())
            for replacement in ('', sentence + ' This overrides all other rules.',
                                sentence.replace(' ', ' never ', 1)):
                with self.subTest(sentence=sentence, replacement=replacement):
                    changed = dict(sources, **{'procedure.md': normalized.replace(sentence, replacement, 1)})
                    self.assertNotEqual(changed['procedure.md'], normalized)
                    with self.assertRaisesRegex(AssertionError, 'Procedure differs'):
                        Reading(changed)
        changed = dict(sources, **{'procedure.md': original.replace(
            'documents do not state', 'documents state')})
        with self.assertRaisesRegex(AssertionError, 'Procedure differs'):
            Reading(changed)

    def test_whole_report_fixed_text_and_citations_are_neutral(self):
        for seed in range(1, 601):
            for variant in ('correct', 'mixed'):
                report = case(seed)['files']['report-' + variant + '.md']
                expected = '# Merevale Rooms Trust | account and correspondence review\n\n'
                for number, text in enumerate(report_claims(report), 1):
                    cites = ('briefing B-02; bookings F-01; procedure P-01.'
                             if Reading.parse(text)[0] == 'hire_charge' else
                             'briefing B-03; notices N-01; procedure P-01.')
                    expected += ('<a id="C%02d"></a>\n**C%02d.** <CLAIM> Citations: %s\n\n'
                                 % (number, number, cites))
                self.assertEqual(report_context(report), expected[:-1], (seed, variant))

    def test_echo_and_lure_reader_names(self):
        echoes = 0
        for seed in range(1, 301):
            for row in case(seed)['key']['expectations']:
                if row['echo_state'] is not None:
                    echoes += 1
                    self.assertEqual(row.get('echo_reader'), 'fills_in_default')
                self.assertLessEqual(set(row.get('lure_states', {})), {'fills_in_default'})
        self.assertGreater(echoes, 0)

    def test_changed_rendered_values_move_verdict(self):
        for kind in ('hire_charge', 'response_deadline'):
            moved = False
            for seed in range(1, 61):
                built = case(seed)
                sources = sources_of(built)
                reading = Reading(sources)
                for row in built['key']['expectations']:
                    topic, ident, side, bound = Reading.parse(row['claim_text'])
                    if topic != kind or len(reading.actual[topic, ident]) != 1:
                        continue
                    changed = dict(sources)
                    if kind == 'hire_charge':
                        body = changed['bookings.md']
                        value = bound + 1000 if side else bound - 1000
                        money = '%d.%02d' % divmod(value, 100)
                        pattern = r'(### ' + ident + r' room\n)(.*?)(?=### |\Z)'
                        def replace(match):
                            section = re.sub(r'(For September the rate is )\d+\.\d\d', r'\g<1>' + money, match[2])
                            section = re.sub(r'(rates for this room: )\d+\.\d\d', r'\g<1>' + money, section)
                            return match[1] + section
                        changed['bookings.md'] = re.sub(pattern, replace, body, flags=re.S)
                    else:
                        delta = 365 if side else -365
                        pattern = r'(### Notice ' + ident + r'\n)(.*?)(?=### |\Z)'
                        def replace(match):
                            section = re.sub(r'(records receipt on )(\d{4}-\d\d-\d\d)',
                                lambda found: found[1] + (datetime.date.fromisoformat(found[2]) +
                                                         datetime.timedelta(days=delta)).isoformat(), match[2])
                            return match[1] + section
                        changed['notices.md'] = re.sub(pattern, replace, changed['notices.md'], flags=re.S)
                    if Reading(changed).verdict(row['claim_text']) != reading.verdict(row['claim_text']):
                        moved = True
                        break
                if moved:
                    break
            self.assertTrue(moved, kind)

    def test_keys_and_echoes_match_independent_rendered_reading(self):
        for seed in range(1, 61):
            built = case(seed)
            reading = Reading(sources_of(built))
            for row in built['key']['expectations']:
                self.assertEqual(row['semantic_state'], reading.verdict(row['claim_text']), (seed, row))
                self.assertEqual(row['echo_state'], reading.echo(row['claim_text']), (seed, row))
                self.assertEqual(row['claim_kind'], Reading.parse(row['claim_text'])[0])
                if row['echo_state'] is not None:
                    self.assertEqual(row['echo_derivation'], {
                        'worlds': 1, 'worlds_where_true': int(row['echo_state'] == 'supported')})

    def test_every_slot_variant(self):
        counts, errors = collections.Counter(), collections.Counter()
        for seed in range(1, 61):
            built = generate.family_builder('defaults')(random.Random(seed))
            reading = Reading(built['sources'])
            self.assertEqual(len(built['slots']), 12)
            self.assertEqual(built['told'] is None, all(len(v) == 1 for v in reading.actual.values()))
            if built['told']:
                self.assertEqual(built['told']['unknowns'], {})
                for key, value in built['base'].items():
                    self.assertEqual(built['told']['base'][key], value)
            for true, variants in built['slots']:
                self.assertEqual(reading.verdict(true['text']), 'supported')
                self.assertEqual(len({item['text'] for item in [true] + variants}), 1 + len(variants))
                for item in [true] + variants:
                    self.assertEqual(item['kind'], true['kind'])
                    self.assertEqual(reading.verdict(item['text']),
                                     generate.judge(item, built['base'], built['unknowns'])[0])
                    told = built['told'] or {'base': built['base'], 'unknowns': built['unknowns']}
                    self.assertEqual(reading.verdict(item['text'], default=True),
                                     generate.judge(item, told['base'], told['unknowns'])[0])
                    counts[item['kind']] += 1
                kind, ident, side, bound = Reading.parse(true['text'])
                unit = datetime.timedelta(days=1) if kind == 'response_deadline' else 1
                low, high = reading.pair[kind, ident]
                selected = reading.actual[kind, ident]
                # A stated item: every threshold is the lower alternative or the value just below the
                # higher one; the right claim says "at most" exactly when the lower one is selected.
                self.assertEqual(len(selected), 1, true['text'])
                self.assertIn(bound, {low, high - unit})
                self.assertEqual(side, selected == (low,))
                for variant in variants:
                    v_kind, v_ident, v_side, v_bound = Reading.parse(variant['text'])
                    if variant['error'] == 'fills_in_default':
                        # The lure takes the place of the item: the open item, either wording, same thresholds.
                        o_low, o_high = reading.pair[v_kind, v_ident]
                        self.assertNotEqual(v_ident, ident)
                        self.assertEqual(len(reading.actual[v_kind, v_ident]), 2)
                        self.assertIn(v_bound, {o_low, o_high - unit})
                    else:
                        self.assertEqual(v_ident, ident)
                        self.assertEqual(v_side, not side)
                        self.assertIn(v_bound, {low, high - unit})
                        boundary = kind == 'hire_charge' and v_bound == (low if side else high - unit)
                        self.assertEqual(variant['error'], 'excludes_boundary' if boundary else 'other_alternative_taken')
                    errors[kind, variant['error']] += 1
                if any(v['error'] == 'fills_in_default' for v in variants):
                    # One wording, at every threshold of the open item.
                    o_low, o_high = reading.pair[Reading.parse(variants[0]['text'])[:2]]
                    parsed = [Reading.parse(v['text']) for v in variants]
                    self.assertEqual(len({p[2] for p in parsed}), 1)
                    self.assertEqual(sorted(p[3] for p in parsed), sorted({o_low, o_high - unit}))
        self.assertEqual(len(counts), 2)
        # Measured: 1,260 claims (a right claim and its variants, all slots) over seeds 1 to 60; the earlier
        # construction gave 1,440, against a bound of 1,400, because the three wording-stable items per
        # kind have no variants. The band is for a change that is meant, the exact count for one that is not.
        self.assertGreater(sum(counts.values()), 1200)
        self.assertEqual(sum(counts.values()), 1260)
        self.assertEqual(set(errors), {('hire_charge', 'fills_in_default'), ('hire_charge', 'excludes_boundary'),
                                       ('hire_charge', 'other_alternative_taken'),
                                       ('response_deadline', 'fills_in_default'),
                                       ('response_deadline', 'other_alternative_taken')})

    def test_correct_supported_and_mixed_typed_errors(self):
        errors = set()
        for seed in range(1, 301):
            expected = case(seed)['key']['expectations']
            correct = {row['claim_id']: row for row in expected if row['variant'] == 'correct'}
            changed = []
            for row in expected:
                if row['variant'] == 'correct' or row['claim_text'] == correct[row['claim_id']]['claim_text']:
                    self.assertEqual(row['semantic_state'], 'supported')
                    self.assertIsNone(row['error_type'])
                else:
                    self.assertIn(row['semantic_state'], ('refuted', 'undetermined'))
                    self.assertIsInstance(row['error_type'], str)
                    self.assertTrue(row['error_type'])
                    errors.add(row['error_type'])
                    changed.append(row)
            self.assertGreaterEqual(len(changed), 2)
        self.assertEqual(errors, {'fills_in_default', 'excludes_boundary', 'other_alternative_taken'})

    def test_determinism_and_global_random_independence_over_300_seeds(self):
        previous = random.getstate()
        try:
            for seed in range(1, 301):
                random.seed(seed * 37)
                state = random.getstate()
                self.assertEqual(case(seed), generate.build_case('defaults', seed, 1))
                self.assertEqual(random.getstate(), state)
            self.assertNotEqual(case(1)['files'], case(2)['files'])
        finally:
            random.setstate(previous)

    def test_hash_seed_independence(self):
        script = ("import sys,json,hashlib;sys.dont_write_bytecode=True;sys.path.insert(0,'evalsuite');"
                  "import generate;generate.HARD_FAMILIES.setdefault('defaults','lure_defaults');"
                  "print(hashlib.sha256(json.dumps([generate.build_case('defaults',s,1) "
                  "for s in (1,13,71)],sort_keys=True).encode()).hexdigest())")
        outputs = []
        for seed in ('1', '9876'):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONDONTWRITEBYTECODE='1')
            outputs.append(subprocess.check_output([sys.executable, '-c', script], cwd=ROOT, env=env, text=True))
        self.assertEqual(outputs[0], outputs[1])

    def test_same_kinds_slots_and_no_label_leaks(self):
        for seed in range(1, 301):
            built = case(seed)
            for name, text in sources_of(built).items():
                self.assertNotRegex(text.lower(), r'\b(evaluator|echo_state|error_type|fills_in_default|'
                                    r'excludes_boundary|undetermined|refuted|answer key)\b', (seed, name))
            selected = {variant: [r for r in built['key']['expectations'] if r['variant'] == variant]
                        for variant in ('correct', 'mixed')}
            for left, right in zip(selected['correct'], selected['mixed']):
                # The kind of every slot is the same in both reports. An altered stated claim takes the
                # other wording (so the wording of a claim never shows an alteration) and a lure takes the
                # place of a stated item; an unaltered claim is identical in both reports.
                self.assertEqual((left['claim_id'], left['claim_kind']), (right['claim_id'], right['claim_kind']))
                if left['claim_text'] == right['claim_text']:
                    continue
                self.assertIsNotNone(right['error_type'])
                subject = [Reading.parse(row['claim_text'])[:2] for row in (left, right)]
                if right['error_type'] == 'fills_in_default':
                    self.assertNotEqual(subject[0], subject[1])
                    self.assertIsNotNone(right['echo_state'])
                else:
                    self.assertEqual(subject[0], subject[1])
                    self.assertNotEqual(shape(left['claim_text']), shape(right['claim_text']))
            for variant in selected:
                report = built['files']['report-' + variant + '.md']
                self.assertEqual(len(report_claims(report)), 12)
                self.assertEqual(collections.Counter(report_claims(report)),
                                 collections.Counter(row['claim_text'] for row in selected[variant]))
                self.assertNotRegex(report.lower(), r'\b(correct|mixed|evaluator|echo_state|error_type|undetermined|refuted)\b')
                self.assertEqual(len({Reading.parse(row['claim_text'])[:2] for row in selected[variant]}), 12)
                for row in selected[variant]:
                    self.assertTrue(row['source_anchors'])
                    for anchor in row['source_anchors']:
                        file, section = anchor.split('#')
                        self.assertIn('<a id="' + section + '"></a>', built['files'][file])

    def test_all_wording_shapes_reach_all_three_states(self):
        states = collections.defaultdict(set)
        for seed in range(1, 401):
            for row in case(seed)['key']['expectations']:
                states[shape(row['claim_text'])].add(row['semantic_state'])
        self.assertEqual(len(states), 4)
        for wording, seen in states.items():
            self.assertEqual(seen, {'supported', 'refuted', 'undetermined'}, wording)

    def test_same_shape_controls_in_each_report(self):
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            for variant in ('correct', 'mixed'):
                texts = report_claims(built['files']['report-' + variant + '.md'])
                for text in texts:
                    if reading.echo(text) is not None:
                        self.assertTrue(any(shape(other) == shape(text) and reading.echo(other) is None
                                            for other in texts), (seed, variant, text))

    def test_each_kind_has_both_undetermined_echo_directions(self):
        directions = collections.defaultdict(set)
        for seed in range(1, 401):
            for row in case(seed)['key']['expectations']:
                if row['echo_state'] is not None:
                    self.assertEqual(row['semantic_state'], 'undetermined')
                    directions[row['claim_kind']].add(row['echo_state'])
        self.assertEqual(len(directions), 2)
        for kind, seen in directions.items():
            self.assertEqual(seen, {'supported', 'refuted'}, kind)

    def test_no_none_text(self):
        for seed in range(1, 301):
            for name, text in case(seed)['files'].items():
                self.assertNotIn('None', text, (seed, name))

    def test_audit_paragraph_splitter_compatibility(self):
        import audit
        for seed in (1, 11, 31):
            for variant in ('correct', 'mixed'):
                text = case(seed)['files']['report-' + variant + '.md']
                self.assertEqual(run_blind.paragraphs(text), audit.paragraphs(text))

    def test_source_length_purpose_office_and_date(self):
        lengths = collections.defaultdict(set)
        for seed in range(1, 601):
            sources = sources_of(case(seed))
            self.assertEqual(set(sources), {'briefing.md', 'procedure.md', 'bookings.md', 'notices.md'})
            prose = '\n'.join(line for text in sources.values() for line in text.splitlines()
                              if not line.startswith(('|', '#', '<a ')))
            self.assertGreaterEqual(len(prose.split()), 1500)
            for name, text in sources.items():
                for measure, length in zip(('bytes', 'stripped_chars', 'normalized_chars', 'words'),
                                           source_lengths(text)):
                    lengths[name, measure].add(length)
                self.assertRegex(text, r'\d{4}-\d\d-\d\d|\d{1,2} [A-Z][a-z]+ 2034', name)
                self.assertRegex(text.lower(), r'\boffice\b', name)
                self.assertRegex(text.lower(), r'\b(purpose|supports|governs)\b', name)
        for field, values in lengths.items():
            self.assertEqual(len(values), 1, (field, values))

    def test_stated_nondefault_values_occur_for_both_kinds(self):
        total, nondefault = collections.Counter(), collections.Counter()
        for seed in range(1, 301):
            sources = sources_of(case(seed))
            reading = Reading(sources)
            august = {room: Reading.pence(value) for room, value in re.findall(
                r'(\w+) room: August rate (\d+\.\d\d) pounds per session\.', sources['briefing.md'])}
            for (kind, ident), values in reading.actual.items():
                if len(values) != 1:
                    continue
                total[kind] += 1
                if kind == 'hire_charge':
                    nondefault[kind] += values[0] != august[ident]
                else:
                    section = re.search(r'### Notice ' + ident + r'\n(.*?)(?=### |\Z)', sources['notices.md'], re.S)[1]
                    nondefault[kind] += 'using inclusive counting' in section
        for kind in total:
            self.assertTrue(0.40 <= nondefault[kind] / total[kind] <= 0.60, (kind, nondefault, total))

    def test_no_lure_share_and_r9_campaign_counts(self):
        no_lure, counts = 0, []
        for seed in range(1, 101):
            count = 0
            for index in range(1, 13):
                built = case(seed, index)
                reading = Reading(sources_of(built))
                echoes = []
                for row in built['key']['expectations']:
                    echo = reading.echo(row['claim_text'])
                    self.assertEqual(row['echo_state'], echo)
                    self.assertEqual(row['semantic_state'], reading.verdict(row['claim_text']))
                    if echo is not None:
                        echoes.append(row)
                self.assertIn(len(echoes), (0, 2))
                if echoes:
                    self.assertEqual(len({row['claim_kind'] for row in echoes}), 2)
                    self.assertEqual({row['variant'] for row in echoes}, {'mixed'})
                no_lure += not echoes
                count += len(echoes)
            counts.append(count)
        print('Defaults R9 min/median/max:', min(counts), statistics.median(counts), max(counts),
              '; no-lure share:', no_lure / 1200)
        self.assertTrue(0.24 <= no_lure / 1200 <= 0.36)
        self.assertGreaterEqual(statistics.median(counts), 15)

    def check_guessers(self, metadata):
        # Predeclared tolerance: three conservative standard errors at p=.5,
        # treating a seed as the independent unit (300 train, 300 holdout).
        # These are finite probes, not a proof that no stronger classifier exists.
        datasets = collections.defaultdict(list)
        for seed in range(1, 601):
            built = case(seed)
            source_files = sorted(sources_of(built).items())
            # Keep each size as its own numeric feature: an opaque metadata
            # tuple would prevent the stump from finding a length threshold.
            extra = ((tuple(name for name, _ in source_files),) +
                     tuple(size for _, text in source_files for size in source_lengths(text))) if metadata else ()
            for variant in ('correct', 'mixed'):
                report = built['files']['report-' + variant + '.md']
                texts = report_claims(report)
                parsed = [Reading.parse(text) for text in texts]
                # Features depend only on the selected report, plus permitted
                # names/lengths. Keys are attached after feature extraction.
                values = [p[3].toordinal() if isinstance(p[3], datetime.date) else p[3] for p in parsed]
                summaries = []
                for kind in ('hire_charge', 'response_deadline'):
                    for polarity in (False, True):
                        group = [p[3] for p in parsed if p[0] == kind and p[2] == polarity]
                        # A report no longer holds three claims of each wording per kind: a wording
                        # that is absent is summarised by zeros, so its absence is itself a feature.
                        numbers = [v.toordinal() if isinstance(v, datetime.date) else v for v in group] or [0]
                        summaries.extend((min(numbers), statistics.mean(numbers), max(numbers)))
                        if kind == 'hire_charge':
                            pennies = [v % 100 for v in group] or [0]
                            summaries.extend((min(pennies), statistics.mean(pennies), max(pennies),
                                              sum(v % 10 == 0 for v in pennies)))
                        else:
                            summaries.extend((statistics.mean([v.weekday() for v in group] or [0]),
                                              sum(v.weekday() >= 5 for v in group),
                                              statistics.mean([v.day for v in group] or [0]),
                                              statistics.mean([v.month for v in group] or [0])))
                visible = visible_report_features(report)
                report_features = (len(report), tuple(sorted(collections.Counter(shape(t) for t in texts).items())),
                                   tuple(sorted(int(v) // 100 for v in values))) + tuple(summaries) + extra + visible
                selected = {row['claim_text']: row for row in built['key']['expectations']
                            if row['variant'] == variant}
                datasets['altered_report'].append((seed, report_features, variant == 'mixed'))
                datasets['dossier_has_lure'].append((seed, report_features,
                    any(row['echo_state'] is not None for row in built['key']['expectations'])))
                for text, parsed_claim, value in zip(texts, parsed, values):
                    same_kind = sorted(v for p, v in zip(parsed, values) if p[0] == parsed_claim[0])
                    bound = parsed_claim[3]
                    details = (bound.weekday(), bound.month, bound.day) if isinstance(bound, datetime.date) else (
                        bound % 100, bound % 10, 0)
                    feature = (shape(text), parsed_claim[1], int(value) // 100,
                               same_kind.index(value), len(report), value) + details + extra + visible
                    row = selected[text]
                    datasets['wrong_claim'].append((seed, feature, row['semantic_state'] != 'supported'))
                    datasets['lure_claim'].append((seed, feature, row['echo_state'] is not None))
        for target, samples in datasets.items():
            train = [(features, label) for seed, features, label in samples if seed <= 300]
            heldout = [(features, label) for seed, features, label in samples if seed > 300]
            counts = collections.Counter(label for _, label in train)
            tables = collections.defaultdict(collections.Counter)
            vocabulary = collections.defaultdict(set)
            for features, label in train:
                for i, feature in enumerate(features):
                    tables[label, i][feature] += 1
                    vocabulary[i].add(feature)
            def guess(features):
                def likelihood(label):
                    return math.log((counts[label] + 1) / (len(train) + 2)) + sum(
                        math.log((tables[label, i][feature] + 1) / (counts[label] + len(vocabulary[i]) + 1))
                        for i, feature in enumerate(features))
                return max((False, True), key=likelihood)
            baseline = max(collections.Counter(label for _, label in heldout).values()) / len(heldout)
            stump = fit_stump(train)
            if target in ('altered_report', 'dossier_has_lure'):
                # Positive control: every exposed report summary must be able
                # to carry a learnable threshold, with unseen values at test.
                for index in range(3, 31):
                    self.assertIsInstance(train[0][0][index], (int, float))
                    def synthetic(value):
                        features = list(train[0][0])
                        features[index] = value
                        return tuple(features)
                    control = fit_stump([(synthetic(value), value > 0) for value in (-5, -3, -1, 1, 3, 5)])
                    for value in (-4, -2, 2, 4):
                        self.assertEqual(control(synthetic(value)), value > 0, (target, index))
            for method, classifier in (('naive_bayes', guess), ('numeric_stump', stump)):
                accuracy = sum(classifier(features) == label for features, label in heldout) / len(heldout)
                print('Defaults guesser', 'report+metadata' if metadata else 'report-only', target, method,
                      'accuracy=%.4f majority=%.4f delta=%+.4f n=%d' %
                      (accuracy, baseline, accuracy - baseline, len(heldout)))
                self.assertLessEqual(accuracy, baseline + 3 * math.sqrt(0.25 / 300),
                                     (metadata, target, method, accuracy, baseline))

    def test_report_only_guessers_held_out_by_seed(self):
        self.check_guessers(False)

    def test_report_and_filenames_lengths_guessers_held_out_by_seed(self):
        self.check_guessers(True)

    def test_threshold_position_does_not_decide_a_stated_or_lure_claim(self):
        """A claim's threshold stands in a fixed relation to the two listed alternatives: outside the pair
        the wording alone decides the claim, inside it the selection does. This reads that relation from
        the rendered bookings and notices (never the selection sentences) and asks, per kind of claim,
        whether it says which stated claim is wrong or which claim is a lure. Trained on seeds 1 to 500,
        scored on 501 to 1000. A partial cue shows in the AUC and in precision and recall, not only in
        accuracy; each must stay within three standard errors of chance."""
        rows = claim_rows()
        for kind in ('hire_charge', 'response_deadline'):
            of_kind = [row for row in rows if row['kind'] == kind]
            stated = [row for row in of_kind if row['stated']]
            for target, population in (('wrong', stated), ('lure', of_kind)):
                # The reviewer's rule: "at most V" is wrong when V is not a listed alternative, "exceeds V"
                # when it is.
                for name, key in (('listed alternative rule', lambda row: (not row['listed']) if row['at_most']
                                   else row['listed']),
                                  ('wording', lambda row: row['at_most']),
                                  ('wording and relation', lambda row: (row['at_most'], row['coarse'])),
                                  ('wording and fine relation', lambda row: (row['at_most'], row['fine']))):
                    score = cell_score(population, target, key)
                    print('Defaults relation guesser', kind, target, name, ' '.join(
                        '%s=%.3f' % item for item in score.items()))
                    self.assertLessEqual(score['accuracy'], score['baseline'] + 3 * math.sqrt(0.25 / score['n']), (kind, target, name))
                    self.assertLess(abs(score['z']), 3, (kind, target, name, score))
            # Every wrong claim and every lure sits inside the pair, as every right claim does.
            for row in of_kind:
                self.assertIn(row['coarse'], ('equals lower', 'between'), row)

    def test_relation_measure_sees_a_partial_cue(self):
        """The measure above must see a cue that accuracy cannot: about one stated claim in twenty-five is made
        wrong whenever it sits at one position and has one seed residue (a sixth of that cell)."""
        rows = [dict(row) for row in claim_rows() if row['kind'] == 'hire_charge' and row['stated']]
        rate = sum(row['wrong'] for row in rows) / len(rows)
        for row in rows:
            row['planted'] = row['wrong'] or (row['seed'] % 6 == 0 and row['at_most'] and row['coarse'] == 'between')
        score = cell_score(rows, 'planted', lambda row: (row['at_most'], row['coarse']))
        self.assertAlmostEqual(score['accuracy'], score['baseline'], places=9, msg=score)
        self.assertGreater(score['z'], 3, score)
        self.assertGreater(score['precision'], rate + 0.05, score)

    def test_report_position_counts_do_not_show_the_altered_report_or_a_lure(self):
        """Per report: how many thresholds lie outside the pair, how many claims say "at most" or "on or
        before", per kind. Every report holds none outside, and the wording counts are spread alike in
        both reports of a dossier (an alteration takes the other wording), so neither says which report
        is altered or whether the dossier holds a lure."""
        reports = collections.defaultdict(list)
        for row in claim_rows():
            reports[row['seed'], row['altered']].append(row)
        units = []
        for (seed, altered), claims in reports.items():
            outside = sum(row['coarse'] not in ('equals lower', 'between') for row in claims)
            self.assertEqual(outside, 0, (seed, altered))
            units.append({'seed': seed, 'altered': altered, 'dossier_lure': claims[0]['dossier_lure'],
                          'hire_at_most': sum(r['at_most'] for r in claims if r['kind'] == 'hire_charge'),
                          'date_at_most': sum(r['at_most'] for r in claims if r['kind'] == 'response_deadline')})
        for target in ('altered', 'dossier_lure'):
            for name, key in (('wording counts', lambda u: (u['hire_at_most'], u['date_at_most'])),
                              ('hire wording count', lambda u: u['hire_at_most']),
                              ('date wording count', lambda u: u['date_at_most'])):
                score = cell_score(units, target, key)
                print('Defaults report-count guesser', target, name, ' '.join('%s=%.3f' % item for item in score.items()))
                self.assertLessEqual(score['accuracy'], score['baseline'] + 3 * math.sqrt(0.25 / score['n']), (target, name))
                self.assertLess(abs(score['z']), 3, (target, name, score))
        # The two histograms of the number of "at most" claims per kind: chi-square, 6 degrees of freedom.
        for kind in ('hire_at_most', 'date_at_most'):
            histogram = {flag: collections.Counter(u[kind] for u in units if u['altered'] == flag) for flag in (False, True)}
            statistic = 0.0
            for count in range(7):
                pair = (histogram[False][count], histogram[True][count])
                if sum(pair):
                    statistic += (pair[0] - pair[1]) ** 2 / sum(pair)
            self.assertLess(statistic, 22.46, (kind, histogram))

    def test_wording_count_cue_stays_within_its_documented_size(self):
        """What the construction does not remove, and its measured size. A report no longer holds three
        claims of each wording per kind: the claim of an item whose wording is fixed (never altered) and
        the lure's same-wording control make the number of claims that share a wording in the report
        a weak cue, measured in the altered reports (the correct ones hold no wrong claim and no lure).
        The bounds are the documented sizes, not a claim that the cue is zero."""
        rows = [row for row in claim_rows() if row['altered']]
        for kind in ('hire_charge', 'response_deadline'):
            for target, population in (('wrong', [row for row in rows if row['stated'] and row['kind'] == kind]),
                                       ('lure', [row for row in rows if row['kind'] == kind])):
                score = cell_score(population, target, lambda row: (row['at_most'], row['same_wording']))
                print('Defaults same-wording-count guesser', kind, target, ' '.join('%s=%.3f' % item for item in score.items()))
                self.assertLess(score['auc'], 0.58, (kind, target, score))
                self.assertLessEqual(score['accuracy'], score['baseline'] + 3 * math.sqrt(0.25 / score['n']))

    def test_every_alteration_the_generator_can_draw_keeps_a_same_wording_control(self):
        """R4 by enumeration, not sampling. For each kind, take every combination of the claims a slot can
        show in the altered report (a lure slot always shows one of its lures) and require that each
        lure has a claim of the same wording and kind, with no echo, in that report."""
        checked = 0
        for seed in range(1, 101):
            built = generate.family_builder('defaults')(random.Random(seed))
            if built['told'] is None:
                continue
            told = built['told']
            for kind in ('hire_charge', 'response_deadline'):
                options = []
                for true, variants in built['slots']:
                    if true['kind'] != kind:
                        continue
                    lures = [v for v in variants if v['error'] == 'fills_in_default']
                    options.append(lures if lures else [true] + variants)
                self.assertEqual(len(options), 6)
                for combination in itertools.product(*options):
                    echoing = [item for item in combination
                               if generate.judge(item, built['base'], built['unknowns'])[0] !=
                               generate.judge(item, told['base'], told['unknowns'])[0]]
                    self.assertEqual(len(echoing), 1, (seed, kind))
                    wording = Reading.parse(echoing[0]['text'])[2]
                    controls = [item for item in combination if item is not echoing[0] and
                                Reading.parse(item['text'])[2] == wording]
                    self.assertTrue(controls, (seed, kind, echoing[0]['text']))
                    checked += 1
        self.assertGreater(checked, 1000)

    def test_open_items_are_claimed_only_in_altered_reports_and_always_as_lures(self):
        """The design, measured. A claim about an open item exists only in an altered report, where it is
        always altered, undetermined and a lure. Stated claims are altered less often in a dossier with a
        lure than in a plain one, because two altered claims of a lure dossier are used up on the lures."""
        by_report, altered = collections.Counter(), collections.defaultdict(list)
        for seed in range(1, 1001):
            built = case(seed)
            reading = Reading(sources_of(built))
            has_lure = any(row['echo_state'] for row in built['key']['expectations'])
            for row in built['key']['expectations']:
                kind, ident = Reading.parse(row['claim_text'])[:2]
                if len(reading.actual[kind, ident]) == 2:
                    by_report[row['variant'], row['echo_state'] is not None, row['error_type'], row['semantic_state']] += 1
                elif row['variant'] == 'mixed':
                    altered[has_lure].append(row['error_type'] is not None)
        self.assertEqual(set(by_report), {('mixed', True, 'fills_in_default', 'undetermined')})
        self.assertEqual(by_report['mixed', True, 'fills_in_default', 'undetermined'], 1432)
        lure_rate, plain_rate = (sum(altered[flag]) / len(altered[flag]) for flag in (True, False))
        print('Defaults altered rate of stated claims: lure dossier %.3f, plain dossier %.3f' % (lure_rate, plain_rate))
        self.assertTrue(0.25 <= lure_rate <= 0.32, lure_rate)
        self.assertTrue(0.36 <= plain_rate <= 0.44, plain_rate)

    def test_written_suite_loads_seals_and_stages(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='defaults-tests-', dir=ROOT / 'tmp') as temporary:
            root = Path(temporary)
            fixtures, keys, arena, sealed = [root / name for name in ('fixtures', 'keys', 'arena', 'sealed')]
            summary = generate.write_suite(fixtures, keys, 9, 3, ['defaults'])
            loaded = scorecard.load_keys(keys)
            self.assertEqual(set(loaded), set(summary['dossiers']))
            self.assertEqual(summary['claims'], 72)
            arena.mkdir()
            blindness.seal_keys(keys, sealed, [arena])
            for dossier in summary['dossiers']:
                for variant, other in (('correct', 'mixed'), ('mixed', 'correct')):
                    record = blindness.stage_case(fixtures / dossier / ('report-' + variant + '.md'),
                        fixtures / dossier / 'sources', arena, root / 'records',
                        {'dossier': dossier, 'variant': variant},
                        withheld=[sealed, fixtures / dossier / ('report-' + other + '.md')])
                    run = Path(record['arena_run'])
                    self.assertEqual((run / 'case/report.md').read_bytes(),
                                     (fixtures / dossier / ('report-' + variant + '.md')).read_bytes())
                    self.assertEqual(sorted(p.name for p in (run / 'case/sources').iterdir()),
                                     sorted(p.name for p in (fixtures / dossier / 'sources').iterdir()))
                    self.assertNotIn(dossier, str(run))
            self.assertFalse(list(fixtures.rglob('*.json')))

    def test_restating_claims_is_clean_and_copied_rationale_is_detected(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='defaults-scan-', dir=ROOT / 'tmp') as temporary:
            run = Path(temporary)
            (run / 'case').mkdir()
            built = case(13)
            (run / 'case/report.md').write_text(built['files']['report-mixed.md'], encoding='utf-8')
            rows_ = built['key']['expectations']
            (run / 'notes.md').write_text('\n'.join(row['claim_text'] + ' Verdict: ' + row['semantic_state']
                                                  for row in rows_ if row['variant'] == 'mixed'), encoding='utf-8')
            self.assertEqual(blindness.scan_run(run, rows_)['verbatim_overlaps'], [])
            (run / 'copied.md').write_text(rows_[-1]['rationale'], encoding='utf-8')
            self.assertTrue(blindness.scan_run(run, rows_)['verbatim_overlaps'])


if __name__ == '__main__':
    unittest.main()
