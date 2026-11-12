"""End-to-end gates for omitted briefs, empty dossiers, and report self-support."""
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from test_audit import audit, TEST_ROOT
from lean_location import lean_path
from contract_fixture import annotate_synthetic_finding, register_synthetic_report

RUN = Path(tempfile.mkdtemp(prefix='general-method-tests-', dir=TEST_ROOT))
LEAN = str(lean_path())


class GeneralMethodTests(unittest.TestCase):
    def setUp(self):
        self.case = Path(tempfile.mkdtemp(prefix=self._testMethodName+'-', dir=RUN))
        self.report = self.case/'report.md'; self.report.write_text('The amount is three.')
        self.sources = self.case/'sources'; self.sources.mkdir()

    def prepare(self, indexed=False):
        return Path(audit.prepare(SimpleNamespace(report=self.report, sources=[self.sources],
            temp_root=self.case/'runs', indexed=indexed))['workspace'])

    def finding(self, root, evidence=None):
        f = dict(claim_id='C1', report_quote='The amount is three.', report_anchor='P01',
            question='Is there evidence for the amount?', evidence=evidence or [],
            rules=['Require a recorded amount or a justified calculation.'], assumptions=[],
            reasoning=['The supplied dossier has no source passage establishing this amount.'],
            semantic_state='undetermined', argument_status='insufficient_support', formal_status='not_attempted',
            proofs=[], limits=['No evidence outside the supplied dossier was searched.'],
            recommendation='Supply the ledger that records the amount.')
        annotate_synthetic_finding(f)
        register_synthetic_report(root, [f])
        audit.write_json(root/'findings.json',[f])
        (root/'review-plan.md').write_text('Inspect the sole report assertion against the explicitly supplied source set.')
        (root/'semantic-review.md').write_text('Synthetic test fixture; no independent semantic review was performed.')
        return f

    def test_empty_dossier_without_brief_exports_honest_gap(self):
        for indexed in (False, True):
            root = self.prepare(indexed); self.finding(root)
            result = audit.check(SimpleNamespace(workspace=root, lean='/not-installed', timeout=10))
            self.assertEqual(result['status'], 'not_run')
            self.assertEqual(result['audit_contract']['audited_claims'], 1)
            self.assertEqual(audit.read_json(root/'input/audit-policy.json')['brief_origin'],'inferred')
            audit.export(SimpleNamespace(workspace=root, destination=str(root)+'-export'))
            self.assertIn('No independent source evidence', (root/'audit-report.md').read_text())

    def test_empty_search_cannot_establish_positive_or_negative_verdict(self):
        root = self.prepare(); f = self.finding(root)
        for state in ('supported','refuted','both'):
            f['semantic_state'] = state; audit.write_json(root/'findings.json',[f])
            with self.assertRaisesRegex(audit.AuditError, 'requires source evidence'):
                audit.check(SimpleNamespace(workspace=root, lean='/not-installed', timeout=10))

    def test_renamed_report_copy_cannot_support_itself_in_either_backend(self):
        (self.sources/'apparently-independent.md').write_bytes(self.report.read_bytes())
        for indexed in (False, True):
            root = self.prepare(indexed)
            e = dict(path='input/sources/apparently-independent.md', anchor='P01', quote='The amount is three.')
            f = self.finding(root,[e]); f['semantic_state']='supported'
            audit.write_json(root/'findings.json',[f])
            with self.assertRaisesRegex(audit.AuditError, 'copy of the report'):
                audit.check(SimpleNamespace(workspace=root, lean='/not-installed', timeout=10))

    @unittest.skipUnless(Path(LEAN).is_file(), 'Lean unavailable')
    def test_pure_arithmetic_can_have_direct_proof_without_source_document(self):
        self.report.write_text('One plus two equals three.')
        root=self.prepare()
        f=dict(claim_id='C1',report_quote='One plus two equals three.',report_anchor='P01',
            question='Is this arithmetic equality true?', evidence=[],rules=['Use natural-number arithmetic.'],
            assumptions=[],reasoning=['Compute one plus two in Nat.'],semantic_state='supported',
            argument_status='insufficient_support',formal_status='proved',
            proofs=[dict(module='Arithmetic',theorem='sum_correct',relation='supports',target='(1 + 2 : Nat) = 3')],
            limits=['This is a mathematical statement, not a claim about external ledger entries.'],recommendation='Retain the equality.')
        annotate_synthetic_finding(f); register_synthetic_report(root,[f]);audit.write_json(root/'findings.json',[f])
        (root/'models/Arithmetic.lean').write_text('theorem sum_correct : (1 + 2 : Nat) = 3 := by decide\n')
        result=audit.check(SimpleNamespace(workspace=root,lean=LEAN,timeout=60))
        self.assertEqual(result['status'],'passed')
        self.assertTrue(any(t['target']=='(1 + 2 : Nat) = 3' for t in result['targets']))
