/** Checks of tools/verify.mjs. It runs against a fixture repository root holding copies of verify.mjs and the toolchain
 * resolver, with fake suites, so no real suite, Lean or Python 3.12 is needed and nothing outside tmp/ is touched. The
 * folders are kept under tmp/verify-tests/ for inspection. */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseSummary, skipReasons, SUITES} from './verify.mjs';

const execFileAsync = promisify(execFile);
const tools = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(tools, '..');
await fs.mkdir(path.join(repoRoot, 'tmp/verify-tests'), {recursive: true});
const suite = await fs.mkdtemp(path.join(repoRoot, 'tmp/verify-tests/run-'));
console.log(`Retained verify test folders: ${suite}`);

const nodeSummary = ({tests, pass, fail = 0, skipped = 0}) => `ℹ tests ${tests}\nℹ suites 0\nℹ pass ${pass}\nℹ fail ${fail}\nℹ cancelled 0\nℹ skipped ${skipped}\nℹ todo 0\nℹ duration_ms 12.5\n`;

// One fake node suite; its first argument selects the behaviour.
const FAKE = `
const mode = process.argv[2];
const summary = (tests, pass, fail, skipped) => 'ℹ tests ' + tests + '\\nℹ suites 0\\nℹ pass ' + pass + '\\nℹ fail ' + fail + '\\nℹ cancelled 0\\nℹ skipped ' + skipped + '\\nℹ todo 0\\n';
if (mode === 'pass') { console.log('✔ one (1ms)\\n﹣ two (0.1ms) # Real PDF converters unavailable\\n' + summary(3, 2, 0, 1)); }
else if (mode === 'fail') { console.log('✖ broken (1ms)\\n' + summary(3, 2, 1, 0)); process.exitCode = 1; }
else if (mode === 'crash') { console.error('boom'); process.exitCode = 3; }
else if (mode === 'nosummary') { console.log('all good, trust me'); }
else if (mode === 'zero') { console.log(summary(0, 0, 0, 0)); }
else if (mode === 'failzero') { console.log(summary(2, 1, 1, 0)); }
else if (mode === 'allskipped') { console.log('﹣ a (0.1ms) # Real PDF converters unavailable\\n﹣ b (0.1ms) # Real PDF converters unavailable\\n' + summary(2, 0, 0, 2)); }
else if (mode === 'pyskip') { console.log('﹣ proof (0.1ms) # tools/document_audit.py needs Python 3.12 or later: Python 3.12 or later is required (tools/document_audit.py).\\n' + summary(2, 1, 0, 1)); }
else if (mode === 'unaccounted') { console.log(summary(2, 1, 0, 1)); }
else if (mode === 'leanskip') { console.log('﹣ proof (0.1ms) # Lean is not installed; run "node tools/setup-toolchain.mjs"\\n' + summary(2, 1, 0, 1)); }
`;
const PYTHON_OUTPUT = (version, body) => `#!/bin/sh\nif [ "$1" = "-I" ]; then echo ${version}; exit 0; fi\n${body}\n`;
const UNITTEST_OK = `cat >&2 <<'EOF'
test_a (t.A) ... ok
test_b (t.A) ... skipped 'Repository-local Lean unavailable'
test_c (t.A) ... skipped 'Real Poppler unavailable; recorded explicitly'

----------------------------------------------------------------------
Ran 5 tests in 0.010s

OK (skipped=2)
EOF`;

