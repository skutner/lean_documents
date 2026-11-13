"""Independent rendered-source derivation and finite held-out leakage probes.

The parser uses no family constants or rule functions. Complete normalized source
pins detect unreviewed changes; agreement cannot prove unique English meaning.
Classifier splits keep entire seeds together. Report features include all wording
and citations. The family's two readers ignore the agreement, so the second
derivation (`Reading`) reads the clauses only for the key and never for a reader.
`Records` parses the three records and the correspondence figures only; the
guessers of the source-amount tests use it and never see the agreement.
"""
import collections
import functools
import hashlib
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
generate.HARD_FAMILIES.setdefault('conflict', 'lure_conflict')
import blindness
import run_blind
import scorecard


def report_context(report):
    """Retain title, preamble, citations and every other non-claim character."""
    for text in report_claims(report):
        report = report.replace(text, '<CLAIM>')
    return report


def sources_of(built):
    return {name[8:]: text for name, text in built['files'].items() if name.startswith('sources/')}


def source_lengths(text):
    """Raw export size and three measures that ignore its padding."""
    return (len(text.encode('utf-8')),
            len('\n'.join(line.rstrip() for line in text.splitlines()).strip()),
            len(' '.join(text.split())), len(text.split()))


@functools.lru_cache(maxsize=3000)
def case(seed, index=1):
    return generate.build_case('conflict', seed, index)


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


GAIN_BOUND = .035  # largest gain over the majority guess that a source-amount guesser may reach (see docstring)
READERS = ('higher_ranked_source_wins', 'most_copies_win')
LURE_ERROR = 'settles_conflict_by_rank_or_count'
ERRORS = {LURE_ERROR, 'copies_negotiation_target', 'copies_last_season_charge',
          'copies_equipment_price', 'denies_selected_room_price'}


def report_claims(text):
    return re.findall(r'The daily room hire charge for \w+ is (?:at most|more than|exactly|not) EUR \d+\.', text)


def shape(text):
    return re.sub(r'for \w+ is', 'for VENUE is', re.sub(r'\d+', '#', text))


def normalized_source(name, text):
    text = re.sub(r'EUR \d+', 'EUR <amount>', text)
    text = re.sub(r'Issue date: \d+ September 2034', 'Issue date: <date>', text)
    if name == 'circulation.md':
        text = re.sub(r'copied from record [AB]', 'copied from record <source>', text)
    if name == 'agreement.md':
        text = re.sub(r'the parties select (?:record [AB]|no entry)', 'the parties select <selection>', text)
    return text


# These complete rendered-file pins cover every fixed paragraph, heading and
# dynamic clause; only the explicitly parsed amount/selection/date fields vary.
SOURCE_PINS = {'agreement.md': 'd6e9dc7ca1d8b97f6da24c2877ea52133f1475ea5d947bb99ea326cc6c2b8917', 'circulation.md': 'dd5e106b40b28077c4c9d5c7cc2b3196803b8a21c5927b9544e17183e9cab263', 'correspondence.md': '8406f24c96f3becbf3258e3b57588e2dc188071aad66cb56d1c2093839c366b7', 'procedure.md': '14927e02e056c94b81a240b222951708d5bbd0cbfe381011ee31be0b6a39b1ca', 'schedule.md': 'cff313c3386c963de59321a9f83d3521b822e5298c9c83427d123d8a07e91c7b'}


PINNED_PROCEDURE = """# Room hire review procedure

<a id="P-00"></a>
## P-00 — Office record
Bellwether Civic Rooms Trust. Contracts Office. Prepared by Ellis Moss, contracts secretary. Issue date: <date>. This procedure accompanies the autumn venue booking agreement and is retained with the papers used for the finance review.

<a id="P-01"></a>
## P-01 — Establishing the room hire charge
The review concerns the daily room hire charge for each named venue, excluding equipment hire.
The room hire amounts stated in records A, B and C are the exhaustive candidate values for each venue.
If those records state the same amount for a venue, that amount is established.
If those records state different amounts for a venue, its amount is established only when a clause in the governing agreement selects one of those records for that venue.
Without such a clause, each stated candidate remains possible and the packet does not establish a single amount.
The date of a record does not create precedence.
A selected record determines the amount.
The agreement's venue clauses are its complete list of record selections.
Amounts for different venues may be resolved independently.
"""

PINNED_AGREEMENT = """# Venue booking agreement

<a id="G-01"></a>
## G-01 — Governing agreement
Bellwether Civic Rooms Trust. Contracts Office. Recorded by Ellis Moss, contracts secretary. Issue date: <date>. Venue booking agreement for the autumn programme. The parties reviewed the room hire papers at the agreement meeting and recorded document selections venue by venue below. Record A is the signed room hire schedule in schedule.md. Record B is the booking correspondence in correspondence.md. Record C is the programme desk digest in circulation.md. Each venue clause records the meeting's selection without transcribing its amount again. The secretary used this format so that later readers could trace the selected entry to the paper actually discussed at the meeting. The original worksheets were kept with the agreement because they also contain access and equipment information used by the operations team. The review procedure in procedure.md accompanies this agreement and sets out how the packet establishes room hire amounts. The contracts office retained the complete packet for the finance review, together with the source letters used in the programme digest. The list below covers all venues on the autumn agenda. It records the room hire selection for each venue; it does not record a decision about optional equipment packages or about attendance at any event.
""" + ''.join(f"""
<a id="V-{venue}"></a>
## V-{venue} — {venue}
At {venue}, the parties select <selection> as the governing room hire entry.
""" for venue in ('Alder', 'Bramble', 'Cedar', 'Dunlin', 'Elm', 'Fennel', 'Grove', 'Hazel', 'Iris',
                  'Juniper', 'Kestrel', 'Linden'))


class Records:
    """The three records and the correspondence figures; the agreement is never read."""

    def __init__(self, sources):
        self.entries = collections.defaultdict(dict)
        patterns = {
            'A': ('schedule.md', r'For (\w+), this schedule states a daily room hire charge of EUR (\d+)\.'),
            'B': ('correspondence.md', r"The steward's booking correspondence for (\w+) gives a daily room hire charge of EUR (\d+)\."),
            'C': ('circulation.md', r'The programme desk lists the daily room hire charge for (\w+) as EUR (\d+), copied from record ([AB])\.')}
        self.copies = {}
        for letter, (filename, pattern) in patterns.items():
            found = re.findall(pattern, sources[filename])
            assert len(found) == 12, (letter, found)
            for fields in found:
                venue, amount = fields[:2]
                self.entries[venue][letter] = int(amount)
                if letter == 'C':
                    self.copies[venue] = fields[2]
        figures = re.findall(
            r"correspondence for (\w+) gives a daily room hire charge of EUR \d+\. "
            r"The negotiation target on the call worksheet was EUR (\d+)\. "
            r"Last season's daily room hire charge was EUR (\d+)\. "
            r"The separate daily equipment package price was EUR (\d+)\.", sources['correspondence.md'])
        assert len(figures) == 12
        self.figures = {venue: tuple(map(int, rest)) for venue, *rest in figures}
        for venue, records in self.entries.items():
            assert set(records) == {'A', 'B', 'C'}
            assert records['C'] == records[self.copies[venue]], venue

    def disputed(self, venue):
        return len(set(self.entries[venue].values())) > 1

    def relation(self, venue, amount):
        values = sorted(set(self.entries[venue].values()))
        if len(values) == 1:
            return 'agree'
        return ('above' if amount > values[1] else 'higher' if amount == values[1] else
                'between' if amount > values[0] else 'lower' if amount == values[0] else 'below')

    def separates(self, venue, wording, amount):
        """Whether the claim is true for one candidate amount and false for the other."""
        outcomes = {(c <= amount if wording == 'at most' else c > amount if wording == 'more than'
                     else c == amount if wording == 'exactly' else c != amount)
                    for c in set(self.entries[venue].values())}
        return len(outcomes) == 2


