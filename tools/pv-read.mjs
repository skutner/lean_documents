/** The read gate of the Deep Proof process viewer: the only way the viewer touches a foreign folder.
 *
 * It reads and lists; it never writes, links, renames or follows a link. Three kinds of root exist and nothing else:
 *   1. the data root, limited to relative paths below `projects/`;
 *   2. a live work folder, admitted only by `openWorkFolder` (parent is the temporary root, exact name pattern,
 *      a real directory owned by the viewer's user);
 *   3. each dossier root.
 * A relative path is refused when it is absolute, holds `..`, an empty or `.` segment, a NUL, a backslash, or a segment
 * that starts with `keys` or equals `evaluator`. Every component is checked with `lstat`, then the file is opened
 * read-only, non-blocking and without following links (on macOS with the any-component flag, which `fs.constants` does
 * not hold; elsewhere with a check of the opened descriptor's real path). The code is written fresh; the technique is
 * the common one for reading a folder an untrusted program can write to.
 *
 * Only this module and process-viewer.mjs import node:fs, and only the functions listed here. */
import {openSync, readSync, fstatSync, lstatSync, readdirSync, closeSync, realpathSync, constants} from 'node:fs';
import path from 'node:path';
import os from 'node:os';

/** The macOS "no link in any path component" open flag. Node's `fs.constants` does not hold it. */
export const NOFOLLOW_ANY = 0x20000000;
export const WORK_FOLDER_PATTERN = /^deep-proof-verification-[A-Za-z0-9]{6}$/;
const MAX_REL = 4096;
const MAX_LIST = 50000;

/** True for a name the viewer never opens or lists: answer keys and evaluator material. */
export const refusedName = name => /^keys/i.test(name) || /^evaluator$/i.test(name);

/** Splits a relative path into segments, or returns null when the path is refused. */
export function relativeSegments(rel) {
  if (typeof rel !== 'string' || rel.length === 0 || rel.length > MAX_REL) return null;
  if (rel.includes('\0') || rel.includes('\\') || rel.startsWith('/') || path.isAbsolute(rel)) return null;
  const segments = rel.split('/');
  if (segments.some(s => s === '' || s === '.' || s === '..' || refusedName(s))) return null;
  return segments;
}

function openFlags(platform = process.platform) {
  const base = constants.O_RDONLY | (constants.O_NONBLOCK || 0);
  return platform === 'darwin' ? base | NOFOLLOW_ANY : base | (constants.O_NOFOLLOW || 0);
}

/** Proves, before the server listens and without writing anything, that the macOS flag works on this system. `/etc` is
 * a link of the system to `private/etc`: opening `/etc/hosts` with the flag must be refused with ELOOP, and opening
 * `/private/etc/hosts` with it must succeed. Neither open reads a byte. Not needed elsewhere, where it returns
 * `{skipped}`. Throws a plain message on any other result. */
export function proveNoFollow({platform = process.platform, open = openSync, close = closeSync} = {}) {
  if (platform !== 'darwin') return {skipped: 'The any-component no-follow flag is a macOS feature.'};
  const flags = openFlags('darwin');
  let refused = null;
  try {
    close(open('/etc/hosts', flags));
  } catch (error) {
    refused = error.code || 'ERROR';
  }
  if (refused !== 'ELOOP') {
    throw new Error(`The viewer cannot prove that this system refuses links when a file is opened (opening a system link gave ${refused || 'success'}, expected ELOOP). It will not start.`);
  }
  try {
    close(open('/private/etc/hosts', flags));
  } catch (error) {
    throw new Error(`The viewer cannot prove that the no-link flag lets a real path open (${error.code || 'ERROR'}). It will not start.`);
  }
  return {linkRefused: 'ELOOP', realPathOpened: true};
}

function realDirectory(folder, what) {
  if (typeof folder !== 'string' || !path.isAbsolute(folder)) throw new Error(`The ${what} folder must be an absolute path.`);
  let real;
  try {
    real = realpathSync(folder);
    if (!lstatSync(real).isDirectory()) throw new Error('not a directory');
  } catch {
    throw new Error(`The ${what} folder cannot be opened as a folder.`);
  }
  return real;
}

