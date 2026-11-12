#!/usr/bin/env node
/** Installs the pinned Lean toolchain, and a pinned standalone Python when the host has no usable system Python,
 * into tmp/toolchains. Pins, assets and folder names come from tools/toolchain.json (shared with toolchain.mjs).
 *
 *   node tools/setup-toolchain.mjs [--check] [--only lean|python] [--python] [--platform <key>] [--source <url-or-file>]
 *
 * Exit codes: 0 installed and verified, or already installed (no network); 1 --check and something is missing or
 * unverified; 2 usage error, unsupported platform, unpinned asset, or Node without built-in zstd; 3 download failed;
 * 4 SHA-256 mismatch (nothing installed); 5 unpacking, self-check or install-state failure.
 *
 * Order of work: arguments, manifest, component choice, pin validation, destination state, zstd check, and only then
 * the staging folder, download, SHA-256, unpack, top-level check, self-check (still in staging), atomic rename, marker.
 */
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import zlib from 'node:zlib';
import crypto from 'node:crypto';
import {spawn, execFile} from 'node:child_process';
import {pipeline} from 'node:stream/promises';
import {Readable, Transform} from 'node:stream';
import {promisify} from 'node:util';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {
  ROOT, TOOLCHAINS, loadManifest, platformKey, componentEntry, toolchainFolder, markerPath, readMarker, markerMatches,
  findSystemPython
} from './toolchain.mjs';

const execFileAsync = promisify(execFile);
const USAGE = 'Usage: node tools/setup-toolchain.mjs [--check] [--only lean|python] [--python] [--platform <key>] [--source <url-or-file>]';
const TAR = ['/usr/bin/tar', '/bin/tar'].find(file => fs.existsSync(file)) ?? 'tar';
const DOWNLOAD_TIMEOUT_MS = 30 * 60 * 1000;
const SELF_CHECK_TIMEOUT_MS = 120 * 1000;

class Exit extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

export function parseArguments(argv) {
  const args = {check: false, python: false, only: null, platform: null, source: null};
  const valued = new Set(['--only', '--platform', '--source']);
  for (let i = 0; i < argv.length; i++) {
    const [name, inline] = argv[i].startsWith('--') && argv[i].includes('=') ? [argv[i].slice(0, argv[i].indexOf('=')), argv[i].slice(argv[i].indexOf('=') + 1)] : [argv[i], null];
    if (name === '--check' || name === '--python') {
      if (inline !== null) throw new Exit(2, `${name} takes no value, got "${argv[i]}".\n${USAGE}`);
      args[name.slice(2)] = true;
    } else if (valued.has(name)) {
      const value = inline ?? argv[++i];
      if (value === undefined || value === '') throw new Exit(2, `${name} needs a value.\n${USAGE}`);
      args[name.slice(2)] = value;
    } else throw new Exit(2, `Unknown argument: ${argv[i]}\n${USAGE}`);
  }
  if (args.only !== null && !['lean', 'python'].includes(args.only)) throw new Exit(2, `--only takes lean or python, not "${args.only}".\n${USAGE}`);
  if (args.python && args.only === 'lean') throw new Exit(2, `--python cannot be combined with --only lean.\n${USAGE}`);
  if (args.check && args.source) throw new Exit(2, `--source has no meaning with --check.\n${USAGE}`);
  return args;
}

function assetKind(asset) {
  if (asset.endsWith('.tar.zst')) return 'zst';
  if (asset.endsWith('.tar.gz')) return 'gz';
  return null;
}

