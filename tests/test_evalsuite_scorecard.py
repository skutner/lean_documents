"""Checks for the extended scorer: agreement with the legacy evaluator, error directions, baselines and gate."""
import hashlib
import importlib.util
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
import scorecard  # noqa: E402

spec = importlib.util.spec_from_file_location('evaluate', ROOT / 'validation/evaluate.py')
evaluate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluate)

TEST_ROOT = ROOT / 'tmp'
TEST_ROOT.mkdir(exist_ok=True)
TEST_RUN = Path(tempfile.mkdtemp(prefix='evalsuite-scorecard-tests-', dir=TEST_ROOT))

STRUCTURAL_GATE = {'require_isolated_keys': True, 'require_no_contamination_flags': True,
                   'require_full_coverage': True, 'min_repeats_per_report': 3, 'max_false_formal_certainty': 0}
RATIONALE = 'The ledger total omits the freight line so the stated sum is short by forty units.'


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data, indent=2), encoding='utf-8')


def finding(claim, anchor, state, formal='not_attempted', evidence=(), reasoning='Checked against the cited passage.'):
    return {'claim_id': claim, 'report_anchor': anchor, 'semantic_state': state, 'argument_status': 'sufficient',
            'formal_status': formal, 'evidence': list(evidence), 'reasoning': [reasoning]}


def expectation(claim, state, variant, anchors=(), rationale='Editorial reason kept short.'):
    return {'claim_id': claim, 'semantic_state': state, 'prospective_disposition': 'proof',
            'source_anchors': list(anchors), 'rationale': rationale,
            'report_path': 'report.md' if variant == 'mixed' else 'report-correct.md'}


MIXED_KEY = [expectation('D-01', 'supported', 'mixed'), expectation('D-02', 'refuted', 'mixed'),
             expectation('D-03', 'undetermined', 'mixed'), expectation('D-04', 'refuted', 'mixed')]
CORRECT_KEY = [expectation('D-C01', 'supported', 'correct'), expectation('D-C02', 'supported', 'correct')]
MIXED_LEDGER = {f'a{n}': f'**D-0{n}.** Claim {n}.' for n in range(1, 5)}
CORRECT_LEDGER = {'c1': '**D-C01.** Claim one.', 'c2': '**D-C02.** Claim two.'}
MIXED_RIGHT = [finding('D-01', 'a1', 'supported', 'proved'), finding('D-02', 'a2', 'refuted', 'refuted'),
               finding('D-03', 'a3', 'undetermined'), finding('D-04', 'a4', 'refuted', 'refuted')]
CORRECT_RIGHT = [finding('D-C01', 'c1', 'supported', 'proved'), finding('D-C02', 'c2', 'supported')]


class ScorecardTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + '-', dir=TEST_RUN))
        self.runs, self.keys, self.records = self.root / 'agent/runs', self.root / 'keys', self.root / 'records'
        write(self.keys / 'demo.json', {'expectations': MIXED_KEY + CORRECT_KEY})

    def bundle(self, run_id, findings, ledger, sources=None, stamp=None):
        """Write one exported bundle. Each run's findings differ by a note unless `stamp` repeats."""
        run = self.runs / run_id
        note = f'Audit note {run_id if stamp is None else stamp}.'
        write(run / 'findings.json', [dict(f, reasoning=f['reasoning'] + [note]) for f in findings])
        write(run / 'input/report-ledger.json', ledger)
        write(run / 'proof-results.json', {'status': 'passed', 'targets': [
            {'module': 'Audit', 'theorem': f['claim_id'], 'status': 'passed'}
            for f in findings if f['formal_status'] in ('proved', 'refuted')]})
        for name, text in (sources or {'s.md': '<a id="S01"></a>\nA source passage.\n'}).items():
            write(run / 'input/sources' / name, text)

    def campaign(self, repeats=3, mixed=MIXED_RIGHT, correct=CORRECT_RIGHT):
        """Three separate audits of each report, each with the stage record a blind run leaves."""
        index = {}
        for repeat in range(1, repeats + 1):
            for name, variant, findings, ledger in ((f'm{repeat}', 'mixed', mixed, MIXED_LEDGER),
                                                    (f'c{repeat}', 'correct', correct, CORRECT_LEDGER)):
                self.bundle(name, findings, ledger)
                index[name] = {'dossier': 'demo', 'variant': variant, 'repeat': repeat}
                self.record(name)
        write(self.records / 'index.json', index)
        return index

    def record(self, run_id):
        supplied = {str(p.relative_to(self.runs / run_id)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted((self.runs / run_id / 'input').rglob('*')) if p.is_file()}
        write(self.records / f'{run_id}.json', {'opaque_id': run_id, 'case_files': supplied})

    def score(self, **options):
        options.setdefault('agent_roots', [self.root / 'agent'])
        return scorecard.score(self.runs, self.keys, **options)

    def failed(self, card):
        return [check['check'] for check in card['gate']['checks'] if not check['passed']]

    def test_retained_runs_agree_with_the_legacy_evaluator_claim_by_claim(self):
        legacy = evaluate.evaluate(ROOT / 'validation/runs', 'all')
        card = scorecard.score(ROOT / 'validation/runs', ROOT / 'examples/evaluator', agent_roots=[ROOT])
        self.assertEqual(card['suite']['claims'], legacy['total_expected_claims'])
        self.assertEqual(card['semantic']['all_claims']['accuracy']['count'], legacy['total_matched_claims'])
        self.assertEqual(card['suite']['atomic_findings'], legacy['total_atomic_findings'])
        expected = {(run['run_id'], c['claim_id']): c['observed'] for run in legacy['runs'] for c in run['comparisons']}
        observed, keys = {}, scorecard.load_keys(ROOT / 'examples/evaluator')
        for run in scorecard.discover_runs(ROOT / 'validation/runs', keys):
            rows = scorecard.observe(run, scorecard.read_legacy_bundle(run['path']),
                                     [row for row in keys[run['dossier']]['rows'] if row['variant'] == run['variant']])
            observed.update({(row['run_id'], row['claim_id']): row['observed'] for row in rows})
        self.assertEqual(observed, expected)

    def test_retained_runs_are_reported_as_exposed_and_single_run(self):
        thresholds = json.loads((ROOT / 'evalsuite/thresholds.json').read_text())
        card = scorecard.score(ROOT / 'validation/runs', ROOT / 'examples/evaluator', agent_roots=[ROOT],
                               thresholds=thresholds)
        self.assertEqual(card['blindness']['status'], 'exposed')
        self.assertEqual(card['repeatability']['min_repeats_per_report'], 1)
        self.assertFalse(card['gate']['passed'])
        self.assertLessEqual({'isolated_keys', 'repeats_per_report', 'no_contamination_flags'},
                             set(self.failed(card)))
        self.assertEqual(len(card['blindness']['contamination']['runs_without_stage_record']), 20)
        summary = card['baselines']['summary']
        self.assertEqual(summary['clean_reports']['always_supported_accuracy'], 1.0)
        self.assertLess(summary['flawed_reports']['best_constant_accuracy'], 0.5)
        self.assertGreaterEqual(card['evidence']['expected_anchor_recall']['rate'], 0.95)

    def test_aggregate_equals_the_legacy_rule_for_every_state_combination(self):
        for size in range(1, len(scorecard.STATES) + 1):
            for combination in itertools.combinations(scorecard.STATES, size):
                rows = [{'semantic_state': state} for state in combination]
                self.assertEqual(scorecard.aggregate(rows), evaluate.aggregate(rows), combination)

    def test_wilson_interval_known_values(self):
        self.assertIsNone(scorecard.wilson(0, 0))
        low, high = scorecard.wilson(5, 10)
        self.assertAlmostEqual(low, 0.2366, places=4)
        self.assertAlmostEqual(high, 0.7634, places=4)
        low, high = scorecard.wilson(0, 93)
        self.assertEqual(low, 0.0)
        self.assertAlmostEqual(high, scorecard.Z95 ** 2 / (93 + scorecard.Z95 ** 2), places=12)

    def test_wrong_verdicts_are_counted_by_direction_and_formal_certainty(self):
        wrong_mixed = [finding('D-01', 'a1', 'supported', 'proved'), finding('D-02', 'a2', 'supported', 'proved'),
                       finding('D-03', 'a3', 'undetermined'), finding('D-04', 'a4', 'refuted', 'refuted')]
        wrong_correct = [finding('D-C01', 'c1', 'supported'), finding('D-C02', 'c2', 'refuted', 'refuted')]
        self.bundle('demo-mixed', wrong_mixed, MIXED_LEDGER)
        self.bundle('demo-correct', wrong_correct, CORRECT_LEDGER)
        card = self.score()
        overall = card['semantic']['all_claims']
        self.assertEqual((overall['accuracy']['count'], overall['accuracy']['of']), (4, 6))
        self.assertEqual((overall['false_support']['count'], overall['false_support']['of']), (1, 3))
        self.assertEqual((overall['false_refutation']['count'], overall['false_refutation']['of']), (1, 3))
        self.assertEqual((overall['error_detection']['count'], overall['error_detection']['of']), (1, 2))
        self.assertEqual(overall['confusion']['refuted'], {'refuted': 1, 'supported': 1})
        self.assertEqual(overall['confusion']['supported'], {'refuted': 1, 'supported': 2})
        self.assertEqual(card['formal']['false_formal_certainty']['claims'],
                         ['demo/correct/D-C02', 'demo/mixed/D-02'])
        self.assertEqual(card['semantic']['flawed_reports']['claims'], 4)
        self.assertEqual(card['semantic']['clean_reports']['claims'], 2)
        flawed = card['baselines']['summary']['flawed_reports']
        self.assertEqual((flawed['best_constant'], flawed['best_constant_accuracy']), ('refuted', 0.5))
        self.assertEqual(flawed['always_supported_accuracy'], 0.25)
        self.assertEqual(card['baselines']['constant']['supported']['all_claims']['error_detection']['count'], 0)
        self.assertEqual(card['per_run'][1]['mismatches'], [{'claim_id': 'D-02', 'expected': 'refuted',
                                                             'observed': 'supported'}])

    def test_an_unsupported_wrong_verdict_is_not_formal_certainty(self):
        self.bundle('demo-mixed', [finding('D-01', 'a1', 'supported'), finding('D-02', 'a2', 'supported'),
                                   finding('D-03', 'a3', 'undetermined'), finding('D-04', 'a4', 'refuted')],
                    MIXED_LEDGER)
        self.bundle('demo-correct', CORRECT_RIGHT, CORRECT_LEDGER)
        card = self.score()
        self.assertEqual(card['semantic']['all_claims']['false_support']['count'], 1)
        self.assertEqual(card['formal']['false_formal_certainty']['count'], 0)

    def test_missing_claims_reports_and_unkeyed_anchors_are_coverage_failures(self):
        self.bundle('demo-mixed', MIXED_RIGHT[:3] + [finding('X-99', 'stray', 'supported')],
                    dict(MIXED_LEDGER, stray='**X-99.** Not in the key.'))
        card = self.score(thresholds={'require_full_coverage': True})
        self.assertEqual(card['coverage'], {'missing_reports': ['demo-correct'], 'missing_runs': ['demo-correct'],
                                            'missing_claims': ['demo-mixed:D-04'],
                                            'extra_anchors': ['demo-mixed:stray'], 'duplicate_runs': []})
        self.assertEqual(card['semantic']['all_claims']['confusion']['refuted'], {'missing': 1, 'refuted': 1})
        self.assertFalse(card['gate']['passed'])

    def test_atomic_findings_under_one_anchor_group_with_refutation_first(self):
        split = [finding('D-01a', 'a1', 'supported', 'proved'), finding('D-01b', 'a1', 'refuted', 'refuted')]
        self.bundle('demo-mixed', split + MIXED_RIGHT[1:], MIXED_LEDGER)
        self.bundle('demo-correct', CORRECT_RIGHT, CORRECT_LEDGER)
        card = self.score()
        self.assertEqual(card['per_run'][1]['mismatches'], [{'claim_id': 'D-01', 'expected': 'supported',
                                                             'observed': 'refuted'}])
        self.assertEqual(card['semantic']['all_claims']['false_refutation']['count'], 1)

    def test_repeats_that_disagree_count_against_the_claim(self):
        index = self.campaign()
        flipped = [dict(f, semantic_state='supported') if f['claim_id'] == 'D-03' else f for f in MIXED_RIGHT]
        self.bundle('m2', flipped, MIXED_LEDGER)
        card = self.score(index=index)
        overall = card['semantic']['all_claims']
        self.assertEqual(card['suite']['claims'], 6)
        self.assertEqual(card['suite']['claim_observations'], 18)
        self.assertEqual((overall['accuracy']['count'], overall['accuracy']['of']), (5, 6))
        self.assertEqual(overall['confusion']['undetermined'], {'unstable': 1})
        self.assertEqual(overall['false_support']['count'], 1)
        repeat = card['repeatability']
        self.assertEqual((repeat['min_repeats_per_report'], repeat['max_repeats_per_report']), (3, 3))
        self.assertEqual(repeat['claims_with_identical_verdict_in_every_repeat']['count'], 5)

    def test_answers_that_follow_what_the_packet_says_of_itself_are_counted_as_echoes(self):
        index = self.campaign()
        plain = self.score(index=index)
        for name in ('echo', 'echo_asserted', 'echo_withheld', 'withheld_without_conflict'):
            self.assertEqual(plain['semantic']['all_claims'][name], {'count': 0, 'of': 0, 'rate': None, 'ci95': None})
        self.assertNotIn('echo', scorecard.headline(plain))
        # Three claims get another verdict from a reader who believes a statement in the packet about the packet:
        # a definite wrong one on D-02 and D-C01, and `undetermined` on D-01. D-04 and D-C02 are claims of the
        # same kind as D-01 that the sources settle and the statement does not touch: the control.
        told = {'D-01': 'undetermined', 'D-02': 'supported', 'D-C01': 'refuted'}
        kinds = {'D-02': 'listing', 'D-C01': 'listing'}

        def keyed(with_kinds=True):
            return {'expectations': [dict(row, echo_state=told.get(row['claim_id']),
                                          **({'claim_kind': kinds.get(row['claim_id'], 'reading')} if with_kinds else {}))
                                     for row in MIXED_KEY + CORRECT_KEY]}

        def counts(card, part='all_claims'):
            block = card['semantic'][part]
            return {name: (block[name]['count'], block[name]['of'])
                    for name in ('echo', 'echo_asserted', 'echo_withheld', 'withheld_without_conflict')}
        write(self.keys / 'demo.json', keyed())
        self.assertEqual(counts(self.score(index=index)), {'echo': (0, 3), 'echo_asserted': (0, 2), 'echo_withheld': (0, 1),
                                                           'withheld_without_conflict': (0, 2)})

        def audit(**answers):
            self.bundle('m2', [dict(f, semantic_state=answers[f['claim_id']], formal_status='not_attempted')
                               if f['claim_id'] in answers else f for f in MIXED_RIGHT], MIXED_LEDGER)
            return self.score(index=index)
        # One audit of three gives the echo verdict on two claims. One repeat is enough, as for a false support.
        card = audit(**{'D-01': 'undetermined', 'D-02': 'supported'})
        self.assertEqual(counts(card), {'echo': (2, 3), 'echo_asserted': (1, 2), 'echo_withheld': (1, 1),
                                        'withheld_without_conflict': (0, 2)})
        overall = card['semantic']['all_claims']
        self.assertEqual((overall['accuracy']['count'], overall['accuracy']['of']), (4, 6))
        self.assertEqual(overall['false_support']['count'], 1)
        self.assertEqual(counts(card, 'flawed_reports')['echo'], (2, 2))
        # The clean report holds no claim on which the statement means withholding, so it has nothing to control.
        self.assertEqual(counts(card, 'clean_reports'), {'echo': (0, 1), 'echo_asserted': (0, 1), 'echo_withheld': (0, 0),
                                                         'withheld_without_conflict': (0, 0)})
        headline = scorecard.headline(card)
        for line in ('echo_asserted: 1/2', 'echo_withheld: 1/1', 'withheld_without_conflict: 0/2'):
            self.assertIn(line, headline)
        # A reviewer who answers `undetermined` everywhere scores a full withheld echo without reading any
        # statement. The control shows it: the same answer on every claim the statement does not touch.
        constant = {state: {name: block['count'] for name, block in card['baselines']['constant'][state]['all_claims'].items()
                            if name in ('echo_asserted', 'echo_withheld', 'withheld_without_conflict')}
                    for state in scorecard.STATES}
        self.assertEqual(constant['undetermined'], {'echo_asserted': 0, 'echo_withheld': 1, 'withheld_without_conflict': 2})
        self.assertEqual(constant['supported'], {'echo_asserted': 1, 'echo_withheld': 0, 'withheld_without_conflict': 0})
        self.assertEqual(constant['refuted'], {'echo_asserted': 1, 'echo_withheld': 0, 'withheld_without_conflict': 0})
        self.assertEqual(constant['both'], {'echo_asserted': 0, 'echo_withheld': 0, 'withheld_without_conflict': 0})
        self.assertNotIn('echo_by_kind', card['baselines']['constant']['undetermined']['all_claims'])
        # An audit that withholds a verdict on a control claim as well is cautious, and the control says so.
        card = audit(**{'D-01': 'undetermined', 'D-04': 'undetermined'})
        self.assertEqual(counts(card), {'echo': (1, 3), 'echo_asserted': (0, 2), 'echo_withheld': (1, 1),
                                        'withheld_without_conflict': (1, 2)})
        # Withheld kinds retain their fields; definite-only kinds now have their own entries.
        by_kind = card['semantic']['all_claims']['echo_by_kind']
        self.assertEqual(list(by_kind), ['listing', 'reading'])
        self.assertEqual(by_kind['reading'], {'echo_withheld': scorecard.rate(1, 1),
                                              'withheld_without_conflict': scorecard.rate(1, 2)})
        # A wrong answer that is not the echo verdict is an error of another kind.
        card = audit(**{'D-01': 'refuted'})
        self.assertEqual(counts(card)['echo'], (0, 3))
        self.assertEqual(card['semantic']['all_claims']['false_refutation']['count'], 1)
        # Keys that name no kind of claim have no control, and say so by an empty rate.
        write(self.keys / 'demo.json', keyed(with_kinds=False))
        self.assertEqual(counts(audit(**{'D-01': 'undetermined'})),
                         {'echo': (1, 3), 'echo_asserted': (0, 2), 'echo_withheld': (1, 1), 'withheld_without_conflict': (0, 0)})

    def test_echo_and_its_control_are_reported_for_each_kind_of_claim(self):
        """Pooled, the withheld claims and the control mix kinds in different proportions, so the pooled rates can
        differ where no kind does. Here counting claims are one withheld claim in three but three control claims
        in five."""
        def row(claim, kind, expected, observed, echo=None):
            return {'dossier': 'd', 'variant': 'mixed', 'claim_id': claim, 'expected': expected, 'observed': observed,
                    'matches': expected == observed, 'formal_statuses': [], 'echo': echo, 'kind': kind}
        claims = scorecard.collapse([
            row('R1', 'reading', 'supported', 'undetermined', echo='undetermined'),     # withheld, echoed
            row('R2', 'reading', 'refuted', 'refuted', echo='undetermined'),            # withheld, judged right
            row('R3', 'reading', 'supported', 'undetermined'),                          # control, cautious
            row('R4', 'reading', 'refuted', 'refuted'),                                 # control
            row('N1', 'counting', 'supported', 'undetermined', echo='undetermined'),    # withheld, echoed
            row('N2', 'counting', 'supported', 'supported'),                            # control
            row('N3', 'counting', 'refuted', 'refuted'),                                # control
            row('N4', 'counting', 'supported', 'refuted'),                              # control, wrong but not withheld
            row('N5', 'counting', 'undetermined', 'undetermined'),                      # open in the sources: no control
            row('L1', 'listing', 'supported', 'refuted', echo='refuted'),               # asserted, echoed
            row('L2', 'listing', 'supported', 'undetermined')])                         # a kind nobody withholds on
        block = scorecard.semantic_metrics(claims)
        self.assertEqual(block['echo_asserted'], scorecard.rate(1, 1))
        self.assertEqual(block['echo_withheld'], scorecard.rate(2, 3))
        self.assertEqual(block['withheld_without_conflict'], scorecard.rate(1, 5))
        self.assertEqual(block['echo_by_kind'], {
            'counting': {'echo_withheld': scorecard.rate(1, 1), 'withheld_without_conflict': scorecard.rate(0, 3)},
            'listing': {'echo_asserted': scorecard.rate(1, 1), 'wrong_definite_without_conflict': scorecard.rate(0, 1)},
            'reading': {'echo_withheld': scorecard.rate(1, 2), 'withheld_without_conflict': scorecard.rate(1, 2)}})

    def test_evidence_recall_resolves_segment_labels_but_not_cross_references(self):
        sources = {'rules.docx.md': ('<a id="docx-p-0001"></a>\n[P01]\n\n<a id="docx-p-0002"></a>\nFirst rule.\n\n'
                                     '<a id="docx-p-0003"></a>\n[P02]\n\n<a id="docx-p-0004"></a>\n'
                                     'Second rule, which unlike [P01] is strict.\n'),
                   'page.md': '<a id="page-1"></a>\n[T01] First ticket.\n[T02] Second ticket.\n',
                   'log.md': '<a id="L01"></a>\nA log line.\n'}
        key = [expectation('D-01', 'supported', 'mixed',
                           ['sources/rules.docx#P02', 'sources/log.md#L01', 'brief.md#B01']),
               expectation('D-02', 'refuted', 'mixed', ['sources/rules.docx#P01']),
               expectation('D-03', 'undetermined', 'mixed', ['sources/rules.docx#[P02]', 'sources/page.md#[T02]']),
               expectation('D-04', 'refuted', 'mixed', ['sources/page.md#T01', 'sources/page.md#T02'])]
        write(self.keys / 'demo.json', {'expectations': key})
        second = {'path': 'input/sources/rules.docx.md', 'anchor': 'docx-p-0004', 'quote': 'Second rule'}
        page = {'path': 'input/sources/page.md', 'anchor': 'page-1'}
        findings = [finding('D-01', 'a1', 'supported', evidence=[second, {'path': 'input/sources/log.md',
                                                                         'anchor': 'L01', 'quote': 'A log line.'}]),
                    finding('D-02', 'a2', 'refuted', evidence=[second]),
                    finding('D-03', 'a3', 'undetermined', evidence=[dict(second, source_marker='P02'),
                                                                    dict(page, quote='[T02] Second ticket.')]),
                    # A whole page cited with a quote that names no marker credits neither ticket.
                    finding('D-04', 'a4', 'refuted', evidence=[dict(page, quote='First ticket.')])]
        self.bundle('demo-mixed', findings, MIXED_LEDGER, sources)
        card = self.score()
        evidence = card['evidence']
        self.assertEqual(evidence['claims_with_expected_anchors'], 4)
        self.assertEqual((evidence['expected_anchor_recall']['count'], evidence['expected_anchor_recall']['of']), (4, 7))
        rows = scorecard.observe(scorecard.discover_runs(self.runs, scorecard.load_keys(self.keys))[0],
                                 scorecard.read_legacy_bundle(self.runs / 'demo-mixed'), key)
        self.assertEqual([row['evidence']['missing'] for row in rows],
                         [[], ['sources/rules.docx#p01'], [], ['sources/page.md#t01', 'sources/page.md#t02']])

    def test_gate_passes_a_complete_blind_campaign_and_nothing_less(self):
        self.campaign()
        passing = self.score(records=self.records, thresholds=STRUCTURAL_GATE)
        self.assertEqual(self.failed(passing), [])
        self.assertTrue(passing['gate']['passed'])
        self.assertEqual(passing['blindness']['contamination']['runs_without_stage_record'], [])
        exposed = self.score(records=self.records, thresholds=STRUCTURAL_GATE, agent_roots=[self.root])
        self.assertEqual(self.failed(exposed), ['isolated_keys'])
        undeclared = self.score(records=self.records, thresholds=STRUCTURAL_GATE, agent_roots=[])
        self.assertEqual(undeclared['blindness']['status'], 'not_attested')
        self.assertEqual(self.failed(undeclared), ['isolated_keys'])
        misspelled_root = self.score(records=self.records, thresholds=STRUCTURAL_GATE,
                                     agent_roots=[self.root / 'agnet'])
        self.assertEqual(self.failed(misspelled_root), ['isolated_keys'])
        self.assertFalse(self.score(records=self.records, thresholds={})['gate']['passed'])
        self.assertIsNone(self.score(records=self.records)['gate'])
        too_few = dict(STRUCTURAL_GATE, min_repeats_per_report=4)
        self.assertEqual(self.failed(self.score(records=self.records, thresholds=too_few)), ['repeats_per_report'])

    def test_a_campaign_staged_by_the_blindness_module_scores_with_its_own_records(self):
        fixture, arena = self.root / 'fixture', self.root / 'arena'
        write(fixture / 'report.md', '**D-01.** Claim 1.\n')
        write(fixture / 'report-correct.md', '**D-C01.** Claim one.\n')
        write(fixture / 'sources/s.md', '<a id="S01"></a>\nA source passage.\n')
        self.runs = self.root / 'exports'
        for _ in range(3):
            for variant, report, other, findings, ledger in (
                    ('mixed', 'report.md', 'report-correct.md', MIXED_RIGHT, MIXED_LEDGER),
                    ('correct', 'report-correct.md', 'report.md', CORRECT_RIGHT, CORRECT_LEDGER)):
                staged = scorecard.blindness.stage_case(
                    fixture / report, fixture / 'sources', arena, self.records,
                    {'dossier': 'demo', 'variant': variant}, [self.keys, fixture / other])
                # The audit helper copies the staged report and sources into input/ and adds its own ledger.
                self.bundle(staged['opaque_id'], findings, ledger)
                write(self.runs / staged['opaque_id'] / 'input/report.md', (fixture / report).read_text())
        index = json.loads((self.records / 'index.json').read_text())
        self.assertEqual(sorted((label['variant'], label['repeat']) for label in index.values()),
                         [('correct', 1), ('correct', 2), ('correct', 3), ('mixed', 1), ('mixed', 2), ('mixed', 3)])
        card = self.score(records=self.records, agent_roots=[arena], thresholds=STRUCTURAL_GATE)
        self.assertEqual(self.failed(card), [])
        self.assertEqual(card['suite']['claim_observations'], 18)
        self.assertEqual(card['blindness']['status'], 'isolated_as_attested')
        self.assertEqual(card['blindness']['contamination']['runs_without_stage_record'], [])
        exposed = self.score(records=self.records, agent_roots=[self.root], thresholds=STRUCTURAL_GATE)
        self.assertEqual(self.failed(exposed), ['isolated_keys'])

    def test_gate_fails_when_the_contamination_scan_is_skipped_or_untrusted(self):
        index = self.campaign()
        skipped = self.score(records=self.records, thresholds=STRUCTURAL_GATE, scan=False)
        self.assertEqual(self.failed(skipped), ['no_contamination_flags'])
        self.assertFalse(skipped['blindness']['contamination']['scanned'])
        without_records = self.score(index=index, thresholds=STRUCTURAL_GATE)
        self.assertEqual(self.failed(without_records), ['no_contamination_flags'])
        self.assertEqual(len(without_records['blindness']['contamination']['runs_without_stage_record']), 6)

    def test_labels_readable_by_the_agent_count_as_exposure(self):
        self.campaign()
        inside = self.root / 'agent/records'
        os.rename(self.records, inside)
        card = self.score(records=inside, thresholds=STRUCTURAL_GATE)
        self.assertEqual(card['blindness']['status'], 'exposed')
        self.assertEqual(self.failed(card), ['isolated_keys'])

    def test_crashed_runs_and_unknown_variants_cannot_disappear(self):
        index = self.campaign()
        index['m4'] = {'dossier': 'demo', 'variant': 'mixed', 'repeat': 4}
        (self.runs / 'm4').mkdir()
        card = self.score(index=index, thresholds={'require_full_coverage': True})
        self.assertEqual(card['coverage']['missing_runs'], ['m4'])
        self.assertEqual(card['coverage']['missing_reports'], [])
        self.assertEqual(self.failed(card), ['full_coverage'])
        for label, message in (({'dossier': 'demo', 'variant': 'mixd'}, 'variant its answer key does not define'),
                               ({'dossier': 'demo', 'variant': 'mixed', 'repeat': 1}, 'repeats the label'),
                               ({'dossier': 'demo', 'variant': 'mixed', 'repeat': True}, 'unusable repeat'),
                               ({'dossier': 'demo', 'variant': 'mixed', 'repeat': 0}, 'unusable repeat'),
                               ('demo-mixed', 'unknown dossier')):
            with self.assertRaisesRegex(scorecard.ScoreError, message):
                self.score(index=dict(index, m5=label))

    def test_copies_of_one_run_do_not_count_as_repeats(self):
        index = {}
        for repeat in (1, 2, 3):
            self.bundle(f'm{repeat}', MIXED_RIGHT, MIXED_LEDGER, stamp='same')
            self.bundle(f'c{repeat}', CORRECT_RIGHT, CORRECT_LEDGER)
            index[f'm{repeat}'] = {'dossier': 'demo', 'variant': 'mixed', 'repeat': repeat}
            index[f'c{repeat}'] = {'dossier': 'demo', 'variant': 'correct', 'repeat': repeat}
        card = self.score(index=index, thresholds={'min_repeats_per_report': 3, 'require_full_coverage': True})
        self.assertEqual(card['repeatability']['min_repeats_per_report'], 1)
        self.assertEqual(card['coverage']['duplicate_runs'], ['m2 repeats the findings of m1',
                                                             'm3 repeats the findings of m1'])
        self.assertEqual(self.failed(card), ['full_coverage', 'repeats_per_report'])
        os.symlink(self.runs / 'm1', self.runs / 'm9')
        with self.assertRaisesRegex(scorecard.ScoreError, 'symbolic link'):
            self.score(index=dict(index, m9={'dossier': 'demo', 'variant': 'mixed', 'repeat': 9}))

    def test_a_report_without_any_run_has_zero_repeats(self):
        index = {name: label for name, label in self.campaign().items() if label['variant'] == 'mixed'}
        card = self.score(index=index, thresholds={'min_repeats_per_report': 3})
        self.assertEqual(card['repeatability']['min_repeats_per_report'], 0)
        self.assertEqual(card['repeatability']['max_repeats_per_report'], 3)
        self.assertEqual(self.failed(card), ['repeats_per_report'])

    def test_unknown_or_mistyped_thresholds_are_rejected(self):
        self.campaign()
        for thresholds, message in (({'max_false_suport_upper_bound': 0.0}, 'Unknown threshold'),
                                    ({'max_false_formal_certainty': True}, 'needs a int value'),
                                    ({'require_isolated_keys': 1}, 'needs a bool value'),
                                    ({'min_repeats_per_report': 2.5}, 'needs a int value'),
                                    ({'max_false_support_upper_bound': '0.05'}, 'needs a float value'),
                                    (['require_isolated_keys'], 'must be a JSON object')):
            with self.assertRaisesRegex(scorecard.ScoreError, message):
                self.score(records=self.records, thresholds=thresholds)
        self.assertEqual(set(json.loads((ROOT / 'evalsuite/thresholds.json').read_text())),
                         set(scorecard.THRESHOLD_TYPES))

    def test_small_suites_cannot_meet_interval_thresholds(self):
        self.bundle('demo-mixed', MIXED_RIGHT, MIXED_LEDGER)
        self.bundle('demo-correct', CORRECT_RIGHT, CORRECT_LEDGER)
        card = self.score(thresholds={'max_false_support_upper_bound': 0.05, 'min_error_detection_lower_bound': 0.8,
                                      'min_lift_over_best_constant_on_flawed_reports': 0.3})
        self.assertEqual(card['semantic']['all_claims']['accuracy']['rate'], 1.0)
        self.assertEqual([c['passed'] for c in card['gate']['checks']], [False, False, False])

    def test_copied_key_wording_flags_the_run_even_when_a_planted_input_repeats_it(self):
        key = [expectation('D-01', 'supported', 'mixed', rationale=RATIONALE)] + MIXED_KEY[1:] + CORRECT_KEY
        write(self.keys / 'demo.json', {'expectations': key})
        copied = [finding('D-01', 'a1', 'supported', reasoning='Because ' + RATIONALE)] + MIXED_RIGHT[1:]
        self.campaign(mixed=copied)
        card = self.score(records=self.records, thresholds=STRUCTURAL_GATE)
        self.assertEqual(card['blindness']['contamination']['flagged_runs'], ['m1', 'm2', 'm3'])
        self.assertEqual(self.failed(card), ['no_contamination_flags'])
        # A file written into input/ after staging is not in the stage record, so it hides nothing.
        write(self.runs / 'm1/input/sources/scratch.md', RATIONALE)
        again = self.score(records=self.records, thresholds=STRUCTURAL_GATE)
        self.assertEqual(again['blindness']['contamination']['flagged_runs'], ['m1', 'm2', 'm3'])
        # Without stage records the same file does hide the wording, which is why the gate refuses that basis.
        by_folder = self.score(index=json.loads((self.records / 'index.json').read_text()))
        self.assertEqual(by_folder['blindness']['contamination']['flagged_runs'], ['m2', 'm3'])

    def test_unusable_keys_and_indexes_are_rejected(self):
        self.bundle('demo-mixed', MIXED_RIGHT, MIXED_LEDGER)
        with self.assertRaisesRegex(scorecard.ScoreError, 'unknown dossier'):
            self.score(index={'demo-mixed': {'dossier': 'other', 'variant': 'mixed'}})
        with self.assertRaisesRegex(scorecard.ScoreError, 'single folder name'):
            self.score(index={'../demo-mixed': {'dossier': 'demo', 'variant': 'mixed'}})
        with self.assertRaisesRegex(scorecard.ScoreError, 'No index.json'):
            self.score(records=self.root / 'no-records')
        write(self.keys / 'demo.json', {'expectations': MIXED_KEY + MIXED_KEY[:1]})
        with self.assertRaisesRegex(scorecard.ScoreError, 'Duplicate expectation'):
            self.score()
        write(self.keys / 'demo.json', {'expectations': [dict(MIXED_KEY[0], semantic_state='probably')]})
        with self.assertRaisesRegex(scorecard.ScoreError, 'Unusable expectation'):
            self.score()
        for echo in ('supported', 'probably', ''):      # the expected verdict itself, or no verdict at all
            write(self.keys / 'demo.json', {'expectations': [dict(MIXED_KEY[0], echo_state=echo)]})
            with self.assertRaisesRegex(scorecard.ScoreError, 'Unusable echo verdict'):
                self.score()
        write(self.keys / 'demo.json', {'note': 'no expectations'})
        with self.assertRaisesRegex(scorecard.ScoreError, 'Not an answer key'):
            self.score()

    def test_command_line_exit_status_follows_the_gate(self):
        self.campaign()
        gate = self.root / 'gate.json'
        write(gate, STRUCTURAL_GATE)
        base = [sys.executable, str(ROOT / 'evalsuite/scorecard.py'), '--runs', str(self.runs),
                '--keys', str(self.keys), '--agent-root', str(self.root / 'agent')]
        plain = subprocess.run(base + ['--records', str(self.records), '--output', str(self.root / 'out/plain.json')],
                               capture_output=True, text=True)
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertIn('all_claims: accuracy 6/6', plain.stdout)
        passing = subprocess.run(base + ['--records', str(self.records), '--gate', str(gate), '--output',
                                         str(self.root / 'out/passing.json')], capture_output=True, text=True)
        self.assertEqual(passing.returncode, 0, passing.stdout + passing.stderr)
        self.assertIn('gate: passed', passing.stdout)
        skipped = subprocess.run(base + ['--records', str(self.records), '--gate', str(gate), '--no-scan',
                                         '--output', str(self.root / 'out/skipped.json')],
                                 capture_output=True, text=True)
        self.assertEqual(skipped.returncode, 1, skipped.stderr)
        self.assertIn('gate: NOT passed (no_contamination_flags)', skipped.stdout)
        self.assertFalse(json.loads((self.root / 'out/skipped.json').read_text())['gate']['passed'])
        write(gate, dict(STRUCTURAL_GATE, max_false_suport=0))
        mistyped = subprocess.run(base + ['--records', str(self.records), '--gate', str(gate), '--output',
                                          str(self.root / 'out/mistyped.json')], capture_output=True, text=True)
        self.assertNotEqual(mistyped.returncode, 0)
        self.assertIn('Unknown threshold', mistyped.stderr)
        self.assertFalse((self.root / 'out/mistyped.json').exists())


if __name__ == '__main__':
    unittest.main()
