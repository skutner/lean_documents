/** The dedicated agent home of the pilot. Every pilot path that starts a coding agent passes ALA `--home` with a fresh
 * folder made for that one process. The folder holds only a copy of the selected agent's login, taken from a template
 * that only `tools/setup-agent-home.mjs login` writes, and it is destroyed afterwards. Nothing a run wrote ever goes
 * back to the template. The user's own agent folders (~/.codex, ~/.claude, ...) are never used.
 *
 * Layout under the base (AUDIT_AGENT_HOME, or ~/.lean-documents/agent-home), every folder 0700 and every file 0600:
 *   template/        the login files ALA names, nothing else
 *   homes/           home-<server pid>-<16 hex> while in use, gone-<same suffix> while being removed,
 *                    home-<suffix>.owner beside each (never inside it)
 *   staging/         login-<pid>-<16 hex> scratch homes of a sign-in
 *   template.lock    the owner's pid; it appears with its content already in it
 *
 * Removal of a used home never walks or repairs it by path in this process on macOS: clearing flags, restoring owner
 * access and removing run in a child confined by Seatbelt to the folder itself, as ALA's own cleanup does. */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawn, spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {alaEnvironment} from './ala.mjs';

export const FILE_LIMIT = 16 * 1024 * 1024;
export const GROUP_WAIT_MS = 5000;
export const LOCK_WAIT_MS = 10000;
// How long an interrupted sign-in command may take to end after the forwarded signal before it is killed.
export const SIGN_IN_GRACE_MS = 5000;
const LOGIN_FUNCTIONS = ['homeEnvironment', 'codingAgentLoginFiles', 'detectCodingAgentLogin', 'detectCodingAgentLogins'];
const HOME_NAME = /^home-(\d+)-([0-9a-f]{16})$/;
const GONE_NAME = /^gone-(\d+)-([0-9a-f]{16})$/;
const OWNER_NAME = /^home-(\d+)-([0-9a-f]{16})\.owner(?:\.[0-9a-f]{8}\.tmp)?$/;
const LOGIN_NAME = /^login-(\d+)-([0-9a-f]{16})(?:-adopt)?$/;
const AGENT_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

// ---------- Fixed texts ----------

export function noLogin(agent, base) {
  return `No pilot login for ${agent}. The pilot never uses your own ${agent} folder; it starts ${agent} with a dedicated agent home under ${base}. Sign in there once: node tools/setup-agent-home.mjs login ${agent} -- <sign-in command>. Or start the server with a credential variable that ALA passes to ${agent}. See tools/README.md, "Dedicated agent home".`;
}

export function homeUnavailable(reason) {
  return `The dedicated agent home could not be prepared, so no coding agent was started: ${reason}`;
}

/** The reasons of `homeUnavailable`. `notPrivate` also serves files, with kind 'file'. */
export const reasons = Object.freeze({
  relativeBase: value => `AUDIT_AGENT_HOME must be an absolute path: ${value}`,
  insideRepository: (base, root) => `${base} lies inside the repository, which every run can read. Choose a folder outside ${root}.`,
  insideRuntime: (base, runtime) => `${base} lies inside ${runtime}, the Node installation that ALA makes readable to every run. Choose a folder outside it.`,
  notPrivate: (file, found, kind = 'folder') => `${file} must be a real ${kind} owned by you with no access for others; found ${found}.`,
  foreign: (relative, kind) => `the template holds something other than a login: ${relative} (${kind}). Only the login files ALA names may be there. Remove it by hand (login and logout refuse a template in this state), then sign in again if needed with node tools/setup-agent-home.mjs login <agent> -- <sign-in command>.`,
  tooLarge: relative => `${relative} in the template is larger than 16777216 bytes.`,
  noModule: () => 'this ALA has no src/coding-agents/logins.mjs with homeEnvironment, codingAgentLoginFiles, detectCodingAgentLogin and detectCodingAgentLogins; update ALA.',
  locked: (pid, lock) => `the template is locked by process ${pid}. If no sign-in is running, remove ${lock}.`
});

/** `kind` is 'unavailable', 'module', 'locked', 'noLogin', 'unknownAgent', 'usage', 'signIn', 'noFile' or
 * 'interrupted' (then `signal` names the signal); `status` is the HTTP status a server answers; `reason` is the bare
 * reason of a `homeUnavailable` text. */
export class AgentHomeError extends Error {
  constructor(message, kind, status, reason = null, signal = null) {
    super(message);
    this.kind = kind;
    this.status = status;
    this.reason = reason;
    this.signal = signal;
  }
}

const unavailable = (reason, kind = 'unavailable') => new AgentHomeError(homeUnavailable(reason), kind, 503, reason);

// ---------- Locations ----------

/** `{base, explicit}`. An explicit AUDIT_AGENT_HOME is authoritative, as ALA_CLI and AUDIT_PYTHON are. Only the default
 * location may have its parent (~/.lean-documents) created. */
export function agentHomeLocation({env = process.env, home = os.homedir()} = {}) {
  const value = env.AUDIT_AGENT_HOME;
  if (value) {
    if (!path.isAbsolute(value)) throw unavailable(reasons.relativeBase(value));
    return {base: path.resolve(value), explicit: true};
  }
  return {base: path.join(home, '.lean-documents', 'agent-home'), explicit: false};
}

export function resolveAgentHomeBase(options = {}) {
  return agentHomeLocation(options).base;
}

/** The real path of a path that may not exist yet: the nearest existing ancestor resolved, the rest appended. The
 * native call also gives the letter case the volume stores, which Seatbelt rules compare against. */
export function realLocation(file) {
  const rest = [];
  let current = path.resolve(file);
  for (;;) {
    try {
      return path.join(fs.realpathSync.native(current), ...rest);
    } catch {
      const parent = path.dirname(current);
      if (parent === current) return path.resolve(file);
      rest.unshift(path.basename(current));
      current = parent;
    }
  }
}

/** The path with its ancestors resolved and its last component kept, so a link at the path itself stays visible. */
function canonicalParent(file) {
  const resolved = path.resolve(file);
  return path.join(realLocation(path.dirname(resolved)), path.basename(resolved));
}

function identity(file) {
  try {
    const metadata = fs.statSync(file);
    return `${metadata.dev}:${metadata.ino}`;
  } catch {
    return null;
  }
}

/** Whether `file` is `folder` or lies inside it, compared by device and inode of every ancestor, so neither a link nor
 * a different letter case on a case-insensitive volume hides it. */
export function liesWithin(file, folder) {
  const target = identity(folder);
  if (!target) return false;
  let current = realLocation(file);
  for (;;) {
    if (identity(current) === target) return true;
    const parent = path.dirname(current);
    if (parent === current) return false;
    current = parent;
  }
}

