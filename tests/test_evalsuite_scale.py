"""Checks the shared part of `--scale N` in the evalsuite generator.

Scale 1 must stay byte-identical to the generator before the option existed, so `ScaleOneTests` compares
every family, seeds 1 to 10 and indexes 1 and 2 with hashes written from the unchanged generator
(`tests/data/evalsuite-scale1-golden.json`). The other classes check what does not depend on a family: the
refusals, the helpers, and the scaled path of `build_case` and `write_suite`, through a small stand-in
module defined below and registered for the length of a test: `RefusalTests` and `ScaledPathTests` run the
scaled path through it. Where they name a real family (a refused scale, or scale 1) they never call that
family's scaled builder. `SummaryShapeTests` writes real dossiers, at scale 1 of every family and at scales 2
and 4 of the six scaled families, to read the keys of the generation summary. `DownstreamTests`, last in this
file, generates scaled dossiers of the real families (one of each of the six at scale 12, and a `conflict`
dossier at scale 30) and runs them through the real runner, sealer, scorer and comparison. The
independent parser of each family, and the counts and merge checks of each family at every scale, are in the
other files `tests/test_evalsuite_scale_*.py`.
"""
import hashlib
import json
import math
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
import generate  # noqa: E402

TEST_ROOT = ROOT / 'tmp'
TEST_ROOT.mkdir(exist_ok=True)
TEST_RUN = Path(tempfile.mkdtemp(prefix='evalsuite-scale-tests-', dir=TEST_ROOT))
GOLDEN = ROOT / 'tests/data/evalsuite-scale1-golden.json'
GENERATE = ROOT / 'evalsuite/generate.py'
ALL_FAMILIES = ','.join(generate.family_names())


def fresh_folder():
    return Path(tempfile.mkdtemp(dir=TEST_RUN))


def tree(root):
    """Every file under a folder, by relative path, with its bytes."""
    root = Path(root)
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob('*')) if path.is_file()}


def run_cli(*arguments):
    return subprocess.run([sys.executable, str(GENERATE), *arguments], capture_output=True, text=True, check=False)


class ScaleOneTests(unittest.TestCase):
    """Scale 1 is the generator as it was: nothing is added, nothing changes."""

    def test_golden_hashes_cover_every_family(self):
        golden = json.loads(GOLDEN.read_text(encoding='utf-8'))
        expected = {f'{family}:{seed}:{index}' for family in generate.family_names()
                    for seed in range(1, 11) for index in (1, 2)}
        self.assertEqual(len(generate.family_names()), 16)
        self.assertEqual(len(golden), 320)
        self.assertEqual(set(golden), expected)

    def test_build_case_matches_the_golden_hashes(self):
        golden = json.loads(GOLDEN.read_text(encoding='utf-8'))
        mismatched = []
        for name, digest in sorted(golden.items()):
            family, seed, index = name.split(':')
            case = generate.build_case(family, int(seed), int(index))
            if hashlib.sha256(json.dumps(case, sort_keys=True).encode()).hexdigest() != digest:
                mismatched.append(name)
        self.assertEqual(mismatched, [])

    def test_explicit_scale_one_equals_no_scale(self):
        for family in generate.family_names():
            for seed in (1, 2):
                self.assertEqual(json.dumps(generate.build_case(family, seed, 1, 1), sort_keys=True),
                                 json.dumps(generate.build_case(family, seed, 1), sort_keys=True), family)

    def test_write_suite_scale_one_equals_default(self):
        families = generate.family_names()
        plain, explicit = fresh_folder(), fresh_folder()
        summary_plain = generate.write_suite(plain / 'fixtures', plain / 'keys', 3, 16, families)
        summary_explicit = generate.write_suite(explicit / 'fixtures', explicit / 'keys', 3, 16, families, scale=1)
        self.assertEqual(summary_plain, summary_explicit)
        self.assertEqual(tree(plain), tree(explicit))
        self.assertEqual(len(tree(plain / 'fixtures')) > 16, True)

    def test_scale_one_records_nothing_about_scale(self):
        folder = fresh_folder()
        summary = generate.write_suite(folder / 'fixtures', folder / 'keys', 4, 16, generate.family_names(), scale=1)
        self.assertNotIn('scale', summary)
        for dossier, entry in summary['dossiers'].items():
            self.assertNotIn('scale', entry)
            self.assertNotRegex(dossier, r'-x\d+$')
        keys = sorted((folder / 'keys').glob('*.json'))
        self.assertEqual(len(keys), 16)
        for path in keys:
            key = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(sorted(key['generator']), ['family', 'index', 'seed', 'version'])
            for row in key['expectations']:
                self.assertNotIn('block', row)
                self.assertRegex(row['claim_id'], r'^C\d\d$')

    def test_command_line_scale_one_equals_no_flag(self):
        plain, explicit = fresh_folder(), fresh_folder()
        for folder, extra in ((plain, []), (explicit, ['--scale', '1'])):
            done = run_cli('--fixtures', str(folder / 'fixtures'), '--keys', str(folder / 'keys'), '--seed', '5',
                           '--dossiers', '16', '--families', ALL_FAMILIES, *extra)
            self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(tree(plain), tree(explicit))
        self.assertTrue((plain / 'keys.generation.json').exists())
        self.assertNotIn('scale', json.loads((explicit / 'keys.generation.json').read_text(encoding='utf-8')))


