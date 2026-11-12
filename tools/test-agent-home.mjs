/** Checks of tools/agent-home.mjs and tools/setup-agent-home.mjs (criterion H-1 of the dedicated-agent-home addendum).
 *
 * No coding agent and no model is involved: ALA is the simulated checkout of tools/fake-ala-fixture.mjs and every
 * sign-in command is a small Node script written here. Every fixture, base and home lies in a fresh folder under the
 * host temporary folder, never in the real home or in the repository, and is removed afterwards, because the fixtures
 * hold (fake) login tokens. The token is never written into the repository; assertions use hashes or its absence.
 *
 * Parity with a real ALA runs when ALA_CLI is set; otherwise it is skipped with the reason "ALA_CLI is not set". */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawn, spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import * as agentHome from './agent-home.mjs';
import {createFakeALA} from './fake-ala-fixture.mjs';
import {main as setupMain} from './setup-agent-home.mjs';

const {createAgentHomes, homeUnavailable, loadALALogins, noLogin, reasons} = agentHome;
const TOOLS = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(TOOLS, '..');
const SCRIPT = path.join(TOOLS, 'setup-agent-home.mjs');
const TEMPORARY = fs.realpathSync.native(os.tmpdir());
const TOKEN = `fixture-token-${crypto.randomBytes(12).toString('hex')}`;
const CODEX_LOGIN = JSON.stringify({auth_mode: 'chatgpt', tokens: {access_token: TOKEN}});
const CLAUDE_CREDENTIALS = JSON.stringify({claudeAiOauth: {accessToken: `${TOKEN}-claude`}});
const CLAUDE_CONFIG = JSON.stringify({numStartups: 1});
const sha = data => crypto.createHash('sha256').update(data).digest('hex');
const modeOf = file => fs.lstatSync(file).mode & 0o777;
// Suite logs may land in the repository (tools/verify.mjs keeps them under tmp/verify), so no assertion prints the
// fixture token: contents are compared by hash, and messages are redacted.
const redact = text => String(text).split(TOKEN).join('<fixture token>');
const sameBytes = (actual, expected, label) => assert.equal(sha(actual), sha(expected), label);

// ---------- Fixtures ----------

function removeFixture(dir) {
  // Only a folder this suite made directly in the host temporary folder.
  if (path.dirname(dir) !== TEMPORARY || !path.basename(dir).startsWith('agent-home-test-')) return;
  if (process.platform === 'darwin') spawnSync('/usr/bin/chflags', ['-R', 'nouchg,nouappnd', dir]);
  spawnSync('/bin/chmod', ['-R', 'u+rwx', dir]);
  fs.rmSync(dir, {recursive: true, force: true});
}

async function fixture(t, {name = 'base'} = {}) {
  const dir = fs.realpathSync.native(await fsp.mkdtemp(path.join(TEMPORARY, 'agent-home-test-')));
  t.after(() => removeFixture(dir));
  const ala = await createFakeALA(path.join(dir, 'ala'), '');
  const entry = path.join(ala, 'bin/ala.mjs');
  const base = path.join(dir, name);
  const make = (options = {}) => createAgentHomes({root: ROOT, base, logins: () => loadALALogins(entry), environment: () => ({}), ...options});
  return {dir, ala, entry, base, make};
}

function writeTemplate(homes, files) {
  for (const [relative, content] of Object.entries(files)) {
    const file = path.join(homes.template, relative);
    let folder = homes.template;
    for (const part of path.dirname(relative).split('/').filter(p => p !== '.')) {
      folder = path.join(folder, part);
      if (!fs.existsSync(folder)) fs.mkdirSync(folder, {mode: 0o700});
      fs.chmodSync(folder, 0o700);
    }
    fs.writeFileSync(file, content);
    fs.chmodSync(file, 0o600);
  }
}

function listTree(folder) {
  return fs.readdirSync(folder, {recursive: true}).map(String).sort();
}

function templateFingerprint(homes) {
  if (!fs.existsSync(homes.template)) return 'absent';
  return listTree(homes.template).map(relative => {
    const file = path.join(homes.template, relative);
    return fs.lstatSync(file).isFile() ? `${relative}:${sha(fs.readFileSync(file))}` : relative;
  }).join('\n');
}

function deadPid() {
  return Number(spawnSync(process.execPath, ['-e', 'process.stdout.write(String(process.pid))'], {encoding: 'utf8'}).stdout);
}

/** A detached process that leads its own group, as ALA does. */
function startGroup({ignoreTerm = false} = {}) {
  const child = spawn(process.execPath, ['-e', `${ignoreTerm ? "process.on('SIGTERM',()=>{});" : ''}setInterval(()=>{},1000)`], {detached: true, stdio: 'ignore'});
  const exited = new Promise(resolve => child.on('exit', resolve));
  return {
    pid: child.pid,
    exited,
    kill() {
      try {
        process.kill(-child.pid, 'SIGKILL');
      } catch {}
      return exited;
    }
  };
}

async function rejectsWith(promise, message) {
  await assert.rejects(promise, error => {
    assert.equal(error.message, message);
    return true;
  });
}

const grepToken = folder => spawnSync('/usr/bin/grep', ['-r', '-l', '--', TOKEN, folder], {encoding: 'utf8'});

// ---------- Base ----------

test('Base: a relative AUDIT_AGENT_HOME and a base inside the repository are refused; the default comes from the home', async t => {
  const {dir, entry} = await fixture(t);
  assert.equal(agentHome.resolveAgentHomeBase({env: {}, home: '/home/someone'}), '/home/someone/.lean-documents/agent-home');
  assert.deepEqual(agentHome.agentHomeLocation({env: {AUDIT_AGENT_HOME: path.join(dir, 'x')}, home: '/h'}), {base: path.join(dir, 'x'), explicit: true});
  assert.throws(() => agentHome.resolveAgentHomeBase({env: {AUDIT_AGENT_HOME: 'relative/base'}}), {message: homeUnavailable(reasons.relativeBase('relative/base'))});

  const repository = fs.realpathSync.native(ROOT);
  const inside = path.join(repository, 'tmp', `agent-home-never-created-${process.pid}`);
  assert.throws(() => agentHome.prepareBase({base: inside, root: ROOT, create: true}), {message: homeUnavailable(reasons.insideRepository(inside, repository))});
  assert.equal(fs.existsSync(inside), false, 'a refused base is never created');
  // A link into the repository is refused after resolution.
  fs.symlinkSync(path.join(repository, 'tmp'), path.join(dir, 'link-into-repository'));
  // The text names the resolved location when the link is an ancestor, and the link itself when it is the base.
  const linked = path.join(dir, 'link-into-repository', 'agent-home');
  assert.throws(() => agentHome.prepareBase({base: linked, root: ROOT, create: true}), {message: homeUnavailable(reasons.insideRepository(path.join(repository, 'tmp', 'agent-home'), repository))});
  assert.equal(fs.existsSync(path.join(repository, 'tmp', 'agent-home')), false);
  const homes = createAgentHomes({root: ROOT, base: path.join(dir, 'link-into-repository'), logins: () => loadALALogins(entry)});
  await rejectsWith(homes.open('codex'), homeUnavailable(reasons.insideRepository(path.join(dir, 'link-into-repository'), repository)));
  assert.equal((await homes.status()).error, homeUnavailable(reasons.insideRepository(path.join(dir, 'link-into-repository'), repository)));
  for (const name of ['template', 'homes', 'staging', 'template.lock']) assert.equal(fs.existsSync(path.join(repository, 'tmp', name)), false, name);
  // Node's installation is readable to every run.
  const runtime = agentHome.nodeRuntimeRoot();
  assert.throws(() => agentHome.prepareBase({base: path.join(runtime, 'agent-home'), root: ROOT}), {message: homeUnavailable(reasons.insideRuntime(path.join(runtime, 'agent-home'), runtime))});
});

test('Base: a different letter case of the repository path is still inside it (case-insensitive volume)', {skip: process.platform === 'darwin' ? false : 'only macOS volumes are case-insensitive here'}, () => {
  const repository = fs.realpathSync.native(ROOT);
  const variant = path.join(repository.toUpperCase(), 'TMP', 'agent-home');
  assert.ok(fs.existsSync(repository.toUpperCase()), 'the volume is case-insensitive');
  assert.throws(() => agentHome.prepareBase({base: variant, root: ROOT}), error => error.message.includes('lies inside the repository'));
});

test('Base: a path with spaces and non-ASCII characters works for open and close', async t => {
  const {make} = await fixture(t, {name: 'base with spaces ü ā 日本'});
  const homes = make();
  await homes.check({create: true});
  writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN});
  const home = await homes.open('codex');
  assert.ok(home.path.startsWith(homes.homes + path.sep));
  assert.equal((await home.close()).removed, true);
  assert.deepEqual(fs.readdirSync(homes.homes), []);
});

test('Base: the default location creates its parent with mode 0700 inside the injected home only', async t => {
  const {dir, entry} = await fixture(t);
  const fakeHome = path.join(dir, 'home');
  fs.mkdirSync(fakeHome);
  const location = agentHome.agentHomeLocation({env: {}, home: fakeHome});
  const homes = createAgentHomes({root: ROOT, ...location, logins: () => loadALALogins(entry), environment: () => ({})});
  const status = await homes.status();
  assert.equal(status.ready, true);
  assert.equal(fs.existsSync(path.join(fakeHome, '.lean-documents')), false, 'status() creates nothing');
  await homes.check({create: true});
  assert.equal(modeOf(path.join(fakeHome, '.lean-documents')), 0o700);
  for (const folder of [homes.base, homes.template, homes.homes, homes.staging]) assert.equal(modeOf(folder), 0o700, folder);
});

// ---------- Template ----------

