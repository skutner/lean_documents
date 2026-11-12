/** Offline unit checks for the ALA integration. Catalog metadata is hand-authored; no coding agent or model is called. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createHash } from 'node:crypto';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { alaArguments, alaEnvironment, certificateEnvironment, MACOS_CA_BUNDLE, createALACatalog, createALAJournal, createSettingsStore, formatSettings, homeRequired, loginCheckUnavailable, parseSettings, readALACatalog, readALALogin, resolveALA, locationRefused, ABSENT_CONFIG, DEFAULT_SETTINGS } from './ala.mjs';
import { createFakeALA } from './fake-ala-fixture.mjs';

test('settings round-trip through a readable Markdown record and reject unsafe values', async () => {
  assert.deepEqual(parseSettings(formatSettings({ agent: 'claude', model: 'claude-x/model:1', effort: 'high' })), { agent: 'claude', model: 'claude-x/model:1', effort: 'high' });
  assert.deepEqual(parseSettings(formatSettings({ agent: 'codex', model: '', effort: '' })), DEFAULT_SETTINGS);
  assert.match(formatSettings(DEFAULT_SETTINGS), /\| Model \| Agent default \|/);
  // The API can no longer save where ALA is (A1): an "ALA location" row of an earlier version is ignored and not written again.
  assert.doesNotMatch(formatSettings({ ...DEFAULT_SETTINGS, location: '/opt/AdvancedLanguageAgent' }), /ALA location/);
  assert.deepEqual(parseSettings('| Coding agent | claude |\n| ALA location | `/opt/AdvancedLanguageAgent` |\n'), { ...DEFAULT_SETTINGS, agent: 'claude' });
  assert.deepEqual(parseSettings('| Coding agent | bad agent; rm |\n'), DEFAULT_SETTINGS);
  assert.deepEqual(parseSettings(''), DEFAULT_SETTINGS);
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'ala-settings-'));
  try {
    const store = createSettingsStore(path.join(directory, 'nested', 'agent-settings.md'));
    assert.deepEqual(store.read(), DEFAULT_SETTINGS, 'a missing record means the Codex default');
    assert.deepEqual(await store.write({ agent: 'pi', model: 'm', effort: '' }), { agent: 'pi', model: 'm', effort: '' });
    assert.deepEqual((await fs.readdir(path.dirname(store.file))), ['agent-settings.md'], 'atomic write leaves no temporary file');
    await fs.writeFile(store.file, '| Coding agent | codex |\n| ALA location | `/opt/AdvancedLanguageAgent` |\n');
    await store.write({ ...store.read(), model: 'm2' });
    assert.doesNotMatch(await fs.readFile(store.file, 'utf8'), /ALA location/, 'the old row disappears when the record is next written');
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
});

test('ALA arguments pass the selection explicitly, isolate personal defaults and mount the repository read-only', () => {
  const base = { entry: '/ala/bin/ala.mjs', cwd: '/repo/tmp/runs/r1', home: null, root: '/repo', taskFile: '/repo/tmp/runs/r1/instructions.md', output: '/repo/tmp/runs/r1/final-message.md' };
  const args = alaArguments({ ...base, selection: { agent: 'codex', model: 'm1', effort: 'high' } }), value = flag => args[args.indexOf(flag) + 1];
  assert.equal(args[0], '/ala/bin/ala.mjs');
  assert.equal(value('--ca'), 'codex'); assert.equal(value('--model'), 'm1'); assert.equal(value('--effort'), 'high');
  assert.equal(value('--cwd'), base.cwd); assert.equal(value('--folder'), '/repo');
  // Your own home (null): no --home, the repository mount follows the working folder directly.
  assert.equal(args.includes('--home'), false); assert.equal(args[args.indexOf('--cwd') + 2], '--folder');
  // A set folder comes directly after the working folder, once.
  const set = alaArguments({ ...base, home: '/h', selection: { agent: 'codex' } });
  assert.deepEqual(set.slice(set.indexOf('--cwd'), set.indexOf('--cwd') + 4), ['--cwd', base.cwd, '--home', '/h']);
  assert.equal(set.filter(arg => arg === '--home').length, 1);
  // A forgotten argument never means your own home (A3).
  const { home: _, ...withoutHome } = base;
  assert.throws(() => alaArguments({ ...withoutHome, selection: { agent: 'codex' } }), { message: homeRequired });
  assert.equal(homeRequired, 'The agent home must be given: null for your own home, or an absolute folder');
  for (const home of ['', 'relative', 5, false]) assert.throws(() => alaArguments({ ...base, home, selection: { agent: 'codex' } }), { message: 'The agent home must be an absolute path' }, String(home));
  for (const home of ['/repo', '/repo/tmp/agent-home', base.cwd, base.cwd + '/home']) assert.throws(() => alaArguments({ ...base, home, selection: { agent: 'codex' } }), { message: 'The agent home must lie outside the repository and the run folder' }, home); assert.equal(args[args.indexOf('--folder') + 2], '--taskFile', 'the repository mount has no write modifier');
  assert.equal(value('--config'), path.join(base.cwd, ABSENT_CONFIG));
  assert.equal(value('--taskFile'), base.taskFile); assert.equal(value('--output'), base.output);
  const defaults = alaArguments({ ...base, selection: { agent: 'claude', effort: 'high' } });
  assert.equal(defaults.includes('--model'), false); assert.equal(defaults.includes('--effort'), false, 'effort needs a model');
  assert.throws(() => alaArguments({ ...base, selection: { agent: '--force' } }), /Invalid coding agent/);
  assert.deepEqual(parseSettings(formatSettings({ agent: 'codex', model: '--force', effort: '-x' })), DEFAULT_SETTINGS, 'option-like identifiers are never saved');
  const env = alaEnvironment({ PATH: '/bin', PLOINKY_ROUTER: 'x', CODEX_HOME: '/h/.codex' }, { platform: 'linux' });
  assert.deepEqual(env, { PATH: '/bin', CODEX_HOME: '/h/.codex', ALA_EVENT_STREAM: '1' });
  // macOS: the system CA bundle is named for agents that cannot reach the certificate service from the sandbox, unless
  // the caller chose certificates already or the bundle is missing. Other platforms are left alone.
  const present = { platform: 'darwin', exists: file => file === MACOS_CA_BUNDLE }, plain = { PATH: '/bin' };
  assert.equal(MACOS_CA_BUNDLE, '/etc/ssl/cert.pem');
  assert.deepEqual(certificateEnvironment(plain, present), { PATH: '/bin', SSL_CERT_FILE: '/etc/ssl/cert.pem' }); assert.deepEqual(plain, { PATH: '/bin' }, 'the given environment is not changed');
  assert.deepEqual(alaEnvironment({ PATH: '/bin', PLOINKY_ROUTER: 'x' }, present), { PATH: '/bin', SSL_CERT_FILE: '/etc/ssl/cert.pem', ALA_EVENT_STREAM: '1' });
  for (const chosen of [{ SSL_CERT_FILE: '/my/ca.pem' }, { SSL_CERT_DIR: '/my/certs' }]) assert.deepEqual(certificateEnvironment({ ...plain, ...chosen }, present), { ...plain, ...chosen }, 'a choice of the caller is kept');
  assert.equal(certificateEnvironment(plain, { platform: 'darwin', exists: () => false }), plain, 'no bundle, no variable');
  for (const platform of ['linux', 'win32']) assert.equal(certificateEnvironment(plain, { platform, exists: () => true }), plain, platform);
  assert.equal(certificateEnvironment({ ...plain, SSL_CERT_FILE: '' }, present).SSL_CERT_FILE, '/etc/ssl/cert.pem', 'an empty value is no choice');
});

test('the journal joins streamed fragments, keeps complete outputs separate and explains failures', () => {
  const records = [], journal = createALAJournal((type, details) => records.push([type, details]));
  const event = value => journal.line('@@ALA_EVENT@@' + JSON.stringify(value));
  event({ type: 'coding-agent-selected', agent: 'claude', permissionMode: 'full-access' });
  event({ type: 'coding-agent-message', agent: 'claude', message: 'Reading ', outputKind: 'assistant', outputId: 'a' });
  event({ type: 'coding-agent-message', agent: 'claude', message: 'sources.', outputKind: 'assistant', outputId: 'a' });
  event({ type: 'coding-agent-message', agent: 'claude', message: '', outputKind: 'assistant', outputId: 'a', outputComplete: true });
  event({ type: 'coding-agent-message', agent: 'claude', message: 'Bash: {"command":"ls"}\n', outputKind: 'output' });
  event({ type: 'coding-agent-message', agent: 'claude', message: 'report.md\n', outputKind: 'output' });
  event({ type: 'coding-agent-message', agent: 'claude', message: 'Unfinished ', outputKind: 'assistant', outputId: 'b' });
  journal.line('[warning] native diagnostic');
  journal.line('@@ALA_EVENT@@{not json');
  event({ type: 'coding-agent-final', agent: 'claude', message: 'Done.' });
  journal.line('ala: Coding-agent execution requires Bubblewrap');
  journal.flush();
  // Unrelated diagnostics do not split a streamed message; the next event flushes it.
  assert.deepEqual(records.map(([type]) => type), ['coding agent selected', 'agent message', 'agent output', 'agent output', 'process diagnostic', 'process diagnostic', 'agent message', 'final agent message', 'process diagnostic']);
  assert.equal(records[0][1].model, 'Agent default');
  assert.equal(records[1][1].text, 'Reading sources.');
  assert.equal(records[2][1].aggregated_output, 'Bash: {"command":"ls"}\n');
  assert.equal(records[6][1].text, 'Unfinished ', 'a pending fragment is flushed before the next event record');
  assert.equal(records[7][1].text, 'Done.');
  assert.equal(journal.failure(), 'ala: Coding-agent execution requires Bubblewrap');
});

test('the catalog has no reader that reaches ALA without an agent home: null is your own home, a forgotten one is an error', async () => {
  assert.throws(() => createALACatalog(() => ({ entry: '/ala/bin/ala.mjs' })), /needs a read\(agent\) function that supplies an agent home/);
  await assert.rejects(readALACatalog('/ala/bin/ala.mjs'), { message: homeRequired, status: 400 });
  await assert.rejects(readALACatalog('/ala/bin/ala.mjs', undefined, 'codex'), { message: homeRequired, status: 400 });
  await assert.rejects(readALACatalog('/ala/bin/ala.mjs', 'relative'), { message: 'The agent home must be an absolute path', status: 400 });
});

test('the catalog caches metadata and saves only an installed agent with a model and effort it reports', async () => {
  let calls = 0, failing = false;
  const catalog = createALACatalog(() => null, { read: async agent => { calls++; if (failing) throw Object.assign(Error('Catalog unavailable'), { status: 503 }); return agent ? { models: [{ id: 'm1', label: 'Model 1', efforts: ['low', 'high', 'bad effort', 'low'] }, { id: 'm1' }, { id: 'bad|model' }, { id: null }] } : { agents: [{ id: 'codex', available: true }, { id: 'absent', available: false }, { id: 'bad agent', available: true }] }; } });
  await Promise.all([catalog.agents(), catalog.agents()]); assert.equal(calls, 1);
  assert.deepEqual((await catalog.agents()).agents, [{ id: 'codex', label: 'codex', available: true }, { id: 'absent', label: 'absent', available: false }]);
  assert.deepEqual((await catalog.models('codex')).models, [{ id: 'm1', label: 'Model 1', efforts: ['low', 'high'] }]);
  await catalog.models('codex', { refresh: true }); assert.equal(calls, 3);
  await assert.rejects(catalog.models('absent'), error => error.status === 400);
  assert.deepEqual(await catalog.validate({ agent: 'codex', model: '' }), { agent: 'codex', model: '', effort: '' });
  assert.deepEqual(await catalog.validate({ agent: 'codex', model: 'm1', effort: 'high' }), { agent: 'codex', model: 'm1', effort: 'high' });
  for (const invalid of [{ agent: 'absent', model: '' }, { agent: 'codex', model: 'other' }, { agent: 'codex', model: 'm1', effort: 'max' }, { agent: 'codex', model: '', effort: 'low' }, { agent: 'codex', model: 1 }, null]) {
    await assert.rejects(catalog.validate(invalid), error => error.status === 400, JSON.stringify(invalid));
  }
  failing = true; await assert.rejects(catalog.models('codex', { refresh: true }), /Catalog unavailable/);
  failing = false; assert.equal((await catalog.models('codex')).models.length, 1);
});

test('ALA location: ALA_CLI overrides automatic detection, and an invalid ALA_CLI is reported, never replaced', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'ala-location-'));
  const saved = process.env.ALA_CLI; delete process.env.ALA_CLI;
  try {
    const ala = await createFakeALA(path.join(directory, 'AdvancedLanguageAgent'), '');
    const app = path.join(directory, 'lean_documents'); await fs.mkdir(app);
    const parent = resolveALA(app);
    assert.equal(parent.found, true); assert.equal(parent.source, 'parent'); assert.equal(parent.entry, path.join(await fs.realpath(ala), 'bin/ala.mjs'));
    assert.equal('configured' in parent, false, 'there is no saved location');
    process.env.ALA_CLI = path.join(ala, 'bin/ala.mjs');
    assert.equal(resolveALA(app).source, 'environment');
    process.env.ALA_CLI = path.join(directory, 'missing');
    const wrong = resolveALA(app);
    assert.equal(wrong.found, false, 'an invalid ALA_CLI is never replaced by automatic detection'); assert.deepEqual(wrong.checked.map(c => [c.source, c.reason]), [['environment', 'Not found.']]);
    const other = path.join(directory, 'other'); await fs.mkdir(path.join(other, 'bin'), { recursive: true });
    await fs.writeFile(path.join(other, 'bin/ala.mjs'), ''); await fs.writeFile(path.join(other, 'package.json'), '{"name":"something-else"}');
    process.env.ALA_CLI = other;
    assert.equal(resolveALA(app).checked[0].reason, 'This location is not an ALA installation.');
  } finally {
    if (saved === undefined) delete process.env.ALA_CLI; else process.env.ALA_CLI = saved;
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test('ALA location: the copy npm install puts under node_modules is found after a checkout beside or inside the repository', async () => {
  const directory = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'ala-dependency-')));
  const saved = process.env.ALA_CLI, savedPath = process.env.PATH; delete process.env.ALA_CLI; process.env.PATH = path.join(directory, 'empty-path');
  try {
    const app = path.join(directory, 'lean_documents'); await fs.mkdir(app);
    const missing = resolveALA(app);
    assert.equal(missing.found, false); assert.deepEqual(missing.checked.map(c => c.source), ['parent', 'application', 'dependency']);
    const installed = await createFakeALA(path.join(app, 'node_modules/advanced-language-agent'), '');
    const found = resolveALA(app);
    assert.equal(found.found, true); assert.equal(found.source, 'dependency'); assert.equal(found.entry, path.join(installed, 'bin/ala.mjs'));
    // npm puts node_modules/.bin on PATH, where `ala` is a link to the same entry: the dependency is named first.
    await fs.mkdir(path.join(app, 'node_modules/.bin')); await fs.symlink('../advanced-language-agent/bin/ala.mjs', path.join(app, 'node_modules/.bin/ala'));
    process.env.PATH = path.join(app, 'node_modules/.bin');
    assert.equal(resolveALA(app).source, 'dependency');
    // A checkout inside or beside the repository still comes first.
    await createFakeALA(path.join(app, 'AdvancedLanguageAgent'), ''); assert.equal(resolveALA(app).source, 'application');
    await createFakeALA(path.join(directory, 'AdvancedLanguageAgent'), ''); assert.equal(resolveALA(app).source, 'parent');
  } finally {
    if (saved === undefined) delete process.env.ALA_CLI; else process.env.ALA_CLI = saved;
    process.env.PATH = savedPath;
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test('the ALA location rule refuses an automatic location inside tmp/ or an added folder by folder identity, and detection moves on', async () => {
  const directory = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'ala-rule-')));
  const saved = process.env.ALA_CLI, savedPath = process.env.PATH; delete process.env.ALA_CLI; process.env.PATH = path.join(directory, 'empty-path');
  try {
    const root = path.join(directory, 'lean_documents'), tmp = path.join(root, 'tmp');
    const planted = await createFakeALA(path.join(tmp, 'run-1/ala'), '');
    const text = locationRefused(tmp);
    assert.equal(text, `This ALA location is not accepted: it lies inside ${tmp}, where runs can write. Use a checkout outside it.`);
    // <root>/AdvancedLanguageAgent is a link into tmp/: refused, and the installed copy is used instead.
    await fs.symlink(planted, path.join(root, 'AdvancedLanguageAgent'));
    const installed = await createFakeALA(path.join(root, 'node_modules/advanced-language-agent'), '');
    const resolved = resolveALA(root);
    assert.equal(resolved.found, true); assert.equal(resolved.source, 'dependency'); assert.equal(resolved.entry, path.join(installed, 'bin/ala.mjs'));
    assert.deepEqual(resolved.checked.find(c => c.source === 'application'), { source: 'application', location: path.join(root, 'AdvancedLanguageAgent'), reason: text, refusedInside: tmp });
    // `ala` on PATH inside tmp/ (a case-insensitive volume reaches it as TMP/ as well).
    await fs.rm(path.join(root, 'node_modules'), { recursive: true });
    const bin = path.join(tmp, 'bin'); await fs.mkdir(bin); await fs.symlink(path.join(planted, 'bin/ala.mjs'), path.join(bin, 'ala'));
    process.env.PATH = fsExists(path.join(root, 'TMP')) ? path.join(root, 'TMP/bin') : bin;
    const onPath = resolveALA(root);
    assert.equal(onPath.found, false); assert.deepEqual(onPath.checked.find(c => c.source === 'path')?.reason, text);
    // A folder the caller adds (the server adds the folder set with npm run set-home) is refused too; <root>/tmp stays refused.
    const setFolder = path.join(directory, 'set-home-folder'), beside = await createFakeALA(path.join(setFolder, 'AdvancedLanguageAgent'), '');
    await fs.rm(path.join(root, 'AdvancedLanguageAgent')); await fs.symlink(beside, path.join(root, 'AdvancedLanguageAgent'));
    process.env.PATH = path.join(directory, 'empty-path');
    assert.equal(resolveALA(root).source, 'application', 'outside every refused folder it is used');
    const refused = resolveALA(root, { refused: [setFolder] });
    assert.equal(refused.found, false); assert.deepEqual(refused.checked.find(c => c.source === 'application').refusedInside, setFolder);
    // ALA_CLI is the operator's own statement and is never refused.
    process.env.ALA_CLI = planted;
    assert.equal(resolveALA(root, { refused: [setFolder] }).source, 'environment');
  } finally {
    if (saved === undefined) delete process.env.ALA_CLI; else process.env.ALA_CLI = saved;
    process.env.PATH = savedPath;
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test('the login check runs in the worker: it reports the login of the home, a module it cannot use, and is killed when it blocks', async () => {
  const { execFile } = await import('node:child_process');
  const worker = path.join(path.dirname(new URL(import.meta.url).pathname), 'ala-catalog-worker.mjs');
  const directory = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'ala-login-'))), savedVariables = {};
  try {
    const ala = await createFakeALA(path.join(directory, 'ala'), ''), entry = path.join(ala, 'bin/ala.mjs');
    // The worker inherits this process's environment: a credential variable exported in the developer's shell would count as a login.
    for (const name of ['OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN']) if (name in process.env) { savedVariables[name] = process.env[name]; delete process.env[name]; }
    // Both homes are fixtures under the temporary folder; a folder is always passed, so the real home is never looked at.
    const signedIn = path.join(directory, 'signed-in'), empty = path.join(directory, 'empty');
    await fs.mkdir(path.join(signedIn, '.codex'), { recursive: true }); await fs.writeFile(path.join(signedIn, '.codex/auth.json'), JSON.stringify({ tokens: { access_token: 'fixture' } })); await fs.mkdir(empty);
    assert.deepEqual(await readALALogin(entry, signedIn, 'codex'), { login: { loggedIn: true, file: true, env: [], files: [path.join(signedIn, '.codex/auth.json')] } });
    assert.deepEqual(await readALALogin(entry, empty, 'codex'), { login: { loggedIn: false, file: false, env: [], files: [path.join(empty, '.codex/auth.json')] } });
    // The worker itself, with the caller's home (`-`) as a fixture and a credential variable instead of a file.
    const run = (args, env) => new Promise(resolve => execFile(process.execPath, [worker, ...args], { env: { PATH: process.env.PATH, HOME: empty, ...env } }, (error, stdout, stderr) => resolve({ code: error ? error.code : 0, stdout, stderr })));
    const variable = await run([entry, '-', '--login', 'codex'], { OPENAI_API_KEY: 'fixture-value' });
    assert.equal(variable.code, 0, variable.stderr); assert.deepEqual(JSON.parse(variable.stdout).login, { loggedIn: true, file: false, env: ['OPENAI_API_KEY'], files: [path.join(empty, '.codex/auth.json')] });
    assert.equal(variable.stdout.includes('fixture-value'), false, 'the value of a credential variable is never printed');
    // Misuse of the caller's side is refused before a process starts.
    await assert.rejects(readALALogin(entry, undefined, 'codex'), { message: homeRequired });
    await assert.rejects(readALALogin(entry, 'relative', 'codex'), /absolute path/);
    await assert.rejects(readALALogin(entry, empty, '--login'), /Choose an installed coding agent/);
    await assert.rejects(readALALogin(entry, empty, 'unknown-agent'), { status: 503, message: loginCheckUnavailable('unknown-agent') });
    // A login module that cannot be used is named as such, not as a missing login.
    const modulePath = path.join(ala, 'src/coding-agents/logins.mjs'), original = await fs.readFile(modulePath, 'utf8');
    await fs.writeFile(modulePath, "throw new Error('simulated import failure');\n");
    assert.deepEqual(await readALALogin(entry, empty, 'codex'), { problem: 'noModule' });
    // A check that blocks (as ALA's own does on a login file swapped for a FIFO) is killed: the caller gets 503 and goes on.
    await fs.writeFile(modulePath, "export const homeEnvironment = (env, home) => ({ ...env, HOME: home });\nexport const codingAgentLoginFiles = () => [];\nexport function detectCodingAgentLogin() { Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0); }\nexport const detectCodingAgentLogins = () => [];\n");
    const started = Date.now(); let ticks = 0; const ticking = setInterval(() => { ticks++; }, 20);
    await assert.rejects(readALALogin(entry, empty, 'codex', { timeout: 400 }), { status: 503, message: loginCheckUnavailable('codex') });
    clearInterval(ticking);
    assert.ok(Date.now() - started < 4000, 'the blocked check ended at its timeout');
    assert.ok(ticks >= 10, 'the caller kept running while the check was blocked');
    await fs.writeFile(modulePath, original);
    assert.equal((await readALALogin(entry, signedIn, 'codex')).login.loggedIn, true, 'a later check works again');
  } finally { Object.assign(process.env, savedVariables); await fs.rm(directory, { recursive: true, force: true }); }
});

test('the catalog worker needs a home folder or "-", and lists with the environment of the home a run would get', async () => {
  const { execFile } = await import('node:child_process');
  const worker = path.join(path.dirname(new URL(import.meta.url).pathname), 'ala-catalog-worker.mjs');
  const directory = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'ala-worker-')));
  try {
    const ala = await createFakeALA(path.join(directory, 'ala'), ''), entry = path.join(ala, 'bin/ala.mjs'), records = path.join(directory, 'records'), home = path.join(directory, 'home');
    // The caller's own home is a fixture under the temporary folder, never the real one.
    const callerHome = path.join(directory, 'caller-home'), callerCodex = path.join(callerHome, '.codex');
    await fs.mkdir(records); await fs.mkdir(home, { mode: 0o700 }); await fs.mkdir(callerHome);
    const run = args => new Promise(resolve => execFile(process.execPath, [worker, ...args], { env: { PATH: process.env.PATH, ...(process.env.TMPDIR ? { TMPDIR: process.env.TMPDIR } : {}), HOME: callerHome, FAKE_ALA_RECORDS: records, CODEX_HOME: callerCodex } }, (error, stdout, stderr) => resolve({ code: error ? error.code : 0, stdout, stderr })));
    await fs.symlink(home, path.join(directory, 'home-link'));
    for (const args of [[entry], [entry, 'relative'], [entry, path.join(directory, 'missing'), 'codex'], [entry, path.join(directory, 'missing')], [entry, path.join(directory, 'home-link'), 'codex']]) {
      const result = await run(args); assert.equal(result.code, 1, args.join(' ')); assert.equal(result.stderr, 'An agent home folder or "-" is required.\n'); assert.equal(result.stdout, '');
    }
    assert.deepEqual(await fs.readdir(records), [], 'nothing of ALA was loaded or run');
    const texts = async () => Promise.all((await fs.readdir(records)).sort().map(name => fs.readFile(path.join(records, name), 'utf8')));
    const sha = value => (createHash('sha256').update(value).digest('hex'));
    // `-`: your own home. Discovery sees the caller's HOME; the service gets no home and the caller's CODEX_HOME.
    const list = await run([entry, '-']); assert.equal(list.code, 0, list.stderr);
    assert.deepEqual(JSON.parse(list.stdout).agents.map(a => a.id), ['codex', 'claude', 'pi']);
    const own = await run([entry, '-', 'codex']); assert.equal(own.code, 0, own.stderr);
    assert.equal(JSON.parse(own.stdout).models[0].id, 'test-codex-model');
    let all = await texts();
    const discovery = all.filter(text => text.startsWith('# Simulated discovery')), [service] = all.filter(text => text.startsWith('# Simulated coding-agent service'));
    assert.equal(discovery.length, 2); for (const text of discovery) assert.ok(text.includes(`| Environment HOME | ${callerHome} |`), text);
    assert.match(service, /\| Agent home \| none \|/); assert.ok(service.includes(`| Environment HOME | ${callerHome} |`));
    assert.ok(service.includes('CODEX_HOME ' + sha(callerCodex)), "your own home: the caller's CODEX_HOME reaches ALA");
    // A folder: the environment ALA derives from it, never the caller's.
    await fs.rm(records, { recursive: true }); await fs.mkdir(records);
    const listed = await run([entry, home, 'codex']); assert.equal(listed.code, 0, listed.stderr);
    assert.equal(JSON.parse(listed.stdout).models[0].id, 'test-codex-model');
    all = await texts();
    const [folderDiscovery] = all.filter(text => text.startsWith('# Simulated discovery')), [folderService] = all.filter(text => text.startsWith('# Simulated coding-agent service'));
    assert.ok(folderDiscovery.includes(`| Environment HOME | ${home} |`), folderDiscovery);
    assert.match(folderService, new RegExp(`\\| Agent home \\| ${home.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} \\|`)); assert.match(folderService, /\| Agent home exists \| yes \|/);
    assert.match(folderService, new RegExp(`\\| Environment HOME \\| ${home.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} \\|`), 'the service gets homeEnvironment(env, home)');
    assert.ok(folderService.includes('CODEX_HOME ' + sha(path.join(home, '.codex'))), 'CODEX_HOME is derived from the home');
    assert.ok(!folderService.includes(sha(callerCodex)), "the caller's CODEX_HOME never reaches ALA");
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
});

test('the fake ALA refuses a --home outside the host temporary folder, before it reads or writes anything (A4)', async () => {
  const { execFile } = await import('node:child_process');
  const directory = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'ala-fake-guard-')));
  try {
    const ala = await createFakeALA(path.join(directory, 'ala'), ''), entry = path.join(ala, 'bin/ala.mjs'), cwd = path.join(directory, 'run'), inside = path.join(directory, 'home');
    await fs.mkdir(cwd); await fs.mkdir(inside); await fs.writeFile(path.join(cwd, 'task.md'), '# Task\n');
    const repository = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
    const run = home => new Promise(resolve => execFile(process.execPath, [entry, '--ca', 'codex', '--cwd', cwd, ...(home === undefined ? [] : ['--home', home]), '--taskFile', path.join(cwd, 'task.md'), '--output', path.join(cwd, 'out.md')], { env: { PATH: process.env.PATH, ...(process.env.TMPDIR ? { TMPDIR: process.env.TMPDIR } : {}), HOME: inside } }, (error, stdout, stderr) => resolve({ code: error ? error.code : 0, stderr })));
    // Never the developer's home: the repository and / stand for a folder outside the temporary folder.
    for (const home of [repository, '/', '']) {
      const refused = await run(home);
      assert.equal(refused.code, 1, home); assert.equal(refused.stderr, 'ala: the test fixture refuses a --home outside the temporary folder\n', home);
      assert.deepEqual(await fs.readdir(cwd), ['task.md'], 'nothing was recorded');
    }
    for (const home of [inside, undefined]) assert.equal((await run(home)).code, 0, String(home));
    const { createCodingAgentService } = await import(path.join(ala, 'src/coding-agents/service.mjs'));
    assert.throws(() => createCodingAgentService({ home: repository }), /refuses a home outside the temporary folder/);
    assert.equal(typeof createCodingAgentService({ home: inside }).listModels, 'function');
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
});

// A throwaway repository stub with a fake real home, a real sentinel outside it, a real Node helper and the reader. Nothing
// here touches the user's home. `probe()` runs the probe script directly, so no sandbox confines it.
async function probeFixture() {
  const m = await import('./check-ala-sandbox.mjs'), { execFile } = await import('node:child_process');
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'ala-probe-root-')));
  const id = 'probe-test', check = path.join(root, 'tmp/ala-sandbox-check', id), run = path.join(check, 'run'), reader = path.join(check, 'bin/read-environment.pl'), fakeHome = path.join(root, 'home');
  await fs.mkdir(run, { recursive: true }); await fs.mkdir(path.dirname(reader)); await fs.mkdir(fakeHome); await fs.mkdir(path.join(root, 'skills/evidence-to-proof-review'), { recursive: true });
  await fs.writeFile(path.join(root, 'skills/evidence-to-proof-review/SKILL.md'), '# Stub skill\n'); await fs.writeFile(reader, m.READER_PERL); await fs.writeFile(path.join(fakeHome, '.zshrc'), '# stub\n');
  const sentinel = await m.makeSentinel(root), home = await m.pickHomeEntry({ home: fakeHome }), helper = await m.startHelper();
  const python = await fs.access('/usr/bin/python3').then(() => '/usr/bin/python3', () => null), lean = path.join(root, 'tmp/toolchains/none/bin/lean');
  // The fake real home holds a Codex state folder.
  await m.makeCodexFixture(fakeHome);
  const { loadALALogins } = await import('./agent-home.mjs'), ala = await createFakeALA(path.join(root, 'ala'), ''), logins = await loadALALogins(path.join(ala, 'bin/ala.mjs'));
  const state = await m.agentStateFolders({ logins, home: fakeHome, probeHome: path.join(root, 'never-created') });
  const marker = { name: '.ala-sandbox-check-marker-test', value: 'marker-value-1' };
  const fx = { m, root, id, check, run, reader, fakeHome, sentinel, home, helper, state, marker, logins };
  fx.probe = async (overrides = {}, env = {}) => {
    const script = path.join(check, 'codex'); await fs.writeFile(script, m.probeScript({ run, root, check, id, python, lean, sentinel, home, helper, reader, state, marker, ...overrides }), { mode: 0o755 });
    const stdout = await new Promise((resolve, reject) => execFile('/bin/sh', [script, 'exec', '--json', 'ignored prompt'], { cwd: run, env: { ...process.env, HOME: fakeHome, ...env } }, (error, out) => error ? reject(error) : resolve(out)));
    const tsv = await fs.readFile(path.join(run, 'probe-results.tsv'), 'utf8'), checks = m.evaluate(tsv, { mode: overrides.mode ?? 'folder' });
    return { stdout, tsv, checks, result: Object.fromEntries(checks.map(c => [c.key, c])) };
  };
  fx.close = async () => {
    await fs.chmod(sentinel.dir, 0o700).catch(() => {}); await fs.chmod(fakeHome, 0o700).catch(() => {});
    const stopped = await helper.stop(), removed = m.removeSentinel(sentinel); await fs.rm(root, { recursive: true, force: true });
    assert.equal(stopped, true, 'the helper process is gone'); assert.equal(removed, true, 'the sentinel folder is gone');
  };
  return fx;
}
const unprivileged = process.getuid?.() === 0 ? 'permission bits do not stop root' : false;
const { existsSync } = await import('node:fs');
function fsExists(file) { try { return Boolean(existsSync(file)); } catch { return false; } }

test('the sandbox probe reports writes outside the run folder as failures when no sandbox confines it', async () => {
  const fx = await probeFixture(), { PROBE_MESSAGE, CHECKS, checksFor, confinementFor, evaluate, deniedProbes } = fx.m, CONFINEMENT = confinementFor('folder');
  try {
    // Run deliberately without ALA: every write and read succeeds, so the probe must report every confinement check as failed.
    const { stdout, tsv, checks, result } = await fx.probe();
    assert.equal(JSON.parse(stdout.trim()).item.text, PROBE_MESSAGE, 'ALA needs one Codex agent_message on stdout');
    assert.deepEqual(checks.map(c => c.key), checksFor('folder').map(([key]) => key));
    assert.equal(result.agent_state_shared, undefined, 'folder mode has no agent_state_shared row'); assert.doesNotMatch(tsv, /agent_state_shared/, 'the folder-mode probe does not even write one');
    for (const key of ['cwd', 'run_write', 'repo_read']) assert.equal(result[key].status, 'pass', key);
    assert.deepEqual(CONFINEMENT.filter(key => CHECKS.find(([k]) => k === key)[2] !== true), [], 'every confinement check is required');
    for (const key of CONFINEMENT) { assert.equal(result[key].status, 'fail', key); assert.equal(result[key].ok, false, key); }
    for (const file of Object.values(deniedProbes({ root: fx.root, check: fx.check, id: fx.id }))) assert.ok(await fs.stat(file), 'an unconfined probe really wrote ' + file);
    assert.match(result.outside_read_denied.detail, /Read the sentinel/); assert.match(result.home_private.detail, /HOME is the real home/);
    assert.match(result.other_process_environment.detail, /^visible: .*other=found own=found/, 'the unconfined reader sees the helper');
    for (const key of ['helper_import', 'lean_version', 'lean_check']) assert.equal(result[key].status, 'fail', key + ' has no helper or toolchain in the stub repository');
    assert.match(result.python.status, /^(pass|fail)$/); assert.equal(result.extractors.status, 'info'); assert.equal(result.extractors.ok, true);
    assert.deepEqual(evaluate('').filter(c => c.required).map(c => [c.status, c.ok]), checksFor('folder').filter(([, , required]) => required).map(() => ['missing', false]), 'a missing probe never passes');
    assert.throws(() => fx.m.probeScript({ run: "/a'b", root: fx.root, check: fx.check, id: fx.id, sentinel: fx.sentinel, home: fx.home, helper: fx.helper, reader: fx.reader }), /does not support/);
    // A private HOME alone is not enough: the entry of the real home is still readable.
    const privateHome = path.join(fx.root, 'private-home'); await fs.mkdir(privateHome);
    const privateRun = await fx.probe({}, { HOME: privateHome });
    assert.match(privateRun.result.home_private.detail, /readable inside: .*\.zshrc/);
    // real_agent_state_denied, unconfined: with HOME the real home, $HOME/.codex is the real folder (identity); with a
    // private HOME the real .codex is still listable. The marker goes only into a HOME that is not the real home.
    assert.equal(result.real_agent_state_denied.status, 'fail'); assert.ok(result.real_agent_state_denied.detail.includes(`$HOME/.codex is your real folder ${fx.fakeHome}/.codex`), result.real_agent_state_denied.detail);
    assert.equal(privateRun.result.real_agent_state_denied.status, 'fail'); assert.match(privateRun.result.real_agent_state_denied.detail, /\.codex is listable inside/); assert.doesNotMatch(privateRun.result.real_agent_state_denied.detail, /is your real folder/);
    assert.equal(fsExists(path.join(fx.fakeHome, fx.marker.name)), false, 'no marker is written into the real home');
    assert.equal(await fs.readFile(path.join(privateHome, fx.marker.name), 'utf8'), fx.marker.value + '\n', 'the marker reaches a private HOME');
    // The TMPDIR label comes from content: a folder that lists the sentinel's folder is the host's, a private one is not.
    assert.match((await fx.probe({}, { TMPDIR: fx.sentinel.base })).result.home.detail, /TMPDIR=\S+ \(the host temporary folder: it lists the sentinel folder\)/);
    assert.match((await fx.probe({}, { TMPDIR: privateHome })).result.home.detail, /TMPDIR=\S+ \(private, not the host temporary folder\)/);
  } finally { await fx.close(); }
});

test('outside_read_denied and home_private pass when the sentinel, the home entry and the home listing are refused', { skip: unprivileged }, async () => {
  const fx = await probeFixture();
  try {
    // Permission bits stand in for the sandbox here; the control read in the run folder keeps working.
    await fs.chmod(fx.sentinel.dir, 0o000); await fs.chmod(path.join(fx.fakeHome, '.zshrc'), 0o000); await fs.chmod(fx.fakeHome, 0o300);
    const privateHome = path.join(fx.root, 'private-home'); await fs.mkdir(privateHome);
    const { result } = await fx.probe({}, { HOME: privateHome });
    assert.equal(result.outside_read_denied.status, 'pass', result.outside_read_denied.detail); assert.equal(result.home_private.status, 'pass', result.home_private.detail);
    assert.match(result.home_private.detail, /\.zshrc \(file\) is not readable; listing of .* refused, without it/);
    assert.match(result.home.detail, /HOME=.*private-home \(private, not the real home\) .*TMPDIR=/);
    assert.equal((await fx.probe({}, { HOME: fx.fakeHome })).result.home_private.status, 'fail', 'HOME equal to the real home fails');
    assert.equal((await fx.probe({}, { HOME: '' })).result.home_private.status, 'fail', 'an unset HOME fails');
    assert.equal((await fx.probe({ home: { ...fx.home, entry: null, kind: null } }, { HOME: privateHome })).result.home_private.status, 'fail', 'no host-picked entry fails');
  } finally { await fx.close(); }
});

test('real_agent_state_denied passes when the real folder is refused (permission bits stand in for the sandbox)', { skip: unprivileged }, async () => {
  const fx = await probeFixture();
  try {
    const privateHome = path.join(fx.root, 'private-home'); await fs.mkdir(privateHome);
    await fs.chmod(path.join(fx.fakeHome, '.codex'), 0o000);
    const { result } = await fx.probe({}, { HOME: privateHome });
    assert.equal(result.real_agent_state_denied.status, 'pass', result.real_agent_state_denied.detail);
    assert.equal(result.real_agent_state_denied.detail, `HOME=${privateHome} is none of them, and none is listable or has a readable login file inside: ${fx.fakeHome}/.codex`);
    // A HOME whose .codex is a link to the real folder is the real folder: ALA's default without --home on macOS.
    await fs.symlink(path.join(fx.fakeHome, '.codex'), path.join(privateHome, '.codex'));
    const linked = (await fx.probe({}, { HOME: privateHome })).result.real_agent_state_denied;
    assert.equal(linked.status, 'fail'); assert.match(linked.detail, /\$HOME\/\.codex is your real folder/);
    // No state folder in the real home: the probe says so and the host decides with the positive control.
    const none = (await fx.probe({ state: { home: privateHome + '-none', folders: [] } }, { HOME: privateHome })).result.real_agent_state_denied;
    assert.deepEqual([none.status, none.ok, none.detail], ['na', false, 'not applicable: no coding-agent state folder exists in the real home']);
  } finally { await fs.chmod(path.join(fx.fakeHome, '.codex'), 0o700).catch(() => {}); await fx.close(); }
});

test('own mode: agent_state_shared passes only when $HOME/.codex is the fixture folder, and a marker is written only into that folder', async () => {
  const fx = await probeFixture(), { evaluate, checksFor } = fx.m, fixture = path.join(fx.fakeHome, '.codex'), markerFile = path.join(fixture, fx.marker.name);
  try {
    // HOME is the fixture home that stands in for yours: the shared state folder, as ALA's default without --home.
    const own = await fx.probe({ mode: 'own' });
    assert.deepEqual(own.checks.map(c => c.key), checksFor('own').map(([key]) => key));
    assert.equal(own.result.real_agent_state_denied, undefined, 'own mode has no real_agent_state_denied row'); assert.doesNotMatch(own.tsv, /real_agent_state_denied/);
    assert.equal(own.result.agent_state_shared.status, 'pass', own.result.agent_state_shared.detail);
    assert.equal(own.result.agent_state_shared.detail, `shared: $HOME/.codex (HOME=${fx.fakeHome}) is the folder ${fx.fakeHome}/.codex, and a marker written into it`);
    assert.equal(await fs.readFile(markerFile, 'utf8'), fx.marker.value + '\n', 'the marker is in the fixture .codex');
    assert.equal(fsExists(path.join(fx.fakeHome, fx.marker.name)), false, 'own mode never writes the marker into $HOME itself');
    await fs.rm(markerFile);
    // A private HOME without .codex, with another .codex folder, and without any state folder named by the host: not shared, nothing written.
    const privateHome = path.join(fx.root, 'private-home'); await fs.mkdir(privateHome);
    const missing = await fx.probe({ mode: 'own' }, { HOME: privateHome });
    assert.equal(missing.result.agent_state_shared.status, 'fail'); assert.equal(missing.result.agent_state_shared.detail, `not shared: $HOME/.codex does not exist inside (HOME=${privateHome})`);
    await fs.mkdir(path.join(privateHome, '.codex'));
    const other = await fx.probe({ mode: 'own' }, { HOME: privateHome });
    assert.equal(other.result.agent_state_shared.status, 'fail'); assert.equal(other.result.agent_state_shared.detail, `not shared: $HOME/.codex (HOME=${privateHome}) is not the folder ${fx.fakeHome}/.codex`);
    assert.deepEqual([await fs.readdir(path.join(privateHome, '.codex')), await fs.readdir(fixture)], [[], ['auth.json']], 'a folder with another identity gets no marker, and the fixture none either');
    const none = await fx.probe({ mode: 'own', state: { home: fx.fakeHome, folders: [] } });
    assert.equal(none.result.agent_state_shared.status, 'fail'); assert.match(none.result.agent_state_shared.detail, /^not shared: the host found no \.codex in the fixture home$/);
    // A .codex that is a link to the fixture folder is that folder (stat follows it): ALA's default without --home on macOS.
    await fs.rm(path.join(privateHome, '.codex'), { recursive: true }); await fs.symlink(fixture, path.join(privateHome, '.codex'));
    const linked = await fx.probe({ mode: 'own' }, { HOME: privateHome });
    assert.equal(linked.result.agent_state_shared.status, 'pass', linked.result.agent_state_shared.detail); assert.equal(await fs.readFile(markerFile, 'utf8'), fx.marker.value + '\n');
    // The same TSV evaluated for the other mode never shows the row, and a row the probe did not write is missing, not passing.
    assert.equal(evaluate('agent_state_shared\tpass\tshared: x', { mode: 'folder' }).find(c => c.key === 'agent_state_shared'), undefined);
    assert.deepEqual(evaluate('', { mode: 'own' }).filter(c => c.key === 'agent_state_shared').map(c => [c.required, c.status, c.ok]), [[true, 'missing', false]]);
    assert.throws(() => evaluate('', { mode: 'both' }), /Unknown mode/); assert.throws(() => fx.m.probeScript({ run: fx.run, root: fx.root, check: fx.check, id: fx.id, sentinel: fx.sentinel, home: fx.home, helper: fx.helper, reader: fx.reader, mode: 'both' }), /Unknown mode/);
  } finally { await fx.close(); }
});

test('the modes: checksFor and confinementFor list the rows of one mode, CHECKS is the union, and modeStatement names both modes', async () => {
  const { CHECKS, checksFor, confinementFor, modeStatement, combineShared } = await import('./check-ala-sandbox.mjs'), keys = mode => checksFor(mode).map(([key]) => key);
  assert.equal(keys('own').includes('real_agent_state_denied'), false); assert.equal(keys('own').includes('agent_state_shared'), true);
  assert.equal(keys('folder').includes('real_agent_state_denied'), true); assert.equal(keys('folder').includes('agent_state_shared'), false);
  assert.deepEqual([...new Set([...keys('own'), ...keys('folder')])].sort(), CHECKS.map(([key]) => key).sort(), 'CHECKS is the union');
  assert.deepEqual(confinementFor('own'), ['repo_write_denied', 'tmp_write_denied', 'sibling_write_denied', 'outside_read_denied', 'home_private', 'other_process_environment']);
  assert.deepEqual(confinementFor('folder'), [...confinementFor('own'), 'real_agent_state_denied']);
  assert.equal(CHECKS.find(([key]) => key === 'home_private')[1], 'HOME is not the real home, and an entry of the real home is neither readable nor listed');
  for (const [key, , required] of checksFor('own')) if (['agent_state_shared', 'home_private'].includes(key)) assert.equal(required, true, key);
  assert.throws(() => checksFor('both'), /Unknown mode: both/); assert.throws(() => modeStatement('both', 'own'), /Unknown mode/);
  assert.match(modeStatement('own', 'folder'), /^Configured mode: folder\. Mode checked: own home\.\n\nALA was started without `--home`; a fixture home stood in for yours\. .*can read and change the agent's state folder, including its login, and no row says otherwise\.$/);
  assert.match(modeStatement('folder', 'folder'), /^Configured mode: folder\. Mode checked: folder\.\n\nALA was started with `--home`; an empty temporary folder stood in for the set folder\. All runs share that folder, including the login kept there, and no row says otherwise\.$/);
  assert.match(modeStatement('own', null), /^Configured mode: unknown \(the setting could not be read\)\. Mode checked: own home\./);
  // The host completes the probe's row: only a pass with the marker in the fixture folder is shared.
  const row = (status, detail) => ({ key: 'agent_state_shared', status, detail, required: true, ok: status === 'pass' });
  const passed = combineShared(row('pass', 'shared: x'), { marker: true }); assert.deepEqual([passed.status, passed.ok, passed.detail], ['pass', true, 'shared: x; its marker reached the fixture folder']);
  const unseen = combineShared(row('pass', 'shared: x'), { marker: false }); assert.deepEqual([unseen.status, unseen.ok], ['fail', false]); assert.match(unseen.detail, /^not shared: the probe's marker is missing from the fixture's \.codex folder$/);
  const refused = combineShared(row('fail', 'not shared: y'), { marker: true }); assert.deepEqual([refused.status, refused.detail], ['fail', 'not shared: y']);
  const missing = combineShared(row('missing', 'The probe did not record this check.'), { marker: true }); assert.deepEqual([missing.status, missing.ok, missing.detail], ['fail', false, 'not shared: The probe did not record this check.']);
});

test('the host merges real_agent_state_denied with its positive control', async () => {
  const { combineState } = await import('./check-ala-sandbox.mjs');
  const state = (status, detail = 'probe detail') => ({ key: 'real_agent_state_denied', status, detail, required: true, ok: status === 'pass' });
  const control = { status: 'pass', detail: 'HOME=/c is none of them ... /fixture/.codex', marker: true };
  assert.equal(combineState(state('pass'), { marker: true, control }).ok, true);
  assert.equal(combineState(state('na'), { marker: true, control }).status, 'pass', 'no real state folder: the control decides');
  for (const [options, pattern] of [[{ marker: true }, /positive control did not run/], [{ marker: true, control: { ...control, status: 'fail', detail: 'listable' } }, /positive control failed: listable/], [{ marker: true, control: { ...control, marker: false } }, /its marker is missing/], [{ marker: false, control }, /marker is missing from the --home folder/], [{ marker: true, control, hostProblem: 'The host cannot list /h/.codex' }, /host cannot list/]]) {
    for (const status of ['pass', 'na']) { const merged = combineState(state(status), options); assert.equal(merged.ok, false, pattern + status); assert.equal(merged.status, 'fail'); assert.match(merged.detail, pattern); }
  }
  assert.equal(combineState(state('fail', 'x is listable inside'), { marker: true, control }).ok, false);
});

test('other_process_environment is hidden only for an outside process, and a reader that cannot answer is a failure', async () => {
  const fx = await probeFixture();
  try {
    // A process that does not exist (here the stopped helper) is refused or absent for the reader, whose own environment still reads.
    const stopped = { ...fx.helper }; assert.equal(await fx.helper.stop(), true);
    const hidden = (await fx.probe({ helper: stopped })).result.other_process_environment;
    assert.equal(hidden.status, 'pass', hidden.detail); assert.match(hidden.detail, /^hidden: .*other=denied.* own=found/);
    // A marker the helper does not hold: the reader gets data without it, which is not "hidden".
    const wrong = await fx.m.startHelper(); wrong.value = 'not-the-value';
    try { const answer = (await fx.probe({ helper: wrong })).result.other_process_environment; assert.equal(answer.status, 'fail'); assert.doesNotMatch(answer.detail, /^hidden/); } finally { await wrong.stop(); }
    if (process.platform === 'darwin') {
      const broken = (await fx.probe({ reader: path.join(fx.root, 'missing-reader.pl') })).result.other_process_environment;
      assert.equal(broken.status, 'fail', 'a reader that cannot run is never "hidden"'); assert.match(broken.detail, /did not give a usable answer/);
    }
  } finally { await fx.close(); }
});

test('the host confirms the helper marker with the same reader, and fails the check when it cannot', async () => {
  const fx = await probeFixture();
  try {
    const { confirmOnHost, failCheck, evaluate, startHelper } = fx.m;
    assert.equal((await confirmOnHost({ helper: fx.helper, reader: fx.reader })).ok, true, 'a Node helper shows its marker to the host reader');
    const unreadable = await confirmOnHost({ helper: { ...fx.helper, value: 'not-the-value', needle: fx.helper.name + '=not-the-value' }, reader: fx.reader, attempts: 2, delay: 10 });
    assert.equal(unreadable.ok, false); assert.match(unreadable.detail, /marker|environ/);
    // The probe's own "hidden" answer is overridden by a failed host confirmation.
    const [row] = evaluate('other_process_environment\tpass\thidden: made up').filter(c => c.key === 'other_process_environment');
    assert.equal(row.ok, true); failCheck(row, unreadable.detail); assert.equal(row.status, 'fail'); assert.equal(row.ok, false);
    if (process.platform === 'darwin') {
      // Negative control: an Apple binary's environment is not readable even by an unconfined process, so "hidden" inside would be meaningless.
      const apple = await startHelper({ command: '/bin/sleep', args: ['600'] });
      try { const answer = await confirmOnHost({ helper: apple, reader: fx.reader, attempts: 3, delay: 10 }); assert.equal(answer.ok, false); assert.match(answer.detail, /cannot see the marker/); } finally { assert.equal(await apple.stop(), true); }
    }
  } finally { await fx.close(); }
});

test('the home entry is picked without reading it, the sentinel stays outside the repository and the helper and sentinel are removed', async () => {
  const { pickHomeEntry, makeSentinel, removeSentinel, startHelper } = await import('./check-ala-sandbox.mjs');
  const base = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'ala-home-')));
  try {
    const home = path.join(base, 'home');
    for (const dir of ['work/repo', '.claude', 'docs', "quote'dir"]) await fs.mkdir(path.join(home, dir), { recursive: true });
    for (const file of ['OPENAI_API_KEY', 'notes.txt', ".zshrc"]) await fs.writeFile(path.join(home, file), 'secret-looking content\n');
    await fs.symlink(path.join(home, 'docs'), path.join(home, 'linked'));
    const listing = async () => (await fs.readdir(home, { recursive: true })).sort();
    const before = await listing();
    assert.deepEqual(await pickHomeEntry({ home, avoid: [path.join(home, 'work/repo')] }), { real: home, entry: '.zshrc', kind: 'file' }, 'a plain benign file wins');
    await fs.rm(path.join(home, '.zshrc'));
    assert.deepEqual(await pickHomeEntry({ home, avoid: [path.join(home, 'work/repo')] }), { real: home, entry: 'docs', kind: 'dir' }, 'work leads to a mount, .claude is state, the key and the symlink are skipped');
    assert.deepEqual(await pickHomeEntry({ home, avoid: [path.join(home, 'work/repo'), path.join(home, 'docs/x')] }), { real: home, entry: 'notes.txt', kind: 'file' }, 'an entry that leads to a mount is skipped');
    assert.equal((await pickHomeEntry({ home: path.join(base, 'empty-home-missing').replace('missing', 'x'), avoid: [] }).catch(() => ({ entry: 'error' }))).entry, 'error', 'a missing home is an error, not a pick');
    assert.deepEqual(await listing(), [...before.filter(name => name !== '.zshrc')].sort(), 'picking creates and changes nothing');
    // The sentinel: outside the repository, readable by the host, removed only by its own guarded path.
    const sentinel = await makeSentinel(path.join(base, 'repo'));
    assert.equal(await fs.readFile(sentinel.file, 'utf8'), sentinel.marker); assert.ok(!sentinel.dir.startsWith(path.join(base, 'repo')));
    assert.equal(removeSentinel(sentinel), true); assert.equal(await fs.stat(sentinel.dir).catch(() => null), null);
    const before2 = (await fs.readdir(sentinel.base)).filter(name => name.startsWith('ala-sandbox-sentinel-')).length;
    await assert.rejects(makeSentinel(sentinel.base), /inside the repository/);
    assert.equal((await fs.readdir(sentinel.base)).filter(name => name.startsWith('ala-sandbox-sentinel-')).length, before2, 'a rejected sentinel leaves no folder');
    const other = path.join(base, 'not-a-sentinel'); await fs.mkdir(other);
    assert.equal(removeSentinel({ base, dir: other }), false, 'a folder without the sentinel prefix is never removed'); assert.ok(await fs.stat(other));
    // The helper is gone after stop(), also when its command ignores standard input.
    const helper = await startHelper({ command: '/bin/sleep', args: ['600'] });
    assert.equal(await helper.stop(), true);
  } finally { await fs.rm(base, { recursive: true, force: true }); }
});

test('a target that cannot be read but still shows in a listing fails, for the sentinel and for the home entry', { skip: unprivileged }, async () => {
  const fx = await probeFixture();
  try {
    const privateHome = path.join(fx.root, 'private-home'); await fs.mkdir(privateHome);
    await fs.chmod(fx.sentinel.file, 0o000); await fs.chmod(path.join(fx.fakeHome, '.zshrc'), 0o000);
    const { result } = await fx.probe({}, { HOME: privateHome });
    assert.equal(result.outside_read_denied.status, 'fail'); assert.match(result.outside_read_denied.detail, /Listed the sentinel folder/);
    assert.equal(result.home_private.status, 'fail'); assert.match(result.home_private.detail, /listing of the real home shows \.zshrc/);
  } finally { await fs.chmod(fx.sentinel.file, 0o600).catch(() => {}); await fx.close(); }
});

test('the control covers cat: when cat cannot run, the sentinel row fails instead of passing on the listing alone', async () => {
  const fx = await probeFixture();
  try {
    // A PATH with the tools the probe needs except cat.
    const tools = path.join(fx.root, 'tools-without-cat'); await fs.mkdir(tools);
    for (const name of ['ls', 'head', 'tr', 'cut', 'wc', 'grep', 'dirname']) for (const folder of ['/bin', '/usr/bin']) if (await fs.access(path.join(folder, name)).then(() => true, () => false)) { await fs.symlink(path.join(folder, name), path.join(tools, name)); break; }
    const { result } = await fx.probe({}, { PATH: tools });
    assert.equal(result.outside_read_denied.status, 'fail'); assert.match(result.outside_read_denied.detail, /Control read or listing/);
    assert.match(result.home_private.detail, /Control read or listing/);
  } finally { await fx.close(); }
});

test('the macOS reader takes the value from its own environment, so arguments alone never prove it read the environment', { skip: process.platform === 'darwin' ? false : 'the reader is the macOS perl script' }, async () => {
  const fx = await probeFixture(), { execFile } = await import('node:child_process');
  const read = (args, env) => new Promise(resolve => execFile('/usr/bin/perl', [fx.reader, ...args], { env }, (error, out) => resolve(out.trim())));
  try {
    const { name, value, pid } = fx.helper;
    assert.equal(await read([String(pid), name], { [name]: value }), 'other=found own=found');
    assert.equal(await read([String(pid), name], {}), 'other=found own=absent', 'the name is in the arguments, but the environment lacks the variable: the reader fails its own-process control');
    assert.equal(await read([String(pid), name], { [name]: 'another' }), 'other=absent own=found', 'the environment holds a different value');
  } finally { await fx.close(); }
});

test('on Linux the reader fails closed: with /proc absent, neither the helper nor the reader itself can be read', { skip: fsExists('/proc/self/environ') ? 'this host has /proc, so the forced Linux reader would work' : false }, async () => {
  const fx = await probeFixture();
  try {
    const answer = (await fx.probe({ platform: 'linux' })).result.other_process_environment;
    assert.equal(answer.status, 'fail'); assert.match(answer.detail, /did not give a usable answer \(exit n\/a, grep reader\): other=denied own=denied/); assert.doesNotMatch(answer.detail, /^hidden/);
  } finally { await fx.close(); }
});

test('Python and Lean resolution failures and the launchd line are reported by the host, not by the probe', async () => {
  const { evaluate, launchdResidual } = await import('./check-ala-sandbox.mjs');
  const message = 'Python 3.12 or later is required (tools/document_audit.py). Found: /usr/bin/python3 3.9.6. Run "node tools/setup-toolchain.mjs" to install one.';
  const rows = Object.fromEntries(evaluate('python\tpass\tprobe says yes\nhelper_import\tpass\tprobe says yes\nlean_version\tpass\tx\nlean_check\tpass\tx\n', { pythonError: message, leanError: 'Unsupported platform for the Lean toolchain: sunos-x64' }).map(c => [c.key, c]));
  for (const key of ['python', 'helper_import']) { assert.equal(rows[key].status, 'fail'); assert.equal(rows[key].ok, false); assert.equal(rows[key].detail, message); }
  for (const key of ['lean_version', 'lean_check']) { assert.equal(rows[key].status, 'fail'); assert.match(rows[key].detail, /sunos-x64/); }
  assert.equal(evaluate('python\tpass\t/usr/bin/python3 3.12.1', { pythonWarning: 'lies under your home folder' }).find(c => c.key === 'python').detail, '/usr/bin/python3 3.12.1 Warning: lies under your home folder');
  assert.equal(launchdResidual('darwin'), 'Known residual on macOS: a confined process can start or unload existing launchd jobs in your login session; see ALA DS006.');
  assert.equal(launchdResidual('linux'), 'not applicable');
  for (const [platform, text] of [['darwin', launchdResidual('darwin')], ['linux', 'not applicable']]) {
    const row = evaluate('launchd_residual\tfail\tthe probe cannot decide this', { platform }).find(c => c.key === 'launchd_residual');
    assert.deepEqual([row.status, row.required, row.ok, row.detail], ['info', false, true, text]);
  }
});

// ALA's discovery rule, as the wip checkout implements it: an explicit CODEX_BIN that is executable, else ~/.local/bin/codex, else `codex` on PATH.
const DISCOVERY = `import { access, realpath } from 'node:fs/promises';
import { constants } from 'node:fs';
import path from 'node:path';
const executable = async file => { try { await access(file, constants.X_OK); return true; } catch { return false; } };
async function resolveCandidate(candidate, env) {
  if (path.isAbsolute(candidate) || candidate.includes('/')) return await executable(path.resolve(candidate)) ? realpath(path.resolve(candidate)) : null;
  for (const directory of String(env.PATH || '').split(path.delimiter).filter(Boolean)) if (await executable(path.resolve(directory, candidate))) return realpath(path.resolve(directory, candidate));
  return null;
}
export async function discoverCodingAgents({ env = process.env } = {}) {
  let binary = null;
  for (const candidate of [env.CODEX_BIN, path.join(env.HOME || '', '.local/bin/codex'), 'codex'].filter(Boolean)) { binary = await resolveCandidate(candidate, env); if (binary) break; }
  return [{ name: 'codex', available: Boolean(binary), binary }];
}
`;

test('the model-free guard refuses a real agent binary, a populated agent home, model credentials and a probe ALA would not run', async () => {
  const { assertModelFree, probeScript, alaDiscovery } = await import('./check-ala-sandbox.mjs');
  const dir = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'ala-guard-')));
  try {
    const ala = await createFakeALA(path.join(dir, 'ala'), ''); await fs.writeFile(path.join(ala, 'src/coding-agents/discovery.mjs'), DISCOVERY);
    const discover = alaDiscovery(path.join(ala, 'bin/ala.mjs'));
    const codex = path.join(dir, 'codex'), home = path.join(dir, 'agent-home'), real = path.join(dir, 'real-home/.local/bin/codex');
    await fs.mkdir(home); await fs.mkdir(path.dirname(real), { recursive: true }); await fs.writeFile(real, '#!/bin/sh\necho real\n', { mode: 0o755 });
    const { homeEnvironment } = await import(path.join(ala, 'src/coding-agents/logins.mjs')), codexHome = home;
    const sentinel = { file: '/s/f', dir: '/s', marker: 'm', base: '/b' }, homeEntry = { real: '/h', entry: 'e', kind: 'file' }, helper = { name: 'N', value: 'v', pid: 1 };
    await fs.writeFile(codex, probeScript({ run: '/r', root: '/p', check: '/c', id: 'i', sentinel, home: homeEntry, helper, reader: '/x' }), { mode: 0o755 }); await fs.chmod(codex, 0o755);
    // The caller's HOME holds an installed "real" Codex that ALA would fall back to without CODEX_BIN.
    const env = { CODEX_BIN: codex, PATH: '/bin', HOME: path.join(dir, 'real-home') }, base = { codex, home, homeEnvironment, discover };
    await assertModelFree({ env, ...base });
    await assert.rejects(assertModelFree({ env: { ...env, OPENAI_API_KEY: 'x' }, ...base }), /OPENAI_API_KEY/);
    await assert.rejects(assertModelFree({ env: { ...env, ANTHROPIC_API_KEY: 'x' }, ...base }), /ANTHROPIC_API_KEY/);
    await assert.rejects(assertModelFree({ env: { ...env, CLAUDE_CODE_OAUTH_TOKEN: 'x' }, ...base }), /CLAUDE_CODE_OAUTH_TOKEN/);
    await assert.rejects(assertModelFree({ env: { ...env, CODEX_BIN: real }, ...base }), /CODEX_BIN is not the probe script/);
    await assert.rejects(assertModelFree({ env: { ...env, CODEX_BIN: real }, ...base, codex: real }), /not the simulated Codex/);
    await assert.rejects(assertModelFree({ env, ...base, discover: undefined }), /nothing confirms which Codex ALA will run/);
    // Discovery runs with the environment ALA derives from --home, not with the caller's HOME.
    const seen = []; await assertModelFree({ env, ...base, discover: async e => { seen.push(e.HOME, e.CODEX_HOME); return discover(e); } });
    assert.deepEqual(seen, [home, path.join(home, '.codex')]);
    // Mode 0644: ALA's discovery skips the probe and picks the real Codex, which the value of CODEX_BIN does not show.
    await fs.chmod(codex, 0o644);
    assert.equal((await discover(env))[0].binary, real, 'discovery really falls back to the installed Codex');
    await assert.rejects(assertModelFree({ env, ...base }), /not a regular file that its owner can execute/);
    await fs.chmod(codex, 0o755);
    // Executable, yet discovery would not select it (here a stand-in that answers with the installed Codex).
    await assert.rejects(assertModelFree({ env, ...base, discover: async () => [{ name: 'codex', available: true, binary: real }] }), /ALA would run .*real-home.*instead of the probe script/);
    await assert.rejects(assertModelFree({ env, ...base, discover: async () => [{ name: 'codex', available: false, binary: null }] }), /ALA would run no Codex/);
    await assert.rejects(assertModelFree({ env, ...base, discover: async () => { throw Error('broken'); } }), /ALA discovery failed: broken/);
    // A symlink is not the regular file this check wrote.
    await fs.symlink(codex, path.join(dir, 'link')); await assert.rejects(assertModelFree({ env: { ...env, CODEX_BIN: path.join(dir, 'link') }, ...base, codex: path.join(dir, 'link') }), /regular file/);
    // The --home folder must be an existing, empty folder.
    await assert.rejects(assertModelFree({ env, ...base, home: path.join(dir, 'missing-home') }), /the agent home is not an existing folder/);
    await assert.rejects(assertModelFree({ env, ...base, home: undefined }), /the agent home is not an existing folder/);
    await fs.mkdir(path.join(codexHome, '.codex')); await fs.writeFile(path.join(codexHome, '.codex/auth.json'), '{}');
    await assert.rejects(assertModelFree({ env, ...base }), /the agent home is not an empty folder/);
    // The own-mode fixture home must hold exactly the fixture, with its content.
    await assertModelFree({ env: homeEnvironment(env, home), ...base, homeEnvironment: null, homeFiles: { '.codex/auth.json': '{}' } });
    await fs.writeFile(path.join(codexHome, '.codex/auth.json'), '{"tokens":1}');
    await assert.rejects(assertModelFree({ env: homeEnvironment(env, home), ...base, homeEnvironment: null, homeFiles: { '.codex/auth.json': '{}' } }), /does not hold the fixture content/);
    await fs.writeFile(path.join(codexHome, 'extra'), '');
    await assert.rejects(assertModelFree({ env: homeEnvironment(env, home), ...base, homeEnvironment: null, homeFiles: { '.codex/auth.json': '{}' } }), /holds something other than/);
  } finally { await fs.rm(dir, { recursive: true, force: true }); }
});

// A temporary repository, a fake real home, a fake ALA that runs CODEX_BIN directly (so nothing confines the probe) and
// ALA's discovery rule. `body` runs in the fake ALA before the probe; nothing here starts a real agent or touches the real home.
// Every main() gets this fixture `homeDir`, so the setting and the logins it reads are the fixture's (addendum A4). run()
// checks the folder mode unless a test says `mode` (undefined: read the setting at homeDir); `alaCli: null` leaves ALA_CLI
// unset so that automatic detection runs; `environment` sets more variables for the run.
// Like ALA, the fake gives the probe the environment it derives from --home (agentEnv), so the probe never sees the
// caller's HOME; a body may replace it through globalThis.probeEnv.
async function mainFixture({ body = '', helperOptions = {}, discovery = DISCOVERY } = {}) {
  const { main } = await import('./check-ala-sandbox.mjs');
  const dir = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'ala-main-'))), root = path.join(dir, 'repo'), homeDir = path.join(dir, 'home'), ala = path.join(dir, 'AdvancedLanguageAgent');
  await fs.mkdir(path.join(root, 'skills/evidence-to-proof-review'), { recursive: true }); await fs.writeFile(path.join(root, 'skills/evidence-to-proof-review/SKILL.md'), '# Stub skill\n');
  await fs.mkdir(homeDir); await fs.writeFile(path.join(homeDir, '.zshrc'), '# stub\n');
  await fs.mkdir(path.join(homeDir, '.codex'), { mode: 0o700 }); await fs.writeFile(path.join(homeDir, '.codex/auth.json'), '{}');
  await createFakeALA(ala, `import { spawnSync } from 'node:child_process';
const script = fs.readFileSync(process.env.CODEX_BIN, 'utf8'), grab = name => new RegExp(name + "='([^']*)'").exec(script)[1];
${body}
const result = spawnSync(process.env.CODEX_BIN, ['exec', '--json', prompt], { cwd, env: globalThis.probeEnv ?? agentEnv, encoding: 'utf8' });
fs.writeFileSync(output, JSON.parse(result.stdout.trim().split('\\n').pop()).item.text);
`);
  await fs.writeFile(path.join(ala, 'src/coding-agents/discovery.mjs'), discovery);
  const fx = { dir, root, homeDir, ala };
  fx.run = async (options = {}) => {
    const { alaCli = ala, environment = {}, ...rest } = options, names = ['ALA_CLI', ...Object.keys(environment)], saved = Object.fromEntries(names.map(name => [name, process.env[name]])), lines = [], errors = [], savedError = console.error;
    const set = (name, value) => { if (value == null) delete process.env[name]; else process.env[name] = value; };
    set('ALA_CLI', alaCli); for (const [name, value] of Object.entries(environment)) set(name, value); console.error = (...args) => errors.push(args.join(' '));
    try { fx.code = await main({ root, homeDir, timeout: 60000, helperOptions, log: line => lines.push(line), mode: 'folder', ...rest }); } finally { console.error = savedError; for (const name of names) set(name, saved[name]); }
    fx.errors = errors.join('\n'); fx.output = lines.join('\n'); const reported = /^Report: (.*)$/m.exec(fx.output); fx.report = reported ? await fs.readFile(reported[1], 'utf8') : '';
    fx.rows = Object.fromEntries([...fx.report.matchAll(/^\| (.*?) \| (Yes|No) \| (\w+) \| (.*) \|$/gm)].map(([, label, , status, detail]) => [label, { status, detail }]));
    fx.row = key => fx.rows[fx.labels.get(key)]; return fx;
  };
  fx.labels = new Map((await import('./check-ala-sandbox.mjs')).CHECKS.map(([key, label]) => [key, label]));
  fx.close = async () => { await fs.chmod(homeDir, 0o700).catch(() => {}); await fs.rm(dir, { recursive: true, force: true }); };
  return fx;
}
const probeRow = async (fx, key) => { const [check] = await fs.readdir(path.join(fx.root, 'tmp/ala-sandbox-check')); return (await fs.readFile(path.join(fx.root, 'tmp/ala-sandbox-check', check, 'run/probe-results.tsv'), 'utf8')).split('\n').find(line => line.startsWith(key + '\t')); };
const fixturesGone = async fx => {
  const base = await fs.realpath(os.tmpdir()), pid = Number(/helper process (\d+)/.exec(fx.report)[1]);
  assert.match(fx.report, /\(removed: yes\), a helper process \d+ with the marker variable `\w+` \(stopped: yes\), and the temporary base `[^`]+` with the fixture homes \(removed: yes\)/);
  assert.deepEqual((await fs.readdir(base)).filter(name => name.startsWith('ala-sandbox-sentinel-') || name.startsWith('ala-sandbox-base-')), [], 'no sentinel folder and no temporary base is left');
  assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' }, 'the helper process is gone');
};

test("main({mode: 'folder'}) end to end with nothing confining the probe: every confinement row fails, the exit code is 1 and nothing is left behind", async () => {
  const fx = await mainFixture(), { confinementFor, modeStatement } = await import('./check-ala-sandbox.mjs'), CONFINEMENT = confinementFor('folder');
  try {
    const before = (await fs.readdir(fx.homeDir)).sort(); await fx.run({ mode: 'folder' });
    assert.ok(fx.row('real_agent_state_denied'), 'folder mode has the real_agent_state_denied row'); assert.equal(fx.row('agent_state_shared'), undefined, 'and no agent_state_shared row');
    assert.match(fx.report, /^# ALA sandbox check\n\nConfigured mode: own home\. Mode checked: folder\.\n/); assert.ok(fx.report.includes(modeStatement('folder', 'own')));
    assert.equal(fx.row('home_private').status, 'fail'); assert.equal(fx.labels.get('home_private'), 'HOME is not the real home, and an entry of the real home is neither readable nor listed');
    assert.equal(fx.code, 1); assert.match(fx.report, /\*\*Result: failed\.\*\* ALA exited with code 0 and returned the simulated final message\./);
    for (const key of CONFINEMENT) assert.equal(fx.row(key).status, 'fail', key);
    for (const key of ['cwd', 'run_write', 'repo_read']) assert.equal(fx.row(key).status, 'pass', key);
    assert.match(fx.row('other_process_environment').detail, /^visible:/); assert.match(fx.row('outside_read_denied').detail, /Read the sentinel/); assert.match(fx.row('home_private').detail, /readable inside: .*\.zshrc/, 'the fake home entry was picked and found readable');
    // The fake honours --home, so HOME is not the real home, but the real .codex is listable; the control fails the same way.
    assert.match(fx.row('real_agent_state_denied').detail, new RegExp(`^HOME=\\S+/homes/check-home: \\S+/home/\\.codex is listable inside; positive control failed: HOME=\\S+/homes/control-home: \\S+/control-real-home/\\.codex is listable inside$`));
    // The server's own arguments: --home directly after --cwd, an empty folder in the temporary base.
    const invocation = /## ALA invocation\n\n```sh\n(.*)\n```/.exec(fx.report)[1].split(' ');
    assert.equal(invocation[invocation.indexOf('--cwd') + 2], '--home'); assert.match(invocation[invocation.indexOf('--home') + 1], /\/ala-sandbox-base-[^/]+\/homes\/check-home$/);
    assert.match(fx.report, /## Positive control of real_agent_state_denied/);
    assert.match(fx.report, /list of entries was unchanged while it ran/); assert.deepEqual((await fs.readdir(fx.homeDir)).sort(), before, 'the fake home is untouched');
    assert.deepEqual(await fs.readdir(path.join(fx.homeDir, '.codex')), ['auth.json'], 'nothing was written into the fake real state folder');
    await fixturesGone(fx);
  } finally { await fx.close(); }
});

test('main() fails other_process_environment when the helper stops during the run, although the probe then reports hidden', async () => {
  const fx = await mainFixture({ body: 'await new Promise(resolve => setTimeout(resolve, 2500));', helperOptions: { args: ['-e', 'process.stdin.resume();setTimeout(() => process.exit(0), 1200)'] } });
  try {
    await fx.run();
    // Without the confirmation after the run, this is the meaningless "hidden": the process the reader asks about no longer exists.
    assert.match(await probeRow(fx, 'other_process_environment'), /\tpass\thidden: /);
    assert.equal(fx.row('other_process_environment').status, 'fail'); assert.match(fx.row('other_process_environment').detail, /cannot|did not run/); assert.equal(fx.code, 1);
    await fixturesGone(fx);
  } finally { await fx.close(); }
});

test('main() fails outside_read_denied when the host can no longer read the sentinel after the run', async () => {
  const fx = await mainFixture({ body: "fs.rmSync(grab('SFILE'));" });
  try {
    await fx.run();
    assert.match(await probeRow(fx, 'outside_read_denied'), /\tpass\tNeither read nor listed/, 'the probe sees nothing to read');
    assert.equal(fx.row('outside_read_denied').status, 'fail'); assert.match(fx.row('outside_read_denied').detail, /host can no longer read the sentinel/);
    await fixturesGone(fx);
  } finally { await fx.close(); }
});

test('main() fails home_private when the host cannot read the chosen home entry', { skip: unprivileged }, async () => {
  const fx = await mainFixture({ body: "fs.chmodSync(path.join(grab('HREAL'), grab('HENTRY')), 0); fs.chmodSync(grab('HREAL'), 0o300);" });
  try {
    await fx.run();
    assert.match(await probeRow(fx, 'home_private'), /\tpass\t/, 'the probe finds the entry unreadable and the home unlistable');
    assert.equal(fx.row('home_private').status, 'fail'); assert.match(fx.row('home_private').detail, /host cannot read .*\.zshrc/);
  } finally { await fx.close(); }
});

test('main() fails real_agent_state_denied with the host problem when the host can no longer list the real state folder', { skip: unprivileged }, async () => {
  // The fake ALA makes the real .codex unlistable (mode 000) before the probe runs; the control run's fixture is left alone.
  const fx = await mainFixture({ body: "if (!grab('SREAL').includes('control-real-home')) fs.chmodSync(path.join(grab('SREAL'), '.codex'), 0);" });
  try {
    await fx.run();
    assert.equal(fx.row('real_agent_state_denied').status, 'fail');
    assert.equal(fx.row('real_agent_state_denied').detail, `The host cannot list ${path.join(fx.homeDir, '.codex')}, so a refusal inside proves nothing.`);
    await fixturesGone(fx);
  } finally { await fs.chmod(path.join(fx.homeDir, '.codex'), 0o700).catch(() => {}); await fx.close(); }
});

test('stateHostProblem: a state folder the host cannot list, or cannot even examine, is a problem; an absent one is left out', { skip: unprivileged }, async () => {
  const { agentStateFolders, stateHostProblem } = await import('./check-ala-sandbox.mjs'), { loadALALogins } = await import('./agent-home.mjs');
  const dir = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'ala-state-host-')));
  try {
    const logins = await loadALALogins(path.join(await createFakeALA(path.join(dir, 'ala'), ''), 'bin/ala.mjs')), home = path.join(dir, 'home'), probeHome = path.join(dir, 'never-created');
    await fs.mkdir(path.join(home, '.codex'), { recursive: true, mode: 0o700 });
    const state = await agentStateFolders({ logins, home, probeHome });
    assert.deepEqual(state.folders.map(f => f.path), [path.join(home, '.codex')]); assert.deepEqual(state.problems, []);
    assert.ok(Number.isSafeInteger(state.folders[0].dev) && Number.isSafeInteger(state.folders[0].ino), 'device and inode are recorded');
    assert.equal(stateHostProblem(state), null, 'a listable folder is no problem');
    // Mode 000: the host cannot list it.
    await fs.chmod(path.join(home, '.codex'), 0);
    assert.equal(stateHostProblem(state), `The host cannot list ${path.join(home, '.codex')}, so a refusal inside proves nothing.`);
    await fs.chmod(path.join(home, '.codex'), 0o700);
    // A home the host cannot search: stat fails with EACCES, which must not drop the folder silently.
    await fs.chmod(home, 0o600);
    try {
      const unknown = await agentStateFolders({ logins, home, probeHome });
      assert.deepEqual(unknown.folders, []);
      assert.deepEqual(unknown.problems, ['.codex', '.pi/agent', '.claude'].map(folder => `The host cannot examine ${path.join(home, folder)} (EACCES), so the row cannot tell whether it is protected.`));
      assert.equal(stateHostProblem(unknown), unknown.problems[0]);
    } finally { await fs.chmod(home, 0o700); }
    // An absent folder is simply not a state folder.
    await fs.rm(path.join(home, '.codex'), { recursive: true });
    const absent = await agentStateFolders({ logins, home, probeHome });
    assert.deepEqual([absent.folders, absent.problems, stateHostProblem(absent)], [[], [], null]);
  } finally { await fs.rm(dir, { recursive: true, force: true }); }
});

test('main() refuses to start ALA when ALA would not run the probe script, and ALA never starts', async () => {
  const other = DISCOVERY.replace("return [{ name: 'codex', available: Boolean(binary), binary }];", "return [{ name: 'codex', available: true, binary: '/usr/bin/true' }];");
  const fx = await mainFixture({ discovery: other });
  try {
    await assert.rejects(fx.run(), /Refusing to start ALA: ALA would run \/\S*true instead of the probe script/);
    const checks = path.join(fx.root, 'tmp/ala-sandbox-check'), [check] = await fs.readdir(checks);
    assert.deepEqual((await fs.readdir(path.join(checks, check, 'run'))).sort(), ['instructions.md'], 'the fake ALA never ran: it would have recorded its invocation');
    assert.deepEqual((await fs.readdir(await fs.realpath(os.tmpdir()))).filter(name => name.startsWith('ala-sandbox-sentinel-')), [], 'the sentinel is removed on an error');
  } finally { await fx.close(); }
});

test("main({mode: 'own'}): ALA runs without --home, agent_state_shared passes on the fixture folder and there is no real_agent_state_denied row", async () => {
  // The fake passes the given environment on, as ALA's default does: HOME is the fixture home, so $HOME/.codex is its folder.
  const fx = await mainFixture(), { confinementFor, modeStatement } = await import('./check-ala-sandbox.mjs');
  try {
    const before = (await fs.readdir(fx.homeDir)).sort(); await fx.run({ mode: 'own' });
    assert.equal(fx.code, 1, 'nothing confines the fake, so the confinement rows fail: ' + fx.report);
    assert.match(fx.report, /^# ALA sandbox check\n\nConfigured mode: own home\. Mode checked: own home\.\n/); assert.ok(fx.report.includes(modeStatement('own', 'own')));
    assert.equal(fx.row('real_agent_state_denied'), undefined, 'no real_agent_state_denied row in own mode'); assert.doesNotMatch(fx.report, /real_agent_state_denied|Positive control/);
    const shared = fx.row('agent_state_shared');
    assert.equal(shared.status, 'pass', shared.detail); assert.match(shared.detail, /^shared: \$HOME\/\.codex \(HOME=\S+\/ala-sandbox-base-[^/]+\/own-home\) is the folder \S+\/ala-sandbox-base-[^/]+\/own-home\/\.codex, and a marker written into it; its marker reached the fixture folder$/);
    for (const key of confinementFor('own')) assert.equal(fx.row(key).status, 'fail', key);
    for (const key of ['cwd', 'run_write', 'repo_read']) assert.equal(fx.row(key).status, 'pass', key);
    assert.match(fx.row('home_private').detail, /readable inside: .*\.zshrc/, 'home_private is measured against the real home passed to main, not the fixture');
    const invocation = /## ALA invocation\n\n```sh\n(.*)\n```/.exec(fx.report)[1].split(' ');
    assert.equal(invocation.includes('--home'), false, 'own mode runs ALA without --home'); assert.equal(invocation[invocation.indexOf('--cwd') + 2], '--folder');
    assert.match(fx.report, /`--home` was not passed, and `HOME` and the agent state variables pointed to the fixture home/); assert.doesNotMatch(fx.report, /--home` was an empty folder/);
    assert.deepEqual((await fs.readdir(fx.homeDir)).sort(), before, 'the fake real home is untouched'); assert.deepEqual(await fs.readdir(path.join(fx.homeDir, '.codex')), ['auth.json'], 'no marker reached the fake real state folder');
    await fixturesGone(fx);
  } finally { await fx.close(); }
});

test("main({mode: 'own'}) fails agent_state_shared with 'not shared:' when the probe gets a private HOME", async () => {
  // A sandbox that gives the agent a private HOME although no --home was passed: the default mode would not find a login.
  const fx = await mainFixture({ body: "globalThis.probeEnv = { ...agentEnv, HOME: fs.mkdtempSync(path.join(cwd, 'private-')) };" });
  try {
    await fx.run({ mode: 'own' });
    const shared = fx.row('agent_state_shared');
    assert.equal(shared.status, 'fail'); assert.match(shared.detail, /^not shared: \$HOME\/\.codex does not exist inside \(HOME=\S+\/run\/private-\w+\)$/);
    assert.equal(fx.code, 1); assert.match(fx.report, /\*\*Result: failed\.\*\*/);
    assert.deepEqual(await fs.readdir(path.join(fx.homeDir, '.codex')), ['auth.json']);
    await fixturesGone(fx);
  } finally { await fx.close(); }
});

