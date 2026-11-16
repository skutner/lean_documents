"""Phase-1 fixtures only: never opens retained campaign/validation Lean sources.

All evidence remains beneath tmp/recheck. On Linux missing prerequisites fail;
Mac skips only tests requiring Linux/Lean/Bubblewrap. Flood tests cannot run until
the SAME launch path's effective controls and independent deadline have passed.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import re
from pathlib import Path
import signal
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import uuid

from evalsuite import recheck
from evalsuite import recheck_sandbox as s

# Obviously fake bindings: approving receipts exist ONLY with these, in temporary
# directories, never with real code/runtime/kernel bindings.
FAKE_BINDINGS = {'fake_binding_for_unit_tests_only': '0' * 64}
TERM_ELAB = ('import Std\nopen Lean Elab Term in\n'
             '@[term_elab Lean.Parser.Term.paren] def strictProbe : TermElab := fun _ _ => throwError "never"\n'
             'theorem safe : True := True.intro\n')


def new_path(name):
    return s.WORK / (name + '-' + str(uuid.uuid4()))


def fake_runtime(root):
    """Empty runtime directory with every mount point, for tests that mock its manifest."""
    for name in s.MOUNT_POINTS:
        (root / 'runtime' / name).mkdir(parents=True, exist_ok=True)


def probe_receipt(path, root, hashes, bindings):
    path.write_bytes(s.canonical(dict(schema_version=1, kind='verifier_probe', bindings=bindings,
                                      probe_root=str(root), probe_sources=sorted(set(hashes)))))
    return path


def probe_folder(parent):
    root = parent / ('probe-' + str(uuid.uuid4()))
    root.mkdir(parents=True)
    root.chmod(0o700)
    return root


def probe_job(root, runtime, digest, gate, files, run_dir):
    return dict(schema_version=1, runtime=str(runtime), runtime_hash=digest, gate=str(gate), run_dir=str(run_dir),
                probe_root=str(root),
                sources=[dict(module=m, path=str(p), sha256=s.file_hash(p)) for m, p in files],
                proofs=[dict(module=files[0][0], theorem='safe', target='True', relation='supports', claim_id='probe')])


def normalized_launch(result):
    """Every step's systemd-run and Bubblewrap argv, up to run directory and unit UUID."""
    run_dir = str(Path(result['log_paths'][0]).parent)
    rows = []
    for step in result['steps']:
        unit = step['unit'].rsplit('-s', 1)[0]
        rows.append([step['kind']] + [[a.replace(run_dir, 'RUN_DIR').replace(unit, 'UNIT') for a in step[k]]
                                      for k in ('argv', 'bwrap_argv')])
    return rows


def one_step_prepared(job):
    step = dict(kind='check', module='Fixture', request=0, args=['--threads=1', '/checks/Target001.lean'])
    return dict(job=job, unit='lean-recheck-' + str(uuid.uuid4()), gate_hash=None, plan=[step], requests=[], probe=False)


def fixture_worker(name, runtime, digest, path):
    result = s._run_fixture(name, runtime, digest, path)
    if result['reason'] == 'infrastructure_error':
        raise RuntimeError(result['detail'])