/** Why an entry cannot be downloaded, or null. Placeholders and a null pin are the unpinned state of the manifest. */
function pinProblem(entry) {
  const label = `${entry.component} (${entry.key})`;
  const placeholder = [entry.version, entry.asset, entry.url, entry.folder].find(v => /[<>]/.test(String(v)));
  if (placeholder !== undefined) return `${label}: tools/toolchain.json still has a placeholder ("${placeholder}"), so the asset is not pinned. Pins are added once the release digests are fetched; nothing was downloaded.`;
  if (entry.sha256 === null || entry.sha256 === undefined) return `${label}: the SHA-256 pin in tools/toolchain.json is null, so the asset will not be downloaded. Pins are added once the release digests are fetched; nothing was downloaded.`;
  if (!/^[0-9a-f]{64}$/.test(entry.sha256)) return `${label}: the SHA-256 pin in tools/toolchain.json is not 64 lowercase hexadecimal digits.`;
  if (!assetKind(entry.asset)) return `${label}: unsupported asset type "${entry.asset}" (expected .tar.zst or .tar.gz).`;
  if (!entry.topLevel || entry.topLevel.includes('/') || entry.topLevel.startsWith('.')) return `${label}: tools/toolchain.json has no usable topLevel folder name.`;
  return null;
}

function isExecutableFile(file) {
  try {
    fs.accessSync(file, fs.constants.X_OK);
    return fs.statSync(file).isFile();
  } catch {
    return false;
  }
}

function binaryOf(entry) {
  return entry.component === 'lean' ? 'bin/lean' : 'bin/python3';
}

/** ok: installed with a matching marker. missing: nothing there. conflict: a folder that is not ours to replace. */
function installState(entry) {
  const folder = toolchainFolder(ROOT, entry);
  let exists = true;
  try {
    fs.lstatSync(folder);
  } catch {
    exists = false;
  }
  if (!exists) return {state: 'missing', detail: 'not installed'};
  const marker = readMarker(ROOT, entry);
  if (!marker) return {state: 'conflict', detail: `${folder} exists without an install marker (${markerPath(ROOT, entry)}); it will not be overwritten. Move or remove it by hand to let the script install the pinned build`};
  if (!markerMatches(marker, entry)) return {state: 'conflict', detail: `${folder} was installed from a different pin (marker asset ${marker.asset}, sha256 ${marker.sha256}); it will not be overwritten. Move or remove it and its marker by hand to install the current pin`};
  if (!isExecutableFile(path.join(folder, binaryOf(entry)))) return {state: 'conflict', detail: `${folder} has a matching marker but ${binaryOf(entry)} is missing or not executable; move or remove it by hand to reinstall`};
  return {state: 'ok', detail: `installed (${marker.versionLine ?? marker.version})`};
}

async function chooseComponents(args, manifest, key) {
  const wantLean = args.only !== 'python';
  const out = [];
  const notes = [];
  if (wantLean) out.push(componentEntry('lean', key, manifest));
  if (args.only !== 'lean') {
    if (args.python) out.push(componentEntry('python', key, manifest));
    else {
      const system = findSystemPython({manifest, minimum: manifest.python.minimum});
      if (system.path) notes.push(`python (${key}): not needed, the system interpreter ${system.path} ${system.version} qualifies. Use --python to install the pinned interpreter anyway.`);
      else {
        out.push(componentEntry('python', key, manifest));
        notes.push(`python (${key}): no system interpreter at ${manifest.python.minimum.join('.')} or later in the resolver's system locations (found: ${system.tried.join(', ') || 'none'}); a PATH-only interpreter does not count.`);
      }
    }
  }
  return {entries: out, notes};
}

// Locations

const ASSET_NAME = /\.tar\.(zst|gz)$/;

/** Checks a --source value before anything runs (exit 2) and says whether it names one single asset (a file, a file
 * URL or a URL ending in an asset name) rather than a folder or a base URL that holds the assets by name. */
function inspectSource(source) {
  if (!source) return {single: false};
  if (/^https?:\/\//i.test(source)) {
    let url;
    try {
      url = new URL(source);
    } catch {
      throw new Exit(2, `--source "${source}" is not a valid URL.\n${USAGE}`);
    }
    if (!url.hostname) throw new Exit(2, `--source "${source}" has no host.\n${USAGE}`);
    return {single: ASSET_NAME.test(decodeURIComponent(url.pathname))};
  }
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(source) && !source.startsWith('file:')) throw new Exit(2, `--source takes an http(s) URL, a file: URL or a local path, not "${source}".\n${USAGE}`);
  let local;
  try {
    local = source.startsWith('file:') ? fileURLToPath(source) : path.resolve(source);
  } catch {
    throw new Exit(2, `--source "${source}" is not a valid file URL.\n${USAGE}`);
  }
  try {
    return {single: !fs.statSync(local).isDirectory()};
  } catch {
    return {single: false};
  }
}

