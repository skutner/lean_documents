"""The scaled `conflict` dossier (`--scale N`): counts, merge integrity, trap share and an independent key.

A scale-N dossier is N blocks of twelve venues. The parser below (`Dossier`) is written apart from the
generator: it reads only the merged Markdown sources and the two reports, recomputes every verdict by
enumerating worlds, and recomputes what each of the two flawed readers (rank takes record A, count takes the
amount two records carry) would answer. `Dossier`, `holds` and `aggregate` use nothing from `lure_conflict` or
`generate`, and `generate.judge` is never called. The module does import `lure_conflict`, but only for the
interface checks (`build_scaled`, `MAX_SCALE`) and for `FACTS`, the hand-written note text that the notes check
compares the rendered notes with. The key's `semantic_state`, `lure_states`, `echo_state` and world counts must
equal what `Dossier` computes.

Venue names are compound (`Aldergate`, `Alderfield`) and deliberately alike, so every match here is of a whole
name. Blocks are not visible in the fixtures, so the block of a venue is read from the key's `block` field.

Run time (Python 3.9.6, one core): about 7 to 9 seconds for the file. The heavy parts are the 25-seed builds at
scale 30, which are cached across the tests, and the full products at scale 2.
"""
import collections
import hashlib
import itertools
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
import generate
generate.HARD_FAMILIES.setdefault('conflict', 'lure_conflict')
import lure_conflict

STEMS = ('Alder', 'Bramble', 'Cedar', 'Dunlin', 'Elm', 'Fennel', 'Grove', 'Hazel', 'Iris', 'Juniper', 'Kestrel', 'Linden')
SUFFIXES = tuple('gate field wick ford holm stead mere combe thorpe by ham ton ley worth dale well bridge mouth '
                 'side wood cliff haven moor bank hurst croft garth shaw firth head'.split())
POOL = {stem + suffix: stem for stem in STEMS for suffix in SUFFIXES}
READERS = ('higher_ranked_source_wins', 'most_copies_win')
VENUE_FILES = ('schedule.md', 'correspondence.md', 'circulation.md', 'agreement.md')
DATED_FILES = VENUE_FILES + ('procedure.md',)
WORDINGS = ('at most', 'more than', 'exactly', 'not')


def holds(wording, value, amount):
    return (value <= amount if wording == 'at most' else value > amount if wording == 'more than'
            else value == amount if wording == 'exactly' else value != amount)


def aggregate(outcomes):
    outcomes = set(outcomes)
    return 'undetermined' if len(outcomes) == 2 else 'supported' if True in outcomes else 'refuted'


def wording_of(text):
    return re.fullmatch(r'The daily room hire charge for \w+ is (at most|more than|exactly|not) EUR \d+\.', text)[1]


