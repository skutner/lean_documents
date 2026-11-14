"""Checks the scaled builder of the ledger family (`evalsuite/scaled_ledger.py`, `--scale N`).

A scale-N ledger dossier is N batches of the scale-1 dossier in one set of documents. The checks
below are the ledger rows of the size-ladder specification:

* counts: a report holds the sum of the batches' slots as claims, numbered without gaps, in both
  reports, and every key row names its batch;
* verdicts: a parser written apart from the generator reads only the merged Markdown and a report,
  and decides every claim by trying every combination of the open facts. At N=2 it tries the product
  of the open facts of the whole dossier, so a claim that read another batch's facts would show; at
  larger N it tries the open facts of the claim's own batch, for every claim of both reports at N=4,
  N=12 and N=30 (seeds 1 to 3 at the two larger rungs). At those rungs it also counts the combinations
  it tried and the ones in which the claim holds, and these two counts must equal the key's
  `derivation.worlds` and `derivation.worlds_where_true`;
* merge integrity: no entry, batch or annex is named in two batches, no name is the start of another,
  and the audit cutoff stands once, with one value;
* determinism: another PYTHONHASHSEED gives the same tree.

The parser shares no code with the generator. Like the scale-1 parser in test_evalsuite_generate.py it
shares the reading of the rule sentences with it, which only human review of those sentences can check.
It does not read the method text (sources/method.md) at all. Of its three sections, only the scope
sentence of M-01 is asserted, by a test of its own below; no test checks M-02 (corrections) or M-03
(approvals), so the parser's reading of those rules is checked by human review alone.
"""
import datetime
import functools
import hashlib
import itertools
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
import generate  # noqa: E402
import scaled_ledger  # noqa: E402

TEST_ROOT = ROOT / 'tmp'
TEST_ROOT.mkdir(exist_ok=True)
RUNGS = (4, 12, 30)


@functools.lru_cache(maxsize=None)
def case(seed, scale):
    return generate.build_case('ledger', seed, 1, scale)


def source_texts(built):
    return {name[len('sources/'):]: text for name, text in built['files'].items() if name.startswith('sources/')}


def key_rows(built, variant):
    return [row for row in built['key']['expectations'] if row['variant'] == variant]


def sections(text):
    parts = re.split(r'<a id="([^"]+)"></a>\n', text)
    return dict(zip(parts[1::2], parts[2::2]))


def rows(body):
    lines = [line for line in body.splitlines() if line.startswith('|')]
    if not lines:
        return []
    cells = lambda line: [cell.strip() for cell in line.strip('|').split('|')]  # noqa: E731
    return [dict(zip(cells(lines[0]), cells(line))) for line in lines[2:]]


def report_claims(text):
    """{identifier: statement} of a report, in order."""
    found = re.findall(r'<a id="(C\d+)"></a>\n\*\*C\d+\.\*\* (.+?) Citations?: ', text)
    return dict(found)


# ---------- The parser: the merged Markdown and one claim in, a verdict out ----------

class Batch:
    """What the documents say about one batch, and the facts they leave open."""

    def __init__(self, name, population, budget, table, corrected, annex):
        self.name, self.population, self.budget = name, population, budget
        self.paid = [r for r in table if r['Status'] == 'Paid']
        amount = lambda r: corrected.get(r['Entry'], int(r['Ledger amount, euro cents']))  # noqa: E731
        self.amount = {r['Entry']: amount(r) for r in self.paid}
        self.total = sum(self.amount.values())
        status = lambda r: r['Required approval at cutoff']  # noqa: E731
        self.absent = [r['Entry'] for r in self.paid if status(r) == 'Absent']
        self.sheetless = [r['Entry'] for r in self.paid if status(r) == 'Approval sheet not in packet']
        self.present = sum(status(r) == 'Present' for r in self.paid)
        self.payment = {r['Entry']: (r['Payee'], r['Payment date']) for r in table}
        self.absent_amount = sum(self.amount[e] for e in self.absent)
        self.sheetless_amount = [self.amount[e] for e in self.sheetless]
        self.annex_supplied, self.annex_says, self.annex_entry = annex
        said = (self.annex_says,) if self.annex_supplied else ('obtained', 'not_obtained', 'nothing')
        # One value for each missing sheet (present or not), one for the unsampled entries, one for the annex.
        self.domains = [(True, False)] * len(self.sheetless) + [tuple(range(population - len(self.paid) + 1)), said]

    def lacking(self, world, at):
        k = len(self.sheetless)
        return len(self.absent) + sum(not present for present in world[at:at + k])

    def lacking_amount(self, world, at):
        k = len(self.sheetless)
        return self.absent_amount + sum(a for a, present in zip(self.sheetless_amount, world[at:at + k]) if not present)

    def population_lacking(self, world, at):
        return self.lacking(world, at) + world[at + len(self.sheetless)]

    def annex_value(self, world, at):
        return world[at + len(self.sheetless) + 1]


