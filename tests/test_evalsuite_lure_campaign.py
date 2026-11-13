"""The lure measures on real generated dossiers, through the real runner, scorer and comparison.

`test_evalsuite_readers.py` tests the scorer's reader fields on constructed keys and a toy family. This module
closes the gap: a small suite of the ten families with a named flawed reader is generated, and stand-in reviewers
with known behaviour are run through `run_blind.py` (stage, a stand-in agent that writes `verdicts.json`, the
runner turning it into bundles), then read by `scorecard.py` and compared by `compare.py`:

  * a reviewer who answers the key reads 0% on every lure measure;
  * a reviewer who follows each family's flawed reader reads 100% on it, with the controls still at 0;
  * a reviewer who follows the second `conflict` reader reads 100% on that one;
  * a cautious reviewer who answers `undetermined` everywhere is exposed by the control, not by the echo;
  * a reviewer who leaves the lure claims unanswered counts as a hit in the comparison, not in the scorer.

Path. The real runner path is used end to end. The shared fixture `Campaign` generates the four legacy
families only (`list(generate.FAMILIES)` inside its constructor), so `LureCampaign` below substitutes the
family list for the one call that makes the suite, and changes nothing else; the fixture file is untouched.

Every expected number is computed here from the key rows of the generated suite by `expected()`, which reads
neither `scorecard.py` nor `compare.py`; none is typed in. The seed is searched, not fixed, so the test keeps
working when a family's generator changes: `find_seed()` takes the first seed whose suite gives every family
at least one lure row and tells the two `conflict` readers apart.
"""
import contextlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
sys.path.insert(0, str(ROOT / 'tests'))
import compare  # noqa: E402
import generate  # noqa: E402
import run_blind  # noqa: E402
import scorecard  # noqa: E402
from evalsuite_campaign_fixture import Campaign, perfect, sheet  # noqa: E402

FAMILIES = ['attested', 'conflict', 'defaults', 'derived', 'earlier', 'premise', 'repair', 'tables', 'truncated',
            'uncited']
SECOND = 'most_copies_win'          # the second reader of `conflict`; the first is that family's echo reader
SYSTEM_PYTHON = mock.patch.object(run_blind, 'toolchain_python', lambda *args, **kwargs: None)
TEST_ROOT = ROOT / 'tmp'
STATE = {}


# ---------- The suite, and what the key rows say about it ----------

def is_lure(row):
    return row.get('echo_state') is not None or bool(row.get('lure_states'))


def lure_rows(rows):
    return [row for row in rows if is_lure(row)]


def states_of(row):
    """Every named reader's verdict on this row; a reader named in both fields counts once."""
    states = dict(row.get('lure_states', {}))
    if row.get('echo_reader'):
        states[row['echo_reader']] = row['echo_state']
    return states


def verdict_of(row, reader):
    """What the named reader answers on this row: its own verdict where it has one, the key's elsewhere."""
    if row.get('echo_reader') == reader and row.get('echo_state') is not None:
        return row['echo_state']
    return row.get('lure_states', {}).get(reader) or row['semantic_state']


def find_seed(limit=500):
    """The first seed whose ten dossiers all hold a lure row and whose `conflict` readers can be told apart: a
    row only the second reader lures, and a second-reader lure the first reader does not share and one it does."""
    for seed in range(1, limit):
        suite = {name: generate.build_case(name, seed, number)['key']['expectations']
                 for number, name in enumerate(FAMILIES, 1)}
        if not all(any(row.get('echo_state') for row in rows) for rows in suite.values()):
            continue
        first = reader_of(suite['conflict'])
        second = [row for row in suite['conflict'] if SECOND in row.get('lure_states', {})]
        shared = [row for row in second if verdict_of(row, first) == row['lure_states'][SECOND]]
        alone = [row for row in second if row.get('echo_state') is None]
        every = sum(any(row.get('echo_state') == 'undetermined' for row in rows) for rows in suite.values())
        if alone and shared and len(shared) < len(second) and every:
            return seed
    raise AssertionError('No seed gives every family a lure row')


