"""Phase-0 wire fixtures and structural boundary checks; no Lean execution."""
from dataclasses import replace
from datetime import date
from fractions import Fraction
from itertools import combinations
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/evidence-to-proof-review/scripts'))
import source_bound_contract as contract


def fact(ident='amount', *, source='input/sources/1/register.md', origin='source',
         quote='Q01 amount is EUR 1,234.50', token='EUR 1,234.50', type_='money(EUR)',
         value='123450', unit='inline'):
    # Calculate byte spans programmatically, including multi-byte test tokens.
    start = len(quote[:quote.index(token)].encode('utf-8'))
    end = start + len(token.encode('utf-8'))
    return (f'## Fact {ident}\nName: {ident}\nType: {type_}\nOrigin: {origin}\n'
            f'Value: {value}\nSource: {source}\nAnchor: P1\nQuote-start: 0\n'
            f'Select: {start}:{end}\nLocale: en\nUnit-source: {unit}\n'
            f'Quote:\n```\n{quote}\n```\n')


def claim(ident='C01', shape='equal', slots='Slot a: amount\nSlot b: reported\n',
          rules='', scope='amount'):
    return (f'## Claim {ident}\nReport-anchor: R1\nShape: {shape}\nVersion: 1\n'
            f'Polarity: positive\nRules: {rules}\nScope: {scope}\n'
            f'Rationale: Compare the source amount with the report claim.\n{slots}')


class SourceBoundContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        cls.root = Path(tempfile.mkdtemp(prefix='source-bound-contract-', dir=ROOT / 'tmp'))
        (cls.root / 'input/sources/1').mkdir(parents=True)
        (cls.root / 'input/sources/1/register.md').write_text('Q01 amount is EUR 1,234.50\n')
        (cls.root / 'input/report.md').write_text('The total is EUR 1,234.50.\n')
        cls.bindings = (
            contract.SourceBinding('input/sources/1/register.md', contract.Origin.SOURCE, 'en', frozenset({'P1'})),
            contract.SourceBinding('input/report.md', contract.Origin.REPORT, 'en', frozenset({'P1', 'R1'})),
        )

    def parse(self, text, **kwargs):
        return contract.parse_facts(text, workspace=self.root,
                                    bindings=kwargs.pop('bindings', self.bindings), **kwargs)

    def parse_claims(self, text, **kwargs):
        facts = self.parse(fact() + fact('reported', source='input/report.md', origin='report'))
        return contract.parse_claims(text, facts=kwargs.pop('facts', facts),
                                     report_anchors=frozenset({'R1'}), **kwargs)

    def test_valid_facts_file(self):
        text = '# Source-bound facts\n\n' + fact() + fact('reported', source='input/report.md', origin='report')
        text += ('\n## Derived amounts\nType: list(money(EUR))\nOperation: list\nInputs: amount, amount\n'
                 '\n## Derived total\nType: money(EUR)\nOperation: sum\nInputs: amounts\n'
                 '\n## Summary\n| ID | Type |\n| --- | --- |\n| total | money(EUR) |\n')
        document = self.parse(text)
        self.assertEqual(document.facts[0].type, contract.ScalarType('money', 'EUR'))
        self.assertEqual(document.facts[0].asserted_value, '123450')
        self.assertEqual(document.facts[1].origin, contract.Origin.REPORT)
        self.assertEqual(document.derived[0].inputs, (contract.Reference('amount'),) * 2)
        self.assertEqual(document.derived[1].operation, contract.Operation.SUM)
        self.assertEqual(set(document.records()), {'amount', 'reported', 'amounts', 'total'})

    def test_valid_claims_file(self):
        result = self.parse_claims('# Claims\n\n' + claim())[0]
        self.assertEqual(result.id, 'C01')
        self.assertEqual(result.report_anchor, 'R1')
        self.assertEqual(result.slots[0], contract.ClaimSlot('a', contract.SlotKind.VALUE, contract.Reference('amount')))
        self.assertIsNone(result.not_formalized_reason)
        self.assertEqual(result.polarity, contract.Polarity.POSITIVE)

    def test_unknown_fact_field_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'Unknown fields'):
            self.parse(fact() + 'Trust: yes\n')

    def test_unknown_claim_field_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'Unknown fields'):
            self.parse_claims(claim() + 'Target: True\n')

    def test_duplicate_fact_field_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'Duplicate field'):
            self.parse(fact() + 'Value: 123450\n')

    def test_duplicate_claim_field_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'Duplicate field'):
            self.parse_claims(claim() + 'Shape: equal\n')

    def test_duplicate_slot_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'Duplicate field'):
            self.parse_claims(claim() + 'Slot a: reported\n')

    def test_duplicate_fact_ids_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'Duplicate ID'):
            self.parse(fact() + fact())

    def test_duplicate_claim_ids_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'Duplicate ID'):
            self.parse_claims(claim() + claim())

    def test_duplicate_fact_derived_ids_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'Duplicate ID'):
            self.parse(fact() + '## Derived amount\nType: nat\nOperation: list\nInputs: \n')

    def test_non_ascii_fact_id_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'ASCII identifier'):
            self.parse(fact('amоunt'))  # Cyrillic o.

    def test_non_ascii_claim_id_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'ASCII identifier'):
            self.parse_claims(claim('Cé'))

    def test_derived_dag_cycle_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'cycle'):
            self.parse('## Derived a\nType: nat\nOperation: sum\nInputs: b\n'
                       '## Derived b\nType: nat\nOperation: sum\nInputs: a\n')

    def test_self_cycle_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'cycle'):
            self.parse('## Derived a\nType: list(nat)\nOperation: list\nInputs: a\n')

    def test_deep_dag_and_cycle_do_not_depend_on_python_recursion_limit(self):
        records = tuple(contract.DerivedRecord(f'n{i}', contract.ScalarType('nat'),
            contract.Operation.COUNT, (contract.Reference(f'n{i + 1}'),)) for i in range(1500))
        final = contract.DerivedRecord('n1500', contract.ListType(contract.ScalarType('nat')),
                                       contract.Operation.LIST, ())
        contract.validate_dag(contract.FactsDocument((), records + (final,)))
        final = replace(final, inputs=(contract.Reference('n0'),))
        with self.assertRaisesRegex(contract.ContractError, 'cycle'):
            contract.validate_dag(contract.FactsDocument((), records + (final,)))

    def test_unit_dependency_cycle_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'cycle'):
            self.parse(fact('a', unit='b') + fact('b', unit='a'))

    def test_escaping_relative_path_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'workspace-relative'):
            self.parse(fact(source='../outside.md'))

    def test_absolute_path_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'workspace-relative'):
            self.parse(fact(source=str(self.root / 'input/sources/1/register.md')))

    def test_symlink_escape_rejected(self):
        link = self.root / 'input/sources/1/escape.md'
        link.symlink_to(ROOT / 'AGENTS.md')
        with self.assertRaisesRegex(contract.ContractError, 'escapes workspace'):
            self.parse(fact(source='input/sources/1/escape.md'))

    def test_origin_path_disagreement_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'Origin and path disagree'):
            self.parse(fact(origin='report'))

    def test_source_cannot_use_report_path(self):
        with self.assertRaisesRegex(contract.ContractError, 'Origin and path disagree'):
            self.parse(fact(source='input/report.md'))

    def test_source_symlink_to_report_rejected(self):
        name = 'input/sources/1/report-alias.md'
        (self.root / name).symlink_to(self.root / 'input/report.md')
        binding = contract.SourceBinding(name, contract.Origin.SOURCE, 'en', frozenset({'P1'}))
        with self.assertRaisesRegex(contract.ContractError, 'Origin and path disagree'):
            self.parse(fact(source=name), bindings=self.bindings + (binding,))

    def test_brief_origin_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'Origin'):
            self.parse(fact(origin='brief'))

    def test_unbound_source_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'Unbound source path'):
            self.parse(fact(), bindings=())

    def test_lean_text_in_slots_rejected(self):
        for text in ('by decide', 'amount + reported', 'SourceBound.Facts.amount',
                     '(amount)', 'fun x => x', 'amount := 1', '1', 'amount -- comment'):
            with self.subTest(text=text), self.assertRaisesRegex(contract.ContractError, 'ASCII identifier'):
                self.parse_claims(claim(slots=f'Slot a: {text}\nSlot b: reported\n'))

    def test_unknown_identifier_is_not_formalized(self):
        parsed = self.parse_claims(claim(slots='Slot a: True\nSlot b: reported\n'))[0]
        self.assertIn('Unresolved', parsed.not_formalized_reason)

    def test_unknown_slot_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'Unknown or missing shape slots'):
            self.parse_claims(claim() + 'Slot c: amount\n')

    def test_unresolved_dependency_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'Unresolved dependency'):
            self.parse('## Derived xs\nType: list(nat)\nOperation: list\nInputs: missing\n')

    def test_derived_literal_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'ASCII identifier'):
            self.parse('## Derived xs\nType: list(nat)\nOperation: list\nInputs: 123\n')

    def test_derived_value_field_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'Unknown fields'):
            self.parse('## Derived xs\nType: list(nat)\nOperation: list\nInputs: \nValue: 0\n')

    def test_name_mismatch_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'Name must equal'):
            self.parse(fact().replace('Name: amount', 'Name: other'))

    def test_invalid_offsets_rejected(self):
        for field, value in (('Quote-start', '-1'), ('Select', '0:0'), ('Select', '0:999'),
                             ('Select', '1'), ('Select', '٠:1')):
            lines = fact().splitlines()
            lines = [f'{field}: {value}' if line.startswith(field + ':') else line for line in lines]
            with self.subTest(field=field, value=value), self.assertRaises(contract.ContractError):
                self.parse('\n'.join(lines) + '\n')

    def test_utf8_split_rejected(self):
        text = fact(quote='é 12', token='12', type_='nat', value='12')
        original = next(line for line in text.splitlines() if line.startswith('Select:'))
        with self.assertRaisesRegex(contract.ContractError, 'UTF-8'):
            self.parse(text.replace(original, 'Select: 1:2'))

    def test_quote_is_literal_even_with_headings_and_fields(self):
        quote = 'é\\*\n## Fact forged\nValue: 999\n12'
        parsed = self.parse(fact(quote=quote, token='12', type_='nat', value='12')).facts[0]
        self.assertEqual(parsed.quote, quote)
        self.assertEqual(parsed.quote.encode()[parsed.selection.start:parsed.selection.end], b'12')

    def test_quote_crlf_and_trailing_newline_preserved(self):
        quote = 'first\r\n12\r\n'
        parsed = self.parse(fact(quote=quote, token='12', type_='nat', value='12')).facts[0]
        self.assertEqual(parsed.quote, quote)

    def test_missing_quote_fence_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'fence'):
            self.parse(fact().replace('```\n', '', 1))

    def test_unknown_source_anchor_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'source anchor'):
            self.parse(fact().replace('Anchor: P1', 'Anchor: missing'))

    def test_unknown_report_anchor_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'report anchor'):
            self.parse_claims(claim().replace('Report-anchor: R1', 'Report-anchor: R2'))

    def test_locale_binding_and_reviewed_override(self):
        text = fact().replace('Locale: en', 'Locale: ro')
        with self.assertRaisesRegex(contract.ContractError, 'manifest-bound'):
            self.parse(text)
        bindings = (replace(self.bindings[0], anchor_locales=(('P1', 'ro'),)), self.bindings[1])
        self.assertEqual(self.parse(text, bindings=bindings).facts[0].locale, 'ro')

    def test_all_scalar_and_generated_types(self):
        for type_ in ('nat', 'money(EUR)', 'money(RON)', 'money(USD)', 'date', 'bool', 'enum(evidence_state)', 'ratio'):
            with self.subTest(type=type_):
                self.assertIsInstance(contract.parse_type(type_), contract.ScalarType)
        self.assertEqual(contract.parse_type('list(record(amount:money(EUR),count:nat))', generated=True),
                         contract.ListType(contract.RecordType((('amount', contract.ScalarType('money', 'EUR')),
                                                               ('count', contract.ScalarType('nat'))))))
        self.assertEqual(contract.parse_type('int', generated=True), contract.ScalarType('int'))
        for type_ in ('int', 'float', 'money(GBP)', 'list(list(nat))', 'enum(α)', 'record(x:nat,x:nat)'):
            with self.subTest(type=type_), self.assertRaises(contract.ContractError):
                contract.parse_type(type_)

    def test_record_field_and_forward_references(self):
        text = ('## Derived n\nType: money(EUR)\nOperation: field\nInputs: row\nField: amount\n'
                '## Derived row\nType: record(amount:money(EUR))\nOperation: record\nInputs: \nFields: amount=amount\n')
        parsed = self.parse(text + fact())
        self.assertEqual(parsed.derived[1].fields, (('amount', contract.Reference('amount')),))

    def test_registered_rule_and_predicate_slots(self):
        rule = contract.RuleRegistration('eligible', (('x', contract.ScalarType('money', 'EUR')),),
            contract.ScalarType('bool'), (contract.EvidenceReference('input/sources/1/register.md', 'P1',
            'Q01 amount is EUR 1,234.50', 0),), 'The policy predicate is source-backed.', (contract.Reference('amount'),))
        facts = self.parse(fact() + '## Derived xs\nType: list(money(EUR))\nOperation: list\nInputs: amount\n')
        parsed = self.parse_claims(claim(shape='all', slots='Slot xs: xs\nSlot p: eligible\n', rules='eligible'),
                                   facts=facts, rules=(rule,))[0]
        self.assertEqual(parsed.slots[1].kind, contract.SlotKind.RULE)
        self.assertIsNone(parsed.not_formalized_reason)
        derived = '## Derived answer\nType: bool\nOperation: rule\nInputs: amount\nRule: eligible\n'
        self.assertEqual(self.parse(fact() + derived, rules=(rule,)).derived[0].rule, contract.Reference('eligible'))

    def test_scope_can_cite_prose_without_inventing_scalar_values(self):
        scope = contract.ScopeRegistration('ledger_scope',
            (contract.EvidenceReference('input/sources/1/register.md', 'P1',
                                       'All rows in the current ledger.', 0),),
            'The cited policy defines the selected rows.')
        facts = self.parse(fact(), scopes=(scope,))
        text = claim(scope='ledger_scope', slots='Slot a: amount\nSlot b: amount\n')
        self.assertIsNone(self.parse_claims(text, facts=facts)[0].not_formalized_reason)

    def test_versioned_enum_domains_retained(self):
        domain = contract.EnumDomain('evidence_state', '1', ('Not recorded', 'Absent', 'Present'))
        parsed = self.parse(fact(type_='enum(evidence_state)', value='Present',
                                 quote='Present', token='Present'), enum_domains=(domain,))
        self.assertEqual(parsed.enum_domains, (domain,))
        for domains in ((domain, domain), (replace(domain, labels=('Present', 'Present')),),
                        (replace(domain, labels=()),), (replace(domain, id='dömain'),)):
            with self.subTest(domains=domains), self.assertRaises(contract.ContractError):
                self.parse('', enum_domains=domains)

    def test_rule_registration_is_not_a_value_input(self):
        rule = contract.RuleRegistration('r', (), contract.ScalarType('nat'), (), 'Input category probe')
        with self.assertRaisesRegex(contract.ContractError, 'non-value operation input'):
            self.parse('## Derived xs\nType: list(nat)\nOperation: list\nInputs: r\n', rules=(rule,))

    def test_operation_metadata_and_arity_rejected(self):
        cases = ('## Derived d\nType: nat\nOperation: add\nInputs: amount\n',
                 '## Derived d\nType: list(nat)\nOperation: list\nInputs: amount\nRule: r\n',
                 '## Derived d\nType: nat\nOperation: field\nInputs: amount\n',
                 '## Derived d\nType: nat\nOperation: eval\nInputs: amount\n')
        for text in cases:
            with self.subTest(text=text), self.assertRaises(contract.ContractError):
                self.parse(fact() + text)

    def test_missing_fields_and_unterminated_quote_rejected(self):
        with self.assertRaisesRegex(contract.ContractError, 'missing fields'):
            self.parse(fact().replace('Value: 123450\n', ''))
        with self.assertRaisesRegex(contract.ContractError, 'Unterminated'):
            self.parse(fact().removesuffix('```\n'))
        with self.assertRaisesRegex(contract.ContractError, 'missing fields'):
            self.parse_claims(claim().replace('Shape: equal\n', ''))

    def test_summary_cannot_hide_fields_or_records(self):
        for text in ('Value: 99', '## Fact hidden', '# Another title'):
            with self.subTest(text=text), self.assertRaisesRegex(contract.ContractError, 'Summary'):
                self.parse(fact() + '## Summary\n' + text + '\n')

    def test_signatures_resolve_and_providers_are_unimplemented(self):
        import inspect
        import typing
        for name, member in vars(contract).items():
            if inspect.isclass(member) and member.__module__ == contract.__name__:
                typing.get_type_hints(member)
        for name in ('ExtractFacts', 'MakeFact', 'GenerateFacts', 'RenderClaim', 'CheckGuards', 'CheckProof'):
            provider = getattr(contract, name)
            self.assertTrue(provider._is_protocol)
            typing.get_type_hints(provider.__call__)
        helper = inspect.signature(contract.MakeFact.__call__)
        self.assertIn('occurrence', helper.parameters)
        self.assertIn('quote_occurrence', helper.parameters)
        self.assertIn('context', helper.parameters)

    def test_rule_dependency_cycle_rejected(self):
        rule = contract.RuleRegistration('r', (), contract.ScalarType('nat'), (), 'Cycle probe',
                                         (contract.Reference('answer'),))
        with self.assertRaisesRegex(contract.ContractError, 'cycle'):
            self.parse('## Derived answer\nType: nat\nOperation: rule\nInputs: \nRule: r\n', rules=(rule,))

    def test_unsupported_shape_and_version_retained(self):
        for text in (claim(shape='annex_scope'), claim().replace('Version: 1', 'Version: 2')):
            parsed = self.parse_claims(text)[0]
            self.assertIn('not formalized', parsed.not_formalized_reason)
        with self.assertRaises(contract.ContractError):
            self.parse_claims(claim(shape='annex_scope', slots='Slot a: by decide\n'))

    def test_fixed_twelve_shapes_and_slot_names(self):
        expected = {'equal': ('a', 'b'), 'sum': ('xs', 'v'), 'compare': ('a', 'b', 'cmp'),
            'difference': ('a', 'b', 'v'), 'date_add': ('start', 'due', 'days'),
            'within': ('event', 'due', 'inclusive'), 'all': ('xs', 'p'), 'any': ('xs', 'p'),
            'count': ('xs', 'p', 'n', 'cmp'), 'maximum': ('xs', 'x', 'f', 'v'),
            'conflict': ('a', 'b', 'field'), 'ratio': ('a', 'b', 'r', 'cmp')}
        self.assertEqual({name: tuple(s.name for s in shape.slots) for name, shape in contract.SHAPES.items()}, expected)
        with self.assertRaises(TypeError):
            contract.SHAPES['invented'] = contract.SHAPES['equal']

    def test_operator_and_polarity_choices(self):
        for cmp in ('=', '≠', '<', '≤', '>', '≥'):
            for polarity in ('positive', 'negative'):
                text = claim(shape='compare', slots=f'Slot a: amount\nSlot b: reported\nSlot cmp: {cmp}\n')
                parsed = self.parse_claims(text.replace('Polarity: positive', f'Polarity: {polarity}'))[0]
                self.assertIsNone(parsed.not_formalized_reason)
                self.assertEqual(parsed.polarity.value, polarity)
        for choice in ('==', 'by decide', 'true'):
            with self.subTest(choice=choice), self.assertRaisesRegex(contract.ContractError, 'catalogue choice'):
                self.parse_claims(claim(shape='compare', slots=f'Slot a: amount\nSlot b: reported\nSlot cmp: {choice}\n'))

    def test_status_precedence_and_all_flag_pairs(self):
        F, S = contract.GuardFlag, contract.FormalStatus
        for flags in (frozenset(), *(frozenset({f}) for f in F),
                      *(frozenset(pair) for pair in combinations(F, 2))):
            with self.subTest(flags=flags):
                decision = contract.resolve_status(supported=True, baseline_checked=True,
                    flags=flags, relation=contract.ProofRelation.SUPPORTS)
                expected = (S.REJECTED if F.ROOT_TRUE in flags else
                            S.BLOCKED if F.BASELINE_FAILED in flags else
                            S.AUXILIARY if F.TRANSCRIPTION in flags else
                            S.BLOCKED if flags else S.PROVED)
                self.assertEqual(decision.status, expected)
                self.assertEqual(decision.reference_kind, 'auxiliary' if expected == S.AUXILIARY else
                                 'proof' if expected == S.PROVED else None)
        decision = contract.resolve_status(supported=True, baseline_checked=True, flags=frozenset(),
                                           relation=contract.ProofRelation.REFUTES)
        self.assertEqual(decision.status, S.REFUTED)
        decision = contract.resolve_status(supported=False, baseline_checked=False, flags=frozenset(),
                                           relation=contract.ProofRelation.SUPPORTS)
        self.assertEqual(decision.status, S.NOT_ATTEMPTED)
        self.assertEqual(decision.reason, 'not formalized')
        decision = contract.resolve_status(supported=True, baseline_checked=False,
                                           flags=frozenset({F.TRANSCRIPTION}), relation=contract.ProofRelation.SUPPORTS)
        self.assertEqual(decision.status, S.BLOCKED)

    def test_authored_status_and_reference_inconsistency_rejected(self):
        decision = contract.resolve_status(supported=True, baseline_checked=True,
            flags=frozenset({contract.GuardFlag.TRANSCRIPTION, contract.GuardFlag.INCOMPLETE}),
            relation=contract.ProofRelation.SUPPORTS)
        contract.validate_status(contract.FormalStatus.AUXILIARY, decision, 'auxiliary')
        for status, kind in ((contract.FormalStatus.PROVED, 'proof'), (contract.FormalStatus.BLOCKED, None),
                             (contract.FormalStatus.AUXILIARY, 'proof'), (contract.FormalStatus.AUXILIARY, None)):
            with self.subTest(status=status, kind=kind), self.assertRaises(contract.ContractError):
                contract.validate_status(status, decision, kind)

    def interface_fixture(self):
        """Compatibility fixture consumed identically by phases A, B and C."""
        domain = contract.EnumDomain('evidence_state', '1', ('Not recorded', 'Absent', 'Present'))
        text = fact() + fact('reported', source='input/report.md', origin='report')
        text += fact('day', type_='date', quote='2026-11-12', token='2026-11-12', value='2026-11-12')
        text += fact('rate', type_='ratio', quote='12.5%', token='12.5%', value='1/8')
        text += fact('state', type_='enum(evidence_state)', quote='Present', token='Present', value='Present')
        text += ('## Derived amounts\nType: list(money(EUR))\nOperation: list\nInputs: amount, amount\n'
                 '## Derived report_amounts\nType: list(money(EUR))\nOperation: list\nInputs: reported\n'
                 '## Derived mixed\nType: list(money(EUR))\nOperation: list\nInputs: amount, reported, amount\n'
                 '## Derived total\nType: money(EUR)\nOperation: sum\nInputs: amounts\n'
                 '## Derived row\nType: record(amount:money(EUR),state:enum(evidence_state))\n'
                 'Operation: record\nInputs: \nFields: amount=amount, state=state\n'
                 '## Derived rows\nType: list(record(amount:money(EUR),state:enum(evidence_state)))\n'
                 'Operation: list\nInputs: row\n')
        document = self.parse(text, enum_domains=(domain,))
        row = (('amount', 123450), ('state', 'Present'))
        data = {'amount': (123450, ('amount',), ()), 'reported': (123450, (), ('reported',)),
                'day': (date(2026, 11, 12), ('day',), ()), 'rate': (Fraction(1, 8), ('rate',), ()),
                'state': ('Present', ('state',), ()), 'amounts': ((123450, 123450), ('amount',), ()),
                'report_amounts': ((123450,), (), ('reported',)),
                'mixed': ((123450, 123450, 123450), ('amount',), ('reported',)),
                'total': (246900, ('amount',), ()), 'row': (row, ('amount', 'state'), ()),
                'rows': ((row,), ('amount', 'state'), ())}
        return contract.ResolvedFacts(document, tuple(contract.ResolvedValue(
            r.id, r.type, data[r.id][0], data[r.id][1], data[r.id][2], ())
            for r in (*document.facts, *document.derived)))

    def test_exact_generated_interface_compatibility_fixture(self):
        resolved = self.interface_fixture()
        money = 'SourceBound.Money SourceBound.Currency.eur'
        enum = 'SourceBound.Enums.evidence_state'
        record = 'SourceBound.Records.R_2abb26f04f538f8b0cc473f9323efc2db538844f15fbd6f198eb55e0764fd654'
        T, B = contract.LeanTypeBinding, contract.LeanBinding
        money_type, enum_type = contract.ScalarType('money', 'EUR'), contract.ScalarType('enum', 'evidence_state')
        record_type = contract.RecordType((('amount', money_type), ('state', enum_type)))
        expected = contract.LeanBindingMap(values={
            'amount': B('SourceBound.Facts.amount', money, 'SourceBound.Facts'),
            'reported': B('SourceBound.Report.reported', money, 'SourceBound.Facts'),
            'day': B('SourceBound.Facts.day', 'SourceBound.Date', 'SourceBound.Facts'),
            'rate': B('SourceBound.Facts.rate', 'SourceBound.Ratio', 'SourceBound.Facts'),
            'state': B('SourceBound.Facts.state', enum, 'SourceBound.Facts'),
            'amounts': B('SourceBound.Derived.amounts', f'List ({money})', 'SourceBound.Derived'),
            'report_amounts': B('SourceBound.Derived.report_amounts', f'List ({money})', 'SourceBound.Derived'),
            'mixed': B('SourceBound.Derived.mixed', f'List ({money})', 'SourceBound.Derived'),
            'total': B('SourceBound.Derived.total', money, 'SourceBound.Derived'),
            'row': B('SourceBound.Derived.row', record, 'SourceBound.Derived'),
            'rows': B('SourceBound.Derived.rows', f'List ({record})', 'SourceBound.Derived'),
        }, types={
            money_type: T(money, 'SourceBound.Types', '({value}).minor'),
            contract.ScalarType('date'): T('SourceBound.Date', 'SourceBound.Types', '({value}).ordinal'),
            contract.ScalarType('ratio'): T('SourceBound.Ratio', 'SourceBound.Types', ratio_projection=(
                '({value}).num', 'Int.ofNat (({value}).den)')),
            enum_type: T(enum, 'SourceBound.Facts', constructors=(
                ('Not recorded', enum + '.label_not_recorded'), ('Absent', enum + '.label_absent'),
                ('Present', enum + '.label_present'))),
            contract.ListType(money_type): T(f'List ({money})', None),
            record_type: T(record, 'SourceBound.Facts', fields=(
                ('amount', 'f_amount', money), ('state', 'f_state', enum))),
            contract.ListType(record_type): T(f'List ({record})', None),
        }, module_imports={
            'SourceBound.Types': (), 'SourceBound.Facts': ('SourceBound.Types',),
            'Rules': ('SourceBound.Facts',), 'SourceBound.Derived': ('Rules',),
            'SourceBound.Claims': ('SourceBound.Derived',),
        })
        self.assertEqual(resolved.bindings, expected)
        self.assertEqual(contract.build_lean_bindings(resolved), expected)
        self.assertEqual(contract.MODULE_ORDER, tuple(expected.module_imports))
        self.assertEqual(contract.lean_claim_name('C01'), 'SourceBound.Claims.C01')
        reordered = contract.ResolvedFacts(replace(resolved.document,
            facts=tuple(reversed(resolved.document.facts)), derived=tuple(reversed(resolved.document.derived))),
            tuple(reversed(resolved.values)))
        self.assertEqual(tuple(reordered.bindings.values.items()), tuple(resolved.bindings.values.items()))
        self.assertEqual(tuple(reordered.bindings.types.items()), tuple(resolved.bindings.types.items()))
        with self.assertRaises(TypeError):
            resolved.bindings.values['amount'] = expected.values['reported']
        with self.assertRaises(TypeError):
            resolved.bindings.types[money_type] = expected.types[enum_type]
        with self.assertRaises(TypeError):
            contract.MODULE_IMPORTS['Rules'] = ()

    def test_scalar_type_and_integer_projection_abi(self):
        for name, lean, projection in (('nat', 'Nat', 'Int.ofNat ({value})'),
                                       ('int', 'Int', '({value})'), ('bool', 'Bool', None)):
            self.assertEqual(contract.lean_type_binding(contract.ScalarType(name)),
                             contract.LeanTypeBinding(lean, None, projection))
        for currency in ('EUR', 'RON', 'USD'):
            binding = contract.lean_type_binding(contract.ScalarType('money', currency))
            self.assertEqual(binding.lean_type, f'SourceBound.Money SourceBound.Currency.{currency.lower()}')
            self.assertEqual(contract.lean_comparison_operands(binding, 'a', 'b'), ('(a).minor', '(b).minor'))
        for type_ in (contract.ScalarType('float'), contract.ScalarType('nat', 'x'),
                      contract.ScalarType('money', 'GBP'), contract.ScalarType('enum', 'missing'),
                      contract.ListType(contract.ListType(contract.ScalarType('nat')))):
            with self.subTest(type=type_), self.assertRaises(contract.ContractError):
                contract.lean_type_binding(type_)

    def test_ratio_cross_products_are_signed_and_exact(self):
        binding = self.interface_fixture().bindings.types[contract.ScalarType('ratio')]
        self.assertEqual(contract.lean_comparison_operands(binding, 'left', 'right'), (
            '(left).num * Int.ofNat ((right).den)', '(right).num * Int.ofNat ((left).den)'))
        with self.assertRaisesRegex(contract.ContractError, 'no arithmetic'):
            contract.lean_comparison_operands(contract.lean_type_binding(contract.ScalarType('bool')), 'a', 'b')

    def test_enum_constructor_collisions_reject_before_generation(self):
        for labels in (('Not recorded', 'Not-recorded'), ('Present', 'present'), ('x', 'x'), ('é', 'ue9_')):
            domain = contract.EnumDomain('state', '1', labels)
            with self.subTest(labels=labels), self.assertRaisesRegex(contract.ContractError, 'collision'):
                contract.build_lean_bindings(contract.ResolvedFacts(contract.FactsDocument((), (), (domain,)), ()))
        domain = contract.EnumDomain('state', '1', ('Ștampilat', '123', 'match'))
        self.assertEqual(contract.lean_enum_constructors(domain), (
            ('Ștampilat', 'SourceBound.Enums.state.label_u218_tampilat'),
            ('123', 'SourceBound.Enums.state.label_123'), ('match', 'SourceBound.Enums.state.label_match')))

    def test_record_fields_and_structural_type_identity(self):
        type_ = contract.parse_type('record(match:nat,x:record(value:bool))', generated=True)
        binding = contract.lean_type_binding(type_)
        self.assertEqual(binding.fields[0], ('match', 'f_match', 'Nat'))
        self.assertEqual(binding.fields[1][1], 'f_x')
        self.assertNotEqual(binding.lean_type, contract.lean_type_binding(
            contract.RecordType(tuple(reversed(type_.fields)))).lean_type)

    def test_binding_map_rejects_missing_duplicate_or_mistyped_resolved_values(self):
        resolved = self.interface_fixture()
        for values in (resolved.values[:-1], resolved.values + resolved.values[:1],
                       (replace(resolved.values[0], type=contract.ScalarType('nat')), *resolved.values[1:])):
            with self.subTest(values=values), self.assertRaises(contract.ContractError):
                contract.build_lean_bindings(replace(resolved, values=values))

    def test_report_taint_source_report_and_mixed_lists(self):
        resolved = self.interface_fixture()
        for ident, expected in (('amounts', ()), ('report_amounts', ('reported',)),
                                ('mixed', ('reported',)), ('total', ()), ('rows', ())):
            with self.subTest(ident=ident):
                self.assertEqual(contract.report_origin_leaves(contract.Reference(ident), resolved), expected)
        # Traversal must not trust missing/stale cached taint or stop at a source member.
        stale = replace(resolved, values=tuple(replace(v, report_leaves=()) for v in resolved.values))
        self.assertEqual(contract.report_origin_leaves(contract.Reference('mixed'), stale), ('reported',))
        with self.assertRaisesRegex(contract.ContractError, 'Unresolved dependency'):
            contract.report_origin_leaves(contract.Reference('missing'), resolved)

    def test_report_taint_traverses_units_fields_and_rule_dependencies(self):
        resolved = self.interface_fixture()
        amount = replace(resolved.document.facts[0], unit_source=contract.Reference('reported'))
        unit = replace(resolved, document=replace(resolved.document, facts=(amount, *resolved.document.facts[1:])))
        self.assertEqual(contract.report_origin_leaves(contract.Reference('rows'), unit), ('reported',))
        rule = contract.RuleRegistration('f', (), contract.ScalarType('money', 'EUR'), (), 'probe',
                                         (contract.Reference('reported'),))
        derived = contract.DerivedRecord('answer', contract.ScalarType('money', 'EUR'), contract.Operation.RULE,
                                        (), rule=contract.Reference('f'))
        rules = replace(resolved, document=replace(resolved.document, derived=resolved.document.derived + (derived,)))
        self.assertEqual(contract.report_origin_leaves(contract.Reference('answer'), rules, (rule,)), ('reported',))

    def test_phase_b_implicit_rule_operand_gate(self):
        resolved = self.interface_fixture()
        for shape in ('all', 'any', 'count', 'maximum'):
            for polarity in contract.Polarity:
                for ident in ('amounts', 'report_amounts', 'mixed'):
                    slots = (contract.ClaimSlot('xs', contract.SlotKind.VALUE, contract.Reference(ident)),
                             contract.ClaimSlot('x', contract.SlotKind.VALUE, contract.Reference('amount')))
                    c = contract.ClaimRecord('C', 'R1', shape, '1', slots, polarity, (), (), 'probe')
                    reason = contract.claim_rule_input_reason(c, resolved)
                    with self.subTest(shape=shape, polarity=polarity, ident=ident):
                        if ident == 'amounts':
                            self.assertIsNone(reason)
                        else:
                            self.assertEqual(reason, 'not formalized: rule operand xs has report-origin leaves: reported')
        # The selected x is a separate projection operand; v is only a comparator.
        c = replace(c, shape='maximum', slots=(
            contract.ClaimSlot('xs', contract.SlotKind.VALUE, contract.Reference('amounts')),
            contract.ClaimSlot('x', contract.SlotKind.VALUE, contract.Reference('reported')),
            contract.ClaimSlot('v', contract.SlotKind.VALUE, contract.Reference('reported'))))
        self.assertIn('operand x', contract.claim_rule_input_reason(c, resolved))
        c = replace(c, slots=(c.slots[0], replace(c.slots[1], value=contract.Reference('amount')), c.slots[2]))
        self.assertIsNone(contract.claim_rule_input_reason(c, resolved))
        self.assertIsNone(contract.claim_rule_input_reason(replace(c, shape='equal'), resolved))
        with self.assertRaisesRegex(contract.ContractError, 'Missing or malformed'):
            contract.claim_rule_input_reason(replace(c, slots=()), resolved)


if __name__ == '__main__':
    unittest.main()
