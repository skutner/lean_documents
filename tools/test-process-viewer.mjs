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
import http from 'node:http';
import net from 'node:net';
import crypto from 'node:crypto';
import {spawn, spawnSync, execFile} from 'node:child_process';
import {promisify} from 'node:util';
import * as gate from './pv-read.mjs';
import {createTraceIndex, ViewerError, partOf, cell} from './pv-trace.mjs';
import {createRunIndex, processAlive} from './pv-runs.mjs';
import {createDocuments} from './pv-documents.mjs';
import {createDossiers} from './pv-dossiers.mjs';
import {startViewer, loadSettings, pagePolicy} from './process-viewer.mjs';
import {treeManifest, crawl, diff} from './check-viewer-readonly.mjs';
import {buildFixture, removeFixture, makeReadOnly, makeRemovable, runStartFields, bookTexts, sha256, SENTINEL, WORK_PREFIX} from './process-viewer-fixture.mjs';

const tools = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(tools, '..');
const realTmp = fs.realpathSync(os.tmpdir());
const onMac = process.platform === 'darwin';

/** Builds a fixture below a fresh temporary folder and removes it when the test ends. */
function fixture(t, options) {
  const base = fs.mkdtempSync(path.join(realTmp, 'pv-test-'));
  t.after(() => removeFixture(base));
  // A process id that is certainly gone: a child that has already exited.
  const deadPid = spawnSync(process.execPath, ['-e', '0']).pid;
  const fx = buildFixture(base, {deadPid, ...options});
  fx.deadPid = deadPid;
  fx.roots = gate.openRoots({deepProofData: fx.data, deepProofTmp: fx.tmp, dossiers: fx.dossiers});
  return fx;
}

// The server modules, in the order they are built. Each must exist: a missing module fails the static test.
// Every server module must exist: a missing one fails this test.
/** A settings file below the worktree's tmp/ for a child viewer, so that no child reads the user's own settings file. */
function pinnedSettings(t, fx, content = null) {
  fs.mkdirSync(path.join(repoRoot, 'tmp'), {recursive: true});
  const dir = fs.mkdtempSync(path.join(repoRoot, 'tmp', 'pv-pinned-'));
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  const file = path.join(dir, 'settings.json');
  fs.writeFileSync(file, JSON.stringify(content ?? {deepProofData: fx.data, dossiers: fx.dossiers, deepProofTmp: fx.tmp, port: 0}));
  return file;
}
/** The environment of a child viewer: pinned to fixture settings and to an explicit dossier list. */
const childEnv = (t, fx, extra = {}) => ({PATH: process.env.PATH, VIEWER_SETTINGS: pinnedSettings(t, fx), VIEWER_DOSSIERS: fx.dossiers.join(path.delimiter), VIEWER_DEEP_PROOF_DATA: fx.data, VIEWER_DEEP_PROOF_TMP: fx.tmp, VIEWER_PORT: '0', ...extra});

// Every server module must exist: a missing one fails this test.
const SERVER_MODULES = ['pv-read.mjs', 'pv-trace.mjs', 'pv-runs.mjs', 'pv-documents.mjs', 'pv-dossiers.mjs', 'process-viewer.mjs'];
const FS_FUNCTIONS = ['openSync', 'readSync', 'fstatSync', 'lstatSync', 'readdirSync', 'closeSync', 'realpathSync', 'readFileSync', 'constants'];
const FORBIDDEN_MODULES = ['node:child_process', 'child_process', 'node:sqlite', 'node:net', 'node:https', 'node:worker_threads', 'node:fs/promises', 'fs/promises', 'node:cluster', 'node:dgram', 'node:tls'];