def reader_of(rows):
    names = {row['echo_reader'] for row in rows if row.get('echo_reader')}
    assert len(names) == 1, names
    return names.pop()


def expected(rows, answer, silence=False):
    """(hits, of) on every lure measure for a reviewer who gives `answer(row)` on each row (None: no answer),
    from the key rows alone. With `silence`, a claim left unanswered counts as the comparison counts it."""
    def hit(row, condition):
        return int(bool(condition) or (silence and answer(row) is None))

    definite = ('supported', 'refuted')
    asserting = {row['claim_kind'] for row in rows if any(
        state in definite for state in (row.get('echo_state'), *row.get('lure_states', {}).values()))}
    withholding = {row['claim_kind'] for row in rows if 'undetermined' in (
        row.get('echo_state'), *row.get('lure_states', {}).values())}
    plain = [row for row in rows if row.get('echo_state') is None and not row.get('lure_states')]
    told = {'echo_asserted': [r for r in rows if r.get('echo_state') in definite],
            'echo_withheld': [r for r in rows if r.get('echo_state') == 'undetermined']}
    result = {name: (sum(hit(r, answer(r) == r['echo_state']) for r in chosen), len(chosen))
              for name, chosen in told.items()}
    wrong = [r for r in plain if r['claim_kind'] in asserting]
    result['wrong_definite_without_conflict'] = (
        sum(hit(r, answer(r) in definite and answer(r) != r['semantic_state']) for r in wrong), len(wrong))
    caution = [r for r in plain if r['claim_kind'] in withholding and r['semantic_state'] != 'undetermined']
    result['withheld_without_conflict'] = (sum(hit(r, answer(r) == 'undetermined') for r in caution), len(caution))
    result['accuracy'] = (sum(answer(r) == r['semantic_state'] for r in rows), len(rows))
    readers = {}
    for row in rows:
        for name, state in states_of(row).items():
            block = readers.setdefault(name, {'asserted': [0, 0], 'withheld': [0, 0]})
            pair = block['withheld' if state == 'undetermined' else 'asserted']
            pair[0] += hit(row, answer(row) == state)
            pair[1] += 1
    result['readers'] = {name: {kind: tuple(pair) for kind, pair in block.items()}
                         for name, block in sorted(readers.items())}
    return result


class LureCampaign(Campaign):
    """The shared campaign fixture, over the ten families with a named flawed reader."""

    def __init__(self, root, seed):
        real = generate.write_suite
        with mock.patch.object(generate, 'write_suite',
                               lambda fixtures, keys, number, dossiers, families: real(
                                   fixtures, keys, number, dossiers, FAMILIES)):
            super().__init__(root, dossiers=len(FAMILIES), seed=seed)
        self.rows, self.family, self.dossier, self.reader = {}, {}, {}, {}
        for path in sorted(self.draft.glob('*.json')):
            key = json.loads(path.read_text(encoding='utf-8'))
            family = key['generator']['family']
            self.rows[path.stem] = [dict(row, family=family) for row in key['expectations']]
            self.family[path.stem], self.dossier[family] = family, path.stem
            self.reader[family] = reader_of(self.rows[path.stem])
        self.next_folder = 0

    def rows_of(self, families=None):
        return [row for name, rows in sorted(self.rows.items()) for row in rows
                if families is None or self.family[name] in families]

    def score(self, arm, families=None):
        """The real scorer on this arm's bundles; with `families`, on the dossiers of those families alone."""
        base = self.out / arm
        index, keys = json.loads((base / 'records/index.json').read_text(encoding='utf-8')), self.keys
        if families is not None:
            self.next_folder += 1
            keys = self.root / f'subset-{self.next_folder}'
            keys.mkdir()
            wanted = {self.dossier[name] for name in families}
            for dossier in wanted:
                shutil.copyfile(self.keys / f'{dossier}.json', keys / f'{dossier}.json')
            index = {name: label for name, label in index.items() if label['dossier'] in wanted}
        return scorecard.score(base / 'runs', keys, index=index, scan=False)

    def compare(self, baseline, candidate):
        keys = scorecard.load_keys(self.keys)
        reviews = {arm: compare.arm_reviews(self.out, arm, keys) for arm in (baseline, candidate)}
        return reviews, compare.compare(reviews[baseline], reviews[candidate], bootstrap=1000)


