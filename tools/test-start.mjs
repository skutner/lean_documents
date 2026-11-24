#!/usr/bin/env node
/** Model-free tests of the launcher behind `npm run start` (tools/start.mjs).
 *
 *   node --test tools/test-start.mjs
 *
 * Every test builds a throwaway world in the host temporary folder: a fake Deep Proof checkout (a tiny server.mjs that
 * serves a page titled "Deep Proof"), a fake build script, free ports, an injected opener and an injected command
 * runner. Nothing here touches port 8000 or 8790, the real Deep Proof checkout, the real books or a model. The helper
 * that reads another process is fed made-up text, never a real process. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {spawn} from 'node:child_process';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {start, StartError, StartAborted, deepProofEnvironment, inspectProcess, classifyDeepProofPort, listenerPid} from './start.mjs';
import {startViewer} from './process-viewer.mjs';
import {TraceBuilder, runStartFields} from './process-viewer-fixture.mjs';

const tools = path.dirname(fileURLToPath(import.meta.url));
const realTmp = fs.realpathSync(os.tmpdir());
const FAST = {probeMs: 300, startMs: 8000, killMs: 1500, lockMs: 8000, pollMs: 40};
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const COMMIT = 'e36c1d7b16dc39d073a399eef7806c14e0b228c8';

const FAKE_SERVER = `import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
if (process.env.FAKE_EXIT) { console.error('boom-from-the-fake-server'); process.exit(Number(process.env.FAKE_EXIT)); }
fs.appendFileSync('spawns.log', process.pid + '\\n');
const dir = path.resolve(process.env.ANALYST_DATA_DIR || '.runtime');
fs.mkdirSync(path.join(dir, 'projects'), {recursive: true});
http.createServer((req, res) => {
  if (req.url === '/__env') {
    res.setHeader('content-type', 'application/json');
    return res.end(JSON.stringify({trace: process.env.DEEP_PROOF_TRACE ?? null, port: process.env.PORT ?? null, host: process.env.HOST ?? null, cert: process.env.SSL_CERT_FILE ?? null, pid: process.pid, cwd: process.cwd(), npm: process.env.npm_lifecycle_event ?? null, path: process.env.PATH ?? null}));
  }
  res.setHeader('content-type', 'text/html');
  res.end('<!doctype html><title>Deep Proof</title>');
}).listen(Number(process.env.PORT), process.env.HOST);
`;

const FAKE_BUILD = `import fs from 'node:fs';
import path from 'node:path';
const argv = process.argv.slice(2);
const data = argv[argv.indexOf('--data') + 1];
fs.mkdirSync(data, {recursive: true});
fs.appendFileSync(path.join(data, 'builds.log'), JSON.stringify(argv.map(a => (a.startsWith('/') ? 'ABS' : a))) + '\\n');
const build = path.join(data, 'build');
if (process.env.FAKE_BUILD_FAIL) {
  fs.mkdirSync(path.join(build, '.minutes-edition-8-cafe', 'keys'), {recursive: true});
  console.error('the generator failed (exit 2)');
  process.exit(1);
}
if (process.env.FAKE_BUILD_GRANDCHILD) {
  const {spawn} = await import('node:child_process');
  const late = "const fs=require('fs'),path=require('path');const d=process.argv[1];fs.writeFileSync(path.join(d,'grandchild.pid'),String(process.pid));setTimeout(()=>{fs.mkdirSync(path.join(d,'build','minutes-edition-8','keys'),{recursive:true})},1500)";
  spawn(process.execPath, ['-e', late, data], {stdio: 'ignore'});
}
if (process.env.FAKE_BUILD_SLEEP) await new Promise(resolve => setTimeout(resolve, Number(process.env.FAKE_BUILD_SLEEP)));
const target = path.join(build, 'minutes-edition-8');
for (let n = 1; n <= 6; n++) {
  const dossier = path.join(target, 'fixtures', 'g82829258-00' + n + '-minutes');
  fs.mkdirSync(path.join(dossier, 'sources'), {recursive: true});
  fs.writeFileSync(path.join(dossier, 'sources', 'minutes-' + n + '.md'), '# Minutes ' + n + '\\n');
  fs.writeFileSync(path.join(dossier, 'report-correct.md'), 'Report one of book ' + n + '\\n');
  fs.writeFileSync(path.join(dossier, 'report-mixed.md'), 'Report two of book ' + n + '\\n');
}
fs.mkdirSync(path.join(target, 'keys'), {recursive: true});
fs.writeFileSync(path.join(target, 'keys', 'k.json'), '{}');
fs.writeFileSync(path.join(target, 'keys.generation.json'), '{}');
fs.mkdirSync(path.join(build, '.minutes-edition-8-deadbeef', 'keys'), {recursive: true});
console.log('OK 6 dossiers');
`;

/** A throwaway world: <base>/lean (the repository root of the launcher) and <base>/deep-proof-community-edition (fake). */
function world(t) {
  const base = fs.mkdtempSync(path.join(realTmp, 'start-test-'));
  const repo = path.join(base, 'lean');
  const dp = path.join(base, 'deep-proof-community-edition');
  fs.mkdirSync(repo, {recursive: true});
  const book = path.join(dp, 'tests', 'evaluation', 'minute-books');
  fs.mkdirSync(book, {recursive: true});
  fs.writeFileSync(path.join(dp, 'package.json'), JSON.stringify({name: 'deep-proof', engines: {node: '>=24'}}));
  fs.writeFileSync(path.join(dp, 'server.mjs'), FAKE_SERVER);
  fs.writeFileSync(path.join(book, 'build.mjs'), FAKE_BUILD);
  fs.writeFileSync(path.join(book, 'selection.json'), JSON.stringify({id: 'minutes-edition-8', generator: {commit: COMMIT}}));
  const w = {base, repo, dp, started: [], logs: [], opened: []};
  t.after(async () => {
    for (const s of w.started) { try { await s.stop(); } catch { /* already stopped */ } }
    for (const pid of w.pids ?? []) { try { process.kill(pid, 'SIGKILL'); } catch { /* gone */ } }
    fs.rmSync(base, {recursive: true, force: true});
  });
  return w;
}