def read_batches(sources):
    book, notes = sections(sources['ledger.md']), sections(sources['corrections.md'])
    populations = {r['Batch']: int(r['Population entries']) for r in rows(book['L-01'])}
    budgets = {r['Batch']: int(r['Comparison budget, euro cents']) for r in rows(book['L-03'])}
    corrected = {r['Entry']: int(r['Corrected amount, euro cents']) for r in rows(notes['K-01'])}
    table = {}
    for r in rows(book['L-02']):
        table.setdefault(r['Batch'], []).append(r)
    annexes = {}
    for body in sections(sources['annex.md']).values():
        name = re.search(r'— Annex (B\d+)\n', body)[1]
        supplied = 'is included in the supplied packet' in body
        says = ('obtained' if 'had been obtained, on' in body else 'not_obtained' if 'had still not been obtained' in body
                else None)
        entry = re.search(r'for entry (P\d+)', body)
        annexes[name] = (supplied, says, entry[1] if entry else None)
    assert sorted(populations) == sorted(budgets) == sorted(table) == sorted(annexes), 'the batches differ between documents'
    return {name: Batch(name, populations[name], budgets[name], table[name], corrected, annexes[name])
            for name in sorted(populations)}


def number(value, exact):
    return (lambda found: found == int(value)) if exact else (lambda found: found >= int(value))


NAME = r'(?P<batch>B\d+)'
SHAPES = (
    ('total', rf'The sampled payments of batch {NAME} that are not voided total exactly (?P<value>\d+) euro cents\.'),
    ('over', rf'The sampled payments of batch {NAME} that are not voided exceed the comparison budget of batch '
             r'(?P<again>B\d+) by exactly (?P<value>\d+) euro cents\.'),
    ('under', rf'The comparison budget of batch {NAME} exceeds the sampled payments of batch (?P<again>B\d+) that are '
              r'not voided by exactly (?P<value>\d+) euro cents\.'),
    ('largest', rf'The largest sampled payment of batch {NAME} that is not voided is entry (?P<entry>P\d+), '
                r'at (?P<value>\d+) euro cents\.'),
    ('count', rf'(?P<lead>Exactly|At least) (?P<value>\d+) of the sampled entries of batch {NAME} that are not voided '
              r'lacked the required approval at the cutoff\.'),
    ('amount', rf'The sampled payments of batch {NAME} that are not voided and lacked the required approval total '
               r'(?P<lead>exactly|at least) (?P<value>\d+) euro cents\.'),
    ('present', r'The table in L-02 shows the required approval as “Present” for exactly (?P<value>\d+) of the '
                rf'(?P<sampled>\d+) sampled entries of batch {NAME} that are not voided\.'),
    ('population', rf'(?P<lead>Exactly|At least) (?P<value>\d+) of the (?P<population>\d+) population entries of batch '
                   rf'{NAME} lacked the required approval at the cutoff\.'),
    ('lookup', r'Entry (?P<entry>P\d+) was paid to (?P<payee>.+) on (?P<date>\d{4}-\d\d-\d\d)\.'),
    ('listed', rf'Annex {NAME} is listed in the packet index but is not included in the supplied packet\.'),
    ('obtained', rf'Annex {NAME} confirms that the approval missing for entry (?P<entry>P\d+) was obtained after '
                 r'the cutoff\.'),
    ('still_open', rf'Annex {NAME} records that the approval missing for entry (?P<entry>P\d+) had still not been '
                   r'obtained when the annex was closed\.'))


