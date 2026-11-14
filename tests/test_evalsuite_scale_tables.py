"""The scaled `tables` dossier: N blocks of eight accounts in one merged payment register.

`Auditor` below reads only the merged Markdown sources and a report, as a clerk would, and works out
each claim's verdict twice: once by the conventions (the true figure) and once by the flawed reader's
program (the plain sum). It shares no code with the generator. Every fact of this family is closed, so
the "product of every open fact" of a dossier, or of a claim's own unit, is one world, and the verdict
is one comparison of the stated figure with the computed one.
"""
import collections
import datetime
import functools
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
import generate
import lure_tables

SUBTOTALS = ('Subtotal', 'Carried forward')


@functools.lru_cache(maxsize=None)
def built(seed, scale, index=1):
    return generate.build_case('tables', seed, index, scale)


def sources_of(case):
    return {name[8:]: text for name, text in case['files'].items() if name.startswith('sources/')}


def width_of(count):
    return max(2, len(str(count)))


class Auditor:
    """Reads the merged sources and a report; judges each claim by the conventions and by the plain sum."""

    CLAIM = re.compile(r'<a id="(C\d+)"></a>\n\*\*(C\d+)\.\*\* '
                       r'Net disbursements for the (.+) account during the reporting period '
                       r'(totalled|did not total) EUR (-?[\d,]+)\. Citations?: ([^\n]*)\n')

    def __init__(self, sources):
        self.sources = sources
        found = re.findall(r'The reporting period runs from (\d{4}-\d\d-\d\d) through (\d{4}-\d\d-\d\d), inclusive\.',
                           sources['covering.md'])
        assert len(found) == 1, 'the covering note must state the period once'
        self.start, self.end = map(datetime.date.fromisoformat, found[0])
        table = sources['register.md'].split('<a id="R-02"></a>', 1)[1]
        lines = [line for line in table.splitlines() if line.startswith('|')]
        cells = lambda line: [cell.strip() for cell in line.strip('|').split('|')]
        assert cells(lines[0]) == ['Entry', 'Date', 'Account', 'Payee', 'Amount EUR', 'Record']
        self.rows = [dict(zip(cells(lines[0]), cells(line))) for line in lines[2:]]
        self.replaced = {r['Record'][len('Reissue of '):] for r in self.rows if r['Record'].startswith('Reissue of ')}
        self.accounts = list(dict.fromkeys(r['Account'] for r in self.rows))
        self.true = self.total(False)
        self.plain = self.total(True)

    @staticmethod
    def date(text, plain):
        if '-' in text:
            return datetime.date.fromisoformat(text)
        first, second, year = map(int, text.split('/'))
        if plain:
            # The plain sum reads month/day first and falls back to day/month for a first number above 12.
            try:
                return datetime.date(year, first, second)
            except ValueError:
                pass
        return datetime.date(year, second, first)

    @staticmethod
    def amount(text, plain):
        # A leading minus sign is part of int(); brackets are negative by the conventions, positive to the plain sum.
        number = int(text.replace(',', '').strip('()'))
        return -number if not plain and text.startswith('(') else number

    def total(self, plain):
        """The conventions' figure, or the plain sum: every row, brackets as positive, month/day first."""
        result = {account: 0 for account in self.accounts}
        for r in self.rows:
            if not plain and (r['Record'] in SUBTOTALS or r['Entry'] in self.replaced):
                continue
            if self.start <= self.date(r['Date'], plain) <= self.end:
                result[r['Account']] += self.amount(r['Amount EUR'], plain)
        return result

    def markers(self):
        """The accounts that show a layout quirk, from what the rendered rows look like."""
        found = collections.defaultdict(set)
        for r in self.rows:
            on = lambda plain: self.start <= self.date(r['Date'], plain) <= self.end
            if r['Record'] in SUBTOTALS:
                found['subtotal'].add(r['Account'])
            if r['Amount EUR'].startswith('('):
                found['brackets'].add(r['Account'])
            if r['Record'].startswith('Reissue of '):
                found['reissue'].add(r['Account'])
            if on(False) != on(True):
                found['dates'].add(r['Account'])
        return found

    @classmethod
    def claims(cls, report):
        """(identifier, account, equal, figure, text) in the order of the report."""
        found = []
        for match in cls.CLAIM.finditer(report):
            assert match[1] == match[2], match[0]
            text = match[0].split('.** ', 1)[1].split(' Citation', 1)[0]
            found.append((match[1], match[3], match[4] == 'totalled', int(match[5].replace(',', '')), text))
        assert found and len(found) == report.count('<a id="C'), 'a claim of the report did not parse'
        return found

    def verdicts(self, account, equal, figure):
        """(verdict by the conventions, verdict of the plain sum, flawed reader's verdict where it differs)."""
        verdict = lambda totals: 'supported' if (totals[account] == figure) == equal else 'refuted'
        true, plain = verdict(self.true), verdict(self.plain)
        return true, plain, plain if plain != true else None