class PortableTests(unittest.TestCase):
    def setUp(self):
        self.root = new_path('portable')
        self.root.mkdir(parents=True)

    def job(self, name='passing', proofs=None):
        path = s.FIXTURES / (name + '.lean')
        return dict(schema_version=1, runtime=str(self.root / 'runtime'), runtime_hash='0'*64,
                    gate=str(self.root / 'missing-gate.json'), run_dir=str(self.root / str(uuid.uuid4())),
                    sources=[dict(module='Fixture', path=str(path), sha256=s.file_hash(path))],
                    proofs=proofs or [])

    def test_post_spawn_setup_faults_always_cleanup(self):
        for point in ('selector', 'blocking', 'registration', 'interrupt', 'observation'):
            with self.subTest(point=point):
                job = self.job(); job['run_dir'] = str(self.root)
                prepared = one_step_prepared(job)
                result = s.empty_result(job)
                process = mock.Mock(returncode=0)
                process.poll.return_value = None
                watcher = mock.Mock()
                watcher.get_map.return_value = {}
                error = OSError(24, 'injected descriptor exhaustion')
                with mock.patch.object(s.subprocess, 'Popen', return_value=process), \
                     mock.patch.object(s.selectors, 'DefaultSelector', side_effect=error if point == 'selector' else None, return_value=watcher), \
                     mock.patch.object(s.os, 'set_blocking', side_effect=KeyboardInterrupt() if point == 'interrupt' else error if point == 'blocking' else None), \
                     mock.patch.object(watcher, 'register', side_effect=error if point == 'registration' else None), \
                     mock.patch.object(s, 'cgroup_values', side_effect=error if point == 'observation' else None, return_value={}), \
                     mock.patch.object(s, 'kill_scope', return_value=(True, 0.0)) as cleanup:
                    with self.assertRaises(s.Rejection) as raised:
                        s._launch_step(prepared, 0, result, time.monotonic(), bytearray())
                self.assertEqual(raised.exception.reason, 'interrupted' if point == 'interrupt' else 'infrastructure_error')
                self.assertEqual(cleanup.call_count, 2)
                self.assertEqual(cleanup.call_args.args[1].name, prepared['unit']+'-s000.scope')
                process.wait.assert_called()
                if point != 'observation': process.kill.assert_called_once()
                process.stdout.close.assert_called_once()
                process.stderr.close.assert_called_once()
                self.assertTrue(result['evidence']['cleanup_empty'])

    def test_exec_imports_suppress_repository_bytecode_without_B(self):
        cache = self.root / 'bytecode-probe'
        program = 'import runpy,sys; runpy.run_path(' + repr(str(Path(s.__file__))) + '); print(sys.dont_write_bytecode)'
        result = s.subprocess.run([sys.executable, '-c', program], capture_output=True, text=True,
            env={'PATH': os.environ.get('PATH', ''), 'PYTHONPYCACHEPREFIX': str(cache)}, check=True)
        self.assertEqual(result.stdout.strip(), 'True')
        self.assertFalse(any('evidence-to-proof-review/scripts' in str(p) for p in cache.rglob('*.pyc')))

    def test_expired_setup_never_spawns(self):
        job = self.job(); job['run_dir'] = str(self.root)
        prepared = one_step_prepared(job)
        result = s.empty_result(job)
        with mock.patch.object(s.subprocess, 'Popen', side_effect=AssertionError('spawn')), \
             mock.patch.object(s, 'cgroup_values', return_value={}), \
             mock.patch.object(s, 'kill_scope', return_value=(True, 0.0)):
            with self.assertRaises(s.Rejection) as raised:
                s._launch_step(prepared, 0, result, time.monotonic()-121, bytearray())
        self.assertEqual(raised.exception.reason, 'timeout')
        self.assertEqual(result['lean_subprocesses'], 0)

    def test_invalid_jobs_return_typed_failure_records(self):
        bad_hash = self.job(); bad_hash['runtime_hash'] = 7
        for job in (None, [], {}, bad_hash):
            with self.subTest(job=job):
                result = s.run_job(job)
                self.assertEqual(result['reason'], 'invalid_metadata')
                self.assertEqual(result['runtime_hash'], None)
                self.assertEqual(result['lean_subprocesses'], 0)
                self.assertIsInstance(result['seconds'], float)

    def test_existing_run_directory_is_never_modified(self):
        job = self.job()
        path = Path(job['run_dir']); path.mkdir()
        sentinel = path / 'result.json'; sentinel.write_text('keep this existing result')
        result = s.run_job(job)
        self.assertEqual(result['reason'], 'unsafe_path')
        self.assertEqual(sentinel.read_text(), 'keep this existing result')

    def test_fixed_fixture_hashes(self):
        self.assertGreater(len(s.FIXTURE_HASHES), 10)
        self.assertEqual(set(s.FIXTURE_HASHES), {p.stem for p in s.FIXTURES.glob('*.lean')})
        for name, digest in s.FIXTURE_HASHES.items():
            self.assertEqual(s.file_hash(s.FIXTURES / (name + '.lean')), digest)

    def test_all_executable_constructs_rejected_before_spawn(self):
        names = ('sorry', 'initialize', 'macro', 'network', 'filesystem', 'sleep', 'output',
                 'memory', 'tasks', 'descendant', 'scratch', 'fsize', 'forged', 'forbidden_axiom')
        with mock.patch.object(s.subprocess, 'Popen', side_effect=AssertionError('Lean was launched')):
            for name in names:
                with self.subTest(name=name):
                    result = s.run_job(self.job(name))
                    self.assertEqual(result['outcome'], 'filter_rejected', result)
                    self.assertEqual(result['reason'], 'lexical_rejection')
                    self.assertEqual(result['lean_subprocesses'], 0)

    def test_target_injection_rejected_before_spawn(self):
        for target in ('True\n#eval IO.println "bad"', 'True := by trivial', 'True; import Lean', 'True /- evil -/', '(True'):
            job = self.job(proofs=[dict(module='Fixture', theorem='safe', target=target, relation='supports', claim_id='C01')])
            with self.subTest(target=target), mock.patch.object(s.subprocess, 'Popen', side_effect=AssertionError('spawn')):
                result = s.run_job(job)
                self.assertEqual(result['outcome'], 'filter_rejected', result)
                self.assertEqual(result['lean_subprocesses'], 0)

    def test_symlinks_hardlinks_fifo_and_traversal_fail(self):
        original = self.root / 'original.lean'
        original.write_text('theorem safe : True := True.intro\n')
        symlink = self.root / 'symbol.lean'; symlink.symlink_to(original)
        hardlink = self.root / 'hard.lean'; os.link(original, hardlink)
        fifo = self.root / 'fifo.lean'; os.mkfifo(fifo)
        for path in (symlink, hardlink, original, fifo, self.root / '../escape.lean'):
            with self.subTest(path=str(path)), self.assertRaises(s.Rejection) as raised:
                s.read_regular(path)
            self.assertEqual(raised.exception.reason, 'unsafe_path')

    def test_import_escape_and_reserved_shadow(self):
        result = s.run_job(self.job('import_escape'))
        self.assertEqual(result['outcome'], 'filter_rejected', result)
        for module in ('Init', 'Std.Escape', 'Lean', '../Escape'):
            job = self.job(); job['sources'][0]['module'] = module
            self.assertEqual(s.run_job(job)['reason'], 'unsafe_path')

    def test_missing_gate_never_spawns(self):
        job = self.job()
        with mock.patch.object(s, 'runtime_manifest', return_value={}), mock.patch.object(s.subprocess, 'Popen', side_effect=AssertionError('spawn')):
            job['runtime_hash'] = s.sha(s.canonical({}))
            result = s.run_job(job)
        self.assertNotEqual(result['outcome'], 'pass')
        self.assertEqual(result['lean_subprocesses'], 0)

    def test_no_arbitrary_fixture_bypass(self):
        with self.assertRaises(s.Rejection):
            s._run_fixture('../passing', '/runtime', '0'*64, new_path('bad-fixture'))
        job = self.job('passing'); job['gate'] = '/fixture-only'
        with self.assertRaises(s.Rejection):
            s._prepare(job, 'passing')

    def test_axiom_reports_exact_and_unambiguous(self):
        self.assertEqual(s.axiom_report("'safe' does not depend on any axioms\n", 'safe'), [])
        self.assertEqual(s.axiom_report("'safe' depends on axioms: [propext, Classical.choice]\n", 'safe'), ['Classical.choice', 'propext'])
        for output, reason in [("'other' does not depend on any axioms", 'axioms_missing'),
                               ("'safe' depends on axioms: [sorryAx]", 'axioms_disallowed'),
                               ("'safe' does not depend on any axioms\n"*2, 'axioms_missing'),
                               ("'safe' does not depend on any axioms trailing garbage", 'axioms_missing'),
                               ("'safe' depends on axioms: [propext,,]", 'axioms_missing'),
                               ('{"event":"complete"}', 'axioms_missing')]:
            with self.assertRaises(s.Rejection) as raised: s.axiom_report(output, 'safe')
            self.assertEqual(raised.exception.reason, reason)

    def test_metadata_and_source_budgets(self):
        for data in (b'{"a":1,"a":2}', b'{'):
            with self.assertRaises(s.Rejection): s.parse_json(data)
        with self.assertRaises(s.Rejection): s.parse_json(b'['*34+b'0'+b']'*34)
        path = self.root / 'large.lean'; path.write_bytes(b'x'*(s.LIMIT+1))
        with self.assertRaises(s.Rejection) as raised: s.read_regular(path)
        self.assertEqual(raised.exception.reason, 'input_limit')
        job = self.job(); job['sources'][0]['sha256'] = 'f'*64
        self.assertEqual(s.run_job(job)['reason'], 'source_changed')

    def test_accept_object_turns_every_bad_output_into_a_build_failure(self):
        step = dict(kind='build', module='A.B', object='A.B.olean', args=[])
        def attempt(prepare):
            out = self.root / ('out-' + str(uuid.uuid4())); out.mkdir()
            run_dir = self.root / ('run-' + str(uuid.uuid4())); (run_dir / 'objects').mkdir(parents=True)
            prepare(out)
            try:
                return s.accept_object(out, step, run_dir), run_dir
            finally:
                s.remove_tree(out)
                self.assertFalse(os.path.lexists(out))
        digest, run_dir = attempt(lambda out: (out / 'A.B.olean').write_bytes(b'object'))   # positive control
        self.assertEqual(digest, s.sha(b'object'))
        self.assertEqual((run_dir / 'objects/A/B.olean').read_bytes(), b'object')
        target = self.root / 'outside.txt'; target.write_text('outside')
        cases = {
            'extra': lambda out: [(out / 'A.B.olean').write_bytes(b'x'), (out / 'Other.olean').write_bytes(b'y')],
            'wrong name': lambda out: (out / 'B.olean').write_bytes(b'x'),
            'empty': lambda out: None,
            'symlink': lambda out: (out / 'A.B.olean').symlink_to(target),
            'hardlink': lambda out: os.link(target, out / 'A.B.olean'),
            'fifo': lambda out: os.mkfifo(out / 'A.B.olean'),
            'directory': lambda out: (out / 'A.B.olean').mkdir(),
            'locked dir': lambda out: [(out / 'A.B.olean').write_bytes(b'x'), (out / 'locked').mkdir(), (out / 'locked').chmod(0)],
        }
        # A step that merely locked its own /out still yields its one object.
        digest, _ = attempt(lambda out: [(out / 'A.B.olean').write_bytes(b'locked'), out.chmod(0)])
        self.assertEqual(digest, s.sha(b'locked'))
        for name, prepare in cases.items():
            with self.subTest(case=name), self.assertRaises(s.Rejection) as raised:
                attempt(prepare)
            self.assertEqual(raised.exception.reason, 'compile_error')
        with mock.patch.object(s, 'OBJECT_LIMIT', 4), self.assertRaises(s.Rejection) as raised:
            attempt(lambda out: (out / 'A.B.olean').write_bytes(b'too large'))
        self.assertEqual(raised.exception.reason, 'compile_error')
        self.assertEqual(target.read_text(), 'outside')

    def test_remove_tree_handles_locked_directories_and_links(self):
        tree = self.root / 'locked'; (tree / 'a/b').mkdir(parents=True)
        (tree / 'a/b/f').write_text('x'); outside = self.root / 'keep'; outside.mkdir(); (outside / 'k').write_text('k')
        (tree / 'a/link').symlink_to(outside)
        (tree / 'a/b').chmod(0); (tree / 'a').chmod(0); tree.chmod(0)
        s.remove_tree(tree)
        self.assertFalse(os.path.lexists(tree))
        self.assertEqual((outside / 'k').read_text(), 'k')

    def test_launch_contract(self):
        plan = s.make_plan(['A.B', 'C'], [dict(module='C', theorem='t', target=None, relation=None, claim_id=None)])
        self.assertEqual([(p['kind'], p['module']) for p in plan],
                         [('build', 'A.B'), ('build', 'C'), ('replay', 'A.B'), ('replay', 'C'), ('check', 'C')])
        for step in plan:
            argv = s.bwrap_argv(Path('/runtime'), Path('/job'), step, Path('/dev/shm/x/s000'))
            for flag in ('--unshare-net', '--unshare-pid', '--unshare-user', '--unshare-ipc', '--unshare-uts',
                         '--unshare-cgroup', '--disable-userns', '--assert-userns-disabled', '--clearenv', '--die-with-parent'):
                self.assertIn(flag, argv)
            self.assertEqual(argv.count('--ro-bind'), 5)
            self.assertEqual(argv[argv.index('/control') - 1], '/job/control')
            self.assertEqual(argv[argv.index('/build') - 1], '/job/objects')
            # Only a build step gets a writable host location, and only /out.
            self.assertEqual(argv.count('--bind'), 1 if step['kind'] == 'build' else 0)
            if step['kind'] == 'build':
                self.assertEqual(argv[argv.index('--bind') + 1:argv.index('--bind') + 3], ['/dev/shm/x/s000', '/out'])
            self.assertIn('LEAN_ABORT_ON_PANIC', argv)
            self.assertNotIn('HOME', argv)
            self.assertEqual(argv[argv.index('/lean/bin/lean') + 1:], step['args'])
        self.assertEqual(plan[2]['args'], ['--threads=1', '--run', '/control/Replay.lean', 'A.B'])
        self.assertEqual(plan[3]['args'], ['--threads=1', '--run', '/control/Replay.lean', 'C', 't'])
        argv = s.systemd_argv('lean-recheck-test', '/job.json', 3)
        self.assertIn('--property=RuntimeMaxSec=120s', argv)
        self.assertEqual(argv[-4:], ['--job', '/job.json', '--step', '3'])