def read_claim(text, batches):
    """(batch, test) for a statement: `test(world, at)` is true when the statement holds in the world whose
    values for this batch start at index `at`. None when the statement fits no known shape."""
    for shape, pattern in SHAPES:
        found = re.fullmatch(pattern, text)
        if not found:
            continue
        g = found.groupdict()
        if shape == 'lookup':
            owner = next((b for b in batches.values() if g['entry'] in b.payment), None)
            if owner is None:
                return None
            return owner, lambda w, at: owner.payment[g['entry']] == (g['payee'], g['date'])
        b = batches[g['batch']]
        if shape == 'total':
            return b, lambda w, at: b.total == int(g['value'])
        if shape in ('over', 'under'):
            assert g['batch'] == g['again'], text
            gap = b.total - b.budget if shape == 'over' else b.budget - b.total
            return b, lambda w, at: gap == int(g['value'])
        if shape == 'largest':
            top = max(b.amount, key=b.amount.get)
            return b, lambda w, at: (top, b.amount[top]) == (g['entry'], int(g['value']))
        if shape == 'count':
            ok = number(g['value'], g['lead'] == 'Exactly')
            return b, lambda w, at: ok(b.lacking(w, at))
        if shape == 'amount':
            ok = number(g['value'], g['lead'] == 'exactly')
            return b, lambda w, at: ok(b.lacking_amount(w, at))
        if shape == 'present':
            same = b.present == int(g['value']) and len(b.paid) == int(g['sampled'])
            return b, lambda w, at: same
        if shape == 'population':
            ok = number(g['value'], g['lead'] == 'Exactly')
            same = int(g['population']) == b.population
            return b, lambda w, at: same and ok(b.population_lacking(w, at))
        if shape == 'listed':
            return b, lambda w, at: not b.annex_supplied
        # The two statements about what the annex says name the entry whose approval it concerns.
        named = g['entry'] in b.absent and (not b.annex_supplied or g['entry'] == b.annex_entry)
        wanted = 'obtained' if shape == 'obtained' else 'not_obtained'
        return b, lambda w, at: named and b.annex_value(w, at) == wanted
    return None


def verdict(test, owner, batches, whole):
    """`(state, combinations tried, combinations in which the claim holds)`, where the state is supported,
    refuted or undetermined, from every combination of the open facts. With `whole`, a combination holds a
    value for every open fact of every batch; otherwise only for the claim's own batch."""
    scope = list(batches.values()) if whole else [owner]
    at, start = 0, {}
    for b in scope:
        start[b.name] = at
        at += len(b.domains)
    index = start[owner.name]
    domains = [d for b in scope for d in b.domains]
    true = size = 0
    for world in itertools.product(*domains):
        size += 1
        true += bool(test(world, index))
    return ('supported' if true == size else 'refuted' if true == 0 else 'undetermined'), size, true


def disagreements(built, whole, counts=False):
    """Claims whose parsed verdict differs from the key, as (variant, id, statement, parsed, key); and how many
    claims were decided. With `counts`, the compared value is the verdict with the number of combinations
    tried and the number in which the claim holds, against the key's `semantic_state`, `derivation.worlds`
    and `derivation.worlds_where_true`; the key counts the claim's own batch, so `counts` is for
    `whole=False`."""
    assert not (whole and counts), 'the key counts the claim\'s own batch'
    batches = read_batches(source_texts(built))
    wrong, decided = [], 0
    for variant in ('correct', 'mixed'):
        claims = report_claims(built['files'][f'report-{variant}.md'])
        by_id = {row['claim_id']: row for row in key_rows(built, variant)}
        assert sorted(claims) == sorted(by_id)
        cache = {}
        for ident in sorted(claims):
            text = claims[ident]
            assert by_id[ident]['claim_text'] == text, (ident, text)
            read = read_claim(text, batches)
            assert read is not None, f'no shape fits: {text}'
            if text not in cache:
                cache[text] = verdict(read[1], read[0], batches, whole)
            decided += 1
            row = by_id[ident]
            parsed, kept = cache[text], row['semantic_state']
            if counts:
                kept = (kept, row['derivation']['worlds'], row['derivation']['worlds_where_true'])
            else:
                parsed = parsed[0]
            if parsed != kept:
                wrong.append((variant, ident, text, parsed, kept))
    return wrong, decided


# ---------- The tests ----------

