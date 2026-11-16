"""What skill folder each arm is staged: the original arms keep the skill as it was at commit 569a4d89.

The opt-in source-bound profile adds files to `skills/evidence-to-proof-review/` and changes its
`scripts/markdown_audit.py`. These tests stage whole campaigns with stand-in agents, hash the skill
folder each agent would see, and compare it with the stored manifest of the 569a4d89 folder. They need
neither git (except the one test that regenerates the manifest) nor Lean nor a model.
"""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
sys.path.insert(0, str(ROOT / 'tests'))
import run_blind  # noqa: E402
from evalsuite_campaign_fixture import Campaign, perfect  # noqa: E402
from evalsuite_fidelity_campaign import FamilyCampaign  # noqa: E402

ARMS_FILE = ROOT / 'evalsuite/arms-fidelity.json'
SKILL = ROOT / 'skills/evidence-to-proof-review'
BASELINE = ROOT / 'evalsuite/skill_baseline'
MANIFEST = json.loads((BASELINE / 'manifest.json').read_text(encoding='utf-8'))
COMMIT = '569a4d89f8c9a6013e004f964454c64c390932d1'
AUDIT = 'scripts/markdown_audit.py'
AUDIT_BLOB = '59286045c5ac1dd8aa7c652b5e1089cd381807a2'     # git blob of the original helper at the commit
PLAIN_ARMS = ('skill-no-lean', 'skill-lean', 'skill-lean-interface')
OPT_IN = 'skill-lean-source-bound'
# SHA-256 of the files of the three original arms, as committed: they must stay byte-identical.
ORIGINAL_FILES = {
    'arms.json': 'b9293ba66c612f108dd7ac535353db3a0fa3efa421eb003283faa16ba2d19562',
    'prompts/task.md': 'a2b829fbbac1ffa16cc709f1d9408676fe6913be8afc3a02ae622db2026ac1ca',
    'prompts/plain.md': 'd0a2e1eea236c34245ec626de4a3d9fd7b01b11d65127743ce858124b671db78',
    'prompts/skill-no-lean.md': 'f83875065995cf9a282128edca543760547ca6f8e9724904f8ad1b3a54c6d612',
    'prompts/skill-lean.md': '620e59f41c429fa719a84c8d0dc9659e425e539804a8473edcc3ddcdcef106ba',
}
TEST_RUN = Path(tempfile.mkdtemp(prefix='evalsuite-source-bound-tests-', dir=ROOT / 'tmp'))


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def folder_files(root):
    """Relative path to SHA-256 for every file below `root`, ignoring the caches the runner never stages."""
    return {path.relative_to(root).as_posix(): sha256(path.read_bytes())
            for path in sorted(Path(root).rglob('*'))
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'}


def profile_files():
    return sorted(name for name in folder_files(SKILL) if run_blind.is_source_bound_path(name))


class BaselineTests(unittest.TestCase):
    def test_original_helper_is_pinned_to_the_git_blob_without_git(self):
        data = (BASELINE / 'markdown_audit.py').read_bytes()
        self.assertEqual(hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest(), AUDIT_BLOB)
        self.assertEqual(sha256(data), MANIFEST['files'][AUDIT])
        self.assertEqual(run_blind.SKILL_BASELINE_AUDIT, BASELINE / 'markdown_audit.py')

    def test_manifest_lists_the_original_folder_and_no_profile_file(self):
        self.assertEqual((MANIFEST['commit'], MANIFEST['algorithm'], MANIFEST['schema_version']), (COMMIT, 'sha256', 1))
        self.assertEqual(MANIFEST['folder'], 'skills/evidence-to-proof-review')
        self.assertIn('SKILL.md', MANIFEST['files'])
        self.assertFalse([name for name in MANIFEST['files'] if run_blind.is_source_bound_path(name)])
        for name, digest in MANIFEST['files'].items():
            self.assertRegex(digest, r'^[0-9a-f]{64}$', name)

    def test_profile_files_are_exactly_what_the_checkout_adds(self):
        """Everything the profile added is recognised, and every other file is one the original folder had."""
        current = folder_files(SKILL)
        added = sorted(set(current) - set(MANIFEST['files']))
        self.assertEqual(added, profile_files())
        self.assertTrue({'scripts/source_bound_audit.py', 'references/source-bound.md',
                         'assets/source_bound/Types.lean'} <= set(added))
        changed = sorted(name for name in MANIFEST['files'] if current.get(name) != MANIFEST['files'][name])
        self.assertEqual(changed, [AUDIT], 'only the helper differs from the original folder')
        self.assertEqual(sha256((BASELINE / 'markdown_audit.py').read_bytes()), MANIFEST['files'][AUDIT])
        self.assertNotEqual(current[AUDIT], MANIFEST['files'][AUDIT])

    def test_original_files_of_the_three_original_arms_are_unchanged(self):
        for name, digest in ORIGINAL_FILES.items():
            with self.subTest(name=name):
                self.assertEqual(sha256((ROOT / 'evalsuite' / name).read_bytes()), digest)

    @unittest.skipUnless((ROOT / '.git').exists() and shutil.which('git'), 'needs a git checkout (Mac only)')
    def test_manifest_matches_git_ls_tree_and_show(self):
        """Regenerate the manifest from the repository itself, with git, and compare it with the stored one."""
        def git(*args):
            return subprocess.run(['git', '-C', str(ROOT), *args], check=True, capture_output=True).stdout
        commit = git('rev-parse', '--verify', COMMIT + '^{commit}').decode().strip()
        self.assertEqual(commit, COMMIT)
        files = {}
        for entry in git('ls-tree', '-r', '-z', commit, '--', MANIFEST['folder']).split(b'\0'):
            if entry:
                meta, path = entry.split(b'\t', 1)
                self.assertEqual(meta.split()[1], b'blob')
                relative = path.decode().removeprefix(MANIFEST['folder'] + '/')
                files[relative] = sha256(git('show', f'{commit}:{path.decode()}'))
        self.assertEqual(files, MANIFEST['files'])
        blob = git('rev-parse', f'{commit}:{MANIFEST["folder"]}/{AUDIT}').decode().strip()
        self.assertEqual(blob, AUDIT_BLOB)


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName[:35] + '-', dir=TEST_RUN))
        patch = mock.patch.object(run_blind, 'toolchain_python', return_value=None)
        patch.start()
        self.addCleanup(patch.stop)

    def run_campaign(self, campaign, policy=perfect, **options):
        options.setdefault('arms_file', ARMS_FILE)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as errors:
            code = campaign.run(policy, **options)
        return code, errors.getvalue()

    def staged_skills(self, campaign, **options):
        """Run every skill arm of the fidelity arms file and return what each agent's skill folder held."""
        seen = {}

        def agent(call):
            skill = call['cwd'] / 'skill'
            if skill.is_dir():
                seen.setdefault(call['arm'], []).append((folder_files(skill), run_blind.tree_hash(skill)))
            return perfect(call)
        arms = ','.join(('plain',) + PLAIN_ARMS + (OPT_IN,))
        code, errors = self.run_campaign(campaign, agent, arms=arms, **options)
        self.assertEqual(code, 0, errors)
        return seen

    def test_original_arms_see_the_original_skill_and_the_opt_in_arm_the_profile(self):
        campaign = FamilyCampaign(self.root, dossiers=1, families=['approvals'])
        seen = self.staged_skills(campaign)
        self.assertNotIn('plain', seen, 'the arm without the skill stages no skill folder')
        self.assertEqual(sorted(seen), sorted(PLAIN_ARMS + (OPT_IN,)))
        current = folder_files(SKILL)
        for arm in PLAIN_ARMS:
            with self.subTest(arm=arm):
                self.assertEqual(len(seen[arm]), 2, 'a correct and a mixed report')
                for files, _ in seen[arm]:
                    self.assertEqual(files, MANIFEST['files'], f'{arm} must see the 569a4d89 skill, file for file')
        self.assertEqual(len(seen[OPT_IN]), 2)
        self.assertEqual(seen[OPT_IN][0], seen[OPT_IN][1])
        files, _ = seen[OPT_IN][0]
        self.assertEqual(files, current)
        self.assertEqual(sorted(name for name in files if run_blind.is_source_bound_path(name)), profile_files())
        self.assertTrue(set(profile_files()) <= set(files))
        self.assertNotEqual(files[AUDIT], MANIFEST['files'][AUDIT])
        self.assertEqual({name for name in files if files[name] != MANIFEST['files'].get(name)},
                         set(profile_files()) | {AUDIT})
        # The campaign records the skill as the original arms saw it, and the whole folder for the opt-in arm.
        stored = json.loads((campaign.out / 'campaign.json').read_text())
        self.assertEqual(stored['skill_sha256'], seen[PLAIN_ARMS[0]][0][1])
        self.assertEqual(stored['skill_source_bound_sha256'], seen[OPT_IN][0][1])
        self.assertNotEqual(stored['skill_sha256'], stored['skill_source_bound_sha256'])

    def test_a_campaign_of_the_original_arms_records_what_it_always_recorded(self):
        campaign = Campaign(self.root, dossiers=1)
        seen = {}

        def agent(call):
            if (call['cwd'] / 'skill').is_dir():
                seen.setdefault(call['arm'], []).append(folder_files(call['cwd'] / 'skill'))
            return perfect(call)
        code, errors = self.run_campaign(campaign, agent, arms_file=run_blind.HERE / 'arms.json')
        self.assertEqual(code, 0, errors)
        self.assertEqual(sorted(seen), ['skill-lean', 'skill-no-lean'])
        for arm, listed in seen.items():
            self.assertEqual(len(listed), 2, arm)
            for files in listed:
                self.assertEqual(files, MANIFEST['files'], arm)
        stored = json.loads((campaign.out / 'campaign.json').read_text())
        self.assertNotIn('skill_source_bound_sha256', stored)
        self.assertEqual(stored['skill_sha256'], run_blind.skill_tree_hash({'skill': True}, SKILL))
        # A campaign that already holds these original arms resumes with the same record and starts nothing.
        calls = len(campaign.calls)
        code, errors = self.run_campaign(campaign, perfect, arms_file=run_blind.HERE / 'arms.json')
        self.assertEqual((code, len(campaign.calls)), (0, calls), errors)

    def test_probe_stages_the_same_skill_as_a_review(self):
        for arm, expected in (('skill-lean', MANIFEST['files']), (OPT_IN, None)):
            with self.subTest(arm=arm):
                campaign = Campaign(self.root / arm, dossiers=1)
                observed = []

                def launch(command, cwd, env, seconds, log):
                    observed.append(folder_files(Path(cwd) / 'skill'))
                    return {'ended': 'exit_0', 'exit_code': 0, 'seconds': 0}
                overrides = {'launch': launch, 'node': 'node', 'ala': {'entry': '/fake/ala.mjs'},
                             'probe': lambda context: [run_blind.probe_arm(context, context['arms'][arm])]}
                with mock.patch.object(run_blind.sys, 'platform', 'linux'), \
                        mock.patch.object(run_blind, 'assert_simulated_agent', return_value='/fake/codex'), \
                        redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    run_blind.main(campaign.argv('probe', arms=arm, arms_file=ARMS_FILE), overrides)
                self.assertEqual(observed, [expected if expected is not None else folder_files(SKILL)])

    def test_opt_in_arm_needs_a_skill_folder_with_the_profile(self):
        campaign = Campaign(self.root, dossiers=1)
        bare = campaign.root / 'skill'
        bare.mkdir()
        (bare / 'SKILL.md').write_text('Test skill\n')
        code, errors = self.run_campaign(campaign, arms=OPT_IN, skill=bare)
        self.assertEqual(code, 1, errors)
        self.assertIn('source_bound', errors)
        self.assertFalse(campaign.calls)

    def test_a_folder_without_the_profile_is_staged_as_it_is(self):
        """An arm that does not opt in never gets a helper substituted into a folder that has no profile."""
        campaign = Campaign(self.root, dossiers=1)
        custom = campaign.root / 'skill'
        (custom / 'scripts').mkdir(parents=True)
        (custom / 'SKILL.md').write_text('Test skill\n')
        (custom / AUDIT).write_text('print("custom helper")\n')
        staged = []
        code, errors = self.run_campaign(
            campaign, lambda call: staged.append(folder_files(call['cwd'] / 'skill')) or perfect(call),
            arms='skill-lean', skill=custom)
        self.assertEqual(code, 0, errors)
        self.assertEqual(staged, [folder_files(custom)] * 2)
        self.assertEqual(staged[0][AUDIT], sha256(b'print("custom helper")\n'))

    def test_view_never_writes_through_a_link_into_the_real_skill(self):
        skill = self.root / 'skill'
        (skill / 'scripts').mkdir(parents=True)
        (skill / 'SKILL.md').write_text('Test skill\n')
        (skill / 'scripts/source_bound_audit.py').write_text('# profile\n')
        outside = self.root / 'outside.py'
        outside.write_text('# not to be touched\n')
        (skill / AUDIT).symlink_to(outside)
        with run_blind.skill_folder({'id': 'skill-lean'}, skill) as view:
            self.assertEqual(sorted(folder_files(view)), ['SKILL.md', AUDIT])
            self.assertFalse((view / AUDIT).is_symlink())
            self.assertEqual((view / AUDIT).read_bytes(), (BASELINE / 'markdown_audit.py').read_bytes())
            view_dir = view.parent
        self.assertFalse(view_dir.exists(), 'the copy is removed after staging')
        self.assertEqual(outside.read_text(), '# not to be touched\n')
        self.assertTrue((skill / 'scripts/source_bound_audit.py').is_file())
        with run_blind.skill_folder({'id': OPT_IN, 'source_bound': True}, skill) as view:
            self.assertEqual(view, skill)

    def test_view_keeps_the_original_file_mode_and_leaves_the_source_alone(self):
        before = folder_files(SKILL)
        with run_blind.skill_folder({'id': 'skill-lean'}, SKILL) as view:
            for name in MANIFEST['files']:
                self.assertEqual(os.stat(view / name).st_mode & 0o777, os.stat(SKILL / name).st_mode & 0o777, name)
        self.assertEqual(folder_files(SKILL), before)


