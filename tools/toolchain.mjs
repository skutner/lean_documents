/** Locations of the repository-local Lean toolchain and of a Python 3.12+ interpreter. The pins and layout live in
 * toolchain.json, which tools/toolchain.py reads as well. The installer is tools/setup-toolchain.mjs. */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(HERE, '..');
export const MANIFEST_PATH = path.join(HERE, 'toolchain.json');
export const TOOLCHAINS = 'tmp/toolchains';
// Searched on PATH after the system locations, in this order.
export const PYTHON_PATH_NAMES = ['python3', 'python3.14', 'python3.13', 'python3.12'];

const PLATFORMS = {linux: 'linux', darwin: 'darwin'};
const ARCHITECTURES = {x64: 'x64', arm64: 'arm64'};

export function loadManifest() {
  return JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
}

export function platformKey({platform, arch} = process) {
  if (!PLATFORMS[platform] || !ARCHITECTURES[arch]) throw new Error(`Unsupported platform for the Lean toolchain: ${platform}-${arch}`);
  return `${PLATFORMS[platform]}-${ARCHITECTURES[arch]}`;
}

/** The manifest entry of one component ('lean' or 'python') for a platform key, with the component's version. */
export function componentEntry(component, key, manifest = loadManifest()) {
  const entry = manifest[component]?.platforms?.[key];
  if (!entry) throw new Error(`The ${component} toolchain has no entry for the platform ${key}`);
  return {...entry, component, key, version: manifest[component].version};
}

export function toolchainFolder(root, entry) {
  return path.join(root, TOOLCHAINS, entry.folder);
}

export function markerPath(root, entry) {
  return path.join(root, TOOLCHAINS, `${entry.folder}.installed.json`);
}

export function readMarker(root, entry) {
  try {
    return JSON.parse(fs.readFileSync(markerPath(root, entry), 'utf8'));
  } catch {
    return null;
  }
}

/** A marker matches only a manifest entry that has a pin; a null pin never matches. */
export function markerMatches(marker, entry) {
  return Boolean(marker) && typeof entry.sha256 === 'string' && marker.sha256 === entry.sha256 && marker.asset === entry.asset;
}

export function leanExecutable(root, key = platformKey()) {
  return path.join(toolchainFolder(root, componentEntry('lean', key)), 'bin/lean');
}

export function pythonExecutable(root, key = platformKey()) {
  return path.join(toolchainFolder(root, componentEntry('python', key)), 'bin/python3');
}

/** `installed` means the executable exists. It does not require an install marker, because a folder placed by hand
 * (the Linux layout that predates the setup script) is a usable toolchain. */
export function leanStatus(root) {
  const key = platformKey();
  const file = leanExecutable(root, key);
  return {path: file, platform: key, installed: isExecutable(file)};
}

function isExecutable(file) {
  try {
    fs.accessSync(file, fs.constants.X_OK);
    return fs.statSync(file).isFile();
  } catch {
    return false;
  }
}

function versionAtLeast(parts, minimum) {
  for (let i = 0; i < minimum.length; i++) {
    if ((parts[i] ?? 0) !== minimum[i]) return (parts[i] ?? 0) > minimum[i];
  }
  return true;
}

/** Asks an interpreter for its version. Returns null when it cannot be run or answers unexpectedly. */
export function probePython(file, env = process.env) {
  try {
    const out = execFileSync(file, ['-I', '-c', 'import sys; print(".".join(str(n) for n in sys.version_info[:3]))'], {
      env, encoding: 'utf8', timeout: 15000, stdio: ['ignore', 'pipe', 'ignore']
    }).trim();
    const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(out);
    return match ? {path: file, version: out, parts: match.slice(1).map(Number)} : null;
  } catch {
    return null;
  }
}

export function systemPythonLocations(manifest = loadManifest(), platform = process.platform) {
  return manifest.python?.systemLocations?.[platform] ?? [];
}

/** The first system-location interpreter at the minimum version, or null. `PATH` is deliberately not searched:
 * the ALA sandbox may not see it. `tried` lists every existing candidate as 'path version' or 'path unusable'. */
export function findSystemPython({env = process.env, minimum = [3, 12], manifest = loadManifest(), platform = process.platform} = {}) {
  const tried = [];
  for (const file of systemPythonLocations(manifest, platform)) {
    if (!isExecutable(file)) continue;
    const probe = probePython(file, env);
    tried.push(probe ? `${file} ${probe.version}` : `${file} unusable`);
    if (probe && versionAtLeast(probe.parts, minimum)) return {path: file, version: probe.version, tried};
  }
  return {path: null, version: null, tried};
}

