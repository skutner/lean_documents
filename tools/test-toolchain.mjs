/** Checks of tools/toolchain.mjs and tools/setup-toolchain.mjs against a loopback fixture.
 *
 * Every download in this file comes from a loopback HTTP server (127.0.0.1) or from local files. Each fixture is a
 * fresh folder under tmp/toolchain-tests/ holding copies of the two scripts and a test manifest, so the scripts run
 * unchanged with the fixture as their repository root and nothing touches the real tmp/toolchains. The single test
 * that uses the real manifest nulls every pin first, so the scripts refuse before any request. The folders are kept
 * for inspection. */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import zlib from 'node:zlib';
import crypto from 'node:crypto';
import {fileURLToPath, pathToFileURL} from 'node:url';
import * as realToolchain from './toolchain.mjs';

const execFileAsync = promisify(execFile);
const tools = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(tools, '..');
const realManifest = JSON.parse(await fs.readFile(path.join(tools, 'toolchain.json'), 'utf8'));
const KEYS = ['linux-x64', 'linux-arm64', 'darwin-x64', 'darwin-arm64'];
const HAS_ZSTD = typeof zlib.createZstdDecompress === 'function' && typeof zlib.zstdCompressSync === 'function';
await fs.mkdir(path.join(repoRoot, 'tmp/toolchain-tests'), {recursive: true});
const suite = await fs.mkdtemp(path.join(repoRoot, 'tmp/toolchain-tests/run-'));
console.log(`Retained toolchain test folders: ${suite} (zstd in this Node: ${HAS_ZSTD})`);

const LEAN_LINE = 'Lean (version 4.24.0, fixture-triple, commit 0000000000000000, Release)';
// TOOL_SENTINEL: a test sets it to a path, and an installed-or-staged binary that actually runs creates that file. A test
// can therefore tell whether unverified bytes were ever executed, which the final folder state cannot show.
const leanStub = (line = LEAN_LINE) => `#!/bin/sh\n[ -n "$TOOL_SENTINEL" ] && : > "$TOOL_SENTINEL"\ncase "$1" in --version) echo '${line}' ;; esac\nexit 0\n`;
// Only the interpreter inside the fixture archive writes the sentinel; the stubs standing in for system interpreters do not.
const pythonStub = (version = '3.12.7', sentinel = false) => `#!/bin/sh
${sentinel ? '[ -n "$TOOL_SENTINEL" ] && : > "$TOOL_SENTINEL"' : ':'}
case "$1" in
  --version) echo "Python ${version}" ;;
  -I) echo "${version}" ;;
  *) if [ -f "$1" ] && grep -q FAIL_HELP "$1"; then echo "helper failed" >&2; exit 1; fi ;;
esac
exit 0
`;

async function writeExecutable(file, content) {
  await fs.mkdir(path.dirname(file), {recursive: true});
  await fs.writeFile(file, content);
  await fs.chmod(file, 0o755);
}

/** Builds a tar archive of `files` (relative path -> content), compressed as zst or gz. Returns {file, sha256}. */
async function makeArchive(dir, asset, files, {kind, transform = buffer => buffer, lock = []} = {}) {
  const tree = await fs.mkdtemp(path.join(dir, 'tree-'));
  for (const [relative, content] of Object.entries(files)) await writeExecutable(path.join(tree, relative), content);
  const tops = [...new Set(Object.keys(files).map(relative => relative.split('/')[0]))];
  const tarFile = path.join(tree, '..', `${path.basename(tree)}.tar`);
  for (const folder of lock) await fs.chmod(path.join(tree, folder), 0o555); // read-only folders: removing them later fails for non-root
  await execFileAsync('tar', ['-cf', tarFile, '-C', tree, ...tops], {env: {...process.env, COPYFILE_DISABLE: '1'}});
  for (const folder of lock) await fs.chmod(path.join(tree, folder), 0o755);
  const raw = await fs.readFile(tarFile);
  const packed = transform(kind === 'zst' ? zlib.zstdCompressSync(raw) : zlib.gzipSync(raw));
  const file = path.join(dir, asset);
  await fs.writeFile(file, packed);
  return {file, sha256: crypto.createHash('sha256').update(packed).digest('hex')};
}

async function startServer(directory) {
  const requests = [];
  const overrides = new Map();
  const server = http.createServer(async (request, response) => {
    const name = decodeURIComponent(new URL(request.url, 'http://x').pathname);
    requests.push(name);
    if (overrides.has(name)) {
      response.statusCode = overrides.get(name);
      return response.end('override');
    }
    try {
      const body = await fs.readFile(path.join(directory, path.basename(name)));
      response.setHeader('content-length', body.length);
      response.end(body);
    } catch {
      response.statusCode = 404;
      response.end('not found');
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return {
    base: `http://127.0.0.1:${server.address().port}`, requests, overrides,
    close: () => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); })
  };
}

/** A fresh repository root with the real scripts, a test manifest, archives and a loopback server.
 * Options: leanKind ('zst'|'gz'), lean/python ({files, top, pin, transform, asset}), system ('ok'|'old'|'none'),
 * helper ('ok'|'fail'|'absent'), nullPins. */
