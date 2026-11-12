#!/usr/bin/env node
/** Authentication of the pilot's API. A confined run shares the loopback network with the pilot server, so every
 * `/api/*` request needs a key that no run can read: 32 random bytes per server start, kept in the server's memory and
 * in `<agent-home base>/server/port-<PORT>.key` (mode 0600, folder 0700, outside the repository). The browser receives
 * it once through the fragment of the sign-in link and sends it as `Authorization: Bearer <key>`. File previews and
 * downloads, which cannot carry a header, may use a second, weaker value from the `pilot_view_<PORT>` cookie, accepted
 * only on `GET /api/file`.
 *
 * The key is never placed in an environment, an argument, a record, a journal, a log or an API answer.
 *
 *   node tools/api-auth.mjs link [--port <n>] [--allow-pipe]
 *
 * prints the sign-in link of a running server from its key file. Exit codes: 0 printed; 1 no key file for that port;
 * 2 usage error, standard output is not a terminal and --allow-pipe is absent, or standard output is a regular file;
 * 4 base refused. */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {agentHomeLocation, checkPrivateFolder, prepareBase, processAlive, readBounded} from './agent-home.mjs';
import {ROOT} from './toolchain.mjs';

export const API_UNAUTHORIZED = 'Not signed in to this pilot server. Open the sign-in link it printed on its console, or run "node tools/api-auth.mjs link" in a terminal on this machine.';
export const KEY_PATTERN = /^[A-Za-z0-9_-]{43}$/;
export const DEFAULT_PORT = 8787;
// Longer presented values are refused before they are hashed. The Cookie header as a whole is bounded only by Node's
// own header limit (http.maxHeaderSize, 16 KiB by default): a module limit below it would let a run's listener on
// another loopback port switch off previews and downloads by setting a few large cookies for 127.0.0.1.
const PRESENTED_LIMIT = 256;
const LINK_USAGE = 'Usage: node tools/api-auth.mjs link [--port <n>] [--allow-pipe]';

export const notStarted = reason => `The pilot server did not start: ${reason}`;
export const noKey = (port, base) => `No running pilot server left a key for port ${port} under ${base}.`;
export const keyFilePath = (base, port) => path.join(base, 'server', `port-${port}.key`);
export const viewCookieName = port => `pilot_view_${port}`;
export const signInLink = (port, key) => `http://127.0.0.1:${port}/#key=${key}`;

const digest = value => crypto.createHash('sha256').update(value, 'utf8').digest();
const matches = (presented, expected) => typeof presented === 'string' && presented.length <= PRESENTED_LIMIT && crypto.timingSafeEqual(digest(presented), expected);
const validPort = port => Number.isSafeInteger(port) && port > 0 && port < 65536;

/** Writes `content` to `file` by write-then-rename in the same folder, with mode 0600. */
function writePrivateAtomically(file, content) {
  const temporary = `${file}.${process.pid}.${crypto.randomBytes(4).toString('hex')}.tmp`;
  const fd = fs.openSync(temporary, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
  try {
    fs.fchmodSync(fd, 0o600);
    fs.writeSync(fd, content);
    fs.fsyncSync(fd);
  } catch (error) {
    fs.closeSync(fd);
    try {
      fs.unlinkSync(temporary);
    } catch {}
    throw error;
  }
  fs.closeSync(fd);
  try {
    fs.renameSync(temporary, file);
  } catch (error) {
    try {
      fs.unlinkSync(temporary);
    } catch {}
    throw error;
  }
}

/** Removes the temporary files `<key file>.<pid>.<8 hex>.tmp` that a writer which died between writing and renaming
 * left beside `file`: they may hold a key. Only regular files of dead processes with exactly that name are touched. */
function removeStaleTemporaries(file) {
  const prefix = `${path.basename(file)}.`;
  let names;
  try {
    names = fs.readdirSync(path.dirname(file));
  } catch {
    return;
  }
  for (const name of names) {
    if (!name.startsWith(prefix)) continue;
    const match = /^(\d+)\.[0-9a-f]{8}\.tmp$/.exec(name.slice(prefix.length));
    if (!match || processAlive(Number(match[1]))) continue;
    const temporary = path.join(path.dirname(file), name);
    try {
      if (fs.lstatSync(temporary).isFile()) fs.unlinkSync(temporary);
    } catch {}
  }
}

/** Answers `status` with the JSON `value` without reading the request body. When a body is announced the answer says
 * `Connection: close`, and once it is flushed the connection is cut: ending it politely would let Node read and discard
 * the rest of the upload. A client still sending may then see a reset instead of the answer (a bounded lingering close
 * was measured and not adopted: it delivers the answer but holds about 128 KiB of every refused upload in memory). Used
 * by the gate and by the server's refusal of methods other than GET outside /api/. */
export function answerUnread(req, res, status, headers, value) {
  const body = (req.headers['content-length'] && req.headers['content-length'] !== '0') || req.headers['transfer-encoding'];
  const answer = JSON.stringify(value);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(answer),
    ...headers,
    ...(body ? {Connection: 'close'} : {})
  });
  res.end(answer);
  if (body) res.on('finish', () => req.socket.destroy());
}

