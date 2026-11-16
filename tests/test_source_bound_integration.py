"""Profile-off byte parity with the pre-source-bound Markdown audit implementation.

Replay the existing scenarios with their original assertions. Only clocks and
temporary directory names are controlled; converters and Lean execute normally.
Every generated file, including native originals and proof logs, is compared.
"""
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timezone
import hashlib
import inspect
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch
import zipfile

# Import the module, not its TestCase, to avoid duplicate unittest discovery.
import test_markdown_audit as markdown_fixtures
import markdown_proofs


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/evidence-to-proof-review/scripts/markdown_audit.py'
PARITY_BASE = '6c80ee3b71394d5e71f39519070931434373c84b'
PARITY_FIXTURE = ROOT / 'tests/fixtures/source_bound/integration/markdown_audit_6c80ee3b.py'
PARITY_SHA256 = '4558f59c55628818b8cc56e5daee57796a4fd5f074200572ad5e32b7114741d0'


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        instant = cls(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)
        return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)


class ProfileOffParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        cls.artifacts = Path(tempfile.mkdtemp(prefix='profile-off-parity-', dir=ROOT / 'tmp'))
        original_source = PARITY_FIXTURE.read_bytes()
        if hashlib.sha256(original_source).hexdigest() != PARITY_SHA256:
            raise AssertionError(f'Frozen parity fixture differs from {PARITY_BASE}')
        (cls.artifacts / 'original-markdown_audit.py').write_bytes(original_source)
        candidate_source = SCRIPT.read_bytes()
        (cls.artifacts / 'candidate-markdown_audit.py').write_bytes(candidate_source)
        (cls.artifacts / 'candidate.txt').write_text(
            f'Original commit: {PARITY_BASE}\n'
            f'Candidate script SHA256: {hashlib.sha256(candidate_source).hexdigest()}\n')
        cls.original = ModuleType('profile_off_original_markdown_audit')
        # The original script uses __file__ to find sibling imports. Keep that
        # path in scripts/, even though its preserved source lives under tmp/.
        cls.original.__file__ = str(SCRIPT)
        exec(compile(original_source, str(SCRIPT), 'exec'), cls.original.__dict__)
        print(f'\nProfile-off parity artifacts retained at {cls.artifacts}', flush=True)

    def _replay(self, scenario, module, scenario_root, label):
        work = scenario_root / 'work'
        work.mkdir()
        counter = 0

        def deterministic_mkdtemp(suffix=None, prefix=None, dir=None):
            nonlocal counter
            counter += 1
            parent = Path(dir) if dir is not None else work
            if parent.resolve() == (ROOT / 'tmp').resolve():
                parent = work
            destination = parent / f'{prefix or "tmp"}{counter:04d}{suffix or ""}'
            destination.mkdir()
            return str(destination)

        case = markdown_fixtures.MarkdownAuditTests(scenario)
        # Unwrap existing availability decorators. Missing converters/Lean must
        # fail here, except for the explicit macOS Lean exemption below.
        method = inspect.unwrap(getattr(type(case), scenario))
        output = io.StringIO()
        try:
            with ExitStack() as stack:
                stack.enter_context(patch.object(markdown_fixtures, 'md', module))
                stack.enter_context(patch.object(module, 'datetime', _FrozenDatetime))
                stack.enter_context(patch.object(markdown_proofs, 'datetime', _FrozenDatetime))
                stack.enter_context(patch.object(tempfile, 'mkdtemp', deterministic_mkdtemp))
                # DOCX fixture ZIP members otherwise carry wall-clock metadata.
                stack.enter_context(patch.object(zipfile.time, 'localtime',
                    return_value=(2026, 11, 12, 12, 0, 0, 3, 316, 0)))
                stack.enter_context(redirect_stdout(output))
                case.setUp()
                try:
                    method(case)
                finally:
                    case.tearDown()
                    case.doCleanups()
        finally:
            # Rename after execution so both runs used identical absolute paths,
            # while retaining separate original and candidate evidence trees.
            work.rename(scenario_root / label)
            (scenario_root / f'{label}-stdout.txt').write_text(output.getvalue())
        return output.getvalue()

    def _assert_parity(self, scenario):
        scenario_root = self.artifacts / scenario
        scenario_root.mkdir()
        original_stdout = self._replay(scenario, self.original, scenario_root, 'original')
        candidate_stdout = self._replay(scenario, markdown_fixtures.md, scenario_root, 'candidate')
        original_root, candidate_root = scenario_root / 'original', scenario_root / 'candidate'
        original_files = {p.relative_to(original_root) for p in original_root.rglob('*') if p.is_file()}
        candidate_files = {p.relative_to(candidate_root) for p in candidate_root.rglob('*') if p.is_file()}
        self.assertTrue(original_files, 'A parity scenario must produce artifacts')
        self.assertEqual(original_files, candidate_files, 'Output file inventory changed')
        for relative in sorted(original_files):
            with self.subTest(file=str(relative)):
                self.assertEqual((original_root / relative).read_bytes(),
                                 (candidate_root / relative).read_bytes(),
                                 f'Profile-off bytes changed: {relative}')
        self.assertEqual(original_stdout, candidate_stdout, 'Stdout transport changed')