test('main() without a mode checks the configured one: own without a setting file, folder with one; the report names both modes', async () => {
  const fx = await mainFixture(), { modeStatement } = await import('./check-ala-sandbox.mjs'), { writeAgentHome } = await import('./agent-home.mjs');
  try {
    await fx.run({ mode: undefined });
    assert.equal(fx.code, 1); assert.match(fx.report, /^# ALA sandbox check\n\nConfigured mode: own home\. Mode checked: own home\.$/m); assert.ok(fx.report.includes(modeStatement('own', 'own')));
    assert.ok(fx.row('agent_state_shared')); assert.equal(fx.row('real_agent_state_denied'), undefined); assert.equal(/## ALA invocation\n\n```sh\n(.*)\n```/.exec(fx.report)[1].includes('--home'), false);
    await fixturesGone(fx);
    // A setting (written into the fixture home, never the real one) makes the folder mode the configured one.
    const folder = path.join(fx.dir, 'set-folder'); writeAgentHome(folder, { root: fx.root, home: fx.homeDir });
    const settingBefore = await fs.readFile(path.join(fx.homeDir, '.lean-documents/agent-home.json'), 'utf8');
    await fx.run({ mode: undefined });
    assert.match(fx.report, /^Configured mode: folder\. Mode checked: folder\.$/m); assert.ok(fx.report.includes(modeStatement('folder', 'folder')));
    assert.ok(fx.row('real_agent_state_denied')); assert.equal(fx.row('agent_state_shared'), undefined);
    assert.match(/## ALA invocation\n\n```sh\n(.*)\n```/.exec(fx.report)[1], / --home \S+\/ala-sandbox-base-[^/]+\/homes\/check-home /, 'the check uses its own empty folder, never the set one');
    assert.deepEqual(await fs.readdir(await fs.realpath(folder)), [], 'nothing was written into the set folder'); await fixturesGone(fx);
    // --mode checks the other mode on demand; the configured one is still named.
    await fx.run({ mode: 'own' });
    assert.match(fx.report, /^Configured mode: folder\. Mode checked: own home\.$/m); assert.ok(fx.row('agent_state_shared'));
    assert.equal(await fs.readFile(path.join(fx.homeDir, '.lean-documents/agent-home.json'), 'utf8'), settingBefore, 'the check never changes the setting');
    await fixturesGone(fx);
  } finally { await fx.close(); }
});

test('main() exits 2 for an unreadable setting without a mode, an unknown mode and an ALA inside the set folder, and leaves nothing behind', async () => {
  const fx = await mainFixture(), { writeAgentHome } = await import('./agent-home.mjs'), base = await fs.realpath(os.tmpdir());
  const leftovers = async () => (await fs.readdir(base)).filter(name => name.startsWith('ala-sandbox-sentinel-') || name.startsWith('ala-sandbox-base-'));
  try {
    await fs.mkdir(path.join(fx.homeDir, '.lean-documents')); await fs.writeFile(path.join(fx.homeDir, '.lean-documents/agent-home.json'), 'not json');
    await fx.run({ mode: undefined });
    assert.equal(fx.code, 2); assert.match(fx.errors, /not a usable agent-home setting \(not JSON\)/); assert.equal(fx.output, '');
    await fx.run({ mode: 'bogus' }); assert.equal(fx.code, 2); assert.match(fx.errors, /^Usage: node tools\/check-ala-sandbox\.mjs \[--mode own\|folder\]$/);
    assert.equal(await fs.stat(path.join(fx.root, 'tmp')).catch(() => null), null, 'no check folder was created'); assert.deepEqual(await leftovers(), []);
    // An explicit mode still runs when the setting is unreadable; the configured mode is then unknown.
    await fx.run({ mode: 'own' });
    assert.match(fx.report, /^Configured mode: unknown \(the setting could not be read\)\. Mode checked: own home\.$/m); await fixturesGone(fx);
    // ALA placed inside the set folder is refused, as it is inside tmp/: automatic detection moves on, and finds nothing.
    await fs.rm(path.join(fx.homeDir, '.lean-documents/agent-home.json'));
    const folder = path.join(fx.dir, 'set-folder'); writeAgentHome(folder, { root: fx.root, home: fx.homeDir });
    await fs.rename(fx.ala, path.join(folder, 'ala')); await fs.symlink(path.join(folder, 'ala'), fx.ala);
    const detect = { alaCli: null, environment: { PATH: '/nonexistent' } };
    await fx.run({ ...detect, mode: undefined });
    assert.equal(fx.code, 2); assert.match(fx.errors, /^ALA was not found\. Checked: .*lies inside .*set-folder.*Run "npm install" in this repository, or set ALA_CLI to the AdvancedLanguageAgent checkout\.$/s);
    assert.doesNotMatch(fx.errors, /Agent settings/); assert.deepEqual(await leftovers(), []);
    await fs.rm(path.join(fx.homeDir, '.lean-documents/agent-home.json'));
    await fx.run({ ...detect, mode: undefined }); assert.match(fx.report, /Configured mode: own home\./, 'without the setting the same location is found'); await fixturesGone(fx);
  } finally { await fx.close(); }
});

test('the command line takes only --mode own|folder; any other argument, including a removed option, exits 2 before anything runs', async () => {
  const { execFile } = await import('node:child_process'), home = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'ala-cli-home-')));
  try {
    for (const args of [['--bogus'], ['--mode'], ['--mode', 'both'], ['--mode=own'], ['own'], ['--mode', 'own', 'extra']]) {
      const result = await new Promise(resolve => execFile(process.execPath, [fileURLToPath(new URL('./check-ala-sandbox.mjs', import.meta.url)), ...args], { env: { ...process.env, HOME: home } }, (error, stdout, stderr) => resolve({ code: error?.code ?? 0, stdout, stderr })));
      assert.equal(result.code, 2, args.join(' ')); assert.match(result.stderr, /^Usage: node tools\/check-ala-sandbox\.mjs \[--mode own\|folder\]\n$/); assert.equal(result.stdout, '');
    }
    assert.deepEqual(await fs.readdir(home), [], 'nothing was created in the home');
  } finally { await fs.rm(home, { recursive: true, force: true }); }
});