class StrictAdmissionTests(unittest.TestCase):
    """The recheck's own admission after the unchanged product filter."""
    def setUp(self):
        self.root = new_path('strict')
        self.root.mkdir(parents=True)
        fake_runtime(self.root)

    def product_admits(self, text):
        path = self.root / (str(uuid.uuid4()) + '.lean')
        path.write_text(text)
        try:
            s.audit.inspect_model(path, {})
            return True
        except s.audit.AuditError:
            return False

    def categories(self, text):
        return {f[0] for f in s.strict_findings(text)}

    def test_refuses_constructs_the_product_filter_admits(self):
        cases = {
            '@[term_elab Lean.Parser.Term.paren] def a : Nat := 1\n': 'attribute',
            '@[command_elab Lean.Parser.Command.open] def a : Nat := 1\n': 'attribute',
            '@[init] def a : IO Unit := pure ()\n': 'attribute',
            '@[tactic Lean.Parser.Tactic.done] def a : Nat := 1\n': 'attribute',
            '@[app_unexpander Nat.succ] def a : Nat := 1\n': 'attribute',
            '@ [simp] theorem a : True := True.intro\n': 'attribute',
            '@[simp, reducible] def a : Nat := 1\n': 'attribute',
            'def a : Nat := 1\nattribute [simp] a\n': 'attribute',
            'notation "tw" => 2\n': 'notation',
            'local notation "tw" => 2\n': 'notation',
            'infixl:65 " +++ " => Nat.add\n': 'notation',
            'infixr:65 " +++ " => Nat.add\n': 'notation',
            'prefix:max "√" => Nat.sqrt\n': 'notation',
            'postfix:max "!" => Nat.succ\n': 'notation',
            'binder_predicate x " > " y:term => `($x > $y)\n': 'notation',
            'declare_syntax_cat thing\n': 'metaprogramming',
            'simproc reduceFoo (Nat.succ _) := fun _ => pure .continue\n': 'metaprogramming',
            'dsimproc_decl reduceFoo (Nat.succ _) := fun _ => pure .continue\n': 'metaprogramming',
            'run_cmd pure ()\n': 'metaprogramming',
            'run_elab pure ()\n': 'metaprogramming',
            'run_meta pure ()\n': 'metaprogramming',
            'register_simp_attr mySimp\n': 'metaprogramming',
            'def a := Lean.Elab.Term.term_elab\n': 'metaprogramming',
            'partial def loop : Nat → Nat := loop\n': 'unsafe_or_partial',
            'theorem t : 2 = 2 := by decide +native\n': 'native_evaluation',
            'theorem t : Lean.reduceBool true = true := rfl\n': 'native_evaluation',
            'theorem t : Lean.reduceNat 2 = 2 := rfl\n': 'native_evaluation',
            'def t := @Lean.ofReduceBool\n': 'native_evaluation',
            'def t := Lean.trustCompiler\n': 'native_evaluation',
            'class Bad where\n  x : Nat\n': 'class_or_alias',
            'export Nat (add)\n': 'class_or_alias',
            'open Nat renaming add → plus\n': 'class_or_alias',
            'inductive R | a\n  deriving DecidableEq, Nonempty\n': 'deriving',
            'deriving instance Nonempty for Nat\n': 'deriving',
            'instance (priority := low) : Decidable True := isTrue trivial\n': 'instance',
            'instance : OfNat Nat 7 := ⟨8⟩\n': 'instance',
            'instance : Coe Bool Nat := ⟨fun _ => 0⟩\n': 'instance',
            'instance : Add Nat := ⟨Nat.mul⟩\n': 'instance',
            'instance : BEq Nat := ⟨fun _ _ => true⟩\n': 'instance',
            'instance := 1\n': 'instance',
            'def p := { x : Int //- c -/ }\ninstance : OfNat Nat 7 := ⟨8⟩\n-/ }\n': 'comment_opener',
            'theorem t (x : Int) : True \\/-x = x := Or.inl trivial\n#eval 1\n': 'comment_opener',
            'def f : IO Unit := do\n  let z <--1\n  pure ()\nset_option maxHeartbeats 0\n': 'comment_opener',
            '/--/ " -/\n@[term_elab x] def y := 1\n-- "\n': 'mask_divergence',
            'def q := 1--x\n': 'comment_opener',
            'instance (priority := high) : Inhabited Nat := ⟨7⟩\n': 'instance',
            'instance : ToString Nat := ⟨fun _ => "five"⟩\n': 'instance',
            'instance : Inhabited Nat := ⟨7⟩\n': 'instance',
            'instance : Repr Nat := ⟨fun _ _ => .nil⟩\n': 'instance',
            'deriving instance Ord for Int\n': 'deriving',
            'deriving instance Repr for Nat\n': 'deriving',
            'def n : Nat := (OfNat.ofNat 3 : Nat)\n': 'numeral_or_coercion',
            'abbrev Decidable := Add Nat\n': 'shadowing',
            'structure S where\n  Repr : Nat\n': 'shadowing',
            'inductive T\n  | Inhabited\n': 'shadowing',
            'def «weird» : Nat := 1\n': 'escaped_identifier',
            'def N := 1\ndef x := N.r"a\\"b"\n@[term_elab foo] def y := 1\n-- "\n': 'literal_opener',
            "def x? : Nat := 1\ndef y := x?'\"'\n@[term_elab foo] def z := 1\n-- \"\n": 'literal_opener',
        }
        for text, category in cases.items():
            with self.subTest(text=text):
                self.assertIn(category, self.categories('import Std\n' + text))
        # The verifier's observation: these pass the unchanged product filter.
        for text in ('@[term_elab Lean.Parser.Term.paren] def a : Nat := 1\n', '@[init] def a : IO Unit := pure ()\n',
                     '@[tactic Lean.Parser.Tactic.done] def a : Nat := 1\n', '@[app_unexpander Nat.succ] def a : Nat := 1\n',
                     '@[command_elab Lean.Parser.Command.open] def a : Nat := 1\n', 'notation "tw" => 2\n',
                     'infixl:65 " +++ " => Nat.add\n', 'run_cmd pure ()\n', 'instance : OfNat Nat 7 := ⟨8⟩\n',
                     'theorem t : 2 = 2 := by decide +native\n', 'theorem t : Lean.reduceBool true = true := rfl\n'):
            with self.subTest(product=text):
                self.assertTrue(self.product_admits('import Std\n' + text))
        self.assertTrue(self.product_admits(TERM_ELAB))

    def test_overlap_comments_strings_and_identifiers_do_not_trigger(self):
        admitted = [
            '-- @[term_elab foo] notation infixl instance : OfNat class\ntheorem safe : True := True.intro\n',
            '/- @[init] @[command_elab x] /- nested @[tactic t] -/ macro_rules -/\ntheorem safe : True := True.intro\n',
            '/-- Doc @[simp] notation `infixl` -/\ntheorem safe : True := True.intro\n',
            'def label : String := "@[term_elab x] notation infixl attribute [simp] instance : OfNat"\n',
            "def quote : Char := '\"'\ndef tick : Char := '@'\ndef h' : Nat := 1\ndef h'' : Nat := h'\n",
            'def raw : String := r#"@[app_unexpander x] "quoted" notation"#\n',
            'def macroeconomic : Nat := 1\ndef prefixLength : Nat := 2\ndef isPrefixOf : Nat := 3\n'
            'def prefix_sum : Nat := 4\ndef elaborate : Nat := 5\ndef classify : Nat := 6\n'
            'def instanceCount : Nat := 7\ndef Coeff : Nat := 8\ndef syntaxTree : Nat := 9\ndef notations : Nat := 10\n'
            'def infixOp : Nat := 11\ndef derivingNote : Nat := 12\ndef attributes : Nat := 13\ndef partialSum : Nat := 14\n'
            'def nativeCount : Nat := 15\ndef reduceBoolean : Nat := 16\n',
            'inductive Role | a | b\n  deriving DecidableEq, Repr\nstructure P where\n  r : Role\n  deriving BEq, Hashable, Ord, Inhabited\n',
            '/-- doc -/\ntheorem safe : 1 - -1 = (2 : Int) := by decide -- trailing\n/-! module doc -/\n/-\n  block -/\n',
            'def mayStay (u : Fin 6) : Prop := u = u\ninstance (u : Fin 6) : Decidable (mayStay u) := inferInstanceAs (Decidable (u = u))\n'
            'instance instN : Nonempty Nat := ⟨0⟩\n'
            'instance {α} [DecidableEq α] (p : α → Prop) [DecidablePred p] : DecidablePred (fun x : α => p x) := inferInstance\n',
            'namespace Person\ndef x : Nat := 1\nend Person\nopen Person in\ntheorem safe : x = 1 := rfl\n',
            'theorem safe (h : Decidable True) [inst : DecidableEq Nat] : True := True.intro\n',
            'theorem safe : (fun (x : Nat) => x) 1 = 1 := rfl\ndef pair : Nat × Nat := (1, 2)\n',
        ]
        for text in admitted:
            with self.subTest(text=text):
                self.assertEqual(s.strict_findings('import Std\n' + text), [])
        self.assertEqual(s.strict_findings((s.FIXTURES / 'passing.lean').read_text()), [])

    def test_deliberate_over_refusals_are_pinned(self):
        # Exact identifier components and fixed prefix families are refused even
        # when they are harmless names; failing closed is intended here.
        cases = {'def f (s : String) := s.prefix\n': 'notation', 'def register_count : Nat := 1\n': 'metaprogramming',
                 'def macro_count : Nat := 1\n': 'metaprogramming', 'def t := @Eq.elab_as_elim\n': 'metaprogramming',
                 'def native : Nat := 1\n': 'native_evaluation', 'def Foo.class : Nat := 1\n': 'class_or_alias',
'instance : ∀ n : Nat, Decidable (n = n) := inferInstance\n': 'instance'}
        for text, category in cases.items():
            with self.subTest(text=text):
                self.assertIn(category, self.categories('import Std\n' + text))

    def test_guillemets_fail_closed_even_in_strings(self):
        # «"» could make the product lexer mask real code; refused wherever it occurs.
        for text in ('def label : String := "«x»"\n', '-- «x»\n', 'def «a"b» := 1\n@[term_elab x] def c := 1\ndef «"d» := 2\n'):
            with self.subTest(text=text):
                self.assertEqual(self.categories(text), {'escaped_identifier'})

    def test_categories_do_not_suppress_each_other(self):
        text = 'import Std\n@[simp] theorem a : True := True.intro\nnotation "tw" => 2\ninstance : OfNat Nat 7 := ⟨8⟩\npartial def f : Nat → Nat := f\n'
        self.assertTrue({'attribute', 'notation', 'instance', 'numeral_or_coercion', 'unsafe_or_partial'} <= self.categories(text))
        lines = {f[0]: f[2] for f in s.strict_findings(text)}
        self.assertEqual((lines['attribute'], lines['notation'], lines['instance']), (2, 3, 4))

    def job(self, files, gate=None):
        return dict(schema_version=1, runtime=str(self.root / 'runtime'), runtime_hash='0' * 64,
                    gate=str(gate or self.root / 'missing-gate.json'), run_dir=str(self.root / str(uuid.uuid4())),
                    sources=[dict(module=m, path=str(p), sha256=s.file_hash(p)) for m, p in files], proofs=[])

    def test_term_elab_refused_in_production_with_zero_lean(self):
        path = self.root / 'TermElab.lean'; path.write_text(TERM_ELAB)
        helper = self.root / 'Helper.lean'; helper.write_text('import Std\ntheorem helper : True := True.intro\n')
        for files in ([('Fixture', path)], [('Helper', helper), ('Fixture', path)]):
            with self.subTest(modules=len(files)), mock.patch.object(s.subprocess, 'Popen', side_effect=AssertionError('Lean was launched')), \
                 mock.patch.object(s, 'check_gate', side_effect=AssertionError('gate reached')):
                job = self.job(files)
                result = s.run_job(job)
                self.assertEqual((result['outcome'], result['reason']), ('filter_rejected', 'strict_rejection'), result)
                self.assertIn('attribute', result['detail'])
                self.assertEqual(result['lean_subprocesses'], 0)
                self.assertEqual(result['argv'], [])
                self.assertFalse((Path(job['run_dir']) / 'job.json').exists())
                self.assertEqual(json.loads((Path(job['run_dir']) / 'result.json').read_text())['reason'], 'strict_rejection')
        self.assertIn('strict_rejection', s.REASONS)

    def test_r1_r2_forms_pass_product_filter_but_are_refused_with_zero_lean(self):
        forms = {
            'subtype': 'import Std\ndef p := { x : Int //- x = 0 }\ninstance : OfNat Nat 7 := ⟨8⟩\ndef q : Int := -/ 1\ntheorem safe : True := trivial\n',
            'or': 'import Std\ntheorem t (x : Int) : True \\/-x = x\n@[term_elab Lean.Parser.Term.paren] def e : Nat := 1\n-/ := Or.inl trivial\ntheorem safe : True := trivial\n',
            'arrow': 'import Std\ndef f : Nat := Id.run do\n  let z <--1 + (set_option maxRecDepth 1 in notation "seven" => 7)\n  z\ntheorem safe : True := trivial\n',
            'doc': 'import Std\n/--/ " -/\n@[term_elab Lean.Parser.Term.paren] def y : Nat := 1\n-- "\ntheorem safe : True := trivial\n',
            'priority': 'import Std\ninstance (priority := high) : Inhabited Nat := ⟨7⟩\ntheorem safe : True := trivial\n',
            'tostring': 'import Std\ninstance : ToString Nat := ⟨fun _ => "five"⟩\ntheorem safe : True := trivial\n',
            'deriving': 'import Std\nderiving instance Ord for Int\ntheorem safe : True := trivial\n',
        }
        for name, text in forms.items():
            path = self.root / (name + '.lean'); path.write_text(text)
            with self.subTest(form=name):
                self.assertTrue(self.product_admits(text), name)
                with mock.patch.object(s.subprocess, 'Popen', side_effect=AssertionError('Lean was launched')):
                    result = s.run_job(self.job([('Fixture', path)]))
                self.assertEqual((result['outcome'], result['reason'], result['lean_subprocesses']),
                                 ('filter_rejected', 'strict_rejection', 0), result)

    def test_interpolated_strings_refused_without_enumerating_macros(self):
        hiding = {
            # The verifier's a07 shape: a space after s! defeats the product's `!"` check.
            'a07': 'import Std\ndef leak : String := s! "{(by native_decide : 2 + 2 = 4)}"\ntheorem safe : True := trivial\n',
            'dbg_trace': 'import Std\ndef t : Nat := dbg_trace "{(List.range 3).length}"; 0\ntheorem safe : True := trivial\n',
        }
        for name, text in hiding.items():
            path = self.root / (name + '.lean'); path.write_text(text)
            with self.subTest(form=name):
                self.assertTrue(self.product_admits(text))
                self.assertEqual(self.categories(text), {'interpolation'})
                with mock.patch.object(s.subprocess, 'Popen', side_effect=AssertionError('Lean was launched')):
                    result = s.run_job(self.job([('Fixture', path)]))
                self.assertEqual((result['outcome'], result['reason'], result['lean_subprocesses']),
                                 ('filter_rejected', 'strict_rejection', 0), result)
        # Without the space the product filter already refuses it (and strict would too).
        glued = 'import Std\ndef x : Nat := 1\ndef m : String := s!"a{x}b"\ntheorem safe : True := trivial\n'
        path = self.root / 'glued.lean'; path.write_text(glued)
        self.assertFalse(self.product_admits(glued))
        self.assertTrue(s.strict_findings(glued))
        with mock.patch.object(s.subprocess, 'Popen', side_effect=AssertionError('Lean was launched')):
            self.assertEqual(s.run_job(self.job([('Fixture', path)]))['reason'], 'lexical_rejection')
        # Neighbours: no brace in the string, a brace in a comment, a brace character
        # literal and structure braces outside strings stay admitted.
        for text in ('def p : String := "plain text, no brace"\n', '-- {comment} "x {y}"\n/- {block} -/\ndef q := 1\n',
                     "def c : Char := '{'\n", 'structure P where\n  a : Nat\ndef p : P := { a := 1 }\n'):
            with self.subTest(admitted=text):
                self.assertEqual(s.strict_findings('import Std\n' + text), [])
        # A brace in a raw string also fails closed (no verified claim that no macro accepts one).
        self.assertEqual(self.categories('def r : String := r"{raw}"\n'), {'interpolation'})

    def test_runtime_without_mount_points_is_infrastructure(self):
        broken = self.root / 'old-runtime'
        for name in s.MOUNT_POINTS:
            if name not in ('build', 'out'): (broken / name).mkdir(parents=True)
        job = self.job([('Fixture', s.FIXTURES / 'passing.lean')]); job['runtime'] = str(broken)
        with mock.patch.object(s, 'runtime_manifest', return_value={}), \
             mock.patch.object(s.subprocess, 'Popen', side_effect=AssertionError('Lean was launched')):
            job['runtime_hash'] = s.sha(s.canonical({}))
            result = s.run_job(job)
        self.assertEqual((result['outcome'], result['reason'], result['lean_subprocesses']), ('not_run', 'infrastructure_error', 0))
        self.assertIn("lacks mount points ['build', 'out']", result['detail'])

    def test_product_rejection_is_counted_before_strict(self):
        path = self.root / 'Both.lean'
        path.write_text('import Std\nmacro "evil" : term => `(True)\n@[simp] theorem safe : True := True.intro\n')
        self.assertTrue(s.strict_findings(path.read_text()))
        with mock.patch.object(s.subprocess, 'Popen', side_effect=AssertionError('spawn')):
            result = s.run_job(self.job([('Fixture', path)]))
        self.assertEqual((result['outcome'], result['reason']), ('filter_rejected', 'lexical_rejection'))

    def test_strict_clean_source_still_requires_gate(self):
        with mock.patch.object(s, 'runtime_manifest', return_value={}), \
             mock.patch.object(s.subprocess, 'Popen', side_effect=AssertionError('spawn')):
            job = self.job([('Fixture', s.FIXTURES / 'passing.lean')]); job['runtime_hash'] = s.sha(s.canonical({}))
            result = s.run_job(job)
        self.assertEqual((result['outcome'], result['reason']), ('not_run', 'infrastructure_error'), result)
        self.assertIn('receipt', result['detail'])


