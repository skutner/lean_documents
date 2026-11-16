"""Stage and score blind fidelity checks; never invoke a model or execute Lean.

Only each staged case directory is checker input. The generator tree and stage
key are evaluator-only. Directory separation is not a filesystem sandbox: the
caller must enforce that boundary when launching a real checker.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from evalsuite import blindness
from evalsuite.scorecard import rate

CHECK_KINDS = ('second-formalization', 'back-translation', 'semantic-review')
ERROR_CLASSES = ('rule', 'fact', 'claim')
ANALYSIS_FIELDS = {
    'second-formalization': {'independent_formalization', 'comparison'},
    'back-translation': {'back_translation', 'comparison'},
    'semantic-review': {'source_evidence', 'analysis'},
}
PROMPTS = Path(__file__).with_name('prompts') / 'detectors'
SAFE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}')


class DetectorError(ValueError):
    """The requested detector input or answer does not satisfy its contract."""


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DetectorError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def _constant(value):
    raise DetectorError(f'Non-JSON constant: {value}')


def _json(value):
    if isinstance(value, Path):
        value = value.read_text(encoding='utf-8')
    if isinstance(value, str):
        try:
            return json.loads(value, object_pairs_hook=_pairs, parse_constant=_constant)
        except (ValueError, TypeError) as exc:
            raise DetectorError(f'Invalid JSON: {exc}') from exc
    return deepcopy(value)


def _location(value):
    return (isinstance(value, dict) and set(value) == {'kind', 'id'}
            and value['kind'] in ERROR_CLASSES and isinstance(value['id'], str)
            and SAFE_ID.fullmatch(value['id']) is not None)


def _rows(key):
    if (not isinstance(key, dict) or type(key.get('schema_version')) is not int
            or key['schema_version'] != 1 or not isinstance(key.get('cases'), list)
            or not key['cases']):
        raise DetectorError('Expected schema_version 1 and a nonempty cases list')
    if 'complete' in key and key['complete'] is not True:
        raise DetectorError('Incomplete generator or stage keys cannot be used')
    seen = set()
    for row in key['cases']:
        if not isinstance(row, dict):
            raise DetectorError('Each case must be an object')
        ident = row.get('case_id')
        if not isinstance(ident, str) or not SAFE_ID.fullmatch(ident) or ident in seen:
            raise DetectorError('Case IDs must be unique nonempty identifiers')
        seen.add(ident)
        error, visibility = row.get('error_class'), row.get('visibility')
        faithful = error == 'faithful'
        if error not in ('faithful', *ERROR_CLASSES) or row.get('validated') is not True:
            raise DetectorError('Only validated faithful/rule/fact/claim cases may be staged or scored')
        digest = row.get('formalization_sha256')
        if not isinstance(digest, str) or re.fullmatch(r'[0-9a-fA-F]{64}', digest) is None:
            raise DetectorError('Each case requires a 64-hex formalization_sha256')
        if not faithful and 'effective' in row and row['effective'] is not True:
            raise DetectorError('Planted errors must be measured effective when effectiveness is recorded')
        if (faithful and (visibility != 'faithful' or row.get('location') is not None)
                or not faithful and (visibility not in ('silent', 'visible')
                                     or not _location(row.get('location'))
                                     or row['location']['kind'] != error)):
            raise DetectorError('Error class, visibility and location disagree')
        claims = row.get('claims')
        if (not isinstance(claims, list) or not claims
                or any(not isinstance(c, str) or not SAFE_ID.fullmatch(c) for c in claims)
                or len(set(claims)) != len(claims)):
            raise DetectorError('Each case needs unique claim identifiers')
        if error == 'claim' and row['location']['id'] not in claims:
            raise DetectorError('Claim mutation location must be a registered claim')
    return key['cases']


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _check_kinds(kinds):
    kinds = CHECK_KINDS if kinds is None else (kinds,) if isinstance(kinds, str) else kinds
    if (not isinstance(kinds, (tuple, list)) or not kinds
            or any(kind not in CHECK_KINDS for kind in kinds) or len(set(kinds)) != len(kinds)):
        raise DetectorError('Select one or more unique known check kinds')
    return tuple(kind for kind in CHECK_KINDS if kind in kinds)


def stage_cases(generator_root, checker_root, stage_key_path, *, check_kinds=None):
    """Copy allowlisted input to new neutral folders, retaining an external key.

    The same generator yields byte-identical checker trees. Input symlinks and
    non-regular files are refused, and destination trees must not overlap the
    generator or evaluator key. No key/variant/class metadata enters a case.
    """
    check_kinds = _check_kinds(check_kinds)
    generator_root = Path(generator_root).resolve()
    checker_root, stage_key_path = Path(checker_root).resolve(), Path(stage_key_path).resolve()
    try:
        blindness.assert_isolated([checker_root], [generator_root, stage_key_path], roots_must_exist=False)
    except blindness.BlindnessError as exc:
        raise DetectorError(str(exc)) from exc
    if blindness.within(stage_key_path, generator_root):
        raise DetectorError('Stage key must be outside the generator tree')
    if checker_root.exists() and (not checker_root.is_dir() or any(checker_root.iterdir())):
        raise DetectorError('Checker directory must be new or empty')
    if stage_key_path.exists():
        raise DetectorError('Stage key must be a new file')
    source_key = generator_root / 'key.json'
    if source_key.is_symlink() or not source_key.is_file():
        raise DetectorError('Generator key must be a regular file')
    key = _json(source_key)
    rows = _rows(key)
    payloads = []
    for row in sorted(rows, key=lambda item: item['case_id']):
        relative = row.get('path')
        if (not isinstance(relative, str) or not relative or Path(relative).is_absolute()
                or any(part in ('', '.', '..') for part in relative.split('/'))):
            raise DetectorError('Case path must be relative and traversal-free')
        source = generator_root / relative
        if not source.resolve().is_relative_to(generator_root):
            raise DetectorError('Case path must remain inside the generator tree')
        ancestors = [source, *source.parents[:len(Path(relative).parts) - 1]]
        if any(p.is_symlink() for p in ancestors):
            raise DetectorError('Case directories cannot be symlinks')
        sources = source / 'sources'
        if sources.is_symlink() or not sources.is_dir():
            raise DetectorError('Each case needs a regular sources directory')
        files = [source / 'report.md', source / 'Formalization.lean']
        for item in sorted(sources.rglob('*')):
            if item.is_symlink() or not (item.is_dir() or item.is_file()):
                raise DetectorError('Sources must contain only regular files and directories')
            if item.is_file():
                files.append(item)
        if len(files) == 2:
            raise DetectorError('Case sources must not be empty')
        payload = {}
        for item in files:
            if item.is_symlink() or not item.is_file():
                raise DetectorError(f'Missing or non-regular input: {item.name}')
            data = item.read_bytes()
            if blindness.CANARY_PREFIX.encode() in data or data == source_key.read_bytes():
                raise DetectorError('Evaluator key material in checker input')
            payload[item.relative_to(source).as_posix()] = data
        if hashlib.sha256(payload['Formalization.lean']).hexdigest() != row['formalization_sha256'].lower():
            raise DetectorError(f'Formalization hash mismatch for case {row["case_id"]}')
        payloads.append((row, payload))
    # Read all inputs and prompts before creating output; failure cannot leave a
    # seemingly complete stage key with only some cases copied.
    prompts = {kind: (PROMPTS / f'{kind}.md').read_bytes() for kind in check_kinds}
    checker_root.mkdir(parents=True, exist_ok=True)
    staged = []
    for row, payload in payloads:
        for kind in check_kinds:
            ident = 'case-' + hashlib.sha256((kind + '\0' + row['case_id']).encode()).hexdigest()[:24]
            relative = f'{kind}/{ident}'
            target = checker_root / relative
            target.mkdir(parents=True)
            for name, data in payload.items():
                destination = target / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
            (target / 'prompt.md').write_bytes(prompts[kind])
            _write_json(target / 'task.json', {'schema_version': 1, 'case_id': ident,
                                              'check_kind': kind, 'claims': row['claims']})
            hashes = {p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(target.rglob('*')) if p.is_file()}
            staged.append({'case_id': ident, 'check_kind': kind, 'path': relative,
                           'generator_case_id': row['case_id'], 'files': hashes})
    result = {'schema_version': 1, 'complete': True, 'check_kinds': list(check_kinds),
              'cases': deepcopy(rows), 'staged': staged,
              'generator_key_sha256': hashlib.sha256(source_key.read_bytes()).hexdigest(),
              'boundary': 'Only individual staged case directories are checker inputs; isolation is caller-enforced.'}
    stage_key_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(stage_key_path, result)
    return result


def validate_answer(answer, *, case_id, check_kind, claims):
    """Strict method-specific answer schema; no fences, extra fields or coercion."""
    if check_kind not in CHECK_KINDS:
        raise DetectorError('Unknown check kind')
    answer = _json(answer)
    fields = {'schema_version', 'case_id', 'check_kind', 'verdict', 'location', 'reason', 'claims'}
    if check_kind == 'back-translation':
        fields.add('rule_translation')
    if not isinstance(answer, dict) or set(answer) != fields:
        raise DetectorError('Answer must contain exactly the declared top-level fields')
    if (type(answer['schema_version']) is not int or answer['schema_version'] != 1
            or answer['case_id'] != case_id or answer['check_kind'] != check_kind):
        raise DetectorError('Answer version, case ID or check kind does not match the task')
    if answer['verdict'] not in ('faithful', 'not_faithful') or not _text(answer['reason']):
        raise DetectorError('Answer needs a valid verdict and nonempty reason')
    if check_kind == 'back-translation' and not _text(answer['rule_translation']):
        raise DetectorError('Back-translation requires a nonempty rule_translation')
    if (answer['verdict'] == 'faithful' and answer['location'] is not None
            or answer['verdict'] == 'not_faithful' and not _location(answer['location'])):
        raise DetectorError('Faithful answers need null location; not_faithful needs kind and ID')
    analyses, seen = answer['claims'], set()
    if not isinstance(analyses, list):
        raise DetectorError('Claim analyses must be a list')
    expected = ANALYSIS_FIELDS[check_kind] | {'claim_id', 'agrees'}
    for item in analyses:
        if not isinstance(item, dict) or set(item) != expected:
            raise DetectorError('Incorrect method-specific claim analysis fields')
        ident = item['claim_id']
        if not isinstance(ident, str) or ident not in claims or ident in seen:
            raise DetectorError('Unknown or duplicate claim analysis')
        if any(not _text(item[field]) for field in ANALYSIS_FIELDS[check_kind]):
            raise DetectorError('Claim analysis fields must be nonempty strings')
        if type(item['agrees']) is not bool:
            raise DetectorError('Claim agrees must be a JSON boolean')
        if answer['verdict'] == 'faithful' and not item['agrees']:
            raise DetectorError('Faithful verdict contradicts a claim disagreement')
        seen.add(ident)
    if seen != set(claims):
        raise DetectorError('Every registered claim requires analysis')
    return answer


def _metrics(rows):
    errors = [r for r in rows if r['error_class'] != 'faithful']
    faithful = [r for r in rows if r['error_class'] == 'faithful']
    caught = sum(r['caught'] for r in errors)
    matched = sum(r['location_match'] for r in errors)
    return {'cases': len(rows), 'answered': sum(r['status'] == 'answered' for r in rows),
            'unanswered': sum(r['status'] != 'answered' for r in rows),
            'invalid': sum(r['status'] == 'invalid' for r in rows),
            'unanswered_misses': sum(r['status'] != 'answered' for r in errors),
            'catch_rate': rate(caught, len(errors)),
            'faithful_false_alarm_rate': rate(sum(r['false_alarm'] for r in faithful), len(faithful)),
            'location_match_rate': rate(matched, len(errors)),
            'location_match_among_catches': rate(matched, caught)}


def _stage_units(key):
    """Validate the complete stage mapping shared by sheet loading and scoring."""
    cases = {row['case_id']: row for row in _rows(key)}
    check_kinds = _check_kinds(key.get('check_kinds'))
    staged = key.get('staged')
    if not isinstance(staged, list) or len(staged) != len(cases) * len(check_kinds):
        raise DetectorError('Stage key must include every case for every selected check kind')
    pairs, units = set(), set()
    for unit in staged:
        if not isinstance(unit, dict):
            raise DetectorError('Invalid staged unit')
        kind, ident, original = (unit.get(n) for n in ('check_kind', 'case_id', 'generator_case_id'))
        if (kind not in check_kinds or not isinstance(ident, str) or not SAFE_ID.fullmatch(ident)
                or not isinstance(original, str) or original not in cases
                or (kind, original) in pairs or (kind, ident) in units):
            raise DetectorError('Invalid or duplicate staged unit')
        pairs.add((kind, original))
        units.add((kind, ident))
    return cases, check_kinds, staged, units


def read_answer_sheets(stage_key, root):
    """Read <root>/<staged path>/answer.json without repairing JSON.

    Missing sheets are omitted so scoring records them as unanswered. Malformed
    UTF-8/JSON remains invalid, never a fabricated answer. Paths must stay within
    root and may not use symlinks; callers must stop checker writes before reading.
    """
    key = _json(stage_key)
    _, check_kinds, staged, _ = _stage_units(key)
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise DetectorError('Answers root must be an existing regular directory')
    root = root.resolve()
    answers = {kind: {} for kind in check_kinds}
    for unit in staged:
        relative = unit.get('path')
        if (not isinstance(relative, str) or not relative or Path(relative).is_absolute()
                or any(part in ('', '.', '..') for part in relative.split('/'))):
            raise DetectorError('Answer sheet path must be relative and traversal-free')
        path = root / relative / 'answer.json'
        if not path.resolve().is_relative_to(root):
            raise DetectorError('Answer sheet path must remain inside the answers root')
        ancestors = [path, *path.parents[:len(Path(relative).parts)]]
        if any(p.is_symlink() for p in ancestors):
            raise DetectorError('Answer sheet paths cannot use symlinks')
        if not path.exists():
            continue
        if not path.is_file():
            raise DetectorError('Answer sheet must be a regular file')
        data = path.read_bytes()
        try:
            answer = data.decode('utf-8')
        except UnicodeDecodeError:
            # Deliberately invalid JSON; no replacement decoding that could turn
            # malformed bytes into a valid submitted explanation.
            answer = 'INVALID UTF-8 ANSWER SHEET'
        answers[unit['check_kind']][unit['case_id']] = answer
    return answers


def score_answers(stage_key, answers):
    """Score {check_kind: {opaque_case_id: JSON string/object}} without retries.

    Missing and malformed answers remain in all denominators. A catch is a
    not_faithful verdict; exact kind/ID localization is reported independently.
    Unknown kinds/IDs are input errors, never silently discarded.
    """
    key, answers = _json(stage_key), _json(answers)
    cases, check_kinds, staged, units = _stage_units(key)
    if not isinstance(answers, dict) or set(answers) - set(check_kinds):
        raise DetectorError('Answers must map selected check kinds to case answers')
    for kind, group in answers.items():
        if not isinstance(group, dict) or any((kind, ident) not in units for ident in group):
            raise DetectorError('Answers contain an unknown case ID or invalid group')
    outcomes = []
    for unit in staged:
        kind, ident = unit['check_kind'], unit['case_id']
        case = cases[unit['generator_case_id']]
        result = {'case_id': ident, 'check_kind': kind, 'generator_case_id': case['case_id'],
                  'error_class': case['error_class'], 'visibility': case['visibility'],
                  'status': 'missing', 'caught': False, 'false_alarm': False, 'location_match': False}
        if ident in answers.get(kind, {}):
            try:
                answer = validate_answer(answers[kind][ident], case_id=ident,
                                         check_kind=kind, claims=case['claims'])
            except DetectorError as exc:
                result.update(status='invalid', invalid_reason=str(exc))
            else:
                flagged = answer['verdict'] == 'not_faithful'
                faithful = case['error_class'] == 'faithful'
                result.update(status='answered', verdict=answer['verdict'],
                              caught=flagged and not faithful, false_alarm=flagged and faithful,
                              location_match=flagged and not faithful and answer['location'] == case['location'])
        outcomes.append(result)
    by_kind = {}
    for kind in check_kinds:
        group = [row for row in outcomes if row['check_kind'] == kind]
        by_kind[kind] = {
            'overall': _metrics(group),
            'by_error_class': {error: _metrics([r for r in group if r['error_class'] == error])
                               for error in ('faithful', *ERROR_CLASSES)},
            'by_visibility': {
                visibility: {'overall': _metrics([r for r in group if r['visibility'] == visibility]),
                             'by_error_class': {
                                 error: _metrics([r for r in group if r['visibility'] == visibility
                                                  and r['error_class'] == error])
                                 for error in ERROR_CLASSES}}
                for visibility in ('silent', 'visible')},
        }
    return {'schema_version': 1, 'by_check_kind': by_kind, 'outcomes': outcomes,
            'limits': ['No model was executed by this scorer.',
                       'Missing or malformed answers are unanswered; erroneous cases remain misses.',
                       'False-alarm denominators include unanswered faithful cases; inspect answer coverage.',
                       '95% Wilson intervals treat cases as independent; shared dossiers and mutations are correlated.',
                       'Staging separates files but does not enforce runtime filesystem isolation.']}


def stand_in_checker_for_tests(task, *, verdict='faithful', location=None):
    """TEST ONLY: synthesize a schema fixture; not a detector or model result.

    This helper has no access to an evaluator key and makes no semantic decision.
    Tests choose its verdict/location explicitly. Never report its score as model
    performance or independent verification.
    """
    kind = task['check_kind']
    if kind not in CHECK_KINDS:
        raise DetectorError('Unknown check kind')
    answer = {'schema_version': 1, 'case_id': task['case_id'], 'check_kind': kind,
            'verdict': verdict, 'location': location,
            'reason': 'TEST ONLY: caller-selected schema fixture; no semantic check performed.',
            'claims': [dict(claim_id=claim, agrees=verdict == 'faithful',
                           **{field: 'TEST ONLY: synthetic analysis.'
                                             for field in ANALYSIS_FIELDS[kind]})
                       for claim in task['claims']]}
    if kind == 'back-translation':
        answer['rule_translation'] = 'TEST ONLY: synthetic rule translation.'
    return answer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    stage = commands.add_parser('stage', help='Stage blind inputs without invoking a checker')
    stage.add_argument('--generator-root', type=Path, required=True)
    stage.add_argument('--checker-root', type=Path, required=True)
    stage.add_argument('--stage-key', type=Path, required=True)
    stage.add_argument('--check-kind', choices=CHECK_KINDS, action='append',
                       help='Select one kind; repeat to select more (default: all)')
    score = commands.add_parser('score', help='Score supplied answers without invoking a checker')
    score.add_argument('--stage-key', type=Path, required=True)
    sheets = score.add_mutually_exclusive_group(required=True)
    sheets.add_argument('--answers', type=Path, help='Aggregate JSON answer mapping')
    sheets.add_argument('--answers-dir', type=Path, help='Root containing staged paths with answer.json sheets')
    args = parser.parse_args(argv)
    try:
        if args.command == 'stage':
            result = stage_cases(args.generator_root, args.checker_root, args.stage_key,
                                 check_kinds=args.check_kind)
            print(json.dumps({'staged_cases': len(result['staged']), 'check_kinds': result['check_kinds']}))
        else:
            answers = (read_answer_sheets(args.stage_key, args.answers_dir)
                       if args.answers_dir is not None else args.answers)
            print(json.dumps(score_answers(args.stage_key, answers), indent=2))
    except (DetectorError, OSError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