/** Resolves the configured folders once; the real paths become the roots. Throws a plain message when a dossier root
 * lies inside answer keys or evaluator material, or a folder is not an absolute folder. */
export function openRoots(settings = {}) {
  const roots = {data: null, tmp: null, dossiers: []};
  if (settings.deepProofData) roots.data = {kind: 'data', path: realDirectory(settings.deepProofData, 'Deep Proof data')};
  roots.tmp = {kind: 'tmp', path: realDirectory(settings.deepProofTmp || os.tmpdir(), 'temporary')};
  for (const folder of settings.dossiers || []) {
    const real = realDirectory(folder, 'dossier');
    if (real.split(path.sep).some(refusedName)) {
      throw new Error('A dossier folder must not lie inside answer keys or evaluator material. The viewer will not start.');
    }
    roots.dossiers.push({kind: 'dossier', path: real});
  }
  return roots;
}

/** A live work folder from a `workspace-created` record. Returns a root, or null when the path is not admitted. */
export function openWorkFolder(roots, absolute) {
  try {
    if (!roots?.tmp || typeof absolute !== 'string' || !path.isAbsolute(absolute) || absolute.includes('\0')) return null;
    if (path.dirname(absolute) !== roots.tmp.path || !WORK_FOLDER_PATTERN.test(path.basename(absolute))) return null;
    const st = lstatSync(absolute);
    if (!st.isDirectory() || st.isSymbolicLink()) return null;
    if (typeof process.getuid === 'function' && st.uid !== process.getuid()) return null;
    if (realpathSync(absolute) !== absolute) return null;
    return {kind: 'work', path: absolute};
  } catch {
    return null;
  }
}

/** Checks the path and each component below the root with lstat. `last` tells what the final component may be. */
function locate(root, rel, {want = 'any'} = {}) {
  if (!root || typeof root.path !== 'string') return {ok: false, reason: 'refused'};
  const segments = relativeSegments(rel);
  if (!segments) return {ok: false, reason: 'refused'};
  if (root.kind === 'data' && segments[0] !== 'projects') return {ok: false, reason: 'refused'};
  let current = root.path;
  let st = null;
  for (let i = 0; i < segments.length; i++) {
    current = path.join(current, segments[i]);
    try {
      st = lstatSync(current);
    } catch (error) {
      return {ok: false, reason: error.code === 'ENOENT' || error.code === 'ENOTDIR' ? 'missing' : 'refused'};
    }
    if (st.isSymbolicLink()) return {ok: false, reason: 'link'};
    if (i < segments.length - 1 && !st.isDirectory()) return {ok: false, reason: 'missing'};
  }
  if (want === 'file' && !st.isFile()) return {ok: false, reason: 'refused'};
  if (want === 'dir' && !st.isDirectory()) return {ok: false, reason: 'refused'};
  if (st.isFile() && st.nlink !== 1) return {ok: false, reason: 'refused'};
  if (!st.isFile() && !st.isDirectory()) return {ok: false, reason: 'refused'};
  return {ok: true, absolute: current, stat: st};
}

const kindOf = st => (st.isDirectory() ? 'dir' : 'file');

/** `{ok: true, kind, size, mtimeMs, ino}` or `{ok: false, reason}`. Metadata only; no byte is read. */
export function stat(root, rel) {
  const found = locate(root, rel);
  if (!found.ok) return found;
  return {ok: true, kind: kindOf(found.stat), size: found.stat.size, mtimeMs: found.stat.mtimeMs, ino: found.stat.ino};
}

/** Entries of a folder as `{ok, entries, reason}`. Links, devices, pipes, files with more than one name, answer keys
 * and evaluator material are left out. Entries are sorted by name. */
