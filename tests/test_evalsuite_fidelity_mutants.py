"""Step 8 generated fixtures; no model execution, retained evidence under tmp/."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from evalsuite import fidelity_mutants as mutants
from evalsuite.fidelity import FidelityError, trusted_lean_runner

ROOT = Path(__file__).resolve().parents[1]
(ROOT / 'tmp').mkdir(exist_ok=True)
RUN = Path(tempfile.mkdtemp(prefix='step8-tests-', dir=ROOT / 'tmp'))
LEAN = Path(os.environ.get('AUDIT_TEST_LEAN', '/unavailable/lean'))
READY = bool(os.environ.get('AUDIT_TEST_LEAN')) and LEAN.is_file()


def tree(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob('*') if p.is_file()}


class MutantMaterialTests(unittest.TestCase):
    def test_deterministic_blind_cases_all_families_both_reports(self):
        for family in mutants.FAMILIES:
            for variant in mutants.REPORTS:
                with self.subTest(family=family, report=variant):
                    left, right = [RUN / f'determinism-{family}-{variant}-{i}' for i in range(2)]
                    a = mutants.generate_dossier(family, 511, 1, variant, left)
                    b = mutants.generate_dossier(family, 511, 1, variant, right)
                    self.assertEqual(a, b)
                    self.assertEqual(tree(left), tree(right))
                    self.assertEqual(Counter(r['error_class'] for r in a['cases'])['faithful'], 1)
                    self.assertEqual({r['mutation'] for r in a['cases'] if r['error_class'] == 'rule'},
                                     set(mutants.family_module(family).VARIANTS))
                    for row in a['cases']:
                        self.assertFalse(row['validated'])
                        self.assertIsNone(row['effective'])
                        self.assertEqual(row['visibility'], 'pending')
                        folder = left / row['path']
                        self.assertTrue(mutants.blindness_scan(folder))
                        source = (folder / 'Formalization.lean').read_text()
                        self.assertNotIn('correct/', source)
                        self.assertNotIn('mixed/', source)
                        self.assertNotIn('expectedWorlds', source)

    def test_each_claim_plant_changes_one_dispatch_arm_and_fact_one_cell(self):
        for family in mutants.FAMILIES:
            root = RUN / f'one-error-{family}'
            key = mutants.generate_dossier(family, 512, 2, 'mixed', root)
            baseline = next(r for r in key['cases'] if r['error_class'] == 'faithful')
            original = (root / baseline['path'] / 'Formalization.lean').read_text().splitlines()
            self.assertEqual({r['error_class'] for r in key['cases']}, {'faithful', 'rule', 'fact', 'claim'}, family)
            self.assertEqual({r['mutation'] for r in key['cases'] if r['error_class'] == 'claim'},
                             {'constant', 'wrong_item', 'weaker'}, family)
            for row in key['cases']:
                if row['error_class'] not in ('claim', 'fact'):
                    continue
                current = (root / row['path'] / 'Formalization.lean').read_text().splitlines()
                if row['error_class'] == 'claim':
                    # An arm can span several lines; everything outside that one arm is unchanged.
                    ident = row['location']['id']
                    outside = lambda lines: lines[:mutants._arm_blocks(lines)[ident][0]] + lines[mutants._arm_blocks(lines)[ident][1]:]
                    self.assertEqual(outside(original), outside(current), row)
                    self.assertNotEqual(original, current)
                    continue
                self.assertEqual(len(original), len(current))
                differences = [(a, b) for a, b in zip(original, current) if a != b]
                self.assertEqual(len(differences), 1, row)
                self.assertTrue(differences[0][0].startswith('def recorded '))
                self.assertEqual(row['change']['after'] - row['change']['before'], 1)

    def test_harder_family_files_are_slip_free_engines_with_one_named_edit(self):
        for family in mutants.HARD:
            root = RUN / f'engine-{family}'
            key = mutants.generate_dossier(family, 511, 1, 'correct', root)
            module = mutants.family_module(family)
            texts = {r['mutation']: (root / r['path'] / 'Formalization.lean').read_text()
                     for r in key['cases'] if r['error_class'] == 'rule'}
            self.assertEqual(set(texts), set(module.VARIANTS))
            baseline = next(r for r in key['cases'] if r['error_class'] == 'faithful')
            faithful = (root / baseline['path'] / 'Formalization.lean').read_text()
            for name, text in texts.items():
                self.assertNotIn(name, text)
                self.assertNotRegex(text, r'\b(slip|flag)\b')
                self.assertNotEqual(text, faithful, name)
            self.assertNotRegex(faithful, r'\b(slip|flag)\b')

    def test_blindness_scan_detects_content_names_and_symlinks(self):
        for i, name in enumerate(('note.md', 'Faithful.lean')):
            folder = RUN / f'leak-{i}'
            folder.mkdir()
            (folder / name).write_text('distinctDropped' if i == 0 else 'import Std')
            with self.assertRaisesRegex(FidelityError, 'Blindness'):
                mutants.blindness_scan(folder)
        folder = RUN / 'symlink'
        folder.mkdir()
        (folder / 'note').symlink_to(RUN / 'leak-0/note.md')
        with self.assertRaisesRegex(FidelityError, 'Blindness'):
            mutants.blindness_scan(folder)

    def test_blindness_scan_refuses_capitalised_labels_but_allows_lowercase_prose(self):
        for i, (name, text) in enumerate([('note.md', 'See Reference.lean'), ('Reference.md', 'plain'),
                                          ('Fixture.lean', 'plain'), ('note2.md', 'a Fixture for tests')]):
            folder = RUN / f'capital-{i}'
            folder.mkdir()
            (folder / name).write_text(text)
            with self.assertRaisesRegex(FidelityError, 'Blindness'):
                mutants.blindness_scan(folder)
        folder = RUN / 'lowercase-prose'
        folder.mkdir()
        (folder / 'document-index.md').write_text('A notice contributes no class, date, channel or reference.')
        self.assertTrue(mutants.blindness_scan(folder))

    def test_every_harder_family_report_of_several_seeds_prepares_and_passes_the_scan(self):
        for family in mutants.HARD:
            for seed, index in ((511, 2), (512, 1), (513, 3)):
                for variant in mutants.REPORTS:
                    with self.subTest(family=family, seed=seed, index=index, report=variant):
                        key = mutants.generate_dossier(family, seed, index, variant,
                                                       RUN / f'prepared-{family}-{seed}-{index}-{variant}')
                        self.assertEqual({r['error_class'] for r in key['cases']}, {'faithful', 'rule', 'fact', 'claim'})
                        self.assertEqual(len([r for r in key['cases'] if r['error_class'] == 'rule']),
                                         len(mutants.family_module(family).VARIANTS))

    def test_single_supported_claim_constant_has_an_explicitly_unused_world(self):
        for variant in mutants.REPORTS:
            root = RUN / f'singleton-{variant}'
            key = mutants.generate_dossier('packet', 511, 4, variant, root)
            row = next(r for r in key['cases'] if r['mutation'] == 'constant')
            self.assertEqual(len(row['claims']), 1)
            source = (root / row['path'] / 'Formalization.lean').read_text()
            body = source.split('def claims', 1)[1].split('def model', 1)[0]
            self.assertIn('(_w : World)', body)
            self.assertNotIn('w.', body)
            self.assertNotIn('linter.unusedVariables', source)

    def test_invalid_family_variant_path_and_reuse_fail_closed(self):
        for family, variant in [('no-family', 'correct'), ('approvals', 'bad')]:
            with self.assertRaises(FidelityError):
                mutants.generate_dossier(family, 1, 1, variant, RUN / f'bad-{family}-{variant}')
        with self.assertRaisesRegex(FidelityError, 'under repository tmp'):
            mutants.generate_dossier('approvals', 1, 1, 'correct', ROOT / 'not-permitted-output')
        with self.assertRaisesRegex(FidelityError, 'distinct'):
            mutants.generate_dossier('approvals', 1, 1, 'correct', RUN)


@unittest.skipUnless(READY, 'Lean requires AUDIT_TEST_LEAN naming a binary; Mac has no Lean')
class MutantLeanTests(unittest.TestCase):
    def test_all_families_two_dossiers_and_singleton_regression_execute_grader(self):
        # The harder families need minutes per dossier under load, above the 180 s default.
        runner = trusted_lean_runner(LEAN, timeout=1500)
        keys = []
        # Two dossiers per family, plus the singleton-report warning regression.
        # The larger results table can reuse unchanged, hash-matched executions.
        with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as pool:
            jobs = [(family, index, variant, RUN / f'lean-{family}-{index}-{variant}')
                    for family in mutants.FAMILIES for index in (1, 2)
                    for variant in mutants.REPORTS]
            jobs += [('packet', 4, variant, RUN / f'lean-packet-4-{variant}') for variant in mutants.REPORTS]
            futures = [(family, index, variant, root, pool.submit(
                mutants.generate_dossier, family, 511, index, variant, root, runner))
                for family, index, variant, root in jobs]
            for family, index, variant, root, future in futures:
                    with self.subTest(family=family, index=index, report=variant):
                        key = future.result()
                        keys.append(key)
                        faithful = [r for r in key['cases'] if r['error_class'] == 'faithful']
                        self.assertEqual(len(faithful), 1)
                        self.assertEqual(faithful[0]['rule_rate'], 1)
                        self.assertEqual(faithful[0]['supported_claim_rate'], 1)
                        self.assertTrue(faithful[0]['own_rule_agrees'])
                        for row in key['cases']:
                            self.assertTrue(row['validated'])
                            self.assertTrue(mutants.blindness_scan(root / row['path']))
                            if family in mutants.HARD:
                                # Rule plants reproduce the generator's slip output; all other files its rule.
                                self.assertIs(row['rule_equals_generator'], True, row)
                            if row['error_class'] == 'faithful':
                                self.assertEqual(row['fact_rate'], 1)
                            else:
                                self.assertTrue(row['effective'])
                                self.assertTrue(row['rule_rate'] < 1 or row['unseen_claim_rate'] < 1 or row['fact_rate'] < 1)
                                # Only a fact plant changes the recorded cells, and every one of them counts.
                                self.assertEqual(row['fact_rate'] < 1, row['error_class'] == 'fact', row)
                                self.assertEqual(row['visibility'] == 'silent',
                                                 row['own_rule_agrees'] and row['own_claim_tests_pass'])
                            if row['visibility'] in ('faithful', 'silent'):
                                self.assertTrue(row['own_claim_tests_pass'])
                                execution = json.loads((root / row['evidence'] / 'Formalization.execution.json').read_text())
                                self.assertEqual(execution['returncode'], 0)
                                self.assertEqual(execution['source_sha256'], row['formalization_sha256'])
                        attempts = key['cases'] + key['excluded']
                        self.assertEqual({r['mutation'] for r in attempts if r['error_class'] == 'rule'},
                                         set(mutants.family_module(family).VARIANTS))
                        fact = next(r for r in key['cases'] if r['error_class'] == 'fact')
                        self.assertTrue(fact['effective'])
                        self.assertEqual(fact['fact_cells_wrong'], 1)
                        self.assertEqual(fact['fact_first_mismatch'] is not None, True)
                        # The unseen-input graders still ignore submitted facts; fact accuracy is what drops.
                        self.assertEqual(fact['rule_rate'], 1)
                        self.assertEqual(fact['unseen_claim_rate'], 1)
                        print(f'EXECUTED {family}/{index}/{variant}: {len(key["cases"])} retained; '
                              f'{len(key["excluded"])} excluded', flush=True)
        # The table covers seed 511, indices 1 and 2; the packet-4 jobs are only the warning regression.
        table = mutants.summarize([k for k in keys if k['cases'][0]['index'] in (1, 2)])
        (RUN / 'summary.json').write_text(json.dumps(table, indent=2) + '\n')
        aggregate = {'schema_version': 1, 'complete': True, 'cases': [], 'excluded': [], 'limits': mutants.LIMITS}
        for key in keys:
            for category in ('cases', 'excluded'):
                for original in key[category]:
                    row = dict(original)
                    prefix = f'lean-{row["family"]}-{row["index"]}-{row["report_variant"]}'
                    row['path'] = prefix + '/' + row['path']
                    row['evidence'] = prefix + '/' + row['evidence']
                    aggregate[category].append(row)
        (RUN / 'key.json').write_text(json.dumps(aggregate, indent=2) + '\n')
        print(json.dumps(table, indent=2), flush=True)
        print(f'Step 8 evidence: {RUN}', flush=True)


if __name__ == '__main__':
    unittest.main()
