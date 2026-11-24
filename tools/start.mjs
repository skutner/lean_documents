#!/usr/bin/env node
/** The one command of this repository: `npm run start`.
 *
 *   node tools/start.mjs [--no-open]
 *
 * It starts Deep Proof from the sibling checkout with recording on (port 8000), or reports on a Deep Proof that is
 * already running; starts the read-only process viewer pointed at Deep Proof's data folder and at the six edition 8
 * books; and opens the browser at http://127.0.0.1:8790/. Ctrl-C stops the viewer and a Deep Proof this command
 * started. A Deep Proof it did not start is never stopped. Exit 0 on a normal stop or "already running", 1 on an error.
 * It never writes into a Deep Proof folder and never starts a model run.
 *
 * This is an operator tool, not a server module: the viewer's own modules start no process (tools/test-process-viewer.mjs).
 * Settings (environment only): DEEP_PROOF_DIR, VIEWER_PORT, VIEWER_DEEP_PROOF_DATA, VIEWER_DOSSIERS, VIEWER_DEEP_PROOF_TMP. */
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import {spawn, spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {startViewer, loadSettings, REPO_ROOT} from './process-viewer.mjs';
import {resolvePython} from './toolchain.mjs';

export const DEEP_PROOF_PORT = 8000;
const MAC_CA_BUNDLE = '/etc/ssl/cert.pem'; // as tools/ala.mjs: the sandbox of a coding agent cannot reach the system certificates
const OFF_TEXT = 'New runs will show no steps. Stop that server, then run npm run start again.';
const OFF_TEXT_VIEWER_UP = 'New runs will show no steps. Stop that Deep Proof server and any other npm run start, then run npm run start again.';
const RUN_LOST_TEXT = 'A run was still going; its agent may keep running on its own, and Deep Proof will mark the run failed at its next start.';
const TIMEOUTS = {probeMs: 2000, startMs: 30000, killMs: 5000, lockMs: 180000, pollMs: 200, buildMs: 10 * 60 * 1000};

/** A named problem, printed as one plain line. */
export class StartError extends Error {}
/** The launcher was interrupted while it was starting; everything it had started is stopped. */
export class StartAborted extends StartError {}
const fail = message => { throw new StartError(message); };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const isDirectory = folder => { try { return fs.statSync(folder).isDirectory(); } catch { return false; } };
const isFile = file => { try { return fs.statSync(file).isFile(); } catch { return false; } };

// ---------- Probes ----------

/** One GET on 127.0.0.1. Resolves {state: 'refused'} when the connection is refused, {state: 'error'} for anything else
 * that is not an answer (a timeout, a reset), or {state: 'ok', status, body} (the body capped at 64 KiB). */
export function httpGet(port, route, timeoutMs) {
  return new Promise(resolve => {
    let done = false;
    const finish = value => { if (!done) { done = true; clearTimeout(timer); resolve(value); } };
    const req = http.get({host: '127.0.0.1', port, path: route, timeout: timeoutMs, agent: false}, res => {
      const chunks = [];
      let size = 0;
      res.on('data', chunk => {
        size += chunk.length;
        if (size <= 65536) chunks.push(chunk);
        else { res.destroy(); finish({state: 'ok', status: res.statusCode, body: Buffer.concat(chunks).toString('utf8')}); }
      });
      res.on('end', () => finish({state: 'ok', status: res.statusCode, body: Buffer.concat(chunks).toString('utf8')}));
      res.on('error', () => finish({state: 'error'}));
    });
    const timer = setTimeout(() => { req.destroy(); finish({state: 'error'}); }, timeoutMs + 500);
    req.on('timeout', () => { req.destroy(); finish({state: 'error'}); });
    req.on('error', error => finish({state: error.code === 'ECONNREFUSED' ? 'refused' : 'error'}));
  });
}

/** Port 8000 is free only when the connection is refused (a second server on one data folder would fail the live
 * server's running runs). Deep Proof answers 200 with `<title>Deep Proof` on `/`. Anything else is another program. */
export async function classifyDeepProofPort(port, timeoutMs) {
  const answer = await httpGet(port, '/', timeoutMs);
  if (answer.state === 'refused') return 'free';
  if (answer.state === 'ok' && answer.status === 200 && answer.body.includes('<title>Deep Proof')) return 'deepproof';
  return 'other';
}

/** Is whatever listens on the viewer port this viewer? Resolves {kind: 'free' | 'viewer' | 'other', status}. */
async function classifyViewerPort(port, timeoutMs) {
  const answer = await httpGet(port, '/api/status', timeoutMs);
  if (answer.state === 'refused') return {kind: 'free'};
  if (answer.state === 'ok' && answer.status === 200) {
    try {
      const status = JSON.parse(answer.body);
      if (status && status.starting === true) return {kind: 'starting'};
      if (status && typeof status.pollMs === 'number' && status.folders && 'dossiersConfigured' in status) return {kind: 'viewer', status};
    } catch { /* not JSON: another program */ }
  }
  return {kind: 'other'};
}

// ---------- Inspecting a Deep Proof this command did not start ----------

/** Runs a command to completion with its output captured. Returns {status, stdout}; never inherits the terminal. */
export function runCommand(command, args) {
  const result = spawnSync(command, args, {encoding: 'utf8', timeout: 10000, maxBuffer: 16 * 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe']});
  return {status: result.status, stdout: result.stdout ?? ''};
}

/** The pid that listens on a TCP port on this machine, or null. */
export function listenerPid(port, run = runCommand) {
  try {
    const m = /^p(\d+)$/m.exec(String(run('lsof', ['-nP', `-iTCP:${port}`, '-sTCP:LISTEN', '-Fp']).stdout));
    return m ? Number(m[1]) : null;
  } catch {
    return null;
  }
}

/** Reads another process and returns ONLY {recording, dataDir}. It looks at three names (PATH, DEEP_PROOF_TRACE,
 * ANALYST_DATA_DIR) and keeps nothing else: not the environment, not an error's text. Whatever goes wrong gives
 * {recording: null, dataDir: null}. `recording` is true when DEEP_PROOF_TRACE is `1`, false when the environment was
 * readable and holds anything else, and null when it was not readable (no PATH seen). */
export function inspectProcess(pid, {run = runCommand, platform = process.platform, readFile = fs.readFileSync, readLink = fs.readlinkSync} = {}) {
  try {
    let tokens;
    let cwd = null;
    if (platform === 'linux') {
      tokens = [...String(readFile(`/proc/${pid}/cmdline`, 'latin1')).split('\0'), ...String(readFile(`/proc/${pid}/environ`, 'latin1')).split('\0')];
      cwd = String(readLink(`/proc/${pid}/cwd`));
    } else {
      tokens = String(run('ps', ['-E', '-ww', '-o', 'command=', '-p', String(pid)]).stdout).split(/\s+/);
      const m = /^n(\/.*)$/m.exec(String(run('lsof', ['-a', '-p', String(pid), '-d', 'cwd', '-Fn']).stdout));
      cwd = m ? m[1] : null;
    }
    let sawPath = false;
    let trace = null;
    let data = null;
    for (const token of tokens) {
      if (token.startsWith('PATH=')) sawPath = true;
      else if (token.startsWith('DEEP_PROOF_TRACE=')) trace = token.slice('DEEP_PROOF_TRACE='.length);
      else if (token.startsWith('ANALYST_DATA_DIR=')) data = token.slice('ANALYST_DATA_DIR='.length);
    }
    const script = tokens.find(token => !token.includes('=') && /\.(mjs|js)$/.test(token));
    let dataDir = null;
    if (data && path.isAbsolute(data)) dataDir = path.resolve(data);
    else if (data && cwd) dataDir = path.resolve(cwd, data);
    else if (!data && script && (path.isAbsolute(script) || cwd)) dataDir = path.join(path.dirname(path.resolve(cwd ?? '/', script)), '.runtime');
    return {recording: sawPath ? trace === '1' : null, dataDir};
  } catch {
    return {recording: null, dataDir: null};
  }
}

// ---------- The viewer port as the start-up lock ----------

/** Binds the viewer port before anything else is started and answers `{starting: true}` on it until the viewer takes the
 * server over. The operating system frees the port when the launcher dies, so nothing goes stale. */
function listenEarly(port) {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const body = JSON.stringify({starting: true});
      res.writeHead(200, {'Content-Type': 'application/json; charset=utf-8', 'Content-Length': Buffer.byteLength(body), 'Cache-Control': 'no-store'});
      res.end(body);
    });
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => { server.removeListener('error', reject); resolve(server); });
  });
}
const closeServer = server => new Promise(resolve => { server.close(() => resolve()); server.closeAllConnections(); });

