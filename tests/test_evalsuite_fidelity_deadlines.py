"""Deadlines tests: only generated Lean and authored fixtures are executed."""
from copy import deepcopy
import datetime as dt
import itertools
import json
import os
from pathlib import Path
import tempfile
import unittest

from evalsuite import generate, fidelity_deadlines as deadlines
from evalsuite.fidelity import FidelityError, LeanResult, parse_vectors, regenerate, trusted_lean_runner
from tests.lean_location import lean_path

ROOT = Path(__file__).resolve().parents[1]
(ROOT / 'tmp').mkdir(exist_ok=True)
RUN = Path(tempfile.mkdtemp(prefix='fidelity-deadlines-', dir=ROOT / 'tmp'))
LEAN_READY = bool(os.environ.get('AUDIT_TEST_LEAN')) and lean_path().is_file()


def material(seed=511, index=1):
    folder = Path(tempfile.mkdtemp(prefix=f'g{seed}-{index}-', dir=RUN))
    return deadlines.emit(generate.build_case('deadlines', seed, index)['key'], folder)


class DeadlinesMaterialTests(unittest.TestCase):
    def test_material_serializes_dates_reports_and_all_unknown_combinations(self):
        for seed in (511, 512):
            for index in range(1, 6):
                with self.subTest(seed=seed, index=index):
                    m = material(seed, index)
                    saved = json.loads((m.directory / 'truth.json').read_text())
                    self.assertEqual(saved, deadlines.jsonable(m.truth))
                    self.assertEqual({c['id'].split('/')[0] for c in m.truth['claims']}, {'correct', 'mixed'})
                    base = dict(m.regenerated.case['base'], _policy=deadlines.policy(m.regenerated.case))
                    self.assertEqual(m.truth['sealed_worlds'], list(generate.worlds(base, m.truth['unknowns'])))
                    for row, c in zip(m.regenerated.key['expectations'], m.truth['claims']):
                        self.assertEqual(sum(c['sealed']), row['derivation']['worlds_where_true'])
                        self.assertEqual(len(c['sealed']), row['derivation']['worlds'])
                        self.assertEqual(c['input_count'], len(c['sealed']) + len(c['unseen']))
                    with self.assertRaisesRegex(FidelityError, 'new or empty'):
                        deadlines.emit(m.regenerated.key, m.directory)

    def test_grid_covers_every_boundary_category_period_and_unknown(self):
        m = material()
        rows = m.truth['rule_inputs']
        self.assertEqual(len(rows), 1245)
        self.assertEqual({(x['policy']['first'], x['policy']['second']) for x in rows},
                         set(itertools.permutations(deadlines.PERIODS, 2)))
        self.assertTrue({-1, 0, 1} <= {(x['notice']['issued'] - x['policy']['change']).days for x in rows})
        self.assertEqual({x['notice']['extension'] for x in rows}, {None, 'granted', 'refused', 'undecided'})
        self.assertEqual({x['grant'] for x in rows if x['notice']['extension'] == 'undecided'}, {False, True})
        self.assertEqual({x['notice']['days'] for x in rows}, set(deadlines.DAYS))
        self.assertEqual({x[2] for x in m.truth['rule_expected']}, {None, -1, 0, 1})
        self.assertTrue(any(x['notice']['reply'] is None and x['reply'] is not None for x in rows))
        self.assertTrue(any(x['notice']['issued'] == dt.date(2032, 2, 29) for x in rows))
        self.assertTrue(all(len(set(c['sealed'] + c['unseen'])) == 2 for c in m.truth['claims']))

    def test_ten_dossier_claim_coverage_and_generator_error_kinds(self):
        kinds, errors, sizes, periods = set(), set(), set(), set()
        graded = total = 0
        for seed in (511, 512):
            for index in range(1, 6):
                m = material(seed, index)
                graded += m.truth['coverage']['graded']
                total += m.truth['coverage']['total']
                kinds.update(c['kind'] for c in m.truth['claims'])
                sizes.add(len(m.truth['sealed_worlds']))
                periods.update((m.truth['recorded']['_policy']['first'], m.truth['recorded']['_policy']['second']))
                errors.update(c['error'] for good, bad in m.regenerated.case['slots'] for c in bad)
        self.assertEqual(kinds, deadlines.KINDS)
        self.assertEqual(graded, total)
        self.assertEqual(periods, set(deadlines.PERIODS))
        self.assertEqual(sizes, {1, 2, 152, 304})
        self.assertTrue({'wrong_rule_version', 'counts_notice_day', 'ignores_extension', 'assumes_extension_granted',
                         'flips_outcome', 'wrong_day_count', 'wrong_value', 'overstated',
                         'asserts_unknown_as_fact', 'treats_unknown_as_settled',
                         'overlooks_recorded_value', 'wrong_date'} <= errors)
        print(f'\nClaim coverage: {graded}/{total} = {graded/total:.1%}; kinds={sorted(kinds)}; unsupported=[]', flush=True)

    def test_policy_bridge_checks_derived_fields_and_sentence_drift(self):
        case = deepcopy(regenerate(generate.build_case('deadlines', 511, 1)['key']).case)
        case['base']['notices'][0]['period'] += 1
        with self.assertRaisesRegex(FidelityError, 'disagrees with policy'):
            deadlines.policy(case)
        case = deepcopy(regenerate(generate.build_case('deadlines', 511, 1)['key']).case)
        case['sources']['policy.md'] = case['sources']['policy.md'].replace('issued before', 'issued until')
        with self.assertRaisesRegex(FidelityError, 'policy sentences'):
            deadlines.policy(case)

    def test_wrong_family_version_key_and_unsupported_kind_fail_closed(self):
        with self.assertRaisesRegex(FidelityError, 'deadlines key'):
            deadlines.emit(generate.build_case('ledger', 1, 1)['key'], RUN / 'wrong-family')
        key = generate.build_case('deadlines', 511, 1)['key']
        key['generator']['version'] += 1
        with self.assertRaisesRegex(FidelityError, 'version mismatch'):
            deadlines.emit(key, RUN / 'wrong-version')
        key = generate.build_case('deadlines', 511, 1)['key']
        key['expectations'][0]['derivation']['worlds_where_true'] += 1
        with self.assertRaisesRegex(FidelityError, 'differs from regenerated'):
            deadlines.emit(key, RUN / 'wrong-key')
        with self.assertRaisesRegex(FidelityError, 'Unsupported deadlines claim.*no fact-test adapter'):
            deadlines.claim_spec({'pred': lambda w: True, 'kind': 'document_content'})
        with self.assertRaisesRegex(FidelityError, 'Unknown fixture slip'):
            deadlines.fixture(material(), slip='unknown')

    def test_malformed_duplicate_short_missing_vectors_fail_closed(self):
        good = '"DEADLINES|rule0|1,42,-1,0,1"'
        self.assertEqual(deadlines.parse_outcomes(good, ['rule0'], 1), {'rule0': [[1, 42, -1, False, True]]})
        for bad in ('', good, good + '\n' + good, good.replace('-1', 'bad'), good.replace('rule0', 'other')):
            with self.subTest(bad=bad), self.assertRaises(FidelityError):
                deadlines.parse_outcomes(bad, ['rule0'], 2)
        with self.assertRaises(FidelityError):
            parse_vectors('"FIDELITY|claim0|1"', {'claim0': 2})
        m = material()
        with self.assertRaisesRegex(FidelityError, 'Missing deadlines'):
            deadlines.validate(m, lambda path: LeanResult(0, ''))
        with self.assertRaisesRegex(FidelityError, 'distinct run directory'):
            deadlines.validate(m, lambda path: LeanResult(0, ''))


