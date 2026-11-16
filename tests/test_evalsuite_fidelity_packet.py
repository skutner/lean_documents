"""Packet fidelity: only generated evaluator code and locally authored fixtures."""
from collections import Counter
from copy import deepcopy
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest

from evalsuite import fidelity_packet as packet, generate
from evalsuite.fidelity import FidelityError, LeanResult, trusted_lean_runner
from tests.lean_location import lean_path

ROOT = Path(__file__).resolve().parents[1]
(ROOT / 'tmp').mkdir(exist_ok=True)
RUN = Path(tempfile.mkdtemp(prefix='fidelity-packet-', dir=ROOT / 'tmp'))
LEAN_READY = bool(os.environ.get('AUDIT_TEST_LEAN')) and lean_path().is_file()


def material(seed=511, index=1):
    directory = Path(tempfile.mkdtemp(prefix=f'g{seed}-{index}-', dir=RUN))
    return packet.emit(generate.build_case('packet', seed, index)['key'], directory)


class PacketMaterialTests(unittest.TestCase):
    def test_ten_dossiers_coverage_and_all_unknown_combinations(self):
        counts, sizes = Counter(), set()
        for seed in (511, 512):
            for index in range(1, 6):
                m = material(seed, index)
                counts.update(c['kind'] for c in m.truth['claims'])
                count = len(m.truth['sealed_worlds'])
                self.assertEqual(count, 4 ** len(m.truth['unknowns']))
                sizes.add(count)
                self.assertEqual({c['id'].split('/')[0] for c in m.truth['claims']}, {'correct', 'mixed'})
                self.assertEqual(m.truth['unseen_count'], 264)
                for row, c in zip(m.regenerated.key['expectations'], m.truth['claims']):
                    self.assertEqual(len(c['sealed']), row['derivation']['worlds'])
                    self.assertEqual(sum(c['sealed']), row['derivation']['worlds_where_true'])
                    if c['supported']:
                        self.assertEqual(c['input_count'], len(c['sealed']) + len(c['unseen']))
                        self.assertEqual(set(c['sealed'] + c['unseen']), {True, False})
                    else:
                        self.assertTrue(c['reason'])
                        self.assertEqual(c['input_count'], 0)
                self.assertTrue(all(c is None for c, row in zip(m.specs, m.truth['claims']) if not row['supported']))
        self.assertEqual(set(counts), packet.SUPPORTED | packet.UNSUPPORTED.keys())
        self.assertEqual(sizes, {1, 4, 16, 64, 256})
        graded = sum(counts[k] for k in packet.SUPPORTED)
        self.assertEqual((graded, sum(counts.values())), (47, 140))
        print(f'\nPacket claim coverage: {graded}/{sum(counts.values())} = {graded / sum(counts.values()):.6%}; {dict(counts)}', flush=True)

    def test_unseen_boundaries_calendar_anchors_and_categories(self):
        m = material()
        rows = m.truth['rule_inputs']
        for period in packet.PERIODS:
            chosen = [x for x in rows if x['world']['max_age'] == period]
            for result in ('Pass', 'Fail'):
                ages = {x['world']['review_day'] - x['unit']['certificate']['tested_day']
                        for x in chosen if x['unit']['certificate']['result'] == result}
                self.assertTrue({0, 1, period - 1, period, period + 1} <= ages)
        self.assertIn(dt.date(2032, 2, 29).toordinal(), {x['world']['review_day'] for x in rows})
        self.assertEqual(len(rows), 264)
        first = m.truth['sealed_worlds'][0]
        for x in rows:
            self.assertLessEqual(x['unit']['certificate']['tested_day'], x['world']['review_day'])
            for old, moved in zip(first['units'], x['world']['units']):
                self.assertEqual(first['review_day'] - old['certificate']['tested_day'],
                                 x['world']['review_day'] - moved['certificate']['tested_day'])
        # Reconstruct every applicable claim input, including the untouched units.
        for c in m.truth['claims']:
            if not c['supported']:
                continue
            recipe = c['input_recipe']
            claim_worlds = m.truth['sealed_worlds'] + recipe['extra_worlds'] + [
                packet.replace(x['world'], ident, x['unit']['certificate'])
                for ident in recipe['replace_targets_with_rule_inputs'] for x in rows]
            for w in claim_worlds:
                self.assertTrue(all(u['certificate']['tested_day'] <= w['review_day'] for u in w['units']))
        # Representative dates of absent certificates encode every original class.
        for ident, choices in m.truth['unknowns']:
            self.assertIsNone(next(u['certificate'] for u in m.truth['recorded']['units'] if u['id'] == ident))
            self.assertEqual({(c['result'], m.truth['recorded']['review_day'] - c['tested_day'] <= m.truth['recorded']['max_age'])
                              for c in choices}, set(generate.OPEN_CERTIFICATE))

    def test_rejects_wrong_family_drift_reuse_and_unknown_slip(self):
        with self.assertRaisesRegex(FidelityError, 'packet key'):
            packet.emit(generate.build_case('ledger', 1, 1)['key'], RUN / 'wrong-family')
        m = material()
        with self.assertRaisesRegex(FidelityError, 'new or empty'):
            packet.emit(m.regenerated.key, m.directory)
        with self.assertRaisesRegex(FidelityError, 'Unknown fixture slip'):
            packet.fixture(m, slip='unknown')
        for field in ('version', 'seed'):
            key = deepcopy(m.regenerated.key)
            key['generator'][field] += 1
            with self.assertRaises(FidelityError):
                packet.emit(key, RUN / f'drift-{field}')

    def test_malformed_short_and_failed_execution_fail_closed(self):
        for result in (LeanResult(0, ''), LeanResult(0, '"FIDELITY|rule0|1"'),
                       LeanResult(0, '"FIDELITY|rule0|bad"'), LeanResult(1, 'error: rejected')):
            m = material()
            with self.assertRaises(FidelityError):
                packet.validate(m, lambda _: result)
            self.assertFalse(getattr(m, '_validated', False))