@functools.lru_cache(maxsize=None)
def auditor(seed, scale):
    return Auditor(sources_of(built(seed, scale)))


def block_of_accounts(case):
    """Account -> block number, from the key rows' claim texts (the report names no block)."""
    owner = {}
    for row in case['key']['expectations']:
        account = re.fullmatch(r'Net disbursements for the (.+) account during the reporting period .*', row['claim_text'])[1]
        assert owner.setdefault(account, row['block']) == row['block'], account
    return owner


def key_rows(case, variant):
    return {r['claim_id']: r for r in case['key']['expectations'] if r['variant'] == variant}


def check_claims(test, seed, scale):
    """Compare the key with the auditor's verdicts for every claim of both reports."""
    case, reader = built(seed, scale), auditor(seed, scale)
    for variant in ('correct', 'mixed'):
        claims = Auditor.claims(case['files'][f'report-{variant}.md'])
        rows = key_rows(case, variant)
        test.assertEqual([c[0] for c in claims], sorted(rows), (seed, scale, variant))
        for ident, account, equal, figure, text in claims:
            true, plain, echo = reader.verdicts(account, equal, figure)
            row = rows[ident]
            where = (seed, scale, variant, ident, text)
            test.assertEqual(row['claim_text'], text, where)
            test.assertEqual(row['semantic_state'], true, where)
            test.assertEqual(row['echo_state'], echo, where)
            test.assertNotIn('lure_states', row, where)
            test.assertEqual(row.get('echo_reader'), 'runs_naive_script' if echo else None, where)


