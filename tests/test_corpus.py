"""Independent corpus/integration regressions; retain every run under repo tmp/."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

REPO=Path(__file__).resolve().parents[1]
SCRIPTS=REPO/'skills/evidence-to-proof-review/scripts'
if str(SCRIPTS) not in sys.path:sys.path.insert(0,str(SCRIPTS))
import corpus
import audit
import corrections
from test_extractors import write_pdf, write_docx
from contract_fixture import annotate_synthetic_finding, register_synthetic_report
from lean_location import lean_path
LEAN=lean_path()


class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (REPO/'tmp').mkdir(exist_ok=True)
        cls.retained_root=Path(tempfile.mkdtemp(prefix='corpus-tests-',dir=REPO/'tmp'))
        print('\nCorpus test artifacts retained at '+str(cls.retained_root),flush=True)
        corpus.save(cls.retained_root/'capabilities.json',{'lean':str(LEAN) if LEAN.is_file() else None,
            **{tool:shutil.which(tool) for tool in ('pdfinfo','pdftotext','pdftoppm','tesseract')}})

    def setUp(self):
        self.case=self.retained_root/self._testMethodName;self.case.mkdir()
        self.brief=self.case/'brief.md';self.brief.write_text('Audit the exact reported amount.\n')
        self.report=self.case/'report.md';self.report.write_text('<a id="R1"></a>\nThe total is three.\n\n<a id="R2"></a>\nThe other amount is five.\n')
        self.sources=self.case/'sources';self.sources.mkdir()
        self.first=self.sources/'first.md';self.first.write_text('<a id="S1"></a>\nThe exact amount is three.\n')
        self.second=self.sources/'second.md';self.second.write_text('<a id="S2"></a>\nThe other exact amount is five.\n')
        self.work=self.case/'runs'

    def args(self,**updates):
        values=dict(brief=self.brief,report=self.report,sources=[self.sources],temp_root=self.work,
            indexed=True,ocr_language='eng',extract_timeout=60,max_pages=None,max_documents=None,max_seconds=None)
        values.update(updates);return SimpleNamespace(**values)

    def prepare(self,**updates):return Path(corpus.prepare(self.args(**updates))['workspace'])

    def finding(self,cid='C1',path='input/sources/first.md',anchor='S1',quote='The exact amount is three.',formal=False):
        return dict(claim_id=cid,report_quote='The total is three.',report_anchor='R1',question='Does the supplied exact amount support three?',
            evidence=[dict(path=path,anchor=anchor,quote=quote)],rules=['Use the named source amount.'],assumptions=['The source transcription is accurate.'],
            reasoning=['The cited passage states the amount directly.'],semantic_state='supported',argument_status='insufficient_support',
            formal_status='proved' if formal else 'not_attempted',
            proofs=[dict(module='Audit',theorem='Audit.total',relation='supports',target='(1 + 2 : Nat) = 3')] if formal else [],
            limits=['This unit fixture does not independently authenticate its source.'],recommendation='Retain the amount within the supplied scope.')

    def save_findings(self,root,formal=False):
        fs=[self.finding(formal=formal),self.finding(cid='C2',path='input/sources/second.md',anchor='S2',quote='The other exact amount is five.')]
        fs[1].update(report_quote='The other amount is five.',report_anchor='R2')
        for finding in fs: annotate_synthetic_finding(finding)
        register_synthetic_report(root, fs)
        corpus.save(root/'findings.json',fs)
        if formal:(root/'models/Audit.lean').write_text('import Std\nnamespace Audit\ntheorem total : (1 + 2 : Nat) = 3 := by decide\nend Audit\n')
        return fs

    def document(self,root,logical):return next(d for d in corpus.inventory(root) if d['logical']==logical)

    def test_indexed_prepare_without_optional_brief(self):
        for explicit_none in (True, False):
            args = self.args(brief=None)
            if not explicit_none:
                del args.brief
            with self.subTest(explicit_none=explicit_none):
                root = Path(corpus.prepare(args)['workspace'])
                self.assertFalse((root/'input/brief.md').exists())
                self.assertNotIn('input/brief.md', [d['logical'] for d in corpus.inventory(root)])
                self.assertEqual(corpus.read(root/'input/audit-policy.json')['contract_version'], 2)
                corpus.verify(root)

    def test_bounded_ingestion_resumes_all_documents(self):
        result=corpus.prepare(self.args(max_documents=1));root=Path(result['workspace'])
        self.assertEqual(len(result['coverage']['processed_this_batch']),1)
        self.assertFalse(result['coverage']['complete'])
        self.assertEqual(sum(d['status']=='pending' for d in corpus.inventory(root)),3)
        result=corpus.resume(root,max_documents=2)
        self.assertEqual(len(result['coverage']['processed_this_batch']),2)
        self.assertFalse(result['coverage']['complete'])
        result=corpus.resume(root)
        self.assertTrue(result['coverage']['complete'])
        self.assertEqual(len(result['coverage']['processed_this_batch']),1)
        corpus.verify(root)

    def test_native_docx_auto_prepare_preserves_bytes_and_coordinates(self):
        path=self.case/'native.docx'
        write_docx(path,'<w:p><w:r><w:t>Native amount three.</w:t></w:r></w:p>')
        root=Path(audit.prepare(self.args(sources=[path],indexed=False))['workspace'])
        doc=self.document(root,'input/sources/native.docx.md')
        self.assertEqual((root/doc['original']).read_bytes(),path.read_bytes())
        segment=corpus.passage(root,doc['logical'],'docx-p-0001')
        self.assertEqual(segment['quote'],'Native amount three.')
        self.assertEqual(segment['locator']['member'],'word/document.xml')
        self.assertIn('/w:p[1]',segment['locator']['xml_path'])
        self.assertEqual(segment['locator']['original_sha256'],corpus.sha(path))
        self.assertTrue((root/segment['locator']['artifact_directory']/'docx-xml/document.xml').exists())

    def test_search_paginates_and_retains_cross_chunk_exception(self):
        paragraphs=['<a id="A%d"></a>\nregulation paragraph %d.'%(i,i) for i in range(8)]
        long='General obligation '+('context '*260)+'EXCEPTION signed waiver eliminates this obligation.'
        self.first.write_text('\n\n'.join(paragraphs)+'\n\n<a id="LONG"></a>\n'+long)
        root=self.prepare()
        pages=[corpus.search(root,'regulation',limit=3,offset=offset) for offset in (0,3,6)]
        self.assertEqual(pages[0]['total_matching_chunks'],8)
        self.assertEqual(pages[0]['limit'],3)
        self.assertIn('not exhaustive',pages[0]['limitations'])
        ids=[r['id'] for page in pages for r in page['results']]
        self.assertEqual(len(ids),8);self.assertEqual(len(set(ids)),8)
        match=corpus.search(root,'EXCEPTION',limit=1)['results'][0]
        passage=corpus.passage(root,match['path'],match['anchor'])
        self.assertTrue(passage['quote'].startswith('General obligation'))
        self.assertIn('waiver eliminates',passage['quote'])
        self.assertGreater(len(passage['quote']),2000)
        self.assertTrue((root/'search-history.jsonl').exists())

    def test_tampered_sqlite_index_rejected(self):
        root=self.prepare()
        with (root/'input/index.sqlite3').open('ab') as stream:stream.write(b'tampered')
        with self.assertRaises(ValueError):corpus.search(root,'amount')

    def test_tampered_original_snapshot_rejected(self):
        root=self.prepare();doc=self.document(root,'input/sources/first.md')
        (root/doc['original']).write_text('changed snapshot')
        with self.assertRaises(ValueError):corpus.resume(root)

    def test_tampered_raw_extraction_artifact_rejected(self):
        root=self.prepare();segment=corpus.passage(root,'input/sources/first.md','S1')
        artifact=root/segment['locator']['artifact_directory']/'extraction.json'
        artifact.write_text('{}\n')
        with self.assertRaises(ValueError):corpus.verify(root)

    def test_tampered_extraction_blocks_audit_integration(self):
        root=self.prepare();self.save_findings(root)
        segment=corpus.passage(root,'input/sources/first.md','S1')
        artifact=root/segment['locator']['artifact_directory']/'extraction.json'
        artifact.write_text('{}\n')
        with self.assertRaises((ValueError,audit.AuditError)):audit.findings(root)

    def test_missing_extraction_lock_cannot_disable_integrity(self):
        root=self.prepare()
        (root/'extraction-lock.json').unlink()
        with self.assertRaises(ValueError):corpus.verify(root)

    def test_tiny_time_budget_is_explicitly_incomplete(self):
        result=corpus.prepare(self.args(max_seconds=0.000000001))
        self.assertFalse(result['coverage']['complete'])
        self.assertEqual(result['coverage']['processed_this_batch'],[])
        self.assertTrue(all(d['status']=='pending' for d in result['coverage']['documents']))

    def test_incomplete_ingestion_requires_explicit_accounting(self):
        root=self.prepare(max_documents=2)
        with self.assertRaisesRegex(ValueError,'Incomplete ingestion'):corpus.assert_reviewable(root,[])
        incomplete=[d for d in corpus.inventory(root) if d['status']!='ready']
        corpus.save(root/'coverage-acknowledgement.json',{'documents':[{'document_id':d['id'],'reason':'Not processed in bounded batch.','impact':'No source-supported verdict may depend on this missing document.'} for d in incomplete]})
        corpus.assert_reviewable(root,[])

    @unittest.skipUnless(LEAN.is_file(),'Actual Lean unavailable; capabilities.json records this skip')
    def test_noop_resume_preserves_successful_proof_fingerprint(self):
        root=self.prepare();self.save_findings(root,formal=True)
        review=b'The complete synthetic fixture corpus has been reviewed.\n'
        (root/'semantic-review.md').write_bytes(review)
        result=audit.check(SimpleNamespace(workspace=root,lean=LEAN,timeout=60))
        self.assertEqual(result['status'],'passed')
        before=corpus.sha(root/'manifest.json')
        response=corpus.resume(root)
        self.assertEqual(response['coverage']['processed_this_batch'],[])
        self.assertEqual(corpus.sha(root/'manifest.json'),before)
        self.assertEqual((root/'semantic-review.md').read_bytes(),review)
        self.assertFalse(list((root/'history').glob('ingestion-review-*')))
        audit.verified(root)

    def test_source_temp_overlap_rejected(self):
        with self.assertRaisesRegex(ValueError,'outside supplied source'):
            corpus.prepare(self.args(temp_root=self.sources/'nested-tmp'))

    def test_legacy_source_temp_overlap_also_rejected(self):
        with self.assertRaises((ValueError,audit.AuditError)):
            audit.prepare(self.args(indexed=False,temp_root=self.sources/'nested-tmp'))

    @unittest.skipUnless(shutil.which('pdfinfo') and shutil.which('pdftotext'),'Real Poppler unavailable; recorded explicitly')
    def test_page_limited_pdf_retry_retains_old_attempt_and_original(self):
        path=self.case/'two.pdf';write_pdf(path,['First native page.','Second native page.'])
        root=self.prepare(sources=[path],max_pages=1)
        doc=self.document(root,'input/sources/two.pdf.md')
        self.assertEqual(doc['status'],'partial')
        before=corpus.sha(root/doc['original'])
        attempts=list((root/'extraction'/doc['id']).iterdir());self.assertEqual(len(attempts),1)
        result=corpus.resume(root,retry=True,max_pages=2)
        self.assertTrue(result['coverage']['complete'])
        self.assertEqual(corpus.sha(root/doc['original']),before)
        self.assertEqual(len(list((root/'extraction'/doc['id']).iterdir())),2)
        self.assertIsNotNone(corpus.passage(root,doc['logical'],'pdf-page-0002'))

    @unittest.skipUnless(shutil.which('pdfinfo') and shutil.which('pdftotext'),'Real Poppler unavailable; recorded explicitly')
    def test_pdf_retry_archives_stale_semantic_review_and_blocks_export(self):
        pdf=self.case/'reviewed.pdf'
        write_pdf(pdf,['The total is three.','The first-page total is superseded by four.'])
        self.report.write_text('<a id="R1"></a>\nThe total is three.\n')
        root=self.prepare(sources=[pdf],max_pages=1)
        doc=self.document(root,'input/sources/reviewed.pdf.md')
        finding=self.finding(path=doc['logical'],anchor='pdf-page-0001',quote='The total is three.')
        annotate_synthetic_finding(finding);register_synthetic_report(root,[finding])
        corpus.save(root/'findings.json',[finding])
        corpus.save(root/'coverage-acknowledgement.json',{'documents':[{
            'document_id':doc['id'],'reason':'Second page is pending.',
            'impact':'Only the extracted first page was reviewed.'}]})
        (root/'review-plan.md').write_text('Review all available source pages and record unprocessed pages.\n')
        review=b'Only the first page was available and reviewed; later pages remain outside this assessment.\n'
        (root/'semantic-review.md').write_bytes(review)
        audit.check(SimpleNamespace(workspace=root,lean='/not-needed',timeout=60))
        prior_manifest=corpus.sha(root/'manifest.json')
        corpus.resume(root,retry=True,max_pages=2)
        self.assertFalse((root/'semantic-review.md').exists())
        archives=list((root/'history').glob('ingestion-review-*'))
        self.assertEqual(len(archives),1)
        self.assertEqual((archives[0]/'semantic-review.md').read_bytes(),review)
        invalidation=corpus.read(archives[0]/'invalidation.json')
        self.assertEqual(invalidation['prior_manifest_sha256'],prior_manifest)
        self.assertEqual(invalidation['current_manifest_sha256'],corpus.sha(root/'manifest.json'))
        self.assertEqual(invalidation['processed_documents'],[doc['id']])
        self.assertIn('superseded',corpus.passage(root,doc['logical'],'pdf-page-0002')['quote'])
        audit.check(SimpleNamespace(workspace=root,lean='/not-needed',timeout=60))
        with self.assertRaisesRegex(audit.AuditError,'completed semantic-review.md'):
            audit.export(SimpleNamespace(workspace=root,destination=self.case/'stale-export'))
        self.assertFalse((self.case/'stale-export').exists())
        corpus.verify(root)

    @unittest.skipUnless(shutil.which('pdfinfo') and shutil.which('pdftotext'),'Real Poppler unavailable; recorded explicitly')
    def test_pdf_retry_preserves_reviewed_overlays_and_invalidates_old_check(self):
        pdf=self.case/'two.pdf';write_pdf(pdf,['First native page.','Second native page.'])
        root=self.prepare(sources=[pdf],max_pages=1)
        path='input/sources/two.pdf.md';doc=self.document(root,path)
        original_bytes=(root/doc['original']).read_bytes()
        manual=self.case/'manual.txt';manual.write_text('Reviewed first native page.\n')
        first=corrections.transcribe(root,path,'pdf-page-0001',manual,'Fixture reviewer','Synthetic fixture: preserve a reviewed overlay during retry.')
        manual.write_text('Second reviewed interpretation of the first page.\n')
        second=corrections.transcribe(root,path,first['anchor'],manual,'Fixture reviewer','Synthetic fixture: preserve the review chain.')
        before={r['anchor']:corpus.passage(root,path,r['anchor']) for r in (first,second)}
        snapshots={r['transcription_snapshot']:(root/r['transcription_snapshot']).read_bytes() for r in (first,second)}
        reviews=(root/'extraction-review.json').read_bytes()
        corpus.save(root/'coverage-acknowledgement.json',{'documents':[{'document_id':doc['id'],'reason':'Page two is pending.','impact':'No assertion about omitted page content.'}]})
        finding=self.finding(path=path,anchor=first['anchor'],quote=before[first['anchor']]['quote'])
        annotate_synthetic_finding(finding)
        register_synthetic_report(root, [finding], deferred=[{
            'claim_id': 'C2', 'report_anchor': 'R2', 'report_quote': 'The other amount is five.',
            'reason': 'This focused provenance test audits only the first fixture claim.'}])
        corpus.save(root/'findings.json',[finding])
        audit.check(SimpleNamespace(workspace=root,lean='/not-needed',timeout=60))
        result=corpus.resume(root,retry=True,max_pages=2)
        self.assertTrue(result['coverage']['complete'])
        for anchor,segment in before.items():
            self.assertEqual(corpus.passage(root,path,anchor),segment)
            self.assertIn(f'<a id="{anchor}"></a>',(root/path).read_text())
        self.assertTrue(any(r['anchor']==second['anchor'] for r in corpus.search(root,'interpretation')['results']))
        self.assertEqual((root/'extraction-review.json').read_bytes(),reviews)
        self.assertEqual((root/doc['original']).read_bytes(),original_bytes)
        for rel,data in snapshots.items():self.assertEqual((root/rel).read_bytes(),data)
        # Original review bindings still cover exactly these overlays, not new extraction.
        finding['formal_status']='proved';corpus.assert_reviewable(root,[finding])
        new_machine=corpus.passage(root,path,'pdf-page-0001')
        raw=corpus.read(root/new_machine['locator']['artifact_directory']/'extraction.json')
        self.assertFalse(any(s['method']=='manual-transcription' for s in raw['segments']))
        with self.assertRaises(audit.AuditError):audit.verified(root)
        corpus.verify(root)

    @unittest.skipUnless(shutil.which('pdfinfo') and shutil.which('pdftotext'),'Real Poppler unavailable; recorded explicitly')
    def test_retry_rejects_manual_anchor_collision_without_overwriting_passage(self):
        pdf=self.case/'two.pdf';write_pdf(pdf,['First native page.','Second native page.'])
        root=self.prepare(sources=[pdf],max_pages=1);path='input/sources/two.pdf.md'
        manual=self.case/'manual.txt';manual.write_text('Reviewed first native page.\n')
        record=corrections.transcribe(root,path,'pdf-page-0001',manual,'Fixture reviewer','Synthetic collision test.')
        before=corpus.passage(root,path,record['anchor']);derived=(root/path).read_bytes()
        extract=corpus.extract_document
        def collision(*args,**kwargs):
            result=extract(*args,**kwargs)
            result['segments'][0]['anchor']=record['anchor']
            return result
        with mock.patch.object(corpus,'extract_document',side_effect=collision):
            with self.assertRaisesRegex(ValueError,'collides with retained manual'):
                corpus.resume(root,retry=True,max_pages=2)
        self.assertEqual(corpus.passage(root,path,record['anchor']),before)
        self.assertEqual((root/path).read_bytes(),derived)
        corpus.verify(root)

    def test_refresh_changed_original_does_not_reapply_manual_overlay(self):
        root=self.prepare();path='input/sources/first.md';doc=self.document(root,path)
        manual=self.case/'manual.txt';manual.write_text('Reviewed original wording.\n')
        record=corrections.transcribe(root,path,'S1',manual,'Fixture reviewer','Synthetic revision invalidation test.')
        finding=self.finding(anchor=record['anchor'],quote='Reviewed original wording.')
        annotate_synthetic_finding(finding)
        register_synthetic_report(root, [finding], deferred=[{
            'claim_id': 'C2', 'report_anchor': 'R2', 'report_quote': 'The other amount is five.',
            'reason': 'This focused provenance test audits only the first fixture claim.'}])
        corpus.save(root/'findings.json',[finding])
        replacement=self.case/'replacement.md';replacement.write_text('<a id="S1"></a>\nChanged original wording.\n')
        result=corpus.refresh(root,doc['id'],replacement)
        revised=Path(result['workspace'])
        self.assertIsNotNone(corpus.passage(root,path,record['anchor']))
        self.assertIsNone(corpus.passage(revised,path,record['anchor']))
        self.assertEqual((root/record['transcription_snapshot']).read_bytes(),(revised/record['transcription_snapshot']).read_bytes())
        self.assertEqual(result['invalidation']['invalidated_manual_transcriptions'],[record['anchor']])
        revised_doc=self.document(revised,path)
        self.assertIn('invalidated by changed original bytes',revised_doc['warnings'])
        self.assertEqual(result['invalidation']['affected_claims'],['C1'])
        corpus.verify(root);corpus.verify(revised)

    @unittest.skipUnless(shutil.which('pdfinfo') and shutil.which('pdftotext'),'Real Poppler unavailable; recorded explicitly')
    def test_uncertain_source_formal_gate_bound_to_original_hash(self):
        path=self.case/'native.pdf';write_pdf(path,['The total is three.'])
        root=self.prepare(sources=[path]);doc=self.document(root,'input/sources/native.pdf.md')
        f=self.finding(path=doc['logical'],anchor='pdf-page-0001',quote='The total is three.',formal=True)
        with self.assertRaisesRegex(ValueError,'original-source review'):corpus.assert_reviewable(root,[f])
        review={'path':doc['logical'],'anchor':'pdf-page-0001','decision':'accepted','reviewer':'Unit-test reviewer','reason':'Compared the retained fixture PDF with its exact text.','source_sha256':doc['original_sha'],'segment_sha256':hashlib.sha256(corpus.passage(root,doc['logical'],'pdf-page-0001')['quote'].encode()).hexdigest()}
        corpus.save(root/'extraction-review.json',{'reviews':[review]})
        corpus.assert_reviewable(root,[f])
        review['source_sha256']='wrong-hash';corpus.save(root/'extraction-review.json',{'reviews':[review]})
        with self.assertRaises(ValueError):corpus.assert_reviewable(root,[f])

    @unittest.skipUnless(shutil.which('pdfinfo') and shutil.which('pdftotext'),'Real Poppler unavailable; recorded explicitly')
    def test_uncertain_report_claim_requires_original_review(self):
        report=self.case/'report.pdf';write_pdf(report,['The total is three.'])
        root=self.prepare(report=report)
        f=self.finding(formal=True);f['report_anchor']='pdf-page-0001'
        with self.assertRaisesRegex(ValueError,'review'):corpus.assert_reviewable(root,[f])

    @unittest.skipUnless(shutil.which('pdftoppm') and shutil.which('tesseract'),'Real OCR dependencies unavailable; recorded explicitly')
    def test_actual_ocr_report_requires_hash_bound_review(self):
        native=self.case/'report-native.pdf';write_pdf(native,['The total is three.'])
        prefix=self.case/'report-scan'
        command=[shutil.which('pdftoppm'),'-f','1','-singlefile','-r','200','-png',str(native),str(prefix)]
        completed=subprocess.run(command,capture_output=True,text=True,timeout=60)
        corpus.save(self.case/'raster-command.json',{'command':command,'returncode':completed.returncode,'stdout':completed.stdout,'stderr':completed.stderr})
        self.assertEqual(completed.returncode,0)
        root=self.prepare(report=prefix.with_suffix('.png'))
        doc=self.document(root,'input/report.md');seg=corpus.passage(root,'input/report.md','image-page-0001')
        self.assertIsNotNone(seg);self.assertEqual(seg['method'],'tesseract-tsv')
        if doc['status']!='ready':
            corpus.save(root/'coverage-acknowledgement.json',{'documents':[{'document_id':doc['id'],'reason':'OCR uncertainty requires original review.','impact':'Only manually checked passages may be used.'}]})
        f=self.finding(formal=True);f.update(report_anchor='image-page-0001',report_quote=seg['quote'])
        with self.assertRaisesRegex(ValueError,'original-source review'):corpus.assert_reviewable(root,[f])
        review={'path':'input/report.md','anchor':'image-page-0001','decision':'accepted','reviewer':'Unit-test reviewer','reason':'Compared OCR quote with the original rendered test sentence.','source_sha256':doc['original_sha'],'segment_sha256':hashlib.sha256(seg['quote'].encode()).hexdigest()}
        corpus.save(root/'extraction-review.json',{'reviews':[review]})
        corpus.assert_reviewable(root,[f])
        review['segment_sha256']='stale-segment-hash';corpus.save(root/'extraction-review.json',{'reviews':[review]})
        with self.assertRaises(ValueError):corpus.assert_reviewable(root,[f])

    def test_refresh_destination_cannot_be_inside_parent_workspace(self):
        root=self.prepare();first=self.document(root,'input/sources/first.md')
        replacement=self.case/'replacement.md';replacement.write_text('Changed source.')
        # Prevent actual recursive copying if the production guard is missing.
        with mock.patch.object(corpus.shutil,'copytree',side_effect=AssertionError('Recursive source/destination copy attempted')):
            with self.assertRaises(ValueError):corpus.refresh(root,first['id'],replacement,temp_root=root/'revisions')

    @unittest.skipUnless(LEAN.is_file(),'Actual Lean unavailable; capabilities.json records this skip')
    def test_interrupted_derived_state_requires_explicit_recovery(self):
        root=self.prepare();self.save_findings(root,formal=True)
        audit.check(SimpleNamespace(workspace=root,lean=LEAN,timeout=60))
        old_results=(root/'proof-results.json').read_bytes()
        original_hashes={d['id']:corpus.sha(root/d['original']) for d in corpus.inventory(root)}
        (root/'input/sources/first.md').write_text('Interrupted derived output; not original evidence.')
        with (root/'input/index.sqlite3').open('ab') as stream:stream.write(b'interrupted-index')
        with self.assertRaises(ValueError):corpus.resume(root)
        result=corpus.resume(root,recover=True)
        self.assertTrue(result['coverage']['complete'])
        self.assertIn('three',corpus.passage(root,'input/sources/first.md','S1')['quote'])
        self.assertEqual(original_hashes,{d['id']:corpus.sha(root/d['original']) for d in corpus.inventory(root)})
        self.assertFalse((root/'proof-results.json').exists())
        history=sorted((root/'history').glob('interrupted-*'))
        self.assertEqual(len(history),1)
        self.assertEqual((history[0]/'proof-results.json').read_bytes(),old_results)
        self.assertTrue((history[0]/'logs/lake-build.log').is_file())
        self.assertEqual(corpus.read(root/'invalidation.json')['affected_claims'],['C1','C2'])
        with self.assertRaisesRegex(ValueError,'revision-review'):corpus.assert_reviewable(root,[])
        corpus.save(root/'revision-review.json',{'revision_manifest_sha256':corpus.sha(root/'manifest.json'),'reviewed_claims':['C1','C2']})
        corpus.assert_reviewable(root,[])
        with self.assertRaises(audit.AuditError):audit.verified(root)

    def test_recovery_rejects_changed_original_snapshot(self):
        root=self.prepare();doc=self.document(root,'input/sources/first.md')
        (root/doc['original']).write_text('Changed protected original.')
        with self.assertRaisesRegex(ValueError,'modified original/catalog'):corpus.resume(root,recover=True)

    def test_recovery_rejects_changed_catalog(self):
        root=self.prepare()
        (root/'input/catalog.json').write_text('[]\n')
        with self.assertRaisesRegex(ValueError,'modified original/catalog'):corpus.resume(root,recover=True)

    def test_cli_search_show_and_resume_are_wired(self):
        root=self.prepare()
        commands=[['search','--workspace',str(root),'--query','amount','--limit','1','--offset','1'],
            ['show','--workspace',str(root),'--path','input/sources/first.md','--anchor','S1'],
            ['resume','--workspace',str(root)]]
        for index,arguments in enumerate(commands):
            command=[sys.executable,str(SCRIPTS/'audit.py'),*arguments]
            completed=subprocess.run(command,capture_output=True,text=True,timeout=60)
            corpus.save(self.case/f'cli-{index}.json',{'command':command,'returncode':completed.returncode,'stdout':completed.stdout,'stderr':completed.stderr})
            self.assertEqual(completed.returncode,0,completed.stderr)
            self.assertIsInstance(json.loads(completed.stdout),dict)

    def test_refresh_complete_dependency_map_selects_only_affected_claims(self):
        root=self.prepare();self.save_findings(root)
        first=self.document(root,'input/sources/first.md');second=self.document(root,'input/sources/second.md')
        corpus.save(root/'dependencies.json',{'coverage':'complete','claims':{'C1':[first['id']],'C2':[second['id']]}})
        replacement=self.case/'replacement.md';replacement.write_text('<a id="S1"></a>\nThe exact amount is four.\n')
        old_manifest=corpus.sha(root/'manifest.json')
        result=corpus.refresh(root,first['id'],replacement)
        revised=Path(result['workspace'])
        self.assertNotEqual(root,revised);self.assertEqual(corpus.sha(root/'manifest.json'),old_manifest)
        self.assertEqual(result['invalidation']['affected_claims'],['C1'])
        self.assertIn('three',corpus.passage(root,first['logical'],'S1')['quote'])
        self.assertIn('four',corpus.passage(revised,first['logical'],'S1')['quote'])
        with self.assertRaisesRegex(ValueError,'revision-review'):corpus.assert_reviewable(revised,[])
        corpus.save(revised/'revision-review.json',{'revision_manifest_sha256':corpus.sha(revised/'manifest.json'),'reviewed_claims':['C1']})
        corpus.assert_reviewable(revised,[])

    def test_refresh_accounts_for_support_search_dependencies(self):
        root=self.prepare();findings=self.save_findings(root)
        first=self.document(root,'input/sources/first.md');second=self.document(root,'input/sources/second.md')
        findings[0]['evidence']=[];findings[0]['semantic_state']='undetermined'
        annotate_synthetic_finding(findings[0])
        findings[0]['support_search']['inspected']=[{'path':first['logical'],'anchor':'S1'}]
        findings[0]['support_search'].pop('uninspected_reason',None)
        corpus.save(root/'findings.json',findings)
        audit.findings(root)
        replacement=self.case/'replacement.md';replacement.write_text('<a id="S1"></a>\nThe amount is four.\n')
        for include_search in (False,True):
            with self.subTest(include_search=include_search):
                corpus.save(root/'dependencies.json',{'coverage':'complete','claims':{
                    'C1':[first['id']] if include_search else [],'C2':[second['id']]}})
                result=corpus.refresh(root,first['id'],replacement)
                self.assertEqual(result['invalidation']['affected_claims'],['C1'] if include_search else ['C1','C2'])

    def test_refresh_propagates_transitive_claim_dependencies(self):
        self.report.write_text('\n\n'.join(f'<a id="R{i}"></a>\nClaim {i} reports three.' for i in range(1,5)))
        root=self.prepare()
        first=self.document(root,'input/sources/first.md');second=self.document(root,'input/sources/second.md')
        findings=[]
        for index in range(1,5):
            finding=self.finding(cid=f'C{index}') if index==1 else self.finding(
                cid=f'C{index}',path=second['logical'],anchor='S2',quote='The other exact amount is five.')
            finding.update(report_anchor=f'R{index}',report_quote=f'Claim {index} reports three.',semantic_state='undetermined')
            annotate_synthetic_finding(finding)
            if index in (2,3):finding['argument_audit']['claim_dependencies']=[f'C{index-1}']
            findings.append(finding)
        register_synthetic_report(root,findings);corpus.save(root/'findings.json',findings)
        audit.findings(root)
        corpus.save(root/'dependencies.json',{'coverage':'complete','claims':{
            'C1':[first['id']],'C2':[second['id']],'C3':[second['id']],'C4':[second['id']]}})
        replacement=self.case/'replacement.md';replacement.write_text('<a id="S1"></a>\nThe amount is four.\n')
        result=corpus.refresh(root,first['id'],replacement)
        self.assertEqual(result['invalidation']['affected_claims'],['C1','C2','C3'])
        self.assertIn('transitive claim dependents',result['invalidation']['reason'])

    def test_refresh_malformed_dependency_map_conservatively_invalidates_all(self):
        root=self.prepare();self.save_findings(root)
        first=self.document(root,'input/sources/first.md');second=self.document(root,'input/sources/second.md')
        correct={'C1':[first['id']],'C2':[second['id']]}
        malformed=[None, [], {'coverage':'complete','claims':[]},
            {'coverage':'complete','claims':{'C1':first['id'],'C2':[second['id']]}},
            {'coverage':'complete','claims':{'C1':[{'id':first['id']}],'C2':[second['id']]}},
            {'coverage':'complete','claims':correct,'global_documents':'not-a-list'},
            {'coverage':'complete','claims':correct,'global_documents':['unknown-document']},
            {'coverage':'complete','claims':correct,'global_documents':[first['id'],first['id']]}]
        replacement=self.case/'replacement.md';replacement.write_text('<a id="S1"></a>\nThe amount is four.\n')
        for index,mapping in enumerate(malformed):
            with self.subTest(case=index):
                corpus.save(root/'dependencies.json',mapping)
                result=corpus.refresh(root,first['id'],replacement)
                self.assertEqual(result['invalidation']['affected_claims'],['C1','C2'])
        (root/'dependencies.json').write_text('{invalid JSON')
        result=corpus.refresh(root,first['id'],replacement)
        self.assertEqual(result['invalidation']['affected_claims'],['C1','C2'])

    def test_refresh_missing_dependency_map_invalidates_all_claims(self):
        root=self.prepare();self.save_findings(root);first=self.document(root,'input/sources/first.md')
        replacement=self.case/'replacement.md';replacement.write_text('<a id="S1"></a>\nRevised source.\n')
        result=corpus.refresh(root,first['id'],replacement)
        self.assertEqual(result['invalidation']['affected_claims'],['C1','C2'])

    def test_dependency_map_omitting_explicit_evidence_falls_back(self):
        root=self.prepare();self.save_findings(root);first=self.document(root,'input/sources/first.md')
        corpus.save(root/'dependencies.json',{'coverage':'complete','claims':{'C1':[],'C2':[]}})
        replacement=self.case/'replacement.md';replacement.write_text('<a id="S1"></a>\nChanged.\n')
        result=corpus.refresh(root,first['id'],replacement)
        self.assertEqual(result['invalidation']['affected_claims'],['C1','C2'])

    @unittest.skipUnless(LEAN.is_file(),'Actual Lean unavailable; capabilities.json records this skip')
    def test_refresh_archives_actual_proofs_and_stale_quote_cannot_pass(self):
        root=self.prepare();self.save_findings(root,formal=True)
        audit.check(SimpleNamespace(workspace=root,lean=LEAN,timeout=60))
        old_results=(root/'proof-results.json').read_bytes();first=self.document(root,'input/sources/first.md')
        replacement=self.case/'replacement.md';replacement.write_text('<a id="S1"></a>\nThe exact amount is four.\n')
        revised=Path(corpus.refresh(root,first['id'],replacement)['workspace'])
        self.assertEqual((root/'proof-results.json').read_bytes(),old_results)
        self.assertFalse((revised/'proof-results.json').exists())
        self.assertEqual((revised/'history/parent/proof-results.json').read_bytes(),old_results)
        self.assertTrue((revised/'history/parent/logs/lake-build.log').exists())
        corpus.save(revised/'revision-review.json',{'revision_manifest_sha256':corpus.sha(revised/'manifest.json'),'reviewed_claims':['C1','C2']})
        with self.assertRaisesRegex(audit.AuditError,'evidence quote'):audit.findings(revised)


if __name__=='__main__':unittest.main()