class SummaryShapeTests(unittest.TestCase):
    """The generation summary (`keys.generation.json`): its keys are pinned, at scale 1 as the unchanged
    generator wrote them (commit 3e70aa40) and at scale above 1 with `scale` added to the summary and to every
    dossier entry, and nothing else. `lure_claims` is the one key that depends on the dossiers: it is written
    exactly when some key row names a flawed reader in `lure_states`."""

    SUMMARY_KEYS = {'schema_version', 'generator_version', 'seed', 'python', 'families', 'dossiers', 'claims',
                    'expected_states', 'injected_errors', 'echo_claims'}
    DOSSIER_KEYS = {'family', 'files', 'claims_per_report'}

    def written(self, families, scale, dossiers):
        """The summary `write_suite` returns, whether any key row of the folder has `lure_states`, and the summary
        file that the command line writes for the same arguments."""
        folder = fresh_folder()
        summary = generate.write_suite(folder / 'fixtures', folder / 'keys', 4, dossiers, families, scale=scale)
        rows = [row for path in (folder / 'keys').glob('*.json')
                for row in json.loads(path.read_text(encoding='utf-8'))['expectations']]
        lured = any(row.get('lure_states') for row in rows)
        other = fresh_folder()
        done = run_cli('--fixtures', str(other / 'fixtures'), '--keys', str(other / 'keys'), '--seed', '4',
                       '--dossiers', str(dossiers), '--families', ','.join(families), '--scale', str(scale))
        self.assertEqual(done.returncode, 0, done.stderr)
        return summary, lured, json.loads((other / 'keys.generation.json').read_text(encoding='utf-8'))

    def check_shape(self, families, scale, dossiers, want_lured):
        summary, lured, from_file = self.written(families, scale, dossiers)
        self.assertEqual(lured, want_lured, 'the case is meant to exercise the lure_claims branch')
        top = self.SUMMARY_KEYS | ({'lure_claims'} if lured else set()) | ({'scale'} if scale > 1 else set())
        entry = self.DOSSIER_KEYS | ({'scale'} if scale > 1 else set())
        for label, found in (('write_suite', summary), ('the summary file', from_file)):
            self.assertEqual(set(found), top, (label, scale))
            self.assertEqual(len(found['dossiers']), dossiers, (label, scale))
            for name, one in found['dossiers'].items():
                self.assertEqual(set(one), entry, (label, scale, name))
            if scale > 1:
                self.assertEqual(found['scale'], scale)
                self.assertEqual({one['scale'] for one in found['dossiers'].values()}, {scale})

    def test_scale_one_summary_of_the_default_families(self):
        self.assertEqual(list(generate.FAMILIES), ['ledger', 'deadlines', 'approvals', 'packet'])
        self.check_shape(list(generate.FAMILIES), 1, 8, False)

    def test_scale_one_summary_of_every_family_adds_the_lure_count(self):
        self.check_shape(generate.family_names(), 1, 16, True)

    def test_scaled_summaries_add_scale_to_the_summary_and_to_every_dossier(self):
        scaled = list(generate.SCALED_FAMILIES)
        self.assertEqual(sorted(scaled), sorted(['ledger', 'approvals', 'tables', 'conflict', 'earlier', 'defaults']))
        for scale in (2, 4):
            self.check_shape(scaled, scale, len(scaled), True)


class HelperTests(unittest.TestCase):
    """The shared name pools and the merge helper."""

    def test_name_pools_hold_the_listed_values(self):
        suffixes = ('gate field wick ford holm stead mere combe thorpe by ham ton ley worth dale well bridge mouth '
                    'side wood cliff haven moor bank hurst croft garth shaw firth head')
        places = ('Northgate Southmere Eastbrook Westfield Highmoor Lowfold Kingsmead Queensway Abbotsham Brackley '
                  'Calder Denholm Easby Farndale Glaisdale Hawkser Ingleby Kildale Lealholm Marske Newby Ormsby '
                  'Pickton Rosedale Skelton Thornaby Ugthorpe Westerdale Yarm Aislaby')
        given = 'Ada Bo Cy Di Ed Fay Gus Ivy Jon Kit Lou Mae Ned Oz Pia'
        surnames = 'Brandt Castell Dorn Ebner Farrow Gould Hale Jansen Keane Lowry Marlow Nash Orme Pryce Quint'
        for pool, listed, size in ((generate.NAME_SUFFIXES, suffixes, 30), (generate.PLACE_STEMS, places, 30),
                                   (generate.GIVEN_NAMES, given, 15), (generate.SURNAMES, surnames, 15)):
            self.assertEqual(list(pool), listed.split())
            self.assertEqual(len(pool), size)
            self.assertEqual(len(set(pool)), size)
        for pool in (generate.PLACE_STEMS, generate.GIVEN_NAMES, generate.SURNAMES):
            for name in pool:
                self.assertEqual(len(name.split()), 1)

    def test_compound_names(self):
        self.assertEqual(generate.compound_names(['Alder', 'Birch'], ['gate', 'field']),
                         ['Aldergate', 'Alderfield', 'Birchgate', 'Birchfield'])
        self.assertEqual(generate.compound_names([], ['gate']), [])
        names = generate.compound_names(generate.PLACE_STEMS, generate.NAME_SUFFIXES)
        self.assertEqual(len(names), 900)

    def test_interleave_keeps_every_item_and_each_order(self):
        sequences = [[f'a{i}' for i in range(5)], [f'b{i}' for i in range(3)], [], [f'd{i}' for i in range(7)]]
        original = [list(sequence) for sequence in sequences]
        for seed in range(20):
            merged = generate.interleave(random.Random(seed), sequences)
            self.assertEqual(len(merged), 15)
            for letter, sequence in zip('abcd', original):
                self.assertEqual([item for item in merged if item[0] == letter], sequence)
        self.assertEqual(sequences, original)
        self.assertEqual(generate.interleave(random.Random(0), []), [])
        self.assertEqual(generate.interleave(random.Random(0), [[], []]), [])
        self.assertEqual(generate.interleave(random.Random(0), [[1, 2, 3]]), [1, 2, 3])

    def test_interleave_is_deterministic_and_does_not_sort_blocks(self):
        sequences = [[(block, i) for i in range(8)] for block in range(6)]
        first = generate.interleave(random.Random(7), sequences)
        self.assertEqual(first, generate.interleave(random.Random(7), sequences))
        self.assertNotEqual(first, sorted(first))
        self.assertNotEqual(first, generate.interleave(random.Random(8), sequences))

    def test_interleave_draws_in_proportion_to_what_is_left(self):
        # One item against three: proportional picking puts the single item at each of the four places equally.
        places = [0] * 4
        for seed in range(4000):
            merged = generate.interleave(random.Random(seed), [['x'], ['y', 'y', 'y']])
            places[merged.index('x')] += 1
        for count in places:
            self.assertTrue(850 <= count <= 1150, places)

    def test_interleave_draws_one_number_per_item(self):
        class Counting:
            def __init__(self):
                self.draws = []

            def randrange(self, stop):
                self.draws.append(stop)
                return 0

        rng = Counting()
        generate.interleave(rng, [[1, 2], [3]])
        self.assertEqual(rng.draws, [3, 2, 1])


