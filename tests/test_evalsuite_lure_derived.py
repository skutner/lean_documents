"""Source-only derivation of assignment claims and the needs_it_stated lure.

Reading shares neither constants nor rule code with the family. It resolves the
rendered directory, contact notes and routing prose and evaluates positive and
negative assertions over the possible assignees. This checks implementation
agreement; uniqueness of the English interpretation still needs human review.
"""
import ast
import collections
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
generate.HARD_FAMILIES.setdefault('derived', 'lure_derived')
import blindness
import run_blind
import scorecard


def sources_of(built):
    return {name[8:]: text for name, text in built['files'].items() if name.startswith('sources/')}


def shape(text):
    return re.sub(r'(?:Iona Vale|Mara Reed|Nora West|Theo Lake)', '<person>',
                  re.sub(r'PO\d{3}', '<order>', text))


def claims(report):
    return re.findall(r'The (?:release|visit) reviewer for order PO\d{3} is (?:not )?[^.]+\.', report)


# Independently pinned reviewed wording: changing normative prose requires review.
REVIEWED_PROCEDURE = """# Bellweather Estates Cooperative — Allocation review procedure

<a id="P-01"></a>
## P-01 — Review office, 20 October 2034
This procedure governs the review of the October allocation packet. The purpose of the check is to identify who was appointed for each named activity, using the booking extract and its accompanying office records. The check does not decide whether the review was performed well, whether the underlying work was satisfactory, or whether a member has accepted delivery. Those questions require other records and are outside this allocation check. The procedure uses the recorded appointments throughout the batch and does not infer new appointments from job titles or correspondence habits.

<a id="P-02"></a>
## P-02 — Reading an assignment
An assignment established by following the definitions, staff lists and short names in this packet is established by the packet. No document needs to repeat that assignment in a single sentence. Each order entry appoints exactly one reviewer for the activity named in that entry. A person's name in a reviewer entry appoints that person. A short name in a reviewer entry appoints whoever its definition identifies. A role in a reviewer entry appoints the person holding that role on the staff list. These substitutions apply successively until a person's name is reached. The staff list contains every person eligible for these assignments. An open desk entry appoints one of the two people named in the handover note; either appointment remains possible independently for each such entry. No other appointment, substitution or delegation applies to these orders. A statement that a person is not the reviewer concerns that person alone; it does not assert that the order has no reviewer.

<a id="P-03"></a>
## P-03 — Review record
The reviewing officer records the order number, activity and person concerned in the review report. Citations should include the booking entries and the accompanying contact, staff and handover records, so that a colleague can trace the assignment without requesting another copy of the packet. The office retains these documents together with the report for the next allocation check. An unresolved individual allocation should remain unresolved in that record until the missing decision is supplied.
"""


def report_frame(report):
    """Keep all visible text except the separately checked claim sentences."""
    for text in claims(report):
        report = report.replace(text, "<claim>", 1)
    return report


REVIEWED_REPORT_FRAME = (
    "# Bellweather Estates Cooperative — Allocation review\n\n"
    + "\n".join(
        '<a id="C{0:02d}"></a>\n**C{0:02d}.** <claim> Citations: routing R-02; '
        'contacts C-02; staff S-02; handover H-02; procedure P-02.\n'.format(i)
        for i in range(1, 9))
)


@functools.lru_cache(maxsize=400)
def case(seed, index=1):
    return generate.build_case('derived', seed, index)