/** The environment given to Deep Proof and to the book build: the launcher's own, without what npm adds when this runs as
 * `npm run start` (the npm_* variables and every node_modules/.bin on PATH), so that Deep Proof does not find this
 * repository's tools there. */
export function deepProofEnvironment(env) {
  const clean = {};
  for (const [key, value] of Object.entries(env)) if (!/^npm_/i.test(key)) clean[key] = value;
  if (typeof clean.PATH === 'string') {
    clean.PATH = clean.PATH.split(path.delimiter).filter(entry => !/(^|[\\/])node_modules[\\/]\.bin[\\/]?$/.test(entry)).join(path.delimiter);
  }
  return clean;
}

// ---------- Pieces of start() ----------

function findCheckout(env, repoRoot, nodeVersion) {
  if (Number(String(nodeVersion).split('.')[0]) < 24) fail(`Deep Proof needs Node.js 24 or later; this is ${nodeVersion}.`);
  const dir = path.resolve(env.DEEP_PROOF_DIR || path.join(repoRoot, '..', 'deep-proof-community-edition'));
  let name = null;
  try { name = JSON.parse(fs.readFileSync(path.join(dir, 'package.json'), 'utf8')).name; } catch { /* named below */ }
  if (!isFile(path.join(dir, 'server.mjs')) || name !== 'deep-proof') fail(`Deep Proof was not found in ${dir}. Set DEEP_PROOF_DIR to its folder.`);
  return dir;
}