class Dossier:
    """The sources and reports of one dossier, read the way an auditor would. Nothing here knows the key."""

    CLAIM = re.compile(r'<a id="(C\d+)"></a>\n\*\*\1\.\*\* '
                       r'The daily room hire charge for (\w+) is (at most|more than|exactly|not) EUR (\d+)\. '
                       r'Citations: ([^\n]*)\n')

    def __init__(self, files):
        self.files = files
        self.sources = {name[8:]: text for name, text in files.items() if name.startswith('sources/')}
        patterns = {
            'A': ('schedule.md', r'For (\w+), this schedule states a daily room hire charge of EUR (\d+)\.'),
            'B': ('correspondence.md', r"The steward's booking correspondence for (\w+) gives a daily room hire charge of EUR (\d+)\."),
            'C': ('circulation.md', r'The programme desk lists the daily room hire charge for (\w+) as EUR (\d+), copied from record [AB]\.')}
        self.records, self.listed = {}, {}
        for letter, (filename, pattern) in patterns.items():
            found = re.findall(pattern, self.sources[filename])
            self.listed[filename] = [venue for venue, _ in found]
            self.records[letter] = {venue: int(amount) for venue, amount in found}
        found = re.findall(r'At (\w+), the parties select (record [AB]|no entry) as the governing room hire entry\.',
                           self.sources['agreement.md'])
        self.listed['agreement.md'] = [venue for venue, _ in found]
        self.selection = {venue: None if value == 'no entry' else value[-1] for venue, value in found}
        self.venues = sorted(self.records['A'])
        self.candidates, self.fixed = {}, {}
        for venue in self.venues:
            values = [self.records[letter][venue] for letter in 'ABC']
            self.candidates[venue] = sorted(set(values))
            # Rule 3 and rules 4 to 7 of the procedure: the same amount is established; otherwise a selected record.
            if len(self.candidates[venue]) == 1:
                self.fixed[venue] = values[0]
            elif self.selection[venue]:
                self.fixed[venue] = self.records[self.selection[venue]][venue]
        self.open = [venue for venue in self.venues if venue not in self.fixed]
        # The two flawed readers ignore every clause: each holds one amount per venue.
        self.reader_value = {
            READERS[0]: dict(self.records['A']),
            READERS[1]: {venue: collections.Counter(self.records[letter][venue] for letter in 'ABC').most_common(1)[0][0]
                         for venue in self.venues}}
        self.reports = {variant: self.CLAIM.findall(files[f'report-{variant}.md']) for variant in ('correct', 'mixed')}
        for variant, claims in self.reports.items():
            assert len(claims) == files[f'report-{variant}.md'].count('<a id="C'), variant

    def worlds(self, venues):
        """Every combination of the candidate amounts of `venues`, an open venue at a time."""
        names = [venue for venue in venues if venue in self.open]
        return [dict(zip(names, combination)) for combination in
                itertools.product(*(self.candidates[venue] for venue in names))]

    def verdict(self, venue, wording, amount, worlds):
        return aggregate(holds(wording, world[venue] if venue in world else self.fixed[venue], amount)
                         for world in worlds)

    def reader_verdicts(self, venue, wording, amount):
        return {name: 'supported' if holds(wording, values[venue], amount) else 'refuted'
                for name, values in self.reader_value.items()}


def ident_list(count):
    width = max(2, len(str(count)))
    return [f'C{n:0{width}d}' for n in range(1, count + 1)]


_cache = {}


def case(seed, scale, index=1):
    if (seed, scale, index) not in _cache:
        if len(_cache) > 120:
            _cache.pop(next(iter(_cache)))
        _cache[seed, scale, index] = generate.build_case('conflict', seed, index, scale)
    return _cache[seed, scale, index]


def rows_of(built, variant):
    return [row for row in built['key']['expectations'] if row['variant'] == variant]


def block_of_venue(built):
    """venue -> the set of blocks the key gives to it, over both reports."""
    blocks = collections.defaultdict(set)
    for row in built['key']['expectations']:
        blocks[re.search(r'charge for (\w+) is', row['claim_text'])[1]].add(row['block'])
    return blocks


