/** Checks of tools/api-auth.mjs (criterion H-A1 of the pilot API authentication). Every server here is a small
 * loopback listener built on createApiAuth and gate, never the pilot server, and every base is a fresh folder under the
 * host temporary folder, removed afterwards. Keys are never printed: assertions compare hashes or test for absence,
 * because suite logs may land in the repository (tools/verify.mjs keeps them under tmp/verify). */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawn, spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import * as auth from './api-auth.mjs';
import {homeUnavailable, reasons} from './agent-home.mjs';

const {API_UNAUTHORIZED, createApiAuth, readApiKey} = auth;
const TOOLS = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(TOOLS, '..');
const TEMPORARY = fs.realpathSync.native(os.tmpdir());
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const modeOf = file => fs.lstatSync(file).mode & 0o777;

async function fixture(t) {
  const dir = fs.realpathSync.native(await fsp.mkdtemp(path.join(TEMPORARY, 'api-auth-test-')));
  t.after(() => {
    if (path.dirname(dir) === TEMPORARY && path.basename(dir).startsWith('api-auth-test-')) {
      fs.chmodSync(dir, 0o700);
      for (const folder of [path.join(dir, 'base'), path.join(dir, 'base/server')]) {
        try {
          fs.chmodSync(folder, 0o700);
        } catch {}
      }
      fs.rmSync(dir, {recursive: true, force: true});
    }
  });
  return {dir, base: path.join(dir, 'base')};
}

const keyOf = (base, port) => fs.readFileSync(auth.keyFilePath(base, port), 'utf8').replace(/\n$/, '');

/** A loopback listener with the gate in front of every request; it answers 200 for anything that passes. */
async function listener(t, base) {
  let gate;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (!gate.gate(req, res, url)) return;
    if (req.method === 'POST' && url.pathname === '/api/session') return gate.session(req, res);
    res.writeHead(200, {'Content-Type': 'application/json'});
    res.end(JSON.stringify({ok: true, path: url.pathname}));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  gate = createApiAuth({base, port, root: ROOT});
  gate.start();
  t.after(() => {
    gate.stopSync();
    server.closeAllConnections();
    return new Promise(resolve => server.close(resolve));
  });
  return {server, port, auth: gate, key: keyOf(base, port)};
}

/** One raw request, so repeated or unusual headers reach the server exactly as written. */
function raw(port, lines, body = '') {
  return new Promise((resolve, reject) => {
    const socket = net.connect(port, '127.0.0.1');
    let data = '';
    socket.on('data', chunk => (data += chunk));
    socket.on('error', reject);
    socket.on('close', () => {
      const [head, ...rest] = data.split('\r\n\r\n');
      resolve({status: Number(/^HTTP\/1\.1 (\d+)/.exec(head)?.[1]), head, body: rest.join('\r\n\r\n')});
    });
    socket.end([...lines, `Host: 127.0.0.1:${port}`, 'Connection: close', '', body].join('\r\n'));
  });
}

const refused = response => response.status === 401 && JSON.parse(response.body).error === API_UNAUTHORIZED;

function capture({tty = false} = {}) {
  const stream = {isTTY: tty, text: '', write: chunk => (stream.text += chunk)};
  return stream;
}

// ---------- The key and its file ----------

for (const umask of [0o000, 0o077]) {
  test(`Key: 43 base64url characters, new per instance; file 0600 and folder 0700 under umask 0${umask.toString(8).padStart(2, '0')}`, async t => {
    const {base} = await fixture(t);
    const previous = process.umask(umask);
    t.after(() => process.umask(previous));
    const first = createApiAuth({base, port: 41001, root: ROOT}), second = createApiAuth({base, port: 41002, root: ROOT});
    assert.equal(fs.existsSync(first.keyFile), false, 'nothing is written before start()');
    first.start();
    second.start();
    const one = keyOf(base, 41001), two = keyOf(base, 41002);
    assert.equal(auth.KEY_PATTERN.test(one) && auth.KEY_PATTERN.test(two), true, 'the key is 43 characters of A-Za-z0-9_-');
    assert.notEqual(sha(one), sha(two));
    assert.equal(modeOf(first.keyFile), 0o600);
    assert.equal(modeOf(path.join(base, 'server')), 0o700);
    assert.equal(modeOf(base), 0o700);
    assert.equal(fs.readFileSync(first.keyFile, 'utf8').endsWith('\n'), true);
    assert.deepEqual(fs.readdirSync(path.join(base, 'server')).sort(), ['port-41001.key', 'port-41002.key'], 'no temporary file is left');
    assert.equal(sha(await readApiKey({base, port: 41001})), sha(one));
    first.stopSync();
    first.stopSync();
    second.stopSync();
    assert.deepEqual(fs.readdirSync(path.join(base, 'server')), []);
  });
}

test('Key: stopSync() leaves a newer server\'s file alone; start() replaces a stale file of a dead server', async t => {
  const {base} = await fixture(t);
  const older = createApiAuth({base, port: 41003, root: ROOT});
  older.start();
  const stale = keyOf(base, 41003);
  const newer = createApiAuth({base, port: 41003, root: ROOT});
  newer.start();
  const current = keyOf(base, 41003);
  assert.notEqual(sha(stale), sha(current), 'start() replaced the stale key');
  older.stopSync();
  assert.equal(sha(keyOf(base, 41003)), sha(current), 'the older server left the newer key in place');
  newer.stopSync();
  assert.equal(fs.existsSync(auth.keyFilePath(base, 41003)), false);
  await assert.rejects(readApiKey({base, port: 41003}), {message: auth.noKey(41003, base)});
});