/** The folder ALA grants read access to for the Node that runs it (ALA's collectAgentRuntimeMounts for Node). */
export function nodeRuntimeRoot(execPath = process.execPath) {
  const real = realLocation(execPath);
  const marker = `${path.sep}lib${path.sep}node_modules${path.sep}`;
  if (real.includes(marker)) return real.slice(0, real.indexOf(marker));
  if (path.basename(real) === 'node' && path.basename(path.dirname(real)) === 'bin') return path.dirname(path.dirname(real));
  return path.dirname(real);
}

// ---------- Private folders and files ----------

function kindOf(metadata) {
  if (metadata.isSymbolicLink()) return 'link';
  if (metadata.isDirectory()) return 'folder';
  if (metadata.isFile()) return 'file';
  if (metadata.isFIFO()) return 'FIFO';
  if (metadata.isSocket()) return 'socket';
  return 'device';
}

function privacyProblem(metadata) {
  if (metadata.uid !== process.getuid()) return `owner uid ${metadata.uid}`;
  if ((metadata.mode & 0o077) !== 0) return `mode 0${(metadata.mode & 0o777).toString(8)}`;
  return null;
}

/** Confirms that `folder` is a real folder of this user with no group or other access, creating it with mode 0700
 * first when `create` is set. Returns false when it is absent and not created. */
export function checkPrivateFolder(folder, {create = false} = {}) {
  if (create) {
    try {
      fs.mkdirSync(folder, {mode: 0o700});
      fs.chmodSync(folder, 0o700); // the umask must not decide the mode
    } catch (error) {
      if (error.code !== 'EEXIST') throw unavailable(reasons.notPrivate(folder, `an error (${error.code})`));
    }
  }
  let metadata;
  try {
    metadata = fs.lstatSync(folder);
  } catch (error) {
    if (error.code === 'ENOENT' && !create) return false;
    throw unavailable(reasons.notPrivate(folder, `an error (${error.code})`));
  }
  if (!metadata.isDirectory()) throw unavailable(reasons.notPrivate(folder, `a ${kindOf(metadata)}`));
  const problem = privacyProblem(metadata);
  if (problem) throw unavailable(reasons.notPrivate(folder, problem));
  return true;
}

/** The spelling the volume stores for a base that `lstat` confirms is a real folder: on a case- or
 * normalization-insensitive volume `<x>/casebase` reaches `<x>/CaseBase`, and the exact-path rule of the removal
 * routine and Seatbelt's TARGET compare against the stored form. Anything else (absent, a link) is returned unchanged. */
function storedSpelling(location) {
  try {
    if (!fs.lstatSync(location).isDirectory()) return location;
    return fs.realpathSync.native(location);
  } catch {
    return location;
  }
}

/** HD-6 for a base: not inside the repository, not inside Node's runtime root, and a private folder, created when
 * `create` is set (its parent only for the default location, `explicit: false`). Returns {base, exists}: the base with
 * its ancestors resolved, and once it exists as a real folder, in the spelling the volume stores. Used by
 * createAgentHomes. */
export function prepareBase({base, root, explicit = true, create = false}) {
  if (typeof base !== 'string' || !path.isAbsolute(base)) throw unavailable(reasons.relativeBase(base));
  const location = canonicalParent(base);
  const repository = realLocation(root);
  if (liesWithin(location, repository)) throw unavailable(reasons.insideRepository(location, repository));
  const runtime = nodeRuntimeRoot();
  if (liesWithin(location, runtime)) throw unavailable(reasons.insideRuntime(location, runtime));
  if (create && !explicit) checkPrivateFolder(path.dirname(location), {create: true});
  const exists = checkPrivateFolder(location, {create});
  return {base: exists ? storedSpelling(location) : location, exists};
}

/** Reads a regular file without following a link and without blocking on a FIFO, up to `limit` bytes. Returns
 * {state: 'file', data} or {state: 'missing' | 'link' | 'special' | 'large' | 'unreadable'}. */
export function readBounded(file, limit = FILE_LIMIT) {
  let fd;
  try {
    fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  } catch (error) {
    if (error.code === 'ENOENT' || error.code === 'ENOTDIR') return {state: 'missing'};
    return {state: error.code === 'ELOOP' ? 'link' : 'unreadable'};
  }
  try {
    const metadata = fs.fstatSync(fd);
    if (!metadata.isFile()) return {state: 'special'};
    if (metadata.size > limit) return {state: 'large'};
    const chunks = [];
    const chunk = Buffer.alloc(65536);
    let total = 0;
    for (;;) {
      const count = fs.readSync(fd, chunk, 0, chunk.length, null);
      if (count === 0) break;
      total += count;
      if (total > limit) return {state: 'large'};
      chunks.push(Buffer.from(chunk.subarray(0, count)));
    }
    return {state: 'file', data: Buffer.concat(chunks), uid: metadata.uid, mode: metadata.mode};
  } catch {
    return {state: 'unreadable'};
  } finally {
    fs.closeSync(fd);
  }
}

/** `relative` below `root`, only when every folder between them is a real folder ([delta 2]): a link or anything else
 * on the way could lead outside, so nothing below it may be read, adopted or unlinked by path. Returns
 * {state: 'ok', file}, {state: 'missing'} when a folder on the way is absent, or {state: 'blocked', at} naming the
 * first one that is not a real folder. */
function belowRealFolders(root, relative) {
  const parts = relative.split(path.sep);
  let folder = root;
  for (const part of parts.slice(0, -1)) {
    folder = path.join(folder, part);
    let metadata;
    try {
      metadata = fs.lstatSync(folder);
    } catch (error) {
      if (error.code === 'ENOENT') return {state: 'missing'};
      return {state: 'blocked', at: path.relative(root, folder)};
    }
    if (!metadata.isDirectory()) return {state: 'blocked', at: path.relative(root, folder)};
  }
  return {state: 'ok', file: path.join(folder, parts.at(-1))};
}

const sha256 = data => crypto.createHash('sha256').update(data).digest('hex');
const randomHex = bytes => crypto.randomBytes(bytes).toString('hex');

/** Creates the folders of `relativeFolder` under `folder` that are missing, each with mode 0700. */
function makeFolders(folder, relativeFolder) {
  let current = folder;
  for (const part of relativeFolder.split(path.sep).filter(part => part && part !== '.')) {
    current = path.join(current, part);
    try {
      fs.mkdirSync(current, {mode: 0o700});
      fs.chmodSync(current, 0o700);
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      if (!fs.lstatSync(current).isDirectory()) throw Object.assign(new Error(`${current} is not a folder`), {code: 'ENOTDIR'});
    }
  }
}

