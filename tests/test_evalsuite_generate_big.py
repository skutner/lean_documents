"""Checks of the launcher `evalsuite/generate_big.py`, which puts the facts of a large family into its answer key.

The generator `generate.py` must stay byte-identical; the fixtures of a large family must be the same bytes with and
without the facts; the key only gains entries; every other family is generated exactly as before, key included; and a
large family that does not export the interface is refused with a plain message before any folder is made.

The runs that compare bytes are subprocesses: the launcher is run as a command, as it is used.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
sys.dont_write_bytecode = True
import generate  # noqa: E402
import generate_big  # noqa: E402


_FAMILIES_BEFORE = {}


def setUpModule():
    """The large families are registered for the tests of this module only; `tearDownModule` puts the list back."""
    _FAMILIES_BEFORE.update(generate.HARD_FAMILIES)
    generate_big.register()


GENERATE_SHA256 = '9f166028544b24cd469116f7ee56f8d51966328b5df7742b75d16484e190ee98'
FAMILIES_WITH_INTERFACE = ('amendments',)      # one entry per large family once its module exports the interface
PLAIN = ('import sys; sys.dont_write_bytecode = True; sys.path.insert(0, %r); import generate, generate_big; '
         'generate_big.register(); generate.main()' % str(ROOT / 'evalsuite'))
TEST_ROOT = ROOT / 'tmp'
TEST_ROOT.mkdir(exist_ok=True)
TEST_RUN = Path(tempfile.mkdtemp(prefix='evalsuite-generate-big-tests-', dir=TEST_ROOT))


def tearDownModule():
    shutil.rmtree(TEST_RUN, ignore_errors=True)
    generate.HARD_FAMILIES.clear()
    generate.HARD_FAMILIES.update(_FAMILIES_BEFORE)      # the list as it was found, for the modules that follow


def tree(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(Path(root).rglob('*')) if path.is_file()}


def run(name, launcher, families, seed=1, dossiers=1):
    """Generate with the launcher (facts in the keys) or with the generator's own command line."""
    base = Path(tempfile.mkdtemp(dir=TEST_RUN, prefix=name + '-'))
    command = [sys.executable, str(ROOT / 'evalsuite/generate_big.py')] if launcher else [sys.executable, '-c', PLAIN]
    done = subprocess.run(command + ['--fixtures', str(base / 'fx'), '--keys', str(base / 'keys'), '--seed', str(seed),
                                     '--dossiers', str(dossiers), '--families', families],
                          capture_output=True, text=True, env={'PYTHONDONTWRITEBYTECODE': '1', 'PATH': '/usr/bin:/bin'})
    assert done.returncode == 0, done.stderr
    return base, done


