"""Checks that answer keys stay outside agent-readable folders and that copied key material is detected."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
import blindness  # noqa: E402

TEST_ROOT = ROOT / 'tmp'
TEST_ROOT.mkdir(exist_ok=True)
TEST_RUN = Path(tempfile.mkdtemp(prefix='evalsuite-blindness-tests-', dir=TEST_ROOT))

RATIONALE = 'The ledger total omits the freight line so the stated sum is short by forty units.'
LABEL = {'dossier': 'zz-dossier-id', 'variant': 'variantx', 'repeat': 2}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data, indent=2), encoding='utf-8')


class BlindnessTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + '-', dir=TEST_RUN))
        self.agent, self.keys = self.root / 'agent', self.root / 'keys'
        self.arena, self.records = self.root / 'arena', self.root / 'records'
        self.fixture = self.root / 'fixture'
        write(self.keys / 'demo.json', {'expectations': [{'claim_id': 'D-01', 'semantic_state': 'refuted',
                                                          'rationale': RATIONALE, 'variant': 'mixed'}]})
        write(self.fixture / 'report.md', '**D-01.** The total is 400 units.\n')
        write(self.fixture / 'report-correct.md', '**D-01.** The total is 360 units.\n')
        write(self.fixture / 'sources/ledger.md', '<a id="L01"></a>\nGoods 360. Freight 40.\n')
        write(self.fixture / 'sources/nested/note.md', '<a id="N01"></a>\nA note.\n')
        self.agent.mkdir()

    def stage(self, report='report-correct.md', **options):
        other = 'report.md' if report == 'report-correct.md' else 'report-correct.md'
        options.setdefault('withheld', [self.keys, self.fixture / other])
        return blindness.stage_case(self.fixture / report, self.fixture / 'sources', self.arena, self.records,
                                    LABEL, **options)

    def assert_nothing_staged(self):
        self.assertFalse(self.arena.exists() and any(self.arena.iterdir()))
        self.assertFalse(self.records.exists())

    def test_exposure_follows_real_paths_in_both_directions(self):
        inside = self.agent / 'hidden-keys'
        inside.mkdir()
        os.symlink(inside, self.root / 'alias')
        rows = blindness.exposure([self.agent], [self.keys, self.root / 'alias', self.root, self.root / 'absent'])
        self.assertEqual(rows[0]['readable_from'], [])
        self.assertEqual(rows[1]['readable_from'], [str(blindness.real(self.agent))])
        self.assertEqual(rows[2]['contains_agent_root'], [str(blindness.real(self.agent))])
        self.assertEqual((rows[3]['exists'], rows[3]['readable_from']), (False, []))
        blindness.assert_isolated([self.agent], [self.keys])
        for exposed in (self.root / 'alias', self.agent, self.root):
            with self.assertRaises(blindness.BlindnessError):
                blindness.assert_isolated([self.agent], [exposed])

    def test_a_root_written_in_another_letter_case_is_still_the_same_folder(self):
        mixed_case = self.root / 'CaseRoot'
        write(mixed_case / 'keys/demo.json', {'expectations': []})
        alias = self.root / 'caseroot'
        if not alias.exists():
            self.skipTest('This file system distinguishes letter case, so the alias does not exist')
        self.assertNotEqual(str(blindness.real(alias)), str(blindness.real(mixed_case)))
        self.assertTrue(blindness.within(mixed_case / 'keys', alias))
        self.assertTrue(blindness.within(mixed_case / 'keys/not-created-yet', alias))
        self.assertFalse(blindness.within(self.keys, alias))
        self.assertEqual(blindness.attest([alias], [mixed_case / 'keys'])['status'], 'exposed')
        self.assertEqual(blindness.attest([self.root / 'CASEROOT' / 'KEYS'], [mixed_case])['status'], 'exposed')
        with self.assertRaisesRegex(blindness.BlindnessError, 'readable from agent root'):
            blindness.seal_keys(self.keys, mixed_case / 'sealed', [alias])
        write(self.arena / 'old-keys/demo.json', {'expectations': []})
        with self.assertRaisesRegex(blindness.BlindnessError, 'readable from agent root'):
            self.stage(withheld=[self.root / 'ARENA' / 'old-keys'])
        self.assertEqual([p.name for p in self.arena.iterdir()], ['old-keys'])

    def test_attestation_needs_declared_roots_that_exist(self):
        self.assertEqual(blindness.attest([], [self.keys])['status'], 'not_attested')
        self.assertEqual(blindness.attest([self.root], [self.keys])['status'], 'exposed')
        result = blindness.attest([self.agent], [self.keys])
        self.assertEqual(result['status'], 'isolated_as_attested')
        self.assertIn('did not observe', result['basis'])
        misspelled = blindness.attest([self.agent, self.root / 'agnet'], [self.keys])
        self.assertEqual(misspelled['status'], 'not_attested')
        self.assertEqual(misspelled['missing_agent_roots'], [str(self.root / 'agnet')])
        self.assertEqual(blindness.attest([self.keys / 'demo.json'], [self.keys])['status'], 'not_attested')
        with self.assertRaisesRegex(blindness.BlindnessError, 'not an existing directory'):
            blindness.assert_isolated([self.root / 'agnet'], [self.keys])

    def test_sealed_keys_carry_one_canary_and_stay_outside_agent_roots(self):
        sealed = self.root / 'sealed'
        original = (self.keys / 'demo.json').read_bytes()
        manifest = blindness.seal_keys(self.keys, sealed, [self.agent])
        self.assertTrue(manifest['canary'].startswith(blindness.CANARY_PREFIX))
        self.assertEqual(json.loads((sealed / 'demo.json').read_text())['canary'], manifest['canary'])
        self.assertEqual(json.loads((sealed / blindness.MANIFEST_NAME).read_text()), manifest)
        self.assertEqual(manifest['files']['demo.json']['source_sha256'], blindness.sha(self.keys / 'demo.json'))
        self.assertEqual((self.keys / 'demo.json').read_bytes(), original)
        self.assertNotEqual(blindness.seal_keys(self.keys, self.root / 'sealed-2', [self.agent])['canary'],
                            manifest['canary'])
        with self.assertRaisesRegex(blindness.BlindnessError, 'new or empty'):
            blindness.seal_keys(self.keys, sealed, [self.agent])
        with self.assertRaisesRegex(blindness.BlindnessError, 'readable from agent root'):
            blindness.seal_keys(self.keys, self.agent / 'sealed', [self.agent])
        with self.assertRaisesRegex(blindness.BlindnessError, 'not an existing directory'):
            blindness.seal_keys(self.keys, self.root / 'sealed-4', [self.root / 'agnet'])
        with self.assertRaisesRegex(blindness.BlindnessError, 'at least one declared agent root'):
            blindness.seal_keys(self.keys, self.root / 'sealed-4', [])
        write(self.root / 'not-keys/readme.json', {'note': 'no expectations'})
        with self.assertRaisesRegex(blindness.BlindnessError, 'Not an answer key'):
            blindness.seal_keys(self.root / 'not-keys', self.root / 'sealed-3', [self.agent])
        for refused in (self.agent / 'sealed', self.root / 'sealed-3', self.root / 'sealed-4'):
            self.assertFalse(refused.exists())

    def test_staged_run_reveals_neither_dossier_nor_variant(self):
        write(self.root / 'skill/SKILL.md', '# Skill\n')
        write(self.root / 'skill/scripts/__pycache__/audit.cpython-312.pyc', 'bytecode')
        record = self.stage(brief=self.fixture / 'sources/nested/note.md', skill=self.root / 'skill')
        run = Path(record['arena_run'])
        self.assertEqual(run.parent, blindness.real(self.arena))
        self.assertRegex(run.name, r'^run-[0-9a-f]{16}$')
        staged = sorted(str(p.relative_to(run)) for p in run.rglob('*') if p.is_file())
        self.assertEqual(staged, ['case/brief.md', 'case/report.md', 'case/sources/ledger.md',
                                  'case/sources/nested/note.md', 'skill/SKILL.md'])
        # The fixture names its variants report.md and report-correct.md; the staged name is the same for both.
        self.assertEqual((run / 'case/report.md').read_bytes(), (self.fixture / 'report-correct.md').read_bytes())
        for path in [run] + list(run.rglob('*')):
            self.assertNotIn('correct', str(path.relative_to(self.arena)))
            self.assertNotIn('zz-dossier-id', str(path))
            self.assertNotIn('variantx', str(path))
            if path.is_file():
                self.assertNotIn(b'zz-dossier-id', path.read_bytes())
                self.assertNotIn(b'variantx', path.read_bytes())
        self.assertEqual(json.loads((self.records / 'index.json').read_text()), {run.name: LABEL})
        self.assertEqual(json.loads((self.records / f'{run.name}.json').read_text())['label'], LABEL)
        self.assertEqual(record['case_files']['case/report.md'], blindness.sha(self.fixture / 'report-correct.md'))
        with self.assertRaisesRegex(blindness.BlindnessError, 'Repeat 2 of this report is already staged'):
            self.stage(report='report.md')
        self.assertEqual([p.name for p in self.arena.iterdir()], [run.name])
        unnumbered = {'dossier': 'zz-dossier-id', 'variant': 'variantx'}
        second = blindness.stage_case(self.fixture / 'report.md', self.fixture / 'sources', self.arena,
                                      self.records, unnumbered, [self.keys])
        self.assertNotEqual(second['opaque_id'], record['opaque_id'])
        self.assertEqual(sorted(p.name for p in (Path(second['arena_run']) / 'case').iterdir()),
                         ['report.md', 'sources'])
        self.assertEqual(unnumbered, {'dossier': 'zz-dossier-id', 'variant': 'variantx'})
        other = blindness.stage_case(self.fixture / 'report.md', self.fixture / 'sources', self.arena,
                                     self.records, {'dossier': 'zz-dossier-id', 'variant': 'othervariant'},
                                     [self.keys])
        index = json.loads((self.records / 'index.json').read_text())
        self.assertEqual((index[second['opaque_id']]['repeat'], index[other['opaque_id']]['repeat']), (3, 1))
        self.assertEqual(len(index), 3)

    def test_staging_refuses_withheld_material_links_and_overlapping_folders(self):
        cases = []
        cases.append(('same bytes as withheld', lambda: write(self.fixture / 'sources/answers.json',
                                                              (self.keys / 'demo.json').read_text())))
        cases.append(('same bytes as withheld', lambda: write(self.fixture / 'sources/other.md',
                                                              (self.fixture / 'report.md').read_text())))
        cases.append(('answer-key canary', lambda: write(self.fixture / 'sources/leak.md',
                                                         'see ' + blindness.CANARY_PREFIX + 'abc')))
        cases.append(('Symbolic link', lambda: os.symlink(self.keys / 'demo.json',
                                                          self.fixture / 'sources/link.json')))
        for message, plant in cases:
            plant()
            with self.assertRaisesRegex(blindness.BlindnessError, message):
                self.stage()
            for name in ('answers.json', 'other.md', 'leak.md', 'link.json'):
                planted = self.fixture / 'sources' / name
                if planted.is_symlink() or planted.exists():
                    planted.unlink()
        arguments = (self.fixture / 'report.md', self.fixture / 'sources', self.arena)
        with self.assertRaisesRegex(blindness.BlindnessError, 'readable from agent root'):
            blindness.stage_case(*arguments, self.arena / 'records', LABEL, [self.keys])
        with self.assertRaisesRegex(blindness.BlindnessError, 'Withheld path does not exist'):
            self.stage(withheld=[self.root / 'kyes'])
        with self.assertRaisesRegex(blindness.BlindnessError, 'contains agent root'):
            self.stage(protected=[self.root])
        with self.assertRaisesRegex(blindness.BlindnessError, 'dossier and variant'):
            blindness.stage_case(*arguments, self.records, {'dossier': 'zz'}, [self.keys])
        with self.assertRaisesRegex(blindness.BlindnessError, 'needs the withheld answer material'):
            blindness.stage_case(*arguments, self.records, LABEL, [])
        empty = self.root / 'empty-sources'
        empty.mkdir()
        with self.assertRaisesRegex(blindness.BlindnessError, 'No source files'):
            blindness.stage_case(self.fixture / 'report.md', empty, self.arena, self.records, LABEL, [self.keys])
        self.assert_nothing_staged()

    def test_a_skill_carrying_key_bytes_a_canary_or_a_link_removes_the_run(self):
        write(self.root / 'skill/SKILL.md', '# Skill\n')
        plants = (('same bytes as withheld', lambda p: write(p, (self.keys / 'demo.json').read_text())),
                  ('answer-key canary', lambda p: write(p, 'ref ' + blindness.CANARY_PREFIX + 'abc')),
                  ('symbolic link', lambda p: os.symlink(self.keys / 'demo.json', p)))
        planted = self.root / 'skill/references/extra.json'
        planted.parent.mkdir()
        for message, plant in plants:
            plant(planted)
            with self.assertRaisesRegex(blindness.BlindnessError, message):
                self.stage(skill=self.root / 'skill')
            planted.unlink()
            self.assertEqual(list(self.arena.iterdir()), [])
            self.assertFalse(self.records.exists())
        self.assertTrue((self.keys / 'demo.json').is_file())

    def test_scan_flags_copied_rationale_but_not_wording_from_supplied_inputs(self):
        key_rows = json.loads((self.keys / 'demo.json').read_text())['expectations']
        run = self.root / 'run'
        write(run / 'input/report.md', '**D-01.** The total is 400 units.\n')
        write(run / 'input/sources/ledger.md', 'Goods 360. Freight 40.\n')
        write(run / 'findings.json', [{'claim_id': 'D-01', 'reasoning': ['Independent: 360 plus 40 is 400.',
                                                                         'The ledger total omits the freight line.']}])
        clean = blindness.scan_run(run, key_rows)
        self.assertEqual((clean['canary_hits'], clean['verbatim_overlaps']), ([], []))
        self.assertEqual((clean['authored_files_scanned'], clean['supplied_basis']), (1, 'folder_names'))
        write(run / 'findings.json', [{'claim_id': 'D-01', 'reasoning': ['First,\n' + RATIONALE.upper()]}])
        # Seven shared words stay below the eight-word threshold; the full sentence does not.
        write(run / 'model-notes.md', 'Note: a stated sum is short by forty units, as expected.\n')
        copied = blindness.scan_run(run, key_rows)['verbatim_overlaps']
        self.assertEqual(copied, [{'claim_id': 'D-01', 'variant': 'mixed', 'file': 'findings.json', 'words': 16,
                                   'text': RATIONALE.lower().rstrip('.')}])
        write(run / 'input/sources/ledger.md', 'Goods 360. Freight 40. ' + RATIONALE + '\n')
        self.assertEqual(blindness.scan_run(run, key_rows)['verbatim_overlaps'], [])
        write(run / 'source-ledger.jsonl', json.dumps({'text': 'unrelated'}) + '\n')
        self.assertEqual(blindness.scan_run(run, key_rows)['authored_files_scanned'], 2)

    def test_a_planted_input_hides_nothing_when_the_stage_record_is_used(self):
        key_rows = json.loads((self.keys / 'demo.json').read_text())['expectations']
        run = self.root / 'run'
        write(run / 'case/report.md', '**D-01.** The total is 400 units.\n')
        write(run / 'case/sources/ledger.md', 'Goods 360. Freight 40.\n')
        staged = {hashlib.sha256(p.read_bytes()).hexdigest() for p in (run / 'case').rglob('*') if p.is_file()}
        write(run / 'findings.json', [{'claim_id': 'D-01', 'reasoning': [RATIONALE]}])
        write(run / 'case/scratch-notes.md', RATIONALE)
        self.assertEqual(blindness.scan_run(run, key_rows)['verbatim_overlaps'], [])
        trusted = blindness.scan_run(run, key_rows, supplied_digests=staged)
        self.assertEqual(trusted['supplied_basis'], 'stage_record')
        self.assertEqual(sorted(o['file'] for o in trusted['verbatim_overlaps']),
                         ['case/scratch-notes.md', 'findings.json'])
        write(run / 'case/sources/ledger.md', 'Goods 360. Freight 40. ' + RATIONALE)
        self.assertEqual(len(blindness.scan_run(run, key_rows, supplied_digests=staged)['verbatim_overlaps']), 3)

    def test_scan_reads_any_text_file_and_finds_canaries_in_every_file(self):
        key_rows = [{'claim_id': 'D-02', 'variant': 'correct',
                     'rationale': 'alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi '
                                  'omicron pi rho sigma tau upsilon'}]
        run = self.root / 'run'
        write(run / 'input/report.md', 'A report.\n')
        write(run / 'audit-report.xhtml', '<p>alpha beta gamma delta epsilon zeta eta theta iota. Then unrelated '
                                          'words. lambda mu nu xi omicron pi rho sigma tau.</p>\n')
        (run / 'render.bin').write_bytes(b'\xff\xfe alpha beta gamma delta epsilon zeta eta theta iota')
        write(run / 'logs/tool.bin', 'x' + blindness.CANARY_PREFIX + 'deadbeef')
        write(run / '.lake/build/cache.md', blindness.CANARY_PREFIX + ' alpha beta gamma delta epsilon zeta eta theta')
        result = blindness.scan_run(run, key_rows)
        self.assertEqual(result['canary_hits'], ['.lake/build/cache.md', 'logs/tool.bin'])
        self.assertEqual([(o['file'], o['words'], o['text']) for o in result['verbatim_overlaps']],
                         [('audit-report.xhtml', 9, 'alpha beta gamma delta epsilon zeta eta theta iota'),
                          ('audit-report.xhtml', 9, 'lambda mu nu xi omicron pi rho sigma tau')])
        self.assertEqual(blindness.scan_run(run, key_rows, min_words=10)['verbatim_overlaps'], [])

    def test_command_line_reports_exposure_and_keeps_the_label_out_of_stage_output(self):
        script = [sys.executable, str(ROOT / 'evalsuite/blindness.py')]
        exposed = subprocess.run(script + ['exposure', '--agent-root', str(self.root), '--protected', str(self.keys)],
                                 capture_output=True, text=True)
        self.assertEqual(exposed.returncode, 1)
        self.assertEqual(json.loads(exposed.stdout)['status'], 'exposed')
        isolated = subprocess.run(script + ['exposure', '--agent-root', str(self.agent), '--protected',
                                            str(self.keys)], capture_output=True, text=True)
        self.assertEqual(isolated.returncode, 0, isolated.stderr)
        absent = subprocess.run(script + ['exposure', '--agent-root', str(self.root / 'agnet'), '--protected',
                                          str(self.keys)], capture_output=True, text=True)
        self.assertEqual(absent.returncode, 1)
        self.assertEqual(json.loads(absent.stdout)['status'], 'not_attested')
        staged = subprocess.run(script + ['stage', '--report', str(self.fixture / 'report.md'), '--sources',
                                          str(self.fixture / 'sources'), '--arena', str(self.arena), '--records',
                                          str(self.records), '--dossier', 'zz-dossier-id', '--variant', 'variantx',
                                          '--withheld', str(self.keys)], capture_output=True, text=True)
        self.assertEqual(staged.returncode, 0, staged.stderr)
        self.assertNotIn('zz-dossier-id', staged.stdout)
        self.assertNotIn('variantx', staged.stdout)
        refused = subprocess.run(script + ['seal', '--source', str(self.keys), '--dest', str(self.agent / 'k'),
                                           '--agent-root', str(self.agent)], capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('Blindness check failed', refused.stderr)


if __name__ == '__main__':
    unittest.main()
