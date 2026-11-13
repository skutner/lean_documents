"""Optional flawed readers, their controls, and unchanged legacy generation."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
sys.path.insert(0, str(ROOT / 'skills/evidence-to-proof-review/scripts'))
import audit
import compare
import generate
import run_blind
import scorecard
from tests.evalsuite_campaign_fixture import Campaign, sheet


def toy(_rng):
    def item(field, value):
        return generate.claim(field, f'{field} is {value}.', lambda w: w[field] == value,
                              [('facts.md', 'F01')], lambda w: w[field], value)
    return {'title': 'Toy review', 'sources': {'facts.md': '<a id="F01"></a>\nx, y and z are true.\n'},
            'base': dict(x=True, y=True, z=True), 'unknowns': {},
            'slots': [(item(field, True), [item(field, False)]) for field in ('x', 'y', 'z')],
            'told': {'base': dict(x=False, y=True, z=True), 'unknowns': {}}, 'reader': 'alpha',
            'readers': {'alpha': {'base': dict(x=False, y=True, z=True), 'unknowns': {}},
                        'beta': {'base': dict(x=True, y=False, z=True), 'unknowns': {}},
                        'cautious': {'base': dict(y=True, z=True), 'unknowns': {'x': [False, True]}}},
            'preamble': 'Please check the report using the supplied records.'}


@contextlib.contextmanager
def registered_toy():
    module = types.ModuleType('reader_test_family')
    module.build = toy
    with mock.patch.dict(sys.modules, {module.__name__: module}), \
            mock.patch.dict(generate.HARD_FAMILIES, {'reader_test': module.__name__}):
        yield


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


def key_rows():
    # Both sorts of echo share a kind; a second kind has only definite echoes.
    specs = [('A', 'supported', 'shared', 'refuted', {'alpha': 'refuted', 'beta': 'undetermined'}),
             ('B', 'refuted', 'shared', 'undetermined', {'alpha': 'undetermined', 'beta': 'supported'}),
             ('C', 'supported', 'shared', None, {}), ('D', 'refuted', 'shared', None, {}),
             ('E', 'undetermined', 'shared', None, {}),
             ('F', 'supported', 'definite_only', 'refuted', {'alpha': 'refuted', 'beta': 'undetermined'}),
             ('G', 'supported', 'definite_only', None, {}), ('H', 'refuted', 'unrelated', None, {}),
             ('I', 'supported', 'shared', None, {'beta': 'refuted'})]
    return [dict(claim_id=ident, variant='mixed', semantic_state=expected, claim_kind=kind,
                 echo_state=echo, lure_states=lures, **({'echo_reader': 'alpha'} if echo else {}))
            for ident, expected, kind, echo, lures in specs]


class ReaderTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='readers-', dir=ROOT / 'tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def scored(self, answers, rows=None):
        rows = key_rows() if rows is None else rows
        write(self.root / 'keys/demo.json', {'expectations': rows})
        index = {}
        for repeat, states in enumerate(answers, 1):
            name = f'run-{repeat}'
            base = self.root / 'runs' / name
            write(base / 'input/report-ledger.json', {row['claim_id']: f'**{row["claim_id"]}.** A claim.' for row in rows})
            write(base / 'findings.json', [dict(claim_id=row['claim_id'], report_anchor=row['claim_id'],
                  semantic_state=state, formal_status='not_attempted', argument_status='sufficient',
                  evidence=[], reasoning=[f'Repeat {repeat}']) for row, state in zip(rows, states) if state != 'missing'])
            write(base / 'proof-results.json', {'status': 'not_attempted', 'targets': []})
            index[name] = {'dossier': 'demo', 'variant': 'mixed', 'repeat': repeat}
        return scorecard.score(self.root / 'runs', self.root / 'keys', index=index, scan=False)

    def test_second_reader_lures_are_never_controls(self):
        # Alpha agrees on B/C; beta alone supplies both control kinds.
        rows = [
            dict(claim_id='A', variant='mixed', semantic_state='supported', claim_kind='other',
                 echo_state='refuted', echo_reader='alpha', lure_states={'alpha': 'refuted'}),
            dict(claim_id='B', variant='mixed', semantic_state='supported', claim_kind='reader_only',
                 echo_state=None, lure_states={'beta': 'refuted'}),
            dict(claim_id='C', variant='mixed', semantic_state='supported', claim_kind='reader_only',
                 echo_state=None, lure_states={'beta': 'undetermined'}),
            dict(claim_id='D', variant='mixed', semantic_state='supported', claim_kind='reader_only',
                 echo_state=None, lure_states={})]
        for answer in ('refuted', 'undetermined', 'missing', 'beta'):
            with self.subTest(answer=answer):
                answers = [row['lure_states'].get('beta', row['semantic_state']) for row in rows] if answer == 'beta' else [
                    'supported', answer, answer, 'supported']
                card = self.scored([answers], rows)
                keys = scorecard.load_keys(self.root / 'keys')
                run = scorecard.discover_runs(self.root / 'runs', keys, {'run-1': {
                    'dossier': 'demo', 'variant': 'mixed', 'repeat': 1}})[0]
                claims = scorecard.collapse(scorecard.observe(
                    run, scorecard.read_legacy_bundle(run['path']), keys['demo']['rows']))
                self.assertEqual(scorecard.asserting_kinds(claims), {'other', 'reader_only'})
                self.assertEqual(scorecard.withholding_kinds(claims), {'reader_only'})
                block = card['semantic']['all_claims']
                for metric in ('wrong_definite_without_conflict', 'withheld_without_conflict'):
                    self.assertEqual(block[metric], scorecard.rate(0, 1))
                    self.assertEqual(block['echo_by_kind']['reader_only'][metric], scorecard.rate(0, 1))
                    for state in scorecard.STATES:
                        expected = int(state == ('refuted' if metric.startswith('wrong') else 'undetermined'))
                        self.assertEqual(card['baselines']['constant'][state]['all_claims'][metric],
                                         scorecard.rate(expected, 1))
                        constant = scorecard.semantic_metrics(claims, predicted=state)
                        self.assertEqual(constant['echo_by_kind']['reader_only'][metric],
                                         scorecard.rate(expected, 1))
                for subset in (claims, [c for c in claims if c['kind'] == 'reader_only']):
                    totals = compare.rates(compare.by_dossier(subset), ['demo'])
                    for metric in ('wrong_definite_without_conflict', 'withheld_control'):
                        self.assertEqual(totals[metric], {'count': 0, 'of': 1, 'rate': 0.0})
                beta = {'asserted': scorecard.rate(int(answer in ('refuted', 'beta')), 1),
                        'withheld': scorecard.rate(int(answer in ('undetermined', 'beta')), 1)}
                self.assertEqual(card['semantic']['lures_by_reader']['beta'], beta)
                self.assertEqual(scorecard.lures_by_reader(claims)['beta'], beta)

    def test_optional_generator_fields_and_preamble(self):
        with registered_toy():
            case = generate.build_case('reader_test', 5, 1)
            summary = generate.write_suite(self.root / 'fixtures', self.root / 'keys', 5, 1, ['reader_test'])
            output = io.StringIO()
            with mock.patch.object(sys, 'argv', ['generate.py', '--fixtures', str(self.root / 'cli/fixtures'),
                    '--keys', str(self.root / 'cli/keys'), '--seed', '5', '--dossiers', '1',
                    '--families', 'reader_test']), contextlib.redirect_stdout(output):
                generate.main()
            self.assertIn('"lure_claims": 4', output.getvalue())
        prefixes = []
        for variant in ('correct', 'mixed'):
            text = case['files'][f'report-{variant}.md']
            passages = run_blind.paragraphs(text)
            self.assertEqual(passages, audit.paragraphs(text))
            self.assertEqual(set(passages), {'document-preamble', 'C01', 'C02', 'C03'})
            self.assertEqual(run_blind.claim_anchors(passages), {name: name for name in ('C01', 'C02', 'C03')})
            prefixes.append(passages['document-preamble'])
            self.assertEqual(prefixes[-1], '# Toy review\n\n' + toy(None)['preamble'])
        self.assertEqual(*prefixes)
        rows = case['key']['expectations']
        self.assertEqual(summary['lure_claims'], 4)
        for row in rows:
            field = row['claim_text'][0]
            expected = row['semantic_state']
            opposite = 'refuted' if expected == 'supported' else 'supported'
            wanted = {'alpha': opposite, 'cautious': 'undetermined'} if field == 'x' else (
                {'beta': opposite} if field == 'y' else {})
            self.assertEqual(row['lure_states'], wanted)
            self.assertEqual(set(row['lure_derivations']), set(wanted))
            for name, state in wanted.items():
                self.assertEqual(row['lure_derivations'][name], {'worlds': 2 if name == 'cautious' else 1,
                    'worlds_where_true': 1 if state in ('supported', 'undetermined') else 0})
            self.assertEqual(row.get('echo_reader'), 'alpha' if field == 'x' else None)
            self.assertEqual(row['echo_state'], opposite if field == 'x' else None)
        self.assertEqual(len(scorecard.load_keys(self.root / 'keys')), 1)
        case = toy(None)
        item = case['slots'][0][0]
        case.pop('readers')
        named = generate.expectation('d', 'C01', 'correct', item, case)
        self.assertEqual(named['echo_reader'], 'alpha')
        self.assertNotIn('lure_states', named)
        self.assertNotIn('lure_derivations', named)
        case.pop('reader')
        self.assertNotIn('echo_reader', generate.expectation('d', 'C01', 'correct', item, case))
        case['readers'] = {}
        case.pop('told')
        empty = generate.expectation('d', 'C01', 'correct', item, case)
        self.assertEqual((empty['lure_states'], empty['lure_derivations']), ({}, {}))
        case['readers'] = {'beta': toy(None)['readers']['beta']}
        only_readers = generate.expectation('d', 'C02', 'correct', case['slots'][1][0], case)
        self.assertEqual(only_readers['lure_states'], {'beta': 'refuted'})
        self.assertIsNone(only_readers['echo_state'])

    def test_packet_key_bytes_are_unchanged(self):
        # SHA256 of the full serialized keys from the copied, unchanged generator.
        hashes = {1: 'd84515153d1340e79662f0778392e2aff1440205109e21fe9df10dd2cb05afb0',
                  5: 'c801fd5012bc5b019aefada7661f6e295d9d088fa11b4be6a38b7beab62d2085',
                  15: '99f597db7e910e41ecc9ad3f4553a3fc634acb34895fc091b1e6d7852ff690f0'}
        for seed, digest in hashes.items():
            key = generate.build_case('packet', seed, 1)['key']
            self.assertEqual(hashlib.sha256((json.dumps(key, indent=2, ensure_ascii=False) + '\n').encode()).hexdigest(), digest)
            for row in key['expectations']:
                self.assertFalse({'echo_reader', 'lure_states', 'lure_derivations'} & row.keys())
        summary = generate.write_suite(self.root / 'fixtures', self.root / 'keys', 5, 1, ['packet'])
        self.assertNotIn('lure_claims', summary)

    def test_repeats_and_every_control_number(self):
        expected = [row['semantic_state'] for row in key_rows()]
        wrong = ['refuted', 'undetermined', 'refuted', 'supported', 'supported', 'refuted', 'undetermined', 'supported', 'refuted']
        card = self.scored([expected, wrong, wrong])
        block = card['semantic']['all_claims']
        self.assertEqual(block['echo_asserted'], scorecard.rate(2, 2))
        self.assertEqual(block['wrong_definite_without_conflict'], scorecard.rate(3, 4))
        self.assertEqual(block['withheld_without_conflict'], scorecard.rate(1, 3))
        self.assertEqual(block['echo_by_kind'], {
            'shared': {'echo_asserted': scorecard.rate(1, 1), 'wrong_definite_without_conflict': scorecard.rate(3, 3),
                       'echo_withheld': scorecard.rate(1, 1), 'withheld_without_conflict': scorecard.rate(0, 2)},
            'definite_only': {'echo_asserted': scorecard.rate(1, 1), 'wrong_definite_without_conflict': scorecard.rate(0, 1),
                              'echo_withheld': scorecard.rate(0, 0), 'withheld_without_conflict': scorecard.rate(1, 1)}})
        self.assertEqual(card['semantic']['lures_by_reader'], {
            'alpha': {'asserted': scorecard.rate(2, 2), 'withheld': scorecard.rate(1, 1)},
            'beta': {'asserted': scorecard.rate(1, 2), 'withheld': scorecard.rate(0, 2)}})
        self.assertIn('wrong_definite_without_conflict: 3/4', scorecard.headline(card))
        constants = card['baselines']['constant']
        for state, count in [('supported', 2), ('refuted', 3), ('undetermined', 0), ('both', 0), ('not_applicable', 0)]:
            self.assertEqual(constants[state]['all_claims']['wrong_definite_without_conflict'], scorecard.rate(count, 4))

    def test_caution_and_each_reader(self):
        card = self.scored([['undetermined'] * 9])
        self.assertEqual(card['semantic']['all_claims']['wrong_definite_without_conflict'], scorecard.rate(0, 4))
        self.assertEqual(card['semantic']['all_claims']['withheld_without_conflict'], scorecard.rate(3, 3))
        self.assertEqual(card['semantic']['lures_by_reader'], {
            'alpha': {'asserted': scorecard.rate(0, 2), 'withheld': scorecard.rate(1, 1)},
            'beta': {'asserted': scorecard.rate(0, 2), 'withheld': scorecard.rate(2, 2)}})
        for name, other, own, theirs, control in [('alpha', 'beta', (2, 1), (0, 0), 0),
                                                 ('beta', 'alpha', (2, 2), (0, 0), 0)]:
            answers = [row['lure_states'].get(name, row['semantic_state']) for row in key_rows()]
            card = self.scored([answers])
            readers = card['semantic']['lures_by_reader']
            self.assertEqual(tuple(readers[name][kind]['count'] for kind in ('asserted', 'withheld')), own)
            self.assertEqual(tuple(readers[other][kind]['count'] for kind in ('asserted', 'withheld')), theirs)
            self.assertEqual(card['semantic']['all_claims']['wrong_definite_without_conflict'], scorecard.rate(control, 4))

    def test_silence_and_legacy_keys(self):
        card = self.scored([['missing'] * 9])
        self.assertEqual(card['semantic']['all_claims']['wrong_definite_without_conflict'], scorecard.rate(0, 4))
        self.assertEqual(len(card['coverage']['missing_claims']), 9)
        keys = scorecard.load_keys(self.root / 'keys')
        run = scorecard.discover_runs(self.root / 'runs', keys, {'run-1': {'dossier': 'demo', 'variant': 'mixed', 'repeat': 1}})[0]
        claims = scorecard.collapse(scorecard.observe(run, scorecard.read_legacy_bundle(run['path']), keys['demo']['rows']))
        totals = compare.rates(compare.by_dossier(claims), ['demo'])
        self.assertEqual(totals['wrong_definite_without_conflict'], {'count': 4, 'of': 4, 'rate': 1.0})
        self.assertEqual(scorecard.lures_by_reader(claims, count_silence=True), {
            'alpha': {'asserted': scorecard.rate(2, 2), 'withheld': scorecard.rate(1, 1)},
            'beta': {'asserted': scorecard.rate(2, 2), 'withheld': scorecard.rate(2, 2)}})
        rows = [{k: v for k, v in row.items() if k not in ('echo_reader', 'lure_states')} for row in key_rows()]
        write(self.root / 'keys/demo.json', {'expectations': rows})
        legacy = scorecard.score(self.root / 'runs', self.root / 'keys', index={'run-1': {
            'dossier': 'demo', 'variant': 'mixed', 'repeat': 1}}, scan=False)
        self.assertEqual(legacy['semantic']['lures_by_reader'], {})

    def test_load_keys_rejects_bad_reader_fields(self):
        base = dict(claim_id='C01', variant='mixed', semantic_state='supported')
        invalid = [{'echo_reader': name, 'echo_state': 'refuted'} for name in ('', '  ', None, 1, [])]
        invalid += [{'echo_reader': 'alpha'}, {'echo_reader': 'alpha', 'echo_state': None}]
        invalid += [{'lure_states': value} for value in (None, [], 'alpha', {'': 'refuted'}, {' ': 'refuted'},
                    {'alpha': 'supported'}, {'alpha': 'probably'}, {'alpha': None}, {'alpha': []})]
        invalid += [{'echo_reader': 'alpha', 'echo_state': 'refuted', 'lure_states': {'alpha': 'undetermined'}}]
        for fields in invalid:
            with self.subTest(fields=fields):
                write(self.root / 'keys/demo.json', {'expectations': [dict(base, **fields)]})
                with self.assertRaises(scorecard.ScoreError):
                    scorecard.load_keys(self.root / 'keys')
        # JSON object names are strings; non-string Python names cannot occur after JSON decoding.
        for fields in ({}, {'lure_states': {}}, {'echo_reader': 'alpha', 'echo_state': 'refuted'},
                       {'lure_states': {'alpha': 'refuted', 'beta': 'undetermined'}}):
            write(self.root / 'keys/demo.json', {'expectations': [dict(base, **fields)]})
            self.assertEqual(len(scorecard.load_keys(self.root / 'keys')['demo']['rows']), 1)

    def test_each_reader_field_works_alone(self):
        answers = [['refuted', 'undetermined', 'supported', 'refuted', 'undetermined',
                    'refuted', 'supported', 'refuted', 'supported']]
        self.scored(answers)
        for removed, names in [(('lure_states',), {'alpha'}), (('echo_reader', 'echo_state'), {'alpha', 'beta'})]:
            rows = [{k: v for k, v in row.items() if k not in removed} for row in key_rows()]
            write(self.root / 'keys/demo.json', {'expectations': rows})
            card = scorecard.score(self.root / 'runs', self.root / 'keys', index={'run-1': {
                'dossier': 'demo', 'variant': 'mixed', 'repeat': 1}}, scan=False)
            readers = card['semantic']['lures_by_reader']
            self.assertEqual(set(readers), names)
            self.assertEqual(readers['alpha'], {'asserted': scorecard.rate(2, 2), 'withheld': scorecard.rate(1, 1)})
            if 'echo_state' in removed:
                self.assertEqual(card['semantic']['all_claims']['echo'], scorecard.rate(0, 0))
                self.assertEqual(card['semantic']['all_claims']['wrong_definite_without_conflict'], scorecard.rate(0, 4))

    def test_definite_control_kinds_come_from_all_keys(self):
        self.scored([['supported', 'refuted', 'refuted', 'supported', 'supported',
                      'supported', 'undetermined', 'supported', 'refuted']])
        rows = key_rows()
        # All definite echoes and reader lures belong to an unrun report; its keys still define control kinds.
        for row in rows:
            if any(state in ('supported', 'refuted') for state in scorecard.reader_states(row).values()):
                row['variant'] = 'correct'
        write(self.root / 'keys/demo.json', {'expectations': rows})
        card = scorecard.score(self.root / 'runs', self.root / 'keys', index={'run-1': {
            'dossier': 'demo', 'variant': 'mixed', 'repeat': 1}}, scan=False)
        self.assertEqual(card['coverage']['missing_reports'], ['demo-correct'])
        self.assertEqual(card['semantic']['all_claims']['echo_asserted'], scorecard.rate(0, 0))
        self.assertEqual(card['semantic']['all_claims']['wrong_definite_without_conflict'], scorecard.rate(3, 4))

    def test_definite_control_kinds_from_all_keys_apply_inside_each_report_group_and_baseline(self):
        # Kind K carries a definite lure only in the mixed (flawed) report; the clean report has none.
        # (id, variant, expected, kind, echo, answered)
        specs = [('M1', 'mixed', 'supported', 'K', 'refuted', 'supported'),   # lure: makes K a definite kind
                 ('M2', 'mixed', 'refuted', 'K', None, 'supported'),          # control, wrong definite
                 ('M3', 'mixed', 'supported', 'other', None, 'supported'),    # kind without a definite lure
                 ('C1', 'correct', 'supported', 'K', None, 'refuted'),        # control, wrong definite
                 ('C2', 'correct', 'supported', 'other', None, 'refuted')]    # wrong, but not a control kind
        rows = [dict(claim_id=ident, variant=variant, semantic_state=expected, claim_kind=kind, echo_state=echo,
                     lure_states={}, **({'echo_reader': 'alpha'} if echo else {}))
                for ident, variant, expected, kind, echo, _ in specs]
        write(self.root / 'keys/demo.json', {'expectations': rows})
        index = {}
        for variant in ('mixed', 'correct'):
            base = self.root / 'runs' / variant
            mine = [spec for spec in specs if spec[1] == variant]
            write(base / 'input/report-ledger.json', {spec[0]: f'**{spec[0]}.** A claim.' for spec in mine})
            write(base / 'findings.json', [dict(claim_id=spec[0], report_anchor=spec[0], semantic_state=spec[5],
                  formal_status='not_attempted', argument_status='sufficient', evidence=[],
                  reasoning=['One repeat']) for spec in mine])
            write(base / 'proof-results.json', {'status': 'not_attempted', 'targets': []})
            index[variant] = {'dossier': 'demo', 'variant': variant, 'repeat': 1}
        card = scorecard.score(self.root / 'runs', self.root / 'keys', index=index, scan=False)
        metric = 'wrong_definite_without_conflict'
        semantic, constants = card['semantic'], card['baselines']['constant']
        # Kinds come from all keys: {K}. Mixed report is flawed (M2 refuted), correct report is clean.
        # Controls (no lure, kind K): flawed {M2, expected refuted}, clean {C1, expected supported}.
        # Answered: M2 supported (wrong definite) -> 1 of 1; C1 refuted (wrong definite) -> 1 of 1.
        self.assertEqual(semantic['flawed_reports'][metric], scorecard.rate(1, 1))
        self.assertEqual(semantic['clean_reports'][metric], scorecard.rate(1, 1))
        self.assertEqual(semantic['all_claims'][metric], scorecard.rate(2, 2))
        # A constant answer is a wrong definite verdict when it is supported or refuted and differs from the key:
        # M2 expects refuted, so only 'supported' is wrong; C1 expects supported, so only 'refuted' is wrong.
        for state in scorecard.STATES:
            with self.subTest(state=state):
                self.assertEqual(constants[state]['flawed_reports'][metric], scorecard.rate(int(state == 'supported'), 1))
                self.assertEqual(constants[state]['clean_reports'][metric], scorecard.rate(int(state == 'refuted'), 1))
                self.assertEqual(constants[state]['all_claims'][metric],
                                 scorecard.rate(int(state in ('supported', 'refuted')), 2))

    def test_compare_prints_controls_and_each_reader_per_arm(self):
        # Campaign cycles FAMILIES; restrict it to the toy without editing its shared fixture helper.
        with registered_toy(), mock.patch.dict(generate.FAMILIES, {'reader_test': toy}, clear=True):
            campaign = Campaign(self.root, dossiers=1)
        def policy(call):
            reader = 'alpha' if call['arm'] == 'plain' else 'beta'
            return {'sheet': sheet(call, lambda row: row['lure_states'].get(reader, row['semantic_state']))}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(campaign.run(policy, arms='plain,skill-lean', repeats=2), 0)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(compare.main(['--campaign', str(campaign.out), '--keys', str(campaign.keys),
                '--baseline', 'plain', '--candidate', 'skill-lean', '--bootstrap', '1000',
                '--output', str(self.root / 'comparison.json')]), 0)
        text = output.getvalue()
        self.assertIn('Wrong definite where its word agrees', text)
        self.assertIn('Reader "alpha", arm "plain": asserted 100.0% (2/2); withheld n/a (0/0)', text)
        self.assertIn('Reader "alpha", arm "skill-lean": asserted 0.0% (0/2); withheld n/a (0/0)', text)
        self.assertIn('Reader "beta", arm "plain": asserted 0.0% (0/2); withheld n/a (0/0)', text)
        self.assertIn('Reader "beta", arm "skill-lean": asserted 100.0% (2/2); withheld n/a (0/0)', text)
        self.assertEqual(text.count('Reader "'), 6)
        result = json.loads((self.root / 'comparison.json').read_text())['comparisons'][0]
        self.assertEqual(result['baseline']['echo_asserted']['count'], 2)
        self.assertEqual(result['candidate']['echo_asserted']['count'], 0)
        for arm in ('plain', 'skill-lean'):
            card = scorecard.score(campaign.out / arm / 'runs', campaign.keys,
                                   records=campaign.out / arm / 'records', scan=False)
            self.assertEqual(card['coverage']['missing_claims'], [])
            self.assertEqual(card['suite']['claims'], 6)
        # A whole failed review retains the reader metadata too, as does the strict timeout reading.
        keys = scorecard.load_keys(campaign.keys)
        for path in (campaign.out / 'plain/runs').glob('*/findings.json'):
            path.unlink()
        failed = compare.arm_reviews(campaign.out, 'plain', keys)
        good = compare.arm_reviews(campaign.out, 'skill-lean', keys)
        result = compare.compare(failed, good, bootstrap=1000)
        self.assertEqual(result['baseline']['lures_by_reader']['beta']['asserted'], scorecard.rate(2, 2))
        for review in good.values():
            review['ended'] = 'timeout'
        strict = compare.compare(failed, good, bootstrap=1000, strict=True)
        self.assertEqual(strict['candidate']['lures_by_reader']['alpha']['asserted'], scorecard.rate(2, 2))


if __name__ == '__main__':
    unittest.main()