/** The six books: `tmp/minute-books/build/<id>/fixtures/*`, built once by the checkout's own build script. Answer keys
 * never stay: the keys folder, the generation summary and stale staging folders are removed at every start. Returns the
 * dossier folders (empty when the build is not possible; the viewer then starts with none). */
async function prepareBooks({repoRoot, checkout, env, run, python, log, buildMs, signal, build = true}) {
  const script = path.join(checkout, 'tests', 'evaluation', 'minute-books');
  let selection;
  try {
    selection = JSON.parse(fs.readFileSync(path.join(script, 'selection.json'), 'utf8'));
  } catch {
    if (build) log('The six books are not available: this Deep Proof has no minute-books selection. The viewer starts without them.');
    return [];
  }
  const id = selection?.id;
  const commit = selection?.generator?.commit;
  if (typeof id !== 'string' || !/^[A-Za-z0-9._-]+$/.test(id)) {
    if (build) log('The six books are not available: the selection of this Deep Proof is not readable. The viewer starts without them.');
    return [];
  }
  const dataRoot = path.join(repoRoot, 'tmp', 'minute-books');
  const buildRoot = path.join(dataRoot, 'build');
  const target = path.join(buildRoot, id);
  const fixtures = path.join(target, 'fixtures');
  const clean = () => {
    fs.rmSync(path.join(target, 'keys'), {recursive: true, force: true});
    fs.rmSync(path.join(target, 'keys.generation.json'), {force: true});
    let names = [];
    try { names = fs.readdirSync(buildRoot); } catch { /* nothing built yet */ }
    for (const name of names) if (name.startsWith(`.${id}-`) || name.startsWith('.generator-')) fs.rmSync(path.join(buildRoot, name), {recursive: true, force: true});
  };
  clean();
  if (!build) return [];
  if (!isDirectory(fixtures)) {
    fs.rmSync(target, {recursive: true, force: true}); // an unfinished build that the build script would refuse to replace
    const args = [path.join(script, 'build.mjs'), '--data', dataRoot];
    if (typeof commit === 'string' && /^[0-9a-f]{40}$/.test(commit) && run('git', ['-C', repoRoot, 'cat-file', '-e', `${commit}^{commit}`]).status === 0) args.push('--lean-documents', repoRoot);
    const interpreter = python();
    if (interpreter) args.push('--python', interpreter);
    log('Building the six edition 8 books once, below tmp/minute-books. This takes about a minute.');
    const built = await new Promise(resolve => {
      const proc = spawn(process.execPath, args, {cwd: repoRoot, env, detached: true, stdio: ['ignore', 'pipe', 'pipe']});
      // The build runs in its own process group, so that the generator it starts goes with it.
      const killGroup = () => { try { process.kill(-proc.pid, 'SIGKILL'); } catch { try { proc.kill('SIGKILL'); } catch { /* gone */ } } };
      let output = '';
      const add = chunk => { output = (output + chunk).slice(-8192); };
      proc.stdout.on('data', add);
      proc.stderr.on('data', add);
      const timer = setTimeout(killGroup, buildMs);
      const onAbort = killGroup;
      signal?.addEventListener('abort', onAbort, {once: true});
      proc.on('error', error => { clearTimeout(timer); resolve({status: 1, output: error.message}); });
      proc.on('close', status => { clearTimeout(timer); signal?.removeEventListener('abort', onAbort); resolve({status, output}); });
    });
    clean();
    if (signal?.aborted) {
      fs.rmSync(target, {recursive: true, force: true});
      throw new StartAborted('Interrupted while the books were being built.');
    }
    if (built.status !== 0) {
      const reason = (built.output.trim() || 'no output').split('\n').slice(-3).join(' ');
      log(`The six books could not be built: ${reason}`);
      log('The viewer starts without them.');
      fs.rmSync(target, {recursive: true, force: true});
      return [];
    }
  }
  let entries = [];
  try { entries = fs.readdirSync(fixtures, {withFileTypes: true}); } catch { /* none */ }
  return entries.filter(e => e.isDirectory() && !e.name.startsWith('.')).map(e => path.join(fixtures, e.name)).sort();
}

