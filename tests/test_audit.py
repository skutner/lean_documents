"""Behavioral tests for input integrity and checked proof delivery."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from contract_fixture import annotate_synthetic_finding, register_synthetic_report
from lean_location import lean_path

MODULE = Path(__file__).resolve().parents[1] / 'skills/evidence-to-proof-review/scripts/audit.py'
spec = importlib.util.spec_from_file_location('audit', MODULE)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
LEAN = str(lean_path())
TEST_ROOT = MODULE.parents[3] / 'tmp'
TEST_ROOT.mkdir(exist_ok=True)
TEST_RUN = Path(tempfile.mkdtemp(prefix='audit-tests-', dir=TEST_ROOT))


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix=self._testMethodName+'-', dir=TEST_RUN))
        (self.base / 'brief.md').write_text('Audit the reported total.')
        (self.base / 'report.md').write_text('# Report\n\n<a id="R-01"></a>\nThe total is three.\n')
        (self.base / 'sources').mkdir()
        (self.base / 'sources/policy.md').write_text('# Ledger\n\n<a id="L-01"></a>\nThe amounts are one and two.\n')
        self.root = Path(audit.prepare(SimpleNamespace(brief=self.base / 'brief.md', report=self.base / 'report.md',
            sources=[self.base / 'sources'], temp_root=self.base))['workspace'])
        self.finding = dict(claim_id='C1', report_quote='The total is three.', report_anchor='R-01',
            question='Does one plus two equal three?',
            evidence=[dict(path='input/sources/policy.md', anchor='L-01', quote='The amounts are one and two.')],
            rules=['Add the two amounts once.'], assumptions=['Amounts are accurately transcribed.'],
            reasoning=['One plus two equals three.'], semantic_state='supported', argument_status='insufficient_support',
            formal_status='proved', proofs=[dict(module='Audit', theorem='Audit.total', relation='supports', target='(1 + 2 : Nat) = 3')],
            limits=['No source authentication.'], recommendation='Retain the reported total.')
        register_synthetic_report(self.root, [self.finding],
            non_claim_anchors={'document-preamble': 'The fixture title is a heading, not a substantive claim.'})
        self.save()
        (self.root / 'models/Audit.lean').write_text('import Std\nnamespace Audit\ntheorem total : (1 + 2 : Nat) = 3 := by decide\nend Audit\n')
        for name, content in {
            'review-plan.md': 'Inspect the one fixture claim and its anchored source; verify the arithmetic target.\n',
            'semantic-review.md': 'The one-plus-two source matches the encoded total of three; no real-world authenticity is asserted.\n',
            'model-notes.md': 'Natural-number addition represents the two exact fixture amounts without units or rounding.\n',
        }.items():
            (self.root / name).write_text(content)

    def save(self):
        annotate_synthetic_finding(self.finding)
        audit.write_json(self.root / 'findings.json', [self.finding])

    def check(self, lean=LEAN):
        return audit.check(SimpleNamespace(workspace=self.root, lean=lean, timeout=60))

    @unittest.skipUnless(Path(LEAN).is_file(), 'Lean executable unavailable')
    def test_failed_optional_replay_is_recorded_as_failed_and_blocks_delivery(self):
        checker = self.base / 'deliberately-rejecting-test-checker'
        checker.write_text('#!/bin/sh\necho "Intentional negative-control rejection"\nexit 7\n')
        checker.chmod(0o700)
        with self.assertRaisesRegex(audit.AuditError, 'failed'):
            audit.check(SimpleNamespace(workspace=self.root, lean=LEAN, timeout=60, checker=checker, checker_timeout=10))
        result = audit.read_json(self.root / 'proof-results.json')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['independent_checker']['status'], 'failed')
        self.assertIn('Intentional negative-control rejection', (self.root / result['independent_checker']['logs'][0]).read_text())
        with self.assertRaises(audit.AuditError):
            audit.render(SimpleNamespace(workspace=self.root))

    def test_preserves_explicit_anchors_and_file_names(self):
        data = audit.read_json(self.root / 'input/source-ledger.json')
        self.assertEqual(data['input/sources/policy.md'], {'document-preamble': '# Ledger', 'L-01': 'The amounts are one and two.'})
        self.assertEqual(audit.findings(self.root)[0]['report_anchor'], 'R-01')
        rows = [json.loads(line) for line in (self.root / 'source-ledger.jsonl').read_text().splitlines()]
        self.assertEqual(next(row for row in rows if row['path'] == 'input/sources/policy.md' and row['anchor'] == 'L-01')['start_line'], 4)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)

    def test_generated_paragraph_anchors(self):
        self.assertEqual(audit.paragraphs('Heading\n\nParagraph one.\n\nParagraph two.'),
                         {'P01': 'Heading', 'P02': 'Paragraph one.', 'P03': 'Paragraph two.'})

    def test_default_workspace_is_retained_under_local_tmp(self):
        with patch.object(audit.Path, 'cwd', return_value=self.base):
            prepared = audit.prepare(SimpleNamespace(brief=self.base / 'brief.md', report=self.base / 'report.md',
                sources=[self.base / 'sources'], temp_root=None))
        root = Path(prepared['workspace'])
        self.assertEqual(root.parent, self.base / 'tmp')
        self.assertTrue((root / 'input/report.md').is_file())

    def test_report_and_sources_prepare_without_optional_brief(self):
        for explicit_none in (True, False):
            args = SimpleNamespace(report=self.base / 'report.md', sources=[self.base / 'sources'],
                                   temp_root=self.base)
            if explicit_none:
                args.brief = None
            with self.subTest(explicit_none=explicit_none):
                root = Path(audit.prepare(args)['workspace'])
                self.assertFalse((root / 'input/brief.md').exists())
                self.assertEqual(audit.read_json(root / 'input/audit-policy.json')['contract_version'], 2)
                self.assertIn('input/audit-policy.json', audit.verify_manifest(root)['files'])

    def test_duplicate_explicit_anchor_rejected(self):
        with self.assertRaises(audit.AuditError):
            audit.paragraphs('<a id="A"></a>one\n<a id="A"></a>two')

    def test_collision_safe_sources(self):
        other = self.base / 'other'; other.mkdir()
        (other / 'policy.md').write_text('Different evidence.')
        prepared = audit.prepare(SimpleNamespace(brief=self.base / 'brief.md', report=self.base / 'report.md',
            sources=[self.base / 'sources', other], temp_root=self.base))
        paths = list((Path(prepared['workspace']) / 'input/sources').rglob('policy.md'))
        self.assertEqual(len(paths), 2)
        self.assertEqual(len({p.read_text() for p in paths}), 2)

    def test_stale_inputs_rejected(self):
        (self.root / 'input/sources/policy.md').write_text('Changed evidence.')
        with self.assertRaisesRegex(audit.AuditError, 'hash mismatch'):
            audit.findings(self.root)

    def test_missing_input_rejected(self):
        (self.root / 'input/report.md').unlink()
        with self.assertRaisesRegex(audit.AuditError, 'inventory changed'):
            audit.findings(self.root)

    def test_source_misquote_rejected(self):
        self.finding['evidence'][0]['quote'] = 'The amounts are two and two.'; self.save()
        with self.assertRaisesRegex(audit.AuditError, 'evidence quote'):
            audit.findings(self.root)

    def test_report_anchor_mismatch_rejected(self):
        self.finding['report_anchor'] = 'R-02'; self.save()
        with self.assertRaisesRegex(audit.AuditError, 'report quote'):
            audit.findings(self.root)

    def test_formal_label_without_proof_rejected(self):
        self.finding['proofs'] = []; self.save()
        with self.assertRaisesRegex(audit.AuditError, 'formal status requires'):
            audit.findings(self.root)

    def test_missing_theorem_fails_before_tool_execution(self):
        self.finding['proofs'][0]['theorem'] = 'Audit.absent'; self.save()
        with self.assertRaisesRegex(audit.AuditError, 'Missing declared theorem'):
            self.check('/missing/lean')
        self.assertEqual(audit.read_json(self.root / 'proof-results.json')['status'], 'failed')

    def test_previous_failure_is_archived(self):
        self.finding['proofs'][0]['theorem'] = 'Audit.absent'; self.save()
        for _ in range(2):
            with self.assertRaises(audit.AuditError):
                self.check('/missing/lean')
        previous = audit.read_json(self.root / 'attempts/0001/proof-results.json')
        self.assertEqual(previous['status'], 'failed')
        self.assertIn('Missing declared theorem', previous['error'])

    def test_untrusted_proof_constructs_rejected(self):
        for body in ('theorem total : True := by sorry', 'axiom fabricated : False',
                     'theorem total : True := by native_decide', 'unsafe def fake := 3'):
            with self.subTest(body=body):
                (self.root / 'models/Audit.lean').write_text('import Std\n' + body + '\n')
                with self.assertRaisesRegex(audit.AuditError, 'Forbidden'):
                    self.check('/missing/lean')

    def test_noncore_import_rejected(self):
        (self.root / 'models/Audit.lean').write_text('import Mathlib\ntheorem total : True := by trivial\n')
        with self.assertRaisesRegex(audit.AuditError, 'Only Init/Std'):
            self.check('/missing/lean')

    def test_target_injection_rejected(self):
        self.finding['proofs'][0]['target'] = 'True) := by trivial theorem bad : (True'; self.save()
        with self.assertRaisesRegex(audit.AuditError, 'unsupported command'):
            audit.findings(self.root)

    def test_missing_lean_records_failure(self):
        with self.assertRaisesRegex(audit.AuditError, 'Cannot execute'):
            self.check('/missing/lean')
        result = audit.read_json(self.root / 'proof-results.json')
        self.assertEqual(result['status'], 'failed')
        self.assertTrue((self.root / 'logs/lean-version.log').exists())

    def test_timeout_records_log(self):
        log = self.root / 'timeout.log'
        with self.assertRaisesRegex(audit.AuditError, 'timed out'):
            audit.execute([os.sys.executable, '-c', 'import time; time.sleep(1)'], self.root, log, 0.01, os.environ)
        self.assertIn('TIMEOUT', log.read_text())

    def test_render_refuses_unverified_proofs(self):
        with self.assertRaises(audit.AuditError):
            audit.render(SimpleNamespace(workspace=self.root))

    def test_documentary_audit_needs_no_lean(self):
        self.finding['proofs'] = []; self.finding['formal_status'] = 'not_attempted'; self.save()
        (self.root / 'models/Audit.lean').unlink()
        results = self.check('/missing/lean')
        self.assertEqual(results['status'], 'not_run')
        audit.render(SimpleNamespace(workspace=self.root))
        self.assertIn('Formal verification: not run', (self.root / 'audit-report.md').read_text())

    def test_brief_and_selected_report_cannot_supply_source_evidence(self):
        for evidence in (dict(path='input/brief.md', anchor='P01', quote='Audit the reported total.'),
                         dict(path='input/report.md', anchor='R-01', quote='The total is three.')):
            with self.subTest(path=evidence['path']):
                self.finding['evidence'] = [evidence]
                self.save()
                with self.assertRaises(audit.AuditError):
                    audit.findings(self.root)

    def test_formal_semantic_inconsistency_rejected(self):
        self.finding['semantic_state'] = 'undetermined'; self.save()
        with self.assertRaisesRegex(audit.AuditError, 'inconsistent'):
            audit.findings(self.root)

    def test_auxiliary_proof_permitted_for_undetermined(self):
        self.finding['semantic_state'] = 'undetermined'
        self.finding['formal_status'] = 'auxiliary'
        self.finding['proofs'][0]['relation'] = 'auxiliary'; self.save()
        self.assertEqual(audit.findings(self.root)[0]['formal_status'], 'auxiliary')

    def test_proofs_in_not_attempted_rejected(self):
        self.finding['formal_status'] = 'not_attempted'; self.save()
        with self.assertRaisesRegex(audit.AuditError, 'cannot contain'):
            audit.findings(self.root)

    def test_unreviewed_lakefile_rejected(self):
        (self.root / 'lakefile.lean').write_text('import Lake')
        with self.assertRaisesRegex(audit.AuditError, 'Unreviewed lakefile'):
            self.check('/missing/lean')

    @unittest.skipUnless(Path(LEAN).is_file(), 'Lean 4.24.0 not available')
    def test_export_requires_each_completed_review_note(self):
        self.check()
        for name in ('review-plan.md', 'semantic-review.md', 'model-notes.md'):
            path = self.root / name
            before = path.read_bytes()
            for replacement in (None, ' \n'):
                with self.subTest(name=name, replacement=replacement):
                    if replacement is None:
                        path.unlink()
                    else:
                        path.write_text(replacement)
                    with self.assertRaisesRegex(audit.AuditError, 'Export requires completed ' + name):
                        audit.export(SimpleNamespace(workspace=self.root, destination=self.base / 'incomplete-delivery'))
                    self.assertFalse((self.base / 'incomplete-delivery').exists())
                    path.write_bytes(before)

    def test_changed_claim_registry_or_review_notes_block_delivery(self):
        self.finding['proofs'] = []
        self.finding['formal_status'] = 'not_attempted'
        self.save()
        (self.root / 'models/Audit.lean').unlink()
        self.check('/missing/lean')
        for name in ('claims.json', 'review-plan.md', 'semantic-review.md', 'model-notes.md'):
            path = self.root / name
            before = path.read_bytes()
            with self.subTest(name=name):
                path.write_bytes(before + b'\n')
                with self.assertRaisesRegex(audit.AuditError, 'current findings'):
                    audit.render(SimpleNamespace(workspace=self.root))
                path.write_bytes(before)
        audit.render(SimpleNamespace(workspace=self.root))

    @unittest.skipUnless(Path(LEAN).is_file(), 'Lean 4.24.0 not available')
    def test_modified_proof_log_blocks_delivery(self):
        results = self.check()
        (self.root / results['targets'][0]['log']).write_text('Fabricated replacement log.')
        with self.assertRaisesRegex(audit.AuditError, 'artifacts changed'):
            audit.render(SimpleNamespace(workspace=self.root))

    @unittest.skipUnless(Path(LEAN).is_file(), 'Lean 4.24.0 not available')
    def test_real_lean_check_render_export_and_stale_proof(self):
        results = self.check()
        self.assertEqual(results['status'], 'passed')
        self.assertEqual(len(results['targets']), 2)
        audit.render(SimpleNamespace(workspace=self.root))
        report = (self.root / 'audit-report.md').read_text()
        self.assertIn(self.finding['reasoning'][0], report)
        self.assertIn('Actual proof log', report)
        destination = self.base / 'delivered'
        audit.export(SimpleNamespace(workspace=self.root, destination=destination))
        self.assertFalse((destination / '.lake').exists())
        self.assertTrue((destination / 'export-manifest.json').exists())
        (self.root / 'models/Audit.lean').write_text('import Std\n')
        with self.assertRaisesRegex(audit.AuditError, 'current findings'):
            audit.render(SimpleNamespace(workspace=self.root))

    @unittest.skipUnless(Path(LEAN).is_file(), 'Lean 4.24.0 not available')
    def test_real_lean_rejects_wrong_proposition(self):
        self.finding['proofs'][0]['target'] = '(1 + 2 : Nat) = 4'; self.save()
        with self.assertRaisesRegex(audit.AuditError, 'Lean command failed'):
            self.check()
        self.assertEqual(audit.read_json(self.root / 'proof-results.json')['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
