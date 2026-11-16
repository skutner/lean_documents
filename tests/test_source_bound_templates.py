"""Catalogue rendering and real Lean truth-table checks, independent of phase A.

The tiny fixture emitter below stands in for A and only emits test-owned data.
Real Lean tests require AUDIT_TEST_LEAN; a configured missing binary is an error,
not a skip. Every execution keeps its source, stdout and stderr under tmp/.
"""
from dataclasses import replace
from datetime import date
from fractions import Fraction
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/evidence-to-proof-review/scripts'))
import source_bound_contract as c
import source_bound_templates as t

NAT, BOOL, DATE, RATIO = (c.ScalarType(k) for k in ('nat', 'bool', 'date', 'ratio'))
LEAN = os.environ.get('AUDIT_TEST_LEAN')
OPERATORS = {'=': 'equal to', '≠': 'not equal to', '<': 'less than',
             '≤': 'less than or equal to', '>': 'greater than', '≥': 'greater than or equal to'}


class Fixture:
    def __init__(self):
        self.facts, self.derived, self.values, self.rules = [], [], [], []
        self.rule_code = []
        self.domains = ()

    def scalar(self, ident, value, type_=NAT, origin=c.Origin.SOURCE):
        self.facts.append(c.FactRecord(ident, ident, type_, origin, str(value),
            'input/sources/data.md' if origin == c.Origin.SOURCE else 'input/report.md',
            'S1', str(value), 0, c.ByteSpan(0, len(str(value).encode())), 'en', None))
        self.values.append(c.ResolvedValue(ident, type_, value,
            (ident,) if origin == c.Origin.SOURCE else (),
            (ident,) if origin == c.Origin.REPORT else (), ()))
        return ident

    def derived_value(self, ident, type_, value, operation, inputs=(), fields=()):
        self.derived.append(c.DerivedRecord(ident, type_, operation,
            tuple(c.Reference(i) for i in inputs), tuple((k, c.Reference(v)) for k, v in fields)))
        self.values.append(c.ResolvedValue(ident, type_, value, (), (), ()))
        return ident

    def list(self, ident, inputs, element=NAT):
        values = {v.id: v.value for v in self.values}
        return self.derived_value(ident, c.ListType(element), tuple(values[i] for i in inputs),
                                  c.Operation.LIST, inputs)

    def record(self, ident, fields):
        values = {v.id: v for v in self.values}
        type_ = c.RecordType(tuple((k, values[i].type) for k, i in fields))
        return self.derived_value(ident, type_, tuple((k, values[i].value) for k, i in fields),
                                  c.Operation.RECORD, fields=fields)

    def rule(self, ident, argument, result, code, dependencies=()):
        self.rules.append(c.RuleRegistration(ident, (('x', argument),), result, (),
            'Test-owned pure rule fixture.', tuple(c.Reference(i) for i in dependencies)))
        self.rule_code.append(code)
        return ident

    def resolved(self):
        return c.ResolvedFacts(c.FactsDocument(tuple(self.facts), tuple(self.derived), self.domains),
                               tuple(self.values))

    def claim(self, shape, slots, ident='C01', polarity=c.Polarity.POSITIVE):
        specs = {s.name: s for s in c.SHAPES[shape].slots}
        parsed = tuple(c.ClaimSlot(k, specs[k].kind, c.Reference(v)
            if specs[k].kind in (c.SlotKind.VALUE, c.SlotKind.RULE) else v) for k, v in slots.items())
        rules = tuple(s.value for s in parsed if s.kind == c.SlotKind.RULE)
        return c.ClaimRecord(ident, 'R1', shape, '1', parsed, polarity, rules, (), 'Fixture claim.')

    def modules(self):
        """A stand-in, using the same frozen name/type/projection ABI as A."""
        resolved = self.resolved()
        bindings = resolved.bindings
        declarations, emitted = [], set()

        def emit_type(type_):
            if type_ in emitted:
                return
            if isinstance(type_, c.ListType):
                emit_type(type_.element)
            elif isinstance(type_, c.RecordType):
                for _, child in type_.fields:
                    emit_type(child)
                binding = bindings.types[type_]
                declarations.append(f'structure {binding.lean_type} where\n' + ''.join(
                    f'  {lean} : {ty}\n' for _, lean, ty in binding.fields) + '  deriving DecidableEq, Repr\n')
            elif type_.kind == 'enum':
                binding = bindings.types[type_]
                declarations.append(f'inductive {binding.lean_type} where\n' + ''.join(
                    f'  | {name.rsplit(".", 1)[1]}\n' for _, name in binding.constructors) +
                    '  deriving DecidableEq, Repr\n')
            emitted.add(type_)

        def literal(type_, value):
            if isinstance(type_, c.ListType):
                return '[' + ', '.join(literal(type_.element, x) for x in value) + ']'
            if isinstance(type_, c.RecordType):
                return '⟨' + ', '.join(literal(ty, dict(value)[k]) for k, ty in type_.fields) + '⟩'
            if type_.kind == 'bool':
                return 'true' if value else 'false'
            if type_.kind == 'money':
                return f'⟨{value}⟩'
            if type_.kind == 'date':
                return f'⟨{value.toordinal()}⟩'
            if type_.kind == 'ratio':
                return f'⟨{value.numerator}, {value.denominator}⟩'
            if type_.kind == 'enum':
                return dict(bindings.types[type_].constructors)[value]
            return str(value)

        for type_ in bindings.types:
            emit_type(type_)
        texts = {module: ''.join(f'import {i}\n' for i in c.MODULE_IMPORTS[module])
                 for module in c.MODULE_ORDER}
        texts['SourceBound.Types'] = (ROOT / 'skills/evidence-to-proof-review/assets/source_bound/Types.lean').read_text()
        texts['SourceBound.Facts'] += '\n'.join(declarations)
        for v in resolved.values:
            binding = bindings.values[v.id]
            texts[binding.module] += f'def {binding.name} : {binding.lean_type} := {literal(v.type, v.value)}\n'
        texts['Rules'] += '\n'.join(self.rule_code) + '\n'
        return texts


