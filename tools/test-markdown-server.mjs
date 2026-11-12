/** Isolated integration checks; all inputs, logs and runs stay under tmp/. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { spawn, execFile, execFileSync } from 'node:child_process';
import { promisify } from 'node:util';
import { once } from 'node:events';
import net from 'node:net';
import fs from 'node:fs/promises';
import fsSync from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { readExample } from './markdown-store.mjs';
import { createFakeALA } from './fake-ala-fixture.mjs';
import { leanStatus, resolvePython } from './toolchain.mjs';
import { noLogin, homeUnavailable, reasons } from './agent-home.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
await fs.mkdir(path.join(root, 'tmp/markdown-server-tests'), { recursive: true });
const suite = await fs.mkdtemp(path.join(root, 'tmp/markdown-server-tests/run-'));
console.log(`Retained isolated server checks: ${suite}`);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
// Explicit simulation: the servers below never run a script. They get an interpreter that only answers the version
// probe of the Python resolver (AUDIT_PYTHON), so these tests do not depend on the host having Python 3.12 or later.
async function simulatedPython(name, version) {
  const file = path.join(suite, name);
  await fs.writeFile(file, `#!/bin/sh\nif [ "$1" = "-I" ]; then echo ${version}; exit 0; fi\necho "simulated python: no script is run in this test" >&2\nexit 97\n`);
  await fs.chmod(file, 0o755);
  return file;
}
const simulatedNewPython = await simulatedPython('simulated-python-3.12', '3.12.0');
// Both audit prompts must run the helper through the named interpreter. A bare script path would run through its
// shebang, which is whatever python3 the sandbox shows (3.9.6 on a Mac without the pinned interpreter).
async function assertHelperRunsThroughInterpreter(prompt, helperRelative) {
  const helper = path.join(await fs.realpath(root), helperRelative);
  const parts = prompt.split(helper);
  assert.ok(parts.length - 1 >= 3, `${helperRelative} is invoked for prepare, check and render`);
  for (const before of parts.slice(0, -1)) assert.ok(before.endsWith(`${simulatedNewPython} `), `${helperRelative} must follow the interpreter, found "...${before.slice(-60)}"`);
  assert.match(prompt, /Run every Python script with the absolute interpreter .* and never with bare python3/);
  assert.match(prompt, /Never run a \.py file directly/);
}

// Every server gets its own agent-home base in the host temporary folder, outside the repository, never the real one.
// Its template holds fixture logins whose token is random and is never written into the repository: assertions look
// for its absence. The bases are removed at exit (from Node, by guarded path).
const TEMPORARY = fsSync.realpathSync.native(os.tmpdir());
const TOKEN = `fixture-token-${crypto.randomBytes(12).toString('hex')}`;
const fixtures = [];
process.on('exit', () => { for (const dir of fixtures) if (path.dirname(dir) === TEMPORARY && path.basename(dir).startsWith('markdown-server-base-')) fsSync.rmSync(dir, { recursive: true, force: true }); });
async function fixtureBase(kind = 'logins') {
  const dir = await fs.realpath(await fs.mkdtemp(path.join(TEMPORARY, 'markdown-server-base-'))); fixtures.push(dir);
  const base = path.join(dir, 'base'), template = path.join(base, 'template');
  for (const folder of [base, template]) { await fs.mkdir(folder, { mode: 0o700 }); await fs.chmod(folder, 0o700); }
  const files = kind === 'empty' ? {} : { '.codex/auth.json': JSON.stringify({ tokens: { access_token: TOKEN } }), '.claude/.credentials.json': JSON.stringify({ claudeAiOauth: { accessToken: TOKEN + '-claude' } }), ...(kind === 'foreign' ? { '.codex/config.toml': 'model = "fixture"\n' } : {}) };
  for (const [relative, content] of Object.entries(files)) {
    const folder = path.join(template, path.dirname(relative)); await fs.mkdir(folder, { recursive: true, mode: 0o700 }); await fs.chmod(folder, 0o700);
    await fs.writeFile(path.join(template, relative), content, { mode: 0o600 }); await fs.chmod(path.join(template, relative), 0o600);
  }
  return { dir, base };
}
// Test servers never inherit credential variables or an agent-home location from the runner's shell.
const CREDENTIALS = /^(OPENAI|ANTHROPIC|CLAUDE_CODE|CLAUDE|CODEX|OPENCODE|PI_CODING|OMP|GEMINI|GOOGLE|OPENROUTER|XAI|MISTRAL|GROQ|DEEPSEEK|AZURE_OPENAI)_|^AUDIT_AGENT_HOME$|^ALA_CLI$/;
const cleanEnvironment = () => Object.fromEntries(Object.entries(process.env).filter(([key]) => !CREDENTIALS.test(key)));
async function freePort() { const probe = net.createServer(); probe.listen(0, '127.0.0.1'); await once(probe, 'listening'); const port = probe.address().port; await new Promise(resolve => probe.close(resolve)); return port; }
/** One raw HTTP request, so the Host header and repeated or unusual headers reach the server exactly as written. */
function raw(port, lines, body = '', { host = `127.0.0.1:${port}`, wait = null } = {}) {
  return new Promise((resolve, reject) => {
    const socket = net.connect(port, '127.0.0.1'); let data = '';
    socket.on('data', chunk => { data += chunk; }); socket.on('error', error => (data ? resolve(parse()) : reject(error)));
    const dechunk = text => { let out = '', at = 0; for (;;) { const end = text.indexOf('\r\n', at); if (end < 0) return out; const size = parseInt(text.slice(at, end), 16); if (!size) return out; out += text.slice(end + 2, end + 2 + size); at = end + 4 + size; } };
    // The last response on the connection (after a possible "100 Continue"), with a chunked body decoded.
    const parse = () => { const final = data.replace(/^HTTP\/1\.1 100 Continue\r\n\r\n/, ''); const [head, ...rest] = final.split('\r\n\r\n'); const body = rest.join('\r\n\r\n'); return { status: Number(/^HTTP\/1\.1 (\d+)/.exec(head)?.[1]), head, body: /^Transfer-Encoding: chunked$/mi.test(head) ? dechunk(body) : body, data }; };
    socket.on('close', () => resolve(parse()));
    socket.write([...lines, `Host: ${host}`, 'Connection: close', '', ''].join('\r\n'));
    // No half-close: Node drops a half-closed connection before an asynchronous answer; Connection: close ends it.
    if (wait === null) socket.write(body); else setTimeout(() => { if (!socket.destroyed) socket.write(body); }, wait);
  });
}

