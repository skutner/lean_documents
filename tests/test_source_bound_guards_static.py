"""Static syntax/transcription checks; these never stand in for Lean proofs."""
from dataclasses import replace
from itertools import product
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/evidence-to-proof-review/scripts'))
import source_bound_contract as c
import source_bound_guards as g

NAT, INT, BOOL = c.ScalarType('nat'), c.ScalarType('int'), c.ScalarType('bool')
TR = frozenset({c.GuardFlag.TRANSCRIPTION})
ROOT_TRUE = TR | {c.GuardFlag.ROOT_TRUE}
UNKNOWN = frozenset({c.GuardFlag.UNCLASSIFIED})
POLICY = (c.EvidenceReference('input/sources/1/policy.md', 'P1', 'At least the threshold.', 0),)


def fixture():
    facts = tuple(c.FactRecord(
        ident, ident, typ, origin, '5', 'input/report.md' if origin == c.Origin.REPORT
        else 'input/sources/1/data.md', 'P1', '5', 0, c.ByteSpan(0, 1), 'en', None)
        for ident, typ, origin in [('n01', NAT, c.Origin.SOURCE), ('n02', NAT, c.Origin.SOURCE),
                                  ('reported', NAT, c.Origin.REPORT),
                                  ('state', c.ScalarType('enum', 'status'), c.Origin.SOURCE)])
    derived = (c.DerivedRecord('mixed', c.ListType(NAT), c.Operation.LIST,
                              (c.Reference('n01'), c.Reference('reported'))),)
    values = tuple(c.ResolvedValue(f.id, f.type, 'Yes' if f.id == 'state' else 5,
                                   (f.id,) if f.origin == c.Origin.SOURCE else (),
                                   (f.id,) if f.origin == c.Origin.REPORT else (), ()) for f in facts)
    return c.ResolvedFacts(c.FactsDocument(facts, derived, (
        c.EnumDomain('status', '1', ('No', 'Yes')),)), values + (
        c.ResolvedValue('mixed', c.ListType(NAT), (5, 5), ('n01',), ('reported',), ()),))


def rule(ident='r', parameters=(('x', NAT),), result=BOOL, dependencies=()):
    return c.RuleRegistration(ident, parameters, result, POLICY, 'Compare the threshold.',
                              tuple(c.Reference(d) for d in dependencies))


class RulesSyntaxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        cls.root = Path(tempfile.mkdtemp(prefix='source-bound-static-', dir=ROOT / 'tmp'))
        cls.facts = fixture()

    def inspect(self, body, registration=None, *, full=False, facts=None):
        path = self.root / f'rules-{len(list(self.root.iterdir())):04d}.lean'
        path.write_text(body if full else f'import SourceBound.Facts\nnamespace Rules\n{body}\nend Rules\n')
        return g.inspect_rules(path, facts or self.facts, (registration or rule(),))

    def test_registered_signature_and_source_reference(self):
        aliases = self.inspect('def r (x : Nat) : Bool := decide (x ≥ SourceBound.Facts.n01)',
                               rule(dependencies=('n01',)))
        self.assertIsInstance(aliases['Rules.r'], g.Alias)
        self.assertEqual(aliases['Rules.r'].parameters, ('x',))
        self.assertEqual(g.detect_transcription(
            'Rules.r SourceBound.Facts.n02 = decide (SourceBound.Facts.n02 ≥ SourceBound.Facts.n01)', aliases), TR)

    def test_identity_rule_and_digit_identifiers(self):
        aliases = self.inspect('def r (x : Nat) : Nat := let x2 := x; x2', rule(result=NAT))
        self.assertEqual(g.detect_transcription('Rules.r SourceBound.Facts.n01 = SourceBound.Facts.n01', aliases), TR)

    def test_numeric_categories_cross_comment_wrappers(self):
        # Signs, radix, decimals, grouped/rate/date/unit spellings all reject.
        numerals = ('0', '1', '-1', '+1', '0x10', '0b10', '1.5', '1e3', '1,234',
                    '12.5%', '2026-11-12', 'EUR 20', '20 million', '1_000')
        wrappers = ('{}', '(/- 123 /- nested -/ -/ {})', '{} -- harmless numeral 999\n')
        for number, wrapper in product(numerals, wrappers):
            with self.subTest(number=number, wrapper=wrapper), self.assertRaises((c.ContractError, audit_error())):
                self.inspect('def r (x : Nat) : Nat := ' + wrapper.format(number), rule(result=NAT))

    def test_numeric_constructors_conversions_and_closed_terms(self):
        terms = ('Nat.zero', 'Nat.succ x', 'OfNat.ofNat', 'Int.ofNat Nat.zero',
                 'Int.negSucc x', 'String.toNat! "3"', 'List.length ([] : List Nat)',
                 'List.sum ([] : List Nat)', 'Bool.toNat true', 'Nat.ofScientific',
                 'default', '(inferInstance : Nat)', 'by exact x', 'nomatch x')
        for term in terms:
            with self.subTest(term=term), self.assertRaises((c.ContractError, audit_error())):
                self.inspect(f'def r (x : Nat) : Nat := {term}', rule(result=NAT))

    def test_quotes_comments_and_command_overlap(self):
        for comment in ('-- axiom bad : False\n', '/- #eval 99 /- theorem fake -/ -/\n'):
            self.inspect(comment + 'def r (x : Nat) : Bool := true')
        for value in ('"true"', 'r"true"', 'r#"true"#', "'a'", '"--"', '/- unclosed'):
            with self.subTest(value=value), self.assertRaises((c.ContractError, audit_error())):
                self.inspect(f'def r (x : Nat) : Bool := {value}')
        for command in ('theorem t : True := by trivial', 'lemma t : True := by trivial',
                        'example : True := by trivial', 'instance : Inhabited Nat := ⟨0⟩',
                        'notation "n" => Nat', 'macro "n" : term => `(0)',
                        '#eval 1', 'open SourceBound.Facts', 'attribute [simp] r',
                        'universe u', 'variable (x : Nat)', 'namespace SourceBound.Facts'):
            with self.subTest(command=command), self.assertRaises((c.ContractError, audit_error())):
                self.inspect(command + '\ndef r (x : Nat) : Bool := true')

    def test_signature_dependency_and_import_mismatch(self):
        for declaration in ('def r (x : Int) : Bool := true',
                            'def r (y : Nat) : Bool := true',
                            'def r (x : Nat) : Nat := x',
                            'def other (x : Nat) : Bool := true',
                            'def r (x : Nat) : Bool := x',
                            'def r (x : Nat) : Bool := decide (x = SourceBound.Facts.n01)'):
            with self.subTest(declaration=declaration), self.assertRaises(c.ContractError):
                self.inspect(declaration)
        for header in ('import Std', 'import SourceBound.Facts\nimport Std',
                       'import SourceBound.Derived', 'import all SourceBound.Facts', ''):
            with self.subTest(header=header), self.assertRaises((c.ContractError, audit_error())):
                self.inspect(header + '\ndef Rules.r (x : Nat) : Bool := true', full=True)

    def test_report_derived_unknown_and_stale_taint(self):
        stale = replace(self.facts, values=tuple(replace(v, report_leaves=()) for v in self.facts.values))
        for reference, dependency in (('SourceBound.Report.reported', 'reported'),
                                      ('SourceBound.Derived.mixed', 'mixed'),
                                      ('SourceBound.Facts.reported', 'reported'),
                                      ('SourceBound.Facts.missing', 'n01')):
            for facts in (self.facts, stale):
                with self.subTest(reference=reference), self.assertRaises(c.ContractError):
                    self.inspect(f'def r (x : Nat) : Bool := decide (x = {reference})',
                                 rule(dependencies=(dependency,)), facts=facts)

    def test_lists_lambdas_projections_and_generated_only_conversion(self):
        self.inspect('def r (x : List (Nat)) : Nat := List.length x',
                     rule(parameters=(('x', c.ListType(NAT)),), result=NAT))
        self.inspect('def r (x : List (Nat)) : Bool := List.all x (fun y => y == SourceBound.Facts.n01)',
                     rule(parameters=(('x', c.ListType(NAT)),), dependencies=('n01',)))
        with self.assertRaises(c.ContractError):
            self.inspect('def r (x : Nat) : Int := Int.ofNat x', rule(result=INT))
        self.assertEqual(g.detect_transcription(
            'Int.ofNat SourceBound.Facts.n01 = Int.ofNat SourceBound.Report.reported'), frozenset())
        self.inspect('def r (x : Nat) : Nat := (x, SourceBound.Facts.n01).1',
                     rule(result=NAT, dependencies=('n01',)))
        money = c.ScalarType('money', 'EUR')
        self.inspect('def r (x : SourceBound.Money SourceBound.Currency.eur) : Int := x.minor',
                     rule(parameters=(('x', money),), result=INT))

    def test_closed_numeric_terms_cannot_be_laundered_by_lambda_or_rule(self):
        with self.assertRaises(c.ContractError):
            self.inspect('def r (x : Nat) : Nat := (fun (xs : List (Nat)) => List.length xs) []',
                         rule(result=NAT))
        rules = (rule('length', (('xs', c.ListType(NAT)),), NAT),
                 rule('r', (), NAT, ('length',)))
        path = self.root / 'closed-helper.lean'
        path.write_text('import SourceBound.Facts\nnamespace Rules\n'
                        'def length (xs : List (Nat)) : Nat := List.length xs\n'
                        'def r : Nat := Rules.length []\nend Rules\n')
        with self.assertRaises(c.ContractError):
            g.inspect_rules(path, self.facts, rules)
        indirect = (rules[0], rule('wrapper', (('xs', c.ListType(NAT)),), NAT, ('length',)),
                    rule('r', (), NAT, ('wrapper',)))
        path.write_text('import SourceBound.Facts\nnamespace Rules\n'
                        'def length (xs : List (Nat)) : Nat := List.length xs\n'
                        'def wrapper (xs : List (Nat)) : Nat := Rules.length xs\n'
                        'def r : Nat := Rules.wrapper []\nend Rules\n')
        with self.assertRaises(c.ContractError):
            g.inspect_rules(path, self.facts, indirect)

    def test_let_bound_function_aliases_preserve_body_and_capture(self):
        closed = (
            'let f := fun (xs : List (Nat)) => List.length xs; f []',
            'let f := fun (xs : List (Nat)) => List.length xs; let g := f; g []',
            'let f := fun (xs : List (Nat)) => fun (b : Bool) => List.length xs; f [] true',
            'let f := fun (xs : List (Nat)) => List.length xs; '
            'let g := fun (b : Bool) => f []; g true',
        )
        for body in closed:
            with self.subTest(body=body), self.assertRaises(c.ContractError):
                self.inspect('def r (x : Nat) : Nat := ' + body, rule(result=NAT))
        valid = (
            'let f := fun (ys : List (Nat)) => List.length ys; f xs',
            'let f := fun (ys : List (Nat)) => List.length ys; let g := f; g xs',
            'let f := fun (b : Bool) => List.length xs; f true',
            'let f := fun (ys : List (Nat)) => fun (b : Bool) => List.length ys; f xs true',
        )
        for body in valid:
            with self.subTest(body=body):
                self.inspect('def r (xs : List (Nat)) : Nat := ' + body,
                             rule(parameters=(('xs', c.ListType(NAT)),), result=NAT))

    def test_catalogue_argument_order(self):
        predicate = '(fun (n : Nat) => n == SourceBound.Facts.n01)'
        for name in ('all', 'any'):
            registration = rule(parameters=(('xs', c.ListType(NAT)),), dependencies=('n01',))
            self.inspect(f'def r (xs : List (Nat)) : Bool := List.{name} xs {predicate}', registration)
            with self.assertRaises(c.ContractError):
                self.inspect(f'def r (xs : List (Nat)) : Bool := List.{name} {predicate} xs', registration)
        self.inspect(f'def r (xs : List (Nat)) : List (Nat) := List.filter {predicate} xs',
                     rule(parameters=(('xs', c.ListType(NAT)),), result=c.ListType(NAT), dependencies=('n01',)))
        self.inspect('def r (xs : List (Nat)) : List (Nat) := List.map (fun (n : Nat) => n + n) xs',
                     rule(parameters=(('xs', c.ListType(NAT)),), result=c.ListType(NAT)))

    def test_empty_rules_and_signature_only_record_types(self):
        path = self.root / 'empty.lean'
        path.write_text('import SourceBound.Facts\n')
        self.assertEqual(g.inspect_rules(path, self.facts, ()), {})
        typ = c.RecordType((('count', NAT),))
        lean = c.lean_type_binding(typ).lean_type
        self.inspect(f'def r (x : Nat) : {lean} := {{ f_count := x }}', rule(result=typ))
        with self.assertRaises(c.ContractError):
            self.inspect(f'def r (x : Nat) : {lean} := {{ count := x }}', rule(result=typ))

    def test_exhaustive_enum_and_list_matches(self):
        enum = c.ScalarType('enum', 'status')
        body = ('def r (x : SourceBound.Enums.status) : Bool := match x with\n'
                '| SourceBound.Enums.status.label_no => false\n'
                '| SourceBound.Enums.status.label_yes => true')
        self.inspect(body, rule(parameters=(('x', enum),)))
        with self.assertRaises(c.ContractError):
            self.inspect(body.split('\n| SourceBound.Enums.status.label_yes')[0],
                         rule(parameters=(('x', enum),)))
        self.inspect('def r (x : List (Nat)) : Bool := match x with | [] => false | h :: t => true',
                     rule(parameters=(('x', c.ListType(NAT)),)))
        with self.assertRaises(c.ContractError):
            self.inspect('def r (x : SourceBound.Enums.status) : SourceBound.Enums.status := SourceBound.Enums.status.label_yes',
                         rule(parameters=(('x', enum),), result=enum))

    def test_recursive_rule_and_missing_policy_rejected(self):
        with self.assertRaises(c.ContractError):
            self.inspect('def r (x : Nat) : Bool := Rules.r x', rule(dependencies=('r',)))
        with self.assertRaises(c.ContractError):
            self.inspect('def r (x : Nat) : Bool := true', replace(rule(), policy=()))