async function fixture(name, options = {}) {
  const dir = path.join(suite, name);
  const repo = path.join(dir, 'repo');
  const assets = path.join(dir, 'assets');
  await fs.mkdir(path.join(repo, 'tools'), {recursive: true});
  await fs.mkdir(assets, {recursive: true});
  for (const file of ['toolchain.mjs', 'setup-toolchain.mjs']) await fs.copyFile(path.join(tools, file), path.join(repo, 'tools', file));
  if (options.helper !== 'absent') await fs.writeFile(path.join(repo, 'tools/document_audit.py'), options.helper === 'fail' ? '# FAIL_HELP\n' : '# stub\n');

  const server = await startServer(assets);
  const leanKind = options.leanKind ?? (HAS_ZSTD ? 'zst' : 'gz');
  const leanAsset = options.lean?.asset ?? `lean-fixture.tar.${leanKind}`;
  const leanTop = options.lean?.top ?? 'lean-fixture';
  const lean = await makeArchive(assets, leanAsset, options.lean?.files ?? {[`${leanTop}/bin/lean`]: leanStub(), [`${leanTop}/lib/data.txt`]: 'data\n'}, {kind: leanKind, transform: options.lean?.transform, lock: options.lean?.lock});
  const pythonAsset = 'python-fixture.tar.gz';
  const python = await makeArchive(assets, pythonAsset, options.python?.files ?? {'python/bin/python3': pythonStub('3.12.7', true), 'python/lib/data.txt': 'data\n'}, {kind: 'gz', transform: options.python?.transform});

  const system = path.join(dir, 'system');
  await writeExecutable(path.join(system, 'ok/python3'), pythonStub('3.14.4'));
  await writeExecutable(path.join(system, 'old/python3'), pythonStub('3.9.6'));
  const locations = {ok: [path.join(system, 'ok/python3')], old: [path.join(system, 'old/python3')], none: [path.join(system, 'absent/python3')]}[options.system ?? 'ok'];

  const manifest = {
    schema: 1, provenance: 'test fixture',
    lean: {version: '4.24.0', platforms: {}},
    python: {version: '3.12.7', minimum: [3, 12], systemLocations: {linux: locations, darwin: locations}, platforms: {}}
  };
  for (const key of KEYS) {
    manifest.lean.platforms[key] = {
      asset: leanAsset, url: `${server.base}/${leanAsset}`, folder: realManifest.lean.platforms[key].folder, topLevel: leanTop,
      sha256: options.nullPins ? null : options.lean?.pin ?? lean.sha256
    };
    manifest.python.platforms[key] = {
      asset: pythonAsset, url: `${server.base}/${pythonAsset}`, folder: realManifest.python.platforms[key].folder.replace('<PATCH>', '7'), topLevel: 'python',
      sha256: options.nullPins ? null : options.python?.pin ?? python.sha256
    };
  }
  // No test manifest may point anywhere but at this machine.
  for (const entry of [...Object.values(manifest.lean.platforms), ...Object.values(manifest.python.platforms)]) assert.equal(new URL(entry.url).hostname, '127.0.0.1');
  await fs.writeFile(path.join(repo, 'tools/toolchain.json'), JSON.stringify(manifest, null, 2));

  const hostKey = realToolchain.platformKey();
  const toolchains = path.join(repo, 'tmp/toolchains');
  const stubPath = options.pathDirs ?? [];
  const api = {
    dir, repo, assets, server, manifest, hostKey, toolchains, system, lean, python,
    requests: server.requests,
    folder: (component, key = hostKey) => path.join(toolchains, manifest[component].platforms[key].folder),
    marker: (component, key = hostKey) => path.join(toolchains, `${manifest[component].platforms[key].folder}.installed.json`),
    async run(args, {env = {}} = {}) {
      const childEnv = {...process.env, PATH: [...stubPath, path.dirname(process.execPath), '/usr/bin', '/bin'].join(path.delimiter), ...env};
      try {
        const {stdout, stderr} = await execFileAsync(process.execPath, [path.join(repo, 'tools/setup-toolchain.mjs'), ...args], {cwd: repo, env: childEnv, timeout: 120000});
        return {code: 0, stdout, stderr, all: stdout + stderr};
      } catch (error) {
        if (typeof error.code !== 'number') throw error;
        return {code: error.code, stdout: error.stdout, stderr: error.stderr, all: error.stdout + error.stderr};
      }
    },
    async listing() {
      try {
        return (await fs.readdir(toolchains)).sort();
      } catch {
        return null;
      }
    },
    async resolver() {
      return import(pathToFileURL(path.join(repo, 'tools/toolchain.mjs')).href);
    },
    async close() {
      await server.close();
    }
  };
  return api;
}

async function withFixture(name, options, body) {
  const f = await fixture(name, options);
  try {
    await body(f);
  } finally {
    await f.close();
  }
}

const exists = async file => fs.access(file).then(() => true, () => false);

async function assertNothingInstalled(f, component = 'lean') {
  assert.equal(await exists(f.folder(component)), false, 'no install folder');
  assert.equal(await exists(f.marker(component)), false, 'no marker');
  const left = (await f.listing()) ?? [];
  assert.deepEqual(left.filter(name => name.startsWith('.staging-')), [], 'no staging folder left');
}

// Platform keys and manifest

test('platformKey maps the four supported platforms and rejects the others', () => {
  assert.equal(realToolchain.platformKey({platform: 'linux', arch: 'x64'}), 'linux-x64');
  assert.equal(realToolchain.platformKey({platform: 'linux', arch: 'arm64'}), 'linux-arm64');
  assert.equal(realToolchain.platformKey({platform: 'darwin', arch: 'x64'}), 'darwin-x64');
  assert.equal(realToolchain.platformKey({platform: 'darwin', arch: 'arm64'}), 'darwin-arm64');
  assert.throws(() => realToolchain.platformKey({platform: 'win32', arch: 'x64'}), {message: 'Unsupported platform for the Lean toolchain: win32-x64'});
  assert.throws(() => realToolchain.platformKey({platform: 'linux', arch: 'ia32'}), {message: 'Unsupported platform for the Lean toolchain: linux-ia32'});
  assert.ok(KEYS.includes(realToolchain.platformKey()), 'the current machine is one of the four keys');
});

