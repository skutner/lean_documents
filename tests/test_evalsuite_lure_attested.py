"""Independent readings of rendered files, plus finite-sample leakage probes.

Reading never imports family rules, constants, predicates or world data. Its
English interpretation is written here separately; agreement cannot establish
that the English has no other reasonable interpretation. Classifiers are narrow
empirical probes, not evidence that every report-only strategy is ineffective.
"""
import ast
import collections
import datetime
import functools
from pathlib import Path
import re
import statistics
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
sys.path.insert(0, str(ROOT / 'skills/evidence-to-proof-review/scripts'))
import generate
generate.HARD_FAMILIES.setdefault('attested', 'lure_attested')
import blindness
import run_blind
import scorecard
import audit


def sources_of(built):
    return {name[8:]: text for name, text in built['files'].items() if name.startswith('sources/')}


def sections(text):
    parts = re.split(r'<a id="([^"]+)"></a>\n', text)
    return dict(zip(parts[1::2], parts[2::2]))


def wording_shape(text):
    """Keep polarity and every non-entity word intact."""
    return re.sub(r'\d+', '#', re.sub(r'R\d+', '<inspection>', text))


def report_claims(text):
    return re.findall(r'\*\*C\d+\.\*\* (Inspection [^.]+\.)', text)


@functools.lru_cache(maxsize=1600)
def case(seed, index=1):
    return generate.build_case('attested', seed, index)


# Independently retained reviewed procedure, including every section.
REVIEWED_PROCEDURE = """# Merewick Facilities Office — Examination procedure

<a id="P-00"></a>
## P-00 — Issue and purpose
Issued by operations manager Lena Venn on 2033-01-08. This procedure governs the annual review of depot access equipment. It distinguishes the date an examination took place from the date its paperwork reached the office. Inspectors retain the asset label with their worksheet so that a copied appointment cannot substitute for an examination of that asset.

<a id="P-01"></a>
## P-01 — Reading the working files
Only an inspection's own file establishes its state, result, completion date and whether a repeat examination was completed.

Within a file, the entry with the latest entry date replaces the earlier account in full, including corrections to the history of examinations.

An entry date is the date of writing and is not an examination date.

The register's Status note is a convenience maintained by hand and establishes none of those facts.

A completed examination has either passed or failed; an examination not completed has no result or completion date.

In this review, an inspection passed if and only if its current result is passed; did not pass includes failed and not completed.

Re-inspected means that at least two examinations of the same asset were completed, as established by the latest account; a second visit without a completed examination does not count.

For a completed inspection, completion by a checkpoint means that the completion date of its most recent completed examination is on or before that checkpoint, including the whole checkpoint day.

An inspection not completed was not completed by any checkpoint.

When the latest entry says that the completion date was not recorded, exactly the two dates it lists remain possible, and neither may be excluded.

Open dates in different files may take any combination of their listed values.

<a id="P-02"></a>
## P-02 — Review administration
The review concerns the recorded examination history, not permission to put equipment back into service. Repair orders and local isolation instructions remain with the depot supervisor. For each inspection, reviewers may compare completion with the three checkpoints listed in its working file. Those dates belong to the review timetable; none is an examination booking. The archive copy of a working file retains each of its dated entries so that staff can trace how a worksheet reached the file.
"""


# Reviewed wording of every other fixed sentence of the dossier, kept here apart from the module. A slot that
# varies (a person, a date, a number, an asset) is a {placeholder}; everything else must match word for word.
REVIEWED_TECHNICIANS = ('Mara Senn', 'Joss Whitby', 'Dev Okoro', 'Ines Calloway')
REVIEWED_AUTHORS = {(name, 'examining technician') for name in REVIEWED_TECHNICIANS} | {('Hollis Brandt', 'depot supervisor')}
REVIEWED_CLERKS = ('Oren Pell', 'Tamsin Rudd')
REVIEWED_ASSETS = (
    ('Alder depot', 'loading door', 'the lower guide and the emergency release'),
    ('Briar store', 'platform gate', 'the latch housing and the closing spring'),
    ('Cairn workshop', 'service shutter', 'the travel stops and the manual chain'),
    ('Dene warehouse', 'delivery barrier', 'the pivot pin and the locking handle'),
    ('Elver station', 'access hatch', 'the hinges and the retaining catch'),
    ('Fallow yard', 'inspection cover', 'the lifting handles and the edge seal'),
    ('Gorse hall', 'equipment cage', 'the mesh fixings and the access lock'),
    ('Hazel annex', 'stores door', 'the frame anchors and the door closer'),
)
REVIEWED_REGISTER_INTRO = (
    'Merewick Facilities Office. Prepared by appointments clerk {clerk} on 2035-03-01 for the annual equipment review. '
    'This index helps the review secretary locate the eight working files. Each Status note was copied into the index '
    'by hand on the transcription date given beside it; the index was assembled for circulation without another pass '
    'over those entries. Supervisors use the location and asset descriptions to route questions to the right technician. '
    'Booking changes are handled by the appointments desk, while examination worksheets are retained by the records desk. '
    'The two desks use the same inspection reference on correspondence.')
REVIEWED_ASSIGNMENT = (
    'Opened by technician {tech} on {date1} for {location}. Inspection {ident} concerns the {asset}, specifically {parts}. '
    'The supervisor requested an operational examination after routine maintenance, and the appointments diary shows a '
    'booking for {date2}. The technician was to check the asset label before recording travel, closure and manual release. '
    'Access arrangements and repair work are separate from the examination itself. The dated entries below record whether '
    'an examination finished and whether the operating checks met the standard. The working file stays with the records '
    'desk until the annual review.\n\nReview checkpoints: {date3}, {date4}, {date5}.')
REVIEWED_ROW = ('Inspection {ident} — {asset}; working file record-{number}.md. Transcribed {date1}. Status note: {note}')
REVIEWED_NOTES = (
    'Awaiting inspection {tag}, no result filed; repeat: none.',
    'Last inspected on {date1} {tag}, {result}; repeat: {repeat}.',
    'Last inspected on {date1} or {date2}, {result}; repeat: {repeat}.')
REVIEWED_REASONS_FIRST_TWO = (
    # first entry, two examinations held (the first entry records both)
    'I examined the asset twice at the depot and set down both results here, as the diary required.',
    'I looked at the asset twice at the depot and entered both results here, as the diary required.',
    'I inspected the asset twice on the site visit and noted both sets here, as the diary required.')
REVIEWED_REASONS_FIRST_OPEN = (
    # first entry, one examination held, date not recorded: the visit sheet is illegible or still with the supervisor
    'I examined the asset at the depot but the visit sheet was illegible, so the date is uncertain.',
    'I inspected the asset at the depot but the supervisor holds the sheet, so the date stays open.',
    'I examined the asset during the visit but the visit sheet was smudged, so the date stays open.')
REVIEWED_REASONS_FIRST_TWO_OPEN = (
    'I examined the asset twice at the depot but the sheet was illegible, so the date is uncertain.',
    'I examined the asset twice on site but the supervisor holds the sheet, so the date stays open.',
    'I examined the asset twice during the visit but the sheet was smudged, so the date stays open.')
REVIEWED_REASONS_REPEAT_OPEN = (
    'I inspected the asset again after the repairs, but the sheet was smudged, so the date is open.',
    'I tested the asset again after the repairs, but the sheet was smudged, so the date is unclear.',
    'I tested the asset again after the repairs, but the sheet was smudged, so the date stays open.')
REVIEWED_REASONS_COMPLETED_OPEN = (
    'After access was agreed I tested the asset, but the sheet was smudged, so the date stays open.',
    'Once access was agreed I checked the asset, but the sheet was smudged, so the date is unclear.',
    'I came back and examined the asset, but the visit sheet was illegible, so the date stays open.')
REVIEWED_REASONS_DATE_MISSING = (REVIEWED_REASONS_FIRST_OPEN + REVIEWED_REASONS_FIRST_TWO_OPEN
                                 + REVIEWED_REASONS_REPEAT_OPEN + REVIEWED_REASONS_COMPLETED_OPEN)
REVIEWED_REASONS_FIRST_ONE = (
    # first entry, one examination held, date recorded
    'I examined the asset at the depot and entered the results in this file, as the diary required.',
    'I inspected the asset on the site visit and set down the findings here, as the diary required.',
    'I looked at the asset on the site visit and entered the results here, as the supervisor asked.')
REVIEWED_REASONS_REPEAT = (
    # a repeat examination
    'I examined the asset a second time after the repair crew left and set down the findings below.',
    'I inspected the asset once more after the repair visit and set down the findings in this file.',
    'I tested the asset a second time after the repair crew left and noted the results that follow.')
REVIEWED_REASONS_COMPLETED = (
    # the examination finally held
    'I came back once access was agreed and tested the asset, setting down the checks in this file.',
    'I came back once entry was given and examined the asset, setting down the checks in this file.')
REVIEWED_REASONS = (
    REVIEWED_REASONS_FIRST_TWO + REVIEWED_REASONS_DATE_MISSING + REVIEWED_REASONS_FIRST_ONE
    + (
    # first entry, no examination
    'I called at the depot gate but did not manage to examine the asset because access was refused.',
    'I called at the depot gate but did not manage to inspect the asset because access was refused.')
    + REVIEWED_REASONS_REPEAT + REVIEWED_REASONS_COMPLETED + (
    # still no examination
    'I called back but did not manage to test the asset because access had still not been arranged.',
    'I came out again but could not examine the asset as the foreman had still not arranged access.',
    # a corrected date, a corrected result, nothing to change, a visit struck out, a date that cannot be proved
    'I checked the worksheet in full against the log and amended the date given in the first entry.',
    'I compared the worksheet line by line against the diary and fixed the date in the older entry.',
    'I checked the worksheet line by line against the file and changed the date in the prior entry.',
    'I checked the worksheet in full against the log and fixed the result given in the older entry.',
    'I compared the worksheet line by line against the log and fixed the scores in the first entry.',
    'I checked the worksheet in full against the log and found nothing to alter in the first entry.',
    'I checked the worksheet in full against the log and found nothing to amend in the older entry.',
    'I went through the worksheet in full against the diary and struck out a visit listed in error.',
    'I went through the worksheet in full against the file and struck out a visit entered in error.',
    'I checked the worksheet again against the log but could not prove the date in the first entry.',
    'I checked the worksheet twice against the log but could not prove the date in the older entry.'))