test('Template: only allowlisted files are accepted; everything else refuses every agent with the exact text', async t => {
  const {make, dir} = await fixture(t);
  const homes = make();
  await homes.check({create: true});
  writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN, '.claude/.credentials.json': CLAUDE_CREDENTIALS, '.claude/.claude.json': CLAUDE_CONFIG});
  assert.equal((await homes.status()).ready, true);
  const reset = () => {
    for (const name of fs.readdirSync(homes.template)) fs.rmSync(path.join(homes.template, name), {recursive: true, force: true});
    writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN});
  };
  const outside = path.join(dir, 'outside');
  fs.mkdirSync(outside, {mode: 0o700});
  fs.writeFileSync(path.join(outside, 'auth.json'), CODEX_LOGIN, {mode: 0o600});
  const cases = [
    ['.codex/config.toml', () => writeTemplate(homes, {'.codex/config.toml': 'model = "x"\n'}), reasons.foreign('.codex/config.toml', 'file')],
    ['.claude/settings.json', () => writeTemplate(homes, {'.claude/settings.json': '{}'}), reasons.foreign('.claude/settings.json', 'file')],
    ['.local/bin/codex', () => writeTemplate(homes, {'.local/bin/codex': '#!/bin/sh\n'}), reasons.foreign('.local', 'folder')],
    ['.DS_Store', () => writeTemplate(homes, {'.DS_Store': ''}), reasons.foreign('.DS_Store', 'file')],
    ['a link at an allowlisted file name', () => {
      fs.unlinkSync(path.join(homes.template, '.codex/auth.json'));
      fs.symlinkSync(path.join(outside, 'auth.json'), path.join(homes.template, '.codex/auth.json'));
    }, reasons.foreign('.codex/auth.json', 'link')],
    ['a link at an allowlisted folder name', () => {
      fs.rmSync(path.join(homes.template, '.codex'), {recursive: true});
      fs.symlinkSync(outside, path.join(homes.template, '.codex'));
    }, reasons.foreign('.codex', 'link')],
    ['a FIFO', () => {
      fs.unlinkSync(path.join(homes.template, '.codex/auth.json'));
      assert.equal(spawnSync('/usr/bin/mkfifo', ['-m', '600', path.join(homes.template, '.codex/auth.json')]).status, 0);
    }, reasons.foreign('.codex/auth.json', 'FIFO')],
    ['16 MiB plus one byte', () => fs.truncateSync(path.join(homes.template, '.codex/auth.json'), 16 * 1024 * 1024 + 1), reasons.tooLarge('.codex/auth.json')],
    ['a group-readable file', () => fs.chmodSync(path.join(homes.template, '.codex/auth.json'), 0o640), reasons.notPrivate(path.join(homes.template, '.codex/auth.json'), 'mode 0640', 'file')],
    ['a folder with access for others', () => fs.chmodSync(path.join(homes.template, '.codex'), 0o755), reasons.notPrivate(path.join(homes.template, '.codex'), 'mode 0755')]
  ];
  for (const [label, damage, reason] of cases) {
    reset();
    damage();
    const expected = homeUnavailable(reason);
    const status = await homes.status();
    assert.equal(status.ready, false, label);
    assert.equal(status.error, expected, label);
    await rejectsWith(homes.open('claude'), expected);
    await rejectsWith(homes.requireLogin('codex'), expected);
  }
  assert.deepEqual(fs.readdirSync(homes.homes), [], 'no refused open left a folder');
  // A template that is itself a link.
  reset();
  fs.renameSync(homes.template, path.join(dir, 'real-template'));
  fs.symlinkSync(path.join(dir, 'real-template'), homes.template);
  await rejectsWith(homes.open('codex'), homeUnavailable(reasons.notPrivate(homes.template, 'a link')));
});

test('Template: an empty template, a zero-byte login and a login of exactly 16 MiB are accepted', async t => {
  const {make, base} = await fixture(t);
  const homes = make();
  await homes.check({create: true});
  assert.equal((await homes.status()).ready, true);
  await rejectsWith(homes.open('codex'), noLogin('codex', base));
  writeTemplate(homes, {'.codex/auth.json': ''});
  assert.equal((await homes.status()).ready, true);
  await rejectsWith(homes.open('codex'), noLogin('codex', base));
  fs.truncateSync(path.join(homes.template, '.codex/auth.json'), 16 * 1024 * 1024);
  assert.equal((await homes.status()).ready, true);
  await rejectsWith(homes.open('codex'), noLogin('codex', base));
  assert.deepEqual(fs.readdirSync(homes.homes), []);
});

test('Template: a -shm file beside an allowed SQLite database is tolerated and never copied; elsewhere it is refused', async t => {
  const {dir} = await fixture(t);
  // An OMP-like agent whose login is a database, written here because the fake ALA has none.
  const module = {
    homeEnvironment: (env, home) => ({...env, HOME: home, OMP_DIR: path.join(home, '.omp')}),
    codingAgentLoginFiles: (name, {env}) => ['agent/agent.db', 'agent/agent.db-wal'].map(file => path.join(env.OMP_DIR, file)),
    detectCodingAgentLogin: async (name, {env}) => ({loggedIn: fs.existsSync(path.join(env.OMP_DIR, 'agent/agent.db')), file: fs.existsSync(path.join(env.OMP_DIR, 'agent/agent.db')), env: []}),
    detectCodingAgentLogins: async ({env}) => ({omp: await module.detectCodingAgentLogin('omp', {env})})
  };
  const homes = createAgentHomes({root: ROOT, base: path.join(dir, 'base'), logins: async () => module, environment: () => ({})});
  await homes.check({create: true});
  writeTemplate(homes, {'.omp/agent/agent.db': 'db', '.omp/agent/agent.db-wal': 'wal', '.omp/agent/agent.db-shm': 'shm'});
  assert.equal((await homes.status()).ready, true);
  const home = await homes.open('omp');
  assert.deepEqual(listTree(home.path), ['.omp', '.omp/agent', '.omp/agent/agent.db', '.omp/agent/agent.db-wal']);
  await home.close();
  writeTemplate(homes, {'.omp/agent/agent.db-wal-shm': 'x'});
  assert.equal((await homes.status()).error, homeUnavailable(reasons.foreign('.omp/agent/agent.db-wal-shm', 'file')));
});

// ---------- Open ----------

for (const umask of [0o000, 0o077]) {
  test(`Open: a Codex home holds exactly its login, inside homes/, with modes 0700 and 0600 under umask 0${umask.toString(8).padStart(2, '0')}`, async t => {
    const {make} = await fixture(t);
    const previous = process.umask(umask);
    t.after(() => process.umask(previous));
    const homes = make();
    await homes.check({create: true});
    writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN, '.claude/.credentials.json': CLAUDE_CREDENTIALS, '.claude/.claude.json': CLAUDE_CONFIG});
    const home = await homes.open('codex', {purpose: 'audit'});
    assert.deepEqual(listTree(home.path), ['.codex', '.codex/auth.json']);
    assert.equal(path.dirname(home.path), homes.homes);
    assert.match(path.basename(home.path), new RegExp(`^home-${process.pid}-[0-9a-f]{16}$`));
    assert.notEqual(home.path, homes.template);
    assert.equal(modeOf(home.path), 0o700);
    assert.equal(modeOf(path.join(home.path, '.codex')), 0o700);
    assert.equal(modeOf(path.join(home.path, '.codex/auth.json')), 0o600);
    assert.deepEqual(home.files, {'.codex/auth.json': sha(CODEX_LOGIN)});
    assert.equal(home.source, 'file');
    assert.equal(home.purpose, 'audit');
    const owner = JSON.parse(fs.readFileSync(`${home.path}.owner`, 'utf8'));
    assert.equal(owner.server, process.pid);
    assert.equal(owner.group, null, 'the owner record is written when the home is created');
    assert.equal(modeOf(`${home.path}.owner`), 0o600);
    assert.equal((await home.close()).removed, true);
  });
}

test('Open: twelve parallel opens give twelve distinct homes, and closing them leaves homes/ empty', async t => {
  const {make} = await fixture(t);
  const homes = make();
  await homes.check({create: true});
  writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN});
  const opened = await Promise.all(Array.from({length: 12}, () => homes.open('codex')));
  assert.equal(new Set(opened.map(home => home.path)).size, 12);
  for (const home of opened) assert.deepEqual(listTree(home.path), ['.codex', '.codex/auth.json']);
  const results = await Promise.all(opened.map(home => home.close()));
  assert.ok(results.every(result => result.removed && result.rewritten.length === 0));
  assert.deepEqual(fs.readdirSync(homes.homes), []);
});

test('Open: an unknown agent is refused', async t => {
  const {make} = await fixture(t);
  const homes = make();
  await assert.rejects(homes.open('nosuch'), {message: 'Unknown coding agent: nosuch', kind: 'unknownAgent'});
  await assert.rejects(homes.requireLogin('--force'), {kind: 'unknownAgent'});
});

// ---------- No login ----------

test('No login: an absent or unparsable login gives the exact text and leaves no folder', async t => {
  const {make, base} = await fixture(t);
  const homes = make();
  await homes.check({create: true});
  await rejectsWith(homes.open('codex'), noLogin('codex', base));
  await rejectsWith(homes.requireLogin('codex'), noLogin('codex', base));
  writeTemplate(homes, {'.codex/auth.json': '{not json'});
  await rejectsWith(homes.open('codex'), noLogin('codex', base));
  assert.deepEqual(fs.readdirSync(homes.homes), []);
  assert.deepEqual(fs.readdirSync(homes.staging), []);
});

test('No login: a credential variable gives an empty home with source environment, and its value appears nowhere', async t => {
  const {make} = await fixture(t);
  const secret = `variable-value-${crypto.randomBytes(8).toString('hex')}`;
  const homes = make({environment: () => ({OPENAI_API_KEY: secret, PATH: '/usr/bin:/bin'})});
  await homes.check({create: true});
  const home = await homes.open('codex');
  assert.deepEqual(fs.readdirSync(home.path), []);
  assert.equal(home.source, 'environment');
  assert.deepEqual(home.variables, ['OPENAI_API_KEY']);
  const required = await homes.requireLogin('codex');
  assert.deepEqual(required, {source: 'environment', variables: ['OPENAI_API_KEY']});
  const status = await homes.status();
  assert.deepEqual(status.agents.codex, {loggedIn: true, source: 'environment', variables: ['OPENAI_API_KEY'], rewritten: null});
  assert.equal(fs.readFileSync(`${home.path}.owner`, 'utf8').includes(secret), false);
  for (const value of [home, required, status, await home.close()]) assert.equal(JSON.stringify(value).includes(secret), false);
});