function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.once('error', reject);
    s.listen(0, '127.0.0.1', () => { const {port} = s.address(); s.close(() => resolve(port)); });
  });
}

/** The one place a test starts the launcher. It refuses anything that could reach a real port, the real checkout or a real command. */
async function launch(w, options) {
  assert.ok(w.repo.startsWith(realTmp + path.sep), 'the repository root is a temporary folder');
  assert.ok(Number.isInteger(options.deepProofPort) && options.deepProofPort > 0 && ![8000, 8790].includes(options.deepProofPort), 'an explicit Deep Proof port that is not 8000');
  const viewerPort = Number(options.env?.VIEWER_PORT);
  assert.ok(Number.isInteger(viewerPort) && viewerPort > 0 && ![8000, 8790].includes(viewerPort), 'an explicit viewer port that is not 8790');
  assert.equal(typeof options.opener, 'function', 'an injected opener');
  assert.equal(typeof options.run, 'function', 'an injected command runner');
  const result = await start({repoRoot: w.repo, open: false, log: line => w.logs.push(line), platform: 'darwin', python: () => null, timeouts: FAST, ...options});
  w.started.push(result);
  return result;
}

/** A command runner that knows nothing: every command fails. */
const noRun = () => ({status: 1, stdout: ''});

async function ports(t) {
  return {deepProofPort: await freePort(), viewerPort: await freePort()};
}
const envFor = (viewerPort, extra = {}) => ({PATH: process.env.PATH, VIEWER_PORT: String(viewerPort), ...extra});
const getJson = async (port, route) => (await fetch(`http://127.0.0.1:${port}${route}`)).json();
const alive = pid => { try { process.kill(pid, 0); return true; } catch { return false; } };
async function refused(port) { return (await classifyDeepProofPort(port, 300)) === 'free'; }

/** A Deep Proof that this test started by hand, in its own process, so that the launcher finds it held. */
async function heldServer(w, port, {trace, extraEnv = {}} = {}) {
  const env = {PATH: process.env.PATH, PORT: String(port), HOST: '127.0.0.1', ...(trace ? {DEEP_PROOF_TRACE: '1'} : {}), ...extraEnv};
  const child = spawn(process.execPath, ['server.mjs'], {cwd: w.dp, env, stdio: 'ignore'});
  (w.pids ??= []).push(child.pid);
  const until = Date.now() + 8000;
  while ((await classifyDeepProofPort(port, 300)) !== 'deepproof') {
    if (Date.now() > until) throw new Error('the held fake server did not start');
    await sleep(50);
  }
  return child;
}

/** A command runner that answers like lsof and ps would, from made-up text. */
function madeUpRun({port, pid, environment, cwd}) {
  return (command, args) => {
    if (command === 'lsof' && args.some(a => a === `-iTCP:${port}`)) return {status: 0, stdout: `p${pid}\n`};
    if (command === 'lsof' && args.includes('cwd')) return {status: 0, stdout: `p${pid}\nfcwd\nn${cwd}\n`};
    if (command === 'ps') return {status: 0, stdout: `node server.mjs ${environment}\n`};
    return {status: 1, stdout: ''};
  };
}

// ---------- Tests ----------

test('the checkout is the sibling folder, DEEP_PROOF_DIR overrides it (also with a space), and a bad one is refused before anything starts', async t => {
  const w = world(t);
  const {deepProofPort, viewerPort} = await ports(t);
  const first = await launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: noRun});
  assert.equal((await getJson(deepProofPort, '/__env')).cwd, w.dp, 'the sibling checkout was used');
  await first.stop();
  // An override with a space in its path.
  const spaced = path.join(w.base, 'my deep proof');
  fs.cpSync(w.dp, spaced, {recursive: true});
  const second = await launch(w, {deepProofPort, env: envFor(viewerPort, {DEEP_PROOF_DIR: spaced}), opener: () => {}, run: noRun});
  assert.equal((await getJson(deepProofPort, '/__env')).cwd, spaced);
  await second.stop();
  // Refusals: one plain line each, nothing started.
  fs.rmSync(path.join(w.repo, 'tmp'), {recursive: true, force: true});
  const wrong = path.join(w.base, 'other-package');
  fs.mkdirSync(wrong);
  fs.writeFileSync(path.join(wrong, 'package.json'), JSON.stringify({name: 'something-else'}));
  fs.writeFileSync(path.join(wrong, 'server.mjs'), FAKE_SERVER);
  for (const [extra, extraOptions, pattern] of [[{DEEP_PROOF_DIR: path.join(w.base, 'nonexistent')}, {}, /Deep Proof was not found in .*nonexistent\. Set DEEP_PROOF_DIR/], [{DEEP_PROOF_DIR: wrong}, {}, /Deep Proof was not found/], [{}, {nodeVersion: '23.9.0'}, /needs Node\.js 24 or later; this is 23\.9\.0/]]) {
    await assert.rejects(start({repoRoot: w.repo, deepProofPort, env: envFor(viewerPort, extra), open: false, log: () => {}, opener: () => {}, run: noRun, platform: 'darwin', timeouts: FAST, ...extraOptions}), error => error instanceof StartError && pattern.test(error.message) && !error.message.includes('\n'));
  }
  assert.ok(!fs.existsSync(path.join(w.repo, 'tmp')), 'nothing was written: no log');
  assert.ok(await refused(deepProofPort) && await refused(viewerPort), 'nothing listens');
});