# ---------- A stand-in scaled family ----------

def standin_module(per_block=9, max_scale=30, short=False):
    """A scaled family module for the tests: block b holds `per_block` entries with a known verdict for each claim.

    A true claim states an entry's value (supported). One altered wording states another value (refuted).
    The other holds only when the block's unknown is zero (undetermined). The unknown of block b has b + 1
    values, so a block's own worlds are b + 1, and the product over twelve blocks is far past MAX_WORLDS.
    Block 1 has a told world, block 2 a reader, block 3 asks for open claims and block 4 has none of them.
    """
    def build_scaled(rng, scale):
        blocks, sections = [], []  # sections: one list of entry sections per block, merged below
        for b in range(1, scale + 1):
            block_rng = random.Random(rng.getrandbits(64))
            base, slots, unknown = {}, [], f'u{b:02d}'
            sections.append([])
            for k in range(per_block):
                key, name = f'v{b:02d}_{k:02d}', f'E{b:02d}.{k:02d}'
                value = 1000 * b + 10 * k + block_rng.randrange(10)
                base[key] = value
                cite = [('merged.md', f'A{b:02d}-{k:02d}')]
                sections[-1].append(generate.section(f'A{b:02d}-{k:02d}', f'Entry {name}', f'Entry {name} is entered.'))
                true = generate.claim('entry', f'Entry {name} holds {value}.',
                                      lambda w, key=key, value=value: w[key] == value, cite)
                wrong = generate.claim('entry_wrong', f'Entry {name} holds {value + 1}.',
                                       lambda w, key=key, value=value: w[key] == value + 1, cite, error='wrong_value')
                opened = generate.claim('entry_open', f'Entry {name} holds {value} when unit {b:02d} is zero.',
                                        lambda w, key=key, value=value, unknown=unknown:
                                        w[key] == value and w[unknown] == 0,
                                        cite, error='asserts_unknown_as_fact')
                slots.append((true, [wrong, opened]))
            block = {'base': base, 'unknowns': {unknown: tuple(range(b + 1))}, 'slots': slots, 'rng': block_rng}
            if b == 1:
                block['told'] = {'base': dict(base, **{'v01_00': -1, unknown: 0}), 'unknowns': {}}
            if b == 2:
                block['readers'] = {'stub': {'base': dict(base, **{unknown: 0}), 'unknowns': {}}}
            if b == 3:
                block['open_claims'] = 2
            blocks.append(block)
        text = generate.document('Stand-in merged entries', generate.interleave(rng, sections))
        return {'title': 'Stand-in review', 'sources': {'merged.md': text},
                'preamble': 'Every statement below is about one entry.', 'reader': 'stand-in reader',
                'blocks': blocks[:-1] if short else blocks}

    module = types.ModuleType('scaled_standin')
    module.MAX_SCALE = max_scale
    module.build_scaled = build_scaled
    module.PER_BLOCK = per_block
    return module


def report_claims(text):
    """Identifier and text of every claim of a report, in order."""
    return re.findall(r'^<a id="(C\d+)"></a>\n\*\*C\d+\.\*\* (.*) Citations?: ', text, flags=re.M)


def expected_state(kind):
    return {'entry': 'supported', 'entry_wrong': 'refuted', 'entry_open': 'undetermined'}[kind]


def expected_echo(kind, entry):
    """What the told world of block 1 (entry 0 holds -1; the unknown is zero) makes of a claim, where it differs."""
    if kind == 'entry':
        return 'refuted' if entry == 0 else None
    if kind == 'entry_open':
        return 'refuted' if entry == 0 else 'supported'
    return None


def entry_of(text):
    match = re.match(r'Entry E(\d\d)\.(\d\d) ', text)
    return int(match.group(1)), int(match.group(2))


class StandInCase(unittest.TestCase):
    def install(self, module):
        for patch in (mock.patch.dict(generate.SCALED_FAMILIES, {'standin': 'scaled_standin'}),
                      mock.patch.dict(sys.modules, {'scaled_standin': module})):
            patch.start()
            self.addCleanup(patch.stop)

    def setUp(self):
        self.install(standin_module())


