"""Retained-proof planning and run checks. No Lean, model or sandbox execution: run uses a stand-in executor."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
import recheck
import recheck_sandbox as sandbox

WORK = ROOT / 'tmp/recheck'

(ROOT / 'tmp').mkdir(exist_ok=True)
TEST_RUN = Path(tempfile.mkdtemp(prefix='recheck-planning-tests-', dir=ROOT / 'tmp'))
REPORT = '<a id="R02"></a>\n**C02.** Two services qualify.\n'
LEDGER = {'R02': '**C02.** Two services qualify.'}


def finding(**changes):
    row = {'claim_id': 'C02', 'report_anchor': 'R02', 'semantic_state': 'supported',
           'formal_status': 'proved', 'argument_status': 'sufficient', 'evidence': [],
           'reasoning': ['The two services meet the source rule.'], 'explanation': ''}
    return dict(row, **changes)


def workspace(target='True', relation='supports'):
    return {
        'agent/audit/claims.md': b'| Claim ID | Report anchor | Disposition |\n| --- | --- | --- |\n| C-02 | R02 | audited |\n',
        'agent/audit/input/report.md': REPORT.encode(),
        'agent/audit/findings/C-02.md': (
            '## Report anchor\nR02\n\n## Formal status\nproved\n\n'
            '## Semantic state\nsupported\n\n## Proofs\n### Main\n'
            f'- Module: Audit\n- Theorem: serviceCount_eq_two\n- Target: {target}\n- Relation: {relation}\n').encode(),
        'agent/audit/models/Audit.lean': b'theorem serviceCount_eq_two : True := by trivial\n',
    }


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + '-', dir=TEST_RUN))

    def write(self, path, data):
        path = self.root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
        return path

    def reject(self, reason, function, *args):
        with self.assertRaises(recheck.RecheckError) as caught:
            function(*args)
        self.assertEqual(caught.exception.reason, reason)

    def link(self, blobs, **changes):
        return recheck.link_claim(finding(**changes), LEDGER, blobs)

    def test_all_six_link_categories(self):
        named = {'agent/run/Audit.lean': b'theorem C02 : True := by trivial\n'}
        explicit = {'agent/runs/Service.lean': b'theorem eligible : True := by trivial\n'}
        cases = [({}, {}, 'no_lean'),
                 ({'agent/models/A.lean': b'def auxiliary : Nat := 2\n'}, {}, 'unlinked_lean'),
                 (named, {}, 'name_match'),
                 (explicit, {'explanation': 'Service.lean proves eligible.'}, 'explicit_reference'),
                 (workspace(), {}, 'workspace_record'),
                 (named, {'explanation': 'Missing.lean proves C02.'}, 'ambiguous_or_stale')]
        for blobs, changes, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.link(blobs, **changes)['category'], expected)

    def test_registry_alias_requires_matching_supplied_anchor_and_report(self):
        blobs = workspace()
        linked = self.link(blobs)
        self.assertEqual(linked['category'], 'workspace_record')
        self.assertEqual(linked['proofs'][0]['theorem'], 'serviceCount_eq_two')
        blobs['agent/audit/input/report.md'] = REPORT.replace('Two', 'Three').encode()
        self.assertEqual(self.link(blobs)['category'], 'ambiguous_or_stale')
        self.assertEqual(self.link({'agent/models/A.lean': b'theorem C02 : True := by trivial\n'},
                                   claim_id='C-02')['category'], 'unlinked_lean')

    def test_conflicting_workspace_blocks_name_fallback(self):
        blobs = workspace(relation='auxiliary')
        blobs['agent/run/A.lean'] = b'theorem C02 : True := by trivial\n'
        self.assertEqual(self.link(blobs)['category'], 'ambiguous_or_stale')

    def test_duplicate_registry_ids_and_multiple_required_proofs(self):
        blobs = workspace()
        blobs['agent/audit/claims.md'] += b'| C-02 | R02 | audited |\n'
        self.assertEqual(self.link(blobs)['category'], 'ambiguous_or_stale')
        blobs = workspace()
        blobs['agent/audit/models/Audit.lean'] += b'theorem additional : 2 = 2 := by rfl\n'
        blobs['agent/audit/findings/C-02.md'] += (
            b'\n### Additional\n- Module: Audit\n- Theorem: additional\n'
            b'- Target: 2 = 2\n- Relation: supports\n')
        result = self.link(blobs)
        self.assertEqual(result['category'], 'workspace_record')
        self.assertEqual({p['theorem'] for p in result['proofs']}, {'additional', 'serviceCount_eq_two'})

    def test_primary_and_auxiliary_workspace_proofs_preserve_required_relations(self):
        for formal, semantic, primary in (('proved', 'supported', 'supports'),
                                          ('refuted', 'refuted', 'refutes')):
            with self.subTest(formal=formal):
                blobs = workspace(relation=primary)
                path = 'agent/audit/findings/C-02.md'
                blobs[path] = blobs[path].replace(b'\nproved\n', f'\n{formal}\n'.encode()).replace(
                    b'\nsupported\n', f'\n{semantic}\n'.encode())
                blobs['agent/audit/models/Audit.lean'] += b'theorem premise : 2 = 2 := by rfl\n'
                blobs[path] += (b'\n### Premise\n- Module: Audit\n- Theorem: premise\n'
                                b'- Target: 2 = 2\n- Relation: auxiliary\n')
                result = self.link(blobs, formal_status=formal, semantic_state=semantic)
                self.assertEqual(result['category'], 'workspace_record')
                self.assertEqual({p['theorem']: p['relation'] for p in result['proofs']},
                                 {'serviceCount_eq_two': primary, 'premise': 'auxiliary'})
                proofs = [{'id': p['theorem'], 'outcome': 'pass', 'reason': None} for p in result['proofs']]
                next(p for p in proofs if p['id'] == 'premise').update(outcome='fail', reason='target_mismatch')
                self.assertEqual(recheck.claim_outcome([p['id'] for p in proofs], proofs), ('fail', 'target_mismatch'))

    def test_auxiliary_only_and_opposite_workspace_relations_are_ambiguous(self):
        for formal, semantic, primary, opposite in (('proved', 'supported', 'supports', 'refutes'),
                                                    ('refuted', 'refuted', 'refutes', 'supports')):
            for relation in ('auxiliary', opposite):
                with self.subTest(formal=formal, relation=relation):
                    blobs = workspace(relation=relation if relation == 'auxiliary' else primary)
                    path = 'agent/audit/findings/C-02.md'
                    blobs[path] = blobs[path].replace(b'\nproved\n', f'\n{formal}\n'.encode()).replace(
                        b'\nsupported\n', f'\n{semantic}\n'.encode())
                    if relation == opposite:
                        blobs['agent/audit/models/Audit.lean'] += b'theorem conflicting : True := by trivial\n'
                        blobs[path] += (f'\n### Conflicting\n- Module: Audit\n- Theorem: conflicting\n'
                                        f'- Target: True\n- Relation: {opposite}\n').encode()
                    result = self.link(blobs, formal_status=formal, semantic_state=semantic)
                    self.assertEqual(result['category'], 'ambiguous_or_stale')
                    self.assertEqual(result['proofs'], [])

    def test_identical_snapshots_deduplicate_and_keep_paths(self):
        data = b'theorem C02 : True := by trivial\n'
        blobs = {'agent/run/models/A.lean': data, 'agent/runs/models/A.lean': data}
        result = self.link(blobs)
        self.assertEqual(result['category'], 'name_match')
        self.assertEqual(len(result['proofs']), 1)
        self.assertEqual(result['proofs'][0]['source_paths'], sorted(blobs))
        expected_hashes = {path: recheck.digest(data) for path in blobs}
        candidates = result['proofs']
        for _ in range(3):
            candidates = recheck.deduplicate(candidates)
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]['source_paths'], sorted(blobs))
            self.assertEqual(candidates[0]['source_hashes'], expected_hashes)
        blobs['agent/runs/models/A.lean'] = b'theorem C02 : 2 = 2 := by rfl\n'
        self.assertEqual(self.link(blobs)['category'], 'ambiguous_or_stale')

    def test_current_sources_win_over_unassociated_history(self):
        old = 'agent/audit/proof-attempts/old/models/A.lean'
        blobs = {old: b'theorem C02 : 2 = 2 := by rfl\n'}
        self.assertEqual(self.link(blobs)['category'], 'ambiguous_or_stale')
        blobs['agent/audit/models/A.lean'] = b'theorem C02 : True := by trivial\n'
        self.assertEqual(self.link(blobs)['proofs'][0]['target'], 'True')
        result = self.link(blobs, explanation=f'{old} establishes C02.')
        self.assertEqual(result['category'], 'explicit_reference')
        self.assertEqual(result['proofs'][0]['target'], '2 = 2')

    def test_archived_phase_registry_does_not_conflict_with_active_workspace(self):
        blobs = workspace()
        registry = blobs['agent/audit/claims.md']
        archived = f'agent/work/audit/phase-artifacts/{recheck.digest(registry)}/claims.md'
        blobs[archived] = registry
        result = self.link(blobs)
        self.assertEqual(result['category'], 'workspace_record')
        self.assertEqual(result['proofs'][0]['theorem'], 'serviceCount_eq_two')

    def test_current_model_wins_over_differing_phase_artifact(self):
        historical = b'theorem C02 : 2 = 2 := by rfl\n'
        archived = f'agent/audit/phase-artifacts/{recheck.digest(historical)}/Audit.lean'
        blobs = {archived: historical,
                 'agent/audit/models/Audit.lean': b'theorem C02 : True := by trivial\n'}
        result = self.link(blobs)
        self.assertEqual(result['category'], 'name_match')
        self.assertEqual(result['proofs'][0]['target'], 'True')
        self.assertEqual(result['proofs'][0]['source_paths'], ['agent/audit/models/Audit.lean'])

    def test_unassociated_phase_artifact_alone_is_ambiguous(self):
        historical = b'theorem C02 : 2 = 2 := by rfl\n'
        archived = f'agent/audit/phase-artifacts/{recheck.digest(historical)}/Audit.lean'
        result = self.link({archived: historical})
        self.assertEqual(result['category'], 'ambiguous_or_stale')
        self.assertEqual(result['proofs'], [])

    def test_exact_phase_artifact_reference_requires_matching_source_hash(self):
        historical = b'theorem C02 : 2 = 2 := by rfl\n'
        for source_hash, expected in ((recheck.digest(historical), 'explicit_reference'),
                                      ('0' * 64, 'ambiguous_or_stale')):
            with self.subTest(source_hash=source_hash):
                archived = f'agent/audit/phase-artifacts/{source_hash}/Audit.lean'
                result = self.link({archived: historical}, explanation=f'{archived} proves C02.')
                self.assertEqual(result['category'], expected)
                if expected == 'explicit_reference':
                    self.assertEqual(result['proofs'][0]['target'], '2 = 2')
                else:
                    self.assertEqual(result['proofs'], [])

    def test_inconsistent_reported_states_block_name_fallback(self):
        blobs = {'agent/models/A.lean': b'theorem C02 : True := by trivial\n'}
        for formal, semantic in (('proved', 'refuted'), ('refuted', 'supported')):
            with self.subTest(formal=formal, semantic=semantic):
                result = self.link(blobs, formal_status=formal, semantic_state=semantic)
                self.assertEqual(result['category'], 'ambiguous_or_stale')
                self.assertEqual(result['proofs'], [])

    def test_fallback_rejects_binders_and_preserves_refutation_target(self):
        self.assertEqual(self.link({'agent/A.lean': b'theorem C02 (x : Nat) : x = x := by rfl\n'})['category'],
                         'ambiguous_or_stale')
        result = self.link({'agent/A.lean': b'theorem C02_count : 3 = 3 := by rfl\n'},
                           formal_status='refuted', semantic_state='refuted')
        self.assertEqual(result['proofs'][0]['target'], '3 = 3')
        self.assertEqual(result['proofs'][0]['relation'], 'refutes')

    def test_explicit_path_disambiguates_same_filename(self):
        blobs = {'agent/one/A.lean': b'theorem count : True := by trivial\n',
                 'agent/two/A.lean': b'theorem count : 2 = 2 := by rfl\n'}
        self.assertEqual(self.link(blobs, explanation='A.lean proves count.')['category'], 'ambiguous_or_stale')
        self.assertEqual(self.link(blobs, explanation='agent/two/A.lean proves count.')['proofs'][0]['target'], '2 = 2')

    def test_fallback_preserves_string_literals_in_target(self):
        for literal in ('"ok"', '"a  b"', '":="'):
            with self.subTest(literal=literal):
                target = f'{literal} = {literal}'
                result = self.link({'agent/A.lean': f'theorem C02 : {target} := by rfl\n'.encode()})
                self.assertEqual(result['category'], 'name_match')
                self.assertEqual(result['proofs'][0]['target'], target)

    def test_unresolved_required_explicit_reference_blocks_resolved_reference(self):
        blobs = {'agent/A.lean': b'theorem present : True := by trivial\n'}
        result = self.link(blobs, explanation='A.lean proves present and Missing.lean proves absent; both are required.')
        self.assertEqual(result['category'], 'ambiguous_or_stale')
        self.assertEqual(result['proofs'], [])

    def test_conflicting_explicit_current_and_historical_sources_are_ambiguous(self):
        current = 'agent/audit/models/A.lean'
        historical = 'agent/audit/proof-attempts/old/models/A.lean'
        blobs = {current: b'theorem count : True := by trivial\n',
                 historical: b'theorem count : 2 = 2 := by rfl\n'}
        result = self.link(blobs, explanation=f'{current} and {historical} both prove count.')
        self.assertEqual(result['category'], 'ambiguous_or_stale')
        self.assertEqual(result['proofs'], [])

    def test_safe_reads_reject_symlink_fifo_hardlink_and_traversal(self):
        original = self.write('source.json', b'{}')
        (self.root / 'link.json').symlink_to(original)
        os.link(original, self.root / 'hard.json')
        os.mkfifo(self.root / 'pipe.json')
        (self.root / 'directory').symlink_to(self.root, target_is_directory=True)
        with recheck.SafeTree(self.root) as tree:
            for path in ('link.json', 'hard.json', 'pipe.json', 'directory/source.json', '../source.json', '/source.json', 'agent-home/a'):
                with self.subTest(path=path):
                    self.reject('unsafe_path', tree.read, path)

    def test_size_duplicate_fields_and_json_depth_limits(self):
        self.write('large.json', b' ' * (recheck.FILE_LIMIT + 1))
        with recheck.SafeTree(self.root) as tree:
            self.reject('input_limit', tree.read, 'large.json')
        self.reject('invalid_metadata', recheck.parse_json, b'{"x": 1, "x": 2}')
        self.reject('input_limit', recheck.parse_json, b'[' * 33 + b'0' + b']' * 33)
        self.assertEqual(recheck.parse_json(b'{"text":"[[[["}'), {'text': '[[[['})

    def test_capture_ignores_home_and_binary_artifacts(self):
        for name in ('run.json', 'findings.json', 'input/report-ledger.json'):
            self.write('review/' + name, b'{}')
        self.write('review/agent/run/models/A.lean', b'theorem C02 : True := by trivial\n')
        self.write('review/agent/run/models/A.olean', b'old binary')
        self.write('review/agent/agent-home/private.json', b'not valid JSON')
        with recheck.SafeTree(self.root) as tree:
            blobs = recheck.capture_review(tree, 'review')
        self.assertIn('agent/run/models/A.lean', blobs)
        self.assertFalse(any('agent-home' in path or path.endswith('.olean') for path in blobs))

    def test_retained_target_is_evidence_only_and_strictly_parsed(self):
        good = b'import Audit\n#check @C02\n#print axioms C02\nexample : (True) := C02\n'
        self.assertEqual(recheck.parse_retained_target(good)['target'], 'True')
        self.assertIsNone(recheck.parse_retained_target(good + b'#eval IO.println "injected"\n'))
        self.assertEqual(recheck.source_groups({'agent/checks/Target001.lean': good}), {})

    def review_blobs(self):
        run = {'unit': 'review-1', 'dossier': 'demo', 'variant': 'mixed', 'repeat': 1,
               'opaque_id': 'run-1', 'arm': 'skill-lean', 'status': 'scored',
               'ended': 'exit_0', 'finished_at': '2026-11-12T00:00:00Z'}
        return {'run.json': recheck.canonical(run), 'findings.json': recheck.canonical([finding()]),
                'input/report-ledger.json': recheck.canonical(LEDGER), **workspace()}

    def campaign(self):
        key = recheck.canonical({'expectations': []})
        manifest = recheck.canonical({'schema_version': 1, 'canary': 'fixture',
                                     'files': {'demo.json': {'sealed_sha256': recheck.digest(key)}}})
        self.write('campaign/lean/p2lean1/keys-sealed/demo.json', key)
        self.write('campaign/lean/p2lean1/keys-sealed/KEYS-MANIFEST.json', manifest)
        base = 'campaign/runs/p2lean1/X3/lean/out/'
        self.write(base + 'campaign.json', recheck.canonical({'keys_manifest_sha256': recheck.digest(manifest)}))
        self.write(base + 'skill-lean/records/index.json', recheck.canonical({
            'run-1': {'dossier': 'demo', 'variant': 'mixed', 'repeat': 1}}))
        for path, data in self.review_blobs().items():
            self.write(base + 'skill-lean/runs/run-1/' + path, data)
        return self.root / 'campaign', base + 'skill-lean/runs/run-1/'

    def test_inventory_freezes_roster_hashes_and_legacy_profile(self):
        campaign, review = self.campaign()
        frozen = recheck.inventory(campaign, ['X3'], ['p2lean1'], self.root / 'inventory')
        self.assertTrue(frozen['complete'])
        self.assertEqual(len(frozen['reviews']), 1)
        record = frozen['reviews'][0]
        self.assertEqual(record['reported'], 1)
        self.assertEqual(record['registration_profile'], 'legacy')
        self.assertEqual(record['input_hashes']['findings.json'], recheck.digest(self.review_blobs()['findings.json']))
        before = (self.root / 'inventory/inventory.json').read_bytes()
        self.write(review + 'findings.json', b'[]')
        self.assertEqual((self.root / 'inventory/inventory.json').read_bytes(), before)

    def test_inventory_excludes_partial_and_quarantines_unknown_metadata(self):
        campaign, review = self.campaign()
        run = json.loads(self.review_blobs()['run.json'])
        run['finished_at'] = None
        self.write(review + 'run.json', recheck.canonical(run))
        result = recheck.inventory(campaign, ['X3'], ['p2lean1'], self.root / 'partial')
        self.assertFalse(result['complete'])
        self.assertEqual(result['excluded'][0]['reason'], 'unfinished')
        run['finished_at'] = '2026-11-12T00:00:00Z'
        run['unsupported_field'] = True
        self.write(review + 'run.json', recheck.canonical(run))
        result = recheck.inventory(campaign, ['X3'], ['p2lean1'], self.root / 'quarantine')
        self.assertFalse(result['complete'])
        self.assertEqual(result['quarantined'][0]['reason'], 'invalid_metadata')

    def test_metadata_rejects_duplicate_findings_and_unknown_fields(self):
        blobs = self.review_blobs()
        blobs['findings.json'] = recheck.canonical([finding(), finding()])
        self.reject('invalid_metadata', recheck.review_metadata, blobs)
        blobs['findings.json'] = recheck.canonical([finding(unsupported=True)])
        self.reject('invalid_metadata', recheck.review_metadata, blobs)

    def test_inventory_quarantines_malformed_run_shapes_and_status_types(self):
        campaign, review = self.campaign()
        malformed = [[], dict(json.loads(self.review_blobs()['run.json']), status=[])]
        for index, run in enumerate(malformed):
            with self.subTest(run=run):
                self.write(review + 'run.json', recheck.canonical(run))
                result = recheck.inventory(campaign, ['X3'], ['p2lean1'], self.root / f'quarantine-{index}')
                self.assertFalse(result['complete'])
                self.assertEqual(result['reviews'], [])
                self.assertEqual(len(result['quarantined']), 1)
                self.assertEqual(result['quarantined'][0]['reason'], 'invalid_metadata')

    def test_metadata_rejects_unhashable_finding_fields(self):
        for field in ('claim_id', 'report_anchor', 'semantic_state', 'formal_status'):
            for invalid in ([], {}):
                with self.subTest(field=field, invalid=invalid):
                    blobs = self.review_blobs()
                    blobs['findings.json'] = recheck.canonical([finding(**{field: invalid})])
                    self.reject('invalid_metadata', recheck.review_metadata, blobs)

    def test_capture_rejects_symlink_and_directory_depth(self):
        for name in ('run.json', 'findings.json', 'input/report-ledger.json'):
            self.write('review/' + name, b'{}')
        self.write('review/agent/run/model.lean', b'theorem C02 : True := by trivial\n')
        (self.root / 'review/agent/run/link.md').symlink_to(self.root / 'outside.md')
        with recheck.SafeTree(self.root) as tree:
            self.reject('unsafe_path', recheck.capture_review, tree, 'review')
        # A separate fixture retains both failing trees for inspection.
        for name in ('run.json', 'findings.json', 'input/report-ledger.json'):
            self.write('deep/' + name, b'{}')
        self.write('deep/agent/' + 'nested/' * 34 + 'record.md', b'deep')
        with recheck.SafeTree(self.root) as tree:
            self.reject('input_limit', recheck.capture_review, tree, 'deep')

    def test_capture_enforces_source_count_and_total_byte_budgets(self):
        for name in ('run.json', 'findings.json', 'input/report-ledger.json'):
            self.write('review/' + name, b'{}')
        for index in range(257):
            self.write(f'review/agent/models/A{index:03d}.lean', b'-- retained source\n')
        with recheck.SafeTree(self.root) as tree:
            self.reject('input_limit', recheck.capture_review, tree, 'review')
        # Lowering the aggregate limit isolates its check from per-file/count limits.
        with mock.patch.object(recheck, 'TOTAL_LIMIT', 5), recheck.SafeTree(self.root) as tree:
            self.reject('input_limit', recheck.capture_review, tree, 'review')

    def test_safe_read_detects_bytes_changing_between_reads(self):
        self.write('record.json', b'{}')
        with recheck.SafeTree(self.root) as tree, mock.patch.object(recheck.os, 'read', side_effect=[b'{}', b'', b'[]', b'']):
            self.reject('source_changed', tree.read, 'record.json')

    def stage(self, blobs, name='proof'):
        candidate = self.link(blobs)['proofs'][0]
        return recheck.stage_proof(candidate, 'C02', self.root / 'jobs', name)

    def test_stage_only_sources_and_executor_shaped_job(self):
        blobs = workspace()
        blobs.update({'agent/audit/models/Audit.olean': b'old object',
                      'agent/audit/models/lakefile.toml': b'untrusted config',
                      'agent/audit/models/lakefile.lean': b'import Lake\nopen Lake DSL\npackage retained\n',
                      'agent/audit/checks/Target001.lean': b'#eval IO.println "untrusted"\n'})
        proof, job = self.stage(blobs)
        self.assertIsNotNone(job)
        self.assertEqual(proof['outcome'], 'not_run')
        job_root = ROOT / proof['job_path']
        sources = job_root.parent / 'sources'
        self.assertEqual([p.relative_to(sources).as_posix() for p in sources.rglob('*') if p.is_file()], ['Audit.lean'])
        self.assertEqual((sources / 'Audit.lean').read_bytes(), blobs['agent/audit/models/Audit.lean'])
        # The executor writes its own checks; nothing generated or retained is staged beside the sources.
        self.assertEqual(sorted(p.name for p in job_root.parent.iterdir()), ['job.json', 'sources'])
        self.assertEqual(job['sources'], [{'module': 'Audit', 'path': proof['staged_sources'] + '/Audit.lean',
                                           'sha256': recheck.digest(blobs['agent/audit/models/Audit.lean'])}])
        self.assertEqual(job['proofs'], [{'module': 'Audit', 'theorem': 'serviceCount_eq_two', 'target': 'True',
                                          'relation': 'supports', 'claim_id': 'C02'}])
        self.assertEqual(recheck.digest(job_root.read_bytes()), proof['job_sha256'])
        for stale in ('argv', 'checks', 'limits', 'checks_required', 'expected_objects'):
            self.assertNotIn(stale, job)
        self.assertTrue(job['requires_gate'])
        self.assertEqual(job['execution_status'], 'not_run')

    def test_target_injection_and_forbidden_source_are_rejected_before_jobs(self):
        blobs = workspace(target='True) := by trivial; #eval IO.println "injected" --')
        proof, job = self.stage(blobs, 'target')
        self.assertIsNone(job)
        self.assertEqual(proof['outcome'], 'filter_rejected')
        for index, forbidden in enumerate(('sorry', '#eval IO.println "x"', 'initialize IO.println "x"')):
            blobs = workspace()
            blobs['agent/audit/models/Audit.lean'] += ('\n' + forbidden + '\n').encode()
            proof, job = self.stage(blobs, f'source-{index}')
            self.assertIsNone(job)
            self.assertEqual(proof['outcome'], 'filter_rejected')

    def test_dependency_order_missing_cycle_and_reserved_shadow(self):
        blobs = {'agent/models/A.lean': b'import B\ntheorem C02 : True := B.ok\n',
                 'agent/models/B.lean': b'namespace B\ntheorem ok : True := by trivial\nend B\n'}
        proof, job = self.stage(blobs, 'ordered')
        self.assertIsNotNone(job, proof)
        self.assertEqual(job['build_order'], ['B', 'A'])
        del blobs['agent/models/B.lean']
        proof, job = self.stage(blobs, 'missing')
        self.assertIsNone(job)
        self.assertEqual(proof['reason'], 'dependency_missing')
        blobs['agent/models/B.lean'] = b'import A\ntheorem other : True := by trivial\n'
        proof, job = self.stage(blobs, 'cycle')
        self.assertIsNone(job)
        self.assertEqual(proof['reason'], 'dependency_cycle')
        proof, job = self.stage({'agent/models/Init.lean': b'theorem C02 : True := by trivial\n'}, 'reserved')
        self.assertIsNone(job)
        self.assertEqual(proof['outcome'], 'filter_rejected')

    def test_join_uses_report_anchor_and_keeps_missing_conflicting_keys_unknown(self):
        run = json.loads(self.review_blobs()['run.json'])
        rows = [{'claim_id': 'C02', 'semantic_state': 'refuted', 'report_path': 'report.md'}]
        actual = [finding(claim_id='C-02')]
        joined = recheck.join_correctness(run, actual, {'C02': 'R02'}, rows)
        self.assertFalse(joined['C02']['matches'])
        self.assertEqual(joined['C02']['observed'], 'supported')
        self.assertEqual(recheck.join_correctness(run, actual, {'C02': 'R02'}, None), {})
        self.assertEqual(recheck.join_correctness(run, actual, {'C02': 'R02'}, rows + rows), {})

    def test_plan_never_claims_execution_and_false_certainty_is_independent(self):
        campaign, _ = self.campaign()
        # Replace key plus both binding hashes before freezing the inventory.
        key = recheck.canonical({'expectations': [{'claim_id': 'C02', 'semantic_state': 'refuted', 'report_path': 'report.md'}]})
        self.write('campaign/lean/p2lean1/keys-sealed/demo.json', key)
        manifest = recheck.canonical({'schema_version': 1, 'canary': 'fixture', 'files': {'demo.json': {'sealed_sha256': recheck.digest(key)}}})
        self.write('campaign/lean/p2lean1/keys-sealed/KEYS-MANIFEST.json', manifest)
        self.write('campaign/runs/p2lean1/X3/lean/out/campaign.json', recheck.canonical({'keys_manifest_sha256': recheck.digest(manifest)}))
        recheck.inventory(campaign, ['X3'], ['p2lean1'], self.root / 'inventory')
        with mock.patch('subprocess.run', side_effect=AssertionError('No subprocess is allowed in planning')):
            result = recheck.plan(self.root / 'inventory/inventory.json', self.root / 'plan')['X3']
        self.assertFalse(result['run']['complete'])
        self.assertEqual(result['claims'][0]['outcome'], 'not_run')
        self.assertTrue(result['claims'][0]['false_formal_certainty'])
        self.assertEqual(result['summary']['false_formal_certainty'], 1)
        self.assertIn('No proof was rerun.', (self.root / 'plan/X3/lean-recheck.md').read_text())

    def test_changed_input_invalidates_frozen_inventory(self):
        campaign, review = self.campaign()
        recheck.inventory(campaign, ['X3'], ['p2lean1'], self.root / 'inventory')
        self.write(review + 'agent/audit/models/Audit.lean', b'theorem serviceCount_eq_two : 2 = 2 := by rfl\n')
        self.reject('source_changed', recheck.plan, self.root / 'inventory/inventory.json', self.root / 'plan')

    def test_required_proofs_all_pass_and_filter_failures_prevent_launch(self):
        proofs = [{'id': 'a', 'outcome': 'pass', 'reason': None},
                  {'id': 'b', 'outcome': 'fail', 'reason': 'target_mismatch'}]
        self.assertEqual(recheck.claim_outcome(['a', 'b'], proofs), ('fail', 'target_mismatch'))
        proofs[0]['outcome'] = 'not_run'
        proofs[1].update(outcome='filter_rejected', reason='lexical_rejection')
        self.assertEqual(recheck.claim_outcome(['a', 'b'], proofs), ('filter_rejected', 'lexical_rejection'))

    def test_batch_continues_on_proof_failure_and_stops_on_infrastructure_or_interrupt(self):
        calls, recorded = [], []
        def runner(job):
            calls.append(job['id'])
            return {'outcome': 'fail', 'reason': 'compile_error' if job['id'] == 'a' else 'infrastructure_error'}
        results = recheck.execute_jobs([{'id': i} for i in 'abc'], runner, lambda job, result: recorded.append(job['id']))
        self.assertEqual(calls, ['a', 'b'])
        self.assertEqual([r['outcome'] for r in results], ['fail', 'fail', 'not_run'])
        self.assertEqual(results[2]['reason'], 'infrastructure_error')
        self.assertEqual(recorded, ['a'], 'the aborting job is not finished and is never recorded')
        for stopper in (KeyboardInterrupt(), SystemExit(143)):
            calls.clear()
            def interrupted(job):
                calls.append(job['id'])
                if job['id'] == 'b':
                    raise stopper
                return {'outcome': 'pass', 'reason': None}
            results = recheck.execute_jobs([{'id': i} for i in 'abc'], interrupted)
            self.assertEqual(calls, ['a', 'b'])
            self.assertEqual([(r['outcome'], r['reason']) for r in results],
                             [('pass', None), ('not_run', 'interrupted'), ('not_run', 'interrupted')])
        for bad in ({'outcome': 'verifier_probe', 'reason': None}, {'outcome': 'not_linked', 'reason': None}, None):
            recorded.clear()
            with self.assertRaises(recheck.RecheckError) as raised:
                recheck.execute_jobs([{'id': 'a'}, {'id': 'b'}], lambda job: bad, lambda job, result: recorded.append(job['id']))
            self.assertEqual((raised.exception.reason, recorded), ('infrastructure_error', []))

    def test_reconciliation_and_repeat_collapsed_false_certainty(self):
        claims = [dict(dossier='demo', variant='mixed', claim_id='C02', repeat=i,
                       false_formal_certainty=True, matches=False, proofs=[], link_category='no_lean',
                       outcome=outcome, reason='compile_error' if outcome == 'fail' else None)
                  for i, outcome in enumerate(('pass', 'fail', 'filter_rejected', 'timeout', 'not_linked', 'not_run'), 1)]
        summary = recheck.summarize(claims, [])
        self.assertEqual(summary['reported'], 6)
        self.assertEqual(sum(summary[k] for k in ('pass', 'fail', 'filter_rejected', 'timeout', 'not_linked', 'not_run')), 6)
        self.assertEqual(summary['false_formal_certainty'], 6)
        self.assertEqual(summary['distinct_claim_false_formal_certainty'], 1)
        claims[0]['outcome'] = 'made_up_success'
        self.reject('invalid_metadata', recheck.summarize, claims, [])


GATE_HASH = 'a' * 64


class StandIn:
    """The calls recheck.py makes on recheck_sandbox, without Lean, systemd or Bubblewrap.

    run_job still goes through the REAL recheck_sandbox.validate_job, so the job format
    is checked against the executor's own validator, and it insists on a new run_dir like
    the real executor. `script(n, job)` returns (outcome, reason), a full result, or raises.
    """
    def __init__(self, script=None, accepted=None, refuse_gate=False):
        self.script, self.accepted, self.refuse_gate = script, accepted, refuse_gate
        self.jobs, self.manifest = [], {'lean/bin/lean': {'sha256': 'ab' * 32, 'mode': 0o555}}
        self.canonical, self.sha = sandbox.canonical, sandbox.sha

    def runtime_manifest(self, path):
        return self.manifest

    def check_gate(self, path, runtime_hash):
        if self.refuse_gate or (self.accepted and runtime_hash != self.accepted):
            raise sandbox.Rejection('infrastructure_error', 'Missing, invalid or stale verifier receipt')
        return GATE_HASH

    def binding_manifest(self, runtime_hash):
        return {'code': {'evalsuite/recheck_sandbox.py': 'b' * 64}}

    def build_runtime(self, lean, destination):
        Path(destination).mkdir(parents=True)
        return 'c' * 64

    def result(self, job, outcome='pass', reason=None, **changes):
        passed = outcome == 'pass'
        step = {'index': 0, 'kind': 'build', 'module': 'Audit', 'unit': 'u', 'argv': ['x'], 'bwrap_argv': [], 'exit_code': 0,
                'signal': None, 'seconds': 0.1, 'output_offset': 0, 'output_length': 0, 'output_sha256': '0' * 64,
                'object_sha256': '1' * 64, 'evidence': {}}
        result = dict(schema_version=1, outcome=outcome, reason=reason, detail='' if passed else 'stand-in detail',
                      argv=['x'] if outcome not in ('filter_rejected',) else [], steps=[step] if outcome != 'filter_rejected' else [],
                      exit_code=0 if passed else None, signal=None, seconds=0.5, axioms=[],
                      checks=[dict(p, index=i, axioms=[]) for i, p in enumerate(job['proofs'])] if passed else [],
                      log_paths=[], log_hashes={}, truncated=False, lean_subprocesses=1 if outcome != 'filter_rejected' else 0,
                      runtime_hash=job['runtime_hash'],
                      evidence={'gate_hash': GATE_HASH, 'steps_complete': True} if passed else {})
        result.update(changes)
        return result

    def run_job(self, job):
        sandbox.validate_job(job)
        run_dir = Path(job['run_dir'])
        assert not run_dir.exists(), 'the executor requires a NEW run_dir'
        self.jobs.append(job)
        behaviour = self.script(len(self.jobs), job) if self.script else ('pass', None)
        result = behaviour if isinstance(behaviour, dict) else self.result(job, *behaviour)
        run_dir.mkdir(parents=True)
        (run_dir / 'result.json').write_bytes(sandbox.canonical(result))
        (run_dir / 'output.log').write_bytes(b'log')
        (run_dir / 'wire.log').write_bytes(b'wire')
        (run_dir / 'job.json').write_bytes(sandbox.canonical(job))
        return result


def review_files(index, findings, files):
    run = {'unit': f'review-{index}', 'dossier': 'demo', 'variant': 'mixed', 'repeat': index,
           'opaque_id': f'run-{index}', 'arm': 'skill-lean', 'status': 'scored',
           'ended': 'exit_0', 'finished_at': '2026-11-12T00:00:00Z'}
    return {'run.json': recheck.canonical(run), 'findings.json': recheck.canonical(findings),
            'input/report-ledger.json': recheck.canonical(LEDGER), **files}


def refuted_workspace():
    blobs = workspace(relation='refutes')
    path = 'agent/audit/findings/C-02.md'
    blobs[path] = blobs[path].replace(b'\nproved\n', b'\nrefuted\n').replace(b'\nsupported\n', b'\nrefuted\n')
    return blobs


def two_proof_workspace():
    blobs = workspace()
    blobs['agent/audit/models/Audit.lean'] += b'theorem additional : 2 = 2 := by rfl\n'
    blobs['agent/audit/findings/C-02.md'] += (b'\n### Additional\n- Module: Audit\n- Theorem: additional\n'
                                              b'- Target: 2 = 2\n- Relation: supports\n')
    return blobs


def rejected_workspace():
    blobs = workspace()
    blobs['agent/audit/models/Audit.lean'] += b'\nsorry\n'
    return blobs


class RunTests(unittest.TestCase):
    """Planning on a fixture campaign, then run/export with a stand-in executor."""
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.base = Path(tempfile.mkdtemp(prefix='recheck-run-tests-', dir=WORK))
        cls.tmp = cls.base.relative_to(ROOT / 'tmp')
        reviews = {1: ([finding()], workspace()),
                   2: ([finding(formal_status='refuted', semantic_state='refuted')], refuted_workspace()),
                   3: ([finding()], two_proof_workspace()),
                   4: ([finding()], rejected_workspace()),
                   5: ([finding()], {'agent/notes.md': b'no lean here\n'})}
        campaign = cls.base / 'campaign'
        key = recheck.canonical({'expectations': []})
        manifest = recheck.canonical({'schema_version': 1, 'canary': 'fixture',
                                     'files': {'demo.json': {'sealed_sha256': recheck.digest(key)}}})
        base = campaign / 'runs/p2lean1/X3/lean/out'
        for path, data in {'lean/p2lean1/keys-sealed/demo.json': key,
                           'lean/p2lean1/keys-sealed/KEYS-MANIFEST.json': manifest}.items():
            cls.put(campaign / path, data)
        cls.put(base / 'campaign.json', recheck.canonical({'keys_manifest_sha256': recheck.digest(manifest)}))
        cls.put(base / 'skill-lean/records/index.json', recheck.canonical(
            {f'run-{i}': {'dossier': 'demo', 'variant': 'mixed', 'repeat': i} for i in reviews}))
        for index, (findings, files) in reviews.items():
            for path, data in review_files(index, findings, files).items():
                cls.put(base / f'skill-lean/runs/run-{index}' / path, data)
        with mock.patch.dict(recheck.BASELINES, {'X3': 5}), mock.patch.object(recheck, 'REVIEWS_PER_CELL', 5):
            recheck.inventory(campaign, ['X3'], ['p2lean1'], cls.base / 'inventory')
            cls.planned = recheck.plan(cls.base / 'inventory/inventory.json', cls.base / 'plan')['X3']
        cls.plan_dir = cls.base / 'plan'
        cls.counter = 0

    @staticmethod
    def put(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def setUp(self):
        type(self).counter += 1
        self.work = self.base / f'case-{self.counter}'
        self.work.mkdir()
        self.runtime = self.work / 'runtime'
        self.gate = self.work / 'receipt.json'
        self.gate.write_bytes(b'{"stand-in": true}\n')
        self.out = self.work / 'run'

    def argv(self, command='run', *extra, out=None):
        return [command, '--plan', str(self.plan_dir), '--gate', str(self.gate), '--runtime', str(self.runtime),
                '--out', str(out or self.out), '--cells', 'X3', *extra]

    def go(self, standin, *extra, out=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = recheck.main(self.argv('run', *extra, out=out), executor=standin)
        return code, stdout.getvalue(), stderr.getvalue()

    def exported(self, out=None):
        return json.loads((Path(out or self.out) / 'export/X3/lean-recheck.json').read_text())

    def claims(self, result):
        return {(c['review_unit'].rsplit('/', 1)[1], c['claim_id']): c for c in result['claims']}

    def test_plan_fixture_has_the_expected_shape(self):
        claims = self.claims(self.planned)
        self.assertEqual({k[0]: v['link_category'] for k, v in claims.items()},
                         {f'review-{i}': c for i, c in enumerate(['workspace_record'] * 4 + ['no_lean'], 1)})
        self.assertEqual(len(self.planned['jobs']), 4)
        self.assertEqual(claims[('review-4', 'C02')]['outcome'], 'filter_rejected')
        self.assertEqual(claims[('review-5', 'C02')]['outcome'], 'not_linked')

    def test_executor_job_is_exactly_the_executors_format_and_passes_its_validator(self):
        description = self.planned['jobs'][0]
        job = recheck.executor_job(description, WORK / 'x/run', WORK / 'runtime', 'f' * 64, '/tmp/receipt.json')
        sandbox.validate_job(job)
        self.assertEqual(set(job), {'schema_version', 'run_dir', 'runtime', 'runtime_hash', 'gate', 'sources', 'proofs'})
        self.assertEqual(job['proofs'], description['proofs'])
        source = description['sources'][0]
        self.assertEqual(job['sources'], [{'module': 'Audit', 'path': str(ROOT / source['path']), 'sha256': source['sha256']}])
        self.assertTrue(Path(job['sources'][0]['path']).is_file())
        self.assertEqual(recheck.digest(Path(job['sources'][0]['path']).read_bytes()), source['sha256'])
        self.assertNotIn('/scratch', json.dumps(job))

    def test_source_hashes_come_from_the_plan_so_the_executor_can_notice_a_change(self):
        description = json.loads(json.dumps(self.planned['jobs'][0]))
        altered = self.work / 'Audit.lean'
        altered.write_bytes(b'theorem serviceCount_eq_two : True := by trivial\n-- edited after planning\n')
        description['sources'][0]['path'] = str(altered.relative_to(ROOT))
        job = recheck.executor_job(description, WORK / 'x/run', WORK / 'runtime', 'f' * 64, '/tmp/receipt.json')
        self.assertEqual(job['sources'][0]['sha256'], self.planned['jobs'][0]['sources'][0]['sha256'])
        self.assertNotEqual(job['sources'][0]['sha256'], recheck.digest(altered.read_bytes()))

    def test_every_job_is_run_one_at_a_time_and_claims_follow_the_proof_results(self):
        standin = StandIn()
        code, out, err = self.go(standin)
        self.assertEqual((code, err), (0, ''), out)
        self.assertIn('RECHECK_FINISHED jobs=4', out)
        self.assertEqual(len(standin.jobs), 4)
        self.assertEqual(len({j['run_dir'] for j in standin.jobs}), 4)
        result = self.exported()
        claims = self.claims(result)
        self.assertEqual({k[0]: (v['outcome'], v['reason']) for k, v in claims.items()},
                         {'review-1': ('pass', None), 'review-2': ('pass', None), 'review-3': ('pass', None),
                          'review-4': ('filter_rejected', 'lexical_rejection'), 'review-5': ('not_linked', None)})
        refutation = next(p for p in result['proofs'] if p['id'] in claims[('review-2', 'C02')]['proofs'])
        self.assertEqual((refutation['relation'], refutation['outcome']), ('refutes', 'pass'))
        self.assertEqual(len(claims[('review-3', 'C02')]['proofs']), 2)
        self.assertTrue(all(p['lean_subprocesses'] == 1 and p['executor_admission']['passed'] for p in result['proofs'] if p['outcome'] == 'pass'))
        self.assertEqual(result['run']['runtime_hash'], standin.sha(standin.canonical(standin.manifest)))
        self.assertEqual(result['run']['gate_receipt_hash'], GATE_HASH)
        self.assertEqual(result['run']['phase'], 'recheck')
        self.assertTrue(result['run']['complete'], result['run']['incomplete_reason'])
        summary = result['summary']
        self.assertEqual((summary['reported'], summary['pass'], summary['filter_rejected'], summary['not_linked'], summary['not_run']), (5, 3, 1, 1, 0))
        self.assertEqual(summary['proofs_executed'], 4)
        status = json.loads((self.out / 'status.json').read_text())
        self.assertEqual((status['state'], status['jobs_finished'], status['jobs_remaining']), ('finished', 4, 0))
        self.assertFalse((self.out / 'run.pid').exists())

    def test_every_required_reference_must_pass_and_the_first_failure_names_the_reason(self):
        def script(n, job):
            return ('fail', 'replay_failed') if job['proofs'][0]['theorem'] == 'additional' else ('pass', None)
        standin = StandIn(script)
        self.assertEqual(self.go(standin)[0], 0)
        claims = self.claims(self.exported())
        self.assertEqual((claims[('review-3', 'C02')]['outcome'], claims[('review-3', 'C02')]['reason']), ('fail', 'replay_failed'))
        self.assertEqual(claims[('review-1', 'C02')]['outcome'], 'pass')
        self.assertEqual(self.exported()['summary']['fail_by_reason'], {'replay_failed': 1})

    def test_executor_reasons_are_kept_verbatim_and_counted_separately(self):
        reasons = {1: ('filter_rejected', 'strict_rejection'), 2: ('timeout', 'timeout'), 3: ('fail', 'target_mismatch'),
                   4: ('fail', 'compile_error')}
        standin = StandIn(lambda n, job: reasons.get(n, ('pass', None)))
        self.assertEqual(self.go(standin)[0], 0)
        result = self.exported()
        by_id = {p['id']: p for p in result['proofs']}
        self.assertEqual([by_id[p['id']]['reason'] for p in standin_proofs(standin, result)],
                         ['strict_rejection', 'timeout', 'target_mismatch', 'compile_error'])
        summary = result['summary']
        self.assertEqual(summary['filter_rejected_by_reason'], {'lexical_rejection': 1, 'strict_rejection': 1})
        # Claim level: the second proof of review-3 also failed, but the first failure in sorted order names the claim's reason.
        self.assertEqual(summary['fail_by_reason'], {'target_mismatch': 1})
        self.assertEqual((summary['timeout'], summary['pass']), (1, 0))
        self.assertEqual(result['summary']['proofs_passed'], 0)
        self.assertEqual(sum(summary[k] for k in ('pass', 'fail', 'filter_rejected', 'timeout', 'not_linked', 'not_run')), summary['reported'])
        table = (self.out / 'export/X3/lean-recheck.md').read_text()
        for row in ('reason: strict_rejection | 1', 'reason: lexical_rejection | 1',
                    'reason: target_mismatch | 1', 'reason: timeout | 1', 'reason: compile_error | 0', 'reason: replay_failed | 0',
                    'reason: axioms_disallowed | 0', 'reason: source_changed | 0', 'reason: infrastructure_error | 0'):
            self.assertIn('| ' + row + ' |', table)
        self.assertIn('Reconciliation: reported = pass + fail + filter_rejected + timeout + not_linked + not_run.', table)
        strict = next(p for p in result['proofs'] if p['reason'] == 'strict_rejection')
        self.assertEqual(strict['executor_admission'], {'passed': False, 'reason': 'strict_rejection', 'detail': 'stand-in detail'})
        self.assertTrue(strict['filter_result']['passed'], 'the product filter admitted it at planning time')

    def test_namespaced_targets_are_flagged_because_the_executor_checks_without_open(self):
        claim = next(c for c in self.planned['claims'] if c['outcome'] == 'not_run')
        loaded = {'X3': {'result': json.loads(json.dumps(self.planned)), 'sha256': '0' * 64}}
        for proof in loaded['X3']['result']['proofs']:
            if proof['id'] == claim['proofs'][0]:
                proof['namespace'] = 'Service'
        binding = {'cells': ['X3'], 'claims': None, 'limit': None, 'jobs': [], 'runtime_hash': 'f' * 64,
                   'gate_receipt_hash': GATE_HASH, 'gate_file_sha256': 'e' * 64}
        result = recheck.assemble(loaded, binding, {'binding_sha256': '1' * 64}, {}, None)['X3']
        flagged = [c for c in result['claims'] if c['caveats']]
        self.assertEqual([c['claim_id'] for c in flagged], [claim['claim_id']])
        self.assertEqual(flagged[0]['caveats'], ['target checked without `open Service`'])
        self.assertEqual(result['summary']['namespaced_proofs'], 1)
        self.assertIn('target checked without `open Service`', recheck.render_result(result))

    def test_reconciliation_is_checked_in_code(self):
        claims = [dict(dossier='d', variant='mixed', claim_id=f'C{i}', repeat=1, false_formal_certainty=None, matches=None,
                       proofs=[], link_category='no_lean', outcome=o, reason=r)
                  for i, (o, r) in enumerate([('pass', None), ('fail', 'axioms_disallowed'), ('fail', 'source_changed'),
                                              ('filter_rejected', 'lexical_rejection'), ('filter_rejected', 'strict_rejection'),
                                              ('timeout', 'timeout'), ('not_run', 'infrastructure_error')])]
        summary = recheck.summarize(claims, [])
        self.assertEqual(summary['reason_counts']['axioms_disallowed'], 1)
        text = recheck.markdown_table('X3', [], summary)
        self.assertIn('| reason: axioms_disallowed | 1 |', text)
        self.assertIn('| reason: infrastructure_error | 1 |', text)
        claims[1]['reason'] = None
        self.assertRaises(recheck.RecheckError, recheck.summarize, claims, [])
        claims[1].update(reason='axioms_disallowed', outcome='success')
        self.assertRaises(recheck.RecheckError, recheck.summarize, claims, [])

    def test_a_definitive_failure_outranks_a_pending_proof_and_unlinked_never_passes(self):
        proofs = [{'id': 'a', 'outcome': 'not_run', 'reason': 'infrastructure_error'}, {'id': 'b', 'outcome': 'fail', 'reason': 'compile_error'}]
        self.assertEqual(recheck.claim_outcome(['a', 'b'], proofs), ('fail', 'compile_error'))
        proofs[1].update(outcome='pass', reason=None)
        self.assertEqual(recheck.claim_outcome(['a', 'b'], proofs), ('not_run', 'infrastructure_error'))
        proofs[0].update(outcome='compiled_unlinked', reason=None)
        self.assertEqual(recheck.claim_outcome(['a', 'b'], proofs), ('not_linked', None))
        self.assertEqual(recheck.claim_outcome([], proofs), ('not_linked', None))

    def test_a_pass_of_a_job_without_a_linked_request_is_compiled_unlinked(self):
        job = {'runtime_hash': 'f' * 64, 'proofs': []}
        standin = StandIn()
        context = {'runtime_hash': 'f' * 64, 'gate_receipt_hash': GATE_HASH}
        result = standin.result(job)
        self.assertEqual(recheck.validate_executor_result(result, [], context), 'compiled_unlinked')
        proof = {'module': 'Audit', 'theorem': 'serviceCount_eq_two', 'target': 'True', 'relation': 'supports', 'claim_id': 'C02'}
        self.assertEqual(recheck.validate_executor_result(standin.result(dict(job, proofs=[proof])), [proof], context), 'pass')
        summary = recheck.summarize([], [{'id': 'p', 'outcome': 'compiled_unlinked', 'filter_result': {'passed': True}}])
        self.assertEqual((summary['compiled_unlinked'], summary['pass']), (1, 0))

    def test_no_option_skips_the_gate_or_the_filters(self):
        for flag in ('--no-gate', '--skip-gate', '--no-filter', '--skip-filters', '--unsafe', '--probe', '--force'):
            with self.subTest(flag=flag), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    recheck.main(self.argv('run', flag), executor=StandIn())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            recheck.main(['run', '--plan', str(self.plan_dir), '--runtime', str(self.runtime), '--out', str(self.out)], executor=StandIn())

    def test_a_refused_receipt_or_runtime_runs_nothing_and_creates_nothing(self):
        standin = StandIn(refuse_gate=True)
        code, out, err = self.go(standin)
        self.assertEqual(code, 2)
        self.assertIn('refused', err)
        self.assertEqual(standin.jobs, [])
        self.assertFalse(self.out.exists())
        # A receipt accepted only for another runtime is refused for the runtime actually used.
        standin = StandIn(accepted='d' * 64)
        self.assertEqual(self.go(standin)[0], 2)
        self.assertEqual(standin.jobs, [])
        self.assertFalse(self.out.exists())
        # The runtime must sit beneath tmp/recheck/ like the executor's own confinement.
        elsewhere = self.argv('run')
        elsewhere[elsewhere.index('--runtime') + 1] = '/tmp/outside-runtime'
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(recheck.main(elsewhere, executor=standin), 2)
        self.assertEqual(standin.jobs, [])

    def test_check_gate_and_build_runtime_commands(self):
        standin = StandIn()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(recheck.main(['check-gate', '--gate', str(self.gate), '--runtime', str(self.runtime)], executor=standin), 0)
        self.assertIn('gate accepted for this runtime', stdout.getvalue())
        self.assertIn('bound evalsuite/recheck_sandbox.py=' + 'b' * 64, stdout.getvalue())
        wrong = ['check-gate', '--gate', str(self.gate), '--runtime', str(self.runtime), '--expect-runtime', '0' * 64]
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(recheck.main(wrong, executor=standin), 2)
        with contextlib.redirect_stdout(io.StringIO()) as built:
            self.assertEqual(recheck.main(['build-runtime', '--lean', '/lean', '--out', str(self.work / 'built')], executor=standin), 0)
        self.assertEqual(built.getvalue().strip(), 'runtime_hash=' + 'c' * 64)

    def test_infrastructure_error_aborts_and_resume_reruns_only_unfinished_jobs(self):
        def script(n, job):
            return ('not_run', 'infrastructure_error') if n == 2 else ('pass', None)
        first = StandIn(script)
        code, out, err = self.go(first)
        self.assertEqual(code, 3, out + err)
        self.assertEqual(len(first.jobs), 2, 'nothing launches after the infrastructure_error')
        status = json.loads((self.out / 'status.json').read_text())
        self.assertEqual((status['state'], status['jobs_finished'], status['stopped']['reason']), ('aborted', 1, 'infrastructure_error'))
        self.assertEqual(sorted(p.name for p in (self.out / 'journal').iterdir()), ['X3-proof-00001.json'])
        partial = self.exported()
        self.assertFalse(partial['run']['complete'])
        self.assertEqual(partial['run']['stopped'], 'infrastructure_error')
        summary = partial['summary']
        self.assertEqual((summary['pass'], summary['not_run'], summary['filter_rejected'], summary['not_linked']), (1, 2, 1, 1))
        self.assertEqual({c['reason'] for c in partial['claims'] if c['outcome'] == 'not_run'}, {'infrastructure_error'})
        second = StandIn()
        code, out, err = self.go(second)
        self.assertEqual((code, err), (0, ''), out)
        self.assertIn('resumed', out)
        self.assertEqual(len(second.jobs), 3, 'the finished job is not rerun')
        self.assertEqual([j['run_dir'] for j in second.jobs][0][-4:], '-a02', 'the unfinished job gets a new run_dir')
        self.assertTrue(self.exported()['run']['complete'])
        self.assertTrue((self.out / 'history').glob('abort-*.json'))
        third = StandIn()
        self.assertEqual(self.go(third)[0], 0)
        self.assertEqual(third.jobs, [], 'a finished run reruns nothing')

    def test_interrupt_and_sigterm_stop_the_batch_as_interrupted_and_resume(self):
        def script(n, job):
            if n == 2:
                raise SystemExit(143)
            return ('pass', None)
        previous = signal.getsignal(signal.SIGTERM)
        first = StandIn(script)
        code, out, err = self.go(first)
        self.assertEqual((code, signal.getsignal(signal.SIGTERM)), (130, previous))
        self.assertEqual(json.loads((self.out / 'status.json').read_text())['state'], 'interrupted')
        self.assertEqual({c['reason'] for c in self.exported()['claims'] if c['outcome'] == 'not_run'}, {'interrupted'})
        self.assertEqual(self.go(StandIn())[0], 0)
        with self.assertRaises(SystemExit) as caught:
            recheck.terminate(signal.SIGTERM, None)
        self.assertEqual(caught.exception.code, 143)

    def test_resume_refuses_different_runtime_receipt_selection_plan_or_code(self):
        def script(n, job):
            return ('not_run', 'infrastructure_error') if n == 2 else ('pass', None)
        self.assertEqual(self.go(StandIn(script))[0], 3)
        other_runtime = StandIn()
        other_runtime.manifest = {'lean/bin/lean': {'sha256': 'cd' * 32, 'mode': 0o555}}
        other_gate = StandIn()
        (self.gate).write_bytes(b'{"stand-in": "changed"}\n')
        cases = [('runtime', other_runtime, ()), ('receipt file', other_gate, ()), ('selection', StandIn(), ('--limit', '2'))]
        for label, standin, extra in cases:
            with self.subTest(label):
                if label == 'selection':
                    self.gate.write_bytes(b'{"stand-in": true}\n')
                code, out, err = self.go(standin, *extra)
                self.assertEqual(code, 2, out)
                self.assertIn('Resume refused', err)
                self.assertEqual(standin.jobs, [])
        self.gate.write_bytes(b'{"stand-in": true}\n')
        with mock.patch.object(recheck, 'code_hashes', return_value={'evalsuite/recheck.py': '0' * 64}):
            standin = StandIn()
            self.assertEqual(self.go(standin)[0], 2)
            self.assertEqual(standin.jobs, [])
        copy = self.work / 'plan-copy'
        shutil.copytree(self.plan_dir, copy)
        tampered = copy / 'X3/lean-recheck.json'
        tampered.write_bytes(tampered.read_bytes().replace(b'review-1', b'review-9', 1))
        standin = StandIn()
        stdout, stderr = io.StringIO(), io.StringIO()
        arguments = self.argv('run')
        arguments[arguments.index('--plan') + 1] = str(copy)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(recheck.main(arguments, executor=standin), 2)
        self.assertIn('publication marker', stderr.getvalue())
        self.assertEqual(standin.jobs, [])
        resumed = StandIn()
        self.assertEqual(self.go(resumed)[0], 0)
        self.assertEqual(len(resumed.jobs), 3)

    def test_contradictory_or_forbidden_executor_results_abort_and_are_never_recorded(self):
        def tampered(**changes):
            return lambda n, job: StandIn().result(job, **changes)
        def drop_check(n, job):
            return StandIn().result(job, checks=[])
        def other_check(n, job):
            result = StandIn().result(job)
            result['checks'][0]['target'] = 'False'
            return result
        def no_target(n, job):
            result = StandIn().result(job)
            result['checks'][0]['target'] = None
            return result
        cases = {'probe outcome': tampered(outcome='verifier_probe', probe_outcome='pass'),
                 'not_linked': tampered(outcome='not_linked'),
                 'pass with a reason': tampered(reason='compile_error'),
                 'fail with a filter reason': tampered(outcome='fail', reason='strict_rejection'),
                 'unknown reason': tampered(outcome='fail', reason='made_up'),
                 'timeout without timeout reason': tampered(outcome='timeout', reason='compile_error'),
                 'wrong gate': tampered(evidence={'gate_hash': 'e' * 64, 'steps_complete': True}),
                 'steps incomplete': tampered(evidence={'gate_hash': GATE_HASH, 'steps_complete': False}),
                 'no lean step': tampered(lean_subprocesses=0),
                 'forbidden axiom': tampered(axioms=['sorryAx']),
                 'other runtime': tampered(runtime_hash='9' * 64),
                 'missing field': lambda n, job: {'outcome': 'pass', 'reason': None},
                 'pass without its check': drop_check, 'pass for another target': other_check,
                 'declaration-only check': no_target}
        for label, script in cases.items():
            with self.subTest(label):
                standin = StandIn(script)
                out = self.work / ('run-' + label.replace(' ', '-'))
                code, stdout, stderr = self.go(standin, out=out)
                self.assertEqual(code, 3, stdout + stderr)
                self.assertEqual(len(standin.jobs), 1)
                self.assertEqual(list((out / 'journal').iterdir()), [])
                self.assertTrue(list((out / 'history').glob('violation-*.json')))
                status = json.loads((out / 'status.json').read_text())
                self.assertEqual((status['state'], status['stopped']['reason']), ('aborted', 'infrastructure_error'))
                self.assertEqual(self.exported(out)['summary']['pass'], 0)

    def test_selection_runs_only_the_named_claims_and_the_result_is_partial(self):
        standin = StandIn()
        selected = 'p2lean1/X3/review-2#C02'
        self.assertEqual(self.go(standin, '--claims', selected)[0], 0)
        self.assertEqual([j['proofs'][0]['theorem'] for j in standin.jobs], ['serviceCount_eq_two'])
        result = self.exported()
        self.assertFalse(result['run']['complete'])
        self.assertIn('only the selected claims', result['run']['incomplete_reason'])
        claims = self.claims(result)
        self.assertEqual(claims[('review-2', 'C02')]['outcome'], 'pass')
        self.assertEqual(claims[('review-1', 'C02')]['outcome'], 'not_run')
        self.assertEqual(result['summary']['proofs_executed'], 1)
        self.assertIn('PARTIAL', (self.out / 'export/X3/lean-recheck.md').read_text())
        for impossible in ('p2lean1/X3/review-4#C02', 'p2lean1/X3/review-5#C02', 'p2lean1/X3/nothing#C02'):
            with self.subTest(impossible):
                code, out, err = self.go(StandIn(), '--claims', impossible, out=self.work / 'bad')
                self.assertEqual(code, 2)
                self.assertIn('without a runnable job', err)
        standin = StandIn()
        self.assertEqual(self.go(standin, '--limit', '2', out=self.work / 'limited')[0], 0)
        self.assertEqual(len(standin.jobs), 2)

    def test_a_claim_with_a_preflight_rejection_launches_none_of_its_proofs(self):
        loaded = {'X3': {'result': json.loads(json.dumps(self.planned)), 'sha256': '0' * 64}}
        result = loaded['X3']['result']
        claim = next(c for c in result['claims'] if len(c['proofs']) == 2)
        by_id = {p['id']: p for p in result['proofs']}
        by_id[claim['proofs'][1]].update(outcome='filter_rejected', reason='lexical_rejection', filter_result={'passed': False, 'detail': 'x'})
        claim['outcome'], claim['reason'] = recheck.claim_outcome(claim['proofs'], result['proofs'])
        self.assertEqual(claim['outcome'], 'filter_rejected')
        jobs = recheck.planned_jobs(loaded, ['X3'])
        self.assertEqual(len(jobs), 2)
        self.assertFalse(set(claim['proofs']) & {j['id'] for j in jobs})

    def test_a_changed_planned_job_is_refused_before_launch(self):
        loaded = {'X3': {'result': json.loads(json.dumps(self.planned)), 'sha256': '0' * 64}}
        loaded['X3']['result']['proofs'][0]['job_sha256'] = '0' * 64
        self.assertRaises(recheck.RecheckError, recheck.planned_jobs, loaded, ['X3'])

    def test_export_publishes_a_verified_pair_and_evidence_beside_it(self):
        standin = StandIn()
        self.assertEqual(self.go(standin)[0], 0)
        destination = self.work / 'copy-out'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(recheck.main(['export', '--run', str(self.out), '--dest', str(destination)]), 0)
        cell = destination / 'X3'
        publication = json.loads((cell / 'publication.json').read_text())
        self.assertEqual(publication['json_sha256'], recheck.digest((cell / 'lean-recheck.json').read_bytes()))
        self.assertEqual(publication['markdown_sha256'], recheck.digest((cell / 'lean-recheck.md').read_bytes()))
        self.assertEqual((publication['phase'], publication['complete']), ('recheck', True))
        rows = json.loads((cell / 'evidence-manifest.json').read_text())
        self.assertEqual(len(rows), 16)
        for name, value in rows.items():
            self.assertEqual(recheck.digest((cell / name).read_bytes()), value)
        self.assertEqual(recheck.digest((cell / 'evidence-manifest.json').read_bytes()), publication['evidence_manifest_sha256'])
        schema = json.loads((cell / 'lean-recheck.json').read_text())
        for key in ('schema_version', 'cell', 'run', 'reviews', 'claims', 'proofs', 'summary'):
            self.assertIn(key, schema)
        for key in ('id', 'created_utc', 'base_commit', 'code_hashes', 'input_manifest_hash', 'runtime_hash', 'lean_version',
                    'gate_receipt_hash', 'complete'):
            self.assertIn(key, schema['run'])
        for proof in schema['proofs']:
            for key in ('id', 'outcome', 'reason', 'source_paths', 'source_hashes', 'module', 'theorem', 'target', 'target_origin',
                        'relation', 'relation_origin', 'attempt_provenance', 'filter_result', 'argv', 'exit_code', 'signal',
                        'seconds', 'axioms', 'log_paths', 'log_hashes', 'truncated'):
                self.assertIn(key, proof)

    def test_status_is_read_only_and_never_loads_the_executor(self):
        self.assertEqual(self.go(StandIn())[0], 0)
        stdout = io.StringIO()
        with mock.patch.object(recheck, 'load_executor', side_effect=AssertionError('status must not touch the executor')):
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(recheck.main(['status', '--run', str(self.out), '--verbose']), 0)
        text = stdout.getvalue()
        self.assertIn('state=finished', text)
        self.assertIn('finished 4/4', text)
        self.assertIn('check Audit.serviceCount_eq_two target=\'True\' relation=refutes', text)
        status = json.loads((self.out / 'status.json').read_text())
        status.update(state='running', pid=2 ** 22 + 1)
        recheck.atomic_write(self.out / 'status.json', recheck.canonical(status))
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(recheck.main(['status', '--run', str(self.out)]), 4)
        self.assertIn('stale status', stdout.getvalue())

    def test_reasons_and_outcomes_mirror_the_real_executor(self):
        self.assertEqual(recheck.REASONS, sandbox.REASONS)
        self.assertEqual(recheck.OUTCOMES, sandbox.OUTCOMES)
        self.assertEqual(set(recheck.EXECUTOR_OUTCOMES), set(sandbox.OUTCOMES) - {'not_linked'})
        self.assertNotIn(sandbox.PROBE_OUTCOME, recheck.EXECUTOR_OUTCOMES)
        for reason in sandbox.REASONS:
            outcome = ('filter_rejected' if reason in ('lexical_rejection', 'strict_rejection') else 'timeout' if reason == 'timeout'
                       else 'not_run' if reason in ('infrastructure_error', 'interrupted') else 'fail')
            self.assertEqual(recheck.outcome_for_reason(reason), outcome)

    def test_run_is_the_only_caller_of_the_executor_and_planning_never_imports_it(self):
        source = (ROOT / 'evalsuite/recheck.py').read_text()
        self.assertEqual(source.count('executor.run_job('), 1)
        self.assertNotIn('run_probe', source)
        self.assertNotIn('probe_root', source)
        self.assertNotIn('_run_fixture', source)
        self.assertNotIn('/usr/bin/bwrap', source)


def standin_proofs(standin, result):
    """Planned proofs in the order the stand-in executor received them."""
    return [next(p for p in result['proofs'] if p['source_paths'] and p['theorem'] == job['proofs'][0]['theorem']
                 and str(ROOT / p['staged_sources']) in job['sources'][0]['path']) for job in standin.jobs]


if __name__ == '__main__':
    unittest.main()
