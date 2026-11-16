"""Approvals oracle tests; Lean runs only generated code and our authored fixtures."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from evalsuite import generate
from evalsuite import fidelity_approvals as approvals
from evalsuite.fidelity import FidelityError, regenerate, trusted_lean_runner
from tests.lean_location import lean_path

ROOT = Path(__file__).resolve().parents[1]
(ROOT / 'tmp').mkdir(exist_ok=True)
RUN = Path(tempfile.mkdtemp(prefix='fidelity-approvals-', dir=ROOT / 'tmp'))
LEAN_READY = bool(os.environ.get('AUDIT_TEST_LEAN')) and lean_path().is_file()


def material(seed=511, index=3):
    folder = Path(tempfile.mkdtemp(prefix=f'g{seed}-{index}-', dir=RUN))
    return approvals.emit(generate.build_case('approvals', seed, index)['key'], folder)


class ApprovalsMaterialTests(unittest.TestCase):
    def test_probe_regeneration_matches_all_six_requests_and_roles(self):
        reg = regenerate(generate.build_case('approvals', 511, 3)['key'])
        rows = reg.case['base']['requests']
        self.assertEqual([(r['id'], r['requester'], r['amount'], r['signers']) for r in rows], [
            ('Q01', 'Di Ebner', 10600, ['Cy Dorn', 'Di Ebner']),
            ('Q02', 'Fay Gould', 7300, ['Gus Hale', 'Di Ebner']),
            ('Q03', 'Fay Gould', 650, ['Gus Hale', None]),
            ('Q04', 'Ivy Jansen', 4350, ['Bo Castell', None]),
            ('Q05', 'Ada Brandt', 6000, ['Cy Dorn', 'Fay Gould']),
            ('Q06', 'Gus Hale', 5000, ['Ada Brandt', None])])
        self.assertEqual(reg.case['base']['limit'], 5000)
        self.assertEqual(reg.case['unknowns'], {})
        self.assertEqual(reg.case['base']['roles'], {'Ada Brandt': 'Finance officer', 'Bo Castell': 'Analyst',
            'Cy Dorn': 'Finance officer', 'Di Ebner': 'Manager', 'Fay Gould': 'Director',
            'Gus Hale': 'Manager', 'Ivy Jansen': 'Analyst'})

    def test_material_contains_oracle_truth_for_both_reports_and_complete_unknowns(self):
        m = material(512, 1)
        truth = json.loads((m.directory / 'truth.json').read_text())
        self.assertEqual({c['id'].split('/')[0] for c in truth['claims']}, {'correct', 'mixed'})
        self.assertEqual(truth['unseen_count'], 1281)
        self.assertEqual(truth['sealed_worlds'], list(generate.worlds(m.regenerated.case['base'], m.regenerated.case['unknowns'])))
        for row, c in zip(m.regenerated.key['expectations'], truth['claims']):
            self.assertEqual(sum(c['sealed']), row['derivation']['worlds_where_true'])
            self.assertEqual(c['input_count'], len(c['sealed']) + len(c['unseen']))
        with self.assertRaisesRegex(FidelityError, 'new or empty'):
            approvals.emit(m.regenerated.key, m.directory)

    def test_unseen_grid_covers_threshold_roles_duplicate_and_self_signatures(self):
        m = material()
        rows = m.truth['rule_inputs']
        self.assertEqual({x['request']['amount'] for x in rows}, {4999, 5000, 5001})
        self.assertTrue(any(len(x['request']['signers']) == 2 and len(set(x['request']['signers'])) == 1 for x in rows))
        self.assertTrue(any(x['request']['requester'] in x['request']['signers'] for x in rows))
        for person in m.truth['recorded']['roles']:
            self.assertEqual({x['roles'][person] for x in rows}, set(approvals.ROLES))
        self.assertTrue(all({False, True} == set(c['sealed'] + c['unseen']) for c in m.truth['claims']))

    def test_rejects_wrong_family_and_unknown_slip(self):
        with self.assertRaisesRegex(FidelityError, 'approvals key'):
            approvals.emit(generate.build_case('ledger', 1, 1)['key'], RUN / 'wrong-family')
        with self.assertRaisesRegex(FidelityError, 'Unknown fixture slip'):
            approvals.fixture(material(), slip='unknown')


@unittest.skipUnless(LEAN_READY, 'AUDIT_TEST_LEAN is unset or does not name a Lean binary')
class ApprovalsLeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = staticmethod(trusted_lean_runner(lean_path()))

    def test_ten_dossiers_two_seeds_keys_references_and_mutation_strength(self):
        coverage, thresholds, sizes, timings = set(), set(), set(), []
        for seed in (511, 512):
            for index in range(1, 6):
                with self.subTest(seed=seed, index=index):
                    m = material(seed, index)
                    result = approvals.validate(m, self.runner)
                    self.assertTrue(result['key_cross_check'])
                    self.assertEqual(result['reference_agreement']['rate'], 1)
                    self.assertEqual(result['test_strength']['rate'], 1)
                    self.assertEqual(result['test_strength']['undetected'], [])
                    coverage.update(c['kind'] for c in m.truth['claims'])
                    thresholds.add(m.truth['recorded']['limit'])
                    sizes.add(len(m.truth['sealed_worlds']))
                    timings.append(round(result['seconds'], 3))
        self.assertEqual(coverage, {'policy_compliance', 'approval_tier', 'policy_failures',
                                   'request_lookup', 'signer_role', 'illegible_signature'})
        self.assertEqual(thresholds, {2000, 5000, 10000})
        self.assertEqual(sizes, {1, 7})
        print(f'\n10 dossier reference checks seconds: {timings}; inputs=1281; strength=4/4 each', flush=True)

    def test_faithful_probe_candidate_and_kernel_agreement_without_axioms(self):
        m = material()
        result = approvals.grade(m, approvals.fixture(m), self.runner, prove=True)
        self.assertEqual(result['rule_fidelity']['rate'], 1)
        self.assertEqual(result['claim_fidelity']['rate'], 1)
        self.assertEqual(result['proof']['status'], 'kernel_checked')
        self.assertIn('does not depend on any axioms', result['proof']['axiom_report'])
        print('\nFaithful fixture:', json.dumps(result['proof']), flush=True)

    def test_all_four_slips_score_below_one(self):
        for slip in approvals.VARIANTS:
            with self.subTest(slip=slip):
                m = material()
                result = approvals.grade(m, approvals.fixture(m, slip=slip), self.runner, prove=True)
                self.assertLess(result['rule_fidelity']['rate'], 1)
                self.assertEqual(result['proof']['status'], 'not_equal')
                print(f'\n{slip}: {result["rule_fidelity"]}', flush=True)

    def test_wrong_request_claim_is_detected(self):
        m = material()
        result = approvals.grade(m, approvals.fixture(m, wrong_request=True), self.runner)
        self.assertEqual(result['rule_fidelity']['rate'], 1)
        self.assertLess(result['claim_fidelity']['rate'], 1)
        print('\nWrong request:', result['claim_fidelity'], flush=True)

    def test_lookup_cannot_omit_requester_away_from_threshold(self):
        m = material(511, 1)
        result = approvals.grade(m, approvals.fixture(m, omit_requester=True), self.runner)
        for truth, scored in zip(m.truth['claims'], result['claims']):
            if truth['kind'] == 'request_lookup':
                self.assertFalse(scored['agrees_everywhere'], truth['id'])
        self.assertEqual(result['rule_fidelity']['rate'], 1)
        self.assertLess(result['claim_fidelity']['rate'], 1)

    def test_world_ignoring_constant_fails_every_varying_claim(self):
        m = material(512, 1)
        result = approvals.grade(m, approvals.fixture(m, constant=True), self.runner, prove=True, proof_limit=1)
        for truth, scored in zip(m.truth['claims'], result['claims']):
            if len(set(truth['sealed'] + truth['unseen'])) > 1:
                self.assertFalse(scored['agrees_everywhere'], truth['id'])
        self.assertLess(result['claim_fidelity']['rate'], 1)
        self.assertEqual(result['proof']['status'], 'skipped')
        print('\nConstant claims:', result['claim_fidelity'], flush=True)

    def test_corrupted_reference_and_short_candidate_fail_closed(self):
        m = material()
        m.truth['rule_expected'][0] = not m.truth['rule_expected'][0]
        with self.assertRaisesRegex(FidelityError, 'reference disagrees'):
            approvals.validate(m, self.runner)
        m = material()
        candidate = m.directory / 'Incomplete.lean'
        candidate.write_text('namespace Candidate\ndef model := 0\nend Candidate\n')
        with self.assertRaisesRegex(FidelityError, 'Lean rejected'):
            approvals.grade(m, candidate, self.runner)