test('the server modules import only the allowed file functions and no process, database or network module', () => {
  assert.equal(SERVER_MODULES.length, 6, 'the gate, the readers and the server are all checked');
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
  assert.deepEqual(kills, [{name: 'pv-runs.mjs', args: 'pid,0'}]);
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

test('analyst.sqlite and everything outside projects/ is never opened', async t => {
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
  // Through the server: no answer holds the sentinel of the database files (mode 000), of jobs/ or of anything outside
  // projects/, and a request that names them is a plain 404.
  const v = await startServer(t, fx);
  const answers = await crawl(`http://127.0.0.1:${v.port}`, v.key);
  assert.ok(answers.size > 100, `the crawl reached ${answers.size} routes`);
  for (const [route, value] of answers) assert.ok(!value.includes(SENTINEL), route);
  for (const name of ['../analyst.sqlite', 'analyst.sqlite', '../../analyst.sqlite', 'kept:../../../analyst.sqlite', 'kept:../../../../jobs/stale/note.txt', 'source:../../../analyst.sqlite', 'report:analyst.sqlite']) {
    const r = await request(v, `/api/runs/proj-alpha/run-check-done/document?name=${encodeURIComponent(name)}`);
    assert.equal(r.status, 404, name);
    assert.ok(!r.text.includes(SENTINEL));
  }
  for (const route of ['/api/runs/jobs/stale', '/api/runs/..%2Fanalyst.sqlite/x', '/analyst.sqlite', '/api/analyst.sqlite']) {
    const r = await request(v, route);
    assert.ok([400, 404].includes(r.status), `${route} gave ${r.status}`);
    assert.ok(!r.text.includes(SENTINEL));
  }
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

test('the check of a descriptor after an open admits only the located path, on any system', () => {
  const root = {kind: 'data', path: '/data'};
  const located = '/data/projects/p/analyses/r/report.md';
  assert.equal(gate.openedPathVerdict({real: located, located, root}), 'ok');
  // A real path inside the root but not the located one: a link was swapped in between the check and the open.
  assert.equal(gate.openedPathVerdict({real: '/data/projects/p/analyses/r/other.md', located, root}), 'link');
  assert.equal(gate.openedPathVerdict({real: '/data/projects/q/analyses/r/report.md', located, root}), 'link');
  // The database below the data root is inside the root, and still refused.
  assert.equal(gate.openedPathVerdict({real: '/data/analyst.sqlite', located, root}), 'link');
  assert.equal(gate.openedPathVerdict({real: '/etc/hosts', located, root}), 'link');
  assert.equal(gate.openedPathVerdict({real: located + ' (deleted)', located, root}), 'missing');
  assert.equal(gate.openedPathVerdict({real: null, located, root}), 'link');
});

test('a folder swapped for a link between the check and the open is refused', t => {
  const fx = fixture(t);
  const outside = fs.mkdtempSync(path.join(realTmp, 'pv-outside-'));
  t.after(() => fs.rmSync(outside, {recursive: true, force: true}));
  fs.writeFileSync(path.join(outside, 'report.md'), SENTINEL);
  const run = path.join(fx.data, 'projects/proj-alpha/analyses/run-analysis-done');
  const rel = 'projects/proj-alpha/analyses/run-analysis-done/report.md';
  let swapped = false;
  const answer = gate.read(fx.roots.data, rel, {beforeOpen: () => {
    fs.renameSync(run, run + '-aside');
    fs.symlinkSync(outside, run);
    swapped = true;
  }});
  assert.ok(swapped, 'the hook ran between the check and the open');
  assert.deepEqual([answer.ok, answer.reason], [false, 'link']);
  assert.ok(!JSON.stringify(answer).includes(SENTINEL));
});

const KELVIN = '\u212A';
const LONG_S = '\u017F';
const KEPT = 'projects/proj-alpha/traces/run-check-done/workspace';

test('a folded spelling of keys, evaluator or any allowed name is never opened, whatever the volume folds', async t => {
  const fx = fixture(t);
  const keptFolder = path.join(fx.data, KEPT);
  const folds = fs.existsSync(path.join(keptFolder, 'KEYS-x'));
  t.diagnostic(`this volume ${folds ? 'ignores' : 'keeps'} case in file names`);
  const kelvinFolds = fs.existsSync(path.join(keptFolder, `${KELVIN}eys-x`));
  // Spellings of a keys folder at depth 1 and deeper: plain, upper case, Kelvin sign, long s, full width, accents.
  const keys = ['keys', 'KEYS', 'Keys', `${KELVIN}eys`, `key${LONG_S}`, `${KELVIN}EY${LONG_S}`, '\uFF4B\uFF45\uFF59\uFF53', 'k\u00E9ys'];
  const evaluator = ['evaluator', 'EVALUATOR', 'Evaluator', '\uFF45valuator', 'evalu\u0061tor\u200B'];
  const cases = [];
  for (const k of keys) cases.push(`${KEPT}/${k}-x/s.md`, `${KEPT}/notes/${k}-deep/s.md`);
  for (const e of evaluator) cases.push(`${KEPT}/notes/${e}/e.md`);
  for (const rel of cases) {
    const a = gate.read(fx.roots.data, rel);
    assert.equal(a.ok, false, `read ${JSON.stringify(rel)}`);
    assert.ok(!a.bytes, rel);
    assert.equal(gate.stat(fx.roots.data, rel).ok, false, `stat ${JSON.stringify(rel)}`);
    assert.equal(gate.tryList(fx.roots.data, path.posix.dirname(rel)).ok, false, `list ${JSON.stringify(rel)}`);
  }
  // A name that is allowed but spelled with another case or another normalization form is refused as well: the viewer
  // opens a name only when it is spelled as the file system holds it (decision: refused, not matched).
  fs.writeFileSync(path.join(keptFolder, 'notes/caf\u00e9.md'), 'A file with a composed accent in its name.\n');
  const allowed = `${KEPT}/notes/agent-notes.md`;
  assert.equal(gate.read(fx.roots.data, allowed).ok, true);
  assert.match(gate.read(fx.roots.data, allowed).bytes.toString(), /A file the agent wrote/);
  assert.equal(gate.read(fx.roots.data, `${KEPT}/NOTES/agent-notes.md`).ok, false, 'another case');
  assert.equal(gate.read(fx.roots.data, `${KEPT}/notes/Agent-Notes.md`).ok, false);
  assert.equal(gate.read(fx.roots.data, `${KEPT}/notes/caf\u00e9.md`).ok, true, 'a composed name, spelled as on disk');
  const decomposed = gate.read(fx.roots.data, `${KEPT}/notes/cafe\u0301.md`);
  assert.equal(decomposed.ok, false, 'the decomposed spelling of the same name');
  if (fs.existsSync(path.join(keptFolder, 'notes/cafe\u0301.md'))) assert.equal(decomposed.reason, 'refused', 'this volume resolves it, so the viewer refuses it');
  assert.equal(gate.read(fx.roots.data, 'Projects/proj-alpha/project.md').ok, false, 'another case of projects/');
  assert.equal(gate.read(fx.roots.data, 'projects/proj-alpha/project.md').ok, true);
  if (kelvinFolds) assert.ok(fs.existsSync(path.join(keptFolder, `${KELVIN}eys-x/s.md`)), 'on this volume the file system itself resolves the Kelvin spelling');
  // Through the server.
  const v = await startServer(t, fx);
  for (const k of keys) {
    for (const name of [`kept:${k}-x/s.md`, `kept:notes/${k}-deep/s.md`]) {
      const r = await request(v, `/api/runs/proj-alpha/run-check-done/document?name=${encodeURIComponent(name)}`);
      assert.equal(r.status, 404, name);
      assert.ok(!r.text.includes(SENTINEL), name);
    }
  }
  for (const e of evaluator) assert.equal((await request(v, `/api/runs/proj-alpha/run-check-done/document?name=${encodeURIComponent(`kept:notes/${e}/e.md`)}`)).status, 404, e);
  assert.equal((await request(v, `/api/runs/proj-alpha/run-check-done/document?name=${encodeURIComponent('kept:NOTES/agent-notes.md')}`)).status, 404);
  assert.equal((await request(v, `/api/runs/proj-alpha/run-check-done/document?name=${encodeURIComponent('kept:notes/agent-notes.md')}`)).status, 200);
  const all = await crawl(`http://127.0.0.1:${v.port}`, v.key);
  for (const [route, value] of all) assert.ok(!value.includes(SENTINEL), route);
  const page = JSON.parse((await request(v, '/api/runs/proj-alpha/run-check-done')).text);
  assert.ok(!page.results.some(r => /keys|evaluator/i.test(r.title)), 'the kept listing leaves answer-key names out');
});

/** The viewer must refuse these settings. A viewer that starts anyway is closed before the test fails. */
async function refusesToStart(settings, expected, label) {
  let started = null;
  try {
    started = await startViewer({settings});
  } catch (error) {
    assert.match(error.message, expected, label);
    return;
  }
  await started.close();
  assert.fail(`${label}: the viewer started`);
}

test('a dossier setting that a volume resolves to a keys or evaluator folder stops the server', async t => {
  const fx = fixture(t);
  const stage = path.join(fx.bench, 'stage-a');
  const spellings = [`${KELVIN}eys-draft`, `key${LONG_S}-draft`, 'KEYS-DRAFT', 'Keys-sealed'];
  for (const name of spellings) {
    const folder = path.join(stage, name);
    const exists = fs.existsSync(folder);
    const expected = exists ? /answer keys or evaluator/ : /cannot be opened as a folder|answer keys or evaluator/;
    await refusesToStart(settingsOf(fx, {dossiers: [folder]}), expected, name);
    // Also as one of the six, and as an ancestor of a dossier.
    await refusesToStart(settingsOf(fx, {dossiers: [fx.dossiers[0], folder]}), expected, name);
  }
  fs.mkdirSync(path.join(fx.base, 'Evaluator-data'), {recursive: true});
  fs.mkdirSync(path.join(fx.base, 'evaluator', 'inner'), {recursive: true});
  for (const folder of [path.join(fx.base, 'EVALUATOR', 'inner'), path.join(fx.base, '\uFF45valuator')]) {
    await refusesToStart(settingsOf(fx, {dossiers: [folder]}), /answer keys or evaluator|cannot be opened as a folder/, folder);
  }
  // The same check holds for the data root: a folded spelling of an allowed folder is not accepted as a root either.
  const folded = path.join(path.dirname(fx.data), 'DATA');
  if (fs.existsSync(folded)) {
    const started = await startViewer({settings: settingsOf(fx, {deepProofData: folded})});
    try {
      assert.equal(started.roots.data.path, fx.data, 'the root is kept as the file system spells it');
    } finally {
      await started.close();
    }
  }
  // A plain dossier still starts, and the answers hold no sentinel.
  const v = await startServer(t, fx);
  for (const [route, value] of await crawl(`http://127.0.0.1:${v.port}`, v.key)) assert.ok(!value.includes(SENTINEL), route);
});

test('one-line fields of the run list and of the run page are clipped, and the full text still opens', async t => {
  const fx = fixture(t);
  const huge = 'H'.repeat(5 * 1024 * 1024);
  // A hostile register: a finding with a 5 MB title, and more findings than a page lists.
  const file = path.join(fx.data, 'projects/proj-alpha/analyses/run-check-done/quality/findings.jsonl');
  const rows = fs.readFileSync(file, 'utf8').trim().split('\n').map(l => JSON.parse(l));
  rows[0].title = huge;
  rows[0].verdict = 'v'.repeat(100000);
  rows[0].severity = 's'.repeat(100000);
  for (let i = 0; i < 1100; i++) rows.push({id: `F-X${i}`, title: `Extra finding ${i}`, verdict: 'rejected'});
  fs.writeFileSync(file, rows.map(r => JSON.stringify(r)).join('\n') + '\n');
  // A hostile trace: huge title, agent, model, skill name and document title in run-start.
  const dir = path.join(fx.data, 'projects/proj-alpha/traces/run-hostile');
  fs.mkdirSync(dir, {recursive: true});
  const start = runStartFields({runId: 'run-hostile', kind: 'quality', createdAt: new Date().toISOString(), documents: [{id: 'doc-h', title: huge, fileType: 'md', originalName: huge, bytes: 3, sha256: 'a'.repeat(64)}],
    overrides: {title: huge, agent: huge, model: huge, projectName: huge, skill: {id: 's', name: huge, revision: huge, verification: huge}}});
  fs.writeFileSync(path.join(dir, 'trace.jsonl'), JSON.stringify({seq: 1, at: new Date().toISOString(), type: 'run-start', ...start}) + '\n');
  const v = await startServer(t, fx);
  const strings = value => (typeof value === 'string' ? [value] : Array.isArray(value) ? value.flatMap(strings) : value && typeof value === 'object' ? Object.values(value).flatMap(strings) : []);
  const list = await request(v, '/api/runs');
  assert.ok(list.bytes.length < 200000, `the run list answered ${list.bytes.length} bytes`);
  for (const item of JSON.parse(list.text)) for (const s of strings(item)) assert.ok(s.length <= 400, `${item.key}: ${s.length} characters`);
  for (const rid of ['run-check-done', 'run-hostile']) {
    const page = await request(v, `/api/runs/proj-alpha/${rid}`);
    assert.ok(page.bytes.length < 400000, `${rid} answered ${page.bytes.length} bytes`);
    const parsed = JSON.parse(page.text);
    for (const [key, value] of Object.entries(parsed)) for (const s of strings(value)) assert.ok(s.length <= 400, `${rid} ${key}: ${s.length} characters`);
  }
  const page = JSON.parse((await request(v, '/api/runs/proj-alpha/run-check-done')).text);
  const cards = page.results.filter(r => r.kind === 'finding');
  assert.equal(cards.length, 1000, 'findings with a card are capped');
  assert.match(cards[0].title, /^H+…$/);
  assert.ok(cards[0].title.length <= 240);
  assert.ok(page.notices.some(n => n.kind === 'findings-capped' && /1000 of 1102/.test(n.text)));
  assert.equal(cards[0].id, 'finding:F-001', 'identifiers are never clipped');
  assert.equal(JSON.parse(list.text).find(r => r.key === 'proj-alpha/run-hostile').key, 'proj-alpha/run-hostile');
  // The full text is still in the document that opens (its first part).
  const doc = JSON.parse((await request(v, `/api/runs/proj-alpha/run-check-done/document?name=${encodeURIComponent('finding:F-001')}`)).text);
  assert.ok(doc.text.startsWith('## F-001: HHHH'));
  assert.ok(doc.parts >= 1 && doc.text.length <= 262144);
});

// ---------- Readers: trace, runs, documents ----------

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

/** The readers wired as the server wires them, on a fixture. Options: `now`, `alive`, `gate` (a wrapper of the read gate). */
function services(fx, {now, alive, gate: g = gate} = {}) {
  const traces = createTraceIndex({root: fx.roots.data, gate: g});
  const runs = createRunIndex({roots: fx.roots, traces, gate: g, now, alive});
  const docs = createDocuments({roots: fx.roots, traces, runs, gate: g});
  return {traces, runs, docs};
}
const byKey = (list, fx, id) => list.find(r => r.key === `${fx.pid}/${fx.ids[id]}`);

test('a run without a trace shows an inferred status and the list of what is missing', t => {
  const fx = fixture(t);
  const {runs, docs} = services(fx);
  const list = runs.publicList();
  const done = byKey(list, fx, 'untracedDone');
  assert.deepEqual([done.kind, done.status, done.statusSource, done.traced], ['analysis', 'completed', 'inferred', false]);
  assert.deepEqual(['time-limit', 'Validated wrap-up report'], [byKey(list, fx, 'untracedPasted').status, byKey(list, fx, 'untracedPasted').statusDetail]);
  assert.deepEqual(['time-limit', 'Notes only'], [byKey(list, fx, 'untracedNotes').status, byKey(list, fx, 'untracedNotes').statusDetail]);
  assert.deepEqual(['failed', 'Validation'], [byKey(list, fx, 'untracedFailed').status, byKey(list, fx, 'untracedFailed').statusDetail]);
  const check = byKey(list, fx, 'untracedCheck');
  assert.deepEqual([check.kind, check.status, check.statusSource], ['quality', 'completed', 'inferred']);
  for (const id of ['untracedUnknown', 'emptyRun']) {
    const r = byKey(list, fx, id);
    assert.equal(r.status, 'not-recorded', id);
    assert.match(r.statusDetail, /Running, or ended without a result/);
  }
  const page = docs.runPage(fx.pid, fx.ids.untracedDone);
  assert.equal(page.header.trace, 'Not Recorded');
  assert.ok(page.missing.length >= 5, 'the page lists what is missing');
  assert.ok(page.notices.some(n => /inferred from files/.test(n.text)));
  assert.ok(page.results.some(r => r.id === 'report') && page.results.some(r => r.id === 'transcript'));
  // The timeline is built from the transcript under a banner that names what is missing.
  const timeline = docs.timeline(fx.pid, fx.ids.untracedDone, 0);
  assert.equal(timeline.traced, false);
  assert.ok(timeline.banner.missing.length > 0);
  assert.deepEqual(timeline.entries.map(e => e.kind), ['run-started', 'prompt-sent', 'turn-ended']);
  assert.equal(docs.entryBody(fx.pid, fx.ids.untracedDone, 2).text, 'Untraced prompt.');
  assert.match(docs.document(fx.pid, fx.ids.untracedDone, 'transcript').text, /Untraced prompt/);
  // A check without its work folder shows its inputs and registers but no process.
  assert.deepEqual(docs.runPage(fx.pid, fx.ids.untracedCheck).missing.length > 0, true);
});

test('an imported report is classified as Imported Report, not as a failed run', t => {
  const fx = fixture(t);
  const {runs, docs} = services(fx);
  const r = byKey(runs.publicList(), fx, 'imported');
  assert.deepEqual([r.kind, r.kindLabel, r.status, r.statusLabel], ['imported', 'Imported Report', 'imported', 'Imported Report']);
  assert.equal(r.title, 'Review of book 1');
  assert.equal(docs.runPage(fx.pid, fx.ids.imported).missing.length, 0);
  assert.ok(docs.runPage(fx.pid, fx.ids.imported).inputs.some(i => i.kind === 'report-under-review'));
});

test('a run with no run-end is Running while the pid answers and the bound holds, and Interrupted after either fails', t => {
  const fx = fixture(t);
  const list = services(fx).runs.publicList();
  assert.equal(byKey(list, fx, 'checkLive').status, 'running');
  assert.equal(byKey(list, fx, 'onlyStart').status, 'running');
  assert.equal(byKey(list, fx, 'halfLine').status, 'running');
  assert.deepEqual([byKey(list, fx, 'interruptedPid').status, byKey(list, fx, 'interruptedPid').statusDetail], ['interrupted', 'The Deep Proof server is gone']);
  assert.equal(byKey(list, fx, 'interruptedLate').status, 'interrupted', 'the time bound passed although the pid answers');
  // The bound is createdAt + workMs + wrapUpMs + 120 s.
  const later = services(fx, {now: () => Date.now() + 2 * 3600 * 1000}).runs.publicList();
  assert.equal(byKey(later, fx, 'checkLive').status, 'interrupted');
  const gone = services(fx, {alive: () => false}).runs.publicList();
  assert.equal(byKey(gone, fx, 'checkLive').status, 'interrupted');
  // A finished run keeps its recorded status whatever the clock or the pid say.
  assert.equal(byKey(gone, fx, 'analysisDone').status, 'completed');
  assert.equal(byKey(gone, fx, 'failed').statusDetail, 'Validation');
  assert.deepEqual([byKey(list, fx, 'timeLimitNotes').statusDetail, byKey(list, fx, 'timeLimitWrap').statusDetail], ['Notes only', 'Validated wrap-up report']);
  assert.equal(processAlive(process.pid), true);
  for (const bad of [0, -1, 1.5, NaN, '123', null, undefined, 2 ** 60]) assert.equal(processAlive(bad), false, String(bad));
  assert.equal(processAlive(fx.deadPid), false);
});

test('a trace of another version falls back to the files and says so; empty projects and folders are listed', t => {
  const fx = fixture(t);
  const {runs, docs} = services(fx);
  const list = runs.publicList();
  const v2 = byKey(list, fx, 'versionTwo');
  assert.deepEqual([v2.traced, v2.kind, v2.statusSource], [false, 'analysis', 'inferred']);
  assert.ok(docs.runPage(fx.pid, fx.ids.versionTwo).notices.some(n => n.kind === 'trace-version' && /another version \(2\)/.test(n.text)));
  assert.ok(byKey(list, fx, 'onlyStart'), 'a trace with only run-start is listed');
  assert.equal(byKey(list, fx, 'emptyRun').kind, 'unknown');
  assert.ok(!list.some(r => r.key.startsWith('proj-empty/') || r.key.startsWith('proj-bare/')), 'a project with no runs lists no run');
  assert.equal(list.length, Object.keys(fx.ids).length);
  assert.ok(list.every(r => !/\/(?:Users|home|var|tmp|private)\//.test(JSON.stringify(r))), 'no absolute path in the run list');
});

test('a half-written last line of the trace is left for the next poll', t => {
  const fx = fixture(t);
  const {traces} = services(fx);
  const rel = path.join(fx.data, 'projects/proj-alpha/traces/run-half-line/trace.jsonl');
  const before = traces.timeline(fx.pid, fx.ids.halfLine, 0);
  assert.equal(before.entries.length, 17, 'the seventeen finished records of the trace');
  assert.equal(traces.summary(fx.pid, fx.ids.halfLine).bad, 0, 'a half line is not a bad line');
  // The rest of the line arrives: it appears once, after the others.
  fs.appendFileSync(rel, '00.000Z","type":"progress-note","text":"Arrived late.","blocks":9}\n');
  const after = traces.timeline(fx.pid, fx.ids.halfLine, before.next);
  assert.deepEqual(after.entries.map(e => [e.n, e.title, e.summary]), [[18, 'Progress Note', 'Arrived late.']]);
  assert.equal(traces.timeline(fx.pid, fx.ids.halfLine, after.next).entries.length, 0);
  // A line that is complete but not JSON is counted and skipped.
  fs.appendFileSync(rel, 'not json at all\n{"seq":100,"at":"2026-01-01T00:00:00.000Z","type":"progress-note","text":"After the bad line.","blocks":10}\n');
  const again = traces.timeline(fx.pid, fx.ids.halfLine, 18);
  assert.deepEqual(again.entries.map(e => e.summary), ['After the bad line.']);
  assert.equal(traces.summary(fx.pid, fx.ids.halfLine).bad, 1);
});

test('a writer appending in pieces smaller than a line is read once and in order while the viewer polls every 50 ms', async t => {
  const fx = fixture(t);
  const {traces} = services(fx);
  const file = path.join(fx.data, 'projects/proj-alpha/traces/run-only-start/trace.jsonl');
  const base = traces.timeline(fx.pid, fx.ids.onlyStart, 0).entries.length;
  const total = 100;
  let text = '';
  for (let i = 1; i <= total; i++) text += JSON.stringify({seq: 1 + i, at: new Date(Date.now() + i).toISOString(), type: 'progress-note', turn: '1', text: `Note number ${i} with some padding to make the line longer than a few bytes.`, blocks: i}) + '\n';
  let writerDone = false;
  const writer = (async () => {
    let at = 0;
    while (at < text.length) {
      const size = 3 + (at % 7);
      fs.appendFileSync(file, text.slice(at, at + size));
      at += size;
      await sleep(1);
    }
    writerDone = true;
  })();
  const seen = [];
  let after = base;
  const started = Date.now();
  while (!writerDone || Date.now() - started < 5000) {
    const answer = traces.timeline(fx.pid, fx.ids.onlyStart, after);
    for (const e of answer.entries) seen.push([e.n, e.summary]);
    after = answer.next;
    await sleep(50);
  }
  await writer;
  const last = traces.timeline(fx.pid, fx.ids.onlyStart, after);
  for (const e of last.entries) seen.push([e.n, e.summary]);
  assert.equal(seen.length, total, 'every record appears once');
  seen.forEach(([n, summary], i) => {
    assert.equal(n, base + i + 1, 'in order, without a gap');
    assert.equal(summary, `Note number ${i + 1} with some padding to make the line longer than a few bytes.`);
  });
});

test('long lines are read in parts: 300 KiB and 2 MiB engine lines, and a line past the viewer limit is skipped', t => {
  const fx = fixture(t);
  const {traces, docs} = services(fx);
  const file = path.join(fx.data, 'projects/proj-alpha/traces/run-only-start/trace.jsonl');
  const big = (seq, chars, letter) => JSON.stringify({seq, at: '2026-01-01T00:00:10.000Z', type: 'ala-stderr', turn: '1', text: letter.repeat(chars)}) + '\n';
  fs.appendFileSync(file, big(2, 300 * 1024, 'é') + big(3, 2 * 1024 * 1024, 'e') + 'x'.repeat(5 * 1024 * 1024) + '\n' + big(4, 10, 'e'));
  const answer = traces.timeline(fx.pid, fx.ids.onlyStart, 0);
  assert.deepEqual(answer.entries.map(e => e.kind), ['run-started', 'diagnostic', 'diagnostic', 'diagnostic']);
  assert.equal(traces.summary(fx.pid, fx.ids.onlyStart).oversize, 1);
  const part0 = docs.entryBody(fx.pid, fx.ids.onlyStart, 3, 0);
  assert.equal(part0.parts, 8);
  let all = '';
  for (let k = 0; k < part0.parts; k++) all += docs.entryBody(fx.pid, fx.ids.onlyStart, 3, k).text;
  assert.equal(all, 'e'.repeat(2 * 1024 * 1024));
  // Two-byte letters are never cut in the middle: the parts of the 300 KiB line join into the same text.
  const first = docs.entryBody(fx.pid, fx.ids.onlyStart, 2, 0);
  assert.equal(first.parts, 3);
  let joined = '';
  for (let k = 0; k < first.parts; k++) joined += docs.entryBody(fx.pid, fx.ids.onlyStart, 2, k).text;
  assert.equal(joined, 'é'.repeat(300 * 1024));
  assert.throws(() => docs.entryBody(fx.pid, fx.ids.onlyStart, 3, part0.parts), e => e instanceof ViewerError && e.status === 404);
  assert.deepEqual(partOf('abc', 0), {text: 'abc', part: 0, parts: 1});
  assert.equal(partOf('abc', 1), null);
});

test('a work folder removed while it is being read ends the read without an error', t => {
  const fx = fixture(t);
  const removing = {...gate, read: (root, rel, opts) => gate.read(root, rel, root.kind === 'work' ? {...opts, afterOpen: () => fs.rmSync(fx.liveWork, {recursive: true, force: true})} : opts)};
  const {docs} = services(fx, {gate: removing});
  let answer;
  assert.doesNotThrow(() => { answer = docs.document(fx.pid, fx.ids.checkLive, 'prompt:1'); });
  assert.match(answer.text, /LIVE PROMPT/);
  // Afterwards the folder is gone: the document is a plain 404 and the page says so, with the trace still shown.
  assert.throws(() => docs.document(fx.pid, fx.ids.checkLive, 'prompt:1'), e => e instanceof ViewerError && e.status === 404 && !/\//.test(e.message));
  const page = docs.runPage(fx.pid, fx.ids.checkLive);
  assert.ok(page.notices.some(n => n.kind === 'work-folder-gone'));
  assert.ok(docs.timeline(fx.pid, fx.ids.checkLive, 0).entries.length >= 3);
  // The same page for a folder that never existed: the finished check names a path that is not there.
  assert.ok(docs.runPage(fx.pid, fx.ids.checkDone).notices.every(n => n.kind !== 'work-folder-gone'), 'its kept copy is the record');
  // Also the live folder is read through the gate only: a trace naming another folder is not admitted.
  const rogue = fs.mkdtempSync(path.join(realTmp, 'pv-rogue-'));
  t.after(() => fs.rmSync(rogue, {recursive: true, force: true}));
  fs.writeFileSync(path.join(rogue, 'task-1.txt'), SENTINEL);
  const trace = path.join(fx.data, 'projects/proj-alpha/traces/run-check-live/trace.jsonl');
  fs.writeFileSync(trace, fs.readFileSync(trace, 'utf8').replace(JSON.stringify(fx.liveWork).slice(1, -1), rogue.replace(/\\/g, '/')));
  const second = services(fx).docs;
  assert.throws(() => second.document(fx.pid, fx.ids.checkLive, 'prompt:1'), e => e.status === 404);
});

test('registers become tables and cards; no answer holds a raw JSON line of a register or of the trace', t => {
  const fx = fixture(t);
  const {docs} = services(fx);
  const rid = fx.ids.checkDone;
  const page = docs.runPage(fx.pid, rid);
  assert.deepEqual(page.results.filter(r => r.kind === 'register').map(r => r.title), ['Mandate', 'Claims', 'Criteria', 'Evidence', 'Checks', 'Omissions', 'Findings', 'Review Coverage', 'External Sources', 'Coverage']);
  const answers = [JSON.stringify(page)];
  const names = [...page.results.filter(r => r.doc).map(r => r.doc), 'prompt:1', 'response:1', 'source:report-under-review', 'progress'];
  for (const name of names) {
    const d = docs.document(fx.pid, rid, name);
    answers.push(d.text);
  }
  const claims = docs.document(fx.pid, rid, 'register:claims').text;
  assert.match(claims, /^\| ID \| Claim \| Type \| Status \| Document and lines \| Quote \|$/m);
  assert.match(claims, /Costs fell \\\| by a tenth\. Second line\./, 'a pipe is escaped and a line break becomes a space');
  assert.match(claims, /Not recorded/, 'a missing field shows Not recorded');
  const findings = docs.document(fx.pid, rid, 'finding:F-001').text;
  for (const part of ['**Verdict:** Confirmed', 'Revenue was 112 against 100.', '### Challenge', 'No later table restates the figures', 'Figures must match the source tables.', 'Compared the two figures.']) assert.ok(findings.includes(part), part);
  assert.ok(docs.document(fx.pid, rid, 'register:findings').text.includes('**Verdict:** Rejected'), 'rejected candidates are shown, marked Rejected');
  assert.match(docs.document(fx.pid, rid, 'register:mandate').text, /\| Scope \| The quarterly figures \|/);
  assert.match(docs.document(fx.pid, rid, 'register:coverage').text, /Completion:\*\* Complete/);
  const findingCards = page.results.filter(r => r.kind === 'finding');
  assert.deepEqual(findingCards.map(c => c.verdictLabel), ['Confirmed', 'Rejected']);
  const trace = fs.readFileSync(path.join(fx.data, 'projects/proj-alpha/traces/run-analysis-done/trace.jsonl'), 'utf8');
  const everything = [...answers];
  for (const rid2 of [fx.ids.analysisDone, fx.ids.checkDone, fx.ids.checkLive]) {
    const tl = docs.timeline(fx.pid, rid2, 0);
    everything.push(JSON.stringify(tl));
    for (const e of tl.entries) if (e.hasBody) everything.push(docs.entryBody(fx.pid, rid2, e.n).text);
  }
  for (const text of everything) {
    assert.doesNotMatch(text, /"seq"\s*:/, 'no trace line');
    assert.doesNotMatch(text, /\{"id":"(?:CL|C|E|K|O|F|R)-\d+"/, 'no register line');
    assert.ok(!text.includes(SENTINEL));
  }
  assert.ok(trace.includes('"seq"'), 'the fixture trace itself holds raw lines');
  // A data file of the kept copy is listed, not displayed; a path outside the copy is a plain 404.
  const kept = page.results.filter(r => r.kind === 'kept-file');
  assert.ok(kept.some(k => k.title === 'data/raw.json' && k.doc === null && /not displayed/.test(k.note)));
  assert.match(docs.document(fx.pid, rid, 'kept:notes/agent-notes.md').text, /A file the agent wrote/);
  assert.match(docs.document(fx.pid, rid, 'kept:data/raw.json').text, /Data file, not displayed/);
  for (const bad of ['kept:../report.md', 'kept:notes/missing.md', 'kept:/etc/hosts', 'kept:keys/x', 'register:none', 'finding:F-999', 'nonsense', 'source:../x']) {
    assert.throws(() => docs.document(fx.pid, rid, bad), e => e instanceof ViewerError && e.status === 404, bad);
  }
});

test('a half-written or non-JSON register row is counted, not shown, and a register comes from the kept copy for a failed run', t => {
  const fx = fixture(t);
  const file = path.join(fx.data, 'projects/proj-alpha/analyses/run-check-done/quality/claims.jsonl');
  fs.appendFileSync(file, 'this is not json\n{"id":"CL-009","text":"cut off here');
  const {docs} = services(fx);
  const doc = docs.document(fx.pid, fx.ids.checkDone, 'register:claims');
  assert.match(doc.text, /2 rows of this register could not be read/);
  assert.doesNotMatch(doc.text, /cut off here/);
  // With the registers removed from the run folder, the kept copy answers, marked as a draft.
  fs.rmSync(path.join(fx.data, 'projects/proj-alpha/analyses/run-check-done/quality'), {recursive: true});
  const kept = services(fx).docs.document(fx.pid, fx.ids.checkDone, 'register:claims');
  assert.match(kept.notice, /Draft: not validated/);
  // A live work folder answers while the check runs; its report is a draft.
  const live = services(fx).docs.document(fx.pid, fx.ids.checkLive, 'report');
  assert.match(live.notice, /Draft: not validated/);
  assert.match(live.text, /Draft text/);
});

test('a key to a run, an entry, a document or a part that does not exist answers 404 in plain words', t => {
  const fx = fixture(t);
  const {docs} = services(fx);
  const rejects = fn => assert.throws(fn, e => e instanceof ViewerError && e.status === 404 && /^[A-Z][^/]*\.$/.test(e.message));
  rejects(() => docs.runPage('proj-alpha', 'no-such-run'));
  rejects(() => docs.runPage('../etc', 'x'));
  rejects(() => docs.timeline('proj-alpha', 'no-such-run', 0));
  rejects(() => docs.entryBody(fx.pid, fx.ids.analysisDone, 9999));
  rejects(() => docs.entryBody(fx.pid, fx.ids.analysisDone, 0));
  rejects(() => docs.entryBody(fx.pid, fx.ids.analysisDone, 1, 5));
  rejects(() => docs.document(fx.pid, fx.ids.analysisDone, 'report', 3));
  rejects(() => docs.document(fx.pid, fx.ids.analysisDone, 'prompt:9'));
  rejects(() => docs.document(fx.pid, fx.ids.analysisDone, 'prompt:../x'));
  // Past the last entry the timeline is empty, not an error.
  assert.deepEqual(docs.timeline(fx.pid, fx.ids.analysisDone, 9999).entries, []);
});

test('a prompt opens from the run folder, a kept copy or a live folder, and the timeline numbers stay stable', t => {
  const fx = fixture(t);
  const {docs} = services(fx);
  const tl = docs.timeline(fx.pid, fx.ids.analysisDone, 0);
  const prompt = tl.entries.find(e => e.kind === 'prompt-sent');
  const repair = tl.entries.find(e => e.kind === 'repair-prompt');
  assert.equal(docs.entryBody(fx.pid, fx.ids.analysisDone, prompt.n).text, 'PROMPT ONE: review the dossier carefully.');
  assert.equal(docs.entryBody(fx.pid, fx.ids.analysisDone, repair.n).text, 'PROMPT TWO: repair the report.');
  assert.deepEqual(tl.entries.map(e => e.n), tl.entries.map((_, i) => i + 1));
  assert.equal(docs.entryBody(fx.pid, fx.ids.checkDone, docs.timeline(fx.pid, fx.ids.checkDone, 0).entries.find(e => e.kind === 'prompt-sent').n).text, 'KEPT PROMPT: check the report.');
  assert.match(docs.entryBody(fx.pid, fx.ids.checkLive, docs.timeline(fx.pid, fx.ids.checkLive, 0).entries.find(e => e.kind === 'prompt-sent').n).text, /LIVE PROMPT/);
  // Text from an agent is returned as written, with its Romanian letters, emoji and markup.
  const message = tl.entries.find(e => e.kind === 'agent-message');
  const body = docs.entryBody(fx.pid, fx.ids.analysisDone, message.n);
  assert.ok(body.text.includes('ăâîșț') && body.text.includes('😀') && body.text.includes('<script>window.__bad = 1</script>'));
  const command = docs.entryBody(fx.pid, fx.ids.analysisDone, tl.entries.find(e => e.kind === 'command-output').n);
  assert.match(command.note, /not the command line/);
  const failed = docs.entryBody(fx.pid, fx.ids.analysisDone, tl.entries.find(e => e.kind === 'validation-failed').n);
  assert.match(failed.text, /^1\. Finding 1 has no evidence footnote\.\n2\. The Summary chapter is missing\.$/);
  assert.match(docs.document(fx.pid, fx.ids.analysisDone, 'validation:1').text, /1\. Finding 1 has no evidence footnote\.\n2\. .*\n\nFix the Markdown output\./);
});

test('two traces written at the same time keep their own sequence numbers from 1', t => {
  const fx = fixture(t);
  const {traces} = services(fx);
  for (const rid of [fx.ids.analysisDone, fx.ids.checkDone]) {
    const text = fs.readFileSync(path.join(fx.data, `projects/proj-alpha/traces/${rid}/trace.jsonl`), 'utf8').trim().split('\n').map(l => JSON.parse(l));
    assert.deepEqual(text.map(r => r.seq), text.map((_, i) => i + 1));
    assert.equal(text[0].type, 'run-start');
    assert.equal(traces.summary(fx.pid, rid).lastSeq, text.length);
  }
});

test('an output that never completed is shown as a last entry once the run is no longer running', t => {
  const fx = fixture(t);
  const file = path.join(fx.data, 'projects/proj-alpha/traces/run-only-start/trace.jsonl');
  const fragment = (seq, message) => JSON.stringify({seq, at: new Date().toISOString(), type: 'ala-event', turn: '1', event: {type: 'coding-agent-message', agent: 'codex', outputKind: 'assistant', outputId: 'mx', message}}) + '\n';
  fs.appendFileSync(file, fragment(2, 'First half of the last message, ') + fragment(3, 'second half, never completed.'));
  const live = services(fx);
  const before = live.docs.timeline(fx.pid, fx.ids.onlyStart, 0);
  assert.deepEqual(before.entries.map(e => e.kind), ['run-started'], 'a live run behaves as before: the open message is not shown yet');
  assert.throws(() => live.docs.entryBody(fx.pid, fx.ids.onlyStart, 2), e => e.status === 404);
  const ended = services(fx, {alive: () => false});
  assert.equal(ended.runs.get(fx.pid, fx.ids.onlyStart).status, 'interrupted');
  const after = ended.docs.timeline(fx.pid, fx.ids.onlyStart, 0);
  assert.deepEqual(after.entries.map(e => [e.n, e.kind]), [[1, 'run-started'], [2, 'agent-message']]);
  assert.deepEqual(after.entries[0], before.entries[0], 'earlier entries do not change');
  assert.match(after.entries[1].summary, /First half of the last message/);
  assert.match(after.entries[1].summary, /cut off/);
  assert.equal(after.entries[1].hasBody, true);
  assert.equal(after.total, 2);
  const body = ended.docs.entryBody(fx.pid, fx.ids.onlyStart, 2);
  assert.equal(body.text, 'First half of the last message, second half, never completed.');
  assert.match(body.note, /cut off/);
  // The next poll with the same state gives the same entry; paging past it is empty.
  assert.deepEqual(ended.docs.timeline(fx.pid, fx.ids.onlyStart, 1).entries.map(e => e.n), [2]);
  assert.deepEqual(ended.docs.timeline(fx.pid, fx.ids.onlyStart, 2).entries, []);
  // When a record arrives later and flushes the message, it keeps number 2.
  fs.appendFileSync(file, JSON.stringify({seq: 4, at: new Date().toISOString(), type: 'progress-note', turn: '1', text: 'Later note.', blocks: 1}) + '\n');
  const later = ended.docs.timeline(fx.pid, fx.ids.onlyStart, 0);
  assert.deepEqual(later.entries.map(e => [e.n, e.kind]), [[1, 'run-started'], [2, 'agent-message'], [3, 'progress-note']]);
  // A run that ended with run-end has nothing pending and gains no entry.
  assert.equal(ended.docs.timeline(fx.pid, fx.ids.analysisDone, 0).total, 18);
});

test('an object in a register field is shown as readable text, never as [object Object]', t => {
  const fx = fixture(t);
  const {docs} = services(fx);
  const mandate = docs.document(fx.pid, fx.ids.checkDone, 'register:mandate').text;
  assert.match(mandate, /\| Covered period \| from: 2026-01-01; to: 2026-03-31 \|/);
  assert.equal(cell({a: 1, b: ['x', 'y'], c: {d: null}}), 'a: 1; b: x, y; c: d: Not recorded');
  assert.equal(cell(['one', {two: 2}]), 'one, two: 2');
  const rid = fx.ids.checkDone;
  const page = docs.runPage(fx.pid, rid);
  for (const card of page.results) if (card.doc) assert.ok(!docs.document(fx.pid, rid, card.doc).text.includes('[object Object]'), card.doc);
});

const GOLDEN = path.join(tools, 'fixtures/deep-proof-trace-v1-sample.jsonl');
test('the golden trace sample gives the expected timeline', t => {
  assert.ok(fs.existsSync(GOLDEN), 'the golden sample must be present: tools/fixtures/deep-proof-trace-v1-sample.jsonl');
  const base = fs.mkdtempSync(path.join(realTmp, 'pv-golden-'));
  t.after(() => fs.rmSync(base, {recursive: true, force: true}));
  const records = fs.readFileSync(GOLDEN, 'utf8').trim().split('\n').map(l => JSON.parse(l));
  const dir = path.join(base, 'data/projects/project-sample/traces/run-sample');
  fs.mkdirSync(dir, {recursive: true});
  fs.copyFileSync(GOLDEN, path.join(dir, 'trace.jsonl'));
  fs.mkdirSync(path.join(base, 'tmp'));
  const roots = gate.openRoots({deepProofData: path.join(base, 'data'), deepProofTmp: path.join(base, 'tmp')});
  const traces = createTraceIndex({root: roots.data});
  // The expectation is worked out from the records by the contract's table, not by the reader's code.
  const KIND = {'run-start': 'run-started', 'workspace-created': 'workspace', 'workspace-kept': 'workspace', 'workspace-keep-failed': 'workspace', 'progress-note': 'progress-note', 'turn-end': 'turn-ended', continuation: 'continuation', 'time-limit': 'time-limit', 'wrap-up': 'wrap-up', published: 'published', 'ala-stderr': 'diagnostic', 'trace-limit': 'trace-limit', 'run-end': 'run-ended', 'reminder-sent': 'reminder'};
  const expected = [];
  let open = null;
  const close = () => { if (open) expected.push(open.kind); open = null; };
  for (const r of records) {
    const ev = r.event;
    if (r.type === 'ala-event' && ev?.type === 'coding-agent-message') {
      const kind = ev.outputKind === 'assistant' ? 'agent-message' : 'command-output', id = ev.outputId ?? null;
      if (kind === 'command-output' && id === null) { close(); if (ev.message) expected.push(kind); continue; }
      if (open && (open.kind !== kind || open.id !== id || open.turn !== r.turn)) close();
      open ??= {kind, id, turn: r.turn, text: ''};
      open.text += ev.message ?? '';
      if (ev.outputComplete) { if (open.text) expected.push(kind); open = null; }
      continue;
    }
    close();
    if (r.type === 'turn-start') expected.push(r.repair ? 'repair-prompt' : 'prompt-sent');
    else if (r.type === 'validation') expected.push(r.valid ? 'validation-passed' : 'validation-failed');
    else if (r.type === 'ala-event') expected.push({'coding-agent-selected': 'agent-selected', 'coding-agent-final': 'final-message', 'message-accepted': 'reminder', 'message-rejected': 'reminder', 'coding-agent-request': 'agent-request', 'coding-agent-request-resolved': 'agent-request'}[ev?.type] ?? 'other');
    else expected.push(KIND[r.type] ?? 'other');
  }
  const answer = traces.timeline('project-sample', 'run-sample', 0);
  assert.deepEqual(answer.entries.map(e => e.kind), expected);
  // The titles of the sample's 33 records, as the contract's table names them (two fragments join into one message).
  assert.deepEqual(answer.entries.map(e => e.title), ['Run Started', 'Work Folder Created', 'Prompt Sent (Attempt 1)', 'Agent Selected', 'Engine Diagnostic', 'Agent Message', 'Command Output',
    'Progress Note', 'Progress Reminder Sent', 'Progress Reminder Accepted', 'Turn Ended', 'Empty Response: Continuing', 'Prompt Sent (Attempt 1)', 'Final Agent Message', 'Turn Ended',
    'Validation Failed (2 errors)', 'Repair Prompt Sent (Attempt 2)', 'Agent Message', 'Progress Note', 'Progress Reminder Sent', 'Progress Reminder Rejected', 'Turn Ended', 'Time Limit Reached',
    'Wrap-Up Turn', 'Prompt Sent (Wrap-Up)', 'Final Agent Message', 'Turn Ended', 'Validation Passed', 'Wrap-Up Validated', 'Published', 'Work Folder Copy Kept', 'Run Completed']);
  assert.equal(answer.entries.length, records.length - 1);
  assert.deepEqual(answer.entries.map(e => e.offsetMs).slice(0, 4), [0, 1000, 2000, 3000]);
  const joined = traces.body('project-sample', 'run-sample', 6);
  assert.equal(joined.format, 'markdown');
  assert.ok(joined.text.startsWith('I will read the report under review and the board minutes first.') && joined.text.includes('𝔼 <script>alert(1)</script>'));
  assert.match(traces.body('project-sample', 'run-sample', 7).note, /not the command line/);
  const runs = createRunIndex({roots, traces, now: () => Date.parse('2026-01-01T01:00:00.000Z')});
  const run = runs.get('project-sample', 'run-sample');
  assert.deepEqual([run.kind, run.traced, run.status, run.statusDetail, run.statusSource], ['quality', true, 'time-limit', 'Validated wrap-up report', 'trace']);
  assert.equal(records[0].type, 'run-start');
  assert.equal(traces.summary('project-sample', 'run-sample').runStart.runId, 'run-sample');
});

// ---------- The server ----------

function settingsOf(fx, extra = {}) {
  return {deepProofData: fx.data, deepProofTmp: fx.tmp, dossiers: fx.dossiers, port: 0, ...extra};
}

/** Starts the viewer in this process on a free port (port 0) and closes it when the test ends. */
async function startServer(t, fx, {settings = settingsOf(fx), ...options} = {}) {
  const v = await startViewer({settings, ...options});
  t.after(() => v.close());
  return v;
}

/** One request with full control of method, host and headers. Resolves with status, headers, bytes and text. */
function request(v, route, {method = 'GET', headers = {}, host, body, key = v.key} = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({host: '127.0.0.1', port: v.port, path: route, method, headers: {host: host ?? `127.0.0.1:${v.port}`, ...(key === null ? {} : {'x-viewer-key': key}), ...headers}}, res => {
      const chunks = [];
      res.on('data', c => chunks.push(c));
      res.on('end', () => {
        const bytes = Buffer.concat(chunks);
        resolve({status: res.statusCode, headers: res.headers, bytes, text: bytes.toString('utf8')});
      });
    });
    req.on('error', reject);
    if (body !== undefined) req.write(body);
    req.end();
  });
}

const ROUTES = [
  '/api/status', '/api/runs', '/api/runs/proj-alpha/run-analysis-done', '/api/runs/proj-alpha/run-analysis-done/timeline?after=0', '/api/runs/proj-alpha/run-analysis-done/timeline/1',
  '/api/runs/proj-alpha/run-analysis-done/document?name=report', '/api/dossiers', '/api/dossiers/1/document?name=source%3Aprocedure.md', '/api/dossiers/1/download?name=source%3Aprocedure.md'
];
const SECURITY = {'cache-control': 'no-store', 'x-content-type-options': 'nosniff', 'referrer-policy': 'no-referrer'};

test('every route needs the key; a wrong key and a missing key answer 401', async t => {
  const fx = fixture(t);
  const v = await startServer(t, fx);
  const message = 'Open the address the viewer printed in its terminal.';
  for (const route of [...ROUTES, '/api/nothing-here']) {
    const ok = await request(v, route);
    for (const [name, value] of Object.entries(SECURITY)) assert.equal(ok.headers[name], value, `${route} ${name}`);
    for (const key of [null, 'wrong', v.key.slice(0, -1), v.key + '0', v.key.toUpperCase().replace(/[a-f]/gi, 'F'), '']) {
      const r = await request(v, route, {key});
      assert.equal(r.status, 401, `${route} with key ${JSON.stringify(key)}`);
      assert.deepEqual(JSON.parse(r.text), {error: message});
      for (const [name, value] of Object.entries(SECURITY)) assert.equal(r.headers[name], value);
    }
    // A key in the address instead of the header does not count.
    const query = await request(v, route + (route.includes('?') ? '&' : '?') + `key=${v.key}`, {key: null});
    assert.equal(query.status, 401, `${route} with the key in the query`);
    const hash = await request(v, route, {key: null, headers: {'x-api-key': v.key, authorization: `Bearer ${v.key}`, cookie: `key=${v.key}`}});
    assert.equal(hash.status, 401);
  }
  assert.equal((await request(v, '/api/status')).status, 200);
  assert.equal((await request(v, '/api/nothing-here')).status, 404);
  assert.equal(v.key.length, 32);
  assert.match(v.key, /^[0-9a-f]{32}$/);
  assert.equal(v.url, `http://127.0.0.1:${v.port}/#key=${v.key}`);
  // The page is served without the key, holds no data and carries the exact policy of its one script.
  const page = await request(v, '/', {key: null});
  assert.equal(page.status, 200);
  assert.match(page.headers['content-type'], /^text\/html/);
  assert.equal(page.text.split('<script').length, 2, 'exactly one script element');
  const script = Buffer.from(page.text).subarray(Buffer.from(page.text).indexOf('<script>') + 8, Buffer.from(page.text).indexOf('</script>'));
  assert.equal(page.headers['content-security-policy'], `default-src 'none'; script-src 'sha256-${crypto.createHash('sha256').update(script).digest('base64')}'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'`);
  assert.doesNotMatch(page.headers['content-security-policy'], /unsafe-eval|unsafe-hashes/);
  for (const [name, value] of Object.entries(SECURITY)) assert.equal(page.headers[name], value);
  assert.ok(!page.text.includes('Alpha Project') && !page.text.includes(fx.base) && !page.text.includes(v.key));
  assert.equal(pagePolicy(Buffer.from('<p>no script</p>')).includes("script-src 'none'"), true);
  assert.equal(pagePolicy(Buffer.from('<script>a</script><script>b</script>')).includes("script-src 'none'"), true);
  assert.equal((await request(v, '/favicon.ico', {key: null})).status, 404);
  const hosts = await request(v, '/', {key: null});
  assert.equal(hosts.status, 200);
});

test('only GET is served and only for the host 127.0.0.1', async t => {
  const fx = fixture(t);
  const v = await startServer(t, fx);
  for (const method of ['POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS', 'HEAD']) {
    for (const route of ['/api/runs', '/', '/api/dossiers/1/download?name=x']) {
      const r = await request(v, route, {method});
      assert.equal(r.status, 405, `${method} ${route}`);
      assert.equal(r.headers.allow, 'GET');
      if (method !== 'HEAD') assert.deepEqual(JSON.parse(r.text), {error: 'Only GET is served.'});
    }
  }
  // A body that is announced is not read: the answer comes and the connection is cut, or it is reset.
  for (const method of ['POST', 'PUT']) {
    let status = null;
    try {
      status = (await request(v, '/api/runs', {method, body: 'x'.repeat(10000), headers: {'content-type': 'application/json'}})).status;
    } catch (error) {
      assert.match(error.code, /ECONNRESET|EPIPE/);
    }
    assert.ok(status === null || status === 405);
  }
  assert.equal((await request(v, '/api/status', {headers: {'content-length': '3'}, body: 'abc'}).catch(() => ({status: 400}))).status, 400);
  // The host must be exactly 127.0.0.1 and the port of this server.
  for (const host of ['localhost:' + v.port, `127.0.0.1:${v.port + 1}`, '127.0.0.1', 'evil.example', `evil.example:${v.port}`, `127.0.0.1.evil.example:${v.port}`, `[::1]:${v.port}`]) {
    for (const route of ['/', '/api/status', '/api/runs']) {
      const r = await request(v, route, {host});
      assert.equal(r.status, 400, `host ${JSON.stringify(host)} ${route}`);
      assert.ok(!r.text.includes(SENTINEL) && !r.text.includes('proj-alpha'));
    }
  }
  assert.equal((await request(v, '/api/runs', {method: 'POST', host: 'evil.example'}).catch(() => ({status: 400}))).status, 400);
  // An empty Host and a request with no Host at all (a raw socket, since the client library adds its own) are refused.
  const raw = text => new Promise((resolve, reject) => {
    const socket = net.connect(v.port, '127.0.0.1', () => socket.write(text));
    let out = '';
    socket.on('data', c => { out += c; });
    socket.on('close', () => resolve(out));
    socket.on('error', reject);
  });
  assert.match(await raw('GET /api/status HTTP/1.1\r\nHost:\r\nConnection: close\r\n\r\n'), /^HTTP\/1\.1 400 /);
  assert.match(await raw('GET /api/status HTTP/1.0\r\n\r\n'), /^HTTP\/1\.[01] 400 /);
  // An address that tries to name another authority is refused.
  assert.equal((await request(v, '//evil.example/api/status')).status, 400);
  const bad = await request(v, '/api/runs/proj-alpha/run-analysis-done/timeline?after=abc');
  assert.deepEqual([bad.status, JSON.parse(bad.text).error], [400, 'after must be a whole number.']);
  assert.equal((await request(v, '/api/runs/proj-alpha/run-analysis-done/timeline/abc')).status, 404);
  // The server listens on 127.0.0.1 only.
  assert.equal(v.server.address().address, '127.0.0.1');
  assert.ok(v.port > 0 && v.port !== 8787 && v.port !== 8790);
});

test('the data folder is byte for byte and time for time the same after every route was read', async t => {
  const fx = fixture(t);
  const v = await startServer(t, fx);
  // Manifest of every path, type, size, mode, inode, link count, modification time in nanoseconds and SHA-256.
  const before = {data: treeManifest(fx.data), bench: treeManifest(fx.bench), tmp: treeManifest(fx.tmp)};
  assert.ok(before.data.length > 150 && before.bench.length > 20);
  const first = await crawl(`http://127.0.0.1:${v.port}`, v.key);
  const second = await crawl(`http://127.0.0.1:${v.port}`, v.key);
  const after = {data: treeManifest(fx.data), bench: treeManifest(fx.bench), tmp: treeManifest(fx.tmp)};
  for (const name of ['data', 'bench', 'tmp']) assert.deepEqual(diff(before[name], after[name]), {removed: [], added: []}, name);
  assert.deepEqual(after, before);
  // Reading twice gives the same answers.
  assert.equal(first.size, second.size);
  for (const [route, value] of first) assert.equal(second.get(route), value, route);
  // The crawl was not vacuous: it opened steps, documents, registers, kept files and dossiers.
  const routes = [...first.keys()];
  for (const pattern of [/\/timeline\/\d+/, /name=register%3Afindings/, /name=finding%3AF-001/, /name=kept%3Anotes/, /name=source%3A/, /\/api\/dossiers\/6\/download/, /name=transcript/, /name=progress/]) {
    assert.ok(routes.some(r => pattern.test(r)), String(pattern));
  }
  assert.ok([...first.values()].every(a => a.startsWith('200:')), 'every route the pages offer answers 200');
  // Also with the manifest mechanism itself checked: a change is seen.
  fs.utimesSync(path.join(fx.data, 'projects/proj-alpha/project.md'), new Date(1e12), new Date(1e12));
  assert.notDeepEqual(treeManifest(fx.data), before.data);
});

test('every route answers the same when the data folder and the dossiers are read-only for their owner', async t => {
  const fx = fixture(t);
  const normal = await startServer(t, fx);
  const answers = await crawl(`http://127.0.0.1:${normal.port}`, normal.key);
  await normal.close();
  makeReadOnly(fx.data);
  makeReadOnly(fx.bench);
  t.after(() => { makeRemovable(fx.data); makeRemovable(fx.bench); });
  assert.throws(() => fs.writeFileSync(path.join(fx.data, 'projects/proj-alpha/probe.txt'), 'x'), /EACCES|EPERM/, 'the data folder is read-only for its owner');
  const locked = await startServer(t, fx);
  const again = await crawl(`http://127.0.0.1:${locked.port}`, locked.key);
  assert.equal(again.size, answers.size);
  for (const [route, value] of answers) assert.equal(again.get(route), value, route);
  assert.ok(answers.size > 100);
});

test('a link inside the data folder is not followed by any route', async t => {
  const fx = fixture(t);
  const outside = fs.mkdtempSync(path.join(realTmp, 'pv-outside-'));
  t.after(() => fs.rmSync(outside, {recursive: true, force: true}));
  fs.writeFileSync(path.join(outside, 'trace.jsonl'), SENTINEL + '\n');
  const analyses = path.join(fx.data, 'projects/proj-alpha/analyses/run-analysis-done');
  fs.rmSync(path.join(analyses, 'report.md'));
  fs.symlinkSync('/etc/hosts', path.join(analyses, 'report.md'));
  fs.rmSync(path.join(analyses, 'sources'), {recursive: true});
  fs.symlinkSync(outside, path.join(analyses, 'sources'));
  const traces = path.join(fx.data, 'projects/proj-alpha/traces');
  fs.renameSync(traces, traces + '-moved');
  fs.symlinkSync(outside, traces);
  const v = await startServer(t, fx);
  const answers = await crawl(`http://127.0.0.1:${v.port}`, v.key);
  for (const [route, value] of answers) {
    assert.ok(!value.includes(SENTINEL), route);
    assert.ok(!/localhost|127\.0\.0\.1\s+localhost/.test(value) || route.includes('/api/status'), route);
  }
  const runs = JSON.parse((await request(v, '/api/runs')).text);
  assert.ok(runs.every(r => r.traced === false), 'with `traces` replaced by a link, no run has a trace');
  assert.equal((await request(v, '/api/runs/proj-alpha/run-analysis-done/document?name=report')).status, 404);
  assert.equal((await request(v, '/api/runs/proj-alpha/run-analysis-done/document?name=source%3Adoc-a')).status, 404);
  assert.equal((await request(v, '/api/runs/proj-alpha/run-check-done/document?name=kept%3Anotes%2Fagent-notes.md')).status, 404, 'the kept copy lay below the linked folder');
});

test('the six dossiers are listed; the keys folders beside fixtures are never opened', async t => {
  const fx = fixture(t);
  const v = await startServer(t, fx);
  const list = JSON.parse((await request(v, '/api/dossiers')).text);
  assert.deepEqual(list.map(d => [d.n, d.id]), [1, 2, 3, 4, 5, 6].map(n => [n, `book-00${n}`]));
  for (const d of list) {
    assert.deepEqual(d.sources.map(s => s.name), ['minute-book-1.md', 'minute-book-2.md', 'minute-book-3.md', 'minute-book-4.md', 'procedure.md']);
    assert.equal(d.reports.length, 2);
    assert.deepEqual(d.reports.map(r => r.sha12), d.reports.map(r => r.sha12).sort(), 'reports are in the order of their digest, not of their names');
    for (const r of d.reports) assert.equal(r.neutralName, `report-${r.sha12}.md`);
  }
  // The answer holds no report file name, no label of a report and no key.
  const text = (await request(v, '/api/dossiers')).text;
  assert.doesNotMatch(text, /correct|mixed/i);
  assert.ok(!text.includes(SENTINEL) && !text.includes('keys-'));
  // The keys folders (mode 000, sentinel) are outside every root: nothing a request names reaches them.
  for (const name of ['source:../keys-sealed/book-001.json', 'source:../../keys-draft/book-001.json', 'source:keys-sealed', 'report:../keys-sealed', 'report:000000000000', 'source:report-correct.md', 'source:']) {
    for (const kind of ['document', 'download']) {
      const r = await request(v, `/api/dossiers/1/${kind}?name=${encodeURIComponent(name)}`);
      assert.equal(r.status, 404, `${kind} ${name}`);
      assert.ok(!r.text.includes(SENTINEL));
    }
  }
  for (const route of ['/api/dossiers/7/document?name=source%3Aprocedure.md', '/api/dossiers/0/download?name=x', '/api/dossiers/-1/download', '/api/dossiers/x', '/api/dossiers/1/other']) assert.equal((await request(v, route)).status, 404, route);
  assert.equal((await request(v, '/api/dossiers/1/document')).status, 404);
  assert.equal((await request(v, '/api/dossiers/1/document?name=source%3Aprocedure.md&part=9')).status, 404);
  const all = await crawl(`http://127.0.0.1:${v.port}`, v.key);
  for (const [route, value] of all) assert.ok(!value.includes(SENTINEL), route);
  // A dossier path with a keys segment stops the server, also through a link whose real path has one.
  for (const folder of fx.keysFolders) {
    await assert.rejects(startViewer({settings: settingsOf(fx, {dossiers: [folder]})}), /answer keys or evaluator/);
  }
  const link = path.join(fx.base, 'innocent-name');
  fs.symlinkSync(fx.keysFolders[0], link);
  await assert.rejects(startViewer({settings: settingsOf(fx, {dossiers: [fx.dossiers[0], link]})}), /answer keys or evaluator/);
  const evaluator = path.join(fx.base, 'evaluator', 'book');
  fs.mkdirSync(evaluator, {recursive: true});
  await assert.rejects(startViewer({settings: settingsOf(fx, {dossiers: [evaluator]})}), /answer keys or evaluator/);
  const child = spawnSync(process.execPath, [path.join(tools, 'process-viewer.mjs')], {env: childEnv(t, fx, {VIEWER_DOSSIERS: [fx.dossiers[0], fx.keysFolders[1]].join(path.delimiter)}), encoding: 'utf8', timeout: 20000});
  assert.equal(child.status, 1);
  assert.match(child.stderr, /answer keys or evaluator/);
  assert.doesNotMatch(child.stdout, /Process viewer:/);
});

test('a run is linked to a dossier by file hashes; pasted text matches after trimming', async t => {
  const fx = fixture(t);
  const v = await startServer(t, fx);
  const runs = JSON.parse((await request(v, '/api/runs')).text);
  const dossierOf = id => runs.find(r => r.key === `proj-alpha/${fx.ids[id]}`).dossier;
  assert.deepEqual(dossierOf('analysisDone'), {id: 'book-001', match: 'full', trimmed: false}, 'hashes taken from the trace');
  assert.deepEqual(dossierOf('untracedDone'), {id: 'book-002', match: 'full', trimmed: false}, 'hashes taken from the files, byte for byte');
  assert.deepEqual(dossierOf('untracedPasted'), {id: 'book-003', match: 'full', trimmed: true}, 'pasted text is trimmed, so it matches after trimming');
  assert.deepEqual(dossierOf('checkDone'), {id: 'book-001', match: 'full', trimmed: false});
  assert.deepEqual(dossierOf('imported'), {id: 'book-001', match: 'report', trimmed: false});
  assert.equal(dossierOf('untracedNotes'), null);
  assert.equal(dossierOf('failed'), null);
  // The dossier lists its runs; the check's report matches one report by digest, never by name.
  const list = JSON.parse((await request(v, '/api/dossiers')).text);
  const book1 = list[0];
  const mixed = sha256(bookTexts(1).reports['report-mixed.md']).slice(0, 12);
  const correct = sha256(bookTexts(1).reports['report-correct.md']).slice(0, 12);
  // The procedure file is the same in every book, so a run that holds another book's files also matches this dossier in part.
  const matchOf = (d, id) => d.runs.find(r => r.key === `proj-alpha/${fx.ids[id]}`)?.match;
  assert.deepEqual(book1.runs.map(r => [r.key.split('/')[1], r.match]).sort(), [['run-analysis-done', 'full'], ['run-check-done', 'full'], ['run-imported', 'report'], ['run-untraced-done', 'partial'], ['run-untraced-pasted', 'partial']]);
  assert.equal(matchOf(list[1], 'untracedDone'), 'full');
  assert.equal(book1.runs.find(r => r.key.endsWith('run-check-done')).report, mixed);
  assert.equal(book1.runs.find(r => r.key.endsWith('run-imported')).report, correct);
  assert.deepEqual(list[2].runs.find(r => r.key.endsWith('run-untraced-pasted')), {key: 'proj-alpha/run-untraced-pasted', match: 'full', trimmed: true, report: null});
  // A run page says which dossier file each input is.
  const page = JSON.parse((await request(v, `/api/runs/proj-alpha/${fx.ids.checkDone}`)).text);
  assert.ok(page.inputs.some(i => i.kind === 'document' && i.note === 'Matches dossier book-001, source minute-book-1.md'));
  assert.ok(page.inputs.some(i => i.kind === 'report-under-review' && i.note === `Matches dossier book-001, report ${mixed}`));
  const pasted = JSON.parse((await request(v, `/api/runs/proj-alpha/${fx.ids.untracedPasted}`)).text);
  assert.ok(pasted.inputs.some(i => /after trimming$/.test(i.note ?? '')));
  // One document matches only: the link is partial.
  const partial = path.join(fx.data, 'projects/proj-alpha/analyses/run-untraced-notes/sources');
  fs.writeFileSync(path.join(partial, 'doc-a.md'), bookTexts(4).sources['minute-book-2.md']);
  const again = JSON.parse((await request(v, '/api/runs')).text);
  assert.deepEqual(again.find(r => r.key.endsWith('run-untraced-notes')).dossier, {id: 'book-004', match: 'partial', trimmed: false});
  // The name told the agent which report this is: a title or an original name with correct or mixed.
  const named = path.join(fx.data, 'projects/proj-alpha/traces/run-named');
  fs.mkdirSync(named, {recursive: true});
  fs.writeFileSync(path.join(named, 'trace.jsonl'), JSON.stringify({seq: 1, at: new Date().toISOString(), type: 'run-start', ...runStartFields({runId: 'run-named', kind: 'quality', createdAt: new Date().toISOString(), overrides: {title: 'Report Mixed Edition'}})}) + '\n');
  const notice = JSON.parse((await request(v, '/api/runs/proj-alpha/run-named')).text).notices;
  assert.ok(notice.some(n => n.text === 'The name told the agent which report this is.'));
  assert.ok(!page.notices.some(n => n.kind === 'name-told'));
});

test('a report downloads under its neutral name and its bytes are unchanged', async t => {
  const fx = fixture(t);
  const v = await startServer(t, fx);
  const list = JSON.parse((await request(v, '/api/dossiers')).text);
  for (const d of list) {
    for (const r of d.reports) {
      const answer = await request(v, `/api/dossiers/${d.n}/download?name=${encodeURIComponent('report:' + r.sha12)}`);
      assert.equal(answer.status, 200);
      assert.match(answer.headers['content-type'], /^text\/markdown/);
      assert.equal(answer.headers['content-disposition'], `attachment; filename="report-${r.sha12}.md"`);
      assert.equal(crypto.createHash('sha256').update(answer.bytes).digest('hex').slice(0, 12), r.sha12);
      assert.equal(answer.bytes.length, r.bytes);
      for (const [name, text] of Object.entries(bookTexts(d.n).reports)) if (sha256(text).startsWith(r.sha12)) assert.deepEqual(answer.bytes, fs.readFileSync(path.join(fx.dossiers[d.n - 1], name)));
      assert.doesNotMatch(answer.headers['content-disposition'], /correct|mixed/);
    }
    const source = await request(v, `/api/dossiers/${d.n}/download?name=${encodeURIComponent('source:procedure.md')}`);
    assert.deepEqual(source.bytes, fs.readFileSync(path.join(fx.dossiers[d.n - 1], 'sources/procedure.md')));
    assert.equal(source.headers['content-disposition'], 'attachment; filename="procedure.md"');
  }
  const doc = JSON.parse((await request(v, `/api/dossiers/1/document?name=${encodeURIComponent('report:' + list[0].reports[0].sha12)}`)).text);
  assert.equal(doc.format, 'markdown');
  assert.deepEqual([doc.part, doc.parts], [0, 1]);
});

test('settings come from the environment and a settings file below tmp, and nothing configured shows Not Connected', async t => {
  const fx = fixture(t);
  const repo = fs.mkdtempSync(path.join(realTmp, 'pv-repo-'));
  t.after(() => fs.rmSync(repo, {recursive: true, force: true}));
  fs.mkdirSync(path.join(repo, 'tmp/process-viewer'), {recursive: true});
  const file = path.join(repo, 'tmp/process-viewer/settings.json');
  const none = loadSettings({env: {}, repoRoot: repo});
  assert.deepEqual(none, {deepProofData: null, dossiers: [], deepProofTmp: null, port: 8790});
  fs.writeFileSync(file, JSON.stringify({deepProofData: fx.data, dossiers: [fx.dossiers[0]], deepProofTmp: fx.tmp, port: 8791}));
  assert.deepEqual(loadSettings({env: {}, repoRoot: repo}), {deepProofData: fx.data, dossiers: [fx.dossiers[0]], deepProofTmp: fx.tmp, port: 8791});
  const env = loadSettings({env: {VIEWER_DEEP_PROOF_DATA: '/x/y', VIEWER_DOSSIERS: ['/a', '/b'].join(path.delimiter), VIEWER_PORT: '0', VIEWER_DEEP_PROOF_TMP: '/z'}, repoRoot: repo});
  assert.deepEqual(env, {deepProofData: '/x/y', dossiers: ['/a', '/b'], deepProofTmp: '/z', port: 0});
  for (const bad of [{VIEWER_PORT: '70000'}, {VIEWER_PORT: 'abc'}, {VIEWER_PORT: '1.5'}, {VIEWER_SETTINGS: '/etc/hosts'}, {VIEWER_SETTINGS: path.join(repo, 'tmp/../outside.json')}, {VIEWER_SETTINGS: path.join(repo, 'tmp/process-viewer/missing.json')}]) {
    assert.throws(() => loadSettings({env: bad, repoRoot: repo}), Error, JSON.stringify(bad));
  }
  fs.writeFileSync(file, '{not json');
  assert.throws(() => loadSettings({env: {}, repoRoot: repo}), /not valid JSON/);
  // A relative folder is refused by the gate; nothing configured starts a server that shows Not Connected.
  await assert.rejects(startViewer({settings: {deepProofData: 'relative/path', dossiers: [], port: 0}}), /absolute/);
  const v = await startServer(t, fx, {settings: {deepProofData: null, dossiers: [], deepProofTmp: fx.tmp, port: 0}});
  const status = JSON.parse((await request(v, '/api/status')).text);
  assert.deepEqual([status.connected, status.dossiersConfigured, status.pollMs, status.tmpMatches], [false, 0, 3000, null]);
  assert.match(status.settingsHint, /"deepProofData": "<absolute path of the Deep Proof data folder>"/);
  assert.deepEqual(JSON.parse((await request(v, '/api/runs')).text), []);
  assert.deepEqual(JSON.parse((await request(v, '/api/dossiers')).text), []);
  assert.equal((await request(v, '/api/runs/proj-alpha/run-analysis-done')).status, 404);
  // Connected: tmpMatches says whether the work folders of the traces lie in the configured temporary folder.
  const connected = await startServer(t, fx);
  const ok = JSON.parse((await request(connected, '/api/status')).text);
  assert.deepEqual([ok.connected, ok.dossiersConfigured, ok.settingsHint], [true, 6, null]);
  assert.equal(ok.tmpMatches, true);
  const other = fs.mkdtempSync(path.join(realTmp, 'pv-other-tmp-'));
  t.after(() => fs.rmSync(other, {recursive: true, force: true}));
  const mismatched = await startServer(t, fx, {settings: settingsOf(fx, {deepProofTmp: other})});
  assert.equal(JSON.parse((await request(mismatched, '/api/status')).text).tmpMatches, false);
});

test('a 70 MiB trace and a 2 MiB line are read in parts within the memory bound', async t => {
  const fx = fixture(t);
  // The 70 MiB trace is written below the fixture folder (the operating system's temporary folder), which the test removes.
  const dir = path.join(fx.data, 'projects/proj-alpha/traces/run-big');
  fs.mkdirSync(dir, {recursive: true});
  const file = path.join(dir, 'trace.jsonl');
  const fd = fs.openSync(file, 'w');
  let seq = 0;
  const put = (type, fields) => fs.writeSync(fd, JSON.stringify({seq: ++seq, at: new Date(Date.now() - 20000 + seq).toISOString(), type, ...fields}) + '\n');
  put('run-start', runStartFields({runId: 'run-big', createdAt: new Date(Date.now() - 20000).toISOString()}));
  put('ala-stderr', {turn: '1', text: 'c'.repeat(2 * 1024 * 1024)});
  while (fs.fstatSync(fd).size < 70 * 1024 * 1024) put('ala-stderr', {turn: '1', text: 'a'.repeat(262144)});
  put('progress-note', {turn: '1', text: 'Done.', blocks: 1});
  fs.closeSync(fd);
  t.after(() => fs.rmSync(file, {force: true}));
  const size = fs.statSync(file).size;
  assert.ok(size > 70 * 1024 * 1024, `${size} bytes`);
  // The server runs as a child process, so that its resident memory can be sampled from outside.
  const child = spawn(process.execPath, [path.join(tools, 'process-viewer.mjs')], {env: childEnv(t, fx), stdio: ['ignore', 'pipe', 'pipe']});
  t.after(() => child.kill('SIGKILL'));
  const line = await new Promise((resolve, reject) => {
    let out = '';
    child.stdout.on('data', c => { out += c; const m = /Process viewer: (http:\/\/127\.0\.0\.1:(\d+)\/#key=([0-9a-f]{32}))/.exec(out); if (m) resolve(m); });
    child.on('exit', code => reject(new Error(`the viewer exited with ${code}`)));
    setTimeout(() => reject(new Error('the viewer did not start')), 20000);
  });
  const v = {port: Number(line[2]), key: line[3]};
  const rss = async () => Number((await promisify(execFile)('ps', ['-o', 'rss=', '-p', String(child.pid)])).stdout.trim()) / 1024;
  const baseline = await rss();
  let peak = baseline;
  const sampler = setInterval(() => rss().then(m => { peak = Math.max(peak, m); }, () => {}), 25);
  try {
    const runs = JSON.parse((await request(v, '/api/runs')).text);
    assert.equal(runs.find(r => r.key === 'proj-alpha/run-big').status, 'running');
    let after = 0, entries = [], rounds = 0;
    for (;;) {
      const answer = JSON.parse((await request(v, `/api/runs/proj-alpha/run-big/timeline?after=${after}`)).text);
      assert.ok(answer.entries.length <= 500, 'a request has a cap');
      entries.push(...answer.entries);
      rounds++;
      if (!answer.more) break;
      after = answer.next;
    }
    assert.ok(entries.length >= 270, `${entries.length} entries`);
    assert.ok(entries.every((e, i) => e.n === i + 1));
    // The 2 MiB line is entry 2: eight parts of 262,144 bytes that join into the whole text.
    const big = JSON.parse((await request(v, '/api/runs/proj-alpha/run-big/timeline/2?part=0')).text);
    assert.equal(big.parts, 8);
    let total = 0;
    for (let k = 0; k < big.parts; k++) total += JSON.parse((await request(v, `/api/runs/proj-alpha/run-big/timeline/2?part=${k}`)).text).text.length;
    assert.equal(total, 2 * 1024 * 1024);
    for (const e of entries.slice(2, 30)) assert.equal(JSON.parse((await request(v, `/api/runs/proj-alpha/run-big/timeline/${e.n}?part=0`)).text).parts, 1);
    assert.equal((await request(v, '/api/runs/proj-alpha/run-big')).status, 200);
    assert.equal((await request(v, '/api/status')).status, 200);
    await sleep(100);
  } finally {
    clearInterval(sampler);
  }
  peak = Math.max(peak, await rss());
  t.diagnostic(`file ${(size / 1048576).toFixed(1)} MiB; resident memory of the viewer: ${baseline.toFixed(0)} MiB at start, peak ${peak.toFixed(0)} MiB, growth ${(peak - baseline).toFixed(0)} MiB`);
  assert.ok(peak - baseline < 200, `resident memory grew by ${(peak - baseline).toFixed(0)} MiB`);
});

test('the read-only check tool reports an unchanged manifest on a fixture and finds a change', async t => {
  const fx = fixture(t);
  const repo = fs.mkdtempSync(path.join(realTmp, 'pv-repo-'));
  t.after(() => fs.rmSync(repo, {recursive: true, force: true}));
  const run = args => spawnSync(process.execPath, [path.join(tools, 'check-viewer-readonly.mjs'), ...args], {env: childEnv(t, fx), encoding: 'utf8', timeout: 60000});
  const ok = run([]);
  assert.equal(ok.status, 0, ok.stdout + ok.stderr);
  assert.match(ok.stdout, /Manifest unchanged\./);
  assert.match(ok.stdout, /second read gives the same answers: yes/);
  assert.equal(run(['--bogus']).status, 2);
  // The manifest covers projects/ only: the database files beside it are not part of it.
  assert.ok(treeManifest(path.join(fx.data, 'projects')).every(line => !line.includes('analyst')));
  // Exit 2 means a named problem each time: an explicit settings file that is not there, one outside tmp/, and no data folder.
  const check = env => spawnSync(process.execPath, [path.join(tools, 'check-viewer-readonly.mjs')], {env: childEnv(t, fx, {VIEWER_DEEP_PROOF_DATA: '', VIEWER_DEEP_PROOF_TMP: '', VIEWER_DOSSIERS: '', ...env}), encoding: 'utf8', timeout: 60000});
  const missing = check({VIEWER_SETTINGS: path.join(repoRoot, 'tmp', 'pv-no-such-settings', 'settings.json')});
  assert.equal(missing.status, 2);
  assert.match(missing.stderr, /The settings file cannot be read\./);
  const outside = check({VIEWER_SETTINGS: path.join(repo, 'tmp/none.json')});
  assert.equal(outside.status, 2);
  assert.match(outside.stderr, /The settings file must lie below the repository tmp\/ folder\./);
  const empty = check({VIEWER_SETTINGS: pinnedSettings(t, fx, {}), VIEWER_DOSSIERS: ''});
  assert.equal(empty.status, 2);
  assert.match(empty.stderr, /No Deep Proof data folder is set\./);
  assert.equal(run(['--bogus']).status, 2);
  assert.match(run(['--bogus']).stderr, /Usage: node tools\/check-viewer-readonly\.mjs/);
});

test('every child viewer is pinned to fixture settings, and no test writes where the real settings file lives', () => {
  // Static check of this file and of the fixture builder. The place of the user's own settings file is named in two
  // pieces here, so that this check does not match itself.
  const place = 'tmp/' + 'process-viewer';
  const split = "'process-viewer', " + "'settings";
  const source = fs.readFileSync(fileURLToPath(import.meta.url), 'utf8');
  const fixtureSource = fs.readFileSync(path.join(tools, 'process-viewer-fixture.mjs'), 'utf8');
  // 1. Each place that starts the viewer or its check as a child process builds its environment with childEnv(, so
  // that none can fall back to the default settings file.
  const starts = [...source.matchAll(/spawn(?:Sync)?\(process\.execPath, \[path\.join\(tools, '([a-z-]+\.mjs)'\)[^\n]*/g)];
  assert.ok(starts.length >= 4, `found ${starts.length} places that start a viewer script`);
  for (const m of starts) assert.ok(m[0].includes('childEnv('), `${m[1]} is started without childEnv(: ${m[0].slice(0, 120)}`);
  // 2. The place of the real settings file is named only through the temporary repository root (a folder of this test),
  // (also through the `write` helper of the test below, which joins it with that root) and the fixture builder never names it.
  for (const line of source.split('\n')) {
    if (line.includes(place) || line.includes(split)) assert.ok(/path\.join\(repo, |^\s*write\('/.test(line), `a line names the real settings place: ${line.trim().slice(0, 120)}`);
  }
  assert.ok(!fixtureSource.includes(place) && !fixtureSource.includes('settings.json'));
  // No write call joins the worktree root with that place.
  assert.doesNotMatch(source, /(?:writeFileSync|mkdirSync|rmSync|rmdirSync|mkdtempSync|renameSync|symlinkSync|appendFileSync)\([^\n]*repoRoot[^\n]*process-viewer'/);
});

test('an explicit settings file wins over the default file, and the default file of a temporary root is read without it', t => {
  const repo = fs.mkdtempSync(path.join(realTmp, 'pv-repo-'));
  t.after(() => fs.rmSync(repo, {recursive: true, force: true}));
  const write = (rel, value) => {
    fs.mkdirSync(path.dirname(path.join(repo, rel)), {recursive: true});
    fs.writeFileSync(path.join(repo, rel), JSON.stringify(value));
    return path.join(repo, rel);
  };
  write('tmp/process-viewer/settings.json', {deepProofData: '/decoy/default/data', dossiers: ['/decoy/default/dossier'], port: 8801});
  const explicit = write('tmp/elsewhere/own-settings.json', {deepProofData: '/chosen/data', dossiers: ['/chosen/dossier'], port: 8802});
  // Without VIEWER_SETTINGS the default file of this root is the one read.
  assert.deepEqual(loadSettings({env: {}, repoRoot: repo}), {deepProofData: '/decoy/default/data', dossiers: ['/decoy/default/dossier'], deepProofTmp: null, port: 8801});
  // With it, the named file wins and the default one is not read.
  assert.deepEqual(loadSettings({env: {VIEWER_SETTINGS: explicit}, repoRoot: repo}), {deepProofData: '/chosen/data', dossiers: ['/chosen/dossier'], deepProofTmp: null, port: 8802});
  // Environment values win over both files.
  assert.equal(loadSettings({env: {VIEWER_SETTINGS: explicit, VIEWER_DEEP_PROOF_DATA: '/from/env'}, repoRoot: repo}).deepProofData, '/from/env');
  // A named file that is not below the root's tmp/ is refused, and a named file that is missing is an error, never a fallback.
  assert.throws(() => loadSettings({env: {VIEWER_SETTINGS: path.join(repo, 'outside.json')}, repoRoot: repo}), /below the repository tmp\//);
  assert.throws(() => loadSettings({env: {VIEWER_SETTINGS: path.join(repo, 'tmp/elsewhere/missing.json')}, repoRoot: repo}), /cannot be read/);
});