class LauncherTests(unittest.TestCase):
    def test_the_generator_is_byte_identical(self):
        self.assertEqual(hashlib.sha256((ROOT / 'evalsuite/generate.py').read_bytes()).hexdigest(), GENERATE_SHA256)

    def test_fixtures_are_the_same_bytes_and_the_key_only_gains_entries(self):
        for family in FAMILIES_WITH_INTERFACE:
            with_facts, _ = run('with-' + family, True, family)
            without, _ = run('without-' + family, False, family)
            self.assertEqual(tree(with_facts / 'fx'), tree(without / 'fx'))
            for name, text in tree(without / 'keys').items():
                added = json.loads(tree(with_facts / 'keys')[name])
                plain = json.loads(text)
                facts = added.pop('facts')
                for row in added['expectations']:
                    self.assertIsInstance(row.pop('query'), dict)
                    self.assertIsNotNone(row.pop('value_form'))
                self.assertEqual(added, plain, name)
                self.assertEqual(set(facts) - {'size'}, {'family', 'interpreter', 'interpreter_sha256', 'events',
                                                         'passages', 'names', 'sources', 'readers'})
                # the hash of every source file written, and the flawed readers of the dossier
                fixtures = tree(with_facts / 'fx')
                written = {path.split('/')[-1]: hashlib.sha256(data).hexdigest() for path, data in fixtures.items()
                           if '/sources/' in path}
                self.assertEqual(facts['sources'], dict(sorted(written.items())))
                self.assertTrue(facts['readers'])
                self.assertEqual(facts['readers'], sorted(facts['readers']))
                self.assertTrue({name for row in added['expectations'] for name in row.get('lure_states', {})}
                                <= set(facts['readers']))
                self.assertEqual(facts['family'], family)
                self.assertEqual(facts['interpreter'], generate_big.BIG_FAMILIES[family])
                module = sys.modules[facts['interpreter']] if facts['interpreter'] in sys.modules else __import__(
                    facts['interpreter'])
                self.assertEqual(facts['interpreter_sha256'],
                                 hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest())

    def test_the_facts_are_what_the_family_built_and_each_row_says_how_to_recompute_it(self):
        family = FAMILIES_WITH_INTERFACE[0]
        with generate_big.facts_in_keys():
            built = generate.build_case(family, 2, 1)
        module = __import__(generate_big.BIG_FAMILIES[family])
        import random
        raw = module.build(random.Random(f'evalsuite:{generate.GENERATOR_VERSION}:2:1:{family}'))
        facts = built['key']['facts']
        self.assertEqual(facts['events'], json.loads(json.dumps(raw['events'])))
        self.assertEqual(facts['passages'], json.loads(json.dumps(raw['passages'])))
        self.assertEqual(facts['names'], json.loads(json.dumps(raw['names'])))
        self.assertEqual(facts['readers'], sorted(raw['readers']))
        self.assertEqual(facts['sources'], {name: hashlib.sha256(text.encode('utf-8')).hexdigest()
                                            for name, text in sorted(raw['sources'].items())})
        for row in built['key']['expectations']:
            truth = module.answer(row['query'], facts['events'])
            self.assertEqual('supported' if truth == row['derivation']['stated'] else 'refuted', row['semantic_state'])
            self.assertEqual(row['value_form'], json.loads(json.dumps(module.value_form(row['query']))))
        # outside the block the generator is as it was
        self.assertNotIn('facts', generate.build_case(family, 2, 1)['key'])

    def test_every_other_family_is_generated_as_before_keys_included(self):
        with_facts, _ = run('others-with', True, 'ledger,approvals', dossiers=2)
        without, _ = run('others-without', False, 'ledger,approvals', dossiers=2)
        self.assertEqual(tree(with_facts / 'fx'), tree(without / 'fx'))
        self.assertEqual(tree(with_facts / 'keys'), tree(without / 'keys'))

    def test_the_wrappers_are_removed_after_the_block_and_nest(self):
        saved = generate.expectation, generate.build_case, generate.write_suite
        with generate_big.facts_in_keys():
            self.assertIsNot(generate.build_case, saved[1])
            with generate_big.facts_in_keys():
                pass
            self.assertIsNot(generate.build_case, saved[1])
        self.assertEqual((generate.expectation, generate.build_case, generate.write_suite), saved)


def stub_module(with_interface, size=None):
    """A tiny large family: one fact, four statements, a round trip that the launcher can generate."""
    module = types.ModuleType('big_stub')
    module.__file__ = str(TEST_RUN / 'big_stub.py')
    Path(module.__file__).write_text('# stub\n', encoding='utf-8')

    def build(rng):
        events = [{'id': 'E001', 'kind': 'fact', 'value': 7}]
        slots = []
        for number in range(4):
            def pred(world, number=number):
                return number != 3

            true = generate.claim('stub', f'Statement {number} is true.', lambda world: True,
                                  [('a.md', 'A-00')], quantity=lambda world: 7, stated=7)
            true['query'] = {'kind': 'value', 'stated': 7}
            false = generate.claim('stub', f'Statement {number} is false.', lambda world: False,
                                   [('a.md', 'A-00')], quantity=lambda world: 7, stated=8)
            false['query'] = {'kind': 'value', 'stated': 8}
            slots.append((true, [false]))
        case = {'title': 'Stub', 'sources': {'a.md': 'The value is 7.\n'}, 'base': {'events': events},
                'unknowns': {}, 'slots': slots, 'events': events,
                'passages': [{'file': 'a.md', 'paragraph': 0, 'event_ids': ['E001'], 'role': 'states'}],
                'names': {}}
        if size is not None:
            case['size'] = size
        return case

    module.build = build
    if with_interface:
        module.NONE_VALUE = 'none'
        module.answer = lambda query, events, reader=None: 7
        module.value_form = lambda query: 'integer'
        module.relevant = lambda query, events: ['E001']
        module.depends = lambda events: {e['id']: {'name': [], 'amount': [], 'target': []} for e in events}
    return module


