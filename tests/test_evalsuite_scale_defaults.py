"""The scaled `defaults` family: N blocks of seven rooms and seven notices in one dossier.

A scale-N dossier is N independently drawn blocks whose rooms and notices carry dossier-unique
names, merged into one briefing, one booking file and one notice file, with the claims of all
blocks shuffled together. These tests check that the merge keeps every verdict exact and every
name single.

The reader below is written apart from the generator. It reads only the merged Markdown sources
and a report, imports no judging code, and decides each claim a second time: over every world the
documents allow (all open items of the dossier at N=2, the claim's own room or notice at larger
scales), and again for the flawed reader who fills an open item with its desk default (the August
rate for a room, exclusive counting for a notice). That second verdict must equal `echo_state` in
the key. The builder's own output (`scaled_builder`) is used only to learn which names belong to
which block, which the Markdown does not say.

Run time on an Apple laptop with Python 3.9: see the report of the change; the slowest parts are
the 25-seed trap-share counts and the two generator runs of the determinism test.
"""
import collections
import datetime
import functools
import hashlib
import itertools
import json
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
generate.HARD_FAMILIES.setdefault('defaults', 'lure_defaults')

SCALES = (4, 12, 30)
CLAIMS_PER_BLOCK = 12


@functools.lru_cache(maxsize=None)
def built(seed, scale):
    return generate.build_case('defaults', seed, 1, scale)


def blocks_of(seed, scale):
    """The builder's blocks for the dossier `build_case` makes, drawn with the dossier rng it uses."""
    rng = random.Random(f'evalsuite:{generate.GENERATOR_VERSION}:{seed}:1:defaults:scale{scale}')
    return generate.scaled_builder('defaults')(rng, scale)['blocks']


def sources_of(case):
    return {name[8:]: text for name, text in case['files'].items() if name.startswith('sources/')}


def rows_of(case, variant):
    return [row for row in case['key']['expectations'] if row['variant'] == variant]


# ---------- The independent reader ----------

def pence(text):
    pounds, pennies = text.split('.')
    return 100 * int(pounds) + int(pennies)


def entries(text, heading):
    """(name, body) for every `### ...` entry of a document; the heading pattern has one group, the name."""
    found = list(re.finditer(heading, text, re.M))
    return [(match[1], text[match.end():found[n + 1].start() if n + 1 < len(found) else len(text)])
            for n, match in enumerate(found)]