class CountsTests(unittest.TestCase):
    def test_builder_contract_at_each_rung(self):
        self.assertEqual(lure_tables.MAX_SCALE, 30)
        for scale in (1, 2, 4, 12, 30):
            case = lure_tables.build_scaled(random.Random(scale), scale)
            reader = Auditor(case['sources'])
            self.assertEqual(case['reader'], 'runs_naive_script')
            self.assertEqual(len(case['blocks']), scale)
            seen = set()
            for block in case['blocks']:
                self.assertIsInstance(block['rng'], random.Random)
                self.assertEqual(len(block['slots']), 8)
                self.assertEqual(block['unknowns'], {})
                accounts = set(block['base'])
                self.assertEqual(len(accounts), 8)
                self.assertFalse(accounts & seen, 'an account in two blocks')
                seen |= accounts
                # The block's figures are the conventions' figures for its own accounts, and its told world is
                # the plain sum for exactly those accounts, absent when no quirk affects the block.
                self.assertEqual(block['base'], {a: reader.true[a] for a in accounts})
                affected = {a for a in accounts if reader.true[a] != reader.plain[a]}
                if affected:
                    self.assertEqual(block['told'], {'base': {a: reader.plain[a] for a in accounts}, 'unknowns': {}})
                else:
                    self.assertIsNone(block['told'])
            self.assertEqual(seen, set(reader.accounts))

    def test_scale_out_of_range_is_refused(self):
        for scale in (0, 31):
            with self.assertRaises(generate.GenerationError):
                lure_tables.build_scaled(random.Random(1), scale)

    def test_counts_identifiers_and_blocks(self):
        for scale in (4, 12, 30):
            for seed in range(1, 6):
                case, where = built(seed, scale), (seed, scale)
                total = 8 * scale
                width = width_of(total)
                expected = [f'C{n:0{width}d}' for n in range(1, total + 1)]
                self.assertEqual(case['key']['generator']['scale'], scale, where)
                self.assertEqual(case['key']['generator']['blocks'], scale, where)
                named = {}
                for variant in ('correct', 'mixed'):
                    claims = Auditor.claims(case['files'][f'report-{variant}.md'])
                    named[variant] = [c[0] for c in claims]
                    rows = [r for r in case['key']['expectations'] if r['variant'] == variant]
                    self.assertEqual([r['claim_id'] for r in rows], expected, where)
                    self.assertEqual(len(rows), total, where)
                    self.assertTrue(all(1 <= r['block'] <= scale for r in rows), where)
                    per_block = collections.Counter(r['block'] for r in rows)
                    self.assertEqual(per_block, {b: 8 for b in range(1, scale + 1)}, where)
                self.assertEqual(named['correct'], expected, where)
                self.assertEqual(named['correct'], named['mixed'], where)
                self.assertTrue(all(r['block'] for r in case['key']['expectations']), where)

    def test_each_account_is_claimed_once_per_report_and_stays_in_its_block(self):
        for scale in (4, 12, 30):
            for seed in range(1, 6):
                case, owner = built(seed, scale), {}
                for variant in ('correct', 'mixed'):
                    claims = Auditor.claims(case['files'][f'report-{variant}.md'])
                    self.assertEqual(len({c[1] for c in claims}), 8 * scale, (seed, scale))
                    for (ident, account, *_), row in zip(claims, case['key']['expectations'][
                            0 if variant == 'correct' else 8 * scale:]):
                        self.assertEqual(row['claim_id'], ident)
                        self.assertEqual(owner.setdefault(account, row['block']), row['block'], (seed, scale, account))
                self.assertEqual(set(owner), set(auditor(seed, scale).accounts))


class BruteForceTests(unittest.TestCase):
    """The key against an auditor that reads only the merged Markdown, at every rung of criterion 4."""

    def test_two_blocks_every_claim(self):
        for seed in range(1, 21):
            check_claims(self, seed, 2)

    def test_four_blocks_every_claim(self):
        for seed in range(1, 6):
            check_claims(self, seed, 4)

    def test_twelve_and_thirty_blocks_every_claim(self):
        # Every claim, not a sample: a defect confined to one block must not slip between the sampled claims.
        for scale in (12, 30):
            for seed in range(1, 4):
                check_claims(self, seed, scale)
                for variant in ('correct', 'mixed'):
                    self.assertEqual(len(Auditor.claims(built(seed, scale)['files'][f'report-{variant}.md'])), 8 * scale)

    def test_the_sample_reaches_lure_rows_and_every_wrong_figure(self):
        # The auditor's agreement would be hollow if it never met a claim the flawed reader gets wrong.
        lures, errors = 0, collections.Counter()
        for seed in range(1, 6):
            for row in built(seed, 4)['key']['expectations']:
                lures += row['echo_state'] is not None
                errors[row['error_type']] += 1
        self.assertGreater(lures, 20)
        self.assertGreater(errors['runs_naive_script'] + errors['denies_true_total'] + errors['transposed_digits'], 10)

    def test_a_block_has_a_quirk_marker_exactly_where_the_plain_sum_differs(self):
        # Every block is validated alone and again once merged; the register shows it.
        for scale in (4, 12, 30):
            for seed in range(1, 6):
                reader, case = auditor(seed, scale), built(seed, scale)
                marked = set().union(*reader.markers().values())
                differs = {a for a in reader.accounts if reader.true[a] != reader.plain[a]}
                self.assertEqual(marked, differs, (seed, scale))
                owner = block_of_accounts(case)
                per_block = collections.Counter(owner[a] for a in differs)
                self.assertTrue(set(per_block.values()) <= {1, 2}, (seed, scale, per_block))
                self.assertTrue(all(reader.true[a] > 0 for a in reader.accounts), (seed, scale))