async function fixture(name, suites, {python = '3.12.1', pythonBody = UNITTEST_OK, lean = false} = {}) {
  const dir = path.join(suite, name);
  const root = path.join(dir, 'repo');
  await fs.mkdir(path.join(root, 'tools'), {recursive: true});
  for (const file of ['verify.mjs', 'toolchain.mjs', 'toolchain.json']) await fs.copyFile(path.join(tools, file), path.join(root, 'tools', file));
  await fs.writeFile(path.join(root, 'fake-suite.mjs'), FAKE);
  const pythonFile = path.join(dir, 'fake-python3');
  await fs.writeFile(pythonFile, PYTHON_OUTPUT(python, pythonBody));
  await fs.chmod(pythonFile, 0o755);
  await fs.writeFile(path.join(dir, 'suites.json'), JSON.stringify(suites));
  if (lean) {
    const manifest = JSON.parse(await fs.readFile(path.join(tools, 'toolchain.json'), 'utf8'));
    for (const entry of Object.values(manifest.lean.platforms)) {
      const file = path.join(root, 'tmp/toolchains', entry.folder, 'bin/lean');
      await fs.mkdir(path.dirname(file), {recursive: true});
      await fs.writeFile(file, '#!/bin/sh\n');
      await fs.chmod(file, 0o755);
    }
  }
  const api = {
    dir, root, pythonFile,
    async run(args = [], {env = {}} = {}) {
      const argv = [path.join(root, 'tools/verify.mjs'), '--suites', path.join(dir, 'suites.json'), '--log-dir', path.join(dir, 'logs'), ...args];
      try {
        const {stdout, stderr} = await execFileAsync(process.execPath, argv, {cwd: root, env: {...process.env, AUDIT_PYTHON: pythonFile, ...env}, timeout: 60000});
        return {code: 0, stdout, stderr};
      } catch (error) {
        if (typeof error.code !== 'number') throw error;
        return {code: error.code, stdout: error.stdout, stderr: error.stderr};
      }
    }
  };
  return api;
}

const node = (name, mode) => ({name, command: ['node', 'fake-suite.mjs', mode]});
const python = name => ({name, command: ['{python}', '-m', 'unittest', 'discover'], python: true});

test('parseSummary reads node:test spec and TAP output', () => {
  assert.deepEqual(parseSummary(nodeSummary({tests: 37, pass: 37})), {kind: 'node:test', total: 37, pass: 37, fail: 0, skip: 0});
  assert.deepEqual(parseSummary(nodeSummary({tests: 6, pass: 5, skipped: 1})), {kind: 'node:test', total: 6, pass: 5, fail: 0, skip: 1});
  assert.equal(parseSummary(nodeSummary({tests: 6, pass: 4, fail: 1, skipped: 1})).fail, 1);
  assert.deepEqual(parseSummary('# tests 4\n# suites 0\n# pass 3\n# fail 1\n# cancelled 0\n# skipped 0\n# todo 0\n'), {kind: 'node:test', total: 4, pass: 3, fail: 1, skip: 0});
  assert.equal(parseSummary('cancelled tests are not counted when no summary is printed'), null);
});

test('parseSummary reads unittest output, including skips, errors and failures', () => {
  assert.deepEqual(parseSummary('\nRan 266 tests in 30.1s\n\nOK\n'), {kind: 'unittest', total: 266, pass: 266, fail: 0, skip: 0});
  assert.deepEqual(parseSummary('Ran 301 tests in 29.4s\n\nOK (skipped=41)\n'), {kind: 'unittest', total: 301, pass: 260, fail: 0, skip: 41});
  assert.deepEqual(parseSummary('Ran 294 tests in 30.0s\n\nFAILED (errors=1, skipped=33)\n'), {kind: 'unittest', total: 294, pass: 260, fail: 1, skip: 33});
  assert.deepEqual(parseSummary('Ran 10 tests in 1s\n\nFAILED (failures=2, errors=1)\n'), {kind: 'unittest', total: 10, pass: 7, fail: 3, skip: 0});
  assert.equal(parseSummary('Ran 3 tests in 1s\n'), null, 'a run without a verdict line is not a summary');
  // Two-word keys: expected failures are neither failures nor passes; unexpected successes fail the run.
  assert.deepEqual(parseSummary('Ran 10 tests in 1s\n\nOK (expected failures=1)\n'), {kind: 'unittest', total: 10, pass: 9, fail: 0, skip: 0});
  assert.deepEqual(parseSummary('Ran 10 tests in 1s\n\nOK (skipped=2, expected failures=3)\n'), {kind: 'unittest', total: 10, pass: 5, fail: 0, skip: 2});
  assert.deepEqual(parseSummary('Ran 10 tests in 1s\n\nFAILED (failures=1, unexpected successes=2)\n'), {kind: 'unittest', total: 10, pass: 7, fail: 3, skip: 0});
});