def _parity_test(scenario):
    def test(self):
        if (scenario == 'test_real_lean_markdown_only_end_to_end'
                and sys.platform == 'darwin' and not os.environ.get('AUDIT_TEST_LEAN')):
            self.skipTest('macOS real Lean requires AUDIT_TEST_LEAN')
        if (scenario == 'test_native_pdf_formal_verdict_requires_hash_bound_visual_review'
                and sys.platform == 'darwin'
                and not all(shutil.which(tool) for tool in ('pdfinfo', 'pdftotext', 'pdfimages'))):
            self.skipTest('macOS PDF converters unavailable')
        self._assert_parity(scenario)
    test.__name__ = 'test_profile_off_' + scenario.removeprefix('test_')
    test.__doc__ = f'Compare all output bytes for existing fixture {scenario}.'
    return test


for _scenario in unittest.defaultTestLoader.getTestCaseNames(markdown_fixtures.MarkdownAuditTests):
    _test = _parity_test(_scenario)
    setattr(ProfileOffParityTests, _test.__name__, _test)

# Profile-on tests use newly written synthetic fixtures only. No historical
# validation model or agent-authored audit model is ever executed.
from types import SimpleNamespace
import json
import source_bound_audit as sb
import source_bound_contract as contract
import source_bound_facts as facts_module

md = markdown_fixtures.md


