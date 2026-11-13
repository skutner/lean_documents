"""Independent Markdown derivation and integration checks for uncited sheets.

Reading imports neither the family nor its constants or comparison function.
It parses all readings and local ceilings, then reads the quantified claim.
The same parser restricts its view to the union of report citations for echo.
Cases are shared across checks; disk tests use auto-removed directories in tmp.

The cited-sheet attacks (a reader who opens only the sheets a report cites and
computes the flawed reader's verdicts itself) are written from rendered Markdown
and do not need the settling rule, so they also run against an older build.
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
import generate
generate.HARD_FAMILIES.setdefault('uncited', 'lure_uncited')
import blindness
import run_blind
import scorecard

MONTH_NAMES = ('April', 'May', 'June', 'July', 'August', 'September')


@functools.lru_cache(maxsize=600)
def case(seed):
    return generate.build_case('uncited', seed, 1)


def sources_of(built):
    return {name[8:]: body for name, body in built['files'].items() if name.startswith('sources/')}


def report_claims(text):
    return re.findall(r'\*\*C\d+\.\*\* (.*?) Citations?:', text)


def shape(text):
    text = re.sub(r'April|May|June|July|August|September', 'MONTH', text)
    return re.sub(r'\d+', 'N', text)


# Reviewed wording of the whole settling document, kept here and never taken from
# the module at run time. A person reviews it once; any other sentence, in any
# section, is a change to the rule and must break the tests.
REVIEWED_PROCEDURE = """# Inspection register procedure

<a id="P-01"></a>
## P-01 — Scope and comparison
Merehaven Estates, Maintenance Assurance Office. Issued 02 April 2034.

The inspection register consists of every numbered site sheet supplied in this packet. Each sheet records all three inspection readings and the applicable operating ceiling for each month shown. A site exceeded its operating ceiling in a month if and only if at least one of its three readings for that month is strictly greater than that month's ceiling on its own sheet. A reading equal to the ceiling is not an exceedance. A monthly count of sites exceeding their ceilings counts each site once, even when several readings on that site exceed its ceiling. A statement about sites in a month ranges over every site in the register, not just the sites cited beside the statement. A finding that no site exceeded its ceiling requires checking every site's sheet for that month. Citations are navigation aids and do not restrict the register's scope.

<a id="P-02"></a>
## P-02 — Circulation and use
Added by the records coordinator on 02 October 2034 for the autumn maintenance meeting. The sheet references listed beside each statement in the review are the returns for which the plant caretaker telephoned the office during the six months. A caretaker does not telephone for every reading above a ceiling, so the list shows where the office was already alerted; it is not an index of findings. The office keeps the individual site returns as separate files because each caretaker receives only the return for the plant under their care. For the meeting, the records team brought all of those returns together in this packet. The telephone list was useful when preparing the review, but the meeting needs a finding about the full register.