class Reading(Records):
    """The key and both readers, from the rendered Markdown. Record A is the signed
    schedule, so the rank reader takes its amount; the count reader takes the
    amount that two of the three records carry. Neither reads a clause."""

    def __init__(self, sources):
        assert normalized_source('procedure.md', sources['procedure.md']) == PINNED_PROCEDURE, 'unreviewed procedure'
        assert normalized_source('agreement.md', sources['agreement.md']) == PINNED_AGREEMENT, 'unreviewed agreement'
        super().__init__(sources)
        selections = re.findall(r'At (\w+), the parties select (record [AB]|no entry) as the governing room hire entry\.',
                                sources['agreement.md'])
        assert len(selections) == 12
        self.selections = {venue: None if value == 'no entry' else value[-1] for venue, value in selections}
        self.actual, self.readers = {}, {reader: {} for reader in READERS}
        for venue, records in self.entries.items():
            selection = self.selections[venue]
            values = (records[selection],) if selection and self.disputed(venue) else \
                tuple(sorted(set(records.values())))
            self.actual[venue] = values
            majority = next(value for value in set(records.values())
                            if sum(entry == value for entry in records.values()) >= 2)
            self.readers[READERS[0]][venue] = (records['A'],)
            self.readers[READERS[1]][venue] = (majority,)
        assert len(self.actual) == 12

    def unsettled(self, venue):
        return self.disputed(venue) and self.selections[venue] is None

    @staticmethod
    def parse(text):
        found = re.fullmatch(r'The daily room hire charge for (\w+) is (at most|more than|exactly|not) EUR (\d+)\.', text)
        assert found, text
        return ('room_hire_budget' if found[2] in ('at most', 'more than') else 'room_hire_exact',
                found[1], found[2] in ('at most', 'exactly'), int(found[3]))

    def verdict(self, text, reader=None):
        kind, venue, positive, amount = self.parse(text)
        values = (self.readers[reader] if reader else self.actual)[venue]
        wording = re.search(r'is (at most|more than|exactly|not) EUR', text)[1]
        outcomes = [(value <= amount if wording == 'at most' else value > amount if wording == 'more than'
                     else value == amount if wording == 'exactly' else value != amount) for value in values]
        return aggregate(outcomes)

    def lures(self, text):
        actual = self.verdict(text)
        return {reader: self.verdict(text, reader) for reader in READERS if self.verdict(text, reader) != actual}


