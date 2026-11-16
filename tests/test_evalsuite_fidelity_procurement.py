"""Procurement checks execute only generated programs and locally authored fixtures."""
from copy import deepcopy
import datetime as dt
import json
import os
import shutil
from pathlib import Path
import tempfile
import unittest

from evalsuite import generate
from evalsuite import fidelity_procurement as procurement
from evalsuite.fidelity import FidelityError, LeanResult, run_checked, trusted_lean_runner
from tests.lean_location import lean_path

ROOT = Path(__file__).resolve().parents[1]
(ROOT / 'tmp').mkdir(exist_ok=True)
RUN = Path(tempfile.mkdtemp(prefix='fidelity-procurement-', dir=ROOT / 'tmp'))
LEAN_READY = bool(os.environ.get('AUDIT_TEST_LEAN')) and lean_path().is_file()


def material(seed=511, index=1):
    return procurement.emit(generate.build_case('procurement', seed, index)['key'],
                            Path(tempfile.mkdtemp(prefix=f'g{seed}-{index}-', dir=RUN)))


class ProcurementMaterialTests(unittest.TestCase):
    def test_key_drift_version_family_and_output_reuse_fail_closed(self):
        m = material()
        with self.assertRaisesRegex(FidelityError, 'new or empty'):
            procurement.emit(m.regenerated.key, m.directory)
        for field in ('version', 'family'):
            key = deepcopy(m.regenerated.key)
            key['generator'][field] = 'invalid'
            with self.assertRaises(FidelityError): procurement.regenerate(key)
        key = deepcopy(m.regenerated.key)
        key['expectations'][0]['semantic_state'] = 'invented'
        with self.assertRaisesRegex(FidelityError, 'differs'): procurement.regenerate(key)
        with self.assertRaisesRegex(FidelityError, 'Unknown fixture slip'): procurement.fixture(m, slip='unknown')
        with self.assertRaisesRegex(FidelityError, 'Unsupported'):
            procurement.claim_body({'kind': 'document_contains', 'pred': lambda w: True}, 'rule')

    def test_complete_worlds_two_issues_predicate_truth_and_slips(self):
        m = material()
        d = m.regenerated.case['base']['data']
        self.assertEqual({k.split(':')[0] for k in m.truth['unknowns']}, {'amount', 'signer', 'last'})
        self.assertEqual(len(m.truth['sealed_worlds']), len(list(generate.worlds(m.regenerated.case['base'], m.regenerated.case['unknowns']))))
        sealed_count = len(m.truth['sealed_worlds'])
        self.assertEqual(m.truth['rule_inputs'][-sealed_count:], m.truth['sealed_worlds'])
        self.assertEqual(m.truth['rule_labels'][-sealed_count:], ['sealed-complete-register'] * sealed_count)
        self.assertEqual(tuple(name for name, flag in procurement.hard().SLIPS), procurement.VARIANTS)
        self.assertIn(d.policy_file, (m.directory / 'Facts.lean').read_text())
        self.assertIn(d.other_file, (m.directory / 'Facts.lean').read_text())
        self.assertEqual({c['id'].split('/')[0] for c in m.truth['claims']}, {'mixed', 'correct'})
        self.assertGreaterEqual(sum(c['expected_verdict'] == 'undetermined' for c in m.truth['claims']), 2)
        for row, c in zip(m.regenerated.key['expectations'], m.truth['claims']):
            self.assertEqual(sum(c['sealed']), row['derivation']['worlds_where_true'])
            self.assertEqual(len(c['sealed']), row['derivation']['worlds'])
            self.assertEqual(c['input_count'], len(c['sealed']) + len(c['unseen']))
        self.assertTrue(all(len(set(c['sealed'] + c['unseen'])) == 2 for c in m.truth['claims']))

    def test_unseen_boundaries_and_categories(self):
        reg = procurement.regenerate(generate.build_case('procurement', 511, 1)['key'])
        first = next(generate.worlds(reg.case['base'], reg.case['unknowns']))
        inputs, labels = procurement.rule_inputs(first)
        d = first['data']
        self.assertLessEqual(len(inputs), procurement.MAX_RULE_INPUTS)
        dates = {r['date'] for w in inputs for r in w['data'].requests}
        for edge in (d.issued, d.effective):
            self.assertTrue({edge + procurement.DAY * s for s in (-1, 0, 1)} <= dates)
        for a in d.delegations:
            for edge in [a['first']] + ([a['last']] if a['last'] else a['choices']):
                self.assertTrue({edge + procurement.DAY * s for s in (-1, 0, 1)} <= dates)
        self.assertIn(dt.date(2036, 2, 29), dates)
        self.assertIn(dt.date(2034, 12, 31), dates)
        self.assertEqual({w['data'].policy_file for w in inputs}, set(procurement.hard().POLICIES))
        self.assertEqual({r['currency'] for w in inputs for r in w['data'].requests}, {'EUR', 'USD', 'GBP', 'CHF'})
        for bound in {*d.limits, *d.amended, *d.previous, *[x for x in (d.exempt_in, d.exempt_out) if x is not None]}:
            self.assertTrue({bound - 1, bound, bound + 1} <= {r['amount'] for w in inputs for r in w['data'].requests})
        self.assertEqual({w['data'].exempt_in for w, label in zip(inputs, labels) if label == 'exemption/category'}, {None, 200, 250, 300})
        for flag in (flag for name, flag in procurement.hard().SLIPS):
            self.assertTrue(any(procurement.hard().assess(w['data'], w).rows !=
                                procurement.hard().assess(w['data'], w, frozenset([flag])).rows for w in inputs), flag)

    def test_malformed_short_and_duplicate_results_fail_closed(self):
        for output in ('', '"ROWS|rule0|probe,1,2"', '"ROWS|rule0|probe,1,2,3,4,2,1"',
                       '"ROWS|rule0|probe,1,2,3,4,1,1"\n"ROWS|rule0|probe,1,2,3,4,1,1"'):
            with self.assertRaises(FidelityError): procurement.parse_rows(output, ['rule0'], [1])
        m = material()
        with self.assertRaisesRegex(FidelityError, 'Missing'):
            procurement.validate(m, lambda p: LeanResult(0, ''))

    def test_ten_dossiers_two_seeds_coverage(self):
        kinds, counts, sizes = set(), [0, 0], []
        for seed in (511, 512):
            for index in range(1, 6):
                m = material(seed, index)
                kinds.update(c['kind'] for c in m.truth['claims'])
                counts[0] += m.truth['coverage']['graded']
                counts[1] += m.truth['coverage']['total']
                sizes.append(m.truth['unseen_count'])
        self.assertEqual(kinds, procurement.KINDS)
        self.assertEqual(counts[0], counts[1])
        print(f'\nProcurement coverage: {counts[0]}/{counts[1]} claims; kinds={sorted(kinds)}; unseen sizes={sizes}', flush=True)


