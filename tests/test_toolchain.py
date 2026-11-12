"""tools/toolchain.py and its parity with tools/toolchain.mjs. Runs on Python 3.9 as well as newer versions."""
import ast
import importlib.util
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
spec = importlib.util.spec_from_file_location('toolchain_tested', TOOLS / 'toolchain.py')
toolchain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(toolchain)

KEYS = ['linux-x64', 'linux-arm64', 'darwin-x64', 'darwin-arm64']
# (Python sys.platform, platform.machine(), Node process.platform, Node process.arch, key)
PAIRS = [
    ('linux', 'x86_64', 'linux', 'x64', 'linux-x64'),
    ('linux', 'aarch64', 'linux', 'arm64', 'linux-arm64'),
    ('darwin', 'x86_64', 'darwin', 'x64', 'darwin-x64'),
    ('darwin', 'arm64', 'darwin', 'arm64', 'darwin-arm64'),
]
UNSUPPORTED = [('win32', 'AMD64', 'win32', 'x64'), ('linux', 'i686', 'linux', 'ia32'), ('darwin', 'ppc', 'darwin', 'ppc')]
NODE = shutil.which('node')


def node_json(code):
    """Runs a Node ES module snippet that prints one JSON document."""
    if not NODE:
        raise AssertionError('node is not on PATH, so JS/Python parity cannot be checked. This repository\'s tools need Node.js (24 or later); install it or put it on PATH.')
    done = subprocess.run([NODE, '--input-type=module', '-e', code], cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
    if done.returncode != 0:
        raise AssertionError('node failed: ' + done.stderr)
    return json.loads(done.stdout)


class PlatformKeyTests(unittest.TestCase):
    def test_four_supported_platforms(self):
        for system, machine, _, _, key in PAIRS:
            self.assertEqual(toolchain.platform_key(system, machine), key)

    def test_machine_spellings(self):
        self.assertEqual(toolchain.platform_key('linux', 'AMD64'), 'linux-x64')
        self.assertEqual(toolchain.platform_key('Linux', 'x64'), 'linux-x64')
        self.assertEqual(toolchain.platform_key('linux', 'ARM64'), 'linux-arm64')
        self.assertEqual(toolchain.platform_key('darwin', 'aarch64'), 'darwin-arm64')

    def test_unsupported_platform_message(self):
        for system, machine, node_platform, node_arch in UNSUPPORTED:
            with self.assertRaises(toolchain.UnsupportedPlatformError) as caught:
                toolchain.platform_key(system, machine)
            self.assertEqual(str(caught.exception), 'Unsupported platform for the Lean toolchain: %s-%s' % (node_platform, node_arch))

    def test_current_platform_is_one_of_the_four_keys(self):
        self.assertIn(toolchain.platform_key(), KEYS)


class LeanExecutableTests(unittest.TestCase):
    def test_path_for_every_key_follows_the_manifest(self):
        manifest = json.loads((TOOLS / 'toolchain.json').read_text(encoding='utf-8'))
        expected = {
            'linux-x64': 'lean-4.24.0-linux',
            'linux-arm64': 'lean-4.24.0-linux_aarch64',
            'darwin-x64': 'lean-4.24.0-darwin',
            'darwin-arm64': 'lean-4.24.0-darwin_aarch64',
        }
        for key in KEYS:
            self.assertEqual(manifest['lean']['platforms'][key]['folder'], expected[key])
            self.assertEqual(toolchain.lean_executable('/repo', key), Path('/repo/tmp/toolchains') / expected[key] / 'bin' / 'lean')

    def test_default_key_is_the_current_platform(self):
        self.assertEqual(toolchain.lean_executable(ROOT), toolchain.lean_executable(ROOT, toolchain.platform_key()))

    def test_unknown_key_is_an_error(self):
        with self.assertRaises(toolchain.UnsupportedPlatformError):
            toolchain.lean_executable(ROOT, 'plan9-x64')

    def test_accepts_str_and_path_repositories(self):
        self.assertEqual(toolchain.lean_executable(str(ROOT), 'linux-x64'), toolchain.lean_executable(ROOT, 'linux-x64'))


class SyntaxTests(unittest.TestCase):
    def test_parses_as_python_39(self):
        source = (TOOLS / 'toolchain.py').read_text(encoding='utf-8')
        ast.parse(source, filename='toolchain.py', feature_version=(3, 9))


class ParityTests(unittest.TestCase):
    # No skip when node is missing: a run without the parity checks must not report OK.
    def test_node_is_available(self):
        self.assertTrue(NODE, 'node is not on PATH, so JS/Python parity cannot be checked. This repository\'s tools need Node.js (24 or later); install it or put it on PATH.')

    def test_same_manifest_file(self):
        theirs = node_json("import {MANIFEST_PATH} from './tools/toolchain.mjs'; console.log(JSON.stringify(MANIFEST_PATH))")
        self.assertEqual(Path(theirs).resolve(), toolchain.MANIFEST.resolve())

    def test_lean_path_matches_for_all_four_keys(self):
        repo = '/some/repo root'
        for system, machine, node_platform, node_arch, key in PAIRS:
            code = (
                "import {platformKey, leanExecutable} from './tools/toolchain.mjs';"
                "const key = platformKey({platform: %s, arch: %s});"
                "console.log(JSON.stringify({key, path: leanExecutable(%s, key)}))"
            ) % (json.dumps(node_platform), json.dumps(node_arch), json.dumps(repo))
            theirs = node_json(code)
            ours_key = toolchain.platform_key(system, machine)
            self.assertEqual(theirs['key'], ours_key)
            self.assertEqual(theirs['path'], str(toolchain.lean_executable(repo, ours_key)))

    def test_unsupported_platform_messages_match(self):
        for system, machine, node_platform, node_arch in UNSUPPORTED:
            code = (
                "import {platformKey} from './tools/toolchain.mjs';"
                "try { platformKey({platform: %s, arch: %s}); console.log(JSON.stringify(null)) }"
                "catch (error) { console.log(JSON.stringify(error.message)) }"
            ) % (json.dumps(node_platform), json.dumps(node_arch))
            theirs = node_json(code)
            with self.assertRaises(toolchain.UnsupportedPlatformError) as caught:
                toolchain.platform_key(system, machine)
            self.assertEqual(theirs, str(caught.exception))

    def test_unknown_key_messages_match(self):
        theirs = node_json(
            "import {leanExecutable} from './tools/toolchain.mjs';"
            "try { leanExecutable('/r', 'plan9-x64'); console.log(JSON.stringify(null)) }"
            "catch (error) { console.log(JSON.stringify(error.message)) }"
        )
        with self.assertRaises(toolchain.UnsupportedPlatformError) as caught:
            toolchain.lean_executable('/r', 'plan9-x64')
        self.assertEqual(theirs, str(caught.exception))

    def test_current_platform_key_matches(self):
        theirs = node_json("import {platformKey} from './tools/toolchain.mjs'; console.log(JSON.stringify(platformKey()))")
        self.assertEqual(theirs, toolchain.platform_key())


if __name__ == '__main__':
    unittest.main()
