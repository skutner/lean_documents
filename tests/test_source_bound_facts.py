"""Phase A normative parsing, provenance/DAG rejection and real Lean checks."""
from dataclasses import replace
from datetime import date
from fractions import Fraction
from hashlib import sha256
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/evidence-to-proof-review/scripts'))
import source_bound_contract as c
import source_bound_facts as f

FIXTURES = ROOT / 'tests/fixtures/source_bound/facts'
NAT, INT, EUR = c.ScalarType('nat'), c.ScalarType('int'), c.ScalarType('money', 'EUR')
RATIO, DATE, BOOL = c.ScalarType('ratio'), c.ScalarType('date'), c.ScalarType('bool')


def table(headers, rows):
    return '| ' + ' | '.join(headers) + ' |\n| ' + ' | '.join('---' for _ in headers) + ' |\n' + ''.join('| ' + ' | '.join(map(str, row)) + ' |\n' for row in rows)


class FactsTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='sb-facts-', dir=ROOT / 'tmp'))
        self.bindings = []
        self.domains = ()

    def manifest(self):
        paths = sorted(p for p in (self.root / 'input').rglob('*') if p.is_file())
        (self.root / 'manifest.md').write_text('# Manifest\n\n' + table(('Path', 'SHA256'),
            [(p.relative_to(self.root).as_posix(), sha256(p.read_bytes()).hexdigest()) for p in paths]))

    def source(self, text, *, locale='en', origin=c.Origin.SOURCE, name=None, anchor='P01'):
        name = name or f'doc{len(self.bindings)}.md'
        path = ('input/sources/1/' if origin == c.Origin.SOURCE else 'input/') + name
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode())
        self.bindings.append(c.SourceBinding(path, origin, locale, frozenset({anchor})))
        self.manifest()
        return path

    def fact(self, text, *, type_=NAT, locale='en', ident=None, quote=None, token=None,
             origin=c.Origin.SOURCE, **kwargs):
        path = self.source(text, locale=locale, origin=origin)
        return f.make_fact(self.root, id=ident or f'v{len(self.bindings)}', type=type_, source_path=path,
                          anchor='P01', quote=text if quote is None else quote,
                          selected_token=text if token is None else token, bindings=tuple(self.bindings), **kwargs)

    def extract(self, facts, derived=(), rules=(), scopes=()):
        return f.extract_facts(self.root, c.FactsDocument(tuple(facts), tuple(derived), self.domains, scopes),
                               tuple(self.bindings), rules)

    def derived(self, ident, type_, op, *inputs, **kwargs):
        return c.DerivedRecord(ident, type_, c.Operation(op), tuple(map(c.Reference, inputs)), **kwargs)

    def test_normative_parsing_matrix(self):
        for line in (FIXTURES / 'parsing-v1.tsv').read_text().splitlines():
            if not line or line.startswith('#'):
                continue
            kind, locale, token, expected = line.split('\t')
            with self.subTest(kind=kind, locale=locale, token=token):
                if expected == 'REJECT':
                    with self.assertRaises(c.ContractError):
                        f.parse_token(token, c.parse_type(kind), locale)
                else:
                    value, _ = f.parse_token(token, c.parse_type(kind), locale)
                    self.assertEqual(f._canonical(value), expected)

    def test_every_small_word_and_multiplier(self):
        for locale, words in f._WORDS.items():
            for word, value in words.items():
                token = word + ' proiect' if word in {'un', 'o'} else word
                with self.subTest(locale=locale, word=word):
                    self.assertEqual(f.parse_token(token, NAT, locale)[0], value)
                    with self.assertRaises(c.ContractError):
                        f.parse_token(token + 'ish', NAT, locale)
        for word, mult in f._MULTIPLIERS.items():
            locale = 'en' if word in {'thousand', 'million'} else 'ro'
            self.assertEqual(f.parse_token(f'2 {word} EUR', EUR, locale)[0], 200 * mult)
            with self.assertRaises(c.ContractError):
                f.parse_token(f'2 {word}ish EUR', EUR, locale)

    def test_exact_assertion_and_multiple_numbers(self):
        record = self.fact('Q01 amount is EUR 1,234.50; Q02 amount is EUR 5.00.', type_=EUR,
                           token='EUR 1,234.50')
        self.assertEqual(self.extract([record]).values[0].value, 123450)
        with self.assertRaisesRegex(c.ContractError, 'Asserted Value'):
            self.extract([replace(record, asserted_value='500')])
        with self.assertRaises(c.ContractError):
            self.fact('4 and 4', quote='4 and 4', token='4')
        record = self.fact('4 and 4', quote='4 and 4', token='4', occurrence=1)
        self.assertEqual(record.selection, c.ByteSpan(6, 7))
        self.assertEqual(self.extract([record]).values[0].value, 4)

    def test_utf8_offsets_and_repeated_quotes(self):
        record = self.fact('é: 12; é: 12', quote='é: 12', token='12', quote_occurrence=1)
        self.assertEqual(record.quote_start, 8)
        self.assertEqual(record.selection, c.ByteSpan(4, 6))
        self.extract([record])
        with self.assertRaises(c.ContractError):
            self.fact('é: 12; é: 12', quote='é: 12', token='12')
        with self.assertRaises(c.ContractError):
            self.extract([replace(record, selection=c.ByteSpan(1, 2))])
        for index in (-1, 2, True):
            with self.subTest(index=index), self.assertRaises(c.ContractError):
                self.fact('12', occurrence=index)

    def test_boundary_rejections_even_when_quote_is_shrunk(self):
        cases = [('Q123', '123', NAT), ('2024-01-02', '2024', NAT), ('1,234', '234', NAT),
                 ('1.25', '1', NAT), ('12345', '123', NAT), ('-12', '12', NAT),
                 ('(12)', '12', NAT), ('12%', '12', NAT), ('EUR 12', '12', NAT),
                 ('12 million', '12', NAT), ('1 234', '234', NAT), ('12...', '12', NAT),
                 ('12–14', '12', NAT), ('- EUR 20', 'EUR 20', EUR),
                 ('31 December 2024', '31', NAT), ('31 December 2024', '2024', NAT), ('2024-01-01T12:00', '2024-01-01', DATE),
                 ('2024-01-01 12:00', '2024-01-01', DATE)]
        for text, token, type_ in cases:
            with self.subTest(text=text), self.assertRaises(c.ContractError):
                self.fact(text, token=token, quote=token, type_=type_)

    def test_complete_token_after_prose_punctuation(self):
        for text in ('After adjustment, EUR 20', 'Budget approved. EUR 20'):
            with self.subTest(text=text):
                record = self.fact(text, token='EUR 20', type_=EUR)
                self.assertEqual(self.extract([record]).values[0].value, 2000)

    def test_wrong_offset_tampering_and_unbound_hash(self):
        record = self.fact('The total is 12.', token='12')
        with self.assertRaisesRegex(c.ContractError, 'Quote bytes'):
            self.extract([replace(record, quote_start=1)])
        (self.root / record.source_path).write_text('The total is 13.')
        with self.assertRaisesRegex(c.ContractError, 'Input changed'):
            self.extract([record])
        (self.root / 'manifest.md').write_text('# Empty\n')
        with self.assertRaises(c.ContractError):
            self.extract([record])

    def test_locale_override_and_missing_locale(self):
        path = self.source('1,234', locale='')
        with self.assertRaises(c.ContractError):
            f.make_fact(self.root, id='n', type=NAT, source_path=path, anchor='P01', quote='1,234',
                        selected_token='1,234', bindings=tuple(self.bindings))
        self.bindings[0] = replace(self.bindings[0], anchor_locales=(('P01', 'en'),))
        record = f.make_fact(self.root, id='n', type=NAT, source_path=path, anchor='P01', quote='1,234',
                             selected_token='1,234', bindings=tuple(self.bindings))
        self.assertEqual(self.extract([record]).values[0].value, 1234)
        with self.assertRaises(c.ContractError):
            self.extract([replace(record, locale='ro')])

    def test_bool_whole_cell_and_exact_enum(self):
        self.assertIs(self.extract([self.fact('| flag | YES |', token='YES', type_=BOOL)]).values[0].value, True)
        with self.assertRaisesRegex(c.ContractError, 'whole cell'):
            self.fact('They say yes today.', quote='yes', token='yes', type_=BOOL)
        self.domains = (c.EnumDomain('state', '1', ('Not recorded', 'Absent', 'Present')),)
        for label in self.domains[0].labels:
            record = self.fact(label, type_=c.ScalarType('enum', 'state'),
                               context=c.FactsDocument((), (), self.domains))
            self.assertEqual(self.extract([record]).values[0].value, label)
        with self.assertRaises(c.ContractError):
            self.fact('present', type_=c.ScalarType('enum', 'state'), context=c.FactsDocument((), (), self.domains))

    def test_unit_header_and_count_unit_mismatch(self):
        self.domains = (c.EnumDomain('units', '1', ('EUR', 'USD', 'days', 'items', 'proiecte')),)
        unit = self.fact('EUR', ident='unit', type_=c.ScalarType('enum', 'units'), context=c.FactsDocument((), (), self.domains))
        context = c.FactsDocument((unit,), (), self.domains)
        amount = self.fact('1,234.50', type_=EUR, unit_source=c.Reference('unit'), context=context)
        self.assertEqual(next(v.value for v in self.extract([unit, amount]).values if v.id == amount.id), 123450)
        with self.assertRaises(c.ContractError):
            self.fact('2', type_=EUR, unit_source=c.Reference('missing'))
        days = self.fact('2 days', ident='days')
        items = self.fact('2 items', ident='items')
        with self.assertRaisesRegex(c.ContractError, 'unit'):
            self.extract([days, items], [self.derived('sum', NAT, 'add', 'days', 'items')])

    def test_report_origin_taint_and_copied_report(self):
        source = self.fact('2', ident='source')
        report = self.fact('Claimed count: 3', quote='3', token='3', ident='reported', origin=c.Origin.REPORT)
        xs = self.derived('xs', c.ListType(NAT), 'list', 'source', 'reported')
        resolved = self.extract([source, report], [xs])
        self.assertEqual(next(v.report_leaves for v in resolved.values if v.id == 'xs'), ('reported',))
        policy = c.EvidenceReference(source.source_path, 'P01', source.quote, 0)
        rule = c.RuleRegistration('pred', (('x', NAT),), BOOL, (policy,), 'Fixture predicate is sourced.')
        scope = c.ScopeRegistration('scope', (policy,), 'All rows in this fixture.')
        filtered = self.derived('filtered', c.ListType(NAT), 'filter', 'xs', rule=c.Reference('pred'), scope=(c.Reference('scope'),))
        with self.assertRaisesRegex(c.ContractError, 'Report-origin'):
            self.extract([source, report], [xs, filtered], (rule,), (scope,))
        poisoned = replace(rule, dependencies=(c.Reference('reported'),))
        with self.assertRaisesRegex(c.ContractError, 'Report-origin'):
            self.extract([source, report], rules=(poisoned,))
        self.source('Claimed count: 3')
        with self.assertRaisesRegex(c.ContractError, 'copied report'):
            self.extract([source, report])

    def test_source_role_disagreement_and_path_escape(self):
        record = self.fact('2')
        for bad in (replace(record, origin=c.Origin.REPORT), replace(record, source_path='../outside.md')):
            with self.subTest(bad=bad), self.assertRaises(c.ContractError):
                self.extract([bad])
        target = self.root / 'input/sources/1/link.md'
        target.symlink_to('/etc/hosts')
        with self.assertRaises(c.ContractError):
            self.source('3')  # Manifest follows path but extraction still rejects escape.
            self.extract([record])

    def arithmetic_fixture(self):
        a, b = self.fact('EUR 2.50', ident='a', type_=EUR), self.fact('EUR 1.00', ident='b', type_=EUR)
        count, ratio = self.fact('2', ident='n'), self.fact('12.5%', ident='r', type_=RATIO)
        start = self.fact('2024-02-28', ident='start', type_=DATE)
        rt = c.RecordType((('amount', EUR), ('count', NAT)))
        ds = [self.derived('xs', c.ListType(EUR), 'list', 'a', 'b', 'a'),
              self.derived('total', EUR, 'sum', 'xs'), self.derived('count', NAT, 'count', 'xs'),
              self.derived('added', EUR, 'add', 'a', 'b'), self.derived('sub', EUR, 'subtract', 'b', 'a'),
              self.derived('signed', INT, 'subtract', 'n', 'count'),
              self.derived('mul', EUR, 'multiply', 'a', 'n'), self.derived('div', RATIO, 'ratio', 'a', 'b'),
              self.derived('due', DATE, 'date_add', 'start', 'n'), self.derived('elapsed', INT, 'subtract', 'due', 'start'),
              self.derived('row', rt, 'record', fields=(('amount', c.Reference('a')), ('count', c.Reference('n')))),
              self.derived('field', EUR, 'field', 'row', field='amount'),
              self.derived('rats', c.ListType(RATIO), 'list', 'r', 'r'),
              self.derived('rat_sum', RATIO, 'sum', 'rats'), self.derived('rat_add', RATIO, 'add', 'r', 'r'),
              self.derived('rat_sub', RATIO, 'subtract', 'r', 'r'), self.derived('rat_mul', RATIO, 'multiply', 'r', 'n'),
              self.derived('rat_div', RATIO, 'ratio', 'r', 'r'), self.derived('cents', EUR, 'multiply', 'b', 'rat_sum')]
        resolved = self.extract([a, b, count, ratio, start], reversed(ds))
        values = {v.id: v.value for v in resolved.values}
        self.assertEqual({k: values[k] for k in ('total', 'count', 'added', 'sub', 'signed', 'mul', 'div', 'elapsed', 'field', 'rat_sum', 'cents')},
                         dict(total=600, count=3, added=350, sub=-150, signed=-1, mul=500, div=Fraction(5, 2), elapsed=2, field=250, rat_sum=Fraction(1, 4), cents=25))
        self.assertEqual(values['due'], date(2024, 3, 1))
        return resolved

    def test_dag_exact_arithmetic_and_types(self):
        self.arithmetic_fixture()

    def test_empty_money_list_keeps_currency_unit(self):
        a = self.fact('EUR 2', type_=EUR, ident='a')
        ds = [self.derived('xs', c.ListType(EUR), 'list'),
              self.derived('zero', EUR, 'sum', 'xs'), self.derived('total', EUR, 'add', 'zero', 'a')]
        resolved = self.extract([a], ds)
        self.assertEqual(next(v.value for v in resolved.values if v.id == 'total'), 200)

    def test_currency_mismatch_zero_division_and_bad_operations(self):
        a = self.fact('2 EUR', type_=EUR, ident='a')
        b = self.fact('3 USD', type_=c.ScalarType('money', 'USD'), ident='b')
        zero = self.fact('0', ident='zero')
        fraction = self.fact('0.001', type_=RATIO, ident='fraction')
        bad = [self.derived('d', c.ListType(EUR), 'list', 'a', 'b'),
               self.derived('d', EUR, 'add', 'a', 'b'), self.derived('d', EUR, 'multiply', 'a', 'b'),
               self.derived('d', RATIO, 'ratio', 'zero', 'zero'), self.derived('d', NAT, 'subtract', 'zero', 'zero'),
               self.derived('d', EUR, 'multiply', 'a', 'fraction'), self.derived('d', NAT, 'sum', 'zero'),
               self.derived('d', NAT, 'field', 'zero', field='missing')]
        for d in bad:
            with self.subTest(op=d.operation), self.assertRaises(c.ContractError):
                self.extract([a, b, zero, fraction], [d])
        xs = self.derived('xs', c.ListType(c.ScalarType('money', 'USD')), 'list', 'b')
        with self.assertRaises(c.ContractError):
            self.extract([a, b], [xs, self.derived('total', EUR, 'sum', 'xs')])
        with self.assertRaises(c.ContractError):
            self.extract([zero], [self.derived('cycle', NAT, 'add', 'zero', 'cycle')])

    def symbolic_fixture(self):
        a = self.fact('2', ident='a')
        policy = c.EvidenceReference(a.source_path, 'P01', '2', 0)
        rule = c.RuleRegistration('identity', (('x', NAT),), NAT, (policy,), 'Fixture identity function.')
        pred = c.RuleRegistration('keep', (('x', NAT),), BOOL, (policy,), 'Fixture always-true predicate.')
        scope = c.ScopeRegistration('scope', (policy,), 'Fixture all rows.')
        ds = [self.derived('xs', c.ListType(NAT), 'list', 'a', 'a'),
              self.derived('selected', c.ListType(NAT), 'filter', 'xs', rule=c.Reference('keep'), scope=(c.Reference('scope'),)),
              self.derived('size', NAT, 'count', 'selected'), self.derived('sum', NAT, 'sum', 'selected'),
              self.derived('call', NAT, 'rule', 'a', rule=c.Reference('identity')),
              self.derived('plus', NAT, 'add', 'call', 'a')]
        resolved = self.extract([a], ds, (rule, pred), (scope,))
        self.assertTrue(all(isinstance(v.value, f.SymbolicValue) for v in resolved.values if v.id not in {'a', 'xs'}))
        self.assertIn('Rules.identity', '\n'.join(g.content for g in f.generate_facts(resolved, (rule, pred))))
        with self.assertRaises(c.ContractError):
            self.extract([a], ds + [self.derived('unsafe', RATIO, 'ratio', 'a', 'call')], (rule, pred), (scope,))
        return resolved, (rule, pred)

    def test_symbolic_rule_filter_and_transitive_descendants(self):
        self.symbolic_fixture()

    def test_table_all_rows_duplicates_order_and_rejections(self):
        text = '| Row | Amount |\n| --- | --- |\n| Q1 | EUR 2 |\n| Q2 | EUR 2 |'
        path = self.source(text)
        result = f.read_table(self.root, source_path=path, anchor='P01', column='Amount', row_id_column='Row',
                              id_prefix='rows', type=EUR, bindings=tuple(self.bindings))
        self.assertEqual(result.count, 2)
        self.assertEqual([r for r, _ in result.rows], ['Q1', 'Q2'])
        values = self.extract(result.facts, [result.list_record]).values
        self.assertEqual(next(v.value for v in values if v.id == 'rows'), (200, 200))
        for malformed in (text.replace('Q2', 'Q1'), text.replace('EUR 2 |', '|', 1), text + '\n| Q3 | 3 | extra |',
                          text + '\n\n' + text, text + '\n Q3 | EUR 3 |'):
            path = self.source('<a id="P01"></a>\n' + malformed)
            with self.subTest(text=malformed), self.assertRaises(c.ContractError):
                f.read_table(self.root, source_path=path, anchor='P01', column='Amount', row_id_column='Row',
                             id_prefix='rows', type=EUR, bindings=tuple(self.bindings))

    def test_native_uncertainty_provenance_and_stale_review(self):
        record = self.fact('12', ident='n')
        original = 'input/sources/1/original.pdf'
        (self.root / original).write_bytes(b'fixture original; not a real PDF')
        original_hash = sha256((self.root / original).read_bytes()).hexdigest()
        projection_hash = sha256(b'12').hexdigest()
        (self.root / 'input/extraction-index.md').write_text(table(
            ('Original', 'Original SHA256', 'Projection', 'Projection SHA256', 'Extraction record', 'Status', 'Warnings'),
            [(original, original_hash, record.source_path, projection_hash, 'extraction/D1/extraction.md', 'ready', 'OCR uncertainty')]))
        (self.root / 'input/extraction-segments.md').write_text(table(
            ('Original', 'Projection', 'Anchor', 'Passage SHA256', 'Method', 'Review required', 'Uncertainty', 'Extraction record'),
            [(original, record.source_path, 'P01', projection_hash, 'OCR', 'yes', 'OCR uncertainty', 'extraction/D1/extraction.md')]))
        self.manifest()
        with self.assertRaisesRegex(c.ContractError, 'hash-bound'):
            self.extract([record])
        review = table(('Projection', 'Anchor', 'Original SHA256', 'Passage SHA256', 'Decision', 'Reviewer', 'Reason'),
                       [(record.source_path, 'P01', original_hash, projection_hash, 'accepted', 'Fixture reviewer', 'Synthetic gate test')])
        (self.root / 'extraction-review.md').write_text(review)
        provenance = self.extract([record]).values[0].provenance[0]
        self.assertEqual(provenance.original_path, original)
        self.assertEqual(provenance.uncertainty, ('OCR uncertainty',))
        self.assertIn('accepted', provenance.uncertainty_decision)
        (self.root / 'extraction-review.md').write_text(review.replace(original_hash, '0' * 64))
        with self.assertRaises(c.ContractError):
            self.extract([record])

    def test_regeneration_byte_identity_binding_map_and_no_quote_in_lean(self):
        record = self.fact('Injected text: theorem fake : False := by sorry; actual value 12.', token='12')
        resolved = self.extract([record])
        first = f.generate_facts(resolved)
        second = f.generate_facts(self.extract([record]))
        self.assertEqual(first, second)
        lean = '\n'.join(g.content for g in first if g.path.endswith('.lean'))
        self.assertNotIn('theorem fake', lean)
        b = resolved.bindings.values[record.id]
        self.assertIn(f'def {b.name} : {b.lean_type} := 12', lean)
        self.assertIn('theorem fake', first[-1].content)

    def test_synthetic_unit_header_propagates_or_rejects_currency(self):
        self.domains = (c.EnumDomain('units', '1', ('EUR', 'euro cents', 'USD', 'days', 'items')),)
        unit = self.fact('EUR', ident='unit', type_=c.ScalarType('enum', 'units'),
                         context=c.FactsDocument((), (), self.domains))
        amount = self.fact('2', ident='amount', type_=EUR, unit_source=c.Reference('unit'),
                           context=c.FactsDocument((unit,), (), self.domains))
        baseline = self.extract([unit, amount])
        changed = f.recompute(baseline, {'unit': 'euro cents'})
        self.assertEqual(next(v.value for v in changed.values if v.id == 'amount'), 2)
        self.assertIn(':= ⟨2⟩', f.generate_facts(changed)[1].content)
        with self.assertRaisesRegex(c.ContractError, 'Currency'):
            f.recompute(baseline, {'unit': 'USD'})
        reported = self.fact('Reported: 2', quote='2', token='2', ident='reported', type_=EUR,
                             origin=c.Origin.REPORT, unit_source=c.Reference('unit'),
                             context=c.FactsDocument((unit,), (), self.domains))
        with self.assertRaisesRegex(c.ContractError, 'report-origin'):
            f.recompute(self.extract([unit, amount, reported]), {'unit': 'euro cents'})
        days = self.fact('days', ident='days', type_=unit.type,
                         context=c.FactsDocument((), (), self.domains))
        count = self.fact('2', ident='count', unit_source=c.Reference('days'),
                          context=c.FactsDocument((days,), (), self.domains))
        start = self.fact('2024-01-01', ident='start', type_=DATE)
        due = self.derived('due', DATE, 'date_add', 'start', 'count')
        dated = self.extract([days, count, start], [due])
        with self.assertRaisesRegex(c.ContractError, 'day count'):
            f.recompute(dated, {'days': 'items'})

    def test_units_and_synthetic_recomputation(self):
        a = self.fact('2 days', ident='a')
        b = self.fact('3 days', ident='b')
        xs = self.derived('xs', c.ListType(NAT), 'list', 'a', 'b', 'a')
        total = self.derived('total', NAT, 'sum', 'xs')
        baseline = self.extract([a, b], [xs, total])
        self.assertEqual(f.value_unit(c.Reference('total'), baseline), 'day')
        for changes, expected in (({'a': 4}, 11), ({'xs': (3, 2)}, 5), ({'xs': (2, 2, 3, 2)}, 9)):
            with self.subTest(changes=changes):
                changed = f.recompute(baseline, changes)
                self.assertEqual(changed.document, baseline.document)
                self.assertEqual(next(v.value for v in changed.values if v.id == 'total'), expected)
                for old, new in zip(baseline.values, changed.values):
                    self.assertEqual(replace(new, value=old.value), old)
                generated = f.generate_facts(changed)
                self.assertIn('SYNTHETIC', generated[-1].content)
                self.assertEqual(generated, f.generate_facts(f.recompute(baseline, changes)))
        with self.assertRaises(c.ContractError):
            f.recompute(baseline, {'a': -1})
        with self.assertRaises(c.ContractError):
            f.recompute(baseline, {'xs': (2, 3)})

    def test_cli_success_and_error(self):
        path = self.source('Total: 12')
        binding_path = self.root / 'bindings.md'
        binding_path.write_text(table(('Path', 'Origin', 'Locale', 'Anchors'), [(path, 'source', 'en', 'P01')]))
        command = [sys.executable, '-B', str(Path(f.__file__)), str(self.root), '--id', 'n', '--type', 'nat',
                   '--source', path, '--anchor', 'P01', '--quote', 'Total: 12', '--token', '12', '--bindings', str(binding_path)]
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Select: 7:9', result.stdout)
        c.parse_facts(result.stdout, workspace=self.root, bindings=tuple(self.bindings))
        result = subprocess.run(command + ['--occurrence', '1'], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('occurrence', result.stderr)

    def compile(self, resolved, rules=(), rule_text=''):
        run_root = Path(tempfile.mkdtemp(prefix='lean-', dir=self.root))
        for artifact in f.generate_facts(resolved, rules):
            path = run_root / artifact.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(artifact.content)
        (run_root / 'models/Rules.lean').write_text('import SourceBound.Facts\n' + rule_text)
        lean = os.environ['AUDIT_TEST_LEAN']
        env = {**os.environ, 'LEAN_PATH': str(run_root / 'models')}
        checks = ['import SourceBound.Derived']
        for v in resolved.values:
            if not f._symbolic(v.value):
                checks.append(f'example : {resolved.bindings.values[v.id].name} = ({f._literal(v.value, v.type, resolved.document.enum_domains)} : {resolved.bindings.values[v.id].lean_type}) := by decide')
        (run_root / 'models/Checks.lean').write_text('\n'.join(checks) + '\n')
        for module in (*c.MODULE_ORDER[:-1], 'Checks'):
            relative = module.replace('.', '/')
            command = [lean, '-o', relative + '.olean', relative + '.lean']
            result = subprocess.run(command, cwd=run_root / 'models', env=env, text=True, capture_output=True, timeout=30)
            (run_root / (module + '.log')).write_text('Command: ' + repr(command) + '\nExit code: ' + str(result.returncode) + '\n' + result.stdout + result.stderr)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(os.environ.get('AUDIT_TEST_LEAN'), 'AUDIT_TEST_LEAN unset; Lean runs on the Linux host')
    def test_lean_arithmetic_workspace(self):
        baseline = self.arithmetic_fixture()
        self.compile(baseline)
        self.compile(f.recompute(baseline, {'a': 300}))
        self.compile(f.recompute(baseline, {'xs': (100, 250)}))

    @unittest.skipUnless(os.environ.get('AUDIT_TEST_LEAN'), 'AUDIT_TEST_LEAN unset; Lean runs on the Linux host')
    def test_lean_symbolic_workspace(self):
        resolved, rules = self.symbolic_fixture()
        self.compile(resolved, rules, 'def Rules.identity (x : Nat) : Nat := x\ndef Rules.keep (_x : Nat) : Bool := true\n')

    @unittest.skipUnless(os.environ.get('AUDIT_TEST_LEAN'), 'AUDIT_TEST_LEAN unset; Lean runs on the Linux host')
    def test_lean_enum_record_report_workspace(self):
        self.domains = (c.EnumDomain('state', '1', ('Not recorded', 'Absent', 'Present', 'Șase')),)
        context = c.FactsDocument((), (), self.domains)
        state = self.fact('Present', ident='state', type_=c.ScalarType('enum', 'state'), context=context)
        report = self.fact('Report value: EUR 2', ident='reported', token='EUR 2', type_=EUR, origin=c.Origin.REPORT)
        flag = self.fact('yes', type_=BOOL, ident='flag')
        rt = c.RecordType((('state', state.type), ('flag', BOOL)))
        row = self.derived('row', rt, 'record', fields=(('state', c.Reference('state')), ('flag', c.Reference('flag'))))
        rows = self.derived('rows', c.ListType(rt), 'list', 'row', 'row')
        self.compile(self.extract([state, report, flag], [row, rows]))


if __name__ == '__main__':
    unittest.main()