async function startServer(name, mode, pilot = false, python = simulatedNewPython, options = {}) {
  const { template = 'logins', alaCli = true, settings = null, env: extra = {}, port: fixedPort = null, fixture = null, home = null, nodeArgs = [] } = options;
  const directory = path.join(suite, name), examples = path.join(directory, 'examples'), runs = path.join(directory, 'runs'), records = path.join(directory, 'fake-ala-records');
  await fs.mkdir(examples, { recursive: true }); await fs.mkdir(records, { recursive: true });
  await fs.mkdir(path.join(examples, 'builder-without-metadata'), { recursive: true });
  if (settings !== null) await fs.writeFile(path.join(directory, 'agent-settings.md'), settings);
  const { base } = home ? { base: path.join(home, '.lean-documents/agent-home') } : fixture || await fixtureBase(template);
  // The simulated ALA checkout stays outside the server folder, whose persisted documents are checked for data files.
  const fake = path.join(suite, name + '-fake-ala'), ala = mode === 'missing' ? path.join(suite, name + '-missing-ala') : fake;
  if (mode !== 'missing' && !fsSync.existsSync(fake)) await createFakeALA(fake, `
if(process.env.FAKE_ALA_WAIT)await new Promise(resolve=>setTimeout(resolve,Number(process.env.FAKE_ALA_WAIT)));
// Error injection, chosen by the review notes of a run (the prompt carries them): what a hostile run might do to its home.
const inject=/INJECT:(\\w+)/.exec(prompt)?.[1],login=home&&path.join(home,'.codex/auth.json');
if(inject==='remove')fs.rmSync(home,{recursive:true,force:true});
if(inject==='link'){const outside=path.join(cwd,'outside-target.md');fs.writeFileSync(outside,'# Outside file\\n');fs.rmSync(login);fs.symlinkSync(outside,login);}
if(inject==='nested'){let folder=home;for(let i=0;i<200;i++){folder=path.join(folder,'n');fs.mkdirSync(folder);}fs.writeFileSync(path.join(folder,'deep.md'),'deep');}
if(inject==='copy')fs.copyFileSync(login,path.join(home,'.codex/auth-backup.json'));
if(inject==='mode')fs.chmodSync(home,0);
event({type:'coding-agent-message',agent,message:'Simulated executor; ',outputKind:'assistant',outputComplete:false,outputId:'m1'});
event({type:'coding-agent-message',agent,message:'no audit was performed.',outputKind:'assistant',outputComplete:true,outputId:'m1'});
event({type:'coding-agent-message',agent,message:'simulated tool output\\n',outputKind:'output',outputComplete:false});
process.stderr.write('Intentional test diagnostic\\n');
${mode === 'failure' ? "process.stderr.write('ala: Simulated ALA failure\\n');process.exitCode=7;" : "event({type:'coding-agent-final',agent,message:'Simulated final message.'});fs.writeFileSync(output,'Simulated final message.\\n');"}
`);
  const port = fixedPort || await freePort();
  const child = spawn(process.execPath, [...nodeArgs, path.join(root, 'tools/audit-ui.mjs')], {
    cwd: root, env: { ...cleanEnvironment(), PORT: String(port), AUDIT_RUNS_ROOT: runs, AUDIT_EXAMPLES_ROOT: examples, ...(alaCli ? { ALA_CLI: ala } : {}), ...(home ? { HOME: home } : { AUDIT_AGENT_HOME: base }), FAKE_ALA_RECORDS: records, AUDIT_PYTHON: python, PLOINKY_TEST_ROUTER: 'must-not-reach-ala', AUDIT_TEST_MODE: '1', AUDIT_DOCUMENT_PILOT: pilot ? '1' : '0', ...extra },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '';
  child.stdout.on('data', bytes => { output += bytes; });
  child.stderr.on('data', bytes => { output += bytes; });
  const origin = `http://127.0.0.1:${port}`, answers = [];
  for (let index = 0; index < 100 && !output.includes('Audit laboratory:'); index++) {
    if (child.exitCode !== null) throw Error(`Server exited before startup: ${output}`);
    await pause(30);
  }
  assert.match(output, /Audit laboratory:/, 'isolated server started');
  // Every answer is kept to show the fixture token is in none.
  async function request(route, data, expected = 200, { headers = {} } = {}) {
    const response = await fetch(origin + route, data === undefined ? { headers } : { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: origin, ...headers }, body: JSON.stringify(data) });
    const text = await response.text(); answers.push(text);
    const body = JSON.parse(text);
    assert.equal(response.status, expected, `${route}: ${text}`);
    return body;
  }
  const server = { directory, examples, runs, records, base, port, child, request, origin, answers, ala,
    output: () => output,
    homes: () => path.join(fsSync.realpathSync.native(base), 'homes'),
    async stop() {
      if (child.exitCode === null && child.signalCode === null) { child.kill('SIGTERM'); await once(child, 'exit'); }
      await fs.writeFile(path.join(directory, 'server-output.md'), '# Isolated server output\n\n```text\n' + output + '\n```\n');
      // The fixture token never reaches the output nor an answer.
      assert.equal(output.includes(TOKEN), false, 'the fixture token is not in the server output');
      for (const answer of answers) assert.equal(answer.includes(TOKEN), false, 'no answer holds the fixture token');
    },
    async terminal(id) {
      for (let index = 0; index < 160; index++) {
        if (child.exitCode !== null || child.signalCode !== null) throw Error(`Server crashed while recording executor failure:\n${output}`);
        const result = await request(`/api/runs/${id}`);
        if (!['queued', 'running', 'finalizing'].includes(result.status)) return result;
        await pause(30);
      }
      throw Error('Run never reached a terminal state');
    },
  };
  return server;
}

async function seed(server) {
  const { id } = await server.request('/api/examples', { title: 'Household bill', description: '' });
  const catalog = await server.request('/api/examples');
  assert.equal(catalog.length, 1, 'directories without metadata are skipped');
  assert.deepEqual(catalog[0].reports, []);
  const metadata = await readExample(path.join(server.examples, id, 'example.md'));
  assert.equal(metadata.description, ''); assert.equal(metadata.question, ''); assert.deepEqual(metadata.reports, []);
  const bytes = Buffer.from('# Supplied report\r\n\r\n[R1] Total is 9.\r\n', 'utf8');
  const uploaded = await server.request(`/api/examples/${id}/files`, { role: 'report', files: [{ name: 'Original report.md', data: bytes.toString('base64') }] });
  const report = uploaded.reports[0];
  assert.deepEqual(await fs.readFile(path.join(server.examples, id, report.path)), bytes);
  return { id, report, bytes };
}

// Names under the simulated agent home, as the fake ALA recorded them (never contents).
const homeEntries = text => text.split('## Agent home entries\n\n```text\n')[1].split('\n```')[0].split('\n').filter(Boolean);
async function filesContaining(directory, needle) {
  const found = [];
  for (const entry of await fs.readdir(directory, { withFileTypes: true, recursive: true })) {
    if (!entry.isFile()) continue;
    const file = path.join(entry.parentPath ?? entry.path, entry.name);
    if ((await fs.readFile(file)).includes(needle)) found.push(file);
  }
  return found;
}
async function records(server, kind) {
  const names = (await fs.readdir(server.records)).filter(name => name.startsWith(kind + '-')).sort();
  return Promise.all(names.map(name => fs.readFile(path.join(server.records, name), 'utf8')));
}

async function noDataDocuments(directory) {
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) await noDataDocuments(file);
    else assert.ok(!/\.(json|jsonl|log)$/i.test(entry.name), `Unexpected persisted data/log document: ${file}`);
  }
}