test('the real manifest lists the planned assets and folders, with pins that are null or SHA-256 digests', () => {
  const expected = {
    'linux-x64': ['lean-4.24.0-linux', 'lean-4.24.0-linux.tar.zst'],
    'linux-arm64': ['lean-4.24.0-linux_aarch64', 'lean-4.24.0-linux_aarch64.tar.zst'],
    'darwin-x64': ['lean-4.24.0-darwin', 'lean-4.24.0-darwin.tar.zst'],
    'darwin-arm64': ['lean-4.24.0-darwin_aarch64', 'lean-4.24.0-darwin_aarch64.tar.zst']
  };
  assert.equal(realManifest.lean.version, '4.24.0');
  assert.deepEqual(realManifest.python.minimum, [3, 12]);
  assert.equal(typeof realManifest.provenance, 'string');
  for (const key of KEYS) {
    const lean = realManifest.lean.platforms[key];
    assert.deepEqual([lean.folder, lean.asset], expected[key]);
    assert.equal(lean.topLevel, lean.folder);
    assert.equal(lean.url, `https://github.com/leanprover/lean4/releases/download/v4.24.0/${lean.asset}`);
    for (const entry of [lean, realManifest.python.platforms[key]]) {
      assert.ok(entry.sha256 === null || /^[0-9a-f]{64}$/.test(entry.sha256), `${key} pin`);
      assert.ok(entry.url.startsWith('https://'));
    }
    assert.equal(realToolchain.leanExecutable('/r', key), `/r/tmp/toolchains/${lean.folder}/bin/lean`);
  }
});

test('leanStatus reports the executable path and whether it exists', async () => {
  await withFixture('status', {}, async f => {
    const mod = await f.resolver();
    const before = mod.leanStatus(f.repo);
    assert.deepEqual(before, {path: path.join(f.folder('lean'), 'bin/lean'), platform: f.hostKey, installed: false});
    await writeExecutable(before.path, '#!/bin/sh\n');
    assert.equal(mod.leanStatus(f.repo).installed, true, 'a hand-placed toolchain without a marker counts');
    assert.equal(mod.leanExecutable(f.repo, 'darwin-arm64'), path.join(f.toolchains, realManifest.lean.platforms['darwin-arm64'].folder, 'bin/lean'));
  });
});

// Installer: Lean

test('installs Lean from the loopback server, verifies it, and a second run makes no request', async () => {
  await withFixture('install', {}, async f => {
    const first = await f.run([]);
    assert.equal(first.code, 0, first.all);
    assert.deepEqual(f.requests, [`/${f.manifest.lean.platforms[f.hostKey].asset}`]);
    assert.match(first.stdout, /python .*not needed/);
    const folder = f.folder('lean');
    assert.ok((await fs.stat(path.join(folder, 'bin/lean'))).isFile());
    const marker = JSON.parse(await fs.readFile(f.marker('lean'), 'utf8'));
    assert.equal(marker.sha256, f.lean.sha256);
    assert.equal(marker.asset, f.manifest.lean.platforms[f.hostKey].asset);
    assert.equal(marker.versionLine, LEAN_LINE);
    assert.equal(marker.component, 'lean');
    assert.deepEqual((await f.listing()).filter(name => name.startsWith('.')), [], 'no staging folder left');
    assert.equal(await exists(f.folder('python')), false, 'a qualifying system Python means no Python install');
    assert.deepEqual((await f.run(['--check'])).code, 0);

    const before = f.requests.length;
    f.server.overrides.set(`/${f.manifest.lean.platforms[f.hostKey].asset}`, 500);
    const second = await f.run([]);
    assert.equal(second.code, 0, second.all);
    assert.match(second.stdout, /already installed/);
    assert.equal(f.requests.length, before, 'the second run makes no request');
  });
});

test('installs a .tar.gz Lean asset as well as .tar.zst', async () => {
  await withFixture('install-gz', {leanKind: 'gz'}, async f => {
    const result = await f.run([]);
    assert.equal(result.code, 0, result.all);
    assert.ok(f.manifest.lean.platforms[f.hostKey].asset.endsWith('.tar.gz'));
    assert.ok((await fs.stat(path.join(f.folder('lean'), 'bin/lean'))).isFile());
  });
});

test('installs a .tar.zst Lean asset through the built-in zstd', {skip: HAS_ZSTD ? false : 'this Node.js has no built-in zstd'}, async () => {
  await withFixture('install-zst', {leanKind: 'zst'}, async f => {
    assert.ok(f.manifest.lean.platforms[f.hostKey].asset.endsWith('.tar.zst'));
    const result = await f.run([]);
    assert.equal(result.code, 0, result.all);
    assert.ok((await fs.stat(path.join(f.folder('lean'), 'bin/lean'))).isFile());
  });
});

test('--check exits 1 when nothing is installed and writes nothing', async () => {
  await withFixture('check-empty', {}, async f => {
    const result = await f.run(['--check']);
    assert.equal(result.code, 1, result.all);
    assert.match(result.stdout, /lean .*missing/);
    assert.equal(await f.listing(), null, 'tmp/toolchains was not created');
    assert.deepEqual(f.requests, []);
  });
});

/** Nothing from a download whose SHA-256 does not match the pin may be unpacked, executed or announced as verified. */
async function assertRefusedBeforeUse(f, result, component = 'lean') {
  assert.equal(result.code, 4, result.all);
  assert.match(result.stderr, /SHA-256 mismatch/);
  assert.doesNotMatch(result.all, /SHA-256 verified|unpacking/, 'the output never claims verification or unpacking');
  assert.equal(await exists(path.join(f.dir, 'sentinel')), false, 'no binary from the unverified archive was ever run');
  await assertNothingInstalled(f, component);
}

test('a checksum mismatch exits 4 and leaves no folder, marker or staging folder; nothing runs', async () => {
  await withFixture('mismatch', {lean: {pin: '0'.repeat(64)}}, async f => {
    const result = await f.run([], {env: {TOOL_SENTINEL: path.join(f.dir, 'sentinel')}});
    assert.equal(f.requests.length, 1);
    await assertRefusedBeforeUse(f, result);
  });
  await withFixture('mismatch-python', {system: 'old', python: {pin: '0'.repeat(64)}}, async f => {
    const result = await f.run(['--only', 'python'], {env: {TOOL_SENTINEL: path.join(f.dir, 'sentinel')}});
    await assertRefusedBeforeUse(f, result, 'python');
  });
});