def audit_error():
    return g.audit.AuditError


class TranscriptionTests(unittest.TestCase):
    def test_root_true_and_identifier_bool_distinctions(self):
        for text in ('True', '(True)', '/- True? -/ True -- done', 'let a := True; a'):
            self.assertEqual(g.detect_transcription(text), ROOT_TRUE, text)
        for text in ('true', 'Bool.true', 'Trueish', 'My.True', 'False', 'False → False ∧ True'):
            self.assertEqual(g.detect_transcription(text), frozenset(), text)
        self.assertEqual(g.detect_transcription('root', {'root': 'True'}), ROOT_TRUE)

    def test_identity_arithmetic_hypotheses_empty_quantifiers_contradiction(self):
        for text in ('a = a', 'p ↔ p', '(a = a) ∧ (b = b)', 'p → p', 'p → True',
                     '∀ x : Nat, x = x', '∀ x ∈ [], x = x', 'False → False',
                     '(a + b) = (a + b)', '¬p → ¬p'):
            self.assertEqual(g.detect_transcription(text), TR, text)
        for text in ('1 + 1 = 2', 'a + b = reportedTotal', 'False → a = b',
                     '∀ x ∈ [], x = y', '∃ x : Nat, x = x', 'p ∧ ¬p',
                     '(a = a) ∧ (b = c)', '(if true then a else b) = a',
                     '(match x with | [] => a | h :: t => b) = a'):
            self.assertEqual(g.detect_transcription(text), frozenset(), text)

    def test_alias_let_beta_alpha_record_pairwise_overlap(self):
        for left, right in (
            ('let y := x; y', 'x'),
            ('(fun z => z) x', 'x'),
            ('fun x => x', 'fun y => y'),
            ('({ item := x }).item', 'x'),
            ('(x, y).1', 'x'),
            ('(fun r => r.item) { item := x }', 'x'),
            ('let a := x; (fun z => z) a', 'x'),
            ('fun x => let z := x; z', 'fun y => y'),
            ('(fun x => fun y => x) (fun z => z)', 'fun y => fun z => z'),
            ('(fun x => fun y => x y) (fun z => z)', 'fun y => y'),
            ('SourceBound.Facts.amount.minor', '(SourceBound.Facts.amount).minor'),
        ):
            with self.subTest(left=left, right=right):
                self.assertEqual(g.detect_transcription(f'({left}) = ({right})'), TR)
        aliases = {'alias': 'original', 'twice': g.Alias(('x',), g.parse_expression('x + x'))}
        self.assertEqual(g.detect_transcription('alias = original', aliases), TR)
        self.assertEqual(g.detect_transcription('twice a = a + a', aliases), TR)
        self.assertEqual(g.detect_transcription('twice a = a * 2', aliases), frozenset())

    def test_generated_fold_and_match_syntax_remain_unevaluated(self):
        target = '(List.foldl (fun a b => a + b) 0 SourceBound.Facts.rows) = SourceBound.Report.total'
        self.assertEqual(g.detect_transcription(target), frozenset())
        self.assertEqual(g.detect_transcription('(List.foldl (fun a b => a + b) 0 []) = 0'), frozenset())
        self.assertEqual(g.detect_transcription(
            '(match xs with | [] => a | h :: t => h) = (match xs with | [] => a | x :: tail => x)'), TR)

    def test_fact_leaves_opaque_and_alias_capture_avoided(self):
        aliases = {'SourceBound.Facts.a': '5', 'SourceBound.Report.b': '5', 'a': 'x'}
        self.assertEqual(g.detect_transcription('SourceBound.Facts.a = SourceBound.Report.b', aliases), frozenset())
        self.assertEqual(g.detect_transcription('(fun x => a) = (fun y => x)', aliases), TR)
        self.assertEqual(g.detect_transcription('(fun x => a) = (fun x => x)', aliases), frozenset())

    def test_unsupported_returns_unclassified_never_empty(self):
        for text in ('', 'by trivial', 'a =', '"True"', "'T'", 'r#"True"#',
                     'a := b', '/- unterminated', '⟨a⟩ = a', 'a #eval 1', 'x.{1}'):
            with self.subTest(text=text):
                self.assertEqual(g.detect_transcription(text), UNKNOWN)
        self.assertEqual(g.detect_transcription('a', {'a': 'b', 'b': 'a'}), UNKNOWN)

    def test_true_transcription_flags_overlap_preserved(self):
        for prefix, suffix in product(('True', 't', 'let u := t; u'), ('', ' -- ignored 10')):
            self.assertEqual(g.detect_transcription(prefix + suffix, {'t': 'True'}), ROOT_TRUE)

    def test_lazy_guard_delegation_exact_signature(self):
        fake = types.ModuleType('source_bound_proofs')
        calls = []
        fake.check_proof = lambda *args, **kwargs: calls.append((args, kwargs)) or 'sentinel'
        with patch.dict(sys.modules, {'source_bound_proofs': fake}):
            result = g.check_guards(Path('.'), None, None, (), (), budget=c.GuardBudget(),
                                    attempt_directory=Path('tmp'), sandbox_command=('sandbox',))
        self.assertEqual(result, 'sentinel')
        self.assertEqual(calls[0][1]['sandbox_command'], ('sandbox',))