// ---------- Close ----------

test('Close: a changed, deleted, link-replaced or folder-replaced login is reported as rewritten; the template is unchanged', async t => {
  const {make, dir} = await fixture(t);
  const homes = make();
  await homes.check({create: true});
  writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN});
  const before = templateFingerprint(homes);
  const outsideFile = path.join(dir, 'outside-auth.json');
  fs.writeFileSync(outsideFile, CODEX_LOGIN, {mode: 0o600});
  const damages = {
    changed: file => fs.writeFileSync(file, JSON.stringify({tokens: {access_token: 'rotated'}})),
    deleted: file => fs.unlinkSync(file),
    'link-replaced': file => {
      fs.unlinkSync(file);
      fs.symlinkSync(outsideFile, file);
    },
    'folder-replaced': file => {
      fs.unlinkSync(file);
      fs.mkdirSync(file);
    },
    'a FIFO': file => {
      fs.unlinkSync(file);
      assert.equal(spawnSync('/usr/bin/mkfifo', [file]).status, 0, 'mkfifo');
    },
    'a huge file': file => fs.truncateSync(file, 16 * 1024 * 1024 + 1)
  };
  for (const [label, damage] of Object.entries(damages)) {
    const home = await homes.open('codex');
    damage(path.join(home.path, '.codex/auth.json'));
    const result = await home.close();
    assert.deepEqual(result.rewritten, ['.codex/auth.json'], label);
    assert.equal(result.removed, true, label);
    assert.equal(fs.existsSync(home.path), false, label);
  }
  sameBytes(fs.readFileSync(outsideFile), CODEX_LOGIN, 'the outside file survives');
  assert.equal(templateFingerprint(homes), before);
  const status = await homes.status();
  assert.deepEqual(status.agents.codex.rewritten.files, ['.codex/auth.json'], 'Agent settings can show "login rewritten by a run"');
  assert.deepEqual(fs.readdirSync(homes.homes), []);
});

test('Close: a home with read-only folders, an outside link, a FIFO and 5,000 files is removed; a second close does not throw', async t => {
  const {make, dir} = await fixture(t);
  const homes = make();
  await homes.check({create: true});
  writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN});
  const outside = path.join(dir, 'outside-folder');
  fs.mkdirSync(outside);
  fs.writeFileSync(path.join(outside, 'keep.txt'), 'keep');
  const home = await homes.open('codex');
  const many = path.join(home.path, 'many');
  fs.mkdirSync(many);
  for (let i = 0; i < 5000; i++) fs.writeFileSync(path.join(many, `f${i}`), 'x');
  fs.mkdirSync(path.join(home.path, 'ro/inner'), {recursive: true});
  fs.writeFileSync(path.join(home.path, 'ro/inner/file'), 'x');
  fs.chmodSync(path.join(home.path, 'ro/inner'), 0o500);
  fs.chmodSync(path.join(home.path, 'ro'), 0o500);
  fs.chmodSync(path.join(home.path, '.codex'), 0o500);
  fs.symlinkSync(outside, path.join(home.path, 'outside-link'));
  assert.equal(spawnSync('/usr/bin/mkfifo', [path.join(home.path, 'pipe')]).status, 0, 'mkfifo');
  // The read-only folders force the repair step; it must not follow the link and change the outside folder's modes.
  fs.chmodSync(path.join(outside, 'keep.txt'), 0o400);
  fs.chmodSync(outside, 0o500);
  const first = await home.close();
  assert.equal(modeOf(outside), 0o500, 'the repair did not follow the link');
  assert.equal(modeOf(path.join(outside, 'keep.txt')), 0o400);
  fs.chmodSync(outside, 0o700);
  assert.equal(first.removed, true);
  assert.ok(first.left.entries >= 5000, JSON.stringify(first.left));
  assert.deepEqual(await home.close(), first);
  assert.equal(fs.readFileSync(path.join(outside, 'keep.txt'), 'utf8'), 'keep');
  assert.deepEqual(fs.readdirSync(homes.homes), []);
});

test('Close: a survivor that keeps swapping a folder for an outside link during the confined repair changes nothing outside', {skip: process.platform === 'darwin' ? false : 'on Linux removal waits until the recorded group has ended, so nothing races'}, async t => {
  const {make, dir} = await fixture(t);
  const homes = make();
  await homes.check({create: true});
  writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN});
  const outside = path.join(dir, 'outside-race');
  fs.mkdirSync(outside);
  fs.writeFileSync(path.join(outside, 'keep.txt'), 'keep');
  fs.chmodSync(path.join(outside, 'keep.txt'), 0o400);
  fs.chmodSync(outside, 0o500);
  const home = await homes.open('codex');
  // Read-only folders make the first removal fail, so the flag and permission repair runs while the survivor races.
  for (let i = 0; i < 5; i++) {
    fs.mkdirSync(path.join(home.path, `ro${i}/inner`), {recursive: true});
    fs.writeFileSync(path.join(home.path, `ro${i}/inner/file`), 'x');
    fs.chmodSync(path.join(home.path, `ro${i}/inner`), 0o500);
    fs.chmodSync(path.join(home.path, `ro${i}`), 0o500);
  }
  // Not recorded in the owner record, as a process that left ALA's group; its working folder follows the rename.
  const survivor = spawn(process.execPath, ['-e', `const fs = require('node:fs');
process.stdout.write('ready\\n');
for (;;) {
  try { fs.rmSync('x', {recursive: true, force: true}); fs.mkdirSync('x'); fs.writeFileSync('x/f', 'x'); } catch {}
  try { fs.rmSync('x', {recursive: true, force: true}); fs.symlinkSync(${JSON.stringify(outside)}, 'x'); } catch {}
}`], {cwd: home.path, detached: true, stdio: ['ignore', 'pipe', 'ignore']});
  const exited = new Promise(resolve => survivor.on('exit', resolve));
  t.after(() => {
    try {
      process.kill(-survivor.pid, 'SIGKILL');
    } catch {}
    return exited;
  });
  await new Promise(resolve => survivor.stdout.once('data', resolve));
  await new Promise(resolve => setTimeout(resolve, 100));
  const result = await home.close();
  process.kill(-survivor.pid, 'SIGKILL');
  await exited;
  assert.equal(modeOf(outside), 0o500, 'the outside folder keeps its mode');
  assert.equal(modeOf(path.join(outside, 'keep.txt')), 0o400);
  sameBytes(fs.readFileSync(path.join(outside, 'keep.txt')), 'keep', 'the outside file keeps its bytes');
  assert.deepEqual(fs.readdirSync(outside), ['keep.txt']);
  if (!result.removed) assert.deepEqual((await homes.sweep()).removed.length, 1, 'a home left by the race is removed by the next sweep');
  assert.deepEqual(fs.readdirSync(homes.homes), []);
  t.diagnostic(`close() during the race: removed ${result.removed}`);
});

test('Close [delta 2]: .codex replaced by a link to an outside folder with identical bytes: outside login survives and counts as rewritten', async t => {
  const {make, dir} = await fixture(t);
  const homes = make();
  await homes.check({create: true});
  writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN});
  const real = path.join(dir, 'real-codex');
  fs.mkdirSync(real, {mode: 0o700});
  fs.writeFileSync(path.join(real, 'auth.json'), CODEX_LOGIN, {mode: 0o600});
  const inode = fs.statSync(path.join(real, 'auth.json')).ino;
  const home = await homes.open('codex');
  fs.rmSync(path.join(home.path, '.codex'), {recursive: true});
  fs.symlinkSync(real, path.join(home.path, '.codex'));
  const result = await home.close();
  assert.deepEqual(result.rewritten, ['.codex/auth.json']);
  assert.equal(result.removed, true);
  assert.equal(fs.statSync(path.join(real, 'auth.json')).ino, inode);
  sameBytes(fs.readFileSync(path.join(real, 'auth.json')), CODEX_LOGIN, 'the real login is unchanged');
});

test('Identity: a home replaced by a link before close() removes nothing in the target and reports removed: false', async t => {
  const {make, dir} = await fixture(t);
  const homes = make();
  await homes.check({create: true});
  writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN});
  const target = path.join(dir, 'link-target');
  fs.mkdirSync(path.join(target, '.codex'), {recursive: true});
  fs.writeFileSync(path.join(target, '.codex/auth.json'), CODEX_LOGIN);
  fs.writeFileSync(path.join(target, 'other.txt'), 'other');
  const home = await homes.open('codex');
  fs.renameSync(home.path, path.join(dir, 'moved-home'));
  fs.symlinkSync(target, home.path);
  const result = await home.close();
  assert.equal(result.removed, false);
  assert.equal(result.identity, 'mismatch');
  assert.deepEqual(listTree(target), ['.codex', '.codex/auth.json', 'other.txt']);
  sameBytes(fs.readFileSync(path.join(target, '.codex/auth.json')), CODEX_LOGIN, 'the link target is unchanged');
});

test('Orphan: close() after the home was removed by hand does not throw', async t => {
  const {make} = await fixture(t);
  const homes = make();
  await homes.check({create: true});
  writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN});
  const home = await homes.open('codex');
  fs.rmSync(home.path, {recursive: true});
  assert.deepEqual(await home.close(), {removed: true, identity: 'missing', rewritten: ['.codex/auth.json'], left: {entries: 0, bytes: 0}});
  assert.deepEqual(fs.readdirSync(homes.homes), []);
});

test('Idempotency: 100 open-and-close rounds leave homes/ empty', async t => {
  const {make} = await fixture(t);
  const homes = make();
  await homes.check({create: true});
  writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN});
  for (let i = 0; i < 100; i++) {
    const home = await homes.open('codex');
    assert.equal((await home.close()).removed, true);
  }
  assert.deepEqual(fs.readdirSync(homes.homes), []);
});

// ---------- Error injection: a simulated ALA that attacks its home ----------

