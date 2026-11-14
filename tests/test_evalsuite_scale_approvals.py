"""Checks of the approvals family at `--scale N` (departments as blocks).

`Reading` is written apart from the generator: it takes the merged Markdown sources and one report as
text, and decides each claim again by listing every world its own rules allow, as an auditor would. It
imports nothing from the generator and uses no key. Tests compare its verdicts, and the number of worlds
it listed, with the answer key. Agreement cannot show that a rule sentence has no second reading: the
rules are read here the way the generator reads them.
"""
import collections
import itertools
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
import generate  # noqa: E402
import scaled_approvals  # noqa: E402

TEST_ROOT = ROOT / 'tmp'
PREFIX = 'evalsuite-scale-approvals-tests-'
ILLEGIBLE = 'Illegible in the supplied scan'


# ---------- The independent reading of the merged Markdown ----------

def sections(text):
    parts = re.split(r'<a id="([^"]+)"></a>\n', text)
    return dict(zip(parts[1::2], parts[2::2]))


def table_rows(body):
    lines = [line for line in body.splitlines() if line.startswith('|')]
    cells = lambda line: [cell.strip() for cell in line.strip('|').split('|')]  # noqa: E731
    return [dict(zip(cells(lines[0]), cells(line))) for line in lines[2:]]


def over_all(outcomes):
    """Verdict of a statement from its truth in each remaining possibility."""
    return 'supported' if all(outcomes) else 'undetermined' if any(outcomes) else 'refuted'


class Reading:
    """What the sources say, and what each claim of a report comes to."""

    PATTERNS = (
        ('compliance', re.compile(r'Request (Q\d+) (met|did not meet) the approval policy\.')),
        ('tier', re.compile(r'Request (Q\d+) needed (two counting approvals|one counting approval)\.')),
        ('failures', re.compile(r'(Exactly|At least) (\d+) of the (\d+) requests of the (.+ office) did not meet '
                                r'the approval policy\.')),
        ('role', re.compile(r'(.+), who signed request (Q\d+), is an? (.+)\.')),
        ('lookup', re.compile(r'Request (Q\d+) was submitted by (.+) for (\d+) euro\.')),
        ('illegible', re.compile(r'The second signature on request (Q\d+) is illegible in the supplied scan\.')),
    )

    def __init__(self, sources):
        policy = sections(sources['policy.md'])
        limits = re.findall(r'(?:at most|above) (\d+) euro', policy['Q-01'] + policy['Q-02'])
        self.limit_values = set(limits)
        self.limit = int(limits[0])
        staff = sections(sources['staff.md'])
        self.staff_rows = table_rows(staff['S-01'])
        self.signature_rule = staff['S-02'].strip().splitlines()[-1]
        self.role = {r['Person']: r['Role'] for r in self.staff_rows}
        self.department_of_person = {r['Person']: r['Department'] for r in self.staff_rows}
        self.team = collections.defaultdict(list)
        for r in self.staff_rows:
            self.team[r['Department']].append(r['Person'])
        self.request_rows = table_rows(sections(sources['requests.md'])['B-01'])
        self.request = {r['Request']: r for r in self.request_rows}
        self.department_of_request = {r['Request']: r['Department'] for r in self.request_rows}
        self.of_department = collections.defaultdict(list)
        for r in self.request_rows:
            self.of_department[r['Department']].append(r['Request'])

    @staticmethod
    def claims(report):
        found = re.findall(r'<a id="(C\d+)"></a>\n\*\*C\d+\.\*\* (.*?) Citations?: ', report)
        assert found, 'no claim found in the report'
        return found

    def parse(self, text):
        for kind, pattern in self.PATTERNS:
            match = pattern.fullmatch(text)
            if match:
                return kind, match.groups()
        raise AssertionError(f'a claim this reading cannot parse: {text}')

    def department_of(self, kind, args):
        """The unit a claim is about: the department of its request, or the one it names."""
        if kind == 'failures':
            return args[3]
        request = args[1] if kind == 'role' else args[0]
        return self.department_of_request[request]

    def unclear(self, department=None):
        """Requests with a signature the packet does not read, in one department or in the whole dossier."""
        return [r['Request'] for r in self.request_rows
                if ILLEGIBLE in (r['First signature'], r['Second signature'])
                and (department is None or r['Department'] == department)]

    def worlds(self, unclear):
        """Every way each unreadable signature can belong to a person listed for its request's department."""
        pools = [self.team[self.department_of_request[ident]] for ident in unclear]
        return [dict(zip(unclear, combination)) for combination in itertools.product(*pools)]

    def meets(self, ident, signer):
        row = self.request[ident]
        names = [signer.get(ident) if s == ILLEGIBLE else s for s in (row['First signature'], row['Second signature'])
                 if s != 'None']
        counting = {n for n in names if n != row['Requester'] and self.role[n] in ('Manager', 'Finance officer', 'Director')}
        if int(row['Amount, euro']) <= self.limit:
            return len(counting) >= 1
        return len(counting) >= 2 and any(self.role[n] == 'Finance officer' for n in counting)

    def holds(self, kind, args, signer):
        if kind == 'compliance':
            return self.meets(args[0], signer) == (args[1] == 'met')
        if kind == 'tier':
            return (args[1] == 'two counting approvals') == (int(self.request[args[0]]['Amount, euro']) > self.limit)
        if kind == 'failures':
            count = sum(not self.meets(ident, signer) for ident in self.of_department[args[3]])
            return count == int(args[1]) if args[0] == 'Exactly' else count >= int(args[1])
        if kind == 'role':
            return self.role[args[0]] == args[2]
        if kind == 'lookup':
            row = self.request[args[0]]
            return (row['Requester'], row['Amount, euro']) == (args[1], args[2])
        return self.request[args[0]]['Second signature'] == ILLEGIBLE

    def verdict(self, text, scope):
        """`(state, number of worlds, worlds where the claim holds)`. With scope 'dossier' every unreadable
        signature of the whole dossier is varied at once; with 'unit', only those of the claim's department."""
        kind, args = self.parse(text)
        unclear = self.unclear(None if scope == 'dossier' else self.department_of(kind, args))
        outcomes = [self.holds(kind, args, signer) for signer in self.worlds(unclear)]
        return over_all(outcomes), len(outcomes), sum(outcomes)

    def names(self, text):
        """Every entity name or identifier a claim uses."""
        kind, args = self.parse(text)
        if kind == 'failures':
            return {args[3]}
        if kind == 'role':
            return {args[0], args[1]}
        if kind == 'lookup':
            return {args[0], args[1]}
        return {args[0]}


