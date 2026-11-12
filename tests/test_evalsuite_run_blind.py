"""Checks of the blind runner and the paired comparison, with a stand-in agent in place of ALA and a model.

Each test runs a whole campaign on generated cases: staging, launch, answer sheet, bundle, records.
The stand-in decides what the "agent" hands in, so the tests can show what a right sheet, a wrong
sheet, a missing sheet or a hostile sheet does to the result.
"""
from contextlib import redirect_stdout
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
import blindness  # noqa: E402
import compare  # noqa: E402
import run_blind  # noqa: E402
import scorecard  # noqa: E402
from evalsuite_campaign_fixture import ARMS, Campaign, credulous, echoing, perfect, sheet  # noqa: E402

TEST_ROOT = ROOT / 'tmp'
TEST_ROOT.mkdir(exist_ok=True)
TEST_RUN = Path(tempfile.mkdtemp(prefix='evalsuite-run-blind-tests-', dir=TEST_ROOT))
REAL_TOOLCHAIN_PYTHON = run_blind.toolchain_python
SYSTEM_PYTHON = mock.patch.object(run_blind, 'toolchain_python', lambda *args, **kwargs: None)


def setUpModule():
    # Every campaign here runs as on a host with a system interpreter, whatever this host has installed; the tests of
    # the Python part patch in a toolchain interpreter of their own.
    SYSTEM_PYTHON.start()


def tearDownModule():
    SYSTEM_PYTHON.stop()


class CampaignCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName[:40] + '-', dir=TEST_RUN))

    def score(self, campaign, arm):
        base = campaign.out / arm
        return scorecard.score(base / 'runs', campaign.keys, agent_roots=[campaign.arena, campaign.lean.parent.parent,
                                                                           campaign.home],
                               records=base / 'records', thresholds={'require_isolated_keys': True,
                                                                     'require_no_contamination_flags': True,
                                                                     'require_full_coverage': True})

    def compare(self, campaign, baseline, candidate, **options):
        keys = scorecard.load_keys(campaign.keys)
        reviews = {arm: compare.arm_reviews(campaign.out, arm, keys) for arm in (baseline, candidate)}
        return compare.compare(reviews[baseline], reviews[candidate], bootstrap=1000, **options)


