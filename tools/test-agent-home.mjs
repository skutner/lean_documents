/** Checks of the agent home setting: tools/agent-home.mjs and tools/set-home.mjs (`npm run set-home`).
 *
 * No coding agent and no model is involved. Every call gets a fixture home in a fresh folder under the host temporary
 * folder, never the real home: in-process calls get it as `home`, spawned scripts as HOME. Fixtures are removed
 * afterwards. Parity with a real ALA uses ALA_CLI when set, else the copy `npm install` puts under node_modules. */
import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
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
import {main} from './set-home.mjs';

const {AgentHomeError, OWN_HOME, homeUnavailable, loadALALogins, noLogin, readAgentHome, reasons, realLocation, settingFile, writeAgentHome} = agentHome;
const TOOLS = path.dirname(fileURLToPath(import.meta.url));
const ROOT = fs.realpathSync.native(path.resolve(TOOLS, '..'));
const SCRIPT = path.join(TOOLS, 'set-home.mjs');
const TEMPORARY = fs.realpathSync.native(os.tmpdir());
const DEFAULT_LINE = 'Agent home: your own home (default). ALA starts coding agents without --home, with your own login and agent state.';
const modeOf = file => fs.statSync(file).mode & 0o777;
const sha = file => (fs.existsSync(file) ? crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex') : 'absent');
const tree = folder => fs.readdirSync(folder, {recursive: true}).map(String).sort();

function removeFixture(dir) {
  // Only a folder this suite made directly in the host temporary folder.
  if (path.dirname(dir) !== TEMPORARY || !path.basename(dir).startsWith('agent-home-test-')) return;
  spawnSync('/bin/chmod', ['-R', 'u+rwx', dir]);
  fs.rmSync(dir, {recursive: true, force: true});
}

/** A fixture home (never the real one) and `run(argv)`, which calls set-home's main with it and captures its output. */
async function fixture(t) {
  const dir = fs.realpathSync.native(await fsp.mkdtemp(path.join(TEMPORARY, 'agent-home-test-')));
  t.after(() => removeFixture(dir));
  const home = path.join(dir, 'home');
  fs.mkdirSync(home, {mode: 0o700});
  const file = settingFile(home);
  const run = async argv => {
    let out = '', err = '';
    const code = await main(argv, {out: {write: text => (out += text)}, err: {write: text => (err += text)}, home});
    return {code, out, err};
  };
  return {dir, home, file, run};
}

/** The script itself, with an explicit environment: only PATH, the fixture HOME and what a test adds. */
function spawnScript(args, env) {
  assert.ok(env.HOME.startsWith(TEMPORARY + path.sep), 'a spawned set-home gets a fixture HOME');
  return new Promise(resolve => {
    const child = spawn(process.execPath, [SCRIPT, ...args], {cwd: ROOT, env: {PATH: process.env.PATH, ...env}, stdio: ['ignore', 'pipe', 'pipe']});
    let out = '', err = '';
    child.stdout.on('data', chunk => (out += chunk));
    child.stderr.on('data', chunk => (err += chunk));
    child.on('close', code => resolve({code, out, err}));
  });
}

// ---------- Show, set, reset ----------

test('Default: without a setting file set-home shows your own home and creates nothing', async t => {
  const {home, file, run} = await fixture(t);
  const shown = await run([]);
  assert.deepEqual(shown, {code: 0, out: `${DEFAULT_LINE}\nSetting file: ${file} (absent)\n`, err: ''});
  assert.deepEqual(tree(home), [], 'no .lean-documents is created by showing');
  assert.deepEqual(readAgentHome({root: ROOT, home}), {mode: 'own', folder: null, setting: file});
  assert.equal(OWN_HOME, 'your own home (ALA started without --home)');
});

for (const umask of [0o022, 0o077]) {
  test(`Set: the folder is created with mode 0700 and its real path is saved in a 0600 file inside a 0700 folder (umask 0${umask.toString(8)})`, async t => {
    const {dir, home, file, run} = await fixture(t);
    const previous = process.umask(umask);
    try {
      const folder = path.join(dir, 'h');
      const result = await run([folder]);
      assert.equal(result.code, 0, result.err);
      assert.equal(result.err, '');
      assert.ok(result.out.startsWith(`Agent home: ${folder}\nSetting file: ${file}\n`), result.out);
      assert.match(result.out, /All runs share it .* No restart is needed\. .*tools\/README\.md, "Agent home"/);
      assert.equal(modeOf(folder), 0o700);
      assert.deepEqual(JSON.parse(fs.readFileSync(file, 'utf8')), {home: fs.realpathSync.native(folder)});
      assert.equal(modeOf(file), 0o600);
      assert.equal(modeOf(path.dirname(file)), 0o700);
      assert.deepEqual(fs.readdirSync(path.dirname(file)), ['agent-home.json'], 'the write leaves no temporary file');
      assert.deepEqual(readAgentHome({root: ROOT, home}), {mode: 'folder', folder, setting: file});
      // A missing folder below missing parents is created as well.
      const deep = path.join(dir, 'a', 'b', 'c');
      assert.equal((await run([deep])).code, 0);
      assert.equal(modeOf(deep), 0o700);
    } finally {
      process.umask(previous);
    }
  });
}

test('Show and reset: show prints the folder; --reset removes only the setting file; a second reset exits 0', async t => {
  const {dir, home, file, run} = await fixture(t);
  const folder = path.join(dir, 'kept');
  assert.equal((await run([folder])).code, 0);
  fs.writeFileSync(path.join(folder, 'what-a-run-wrote.md'), '# Kept\n');
  const before = tree(home);
  assert.deepEqual(await run([]), {code: 0, out: `Agent home: ${folder}\nSetting file: ${file}\n`, err: ''});
  assert.deepEqual(tree(home), before, 'showing changes nothing');
  const reset = await run(['--reset']);
  assert.deepEqual(reset, {code: 0, out: `${DEFAULT_LINE}\nSetting file: ${file} (absent)\n${folder} was left as it is.\n`, err: ''});
  assert.equal(fs.existsSync(file), false);
  assert.deepEqual(fs.readdirSync(folder), ['what-a-run-wrote.md'], 'the folder and its entries stay');
  assert.deepEqual(readAgentHome({root: ROOT, home}).mode, 'own');
  assert.deepEqual(await run(['--reset']), {code: 0, out: `${DEFAULT_LINE}\nSetting file: ${file} (absent)\n`, err: ''});
  // A FIFO at the setting path (only you could put it there) never blocks: showing refuses it, a reset removes it.
  childProcess.execFileSync('mkfifo', [file]);
  const shown = await run([]);
  assert.equal(shown.code, 1); assert.equal(shown.err, reasons.badSetting(file, 'not a regular file') + '\n');
  assert.deepEqual(await run(['--reset']), {code: 0, out: `${DEFAULT_LINE}\nSetting file: ${file} (absent)\n`, err: ''});
  assert.equal(fs.existsSync(file), false);
});

test('Refusals: exit codes and exact reasons; the setting bytes stay unchanged and nothing is created', async t => {
  const {dir, home, file, run} = await fixture(t);
  const settings = path.dirname(file);
  const repository = realLocation(ROOT);
  const usage = 'Usage: npm run set-home [-- <absolute folder> | -- --reset]\n';
  const insideTmp = path.join(ROOT, 'tmp', `agent-home-refused-${crypto.randomBytes(4).toString('hex')}`);
  const aFile = path.join(dir, 'a-file');
  fs.writeFileSync(aFile, 'not a folder\n');
  const open = path.join(dir, 'open');
  fs.mkdirSync(open);
  fs.chmodSync(open, 0o755);
  const homeLink = path.join(dir, 'home-link');
  fs.symlinkSync(home, homeLink);
  const parent = path.dirname(ROOT);
  const rows = [
    [['rel/home'], 2, reasons.notAbsolute('rel/home') + '\n'],
    [['--bogus'], 2, usage],
    [['--show'], 2, usage],
    [['a', 'b'], 2, usage],
    [['--reset', 'x'], 2, usage],
    [[ROOT], 3, reasons.insideRepository(repository, repository) + '\n'],
    [[insideTmp], 3, reasons.insideRepository(path.join(repository, 'tmp', path.basename(insideTmp)), repository) + '\n'],
    [[home], 3, reasons.containsSetting(home, settings) + '\n'],
    [['/'], 3, reasons.containsSetting('/', settings) + '\n'],
    [[settings], 3, reasons.containsSetting(settings, settings) + '\n'],
    // The setting file itself, its temporary name and anything below it: creating one would leave a folder where the
    // setting belongs.
    [[file], 3, reasons.isSetting(file) + '\n'],
    [[file + '.0a1b2c3d.tmp'], 3, reasons.isSetting(file + '.0a1b2c3d.tmp') + '\n'],
    [[path.join(file, 'below')], 3, reasons.isSetting(path.join(file, 'below')) + '\n'],
    [[homeLink], 3, reasons.containsSetting(home, settings) + '\n'],
    [[parent], 3, reasons.containsRepository(realLocation(parent), repository) + '\n'],
    [[aFile], 3, reasons.notFolder(aFile) + '\n'],
    [[open], 3, reasons.notPrivate(open, 'mode 0755') + '\n']
  ];
  // On a case-insensitive volume (the macOS default) the repository in another letter case is still the repository.
  const swapped = ROOT.replace(/[a-z]/g, c => c.toUpperCase());
  if (swapped !== ROOT && fs.existsSync(swapped)) rows.push([[swapped], 3, reasons.insideRepository(repository, repository) + '\n']);
  else t.diagnostic('the repository lies on a case-sensitive volume; the letter-case row does not apply');
  // Once without a setting file (and without ~/.lean-documents), once with a saved setting.
  for (const saved of [false, true]) {
    if (saved) assert.equal((await run([path.join(dir, 'saved')])).code, 0);
    const homeBefore = tree(home), dirBefore = tree(dir), bytes = sha(file);
    for (const [argv, code, err] of rows) {
      const result = await run(argv);
      assert.deepEqual(result, {code, out: '', err}, argv.join(' '));
      assert.equal(sha(file), bytes, `${argv.join(' ')}: the setting bytes are unchanged`);
      assert.deepEqual(tree(home), homeBefore, `${argv.join(' ')}: nothing is created in the home`);
      assert.deepEqual(tree(dir), dirBefore, `${argv.join(' ')}: nothing is created beside it`);
    }
    assert.equal(fs.existsSync(insideTmp), false, 'the refused folder inside tmp/ was never created');
    assert.equal(modeOf(open), 0o755, 'set-home does not change the mode of an existing folder');
  }
});

test('Refusals: the setting file in another letter case is refused before anything is created, with and without ~/.lean-documents', async t => {
  const {home, file, run} = await fixture(t);
  const settings = path.dirname(file), variant = path.join(settings, 'Agent-Home.JSON'), temporary = path.join(settings, 'AGENT-HOME.json.0a1b2c3d.tmp');
  // Without ~/.lean-documents: refused, and the folder is not created either.
  for (const folder of [variant, temporary]) assert.deepEqual(await run([folder]), {code: 3, out: '', err: reasons.isSetting(folder) + '\n'});
  assert.equal(fs.existsSync(settings), false, 'nothing was created');
  // After a reset: ~/.lean-documents exists and the setting file does not, so the typed name does not exist yet.
  const kept = path.join(home, 'kept-home');
  assert.equal((await run([kept])).code, 0); assert.equal((await run(['--reset'])).code, 0);
  for (const folder of [variant, temporary]) assert.deepEqual(await run([folder]), {code: 3, out: '', err: reasons.isSetting(folder) + '\n'});
  assert.deepEqual(fs.readdirSync(settings), [], 'no folder was left where the setting belongs');
  // The leftover folder of the earlier design, beside the setting file, stays an acceptable agent home.
  const leftover = path.join(settings, 'agent-home');
  assert.equal((await run([leftover])).code, 0); assert.equal(readAgentHome({root: ROOT, home}).folder, realLocation(leftover));
});

test('AUDIT_AGENT_HOME is ignored by set-home (A5): it shows and sets as without it', async t => {
  const {dir, home, file} = await fixture(t);
  const env = {HOME: home, AUDIT_AGENT_HOME: '/x'};
  assert.deepEqual(await spawnScript([], env), {code: 0, out: `${DEFAULT_LINE}\nSetting file: ${file} (absent)\n`, err: ''});
  const folder = path.join(dir, 'spawned');
  const set = await spawnScript([folder], env);
  assert.equal(set.code, 0, set.err);
  assert.equal(readAgentHome({root: ROOT, home}).folder, folder);
});

// ---------- Probes ----------

test('Boundary: spaces and non-ASCII are accepted; a 300-character component, a line break and an empty argument are refused', async t => {
  const {dir, file, run} = await fixture(t);
  const spaced = path.join(dir, 'pilot home ș €');
  assert.equal((await run([spaced])).code, 0);
  assert.equal(JSON.parse(fs.readFileSync(file, 'utf8')).home, spaced);
  const bytes = sha(file);
  const long = await run([path.join(dir, 'a'.repeat(300))]);
  assert.notEqual(long.code, 0);
  assert.match(long.err, /ENAMETOOLONG|name too long/i);
  assert.equal(fs.existsSync(path.join(dir, 'a'.repeat(300))), false);
  for (const value of [path.join(dir, 'line\nbreak'), path.join(dir, 'carriage\rreturn'), '']) {
    assert.deepEqual(await run([value]), {code: 2, out: '', err: reasons.notAbsolute(value) + '\n'}, JSON.stringify(value));
  }
  assert.equal(sha(file), bytes, 'the setting is unchanged by every refusal');
});

test('Idempotency: the same folder twice gives the same bytes; a link to an acceptable folder saves the real folder', async t => {
  const {dir, file, run} = await fixture(t);
  const folder = path.join(dir, 'same');
  assert.equal((await run([folder])).code, 0);
  const first = fs.readFileSync(file);
  assert.equal((await run([folder])).code, 0, 'an existing private folder is accepted');
  assert.deepEqual(fs.readFileSync(file), first);
  const link = path.join(dir, 'link-to-same');
  fs.symlinkSync(folder, link);
  const linked = await run([link]);
  assert.equal(linked.code, 0, linked.err);
  assert.ok(linked.out.startsWith(`Agent home: ${folder}\n`), 'the real path is printed');
  assert.deepEqual(fs.readFileSync(file), first, 'and saved');
});

test('Concurrency: two set-home processes with different folders at once both exit 0, and the file holds one complete value', async t => {
  const {dir, home, file} = await fixture(t);
  const folders = [path.join(dir, 'first'), path.join(dir, 'second')];
  for (let round = 0; round < 3; round++) {
    const results = await Promise.all(folders.map(folder => spawnScript([folder], {HOME: home})));
    for (const result of results) assert.equal(result.code, 0, result.err);
    const value = JSON.parse(fs.readFileSync(file, 'utf8'));
    assert.ok(folders.includes(value.home), value.home);
    assert.deepEqual(fs.readdirSync(path.dirname(file)), ['agent-home.json'], 'no temporary file is left');
  }
});

// ---------- Reading the setting ----------

test('Read: a setting that cannot be used is an error with status 503, never the default; a changed folder mode is accepted', async t => {
  const {dir, home, file, run} = await fixture(t);
  const folder = path.join(dir, 'set');
  assert.equal((await run([folder])).code, 0);
  const refused = (expected, label) => {
    assert.throws(() => readAgentHome({root: ROOT, home}), error => {
      assert.ok(error instanceof AgentHomeError, label);
      assert.equal(error.status, 503, label);
      assert.equal(error.reason, expected, label);
      assert.equal(error.message, homeUnavailable(expected), label);
      return true;
    }, label);
  };
  // A run may change the mode of its home; read time does not require privacy (A5).
  fs.chmodSync(folder, 0o755);
  assert.equal(readAgentHome({root: ROOT, home}).folder, folder);
  const write = text => {
    fs.rmSync(file, {recursive: true, force: true});
    fs.writeFileSync(file, text);
  };
  write('not json');
  refused(reasons.badSetting(file, 'not JSON'), 'not json');
  for (const value of [{home: 5}, [folder], {home: folder, extra: 1}, {path: folder}, {home: folder, pad: 'x'.repeat(5000)}, null]) {
    write(JSON.stringify(value));
    refused(reasons.badSetting(file, 'not {"home": "<absolute folder>"}'), JSON.stringify(value).slice(0, 60));
  }
  write(JSON.stringify({home: 'relative'}));
  refused(reasons.notAbsolute('relative'), 'relative');
  write(JSON.stringify({home: home}));
  refused(reasons.containsSetting(home, path.dirname(file)), 'the home itself');
  write(JSON.stringify({home: path.join(ROOT, 'tmp')}));
  refused(reasons.insideRepository(path.join(ROOT, 'tmp'), ROOT), 'tmp/ of the repository');
  write(JSON.stringify({home: path.dirname(ROOT)}));
  refused(reasons.containsRepository(path.dirname(ROOT), ROOT), 'a folder that contains the repository');
  // Replaced by a link to a valid setting file: never followed.
  const other = path.join(dir, 'other-setting.json');
  fs.writeFileSync(other, JSON.stringify({home: folder}));
  fs.rmSync(file);
  fs.symlinkSync(other, file);
  refused(reasons.badSetting(file, 'a link'), 'a link');
  fs.rmSync(file);
  fs.mkdirSync(file);
  refused(reasons.badSetting(file, 'not a regular file'), 'a folder');
  fs.rmSync(file, {recursive: true});
  assert.equal(spawnSync('/usr/bin/mkfifo', [file]).status, 0);
  refused(reasons.badSetting(file, 'not a regular file'), 'a FIFO');
  fs.rmSync(file);
  // The folder removed, or replaced by a file.
  write(JSON.stringify({home: folder}));
  fs.rmSync(folder, {recursive: true});
  refused(reasons.missing(folder), 'a missing folder');
  fs.writeFileSync(folder, 'a file now\n');
  refused(reasons.notFolder(folder), 'a file');
  // set-home's show form reports the same reason with exit 1; --reset still removes the setting.
  assert.deepEqual(await run([]), {code: 1, out: '', err: reasons.notFolder(folder) + '\n'});
  assert.equal((await run(['--reset'])).code, 0);
  assert.equal(readAgentHome({root: ROOT, home}).mode, 'own');
});

test('writeAgentHome(null) deletes nothing but the setting file, and the server modules never import a writer', async t => {
  const {dir, home, file} = await fixture(t);
  const folder = path.join(dir, 'w');
  assert.deepEqual(writeAgentHome(folder, {root: ROOT, home}), {mode: 'folder', folder, setting: file});
  assert.deepEqual(writeAgentHome(null, {root: ROOT, home}), {mode: 'own', folder: null, setting: file, previous: folder});
  assert.deepEqual(fs.readdirSync(path.dirname(file)), []);
  assert.equal(fs.statSync(folder).isDirectory(), true);
  for (const name of ['audit-ui.mjs', 'ala.mjs', 'ala-catalog-worker.mjs']) {
    assert.doesNotMatch(fs.readFileSync(path.join(TOOLS, name), 'utf8'), /writeAgentHome/, `${name} never writes the setting`);
  }
});

// ---------- Logins ----------

test('Logins: a missing login module, one that throws on import, or one without the functions gives the exact text', async t => {
  const {dir} = await fixture(t);
  const ala = await createFakeALA(path.join(dir, 'ala'), ''), entry = path.join(ala, 'bin/ala.mjs');
  const logins = await loadALALogins(entry);
  assert.deepEqual(Object.keys(logins).sort(), ['codingAgentLoginFiles', 'detectCodingAgentLogin', 'detectCodingAgentLogins', 'homeEnvironment']);
  const expected = {message: homeUnavailable(reasons.noModule()), kind: 'module', status: 503};
  const module = path.join(ala, 'src/coding-agents/logins.mjs');
  // Each variant is a new file name, because an imported module stays cached.
  for (const [name, text] of [['throws', 'throw new Error("broken login module");\n'], ['partial', 'export const homeEnvironment = () => ({});\n']]) {
    const copy = await createFakeALA(path.join(dir, `ala-${name}`), '');
    fs.writeFileSync(path.join(copy, 'src/coding-agents/logins.mjs'), text);
    await assert.rejects(loadALALogins(path.join(copy, 'bin/ala.mjs')), expected, name);
  }
  fs.unlinkSync(module);
  const missing = await createFakeALA(path.join(dir, 'ala-missing'), '');
  fs.unlinkSync(path.join(missing, 'src/coding-agents/logins.mjs'));
  await assert.rejects(loadALALogins(path.join(missing, 'bin/ala.mjs')), expected, 'missing');
});

test('Logins: the noLogin texts name the mode and the files looked for', async t => {
  const {dir} = await fixture(t);
  const logins = await loadALALogins(path.join(await createFakeALA(path.join(dir, 'ala'), ''), 'bin/ala.mjs'));
  const env = {PATH: '/bin', HOME: path.join(dir, 'own')}, folder = path.join(dir, 'set');
  const files = logins.codingAgentLoginFiles('codex', {env});
  assert.deepEqual(files, [path.join(dir, 'own', '.codex', 'auth.json')]);
  assert.equal(noLogin('codex', {mode: 'own', folder: null}, files), `No login found for codex. ALA looked for ${files[0]} and for a credential variable in the server's environment. Sign in with codex's own command, then try again. A login kept only in the macOS Keychain is not visible inside ALA's sandbox. See tools/README.md, "Agent home".`);
  assert.equal(noLogin('claude', {mode: 'folder', folder}, ['/f/a', '/f/b']), `No login found for claude in the agent home ${folder}. ALA looked for /f/a, /f/b and for a credential variable in the server's environment. Sign in inside that folder: see tools/README.md, "Agent home".`);
});

// ---------- Parity with a real ALA ----------

const REAL_ALA = process.env.ALA_CLI || (fs.existsSync(path.join(ROOT, 'node_modules/advanced-language-agent/bin/ala.mjs')) ? path.join(ROOT, 'node_modules/advanced-language-agent') : null);
test('Parity: the fixture\'s login-file lists for Codex and Claude equal the real module\'s, and every real list lies inside the home', {skip: REAL_ALA ? false : 'neither ALA_CLI is set nor node_modules/advanced-language-agent installed'}, async t => {
  const {dir} = await fixture(t);
  const entry = path.join(await createFakeALA(path.join(dir, 'ala'), ''), 'bin/ala.mjs');
  let real = path.resolve(REAL_ALA);
  if (fs.statSync(real).isDirectory()) real = path.join(real, 'bin/ala.mjs');
  const [fake, actual] = await Promise.all([loadALALogins(entry), loadALALogins(real)]);
  // Only a home that is never created: no real login is read.
  const home = path.join(dir, 'parity-never-created');
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
  // The server reads {loggedIn, file, env: [names]} from both modules; a dummy variable value stands for a credential.
  for (const module of [fake, actual]) {
    assert.deepEqual(await module.detectCodingAgentLogin('codex', {env: module.homeEnvironment({}, home)}), {loggedIn: false, file: false, env: []});
    assert.deepEqual(await module.detectCodingAgentLogin('codex', {env: {...module.homeEnvironment({}, home), OPENAI_API_KEY: 'dummy'}}), {loggedIn: true, file: false, env: ['OPENAI_API_KEY']});
  }
  assert.equal(fs.existsSync(home), false);
  t.diagnostic(`real ALA ${real}; agents: ${names.join(', ')}`);
});