def instance(shape, truth=True):
    """The exact section-4 T/F instances; numeric facts are fixture data only."""
    f = Fixture()
    if shape == 'equal':
        slots = {'a': f.scalar('left', 3), 'b': f.scalar('right', 3 if truth else 4)}
    elif shape == 'sum':
        f.scalar('two', 2)
        f.scalar('three', 3)
        slots = {'xs': f.list('amounts', ('two', 'three')), 'v': f.scalar('reported_total', 5 if truth else 6, origin=c.Origin.REPORT)}
    elif shape == 'compare':
        slots = {'a': f.scalar('left', 5 if truth else 4), 'b': f.scalar('right', 5), 'cmp': '≥'}
    elif shape == 'difference':
        slots = {k: f.scalar(k, v) for k, v in (('a', 8), ('b', 3), ('v', 5 if truth else 6))}
    elif shape == 'date_add':
        slots = {'start': f.scalar('start_date', date(2026, 1, 1), DATE),
                 'days': f.scalar('elapsed_days', 2),
                 'due': f.scalar('due_date', date(2026, 1, 3 if truth else 2), DATE)}
    elif shape == 'within':
        slots = {'event': f.scalar('event_date', date(2026, 1, 3 if truth else 4), DATE),
                 'due': f.scalar('due_date', date(2026, 1, 3), DATE), 'inclusive': 'true'}
    elif shape in ('all', 'any', 'count'):
        numbers = ((2, 4) if truth else (2, 3)) if shape == 'all' else (
            ((2, 3) if truth else (1, 3)) if shape == 'any' else (2, 3, 4))
        ids = tuple(f.scalar(f'entry_{i}', n) for i, n in enumerate(numbers))
        slots = {'xs': f.list('entries', ids), 'p': f.rule('even', NAT, BOOL,
            'def Rules.even (x : Nat) : Bool := x % 2 == 0')}
        if shape == 'count':
            slots.update(n=f.scalar('counted', 2 if truth else 3), cmp='=')
    elif shape == 'maximum':
        f.scalar('label_A', 1)
        f.scalar('label_B', 2)
        f.scalar('value_A', 2)
        f.scalar('value_B', 3)
        f.record('row_A', (('label', 'label_A'), ('value', 'value_A')))
        f.record('row_B', (('label', 'label_B'), ('value', 'value_B')))
        ty = f.values[-1].type
        binding = c.lean_type_binding(ty)
        field = next(lean for wire, lean, _ in binding.fields if wire == 'value')
        slots = {'xs': f.list('rows', ('row_A', 'row_B'), ty), 'x': 'row_B' if truth else 'row_A',
                 'f': f.rule('amount', ty, NAT, f'def Rules.amount (x : {binding.lean_type}) : Nat := x.{field}'),
                 'v': 'value_B' if truth else 'value_A'}
    elif shape == 'conflict':
        f.scalar('scope', 1)
        f.scalar('issuer_A', 1)
        f.scalar('issuer_B', 2)
        f.scalar('observed_A', True, BOOL)
        f.scalar('observed_B', not truth, BOOL)
        for k in ('A', 'B'):
            f.record('observation_' + k, (('scope', 'scope'), ('issuer', 'issuer_' + k), ('value', 'observed_' + k)))
        slots = {'a': 'observation_A', 'b': 'observation_B', 'field': 'value'}
    else:
        slots = {'a': f.scalar('numerator', 6), 'b': f.scalar('denominator', 2),
                 'r': f.scalar('rate', Fraction(3 if truth else 4), RATIO), 'cmp': '='}
    return f, f.claim(shape, slots)