class RefusalTests(StandInCase):
    """Criterion 9: a refused run is refused before any folder exists."""

    def test_family_without_support_is_refused(self):
        with self.assertRaisesRegex(generate.GenerationError, 'Family truncated does not support --scale 4'):
            generate.build_case('truncated', 1, 1, 4)
        for name in ('truncated', 'uncited', 'premise', 'no-such-family'):
            with self.assertRaisesRegex(generate.GenerationError, 'does not support --scale 7'):
                generate.scaled_builder(name, 7)
        with self.assertRaisesRegex(generate.GenerationError, 'does not support --scale$'):
            generate.scaled_builder('truncated')

    def test_missing_module_or_attribute_is_the_same_refusal(self):
        with mock.patch.dict(generate.SCALED_FAMILIES, {'ghost': 'scaled_no_such_module_anywhere'}):
            with self.assertRaisesRegex(generate.GenerationError, 'Family ghost does not support --scale 3$'):
                generate.scaled_builder('ghost', 3)
        no_builder, no_limit = types.ModuleType('scaled_bare'), types.ModuleType('scaled_unlimited')
        no_builder.MAX_SCALE = 5
        no_limit.build_scaled = lambda rng, scale: {}
        for name, module in (('bare', no_builder), ('unlimited', no_limit)):
            with mock.patch.dict(generate.SCALED_FAMILIES, {name: module.__name__}), \
                    mock.patch.dict(sys.modules, {module.__name__: module}):
                with self.assertRaisesRegex(generate.GenerationError, f'Family {name} does not support --scale 3$'):
                    generate.scaled_builder(name, 3)

    def test_write_suite_checks_every_family_before_creating_a_folder(self):
        for families in (['truncated'], ['standin', 'truncated'], ['truncated', 'standin']):
            folder = fresh_folder()
            with self.assertRaisesRegex(generate.GenerationError, 'does not support --scale 4'):
                generate.write_suite(folder / 'fixtures', folder / 'keys', 1, 3, families, scale=4)
            self.assertFalse((folder / 'fixtures').exists())
            self.assertFalse((folder / 'keys').exists())

    def test_scale_above_the_limit_is_refused(self):
        self.install(standin_module(max_scale=5))
        folder = fresh_folder()
        with self.assertRaisesRegex(generate.GenerationError, 'Family standin does not support --scale 6'):
            generate.write_suite(folder / 'fixtures', folder / 'keys', 1, 2, ['standin'], scale=6)
        self.assertFalse((folder / 'fixtures').exists())
        self.assertFalse((folder / 'keys').exists())
        with self.assertRaisesRegex(generate.GenerationError, 'does not support --scale 6'):
            generate.build_case('standin', 1, 1, 6)
        case = generate.build_case('standin', 1, 1, 5)
        self.assertEqual(case['key']['generator']['scale'], 5)
        generate.write_suite(folder / 'fixtures', folder / 'keys', 1, 2, ['standin'], scale=5)
        self.assertTrue((folder / 'fixtures').exists())

    def test_zero_and_negative_scale_are_refused(self):
        for scale in (0, -1, -30):
            folder = fresh_folder()
            with self.assertRaisesRegex(generate.GenerationError, '--scale needs a positive number'):
                generate.write_suite(folder / 'fixtures', folder / 'keys', 1, 2, ['ledger'], scale=scale)
            with self.assertRaisesRegex(generate.GenerationError, '--scale needs a positive number'):
                generate.build_case('ledger', 1, 1, scale)
            self.assertFalse((folder / 'fixtures').exists())
            self.assertFalse((folder / 'keys').exists())

    def test_command_line_refusals_exit_one_and_write_nothing(self):
        cases = (('4', 'truncated', 'does not support --scale 4'), ('4', 'truncated,ledger', 'does not support --scale 4'),
                 ('0', 'ledger', '--scale needs a positive number'), ('-1', 'ledger', '--scale needs a positive number'))
        for scale, families, message in cases:
            folder = fresh_folder()
            done = run_cli('--fixtures', str(folder / 'fixtures'), '--keys', str(folder / 'keys'), '--seed', '1',
                           '--dossiers', '2', '--families', families, '--scale', scale)
            self.assertEqual(done.returncode, 1, (scale, families, done.stdout, done.stderr))
            self.assertIn('Cannot generate: ', done.stderr)
            self.assertIn(message, done.stderr)
            self.assertEqual(sorted(path.name for path in folder.iterdir()), [], (scale, families))

    def test_command_line_rejects_a_scale_that_is_not_a_number(self):
        folder = fresh_folder()
        done = run_cli('--fixtures', str(folder / 'fixtures'), '--keys', str(folder / 'keys'), '--seed', '1',
                       '--scale', 'many')
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(sorted(path.name for path in folder.iterdir()), [])


