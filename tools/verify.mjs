#!/usr/bin/env node
/** Runs every model-free check of this repository with the resolved Python and prints pass, fail and skip counts per
 * suite. No model, coding agent or network request is involved.
 *
 *   node tools/verify.mjs [--require-lean] [--only <suite>[,<suite>]] [--list]
 *
 * Exit codes: 0 every suite ran and passed; 1 a suite failed, did not run, printed no readable summary, or
 * --require-lean found Lean missing or a Lean test skipped; 2 usage error.
 * Without Python 3.12 or later the script says so, runs the suites that need none, marks the others "not run" and
 * exits 1. It never reports a pass for a suite it did not run.
 * Test hooks: --suites <file.json> replaces the suite list ([{name, command, python}]; "{python}" in a command is the
 * resolved interpreter) and --log-dir <dir> chooses where the logs go. */
import fs from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {ROOT, resolvePython, leanStatus} from './toolchain.mjs';

const USAGE = 'Usage: node tools/verify.mjs [--require-lean] [--only <suite>[,<suite>]] [--list]';
const SUITE_TIMEOUT_MS = 30 * 60 * 1000;

// Every check that needs no model, no browser and no running server. The Python suite needs the resolved interpreter.
export const SUITES = [
  {name: 'toolchain', command: ['node', 'tools/test-toolchain.mjs']},
  {name: 'verify', command: ['node', 'tools/test-verify.mjs']},
  {name: 'remote-verify', command: ['node', 'tools/test-remote-verify.mjs']},
  {name: 'agent-home', command: ['node', '--test', 'tools/test-agent-home.mjs']},
  {name: 'ala', command: ['node', '--test', 'tools/test-ala.mjs']},
  {name: 'markdown-server', command: ['node', 'tools/test-markdown-server.mjs']},
  {name: 'report-generation', command: ['node', 'tools/test-report-generation.mjs']},
  {name: 'python-tests', command: ['{python}', '-m', 'unittest', 'discover', '-s', 'tests', '-v'], python: true}
];