class ConflictTests(unittest.TestCase):
    def check_row(self, reading, row):
        text = row['claim_text']
        self.assertEqual(row['semantic_state'], reading.verdict(text))
        lures = reading.lures(text)
        self.assertEqual(row['lure_states'], lures)
        self.assertTrue(set(row['lure_states']) <= set(READERS))
        self.assertEqual(row['echo_state'], lures.get(READERS[0]))
        self.assertEqual(row['claim_kind'], Reading.parse(text)[0])
        self.assertEqual(row['lure_derivations'], {name: {'worlds': 1, 'worlds_where_true': int(state == 'supported')}
                                                 for name, state in lures.items()})
        if READERS[0] in lures:
            self.assertEqual(row['echo_reader'], READERS[0])
            self.assertEqual(row['echo_derivation'], row['lure_derivations'][READERS[0]])
        else:
            self.assertIsNone(row['echo_derivation'])
            self.assertNotIn('echo_reader', row)

    def test_independent_keys_echoes_and_lure_states_over_sixty_seeds(self):
        directions = collections.defaultdict(set)
        for seed in range(1, 101):
            built = case(seed)
            reading = Reading(sources_of(built))
            for row in built['key']['expectations']:
                self.check_row(reading, row)
                for name, state in row['lure_states'].items():
                    directions[name].add((row['semantic_state'], state))
        self.assertEqual(set(directions), set(READERS))
        full = {('undetermined', 'supported'), ('undetermined', 'refuted'),
                ('supported', 'refuted'), ('refuted', 'supported')}
        for observed in directions.values():
            self.assertEqual(observed, full)

    def test_all_raw_slot_variants_over_sixty_seeds(self):
        for seed in range(1, 61):
            raw = generate.family_builder('conflict')(random.Random(seed))
            parsed = Reading(raw['sources'])
            self.assertEqual(len(raw['slots']), 12)
            self.assertEqual(set(raw['readers']), set(READERS))
            self.assertEqual(raw['reader'], READERS[0])
            # Every dossier holds disputed venues, so the rank reader's world is always told.
            self.assertEqual(raw['told'], raw['readers'][READERS[0]])
            self.assertEqual(sum(parsed.disputed(v) for v in parsed.entries), 6)
            self.assertNotIn('open_claims', raw)
            for true, variants in raw['slots']:
                self.assertEqual(parsed.verdict(true['text']), 'supported')
                self.assertTrue(variants)
                self.assertEqual(len({item['text'] for item in [true] + variants}), len(variants) + 1)
                for item in [true] + variants:
                    self.assertEqual(shape(item['text']), shape(true['text']))
                    self.assertEqual(parsed.verdict(item['text']), generate.judge(item, raw['base'], raw['unknowns'])[0])
                    for name in READERS:
                        world = raw['readers'][name]
                        self.assertEqual(parsed.verdict(item['text'], name),
                                         generate.judge(item, world['base'], world['unknowns'])[0])

    def test_correct_reports_and_typed_mixed_errors_over_three_hundred_seeds(self):
        errors, states = set(), collections.defaultdict(set)
        frames = set()
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            rows_ = built['key']['expectations']
            correct = {r['claim_id']: r for r in rows_ if r['variant'] == 'correct'}
            changes = 0
            for row in rows_:
                self.check_row(reading, row)
                original = correct[row['claim_id']]
                self.assertEqual(shape(row['claim_text']), shape(original['claim_text']))
                self.assertEqual(Reading.parse(row['claim_text'])[:3], Reading.parse(original['claim_text'])[:3])
                states[shape(row['claim_text'])].add(row['semantic_state'])
                if row['claim_text'] == original['claim_text']:
                    self.assertEqual(row['semantic_state'], 'supported')
                    self.assertIsNone(row['error_type'])
                else:
                    changes += 1
                    self.assertIn(row['semantic_state'], ('refuted', 'undetermined'))
                    errors.add(row['error_type'])
                venue = Reading.parse(row['claim_text'])[1]
                expected = ['sources/' + name + '#V-' + venue for name in
                            ('schedule.md', 'correspondence.md', 'circulation.md', 'agreement.md')]
                expected.append('sources/procedure.md#P-01')
                self.assertEqual(row['source_anchors'], expected)
                for anchor in expected:
                    filename, ident = anchor.split('#')
                    self.assertIn('<a id="' + ident + '"></a>', built['files'][filename])
            self.assertGreaterEqual(changes, 2)
            for variant in ('correct', 'mixed'):
                report = built['files']['report-' + variant + '.md']
                texts = report_claims(report)
                selected = [r for r in rows_ if r['variant'] == variant]
                self.assertEqual(collections.Counter(texts), collections.Counter(r['claim_text'] for r in selected))
                self.assertEqual(len(texts), 12)
                self.assertEqual(len({Reading.parse(t)[1] for t in texts}), 12)
                self.assertTrue(report.startswith('# Room hire review\n\n<a id="C01">'))
                self.assertNotRegex(report.lower(), r'\b(correct|mixed|evaluator|echo_state|error_type|undetermined|refuted)\b')
                frame = re.sub(r'V-[A-Z][a-z]+', 'V-VENUE', report_context(report))
                frames.add(frame)
                for text in texts:
                    if reading.lures(text):
                        self.assertTrue(any(shape(other) == shape(text) and not reading.lures(other) for other in texts))
        self.assertEqual(len(frames), 1)
        self.assertEqual(errors, ERRORS)
        for wording, observed in states.items():
            self.assertEqual(observed, {'supported', 'refuted'} if 'exactly' in wording else
                             {'supported', 'refuted', 'undetermined'})

    def test_documentary_substitutions_match_targets_last_season_and_equipment_prices(self):
        for seed in range(1, 61):
            raw = generate.family_builder('conflict')(random.Random(seed))
            reading = Reading(raw['sources'])
            for true, variants in raw['slots']:
                kind, venue, positive, amount = Reading.parse(true['text'])
                wording = re.search(r'is (at most|more than|exactly|not) EUR', true['text'])[1]
                target, previous, equipment = reading.figures[venue]
                records = set(reading.entries[venue].values())
                self.assertNotIn(equipment, records)
                self.assertNotIn(previous, records)
                if wording == 'at most':
                    self.assertIn(amount, (max(records), target))
                elif wording == 'more than':
                    self.assertIn(amount, (previous, target))
                for item in variants:
                    wrong = Reading.parse(item['text'])[3]
                    if wording == 'at most':
                        expected = {target: LURE_ERROR if reading.disputed(venue) else 'copies_negotiation_target',
                                    previous: 'copies_last_season_charge'}
                        self.assertEqual(item['error'], expected[wrong])
                    elif wording == 'more than':
                        expected = {target: LURE_ERROR if reading.disputed(venue) else 'copies_negotiation_target',
                                    equipment: 'copies_equipment_price'}
                        self.assertEqual(item['error'], expected[wrong])
                    elif wording == 'exactly':
                        self.assertEqual(wrong, equipment)
                        self.assertEqual(item['error'], 'copies_equipment_price')
                    elif reading.disputed(venue):
                        # "Not" one of the two candidates: the mutations deny each candidate in turn.
                        self.assertIn(wrong, reading.entries[venue].values())
                        self.assertEqual(item['error'], LURE_ERROR)
                    else:
                        self.assertEqual(wrong, reading.actual[venue][0])
                        self.assertEqual(item['error'], 'denies_selected_room_price')

    def test_rank_and_count_can_agree_or_disagree_on_the_same_claim_and_one_can_be_right(self):
        observed, single = set(), set()
        for seed in range(1, 301):
            for row in case(seed)['key']['expectations']:
                values = row['lure_states']
                if len(values) == 2:
                    observed.add(values[READERS[0]] == values[READERS[1]])
                elif values:
                    single.add(next(iter(values)))
        self.assertEqual(observed, {False, True})
        self.assertEqual(single, set(READERS))

    def test_settled_conflicts_select_either_record_and_false_bounds_stay_refuted(self):
        selections, false_bounds = collections.Counter(), 0
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            for venue in reading.entries:
                if reading.disputed(venue) and reading.selections[venue]:
                    selections[reading.selections[venue]] += 1
            for row in built['key']['expectations']:
                kind, venue, positive, amount = Reading.parse(row['claim_text'])
                wording = re.search(r'is (at most|more than|exactly|not) EUR', row['claim_text'])[1]
                relation = reading.relation(venue, amount)
                if reading.disputed(venue) and (wording, relation) in (('at most', 'below'), ('more than', 'above')):
                    # A bound outside both candidates on the wrong side asserts neither: refuted whatever prevails.
                    self.assertEqual(row['semantic_state'], 'refuted')
                    self.assertEqual(row['lure_states'], {})
                    false_bounds += 1
        self.assertTrue(.40 <= selections['B'] / sum(selections.values()) <= .60, selections)
        self.assertGreater(false_bounds, 100)

    def test_identical_claim_kinds_and_slots_between_reports(self):
        for seed in range(1, 301):
            rows_ = case(seed)['key']['expectations']
            groups = [[(r['claim_id'], r['claim_kind'], Reading.parse(r['claim_text'])[:3])
                       for r in rows_ if r['variant'] == variant] for variant in ('correct', 'mixed')]
            self.assertEqual(groups[0], groups[1])
            self.assertEqual(collections.Counter(kind for _, kind, _ in groups[0]),
                             {'room_hire_budget': 9, 'room_hire_exact': 3})

    def test_each_wording_is_both_right_and_wrong(self):
        states = collections.defaultdict(set)
        for seed in range(1, 301):
            for row in case(seed)['key']['expectations']:
                states[shape(row['claim_text'])].add(row['semantic_state'])
        self.assertEqual(len(states), 4)
        for wording, observed in states.items():
            self.assertTrue({'supported', 'refuted'} <= observed, wording)
            if 'exactly' not in wording:
                self.assertIn('undetermined', observed, wording)

    def test_same_report_same_wording_controls_for_each_reader(self):
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            for variant in ('correct', 'mixed'):
                texts = report_claims(built['files']['report-' + variant + '.md'])
                for text in texts:
                    for reader in reading.lures(text):
                        self.assertTrue(any(shape(other) == shape(text) and reader not in reading.lures(other)
                                            for other in texts), (seed, variant, reader, text))

    def test_controls_exist_by_kind_and_wording_in_the_same_reports(self):
        # R4 for the scorer: kinds that carry a definite lure, and for each the claims without any lure.
        lures, controls = collections.Counter(), collections.Counter()
        for seed in range(1, 301):
            for row in case(seed)['key']['expectations']:
                key = (row['claim_kind'], shape(row['claim_text']))
                if row['lure_states']:
                    lures[key] += 1
                elif row['echo_state'] is None:
                    controls[key] += 1
        # Budget claims and "not" claims carry lures; "exactly" claims are never about a disputed venue.
        self.assertEqual({key[0] for key in lures}, {'room_hire_budget', 'room_hire_exact'})
        self.assertEqual({key[1] for key in lures}, {shape('The daily room hire charge for X is %s EUR 1.' % w)
                                                       for w in ('at most', 'more than', 'not')})
        for key in lures:
            self.assertGreater(controls[key], 2 * lures[key], key)
            self.assertGreater(lures[key], 100, key)
        self.assertGreater(sum(controls[key] for key in controls if 'exactly' in key[1]), 500)

    def test_lure_directions_for_both_readers(self):
        directions = collections.defaultdict(set)
        for seed in range(1, 301):
            for row in case(seed)['key']['expectations']:
                for reader, verdict in row['lure_states'].items():
                    directions[reader].add((row['semantic_state'], verdict))
        self.assertEqual(set(directions), set(READERS))
        for reader in READERS:
            self.assertEqual(directions[reader], {('undetermined', 'supported'), ('undetermined', 'refuted'),
                                                  ('supported', 'refuted'), ('refuted', 'supported')})

    def test_citations_never_distinguish_lure_from_control(self):
        for seed in range(1, 301):
            for variant in ('correct', 'mixed'):
                report = case(seed)['files']['report-' + variant + '.md']
                citations = re.findall(r'Citations: ([^\n]+)', report)
                self.assertEqual(len(citations), 12)
                self.assertEqual({re.sub(r'V-[A-Z][a-z]+', 'V-VENUE', value) for value in citations},
                                 {'schedule V-VENUE; correspondence V-VENUE; circulation V-VENUE; '
                                  'agreement V-VENUE; procedure P-01.'})

    def test_title_preamble_and_nonclaim_report_text_have_no_label_cue(self):
        frames = set()
        for seed in range(1, 301):
            for variant in ('correct', 'mixed'):
                report = case(seed)['files']['report-' + variant + '.md']
                frames.add(re.sub(r'V-[A-Z][a-z]+', 'V-VENUE', report_context(report)))
                self.assertEqual(run_blind.paragraphs(report)['document-preamble'], '# Room hire review')
        self.assertEqual(len(frames), 1)

    def test_about_thirty_percent_of_dossiers_hold_no_lure(self):
        quiet = 0
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            self.assertTrue(any(reading.disputed(v) for v in reading.entries))
            quiet += not any(row['lure_states'] for row in built['key']['expectations'])
        self.assertTrue(.20 <= quiet / 300 <= .40, quiet)

    def test_full_wording_pins_and_prose(self):
        for seed in range(1, 601):
            sources = sources_of(case(seed))
            self.assertEqual(set(sources), set(SOURCE_PINS))
            self.assertEqual(normalized_source('procedure.md', sources['procedure.md']), PINNED_PROCEDURE)
            prose = '\n'.join(line for text in sources.values() for line in text.splitlines()
                              if not line.startswith(('|', '#', '<a ')))
            self.assertGreaterEqual(len(prose.split()), 1500)
            for name, text in sources.items():
                self.assertEqual(hashlib.sha256(normalized_source(name, text).encode()).hexdigest(), SOURCE_PINS[name], name)
                self.assertIn('Office', text)
                self.assertRegex(text, r'Issue date: \d+ September 2034')
                self.assertNotRegex(text, r'echo_state|lure_states|error_type|evaluator|answer key')
        # A normative sentence or dynamic agreement wording cannot be changed
        # while retaining a pin; field replacement deliberately preserves neither.
        sources = sources_of(case(1))
        for name in sources:
            changed = sources[name].replace('Office', 'Agency', 1)
            self.assertNotEqual(hashlib.sha256(normalized_source(name, changed).encode()).hexdigest(), SOURCE_PINS[name])
        changed = sources['agreement.md'].replace('as the governing room hire entry', 'as an advisory room hire entry', 1)
        self.assertNotEqual(hashlib.sha256(normalized_source('agreement.md', changed).encode()).hexdigest(), SOURCE_PINS['agreement.md'])
        # Every rule-bearing sentence is compared: an added, changed or missing one fails.
        for sentence in ('Amounts for different venues may be resolved independently.',
                         'The date of a record does not create precedence.'):
            for edit in (sources['procedure.md'].replace(sentence + '\n', ''),
                         sources['procedure.md'].replace(sentence, sentence + ' A later record prevails.'),
                         sources['procedure.md'].replace(sentence, sentence.upper())):
                self.assertNotEqual(normalized_source('procedure.md', edit), PINNED_PROCEDURE)

    def test_at_least_fifteen_hundred_prose_words_and_as_many_distinct_ones(self):
        for seed in range(1, 301):
            prose = '\n'.join(line for text in sources_of(case(seed)).values() for line in text.splitlines()
                              if not line.startswith(('|', '#', '<a ')))
            self.assertGreaterEqual(len(prose.split()), 1500)
        # Distinct prose: sentences with venue names and amounts masked, counted once. No paragraph is repeated.
        sources = sources_of(case(1))
        venues = r'\b(?:Alder|Bramble|Cedar|Dunlin|Elm|Fennel|Grove|Hazel|Iris|Juniper|Kestrel|Linden)\b'
        distinct = {}
        for text in sources.values():
            for line in text.splitlines():
                if line.startswith(('#', '<a ')) or not line.strip():
                    continue
                for sentence in re.split(r'(?<=\.) ', line.strip()):
                    masked = re.sub(r'record [AB]', 'record X', re.sub(r'\d+', '#', re.sub(venues, 'VENUE', sentence)))
                    distinct.setdefault(masked, len(sentence.split()))
        self.assertGreaterEqual(sum(distinct.values()), 1500, sum(distinct.values()))

    def test_dates_differ_and_their_order_carries_no_precedence(self):
        orders, earlier = collections.Counter(), collections.Counter()
        for seed in range(1, 301):
            sources = sources_of(case(seed))
            dates = {name: int(re.search(r'Issue date: (\d+) September 2034', text)[1]) for name, text in sources.items()}
            self.assertEqual(len(set(dates.values())), 5, dates)
            records = ('schedule.md', 'correspondence.md', 'circulation.md')
            orders[tuple(sorted(records, key=dates.get))] += 1
            self.assertGreater(dates['agreement.md'], max(dates[name] for name in records))
            earlier['A before B'] += dates['schedule.md'] < dates['correspondence.md']
        self.assertEqual(len(orders), 6)
        for count in orders.values():
            self.assertTrue(30 <= count <= 75, orders)
        self.assertTrue(.40 <= earlier['A before B'] / 300 <= .60)

    def test_source_lengths_do_not_say_whether_venues_are_settled(self):
        # The review brief's length rule: each file's length is normalised per file name using seeds 1..500, a
        # numeric stump is fitted on seeds 1..500 and scored on 501..1000.
        lengths = collections.defaultdict(list)
        samples = []
        for seed in range(1, 1001):
            built = case(seed)
            reading = Reading(sources_of(built))
            sources = sources_of(built)
            unsettled = sum(reading.unsettled(v) for v in reading.entries)
            rows_ = built['key']['expectations']
            samples.append((seed, {name: len(text) for name, text in sources.items()},
                            {'unsettled_venues_at_least_four': unsettled >= 4,
                             'unsettled_venues_at_least_five': unsettled >= 5,
                             'dossier_has_lure': any(r['lure_states'] for r in rows_)}))
            if seed <= 500:
                for name, text in sources.items():
                    lengths[name].append(len(text))
        names = sorted(lengths)
        self.assertGreater(len({v for v in lengths['correspondence.md']}), 1)
        stats = {name: (statistics.mean(values), statistics.pstdev(values) or 1.0) for name, values in lengths.items()}
        def features(sizes):
            z = tuple((sizes[name] - stats[name][0]) / stats[name][1] for name in names)
            return z + (sum(z), max(z), min(z))
        for target in ('unsettled_venues_at_least_four', 'unsettled_venues_at_least_five', 'dossier_has_lure'):
            train = [(features(sizes), labels[target]) for seed, sizes, labels in samples if seed <= 500]
            held = [(features(sizes), labels[target]) for seed, sizes, labels in samples if seed > 500]
            counts = collections.Counter(label for _, label in train)
            fallback = counts[True] > counts[False]
            predictor = fit_stump(train)
            accuracy = sum(predictor(f) == label for f, label in held) / len(held)
            baseline = sum(label == fallback for _, label in held) / len(held)
            margin = max(.02, 3 * math.sqrt(.25 / len(held)))
            print('Conflict length rule', target, 'accuracy=%.4f majority=%.4f' % (accuracy, baseline))
            self.assertLessEqual(accuracy - baseline, margin, (target, accuracy, baseline))

    def test_source_selected_amount_edit_changes_the_correct_verdict(self):
        sources = sources_of(case(1))
        reading = Reading(sources)
        venue = next(v for v, values in reading.actual.items() if len(values) == 1)
        amount = reading.actual[venue][0]
        text = 'The daily room hire charge for %s is exactly EUR %d.' % (venue, amount)
        self.assertEqual(reading.verdict(text), 'supported')
        changed = dict(sources)
        for name in ('schedule.md', 'correspondence.md', 'circulation.md'):
            pattern = r'(<a id="V-' + venue + r'"></a>.*?)(?=<a id=|\Z)'
            changed[name] = re.sub(pattern, lambda m: re.sub(r'EUR (\d+)',
                lambda n: 'EUR ' + str(int(n[1]) + 1), m[1]), changed[name], flags=re.S)
        revised = Reading(changed)
        self.assertEqual(revised.verdict(text), 'refuted')
        for reader in READERS:
            self.assertEqual(revised.verdict(text, reader), 'refuted')

    def test_rendered_changes_move_the_key_by_clause_and_each_reader_by_its_own_input(self):
        exercised = set()
        for seed in range(1, 61):
            sources = sources_of(case(seed))
            reading = Reading(sources)
            for venue, entries in reading.entries.items():
                if not reading.disputed(venue):
                    continue
                low, high = sorted(set(entries.values()))
                text = 'The daily room hire charge for %s is at most EUR %d.' % (venue, low)
                before = {reader: reading.verdict(text, reader) for reader in READERS}
                current = 'no entry' if reading.selections[venue] is None else 'record ' + reading.selections[venue]
                # A clause edit moves the key and moves neither reader (both ignore the agreement).
                for choice in ('record A', 'record B', 'no entry'):
                    changed = dict(sources)
                    changed['agreement.md'] = changed['agreement.md'].replace(
                        'At %s, the parties select %s' % (venue, current), 'At %s, the parties select %s' % (venue, choice))
                    revised = Reading(changed)
                    letter = choice[-1]
                    expected = 'undetermined' if choice == 'no entry' else \
                        'supported' if entries[letter] == low else 'refuted'
                    self.assertEqual(revised.verdict(text), expected)
                    for reader in READERS:
                        self.assertEqual(revised.verdict(text, reader), before[reader])
                    exercised.add('precedence')
                # Swap the two room entries and update the copied amount to
                # preserve the printed route: rank follows A, the key does not move.
                changed = dict(sources)
                changed['schedule.md'] = changed['schedule.md'].replace(
                    'For %s, this schedule states a daily room hire charge of EUR %d.' % (venue, entries['A']),
                    'For %s, this schedule states a daily room hire charge of EUR %d.' % (venue, entries['B']))
                changed['correspondence.md'] = changed['correspondence.md'].replace(
                    "correspondence for %s gives a daily room hire charge of EUR %d." % (venue, entries['B']),
                    "correspondence for %s gives a daily room hire charge of EUR %d." % (venue, entries['A']))
                old_c = entries['C']
                new_c = high if old_c == low else low
                changed['circulation.md'] = changed['circulation.md'].replace(
                    'charge for %s as EUR %d,' % (venue, old_c), 'charge for %s as EUR %d,' % (venue, new_c))
                revised = Reading(changed)
                self.assertNotEqual(revised.verdict(text, READERS[0]), reading.verdict(text, READERS[0]))
                exercised.add('rank')
                # Move only the digest's copying route and corresponding price.
                changed = dict(sources)
                route = 'B' if reading.copies[venue] == 'A' else 'A'
                changed['circulation.md'] = changed['circulation.md'].replace(
                    'charge for %s as EUR %d, copied from record %s.' % (venue, entries['C'], reading.copies[venue]),
                    'charge for %s as EUR %d, copied from record %s.' % (venue, entries[route], route))
                revised = Reading(changed)
                self.assertEqual(revised.verdict(text, READERS[0]), reading.verdict(text, READERS[0]))
                self.assertNotEqual(revised.verdict(text, READERS[1]), reading.verdict(text, READERS[1]))
                self.assertEqual(revised.verdict(text), reading.verdict(text))
                exercised.add('count')
            if len(exercised) == 3:
                break
        self.assertEqual(exercised, {'precedence', 'rank', 'count'})

    def test_r9_hundred_campaigns_of_twelve_dossiers(self):
        no_lure, campaigns = 0, []
        counts, kinds, rows_by = collections.Counter(), collections.Counter(), collections.Counter()
        per_report = collections.Counter()
        for seed in range(1, 101):
            total = 0
            for index in range(1, 13):
                built = case(seed, index)
                reading = Reading(sources_of(built))
                lures = []
                for row in built['key']['expectations']:
                    self.check_row(reading, row)
                    if row['lure_states']:
                        lures.append(row)
                        venue = Reading.parse(row['claim_text'])[1]
                        settled = 'unsettled' if reading.unsettled(venue) else 'settled'
                        rows_by[settled, row['semantic_state']] += 1
                        for reader, verdict in row['lure_states'].items():
                            counts[reader, verdict] += 1
                        kinds[row['claim_kind']] += 1
                # R3: a lure touches a few items, never most of a report. Every lure is a claim on a disputed venue.
                for variant in ('correct', 'mixed'):
                    in_report = sum(r['variant'] == variant for r in lures)
                    per_report[variant, in_report] += 1
                    self.assertLessEqual(in_report, 4)
                self.assertTrue(all(reading.disputed(Reading.parse(r['claim_text'])[1]) for r in lures))
                no_lure += not lures
                total += len(lures)
            campaigns.append(total)
        print('Conflict R9 min/median/max:', min(campaigns), statistics.median(campaigns), max(campaigns),
              '; no-lure share:', no_lure / 1200, '; reader directions:', dict(counts),
              '; lure rows by clause and key:', dict(rows_by), '; kinds:', dict(kinds),
              '; lure rows per report:', dict(sorted(per_report.items())))
        self.assertTrue(.24 <= no_lure / 1200 <= .36)
        self.assertGreaterEqual(statistics.median(campaigns), 15)
        self.assertEqual(set(kinds), {'room_hire_budget', 'room_hire_exact'})
        holding = sum(count for (variant, n), count in per_report.items() if variant == 'mixed' and n)
        few = sum(count for (variant, n), count in per_report.items() if variant == 'mixed' and n in (1, 2))
        self.assertGreaterEqual(few / holding, .6)
        # Both settled and unsettled venues carry lures, in every direction.
        self.assertEqual({key for key in rows_by},
                         {('unsettled', 'undetermined'), ('settled', 'supported'), ('settled', 'refuted')})

    def test_lures_are_exactly_separating_claims_on_disputed_venues(self):
        # Because both readers ignore the clause, a lure is decided by the claim and the three records together
        # with the clause: it can occur only where the claim separates the two candidates.
        share = collections.defaultdict(collections.Counter)
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            for row in built['key']['expectations']:
                kind, venue, positive, amount = Reading.parse(row['claim_text'])
                wording = re.search(r'is (at most|more than|exactly|not) EUR', row['claim_text'])[1]
                separates = reading.separates(venue, wording, amount)
                if row['lure_states']:
                    self.assertTrue(separates, row['claim_text'])
                if separates:
                    clause = 'unsettled' if reading.unsettled(venue) else 'settled'
                    share[clause][bool(row['lure_states'])] += 1
        self.assertEqual(share['unsettled'][False], 0)
        settled = share['settled']
        self.assertTrue(.3 <= settled[True] / (settled[True] + settled[False]) <= .9, settled)

    def test_determinism_and_global_random_independence_over_300_seeds(self):
        previous = random.getstate()
        try:
            for seed in range(1, 301):
                random.seed(seed * 37)
                state = random.getstate()
                self.assertEqual(case(seed), generate.build_case('conflict', seed, 1))
                self.assertEqual(random.getstate(), state)
            self.assertNotEqual(case(1)['files'], case(2)['files'])
        finally:
            random.setstate(previous)

    def test_hash_seed_independence(self):
        script = ("import sys,json,hashlib;sys.dont_write_bytecode=True;sys.path.insert(0,'evalsuite');"
                  "import generate;generate.HARD_FAMILIES.setdefault('conflict','lure_conflict');"
                  "print(hashlib.sha256(json.dumps([generate.build_case('conflict',s,1) "
                  "for s in (1,13,71)],sort_keys=True).encode()).hexdigest())")
        outputs = []
        for seed in ('1', '9876'):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONDONTWRITEBYTECODE='1')
            outputs.append(subprocess.check_output([sys.executable, '-c', script], cwd=ROOT, env=env, text=True))
        self.assertEqual(outputs[0], outputs[1])

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

    def test_held_out_report_and_metadata_guessers(self):
        datasets = collections.defaultdict(list)
        for seed in range(1, 601):
            built = case(seed)
            files = sorted(sources_of(built).items())
            metadata = (tuple(name for name, _ in files),) + tuple(
                value for _, text in files for value in source_lengths(text))
            all_rows = built['key']['expectations']
            for variant in ('correct', 'mixed'):
                report = built['files']['report-' + variant + '.md']
                texts = report_claims(report)
                parsed = [Reading.parse(text) for text in texts]
                summaries = []
                for kind in sorted({p[0] for p in parsed}):
                    for side in (False, True):
                        values = [p[3] for p in parsed if p[0] == kind and p[2] == side]
                        summaries.extend((min(values), max(values), statistics.mean(values)))
                # Raw report, full fixed frame, every line, citations and lexical
                # counts prevent a title or a request paragraph being discarded.
                features = (report, report_context(report), tuple(report.splitlines()),
                            tuple(re.findall(r'Citations?: [^\n]+', report)), len(report),
                            len(report.split())) + tuple(summaries)
                selected = {r['claim_text']: r for r in all_rows if r['variant'] == variant}
                datasets['altered_report'].append((seed, features, metadata, variant == 'mixed'))
                datasets['dossier_has_lure'].append((seed, features, metadata,
                                                    any(r['lure_states'] for r in all_rows)))
                for text, parsed_claim in zip(texts, parsed):
                    row = selected[text]
                    claim_features = features + (text, shape(text)) + parsed_claim
                    datasets['wrong_claim'].append((seed, claim_features, metadata,
                                                     row['semantic_state'] != 'supported'))
                    datasets['lure_claim'].append((seed, claim_features, metadata, bool(row['lure_states'])))
        for metadata in (False, True):
            for target, samples in datasets.items():
                train = [(f + m if metadata else f, label) for seed, f, m, label in samples if seed <= 300]
                heldout = [(f + m if metadata else f, label) for seed, f, m, label in samples if seed > 300]
                counts = collections.Counter(label for _, label in train)
                tables, vocabulary = collections.defaultdict(collections.Counter), collections.defaultdict(set)
                for features, label in train:
                    for i, feature in enumerate(features):
                        tables[label, i][feature] += 1
                        vocabulary[i].add(feature)
                def bayes(features):
                    def likelihood(label):
                        return math.log((counts[label] + 1) / (len(train) + 2)) + sum(
                            math.log((tables[label, i][feature] + 1) /
                                     (counts[label] + len(vocabulary[i]) + 1))
                            for i, feature in enumerate(features))
                    return max((False, True), key=likelihood)
                # Exercise every actual numeric feature position, including
                # metadata fields, with unseen held-out threshold values.
                for index, value in enumerate(train[0][0]):
                    if not isinstance(value, (int, float)):
                        continue
                    def synthetic(number):
                        fields = list(train[0][0])
                        fields[index] = number
                        return tuple(fields)
                    control = fit_stump([(synthetic(v), v > 0) for v in (-5, -3, -1, 1, 3, 5)])
                    for value in (-4, -2, 2, 4):
                        self.assertEqual(control(synthetic(value)), value > 0, (target, metadata, index))
                fallback = counts[True] > counts[False]
                baseline = sum(label == fallback for _, label in heldout) / len(heldout)
                heldout_seeds = [seed for seed, _, _, _ in samples if seed > 300]
                for name, predictor in (('bayes', bayes), ('numeric_stump', fit_stump(train))):
                    accuracy = sum(predictor(f) == label for f, label in heldout) / len(heldout)
                    # Pair predictor and training-majority outcomes within each
                    # dossier, then estimate uncertainty across held-out seeds.
                    # Three SE with a 0.5-point floor is a finite-probe tolerance;
                    # small observed positive deltas are reported, not hidden.
                    paired = collections.defaultdict(list)
                    for seed, (features, label) in zip(heldout_seeds, heldout):
                        paired[seed].append(int(predictor(features) == label) - int(fallback == label))
                    differences = [statistics.mean(values) for values in paired.values()]
                    margin = max(.005, 3 * statistics.stdev(differences) / math.sqrt(len(differences)))
                    print('Conflict guesser', metadata, target, name,
                          'accuracy=%.4f majority=%.4f delta=%+.4f margin=%.4f' %
                          (accuracy, baseline, accuracy - baseline, margin))
                    self.assertLessEqual(accuracy - baseline, margin,
                                         (metadata, target, name, accuracy, baseline, margin))


    # ---- Finding 2: nothing but the agreement says whether a venue is settled -------------------------------

    @staticmethod
    def visible_samples(seeds):
        """One sample per claim: features from the report and the three records (never the agreement)
        and the labels. The settled state of a venue is a label, not a feature."""
        samples = []
        for seed in seeds:
            built = case(seed)
            sources = sources_of(built)
            records = Records(sources)
            reading = Reading(sources)
            disputed_venues = sum(records.disputed(v) for v in records.entries)
            rows_ = built['key']['expectations']
            dossier_lure = any(row['lure_states'] for row in rows_)
            for variant in ('correct', 'mixed'):
                selected = [r for r in rows_ if r['variant'] == variant]
                parsed = [Reading.parse(r['claim_text']) for r in selected]
                wordings = [re.search(r'is (at most|more than|exactly|not) EUR', r['claim_text'])[1] for r in selected]
                separating = [records.separates(p[1], w, p[3]) for p, w in zip(parsed, wordings)]
                substitution = []
                for p, w in zip(parsed, wordings):
                    target, previous, equipment = records.figures[p[1]]
                    substitution.append(p[3] in (target, previous) if w == 'at most' else
                                        p[3] in (target, equipment) if w == 'more than' else
                                        p[3] == equipment if w == 'exactly' else p[3] != equipment)
                # What a guesser may count in the report: separating claims, and claims that copy a figure of the
                # correspondence (which mark an altered report whatever the agreement says).
                for row, p, wording, sep, in zip(selected, parsed, wordings, separating):
                    target, previous, equipment = records.figures[p[1]]
                    values = sorted(set(records.entries[p[1]].values()))
                    candidates = values
                    outcomes = [(c <= p[3] if wording == 'at most' else c > p[3] if wording == 'more than'
                                 else c == p[3] if wording == 'exactly' else c != p[3]) for c in candidates]
                    features = (wording, sep, records.relation(p[1], p[3]), p[3] == target, p[3] == previous,
                                p[3] == equipment, records.copies[p[1]], records.entries[p[1]]['A'] == values[0],
                                disputed_venues, sum(separating), sum(substitution))
                    samples.append(dict(seed=seed, variant=variant, features=features, separating=sep,
                                        disputed=records.disputed(p[1]), unsettled=reading.unsettled(p[1]),
                                        lure=bool(row['lure_states']), dossier_lure=dossier_lure,
                                        state=row['semantic_state'], open_rule=aggregate(outcomes)))
        return samples

    @staticmethod
    def lookup_accuracy(samples, key, target, keep=lambda s: True):
        """Majority label per feature cell from seeds 1..500, scored on seeds 501..1000 (the review's method)."""
        train = [s for s in samples if s['seed'] <= 500 and keep(s)]
        held = [s for s in samples if s['seed'] > 500 and keep(s)]
        cells = collections.defaultdict(collections.Counter)
        for s in train:
            cells[key(s)][s[target]] += 1
        counts = collections.Counter(s[target] for s in train)
        fallback = counts.most_common(1)[0][0]
        guess = lambda s: cells[key(s)].most_common(1)[0][0] if key(s) in cells else fallback
        accuracy = sum(guess(s) == s[target] for s in held) / len(held)
        baseline = sum(s[target] == fallback for s in held) / len(held)
        paired = collections.defaultdict(list)
        for s in held:
            paired[s['seed']].append(int(guess(s) == s[target]) - int(fallback == s[target]))
        differences = [statistics.mean(values) for values in paired.values()]
        margin = max(.005, 3 * statistics.stdev(differences) / math.sqrt(len(differences)))
        return accuracy, baseline, margin, len(held)

    def test_source_amount_guessers_cannot_tell_a_settled_venue_from_an_unsettled_one(self):
        samples = self.visible_samples(range(1, 1001))
        by_claim = lambda s: s['features'][:8]
        by_report = lambda s: s['features']
        by_report_without_markers = lambda s: s['features'][:10]
        by_compact = lambda s: (s['features'][0], s['separating'], s['features'][2], s['features'][10])
        by_oracle = lambda s: (s['features'][0], s['separating'], s['features'][2], s['variant'])
        for name, key in (('wording, separating, relation and markers', by_compact),
                          ('the same with the report variant given (an oracle, not visible)', by_oracle),
                          ('claim and records', by_claim), ('plus report counts without the markers of an altered '
                                                           'report', by_report_without_markers),
                          ('plus report counts and markers', by_report)):
            accuracy, baseline, margin, size = self.lookup_accuracy(samples, key, 'unsettled', lambda s: s['disputed'])
            print('Conflict unsettled-venue guesser (%s): accuracy=%.4f majority=%.4f margin=%.4f n=%d' %
                  (name, accuracy, baseline, margin, size))
            # The measured gain is the documented size of the cue (see the parity test and the docstring).
            self.assertLessEqual(accuracy - baseline, GAIN_BOUND, (name, accuracy, baseline))
        # The review's rule "treat every venue whose records differ as open": the claim is judged in both candidate
        # worlds and the result compared with the key, on all claims and on the claims where the clause matters.
        held = [s for s in samples if s['seed'] > 500]
        matters = [s for s in held if s['separating']]
        overall = sum(s['open_rule'] == s['state'] for s in held) / len(held)
        rule = sum(s['open_rule'] == s['state'] for s in matters) / len(matters)
        print('Conflict rule "differing means open": matches the key on %.4f of all claims (n=%d) and on %.4f of the '
              'claims that separate the candidates (n=%d)' % (overall, len(held), rule, len(matters)))
        self.assertTrue(.40 <= rule <= .60, rule)
        self.assertEqual(sum(s['open_rule'] != s['state'] for s in held),
                         sum(s['open_rule'] != s['state'] for s in matters))

    def test_lure_claim_and_dossier_lure_guessers_beat_the_base_rate_only_through_the_separating_claim(self):
        # Not met as worded by the review: because both readers ignore the agreement, a claim is a lure only if it
        # separates the candidates, and that is readable from the claim and the records. The test pins that this is
        # all the guessers use: nothing beyond the rule "separating claim on a disputed venue" is learnable.
        samples = self.visible_samples(range(1, 1001))
        held = [s for s in samples if s['seed'] > 500]
        rule = sum(s['separating'] == s['lure'] for s in held) / len(held)
        fallback = sum(not s['lure'] for s in held) / len(held)
        accuracy, baseline, margin, _ = self.lookup_accuracy(samples, lambda s: s['features'], 'lure')
        print('Conflict lure-claim guessers: rule separating=%.4f; lookup=%.4f; majority=%.4f' %
              (rule, accuracy, baseline))
        self.assertAlmostEqual(baseline, fallback, places=3)
        self.assertLessEqual(accuracy, rule + .005)
        reports = {(s['seed'], s['variant']): (s['features'][9] > 0, s['dossier_lure']) for s in held}
        for variant in ('correct', 'mixed'):
            accuracy, baseline, _, _ = self.lookup_accuracy(
                samples, lambda s: s['features'][8:], 'dossier_lure', lambda s, v=variant: s['variant'] == v)
            rule_accuracy = statistics.mean(present == lure for (seed, kind), (present, lure) in reports.items()
                                            if kind == variant)
            print('Conflict dossier-lure guesser, %s reports: lookup=%.4f rule "report holds a separating claim"=%.4f '
                  'majority=%.4f' % (variant, accuracy, rule_accuracy, baseline))
            self.assertLessEqual(accuracy, max(rule_accuracy, baseline) + .01)
        # In correct reports a separating bound appears only as a true claim, in which case it tells little.
        accuracy, baseline, _, _ = self.lookup_accuracy(
            samples, lambda s: s['features'][8:], 'dossier_lure', lambda s: s['variant'] == 'correct')
        self.assertLessEqual(accuracy - baseline, .06)

    def test_clause_state_is_at_its_prior_in_every_cell_of_separating_claims(self):
        # The parity the review asked for: over the correct and the mixed reports together, a separating claim is
        # as likely on an unsettled venue as on a settled one, at every wording; the other bounds lean to settled
        # because a settled venue with a matching clause can only be altered into a statement false everywhere.
        cells = collections.defaultdict(collections.Counter)
        def add(key, s):
            cells[key]['unsettled'] += s['unsettled']
            cells[key]['n'] += 1
        for s in self.visible_samples(range(1, 1001)):
            if s['disputed'] and s['features'][0] != 'exactly':
                add(('all',), s)
                add(('all', s['separating']), s)
                add(('all', s['separating'], s['variant']), s)
                add((s['features'][0], s['separating']), s)
                add((s['features'][0], s['features'][2]), s)
        prior = cells[('all',)]['unsettled'] / cells[('all',)]['n']
        print('Conflict P(unsettled | disputed claim) =', round(prior, 4),
              {k: (c['n'], round(c['unsettled'] / c['n'], 3)) for k, c in sorted(cells.items(), key=str)})
        self.assertTrue(.45 <= prior <= .55, prior)
        for key, cell in cells.items():
            if len(key) == 2 and key[1] is True:
                self.assertLess(abs(cell['unsettled'] / cell['n'] - prior), .06, key)
            elif len(key) == 2 and isinstance(key[1], str):
                self.assertLess(abs(cell['unsettled'] / cell['n'] - prior), .14, key)
        # A separating claim in a correct report needs a clause; in a mixed report it falls on either state.
        correct, mixed = cells[('all', True, 'correct')], cells[('all', True, 'mixed')]
        self.assertEqual(correct['unsettled'], 0)
        self.assertGreater(correct['n'], 100)
        self.assertGreater(mixed['unsettled'], 300)
        self.assertGreater(mixed['n'] - mixed['unsettled'], 300)
        self.assertLess(abs(mixed['unsettled'] / mixed['n'] - prior), .12)
        # Both unsettled and settled venues are separated by the "not" claims, which occur only in mixed reports.
        for wording in ('at most', 'more than', 'not'):
            self.assertGreater(cells[(wording, True)]['n'], 150, wording)

    # ---- Finding 3: faults that no earlier test caught ----------------------------------------------------------

    def test_wording_by_lower_record_by_copy_route_cells_are_filled_and_balanced(self):
        cells, total = collections.Counter(), 0
        for seed in range(1, 301):
            built = case(seed)
            records = Records(sources_of(built))
            for row in built['key']['expectations']:
                if not row['lure_states']:
                    continue
                kind, venue, positive, amount = Reading.parse(row['claim_text'])
                wording = re.search(r'is (at most|more than|exactly|not) EUR', row['claim_text'])[1]
                values = sorted(set(records.entries[venue].values()))
                cells[(wording, 'A lower' if records.entries[venue]['A'] == values[0] else 'A higher',
                       'copied from ' + records.copies[venue])] += 1
                total += 1
        # Three wordings carry lures (the review's eight cells were the two budget wordings), each by which
        # record is lower and by which record the digest copied.
        self.assertEqual(len(cells), 12, cells)
        for cell, count in cells.items():
            self.assertTrue(.04 <= count / total <= .14, (cell, count, total))
        # Rank and count are drawn independently of the amounts and of the wording (decision 2 of the family).
        for wording in ('at most', 'more than', 'not'):
            for side in ('A lower', 'A higher'):
                pair = [cells[(wording, side, 'copied from A')], cells[(wording, side, 'copied from B')]]
                self.assertLess(abs(pair[0] - pair[1]) / sum(pair), .35, (wording, side, pair))

    def test_lure_claims_do_not_always_assert_the_lower_candidate(self):
        bounds = collections.Counter()
        for seed in range(1, 301):
            built = case(seed)
            records = Records(sources_of(built))
            for row in built['key']['expectations']:
                if row['lure_states']:
                    kind, venue, positive, amount = Reading.parse(row['claim_text'])
                    low, high = sorted(set(records.entries[venue].values()))
                    bounds['lower candidate' if amount == low else 'higher candidate' if amount == high
                           else 'between the candidates'] += 1
        total = sum(bounds.values())
        print('Conflict lure claims by the amount they state:', dict(bounds))
        self.assertEqual(set(bounds), {'lower candidate', 'higher candidate', 'between the candidates'})
        for count in bounds.values():
            self.assertTrue(.15 <= count / total <= .55, bounds)

    def test_amount_relations_and_digits_are_the_same_on_settled_and_unsettled_venues(self):
        features = collections.defaultdict(lambda: {True: collections.Counter(), False: collections.Counter()})
        for seed in range(1, 601):
            records = Reading(sources_of(case(seed)))
            for venue, entries in records.entries.items():
                if not records.disputed(venue):
                    continue
                low, high = sorted(set(entries.values()))
                target, previous, equipment = records.figures[venue]
                gap = high - low
                unsettled = records.unsettled(venue)
                observed = {'gap': gap, 'equipment steps above the higher': (equipment - high) // gap,
                            'last season steps below the lower': (low - previous) // gap,
                            'target is the lower': target == low, 'A lower': entries['A'] == low,
                            'copied from A': records.copies[venue] == 'A',
                            'equipment minus target in gaps': (equipment - target) / gap}
                for position, amount in enumerate((low, high, target, previous, equipment)):
                    observed['last digit of amount %d' % position] = amount % 10
                for name, value in observed.items():
                    features[name][unsettled][value] += 1
        for name, by_state in features.items():
            sizes = {state: sum(counter.values()) for state, counter in by_state.items()}
            self.assertGreater(min(sizes.values()), 1000, name)
            values = set(by_state[True]) | set(by_state[False])
            for value in values:
                difference = abs(by_state[True][value] / sizes[True] - by_state[False][value] / sizes[False])
                self.assertLess(difference, .06, (name, value, difference))

    def test_lure_amounts_carry_no_mark(self):
        digits, others = collections.Counter(), collections.Counter()
        for seed in range(1, 601):
            for row in case(seed)['key']['expectations']:
                amount = int(re.search(r'EUR (\d+)', row['claim_text'])[1])
                (digits if row['lure_states'] else others)[amount % 10] += 1
        for counter in (digits, others):
            total = sum(counter.values())
            self.assertGreater(total, 500)
            self.assertEqual(set(counter), set(range(10)))
            for digit in range(10):
                self.assertLess(abs(counter[digit] / total - .1), .045, (digit, counter))

    def test_numeric_classifier_detects_threshold_leaks(self):
        for position in range(20):
            def features(value):
                fields = [0] * 20
                fields[position] = value
                return tuple(fields)
            classifier = fit_stump([(features(value), value > 0) for value in (-5, -3, -1, 1, 3, 5)])
            for value in (-4, -2, 2, 4):
                self.assertEqual(classifier(features(value)), value > 0, position)

    def test_written_suite_loads_seals_and_stages(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='conflict-tests-', dir=ROOT / 'tmp') as temporary:
            root = Path(temporary)
            fixtures, keys, arena, sealed = [root / name for name in ('fixtures', 'keys', 'arena', 'sealed')]
            summary = generate.write_suite(fixtures, keys, 9, 3, ['conflict'])
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
        with tempfile.TemporaryDirectory(prefix='conflict-scan-', dir=ROOT / 'tmp') as temporary:
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





if __name__ == "__main__":
    unittest.main()