const ATTACKS = `import fs from 'node:fs';
import path from 'node:path';
const [mode, home, outside] = process.argv.slice(2);
const login = path.join(home, '.codex/auth.json');
if (mode === 'remove') fs.rmSync(home, {recursive: true});
if (mode === 'mode0000') fs.chmodSync(home, 0o000);
if (mode === 'link') { fs.unlinkSync(login); fs.symlinkSync(outside, login); }
if (mode === 'deep') { let folder = home; for (let i = 0; i < 200; i++) { folder = path.join(folder, 'd'); fs.mkdirSync(folder); } fs.writeFileSync(path.join(folder, 'f'), 'x'); }
if (mode === 'copy') { fs.copyFileSync(login, path.join(home, '.codex/auth.backup.json')); fs.mkdirSync(path.join(home, 'notes')); fs.copyFileSync(login, path.join(home, 'notes/copy.txt')); }
`;

test('Error injection: a simulated ALA that removes, locks, relinks, deepens or copies its home leaves nothing behind', async t => {
  const {make, dir} = await fixture(t);
  const homes = make();
  await homes.check({create: true});
  writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN});
  const attacker = path.join(dir, 'attack.mjs');
  fs.writeFileSync(attacker, ATTACKS);
  const outside = path.join(dir, 'outside.txt');
  fs.writeFileSync(outside, 'outside');
  for (const mode of ['remove', 'mode0000', 'link', 'deep', 'copy']) {
    const home = await homes.open('codex');
    const child = spawn(process.execPath, [attacker, mode, home.path, outside], {detached: true, stdio: 'ignore'});
    home.recordProcess({pid: child.pid});
    assert.equal(JSON.parse(fs.readFileSync(`${home.path}.owner`, 'utf8')).group, child.pid);
    assert.equal(await new Promise(resolve => child.on('exit', resolve)), 0, mode);
    const result = await home.close();
    assert.equal(result.removed, true, `${mode}: ${JSON.stringify(result)}`);
    if (mode !== 'copy' && mode !== 'deep') assert.deepEqual(result.rewritten, ['.codex/auth.json'], mode);
  }
  await homes.sweep();
  assert.deepEqual(fs.readdirSync(homes.homes), []);
  assert.equal(fs.readFileSync(outside, 'utf8'), 'outside');
  assert.equal(grepToken(homes.homes).status, 1, 'the fixture token is in no file under homes/');
});

// ---------- Sweep ----------

test('Sweep: dead owners are removed, live owners and other names are kept, a live ALA group keeps its home', async t => {
  const {make} = await fixture(t);
  const homes = make();
  await homes.check({create: true});
  const dead = deadPid(), live = process.ppid, hex = () => crypto.randomBytes(8).toString('hex');
  const made = name => {
    fs.mkdirSync(name, {mode: 0o700});
    fs.writeFileSync(path.join(name, 'f'), TOKEN);
    return name;
  };
  const deadHome = made(path.join(homes.homes, `home-${dead}-${hex()}`));
  fs.writeFileSync(`${deadHome}.owner`, JSON.stringify({server: dead, ala: null, group: null}));
  const deadGone = made(path.join(homes.homes, `gone-${dead}-${hex()}`));
  const deadLogin = made(path.join(homes.staging, `login-${dead}-${hex()}`));
  const liveHome = made(path.join(homes.homes, `home-${live}-${hex()}`));
  const liveLogin = made(path.join(homes.staging, `login-${live}-${hex()}`));
  const others = [made(path.join(homes.homes, 'notes')), made(path.join(homes.homes, `home-abc-${hex()}`)), made(path.join(homes.homes, `home-${dead}-0123`)), made(path.join(homes.staging, 'login-x'))];
  const group = startGroup();
  t.after(() => group.kill());
  const suffix = `${dead}-${hex()}`;
  const guarded = made(path.join(homes.homes, `home-${suffix}`));
  fs.writeFileSync(`${guarded}.owner`, JSON.stringify({server: dead, ala: group.pid, group: group.pid}));
  const orphanRecord = path.join(homes.homes, `home-${dead}-${hex()}.owner`);
  fs.writeFileSync(orphanRecord, '{}');

  const first = await homes.sweep();
  for (const gone of [deadHome, `${deadHome}.owner`, deadGone, deadLogin, orphanRecord]) assert.equal(fs.existsSync(gone), false, gone);
  for (const kept of [liveHome, liveLogin, ...others, guarded, `${guarded}.owner`]) assert.equal(fs.existsSync(kept), true, kept);
  assert.deepEqual(first.kept.map(entry => entry.name), [`home-${suffix}`]);
  const status = await homes.status();
  assert.ok(status.leftovers.some(entry => entry.name === `home-${suffix}` && entry.reason.includes(String(group.pid))), JSON.stringify(status.leftovers));
  assert.deepEqual((await homes.sweep()).removed, [], 'a second sweep changes nothing');
  await group.kill();
  const third = await homes.sweep();
  assert.deepEqual(third.removed, [`home-${suffix}`]);
  assert.equal(fs.existsSync(`${guarded}.owner`), false);
  for (const kept of [liveHome, liveLogin, ...others]) assert.equal(fs.existsSync(kept), true, kept);
});

test('Sweep [delta 5]: a dead server\'s home holding a user-immutable file is removed', {skip: process.platform === 'darwin' ? false : 'user-immutable flags are a macOS feature'}, async t => {
  const {make} = await fixture(t);
  const homes = make();
  await homes.check({create: true});
  const folder = path.join(homes.homes, `home-${deadPid()}-${crypto.randomBytes(8).toString('hex')}`);
  fs.mkdirSync(path.join(folder, 'locked'), {recursive: true});
  fs.writeFileSync(path.join(folder, 'locked/file'), 'x');
  assert.equal(spawnSync('/usr/bin/chflags', ['uchg', path.join(folder, 'locked/file')]).status, 0);
  assert.equal(spawnSync('/usr/bin/chflags', ['uchg', path.join(folder, 'locked')]).status, 0);
  const result = await homes.sweep();
  assert.deepEqual(result.removed, [path.basename(folder)]);
  assert.equal(fs.existsSync(folder), false);
});

test('Exit: removeOpenSync signals the recorded ALA group and removes its home; a group that survives keeps it', async t => {
  const {make} = await fixture(t);
  const homes = make({groupWait: 800});
  await homes.check({create: true});
  writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN});
  const polite = startGroup(), stubborn = startGroup({ignoreTerm: true});
  t.after(() => Promise.all([polite.kill(), stubborn.kill()]));
  const first = await homes.open('codex'), second = await homes.open('codex');
  first.recordProcess({pid: polite.pid});
  second.recordProcess({pid: stubborn.pid});
  await new Promise(resolve => setTimeout(resolve, 300)); // let the children install their handlers
  homes.removeOpenSync();
  assert.equal(fs.existsSync(first.path), false, 'the home of the group that ended is removed');
  assert.equal(fs.existsSync(second.path), true, 'the home of a live group is left in place');
  const status = await homes.status();
  assert.ok(status.leftovers.some(entry => entry.path === second.path && entry.reason.includes(String(stubborn.pid))));
  await stubborn.kill();
  assert.deepEqual((await homes.sweep()).removed, [path.basename(second.path)]);
  assert.deepEqual(fs.readdirSync(homes.homes), []);
});

test('Concurrency (approximation of the server probe): SIGTERM to a process with three open homes; afterwards homes/ is empty', async t => {
  const {dir, entry, base, make} = await fixture(t);
  const homes = make();
  await homes.check({create: true});
  writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN});
  const driver = path.join(dir, 'driver.mjs');
  fs.writeFileSync(driver, `import {spawn} from 'node:child_process';
import {createAgentHomes, loadALALogins} from ${JSON.stringify(path.join(TOOLS, 'agent-home.mjs'))};
const homes = createAgentHomes({root: ${JSON.stringify(ROOT)}, base: ${JSON.stringify(base)}, logins: () => loadALALogins(${JSON.stringify(entry)}), environment: () => ({})});
process.on('exit', homes.removeOpenSync);
process.on('SIGTERM', () => process.exit(143));
for (let i = 0; i < 3; i++) {
  const home = await homes.open('codex', {purpose: 'audit'});
  const child = spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], {detached: true, stdio: 'ignore'});
  home.recordProcess({pid: child.pid});
  console.log('group ' + child.pid);
}
console.log('ready');
setInterval(() => {}, 1000);
`);
  const server = spawn(process.execPath, [driver], {stdio: ['ignore', 'pipe', 'inherit']});
  let output = '';
  await new Promise(resolve => server.stdout.on('data', chunk => {
    output += chunk;
    if (output.includes('ready')) resolve();
  }));
  const groups = [...output.matchAll(/group (\d+)/g)].map(match => Number(match[1]));
  assert.equal(fs.readdirSync(homes.homes).filter(name => name.startsWith('home-') && !name.endsWith('.owner')).length, 3);
  server.kill('SIGTERM');
  assert.equal(await new Promise(resolve => server.on('exit', code => resolve(code))), 143);
  for (const group of groups) assert.equal(agentHome.groupAlive(group), false, `group ${group} ended`);
  await make().sweep();
  assert.deepEqual(fs.readdirSync(homes.homes), []);
});