test('a mismatch for a modified archive is caught before anything is unpacked or run', async () => {
  await withFixture('tampered', {}, async f => {
    const asset = path.join(f.assets, f.manifest.lean.platforms[f.hostKey].asset);
    await fs.appendFile(asset, 'tampered');
    await assertRefusedBeforeUse(f, await f.run([], {env: {TOOL_SENTINEL: path.join(f.dir, 'sentinel')}}));
  });
});

test('bad bytes under a wrong pin give exit 4, never exit 5: the pin is compared before any unpacking', async () => {
  for (const [label, bytes] of [['garbage', Buffer.from('this is not an archive')], ['empty', Buffer.alloc(0)], ['truncated', null]]) {
    await withFixture(`wrong-pin-${label}`, {}, async f => {
      const asset = path.join(f.assets, f.manifest.lean.platforms[f.hostKey].asset);
      const body = bytes ?? (await fs.readFile(asset)).subarray(0, 40);
      await fs.writeFile(asset, body); // the manifest still pins the original, valid archive
      await assertRefusedBeforeUse(f, await f.run([], {env: {TOOL_SENTINEL: path.join(f.dir, 'sentinel')}}));
    });
  }
});

test('tar runs with a fixed environment: an inherited TAR_OPTIONS changes nothing', async () => {
  // GNU tar reads TAR_OPTIONS; with --strip-components=5 it extracts nothing from these archives, so an inherited value
  // would make the install fail with exit 5. (GNU tar 1.35 ignores --version given this way, so that value proves
  // nothing.) bsdtar (macOS) ignores the variable, so this test only proves something on Linux.
  await withFixture('tar-options', {}, async f => {
    const result = await f.run([], {env: {TAR_OPTIONS: '--strip-components=5'}});
    assert.equal(result.code, 0, result.all);
    assert.ok((await fs.stat(path.join(f.folder('lean'), 'bin/lean'))).isFile());
    assert.ok((await fs.stat(path.join(f.folder('lean'), 'lib/data.txt'))).isFile());
  });
});

test('a failure while removing the staging folder does not replace the real exit code', async () => {
  // A read-only folder inside the unpacked archive makes the final removal fail for any user except root.
  await withFixture('cleanup-failure', {lean: {files: {'lean-fixture/bin/lean': leanStub('Lean (version 4.23.0, fixture, Release)'), 'lean-fixture/locked/data.txt': 'x'}, lock: ['lean-fixture/locked']}}, async f => {
    const result = await f.run([]);
    try {
      assert.equal(result.code, 5, result.all);
      assert.match(result.stderr, /Self-check failed/);
      if (process.getuid?.() !== 0) assert.match(result.stderr, /Warning: could not remove the staging folder/);
      assert.equal(await exists(f.folder('lean')), false);
      assert.equal(await exists(f.marker('lean')), false);
    } finally {
      // Make the leftover folder removable again so that it does not obstruct later cleanup by hand.
      for (const name of (await f.listing()) ?? []) if (name.startsWith('.staging-')) await execFileAsync('chmod', ['-R', 'u+w', path.join(f.toolchains, name)]);
    }
  });
});

test('a folder without a marker is never overwritten', async () => {
  await withFixture('no-marker', {}, async f => {
    await fs.mkdir(f.folder('lean'), {recursive: true});
    await fs.writeFile(path.join(f.folder('lean'), 'precious.txt'), 'keep');
    for (const args of [[], ['--source', f.server.base]]) {
      const result = await f.run(args);
      assert.equal(result.code, 5, result.all);
      assert.match(result.stderr, /without an install marker/);
    }
    assert.equal(await fs.readFile(path.join(f.folder('lean'), 'precious.txt'), 'utf8'), 'keep');
    assert.deepEqual(f.requests, [], 'refused before any download');
    assert.equal((await f.run(['--check'])).code, 1);
  });
});

test('a marker for a different pin is not overwritten either', async () => {
  await withFixture('stale-marker', {}, async f => {
    assert.equal((await f.run([])).code, 0);
    const marker = JSON.parse(await fs.readFile(f.marker('lean'), 'utf8'));
    marker.sha256 = 'f'.repeat(64);
    await fs.writeFile(f.marker('lean'), JSON.stringify(marker));
    const count = f.requests.length;
    const result = await f.run([]);
    assert.equal(result.code, 5, result.all);
    assert.match(result.stderr, /different pin/);
    assert.equal(f.requests.length, count);
    assert.equal((await f.run(['--check'])).code, 1);
  });
});

test('download failures exit 3 and leave nothing behind', async () => {
  await withFixture('download-404', {}, async f => {
    f.server.overrides.set(`/${f.manifest.lean.platforms[f.hostKey].asset}`, 404);
    const result = await f.run([]);
    assert.equal(result.code, 3, result.all);
    assert.match(result.stderr, /HTTP 404/);
    await assertNothingInstalled(f);
  });
  await withFixture('download-refused', {}, async f => {
    const dead = `http://127.0.0.1:${f.server.base.split(':')[2]}`;
    await f.close();
    const result = await f.run(['--source', dead]);
    assert.equal(result.code, 3, result.all);
    await assertNothingInstalled(f);
  });
  await withFixture('download-missing-file', {}, async f => {
    const result = await f.run(['--source', path.join(f.dir, 'does-not-exist')]);
    assert.equal(result.code, 3, result.all);
    await assertNothingInstalled(f);
  });
});