class ScaledPathTests(StandInCase):
    """The scaled path of `build_case` and `write_suite`, through the stand-in family."""

    def build(self, scale, seed=1, index=1):
        return generate.build_case('standin', seed, index, scale)

    def rows(self, case, variant):
        return [row for row in case['key']['expectations'] if row['variant'] == variant]

    def test_identifier_format_and_width(self):
        for scale, count, last in ((3, 27, 'C27'), (4, 36, 'C36'), (11, 99, 'C99'), (12, 108, 'C108'), (30, 270, 'C270')):
            case = self.build(scale)
            width = 2 if count < 100 else 3
            expected = [f'C{n:0{width}d}' for n in range(1, count + 1)]
            correct = report_claims(case['files']['report-correct.md'])
            mixed = report_claims(case['files']['report-mixed.md'])
            self.assertEqual([ident for ident, _ in correct], expected, scale)
            self.assertEqual([ident for ident, _ in mixed], expected, scale)
            self.assertEqual(expected[-1], last)
            for variant in ('correct', 'mixed'):
                self.assertEqual([row['claim_id'] for row in self.rows(case, variant)], expected, (scale, variant))
            self.assertEqual(len(case['key']['expectations']), 2 * count)

    def test_reports_and_key_name_the_same_claims(self):
        case = self.build(12)
        for variant in ('correct', 'mixed'):
            claims = report_claims(case['files'][f'report-{variant}.md'])
            rows = self.rows(case, variant)
            self.assertEqual([(row['claim_id'], row['claim_text']) for row in rows], claims)
            for row in rows:
                self.assertEqual(row['block'], entry_of(row['claim_text'])[0])
                self.assertEqual(row['semantic_state'], expected_state(row['claim_kind']))
        for position in range(108):
            correct, mixed = self.rows(case, 'correct')[position], self.rows(case, 'mixed')[position]
            self.assertEqual(entry_of(correct['claim_text']), entry_of(mixed['claim_text']))
            self.assertEqual(correct['block'], mixed['block'])
            self.assertEqual(correct['claim_kind'], 'entry')
        self.assertTrue(all(row['claim_kind'] == 'entry' and row['semantic_state'] == 'supported'
                            and row['error_type'] is None for row in self.rows(case, 'correct')))
        altered = [row for row in self.rows(case, 'mixed') if row['claim_kind'] != 'entry']
        self.assertTrue(12 <= len(altered) <= 108)
        for row in altered:
            self.assertEqual(row['error_type'], {'entry_wrong': 'wrong_value',
                                                 'entry_open': 'asserts_unknown_as_fact'}[row['claim_kind']])

    def test_every_claim_of_every_block_is_there_once_in_a_shuffled_order(self):
        for scale in (4, 12):
            case = self.build(scale)
            entries = [entry_of(text) for _, text in report_claims(case['files']['report-correct.md'])]
            self.assertEqual(sorted(entries), [(b, k) for b in range(1, scale + 1) for k in range(9)])
            blocks = [b for b, _ in entries]
            self.assertNotEqual(blocks, sorted(blocks))
            self.assertNotEqual(entries, [(b, k) for b in range(1, scale + 1) for k in range(9)])

    def test_block_on_every_row_and_scale_in_the_key(self):
        for scale in (2, 4, 12):
            case = self.build(scale)
            key = case['key']
            self.assertEqual(key['generator'], {'version': generate.GENERATOR_VERSION, 'seed': 1, 'index': 1,
                                                'family': 'standin', 'scale': scale, 'blocks': scale})
            for row in key['expectations']:
                self.assertIn(row['block'], range(1, scale + 1))
                self.assertIs(type(row['block']), int)
            for variant in ('correct', 'mixed'):
                counts = {}
                for row in self.rows(case, variant):
                    counts[row['block']] = counts.get(row['block'], 0) + 1
                self.assertEqual(counts, {b: 9 for b in range(1, scale + 1)})

    def test_dossier_name(self):
        case = self.build(4, seed=7, index=3)
        self.assertEqual(case['dossier'], 'g7-003-standin-x4')
        self.assertEqual(case['key']['dossier_id'], 'g7-003-standin-x4')
        self.assertEqual(case['family'], 'standin')
        self.assertRegex(case['dossier'], r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
        self.assertEqual(sorted(case['files']), ['report-correct.md', 'report-mixed.md', 'sources/merged.md'])

    def test_each_block_is_judged_over_its_own_worlds(self):
        scale = 12
        self.assertGreater(math.prod(b + 1 for b in range(1, scale + 1)), generate.MAX_WORLDS)
        case = self.build(scale)
        for row in case['key']['expectations']:
            block = row['block']
            true_in = {'entry': block + 1, 'entry_wrong': 0, 'entry_open': 1}[row['claim_kind']]
            self.assertEqual(row['derivation']['worlds'], block + 1)
            self.assertEqual(row['derivation']['worlds_where_true'], true_in)

    def test_family_facts_reach_only_the_rows_of_their_block(self):
        case = self.build(12)
        for row in case['key']['expectations']:
            block, entry = entry_of(row['claim_text'])
            kind = row['claim_kind']
            if block == 1:
                told = expected_echo(kind, entry)
                self.assertEqual(row['echo_state'], told, row['claim_text'])
                self.assertEqual('echo_reader' in row, told is not None)
                if told:
                    self.assertEqual(row['echo_reader'], 'stand-in reader')
                    self.assertEqual(row['echo_derivation'], {'worlds': 1, 'worlds_where_true':
                                                              1 if told == 'supported' else 0})
            else:
                self.assertIsNone(row['echo_state'])
                self.assertIsNone(row['echo_derivation'])
                self.assertNotIn('echo_reader', row)
            if block == 2:
                self.assertEqual(row['lure_states'], {'stub': 'supported'} if kind == 'entry_open' else {})
                self.assertEqual(row['lure_derivations'], {'stub': {'worlds': 1, 'worlds_where_true': 1}}
                                 if kind == 'entry_open' else {})
            else:
                self.assertNotIn('lure_states', row)
                self.assertNotIn('lure_derivations', row)
        for variant in ('correct', 'mixed'):
            text = self.build(3)['files'][f'report-{variant}.md']
            self.assertIn('Every statement below is about one entry.', text)
        self.assertTrue(any(row['echo_state'] for row in case['key']['expectations']))
        self.assertTrue(any(row.get('lure_states') for row in case['key']['expectations']))

    def test_choose_runs_on_each_blocks_own_random_source_and_nothing_draws_before_it(self):
        seed, index, scale = 5, 2, 6
        case = self.build(scale, seed, index)
        rng = random.Random(f'evalsuite:{generate.GENERATOR_VERSION}:{seed}:{index}:standin:scale{scale}')
        built = standin_module().build_scaled(rng, scale)
        for number, block in enumerate(built['blocks'], 1):
            correct, mixed = generate.choose(block, block['rng'])
            for variant, items in (('correct', correct), ('mixed', mixed)):
                texts = sorted(row['claim_text'] for row in self.rows(case, variant) if row['block'] == number)
                self.assertEqual(texts, sorted(item['text'] for item in items), (number, variant))
        # Block 3 asked for open claims: at least two of its altered claims are ones the sources leave open.
        opened = [row for row in self.rows(case, 'mixed') if row['block'] == 3 and row['semantic_state'] == 'undetermined']
        self.assertGreaterEqual(len(opened), 2)

    def test_cited_anchors_exist_in_the_merged_sources(self):
        case = self.build(12)
        merged = case['files']['sources/merged.md']
        for row in case['key']['expectations']:
            self.assertEqual(len(row['source_anchors']), 1)
            name, anchor = row['source_anchors'][0].split('#')
            self.assertEqual(name, 'sources/merged.md')
            self.assertIn(f'<a id="{anchor}"></a>', merged)
            self.assertEqual(anchor, f'A{row["block"]:02d}-{entry_of(row["claim_text"])[1]:02d}')

    def test_write_suite_records_the_scale_and_the_dossier_folder(self):
        folder = fresh_folder()
        summary = generate.write_suite(folder / 'fixtures', folder / 'keys', 3, 3, ['standin'], scale=12)
        self.assertEqual(summary['scale'], 12)
        self.assertEqual(sorted(summary['dossiers']), ['g3-001-standin-x12', 'g3-002-standin-x12', 'g3-003-standin-x12'])
        for dossier, entry in summary['dossiers'].items():
            self.assertEqual(entry['scale'], 12)
            self.assertEqual(entry['claims_per_report'], 108)
            self.assertEqual(entry['family'], 'standin')
            self.assertTrue((folder / 'fixtures' / dossier / 'sources' / 'merged.md').is_file())
            for name, digest in entry['files'].items():
                text = (folder / 'fixtures' / dossier / name).read_text(encoding='utf-8')
                self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), digest)
            key = json.loads((folder / 'keys' / f'{dossier}.json').read_text(encoding='utf-8'))
            self.assertEqual(key['generator']['scale'], 12)
            self.assertEqual(key['generator']['blocks'], 12)
            self.assertEqual(len(key['expectations']), 216)
        self.assertEqual(sorted(path.name for path in (folder / 'fixtures').iterdir()), sorted(summary['dossiers']))
        self.assertEqual(summary['claims'], 3 * 216)
        # Nothing about the scale is written into the fixtures: only a reviewer's files.
        for path in (folder / 'fixtures').rglob('*'):
            if path.is_file():
                self.assertNotIn('scale', path.read_text(encoding='utf-8').lower())

    def test_two_runs_give_identical_output(self):
        outputs = []
        for _ in range(2):
            folder = fresh_folder()
            summary = generate.write_suite(folder / 'fixtures', folder / 'keys', 9, 4, ['standin'], scale=12)
            outputs.append((summary, tree(folder)))
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(json.dumps(self.build(12, 2, 2), sort_keys=True), json.dumps(self.build(12, 2, 2), sort_keys=True))
        self.assertNotEqual(json.dumps(self.build(12, 2, 2), sort_keys=True), json.dumps(self.build(12, 2, 3), sort_keys=True))
        self.assertNotEqual(json.dumps(self.build(12, 2, 2), sort_keys=True), json.dumps(self.build(12, 3, 2), sort_keys=True))
        self.assertNotEqual(self.build(4)['files']['report-correct.md'], self.build(5)['files']['report-correct.md'])

    def test_a_builder_that_returns_the_wrong_number_of_blocks_is_refused(self):
        self.install(standin_module(short=True))
        with self.assertRaisesRegex(generate.GenerationError, 'built 3 blocks for --scale 4'):
            self.build(4)

    def test_scale_one_of_a_registered_family_still_takes_the_plain_path(self):
        case = generate.build_case('ledger', 1, 1, 1)
        self.assertEqual(case['dossier'], 'g1-001-ledger')
        self.assertNotIn('block', case['key']['expectations'][0])