/** Counts from the end of a node:test run (spec or TAP lines) or a unittest run. Returns null without a summary. */
export function parseSummary(text) {
  const node = {};
  for (const m of text.matchAll(/^(?:ℹ|#) (tests|pass|fail|cancelled|skipped|todo) (\d+)\s*$/gm)) node[m[1]] = Number(m[2]);
  if (node.tests !== undefined && node.pass !== undefined) {
    return {kind: 'node:test', total: node.tests, pass: node.pass, fail: (node.fail ?? 0) + (node.cancelled ?? 0), skip: (node.skipped ?? 0) + (node.todo ?? 0)};
  }
  const ran = [...text.matchAll(/^Ran (\d+) tests? in /gm)].pop();
  if (ran) {
    const tail = text.slice(ran.index);
    const verdict = /^(OK|FAILED)(?: \(([^)]*)\))?\s*$/m.exec(tail);
    if (!verdict) return null;
    // Keys can have two words: "expected failures", "unexpected successes".
    const detail = Object.fromEntries([...(verdict[2] ?? '').matchAll(/(\w+(?: \w+)*)=(\d+)/g)].map(m => [m[1].replace(/ /g, '_'), Number(m[2])]));
    const fail = (detail.failures ?? 0) + (detail.errors ?? 0) + (detail.unexpected_successes ?? 0);
    const skip = detail.skipped ?? 0;
    return {kind: 'unittest', total: Number(ran[1]), pass: Number(ran[1]) - fail - skip - (detail.expected_failures ?? 0), fail: verdict[1] === 'OK' ? fail : Math.max(fail, 1), skip};
  }
  return null;
}

/** Skip reasons with counts, from node:test ("# reason" after a ﹣ line) and unittest -v ("skipped 'reason'") output. */
export function skipReasons(text) {
  const counts = new Map();
  const add = reason => counts.set(reason.trim(), (counts.get(reason.trim()) ?? 0) + 1);
  for (const m of text.matchAll(/^\s*﹣ .*? # (.+)$/gm)) add(m[1]);
  for (const m of text.matchAll(/ \.\.\. skipped '(.*)'$/gm)) add(m[1]);
  return [...counts].map(([reason, count]) => ({reason, count}));
}

export function parseArguments(argv) {
  const args = {requireLean: false, only: null, list: false, suites: null, logDir: null};
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--require-lean') args.requireLean = true;
    else if (arg === '--list') args.list = true;
    else if (['--only', '--suites', '--log-dir'].includes(arg)) {
      const value = argv[++i];
      if (!value) throw Object.assign(new Error(`${arg} needs a value.\n${USAGE}`), {code: 2});
      if (arg === '--only') args.only = value.split(',').filter(Boolean);
      else if (arg === '--suites') args.suites = value;
      else args.logDir = value;
    } else throw Object.assign(new Error(`Unknown argument: ${arg}\n${USAGE}`), {code: 2});
  }
  return args;
}

function runSuite(suite, python, logFile) {
  return new Promise(resolve => {
    const [file, ...rest] = suite.command.map(part => (part === '{python}' ? python.path : part));
    const started = Date.now();
    const out = fs.createWriteStream(logFile);
    let text = '';
    let child;
    try {
      child = spawn(file === 'node' ? process.execPath : file, rest, {cwd: ROOT, env: {...process.env, ...(python ? {AUDIT_PYTHON: python.path} : {})}, stdio: ['ignore', 'pipe', 'pipe']});
    } catch (error) {
      out.end(String(error));
      return resolve({exit: null, text: String(error), seconds: 0, error});
    }
    const collect = chunk => { text += chunk; out.write(chunk); };
    child.stdout.on('data', collect);
    child.stderr.on('data', collect);
    const timer = setTimeout(() => child.kill('SIGKILL'), SUITE_TIMEOUT_MS);
    const finish = (exit, signal, error) => {
      clearTimeout(timer);
      out.end();
      resolve({exit, signal, text, seconds: (Date.now() - started) / 1000, error});
    };
    child.on('error', error => finish(null, null, error));
    child.on('close', (exit, signal) => finish(exit, signal, null));
  });
}

export async function main(argv) {
  const args = parseArguments(argv);
  let suites = SUITES;
  if (args.suites) suites = JSON.parse(fs.readFileSync(path.resolve(args.suites), 'utf8'));
  if (args.only && !args.only.length) throw Object.assign(new Error(`--only names no suite.\n${USAGE}`), {code: 2});
  if (!Array.isArray(suites) || !suites.length) throw Object.assign(new Error(`No suite is selected (the suite list is empty).\n${USAGE}`), {code: 2});
  if (args.list) {
    for (const suite of suites) console.log(`${suite.name}${suite.python ? ' (needs Python 3.12 or later)' : ''}: ${suite.command.join(' ')}`);
    return 0;
  }
  if (args.only) {
    const unknown = args.only.filter(name => !suites.some(s => s.name === name));
    if (unknown.length) throw Object.assign(new Error(`Unknown suite: ${unknown.join(', ')}. Known: ${suites.map(s => s.name).join(', ')}`), {code: 2});
    suites = suites.filter(s => args.only.includes(s.name));
  }

  let python = null;
  let pythonProblem = null;
  try {
    python = resolvePython();
    console.log(`Python: ${python.path} ${python.version}`);
    if (python.warning) console.log(`Python warning: ${python.warning}`);
  } catch (error) {
    pythonProblem = error.message;
    console.log(`Python: not available. ${pythonProblem}`);
    if (suites.some(s => s.python)) console.log('The suites that need it will not run; the others run.');
  }
  const lean = leanStatus(ROOT);
  console.log(`Lean: ${lean.installed ? lean.path : `not installed (${lean.path}); run "node tools/setup-toolchain.mjs"`}`);

  const logDir = path.resolve(args.logDir ?? path.join(ROOT, 'tmp/verify', new Date().toISOString().replace(/[:.]/g, '-')));
  fs.mkdirSync(logDir, {recursive: true});
  console.log(`Logs: ${logDir}\n`);

  const results = [];
  for (const suite of suites) {
    if (suite.python && !python) {
      results.push({suite, status: 'NOT RUN', note: 'needs Python 3.12 or later'});
      console.log(`${suite.name}: NOT RUN (needs Python 3.12 or later)`);
      continue;
    }
    console.log(`${suite.name}: running ${suite.command.join(' ')}`);
    const run = await runSuite(suite, python, path.join(logDir, `${suite.name}.log`));
    const summary = parseSummary(run.text);
    const skips = skipReasons(run.text);
    let status = 'PASS';
    let note = '';
    if (run.error) { status = 'FAIL'; note = `could not run: ${run.error.message}`; }
    else if (run.exit !== 0) { status = 'FAIL'; note = (run.signal ? `killed by ${run.signal}` : `exit code ${run.exit}`) + (summary?.fail > 0 ? `, ${summary.fail} failed` : ''); }
    else if (!summary) { status = 'FAIL'; note = 'exit 0 but no test summary in the output'; }
    else if (summary.total === 0 || summary.pass + summary.fail + summary.skip === 0) { status = 'FAIL'; note = 'no test ran'; }
    else if (summary.fail > 0) { status = 'FAIL'; note = 'failures reported although the exit code was 0'; }
    else if (summary.pass === 0) { status = 'FAIL'; note = 'no test passed (every test was skipped)'; }
    results.push({suite, status, note, summary, skips, seconds: run.seconds});
    console.log(`${suite.name}: ${status}${summary ? ` pass ${summary.pass}, fail ${summary.fail}, skip ${summary.skip}` : ''}${note ? ` (${note})` : ''} in ${run.seconds.toFixed(1)}s`);
  }

  console.log('\nSuite               Result    Pass  Fail  Skip');
  let failed = 0, notRun = 0, pass = 0, fail = 0, skip = 0;
  for (const r of results) {
    const s = r.summary;
    console.log(`${r.suite.name.padEnd(19)} ${r.status.padEnd(9)} ${s ? String(s.pass).padStart(4) : '   -'}  ${s ? String(s.fail).padStart(4) : '   -'}  ${s ? String(s.skip).padStart(4) : '   -'}${r.note ? `  ${r.note}` : ''}`);
    if (r.status === 'FAIL') failed++;
    if (r.status === 'NOT RUN') notRun++;
    if (s) { pass += s.pass; fail += s.fail; skip += s.skip; }
  }
  console.log(`${'total'.padEnd(19)} ${' '.padEnd(9)} ${String(pass).padStart(4)}  ${String(fail).padStart(4)}  ${String(skip).padStart(4)}`);

  const reasons = results.filter(r => r.skips?.length);
  if (reasons.length) {
    console.log('\nSkips by reason:');
    for (const r of reasons) for (const {reason, count} of r.skips) console.log(`  ${r.suite.name}: ${count} x ${reason.length > 260 ? `${reason.slice(0, 257)}...` : reason}`);
  }

  let leanProblem = null;
  if (args.requireLean) {
    // A skipped Lean test can be recognised by "Lean" in its reason, or by a missing Python 3.12 that skips it first.
    const leanSkips = results.flatMap(r => (r.skips ?? []).filter(k => /\blean\b|Python 3\.12 or later/i.test(k.reason)).map(k => `${r.suite.name}: ${k.count} x ${k.reason}`));
    // Every skip must be accounted for by a parsed reason, or a Lean test could hide among the unexplained ones.
    const unexplained = results.filter(r => r.summary && r.summary.skip !== (r.skips ?? []).reduce((n, k) => n + k.count, 0)).map(r => `${r.suite.name}: ${r.summary.skip} skipped, ${(r.skips ?? []).reduce((n, k) => n + k.count, 0)} with a reason`);
    if (!lean.installed) leanProblem = `--require-lean: Lean is not installed at ${lean.path}; run "node tools/setup-toolchain.mjs".`;
    else if (leanSkips.length) leanProblem = `--require-lean: Lean tests were skipped:\n  ${leanSkips.join('\n  ')}`;
    else if (unexplained.length) leanProblem = `--require-lean: skipped tests without a parsed reason, so none can be ruled out as a Lean test:\n  ${unexplained.join('\n  ')}`;
  }
  const problems = [];
  if (notRun) problems.push(`${notRun} suite(s) did not run${pythonProblem ? ` because Python 3.12 or later is missing: ${pythonProblem}` : ''}`);
  if (failed) problems.push(`${failed} suite(s) failed`);
  if (leanProblem) problems.push(leanProblem);
  if (problems.length) {
    console.log(`\nFAILED: ${problems.join('; ')}`);
    return 1;
  }
  console.log(`\nPASSED: ${results.length} suite(s), ${pass} passed, ${skip} skipped, 0 failed.`);
  return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main(process.argv.slice(2)).then(
    code => { process.exitCode = code; },
    error => {
      console.error(error.code === 2 ? error.message : error.stack || String(error));
      process.exitCode = error.code === 2 ? 2 : 70;
    }
  );
}