test('a free Deep Proof port starts the server with recording on, in its own folder, logs to a file, and stop() ends only what was started', async t => {
  const w = world(t);
  const {deepProofPort, viewerPort} = await ports(t);
  const opened = [];
  const r = await launch(w, {deepProofPort, env: envFor(viewerPort), opener: url => opened.push(url), run: noRun, open: true});
  const env = await getJson(deepProofPort, '/__env');
  assert.deepEqual([env.trace, env.port, env.host], ['1', String(deepProofPort), '127.0.0.1']);
  assert.equal(env.cert, fs.existsSync('/etc/ssl/cert.pem') ? '/etc/ssl/cert.pem' : null, 'the system certificates are named on macOS');
  assert.equal(r.deepProof.startedHere, true);
  assert.equal(r.deepProof.recording, true);
  assert.equal(r.deepProof.pid, env.pid);
  assert.deepEqual(opened, [`http://127.0.0.1:${viewerPort}/`], 'the browser opens the plain address, once');
  const status = await getJson(viewerPort, '/api/status');
  assert.deepEqual(status.deepProof, {url: `http://127.0.0.1:${deepProofPort}/`, recording: true, startedHere: true});
  assert.equal(status.connected, true);
  assert.equal(status.folders.data, path.join(w.dp, '.runtime'), 'the data folder of the server it started');
  assert.ok(fs.existsSync(path.join(w.repo, 'tmp', 'deep-proof-server.log')));
  // stop() is safe to call twice and ends both.
  const [a, b] = [r.stop(), r.stop()];
  assert.strictEqual(a, b);
  await a;
  assert.equal(await r.finished, 0);
  assert.equal(alive(env.pid), false, 'the server it started is gone');
  assert.ok(await refused(deepProofPort) && await refused(viewerPort));
});

test('a Deep Proof that is already running is read, never stopped: recording ON with the data folder of that server', async t => {
  const w = world(t);
  const {deepProofPort, viewerPort} = await ports(t);
  const held = await heldServer(w, deepProofPort, {trace: true});
  const dataDir = path.join(w.base, 'held data');
  fs.mkdirSync(path.join(dataDir, 'projects'), {recursive: true});
  const SECRET = 'SECRET_TOKEN=hunter2';
  const run = madeUpRun({port: deepProofPort, pid: held.pid, cwd: w.dp, environment: `PATH=/usr/bin ${SECRET} DEEP_PROOF_TRACE=1 ANALYST_DATA_DIR=${dataDir.replace(/ /g, '\\ ')}`});
  // A folder with a space cannot be read from ps output: the launcher falls back and prints the folder it uses.
  const r = await launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run});
  assert.equal(r.deepProof.startedHere, false);
  assert.equal(r.deepProof.recording, true);
  assert.equal(r.deepProof.pid, held.pid);
  assert.equal((await getJson(viewerPort, '/api/status')).folders.data, path.join(w.dp, '.runtime'), 'a folder that cannot be read from ps output falls back to the checkout .runtime');
  assert.ok(w.logs.some(l => /Deep Proof data folder: .*\.runtime/.test(l)), 'and the folder used is printed');
  assert.ok(w.logs.some(l => /recording ON/.test(l)));
  assert.ok(!JSON.stringify(w.logs).includes('hunter2') && !JSON.stringify(r.deepProof).includes('hunter2'), 'no value of the environment is printed');
  await r.stop();
  assert.equal(alive(held.pid), true, 'a Deep Proof the command did not start is never stopped');
  assert.equal((await getJson(deepProofPort, '/__env')).pid, held.pid);
  assert.ok(!fs.existsSync(path.join(w.repo, 'tmp', 'deep-proof-server.log')), 'nothing was spawned');
  // Without a space in the path the server's own folder is read.
  const plain = path.join(w.base, 'held-data');
  fs.mkdirSync(path.join(plain, 'projects'), {recursive: true});
  const again = await launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: madeUpRun({port: deepProofPort, pid: held.pid, cwd: w.dp, environment: `PATH=/usr/bin DEEP_PROOF_TRACE=1 ANALYST_DATA_DIR=${plain}`})});
  assert.equal((await getJson(viewerPort, '/api/status')).folders.data, plain);
  await again.stop();
  assert.equal(alive(held.pid), true);
});

test('a running Deep Proof without the flag reads OFF and says what to do; one whose environment cannot be read reads UNKNOWN without any text of the failure', async t => {
  const w = world(t);
  const {deepProofPort, viewerPort} = await ports(t);
  const held = await heldServer(w, deepProofPort, {trace: false});
  const off = await launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: madeUpRun({port: deepProofPort, pid: held.pid, cwd: w.dp, environment: 'PATH=/usr/bin HOME=/home/x DEEP_PROOF_TRACE=0'})});
  assert.equal(off.deepProof.recording, false);
  assert.ok(w.logs.some(l => /recording OFF/.test(l)));
  assert.ok(w.logs.includes('New runs will show no steps. Stop that server, then run npm run start again.'));
  assert.equal((await getJson(viewerPort, '/api/status')).deepProof.recording, false);
  await off.stop();
  // No PATH in the text: the environment was not readable.
  w.logs.length = 0;
  const blind = await launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: madeUpRun({port: deepProofPort, pid: held.pid, cwd: w.dp, environment: 'DEEP_PROOF_TRACE=1'})});
  assert.equal(blind.deepProof.recording, null);
  assert.ok(w.logs.some(l => /recording UNKNOWN/.test(l)));
  assert.equal((await getJson(viewerPort, '/api/status')).deepProof.recording, null);
  await blind.stop();
  // The command runner throws an error whose text holds a secret: it appears nowhere.
  w.logs.length = 0;
  const throwing = (command, args) => {
    if (command === 'lsof' && args.some(a => a === `-iTCP:${deepProofPort}`)) return {status: 0, stdout: `p${held.pid}\n`};
    throw new Error('ps failed with SECRET_TOKEN=hunter2 in its text');
  };
  const failed = await launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: throwing});
  assert.equal(failed.deepProof.recording, null);
  const everything = JSON.stringify([w.logs, failed.deepProof, await getJson(viewerPort, '/api/status')]);
  assert.ok(!everything.includes('hunter2') && !everything.includes('SECRET_TOKEN'), 'no text of the failure is kept or shown');
  await failed.stop();
  assert.equal(alive(held.pid), true);
});