class BlindCampaignTests(CampaignCase):
    def test_plan_stages_nothing_and_starts_no_agent(self):
        campaign = Campaign(self.root)
        self.assertEqual(run_blind.main(campaign.argv('plan')), 0)
        self.assertEqual(list(campaign.arena.iterdir()), [])
        self.assertFalse(campaign.out.exists())

    def test_every_arm_is_staged_blind_and_its_sheet_is_scored(self):
        campaign = Campaign(self.root)
        self.assertEqual(campaign.run(perfect), 0)
        self.assertEqual(len(campaign.calls), 3 * 2 * 3)
        canary = json.loads((campaign.keys / blindness.MANIFEST_NAME).read_text())['canary']
        arms = run_blind.load_arms()
        self.assertEqual(sorted({call['arm'] for call in campaign.calls}), sorted(arms))
        for call in campaign.calls:
            command, spec = call['command'], arms[call['arm']]
            # Each review was launched with the set-up of the arm it is recorded under.
            self.assertEqual('--folder' in command, spec['lean'])
            self.assertEqual((call['cwd'] / 'skill/SKILL.md').is_file() or 'skill/SKILL.md' in call['staged'], spec['skill'])
            self.assertEqual(call['instructions'], run_blind.instructions(spec, 30, str(campaign.lean)))
            self.assertEqual(command[command.index('--cwd') + 1], str(call['cwd']))
            self.assertTrue(blindness.within(call['cwd'], campaign.arena))
            # Only the Lean arm gets a second mount, and it is the toolchain: the repository is never mounted.
            folders = [command[i + 1] for i, word in enumerate(command) if word == '--folder']
            self.assertEqual(folders, [str(campaign.lean.parent.parent.resolve())] if call['arm'] == 'skill-lean' else [])
            self.assertEqual(any(name.startswith('skill/') for name in call['staged']), call['arm'] != 'plain')
            self.assertEqual(str(campaign.lean) in call['instructions'], call['arm'] == 'skill-lean')
            self.assertNotIn('{{', call['instructions'])
            self.assertTrue(blindness.within(call['home'], campaign.arena))
            self.assertEqual(call['home_files'], ['.codex/auth.json'])
            # The staged folder names neither the dossier nor the variant and holds no key material.
            self.assertNotIn(call['dossier'], str(call['cwd']))
            for name in call['staged']:
                data = (call['cwd'] / name).read_bytes() if (call['cwd'] / name).exists() else b''
                self.assertNotIn(canary.encode(), data)
            self.assertNotIn('report-correct', ' '.join(call['staged']))
            self.assertNotIn('report-mixed', ' '.join(call['staged']))
        self.assertEqual(list(campaign.arena.iterdir()), [], 'every review is moved out of the arena when it ends')
        self.assertEqual({record['status'] for record in campaign.units().values()}, {'scored'})
        for arm in ARMS.split(','):
            card = self.score(campaign, arm)
            self.assertEqual(card['semantic']['all_claims']['accuracy']['rate'], 1.0, arm)
            self.assertEqual(card['blindness']['status'], 'isolated_as_attested')
            self.assertEqual(card['blindness']['contamination']['runs_without_stage_record'], [])
            self.assertTrue(card['gate']['passed'], card['gate'])
            self.assertEqual(card['evidence']['expected_anchor_recall']['rate'], 1.0)
        result = self.compare(campaign, 'plain', 'skill-lean')
        self.assertEqual(result['difference']['accuracy']['value'], 0.0)
        self.assertEqual((result['claims_only_candidate_right'], result['claims_only_baseline_right']), (0, 0))

    def test_instructions_differ_between_arms_only_in_the_way_of_working(self):
        arms = run_blind.load_arms()
        texts = {name: run_blind.instructions(arm, 40, '/opt/lean/bin/lean') for name, arm in arms.items()}
        for name, arm in arms.items():
            before, _, rest = texts[name].partition('## How to work')
            _, _, after = rest.partition('## Answer sheet')
            self.assertEqual(before, texts['plain'].partition('## How to work')[0])
            self.assertEqual(after, texts['plain'].partition('## Answer sheet')[2])
            self.assertIn('40 minutes', after)
            self.assertEqual('/opt/lean/bin/lean' in texts[name], arm['lean'])
        self.assertEqual(len({arm['fingerprint'] for arm in arms.values()}), 3)

    def test_a_review_without_a_sheet_fails_and_its_claims_count_as_wrong(self):
        campaign = Campaign(self.root)
        self.assertEqual(campaign.run(lambda call: perfect(call) if call['arm'] != 'plain' else {'exit_code': 0}), 0)
        plain = [r for r in campaign.units().values() if r['arm'] == 'plain']
        self.assertEqual({(r['status'], r['sheet']) for r in plain}, {('failed', 'missing')})
        self.assertFalse(any(r['suspect'] for r in plain), 'an agent that ends normally without a sheet is not an outage')
        result = self.compare(campaign, 'plain', 'skill-lean')
        self.assertEqual(result['baseline']['failed'], 6)
        self.assertEqual(result['baseline']['accuracy']['count'], 0)
        self.assertEqual(result['baseline']['accuracy']['of'], result['candidate']['accuracy']['of'])
        self.assertEqual(result['candidate']['accuracy']['rate'], 1.0)
        # Silence is not a good answer: an arm that hands in nothing must not look careful.
        for measure in ('false_support', 'false_refutation'):
            self.assertEqual(result['baseline'][measure]['rate'], 1.0, measure)
            self.assertEqual(result['candidate'][measure]['rate'], 0.0, measure)
            self.assertGreater(result['difference'][measure]['value'], -1.01)
            self.assertLess(result['difference'][measure]['value'], 0)
        self.assertEqual(result['baseline']['error_detection']['count'], 0)
        self.assertEqual(result['baseline']['claims_unanswered'], result['claims'])
        self.assertEqual(result['candidate']['claims_unanswered'], 0)
        # The same holds for the strict reading of a review that ran into the time limit.
        reversed_result = self.compare(campaign, 'skill-lean', 'plain')
        self.assertEqual(reversed_result['candidate']['false_support']['rate'], 1.0)
        # The scorer on its own lists these runs as missing and refuses the gate.
        with self.assertRaises(scorecard.ScoreError):
            self.score(campaign, 'plain')

    def test_a_sheet_at_the_time_limit_is_scored_and_the_strict_reading_fails_it(self):
        campaign = Campaign(self.root)
        late = lambda call: dict(perfect(call), ended='timeout', exit_code=-15) if call['arm'] == 'skill-lean' else perfect(call)  # noqa: E731
        self.assertEqual(campaign.run(late), 0)
        lean = [r for r in campaign.units().values() if r['arm'] == 'skill-lean']
        self.assertEqual({(r['status'], r['ended']) for r in lean}, {('scored', 'timeout')})
        self.assertEqual(self.compare(campaign, 'plain', 'skill-lean')['candidate']['accuracy']['rate'], 1.0)
        strict = self.compare(campaign, 'plain', 'skill-lean', strict=True)
        self.assertEqual(strict['candidate']['accuracy']['count'], 0)
        self.assertEqual(strict['baseline']['accuracy']['rate'], 1.0)

    def test_a_proof_reported_by_an_arm_without_lean_is_not_recorded_as_one(self):
        campaign = Campaign(self.root, dossiers=1)

        def boastful(call):
            answer = sheet(call)
            for claim in answer['claims']:
                claim['formal_status'] = 'proved' if claim['semantic_state'] == 'supported' else 'refuted'
            return {'sheet': answer}
        self.assertEqual(campaign.run(boastful), 0)
        for record in campaign.units().values():
            findings = json.loads((campaign.out / record['arm'] / 'runs' / record['opaque_id'] / 'findings.json').read_text())
            formal = {finding['formal_status'] for finding in findings}
            if record['arm'] == 'skill-lean':
                self.assertEqual(record['formal_claims_stripped'], 0)
                self.assertTrue(formal <= {'proved', 'refuted'})
            else:
                self.assertEqual(record['formal_claims_stripped'], len(findings))
                self.assertEqual(formal, {'not_attempted'})

    def test_a_sheet_that_is_a_link_or_not_a_sheet_is_never_read(self):
        campaign = Campaign(self.root, dossiers=1)
        key_file = next(path for path in campaign.keys.glob('*.json') if path.name != blindness.MANIFEST_NAME)

        def hostile(call):
            target = call['cwd'] / run_blind.SHEET
            if call['arm'] == 'plain':
                os.symlink(key_file, target)                 # try to make the runner read the answer key
            elif call['arm'] == 'skill-no-lean':
                target.write_text('{"claims": "all of them"', encoding='utf-8')
            else:
                target.write_text(json.dumps({'claims': [{'claim_id': 'C01', 'semantic_state': 'definitely'}, 7]}), encoding='utf-8')
            return {}
        self.assertEqual(campaign.run(hostile), 0)
        by_arm = {r['arm']: r for r in campaign.units().values() if r['variant'] == 'mixed'}
        self.assertEqual((by_arm['plain']['status'], by_arm['plain']['sheet']), ('failed', 'unreadable'))
        self.assertEqual((by_arm['skill-no-lean']['status'], by_arm['skill-no-lean']['sheet']), ('failed', 'unreadable'))
        self.assertEqual((by_arm['skill-lean']['status'], by_arm['skill-lean']['sheet'],
                          by_arm['skill-lean']['sheet_rows_dropped']), ('failed', 'malformed', 2))
        for record in by_arm.values():
            self.assertFalse((campaign.out / record['arm'] / 'runs' / record['opaque_id'] / 'findings.json').exists())

    def test_editing_the_staged_copies_does_not_change_what_is_scored(self):
        campaign = Campaign(self.root, dossiers=1)

        def tamper(call):
            (call['cwd'] / 'case/report.md').write_text('<a id="C01"></a>\n**C01.** Everything is fine.\n', encoding='utf-8')
            for path in (call['cwd'] / 'case/sources').glob('*.md'):
                path.write_text('<a id="L-02"></a>\n[L-02] rewritten\n', encoding='utf-8')
            return {'sheet': {'claims': [{'claim_id': 'C01', 'semantic_state': 'supported'}]}}
        self.assertEqual(campaign.run(tamper, arms='plain'), 0)
        for record in campaign.units().values():
            bundle = campaign.out / 'plain/runs' / record['opaque_id']
            original = (campaign.fixtures / record['dossier'] / f'report-{record["variant"]}.md').read_text()
            self.assertEqual(json.loads((bundle / 'input/report-ledger.json').read_text()), run_blind.paragraphs(original))
            self.assertIn('case/report.md', record['supplied_files_changed'])
            self.assertGreater(record['claims_in_report'], 1)
            for path in (bundle / 'input/sources').glob('*.md'):
                self.assertEqual(path.read_bytes(), (campaign.fixtures / record['dossier'] / 'sources' / path.name).read_bytes())
        keys = scorecard.load_keys(campaign.keys)
        reviews = compare.arm_reviews(campaign.out, 'plain', keys)
        claims = compare.collapse(reviews, sorted(reviews))
        self.assertEqual(sum(claim['observed'] == 'missing' for claim in claims), len(claims) - 2)

    def test_a_review_is_never_run_twice_and_an_interrupted_one_is_closed_as_failed(self):
        campaign = Campaign(self.root, dossiers=1)

        def interrupted(call):
            if len(campaign.calls) == 2:
                (call['cwd'] / run_blind.SHEET).write_text(json.dumps(sheet(call)), encoding='utf-8')
                raise KeyboardInterrupt
            return perfect(call)
        with self.assertRaises(KeyboardInterrupt):
            campaign.run(interrupted)
        self.assertFalse((campaign.out / 'runner.lock').exists(), 'the lock is released when the runner is stopped')
        self.assertEqual(len(campaign.units()), 1)
        self.assertEqual(campaign.run(perfect), 0)
        records = campaign.units()
        self.assertEqual(len(records), 6)
        self.assertEqual(len(campaign.calls), 2 + 4, 'the interrupted review was not started again')
        cut = [r for r in records.values() if r['ended'] == 'interrupted']
        self.assertEqual([(r['status'], r['sheet']) for r in cut], [('failed', 'not_read')])
        self.assertEqual(list(campaign.arena.iterdir()), [])
        self.assertEqual(campaign.run(perfect), 0)
        self.assertEqual(len(campaign.calls), 6, 'a finished campaign starts nothing')

    def test_a_campaign_cannot_continue_under_other_settings(self):
        campaign = Campaign(self.root, dossiers=1)
        argv = campaign.argv('run', arms='plain', skip_probe=True)
        argv[argv.index('--model'):argv.index('--model') + 2] = ['--model=-x']      # a model ALA would read as an option
        overrides = {'launch': campaign.launcher(perfect), 'ala': {'entry': '/stand-in/bin/ala.mjs'}, 'node': 'node'}
        self.assertEqual(run_blind.main(argv, overrides), 1)
        self.assertEqual(list(campaign.arena.iterdir()), [], 'refused before anything was staged')
        self.assertEqual(campaign.run(perfect, arms='plain'), 0)
        for change in ({'minutes': 45}, {'model': 'another-model'}, {'repeats': 2}, {'arms': 'plain,skill-lean'}, {'seed': 2}):
            options = dict({'arms': 'plain'}, **change)
            self.assertEqual(campaign.run(perfect, **options), 1, change)
        (campaign.fixtures / sorted(campaign.summary['dossiers'])[0] / 'sources/extra.md').write_text('new\n', encoding='utf-8')
        self.assertEqual(campaign.run(perfect, arms='plain'), 1, 'a changed source is a changed campaign')
        self.assertEqual(len(campaign.calls), 2)

    def test_layouts_that_put_answers_within_reach_are_refused(self):
        campaign = Campaign(self.root, dossiers=1)
        inside = campaign.arena / 'nested'
        for options in ({'out': inside}, {'arena': campaign.root}, {'arena': campaign.fixtures / 'arena'},
                        {'arena': campaign.keys / 'arena'}, {'agent_home': campaign.out / 'home'}):
            if 'agent_home' in options:
                (options['agent_home'] / '.codex').mkdir(parents=True)
                (options['agent_home'] / '.codex/auth.json').write_text('{}', encoding='utf-8')
            self.assertEqual(campaign.run(perfect, **options), 1, options)
        self.assertEqual(campaign.calls, [])
        # Unsealed keys carry no canary, so nothing could show a leak afterwards.
        self.assertEqual(campaign.run(perfect, keys=campaign.draft), 1)

    def test_each_review_gets_its_own_home_and_nothing_it_writes_reaches_the_next(self):
        campaign = Campaign(self.root, dossiers=1)
        seen = []

        def chatty(call):
            codex = call['home'] / '.codex'
            seen.append({name: (call['home'] / name).read_text() for name in call['home_files']})
            (codex / 'sessions').mkdir()
            (codex / 'sessions/history.jsonl').write_text(f'what I concluded about {call["variant"]}\n', encoding='utf-8')
            # What a client unpacks into a home of its own: a runtime and caches, of no use as a record.
            (call['home'] / '.cache/runtime/bin').mkdir(parents=True)
            (call['home'] / '.cache/runtime/bin/node').write_bytes(b'\0' * 4096)
            (call['home'] / '.cache/runtime/package.json').write_text('{}', encoding='utf-8')
            os.symlink('/etc', call['home'] / '.cache/elsewhere')
            # A message for the next review, hidden in the one file a client may legitimately rewrite.
            (codex / 'auth.json').write_text(f'{{"token": "x", "note": "the {call["variant"]} report says C03 is wrong"}}\n',
                                             encoding='utf-8')
            return perfect(call)
        self.assertEqual(campaign.run(chatty, arms='plain,skill-lean'), 0)
        original = {'.codex/auth.json': '{"token": "made-up-login"}\n'}
        self.assertEqual(seen, [original] * 4, 'every review starts from the same login and sees nothing of another')
        self.assertEqual((campaign.home / '.codex/auth.json').read_text(), original['.codex/auth.json'])
        for record in campaign.units().values():
            self.assertEqual(record['login_files_changed'], ['.codex/auth.json'])
            kept = campaign.out / record['arm'] / 'runs' / record['opaque_id'] / 'agent-home'
            self.assertTrue((kept / '.codex/sessions/history.jsonl').is_file())
            self.assertFalse((kept / '.codex/auth.json').exists(), 'no login is kept with a finished review')
            self.assertEqual(sorted(p.relative_to(kept).as_posix() for p in kept.rglob('*') if not p.is_dir()),
                             ['.cache/runtime/package.json', '.codex/sessions/history.jsonl'],
                             'only the readable record of a home is kept: no runtime, no link')
        # A login that changes between invocations is a changed campaign.
        (campaign.home / '.codex/auth.json').write_text('{"token": "another-account"}\n', encoding='utf-8')
        self.assertEqual(campaign.run(chatty, arms='plain,skill-lean'), 1)

    def test_links_left_by_an_agent_are_never_followed_after_the_review(self):
        campaign = Campaign(self.root, dossiers=1)
        outside = self.root / 'somewhere-else'
        (outside / 'case').mkdir(parents=True)
        (outside / 'auth.json').write_text('{"token": "a login that belongs to someone else"}\n', encoding='utf-8')
        (outside / 'case/report.md').write_text('not the staged report\n', encoding='utf-8')
        answers = self.root / 'answers.json'

        def redirect(call):
            answers.write_text(json.dumps(sheet(call)), encoding='utf-8')
            codex = call['home'] / '.codex'
            (codex / 'auth.json').unlink()
            codex.rmdir()
            os.symlink(outside, codex)                       # the login path now leads out of the home
            (call['cwd'] / 'verdicts-real.json').write_text('{}', encoding='utf-8')
            os.symlink(answers, call['cwd'] / run_blind.SHEET)
            return {}
        self.assertEqual(campaign.run(redirect, arms='plain'), 0)
        self.assertIn('someone else', (outside / 'auth.json').read_text(), 'a file outside the home was removed or changed')
        self.assertIn('made-up-login', (campaign.home / '.codex/auth.json').read_text(), 'the template took a file from outside')
        for record in campaign.units().values():
            self.assertEqual((record['status'], record['sheet']), ('failed', 'unreadable'))
            self.assertEqual(record['login_files_changed'], ['.codex/auth.json'])
            self.assertFalse(os.path.lexists(campaign.out / 'plain/runs' / record['opaque_id'] / 'agent-home'),
                             'a home that held only a link leaves nothing behind')
        self.assertTrue((outside / 'case/report.md').is_file())

    def test_permissions_an_agent_takes_away_do_not_stop_the_campaign(self):
        campaign = Campaign(self.root, dossiers=1)

        def lock_up(call):
            outcome = perfect(call)
            (call['cwd'] / run_blind.SHEET).write_text(json.dumps(outcome['sheet']), encoding='utf-8')
            (call['cwd'] / 'private').mkdir()
            (call['cwd'] / 'private/notes.md').write_text('mine\n', encoding='utf-8')
            os.chmod(call['cwd'] / 'private/notes.md', 0)
            os.chmod(call['cwd'] / 'private', 0)
            os.chmod(call['cwd'] / 'case/report.md', 0)
            os.chmod(call['cwd'] / run_blind.SHEET, 0)
            os.chmod(call['home'] / '.codex/auth.json', 0)
            os.chmod(call['home'] / '.codex', 0o555)
            os.chmod(call['cwd'], 0o555)
            return {}
        self.assertEqual(campaign.run(lock_up, arms='plain,skill-lean'), 0)
        records = campaign.units()
        self.assertEqual(len(records), 4)
        self.assertEqual({(r['status'], r['sheet']) for r in records.values()}, {('scored', 'valid')})
        self.assertEqual({tuple(r['supplied_files_changed']) for r in records.values()}, {()})
        self.assertEqual(list(campaign.arena.iterdir()), [])
        for record in records.values():
            bundle = campaign.out / record['arm'] / 'runs' / record['opaque_id']
            self.assertEqual((bundle / 'agent/private/notes.md').read_text(), 'mine\n')
            self.assertFalse((bundle / 'agent-home').exists(), 'a home that held only the login leaves nothing behind')

    @unittest.skipIf(os.geteuid() == 0, 'root can read files of any mode')
    def test_the_runner_works_when_it_cannot_read_the_answer_keys(self):
        campaign = Campaign(self.root, dossiers=1)
        keys = [path for path in campaign.keys.glob('*.json') if path.name != blindness.MANIFEST_NAME]
        for path in keys:
            os.chmod(path, 0)
        try:
            self.assertEqual(campaign.run(perfect, arms='plain'), 0)
            self.assertEqual({r['status'] for r in campaign.units().values()}, {'scored'})
            # The bytes of a sealed key are still refused in a staged folder, by the hashes the manifest recorded.
            os.chmod(keys[0], 0o600)
            leaked = campaign.fixtures / sorted(campaign.summary['dossiers'])[0] / 'sources/leaked.json'
            # Strip the canary so only the hash can catch it.
            data = keys[0].read_bytes()
            os.chmod(keys[0], 0)
            leaked.write_bytes(data.replace(blindness.CANARY_PREFIX.encode(), b'NOT-A-CANARY-'))
            other = Campaign(self.root / 'second', dossiers=1)
            manifest = json.loads((other.keys / blindness.MANIFEST_NAME).read_text())
            plain_key = next(path for path in other.keys.glob('*.json') if path.name != blindness.MANIFEST_NAME)
            (other.fixtures / plain_key.stem / 'sources/copy.json').write_bytes(plain_key.read_bytes())
            self.assertIn(plain_key.name, manifest['files'])
            self.assertEqual(other.run(perfect, arms='plain'), 1, 'a sealed key among the sources is refused')
            self.assertEqual(other.calls, [])
        finally:
            for path in keys:
                os.chmod(path, 0o600)

    def test_shared_agent_state_must_be_accepted_and_is_reported(self):
        campaign = Campaign(self.root, dossiers=1, home=False)
        self.assertEqual(campaign.run(perfect, arms='plain'), 1)
        self.assertEqual(campaign.calls, [])
        self.assertEqual(campaign.run(perfect, arms='plain,skill-lean', shared_agent_state=True), 0)
        self.assertTrue(all('--home' not in call['command'] for call in campaign.calls))
        self.assertEqual(json.loads((campaign.out / 'campaign.json').read_text())['agent_state'], 'shared_with_other_sessions')
        self.assertEqual(compare.main(['--campaign', str(campaign.out), '--keys', str(campaign.keys),
                                       '--baseline', 'plain', '--candidate', 'skill-lean', '--bootstrap', '1000',
                                       '--output', str(self.root / 'comparison.json')]), 0)
        written = json.loads((self.root / 'comparison.json').read_text())['comparisons'][0]
        self.assertTrue(written['exploratory'])
        self.assertTrue(any('shared the agent state' in text for text in written['warnings']))
        self.assertTrue(any('without a passed sandbox probe' in text for text in written['warnings']))

    def test_an_arm_that_keeps_crashing_stops_the_campaign(self):
        campaign = Campaign(self.root)
        crash = lambda call: {'ended': 'exit_nonzero', 'exit_code': 5} if call['arm'] == 'plain' else perfect(call)  # noqa: E731
        self.assertEqual(campaign.run(crash), 2)
        crashed = [r for r in campaign.units().values() if r['arm'] == 'plain']
        self.assertEqual(len(crashed), run_blind.SUSPECT_LIMIT)
        self.assertTrue(all(r['status'] == 'failed' and r['suspect'] for r in crashed))
        started = len(campaign.calls)
        self.assertLess(started, 18)
        # The outage is over, but the campaign does not quietly carry on with one arm already charged for it.
        self.assertEqual(campaign.run(perfect), 1)
        self.assertEqual(len(campaign.calls), started)
        self.assertIn('plain', json.loads((campaign.out / 'stopped.json').read_text())['reason'])

    def test_a_failed_probe_starts_no_review_and_a_passed_one_is_recorded(self):
        campaign = Campaign(self.root, dossiers=1)
        verdict = lambda passed: lambda context: [{'arm': arm, 'passed': passed, 'evidence': 'none',  # noqa: E731
                                                   'checks': [{'check': 'keys_hidden', 'status': 'pass' if passed else 'fail'}]}
                                                  for arm in context['arm_ids']]
        overrides = {'launch': campaign.launcher(perfect), 'ala': {'entry': '/stand-in/bin/ala.mjs'}, 'node': 'node'}
        self.assertEqual(run_blind.main(campaign.argv('run'), dict(overrides, probe=verdict(False))), 1)
        self.assertEqual(campaign.calls, [])
        self.assertEqual(run_blind.main(campaign.argv('run'), dict(overrides, probe=verdict(True))), 0)
        self.assertEqual(len(campaign.calls), 6)
        self.assertEqual([p['status'] for p in json.loads((campaign.out / 'probes.json').read_text())], ['failed', 'passed'])

    def test_a_second_runner_is_refused_while_the_first_is_alive(self):
        campaign = Campaign(self.root, dossiers=1)
        campaign.out.mkdir()
        (campaign.out / 'runner.lock').write_text(f'{os.getpid()} now\n', encoding='utf-8')
        self.assertEqual(campaign.run(perfect), 1)
        self.assertEqual(campaign.calls, [])
        (campaign.out / 'runner.lock').write_text('999999999 long ago\n', encoding='utf-8')
        self.assertEqual(campaign.run(perfect, arms='plain'), 0, 'a lock whose process is gone is taken over')


class SheetAndProbeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName[:40] + '-', dir=TEST_RUN))

    def run_probe_script(self, arm, hidden, home_files=(), python=None, seatbelt=None, path_first=None, env=None):
        """Run the probe script on this machine, outside any sandbox, and return its rows. `path_first` is a folder
        put first on PATH (stand-ins for ls or head); `env` adds variables."""
        run, home = self.root / 'arena/run-1', self.root / 'home'
        (run / 'case/sources').mkdir(parents=True, exist_ok=True)
        (run / 'case/report.md').write_text('report\n', encoding='utf-8')
        home.mkdir(exist_ok=True)
        for name, text in home_files:
            (home / name).write_text(text, encoding='utf-8')
        for leftover in ('probe-finished', 'probe-results.tsv'):
            if (run / leftover).exists():
                (run / leftover).unlink()
        script = self.root / 'codex'
        script.write_text(run_blind.probe_script(run.resolve(), run_blind.load_arms()[arm], hidden, None,
                                                 blindness.CANARY_PREFIX, python, seatbelt), encoding='utf-8')
        path = (str(path_first) + os.pathsep if path_first else '') + os.environ['PATH']
        subprocess.run(['/bin/sh', str(script), 'app-server'], cwd=str(run), env=dict({'HOME': str(home), 'PATH': path}, **(env or {})),
                       check=True, capture_output=True, timeout=60)
        rows = dict(line.split('\t')[:2] for line in (run / 'probe-results.tsv').read_text().splitlines())
        return rows, (run / 'probe-results.tsv').read_text()

    def test_the_probe_script_really_reports_what_it_can_see(self):
        visible, absent = self.root / 'keys', self.root / 'no-such-folder'
        visible.mkdir()
        rows, text = self.run_probe_script('plain', [('keys_hidden', visible.resolve()), ('records_hidden', absent)])
        self.assertEqual((rows['keys_hidden'], rows['records_hidden']), ('fail', 'pass'))
        self.assertEqual((rows['cwd'], rows['case_readable'], rows['run_writable'], rows['no_canary'], rows['skill_absent'],
                          rows['finished']), ('pass',) * 6)
        # Outside a sandbox the folder beside the run folder is writable, and the probe says so.
        self.assertEqual(rows['outside_write_refused'], 'fail')
        required = run_blind.probe_checks(run_blind.load_arms()['plain'], [('keys_hidden', visible)], None)
        self.assertFalse(run_blind.evaluate_probe(text, required, 'mnt:[1]')['passed'])
        rows, _ = self.run_probe_script('plain', [('records_hidden', absent)],
                                        home_files=[('notes.txt', 'copied: ' + blindness.CANARY_PREFIX + 'abc')])
        self.assertEqual(rows['no_canary'], 'fail')
        (self.root / 'home/notes.txt').unlink()
        (self.root / 'arena/run-1/skill').mkdir()
        rows, _ = self.run_probe_script('plain', [('records_hidden', absent)])
        self.assertEqual((rows['no_canary'], rows['skill_absent']), ('pass', 'fail'))

    def test_what_an_agent_leaves_is_read_only_through_real_folders(self):
        inside, outside = self.root / 'run', self.root / 'outside'
        (inside / 'case').mkdir(parents=True)
        outside.mkdir()
        (inside / 'case/report.md').write_text('staged\n', encoding='utf-8')
        (outside / 'secret.md').write_text('elsewhere\n', encoding='utf-8')
        self.assertEqual(run_blind.read_plain(inside, 'case/report.md', 100), b'staged\n')
        self.assertIsNone(run_blind.read_plain(inside, 'case/report.md', 3), 'larger than the limit')
        self.assertIsNone(run_blind.read_plain(inside, 'case/absent.md', 100))
        self.assertIsNone(run_blind.read_plain(inside, '../outside/secret.md', 100))
        self.assertIsNone(run_blind.read_plain(inside, 'case', 100), 'a folder is not a file')
        os.symlink(outside / 'secret.md', inside / 'case/link.md')
        os.symlink(outside, inside / 'elsewhere')
        os.mkfifo(inside / 'pipe')
        self.assertIsNone(run_blind.read_plain(inside, 'case/link.md', 100))
        self.assertIsNone(run_blind.read_plain(inside, 'elsewhere/secret.md', 100))
        self.assertIsNone(run_blind.read_plain(inside, 'pipe', 100), 'a pipe must not block the runner')
        self.assertFalse(run_blind.unlink_inside(inside, 'elsewhere/secret.md'))
        self.assertTrue((outside / 'secret.md').is_file())
        self.assertTrue(run_blind.unlink_inside(inside, 'case/link.md'))
        self.assertTrue((outside / 'secret.md').is_file(), 'removing a link removes the link, not its target')
        (inside / run_blind.SHEET).write_bytes(b'\xef\xbb\xbf' + json.dumps({'claims': [{'claim_id': 'C01', 'semantic_state': 'refuted'}]}).encode())
        self.assertEqual(run_blind.read_sheet(inside)['status'], 'valid', 'a byte-order mark does not spoil a sheet')

    def test_sheet_rows_that_cannot_be_used_are_dropped_and_counted(self):
        (self.root / run_blind.SHEET).write_text(json.dumps([
            {'claim_id': 'C01', 'semantic_state': 'refuted'}, {'claim_id': 'C02', 'semantic_state': 'maybe'},
            {'claim_id': '', 'semantic_state': 'supported'}, {'semantic_state': 'supported'}, 'C05',
            {'claim_id': 'C06', 'semantic_state': ['supported']}]), encoding='utf-8')
        read = run_blind.read_sheet(self.root)
        self.assertEqual((read['status'], [row['claim_id'] for row in read['rows']], read['dropped']), ('valid', ['C01'], 5))
        (self.root / run_blind.SHEET).write_text('x' * (run_blind.SHEET_LIMIT + 1), encoding='utf-8')
        self.assertEqual(run_blind.read_sheet(self.root)['status'], 'unreadable')
        self.assertEqual(run_blind.read_sheet(self.root / 'absent')['status'], 'missing')

    def test_cited_sources_are_named_as_the_bundle_names_them(self):
        for given in ('case/sources/ledger.md', './case/sources/ledger.md', 'sources/ledger.md',
                      '/arena/run-1/case/sources/ledger.md', 'case\\sources\\ledger.md'):
            self.assertEqual(run_blind.source_path(given), 'input/sources/ledger.md', given)
        self.assertEqual(run_blind.source_path('case/sources/annex/a.md'), 'input/sources/annex/a.md')
        self.assertEqual(run_blind.source_path('ledger.md'), 'ledger.md')
        findings, stripped = run_blind.findings_from(
            [{'claim_id': 'C01', 'semantic_state': 'refuted', 'formal_status': 'refuted', 'evidence': [
                {'path': 'case/sources/ledger.md', 'anchor': 'L-02'}, 'ledger', {'anchor': 'L-03'}]},
             {'claim_id': 'C99', 'semantic_state': 'supported', 'formal_status': 'certain'}], {'C01': 'C01'}, lean=True)
        self.assertEqual(stripped, 0)
        self.assertEqual(findings[0]['evidence'], [{'path': 'input/sources/ledger.md', 'anchor': 'L-02', 'quote': ''}])
        self.assertEqual((findings[1]['report_anchor'], findings[1]['formal_status']), ('unlisted:C99', 'not_attempted'))

    def test_the_probe_script_looks_for_everything_that_must_stay_hidden(self):
        arms = run_blind.load_arms()
        hidden = [('keys_hidden', '/keys/sealed'), ('records_hidden', '/campaign/out')]
        lean = {'path': '/opt/lean/bin/lean', 'root': '/opt/lean'}
        plain = run_blind.probe_script('/arena/run-1', arms['plain'], hidden, lean, blindness.CANARY_PREFIX)
        for expected in ("hidden keys_hidden '/keys/sealed'", "hidden records_hidden '/campaign/out'",
                         "hidden lean_hidden '/opt/lean'", 'hidden skill_absent "$RUN/skill"', 'no_lean_on_path',
                         blindness.CANARY_PREFIX, '/proc/self/ns/mnt'):
            self.assertIn(expected, plain)
        with_lean = run_blind.probe_script('/arena/run-1', arms['skill-lean'], hidden, lean, blindness.CANARY_PREFIX)
        self.assertIn('lean_available', with_lean)
        self.assertIn('skill_usable', with_lean)
        self.assertNotIn('lean_hidden', with_lean)
        with self.assertRaises(run_blind.RunError):
            run_blind.probe_script("/arena/it's", arms['plain'], hidden, lean, blindness.CANARY_PREFIX)

    def test_a_probe_passes_only_when_every_check_passed_inside_another_namespace(self):
        arms = run_blind.load_arms()
        hidden = [('keys_hidden', '/keys')]
        required = run_blind.probe_checks(arms['plain'], hidden, None)
        good = 'namespace\tinfo\tmnt:[4026532999]\n' + ''.join(f'{name}\tpass\tok\n' for name in required)
        self.assertTrue(run_blind.evaluate_probe(good, required, 'mnt:[4026531841]')['passed'])
        # The same results from the runner's own namespace mean the script looked at the host, not at the sandbox.
        self.assertFalse(run_blind.evaluate_probe(good, required, 'mnt:[4026532999]')['passed'])
        self.assertFalse(run_blind.evaluate_probe(good, required, None)['passed'])
        self.assertFalse(run_blind.evaluate_probe(good.replace('keys_hidden\tpass', 'keys_hidden\tfail'), required,
                                                  'mnt:[4026531841]')['passed'])
        cut = '\n'.join(line for line in good.splitlines() if not line.startswith('finished'))
        self.assertFalse(run_blind.evaluate_probe(cut, required, 'mnt:[4026531841]')['passed'])
        self.assertFalse(run_blind.evaluate_probe('', required, 'mnt:[4026531841]')['passed'])
        self.assertIn('lean_available', run_blind.probe_checks(arms['skill-lean'], hidden, {'root': '/opt/lean'}))
        self.assertIn('lean_hidden', run_blind.probe_checks(arms['skill-no-lean'], hidden, {'root': '/opt/lean'}))

    def test_a_used_home_is_refused_as_a_login_template(self):
        home = self.root / 'home'
        (home / '.codex/sessions').mkdir(parents=True)
        with self.assertRaises(run_blind.RunError):
            run_blind.check_home_template(home)                      # empty
        (home / '.codex/auth.json').write_text('{}', encoding='utf-8')
        self.assertEqual(list(run_blind.check_home_template(home)), ['.codex/auth.json'])
        for number in range(run_blind.HOME_LIMIT_FILES):
            (home / '.codex/sessions' / f'{number}.jsonl').write_text('x', encoding='utf-8')
        with self.assertRaises(run_blind.RunError):
            run_blind.check_home_template(home)                      # holds history
        os.symlink(self.root, self.root / 'link-home')
        with self.assertRaises(run_blind.RunError):
            run_blind.check_home_template(self.root / 'link-home')


# What the macOS probe records inside Seatbelt, written from ALA DS006 (a nested sandbox-exec fails with
# "sandbox_apply: Operation not permitted"; signal 0 to a live outside process answers EPERM). Not a live recording.
SEATBELT_RAW = {
    'nested_sandbox': 'nested_sandbox\traw\texit=71 | sandbox-exec: sandbox_apply: Operation not permitted\n',
    'runner_signal': 'runner_signal\traw\town=0 other=1 | sh: line 1: kill: (4242) - Operation not permitted\n',
    'sentinel_read': 'sentinel_read\traw\tcontrol=ok read=1 marker=no list=1 listed=no\n',
    'environment_read': 'environment_read\traw\texit=0 | other=denied errno=1 own=found\n'}
