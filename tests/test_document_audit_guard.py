"""The Python 3.12 floor of tools/document_audit.py: an older interpreter must see the standard error text, never a
SyntaxError, and the whole file must parse with Python 3.9 grammar. Runs on every interpreter version."""
import ast
import contextlib
import io
import runpy
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'tools/document_audit.py'
OLD = (3, 9, 6, 'final', 0)
TAIL = 'Run "node tools/setup-toolchain.mjs" to install a pinned interpreter into tmp/toolchains, or set AUDIT_PYTHON to an absolute path. Other interpreters under your home folder are not visible inside the ALA sandbox.'


class GuardTests(unittest.TestCase):
    def run_as_main_under_old_python(self):
        error = io.StringIO()
        with mock.patch.object(sys, 'version_info', OLD), contextlib.redirect_stderr(error):
            with self.assertRaises(SystemExit) as caught:
                runpy.run_path(str(HELPER), run_name='__main__')
        return caught.exception.code, error.getvalue()

    def test_run_directly_prints_the_standard_text_and_exits_non_zero(self):
        code, text = self.run_as_main_under_old_python()
        self.assertEqual(code, 1)
        self.assertTrue(text.startswith('Python 3.12 or later is required (tools/document_audit.py). Found: '), text)
        self.assertIn(' 3.9.6. ' + TAIL, text)
        self.assertNotIn('SyntaxError', text)

    def test_import_raises_import_error_with_the_same_text(self):
        with mock.patch.object(sys, 'version_info', OLD):
            with self.assertRaises(ImportError) as caught:
                runpy.run_path(str(HELPER), run_name='document_audit_guard_import')
        self.assertIn(' 3.9.6. ' + TAIL, str(caught.exception))

    def test_text_matches_the_resolver_in_toolchain_mjs(self):
        self.assertIn(TAIL, (ROOT / 'tools/toolchain.mjs').read_text(encoding='utf-8'))
        self.assertIn(TAIL, HELPER.read_text(encoding='utf-8'))

    def test_not_triggered_on_this_interpreter_when_it_is_new_enough(self):
        if sys.version_info < (3, 12):
            self.skipTest('this interpreter is older than 3.12; the real guard is covered by the subprocess test')
        done = subprocess.run([sys.executable, str(HELPER), '--help'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_real_old_interpreter_gets_the_text_not_a_syntax_error(self):
        if sys.version_info >= (3, 12):
            self.skipTest('this interpreter is 3.12 or later; the guard is covered with a patched version')
        done = subprocess.run([sys.executable, str(HELPER), '--help'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertNotEqual(done.returncode, 0)
        self.assertNotIn('SyntaxError', done.stderr)
        self.assertIn('Python 3.12 or later is required (tools/document_audit.py)', done.stderr)


class GrammarTests(unittest.TestCase):
    def test_whole_file_parses_with_python_39_grammar(self):
        source = HELPER.read_text(encoding='utf-8')
        tree = ast.parse(source, filename=str(HELPER), feature_version=(3, 9))
        if sys.version_info >= (3, 12):
            # Python 3.12 accepts a backslash inside an f-string expression, which 3.9 rejects; look for it explicitly.
            for node in ast.walk(tree):
                if isinstance(node, ast.FormattedValue):
                    segment = ast.get_source_segment(source, node.value) or ''
                    self.assertNotIn('\\', segment, 'line %d: backslash inside an f-string expression' % node.lineno)


if __name__ == '__main__':
    unittest.main()