REVIEWED_COUNTS = ('Completed examinations of this asset, counting from the first: {n}.',
                   'Examinations of this asset completed to date, repeats included: {n}.')
REVIEWED_RESULTS = ('Result of the latest examination: {r}.', 'Operating checks, final result: {r}.')
REVIEWED_DATES = ('Completion date of the latest examination: {date1}, as transcribed on the visit sheet.',
                  'Completion date of the latest examination: not recorded; one of {date1} or {date2}.',
                  'Completion date of the latest examination: none, since no examination was completed here.')
REVIEWED_TAILS = (
    'The worksheet is kept in this file.',
    'The asset label was legible and matched the worksheet.',
    'The photographs taken are held by the records desk.',
    'The foreman signed the visit sheet.',
    'Copies went to the supervisor and the records desk.',
    'Nothing else was raised at the visit.')
REVIEWED_ROUTING = (
    'The records clerk checked the folder reference against the shelf list.',
    'The office copy was returned to the records desk after circulation.',
    'The review secretary requested a spare copy for the meeting folder.',
    'The clerk replaced the worn cover and retained the original reference label.')
REVIEWED_NO_ROUTING = 'No movement of the file has been recorded.'
FOOTER = '\nFiled by the Merewick Facilities Office.\n'

SLOT_PATTERNS = {'date': r'\d{4}-\d\d-\d\d', 'ident': r'R0[1-8]', 'number': r'0[1-8]', 'clerk': '|'.join(REVIEWED_CLERKS),
                 'tech': '|'.join(REVIEWED_TECHNICIANS), 'tag': r'\((?:diary entry|as diarised)\)', 'n': '[012]',
                 'r': 'pass|fail|none', 'result': 'passed|failed', 'repeat': 'done|none', 'note': '.+',
                 'location': r'[A-Z][a-z]+ [a-z]+', 'asset': '[a-z ]+?', 'parts': '[a-z ]+?'}


def slots_of(template, text):
    """The values of the {slots} if the text is the template word for word; otherwise None."""
    parts = re.split(r'\{(\w+)\}', template)
    pattern = ''.join(re.escape(part) if index % 2 == 0 else '(?P<%s>%s)' % (part, SLOT_PATTERNS[re.sub(r'\d+$', '', part)])
                      for index, part in enumerate(parts))
    match = re.fullmatch(pattern, text, re.S)
    return match.groupdict() if match else None


def entry_parts(paragraph):
    """Header, then the sentences of one dated entry sorted into their kinds; None where it is not as reviewed."""
    header = re.fullmatch(r'Entry dated (\d{4}-\d\d-\d\d) — (.+?), (examining technician|depot supervisor)\. (.+)', paragraph, re.S)
    if not header or (header[2], header[3]) not in REVIEWED_AUTHORS:
        return None
    sentences = re.split(r'(?<=\.) (?=[A-Z])', header[4])
    parts = {'date': header[1], 'author': (header[2], header[3]), 'reason': sentences[0], 'tails': []}
    for kind, templates in (('count', REVIEWED_COUNTS), ('result', REVIEWED_RESULTS), ('when', REVIEWED_DATES)):
        found = [(sentence, slots) for sentence in sentences[1:4] for template in templates
                 for slots in [slots_of(template, sentence)] if slots is not None]
        if len(found) != 1:
            return None
        parts[kind] = found[0]
    parts['tails'] = sentences[4:]
    return parts


def wb(text):
    """Bytes and words of a sentence."""
    return len(text.encode('utf-8')), len(text.split())


class Reading:
    """Use the entry with the latest date; the note reader trusts the register line."""

    def __init__(self, sources):
        procedure = sources['procedure.md']
        for phrase in ("Only an inspection's own file establishes", 'latest entry date replaces the earlier account in full',
                       'An entry date is the date of writing and is not an examination date',
                       'establishes none of those facts', 'did not pass includes failed and not completed',
                       'at least two examinations', 'second visit without a completed examination does not count',
                       'on or before that checkpoint', 'not completed by any checkpoint',
                       'exactly the two dates it lists remain possible', 'any combination of their listed values'):
            assert phrase in procedure, phrase
        self.own, self.note, self.files, self.checkpoints, self.entries, self.transcribed = {}, {}, {}, {}, {}, {}
        register = sections(sources['register.md'])
        for section, body in register.items():
            if not section.startswith('G-R'):
                continue
            ident, filename, transcribed, note = re.search(
                r'Inspection (R\d+) — .*?; working file (record-\d+\.md)\. Transcribed (\d{4}-\d\d-\d\d)\. Status note: (.+)\n', body).groups()
            self.files[ident] = filename
            own = sources[filename]
            dates = re.search(r'Review checkpoints: ([^.]+)\.', own)[1].split(', ')
            self.checkpoints[ident] = tuple(map(datetime.date.fromisoformat, dates))
            assert len(dates) == 3 and dates == sorted(dates)
            accounts = re.findall(r'Entry dated (\d{4}-\d\d-\d\d) — ([^\n]+)', own)
            assert 1 <= len(accounts) <= 3 and len({date for date, _ in accounts}) == len(accounts)
            self.entries[ident] = sorted(accounts)
            self.own[ident] = self.account(max(accounts)[1])
            self.note[ident] = self.status(note)
            self.transcribed[ident] = datetime.date.fromisoformat(transcribed)

    @staticmethod
    def account(text):
        count = int(re.search(r'(?:counting from the first|repeats included): (\d)\.', text)[1])
        result = re.search(r'(?:Result of the latest examination|Operating checks, final result): (pass|fail|none)\.', text)[1]
        if count == 0:
            assert result == 'none' and 'Completion date of the latest examination: none,' in text, text
            return False, False, False, ()
        assert count in (1, 2) and result in ('pass', 'fail'), text
        definite = re.search(r'Completion date of the latest examination: (\d{4}-\d\d-\d\d), as transcribed', text)
        if definite:
            dates = (datetime.date.fromisoformat(definite[1]),)
        else:
            dates = tuple(map(datetime.date.fromisoformat, re.search(
                r'Completion date of the latest examination: not recorded; one of (\d{4}-\d\d-\d\d) or (\d{4}-\d\d-\d\d)\.', text).groups()))
            assert len(set(dates)) == 2
        return True, result == 'pass', count == 2, dates

    @staticmethod
    def status(text):
        if re.fullmatch(r'Awaiting inspection \((?:diary entry|as diarised)\), no result filed; repeat: none\.', text):
            return False, False, False, ()
        match = re.fullmatch(r'Last inspected on (\d{4}-\d\d-\d\d)(?: \((?:diary entry|as diarised)\)| or (\d{4}-\d\d-\d\d)), '
                             r'(passed|failed); repeat: (done|none)\.', text)
        assert match, text
        dates = (match[1],) if not match[2] else (match[1], match[2])
        return True, match[3] == 'passed', match[4] == 'done', tuple(map(datetime.date.fromisoformat, dates))

    @staticmethod
    def parse(text):
        match = re.fullmatch(r'Inspection (R\d+) was (not )?completed by the checkpoint of (\d{4}-\d\d-\d\d)\.', text)
        if match:
            return match[1], 'timing', not bool(match[2]), datetime.date.fromisoformat(match[3])
        match = re.fullmatch(r'Inspection (R\d+) (passed|did not pass)\.', text)
        if match:
            return match[1], 'result', match[2] == 'passed', None
        match = re.fullmatch(r'Inspection (R\d+) was (not )?re-inspected\.', text)
        assert match, text
        return match[1], 'reinspection', not bool(match[2]), None

    def outcomes(self, ident, topic, checkpoint, note=False):
        completed, passed, repeated, dates = (self.note if note else self.own)[ident]
        if topic == 'result':
            return {passed}
        if topic == 'reinspection':
            return {repeated}
        assert checkpoint in self.checkpoints[ident]
        return {date <= checkpoint for date in dates} if completed else {False}

    def verdict(self, text, note=False):
        ident, topic, positive, checkpoint = self.parse(text)
        outcomes = {value == positive for value in self.outcomes(ident, topic, checkpoint, note)}
        return 'undetermined' if len(outcomes) == 2 else 'supported' if True in outcomes else 'refuted'

    def echo(self, text):
        actual, told = self.verdict(text), self.verdict(text, note=True)
        return told if actual != told else None

    def slip(self, text):
        ident, topic, _, checkpoint = self.parse(text)
        own = self.outcomes(ident, topic, checkpoint)
        told = self.outcomes(ident, topic, checkpoint, note=True)
        if own != told:
            return 'settles_status_note_conflict'
        if self.verdict(text) == 'undetermined':
            return 'settles_unrecorded_date'
        return 'reverses_' + topic


def auc(scores, labels):
    """Probability that a random wrong note scores above a random right one (ties count half)."""
    ranked = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks, start = [0.0] * len(scores), 0
    while start < len(ranked):
        end = start
        while end + 1 < len(ranked) and scores[ranked[end + 1]] == scores[ranked[start]]:
            end += 1
        for position in range(start, end + 1):
            ranks[ranked[position]] = (start + end) / 2 + 1
        start = end + 1
    positives = sum(labels)
    negatives = len(labels) - positives
    return (sum(rank for rank, label in zip(ranks, labels) if label) - positives * (positives + 1) / 2) / (positives * negatives)