def render(f, claim, relation=c.ProofRelation.SUPPORTS, **kwargs):
    return t.render_claim(claim, f.resolved(), tuple(f.rules), relation, **kwargs)


class SourceBoundTemplateTests(unittest.TestCase):
    def assert_blocked(self, result, reason):
        self.assertIn(reason, result.not_formalized_reason)
        self.assertIsNone(result.proposition)
        self.assertIsNone(result.target)
        self.assertEqual(result.files, ())

    def test_every_shape_fixed_english(self):
        sentences = {
            'equal': 'left equals right.', 'sum': 'The sum of amounts equals reported total.',
            'compare': 'left is greater than or equal to right.', 'difference': 'a minus b equals v.',
            'date_add': 'due date is elapsed days calendar days after start date.',
            'within': 'event date is on or before due date.',
            'all': 'Every member of the nonempty list entries satisfies even.',
            'any': 'Some member of entries satisfies even.',
            'count': 'The number of members of entries satisfying even is equal to counted.',
            'maximum': 'row B is a maximum-valued member of rows, with amount equal to value B.',
            'conflict': 'observation A and observation B report different value values for the same scope.',
            'ratio': 'With positive denominator, numerator/denominator is equal to 3/1.',
        }
        for shape, sentence in sentences.items():
            f, claim = instance(shape)
            with self.subTest(shape=shape):
                result = render(f, claim)
                self.assertIsNone(result.not_formalized_reason)
                self.assertEqual(result.english, sentence)
                self.assertEqual(result.target, result.proposition)
                self.assertIn(':= by decide', result.files[0].content)
                self.assertEqual(result.files[0].path, 'models/SourceBound/Claims.lean')

    def test_all_shapes_polarity_and_relation_are_independent(self):
        for shape in c.SHAPES:
            f, claim = instance(shape)
            base = render(f, claim)
            for polarity in c.Polarity:
                for relation in c.ProofRelation:
                    with self.subTest(shape=shape, polarity=polarity, relation=relation):
                        q = base.proposition if polarity == c.Polarity.POSITIVE else f'¬ ({base.proposition})'
                        result = render(f, replace(claim, polarity=polarity), relation)
                        self.assertEqual(result.proposition, q)
                        self.assertEqual(result.target, q if relation == c.ProofRelation.SUPPORTS else f'¬ ({q})')
                        self.assertEqual(result.english, ('' if polarity == c.Polarity.POSITIVE else
                            'It is not the case that: ') + base.english)
                        self.assertEqual(result.claim.report_anchor, claim.report_anchor)

    def test_every_comparison_operator_and_polarity(self):
        for shape in ('compare', 'count', 'ratio'):
            f, claim = instance(shape)
            for op, phrase in OPERATORS.items():
                for polarity in c.Polarity:
                    for relation in c.ProofRelation:
                        variant = replace(claim, polarity=polarity, slots=tuple(
                            replace(s, value=op) if s.name == 'cmp' else s for s in claim.slots))
                        with self.subTest(shape=shape, op=op, polarity=polarity, relation=relation):
                            result = render(f, variant, relation)
                            self.assertIn(f' {op} ', result.proposition)
                            self.assertIn('is ' + phrase, result.english)

    def test_applicability_outside_all_negations(self):
        for shape in ('all', 'maximum', 'ratio'):
            for polarity in c.Polarity:
                for relation in c.ProofRelation:
                    f, claim = instance(shape)
                    gate_id = next(s.value.id for s in claim.slots if s.name == ('b' if shape == 'ratio' else 'xs'))
                    f.values = [replace(v, value=0 if shape == 'ratio' else ()) if v.id == gate_id else v for v in f.values]
                    with self.subTest(shape=shape, polarity=polarity, relation=relation):
                        self.assert_blocked(render(f, replace(claim, polarity=polarity), relation), 'Applicability')
        f, claim = instance('ratio')
        for bad in (-2, False):
            f.values = [replace(v, value=bad) if v.id == 'denominator' else v for v in f.values]
            self.assert_blocked(render(f, claim, c.ProofRelation.REFUTES), 'positive b')
        f, claim = instance('ratio')
        f.values = [replace(v, value=(3, 0)) if v.id == 'rate' else v for v in f.values]
        self.assert_blocked(render(f, claim, c.ProofRelation.REFUTES), 'positive r.denominator')

    def test_implicit_rule_source_report_and_mixed_lists(self):
        for shape in ('all', 'any', 'count', 'maximum'):
            for mode in ('source', 'report', 'mixed'):
                f = Fixture()
                f.scalar('source', 2)
                f.scalar('report', 2, origin=c.Origin.REPORT)
                f.list('entries', ('source',) if mode == 'source' else ('report',) if mode == 'report' else ('source', 'report'))
                f.rule('eligible', NAT, BOOL, 'def Rules.eligible (x : Nat) : Bool := x == x')
                f.rule('amount', NAT, NAT, 'def Rules.amount (x : Nat) : Nat := x')
                slots = {'xs': 'entries', 'p': 'eligible'}
                if shape == 'count':
                    slots.update(n='source', cmp='=')
                if shape == 'maximum':
                    slots = {'xs': 'entries', 'x': 'source', 'f': 'amount', 'v': 'report'}
                for polarity in c.Polarity:
                    for relation in c.ProofRelation:
                        claim = f.claim(shape, slots, polarity=polarity)
                        result = render(f, claim, relation)
                        with self.subTest(shape=shape, mode=mode, polarity=polarity, relation=relation):
                            if mode == 'source':
                                self.assertIsNone(result.not_formalized_reason)
                            else:
                                self.assert_blocked(result, 'rule operand xs has report-origin leaves: report')
                                self.assertEqual(result.not_formalized_reason, c.claim_rule_input_reason(claim, f.resolved(), tuple(f.rules)))

    def test_selected_maximum_operand_taint_and_rule_dependency_taint(self):
        f, claim = instance('maximum')
        f.facts = [replace(n, origin=c.Origin.REPORT) if n.id == 'value_B' else n for n in f.facts]
        # A separate source-only xs still cannot justify projecting report-origin x.
        f.list('source_rows', ('row_A',), f.values[-1].type.element)
        claim = replace(claim, slots=tuple(replace(s, value=c.Reference('source_rows')) if s.name == 'xs' else s for s in claim.slots))
        self.assert_blocked(render(f, claim), 'rule operand x')
        f, claim = instance('all')
        f.scalar('report_constant', 3, origin=c.Origin.REPORT)
        f.rules[0] = replace(f.rules[0], dependencies=(c.Reference('report_constant'),))
        self.assert_blocked(render(f, claim), 'Rule even has report-origin leaves')

    def test_unknown_unresolved_and_malformed_inputs(self):
        f, claim = instance('equal')
        self.assert_blocked(render(f, replace(claim, shape='unknown')), 'Unsupported')
        self.assert_blocked(render(f, replace(claim, version='2')), 'Unsupported')
        self.assert_blocked(render(f, replace(claim, not_formalized_reason='Unresolved source')), 'Unresolved source')
        missing = replace(claim, slots=(replace(claim.slots[0], value=c.Reference('missing')), claim.slots[1]))
        self.assert_blocked(render(f, missing), 'Unresolved value')
        f.values.pop()
        self.assert_blocked(render(f, claim), 'Unresolved value')
        for value in ('by decide', c.Reference('a + b'), c.Reference('x\ntheorem injected')):
            with self.subTest(value=value), self.assertRaises(c.ContractError):
                render(f, replace(claim, slots=(replace(claim.slots[0], value=value), claim.slots[1])))

    def test_no_source_dependency_even_with_decorative_source_scope(self):
        f, claim = instance('equal')
        f.facts = [replace(n, origin=c.Origin.REPORT) for n in f.facts]
        f.scalar('unused_source', 3)
        self.assert_blocked(render(f, replace(claim, scope=(c.Reference('unused_source'),))), 'No independent source')

    def test_type_and_currency_constraints(self):
        for shape in ('equal', 'compare', 'difference', 'sum', 'ratio'):
            f, claim = instance(shape)
            target = 'v' if shape in ('difference', 'sum') else 'b'
            ident = next(s.value.id for s in claim.slots if s.name == target)
            new_type = c.ScalarType('money', 'USD')
            f.facts = [replace(v, type=new_type) if v.id == ident else v for v in f.facts]
            f.values = [replace(v, type=new_type) if v.id == ident else v for v in f.values]
            with self.subTest(shape=shape):
                self.assert_blocked(render(f, claim), 'type' if shape in ('equal', 'compare') else 'units')
        f, claim = instance('all')
        f.rules[0] = replace(f.rules[0], result_type=NAT)
        self.assert_blocked(render(f, claim), 'Wrong result type')
        f.rules[0] = replace(f.rules[0], parameters=(('x', BOOL),), result_type=BOOL)
        self.assert_blocked(render(f, claim), 'one list element')

    def test_binding_map_money_date_ratio_enum_record(self):
        f = Fixture()
        f.scalar('first', -200, c.ScalarType('money', 'EUR'))
        f.scalar('second', -300, c.ScalarType('money', 'EUR'))
        claim = f.claim('compare', {'a': 'first', 'b': 'second', 'cmp': '>'})
        self.assertIn('(SourceBound.Facts.first).minor', render(f, claim).target)
        f = Fixture()
        f.scalar('first', Fraction(-1, 3), RATIO)
        f.scalar('second', Fraction(-1, 2), RATIO)
        claim = f.claim('compare', {'a': 'first', 'b': 'second', 'cmp': '>'})
        self.assertIn('(SourceBound.Facts.first).num * Int.ofNat ((SourceBound.Facts.second).den)', render(f, claim).target)
        f.domains = (c.EnumDomain('state', '1', ('Present', 'Not recorded')),)
        f.scalar('state', 'Present', c.ScalarType('enum', 'state'))
        self.assertIsNone(render(f, f.claim('equal', {'a': 'state', 'b': 'state'})).not_formalized_reason)

    def test_display_labels_never_enter_lean_or_change_report(self):
        f, claim = instance('sum')
        base = render(f, claim)
        labeled = render(f, claim, labels={'amounts': 'the register amounts', 'reported_total': 'the reported total'})
        self.assertEqual(labeled.english, 'The sum of the register amounts equals the reported total.')
        self.assertEqual(base.files, labeled.files)
        self.assertEqual(base.claim, labeled.claim)
        self.assertEqual(render(f, claim, labels={'amounts': 'evil\ntheorem x'}).files, base.files)

    def test_batch_determinism_and_no_free_theorem(self):
        f, a = instance('equal')
        b = replace(a, id='C02', polarity=c.Polarity.NEGATIVE)
        unsupported = replace(a, id='C03', shape='unknown')
        relations = {'C01': c.ProofRelation.SUPPORTS, 'C02': c.ProofRelation.REFUTES, 'C03': c.ProofRelation.SUPPORTS}
        facts = f.resolved()
        output = t.generate_claims((a, b, unsupported), facts, (), relations)
        reordered = replace(facts, document=replace(facts.document, facts=tuple(reversed(facts.document.facts))), values=tuple(reversed(facts.values)))
        self.assertEqual(output, t.generate_claims((unsupported, b, a), reordered, (), relations))
        self.assertEqual(output[0].content.count('theorem '), 2)
        self.assertNotIn('C03', output[0].content)
        self.assertEqual(t.generate_claims((unsupported,), facts, (), {'C03': c.ProofRelation.SUPPORTS}), ())
        self.assertEqual(t.generate_claims((), facts, (), {}), ())
        for claims, mapping in (((a, a), {'C01': c.ProofRelation.SUPPORTS}), ((a,), {})):
            with self.assertRaises(c.ContractError):
                t.generate_claims(claims, facts, (), mapping)


