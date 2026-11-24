#!/usr/bin/env node
/** The Deep Proof process viewer: a read-only server that shows the recorded steps of Deep Proof analyses and Check
 * Reports from a Deep Proof data folder on this machine, and the benchmark dossiers where they are.
 *
 *   node tools/process-viewer.mjs        (bare server; `npm run start` runs tools/start.mjs, which sets everything up)
 *
 * It binds 127.0.0.1 only, serves GET only and answers only its own host name. It never writes, never follows a link
 * inside a foreign folder, never opens the Deep Proof database or anything outside `projects/`, never opens answer keys,
 * and starts no process. Settings come from the environment only and are never written: VIEWER_DEEP_PROOF_DATA,
 * VIEWER_DOSSIERS, VIEWER_DEEP_PROOF_TMP, VIEWER_PORT. */
import http from 'node:http';
import crypto from 'node:crypto';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {readFileSync, realpathSync} from 'node:fs';
import {openRoots, proveNoFollow} from './pv-read.mjs';
import {createTraceIndex, ViewerError, isId} from './pv-trace.mjs';
import {createRunIndex} from './pv-runs.mjs';
import {createDocuments} from './pv-documents.mjs';
import {createDossiers} from './pv-dossiers.mjs';

const TOOLS = path.dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = path.resolve(TOOLS, '..');
export const DEFAULT_PORT = 8790;
export const POLL_MS = 3000;
const PAGE_FILE = path.join(TOOLS, 'process-viewer.html');

// ---------- Settings ----------

/** Reads the settings once, from the environment only. Throws a plain message on a bad value. */
export function loadSettings({env = process.env} = {}) {
  const dossiers = env.VIEWER_DOSSIERS ? env.VIEWER_DOSSIERS.split(path.delimiter).filter(Boolean) : [];
  const text = value => (value === undefined || value === null || value === '' ? null : value);
  const rawPort = env.VIEWER_PORT !== undefined && env.VIEWER_PORT !== '' ? Number(env.VIEWER_PORT) : DEFAULT_PORT;
  if (!Number.isInteger(rawPort) || rawPort < 0 || rawPort > 65535) throw new Error('The port must be a whole number from 0 to 65535.');
  return {
    deepProofData: text(env.VIEWER_DEEP_PROOF_DATA),
    dossiers,
    deepProofTmp: text(env.VIEWER_DEEP_PROOF_TMP),
    port: rawPort
  };
}

// ---------- Answers ----------

const COMMON_HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'};