test('Concurrency: twelve opens while login adopts a two-file login each hold the complete old or the complete new pair', async t => {
  const {make, dir} = await fixture(t);
  const homes = make();
  await homes.check({create: true});
  const old = {'.claude/.credentials.json': JSON.stringify({claudeAiOauth: {accessToken: 'old'}}), '.claude/.claude.json': JSON.stringify({generation: 'old'})};
  writeTemplate(homes, old);
  const signIn = path.join(dir, 'sign-in-pair.mjs');
  fs.writeFileSync(signIn, `import fs from 'node:fs';
import path from 'node:path';
fs.mkdirSync(process.env.CLAUDE_CONFIG_DIR, {recursive: true});
fs.writeFileSync(path.join(process.env.CLAUDE_CONFIG_DIR, '.credentials.json'), JSON.stringify({claudeAiOauth: {accessToken: 'new'}}));
fs.writeFileSync(path.join(process.env.CLAUDE_CONFIG_DIR, '.claude.json'), JSON.stringify({generation: 'new'}));
`);
  const opens = [];
  const login = homes.login('claude', [process.execPath, signIn], {stdio: 'ignore', env: {PATH: process.env.PATH}});
  for (let i = 0; i < 12; i++) {
    opens.push(homes.open('claude'));
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  const [adopted, ...opened] = await Promise.all([login, ...opens]);
  assert.deepEqual(adopted.adopted.sort(), ['.claude/.claude.json', '.claude/.credentials.json']);
  const generations = new Set();
  for (const home of opened) {
    const credentials = JSON.parse(fs.readFileSync(path.join(home.path, '.claude/.credentials.json'), 'utf8')).claudeAiOauth.accessToken;
    const config = JSON.parse(fs.readFileSync(path.join(home.path, '.claude/.claude.json'), 'utf8')).generation;
    assert.equal(credentials, config, 'a home never mixes the old and the new pair');
    generations.add(config);
    await home.close();
  }
  assert.deepEqual(fs.readdirSync(homes.homes), []);
  assert.deepEqual(fs.readdirSync(homes.staging), []);
  t.diagnostic(`generations seen by the twelve opens: ${[...generations].sort().join(', ')}`);
});

// ---------- Lock ----------

test('Lock: a live holder gives the lock text after the wait; a dead holder\'s lock is taken over', async t => {
  const {make} = await fixture(t);
  const homes = make({lockWait: 300});
  await homes.check({create: true});
  writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN});
  const holder = process.ppid;
  fs.writeFileSync(homes.lockFile, `${holder} 2026-11-11T00:00:00.000Z\n`);
  const started = Date.now();
  await rejectsWith(homes.open('codex'), homeUnavailable(reasons.locked(holder, homes.lockFile)));
  assert.ok(Date.now() - started >= 300, 'the live holder was waited for');
  assert.deepEqual(fs.readdirSync(homes.homes), []);
  fs.writeFileSync(homes.lockFile, `${deadPid()} 2026-11-11T00:00:00.000Z\n`);
  const home = await homes.open('codex');
  assert.equal(fs.existsSync(homes.lockFile), false, 'the lock is released after the copy');
  await home.close();
  assert.deepEqual(fs.readdirSync(homes.base).sort(), ['homes', 'staging', 'template']);
});

// ---------- ALA without the module ----------

test('ALA without the module: a missing module, one that throws on import, or one without the functions gives the exact text', async t => {
  const {make, ala} = await fixture(t);
  const expected = homeUnavailable(reasons.noModule());
  const module = path.join(ala, 'src/coding-agents/logins.mjs');
  fs.writeFileSync(module, 'throw new Error("broken login module");\n');
  await rejectsWith(make().open('codex'), expected);
  fs.writeFileSync(module, 'export const homeEnvironment = () => ({});\n');
  await rejectsWith(make().open('codex'), expected);
  fs.unlinkSync(module);
  const homes = make();
  await rejectsWith(homes.open('codex'), expected);
  await assert.rejects(homes.open('codex'), {kind: 'module'});
  const status = await homes.status();
  assert.equal(status.ready, false);
  assert.equal(status.error, expected);
});

// ---------- Parity with a real ALA ----------

test('Parity: the fixture\'s login-file lists for Codex and Claude equal the real module\'s, and every real list lies inside the home', {skip: process.env.ALA_CLI ? false : 'ALA_CLI is not set'}, async t => {
  const {entry} = await fixture(t);
  let real = path.resolve(process.env.ALA_CLI);
  if (fs.statSync(real).isDirectory()) real = path.join(real, 'bin/ala.mjs');
  const [fake, actual] = await Promise.all([loadALALogins(entry), loadALALogins(real)]);
  const home = path.join(TEMPORARY, 'agent-home-parity-never-created');
  for (const agent of ['codex', 'claude']) {
    assert.deepEqual(fake.codingAgentLoginFiles(agent, {env: fake.homeEnvironment({}, home)}), actual.codingAgentLoginFiles(agent, {env: actual.homeEnvironment({}, home)}), agent);
  }
  const names = Object.keys(await actual.detectCodingAgentLogins({env: actual.homeEnvironment({}, home)}));
  assert.ok(names.includes('codex') && names.includes('claude'), names.join(', '));
  for (const agent of names) {
    for (const file of actual.codingAgentLoginFiles(agent, {env: actual.homeEnvironment({}, home)})) {
      assert.ok(file.startsWith(home + path.sep), `${agent}: ${file}`);
    }
  }
  t.diagnostic(`real ALA agents: ${names.join(', ')}`);
});

// ---------- The operator script ----------

const SIGN_IN = `import fs from 'node:fs';
import path from 'node:path';
const [agent, mode, record] = process.argv.slice(2);
fs.writeFileSync(record, JSON.stringify({HOME: process.env.HOME, CODEX_HOME: process.env.CODEX_HOME, CLAUDE_CONFIG_DIR: process.env.CLAUDE_CONFIG_DIR, cwd: process.cwd(), ploinky: Object.keys(process.env).some(k => k.startsWith('PLOINKY_'))}));
if (mode === 'exit3') process.exit(3);
if (mode === 'nofile') process.exit(0);
const token = process.env.FAKE_SIGN_IN_TOKEN + '-' + mode;
if (agent === 'codex') {
  const folder = process.env.CODEX_HOME;
  fs.mkdirSync(path.join(folder, 'sessions'), {recursive: true});
  fs.writeFileSync(path.join(folder, 'auth.json'), JSON.stringify({tokens: {access_token: token}}));
  fs.writeFileSync(path.join(folder, 'config.toml'), 'model = "planted"\\n');
  fs.writeFileSync(path.join(folder, 'sessions/session.jsonl'), '{}\\n');
  fs.symlinkSync('/', path.join(folder, 'root-link'));
  fs.mkdirSync(path.join(process.env.HOME, '.local/bin'), {recursive: true});
  fs.writeFileSync(path.join(process.env.HOME, '.local/bin/codex'), '#!/bin/sh\\n');
}
if (agent === 'claude') {
  fs.mkdirSync(process.env.CLAUDE_CONFIG_DIR, {recursive: true});
  fs.writeFileSync(path.join(process.env.CLAUDE_CONFIG_DIR, '.credentials.json'), JSON.stringify({claudeAiOauth: {accessToken: token}}));
  fs.writeFileSync(path.join(process.env.CLAUDE_CONFIG_DIR, '.claude.json'), JSON.stringify({numStartups: 1}));
  fs.writeFileSync(path.join(process.env.CLAUDE_CONFIG_DIR, 'settings.json'), '{}');
}
`;

async function scriptFixture(t) {
  const context = await fixture(t);
  const fakeHome = path.join(context.dir, 'home');
  fs.mkdirSync(fakeHome);
  const signIn = path.join(context.dir, 'sign-in.mjs');
  fs.writeFileSync(signIn, SIGN_IN);
  const record = path.join(context.dir, 'sign-in-record.json');
  // An explicit environment: nothing from the runner's shell (credential variables, ALA_CLI, PLOINKY_*) leaks in.
  const env = {PATH: process.env.PATH, HOME: fakeHome, AUDIT_AGENT_HOME: context.base, ALA_CLI: context.entry, FAKE_SIGN_IN_TOKEN: TOKEN, PLOINKY_ROUTER: 'x'};
  const run = (args, extra = {}) => new Promise(resolve => {
    const child = spawn(process.execPath, [SCRIPT, ...args], {cwd: ROOT, env: {...env, ...extra}, stdio: ['ignore', 'pipe', 'pipe']});
    let stdout = '', stderr = '';
    child.stdout.on('data', chunk => (stdout += chunk));
    child.stderr.on('data', chunk => (stderr += chunk));
    child.on('close', code => resolve({code, stdout, stderr}));
  });
  const login = (agent, mode) => run(['login', agent, '--', process.execPath, signIn, agent, mode, record]);
  const homes = context.make();
  return {...context, fakeHome, signIn, record, env, run, login, homes};
}

test('Script: login adopts only the login file, the sign-in saw the scratch home, and no scratch home is left', async t => {
  const {login, homes, record, fakeHome} = await scriptFixture(t);
  const result = await login('codex', 'first');
  assert.equal(result.code, 0, redact(result.stderr));
  assert.equal(redact(result.stdout), 'Adopted .codex/auth.json into the pilot template of codex.\n');
  assert.equal((result.stdout + result.stderr).includes(TOKEN), false, 'no content is printed');
  assert.deepEqual(listTree(homes.template), ['.codex', '.codex/auth.json']);
  assert.equal(modeOf(path.join(homes.template, '.codex/auth.json')), 0o600);
  sameBytes(JSON.parse(fs.readFileSync(path.join(homes.template, '.codex/auth.json'), 'utf8')).tokens.access_token, `${TOKEN}-first`, 'the adopted login is the first one');
  const seen = JSON.parse(fs.readFileSync(record, 'utf8'));
  assert.match(seen.HOME, new RegExp(`^${homes.staging.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}/login-\\d+-[0-9a-f]{16}$`));
  assert.equal(seen.CODEX_HOME, path.join(seen.HOME, '.codex'));
  assert.equal(seen.cwd, seen.HOME);
  assert.equal(seen.ploinky, false, 'PLOINKY_* variables are removed');
  assert.deepEqual(fs.readdirSync(homes.staging), []);
  assert.deepEqual(fs.readdirSync(fakeHome), [], 'nothing is written in the injected real home');
});

test('Script: a failing sign-in gives exit 5, no login file exit 6, both leave the template unchanged', async t => {
  const {login, homes, run} = await scriptFixture(t);
  assert.equal((await login('codex', 'first')).code, 0);
  const before = templateFingerprint(homes);
  const failed = await login('codex', 'exit3');
  assert.equal(failed.code, 5, redact(failed.stderr));
  assert.match(redact(failed.stderr), /exit code 3\. The template is unchanged\./);
  const empty = await login('codex', 'nofile');
  assert.equal(empty.code, 6, redact(empty.stderr));
  const cannotStart = await run(['login', 'codex', '--', '/nonexistent/sign-in']);
  assert.equal(cannotStart.code, 5, redact(cannotStart.stderr));
  assert.equal(templateFingerprint(homes), before);
  assert.deepEqual(fs.readdirSync(homes.staging), []);
});

