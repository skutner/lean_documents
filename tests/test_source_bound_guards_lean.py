"""Phase C's own generated fixtures, protocol doubles and real Lean processes.

Never loads validation/runs or another phase's private implementation. Linux tests
require AUDIT_TEST_LEAN; the sandbox is the same reviewed Bubblewrap no-network
shape used by the coordinator's host. Retain every workspace under repo tmp/.
"""
from dataclasses import replace
import ast
from datetime import date
from fractions import Fraction
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/evidence-to-proof-review/scripts'))
import source_bound_contract as c
import source_bound_proofs as p

LEAN = os.environ.get('AUDIT_TEST_LEAN')
TYPES = ROOT / 'skills/evidence-to-proof-review/assets/source_bound/Types.lean'


def fixture(kind='nat', source=2, report=2, *, duplicate=False, with_list=False):
    type_ = c.ScalarType(kind, 'EUR' if kind == 'money' else 'state' if kind == 'enum' else None)
    domains = (c.EnumDomain('state', '1', ('Absent', 'Present')),) if kind == 'enum' else ()
    records, values = [], []
    for ident, value, origin in [('source', source, c.Origin.SOURCE), ('reported', report, c.Origin.REPORT)]:
        path = 'input/sources/register.md' if origin == c.Origin.SOURCE else 'input/report.md'
        evidence = c.EvidenceReference(path, 'P1', str(value), 0)
        provenance = c.Provenance(evidence, c.ByteSpan(0, len(str(value))), path,
                                  'a' * 64, 'b' * 64, 'c' * 64, (), 'reviewed')
        records.append(c.FactRecord(ident, ident, type_, origin, str(value), path, 'P1',
            str(value), 0, provenance.selection, 'en', None))
        values.append(c.ResolvedValue(ident, type_, value,
            (ident,) if origin == c.Origin.SOURCE else (),
            (ident,) if origin == c.Origin.REPORT else (), (provenance,)))
    if duplicate:
        records.append(replace(records[0], id='alias', name='alias'))
        values.append(replace(values[0], id='alias', source_leaves=('alias',)))
    derived = ()
    if with_list:
        derived = (c.DerivedRecord('rows', c.ListType(type_), c.Operation.LIST,
                                  (c.Reference('source'),)),)
        values.append(c.ResolvedValue('rows', c.ListType(type_), (source,), ('source',), (), values[0].provenance))
    facts = c.ResolvedFacts(c.FactsDocument(tuple(records), derived, domains), tuple(values))
    slots = (c.ClaimSlot('a', c.SlotKind.VALUE, c.Reference('rows' if with_list else 'source')),
             c.ClaimSlot('b', c.SlotKind.VALUE, c.Reference('reported')))
    claim = c.ClaimRecord('C01', 'R1', 'sum' if with_list else 'equal', '1', slots,
                          c.Polarity.POSITIVE, (), (), 'Own generated guard fixture')
    return facts, claim


def literal(type_, value, domains):
    if isinstance(type_, c.RecordType):
        fields = dict(value)
        return '⟨' + ', '.join(literal(t, fields[n], domains) for n, t in type_.fields) + '⟩'
    if isinstance(type_, c.ListType):
        return '[' + ', '.join(literal(type_.element, v, domains) for v in value) + ']'
    if type_.kind in {'nat', 'int'}:
        return str(value)
    if type_.kind == 'bool':
        return 'true' if value else 'false'
    if type_.kind == 'enum':
        return dict(c.lean_type_binding(type_, domains).constructors)[value]
    if type_.kind == 'date':
        return f'⟨{value.toordinal()}⟩'
    if type_.kind == 'ratio':
        return f'⟨{value.numerator}, {value.denominator}⟩'
    return f'⟨{value}⟩'