const BAD_ARCHIVES = {
  'garbage bytes with a matching pin': {kind: 'garbage'},
  'a truncated archive with a matching pin': {transform: buffer => buffer.subarray(0, Math.floor(buffer.length / 2))},
  'an archive with a different top-level folder': {top: 'other-folder', expectTop: 'lean-fixture', files: {'other-folder/bin/lean': leanStub()}},
  'an archive with two top-level entries': {files: {'lean-fixture/bin/lean': leanStub(), 'extra/file.txt': 'x'}},
  'an archive without the lean binary': {files: {'lean-fixture/lib/data.txt': 'x'}},
  'a lean that reports another version': {files: {'lean-fixture/bin/lean': leanStub('Lean (version 4.23.0, fixture, Release)')}}
};
for (const [label, spec] of Object.entries(BAD_ARCHIVES)) {
  test(`unpack or self-check failure exits 5 and installs nothing: ${label}`, async () => {
    const name = 'bad-' + label.replace(/\W+/g, '-');
    await withFixture(name, {lean: {files: spec.files, top: spec.top, transform: spec.transform}}, async f => {
      if (spec.kind === 'garbage') {
        const asset = path.join(f.assets, f.manifest.lean.platforms[f.hostKey].asset);
        const bytes = Buffer.from('this is not an archive');
        await fs.writeFile(asset, bytes);
        for (const key of KEYS) f.manifest.lean.platforms[key].sha256 = crypto.createHash('sha256').update(bytes).digest('hex');
      } else if (spec.transform) {
        const asset = path.join(f.assets, f.manifest.lean.platforms[f.hostKey].asset);
        const bytes = await fs.readFile(asset);
        for (const key of KEYS) f.manifest.lean.platforms[key].sha256 = crypto.createHash('sha256').update(bytes).digest('hex');
      }
      if (spec.expectTop) for (const key of KEYS) f.manifest.lean.platforms[key].topLevel = spec.expectTop;
      await fs.writeFile(path.join(f.repo, 'tools/toolchain.json'), JSON.stringify(f.manifest));
      const result = await f.run([]);
      assert.equal(result.code, 5, result.all);
      await assertNothingInstalled(f);
    });
  });
}

test('an unpinned asset is refused with exit 2 before any request or folder', async () => {
  await withFixture('null-pin', {nullPins: true}, async f => {
    const result = await f.run([]);
    assert.equal(result.code, 2, result.all);
    assert.match(result.stderr, /pin in tools\/toolchain\.json is null/);
    assert.match(result.stderr, /Pins are added once the release digests are fetched/);
    assert.deepEqual(f.requests, []);
    assert.equal(await f.listing(), null, 'tmp/toolchains was not created');
    const check = await f.run(['--check']);
    assert.equal(check.code, 1, check.all);
  });
});

test('the real manifest, with every pin nulled, is refused before any network use', async () => {
  const dir = path.join(suite, 'real-manifest-nulled');
  await fs.mkdir(path.join(dir, 'tools'), {recursive: true});
  for (const file of ['toolchain.mjs', 'setup-toolchain.mjs']) await fs.copyFile(path.join(tools, file), path.join(dir, 'tools', file));
  const nulled = structuredClone(realManifest);
  // Every URL is pointed at a closed loopback port, keeping the real asset names, so that a regression in the pin check can
  // only fail to connect and never reaches GitHub.
  for (const component of ['lean', 'python']) for (const entry of Object.values(nulled[component].platforms)) {
    entry.sha256 = null;
    entry.url = `http://127.0.0.1:9/${entry.asset}`;
  }
  await fs.writeFile(path.join(dir, 'tools/toolchain.json'), JSON.stringify(nulled));
  // --python guarantees that both components are selected whatever the host's Python is.
  for (const key of KEYS) {
    const result = await execFileAsync(process.execPath, [path.join(dir, 'tools/setup-toolchain.mjs'), '--python', '--platform', key], {cwd: dir}).catch(error => error);
    assert.equal(result.code, 2, `${key}: ${result.stderr}`);
    assert.match(result.stderr, new RegExp(`lean \\(${key}\\): the SHA-256 pin in tools/toolchain\\.json is null`));
    assert.match(result.stderr, /Pins are added once the release digests are fetched/);
  }
  assert.equal(await exists(path.join(dir, 'tmp')), false, 'nothing was created');
});

test('usage errors and unsupported platforms exit 2', async () => {
  await withFixture('usage', {}, async f => {
    for (const args of [['--platform', 'plan9-x64'], ['--platform', 'win32-x64'], ['--bogus'], ['--only', 'both'], ['--only'], ['--python', '--only', 'lean'], ['--check', '--source', f.server.base], ['stray'], ['--source', 'http://'], ['--source', 'ftp://host/x'], ['--python=false'], ['--check=x'], ['--source=']]) {
      const result = await f.run(args);
      assert.equal(result.code, 2, `${args.join(' ')}: ${result.all}`);
    }
    assert.match((await f.run(['--platform', 'plan9-x64'])).stderr, /Unsupported platform for the Lean toolchain: plan9-x64/);
    assert.deepEqual(f.requests, []);
    assert.equal(await f.listing(), null);
  });
});

test('every platform key installs into its own folder, from a local directory source', async () => {
  for (const key of KEYS) {
    await withFixture(`key-${key}`, {}, async f => {
      // The default URLs are dead on purpose: only --source may be used.
      for (const platforms of [f.manifest.lean.platforms, f.manifest.python.platforms]) for (const entry of Object.values(platforms)) entry.url = 'http://127.0.0.1:9/unused';
      await fs.writeFile(path.join(f.repo, 'tools/toolchain.json'), JSON.stringify(f.manifest));
      const result = await f.run(['--platform', key, '--source', f.assets]);
      assert.equal(result.code, 0, result.all);
      assert.equal(f.folder('lean', key), path.join(f.toolchains, realManifest.lean.platforms[key].folder));
      assert.ok((await fs.stat(path.join(f.folder('lean', key), 'bin/lean'))).isFile());
      assert.deepEqual(f.requests, []);
    });
  }
});