test('the helper that reads another process returns only {recording, dataDir}, from made-up text on macOS and Linux', () => {
  const mac = environment => inspectProcess(7, {platform: 'darwin', run: (c, a) => (c === 'ps' ? {stdout: `/usr/bin/node server.mjs ${environment}`} : {stdout: 'p7\nfcwd\nn/srv/deep-proof\n'})});
  const on = mac('PATH=/bin SECRET_TOKEN=hunter2 DEEP_PROOF_TRACE=1 ANALYST_DATA_DIR=/srv/data');
  assert.deepEqual(Object.keys(on).sort(), ['dataDir', 'recording']);
  assert.deepEqual(on, {recording: true, dataDir: '/srv/data'});
  assert.deepEqual(mac('PATH=/bin DEEP_PROOF_TRACE=true'), {recording: false, dataDir: '/srv/deep-proof/.runtime'});
  assert.deepEqual(mac('PATH=/bin'), {recording: false, dataDir: '/srv/deep-proof/.runtime'});
  assert.deepEqual(mac('HOME=/x DEEP_PROOF_TRACE=1'), {recording: null, dataDir: '/srv/deep-proof/.runtime'}, 'no PATH seen: the environment was not readable');
  assert.deepEqual(mac('PATH=/bin ANALYST_DATA_DIR=relative/data'), {recording: false, dataDir: '/srv/deep-proof/relative/data'}, 'a relative folder resolves against the server folder');
  // A script given by a relative path resolves against the server folder too; with no folder known there is no answer.
  const noCwd = inspectProcess(7, {platform: 'darwin', run: (c) => (c === 'ps' ? {stdout: 'node server.mjs PATH=/bin'} : {stdout: ''})});
  assert.deepEqual(noCwd, {recording: false, dataDir: null});
  // Linux reads /proc: the files are given as text.
  const files = {'/proc/9/cmdline': 'node\0/opt/dp/server.mjs\0', '/proc/9/environ': 'PATH=/bin\0DEEP_PROOF_TRACE=1\0SECRET_TOKEN=hunter2\0'};
  const linux = inspectProcess(9, {platform: 'linux', readFile: f => files[f], readLink: () => '/opt/other'});
  assert.deepEqual(linux, {recording: true, dataDir: '/opt/dp/.runtime'});
  // Every failure is UNKNOWN with no text.
  const broken = inspectProcess(9, {platform: 'linux', readFile: () => { throw new Error('denied SECRET_TOKEN=hunter2'); }});
  assert.deepEqual(broken, {recording: null, dataDir: null});
  assert.deepEqual(inspectProcess(9, {platform: 'darwin', run: () => { throw new Error('SECRET_TOKEN=hunter2'); }}), {recording: null, dataDir: null});
  assert.equal(listenerPid(8123, () => ({stdout: 'p4242\n'})), 4242);
  assert.equal(listenerPid(8123, () => { throw new Error('x'); }), null);
});

test('port 8000 is free only when refused: another web page and a listener that never answers are "another program", and nothing is spawned', async t => {
  const w = world(t);
  const {deepProofPort, viewerPort} = await ports(t);
  const page = http.createServer((req, res) => { res.setHeader('content-type', 'text/html'); res.end('<title>Another page</title>'); });
  const sockets = new Set();
  const silent = net.createServer(socket => { sockets.add(socket); socket.on('error', () => {}); });
  t.after(() => { page.close(); silent.close(); page.closeAllConnections(); for (const s of sockets) s.destroy(); });
  for (const server of [page, silent]) {
    await new Promise(resolve => server.listen(deepProofPort, '127.0.0.1', resolve));
    await assert.rejects(launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: noRun}), error => error instanceof StartError && error.message === `Port ${deepProofPort} is used by another program.`);
    await new Promise(resolve => { server.closeAllConnections?.(); for (const s of sockets) s.destroy(); server.close(resolve); });
  }
  assert.ok(!fs.existsSync(path.join(w.repo, 'tmp', 'deep-proof-server.log')), 'nothing was spawned');
  assert.ok(await refused(viewerPort), 'no viewer was started, and the port held while starting is freed');
});