class ArmSpecTests(unittest.TestCase):
    def arms_file(self, **changes):
        root = Path(tempfile.mkdtemp(prefix='arms-', dir=TEST_RUN))
        shutil.copytree(ROOT / 'evalsuite/prompts', root / 'prompts')
        data = json.loads(ARMS_FILE.read_text())
        data['arms'] = [dict(spec, **changes) if spec['id'] == OPT_IN else spec for spec in data['arms']]
        path = root / 'arms.json'
        path.write_text(json.dumps(data))
        return path

    def test_fifth_arm_is_the_lean_arm_plus_the_profile_and_changes_no_other_arm(self):
        original = run_blind.load_arms()
        arms = run_blind.load_arms(ARMS_FILE)
        self.assertEqual(list(arms), ['plain', 'skill-no-lean', 'skill-lean', 'skill-lean-interface', OPT_IN])
        for name, arm in original.items():
            self.assertEqual(arms[name], arm)
            self.assertNotIn('source_bound', arms[name])
        self.assertNotIn('source_bound', arms['skill-lean-interface'])
        fifth, lean = arms[OPT_IN], arms['skill-lean']
        self.assertEqual((fifth['skill'], fifth['lean'], fifth['source_bound']), (True, True, True))
        self.assertNotIn('fidelity_interface', fifth)
        self.assertEqual(fifth['task'], lean['task'])
        self.assertNotEqual(fifth['fingerprint'], lean['fingerprint'])
        prompt = (ROOT / 'evalsuite/prompts/skill-lean-source-bound.md').read_text()
        self.assertTrue(prompt.startswith((ROOT / 'evalsuite/prompts/skill-lean.md').read_text()))
        self.assertIn('--formalization-profile source-bound-v1', prompt)
        self.assertIn('skill/references/source-bound.md', prompt)
        self.assertIn('--formalization-profile', run_blind.instructions(fifth, 30, '/lean'))
        self.assertNotIn('--formalization-profile', run_blind.instructions(lean, 30, '/lean'))

    def test_opt_in_needs_a_boolean_and_both_skill_and_lean(self):
        for changes in ({'source_bound': 'true'}, {'source_bound': 1}, {'lean': False}, {'skill': False}):
            with self.subTest(changes=changes):
                with self.assertRaises(run_blind.RunError):
                    run_blind.load_arms(self.arms_file(**changes))
        self.assertTrue(run_blind.load_arms(self.arms_file())[OPT_IN]['source_bound'])
        self.assertNotIn('source_bound', run_blind.load_arms(self.arms_file(source_bound=False))[OPT_IN])

    def test_profile_paths_are_recognised_exactly(self):
        yes = ['scripts/source_bound_audit.py', 'scripts/source_bound_x.py', 'assets/source_bound',
               'assets/source_bound/Types.lean', 'references/source-bound.md']
        no = ['scripts/markdown_audit.py', 'scripts/source_bound_x.txt', 'scripts/sub/source_bound_x.py',
              'assets/source_bound.lean', 'assets/EvidenceLogic.lean', 'references/source_bound.md',
              'references/formalization.md', 'SKILL.md']
        self.assertEqual([name for name in yes if run_blind.is_source_bound_path(name)], yes)
        self.assertEqual([name for name in no if run_blind.is_source_bound_path(name)], [])


if __name__ == '__main__':
    unittest.main()