class ProfileFixtures:
    def workspace(self, family='money', *, relation='supports', second=False):
        base = Path(tempfile.mkdtemp(prefix='sb-integration-', dir=ROOT / 'tmp'))
        self.artifacts = base
        report, source = base / 'report.md', base / 'source.md'
        cases = {
            'money': ('The total is EUR 3.00.', 'First EUR 1.00; second EUR 2.00.',
                [('a', 'money(EUR)', 'EUR 1.00'), ('b', 'money(EUR)', 'EUR 2.00')],
                ('reported', 'money(EUR)', 'EUR 3.00'), 'sum', {'xs': 'items', 'v': 'reported'}, 'list(money(EUR))'),
            'threshold': ('The amount meets the threshold.', 'Amount 5; threshold 5.',
                [('amount', 'nat', '5'), ('threshold', 'nat', '5')], None,
                'compare', {'a': 'amount', 'b': 'threshold', 'cmp': '≥'}, None),
            'deadline': ('The deadline is 2026-01-03.', 'Start 2026-01-01; allowance 2 days.',
                [('start', 'date', '2026-01-01'), ('days', 'nat', '2 days')],
                ('due', 'date', '2026-01-03'), 'date_add', {'start': 'start', 'days': 'days', 'due': 'due'}, None),
            'list': ('Every listed amount meets the minimum.', 'First 2; second 3; minimum 2.',
                [('a', 'nat', '2'), ('b', 'nat', '3'), ('minimum', 'nat', '2')], None,
                'all', {'xs': 'items', 'p': 'eligible'}, 'list(nat)'),
            'insensitive': ('The amount is at most 10.', 'Amount 2.', [('amount', 'nat', '2')],
                ('limit', 'nat', '10'), 'compare', {'a': 'amount', 'b': 'limit', 'cmp': '≤'}, None),
            'refuted': ('The amount is 3.', 'Amount 2.', [('amount', 'nat', '2')],
                ('claimed', 'nat', '3'), 'equal', {'a': 'amount', 'b': 'claimed'}, None),
            'auxiliary': ('The source amount equals itself.', 'Amount 2.', [('amount', 'nat', '2')],
                None, 'equal', {'a': 'amount', 'b': 'amount'}, None),
        }
        report_text, source_text, specs, reported, shape, slots, list_type = cases[family]
        report.write_text('<a id="R1"></a>\n' + report_text + '\n')
        source.write_text('<a id="S1"></a>\n' + source_text + '\n')
        self.root = md.prepare(SimpleNamespace(report=report, sources=[source], temp_root=base / 'runs',
            lean_version='4.24.0', formalization_profile='source-bound-v1', locale='en'))
        self.source_text, self.report_text = source_text, report_text
        self.assertIn('Formalization profile: source-bound-v1', (self.root / 'audit-policy.md').read_text())
        before_init = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        sb.initialize(self.root)
        self.assertEqual(before_init, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        for index, (ident, typ, token) in enumerate(specs):
            occurrence = None
            if source_text.count(token) > 1:
                occurrence = 1 if ident in {'threshold', 'minimum'} else 0
            sb.add_fact(SimpleNamespace(workspace=self.root, id=ident, type=typ,
                source='input/sources/1/source.md', anchor='S1', quote=source_text,
                token=token, occurrence=occurrence))
        if reported:
            ident, typ, token = reported
            sb.add_fact(SimpleNamespace(workspace=self.root, id=ident, type=typ,
                source='input/report.md', anchor='R1', quote=report_text, token=token))
        if list_type:
            with (self.root / 'source-bound/facts.md').open('a') as stream:
                stream.write(f'\n## Derived items\nType: {list_type}\nOperation: list\nInputs: a, b\n')
        if family == 'list':
            self.register_rule(source_text)
        self.claim(shape, slots, relation=relation, rules='eligible' if family == 'list' else '')
        if second:
            self.claim('compare', {'a': 'a', 'b': 'b', 'cmp': '>'}, ident='C2', append=True)
        self.ids = ['C1', 'C2'] if second else ['C1']
        (self.root / 'claims.md').write_text('# Claims\n\n' + md.table(
            ['Claim ID', 'Report anchor', 'Disposition', 'Reason'],
            [(ident, 'R1', 'audited', 'Synthetic fixture atomic comparison.') for ident in self.ids]))
        sb.regenerate(self.root)
        return self.root

    def register_rule(self, quote):
        (self.root / 'source-bound/registrations.md').write_text(
            '# Registrations\n\n## Rule eligible\nParameters: x:nat\nResult: bool\nDependencies: minimum\n'
            'Correspondence: Compare each supplied amount against the quoted minimum, inclusively.\n'
            'Source: input/sources/1/source.md\nAnchor: S1\nQuote-start: 0\nQuote:\n```\n' + quote + '\n```\n')
        (self.root / 'models/Rules.lean').write_text('import SourceBound.Facts\n'
            'def Rules.eligible (x : Nat) : Bool := decide (x >= SourceBound.Facts.minimum)\n')

    def claim(self, shape, slots, *, ident='C1', relation='supports', rules='', append=False):
        content = (f'## Claim {ident}\nReport-anchor: R1\nShape: {shape}\nVersion: 1\n'
            f'Polarity: positive\nProof-relation: {relation}\nRules: {rules}\nScope:\n'
            'Rationale: The selected finite comparison corresponds to this synthetic report claim.\n')
        content += ''.join(f'Slot {key}: {value}\n' for key, value in slots.items())
        with (self.root / 'source-bound/claims.md').open('a' if append else 'w') as stream:
            stream.write('\n' + content)

    def findings(self, statuses, individual=None):
        rendered = {r.claim.id: r for r in sb.load(self.root).rendered}
        for ident, status in statuses.items():
            r = rendered[ident]
            sec = {heading: 'Explicit synthetic analysis with bounded source coverage.' for heading in md.HEADINGS}
            sec.update({'Exact report claim': '> ' + self.report_text, 'Report anchor': 'R1',
                'Executive conclusion': 'The supplied values were compared under the stated rule.',
                'How verified': 'Extracted the quoted values and checked the finite comparison.',
                'Executive limits': 'Source authenticity and completeness were not established.',
                'Evidence': '### Source\n\n- Source: input/sources/1/source.md\n- Anchor: S1\n\n> ' + self.source_text,
                'Semantic state': 'refuted' if status == 'refuted' else 'supported',
                'Argument status': 'insufficient_support', 'Formal status': status,
                'Proofs': 'None; no acceptable formal proof reference.'})
            if status in {'proved', 'refuted', 'auxiliary'}:
                sec['Proofs'] = ('### Proof P1\n\n- Module: SourceBound.Claims\n- Theorem: ' + contract.lean_claim_name(ident)
                    + '\n- Target: ' + r.target + '\n- Relation: ' + ('auxiliary' if status == 'auxiliary' else str(r.relation)))
            (self.root / f'findings/{ident}.md').write_text('# ' + ident + '\n\n' + ''.join(
                '## ' + heading + '\n\n' + sec[heading] + '\n\n' for heading in md.HEADINGS))
            for step in md.STEPS:
                work = self.root / f'claim-work/{ident}/{step}.md'
                work.parent.mkdir(parents=True, exist_ok=True)
                work.write_text(f'# {ident}: {step}\n\nSynthetic fixture retained intermediate analysis.\n')
                outputs = [work]
                if step == 'verification' and individual:
                    result = dict((r.claim.id, v) for r, v in individual)[ident]
                    outputs.extend(result.logs)
                md.trace(SimpleNamespace(workspace=self.root, claim_id=ident, step=step,
                    status='completed', title='Individual ' + step,
                    description='Reviewed the exact synthetic claim and its bounded evidence.',
                    input=['input/sources/1/source.md', 'source-bound/claims.md'], output=outputs,
                    command='source_bound_audit.py check --workspace ' + str(self.root) if step == 'verification' else None,
                    result='The actual result and limitations are retained in the linked fixture artifacts.'))


class ProfileStaticIntegrationTests(ProfileFixtures, unittest.TestCase):
    def test_generated_quote_and_unknown_profile_tampering(self):
        self.workspace()
        generated = self.root / 'models/SourceBound/Facts.lean'
        generated.write_text(generated.read_text() + '\n-- tampered\n')
        with self.assertRaisesRegex(ValueError, 'regeneration mismatch'):
            sb.load(self.root)
        sb.regenerate(self.root)
        facts = self.root / 'source-bound/facts.md'
        facts.write_text(facts.read_text().replace('First EUR 1.00', 'First EUR 9.00'))
        with self.assertRaisesRegex(ValueError, 'Quote bytes'):
            sb.load(self.root)
        with self.assertRaisesRegex(ValueError, 'Unsupported formalization profile'):
            sb.profile({'Formalization profile': 'unknown'})

    def test_missing_sandbox_blocks_and_status_promotions_reject(self):
        self.workspace('threshold')
        args = SimpleNamespace(lean='unused', command_prefix=())
        with patch.object(markdown_proofs, '_execute', side_effect=AssertionError('Must not launch')):
            _, result = sb.run(self.root, args)
        self.assertEqual(result[0][1].decision.status, contract.FormalStatus.BLOCKED)
        self.assertIn(contract.GuardFlag.INCOMPLETE, result[0][1].flags)
        self.findings({'C1': 'proved'})
        with self.assertRaisesRegex(ValueError, 'guard precedence'):
            md.check(SimpleNamespace(workspace=self.root, lean='unused', timeout=10, command_prefix=()))
        self.assertIn('Status: failed', (self.root / 'validation.md').read_text())

    def test_numeric_rule_and_unregistered_authored_dependency_reject(self):
        self.workspace('list')
        rules = self.root / 'models/Rules.lean'
        rules.write_text(rules.read_text().replace('SourceBound.Facts.minimum', '0'))
        with self.assertRaises(ValueError):
            sb.regenerate(self.root)
        self.register_rule(self.source_text)
        (self.root / 'models/Unexpected.lean').write_text('def stray : Nat := 1\n')
        with self.assertRaisesRegex(ValueError, 'Only generated'):
            sb.load(self.root)

    def test_unsupported_shape_not_formalized_and_rendered(self):
        self.workspace('threshold')
        self.claim('unsupported', {})
        sb.regenerate(self.root)
        self.findings({'C1': 'not_attempted'})
        md.check(SimpleNamespace(workspace=self.root, lean='unused', timeout=10))
        md.render(SimpleNamespace(workspace=self.root))
        inspection = json.loads(md.inspect(SimpleNamespace(workspace=self.root, machine=True)))
        self.assertEqual(inspection['formalization_profile'], 'source-bound-v1')
        self.assertTrue(inspection['report_fresh'])
        self.assertIn('not formalized', (self.root / 'audit-report.md').read_text())
        self.assertNotIn('theorem', (self.root / 'models/SourceBound/Claims.lean').read_text())

    def test_source_dimensions_and_calendar_units_are_enforced(self):
        self.workspace('threshold')
        state = sb.load(self.root)
        changed = contract.ResolvedFacts(state.facts.document, state.facts.values,
            (('amount', 'day'), ('threshold', 'item')))
        rendered = sb.render_claim(state.claims[0], changed, (), contract.ProofRelation.SUPPORTS)
        self.assertIn('dimensions', rendered.not_formalized_reason)

    def test_recompute_does_not_reparse_quotes_and_recomputes_descendants(self):
        self.workspace('money')
        state = sb.load(self.root)
        with patch.object(facts_module, 'parse_token', side_effect=AssertionError('Synthetic values cannot be re-extracted')):
            mutant = facts_module.recompute(state.facts, {'a': 101})
        values = {v.id: v.value for v in mutant.values}
        self.assertEqual(values['items'], (101, 200))
        self.assertEqual(values['reported'], 300)
        self.assertEqual(mutant.document, state.facts.document)

    def test_cli_shapes_and_duplicate_add_rejection(self):
        self.workspace('threshold')
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(sb.main(['shapes']), 0)
        self.assertEqual(len(stdout.getvalue().splitlines()), 12)
        before = (self.root / 'source-bound/facts.md').read_bytes()
        from contextlib import redirect_stderr
        with redirect_stderr(stderr):
            result = sb.main(['add-fact', '--workspace', str(self.root), '--id', 'amount', '--type', 'nat',
                '--source', 'input/sources/1/source.md', '--anchor', 'S1', '--quote', self.source_text,
                '--token', '5', '--occurrence', '0'])
        self.assertEqual(result, 1)
        self.assertEqual(before, (self.root / 'source-bound/facts.md').read_bytes())

    def test_source_locale_and_generated_metadata_are_bound(self):
        self.workspace()
        names = dict(md.fingerprints(self.root))
        self.assertIn('@version/parser', names)
        self.assertIn('@implementation/source_bound_facts.py', names)
        self.assertIn('source-bound/claims.md', names)
        locale = self.root / 'input/source-bound-bindings.md'
        locale.write_text(locale.read_text().replace('| en |', '| ro |'))
        with self.assertRaisesRegex(ValueError, 'Input changed'):
            sb.load(self.root)


@unittest.skipIf(sys.platform == 'darwin' and not os.environ.get('AUDIT_TEST_LEAN'),
                 'macOS has no Lean; run the sb-integration Linux stream')
class ProfileLeanIntegrationTests(ProfileFixtures, unittest.TestCase):
    def setUp(self):
        self.lean = os.environ.get('AUDIT_TEST_LEAN')
        self.assertTrue(self.lean and Path(self.lean).is_file(), 'Linux requires AUDIT_TEST_LEAN')
        self.assertIsNotNone(shutil.which('bwrap'), 'Linux requires reviewed no-network Bubblewrap')
        self.prefix = (shutil.which('bwrap'), '--unshare-net', '--die-with-parent',
                       '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc', '--')

    def check_args(self):
        return SimpleNamespace(workspace=self.root, lean=self.lean, timeout=10, command_prefix=self.prefix)

    def full(self, family, status='proved', **kwargs):
        self.workspace(family, **kwargs)
        _, results = sb.run(self.root, self.check_args())
        self.assertEqual(results[0][1].decision.status, status, (self.root, results[0][1]))
        self.findings({'C1': status}, results)
        md.check(self.check_args())
        md.render(SimpleNamespace(workspace=self.root))
        inspection = json.loads(md.inspect(SimpleNamespace(workspace=self.root, machine=True)))
        self.assertTrue(inspection['report_fresh'], inspection)
        self.assertTrue(inspection['trace_complete'])
        self.assertEqual(inspection['proof_results']['integrity'], 'current')
        return results

    def test_money_sum_complete_workspace(self):
        self.full('money')

    def test_threshold_complete_workspace(self):
        self.full('threshold')

    def test_deadline_complete_workspace(self):
        self.full('deadline')

    def test_list_predicate_complete_workspace(self):
        self.full('list')

    def test_rule_change_after_verification_invalidates_render_and_inspect(self):
        self.full('list')
        rule = self.root / 'models/Rules.lean'
        rule.write_text(rule.read_text() + '\n-- Reviewed rule changed after check.\n')
        with self.assertRaisesRegex(ValueError, 'changed after validation'):
            md.render(SimpleNamespace(workspace=self.root))
        inspection = json.loads(md.inspect(SimpleNamespace(workspace=self.root, machine=True)))
        self.assertFalse(inspection['report_fresh'])
        self.assertEqual(inspection['proof_results']['integrity'], 'stale_or_failed')

    def test_all_mutations_survive_blocks_and_has_no_reference(self):
        results = self.full('insensitive', 'blocked')
        self.assertIn(contract.GuardFlag.NO_TESTED_SOURCE_SENSITIVITY, results[0][1].flags)
        self.assertIsNone(results[0][1].proof_reference)
        self.assertIn('not depending on sources under these perturbations', (self.root / 'source-bound/results.md').read_text())
        # Authored status promotion must fail, and a stale delivery cannot remain fresh.
        finding = self.root / 'findings/C1.md'
        text_ = finding.read_text().replace('## Formal status\n\nblocked', '## Formal status\n\nproved')
        finding.write_text(text_)
        with self.assertRaises(ValueError):
            md.check(self.check_args())
        self.assertFalse(json.loads(md.inspect(SimpleNamespace(workspace=self.root, machine=True)))['report_fresh'])

    def test_transcription_auxiliary_and_refutation(self):
        result = self.full('auxiliary', 'auxiliary')
        self.assertIn(contract.GuardFlag.TRANSCRIPTION, result[0][1].flags)
        self.full('refuted', 'refuted', relation='refutes')

    def test_aggregate_claims_are_checked_in_separate_units(self):
        self.workspace('money', second=True)
        _, results = sb.run(self.root, self.check_args())
        self.assertEqual([str(v.decision.status) for _, v in results], ['proved', 'blocked'])
        self.findings({'C1': 'proved', 'C2': 'blocked'}, results)
        md.check(self.check_args())
        md.render(SimpleNamespace(workspace=self.root))
        first_unit = Path(results[0][1].proof_reference).read_text()
        self.assertIn('theorem SourceBound.Claims.C1', first_unit)
        self.assertNotIn('theorem SourceBound.Claims.C2', first_unit)
        self.assertEqual(first_unit.count('#print axioms'), 1)


if __name__ == '__main__':
    unittest.main()