test('the six books come from one build, with no answer keys left, nothing written in the checkout; a failed build gives no books and a working viewer', async t => {
  const w = world(t);
  const {deepProofPort, viewerPort} = await ports(t);
  const listTree = dir => fs.readdirSync(dir, {recursive: true}).filter(f => !f.startsWith('.runtime') && f !== 'spawns.log').sort();
  const before = listTree(w.dp);
  const buildRoot = path.join(w.repo, 'tmp', 'minute-books', 'build');
  fs.mkdirSync(path.join(buildRoot, '.minutes-edition-8-old', 'keys'), {recursive: true});
  const r = await launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: noRun});
  const status = await getJson(viewerPort, '/api/status');
  assert.equal(status.dossiersConfigured, 6);
  assert.equal((await getJson(viewerPort, '/api/dossiers')).length, 6);
  assert.deepEqual(fs.readdirSync(path.join(buildRoot, 'minutes-edition-8')), ['fixtures'], 'no keys folder and no generation summary');
  assert.deepEqual(fs.readdirSync(buildRoot), ['minutes-edition-8'], 'no staging folder, old or new');
  assert.deepEqual(listTree(w.dp), before, 'nothing was written in the Deep Proof checkout');
  const builds = () => fs.readFileSync(path.join(w.repo, 'tmp', 'minute-books', 'builds.log'), 'utf8').trim().split('\n');
  assert.equal(builds().length, 1);
  // The build was asked for the folder below tmp/minute-books and, with no pinned commit here, not for --lean-documents.
  assert.deepEqual(JSON.parse(builds()[0]).filter(a => a !== 'ABS'), ['--data']);
  await r.stop();
  // A second start finds the books and builds nothing; keys that reappeared are removed again.
  fs.mkdirSync(path.join(buildRoot, 'minutes-edition-8', 'keys'), {recursive: true});
  fs.writeFileSync(path.join(buildRoot, 'minutes-edition-8', 'keys.generation.json'), '{}');
  const again = await launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: noRun});
  assert.equal(builds().length, 1, 'built once');
  assert.deepEqual(fs.readdirSync(path.join(buildRoot, 'minutes-edition-8')), ['fixtures']);
  await again.stop();
  // A pinned commit that this checkout holds makes the build offline.
  fs.rmSync(path.join(w.repo, 'tmp', 'minute-books'), {recursive: true, force: true});
  const offline = await launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: (c, a) => (c === 'git' && a.includes('cat-file') ? {status: 0, stdout: ''} : noRun()), python: () => '/opt/py/python3'});
  assert.deepEqual(JSON.parse(builds()[0]).filter(a => a !== 'ABS'), ['--data', '--lean-documents', '--python'], 'offline from this checkout, with the interpreter that was found');
  await offline.stop();
  // A failing build: reason printed, no books, the viewer works, the staging folder is gone.
  fs.rmSync(path.join(w.repo, 'tmp', 'minute-books'), {recursive: true, force: true});
  w.logs.length = 0;
  const failed = await launch(w, {deepProofPort, env: envFor(viewerPort, {FAKE_BUILD_FAIL: '1'}), opener: () => {}, run: noRun});
  assert.equal((await getJson(viewerPort, '/api/status')).dossiersConfigured, 0);
  assert.deepEqual(await getJson(viewerPort, '/api/dossiers'), []);
  assert.ok(w.logs.some(l => /could not be built: the generator failed/.test(l)));
  assert.deepEqual(fs.readdirSync(buildRoot), [], 'no staging folder after a failed build');
  await failed.stop();
  // VIEWER_DOSSIERS replaces the default and skips the build.
  fs.rmSync(path.join(w.repo, 'tmp', 'minute-books'), {recursive: true, force: true});
  const own = path.join(w.base, 'own-dossier');
  fs.mkdirSync(path.join(own, 'sources'), {recursive: true});
  // Keys that an earlier build left are removed at this start too, although it builds nothing.
  fs.mkdirSync(path.join(buildRoot, 'minutes-edition-8', 'keys'), {recursive: true});
  fs.mkdirSync(path.join(buildRoot, '.minutes-edition-8-stale', 'keys'), {recursive: true});
  const replaced = await launch(w, {deepProofPort, env: envFor(viewerPort, {VIEWER_DOSSIERS: own}), opener: () => {}, run: noRun});
  assert.equal((await getJson(viewerPort, '/api/status')).dossiersConfigured, 1);
  assert.ok(!fs.existsSync(path.join(w.repo, 'tmp', 'minute-books', 'builds.log')), 'no build');
  assert.deepEqual(fs.readdirSync(buildRoot), ['minutes-edition-8'], 'the staging folder is gone');
  assert.deepEqual(fs.readdirSync(path.join(buildRoot, 'minutes-edition-8')), [], 'and the keys folder');
  await replaced.stop();
});

test('a viewer that is already up means nothing starts: the opener gets the exact address, never with open false, and a missing Deep Proof is an error', async t => {
  const w = world(t);
  const {deepProofPort, viewerPort} = await ports(t);
  const first = await launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: noRun});
  const pid = (await getJson(deepProofPort, '/__env')).pid;
  const opened = [];
  const second = await launch(w, {deepProofPort, env: envFor(viewerPort), opener: url => opened.push(url), run: noRun, open: true});
  assert.equal(second.alreadyRunning, true);
  assert.deepEqual(opened, [`http://127.0.0.1:${viewerPort}/`]);
  assert.ok(w.logs.some(l => /already running/.test(l)) && w.logs.some(l => /recording ON/.test(l)), 'the recording state is told');
  assert.equal((await getJson(deepProofPort, '/__env')).pid, pid, 'the same Deep Proof');
  const quiet = [];
  const third = await launch(w, {deepProofPort, env: envFor(viewerPort), opener: url => quiet.push(url), run: noRun, open: false});
  assert.equal(third.alreadyRunning, true);
  assert.deepEqual(quiet, [], 'never opened with open false');
  await first.stop();
  // A viewer with no Deep Proof behind it.
  const lone = await startViewer({settings: {deepProofData: null, dossiers: [], deepProofTmp: null, port: viewerPort}});
  t.after(() => lone.close());
  await assert.rejects(launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: noRun}), error => error.message === 'Deep Proof is not running. Stop the other npm run start, then run it again.');
  assert.ok(await refused(deepProofPort), 'nothing was started');
});