CONFINEMENT_RAW = ('nested_sandbox', 'sentinel_read', 'runner_signal')


def host_controls(**failed):
    """The macOS host controls, all working unless named."""
    return {name: {'ok': name not in failed, 'detail': failed.get(name, 'works on the host')}
            for name in ('nested_sandbox', 'runner_signal', 'sentinel', 'environment')}


class MacOSWitnessTests(unittest.TestCase):
    """The macOS witness is decided by pure functions from recorded probe lines, so it is tested on every platform."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName[:40] + '-', dir=TEST_RUN))
        self.arms = run_blind.load_arms()
        self.hidden = [('keys_hidden', '/keys')]
        self.required = run_blind.probe_checks(self.arms['plain'], self.hidden, None, platform='darwin')
        decided = set(run_blind.SEATBELT_ROWS)
        self.good = 'namespace\tinfo\t\n' + ''.join(f'{name}\tpass\tok\n' for name in self.required if name not in decided)

    def evaluate(self, raw=SEATBELT_RAW, host=None, extra=''):
        tsv = self.good + ''.join(raw.values()) + extra
        return run_blind.evaluate_probe(tsv, self.required, None, host if host is not None else host_controls())

    def status(self, verdict, name):
        return next(check['status'] for check in verdict['checks'] if check['check'] == name)

    def test_the_macos_witness_passes_with_all_three_differentials_and_the_environment(self):
        self.assertEqual(self.required[-2:], ['confinement', 'other_process_environment_hidden'])
        verdict = self.evaluate()
        self.assertTrue(verdict['passed'], verdict['checks'])
        for name in ('confinement', 'other_process_environment_hidden', 'ran_inside_sandbox'):
            self.assertEqual(self.status(verdict, name), 'pass', name)
        self.assertIn('exists is observable', verdict['info']['existence_oracle'])
        # The Linux witness is not consulted on macOS: no namespace and no runner namespace are needed.
        self.assertNotIn('probe unknown', ' '.join(check['detail'] for check in verdict['checks']))

    def test_the_macos_witness_fails_when_any_one_differential_is_missing(self):
        for name in CONFINEMENT_RAW:
            raw = {key: line for key, line in SEATBELT_RAW.items() if key != name}
            verdict = self.evaluate(raw)
            self.assertFalse(verdict['passed'], name)
            self.assertEqual((self.status(verdict, 'confinement'), self.status(verdict, 'ran_inside_sandbox')),
                             ('fail', 'fail'), name)
            self.assertEqual(self.status(verdict, 'other_process_environment_hidden'), 'pass', name)

    def test_each_differential_needs_the_specific_refusal_inside(self):
        wrong = {
            'nested_sandbox': ['nested_sandbox\traw\texit=0 | \n',                                  # it worked: no sandbox
                               'nested_sandbox\traw\texit=127 | sh: /usr/bin/sandbox-exec: not found\n',
                               'nested_sandbox\traw\texit=126 | sandbox-exec is not executable inside\n',
                               'nested_sandbox\traw\texit=1 | sandbox-exec: invalid profile\n',
                               # Could not even start sandbox-exec: a refusal, but not of a nested sandbox.
                               'nested_sandbox\traw\texit=126 | sh: /usr/bin/sandbox-exec: Operation not permitted\n',
                               'nested_sandbox\traw\texit=127 | sh: /usr/bin/sandbox-exec: Operation not permitted\n'],
            'runner_signal': ['runner_signal\traw\town=0 other=0 | \n',                              # reached the runner
                              'runner_signal\traw\town=0 other=1 | kill: (4242) - No such process\n',  # ESRCH: wrong pid
                              'runner_signal\traw\town=1 other=1 | kill: Operation not permitted\n'],  # own control failed
            'sentinel_read': ['sentinel_read\traw\tcontrol=ok read=0 marker=yes list=1 listed=no\n',
                              'sentinel_read\traw\tcontrol=ok read=1 marker=no list=0 listed=yes\n',
                              'sentinel_read\traw\tcontrol=ok read=1 marker=no list=0 listed=no\n',
                              'sentinel_read\traw\tcontrol=fail read=1 marker=no list=1 listed=no\n',
                              'sentinel_read\traw\tread=1 marker=no list=1 listed=no\n'],
        }
        for name, lines in wrong.items():
            for line in lines:
                verdict = self.evaluate(dict(SEATBELT_RAW, **{name: line}))
                self.assertEqual(self.status(verdict, 'confinement'), 'fail', line)
                self.assertFalse(verdict['passed'], line)

    def test_the_host_controls_are_decisive(self):
        for name in ('nested_sandbox', 'runner_signal', 'sentinel'):
            verdict = self.evaluate(host=host_controls(**{name: 'fails on the host too'}))
            self.assertEqual(self.status(verdict, 'confinement'), 'fail', name)
            self.assertIn('fails on the host too', next(c['detail'] for c in verdict['checks'] if c['check'] == 'confinement'))
            self.assertFalse(verdict['passed'], name)
        verdict = self.evaluate(host=host_controls(environment='the host reader cannot see the marker'))
        self.assertEqual(self.status(verdict, 'other_process_environment_hidden'), 'fail')
        self.assertEqual(self.status(verdict, 'confinement'), 'pass')
        self.assertFalse(verdict['passed'])
        self.assertFalse(self.evaluate(host={})['passed'], 'host controls that did not run prove nothing')

    def host_fixtures(self):
        sentinel = self.root / 'campaign-probe/sentinel-host.txt'
        sentinel.parent.mkdir(exist_ok=True)
        sentinel.write_text('marker0123', encoding='utf-8')
        return {'runner_pid': os.getpid(), 'sentinel': str(sentinel), 'sentinel_marker': 'marker0123',
                'reader': str(self.root / 'reader.pl'), 'helper_pid': 4242, 'helper_name': 'EVALSUITE_PROBE_MARKER_T',
                'helper_value': 'value0123'}

    def host_stub(self, fixtures, failing=()):
        """host_run for seatbelt_host: the three commands it may run, each working unless named in `failing`."""
        seen = []

        def host_run(command, env=None):
            if command == [run_blind.SEATBELT, '-p', '(version 1)(allow default)', '/usr/bin/true']:
                name = 'nested'
            elif command == ['/bin/sh', '-c', f'kill -0 {fixtures["runner_pid"]}']:
                name = 'kill'
            elif command == ['/usr/bin/perl', fixtures['reader'], str(fixtures['helper_pid']), fixtures['helper_name']] \
                    and env.get(fixtures['helper_name']) == fixtures['helper_value']:
                name = 'reader'
            else:
                raise AssertionError(f'unexpected host command {command}')
            seen.append(name)
            if name == 'reader':       # a helper that has stopped: the reader no longer sees it
                return (0, 'other=denied errno=3 own=found') if name in failing else (0, 'other=found own=found')
            return (1, f'{name} failed') if name in failing else (0, '')
        return host_run, seen

    def test_seatbelt_host_runs_each_control_and_each_can_fail(self):
        names = ('nested_sandbox', 'runner_signal', 'sentinel', 'environment')
        working = {'ok': True, 'detail': 'the host reader sees the marker'}

        def controls(failing=(), before=working, fixtures=None):
            fixtures = fixtures or self.host_fixtures()
            stub, seen = self.host_stub(fixtures, failing)
            with mock.patch.object(run_blind, 'host_run', stub):
                host = run_blind.seatbelt_host(fixtures, before)
            return {name: host[name]['ok'] for name in names}, seen
        result, seen = controls()
        self.assertEqual(result, dict.fromkeys(names, True))
        self.assertEqual(sorted(seen), ['kill', 'nested', 'reader'])
        for failing, name in (('nested', 'nested_sandbox'), ('kill', 'runner_signal'), ('reader', 'environment')):
            result, _ = controls(failing=(failing,))
            self.assertEqual(result, dict(dict.fromkeys(names, True), **{name: False}), failing)
        result, _ = controls(before={'ok': False, 'detail': 'the host reader cannot see the marker'})
        self.assertEqual(result, dict(dict.fromkeys(names, True), environment=False), 'a failed read before ALA counts')
        fixtures = self.host_fixtures()
        Path(fixtures['sentinel']).unlink()
        self.assertEqual(controls(fixtures=fixtures)[0], dict(dict.fromkeys(names, True), sentinel=False), 'deleted')
        fixtures = self.host_fixtures()
        Path(fixtures['sentinel']).write_text('another text', encoding='utf-8')
        self.assertEqual(controls(fixtures=fixtures)[0], dict(dict.fromkeys(names, True), sentinel=False), 'rewritten')

    def test_the_environment_row_needs_a_refusal_and_the_own_process_control(self):
        for line in ('environment_read\traw\texit=0 | other=found own=found\n',
                     'environment_read\traw\texit=0 | other=denied errno=1 own=absent\n',
                     'environment_read\traw\texit=0 | other=absent own=found\n',
                     'environment_read\traw\texit=0 | other=denied errno=3 own=found\n',   # ESRCH: the helper is gone
                     'environment_read\traw\texit=0 | other=denied errno=22 own=found\n',
                     'environment_read\traw\texit=2 | Can\'t open perl script\n',
                     'environment_read\traw\texit=127 | /usr/bin/perl: not found\n'):
            verdict = self.evaluate(dict(SEATBELT_RAW, environment_read=line))
            self.assertEqual(self.status(verdict, 'other_process_environment_hidden'), 'fail', line)
            self.assertFalse(verdict['passed'], line)
        raw = {key: line for key, line in SEATBELT_RAW.items() if key != 'environment_read'}
        self.assertEqual(self.status(self.evaluate(raw), 'other_process_environment_hidden'), 'fail')

    def test_a_probe_that_claims_its_own_verdicts_or_printed_nothing_fails(self):
        claims = ''.join(f'{name}\tpass\tok\n' for name in ('confinement', 'other_process_environment_hidden', 'ran_inside_sandbox'))
        verdict = self.evaluate(raw={}, extra=claims)
        self.assertFalse(verdict['passed'])
        self.assertEqual([self.status(verdict, name) for name in ('confinement', 'other_process_environment_hidden',
                                                                  'ran_inside_sandbox')], ['fail'] * 3)
        bare = {key: f'{key}\tpass\tok\n' for key in SEATBELT_RAW}
        self.assertFalse(self.evaluate(raw=bare)['passed'], 'a bare "pass" is not an observation')
        self.assertFalse(run_blind.evaluate_probe('', self.required, None, host_controls())['passed'])
        # Rows the runner decides are added even when the caller's list forgot them.
        linux_list = run_blind.probe_checks(self.arms['plain'], self.hidden, None)
        verdict = run_blind.evaluate_probe(self.good + ''.join(SEATBELT_RAW.values()), linux_list, None, host_controls())
        self.assertIn('other_process_environment_hidden', [check['check'] for check in verdict['checks']])
        self.assertTrue(verdict['passed'])
        verdict = run_blind.evaluate_probe(self.good, linux_list, None, host_controls())
        self.assertFalse(verdict['passed'])

    def test_the_probe_script_records_the_macos_observations_and_unconfined_they_fail(self):
        sentinel_dir = self.root / 'campaign-probe'
        sentinel_dir.mkdir()
        sentinel = sentinel_dir / 'sentinel-test.txt'
        sentinel.write_text('marker0123', encoding='utf-8')
        seatbelt = {'runner_pid': os.getpid(), 'sentinel': str(sentinel.resolve()), 'sentinel_marker': 'marker0123',
                    'reader': str(self.root / 'no-reader-here.pl'), 'helper_pid': os.getpid(),
                    'helper_name': 'EVALSUITE_PROBE_MARKER_TEST', 'helper_value': 'value0123'}
        case = SheetAndProbeTests('run_probe_script')
        case.root = self.root
        rows, text = case.run_probe_script('plain', self.hidden, seatbelt=seatbelt)
        for name in ('nested_sandbox', 'runner_signal', 'sentinel_read', 'environment_read'):
            self.assertEqual(rows[name], 'raw', name)
        observed = {line.split('\t')[0]: line.split('\t')[2] for line in text.splitlines()}
        # Outside any sandbox the runner can be signalled and the sentinel is read and listed.
        self.assertTrue(observed['runner_signal'].startswith('own=0 other=0'), observed['runner_signal'])
        self.assertEqual(observed['sentinel_read'], 'control=ok read=0 marker=yes list=0 listed=yes')
        verdict = run_blind.evaluate_probe(text, run_blind.probe_checks(self.arms['plain'], self.hidden, None, platform='darwin'),
                                           None, host_controls())
        self.assertEqual(self.status(verdict, 'confinement'), 'fail')
        self.assertEqual(self.status(verdict, 'other_process_environment_hidden'), 'fail')
        self.assertFalse(verdict['passed'])
        with self.assertRaises(run_blind.RunError):
            run_blind.probe_script('/arena/run-1', self.arms['plain'], self.hidden, None, blindness.CANARY_PREFIX,
                                   seatbelt=dict(seatbelt, helper_value='a b'))

    def test_the_probe_needs_a_supported_sandbox(self):
        tool = self.root / 'sandbox-exec'
        tool.write_text('#!/bin/sh\n', encoding='utf-8')
        self.assertFalse(run_blind.probe_platform_ready('darwin', seatbelt=str(tool)), 'not executable')
        os.chmod(tool, 0o755)
        self.assertTrue(run_blind.probe_platform_ready('darwin', seatbelt=str(tool)))
        self.assertFalse(run_blind.probe_platform_ready('darwin', seatbelt=str(self.root / 'absent')))
        self.assertTrue(run_blind.probe_platform_ready('linux', which=lambda name: '/usr/bin/bwrap'))
        self.assertFalse(run_blind.probe_platform_ready('linux', which=lambda name: None))
        self.assertFalse(run_blind.probe_platform_ready('win32', which=lambda name: '/usr/bin/bwrap', seatbelt=str(tool)))
        with mock.patch.object(run_blind, 'probe_platform_ready', lambda: False):
            with self.assertRaises(run_blind.RunError) as caught:
                run_blind.run_probe({'arms': {}, 'arm_ids': []})
        self.assertEqual(str(caught.exception), 'The probe needs Linux with Bubblewrap or macOS with sandbox-exec, '
                                                'because ALA runs agents only inside a sandbox.')

    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('node') and os.path.isfile('/usr/bin/perl'),
                         'macOS host control: needs Node for the helper and /usr/bin/perl for the reader')
    def test_on_macos_the_host_reader_sees_a_node_helper_and_its_own_process(self):
        work, run = self.root / 'work', self.root / 'run'
        work.mkdir()
        run.mkdir()
        fixtures = run_blind.seatbelt_fixtures(work, run, shutil.which('node'))
        try:
            before = run_blind.environment_on_host(fixtures)
            self.assertTrue(before['ok'], before['detail'])
            host = run_blind.seatbelt_host(fixtures, before)
            self.assertTrue(host['runner_signal']['ok'] and host['sentinel']['ok'] and host['environment']['ok'], host)
        finally:
            run_blind.stop_helper(fixtures)
        self.assertIsNotNone(fixtures['helper'].poll(), 'the helper ends with the probe')
        # A process that has gone cannot be read: the host control then fails.
        self.assertFalse(run_blind.environment_on_host(fixtures, attempts=1)['ok'])


class StricterHiddenTests(unittest.TestCase):
    """`hidden` holds only when the path is not visible, not listed and not read, under a working control."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName[:40] + '-', dir=TEST_RUN))
        self.case = SheetAndProbeTests('run_probe_script')
        self.case.root = self.root

    def stand_in(self, name, body):
        """A stand-in for `name` first on PATH. It answers for paths with "ghost" in them and runs the real tool
        for everything else."""
        real = shutil.which(name)
        folder = self.root / f'stand-in-{name}'
        folder.mkdir(exist_ok=True)
        (folder / name).write_text(f'#!/bin/sh\ncase "$*" in *ghost*) {body};; esac\nexec {real} "$@"\n', encoding='utf-8')
        os.chmod(folder / name, 0o755)
        return folder

    def test_a_path_that_is_listed_or_read_is_not_hidden_even_when_test_e_says_no(self):
        ghost = self.root / 'ghost-keys'                      # does not exist, so test -e and -L say no
        rows, _ = self.case.run_probe_script('plain', [('keys_hidden', ghost)])
        self.assertEqual(rows['keys_hidden'], 'pass')
        rows, text = self.case.run_probe_script('plain', [('keys_hidden', ghost)],
                                                path_first=self.stand_in('ls', 'echo answers.json; exit 0'))
        self.assertEqual(rows['keys_hidden'], 'fail', text)
        self.assertIn('Listed inside the sandbox', text)
        rows, text = self.case.run_probe_script('plain', [('keys_hidden', ghost)],
                                                path_first=self.stand_in('head', 'printf x; exit 0'))
        self.assertEqual(rows['keys_hidden'], 'fail', text)
        self.assertIn('Read inside the sandbox', text)

    def test_a_refusal_proves_nothing_when_the_control_fails(self):
        folder = self.root / 'broken-ls'
        folder.mkdir()
        (folder / 'ls').write_text('#!/bin/sh\nexit 1\n', encoding='utf-8')
        os.chmod(folder / 'ls', 0o755)
        rows, text = self.case.run_probe_script('plain', [('keys_hidden', self.root / 'absent')], path_first=folder)
        self.assertEqual((rows['keys_hidden'], rows['skill_absent']), ('fail', 'fail'), text)
        self.assertIn('Control listing or read in the run folder failed', text)

    def test_the_canary_search_covers_tmpdir(self):
        private = self.root / 'private-tmp'
        private.mkdir()
        (private / 'left.txt').write_text('copied: ' + blindness.CANARY_PREFIX + 'abc', encoding='utf-8')
        rows, _ = self.case.run_probe_script('plain', [('records_hidden', self.root / 'absent')], env={'TMPDIR': str(private)})
        self.assertEqual(rows['no_canary'], 'fail')
        rows, _ = self.case.run_probe_script('plain', [('records_hidden', self.root / 'absent')])
        self.assertEqual(rows['no_canary'], 'pass')