await test('Markdown uploads preserve bytes, support concurrent catalog reads, protect paths, and record failed executor honestly', { timeout: 30000 }, async () => {
  const server = await startServer('uploads-and-failure', 'failure');
  try {
    const { id, report, bytes } = await seed(server);
    let reading = true, reads = 0;
    const reader = (async () => { while (reading) { const catalog = await server.request('/api/examples'); assert.equal(catalog.length, 1); reads++; await pause(2); } })();
    const sources = Array.from({ length: 12 }, (_, i) => ({ name: i === 0 ? '../Original | bill.md' : `Document ${i}.md`, bytes: Buffer.from(`Source ${i}\r\nexact bytes: ș €\n`) }));
    try {
      await Promise.all(sources.map(source => server.request(`/api/examples/${id}/files`, { role: 'source', files: [{ name: source.name, data: source.bytes.toString('base64') }] })));
    } finally { reading = false; }
    await reader; assert.ok(reads > 0);
    const [example] = await server.request('/api/examples');
    assert.equal(example.files.filter(file => file.role === 'source').length, sources.length);
    const metadata = await readExample(path.join(server.examples, id, 'example.md'));
    for (const source of sources) {
      const relative = Object.keys(metadata.file_names).find(file => metadata.file_names[file] === source.name);
      assert.ok(relative?.startsWith('sources/document-'));
      assert.deepEqual(await fs.readFile(path.join(server.examples, id, relative)), source.bytes);
      const download = await fetch(server.origin + '/api/file?' + new URLSearchParams({ example: id, path: relative, download: '1' }));
      assert.equal(download.status, 200); assert.deepEqual(Buffer.from(await download.arrayBuffer()), source.bytes);
    }
    const outside = path.join(server.directory, 'outside.md'); await fs.writeFile(outside, 'Outside selected example');
    await fs.symlink(outside, path.join(server.examples, id, 'outside-link.md'));
    await fs.mkdir(path.join(server.examples, id, 'evaluator')); await fs.writeFile(path.join(server.examples, id, 'evaluator/expected.md'), 'Withheld');
    for (const relative of ['../outside.md', '/etc/passwd', 'evaluator/expected.md', 'outside-link.md']) {
      await server.request('/api/file?' + new URLSearchParams({ example: id, path: relative }), undefined, 400);
    }
    await server.request('/api/file?' + new URLSearchParams({ example: '../bad', path: 'example.md' }), undefined, 400);
    const invalidOrigin = await fetch(server.origin + '/api/examples', { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: 'https://untrusted.invalid' }, body: '{}' });
    assert.equal(invalidOrigin.status, 400); assert.deepEqual(await invalidOrigin.json(), { error: 'Invalid Origin' });
    for (const origin of ['http://localhost:' + server.port, 'http://127.0.0.1:1']) {
      const refused = await fetch(server.origin + '/api/examples', { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: origin }, body: '{}' });
      assert.equal(refused.status, 400, origin);
    }
    const run = await server.request('/api/runs', { exampleId: id, reportPath: report.path });
    const result = await server.terminal(run.id);
    assert.equal(result.status, 'failed'); assert.equal(result.exitCode, 7); assert.equal(result.reportAvailability.status, 'unavailable');
    assert.equal(result.error, 'ALA exited with code 7: ala: Simulated ALA failure');
    assert.equal(result.auditReportPath, null); assert.deepEqual(result.findings, []);
    const runDirectory = path.join(server.runs, run.id);
    assert.deepEqual(await fs.readFile(path.join(runDirectory, 'inputs/report.md')), bytes);
    const snapshots = await fs.readdir(path.join(runDirectory, 'inputs/sources'));
    assert.equal(snapshots.length, sources.length);
    const saved = await Promise.all(snapshots.map(file => fs.readFile(path.join(runDirectory, 'inputs/sources', file))));
    for (const source of sources) assert.ok(saved.some(value => value.equals(source.bytes)));
    assert.match(await fs.readFile(path.join(runDirectory, 'run.md'), 'utf8'), /## Status\n\nfailed/);
    assert.match(await fs.readFile(path.join(runDirectory, 'execution.md'), 'utf8'), /Simulated executor/);
    await noDataDocuments(server.directory);
  } finally { await server.stop(); }
});

await test('missing ALA refuses new verification with a clear message without crashing the server', { timeout: 15000 }, async () => {
  const server = await startServer('missing-executor', 'missing');
  try {
    const { id, report } = await seed(server);
    const refused = await server.request('/api/runs', { exampleId: id, reportPath: report.path }, 400);
    assert.match(refused.error, /ALA was not found/);
    assert.deepEqual(await fs.readdir(server.runs), [], 'no run is created while ALA is missing');
    const settings = await server.request('/api/agent-settings');
    assert.equal(settings.ala.found, false); assert.equal(settings.ala.checked[0].source, 'environment');
    assert.match((await server.request('/api/coding-agents', undefined, 503)).error, /ALA was not found/);
    assert.equal(settings.python.found, true); assert.equal(settings.python.path, simulatedNewPython); assert.equal(settings.python.warning, null);
    assert.equal((await server.request('/api/examples')).length, 1);
    await noDataDocuments(server.directory);
  } finally { await server.stop(); }
  // Without Python 3.12 or later, new work is refused with the resolver's text, before ALA is even considered.
  const old = await startServer('missing-python', 'missing', false, await simulatedPython('simulated-python-3.9', '3.9.1'));
  try {
    const { id, report } = await seed(old);
    const refused = await old.request('/api/runs', { exampleId: id, reportPath: report.path }, 400);
    assert.match(refused.error, /^AUDIT_PYTHON is set to .*, which cannot be used\. Python 3\.12 or later is required \(tools\/document_audit\.py\)\. Found: .*simulated-python-3\.9 3\.9\.1\. Run "node tools\/setup-toolchain\.mjs"/);
    assert.doesNotMatch(refused.error, /ALA was not found/);
    assert.deepEqual(await fs.readdir(old.runs), [], 'no run is created without Python');
    await old.request(`/api/examples/${id}/files`, { role: 'source', files: [{ name: 'Bill.md', data: Buffer.from('# Bill\n\nTotal 9.\n').toString('base64') }] });
    const generation = await old.request(`/api/examples/${id}/reports/generate`, { mode: 'grounded' }, 400);
    assert.match(generation.error, /Python 3\.12 or later is required/);
    const settings = await old.request('/api/agent-settings');
    assert.equal(settings.python.found, false); assert.match(settings.python.error, /Python 3\.12 or later is required/);
    assert.equal((await old.request('/api/examples')).length, 1, 'browsing still works');
  } finally { await old.stop(); }
});

await test('zero executor exit without an audit is incomplete, never verified', { timeout: 15000 }, async () => {
  const server = await startServer('empty-success', 'success');
  try {
    const { id, report } = await seed(server);
    const run = await server.request('/api/runs', { exampleId: id, reportPath: report.path });
    const result = await server.terminal(run.id);
    assert.equal(result.status, 'incomplete'); assert.equal(result.exitCode, 0);
    assert.equal(result.reportAvailability.status, 'unavailable'); assert.equal(result.auditReportPath, null);
    assert.match(result.error, /did not pass/);
    await noDataDocuments(server.directory);
  } finally { await server.stop(); }
});