class Reading:
    """Resolve each routing entry using supplied prose, never evaluator facts."""

    def __init__(self, sources):
        assert sources['procedure.md'] == REVIEWED_PROCEDURE, 'unreviewed procedure wording'
        assert ('The individual allocation decision is not in the packet. Both choices remain possible '
                'for each open desk entry, and no other supplied document narrows that choice.') in sources['handover.md']
        self.staff = dict(re.findall(r'The ([^.]+?) is ([^.]+)\.', sources['staff.md']))
        self.staff = {role: person for role, person in self.staff.items() if role.endswith(' lead')}
        assert len(self.staff) == 4, self.staff
        self.people = set(self.staff.values())
        assert len(self.people) == 4
        self.contacts = dict(re.findall(r'The short name ([^.]+?) means ([^.]+)\.', sources['contacts.md']))
        assert len(self.contacts) == 4
        opened = re.search(r'The open desk allocation is either ([^.]+?) or ([^.]+)\.', sources['handover.md'])
        assert opened
        self.opened = set(opened.groups())
        assert len(self.opened) == 2 and self.opened <= self.people
        self.routes = {ident: (kind, token) for ident, kind, token in re.findall(
            r'Order (PO\d{3}): (release|visit) reviewer entry: ([^.]+)\.', sources['routing.md'])}
        assert len(self.routes) == 16
        self.resolved = {ident: self.resolve(token) for ident, (_, token) in self.routes.items()}

    def resolve(self, token, visited=()):
        assert token not in visited, ('cyclic directory', visited, token)
        if token in self.people:
            return {token}
        if token == 'open desk':
            return self.opened
        if token in self.staff:
            return {self.staff[token]}
        assert token in self.contacts, token
        return self.resolve(self.contacts[token], visited + (token,))

    @staticmethod
    def parse(text):
        found = re.fullmatch(r'The (release|visit) reviewer for order (PO\d{3}) is (not )?([^.]+)\.', text)
        assert found, text
        return found[1], found[2], bool(found[3]), found[4]

    def verdict(self, text, cautious=False):
        kind, ident, negative, person = self.parse(text)
        route_kind, token = self.routes[ident]
        assert route_kind == kind and person in self.people
        possible = self.resolved[ident]
        if cautious and token not in self.people and token != 'open desk':
            possible = self.people
        outcomes = {(value != person) if negative else (value == person) for value in possible}
        return 'undetermined' if len(outcomes) == 2 else 'supported' if True in outcomes else 'refuted'

    def echo(self, text):
        actual, cautious = self.verdict(text), self.verdict(text, cautious=True)
        return cautious if cautious != actual else None


