"""Executed Markdown-only audit workflow, including real Lean and claim traces."""
import importlib.util
import json
from pathlib import Path
import tempfile
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('markdown_audit_tested', ROOT / 'skills/evidence-to-proof-review/scripts/markdown_audit.py')
md = importlib.util.module_from_spec(spec)
spec.loader.exec_module(md)
from lean_location import lean_path
LEAN = lean_path()


class MarkdownAuditTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='markdown-audit-test-', dir=ROOT / 'tmp'))
        (self.base / 'report.md').write_text('<a id="R1"></a>\nThe total is three.\n')
        (self.base / 'source.md').write_text('<a id="S1"></a>\nThe quantities are one and two.\n')
        self.root = md.prepare(SimpleNamespace(report=self.base / 'report.md', sources=[self.base / 'source.md'], temp_root=self.base / 'runs', lean_version='4.24.0'))
        (self.root / 'claims.md').write_text('# Claims\n\n' + md.table(['Claim ID', 'Report anchor', 'Disposition', 'Reason'], [('C1', 'R1', 'audited', 'Atomic arithmetic claim.')]))
        sections = {heading: 'Explicit documentary analysis with bounded source coverage.' for heading in md.HEADINGS}
        sections.update({'Exact report claim': '> The total is three.', 'Report anchor': 'R1',
            'Executive conclusion': 'The report total matches the supplied quantities.',
            'How verified': 'Compared the two quantities and checked their sum.', 'Executive limits': 'Authenticity was not checked.',
            'Evidence': '### Evidence 1\n\n- Source: input/sources/1/source.md\n- Anchor: S1\n\n> The quantities are one and two.',
            'Proofs': 'None; documentary arithmetic check only.', 'Semantic state': 'supported',
            'Argument status': 'sufficient', 'Formal status': 'not_attempted'})
        self.finding_sections = sections
        self.write_finding()

    def write_finding(self):
        (self.root / 'findings/C1.md').write_text('# C1\n\n' + ''.join('## ' + heading + '\n\n' + value + '\n\n' for heading, value in self.finding_sections.items()))

    def traces(self):
        for step in md.STEPS:
            path = self.root / 'claim-work/C1' / (step + '.md')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# Individual ' + step + '\n\nActually reviewed the supplied quantities one and two; their sum is three. Source authenticity remains outside scope.\n')
            md.trace(SimpleNamespace(workspace=self.root, claim_id='C1', step=step, status='completed', title='Individual ' + step,
                description='Reviewed this claim and retained its intermediate result.', input=[getattr(self, 'trace_source', 'input/sources/1/source.md')], output=[path],
                command='documentary arithmetic inspection', result='The recorded individual analysis is complete within its stated source limits.'))

    def test_documentary_end_to_end_and_stdout_transport(self):
        self.traces()
        md.check(SimpleNamespace(workspace=self.root, lean='unused', timeout=30))
        md.render(SimpleNamespace(workspace=self.root))
        result = json.loads(md.inspect(SimpleNamespace(workspace=self.root, machine=True)))
        self.assertTrue(result['trace_complete'])
        self.assertTrue(result['report_fresh'])
        self.assertEqual(result['proof_results']['integrity'], 'current')
        self.assertEqual(result['findings'][0]['executive_summary']['conclusion'], self.finding_sections['Executive conclusion'])
        self.assertEqual(len(result['findings'][0]['trace_events']), 5)
        self.assertFalse(list(self.root.rglob('*.json')) + list(self.root.rglob('*.jsonl')))
        self.assertTrue((self.root / 'audit-report.md').is_file())

    def test_shared_or_missing_individual_history_fails(self):
        with self.assertRaisesRegex(ValueError, 'missing individual registration'):
            md.validate(self.root)

    def test_modified_finding_requires_recheck(self):
        self.traces()
        md.check(SimpleNamespace(workspace=self.root, lean='unused', timeout=30))
        self.finding_sections['Executive limits'] = 'Updated material limit.'
        self.write_finding()
        with self.assertRaisesRegex(ValueError, 'changed after validation'):
            md.render(SimpleNamespace(workspace=self.root))

    def test_json_files_and_wrong_quotes_are_rejected(self):
        (self.root / 'forbidden.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'JSON/JSONL'):
            md.validate(self.root)
        (self.root / 'forbidden.json').unlink()
        self.finding_sections['Exact report claim'] = '> A fabricated quote.'
        self.write_finding()
        with self.assertRaisesRegex(ValueError, 'exact quote'):
            md.validate(self.root)

    def test_bound_report_survives_renderer_change_but_not_tampering(self):
        self.traces()
        md.check(SimpleNamespace(workspace=self.root, lean='unused', timeout=30))
        md.render(SimpleNamespace(workspace=self.root))
        self.assertTrue((self.root / 'report-integrity.md').is_file())
        with patch.object(md, 'report_content', return_value='# A future renderer style\n'):
            result = json.loads(md.inspect(SimpleNamespace(workspace=self.root, machine=True)))
            self.assertTrue(result['report_fresh'])
        report = self.root / 'audit-report.md'
        report.write_text(report.read_text() + '\nUnauthorized report change.\n')
        result = json.loads(md.inspect(SimpleNamespace(workspace=self.root, machine=True)))
        self.assertFalse(result['report_fresh'])
        self.assertEqual(result['proof_results']['integrity'], 'current')
        md.render(SimpleNamespace(workspace=self.root))
        self.finding_sections['Executive limits'] = 'A material audit scope limit changed.'
        self.write_finding()
        result = json.loads(md.inspect(SimpleNamespace(workspace=self.root, machine=True)))
        self.assertFalse(result['report_fresh'])
        self.assertEqual(result['proof_results']['integrity'], 'stale_or_failed')

    def test_failed_latest_check_cannot_reuse_prior_success(self):
        self.traces()
        md.check(SimpleNamespace(workspace=self.root, lean='unused', timeout=30))
        with patch.object(md, 'validate', side_effect=ValueError('Actual new validation failed')):
            with self.assertRaisesRegex(ValueError, 'Actual new validation failed'):
                md.check(SimpleNamespace(workspace=self.root, lean='unused', timeout=30))
        self.assertTrue(list((self.root / 'validation-history').glob('*.md')))
        with self.assertRaisesRegex(ValueError, 'Latest validation did not pass'):
            md.render(SimpleNamespace(workspace=self.root))
        result = json.loads(md.inspect(SimpleNamespace(workspace=self.root, machine=True)))
        self.assertEqual(result['proof_results']['status'], 'failed')
        self.assertFalse(result['report_fresh'])

    def test_unfounded_formal_or_source_less_verdict_rejected(self):
        self.traces()
        self.finding_sections['Evidence'] = 'No source evidence inspected.'
        self.write_finding()
        with self.assertRaisesRegex(ValueError, 'requires independent evidence'):
            md.validate(self.root)

    def test_executive_summaries_reject_links_paths_and_raw_metadata(self):
        for text in ('Checked [proof](proof-attempts/target.md).',
                     'See claim-work/C1/verification.md for details.',
                     'Checks passed; axioms: [] are recorded.',
                     '{"status": "passed"}', 'Verified.'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                md.validate_summary('C1', 'How verified', text)
        md.validate_summary('C1', 'How verified', 'Compared the values in [0, 1] and checked that 300 + 200 + 400 = 900 cents.')

    def test_native_docx_report_and_source_end_to_end_without_json(self):
        import test_extractors as fixtures
        report = self.base / 'native-report.docx'
        source = self.base / 'source.docx'
        fixtures.write_docx(report, '<w:p><w:r><w:t>The total is three.</w:t></w:r></w:p>')
        fixtures.write_docx(source, '<w:p><w:r><w:t>The quantities are one and two.</w:t></w:r></w:p>')
        self.root = md.prepare(SimpleNamespace(report=report, sources=[source], temp_root=self.base / 'native-runs', lean_version='4.24.0'))
        report_anchor = next(iter(md.audit.paragraphs((self.root / 'input/report.md').read_text())))
        self.trace_source = 'input/sources/1/source.docx.extracted.md'
        source_anchor = next(iter(md.audit.paragraphs((self.root / self.trace_source).read_text())))
        (self.root / 'claims.md').write_text('# Claims\n\n' + md.table(['Claim ID', 'Report anchor', 'Disposition', 'Reason'], [('C1', report_anchor, 'audited', 'Atomic arithmetic claim.')]))
        self.finding_sections['Report anchor'] = report_anchor
        self.finding_sections['Evidence'] = '### Evidence 1\n\n- Source: ' + self.trace_source + '\n- Anchor: ' + source_anchor + '\n\n> The quantities are one and two.'
        self.write_finding(); self.traces()
        md.check(SimpleNamespace(workspace=self.root, lean='unused', timeout=30)); md.render(SimpleNamespace(workspace=self.root))
        self.assertEqual((self.root / 'input/originals/report.docx').read_bytes(), report.read_bytes())
        self.assertEqual((self.root / 'input/sources/1/source.docx').read_bytes(), source.read_bytes())
        self.assertTrue((self.root / 'input/extraction-index.md').is_file())
        self.assertTrue((self.root / 'input/extraction-segments.md').is_file())
        self.assertFalse(list(self.root.rglob('*.json')) + list(self.root.rglob('*.jsonl')))

    def test_native_blocked_source_requires_explicit_impact(self):
        import test_extractors as fixtures
        import markdown_extraction
        source = self.base / 'source.pdf'
        fixtures.write_pdf(source, ['The quantities are one and two.'])
        with patch.object(markdown_extraction.extractors.shutil, 'which', return_value=None):
            self.root = md.prepare(SimpleNamespace(report=self.base / 'report.md', sources=[source], temp_root=self.base / 'blocked-runs', lean_version='4.24.0'))
        with self.assertRaisesRegex(ValueError, 'ingestion-acknowledgement'):
            md.validate(self.root)
        self.assertIn('blocked', (self.root / 'input/extraction-index.md').read_text())
        self.assertFalse(list(self.root.rglob('*.json')) + list(self.root.rglob('*.jsonl')))

    @unittest.skipUnless(all(shutil.which(tool) for tool in ('pdfinfo', 'pdftotext', 'pdfimages')), 'PDF converters unavailable')
    def test_native_pdf_formal_verdict_requires_hash_bound_visual_review(self):
        import test_extractors as fixtures
        source = self.base / 'source.pdf'
        fixtures.write_pdf(source, ['The quantities are one and two.'])
        self.root = md.prepare(SimpleNamespace(report=self.base / 'report.md', sources=[source], temp_root=self.base / 'pdf-runs', lean_version='4.24.0'))
        projection = 'input/sources/1/source.pdf.extracted.md'
        anchor = next(iter(md.audit.paragraphs((self.root / projection).read_text())))
        (self.root / 'claims.md').write_text('# Claims\n\n' + md.table(['Claim ID', 'Report anchor', 'Disposition', 'Reason'], [('C1', 'R1', 'audited', 'Atomic arithmetic claim.')]))
        self.finding_sections['Evidence'] = '### Evidence 1\n\n- Source: ' + projection + '\n- Anchor: ' + anchor + '\n\n> The quantities are one and two.'
        self.finding_sections['Formal status'] = 'proved'
        self.finding_sections['Proofs'] = '### Proof P1\n\n- Module: Count\n- Theorem: Count.total\n- Target: (1 + 2 : Nat) = 3\n- Relation: supports'
        self.write_finding()
        with self.assertRaisesRegex(ValueError, 'visual review tied to original and passage hashes'):
            md.validate(self.root)
        index = md.rows((self.root / 'input/extraction-index.md').read_text())[0]
        segment = md.rows((self.root / 'input/extraction-segments.md').read_text())[0]
        headings = ['Projection', 'Anchor', 'Original SHA256', 'Passage SHA256', 'Decision', 'Reviewer', 'Reason']
        (self.root / 'extraction-review.md').write_text('# Synthetic review declaration for gate testing\n\n' + md.table(headings, [(projection, anchor, '0' * 64, segment['Passage SHA256'], 'accepted', 'Synthetic fixture', 'Validator test only; no independent visual inspection is claimed.')]))
        with self.assertRaisesRegex(ValueError, 'visual review tied to original and passage hashes'):
            md.validate(self.root)
        (self.root / 'extraction-review.md').write_text('# Synthetic review declaration for gate testing\n\n' + md.table(headings, [(projection, anchor, index['Original SHA256'], segment['Passage SHA256'], 'accepted', 'Synthetic fixture', 'Validator test only; no independent visual inspection is claimed.')]))
        with self.assertRaisesRegex(ValueError, 'missing individual registration'):
            md.validate(self.root)
        self.assertFalse(list(self.root.rglob('*.json')) + list(self.root.rglob('*.jsonl')))

    @unittest.skipUnless(LEAN.is_file(), 'Repository-local Lean unavailable; run "node tools/setup-toolchain.mjs"')
    def test_real_lean_markdown_only_end_to_end(self):
        (self.root / 'models/Count.lean').write_text('import Std\nnamespace Count\ntheorem total : (1 + 2 : Nat) = 3 := by decide\nend Count\n')
        self.finding_sections['Formal status'] = 'proved'
        self.finding_sections['Proofs'] = '### Proof P1\n\n- Module: Count\n- Theorem: Count.total\n- Target: (1 + 2 : Nat) = 3\n- Relation: supports'
        self.write_finding()
        # Execute the individual check before recording its verification event.
        import markdown_proofs
        individual = markdown_proofs.check_models(self.root, LEAN, [{'module':'Count','theorem':'Count.total','target':'(1 + 2 : Nat) = 3','relation':'supports','claim_id':'C1'}])
        for step in md.STEPS:
            path = self.root / 'claim-work/C1' / (step + '.md')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# Individual ' + step + '\n\nCompared the preserved quantities with the exact natural-number addition target.\n')
            outputs = [path] + ([individual[-1]['log']] if step == 'verification' else [])
            md.trace(SimpleNamespace(workspace=self.root, claim_id='C1', step=step, status='completed', title='Individual ' + step,
                description='Executed the individual source and arithmetic check.', input=['input/sources/1/source.md'], output=outputs,
                command=str(LEAN) + ' ' + individual[-1]['check_file'] if step == 'verification' else None,
                result='The exact target was checked; source authenticity is not established.'))
        md.check(SimpleNamespace(workspace=self.root, lean=LEAN, timeout=60))
        md.render(SimpleNamespace(workspace=self.root))
        self.assertIn('Executed exact-target', (self.root / 'validation.md').read_text())
        self.assertFalse(list(self.root.rglob('*.json')) + list(self.root.rglob('*.jsonl')))