test('Key: a base inside the repository, a relative base and an unwritable key folder stop the start with the text', async t => {
  const {base} = await fixture(t);
  const repository = fs.realpathSync.native(ROOT);
  const inside = path.join(repository, 'tmp', `api-auth-never-created-${process.pid}`);
  assert.throws(() => createApiAuth({base: inside, port: 41004, root: ROOT}).start(), {message: auth.notStarted(reasons.insideRepository(inside, repository))});
  assert.equal(fs.existsSync(inside), false);
  assert.throws(() => createApiAuth({base: 'relative', port: 41004, root: ROOT}).start(), {message: auth.notStarted(reasons.relativeBase('relative'))});
  createApiAuth({base, port: 41005, root: ROOT}).start();
  fs.chmodSync(path.join(base, 'server'), 0o500);
  const blocked = createApiAuth({base, port: 41006, root: ROOT});
  assert.throws(() => blocked.start(), {message: auth.notStarted(`the key file ${blocked.keyFile} could not be written (EACCES).`)});
  assert.equal(fs.existsSync(blocked.keyFile), false, 'a start that failed wrote no key file');
});

// ---------- The gate ----------

test('Gate: a non-API path, the right header, and the view cookie on GET /api/file proceed', async t => {
  const {base} = await fixture(t);
  const {port, key} = await listener(t, base);
  assert.equal((await raw(port, ['GET / HTTP/1.1'])).status, 200);
  assert.equal((await raw(port, ['GET /api HTTP/1.1'])).status, 200, '"/api" does not start with "/api/"');
  assert.equal((await raw(port, ['GET /api/examples HTTP/1.1', `Authorization: Bearer ${key}`])).status, 200);
  assert.equal((await raw(port, ['POST /api/runs HTTP/1.1', `Authorization: Bearer ${key}`, 'Content-Length: 2'], '{}')).status, 200);
  const session = await raw(port, ['POST /api/session HTTP/1.1', `Authorization: Bearer ${key}`, 'Content-Length: 2'], '{}');
  assert.equal(session.status, 200);
  const cookie = /^Set-Cookie: (pilot_view_\d+)=([^;]+); Path=\/api\/file; HttpOnly; SameSite=Strict$/mi.exec(session.head);
  assert.ok(cookie, 'the session sets the view cookie with Path, HttpOnly and SameSite=Strict');
  assert.equal(cookie[1], `pilot_view_${port}`);
  assert.equal(/max-age|expires|secure/i.test(/^Set-Cookie: .*$/mi.exec(session.head)[0]), false);
  const again = await raw(port, ['POST /api/session HTTP/1.1', `Authorization: Bearer ${key}`, 'Content-Length: 2'], '{}');
  assert.equal(again.status, 200, 'a second exchange works too');
  assert.equal(sha(/^Set-Cookie: pilot_view_\d+=([^;]+);/mi.exec(again.head)[1]), sha(cookie[2]));
  const view = cookie[2];
  assert.notEqual(sha(view), sha(key));
  assert.equal((await raw(port, ['GET /api/file?path=x HTTP/1.1', `Cookie: pilot_view_${port}=${view}`])).status, 200);
  // A run's listener may set a same-named cookie: any matching value is accepted.
  assert.equal((await raw(port, ['GET /api/file HTTP/1.1', `Cookie: pilot_view_${port}=wrong; other=1; pilot_view_${port}=${view}`])).status, 200);
  assert.equal((await raw(port, ['GET /api/file HTTP/1.1', `Cookie: pilot_view_${port}=wrong`, `Cookie: pilot_view_${port}=${view}`])).status, 200, 'two Cookie headers are joined');
});

