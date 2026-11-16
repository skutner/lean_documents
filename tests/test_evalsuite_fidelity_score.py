"""Model-free interface campaigns and scoring. Retain all artifacts under tmp/.

The Lean test adapter declares itself sandboxed ONLY because every submission is
an authored trusted fixture constructed here. It is not a production sandbox and
must never be used on agent-written files. Real scorer integration stays gated.
"""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from evalsuite import fidelity_score as score
from evalsuite.fidelity import FidelityError, LeanResult, trusted_lean_runner
from evalsuite_fidelity_campaign import FamilyCampaign, perfect, run_blind

CORE = ('ledger', 'deadlines', 'approvals', 'packet')   # the generator's original four, in its order
HARD = ('casework', 'procurement')
ALL = CORE + HARD
(ROOT / 'tmp').mkdir(exist_ok=True)
RUN = Path(tempfile.mkdtemp(prefix='fidelity-score-', dir=ROOT / 'tmp'))
LEAN = os.environ.get('AUDIT_TEST_LEAN', '')
LEAN_READY = bool(LEAN and Path(LEAN).is_file())


def trusted_fixture_adapter():
    """TEST ONLY: no hostile code; this marker exercises the scorer's runner gate."""
    # Harder families need minutes per review on a loaded host, above the default timeout.
    direct = trusted_lean_runner(LEAN, timeout=1500)
    def run(path):
        return direct(path)
    run.sandboxed = True
    return run


def unused_sandbox(path):
    raise AssertionError(f'Lean must not run in this test: {path}')
unused_sandbox.sandboxed = True


ARM_HEADER = re.compile(r'  \| "(correct|mixed)/([^"/]+)" => (.*)')
WRONG_CLAIM = {'approvals': 'wrong_request', 'deadlines': 'wrong_notice', 'casework': 'wrong_request',
               'procurement': 'wrong_request'}
WRONG_FACT = {'Approvals': 'limit := recorded.limit + 1', 'Ledger': 'budget := recorded.budget + 1',
              'Deadlines': 'firstPeriod := recorded.firstPeriod + 1', 'Packet': 'maxAge := recorded.maxAge + 1',
              'Casework': 'effective := recorded.effective + 1',
              'Procurement': 'amendedLow := recorded.amendedLow + 1'}


def alias_arms(source, variant):
    """Alias both report prefixes to the one report's predicates, keeping multi-line arms whole."""
    items = []
    for line in source.splitlines():
        match = ARM_HEADER.match(line)
        if match:
            items.append(['arm', match[1], match[2], [match[3]]])
        elif items and items[-1][0] == 'arm' and line.startswith('    '):
            items[-1][3].append(line)
        else:
            items.append(['line', line])
    lines = []
    for item in items:
        if item[0] == 'line':
            lines.append(item[1])
        elif item[1] == variant:
            for prefix in ('correct', 'mixed'):
                lines.append(f'  | "{prefix}/{item[2]}" => {item[3][0]}')
                lines.extend(item[3][1:])
    return '\n'.join(lines) + '\n'


def fixture_submission(module, key, directory, variant, *, mode='faithful', slip=None):
    material = module.emit(key, directory)
    options = {'slip': slip} if slip else {}
    if mode == 'wrong_claim':
        options[WRONG_CLAIM.get(key['generator']['family'], 'wrong_item')] = True
    source = module.fixture(material, **options).read_text()
    # A blind agent sees only one report. Alias both prefixes to that report's
    # exact predicate; never give the stand-in the other report's predicates.
    source = alias_arms(source, variant)
    family = key['generator']['family'].title()
    # Copy evaluator-authored facts ONLY into our trusted stand-in submission.
    # A real reviewer transcribes these from sources and never sees Facts.lean.
    facts = (directory / 'Facts.lean').read_text().split('def sealedWorlds', 1)[0]
    facts = facts.replace('namespace ' + family + '.Material', 'namespace Candidate\nopen ' + family)
    source += '\n' + facts + '\nend Candidate\n'
    ids = [prefix + '/' + c['id'].split('/')[1] for c in material.truth['claims']
           if c['id'].startswith(variant + '/') and c.get('supported', True)
           for prefix in ('correct', 'mixed')]
    if mode == 'missing_claim':
        ids.remove(next(c['id'] for c in material.truth['claims']
                        if c['id'].startswith(variant + '/') and c.get('supported', True)))
    source += '\nnamespace Candidate\ndef claimIds : List String := ' + json.dumps(ids) + '\nend Candidate\n'
    if mode == 'wrong_fact':
        # Change a single recorded scalar, retaining operational functions unchanged.
        source = source.replace('def dossier : Dossier := ⟨recorded,',
                                'def dossier : Dossier := ⟨{ recorded with ' + WRONG_FACT[family] + ' },')
    return source


class ScoreCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName[:40] + '-', dir=RUN))

    def campaign(self, modes=None, *, families=None, slips=None, suite=CORE):
        modes = modes or {}
        campaign = FamilyCampaign(self.root / 'campaign', seed=511, families=suite)
        def policy(call):
            family = call['dossier'].rsplit('-', 1)[1]
            mode = modes.get((family, call['variant']), modes.get(family, 'faithful'))
            if mode != 'nothing':
                key = json.loads((campaign.draft / (call['dossier'] + '.json')).read_text())
                if mode == 'rejected':
                    source = 'axiom fabricated : False\n'
                elif mode == 'instance_override':
                    source = 'instance (priority := 2000) : Repr Nat where\n  reprPrec _ _ := Std.Format.text "0"\n'
                elif mode == 'notation_override':
                    source = 'local notation "Candidate.model.rule" => true\n'
                elif mode == 'compile_error':
                    source = 'namespace Candidate\ndef notAModel : Nat := 0\nend Candidate\n'
                else:
                    source = fixture_submission(score.FAMILIES[family], key,
                        self.root / ('fixture-' + call['cwd'].name), call['variant'],
                        mode=mode, slip=(slips or {}).get(family) if mode in ('faithful', 'slip') else None)
                path = call['cwd'] / score.SUBMISSION
                path.parent.mkdir(parents=True)
                path.write_text(source)
            return perfect(call)
        options = {'arms': score.ARM, 'arms_file': ROOT / 'evalsuite/arms-fidelity.json'}
        if families:
            options['dossiers'] = ','.join(sorted({d for d, _, _ in campaign.by_report.values()
                                                 if d.rsplit('-', 1)[1] in families}))
        with mock.patch.object(run_blind, 'toolchain_python', return_value=None), redirect_stdout(io.StringIO()):
            self.assertEqual(campaign.run(policy, **options), 0)
        return campaign

    def scored(self, campaign, runner, *, prove=False):
        output = self.root / 'scored'
        result = score.score_campaign(campaign.out, campaign.keys, output, runner, prove=prove)
        records = [json.loads(p.read_text()) for p in sorted((output / 'reviews').glob('*/review.json'))]
        self.assertEqual(len(records), len(campaign.units()))
        self.assertEqual(sum(result['overall']['outcomes'].values()), len(records))
        self.assertTrue((output / 'summary.json').is_file())
        self.assertTrue((output / 'summary.md').is_file())
        for path in result['records']:
            self.assertTrue((output / path).is_file())
        return result, records