test('main() refuses the run when the fake ALA ignores --home: the marker is missing from the --home folder', async () => {
  const fx = await mainFixture({ body: "globalThis.probeEnv = { ...process.env, HOME: fs.mkdtempSync(path.join(cwd, 'elsewhere-')) };" });
  try {
    await fx.run();
    assert.equal(fx.code, 1); assert.match(fx.row('real_agent_state_denied').detail, /marker is missing from the --home folder/);
  } finally { await fx.close(); }
});

test('the sentinel moves out of a temporary folder inside the home, or is refused', async () => {
  const { makeSentinel, removeSentinel } = await import('./check-ala-sandbox.mjs');
  const dir = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'ala-sentinel-home-')));
  try {
    const home = path.join(dir, 'home'), tmp = path.join(home, 'T'); await fs.mkdir(tmp, { recursive: true });
    const sentinel = await makeSentinel(path.join(dir, 'repo'), { home, tmpdir: tmp });
    try { assert.equal(sentinel.base, await fs.realpath('/tmp')); assert.ok(!sentinel.dir.startsWith(home)); } finally { assert.equal(removeSentinel(sentinel), true); }
    assert.deepEqual(await fs.readdir(tmp), [], 'nothing was created in the temporary folder inside the home');
    await assert.rejects(makeSentinel(path.join(dir, 'repo'), { home: '/', tmpdir: tmp }), /lies inside the home folder/);
  } finally { await fs.rm(dir, { recursive: true, force: true }); }
});
