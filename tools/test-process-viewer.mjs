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
import {spawnSync} from 'node:child_process';
import * as gate from './pv-read.mjs';
import {createTraceIndex, ViewerError, partOf} from './pv-trace.mjs';
import {createRunIndex, processAlive} from './pv-runs.mjs';
import {createDocuments} from './pv-documents.mjs';
import {buildFixture, removeFixture, makeReadOnly, makeRemovable, SENTINEL, WORK_PREFIX} from './process-viewer-fixture.mjs';

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
const SERVER_MODULES = ['pv-read.mjs', 'pv-trace.mjs', 'pv-runs.mjs', 'pv-documents.mjs'];
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

const GOLDEN = path.join(tools, 'fixtures/deep-proof-trace-v1-sample.jsonl');
test('the golden trace sample gives the expected timeline', {skip: fs.existsSync(GOLDEN) ? false : 'golden sample not delivered yet'}, t => {
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