test('Gate: 401 with the exact text for every missing, wrong or misplaced value', async t => {
  const {base} = await fixture(t);
  const {port, key} = await listener(t, base);
  const session = await raw(port, ['POST /api/session HTTP/1.1', `Authorization: Bearer ${key}`, 'Content-Length: 2'], '{}');
  const view = /^Set-Cookie: pilot_view_\d+=([^;]+);/mi.exec(session.head)[1];
  const flipped = key.slice(0, 20) + (key[20] === 'A' ? 'B' : 'A') + key.slice(21);
  assert.equal(flipped.length, key.length);
  const cases = {
    'no header': ['GET /api/examples HTTP/1.1'],
    'a wrong key': ['GET /api/examples HTTP/1.1', `Authorization: Bearer ${crypto.randomBytes(32).toString('base64url')}`],
    'one character changed': ['GET /api/examples HTTP/1.1', `Authorization: Bearer ${flipped}`],
    'a 10,000-character value': ['GET /api/examples HTTP/1.1', `Authorization: Bearer ${'A'.repeat(10000)}`],
    'the key with a 10,000-character tail': ['GET /api/examples HTTP/1.1', `Authorization: Bearer ${key}${'A'.repeat(10000)}`],
    'the view value as a header': ['GET /api/file HTTP/1.1', `Authorization: Bearer ${view}`],
    'the key as a cookie': ['GET /api/file HTTP/1.1', `Cookie: pilot_view_${port}=${key}`],
    'the view cookie on GET /api/examples': ['GET /api/examples HTTP/1.1', `Cookie: pilot_view_${port}=${view}`],
    'the view cookie on POST /api/file': ['POST /api/file HTTP/1.1', `Cookie: pilot_view_${port}=${view}`, 'Content-Length: 0'],
    'the view cookie under another port\'s name': ['GET /api/file HTTP/1.1', `Cookie: pilot_view_1=${view}`],
    'an empty Authorization header': ['GET /api/examples HTTP/1.1', 'Authorization: '],
    'Bearer with no value': ['GET /api/examples HTTP/1.1', 'Authorization: Bearer'],
    'Bearer and a space': ['GET /api/examples HTTP/1.1', 'Authorization: Bearer '],
    'lower-case bearer': ['GET /api/examples HTTP/1.1', `Authorization: bearer ${key}`],
    'another scheme': ['GET /api/examples HTTP/1.1', `Authorization: Basic ${key}`],
    'two Authorization headers, both right': ['GET /api/examples HTTP/1.1', `Authorization: Bearer ${key}`, `Authorization: Bearer ${key}`],
    'two Authorization headers, wrong first': ['GET /api/examples HTTP/1.1', 'Authorization: Bearer wrong', `Authorization: Bearer ${key}`],
    'the key with an inner space': ['GET /api/examples HTTP/1.1', `Authorization: Bearer  ${key}`],
    'the session without a key': ['POST /api/session HTTP/1.1', 'Content-Length: 2'],
    'the cookie name twice, both wrong': ['GET /api/file HTTP/1.1', `Cookie: pilot_view_${port}=a; pilot_view_${port}=b`]
  };
  for (const [label, lines] of Object.entries(cases)) {
    const response = await raw(port, lines, lines.some(line => line === 'Content-Length: 2') ? '{}' : '');
    assert.equal(refused(response), true, `${label}: status ${response.status}`);
    assert.match(response.head, /^WWW-Authenticate: Bearer$/mi, label);
    assert.match(response.head, /^Cache-Control: no-store$/mi, label);
    assert.equal(response.body.includes(key) || response.head.includes(key), false, label);
  }
});

test('Gate: Node trims a trailing space from the header value, so a key followed by a space is the key', async t => {
  const {base} = await fixture(t);
  const {port, key} = await listener(t, base);
  // Recorded behaviour, not a design choice: HTTP strips optional whitespace around a field value.
  assert.equal((await raw(port, ['GET /api/examples HTTP/1.1', `Authorization: Bearer ${key} `])).status, 200);
  assert.equal((await raw(port, ['GET /api/examples HTTP/1.1', `Authorization: Bearer ${key}\t`])).status, 200);
  // A newline ends the header line, so "key + newline" is the key; a bare LF inside a value is rejected by the parser.
  const injected = await raw(port, ['GET /api/examples HTTP/1.1', `Authorization: Bearer ${key}\nX: y`]);
  assert.ok([200, 400].includes(injected.status), `status ${injected.status}`);
});