/** Every value of one header as sent, so a repeated header is seen even where Node keeps only the first. */
function headerValues(req, name) {
  const values = [];
  for (let i = 0; i + 1 < req.rawHeaders.length; i += 2) {
    if (req.rawHeaders[i].toLowerCase() === name) values.push(req.rawHeaders[i + 1]);
  }
  return values;
}

/**
 * `explicit: false` marks the default base location, whose parent (~/.lean-documents) may be created.
 * Returns {keyFile, start(), announce(stream), gate(req, res, url), session(req, res), stopSync()}.
 */
export function createApiAuth({base, port, root = ROOT, explicit = true}) {
  if (!validPort(port)) throw new TypeError('createApiAuth needs a TCP port');
  const key = crypto.randomBytes(32).toString('base64url');
  const view = crypto.randomBytes(32).toString('base64url');
  const keyDigest = digest(key), viewDigest = digest(view);
  const cookieName = viewCookieName(port);
  let keyFile = keyFilePath(base, port);
  let written = false;

  /** Exactly one `Authorization: Bearer <key>` header. The scheme is matched case-sensitively, as the page sends it. */
  function bearerMatches(req) {
    const values = headerValues(req, 'authorization');
    if (values.length !== 1 || values[0].length > PRESENTED_LIMIT + 7 || !values[0].startsWith('Bearer ')) return false;
    return matches(values[0].slice(7), keyDigest);
  }

  /** Any `pilot_view_<PORT>` value matches: a run's own listener can set a cookie of the same name for 127.0.0.1. */
  function viewCookieMatches(req) {
    const header = req.headers.cookie;
    if (typeof header !== 'string') return false;
    return header.split(';').some(pair => {
      const at = pair.indexOf('=');
      return at > 0 && pair.slice(0, at).trim() === cookieName && matches(pair.slice(at + 1).trim(), viewDigest);
    });
  }

  function refuse(req, res) {
    // The body is never read, so an unauthenticated caller cannot make the server buffer an upload.
    answerUnread(req, res, 401, {'WWW-Authenticate': 'Bearer', 'Cache-Control': 'no-store'}, {error: API_UNAUTHORIZED});
  }

  return {
    get keyFile() {
      return keyFile;
    },
    /** Writes the key file. Call it after the socket is bound, so a server that fails on a busy port never replaces a
     * running server's key. Throws `notStarted(<reason>)`. */
    start() {
      try {
        const prepared = prepareBase({base, root, explicit, create: true});
        checkPrivateFolder(path.join(prepared.base, 'server'), {create: true});
        keyFile = keyFilePath(prepared.base, port);
      } catch (error) {
        throw new Error(notStarted(error.reason || error.message));
      }
      removeStaleTemporaries(keyFile);
      try {
        writePrivateAtomically(keyFile, key + '\n');
      } catch (error) {
        throw new Error(notStarted(`the key file ${keyFile} could not be written (${error.code || error.message}).`));
      }
      written = true;
    },
    /** The console lines after "Audit laboratory: http://127.0.0.1:<PORT>". The link reaches a terminal only. */
    announce(stream = process.stdout) {
      if (stream.isTTY) {
        stream.write(`Sign in by opening: ${signInLink(port, key)}\nThis link works until the server stops. Do not paste it into documents or prompts.\n`);
      } else {
        stream.write(`Sign-in link not printed because this output is not a terminal. Run in a terminal on this machine: node tools/api-auth.mjs link --port ${port}\n`);
      }
    },
    /** True when the request may proceed; otherwise it has been answered with 401 and the handler stops. */
    gate(req, res, url = new URL(req.url, `http://127.0.0.1:${port}`)) {
      if (!url.pathname.startsWith('/api/')) return true;
      if (bearerMatches(req)) return true;
      if (req.method === 'GET' && url.pathname === '/api/file' && viewCookieMatches(req)) return true;
      refuse(req, res);
      return false;
    },
    /** POST /api/session, behind the gate: sets the view cookie. */
    session(req, res) {
      res.writeHead(200, {
        'Content-Type': 'application/json; charset=utf-8',
        'Content-Length': 11,
        'Cache-Control': 'no-store',
        'Set-Cookie': `${cookieName}=${view}; Path=/api/file; HttpOnly; SameSite=Strict`
      });
      res.end(JSON.stringify({ok: true}));
    },
    /** At exit: removes the key file only if it still holds this server's key. Never throws. */
    stopSync() {
      if (!written) return;
      try {
        const read = readBounded(keyFile, 256);
        if (read.state === 'file' && read.data.toString('utf8') === key + '\n') fs.unlinkSync(keyFile);
      } catch {}
      written = false;
    }
  };
}