def generate(facts, rules):
    binding = facts.bindings
    bodies = {'SourceBound.Facts': [], 'SourceBound.Derived': []}
    for domain in facts.document.enum_domains:
        constructors = c.lean_enum_constructors(domain)
        bodies['SourceBound.Facts'].append('inductive ' + c.lean_type_binding(c.ScalarType('enum', domain.id), facts.document.enum_domains).lean_type + ' where\n' +
            '\n'.join('  | ' + name.rsplit('.', 1)[1] for _, name in constructors) + '\n  deriving DecidableEq, Repr')
    for typ, type_binding in binding.types.items():
        if isinstance(typ, c.RecordType):
            bodies['SourceBound.Facts'].append('structure ' + type_binding.lean_type + ' where\n' +
                '\n'.join('  ' + lean_name + ' : ' + lean_type for _, lean_name, lean_type in type_binding.fields)
                + '\n  deriving DecidableEq, Repr')
    records = facts.document.records()
    for value in facts.values:
        b = binding.values[value.id]
        expression = literal(value.type, value.value, facts.document.enum_domains)
        record = records[value.id]
        if isinstance(record, c.DerivedRecord) and record.operation == c.Operation.RECORD:
            expression = '⟨' + ', '.join(binding.values[r.id].name for _, r in record.fields) + '⟩'
        elif isinstance(record, c.DerivedRecord) and record.operation == c.Operation.FIELD:
            source = next(v for v in facts.values if v.id == record.inputs[0].id)
            field = next(lean_name for wire, lean_name, _ in binding.types[source.type].fields if wire == record.field)
            expression = f'({binding.values[source.id].name}).{field}'
        bodies[b.module].append(f'def {b.name} : {b.lean_type} := {expression}')
    return (c.GeneratedFile('models/SourceBound/Types.lean', TYPES.read_text()),) + tuple(
        c.GeneratedFile('models/' + module.replace('.', '/') + '.lean',
            ''.join(f'import {m}\n' for m in c.MODULE_IMPORTS[module]) + '\n'.join(body) + '\n')
        for module, body in bodies.items())


def render(claim, facts, rules, relation):
    binding = facts.bindings
    left = binding.values['rows' if claim.shape == 'sum' else 'source'].name
    right = binding.values['reported'].name
    type_ = next(v.type for v in facts.values if v.id == 'source')
    if claim.shape == 'sum':
        left = f'({left}).foldl (fun a b => a + b) 0'
    elif type_.kind in {'money', 'date', 'ratio'}:
        left, right = c.lean_comparison_operands(binding.types[type_], left, right)
    operator = '≤' if claim.rationale == 'insensitive' else '='
    proposition = f'{left} {operator} {right}'
    if claim.polarity == c.Polarity.NEGATIVE:
        proposition = f'¬ ({proposition})'
    target = proposition if relation == c.ProofRelation.SUPPORTS else f'¬ ({proposition})'
    file = c.GeneratedFile('models/SourceBound/Claims.lean', 'import SourceBound.Derived\n' +
                           f'theorem {c.lean_claim_name(claim.id)} : ({target}) := by decide\n')
    return c.RenderedClaim(claim, relation, proposition, target, 'Fixture comparison.',
        tuple(s.value for s in claim.slots), (file,))


def rule_render(claim, facts, rules, relation):
    rendered = render(claim, facts, rules, relation)
    target = f'Rules.{rules[0].id} {facts.bindings.values["rows"].name} = true'
    file = c.GeneratedFile('models/SourceBound/Claims.lean', 'import SourceBound.Derived\n' +
        f'theorem {c.lean_claim_name(claim.id)} : ({target}) := by decide\n')
    return replace(rendered, proposition=target, target=target, files=(file,))


def derived_render(claim, facts, rules, relation):
    rendered = render(claim, facts, rules, relation)
    target = facts.bindings.values['selected'].name + ' = ' + facts.bindings.values['source'].name
    file = c.GeneratedFile('models/SourceBound/Claims.lean', 'import SourceBound.Derived\n' +
        f'theorem {c.lean_claim_name(claim.id)} : ({target}) := by decide\n')
    return replace(rendered, proposition=target, target=target, files=(file,))