class MergeTests(unittest.TestCase):
    def test_names_identifiers_and_dossier_level_facts(self):
        for scale in (4, 12, 30):
            for seed in range(1, 6):
                case, reader, where = built(seed, scale), auditor(seed, scale), (seed, scale)
                sources = sources_of(case)
                # No name is a prefix of another, so a match by prefix cannot mix two accounts up.
                names = reader.accounts
                self.assertEqual(len(names), 8 * scale, where)
                self.assertFalse([(a, b) for a in names for b in names if a != b and b.startswith(a)], where)
                # Row identifiers are unique and numbered in table order; a reissue replaces an earlier row
                # of its own account, so it was merged after that row.
                idents = [r['Entry'] for r in reader.rows]
                self.assertEqual(idents, [f'P{n:04d}' for n in range(1, len(idents) + 1)], where)
                by_id = {r['Entry']: (i, r) for i, r in enumerate(reader.rows)}
                for i, r in enumerate(reader.rows):
                    if r['Record'].startswith('Reissue of '):
                        j, original = by_id[r['Record'][len('Reissue of '):]]
                        self.assertLess(j, i, where)
                        self.assertEqual(original['Account'], r['Account'], where)
                    if r['Record'] in SUBTOTALS:
                        self.assertTrue(any(o['Account'] == r['Account'] and o['Date'] == r['Date'] and o['Record'] == 'Payment'
                                            for o in reader.rows[:i]), where)
                # Blocks are interleaved: the run of consecutive rows of one block is short.
                owner = block_of_accounts(case)
                runs = 1 + sum(owner[a['Account']] != owner[b['Account']] for a, b in zip(reader.rows, reader.rows[1:]))
                self.assertGreater(runs, 20 * scale, where)
                # One period, one year, one value for each boundary day, in every document.
                year = reader.start.year
                self.assertEqual((reader.start, reader.end), (datetime.date(year, 4, 1), datetime.date(year, 6, 30)), where)
                self.assertEqual({r['Date'][-4:] for r in reader.rows}, {str(year)}, where)
                for name, pattern in (('conventions.md', r'Issued 15 January (\d{4})\.'),
                                      ('covering.md', r'on 18 July (\d{4})\.'),
                                      ('period.md', r'19 July (\d{4})\.'),
                                      ('register.md', r'Export prepared on 17 July (\d{4}) ')):
                    self.assertEqual(re.findall(pattern, sources[name]), [str(year)], (where, name))
                boundary = [r for r in reader.rows if r['Date'] in (f'31/03/{year}', f'30/06/{year}')]
                self.assertGreater(len(boundary), 0, where)

    def test_one_register_and_the_unchanged_documents(self):
        case = built(1, 12)
        self.assertEqual(sorted(sources_of(case)), ['conventions.md', 'covering.md', 'period.md', 'register.md'])
        self.assertEqual(case['files']['sources/register.md'].count('<a id="R-02"></a>'), 1)
        one = sources_of(generate.build_case('tables', 1, 1, 1))
        for name in ('conventions.md', 'covering.md', 'period.md'):
            masked = lambda text: re.sub(r'\d{4}-\d\d-\d\d|\b20\d\d\b', '<date>', text)
            self.assertEqual(masked(sources_of(case)[name]), masked(one[name]), name)