/** The key of a running server, for callers of the same user. Throws the `noKey` text. */
export async function readApiKey({base, port}) {
  const read = readBounded(keyFilePath(base, port), 256);
  const value = read.state === 'file' && read.uid === process.getuid() ? read.data.toString('utf8').replace(/\n$/, '') : '';
  if (!KEY_PATTERN.test(value)) throw new Error(noKey(port, base));
  return value;
}

// ---------- Confirmation of a saved ALA location ([delta 3]) ----------

// A location saved through the API before sign-in was required may have been planted by a run, so a saved location is
// used only when it equals this record, which only an authenticated POST /api/ala writes. It lives under the base, where
// no earlier code wrote and no run can read or write.
export const ALA_LOCATION_UNCONFIRMED = 'saved before sign-in was required; confirm it in Agent settings';
const confirmationFile = base => path.join(base, 'server', 'ala-location');

/** The confirmed location, or null unless the record is a regular file (never read through a link), at most 8192
 * bytes, owned by this user and private (no access for group or others). */
export function readALAConfirmation(base) {
  const read = readBounded(confirmationFile(base), 8192);
  if (read.state !== 'file' || read.uid !== process.getuid() || (read.mode & 0o077) !== 0) return null;
  const value = read.data.toString('utf8').replace(/\n$/, '');
  return value && !value.includes('\n') ? value : null;
}

/** The server folder must exist already (the server created it at start). */
export function writeALAConfirmation(base, location) {
  if (typeof location !== 'string' || !location || /[\0\r\n]/.test(location)) throw new TypeError('An ALA location is required');
  checkPrivateFolder(path.join(base, 'server'));
  writePrivateAtomically(confirmationFile(base), location + '\n');
}

/** Removes the record (a file, or a link left in its place). An absent record or an absent server/ folder is fine;
 * anything else that cannot be removed throws, so the caller can report that the location is still confirmed. As for
 * writing, server/ must be a private real folder of this user: a link in its place would aim the removal elsewhere. */
export function clearALAConfirmation(base) {
  const file = confirmationFile(base);
  try {
    if (!checkPrivateFolder(path.dirname(file))) return;
  } catch (error) {
    throw new Error(`The ALA location confirmation ${file} could not be removed: ${error.reason || error.message}`);
  }
  try {
    const metadata = fs.lstatSync(file);
    if (!metadata.isFile() && !metadata.isSymbolicLink()) throw Object.assign(new Error('not a file'), {code: 'EISDIR'});
    fs.unlinkSync(file);
  } catch (error) {
    if (error.code === 'ENOENT') return;
    throw new Error(`The ALA location confirmation ${file} could not be removed (${error.code || error.message}).`);
  }
}

export function savedLocationConfirmed(base, saved) {
  return typeof saved === 'string' && saved !== '' && readALAConfirmation(base) === saved;
}

// ---------- The link command ----------

/** Whether a stream writes to a regular file: --allow-pipe allows a pipe, never a file. A stream without a descriptor
 * (a test's capture) is not a file; a descriptor that cannot be examined counts as one. */
function outputIsFile(stream) {
  if (typeof stream.fd !== 'number') return false;
  try {
    return fs.fstatSync(stream.fd).isFile();
  } catch {
    return true;
  }
}

export async function main(argv = process.argv.slice(2), {env = process.env, home = os.homedir(), root = ROOT, stdout = process.stdout, stderr = process.stderr} = {}) {
  const usage = message => {
    stderr.write(`${message ? message + '\n' : ''}${LINK_USAGE}\n`);
    return 2;
  };
  if (argv[0] !== 'link') return usage();
  let port = Number(env.PORT || DEFAULT_PORT), allowPipe = false;
  for (let i = 1; i < argv.length; i++) {
    if (argv[i] === '--allow-pipe') allowPipe = true;
    else if (argv[i] === '--port' && i + 1 < argv.length && /^\d{1,5}$/.test(argv[i + 1])) port = Number(argv[++i]);
    else return usage(`Unknown or incomplete argument: ${argv[i]}`);
  }
  if (!validPort(port)) return usage(`Not a TCP port: ${port}`);
  if (!stdout.isTTY && !allowPipe) {
    return usage('The sign-in link is printed only to a terminal, so that it reaches no file. Run this in a terminal on this machine, or add --allow-pipe when the output goes to no file.');
  }
  if (outputIsFile(stdout)) {
    return usage('The sign-in link is never written to a file, and standard output is a file. Run this in a terminal on this machine, or pipe it to a program.');
  }
  let base;
  try {
    const location = agentHomeLocation({env, home});
    base = prepareBase({base: location.base, root, explicit: location.explicit}).base;
  } catch (error) {
    stderr.write(`${error.message}\n`);
    return 4;
  }
  let key;
  try {
    key = await readApiKey({base, port});
  } catch (error) {
    stderr.write(`${error.message}\n`);
    return 1;
  }
  stdout.write(signInLink(port, key) + '\n');
  return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(fs.realpathSync(process.argv[1])).href) process.exitCode = await main();