class ScaledConflictTests(unittest.TestCase):
    def check_rows(self, built, dossier, scale, claims, product_of_dossier):
        """Compare `claims` (variant, index into that report) with the key, recomputed from the Markdown."""
        key_rows = {(row['variant'], row['claim_id']): row for row in built['key']['expectations']}
        block_of = {venue: next(iter(blocks)) for venue, blocks in block_of_venue(built).items()}
        open_in_block = collections.defaultdict(list)
        for venue in dossier.open:
            open_in_block[block_of[venue]].append(venue)
        everything = dossier.worlds(dossier.venues) if product_of_dossier else None
        for variant, position in claims:
            ident, venue, wording, amount, citations = dossier.reports[variant][position]
            amount = int(amount)
            row = key_rows[variant, ident]
            self.assertEqual(row['claim_text'], f'The daily room hire charge for {venue} is {wording} EUR {amount}.')
            worlds = everything if product_of_dossier else dossier.worlds([venue])
            state = dossier.verdict(venue, wording, amount, worlds)
            self.assertEqual(row['semantic_state'], state, (variant, ident, venue))
            readers = dossier.reader_verdicts(venue, wording, amount)
            lures = {name: verdict for name, verdict in readers.items() if verdict != state}
            self.assertEqual(row['lure_states'], lures, (variant, ident, venue))
            self.assertEqual(row['echo_state'], lures.get(READERS[0]))
            self.assertEqual(row['lure_derivations'], {name: {'worlds': 1, 'worlds_where_true': int(verdict == 'supported')}
                                                       for name, verdict in lures.items()})
            self.assertEqual(row['claim_kind'], 'room_hire_budget' if wording in ('at most', 'more than') else 'room_hire_exact')
            # The derivation counts the claim's own block, not the dossier.
            size = 1
            for name in open_in_block[row['block']]:
                size *= len(dossier.candidates[name])
            own = dossier.worlds([venue])
            true_own = sum(holds(wording, world[venue] if venue in world else dossier.fixed[venue], amount) for world in own)
            self.assertEqual(row['derivation']['worlds'], size, (variant, ident))
            self.assertEqual(row['derivation']['worlds_where_true'], true_own * size // len(own), (variant, ident))
            self.assertEqual(row['block'], block_of[venue])
            expected = ['sources/' + name + '#V-' + venue for name in VENUE_FILES] + ['sources/procedure.md#P-01']
            self.assertEqual(row['source_anchors'], expected)
            for anchor in expected:
                filename, ident_ = anchor[len('sources/'):].split('#')
                self.assertIn(f'<a id="{ident_}"></a>', dossier.sources[filename])

    # ----- criterion 3: counts -----

    def test_counts_identifiers_blocks_and_scale_for_every_rung(self):
        for scale in (4, 12, 30):
            for seed in range(1, 6):
                built = case(seed, scale)
                raw = lure_conflict.build_scaled(random.Random(seed), scale)
                self.assertEqual(len(raw['blocks']), scale)
                self.assertTrue(all(len(block['slots']) == 12 for block in raw['blocks']))
                total = sum(len(block['slots']) for block in raw['blocks'])
                self.assertEqual(total, 12 * scale)
                key = built['key']
                self.assertEqual(key['generator']['scale'], scale)
                self.assertEqual(key['generator']['blocks'], scale)
                self.assertEqual(built['dossier'], f'g{seed}-001-conflict-x{scale}')
                idents = ident_list(total)
                dossier = Dossier(built['files'])
                for variant in ('correct', 'mixed'):
                    rows = rows_of(built, variant)
                    self.assertEqual([row['claim_id'] for row in rows], idents, (scale, seed, variant))
                    self.assertEqual([claim[0] for claim in dossier.reports[variant]], idents, (scale, seed, variant))
                    self.assertEqual([row['claim_text'] for row in rows],
                                     [f'The daily room hire charge for {v} is {w} EUR {a}.'
                                      for _, v, w, a, _ in dossier.reports[variant]])
                    self.assertTrue(all(1 <= row['block'] <= scale for row in rows))
                    self.assertEqual(collections.Counter(row['block'] for row in rows),
                                     collections.Counter({block: 12 for block in range(1, scale + 1)}))
                # Both reports use one order: the same claim position is the same venue in the same block.
                for correct, mixed in zip(rows_of(built, 'correct'), rows_of(built, 'mixed')):
                    self.assertEqual(correct['block'], mixed['block'])
                    self.assertEqual(re.search(r'charge for (\w+) is', correct['claim_text'])[1],
                                     re.search(r'charge for (\w+) is', mixed['claim_text'])[1])
                self.assertEqual(sorted(raw['blocks'][0]), ['base', 'readers', 'rng', 'slots', 'told', 'unknowns'])
                for block in raw['blocks']:
                    self.assertIsInstance(block['rng'], random.Random)
                    self.assertEqual(block['told'], block['readers'][READERS[0]])
                    self.assertEqual(set(block['readers']), set(READERS))
                self.assertEqual(raw['reader'], READERS[0])

    def test_scale_limits_and_defaults(self):
        self.assertEqual(lure_conflict.MAX_SCALE, 30)
        for scale in (0, 31, -1):
            with self.assertRaises(generate.GenerationError):
                lure_conflict.build_scaled(random.Random(1), scale)
        names = {re.search(r'charge for (\w+) is', row['claim_text'])[1] for row in case(1, 30)['key']['expectations']}
        self.assertEqual(names, set(POOL), 'scale 30 uses all 360 compound names')

    # ----- criterion 4: the key, recomputed from the merged Markdown -----

    def test_scale_two_full_product_of_every_open_fact_of_the_dossier(self):
        worlds_seen = set()
        for seed in range(1, 21):
            built = case(seed, 2)
            dossier = Dossier(built['files'])
            worlds_seen.add(len(dossier.worlds(dossier.venues)))
            claims = [(variant, position) for variant in ('correct', 'mixed') for position in range(24)]
            self.assertEqual(len(dossier.reports['correct']), 24)
            self.assertEqual(len(dossier.reports['mixed']), 24)
            self.check_rows(built, dossier, 2, claims, True)
        self.assertGreater(max(worlds_seen), 1)

    def test_scale_four_every_claim_over_its_own_venues_open_facts(self):
        for seed in range(1, 6):
            built = case(seed, 4)
            dossier = Dossier(built['files'])
            claims = [(variant, position) for variant in ('correct', 'mixed') for position in range(48)]
            self.check_rows(built, dossier, 4, claims, False)

    def test_scales_twelve_and_thirty_every_claim_of_both_reports(self):
        # Every claim, not a sample: a defect confined to one block must not slip between the sampled claims.
        checked = 0
        for scale in (12, 30):
            for seed in range(1, 4):
                built = case(seed, scale)
                dossier = Dossier(built['files'])
                for variant in ('correct', 'mixed'):
                    self.assertEqual(len(dossier.reports[variant]), 12 * scale, (scale, seed, variant))
                claims = [(variant, position) for variant in ('correct', 'mixed') for position in range(12 * scale)]
                self.assertEqual(len(claims), 24 * scale)
                self.check_rows(built, dossier, scale, claims, False)
                checked += len(claims)
        self.assertEqual(checked, 3 * 24 * (12 + 30))

    def test_the_flawed_readers_are_wrong_in_every_direction_at_scale(self):
        seen = collections.defaultdict(set)
        for seed in range(1, 6):
            built = case(seed, 12)
            for row in built['key']['expectations']:
                for name, state in row['lure_states'].items():
                    seen[name].add((row['semantic_state'], state))
        self.assertEqual(set(seen), set(READERS))
        for observed in seen.values():
            self.assertIn(('undetermined', 'supported'), observed)
            self.assertTrue(observed <= {('undetermined', 'supported'), ('undetermined', 'refuted'),
                                         ('supported', 'refuted'), ('refuted', 'supported')})

    # ----- criterion 5: merge integrity -----

    def test_names_blocks_notes_and_dates_stay_whole(self):
        for scale in (4, 12, 30):
            for seed in range(1, 6):
                built = case(seed, scale)
                dossier = Dossier(built['files'])
                where = (scale, seed)
                # Four files each list every venue once; a dict keyed by venue would hide a repeat.
                for filename in VENUE_FILES:
                    listed = dossier.listed[filename]
                    self.assertEqual(len(listed), 12 * scale, where)
                    self.assertEqual(len(set(listed)), 12 * scale, (filename, where))
                    self.assertEqual(set(listed), set(dossier.venues))
                    anchors = re.findall(r'<a id="([^"]+)"></a>', dossier.sources[filename])
                    self.assertEqual(len(anchors), len(set(anchors)), (filename, where))
                    self.assertEqual({a for a in anchors if a.startswith('V-')}, {'V-' + v for v in dossier.venues})
                self.assertTrue(set(dossier.venues) <= set(POOL), where)
                for a, b in itertools.permutations(dossier.venues, 2):
                    self.assertFalse(b.startswith(a), (a, b))
                # Every claim names one venue; a venue belongs to one block; each block has twelve.
                blocks = block_of_venue(built)
                self.assertEqual(set(blocks), set(dossier.venues))
                self.assertTrue(all(len(value) == 1 for value in blocks.values()), where)
                per_block = collections.Counter(next(iter(value)) for value in blocks.values())
                self.assertEqual(per_block, collections.Counter({b: 12 for b in range(1, scale + 1)}))
                # A block is not contiguous in the merged files (venues are listed by name).
                runs = collections.Counter()
                previous = None
                for venue in dossier.venues:
                    block = next(iter(blocks[venue]))
                    runs[block] += block != previous
                    previous = block
                self.assertTrue(all(count > 1 for count in runs.values()), (where, runs))
                # No stem stands alone: the notes carry the full name.
                everything = ''.join(dossier.sources.values())
                for stem in STEMS:
                    self.assertIsNone(re.search(rf'\b{stem}\b', everything), (stem, where))
                self.check_notes(dossier, where)
                self.check_dates(dossier, where)

    def check_notes(self, dossier, where):
        from lure_conflict import FACTS
        endings = {
            'schedule.md': 0, 'correspondence.md': 1, 'circulation.md': 2}
        for filename, note in endings.items():
            text = dossier.sources[filename]
            bodies = dict(re.findall(r'<a id="V-(\w+)"></a>\n## V-\1 — \1\n(.*?)\n(?=\n<a id=|\Z)', text, re.S))
            self.assertEqual(set(bodies), set(dossier.venues), (filename, where))
            for venue, body in bodies.items():
                stem = POOL[venue]
                self.assertTrue(body.endswith(FACTS[stem][note].replace(stem, venue)), (filename, venue, where))

    def check_dates(self, dossier, where):
        days = {}
        for filename in DATED_FILES:
            text = dossier.sources[filename]
            found = re.findall(r'Issue date: (\d+) September 2034\.', text)
            self.assertEqual(len(found), 1, (filename, where))
            self.assertEqual(text.count('September 2034'), 1, (filename, where))
            days[filename] = int(found[0])
        others = [days[name] for name in DATED_FILES if name != 'agreement.md']
        self.assertEqual(len(set(others)), 4, where)
        self.assertTrue(all(1 <= day <= 23 for day in others), where)
        self.assertTrue(max(others) < days['agreement.md'] <= max(others) + 4, where)

    # ----- criterion 6: the share of lure rows and the controls -----

    def test_lure_share_and_controls_match_scale_one(self):
        for scale, tolerance in ((4, .35), (12, .25), (30, .25)):
            def share(built_cases):
                rows = [row for built in built_cases for row in built['key']['expectations']]
                return sum(row['echo_state'] is not None or bool(row['lure_states']) for row in rows) / len(rows)
            scaled = share([case(seed, scale) for seed in range(1, 26)])
            # Scale 1: seeds 1 to 25 at indexes 1 to N, 25 x N dossiers.
            single = share([case(seed, 1, index) for seed in range(1, 26) for index in range(1, scale + 1)])
            self.assertGreater(single, .02)
            self.assertLessEqual(abs(scaled / single - 1), tolerance, (scale, scaled, single))
            for seed in range(1, 26):
                built = case(seed, scale)
                for variant in ('correct', 'mixed'):
                    rows = rows_of(built, variant)
                    controls = {(row['block'], wording_of(row['claim_text'])) for row in rows
                                if not row['lure_states'] and row['echo_state'] is None}
                    for row in rows:
                        if row['lure_states']:
                            self.assertIn((row['block'], wording_of(row['claim_text'])), controls,
                                          (scale, seed, variant, row['claim_id']))

    # ----- criterion 7: determinism -----

    def test_hash_seed_independence_of_the_written_trees(self):
        def tree(folder):
            digest = hashlib.sha256()
            for path in sorted(folder.rglob('*')):
                if path.is_file():
                    digest.update(f'{path.relative_to(folder)}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n'.encode())
            return digest.hexdigest()
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='conflict-scale-', dir=ROOT / 'tmp') as temporary:
            results = []
            for hash_seed in ('1', '2'):
                folder = Path(temporary) / hash_seed
                env = dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONDONTWRITEBYTECODE='1')
                subprocess.run([sys.executable, str(ROOT / 'evalsuite/generate.py'), '--fixtures', str(folder / 'fixtures'),
                                '--keys', str(folder / 'keys'), '--seed', '3', '--dossiers', '3', '--families', 'conflict',
                                '--scale', '12'], cwd=ROOT, env=env, check=True, capture_output=True, text=True)
                names = sorted(path.name for path in (folder / 'fixtures').iterdir())
                self.assertEqual(names, [f'g3-00{n}-conflict-x12' for n in (1, 2, 3)])
                results.append((tree(folder / 'fixtures'), tree(folder / 'keys'),
                                (folder / 'keys.generation.json').read_bytes()))
            self.assertEqual(results[0], results[1])

    def test_building_twice_gives_the_same_dossier(self):
        self.assertEqual(generate.build_case('conflict', 7, 2, 12), generate.build_case('conflict', 7, 2, 12))


if __name__ == '__main__':
    unittest.main()