# ---------- The stand-in reviewers ----------

def follows_flawed_reader(campaign):
    def policy(call):
        reader = campaign.reader[campaign.family[call['dossier']]]
        return {'sheet': sheet(call, lambda row: verdict_of(row, reader))}
    return policy


def follows_second_reader(campaign):
    def policy(call):
        if campaign.family[call['dossier']] != 'conflict':
            return perfect(call)
        return {'sheet': sheet(call, lambda row: verdict_of(row, SECOND))}
    return policy


def cautious(call):
    return {'sheet': sheet(call, lambda row: 'undetermined')}


def leaves_lures_unanswered(call):
    """Answers the key, but hands in no row for a claim that is a lure for any reader."""
    unanswered = {row['claim_id'] for row in lure_rows(call['rows'])}
    answers = sheet(call)
    answers['claims'] = [row for row in answers['claims'] if row['claim_id'] not in unanswered]
    return {'sheet': answers}


def key_answer(row):
    return row['semantic_state']


def setUpModule():
    # Cleanups run even when this function fails half way, so neither the patch nor the folder is left behind.
    SYSTEM_PYTHON.start()
    unittest.addModuleCleanup(SYSTEM_PYTHON.stop)
    TEST_ROOT.mkdir(exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix='evalsuite-lure-campaign-', dir=TEST_ROOT))
    unittest.addModuleCleanup(shutil.rmtree, run, ignore_errors=True)
    seed = find_seed()
    first, second = LureCampaign(run / 'first', seed), LureCampaign(run / 'second', seed)
    with contextlib.redirect_stdout(io.StringIO()):         # the runner reports every review it ends
        status = (first.run(lambda call: {'plain': perfect, 'skill-no-lean': follows_flawed_reader(first),
                                          'skill-lean': follows_second_reader(first)}[call['arm']](call)),
                  second.run(lambda call: cautious(call) if call['arm'] == 'plain' else leaves_lures_unanswered(call),
                             arms='plain,skill-no-lean'))
    assert status == (0, 0), status
    STATE.update(seed=seed, first=first, second=second)


def tearDownModule():
    """Empty on purpose: Python 3.9 runs the module cleanups added above only when a tearDownModule exists."""


# ---------- The tests ----------

LURE_MEASURES = ('echo_asserted', 'echo_withheld', 'wrong_definite_without_conflict', 'withheld_without_conflict')


class LureCampaignCase(unittest.TestCase):
    first = property(lambda self: STATE['first'])
    second = property(lambda self: STATE['second'])
    GROUPS = [None] + [[name] for name in FAMILIES]

    def assertPair(self, block, pair, where=''):
        self.assertEqual((block['count'], block['of']), pair, where)

    def assertMeasures(self, card, want, where):
        """Every lure measure of the scorecard, and every reader's line, equals what the key rows imply."""
        block = card['semantic']['all_claims']
        for name in LURE_MEASURES + ('accuracy',):
            self.assertPair(block[name], want[name], f'{where}: {name}')
        readers = card['semantic']['lures_by_reader']
        self.assertEqual(sorted(readers), sorted(want['readers']), where)
        for name, kinds in want['readers'].items():
            for kind, pair in kinds.items():
                self.assertPair(readers[name][kind], pair, f'{where}: {name} {kind}')

    def assertCoverage(self, card, where):
        self.assertEqual({name: value for name, value in card['coverage'].items() if value}, {},
                         f'{where}: coverage is not complete')

    def assertFull(self, block, where):
        """At 100% of its denominator, and a rate of 1.0 wherever there is a denominator."""
        self.assertEqual(block['count'], block['of'], where)
        self.assertEqual(block['rate'], 1.0 if block['of'] else None, where)

    def assertNone(self, block, where):
        self.assertEqual(block['count'], 0, where)
        self.assertEqual(block['rate'], 0.0 if block['of'] else None, where)


