/** Checks of tools/remote-verify.mjs with a local transport only: the "other host" is this machine, reached through
 * /bin/sh, so the same remote scripts run without ssh or network. Nothing is run against a real host. Each test works
 * on a fixture repository (a fresh Git folder holding copies of the scripts) under tmp/remote-verify-tests/, kept for
 * inspection. */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL, fileURLToPath} from 'node:url';

const execFileAsync = promisify(execFile);
// The local transport inherits this process's environment, which a real remote shell would not. An ambient ALA_CLI
// (set, for example, when this suite itself runs through remote-verify --ala) must not reach the checks below.
delete process.env.ALA_CLI;
const tools = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(tools, '..');
await fs.mkdir(path.join(repoRoot, 'tmp/remote-verify-tests'), {recursive: true});
const suite = await fs.mkdtemp(path.join(repoRoot, 'tmp/remote-verify-tests/run-'));
console.log(`Retained remote-verify test folders: ${suite}`);
const real = await import('./remote-verify.mjs');

const sha256 = async file => crypto.createHash('sha256').update(await fs.readFile(file)).digest('hex');
const exists = file => fs.access(file).then(() => true, () => false);
const quiet = () => {};

async function gitFolder(dir, files) {
  await fs.mkdir(dir, {recursive: true});
  await execFileAsync('git', ['init', '-q', dir]);
  for (const [name, content] of Object.entries(files)) {
    await fs.mkdir(path.dirname(path.join(dir, name)), {recursive: true});
    await fs.writeFile(path.join(dir, name), content);
  }
}

/** A fixture candidate repository whose copy of remote-verify.mjs freezes that repository, plus a remote area. */
async function fixture(name) {
  const base = path.join(suite, name);
  const repo = path.join(base, 'repo');
  await gitFolder(repo, {
    '.gitignore': 'tmp/\nignored.log\n', 'hello.txt': 'hello\n', 'dir/with space.txt': 'spaced\n', 'ignored.log': 'ignored\n', 'tmp/scratch.txt': 'scratch\n',
    'deleted-later.txt': 'gone\n'
  });
  await fs.mkdir(path.join(repo, 'tools'), {recursive: true});
  for (const file of ['remote-verify.mjs', 'toolchain.mjs', 'toolchain.json']) await fs.copyFile(path.join(tools, file), path.join(repo, 'tools', file));
  await execFileAsync('git', ['-C', repo, 'add', '-N', 'deleted-later.txt']); // Git lists it; the disk no longer has it.
  await fs.unlink(path.join(repo, 'deleted-later.txt'));
  const remote = path.join(base, 'remote');
  await fs.mkdir(remote);
  const mod = await import(pathToFileURL(path.join(repo, 'tools/remote-verify.mjs')).href);
  const logs = path.join(base, 'logs');
  return {
    base, repo, remote, logs, mod,
    run: (extra, {transport, hooks} = {}) => mod.main(['--transport', 'local', '--logs', logs, ...extra], {transport, hooks, log: quiet}),
    record: async () => JSON.parse(await fs.readFile(path.join(logs, 'remote-verify.json'), 'utf8'))
  };
}

const refused = code => error => error.code === code;

test('shq round-trips any string through sh', async () => {
  for (const value of ["plain", "it's", 'two words', '$HOME $(echo no) `echo no`', 'back\\slash', 'new\nline', '"quoted"', '']) {
    const {stdout} = await execFileAsync('/bin/sh', ['-c', `printf %s ${real.shq(value)}`]);
    assert.equal(stdout, value);
  }
});

test('sshArguments never prompts and wraps the script for sh whatever the login shell is', async () => {
  const script = `printf '%s' "it's $HOME"`;
  const args = real.sshArguments('host.example', script);
  assert.deepEqual(args.slice(0, 4), ['-o', 'BatchMode=yes', '--', 'host.example']);
  assert.equal(args.length, 5);
  const {stdout} = await execFileAsync('/bin/sh', ['-c', args[4]], {env: {...process.env, HOME: '/nowhere'}});
  assert.equal(stdout, "it's /nowhere", 'the remote shell expands $HOME once, inside the script');
});

