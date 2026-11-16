"""Coverage measures source extraction, never truth or proof acceptance."""
from contextlib import redirect_stdout
from datetime import date
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evalsuite import source_bound_coverage as coverage


class CoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        cls.root = Path(tempfile.mkdtemp(prefix='sb-coverage-tests-', dir=ROOT / 'tmp'))
        cls.result, cls.markdown = coverage.measure(511, 40, list(coverage.generate.FAMILIES),
                                                   ['correct', 'mixed'], cls.root / 'canonical')

    def extractor(self, text):
        root = Path(tempfile.mkdtemp(prefix='extract-', dir=self.root))
        return coverage.Extraction(root, {'source.md': text})

    def test_canonical_shape_estimate_and_all_claims(self):
        r = self.result
        self.assertEqual(r['totals']['claims'], 676)
        self.assertEqual(r['totals']['shape_eligible'], 605)
        self.assertEqual((r['observed_kinds'], r['shape_eligible_kinds']), (28, 26))
        self.assertEqual(r['spec_estimate'], 'reproduced')
        self.assertEqual(sum(row['claims'] for row in r['rows']), 676)
        self.assertEqual(len({(q['dossier'], q['variant'], q['claim']) for q in r['claims']}), 676)
        self.assertEqual(set(q['kind'] for q in r['claims']), set(coverage.KIND_SHAPES))
        self.assertTrue(all(shape is None or shape in coverage.c.SHAPES for shape, _ in coverage.KIND_SHAPES.values()))

    def test_all_values_required_and_no_unmeasured_passes(self):
        for q in self.result['claims']:
            self.assertTrue(q['values'])
            self.assertEqual(q['extractable'], all(v['ok'] for v in q['values']))
            for key in ('rule_eligibility', 'execution', 'guard_acceptance'):
                self.assertEqual(q[key], 'not measured')
        self.assertIn('605/676 (89.5%)', self.markdown)
        self.assertTrue(self.markdown.startswith('Measured:'))
        self.assertEqual(json.loads((self.root / 'canonical/coverage.json').read_text())['totals'], self.result['totals'])
        self.assertEqual((self.root / 'canonical/coverage.md').read_text(), self.markdown)

    def test_single_variant_is_identical_subset(self):
        result, _ = coverage.measure(511, 1, ['ledger'], ['mixed'], self.root / 'single')
        expected = [q for q in self.result['claims'] if q['dossier'] == 'g511-001-ledger' and q['variant'] == 'mixed']
        self.assertEqual(result['claims'], expected)

    def test_regeneration_matches_public_generator_and_restores_profiler(self):
        before = sys.getprofile()
        result, data, reports = coverage.regenerate('packet', 17, 1)
        self.assertIs(sys.getprofile(), before)
        self.assertEqual(result, coverage.generate.build_case('packet', 17, 1))
        for variant in ('correct', 'mixed'):
            self.assertEqual(coverage.generate.report(data['title'] if 'title' in data else 'Synthetic calibration packet review', reports[variant]),
                             result['files'][f'report-{variant}.md'])
        with patch.object(coverage.generate, 'GENERATOR_VERSION', 99):
            with self.assertRaisesRegex(ValueError, 'version 4'):
                coverage.regenerate('ledger', 1, 1)

    def test_unmapped_fails_unless_allowed_and_remains_in_denominator(self):
        mapping = dict(coverage.KIND_SHAPES)
        del mapping['sample_total']
        args = ['--seed', '511', '--dossiers', '1', '--families', 'ledger', '--out-root', str(self.root / 'unmapped')]
        with patch.object(coverage, 'KIND_SHAPES', mapping), redirect_stdout(io.StringIO()):
            self.assertEqual(coverage.main(args), 1)
            self.assertEqual(coverage.main(args + ['--allow-unmapped']), 0)
        r = json.loads((self.root / 'unmapped/coverage.json').read_text())
        self.assertEqual(r['totals']['unmapped'], 2)
        self.assertEqual(r['totals']['claims'], 18)
        self.assertEqual(r['totals']['failures']['unmapped_kind'], 2)

    def test_invalid_configuration(self):
        for dossiers, families, variants in ((0, ['ledger'], ['correct']), (-1, ['ledger'], ['mixed']),
                (1, [], ['correct']), (1, ['bad'], ['correct']), (1, ['ledger', 'ledger'], ['correct']),
                (1, ['ledger'], []), (1, ['ledger'], ['wrong'])):
            with self.subTest(dossiers=dossiers, families=families, variants=variants), self.assertRaises(ValueError):
                coverage.measure(1, dossiers, families, variants, self.root / 'invalid')

    def test_missing_sources_and_unknowns_never_pass(self):
        for q in self.result['claims']:
            if any(v.get('reason') in {'missing_source', 'missing_underlying_value', 'illegible_identity', 'unsampled_population'} for v in q['values']):
                self.assertFalse(q['extractable'])
        self.assertGreater(self.result['failure_counts']['missing_source'], 0)
        self.assertGreater(self.result['failure_counts']['illegible_identity'], 0)
        # Known evidence-state labels can be read even though the underlying value is unknown.
        self.assertTrue(any(q['kind'] == 'illegible_signature' and q['extractable'] for q in self.result['claims']))

    def test_row_and_column_selection_not_expected_value_search(self):
        text = coverage.generate.section('A', 'Rows', coverage.generate.table(('ID', 'First', 'Second'),
                    [('X01', 7, 9), ('X02', 9, 7)]))
        ex = self.extractor(text)
        need = coverage.Need('number', 'source.md', 'A', 7, coverage.NAT, 'X02', 1)
        result = ex.read(need, [('source.md', 'A')])
        self.assertEqual(result['reason'], 'value_mismatch')
        self.assertEqual(result['actual'], 9)
        self.assertEqual(ex.read(need, [])['reason'], 'uncited_dependency')

    def test_duplicate_tokens_select_exact_column(self):
        text = coverage.generate.section('A', 'Rows', coverage.generate.table(('ID', 'First', 'Second'), [('X01', 7, 7)]))
        ex = self.extractor(text)
        result = ex.read(coverage.Need('number', 'source.md', 'A', 7, coverage.NAT, 'X01', 2), [('source.md', 'A')])
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['selection'], [12, 13])

    def test_money_header_and_date_are_real_parser_calls(self):
        text = coverage.generate.section('A', 'Rows', coverage.generate.table(('ID', 'Amount, euro', 'Date'),
                                   [('X01', 50, '2032-02-29')]))
        ex = self.extractor(text)
        with patch.object(coverage.facts, 'make_fact', wraps=coverage.facts.make_fact) as make, \
             patch.object(coverage.facts, 'extract_facts', wraps=coverage.facts.extract_facts) as extract:
            amount = ex.read(coverage.Need('amount', 'source.md', 'A', 5000, coverage.EUR, 'X01', 1, unit='euro'), [('source.md', 'A')])
            when = ex.read(coverage.Need('date', 'source.md', 'A', date(2032, 2, 29), coverage.DATE, 'X01', 2), [('source.md', 'A')])
            self.assertTrue(amount['ok'], amount)
            self.assertTrue(when['ok'], when)
            self.assertGreaterEqual(make.call_count, 3)
            self.assertGreaterEqual(extract.call_count, 2)

    def test_ambiguous_prose_and_parser_failure(self):
        ex = self.extractor(coverage.generate.section('A', 'Values', 'There are 7 days and 7 days.'))
        need = coverage.Need('days', 'source.md', 'A', 7, pattern=r'(\d+ days)')
        self.assertEqual(ex.read(need, [('source.md', 'A')])['reason'], 'missing_or_ambiguous_token')
        ex = self.extractor(coverage.generate.section('A', 'Date', 'Date: 2032-02-30'))
        need = coverage.Need('date', 'source.md', 'A', date(2032, 2, 29), coverage.DATE, pattern=r'(\d{4}-\d{2}-\d{2})')
        self.assertEqual(ex.read(need, [('source.md', 'A')])['reason'], 'parser_rejection')

    def test_partly_successful_claim_is_not_extractable(self):
        q = next(q for q in self.result['claims'] if q['kind'] == 'units_in_service' and not q['extractable'])
        self.assertTrue(any(v['ok'] for v in q['values']))
        self.assertTrue(any(not v['ok'] for v in q['values']))
        self.assertFalse(q['extractable'])


if __name__ == '__main__':
    unittest.main()