# ---------- Cases ----------

_cache = {}


def case(seed, scale):
    """`build_case` for the approvals family, with the builder's own case kept for the block counts."""
    if (seed, scale) not in _cache:
        real = scaled_approvals.build_scaled
        built = []

        def keep(rng, n):
            built.append(real(rng, n))
            return built[-1]
        with mock.patch.object(scaled_approvals, 'build_scaled', keep):
            made = generate.build_case('approvals', seed, 1, scale)
        _cache[(seed, scale)] = (made, built[0])
    return _cache[(seed, scale)]


def read_case(made):
    files = made['files']
    sources = {name[len('sources/'):]: text for name, text in files.items() if name.startswith('sources/')}
    return Reading(sources), {v: files[f'report-{v}.md'] for v in ('correct', 'mixed')}


def key_rows(made, variant):
    return [r for r in made['key']['expectations'] if r['variant'] == variant]


class CountTests(unittest.TestCase):
    def test_claims_identifiers_and_blocks(self):
        for scale in (4, 12, 30):
            for seed in range(1, 6):
                made, built = case(seed, scale)
                name = f'seed {seed} scale {scale}'
                self.assertEqual(len(built['blocks']), scale, name)
                slots = [len(block['slots']) for block in built['blocks']]
                total = sum(slots)
                width = max(2, len(str(total)))
                wanted = [f'C{n:0{width}d}' for n in range(1, total + 1)]
                self.assertEqual(len(made['key']['expectations']), 2 * total, name)
                by_variant = {v: key_rows(made, v) for v in ('correct', 'mixed')}
                for variant, rows in by_variant.items():
                    self.assertEqual([r['claim_id'] for r in rows], wanted, f'{name} {variant}')
                    self.assertTrue(all(1 <= r['block'] <= scale for r in rows), f'{name} {variant}')
                    per_block = collections.Counter(r['block'] for r in rows)
                    self.assertEqual([per_block[b] for b in range(1, scale + 1)], slots, f'{name} {variant}')
                _, reports = read_case(made)
                for variant, text in reports.items():
                    self.assertEqual([ident for ident, _ in Reading.claims(text)], wanted, f'{name} {variant}')
                self.assertEqual(made['key']['generator']['scale'], scale, name)
                self.assertEqual(made['key']['generator'].get('blocks'), scale, name)
                self.assertEqual(made['dossier'], f'g{seed}-001-approvals-x{scale}')

    def test_scale_outside_the_range_is_refused(self):
        self.assertEqual(scaled_approvals.MAX_SCALE, 30)
        for scale in (0, 31):
            with self.assertRaises(generate.GenerationError):
                scaled_approvals.build_scaled(random.Random(1), scale)
        one = scaled_approvals.build_scaled(random.Random(1), 1)
        self.assertEqual(len(one['blocks']), 1)