function json(res, status, value, extra = {}) {
  const body = JSON.stringify(value);
  res.writeHead(status, {...COMMON_HEADERS, 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': Buffer.byteLength(body), ...extra});
  res.end(body);
}

/** Answers without reading the request. When a body is announced the connection is cut once the answer is flushed. */
function unread(req, res, status, message, extra = {}) {
  const body = Boolean((req.headers['content-length'] && req.headers['content-length'] !== '0') || req.headers['transfer-encoding']);
  json(res, status, {error: message}, {...extra, ...(body ? {Connection: 'close'} : {})});
  if (body) res.on('finish', () => req.socket.destroy());
}

/** The policy of the page: its one inline script is allowed by hash, computed from the bytes served. */
export function pagePolicy(page) {
  const open = Buffer.from('<script>');
  const close = Buffer.from('</script>');
  const start = page.indexOf(open);
  const end = start < 0 ? -1 : page.indexOf(close, start + open.length);
  const single = start >= 0 && end >= 0 && page.indexOf(Buffer.from('<script'), end) < 0 && page.indexOf(Buffer.from('<script'), 0) === start;
  const script = single ? `'sha256-${crypto.createHash('sha256').update(page.subarray(start + open.length, end)).digest('base64')}'` : "'none'";
  return `default-src 'none'; script-src ${script}; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'`;
}

const number = (value, name) => {
  if (value === null || value === undefined || value === '') return 0;
  if (!/^\d{1,9}$/.test(value)) throw new ViewerError(400, `${name} must be a whole number.`);
  return Number(value);
};
const segment = value => {
  let decoded;
  try {
    decoded = decodeURIComponent(value);
  } catch {
    throw new ViewerError(400, 'The address is not valid.');
  }
  return decoded;
};

// ---------- The server ----------

/** Starts the viewer. Proves the no-link flag first (macOS), resolves the roots, then listens on 127.0.0.1.
 * `server` is an http server that already listens on 127.0.0.1 (the launcher holds the port while it starts Deep Proof);
 * the viewer takes it over instead of listening itself. `deepProof` is `null` or `{url, recording: true|false|null, startedHere}`, shown by /api/status. `now`, `alive` and
 * `pageFile` exist for tests. Returns `{server, port, url, roots, close}`. */
export async function startViewer({settings, now = Date.now, alive, pageFile = PAGE_FILE, deepProof = null, server: listening = null} = {}) {
  proveNoFollow();
  const roots = openRoots(settings);
  const traces = createTraceIndex({root: roots.data});
  const dossiers = createDossiers({roots});
  const runs = createRunIndex({roots, traces, now, ...(alive ? {alive} : {}), link: (run, helpers) => dossiers.linkRun(run, helpers)});
  const docs = createDocuments({roots, traces, runs, dossiers});
  const helpers = {documents: runs.documents, reportUnderReview: runs.reportUnderReview};
  let port = null;

  function status() {
    const folders = new Set();
    for (const run of runs.all()) if (run.trace?.workspacePath) folders.add(run.trace.workspacePath);
    const parents = [...folders].map(f => path.dirname(f));
    return {
      connected: Boolean(roots.data), dossiersConfigured: roots.dossiers.length,
      tmpMatches: parents.length ? parents.every(p => p === roots.tmp.path) : null,
      pollMs: POLL_MS,
      folders: {data: roots.data?.path ?? null, tmp: roots.tmp.path, dossiers: roots.dossiers.map(d => d.path)},
      deepProof: deepProof ? {url: deepProof.url, recording: deepProof.recording, startedHere: Boolean(deepProof.startedHere)} : null
    };
  }

  function page(res) {
    let bytes;
    try {
      bytes = readFileSync(pageFile);
    } catch {
      return json(res, 404, {error: 'The page file is missing.'});
    }
    res.writeHead(200, {...COMMON_HEADERS, 'Content-Type': 'text/html; charset=utf-8', 'Content-Length': bytes.length, 'Content-Security-Policy': pagePolicy(bytes)});
    res.end(bytes);
  }

  function route(res, url) {
    const parts = url.pathname.split('/').slice(1).map(segment); // ['api', ...]
    const q = url.searchParams;
    if (parts.length === 2 && parts[1] === 'status') return json(res, 200, status());
    if (parts[1] === 'runs') {
      if (parts.length === 2) return json(res, 200, runs.publicList());
      const [pid, rid, what, extra] = [parts[2], parts[3], parts[4], parts[5]];
      if (!isId(pid) || !isId(rid)) throw new ViewerError(404, 'This run does not exist.');
      if (parts.length === 4) return json(res, 200, docs.runPage(pid, rid));
      if (what === 'timeline' && parts.length === 5) return json(res, 200, docs.timeline(pid, rid, number(q.get('after'), 'after')));
      if (what === 'timeline' && parts.length === 6) {
        if (!/^\d{1,9}$/.test(extra)) throw new ViewerError(404, 'This step does not exist.');
        return json(res, 200, docs.entryBody(pid, rid, Number(extra), number(q.get('part'), 'part')));
      }
      if (what === 'document' && parts.length === 5) return json(res, 200, docs.document(pid, rid, q.get('name'), number(q.get('part'), 'part')));
      throw new ViewerError(404, 'There is no such address.');
    }
    if (parts[1] === 'dossiers') {
      if (parts.length === 2) return json(res, 200, dossiers.list(runs.all(), helpers));
      const n = /^\d{1,6}$/.test(parts[2] ?? '') ? Number(parts[2]) : 0;
      if (!n) throw new ViewerError(404, 'This dossier does not exist.');
      if (parts[3] === 'document' && parts.length === 4) return json(res, 200, dossiers.document(n, q.get('name'), number(q.get('part'), 'part')));
      if (parts[3] === 'download' && parts.length === 4) {
        const {bytes, filename} = dossiers.file(n, q.get('name'));
        res.writeHead(200, {...COMMON_HEADERS, 'Content-Type': 'text/markdown; charset=utf-8', 'Content-Length': bytes.length, 'Content-Disposition': `attachment; filename="${filename}"`});
        return res.end(bytes);
      }
    }
    throw new ViewerError(404, 'There is no such address.');
  }

  const handle = (req, res) => {
    try {
      if (req.headers.host !== `127.0.0.1:${port}`) return unread(req, res, 400, `Use http://127.0.0.1:${port}`);
      if (req.method !== 'GET') return unread(req, res, 405, 'Only GET is served.', {Allow: 'GET'});
      if ((req.headers['content-length'] && req.headers['content-length'] !== '0') || req.headers['transfer-encoding']) return unread(req, res, 400, 'A request must not carry a body.');
      if (!req.url.startsWith('/') || req.url.startsWith('//')) return unread(req, res, 400, 'The address is not valid.');
      const url = new URL(req.url, `http://127.0.0.1:${port}`);
      if (url.pathname === '/') return page(res);
      if (!url.pathname.startsWith('/api/')) return json(res, 404, {error: 'There is no such address.'});
      return route(res, url);
    } catch (error) {
      if (res.headersSent) return res.destroy();
      if (error instanceof ViewerError) return json(res, error.status, {error: error.message});
      return json(res, 500, {error: 'The viewer could not complete this request.'});
    }
  };
  const server = listening ?? http.createServer();
  server.removeAllListeners('request');
  server.on('request', handle);


  if (!listening) {
    await new Promise((resolve, reject) => {
      server.once('error', error => reject(new Error(error.code === 'EADDRINUSE' ? `Port ${settings.port} is in use. Set VIEWER_PORT to another port.` : 'The viewer could not start listening.')));
      server.listen(settings.port, '127.0.0.1', resolve);
    });
  }
  port = server.address().port;
  return {
    server, port, roots, url: `http://127.0.0.1:${port}/`,
    close: () => new Promise(resolve => {
      server.close(() => resolve());
      server.closeAllConnections();
    })
  };
}

async function main() {
  let viewer;
  try {
    viewer = await startViewer({settings: loadSettings()});
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
    return;
  }
  console.log(`Process viewer: ${viewer.url}`);
  if (!viewer.roots.data) console.log('No Deep Proof data folder is set. Set VIEWER_DEEP_PROOF_DATA or use npm run start.');
}

if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) main();