@functools.lru_cache(maxsize=None)
def survey(first=1, last=1000):
    """What the register and the first section of each file show about every record, with the label "note is wrong".

    Read from the rendered text only. The label compares the account in the latest entry with the register note."""
    rows = []
    for seed in range(first, last + 1):
        sources = sources_of(case(seed))
        reading = Reading(sources)
        notes = {ident: re.search(r'Status note: (.+)\n', sections(sources['register.md'])['G-' + ident])[1]
                 for ident in reading.files}
        topic = Reading.parse(report_claims(case(seed)['files']['report-correct.md'])[0])[1]
        dates = collections.Counter(date for ident in reading.files for date in reading.note[ident][3])
        for ident, filename in reading.files.items():
            first_section = sections(sources[filename])['F-01']
            booking = datetime.date.fromisoformat(re.search(r'booking for (\d{4}-\d\d-\d\d)', first_section)[1])
            note_dates = reading.note[ident][3]
            rows.append({'seed': seed, 'ident': ident, 'topic': topic, 'wrong': reading.own[ident] != reading.note[ident],
                         'note': notes[ident], 'dates': note_dates, 'transcribed': reading.transcribed[ident],
                         'booking': booking,
                         'middle': reading.checkpoints[ident][1],
                         'opened': datetime.date.fromisoformat(re.search(r'Opened by technician [A-Za-z ]+ on (\d{4}-\d\d-\d\d)', first_section)[1]),
                         'line_group': sum(text == notes[ident] for text in notes.values()),
                         'pair_distance': next((abs(int(ident[1:]) - int(other[1:])) for other in notes
                                                if other != ident and notes[other] == notes[ident]), None),
                         'line_shared': int(sum(text == notes[ident] for text in notes.values()) > 1),
                         'date_shared': int(any(dates[date] > 1 for date in note_dates))})
    return rows