class PythonToolchainTests(CampaignCase):
    """The installed Python toolchain changes mounts, instructions and records; a system interpreter changes nothing."""

    def toolchain(self, version='3.12.16'):
        root = self.root / 'toolchains/python-3.12.16-test'
        (root / 'bin').mkdir(parents=True, exist_ok=True)
        (root / 'bin/python3').write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        os.chmod(root / 'bin/python3', 0o755)
        return {'path': str(root.resolve() / 'bin/python3'), 'root': str(root.resolve()), 'version': version}

    def test_a_system_interpreter_leaves_everything_byte_identical(self):
        path = run_blind.HERE / 'arms.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        task = (path.parent / data['task']).read_text(encoding='utf-8')
        arms = run_blind.load_arms(path, None)
        for spec in data['arms']:
            method = (path.parent / spec['prompt']).read_text(encoding='utf-8')
            old = run_blind.sha_bytes(json.dumps([run_blind.RUNNER_VERSION, spec['id'], spec['skill'], spec['lean'],
                                                  method, task]).encode())
            self.assertEqual(arms[spec['id']]['fingerprint'], old, spec['id'])
            self.assertEqual(arms[spec['id']]['task'], task)
            self.assertNotIn('wherever these instructions', run_blind.instructions(arms[spec['id']], 30, '/opt/lean/bin/lean'))
        self.assertEqual(run_blind.ala_arguments('/ala/bin/ala.mjs', {'agent': 'codex'}, '/arena/run-1', '/opt/lean', '/arena/home-1',
                                                 'session-1', None),
                         ['/ala/bin/ala.mjs', '--ca', 'codex', '--config', '/arena/run-1/no-ala-config.json', '--cwd',
                          '/arena/run-1', '--home', '/arena/home-1', '--folder', '/opt/lean', '--session-id', 'session-1',
                          '--taskFile', '/arena/run-1/instructions.md', '--output', '/arena/run-1/final-message.md'])
        script = run_blind.probe_script('/arena/run-1', arms['skill-no-lean'], [('keys_hidden', '/keys')], None, blindness.CANARY_PREFIX)
        self.assertIn(' && python3 -B "$RUN/skill/scripts/markdown_audit.py"', script)
        for absent in ('PY=', 'python_available', 'bare_python3'):
            self.assertNotIn(absent, script)
        self.assertNotIn('python_available', run_blind.probe_checks(arms['skill-no-lean'], [], None))
        campaign = Campaign(self.root, dossiers=1)
        self.assertEqual(campaign.run(perfect, arms='plain,skill-lean'), 0)
        stored = json.loads((campaign.out / 'campaign.json').read_text())
        self.assertNotIn('python', stored)
        self.assertEqual(stored['arms'], {name: arms[name]['fingerprint'] for name in ('plain', 'skill-lean')})
        for call in campaign.calls:
            folders = [call['command'][i + 1] for i, word in enumerate(call['command']) if word == '--folder']
            self.assertEqual(folders, [str(campaign.lean.parent.parent.resolve())] if call['arm'] == 'skill-lean' else [])

    def test_the_toolchain_interpreter_is_mounted_named_and_recorded_for_every_arm(self):
        python = self.toolchain()
        sentence = f'Use {python["path"]} wherever these instructions or the skill say python3.'
        campaign = Campaign(self.root, dossiers=1)
        printed = io.StringIO()
        with mock.patch.object(run_blind, 'toolchain_python', lambda *args, **kwargs: python), redirect_stdout(printed):
            self.assertEqual(campaign.run(perfect), 0)
        self.assertEqual(len(campaign.calls), 6)
        for call in campaign.calls:
            folders = [call['command'][i + 1] for i, word in enumerate(call['command']) if word == '--folder']
            lean = [str(campaign.lean.parent.parent.resolve())] if call['arm'] == 'skill-lean' else []
            self.assertEqual(folders, lean + [python['root']], call['arm'])
            self.assertTrue(call['instructions'].endswith(sentence + '\n'), call['instructions'][-200:])
            self.assertEqual(call['instructions'].count(sentence), 1)
        stored = json.loads((campaign.out / 'campaign.json').read_text())
        self.assertEqual(stored['python'], python)
        system = run_blind.load_arms()
        with_python = run_blind.load_arms(python=python)
        for name, fingerprint in stored['arms'].items():
            self.assertEqual(fingerprint, with_python[name]['fingerprint'])
            self.assertNotEqual(fingerprint, system[name]['fingerprint'], 'the sentence is part of the fingerprint')
        # The interpreter's folder is listed as agent-readable, so the scorer's blindness check covers it too.
        readable = printed.getvalue().split('Agent-readable folders: ', 1)[1].split('\n', 1)[0].rstrip('.').split(', ')
        self.assertIn(python['root'], readable)
        # The same campaign without the toolchain is another campaign.
        self.assertEqual(campaign.run(perfect), 1)

    def test_the_toolchain_folder_is_checked_like_the_lean_toolchain(self):
        campaign = Campaign(self.root, dossiers=1)
        for root in (campaign.arena / 'python', self.root, campaign.fixtures):
            python = {'path': str(root / 'bin/python3'), 'root': str(root), 'version': '3.12.16'}
            Path(root).mkdir(parents=True, exist_ok=True)
            with mock.patch.object(run_blind, 'toolchain_python', lambda *args, **kwargs: python):
                self.assertEqual(campaign.run(perfect, arms='plain'), 1, root)
        self.assertEqual(campaign.calls, [])
        python = self.toolchain()
        with mock.patch.object(run_blind, 'toolchain_python', lambda *args, **kwargs: python):
            self.assertEqual(run_blind.main(campaign.argv('plan', arms='plain')), 0)

    def test_the_probe_runs_the_toolchain_interpreter_and_checks_its_version(self):
        python = self.toolchain()
        arms = run_blind.load_arms(python=python)
        script = run_blind.probe_script('/arena/run-1', arms['skill-no-lean'], [('keys_hidden', '/keys')], None,
                                        blindness.CANARY_PREFIX, python)
        self.assertIn(f"PY='{python['path']}'", script)
        self.assertIn(' && "$PY" -B "$RUN/skill/scripts/markdown_audit.py"', script)
        self.assertIn(f"'{python['root']}' 2>/dev/null | head -n 5", script, 'the canary search covers the toolchain')
        self.assertIn('record bare_python3 info', script)
        required = run_blind.probe_checks(arms['skill-no-lean'], [], None, python)
        self.assertIn('python_available', required)
        good = 'namespace\tinfo\tmnt:[2]\n' + ''.join(f'{name}\tpass\tok\n' for name in required if name != 'python_available')
        good += 'bare_python3\tinfo\t/usr/bin/python3 3.9.6\n'
        verdict = run_blind.evaluate_probe(good + 'python_available\traw\texit=0 version=3.12.16\n', required, 'mnt:[1]', None, python)
        self.assertTrue(verdict['passed'], verdict['checks'])
        self.assertEqual(verdict['info']['bare_python3'], '/usr/bin/python3 3.9.6')
        for line in ('python_available\traw\texit=0 version=3.9.6\n', 'python_available\tpass\tok\n',
                     'python_available\traw\texit=1 version=Traceback\n', 'python_available\traw\texit=0 version=3.12.16 extra\n', ''):
            self.assertFalse(run_blind.evaluate_probe(good + line, required, 'mnt:[1]', None, python)['passed'], line)
        # Run unconfined with a stand-in interpreter that prints the version: the raw line is what the runner reads.
        (Path(python['path'])).write_text('#!/bin/sh\necho 3.12.16\n', encoding='utf-8')
        case = SheetAndProbeTests('run_probe_script')
        case.root = self.root
        rows, text = case.run_probe_script('plain', [('records_hidden', self.root / 'absent')], python=python)
        self.assertEqual(rows['python_available'], 'raw')
        self.assertIn('python_available\traw\texit=0 version=3.12.16\n', text)

    def test_a_missing_or_broken_toolchain_manifest_is_a_clear_refusal(self):
        campaign = Campaign(self.root, dossiers=1)

        class Tools:
            class UnsupportedPlatformError(RuntimeError):
                pass

            def __init__(self, manifest=None, error=None):
                self.manifest, self.error = manifest, error

            def platform_key(self):
                return 'darwin-arm64'

            def load_manifest(self):
                if self.error:
                    raise self.error
                return self.manifest

        def missing():
            raise FileNotFoundError('tools/toolchain.py')
        cases = [(missing, 'tools/toolchain.py cannot be loaded'),
                 (lambda: Tools(error=ValueError('Expecting value: line 1 column 1')), 'tools/toolchain.json cannot be read'),
                 (lambda: Tools(error=FileNotFoundError('tools/toolchain.json')), 'tools/toolchain.json cannot be read'),
                 (lambda: Tools(manifest={'python': []}), 'tools/toolchain.json is malformed'),
                 (lambda: Tools(manifest=[]), 'tools/toolchain.json is malformed'),
                 (lambda: Tools(manifest={'python': {'platforms': []}}), 'tools/toolchain.json is malformed'),
                 (lambda: Tools(manifest={'python': {'platforms': {'darwin-arm64': 'x'}}}), 'tools/toolchain.json is malformed'),
                 (lambda: Tools(manifest={'python': {'minimum': '3.12', 'platforms': {}}}), 'tools/toolchain.json is malformed')]
        for loader, text in cases:
            errors = io.StringIO()
            with mock.patch.object(run_blind, 'toolchain_python', REAL_TOOLCHAIN_PYTHON), \
                    mock.patch.object(run_blind, 'toolchain_module', loader), mock.patch.object(sys, 'stderr', errors), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(run_blind.main(campaign.argv('plan')), 1, text)
            self.assertIn('Cannot run: ' + text, errors.getvalue())
        # A manifest without a python section, or without an entry for this platform, is no refusal: a system
        # interpreter is used, as before the Python toolchain existed.
        for manifest in ({}, {'lean': {}}, {'python': {}}, {'python': {'platforms': {}}},
                         {'python': {'platforms': {'linux-x64': {'folder': 'python-3.12.16-linux-x64'}}}}):
            with mock.patch.object(run_blind, 'toolchain_python', REAL_TOOLCHAIN_PYTHON), \
                    mock.patch.object(run_blind, 'toolchain_module', lambda: Tools(manifest=manifest)), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(run_blind.main(campaign.argv('plan')), 0, manifest)

    def test_the_resolver_picks_the_toolchain_only_as_tools_toolchain_mjs_does(self):
        repo = self.root / 'repo'
        manifest = {'python': {'version': '3.12.16', 'minimum': [3, 12], 'platforms': {'darwin-arm64': {
            'asset': 'cpython-3.12.16-test.tar.gz', 'folder': 'python-3.12.16-darwin-arm64', 'sha256': 'a' * 64}}}}
        root = repo / 'tmp/toolchains/python-3.12.16-darwin-arm64'
        (root / 'bin').mkdir(parents=True)
        (root / 'bin/python3').write_text('#!/bin/sh\n', encoding='utf-8')
        os.chmod(root / 'bin/python3', 0o755)
        marker = root.with_name('python-3.12.16-darwin-arm64.installed.json')
        resolve = lambda env=None, version='3.12.16', **changes: REAL_TOOLCHAIN_PYTHON(  # noqa: E731
            env or {}, repo, changes.get('manifest', manifest), 'darwin-arm64', lambda path: version)
        self.assertIsNone(resolve(), 'no install marker')
        marker.write_text(json.dumps({'asset': 'cpython-3.12.16-test.tar.gz', 'sha256': 'a' * 64}), encoding='utf-8')
        self.assertEqual(resolve(), {'path': str(root.resolve() / 'bin/python3'), 'root': str(root.resolve()), 'version': '3.12.16'})
        self.assertIsNone(resolve(version='3.11.9'), 'below the minimum')
        self.assertIsNone(resolve(version=None), 'does not run')
        unpinned = json.loads(json.dumps(manifest))
        unpinned['python']['platforms']['darwin-arm64']['sha256'] = None
        self.assertIsNone(resolve(manifest=unpinned), 'a null pin never matches')
        placeholder = json.loads(json.dumps(manifest))
        placeholder['python']['platforms']['darwin-arm64']['folder'] = 'python-3.12.<PATCH>-darwin-arm64'
        self.assertIsNone(resolve(manifest=placeholder))
        self.assertIsNone(resolve({'AUDIT_PYTHON': '/usr/bin/python3'}), 'AUDIT_PYTHON decides: a system interpreter')
        self.assertEqual(resolve({'AUDIT_PYTHON': str(root / 'bin/python3')})['root'], str(root.resolve()))
        marker.write_text(json.dumps({'asset': 'another.tar.gz', 'sha256': 'a' * 64}), encoding='utf-8')
        self.assertIsNone(resolve(), 'a marker for another asset')
        self.assertIsNone(REAL_TOOLCHAIN_PYTHON({}, repo, manifest, 'linux-x64', lambda path: '3.12.16'), 'no entry')