test('Script: a second login replaces the first and leaves another agent alone; logout removes only that agent', async t => {
  const {login, homes, run} = await scriptFixture(t);
  assert.equal((await login('claude', 'claude')).code, 0);
  assert.equal((await login('codex', 'first')).code, 0);
  const claude = fs.readFileSync(path.join(homes.template, '.claude/.credentials.json'));
  const second = await login('codex', 'second');
  assert.equal(second.code, 0, redact(second.stderr));
  sameBytes(JSON.parse(fs.readFileSync(path.join(homes.template, '.codex/auth.json'), 'utf8')).tokens.access_token, `${TOKEN}-second`, 'the adopted login is the second one');
  sameBytes(fs.readFileSync(path.join(homes.template, '.claude/.credentials.json')), claude, 'the Claude login is left alone');
  assert.deepEqual(listTree(homes.template), ['.claude', '.claude/.claude.json', '.claude/.credentials.json', '.codex', '.codex/auth.json']);
  const logout = await run(['logout', 'codex']);
  assert.equal(logout.code, 0, redact(logout.stderr));
  assert.match(logout.stdout, /revoke it with the provider/);
  assert.deepEqual(listTree(homes.template), ['.claude', '.claude/.claude.json', '.claude/.credentials.json', '.codex']);
  const again = await run(['logout', 'codex']);
  assert.equal(again.code, 0);
  assert.equal(again.stdout, 'No pilot login for codex was stored.\n');
});

test('Script: usage errors and unknown agents give exit 2; status, sweep and the refusals give their codes', async t => {
  const {run, login, homes, entry, dir} = await scriptFixture(t);
  for (const args of [[], ['bogus'], ['status', '--bogus'], ['login', 'codex'], ['login', 'codex', 'x'], ['login', 'codex', '--'], ['logout'], ['sweep', 'x'], ['login', 'nosuch', '--', 'true']]) {
    const result = await run(args);
    assert.equal(result.code, 2, `${args.join(' ')}: ${result.stderr}`);
  }
  assert.equal((await login('codex', 'first')).code, 0);
  const status = await run(['status']);
  assert.equal(status.code, 0, redact(status.stderr));
  assert.match(status.stdout, /codex: installed \(available\); signed in for the pilot \(login file\)/);
  assert.match(status.stdout, /Leftover homes: 0/);
  const json = JSON.parse((await run(['status', '--json'])).stdout);
  assert.equal(json.agents.codex.source, 'file');
  assert.equal(json.ready, true);
  assert.equal(JSON.stringify(json).includes(TOKEN), false);
  for (let i = 0; i < 2; i++) assert.equal((await run(['sweep'])).code, 0);
  // Exit 4: a relative base, and a refused template.
  const relative = await run(['status'], {AUDIT_AGENT_HOME: 'relative/base'});
  assert.equal(relative.code, 4);
  assert.equal(relative.stderr, homeUnavailable(reasons.relativeBase('relative/base')) + '\n');
  writeTemplate(homes, {'.codex/config.toml': ''});
  assert.equal((await run(['status'])).code, 4);
  const refused = await login('codex', 'first');
  assert.equal(refused.code, 4);
  assert.equal(refused.stderr, homeUnavailable(reasons.foreign('.codex/config.toml', 'file')) + '\n');
  // Exit 3: no ALA, and an ALA without the login module.
  assert.equal((await run(['status'], {ALA_CLI: path.join(dir, 'missing-ala')})).code, 3);
  fs.unlinkSync(path.join(path.dirname(path.dirname(entry)), 'src/coding-agents/logins.mjs'));
  const noModule = await run(['status']);
  assert.equal(noModule.code, 3);
  assert.equal(noModule.stderr, homeUnavailable(reasons.noModule()) + '\n');
});

test('Script: a template locked by a live process gives exit 7 after the wait, and no scratch home is left', async t => {
  const {login, homes} = await scriptFixture(t);
  await homes.check({create: true});
  fs.writeFileSync(homes.lockFile, `${process.pid} 2026-11-11T00:00:00.000Z\n`);
  const result = await login('codex', 'first');
  assert.equal(result.code, 7, redact(result.stderr));
  assert.equal(result.stderr, homeUnavailable(reasons.locked(process.pid, homes.lockFile)) + '\n');
  assert.deepEqual(fs.readdirSync(homes.staging), []);
  assert.equal(fs.existsSync(path.join(homes.template, '.codex')), false);
});

test('Script: a saved ALA location is used, but never one inside tmp/ of the repository', async t => {
  const {dir, base} = await fixture(t);
  const root = path.join(dir, 'repository');
  const saved = await createFakeALA(path.join(dir, 'saved-ala'), '');
  const settings = path.join(root, 'tmp/readable-pilot/agent-settings.md');
  fs.mkdirSync(path.dirname(settings), {recursive: true});
  const write = location => fs.writeFileSync(settings, `| ALA location | \`${location}\` |\n`);
  const previous = process.env.ALA_CLI;
  delete process.env.ALA_CLI;
  t.after(() => {
    if (previous !== undefined) process.env.ALA_CLI = previous;
  });
  const capture = () => {
    const stream = {text: '', write: chunk => (stream.text += chunk)};
    return stream;
  };
  const env = {AUDIT_AGENT_HOME: base};
  write(saved);
  agentHome.prepareBase({base, root, create: true});
  assert.equal(await setupMain(['status'], {out: capture(), err: capture(), env, root}), 0);
  const planted = await createFakeALA(path.join(root, 'tmp/run-1/ala'), '');
  write(planted);
  const err = capture();
  assert.equal(await setupMain(['status'], {out: capture(), err, env, root}), 3);
  assert.equal(err.text, `This ALA location is not accepted: it lies inside ${path.join(root, 'tmp')}, where runs can write. Use a checkout outside it.\n`);
});

// ---------- Findings of the independent code review (A1 to A9) ----------

const caseInsensitive = dir => process.platform === 'darwin' && fs.existsSync(dir.toUpperCase());

async function waitFor(condition, what, limit = 15000) {
  const deadline = Date.now() + limit;
  while (!condition()) {
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
    await new Promise(resolve => setTimeout(resolve, 20));
  }
}

/** A home under homes/ named for a process that has ended, holding a login copy and a read-only folder (which makes the
 * first removal fail, so the repair steps run). */
function deadHome(homes, {prefix = 'home'} = {}) {
  const folder = path.join(homes.homes, `${prefix}-${deadPid()}-${crypto.randomBytes(8).toString('hex')}`);
  fs.mkdirSync(path.join(folder, '.codex'), {recursive: true, mode: 0o700});
  fs.writeFileSync(path.join(folder, '.codex/auth.json'), CODEX_LOGIN, {mode: 0o600});
  readOnlyInside(folder);
  return folder;
}

function readOnlyInside(folder) {
  fs.mkdirSync(path.join(folder, 'ro/inner'), {recursive: true});
  fs.writeFileSync(path.join(folder, 'ro/inner/file'), 'x');
  fs.chmodSync(path.join(folder, 'ro/inner'), 0o500);
  fs.chmodSync(path.join(folder, 'ro'), 0o500);
}

const goneOf = home => path.join(path.dirname(home), path.basename(home).replace(/^home-/, 'gone-'));

test('Logout [A1]: a template whose .codex is a link to an outside folder is refused with the template text and exit 4; the outside login survives', async t => {
  const {run, homes, dir} = await scriptFixture(t);
  await homes.check({create: true});
  const outside = path.join(dir, 'outside-codex');
  fs.mkdirSync(outside, {mode: 0o700});
  fs.writeFileSync(path.join(outside, 'auth.json'), CODEX_LOGIN, {mode: 0o600});
  fs.writeFileSync(path.join(outside, 'auth.json-shm'), 'shm', {mode: 0o600});
  fs.symlinkSync(outside, path.join(homes.template, '.codex'));
  const expected = homeUnavailable(reasons.foreign('.codex', 'link'));
  const status = await run(['status']);
  assert.equal(status.code, 4, redact(status.stderr));
  const logout = await run(['logout', 'codex']);
  assert.equal(logout.code, 4, redact(logout.stdout + logout.stderr));
  assert.equal(logout.stderr, expected + '\n');
  await rejectsWith(homes.logout('codex'), expected);
  sameBytes(fs.readFileSync(path.join(outside, 'auth.json')), CODEX_LOGIN, 'the outside login survives');
  assert.deepEqual(fs.readdirSync(outside).sort(), ['auth.json', 'auth.json-shm']);
  assert.equal(fs.lstatSync(path.join(homes.template, '.codex')).isSymbolicLink(), true, 'the link itself is left for the user');
});

test('Base [A2]: an explicit base spelled in another letter case than on disk is closed and swept completely', {skip: process.platform === 'darwin' ? false : 'only macOS volumes are case-insensitive here'}, async t => {
  const {dir, entry} = await fixture(t);
  if (!caseInsensitive(dir)) return t.skip('the temporary folder is on a case-sensitive volume');
  const stored = path.join(dir, 'CaseBase');
  const make = base => createAgentHomes({root: ROOT, base, logins: () => loadALALogins(entry), environment: () => ({})});
  const creator = make(stored);
  await creator.check({create: true});
  writeTemplate(creator, {'.codex/auth.json': CODEX_LOGIN});
  const homes = make(path.join(dir, 'casebase'));
  const home = await homes.open('codex');
  const result = await home.close();
  assert.equal(result.removed, true, JSON.stringify(result));
  assert.deepEqual(fs.readdirSync(path.join(stored, 'homes')), [], 'neither the home, its login copy nor its owner record is left');
  const sweep = await homes.sweep();
  assert.deepEqual(sweep.kept, []);
  assert.equal(agentHome.prepareBase({base: path.join(dir, 'casebase'), root: ROOT}).base, stored);
  assert.equal(homes.base, stored, 'the base takes the stored spelling');
  assert.equal(path.dirname(home.path), path.join(stored, 'homes'), 'the home path has the stored spelling');
});

const CONFINED_COMMANDS = ['/bin/rm', '/bin/chmod', '/usr/bin/chflags'];

/** Every recorded step runs `/usr/bin/sandbox-exec -p <CLEANUP_PROFILE> -D TARGET=<folder> <command> ...` for one of
 * `targets`, and every path it names lies inside its TARGET. Returns the steps by target. */
