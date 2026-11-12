/** The agent home setting. By default ALA starts every coding agent without `--home` (your own login and agent state);
 * after `npm run set-home -- <folder>` every agent start passes `--home <folder>`, one folder shared by all runs. The
 * setting is ~/.lean-documents/agent-home.json, exactly {"home": "<absolute real path>"}, written only by tools/set-home.mjs
 * and read by the server at every use. An absent file is the default; a file that cannot be used is an error, never the
 * default. Nothing here removes a folder. */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';

export const OWN_HOME = 'your own home (ALA started without --home)';
const LOGIN_FUNCTIONS = ['homeEnvironment', 'codingAgentLoginFiles', 'detectCodingAgentLogin', 'detectCodingAgentLogins'];

// ---------- Fixed texts ----------

export const homeUnavailable = reason => `The agent home cannot be used, so no coding agent was started: ${reason}`;

export function noLogin(agent, setting, files) {
  const looked = `ALA looked for ${files.join(', ')} and for a credential variable in the server's environment.`;
  if (setting?.folder) return `No login found for ${agent} in the agent home ${setting.folder}. ${looked} Sign in inside that folder: see tools/README.md, "Agent home".`;
  return `No login found for ${agent}. ${looked} Sign in with ${agent}'s own command, then try again. A login kept only in the macOS Keychain is not visible inside ALA's sandbox. See tools/README.md, "Agent home".`;
}

export const reasons = Object.freeze({
  notAbsolute: value => `The agent home must be an absolute folder path without line breaks; got ${JSON.stringify(String(value))}.`,
  insideRepository: (folder, root) => `${folder} lies inside the repository, which every run can read. Choose a folder outside ${root}.`,
  containsSetting: (folder, settings) => `${folder} is or contains ${settings}, where the setting is kept; a run could rewrite it. Choose a folder that does not contain it.`,
  isSetting: folder => `${folder} is where the agent-home setting itself is kept. Choose another folder.`,
  containsRepository: (folder, root) => `${folder} contains the repository ${root}; a run could change the tools the server runs. Choose a folder that does not contain it.`,
  notFolder: folder => `${folder} exists and is not a folder.`,
  notPrivate: (folder, found) => `${folder} must be owned by you with no access for others; found ${found}. Run: chmod 700 ${folder}`,
  missing: folder => `the folder ${folder} does not exist. Create it with npm run set-home -- ${folder}, or go back to your own home with npm run set-home -- --reset.`,
  badSetting: (file, detail) => `${file} is not a usable agent-home setting (${detail}). Set it again with npm run set-home -- <folder>, or remove it with npm run set-home -- --reset.`,
  noModule: () => 'this ALA has no src/coding-agents/logins.mjs with homeEnvironment, codingAgentLoginFiles, detectCodingAgentLogin and detectCodingAgentLogins; update ALA.',
  retired: () => 'AUDIT_AGENT_HOME is no longer used. Unset it; to give coding agents a separate home, run npm run set-home -- <folder>. See tools/README.md, "Agent home".'
});

/** `kind` picks the answer: 'usage' and 'refused' (a folder set-home refuses, exit 2 and 3), 'setting' (a setting that
 * cannot be read or written), 'module' (no usable login module), 'noLogin'. `status` is the HTTP status a server
 * answers, `reason` the bare reason inside `homeUnavailable`. */
export class AgentHomeError extends Error {
  constructor(message, kind, status, reason = null) {
    super(message);
    Object.assign(this, {kind, status, reason});
  }
}
const unavailable = (reason, kind) => new AgentHomeError(homeUnavailable(reason), kind, 503, reason);

// ---------- Locations ----------

export const settingFile = (home = os.homedir()) => path.join(home, '.lean-documents', 'agent-home.json');

/** The real path of a path that may not exist yet: the nearest existing ancestor resolved, the rest appended. The
 * native call also gives the letter case the volume stores. */
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

const lexicallyWithin = (inner, outer) => {
  const relative = path.relative(outer, inner);
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
};

// ---------- Validation ----------

/** The placement rules, in the order of the refusals; returns the real location. ~/.lean-documents may not exist yet, so
 * it is also compared by its real location. */
function checkPlacement(folder, {root, home}) {
  if (typeof folder !== 'string' || !path.isAbsolute(folder) || /[\0\r\n]/.test(folder)) throw unavailable(reasons.notAbsolute(folder), 'usage');
  const real = realLocation(folder), repository = realLocation(root), settings = path.dirname(settingFile(home));
  if (liesWithin(real, repository)) throw unavailable(reasons.insideRepository(real, repository), 'refused');
  if (lexicallyWithin(realLocation(settings), real) || liesWithin(settings, real)) throw unavailable(reasons.containsSetting(real, settings), 'refused');
  // The setting file itself (and its temporary name while it is written) can never be the folder: creating it would
  // leave a folder where the setting belongs, which neither set-home nor the server could then use or remove.
  // The name is compared without regard to letter case, because a volume may not distinguish it, and before anything
  // is created, because a name that does not exist yet keeps the case it was typed in.
  const file = realLocation(settingFile(home)), name = path.basename(real).toLowerCase(), parent = path.dirname(real);
  const besideSetting = realLocation(parent) === realLocation(settings) || (identity(parent) !== null && identity(parent) === identity(settings));
  if (real === file || real.startsWith(file + '.') || lexicallyWithin(real, file) || (besideSetting && (name === path.basename(file) || name.startsWith(path.basename(file) + '.')))) throw unavailable(reasons.isSetting(real), 'refused');
  if (liesWithin(repository, real)) throw unavailable(reasons.containsRepository(real, repository), 'refused');
  return real;
}

