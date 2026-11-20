/** Model-free checks of the Deep Proof process viewer: the read gate, the readers and the HTTP server. Every fixture is
 * synthetic and built below the operating system's temporary folder (resolved to its real path) or below the
 * repository's ignored tmp/ folder; nothing here starts a model or reaches the network. Run with
 * `node --test tools/test-process-viewer.mjs`. */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import * as gate from './pv-read.mjs';
import {buildFixture, removeFixture, makeReadOnly, makeRemovable, SENTINEL, WORK_PREFIX} from './process-viewer-fixture.mjs';

const tools = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(tools, '..');
const realTmp = fs.realpathSync(os.tmpdir());
const onMac = process.platform === 'darwin';

/** Builds a fixture below a fresh temporary folder and removes it when the test ends. */
function fixture(t, options) {
  const base = fs.mkdtempSync(path.join(realTmp, 'pv-test-'));
  t.after(() => removeFixture(base));
  const fx = buildFixture(base, options);
  fx.roots = gate.openRoots({deepProofData: fx.data, deepProofTmp: fx.tmp, dossiers: fx.dossiers});
  return fx;
}

// The server modules, in the order they are built. Each must exist: a missing module fails the static test.
const SERVER_MODULES = ['pv-read.mjs'];
const FS_FUNCTIONS = ['openSync', 'readSync', 'fstatSync', 'lstatSync', 'readdirSync', 'closeSync', 'realpathSync', 'readFileSync', 'constants'];
const FORBIDDEN_MODULES = ['node:child_process', 'child_process', 'node:sqlite', 'node:net', 'node:https', 'node:worker_threads', 'node:fs/promises', 'fs/promises', 'node:cluster', 'node:dgram', 'node:tls'];

