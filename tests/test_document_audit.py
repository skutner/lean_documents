"""Behavioral checks for retained, readable individual audit records.

All execution artifacts stay below repository tmp for inspection.
"""
from pathlib import Path
from types import SimpleNamespace as A
from unittest import mock
import importlib.util
import json
import re
import shutil
import sys
import tempfile
import unittest

repo = Path(__file__).resolve().parents[1]
from lean_location import lean_path
spec = importlib.util.spec_from_file_location('document_audit', repo / 'tools/document_audit.py')
d = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(d)
    OLD_PYTHON = None
except ImportError as error:
    # tools/document_audit.py refuses to be imported below Python 3.12; the tests then skip with that reason.
    if sys.version_info >= (3, 12):
        raise
    d, OLD_PYTHON = None, str(error)
LEAN = lean_path()
TEST_ROOT = repo / 'tmp/document-audit-tests'


def completed_fixture(language="en"):
    base=Path(tempfile.mkdtemp(prefix='case-',dir=TEST_ROOT))
    (base/'report.md').write_text('# Synthetic helper test\n\n## R01 — Sum\n\nThe total is three.\n\n## R02 — Difference\n\nThe difference from ten is eight.\n\n## R03 — Payment\n\nThe payment completed.\n')
    (base/'source.md').write_text('# Synthetic source\n\n## S01 — Quantities\n\nThe quantities are one and two; their total is their sum.\n\n## S02 — Comparison\n\nThe comparison amount is ten; the difference is ten minus the total.\n\n## S03 — Missing outcome\n\nThe supplied record contains no payment result and establishes neither success nor failure.\n')
    w=d.prepare(A(report=base/'report.md',sources=[base/'source.md'],temp_root=base/'work', language=language))
    reg=d.read(w/'claims.json'); claims=[]
    for a in reg['anchors']:
     a['classification']='non_claim' if a['anchor']=='document-preamble' else 'claims';a['reason']='Synthetic helper test classification.'
     if a['classification']=='claims':claims.append({'claim_id':'C'+a['anchor'][-1], 'report_anchor':a['anchor'],'report_quote':a['quote'],'disposition':'audited','reason':'Synthetic individual claim.'})
    reg['claims']=claims;d.write(w/'claims.json',reg)
    (w/'models/Sum.lean').write_text('theorem total : (1 + 2 : Nat) = 3 := by decide\n')
    (w/'models/Difference.lean').write_text('theorem difference : (10 - (1 + 2) : Nat) = 7 ∧ (10 - (1 + 2) : Nat) ≠ 8 := by decide\n')
    source=d.paragraphs((w/'input/sources/1/source.md').read_text())
    for index,c in enumerate(claims,1):
     ident=c['claim_id']; state=['supported','refuted','undetermined'][index-1]
     proof=({'module':'Sum','theorem':'total','target':'(1 + 2 : Nat) = 3','relation':'supports','claim_id':ident} if index==1 else {'module':'Difference','theorem':'difference','target':'(10 - (1 + 2) : Nat) = 7 ∧ (10 - (1 + 2) : Nat) ≠ 8','relation':'refutes','claim_id':ident}) if index<3 else None
     f={**{k:c[k] for k in ('claim_id','report_anchor','report_quote')},'title':['Exact sum','Comparison difference','Missing payment result'][index-1], 'question':'Does this exact assertion follow from the synthetic source?',
     'executive_summary':{'conclusion':['The total is three.','The difference is seven, so eight is incorrect.','The payment outcome remains undetermined.'][index-1],'how_verified':['Added one and two and checked the exact total in Lean.','Subtracted the three-unit total from ten and checked seven differs from eight in Lean.','Read the source record that explicitly withholds any payment outcome.'][index-1],'limits':'This is a synthetic helper test, not an independent real-world audit.'},
     'evidence':[{'path':'input/sources/1/source.md','anchor':'S0'+str(index),'quote':source['S0'+str(index)],'explanation':'Supplies the relevant synthetic comparison or explicit limitation.'}],
     'rules':'Apply only the source arithmetic and explicit evidence limits.','assumptions':[],'reasoning':'Compare the exact reported statement with the recorded source rule and values.','argument_audit':'The report states the assertion without a separate argument; independent inspection supplies the described check.','limits':'Source authenticity is outside this synthetic test.','formalization':'The exact arithmetic target checks the source quantities.' if proof else 'No event proof is justified because the source provides no payment outcome.','proofs':[proof] if proof else [],'semantic_state':state,'argument_status':'sufficient' if index==1 else 'invalid_inference' if index==2 else 'insufficient_support','formal_status':'proved' if index==1 else 'refuted' if index==2 else 'not_attempted'}
     d.write(w/f'findings/{ident}.json',f)
     for step in d.STEPS:
      logs=[];command=None
      if step=='verification' and proof:
       results=d.check_models(w,LEAN,[proof]);logs=[r['log'] for r in results if r.get('claim_id')==ident];command=str(Path(sys.executable))+' '+str(Path(__file__).resolve())
      document=w/f'claim-work/{ident}/{step}.json'
      d.write(document,{'title':ident+' '+step,'purpose':'Exercise this individual synthetic audit step with retained artifacts.','method':'Read the source and perform the specific arithmetic or documentary check described in the finding.','observations':[{'source':'input/sources/1/source.md','quote':source['S0'+str(index)],'use':'The recorded synthetic input for this individual claim.'}],'result':'The helper retained the actual individual work and its declared scope.','limits':'Synthetic functional test only; not independent semantic validation.',**({'command':command} if command else {})})
      outputs=[document,*logs]+([w/f'findings/{ident}.json'] if step=='verdict' else [])
      d.record(A(workspace=w,claim_id=ident,step=step,document=document,input=['input/report.md','claims.json','input/sources/1/source.md'],output=outputs))
    d.check(A(workspace=w,lean=LEAN,lean_version='4.24.0',timeout=60));d.render(A(workspace=w))
    return w