@unittest.skipUnless(LEAN_READY, 'AUDIT_TEST_LEAN is unset or does not name a Lean binary')
class PacketLeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = staticmethod(trusted_lean_runner(lean_path()))

    def test_ten_dossiers_two_seeds_keys_reference_and_strength(self):
        timings, kinds, graded, total = [], set(), 0, 0
        for seed in (511, 512):
            for index in range(1, 6):
                with self.subTest(seed=seed, index=index):
                    m = material(seed, index)
                    result = packet.validate(m, self.runner)
                    self.assertTrue(result['key_cross_check'])
                    self.assertEqual(set(result['key_cross_check_scope']), packet.SUPPORTED)
                    self.assertEqual(result['reference_agreement']['rate'], 1)
                    self.assertEqual(result['test_strength']['rate'], 1)
                    self.assertEqual(result['test_strength']['undetected'], [])
                    kinds.update(c['kind'] for c in m.truth['claims'] if c['supported'])
                    graded += result['claim_coverage']['graded']
                    total += result['claim_coverage']['total']
                    timings.append(round(result['seconds'], 3))
        self.assertEqual(kinds, packet.SUPPORTED)
        self.assertEqual((graded, total), (47, 140))
        print(f'\n10 dossier reference seconds: {timings}; unseen=264; strength=6/6 each; graded={graded}/{total}; artifacts={RUN}', flush=True)

    def test_faithful_fixture_kernel_proof_and_unsupported_fail_closed(self):
        m = material()
        result = packet.grade(m, packet.fixture(m), self.runner, prove=True)
        self.assertEqual(result['rule_fidelity']['rate'], 1)
        self.assertEqual(result['graded_claim_fidelity']['rate'], 1)
        self.assertEqual(result['claim_fidelity']['rate'], result['claim_coverage']['rate'])
        self.assertEqual(result['proof']['status'], 'kernel_checked')
        self.assertIn('does not depend on any axioms', result['proof']['axiom_report'])
        for c, scored in zip(m.truth['claims'], result['claims']):
            if not c['supported']:
                self.assertEqual(scored['status'], 'unsupported')
                self.assertFalse(scored['agrees_everywhere'])
        self.assertNotIn('namespace Packet.Reference', (m.directory / 'CandidateCheck.lean').read_text())
        print('\nFaithful:', result['rule_fidelity'], result['graded_claim_fidelity'], result['proof'], flush=True)

    def test_every_slip_below_one(self):
        for slip in packet.VARIANTS:
            with self.subTest(slip=slip):
                m = material()
                result = packet.grade(m, packet.fixture(m, slip=slip), self.runner, prove=True)
                self.assertLess(result['rule_fidelity']['rate'], 1)
                self.assertEqual(result['proof']['status'], 'not_equal')
                print(f'\n{slip}: {result["rule_fidelity"]}', flush=True)

    def test_wrong_item_claim_detected(self):
        m = material()
        self.assertTrue(any(c['kind'] in {'service_status', 'certificate_result'} for c in m.truth['claims']))
        result = packet.grade(m, packet.fixture(m, wrong_item=True), self.runner)
        self.assertEqual(result['rule_fidelity']['rate'], 1)
        self.assertLess(result['graded_claim_fidelity']['rate'], 1)
        for c, scored in zip(m.truth['claims'], result['claims']):
            if c['kind'] in {'service_status', 'certificate_result'}:
                self.assertFalse(scored['agrees_everywhere'])
        print('\nWrong item:', result['graded_claim_fidelity'], flush=True)

    def test_constant_claims_fail_when_truth_varies(self):
        m = material(512, 1)
        result = packet.grade(m, packet.fixture(m, constant=True), self.runner, prove=True, proof_limit=1)
        self.assertEqual(result['rule_fidelity']['rate'], 1)
        self.assertEqual(result['proof']['status'], 'skipped')
        for truth, scored in zip(m.truth['claims'], result['claims']):
            if truth['supported'] and len(set(truth['sealed'] + truth['unseen'])) > 1:
                self.assertFalse(scored['agrees_everywhere'])
        self.assertLess(result['graded_claim_fidelity']['rate'], 1)
        print('\nConstant claims:', result['graded_claim_fidelity'], flush=True)

    def test_corrupted_reference_and_incomplete_candidate_fail_closed(self):
        m = material()
        m.truth['rule_expected'][0] = not m.truth['rule_expected'][0]
        with self.assertRaisesRegex(FidelityError, 'reference disagrees'):
            packet.validate(m, self.runner)
        m = material()
        candidate = m.directory / 'Incomplete.lean'
        candidate.write_text('namespace Candidate\ndef model := 0\nend Candidate\n')
        with self.assertRaisesRegex(FidelityError, 'Lean rejected'):
            packet.grade(m, candidate, self.runner)
