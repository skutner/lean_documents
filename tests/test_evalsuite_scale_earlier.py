"""The scaled `earlier` family: many blocks of eight programmes in one dossier.

A scale-N dossier holds N blocks. Each block draws what a scale-1 dossier draws (whether a review memo
exists, whether it misleads, which points it changes, the claim kinds and every figure); the programmes
are named by a place and a service; every record is numbered in one dossier-wide shuffle; and one review
memo covers the programmes of the blocks that drew a memo. The flawed reader takes that memo at its word
for the programmes it covers and reads the records for the others.

`Reading` below consumes only the merged Markdown sources and the report text. It never imports the
family's facts, predicates or constants, and it does not use the generator's judging code. It decides each
claim's verdict from the records and the verdict of the flawed reader from the memo, and both must equal
what the key says. The family has no open facts (`unknowns` is empty in every block), so the "product of
the open facts" of a claim's unit has exactly one world and the verdict is a plain comparison.

Run time (observed on the machine that wrote it, Python 3.9.6): about 2 s for this module. Generating a
scale-30 dossier takes about 15 ms; the 95 scaled dossiers and 1,150 scale-1 dossiers that the criteria
need are built once and cached, and one test runs the command line twice for the hash-seed check.
"""
import collections
import functools
from pathlib import Path
import os
import random
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
import generate
import lure_earlier

PREPARATION = ('The findings below state, for each programme I reviewed, whether its Q3 disbursements '
               'reconcile to its authorised schedule.')
SCALES = (4, 12, 30)
KINDS = ('programme_variance', 'programme_total')


@functools.lru_cache(maxsize=None)
def dossier(scale, seed):
    if scale == 1:
        return generate.build_case('earlier', seed, 1)
    return generate.build_case('earlier', seed, 1, scale=scale)


def sources_of(built):
    return {name[8:]: text for name, text in built['files'].items() if name.startswith('sources/')}


def sections(text):
    parts = re.split(r'<a id="([^"]+)"></a>\n', text)
    return dict(zip(parts[1::2], parts[2::2]))


def identifiers(width, count):
    return [f'C{number:0{width}d}' for number in range(1, count + 1)]


CLAIM = re.compile(r'<a id="(C\d+)"></a>\n\*\*(C\d+)\.\*\* (.*?) (Citations?: [^\n]*)\n')
CITATION = re.compile(r'Citations: procedure (P-\d+), (P-\d+); record-(\d+) (R\d+-S), (R\d+-P), (R\d+-A)\.')
VARIANCE = re.compile(r'The Q3 disbursement variance for the (.+) programme is (\d+) euros\.')
TOTAL = re.compile(r'The authorised schedule total for the (.+) programme is (\d+) euros\.')
DATE = re.compile(r'\d{4}-\d\d-\d\d')


def report_claims(text):
    """(identifier, claim text, citation line) of every claim, read from the report text alone."""
    found = CLAIM.findall(text)
    assert all(first == second for first, second, _, _ in found)
    return [(first, claim, citation) for first, _, claim, citation in found]