test('the server modules import only the allowed file functions and no process, database or network module', () => {
  assert.ok(SERVER_MODULES.includes('pv-read.mjs'), 'the gate is always checked');
  const kills = [];
  for (const name of SERVER_MODULES) {
    const file = path.join(tools, name);
    assert.ok(fs.existsSync(file), `${name} must exist`);
    const source = fs.readFileSync(file, 'utf8');
    for (const m of source.matchAll(/^\s*import\s+(?:([\s\S]*?)\s+from\s+)?['"]([^'"]+)['"]/gm)) {
      const [, clause, specifier] = m;
      assert.ok(!FORBIDDEN_MODULES.includes(specifier), `${name} must not import ${specifier}`);
      assert.ok(specifier.startsWith('node:') || /^\.\/pv-[a-z]+\.mjs$/.test(specifier), `${name} imports ${specifier}, which is neither a built-in module nor a viewer module`);
      if (specifier === 'node:fs') {
        assert.ok(['pv-read.mjs', 'process-viewer.mjs'].includes(name), `${name} must not import node:fs`);
        const named = /^\{([^}]*)\}$/.exec((clause || '').trim());
        assert.ok(named, `${name} must import node:fs by named imports only`);
        const names = named[1].split(',').map(s => s.trim()).filter(Boolean);
        for (const n of names) assert.ok(FS_FUNCTIONS.includes(n), `${name} imports node:fs ${n}, which is not on the list`);
        if (names.includes('readFileSync')) assert.equal(name, 'process-viewer.mjs', 'readFileSync is for the server only');
      }
    }
    assert.doesNotMatch(source, /\bimport\s*\(/, `${name} must not import dynamically`);
    assert.doesNotMatch(source, /\brequire\s*\(/, `${name} must not use require`);
    assert.doesNotMatch(source, /\bfetch\s*\(/, `${name} must not call fetch`);
    assert.doesNotMatch(source, /\.\/ala\.mjs/, `${name} must not import the pilot's ALA module`);
    for (const k of source.matchAll(/process\.kill\(([^)]*)\)/g)) kills.push({name, args: k[1].replace(/\s+/g, '')});
  }
  // The pid of a trace can be written by an agent: the only kill call passes the literal 0 (liveness, no signal).
  if (SERVER_MODULES.includes('pv-runs.mjs')) {
    assert.deepEqual(kills, [{name: 'pv-runs.mjs', args: 'pid,0'}]);
  } else assert.deepEqual(kills, []);
});

test('the read gate refuses absolute paths, parent segments, NUL, backslashes, answer keys and evaluator material', t => {
  const fx = fixture(t);
  for (const rel of ['/etc/hosts', 'projects/../analyst.sqlite', 'projects//x', 'projects/./x', 'projects/a\0b', 'projects\\x', '', 'projects/keys-draft/x', 'projects/Keys/x', 'projects/evaluator/x']) {
    assert.equal(gate.read(fx.roots.data, rel).ok, false, `refused: ${JSON.stringify(rel)}`);
  }
  assert.deepEqual(gate.list(fx.roots.data, 'projects/..'), []);
  assert.equal(gate.relativeSegments('keys-sealed/x.json'), null);
  assert.equal(gate.relativeSegments('fixtures/book/sources/a.md').length, 4);
  // A dossier root inside answer keys stops the server.
  for (const keys of fx.keysFolders) {
    assert.throws(() => gate.openRoots({dossiers: [keys]}), /answer keys or evaluator/);
  }
});

test('analyst.sqlite and everything outside projects/ is never opened by the gate', t => {
  const fx = fixture(t);
  for (const rel of ['analyst.sqlite', 'analyst.sqlite-wal', 'analyst.sqlite-shm', 'jobs/stale/note.txt', 'project.md']) {
    const answer = gate.read(fx.roots.data, rel);
    assert.equal(answer.ok, false, rel);
    assert.equal(answer.reason, 'refused');
    assert.equal(gate.stat(fx.roots.data, rel).ok, false);
  }
  assert.deepEqual(gate.list(fx.roots.data, 'jobs'), []);
  assert.deepEqual(gate.list(fx.roots.data, '.'), []);
  const top = gate.tryList(fx.roots.data, 'projects');
  assert.ok(top.ok && top.entries.some(e => e.name === 'proj-alpha'));
});

test('a link inside the data folder is not followed, as a file and as a folder', t => {
  const fx = fixture(t);
  const analyses = path.join(fx.data, 'projects/proj-alpha/analyses/run-analysis-done');
  // A file link to a system file, a folder link beside the traces and a link in place of the whole traces folder.
  fs.rmSync(path.join(analyses, 'report.md'));
  fs.symlinkSync('/etc/hosts', path.join(analyses, 'report.md'));
  const outside = fs.mkdtempSync(path.join(realTmp, 'pv-outside-'));
  t.after(() => fs.rmSync(outside, {recursive: true, force: true}));
  fs.writeFileSync(path.join(outside, 'trace.jsonl'), SENTINEL + '\n');
  fs.symlinkSync(outside, path.join(analyses, 'linked-dir'));
  const traces = path.join(fx.data, 'projects/proj-alpha/traces');
  fs.renameSync(traces, traces + '-moved');
  fs.symlinkSync(outside, traces);
  const file = gate.read(fx.roots.data, 'projects/proj-alpha/analyses/run-analysis-done/report.md');
  assert.deepEqual([file.ok, file.reason], [false, 'link']);
  const viaDir = gate.read(fx.roots.data, 'projects/proj-alpha/analyses/run-analysis-done/linked-dir/trace.jsonl');
  assert.deepEqual([viaDir.ok, viaDir.reason], [false, 'link']);
  const viaTraces = gate.read(fx.roots.data, 'projects/proj-alpha/traces/trace.jsonl');
  assert.deepEqual([viaTraces.ok, viaTraces.reason], [false, 'link']);
  assert.equal(gate.tryList(fx.roots.data, 'projects/proj-alpha/traces').ok, false);
  const names = gate.list(fx.roots.data, 'projects/proj-alpha/analyses/run-analysis-done').map(e => e.name);
  assert.ok(!names.includes('report.md') && !names.includes('linked-dir'), 'links are left out of a listing');
  assert.ok(names.includes('task-1.txt'));
  // A hard link is a file with a second name: refused, and left out of the listing.
  const original = path.join(analyses, 'task-1.txt');
  fs.linkSync(original, path.join(analyses, 'task-1-copy.txt'));
  const hard = gate.read(fx.roots.data, 'projects/proj-alpha/analyses/run-analysis-done/task-1.txt');
  assert.deepEqual([hard.ok, hard.reason], [false, 'refused']);
  assert.ok(!gate.list(fx.roots.data, 'projects/proj-alpha/analyses/run-analysis-done').some(e => e.name.startsWith('task-1')));
});

test('a file that vanishes or grows during a read gives a plain answer, not an error page', t => {
  const fx = fixture(t);
  const rel = 'projects/proj-alpha/analyses/run-analysis-done/response-1.txt';
  const file = path.join(fx.data, rel);
  const missing = gate.read(fx.roots.data, 'projects/proj-alpha/analyses/run-analysis-done/none.txt');
  assert.deepEqual([missing.ok, missing.reason], [false, 'missing']);
  const grown = gate.read(fx.roots.data, rel, {afterOpen: () => fs.appendFileSync(file, ' more text appended during the read')});
  assert.deepEqual([grown.ok, grown.reason], [false, 'changed']);
  fs.writeFileSync(file, 'Response one text.');
  const shrunk = gate.read(fx.roots.data, rel, {afterOpen: () => fs.truncateSync(file, 4)});
  assert.deepEqual([shrunk.ok, shrunk.reason], [false, 'changed']);
  fs.writeFileSync(file, 'Response one text.');
  // A file removed after it was opened can still be read through the descriptor; the answer is whole or plain.
  const vanished = gate.read(fx.roots.data, rel, {afterOpen: () => fs.rmSync(file)});
  assert.ok(vanished.ok ? vanished.bytes.toString() === 'Response one text.' : ['missing', 'changed'].includes(vanished.reason));
  // A window read returns the bytes that were there, however the file grows.
  fs.writeFileSync(file, '0123456789');
  const window = gate.read(fx.roots.data, rel, {offset: 2, length: 4, afterOpen: () => fs.appendFileSync(file, 'abc')});
  assert.equal(window.ok, true);
  assert.equal(window.bytes.toString(), '2345');
  assert.equal(window.size, 13);
  assert.deepEqual([gate.read(fx.roots.data, rel, {maxBytes: 3}).reason], ['too-large']);
  // A listing of a folder that is gone is empty, not an exception.
  fs.rmSync(path.join(fx.data, 'projects/proj-alpha/analyses/run-failed'), {recursive: true});
  assert.deepEqual(gate.list(fx.roots.data, 'projects/proj-alpha/analyses/run-failed'), []);
});

test('the read gate gives the same answers when the data folder is read-only for its owner', t => {
  const fx = fixture(t);
  const answers = () => JSON.stringify([
    gate.list(fx.roots.data, 'projects/proj-alpha/analyses/run-analysis-done').map(e => [e.name, e.kind, e.size]),
    gate.read(fx.roots.data, 'projects/proj-alpha/analyses/run-analysis-done/report.md').bytes.toString(),
    gate.read(fx.roots.data, 'projects/proj-alpha/traces/run-analysis-done/trace.jsonl').bytes.length,
    gate.read(fx.roots.data, 'analyst.sqlite').reason
  ]);
  const before = answers();
  makeReadOnly(fx.data);
  t.after(() => makeRemovable(fx.data));
  assert.equal(answers(), before);
});

test('a live work folder is read only under the configured temporary root and only by its exact name pattern', t => {
  const fx = fixture(t);
  const work = fx.liveWork;
  const admitted = gate.openWorkFolder(fx.roots, work);
  assert.ok(admitted, 'a folder made with mkdtemp and the real prefix is admitted');
  assert.equal(admitted.kind, 'work');
  assert.match(path.basename(work), gate.WORK_FOLDER_PATTERN);
  assert.equal(gate.read(admitted, 'task-1.txt').bytes.toString(), 'LIVE PROMPT: check the report now.');
  // The input file of a work folder and anything outside the folder are not special-cased: only names and roots matter.
  assert.equal(gate.read(admitted, '../data/analyst.sqlite').ok, false);
  const make = name => {
    const folder = path.join(fx.tmp, name);
    fs.mkdirSync(folder);
    return folder;
  };
  for (const name of [WORK_PREFIX + 'abcde', WORK_PREFIX + 'abcdefg', WORK_PREFIX + 'abc-ef', 'other-prefix-abcdef', WORK_PREFIX]) {
    assert.equal(gate.openWorkFolder(fx.roots, make(name)), null, name);
  }
  // A name that fits but lies one level deeper, or in another parent, is refused; so are a file and a link.
  const deeper = path.join(make('nested'), WORK_PREFIX + 'abcdef');
  fs.mkdirSync(deeper);
  assert.equal(gate.openWorkFolder(fx.roots, deeper), null);
  const elsewhere = fs.mkdtempSync(path.join(realTmp, WORK_PREFIX));
  t.after(() => fs.rmSync(elsewhere, {recursive: true, force: true}));
  assert.equal(gate.openWorkFolder(fx.roots, elsewhere), null, 'the parent must equal the configured temporary root');
  fs.writeFileSync(path.join(fx.tmp, WORK_PREFIX + 'file01'), 'x');
  assert.equal(gate.openWorkFolder(fx.roots, path.join(fx.tmp, WORK_PREFIX + 'file01')), null);
  fs.symlinkSync(work, path.join(fx.tmp, WORK_PREFIX + 'link01'));
  assert.equal(gate.openWorkFolder(fx.roots, path.join(fx.tmp, WORK_PREFIX + 'link01')), null);
  assert.equal(gate.openWorkFolder(fx.roots, 'relative/' + WORK_PREFIX + 'abcdef'), null);
  assert.equal(gate.openWorkFolder(fx.roots, work + '\0'), null);
  // The names the operating system's mkdtemp makes with the prefix all fit the pattern (20 of 20 on the Mac, 2026-11-20).
  for (let i = 0; i < 40; i++) {
    const made = fs.mkdtempSync(path.join(fx.tmp, WORK_PREFIX));
    assert.ok(gate.openWorkFolder(fx.roots, made), made);
  }
});

test('on macOS the no-follow flag is proven at start without a write: a system link is refused and its real path opens', {skip: onMac ? false : 'The any-component no-follow flag is a macOS feature; this system is not macOS.'}, () => {
  assert.equal(fs.constants.O_NOFOLLOW_ANY, undefined, 'fs.constants does not hold the flag, so the gate names its number');
  assert.equal(gate.NOFOLLOW_ANY, 0x20000000);
  const proof = gate.proveNoFollow();
  assert.deepEqual(proof, {linkRefused: 'ELOOP', realPathOpened: true});
  // A system that did not refuse the link, or refused the real path, stops the server with a plain message.
  assert.throws(() => gate.proveNoFollow({open: () => 1, close: () => {}}), /cannot prove/);
  assert.throws(() => gate.proveNoFollow({open: file => { if (file === '/etc/hosts') { const e = new Error('x'); e.code = 'ELOOP'; throw e; } const e = new Error('y'); e.code = 'EACCES'; throw e; }, close: () => {}}), /cannot prove/);
  assert.deepEqual(Object.keys(gate.proveNoFollow({platform: 'linux'})), ['skipped']);
});