test('stop() mentions a run that is still going, and says nothing when none is; a Deep Proof that exits by itself closes the viewer with exit 1', async t => {
  const w = world(t);
  const {deepProofPort, viewerPort} = await ports(t);
  const quiet = await launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: noRun});
  await quiet.stop();
  assert.ok(!w.logs.some(l => /A run was still going/.test(l)));
  w.logs.length = 0;
  const busy = await launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: noRun});
  const trace = new TraceBuilder({start: Date.now() - 5000});
  trace.add('run-start', runStartFields({runId: 'run-live', createdAt: new Date(Date.now() - 5000).toISOString()}), 0);
  const dir = path.join(w.dp, '.runtime', 'projects', 'proj-alpha', 'traces', 'run-live');
  fs.mkdirSync(dir, {recursive: true});
  fs.writeFileSync(path.join(dir, 'trace.jsonl'), trace.text());
  for (let i = 0; i < 50 && !(await getJson(viewerPort, '/api/runs')).some(r => r.status === 'running'); i++) await sleep(100);
  const pid = busy.deepProof.pid;
  await busy.stop();
  assert.ok(w.logs.includes('A run was still going; its agent may keep running on its own, and Deep Proof will mark the run failed at its next start.'));
  assert.equal(alive(pid), false);
  // The server it started dies on its own: the viewer closes, the exit code is 1.
  w.logs.length = 0;
  const doomed = await launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: noRun});
  process.kill(doomed.deepProof.pid, 'SIGKILL');
  assert.equal(await doomed.finished, 1);
  assert.ok(w.logs.some(l => /Deep Proof stopped by itself/.test(l) && l.includes('deep-proof-server.log')));
  assert.ok(await refused(viewerPort), 'the viewer is closed');
});

test('a Deep Proof that exits at once fails the start with the log tail; two launchers started together in one process start one Deep Proof', async t => {
  const w = world(t);
  const {deepProofPort, viewerPort} = await ports(t);
  await assert.rejects(launch(w, {deepProofPort, env: envFor(viewerPort, {FAKE_EXIT: '3'}), opener: () => {}, run: noRun}),
    error => /exited at start \(exit code 3\)/.test(error.message) && /boom-from-the-fake-server/.test(error.message));
  assert.ok(await refused(deepProofPort) && await refused(viewerPort), 'the port held while starting is freed after a failure');
  const [a, b] = await Promise.all([launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: noRun}), launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: noRun})]);
  assert.deepEqual([a.alreadyRunning, b.alreadyRunning].sort(), [false, true], 'one starts, the other finds it');
  const winner = a.alreadyRunning ? b : a;
  assert.equal((await getJson(deepProofPort, '/__env')).pid, winner.deepProof.pid, 'the winner\'s server still answers');
  assert.equal(fs.readFileSync(path.join(w.dp, 'spawns.log'), 'utf8').trim().split('\n').length, 1, 'exactly one Deep Proof was spawned');
  assert.ok(w.logs.some(l => /already running/.test(l)));
  await winner.stop();
});

test('two launcher processes started together spawn exactly one Deep Proof; the other finds it and exits 0; the real listener check accepts the child', async t => {
  const w = world(t);
  const {deepProofPort, viewerPort} = await ports(t);
  const dossier = path.join(w.base, 'a-dossier');
  fs.mkdirSync(path.join(dossier, 'sources'), {recursive: true});
  const code = `import {main} from ${JSON.stringify(pathToFileURL(path.join(tools, 'start.mjs')).href)};
main(['--no-open'], {deepProofPort: ${deepProofPort}, repoRoot: ${JSON.stringify(w.repo)}, env: process.env}).then(c => { process.exitCode = c; });`;
  const env = {PATH: process.env.PATH, VIEWER_PORT: String(viewerPort), VIEWER_DOSSIERS: dossier};
  const launchers = [0, 1].map(() => {
    const child = spawn(process.execPath, ['--input-type=module', '-e', code], {env, stdio: ['ignore', 'pipe', 'pipe']});
    child.out = '';
    child.stdout.on('data', c => { child.out += c; });
    child.stderr.on('data', c => { child.out += c; });
    child.done = new Promise(resolve => child.on('exit', (c, sig) => resolve({code: c, signal: sig})));
    t.after(() => child.kill('SIGKILL'));
    return child;
  });
  for (let i = 0; i < 150 && !launchers.some(l => /Press Ctrl-C to stop the viewer and the Deep Proof/.test(l.out)) ; i++) await sleep(100);
  const winner = launchers.find(l => /Press Ctrl-C to stop the viewer and the Deep Proof/.test(l.out));
  assert.ok(winner, launchers.map(l => l.out).join('\n---\n'));
  const loser = launchers.find(l => l !== winner);
  assert.deepEqual(await loser.done, {code: 0, signal: null}, loser.out);
  assert.match(loser.out, /already running/);
  assert.equal(fs.readFileSync(path.join(w.dp, 'spawns.log'), 'utf8').trim().split('\n').length, 1, 'exactly one fake Deep Proof was spawned');
  const dpPid = (await getJson(deepProofPort, '/__env')).pid;
  winner.kill('SIGINT');
  assert.deepEqual(await winner.done, {code: 0, signal: null});
  assert.equal(alive(dpPid), false);
  assert.ok(await refused(deepProofPort) && await refused(viewerPort));
});

