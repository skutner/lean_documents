/** Benchmark dossiers for the Deep Proof process viewer, read where they are.
 *
 * A dossier is one configured folder, and its root is the folder itself, so the answer-key folders that lie beside it
 * are outside every root. Only `sources/*.md` and the `report-*.md` files at the top are opened. Reports are offered by
 * the first 12 hex digits of their SHA-256 and under a neutral name, in the order of that digest, so that neither a
 * file name nor a position tells which report is which. Runs are linked to dossiers by the SHA-256 of file bytes. */
import crypto from 'node:crypto';
import path from 'node:path';
import * as defaultGate from './pv-read.mjs';
import {ViewerError, partOf} from './pv-trace.mjs';

const MAX_SOURCES = 20;
const MAX_REPORTS = 10;
const MAX_BYTES = 4 * 1024 * 1024;
const SOURCE_NAME = /^[A-Za-z0-9._ -]+\.md$/;
const REPORT_NAME = /^report-[A-Za-z0-9._ -]+\.md$/;
const SHA12 = /^[0-9a-f]{12}$/;

const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
export const neutralName = sha12 => `report-${sha12}.md`;

export function createDossiers({roots, gate = defaultGate}) {
  const folders = roots?.dossiers ?? [];
  const hashes = new Map();

  function hashed(root, rel, entry) {
    const key = `${root.path}:${rel}:${entry.size}:${entry.mtimeMs}`;
    if (hashes.has(key)) return hashes.get(key);
    const r = gate.read(root, rel, {maxBytes: MAX_BYTES});
    const value = r.ok ? {sha: sha(r.bytes), trim: sha(Buffer.from(r.bytes.toString('utf8').trim(), 'utf8')), bytes: r.bytes.length} : null;
    hashes.set(key, value);
    while (hashes.size > 2000) hashes.delete(hashes.keys().next().value);
    return value;
  }

  // The listing is reused for a moment, so that a list of many runs does not list every dossier again for each run.
  let cached = null;
  /** The sources and reports of every configured dossier, with their hashes. */
  function scan() {
    if (cached && Date.now() - cached.at < 250) return cached.value;
    const value = scanNow();
    cached = {at: Date.now(), value};
    return value;
  }

  function scanNow() {
    return folders.map((root, i) => {
      const sources = gate.list(root, 'sources').filter(e => e.kind === 'file' && SOURCE_NAME.test(e.name) && e.size <= MAX_BYTES).slice(0, MAX_SOURCES)
        .map(e => ({name: e.name, h: hashed(root, `sources/${e.name}`, e)})).filter(s => s.h)
        .map(s => ({name: s.name, bytes: s.h.bytes, sha: s.h.sha, trim: s.h.trim, sha12: s.h.sha.slice(0, 12)}));
      const reports = gate.list(root, '').filter(e => e.kind === 'file' && REPORT_NAME.test(e.name) && e.size <= MAX_BYTES).slice(0, MAX_REPORTS)
        .map(e => ({file: e.name, h: hashed(root, e.name, e)})).filter(r => r.h)
        .map(r => ({file: r.file, bytes: r.h.bytes, sha: r.h.sha, trim: r.h.trim, sha12: r.h.sha.slice(0, 12)}))
        .sort((a, b) => (a.sha12 < b.sha12 ? -1 : a.sha12 > b.sha12 ? 1 : 0));
      return {n: i + 1, id: path.basename(root.path), root, sources, reports};
    });
  }

  /** Which dossier file has these bytes: by exact hash, else a dossier text equal to it after trimming. */
  function matchHash(exact, trimmed = null) {
    if (typeof exact !== 'string') return null;
    for (const pass of ['exact', 'trim']) {
      for (const d of scan()) {
        for (const s of d.sources) if ((pass === 'exact' ? s.sha : s.trim) === exact) return {n: d.n, id: d.id, role: 'source', file: s.name, sha12: s.sha12, trimmed: pass === 'trim'};
        for (const r of d.reports) if ((pass === 'exact' ? r.sha : r.trim) === exact) return {n: d.n, id: d.id, role: 'report', sha12: r.sha12, trimmed: pass === 'trim'};
      }
    }
    return null;
  }

  /** The links between one run and every dossier it matches, best first. */
  function linksOf(run, {documents, reportUnderReview}, scanned = scan()) {
    const docs = documents(run).map(d => d.sha256).filter(Boolean);
    const exact = new Set(docs);
    const report = reportUnderReview(run);
    const links = [];
    for (const d of scanned) {
      let matched = 0;
      let viaTrim = false;
      for (const s of d.sources) {
        if (exact.has(s.sha)) matched++;
        else if (exact.has(s.trim)) {
          matched++;
          viaTrim = true;
        }
      }
      const reportHit = report ? d.reports.find(r => r.sha === report.sha256 || r.trim === report.sha256) : null;
      if (matched === 0 && !reportHit) continue;
      const match = matched === 0 ? 'report' : matched === d.sources.length ? 'full' : 'partial';
      const trimmed = viaTrim || Boolean(reportHit && reportHit.sha !== report.sha256);
      links.push({n: d.n, id: d.id, match, matched, trimmed, report: reportHit ? reportHit.sha12 : null});
    }
    const rank = {full: 3, partial: 2, report: 1};
    links.sort((a, b) => rank[b.match] - rank[a.match] || b.matched - a.matched || a.n - b.n);
    return links;
  }

  return {
    matchHash,
    /** The best link of a run, as shown in the run list: `{id, match, trimmed}` or null. */
    linkRun(run, helpers) {
      const best = linksOf(run, helpers)[0];
      return best ? {id: best.id, match: best.match, trimmed: best.trimmed} : null;
    },
    /** `[{n, id, sources, reports, runs}]`. Reports carry no file name and are sorted by digest. */
    list(runs = [], helpers = null) {
      const scanned = scan();
      const per = new Map(scanned.map(d => [d.n, []]));
      if (helpers) {
        for (const run of runs) {
          for (const link of linksOf(run, helpers, scanned)) per.get(link.n)?.push({key: run.key, match: link.match, trimmed: link.trimmed, report: link.report});
        }
      }
      return scanned.map(d => ({
        n: d.n, id: d.id,
        sources: d.sources.map(s => ({name: s.name, bytes: s.bytes, sha12: s.sha12})),
        reports: d.reports.map(r => ({sha12: r.sha12, bytes: r.bytes, neutralName: neutralName(r.sha12)})),
        runs: per.get(d.n)
      }));
    },
    /** The bytes of a source or report for the document and download routes. */
    file(n, name) {
      const d = scan().find(x => x.n === n);
      if (!d) throw new ViewerError(404, 'This dossier does not exist.');
      const [kind, ...rest] = String(name ?? '').split(':');
      const arg = rest.join(':');
      let rel = null;
      let filename = null;
      if (kind === 'source') {
        const s = d.sources.find(x => x.name === arg);
        if (s) {
          rel = `sources/${s.name}`;
          filename = s.name;
        }
      } else if (kind === 'report' && SHA12.test(arg)) {
        const r = d.reports.find(x => x.sha12 === arg);
        if (r) {
          rel = r.file;
          filename = neutralName(r.sha12);
        }
      }
      if (!rel) throw new ViewerError(404, 'This file is not in the dossier.');
      const r = gate.read(d.root, rel, {maxBytes: MAX_BYTES});
      if (!r.ok) throw new ViewerError(404, 'This file could not be read.');
      return {bytes: r.bytes, filename};
    },
    document(n, name, part = 0) {
      const {bytes} = this.file(n, name);
      const piece = partOf(bytes.toString('utf8'), part);
      if (!piece) throw new ViewerError(404, 'There is no such part of this document.');
      return {format: 'markdown', ...piece};
    }
  };
}