class ProbeTests(unittest.TestCase):
    """Verifier probes: separate receipt type, identical launch path, never countable."""
    def setUp(self):
        self.root = new_path('probe-portable')
        self.root.mkdir(parents=True)
        fake_runtime(self.root)
        self.folder = probe_folder(self.root)
        self.listed = self.folder / 'Listed.lean'
        nonce = uuid.uuid4().hex
        # Product-forbidden on purpose (#eval): only a probe receipt can admit it.
        self.listed.write_text(f'import Std\n#eval IO.println "PROBE_{nonce}"\ntheorem safe : True := True.intro\n')
        self.unlisted = self.folder / 'Unlisted.lean'
        self.unlisted.write_text('import Std\ntheorem safe : True := True.intro\n')
        self.receipt = probe_receipt(self.folder / 'probe-receipt.json', self.folder, [s.file_hash(self.listed)], FAKE_BINDINGS)
        self.patches = [mock.patch.object(s, 'binding_manifest', return_value=FAKE_BINDINGS),
                        mock.patch.object(s, 'runtime_manifest', return_value={}),
                        mock.patch.object(s.subprocess, 'Popen', side_effect=AssertionError('Lean was launched'))]
        for patch in self.patches: patch.start()
        self.digest = s.sha(s.canonical({}))

    def tearDown(self):
        for patch in self.patches: patch.stop()

    def job(self, files, gate=None, root=None):
        return probe_job(root or self.folder, self.root / 'runtime', self.digest, gate or self.receipt,
                         files, self.root / ('run-' + str(uuid.uuid4())))

    def approving(self, directory):
        # Fake bindings only, inside a TemporaryDirectory; never a real approval.
        path = Path(directory) / 'approving-fake.json'
        path.write_bytes(s.canonical(dict(schema_version=1, approved=True, bindings=FAKE_BINDINGS,
                                          fixture_observations={n: True for n in s.FIXTURE_HASHES})))
        return path

    def test_probe_receipt_never_opens_production_gate(self):
        with tempfile.TemporaryDirectory(dir=s.WORK) as directory:
            # Positive control: identical bindings, approving shape, accepted.
            self.assertRegex(s.check_gate(str(self.approving(directory)), self.digest), '^[a-f0-9]{64}$')
        with self.assertRaises(s.Rejection) as raised:
            s.check_gate(str(self.receipt), self.digest)
        self.assertEqual(raised.exception.reason, 'infrastructure_error')
        self.assertIn('probe receipt can never open', str(raised.exception))
        job = self.job([('Fixture', s.FIXTURES / 'passing.lean')]); del job['probe_root']
        result = s.run_job(job)
        self.assertEqual((result['outcome'], result['reason']), ('not_run', 'infrastructure_error'), result)
        self.assertIn('probe receipt can never open', result['detail'])
        self.assertEqual(result['lean_subprocesses'], 0)
        # A probe-shaped job is refused by production before anything is read.
        self.assertEqual(s.run_job(self.job([('Listed', self.listed)]))['reason'], 'invalid_metadata')

    def test_approving_receipt_never_authorizes_probe(self):
        _, allowed = s.check_probe_gate(str(self.receipt), self.digest, str(self.folder))   # positive control
        self.assertEqual(allowed, {s.file_hash(self.listed)})
        with tempfile.TemporaryDirectory(dir=s.WORK) as directory:
            approving = self.approving(directory)
            with self.assertRaises(s.Rejection) as raised:
                s.check_probe_gate(str(approving), self.digest, str(self.folder))
            self.assertIn('approving receipt can never authorize', str(raised.exception))
            result = s.run_probe(self.job([('Listed', self.listed)], gate=approving))
        self.assertEqual((result['outcome'], result['probe_outcome'], result['reason']),
                         (s.PROBE_OUTCOME, 'not_run', 'infrastructure_error'), result)
        self.assertIn('approving receipt can never authorize', result['detail'])
        self.assertEqual(result['lean_subprocesses'], 0)

    def test_receipt_shape_is_exact(self):
        good = json.loads(self.receipt.read_text())
        variants = [dict(good, kind='other'), dict(good, probe_root=str(self.root)), dict(good, schema_version=True),
                    dict(good, probe_sources=[]), dict(good, probe_sources=['F' * 64]), dict(good, extra=1),
                    dict(good, probe_sources=['b' * 64, 'a' * 64]), dict(good, bindings={'other': '1'})]
        for i, variant in enumerate(variants):
            path = self.folder / f'variant-{i}.json'; path.write_bytes(s.canonical(variant))
            with self.subTest(i=i), self.assertRaises(s.Rejection):
                s.check_probe_gate(str(path), self.digest, str(self.folder))

    def test_listed_probe_skips_filters_and_unlisted_is_refused(self):
        prepared = s._prepare(self.job([('Listed', self.listed)]), None, probe=True)
        self.assertIs(prepared['probe'], True)
        self.assertIsNone(prepared['fixture'])
        self.assertEqual(prepared['order'], ['Listed'])
        self.assertEqual([r['theorem'] for r in prepared['requests']], ['safe', 'safe'])
        self.assertEqual(prepared['gate_hash'], s.sha(s.canonical(json.loads(self.receipt.read_text()))))
        with self.assertRaises(s.audit.AuditError):   # the product filter would refuse this listed file
            s.audit.inspect_model(self.listed, {})
        for files in ([('Unlisted', self.unlisted)], [('Listed', self.listed), ('Unlisted', self.unlisted)]):
            with self.subTest(files=len(files)):
                job = self.job(files)
                result = s.run_probe(job)
                self.assertEqual((result['outcome'], result['probe_outcome'], result['reason']),
                                 (s.PROBE_OUTCOME, 'not_run', 'infrastructure_error'), result)
                self.assertIn('not listed', result['detail'])
                self.assertEqual(result['lean_subprocesses'], 0)
                self.assertFalse((Path(job['run_dir']) / 'sources/Unlisted.lean').exists())

    def test_probe_path_rules(self):
        outside = new_path('probe-outside'); outside.mkdir(parents=True)
        stray = outside / 'Stray.lean'; stray.write_text(self.listed.read_text())
        campaign = self.folder / 'harness-matrix-copy'; campaign.mkdir()
        (campaign / 'Campaign.lean').write_text(self.listed.read_text())
        validation = self.folder / 'validation/runs/x'; validation.mkdir(parents=True)
        (validation / 'Audit.lean').write_text(self.listed.read_text())
        symlink = self.folder / 'Link.lean'; symlink.symlink_to(self.listed)
        hardlink = self.folder / 'Hard.lean'; os.link(self.unlisted, hardlink)
        fifo = self.folder / 'Fifo.lean'; os.mkfifo(fifo)
        def sourced(path):
            return [dict(module='Listed', path=str(path), sha256=s.file_hash(self.listed))]
        cases = [sourced(stray), sourced(campaign / 'Campaign.lean'), sourced(validation / 'Audit.lean'),
                 sourced(self.folder), sourced(str(self.folder) + '/../' + self.folder.name + '/Listed.lean')]
        for sources in cases:
            job = self.job([('Listed', self.listed)]); job['sources'] = sources
            with self.subTest(path=sources[0]['path']):
                result = s.run_probe(job)
                self.assertEqual((result['probe_outcome'], result['reason']), ('fail', 'unsafe_path'), result)
        for path in (symlink, hardlink, fifo):
            job = self.job([('Listed', self.listed)]); job['sources'] = sourced(path)
            with self.subTest(path=str(path)):
                self.assertEqual(s.run_probe(job)['reason'], 'unsafe_path')
        for root in (s.ROOT, s.ROOT.parent, Path('/'), self.root / 'harness-matrix-x'):
            with self.subTest(root=str(root)):
                self.assertEqual(s.run_probe(self.job([('Listed', self.listed)], root=root))['reason'], 'unsafe_path')
        self.folder.chmod(0o770)
        self.assertEqual(s.run_probe(self.job([('Listed', self.listed)]))['reason'], 'unsafe_path')
        self.folder.chmod(0o700)
        alias = self.root / 'alias'; alias.symlink_to(self.folder)
        job = probe_job(alias, self.root / 'runtime', self.digest, self.receipt, [('Listed', alias / 'Listed.lean')],
                        self.root / ('run-' + str(uuid.uuid4())))
        self.assertEqual(s.run_probe(job)['reason'], 'unsafe_path')

    def test_probe_result_is_never_a_recheck_result(self):
        job = self.job([('Unlisted', self.unlisted)])
        result = s.run_probe(job)
        self.assertEqual(result['outcome'], s.PROBE_OUTCOME)
        self.assertNotIn(result['outcome'], s.OUTCOMES)
        self.assertNotIn(s.PROBE_OUTCOME, s.OUTCOMES)
        self.assertEqual(json.loads((Path(job['run_dir']) / 'result.json').read_text())['outcome'], s.PROBE_OUTCOME)
        # Even a passing probe cannot be recorded through the planning seam.
        for probe_result in (result, dict(result, probe_outcome='pass', reason=None)):
            with self.subTest(probe_outcome=probe_result['probe_outcome']), self.assertRaises(recheck.RecheckError) as raised:
                recheck.execute_jobs([{'id': 'X3-proof-00001'}], lambda job: probe_result)
            self.assertEqual(raised.exception.reason, 'infrastructure_error')

    def test_exec_rejects_inconsistent_probe_mode(self):
        run_dir = self.root / ('exec-' + str(uuid.uuid4())); run_dir.mkdir()
        job = self.job([('Listed', self.listed)]); job['run_dir'] = str(run_dir)
        production = {k: v for k, v in job.items() if k != 'probe_root'}
        for prepared in (dict(job=job, fixture=None, probe=False), dict(job=production, fixture=None, probe=True),
                         dict(job=job, fixture='passing', probe=True), dict(job=job, fixture=None, probe=1),
                         dict(job=job, fixture=None)):
            path = run_dir / 'job.json'
            path.unlink(missing_ok=True); path.write_bytes(s.canonical(prepared))
            with self.subTest(prepared={k: v for k, v in prepared.items() if k != 'job'}), \
                 mock.patch.object(s.os, 'execve', side_effect=AssertionError('exec')):
                with self.assertRaises((s.Rejection, KeyError)) as raised:
                    s._exec(str(path), 0)
                if 'probe' in prepared:
                    self.assertIn('Inconsistent prepared job mode', str(raised.exception))