test('Gate: a refused request with a body is answered before any body byte arrives, the connection is closed, and the server reads only a bounded part of a body', async t => {
  const {base} = await fixture(t);
  let gate;
  const reads = [];
  const server = http.createServer((req, res) => {
    req.socket.once('close', () => reads.push(req.socket.bytesRead));
    if (gate.gate(req, res)) res.end('{}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  gate = createApiAuth({base, port, root: ROOT});
  t.after(() => {
    server.closeAllConnections();
    return new Promise(resolve => server.close(resolve));
  });
  const declared = 8 * 1024 * 1024;
  const head = ['POST /api/examples HTTP/1.1', `Host: 127.0.0.1:${port}`, 'Content-Type: application/json', `Content-Length: ${declared}`, '', ''].join('\r\n');
  const exchange = body => new Promise((resolve, reject) => {
    const socket = net.connect(port, '127.0.0.1');
    let data = '';
    const timer = setTimeout(() => socket.destroy(new Error('the server did not answer and close the connection within 5 s')), 5000);
    socket.on('data', chunk => (data += chunk));
    socket.on('error', error => {
      // A client still sending may see the connection cut once the answer was written.
      if (!['EPIPE', 'ECONNRESET'].includes(error.code)) reject(error);
    });
    socket.on('close', () => {
      clearTimeout(timer);
      const [answerHead, ...rest] = data.split('\r\n\r\n');
      resolve({status: Number(/^HTTP\/1\.1 (\d+)/.exec(answerHead)?.[1]), head: answerHead, body: rest.join('\r\n\r\n')});
    });
    socket.write(head);
    if (body) socket.write(body);
  });
  // The headers alone: the answer comes without one byte of the body, and the server closes the connection.
  const early = await exchange(null);
  assert.equal(refused(early), true, `status ${early.status}`);
  assert.match(early.head, /^Connection: close$/mi);
  // The whole body sent at once: the server reads only a bounded part before it cuts the connection.
  await exchange(Buffer.alloc(declared, 120));
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(reads.length, 2);
  assert.ok(reads[1] < 1024 * 1024, `the server read ${reads[1]} bytes of an ${declared}-byte body`);
});

/** Runs a listener in a child process and sends it 200 parallel 1 MB uploads without a key. The child samples its heap
 * and external buffers every 5 ms and reports the peak growth. Mode 'gate' is the real gate; mode 'buffer' is a
 * control that reads and keeps every body, to show that the measurement sees buffering. */
async function uploadPeak(t, dir, base, mode) {
  const script = path.join(dir, `memory-${mode}.mjs`);
  fs.writeFileSync(script, `import http from 'node:http';
import {createApiAuth} from ${JSON.stringify(path.join(TOOLS, 'api-auth.mjs'))};
const used = () => { const m = process.memoryUsage(); return m.heapUsed + m.external + m.arrayBuffers; };
const kept = [];
let gate, peak = 0, start = 0;
const server = http.createServer((req, res) => {
  if (${JSON.stringify(mode)} === 'buffer') { req.on('data', chunk => kept.push(chunk)); req.on('end', () => res.end('{}')); return; }
  if (gate.gate(req, res)) res.end('{}');
});
server.listen(0, '127.0.0.1', () => {
  gate = createApiAuth({base: ${JSON.stringify(base)}, port: server.address().port, root: ${JSON.stringify(ROOT)}});
  globalThis.gc();
  start = used();
  setInterval(() => { peak = Math.max(peak, used() - start); }, 5);
  process.stdout.write(JSON.stringify({port: server.address().port}) + '\\n');
});
process.stdin.on('data', () => process.stdout.write(JSON.stringify({peak}) + '\\n'));
`);
  const child = spawn(process.execPath, ['--expose-gc', script], {stdio: ['pipe', 'pipe', 'inherit']});
  t.after(() => child.kill('SIGKILL'));
  const next = () => new Promise(resolve => child.stdout.once('data', chunk => resolve(JSON.parse(String(chunk).trim().split('\n').pop()))));
  const {port} = await next();
  const body = Buffer.alloc(1024 * 1024, 120);
  const statuses = await Promise.all(Array.from({length: 200}, () => new Promise(resolve => {
    const request = http.request({host: '127.0.0.1', port, method: 'POST', path: '/api/examples/x/files', headers: {'Content-Type': 'application/json', 'Content-Length': body.length}, agent: false}, response => {
      response.resume();
      resolve(response.statusCode);
    });
    // The gate answers and closes without reading, so a client still sending may see the connection reset.
    request.on('error', error => resolve(error.code));
    request.end(body);
  })));
  await new Promise(resolve => setTimeout(resolve, 50));
  child.stdin.write('report\n');
  const {peak} = await next();
  return {statuses, peak: peak / (1024 * 1024)};
}

test('Concurrency (approximation with a gate-only listener): 200 parallel unauthenticated 1 MB uploads leave memory flat', async t => {
  const {dir, base} = await fixture(t);
  const gate = await uploadPeak(t, dir, base, 'gate');
  const control = await uploadPeak(t, dir, base, 'buffer');
  const count = list => Object.entries(list.reduce((counts, status) => ({...counts, [status]: (counts[status] || 0) + 1}), {})).map(([status, n]) => `${status} x${n}`).join(', ');
  t.diagnostic(`gate: ${count(gate.statuses)}; peak heap and buffer growth ${gate.peak.toFixed(1)} MB`);
  t.diagnostic(`buffering control: ${count(control.statuses)}; peak growth ${control.peak.toFixed(1)} MB`);
  assert.equal(gate.statuses.includes(200), false, 'no upload passed the gate');
  assert.ok(gate.statuses.includes(401));
  assert.ok(control.peak > 100, 'the measurement sees a server that buffers');
  // Measured on macOS 27: about 29 MB with the connection cut, 77 MB when Node read and discarded the bodies.
  assert.ok(gate.peak < 48, `the gate's peak growth was ${gate.peak.toFixed(1)} MB`);
});

// ---------- Console ----------

test('Announce: the link goes only to a terminal; otherwise the line names the link command and holds no key', async t => {
  const {base} = await fixture(t);
  const gate = createApiAuth({base, port: 41010, root: ROOT});
  gate.start();
  t.after(() => gate.stopSync());
  const key = keyOf(base, 41010);
  const terminal = capture({tty: true}), pipe = capture();
  gate.announce(terminal);
  gate.announce(pipe);
  const expected = `Sign in by opening: ${auth.signInLink(41010, key)}\nThis link works until the server stops. Do not paste it into documents or prompts.\n`;
  assert.equal(sha(terminal.text), sha(expected));
  assert.equal(pipe.text, 'Sign-in link not printed because this output is not a terminal. Run in a terminal on this machine: node tools/api-auth.mjs link --port 41010\n');
  assert.equal(pipe.text.includes(key), false);
});

// ---------- The link command ----------

test('Link: exit 0 prints the address, 1 without a key file, 2 for usage or a pipe without --allow-pipe, 4 for a refused base', async t => {
  const {base, dir} = await fixture(t);
  const gate = createApiAuth({base, port: 41011, root: ROOT});
  gate.start();
  t.after(() => gate.stopSync());
  const key = keyOf(base, 41011);
  const env = {AUDIT_AGENT_HOME: base};
  const run = async (argv, options = {}) => {
    const stdout = capture(options), stderr = capture();
    const code = await auth.main(argv, {env, home: dir, stdout, stderr, root: ROOT, ...options.extra});
    return {code, stdout: stdout.text, stderr: stderr.text};
  };
  const printed = await run(['link', '--port', '41011', '--allow-pipe']);
  assert.equal(printed.code, 0);
  assert.equal(sha(printed.stdout), sha(`http://127.0.0.1:41011/#key=${key}\n`));
  // A boolean, so that a failure never prints the line, which holds the key.
  assert.equal(/#key=[A-Za-z0-9_-]{43}\n$/.test(printed.stdout), true, 'the line ends with #key= and 43 key characters');
  assert.equal((await run(['link', '--port', '41011'], {tty: true})).code, 0, 'a terminal needs no --allow-pipe');
  const missing = await run(['link', '--port', '41012', '--allow-pipe']);
  assert.equal(missing.code, 1);
  assert.equal(missing.stderr, auth.noKey(41012, base) + '\n');
  for (const argv of [[], ['links'], ['link', '--port'], ['link', '--port', 'x'], ['link', '--port', '70000', '--allow-pipe'], ['link', '--bogus']]) {
    assert.equal((await run(argv)).code, 2, argv.join(' '));
  }
  const piped = await run(['link', '--port', '41011']);
  assert.equal(piped.code, 2);
  assert.equal(piped.stdout, '');
  const relative = await run(['link', '--port', '41011', '--allow-pipe'], {extra: {env: {AUDIT_AGENT_HOME: 'relative'}}});
  assert.equal(relative.code, 4);
  assert.equal(relative.stderr, homeUnavailable(reasons.relativeBase('relative')) + '\n');
  // The command itself, as a process whose standard output is a pipe.
  const child = await new Promise(resolve => {
    const process_ = spawn(process.execPath, [path.join(TOOLS, 'api-auth.mjs'), 'link', '--port', '41011'], {env: {PATH: process.env.PATH, AUDIT_AGENT_HOME: base}, stdio: ['ignore', 'pipe', 'pipe']});
    let out = '';
    process_.stdout.on('data', chunk => (out += chunk));
    process_.on('close', code => resolve({code, out}));
  });
  assert.equal(child.code, 2);
  assert.equal(child.out, '');
});

test('Orphan: the address a killed server left answers 401 on a new server of the same port', async t => {
  const {base} = await fixture(t);
  const {port, key} = await listener(t, base);
  const killed = key;
  const replacement = createApiAuth({base, port, root: ROOT});
  replacement.start(); // the next server on that port
  t.after(() => replacement.stopSync());
  const server = http.createServer((req, res) => {
    if (replacement.gate(req, res)) res.end('{}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  assert.equal(refused(await raw(server.address().port, ['GET /api/examples HTTP/1.1', `Authorization: Bearer ${killed}`])), true);
});

// The probe with a real second server (that it never reaches start()) belongs to the server wiring's tests.
test('Concurrency: Node refuses a second bind of a port in use, the precondition for writing the key only after listen()', async t => {
  const {base} = await fixture(t);
  const {port} = await listener(t, base);
  const before = sha(keyOf(base, port));
  const second = http.createServer();
  const failed = await new Promise(resolve => {
    second.once('error', error => resolve(error.code));
    second.listen(port, '127.0.0.1', () => resolve('bound'));
  });
  assert.equal(failed, 'EADDRINUSE');
  // The server writes its key only after listen succeeded, so start() is never reached here.
  assert.equal(sha(keyOf(base, port)), before);
});

// ---------- Findings of the independent code review (B2, B3, B5) ----------

function deadPid() {
  const child = spawnSync(process.execPath, ['-e', 'process.stdout.write(String(process.pid))'], {encoding: 'utf8'});
  return Number(child.stdout);
}

test('Key [B2 M3]: start() replaces a link planted at the key path and a stale 0644 key file by a regular 0600 file; the link target is unchanged', async t => {
  const {base, dir} = await fixture(t);
  const setup = createApiAuth({base, port: 41020, root: ROOT});
  setup.start();
  setup.stopSync();
  const target = path.join(dir, 'planted-target');
  fs.writeFileSync(target, 'planted\n', {mode: 0o600});
  fs.symlinkSync(target, auth.keyFilePath(base, 41020));
  const linked = createApiAuth({base, port: 41020, root: ROOT});
  linked.start();
  t.after(() => linked.stopSync());
  assert.equal(fs.readFileSync(target, 'utf8'), 'planted\n', 'the link target is unchanged');
  assert.equal(fs.lstatSync(linked.keyFile).isFile(), true, 'the key file is a regular file, not the link');
  assert.equal(modeOf(linked.keyFile), 0o600);
  const stale = auth.keyFilePath(base, 41021);
  fs.writeFileSync(stale, 'stale\n');
  fs.chmodSync(stale, 0o644);
  const fresh = createApiAuth({base, port: 41021, root: ROOT});
  fresh.start();
  t.after(() => fresh.stopSync());
  assert.equal(modeOf(stale), 0o600, 'a stale 0644 key file is replaced by a 0600 one');
  assert.equal(auth.KEY_PATTERN.test(keyOf(base, 41021)), true);
});

test('Key [B5]: start() removes temporary key files that a dead writer left for its port, and nothing else', async t => {
  const {base} = await fixture(t);
  const setup = createApiAuth({base, port: 41022, root: ROOT});
  setup.start();
  setup.stopSync();
  const server = path.join(base, 'server'), dead = deadPid(), live = process.ppid;
  const names = {
    deadWriter: `port-41022.key.${dead}.0123abcd.tmp`,
    liveWriter: `port-41022.key.${live}.0123abcd.tmp`,
    otherPort: `port-41023.key.${dead}.0123abcd.tmp`,
    otherName: `port-41022.key.${dead}.not-hex.tmp`
  };
  for (const name of Object.values(names)) fs.writeFileSync(path.join(server, name), 'fixture\n', {mode: 0o600});
  const gate = createApiAuth({base, port: 41022, root: ROOT});
  gate.start();
  t.after(() => gate.stopSync());
  const left = fs.readdirSync(server);
  assert.equal(left.includes(names.deadWriter), false, 'the dead writer\'s temporary file is removed');
  for (const name of [names.liveWriter, names.otherPort, names.otherName]) assert.equal(left.includes(name), true, name);
});

test('Key [B2 M11]: start() refuses a server/ folder with access for others, or a link in its place, and writes no key', async t => {
  const {base, dir} = await fixture(t);
  const setup = createApiAuth({base, port: 41024, root: ROOT});
  setup.start();
  setup.stopSync();
  const server = path.join(base, 'server');
  fs.chmodSync(server, 0o755);
  const open = createApiAuth({base, port: 41024, root: ROOT});
  assert.throws(() => open.start(), {message: auth.notStarted(reasons.notPrivate(server, 'mode 0755'))});
  assert.equal(fs.existsSync(auth.keyFilePath(base, 41024)), false);
  fs.rmdirSync(server);
  const elsewhere = path.join(dir, 'elsewhere');
  fs.mkdirSync(elsewhere, {mode: 0o700});
  fs.symlinkSync(elsewhere, server);
  assert.throws(() => createApiAuth({base, port: 41024, root: ROOT}).start(), {message: auth.notStarted(reasons.notPrivate(server, 'a link'))});
  assert.deepEqual(fs.readdirSync(elsewhere), [], 'nothing was written through the link');
});

test('Gate [B3]: a long junk cookie beside the right view value still reads GET /api/file; an over-long view value does not', async t => {
  const {base} = await fixture(t);
  const {port, key} = await listener(t, base);
  const session = await raw(port, ['POST /api/session HTTP/1.1', `Authorization: Bearer ${key}`, 'Content-Length: 2'], '{}');
  const view = /^Set-Cookie: pilot_view_\d+=([^;]+);/mi.exec(session.head)[1];
  const junk = `junk=${'j'.repeat(12000)}`;
  const passed = await raw(port, ['GET /api/file?path=x HTTP/1.1', `Cookie: ${junk}; pilot_view_${port}=${view}`]);
  assert.equal(passed.status, 200, `status ${passed.status}`);
  const split = await raw(port, ['GET /api/file?path=x HTTP/1.1', `Cookie: ${junk}`, `Cookie: pilot_view_${port}=${view}`]);
  assert.equal(split.status, 200, `two Cookie headers: status ${split.status}`);
  assert.equal(refused(await raw(port, ['GET /api/file HTTP/1.1', `Cookie: pilot_view_${port}=${view}${'A'.repeat(300)}`])), true, 'the per-value bound stays');
});

test('Link [B5]: --allow-pipe refuses a standard output that is a regular file, and nothing is written to it', async t => {
  const {base, dir} = await fixture(t);
  const gate = createApiAuth({base, port: 41025, root: ROOT});
  gate.start();
  t.after(() => gate.stopSync());
  const output = path.join(dir, 'link-output');
  const fd = fs.openSync(output, 'w', 0o600);
  const child = await new Promise(resolve => {
    const process_ = spawn(process.execPath, [path.join(TOOLS, 'api-auth.mjs'), 'link', '--port', '41025', '--allow-pipe'], {env: {PATH: process.env.PATH, AUDIT_AGENT_HOME: base}, stdio: ['ignore', fd, 'pipe']});
    let err = '';
    process_.stderr.on('data', chunk => (err += chunk));
    process_.on('close', code => resolve({code, err}));
  });
  fs.closeSync(fd);
  assert.equal(child.code, 2, child.err);
  assert.equal(fs.statSync(output).size, 0, 'the file stays empty');
  assert.match(child.err, /standard output is a file/);
});

test('Confirmation record [B2 M9, B5]: a linked, oversized or group-readable record is not trusted; clearing reports a failure', async t => {
  const {base, dir} = await fixture(t);
  const gate = createApiAuth({base, port: 41026, root: ROOT});
  gate.start();
  t.after(() => gate.stopSync());
  const record = path.join(base, 'server/ala-location');
  const location = '/opt/AdvancedLanguageAgent';
  auth.writeALAConfirmation(base, location);
  assert.equal(auth.readALAConfirmation(base), location);
  // A link to a file holding the location.
  const elsewhere = path.join(dir, 'elsewhere-record');
  fs.writeFileSync(elsewhere, location + '\n', {mode: 0o600});
  fs.unlinkSync(record);
  fs.symlinkSync(elsewhere, record);
  assert.equal(auth.readALAConfirmation(base), null, 'a linked record');
  assert.equal(auth.savedLocationConfirmed(base, location), false);
  auth.clearALAConfirmation(base);
  assert.equal(fs.existsSync(elsewhere), true, 'clearing removes the link, not its target');
  assert.equal(fs.existsSync(record), false);
  // An otherwise valid record over 8192 bytes.
  const long = `/opt/${'a'.repeat(9000)}`;
  fs.writeFileSync(record, long, {mode: 0o600});
  assert.equal(auth.readALAConfirmation(base), null, 'an oversized record');
  // A record with access for others.
  fs.writeFileSync(record, location + '\n');
  fs.chmodSync(record, 0o644);
  assert.equal(auth.readALAConfirmation(base), null, 'a group-readable record');
  fs.chmodSync(record, 0o600);
  assert.equal(auth.readALAConfirmation(base), location);
  // Clearing: an absent record is fine; one that cannot be removed is reported.
  auth.clearALAConfirmation(base);
  auth.clearALAConfirmation(base);
  fs.mkdirSync(record);
  assert.throws(() => auth.clearALAConfirmation(base), {message: `The ALA location confirmation ${record} could not be removed (EISDIR).`});
  fs.rmdirSync(record);
});

test('Confirmation record [A1]: clearing checks server/ as writing does; a link or an open folder in its place is reported and nothing is removed', async t => {
  const {base, dir} = await fixture(t);
  const server = path.join(base, 'server');
  // No server/ folder yet: nothing to clear.
  fs.mkdirSync(base, {mode: 0o700});
  auth.clearALAConfirmation(base);
  // server/ is a link to another folder that holds a file of the record's name: it survives.
  const elsewhere = path.join(dir, 'elsewhere');
  fs.mkdirSync(elsewhere, {mode: 0o700});
  fs.writeFileSync(path.join(elsewhere, 'ala-location'), '/opt/AdvancedLanguageAgent\n', {mode: 0o600});
  fs.symlinkSync(elsewhere, server);
  assert.throws(() => auth.clearALAConfirmation(base), {message: `The ALA location confirmation ${path.join(server, 'ala-location')} could not be removed: ${reasons.notPrivate(server, 'a link')}`});
  assert.equal(fs.existsSync(path.join(elsewhere, 'ala-location')), true, 'the file behind the link was not removed');
  // server/ with access for others: the record stays.
  fs.unlinkSync(server);
  fs.mkdirSync(server, {mode: 0o700});
  auth.writeALAConfirmation(base, '/opt/AdvancedLanguageAgent');
  fs.chmodSync(server, 0o755);
  assert.throws(() => auth.clearALAConfirmation(base), {message: `The ALA location confirmation ${path.join(server, 'ala-location')} could not be removed: ${reasons.notPrivate(server, 'mode 0755')}`});
  assert.equal(fs.existsSync(path.join(server, 'ala-location')), true, 'the record stays');
  fs.chmodSync(server, 0o700);
  auth.clearALAConfirmation(base);
  assert.equal(fs.existsSync(path.join(server, 'ala-location')), false);
});

// ---------- Confirmation record of a saved ALA location ----------

test('Confirmation record: written with mode 0600 under server/, matched exactly, cleared', async t => {
  const {base} = await fixture(t);
  const gate = createApiAuth({base, port: 41013, root: ROOT});
  gate.start();
  t.after(() => gate.stopSync());
  assert.equal(auth.readALAConfirmation(base), null);
  assert.equal(auth.savedLocationConfirmed(base, '/opt/AdvancedLanguageAgent'), false);
  auth.writeALAConfirmation(base, '/opt/AdvancedLanguageAgent');
  assert.equal(modeOf(path.join(base, 'server/ala-location')), 0o600);
  assert.equal(auth.savedLocationConfirmed(base, '/opt/AdvancedLanguageAgent'), true);
  assert.equal(auth.savedLocationConfirmed(base, '/opt/AdvancedLanguageAgent/'), false);
  assert.throws(() => auth.writeALAConfirmation(base, '/a\nb'), TypeError);
  auth.clearALAConfirmation(base);
  assert.equal(auth.readALAConfirmation(base), null);
});

// ---------- H-A8: the page's start-up code ----------

const {default: vm} = await import('node:vm');

/** The whole script of tools/audit-ui.html in a node:vm context with stand-ins for location, history, localStorage,
 * fetch and the timers. `respond(url, init)` answers each fetch; every call is recorded. The page's elements are
 * permissive stand-ins that accept any property or call. */
async function pageContext({host = '127.0.0.1:8787', hash = '', stored = null, respond}) {
  const html = fs.readFileSync(path.join(TOOLS, 'audit-ui.html'), 'utf8');
  const script = html.split('<script>')[1].split('</script>')[0];
  const element = () => {
    const store = {dataset: {}, style: {}, classList: {toggle() {}, add() {}, remove() {}, contains: () => false}};
    return new Proxy(function () {}, {
      get(target, property) {
        if (property in store) return store[property];
        if (property === 'then') return undefined;
        if (property === Symbol.toPrimitive) return () => '';
        if (property === Symbol.iterator) return function* () {};
        if (property === 'isConnected') return true;
        return element();
      },
      set(target, property, value) {
        store[property] = value;
        return true;
      },
      apply: () => element()
    });
  };
  const elements = new Map(), calls = [], storage = new Map(stored ? [['lean-documents-pilot-key', stored]] : []), timers = {intervals: [], cleared: []};
  const location = {protocol: 'http:', host, hash, pathname: '/', search: ''};
  const context = vm.createContext({
    console, URLSearchParams, setTimeout, clearTimeout,
    location,
    history: {replaced: [], replaceState(state, title, url) { this.replaced.push(url); location.hash = ''; }},
    localStorage: {getItem: name => storage.get(name) ?? null, setItem: (name, value) => storage.set(name, String(value)), removeItem: name => storage.delete(name)},
    document: {getElementById: id => elements.get(id) || elements.set(id, element()).get(id), querySelector: () => element(), querySelectorAll: () => [], createElement: () => element()},
    setInterval: fn => { timers.intervals.push(fn); return 41; },
    clearInterval: id => timers.cleared.push(id),
    fetch: async (url, init = {}) => {
      calls.push({url, method: init.method || 'GET', headers: {...(init.headers || {})}, body: init.body});
      return respond(url, init);
    }
  });
  vm.runInContext(script, context);
  const settle = () => new Promise(resolve => setTimeout(resolve, 50));
  await settle();
  return {context, calls, storage, timers, location, elements, settle, run: code => vm.runInContext(code, context)};
}
const reply = (status, body) => ({status, ok: status >= 200 && status < 300, statusText: String(status), json: async () => body});
const pageAnswers = (url, init) => {
  if (url === '/api/session') return reply(200, {ok: true});
  if (url === '/api/agent-settings') return reply(200, {agent: 'codex', model: '', effort: '', ala: {found: true, checked: []}, python: {found: true}, agentHome: {ready: true, base: '/b', agents: {codex: {loggedIn: true, source: 'file', variables: []}}, leftovers: []}});
  if (url === '/api/examples') return reply(200, []);
  return reply(404, {error: 'unknown'});
};

test('H-A8: the page takes the key from #key= on 127.0.0.1, sends it on every call, and a 401 removes it and stops polling', async () => {
  const key = crypto.randomBytes(32).toString('base64url');
  const page = await pageContext({hash: `#key=${key}`, respond: pageAnswers});
  assert.equal(sha(page.storage.get('lean-documents-pilot-key')), sha(key), 'the key is stored under lean-documents-pilot-key');
  assert.deepEqual(page.run('history.replaced'), ['/'], 'the fragment is removed from the address');
  assert.equal(page.location.hash, '');
  const sessions = page.calls.filter(call => call.url === '/api/session');
  assert.equal(sessions.length, 1, 'POST /api/session is called once per load');
  assert.equal(sessions[0].method, 'POST'); assert.equal(page.calls[0].url, '/api/session', 'the session comes first');
  assert.ok(page.calls.length >= 3, page.calls.map(call => call.url).join(', '));
  for (const call of page.calls) assert.equal(call.headers.Authorization === `Bearer ${key}`, true, `${call.method} ${call.url} carries the key`);
  assert.equal(page.timers.intervals.length, 1, 'polling started');
  // A later poll gets 401: the stored key is removed, the notice shows the server's text, and polling stops.
  const before = page.calls.length;
  page.context.fetch = async (url, init = {}) => { page.calls.push({url, method: init.method || 'GET', headers: {...(init.headers || {})}}); return reply(401, {error: API_UNAUTHORIZED}); };
  await page.timers.intervals[0](); await page.settle();
  assert.equal(page.storage.has('lean-documents-pilot-key'), false, 'a 401 removes the stored key');
  assert.deepEqual(page.timers.cleared, [41], 'a 401 stops polling');
  assert.equal(page.elements.get('signin-alert').textContent, API_UNAUTHORIZED);
  const after = page.calls.length; assert.ok(after > before);
  await page.timers.intervals[0](); await page.settle();
  assert.equal(page.calls.length, after, 'a signed-out page makes no further calls');
  await assert.rejects(page.run("api('/api/examples')"), {message: API_UNAUTHORIZED});
});

test('H-A8: on localhost nothing is stored or sent, and the fragment is still removed', async () => {
  const key = crypto.randomBytes(32).toString('base64url');
  for (const host of ['localhost:8787', '[::1]:8787', '127.0.0.1']) {
    const page = await pageContext({host, hash: `#key=${key}`, respond: pageAnswers});
    assert.equal(page.storage.size, 0, `${host}: nothing is stored`);
    assert.deepEqual(page.run('history.replaced'), ['/'], `${host}: the fragment is removed`);
    assert.equal(page.calls.some(call => call.url === '/api/session'), false, `${host}: no session exchange`);
    for (const call of page.calls) assert.equal('Authorization' in call.headers, false, `${host}: ${call.url} carries no key`);
  }
  // A stored key from before is not sent from the wrong host either.
  const stale = await pageContext({host: 'localhost:8787', stored: key, respond: pageAnswers});
  for (const call of stale.calls) assert.equal('Authorization' in call.headers, false);
  // A malformed fragment is removed and not stored.
  const malformed = await pageContext({hash: '#key=short', respond: pageAnswers});
  assert.equal(malformed.storage.size, 0); assert.deepEqual(malformed.run('history.replaced'), ['/']);
});

test('H-A8: an upload cut off by the server after its 401 is noticed through one bodiless authenticated call', async () => {
  const key = crypto.randomBytes(32).toString('base64url');
  const page = await pageContext({stored: key, respond: pageAnswers});
  assert.equal(page.storage.get('lean-documents-pilot-key'), key);
  const checks = [];
  page.context.fetch = async (url, init = {}) => {
    if (init.body !== undefined) throw new TypeError('fetch failed'); // the connection was reset after a 401
    checks.push({url, method: init.method || 'GET', headers: {...(init.headers || {})}, body: init.body});
    return reply(401, {error: API_UNAUTHORIZED});
  };
  await assert.rejects(page.run("api('/api/examples/x/files', {role: 'source', files: []})"), {message: 'fetch failed'});
  assert.equal(checks.length, 1, 'exactly one follow-up call');
  assert.deepEqual([checks[0].url, checks[0].method, checks[0].body, checks[0].headers.Authorization], ['/api/session', 'GET', undefined, `Bearer ${key}`]);
  assert.equal(page.storage.has('lean-documents-pilot-key'), false, 'the 401 of the follow-up signs the page out');
  assert.deepEqual(page.timers.cleared, [41]);
  // A network error while the key is still valid (the follow-up gets 404) keeps the page signed in.
  const valid = await pageContext({stored: key, respond: pageAnswers});
  valid.context.fetch = async (url, init = {}) => { if (init.body !== undefined) throw new TypeError('fetch failed'); return reply(404, {}); };
  await assert.rejects(valid.run("api('/api/runs', {})"), {message: 'fetch failed'});
  assert.equal(valid.storage.get('lean-documents-pilot-key'), key); assert.deepEqual(valid.timers.cleared, []);
});