await test('the selected coding agent is validated, snapshotted at queue time and passed to ALA with a read-only repository', { timeout: 30000 }, async () => {
  const server = await startServer('agent-selection', 'success');
  try {
    const initial = await server.request('/api/agent-settings');
    assert.deepEqual([initial.agent, initial.model, initial.effort], ['codex', '', ''], 'Codex with its own default model remains the default');
    // The settings carry the dedicated agent home's state: a usable template with file logins for Codex and Claude.
    const homes = server.homes(), base = path.dirname(homes);
    assert.equal(initial.agentHome.base, base); assert.equal(initial.agentHome.ready, true); assert.equal(initial.agentHome.error, null);
    assert.deepEqual(['codex', 'claude', 'pi'].map(name => [name, initial.agentHome.agents[name]?.loggedIn, initial.agentHome.agents[name]?.source]), [['codex', true, 'file'], ['claude', true, 'file'], ['pi', false, 'none']]);
    assert.equal(initial.ala.found, true); assert.equal(initial.ala.source, 'environment');
    const { agents } = await server.request('/api/coding-agents');
    assert.deepEqual(agents.map(agent => [agent.id, agent.available]), [['codex', true], ['claude', true], ['pi', false]]);
    assert.deepEqual((await server.request('/api/coding-agents/codex/models')).models, [{ id: 'test-codex-model', label: 'Test Codex Model', efforts: ['low', 'high'] }]);
    // The agent list derives install paths from a home that is never created; the model listing got a home of its own,
    // which existed while the service was created and is gone afterwards.
    const [discovery] = await records(server, 'discovery'), [service] = await records(server, 'service');
    assert.ok(discovery.includes(`| Environment HOME | ${path.join(homes, '.discovery')} |`), discovery); assert.equal(fsSync.existsSync(path.join(homes, '.discovery')), false);
    const listingHome = /\| Agent home \| ([^|]+) \|/.exec(service)[1];
    assert.equal(path.dirname(listingHome), homes); assert.match(service, /\| Agent home exists \| yes \|/); assert.ok(service.includes(`| Environment HOME | ${listingHome} |`));
    assert.deepEqual(homeEntries(service), ['.codex/', '.codex/auth.json'], 'only the Codex login was copied for the Codex listing');
    assert.equal(fsSync.existsSync(listingHome), false, 'the listing home is removed once the worker has ended');
    assert.deepEqual(await fs.readdir(homes), [], 'nothing is left in homes/ after the model listing');
    await server.request('/api/coding-agents/pi/models', undefined, 400);
    for (const invalid of [{ agent: 'pi', model: '' }, { agent: 'unknown', model: '' }, { agent: 'claude', model: 'test-codex-model' }, { agent: 'codex', model: 'test-codex-model', effort: 'max' }, { agent: 'codex', model: '', effort: 'low' }, { agent: 'codex', model: 5 }]) {
      await server.request('/api/agent-settings', invalid, 400);
    }
    const saved = await server.request('/api/agent-settings', { agent: 'codex', model: 'test-codex-model', effort: 'high' });
    assert.deepEqual([saved.agent, saved.model, saved.effort], ['codex', 'test-codex-model', 'high']);
    const record = await fs.readFile(path.join(server.directory, 'agent-settings.md'), 'utf8');
    assert.match(record, /^\| Coding agent \| codex \|$/m); assert.match(record, /^\| Model \| `test-codex-model` \|$/m); assert.match(record, /^\| Effort \| high \|$/m);
    await server.request('/api/agent-settings', { agent: 'claude', model: 'test-claude-model' });
    const { id, report } = await seed(server);
    const run = await server.request('/api/runs', { exampleId: id, reportPath: report.path });
    assert.equal(run.agent, 'claude'); assert.equal(run.model, 'test-claude-model'); assert.equal(run.effort, undefined);
    await server.request('/api/agent-settings', { agent: 'codex', model: '' });
    const result = await server.terminal(run.id);
    assert.equal(result.status, 'incomplete'); assert.equal(result.agent, 'claude', 'queued work keeps its agent'); assert.equal(result.model, 'test-claude-model');
    const runDirectory = path.join(server.runs, run.id);
    const invocation = await fs.readFile(path.join(runDirectory, 'fake-ala-invocation.md'), 'utf8');
    const args = invocation.split('```text\n')[1].split('\n```')[0].split('\n'), value = flag => args[args.indexOf(flag) + 1];
    // The run's own agent home: directly after --cwd, inside <base>/homes, existing during the run with exactly Claude's
    // login file, and removed once the run ended.
    const runHome = value('--home');
    assert.equal(args[args.indexOf('--cwd') + 2], '--home'); assert.equal(path.dirname(runHome), homes); assert.match(path.basename(runHome), /^home-\d+-[0-9a-f]{16}$/);
    assert.match(invocation, /\| Agent home exists \| yes \|/); assert.deepEqual(homeEntries(invocation), ['.claude/', '.claude/.credentials.json']);
    assert.equal(fsSync.existsSync(runHome), false, 'the run home is gone after the terminal state');
    for (const text of [invocation, discovery, service]) assert.equal(text.includes(path.join(base, 'template')), false, 'no invocation or catalog record names the template');
    assert.equal(value('--ca'), 'claude'); assert.equal(value('--model'), 'test-claude-model'); assert.equal(args.includes('--effort'), false);
    assert.equal(value('--cwd'), runDirectory); assert.equal(value('--folder'), await fs.realpath(root), 'the repository is mounted read-only');
    assert.equal(value('--taskFile'), path.join(runDirectory, 'instructions.md')); assert.equal(value('--output'), path.join(runDirectory, 'final-message.md'));
    await assertHelperRunsThroughInterpreter(await fs.readFile(path.join(runDirectory, 'instructions.md'), 'utf8'), 'skills/evidence-to-proof-review/scripts/markdown_audit.py');
    assert.match(invocation, /\| Process folder \| [^|]+ \|/);
    assert.match(invocation, /\| Event stream \| 1 \|/); assert.match(invocation, /\| Ploinky variables \| absent \|/); assert.match(invocation, /\| Configuration file exists \| no \|/);
    assert.equal(await fs.readFile(path.join(runDirectory, 'final-message.md'), 'utf8'), 'Simulated final message.\n');
    const runRecord = await fs.readFile(path.join(runDirectory, 'run.md'), 'utf8');
    assert.match(runRecord, /## Coding agent\n\nclaude\n/); assert.match(runRecord, /## Model\n\ntest-claude-model\n/); assert.match(runRecord, /## ALA entrypoint\n\n\S+bin\/ala\.mjs\n/);
    const journal = await fs.readFile(path.join(runDirectory, 'execution.md'), 'utf8');
    assert.match(journal, /Simulated executor; no audit was performed\./, 'fragments sharing one output id form one record');
    assert.match(journal, /simulated tool output/); assert.match(journal, /Intentional test diagnostic/); assert.match(journal, /Simulated final message\./);
    assert.match(journal, /agent home prepared[\s\S]*### Agent\n\nclaude[\s\S]*### Login\n\nlogin file/);
    assert.match(journal, /agent home removed[\s\S]*### Removed\n\nyes[\s\S]*### Login files rewritten\n\nnone/);
    const second = await server.request('/api/runs', { exampleId: id, reportPath: report.path });
    assert.equal(second.agent, 'codex'); assert.equal(second.model, undefined, 'new work uses the latest saved selection');
    await server.terminal(second.id);
    assert.deepEqual(await fs.readdir(homes), [], 'homes/ is empty after every run reached its terminal state');
    // The fixture token is in no file the server, its runs or the fake ALA wrote.
    assert.deepEqual(await filesContaining(server.directory, TOKEN), []);
    await noDataDocuments(server.directory);
  } finally { await server.stop(); }
});

// This test runs the real helper and real Lean, so it needs the repository Lean toolchain and a Python 3.12 or later.
const lean = leanStatus(root);
let realPython = null, pythonProblem = null;
try { realPython = resolvePython().path; } catch (error) { pythonProblem = error.message; }
const skipReasons = [];
if (!lean.installed) skipReasons.push(`Lean is not installed at ${lean.path}; run "node tools/setup-toolchain.mjs"`);
if (pythonProblem) skipReasons.push(pythonProblem);
await test('fresh controlled Lean audit exposes links, detects tampering, and repairs rendered reports', { timeout: 60000, skip: skipReasons.length ? `Lean-dependent audit test (needs the Lean toolchain and Python 3.12 or later): ${skipReasons.join(' | ')}` : false }, async () => {
  // Build an explicit synthetic helper fixture now; no user audit or deleted
  // historical run is read, restored or required. Lean really executes here.
  const fixtureRoot = path.join(suite, 'controlled-fixture');
  await fs.mkdir(fixtureRoot);
  const exercise = path.join(fixtureRoot, 'exercise.py');
  await fs.writeFile(exercise, `from pathlib import Path
import sys
sys.path.insert(0, str(Path(sys.argv[1]) / 'tests'))
import test_document_audit as fixture
fixture.TEST_ROOT = Path(sys.argv[2])
workspace = fixture.completed_fixture()
print('CONTROLLED_WORKSPACE=' + str(workspace))
`);
  let execution;
  try {
    execution = await promisify(execFile)(realPython, [exercise, root, fixtureRoot], { cwd: root, timeout: 45000, maxBuffer: 4 * 1024 * 1024 });
  } catch (error) {
    await fs.writeFile(path.join(fixtureRoot, 'execution.md'), '# Controlled fixture execution failed\n\n```text\n' + (error.stdout || '') + '\n' + (error.stderr || error.message) + '\n```\n');
    throw error;
  }
  await fs.writeFile(path.join(fixtureRoot, 'execution.md'), '# Controlled fixture execution\n\nThe retained Python script called the real helper and Lean checks. No Codex process or user audit was reused. Exit code: 0.\n\n```text\n' + execution.stdout + '\n' + execution.stderr + '\n```\n');
  const originalWorkspace = /^CONTROLLED_WORKSPACE=(.+)$/m.exec(execution.stdout)?.[1];
  assert.ok(originalWorkspace && originalWorkspace.startsWith(fixtureRoot + path.sep));
  const originalReport = await fs.readFile(path.join(originalWorkspace, 'audit-report.md'));
  const originalValidation = await fs.readFile(path.join(originalWorkspace, 'validation.md'));
  const server = await startServer('controlled-document-audit', 'missing', true, realPython);
  try {
    const id = 'controlled-proof-fixture', runDirectory = path.join(server.runs, id);
    const workspace = path.join(runDirectory, 'work', path.basename(originalWorkspace));
    await fs.mkdir(path.dirname(workspace), { recursive: true });
    await fs.cp(originalWorkspace, workspace, { recursive: true, force: false, errorOnExist: true });
    await fs.mkdir(path.join(runDirectory, 'internal'));
    const state = { schema_version: 2, id, exampleId: 'controlled-fixture', exampleTitle: 'Fresh synthetic helper fixture', created: new Date().toISOString(), status: 'finished', test: true, language: 'en' };
    await fs.writeFile(path.join(runDirectory, 'internal/run.json'), JSON.stringify(state, null, 2) + '\n');
    await fs.writeFile(path.join(runDirectory, 'run.md'), '# Controlled helper fixture\n\nThis test generated a new synthetic audit and executed real Lean checks. It did not run Codex or restore a previous user audit. The source workspace is retained separately for comparison.\n');
    await fs.writeFile(path.join(runDirectory, 'fixture-provenance.md'), `# Controlled regression fixture\n\nFreshly generated by ${exercise}, then copied without modifying workspace bytes from ${originalWorkspace}.\n\nActual Lean target and axiom checks ran during fixture creation. The API checks below inspect their retained results; the copy does not claim an additional proof execution or independent semantic review.\n`);
    let result = await server.request(`/api/runs/${id}`);
    assert.equal(result.reportAvailability.status, 'ready', JSON.stringify(result));
    assert.equal(result.proofIntegrity.status, 'matches'); assert.equal(result.reportBinding.status, 'matches');
    assert.ok(result.findings.length > 0); assert.ok(result.proofResults.targets.length > 0);
    const links = new Set([result.auditReportPath, result.findingsPath]);
    for (const finding of result.findings) {
      links.add(finding.path);
      for (const evidence of finding.evidence) links.add(evidence.path);
      for (const proof of finding.proofs) links.add(proof.path);
    }
    for (const target of result.proofResults.targets) { links.add(target.check_file); links.add(target.log); }
    for (const relative of links) {
      assert.ok(relative, 'returned artifact link must be present');
      const value = await server.request('/api/file?' + new URLSearchParams({ run: id, path: relative }));
      assert.ok(value.size > 0, relative);
      if (relative.endsWith('.lean')) assert.equal(typeof value.text, 'string');
    }
    const reportPath = path.join(runDirectory, result.auditReportPath);
    await fs.appendFile(reportPath, '\nThis line is a test modification of rendered prose only.\n');
    result = await server.request(`/api/runs/${id}`);
    assert.equal(result.reportAvailability.status, 'stale'); assert.equal(result.reportAvailability.canRender, true);
    assert.equal(result.proofIntegrity.status, 'matches');
    result = await server.request(`/api/runs/${id}/render`, {});
    assert.equal(result.reportAvailability.status, 'ready');
    assert.deepEqual(await fs.readFile(reportPath), originalReport);
    await fs.unlink(reportPath);
    result = await server.request(`/api/runs/${id}`);
    assert.equal(result.reportAvailability.status, 'renderable'); assert.equal(result.reportAvailability.canRender, true);
    result = await server.request(`/api/runs/${id}/render`, {});
    assert.equal(result.reportAvailability.status, 'ready'); assert.deepEqual(await fs.readFile(reportPath), originalReport);
    const findingPath = path.join(workspace, 'findings', result.findings[0].claim_id + '.json');
    const finding = JSON.parse(await fs.readFile(findingPath, 'utf8'));
    finding.reasoning += ' Unvalidated test modification of the canonical finding.';
    await fs.writeFile(findingPath, JSON.stringify(finding, null, 2) + '\n');
    result = await server.request(`/api/runs/${id}`);
    assert.equal(result.reportAvailability.status, 'stale'); assert.equal(result.proofIntegrity.status, 'changed');
    assert.equal(result.reportAvailability.canRender, false);
    const rejection = await server.request(`/api/runs/${id}/render`, {}, 400);
    assert.match(rejection.error, /validation failed|changed|stale|check/i);
    assert.deepEqual(await fs.readFile(reportPath), originalReport, 'failed rendering must not replace the previous report');
    assert.ok(result.files.every(file => !file.path.startsWith('internal/') && !/\.jsonl?$/.test(file.path)), 'internal persistence must stay out of human artifact listings');
    assert.deepEqual(await fs.readFile(path.join(originalWorkspace, 'audit-report.md')), originalReport, 'freshly generated fixture report is untouched');
    assert.deepEqual(await fs.readFile(path.join(originalWorkspace, 'validation.md')), originalValidation, 'freshly generated fixture validation is untouched');
  } finally { await server.stop(); }
});

await test('Example language persists and controls the audit while uploaded bytes remain unchanged', {timeout: 30000}, async()=>{
  const server = await startServer('romanian-pilot-language','success',true);
  try {
    await server.request('/api/examples',{title:'Invalid language',language:'fr'},400);
    const created = await server.request('/api/examples',{title:'Aprobarea investiției',description:'Exemplu de test izolat.',language:'ro'});
    const reportBytes=Buffer.from('# Raport\n\nAfirmație în limba română.\n','utf8');
    const sourceBytes=Buffer.from('# Document sursă\n\nValoarea propusă este 8 milioane EUR.\n','utf8');
    const uploaded=await server.request(`/api/examples/${created.id}/files`,{role:'report',files:[{name:'raport.md',data:reportBytes.toString('base64')}]});
    await server.request(`/api/examples/${created.id}/files`,{role:'source',files:[{name:'termeni.md',data:sourceBytes.toString('base64')}]});
    const [example]=await server.request('/api/examples');
    assert.equal(example.language,'ro');
    assert.equal((await readExample(path.join(server.examples,created.id,'example.md'))).language,'ro');
    const started=await server.request('/api/runs',{exampleId:created.id,reportPath:uploaded.reports[0].path});
    const completed=await server.terminal(started.id);
    assert.equal(completed.language,'ro');assert.equal(completed.test,true);assert.equal(completed.status,'incomplete');
    assert(completed.files.every(file=>!file.path.startsWith('internal/')&&!/\.jsonl?$/.test(file.path)));
    const instructions=await fs.readFile(path.join(server.runs,started.id,'instructions.md'),'utf8');
    assert.match(instructions,/--language ro/);assert.match(instructions,/human documents in Romanian/);
    await assertHelperRunsThroughInterpreter(instructions,'tools/document_audit.py');
    assert.deepEqual(await fs.readFile(path.join(server.runs,started.id,'inputs/report.md')),reportBytes);
    const names=await fs.readdir(path.join(server.runs,started.id,'inputs/sources'));
    assert.equal(names.length,1);assert.deepEqual(await fs.readFile(path.join(server.runs,started.id,'inputs/sources',names[0])),sourceBytes);
  } finally {await server.stop();}
});

await test('the page allows only its own script, and the server answers only for the host 127.0.0.1:<PORT>', { timeout: 30000 }, async () => {
  const server = await startServer('page-and-host', 'success');
  try {
    const { id } = await seed(server);
    // GET / allows only its own script by hash.
    const page = await fetch(server.origin + '/'), html = await page.text();
    assert.equal(page.status, 200);
    assert.equal(page.headers.get('referrer-policy'), 'no-referrer'); assert.equal(page.headers.get('x-content-type-options'), 'nosniff');
    assert.equal(html.split('<script').length, 2, 'the page has exactly one script element');
    const script = Buffer.from(html).subarray(Buffer.from(html).indexOf('<script>') + 8, Buffer.from(html).indexOf('</script>'));
    const policy = page.headers.get('content-security-policy');
    assert.equal(policy, `script-src 'sha256-${crypto.createHash('sha256').update(script).digest('base64')}'; base-uri 'none'; frame-ancestors 'none'`);
    assert.doesNotMatch(policy, /unsafe-inline|unsafe-hashes/);
    assert.doesNotMatch(html, /\son[a-z]+\s*=\s*["']/i, 'no inline event handler attribute in the page');

    // Host: only 127.0.0.1:<PORT>; localhost would share an origin with [::1].
    for (const host of [`localhost:${server.port}`, `[::1]:${server.port}`, `evil.example:${server.port}`, '127.0.0.1']) {
      const refused = await raw(server.port, ['GET /api/examples HTTP/1.1'], '', { host });
      assert.equal(refused.status, 400, host); assert.deepEqual(JSON.parse(refused.body), { error: `Use http://127.0.0.1:${server.port}` }, host);
    }
    assert.equal((await raw(server.port, ['GET /api/examples HTTP/1.1'])).status, 200, 'GET /api/examples on 127.0.0.1');
    // Expect: 100-continue. The client is invited to send its body, but never under another Host.
    const body = JSON.stringify({ title: 'Continued' });
    const invited = await raw(server.port, ['POST /api/examples HTTP/1.1', 'Content-Type: application/json', 'Expect: 100-continue', `Content-Length: ${body.length}`], body, { wait: 500 });
    assert.match(invited.data, /^HTTP\/1\.1 100 Continue\r\n\r\nHTTP\/1\.1 200/); assert.match(invited.data, /"id":"custom-continued-/);
    const wrongHost = await raw(server.port, ['POST /api/examples HTTP/1.1', 'Content-Type: application/json', 'Expect: 100-continue', `Content-Length: ${body.length}`], body, { wait: 500, host: `localhost:${server.port}` });
    assert.doesNotMatch(wrongHost.data, /^HTTP\/1\.1 100/); assert.equal(wrongHost.status, 400);

    // Active content is never served inline (A-E7): the page's script origin depends on it.
    const active = await server.request(`/api/examples/${id}/files`, { role: 'source', files: [{ name: 'page.html', data: Buffer.from('<script>alert(1)</script>').toString('base64') }, { name: 'image.svg', data: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>').toString('base64') }] });
    for (const file of active.files.filter(f => /\.(html|svg)$/.test(f.path))) {
      const response = await fetch(server.origin + '/api/file?' + new URLSearchParams({ example: id, path: file.path, inline: '1' }));
      assert.equal(response.headers.get('content-type'), 'application/octet-stream', file.path); assert.match(response.headers.get('content-disposition'), /^attachment;/, file.path);
      assert.equal(response.headers.get('x-content-type-options'), 'nosniff'); await response.arrayBuffer();
    }
  } finally { await server.stop(); }
});

await test('outside /api/ only GET is served: any other method is refused unread, before the Origin, content-type and body steps', { timeout: 30000 }, async () => {
  // Test instrumentation, loaded into the server with --import: it records how many bytes the server read from each
  // connection (never their content), keyed by the client's port, from socket.bytesRead.
  const reads = path.join(suite, 'outside-api-socket-reads.txt'), preload = path.join(suite, 'outside-api-socket-reads.mjs');
  await fs.writeFile(preload, `import http from 'node:http';\nimport fs from 'node:fs';\nconst create = http.createServer;\nhttp.createServer = (...args) => { const server = create(...args); server.on('connection', socket => { const peer = socket.remotePort; socket.once('close', () => fs.appendFileSync(${JSON.stringify(reads)}, peer + ' ' + socket.bytesRead + '\\n')); }); return server; };\n`);
  const server = await startServer('outside-api', 'success', false, simulatedNewPython, { nodeArgs: ['--import', pathToFileURL(preload).href] });
  try {
    const declared = 8 * 1024 * 1024, refusal = { error: 'Only GET is served outside /api/.' };
    // One raw exchange that may be cut by the server: the answer, and the client port that keys the server's read count.
    const exchange = (lines, body = null, host = `127.0.0.1:${server.port}`) => new Promise((resolve, reject) => {
      const socket = net.connect(server.port, '127.0.0.1'); let data = '', local = null;
      const timer = setTimeout(() => socket.destroy(new Error('the server did not answer and close the connection within 5 s')), 5000);
      socket.on('connect', () => { local = socket.localPort; });
      socket.on('data', chunk => { data += chunk; });
      socket.on('error', error => { if (!['EPIPE', 'ECONNRESET'].includes(error.code)) reject(error); });
      socket.on('close', () => { clearTimeout(timer); const [head, ...rest] = data.split('\r\n\r\n'); resolve({ status: Number(/^HTTP\/1\.1 (\d+)/.exec(head)?.[1]), head, body: rest.join('\r\n\r\n'), data, local }); });
      socket.write([...lines, `Host: ${host}`, '', ''].join('\r\n'));
      if (body) socket.write(body);
    });
    const readBy = async local => { await pause(150); const line = (await fs.readFile(reads, 'utf8')).split('\n').find(l => l.startsWith(local + ' ')); assert.ok(line, `no read count for client port ${local}`); return Number(line.split(' ')[1]); };
    for (const target of ['/x', '/']) {
      const head = [`POST ${target} HTTP/1.1`, 'Content-Type: application/json', `Origin: ${server.origin}`, `Content-Length: ${declared}`];
      // The headers alone: the refusal comes without one byte of the body, and the server closes the connection.
      const early = await exchange(head);
      assert.equal(early.status, 405, `${target}: ${early.data.slice(0, 200)}`); assert.deepEqual(JSON.parse(early.body), refusal, target);
      assert.match(early.head, /^Allow: GET$/mi, target); assert.match(early.head, /^Connection: close$/mi, target);
      // The whole body sent at once: the server reads less than 1 MiB of it before it cuts the connection.
      const full = await exchange(head, Buffer.alloc(declared, 120)), read = await readBy(full.local);
      assert.ok(read < 1024 * 1024, `${target}: the server read ${read} bytes of an ${declared}-byte body`);
    }
    // A small body that is not JSON: refused for its method, not parsed (the finding answered 400 with a parse error).
    const notJson = await exchange(['POST /x HTTP/1.1', 'Content-Type: application/json', 'Content-Length: 8'], Buffer.from('not-json'));
    assert.equal(notJson.status, 405); assert.deepEqual(JSON.parse(notJson.body), refusal);
    // Another Host is refused just as unread: a caller must not make the server read an upload by naming a Host the
    // server refuses.
    for (const target of ['/api/examples', '/x']) {
      const head = [`POST ${target} HTTP/1.1`, 'Content-Type: application/json', `Content-Length: ${declared}`], other = `localhost:${server.port}`;
      const early = await exchange(head, null, other);
      assert.equal(early.status, 400, `${target}: ${early.data.slice(0, 200)}`); assert.deepEqual(JSON.parse(early.body), { error: `Use http://127.0.0.1:${server.port}` }, target);
      assert.match(early.head, /^Connection: close$/mi, target);
      const full = await exchange(head, Buffer.alloc(declared, 120), other), read = await readBy(full.local);
      assert.ok(read < 1024 * 1024, `${target} with another Host: the server read ${read} bytes of an ${declared}-byte body`);
    }
    // A GET outside /api/ that announces a body is refused unread as well; the page is only ever requested without one.
    const bodyRefusal = { error: 'A request outside /api/ must not carry a body.' };
    for (const target of ['/', '/nope']) {
      const head = [`GET ${target} HTTP/1.1`, `Content-Length: ${declared}`];
      const early = await exchange(head);
      assert.equal(early.status, 400, `GET ${target}: ${early.data.slice(0, 200)}`); assert.deepEqual(JSON.parse(early.body), bodyRefusal, target);
      assert.match(early.head, /^Connection: close$/mi, target);
      const full = await exchange(head, Buffer.alloc(declared, 120)), read = await readBy(full.local);
      assert.ok(read < 1024 * 1024, `GET ${target}: the server read ${read} bytes of an ${declared}-byte body`);
    }
    const invited = await exchange(['GET / HTTP/1.1', 'Expect: 100-continue', `Content-Length: ${declared}`]);
    assert.doesNotMatch(invited.data, /^HTTP\/1\.1 100/, 'no 100 Continue for a GET with a body outside /api/'); assert.equal(invited.status, 400);
    // Expect: 100-continue on a path outside /api/: the client is never invited to send its body.
    const continued = await exchange(['POST /x HTTP/1.1', 'Content-Type: application/json', 'Expect: 100-continue', `Content-Length: ${declared}`]);
    assert.doesNotMatch(continued.data, /^HTTP\/1\.1 100/, 'no 100 Continue outside /api/'); assert.equal(continued.status, 405); assert.deepEqual(JSON.parse(continued.body), refusal);
    for (const method of ['PUT', 'DELETE', 'PATCH', 'OPTIONS']) assert.equal((await exchange([`${method} / HTTP/1.1`, 'Content-Length: 0', 'Connection: close'])).status, 405, method);
    // An unknown /api/ path is no route: a GET gets 404 and a POST is refused as an unknown action.
    const unknown = `/api/${crypto.randomBytes(6).toString('hex')}`;
    assert.equal((await fetch(server.origin + unknown)).status, 404);
    assert.deepEqual(await server.request(unknown, {}, 400), { error: 'Unknown action' });
    // GET / stays open.
    const page = await fetch(server.origin + '/'); assert.equal(page.status, 200); assert.match(await page.text(), /<script>/);
    assert.equal(server.child.exitCode, null, 'the server is still running');
  } finally { await server.stop(); }
});

await test('without a pilot login new work and the model listing are refused with the exact text and nothing is created (H-3)', { timeout: 30000 }, async () => {
  const empty = await startServer('no-login', 'success', false, simulatedNewPython, { template: 'empty' });
  try {
    const base = path.dirname(empty.homes()), { id, report } = await seed(empty);
    for (const agent of ['codex', 'claude']) {
      await empty.request('/api/agent-settings', { agent, model: '' });
      assert.deepEqual(await empty.request('/api/runs', { exampleId: id, reportPath: report.path }, 400), { error: noLogin(agent, base) });
      assert.deepEqual(await empty.request(`/api/coding-agents/${agent}/models?refresh=1`, undefined, 400), { error: noLogin(agent, base) });
    }
    assert.deepEqual(await fs.readdir(empty.runs), [], 'no run folder is created');
    const settings = await empty.request('/api/agent-settings');
    assert.equal(settings.agentHome.ready, true); assert.equal(settings.agentHome.agents.claude.loggedIn, false); assert.equal(settings.agentHome.agents.claude.source, 'none');
    assert.deepEqual(fsSync.existsSync(empty.homes()) ? await fs.readdir(empty.homes()) : [], []);
  } finally { await empty.stop(); }
  // A template holding anything but a login file refuses every agent with the template text.
  const foreign = await startServer('foreign-template', 'success', false, simulatedNewPython, { template: 'foreign' });
  try {
    const { id, report } = await seed(foreign);
    const refused = await foreign.request('/api/runs', { exampleId: id, reportPath: report.path }, 503);
    assert.equal(refused.error, homeUnavailable(reasons.foreign('.codex/config.toml', 'file')));
    assert.deepEqual(await fs.readdir(foreign.runs), []);
    const settings = await foreign.request('/api/agent-settings');
    assert.equal(settings.agentHome.ready, false); assert.equal(settings.agentHome.error, refused.error);
  } finally { await foreign.stop(); }
  // A credential variable in the server's environment is a login: the home is empty and only the name is recorded.
  const variable = await startServer('environment-login', 'success', false, simulatedNewPython, { template: 'empty', env: { OPENAI_API_KEY: TOKEN + '-variable' } });
  try {
    const { id, report } = await seed(variable);
    const run = await variable.request('/api/runs', { exampleId: id, reportPath: report.path }); await variable.terminal(run.id);
    const invocation = await fs.readFile(path.join(variable.runs, run.id, 'fake-ala-invocation.md'), 'utf8');
    assert.match(invocation, /\| Agent home exists \| yes \|/); assert.deepEqual(homeEntries(invocation), [], 'with an environment login the home is empty');
    assert.match(await fs.readFile(path.join(variable.runs, run.id, 'execution.md'), 'utf8'), /### Login\n\nvariable from the server's environment \(OPENAI_API_KEY\)/);
    assert.deepEqual(await filesContaining(variable.directory, TOKEN), [], 'the variable value is in no file');
  } finally { await variable.stop(); }
});

await test('three audits at once get three homes, each owned by its ALA process group, and SIGTERM leaves homes/ empty', { timeout: 60000 }, async () => {
  const fixture = await fixtureBase();
  const server = await startServer('three-homes', 'success', false, simulatedNewPython, { fixture, env: { AUDIT_CONCURRENCY: '3', FAKE_ALA_WAIT: '60000' } });
  let pids = [];
  try {
    const { id } = await seed(server);
    const uploaded = await server.request(`/api/examples/${id}/files`, { role: 'report', files: ['Second.md', 'Third.md'].map(name => ({ name, data: Buffer.from(`# ${name}\n`).toString('base64') })) });
    const runs = [];
    for (const report of uploaded.reports) runs.push(await server.request('/api/runs', { exampleId: id, reportPath: report.path }));
    let states = [];
    for (let i = 0; i < 200; i++) {
      states = await Promise.all(runs.map(run => server.request(`/api/runs/${run.id}`)));
      if (states.every(state => state.status === 'running') && runs.every(run => fsSync.existsSync(path.join(server.runs, run.id, 'fake-ala-invocation.md')))) break;
      await pause(50);
    }
    assert.deepEqual(states.map(state => state.status), ['running', 'running', 'running']);
    const homes = await Promise.all(runs.map(async run => /\| Agent home \| ([^|]+) \|/.exec(await fs.readFile(path.join(server.runs, run.id, 'fake-ala-invocation.md'), 'utf8'))[1]));
    assert.equal(new Set(homes).size, 3, 'three distinct homes'); for (const home of homes) assert.equal(path.dirname(home), server.homes());
    // The owner record beside each home names the ALA process, which leads its own process group (detached spawn).
    pids = states.map(state => state.pid);
    for (const home of homes) {
      const owner = JSON.parse(await fs.readFile(home + '.owner', 'utf8'));
      assert.equal(owner.group, owner.ala, 'the recorded group is the ALA pid'); assert.ok(pids.includes(owner.ala));
      const pgid = Number(execFileSync('/bin/ps', ['-o', 'pgid=', '-p', String(owner.ala)], { encoding: 'utf8' }).trim());
      assert.equal(pgid, owner.ala, 'the ALA process leads its own process group');
    }
    server.child.kill('SIGTERM'); await once(server.child, 'exit');
    assert.deepEqual(await fs.readdir(server.homes()), [], 'the exit handler removed every open home once the ALA groups ended');
    for (const pid of pids) assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' }, `ALA process ${pid} ended`);
  } finally { await server.stop(); }
  const restarted = await startServer('three-homes', 'success', false, simulatedNewPython, { fixture });
  try {
    assert.deepEqual(await fs.readdir(restarted.homes()), [], 'homes/ is empty after the restart');
    const catalog = await restarted.request('/api/examples');
    assert.deepEqual(catalog[0].runs.map(run => run.status), ['interrupted', 'interrupted', 'interrupted']);
  } finally { await restarted.stop(); }
});

await test('a location runs can write is never imported, whether it is sent to the API or already saved; a saved location outside is used', { timeout: 30000 }, async () => {
  // A fake ALA in tmp/, which every run can write, whose discovery module leaves a marker if it is ever imported.
  const planted = await createFakeALA(path.join(suite, 'planted-ala'), ''), marker = path.join(suite, 'planted-ala-imported.md');
  await fs.writeFile(path.join(planted, 'src/coding-agents/discovery.mjs'), `import fs from 'node:fs';\nfs.writeFileSync(${JSON.stringify(marker)}, '# Imported\\n');\nexport async function discoverCodingAgents() { return []; }\n`);
  const rule = `This ALA location is not accepted: it lies inside ${path.join(await fs.realpath(root), 'tmp')}, where runs can write. Use a checkout outside it.`;
  const open = await startServer('no-ala-cli', 'missing', false, simulatedNewPython, { alaCli: false });
  try {
    assert.deepEqual(await open.request('/api/ala', { path: planted }, 400), { error: rule });
    assert.equal(fsSync.existsSync(path.join(open.directory, 'agent-settings.md')), false, 'nothing was saved');
  } finally { await open.stop(); }
  // A saved location that runs can write is refused when it is resolved, and never replaced by detection.
  const saved = await startServer('planted-saved', 'missing', false, simulatedNewPython, { alaCli: false, settings: `| ALA location | \`${planted}\` |\n` });
  try {
    const settings = await saved.request('/api/agent-settings');
    assert.equal(settings.ala.found, false); assert.deepEqual(settings.ala.checked.map(c => [c.source, c.reason]), [['configured', rule]]);
    assert.match((await saved.request('/api/coding-agents', undefined, 503)).error, /^ALA was not found/);
    assert.match((await saved.request('/api/coding-agents/codex/models', undefined, 503)).error, /^ALA was not found/);
  } finally { await saved.stop(); }
  assert.equal(fsSync.existsSync(marker), false, 'the planted discovery module was never imported');
  // A saved location outside tmp/ is used as it is, and saving it through the API keeps it.
  const { dir } = await fixtureBase('empty'), outside = await createFakeALA(path.join(dir, 'checkout'), '');
  const later = await startServer('outside-saved', 'missing', false, simulatedNewPython, { alaCli: false, settings: `| ALA location | \`${outside}\` |\n` });
  try {
    const before = await later.request('/api/agent-settings');
    assert.equal(before.ala.found, true); assert.equal(before.ala.source, 'configured');
    const after = await later.request('/api/ala', { path: outside });
    assert.equal(after.ala.found, true); assert.equal(after.ala.source, 'configured');
    assert.match(await fs.readFile(path.join(later.directory, 'agent-settings.md'), 'utf8'), new RegExp('\\| ALA location \\| `' + outside.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '` \\|'));
    assert.equal((await later.request('/api/coding-agents')).agents[0].id, 'codex');
  } finally { await later.stop(); }
});

await test('the server does not start with an agent-home base inside the repository or with a PORT that is no TCP port', { timeout: 30000 }, async () => {
  const start = async env => {
    const child = spawn(process.execPath, [path.join(root, 'tools/audit-ui.mjs')], { cwd: root, env: { ...cleanEnvironment(), AUDIT_RUNS_ROOT: path.join(suite, 'not-started-runs'), AUDIT_EXAMPLES_ROOT: path.join(suite, 'not-started-examples'), AUDIT_PYTHON: simulatedNewPython, ...env }, stdio: ['ignore', 'pipe', 'pipe'] });
    let output = ''; child.stdout.on('data', b => { output += b; }); child.stderr.on('data', b => { output += b; });
    const [code] = await once(child, 'exit'); return { code, output };
  };
  const { dir } = await fixtureBase('empty'), repository = await fs.realpath(root), inside = path.join(repository, 'tmp/markdown-server-tests', path.basename(suite), 'base-inside');
  const refusedBase = await start({ PORT: String(await freePort()), AUDIT_AGENT_HOME: inside });
  assert.equal(refusedBase.code, 1); assert.equal(refusedBase.output, `The pilot server did not start: ${reasons.insideRepository(inside, repository)}\n`);
  assert.equal(fsSync.existsSync(inside), false, 'the refused base was not created');
  for (const port of ['0', 'abc', '70000', '80.5']) {
    const refused = await start({ PORT: port, AUDIT_AGENT_HOME: path.join(dir, 'base') });
    assert.equal(refused.code, 1, port); assert.equal(refused.output, `The pilot server did not start: PORT must be a TCP port from 1 to 65535, not ${port}.\n`, port);
  }
});

await test('a second server on the same port fails to bind and leaves the first server working', { timeout: 30000 }, async () => {
  const first = await startServer('one-port', 'success');
  try {
    const second = spawn(process.execPath, [path.join(root, 'tools/audit-ui.mjs')], { cwd: root, env: { ...cleanEnvironment(), PORT: String(first.port), AUDIT_AGENT_HOME: first.base, AUDIT_RUNS_ROOT: path.join(first.directory, 'second-runs'), AUDIT_EXAMPLES_ROOT: first.examples, ALA_CLI: first.ala, AUDIT_PYTHON: simulatedNewPython }, stdio: ['ignore', 'pipe', 'pipe'] });
    let output = ''; second.stdout.on('data', b => { output += b; }); second.stderr.on('data', b => { output += b; });
    const [code] = await once(second, 'exit');
    assert.equal(code, 1); assert.match(output, /EADDRINUSE/); assert.doesNotMatch(output, /Audit laboratory/);
    assert.equal((await first.request('/api/examples')).length, 0, 'the first server still answers');
  } finally { await first.stop(); }
});

await test('without AUDIT_AGENT_HOME the server creates the default base under HOME (an injected fixture home, never the real one)', { timeout: 30000 }, async () => {
  const { dir } = await fixtureBase('empty'), home = path.join(dir, 'home'); await fs.mkdir(home);
  const server = await startServer('default-base', 'success', false, simulatedNewPython, { home });
  try {
    const base = path.join(home, '.lean-documents/agent-home');
    assert.equal(path.dirname(server.homes()), base);
    for (const folder of [path.join(home, '.lean-documents'), base]) assert.equal(fsSync.statSync(folder).mode & 0o777, 0o700, folder);
    assert.equal((await server.request('/api/examples')).length, 0);
    assert.match(server.output(), new RegExp(`Dedicated agent home: ${base.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\.`));
  } finally { await server.stop(); }
});

await test('error injection: whatever a run does to its home, the home is removed and no login copy is left (probes)', { timeout: 60000 }, async () => {
  const fixture = await fixtureBase();
  const server = await startServer('error-injection', 'success', false, simulatedNewPython, { fixture, env: { AUDIT_CONCURRENCY: '1' } });
  const homes = [];
  try {
    const { id } = await seed(server);
    const uploaded = await server.request(`/api/examples/${id}/files`, { role: 'report', files: ['remove', 'mode', 'link', 'nested', 'copy'].map(name => ({ name: `${name}.md`, data: Buffer.from(`# ${name}\n`).toString('base64') })) });
    for (const report of uploaded.reports) {
      const injection = report.label.replace('.md', '');
      const run = await server.request('/api/runs', { exampleId: id, reportPath: report.path, reviewNotes: `INJECT:${injection}` });
      const result = await server.terminal(run.id);
      assert.equal(result.status, 'incomplete', injection);
      const journal = await fs.readFile(path.join(server.runs, run.id, 'execution.md'), 'utf8');
      assert.match(journal, /agent home removed/, injection);
      homes.push([injection, /\| Agent home \| ([^|]+) \|/.exec(await fs.readFile(path.join(server.runs, run.id, 'fake-ala-invocation.md'), 'utf8'))[1], journal]);
    }
    const byName = Object.fromEntries(homes.map(([name, home, journal]) => [name, { home, journal }]));
    assert.match(byName.link.journal, /### Login files rewritten\n\n\.codex\/auth\.json/, 'a login replaced by a link is reported as rewritten');
    assert.match(byName.remove.journal, /### Login files rewritten\n\n\.codex\/auth\.json/, 'a removed home counts its login as rewritten');
    assert.match(byName.copy.journal, /### Login files rewritten\n\nnone/);
    for (const [name, home] of homes) assert.equal(fsSync.existsSync(home), false, `${name}: the home is gone`);
    assert.deepEqual(await fs.readdir(server.homes()), [], 'homes/ is empty');
    assert.deepEqual(await filesContaining(server.homes(), TOKEN), []); assert.deepEqual(await filesContaining(server.directory, TOKEN), [], 'no login copy anywhere in the server folder');
  } finally { await server.stop(); }
  // The outside file a run linked its login to survives the removal.
  const linked = (await fs.readdir(server.runs)).map(run => path.join(server.runs, run, 'outside-target.md')).filter(file => fsSync.existsSync(file));
  assert.equal(linked.length, 1); assert.equal(await fs.readFile(linked[0], 'utf8'), '# Outside file\n');
  // A login module that throws on import refuses new work with the module text; nothing is created.
  const broken = await startServer('throwing-login-module', 'success');
  try {
    await fs.writeFile(path.join(broken.ala, 'src/coding-agents/logins.mjs'), "throw new Error('simulated import failure');\n");
    const { id, report } = await seed(broken);
    assert.deepEqual(await broken.request('/api/runs', { exampleId: id, reportPath: report.path }, 503), { error: homeUnavailable(reasons.noModule()) });
    assert.deepEqual(await fs.readdir(broken.runs), []);
  } finally { await broken.stop(); }
});