class Reading:
    """The arithmetic of the records and, separately, what the memo says about the programmes it covers."""

    def __init__(self, sources):
        self.records, self.paid, self.total, self.issues, self.initial = {}, {}, {}, {}, {}
        self.truth, self.original, self.digits = {}, {}, {}
        for filename, text in sources.items():
            match = re.fullmatch(r'record-(\d+)\.md', filename)
            if not match:
                continue
            title = re.fullmatch(r'# (.+) programme account', text.splitlines()[0])
            name, digits = title[1], match[1]
            assert name not in self.records, name
            parts = sections(text)
            assert list(parts) == [f'R{digits}-S', f'R{digits}-P', f'R{digits}-A'], (filename, list(parts))
            first = re.search(r'was issue ([AB]), with a quarter total of (\d+) euros', parts[f'R{digits}-S'])
            current = re.search(r'The authorised quarter total in issue ([AB]) is (\d+) euros', parts[f'R{digits}-A'])
            operative = re.search(r'Issue ([AB]) is operative for Q3 and replaces issue ([AB])', parts[f'R{digits}-A'])
            assert first and current and operative
            assert current[1] == operative[1] and first[1] == operative[2] and first[1] != current[1]
            payments = re.findall(r'The bank settled the (July|August|September) payment of (\d+) euros',
                                  parts[f'R{digits}-P'])
            assert [month for month, _ in payments] == ['July', 'August', 'September']
            self.records[name], self.digits[name] = int(digits), digits
            self.paid[name] = sum(int(value) for _, value in payments)
            self.total[name], self.initial[name] = int(current[2]), int(first[2])
            self.issues[name] = {first[1]: int(first[2]), current[1]: int(current[2])}
            self.truth[name] = abs(self.paid[name] - self.total[name])
            self.original[name] = abs(self.paid[name] - self.initial[name])
        assert sorted(self.records.values()) == list(range(1, len(self.records) + 1))
        assert len({len(digits) for digits in self.digits.values()}) == 1
        self.memo, self.memo_total, self.memo_date = {}, {}, None
        if 'review.md' in sources:
            self.read_memo(sources['review.md'])

    def read_memo(self, memo):
        assert memo.startswith('# Accounts review\n')
        parts = sections(memo)
        width = len(next(iter(self.digits.values())))
        anchors = list(parts)
        assert anchors[0] == f'V-{1:0{width}d}'
        assert PREPARATION in parts[anchors[0]]
        dates = set(DATE.findall(memo))
        assert len(dates) == 1, dates
        self.memo_date = dates.pop()
        previous = 0
        for anchor in anchors[1:]:
            entry = re.fullmatch(r'## ' + re.escape(anchor) + r' — .+\n'
                                 r'(No variance found\.|One variance seen\.) Q3 disbursements for the (.+?) programme: '
                                 r'(\d+) euros against (\d+) euros in schedule issue ([AB])\. (.+)\n+', parts[anchor])
            assert entry, parts[anchor]
            finding, name, paid, scheduled, issue, tail = entry.groups()
            assert name not in self.memo, name
            assert parts[anchor].startswith(f'## {anchor} — {name}\n'), parts[anchor]
            assert anchor == f'V-{self.records[name] + 1:0{width}d}', (anchor, name)
            assert self.records[name] > previous, 'memo sections must follow the record order'
            previous = self.records[name]
            assert tail.count(f'issue {issue} attachment') == 1, tail
            assert int(paid) == self.paid[name]
            assert int(scheduled) == self.issues[name][issue]
            assert (finding == 'No variance found.') == (int(paid) == int(scheduled))
            self.memo[name] = abs(int(paid) - int(scheduled))
            self.memo_total[name] = int(scheduled)

    @staticmethod
    def parse(text):
        match = VARIANCE.fullmatch(text)
        if match:
            return match[1], 'variance', int(match[2])
        match = TOTAL.fullmatch(text)
        assert match, text
        return match[1], 'total', int(match[2])

    def verdict(self, text):
        name, kind, value = self.parse(text)
        return 'supported' if (self.truth if kind == 'variance' else self.total)[name] == value else 'refuted'

    def echo(self, text):
        """The verdict of the reader who takes the memo at its word where it speaks, if it differs."""
        name, kind, value = self.parse(text)
        if name in self.memo:
            believed_value = (self.memo if kind == 'variance' else self.memo_total)[name]
        else:
            believed_value = (self.truth if kind == 'variance' else self.total)[name]
        believed = 'supported' if believed_value == value else 'refuted'
        return believed if believed != self.verdict(text) else None

    def misjudged(self, name):
        return name in self.memo_total and self.memo_total[name] != self.total[name]

    def original_value(self, kind, name):
        return self.original[name] if kind == 'variance' else self.initial[name]


def key_rows(built):
    return {(row['variant'], row['claim_id']): row for row in built['key']['expectations']}


def check_cites(test, reading, sources, name, citation):
    match = CITATION.fullmatch(citation)
    test.assertTrue(match, citation)
    first, second, digits = match[1], match[2], match[3]
    test.assertEqual((first, second), ('P-02', 'P-03'))
    test.assertIn(f'<a id="{first}"></a>', sources['procedure.md'])
    test.assertIn(f'<a id="{second}"></a>', sources['procedure.md'])
    test.assertIn(f'record-{digits}.md', sources)
    test.assertEqual(digits, reading.digits[name])
    test.assertEqual(match.groups()[3:], tuple(f'R{digits}-{part}' for part in 'SPA'))
    for anchor in match.groups()[3:]:
        test.assertIn(f'<a id="{anchor}"></a>', sources[f'record-{digits}.md'])