class GeneratedAliasTests(unittest.TestCase):
    def generated_fixture(self):
        facts = fixture()
        row_type = c.RecordType((('count', NAT),))
        nodes = (
            c.DerivedRecord('row', row_type, c.Operation.RECORD, (),
                            fields=(('count', c.Reference('n01')),)),
            c.DerivedRecord('selected', NAT, c.Operation.FIELD, (c.Reference('row'),), field='count'),
            c.DerivedRecord('rows', c.ListType(NAT), c.Operation.LIST,
                            (c.Reference('n01'), c.Reference('n02'))),
            c.DerivedRecord('total', NAT, c.Operation.SUM, (c.Reference('rows'),)),
            c.DerivedRecord('called', NAT, c.Operation.RULE, (c.Reference('selected'),), rule=c.Reference('identity')),
        )
        data = {'row': (('count', 5),), 'selected': 5, 'rows': (5, 5), 'total': 10, 'called': 5}
        resolved = replace(facts, document=replace(facts.document, derived=nodes), values=tuple(
            v for v in facts.values if v.id != 'mixed') + tuple(
                c.ResolvedValue(n.id, n.type, data[n.id], ('n01', 'n02'), (), ()) for n in nodes))
        return resolved

    def files(self, facts, overrides=None):
        bodies = {'row': '⟨SourceBound.Facts.n01⟩',
                  'selected': '(SourceBound.Derived.row).f_count',
                  'rows': '[SourceBound.Facts.n01, SourceBound.Facts.n02]',
                  'total': '(SourceBound.Derived.rows).foldl (fun a b => a + b) 0',
                  'called': 'Rules.identity (SourceBound.Derived.selected)'}
        bodies.update(overrides or {})
        text = 'import Rules\n' + ''.join(
            f'def {facts.bindings.values[n.id].name} : {facts.bindings.values[n.id].lean_type} := {bodies[n.id]}\n'
            for n in facts.document.derived)
        return (c.GeneratedFile('models/SourceBound/Derived.lean', text),)

    def test_record_field_chain_is_transcription_but_sum_is_not(self):
        facts = self.generated_fixture()
        aliases = g.generated_aliases(facts, self.files(facts))
        self.assertEqual(g.detect_transcription(
            'SourceBound.Derived.selected = SourceBound.Facts.n01', aliases), TR)
        self.assertEqual(g.detect_transcription(
            'SourceBound.Derived.row.f_count = SourceBound.Facts.n01', aliases), TR)
        self.assertEqual(g.detect_transcription(
            'SourceBound.Derived.total = SourceBound.Report.reported', aliases), frozenset())
        self.assertEqual(g.detect_transcription(
            'SourceBound.Derived.rows = [SourceBound.Facts.n01, SourceBound.Facts.n02]', aliases), TR)
        # Equal resolved numeric values are never unfolded or compared.
        self.assertEqual(g.detect_transcription(
            'SourceBound.Derived.selected = SourceBound.Facts.n02', aliases), frozenset())

    def test_generated_rule_aliases_merge_with_registered_rule_aliases(self):
        facts = self.generated_fixture()
        aliases = g.generated_aliases(facts, self.files(facts))
        aliases['Rules.identity'] = g.Alias(('x',), g.Expr('name', 'x'))
        self.assertEqual(g.detect_transcription(
            'SourceBound.Derived.called = SourceBound.Facts.n01', aliases), TR)
        aliases['SourceBound.Facts.n01'] = 'True'
        aliases['SourceBound.Report.reported'] = 'True'
        self.assertEqual(g.detect_transcription('SourceBound.Facts.n01', aliases), frozenset())
        self.assertEqual(g.detect_transcription('SourceBound.Report.reported', aliases), frozenset())

    def test_missing_unsupported_or_recursive_derived_alias_is_unclassified(self):
        facts = self.generated_fixture()
        target = 'SourceBound.Derived.total = SourceBound.Report.reported'
        for files in ((), self.files(facts, {'total': '⟨unsupported⟩'})):
            aliases = g.generated_aliases(facts, files)
            self.assertEqual(g.detect_transcription(target, aliases), UNKNOWN)
        self.assertEqual(g.detect_transcription(target), UNKNOWN)
        aliases = g.generated_aliases(facts, self.files(facts, {'total': 'SourceBound.Derived.total'}))
        self.assertEqual(g.detect_transcription(target, aliases), UNKNOWN)
        # Unsupported unrelated definitions do not poison a supported target.
        aliases = g.generated_aliases(facts, self.files(facts, {'total': '⟨unsupported⟩'}))
        self.assertEqual(g.detect_transcription(
            'SourceBound.Derived.selected = SourceBound.Facts.n01', aliases), TR)
        with self.assertRaises(c.ContractError):
            g.generated_aliases(facts, self.files(facts) * 2)

    def test_synthetic_list_body_is_not_replaced_by_original_inputs(self):
        facts = self.generated_fixture()
        facts = replace(facts, values=tuple(replace(v, value=(5,)) if v.id == 'rows' else v for v in facts.values))
        aliases = g.generated_aliases(facts, self.files(facts, {
            'rows': '([SourceBound.Facts.n01, SourceBound.Facts.n02]).drop 1'}))
        self.assertEqual(g.detect_transcription(
            'SourceBound.Derived.rows = [SourceBound.Facts.n01, SourceBound.Facts.n02]', aliases), frozenset())

    def test_top_wrapper_fields_come_from_binding_projection(self):
        facts = fixture()
        money = c.ScalarType('money', 'EUR')
        nodes = (c.DerivedRecord('total', money, c.Operation.ADD,
                                (c.Reference('n01'), c.Reference('n01'))),)
        facts = replace(facts, document=replace(facts.document, derived=nodes, facts=tuple(
            replace(f, type=money) if f.id == 'n01' else f for f in facts.document.facts)),
            values=tuple(replace(v, type=money) if v.id == 'n01' else v for v in facts.values if v.id != 'mixed') + (
                c.ResolvedValue('total', money, 10, ('n01',), (), ()),))
        expression = '(SourceBound.Facts.n01).minor + (SourceBound.Facts.n01).minor'
        aliases = g.generated_aliases(facts, self.files(facts, {'total': f'⟨{expression}⟩'}))
        self.assertEqual(g.detect_transcription(f'(SourceBound.Derived.total).minor = {expression}', aliases), TR)
        self.assertEqual(g.detect_transcription('(SourceBound.Derived.total).minor = 10', aliases), frozenset())


if __name__ == '__main__':
    unittest.main()