class SuiteTests(LureCampaignCase):
    def test_every_family_contributes_a_dossier_with_a_lure_row(self):
        campaign = self.first
        self.assertEqual(sorted(campaign.family.values()), sorted(FAMILIES))
        for name in FAMILIES:
            rows = campaign.rows_of([name])
            self.assertTrue(lure_rows(rows), f'{name} has no lure row, so it would pass vacuously')
            self.assertTrue(any(row.get('echo_state') for row in rows), name)
        self.assertEqual(len(set(campaign.reader.values())), len(FAMILIES))
        # Both kinds of echo and both sorts of control exist in the suite, or those measures would be vacuous.
        overall = expected(campaign.rows_of(), key_answer)
        for name in LURE_MEASURES:
            self.assertGreater(overall[name][1], 0, name)
        # `conflict` carries two readers, and they differ: one lure row is the second reader's alone.
        conflict = campaign.rows_of(['conflict'])
        self.assertEqual({name for row in conflict for name in states_of(row)}, {campaign.reader['conflict'], SECOND})
        self.assertTrue([row for row in conflict if SECOND in states_of(row) and not row.get('echo_state')])

    def test_every_review_went_through_the_runner_and_was_scored(self):
        for campaign, arms in ((self.first, ('plain', 'skill-no-lean', 'skill-lean')), (self.second, ('plain', 'skill-no-lean'))):
            self.assertEqual(len(campaign.calls), len(FAMILIES) * 2 * len(arms))
            self.assertEqual({record['status'] for record in campaign.units().values()}, {'scored'})
            self.assertEqual({call['arm'] for call in campaign.calls}, set(arms))
            for arm in arms:
                bundles = sorted((campaign.out / arm / 'runs').iterdir())
                self.assertEqual(len(bundles), len(FAMILIES) * 2)
                for bundle in bundles:
                    self.assertTrue((bundle / 'input/report-ledger.json').is_file(), bundle)
        for bundle in (self.first.out / 'skill-lean/runs').iterdir():
            self.assertTrue((bundle / 'findings.json').is_file(), bundle)