# ---------- Downstream: the unchanged runner, sealer, scorer and comparison ----------
# Acceptance criterion 10. A scaled dossier must go through the tools that never learned about `--scale`.
# The stand-in reviewers may read the keys (a test has to know what a right or a wrong sheet is); the runner
# does not. Every expected number is computed here from the key rows, not from `scorecard.py` or `compare.py`.

import contextlib  # noqa: E402
import io  # noqa: E402
sys.path.insert(0, str(ROOT / 'tests'))
import compare  # noqa: E402
import run_blind  # noqa: E402
import scorecard  # noqa: E402
from evalsuite_campaign_fixture import Campaign, perfect, sheet  # noqa: E402

FIRST_SET = ['ledger', 'approvals', 'tables', 'conflict', 'earlier', 'defaults']
TRAP_FAMILIES = ['tables', 'conflict', 'earlier', 'defaults']
LURE_MEASURES = ('echo_asserted', 'echo_withheld', 'wrong_definite_without_conflict', 'withheld_without_conflict')
DEFINITE = ('supported', 'refuted')


def lure_states_of(row):
    """Every named reader's verdict on this row; a reader named in both fields counts once."""
    states = dict(row.get('lure_states', {}))
    if row.get('echo_reader'):
        states[row['echo_reader']] = row['echo_state']
    return states


def reader_verdict(row, reader):
    """What the named reader answers on this row: its own verdict where it has one, the key's elsewhere."""
    if row.get('echo_reader') == reader and row.get('echo_state') is not None:
        return row['echo_state']
    return row.get('lure_states', {}).get(reader) or row['semantic_state']


def key_verdict(row):
    return row['semantic_state']


def expected_counts(rows, answer):
    """(hits, of) on every lure measure for a reviewer who gives `answer(row)` on each row, from the key rows alone."""
    asserting = {row['claim_kind'] for row in rows if any(
        state in DEFINITE for state in (row.get('echo_state'), *row.get('lure_states', {}).values()))}
    withholding = {row['claim_kind'] for row in rows if 'undetermined' in (
        row.get('echo_state'), *row.get('lure_states', {}).values())}
    plain_rows = [row for row in rows if row.get('echo_state') is None and not row.get('lure_states')]
    told = {'echo_asserted': [r for r in rows if r.get('echo_state') in DEFINITE],
            'echo_withheld': [r for r in rows if r.get('echo_state') == 'undetermined']}
    result = {name: (sum(answer(r) == r['echo_state'] for r in chosen), len(chosen)) for name, chosen in told.items()}
    wrong = [r for r in plain_rows if r['claim_kind'] in asserting]
    result['wrong_definite_without_conflict'] = (
        sum(answer(r) in DEFINITE and answer(r) != r['semantic_state'] for r in wrong), len(wrong))
    caution = [r for r in plain_rows if r['claim_kind'] in withholding and r['semantic_state'] != 'undetermined']
    result['withheld_without_conflict'] = (sum(answer(r) == 'undetermined' for r in caution), len(caution))
    result['accuracy'] = (sum(answer(r) == r['semantic_state'] for r in rows), len(rows))
    readers = {}
    for row in rows:
        for name, state in lure_states_of(row).items():
            block = readers.setdefault(name, {'asserted': [0, 0], 'withheld': [0, 0]})
            pair = block['withheld' if state == 'undetermined' else 'asserted']
            pair[0] += answer(row) == state
            pair[1] += 1
    result['readers'] = {name: {kind: tuple(pair) for kind, pair in block.items()}
                         for name, block in sorted(readers.items())}
    return result


class ScaledCampaign(Campaign):
    """The shared campaign fixture over scaled dossiers: one dossier per family, at one scale.

    The fixture's constructor writes the suite with `list(generate.FAMILIES)`, so the one call that makes the
    suite is replaced by a call for these families and this scale. Nothing else of the fixture is changed."""

    def __init__(self, root, families, scale, seed):
        real = generate.write_suite
        with mock.patch.object(generate, 'write_suite',
                               lambda fixtures, keys, number, dossiers, listed: real(
                                   fixtures, keys, number, dossiers, families, scale)):
            super().__init__(root, dossiers=len(families), seed=seed)
        self.scale, self.rows, self.family, self.dossier, self.reader = scale, {}, {}, {}, {}
        for path in sorted(self.draft.glob('*.json')):
            key = json.loads(path.read_text(encoding='utf-8'))
            family = key['generator']['family']
            self.rows[path.stem] = [dict(row, family=family) for row in key['expectations']]
            self.family[path.stem], self.dossier[family] = family, path.stem
            names = {row['echo_reader'] for row in key['expectations'] if row.get('echo_reader')}
            if names:
                self.reader[family] = names.pop() if len(names) == 1 else sorted(names)[0]

    def all_rows(self):
        return [row for name, rows in sorted(self.rows.items()) for row in rows]

    def score(self, arm):
        """The real scorer on this arm's bundles."""
        base = self.out / arm
        index = json.loads((base / 'records/index.json').read_text(encoding='utf-8'))
        return scorecard.score(base / 'runs', self.keys, index=index, scan=False)

    def compare(self, baseline, candidate):
        keys = scorecard.load_keys(self.keys)
        reviews = {arm: compare.arm_reviews(self.out, arm, keys) for arm in (baseline, candidate)}
        return reviews, compare.compare(reviews[baseline], reviews[candidate], bootstrap=1000)