def special_render(claim, facts, rules, relation):
    rendered = render(claim, facts, rules, relation)
    name = facts.bindings.values['source'].name
    target = 'True' if claim.rationale == 'root_true' else f'{name} = {name}'
    file = c.GeneratedFile('models/SourceBound/Claims.lean', 'import SourceBound.Derived\n' +
        f'theorem {c.lean_claim_name(claim.id)} : ({target}) := by decide\n')
    return replace(rendered, proposition=target, target=target, files=(file,))


def recompute(facts, changes, rules):
    values = {v.id: v for v in facts.values}
    for ident, value in changes.items():
        values[ident] = replace(values[ident], value=value)
    for record in facts.document.derived:
        if record.id not in changes:
            if record.operation == c.Operation.RECORD:
                value = tuple((n, values[r.id].value) for n, r in record.fields)
            elif record.operation == c.Operation.FIELD:
                value = dict(values[record.inputs[0].id].value)[record.field]
            else:
                value = tuple(values[r.id].value for r in record.inputs)
            values[record.id] = replace(values[record.id], value=value)
    return replace(facts, values=tuple(values[v.id] for v in facts.values))


def invalid_zero_recompute(facts, changes, rules):
    if 0 in changes.values():
        raise c.ContractError('Synthetic denominator became zero')
    return recompute(facts, changes, rules)


def broken_generate(facts, rules):
    files = generate(facts, rules)
    if facts.values[0].value != 2:
        files = tuple(replace(f, content=f.content + '\ndef broken : Nat := unknownBuildValue\n')
                      if f.path.endswith('/Facts.lean') else f for f in files)
    return files


def slow_generate(facts, rules):
    # Own generator fixture, never authored Rules: deterministic real Lean delay
    # tests subprocess timeout without racing kernel stack-overflow diagnostics.
    files = generate(facts, rules)
    if facts.values[0].value != 2:
        files = tuple(replace(f, content=f.content + '\n#eval IO.sleep 3000\n')
                      if f.path.endswith('/Facts.lean') else f for f in files)
    return files


def inapplicable_render(claim, facts, rules, relation):
    rendered = render(claim, facts, rules, relation)
    if facts.values[0].value != 2:
        return replace(rendered, target=None, files=(), not_formalized_reason='nonempty-domain gate failed')
    return rendered