/** Creates `relative` under `folder` exclusively with mode 0600, creating missing folders with mode 0700. */
function writePrivateFile(folder, relative, data) {
  makeFolders(folder, path.dirname(relative));
  const fd = fs.openSync(path.join(folder, relative), fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
  try {
    fs.fchmodSync(fd, 0o600);
    fs.writeSync(fd, data);
  } finally {
    fs.closeSync(fd);
  }
}

// ---------- Processes ----------

export function processAlive(pid) {
  if (!Number.isSafeInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === 'EPERM';
  }
}

// Group 0 means "my own group" and -1 "every process I may signal" to kill(2); neither may ever be recorded or signalled.
const validGroup = group => Number.isSafeInteger(group) && group > 1 && group !== process.pid;

const PS = ['/bin/ps', '/usr/bin/ps'].find(file => fs.existsSync(file)) || '/bin/ps';

/** Whether the group has a member that is not a zombie. An ALA leader the server has not reaped yet (for example
 * while removeOpenSync waits synchronously at exit) is a zombie, and kill(2) still finds its group. When `ps` cannot
 * answer, the group counts as alive, so no home is removed under a running process. */
function liveMember(group) {
  const result = spawnSync(PS, ['-A', '-o', 'pgid=,stat='], {encoding: 'utf8', timeout: 5000, env: {}});
  if (result.status !== 0 || typeof result.stdout !== 'string') return true;
  return result.stdout.split('\n').some(line => {
    const [id, state] = line.trim().split(/\s+/);
    return Number(id) === group && Boolean(state) && !state.startsWith('Z');
  });
}

export function groupAlive(group) {
  if (!validGroup(group)) return false;
  try {
    process.kill(-group, 0);
  } catch (error) {
    if (error.code !== 'EPERM') return false;
  }
  return liveMember(group);
}

/** The process group of `pid`, read through `ps` as liveMember reads it, or null when `ps` cannot tell. */
export function processGroupOf(pid) {
  if (!Number.isSafeInteger(pid) || pid <= 0) return null;
  const result = spawnSync(PS, ['-o', 'pgid=', '-p', String(pid)], {encoding: 'utf8', timeout: 5000, env: {}});
  if (result.status !== 0 || typeof result.stdout !== 'string' || !/^\s*\d+\s*$/.test(result.stdout)) return null;
  return Number(result.stdout.trim());
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const sleepSync = ms => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);

// ---------- The one removal routine ----------

// The profile of ALA's cleanup (src/coding-agents/seatbelt.mjs, CLEANUP_PROFILE): every write outside TARGET is refused
// by the kernel, whatever a path resolves to at that instant.
export const CLEANUP_PROFILE = `(version 1)
(deny default)
(allow process-exec* (literal "/bin/chmod") (literal "/bin/rm") (literal "/usr/bin/chflags"))
(allow file-read*)
(allow sysctl-read)
(allow file-write* (subpath (param "TARGET")))
`;
const SEATBELT = '/usr/bin/sandbox-exec';
const firstExisting = (...files) => files.find(file => fs.existsSync(file)) || files[0];

/** The steps for this platform: a removal, and when it fails, a repair and the removal once more. On macOS each runs
 * confined to the folder. On Linux they run unconfined, and only once the ALA process group has ended, so that nothing
 * can race them; that is the whole safety argument there. How the host's rm and chmod walk the tree (by descriptor or
 * by path) was not verified: the Linux host's chmod is uutils 0.10.0, not GNU coreutils. Every step is a
 * {file, args, timeout} run through runStep, the only place a mutating step is started. */
export function removalSteps(folder) {
  if (process.platform === 'darwin') {
    const confined = (command, timeout) => ({file: SEATBELT, args: ['-p', CLEANUP_PROFILE, '-D', `TARGET=${folder}`, ...command, folder], timeout});
    return {
      remove: confined(['/bin/rm', '-rf', '--'], 10000),
      repair: [
        confined(['/usr/bin/chflags', '-R', '-P', 'nouchg,nouappnd'], 5000),
        confined(['/bin/chmod', '-R', '-P', 'u+rwx'], 5000),
        confined(['/usr/bin/chflags', '-R', '-P', 'nouchg,nouappnd'], 5000)
      ]
    };
  }
  if (process.platform === 'linux') {
    const rm = firstExisting('/usr/bin/rm', '/bin/rm'), chmod = firstExisting('/usr/bin/chmod', '/bin/chmod');
    return {
      remove: {file: rm, args: ['-rf', '--', folder], timeout: 10000},
      repair: [{file: chmod, args: ['-R', 'u+rwx', '--', folder], timeout: 5000}]
    };
  }
  return null;
}

function gone(folder) {
  try {
    fs.lstatSync(folder);
    return false;
  } catch (error) {
    return error.code === 'ENOENT';
  }
}

/** 'gone', 'ok', or 'refused' for a path that is not exactly a real folder of this user. */
function removable(folder) {
  if (typeof folder !== 'string' || folder.includes('\0') || !path.isAbsolute(folder) || path.normalize(folder) !== folder) return 'refused';
  let real;
  try {
    real = fs.realpathSync.native(folder);
  } catch (error) {
    return error.code === 'ENOENT' ? 'gone' : 'refused';
  }
  if (real !== folder) return 'refused';
  try {
    const metadata = fs.lstatSync(folder);
    return metadata.isDirectory() && metadata.uid === process.getuid() ? 'ok' : 'refused';
  } catch {
    return 'refused';
  }
}

/** Starts one removal step and waits for it: synchronously (returns undefined) or not (returns a promise). Never
 * throws; a step that cannot start simply has no effect. */
export function runStep({file, args, timeout}, {sync}) {
  if (sync) {
    try {
      spawnSync(file, args, {stdio: 'ignore', env: {}, timeout});
    } catch {}
    return undefined;
  }
  return new Promise(resolve => {
    let child;
    try {
      child = spawn(file, args, {stdio: 'ignore', env: {}, timeout});
    } catch {
      resolve();
      return;
    }
    child.on('error', () => resolve());
    child.on('close', () => resolve());
  });
}

// For the structural tests of tools/test-agent-home.mjs only: every mutating step of a removal passes through here.
let stepInterceptor = null;

/** Routes every removal step through `intercept(step, {sync})` (which may call runStep) until the returned function
 * is called. For tests only. */
export function interceptRemovalSteps(intercept) {
  const previous = stepInterceptor;
  stepInterceptor = intercept;
  return () => {
    stepInterceptor = previous;
  };
}

function runSync(step) {
  if (stepInterceptor) {
    try {
      stepInterceptor(step, {sync: true});
    } catch {}
    return;
  }
  runStep(step, {sync: true});
}

async function runAsync(step) {
  if (stepInterceptor) {
    try {
      await stepInterceptor(step, {sync: false});
    } catch {}
    return;
  }
  await runStep(step, {sync: false});
}

/** Removes a home or scratch home. Never throws; returns whether the folder is gone. If the confined helpers cannot
 * run, the folder is left: there is no unconfined fallback. */
export async function removeFolder(folder) {
  try {
    const state = removable(folder);
    if (state !== 'ok') return state === 'gone';
    const steps = removalSteps(folder);
    if (!steps) return false;
    await runAsync(steps.remove);
    if (gone(folder)) return true;
    for (const step of steps.repair) await runAsync(step);
    await runAsync(steps.remove);
    return gone(folder);
  } catch {
    return false;
  }
}

export function removeFolderSync(folder) {
  try {
    const state = removable(folder);
    if (state !== 'ok') return state === 'gone';
    const steps = removalSteps(folder);
    if (!steps) return false;
    runSync(steps.remove);
    if (gone(folder)) return true;
    for (const step of steps.repair) runSync(step);
    runSync(steps.remove);
    return gone(folder);
  } catch {
    return false;
  }
}

/** Unlinks the copied login files of a used home before any bulk removal. `files` are absolute paths whose folders
 * between the home and the file were confirmed real. On macOS the unlink runs confined to the home; on Linux the
 * ALA process group has ended, so this process unlinks them. A run can make that unlink fail (a read-only folder, a
 * user-immutable flag), so when a copy is still there the platform's repair steps run on the home and the unlink is
 * tried once more, rather than leaving the copy to depend on the bulk removal. */
function unlinkLogins(home, files, sync) {
  if (!files.length || removable(home) !== 'ok') return sync ? undefined : Promise.resolve();
  const confined = process.platform === 'darwin'
    ? {file: SEATBELT, args: ['-p', CLEANUP_PROFILE, '-D', `TARGET=${home}`, '/bin/rm', '-f', '--', ...files], timeout: 5000}
    : null;
  const unlinkInProcess = () => {
    for (const file of files) {
      try {
        fs.unlinkSync(file);
      } catch {}
    }
  };
  const repair = removalSteps(home)?.repair || [];
  const left = () => files.some(file => !gone(file));
  if (sync) {
    if (confined) runSync(confined);
    else unlinkInProcess();
    if (!left()) return undefined;
    for (const step of repair) runSync(step);
    if (confined) runSync(confined);
    else unlinkInProcess();
    return undefined;
  }
  return (async () => {
    if (confined) await runAsync(confined);
    else unlinkInProcess();
    if (!left()) return;
    for (const step of repair) await runAsync(step);
    if (confined) await runAsync(confined);
    else unlinkInProcess();
  })();
}

// ---------- Inspecting a used home ----------

/** Compares a copied login file with its hash. Before anything is read, every folder between the home and the file
 * must be a real folder ([delta 2]): a run can replace $HOME/.codex with a link to the real ~/.codex, and nothing below
 * such a link may be read or unlinked by path. Returns {changed, unlink}: `unlink` is the path to unlink or null. */
function inspectLogin(home, relative, digest) {
  const below = belowRealFolders(home, relative);
  if (below.state !== 'ok') return {changed: true, unlink: null};
  const file = below.file;
  const read = readBounded(file);
  if (read.state === 'missing') return {changed: true, unlink: null};
  return {changed: read.state !== 'file' || sha256(read.data) !== digest, unlink: file};
}

/** Entries and bytes left in a used home: a bounded, read-only walk that never follows a link. It only informs the
 * journal and never decides whether the home is removed. */
function countLeft(folder, limit = 200000) {
  let entries = 0, bytes = 0, complete = true;
  const pending = [folder];
  while (pending.length) {
    const current = pending.pop();
    let names;
    try {
      names = fs.readdirSync(current);
    } catch {
      continue;
    }
    for (const name of names) {
      if (entries >= limit) {
        complete = false;
        pending.length = 0;
        break;
      }
      let metadata;
      try {
        metadata = fs.lstatSync(path.join(current, name));
      } catch {
        continue;
      }
      entries += 1;
      if (metadata.isFile()) bytes += metadata.size;
      else if (metadata.isDirectory()) pending.push(path.join(current, name));
    }
  }
  return complete ? {entries, bytes} : {entries, bytes, complete};
}

// ---------- Registry of homes in use ----------

// Every home and scratch home this process has open, by suffix (`<pid>-<16 hex>`), shared by every createAgentHomes
// instance, so the sweep of one never removes a home another has open.
const active = new Map();
// One in-process queue per lock file: within one process the file lock alone would see its own live pid and wait.
const lockQueues = new Map();

// ---------- The agent homes ----------

/** `logins()` returns the promise of ALA's login module for the ALA location in force (see loadALALogins). */
export async function loadALALogins(alaEntry) {
  let module;
  try {
    const alaRoot = path.dirname(path.dirname(fs.realpathSync.native(alaEntry)));
    module = await import(pathToFileURL(path.join(alaRoot, 'src/coding-agents/logins.mjs')).href);
  } catch {
    throw unavailable(reasons.noModule(), 'module');
  }
  return pickLogins(module);
}

function pickLogins(module) {
  if (!module || LOGIN_FUNCTIONS.some(name => typeof module[name] !== 'function')) throw unavailable(reasons.noModule(), 'module');
  return Object.fromEntries(LOGIN_FUNCTIONS.map(name => [name, module[name]]));
}

export function createAgentHomes({root, base: requestedBase, explicit = true, logins, environment = () => alaEnvironment(), pid = process.pid, lockWait = LOCK_WAIT_MS, groupWait = GROUP_WAIT_MS}) {
  if (typeof logins !== 'function') throw new TypeError('createAgentHomes needs a logins() function');
  if (!path.isAbsolute(requestedBase || '')) throw unavailable(reasons.relativeBase(requestedBase));
  // The base and every path derived from it take the spelling the volume stores once the base is a real folder (see
  // storedSpelling), so the removal routine's exact-path rule and Seatbelt's TARGET match what is on disk.
  let base, template, homes, staging, lockFile, discoveryHome;
  const derive = location => {
    base = location;
    template = path.join(base, 'template');
    homes = path.join(base, 'homes');
    staging = path.join(base, 'staging');
    lockFile = path.join(base, 'template.lock');
    discoveryHome = path.join(homes, '.discovery');
  };
  const settle = () => {
    const stored = storedSpelling(base);
    if (stored !== base) derive(stored);
  };
  derive(canonicalParent(requestedBase));
  settle();
  const instance = Symbol('agent homes');
  const rewrittenBy = new Map();
  let agentNames = {from: null, names: null};

  async function modules() {
    return pickLogins(await logins());
  }

  /** ALA's agent names, from detection on a home that never exists, so nothing is read. Cached per module. */
  async function names(module) {
    if (agentNames.from !== module.detectCodingAgentLogins) {
      agentNames = {from: module.detectCodingAgentLogins, names: Object.keys(await module.detectCodingAgentLogins({env: module.homeEnvironment({}, discoveryHome)}))};
    }
    return agentNames.names;
  }

  async function requireAgent(module, agent) {
    if (typeof agent !== 'string' || !AGENT_ID.test(agent) || !(await names(module)).includes(agent)) {
      throw new AgentHomeError(`Unknown coding agent: ${String(agent).slice(0, 64)}`, 'unknownAgent', 400);
    }
  }

  /** The template paths of one agent's login files, relative to the template. */
  function loginFiles(module, agent, home = template) {
    return module.codingAgentLoginFiles(agent, {env: module.homeEnvironment({}, home)})
      .map(file => path.relative(home, file))
      .filter(relative => relative && !relative.startsWith('..') && !path.isAbsolute(relative));
  }

  /** HD-5: every entry is an allowed login file or a folder on the way to one, real, private and bounded. A `-shm`
   * file beside an allowed SQLite database is tolerated, because ALA's read-only detection creates it; it is never
   * copied into a home. */
  async function checkTemplate(module) {
    if (!checkPrivateFolder(template)) return;
    const allowed = new Set(), folders = new Set(), tolerated = new Set();
    for (const agent of await names(module)) {
      for (const relative of loginFiles(module, agent)) {
        allowed.add(relative);
        if (relative.endsWith('.db')) tolerated.add(`${relative}-shm`);
        for (let folder = path.dirname(relative); folder !== '.'; folder = path.dirname(folder)) folders.add(folder);
      }
    }
    const walk = relativeFolder => {
      for (const name of fs.readdirSync(path.join(template, relativeFolder)).sort()) {
        const relative = relativeFolder ? path.join(relativeFolder, name) : name;
        const full = path.join(template, relative);
        const metadata = fs.lstatSync(full);
        const kind = kindOf(metadata);
        if (metadata.isSymbolicLink()) throw unavailable(reasons.foreign(relative, kind));
        if (folders.has(relative)) {
          if (!metadata.isDirectory()) throw unavailable(reasons.foreign(relative, kind));
          const problem = privacyProblem(metadata);
          if (problem) throw unavailable(reasons.notPrivate(full, problem));
          walk(relative);
        } else if (allowed.has(relative) || tolerated.has(relative)) {
          if (!metadata.isFile()) throw unavailable(reasons.foreign(relative, kind));
          const problem = privacyProblem(metadata);
          if (problem) throw unavailable(reasons.notPrivate(full, problem, 'file'));
          if (metadata.size > FILE_LIMIT) throw unavailable(reasons.tooLarge(relative));
        } else {
          throw unavailable(reasons.foreign(relative, kind));
        }
      }
    };
    walk('');
  }

  /** HD-5 and HD-6. With `create`, the base, template, homes and staging are created with mode 0700 when absent (the
   * base's parent only for the default location). Without it nothing is created, and an absent base or template
   * simply holds no login. */
  async function check({create = false} = {}) {
    const module = await modules();
    const exists = prepareBase({base, root, explicit, create}).exists;
    settle();
    if (!exists) return module;
    for (const folder of [template, homes, staging]) checkPrivateFolder(folder, {create});
    await checkTemplate(module);
    return module;
  }

  // ---------- Lock ----------

  function lockHolder() {
    const read = readBounded(lockFile, 256);
    if (read.state === 'missing') return {missing: true};
    const holder = read.state === 'file' ? Number(/^(\d+) /.exec(read.data.toString('utf8'))?.[1]) : NaN;
    return {holder: Number.isSafeInteger(holder) && holder > 0 ? holder : null};
  }

  /** Takes over the lock of a dead holder: it is moved aside first, and put back if it turns out to be someone else's. */
  function takeOver(holder) {
    const aside = `${lockFile}.stale.${process.pid}.${randomHex(4)}`;
    try {
      fs.renameSync(lockFile, aside);
    } catch {
      return;
    }
    const moved = readBounded(aside, 256);
    const owner = moved.state === 'file' ? Number(/^(\d+) /.exec(moved.data.toString('utf8'))?.[1]) : NaN;
    if (owner !== holder) {
      try {
        fs.linkSync(aside, lockFile);
      } catch {}
    }
    try {
      fs.unlinkSync(aside);
    } catch {}
  }

  async function fileLock() {
    const draft = `${lockFile}.${process.pid}.${randomHex(4)}`;
    const fd = fs.openSync(draft, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
    try {
      fs.fchmodSync(fd, 0o600);
      fs.writeSync(fd, `${process.pid} ${new Date().toISOString()}\n`);
    } finally {
      fs.closeSync(fd);
    }
    const inode = fs.lstatSync(draft).ino;
    const deadline = Date.now() + lockWait;
    try {
      for (;;) {
        try {
          fs.linkSync(draft, lockFile);
          return inode;
        } catch (error) {
          if (error.code !== 'EEXIST') throw unavailable(reasons.notPrivate(lockFile, `an error (${error.code})`, 'file'));
        }
        const {missing, holder} = lockHolder();
        if (missing) continue;
        if (holder === null) throw unavailable(reasons.locked('unknown', lockFile), 'locked');
        // Every holder within this process waits in the queue, so a lock naming this pid is left from an earlier process.
        if (holder === process.pid || !processAlive(holder)) {
          takeOver(holder);
          continue;
        }
        if (Date.now() >= deadline) throw unavailable(reasons.locked(holder, lockFile), 'locked');
        await sleep(100);
      }
    } finally {
      try {
        fs.unlinkSync(draft);
      } catch {}
    }
  }

  /** Returns a release function. A live holder is waited for up to `lockWait` milliseconds. */
  async function lock() {
    const file = lockFile;
    const previous = lockQueues.get(file) || Promise.resolve();
    let leave;
    const turn = new Promise(resolve => {
      leave = resolve;
    });
    const queued = previous.then(() => turn);
    lockQueues.set(file, queued);
    await previous;
    let inode;
    try {
      inode = await fileLock();
    } catch (error) {
      leave();
      if (lockQueues.get(file) === queued) lockQueues.delete(file);
      throw error;
    }
    return () => {
      try {
        if (fs.lstatSync(file).ino === inode) fs.unlinkSync(file);
      } catch {}
      leave();
      if (lockQueues.get(file) === queued) lockQueues.delete(file);
    };
  }

  // ---------- Owner records ----------

  const ownerFile = suffix => path.join(homes, `home-${suffix}.owner`);

  function writeOwner(suffix, record) {
    const temporary = `${ownerFile(suffix)}.${randomHex(4)}.tmp`;
    try {
      writePrivateFile(homes, path.basename(temporary), JSON.stringify(record) + '\n');
      fs.renameSync(temporary, ownerFile(suffix));
    } catch (error) {
      try {
        fs.unlinkSync(temporary);
      } catch {}
      throw error;
    }
  }

  function readOwner(suffix) {
    const read = readBounded(ownerFile(suffix), 4096);
    if (read.state !== 'file' || read.uid !== process.getuid()) return null;
    try {
      const record = JSON.parse(read.data.toString('utf8'));
      return record && typeof record === 'object' ? record : null;
    } catch {
      return null;
    }
  }

  function removeOwner(suffix) {
    try {
      if (fs.lstatSync(ownerFile(suffix)).isFile()) fs.unlinkSync(ownerFile(suffix));
    } catch {}
  }

  // ---------- Sweep ----------

  const ownedByUs = (owner, suffix) => owner === pid && active.has(suffix);
  const stale = (owner, suffix) => owner === pid ? !active.has(suffix) : !processAlive(owner);

  function realFolderOfMine(folder) {
    try {
      const metadata = fs.lstatSync(folder);
      return metadata.isDirectory() && metadata.uid === process.getuid();
    } catch {
      return false;
    }
  }

  function readNames(folder) {
    try {
      return fs.readdirSync(folder);
    } catch {
      return [];
    }
  }

  /** Removes homes and scratch homes whose owner server is gone, or whose pid is this server's but which are not
   * open, unless their recorded ALA process group is still alive. Other names are never touched. */
  async function sweep() {
    settle();
    const removed = [], kept = [];
    for (const name of readNames(homes)) {
      const match = HOME_NAME.exec(name) || GONE_NAME.exec(name);
      if (!match) continue;
      const owner = Number(match[1]), suffix = `${match[1]}-${match[2]}`, folder = path.join(homes, name);
      if (!stale(owner, suffix) || !realFolderOfMine(folder)) continue;
      const group = readOwner(suffix)?.group;
      if (groupAlive(group)) {
        kept.push({name, reason: `its ALA process group ${group} is still running`});
        continue;
      }
      if (await removeFolder(folder)) {
        removed.push(name);
        if (!fs.existsSync(path.join(homes, `home-${suffix}`)) && !fs.existsSync(path.join(homes, `gone-${suffix}`))) removeOwner(suffix);
      } else {
        kept.push({name, reason: 'it could not be removed'});
      }
    }
    // Owner records whose home is gone.
    for (const name of readNames(homes)) {
      const match = OWNER_NAME.exec(name);
      if (!match) continue;
      const owner = Number(match[1]), suffix = `${match[1]}-${match[2]}`;
      if (!stale(owner, suffix) || fs.existsSync(path.join(homes, `home-${suffix}`)) || fs.existsSync(path.join(homes, `gone-${suffix}`))) continue;
      try {
        if (fs.lstatSync(path.join(homes, name)).isFile()) fs.unlinkSync(path.join(homes, name));
      } catch {}
    }
    for (const name of readNames(staging)) {
      const match = LOGIN_NAME.exec(name);
      if (!match) continue;
      const owner = Number(match[1]), folder = path.join(staging, name);
      if (!stale(owner, `${match[1]}-${match[2]}`) || !realFolderOfMine(folder)) continue;
      if (await removeFolder(folder)) removed.push(path.join('staging', name));
      else kept.push({name: path.join('staging', name), reason: 'it could not be removed'});
    }
    return {removed, kept};
  }

  function leftovers() {
    const found = [];
    for (const name of readNames(homes)) {
      const match = HOME_NAME.exec(name) || GONE_NAME.exec(name);
      if (!match || ownedByUs(Number(match[1]), `${match[1]}-${match[2]}`)) continue;
      const group = readOwner(`${match[1]}-${match[2]}`)?.group;
      const running = groupAlive(group);
      found.push({name, path: path.join(homes, name), reason: running ? `its ALA process group ${group} is still running` : (processAlive(Number(match[1])) && Number(match[1]) !== pid ? 'in use by another pilot server' : 'waiting for the next sweep')});
    }
    for (const name of readNames(staging)) {
      const match = LOGIN_NAME.exec(name);
      if (!match || ownedByUs(Number(match[1]), `${match[1]}-${match[2]}`)) continue;
      found.push({name: path.join('staging', name), path: path.join(staging, name), reason: processAlive(Number(match[1])) && Number(match[1]) !== pid ? 'a sign-in is running' : 'waiting for the next sweep'});
    }
    return found;
  }

  // ---------- Open and close ----------

  function waitForGroupSync(groups, wait) {
    const deadline = Date.now() + wait;
    while (groups.some(groupAlive) && Date.now() < deadline) sleepSync(100);
  }

  async function waitForGroup(group, wait) {
    const deadline = Date.now() + wait;
    while (groupAlive(group) && Date.now() < deadline) await sleep(100);
    return !groupAlive(group);
  }

  /** Steps 1 to 4 of close() for one home: identity, rename, comparison and unlink of the login copies, count. A home
   * already renamed to gone-<suffix> (a close() still in flight when the process exits) is taken from there, under
   * the same identity check, so it is never counted as removed while it is still on disk. */
  function prepareRemoval(entry, sync) {
    const {suffix, files, dev, ino} = entry;
    const renamed = path.join(homes, `gone-${suffix}`);
    let current = path.join(homes, `home-${suffix}`);
    let metadata;
    for (const candidate of [current, renamed]) {
      try {
        metadata = fs.lstatSync(candidate);
        current = candidate;
        break;
      } catch (error) {
        if (error.code !== 'ENOENT') return {result: {removed: false, identity: 'unreadable', rewritten: [], left: null}};
      }
    }
    if (!metadata) return {result: {removed: true, identity: 'missing', rewritten: Object.keys(files).sort(), left: {entries: 0, bytes: 0}}};
    if (!metadata.isDirectory() || metadata.dev !== dev || metadata.ino !== ino) {
      return {result: {removed: false, identity: 'mismatch', rewritten: [], left: null}};
    }
    // Defence in depth on macOS: a survivor's path-based grant no longer names the folder. Removal does not rely on it.
    if (current !== renamed) {
      try {
        fs.renameSync(current, renamed);
        const moved = fs.lstatSync(renamed);
        if (moved.isDirectory() && moved.dev === dev && moved.ino === ino) current = renamed;
        else return {result: {removed: false, identity: 'mismatch', rewritten: [], left: null}};
      } catch {}
    }
    const rewritten = [], unlink = [];
    for (const [relative, digest] of Object.entries(files).sort()) {
      const verdict = inspectLogin(current, relative, digest);
      if (verdict.changed) rewritten.push(relative);
      if (verdict.unlink) unlink.push(verdict.unlink);
    }
    return {current, rewritten, unlink: unlinkLogins(current, unlink, sync)};
  }

  /** A home that was not removed is no longer open either: the next sweep tries again, subject to the owner record. */
  function finish(entry, removed) {
    if (removed) removeOwner(entry.suffix);
    active.delete(entry.suffix);
  }

  async function closeHome(entry) {
    try {
      const group = readOwner(entry.suffix)?.group;
      if (validGroup(group) && !(await waitForGroup(group, groupWait))) {
        active.delete(entry.suffix);
        return {removed: false, kept: `its ALA process group ${group} is still running; a later sweep removes it`, rewritten: [], left: null};
      }
      const prepared = prepareRemoval(entry, false);
      if (prepared.result) {
        if (prepared.result.identity === 'missing') removeOwner(entry.suffix);
        active.delete(entry.suffix);
        return prepared.result;
      }
      await prepared.unlink;
      const left = countLeft(prepared.current);
      const removed = await removeFolder(prepared.current);
      finish(entry, removed);
      if (prepared.rewritten.length) rewrittenBy.set(entry.agent, {at: new Date().toISOString(), files: prepared.rewritten});
      return {removed, rewritten: prepared.rewritten, left};
    } catch (error) {
      active.delete(entry.suffix);
      return {removed: false, rewritten: [], left: null, error: error.message};
    }
  }

  async function discard(suffix, folder) {
    await removeFolder(folder);
    removeOwner(suffix);
    active.delete(suffix);
  }

  /** Opens a fresh home for one ALA process: sweep, lock, check, create, copy, unlock, detection on the copy. */
  async function open(agent, {purpose = 'audit'} = {}) {
    const module = await modules();
    await requireAgent(module, agent);
    await sweep();
    prepareBase({base, root, explicit, create: true});
    settle();
    const release = await lock();
    const suffix = `${pid}-${randomHex(8)}`, folder = path.join(homes, `home-${suffix}`), files = {};
    const entry = {owner: instance, kind: 'home', suffix, agent, files};
    let identityOf;
    try {
      await check({create: true});
      active.set(suffix, entry); // registered before it exists, so no parallel sweep can take it
      writeOwner(suffix, {server: pid, ala: null, group: null, agent, purpose, created: new Date().toISOString()});
      fs.mkdirSync(folder, {mode: 0o700});
      fs.chmodSync(folder, 0o700);
      identityOf = fs.lstatSync(folder);
      for (const relative of loginFiles(module, agent)) {
        const read = readBounded(path.join(template, relative));
        if (read.state === 'missing') continue;
        if (read.state !== 'file') throw unavailable(read.state === 'large' ? reasons.tooLarge(relative) : reasons.foreign(relative, read.state));
        writePrivateFile(folder, relative, read.data);
        files[relative] = sha256(read.data);
      }
    } catch (error) {
      release();
      await discard(suffix, folder);
      throw error;
    }
    release();
    Object.assign(entry, {dev: identityOf.dev, ino: identityOf.ino});
    let detection;
    try {
      detection = await module.detectCodingAgentLogin(agent, {env: module.homeEnvironment(environment(), folder)});
    } catch {
      detection = null;
    }
    if (!detection?.loggedIn) {
      await discard(suffix, folder);
      throw new AgentHomeError(noLogin(agent, base), 'noLogin', 400);
    }
    let closing = null;
    return {
      path: folder,
      agent,
      purpose,
      source: detection.file ? 'file' : 'environment',
      variables: [...(detection.env || [])],
      files: Object.freeze({...files}),
      /** Completes the owner record once ALA has started, detached, as the leader of its own process group. Call it
       * synchronously right after spawn(), while the pid cannot have been reaped. A pid whose process group (read
       * through `ps`) is not `group`, or that `ps` cannot find, is refused with a TypeError and the record keeps
       * group null: a non-detached ALA would share the server's group, its own group would read as ended, and its
       * live home could be removed. */
      recordProcess({pid: alaPid, group = alaPid}) {
        if (!Number.isSafeInteger(alaPid) || alaPid <= 1 || !validGroup(group)) throw new TypeError('recordProcess needs the pid and process group of a detached ALA process');
        const actual = processGroupOf(alaPid);
        if (actual !== group) throw new TypeError(`recordProcess needs a detached ALA process that leads its own process group: process ${alaPid} is in group ${actual ?? 'unknown'}, not ${group}`);
        writeOwner(suffix, {server: pid, ala: alaPid, group, agent, purpose, created: new Date().toISOString()});
      },
      /** Never throws; a second call returns the first result. Call it only after the ALA process has ended. */
      close() {
        closing ??= closeHome(entry);
        return closing;
      }
    };
  }

  async function requireLogin(agent) {
    const module = await check();
    await requireAgent(module, agent);
    const detection = await module.detectCodingAgentLogin(agent, {env: module.homeEnvironment(environment(), template)});
    if (!detection.loggedIn) throw new AgentHomeError(noLogin(agent, base), 'noLogin', 400);
    return {source: detection.file ? 'file' : 'environment', variables: [...detection.env]};
  }

  /** Never throws. Without a usable template only credential variables count. */
  async function status() {
    const result = {base, ready: false, error: null, agents: {}, leftovers: []};
    let module;
    try {
      module = await check();
      result.ready = true;
    } catch (error) {
      result.error = error.message;
      try {
        module = await modules();
      } catch {
        return result;
      }
    }
    try {
      const detected = await module.detectCodingAgentLogins({env: module.homeEnvironment(environment(), result.ready ? template : discoveryHome)});
      for (const [name, login] of Object.entries(detected)) {
        result.agents[name] = {
          loggedIn: Boolean(login.loggedIn),
          source: login.file ? 'file' : login.env?.length ? 'environment' : 'none',
          variables: [...(login.env || [])],
          rewritten: rewrittenBy.get(name) || null
        };
      }
      result.leftovers = leftovers();
    } catch (error) {
      result.error ??= error.message;
    }
    return result;
  }

  /** At process exit: signal each open home's ALA group, wait up to `groupWait`, then remove synchronously. A home
   * whose group is still alive is left and reported. */
  function removeOpenSync() {
    const entries = [...active.values()].filter(entry => entry.owner === instance && entry.kind === 'home');
    const groups = [];
    for (const entry of entries) {
      const group = readOwner(entry.suffix)?.group;
      entry.group = validGroup(group) ? group : null;
      if (entry.group && groupAlive(entry.group)) {
        try {
          process.kill(-entry.group, 'SIGTERM');
        } catch {}
        groups.push(entry.group);
      }
    }
    if (groups.length) waitForGroupSync(groups, groupWait);
    for (const entry of entries) {
      let removed = false;
      if (!entry.group || !groupAlive(entry.group)) {
        const prepared = entry.dev === undefined ? {current: path.join(homes, `home-${entry.suffix}`)} : prepareRemoval(entry, true);
        removed = prepared.result ? prepared.result.removed : removeFolderSync(prepared.current);
      }
      finish(entry, removed);
      if (!removed) {
        const left = [`home-${entry.suffix}`, `gone-${entry.suffix}`].map(name => path.join(homes, name)).find(folder => !gone(folder)) || path.join(homes, `home-${entry.suffix}`);
        try {
          fs.writeSync(2, `Warning: the agent home ${left} was left in place${entry.group && groupAlive(entry.group) ? `; its ALA process group ${entry.group} is still running` : ''}. The next start or "node tools/setup-agent-home.mjs sweep" removes it.\n`);
        } catch {}
      }
    }
  }

  // ---------- Sign-in (tools/setup-agent-home.mjs) ----------

  /** Unlinks a regular file of the template by path, only below real folders: a link on the way is never followed. */
  function unlinkTemplateFile(relative) {
    const below = belowRealFolders(template, relative);
    if (below.state !== 'ok') return false;
    try {
      if (!fs.lstatSync(below.file).isFile()) return false;
      fs.unlinkSync(below.file);
      return true;
    } catch {
      return false;
    }
  }

  /** The agent's login files in the scratch home, read without following a link anywhere between the scratch home
   * and the file (A7). Every listed file that exists must be a regular file of at most FILE_LIMIT bytes, so a login
   * of several files is adopted whole or not at all; at least one must exist. Throws 'noFile' otherwise. */
  function readSignIn(module, agent, scratch) {
    const refuse = message => new AgentHomeError(`${message} The template is unchanged.`, 'noFile', 400);
    let metadata;
    try {
      metadata = fs.lstatSync(scratch);
    } catch {
      metadata = null;
    }
    if (!metadata?.isDirectory()) throw refuse(`The sign-in ended, but its scratch home ${scratch} is no longer a real folder.`);
    const fresh = [];
    for (const relative of loginFiles(module, agent, scratch)) {
      const below = belowRealFolders(scratch, relative);
      if (below.state === 'missing') continue;
      if (below.state === 'blocked') throw refuse(`The sign-in ended, but ${relative} in the scratch home lies below ${below.at}, which is not a real folder, so it is not adopted.`);
      const read = readBounded(below.file);
      if (read.state === 'missing') continue;
      if (read.state !== 'file') {
        const found = {link: 'a link', special: 'not a regular file', large: 'larger than 16777216 bytes', unreadable: 'unreadable'}[read.state] || read.state;
        throw refuse(`The sign-in ended, but ${relative} in the scratch home is ${found}, so the login is not adopted.`);
      }
      fresh.push({relative, data: read.data});
    }
    if (!fresh.length) throw refuse(`The sign-in ended and ALA reports a ${agent} login, but no login file in the scratch home could be adopted.`);
    return fresh;
  }

  /** Runs a sign-in command in an empty scratch home and adopts only the agent's login files. The lock is held only
   * while files are adopted. Returns {adopted}; throws AgentHomeError with kind 'signIn' (exit 5), 'noFile' (6) or
   * 'interrupted'. `signal` is an AbortSignal whose reason is a signal name: when it aborts, the sign-in command
   * receives that signal (SIGKILL after SIGN_IN_GRACE_MS), nothing is adopted unless adoption had begun, and the
   * scratch home is removed before the 'interrupted' error is thrown. */
  async function login(agent, command, {stdio = 'inherit', env = process.env, signal: abort = null} = {}) {
    const module = await check({create: true});
    await requireAgent(module, agent);
    if (!Array.isArray(command) || !command.length || typeof command[0] !== 'string' || !command[0]) throw new AgentHomeError('A sign-in command is required.', 'usage', 400);
    await sweep();
    const suffix = `${pid}-${randomHex(8)}`;
    const scratch = path.join(staging, `login-${suffix}`), adopt = path.join(staging, `login-${suffix}-adopt`);
    const stopIfInterrupted = () => {
      if (!abort?.aborted) return;
      const name = typeof abort.reason === 'string' ? abort.reason : 'SIGTERM';
      throw new AgentHomeError(`The sign-in was interrupted by ${name}. The template is unchanged.`, 'interrupted', 400, null, name);
    };
    active.set(suffix, {owner: instance, kind: 'login', suffix});
    try {
      stopIfInterrupted();
      checkPrivateFolder(scratch, {create: true});
      const signInEnvironment = module.homeEnvironment(Object.fromEntries(Object.entries(env).filter(([key]) => !key.startsWith('PLOINKY_'))), scratch);
      const outcome = await new Promise(resolve => {
        let child, escalation = null;
        try {
          child = spawn(command[0], command.slice(1), {cwd: scratch, env: signInEnvironment, stdio, shell: false});
        } catch (error) {
          resolve({error});
          return;
        }
        const forward = () => {
          try {
            child.kill(typeof abort.reason === 'string' ? abort.reason : 'SIGTERM');
          } catch {}
          escalation = setTimeout(() => {
            try {
              child.kill('SIGKILL');
            } catch {}
          }, SIGN_IN_GRACE_MS);
        };
        const done = result => {
          clearTimeout(escalation);
          abort?.removeEventListener('abort', forward);
          resolve(result);
        };
        if (abort?.aborted) forward();
        else abort?.addEventListener('abort', forward, {once: true});
        child.on('error', error => done({error}));
        child.on('close', (code, signal) => done({code, signal}));
      });
      stopIfInterrupted();
      if (outcome.error) throw new AgentHomeError(`The sign-in command could not start: ${outcome.error.message}. The template is unchanged.`, 'signIn', 400);
      if (outcome.code !== 0) throw new AgentHomeError(`The sign-in command ended with ${outcome.signal ? `signal ${outcome.signal}` : `exit code ${outcome.code}`}. The template is unchanged.`, 'signIn', 400);
      const detection = await module.detectCodingAgentLogin(agent, {env: module.homeEnvironment({}, scratch)});
      if (!detection.file) throw new AgentHomeError(`The sign-in ended, but ALA finds no ${agent} login file in the scratch home. A login kept in the system keychain cannot be used by the pilot; use a credential variable instead. The template is unchanged.`, 'noFile', 400);
      const fresh = readSignIn(module, agent, scratch);
      stopIfInterrupted();
      const release = await lock();
      try {
        await check({create: true});
        checkPrivateFolder(adopt, {create: true});
        // Written in staging/ and renamed in, so the template never holds a partial or foreign file.
        fresh.forEach(({data}, index) => writePrivateFile(adopt, String(index), data));
        fresh.forEach(({relative}, index) => {
          makeFolders(template, path.dirname(relative));
          fs.renameSync(path.join(adopt, String(index)), path.join(template, relative));
        });
        const kept = new Set(fresh.map(({relative}) => relative));
        for (const relative of loginFiles(module, agent)) {
          for (const candidate of kept.has(relative) ? [`${relative}-shm`] : [relative, `${relative}-shm`]) unlinkTemplateFile(candidate);
        }
      } finally {
        release();
      }
      return {adopted: fresh.map(({relative}) => relative)};
    } finally {
      await removeFolder(scratch);
      await removeFolder(adopt);
      active.delete(suffix);
    }
  }

  /** Removes one agent's files from the template. Returns {removed}. A template that fails its check (HD-5) is
   * refused with its text, so a link in it is never followed; files are unlinked only below real folders. */
  async function logout(agent) {
    const module = await modules();
    await requireAgent(module, agent);
    const exists = prepareBase({base, root, explicit}).exists;
    settle();
    if (!exists || !checkPrivateFolder(template)) return {removed: []};
    const release = await lock();
    try {
      await check();
      const removed = [];
      for (const relative of loginFiles(module, agent)) {
        if (unlinkTemplateFile(relative)) removed.push(relative);
        unlinkTemplateFile(`${relative}-shm`);
      }
      return {removed};
    } finally {
      release();
    }
  }

  return {
    get base() {
      return base;
    },
    get template() {
      return template;
    },
    get homes() {
      return homes;
    },
    get staging() {
      return staging;
    },
    get discoveryHome() {
      return discoveryHome;
    },
    get lockFile() {
      return lockFile;
    },
    check, status, requireLogin, open, sweep, removeOpenSync, login, logout,
    agents: async () => names(await modules())
  };
}