class ScaledEarlierTests(unittest.TestCase):
    def check_dossier(self, scale, seed):
        """Every claim of both reports."""
        built = dossier(scale, seed)
        sources = sources_of(built)
        reading = Reading(sources)
        rows = key_rows(built)
        for variant in ('correct', 'mixed'):
            claims = report_claims(built['files'][f'report-{variant}.md'])
            self.assertEqual(len(claims), 8 * scale, (scale, seed, variant))
            for ident, text, citation in claims:
                row = rows[(variant, ident)]
                label = (scale, seed, variant, ident)
                self.assertEqual(row['claim_text'], text, label)
                self.assertEqual(reading.verdict(text), row['semantic_state'], label)
                self.assertEqual(reading.echo(text), row['echo_state'], label)
                name, kind, value = reading.parse(text)
                check_cites(self, reading, sources, name, citation)
                if row['echo_state']:
                    self.assertEqual(row['echo_reader'], 'follows_earlier_review', label)
                self.assertEqual(row['semantic_state'] != 'supported', row['error_type'] is not None, label)
                if row['error_type']:
                    # A wrong claim states what the replaced issue gives; the memo is the cause where it used it.
                    self.assertEqual(value, reading.original_value(kind, name), label)
                    self.assertEqual(row['error_type'], 'follows_earlier_review' if reading.misjudged(name)
                                     else 'uses_superseded_schedule', label)
        return reading

    # ---------- criterion 3: counts ----------

    def test_counts_identifiers_and_blocks_per_rung(self):
        for scale in SCALES:
            width = max(2, len(str(8 * scale)))
            for seed in range(1, 6):
                built = dossier(scale, seed)
                label = (scale, seed)
                self.assertEqual(built['dossier'], f'g{seed}-001-earlier-x{scale}')
                self.assertEqual(built['key']['generator']['scale'], scale, label)
                self.assertEqual(built['key']['generator']['blocks'], scale, label)
                expected = identifiers(width, 8 * scale)
                reports = {variant: report_claims(built['files'][f'report-{variant}.md'])
                           for variant in ('correct', 'mixed')}
                for variant, claims in reports.items():
                    self.assertEqual([ident for ident, _, _ in claims], expected, (label, variant))
                    rows = [row for row in built['key']['expectations'] if row['variant'] == variant]
                    self.assertEqual([row['claim_id'] for row in rows], expected, (label, variant))
                    self.assertEqual(collections.Counter(row['block'] for row in rows),
                                     {block: 8 for block in range(1, scale + 1)}, (label, variant))
                self.assertEqual({(row['claim_id'], row['block']) for row in built['key']['expectations']
                                  if row['variant'] == 'correct'},
                                 {(row['claim_id'], row['block']) for row in built['key']['expectations']
                                  if row['variant'] == 'mixed'}, label)
                self.assertEqual(len(built['key']['expectations']), 16 * scale, label)
                self.assertEqual(len([name for name in sources_of(built) if name.startswith('record-')]),
                                 8 * scale, label)
                self.assertEqual(sorted(name for name in sources_of(built) if not name.startswith('record-')),
                                 ['procedure.md'] + (['review.md'] if 'review.md' in sources_of(built) else []))

    def test_builder_contract(self):
        self.assertEqual(lure_earlier.MAX_SCALE, 30)
        case = lure_earlier.build_scaled(random.Random(5), 4)
        self.assertEqual(len(case['blocks']), 4)
        self.assertEqual(case['reader'], 'follows_earlier_review')
        for block in case['blocks']:
            self.assertEqual(len(block['slots']), 8)
            self.assertEqual(block['unknowns'], {})
            self.assertIsInstance(block['rng'], random.Random)
            self.assertTrue(block['told'] is None or set(block['told']) == {'base', 'unknowns'})
        for scale in (0, 31):
            with self.assertRaises(generate.GenerationError):
                lure_earlier.build_scaled(random.Random(5), scale)

    # ---------- criterion 4: a second derivation from the merged Markdown ----------

    def test_every_claim_at_scale_two_for_twenty_seeds(self):
        for seed in range(1, 21):
            self.check_dossier(2, seed)

    def test_every_claim_at_scale_four(self):
        for seed in range(1, 6):
            self.check_dossier(4, seed)

    def test_every_claim_at_scales_twelve_and_thirty(self):
        # Every claim, not a sample: a defect confined to one block must not slip between the sampled claims.
        for scale in (12, 30):
            for seed in range(1, 4):
                self.check_dossier(scale, seed)

    def test_the_second_derivation_sees_a_memo_that_covers_the_wrong_block(self):
        """A memo entry, with a wrong finding, for a programme of a block that drew no memo and has no `told`:
        the flawed reader would follow it and the key says nothing, so the second derivation must disagree."""
        for seed in range(1, 26):
            built = dossier(4, seed)
            sources = sources_of(built)
            reading = Reading(sources)
            uncovered = [row for row in built['key']['expectations']
                         if row['variant'] == 'correct' and reading.parse(row['claim_text'])[0] not in reading.memo]
            if not uncovered or 'review.md' not in sources:
                continue
            name = reading.parse(uncovered[0]['claim_text'])[0]
            width = len(reading.digits[name])
            issue = next(letter for letter, value in reading.issues[name].items() if value == reading.initial[name])
            number = reading.records[name]
            finding = 'No variance found.' if reading.paid[name] == reading.initial[name] else 'One variance seen.'
            entry = (f'<a id="V-{number + 1:0{width}d}"></a>\n## V-{number + 1:0{width}d} — {name}\n'
                     f'{finding} Q3 disbursements for the {name} programme: {reading.paid[name]} euros '
                     f'against {reading.initial[name]} euros in schedule issue {issue}. I took the issue {issue} '
                     'attachment from the programme correspondence and the three bank settlements listed in the '
                     'payment folio.\n')
            pieces = re.split(r'(?=<a id=")', sources['review.md'])
            head, found = pieces[0] + pieces[1], pieces[2:] + [entry]
            found.sort(key=lambda piece: int(re.match(r'<a id="V-(\d+)"', piece)[1]))
            mutated = dict(sources, **{'review.md': head + '\n'.join(found)})
            self.assertEqual(Reading(sources).memo, reading.memo)
            changed = Reading(mutated)
            self.assertEqual(changed.memo[name], abs(reading.paid[name] - reading.initial[name]))
            mine = changed.echo(uncovered[0]['claim_text'])
            self.assertNotEqual(mine, uncovered[0]['echo_state'])
            return
        self.fail('no dossier had a block without a memo next to one with a memo')

    # ---------- criterion 5: merge integrity ----------

    def test_names_numbers_dates_and_citations_across_blocks(self):
        for scale in SCALES:
            for seed in range(1, 6):
                built = dossier(scale, seed)
                label = (scale, seed)
                sources = sources_of(built)
                reading = Reading(sources)
                names = sorted(reading.records)
                self.assertEqual(len(names), 8 * scale, label)
                for first in names:
                    self.assertEqual([other for other in names if other != first and other.startswith(first)], [],
                                     (label, first))
                    # a name is two tokens, so a reader matching "<name> programme" or a whole line cannot confuse two
                    self.assertEqual(len(first.split(' ')) >= 2, True, first)
                blocks = collections.defaultdict(list)
                for row in built['key']['expectations']:
                    if row['variant'] == 'correct':
                        blocks[row['block']].append(row)
                self.assertEqual(sorted(blocks), list(range(1, scale + 1)), label)
                names_of, numbers_of = {}, {}
                for block, rows in blocks.items():
                    names_of[block] = [reading.parse(row['claim_text'])[0] for row in rows]
                    numbers_of[block] = sorted(reading.records[name] for name in names_of[block])
                    self.assertEqual(len(set(names_of[block])), 8, (label, block))
                    self.assertNotEqual(numbers_of[block], list(range(numbers_of[block][0], numbers_of[block][0] + 8)),
                                        (label, block, 'a block must not be one run of record numbers'))
                every_name = [name for names_ in names_of.values() for name in names_]
                every_number = [number for numbers in numbers_of.values() for number in numbers]
                self.assertEqual(len(set(every_name)), 8 * scale, label)
                self.assertEqual(sorted(every_number), list(range(1, 8 * scale + 1)), label)
                # a block holds the services drawn for it; two blocks share no name or record
                for first in range(1, scale + 1):
                    for second in range(first + 1, scale + 1):
                        self.assertFalse(set(names_of[first]) & set(names_of[second]), (label, first, second))
                        self.assertFalse(set(numbers_of[first]) & set(numbers_of[second]), (label, first, second))
                for variant in ('correct', 'mixed'):
                    claims = report_claims(built['files'][f'report-{variant}.md'])
                    for _, text, citation in claims:
                        check_cites(self, reading, sources, reading.parse(text)[0], citation)
                    self.assertEqual(len({reading.parse(text)[0] for _, text, _ in claims}), 8 * scale)
                # the memo: one document, one date, later than every other date, and a block is covered whole or not at all
                covered = {block: [name in reading.memo for name in names_] for block, names_ in names_of.items()}
                self.assertTrue(all(len(set(flags)) == 1 for flags in covered.values()), (label, covered))
                self.assertEqual('review.md' in sources, any(flags[0] for flags in covered.values()), label)
                if 'review.md' in sources:
                    other_dates = [day for name, text in sources.items() if name != 'review.md'
                                   for day in DATE.findall(text)]
                    self.assertGreater(reading.memo_date, max(other_dates), label)
                    self.assertEqual(len(reading.memo), 8 * sum(flags[0] for flags in covered.values()), label)

    # ---------- criterion 6: the trap share ----------

    @staticmethod
    def lure_share(built_cases):
        rows = [row for built in built_cases for row in built['key']['expectations']]
        return sum(row['echo_state'] is not None for row in rows) / len(rows)

    def test_lure_share_matches_scale_one(self):
        for scale, tolerance in ((4, 0.35), (12, 0.25), (30, 0.25)):
            base = self.lure_share([dossier(1, seed) for seed in range(1, 25 * scale + 1)])
            scaled = self.lure_share([dossier(scale, seed) for seed in range(1, 26)])
            self.assertGreater(base, 0.05)
            self.assertLessEqual(abs(scaled / base - 1), tolerance, (scale, base, scaled))

    def test_every_lure_has_a_same_kind_row_without_one_in_its_block(self):
        lures = 0
        for scale in SCALES:
            for seed in range(1, 26):
                groups = collections.defaultdict(list)
                for row in dossier(scale, seed)['key']['expectations']:
                    groups[(row['block'], row['variant'], row['claim_kind'])].append(row['echo_state'] is not None)
                for key, flags in groups.items():
                    self.assertIn(key[2], KINDS)
                    self.assertEqual(len(flags), 4, key)
                    lures += sum(flags)
                    if any(flags):
                        self.assertFalse(all(flags), (scale, seed, key))
        self.assertGreater(lures, 0)

    def test_blocks_draw_their_memo_and_trap_independently(self):
        """Over 25 scale-30 dossiers, memo coverage and wrong memos are neither all nor none of the blocks."""
        covered = misleading = total = 0
        for seed in range(1, 26):
            built = dossier(30, seed)
            reading = Reading(sources_of(built))
            by_block = collections.defaultdict(list)
            for row in built['key']['expectations']:
                if row['variant'] == 'correct':
                    by_block[row['block']].append(reading.parse(row['claim_text'])[0])
            for names in by_block.values():
                total += 1
                covered += names[0] in reading.memo
                misleading += any(reading.misjudged(name) for name in names)
        self.assertEqual(total, 750)
        self.assertTrue(0.75 < covered / total < 0.95, covered / total)
        self.assertTrue(0.55 < misleading / total < 0.80, misleading / total)

    # ---------- criterion 7: determinism ----------

    def test_two_hash_seeds_give_identical_trees(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='earlier-scale-tests-', dir=ROOT / 'tmp') as temporary:
            trees = []
            for hash_seed in ('1', '2'):
                root = Path(temporary) / hash_seed
                root.mkdir()
                result = subprocess.run(
                    [sys.executable, str(ROOT / 'evalsuite/generate.py'), '--fixtures', str(root / 'fixtures'),
                     '--keys', str(root / 'keys'), '--seed', '3', '--dossiers', '2', '--families', 'earlier',
                     '--scale', '12'],
                    env=dict(os.environ, PYTHONHASHSEED=hash_seed), capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                trees.append({str(path.relative_to(root)): path.read_bytes()
                              for path in sorted(root.rglob('*')) if path.is_file()})
            self.assertEqual(trees[0].keys(), trees[1].keys())
            self.assertEqual(trees[0], trees[1])
            self.assertTrue(any(name.endswith('earlier-x12.json') for name in trees[0]))
            self.assertTrue(any(name == 'keys.generation.json' for name in trees[0]))


if __name__ == '__main__':
    unittest.main()