/** The checks of validateAgentHome without creating anything: {real, metadata}, metadata null for a missing folder.
 * With `create` (set-home only, A5) an existing folder must also be private. */
function inspectAgentHome(folder, {root, home, create}) {
  const real = checkPlacement(folder, {root, home});
  let metadata = null;
  try { metadata = fs.statSync(real); } catch (error) { if (error.code !== 'ENOENT') throw unavailable(`${real} cannot be examined (${error.code}).`, 'setting'); }
  if (metadata && !metadata.isDirectory()) throw unavailable(reasons.notFolder(real), 'refused');
  if (metadata && create && metadata.uid !== process.getuid()) throw unavailable(reasons.notPrivate(real, `owner uid ${metadata.uid}`), 'refused');
  if (metadata && create && (metadata.mode & 0o077) !== 0) throw unavailable(reasons.notPrivate(real, `mode 0${(metadata.mode & 0o777).toString(8)}`), 'refused');
  return {real, metadata};
}

/** Read-time validation: absolute, outside the repository, not containing the repository or ~/.lean-documents, and an
 * existing folder after resolving links. Returns its real path. With `create` (set-home) a missing folder is created
 * with mode 0700 instead of being refused. */
export function validateAgentHome(folder, {root, home = os.homedir(), create = false}) {
  const {real, metadata} = inspectAgentHome(folder, {root, home, create});
  if (!metadata && !create) throw unavailable(reasons.missing(real), 'refused');
  // The umask must not decide the mode of the folder itself.
  if (!metadata) try { fs.mkdirSync(real, {recursive: true, mode: 0o700}); fs.chmodSync(real, 0o700); } catch (error) { throw unavailable(`${real} could not be created: ${error.message}`, 'setting'); }
  const final = fs.realpathSync.native(real);
  checkPlacement(final, {root, home});
  return final;
}

// ---------- The setting ----------

/** The text of the setting file, or null when it is not a regular file. Never through a link, and never blocking on a
 * FIFO. Throws the system error (ENOENT for an absent file, ELOOP for a link). */
function readSettingText(file) {
  const handle = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  try { return fs.fstatSync(handle).isFile() ? fs.readFileSync(handle, 'utf8') : null; } finally { fs.closeSync(handle); }
}

/** The setting in force: {mode: 'own', folder: null, setting} without a setting file, else {mode: 'folder', folder,
 * setting} with the validated real folder. Any defect throws an AgentHomeError with status 503. */
export function readAgentHome({root, home = os.homedir()}) {
  const file = settingFile(home), bad = detail => unavailable(reasons.badSetting(file, detail), 'setting');
  let text, value;
  try {
    text = readSettingText(file);
  } catch (error) {
    if (error.code === 'ENOENT') return {mode: 'own', folder: null, setting: file};
    throw bad(error.code === 'ELOOP' ? 'a link' : `cannot be read: ${error.code}`);
  }
  if (text === null) throw bad('not a regular file');
  try { value = JSON.parse(text); } catch { throw bad('not JSON'); }
  if (!value || typeof value !== 'object' || Object.keys(value).join() !== 'home' || typeof value.home !== 'string') throw bad('not {"home": "<absolute folder>"}');
  try { return {mode: 'folder', folder: validateAgentHome(value.home, {root, home}), setting: file}; } catch (error) { throw unavailable(error.reason || error.message, 'setting'); }
}

/** Saves `folder`, or with null removes the setting file; nothing else is ever removed. The folder is validated before
 * anything is created; then ~/.lean-documents is created with mode 0700 when missing, the folder when missing, and the
 * file is written as <file>.<8 hex>.tmp with mode 0600 and renamed. After a reset `previous` names the folder set before. */
export function writeAgentHome(folder, {root, home = os.homedir()}) {
  const file = settingFile(home), failed = (detail, error) => unavailable(reasons.badSetting(file, `${detail}: ${error.code || error.message}`), 'setting');
  if (folder === null) {
    let previous = null;
    try { previous = JSON.parse(readSettingText(file) ?? 'null')?.home ?? null; } catch {}
    try { fs.unlinkSync(file); } catch (error) { if (error.code !== 'ENOENT') throw failed('cannot be removed', error); }
    return {mode: 'own', folder: null, setting: file, previous: typeof previous === 'string' ? previous : null};
  }
  inspectAgentHome(folder, {root, home, create: true});
  try { fs.mkdirSync(path.dirname(file), {mode: 0o700}); fs.chmodSync(path.dirname(file), 0o700); } catch (error) { if (error.code !== 'EEXIST') throw failed('its folder cannot be created', error); }
  const real = validateAgentHome(folder, {root, home, create: true});
  const temporary = `${file}.${crypto.randomBytes(4).toString('hex')}.tmp`;
  try {
    fs.writeFileSync(temporary, JSON.stringify({home: real}) + '\n', {mode: 0o600, flag: 'wx'});
    fs.chmodSync(temporary, 0o600);
    fs.renameSync(temporary, file);
  } catch (error) {
    fs.rmSync(temporary, {force: true});
    throw failed('cannot be written', error);
  }
  return {mode: 'folder', folder: real, setting: file};
}

// ---------- Logins ----------

/** ALA's own login module for the ALA location in force, so the server reports what `ala status` would. */
export async function loadALALogins(alaEntry) {
  let module;
  try { module = await import(pathToFileURL(path.join(path.dirname(path.dirname(fs.realpathSync.native(alaEntry))), 'src/coding-agents/logins.mjs')).href); } catch {}
  if (!module || LOGIN_FUNCTIONS.some(name => typeof module[name] !== 'function')) throw unavailable(reasons.noModule(), 'module');
  return Object.fromEntries(LOGIN_FUNCTIONS.map(name => [name, module[name]]));
}

