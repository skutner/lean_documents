"""Core regeneration, strict result parsing, and execution-boundary tests."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from evalsuite import generate
from evalsuite.fidelity import (FidelityError, LeanResult, agreement, parse_vectors,
                               regenerate, run_checked, verdict)

ROOT = Path(__file__).resolve().parents[1]


class FidelityCoreTests(unittest.TestCase):
    def test_regenerates_both_reports_and_records_generator_identity(self):
        key = generate.build_case('approvals', 511, 3)['key']
        reg = regenerate(key)
        self.assertEqual(len(reg.claims), len(key['expectations']))
        self.assertEqual(reg.provenance['version'], generate.GENERATOR_VERSION)
        self.assertEqual(len(reg.provenance['source_sha256']), 64)
        for row, item in zip(key['expectations'], reg.claims):
            self.assertEqual(generate.judge(item, reg.case['base'], reg.case['unknowns'])[0], row['semantic_state'])

    def test_regenerates_every_family_with_the_selected_builder_module(self):
        for family, module in (('ledger', 'generate.py'), ('approvals', 'generate.py'),
                               ('casework', 'hard_casework.py'), ('procurement', 'hard_procurement.py')):
            with self.subTest(family=family):
                key = generate.build_case(family, 511, 2)['key']
                reg = regenerate(key)
                self.assertEqual(len(reg.claims), len(key['expectations']))
                self.assertEqual(reg.provenance['source_file'], 'evalsuite/' + module)
                source = Path(generate.__file__).with_name(module)
                self.assertEqual(reg.provenance['source_sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
                self.assertEqual('generate_sha256' in reg.provenance, module != 'generate.py')
                for row, item in zip(key['expectations'], reg.claims):
                    self.assertEqual(generate.judge(item, reg.case['base'], reg.case['unknowns'])[0],
                                     row['semantic_state'])

    def test_rejects_version_and_key_drift(self):
        for mutate in (lambda k: k['generator'].update(version=-1),
                       lambda k: k['expectations'][0].update(semantic_state='refuted'),
                       lambda k: k['expectations'].pop()):
            key = generate.build_case('approvals', 511, 3)['key']
            mutate(key)
            with self.assertRaises(FidelityError):
                regenerate(key)

    def test_verdict_and_agreement_are_nonvacuous(self):
        self.assertEqual([verdict(v) for v in ([True], [False], [True, False])],
                         ['supported', 'refuted', 'undetermined'])
        self.assertEqual(agreement([True, False], [True, True])['rate'], .5)
        for values in ([], [1], [None]):
            with self.assertRaises(FidelityError):
                verdict(values)
        for actual, expected in (([], []), ([True], []), ([1], [True])):
            with self.assertRaises(FidelityError):
                agreement(actual, expected)

    def test_output_protocol_rejects_missing_duplicate_malformed_and_short_vectors(self):
        self.assertEqual(parse_vectors('"FIDELITY|x|01"\n', {'x': 2}), {'x': [False, True]})
        for output in ('', '"FIDELITY|x|0"', '"FIDELITY|x|02"', '"FIDELITY|other|01"',
                       '"FIDELITY|x|01"\n"FIDELITY|x|01"', '"FIDELITY|x|01'):
            with self.assertRaises(FidelityError):
                parse_vectors(output, {'x': 2})

    def test_runner_is_injected_and_logs_failed_execution(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix='fidelity-core-', dir=ROOT / 'tmp'))
        path = root / 'Generated.lean'
        path.write_text('#eval true\n')
        seen = []
        def runner(p):
            seen.append(p)
            return LeanResult(1, 'synthetic failure', 'stderr', .01)
        with self.assertRaises(FidelityError):
            run_checked(path, runner)
        self.assertEqual(seen, [path])
        self.assertEqual(path.with_suffix('.output.txt').read_text(), 'synthetic failurestderr')
        self.assertEqual(json.loads(path.with_suffix('.execution.json').read_text())['returncode'], 1)
        with self.assertRaises(FidelityError):
            run_checked(path, lambda p: LeanResult(0, 'warning: declaration uses sorry'))
