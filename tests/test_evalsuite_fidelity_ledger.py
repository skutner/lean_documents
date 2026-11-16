"""Ledger tests: only generated evaluator programs and authored fixtures run Lean."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest

from evalsuite import generate
from evalsuite import fidelity_ledger as ledger
from evalsuite.fidelity import FidelityError, LeanResult, regenerate, trusted_lean_runner
from tests.lean_location import lean_path

ROOT = Path(__file__).resolve().parents[1]
(ROOT / 'tmp').mkdir(exist_ok=True)
RUN = Path(tempfile.mkdtemp(prefix='fidelity-ledger-', dir=ROOT / 'tmp'))
LEAN_READY = bool(os.environ.get('AUDIT_TEST_LEAN')) and lean_path().is_file()


def material(seed=511, index=1):
    return ledger.emit(generate.build_case('ledger', seed, index)['key'],
                       Path(tempfile.mkdtemp(prefix=f'g{seed}-{index}-', dir=RUN)))


class LedgerMaterialTests(unittest.TestCase):
    def test_regeneration_drift_family_and_unknown_kind_fail_closed(self):
        key = generate.build_case('ledger', 511, 1)['key']
        altered = deepcopy(key)
        altered['generator']['version'] = -1
        with self.assertRaisesRegex(FidelityError, 'version mismatch'):
            ledger.emit(altered, RUN / 'bad-version')
        altered = deepcopy(key)
        altered['expectations'][0]['semantic_state'] = 'invented'
        with self.assertRaisesRegex(FidelityError, 'differs'):
            ledger.emit(altered, RUN / 'bad-key')
        with self.assertRaisesRegex(FidelityError, 'ledger key'):
            ledger.emit(generate.build_case('approvals', 511, 1)['key'], RUN / 'bad-family')
        with self.assertRaisesRegex(FidelityError, 'Unsupported ledger claim kind'):
            ledger.claim_spec({'kind': 'invented', 'pred': lambda w: True})

    def test_sealed_unknowns_both_reports_and_coverage_ten_dossiers(self):
        graded = total = 0
        kinds, sizes, errors = set(), set(), set()
        for seed in (511, 512):
            for index in range(1, 6):
                m = material(seed, index)
                reg = m.regenerated
                self.assertEqual(m.truth['sealed_worlds'], list(generate.worlds(
                    reg.case['base'], reg.case['unknowns'])))
                self.assertEqual({c['id'].split('/')[0] for c in m.truth['claims']}, {'correct', 'mixed'})
                for c, row in zip(m.truth['claims'], reg.key['expectations']):
                    self.assertEqual(sum(c['sealed']), row['derivation']['worlds_where_true'])
                    if c['supported']:
                        self.assertEqual(c['input_count'], len(c['sealed']) + len(c['unseen']))
                    else:
                        self.assertIsNone(c['spec'])
                        self.assertTrue(c['reason'])
                        self.assertEqual(c['input_count'], 0)
                    kinds.add(c['kind'])
                # Also inspect every planted option, not merely selected errors.
                errors.update(c['error'] for _, bad in reg.case['slots'] for c in bad)
                sizes.add(len(m.truth['sealed_worlds']))
                graded += m.truth['coverage']['graded']
                total += m.truth['coverage']['total']
        self.assertEqual(kinds, ledger.SUPPORTED | set(ledger.UNSUPPORTED))
        self.assertEqual((graded, total), (140, 180))
        self.assertGreater(len(sizes), 1)
        self.assertTrue({'arithmetic_slip', 'wrong_date', 'uses_superseded_amount',
                         'includes_voided_entry', 'reversed_direction', 'extrapolates_sample_rate',
                         'treats_missing_sheet_as_present'} <= errors)
        print(f'\nCoverage: {graded}/{total} = {graded / total:.6%}; sealed sizes={sorted(sizes)}; '
              f'all planted errors={sorted(errors)}', flush=True)

    def test_unseen_boundaries_categories_and_claim_truth_variation(self):
        m = material()
        worlds = m.truth['rule_inputs']
        self.assertEqual(len(worlds), 870)
        self.assertEqual({e['amount'] for w in worlds for e in w['entries']}, {0, 1, 2})
        self.assertEqual({e['approval'] for w in worlds for e in w['entries']}, {'yes', 'no', 'unknown'})
        self.assertEqual({e['voided'] for w in worlds for e in w['entries']}, {True, False})
        self.assertTrue({-1, 0, 1} <= {s['gap'] for s in m.truth['rule_expected']})
        self.assertTrue(any(w['entries'][0]['amount'] == w['entries'][1]['amount'] for w in worlds))
        self.assertEqual({w['unsampled_missing'] for w in worlds}, {0, 1, 2})
        for w in worlds:
            n = sum(not e['voided'] for e in w['entries'])
            self.assertGreater(n, 0)
            self.assertLessEqual(w['unsampled_missing'], w['population'] - n)
            for e in w['entries']:
                if e['approval'] == 'unknown':
                    self.assertIn('approval:' + e['id'], w)
        for c in m.truth['claims']:
            if c['supported']:
                self.assertEqual(set(c['sealed'] + c['unseen']), {False, True}, c['id'])
        with self.assertRaisesRegex(FidelityError, 'new or empty'):
            ledger.emit(m.regenerated.key, m.directory)
        with self.assertRaisesRegex(FidelityError, 'Unknown fixture slip'):
            ledger.fixture(material(), slip='typo')

    def test_lookup_is_world_ignoring_and_explicitly_unsupported(self):
        m = material()
        claim = next(c for c in m.regenerated.claims if c['kind'] == 'payment_lookup')
        w = deepcopy(m.truth['sealed_worlds'][0])
        expected = claim['pred'](w)
        w['entries'] = []
        self.assertEqual(claim['pred'](w), expected)
        self.assertIn('ignores the world', ledger.UNSUPPORTED['payment_lookup'])

    def test_malformed_missing_short_vectors_and_nonzero_runner_fail_closed(self):
        for output in ('', '"FIDELITY|rule0|1"\n', '"FIDELITY|rule0|x"\n'):
            with self.subTest(output=output):
                with self.assertRaises(FidelityError):
                    ledger.validate(material(), lambda path: LeanResult(0, output))
        with self.assertRaisesRegex(FidelityError, 'Lean rejected'):
            ledger.validate(material(), lambda path: LeanResult(1, '', 'compiler error'))

    def test_summary_protocol_and_amount_only_claim_are_not_accepted(self):
        valid = '"LEDGER|rule0|1,-2,P01,1,0,0,1,0"\n'
        parsed = ledger.parse_summaries(valid, {'rule0': 1})
        self.assertEqual(parsed['rule0'][0]['gap'], -2)
        for output in ('', valid + valid, valid.replace('rule0', 'rule1'),
                       valid.replace('1,-2', '-1,-2'), valid.replace('1,-2', 'x,-2'),
                       '"LEDGER|rule0|1,2"\n'):
            with self.assertRaises(FidelityError):
                ledger.parse_summaries(output, {'rule0': 1})
        with self.assertRaises(FidelityError):
            ledger.parse_summaries(valid, {'rule0': 2})
        m = material()
        self.assertNotIn('expectedSummaries', ledger.program(m, reference=False))
        for item in m.regenerated.claims:
            if item['kind'] == 'largest_payment':
                c = ledger.capture(item)
                extra = ledger.boundary_worlds(item, m.truth['sealed_worlds'][0])
                self.assertTrue(any((max(e['amount'] for e in w['entries'] if not e['voided']) ==
                                     c['amount']) != bool(item['pred'](w)) for w in extra))


@unittest.skipUnless(LEAN_READY, 'AUDIT_TEST_LEAN is unset or does not name a Lean binary')
class LedgerLeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = staticmethod(trusted_lean_runner(lean_path()))

    def test_ten_dossiers_two_seeds_reference_key_strength_and_faithful(self):
        timings, candidate_times = [], []
        for seed in (511, 512):
            for index in range(1, 6):
                with self.subTest(seed=seed, index=index):
                    m = material(seed, index)
                    result = ledger.validate(m, self.runner)
                    self.assertTrue(result['key_cross_check'])
                    self.assertEqual(result['reference_agreement']['rate'], 1)
                    self.assertEqual(result['test_strength']['rate'], 1)
                    self.assertEqual(result['lean_checked_claims'], 14)
                    self.assertEqual(result['unsupported_claims'], 4)
                    timings.append(round(result['seconds'], 3))
                    scored = ledger.grade(m, ledger.fixture(m), self.runner)
                    self.assertEqual(scored['rule_fidelity']['rate'], 1)
                    self.assertEqual(scored['claim_fidelity']['rate'], 1)
                    self.assertEqual(scored['all_claims_fidelity']['rate'], 14 / 18)
                    for c in scored['claims']:
                        if c['status'] == 'unsupported':
                            self.assertFalse(c['agrees_everywhere'])
                            self.assertIsNone(c['rate'])
                    candidate_times.append(round(scored['seconds'], 3))
        print(f'\n10 dossiers reference seconds={timings}; faithful seconds={candidate_times}; '
              'unseen=870; strength=10/10 each; coverage=140/180', flush=True)

    def test_kernel_agreement_without_axioms(self):
        m = material()
        result = ledger.grade(m, ledger.fixture(m), self.runner, prove=True)
        self.assertEqual(result['proof']['status'], 'kernel_checked')
        self.assertIn('does not depend on any axioms', result['proof']['axiom_report'])
        print('\nKernel proof:', json.dumps(result['proof']), flush=True)

    def test_all_ten_slips_below_one(self):
        for slip in ledger.VARIANTS:
            with self.subTest(slip=slip):
                m = material()
                result = ledger.grade(m, ledger.fixture(m, slip=slip), self.runner, prove=True)
                self.assertLess(result['rule_fidelity']['rate'], 1)
                self.assertEqual(result['proof']['status'], 'not_equal')
                print(f'\n{slip}: {result["rule_fidelity"]}', flush=True)

    def test_wrong_item_claim(self):
        m = material()
        result = ledger.grade(m, ledger.fixture(m, wrong_item=True), self.runner)
        self.assertEqual(result['rule_fidelity']['rate'], 1)
        self.assertLess(result['claim_fidelity']['rate'], 1)
        for c, scored in zip(m.truth['claims'], result['claims']):
            if c['kind'] == 'largest_payment':
                self.assertFalse(scored['agrees_everywhere'])
        print('\nWrong item:', result['claim_fidelity'], flush=True)

    def test_largest_claim_cannot_omit_identity(self):
        m = material()
        result = ledger.grade(m, ledger.fixture(m, omit_item=True), self.runner)
        self.assertEqual(result['rule_fidelity']['rate'], 1)
        for c, scored in zip(m.truth['claims'], result['claims']):
            if c['kind'] == 'largest_payment':
                self.assertFalse(scored['agrees_everywhere'])
        self.assertLess(result['claim_fidelity']['rate'], 1)

    def test_constant_claims_and_proof_bound(self):
        m = material(512, 1)
        result = ledger.grade(m, ledger.fixture(m, constant=True), self.runner, prove=True, proof_limit=1)
        for c, scored in zip(m.truth['claims'], result['claims']):
            if c['supported'] and len(set(c['sealed'] + c['unseen'])) > 1:
                self.assertFalse(scored['agrees_everywhere'], c['id'])
        self.assertLess(result['claim_fidelity']['rate'], 1)
        self.assertEqual(result['proof']['status'], 'skipped')
        print('\nConstant claims:', result['claim_fidelity'], flush=True)

    def test_corrupt_reference_or_incomplete_candidate_rejected(self):
        m = material()
        m.truth['rule_expected'][0]['total'] += 1
        with self.assertRaisesRegex(FidelityError, 'reference disagrees'):
            ledger.validate(m, self.runner)
        m = material()
        candidate = m.directory / 'Incomplete.lean'
        candidate.write_text('namespace Candidate\ndef model := 0\nend Candidate\n')
        with self.assertRaisesRegex(FidelityError, 'Lean rejected'):
            ledger.grade(m, candidate, self.runner)