@unittest.skipUnless(LEAN_READY, 'AUDIT_TEST_LEAN is unset or does not name a Lean binary')
class DeadlinesLeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = staticmethod(trusted_lean_runner(lean_path()))

    def test_ten_dossiers_two_seeds_keys_references_strength_and_faithful(self):
        timings, coverage = [], set()
        for seed in (511, 512):
            for index in range(1, 6):
                with self.subTest(seed=seed, index=index):
                    m = material(seed, index)
                    result = deadlines.validate(m, self.runner)
                    self.assertTrue(result['key_cross_check'])
                    self.assertEqual(result['reference_agreement']['rate'], 1)
                    self.assertEqual(result['test_strength']['rate'], 1)
                    self.assertEqual(result['test_strength']['undetected'], [])
                    candidate = deadlines.grade(m, deadlines.fixture(m), self.runner)
                    self.assertEqual(candidate['rule_fidelity']['rate'], 1)
                    self.assertEqual(candidate['claim_fidelity']['rate'], 1)
                    coverage.update(c['kind'] for c in m.truth['claims'])
                    timings.append([round(result['seconds'], 3), round(candidate['seconds'], 3)])
                    print(f'\n{m.truth["dossier_id"]}: key=True reference=100% strength=10/10 faithful rule/claim=100%; seconds={timings[-1]}', flush=True)
        self.assertEqual(coverage, deadlines.KINDS)
        print(f'\n10 dossier reference/candidate seconds: {timings}; inputs=1245', flush=True)

    def test_faithful_kernel_agreement_without_axioms(self):
        m = material()
        result = deadlines.grade(m, deadlines.fixture(m), self.runner, prove=True)
        self.assertEqual(result['rule_fidelity']['rate'], 1)
        self.assertEqual(result['claim_fidelity']['rate'], 1)
        self.assertEqual(result['proof']['status'], 'kernel_checked')
        self.assertIn('does not depend on any axioms', result['proof']['axiom_report'])
        print('\nFaithful proof:', json.dumps(result['proof']), flush=True)

    def test_all_ten_slips_score_below_one(self):
        for slip in deadlines.VARIANTS:
            with self.subTest(slip=slip):
                m = material()
                result = deadlines.grade(m, deadlines.fixture(m, slip=slip), self.runner, prove=True)
                self.assertLess(result['rule_fidelity']['rate'], 1)
                self.assertEqual(result['proof']['status'], 'not_equal')
                print(f'\n{slip}: {result["rule_fidelity"]}', flush=True)

    def test_wrong_notice_claim_is_detected(self):
        m = material()
        result = deadlines.grade(m, deadlines.fixture(m, wrong_notice=True), self.runner)
        self.assertEqual(result['rule_fidelity']['rate'], 1)
        self.assertLess(result['claim_fidelity']['rate'], 1)
        print('\nWrong notice:', result['claim_fidelity'], flush=True)

    def test_constant_fails_every_varying_claim_and_proof_limit_is_explicit(self):
        m = material(512, 1)
        result = deadlines.grade(m, deadlines.fixture(m, constant=True), self.runner, prove=True, proof_limit=1)
        for truth, scored in zip(m.truth['claims'], result['claims']):
            if len(set(truth['sealed'] + truth['unseen'])) > 1:
                self.assertFalse(scored['agrees_everywhere'], truth['id'])
        self.assertLess(result['claim_fidelity']['rate'], 1)
        self.assertEqual(result['proof']['status'], 'skipped')
        print('\nConstant claims:', result['claim_fidelity'], flush=True)

    def test_corrupt_reference_and_incomplete_candidate_fail_closed(self):
        m = material()
        m.truth['rule_expected'][0][1] += 1
        with self.assertRaisesRegex(FidelityError, 'reference disagrees'):
            deadlines.validate(m, self.runner)
        m = material()
        candidate = m.directory / 'Incomplete.lean'
        candidate.write_text('namespace Candidate\ndef model := 0\nend Candidate\n')
        with self.assertRaisesRegex(FidelityError, 'Lean rejected'):
            deadlines.grade(m, candidate, self.runner)