test('--source accepts a directory, a file, a file URL, a base URL and a direct asset URL', async () => {
  const forms = {
    directory: f => f.assets,
    file: f => path.join(f.assets, f.manifest.lean.platforms[f.hostKey].asset),
    'file URL': f => pathToFileURL(path.join(f.assets, f.manifest.lean.platforms[f.hostKey].asset)).href,
    'base URL': f => f.server.base,
    'asset URL': f => `${f.server.base}/${f.manifest.lean.platforms[f.hostKey].asset}`
  };
  for (const [label, source] of Object.entries(forms)) {
    await withFixture(`source-${label.replace(/\W+/g, '-')}`, {}, async f => {
      // The default URL is dead on purpose: only --source may be used.
      for (const entry of Object.values(f.manifest.lean.platforms)) entry.url = 'http://127.0.0.1:9/unused';
      await fs.writeFile(path.join(f.repo, 'tools/toolchain.json'), JSON.stringify(f.manifest));
      const result = await f.run(['--only', 'lean', '--source', source(f)]);
      assert.equal(result.code, 0, `${label}: ${result.all}`);
      assert.ok((await fs.stat(path.join(f.folder('lean'), 'bin/lean'))).isFile(), label);
      assert.equal(f.requests.length, label.endsWith('URL') && label !== 'file URL' ? 1 : 0, `requests for ${label}`);
    });
  }
});

test('a single-asset --source is refused when Lean and Python are both pending; folder and base URL still work', async () => {
  for (const form of ['file', 'file URL', 'asset URL']) {
    await withFixture(`source-single-${form.replace(/\W+/g, '-')}`, {system: 'old'}, async f => {
      const asset = f.manifest.lean.platforms[f.hostKey].asset;
      const sources = {file: path.join(f.assets, asset), 'file URL': pathToFileURL(path.join(f.assets, asset)).href, 'asset URL': `${f.server.base}/${asset}`};
      const result = await f.run(['--source', sources[form]]);
      assert.equal(result.code, 2, `${form}: ${result.all}`);
      assert.match(result.stderr, /names one asset, but lean and python are both pending.*Add --only lean or --only python/);
      assert.doesNotMatch(result.all, /downloading|Nothing was installed/);
      assert.deepEqual(f.requests, []);
      assert.equal(await f.listing(), null, 'nothing was created');
      // The same source is fine for one component.
      assert.equal((await f.run(['--only', 'lean', '--source', sources[form]])).code, 0, form);
    });
  }
  for (const form of ['folder', 'base URL']) {
    await withFixture(`source-both-${form.replace(/\W+/g, '-')}`, {system: 'old'}, async f => {
      for (const platforms of [f.manifest.lean.platforms, f.manifest.python.platforms]) for (const entry of Object.values(platforms)) entry.url = 'http://127.0.0.1:9/unused';
      await fs.writeFile(path.join(f.repo, 'tools/toolchain.json'), JSON.stringify(f.manifest));
      const result = await f.run(['--source', form === 'folder' ? f.assets : f.server.base]);
      assert.equal(result.code, 0, `${form}: ${result.all}`);
      assert.ok((await fs.stat(path.join(f.folder('lean'), 'bin/lean'))).isFile());
      assert.ok((await fs.stat(path.join(f.folder('python'), 'bin/python3'))).isFile());
    });
  }
});

// Installer: Python

test('--only lean never installs Python, even when no system interpreter qualifies', async () => {
  await withFixture('only-lean', {system: 'old'}, async f => {
    const result = await f.run(['--only', 'lean']);
    assert.equal(result.code, 0, result.all);
    assert.deepEqual(f.requests, [`/${f.manifest.lean.platforms[f.hostKey].asset}`]);
    assert.equal(await exists(f.folder('python')), false);
  });
});

test('Python is not installed when a system interpreter qualifies, and --only python then does nothing', async () => {
  await withFixture('python-not-needed', {system: 'ok'}, async f => {
    const result = await f.run(['--only', 'python']);
    assert.equal(result.code, 0, result.all);
    assert.match(result.stdout, /not needed, the system interpreter .* 3\.14\.4 qualifies/);
    assert.deepEqual(f.requests, []);
    assert.equal(await f.listing(), null);
    assert.equal((await f.run(['--check', '--only', 'python'])).code, 0);
  });
});

test('Python is installed alongside Lean when no system interpreter qualifies', async () => {
  await withFixture('python-needed', {system: 'old'}, async f => {
    const result = await f.run([]);
    assert.equal(result.code, 0, result.all);
    assert.match(result.stdout, /no system interpreter at 3\.12 or later/);
    assert.deepEqual([...f.requests].sort(), [`/${f.manifest.lean.platforms[f.hostKey].asset}`, `/${f.manifest.python.platforms[f.hostKey].asset}`].sort());
    const marker = JSON.parse(await fs.readFile(f.marker('python'), 'utf8'));
    assert.equal(marker.component, 'python');
    assert.equal(marker.sha256, f.python.sha256);
    assert.equal(marker.versionLine, 'Python 3.12.7');
    assert.ok((await fs.stat(path.join(f.folder('python'), 'bin/python3'))).isFile());
    assert.equal((await f.run(['--check'])).code, 0);
    const before = f.requests.length;
    assert.equal((await f.run([])).code, 0);
    assert.equal(f.requests.length, before);
  });
});

test('--python installs the pinned interpreter although a system interpreter qualifies', async () => {
  await withFixture('python-forced', {system: 'ok'}, async f => {
    const result = await f.run(['--only', 'python', '--python']);
    assert.equal(result.code, 0, result.all);
    assert.deepEqual(f.requests, [`/${f.manifest.python.platforms[f.hostKey].asset}`]);
    assert.equal(await exists(f.folder('lean')), false, '--only python leaves Lean alone');
    assert.ok((await fs.stat(path.join(f.folder('python'), 'bin/python3'))).isFile());
  });
});

