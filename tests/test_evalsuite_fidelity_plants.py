"""Slip-free casework and procurement engines and their named single edits. No model is run."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from evalsuite import fidelity_mutants as mutants
from evalsuite import fidelity_plants as plants
from evalsuite.fidelity import FidelityError, run_checked, trusted_lean_runner

ROOT = Path(__file__).resolve().parents[1]
(ROOT / 'tmp').mkdir(exist_ok=True)
RUN = Path(tempfile.mkdtemp(prefix='plants-', dir=ROOT / 'tmp'))
LEAN = Path(os.environ.get('AUDIT_TEST_LEAN', '/unavailable/lean'))
READY = bool(os.environ.get('AUDIT_TEST_LEAN')) and LEAN.is_file()
ALL_NAMES = {name for family in mutants.FAMILIES for name in mutants.family_module(family).VARIANTS}


class PlantTextTests(unittest.TestCase):
    def test_every_generator_slip_has_exactly_one_named_edit(self):
        for family in mutants.HARD:
            module = mutants.family_module(family)
            self.assertEqual(set(plants.PATCHES[family]), set(module.VARIANTS), family)
            self.assertEqual(len(module.VARIANTS), len(set(module.VARIANTS)))

    def test_each_edit_changes_the_engine_and_matches_its_target_once(self):
        for family in mutants.HARD:
            neutral = plants.plant(family)
            self.assertEqual(neutral, plants.neutral(family))
            seen = set()
            for slip in mutants.family_module(family).VARIANTS:
                with self.subTest(family=family, slip=slip):
                    text = plants.plant(family, slip)
                    self.assertNotEqual(text, neutral)
                    self.assertNotIn(text, seen, 'two slips produced the same file')
                    seen.add(text)

    def test_no_file_names_a_slip_a_switch_or_an_evaluator_label(self):
        for family in mutants.HARD:
            for slip in (None, *mutants.family_module(family).VARIANTS):
                with self.subTest(family=family, slip=slip):
                    folder = RUN / f'blind-{family}-{slip}'
                    folder.mkdir()
                    text = mutants.clean(plants.plant(family, slip))
                    (folder / 'Formalization.lean').write_text(text)
                    self.assertTrue(mutants.blindness_scan(folder))
                    self.assertFalse(ALL_NAMES & set(re.findall(r'\w+', text)))
                    self.assertNotRegex(text, r'\b(slip|flag)\b')

    def test_an_edit_that_misses_or_repeats_its_target_fails_closed(self):
        for text, target in (('x\n', 'not in the text\n'), ('a\na\n', 'a\n')):
            with mock.patch.object(plants, 'PATCHES', {'casework': {'probe': [(target, 'b\n')]}}), \
                    mock.patch.object(plants, 'neutral', return_value=text):
                with self.assertRaisesRegex(FidelityError, 'not once'):
                    plants.plant('casework', 'probe')
        with self.assertRaises(FidelityError):
            plants.plant('casework', 'no_such_slip')
        with self.assertRaises(FidelityError):
            plants.plant('approvals')


@unittest.skipUnless(READY, 'Lean requires AUDIT_TEST_LEAN naming a binary; Mac has no Lean')
class PlantLeanTests(unittest.TestCase):
    def test_every_engine_compiles_without_warnings(self):
        """Interface plus one engine: catches unused binders and syntax slips in each edit, in seconds."""
        runner = trusted_lean_runner(LEAN, timeout=600)
        jobs = [(family, slip) for family in mutants.HARD
                for slip in (None, *mutants.family_module(family).VARIANTS)]

        def compile_one(job):
            family, slip = job
            folder = RUN / f'compile-{family}-{slip}'
            folder.mkdir()
            module = mutants.family_module(family)
            path = folder / 'Engine.lean'
            path.write_text((module.LEAN_DIR / 'Interface.lean').read_text() + '\n' + plants.plant(family, slip))
            return run_checked(path, runner)
        with ThreadPoolExecutor(max_workers=min(6, os.cpu_count() or 1)) as pool:
            for job, result in zip(jobs, pool.map(compile_one, jobs)):
                with self.subTest(job=job):
                    self.assertEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