# A stand-in for ALA's discovery and login modules with ALA's order for Codex: CODEX_BIN when executable, then
# <HOME>/.local/bin/codex, then codex on PATH. With ALA_CLI set, the same tests also ask the real ALA.
FIXTURE_DISCOVERY = r"""import {access, realpath} from 'node:fs/promises';
import {constants} from 'node:fs';
import {delimiter, isAbsolute, join, resolve} from 'node:path';
import {homedir} from 'node:os';
async function executable(file) { try { await access(file, constants.X_OK); return true; } catch { return false; } }
async function find(candidate, env) {
  if (isAbsolute(candidate) || candidate.includes('/')) return await executable(resolve(candidate)) ? realpath(resolve(candidate)) : null;
  for (const folder of String(env.PATH || '').split(delimiter).filter(Boolean)) {
    if (await executable(resolve(folder, candidate))) return realpath(resolve(folder, candidate));
  }
  return null;
}
export async function discoverCodingAgents({env = process.env} = {}) {
  for (const candidate of [env.CODEX_BIN, join(env.HOME || homedir(), '.local', 'bin', 'codex'), 'codex'].filter(Boolean)) {
    const binary = await find(candidate, env);
    if (binary) return [{name: 'codex', available: true, binary}];
  }
  return [{name: 'codex', available: false, binary: null}];
}
"""
FIXTURE_LOGINS = "export function homeEnvironment(env, home) { return {...env, HOME: home, CODEX_HOME: home + '/.codex'}; }\n"
NODE = shutil.which('node')