@unittest.skipIf(OLD_PYTHON is not None, 'tools/document_audit.py needs Python 3.12 or later: ' + str(OLD_PYTHON))
@unittest.skipUnless(LEAN.is_file(), 'Repository-local Lean 4.24.0 is required; run "node tools/setup-toolchain.mjs"')
class DocumentAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        cls.original = completed_fixture()

    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp(prefix='review-', dir=TEST_ROOT)) / 'audit'
        shutil.copytree(self.original, self.workspace)

    def inspect(self):
        return json.loads(d.inspect(A(workspace=self.workspace, machine=True)))

    def test_real_lean_and_complete_readable_delivery(self):
        result = self.inspect()
        self.assertTrue(result['trace_complete'])
        self.assertTrue(result['report_fresh'])
        self.assertEqual(result['proof_results']['integrity'], 'current')
        self.assertEqual(len(result['findings']), 3)
        self.assertEqual(len(result['phases']), 15)
        targets = [r for r in result['proof_results']['targets'] if r.get('claim_id')]
        self.assertEqual({r['relation'] for r in targets}, {'supports', 'refutes'})
        for target in targets:
            self.assertIn('Exit code: 0', (self.workspace / target['log']).read_text())
            self.assertTrue((self.workspace / target['check_file']).is_file())
        for event in result['phases']:
            for ref in event['inputs'] + event['outputs']:
                self.assertFalse(ref['snapshot'].endswith('.json'))
                self.assertEqual(d.sha(self.workspace / ref['snapshot']), ref['sha256'])
        for canonical in self.workspace.rglob('*.json'):
            self.assertTrue(canonical.with_suffix('.md').is_file(), canonical)
        for document in self.workspace.rglob('*.md'):
            for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', document.read_text()):
                self.assertFalse(target.endswith('.json'), (document, target))
                self.assertTrue((document.parent / target.split('#')[0]).is_file(), (document, target))
        text = (self.workspace / 'findings/C3.md').read_text()
        self.assertIn('No additional assumptions are declared.', text)
        self.assertIn('insufficient support', text)
        binding = (self.workspace / 'report-integrity.md').read_text()
        self.assertIn('validation.json (internal data)', binding)
        self.assertIn(d.sha(self.workspace / 'validation.md'), binding)

    def test_romanian_templates_preserve_authored_content_and_canonical_keys(self):
        workspace = completed_fixture(language='ro')
        self.assertEqual(d.read(workspace / 'manifest.json')['language'], 'ro')
        finding = d.read(workspace / 'findings/C3.json')
        text = (workspace / 'findings/C3.md').read_text()
        self.assertIn('## Ce a fost examinat', text)
        self.assertIn('## Verificări efectuate', text)
        self.assertIn('justificare insuficientă', text)
        self.assertIn(finding['executive_summary']['conclusion'], text)
        self.assertIn('> ' + finding['report_quote'], text)
        self.assertIn('> ' + finding['evidence'][0]['quote'], text)
        self.assertEqual(finding['semantic_state'], 'undetermined')
        self.assertIn('# Auditul afirmațiilor bazat pe dovezi', (workspace / 'audit-report.md').read_text())
        self.assertIn('# Inventarul documentelor furnizate', (workspace / 'input-inventory.md').read_text())
        self.assertIn('Rezultatul validării înregistrate este **reușită**', (workspace / 'validation.md').read_text())
        # Even text identical to an English heading belongs to the author here.
        phase = dict(title='Source', purpose='Work performed', method='Limits',
                     observations=[dict(source='input/report.md', quote='Source\nLimits', use='Result')],
                     result='Result', limits='Limits')
        rendered = d.phase_document(workspace, workspace / 'phase.md', phase)
        self.assertIn('# Source\n\nWork performed', rendered)
        self.assertIn('> Source\n> Limits', rendered)
        self.assertIn('## Rezultat\n\nResult', rendered)
        self.assertTrue(json.loads(d.inspect(A(workspace=workspace, machine=True)))['report_fresh'])
        self.assertTrue(self.inspect()['report_fresh'])

    def test_unrecognized_finding_data_is_not_silently_discarded(self):
        path = self.workspace / 'findings/C1.json'
        data = d.read(path)
        data['unrendered_material_fact'] = 'Material information must not disappear.'
        d.write(path, data)
        with self.assertRaisesRegex(ValueError, 'unsupported fields'):
            d.validate(self.workspace)

    def test_other_claim_steps_cannot_fill_individual_gap(self):
        records = list((self.workspace / 'trace/C3').glob('*analysis.json'))
        self.assertEqual(len(records), 1)
        records[0].unlink()
        result = self.inspect()
        self.assertFalse(result['trace_complete'])
        self.assertTrue(any('missing individual analysis' in e for e in result['errors']))

    def test_changed_source_invalidates_prior_delivery(self):
        path = self.workspace / 'input/sources/1/source.md'
        path.write_text(path.read_text() + '\nChanged source bytes.\n')
        result = self.inspect()
        self.assertFalse(result['report_fresh'])
        self.assertFalse(result['proof_results']['current'])
        with self.assertRaises(ValueError):
            d.render(A(workspace=self.workspace))

    def test_style_upgrade_preserves_binding_but_report_tampering_does_not(self):
        with mock.patch.object(d, 'report_content', return_value='# Future design\n'):
            self.assertTrue(self.inspect()['report_fresh'])
        (self.workspace / 'audit-report.md').write_text('# Changed delivered conclusion\n')
        self.assertFalse(self.inspect()['report_fresh'])

    def test_target_rejects_commands_before_execution(self):
        proof = d.read(self.workspace / 'findings/C1.json')['proofs'][0]
        proof['target'] = 'True\n#eval IO.println "unexpected execution"'
        with self.assertRaisesRegex(ValueError, 'Target must be one proposition'):
            d.check_models(self.workspace, LEAN, [proof])

    def test_long_quoted_tables_remain_readable_and_phase_metadata_visible(self):
        quote = 'Source context.\n\n| Term | Value |\n| --- | --- |\n| Amount | 8 |'
        phase = dict(title='Documentary check', purpose='Inspect the supplied terms.',
                     method='Compare the recorded amount.', observations=[dict(source='input/report.md', quote=quote, use='Establish the amount.')],
                     result='The amount is eight.', limits='Supplied terms only.', status='blocked', command='python inspect_terms.py')
        rendered = d.phase_document(self.workspace, self.workspace / 'phase.md', phase)
        self.assertIn('> | Amount | 8 |', rendered)
        self.assertNotIn('&#124;', rendered)
        self.assertIn('**blocked**', rendered)
        self.assertIn('python inspect_terms.py', rendered)


if __name__ == '__main__':
    unittest.main()