class ScaledLedgerTests(unittest.TestCase):
    maxDiff = None

    def test_claims_per_report_equal_the_slots_of_the_batches_and_every_row_names_its_batch(self):
        for scale in RUNGS:
            for seed in range(1, 6):
                built, where = case(seed, scale), f'seed {seed} scale {scale}'
                replay = scaled_ledger.build_scaled(
                    random.Random(f'evalsuite:{generate.GENERATOR_VERSION}:{seed}:1:ledger:scale{scale}'), scale)
                self.assertEqual(len(replay['blocks']), scale, where)
                slots = [len(block['slots']) for block in replay['blocks']]
                expected = sum(slots)
                width = max(2, len(str(expected)))
                names = [f'C{n:0{width}d}' for n in range(1, expected + 1)]
                self.assertEqual(sorted(slots), [9] * scale, where)
                self.assertEqual(built['key']['generator']['scale'], scale, where)
                self.assertEqual(built['key']['generator']['blocks'], scale, where)
                seen = {}
                for variant in ('correct', 'mixed'):
                    in_report = list(report_claims(built['files'][f'report-{variant}.md']))
                    in_key = [row['claim_id'] for row in key_rows(built, variant)]
                    self.assertEqual(in_report, names, f'{where} {variant}')
                    self.assertEqual(in_key, names, f'{where} {variant}')
                    blocks = [row['block'] for row in key_rows(built, variant)]
                    self.assertTrue(all(isinstance(b, int) and 1 <= b <= scale for b in blocks), where)
                    seen[variant] = blocks
                    self.assertEqual([blocks.count(b) for b in range(1, scale + 1)], slots, f'{where} {variant}')
                # Both reports hold the same claim in each place, so the same block.
                self.assertEqual(seen['correct'], seen['mixed'], where)

    def test_a_parser_that_reads_only_the_documents_agrees_with_the_key_over_the_whole_dossier_at_scale_2(self):
        decided = 0
        for seed in range(1, 21):
            wrong, count = disagreements(case(seed, 2), whole=True)
            decided += count
            self.assertEqual(wrong, [], f'seed {seed}')
        self.assertEqual(decided, 20 * 2 * 18)

    def test_the_parser_agrees_with_the_key_for_every_claim_over_its_own_batch_at_scale_4(self):
        decided = 0
        for seed in range(1, 6):
            wrong, count = disagreements(case(seed, 4), whole=False, counts=True)
            decided += count
            self.assertEqual(wrong, [], f'seed {seed}')
        self.assertEqual(decided, 5 * 2 * 36)

    def test_the_parser_agrees_with_the_key_for_every_claim_of_both_reports_at_scales_12_and_30(self):
        # Every claim, not a sample: a defect confined to one batch must not slip between the sampled claims.
        decided = 0
        for scale in (12, 30):
            for seed in range(1, 4):
                wrong, count = disagreements(case(seed, scale), whole=False, counts=True)
                self.assertEqual(wrong, [], f'seed {seed} scale {scale}')
                self.assertEqual(count, 2 * 9 * scale, f'seed {seed} scale {scale}')
                decided += count
        self.assertEqual(decided, 3 * 2 * 9 * (12 + 30))

    def test_the_parser_notices_a_document_that_no_longer_matches_the_key(self):
        """A control for the parser: a budget changed in L-03 must change a verdict it reaches."""
        built = case(1, 4)
        first = rows(sections(source_texts(built)['ledger.md'])['L-03'])[0]
        changed = dict(built, files=dict(built['files']))
        name = 'sources/ledger.md'
        changed['files'][name] = built['files'][name].replace(
            f'| {first["Batch"]} | {first["Comparison budget, euro cents"]} |',
            f'| {first["Batch"]} | {int(first["Comparison budget, euro cents"]) + 500} |')
        self.assertNotEqual(changed['files'][name], built['files'][name])
        wrong, _ = disagreements(changed, whole=False)
        self.assertTrue(wrong)
        self.assertTrue(all(first['Batch'] in text for _, _, text, _, _ in wrong), wrong)

    def test_no_name_is_used_in_two_batches_no_name_starts_another_and_the_cutoff_has_one_value(self):
        for scale in RUNGS:
            for seed in range(1, 6):
                built = case(seed, scale)
                where = f'seed {seed} scale {scale}'
                sources = source_texts(built)
                book = sections(sources['ledger.md'])
                batches = [f'B{n:0{max(2, len(str(scale)))}d}' for n in range(1, scale + 1)]
                table = rows(book['L-02'])
                entries = [r['Entry'] for r in table]
                owner = {r['Entry']: r['Batch'] for r in table}
                self.assertEqual(len(set(entries)), len(entries), where)
                self.assertEqual(sorted(set(owner.values())), batches, where)
                for part in ('L-01', 'L-03'):
                    self.assertEqual([r['Batch'] for r in rows(book[part])], batches, f'{where} {part}')
                annexes = sections(sources['annex.md'])
                self.assertEqual(sorted(annexes), [f'A-{b[1:]}' for b in batches], where)
                for anchor, body in annexes.items():
                    self.assertIn(f'## {anchor} — Annex B{anchor[2:]}\n', body, where)
                # Entry identifiers, batch names and annex names: none is the start of another.
                names = set(entries) | set(batches) | {f'Annex {b}' for b in batches}
                for one, other in itertools.permutations(sorted(names), 2):
                    self.assertFalse(other.startswith(one), f'{where}: {one} starts {other}')
                self.assertEqual({len(e) for e in entries}, {len(entries[0])}, where)
                # What each claim names belongs to the claim's block, and no name turns up in two blocks.
                where_named = {}
                for variant in ('correct', 'mixed'):
                    for row in key_rows(built, variant):
                        text = row['claim_text']
                        named_batches = set(re.findall(r'\bB\d+\b', text))
                        named_entries = set(re.findall(r'\bP\d+\b', text))
                        self.assertTrue(named_batches or named_entries, f'{where}: {text}')
                        self.assertLessEqual(len(named_batches), 1, f'{where}: {text}')
                        for name in named_batches | named_entries:
                            where_named.setdefault(name, set()).add(row['block'])
                        for entry in named_entries:
                            self.assertEqual(row['block'], batches.index(owner[entry]) + 1, f'{where}: {text}')
                        for batch in named_batches:
                            self.assertEqual(row['block'], batches.index(batch) + 1, f'{where}: {text}')
                            self.assertEqual({owner[e] for e in named_entries} - {batch}, set(), f'{where}: {text}')
                self.assertTrue(all(len(blocks) == 1 for blocks in where_named.values()), where)
                # Corrections concern entries of one batch each and restate that entry's ledger amount.
                ledger_amount = {r['Entry']: r['Ledger amount, euro cents'] for r in table}
                for r in rows(sections(sources['corrections.md'])['K-01']):
                    self.assertEqual(r['Ledger amount, euro cents'], ledger_amount[r['Entry']], where)
                # One audit cutoff, stated once; every payment is dated in the 90 days before it, every
                # follow-up in an annex after it.
                every = '\n'.join(sources.values())
                stated = re.findall(r'(\d{4}-\d\d-\d\d) audit cutoff', every)
                self.assertEqual(len(stated), 1, where)
                cutoff = datetime.date.fromisoformat(stated[0])
                self.assertEqual(every.count(stated[0]), 1, where)
                for r in table:
                    paid_on = datetime.date.fromisoformat(r['Payment date'])
                    self.assertTrue(cutoff - datetime.timedelta(days=90) <= paid_on < cutoff, where)
                for body in annexes.values():
                    for later in re.findall(r'\d{4}-\d\d-\d\d', body):
                        self.assertGreater(datetime.date.fromisoformat(later), cutoff, where)

    def test_section_m01_says_what_a_statement_that_names_a_batch_covers(self):
        """The parser never reads sources/method.md, so this is the only check of the sentence that tells an
        auditor what a statement naming a batch covers: that batch's sampled entries that are not voided, or its
        whole population where the statement names the population (not, say, the whole sample of the dossier)."""
        sentence = ('A statement that names a batch covers only that batch: its sampled entries that are not voided, '
                    'or its whole population where the statement names the population.')
        for scale in (2,) + RUNGS:
            for seed in range(1, 4):
                scope = sections(source_texts(case(seed, scale))['method.md'])['M-01']
                self.assertIn(sentence, ' '.join(scope.split()), f'seed {seed} scale {scale}')

    def test_the_same_seed_gives_the_same_dossier_in_one_process(self):
        again = generate.build_case('ledger', 2, 1, 12)
        self.assertEqual(json.dumps(case(2, 12), sort_keys=True), json.dumps(again, sort_keys=True))
        self.assertNotEqual(case(2, 12)['files'], case(3, 12)['files'])