class Reading:
    """What the merged documents of one dossier say, and the verdict of a claim over the worlds they allow."""

    def __init__(self, sources):
        self.values, self.fills = {}, {}
        desk = re.findall(r'^(\S+) room: August rate (\d+\.\d\d) pounds per session\.[ ]*$', sources['briefing.md'], re.M)
        self.desk = {name: pence(rate) for name, rate in desk}
        assert len(self.desk) == len(desk), 'a room has two desk estimates'
        assert 'starts counting on the day after receipt' in sources['briefing.md']
        rooms = entries(sources['bookings.md'], r'^### (\S+) room$')
        assert len({name for name, _ in rooms}) == len(rooms), 'a room has two booking entries'
        assert {name for name, _ in rooms} == set(self.desk), 'desk estimates and bookings name different rooms'
        self.pairs = {}
        for room, body in rooms:
            listed = re.search(r'exactly two possible September rates for this room: (\d+\.\d\d) pounds or '
                               r'(\d+\.\d\d) pounds per session', body)
            chosen = re.findall(r'For September the rate is (\d+\.\d\d) pounds per session\.', body)
            missing = 'No supplied document selects either September rate.' in body
            assert listed and len(chosen) + missing == 1, room
            pair = sorted(pence(value) for value in listed.groups())
            self.pairs[room] = pair
            key = ('hire', room)
            if chosen:
                assert pence(chosen[0]) in pair, room
                self.values[key] = [pence(chosen[0])]
                self.fills[key] = pence(chosen[0])
            else:
                self.values[key] = pair
                self.fills[key] = self.desk[room]
        notices = entries(sources['notices.md'], r'^### Notice (N\d+)$')
        assert len({name for name, _ in notices}) == len(notices), 'a notice has two entries'
        for ident, body in notices:
            received = datetime.date.fromisoformat(re.search(
                rf'delivery acknowledgement for notice {ident} records receipt on (\d{{4}}-\d\d-\d\d)\.', body)[1])
            period = int(re.search(r'The response period is (\d+) days\.', body)[1])
            chosen = re.findall(r'using (inclusive|exclusive) counting\.', body)
            open_ = 'does not select inclusive or exclusive counting.' in body
            assert len(chosen) + open_ == 1, ident

            def last_day(convention):
                # Day one is the receipt date when counting inclusively, the day after when exclusively.
                first = received.toordinal() + (convention == 'exclusive')
                return first + period - 1

            key = ('date', ident)
            self.values[key] = [last_day(c) for c in (chosen or ['inclusive', 'exclusive'])]
            self.fills[key] = last_day(chosen[0]) if chosen else last_day('exclusive')

    @staticmethod
    def claims(report):
        """{identifier: claim text} from a report, in order; the order is checked by the caller."""
        found = re.findall(r'<a id="(C\d+)"></a>\n\*\*C\d+\.\*\* (.*?) Citations?: [^\n]*\n', report)
        return found

    @staticmethod
    def parse(text):
        match = re.fullmatch(r'The September hire charge for the (\S+) room (is at most|exceeds) '
                             r'(\d+\.\d\d) pounds\.', text)
        if match:
            return ('hire', match[1]), match[2] == 'is at most', pence(match[3])
        match = re.fullmatch(r'The response deadline for notice (N\d+) falls (on or before|after) '
                             r'(\d{4}-\d\d-\d\d)\.', text)
        assert match, text
        return ('date', match[1]), match[2] == 'on or before', datetime.date.fromisoformat(match[3]).toordinal()

    def verdict(self, text, scope=None):
        """supported, refuted or undetermined over every world of `scope` (a list of keys that take each of
        their values; the others keep their only value). Default scope: the claim's own item."""
        key, at_most, bound = self.parse(text)
        scope = [key] if scope is None else scope
        varying = [k for k in scope if len(self.values[k]) > 1]
        outcomes = set()
        for combination in itertools.product(*(self.values[k] for k in varying)):
            world = {k: v[0] for k, v in self.values.items() if len(v) == 1}
            world.update(zip(varying, combination))
            outcomes.add(world[key] <= bound if at_most else world[key] > bound)
        return 'undetermined' if len(outcomes) == 2 else 'supported' if True in outcomes else 'refuted'

    def reader_verdict(self, text):
        """The verdict of a reader who takes the desk default for every open item and the stated value for
        every other."""
        key, at_most, bound = self.parse(text)
        value = self.fills[key]
        return 'supported' if (value <= bound if at_most else value > bound) else 'refuted'

    def open_keys(self):
        return [key for key, values in self.values.items() if len(values) > 1]


def check_dossier(test, case, scope):
    """Every claim of both reports against the key."""
    reading = Reading(sources_of(case))
    for variant in ('correct', 'mixed'):
        claims = Reading.claims(case['files'][f'report-{variant}.md'])
        rows = rows_of(case, variant)
        test.assertEqual([ident for ident, _ in claims], [row['claim_id'] for row in rows])
        for (ident, text), row in zip(claims, rows):
            where = (case['dossier'], variant, ident)
            test.assertEqual(text, row['claim_text'], where)
            actual = reading.verdict(text, scope(reading, text))
            test.assertEqual(row['semantic_state'], actual, where)
            believed = reading.reader_verdict(text)
            test.assertEqual(row['echo_state'], believed if believed != actual else None, where)
            if row['echo_state'] is not None:
                test.assertEqual(row.get('echo_reader'), 'fills_in_default', where)
                test.assertEqual(actual, 'undetermined', where)
                test.assertEqual(row['echo_derivation'], {'worlds': 1, 'worlds_where_true': int(believed == 'supported')})
            # Judged per block: no row ever enumerates more than the block's two open items.
            test.assertIn(row['derivation']['worlds'], (1, 2, 4), where)
    return reading


