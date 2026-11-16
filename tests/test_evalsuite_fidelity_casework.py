"""Casework differential checks; execute generated programs/authored fixtures only."""
from copy import deepcopy
import json
import hashlib
import os
import shutil
from pathlib import Path
import tempfile
import unittest

from evalsuite import generate
from evalsuite import fidelity_casework as casework
from evalsuite.fidelity import FidelityError, run_checked, trusted_lean_runner
from tests.lean_location import lean_path

ROOT = Path(__file__).resolve().parents[1]
(ROOT/'tmp').mkdir(exist_ok=True)
RUN = Path(tempfile.mkdtemp(prefix='fidelity-casework-', dir=ROOT/'tmp'))
LEAN_READY = bool(os.environ.get('AUDIT_TEST_LEAN')) and lean_path().is_file()


_MATERIALS = {}


def material(seed=511, index=1):
    folder = Path(tempfile.mkdtemp(prefix=f'g{seed}-{index}-', dir=RUN))
    key = (seed,index)
    if key not in _MATERIALS:
        _MATERIALS[key] = casework.emit(generate.build_case('casework', seed, index)['key'], folder)
        fresh = _MATERIALS[key]
        _MATERIALS[key] = casework.Material(fresh.directory, deepcopy(fresh.truth), list(fresh.specs), fresh.regenerated)
        return fresh
    original = _MATERIALS[key]
    for name in ('Facts.lean','Inputs.lean','truth.json','key.json'):
        shutil.copy2(original.directory/name, folder/name)
    return casework.Material(folder, deepcopy(original.truth), list(original.specs), original.regenerated)


class CaseworkMaterialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = material()

    def test_regeneration_versions_drift_and_wrong_family_fail_closed(self):
        key = deepcopy(self.m.regenerated.key)
        key['generator']['version'] = -1
        with self.assertRaisesRegex(FidelityError, 'version mismatch'):
            casework.regenerate_casework(key)
        key = deepcopy(self.m.regenerated.key)
        key['expectations'][0]['semantic_state'] = 'changed'
        with self.assertRaisesRegex(FidelityError, 'differs'):
            casework.regenerate_casework(key)
        with self.assertRaisesRegex(FidelityError, 'casework key'):
            casework.regenerate_casework(generate.build_case('ledger', 1, 1)['key'])
        with self.assertRaisesRegex(FidelityError, 'new or empty'):
            casework.emit(self.m.regenerated.key, self.m.directory)
        with self.assertRaisesRegex(FidelityError, 'Unknown fixture slip'):
            casework.fixture(self.m, slip='unknown')

    def test_both_issues_index_and_all_unknown_completions(self):
        t = self.m.truth
        self.assertEqual(len(t['recorded']['registers']), 2)
        self.assertIn(t['recorded']['in_force'], (0, 1))
        expected = list(generate.worlds(self.m.regenerated.case['base'], self.m.regenerated.case['unknowns']))
        self.assertEqual(len(t['sealed_worlds']), len(expected))
        self.assertGreaterEqual(len(t['unknowns']), 3)
        for w, e in zip(t['sealed_worlds'], expected):
            self.assertEqual(w['resolutions'], {k:v.isoformat() for k,v in e.items() if k != 'notices'})
        self.assertEqual({c['id'].split('/')[0] for c in t['claims']}, {'correct', 'mixed'})
        self.assertGreaterEqual(sum(c['expected_verdict']=='undetermined' for c in t['claims']), 2)
        for row, c in zip(self.m.regenerated.key['expectations'], t['claims']):
            self.assertEqual(sum(c['sealed']), row['derivation']['worlds_where_true'])
            self.assertEqual(c['input_count'], len(c['sealed']) + len(c['unseen']))

    def test_unseen_boundaries_categories_and_all_slip_witnesses(self):
        import datetime as dt
        t = self.m.truth
        probes = t['rule_inputs']
        self.assertLessEqual(len(probes), casework.MAX_RULE_INPUTS)
        self.assertEqual({p['boundary'] for p in probes}, {'recorded-row', 'amendment-boundary',
            'deadline-weekday-holiday-receipt', 'extension-boundary', 'postal-boundary', 'index-choice'})
        notices = [p['world']['registers'][p['world']['in_force']][0] for p in probes]
        self.assertEqual({n['cls'] for n in notices}, {'standard','complex','urgent'})
        self.assertEqual({n['channel'] for n in notices}, {'Electronic','Hand','Post'})
        self.assertEqual({dt.date.fromisoformat(n['sent']).weekday() for n in notices if n['sent']}, set(range(7)))
        self.assertEqual({p['world']['in_force'] for p in probes if p['boundary']=='index-choice'}, {0,1})
        self.assertEqual({e['decision'] for p in probes for e in p['world']['extensions']}, {'granted','refused','pending'})
        for v in casework.VARIANTS:
            self.assertNotEqual(t['rule_expected'], t['variant_expected'][v], v)
        for c in t['claims']:
            self.assertLessEqual(c['input_count'], casework.MAX_CLAIM_INPUTS)

    def test_counterfactuals_preserve_alternate_issue_completions_and_attain_counts(self):
        reg, d = casework.regenerate_casework(self.m.regenerated.key)
        recorded = casework.recorded_world(d)
        w = next(generate.worlds(reg.case['base'],reg.case['unknowns']))
        first = dict(recorded,resolutions={k:v for k,v in w.items() if k != 'notices'})
        for mode in range(4):
            for count in (0,1,len(d.notices)//2,len(d.notices)):
                altered = casework.count_world(first,mode,count)
                results = [casework.oracle_rule(altered,n['id']) for n in casework.selected(altered)]
                field = ('late','shifted','took','late')[mode]
                self.assertEqual(sum(getattr(r,field) for r in results),count)
                for n in casework.selected(altered):
                    casework.oracle_rule(altered,n['id'],'uses_draft_register')
        for item in reg.claims:
            if item['kind'] in ('receipt_date','days_late'):
                for altered in casework.threshold_worlds(first,item,d.model):
                    for n in casework.selected(altered):
                        casework.oracle_rule(altered,n['id'],'uses_draft_register')

    def test_protocol_rejects_short_duplicate_malformed_and_missing_outputs(self):
        valid = '"CASEWORK|rule0|1,1,0,0,1,0,0"'
        self.assertEqual(casework.parse_results(valid, {'rule0':1})['rule0'], [[1,1,0,0,1,0,0]])
        for output, count in ((valid,2),(valid+'\n'+valid,1),('"CASEWORK|rule0|1,2"',1),('',1),
                              ('"CASEWORK|rule0|1,1,7,0,1,0,0"',1)):
            with self.assertRaises(FidelityError):
                casework.parse_results(output, {'rule0':count})


@unittest.skipUnless(LEAN_READY, 'AUDIT_TEST_LEAN is unset or does not name a Lean binary')
class CaseworkLeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = staticmethod(trusted_lean_runner(lean_path(), timeout=300))

    def test_ten_dossiers_two_seeds_keys_reference_strength_and_faithful(self):
        timings, sizes, kinds, total, graded = [], [], set(), 0, 0
        for seed in (511,512):
            for index in range(1,6):
                with self.subTest(seed=seed,index=index):
                    m = material(seed,index)
                    report = casework.validate(m,self.runner)
                    self.assertTrue(report['key_cross_check'])
                    self.assertEqual(report['reference_agreement']['rate'],1)
                    self.assertEqual(report['test_strength']['rate'],1)
                    score = casework.grade(m,casework.fixture(m),self.runner)
                    self.assertEqual(score['rule_fidelity']['rate'],1)
                    self.assertEqual(score['claim_fidelity']['rate'],1)
                    timings.append([round(report['seconds'],3),round(score['seconds'],3)])
                    sizes.append(m.truth['unseen_count'])
                    kinds.update(c['kind'] for c in m.truth['claims'])
                    total += m.truth['coverage']['total']
                    graded += m.truth['coverage']['graded']
        self.assertEqual(kinds,casework.KINDS)
        self.assertEqual(graded,total)
        print(f'\n10 dossiers: reference/candidate seconds={timings}; unseen={sizes}; graded={graded}/{total}; strength=18/18 each',flush=True)

    def test_faithful_kernel_agreement(self):
        m = material()
        result = casework.grade(m,casework.fixture(m),self.runner,prove=True)
        self.assertEqual(result['rule_fidelity']['rate'],1)
        self.assertEqual(result['claim_fidelity']['rate'],1)
        self.assertEqual(result['proof']['status'],'kernel_checked')
        self.assertIn('does not depend on any axioms',result['proof']['axiom_report'])
        print('\nFaithful proof:',json.dumps(result['proof']),flush=True)

    def test_all_eighteen_slips_below_one(self):
        # Compile shared unseen facts once; every authored candidate is still run.
        # Each original fixture is retained in its own directory. Namespace renaming
        # is the only transformation; no reference implementation is in this program.
        m = material()
        casework.validate(m,self.runner)
        source = casework.program(m,reference=False)
        fixtures = {}
        for j, slip in enumerate(casework.VARIANTS):
            candidate_material = material()
            path = casework.fixture(candidate_material,slip=slip)
            fixtures[slip] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            source += '\n' + path.read_text().replace('namespace Candidate',f'namespace Fixture{j}').replace(
                'end Candidate',f'end Fixture{j}') + '\n'
            source += f'#eval emitResults "slip{j}" (unseen.map (fun x => Fixture{j}.model.rule x.1 x.2))\n'
        path = m.directory/'SlipCandidates.lean'
        path.write_text(source)
        result = run_checked(path,self.runner)
        vectors = casework.parse_results(result.stdout,{f'slip{j}':m.truth['unseen_count'] for j in range(len(casework.VARIANTS))})
        for j, slip in enumerate(casework.VARIANTS):
            with self.subTest(slip=slip):
                self.assertEqual(vectors[f'slip{j}'],m.truth['variant_expected'][slip])
                score = casework.result_agreement(vectors[f'slip{j}'],m.truth['rule_expected'])
                self.assertLess(score['rate'],1)
                fixtures[slip]['rule_fidelity'] = score
                print(f'\n{slip}: {score}',flush=True)
        (m.directory/'slip-grades.json').write_text(json.dumps({'fixtures':fixtures,'seconds':result.seconds},indent=2)+'\n')

    def test_wrong_item_and_constant_claims(self):
        for mutation in ('wrong_request','constant'):
            with self.subTest(mutation=mutation):
                m = material()
                score = casework.grade(m,casework.fixture(m,**{mutation:True}),self.runner)
                self.assertEqual(score['rule_fidelity']['rate'],1)
                self.assertLess(score['claim_fidelity']['rate'],1)
                if mutation == 'constant':
                    for truth, claim in zip(m.truth['claims'],score['claims']):
                        if len(set(truth['sealed']+truth['unseen']))>1:
                            self.assertFalse(claim['agrees_everywhere'],truth['id'])
                print(f'\n{mutation}: {score["claim_fidelity"]}',flush=True)

    def test_corrupt_truth_and_incomplete_candidate_fail_closed(self):
        m = material()
        m.truth['rule_expected'][0][0] += 1
        with self.assertRaisesRegex(FidelityError,'reference disagrees'):
            casework.validate(m,self.runner)
        m = material()
        candidate = m.directory/'Incomplete.lean'
        candidate.write_text('namespace Candidate\ndef model := 0\nend Candidate\n')
        with self.assertRaisesRegex(FidelityError,'Lean rejected'):
            casework.grade(m,candidate,self.runner)