function assertConfined(steps, targets) {
  assert.ok(steps.length > 0, 'steps were recorded');
  const byTarget = new Map(targets.map(target => [target, []]));
  for (const step of steps) {
    const shown = JSON.stringify([step.file, ...step.args.filter(arg => arg !== agentHome.CLEANUP_PROFILE)]);
    assert.equal(step.file, '/usr/bin/sandbox-exec', `unconfined step ${shown}`);
    assert.deepEqual(step.args.slice(0, 3), ['-p', agentHome.CLEANUP_PROFILE, '-D'], shown);
    const target = step.args[3].replace(/^TARGET=/, '');
    assert.ok(byTarget.has(target), `TARGET ${target} is one of ${targets.join(', ')}`);
    assert.ok(CONFINED_COMMANDS.includes(step.args[4]), shown);
    const paths = step.args.slice(5).filter(arg => arg.startsWith('/'));
    assert.ok(paths.length > 0, shown);
    for (const named of paths) assert.ok(named === target || named.startsWith(target + path.sep), `${named} lies inside ${target}`);
    byTarget.get(target).push(step);
  }
  for (const [target, list] of byTarget) assert.ok(list.length > 0, `steps for ${target}`);
  return byTarget;
}

const isBulkRemoval = step => step.args[4] === '/bin/rm' && step.args.includes('-rf');
const unlinks = (step, file) => step.args[4] === '/bin/rm' && step.args.includes('-f') && step.args.includes(file);

test('Removal [A3]: on macOS every mutating step of close(), sweep() and removeOpenSync() runs confined to its folder; the login copy is unlinked first', {skip: process.platform === 'darwin' ? false : 'on Linux the steps run unconfined once the ALA process group has ended'}, async t => {
  const {make} = await fixture(t);
  const homes = make();
  await homes.check({create: true});
  writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN});
  const recorded = [];
  let skipBulk = false;
  t.after(agentHome.interceptRemovalSteps((step, options) => {
    recorded.push(step);
    // A bulk removal that cannot run, to show that the login copy does not depend on it.
    if (skipBulk && isBulkRemoval(step)) return options.sync ? undefined : Promise.resolve();
    return agentHome.runStep(step, options);
  }));

  // close(): a read-only .codex makes the targeted unlink fail once, so the repair runs before it is retried.
  const home = await homes.open('codex');
  readOnlyInside(home.path);
  fs.chmodSync(path.join(home.path, '.codex'), 0o500);
  const gone = goneOf(home.path), copy = path.join(goneOf(home.path), '.codex/auth.json');
  assert.equal((await home.close()).removed, true);
  const closeSteps = assertConfined(recorded.splice(0), [gone]).get(gone);
  const firstUnlink = closeSteps.findIndex(step => unlinks(step, copy)), firstBulk = closeSteps.findIndex(isBulkRemoval);
  assert.ok(firstUnlink === 0 && firstBulk > firstUnlink, 'the targeted unlink of the copy is the first step, before any bulk removal');
  assert.ok(closeSteps.some(step => step.args[4] === '/bin/chmod') && closeSteps.some(step => step.args[4] === '/usr/bin/chflags'), 'the repair ran confined');

  // sweep(): a dead server's home and scratch home.
  const leftHome = deadHome(homes);
  const leftLogin = path.join(homes.staging, `login-${deadPid()}-${crypto.randomBytes(8).toString('hex')}`);
  fs.mkdirSync(leftLogin, {mode: 0o700});
  readOnlyInside(leftLogin);
  const swept = await homes.sweep();
  assert.deepEqual(swept.kept, []);
  assertConfined(recorded.splice(0), [leftHome, leftLogin]);
  assert.equal(fs.existsSync(leftHome) || fs.existsSync(leftLogin), false);

  // removeOpenSync().
  const open = await homes.open('codex');
  readOnlyInside(open.path);
  homes.removeOpenSync();
  assert.equal(fs.existsSync(open.path) || fs.existsSync(goneOf(open.path)), false);
  const exitSteps = assertConfined(recorded.splice(0), [goneOf(open.path)]).get(goneOf(open.path));
  assert.ok(unlinks(exitSteps[0], path.join(goneOf(open.path), '.codex/auth.json')), 'the copy is unlinked first at exit too');

  // When the bulk removal cannot run, the copy is gone all the same, and the folder is left and reported.
  skipBulk = true;
  const stuck = await homes.open('codex');
  fs.chmodSync(path.join(stuck.path, '.codex'), 0o500);
  const result = await stuck.close();
  assert.equal(result.removed, false);
  assert.equal(fs.existsSync(goneOf(stuck.path)), true);
  assert.equal(fs.existsSync(path.join(goneOf(stuck.path), '.codex/auth.json')), false, 'the login copy was unlinked without the bulk removal');
  assertConfined(recorded.splice(0), [goneOf(stuck.path)]);
  skipBulk = false;
  assert.deepEqual((await homes.sweep()).removed, [path.basename(goneOf(stuck.path))]);
  assert.deepEqual(fs.readdirSync(homes.homes), []);
});

test('Removal [A3]: when the removal helpers cannot run, nothing is removed or unlinked by this process; the homes are left and reported', async t => {
  const {make} = await fixture(t);
  const homes = make();
  await homes.check({create: true});
  writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN});
  const attempted = [];
  const restore = agentHome.interceptRemovalSteps(step => {
    attempted.push(step);
  });
  t.after(restore);
  const copyLeft = folder => fs.existsSync(path.join(folder, '.codex/auth.json'));

  const home = await homes.open('codex');
  const result = await home.close();
  assert.equal(result.removed, false);
  assert.equal(fs.existsSync(goneOf(home.path)), true, 'close() left the home');
  // On Linux the copies are unlinked by this process by design; on macOS only a confined helper may do it.
  if (process.platform === 'darwin') assert.equal(copyLeft(goneOf(home.path)), true, 'no in-process unlink on macOS');

  const left = deadHome(homes);
  const swept = await homes.sweep();
  assert.ok(swept.kept.some(entry => entry.name === path.basename(left) && entry.reason === 'it could not be removed'), JSON.stringify(swept));
  assert.equal(copyLeft(left), true);

  const open = await homes.open('codex');
  homes.removeOpenSync();
  const atExit = [open.path, goneOf(open.path)].find(folder => fs.existsSync(folder));
  assert.ok(atExit, 'removeOpenSync() left the home');
  if (process.platform === 'darwin') assert.equal(copyLeft(atExit), true);
  const leftovers = (await homes.status()).leftovers.map(entry => entry.path);
  for (const folder of [goneOf(home.path), left, atExit]) assert.ok(leftovers.includes(folder), `${folder} is reported`);
  assert.ok(attempted.length > 0 && attempted.every(step => process.platform !== 'darwin' || step.file === '/usr/bin/sandbox-exec'));

  restore();
  await homes.sweep();
  assert.deepEqual(fs.readdirSync(homes.homes), []);
});

test('Script [A4]: SIGINT, SIGTERM or SIGHUP during the sign-in ends the sign-in, removes the scratch home and exits 128 + n', async t => {
  const {dir, env, homes} = await scriptFixture(t);
  await homes.check({create: true});
  writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN});
  const before = templateFingerprint(homes);
  const waiting = path.join(dir, 'sign-in-waiting.mjs');
  fs.writeFileSync(waiting, `import fs from 'node:fs';
import path from 'node:path';
fs.mkdirSync(process.env.CODEX_HOME, {recursive: true});
fs.writeFileSync(path.join(process.env.CODEX_HOME, 'auth.json'), JSON.stringify({tokens: {access_token: process.env.FAKE_SIGN_IN_TOKEN + '-waiting'}}));
fs.writeFileSync(process.argv[2], String(process.pid));
setTimeout(() => process.exit(9), 20000);
`);
  for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
    const ready = path.join(dir, `ready-${signal}`);
    const script = spawn(process.execPath, [SCRIPT, 'login', 'codex', '--', process.execPath, waiting, ready], {cwd: ROOT, env, stdio: ['ignore', 'ignore', 'pipe']});
    let stderr = '';
    script.stderr.on('data', chunk => (stderr += chunk));
    // 'exit', not 'close': a sign-in left running would keep the pipes open.
    const exited = new Promise(resolve => script.on('exit', (code, killedBy) => resolve({code, killedBy})));
    await waitFor(() => fs.existsSync(ready) && fs.readFileSync(ready, 'utf8') !== '', 'the sign-in to write its login');
    const signInPid = Number(fs.readFileSync(ready, 'utf8'));
    t.after(() => {
      try {
        process.kill(signInPid, 'SIGKILL');
      } catch {}
    });
    script.kill(signal);
    const {code, killedBy} = await exited;
    assert.equal(code, 128 + os.constants.signals[signal], `${signal}: exit ${code}, signal ${killedBy}: ${redact(stderr)}`);
    assert.equal(redact(stderr), `The sign-in was interrupted by ${signal}. The template is unchanged.\n`);
    assert.equal(agentHome.processAlive(signInPid), false, `${signal}: the sign-in command ended`);
    assert.deepEqual(fs.readdirSync(homes.staging), [], `${signal}: no scratch home is left`);
    assert.equal(templateFingerprint(homes), before);
  }
});

test('Exit [A5]: removeOpenSync() during a close() in flight removes the renamed home and its owner record', async t => {
  const {make} = await fixture(t);
  const homes = make();
  await homes.check({create: true});
  writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN});
  const home = await homes.open('codex');
  // close() renames the home to gone-<suffix> before its first await, as at a process exit during a close.
  const closing = home.close();
  assert.equal(fs.existsSync(goneOf(home.path)), true, 'the home was renamed synchronously');
  homes.removeOpenSync();
  assert.deepEqual(fs.readdirSync(homes.homes), [], 'no gone-* folder and no owner record is left');
  const result = await closing;
  assert.equal(result.removed, true);
});