class AttestedTests(unittest.TestCase):
    def test_documented_rules_are_verbatim_rendered_procedure(self):
        module = ast.parse((ROOT / 'evalsuite/lure_attested.py').read_text(encoding='utf-8'))
        documented = ast.get_docstring(module).split('Rule sentences, verbatim (procedure.md):\n', 1)[1].split('\n\n', 1)[0]
        rendered = sections(sources_of(case(1))['procedure.md'])['P-01'].split('\n', 1)[1]
        self.assertEqual(' '.join(documented.split()), ' '.join(rendered.split()))

    def test_whole_procedure_matches_reviewed_wording(self):
        for seed in range(1, 301):
            self.assertEqual(sources_of(case(seed))['procedure.md'],
                             REVIEWED_PROCEDURE + '\nFiled by the Merewick Facilities Office.\n')

    def test_whole_report_fixed_text_and_citations_are_neutral(self):
        for seed in range(1, 301):
            for variant in ('correct', 'mixed'):
                report = case(seed)['files']['report-' + variant + '.md']
                expected = '# Merewick Facilities Office — Examination review\n\n'
                for number, text in enumerate(report_claims(report), 1):
                    ident = Reading.parse(text)[0]
                    expected += ('<a id="C%02d"></a>\n**C%02d.** %s Citations: '
                                 'register G-%s; record-%s F-01, F-02; procedure P-01.\n\n'
                                 % (number, number, text, ident, ident[1:]))
                self.assertEqual(report, expected[:-1])

    def test_generated_checkpoint_equality_in_both_polarities(self):
        counts = collections.Counter()
        timing = 0
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            for row in built['key']['expectations']:
                ident, topic, positive, checkpoint = reading.parse(row['claim_text'])
                if topic != 'timing':
                    continue
                timing += 1
                if reading.own[ident][3] == (checkpoint,):
                    counts[positive] += 1
                    expected = 'supported' if positive else 'refuted'
                    self.assertEqual(reading.verdict(row['claim_text']), expected)
                    self.assertEqual(row['semantic_state'], expected)
        self.assertEqual(set(counts), {False, True})
        self.assertTrue(0.02 <= sum(counts.values()) / timing <= 0.20, counts)
        print('Attested equality rows:', dict(counts), 'of', timing)

    def test_reader_labels_are_family_names(self):
        for seed in range(1, 301):
            for row in case(seed)['key']['expectations']:
                if row['echo_state'] is not None:
                    self.assertEqual(row['echo_reader'], 'believes_status_note')
                self.assertLessEqual(set(row.get('lure_states', {})), {'believes_status_note'})

    def test_sixty_seeds_independent_keys_echoes_and_exact_slips(self):
        seen = collections.Counter()
        for seed in range(1, 61):
            built = case(seed)
            reading = Reading(sources_of(built))
            for row in built['key']['expectations']:
                text = row['claim_text']
                self.assertEqual(row['semantic_state'], reading.verdict(text), (seed, text))
                self.assertEqual(row['echo_state'], reading.echo(text), (seed, text))
                if row['echo_state'] is not None:
                    self.assertEqual(row['echo_reader'], 'believes_status_note')
                self.assertEqual(row['claim_kind'], 'check_' + reading.parse(text)[1])
                if row['semantic_state'] == 'supported':
                    self.assertIsNone(row['error_type'])
                else:
                    self.assertEqual(row['variant'], 'mixed')
                    self.assertEqual(row['error_type'], reading.slip(text), (seed, text))
                    seen[row['error_type']] += 1
                if row['variant'] == 'correct':
                    self.assertEqual(row['semantic_state'], 'supported')
        self.assertEqual(set(seen), {'settles_status_note_conflict', 'settles_unrecorded_date',
                                    'reverses_timing', 'reverses_result', 'reverses_reinspection'})

    def test_every_raw_slot_alternative_against_rendered_documents(self):
        count = 0
        for seed in range(1, 61):
            captured = []
            real = generate.family_builder
            def spy(name):
                builder = real(name)
                def capture(rng):
                    captured.append(builder(rng))
                    return captured[-1]
                return capture
            with mock.patch.object(generate, 'family_builder', spy):
                built = generate.build_case('attested', seed, 1)
            self.assertEqual(built, case(seed))
            family = captured[0]
            reading = Reading(sources_of(built))
            self.assertEqual(len(family['slots']), 8)
            self.assertTrue(all(alternatives for _, alternatives in family['slots']))
            for correct, alternatives in family['slots']:
                self.assertEqual(reading.verdict(correct['text']), 'supported')
                for alternative in alternatives:
                    count += 1
                    self.assertEqual(alternative['kind'], correct['kind'])
                    self.assertEqual(alternative['cites'], correct['cites'])
                    self.assertEqual(generate.judge(alternative, family['base'], family['unknowns'])[0],
                                     reading.verdict(alternative['text']))
                    self.assertIn(reading.verdict(alternative['text']), ('refuted', 'undetermined'))
                    self.assertEqual(alternative['error'], reading.slip(alternative['text']))
                    told = family.get('told')
                    if told:
                        self.assertEqual(generate.judge(alternative, told['base'], told['unknowns'])[0],
                                         reading.verdict(alternative['text'], note=True))
        self.assertGreaterEqual(count, 480)

    def test_preview_reports_match_actual_generator_selection(self):
        # This production import tests integration only, never Reading's rules.
        from lure_attested import preview_reports
        for seed in range(1, 61):
            previews = []
            real = generate.family_builder
            def spy(name):
                builder = real(name)
                def capture(rng):
                    family = builder(rng)
                    before = rng.getstate()
                    previews.append(preview_reports(family, rng))
                    self.assertEqual(rng.getstate(), before)
                    return family
                return capture
            with mock.patch.object(generate, 'family_builder', spy):
                built = generate.build_case('attested', seed, 1)
            for variant, items in zip(('correct', 'mixed'), previews[0]):
                self.assertEqual([item['text'] for item in items],
                                 report_claims(built['files']['report-' + variant + '.md']))
                self.assertTrue(3 <= sum(Reading.parse(item['text'])[2] for item in items) <= 5)

    def test_rendered_tamper_and_entry_order_change_the_reading(self):
        checked = set()
        for seed in range(1, 61):
            built = case(seed)
            sources = sources_of(built)
            for name, text in built['files'].items():
                self.assertNotIn('None', text, (seed, name))
            reading = Reading(sources)
            for row in built['key']['expectations']:
                if row['variant'] != 'correct':
                    continue
                text = row['claim_text']
                ident, topic, positive, checkpoint = reading.parse(text)
                if (topic, positive) in checked:
                    continue
                completed, passed, repeated, dates = reading.own[ident]
                if not completed:
                    continue
                filename = reading.files[ident]
                latest = reading.entries[ident][-1][1]
                if topic == 'result':
                    replacement = re.sub(r'(final result|latest examination): (pass|fail)\.',
                                         lambda m: m[1] + ': ' + ('fail' if passed else 'pass') + '.', latest)
                elif topic == 'reinspection':
                    replacement = re.sub(r'(counting from the first|repeats included): [12]\.',
                                         lambda m: m[1] + ': ' + ('1' if repeated else '2') + '.', latest)
                else:
                    replacement_date = checkpoint + datetime.timedelta(days=1) if positive else checkpoint
                    replacement = re.sub(r'Completion date of the latest examination: \d{4}-\d\d-\d\d, as transcribed on the visit sheet\.|Completion date of the latest examination: not recorded; one of [^.]+\.',
                                         'Completion date of the latest examination: ' + replacement_date.isoformat() + ', as transcribed on the visit sheet.', latest)
                self.assertNotEqual(replacement, latest)
                tampered = dict(sources, **{filename: sources[filename].replace(latest, replacement)})
                self.assertEqual(Reading(tampered).verdict(text), 'refuted', (topic, replacement))
                # Reversing physical paragraphs must not reverse the authority rule.
                checked.add((topic, positive))
        self.assertEqual(checked, {(topic, positive) for topic in ('timing', 'result', 'reinspection') for positive in (True, False)})

    def test_reversing_the_physical_order_of_entries_does_not_change_the_reading(self):
        swapped_files = 0
        for seed in range(1, 61):
            sources = sources_of(case(seed))
            reading = Reading(sources)
            for ident, filename in reading.files.items():
                texts = ['Entry dated ' + date + ' — ' + text for date, text in reading.entries[ident]]
                if len(texts) < 2:
                    continue
                block = '\n\n'.join(texts)
                self.assertIn(block, sources[filename])
                swapped = sources[filename].replace(block, '\n\n'.join(reversed(texts)))
                self.assertNotEqual(swapped, sources[filename])
                self.assertEqual(Reading(dict(sources, **{filename: swapped})).own, reading.own)
                swapped_files += 1
        self.assertGreater(swapped_files, 100)

    def test_300_builds_are_deterministic_and_different_seeds_differ(self):
        for seed in range(1, 301):
            self.assertEqual(case(seed), generate.build_case('attested', seed, 1))
        self.assertNotEqual(case(1)['files'], case(2)['files'])
        self.assertNotEqual(case(1, 1)['files'], case(1, 2)['files'])

    def test_source_files_are_substantial_without_artificial_padding(self):
        for seed in range(1, 301):
            sources = sources_of(case(seed))
            reading = Reading(sources)
            self.assertEqual(len(reading.own), 8)
            self.assertEqual(len(sources), 10)
            self.assertGreater(len({len(text.encode('utf-8')) for text in sources.values()}), 1)
            for text in sources.values():
                self.assertNotRegex(text, r' {2,}')
            self.assertGreaterEqual(sum(len(text.split()) for name, text in sources.items() if name != 'register.md'), 1500)
            for filename in reading.files.values():
                self.assertTrue(150 <= len(sources[filename].split()) <= 400)

    def test_about_thirty_percent_of_dossiers_have_no_note_discrepancy(self):
        conflicts = collections.Counter()
        for seed in range(1, 301):
            reading = Reading(sources_of(case(seed)))
            conflicts[sum(reading.own[i] != reading.note[i] for i in reading.own)] += 1
        self.assertEqual(set(conflicts), {0, 1, 2})
        self.assertTrue(0.22 <= conflicts[0] / 300 <= 0.38, conflicts)
        print('Attested 300-seed source conflicts:', dict(conflicts))

    def test_correct_supported_and_altered_reports_have_typed_errors(self):
        for seed in range(1, 61):
            rows = case(seed)['key']['expectations']
            for row in rows:
                if row['variant'] == 'correct':
                    self.assertEqual((row['semantic_state'], row['error_type']), ('supported', None))
                else:
                    self.assertEqual(row['error_type'] is not None, row['semantic_state'] != 'supported')
            self.assertGreaterEqual(sum(row['variant'] == 'mixed' and row['error_type'] is not None for row in rows), 2)

    def test_reports_preserve_claim_counts_kinds_and_unique_subjects(self):
        for seed in range(1, 301):
            built = case(seed)
            by_variant = {v: [r for r in built['key']['expectations'] if r['variant'] == v] for v in ('correct', 'mixed')}
            self.assertEqual(collections.Counter(r['claim_kind'] for r in by_variant['correct']),
                             collections.Counter(r['claim_kind'] for r in by_variant['mixed']))
            for variant, selected in by_variant.items():
                self.assertEqual(len(selected), 8)
                texts = report_claims(built['files']['report-' + variant + '.md'])
                self.assertEqual(len(texts), 8)
                self.assertEqual(len({Reading.parse(text)[0] for text in texts}), 8)

    def test_no_none_or_answer_markers_in_auditor_documents(self):
        for seed in range(1, 301):
            for name, text in case(seed)['files'].items():
                self.assertNotIn('None', text, (seed, name))
                self.assertNotRegex(text, r'completion code|result code|deadline code|\b(?:mixed|correct|lure|echo_state|error_type)\b')
                if name.startswith('report-'):
                    self.assertNotRegex(text.lower(), r'\b(?:exactly|total|number of|all inspections)\b')

    def test_audit_paragraph_split_and_citation_anchors(self):
        for seed in range(1, 61):
            built = case(seed)
            for variant in ('correct', 'mixed'):
                selected = [r for r in built['key']['expectations'] if r['variant'] == variant]
                ledger = run_blind.paragraphs(built['files']['report-' + variant + '.md'])
                self.assertEqual(ledger, audit.paragraphs(built['files']['report-' + variant + '.md']))
                anchors = run_blind.claim_anchors(ledger)
                self.assertEqual(set(anchors), {r['claim_id'] for r in selected})
                for row in selected:
                    self.assertIn(row['claim_text'], ledger[anchors[row['claim_id']]])
                    self.assertTrue(row['source_anchors'])
                    for anchor in row['source_anchors']:
                        filename, section = anchor.split('#')
                        self.assertIn('<a id="' + section + '"></a>', built['files'][filename])

    def test_every_lure_has_same_kind_same_polarity_nonlure_control(self):
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            for variant in ('correct', 'mixed'):
                selected = [r for r in built['key']['expectations'] if r['variant'] == variant]
                for row in selected:
                    text = row['claim_text']
                    if reading.echo(text) is not None:
                        controls = [other for other in selected if other['claim_id'] != row['claim_id']
                                    and other['claim_kind'] == row['claim_kind']
                                    and wording_shape(other['claim_text']) == wording_shape(text)
                                    and reading.echo(other['claim_text']) is None]
                        self.assertTrue(controls, (seed, variant, text))

    def test_300_seed_wording_shapes_have_both_truth_values(self):
        shapes = collections.defaultdict(set)
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            for row in built['key']['expectations']:
                shapes[(row['claim_kind'], wording_shape(row['claim_text']))].add(reading.verdict(row['claim_text']))
        self.assertEqual(len(shapes), 6)
        for shape, states in shapes.items():
            self.assertIn('supported', states, shape)
            self.assertTrue({'refuted', 'undetermined'} & states, shape)

    def test_r9_100_seeds_twelve_dossiers(self):
        counts, states, directions = [], collections.Counter(), collections.Counter()
        for seed in range(1, 101):
            count = 0
            for index in range(1, 13):
                built = case(seed, index)
                reading = Reading(sources_of(built))
                for row in built['key']['expectations']:
                    text = row['claim_text']
                    self.assertEqual(row['semantic_state'], reading.verdict(text))
                    self.assertEqual(row['echo_state'], reading.echo(text))
                    if row['echo_state'] is not None:
                        count += 1
                        states[row['semantic_state']] += 1
                        directions[(row['semantic_state'], row['echo_state'])] += 1
            counts.append(count)
        print('Attested R9 min/median/max:', min(counts), statistics.median(counts), max(counts),
              '; actual states:', dict(states), '; directions:', dict(directions))
        self.assertGreaterEqual(statistics.median(counts), 15)
        self.assertEqual(set(directions), {('supported', 'refuted'), ('refuted', 'supported'),
                                          ('undetermined', 'supported'), ('undetermined', 'refuted')})
        self.assertGreater(states['undetermined'], 0)
        self.assertGreater(states['supported'] + states['refuted'], 0)

    def test_report_and_source_surface_classifiers(self):
        """Predeclared 8-point tolerance; 150 training and 150 disjoint test seeds.

        This tolerates sampling noise. It does not prove absence of smaller or
        untested cues; all measured improvements and base rates are printed.
        """
        datasets = collections.defaultdict(list)
        for seed in range(1, 301):
            built = case(seed)
            sources = sources_of(built)
            reading = Reading(sources)
            conflict = any(reading.own[i] != reading.note[i] for i in reading.own)
            surface = tuple((name, len(text.encode('utf-8'))) for name, text in sorted(sources.items()))
            trimmed = tuple(len(text.rstrip().encode('utf-8')) // 64 for _, text in sorted(sources.items()))
            for variant in ('correct', 'mixed'):
                report = built['files']['report-' + variant + '.md']
                texts = report_claims(report)
                parsed = [Reading.parse(text) for text in texts]
                date_order = sorted({item[3] for item in parsed if item[3] is not None})
                histogram = tuple(sorted(collections.Counter((item[1], item[2]) for item in parsed).items()))
                features = {'whole_report': wording_shape(report),
                            'report_title_preamble': report.split('<a id=', 1)[0],
                            'report_histogram': histogram, 'report_length': len(report) // 32,
                            'register_collapsed_with_polarity': (parsed[0][1], sum(item[2] for item in parsed),
                                len(' '.join(sources['register.md'].split()).encode('utf-8'))),
                            'register_words_with_polarity': (parsed[0][1], sum(item[2] for item in parsed),
                                len(sources['register.md'].split())),
                            'source_names_lengths': surface, 'trimmed_source_lengths': trimmed,
                            'trimmed_source_total': sum(trimmed), 'trimmed_source_extrema': (min(trimmed), max(trimmed))}
                labels = {r['claim_text']: r for r in built['key']['expectations'] if r['variant'] == variant}
                for target, label in (('altered_report', variant == 'mixed'), ('dossier_conflict', conflict)):
                    for feature, value in features.items():
                        datasets[(target, feature)].append((seed, value, label))
                for position, (text, item) in enumerate(zip(texts, parsed)):
                    ident, topic, positive, date = item
                    linked = sources[reading.files[ident]]
                    claim_features = dict(features, wording=wording_shape(text),
                                          polarity_kind=(topic, positive), position=position,
                                          polarity_frequency=(topic, sum(p[1:3] == item[1:3] for p in parsed)),
                                          date_rank=(topic, positive, date_order.index(date) if date else -1),
                                          calendar_day=(topic, positive, date.day if date else -1),
                                          calendar_month=(topic, positive, date.month if date else -1),
                                          calendar_day_band=(topic, positive, (date.day - 1) // 5 if date else -1),
                                          linked_record_bytes=(topic, positive, len(linked.encode('utf-8'))),
                                          linked_record_trimmed_bytes=(topic, positive, len(linked.rstrip().encode('utf-8'))),
                                          linked_record_words=(topic, positive, len(linked.split())),
                                          linked_record_collapsed_bytes=(topic, positive, len(' '.join(linked.split()).encode('utf-8'))),
                                          linked_record_compact_bytes=(topic, positive, len(''.join(linked.split()).encode('utf-8'))),
                                          record_id=ident)
                    row = labels[text]
                    for target, label in (('wrong_claim', row['semantic_state'] != 'supported'),
                                          ('lure_claim', row['echo_state'] is not None)):
                        for feature, value in claim_features.items():
                            datasets[(target, feature)].append((seed, value, label))
        excesses = []
        for (target, feature), samples in sorted(datasets.items()):
            train = [row for row in samples if row[0] <= 150]
            test = [row for row in samples if row[0] > 150]
            totals = collections.Counter(row[2] for row in train)
            default = totals.most_common(1)[0][0]
            groups = collections.defaultdict(collections.Counter)
            for _, value, label in train:
                groups[value][label] += 1
            predicted = {value: count.most_common(1)[0][0] for value, count in groups.items()}
            accuracy = sum(predicted.get(value, default) == label for _, value, label in test) / len(test)
            baseline = max(collections.Counter(row[2] for row in test).values()) / len(test)
            print('Attested classifier', target, feature, 'accuracy/base/excess:',
                  round(accuracy, 4), round(baseline, 4), round(accuracy - baseline, 4))
            if accuracy - baseline > 0.08:
                excesses.append((target, feature, accuracy, baseline))
        self.assertEqual(excesses, [])

    def test_report_only_calendar_bins_do_not_decide_correctness(self):
        bins = collections.defaultdict(collections.Counter)
        for seed in range(1, 301):
            built = case(seed)
            for variant in ('correct', 'mixed'):
                texts = report_claims(built['files']['report-' + variant + '.md'])
                # Extract calendar features before attaching any key label.
                visible = [(text, Reading.parse(text)) for text in texts]
                labels = {row['claim_text']: row['semantic_state'] == 'supported'
                          for row in built['key']['expectations'] if row['variant'] == variant}
                for text, (_, topic, positive, date) in visible:
                    if topic != 'timing':
                        continue
                    self.assertEqual(date.year, 2034)
                    for feature in (('day', date.day), ('month', date.month),
                                    ('day_band_polarity', (date.day - 1) // 5, positive),
                                    ('month_polarity', date.month, positive)):
                        bins[feature][labels[text]] += 1
        decisive = {feature: dict(counts) for feature, counts in bins.items()
                    if sum(counts.values()) >= 20 and len(counts) == 1}
        self.assertEqual({feature[1] for feature in bins if feature[0] == 'day'}, set(range(1, 32)))
        self.assertEqual({feature[1] for feature in bins if feature[0] == 'month'}, set(range(1, 13)))
        print('Attested decisive report-only calendar bins:', decisive)
        self.assertEqual(decisive, {})

    def test_report_only_minority_polarity_does_not_decide_correctness(self):
        """A frequent minority shape must not certify correctness without files."""
        bins = collections.defaultdict(collections.Counter)
        for seed in range(1, 301):
            built = case(seed)
            for variant in ('correct', 'mixed'):
                texts = report_claims(built['files']['report-' + variant + '.md'])
                parsed = [Reading.parse(text) for text in texts]
                frequencies = collections.Counter((item[1], item[2]) for item in parsed)
                labels = {row['claim_text']: row['semantic_state'] == 'supported'
                          for row in built['key']['expectations'] if row['variant'] == variant}
                for text, item in zip(texts, parsed):
                    topic, positive = item[1:3]
                    feature = (topic, frequencies[(topic, positive)], frequencies[(topic, not positive)])
                    bins[feature][labels[text]] += 1
        decisive = {feature: dict(counts) for feature, counts in bins.items()
                    if sum(counts.values()) >= 20 and len(counts) == 1}
        print('Attested decisive report-only polarity bins:', decisive)
        self.assertEqual(decisive, {})

    def test_written_suite_loads_seals_stages_and_restating_does_not_leak(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='attested-tests-', dir=ROOT / 'tmp') as temporary:
            root = Path(temporary)
            fixtures, keys, arena, sealed = [root / name for name in ('fixtures', 'keys', 'arena', 'sealed')]
            summary = generate.write_suite(fixtures, keys, 9, 4, ['attested'])
            loaded = scorecard.load_keys(keys)
            self.assertEqual(set(loaded), set(summary['dossiers']))
            self.assertEqual(summary['claims'], 64)
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
                    self.assertEqual(len(list((run / 'case/sources').glob('*.md'))), 10)
                    self.assertNotIn(dossier, record['arena_run'])
                    claims = [row for row in loaded[dossier]['rows'] if row['variant'] == variant]
                    (run / 'notes.md').write_text('\n'.join(row['claim_text'] + ' Verdict: ' + row['semantic_state']
                                                          for row in claims), encoding='utf-8')
                    self.assertEqual(blindness.scan_run(run, loaded[dossier]['rows'])['verbatim_overlaps'], [])
            self.assertFalse(list(fixtures.rglob('*.json')))

    # ---------- every fixed sentence of every document is pinned ----------

    def assert_routing(self, content, context):
        if content == REVIEWED_NO_ROUTING:
            return
        sentences = content.split('\n\n')
        self.assertTrue(1 <= len(sentences) <= 3 and len(set(sentences)) == len(sentences), context)
        for sentence in sentences:
            self.assertIn(sentence, REVIEWED_ROUTING, context)

    @staticmethod
    def split_document(text):
        self_footer = text[-len(FOOTER):]
        assert self_footer == FOOTER
        title, rest = text[:-len(FOOTER)].rstrip('\n').split('\n\n', 1)
        parts = re.split(r'<a id="([^"]+)"></a>\n', rest)
        assert parts[0] == ''
        return title, [(anchor, body.rstrip('\n')) for anchor, body in zip(parts[1::2], parts[2::2])]

    def assert_register_pinned(self, text, used, context):
        title, parts = self.split_document(text)
        self.assertEqual(title, '# Merewick Facilities Office — Examination register', context)
        self.assertEqual([anchor for anchor, _ in parts], ['G-00'] + ['G-R0%d' % i for i in range(1, 9)] + ['G-09'], context)
        body = dict(parts)
        heading, _, intro = body['G-00'].partition('\n')
        self.assertEqual(heading, '## G-00 — Circulation', context)
        self.assertIsNotNone(slots_of(REVIEWED_REGISTER_INTRO, intro), (context, intro))
        for number in range(1, 9):
            location, asset, _ = REVIEWED_ASSETS[number - 1]
            heading, _, row = body['G-R0%d' % number].partition('\n')
            self.assertEqual(heading, '## G-R0%d — %s' % (number, location), context)
            slots = slots_of(REVIEWED_ROW, row)
            self.assertIsNotNone(slots, (context, row))
            self.assertEqual((slots['ident'], slots['asset'], slots['number']), ('R0%d' % number, asset, '0%d' % number), context)
            forms = [index for index, template in enumerate(REVIEWED_NOTES) if slots_of(template, slots['note']) is not None]
            self.assertEqual(len(forms), 1, (context, slots['note']))
            used['note form %d' % forms[0]] += 1
        heading, _, routing = body['G-09'].partition('\n')
        self.assertEqual(heading, '## G-09 — Office routing', context)
        self.assert_routing(routing, context)

    def assert_record_pinned(self, number, text, used, context):
        title, parts = self.split_document(text)
        self.assertEqual(title, '# Merewick Facilities Office — Inspection R0%d' % number, context)
        self.assertEqual([anchor for anchor, _ in parts], ['F-01', 'F-02', 'F-03'], context)
        body = dict(parts)
        heading, _, assignment = body['F-01'].partition('\n')
        self.assertEqual(heading, '## F-01 — Assignment and review checkpoints', context)
        slots = slots_of(REVIEWED_ASSIGNMENT, assignment)
        self.assertIsNotNone(slots, (context, assignment))
        self.assertEqual((slots['location'], slots['asset'], slots['parts']), REVIEWED_ASSETS[number - 1], context)
        self.assertEqual(slots['ident'], 'R0%d' % number, context)
        checkpoints = [slots['date%d' % i] for i in (3, 4, 5)]
        self.assertEqual(checkpoints, sorted(checkpoints), context)
        heading, _, entries = body['F-02'].partition('\n')
        self.assertEqual(heading, '## F-02 — Dated accounts', context)
        paragraphs = entries.split('\n\n')
        self.assertTrue(1 <= len(paragraphs) <= 3, context)
        previous = None
        for position, paragraph in enumerate(paragraphs):
            parts_ = entry_parts(paragraph)
            self.assertIsNotNone(parts_, (context, paragraph))
            self.assertIn(parts_['reason'], REVIEWED_REASONS, (context, paragraph))
            used[parts_['reason']] += 1
            self.assertLessEqual(len(parts_['tails']), 2, context)
            self.assertEqual(len(set(parts_['tails'])), len(parts_['tails']), context)
            for tail in parts_['tails']:
                self.assertIn(tail, REVIEWED_TAILS, (context, paragraph))
                used[tail] += 1
            if previous:
                self.assertLess(previous, parts_['date'], context)
            else:
                self.assertEqual(parts_['author'][0] in REVIEWED_TECHNICIANS, True, context)
                self.assertEqual(parts_['author'][0], slots['tech'], context)
            previous = parts_['date']
        heading, _, routing = body['F-03'].partition('\n')
        self.assertEqual(heading, '## F-03 — File circulation', context)
        self.assert_routing(routing, context)
        for sentence in re.split(r'\n\n', routing):
            used[sentence] += 1

    def test_every_fixed_sentence_of_every_document_is_pinned_word_for_word(self):
        """Findings F3: the register, every record section and the entries, with only named slots free."""
        used = collections.Counter()
        for seed in range(1, 301):
            sources = sources_of(case(seed))
            self.assertEqual(set(sources), {'procedure.md', 'register.md'} | {'record-0%d.md' % i for i in range(1, 9)})
            self.assert_register_pinned(sources['register.md'], used, 'seed %d register' % seed)
            for number in range(1, 9):
                self.assert_record_pinned(number, sources['record-0%d.md' % number], used, 'seed %d record %d' % (seed, number))
        # A reviewed sentence that is never produced would mean the inventory is out of step with the module.
        for sentence in REVIEWED_REASONS + REVIEWED_TAILS + REVIEWED_ROUTING:
            self.assertGreater(used[sentence], 0, sentence)
        self.assertEqual(sorted(key for key in used if key.startswith('note form')), ['note form 0', 'note form 1', 'note form 2'])

    # ---------- the length of a file carries no information about the facts in it ----------

    def test_sentences_that_carry_facts_have_one_size_whatever_the_facts(self):
        """Findings F1: read from the rendered text, every fact-bearing sentence of a kind has one size."""
        sizes, values, notes = collections.defaultdict(set), collections.defaultdict(set), set()
        for seed in range(1, 301):
            sources = sources_of(case(seed))
            for number in range(1, 9):
                text = sources['record-0%d.md' % number]
                for paragraph in sections(text)['F-02'].split('\n', 1)[1].strip().split('\n\n'):
                    parts = entry_parts(paragraph)
                    sizes['reason'].add(wb(parts['reason']))
                    for kind, templates in (('count', REVIEWED_COUNTS), ('result', REVIEWED_RESULTS), ('when', REVIEWED_DATES)):
                        sentence, slots = parts[kind]
                        index = [i for i, template in enumerate(templates) if slots_of(template, sentence) is not None][0]
                        # The two frames of a count or a result are separate sets; the three date forms share one set.
                        frame = 0 if kind == 'when' else index
                        value = ('one', 'open', 'none')[index] if kind == 'when' else slots['n'] if kind == 'count' else slots['r']
                        sizes[(kind, frame)].add(wb(sentence))
                        values[(kind, frame)].add(value)
            for ident, row in sections(sources['register.md']).items():
                if ident.startswith('G-R'):
                    notes.add(wb(row.split('Status note: ')[1].strip()))
        for key, found in sizes.items():
            self.assertEqual(len(found), 1, (key, found))
        self.assertEqual(sizes['reason'], {(94, 18)})
        for kind, expected in (('count', {'0', '1', '2'}), ('result', {'pass', 'fail', 'none'}), ('when', {'one', 'open', 'none'})):
            for frame in ((0, 1) if kind != 'when' else (0,)):
                self.assertEqual(values[(kind, frame)], expected, (kind, frame))
        self.assertEqual(len(notes), 1, notes)
        print('Attested fact-bearing sentence sizes (bytes, words):', {str(key): sorted(found)[0] for key, found in sizes.items()}, 'register note', sorted(notes))

    def test_offset_normalised_length_rules_trained_on_500_seeds_stay_at_base_rate(self):
        """Findings F1: the reviewer's method. Learn each record file's offset, then look up (topic, polarity,
        length residual) from seeds 1-500 and score it on seeds 501-1000, for five targets and three measures."""
        rows, files = [], {}
        for seed in range(1, 1001):
            built = case(seed)
            sources = sources_of(built)
            reading = Reading(sources)
            measures = {name: (len(text.encode('utf-8')), len(text.split()), len(''.join(text.split())))
                        for name, text in sources.items() if name.startswith('record-')}
            files[seed] = measures
            conflict = {ident for ident in reading.own if reading.own[ident] != reading.note[ident]}
            for variant in ('correct', 'mixed'):
                labels = {r['claim_text']: r for r in built['key']['expectations'] if r['variant'] == variant}
                for text in report_claims(built['files']['report-' + variant + '.md']):
                    ident, topic, positive, _ = Reading.parse(text)
                    row = labels[text]
                    rows.append({'seed': seed, 'variant': variant, 'ident': ident, 'topic': topic, 'positive': positive,
                                 'file': reading.files[ident], 'wrong': row['semantic_state'] != 'supported',
                                 'lure': row['echo_state'] is not None, 'conflict': ident in conflict,
                                 'ordinary': row['echo_state'] is None and row['semantic_state'] != 'undetermined'
                                 and row['error_type'] != 'settles_unrecorded_date'})
        results = {}
        for metric, label in ((0, 'bytes'), (1, 'words'), (2, 'non-space characters')):
            smallest = {}
            for seed in range(1, 501):
                for name, measure in files[seed].items():
                    smallest[name] = min(smallest.get(name, 10 ** 9), measure[metric])
            for coarse in (1, 16):
                residual = lambda r: (files[r['seed']][r['file']][metric] - smallest[r['file']]) // coarse
                feature = lambda r: (r['topic'], r['positive'], residual(r))
                train = [r for r in rows if r['seed'] <= 500]
                test = [r for r in rows if r['seed'] > 500]

                def fit(key, target, rows_):
                    groups = collections.defaultdict(collections.Counter)
                    for r in rows_:
                        groups[key(r)][target(r)] += 1
                    default = collections.Counter(target(r) for r in rows_).most_common(1)[0][0]
                    return lambda r: groups[key(r)].most_common(1)[0][0] if key(r) in groups else default

                def accuracy(predict, target, rows_):
                    base = max(collections.Counter(target(r) for r in rows_).values()) / len(rows_)
                    return sum(predict(r) == target(r) for r in rows_) / len(rows_), base

                name = '%s/%d' % (label, coarse)
                wrong = fit(feature, lambda r: r['wrong'], train)
                results[name, 'wrong claim'] = accuracy(wrong, lambda r: r['wrong'], test)
                results[name, 'ordinary wrong claim in altered reports'] = accuracy(
                    wrong, lambda r: r['wrong'], [r for r in test if r['variant'] == 'mixed' and r['ordinary']])
                results[name, 'lure claim'] = accuracy(fit(feature, lambda r: r['lure'], train), lambda r: r['lure'], test)
                reports = collections.defaultdict(list)
                for r in test:
                    reports[r['seed'], r['variant']].append(r)
                results[name, 'altered report'] = (sum(any(map(wrong, items)) == (variant == 'mixed')
                                                       for (_, variant), items in reports.items()) / len(reports), 0.5)
                record_key = lambda r: (r['file'], residual(r))
                seen_records = {}
                for r in rows:
                    seen_records[r['seed'], r['ident']] = r
                counts = collections.defaultdict(lambda: [0, 0])
                for r in seen_records.values():
                    if r['seed'] <= 500:
                        counts[record_key(r)][r['conflict']] += 1
                prior = sum(r['conflict'] for r in seen_records.values() if r['seed'] <= 500) / sum(1 for r in seen_records.values() if r['seed'] <= 500)

                def posterior(r):
                    absent, present = counts[record_key(r)]
                    return (present + 5 * prior) / (absent + present + 5)

                dossiers = collections.defaultdict(lambda: [False, 1.0])
                for r in seen_records.values():
                    if r['seed'] > 500:
                        dossiers[r['seed']][0] |= r['conflict']
                        dossiers[r['seed']][1] *= 1 - posterior(r)
                held = sum(truth for truth, _ in dossiers.values()) / len(dossiers)
                results[name, 'record misstated'] = accuracy(
                    lambda r: posterior(r) > 0.5, lambda r: r['conflict'], [r for r in seen_records.values() if r['seed'] > 500])
                results[name, 'dossier discrepancy'] = (sum(truth == (none_ < 0.5) for truth, none_ in dossiers.values()) / len(dossiers),
                                                        max(held, 1 - held))
        excesses = []
        for (name, target), (accuracy_, base) in sorted(results.items()):
            print('Attested residual rule', name, target, 'accuracy/base/excess:', round(accuracy_, 4), round(base, 4), round(accuracy_ - base, 4))
            if accuracy_ - base > {'altered report': 0.05, 'dossier discrepancy': 0.06}.get(target, 0.03):
                excesses.append((name, target, accuracy_, base))
        self.assertEqual(excesses, [])

    def test_mean_file_length_does_not_move_with_any_fact_of_the_record(self):
        """Findings F1, the direct form: file length (bytes and words) against each fact, by a two-sample z score.

        The length that the facts cannot explain is isolated first: the mean for the record number and for the
        number of entries, routing sentences, paperwork sentences and supervisor entries is taken out, so that
        what is left is mostly the length of names and the wording frames, which are drawn without the facts."""
        records = []
        for seed in range(1, 501):
            sources = sources_of(case(seed))
            reading = Reading(sources)
            for ident, filename in reading.files.items():
                text = sources[filename]
                stratum = (len(reading.entries[ident]), sum(s in text for s in REVIEWED_ROUTING),
                           sum(text.count(s) for s in REVIEWED_TAILS), text.count('depot supervisor'))
                records.append((ident, stratum, reading.own[ident], reading.note[ident],
                                {'bytes': len(text.encode('utf-8')), 'words': len(text.split())}))
        samples = collections.defaultdict(lambda: ([], []))
        for unit in ('bytes', 'words'):
            residual = [r[4][unit] for r in records]
            for _ in range(3):
                for key in (lambda r: r[0], lambda r: r[1]):
                    groups = collections.defaultdict(list)
                    for index, r in enumerate(records):
                        groups[key(r)].append(index)
                    for members in groups.values():
                        mean = sum(residual[i] for i in members) / len(members)
                        for i in members:
                            residual[i] -= mean
            spread = (sum(x * x for x in residual) / len(residual)) ** 0.5
            print('Attested length left after removing record number and neutral structure:', unit, 'sd', round(spread, 2))
            for index, (ident, stratum, own, note, _) in enumerate(records):
                for fact, value in (('completed', own[0]), ('passed', own[1]), ('re-inspected', own[2]), ('date open', len(own[3]) == 2),
                                    ('note completed', note[0]), ('note passed', note[1]), ('note re-inspected', note[2]),
                                    ('note misstates', own != note), ('note misstates a date', own[3] != note[3])):
                    samples[fact, unit][bool(value)].append(residual[index])
        worst = 0.0
        for (fact, unit), (false, true) in sorted(samples.items()):
            mean = lambda xs: sum(xs) / len(xs)
            variance = lambda xs: sum((x - mean(xs)) ** 2 for x in xs) / (len(xs) - 1)
            z = (mean(true) - mean(false)) / (variance(true) / len(true) + variance(false) / len(false)) ** 0.5
            worst = max(worst, abs(z))
            self.assertLess(abs(z), 4.0, (fact, unit, z))
        print('Attested largest |z| of file length against any fact:', round(worst, 2))

    # ---------- no per-record source feature depends on whether the note misstates the record ----------

    def test_record_file_features_are_independent_of_note_misstatement(self):
        """Findings F2: chi-square of each per-record source feature against 'the note misstates this record'.

        Excluded on purpose: the reason and fact sentences, which describe the facts and so legitimately
        differ between records whose notes are stale and records whose notes are right."""
        facts = re.compile(r'(Completed examinations|Examinations of this asset|Result of the latest|Operating checks, final|Completion date)')
        categories = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
        presence = collections.defaultdict(lambda: [0, 0])
        totals = [0, 0]
        for seed in range(1, 301):
            sources = sources_of(case(seed))
            reading = Reading(sources)
            for ident, filename in reading.files.items():
                text = sources[filename]
                mis = int(reading.own[ident] != reading.note[ident])
                totals[mis] += 1
                body = ' '.join(line for line in text.split('\n') if line and not line.startswith(('#', '<a id')))
                found = set()
                for sentence in re.split(r'(?<=\.) (?=[A-Z])', body):
                    sentence = re.sub(r'\d{4}-\d\d-\d\d', '<date>', re.sub(r'R0[1-8]', '<id>', sentence))
                    sentence = re.sub(r'^Entry dated <date> — [^.]+\. ', '', sentence)
                    if sentence not in REVIEWED_REASONS and not facts.match(sentence):
                        found.add(sentence)
                for sentence in found:
                    presence[sentence][mis] += 1
                for kind, value in (('entries', len(reading.entries[ident])),
                                    ('routing sentences', sum(s in text for s in REVIEWED_ROUTING)),
                                    ('tail sentences', sum(text.count(s) for s in REVIEWED_TAILS)),
                                    ('supervisor entries', text.count('depot supervisor')),
                                    ('blank-line runs', text.count('\n\n\n')),
                                    ('trailing newlines', len(text) - len(text.rstrip('\n'))),
                                    ('bytes per 150', len(text.encode('utf-8')) // 150),
                                    ('words per 25', len(text.split()) // 25)):
                    categories[kind][value][mis] += 1

        def chi_square(table):
            """Excess of the statistic over its critical value at about p = 3e-5 (Wilson-Hilferty); positive means dependence."""
            columns, total = [sum(row[i] for row in table.values()) for i in (0, 1)], sum(map(sum, table.values()))
            value = sum((row[i] - sum(row) * columns[i] / total) ** 2 / (sum(row) * columns[i] / total)
                        for row in table.values() for i in (0, 1) if sum(row) and columns[i])
            df = max(1, len([row for row in table.values() if sum(row)]) - 1)
            critical = df * (1 - 2 / (9 * df) + 4.0 * (2 / (9 * df)) ** 0.5) ** 3
            return value - critical, value, df

        results = {kind: chi_square(table) for kind, table in categories.items()}
        for sentence, present in presence.items():
            if sum(present) not in (0, sum(totals)) and sum(present) >= 30:
                results['sentence: ' + sentence[:60]] = chi_square({'present': present, 'absent': [totals[0] - present[0], totals[1] - present[1]]})
        for name, (excess, value, df) in sorted(results.items(), key=lambda item: -item[1][0])[:8]:
            print('Attested independence chi-square', name, 'statistic', round(value, 2), 'df', df, 'excess over critical', round(excess, 2))
        self.assertGreater(len(results), 40)
        self.assertEqual({name: value for name, (excess, value, df) in results.items() if excess > 0}, {})

    # ---------- realism ----------

    def test_files_vary_in_entries_people_gaps_and_transcription_dates(self):
        entries, openers, authors, gaps, lags = collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter()
        for seed in range(1, 301):
            sources = sources_of(case(seed))
            reading = Reading(sources)
            for ident, filename in reading.files.items():
                dated = [datetime.date.fromisoformat(d) for d, _ in reading.entries[ident]]
                entries[len(dated)] += 1
                for later, earlier in zip(dated[1:], dated):
                    gaps[(later - earlier).days] += 1
                lags[(reading.transcribed[ident] - dated[-1]).days] += 1
                header = re.findall(r'Entry dated \d{4}-\d\d-\d\d — ([^,]+), ([^.]+)\.', sources[filename])
                openers[header[0][0]] += 1
                for person in header:
                    authors[person] += 1
        total = sum(entries.values())
        print('Attested entries per file:', dict(sorted(entries.items())), '; gaps in days (distinct):', len(gaps),
              '; transcription minus latest entry, days (distinct):', len(lags), '; authors:', dict(authors))
        self.assertEqual(set(entries), {1, 2, 3})
        for count in entries.values():
            self.assertTrue(0.15 <= count / total <= 0.5, entries)
        self.assertEqual(set(authors), REVIEWED_AUTHORS)
        self.assertEqual(set(openers), set(REVIEWED_TECHNICIANS))
        self.assertGreaterEqual(len(gaps), 6)
        self.assertLess(max(gaps.values()) / sum(gaps.values()), 0.4)
        self.assertGreaterEqual(len(lags), 8)
        self.assertLess(max(lags.values()) / sum(lags.values()), 0.4)
        self.assertTrue(min(lags) < 0 < max(lags))

    def test_later_entries_say_what_happened_in_their_own_words(self):
        for seed in range(1, 301):
            sources = sources_of(case(seed))
            reading = Reading(sources)
            for ident, filename in reading.files.items():
                self.assertNotRegex(sections(sources[filename])['F-02'], r'(?i)replac|supersed|overrid|intake')
                bodies = [re.sub(r'^[^.]+\. ', '', text) for _, text in reading.entries[ident]]
                self.assertEqual(len(set(bodies)), len(bodies), (seed, ident))
                self.assertNotRegex(sources['register.md'], r'(?i)intake|stale|wrong')

    def test_having_a_later_entry_is_independent_of_the_note_being_wrong(self):
        mis, echo = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
        transcribed = collections.Counter()
        single_wrong = duplicate_wrong = 0
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            lures = {Reading.parse(r['claim_text'])[0] for r in built['key']['expectations'] if r['echo_state'] is not None}
            texts = {}
            for anchor, body in sections(sources_of(built)['register.md']).items():
                if anchor.startswith('G-R'):
                    texts[anchor[2:]] = body.split('Status note: ')[1].strip()
            for ident in reading.own:
                entries = len(reading.entries[ident])
                wrong = reading.own[ident] != reading.note[ident]
                mis[entries][wrong] += 1
                echo[entries][ident in lures] += 1
                before = reading.transcribed[ident] < datetime.date.fromisoformat(max(d for d, _ in reading.entries[ident]))
                transcribed[(wrong, before)] += 1
                single_wrong += wrong and entries == 1
                duplicate_wrong += wrong and not texts[ident].startswith('Awaiting') and list(texts.values()).count(texts[ident]) > 1
        overall = sum(c[True] for c in mis.values()) / sum(sum(c.values()) for c in mis.values())
        for entries in (1, 2, 3):
            share = mis[entries][True] / sum(mis[entries].values())
            print('Attested entries', entries, 'P(note wrong) %.3f' % share, 'P(a lure claim on the record) %.3f' % (echo[entries][True] / sum(echo[entries].values())))
            self.assertLess(abs(share - overall), 0.05, (entries, share, overall))
        print('Attested note dated before the latest entry: wrong', transcribed[(True, True)], 'of', transcribed[(True, True)] + transcribed[(True, False)],
              '; right', transcribed[(False, True)], 'of', transcribed[(False, True)] + transcribed[(False, False)],
              '; wrong notes on single-entry files', single_wrong, '; wrong notes that repeat another row', duplicate_wrong)
        self.assertGreater(single_wrong, 100)
        self.assertGreater(transcribed[(True, True)], 100)
        self.assertGreater(transcribed[(True, False)], 100)
        self.assertGreater(transcribed[(False, True)], 100)
        self.assertGreater(duplicate_wrong, 20)

    def test_passed_reinspected_and_on_time_are_drawn_independently(self):
        joint = collections.Counter()
        for seed in range(1, 301):
            reading = Reading(sources_of(case(seed)))
            for ident, (completed, passed, repeated, dates) in reading.own.items():
                if completed and len(dates) == 1:
                    joint[(passed, repeated, dates[0] <= reading.checkpoints[ident][1])] += 1
        total = sum(joint.values())
        self.assertEqual(len(joint), 8)
        for combination, count in sorted(joint.items()):
            print('Attested uncoupled facts (passed, re-inspected, on time at the middle checkpoint):', combination, round(count / total, 3))
            self.assertGreater(count / total, 0.07, combination)
        for axis in range(3):
            for other in range(3):
                if axis < other:
                    both = sum(c for k, c in joint.items() if k[axis] and k[other])
                    first = sum(c for k, c in joint.items() if k[axis])
                    second = sum(c for k, c in joint.items() if k[other])
                    self.assertLess(abs(both / second - first / total), 0.04, (axis, other))

    def test_wrong_timing_claims_use_all_three_checkpoints(self):
        """Findings F4: the checkpoint says nothing about whether a claim is right."""
        positions = collections.defaultdict(collections.Counter)
        for seed in range(1, 301):
            built = case(seed)
            reading = Reading(sources_of(built))
            for row in built['key']['expectations']:
                ident, topic, _, checkpoint = Reading.parse(row['claim_text'])
                if topic == 'timing':
                    positions[reading.checkpoints[ident].index(checkpoint)][row['semantic_state'] != 'supported'] += 1
        shares = {p: counts[True] / sum(counts.values()) for p, counts in positions.items()}
        print('Attested share of wrong timing claims by checkpoint (earliest, middle, latest):', {p: round(v, 3) for p, v in sorted(shares.items())},
              {p: sum(c.values()) for p, c in sorted(positions.items())})
        self.assertEqual(set(positions), {0, 1, 2})
        for count in positions.values():
            self.assertGreater(count[True], 40)
        self.assertLess(max(shares.values()) - min(shares.values()), 0.06)

    # ---------- the register says nothing about which notes are wrong (third fix round) ----------

    def test_every_note_is_copied_after_every_date_it_states(self):
        """Findings G1: a true note cannot state an inspection on or after the day it was copied."""
        smallest, checked = None, 0
        for row in survey():
            for date in row['dates']:
                gap = (row['transcribed'] - date).days
                self.assertGreaterEqual(gap, 2, (row['seed'], row['ident'], row['note']))
                smallest = gap if smallest is None else min(smallest, gap)
                checked += 1
            if not row['dates']:  # an awaiting note states no date; the visit it records was the booking
                self.assertGreaterEqual((row['transcribed'] - row['booking']).days, 2, (row['seed'], row['ident']))
        print('Attested smallest copy date minus stated date, days:', smallest, 'over', checked, 'stated dates')
        self.assertGreater(checked, 5000)

    def test_register_only_date_arithmetic_guesser_stays_near_the_base_rate(self):
        """Findings G1: the best threshold rule on the register's dates, trained on seeds 1-500 and scored on 501-1000,
        for "this record's note is wrong". Precision and recall of the rule and the AUC of each date difference are
        printed; a rule that flags a tenth of the notes must not be right about two thirds of them."""
        # A note with two dates is never wrong (a form cue, stated in the docstring), so its first date is left out.
        features = {'copy date minus last stated date': (lambda row: (row['transcribed'] - row['dates'][-1]).days, lambda row: bool(row['dates'])),
                    'copy date minus the stated date': (lambda row: (row['transcribed'] - row['dates'][0]).days, lambda row: len(row['dates']) == 1)}
        for name, (feature, eligible) in features.items():
            rows = [row for row in survey() if eligible(row)]
            train = [row for row in rows if row['seed'] <= 500]
            test = [row for row in rows if row['seed'] > 500]
            base = sum(row['wrong'] for row in test) / len(test)
            best = None
            for threshold in sorted({feature(row) for row in train}):
                for below in (True, False):
                    flagged = [row for row in train if (feature(row) <= threshold) == below]
                    if len(flagged) < 0.03 * len(train) or len(flagged) > 0.97 * len(train):
                        continue
                    precision = sum(row['wrong'] for row in flagged) / len(flagged)
                    if best is None or precision > best[0]:
                        best = (precision, threshold, below)
            _, threshold, below = best
            flagged = [row for row in test if (feature(row) <= threshold) == below]
            precision = sum(row['wrong'] for row in flagged) / len(flagged)
            recall = sum(row['wrong'] for row in flagged) / sum(row['wrong'] for row in test)
            score = auc([feature(row) for row in test], [row['wrong'] for row in test])
            print('Attested register-only rule', name, '<=' if below else '>', threshold, 'precision %.3f recall %.3f base %.3f AUC %.3f'
                  % (precision, recall, base, score))
            self.assertLess(precision, base + 0.12, name)
            self.assertLess(abs(score - 0.5), 0.05, name)

    def test_trusting_the_note_but_flipping_it_for_an_impossible_date_gains_nothing(self):
        """Findings G1: "trust the note, but flip it when its date is on or after its copy day" against the plain
        note-trusting reader, the flip threshold learned on seeds 1-500 and scored on 501-1000."""
        scored = []
        for seed in range(1, 1001):
            built = case(seed)
            reading = Reading(sources_of(built))
            for row in built['key']['expectations']:
                told = reading.verdict(row['claim_text'], note=True)
                if told == 'undetermined' or row['semantic_state'] == 'undetermined':
                    continue
                ident = Reading.parse(row['claim_text'])[0]
                dates = reading.note[ident][3]
                gap = (reading.transcribed[ident] - dates[0]).days if dates else 99
                scored.append((seed, gap, told == row['semantic_state']))

        def flipped_right(rows, threshold):
            return sum(right != (gap <= threshold) for _, gap, right in rows) / len(rows)

        train = [row for row in scored if row[0] <= 500]
        test = [row for row in scored if row[0] > 500]
        never = -10 ** 6
        threshold = max([never] + list(range(-30, 40)), key=lambda value: flipped_right(train, value))
        plain, flip = flipped_right(test, never), flipped_right(test, threshold)
        print('Attested plain note-trusting reader %.4f; flipping when copy date - note date <= %s: %.4f'
              % (plain, 'never' if threshold == never else threshold, flip))
        self.assertLess(flip - plain, 0.01)

    def test_diary_booking_is_the_first_date_the_note_states(self):
        """Findings G1: the booking and the note never differ in one kind of row only."""
        dated = [row for row in survey() if row['dates']]
        self.assertGreater(len(dated), 5000)
        for row in dated:
            self.assertEqual(row['booking'], row['dates'][0], (row['seed'], row['ident']))

    def test_note_dates_sit_alike_around_checkpoints_and_opening_for_right_and_wrong_notes(self):
        """Findings G1: AUC of each date difference in the register and F-01 (single-date notes), for "note wrong"."""
        rows = [row for row in survey() if len(row['dates']) == 1]
        differences = {'note date minus middle checkpoint': lambda row: (row['dates'][0] - row['middle']).days,
                       'distance of the note date from the middle checkpoint': lambda row: abs((row['dates'][0] - row['middle']).days),
                       'note date minus opening date': lambda row: (row['dates'][0] - row['opened']).days,
                       'copy date minus middle checkpoint': lambda row: (row['transcribed'] - row['middle']).days,
                       'copy date minus last stated date': lambda row: (row['transcribed'] - row['dates'][0]).days}
        for name, feature in differences.items():
            score = auc([feature(row) for row in rows], [row['wrong'] for row in rows])
            print('Attested AUC of', name, 'for a wrong note: %.3f' % score)
            self.assertLess(abs(score - 0.5), 0.045, name)

    def test_repeated_lines_and_shared_dates_occur_alike_among_right_and_wrong_notes(self):
        """Findings G1: a repeated line must not be the only duplicate in a register. For single-date notes, by topic,
        the chance that the line, or only the date, is shared with another row of the register."""
        counts = collections.defaultdict(lambda: [0, 0, 0])
        for row in survey():
            if len(row['dates']) != 1:
                continue
            for topic in (row['topic'], 'all'):
                entry = counts[(topic, row['wrong'])]
                entry[0] += 1
                entry[1] += row['line_shared']
                entry[2] += row['date_shared']
        for topic, bound in (('all', 0.04), ('result', 0.08), ('reinspection', 0.08), ('timing', 0.02)):
            for position, name in ((1, 'line'), (2, 'date')):
                wrong, right = (counts[(topic, flag)][position] / counts[(topic, flag)][0] for flag in (True, False))
                print('Attested share of single-date notes whose', name, 'is shared,', topic, ': wrong %.3f right %.3f' % (wrong, right))
                self.assertLess(abs(wrong - right), bound, (topic, name))
        # Each repeated line is a pair: a wrong note repeats one row's line and no other row repeats that row's.
        dated = [row for row in survey() if len(row['dates']) == 1]
        groups = collections.Counter((row['wrong'], min(row['line_group'], 3)) for row in dated)
        triples = {flag: groups[(flag, 3)] / sum(groups[(flag, size)] for size in (1, 2, 3)) for flag in (True, False)}
        print('Attested share of single-date notes whose line is in a group of three or more rows:', triples)
        self.assertLess(max(triples.values()), 0.005)
        # The two rows of a repeated line are neighbours in the register, for a copy and for two right notes alike.
        adjacent = {flag: [row['pair_distance'] == 1 for row in dated if row['line_group'] == 2 and row['wrong'] == flag]
                    for flag in (True, False)}
        shares = {flag: sum(values) / len(values) for flag, values in adjacent.items()}
        print('Attested share of repeated single-date lines whose twin row is adjacent: wrong %.3f right %.3f (of %d and %d)'
              % (shares[True], shares[False], len(adjacent[True]), len(adjacent[False])))
        self.assertLess(abs(shares[True] - shares[False]), 0.10)
        self.assertGreater(min(shares.values()), 0.8)
        self.assertGreater(counts[('result', False)][1] / counts[('result', False)][0], 0.2)
        self.assertGreater(counts[('result', True)][1] / counts[('result', True)][0], 0.2)

    def test_a_first_entry_that_records_two_examinations_says_so_and_a_missing_date_has_a_reason(self):
        """Realism slips: no first entry says "I examined the asset" over two completed examinations, and no entry
        that examines the asset without recording its date leaves the date unexplained."""
        silent = set(REVIEWED_REASONS_FIRST_ONE + REVIEWED_REASONS_FIRST_TWO + REVIEWED_REASONS_REPEAT + REVIEWED_REASONS_COMPLETED)
        seen = collections.Counter()
        for seed in range(1, 301):
            sources = sources_of(case(seed))
            for number in range(1, 9):
                paragraphs = sections(sources['record-0%d.md' % number])['F-02'].split('\n', 1)[1].strip().split('\n\n')
                for position, paragraph in enumerate(paragraphs):
                    parts = entry_parts(paragraph)
                    count = int(parts['count'][1]['n'])
                    open_date = slots_of(REVIEWED_DATES[1], parts['when'][0]) is not None
                    reason = parts['reason']
                    if position == 0 and count == 2:
                        self.assertIn(reason, REVIEWED_REASONS_FIRST_TWO + REVIEWED_REASONS_FIRST_TWO_OPEN, (seed, paragraph))
                        seen['first entry, two examinations'] += 1
                    if position == 0 and count == 1:
                        self.assertIn(reason, REVIEWED_REASONS_FIRST_ONE + REVIEWED_REASONS_FIRST_OPEN, (seed, paragraph))
                    if open_date and reason in silent:
                        self.fail((seed, paragraph))
                    if open_date and reason in REVIEWED_REASONS_DATE_MISSING:
                        seen['examination without a date, with a reason'] += 1
        print('Attested first entries with two examinations and entries with an explained missing date:', dict(seen))
        self.assertGreater(seen['first entry, two examinations'], 50)
        self.assertGreater(seen['examination without a date, with a reason'], 50)

    def test_the_first_section_does_not_promise_findings_it_does_not_hold(self):
        for seed in range(1, 61):
            for number in range(1, 9):
                first = sections(sources_of(case(seed))['record-0%d.md' % number])['F-01']
                self.assertNotIn('findings', first.lower(), (seed, number))


if __name__ == '__main__':
    unittest.main()