class ScoreUnitTests(ScoreCase):
    def test_direct_runner_and_missing_declaration_refused_before_work(self):
        for runner in (trusted_lean_runner('/does/not/exist'), lambda _: LeanResult(0, ''), None):
            with self.assertRaisesRegex(FidelityError, 'sandboxed = True'):
                score.score_campaign(self.root / 'absent', self.root / 'keys', self.root / 'out', runner)
            self.assertFalse((self.root / 'out').exists())
        fake = lambda _: LeanResult(0, '')
        fake.sandboxed = 'true'
        with self.assertRaises(FidelityError):
            score.require_sandbox(fake)

    def test_no_file_and_product_filter_rejection_are_terminal_without_lean(self):
        modes = {family: ('nothing' if i % 2 else 'rejected') for i, family in enumerate(score.FAMILIES)}
        campaign = self.campaign(modes, suite=ALL)
        summary, records = self.scored(campaign, unused_sandbox)
        self.assertEqual(summary['overall']['outcomes'], {'no_submission': 6, 'rejected_filter': 6})
        self.assertTrue(all(r['fact_accuracy']['rate'] is None for r in records))
        self.assertEqual(summary['overall']['fact_accuracy']['unavailable_reviews'], 12)
        self.assertGreater(summary['overall']['unsupported_claims'], 0)

    def test_product_filter_permits_instance_but_scorer_refuses_output_override(self):
        sample = self.root / 'instance.lean'
        sample.write_text('instance (priority := 2000) : Repr Nat where\n  reprPrec _ _ := Std.Format.text "0"\n'
                          'local notation "Candidate.model.rule" => true\n')
        score.product_filter().inspect_model(sample)
        campaign = self.campaign({f: 'instance_override' if i % 2 else 'notation_override'
                                  for i, f in enumerate(score.FAMILIES)}, suite=ALL)
        summary, records = self.scored(campaign, unused_sandbox)
        self.assertEqual(summary['overall']['outcomes'], {'rejected_filter': 12})
        self.assertTrue(all('result-integrity' in r['detail'] for r in records))

    def test_projection_shadow_and_namespace_escape_contracts_are_rejected(self):
        path = self.root / 'shadow.lean'
        audit = score.product_filter()
        bodies = [
            'namespace Candidate\ndef model.rule := true\nend Candidate\n',
            'namespace Candidate\ndef model := true where rule := false\nend Candidate\n',
            'namespace Candidate.model\ndef rule := true\nend Candidate.model\n',
            'namespace Candidate\ndef model := true\n',
            'namespace Candidate\nsection\nend Candidate\n',
            'def model := true\n',
            'namespace Candidate\nopen Approvals in\ndef model := true\nend Candidate\n',
            'namespace Candidate\nopen Std\ndef model := true\nend Candidate\n',
            'namespace Candidate\ndef «model.rule» := true\nend Candidate\n',
        ]
        for source in bodies:
            with self.subTest(source=source):
                path.write_text(source)
                audit.inspect_model(path)
                with self.assertRaises(audit.AuditError):
                    score.inspect_submission(audit, path, 'approvals')
        path.write_text('namespace Candidate\ndef model := true\nend Candidate\n')
        score.inspect_submission(audit, path, 'approvals')

    def test_all_authored_fixtures_fit_the_concrete_submission_contract(self):
        from evalsuite import generate
        audit = score.product_filter()
        for family, module in score.FAMILIES.items():
            for variant in ('correct', 'mixed'):
                with self.subTest(family=family, variant=variant):
                    directory = self.root / (family + '-' + variant)
                    source = fixture_submission(module, generate.build_case(family, 511, 1)['key'],
                                                directory, variant, slip=module.VARIANTS[0])
                    path = directory / 'Submitted.lean'
                    path.write_text(source)
                    audit.inspect_model(path)
                    score.inspect_submission(audit, path, family)

    def test_fact_schemas_name_every_interface_field_in_declaration_order(self):
        omitted = {('casework', 'World'): {'neighbour'}}
        for family, module in score.FAMILIES.items():
            text = (module.LEAN_DIR / 'Interface.lean').read_text()
            declared = {}
            for match in re.finditer(r'^structure (\w+) where\n((?:  [^\n]*\n)+)', text, re.M):
                declared[match[1]] = [m[1] for m in re.finditer(r'^  (\w+) :', match[2], re.M)]
            for name, fields in score.SCHEMAS[family].items():
                with self.subTest(family=family, structure=name):
                    expected = [f for f in declared[name] if f not in omitted.get((family, name), ())]
                    self.assertEqual([field for field, _ in fields], expected)

    def test_runner_exception_records_every_review_instead_of_aborting_campaign(self):
        campaign = self.campaign({f: 'compile_error' for f in score.FAMILIES}, suite=ALL)
        def unavailable(_path):
            raise RuntimeError('sandbox worker unavailable')
        unavailable.sandboxed = True
        summary, records = self.scored(campaign, unavailable)
        self.assertEqual(summary['overall']['outcomes'], {'grading_error': 12})
        self.assertTrue(all('sandbox worker unavailable' in r['detail'] for r in records))

    def test_cells_detect_missing_extra_wrong_fields_and_zero_interval(self):
        result = score.compare_facts({'a': '1', 'b': '9', 'extra': '2'}, {'a': '1', 'b': '2', 'lost': '3'})
        self.assertEqual((result['matched'], result['total']), (1, 4))
        self.assertEqual({m['status'] for m in result['mismatches']}, {'extra', 'missing', 'different'})
        self.assertIsNone(score.wilson(0, 0))
        lo, hi = score.wilson(10, 10)
        self.assertLess(lo, 1)
        self.assertAlmostEqual(hi, 1)
        for output in ('', '"FACTS|a\\t1\\na\\t2"', '"FACTS|bad"', '"FACTS|"'):
            with self.assertRaises(FidelityError):
                score.parse_cells(output)

    def test_symlink_submission_is_rejected_without_following_it(self):
        campaign = self.campaign({f: 'nothing' for f in score.FAMILIES}, suite=ALL)
        review = next(iter(campaign.units().values()))
        path = campaign.out / review['arm'] / 'runs' / review['opaque_id'] / 'agent' / score.SUBMISSION
        path.parent.mkdir(parents=True)
        target = self.root / 'private.lean'
        target.write_text('axiom secret : False')
        path.symlink_to(target)
        summary, records = self.scored(campaign, unused_sandbox)
        self.assertEqual(summary['overall']['outcomes'], {'invalid_submission': 1, 'no_submission': 11})
        self.assertTrue(any('Symlink' in (r['detail'] or '') for r in records))

    def test_cli_refuses_non_runner_and_prints_error(self):
        with redirect_stderr(io.StringIO()) as stderr:
            status = score.main(['--campaign', str(self.root), '--keys', str(self.root),
                                 '--output', str(self.root / 'out'), '--runner', 'evalsuite.fidelity:trusted_lean_runner'])
        self.assertEqual(status, 1)
        self.assertIn('sandboxed = True', stderr.getvalue())

    def test_drifted_key_is_reported_for_every_affected_review(self):
        campaign = self.campaign({f: 'nothing' for f in score.FAMILIES}, suite=ALL)
        keypath = next(p for p in campaign.keys.glob('*.json') if p.name != 'KEYS-MANIFEST.json')
        key = json.loads(keypath.read_text())
        key['generator']['version'] = -1
        keypath.write_text(json.dumps(key))
        summary, records = self.scored(campaign, unused_sandbox)
        self.assertEqual(summary['overall']['outcomes'], {'grading_error': 2, 'no_submission': 10})
        self.assertTrue(all('version mismatch' in r['detail'] for r in records if r['outcome'] == 'grading_error'))