export function tryList(root, rel) {
  const found = locate(root, rel, {want: 'dir'});
  if (!found.ok) return {ok: false, reason: found.reason, entries: []};
  let names;
  try {
    names = readdirSync(found.absolute, {withFileTypes: true});
  } catch (error) {
    return {ok: false, reason: error.code === 'ENOENT' ? 'missing' : 'refused', entries: []};
  }
  // The folder may have been swapped for a link between the check and the listing.
  try {
    if (realpathSync(found.absolute) !== found.absolute) return {ok: false, reason: 'link', entries: []};
  } catch {
    return {ok: false, reason: 'missing', entries: []};
  }
  const entries = [];
  for (const entry of names) {
    if (entries.length >= MAX_LIST) break;
    if (refusedName(entry.name) || entry.name.includes('\0')) continue;
    if (!entry.isFile() && !entry.isDirectory()) continue;
    let st;
    try {
      st = lstatSync(path.join(found.absolute, entry.name));
    } catch {
      continue;
    }
    if (st.isSymbolicLink() || (!st.isFile() && !st.isDirectory()) || (st.isFile() && st.nlink !== 1)) continue;
    entries.push({name: entry.name, kind: kindOf(st), size: st.size, mtimeMs: st.mtimeMs});
  }
  entries.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  return {ok: true, entries};
}

/** `[{name, kind: 'file'|'dir', size, mtimeMs}]`; an empty list when the folder is missing or refused. */
export function list(root, rel) {
  return tryList(root, rel).entries;
}

/** Reads a file. Without `length`, the whole file must be at most `maxBytes`. With `length`, the window starting at
 * `offset` is returned (at most `maxBytes`), however large the file is. A file that vanishes, shrinks or grows during
 * a whole-file read gives `{ok: false, reason: 'changed'}`; a window read returns the bytes that were read. Never throws.
 * `afterOpen` is a test hook that runs between the open and the read. */
export function read(root, rel, {maxBytes = 10000000, offset = 0, length, afterOpen} = {}) {
  const found = locate(root, rel, {want: 'file'});
  if (!found.ok) return found;
  let fd = null;
  try {
    try {
      fd = openSync(found.absolute, openFlags());
    } catch (error) {
      if (error.code === 'ELOOP') return {ok: false, reason: 'link'};
      return {ok: false, reason: error.code === 'ENOENT' || error.code === 'ENOTDIR' ? 'missing' : 'refused'};
    }
    // Where the open flag cannot refuse every link, the descriptor's real path must still lie inside the root.
    if (process.platform === 'linux') {
      try {
        const real = realpathSync(`/proc/self/fd/${fd}`);
        if (real !== found.absolute && !real.startsWith(root.path + path.sep)) return {ok: false, reason: 'link'};
      } catch {
        return {ok: false, reason: 'refused'};
      }
    }
    const st = fstatSync(fd);
    if (!st.isFile() || st.nlink !== 1) return {ok: false, reason: 'refused'};
    const windowed = length !== undefined;
    if (!windowed && st.size > maxBytes) return {ok: false, reason: 'too-large'};
    const start = Math.max(0, Math.floor(Number(offset) || 0));
    const want = windowed ? Math.max(0, Math.min(Math.floor(length), maxBytes, st.size - start)) : st.size;
    if (afterOpen) afterOpen();
    const buffer = Buffer.allocUnsafe(want);
    let got = 0;
    while (got < want) {
      const n = readSync(fd, buffer, got, want - got, start + got);
      if (n <= 0) break;
      got += n;
    }
    const after = fstatSync(fd);
    if (!windowed && (got !== st.size || after.size !== st.size)) return {ok: false, reason: 'changed'};
    return {ok: true, bytes: buffer.subarray(0, got), size: windowed ? after.size : st.size, mtimeMs: after.mtimeMs, ino: st.ino};
  } catch {
    return {ok: false, reason: 'changed'};
  } finally {
    if (fd !== null) {
      try {
        closeSync(fd);
      } catch {
        // Nothing to do: the descriptor was opened read-only.
      }
    }
  }
}