class IndependentReadingTests(unittest.TestCase):
    def check(self, made, scope, counts=False, name=''):
        """Compare the independent verdict with the key for every claim of both reports."""
        reading, reports = read_case(made)
        checked = 0
        for variant, text in reports.items():
            rows = {r['claim_id']: r for r in key_rows(made, variant)}
            for ident, claim_text in Reading.claims(text):
                state, worlds, true = reading.verdict(claim_text, scope)
                row = rows[ident]
                context = f'{name} {variant} {ident}: {claim_text} ({row["rationale"]})'
                self.assertEqual(claim_text, row['claim_text'], context)
                self.assertEqual(state, row['semantic_state'], context)
                self.assertIsNone(row['echo_state'], context)
                self.assertNotIn('lure_states', row, context)
                if counts:
                    self.assertEqual((worlds, true), (row['derivation']['worlds'], row['derivation']['worlds_where_true']),
                                     context)
                checked += 1
        return checked

    def test_two_departments_over_every_open_fact_of_the_dossier(self):
        checked = 0
        for seed in range(1, 21):
            made, _ = case(seed, 2)
            checked += self.check(made, 'dossier', name=f'seed {seed} scale 2')
        self.assertEqual(checked, sum(len(key_rows(case(seed, 2)[0], v)) for seed in range(1, 21) for v in ('correct', 'mixed')))

    def test_four_departments_every_claim_over_the_open_facts_of_its_own_department(self):
        for seed in range(1, 6):
            made, _ = case(seed, 4)
            checked = self.check(made, 'unit', counts=True, name=f'seed {seed} scale 4')
            self.assertEqual(checked, len(made['key']['expectations']))

    def test_every_claim_of_both_reports_at_twelve_and_thirty_departments(self):
        # Every claim, not a sample: a defect confined to one department must not slip between the sampled claims.
        for scale in (12, 30):
            for seed in range(1, 4):
                made, built = case(seed, scale)
                checked = self.check(made, 'unit', counts=True, name=f'seed {seed} scale {scale}')
                self.assertEqual(checked, 2 * sum(len(block['slots']) for block in built['blocks']),
                                 f'seed {seed} scale {scale}')
                self.assertEqual(checked, len(made['key']['expectations']), f'seed {seed} scale {scale}')