test('skipReasons collects reasons from both output styles', () => {
  const reasons = skipReasons("﹣ a (0.1ms) # Lean missing\n﹣ b (0.1ms) # Lean missing\ntest_x (t.T) ... skipped 'No Poppler'\ntest_y (t.T) ... ok\n");
  assert.deepEqual(reasons, [{reason: 'Lean missing', count: 2}, {reason: 'No Poppler', count: 1}]);
});

test('the real suite list names scripts that exist and puts the Python suite behind the resolver', async () => {
  const names = SUITES.map(s => s.name);
  assert.equal(new Set(names).size, names.length);
  for (const s of SUITES) {
    const script = s.command.find(part => part.endsWith('.mjs'));
    if (script) await fs.access(path.join(repoRoot, script));
  }
  const py = SUITES.filter(s => s.python);
  assert.deepEqual(py.map(s => s.name), ['python-tests']);
  assert.equal(py[0].command[0], '{python}');
});

test('all suites pass: exit 0, counts and skip reasons per suite, nothing else claimed', async () => {
  const f = await fixture('pass', [node('alpha', 'pass'), node('beta', 'pass'), python('py')]);
  const result = await f.run();
  assert.equal(result.code, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /^Python: .*fake-python3 3\.12\.1$/m);
  assert.match(result.stdout, /^alpha: PASS pass 2, fail 0, skip 1/m);
  assert.match(result.stdout, /^py: PASS pass 3, fail 0, skip 2/m);
  assert.match(result.stdout, /^alpha\s+PASS\s+2\s+0\s+1$/m);
  assert.match(result.stdout, /^total\s+7\s+0\s+4$/m);
  assert.match(result.stdout, /alpha: 1 x Real PDF converters unavailable/);
  assert.match(result.stdout, /py: 1 x Repository-local Lean unavailable/);
  assert.match(result.stdout, /PASSED: 3 suite\(s\), 7 passed, 4 skipped, 0 failed\./);
  assert.ok((await fs.readFile(path.join(f.dir, 'logs/alpha.log'), 'utf8')).includes('ℹ tests 3'), 'the log is kept');
});

test('a failing suite gives exit 1, and the suites after it still run', async () => {
  const f = await fixture('fail', [node('alpha', 'fail'), node('beta', 'pass')]);
  const result = await f.run();
  assert.equal(result.code, 1, result.stdout);
  assert.match(result.stdout, /^alpha: FAIL pass 2, fail 1, skip 0 \(exit code 1, 1 failed\)/m);
  assert.match(result.stdout, /^beta: PASS/m);
  assert.match(result.stdout, /FAILED: 1 suite\(s\) failed/);
  assert.doesNotMatch(result.stdout, /PASSED:/);
});

test('an exit code of 0 without a summary, a crash and zero tests are never a pass', async () => {
  for (const [mode, note] of [['nosummary', /no test summary/], ['crash', /exit code 3/], ['zero', /no test ran/], ['failzero', /failures reported/], ['allskipped', /no test passed \(every test was skipped\)/]]) {
    const f = await fixture('bad-' + mode, [node('only', mode)]);
    const result = await f.run();
    assert.equal(result.code, 1, `${mode}: ${result.stdout}`);
    assert.match(result.stdout, /^only: FAIL/m, mode);
    assert.match(result.stdout, note, mode);
    assert.doesNotMatch(result.stdout, /PASSED:/, mode);
  }
});

test('without Python 3.12 or later it says so, runs the other suites, marks the Python suite not run and exits 1', async () => {
  for (const env of [{AUDIT_PYTHON: '/nonexistent/python3'}, null]) {
    const f = await fixture('no-python' + (env ? '-env' : '-old'), [node('alpha', 'pass'), python('py')], {python: '3.9.6'});
    const result = await f.run([], {env: env ?? {}});
    assert.equal(result.code, 1, result.stdout);
    assert.match(result.stdout, /^Python: not available\. .*Python 3\.12 or later is required \(tools\/document_audit\.py\)\. Found: /m);
    assert.match(result.stdout, /Run "node tools\/setup-toolchain\.mjs"/);
    assert.match(result.stdout, /^alpha: PASS/m, 'the suites that need no Python still run');
    assert.match(result.stdout, /^py: NOT RUN/m);
    assert.match(result.stdout, /^py\s+NOT RUN\s+-\s+-\s+-/m);
    assert.doesNotMatch(result.stdout, /^py: PASS/m);
    assert.doesNotMatch(result.stdout, /PASSED:/);
    assert.match(result.stdout, /FAILED: 1 suite\(s\) did not run because Python 3\.12 or later is missing/);
    await assert.rejects(fs.access(path.join(f.dir, 'logs/py.log')), 'no log, because nothing ran');
  }
});

