"""Real Lean checks and failure retention for the Markdown-only proof runner."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/evidence-to-proof-review/scripts'))
import markdown_proofs as runner
from lean_location import lean_path

LEAN = str(lean_path())


class MarkdownProofTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / 'tmp/markdown-proof-tests'
        base.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='run-', dir=base))
        (self.root / 'models').mkdir()
        (self.root / 'models/Main.lean').write_text('theorem total : 3 + 2 + 4 = 9 := by decide\n')
        self.proof = dict(module='Main', theorem='total', target='3 + 2 + 4 = 9', relation='supports', claim_id='R1')

    def assert_no_manifests(self):
        self.assertEqual(list(self.root.rglob('*.json')), [])
        self.assertEqual(list(self.root.rglob('*.jsonl')), [])
        self.assertFalse((self.root / '.lake').exists())

    @unittest.skipUnless(Path(LEAN).is_file(), 'Lean binary is not available')
    def test_real_local_dependencies_exact_target_and_retained_repeat(self):
        (self.root / 'models/Helpers.lean').write_text('def amount : Nat := 9\ntheorem amount_value : amount = 9 := rfl\n')
        (self.root / 'models/Main.lean').write_text('import Helpers\ntheorem total : amount ≤ 10 := by decide\n')
        self.proof['target'] = 'amount ≤ 10'
        with patch.dict(os.environ, {'LEAN_PATH': '/unreviewed/path'}):
            results = runner.check_models(self.root, LEAN, [self.proof])
        self.assertEqual(len(results), 3)
        self.assertEqual(results[-1]['claim_id'], 'R1')
        self.assertIsNone(results[0]['target'])
        for result in results:
            self.assertEqual(result['status'], 'passed')
            self.assertTrue((self.root / result['check_file']).exists())
            log = (self.root / result['log']).read_text()
            self.assertIn('Exit code: 0', log)
            self.assertNotIn('/unreviewed/path', log)
        runner.check_models(self.root, LEAN, [self.proof])
        self.assertEqual(len(list((self.root / 'proof-attempts').iterdir())), 2)
        self.assert_no_manifests()

    @unittest.skipUnless(Path(LEAN).is_file(), 'Lean binary is not available')
    def test_wrong_target_preserves_actual_compiler_failure(self):
        self.proof['target'] = '3 + 2 + 4 = 10'
        with self.assertRaisesRegex(ValueError, 'exited with code'):
            runner.check_models(self.root, LEAN, [self.proof])
        logs = list(self.root.rglob('target-002.md'))
        self.assertEqual(len(logs), 1)
        self.assertIn('Exit code: 1', logs[0].read_text())
        self.assertEqual(len(list(self.root.rglob('failure.md'))), 1)
        self.assert_no_manifests()

    def test_filters_and_cycles_fail_before_execution(self):
        cases = [('axiom total : 3 + 2 + 4 = 9\n', 'Forbidden'),
                 ('import Main\ntheorem total : 3 + 2 + 4 = 9 := by decide\n', 'cycle')]
        for source, message in cases:
            with self.subTest(message=message):
                (self.root / 'models/Main.lean').write_text(source)
                with patch.object(runner.subprocess, 'run') as execute:
                    with self.assertRaisesRegex(ValueError, message):
                        runner.check_models(self.root, LEAN, [self.proof])
                    execute.assert_not_called()
        self.assert_no_manifests()

    def test_target_injection_rejected_before_execution(self):
        self.proof['target'] = 'True) := by trivial\n#eval 1\nexample : (True'
        with patch.object(runner.subprocess, 'run') as execute:
            with self.assertRaisesRegex(ValueError, 'Target must'):
                runner.check_models(self.root, LEAN, [self.proof])
            execute.assert_not_called()
        self.assert_no_manifests()

    def test_missing_marker_bad_axiom_version_and_timeout_fail_closed(self):
        for case in ('marker', 'axiom', 'version', 'timeout'):
            with self.subTest(case=case):
                def fake(command, **kwargs):
                    if '--version' in command:
                        if case == 'timeout':
                            raise subprocess.TimeoutExpired(command, 1, output=b'partial output')
                        version = '4.25.0' if case == 'version' else '4.24.0'
                        return subprocess.CompletedProcess(command, 0, f'Lean (version {version}, test)', '')
                    if '-o' in command:
                        Path(command[command.index('-o') + 1]).write_bytes(b'test-double')
                        return subprocess.CompletedProcess(command, 0, '', '')
                    output = "'total' depends on axioms: [untrustedAxiom]" if case == 'axiom' else ''
                    return subprocess.CompletedProcess(command, 0, output, '')
                expected = {'marker': 'No axiom', 'axiom': 'Unapproved axioms', 'version': 'required', 'timeout': 'timed out'}[case]
                with patch.object(runner.subprocess, 'run', side_effect=fake):
                    with self.assertRaisesRegex(ValueError, expected):
                        runner.check_models(self.root, LEAN, [self.proof])
        self.assertEqual(len(list(self.root.rglob('failure.md'))), 4)
        self.assertIn('partial output', '\n'.join(p.read_text() for p in self.root.rglob('version.md')))
        self.assert_no_manifests()


if __name__ == '__main__':
    unittest.main()