class KeyReviewerTests(LureCampaignCase):
    def test_a_reviewer_who_answers_the_key_reads_zero_on_every_lure_measure(self):
        campaign = self.first
        for families in self.GROUPS:
            where = 'overall' if families is None else families[0]
            rows = campaign.rows_of(families)
            card = campaign.score('plain', families)
            self.assertMeasures(card, expected(rows, key_answer), where)
            self.assertCoverage(card, where)
            block = card['semantic']['all_claims']
            self.assertEqual((block['accuracy']['rate'], block['accuracy']['of']), (1.0, len(rows)), where)
            for name in LURE_MEASURES:
                self.assertNone(block[name], f'{where}: {name}')
            for reader, kinds in card['semantic']['lures_by_reader'].items():
                for kind, value in kinds.items():
                    self.assertNone(value, f'{where}: {reader} {kind}')
            # The zeros are not empty measures: the denominators are the echoes the key rows hold.
            echoes = [row['echo_state'] for row in rows if row.get('echo_state')]
            self.assertEqual(block['echo_asserted']['of'], sum(state != 'undetermined' for state in echoes), where)
            self.assertEqual(block['echo_withheld']['of'], echoes.count('undetermined'), where)
            self.assertEqual(sorted(card['semantic']['lures_by_reader']),
                             sorted({name for row in rows for name in states_of(row)}), where)

    def test_the_preamble_of_the_premise_report_is_not_a_claim_that_got_no_answer(self):
        campaign = self.first
        dossier = campaign.dossier['premise']
        for variant in ('correct', 'mixed'):
            report = (campaign.fixtures / dossier / f'report-{variant}.md').read_text(encoding='utf-8')
            before = report.split('<a id="')[0].strip().splitlines()
            preamble = [line for line in before if line.strip() and not line.startswith('#')]
            self.assertTrue(preamble, 'the premise report has no preamble paragraph before its first claim')
            # The runner's report ledger keeps it as a passage of its own, which is no claim.
            runs = [name for name, label in json.loads((campaign.out / 'plain/records/index.json').read_text(
                encoding='utf-8')).items() if (label['dossier'], label['variant']) == (dossier, variant)]
            self.assertEqual(len(runs), 1)
            ledger = json.loads((campaign.out / 'plain/runs' / runs[0] / 'input/report-ledger.json').read_text(
                encoding='utf-8'))
            outside = [text for anchor, text in ledger.items() if anchor not in run_blind.claim_anchors(ledger).values()]
            self.assertTrue(any(line in text for text in outside for line in preamble), variant)
            self.assertEqual(len(run_blind.claim_anchors(ledger)), len(campaign.rows[dossier]) // 2)
        for arm in ('plain', 'skill-no-lean'):
            card = campaign.score(arm, ['premise'])
            self.assertCoverage(card, f'premise under {arm}')
            self.assertEqual(card['suite']['claims'], len(campaign.rows[dossier]), arm)
            self.assertEqual(card['suite']['runs_scored'], 2, arm)


class FlawedReaderTests(LureCampaignCase):
    def test_a_reviewer_who_follows_each_flawed_reader_reads_full_on_it_and_zero_on_the_controls(self):
        campaign = self.first
        follows = lambda row: verdict_of(row, campaign.reader[row['family']])  # noqa: E731
        for families in self.GROUPS:
            where = 'overall' if families is None else families[0]
            rows = campaign.rows_of(families)
            card = campaign.score('skill-no-lean', families)
            self.assertMeasures(card, expected(rows, follows), where)
            self.assertCoverage(card, where)
            block = card['semantic']['all_claims']
            for name in ('echo_asserted', 'echo_withheld'):
                self.assertFull(block[name], f'{where}: {name}')
            for name in ('wrong_definite_without_conflict', 'withheld_without_conflict'):
                self.assertNone(block[name], f'{where}: {name}')
            self.assertEqual(block['accuracy']['count'],
                             len(rows) - sum(follows(row) != row['semantic_state'] for row in rows), where)
            for name in families or FAMILIES:
                for kind, value in card['semantic']['lures_by_reader'][campaign.reader[name]].items():
                    self.assertFull(value, f'{where}: {campaign.reader[name]} {kind}')
        overall = expected(campaign.rows_of(), follows)
        self.assertGreater(overall['echo_asserted'][1], 0)
        self.assertGreater(overall['echo_withheld'][1], 0)

    def test_the_second_conflict_reader_is_lured_only_where_the_key_rows_say_it_agrees(self):
        campaign = self.first
        rows = campaign.rows_of(['conflict'])
        first = campaign.reader['conflict']
        readers = campaign.score('skill-no-lean', ['conflict'])['semantic']['lures_by_reader']
        for kind, value in readers[first].items():
            self.assertFull(value, kind)
        second = [row for row in rows if SECOND in states_of(row)]
        same = [row for row in second if verdict_of(row, first) == row['lure_states'][SECOND]]
        self.assertTrue(0 < len(same) < len(second))
        self.assertEqual(sum(value['count'] for value in readers[SECOND].values()), len(same))
        self.assertEqual(sum(value['of'] for value in readers[SECOND].values()), len(second))

    def test_a_reviewer_who_follows_the_second_conflict_reader(self):
        campaign = self.first
        rows = campaign.rows_of(['conflict'])
        first = campaign.reader['conflict']
        follows = lambda row: verdict_of(row, SECOND)  # noqa: E731
        want = expected(rows, follows)
        card = campaign.score('skill-lean', ['conflict'])
        self.assertMeasures(card, want, 'conflict')
        self.assertCoverage(card, 'conflict')
        readers = card['semantic']['lures_by_reader']
        for kind, value in readers[SECOND].items():
            self.assertFull(value, kind)
        # The first reader is followed only where it gives the second reader's verdict.
        agreeing = [row for row in rows if first in states_of(row) and states_of(row)[first] == follows(row)]
        self.assertEqual(sum(value['count'] for value in readers[first].values()), len(agreeing))
        self.assertLess(len(agreeing), sum(value['of'] for value in readers[first].values()))
        # A claim that lures either reader is no control. The second reader's own rows have no echo, and this
        # reviewer is wrong on them; were they counted in the control, it would read above zero.
        alone = [row for row in rows if SECOND in states_of(row) and not row.get('echo_state')
                 and states_of(row)[SECOND] in ('supported', 'refuted')]
        self.assertTrue(alone, 'no row only the second reader lures, so the control would not be tested')
        block = card['semantic']['all_claims']
        self.assertGreater(block['wrong_definite_without_conflict']['of'], 0)
        self.assertEqual(block['wrong_definite_without_conflict']['count'], 0)
        self.assertNone(block['withheld_without_conflict'], 'withheld control')
        self.assertTrue(all(follows(row) != row['semantic_state'] for row in alone))
        # Over the whole suite, where the same reviewer answers the key outside `conflict`, the picture is the same.
        mixed = lambda row: follows(row) if row['family'] == 'conflict' else key_answer(row)  # noqa: E731
        self.assertMeasures(campaign.score('skill-lean'), expected(campaign.rows_of(), mixed), 'overall')
        self.assertEqual(campaign.score('skill-lean')['semantic']['all_claims']['wrong_definite_without_conflict']['count'], 0)


class CautiousReviewerTests(LureCampaignCase):
    def test_a_cautious_reviewer_is_exposed_by_the_control_and_not_by_the_echo(self):
        campaign = self.second
        for families in self.GROUPS:
            where = 'overall' if families is None else families[0]
            card = campaign.score('plain', families)
            self.assertMeasures(card, expected(campaign.rows_of(families), lambda row: 'undetermined'), where)
            self.assertCoverage(card, where)
            block = card['semantic']['all_claims']
            for name in ('echo_withheld', 'withheld_without_conflict'):
                self.assertFull(block[name], f'{where}: {name}')
            for name in ('echo_asserted', 'wrong_definite_without_conflict'):
                self.assertNone(block[name], f'{where}: {name}')
            for reader, kinds in card['semantic']['lures_by_reader'].items():
                self.assertNone(kinds['asserted'], f'{where}: {reader}')
                self.assertFull(kinds['withheld'], f'{where}: {reader}')
            if families is None:
                for name in LURE_MEASURES:
                    self.assertGreater(block[name]['of'], 0, name)


class ComparisonTests(LureCampaignCase):
    NAMES = {'echo_asserted': 'echo_asserted', 'echo_withheld': 'echo_withheld',
             'wrong_definite_without_conflict': 'wrong_definite_without_conflict',
             'withheld_control': 'withheld_without_conflict', 'accuracy': 'accuracy'}

    def cell(self, pair):
        count, of = pair
        return f'{"n/a" if not of else f"{count / of * 100:.1f}%"} ({count}/{of})'

    def assertArm(self, arm, want, where):
        for name, source in self.NAMES.items():
            self.assertPair(arm[name], want[source], f'{where}: {name}')
        self.assertEqual(sorted(arm['lures_by_reader']), sorted(want['readers']), where)
        for reader, kinds in want['readers'].items():
            for kind, pair in kinds.items():
                self.assertPair(arm['lures_by_reader'][reader][kind], pair, f'{where}: {reader} {kind}')

    def test_the_key_reviewer_against_the_flawed_one_shows_the_measures_and_names_each_reader(self):
        campaign = self.first
        follows = lambda row: verdict_of(row, campaign.reader[row['family']])  # noqa: E731
        rows = campaign.rows_of()
        key, flawed = expected(rows, key_answer), expected(rows, follows)
        _, result = campaign.compare('plain', 'skill-no-lean')
        self.assertArm(result['baseline'], key, 'baseline')
        self.assertArm(result['candidate'], flawed, 'candidate')
        self.assertNone(result['baseline']['echo_asserted'], 'baseline')
        self.assertFull(result['candidate']['echo_asserted'], 'candidate')
        self.assertFull(result['candidate']['echo_withheld'], 'candidate')
        self.assertNone(result['candidate']['wrong_definite_without_conflict'], 'candidate')
        self.assertNone(result['candidate']['withheld_control'], 'candidate')
        self.assertEqual(result['difference']['echo_asserted']['value'], 1.0)
        self.assertEqual(result['difference']['echo_withheld']['value'], 1.0)
        self.assertEqual(result['claims_only_baseline_right'], flawed['accuracy'][1] - flawed['accuracy'][0])
        self.assertEqual(result['claims_only_candidate_right'], 0)
        text = compare.render(('plain', 'skill-no-lean'), result, None, [])
        for label in ('Packet’s word taken over its files', 'Wrong definite where its word agrees',
                      'Withheld where its word is wrong', 'Withheld where its word is right'):
            self.assertIn(label, text)
        row = next(line for line in text.splitlines() if line.startswith('Packet’s word taken over its files'))
        self.assertIn(self.cell(key['echo_asserted']), row)
        self.assertIn(self.cell(flawed['echo_asserted']), row)
        for arm, want in (('plain', key), ('skill-no-lean', flawed)):
            for reader, kinds in want['readers'].items():
                self.assertIn(f'Reader "{reader}", arm "{arm}": asserted {self.cell(kinds["asserted"])}; '
                              f'withheld {self.cell(kinds["withheld"])}', text.splitlines())
        for reader in campaign.reader.values():
            self.assertIn(f'Reader "{reader}"', text)
        self.assertIn(f'Reader "{SECOND}"', text)

    def test_the_command_line_prints_the_same_table(self):
        campaign = self.first
        output = campaign.root / 'comparison.json'
        with mock.patch('sys.stdout', new_callable=io.StringIO) as printed:
            status = compare.main(['--campaign', str(campaign.out), '--keys', str(campaign.keys),
                                   '--baseline', 'plain', '--candidate', 'skill-no-lean', '--bootstrap', '1000',
                                   '--output', str(output)])
        self.assertEqual(status, 0)
        saved = json.loads(output.read_text(encoding='utf-8'))['comparisons'][0]
        _, result = campaign.compare('plain', 'skill-no-lean')
        for arm, name in (('baseline', 'plain'), ('candidate', 'skill-no-lean')):
            for source in self.NAMES:
                self.assertEqual(saved[arm][source], result[arm][source], source)
            self.assertEqual(saved[arm]['lures_by_reader'], result[arm]['lures_by_reader'])
            for reader in saved[arm]['lures_by_reader']:
                self.assertIn(f'Reader "{reader}", arm "{name}": ', printed.getvalue())

    def test_an_unanswered_lure_claim_is_a_hit_in_the_comparison_and_a_miss_in_the_scorer(self):
        campaign = self.second
        rows = campaign.rows_of()
        silent = lambda row: None if is_lure(row) else row['semantic_state']  # noqa: E731
        scored, counted = expected(rows, silent), expected(rows, silent, silence=True)
        card = campaign.score('skill-no-lean')
        self.assertMeasures(card, scored, 'scorer')
        self.assertEqual(len(card['coverage']['missing_claims']), len(lure_rows(rows)))
        for reader, kinds in card['semantic']['lures_by_reader'].items():
            for kind, value in kinds.items():
                self.assertNone(value, f'{reader} {kind}')
        _, result = campaign.compare('plain', 'skill-no-lean')
        self.assertArm(result['candidate'], dict(counted, accuracy=scored['accuracy']), 'candidate')
        for name in ('echo_asserted', 'echo_withheld'):
            self.assertFull(result['candidate'][name], name)
        for reader, kinds in result['candidate']['lures_by_reader'].items():
            for kind, value in kinds.items():
                self.assertFull(value, f'{reader} {kind}')
        self.assertEqual(result['candidate']['claims_unanswered'], len(lure_rows(rows)))
        # The cautious arm beside it withholds everywhere: full on the withheld lures and the control, none on the definite.
        self.assertArm(result['baseline'], expected(rows, lambda row: 'undetermined'), 'baseline')
        self.assertFull(result['baseline']['echo_withheld'], 'baseline')
        self.assertFull(result['baseline']['withheld_control'], 'baseline')
        self.assertNone(result['baseline']['echo_asserted'], 'baseline')


if __name__ == '__main__':
    unittest.main()