test('usage errors exit 2', () => {
  const bad = [
    [], ['--target', '/r/x', '--', 'true'], ['--host', 'h', '--', 'true'], ['--host', 'h', '--target', 'relative/x', '--', 'true'],
    ['--host', 'h', '--target', '/r/../x', '--', 'true'], ['--host', 'h', '--target', '/', '--', 'true'], ['--host', 'h', '--target', '/r/x'],
    ['--host', 'h', '--target', '/r/x', '--bogus', '--', 'true'], ['--host', 'h', '--target', '/r/x', '--env', 'novalue', '--', 'true'],
    ['--host', 'h', '--target', '/r/x', '--transport', 'carrier-pigeon', '--', 'true'], ['--host', 'h', '--target'],
  ];
  for (const argv of bad) assert.throws(() => real.parseArguments(argv), refused(2), argv.join(' '));
  const ok = real.parseArguments(['--transport', 'local', '--target', '/r/x/', '--env', 'A=b=c', '--', 'node', '--version']);
  assert.deepEqual([ok.target, ok.command, ok.env, ok.host], ['/r/x', ['node', '--version'], ['A=b=c'], null]);
});

test('ships the frozen working tree, verifies its SHA-256, runs the command and fetches the logs', async () => {
  const f = await fixture('ship');
  const target = path.join(f.remote, 'fresh', 'run 1');
  const code = await f.run(['--target', target, '--env', 'VERIFY_MARK=it\'s set', '--', 'sh', '-c',
    'pwd; echo "mark=$VERIFY_MARK"; echo "ala=${ALA_CLI-unset}"; echo ERR >&2; mkdir -p tmp/verify; echo suite-log > tmp/verify/a.log; find . -path ./tmp -prune -o -type f -print | sort; exit 7']);
  assert.equal(code, 1, 'a failing command gives exit 1');
  const record = await f.record();
  assert.equal(record.exitCode, 7);
  assert.deepEqual(record.command.slice(0, 2), ['sh', '-c']);

  const archive = `${target}.candidate.tgz`;
  assert.equal(record.candidate.sha256, await sha256(archive), 'the recorded identity is the SHA-256 of the archive that arrived');
  assert.equal(record.candidate.sha256, await sha256(path.join(f.logs, path.basename((await fs.readdir(f.logs)).find(n => n.startsWith('staging-'))), 'candidate.tar.gz')));
  assert.equal(record.candidate.bytes, (await fs.stat(archive)).size);
  const unpackedHeaders = (await execFileAsync('sh', ['-c', `gzip -dc ${real.shq(archive)} | LC_ALL=C grep -a -c LIBARCHIVE.xattr || true`])).stdout.trim();
  assert.equal(unpackedHeaders, '0', 'no macOS extended attributes travel, so GNU tar has nothing to warn about');

  const log = await fs.readFile(path.join(target, 'tmp/remote-verify/command.log'), 'utf8');
  assert.match(log, new RegExp(`^${target.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`, 'm'), 'the command ran inside the target');
  assert.match(log, /^mark=it's set$/m);
  assert.match(log, /^ala=unset$/m);
  assert.match(log, /^ERR$/m, 'stderr is in the log too');
  const listed = log.split('\n').filter(line => line.startsWith('./')).sort();
  assert.deepEqual(listed, ['./.gitignore', './dir/with space.txt', './hello.txt', './tools/remote-verify.mjs', './tools/toolchain.json', './tools/toolchain.mjs'], 'tracked and untracked files ship; ignored files, tmp/ and files gone from disk do not');
  assert.equal(await fs.readFile(path.join(target, 'tmp/remote-verify/exit-code'), 'utf8'), '7\n');

  const fetched = path.join(f.logs, 'fetched');
  assert.equal(await fs.readFile(path.join(fetched, 'tmp/remote-verify/exit-code'), 'utf8'), '7\n');
  assert.equal(await fs.readFile(path.join(fetched, 'tmp/verify/a.log'), 'utf8'), 'suite-log\n');
  assert.match(await fs.readFile(path.join(fetched, 'tmp/remote-verify/command.log'), 'utf8'), /mark=it's set/);
});

test('a command that succeeds gives exit 0, and a target with quotes and a dollar sign works', async () => {
  const f = await fixture('quoted-target');
  const target = path.join(f.remote, `it's $HOME "x" (1)`);
  assert.equal(await f.run(['--target', target, '--', 'true']), 0);
  assert.equal(await fs.readFile(path.join(target, 'hello.txt'), 'utf8'), 'hello\n');
  assert.equal((await f.record()).exitCode, 0);
});

test('records the identity of the dirty tree: HEAD, status text and file list', async () => {
  const f = await fixture('identity');
  assert.equal(await f.run(['--target', path.join(f.remote, 't'), '--', 'true']), 0);
  const {candidate} = await f.record();
  assert.equal(candidate.files, 6);
  assert.equal(candidate.head, null, 'the fixture has no commit');
  assert.equal(candidate.statusSha256, crypto.createHash('sha256').update((await execFileAsync('git', ['-C', f.repo, 'status', '--short'])).stdout).digest('hex'));
  assert.match(candidate.fileListSha256, /^[0-9a-f]{64}$/);
});

test('refuses a target folder that already exists and leaves it untouched', async () => {
  const f = await fixture('refuse-existing');
  const target = path.join(f.remote, 'taken');
  await fs.mkdir(target);
  await fs.writeFile(path.join(target, 'precious.txt'), 'keep');
  await assert.rejects(f.run(['--target', target, '--', 'true']), refused(3));
  assert.deepEqual(await fs.readdir(target), ['precious.txt']);
  assert.equal(await exists(`${target}.candidate.tgz`), false, 'nothing was shipped');
  assert.match((await f.record()).error, /target folder .* already exists/);
});

test('refuses an existing or linked archive name beside the target and never overwrites it', async () => {
  const f = await fixture('refuse-archive');
  const ala = path.join(f.base, 'ala-checkout');
  await gitFolder(ala, {'bin/ala.mjs': '// candidate ala\n'});
  const victim = path.join(f.base, 'victim.txt');
  await fs.writeFile(victim, 'victim\n');
  const kinds = {
    'a regular file': archive => fs.writeFile(archive, 'old archive\n'),
    'a link to an existing file': archive => fs.symlink(victim, archive),
    'a dangling link': archive => fs.symlink(path.join(f.base, 'does-not-exist'), archive)
  };
  for (const [label, make] of Object.entries(kinds)) {
    for (const [name, extra] of [['run', []], ['run-ala', ['--ala', ala]]]) {
      for (const which of name === 'run' ? ['target'] : ['target', 'ala']) {
        const target = path.join(f.remote, `${name}-${which}-${label.replace(/\W+/g, '-')}`);
        const archive = which === 'target' ? `${target}.candidate.tgz` : path.join(f.remote, 'ala-under-test.candidate.tgz');
        await make(archive);
        await assert.rejects(f.run(['--target', target, ...extra, '--', 'true']), refused(3), `${label} at ${which}`);
        assert.match((await f.record()).error, /candidate\.tgz already exists \(or is a link\)/, `${label} at ${which}`);
        assert.equal(await exists(target), false, 'the target was not created');
        assert.equal(await exists(path.join(f.remote, 'ala-under-test')), false, 'no ala-under-test folder was left');
        assert.equal(await fs.readFile(victim, 'utf8'), 'victim\n', 'nothing was written through the link');
        if (label === 'a regular file') assert.equal(await fs.readFile(archive, 'utf8'), 'old archive\n');
        await fs.unlink(archive);
      }
    }
  }
});

test('an archive name that appears after the claim still cannot be overwritten (noclobber)', async () => {
  const f = await fixture('late-archive');
  const base = real.createTransport('local');
  const target = path.join(f.remote, 'run');
  const victim = path.join(f.base, 'victim.txt');
  await fs.writeFile(victim, 'victim\n');
  // Another process plants a link at the archive name between the claim and the transfer.
  const racing = {name: 'racing local', exec: async (script, options = {}) => {
    if (options.stdinFile) await fs.symlink(victim, `${target}.candidate.tgz`);
    return base.exec(script, options);
  }};
  await assert.rejects(f.run(['--target', target, '--', 'true'], {transport: racing}), error => error.code === 5 && /Transfer of the candidate failed \(exit 31\)/.test(error.message));
  assert.equal(await fs.readFile(victim, 'utf8'), 'victim\n', 'the file behind the link is untouched');
});

test('refuses a target with an AdvancedLanguageAgent folder beside it', async () => {
  const f = await fixture('refuse-sibling');
  await fs.mkdir(path.join(f.remote, 'AdvancedLanguageAgent'));
  const target = path.join(f.remote, 'run');
  await assert.rejects(f.run(['--target', target, '--', 'true']), refused(3));
  assert.equal(await exists(target), false, 'the target was not created');
  assert.match((await f.record()).error, /AdvancedLanguageAgent exists beside the target/);
});

test('ships the ALA candidate as ala-under-test and passes it with ALA_CLI; refuses an existing one', async () => {
  const f = await fixture('ala');
  const ala = path.join(f.base, 'ala-checkout');
  await gitFolder(ala, {'bin/ala.mjs': '// candidate ala\n', 'package.json': '{"name":"advanced-language-agent"}\n', '.gitignore': 'node_modules/\n', 'node_modules/dep/index.js': 'ignored'});
  const target = path.join(f.remote, 'run');
  assert.equal(await f.run(['--target', target, '--ala', ala, '--', 'sh', '-c', 'echo "ala=$ALA_CLI"; cat "$ALA_CLI/bin/ala.mjs"; ls "$ALA_CLI"']), 0);
  const placed = path.join(f.remote, 'ala-under-test');
  const log = await fs.readFile(path.join(target, 'tmp/remote-verify/command.log'), 'utf8');
  assert.match(log, new RegExp(`^ala=${placed.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`, 'm'));
  assert.match(log, /candidate ala/);
  assert.doesNotMatch(log, /node_modules/);
  const record = await f.record();
  assert.equal(record.ala.sha256, await sha256(`${placed}.candidate.tgz`));
  assert.equal(record.ala.files, 3);

  await assert.rejects(f.run(['--target', path.join(f.remote, 'second'), '--ala', ala, '--', 'true']), refused(3));
  assert.equal(await exists(path.join(f.remote, 'second')), false);
});

test('a transfer that corrupts the archive is caught by the SHA-256 check: exit 4, nothing unpacked', async () => {
  const f = await fixture('corrupt');
  const base = (await import(pathToFileURL(path.join(f.repo, 'tools/remote-verify.mjs')).href)).createTransport('local');
  const corrupting = {name: 'corrupting local', exec: async (script, options = {}) => {
    if (!options.stdinFile) return base.exec(script, options);
    const copy = `${options.stdinFile}.corrupt`;
    const bytes = await fs.readFile(options.stdinFile);
    bytes[bytes.length - 1] ^= 0xff;
    await fs.writeFile(copy, bytes);
    return base.exec(script, {stdinFile: copy});
  }};
  const target = path.join(f.remote, 'run');
  await assert.rejects(f.run(['--target', target, '--', 'true'], {transport: corrupting}), error => error.code === 4 && /SHA-256/.test(error.message));
  assert.equal(await exists(target), false, 'the empty claimed folder was released');
  assert.equal(await exists(`${target}.candidate.tgz`), false, 'the corrupt archive this run wrote was removed');
  assert.equal(await exists(path.join(target, 'hello.txt')), false);
});

test('a working tree that changes while the archive is built is not shipped: exit 4', async () => {
  const f = await fixture('moving');
  const target = path.join(f.remote, 'run');
  const hooks = {afterArchive: async () => { await fs.writeFile(path.join(f.repo, 'late.txt'), 'written by another agent\n'); }};
  await assert.rejects(f.run(['--target', target, '--', 'true'], {hooks}), error => error.code === 4 && /changed while the archive was built/.test(error.message));
  assert.equal(await exists(target), false);
  assert.equal(await exists(`${target}.candidate.tgz`), false);
});

test('a transport that fails is exit 5', async () => {
  const f = await fixture('transport-down');
  const broken = {name: 'broken', exec: async () => { throw new Error('connection refused'); }};
  await assert.rejects(f.run(['--target', path.join(f.remote, 'run'), '--', 'true'], {transport: broken}), error => error.code === 5 && /connection refused/.test(error.message));
});