test('Deep Proof gets the launcher\'s environment without npm: no npm_* variable and no node_modules/.bin on PATH; made-up values', async t => {
  const dirty = {PATH: ['/repo/node_modules/.bin', '/usr/bin', '/other/node_modules/.bin/', '/opt/node_modules/.binary', '/opt/bin'].join(path.delimiter), npm_lifecycle_event: 'start', npm_config_user_agent: 'npm/11', NPM_CONFIG_PREFIX: '/x', HOME: '/home/made-up', SECRET_KEPT: 'yes'};
  assert.deepEqual(deepProofEnvironment(dirty), {PATH: ['/usr/bin', '/opt/node_modules/.binary', '/opt/bin'].join(path.delimiter), HOME: '/home/made-up', SECRET_KEPT: 'yes'});
  // And what the spawned server really receives.
  const w = world(t);
  const {deepProofPort, viewerPort} = await ports(t);
  const r = await launch(w, {deepProofPort, env: envFor(viewerPort, {npm_lifecycle_event: 'start', PATH: `${path.join(w.repo, 'node_modules', '.bin')}${path.delimiter}${process.env.PATH}`}), opener: () => {}, run: noRun});
  const seen = await getJson(deepProofPort, '/__env');
  assert.equal(seen.npm, null);
  assert.ok(!seen.path.includes('node_modules'), seen.path);
  await r.stop();
});

test('the listener on the Deep Proof port must be the process that was spawned; an interrupt before spawning or after the viewer is up leaves nothing and says nothing misleading', async t => {
  const w = world(t);
  const {deepProofPort, viewerPort} = await ports(t);
  const otherListener = (command, args) => (command === 'lsof' && args.some(a => a === `-iTCP:${deepProofPort}`) ? {status: 0, stdout: 'p1\n'} : {status: 1, stdout: ''});
  await assert.rejects(launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: otherListener}), error => error.message === `Port ${deepProofPort} was taken by another program while Deep Proof started.`);
  assert.ok(await refused(deepProofPort) && await refused(viewerPort), 'the child was stopped and the port freed');
  // An abort that is already set stops before anything is spawned.
  fs.rmSync(path.join(w.repo, 'tmp'), {recursive: true, force: true});
  await assert.rejects(launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: noRun, signal: AbortSignal.abort()}), error => error instanceof StartAborted);
  assert.ok(!fs.existsSync(path.join(w.repo, 'tmp', 'deep-proof-server.log')), 'nothing was spawned');
  // An abort that arrives while the viewer starts: Deep Proof and viewer are stopped and "stopped by itself" is not logged.
  w.logs.length = 0;
  const controller = new AbortController();
  await assert.rejects(launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: noRun, signal: controller.signal, log: line => { w.logs.push(line); if (/^Books:/.test(line)) controller.abort(); }}), error => error instanceof StartAborted);
  assert.ok(!w.logs.some(l => /stopped by itself/.test(l)), w.logs.join(' | '));
  assert.ok(await refused(deepProofPort) && await refused(viewerPort));
});

test('npm run start as a process: Ctrl-C (SIGINT) stops the viewer and the Deep Proof it started and exits 0', async t => {
  const w = world(t);
  const {deepProofPort, viewerPort} = await ports(t);
  const dossier = path.join(w.base, 'a-dossier');
  fs.mkdirSync(path.join(dossier, 'sources'), {recursive: true});
  const code = `import {main} from ${JSON.stringify(pathToFileURL(path.join(tools, 'start.mjs')).href)};
main(['--no-open'], {deepProofPort: ${deepProofPort}, repoRoot: ${JSON.stringify(w.repo)}, env: process.env}).then(c => { process.exitCode = c; });`;
  const env = {PATH: process.env.PATH, VIEWER_PORT: String(viewerPort), VIEWER_DOSSIERS: dossier};
  const child = spawn(process.execPath, ['--input-type=module', '-e', code], {env, stdio: ['ignore', 'pipe', 'pipe']});
  let out = '';
  child.stdout.on('data', c => { out += c; });
  child.stderr.on('data', c => { out += c; });
  t.after(() => child.kill('SIGKILL'));
  const exit = new Promise(resolve => child.on('exit', (c, s) => resolve({code: c, signal: s})));
  for (let i = 0; i < 100 && !/Press Ctrl-C to stop the viewer and the Deep Proof this command started/.test(out); i++) await sleep(100);
  assert.match(out, /Press Ctrl-C to stop the viewer and the Deep Proof this command started/, out);
  const dpPid = (await getJson(deepProofPort, '/__env')).pid;
  assert.equal((await getJson(viewerPort, '/api/status')).deepProof.startedHere, true);
  child.kill('SIGINT');
  assert.deepEqual(await exit, {code: 0, signal: null});
  assert.equal(alive(dpPid), false, 'the Deep Proof it started is gone');
  assert.ok(await refused(deepProofPort) && await refused(viewerPort));
});