The measurements concern ventilation plant, not the quality of occupied-room air. Care staff use the record to plan filter purchases and access visits. Different installations have different operating ceilings; a high absolute pressure at one installation does not by itself establish a maintenance exception at that installation. The site return keeps the ceiling beside the visit account so that equipment with a different setting can be reviewed without relying on a remembered estate-wide figure. The monthly review covers the three recorded inspection readings only. It makes no statement about pressure between visits. Follow-up work, if needed, is arranged separately and is not recorded as another inspection reading in this register.
"""

# Everything in a report that is not a claim text, written out for review: the
# title, the blank lines, the anchors, the numbering, the word "Citations" and
# the form of each citation. The month of a claim fixes the anchor it cites.
REVIEWED_REPORT_TITLE = '# Monthly ventilation inspection review\n'
REVIEWED_MONTH_ANCHORS = {'April': 'M-04', 'May': 'M-05', 'June': 'M-06',
                          'July': 'M-07', 'August': 'M-08', 'September': 'M-09'}
FORM = re.compile(
    r'During the recorded (April|May|June|July|August|September) 2034 inspections, '
    r'the number of sites that exceeded their operating ceilings was (at most )?(\d+)\.')
STRICT_CLAIM = FORM

# Everything that is fixed in a site sheet, written out for review. Only numbers and the choice
# between the listed alternatives may differ from one sheet or visit to the next.
REVIEWED_CUSTODY = (
    'Merehaven Estates, Site Services Office. Site {number:03d}. '
    'Return closed 30 September 2034 by the duty plant caretaker. '
    'This sheet accompanies the maintenance access request for the next quarter. '
    'The inspection port is inside the plant enclosure; staff read the gauge after '
    'the fan has settled and before opening the filter cabinet. The readings below '
    'are retained for the assurance meeting as well as for the caretaker taking '
    'over the next shift. Ceiling settings belong to this installation and are '
    'copied from its commissioning card into each visit account. The card has not '
    'been reissued since commissioning, so the same ceiling applies in every month.')
REVIEWED_CEILING_SENTENCES = (
    'The operating ceiling for {month} was {ceiling} Pa, as recorded on the commissioning card.',
    'On the commissioning card, the operating ceiling for {month} was recorded as {ceiling} Pa.',
    'The commissioning card recorded {ceiling} Pa as the operating ceiling in force in {month}.',
)
REVIEWED_RECORD = (
    'On {day} {month} 2034, the pressure readings at {t1}, {t2} and {t3} '
    'were {a} Pa, {b} Pa and {c} Pa respectively. '
    'The caretaker took these readings at the same port with the plant running. ')
REVIEWED_NOTES = (
    'The access visit also included a visual inspection of the cabinet seal; '
    'the pressure record was transcribed before the enclosure was closed.',
    'The filter cabinet was opened only after the readings had been taken, '
    'and the rest of the access visit was routine.',
    'The caretaker found the seal and the filter housing as at the previous visit.',
    'The visit was combined with the monthly check of the fan belts, which was done after the readings.',
)
REVIEWED_TIMES = (('08:00', '12:00', '16:00'), ('07:30', '11:30', '15:30'),
                  ('09:00', '13:00', '17:00'), ('08:30', '12:30', '16:30'))
REVIEWED_DAYS = range(14, 25)


def reviewed_report(report):
    """The report the reviewed wording prescribes for these claims and this sample.

    Only the claim texts and the four cited sheet numbers are taken from the report
    itself (the sample is drawn per dossier, so its numbers cannot be written out).
    The sample must be four distinct ascending three-digit numbers, and every claim
    of the report must cite exactly that sample.
    """
    found = list(STRICT_CLAIM.finditer(report))
    cited = re.findall(r'sheet-(\d{3}) ', report.split('Citations: ', 1)[-1].split('\n', 1)[0])
    assert len(cited) == 4 and cited == sorted(set(cited)), cited
    blocks = [REVIEWED_REPORT_TITLE]
    for number, match in enumerate(found, 1):
        anchor = REVIEWED_MONTH_ANCHORS[match[1]]
        citations = 'procedure P-01; ' + '; '.join('sheet-{} {}'.format(sheet, anchor) for sheet in cited) + '.'
        blocks.append('<a id="C{0:02d}"></a>\n**C{0:02d}.** {1} Citations: {2}\n'.format(
            number, match[0], citations))
    return '\n'.join(blocks)


def procedure_sections(text):
    """Map each section anchor to its sentences, splitting after full stops."""
    parts = re.split(r'<a id="(P-\d\d)"></a>\n## [^\n]+\n', text)
    assert parts[0].startswith('# ') and parts[0].strip().count('\n') == 0, parts[0]
    return {anchor: re.split(r'(?<=\.)\s+', body.strip()) for anchor, body in zip(parts[1::2], parts[2::2])}


# ---------- the reviewed layout of a site sheet ----------

def render_month(visit):
    ceiling = REVIEWED_CEILING_SENTENCES[visit['phrase']].format(month=visit['month'], ceiling=visit['ceiling'])
    a, b, c = visit['readings']
    t1, t2, t3 = REVIEWED_TIMES[visit['times']]
    record = REVIEWED_RECORD.format(day=visit['day'], month=visit['month'], t1=t1, t2=t2, t3=t3, a=a, b=b, c=c)
    record += REVIEWED_NOTES[visit['note']]
    return ceiling + '\n\n' + record if visit['ceiling_first'] else record + '\n\n' + ceiling


def render_sheet(number, visits):
    sections = ['<a id="S-01"></a>\n## S-01 — Custody and purpose\n{}\n'.format(REVIEWED_CUSTODY.format(number=number))]
    for index, visit in enumerate(visits):
        sections.append('<a id="M-{0:02d}"></a>\n## M-{0:02d} — {1} visit\n{2}\n'.format(
            index + 4, visit['month'], render_month(visit).strip()))
    return '# Site {0:03d} inspection return\n\n'.format(number) + '\n'.join(sections)


def parse_sheet(text):
    """Read a sheet into its number and its six visits, or fail on any text not in the reviewed layout."""
    head = re.match(r'# Site (\d{3}) inspection return\n\n', text)
    assert head, text[:60]
    parts = re.split(r'(?=<a id="M-0\d"></a>\n)', text)
    assert len(parts) == 7, len(parts)
    visits = []
    for index, part in enumerate(parts[1:]):
        found = re.fullmatch(r'<a id="M-0(\d)"></a>\n## M-0\d — (\w+) visit\n(.*)\n+', part, re.S)
        assert found and int(found[1]) == index + 4 and found[2] == MONTH_NAMES[index], part[:80]
        paragraphs = found[3].strip().split('\n\n')
        assert len(paragraphs) == 2, paragraphs
        record_first = bool(re.match(r'On \d\d ' + found[2] + ' 2034', paragraphs[0]))
        record, ceiling = (paragraphs[0], paragraphs[1]) if record_first else (paragraphs[1], paragraphs[0])
        pattern = re.fullmatch(
            r'On (\d\d) ' + found[2] + r' 2034, the pressure readings at (\d\d:\d\d), (\d\d:\d\d) and (\d\d:\d\d) '
            r'were (\d{3}) Pa, (\d{3}) Pa and (\d{3}) Pa respectively\. (.*)', record, re.S)
        assert pattern, record
        phrase = None
        for number, sentence in enumerate(REVIEWED_CEILING_SENTENCES):
            expression = re.escape(sentence).replace(re.escape('{month}'), found[2]).replace(re.escape('{ceiling}'), r'(\d{3})')
            match = re.fullmatch(expression, ceiling)
            if match:
                phrase, value = number, int(match[1])
        assert phrase is not None, ceiling
        notes = [number for number, note in enumerate(REVIEWED_NOTES) if pattern[8].endswith(note)]
        times = [number for number, trio in enumerate(REVIEWED_TIMES) if tuple(pattern.group(2, 3, 4)) == trio]
        assert len(notes) == 1 and len(times) == 1 and int(pattern[1]) in REVIEWED_DAYS, record
        visits.append({'month': found[2], 'ceiling': value, 'phrase': phrase, 'ceiling_first': not record_first,
                       'readings': tuple(int(pattern[number]) for number in (5, 6, 7)), 'day': int(pattern[1]),
                       'times': times[0], 'note': notes[0]})
    return int(head[1]), visits


def reviewed_sheet(text):
    """The text the reviewed layout prescribes for the numbers and choices found in this sheet."""
    number, visits = parse_sheet(text)
    assert len({visit['ceiling'] for visit in visits}) == 1, 'a site keeps one ceiling'
    return render_sheet(number, visits)


class Reading:
    """A second derivation from the documents, with no structured fact input."""

    def __init__(self, sources):
        procedure = sources['procedure.md']
        assert procedure == REVIEWED_PROCEDURE, 'unreviewed procedure wording'
        # These literals intentionally do not import the author's RULES. Losing
        # scope or strictness must break the derivation, not silently use defaults.
        required = (
            'The inspection register consists of every numbered site sheet supplied in this packet.',
            'Each sheet records all three inspection readings and the applicable operating ceiling for each month shown.',
            "A site exceeded its operating ceiling in a month if and only if at least one of its three readings for that month is strictly greater than that month's ceiling on its own sheet.",
            'A reading equal to the ceiling is not an exceedance.',
            'A monthly count of sites exceeding their ceilings counts each site once, even when several readings on that site exceed its ceiling.',
            'A statement about sites in a month ranges over every site in the register, not just the sites cited beside the statement.',
            "A finding that no site exceeded its ceiling requires checking every site's sheet for that month.",
            "Citations are navigation aids and do not restrict the register's scope.",
        )
        for sentence in required:
            assert sentence in procedure, sentence
        self.sheets = {}
        for name, text in sources.items():
            if name == 'procedure.md':
                continue
            assert re.fullmatch(r'sheet-\d{3}\.md', name), name
            self.sheets[name] = self.read_sheet(text)
        assert 40 <= len(self.sheets) <= 120

    @staticmethod
    def read_sheet(text):
        """Month name -> (three readings, ceiling), read from the visit accounts of one sheet."""
        months = {}
        for title, body in re.findall(r'## M-\d+ — (\w+) visit\n(.*?)(?=<a id=|\Z)', text, re.S):
            readings = re.search(r'were (\d+) Pa, (\d+) Pa and (\d+) Pa respectively', body)
            ceiling = re.search(r'operating ceiling for \w+ was (?:recorded as )?(\d+) Pa', body)
            if ceiling is None:
                ceiling = re.search(r'card recorded (\d+) Pa as the operating ceiling', body)
            assert readings and ceiling, title
            months[title] = (tuple(int(value) for value in readings.groups()), int(ceiling[1]))
        assert len(months) == 6
        return months

    @staticmethod
    def parse(text):
        match = FORM.fullmatch(text)
        assert match, text
        return match[1], int(match[3])

    @staticmethod
    def form(text):
        return 'bound' if FORM.fullmatch(text)[2] else 'exact'

    @staticmethod
    def cited(report):
        return {name + '.md' for name in re.findall(r'\bsheet-\d{3}\b', report)}

    def verdict(self, text, subset=None):
        month, stated = self.parse(text)
        exceptional = set()
        for name in self.sheets if subset is None else subset:
            readings, ceiling = self.sheets[name][month]
            for reading in readings:
                if reading > ceiling:
                    exceptional.add(name)
        holds = len(exceptional) <= stated if self.form(text) == 'bound' else len(exceptional) == stated
        return 'supported' if holds else 'refuted'

    def echo(self, text, report):
        actual = self.verdict(text)
        partial = self.verdict(text, self.cited(report))
        return partial if partial != actual else None


def raw_case(seed):
    rng = random.Random('evalsuite:{}:{}:1:uncited'.format(generate.GENERATOR_VERSION, seed))
    return generate.family_builder('uncited')(rng)


def masked(text):
    return re.sub(r'M-0\d', 'M-N', re.sub(r'sheet-\d{3}', 'sheet-N', text))


def features_of(built, report):
    """Extract visible data before attaching any label; no source prose is read."""
    texts = report_claims(report)
    patterns = tuple(Reading.parse(text)[1] for text in texts)
    named = tuple(sorted(Reading.parse(text) for text in texts))
    names = sorted(Reading.cited(report))
    report_features = {
        'sum of counts': sum(patterns),
        'even counts': sum(value % 2 == 0 for value in patterns),
        'minimum count': min(patterns),
        'maximum count': max(patterns),
        'report bytes': len(report.encode('utf-8')),
        'report words': len(report.split()),
        'count order': patterns,
        'month counts': named,
        'bound statements': sum(Reading.form(text) == 'bound' for text in texts),
        'first cited number': int(re.search(r'\d+', names[0])[0]),
        'last cited number': int(re.search(r'\d+', names[-1])[0]),
        'cited filenames': tuple(names),
        # Everything a reviewer sees of a report: the raw text, the title and any
        # preamble, every citation line, and the whole text with the claims, the
        # per-dossier sheet numbers and the per-claim month anchors masked, so that
        # a cue in the fixed text (which is the same in every report) is learnable.
        'report text': report,
        'title and preamble': report.split('<a id=', 1)[0],
        'citation lines': tuple(masked(line) for line in re.findall(r'Citations?: [^\n]+', report)),
        'masked report': masked(STRICT_CLAIM.sub('<CLAIM>', report)),
    }
    source_files = sources_of(built)
    byte_lengths = [len(text.encode('utf-8')) for text in source_files.values()]
    metadata = {
        'source names': tuple(sorted(source_files)),
        'source count': len(source_files),
        'source bytes': sum(byte_lengths),
        'source words': sum(len(text.split()) for text in source_files.values()),
        'source stripped characters': sum(len(text.strip()) for text in source_files.values()),
        'source normalized characters': sum(len(' '.join(text.split())) for text in source_files.values()),
        'shortest file': min(byte_lengths),
        'longest file': max(byte_lengths),
        'cited bytes': sum(len(source_files[name].encode('utf-8')) for name in names),
    }
    return texts, report_features, metadata


def guesser_scores(train, test):
    """Per-feature majority lookup and numeric threshold, fitted only on train."""
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


# ---------- what a reader of the cited sheets alone can compute ----------

@functools.lru_cache(maxsize=1)
def cited_view():
    """For seeds 1 to 1000: one sample per claim and per report from the cited sheets only.

    A claim sample holds the stated count N, the count k of exceeding sites among the cited
    sheets, and the same count under the other slips (equality, readings), plus the labels.
    Sheet lengths are kept for the length attack. No uncited sheet is read.
    """
    claims, reports, lengths = [], [], {}
    for seed in range(1, 1001):
        built = case(seed) if seed <= 600 else generate.build_case('uncited', seed, 1)
        sources = sources_of(built)
        rows = built['key']['expectations']
        dossier_lure = any(row['echo_state'] for row in rows)
        lengths[seed] = {name: len(text.encode('utf-8')) for name, text in sources.items() if name != 'procedure.md'}
        for variant in ('correct', 'mixed'):
            report = built['files']['report-' + variant + '.md']
            names = sorted(Reading.cited(report))
            sheets = [Reading.read_sheet(sources[name]) for name in names]
            labels = {row['claim_text']: row for row in rows if row['variant'] == variant}
            made = []
            texts, report_features, metadata = features_of(built, report)
            for text in texts:
                month, stated = Reading.parse(text)
                facts = [sheet[month] for sheet in sheets]
                known = sum(max(readings) > ceiling for readings, ceiling in facts)
                equal = sum(max(readings) >= ceiling for readings, ceiling in facts)
                counted = sum(sum(value > ceiling for value in readings) for readings, ceiling in facts)
                row = labels[text]
                made.append({'seed': seed, 'variant': variant, 'text': text, 'month': month,
                             'form': Reading.form(text), 'stated': stated,
                             'known': known, 'excess': stated - known, 'equal': equal - known,
                             'counted': counted - known, 'wrong': row['semantic_state'] != 'supported',
                             'lure': row['echo_state'] is not None, 'dossier': dossier_lure})
            claims += made
            reports.append({'seed': seed, 'variant': variant, 'dossier': dossier_lure, 'claims': made,
                            'altered': variant == 'mixed', 'cited': tuple(names),
                            'features': (report_features, metadata)})
    return tuple(claims), tuple(reports), lengths


def lookup_accuracy(rows, feature, target, train=(1, 500), test=(501, 1000)):
    """Accuracy of the per-value majority rule fitted on the training seeds, and the base rate."""
    groups = collections.defaultdict(collections.Counter)
    fitted = [row for row in rows if train[0] <= row['seed'] <= train[1]]
    held = [row for row in rows if test[0] <= row['seed'] <= test[1]]
    for row in fitted:
        groups[feature(row)][target(row)] += 1
    overall = collections.Counter(target(row) for row in fitted)
    fallback = overall.most_common(1)[0][0]
    correct = sum((groups[feature(row)].most_common(1)[0][0] if groups.get(feature(row)) else fallback)
                  == target(row) for row in held)
    counts = collections.Counter(target(row) for row in held)
    return correct / len(held), max(counts.values()) / len(held)


CLAIM_FEATURES = {
    'N-k': lambda row: row['excess'],
    'form, N-k': lambda row: (row['form'], row['excess']),
    'form, N-k, k': lambda row: (row['form'], row['excess'], row['known']),
    'form, N-k, k, equality readings': lambda row: (row['form'], row['excess'], row['known'], row['equal'] > 0),
    'form, N-k, N-equality count, N-readings count': lambda row: (
        row['form'], row['excess'], row['excess'] - row['equal'], row['excess'] - row['counted']),
}


def structural_dossier_rule(report):
    """The reading of cited sheets that no design can avoid: all counts equal means no lure."""
    return not any(row['form'] == 'exact' and row['excess'] != 0 for row in report['claims'])


class UncitedTests(unittest.TestCase):
    def test_keys_and_echo_states_agree_with_an_independent_markdown_reading(self):
        checked = 0
        for seed in range(1, 81):
            built = case(seed)
            reading = Reading(sources_of(built))
            for row in built['key']['expectations']:
                report = built['files']['report-' + row['variant'] + '.md']
                self.assertEqual(reading.verdict(row['claim_text']), row['semantic_state'], (seed, row))
                self.assertEqual(reading.echo(row['claim_text'], report), row['echo_state'], (seed, row))
                if row['echo_state']:
                    self.assertEqual(row['echo_reader'], 'reads_cited_files_only')
                checked += 1
        self.assertEqual(checked, 960)

    def test_second_derivation_moves_when_an_uncited_rendered_value_changes(self):
        built = case(1)
        sources = sources_of(built)
        reading = Reading(sources)
        report = built['files']['report-correct.md']
        # An exact statement that agrees with the cited sheets: one more uncited exceedance refutes it,
        # and the reader of cited sheets alone keeps believing it.
        text = next(text for text in report_claims(report) if Reading.form(text) == 'exact'
                    and reading.echo(text, report) is None
                    and Reading.parse(text)[1] == sum(
                        max(reading.sheets[name][Reading.parse(text)[0]][0]) > reading.sheets[name][Reading.parse(text)[0]][1]
                        for name in reading.cited(report)))
        month, _ = Reading.parse(text)
        name = next(name for name in sorted(reading.sheets) if name not in reading.cited(report)
                    and max(reading.sheets[name][month][0]) <= reading.sheets[name][month][1])
        ceiling = reading.sheets[name][month][1]
        pattern = r'(On \d\d ' + month + r' 2034,.*?were )\d+( Pa,)'
        changed, count = re.subn(pattern, lambda match: match[1] + str(ceiling + 1) + match[2], sources[name])
        self.assertEqual(count, 1)
        sources[name] = changed
        after = Reading(sources)
        self.assertEqual(reading.verdict(text), 'supported')
        self.assertEqual(after.verdict(text), 'refuted')
        self.assertEqual(after.echo(text, report), 'supported')

    def test_equality_is_not_an_exceedance_and_one_pascal_more_is(self):
        sources = sources_of(case(1))
        reading = Reading(sources)
        report = case(1)['files']['report-correct.md']
        text = next(text for text in report_claims(report) if Reading.form(text) == 'exact')
        month, _ = Reading.parse(text)
        name = next(name for name in sorted(reading.sheets)
                    if max(reading.sheets[name][month][0]) <= reading.sheets[name][month][1])
        ceiling = reading.sheets[name][month][1]
        pattern = r'(On \d\d ' + month + r' 2034,.*?were )\d+ Pa, \d+ Pa and \d+ Pa respectively'
        for offset, expected in ((0, 'supported'), (1, 'refuted')):
            altered = dict(sources)
            altered[name] = re.sub(pattern, lambda match: match[1] + '{} Pa, {} Pa and {} Pa respectively'.format(
                ceiling, ceiling, ceiling + offset), sources[name])
            self.assertEqual(Reading(altered).verdict(text), expected)

    def test_a_bound_statement_holds_up_to_its_bound_and_fails_above_it(self):
        sources = sources_of(case(1))
        reading = Reading(sources)
        counts = collections.Counter()
        for month in MONTH_NAMES:
            actual = sum(any(value > ceiling for value in values)
                         for months in reading.sheets.values() for values, ceiling in [months[month]])
            for bound in range(0, 6):
                text = ('During the recorded {} 2034 inspections, the number of sites that exceeded '
                        'their operating ceilings was at most {}.').format(month, bound)
                expected = 'supported' if actual <= bound else 'refuted'
                self.assertEqual(reading.verdict(text), expected, (month, bound, actual))
                counts[expected] += 1
        self.assertEqual(set(counts), {'supported', 'refuted'})

    def test_missing_scope_rule_is_detected(self):
        sources = sources_of(case(1))
        sources['procedure.md'] = sources['procedure.md'].replace(
            'A statement about sites in a month ranges over every site in the register, not just the sites cited beside the statement.', '')
        with self.assertRaises(AssertionError):
            Reading(sources)

    def test_every_slot_variant_agrees_with_the_second_derivation(self):
        errors = collections.Counter()
        for seed in range(1, 81):
            raw = raw_case(seed)
            reading = Reading(raw['sources'])
            report = generate.report(raw['title'], [item for item, _ in raw['slots']])
            for true, variants in raw['slots']:
                self.assertLessEqual(len(variants), 2)
                for item in [true] + variants:
                    actual = generate.judge(item, raw['base'], raw['unknowns'])[0]
                    self.assertEqual(actual, reading.verdict(item['text']))
                    echo = None
                    if raw['told']:
                        other = generate.judge(item, raw['told']['base'], raw['told']['unknowns'])[0]
                        echo = other if other != actual else None
                    self.assertEqual(echo, reading.echo(item['text'], report))
                    if item['error'] == 'ignores_uncited_sheets':
                        self.assertEqual(echo, 'supported')
                    if item['error']:
                        errors[item['error']] += 1
                        self.assertNotEqual(Reading.parse(true['text'])[1], Reading.parse(item['text'])[1])
                        self.assertEqual(true['kind'], item['kind'])
                        self.assertEqual(true['cites'], item['cites'])
                        self.assertEqual(Reading.form(true['text']), Reading.form(item['text']))
                        month, stated = Reading.parse(item['text'])
                        if item['error'] == 'counts_equality_as_exceedance':
                            slipped = sum(any(value >= ceiling for value in values)
                                          for months in reading.sheets.values() for values, ceiling in [months[month]])
                        elif item['error'] == 'counts_readings_not_sites':
                            slipped = sum(sum(value > ceiling for value in values)
                                          for months in reading.sheets.values() for values, ceiling in [months[month]])
                        else:
                            self.assertEqual(item['error'], 'ignores_uncited_sheets')
                            slipped = sum(any(value > ceiling for value in values)
                                          for name, months in reading.sheets.items() if name in reading.cited(report)
                                          for values, ceiling in [months[month]])
                        self.assertEqual(stated, slipped, item['error'])
        self.assertEqual(set(errors), {'ignores_uncited_sheets', 'counts_equality_as_exceedance',
                                       'counts_readings_not_sites'})

    def test_correct_reports_are_supported_and_altered_reports_have_typed_errors(self):
        for seed in range(1, 301):
            rows = case(seed)['key']['expectations']
            for row in rows:
                if row['variant'] == 'correct':
                    self.assertEqual((row['semantic_state'], row['error_type']), ('supported', None))
                else:
                    self.assertEqual(row['semantic_state'] != 'supported', row['error_type'] is not None)
                    if row['error_type']:
                        self.assertIn(row['error_type'], ('ignores_uncited_sheets', 'counts_equality_as_exceedance',
                                                        'counts_readings_not_sites'))
            self.assertGreaterEqual(sum(row['error_type'] is not None for row in rows), 2)

    def test_same_seed_is_identical_and_seeds_1_to_300_build(self):
        for seed in range(1, 301):
            self.assertEqual(len(case(seed)['key']['expectations']), 12)
        self.assertEqual(case(7), generate.build_case('uncited', 7, 1))
        self.assertNotEqual(case(7)['files'], case(8)['files'])

    def test_generation_does_not_use_global_random_state(self):
        state = random.getstate()
        generate.build_case('uncited', 38, 9)
        self.assertEqual(state, random.getstate())

    def test_variant_and_errors_are_not_named_and_claim_kinds_match_in_each_slot(self):
        for seed in range(1, 301):
            built = case(seed)
            for name, text in built['files'].items():
                self.assertNotRegex(text.lower(), r'\bmixed\b|correct report|error_type|\brefuted\b|\bundetermined\b|\bsupported\b|\blure\b')
                if name.startswith('sources/'):
                    self.assertRegex(name, r'^sources/(procedure|sheet-\d{3})\.md$')
            rows = built['key']['expectations']
            signatures = [[(row['claim_id'], row['claim_kind'], Reading.parse(row['claim_text'])[0],
                            Reading.form(row['claim_text']))
                           for row in rows if row['variant'] == variant] for variant in ('correct', 'mixed')]
            self.assertEqual(*signatures)
            self.assertEqual(Reading.cited(built['files']['report-correct.md']),
                             Reading.cited(built['files']['report-mixed.md']))

    def test_every_wording_shape_month_and_count_is_sometimes_right_and_wrong(self):
        seen = collections.defaultdict(set)
        for seed in range(1, 301):
            built = case(seed)
            for row in built['key']['expectations']:
                passage = run_blind.paragraphs(built['files']['report-' + row['variant'] + '.md'])[row['claim_id']]
                citations = re.sub(r'\d+', 'N', passage.split('Citations:')[1])
                month, stated = Reading.parse(row['claim_text'])
                form = Reading.form(row['claim_text'])
                seen[('shape', shape(row['claim_text']), citations)].add(row['semantic_state'])
                seen[('month and form', month, form)].add(row['semantic_state'])
                seen[('form and count', form, stated)].add(row['semantic_state'])
        # Five counts in each of two forms and six months are too many for a literal text to be seen
        # both ways within 300 dossiers, so the old per-text check is split into its three parts.
        self.assertEqual(len([key for key in seen if key[0] == 'shape']), 2)
        self.assertEqual(len([key for key in seen if key[0] == 'month and form']), 12)
        self.assertEqual(len([key for key in seen if key[0] == 'form and count']), 10)
        # A count above what any month can hold is right by arithmetic: no month has more than four
        # exceeding sites, and only ignoring the uncited sheets turns an upper bound into a wrong one,
        # which needs cited exceedances (at most two) below the true count. So "at most 3" and
        # "at most 4" are always right; every other signature is sometimes right and sometimes wrong.
        for signature, states in seen.items():
            if signature[0] == 'form and count' and signature[1] == 'bound' and signature[2] >= 3:
                self.assertEqual(states, {'supported'}, signature)
            else:
                self.assertEqual(states, {'supported', 'refuted'}, signature)

    def test_matched_controls_share_wording_and_lures_go_in_both_directions(self):
        directions = collections.Counter()
        no_conflict = 0
        for seed in range(1, 301):
            built = case(seed)
            rows = built['key']['expectations']
            no_conflict += not any(row['echo_state'] for row in rows)
            for variant in ('correct', 'mixed'):
                report_rows = [row for row in rows if row['variant'] == variant]
                lures = [row for row in report_rows if row['echo_state']]
                self.assertLessEqual(len(lures), 2)
                for row in lures:
                    controls = [other for other in report_rows if not other['echo_state']
                                and other['claim_kind'] == row['claim_kind']
                                and shape(other['claim_text']) == shape(row['claim_text'])]
                    self.assertTrue(controls, (seed, row))
                    directions[(row['semantic_state'], row['echo_state'])] += 1
        self.assertEqual(set(directions), {('supported', 'refuted'), ('refuted', 'supported')})
        print('Uncited no-lure dossiers: {}/300 ({:.3f})'.format(no_conflict, no_conflict / 300))
        self.assertGreaterEqual(no_conflict / 300, 0.25)
        self.assertLessEqual(no_conflict / 300, 0.40)

    def test_singleton_count_parity_is_not_an_immutable_control(self):
        """Regression for the rejected design's sole-polarity truth shortcut."""
        seen = collections.defaultdict(set)
        for seed in range(1, 601):
            built = case(seed)
            for variant in ('correct', 'mixed'):
                rows = [row for row in built['key']['expectations'] if row['variant'] == variant]
                counts = collections.Counter(Reading.parse(row['claim_text'])[1] % 2 for row in rows)
                for row in rows:
                    parity = Reading.parse(row['claim_text'])[1] % 2
                    if counts[parity] == 1:
                        seen[parity].add((row['semantic_state'], bool(row['echo_state'])))
        self.assertEqual(set(seen), {0, 1})
        for parity, results in seen.items():
            self.assertEqual({state for state, _ in results}, {'supported', 'refuted'}, parity)
            self.assertEqual({lure for _, lure in results}, {False, True}, parity)

    def test_stated_count_parity_says_little_about_wrong_or_lure_claims(self):
        """The old build made lure months even and slips parity-signed: 30% of even and 7% of odd claims were lures."""
        claims, _, _ = cited_view()
        stated = collections.defaultdict(collections.Counter)
        for row in claims:
            stated[row['stated'] % 2]['claims'] += 1
            stated[row['stated'] % 2]['wrong'] += row['wrong']
            stated[row['stated'] % 2]['lure'] += row['lure']
        for key in ('wrong', 'lure'):
            rates = {parity: stated[parity][key] / stated[parity]['claims'] for parity in (0, 1)}
            print('Uncited share of {} claims by stated-count parity: {}'.format(key, rates))
            self.assertLess(abs(rates[0] - rates[1]), 0.05, (key, rates))

    def test_exceedance_findings_of_zero_sites_occur_and_are_sometimes_wrong(self):
        """Rule sentence 7 (a finding that no site exceeded needs every sheet) applies to these statements."""
        outcomes = collections.Counter()
        for seed in range(1, 301):
            for row in case(seed)['key']['expectations']:
                if Reading.parse(row['claim_text'])[1] == 0:
                    outcomes[(Reading.form(row['claim_text']), row['semantic_state'])] += 1
        print('Uncited zero-count statements:', dict(outcomes))
        self.assertEqual({state for _, state in outcomes}, {'supported', 'refuted'})
        self.assertGreater(sum(outcomes.values()), 300)
        self.assertGreater(outcomes[('exact', 'refuted')], 30)

    def test_citation_sampling_and_file_content_do_not_mark_exceptions(self):
        positions, styles, lengths = set(), set(), collections.defaultdict(set)
        for seed in range(1, 81):
            built = case(seed)
            sources = sources_of(built)
            reading = Reading(sources)
            self.assertGreaterEqual(sum(len(text.split()) for text in sources.values()), 1500)
            report = built['files']['report-correct.md']
            self.assertEqual(len(Reading.cited(report)), 4)
            for passage in run_blind.paragraphs(report).values():
                if '**C' in passage:
                    self.assertEqual(Reading.cited(passage), Reading.cited(report))
            for name, months in reading.sheets.items():
                exceptional = any(any(value > ceiling for value in readings) for readings, ceiling in months.values())
                if exceptional and name not in reading.cited(report):
                    positions.add(int(re.search(r'\d+', name)[0]) // 10)
                # All values have three digits, without adding padding to the
                # dossier. Word/byte length depends on prose template, not value.
                normalized = re.sub(r'\d', 'N', sources[name])
                self.assertNotRegex(normalized.lower(), r'\bexceed|\bexception|\bpass\b|\bfail\b')
                lengths[len(sources[name])].add(exceptional)
                styles.update(re.findall(r'(?:The operating ceiling for|On the commissioning card|The commissioning card recorded)', sources[name]))
        self.assertGreater(len(positions), 8)
        self.assertGreaterEqual(len(styles), 3)
        # Sheets differ in length for reasons that do not depend on the finding (the visit notes), so a
        # length is shared by exceptional and ordinary sheets alike: every length seen on at least thirty
        # sheets is seen on both kinds. (The length attack below tests the same property by fitting rules.)
        frequent = collections.Counter()
        for seed in range(1, 81):
            sources = sources_of(case(seed))
            reading = Reading(sources)
            for name, months in reading.sheets.items():
                frequent[len(sources[name])] += 1
        self.assertGreater(len(lengths), 20)
        for length, count in frequent.items():
            if count >= 30:
                self.assertEqual(lengths[length], {True, False}, length)

    def test_report_only_and_filename_length_guessers(self):
        """Fitted on seeds 1 to 500 and scored on 501 to 1000, with every feature a reviewer can see."""
        _, reports, _ = cited_view()
        samples = collections.defaultdict(list)
        parity_counts = collections.defaultdict(collections.Counter)
        for report in reports:
            report_features, metadata = report['features']
            seed = report['seed']
            samples['altered report'].append((seed, report_features, metadata, report['altered']))
            samples['dossier lure'].append((seed, report_features, metadata, report['dossier']))
            for position, claim in enumerate(report['claims']):
                stated = claim['stated']
                parity_counts[stated % 2]['claims'] += 1
                parity_counts[stated % 2]['lures'] += claim['lure']
                repeats = sum(other['stated'] == stated for other in report['claims'])
                features = dict(report_features, month=claim['month'], stated=stated, parity=stated % 2,
                                position=position, same_count_claims=repeats,
                                wording=shape(claim['text']), claim_bytes=len(claim['text']))
                samples['wrong claim'].append((seed, features, metadata, claim['wrong']))
                samples['lure claim'].append((seed, features, metadata, claim['lure']))
        for target, data in samples.items():
            for metadata in (False, True):
                def convert(row):
                    return (dict(row[1], **row[2]) if metadata else row[1], bool(row[3]))
                train = [convert(row) for row in data if row[0] <= 500]
                test = [convert(row) for row in data if row[0] > 500]
                base = max(sum(label for _, label in test), sum(not label for _, label in test)) / len(test)
                scores = guesser_scores(train, test)
                best = max(scores.values())
                print('Uncited guessers {} {}: best={:.4f}; base={:.4f}; held-out={}'.format(
                    'report+filenames+lengths' if metadata else 'report-only', target, best, base, len(test)))
                print('  ' + '; '.join('{}={:.4f}/{:.4f}'.format(name, score, base) for name, score in scores.items()))
                # Three points is the predeclared finite-sample tolerance used
                # by the neighbouring lure checks, not proof of no information.
                self.assertLessEqual(best, base + 0.03, (target, metadata, best, base))
        print('Uncited parity cue over 1000 seeds:', {parity: dict(counts) for parity, counts in parity_counts.items()})

    def test_guessers_detect_a_deliberate_numeric_length_leak(self):
        train = [({'length': number}, number >= 50) for number in range(100)]
        test = [({'length': number + 0.1}, number >= 50) for number in range(100)]
        self.assertGreaterEqual(guesser_scores(train, test)['length/threshold'], 0.99)

    # ---------- the cited-sheets-only attacks (review finding F3) ----------

    def test_no_stated_count_is_below_the_count_in_the_cited_sheets(self):
        claims, _, _ = cited_view()
        self.assertGreater(len(claims), 11000)
        self.assertEqual([row for row in claims if row['excess'] < 0], [])
        # Both directions of a wrong count occur, at sizes zero, one and two above the cited count.
        wrong = collections.Counter(row['excess'] for row in claims if row['wrong'])
        self.assertEqual(set(wrong), {0, 1, 2})

    def test_cited_sheets_alone_do_not_predict_wrong_or_lure_claims(self):
        claims, _, _ = cited_view()
        for target_name, target in (('wrong claim', lambda row: row['wrong']), ('lure claim', lambda row: row['lure'])):
            for name, feature in CLAIM_FEATURES.items():
                accuracy, base = lookup_accuracy(claims, feature, target)
                print('Uncited cited-sheets attack, {} by [{}]: {:.4f} against base {:.4f}'.format(
                    target_name, name, accuracy, base))
                self.assertLessEqual(accuracy, base + 0.015, (target_name, name, accuracy, base))

    def test_refuting_only_what_the_cited_sheets_refute_is_no_better_than_the_flawed_reader(self):
        """The old build: this rule scored 86.3% against the flawed reader's 81.0% and avoided 82% of the lures."""
        claims, _, _ = cited_view()
        rule = sum((row['excess'] < 0) == row['wrong'] for row in claims) / len(claims)
        reader = sum(not row['lure'] for row in claims) / len(claims)
        lures = [row for row in claims if row['lure']]
        avoided = sum((row['excess'] < 0) == row['wrong'] for row in lures) / len(lures)
        print('Uncited rule "refuted if N is below k, else supported": {:.4f}; flawed reader: {:.4f}; lures avoided: {:.3f}'.format(
            rule, reader, avoided))
        self.assertLessEqual(rule, reader + 0.005)
        self.assertLessEqual(avoided, 0.6)

    def test_report_context_gives_the_documented_limited_advantage_to_cited_sheet_rules(self):
        """Counting the exact statements above k in the same report lifts the per-claim rules by about five points."""
        claims, reports, _ = cited_view()
        above = {(report['seed'], report['variant']): sum(
            row['form'] == 'exact' and row['excess'] > 0 for row in report['claims']) for report in reports}
        rows = [dict(row, above=above[(row['seed'], row['variant'])]) for row in claims]
        for target_name, target in (('wrong claim', lambda row: row['wrong']), ('lure claim', lambda row: row['lure'])):
            accuracy, base = lookup_accuracy(rows, lambda row: (row['form'], row['excess'], row['above']), target)
            print('Uncited cited-sheets attack with report context, {}: {:.4f} against base {:.4f}'.format(
                target_name, accuracy, base))
            self.assertLessEqual(accuracy, base + 0.06, target_name)
        # The structural cause: a correct report holds at most two such counts (the lures of two decisive
        # months), so three in one report are the three altered months of a dossier without a lure.
        for report in reports:
            count = above[(report['seed'], report['variant'])]
            if report['variant'] == 'correct':
                self.assertLessEqual(count, 2)
            if count >= 3:
                self.assertTrue(report['altered'] and not report['dossier'])
                self.assertTrue(all(row['wrong'] for row in report['claims'] if row['form'] == 'exact' and row['excess'] > 0))

    def test_cited_sheets_alone_predict_dossier_and_report_labels_no_better_than_the_documented_limit(self):
        _, reports, _ = cited_view()
        by_dossier = {'dossier lure': lambda report: report['dossier'], 'altered report': lambda report: report['altered']}
        features = {
            'number of exact statements above the cited count': lambda report: sum(
                row['form'] == 'exact' and row['excess'] > 0 for row in report['claims']),
            'number of statements above the cited count': lambda report: sum(row['excess'] > 0 for row in report['claims']),
            'sorted (form, N-k)': lambda report: tuple(sorted((row['form'], row['excess']) for row in report['claims'])),
            'all exact statements equal the cited count': structural_dossier_rule,
        }
        limit = {'dossier lure': 0.90, 'altered report': 0.72}
        results = {}
        for target_name, target in by_dossier.items():
            for name, feature in features.items():
                accuracy, base = lookup_accuracy(reports, feature, target)
                results[(target_name, name)] = (accuracy, base)
                print('Uncited cited-sheets attack, {} by [{}]: {:.4f} against base {:.4f}'.format(
                    target_name, name, accuracy, base))
                self.assertLessEqual(accuracy, limit[target_name], (target_name, name, accuracy))
        # The structural part of the dossier-lure advantage: where no statement exceeds the cited count,
        # the report is almost always the correct report of a dossier without a lure.
        rule = results[('dossier lure', 'all exact statements equal the cited count')]
        self.assertGreater(rule[0], rule[1])
        self.assertLessEqual(max(accuracy for (target, _), (accuracy, _) in results.items() if target == 'dossier lure'),
                             rule[0] + 0.08)

    def test_a_dossier_without_a_lure_has_every_exact_count_equal_to_the_cited_count_in_its_correct_report(self):
        _, reports, _ = cited_view()
        silent = [report for report in reports if not report['dossier'] and report['variant'] == 'correct']
        self.assertGreater(len(silent), 200)
        for report in silent:
            self.assertTrue(structural_dossier_rule(report), report['seed'])
        loud = [report for report in reports if report['dossier'] and report['variant'] == 'correct']
        share = sum(not structural_dossier_rule(report) for report in loud) / len(loud)
        print('Uncited correct reports of dossiers with a lure that show a count above the cited count: {:.3f}'.format(share))
        self.assertGreater(share, 0.5)

    def test_per_file_length_rule_does_not_beat_base_rates(self):
        claims, reports, lengths = cited_view()
        shortest = {}
        for seed in range(1, 501):
            for name, length in lengths[seed].items():
                shortest[name] = min(shortest.get(name, length), length)

        def normalised(seed):
            return {name: length - shortest.get(name, length) for name, length in lengths[seed].items()}

        view = {seed: normalised(seed) for seed in lengths}
        rows = collections.defaultdict(list)
        for report in reports:
            sheets = view[report['seed']]
            cited = [sheets[name] for name in report['cited']]
            whole = list(sheets.values())
            report_features = {'cited length sum': sum(cited), 'cited length maximum': max(cited),
                               'all lengths sum': sum(whole), 'all lengths maximum': max(whole),
                               'sheets above the median': sum(value > statistics.median(whole) for value in whole)}
            rows['altered report'].append((report['seed'], report_features, report['altered']))
            rows['dossier lure'].append((report['seed'], report_features, report['dossier']))
            for claim in report['claims']:
                polarity = claim['form']
                features = dict(report_features, polarity=polarity,
                                polarity_and_cited_sum=(polarity, report_features['cited length sum']),
                                polarity_and_all_sum=(polarity, report_features['all lengths sum']),
                                polarity_and_excess=(polarity, claim['excess'], report_features['cited length sum']))
                rows['wrong claim'].append((claim['seed'], features, claim['wrong']))
                rows['lure claim'].append((claim['seed'], features, claim['lure']))
        for target, data in rows.items():
            train = [(features, label) for seed, features, label in data if seed <= 500]
            test = [(features, label) for seed, features, label in data if seed > 500]
            base = max(sum(label for _, label in test), sum(not label for _, label in test)) / len(test)
            scores = guesser_scores(train, test)
            best = max(scores.values())
            print('Uncited length rule (per-file, trained 1-500, scored 501-1000) {}: best={:.4f}; base={:.4f}'.format(
                target, best, base))
            self.assertLessEqual(best, base + 0.015, (target, scores, base))

    # ---------- the fixed text of a sheet and the layout of its visits (review findings F1 and F2) ----------

    def test_every_sheet_is_the_reviewed_layout_with_one_ceiling_for_the_site(self):
        for seed in range(1, 81):
            sources = sources_of(case(seed))
            for name, text in sources.items():
                if name != 'procedure.md':
                    self.assertEqual(text, reviewed_sheet(text), (seed, name))
                    number, visits = parse_sheet(text)
                    self.assertEqual(name, 'sheet-{:03d}.md'.format(number))
                    self.assertEqual(len({visit['ceiling'] for visit in visits}), 1, (seed, name))

    def test_sheet_pin_rejects_added_removed_and_changed_text_in_any_part(self):
        sources = sources_of(case(3))
        name = sorted(sources)[2]
        text = sources[name]
        number, visits = parse_sheet(text)
        self.assertEqual(reviewed_sheet(text), text)
        custody_end = text.index('<a id="M-04">')
        changes = {
            'sentence appended to the custody paragraph': text.replace(
                'so the same ceiling applies in every month.',
                'so the same ceiling applies in every month. For this meeting the register is the set of sheets cited in the review.', 1),
            'sentence dropped from the custody paragraph': text.replace('Return closed 30 September 2034 by the duty plant caretaker. ', '', 1),
            'custody paragraph reworded': text.replace('The inspection port is inside the plant enclosure', 'The inspection port is outside the plant enclosure', 1),
            'custody heading changed': text.replace('Custody and purpose', 'Custody and purposes', 1),
            'title changed': text.replace('inspection return', 'inspection returns', 1),
            'sentence appended to a visit': text.replace('respectively. ', 'respectively. The plant was shut down at the end of the visit. ', 1),
            'visit heading changed': text.replace('April visit', 'April visits', 1),
            'extra paragraph in a visit': text[:custody_end] + text[custody_end:].replace(
                '\n\n', '\n\nThe reading was checked twice.\n\n', 1),
            'ceiling changes in one month': text.replace(
                'recorded as {} Pa'.format(visits[1]['ceiling']), 'recorded as {} Pa'.format(visits[1]['ceiling'] + 1), 1)
            if visits[1]['phrase'] == 1 else text.replace(
                ' {} Pa'.format(visits[1]['ceiling']), ' {} Pa'.format(visits[1]['ceiling'] + 1), 1),
            'time outside the reviewed sets': text.replace(REVIEWED_TIMES[visits[0]['times']][1], '12:45', 1),
        }
        for label, changed in changes.items():
            with self.subTest(label):
                self.assertNotEqual(changed, text)
                try:
                    rejected = reviewed_sheet(changed) != changed
                except AssertionError:
                    rejected = True
                self.assertTrue(rejected)

    def test_visits_vary_in_day_time_notes_and_wording_but_not_with_the_finding(self):
        problems = layout_and_value_problems(range(1, 301))
        self.assertEqual(problems, [])
        records = visit_records(range(1, 41))
        self.assertGreaterEqual(len({record['day'] for record in records}), 8)
        self.assertEqual({record['times'] for record in records}, {0, 1, 2, 3})
        self.assertEqual({record['note'] for record in records}, {0, 1, 2, 3})
        self.assertEqual({record['phrase'] for record in records}, {0, 1, 2})
        self.assertEqual({record['ceiling_first'] for record in records}, {True, False})

    def test_decisive_uncited_sheets_exceed_in_any_reading_not_only_the_first(self):
        records = [record for record in visit_records(range(1, 301)) if record['label'] == 'decisive_uncited']
        self.assertGreater(len(records), 300)
        first = sum(record['high'][0] for record in records) / len(records)
        later_only = sum(not record['high'][0] for record in records) / len(records)
        cited = [record for record in visit_records(range(1, 301)) if record['label'] == 'cited_exceptional']
        print('Uncited decisive sheet-months with the first reading high: {:.3f}; later reading only: {:.3f}; cited: {:.3f}'.format(
            first, later_only, sum(not record['high'][0] for record in cited) / len(cited)))
        self.assertGreater(later_only, 0.1)
        self.assertLess(first, 0.95)
        patterns = collections.Counter(record['high'] for record in records)
        self.assertGreaterEqual(len(patterns), 5)

    def test_layout_and_value_comparison_catches_each_injected_difference_between_labels(self):
        seeds = range(1, 81)
        self.assertEqual(layout_and_value_problems(seeds), [])
        for label, fault in LAYOUT_FAULTS.items():
            with self.subTest(label):
                problems = layout_and_value_problems(seeds, fault)
                self.assertTrue(problems, label)
                print('Uncited injected {} is caught by: {}'.format(label, sorted({problem[0] for problem in problems})))

    def test_r9_echo_counts_for_100_seeds_of_twelve_dossiers(self):
        counts, directions = [], collections.Counter()
        for seed in range(1, 101):
            count = 0
            for index in range(1, 13):
                built = case(seed) if index == 1 else generate.build_case('uncited', seed, index)
                for row in built['key']['expectations']:
                    if row['echo_state']:
                        count += 1
                        directions[(row['semantic_state'], row['echo_state'])] += 1
            counts.append(count)
        measured = (min(counts), statistics.median(counts), max(counts))
        print('Uncited R9 min/median/max: {}; directions: {}; total: {}; undetermined: {}'.format(
            measured, dict(directions), sum(directions.values()), sum(n for (_, echo), n in directions.items() if echo == 'undetermined')))
        self.assertGreaterEqual(measured[1], 15)
        self.assertEqual(measured, R9_MEASURED)
        self.assertEqual(sum(directions.values()), R9_TOTAL)
        self.assertEqual(directions[('supported', 'refuted')], R9_SUPPORTED_TO_REFUTED)
        self.assertEqual(directions[('refuted', 'supported')], R9_REFUTED_TO_SUPPORTED)
        self.assertEqual(set(directions), {('supported', 'refuted'), ('refuted', 'supported')})

    def test_no_none_text_and_passages_match_the_audit_helper(self):
        sys.path.insert(0, str(ROOT / 'skills/evidence-to-proof-review/scripts'))
        import audit
        for seed in range(1, 81):
            built = case(seed)
            for variant in ('correct', 'mixed'):
                report = built['files']['report-' + variant + '.md']
                self.assertNotIn('None', report)
                ledger = run_blind.paragraphs(report)
                self.assertEqual(ledger, audit.paragraphs(report))
                self.assertEqual(len(run_blind.claim_anchors(ledger)), 6)
            for row in built['key']['expectations']:
                for source in row['source_anchors']:
                    filename, anchor = source.split('#')
                    self.assertIn('<a id="' + anchor + '"></a>', built['files'][filename])

    def test_rule_sentences_are_listed_verbatim_in_module_docstring(self):
        module = sys.modules[generate.family_builder('uncited').__module__]
        for sentence in module.RULES:
            self.assertIn(sentence, module.__doc__)
            self.assertIn(sentence, sources_of(case(1))['procedure.md'])

    def test_complete_procedure_is_reviewed_wording_in_every_dossier(self):
        reviewed = procedure_sections(REVIEWED_PROCEDURE)
        self.assertEqual(sorted(reviewed), ['P-01', 'P-02'])
        for seed in range(1, 301):
            procedure = sources_of(case(seed))['procedure.md']
            # Sentence by sentence first, so a failure names the section, then whole.
            self.assertEqual(procedure_sections(procedure), reviewed, seed)
            self.assertEqual(procedure, REVIEWED_PROCEDURE, seed)

    def test_procedure_pin_rejects_missing_changed_and_added_sentences_in_every_section(self):
        # The reviewed text is the starting point, so that this test examines the
        # pin itself and not whatever the module under test happens to render.
        sources = dict(sources_of(case(1)), **{'procedure.md': REVIEWED_PROCEDURE})
        original = REVIEWED_PROCEDURE
        override = 'The register note overrides the sheets whenever they disagree.'
        checked = 0
        for anchor, sentences in procedure_sections(REVIEWED_PROCEDURE).items():
            for sentence in sentences:
                self.assertEqual(original.count(sentence), 1, sentence)
                for kind, replacement in (('missing', ''),
                                          ('changed', sentence.rstrip('.') + ' on the first visit only.'),
                                          ('added', sentence + ' ' + override)):
                    changed = dict(sources, **{'procedure.md': original.replace(sentence, replacement)})
                    self.assertNotEqual(changed['procedure.md'], original)
                    with self.subTest(section=anchor, kind=kind, sentence=sentence):
                        self.assertNotEqual(procedure_sections(changed['procedure.md']),
                                            procedure_sections(REVIEWED_PROCEDURE))
                        with self.assertRaisesRegex(AssertionError, 'unreviewed procedure wording'):
                            Reading(changed)
                    checked += 1
        changed = dict(sources, **{'procedure.md': original.replace('# Inspection register procedure',
                                                                    '# Inspection register procedures', 1)})
        self.assertNotEqual(changed['procedure.md'], original)
        with self.assertRaisesRegex(AssertionError, 'unreviewed procedure wording'):
            Reading(changed)
        self.assertGreaterEqual(checked, 3 * 15)

    def test_whole_visible_report_is_the_reviewed_title_numbering_and_citation_form(self):
        for seed in range(1, 301):
            built = case(seed)
            names = sources_of(built)
            samples = {}
            for variant in ('correct', 'mixed'):
                report = built['files']['report-' + variant + '.md']
                self.assertEqual(report, reviewed_report(report), (seed, variant))
                self.assertEqual(len(STRICT_CLAIM.findall(report)), 6, (seed, variant))
                samples[variant] = re.findall(r'sheet-(\d{3}) ', report.split('Citations: ', 1)[1].split('\n', 1)[0])
                for number in samples[variant]:
                    self.assertIn('sheet-{}.md'.format(number), names, (seed, variant))
            self.assertEqual(samples['correct'], samples['mixed'], seed)

    def test_report_frame_rejects_title_numbering_citation_and_preamble_changes(self):
        report = reviewed_report(case(1)['files']['report-correct.md'])
        title = '# Monthly ventilation inspection review'
        first = re.search(r'Citations: [^\n]+', report)[0]
        last = re.findall(r'Citations: [^\n]+', report)[-1]
        sheet = re.search(r'sheet-(\d{3})', last)[1]
        changes = {
            'letter appended to the title': report.replace(title, title + 's', 1),
            'title with the same length': report.replace(title, title[:-1] + 'x', 1),
            'preamble line': report.replace('\n\n', '\n\nInternal note.\n\n', 1),
            'citation heading': report.replace('Citations:', 'Citation:', 1),
            'procedure cited last': report.replace(first, first.replace('procedure P-01; ', '', 1)[:-1] + '; procedure P-01.', 1),
            'procedure anchor': report.replace('procedure P-01', 'procedure P-02', 1),
            'month anchor': report.replace(first, re.sub(r'M-0\d', 'M-00', first, 1), 1),
            'sample differs in one claim': report.replace(last, last.replace('sheet-' + sheet, 'sheet-999', 1), 1),
            'claim numbering': report.replace('**C02.**', '**C2.**', 1),
            'text glued to a claim': report.replace(' Citations:', ' Noted. Citations:', 1),
        }
        for label, changed in changes.items():
            with self.subTest(label):
                self.assertNotEqual(changed, report)
                try:
                    rejected = reviewed_report(changed) != changed
                except AssertionError:
                    rejected = True
                self.assertTrue(rejected)

    def test_echo_and_lure_reader_names(self):
        echoes = 0
        for seed in range(1, 301):
            for row in case(seed)['key']['expectations']:
                if row['echo_state'] is not None:
                    echoes += 1
                    self.assertEqual(row.get('echo_reader'), 'reads_cited_files_only', (seed, row['claim_id']))
                else:
                    self.assertNotIn('echo_reader', row)
                self.assertLessEqual(set(row.get('lure_states', {})), {'reads_cited_files_only'}, (seed, row['claim_id']))
        self.assertGreater(echoes, 0)

    def test_written_suite_loads_in_scorer_seals_and_stages(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='uncited-integration-', dir=ROOT / 'tmp') as temporary:
            root = Path(temporary)
            fixtures, keys, arena, sealed = [root / name for name in ('fixtures', 'keys', 'arena', 'sealed')]
            summary = generate.write_suite(fixtures, keys, 9, 2, ['uncited'])
            loaded = scorecard.load_keys(keys)
            self.assertEqual(set(loaded), set(summary['dossiers']))
            self.assertEqual(summary['claims'], 24)
            self.assertEqual(sum(summary['expected_states'].values()), 24)
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
            self.assertFalse(list(fixtures.rglob('*.json')))

    def test_key_restatement_is_allowed_but_copied_rationale_is_detected(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='uncited-scan-', dir=ROOT / 'tmp') as temporary:
            run = Path(temporary)
            (run / 'case').mkdir()
            built = case(7)
            (run / 'case/report.md').write_text(built['files']['report-mixed.md'], encoding='utf-8')
            rows = [row for row in built['key']['expectations'] if row['variant'] == 'mixed']
            (run / 'notes.md').write_text('\n'.join(row['claim_text'] + ' ' + row['semantic_state'] for row in rows), encoding='utf-8')
            self.assertEqual(blindness.scan_run(run, rows)['verbatim_overlaps'], [])
            (run / 'copied.md').write_text(rows[-1]['rationale'], encoding='utf-8')
            self.assertTrue(blindness.scan_run(run, rows)['verbatim_overlaps'])


# ---------- layout and value comparison between labelled sheet-months ----------

R9_MEASURED = (18, 28.0, 46)
R9_TOTAL = 2957
R9_SUPPORTED_TO_REFUTED = 1673
R9_REFUTED_TO_SUPPORTED = 1284


def visit_records(seeds, fault=None):
    """One record per sheet-month, labelled by what the visit holds; read from rendered Markdown."""
    records = []
    for seed in seeds:
        built = case(seed)
        sources = sources_of(built)
        cited = Reading.cited(built['files']['report-correct.md'])
        if fault:
            sources = fault(seed, sources, cited)
        for name, text in sources.items():
            if name == 'procedure.md':
                continue
            number, visits = parse_sheet(text)
            for visit in visits:
                ceiling = visit['ceiling']
                high = tuple(value > ceiling for value in visit['readings'])
                if any(high):
                    label = 'cited_exceptional' if name in cited else 'decisive_uncited'
                elif max(visit['readings']) == ceiling:
                    label = 'equality'
                else:
                    label = 'ordinary'
                records.append(dict(visit, label=label, high=high, seed=seed, name=name, length=len(render_month(visit))))
    return records


def group_gap(first, second):
    """Largest difference between the shares of the values of two groups."""
    one, two = collections.Counter(first), collections.Counter(second)
    return max(abs(one[key] / len(first) - two[key] / len(second)) for key in set(one) | set(two))


def layout_and_value_problems(seeds, fault=None):
    """Every attribute on which a labelled group differs from the reference group by more than chance allows."""
    records = visit_records(seeds, fault)
    groups = collections.defaultdict(list)
    for record in records:
        groups[record['label']].append(record)
    problems = []

    def tolerance(first, second):
        return 2.0 / math.sqrt(min(len(first), len(second)))

    layout = {'ceiling sentence': lambda r: r['phrase'], 'order of the two paragraphs': lambda r: r['ceiling_first'],
              'closing sentence': lambda r: r['note'], 'reading times': lambda r: r['times'],
              'day of the visit': lambda r: r['day']}
    for label in ('cited_exceptional', 'decisive_uncited', 'equality'):
        for name, attribute in layout.items():
            first, second = [attribute(r) for r in groups[label]], [attribute(r) for r in groups['ordinary']]
            if group_gap(first, second) > tolerance(first, second):
                problems.append((name, label))
        # Length of the visit text: the means differ by no more than chance (four standard errors).
        lengths = [r['length'] for r in groups[label]]
        reference = [r['length'] for r in groups['ordinary']]
        error = statistics.pstdev(reference) / math.sqrt(len(lengths))
        if abs(statistics.mean(lengths) - statistics.mean(reference)) > 4 * error:
            problems.append(('length of the visit text', label))
    # Which reading exceeds, between the two kinds of exceptional visit.
    first = [r['high'] for r in groups['cited_exceptional']]
    second = [r['high'] for r in groups['decisive_uncited']]
    if group_gap(first, second) > tolerance(first, second):
        problems.append(('which readings exceed', 'decisive_uncited'))
    # Margins above the ceiling of the exceeding readings, in bands of ten pascals.
    def exceeding(rs):
        return [(value - r['ceiling'] - 1) // 10 for r in rs for value in r['readings'] if value > r['ceiling']]
    first, second = exceeding(groups['cited_exceptional']), exceeding(groups['decisive_uncited'])
    if group_gap(first, second) > tolerance(first, second):
        problems.append(('margin of exceeding readings', 'decisive_uncited'))
    # Margins below the ceiling of the other readings, against ordinary visits, in bands of ten pascals.
    def below(rs):
        return [(r['ceiling'] - value - 1) // 10 for r in rs for value in r['readings'] if value < r['ceiling']]
    reference = below(groups['ordinary'])
    for label in ('cited_exceptional', 'decisive_uncited', 'equality'):
        sample = below(groups[label])
        if group_gap(sample, reference) > tolerance(sample, reference):
            problems.append(('margin of readings below the ceiling', label))
    return problems


def move_ceiling_after_record(seed, sources, cited):
    """An exceptional visit puts the ceiling after the record: a layout cue."""
    return alter_visits(sources, cited, lambda name, record: dict(record, ceiling_first=False)
                        if any(value > record['ceiling'] for value in record['readings']) else record)


def first_ceiling_wording_on_exceptional(seed, sources, cited):
    return alter_visits(sources, cited, lambda name, record: dict(record, phrase=0)
                        if any(value > record['ceiling'] for value in record['readings']) else record)


def pin_low_readings_below_the_ceiling_by_91(seed, sources, cited):
    """The uncited exceptional visit has two readings exactly 91 Pa below the ceiling."""
    def change(name, record):
        if name in cited or not any(value > record['ceiling'] for value in record['readings']):
            return record
        values = [value if value > record['ceiling'] else record['ceiling'] - 91 for value in record['readings']]
        return dict(record, readings=tuple(values))
    return alter_visits(sources, cited, change)


def decisive_sheet_exceeds_at_the_first_reading(seed, sources, cited):
    """The uncited exceptional visit always exceeds at the first reading and nowhere else."""
    def change(name, record):
        if name in cited or not any(value > record['ceiling'] for value in record['readings']):
            return record
        values = [record['ceiling'] + 5] + [record['ceiling'] - 20, record['ceiling'] - 20]
        return dict(record, readings=tuple(values))
    return alter_visits(sources, cited, change)


def alter_visits(sources, cited, change):
    altered = dict(sources)
    for name, text in sources.items():
        if name != 'procedure.md':
            number, visits = parse_sheet(text)
            altered[name] = render_sheet(number, [change(name, visit) for visit in visits])
    return altered


LAYOUT_FAULTS = {
    'ceiling paragraph after the record on exceptional visits': move_ceiling_after_record,
    'first ceiling wording on exceptional visits': first_ceiling_wording_on_exceptional,
    'two readings 91 Pa below the ceiling on the decisive uncited visit': pin_low_readings_below_the_ceiling_by_91,
    'the decisive uncited visit always exceeds at the first reading': decisive_sheet_exceeds_at_the_first_reading,
}


if __name__ == '__main__':
    unittest.main()