def whole_dossier(reading, text):
    return reading.open_keys()


def own_item(reading, text):
    return None


class ScaleDefaultsTests(unittest.TestCase):
    def test_counts_identifiers_blocks_and_section_widths(self):
        for scale in SCALES:
            for seed in range(1, 6):
                with self.subTest(scale=scale, seed=seed):
                    case = built(seed, scale)
                    blocks = blocks_of(seed, scale)
                    self.assertEqual(len(blocks), scale)
                    total = sum(len(block['slots']) for block in blocks)
                    self.assertEqual(total, CLAIMS_PER_BLOCK * scale)
                    width = max(2, len(str(total)))
                    wanted = [f'C{n:0{width}d}' for n in range(1, total + 1)]
                    named = {}
                    for variant in ('correct', 'mixed'):
                        rows = rows_of(case, variant)
                        self.assertEqual([row['claim_id'] for row in rows], wanted)
                        self.assertEqual([ident for ident, _ in Reading.claims(case['files'][f'report-{variant}.md'])],
                                         wanted)
                        named[variant] = collections.Counter(row['block'] for row in rows)
                        self.assertTrue(all(isinstance(row['block'], int) and 1 <= row['block'] <= scale
                                            for row in rows))
                    self.assertEqual(named['correct'], named['mixed'])
                    self.assertEqual(named['correct'], collections.Counter({b: CLAIMS_PER_BLOCK
                                                                            for b in range(1, scale + 1)}))
                    generator = case['key']['generator']
                    self.assertEqual((generator['scale'], generator['blocks']), (scale, scale))
                    self.assertEqual(case['dossier'], f'g{seed}-001-defaults-x{scale}')

    def test_no_section_outgrows_its_width_at_any_scale(self):
        """A section wider than its field raises ValueError; every scale and many seeds build, and a source
        file has one size whatever is drawn."""
        builder = generate.scaled_builder('defaults')
        for scale in range(2, 31):
            sizes = set()
            for seed in range(1, 9):
                case = builder(random.Random(seed), scale)
                sizes.add(tuple(len(text) for _, text in sorted(case['sources'].items())))
            self.assertEqual(len(sizes), 1, scale)

    def test_independent_reading_over_the_whole_dossier_at_scale_two(self):
        for seed in range(1, 21):
            with self.subTest(seed=seed):
                case = built(seed, 2)
                self.assertEqual(len(rows_of(case, 'mixed')), 2 * CLAIMS_PER_BLOCK)
                check_dossier(self, case, whole_dossier)

    def test_independent_reading_of_each_claims_own_unit_at_scale_four(self):
        for seed in range(1, 6):
            with self.subTest(seed=seed):
                check_dossier(self, built(seed, 4), own_item)

    def test_independent_reading_of_every_claim_at_larger_scales(self):
        # Every claim, not a sample: a defect confined to one block must not slip between the sampled claims.
        for scale in (12, 30):
            for seed in range(1, 4):
                with self.subTest(scale=scale, seed=seed):
                    case = built(seed, scale)
                    for variant in ('correct', 'mixed'):
                        self.assertEqual(len(rows_of(case, variant)), CLAIMS_PER_BLOCK * scale)
                    check_dossier(self, case, own_item)

    def test_open_items_exist_and_reader_disagrees_somewhere(self):
        """The checks above would pass vacuously on dossiers without an open item."""
        for scale in SCALES:
            lures = sum(row['echo_state'] is not None for seed in range(1, 6)
                        for row in built(seed, scale)['key']['expectations'])
            self.assertGreater(lures, 0, scale)
        opened = [len(Reading(sources_of(built(seed, 2))).open_keys()) for seed in range(1, 21)]
        self.assertTrue(any(opened) and max(opened) >= 3, opened)

    def test_desk_estimate_of_an_open_room_is_one_of_its_two_listed_rates(self):
        """The default a flawed reader fills in is that room's own August rate, not another room's."""
        for scale in (2,) + SCALES:
            for seed in range(1, 6):
                reading = Reading(sources_of(built(seed, scale)))
                for room, pair in reading.pairs.items():
                    self.assertIn(reading.desk[room], pair, (scale, seed, room))

    def test_names_belong_to_one_block_and_none_is_a_prefix_of_another(self):
        pool = set(generate.compound_names(('Alder', 'Beech', 'Cedar', 'Hazel', 'Maple', 'Rowan', 'Willow'),
                                           generate.NAME_SUFFIXES))
        for scale in SCALES:
            for seed in range(1, 6):
                with self.subTest(scale=scale, seed=seed):
                    case = built(seed, scale)
                    owner = {}
                    rooms, notices = [], []
                    for number, block in enumerate(blocks_of(seed, scale), 1):
                        names = [key.split(':', 1)[1] for key in list(block['base']) + list(block['unknowns'])]
                        block_rooms = [n for n in names if not re.fullmatch(r'N\d+', n)]
                        block_notices = [n for n in names if re.fullmatch(r'N\d+', n)]
                        self.assertEqual((len(block_rooms), len(block_notices)), (7, 7))
                        rooms += block_rooms
                        notices += block_notices
                        for name in names:
                            self.assertNotIn(name, owner, name)
                            owner[name] = number
                    self.assertEqual(len(rooms), 7 * scale)
                    self.assertTrue(set(rooms) <= pool)
                    width = max(2, len(str(7 * scale)))
                    self.assertEqual(sorted(notices), [f'N{k:0{width}d}' for k in range(1, 7 * scale + 1)])
                    for group in (rooms, notices):
                        for first, second in itertools.permutations(group, 2):
                            self.assertFalse(second.startswith(first), (first, second))
                    # The block each key row names is the block that owns the name its claim is about.
                    for row in case['key']['expectations']:
                        key, _, _ = Reading.parse(row['claim_text'])
                        self.assertEqual(owner[key[1]], row['block'], row['claim_text'])
                    self.check_each_name_stands_once_in_the_sources(sources_of(case), rooms, notices)

    def check_each_name_stands_once_in_the_sources(self, sources, rooms, notices):
        words = {name: collections.Counter(re.findall(r'[A-Za-z0-9]+', text)) for name, text in sources.items()}
        for room in rooms:
            # Whole words only: a room's name is a stem and a suffix, and another's may start with the same stem.
            self.assertEqual({f: c[room] for f, c in words.items() if c[room]},
                             {'briefing.md': 1, 'bookings.md': 1}, room)
        counts = {ident: {f: c[ident] for f, c in words.items() if c[ident]} for ident in notices}
        self.assertEqual({tuple(found) for found in counts.values()}, {('notices.md',)})
        self.assertEqual(len({found['notices.md'] for found in counts.values()}), 1)

    def test_merged_documents_interleave_blocks_and_keep_the_fixed_text_once(self):
        one = sources_of(generate.build_case('defaults', 1, 1))

        def sections(text):
            return {anchor: body.rstrip() for anchor, body in re.findall(r'<a id="([A-Z]-\d\d)"></a>\n(.*?)(?=<a id=|\Z)',
                                                                         text, re.S)}

        for scale in SCALES:
            for seed in range(1, 6):
                case = built(seed, scale)
                sources = sources_of(case)
                self.assertEqual(sources['procedure.md'], one['procedure.md'])
                for name, anchors in (('briefing.md', ('B-01', 'B-03')), ('bookings.md', ('F-00',)),
                                      ('notices.md', ('N-00',))):
                    for anchor in anchors:
                        self.assertEqual(sections(sources[name])[anchor], sections(one[name])[anchor])
                    self.assertEqual(sorted(re.findall(r'<a id="([A-Z]-\d\d)"></a>', sources[name])),
                                     sorted(re.findall(r'<a id="([A-Z]-\d\d)"></a>', one[name])))
                owner = {}
                for number, block in enumerate(blocks_of(seed, scale), 1):
                    owner.update({key.split(':', 1)[1]: number for key in list(block['base']) + list(block['unknowns'])})
                shown = {'bookings.md': [name for name, _ in entries(sources['bookings.md'], r'^### (\S+) room$')],
                         'notices.md': [name for name, _ in entries(sources['notices.md'], r'^### Notice (N\d+)$')],
                         'briefing.md': re.findall(r'^(\S+) room: August rate', sources['briefing.md'], re.M)}
                for name, order in shown.items():
                    self.assertEqual(order, sorted(order), name)
                    runs = [owner[item] for item in order]
                    changes = sum(a != b for a, b in zip(runs, runs[1:]))
                    self.assertGreater(changes + 1, scale, (name, runs))

    def test_trap_share_stays_the_scale_one_share(self):
        def lure_rows(case):
            return sum(row['echo_state'] is not None for row in case['key']['expectations']), len(case['key']['expectations'])

        for scale, tolerance in ((4, .35), (12, .25), (30, .25)):
            with self.subTest(scale=scale):
                lures = rows = 0
                for seed in range(1, 25 * scale + 1):
                    found, count = lure_rows(generate.build_case('defaults', seed, 1))
                    lures, rows = lures + found, rows + count
                base = lures / rows
                lures = rows = 0
                for seed in range(1, 26):
                    case = built(seed, scale) if seed <= 5 else generate.build_case('defaults', seed, 1, scale)
                    found, count = lure_rows(case)
                    lures, rows = lures + found, rows + count
                    self.check_controls(case)
                self.assertGreater(base, 0)
                share = lures / rows
                self.assertLessEqual(abs(share - base) / base, tolerance, (share, base))

    def check_controls(self, case):
        """A lure exists only in the mixed report, and every block with one holds a row of the same kind and
        report without one."""
        groups = collections.defaultdict(list)
        for row in case['key']['expectations']:
            groups[row['variant'], row['block'], row['claim_kind']].append(row['echo_state'] is not None)
        for (variant, block, kind), flags in groups.items():
            if any(flags):
                self.assertEqual(variant, 'mixed')
                self.assertIn(False, flags, (case['dossier'], block, kind))

    def test_runs_under_different_hash_seeds_give_the_same_files(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        trees = []
        with tempfile.TemporaryDirectory(prefix='defaults-scale-tests-', dir=ROOT / 'tmp') as temporary:
            for hash_seed in ('1', '2'):
                fixtures, keys = Path(temporary) / f'f{hash_seed}', Path(temporary) / f'k{hash_seed}'
                env = dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONDONTWRITEBYTECODE='1')
                subprocess.run([sys.executable, str(ROOT / 'evalsuite/generate.py'), '--fixtures', str(fixtures),
                                '--keys', str(keys), '--seed', '3', '--dossiers', '3', '--families', 'defaults',
                                '--scale', '12'], cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)
                tree = {}
                for folder in (fixtures, keys):
                    for path in sorted(folder.rglob('*')):
                        if path.is_file():
                            tree[str(path.relative_to(folder.parent)).replace(f'f{hash_seed}', 'f')
                                 .replace(f'k{hash_seed}', 'k')] = hashlib.sha256(path.read_bytes()).hexdigest()
                summary = json.loads(Path(str(keys) + '.generation.json').read_text())
                summary.pop('python', None)
                tree['summary'] = json.dumps(summary, sort_keys=True)
                trees.append(tree)
        self.assertGreater(len(trees[0]), 10)
        self.assertEqual(trees[0], trees[1])
        self.assertEqual(json.loads(trees[0]['summary'])['scale'], 12)

    def test_building_twice_gives_the_same_case(self):
        self.assertEqual(built(2, 12), generate.build_case('defaults', 2, 1, 12))
        self.assertNotEqual(built(1, 4)['files'], built(2, 4)['files'])


if __name__ == '__main__':
    unittest.main()