class MergeIntegrityTests(unittest.TestCase):
    def test_no_name_or_identifier_is_shared_between_blocks(self):
        for scale in (4, 12, 30):
            for seed in range(1, 6):
                made, _ = case(seed, scale)
                name = f'seed {seed} scale {scale}'
                reading, reports = read_case(made)
                # Sources: one row per request and per person, a department for each, seven people to a department.
                ids = [r['Request'] for r in reading.request_rows]
                people = [r['Person'] for r in reading.staff_rows]
                self.assertEqual(len(ids), len(set(ids)), f'{name}: a request identifier occurs twice')
                self.assertEqual(len(people), len(set(people)), f'{name}: a person is listed twice')
                self.assertEqual(len(reading.team), scale, name)
                self.assertTrue(all(len(team) == 7 for team in reading.team.values()), name)
                self.assertEqual(set(reading.of_department), set(reading.team), name)
                self.assertTrue(all(5 <= len(v) <= 7 for v in reading.of_department.values()), name)
                homes = collections.defaultdict(set)
                for r in reading.request_rows:
                    for cell in (r['Requester'], r['First signature'], r['Second signature']):
                        if cell not in ('None', ILLEGIBLE):
                            self.assertIn(cell, reading.department_of_person, f'{name}: {cell} is not listed')
                            homes[cell].add(r['Department'])
                for person, departments in homes.items():
                    self.assertEqual(departments, {reading.department_of_person[person]},
                                     f'{name}: {person} appears in more than one department')
                # No name is the beginning of another.
                names = sorted(set(ids) | set(people) | set(reading.team))
                for one, other in itertools.permutations(names, 2):
                    self.assertFalse(other.startswith(one), f'{name}: {one!r} begins {other!r}')
                # The limit is stated once, with one value, in both tiers.
                self.assertEqual(len(reading.limit_values), 1, name)
                every_text = ' '.join(made['files'][f'sources/{f}'] for f in ('policy.md', 'staff.md', 'requests.md'))
                self.assertEqual(set(re.findall(r'(?:at most|above) (\d+) euro', every_text)), reading.limit_values, name)
                self.assertEqual(len(re.findall(r'(?:at most|above) \d+ euro', every_text)), 2, name)
                self.assertEqual(reading.signature_rule, 'Every signature on a request belongs to a person listed in '
                                 "S-01 for that request's department.", name)
                # The register is a merge: a department is not one stretch of rows.
                sequence = [r['Department'] for r in reading.request_rows]
                changes = sum(a != b for a, b in zip(sequence, sequence[1:]))
                self.assertGreater(changes, 2 * (scale - 1), f'{name}: the departments are not interleaved')
                # Claims: the names of one claim belong to one department, and a block is one department.
                department_of_name = dict(reading.department_of_person)
                department_of_name.update(reading.department_of_request)
                department_of_name.update({d: d for d in reading.team})
                block_department = {}
                for variant, text in reports.items():
                    rows = {r['claim_id']: r for r in key_rows(made, variant)}
                    for ident, claim_text in Reading.claims(text):
                        homes_of_claim = {department_of_name[n] for n in reading.names(claim_text)}
                        self.assertEqual(len(homes_of_claim), 1, f'{name} {variant} {ident}: {claim_text}')
                        block = rows[ident]['block']
                        self.assertEqual(block_department.setdefault(block, next(iter(homes_of_claim))),
                                         next(iter(homes_of_claim)), f'{name} {variant} {ident}: block {block}')
                self.assertEqual(sorted(block_department), list(range(1, scale + 1)), name)
                # No claim is judged over more than the seven people of one department.
                self.assertLessEqual(max(r['derivation']['worlds'] for r in made['key']['expectations']), 7, name)
                self.assertEqual(len(set(block_department.values())), scale, f'{name}: two blocks share a department')
                # Only the aggregate claim names its department: once for each block in each report.
                for variant, text in reports.items():
                    named = [t for _, t in Reading.claims(text) if ' office ' in t]
                    self.assertEqual(len(named), scale, f'{name} {variant}')
                    self.assertTrue(all(reading.parse(t)[0] == 'failures' for t in named), f'{name} {variant}')


class DeterminismTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TEST_ROOT.mkdir(exist_ok=True)
        cls.run_folder = Path(tempfile.mkdtemp(prefix=PREFIX, dir=TEST_ROOT))

    @classmethod
    def tearDownClass(cls):
        if cls.run_folder.parent == TEST_ROOT and cls.run_folder.name.startswith(PREFIX):
            shutil.rmtree(cls.run_folder, ignore_errors=True)

    def generate_tree(self, hash_seed):
        out = self.run_folder / f'hash{hash_seed}'
        env = dict(os.environ, PYTHONHASHSEED=str(hash_seed))
        done = subprocess.run([sys.executable, str(ROOT / 'evalsuite/generate.py'), '--fixtures', str(out / 'fixtures'),
                               '--keys', str(out / 'keys'), '--seed', '3', '--dossiers', '3', '--families', 'approvals',
                               '--scale', '12'], env=env, capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(done.returncode, 0, done.stderr)
        return {str(path.relative_to(out)): path.read_bytes() for path in sorted(out.rglob('*')) if path.is_file()}

    def test_two_hash_seeds_give_identical_trees(self):
        first, second = self.generate_tree(1), self.generate_tree(2)
        self.assertEqual(len(first), 3 * 5 + 3 + 1)      # per dossier: three sources and two reports; three keys; the summary
        self.assertEqual(set(first), set(second))
        self.assertEqual([n for n in first if first[n] != second[n]], [])


if __name__ == '__main__':
    unittest.main()