class SimulatedAgentGuardTests(unittest.TestCase):
    """The probe starts ALA only when ALA's own discovery names the probe script as the program it would run."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName[:40] + '-', dir=TEST_RUN))
        self.assertTrue(NODE, 'node is not on PATH; the guard asks ALA through Node, and ALA needs it as well')
        self.marker = self.root / 'a-real-agent-ran'
        self.fake_bin = self.root / 'fake-bin'
        self.fake_bin.mkdir()
        self.write_fake(self.fake_bin / 'codex')

    def write_fake(self, path):
        """A program named codex that only leaves a marker: it stands for a real, logged-in coding agent."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(f"#!/bin/sh\necho ran > '{self.marker}'\n", encoding='utf-8')
        os.chmod(path, 0o755)

    def fixture_ala(self, discovery=FIXTURE_DISCOVERY, logins=FIXTURE_LOGINS):
        root = self.root / f'ala-{len(list(self.root.glob("ala-*")))}'
        (root / 'bin').mkdir(parents=True)
        (root / 'bin/ala.mjs').write_text('// stand-in entry; never run\n', encoding='utf-8')
        (root / 'src/coding-agents').mkdir(parents=True)
        if discovery is not None:
            (root / 'src/coding-agents/discovery.mjs').write_text(discovery, encoding='utf-8')
        (root / 'src/coding-agents/logins.mjs').write_text(logins, encoding='utf-8')
        return str(root / 'bin/ala.mjs')

    def entries(self):
        """The stand-in ALA, and the real one when ALA_CLI names a checkout."""
        found = [self.fixture_ala()]
        real = os.environ.get('ALA_CLI')
        if real and (Path(real) / 'src/coding-agents/discovery.mjs').is_file():
            found.append(str(Path(real) / 'bin/ala.mjs'))
        return found

    def shim(self, mode=0o755):
        tools = self.root / 'probe-bin'
        tools.mkdir(exist_ok=True)
        shim = tools / 'codex'
        shim.write_text('#!/bin/sh\n# Simulated coding agent for the model-free blindness probe.\nexit 0\n', encoding='utf-8')
        os.chmod(shim, mode)
        return shim

    def environment(self, home):
        return {'PATH': f'{self.fake_bin}{os.pathsep}/usr/bin{os.pathsep}/bin', 'HOME': str(home)}

    def test_the_guard_names_the_probe_script_when_discovery_does(self):
        home = self.root / 'home'
        self.write_fake(home / '.local/bin/codex')
        shim = self.shim()
        for entry in self.entries():
            env = dict(self.environment(self.root / 'host-home'), CODEX_BIN=str(shim))
            self.assertEqual(run_blind.assert_simulated_agent(NODE, entry, shim, env, home, self.root), os.path.realpath(shim), entry)
        self.assertFalse(self.marker.exists())

    def test_a_probe_script_that_cannot_run_is_refused_because_ala_would_fall_back(self):
        home = self.root / 'home'
        self.write_fake(home / '.local/bin/codex')
        shim = self.shim(0o644)                          # as on a noexec mount: ALA skips it
        for entry in self.entries():
            env = dict(self.environment(self.root / 'host-home'), CODEX_BIN=str(shim))
            with self.assertRaises(run_blind.RunError) as caught:          # the copied home's codex
                run_blind.assert_simulated_agent(NODE, entry, shim, env, home, self.root)
            self.assertIn('ALA would start a real coding agent', str(caught.exception))
            self.assertIn(str((home / '.local/bin/codex').resolve()), str(caught.exception))
            with self.assertRaises(run_blind.RunError) as caught:          # no home: codex on PATH
                run_blind.assert_simulated_agent(NODE, entry, shim, env, None, self.root)
            self.assertIn(str((self.fake_bin / 'codex').resolve()), str(caught.exception))
        self.assertFalse(self.marker.exists(), 'the guard never starts a program')

    def test_discovery_that_names_another_program_refuses_the_probe(self):
        shim = self.shim()
        env = dict(self.environment(self.root), CODEX_BIN=str(shim))
        for named in (str(self.fake_bin / 'codex'), None):
            with mock.patch.object(run_blind, 'ala_discovery', lambda *args, **kwargs: named):
                with self.assertRaises(run_blind.RunError) as caught:
                    run_blind.assert_simulated_agent(NODE, self.fixture_ala(), shim, env, None, self.root)
            self.assertTrue(str(caught.exception).startswith('The probe was not run, because ALA would start a real coding agent'))

    def test_discovery_that_cannot_be_asked_refuses_the_probe(self):
        shim = self.shim()
        env = dict(self.environment(self.root), CODEX_BIN=str(shim))
        cases = [('/no/such/node', self.fixture_ala()),
                 (NODE, self.fixture_ala(discovery=None)),                                    # module missing
                 (NODE, self.fixture_ala(discovery='console.log("hello");\n' + FIXTURE_DISCOVERY)),  # unparsable answer
                 (NODE, self.fixture_ala(discovery='export const discoverCodingAgents = () => { throw new Error("broken"); };\n')),
                 (None, self.fixture_ala())]
        for node, entry in cases:
            with self.assertRaises(run_blind.RunError, msg=(node, entry)) as caught:
                run_blind.assert_simulated_agent(node, entry, shim, env, None, self.root)
            self.assertIn('ALA would start a real coding agent', str(caught.exception))
        slow = self.fixture_ala(discovery='await new Promise(resolve => setTimeout(resolve, 30000));\n' + FIXTURE_DISCOVERY)
        with self.assertRaises(run_blind.RunError) as caught:
            run_blind.ala_discovery(NODE, slow, 'codex', env, None, self.root, timeout=2)
        self.assertIn('cannot be asked', str(caught.exception))

    def probe_campaign(self, home_codex, discovery=FIXTURE_DISCOVERY):
        """A campaign whose probe runs through probe_arm with a stand-in for ALA that starts what ALA's discovery
        order would pick: so a probe that is not refused really starts the fake agent."""
        campaign = Campaign(self.root / 'campaign', dossiers=1)
        if home_codex:
            self.write_fake(campaign.home / '.local/bin/codex')
        started = []

        def launch(command, cwd, env, seconds, log):
            home = Path(command[command.index('--home') + 1]) if '--home' in command else Path(env['HOME'])
            for candidate in (env.get('CODEX_BIN'), str(home / '.local/bin/codex'), shutil.which('codex', path=env['PATH'])):
                if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    started.append(os.path.realpath(candidate))
                    subprocess.run(['/bin/sh', candidate, 'exec'], cwd=str(cwd), env={'HOME': str(home), 'PATH': env['PATH']},
                                   capture_output=True, timeout=60)
                    break
            return {'ended': 'exit_0', 'exit_code': 0, 'seconds': 1.0}
        overrides = {'launch': launch, 'node': NODE, 'ala': {'entry': self.fixture_ala(discovery=discovery)},
                     'probe': lambda context: [run_blind.probe_arm(context, context['arms'][i]) for i in context['arm_ids']]}
        return campaign, started, overrides

    def run_probe_main(self, campaign, overrides):
        errors = io.StringIO()
        with mock.patch.dict(os.environ, self.environment(self.root / 'host-home')), redirect_stdout(io.StringIO()), \
                mock.patch.object(sys, 'stderr', errors):
            code = run_blind.main(campaign.argv('probe', arms='plain'), overrides)
        return code, errors.getvalue()

    def test_a_probe_whose_script_ala_would_skip_starts_nothing(self):
        real_chmod = os.chmod

        def noexec(path, mode, *args, **kwargs):           # the probe script lands on a mount that cannot execute
            real_chmod(path, mode, *args, **kwargs)
            if Path(path).name == 'codex' and Path(path).parent.name.startswith('probe-bin-'):
                real_chmod(path, 0o644)
        for home_codex in (True, False):
            campaign, started, overrides = self.probe_campaign(home_codex)
            with mock.patch.object(run_blind.os, 'chmod', noexec):
                code, errors = self.run_probe_main(campaign, overrides)
            self.assertFalse(self.marker.exists(), 'the fake coding agent never ran')
            self.assertEqual(started, [], 'ALA was not started')
            self.assertEqual(code, 1)
            self.assertIn('The probe was not run, because ALA would start a real coding agent', errors)
            self.assertEqual(list(campaign.arena.iterdir()), [], 'nothing, no copied login either, is left in the arena')
            shutil.rmtree(campaign.root)

    def test_a_probe_whose_script_ala_would_run_proceeds(self):
        campaign, started, overrides = self.probe_campaign(home_codex=True)
        code, errors = self.run_probe_main(campaign, overrides)
        self.assertNotIn('The probe was not run', errors)
        self.assertEqual(len(started), 1)
        self.assertTrue(started[0].endswith('/codex') and '/probe-bin-' in started[0], started)
        self.assertFalse(self.marker.exists())
        record = json.loads(next((campaign.out / 'probe').glob('plain-*/probe.json')).read_text())
        self.assertEqual(record['agent_program'], started[0])
        # Outside a sandbox the probe ran and saw the host, so it fails: but it ran.
        self.assertEqual((code, record['passed']), (1, False))
        self.assertEqual(next(c['status'] for c in record['checks'] if c['check'] == 'finished'), 'pass')

    REAL_AGENT = 'The probe was not run, because ALA would start a real coding agent'
    CHECKS_FAILED = 'The probe was not run, because the checks before ALA starts failed'

    def assert_refused_and_clear(self, campaign, started, code, errors, refusal, cause=None, left=()):
        self.assertFalse(self.marker.exists(), 'the fake coding agent never ran')
        self.assertEqual(started, [], 'ALA was not started')
        self.assertEqual(code, 1)
        self.assertIn(refusal, errors)
        self.assertNotIn('Traceback', errors)
        if cause:
            self.assertIn(cause, errors)
        self.assertEqual(sorted(entry.name for entry in campaign.arena.iterdir()), sorted(left),
                         'nothing of the probe is left in the arena')
        self.assertEqual(list(campaign.out.rglob('auth.json')), [], 'no copy of the login is kept anywhere')

    def test_a_discovery_answer_that_is_not_utf8_refuses_the_probe_and_leaves_nothing(self):
        garbled = 'process.stdout.write(Buffer.from([0xff]));\n' + FIXTURE_DISCOVERY
        campaign, started, overrides = self.probe_campaign(home_codex=True, discovery=garbled)
        code, errors = self.run_probe_main(campaign, overrides)
        self.assert_refused_and_clear(campaign, started, code, errors, self.REAL_AGENT, "ALA's discovery gave no usable answer")

    def test_any_error_before_the_launch_clears_the_arena_and_refuses_the_probe(self):
        def undecodable(*args, **kwargs):
            raise UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid start byte')
        campaign, started, overrides = self.probe_campaign(home_codex=True)
        with mock.patch.object(run_blind, 'ala_discovery', undecodable):
            code, errors = self.run_probe_main(campaign, overrides)
        self.assert_refused_and_clear(campaign, started, code, errors, self.CHECKS_FAILED, 'UnicodeDecodeError')
        self.assertNotIn(self.REAL_AGENT, errors, 'an unrelated failure is not called a real agent')
        shutil.rmtree(campaign.root)
        # A copy of the login that failed half way: open_home removes it, and the probe is refused.
        real_copytree = shutil.copytree

        def half_made(source, destination, *args, **kwargs):
            real_copytree(source, destination, *args, **kwargs)
            if Path(destination).name.startswith('home-'):
                raise OSError('disk full while copying the login')
            return destination
        campaign, started, overrides = self.probe_campaign(home_codex=False)
        with mock.patch.object(shutil, 'copytree', half_made):
            code, errors = self.run_probe_main(campaign, overrides)
        self.assert_refused_and_clear(campaign, started, code, errors, self.CHECKS_FAILED, 'disk full while copying the login')

    def test_a_refused_probe_removes_only_what_it_created(self):
        # A probe takes no campaign lock: a run on the same arena may create a home while the probe works.
        campaign, started, overrides = self.probe_campaign(home_codex=False)

        def another_runner_meanwhile(*args, **kwargs):
            (campaign.arena / 'home-other/.codex').mkdir(parents=True)
            (campaign.arena / 'home-other/.codex/auth.json').write_text('{"token": "another review"}\n', encoding='utf-8')
            raise run_blind.RunError(run_blind.PROBE_REFUSAL.format('stand-in refusal'))
        with mock.patch.object(run_blind, 'ala_discovery', another_runner_meanwhile):
            code, errors = self.run_probe_main(campaign, overrides)
        self.assertFalse(self.marker.exists())
        self.assertEqual((started, code), ([], 1))
        self.assertIn('stand-in refusal', errors)
        self.assertEqual(sorted(entry.name for entry in campaign.arena.iterdir()), ['home-other'])
        self.assertIn('another review', (campaign.arena / 'home-other/.codex/auth.json').read_text())
        self.assertEqual(list(campaign.out.rglob('auth.json')), [])

    def test_a_refused_probe_stops_its_helper_and_an_interrupt_stays_an_interrupt(self):
        # The macOS helper process, started on any platform: environment_on_host is replaced, because the reader is
        # macOS-only.
        helpers = []
        real_fixtures = run_blind.seatbelt_fixtures

        def fixtures(*args, **kwargs):
            made = real_fixtures(*args, **kwargs)
            helpers.append(made['helper'])
            self.addCleanup(lambda: made['helper'].poll() is None and made['helper'].kill())
            return made

        def interrupted(*args, **kwargs):
            raise KeyboardInterrupt
        for failure, expected in ((lambda *a, **k: None, 'refused'), (interrupted, 'interrupt')):
            campaign, started, overrides = self.probe_campaign(home_codex=False)
            with mock.patch.object(run_blind, 'sys', mock.Mock(platform='darwin', stderr=io.StringIO())), \
                    mock.patch.object(run_blind, 'seatbelt_fixtures', fixtures), \
                    mock.patch.object(run_blind, 'environment_on_host', lambda *a, **k: {'ok': True, 'detail': 'stand-in'}), \
                    mock.patch.object(run_blind, 'ala_discovery', failure), \
                    mock.patch.dict(os.environ, self.environment(self.root / 'host-home')), redirect_stdout(io.StringIO()):
                if expected == 'interrupt':
                    with self.assertRaises(KeyboardInterrupt):
                        run_blind.main(campaign.argv('probe', arms='plain'), overrides)
                else:
                    self.assertEqual(run_blind.main(campaign.argv('probe', arms='plain'), overrides), 1)
            self.assertEqual(started, [])
            self.assertIsNotNone(helpers[-1].poll(), f'the helper was stopped ({expected})')
            self.assertEqual(list(campaign.arena.iterdir()), [], expected)
            self.assertEqual(list(campaign.out.rglob('auth.json')), [], expected)
            shutil.rmtree(campaign.root)
        self.assertEqual(len(helpers), 2)

    def test_the_guard_asks_with_the_home_the_probe_passes(self):
        # Discovery names the probe script only for a HOME that holds the campaign's login, as ALA's --home makes it;
        # asked without the home it names codex on PATH.
        by_home = r"""import {existsSync} from 'node:fs';
import {realpath} from 'node:fs/promises';
import {join} from 'node:path';
export async function discoverCodingAgents({env = process.env} = {}) {
  const inHome = existsSync(join(env.HOME || '/nonexistent', '.codex', 'auth.json'));
  const binary = await realpath(inHome ? env.CODEX_BIN : join(String(env.PATH).split(':')[0], 'codex'));
  return [{name: 'codex', available: true, binary}];
}
"""
        campaign, started, overrides = self.probe_campaign(home_codex=False, discovery=by_home)
        code, errors = self.run_probe_main(campaign, overrides)
        self.assertNotIn('The probe was not run', errors)
        self.assertEqual(len(started), 1)
        self.assertIn('/probe-bin-', started[0])
        shim = self.shim()
        env = dict(self.environment(self.root / 'host-home'), CODEX_BIN=str(shim))
        with self.assertRaises(run_blind.RunError):
            run_blind.assert_simulated_agent(NODE, overrides['ala']['entry'], shim, env, None, self.root)
        self.assertFalse(self.marker.exists())


