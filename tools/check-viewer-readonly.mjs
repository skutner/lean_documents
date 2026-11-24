#!/usr/bin/env node
/** Operator check for the live gate: the viewer must leave a Deep Proof data folder exactly as it found it.
 *
 *   VIEWER_DEEP_PROOF_DATA=<data folder> node tools/check-viewer-readonly.mjs
 *
 * It takes a manifest of `<data>/projects/` (path, type, size, mode, inode, link count, modification time in
 * nanoseconds, SHA-256; access time left out), starts the viewer on a free port, reads every route it can reach twice,
 * and takes the manifest again. The database files of Deep Proof change while its server runs and the viewer never
 * reads them, so they are not part of the manifest. Run it while no Deep Proof run is active and the Deep Proof server is
 * not restarting (a restart rewrites source files). Exit 0: manifest and answers unchanged; 1: a difference; 2: usage.
 * This is an operator tool, not a server module; it reads the folder directly to compare. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {loadSettings, startViewer} from './process-viewer.mjs';

/** One line per entry below `root` (or below `root/only`), sorted. A link is recorded, never followed. */
export function treeManifest(root, {only = null} = {}) {
  const lines = [];
  const walk = (abs, rel) => {
    let st;
    try {
      st = fs.lstatSync(abs, {bigint: true});
    } catch {
      return;
    }
    const type = st.isSymbolicLink() ? 'link' : st.isDirectory() ? 'dir' : st.isFile() ? 'file' : 'other';
    let sha = '-';
    if (type === 'file') {
      try {
        sha = crypto.createHash('sha256').update(fs.readFileSync(abs)).digest('hex');
      } catch {
        sha = 'unreadable';
      }
    }
    lines.push([rel || '.', type, st.size, (st.mode & 0o7777n).toString(8), st.ino, st.nlink, st.mtimeNs, sha].join('\t'));
    if (type === 'dir') {
      let names = [];
      try {
        names = fs.readdirSync(abs);
      } catch {
        lines.push(`${rel}/?\tunlistable`);
      }
      for (const name of names.sort()) walk(path.join(abs, name), rel ? `${rel}/${name}` : name);
    }
  };
  walk(only ? path.join(root, only) : root, only || '');
  return lines.sort();
}

const MAX_ROUTES = 20000;

/** Reads every route the viewer offers for the data it finds: status, runs, each run page, its timeline and every step
 * body, every document of its cards, the dossiers with their documents and downloads. Returns a Map of route to
 * `status:body`, in the order read. */
export async function crawl(base) {
  const answers = new Map();
  const get = async route => {
    if (answers.size >= MAX_ROUTES || answers.has(route)) return null;
    const response = await fetch(base + route);
    const body = await response.text();
    answers.set(route, `${response.status}:${body}`);
    return response.status === 200 && (response.headers.get('content-type') || '').includes('json') ? JSON.parse(body) : null;
  };
  const allParts = async route => {
    const first = await get(`${route}${route.includes('?') ? '&' : '?'}part=0`);
    for (let k = 1; first && k < first.parts; k++) await get(`${route}${route.includes('?') ? '&' : '?'}part=${k}`);
  };
  await get('/api/status');
  const runs = (await get('/api/runs')) ?? [];
  for (const run of runs) {
    const page = await get(`/api/runs/${run.key}`);
    if (!page) continue;
    for (const card of [...page.inputs, ...page.results]) if (card.doc) await allParts(`/api/runs/${run.key}/document?name=${encodeURIComponent(card.doc)}`);
    let after = 0;
    for (let rounds = 0; rounds < 1000; rounds++) {
      const tl = await get(`/api/runs/${run.key}/timeline?after=${after}`);
      if (!tl) break;
      for (const e of tl.entries) if (e.hasBody) await allParts(`/api/runs/${run.key}/timeline/${e.n}`);
      if (!tl.more) break;
      after = tl.next;
    }
  }
  const dossiers = (await get('/api/dossiers')) ?? [];
  for (const d of dossiers) {
    for (const s of d.sources) {
      await allParts(`/api/dossiers/${d.n}/document?name=${encodeURIComponent(`source:${s.name}`)}`);
      await get(`/api/dossiers/${d.n}/download?name=${encodeURIComponent(`source:${s.name}`)}`);
    }
    for (const r of d.reports) {
      await allParts(`/api/dossiers/${d.n}/document?name=${encodeURIComponent(`report:${r.sha12}`)}`);
      await get(`/api/dossiers/${d.n}/download?name=${encodeURIComponent(`report:${r.sha12}`)}`);
    }
  }
  return answers;
}

export function diff(before, after) {
  const a = new Set(before);
  const b = new Set(after);
  return {removed: before.filter(x => !b.has(x)), added: after.filter(x => !a.has(x))};
}

async function main(argv) {
  if (argv.length) {
    console.error('Usage: node tools/check-viewer-readonly.mjs (no options; the data folder is VIEWER_DEEP_PROOF_DATA)');
    return 2;
  }
  let settings;
  try {
    settings = loadSettings({env: process.env});
  } catch (error) {
    console.error(error.message);
    return 2;
  }
  if (!settings.deepProofData) {
    console.error('No Deep Proof data folder is set. Name it in VIEWER_DEEP_PROOF_DATA.');
    return 2;
  }
  const viewer = await startViewer({settings: {...settings, port: 0}});
  try {
    const projects = path.join(viewer.roots.data.path, 'projects');
    const before = treeManifest(projects);
    const first = await crawl(`http://127.0.0.1:${viewer.port}`);
    const second = await crawl(`http://127.0.0.1:${viewer.port}`);
    const after = treeManifest(projects);
    const changes = diff(before, after);
    const answersEqual = first.size === second.size && [...first].every(([route, value]) => second.get(route) === value);
    console.log(`Manifest of projects/: ${before.length} entries before, ${after.length} after.`);
    console.log(`Routes read: ${first.size}; second read gives the same answers: ${answersEqual ? 'yes' : 'NO'}.`);
    if (changes.added.length || changes.removed.length) {
      console.log('The manifest CHANGED:');
      for (const line of changes.removed.slice(0, 20)) console.log(`  - ${line}`);
      for (const line of changes.added.slice(0, 20)) console.log(`  + ${line}`);
      return 1;
    }
    console.log(`Manifest unchanged.${answersEqual ? '' : ' (Answers differ between reads: a run was active or a file changed.)'}`);
    return answersEqual ? 0 : 1;
  } finally {
    await viewer.close();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main(process.argv.slice(2)).then(code => { process.exitCode = code; }, error => { console.error(error.message); process.exitCode = 2; });
}