class HashSeedTests(unittest.TestCase):
    def test_two_hash_seeds_give_identical_trees(self):
        root = Path(tempfile.mkdtemp(prefix='evalsuite-scale-ledger-tests-', dir=TEST_ROOT))
        self.addCleanup(shutil.rmtree, root)

        def tree(folder):
            lines = [f'{path.relative_to(folder)}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n'
                     for path in sorted(folder.rglob('*')) if path.is_file()]
            return ''.join(lines)

        trees = {}
        for hash_seed in (1, 2):
            fixtures, keys = root / f'run{hash_seed}/fixtures', root / f'run{hash_seed}/keys'
            subprocess.run([sys.executable, str(ROOT / 'evalsuite/generate.py'), '--fixtures', str(fixtures),
                            '--keys', str(keys), '--seed', '3', '--dossiers', '2', '--families', 'ledger',
                            '--scale', '12'],
                           env=dict(os.environ, PYTHONHASHSEED=str(hash_seed)), check=True, capture_output=True, text=True)
            summary = Path(str(keys.resolve()) + '.generation.json')
            trees[hash_seed] = (tree(fixtures), tree(keys), hashlib.sha256(summary.read_bytes()).hexdigest())
        self.assertEqual(trees[1], trees[2])
        self.assertEqual(trees[1][0].count('report-correct.md'), 2)
        self.assertEqual(trees[1][1].count('.json'), 2)


if __name__ == '__main__':
    unittest.main()