@unittest.skipUnless(LEAN_READY, 'AUDIT_TEST_LEAN is unset or does not name a Lean binary')
class ProcurementLeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = staticmethod(trusted_lean_runner(lean_path(), timeout=300))
        cls.baseline = material()
        cls.baseline_result = procurement.validate(cls.baseline, cls.runner)

    def fresh_validated(self):
        # Reuse this exact in-process validated material, preserving immutable
        # reference evidence and giving each candidate its own execution folder.
        folder = Path(tempfile.mkdtemp(prefix='validated-clone-', dir=RUN))
        for name in ('Facts.lean', 'Inputs.lean', 'truth.json', 'key.json',
                     'ReferenceCheck.lean', 'ReferenceCheck.output.txt',
                     'ReferenceCheck.execution.json', 'reference-result.json'):
            shutil.copyfile(self.baseline.directory / name, folder / name)
        clone = procurement.Material(folder, deepcopy(self.baseline.truth),
                                     self.baseline.specs[:], self.baseline.regenerated)
        clone._validated = True
        return clone

    def test_ten_dossiers_two_seeds_reference_keys_strength(self):
        timings = []
        for seed in (511, 512):
            for index in range(1, 6):
                with self.subTest(seed=seed, index=index):
                    if (seed, index) == (511, 1):
                        m, checked = self.baseline, self.baseline_result
                    else:
                        m = material(seed, index)
                        checked = procurement.validate(m, self.runner)
                    self.assertTrue(checked['key_cross_check'])
                    self.assertEqual(checked['reference_agreement']['rate'], 1)
                    self.assertEqual(checked['test_strength']['rate'], 1)
                    timings.append({'seed': seed, 'index': index, 'inputs': m.truth['unseen_count'],
                                    'worlds': len(m.truth['sealed_worlds']), 'reference': checked['seconds']})
                    print('\nDossier:', json.dumps(timings[-1]), flush=True)
        (RUN / 'timings.json').write_text(json.dumps(timings, indent=2))

    def test_faithful_fixture_and_explicit_proof_limit(self):
        m = self.fresh_validated()
        graded = procurement.grade(m, procurement.fixture(m), self.runner, prove=True)
        self.assertEqual(graded['rule_fidelity']['rate'], 1)
        self.assertEqual(graded['claim_fidelity']['rate'], 1)
        self.assertEqual(graded['proof']['status'], 'skipped')
        self.assertIn('proof_limit', graded['proof']['reason'])
        print('\nFaithful:', graded['rule_fidelity'], graded['claim_fidelity'], graded['proof'], flush=True)

    def test_all_twelve_slips_below_one(self):
        # Compile authored fixture candidates together once; only rule fidelity is
        # relevant here. Each complete fixture is retained with its own namespace.
        m = self.fresh_validated()
        source = procurement.program(m, reference=False)
        for i, slip in enumerate(procurement.VARIANTS):
            path = procurement.fixture(m, slip=slip)
            saved = m.directory / (slip + '.lean')
            path.rename(saved)
            source += saved.read_text().replace('namespace Candidate', f'namespace Candidate{i}').replace(
                'end Candidate', f'end Candidate{i}')
            source += f'\n#eval emitRows "rule{i}" (unseen.map Candidate{i}.model.rule)\n'
        path = m.directory / 'SlipCandidates.lean'
        path.write_text(source)
        execution = run_checked(path, self.runner)
        actual = procurement.parse_rows(execution.stdout, [f'rule{i}' for i in range(12)],
                                        [len(rows) for rows in m.truth['rule_expected']])
        scores = {}
        for i, slip in enumerate(procurement.VARIANTS):
            score = procurement.row_agreement(actual[f'rule{i}'], m.truth['rule_expected'])
            self.assertLess(score['rate'], 1, slip)
            self.assertEqual(actual[f'rule{i}'], m.truth['variant_expected'][slip])
            scores[slip] = score
            print(f'\n{slip}: {score}', flush=True)
        (RUN / 'slip-scores.json').write_text(json.dumps(scores, indent=2))

    def test_wrong_item_claims(self):
        m = self.fresh_validated()
        result = procurement.grade(m, procurement.fixture(m, wrong_request=True), self.runner)
        self.assertEqual(result['rule_fidelity']['rate'], 1)
        self.assertLess(result['claim_fidelity']['rate'], 1)
        print('\nWrong item:', result['claim_fidelity'], flush=True)

    def test_constant_claims_fail_when_truth_varies(self):
        m = self.fresh_validated()
        result = procurement.grade(m, procurement.fixture(m, constant=True), self.runner)
        for truth, score in zip(m.truth['claims'], result['claims']):
            if len(set(truth['sealed'] + truth['unseen'])) > 1:
                self.assertFalse(score['agrees_everywhere'], truth['id'])
        self.assertLess(result['claim_fidelity']['rate'], 1)
        print('\nConstant:', result['claim_fidelity'], flush=True)

    def test_corrupt_oracle_and_incomplete_candidate_fail_closed(self):
        m = material()
        m.truth['rule_expected'][0][0]['tier'] = 99
        with self.assertRaisesRegex(FidelityError, 'reference disagrees'):
            procurement.validate(m, self.runner)
        m = self.fresh_validated()
        candidate = m.directory / 'Incomplete.lean'
        candidate.write_text('namespace Candidate\ndef model := 0\nend Candidate\n')
        with self.assertRaisesRegex(FidelityError, 'Lean rejected'):
            procurement.grade(m, candidate, self.runner)


if __name__ == '__main__': unittest.main()