test('an interpreter that is only on PATH does not count as a system interpreter', async () => {
  await withFixture('python-path-only', {system: 'old'}, async f => {
    const bin = path.join(f.dir, 'pathbin');
    await writeExecutable(path.join(bin, 'python3'), pythonStub('3.13.1'));
    const result = await f.run(['--only', 'python'], {env: {PATH: [bin, path.dirname(process.execPath), '/usr/bin', '/bin'].join(path.delimiter)}});
    assert.equal(result.code, 0, result.all);
    assert.ok((await fs.stat(path.join(f.folder('python'), 'bin/python3'))).isFile(), 'Python was installed');
  });
});

test('a Python that reports another version, or whose helper check fails, exits 5 and installs nothing', async () => {
  await withFixture('python-wrong-version', {system: 'old', python: {files: {'python/bin/python3': pythonStub('3.12.6')}}}, async f => {
    const result = await f.run(['--only', 'python']);
    assert.equal(result.code, 5, result.all);
    assert.match(result.stderr, /expected "Python 3\.12\.7"/);
    await assertNothingInstalled(f, 'python');
  });
  await withFixture('python-helper-fails', {system: 'old', helper: 'fail'}, async f => {
    const result = await f.run(['--only', 'python']);
    assert.equal(result.code, 5, result.all);
    assert.match(result.stderr, /document_audit\.py --help/);
    await assertNothingInstalled(f, 'python');
  });
  await withFixture('python-no-helper', {system: 'old', helper: 'absent'}, async f => {
    const result = await f.run(['--only', 'python']);
    assert.equal(result.code, 0, result.all);
    assert.match(result.stdout, /was not run/);
  });
});

test('a placeholder in the Python entry blocks the whole run, Lean included', async () => {
  await withFixture('python-placeholder', {system: 'old'}, async f => {
    for (const platforms of [f.manifest.python.platforms]) for (const entry of Object.values(platforms)) entry.asset = entry.asset.replace('python-fixture', 'cpython-<VERSION>');
    await fs.writeFile(path.join(f.repo, 'tools/toolchain.json'), JSON.stringify(f.manifest));
    const result = await f.run([]);
    assert.equal(result.code, 2, result.all);
    assert.match(result.stderr, /placeholder/);
    assert.deepEqual(f.requests, []);
    assert.equal(await f.listing(), null);
  });
});

// Resolver

async function resolverFixture(name, options, body) {
  await withFixture(name, options, async f => {
    const mod = await f.resolver();
    // HOME is a folder of its own so that the home-folder rule never depends on where this checkout lives.
    const env = (extra = {}) => ({PATH: '', HOME: path.join(f.dir, 'nohome'), ...extra});
    await body(f, mod, env);
  });
}

test('resolvePython prefers the installed toolchain interpreter over a system one', async () => {
  await resolverFixture('resolve-toolchain', {system: 'ok'}, async (f, mod, env) => {
    assert.equal((await f.run(['--only', 'python', '--python'])).code, 0);
    const found = mod.resolvePython({env: env()});
    assert.equal(found.path, path.join(f.folder('python'), 'bin/python3'));
    assert.equal(found.version, '3.12.7');
  });
});

test('resolvePython ignores a toolchain interpreter whose marker does not match the manifest', async () => {
  await resolverFixture('resolve-marker', {system: 'ok'}, async (f, mod, env) => {
    assert.equal((await f.run(['--only', 'python', '--python'])).code, 0);
    const original = await fs.readFile(f.marker('python'), 'utf8');
    for (const change of [{sha256: 'e'.repeat(64)}, {asset: 'other.tar.gz'}]) {
      await fs.writeFile(f.marker('python'), JSON.stringify({...JSON.parse(original), ...change}));
      assert.equal(mod.resolvePython({env: env()}).path, f.manifest.python.systemLocations[process.platform][0], JSON.stringify(change));
    }
    await fs.rename(f.marker('python'), `${f.marker('python')}.moved`);
    assert.equal(mod.resolvePython({env: env()}).version, '3.14.4', 'no marker');
  });
  await resolverFixture('resolve-marker-none', {system: 'old'}, async (f, mod, env) => {
    assert.equal((await f.run(['--only', 'python'])).code, 0);
    await fs.writeFile(f.marker('python'), JSON.stringify({...JSON.parse(await fs.readFile(f.marker('python'), 'utf8')), sha256: 'e'.repeat(64)}));
    assert.throws(() => mod.resolvePython({env: env()}), /Python 3\.12 or later is required/);
  });
});

test('resolvePython tries system locations before PATH and walks the PATH names in order', async () => {
  await resolverFixture('resolve-order', {system: 'ok'}, async (f, mod, env) => {
    const d1 = path.join(f.dir, 'p1'), d2 = path.join(f.dir, 'p2');
    await writeExecutable(path.join(d1, 'python3'), pythonStub('3.9.1'));
    await writeExecutable(path.join(d1, 'python3.12'), pythonStub('3.12.1'));
    await writeExecutable(path.join(d2, 'python3.14'), pythonStub('3.14.1'));
    const PATH = ['relative/dir', d1, d2].join(path.delimiter);
    assert.equal(mod.resolvePython({env: env({PATH})}).version, '3.14.4', 'system location first');
  });
  await resolverFixture('resolve-path', {system: 'none'}, async (f, mod, env) => {
    const d1 = path.join(f.dir, 'p1'), d2 = path.join(f.dir, 'p2');
    await writeExecutable(path.join(d1, 'python3'), pythonStub('3.9.1'));
    await writeExecutable(path.join(d1, 'python3.12'), pythonStub('3.12.1'));
    await writeExecutable(path.join(d2, 'python3.14'), pythonStub('3.14.1'));
    const found = mod.resolvePython({env: env({PATH: ['relative/dir', d1, d2].join(path.delimiter)})});
    assert.deepEqual(found, {path: path.join(d2, 'python3.14'), version: '3.14.1'});
    assert.ok(path.isAbsolute(found.path));
    assert.equal(mod.resolvePython({env: env({PATH: d1})}).path, path.join(d1, 'python3.12'));
    assert.equal(mod.resolvePython({env: env({PATH: d1}), minimum: [3, 9]}).version, '3.9.1', 'minimum is a parameter');
  });
});