test('--require-lean fails when Lean is missing or a Lean test was skipped, and passes otherwise', async () => {
  const missing = await fixture('lean-missing', [node('alpha', 'pass')]);
  assert.equal((await missing.run()).code, 0, 'without the flag a missing Lean is only reported');
  const refused = await missing.run(['--require-lean']);
  assert.equal(refused.code, 1, refused.stdout);
  assert.match(refused.stdout, /--require-lean: Lean is not installed at .*run "node tools\/setup-toolchain\.mjs"/);

  const skipped = await fixture('lean-skipped', [node('alpha', 'leanskip')], {lean: true});
  assert.equal((await skipped.run()).code, 0, 'a Lean skip is allowed without the flag');
  const strict = await skipped.run(['--require-lean']);
  assert.equal(strict.code, 1, strict.stdout);
  assert.match(strict.stdout, /--require-lean: Lean tests were skipped:\n {2}alpha: 1 x Lean is not installed/);

  // Lean installed, but the Lean test was skipped for a Python reason: the reason never says "Lean".
  const pythonSkip = await fixture('lean-python-skip', [node('alpha', 'pyskip')], {lean: true});
  assert.equal((await pythonSkip.run()).code, 0, 'allowed without the flag');
  const masked = await pythonSkip.run(['--require-lean']);
  assert.equal(masked.code, 1, masked.stdout);
  assert.match(masked.stdout, /--require-lean: Lean tests were skipped:\n {2}alpha: 1 x tools\/document_audit\.py needs Python 3\.12/);

  // A skip with no parsed reason could be a Lean test, so --require-lean cannot rule it out.
  const unexplained = await fixture('lean-unexplained-skip', [node('alpha', 'unaccounted')], {lean: true});
  assert.equal((await unexplained.run()).code, 0, 'allowed without the flag');
  const unknownSkip = await unexplained.run(['--require-lean']);
  assert.equal(unknownSkip.code, 1, unknownSkip.stdout);
  assert.match(unknownSkip.stdout, /skipped tests without a parsed reason[^]*alpha: 1 skipped, 0 with a reason/);

  const ok = await fixture('lean-ok', [node('alpha', 'pass')], {lean: true});
  const passed = await ok.run(['--require-lean']);
  assert.equal(passed.code, 0, passed.stdout);
  assert.match(passed.stdout, /PASSED:/);
});

test('--only, --list and usage errors', async () => {
  const f = await fixture('options', [node('alpha', 'pass'), node('beta', 'fail')]);
  const only = await f.run(['--only', 'alpha']);
  assert.equal(only.code, 0, only.stdout);
  assert.doesNotMatch(only.stdout, /beta/);
  const list = await f.run(['--list']);
  assert.equal(list.code, 0);
  assert.match(list.stdout, /^alpha: node fake-suite\.mjs pass$/m);
  assert.doesNotMatch(list.stdout, /Python:/, '--list runs nothing');
  for (const args of [['--bogus'], ['--only', 'nope'], ['--only'], ['stray'], ['--only', ','], ['--only', ',,'], ['--list', '--only', ',']]) {
    const result = await f.run(args);
    assert.equal(result.code, 2, args.join(' ') + result.stdout + result.stderr);
    assert.doesNotMatch(result.stdout, /PASSED/, 'an empty selection never passes');
  }
  for (const [label, content] of [['an empty list', []], ['not a list', {name: 'x'}]]) {
    const empty = await fixture('empty-suites-' + label.replace(/\W+/g, '-'), content);
    for (const args of [[], ['--list']]) {
      const result = await empty.run(args);
      assert.equal(result.code, 2, `${label} ${args.join(' ')}: ${result.stdout}${result.stderr}`);
      assert.match(result.stderr, /No suite is selected/);
      assert.doesNotMatch(result.stdout, /PASSED/);
    }
  }
});