test('Script [A6]: a login file that cannot be adopted (over 16 MiB) gives exit 6 and leaves the previous login whole', async t => {
  const {dir, run, login, homes} = await scriptFixture(t);
  assert.equal((await login('claude', 'claude')).code, 0);
  const before = templateFingerprint(homes);
  const signIn = path.join(dir, 'sign-in-large.mjs');
  fs.writeFileSync(signIn, `import fs from 'node:fs';
import path from 'node:path';
fs.mkdirSync(process.env.CLAUDE_CONFIG_DIR, {recursive: true});
fs.writeFileSync(path.join(process.env.CLAUDE_CONFIG_DIR, '.credentials.json'), JSON.stringify({claudeAiOauth: {accessToken: process.env.FAKE_SIGN_IN_TOKEN + '-new'}}));
fs.writeFileSync(path.join(process.env.CLAUDE_CONFIG_DIR, '.claude.json'), '');
fs.truncateSync(path.join(process.env.CLAUDE_CONFIG_DIR, '.claude.json'), 16 * 1024 * 1024 + 1);
`);
  const result = await run(['login', 'claude', '--', process.execPath, signIn]);
  assert.equal(result.code, 6, redact(result.stdout + result.stderr));
  assert.equal(result.stderr, 'The sign-in ended, but .claude/.claude.json in the scratch home is larger than 16777216 bytes, so the login is not adopted. The template is unchanged.\n');
  assert.equal(templateFingerprint(homes), before, 'the previous two-file login is unchanged');
  assert.deepEqual(fs.readdirSync(homes.staging), []);
});

test('Login [A6]: detection reports a login but no file can be adopted: noFile (exit 6), and the previous login stays', async t => {
  const {dir} = await fixture(t);
  // A login module whose detection reports a file whatever it holds, as a detection that reads less than 16 MiB could.
  const module = {
    homeEnvironment: (env, home) => ({...env, HOME: home, CODEX_HOME: path.join(home, '.codex')}),
    codingAgentLoginFiles: (name, {env}) => [path.join(env.CODEX_HOME, 'auth.json')],
    detectCodingAgentLogin: async (name, {env}) => ({loggedIn: fs.existsSync(path.join(env.CODEX_HOME, 'auth.json')), file: fs.existsSync(path.join(env.CODEX_HOME, 'auth.json')), env: []}),
    detectCodingAgentLogins: async ({env}) => ({codex: await module.detectCodingAgentLogin('codex', {env})})
  };
  const homes = createAgentHomes({root: ROOT, base: path.join(dir, 'base'), logins: async () => module, environment: () => ({})});
  await homes.check({create: true});
  writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN});
  const before = templateFingerprint(homes);
  const signIn = ['-e', "const fs=require('fs'),p=require('path');fs.mkdirSync(process.env.CODEX_HOME,{recursive:true});const f=p.join(process.env.CODEX_HOME,'auth.json');fs.writeFileSync(f,'');fs.truncateSync(f,16*1024*1024+1);"];
  await assert.rejects(homes.login('codex', [process.execPath, ...signIn], {stdio: 'ignore', env: {PATH: process.env.PATH}}), error => {
    assert.equal(error.kind, 'noFile');
    assert.match(error.message, /larger than 16777216 bytes.*The template is unchanged\.$/);
    return true;
  });
  assert.equal(templateFingerprint(homes), before);
  assert.deepEqual(fs.readdirSync(homes.staging), []);
});

test('Script [A7]: a sign-in that replaces .codex in the scratch home by a link to an outside folder adopts nothing: exit 6', async t => {
  const {dir, run, login, homes} = await scriptFixture(t);
  assert.equal((await login('codex', 'first')).code, 0);
  const before = templateFingerprint(homes);
  const outside = path.join(dir, 'outside-for-adoption');
  const outsideLogin = JSON.stringify({tokens: {access_token: `${TOKEN}-outside`}});
  const signIn = path.join(dir, 'sign-in-link.mjs');
  fs.writeFileSync(signIn, `import fs from 'node:fs';
import path from 'node:path';
const outside = process.argv[2];
fs.mkdirSync(outside, {recursive: true});
fs.writeFileSync(path.join(outside, 'auth.json'), ${JSON.stringify(outsideLogin)});
fs.symlinkSync(outside, process.env.CODEX_HOME);
`);
  const result = await run(['login', 'codex', '--', process.execPath, signIn, outside]);
  assert.equal(result.code, 6, redact(result.stdout + result.stderr));
  assert.equal(result.stderr, 'The sign-in ended, but .codex/auth.json in the scratch home lies below .codex, which is not a real folder, so it is not adopted. The template is unchanged.\n');
  assert.equal(templateFingerprint(homes), before);
  sameBytes(fs.readFileSync(path.join(outside, 'auth.json')), outsideLogin, 'the outside file is untouched');
  assert.deepEqual(fs.readdirSync(homes.staging), []);
});

test('Script [A4]: sweep refuses a base inside the repository, a link and an open folder with exit 4 and the texts of the other commands', async t => {
  const {dir, run} = await scriptFixture(t);
  const repository = fs.realpathSync.native(ROOT);
  const inside = path.join(repository, 'tmp', `agent-home-sweep-never-created-${process.pid}`);
  const insideRun = await run(['sweep'], {AUDIT_AGENT_HOME: inside});
  assert.equal(insideRun.code, 4, insideRun.stdout + insideRun.stderr);
  assert.equal(insideRun.stderr, homeUnavailable(reasons.insideRepository(inside, repository)) + '\n');
  assert.equal(insideRun.stdout, '');
  assert.equal(fs.existsSync(inside), false, 'a refused base is never created');
  // A link to a private folder outside the repository that holds a leftover-looking home: refused, nothing removed.
  const target = path.join(dir, 'link-target');
  fs.mkdirSync(path.join(target, 'homes', 'home-999999-0123456789abcdef'), {recursive: true, mode: 0o700});
  fs.chmodSync(target, 0o700);
  const link = path.join(dir, 'base-link');
  fs.symlinkSync(target, link);
  const linked = await run(['sweep'], {AUDIT_AGENT_HOME: link});
  assert.equal(linked.code, 4, linked.stdout + linked.stderr);
  assert.equal(linked.stderr, homeUnavailable(reasons.notPrivate(link, 'a link')) + '\n');
  assert.deepEqual(fs.readdirSync(path.join(target, 'homes')), ['home-999999-0123456789abcdef'], 'nothing behind the link was swept');
  // A folder with access for others.
  const open = path.join(dir, 'open-base');
  fs.mkdirSync(open);
  fs.chmodSync(open, 0o755);
  const opened = await run(['sweep'], {AUDIT_AGENT_HOME: open});
  assert.equal(opened.code, 4);
  assert.equal(opened.stderr, homeUnavailable(reasons.notPrivate(open, 'mode 0755')) + '\n');
  // A relative base keeps its refusal; a base that does not exist yet has nothing to sweep.
  assert.equal((await run(['sweep'], {AUDIT_AGENT_HOME: 'relative/base'})).code, 4);
  const absent = await run(['sweep'], {AUDIT_AGENT_HOME: path.join(dir, 'not-yet')});
  assert.equal(absent.code, 0, absent.stderr);
  assert.equal(absent.stdout, 'Removed 0 leftover homes.\n');
  assert.equal(fs.existsSync(path.join(dir, 'not-yet')), false, 'the sweep creates no base');
  // The default base on a fresh machine: ~/.lean-documents (here of a fixture home) does not exist yet.
  const freshHome = path.join(dir, 'fresh-home');
  fs.mkdirSync(freshHome);
  const out = {text: '', write: chunk => (out.text += chunk)}, err = {text: '', write: chunk => (err.text += chunk)};
  assert.equal(await setupMain(['sweep'], {out, err, env: {}, home: freshHome, root: ROOT}), 0, err.text);
  assert.equal(out.text, 'Removed 0 leftover homes.\n');
  assert.equal(fs.existsSync(path.join(freshHome, '.lean-documents')), false, 'the sweep creates no default base');
});

test('Script [A8]: sweep needs no ALA, and the script locates ALA from the environment it was given', async t => {
  const {dir, run, homes, base, entry} = await scriptFixture(t);
  await homes.check({create: true});
  const left = deadHome(homes);
  const missing = path.join(dir, 'missing-ala');
  const swept = await run(['sweep'], {ALA_CLI: missing});
  assert.equal(swept.code, 0, redact(swept.stderr));
  assert.equal(swept.stdout, 'Removed 1 leftover home.\n');
  assert.equal(fs.existsSync(left), false);
  assert.equal((await run(['status'], {ALA_CLI: missing})).code, 3, 'status still needs ALA');

  const previous = Object.prototype.hasOwnProperty.call(process.env, 'ALA_CLI') ? process.env.ALA_CLI : undefined;
  process.env.ALA_CLI = missing;
  t.after(() => {
    if (previous === undefined) delete process.env.ALA_CLI;
    else process.env.ALA_CLI = previous;
  });
  const capture = () => {
    const stream = {text: '', write: chunk => (stream.text += chunk)};
    return stream;
  };
  const err = capture();
  assert.equal(await setupMain(['status'], {out: capture(), err, env: {AUDIT_AGENT_HOME: base, ALA_CLI: entry, PATH: process.env.PATH}, home: path.join(dir, 'home'), root: ROOT}), 0, err.text);
  assert.equal(process.env.ALA_CLI, missing, 'process.env is restored');
});

test('Owner record [A9]: recordProcess refuses a pid that does not lead its own process group', async t => {
  const {make} = await fixture(t);
  const homes = make();
  await homes.check({create: true});
  writeTemplate(homes, {'.codex/auth.json': CODEX_LOGIN});
  const home = await homes.open('codex');
  const attached = spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], {stdio: 'ignore'});
  const attachedExit = new Promise(resolve => attached.on('exit', resolve));
  t.after(() => {
    attached.kill('SIGKILL');
    return attachedExit;
  });
  assert.throws(() => home.recordProcess({pid: attached.pid}), TypeError);
  assert.notEqual(agentHome.processGroupOf(attached.pid), attached.pid, 'a child spawned without detached shares the parent\'s group');
  assert.equal(JSON.parse(fs.readFileSync(`${home.path}.owner`, 'utf8')).group, null, 'the record is unchanged');
  const detached = startGroup();
  t.after(() => detached.kill());
  home.recordProcess({pid: detached.pid});
  assert.equal(JSON.parse(fs.readFileSync(`${home.path}.owner`, 'utf8')).group, detached.pid);
  await detached.kill();
  assert.equal((await home.close()).removed, true);
});
