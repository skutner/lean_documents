"""Public interface staging and evaluator-material rejection, using fake agents only."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
sys.path.insert(0, str(ROOT / 'tests'))
import run_blind  # noqa: E402
from evalsuite_campaign_fixture import ARMS, Campaign, perfect  # noqa: E402
from evalsuite_fidelity_campaign import FamilyCampaign  # noqa: E402

ARMS_FILE = ROOT / 'evalsuite/arms-fidelity.json'
INTERFACE_ARM = 'skill-lean-interface'
TEST_RUN = Path(tempfile.mkdtemp(prefix='evalsuite-interface-tests-', dir=ROOT / 'tmp'))


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName[:35] + '-', dir=TEST_RUN))
        patch = mock.patch.object(run_blind, 'toolchain_python', return_value=None)
        patch.start()
        self.addCleanup(patch.stop)

    def run_campaign(self, campaign, policy=perfect, **options):
        options.setdefault('arms_file', ARMS_FILE)
        options.setdefault('arms', INTERFACE_ARM)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as errors:
            code = campaign.run(policy, **options)
        return code, errors.getvalue()

    def test_existing_specs_instructions_and_fingerprints_are_unchanged(self):
        original = json.loads((ROOT / 'evalsuite/arms.json').read_text())
        extended = json.loads(ARMS_FILE.read_text())
        self.assertEqual(extended['arms'][:3], original['arms'])
        self.assertEqual(extended['task'], original['task'])
        before, after = run_blind.load_arms(), run_blind.load_arms(ARMS_FILE)
        for name in before:
            self.assertEqual(after[name], before[name])
            arm = before[name]
            expected = hashlib.sha256(json.dumps([1, name, arm['skill'], arm['lean'], arm['method'], arm['task']]).encode()).hexdigest()
            self.assertEqual(arm['fingerprint'], expected)
            self.assertEqual(run_blind.instructions(arm, 30, '/lean'), run_blind.instructions(after[name], 30, '/lean'))
        self.assertNotIn(INTERFACE_ARM, before)

    def test_all_six_families_stage_only_public_types_and_preserve_submission(self):
        self.assertEqual(set(run_blind.FIDELITY_FAMILIES),
                         {'approvals', 'deadlines', 'ledger', 'packet', 'casework', 'procurement'})
        campaign = FamilyCampaign(self.root, seed=511)
        interfaces = {}
        def agent(call):
            public = call['cwd'] / 'Interface.lean'
            if call['arm'] == INTERFACE_ARM:
                family = call['dossier'].rsplit('-', 1)[1]
                self.assertEqual(public.read_bytes(), (run_blind.FIDELITY_ROOT / family / 'Interface.lean').read_bytes())
                interfaces[family] = interfaces.get(family, 0) + 1
                self.assertEqual([p for p in call['staged'] if p.endswith('.lean') and not p.startswith('skill/')],
                                 ['Interface.lean'])
                for declaration in ('Candidate.dossier', 'Candidate.model', 'Candidate.claimIds'):
                    self.assertIn(declaration, call['instructions'])
                self.assertIn('"correct/C01"', call['instructions'])
                self.assertIn('"mixed/C01"', call['instructions'])
                self.assertEqual(call['instructions'], run_blind.instructions(
                    run_blind.load_arms(ARMS_FILE)[INTERFACE_ARM], 30, campaign.lean))
                self.assertNotIn('{{', call['instructions'])
                candidate = call['cwd'] / 'work/fidelity/Candidate.lean'
                candidate.parent.mkdir(parents=True)
                candidate.write_text('-- fake agent submission, not a Lean correctness claim\n')
            else:
                self.assertFalse(public.exists())
                self.assertNotIn('Candidate.claimIds', call['instructions'])
            return perfect(call)
        code, errors = self.run_campaign(campaign, agent, arms=ARMS + ',' + INTERFACE_ARM)
        self.assertEqual(code, 0, errors)
        self.assertEqual(interfaces, dict.fromkeys(run_blind.FIDELITY_FAMILIES, 2))
        for path in (campaign.out / INTERFACE_ARM / 'records').glob('run-*.json'):
            record = json.loads(path.read_text())
            bundle = campaign.out / INTERFACE_ARM / 'runs' / record['opaque_id']
            self.assertEqual(record['case_files']['Interface.lean'], hashlib.sha256((bundle / 'agent/Interface.lean').read_bytes()).hexdigest())
            self.assertTrue((bundle / 'agent/work/fidelity/Candidate.lean').is_file())
            self.assertEqual(json.loads((bundle / 'run.json').read_text())['supplied_files_changed'], [])

    def test_modified_public_interface_is_recorded_as_changed(self):
        campaign = Campaign(self.root, dossiers=1)
        def agent(call):
            (call['cwd'] / 'Interface.lean').write_text('-- modified\n')
            return perfect(call)
        code, errors = self.run_campaign(campaign, agent)
        self.assertEqual(code, 0, errors)
        self.assertTrue(all(r['supplied_files_changed'] == ['Interface.lean'] for r in campaign.units().values()))

    def test_interface_bytes_are_part_of_only_the_opted_in_fingerprint(self):
        before = run_blind.load_arms(ARMS_FILE)
        copied = self.root / 'interfaces'
        shutil.copytree(run_blind.FIDELITY_ROOT, copied)
        with (copied / 'approvals/Interface.lean').open('a') as stream:
            stream.write('\n-- changed public contract\n')
        with mock.patch.object(run_blind, 'FIDELITY_ROOT', copied):
            after = run_blind.load_arms(ARMS_FILE)
        for name in ARMS.split(','):
            self.assertEqual(before[name]['fingerprint'], after[name]['fingerprint'])
        self.assertNotEqual(before[INTERFACE_ARM]['fingerprint'], after[INTERFACE_ARM]['fingerprint'])

    def test_invalid_interface_opt_in_is_refused(self):
        original = json.loads(ARMS_FILE.read_text())
        # Keep task/prompt paths valid when the temporary arms file moves.
        original['task'] = str(ROOT / 'evalsuite' / original['task'])
        for arm in original['arms']:
            arm['prompt'] = str(ROOT / 'evalsuite' / arm['prompt'])
        path = self.root / 'arms.json'
        for changes in ({'fidelity_interface': 'true'}, {'lean': False}, {'skill': False}):
            data = json.loads(json.dumps(original))
            data['arms'][-1].update(changes)
            path.write_text(json.dumps(data))
            with self.subTest(changes=changes), self.assertRaises(run_blind.RunError):
                run_blind.load_arms(path)

    def test_unknown_family_is_refused_before_any_review(self):
        campaign = Campaign(self.root, dossiers=1)
        with mock.patch.object(run_blind, 'fidelity_family', side_effect=run_blind.RunError('Unknown family')):
            code, errors = self.run_campaign(campaign)
        self.assertEqual(code, 1)
        self.assertIn('Unknown family', errors)
        self.assertFalse(campaign.calls)
        for name in ('manual-case', 'g1-001-other', '../approvals'):
            with self.subTest(name=name), self.assertRaises(run_blind.RunError):
                run_blind.fidelity_family(name)

    def test_every_family_name_resolves_and_lookalikes_do_not(self):
        for family in run_blind.FIDELITY_FAMILIES:
            self.assertEqual(run_blind.fidelity_family(f'g511-005-{family}'), family)
        for name in ('g511-005-casework-extra', 'g511-005-Casework', 'g511-005-procurements', 'casework'):
            with self.subTest(name=name), self.assertRaises(run_blind.RunError):
                run_blind.fidelity_family(name)

    def test_reference_fixture_and_generated_truth_copies_are_refused_in_readable_inputs(self):
        root = run_blind.FIDELITY_ROOT
        leaks = [(root / 'approvals/Reference.lean').read_bytes(),
                 (root / 'ledger/fixtures/Faithful.lean').read_bytes(),
                 b'namespace Packet.Material\ndef sealedWorlds := []\nend Packet.Material\n',
                 b'{"classification": "evaluator_only", "claims": []}',
                 (root / 'casework/Reference.lean').read_bytes(),
                 (root / 'procurement/Reference.lean').read_bytes(),
                 (root / 'casework/fixtures/Faithful.lean').read_bytes(),
                 (root / 'procurement/fixtures/Slips.lean').read_bytes(),
                 (root / 'casework/fixtures/Neutral.lean').read_bytes(),
                 (root / 'procurement/fixtures/Neutral.lean').read_bytes(),
                 b'namespace Casework.Material\ndef sealedWorlds := []\nend Casework.Material\n',
                 b'namespace Procurement.Material\ndef unseen := []\nend Procurement.Material\n']
        for place in ('source', 'skill', 'home', 'lean', 'python'):
            for number, data in enumerate(leaks):
                with self.subTest(place=place, leak=number):
                    campaign = Campaign(self.root / f'{place}-{number}', dossiers=1)
                    options, python = {}, None
                    if place == 'source':
                        target = next(campaign.fixtures.glob('*/sources'))
                    elif place == 'skill':
                        target = campaign.root / 'skill'
                        target.mkdir()
                        (target / 'SKILL.md').write_text('Test skill\n')
                        options['skill'] = target
                    elif place == 'python':
                        target = campaign.root / 'python'
                        target.mkdir()
                        python = {'root': str(target), 'path': str(target / 'python3'), 'version': '3.12.0'}
                    else:
                        target = campaign.home if place == 'home' else campaign.lean.parent
                    (target / 'innocent.bin').write_bytes(data)
                    with mock.patch.object(run_blind, 'toolchain_python', return_value=python):
                        code, errors = self.run_campaign(campaign, **options)
                    self.assertEqual(code, 1, errors)
                    self.assertIn('Blindness check failed', errors)
                    self.assertFalse(campaign.calls)

    def test_legacy_arm_also_rejects_evaluator_material(self):
        campaign = Campaign(self.root, dossiers=1)
        (next(campaign.fixtures.glob('*/sources')) / 'extra.md').write_bytes(
            (run_blind.FIDELITY_ROOT / 'approvals/fixtures/Faithful.lean').read_bytes())
        code, errors = self.run_campaign(campaign, arms='plain')
        self.assertEqual(code, 1, errors)
        self.assertFalse(campaign.calls)

    def test_evaluator_path_alias_and_hardlink_are_refused(self):
        for family, kind in [(family, kind) for family in ('deadlines', 'casework', 'procurement')
                             for kind in ('file-link', 'directory-link', 'hardlink')]:
            reference = run_blind.FIDELITY_ROOT / family / 'Reference.lean'
            with self.subTest(family=family, kind=kind):
                campaign = Campaign(self.root / family / kind, dossiers=1)
                target = campaign.lean.parent / 'innocent'
                if kind == 'hardlink':
                    os.link(reference, target)
                else:
                    target.symlink_to(reference if kind == 'file-link' else reference.parent)
                code, errors = self.run_campaign(campaign)
                self.assertEqual(code, 1, errors)
                self.assertIn('Blindness check failed', errors)
                self.assertFalse(campaign.calls)

    def test_clean_toolchain_directory_alias_is_still_allowed(self):
        campaign = Campaign(self.root, dossiers=1)
        actual = campaign.lean.parent.parent / 'actual'
        actual.mkdir()
        (actual / 'library.txt').write_text('ordinary toolchain library\n')
        (actual / 'cycle').symlink_to(actual)
        (campaign.lean.parent / 'library').symlink_to(actual)
        code, errors = self.run_campaign(campaign)
        self.assertEqual(code, 0, errors)
        self.assertEqual(len(campaign.calls), 2)

    def test_embedded_fixture_and_named_artifact_are_refused(self):
        for name, data in [('renamed.txt', b'-- preface\n' + (run_blind.FIDELITY_ROOT / 'ledger/fixtures/Faithful.lean').read_bytes()),
                           ('renamed-casework.txt', b'-- preface\n' + (run_blind.FIDELITY_ROOT / 'casework/Reference.lean').read_bytes()),
                           ('renamed-procurement.txt', b'-- preface\n' + (run_blind.FIDELITY_ROOT / 'procurement/fixtures/Faithful.lean').read_bytes()),
                           ('Facts.lean', b'-- generated facts with namespace removed\n')]:
            with self.subTest(name=name):
                campaign = Campaign(self.root / name, dossiers=1)
                (campaign.lean.parent / name).write_bytes(data)
                code, errors = self.run_campaign(campaign)
                self.assertEqual(code, 1, errors)
                self.assertFalse(campaign.calls)

    def test_late_staging_contamination_never_reaches_the_agent(self):
        campaign = Campaign(self.root, dossiers=1)
        original = run_blind.blindness.stage_case
        def contaminated(*args, **kwargs):
            record = original(*args, **kwargs)
            (Path(record['arena_run']) / 'surprise.txt').write_text('namespace Ledger.Material\n')
            return record
        with mock.patch.object(run_blind.blindness, 'stage_case', side_effect=contaminated):
            code, errors = self.run_campaign(campaign)
        self.assertEqual(code, 1, errors)
        self.assertFalse(campaign.calls)
        self.assertFalse(list(campaign.arena.iterdir()), 'staged review is archived and copied login removed')
        self.assertEqual([r['status'] for r in campaign.units().values()], ['failed'])

    def test_interrupted_authored_facts_are_archived_before_resuming(self):
        for arm in ('plain', INTERFACE_ARM):
            with self.subTest(arm=arm):
                campaign = Campaign(self.root / arm, dossiers=1)
                def interrupted(call):
                    facts = call['cwd'] / 'work/Facts.lean'
                    facts.parent.mkdir()
                    facts.write_text('-- Independently authored facts from this review.\n')
                    raise KeyboardInterrupt
                with self.assertRaises(KeyboardInterrupt):
                    self.run_campaign(campaign, interrupted, arms=arm)
                self.assertEqual(len(campaign.calls), 1)
                cut_call = campaign.calls[0]
                self.assertTrue(cut_call['home'].is_dir())
                self.assertTrue((cut_call['cwd'] / 'work/Facts.lean').is_file())
                self.assertEqual(campaign.units(), {})
                code, errors = self.run_campaign(campaign, arms=arm)
                self.assertEqual(code, 0, errors)
                self.assertEqual(len(campaign.calls), 2, 'The interrupted review must not launch again')
                self.assertNotEqual(cut_call['cwd'], campaign.calls[1]['cwd'])
                records = list(campaign.units().values())
                self.assertEqual(len(records), 2)
                cut = [record for record in records if record['ended'] == 'interrupted']
                self.assertEqual([(record['status'], record['sheet']) for record in cut], [('failed', 'not_read')])
                archived = campaign.out / arm / 'runs' / cut[0]['opaque_id'] / 'agent/work/Facts.lean'
                self.assertEqual(archived.read_text(), '-- Independently authored facts from this review.\n')
                self.assertFalse(cut_call['home'].exists(), 'The stale copied login must be removed')
                self.assertEqual(list(campaign.arena.iterdir()), [])
                code, errors = self.run_campaign(campaign, arms=arm)
                self.assertEqual(code, 0, errors)
                self.assertEqual(len(campaign.calls), 2, 'A completed campaign starts no further agent')

    def test_interface_probe_adds_only_opted_in_interface_checks(self):
        for arm in ('skill-lean', INTERFACE_ARM):
            with self.subTest(arm=arm):
                campaign = Campaign(self.root / arm, dossiers=1)
                observed = []
                def launch(command, cwd, env, seconds, log):
                    public = Path(cwd) / 'Interface.lean'
                    observed.append(public.read_bytes() if public.exists() else None)
                    script = Path(env['CODEX_BIN']).read_text()
                    self.assertEqual('fidelity_evaluator_hidden' in script, arm == INTERFACE_ARM)
                    self.assertEqual('fidelity_interface_available' in script, arm == INTERFACE_ARM)
                    return {'ended': 'exit_0', 'exit_code': 0, 'seconds': 0}
                overrides = {'launch': launch, 'node': 'node', 'ala': {'entry': '/fake/ala.mjs'},
                             'probe': lambda context: [run_blind.probe_arm(context, context['arms'][arm])]}
                with mock.patch.object(run_blind.sys, 'platform', 'linux'), \
                        mock.patch.object(run_blind, 'assert_simulated_agent', return_value='/fake/codex'), \
                        redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code = run_blind.main(campaign.argv('probe', arms=arm, arms_file=ARMS_FILE), overrides)
                self.assertEqual(code, 1, 'A fake launch without sandbox observations must fail the probe')
                expected = (run_blind.FIDELITY_ROOT / 'approvals/Interface.lean').read_bytes() if arm == INTERFACE_ARM else None
                self.assertEqual(observed, [expected])
                required = run_blind.probe_checks(run_blind.load_arms(ARMS_FILE)[arm], [], {}, platform='linux')
                self.assertEqual('fidelity_interface_available' in required, arm == INTERFACE_ARM)


if __name__ == '__main__':
    unittest.main()