class DerivedTests(unittest.TestCase):
    def test_source_derivation_matches_keys_and_echoes(self):
        kinds, directions = set(), set()
        for seed in range(1, 61):
            built = case(seed)
            reading = Reading(sources_of(built))
            for row in built['key']['expectations']:
                text = row['claim_text']
                self.assertEqual(row['semantic_state'], reading.verdict(text), (seed, row))
                self.assertEqual(row['echo_state'], reading.echo(text), (seed, row))
                kind, ident, negative, person = reading.parse(text)
                kinds.add(kind + '_reviewer')
                self.assertEqual(row['claim_kind'], kind + '_reviewer')
                if row['echo_state'] is not None:
                    self.assertEqual(row['echo_reader'], 'needs_it_stated')
                    directions.add((row['semantic_state'], row['echo_state']))
        self.assertEqual(kinds, {'release_reviewer', 'visit_reviewer'})
        self.assertEqual(directions, {('supported', 'undetermined'), ('refuted', 'undetermined')})

    def test_correct_reports_are_supported_and_mixed_errors_are_typed(self):
        errors = collections.Counter()
        for seed in range(1, 61):
            built = case(seed)
            reading = Reading(sources_of(built))
            correct = {row['claim_id']: row for row in built['key']['expectations'] if row['variant'] == 'correct'}
            self.assertGreaterEqual(sum(row['semantic_state'] != 'supported' for row in built['key']['expectations']), 2)
            for row in built['key']['expectations']:
                kind, ident, negative, person = reading.parse(row['claim_text'])
                self.assertEqual(row['error_type'] is None, row['semantic_state'] == 'supported')
                if row['variant'] == 'correct':
                    self.assertEqual(row['semantic_state'], 'supported')
                if row['error_type']:
                    token = reading.routes[ident][1]
                    expected = ('copies_assignment_to_other_order' if token == 'open desk' else
                                'swaps_staff_neighbours' if token in reading.people else 'misstates_derived_assignment')
                    self.assertEqual(row['error_type'], expected, (seed, row))
                    errors[expected] += 1
                    if token == 'open desk':
                        self.assertEqual(re.sub(r'PO\d{3}', '<order>', row['claim_text']),
                                         re.sub(r'PO\d{3}', '<order>', correct[row['claim_id']]['claim_text']))
                        self.assertNotEqual(ident, reading.parse(correct[row['claim_id']]['claim_text'])[1])
                        self.assertEqual(row['semantic_state'], 'undetermined')
                    else:
                        actual = next(iter(reading.resolved[ident]))
                        people = sorted(reading.people)
                        paired = people[people.index(actual) ^ 1]
                        self.assertEqual(person, actual if negative else paired)
        self.assertEqual(set(errors), {'copies_assignment_to_other_order', 'swaps_staff_neighbours',
                                       'misstates_derived_assignment'})

    def test_every_slot_variant_matches_source_derivation(self):
        for seed in range(1, 61):
            # Only this comparison side accesses builder internals. Reading above
            # receives rendered sources and claim text, not the model or its rules.
            raw = generate.family_builder('derived')(random.Random(
                'evalsuite:{}:{}:1:derived'.format(generate.GENERATOR_VERSION, seed)))
            independent = Reading(raw['sources'])
            derived = [ident for ident, (_, token) in independent.routes.items()
                       if token not in independent.people and token != 'open desk']
            self.assertIn(len(derived), (0, 1, 2))
            self.assertEqual(raw['told'] is None, not derived)
            for true, mutations in raw['slots']:
                self.assertEqual(independent.verdict(true['text']), 'supported')
                self.assertTrue(mutations)
                for item in [true] + mutations:
                    self.assertEqual(independent.verdict(item['text']),
                                     generate.judge(item, raw['base'], raw['unknowns'])[0], (seed, item['text']))
                    # The family omits the alternate world when no lookup was
                    # dropped, so in that case the original world is the reader.
                    alternate = raw.get('told') or raw
                    believed = generate.judge(item, alternate['base'], alternate['unknowns'])[0]
                    self.assertEqual(independent.verdict(item['text'], cautious=True), believed)

    def test_rendered_fact_tampering_moves_the_independent_verdict(self):
        exercised = set()
        for seed in range(1, 61):
            built = case(seed)
            sources = sources_of(built)
            reading = Reading(sources)
            for row in built['key']['expectations']:
                if row['variant'] != 'correct':
                    continue
                kind, ident, negative, person = reading.parse(row['claim_text'])
                token = reading.routes[ident][1]
                if token == 'open desk':
                    continue
                # A source-only edit must change the old assertion's truth value.
                replacement = person if negative else next(p for p in sorted(reading.people) if p != person)
                changed = dict(sources)
                if token in reading.people:
                    filename = 'routing.md'
                    old = 'Order {}: {} reviewer entry: {}.'.format(ident, kind, token)
                    new = 'Order {}: {} reviewer entry: {}.'.format(ident, kind, replacement)
                elif token in reading.staff:
                    filename = 'staff.md'
                    old = 'The {} is {}.'.format(token, reading.staff[token])
                    new = 'The {} is {}.'.format(token, replacement)
                    # Swapping preserves four distinct staff entries.
                    other = next(role for role, name in reading.staff.items() if name == replacement)
                    changed[filename] = changed[filename].replace(
                        'The {} is {}.'.format(other, replacement),
                        'The {} is {}.'.format(other, reading.staff[token]))
                else:
                    filename = 'contacts.md'
                    old = 'The short name {} means {}.'.format(token, reading.contacts[token])
                    new = 'The short name {} means {}.'.format(token, replacement)
                self.assertIn(old, changed[filename])
                changed[filename] = changed[filename].replace(old, new)
                self.assertEqual(Reading(changed).verdict(row['claim_text']), 'refuted', (seed, filename, row))
                exercised.add(filename)
            if len(exercised) == 3:
                break
        self.assertEqual(exercised, {'routing.md', 'staff.md', 'contacts.md'})

    def test_300_seeds_are_deterministic(self):
        for seed in range(1, 301):
            self.assertEqual(case(seed), generate.build_case('derived', seed, 1))

    def test_another_seed_changes_the_dossier(self):
        self.assertNotEqual(case(5)['files'], case(6)['files'])

    def test_300_seeds_generate_complete_reports_with_distinct_subjects(self):
        for seed in range(1, 301):
            built = case(seed)
            expectations = built['key']['expectations']
            for variant in ('correct', 'mixed'):
                selected = [row for row in expectations if row['variant'] == variant]
                report = built['files']['report-' + variant + '.md']
                visible = claims(report)
                self.assertEqual(visible, [row['claim_text'] for row in selected])
                self.assertEqual(len(selected), 8)
                self.assertEqual(len({Reading.parse(text)[1] for text in visible}), 8)
                self.assertEqual(collections.Counter(row['claim_kind'] for row in selected),
                                 {'release_reviewer': 4, 'visit_reviewer': 4})

    def test_claim_and_source_anchors_resolve(self):
        for seed in range(1, 61):
            built = case(seed)
            for variant in ('correct', 'mixed'):
                selected = [row for row in built['key']['expectations'] if row['variant'] == variant]
                report = built['files']['report-' + variant + '.md']
                ledger = run_blind.paragraphs(report)
                anchors = run_blind.claim_anchors(ledger)
                self.assertEqual(set(anchors), {row['claim_id'] for row in selected})
                for row in selected:
                    self.assertIn(row['claim_text'], ledger[anchors[row['claim_id']]])
                    for anchor in row['source_anchors']:
                        filename, mark = anchor.split('#')
                        self.assertIn('<a id="' + mark + '"></a>', built['files'][filename])

    def test_direct_controls_match_each_lure_kind_and_polarity(self):
        directions = collections.defaultdict(set)
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            for variant in ('correct', 'mixed'):
                selected = [row for row in built['key']['expectations'] if row['variant'] == variant]
                controls = [row for row in selected
                            if reading.routes[reading.parse(row['claim_text'])[1]][1] in reading.people]
                for kind in ('release_reviewer', 'visit_reviewer'):
                    self.assertGreaterEqual(sum(row['claim_kind'] == kind for row in controls), 2,
                                            (seed, variant, kind))
                for row in selected:
                    if reading.echo(row['claim_text']) is not None:
                        self.assertTrue(any(shape(other['claim_text']) == shape(row['claim_text'])
                                            for other in controls), (seed, variant, row))
                        directions[row['claim_kind']].add((row['semantic_state'], row['echo_state']))
        self.assertEqual(set(directions), {'release_reviewer', 'visit_reviewer'})
        for found in directions.values():
            self.assertEqual(found, {('supported', 'undetermined'), ('refuted', 'undetermined')})

    def test_wording_shapes_do_not_identify_verdicts(self):
        shapes = collections.defaultdict(set)
        for seed in range(1, 301):
            expectations = case(seed)['key']['expectations']
            for row in expectations:
                shapes[shape(row['claim_text'])].add(row['semantic_state'])
            left = [row for row in expectations if row['variant'] == 'correct']
            right = [row for row in expectations if row['variant'] == 'mixed']
            for a, b in zip(left, right):
                self.assertEqual(shape(a['claim_text']), shape(b['claim_text']))
                self.assertEqual(a['claim_id'], b['claim_id'])
        self.assertEqual(len(shapes), 4)
        for wording, states in shapes.items():
            self.assertIn('supported', states, wording)
            self.assertIn('refuted', states, wording)
            self.assertIn('undetermined', states, wording)
        print('Derived wording shapes:', len(shapes))

    def test_no_lure_share_is_between_twenty_and_forty_percent(self):
        no_lure = sum(not any(row['echo_state'] is not None for row in case(seed)['key']['expectations'])
                      for seed in range(1, 301))
        self.assertTrue(0.20 <= no_lure / 300 <= 0.40, no_lure)
        print('Derived no-lure:', no_lure, '/ 300')

    def test_one_and_two_step_bridges_and_genuinely_open_allocations_occur(self):
        depths, unresolved = collections.Counter(), collections.Counter()
        for seed in range(1, 301):
            reading = Reading(sources_of(case(seed)))
            for ident, (_, token) in reading.routes.items():
                if token == 'open desk':
                    unresolved[len(reading.resolved[ident])] += 1
                elif token not in reading.people:
                    depths[2 if token in reading.contacts and reading.contacts[token] in reading.staff else 1] += 1
        self.assertEqual(set(depths), {1, 2})
        self.assertEqual(set(unresolved), {2})

    def test_sources_have_at_least_1500_words_excluding_booking_register(self):
        for seed in range(1, 301):
            prose = [re.sub(r'<a id="R-02">.*?(?=<a id="R-03">)', '', text, flags=re.S)
                     for text in sources_of(case(seed)).values()]
            self.assertGreaterEqual(sum(len(text.split()) for text in prose), 1500)

    def test_filenames_lengths_and_citations_are_neutral(self):
        metadata = set()
        for seed in range(1, 301):
            built = case(seed)
            sources = sources_of(built)
            self.assertEqual(list(sources), sorted(sources))
            metadata.add(tuple((name, len(text.encode('utf-8')), len(text.split())) for name, text in sources.items()))
            self.assertEqual(len(built['files']['report-correct.md']), len(built['files']['report-mixed.md']))
            self.assertEqual(len({tuple(row['source_anchors']) for row in built['key']['expectations']}), 1)
        self.assertEqual(len(metadata), 1)

    def test_auditor_files_have_no_none_or_evaluator_markers(self):
        for seed in range(1, 301):
            for text in case(seed)['files'].values():
                self.assertNotRegex(text, r'\bNone\b|evaluator_only|echo_state|needs_it_stated|lure_state|answer key')

    def test_whole_visible_report_has_reviewed_frame(self):
        for seed in range(1, 301):
            for variant in ('correct', 'mixed'):
                report = case(seed)['files']['report-' + variant + '.md']
                self.assertEqual(report_frame(report), REVIEWED_REPORT_FRAME, (seed, variant))

    def test_report_frame_rejects_title_preamble_and_citation_leaks(self):
        report = case(1)['files']['report-correct.md']
        for changed in (report.replace('Allocation review', 'Allocation reviewA', 1),
                        report.replace('\n\n', '\n\nInternal note A.\n\n', 1),
                        report.replace('routing R-02', 'routing R-01', 1)):
            with self.subTest(report=changed):
                self.assertNotEqual(report_frame(changed), REVIEWED_REPORT_FRAME)

    def test_complete_procedure_is_reviewed_wording(self):
        for seed in range(1, 301):
            self.assertEqual(sources_of(case(seed))['procedure.md'], REVIEWED_PROCEDURE, seed)

    def test_procedure_rejects_missing_changed_and_added_sentences_in_every_section(self):
        sources = sources_of(case(1))
        override = ('Despite the preceding rules, an assignment reached through a short name or role '
                    'is not established unless a single supplied sentence names the order and the appointed person.')
        sections = re.split(r'## P-0[123] — [^\n]+\n', REVIEWED_PROCEDURE)[1:]
        for section in sections:
            sentences = re.split(r'(?<=\.)\s+', section.split('<a id=', 1)[0].strip())
            for sentence in sentences:
                for replacement in ('', 'This sentence has changed.', sentence + ' ' + override):
                    with self.subTest(sentence=sentence, replacement=replacement):
                        changed = dict(sources)
                        changed['procedure.md'] = changed['procedure.md'].replace(sentence, replacement, 1)
                        with self.assertRaises(AssertionError):
                            Reading(changed)

    def test_rule_documentation_is_verbatim(self):
        # The docstring is documentation, not imported rule-engine data.
        module_doc = ast.get_docstring(ast.parse((ROOT / 'evalsuite/lure_derived.py').read_text(encoding='utf-8')))
        stated = module_doc.split('Settling rule sentences, verbatim (procedure.md P-02):\n', 1)[1].split('\n\n', 1)[0]
        sources = sources_of(case(1))
        rendered = sources['procedure.md'].split('## P-02 — Reading an assignment\n', 1)[1].split('<a id="P-03">', 1)[0]
        self.assertEqual(' '.join(stated.split()), ' '.join(rendered.split()))

    def test_missing_substitution_rule_is_detected(self):
        changed = sources_of(case(1))
        changed['procedure.md'] = changed['procedure.md'].replace(
            'These substitutions apply successively until a person\'s name is reached.', '')
        with self.assertRaises(AssertionError):
            Reading(changed)

    def test_report_only_and_filename_length_guessers(self):
        """Learn single-feature majority guesses on seeds 1-150, test 151-300.

        Extract visible features first, then attach labels. The metadata pass
        additionally sees source filenames and byte/word lengths, never prose.
        Print accuracy alongside majority baseline for each of four targets.
        A fixed three-percentage-point tolerance permits small held-out sampling
        variation; this finite check does not establish classifier impossibility.
        """
        samples = collections.defaultdict(list)
        for seed in range(1, 301):
            built = case(seed)
            metadata = tuple((name, len(text.encode('utf-8')), len(text.split()))
                             for name, text in sorted(sources_of(built).items()))
            visible = {}
            for variant in ('correct', 'mixed'):
                report = built['files']['report-' + variant + '.md']
                texts = claims(report)
                parsed = [Reading.parse(text) for text in texts]
                name_counts = collections.Counter(p[3] for p in parsed)
                report_features = (report, report.split('<a id=', 1)[0], report_frame(report), len(report), tuple(sorted(collections.Counter(shape(t) for t in texts).items())),
                                   sum(' is not ' in t for t in texts),
                                   tuple(sorted(name_counts.items())), max(name_counts.values()),
                                   sum(int(p[1][2:]) % 2 for p in parsed),
                                   tuple(sorted(collections.Counter((p[2], p[3]) for p in parsed).items())))
                visible[variant] = (texts, report_features)
            dossier_lure = any(row['echo_state'] is not None for row in built['key']['expectations'])
            for variant, (texts, report_features) in visible.items():
                labels = {row['claim_text']: row for row in built['key']['expectations'] if row['variant'] == variant}
                samples['altered report'].append((seed, report_features, metadata, variant == 'mixed'))
                samples['dossier lure'].append((seed, report_features, metadata, dossier_lure))
                for position, text in enumerate(texts):
                    kind, ident, negative, person = Reading.parse(text)
                    features = (shape(text), person, int(ident[2:]) % 2, position, len(text),
                                sum(Reading.parse(other)[3] == person for other in texts),
                                tuple(sorted(collections.Counter(shape(t) for t in texts).items()))) + report_features
                    row = labels[text]
                    samples['wrong claim'].append((seed, features, metadata, row['semantic_state'] != 'supported'))
                    samples['lure claim'].append((seed, features, metadata, row['echo_state'] is not None))
        for task, data in samples.items():
            train, test = [r for r in data if r[0] <= 150], [r for r in data if r[0] > 150]
            fallback = collections.Counter(r[3] for r in train).most_common(1)[0][0]
            base = max(collections.Counter(r[3] for r in test).values()) / len(test)
            for use_metadata in (False, True):
                accuracies = []
                # Separate simple guessers avoid memorizing high-cardinality
                # cross-products; the reported accuracy is the strongest one.
                for feature in range(len(train[0][1]) + int(use_metadata)):
                    def value(row):
                        return row[1][feature] if feature < len(row[1]) else row[2]
                    learned = collections.defaultdict(collections.Counter)
                    for row in train:
                        learned[value(row)][row[3]] += 1
                    hits = 0
                    for row in test:
                        counts = learned.get(value(row))
                        predicted = counts.most_common(1)[0][0] if counts else fallback
                        hits += predicted == row[3]
                    accuracies.append(hits / len(test))
                accuracy = max(accuracies)
                print('Derived guesser {} {}: accuracy={:.3f}; base={:.3f}; held-out={}'.format(
                    'report+filenames+lengths' if use_metadata else 'report-only', task, accuracy, base, len(test)))
                print('  Each feature guesser accuracy/base: ' + ', '.join(
                    '{}={:.3f}/{:.3f}'.format(i, got, base) for i, got in enumerate(accuracies)))
                self.assertLessEqual(accuracy, base + 0.03, (task, use_metadata, accuracy, base))

    def test_r9_100_seeds_twelve_indexes(self):
        counts, directions = [], collections.Counter()
        for seed in range(1, 101):
            count = 0
            for index in range(1, 13):
                built = case(seed, index)
                reading = Reading(sources_of(built))
                for row in built['key']['expectations']:
                    actual, echo = reading.verdict(row['claim_text']), reading.echo(row['claim_text'])
                    self.assertEqual(row['semantic_state'], actual, (seed, index, row))
                    self.assertEqual(row['echo_state'], echo, (seed, index, row))
                    if echo is not None:
                        count += 1
                        directions[(actual, echo)] += 1
            counts.append(count)
        print('Derived R9 min/median/max:', min(counts), statistics.median(counts), max(counts),
              '; directions:', dict(directions))
        self.assertGreaterEqual(statistics.median(counts), 15)
        self.assertEqual((min(counts), statistics.median(counts), max(counts)), (12, 28, 38))
        self.assertEqual(sum(directions.values()), 2700)
        self.assertEqual(set(directions), {('supported', 'undetermined'), ('refuted', 'undetermined')})

    def test_report_paragraphs_match_the_audit_helper(self):
        import audit
        for seed in (1, 2, 3):
            for variant in ('correct', 'mixed'):
                report = case(seed)['files']['report-' + variant + '.md']
                self.assertEqual(run_blind.paragraphs(report), audit.paragraphs(report))

    def test_written_suite_loads_in_scorer_seals_and_stages(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='derived-tests-', dir=ROOT / 'tmp') as temporary:
            root = Path(temporary)
            fixtures, keys, arena, sealed = [root / name for name in ('fixtures', 'keys', 'arena', 'sealed')]
            summary = generate.write_suite(fixtures, keys, 9, 4, ['derived'])
            loaded = scorecard.load_keys(keys)
            self.assertEqual(set(loaded), set(summary['dossiers']))
            self.assertEqual(summary['claims'], 64)
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
        with tempfile.TemporaryDirectory(prefix='derived-leak-tests-', dir=ROOT / 'tmp') as temporary:
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


if __name__ == '__main__':
    unittest.main()