class TrapShareTests(unittest.TestCase):
    @staticmethod
    def share(cases):
        lure = total = 0
        for case in cases:
            rows = case['key']['expectations']
            lure += sum(r['echo_state'] is not None for r in rows)
            total += len(rows)
        return lure / total

    def test_lure_share_at_scale_matches_scale_one(self):
        for scale, tolerance in ((4, 0.35), (12, 0.25), (30, 0.25)):
            at_scale = self.share(built(seed, scale) for seed in range(1, 26))
            one = self.share(built(seed, 1) for seed in range(1, 25 * scale + 1))
            self.assertLessEqual(abs(at_scale - one), tolerance * one, (scale, at_scale, one))

    def test_each_block_keeps_unaltered_unaffected_claims_of_both_polarities(self):
        """The controls of a block: its unaffected accounts (no layout quirk) whose claims the mixed report never
        alters, one that "totalled" and one that "did not total". Whichever claims the mixed report alters, in
        each block of each report both wordings must remain among the claims that are unaffected and unaltered,
        so a reader cannot take the wording for the sign of an error. Unaffected is read from the sources (the
        conventions' total equals the plain sum), and a claim of the mixed report is unaltered when it equals
        the claim in the same place of the correct report.

        This checks what a reader can see. It does not see which two accounts the generator designated as the
        controls, so a fault in the controls of a single block shows only in the dossiers where that block's
        other unaffected, unaltered claims happen to share one wording (about one dossier in ten); the 25 seeds,
        which the share test above already builds, are there for that."""
        groups = 0
        for scale in (4, 12, 30):
            for seed in range(1, 26):
                case, reader = built(seed, scale), auditor(seed, scale)
                owner = block_of_accounts(case)
                affected = {a for a in reader.accounts if reader.true[a] != reader.plain[a]}
                self.assertTrue(any(row['echo_state'] is not None for row in case['key']['expectations']),
                                (seed, scale, 'a dossier with no lure row at all'))
                right = {ident: text for ident, _, _, _, text in Auditor.claims(case['files']['report-correct.md'])}
                for variant in ('correct', 'mixed'):
                    wordings = collections.defaultdict(set)
                    for ident, account, equal, _, text in Auditor.claims(case['files'][f'report-{variant}.md']):
                        if account not in affected and text == right[ident]:
                            wordings[owner[account]].add(equal)
                    for block in range(1, scale + 1):
                        self.assertEqual(wordings[block], {True, False}, (seed, scale, variant, block))
                        groups += 1
        self.assertEqual(groups, 2 * 25 * (4 + 12 + 30))

    def test_no_dossier_has_a_trap_in_every_block_or_in_none_at_large_scale(self):
        # Per-block draws: three blocks in ten are plain, so a rung of 30 blocks holds about nine of them.
        for seed in range(1, 6):
            rows = built(seed, 30)['key']['expectations']
            with_lure = {r['block'] for r in rows if r['echo_state'] is not None}
            self.assertTrue(10 <= len(with_lure) <= 29, (seed, len(with_lure)))


class DeterminismTests(unittest.TestCase):
    def test_hash_seeds_give_identical_trees(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='scale-tables-', dir=ROOT / 'tmp') as temp:
            trees = []
            for hash_seed in ('1', '2'):
                run = Path(temp) / hash_seed
                run.mkdir()
                done = subprocess.run(
                    [sys.executable, str(ROOT / 'evalsuite/generate.py'), '--fixtures', str(run / 'fixtures'),
                     '--keys', str(run / 'keys'), '--seed', '3', '--dossiers', '2', '--families', 'tables',
                     '--scale', '12'],
                    env=dict(os.environ, PYTHONHASHSEED=hash_seed), capture_output=True, text=True, cwd=ROOT)
                self.assertEqual(done.returncode, 0, done.stderr)
                trees.append({str(p.relative_to(run)): p.read_bytes() for p in sorted(run.rglob('*')) if p.is_file()})
            self.assertEqual(sorted(trees[0]), sorted(trees[1]))
            self.assertGreaterEqual(len(trees[0]), 11)
            self.assertTrue(any(name.endswith('keys.generation.json') for name in trees[0]))
            for name in trees[0]:
                self.assertEqual(trees[0][name], trees[1][name], name)

    def test_same_seed_same_dossier(self):
        again = generate.build_case('tables', 4, 2, 12)
        self.assertEqual(again['files'], generate.build_case('tables', 4, 2, 12)['files'])
        self.assertNotEqual(again['files']['sources/register.md'], built(4, 12)['files']['sources/register.md'])


if __name__ == '__main__':
    unittest.main()
