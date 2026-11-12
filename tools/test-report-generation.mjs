/** Isolated report-writer integration tests. The explicitly simulated ALA checkout never runs a coding agent. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createHash, randomBytes } from 'node:crypto';
import fs from 'node:fs/promises';
import fsSync from 'node:fs';
import os from 'node:os';
import { API_UNAUTHORIZED, readApiKey } from './api-auth.mjs';
import { noLogin } from './agent-home.mjs';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { writeExample, readExample } from './markdown-store.mjs';
import { createFakeALA } from './fake-ala-fixture.mjs';
import { execFileSync } from 'node:child_process';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
await fs.mkdir(path.join(ROOT, 'tmp/report-generation-tests'), { recursive: true });
const suite = await fs.mkdtemp(path.join(ROOT, 'tmp/report-generation-tests/run-'));
console.log(`Explicit simulated executor; retained test artifacts: ${suite}`);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
// Explicit simulation: the server resolves its interpreter through AUDIT_PYTHON. This shim answers the resolver's version
// probe with 3.12.0 and hands everything else (tools/report_draft.py, which also parses with Python 3.9) to the host's python3.
const hostPython = (() => { try { return execFileSync('python3', ['-c', 'import sys; print(sys.executable)'], { encoding: 'utf8' }).trim(); } catch { return '/usr/bin/python3'; } })();
const pythonShim = path.join(suite, 'python-shim');
await fs.writeFile(pythonShim, `#!/bin/sh\nif [ "$1" = "-I" ]; then echo 3.12.0; exit 0; fi\nexec '${hostPython.replace(/'/g, `'\\''`)}' "$@"\n`);
await fs.chmod(pythonShim, 0o755);
const digest = value => createHash('sha256').update(value).digest('hex');
const sourceBytes = Buffer.from('# Investment terms\r\n\r\n## S01 — Subscription\r\n\r\nThe proposed subscription is EUR 8 million for 20% of equity after issuance. All amounts are exact.\r\n');

// Body of the explicitly simulated ALA executor; the shared prelude supplies prompt, cwd, output and event().
const stub = `
const fsp=fs.promises;
(async()=>{
  await fsp.writeFile(path.join(cwd,'stub-input.md'),'# Simulated executor input\\n\\n'+prompt);
  if(prompt.includes('Audit ONLY')) {
    if(process.env.STUB_AUDIT_WAIT==='1') {await fsp.writeFile('stub-waiting.md','# Simulated waiting audit\\n');setInterval(()=>{},1000);return;}
    console.error('ala: Explicit simulated audit failure: no audit or proof was performed.');process.exitCode=7;return;
  }
  if(prompt.includes('Requested title: WAIT')) {
    await fsp.writeFile('stub-waiting.md','# Simulated waiting writer\\n');
    while(true){try{await fsp.access('stub-release.md');break;}catch{}await new Promise(r=>setTimeout(r,20));}
  }
  const names=await fsp.readdir('sources');const source='sources/'+names[0];
  const ro=prompt.includes('Language: Romanian');
  const report=prompt.includes('Requested title: INVALID')?'# Invalid draft\\n\\nThis deliberately invalid structural fixture has no anchored finding or required source citation. It must never be published.\\n':
    '# '+(ro?'Raport de investiție':'Investment report')+'\\n\\n## R01 — '+(ro?'Subscriere':'Subscription')+'\\n\\n'+(ro?'Subscrierea propusă este de 8 milioane EUR.':'The proposed subscription is EUR 8 million.')+'\\n\\n'+(ro?'Condițiile consemnate indică suma propusă și participația aferentă.':'The recorded transaction terms state the proposed amount and the corresponding ownership interest.')+' ['+(ro?'Condițiile tranzacției S01':'Transaction terms S01')+']('+source+'#s01--subscription).\\n';
  await fsp.writeFile('report.md',report);
  await fsp.writeFile('generation-notes.md','# Simulated generation notes\\n\\nThis document comes from an explicit test stub, not a coding agent. It tests publication boundaries only. Mode: '+(prompt.includes('Mode: with_errors')?'with_errors':'grounded')+'. No audit, semantic validation or proof was performed.\\n');
  if(prompt.includes('Requested title: MUTATE'))await fsp.appendFile(source,'\\nChanged by simulated writer.\\n');
  event({type:'coding-agent-message',agent,message:'Explicit simulated draft created; no real generation occurred.',outputKind:'assistant',outputComplete:true});
})();
`;

// Each server gets an agent-home base in the host temporary folder (never the real one) whose template holds a fixture
// Codex login with a random token; the token never reaches the repository. Bases are removed at exit by guarded path.
const TEMPORARY = fsSync.realpathSync.native(os.tmpdir()), TOKEN = `fixture-token-${randomBytes(12).toString('hex')}`, fixtures = [];
process.on('exit', () => { for (const dir of fixtures) if (path.dirname(dir) === TEMPORARY && path.basename(dir).startsWith('report-generation-base-')) fsSync.rmSync(dir, { recursive: true, force: true }); });
async function fixtureBase(login) {
  const dir = await fs.realpath(await fs.mkdtemp(path.join(TEMPORARY, 'report-generation-base-'))); fixtures.push(dir);
  const base = path.join(dir, 'base');
  for (const folder of [base, path.join(base, 'template'), ...(login ? [path.join(base, 'template/.codex')] : [])]) { await fs.mkdir(folder, { mode: 0o700 }); await fs.chmod(folder, 0o700); }
  if (login) { const file = path.join(base, 'template/.codex/auth.json'); await fs.writeFile(file, JSON.stringify({ tokens: { access_token: TOKEN } }), { mode: 0o600 }); await fs.chmod(file, 0o600); }
  return base;
}
// Test servers never inherit credential variables or an agent-home location from the runner's shell.
const cleanEnvironment = () => Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(OPENAI|ANTHROPIC|CLAUDE_CODE|CLAUDE|CODEX|OPENCODE|PI_CODING|OMP|GEMINI|GOOGLE|OPENROUTER|XAI|MISTRAL|GROQ|DEEPSEEK|AZURE_OPENAI)_|^AUDIT_AGENT_HOME$|^ALA_CLI$/.test(key)));

async function start(name, { missing = false, auditWait = false, login = true } = {}) {
  const directory = path.join(suite, name), examples = path.join(directory, 'examples'), runs = path.join(directory, 'runs'), jobs = path.join(directory, 'report-jobs');
  await fs.mkdir(examples, { recursive: true });
  const base = await fixtureBase(login);
  // A missing ALA location must refuse new work; otherwise a simulated ALA checkout outside the server folder runs.
  const ala = missing ? path.join(suite, name + '-missing-ala') : await createFakeALA(path.join(suite, name + '-fake-ala'), stub);
  const probe = net.createServer(); probe.listen(0, '127.0.0.1'); await once(probe, 'listening');
  const port = probe.address().port; await new Promise(resolve => probe.close(resolve));
  const child = spawn(process.execPath, [path.join(ROOT, 'tools/audit-ui.mjs')], { cwd: ROOT,
    env: { ...cleanEnvironment(), PORT: String(port), AUDIT_RUNS_ROOT: runs, AUDIT_EXAMPLES_ROOT: examples, AUDIT_DOCUMENT_PILOT: '1', AUDIT_TEST_MODE: '1', AUDIT_CONCURRENCY: '1', ALA_CLI: ala, AUDIT_AGENT_HOME: base, AUDIT_PYTHON: pythonShim, STUB_AUDIT_WAIT: auditWait ? '1' : '0' }, stdio: ['ignore', 'pipe', 'pipe'] });
  let output = ''; child.stdout.on('data', b => output += b); child.stderr.on('data', b => output += b);
  for (let n = 0; n < 100 && !output.includes('Audit laboratory:'); n++) { if (child.exitCode !== null) throw Error(output); await pause(20); }
  assert.match(output, /Audit laboratory:/);
  const origin = `http://127.0.0.1:${port}`, key = await readApiKey({ base, port });
  // Every request carries the key unless a test sends none.
  async function request(route, data, status = 200, { key: sent = key } = {}) {
    const auth = sent ? { Authorization: `Bearer ${sent}` } : {};
    const response = await fetch(origin + route, data === undefined ? { headers: auth } : { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: origin, ...auth }, body: JSON.stringify(data) });
    const value = await response.json(); assert.equal(response.status, status, `${route}: ${JSON.stringify(value)}`); return value;
  }
  async function waitFor(route, predicate) {
    for (let n = 0; n < 200; n++) { assert.equal(child.exitCode, null, output); const value = await request(route); if (predicate(value)) return value; await pause(25); }
    throw Error(`Timed out waiting for ${route}`);
  }
  return { directory, examples, runs, jobs, request, key, homes: () => path.join(fsSync.realpathSync.native(base), 'homes'),
    terminal: id => waitFor(`/api/report-jobs/${id}`, v => ['finished', 'failed', 'stopped'].includes(v.status)),
    auditTerminal: id => waitFor(`/api/runs/${id}`, v => !['queued', 'running', 'finalizing'].includes(v.status)),
    async waitMarker(base) { for (let n = 0; n < 150; n++) { try { await fs.access(path.join(base, 'stub-waiting.md')); return; } catch {} await pause(20); } throw Error('Waiting stub did not start'); },
    async close() { if (child.exitCode === null && child.signalCode === null) { child.kill('SIGTERM'); await once(child, 'exit'); } await fs.writeFile(path.join(directory, 'server-output.md'), '# Isolated server output\n\n```text\n' + output + '\n```\n'); assert.equal(output.includes(key), false, 'the key is not in the server output'); assert.equal(output.includes(TOKEN), false, 'the fixture token is not in the server output'); },
  };
}

async function example(server, { id = 'investment', language = 'en', sources = true } = {}) {
  const base = path.join(server.examples, id); await fs.mkdir(path.join(base, 'sources'), { recursive: true });
  if (sources) await fs.writeFile(path.join(base, 'sources/terms.md'), sourceBytes);
  await fs.writeFile(path.join(base, 'report.md'), '# Existing report\n\n## R01 — Subscription\n\nThe proposed subscription is EUR 8 million.\n');
  await writeExample(path.join(base, 'example.md'), { schema_version: 1, title: 'Investment terms', description: '', question: '', source_directory: 'sources', language, reports: [{ path: 'report.md', label: 'Existing report' }] });
  return { id, base };
}
const generate = (server, id, title = 'Investment draft', mode = 'grounded') => server.request(`/api/examples/${id}/reports/generate`, { title, mode });

await test('drafts preserve sources, honor mode/language and publish only the report; later audits exclude writer notes and replace old runs', { timeout: 25000 }, async () => {
  const server = await start('publication');
  try {
    for (const [language, mode] of [['en', 'grounded'], ['ro', 'with_errors']]) {
      const { id, base } = await example(server, { id: `investment-${language}`, language });
      const queued = await generate(server, id, 'Investment draft', mode);
      const job = await server.terminal(queued.id); assert.equal(job.status, 'finished', job.error);
      const directory = path.join(server.jobs, job.id);
      assert.deepEqual(await fs.readFile(path.join(directory, 'sources/terms.md')), sourceBytes);
      const records = JSON.parse(await fs.readFile(path.join(directory, 'internal/sources.json'), 'utf8'));
      assert.equal(records[0].sha256, digest(sourceBytes));
      // The draft's ALA process got its own agent home, directly after --cwd and inside <base>/homes; it is gone now.
      const stubInput = await fs.readFile(path.join(directory, 'stub-input.md'), 'utf8'), journal = await fs.readFile(path.join(directory, 'execution.md'), 'utf8');
      const command = JSON.parse(await fs.readFile(path.join(directory, 'internal/job.json'), 'utf8')).command.split(' '), home = command[command.indexOf('--home') + 1];
      assert.equal(command[command.indexOf('--cwd') + 2], '--home'); assert.equal(path.dirname(home), server.homes()); assert.equal(fsSync.existsSync(home), false);
      assert.match(journal, /Agent home prepared/i); assert.match(journal, /Agent home removed/i); assert.ok(stubInput.length > 0);
      const instructions = await fs.readFile(path.join(directory, 'instructions.md'), 'utf8');
      assert.ok(instructions.includes(`Language: ${language === 'ro' ? 'Romanian' : 'English'}`)); assert.ok(instructions.includes(`Mode: ${mode}`));
      assert.ok(instructions.includes('investment-report-writer/SKILL.md'));
      // The drafting prompt names the absolute interpreter in front of the validator and forbids running a script directly.
      assert.ok(instructions.includes(`Run ${pythonShim} ${path.join(await fs.realpath(ROOT), 'tools/report_draft.py')} --workspace ${directory}`), 'report_draft.py follows the interpreter path');
      assert.match(instructions, /using that absolute interpreter for every Python script and never running a script directly/);
      assert.equal(path.dirname(job.reportPath), '.');
      const metadata = await readExample(path.join(base, 'example.md'));
      assert.equal(metadata.reports.length, 2); assert.equal(metadata.reports[1].path, job.reportPath);
      const published = await fs.readFile(path.join(base, job.reportPath)); assert.equal(digest(published), job.reportSha256);
      assert.deepEqual((await fs.readdir(base)).sort(), ['example.md', 'report.md', job.reportPath, 'sources'].sort());
      const catalog = await server.request('/api/examples');
      assert.ok(catalog.find(v => v.id === id).generationJobs.some(v => v.id === job.id));
      assert.ok(job.files.every(file => !file.path.startsWith('internal/') && !file.path.endsWith('.json')));
      const notes = await server.request('/api/file?' + new URLSearchParams({ job: job.id, path: 'generation-notes.md' })); assert.match(notes.text, /explicit test stub/);
      await server.request('/api/file?' + new URLSearchParams({ job: job.id, path: '../outside.md' }), undefined, 400);
      const first = await server.request('/api/runs', { exampleId: id, reportPath: job.reportPath });
      assert.equal((await server.auditTerminal(first.id)).status, 'failed');
      assert.deepEqual(await fs.readFile(path.join(server.runs, first.id, 'inputs/report.md')), published);
      assert.deepEqual(await fs.readFile(path.join(server.runs, first.id, 'inputs/sources/terms.md')), sourceBytes);
      assert.deepEqual((await fs.readdir(path.join(server.runs, first.id, 'inputs'))).sort(), ['report.md', 'sources']);
      assert.deepEqual(await fs.readdir(path.join(server.runs, first.id, 'inputs/sources')), ['terms.md']);
      const next = await server.request('/api/runs', { exampleId: id, reportPath: job.reportPath }); await server.auditTerminal(next.id);
      await assert.rejects(fs.stat(path.join(server.runs, first.id)), { code: 'ENOENT' });
      const refreshed = (await server.request('/api/examples')).find(v => v.id === id);
      assert.deepEqual(refreshed.runs.map(v => v.id), [next.id]);
    }
  } finally { await server.close(); }
});

await test('a draft needs the API key and a pilot login; without either it is refused and no job folder is created', { timeout: 15000 }, async () => {
  const server = await start('draft-refusals', { login: false });
  try {
    const { id } = await example(server);
    assert.deepEqual(await server.request(`/api/examples/${id}/reports/generate`, { title: 'Draft', mode: 'grounded' }, 401, { key: null }), { error: API_UNAUTHORIZED });
    assert.deepEqual(await server.request(`/api/examples/${id}/reports/generate`, { title: 'Draft', mode: 'grounded' }, 401, { key: randomBytes(32).toString('base64url') }), { error: API_UNAUTHORIZED });
    const refused = await server.request(`/api/examples/${id}/reports/generate`, { title: 'Draft', mode: 'grounded' }, 400);
    assert.equal(refused.error, noLogin('codex', path.dirname(server.homes())));
    assert.deepEqual(await fs.readdir(server.jobs), [], 'no job folder is created');
  } finally { await server.close(); }
});

await test('invalid mode and missing sources are rejected before a job starts', { timeout: 10000 }, async () => {
  const server = await start('invalid-requests');
  try {
    const one = await example(server), empty = await example(server, { id: 'empty', sources: false });
    await server.request(`/api/examples/${one.id}/reports/generate`, { title: 'Draft', mode: 'unsupported' }, 400);
    await server.request(`/api/examples/${empty.id}/reports/generate`, { title: 'Draft', mode: 'grounded' }, 400);
    assert.deepEqual(await fs.readdir(server.jobs), []);
  } finally { await server.close(); }
});

await test('missing ALA and structurally invalid drafts fail without publication', { timeout: 15000 }, async () => {
  for (const missing of [true, false]) {
    const server = await start(missing ? 'missing-executor' : 'invalid-draft', { missing });
    try {
      const { id, base } = await example(server);
      if (missing) {
        const refused = await server.request(`/api/examples/${id}/reports/generate`, { title: 'Draft', mode: 'grounded' }, 400);
        assert.match(refused.error, /ALA was not found/);
        assert.deepEqual(await fs.readdir(server.jobs), [], 'no draft job is created while ALA is missing');
      } else {
        const queued = await generate(server, id, 'INVALID draft');
        const job = await server.terminal(queued.id); assert.equal(job.status, 'failed');
        assert.match(job.error, /validation|anchor/i);
      }
      assert.equal((await readExample(path.join(base, 'example.md'))).reports.length, 1);
      assert.ok(!(await fs.readdir(base)).some(file => file.startsWith('report-generated-')));
    } finally { await server.close(); }
  }
});

await test('changing original or retained source bytes blocks draft publication', { timeout: 15000 }, async () => {
  const server = await start('source-changes');
  try {
    for (const retained of [false, true]) {
      const { id, base } = await example(server, { id: retained ? 'retained-change' : 'original-change' });
      const queued = await generate(server, id, retained ? 'MUTATE draft' : 'WAIT draft');
      if (!retained) {
        const directory = path.join(server.jobs, queued.id); await server.waitMarker(directory);
        await fs.appendFile(path.join(base, 'sources/terms.md'), '\nA later source amendment.\n');
        await fs.writeFile(path.join(directory, 'stub-release.md'), '# Release simulated writer\n');
      }
      const job = await server.terminal(queued.id); assert.equal(job.status, 'failed'); assert.match(job.error, /source.*changed/i);
      assert.equal((await readExample(path.join(base, 'example.md'))).reports.length, 1);
    }
  } finally { await server.close(); }
});

await test('example stop cancels both an executing process and its queued sibling job', { timeout: 20000 }, async () => {
  for (const auditFirst of [true, false]) {
    const server = await start(auditFirst ? 'stop-audit-first' : 'stop-writer-first', { auditWait: true });
    try {
      const { id } = await example(server);
      let audit, job;
      if (auditFirst) {
        audit = await server.request('/api/runs', { exampleId: id, reportPath: 'report.md' }); await server.waitMarker(path.join(server.runs, audit.id));
        job = await generate(server, id, 'WAIT draft'); assert.equal((await server.request(`/api/report-jobs/${job.id}`)).status, 'queued');
      } else {
        job = await generate(server, id, 'WAIT draft'); await server.waitMarker(path.join(server.jobs, job.id));
        audit = await server.request('/api/runs', { exampleId: id, reportPath: 'report.md' }); assert.equal((await server.request(`/api/runs/${audit.id}`)).status, 'queued');
      }
      const stopped = await server.request(`/api/examples/${id}/stop`, {});
      assert.deepEqual(new Set(stopped.jobs), new Set([audit.id, job.id]));
      assert.equal((await server.terminal(job.id)).status, 'stopped');
      assert.equal((await server.auditTerminal(audit.id)).status, 'stopped');
      assert.ok(!(await fs.readdir(path.join(server.examples, id))).some(name => name.startsWith('report-generated-')));
    } finally { await server.close(); }
  }
});