@unittest.skipUnless(LEAN, 'AUDIT_TEST_LEAN is unset; real Lean checks run on the Linux host')
class SourceBoundTemplateLeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Path(LEAN).is_file():
            raise RuntimeError(f'AUDIT_TEST_LEAN is configured but missing: {LEAN}')
        (ROOT / 'tmp').mkdir(exist_ok=True)
        cls.root = Path(tempfile.mkdtemp(prefix='sb-templates-', dir=ROOT / 'tmp'))
        print(f'\nRetained Lean artifacts: {cls.root}', flush=True)

    def run_lean(self, folder, filename, *, output=None):
        command = [LEAN]
        if output:
            command += ['-o', output]
        command.append(filename)
        completed = subprocess.run(command, cwd=folder,
            env={**os.environ, 'LEAN_PATH': str(folder)}, capture_output=True, text=True, timeout=30)
        (folder / (filename.replace('/', '_') + '.log')).write_text(
            'Command: ' + repr(command) + '\nExit code: ' + str(completed.returncode) + '\n' +
            completed.stdout + completed.stderr)
        return completed

    def prepare(self, fixture, prefix):
        folder = Path(tempfile.mkdtemp(prefix=prefix + '-', dir=self.root))
        for module, content in fixture.modules().items():
            if module == 'SourceBound.Claims':
                continue
            path = folder / (module.replace('.', '/') + '.lean')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            result = self.run_lean(folder, str(path.relative_to(folder)),
                                   output=str(path.with_suffix('.olean').relative_to(folder)))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return folder

    def check_target(self, folder, rendered, truth, ident):
        self.assertIsNone(rendered.not_formalized_reason)
        path = folder / (ident + '.lean')
        path.write_text(rendered.files[0].content)
        result = self.run_lean(folder, path.name)
        if truth:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('decide', result.stdout + result.stderr)
            self.assertIn('false', result.stdout + result.stderr)

    def test_section_four_truth_table_all_twelve_shapes_both_polarities(self):
        checks = 0
        for shape in c.SHAPES:
            for truth in (True, False):
                fixture, claim = instance(shape, truth)
                folder = self.prepare(fixture, f'{shape}-{truth}')
                for polarity in c.Polarity:
                    for relation in c.ProofRelation:
                        with self.subTest(shape=shape, truth=truth, polarity=polarity, relation=relation):
                            expected = truth == (polarity == c.Polarity.POSITIVE)
                            expected = expected == (relation == c.ProofRelation.SUPPORTS)
                            self.check_target(folder, render(fixture, replace(claim, polarity=polarity), relation),
                                expected, f'{polarity}-{relation}')
                            checks += 1
        self.assertEqual(checks, 96)
        print(f'Section 4: {checks} Lean target checks (48 accepted, 48 rejected).', flush=True)

    def test_lean_every_operator_signed_arithmetic_and_exact_ratios(self):
        checks = 0
        for shape in ('compare', 'count', 'ratio'):
            fixture, claim = instance(shape, False)
            folder = self.prepare(fixture, shape + '-operators')
            for index, op in enumerate(OPERATORS):
                claim_op = replace(claim, slots=tuple(replace(s, value=op) if s.name == 'cmp' else s for s in claim.slots))
                # Each false SPEC instance has left < right, so this detects
                # reversal of either strict or non-strict comparison as well.
                truth = op in ('≠', '<', '≤')
                for polarity in c.Polarity:
                    for relation in c.ProofRelation:
                        expected = (truth == (polarity == c.Polarity.POSITIVE)) == (relation == c.ProofRelation.SUPPORTS)
                        self.check_target(folder, render(fixture, replace(claim_op, polarity=polarity), relation),
                                          expected, f'op-{index}-{polarity}-{relation}')
                        checks += 1
        self.assertEqual(checks, 72)
        print(f'Operators: {checks} Lean target checks (36 accepted, 36 rejected).', flush=True)
        f = Fixture()
        f.scalar('a', 3)
        f.scalar('b', 8)
        f.derived_value('v', c.ScalarType('int'), -5, c.Operation.SUBTRACT, ('a', 'b'))
        claim = f.claim('difference', {'a': 'a', 'b': 'b', 'v': 'v'})
        self.check_target(self.prepare(f, 'signed-subtraction'), render(f, claim), True, 'negative')
        f = Fixture()
        f.scalar('a', Fraction(-1, 3), RATIO)
        f.scalar('b', Fraction(-1, 2), RATIO)
        claim = f.claim('compare', {'a': 'a', 'b': 'b', 'cmp': '>'})
        self.check_target(self.prepare(f, 'ratio-cross-products'), render(f, claim), True, 'exact')

    def test_lean_empty_any_duplicates_ties_and_exclusive_dates(self):
        f, claim = instance('any')
        f.values = [replace(v, value=()) if v.id == 'entries' else v for v in f.values]
        self.check_target(self.prepare(f, 'empty-any'), render(f, claim, c.ProofRelation.REFUTES), True, 'empty')
        f, claim = instance('count')
        f.values = [replace(v, value=(2, 2, 4)) if v.id == 'entries' else
                    replace(v, value=3) if v.id == 'counted' else v for v in f.values]
        self.check_target(self.prepare(f, 'duplicates'), render(f, claim), True, 'duplicates')
        f, claim = instance('maximum')
        f.values = [replace(v, value=3) if v.id == 'value_A' else
                    replace(v, value=(('label', 1), ('value', 3))) if v.id == 'row_A' else
                    replace(v, value=((('label', 1), ('value', 3)), (('label', 2), ('value', 3)))) if v.id == 'rows' else v for v in f.values]
        self.check_target(self.prepare(f, 'maximum-ties'), render(f, claim), True, 'ties')
        f, claim = instance('within')
        claim = replace(claim, slots=tuple(replace(s, value='false') if s.name == 'inclusive' else s for s in claim.slots))
        self.check_target(self.prepare(f, 'exclusive-date'), render(f, claim, c.ProofRelation.REFUTES), True, 'exclusive')

    def test_lean_gate_failure_emits_no_theorem(self):
        for shape in ('all', 'maximum', 'ratio'):
            f, claim = instance(shape)
            key = 'b' if shape == 'ratio' else 'xs'
            ident = next(s.value.id for s in claim.slots if s.name == key)
            f.values = [replace(v, value=0 if shape == 'ratio' else ()) if v.id == ident else v for v in f.values]
            folder = self.prepare(f, 'gate-' + shape)
            for polarity in c.Polarity:
                for relation in c.ProofRelation:
                    changed = replace(claim, polarity=polarity)
                    self.assertEqual(render(f, changed, relation).files, ())
                    self.assertEqual(t.generate_claims((changed,), f.resolved(), tuple(f.rules), {claim.id: relation}), ())
            self.assertFalse((folder / 'SourceBound/Claims.lean').exists())


if __name__ == '__main__':
    unittest.main()