function openBrowser(url, platform = process.platform) {
  const child = spawn(platform === 'darwin' ? 'open' : 'xdg-open', [url], {detached: true, stdio: 'ignore'});
  child.on('error', () => { /* a failed open is not an error: the address is printed */ });
  child.unref();
}

const recordingWord = recording => (recording === true ? 'ON' : recording === false ? 'OFF' : 'UNKNOWN');

// ---------- start() ----------

/** Starts or finds Deep Proof, starts the viewer in this process and opens the browser. Resolves
 * `{viewer, deepProof: {url, recording, startedHere, pid}, books, stop, finished, alreadyRunning}`. `finished` resolves
 * 0 after stop() and 1 when a Deep Proof started here exits by itself. Everything outside the settings exists for tests. */
export async function start({env = process.env, repoRoot = REPO_ROOT, deepProofPort = DEEP_PROOF_PORT, open = true, opener = openBrowser, log = console.log,
  run = runCommand, platform = process.platform, nodeVersion = process.versions.node, python, timeouts = {}, signal} = {}) {
  const t = {...TIMEOUTS, ...timeouts};
  const findPython = python ?? (() => { try { return resolvePython({env}).path; } catch { return null; } });
  const checkout = findCheckout(env, repoRoot, nodeVersion);
  let settings;
  try { settings = loadSettings({env}); } catch (error) { fail(error.message); }
  const tmp = path.join(repoRoot, 'tmp');
  const deepProofUrl = `http://127.0.0.1:${deepProofPort}/`;
  const childBase = deepProofEnvironment(env);
  let child = null;
  let exited = null;
  let viewer = null;
  let stopping = null;
  let stoppingChild = false;
  let early = null;
  let settle;
  const finished = new Promise(resolve => { settle = resolve; });

  const logFile = path.join(tmp, 'deep-proof-server.log');
  const tail = () => {
    let lines = [];
    try { lines = fs.readFileSync(logFile, 'utf8').trimEnd().split('\n').slice(-20); } catch { /* no log */ }
    return lines.length ? `\n${lines.join('\n')}` : '';
  };
  /** Called between the slow steps: stops the start when the launcher was interrupted or when the server it started is gone. */
  const checkpoint = () => {
    if (signal?.aborted) throw new StartAborted('Interrupted while starting.');
    if (child && exited) fail(`Deep Proof exited during start (${exited.code === null ? 'no exit code' : `exit code ${exited.code}`}). Its log is ${logFile}:${tail()}`);
  };

  /** Stops only the process this function spawned: SIGTERM to its group, SIGKILL when it is still there after killMs. */
  const stopChild = async () => {
    if (!child || exited) return;
    stoppingChild = true;
    const signal = name => { try { process.kill(-child.pid, name); } catch { try { child.kill(name); } catch { /* gone */ } } };
    signal('SIGTERM');
    const until = Date.now() + t.killMs;
    while (!exited && Date.now() < until) await sleep(20);
    if (!exited) {
      signal('SIGKILL');
      while (!exited) await sleep(20);
    }
  };

  try {
    // Step 2: the viewer port is the lock. Whoever binds it first starts everything; the others find it taken.
    if (settings.port !== 0) {
      const until = Date.now() + t.lockMs;
      let waited = false;
      for (;;) {
        if (signal?.aborted) throw new StartAborted('Interrupted while waiting for another start.');
        try {
          early = await listenEarly(settings.port);
          break;
        } catch (error) {
          if (error.code !== 'EADDRINUSE') fail('The viewer could not start listening.');
        }
        const seen = await classifyViewerPort(settings.port, t.probeMs);
        if (seen.kind === 'other') fail(`Port ${settings.port} is used by another program. Set VIEWER_PORT to another port.`);
        if (seen.kind === 'viewer') {
          const address = `http://127.0.0.1:${settings.port}/`;
          const where = await classifyDeepProofPort(deepProofPort, t.probeMs);
          if (where === 'free') fail('Deep Proof is not running. Stop the other npm run start, then run it again.');
          if (where === 'other') fail(`Port ${deepProofPort} is used by another program.`);
          const pid = listenerPid(deepProofPort, run);
          const known = seen.status.deepProof;
          const recording = known && 'recording' in known ? known.recording : pid ? inspectProcess(pid, {run, platform}).recording : null;
          log(`The viewer is already running: ${address}`);
          log(`Deep Proof is running at ${deepProofUrl} (pid ${pid ?? 'unknown'}), recording ${recordingWord(recording)}.`);
          if (recording === false) log(OFF_TEXT_VIEWER_UP);
          if (open) { try { opener(address); } catch { /* the address is printed */ } }
          return {viewer: null, alreadyRunning: true, deepProof: {url: deepProofUrl, recording, startedHere: false, pid}, books: null, stop: async () => {}, finished: Promise.resolve(0)};
        }
        if (seen.kind === 'starting' && !waited) { log('Another npm run start is starting; waiting for it.'); waited = true; }
        if (Date.now() > until) fail('Another npm run start is starting; wait for it.');
        await sleep(t.pollMs);
      }
    }

    // Step 3: Deep Proof.
    const where = await classifyDeepProofPort(deepProofPort, t.probeMs);
    if (where === 'other') fail(`Port ${deepProofPort} is used by another program.`);
    let recording;
    let pid;
    let startedHere = false;
    let heldDataDir = null;
    if (where === 'deepproof') {
      pid = listenerPid(deepProofPort, run);
      const info = pid ? inspectProcess(pid, {run, platform}) : {recording: null, dataDir: null};
      recording = info.recording;
      heldDataDir = info.dataDir;
      log(`Deep Proof is already running at ${deepProofUrl} (pid ${pid ?? 'unknown'}), recording ${recordingWord(recording)}.`);
      if (recording === false) log(OFF_TEXT);
    } else {
      const childEnv = {...childBase, DEEP_PROOF_TRACE: '1', PORT: String(deepProofPort), HOST: '127.0.0.1'};
      if (platform === 'darwin' && !env.SSL_CERT_FILE && !env.SSL_CERT_DIR && isFile(MAC_CA_BUNDLE)) childEnv.SSL_CERT_FILE = MAC_CA_BUNDLE;
      checkpoint();
      fs.mkdirSync(tmp, {recursive: true});
      const fd = fs.openSync(logFile, 'a');
      try {
        child = spawn(process.execPath, ['server.mjs'], {cwd: checkout, env: childEnv, detached: true, stdio: ['ignore', fd, fd]});
      } finally {
        fs.closeSync(fd);
      }
      child.on('error', () => { exited = exited ?? {code: null, signal: null}; });
      child.on('exit', (code, signal) => {
        exited = {code, signal};
        if (!viewer || stopping || stoppingChild) return;
        log(`Deep Proof stopped by itself (${code === null ? `signal ${signal}` : `exit code ${code}`}). Log: ${logFile}`);
        stopping = viewer.close().then(() => settle(1));
      });
      log(`Starting Deep Proof; this can take up to ${Math.round(t.startMs / 1000)} seconds.`);
      const until = Date.now() + t.startMs;
      let ready = false;
      while (!ready) {
        if (signal?.aborted) throw new StartAborted('Interrupted while starting.');
        if (exited) fail(`Deep Proof exited at start (${exited.code === null ? 'no exit code' : `exit code ${exited.code}`}). Its log is ${logFile}:${tail()}`);
        if (Date.now() > until) {
          await stopChild();
          fail(`Deep Proof did not answer on port ${deepProofPort} within ${Math.round(t.startMs / 1000)} seconds. Its log is ${logFile}:${tail()}`);
        }
        if ((await classifyDeepProofPort(deepProofPort, t.probeMs)) === 'deepproof') {
          await sleep(t.pollMs); // a server that lost the port to another one exits within moments
          if (exited) continue;
          // The listener must be the process this command spawned (when the machine can tell).
          const listener = listenerPid(deepProofPort, run);
          if (listener !== null && listener !== child.pid) {
            await stopChild();
            fail(`Port ${deepProofPort} was taken by another program while Deep Proof started.`);
          }
          ready = true;
        } else {
          await sleep(t.pollMs);
        }
      }
      startedHere = true;
      recording = true;
      pid = child.pid;
      log(`Deep Proof started: ${deepProofUrl} (pid ${pid}), recording ON. Its output goes to ${logFile}`);
    }

    // Data folder, books, viewer.
    // A folder read from another process that is not a folder (a path with a space cannot be read from ps output) gives way to the checkout's own.
    const dataDir = settings.deepProofData ?? (startedHere ? path.resolve(checkout, env.ANALYST_DATA_DIR || '.runtime') : heldDataDir && isDirectory(heldDataDir) ? heldDataDir : path.join(checkout, '.runtime'));
    log(`Deep Proof data folder: ${dataDir}`);
    checkpoint();
    const ownBooks = settings.dossiers.length > 0;
    const found = await prepareBooks({repoRoot, checkout, env: childBase, run, python: findPython, log, buildMs: t.buildMs, signal, build: !ownBooks});
    const books = ownBooks ? settings.dossiers : found;
    checkpoint();
    log(`Books: ${books.length}`);
    try {
      viewer = await startViewer({settings: {...settings, deepProofData: dataDir, dossiers: books}, deepProof: {url: deepProofUrl, recording, startedHere}, ...(early ? {server: early} : {})});
      early = null;
    } catch (error) {
      await stopChild();
      fail(error.message);
    }
    checkpoint();
    log(`Process viewer: ${viewer.url}`);
    if (open) { try { opener(viewer.url); } catch { /* the address is printed */ } }

    const stop = () => {
      stopping ??= (async () => {
        if (child && !exited) {
          try {
            const answer = await httpGet(viewer.port, '/api/runs', t.probeMs);
            if (answer.state === 'ok' && JSON.parse(answer.body).some(r => r.status === 'running')) log(RUN_LOST_TEXT);
          } catch { /* the notice is a courtesy */ }
        }
        await viewer.close();
        await stopChild();
        settle(0);
      })();
      return stopping;
    };
    return {viewer, alreadyRunning: false, deepProof: {url: deepProofUrl, recording, startedHere, pid}, books, stop, finished};
  } catch (error) {
    stoppingChild = true;
    if (viewer) await viewer.close().catch(() => {});
    await stopChild();
    throw error;
  } finally {
    if (early) await closeServer(early).catch(() => {});
  }
}