def follows_each_flawed_reader(campaign):
    """Answers each trap family as its named flawed reader does, and the other families by the key."""
    def policy(call):
        reader = campaign.reader.get(campaign.family[call['dossier']])
        return {'sheet': sheet(call, (lambda row: reader_verdict(row, reader)) if reader else None)}
    return policy


class DownstreamTests(unittest.TestCase):
    """Criterion 10: one scale-12 dossier of each first-set family, and one scale-30 dossier, through the real
    runner, sealer, scorer and comparison, with a perfect reviewer and with the flawed readers."""

    @classmethod
    def setUpClass(cls):
        patch = mock.patch.object(run_blind, 'toolchain_python', lambda *args, **kwargs: None)
        patch.start()
        cls.addClassCleanup(patch.stop)
        cls.seed = cls.find_seed()
        cls.twelve = ScaledCampaign(fresh_folder(), FIRST_SET, 12, cls.seed)
        with contextlib.redirect_stdout(io.StringIO()):         # the runner reports every review it ends
            cls.status12 = cls.twelve.run(lambda call: {'plain': perfect, 'skill-no-lean': follows_each_flawed_reader(
                cls.twelve)}[call['arm']](call), arms='plain,skill-no-lean')
        cls.thirty = ScaledCampaign(fresh_folder(), ['conflict'], 30, cls.seed)
        with contextlib.redirect_stdout(io.StringIO()):
            cls.status30 = cls.thirty.run(perfect, arms='plain')

    @staticmethod
    def find_seed(limit=200):
        """The first seed whose scale-12 dossiers give every trap family a flawed-reader row that asserts a verdict,
        so that the trap measure is not checked over an empty set. (No trap family of this set has an echo that
        withholds, at scale 1 or at scale 12, so that measure is checked for equality with the keys and no more.)"""
        for seed in range(1, limit):
            suite = {name: generate.build_case(name, seed, number, 12)['key']['expectations']
                     for number, name in enumerate(FIRST_SET, 1) if name in TRAP_FAMILIES}
            rows = [row for part in suite.values() for row in part]
            if all(any(row.get('echo_state') in DEFINITE for row in part) for part in suite.values()) and rows:
                return seed
        raise AssertionError('No seed gives every trap family a flawed-reader row at scale 12')

    def assertPair(self, block, pair, where=''):
        self.assertEqual((block['count'], block['of']), pair, where)

    def assertMeasures(self, card, want, where):
        """Every lure measure of the scorecard, and every reader's line, equals what the key rows imply."""
        block = card['semantic']['all_claims']
        for name in LURE_MEASURES + ('accuracy',):
            self.assertPair(block[name], want[name], f'{where}: {name}')
        readers = card['semantic']['lures_by_reader']
        self.assertEqual(sorted(readers), sorted(want['readers']), where)
        for name, kinds in want['readers'].items():
            for kind, pair in kinds.items():
                self.assertPair(readers[name][kind], pair, f'{where}: {name} {kind}')

    def assertCoverage(self, card, where):
        self.assertEqual({name: value for name, value in card['coverage'].items() if value}, {},
                         f'{where}: coverage is not complete')

    # ----- the suite and the runner -----

    def test_the_scaled_suite_is_what_the_campaign_runs_on(self):
        campaign = self.twelve
        self.assertEqual(sorted(campaign.family.values()), sorted(FIRST_SET))
        for dossier, rows in campaign.rows.items():
            self.assertRegex(dossier, r'^g\d+-\d{3}-[a-z]+-x12$')
            self.assertEqual({row['block'] for row in rows}, set(range(1, 13)), dossier)
            claims = len(rows) // 2
            width = max(2, len(str(claims)))
            self.assertEqual([row['claim_id'] for row in rows[:claims]], [f'C{n:0{width}d}' for n in range(1, claims + 1)])
            self.assertGreaterEqual(claims, 12 * 6, dossier)
        for name in TRAP_FAMILIES:
            rows = campaign.rows[campaign.dossier[name]]
            self.assertTrue(any(row.get('echo_state') for row in rows), f'{name} has no flawed-reader row')
            self.assertIn(name, campaign.reader)
        # `conflict` has a second reader, so its key rows carry `lure_states` as well as an echo; were the readers
        # of a block lost on the way to the key, the counts below would still agree with the keys, but with less.
        self.assertTrue(any(row.get('lure_states') for row in campaign.rows[campaign.dossier['conflict']]))
        for name in ('ledger', 'approvals'):
            self.assertNotIn(name, campaign.reader)
            self.assertFalse(any(row.get('echo_state') or row.get('lure_states')
                                 for row in campaign.rows[campaign.dossier[name]]))
        # The sealer adds a canary to each key and writes a manifest; the rest of every key is as generated.
        sealed = {path.name: json.loads(path.read_text(encoding='utf-8')) for path in campaign.keys.glob('*.json')}
        self.assertIn('KEYS-MANIFEST.json', sealed)
        sealed.pop('KEYS-MANIFEST.json')
        for key in sealed.values():
            self.assertTrue(key.pop('canary'))
        self.assertEqual(sealed, {path.name: json.loads(path.read_text(encoding='utf-8'))
                                  for path in campaign.draft.glob('*.json')})
        # Nothing about the scale is written into what a reviewer is given.
        for path in campaign.fixtures.rglob('*'):
            if path.is_file():
                self.assertNotIn('"scale"', path.read_text(encoding='utf-8'))

    def test_the_runner_ended_with_status_zero_and_every_unit_scored(self):
        for campaign, arms in ((self.twelve, ('plain', 'skill-no-lean')), (self.thirty, ('plain',))):
            reviews = len(campaign.family) * 2 * len(arms)
            self.assertEqual(len(campaign.calls), reviews)
            units = campaign.units()
            self.assertEqual(len(units), reviews)
            self.assertEqual({record['status'] for record in units.values()}, {'scored'})
            for arm in arms:
                bundles = sorted((campaign.out / arm / 'runs').iterdir())
                self.assertEqual(len(bundles), len(campaign.family) * 2)
                for bundle in bundles:
                    self.assertTrue((bundle / 'input/report-ledger.json').is_file(), bundle)
        self.assertEqual((self.status12, self.status30), (0, 0))

    def test_every_review_was_handed_every_claim_of_its_report(self):
        for campaign in (self.twelve, self.thirty):
            for call in campaign.calls:
                text = (campaign.fixtures / call['dossier'] / f'report-{call["variant"]}.md').read_text(encoding='utf-8')
                self.assertEqual(len(call['rows']), text.count('<a id="C'), call['dossier'])
                self.assertEqual(len(call['rows']), len(campaign.rows[call['dossier']]) // 2)

    # ----- a perfect reviewer -----

    def test_a_perfect_reviewer_is_right_on_every_claim_and_reads_zero_on_every_trap_measure(self):
        campaign = self.twelve
        rows = campaign.all_rows()
        card = campaign.score('plain')
        self.assertMeasures(card, expected_counts(rows, key_verdict), 'plain')
        self.assertCoverage(card, 'plain')
        self.assertEqual(card['suite']['claims'], len(rows))
        self.assertEqual(card['suite']['runs_scored'], 2 * len(FIRST_SET))
        block = card['semantic']['all_claims']
        self.assertEqual((block['accuracy']['count'], block['accuracy']['of']), (len(rows), len(rows)))
        self.assertEqual(block['accuracy']['rate'], 1.0)
        for name in LURE_MEASURES:
            self.assertEqual(block[name]['count'], 0, name)
        for reader, kinds in card['semantic']['lures_by_reader'].items():
            for kind, value in kinds.items():
                self.assertEqual(value['count'], 0, f'{reader} {kind}')
        # The zeros are not empty measures: the denominators are the flawed-reader rows of the keys.
        echoes = [row['echo_state'] for row in rows if row.get('echo_state')]
        self.assertEqual(block['echo_asserted']['of'], sum(state in DEFINITE for state in echoes))
        self.assertEqual(block['echo_withheld']['of'], echoes.count('undetermined'))
        self.assertGreater(block['echo_asserted']['of'], 0)

    # ----- the flawed readers -----

    def test_the_scorers_trap_counts_equal_those_computed_from_the_keys(self):
        campaign = self.twelve
        follows = lambda row: reader_verdict(row, campaign.reader.get(row['family']))  # noqa: E731
        rows = campaign.all_rows()
        want = expected_counts(rows, follows)
        card = campaign.score('skill-no-lean')
        self.assertMeasures(card, want, 'skill-no-lean')
        self.assertCoverage(card, 'skill-no-lean')
        block = card['semantic']['all_claims']
        self.assertGreater(block['echo_asserted']['of'], 0)
        self.assertEqual(block['echo_asserted']['count'], block['echo_asserted']['of'])
        self.assertEqual(block['echo_asserted']['rate'], 1.0)
        self.assertEqual(block['echo_withheld']['count'], block['echo_withheld']['of'])
        for name in ('wrong_definite_without_conflict', 'withheld_without_conflict'):
            self.assertEqual(block[name]['count'], 0, name)
        wrong = sum(follows(row) != row['semantic_state'] for row in rows)
        self.assertGreater(wrong, 0)
        self.assertEqual(block['accuracy']['count'], len(rows) - wrong)
        for name in TRAP_FAMILIES:
            for kind, value in card['semantic']['lures_by_reader'][campaign.reader[name]].items():
                self.assertEqual(value['count'], value['of'], f'{name} {kind}')

    # ----- the comparison -----

    def test_the_comparison_counts_every_claim_of_both_arms(self):
        campaign = self.twelve
        follows = lambda row: reader_verdict(row, campaign.reader.get(row['family']))  # noqa: E731
        rows = campaign.all_rows()
        key, flawed = expected_counts(rows, key_verdict), expected_counts(rows, follows)
        reviews, result = campaign.compare('plain', 'skill-no-lean')
        for arm in ('plain', 'skill-no-lean'):
            self.assertEqual(len(reviews[arm]), 2 * len(FIRST_SET), arm)
        for side, want in (('baseline', key), ('candidate', flawed)):
            arm = result[side]
            self.assertPair(arm['accuracy'], want['accuracy'], side)
            self.assertEqual(arm['accuracy']['of'], len(rows), side)
            self.assertPair(arm['echo_asserted'], want['echo_asserted'], side)
            self.assertPair(arm['echo_withheld'], want['echo_withheld'], side)
            self.assertPair(arm['wrong_definite_without_conflict'], want['wrong_definite_without_conflict'], side)
            self.assertPair(arm['withheld_control'], want['withheld_without_conflict'], side)
            self.assertEqual(sorted(arm['lures_by_reader']), sorted(want['readers']), side)
            for reader, kinds in want['readers'].items():
                for kind, pair in kinds.items():
                    self.assertPair(arm['lures_by_reader'][reader][kind], pair, f'{side} {reader} {kind}')
        self.assertEqual(result['claims_only_baseline_right'], len(rows) - flawed['accuracy'][0])
        self.assertEqual(result['claims_only_candidate_right'], 0)
        self.assertEqual(result['difference']['echo_asserted']['value'], 1.0)

    # ----- scale 30 -----

    def test_a_scale_30_dossier_of_a_long_family_holds_through_the_scorer(self):
        campaign = self.thirty
        (dossier,) = campaign.rows
        rows = campaign.rows[dossier]
        self.assertEqual(dossier.split('-')[-2:], ['conflict', 'x30'])
        self.assertEqual(len(rows) // 2, 360)
        self.assertEqual({row['block'] for row in rows}, set(range(1, 31)))
        self.assertEqual(rows[359]['claim_id'], 'C360')
        for call in campaign.calls:
            self.assertEqual(len(call['rows']), 360)
        card = campaign.score('plain')
        self.assertMeasures(card, expected_counts(rows, key_verdict), 'scale 30')
        self.assertCoverage(card, 'scale 30')
        self.assertEqual(card['suite']['claims'], 720)
        self.assertEqual(card['suite']['runs_scored'], 2)
        accuracy = card['semantic']['all_claims']['accuracy']
        self.assertEqual((accuracy['count'], accuracy['of'], accuracy['rate']), (720, 720, 1.0))
        self.assertEqual({record['status'] for record in campaign.units().values()}, {'scored'})
        sheet_sizes = [len(json.dumps(sheet(call))) for call in campaign.calls]
        self.assertLess(max(sheet_sizes), run_blind.SHEET_LIMIT)


if __name__ == '__main__':
    unittest.main()