test('resolvePython honours AUDIT_PYTHON and never replaces an invalid value', async () => {
  await resolverFixture('resolve-audit', {system: 'ok'}, async (f, mod, env) => {
    const good = path.join(f.dir, 'custom/python3'), old = path.join(f.dir, 'custom/old/python3');
    await writeExecutable(good, pythonStub('3.13.2'));
    await writeExecutable(old, pythonStub('3.9.6'));
    assert.deepEqual(mod.resolvePython({env: env({AUDIT_PYTHON: good})}), {path: good, version: '3.13.2'});
    assert.equal((await f.run(['--only', 'python', '--python'])).code, 0);
    assert.equal(mod.resolvePython({env: env({AUDIT_PYTHON: good})}).path, good, 'beats the installed toolchain interpreter');
    // A system interpreter and a toolchain interpreter qualify, yet an invalid AUDIT_PYTHON is an error.
    assert.throws(() => mod.resolvePython({env: env({AUDIT_PYTHON: old})}), error => /AUDIT_PYTHON is set to/.test(error.message) && error.message.includes(`${old} 3.9.6`));
    assert.throws(() => mod.resolvePython({env: env({AUDIT_PYTHON: path.join(f.dir, 'missing/python3')})}), /AUDIT_PYTHON is set to .* not an executable file/);
    assert.throws(() => mod.resolvePython({env: env({AUDIT_PYTHON: 'python3'})}), /AUDIT_PYTHON must be an absolute path/);
  });
});

test('resolvePython without a qualifying interpreter throws the specified message', async () => {
  await resolverFixture('resolve-error', {system: 'old'}, async (f, mod, env) => {
    const old = f.manifest.python.systemLocations[process.platform][0];
    const bin = path.join(f.dir, 'pbin');
    await writeExecutable(path.join(bin, 'python3.12'), '#!/bin/sh\nexit 3\n');
    let message = '';
    try {
      mod.resolvePython({env: env({PATH: bin})});
    } catch (error) {
      message = error.message;
    }
    assert.equal(message, `Python 3.12 or later is required (tools/document_audit.py). Found: ${old} 3.9.6, ${path.join(bin, 'python3.12')} unusable. Run "node tools/setup-toolchain.mjs" to install a pinned interpreter into tmp/toolchains, or set AUDIT_PYTHON to an absolute path. Other interpreters under your home folder are not visible inside the ALA sandbox.`);
  });
  await resolverFixture('resolve-none', {system: 'none'}, async (_f, mod, env) => {
    assert.throws(() => mod.resolvePython({env: env()}), /Found: none\. Run "node tools\/setup-toolchain\.mjs"/);
  });
});

test('resolvePython warns about an interpreter under the home folder but outside the repository', async () => {
  await resolverFixture('resolve-home', {system: 'none'}, async (f, mod, env) => {
    const home = path.join(f.dir, 'nohome');
    const homeStub = path.join(home, 'tools/python3');
    const outside = path.join(f.dir, 'elsewhere/python3');
    const inRepo = path.join(f.repo, 'tmp/custom/python3');
    for (const file of [homeStub, outside, inRepo]) await writeExecutable(file, pythonStub('3.13.2'));

    const warned = mod.resolvePython({env: env({AUDIT_PYTHON: homeStub})});
    assert.equal(warned.path, homeStub);
    assert.equal(warned.version, '3.13.2');
    assert.match(warned.warning, /lies under your home folder .* not visible inside the ALA sandbox/);
    assert.ok(warned.warning.startsWith(homeStub));

    assert.deepEqual(mod.resolvePython({env: env({AUDIT_PYTHON: outside})}), {path: outside, version: '3.13.2'}, 'outside HOME: no warning');
    // The repository lies under this HOME, and the sandbox mounts it, so anything inside it is exempt.
    const wide = {HOME: f.dir};
    assert.deepEqual(mod.resolvePython({env: env({AUDIT_PYTHON: inRepo, ...wide})}), {path: inRepo, version: '3.13.2'}, 'inside the repository: no warning');
    assert.match(mod.resolvePython({env: env({AUDIT_PYTHON: outside, ...wide})}).warning, /under your home folder/, 'same file, wider HOME');

    // A symlink outside HOME that points into HOME is caught through its resolved target.
    const link = path.join(f.dir, 'links/python3');
    await fs.mkdir(path.dirname(link), {recursive: true});
    await fs.symlink(homeStub, link);
    assert.match(mod.resolvePython({env: env({AUDIT_PYTHON: link})}).warning, /under your home folder/);

    // A PATH interpreter under HOME is warned about as well.
    assert.match(mod.resolvePython({env: env({PATH: path.dirname(homeStub)})}).warning, /under your home folder/);
  });
});

test('the installed toolchain interpreter, which lies in the repository, never gets the home-folder warning', async () => {
  await resolverFixture('resolve-home-toolchain', {system: 'none'}, async (f, mod, env) => {
    assert.equal((await f.run(['--only', 'python', '--python'])).code, 0);
    // HOME contains the whole fixture, repository included.
    const found = mod.resolvePython({env: env({HOME: f.dir})});
    assert.equal(found.path, path.join(f.folder('python'), 'bin/python3'));
    assert.equal('warning' in found, false);
  });
});