@unittest.skipUnless(LEAN_READY, 'AUDIT_TEST_LEAN is unset or does not name a Lean binary; run remote-test.sh step4')
class ScoreLeanTests(ScoreCase):
    def test_faithful_all_families_both_reports_and_bounded_proof(self):
        summary, records = self.scored(self.campaign(), trusted_fixture_adapter(), prove=True)
        self.assertEqual(summary['overall']['outcomes'], {'graded': 8}, records)
        for record in records:
            for metric in score.METRICS:
                self.assertEqual(record[metric]['rate'], 1, record)
            self.assertEqual(record['missing_claims'], [])
            self.assertEqual(record['fact_accuracy']['mismatches'], [])
            self.assertEqual(record['proof']['status'], 'kernel_checked', record)
            self.assertEqual(record['claim_fidelity']['total'] + len(record['unsupported_claims']), record['expected_claims'])
        for family in CORE:
            self.assertEqual(summary['by_family'][family]['reviews'], 2)
        self.assertEqual(summary['by_variant']['correct']['reviews'], 4)
        self.assertEqual(len(summary['cells']), 8)
        print('\nFaithful campaign:', summary['overall'], 'artifacts:', self.root)

    def test_every_family_slip_has_rule_fidelity_below_one(self):
        slips = {f: m.VARIANTS[0] for f, m in score.FAMILIES.items()}
        summary, records = self.scored(self.campaign(slips=slips), trusted_fixture_adapter())
        self.assertEqual(summary['overall']['outcomes'], {'graded': 8}, records)
        for record in records:
            self.assertLess(record['rule_fidelity']['rate'], 1, record)
            self.assertEqual(record['fact_accuracy']['rate'], 1, record)

    def test_wrong_facts_each_family_have_field_mismatches(self):
        summary, records = self.scored(self.campaign({f: 'wrong_fact' for f in score.FAMILIES}), trusted_fixture_adapter())
        self.assertEqual(summary['overall']['outcomes'], {'graded': 8}, records)
        for record in records:
            self.assertLess(record['fact_accuracy']['rate'], 1, record)
            self.assertEqual(len(record['fact_accuracy']['mismatches']), 1, record)
            self.assertEqual(record['rule_fidelity']['rate'], 1, record)

    def test_missing_claims_are_not_given_credit_and_other_report_not_required(self):
        summary, records = self.scored(self.campaign({f: 'missing_claim' for f in score.FAMILIES}), trusted_fixture_adapter())
        self.assertEqual(summary['overall']['outcomes'], {'missing_claims': 8}, records)
        for record in records:
            self.assertEqual(len(record['missing_claims']), 1, record)
            self.assertLess(record['claim_fidelity']['rate'], 1, record)
            self.assertEqual(sum(c['status'] == 'missing' for c in record['claims']), 1)
            self.assertEqual(record['rule_fidelity']['rate'], 1)

    def test_fact_serializer_captures_trusted_instances_before_submission(self):
        module = score.FAMILIES['approvals']
        from evalsuite import generate
        key = generate.build_case('approvals', 511, 3)['key']
        candidate = self.root / 'Candidate.lean'
        candidate.write_text(fixture_submission(module, key, self.root / 'fixture', 'correct', mode='wrong_fact') +
            '\ninstance (priority := 2000) : Repr Nat where\n  reprPrec _ _ := Std.Format.text "0"\n' +
            'instance (priority := 2000) : Repr String where\n  reprPrec _ _ := Std.Format.text "forged"\n')
        # Private serializer probe only: production rejects these instances before Lean.
        actual, _ = score.candidate_facts(module, 'approvals', candidate, self.root, trusted_fixture_adapter())
        material = module.emit(key, self.root / 'expected')
        expected = score.expected_facts(module, 'approvals', material, trusted_fixture_adapter())
        differences = score.compare_facts(actual, expected)
        self.assertEqual(len(differences['mismatches']), 1, differences)
        self.assertEqual(differences['mismatches'][0]['field'], 'dossier.recorded.limit')

    def test_bad_interface_compile_is_terminal(self):
        summary, records = self.scored(self.campaign({f: 'compile_error' for f in score.FAMILIES}), trusted_fixture_adapter())
        self.assertEqual(summary['overall']['outcomes'], {'does_not_compile': 8}, records)
        self.assertTrue(all(r['filter'] == 'accepted' for r in records))

    def test_wrong_row_claim_loses_credit_even_with_faithful_rule(self):
        campaign = self.campaign({'approvals': 'wrong_claim'}, families={'approvals'})
        summary, records = self.scored(campaign, trusted_fixture_adapter())
        self.assertEqual(summary['overall']['outcomes'], {'graded': 2}, records)
        for record in records:
            self.assertLess(record['claim_fidelity']['rate'], 1, record)
            self.assertEqual(record['rule_fidelity']['rate'], 1, record)