// ---------- The command ----------

/** `node tools/start.mjs [--no-open]`. Resolves the exit code. `options` exist for tests (the Deep Proof port and the repository root). */
export async function main(argv, {deepProofPort = DEEP_PROOF_PORT, env = process.env, repoRoot = REPO_ROOT} = {}) {
  if (argv.some(a => a !== '--no-open')) {
    console.error('Usage: npm run start [-- --no-open]');
    return 1;
  }
  // The handlers come first: a Ctrl-C while the books are built or Deep Proof is starting must stop what was started.
  const controller = new AbortController();
  let running = null;
  const stopOnSignal = () => { if (running) running.stop(); else controller.abort(); };
  for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) process.on(signal, stopOnSignal);
  try {
    running = await start({env, repoRoot, deepProofPort, open: !argv.includes('--no-open'), signal: controller.signal});
  } catch (error) {
    if (error instanceof StartAborted) return 0;
    console.error(error instanceof StartError ? error.message : `Could not start: ${error.message}`);
    return 1;
  }
  if (running.alreadyRunning) return 0;
  console.log(running.deepProof.startedHere ? 'Press Ctrl-C to stop the viewer and the Deep Proof this command started.' : 'Press Ctrl-C to stop the viewer. The Deep Proof that was already running stays up.');
  return running.finished;
}

if (process.argv[1] && import.meta.url === pathToFileURL(fs.realpathSync(process.argv[1])).href) {
  main(process.argv.slice(2)).then(code => { process.exitCode = code; });
}