class ComparisonTests(CampaignCase):
    def test_a_better_arm_shows_as_better_with_an_interval_from_whole_dossiers(self):
        campaign = Campaign(self.root, dossiers=12)
        self.assertEqual(campaign.run(lambda call: credulous(call) if call['arm'] == 'plain' else perfect(call),
                                      arms='plain,skill-lean'), 0)
        result = self.compare(campaign, 'plain', 'skill-lean')
        keys = scorecard.load_keys(campaign.keys)
        rows = [row for key in keys.values() for row in key['rows']]
        wrong = sum(row['semantic_state'] != 'supported' for row in rows)
        self.assertEqual(result['claims'], len(rows))
        self.assertEqual(result['dossiers'], 12)
        self.assertEqual(result['baseline']['accuracy']['count'], len(rows) - wrong)
        self.assertEqual(result['baseline']['false_support'], {'count': wrong, 'of': wrong, 'rate': 1.0})
        self.assertEqual(result['baseline']['error_detection']['count'], 0)
        self.assertEqual(result['candidate']['false_support']['count'], 0)
        self.assertEqual(result['candidate']['gap_recognition']['rate'], 1.0)
        self.assertEqual(result['claims_only_candidate_right'], wrong)
        self.assertEqual(result['claims_only_baseline_right'], 0)
        self.assertEqual((result['dossiers_candidate_better'], result['dossiers_baseline_better']), (12, 0))
        self.assertAlmostEqual(result['dossier_sign_test_p'], 2 / 2 ** 12)
        low, high = result['difference']['accuracy']['ci95']
        self.assertTrue(0 < low <= result['difference']['accuracy']['value'] <= high)
        reverse = self.compare(campaign, 'skill-lean', 'plain')
        self.assertLess(reverse['difference']['accuracy']['ci95'][1], 0)
        self.assertEqual(compare.position(reverse['difference']['accuracy']['ci95']), 'below')

    def test_an_arm_that_takes_the_packet_at_its_word_shows_as_echoing(self):
        campaign = Campaign(self.root, dossiers=12, seed=15)     # a seed whose packet cases hold both kinds of echo
        self.assertEqual(campaign.run(lambda call: echoing(call) if call['arm'] == 'plain' else perfect(call),
                                      arms='plain,skill-lean'), 0)
        result = self.compare(campaign, 'plain', 'skill-lean')
        rows = [row for key in scorecard.load_keys(campaign.keys).values() for row in key['rows']]
        withheld = sum(row.get('echo_state') == 'undetermined' for row in rows)
        asserted = sum(row.get('echo_state') is not None for row in rows) - withheld
        self.assertEqual(asserted + withheld, campaign.summary['echo_claims'])
        self.assertGreater(min(asserted, withheld), 3)
        control = result['baseline']['withheld_control']['of']
        self.assertGreater(control, 3)
        self.assertEqual(result['baseline']['echo_asserted'], {'count': asserted, 'of': asserted, 'rate': 1.0})
        self.assertEqual(result['baseline']['echo_withheld'], {'count': withheld, 'of': withheld, 'rate': 1.0})
        # The arm withholds a verdict where the sheet hides a file and nowhere else: the control stays at zero.
        self.assertEqual(result['baseline']['withheld_control'], {'count': 0, 'of': control, 'rate': 0.0})
        for name, chances in (('echo_asserted', asserted), ('echo_withheld', withheld), ('withheld_control', control)):
            self.assertEqual(result['candidate'][name], {'count': 0, 'of': chances, 'rate': 0.0})
        self.assertEqual(result['difference']['echo_asserted']['value'], -1.0)
        self.assertEqual(result['baseline']['accuracy']['count'], len(rows) - asserted - withheld)
        self.assertEqual(result['claims_only_candidate_right'], asserted + withheld)
        table = compare.render(('plain', 'skill-lean'), result, None, [])
        for label in ('Packet’s word taken over its files', 'Withheld where its word is wrong', 'Withheld where its word is right'):
            self.assertIn(label, table)
        # The scorer counts the same answers, arm by arm, and both arms answered every claim.
        for arm, echoes in (('plain', (asserted, withheld)), ('skill-lean', (0, 0))):
            card = self.score(campaign, arm)
            block = card['semantic']['all_claims']
            self.assertEqual(((block['echo_asserted']['count'], block['echo_asserted']['of']),
                              (block['echo_withheld']['count'], block['echo_withheld']['of'])),
                             ((echoes[0], asserted), (echoes[1], withheld)))
            self.assertEqual((block['withheld_without_conflict']['count'], block['withheld_without_conflict']['of']),
                             (0, control))
            self.assertEqual(card['coverage']['missing_claims'], [])
        # Silence is not a careful answer here either, and each claim has a chance in one of the three at most.
        silent = {'expected': 'supported', 'echo': 'undetermined', 'kind': 'reading', 'any_echo': False,
                  'any_undetermined': False, 'all_match': False, 'any_supported': False, 'any_error': False,
                  'all_error': False, 'observed_by_repeat': ['missing']}
        chances = lambda claim, kinds=frozenset({'reading'}): {  # noqa: E731
            name: compare.tally(claim, kinds)[name] for name in ('echo_asserted', 'echo_withheld', 'withheld_control')}
        self.assertEqual(chances(silent), {'echo_asserted': (0, 0), 'echo_withheld': (1, 1), 'withheld_control': (0, 0)})
        self.assertEqual(chances(dict(silent, echo='refuted')),
                         {'echo_asserted': (1, 1), 'echo_withheld': (0, 0), 'withheld_control': (0, 0)})
        self.assertEqual(chances(dict(silent, echo=None)),
                         {'echo_asserted': (0, 0), 'echo_withheld': (0, 0), 'withheld_control': (1, 1)})
        answered = dict(silent, echo=None, observed_by_repeat=['supported'], all_match=True)
        self.assertEqual(chances(answered)['withheld_control'], (0, 1))
        self.assertEqual(chances(dict(answered, any_undetermined=True))['withheld_control'], (1, 1))
        for outside in (dict(answered, kind='listing'), dict(answered, expected='undetermined'), dict(answered, kind=None)):
            self.assertEqual(chances(outside)['withheld_control'], (0, 0))
        self.assertEqual(chances(answered, frozenset())['withheld_control'], (0, 0))

    def test_a_small_or_lopsided_campaign_is_called_exploratory(self):
        campaign = Campaign(self.root, dossiers=3)
        self.assertEqual(campaign.run(perfect, arms='plain,skill-lean'), 0)
        keys = scorecard.load_keys(campaign.keys)
        reviews = {arm: compare.arm_reviews(campaign.out, arm, keys) for arm in ('plain', 'skill-lean')}
        record = {'record': json.loads((campaign.out / 'campaign.json').read_text()), 'probes': [{'status': 'passed'}]}
        result, notes = compare.compare_pair(record, keys, reviews, 'plain', 'skill-lean', 1000, 1)
        texts = ' '.join(note['text'] for note in notes)
        self.assertTrue(result['exploratory'])
        self.assertIn('Only 3 dossier(s)', texts)
        self.assertIn('1 time(s) per arm', texts)
        self.assertNotIn('sandbox probe', texts)
        dropped = dict(reviews)
        dropped['plain'] = {label: review for label, review in reviews['plain'].items() if label[1] == 'mixed'}
        result, notes = compare.compare_pair(record, keys, dropped, 'plain', 'skill-lean', 1000, 1)
        self.assertEqual(result['reviews_compared'], 3)
        self.assertTrue(any('only one of the two arms' in note['text'] for note in notes))
        with self.assertRaises(compare.CompareError):
            compare.arm_reviews(campaign.out, 'skill-no-lean', keys)

    def test_a_sheet_that_answers_no_claim_of_the_report_does_not_look_careful(self):
        campaign = Campaign(self.root, dossiers=2)

        def other_names(call):
            answer = sheet(call)
            if call['arm'] == 'plain':
                for claim in answer['claims']:
                    claim['claim_id'] = claim['claim_id'].lower()
            return {'sheet': answer}
        self.assertEqual(campaign.run(other_names, arms='plain,skill-lean'), 0)
        self.assertEqual({record['status'] for record in campaign.units().values()}, {'scored'})
        keys = scorecard.load_keys(campaign.keys)
        reviews = {arm: compare.arm_reviews(campaign.out, arm, keys) for arm in ('plain', 'skill-lean')}
        record = {'record': json.loads((campaign.out / 'campaign.json').read_text()), 'probes': [{'status': 'passed'}]}
        result, notes = compare.compare_pair(record, keys, reviews, 'plain', 'skill-lean', 1000, 1)
        plain = result['baseline']
        self.assertEqual((plain['failed'], plain['claims_unanswered']), (0, result['claims']))
        self.assertEqual(plain['accuracy']['count'], 0)
        self.assertEqual(plain['false_support']['rate'], 1.0)
        self.assertEqual(plain['false_refutation']['rate'], 1.0)
        self.assertTrue(any('got no answer from the baseline' in note['text'] for note in notes))

    def test_an_outage_or_an_unfinished_campaign_makes_the_comparison_exploratory(self):
        campaign = Campaign(self.root, dossiers=10)
        self.assertEqual(campaign.run(perfect, arms='plain,skill-lean'), 0)
        keys = scorecard.load_keys(campaign.keys)
        reviews = {arm: compare.arm_reviews(campaign.out, arm, keys) for arm in ('plain', 'skill-lean')}
        stored = json.loads((campaign.out / 'campaign.json').read_text())
        record = {'record': stored, 'probes': [{'status': 'passed'}]}
        result, notes = compare.compare_pair(record, keys, reviews, 'plain', 'skill-lean', 1000, 1)
        self.assertFalse(result['exploratory'], [note['text'] for note in notes])
        # Three reviews of one arm are lost to a crash: their claims count as wrong, and the reading says why.
        for label in sorted(reviews['plain'])[:3]:
            review = reviews['plain'][label]
            review.update(failed=True, suspect=True, ended='exit_nonzero',
                          rows=[dict(row, observed='missing', matches=False, formal_statuses=[]) for row in review['rows']])
        result, notes = compare.compare_pair(record, keys, reviews, 'plain', 'skill-lean', 1000, 1)
        self.assertEqual((result['baseline']['failed'], result['baseline']['suspect']), (3, 3))
        self.assertLess(result['baseline']['accuracy']['rate'], 1.0)
        self.assertTrue(result['exploratory'])
        self.assertTrue(any('outage' in note['text'] and note['exploratory'] for note in notes))
        # A campaign that planned more repeats than it has is not finished.
        planned = {'record': dict(stored, repeats=3), 'probes': [{'status': 'passed'}]}
        reviews = {arm: compare.arm_reviews(campaign.out, arm, keys) for arm in ('plain', 'skill-lean')}
        result, notes = compare.compare_pair(planned, keys, reviews, 'plain', 'skill-lean', 1000, 1)
        self.assertTrue(any('not finished' in note['text'] and note['exploratory'] for note in notes))

    def test_the_sign_test_is_exact(self):
        self.assertEqual(compare.binomial_two_sided(0, 0), 1.0)
        self.assertAlmostEqual(compare.binomial_two_sided(5, 0), 2 / 32)
        self.assertAlmostEqual(compare.binomial_two_sided(3, 1), 10 / 16)
        self.assertEqual(compare.binomial_two_sided(4, 4), 1.0)

    def test_claims_are_right_only_when_every_repeat_is_right(self):
        campaign = Campaign(self.root, dossiers=1)
        flaky = lambda call: credulous(call) if call['arm'] == 'plain' and len(campaign.calls) % 2 else perfect(call)  # noqa: E731
        self.assertEqual(campaign.run(flaky, arms='plain,skill-lean', repeats=2), 0)
        result = self.compare(campaign, 'plain', 'skill-lean')
        self.assertEqual(result['repeats'], [1, 2])
        self.assertEqual(result['candidate']['accuracy']['rate'], 1.0)
        self.assertLess(result['baseline']['accuracy']['rate'], 1.0)
        self.assertGreater(result['baseline']['false_support']['count'], 0)


class AlaLocationTests(unittest.TestCase):
    """Where the runner finds ALA, and which commit it records for a copy that npm installed."""

    COMMIT = 'c' * 40

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix='ala-location-', dir=TEST_RUN)) / 'repository'
        self.installed = self.repo / 'node_modules/advanced-language-agent'
        self.write_ala(self.installed)
        # A Git repository with one commit around the copy: a `git rev-parse HEAD` inside the copy would name that commit.
        git = ['git', '-C', str(self.repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid']
        try:
            subprocess.run(git + ['init', '-q'], check=True, capture_output=True)
            subprocess.run(git + ['commit', '-q', '--allow-empty', '-m', 'around'], check=True, capture_output=True)
            self.around = subprocess.run(git + ['rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as error:
            self.skipTest('Git is needed to build the repository around the fixture: ' + str(error))
        for patch in (mock.patch.object(run_blind, 'REPO', self.repo), mock.patch.dict(os.environ)):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop('ALA_CLI', None)

    def write_ala(self, root):
        (root / 'bin').mkdir(parents=True)
        (root / 'bin/ala.mjs').write_text('', encoding='utf-8')
        (root / 'package.json').write_text(json.dumps({'name': 'advanced-language-agent', 'version': '0.1.0'}), encoding='utf-8')

    def write_lock(self, resolved, name='node_modules/.package-lock.json'):
        lock = {'packages': {'node_modules/advanced-language-agent': {'resolved': resolved}}}
        (self.repo / name).write_text(json.dumps(lock), encoding='utf-8')

    def test_installed_copy_is_found_with_the_commit_npm_recorded(self):
        self.write_lock('git+ssh://git@github.com/AssistOS-AI/AdvancedLanguageAgent.git#' + self.COMMIT)
        found = run_blind.locate_ala()
        self.assertEqual(found, {'entry': str((self.installed / 'bin/ala.mjs').resolve()), 'version': '0.1.0', 'commit': self.COMMIT})

    def test_commit_is_what_npm_installed_not_what_the_root_lock_would_install(self):
        # After a pull that moves the pin, the root lock names the new commit while node_modules still holds the old one.
        self.write_lock('git+ssh://git@github.com/AssistOS-AI/AdvancedLanguageAgent.git#' + 'd' * 40, name='package-lock.json')
        self.assertIsNone(run_blind.locate_ala()['commit'], 'the root lock alone names no installed commit')
        self.write_lock('git+ssh://git@github.com/AssistOS-AI/AdvancedLanguageAgent.git#' + self.COMMIT)
        self.assertEqual(run_blind.locate_ala()['commit'], self.COMMIT)

    def test_copy_without_git_folder_never_gets_the_commit_of_the_repository_around_it(self):
        self.assertEqual(subprocess.run(['git', '-C', str(self.installed), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip(), self.around,
                         'Git, asked inside the copy, names the commit of the repository around it')
        for lock in (None, 'https://registry.npmjs.org/advanced-language-agent/-/advanced-language-agent-0.1.0.tgz', 7):
            if lock is not None:
                self.write_lock(lock)
            self.assertIsNone(run_blind.locate_ala()['commit'], lock)
            self.assertIsNone(run_blind.locate_ala(str(self.installed))['commit'], lock)

    def test_checkout_beside_or_inside_the_repository_comes_first(self):
        self.write_lock('git+ssh://git@github.com/AssistOS-AI/AdvancedLanguageAgent.git#' + self.COMMIT)
        inside = self.repo / 'AdvancedLanguageAgent'
        self.write_ala(inside)
        self.assertEqual(run_blind.locate_ala()['entry'], str((inside / 'bin/ala.mjs').resolve()))
        beside = self.repo.parent / 'AdvancedLanguageAgent'
        self.write_ala(beside)
        self.assertEqual(run_blind.locate_ala()['entry'], str((beside / 'bin/ala.mjs').resolve()))

    def test_without_any_copy_the_runner_says_how_to_name_one(self):
        shutil.rmtree(self.installed)
        with self.assertRaises(run_blind.RunError) as raised:
            run_blind.locate_ala()
        self.assertIn('ALA was not found', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