function pathCandidates(env) {
  const dirs = (env.PATH || '').split(path.delimiter).filter(dir => path.isAbsolute(dir));
  // Names keep their listed order; directories keep their PATH order within one name.
  return PYTHON_PATH_NAMES.flatMap(name => dirs.map(dir => path.join(dir, name)));
}

function realOrSame(file) {
  try {
    return fs.realpathSync(file);
  } catch {
    return path.resolve(file);
  }
}

function isInside(file, folder) {
  const relative = path.relative(folder, file);
  return relative !== '' && !relative.startsWith('..') && !path.isAbsolute(relative);
}

/** A warning when the interpreter lies under the home folder but outside this repository. The ALA sandbox does not
 * show the home folder, only the repository, so such an interpreter works on the host and fails for the agent.
 * Both the given path and its resolved target are checked. */
function homeWarning(file, env) {
  const home = realOrSame(env.HOME || os.homedir());
  const repository = realOrSame(ROOT);
  for (const candidate of new Set([path.resolve(file), realOrSame(file)])) {
    if (isInside(candidate, home) && !isInside(candidate, repository)) {
      return `${file} lies under your home folder (${home}), which is not visible inside the ALA sandbox, so the audit agent cannot run it. Use a system interpreter, or run "node tools/setup-toolchain.mjs" to install one into tmp/toolchains.`;
    }
  }
  return null;
}

function pythonError(minimum, found, prefix = '') {
  const wanted = minimum.join('.');
  return new Error(`${prefix}Python ${wanted} or later is required (tools/document_audit.py). Found: ${found.length ? found.join(', ') : 'none'}. Run "node tools/setup-toolchain.mjs" to install a pinned interpreter into tmp/toolchains, or set AUDIT_PYTHON to an absolute path. Other interpreters under your home folder are not visible inside the ALA sandbox.`);
}

/** Candidate order: AUDIT_PYTHON (authoritative), the installed toolchain interpreter whose marker matches the
 * manifest, the system locations, then PATH. The first interpreter at the minimum version wins; the path is absolute.
 * An interpreter under the home folder (env.HOME) but outside the repository is returned with a `warning` string:
 * it works on the host but is invisible inside the ALA sandbox. The caller decides how to surface it. */
export function resolvePython({env = process.env, minimum = [3, 12]} = {}) {
  const manifest = loadManifest();
  const found = [];
  const seen = new Set();
  const qualifies = file => {
    if (seen.has(file)) return null;
    seen.add(file);
    if (!isExecutable(file)) return null;
    const probe = probePython(file, env);
    found.push(probe ? `${file} ${probe.version}` : `${file} unusable`);
    return probe && versionAtLeast(probe.parts, minimum) ? {path: file, version: probe.version} : null;
  };
  // `warning` is present only when the interpreter is under the home folder (see homeWarning).
  const finish = hit => {
    const warning = homeWarning(hit.path, env);
    return warning ? {...hit, warning} : hit;
  };

  if (env.AUDIT_PYTHON) {
    const file = env.AUDIT_PYTHON;
    if (!path.isAbsolute(file)) throw pythonError(minimum, [], `AUDIT_PYTHON must be an absolute path, got "${file}". `);
    const hit = qualifies(file);
    if (hit) return finish(hit);
    if (!found.length) found.push(`${file} not an executable file`);
    throw pythonError(minimum, found, `AUDIT_PYTHON is set to "${file}", which cannot be used. `);
  }

  let key = null;
  try {
    key = platformKey();
  } catch {
    // No pinned interpreter exists for this platform; the other candidates still apply.
  }
  if (key) {
    const entry = componentEntry('python', key, manifest);
    if (markerMatches(readMarker(ROOT, entry), entry)) {
      const hit = qualifies(path.join(toolchainFolder(ROOT, entry), 'bin/python3'));
      if (hit) return finish(hit);
    }
  }
  for (const file of systemPythonLocations(manifest)) {
    const hit = qualifies(file);
    if (hit) return finish(hit);
  }
  for (const file of pathCandidates(env)) {
    const hit = qualifies(file);
    if (hit) return finish(hit);
  }
  throw pythonError(minimum, found);
}