test('Ctrl-C while the books are being built stops the Deep Proof that was already started and the generator it spawned, and exits 0', async t => {
  const w = world(t);
  const {deepProofPort, viewerPort} = await ports(t);
  const code = `import {main} from ${JSON.stringify(pathToFileURL(path.join(tools, 'start.mjs')).href)};
main(['--no-open'], {deepProofPort: ${deepProofPort}, repoRoot: ${JSON.stringify(w.repo)}, env: process.env}).then(c => { process.exitCode = c; });`;
  const env = {PATH: process.env.PATH, VIEWER_PORT: String(viewerPort), FAKE_BUILD_SLEEP: '60000', FAKE_BUILD_GRANDCHILD: '1'};
  const child = spawn(process.execPath, ['--input-type=module', '-e', code], {env, stdio: ['ignore', 'pipe', 'pipe']});
  let out = '';
  child.stdout.on('data', c => { out += c; });
  child.stderr.on('data', c => { out += c; });
  t.after(() => child.kill('SIGKILL'));
  const exit = new Promise(resolve => child.on('exit', (c, s) => resolve({code: c, signal: s})));
  for (let i = 0; i < 100 && !/Building the six/.test(out); i++) await sleep(100);
  assert.match(out, /Building the six/, out);
  const pidFile = path.join(w.repo, 'tmp', 'minute-books', 'grandchild.pid');
  for (let i = 0; i < 100 && !fs.existsSync(pidFile); i++) await sleep(50);
  const dpPid = (await getJson(deepProofPort, '/__env')).pid;
  child.kill('SIGINT');
  assert.deepEqual(await exit, {code: 0, signal: null}, out);
  assert.equal(alive(dpPid), false, 'the Deep Proof that was started before the build is gone');
  assert.ok(await refused(deepProofPort) && await refused(viewerPort));
  const grandchild = Number(fs.readFileSync(path.join(w.repo, 'tmp', 'minute-books', 'grandchild.pid'), 'utf8'));
  await sleep(2200); // the generator would have written its keys after 1.5 seconds
  assert.equal(alive(grandchild), false, 'the process the build started is gone');
  assert.ok(!fs.existsSync(path.join(w.repo, 'tmp', 'minute-books', 'build', 'minutes-edition-8')), 'no half-built book folder and no keys');
});

test('a Deep Proof that dies while the books are being built fails the start with its log tail and leaves nothing', async t => {
  const w = world(t);
  const {deepProofPort, viewerPort} = await ports(t);
  // The fake server writes its pid beside itself; the fake build kills it.
  fs.writeFileSync(path.join(w.dp, 'server.mjs'), FAKE_SERVER.replace("fs.mkdirSync(path.join(dir, 'projects'), {recursive: true});", "fs.mkdirSync(path.join(dir, 'projects'), {recursive: true});\nfs.writeFileSync('server.pid', String(process.pid));"));
  const build = path.join(w.dp, 'tests', 'evaluation', 'minute-books', 'build.mjs');
  fs.writeFileSync(build, `import fs from 'node:fs';import path from 'node:path';
const pid = Number(fs.readFileSync(${JSON.stringify(path.join(w.dp, 'server.pid'))}, 'utf8'));
process.kill(pid, 'SIGKILL');
await new Promise(resolve => setTimeout(resolve, 400));
`);
  await assert.rejects(launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: noRun}), error => /Deep Proof exited during start \(no exit code\)/.test(error.message) && error.message.includes('deep-proof-server.log'));
  assert.ok(await refused(deepProofPort) && await refused(viewerPort));
});

test('the early listener answers 503 {"starting": true} on every route; an open page treats it as not ready; a handed-in server must be bound to 127.0.0.1; a server error after the handover stops everything', async t => {
  const w = world(t);
  const {deepProofPort, viewerPort} = await ports(t);
  // A slow Deep Proof keeps the early listener up while the test reads it.
  fs.writeFileSync(path.join(w.dp, 'server.mjs'), `await new Promise(r => setTimeout(r, 1200));\n${FAKE_SERVER}`);
  const starting = launch(w, {deepProofPort, env: envFor(viewerPort), opener: () => {}, run: noRun});
  let answers = null;
  for (let i = 0; i < 50 && !answers; i++) {
    const status = await fetch(`http://127.0.0.1:${viewerPort}/api/status`).catch(() => null);
    if (status) answers = status;
    else await sleep(20);
  }
  assert.ok(answers, 'the early listener is up');
  for (const route of ['/api/status', '/api/runs', '/']) {
    const r = await fetch(`http://127.0.0.1:${viewerPort}${route}`);
    assert.equal(r.status, 503, route);
    assert.deepEqual(await r.json(), {starting: true});
    assert.match(r.headers.get('content-type'), /^application\/json/);
    assert.equal(r.headers.get('cache-control'), 'no-store');
    assert.equal(r.headers.get('x-content-type-options'), 'nosniff');
  }
  const r = await starting;
  assert.equal((await fetch(`http://127.0.0.1:${viewerPort}/api/status`)).status, 200, 'the handover heals the same address');
  // The page treats a 503 as a viewer that is not ready: a readable alert, no script error (the page reads body.starting before anything else).
  const page = fs.readFileSync(path.join(tools, 'process-viewer.html'), 'utf8');
  assert.match(page, /res\.status === 503 && body\.starting\) throw new ApiError\(503, 'The viewer is starting\. Trying again\.'\)/);
  // A server error after the handover: one line with the code only, and the normal stop runs.
  const dpPid = r.deepProof.pid;
  r.viewer.server.emit('error', Object.assign(new Error('details SECRET_TOKEN=hunter2'), {code: 'EIO'}));
  assert.equal(await r.finished, 0);
  assert.ok(w.logs.includes("The viewer's server failed (EIO); stopping."));
  assert.ok(!JSON.stringify(w.logs).includes('hunter2'));
  assert.equal(alive(dpPid), false);
  assert.ok(await refused(deepProofPort) && await refused(viewerPort));
  // A server bound to every interface is refused before any handler is attached.
  const wide = http.createServer();
  const marker = () => {};
  wide.on('request', marker);
  await new Promise(resolve => wide.listen(0, '0.0.0.0', resolve));
  t.after(() => wide.close());
  await assert.rejects(startViewer({settings: {deepProofData: null, dossiers: [], deepProofTmp: null, port: 0}, server: wide}), /must listen on 127\.0\.0\.1 only/);
  assert.deepEqual(wide.listeners('request'), [marker], 'no request handler was attached');
});