@unittest.skipUnless(sys.platform == 'linux', 'Requires Linux cgroup v2, systemd user scopes, Bubblewrap and AUDIT_TEST_LEAN')
class SandboxIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for path in ('/usr/bin/bwrap', '/usr/bin/systemd-run', os.environ.get('AUDIT_TEST_LEAN', '')):
            if not path or not Path(path).is_file():
                raise RuntimeError('Required apparatus prerequisite missing: ' + path)
        cls.root = new_path('suite'); cls.root.mkdir(parents=True)
        # v2: the runtime gained the /build and /out mount points (new runtime hash).
        cached = s.WORK / 'runtime-location-v2.json'
        if cached.is_file():
            entry = json.loads(cached.read_text())
            cls.runtime, cls.digest = Path(entry['path']), entry['hash']
            if s.sha(s.canonical(s.runtime_manifest(cls.runtime))) != cls.digest:
                raise RuntimeError('Cached runtime changed')
        else:
            cls.runtime = cls.root / 'runtime'
            cls.digest = s.build_runtime(os.environ['AUDIT_TEST_LEAN'], cls.runtime)
            cached.write_text(json.dumps({'path': str(cls.runtime), 'hash': cls.digest}))
        print('\nEVIDENCE_DIRECTORY=' + str(cls.root), flush=True)
        cls.controls = {}
        result = cls.run_fixture('control')
        if result['outcome'] != 'pass':
            raise RuntimeError('STOP: trivial exact-launch control failed: ' + json.dumps(result))
        limits = result['evidence']['limits']
        if any(limits[k] != v for k, v in {'memory.max':'2147483648','memory.swap.max':'0','pids.max':'64','cpu.max':'100000 100000'}.items()):
            raise RuntimeError('STOP: effective limits not enforced')
        cls.controls['control'] = result
        # Every trivial isolation assertion must pass before the first flood.
        cls('test_controls_limits_namespaces_and_environment').test_controls_limits_namespaces_and_environment()
        # Observe the normal outer deadline before any allocation/task/output flood.
        result = cls.run_fixture('sleep')
        if result['reason'] != 'timeout' or not result['evidence'].get('cleanup_empty') or result['seconds'] > 125:
            raise RuntimeError('STOP: normal deadline control failed: ' + json.dumps(result))
        cls.controls['sleep'] = result
        # SIGKILL proves die-with-parent; SIGSTOP leaves the parent alive and
        # unable to supervise, so only systemd can enforce the independent deadline.
        cls.controls['sigkill'] = cls.controller_control(signal.SIGKILL, 'sigkill')
        # The killed controller left its build output on /dev/shm; the next job
        # sweeps it under the executor lock before doing anything else.
        stale = sorted(os.listdir(s.shm_parent())) if s.shm_parent().exists() else []
        swept = cls.run_fixture('passing')
        remaining = os.listdir(s.shm_parent())
        if not stale or swept['evidence'].get('stale_outputs_removed', 0) < len(stale) or remaining:
            raise RuntimeError(f'STOP: stale build outputs not swept: {stale} {remaining} {swept["evidence"].get("stale_outputs_removed")}')
        cls.controls['sweep'] = {'stale_before': stale, 'removed': swept['evidence']['stale_outputs_removed'], 'remaining': remaining}
        print(f'CONTROL sweep: {len(stale)} stale output dir(s) removed', flush=True)
        cls.controls['sigstop'] = cls.controller_control(signal.SIGSTOP, 'sigstop')
        (cls.root / 'controls.json').write_bytes(s.canonical(cls.controls))
        print('CONTROL_GATES_PASSED: effective limits, outer deadline, controller SIGKILL deadline', flush=True)

    @classmethod
    def controller_control(cls, stop_signal, label):
        ctx = multiprocessing.get_context('fork')
        path = cls.root / (label + '-' + str(uuid.uuid4()))
        process = ctx.Process(target=fixture_worker, args=('sleep', str(cls.runtime), cls.digest, str(path)))
        start = time.monotonic(); process.start()
        cgroup = None
        try:
            while time.monotonic() - start < 20:
                if (path / 'job.json').is_file():
                    unit = s.step_unit(json.loads((path / 'job.json').read_text())['unit'], 0)
                    cgroup = Path(f'/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/app.slice/{unit}.scope')
                    try:
                        pids = (cgroup / 'cgroup.procs').read_text().split()
                        if any(b'/src/Fixture.lean' in Path(f'/proc/{pid}/cmdline').read_bytes() for pid in pids): break
                    except OSError: pass
                if not process.is_alive(): raise RuntimeError('Controller exited before control')
                time.sleep(.05)
            else: raise RuntimeError('Fixture never reached sleeping Lean process')
            props = s.scope_properties(unit)
            before = s.cgroup_values(cgroup)
            stopped_at = time.monotonic()
            os.kill(process.pid, stop_signal)
            deadline = stopped_at + 5 if stop_signal == signal.SIGKILL else start + 128
            while time.monotonic() < deadline:
                values = s.cgroup_values(cgroup)
                if not values or 'populated 0' in values.get('cgroup.events',''): break
                time.sleep(.05)
            elapsed = time.monotonic() - start
            if values and 'populated 0' not in values.get('cgroup.events',''):
                raise RuntimeError(f'STOP: {label} cleanup failed: {elapsed}, {values}')
            if stop_signal == signal.SIGSTOP and not 115 <= elapsed <= 127:
                raise RuntimeError(f'STOP: independent systemd deadline not observed: {elapsed}')
            if stop_signal == signal.SIGSTOP: os.kill(process.pid, signal.SIGCONT)
            process.join(5)
            if process.is_alive(): raise RuntimeError('Controller did not terminate after cleanup')
            evidence = dict(seconds=elapsed, after_signal_seconds=time.monotonic()-stopped_at,
                before=before, systemd=props, controller_exitcode=process.exitcode,
                cleanup_empty=True, unit=unit)
            (path / 'host-observation.json').write_bytes(s.canonical(evidence))
            print(f'CONTROL {label}: {elapsed:.3f}s, cgroup empty', flush=True)
            return evidence
        finally:
            if process.is_alive(): process.kill(); process.join(5)
            if cgroup is not None: s.kill_scope(unit, cgroup)

    def setUp(self):
        if getattr(self.__class__, 'infrastructure_failed', False):
            self.fail('STOP: prior infrastructure error; no further sandbox launch permitted')

    @classmethod
    def run_fixture(cls, name):
        result = s._run_fixture(name, cls.runtime, cls.digest, cls.root / (name + '-' + str(uuid.uuid4())))
        print(f'FIXTURE {name}: {result["outcome"]}/{result["reason"]} {result["seconds"]:.3f}s', flush=True)
        if result['reason'] == 'infrastructure_error':
            cls.infrastructure_failed = True
            raise RuntimeError('STOP: sandbox infrastructure failure: ' + json.dumps(result))
        return result

    def output(self, result):
        return Path(result['log_paths'][0]).read_text(errors='replace')

    def assert_empty(self, result):
        self.assertTrue(result['evidence']['cleanup_empty'], result)
        self.assertLessEqual(result['evidence']['cleanup_seconds'], 5)

    def test_controls_limits_namespaces_and_environment(self):
        result = self.controls['control']; text = self.output(result)
        self.assertIn('scratch-ok', text)
        self.assertIn('HOME=none', text)
        self.assertRegex(text, r'CapEff:\s+0000000000000000')
        self.assertRegex(text, r'Max file size\s+33554432\s+33554432')
        self.assertRegex(text, r'Max core file size\s+0\s+0')
        self.assertRegex(text, r'/scratch tmpfs rw,[^\n]*size=65536k')
        self.assertRegex(text, r'/src [^ ]+ ro[, ]')
        self.assertRegex(text, r'/checks [^ ]+ ro[, ]')
        self.assertRegex(text, r'/build [^ ]+ ro[, ]')
        self.assertRegex(text, r'/control [^ ]+ ro[, ]')
        self.assertRegex(text, r'/out tmpfs rw[, ]')
        self.assertRegex(text, r'/proc proc ro[, ]')
        self.assertRegex(text, r'/dev tmpfs ro[, ]')
        for name, host in result['evidence']['namespaces'].items():
            self.assertNotEqual(host, result['evidence']['sandbox_namespaces'][name])
        self.assert_empty(result)

    def test_sleep_deadline_and_controller_sigkill(self):
        self.assertEqual(self.controls['sleep']['reason'], 'timeout')
        self.assertIn('SLEEP_REACHED', self.output(self.controls['sleep']))
        self.assert_empty(self.controls['sleep'])
        self.assertEqual(self.controls['sigkill']['controller_exitcode'], -signal.SIGKILL)
        self.assertTrue(self.controls['sigkill']['cleanup_empty'])

    def test_trusted_passing(self):
        result = self.run_fixture('passing')
        self.assertEqual(result['outcome'], 'pass', result)
        self.assertEqual(len(result['checks']), 2)
        self.assertTrue(result['evidence']['steps_complete'])
        self.assertEqual([(r['kind'], r['exit_code']) for r in result['steps']],
                         [('build', 0), ('replay', 0), ('check', 0), ('check', 0)])
        stored = Path(result['log_paths'][0]).parent / 'objects/Fixture.olean'
        self.assertEqual(s.file_hash(stored), result['steps'][0]['object_sha256'])
        self.assertEqual(oct(stored.stat().st_mode & 0o777), '0o444')
        print('TYPICAL_JOB ' + json.dumps({'seconds': round(result['seconds'], 3),
              'steps': [(r['kind'], round(r['seconds'], 3)) for r in result['steps']]}), flush=True)
        self.assert_empty(result)

    def test_trusted_false_target(self):
        result = self.run_fixture('false_target')
        self.assertEqual(result['reason'], 'target_mismatch', result)
        self.assert_empty(result)

    def test_trusted_forbidden_axiom(self):
        result = self.run_fixture('forbidden_axiom')
        self.assertEqual(result['reason'], 'axioms_disallowed', result)
        self.assert_empty(result)

    def test_forged_axiom_output(self):
        result = self.run_fixture('forged')
        self.assertEqual(result['reason'], 'axioms_disallowed', result)
        self.assertIn('"event":"complete"', self.output(result))
        self.assert_empty(result)

    def test_network_operation_and_host_positive_control(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', 39761)); listener.listen(); listener.settimeout(1)
            nonce = uuid.uuid4().hex.encode()
            with socket.create_connection(('127.0.0.1', 39761)) as client:
                client.sendall(nonce)
                conn, _ = listener.accept()
                with conn: self.assertEqual(conn.recv(1024), nonce)
            result = self.run_fixture('network')
            self.assertEqual(result['outcome'], 'pass', result)
            self.assertIn('TCP_OPERATION_REACHED', self.output(result))
            self.assertRegex(self.output(result), r'TCP_DENIED:.*(refused|unreachable)')
            self.assertNotIn('TCP_UNEXPECTED_CONNECTED', self.output(result))
            with self.assertRaises(socket.timeout): listener.accept()
            self.assertNotEqual(result['evidence']['namespaces']['net'], result['evidence']['sandbox_namespaces']['net'])
            evidence = {'host_positive_control': True, 'listener_received_from_sandbox': False,
                        'namespace_differs': True, 'result': result}
            (self.root / 'network-observation.json').write_bytes(s.canonical(evidence))
            self.assert_empty(result)

    def test_filesystem_and_secret(self):
        # Plant after trusted preparation, before launch, within this job's own directory.
        nonce = uuid.uuid4().hex
        original = s._launch
        def plant(prepared, result, started):
            Path(prepared['job']['run_dir'], 'host-sibling').write_text(nonce)
            return original(prepared, result, started)
        with mock.patch.object(s, '_launch', side_effect=plant):
            result = self.run_fixture('filesystem')
        self.assertEqual(result['outcome'], 'pass', result)
        text = self.output(result)
        self.assertEqual(text.count('WRITE_DENIED:'), 2)
        self.assertIn('SCRATCH_OK', text); self.assertIn('SECRET_READ_DENIED', text)
        self.assertNotIn(nonce, text)
        parent = Path(result['log_paths'][0]).parent
        self.assertEqual((parent / 'host-sibling').read_text(), nonce)
        self.assertFalse((parent / 'sources/attempted').exists())
        (parent / 'host-observation.json').write_bytes(s.canonical({'host_control_reads_nonce': True, 'host_unchanged': True, 'nonce_absent': True}))
        self.assert_empty(result)

    def test_memory_flood(self):
        result = self.run_fixture('memory')
        self.assertEqual(result['reason'], 'memory_limit', result)
        self.assertIn('MEMORY_REACHED', self.output(result))
        step = result['steps'][0]['evidence']
        self.assertTrue(re.search(r'oom_kill [1-9]', step.get('last_cgroup', {}).get('memory.events', ''))
                        or (result['steps'][0]['exit_code'] == 137 and step['parent_oom_kill_delta'] > 0), step)
        self.assert_empty(result)

    def test_task_flood(self):
        result = self.run_fixture('tasks')
        self.assertEqual(result['reason'], 'process_limit', result)
        self.assertRegex(result['evidence']['last_cgroup']['pids.events'], r'max [1-9]')
        self.assert_empty(result)

    def test_output_flood(self):
        result = self.run_fixture('output')
        self.assertEqual(result['reason'], 'output_limit', result)
        self.assertTrue(result['truncated'])
        self.assertLessEqual(Path(result['log_paths'][0]).stat().st_size, s.LIMIT)
        self.assert_empty(result)

    def test_descendant_does_not_survive(self):
        result = self.run_fixture('descendant')
        self.assertEqual(result['outcome'], 'pass', result)
        self.assertIn('DESCENDANT_STARTED', self.output(result))
        self.assertLess(result['seconds'], 10)
        self.assert_empty(result)

    def test_scratch_limit(self):
        result = self.run_fixture('scratch')
        self.assertEqual(result['reason'], 'scratch_limit', result)
        self.assert_empty(result)

    def test_file_size_limit(self):
        result = self.run_fixture('fsize')
        self.assertEqual(result['reason'], 'scratch_limit', result)
        self.assertIn('file too large (error code: 27)', self.output(result))
        self.assert_empty(result)

    def probe_setup(self, text):
        # A probe receipt with REAL bindings is not approving; it is what a verifier writes.
        folder = probe_folder(self.root)
        path = folder / 'Probe.lean'; path.write_text(text)
        receipt = probe_receipt(folder / 'probe-receipt.json', folder, [s.file_hash(path)], s.binding_manifest(self.digest))
        return folder, path, receipt

    def test_probe_identical_launch_path(self):
        nonce = uuid.uuid4().hex
        # Harmless even unsandboxed: prints one nonce line. #eval is product-forbidden.
        folder, path, receipt = self.probe_setup(
            f'import Std\n#eval IO.println "PROBE_REACHED_{nonce}"\ntheorem safe : True := True.intro\n')
        # Same module name as the fixed fixture, so every per-step argument must match exactly.
        job = probe_job(folder, self.runtime, self.digest, receipt, [('Fixture', path)], self.root / ('probe-' + str(uuid.uuid4())))
        result = s.run_probe(job)
        print(f'PROBE: {result["outcome"]}/{result.get("probe_outcome")}/{result["reason"]} {result["seconds"]:.3f}s', flush=True)
        if result['reason'] == 'infrastructure_error':
            self.__class__.infrastructure_failed = True
            self.fail('STOP: probe infrastructure failure: ' + json.dumps(result))
        self.assertEqual((result['outcome'], result['probe_outcome'], result['reason']), (s.PROBE_OUTCOME, 'pass', None), result)
        self.assertIn('PROBE_REACHED_' + nonce, self.output(result))
        self.assertEqual(len(result['checks']), 2)
        self.assertTrue(result['evidence']['steps_complete'])
        self.assertEqual(result['evidence']['gate_hash'], s.sha(s.canonical(json.loads(receipt.read_text()))))
        self.assertEqual([r['kind'] for r in result['steps']], ['build', 'replay', 'check', 'check'])
        self.assertEqual(json.loads((Path(job['run_dir']) / 'job.json').read_text())['probe'], True)
        self.assertEqual(json.loads((Path(job['run_dir']) / 'result.json').read_text())['outcome'], s.PROBE_OUTCOME)
        self.assert_empty(result)
        fixture = self.run_fixture('passing')
        self.assertEqual(fixture['outcome'], 'pass', fixture)
        # Identical per-step systemd-run and Bubblewrap argument lists, up to run directory and unit UUID.
        self.assertEqual(normalized_launch(result), normalized_launch(fixture))
        self.assertEqual(len(normalized_launch(result)), 4)
        print('PROBE_LAUNCH=' + json.dumps(normalized_launch(result)), flush=True)
        for key in ('memory.max', 'memory.swap.max', 'pids.max', 'cpu.max'):
            self.assertEqual(result['evidence']['limits'][key], fixture['evidence']['limits'][key])
        for key in ('RuntimeMaxUSec', 'TimeoutStopUSec', 'MemoryMax', 'MemorySwapMax', 'TasksMax', 'CPUQuotaPerSecUSec'):
            self.assertEqual(result['evidence']['systemd'][key], fixture['evidence']['systemd'][key])
        self.assertEqual((result['evidence']['rlimits'], result['evidence']['nice']), (fixture['evidence']['rlimits'], fixture['evidence']['nice']))
        for name, host in result['evidence']['namespaces'].items():
            self.assertNotEqual(host, result['evidence']['sandbox_namespaces'][name])
        # The probe result can never be recorded as a recheck result.
        with self.assertRaises(recheck.RecheckError):
            recheck.execute_jobs([{'id': 'X3-proof-00001'}], lambda _: result)
        # The same real (non-approving) receipt never opens production.
        with self.assertRaises(s.Rejection):
            s.check_gate(str(receipt), self.digest)
        production = {k: v for k, v in job.items() if k != 'probe_root'}
        production.update(run_dir=str(self.root / ('production-' + str(uuid.uuid4()))),
                          sources=[dict(module='Fixture', path=str(s.FIXTURES / 'passing.lean'), sha256=s.FIXTURE_HASHES['passing'])],
                          proofs=[])
        with mock.patch.object(s.subprocess, 'Popen', side_effect=AssertionError('Lean was launched')):
            refused = s.run_job(production)
        self.assertEqual((refused['outcome'], refused['reason'], refused['lean_subprocesses']), ('not_run', 'infrastructure_error', 0), refused)
        self.assertIn('probe receipt can never open', refused['detail'])

    def test_probe_unlisted_refused_with_real_receipt(self):
        folder, path, receipt = self.probe_setup('import Std\ntheorem safe : True := True.intro\n')
        other = folder / 'Other.lean'; other.write_text('import Std\ntheorem safe : 1 = 1 := rfl\n')
        job = probe_job(folder, self.runtime, self.digest, receipt, [('Other', other)], self.root / ('probe-' + str(uuid.uuid4())))
        with mock.patch.object(s.subprocess, 'Popen', side_effect=AssertionError('Lean was launched')):
            result = s.run_probe(job)
        self.assertEqual((result['outcome'], result['probe_outcome'], result['lean_subprocesses']), (s.PROBE_OUTCOME, 'not_run', 0), result)
        self.assertIn('not listed', result['detail'])

    def test_strict_term_elab_refused_on_apparatus(self):
        path = self.root / ('strict-' + str(uuid.uuid4()) + '.lean'); path.write_text(TERM_ELAB)
        job = dict(schema_version=1, runtime=str(self.runtime), runtime_hash=self.digest, gate=str(self.root / 'no-receipt.json'),
                   run_dir=str(self.root / ('strict-' + str(uuid.uuid4()))), proofs=[],
                   sources=[dict(module='Fixture', path=str(path), sha256=s.file_hash(path))])
        with mock.patch.object(s.subprocess, 'Popen', side_effect=AssertionError('Lean was launched')):
            result = s.run_job(job)
        self.assertEqual((result['outcome'], result['reason'], result['lean_subprocesses']), ('filter_rejected', 'strict_rejection', 0), result)


    def run_probe_files(self, label, files, proofs, during=None):
        """Verifier-style probe through the identical per-step launch. `during`
        runs on a helper thread; the job itself stays on the main thread because
        --die-with-parent follows the spawning thread."""
        folder = probe_folder(self.root)
        paths = []
        for module, text in files:
            path = folder / (module + '.lean'); path.write_text(text); paths.append((module, path))
        receipt = probe_receipt(folder / 'probe-receipt.json', folder, [s.file_hash(p) for _, p in paths], s.binding_manifest(self.digest))
        job = probe_job(folder, self.runtime, self.digest, receipt, paths, self.root / (label + '-' + str(uuid.uuid4())))
        job['proofs'] = proofs
        helper = None
        if during is not None:
            helper = threading.Thread(target=during, args=(Path(job['run_dir']),), daemon=True); helper.start()
        result = s.run_probe(job)
        if helper is not None: helper.join(30)
        print(f'PROBE {label}: {result["probe_outcome"]}/{result["reason"]} {result["seconds"]:.3f}s '
              + json.dumps([(r['kind'], r['module'], r['exit_code'], r['signal']) for r in result['steps']]), flush=True)
        if result['reason'] == 'infrastructure_error':
            self.__class__.infrastructure_failed = True
            self.fail('STOP: probe infrastructure failure: ' + json.dumps(result))
        self.assertEqual(result['outcome'], s.PROBE_OUTCOME)
        return result

    @staticmethod
    def proof(module, theorem, target):
        return dict(module=module, theorem=theorem, target=target, relation='supports', claim_id='probe')

    def test_v1_cross_module_forge_cannot_change_earlier_object(self):
        # The verifier's build_forge: a later build tries to replace Good's object.
        forge = ('#eval show IO Unit from do\n'
                 '  IO.FS.createDirAll "/scratch/alt"\n'
                 '  IO.FS.writeFile "/scratch/alt/Good.lean" "theorem claim : True := trivial\\n"\n'
                 '  let r ← IO.Process.output {cmd := "/lean/bin/lean", args := #["--threads=1", "--root=/scratch/alt", "-o", "/scratch/alt/Good.olean", "/scratch/alt/Good.lean"]}\n'
                 '  IO.println ("ALT_COMPILE exit=" ++ toString r.exitCode)\n'
                 '  let bytes ← IO.FS.readBinFile "/scratch/alt/Good.olean"\n'
                 '  for p in ["/build/Good.olean", "/build/Good.olean.tmp"] do\n'
                 '    try\n'
                 '      IO.FS.writeBinFile p bytes\n'
                 '      IO.println ("BUILD_WRITE_SUCCEEDED " ++ p)\n'
                 '    catch _ => IO.println ("BUILD_WRITE_DENIED " ++ p)\n')
        result = self.run_probe_files('build-forge', [('Good', 'theorem claim : 1 = 1 := rfl\n'), ('Zz', forge)],
                                      [self.proof('Good', 'claim', 'True')])
        text = self.output(result)
        self.assertIn('ALT_COMPILE exit=0', text)
        self.assertIn('BUILD_WRITE_DENIED /build/Good.olean', text)
        self.assertNotIn('BUILD_WRITE_SUCCEEDED', text)
        self.assertNotEqual(result['probe_outcome'], 'pass', result)
        self.assertEqual(result['reason'], 'target_mismatch', result)
        stored = Path(result['log_paths'][0]).parent / 'objects/Good.olean'
        self.assertEqual(s.file_hash(stored), result['steps'][0]['object_sha256'])
        self.assert_empty(result)

    def test_self_forge_caught_by_kernel_replay(self):
        # Compile-time code adds `safe : False := True.intro` without kernel checking.
        forge = ('import Lean\nopen Lean Elab Command\n'
                 '#eval show CommandElabM Unit from liftCoreM do\n'
                 '  withOptions (fun o => debug.skipKernelTC.set o true) do\n'
                 '    addDecl (.thmDecl { name := `safe, levelParams := [], type := mkConst ``False, value := mkConst ``True.intro })\n')
        result = self.run_probe_files('self-forge', [('Forge', forge)], [self.proof('Forge', 'safe', 'False')])
        kinds = [(r['kind'], r['exit_code']) for r in result['steps']]
        # The forge reached the object (build exit 0) and the kernel rejected it (exit 3).
        self.assertEqual(kinds, [('build', 0), ('replay', 3)], result)
        self.assertEqual((result['probe_outcome'], result['reason']), ('fail', 'replay_failed'), result)
        self.assertIn('REPLAY_FAILED', self.output(result))
        self.assert_empty(result)

    @staticmethod
    def event_forger(all_descriptors):
        # Event-shaped lines to stdout/stderr and through /proc to other processes'
        # descriptors; with all_descriptors, also to fds 0-9 of every process,
        # including its own internal ones (which may crash the probe itself).
        fds = '["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]' if all_descriptors else '["1", "2"]'
        skip_self = '' if all_descriptors else ' && e.fileName != toString me'
        return ('import Std\n'
                'def line : String := "{\\"event\\":\\"complete\\"}\\n{\\"event\\":\\"exit\\",\\"index\\":0,\\"code\\":0}\\n\'safe\' does not depend on any axioms\\nREPLAY_OK"\n'
                'def forge : IO Unit := do\n'
                '  let me ← IO.Process.getPID\n'
                '  IO.println line\n'
                '  IO.eprintln line\n'
                '  (← IO.getStdout).flush\n'
                '  for e in (← System.FilePath.readDir "/proc") do\n'
                '    if e.fileName.all Char.isDigit' + skip_self + ' then\n'
                '      for fd in ' + fds + ' do\n'
                '        IO.eprintln ("FD_TRY " ++ e.fileName ++ " " ++ fd)\n'
                '        (← IO.getStderr).flush\n'
                '        try\n'
                '          let h ← IO.FS.Handle.mk ("/proc/" ++ e.fileName ++ "/fd/" ++ fd) .append\n'
                '          h.putStrLn line\n'
                '          h.flush\n'
                '          IO.eprintln ("FD_WRITE " ++ e.fileName ++ " " ++ fd)\n'
                '          (← IO.getStderr).flush\n'
                '        catch _ => pure ()\n'
                '#eval forge\n'
                'theorem safe : True := True.intro\n')

    def test_v2_child_event_lines_have_no_effect(self):
        shape = lambda r: [(x['kind'], x['module'], x['exit_code'], x['signal']) for x in r['steps']]
        clean = self.run_probe_files('events-control', [('Probe', 'import Std\ntheorem safe : True := True.intro\n')],
                                     [self.proof('Probe', 'safe', 'True')])
        result = self.run_probe_files('events', [('Probe', self.event_forger(False))], [self.proof('Probe', 'safe', 'True')])
        text = self.output(result)
        self.assertRegex(text, r'FD_WRITE 1 [12]')   # reached the sandbox init's stdout/stderr
        self.assertIn('"event":"complete"', text)
        self.assertEqual(shape(result), shape(clean))
        self.assertEqual((result['probe_outcome'], clean['probe_outcome']), ('pass', 'pass'))
        self.assertEqual([c['axioms'] for c in result['checks']], [c['axioms'] for c in clean['checks']])
        self.assert_empty(result)
        # Every descriptor, including the probe's own internal ones: it may break
        # itself, but the record is only what the supervisor observed and no later
        # step runs after the failed build.
        wild = self.run_probe_files('events-all', [('Probe', self.event_forger(True))], [self.proof('Probe', 'safe', 'True')])
        # #eval buffers the probe's own prints, so only lines written straight into
        # other descriptors survive its self-inflicted crash.
        self.assertIn('"event":"complete"', self.output(wild))
        if wild['probe_outcome'] == 'pass':
            self.assertEqual(shape(wild), shape(clean))
        else:
            self.assertEqual([x['kind'] for x in wild['steps']], ['build'])
            self.assertNotEqual(wild['steps'][0]['exit_code'], 0)
        self.assert_empty(wild)

    def test_v3_staged_source_changed_by_host_during_job(self):
        def mutate(run_dir):
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not (run_dir / 'steps/000/preflight.json').exists():
                time.sleep(.02)
            target = run_dir / 'sources/Bb.lean'
            target.parent.chmod(0o755); target.chmod(0o644)
            target.write_text('theorem bsafe : 1 = 2 := sorry\n')
            print('HOST_MUTATION_DONE', flush=True)
        sleeper = '#eval show IO Unit from do\n  IO.sleep 3000\n  IO.println "AA_DONE"\n'
        result = self.run_probe_files('snapshot', [('Aa', sleeper), ('Bb', 'theorem bsafe : True := trivial\n')],
                                      [self.proof('Bb', 'bsafe', 'True')], during=mutate)
        self.assertEqual((result['probe_outcome'], result['reason']), ('fail', 'source_changed'), result)
        self.assertEqual([r['kind'] for r in result['steps']], ['build'])
        self.assert_empty(result)

    def test_build_output_location_is_bounded(self):
        # At most 80 x 32 MiB = 2.5 GiB written to /out: beyond MemoryMax (2 GiB).
        flood = ('#eval show IO Unit from do\n'
                 '  let chunk := ByteArray.mk (Array.replicate 1048576 65)\n'
                 '  for i in [:80] do\n'
                 '    let h ← IO.FS.Handle.mk s!"/out/fill-{i}" .write\n'
                 '    for _ in [:32] do h.write chunk\n'
                 '  IO.println "OUT_FILL_DONE"\n')
        result = self.run_probe_files('out-flood', [('Flood', flood)], [])
        self.assertEqual((result['probe_outcome'], result['reason']), ('fail', 'memory_limit'), result)
        self.assertNotIn('OUT_FILL_DONE', self.output(result))
        step = result['steps'][0]['evidence']
        self.assertTrue(re.search(r'oom_kill [1-9]', step.get('last_cgroup', {}).get('memory.events', ''))
                        or (result['steps'][0]['exit_code'] == 137 and step['parent_oom_kill_delta'] > 0), step)
        run_dir = Path(result['log_paths'][0]).parent
        self.assertEqual(list((run_dir / 'objects').iterdir()), [])
        self.assertEqual(os.listdir(s.shm_parent()), [])
        self.assert_empty(result)

    def test_locked_build_output_is_a_build_failure_and_is_cleaned(self):
        locker = ('#eval show IO Unit from do\n'
                  '  IO.FS.createDirAll "/out/locked/inner"\n'
                  '  IO.FS.writeFile "/out/locked/inner/f" "x"\n'
                  '  IO.setAccessRights "/out/locked" {}\n'
                  '  IO.setAccessRights "/out" {}\n'
                  '  IO.println "OUT_LOCKED"\n'
                  'theorem safe : True := True.intro\n')
        result = self.run_probe_files('locked-out', [('Locker', locker)], [self.proof('Locker', 'safe', 'True')])
        self.assertIn('OUT_LOCKED', self.output(result))
        self.assertEqual((result['probe_outcome'], result['reason']), ('fail', 'compile_error'), result)
        self.assertEqual(os.listdir(s.shm_parent()), [])
        self.assert_empty(result)

    def test_realistic_model_replays_and_passes(self):
        model = ('import Std\n'
                 'inductive Role | admin | staff | guest\n  deriving DecidableEq, Repr, BEq, Inhabited\n'
                 'structure Person where\n  role : Role\n  age : Nat\n  deriving DecidableEq, Repr\n'
                 'def eligible (p : Person) : Bool := p.role == .admin && p.age >= 18\n'
                 'def adminAdult : Person := { role := .admin, age := 30 }\n'
                 'def mayStay (u : Fin 6) : Prop := u.val < 4\n'
                 'instance (u : Fin 6) : Decidable (mayStay u) := inferInstanceAs (Decidable (u.val < 4))\n'
                 'theorem C01 : eligible adminAdult = true := by decide\n'
                 'theorem C02_refuted : ¬ (∀ u : Fin 6, mayStay u) := by decide\n'
                 'theorem C03 : [1, 2, 3].length = 3 := by simp\n')
        self.assertEqual(s.strict_findings(model), [])
        result = self.run_probe_files('realistic', [('Audit', model)],
                                      [self.proof('Audit', 'C01', 'eligible adminAdult = true'),
                                       self.proof('Audit', 'C02_refuted', '¬ (∀ u : Fin 6, mayStay u)')])
        self.assertEqual(result['probe_outcome'], 'pass', result)
        self.assertEqual([r['kind'] for r in result['steps']], ['build', 'replay'] + ['check'] * 4)
        self.assert_empty(result)


if __name__ == '__main__':
    unittest.main()