class RefusalAndSizeTests(unittest.TestCase):
    def generate(self, module, families='stub'):
        base = Path(tempfile.mkdtemp(dir=TEST_RUN, prefix='stub-'))
        argv = ['generate_big.py', '--fixtures', str(base / 'fx'), '--keys', str(base / 'keys'), '--seed', '1',
                '--dossiers', '1', '--families', families]
        with mock.patch.dict(sys.modules, {'big_stub': module}), \
                mock.patch.dict(generate_big.BIG_FAMILIES, {'stub': 'big_stub'}), \
                mock.patch.dict(generate.HARD_FAMILIES, {'stub': 'big_stub'}), \
                mock.patch.object(sys, 'argv', argv), mock.patch('builtins.print'):
            try:
                generate_big.main()
            except SystemExit as error:
                return base, str(error)
        return base, None

    def test_a_family_without_the_interface_is_refused_with_a_message_and_no_folder(self):
        base, message = self.generate(stub_module(False))
        self.assertIn('Cannot generate: Family stub does not export what the checked answers need', message)
        self.assertIn('answer, value_form, relevant, depends, NONE_VALUE', message)
        self.assertFalse((base / 'fx').exists() or (base / 'keys').exists())

    def test_a_family_that_lacks_only_depends_is_refused_and_names_it(self):
        module = stub_module(True)
        del module.depends
        base, message = self.generate(module)
        self.assertIn('Cannot generate: Family stub does not export what the checked answers need (depends)', message)
        self.assertFalse((base / 'fx').exists() or (base / 'keys').exists())

    def test_a_refused_family_stops_a_mixed_run_before_the_first_dossier_is_written(self):
        base, message = self.generate(stub_module(False), 'ledger,stub')
        self.assertIn('Cannot generate', message)
        self.assertFalse((base / 'fx').exists())

    def test_the_size_a_case_carries_is_copied_into_the_facts(self):
        base, message = self.generate(stub_module(True, size=3))
        self.assertIsNone(message)
        key = json.loads(next((base / 'keys').glob('*.json')).read_text(encoding='utf-8'))
        self.assertEqual(key['facts']['size'], 3)
        self.assertEqual(key['facts']['interpreter'], 'big_stub')

    def test_a_case_without_readers_records_none_and_every_source_file_is_hashed(self):
        base, message = self.generate(stub_module(True))
        self.assertIsNone(message)
        key = json.loads(next((base / 'keys').glob('*.json')).read_text(encoding='utf-8'))
        self.assertEqual(key['facts']['readers'], [])
        self.assertEqual(key['facts']['sources'],
                         {'a.md': hashlib.sha256((base / 'fx' / key['dossier_id'] / 'sources' / 'a.md').read_bytes()).hexdigest()})

    def test_a_case_without_a_size_has_none_in_its_facts(self):
        base, message = self.generate(stub_module(True))
        self.assertIsNone(message)
        key = json.loads(next((base / 'keys').glob('*.json')).read_text(encoding='utf-8'))
        self.assertNotIn('size', key['facts'])

    def test_an_interpreter_that_disagrees_with_the_key_is_refused(self):
        module = stub_module(True)
        module.answer = lambda query, events, reader=None: 99
        base, message = self.generate(module)
        self.assertIn('Cannot generate', message)
        self.assertIn('the interpreter gives 99', message)


if __name__ == '__main__':
    unittest.main()
