"""Deterministic model-free detector plumbing, not detector performance evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from evalsuite import fidelity_detectors as detectors, fidelity_mutants as mutants

ROOT = Path(__file__).resolve().parents[1]
(ROOT / 'tmp').mkdir(exist_ok=True)
RUN = Path(tempfile.mkdtemp(prefix='fidelity-detectors-', dir=ROOT / 'tmp'))


def fixture():
    run = Path(tempfile.mkdtemp(prefix='case-', dir=RUN))
    generator = run / 'evaluator-input'
    rows = []
    for index, (error, visibility) in enumerate([
            ('faithful', 'faithful'),
            *((error, visibility) for error in detectors.ERROR_CLASSES
              for visibility in ('silent', 'visible'))]):
        ident = f'opaque-{index}'
        path = generator / 'cases' / ident
        (path / 'sources').mkdir(parents=True)
        (path / 'sources' / 'document.md').write_text('[F01] The balance is 10.\n[R01] Use a positive balance.\n')
        (path / 'report.md').write_text('**C01.** The balance is positive.\n**C02.** The balance is 10.\n')
        (path / 'Formalization.lean').write_text('def balance : Nat := 10\n')
        # These must never be staged, even when beside allowed inputs.
        (path / 'private.json').write_text('EVALUATOR-ONLY-MUTATION-SENTINEL')
        rows.append({'case_id': ident, 'path': f'cases/{ident}', 'family': 'test',
                     'seed': 100, 'index': index, 'report_variant': 'mixed',
                     'error_class': error, 'visibility': visibility, 'validated': True,
                     'formalization_sha256': hashlib.sha256((path / 'Formalization.lean').read_bytes()).hexdigest(),
                     'mutation': 'EVALUATOR-ONLY-MUTATION-SENTINEL',
                     'location': None if error == 'faithful' else {
                         'kind': error, 'id': {'rule': 'R01', 'fact': 'F01', 'claim': 'C01'}[error]},
                     'claims': ['C01', 'C02']})
    (generator / 'key.json').write_text(json.dumps({'schema_version': 1, 'cases': rows}))
    return run, generator


def stage():
    run, generator = fixture()
    checker, key_path = run / 'checker', run / 'evaluator' / 'stage-key.json'
    key = detectors.stage_cases(generator, checker, key_path)
    return run, generator, checker, key_path, key


def answers_for(checker, key, faithful=False, false_alarm=False):
    cases = {row['case_id']: row for row in key['cases']}
    answers = {kind: {} for kind in key['check_kinds']}
    for unit in key['staged']:
        case = cases[unit['generator_case_id']]
        task = json.loads((checker / unit['path'] / 'task.json').read_text())
        flagged = not faithful and (case['error_class'] != 'faithful' or false_alarm)
        answers[unit['check_kind']][unit['case_id']] = detectors.stand_in_checker_for_tests(
            task, verdict='not_faithful' if flagged else 'faithful',
            location=(case['location'] or {'kind': 'fact', 'id': 'F01'}) if flagged else None)
    return answers


class StagingTests(unittest.TestCase):
    def test_deterministic_neutral_tree_and_external_key(self):
        run, generator, checker, key_path, key = stage()
        second = run / 'second-checker'
        again = detectors.stage_cases(generator, second, run / 'second-key.json')
        self.assertEqual(key, again)
        self.assertEqual(len(key['staged']), 21)
        self.assertFalse(key_path.is_relative_to(checker))
        first_bytes = {p.relative_to(checker): p.read_bytes() for p in checker.rglob('*') if p.is_file()}
        second_bytes = {p.relative_to(second): p.read_bytes() for p in second.rglob('*') if p.is_file()}
        self.assertEqual(first_bytes, second_bytes)
        for unit in key['staged']:
            directory = checker / unit['path']
            self.assertRegex(directory.name, r'^case-[a-f0-9]{24}$')
            self.assertEqual(set(unit['files']), {'sources/document.md', 'report.md',
                                                'Formalization.lean', 'task.json', 'prompt.md'})
            task = json.loads((directory / 'task.json').read_text())
            self.assertEqual(set(task), {'schema_version', 'case_id', 'check_kind', 'claims'})
        for data in first_bytes.values():
            self.assertNotIn(b'EVALUATOR-ONLY-MUTATION-SENTINEL', data)
            self.assertNotIn(b'"report_variant"', data)
            self.assertNotIn(b'"visibility"', data)
            self.assertNotIn(b'"error_class"', data)

    def test_layout_rejections_leave_existing_material_intact(self):
        run, generator = fixture()
        key_before = (generator / 'key.json').read_bytes()
        for checker, key_path in [
                (generator, run / 'key.json'),
                (generator / 'checker', run / 'key.json'),
                (run, run / 'key.json'),
                (run / 'checker', run / 'checker' / 'key.json'),
                (run / 'checker', generator / 'stage.json')]:
            with self.subTest(checker=checker, key_path=key_path), self.assertRaises(detectors.DetectorError):
                detectors.stage_cases(generator, checker, key_path)
        self.assertEqual((generator / 'key.json').read_bytes(), key_before)
        self.assertFalse((run / 'checker').exists())

    def test_refuses_reused_destination_or_key(self):
        run, generator, checker, key_path, _ = stage()
        with self.assertRaises(detectors.DetectorError):
            detectors.stage_cases(generator, checker, run / 'new-key.json')
        with self.assertRaises(detectors.DetectorError):
            detectors.stage_cases(generator, run / 'new-checker', key_path)
        self.assertFalse((run / 'new-checker').exists())

    def test_changed_or_substituted_formalization_rejected_before_any_staging(self):
        for replacement in ('def balance : Nat := 11\n', 'def unrelated : Bool := false\n'):
            run, generator = fixture()
            # Change the final input so all earlier cases have already been read.
            path = generator / 'cases' / 'opaque-6' / 'Formalization.lean'
            path.write_text(replacement)
            checker, stage_key = run / 'checker', run / 'evaluator' / 'stage.json'
            with self.subTest(replacement=replacement), self.assertRaisesRegex(detectors.DetectorError, 'hash mismatch'):
                detectors.stage_cases(generator, checker, stage_key)
            self.assertFalse(checker.exists())
            self.assertFalse(stage_key.parent.exists())

    def test_missing_or_malformed_formalization_hash_rejected(self):
        for value in (None, '', 'a' * 63, 'g' * 64, 123, ['a' * 64]):
            run, generator = fixture()
            key = json.loads((generator / 'key.json').read_text())
            if value is None:
                del key['cases'][0]['formalization_sha256']
            else:
                key['cases'][0]['formalization_sha256'] = value
            (generator / 'key.json').write_text(json.dumps(key))
            with self.subTest(value=value), self.assertRaisesRegex(detectors.DetectorError, 'formalization_sha256'):
                detectors.stage_cases(generator, run / 'checker', run / 'stage.json')
            self.assertFalse((run / 'checker').exists())

    def test_refuses_symlink_and_traversal_inputs(self):
        run, generator = fixture()
        source = generator / 'cases' / 'opaque-0' / 'sources'
        (source / 'leak.md').symlink_to(generator / 'key.json')
        with self.assertRaisesRegex(detectors.DetectorError, 'regular'):
            detectors.stage_cases(generator, run / 'checker', run / 'stage.json')
        self.assertFalse((run / 'checker').exists())
        run, generator = fixture()
        key = json.loads((generator / 'key.json').read_text())
        key['cases'][0]['path'] = 'cases/../key.json'
        (generator / 'key.json').write_text(json.dumps(key))
        with self.assertRaisesRegex(detectors.DetectorError, 'Case path'):
            detectors.stage_cases(generator, run / 'checker', run / 'stage.json')

    def test_nested_aggregate_paths_and_single_kind_cli(self):
        run, generator = fixture()
        nested = generator / 'approvals-1-mixed'
        nested.mkdir()
        (generator / 'cases').rename(nested / 'cases')
        key = json.loads((generator / 'key.json').read_text())
        key['complete'] = True
        for row in key['cases']:
            row['path'] = 'approvals-1-mixed/' + row['path']
            row['effective'] = row['error_class'] != 'faithful'
        (generator / 'key.json').write_text(json.dumps(key))
        checker, stage_key = run / 'checker', run / 'stage-key.json'
        result = subprocess.run([
            sys.executable, '-B', '-m', 'evalsuite.fidelity_detectors', 'stage',
            '--generator-root', str(generator), '--checker-root', str(checker),
            '--stage-key', str(stage_key), '--check-kind', 'back-translation'],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'staged_cases': 7, 'check_kinds': ['back-translation']})
        self.assertEqual({p.name for p in checker.iterdir()}, {'back-translation'})
        staged = json.loads(stage_key.read_text())
        answers = answers_for(checker, staged)
        answer_file = run / 'answers.json'
        answer_file.write_text(json.dumps(answers))
        result = subprocess.run([
            sys.executable, '-B', '-m', 'evalsuite.fidelity_detectors', 'score',
            '--stage-key', str(stage_key), '--answers', str(answer_file)],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        scores = json.loads(result.stdout)['by_check_kind']
        self.assertEqual(set(scores), {'back-translation'})
        self.assertEqual(scores['back-translation']['overall']['catch_rate']['rate'], 1)
        with self.assertRaises(detectors.DetectorError):
            detectors.score_answers(staged, {'semantic-review': {}})

    def test_nested_symlink_even_with_internal_target_is_rejected(self):
        run, generator = fixture()
        (generator / 'alias').symlink_to(generator / 'cases', target_is_directory=True)
        key = json.loads((generator / 'key.json').read_text())
        key['cases'][0]['path'] = 'alias/opaque-0'
        (generator / 'key.json').write_text(json.dumps(key))
        with self.assertRaisesRegex(detectors.DetectorError, 'symlinks'):
            detectors.stage_cases(generator, run / 'checker', run / 'stage.json')

    def test_pending_real_generator_output_is_not_detector_ready(self):
        run = Path(tempfile.mkdtemp(prefix='integration-pending-', dir=RUN))
        generator = run / 'generator'
        key = mutants.generate_dossier('approvals', 511, 1, 'mixed', generator)
        self.assertTrue(key['complete'])
        self.assertTrue(all(not row['validated'] for row in key['cases']))
        with self.assertRaisesRegex(detectors.DetectorError, 'validated'):
            detectors.stage_cases(generator, run / 'checker', run / 'stage.json')
        self.assertFalse((run / 'checker').exists())

    def test_incomplete_and_ineffective_inputs_rejected(self):
        for complete in (False, None, 1, 'true'):
            run, generator = fixture()
            key = json.loads((generator / 'key.json').read_text())
            key['complete'] = complete
            (generator / 'key.json').write_text(json.dumps(key))
            with self.subTest(complete=complete), self.assertRaisesRegex(detectors.DetectorError, 'Incomplete'):
                detectors.stage_cases(generator, run / 'checker', run / 'stage.json')
        for effective in (False, None, 1, 'true'):
            run, generator = fixture()
            key = json.loads((generator / 'key.json').read_text())
            key['cases'][1]['effective'] = effective
            (generator / 'key.json').write_text(json.dumps(key))
            with self.subTest(effective=effective), self.assertRaisesRegex(detectors.DetectorError, 'effective'):
                detectors.stage_cases(generator, run / 'checker', run / 'stage.json')

    def test_api_selection_and_invalid_selection(self):
        run, generator = fixture()
        key = detectors.stage_cases(generator, run / 'one', run / 'key-one.json',
                                    check_kinds='semantic-review')
        self.assertEqual(key['check_kinds'], ['semantic-review'])
        self.assertEqual(len(key['staged']), 7)
        for selection in ([], ['unknown'], ['semantic-review', 'semantic-review'], {}, 1):
            with self.subTest(selection=selection), self.assertRaises(detectors.DetectorError):
                detectors.stage_cases(generator, run / 'invalid', run / 'invalid.json', check_kinds=selection)

    def test_rejects_unvalidated_or_inconsistent_key(self):
        for field, value in [('validated', False), ('visibility', 'silent'),
                             ('claims', ['C01', 'C01']), ('claims', []),
                             ('location', {'kind': 'fact', 'id': 'F01'})]:
            with self.subTest(field=field, value=value):
                run, generator = fixture()
                key = json.loads((generator / 'key.json').read_text())
                key['cases'][0][field] = value
                (generator / 'key.json').write_text(json.dumps(key))
                with self.assertRaises(detectors.DetectorError):
                    detectors.stage_cases(generator, run / 'checker', run / 'stage.json')
                self.assertFalse((run / 'checker').exists())


class AnswerSchemaTests(unittest.TestCase):
    def test_each_method_accepts_its_own_complete_schema(self):
        for kind in detectors.CHECK_KINDS:
            task = {'case_id': 'case-123', 'check_kind': kind, 'claims': ['C01', 'C02']}
            answer = detectors.stand_in_checker_for_tests(task)
            self.assertIn('TEST ONLY', answer['reason'])
            actual = detectors.validate_answer(json.dumps(answer), **task)
            self.assertEqual(actual, answer)
            self.assertEqual(set(actual['claims'][0]), detectors.ANALYSIS_FIELDS[kind] | {'claim_id', 'agrees'})
            self.assertIn(kind, (detectors.PROMPTS / f'{kind}.md').read_text())

    def test_strict_rejection_matrix(self):
        task = {'case_id': 'case-123', 'check_kind': 'semantic-review', 'claims': ['C01', 'C02']}
        valid = detectors.stand_in_checker_for_tests(task)
        bad = [None, [], '{}', '```json\n' + json.dumps(valid) + '\n```',
               json.dumps(valid) + '\ntrailing text',
               json.dumps(valid).replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'),
               json.dumps(valid).replace('"schema_version": 1', '"schema_version": NaN')]
        for field, value in [('schema_version', True), ('schema_version', 1.0),
                             ('case_id', 'other-case'), ('check_kind', 'back-translation'),
                             ('verdict', 'unknown'), ('verdict', []), ('reason', '  '),
                             ('reason', 7), ('location', {'kind': 'fact', 'id': 'F01'}),
                             ('claims', []), ('claims', {}), ('claims', [valid['claims'][0]]),
                             ('claims', [valid['claims'][0], valid['claims'][0]])]:
            answer = deepcopy(valid)
            answer[field] = value
            bad.append(answer)
        answer = deepcopy(valid)
        answer['extra'] = 'field'
        bad.append(answer)
        for field, value in [('claim_id', 'C99'), ('analysis', ''), ('source_evidence', []),
                             ('agrees', 'true'), ('agrees', 1), ('agrees', False)]:
            answer = deepcopy(valid)
            answer['claims'][0][field] = value
            bad.append(answer)
        answer = deepcopy(valid)
        answer['claims'][0]['back_translation'] = 'wrong method'
        bad.append(answer)
        for location in [None, {}, {'kind': 'fact'}, {'kind': 'unknown', 'id': 'F01'},
                         {'kind': 'fact', 'id': ''}, {'kind': 'fact', 'id': 'F01', 'extra': 1}]:
            answer = deepcopy(valid)
            answer.update(verdict='not_faithful', location=location)
            bad.append(answer)
        for index, answer in enumerate(bad):
            with self.subTest(index=index), self.assertRaises(detectors.DetectorError):
                detectors.validate_answer(answer, **task)

    def test_rule_translation_and_claim_agreement_are_required(self):
        task = {'case_id': 'case-123', 'check_kind': 'back-translation', 'claims': ['C01']}
        valid = detectors.stand_in_checker_for_tests(task)
        for field in ('rule_translation', 'agrees'):
            answer = deepcopy(valid)
            del (answer if field == 'rule_translation' else answer['claims'][0])[field]
            with self.subTest(field=field), self.assertRaises(detectors.DetectorError):
                detectors.validate_answer(answer, **task)
        for value in (' ', [], True):
            answer = deepcopy(valid)
            answer['rule_translation'] = value
            with self.subTest(value=value), self.assertRaises(detectors.DetectorError):
                detectors.validate_answer(answer, **task)
        # A rule mismatch need not make every registered claim predicate differ.
        valid.update(verdict='not_faithful', location={'kind': 'rule', 'id': 'Candidate.rule'})
        self.assertTrue(detectors.validate_answer(valid, **task)['claims'][0]['agrees'])


class ScoringTests(unittest.TestCase):
    def test_cli_reads_per_case_answer_sheets_all_caught(self):
        _, _, checker, key_path, key = stage()
        answers = answers_for(checker, key)
        for unit in key['staged']:
            sheet = checker / unit['path'] / 'answer.json'
            sheet.write_text(json.dumps(answers[unit['check_kind']][unit['case_id']]))
            self.assertIn('Write `answer.json`', (sheet.parent / 'prompt.md').read_text())
        result = subprocess.run([
            sys.executable, '-B', '-m', 'evalsuite.fidelity_detectors', 'score',
            '--stage-key', str(key_path), '--answers-dir', str(checker)],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = json.loads(result.stdout)
        self.assertEqual(actual, detectors.score_answers(key, answers))
        both = subprocess.run([
            sys.executable, '-B', '-m', 'evalsuite.fidelity_detectors', 'score',
            '--stage-key', str(key_path), '--answers-dir', str(checker), '--answers', str(key_path)],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(both.returncode, 2)
        self.assertIn('not allowed with argument', both.stderr)

    def test_per_case_missing_malformed_and_non_utf8_are_unanswered_misses(self):
        _, _, checker, key_path, key = stage()
        answers = answers_for(checker, key)
        for unit in key['staged']:
            sheet = checker / unit['path'] / 'answer.json'
            if unit['generator_case_id'] == 'opaque-1':
                continue
            if unit['generator_case_id'] == 'opaque-2':
                sheet.write_text('{broken JSON')
            elif unit['generator_case_id'] == 'opaque-3':
                sheet.write_bytes(b'\xff')
            else:
                sheet.write_text(json.dumps(answers[unit['check_kind']][unit['case_id']]))
        loaded = detectors.read_answer_sheets(key_path, checker)
        for unit in key['staged']:
            if unit['generator_case_id'] == 'opaque-2':
                self.assertEqual(loaded[unit['check_kind']][unit['case_id']], '{broken JSON')
        result = subprocess.run([
            sys.executable, '-B', '-m', 'evalsuite.fidelity_detectors', 'score',
            '--stage-key', str(key_path), '--answers-dir', str(checker)],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for group in json.loads(result.stdout)['by_check_kind'].values():
            total = group['overall']
            self.assertEqual((total['answered'], total['unanswered'], total['invalid']), (4, 3, 2))
            self.assertEqual(total['unanswered_misses'], 3)
            self.assertEqual((total['catch_rate']['count'], total['catch_rate']['of']), (3, 6))

    def test_answer_sheet_paths_reject_traversal_symlinks_and_nonfiles(self):
        run, _, checker, _, key = stage()
        for path in ('../outside', '/tmp/outside', './case', 'case//child'):
            altered = deepcopy(key)
            altered['staged'][0]['path'] = path
            with self.subTest(path=path), self.assertRaises(detectors.DetectorError):
                detectors.read_answer_sheets(altered, checker)
        unit = key['staged'][0]
        sheet = checker / unit['path'] / 'answer.json'
        sheet.symlink_to(run / 'missing-answer.json')
        with self.assertRaises(detectors.DetectorError):
            detectors.read_answer_sheets(key, checker)
        alias = checker / 'alias'
        alias.symlink_to(checker / unit['path'], target_is_directory=True)
        altered = deepcopy(key)
        altered['staged'][0]['path'] = 'alias'
        with self.assertRaises(detectors.DetectorError):
            detectors.read_answer_sheets(altered, checker)
        root_alias = run / 'answers-alias'
        root_alias.symlink_to(checker, target_is_directory=True)
        with self.assertRaises(detectors.DetectorError):
            detectors.read_answer_sheets(key, root_alias)
        _, _, checker, _, key = stage()
        (checker / key['staged'][0]['path'] / 'answer.json').mkdir()
        with self.assertRaisesRegex(detectors.DetectorError, 'regular file'):
            detectors.read_answer_sheets(key, checker)

    def test_all_errors_caught_correct_locations_and_no_false_alarms(self):
        _, _, checker, key_path, key = stage()
        result = detectors.score_answers(key_path, answers_for(checker, key))
        for kind in detectors.CHECK_KINDS:
            group = result['by_check_kind'][kind]
            totals = group['overall']
            self.assertEqual((totals['cases'], totals['answered'], totals['unanswered']), (7, 7, 0))
            self.assertEqual(totals['catch_rate']['count'], 6)
            self.assertEqual(totals['catch_rate']['rate'], 1)
            self.assertEqual(totals['location_match_rate']['rate'], 1)
            self.assertEqual(totals['faithful_false_alarm_rate']['rate'], 0)
            for visibility in ('silent', 'visible'):
                for error in detectors.ERROR_CLASSES:
                    metric = group['by_visibility'][visibility]['by_error_class'][error]['catch_rate']
                    self.assertEqual((metric['count'], metric['of']), (1, 1))
                    self.assertAlmostEqual(metric['ci95'][0], 0.20654931437723745)
                    self.assertAlmostEqual(metric['ci95'][1], 1)

    def test_none_caught_and_all_faithful_false_alarms(self):
        _, _, checker, _, key = stage()
        none = detectors.score_answers(key, answers_for(checker, key, faithful=True))
        all_flags = detectors.score_answers(key, answers_for(checker, key, false_alarm=True))
        for kind in detectors.CHECK_KINDS:
            no = none['by_check_kind'][kind]['overall']
            self.assertEqual(no['catch_rate']['rate'], 0)
            self.assertEqual(no['unanswered'], 0)
            self.assertIsNone(no['location_match_among_catches']['rate'])
            alarm = all_flags['by_check_kind'][kind]['overall']['faithful_false_alarm_rate']
            self.assertEqual((alarm['count'], alarm['of'], alarm['rate']), (1, 1, 1))

    def test_missing_and_malformed_are_misses_not_excluded(self):
        _, _, checker, _, key = stage()
        answers = answers_for(checker, key)
        for kind in detectors.CHECK_KINDS:
            units = [u for u in key['staged'] if u['check_kind'] == kind]
            # Opaque-0 is faithful, opaque-1 silent rule, opaque-2 visible rule.
            del answers[kind][units[0]['case_id']]
            del answers[kind][units[1]['case_id']]
            answers[kind][units[2]['case_id']] = '{broken'
        result = detectors.score_answers(key, answers)
        for kind in detectors.CHECK_KINDS:
            total = result['by_check_kind'][kind]['overall']
            self.assertEqual((total['answered'], total['unanswered'], total['invalid']), (4, 3, 1))
            self.assertEqual(total['unanswered_misses'], 2)
            self.assertEqual((total['catch_rate']['count'], total['catch_rate']['of']), (4, 6))
            self.assertEqual(total['faithful_false_alarm_rate']['of'], 1)
        empty = detectors.score_answers(key, {})
        for kind in detectors.CHECK_KINDS:
            total = empty['by_check_kind'][kind]['overall']
            self.assertEqual((total['answered'], total['unanswered_misses']), (0, 6))
            self.assertEqual(total['catch_rate']['rate'], 0)

    def test_wrong_location_is_a_catch_without_location_credit(self):
        _, _, checker, _, key = stage()
        answers = answers_for(checker, key)
        for group in answers.values():
            for answer in group.values():
                if answer['verdict'] == 'not_faithful':
                    answer['location'] = {'kind': 'fact', 'id': 'wrong-ID'}
        result = detectors.score_answers(key, answers)
        for kind in detectors.CHECK_KINDS:
            total = result['by_check_kind'][kind]['overall']
            self.assertEqual(total['catch_rate']['rate'], 1)
            self.assertEqual(total['location_match_rate']['rate'], 0)
            self.assertEqual(total['location_match_among_catches']['of'], 6)

    def test_silent_visible_and_method_scores_are_independent(self):
        _, _, checker, _, key = stage()
        answers = answers_for(checker, key)
        cases = {case['case_id']: case for case in key['cases']}
        for unit in key['staged']:
            if unit['check_kind'] == 'back-translation' or cases[unit['generator_case_id']]['visibility'] == 'silent':
                del answers[unit['check_kind']][unit['case_id']]
        result = detectors.score_answers(key, answers)
        for kind in ('second-formalization', 'semantic-review'):
            group = result['by_check_kind'][kind]
            self.assertEqual(group['by_visibility']['silent']['overall']['catch_rate']['rate'], 0)
            self.assertEqual(group['by_visibility']['visible']['overall']['catch_rate']['rate'], 1)
            self.assertEqual(group['overall']['catch_rate']['rate'], 0.5)
        self.assertEqual(result['by_check_kind']['back-translation']['overall']['catch_rate']['rate'], 0)

    def test_unknown_submissions_and_damaged_stage_keys_rejected(self):
        _, _, _, _, key = stage()
        for answers in [{'unknown-kind': {}}, {'semantic-review': {'unknown-case': {}}},
                        {'semantic-review': []}, []]:
            with self.subTest(answers=answers), self.assertRaises(detectors.DetectorError):
                detectors.score_answers(key, answers)
        for edit in ('drop', 'duplicate'):
            broken = deepcopy(key)
            if edit == 'drop':
                broken['staged'].pop()
            else:
                broken['staged'][-1] = broken['staged'][0]
            with self.subTest(edit=edit), self.assertRaises(detectors.DetectorError):
                detectors.score_answers(broken, {})


if __name__ == '__main__':
    unittest.main()