function resolveLocation(entry, source) {
  if (!source) return entry.url.startsWith('file:') ? {file: fileURLToPath(entry.url)} : urlLocation(entry.url);
  if (/^https?:\/\//i.test(source)) {
    const url = new URL(source);
    return ASSET_NAME.test(decodeURIComponent(url.pathname))
      ? {url: url.href}
      : {url: `${source.replace(/\/+$/, '')}/${encodeURIComponent(entry.asset)}`};
  }
  const local = source.startsWith('file:') ? fileURLToPath(source) : path.resolve(source);
  let stat;
  try {
    stat = fs.statSync(local);
  } catch (error) {
    throw new Exit(3, `Download failed: ${local}: ${error.code ?? error.message}`);
  }
  return {file: stat.isDirectory() ? path.join(local, entry.asset) : local};
}

function urlLocation(url) {
  if (!/^https?:\/\//i.test(url)) throw new Exit(2, `Unsupported download URL "${url}" in tools/toolchain.json (expected http, https or file).`);
  return {url};
}

function hashingTap() {
  const hash = crypto.createHash('sha256');
  let bytes = 0;
  const tap = new Transform({
    transform(chunk, _encoding, callback) {
      hash.update(chunk);
      bytes += chunk.length;
      callback(null, chunk);
    }
  });
  return {tap, digest: () => hash.digest('hex'), size: () => bytes};
}

/** Streams the asset into `destination` and returns its SHA-256 over exactly the bytes written. */
async function download(location, destination) {
  const {tap, digest, size} = hashingTap();
  const describe = location.url ?? location.file;
  try {
    let input;
    if (location.url) {
      const response = await fetch(location.url, {redirect: 'follow', signal: AbortSignal.timeout(DOWNLOAD_TIMEOUT_MS)});
      if (!response.ok) throw new Exit(3, `Download failed: HTTP ${response.status} for ${location.url}`);
      if (!response.body) throw new Exit(3, `Download failed: empty response for ${location.url}`);
      input = Readable.fromWeb(response.body);
    } else input = fs.createReadStream(location.file);
    await pipeline(input, tap, fs.createWriteStream(destination));
  } catch (error) {
    if (error instanceof Exit) throw error;
    throw new Exit(3, `Download failed: ${describe}: ${error.cause?.code ?? error.code ?? error.message}`);
  }
  return {sha256: digest(), bytes: size(), source: describe};
}

// Unpacking and self-check

async function unpack(archive, kind, destination) {
  await fsp.mkdir(destination);
  let decompressor;
  if (kind === 'zst') decompressor = zlib.createZstdDecompress();
  else decompressor = zlib.createGunzip();
  // tar gets a fixed minimal environment, never the caller's: GNU tar reads TAR_OPTIONS, which can switch off its path
  // protections. --no-same-owner keeps owners and set-id bits from the archive out of the install when run as root.
  const tar = spawn(TAR, ['-xf', '-', '--no-same-owner'], {cwd: destination, stdio: ['pipe', 'ignore', 'pipe'], env: {PATH: '/usr/bin:/bin', LC_ALL: 'C', COPYFILE_DISABLE: '1'}});
  let stderr = '';
  tar.stderr.on('data', chunk => { stderr += chunk; });
  const closed = new Promise(resolve => {
    tar.on('error', error => resolve({error}));
    tar.on('close', (code, signal) => resolve({code, signal}));
  });
  let streamError = null;
  try {
    await pipeline(fs.createReadStream(archive), decompressor, tar.stdin);
  } catch (error) {
    streamError = error;
    tar.kill();
  }
  const result = await closed;
  if (result.error) throw new Exit(5, `Unpacking failed: could not run tar: ${result.error.message}`);
  if (streamError && streamError.code !== 'EPIPE') throw new Exit(5, `Unpacking failed: ${streamError.message}`);
  if (result.code !== 0 || streamError) throw new Exit(5, `Unpacking failed: tar exited with ${result.code ?? result.signal}${stderr.trim() ? `: ${stderr.trim().split('\n').slice(-3).join(' ')}` : ''}`);
}

async function requireTopLevel(destination, expected) {
  const names = (await fsp.readdir(destination)).sort();
  if (names.length !== 1 || names[0] !== expected) throw new Exit(5, `The archive must contain exactly the top-level folder "${expected}"; found: ${names.length ? names.join(', ') : 'nothing'}.`);
  const stat = await fsp.lstat(path.join(destination, expected));
  if (!stat.isDirectory()) throw new Exit(5, `The top-level entry "${expected}" of the archive is not a folder.`);
}

async function run(file, args, options = {}) {
  try {
    const {stdout} = await execFileAsync(file, args, {timeout: SELF_CHECK_TIMEOUT_MS, encoding: 'utf8', ...options});
    return stdout.trim();
  } catch (error) {
    const detail = String(error.stderr || error.message).trim().split('\n').slice(-3).join(' ');
    throw new Exit(5, `Self-check failed: ${[file, ...args].join(' ')}: ${detail}`);
  }
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Runs the installed interpreter or compiler from where it is staged and returns its version line. */
async function selfCheck(entry, directory) {
  const binary = path.join(directory, binaryOf(entry));
  if (!isExecutableFile(binary)) throw new Exit(5, `Self-check failed: ${binaryOf(entry)} is missing or not executable in the unpacked ${entry.component} toolchain.`);
  const line = await run(binary, ['--version']);
  if (entry.component === 'lean') {
    if (!new RegExp(`^Lean \\(version ${escapeRegExp(entry.version)}[,)]`).test(line)) throw new Exit(5, `Self-check failed: "lean --version" printed "${line}", expected version ${entry.version}.`);
  } else {
    if (line !== `Python ${entry.version}`) throw new Exit(5, `Self-check failed: "python3 --version" printed "${line}", expected "Python ${entry.version}".`);
    const helper = path.join(ROOT, 'tools/document_audit.py');
    if (fs.existsSync(helper)) await run(binary, [helper, '--help'], {cwd: ROOT, env: {...process.env, PYTHONDONTWRITEBYTECODE: '1'}});
    else console.log(`  note: ${helper} is not present, so "document_audit.py --help" was not run.`);
  }
  return line;
}

// Installation

async function removeStaging(directory) {
  const base = path.join(ROOT, TOOLCHAINS) + path.sep + '.staging-';
  if (!directory || !directory.startsWith(base)) throw new Error(`Refusing to remove ${directory}: not a staging folder`);
  await fsp.rm(directory, {recursive: true, force: true});
}

async function install(entry, source) {
  const kind = assetKind(entry.asset);
  const toolchains = path.join(ROOT, TOOLCHAINS);
  const location = resolveLocation(entry, source);
  await fsp.mkdir(toolchains, {recursive: true});
  const staging = await fsp.mkdtemp(path.join(toolchains, '.staging-'));
  const final = toolchainFolder(ROOT, entry);
  let moved = false;
  try {
    console.log(`${entry.component} (${entry.key}): downloading ${location.url ?? location.file}`);
    const archive = path.join(staging, 'asset' + (kind === 'zst' ? '.tar.zst' : '.tar.gz'));
    const got = await download(location, archive);
    if (got.sha256 !== entry.sha256) throw new Exit(4, `SHA-256 mismatch for ${entry.asset}: expected ${entry.sha256}, got ${got.sha256}. Nothing was installed.`);
    console.log(`${entry.component} (${entry.key}): SHA-256 verified (${got.bytes} bytes), unpacking`);
    const unpacked = path.join(staging, 'unpacked');
    await unpack(archive, kind, unpacked);
    await requireTopLevel(unpacked, entry.topLevel);
    const staged = path.join(unpacked, entry.topLevel);
    const versionLine = await selfCheck(entry, staged);
    try {
      await fsp.rename(staged, final);
    } catch (error) {
      throw new Exit(5, `Could not move the toolchain into place at ${final}: ${error.code ?? error.message}`);
    }
    moved = true;
    const marker = {
      schema: 1, component: entry.component, key: entry.key, asset: entry.asset, url: entry.url, source: got.source,
      sha256: entry.sha256, version: entry.version, versionLine, installedAt: new Date().toISOString()
    };
    const temporary = `${markerPath(ROOT, entry)}.tmp-${process.pid}`;
    await fsp.writeFile(temporary, JSON.stringify(marker, null, 2) + '\n');
    await fsp.rename(temporary, markerPath(ROOT, entry));
    console.log(`${entry.component} (${entry.key}): installed ${versionLine} at ${final}`);
  } catch (error) {
    if (moved) {
      // The folder must not stay behind without a marker, because that would block every later run.
      try {
        await fsp.rename(final, path.join(staging, 'rolled-back'));
      } catch {
        // Reported below through the original error.
      }
    }
    throw error instanceof Exit ? error : new Exit(5, `Installing ${entry.component} failed: ${error.message}`);
  } finally {
    // A cleanup failure must not replace the real outcome (an Exit thrown above), so it is only reported.
    try {
      await removeStaging(staging);
    } catch (error) {
      console.error(`Warning: could not remove the staging folder ${staging}: ${error.message}`);
    }
  }
}

// Main

export async function main(argv) {
  const args = parseArguments(argv);
  inspectSource(args.source);
  const manifest = loadManifest();
  let key;
  try {
    key = args.platform ?? platformKey();
  } catch (error) {
    throw new Exit(2, error.message);
  }
  if (!manifest.lean.platforms[key]) throw new Exit(2, `Unsupported platform for the Lean toolchain: ${key}. Known keys: ${Object.keys(manifest.lean.platforms).join(', ')}`);

  const {entries, notes} = await chooseComponents(args, manifest, key);
  for (const note of notes) console.log(note);
  if (!entries.length) {
    console.log('Nothing to do.');
    return 0;
  }

  if (args.check) {
    let missing = 0;
    for (const entry of entries) {
      const problem = pinProblem(entry);
      const state = installState(entry);
      const ok = state.state === 'ok';
      if (!ok) missing++;
      console.log(`${entry.component} (${entry.key}): ${ok ? '' : state.state === 'conflict' ? 'unverified: ' : 'missing: '}${state.detail}`);
      if (!ok && problem) console.log(`  not installable yet - ${problem}`);
    }
    return missing ? 1 : 0;
  }

  const problems = entries.map(pinProblem).filter(Boolean);
  if (problems.length) throw new Exit(2, problems.join('\n'));

  const states = entries.map(entry => ({entry, ...installState(entry)}));
  const conflicts = states.filter(s => s.state === 'conflict');
  if (conflicts.length) throw new Exit(5, conflicts.map(s => `${s.entry.component} (${s.entry.key}): ${s.detail}`).join('\n'));

  const pending = states.filter(s => s.state === 'missing').map(s => s.entry);
  for (const s of states.filter(s => s.state === 'ok')) console.log(`${s.entry.component} (${s.entry.key}): already installed, ${s.detail}; no network used`);
  if (pending.some(entry => assetKind(entry.asset) === 'zst') && typeof zlib.createZstdDecompress !== 'function') {
    throw new Exit(2, `This Node.js (${process.version}) has no built-in zstd (zlib.createZstdDecompress); use Node 24 or later to unpack .tar.zst assets.`);
  }
  if (pending.length > 1 && inspectSource(args.source).single) {
    throw new Exit(2, `--source "${args.source}" names one asset, but ${pending.map(e => e.component).join(' and ')} are both pending, and the same bytes would be used for each. Add --only lean or --only python, or give a folder or base URL that holds the assets by name. Nothing was downloaded.`);
  }
  for (const entry of pending) await install(entry, args.source);
  return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main(process.argv.slice(2)).then(
    code => { process.exitCode = code; },
    error => {
      if (error instanceof Exit) {
        console.error(error.message);
        process.exitCode = error.code;
      } else {
        console.error(error.stack || String(error));
        process.exitCode = 70;
      }
    }
  );
}