@unittest.skipUnless(LEAN_READY, 'AUDIT_TEST_LEAN is unset or does not name a Lean binary; run remote-test.sh step48')
class ScoreHardLeanTests(ScoreCase):
    """Casework and procurement take minutes per review, so each campaign covers both at once."""

    def test_faithful_stand_ins_score_one_hundred_percent_on_both_harder_families(self):
        summary, records = self.scored(self.campaign(suite=HARD), trusted_fixture_adapter(), prove=True)
        self.assertEqual(summary['overall']['outcomes'], {'graded': 4}, records)
        for record in records:
            for metric in score.METRICS:
                self.assertEqual(record[metric]['rate'], 1, record)
            self.assertEqual(record['missing_claims'], [])
            self.assertEqual(record['fact_accuracy']['mismatches'], [])
            self.assertGreater(record['fact_accuracy']['total'], 500, record['family'])
            self.assertEqual(record['claim_fidelity']['total'] + len(record['unsupported_claims']), record['expected_claims'])
            self.assertEqual(record['unsupported_claims'], [])
            # The bounded kernel proof exists for casework; procurement's set exceeds its proof limit.
            self.assertEqual(record['proof']['status'], 'kernel_checked' if record['family'] == 'casework' else 'skipped', record)
        for family in HARD:
            self.assertEqual(summary['by_family'][family]['reviews'], 2)
        print('\nHarder families, faithful:', summary['overall'], 'artifacts:', self.root)

    def test_a_slip_and_a_wrong_fact_are_measured_on_both_harder_families(self):
        modes = {(family, variant): mode for family in HARD
                 for variant, mode in (('correct', 'slip'), ('mixed', 'wrong_fact'))}
        slips = {family: score.FAMILIES[family].VARIANTS[0] for family in HARD}
        summary, records = self.scored(self.campaign(modes, suite=HARD, slips=slips), trusted_fixture_adapter())
        self.assertEqual(summary['overall']['outcomes'], {'graded': 4}, records)
        for record in records:
            if record['variant'] == 'correct':
                self.assertLess(record['rule_fidelity']['rate'], 1, record)
                self.assertEqual(record['fact_accuracy']['rate'], 1, record)
            else:
                self.assertLess(record['fact_accuracy']['rate'], 1, record)
                self.assertEqual(len(record['fact_accuracy']['mismatches']), 1, record)
                self.assertEqual(record['rule_fidelity']['rate'], 1, record)
        print('\nHarder families, slip and wrong fact:',
              [(r['family'], r['variant'], round(r['rule_fidelity']['rate'], 4), round(r['fact_accuracy']['rate'], 4))
               for r in records])

    def test_wrong_row_claims_lose_credit_on_both_harder_families(self):
        summary, records = self.scored(self.campaign({f: 'wrong_claim' for f in HARD}, suite=HARD), trusted_fixture_adapter())
        self.assertEqual(summary['overall']['outcomes'], {'graded': 4}, records)
        for record in records:
            self.assertLess(record['claim_fidelity']['rate'], 1, record)
            self.assertEqual(record['rule_fidelity']['rate'], 1, record)
            self.assertEqual(record['fact_accuracy']['rate'], 1, record)