class MutationAndDependencyTests(unittest.TestCase):
    def test_deterministic_alternatives_and_boundaries(self):
        cases = [(c.ScalarType('nat'), 0, (1,)), (c.ScalarType('nat'), 2, (0, 3)),
                 (c.ScalarType('money', 'EUR'), 0, (-1, 1)),
                 (c.ScalarType('money', 'EUR'), 2, (1, 3, -2)),
                 (c.ScalarType('bool'), False, (True,)),
                 (c.ScalarType('ratio'), Fraction(1, 2), (Fraction(0), Fraction(1))),
                 (c.ScalarType('date'), date.min, (date(1, 1, 2),)),
                 (c.ScalarType('date'), date.max, (date(9999, 12, 30),)),
                 (c.ListType(c.ScalarType('nat')), (), ()),
                 (c.ListType(c.ScalarType('nat')), (2,), ((), (2, 2)))]
        for type_, value, expected in cases:
            with self.subTest(type=type_, value=value):
                self.assertEqual(p.mutations(type_, value), expected)
        enum = c.ScalarType('enum', 'state')
        self.assertEqual(p.mutations(enum, 'b', (c.EnumDomain('state', '1', ('b', 'a', 'c')),)), ('c',))
        self.assertEqual(p.mutations(enum, 'b', (c.EnumDomain('state', '1', ('b',)),)), ())

    def test_source_report_derived_lists_and_duplicate_selections(self):
        facts, claim = fixture(duplicate=True, with_list=True)
        rendered = render(claim, facts, (), c.ProofRelation.SUPPORTS)
        self.assertEqual(p.dependency_groups(rendered, facts, ()), (('alias', 'source'), ('rows',)))
        # Same token on another row is a distinct selection; cached leaves cannot hide it.
        alias = facts.values[-2]
        changed = replace(alias, provenance=(replace(alias.provenance[0], evidence=
            replace(alias.provenance[0].evidence, anchor='P2')),))
        facts = replace(facts, values=tuple(changed if v.id == 'alias' else v for v in facts.values))
        rendered = replace(rendered, dependencies=rendered.dependencies + (c.Reference('alias'),))
        self.assertEqual(p.dependency_groups(rendered, facts, ()), (('alias',), ('rows',), ('source',)))

    def test_unit_aliases_do_not_split_identical_amount_selections(self):
        facts, claim = fixture('money', 12, 12, duplicate=True)
        unit = replace(facts.document.facts[0], id='unit', name='unit', anchor='Header')
        unit_alias = replace(unit, id='unit_alias', name='unit_alias')
        uv = replace(facts.values[0], id='unit', provenance=(replace(facts.values[0].provenance[0],
            evidence=replace(facts.values[0].provenance[0].evidence, anchor='Header')),))
        records = tuple(replace(f, unit_source=c.Reference('unit_alias' if f.id == 'alias' else 'unit'))
                        if f.origin == c.Origin.SOURCE else f for f in facts.document.facts)
        facts = replace(facts, document=replace(facts.document, facts=records + (unit, unit_alias)),
            values=facts.values + (uv, replace(uv, id='unit_alias')))
        rendered = render(claim, facts, (), c.ProofRelation.SUPPORTS)
        self.assertEqual(p.dependency_groups(rendered, facts, ()),
                         (('alias', 'source'), ('unit', 'unit_alias')))

    def test_typed_build_diagnostic_overlaps_false_target(self):
        output = ('unit.lean:7:2: error(lean.unknownIdentifier): Unknown identifier bad\n'
                  'unit.lean:9:3: error: Tactic `decide` proved that the proposition\n'
                  ' False\nis false\n')
        self.assertEqual(p._classify(1, output, Path('unit.lean'), 9, 'C'), 'inconclusive')

    def test_unit_and_rule_dependencies_not_cached_taint(self):
        facts, claim = fixture()
        policy = replace(facts.document.facts[0], id='policy', name='policy', anchor='P2')
        pv = replace(facts.values[0], id='policy', provenance=(replace(facts.values[0].provenance[0],
            evidence=replace(facts.values[0].provenance[0].evidence, anchor='P2')),))
        facts = replace(facts, document=replace(facts.document, facts=facts.document.facts + (policy,)),
                        values=facts.values + (pv,))
        rule = c.RuleRegistration('policyRule', (), c.ScalarType('bool'), (), 'fixture', (c.Reference('policy'),))
        rendered = replace(render(claim, facts, (rule,), c.ProofRelation.SUPPORTS), dependencies=(c.Reference('policyRule'),))
        self.assertEqual(p.dependency_groups(rendered, facts, (rule,)), (('policy',), ('source',)))


@unittest.skipUnless(LEAN or sys.platform.startswith('linux'),
                     'AUDIT_TEST_LEAN is unset; real Lean checks require the Linux host')
class LeanGuardTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(LEAN, 'Required Linux check: AUDIT_TEST_LEAN is unset')
        self.assertTrue(Path(LEAN).is_file(), 'AUDIT_TEST_LEAN must point to Lean')
        self.assertIsNotNone(shutil.which('bwrap'), 'Required no-network sandbox missing')
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='sb-guards-', dir=ROOT / 'tmp'))
        self.sandbox = (shutil.which('bwrap'), '--unshare-net', '--die-with-parent',
                        '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc', '--', LEAN)
        self.engine = p.ProofEngine(generate, render, recompute)
        self.authored = (c.GeneratedFile('models/Rules.lean', 'import SourceBound.Facts\n'),)
        self.attempt = self.root / 'tmp/attempt'

    def prepare(self, facts=None, claim=None, relation=c.ProofRelation.SUPPORTS):
        if facts is None:
            facts, claim = fixture()
        rendered = self.engine.render_claim(claim, facts, (), relation)
        for file in self.engine.generate_facts(facts, ()) + rendered.files + self.authored:
            path = self.root / file.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(file.content)
        return facts, rendered

    def run_guard(self, facts, rendered, **kwargs):
        with p.using_engine(self.engine):
            return p.check_proof(self.root, rendered, facts, (), self.authored,
                budget=kwargs.pop('budget', c.GuardBudget()), attempt_directory=self.attempt,
                sandbox_command=kwargs.pop('sandbox_command', self.sandbox), **kwargs)

    def assert_logs(self, result):
        self.assertTrue(result.logs)
        for path in result.logs:
            self.assertTrue(Path(path).exists(), path)
        self.assertFalse(list(self.root.rglob('*.json')))
        self.assertFalse(list(self.root.rglob('*.olean')))

    def test_lean_catalogue_application_order(self):
        source = ROOT / 'tests/fixtures/source_bound/guards/catalogue_api.lean'
        result = subprocess.run((*self.sandbox, str(source)), capture_output=True, text=True,
                                timeout=10, env={'PATH': os.environ['PATH'], 'LEAN_PATH': ''})
        (self.root / 'catalogue.log.md').write_text(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('List.all', result.stdout)

    def test_authored_all_rule_compiles_and_policy_leaf_is_perturbed(self):
        facts, claim = fixture(with_list=True)
        policy = replace(facts.document.facts[0], id='threshold', name='threshold', anchor='Policy')
        pv = replace(facts.values[0], id='threshold', source_leaves=('threshold',), provenance=(
            replace(facts.values[0].provenance[0], evidence=replace(
                facts.values[0].provenance[0].evidence, anchor='Policy')),))
        facts = replace(facts, document=replace(facts.document, facts=facts.document.facts + (policy,)),
                        values=facts.values + (pv,))
        rule = c.RuleRegistration('check', (('xs', c.ListType(c.ScalarType('nat'))),),
            c.ScalarType('bool'), (pv.provenance[0].evidence,), 'Each row equals the quoted threshold.',
            (c.Reference('threshold'),))
        rules = (rule,)
        claim = replace(claim, rules=(c.Reference('check'),))
        self.engine = p.ProofEngine(generate, rule_render, recompute)
        self.authored = (c.GeneratedFile('models/Rules.lean', 'import SourceBound.Facts\n'
            'def Rules.check (xs : List (Nat)) : Bool := List.all xs '
            '(fun n => n == SourceBound.Facts.threshold)\n'),)
        rendered = rule_render(claim, facts, rules, c.ProofRelation.SUPPORTS)
        for file in generate(facts, rules) + rendered.files + self.authored:
            path = self.root / file.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(file.content)
        result = self.engine.check_proof(self.root, rendered, facts, rules, self.authored,
            budget=c.GuardBudget(), attempt_directory=self.attempt, sandbox_command=self.sandbox)
        self.assertEqual(result.decision.status, c.FormalStatus.PROVED, result)
        policy_leaf = next(leaf for leaf in result.leaves if leaf.leaf_ids == ('threshold',))
        self.assertEqual(policy_leaf.confirmed, 2)

    def test_root_true_and_auxiliary_precedence_keep_all_flags(self):
        self.engine = p.ProofEngine(generate, special_render, recompute)
        facts, claim = fixture()
        facts, rendered = self.prepare(facts, replace(claim, rationale='root_true'))
        with patch.object(p.subprocess, 'run') as execute:
            result = self.run_guard(facts, rendered)
            execute.assert_not_called()
        self.assertEqual(result.decision.status, c.FormalStatus.REJECTED)
        self.assertEqual(result.flags, frozenset({c.GuardFlag.ROOT_TRUE, c.GuardFlag.TRANSCRIPTION}))
        self.attempt = self.root / 'tmp/auxiliary'
        facts, rendered = self.prepare(facts, replace(claim, rationale='identity'))
        result = self.run_guard(facts, rendered)
        self.assertEqual(result.decision.status, c.FormalStatus.AUXILIARY)
        self.assertEqual(result.decision.reference_kind, 'auxiliary')
        self.assertEqual(result.flags, frozenset({c.GuardFlag.TRANSCRIPTION,
                                               c.GuardFlag.NO_TESTED_SOURCE_SENSITIVITY}))
        self.assertIsNotNone(result.proof_reference)
        self.assertIn('not depending on sources under these perturbations',
                      '\n'.join(Path(path).read_text() for path in result.logs))

    def test_generated_record_field_alias_is_auxiliary(self):
        facts, claim = fixture()
        record_type = c.RecordType((('amount', c.ScalarType('nat')),))
        row = c.DerivedRecord('row', record_type, c.Operation.RECORD, (),
                              fields=(('amount', c.Reference('source')),))
        selected = c.DerivedRecord('selected', c.ScalarType('nat'), c.Operation.FIELD,
                                   (c.Reference('row'),), field='amount')
        rv = c.ResolvedValue('row', record_type, (('amount', 2),), ('source',), (), facts.values[0].provenance)
        sv = replace(facts.values[0], id='selected')
        facts = replace(facts, document=replace(facts.document, derived=(row, selected)),
                        values=facts.values + (rv, sv))
        claim = replace(claim, slots=(c.ClaimSlot('a', c.SlotKind.VALUE, c.Reference('selected')),
                                      c.ClaimSlot('b', c.SlotKind.VALUE, c.Reference('source'))))
        self.engine = p.ProofEngine(generate, derived_render, recompute)
        facts, rendered = self.prepare(facts, claim)
        result = self.run_guard(facts, rendered)
        self.assertEqual(result.decision.status, c.FormalStatus.AUXILIARY, result)
        self.assertIn(c.GuardFlag.TRANSCRIPTION, result.flags)
        self.assertIn(c.GuardFlag.NO_TESTED_SOURCE_SENSITIVITY, result.flags)
        self.assertIsNotNone(result.proof_reference)

    def test_source_sensitive_exact_target_axioms_single_unit(self):
        facts, rendered = self.prepare()
        result = self.run_guard(facts, rendered)
        self.assertEqual(result.decision.status, c.FormalStatus.PROVED, result)
        self.assertEqual(result.leaves[0].confirmed, 2)
        self.assertEqual(result.leaves[0].attempted, 2)
        self.assertEqual(len(list(self.attempt.glob('*.lean'))), 5)
        content = Path(result.proof_reference).read_text()
        self.assertIn('#print axioms ' + c.lean_claim_name('C01'), content)
        self.assertIn('example : (' + rendered.target + ') := ' + c.lean_claim_name('C01'), content)
        self.assertEqual(len(result.module_to_unit), 5)
        self.assert_logs(result)

    def test_all_mutations_survive_is_no_tested_source_sensitivity(self):
        facts, claim = fixture(report=100)
        facts, rendered = self.prepare(facts, replace(claim, rationale='insensitive'))
        result = self.run_guard(facts, rendered)
        self.assertEqual(result.flags, frozenset({c.GuardFlag.NO_TESTED_SOURCE_SENSITIVITY}))
        self.assertEqual(result.decision.status, c.FormalStatus.BLOCKED)
        self.assertIsNone(result.proof_reference)
        self.assertEqual(result.leaves[0].confirmed, 0)

    def test_build_error_is_inconclusive_not_target_failure(self):
        self.engine = p.ProofEngine(broken_generate, render, recompute)
        facts, rendered = self.prepare()
        result = self.run_guard(facts, rendered)
        self.assertEqual(result.leaves[0].confirmed, 0)
        self.assertEqual(result.leaves[0].inconclusive, 2)
        self.assertIn(c.GuardFlag.INCOMPLETE, result.flags)
        self.assertFalse(list(self.attempt.glob('negation-*.lean')))

    def test_timeout_is_inconclusive_not_target_failure(self):
        facts, rendered = self.prepare()
        actual = subprocess.run
        def timeout_mutant(command, **kwargs):
            if 'mutant-' in command[-1]:
                raise subprocess.TimeoutExpired(command, kwargs['timeout'], output='partial Lean output')
            return actual(command, **kwargs)
        with patch.object(p.subprocess, 'run', side_effect=timeout_mutant):
            result = self.run_guard(facts, rendered)
        self.assertEqual(result.leaves[0].confirmed, 0)
        self.assertEqual(result.leaves[0].inconclusive, 2)
        self.assertIn(c.GuardFlag.INCOMPLETE, result.flags)
        self.assertIn('TIMEOUT', '\n'.join(Path(s).read_text() for s in result.logs))
        self.assertFalse(list(self.attempt.glob('negation-*.lean')))

    def test_real_lean_timeout_is_inconclusive(self):
        self.engine = p.ProofEngine(slow_generate, render, recompute)
        facts, rendered = self.prepare()
        result = self.run_guard(facts, rendered, budget=c.GuardBudget(process_seconds=1))
        self.assertTrue(result.baseline_checked)
        self.assertEqual(result.leaves[0].confirmed, 0)
        self.assertEqual(result.leaves[0].inconclusive, 2)
        self.assertIn('TIMEOUT', '\n'.join(Path(s).read_text() for s in result.logs))
        self.assertFalse(list(self.attempt.glob('negation-*.lean')))

    def test_mutant_and_time_caps_are_incomplete(self):
        facts, rendered = self.prepare()
        result = self.run_guard(facts, rendered, budget=c.GuardBudget(mutants=1))
        self.assertIn(c.GuardFlag.INCOMPLETE, result.flags)
        self.assertEqual(result.leaves[0].scheduled, 2)
        self.assertEqual(result.leaves[0].attempted, 1)
        self.assertLessEqual(len(list(self.attempt.glob('*.pending.md'))), 3)
        self.attempt = self.root / 'tmp/time-cap'
        result = self.run_guard(facts, rendered, budget=c.GuardBudget(theorem_seconds=0))
        self.assertIn(c.GuardFlag.INCOMPLETE, result.flags)
        self.assertFalse(list(self.attempt.glob('*.pending.md')))
        with self.assertRaisesRegex(c.ContractError, 'hard caps'):
            self.run_guard(facts, rendered, budget=c.GuardBudget(mutants=65))

    def test_interrupted_attempt_resume_and_hash_mismatch(self):
        facts, rendered = self.prepare()
        actual = subprocess.run
        interrupted = False
        def stop_once(command, **kwargs):
            nonlocal interrupted
            if 'mutant-' in command[-1] and not interrupted:
                interrupted = True
                raise KeyboardInterrupt('simulated interrupted process')
            return actual(command, **kwargs)
        with patch.object(p.subprocess, 'run', side_effect=stop_once):
            with self.assertRaises(KeyboardInterrupt):
                self.run_guard(facts, rendered)
        baseline_hash = p._hash((self.attempt / 'baseline.log.md').read_bytes())
        with patch.object(p.subprocess, 'run') as execute:
            capped = self.run_guard(facts, rendered, budget=c.GuardBudget(theorem_seconds=8))
            execute.assert_not_called()
        self.assertIn(c.GuardFlag.INCOMPLETE, capped.flags)
        result = self.run_guard(facts, rendered)
        self.assertTrue(result.baseline_checked)
        self.assertEqual(result.leaves[0].confirmed, 1)
        self.assertIn(c.GuardFlag.INCOMPLETE, result.flags)
        self.assertEqual(baseline_hash, p._hash((self.attempt / 'baseline.log.md').read_bytes()))
        (self.root / 'input').mkdir()
        (self.root / 'input/report.md').write_text('changed report bytes')
        with self.assertRaisesRegex(c.ContractError, 'Stale or tampered'):
            self.run_guard(facts, rendered)

    def test_resume_larger_budget_and_tampered_log(self):
        facts, rendered = self.prepare()
        first = self.run_guard(facts, rendered, budget=c.GuardBudget(mutants=1))
        self.assertIn(c.GuardFlag.INCOMPLETE, first.flags)
        result = self.run_guard(facts, rendered)
        self.assertEqual(result.decision.status, c.FormalStatus.PROVED)
        (self.attempt / 'baseline.log.md').write_text('forged success')
        with self.assertRaisesRegex(c.ContractError, 'tampered process'):
            self.run_guard(facts, rendered)

    def test_metadata_only_tampering_cannot_promote_baseline(self):
        facts, claim = fixture(report=9)
        facts, rendered = self.prepare(facts, claim)
        result = self.run_guard(facts, rendered)
        self.assertFalse(result.baseline_checked)
        path = self.attempt / 'baseline.result.md'
        record = ast.literal_eval(path.read_text().split('\n\n', 1)[1])
        record['code'] = 0
        record['output'] = "'SourceBound.Claims.C01' does not depend on any axioms"
        path.write_text('# Process evidence\n\n' + repr(record) + '\n')
        with self.assertRaisesRegex(c.ContractError, 'metadata disagrees'):
            self.run_guard(facts, rendered)

    def test_generated_tampering_and_missing_sandbox(self):
        facts, rendered = self.prepare()
        result = self.run_guard(facts, rendered, sandbox_command=())
        self.assertFalse(result.baseline_checked)
        self.assertIn(c.GuardFlag.INCOMPLETE, result.flags)
        (self.root / 'models/SourceBound/Facts.lean').write_text('def forged := 2\n')
        with self.assertRaisesRegex(c.ContractError, 'regeneration mismatch'):
            self.run_guard(facts, rendered)

    def test_all_scalar_types_and_duplicate_interning(self):
        cases = [('money', 12), ('date', date(2026, 1, 1)), ('bool', True),
                 ('enum', 'Present'), ('ratio', Fraction(1, 2))]
        for kind, value in cases:
            with self.subTest(kind=kind):
                facts, claim = fixture(kind, value, value, duplicate=True)
                self.attempt = self.root / ('tmp/' + kind)
                facts, rendered = self.prepare(facts, claim)
                result = self.run_guard(facts, rendered)
                self.assertEqual(result.decision.status, c.FormalStatus.PROVED, result)
                self.assertEqual(result.leaves[0].leaf_ids, ('alias', 'source'))
                self.assertGreater(result.leaves[0].confirmed, 0)

    def test_derived_list_and_element_sensitivity(self):
        facts, claim = fixture(with_list=True)
        facts, rendered = self.prepare(facts, claim)
        result = self.run_guard(facts, rendered)
        self.assertEqual(result.decision.status, c.FormalStatus.PROVED, result)
        self.assertEqual({leaf.leaf_ids for leaf in result.leaves}, {('rows',), ('source',)})
        self.assertTrue(all(leaf.confirmed for leaf in result.leaves))

    def test_invalid_synthetic_recomputation_retains_schedule(self):
        self.engine = p.ProofEngine(generate, render, invalid_zero_recompute)
        facts, rendered = self.prepare()
        result = self.run_guard(facts, rendered)
        self.assertTrue(result.baseline_checked)
        self.assertIn(c.GuardFlag.INCOMPLETE, result.flags)
        self.assertEqual(result.leaves[0].attempted, 2)
        self.assertEqual(result.leaves[0].confirmed, 1)
        self.assertEqual(result.leaves[0].inconclusive, 1)
        self.assertIn('Synthetic denominator became zero',
                      '\n'.join(Path(path).read_text() for path in result.logs))

    def test_applicability_does_not_prove_negation(self):
        self.engine = p.ProofEngine(generate, inapplicable_render, recompute)
        facts, rendered = self.prepare()
        result = self.run_guard(facts, rendered)
        self.assertIn(c.GuardFlag.INCOMPLETE, result.flags)
        self.assertEqual(result.leaves[0].confirmed, 0)
        self.assertFalse(list(self.attempt.glob('negation-*.lean')))

    def test_refutation_and_baseline_failure(self):
        facts, claim = fixture(report=9)
        facts, rendered = self.prepare(facts, claim)
        result = self.run_guard(facts, rendered)
        self.assertFalse(result.baseline_checked)
        self.assertIn(c.GuardFlag.BASELINE_FAILED, result.flags)
        facts, claim = fixture(source=0, report=1)
        facts, rendered = self.prepare(facts, claim, c.ProofRelation.REFUTES)
        self.attempt = self.root / 'tmp/refuted'
        result = self.run_guard(facts, rendered)
        self.assertEqual(result.decision.status, c.FormalStatus.REFUTED, result)


if __name__ == '__main__':
    unittest.main()
