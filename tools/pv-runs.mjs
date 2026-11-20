/** Run discovery, classification and status for the Deep Proof process viewer.
 *
 * Runs are found by listing `projects/<pid>/analyses/*` and `projects/<pid>/traces/*` through the read gate; the viewer
 * never opens Deep Proof's database. A run with a trace (version 1) takes its facts and status from it. A run without
 * one gets a status worked out from its files, always marked as inferred. */
import crypto from 'node:crypto';
import * as defaultGate from './pv-read.mjs';
import {isId} from './pv-trace.mjs';

export const STATUS_LABELS = {
  running: 'Running', completed: 'Completed', 'time-limit': 'Time Limit Reached', failed: 'Failed',
  interrupted: 'Interrupted', 'not-recorded': 'Not Recorded', imported: 'Imported Report'
};
const KIND_LABELS = {analysis: 'Analysis', quality: 'Check Report', imported: 'Imported Report', unknown: 'Unknown Kind'};
const FAILURES = {validation: 'Validation', engine: 'Engine', access: 'Service Access', empty_response: 'Empty Response', timeout: 'Timeout'};
const BOUND_MARGIN_MS = 120000;
const MAX_RUNS = 2000;
const INPUT_LIMIT = 30 * 1024 * 1024;
const CACHE = 20000;

/** Liveness of a process: signal 0 sends nothing. The pid comes from a trace an agent could have written, so only a
 * positive integer is tried. A process that exists but belongs to another user still counts as alive. */
export function processAlive(pid) {
  if (!Number.isSafeInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === 'EPERM';
  }
}

const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const iso = ms => (Number.isFinite(ms) ? new Date(ms).toISOString() : null);
const msOf = value => {
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : null;
};

function remember(map, key, value) {
  map.delete(key);
  map.set(key, value);
  while (map.size > CACHE) map.delete(map.keys().next().value);
}

export function createRunIndex({roots, traces, gate = defaultGate, now = Date.now, alive = processAlive, link = null}) {
  const data = roots?.data ?? null;
  const names = new Map();
  const inputs = new Map();
  const hashes = new Map();

  const folderRel = (pid, rid) => `projects/${pid}/analyses/${rid}`;

  function projectName(pid) {
    const rel = `projects/${pid}/project.md`;
    const st = gate.stat(data, rel);
    if (!st.ok) return pid;
    const key = `${pid}:${st.size}:${st.mtimeMs}`;
    const cached = names.get(pid);
    if (cached?.key === key) return cached.name;
    const r = gate.read(data, rel, {maxBytes: 65536});
    const m = r.ok ? /^#\s+(.+?)\s*$/m.exec(r.bytes.toString('utf8')) : null;
    const name = (m?.[1] ?? pid).slice(0, 160);
    names.set(pid, {key, name});
    return name;
  }

  /** The few facts of input.json that identify a run without a trace. Cached by size and modification time. */
  function inputFacts(pid, rid, entry) {
    if (!entry) return null;
    const key = `${entry.size}:${entry.mtimeMs}`;
    const cached = inputs.get(`${pid}/${rid}`);
    if (cached?.key === key) return cached.facts;
    let facts = null;
    const r = gate.read(data, `${folderRel(pid, rid)}/input.json`, {maxBytes: INPUT_LIMIT});
    if (r.ok) {
      try {
        const json = JSON.parse(r.bytes.toString('utf8'));
        facts = {
          kind: json.kind ?? null,
          skillName: typeof json.type?.name === 'string' ? json.type.name : null,
          skillId: json.type?.id ?? null,
          documents: Array.isArray(json.documents) ? json.documents.filter(d => d && isId(d.id)).map(d => ({id: d.id, title: typeof d.title === 'string' ? d.title : d.id, originalName: d.original_name ?? null})) : [],
          sourceReport: json.sourceReport && typeof json.sourceReport === 'object' ? {documentId: json.sourceReport.documentId ?? null, title: json.sourceReport.title ?? null} : null,
          timeLimitMinutes: json.timeLimitMinutes ?? null,
          qualityOptions: json.qualityOptions ?? null,
          reportLanguage: json.report_language ?? null
        };
      } catch {
        facts = null;
      }
    }
    remember(inputs, `${pid}/${rid}`, {key, facts});
    return facts;
  }

  function configFacts(pid, rid, entry) {
    if (!entry) return {agent: null, model: null};
    const r = gate.read(data, `${folderRel(pid, rid)}/config.json`, {maxBytes: 65536});
    if (!r.ok) return {agent: null, model: null};
    try {
      const json = JSON.parse(r.bytes.toString('utf8'));
      const agent = typeof json.codingAgent === 'string' ? json.codingAgent : null;
      const model = agent && json.models && typeof json.models[agent] === 'string' ? json.models[agent] : null;
      return {agent, model};
    } catch {
      return {agent: null, model: null};
    }
  }

  function firstHeading(pid, rid) {
    const r = gate.read(data, `${folderRel(pid, rid)}/report.md`, {offset: 0, length: 4096, maxBytes: 4096});
    const m = r.ok ? /^#\s+(.+?)\s*$/m.exec(r.bytes.toString('utf8')) : null;
    return m ? m[1].slice(0, 160) : null;
  }

  /** The status of a run from its files alone. Returns null when nothing shows how it ended. */
  function inferStatus(kind, files, check) {
    const has = name => files.has(name);
    const validation = (() => {
      if (!has('validation-result.json')) return null;
      const r = check.read('validation-result.json');
      try {
        return JSON.parse(r.toString('utf8'));
      } catch {
        return null;
      }
    })();
    if (validation?.valid === true && validation.attempt !== 'wrapup' && has('report.md')) return {status: 'completed', detail: ''};
    if (validation?.valid === true && validation.attempt === 'wrapup') return {status: 'time-limit', detail: 'Validated wrap-up report'};
    const noValid = validation?.valid !== true;
    if (has('task-wrapup.txt') && noValid) return {status: 'time-limit', detail: 'Notes only'};
    if (has('validation-errors-3.json') && noValid && !has('task-wrapup.txt')) return {status: 'failed', detail: 'Validation'};
    if (kind === 'quality' && has('verification-index.json')) return {status: 'completed', detail: ''};
    if (kind === 'quality' && has('report.md') && !has('verification-index.json')) return {status: 'time-limit', detail: 'Notes only'};
    return null;
  }

  function describeRun(pid, rid, dirEntry = null) {
    if (!data || !isId(pid) || !isId(rid)) return null;
    const folder = gate.tryList(data, folderRel(pid, rid));
    const trace = traces.summary(pid, rid);
    if (!folder.ok && !trace) return null;
    const files = new Map();
    const dirs = new Set();
    for (const e of folder.entries) (e.kind === 'file' ? files.set(e.name, e) : dirs.add(e.name));
    const run = {
      key: `${pid}/${rid}`, pid, rid, projectName: projectName(pid), runFolderExists: folder.ok, files, dirs, trace: null, otherVersion: null,
      traced: false, kind: 'unknown', title: null, agent: null, model: null, attempt: null, maxAttempts: null,
      status: 'not-recorded', statusDetail: '', statusSource: 'inferred', startedAt: null, endedAt: null, lastActivityAt: null, runStart: null
    };
    const head = trace?.head;
    const current = head && head.type === 'run-start' && head.schema === 'deep-proof-trace' && head.version === 1 && trace.runStart;
    if (trace && head?.type === 'run-start' && !current) run.otherVersion = head.version ?? null;
    const lastFileMs = Math.max(0, ...[...files.values()].map(f => f.mtimeMs), dirEntry?.mtimeMs ?? 0);

    if (current) {
      const rs = trace.runStart;
      run.traced = true;
      run.trace = trace;
      run.runStart = rs;
      run.kind = rs.kind === 'quality' ? 'quality' : 'analysis';
      run.title = (run.kind === 'quality' ? rs.title : null) || rs.skill?.name || (run.kind === 'quality' ? 'Check Report' : 'Analysis');
      run.agent = rs.agent ?? null;
      run.model = rs.model ?? null;
      run.attempt = trace.attempt;
      run.maxAttempts = Number.isInteger(rs.maxAttempts) ? rs.maxAttempts : null;
      run.statusSource = 'trace';
      run.startedAt = typeof rs.createdAt === 'string' ? rs.createdAt : rs.at;
      run.lastActivityAt = trace.lastAt ?? iso(trace.mtimeMs);
      const end = trace.runEnd;
      if (end) {
        run.endedAt = end.at ?? null;
        if (end.status === 'completed') {
          if (end.timeLimited) {
            run.status = 'time-limit';
            run.statusDetail = end.partial ? 'Notes only' : 'Validated wrap-up report';
          } else run.status = 'completed';
        } else {
          run.status = 'failed';
          run.statusDetail = FAILURES[end.failureCode] || 'Unexpected';
        }
      } else {
        const start = msOf(run.startedAt);
        const workMs = Number.isFinite(rs.workMs) ? rs.workMs : 0;
        const wrapMs = Number.isFinite(rs.wrapUpMs) ? rs.wrapUpMs : 0;
        const late = start !== null && start + workMs + wrapMs + BOUND_MARGIN_MS < now();
        const pidAlive = alive(rs.server?.pid);
        if (pidAlive && !late) {
          run.status = 'running';
          run.statusDetail = trace.attempt === 'wrapup' || trace.wrapUp ? 'Wrap-up turn' : '';
        } else {
          run.status = 'interrupted';
          run.statusDetail = !pidAlive ? 'The Deep Proof server is gone' : 'The time limit and its margin have passed';
        }
      }
    } else {
      // Classification from files: first match wins.
      const originals = [...files.keys()].some(n => n.startsWith('original.'));
      let check = false;
      if (!originals) {
        if (dirs.has('quality') || dirs.has('quality-input') || files.has('verification-index.json')) check = true;
        else if (dirs.has('sources')) check = gate.list(data, `${folderRel(pid, rid)}/sources`).some(e => /^report-under-review.*\.md$/.test(e.name));
      }
      run.kind = originals ? 'imported' : check ? 'quality' : files.has('task.txt') ? 'analysis' : 'unknown';
      const facts = inputFacts(pid, rid, files.get('input.json'));
      const config = configFacts(pid, rid, files.get('config.json'));
      run.agent = config.agent;
      run.model = config.model;
      if (facts) {
        if (run.kind === 'quality') run.title = facts.documents.find(d => d.id === facts.sourceReport?.documentId)?.title ?? facts.sourceReport?.title ?? null;
        else run.title = facts.skillName;
      }
      if (run.kind === 'imported') run.title = firstHeading(pid, rid);
      run.title = run.title || (run.kind === 'unknown' ? run.rid : KIND_LABELS[run.kind]);
      const known = [...files.values()];
      const dates = known.filter(f => f.name === 'task.txt' || f.name === 'input.json' || f.name === 'task-1.txt').map(f => f.mtimeMs);
      run.startedAt = iso(dates.length ? Math.min(...dates) : (known.length ? Math.min(...known.map(f => f.mtimeMs)) : dirEntry?.mtimeMs ?? null));
      run.lastActivityAt = iso(lastFileMs || null);
      // A trace of another version is not read: the files decide.
      if (run.kind === 'imported') run.status = 'imported';
      else {
        const inferred = inferStatus(run.kind, files, {read: name => {
          const r = gate.read(data, `${folderRel(pid, rid)}/${name}`, {maxBytes: 65536});
          return r.ok ? r.bytes : Buffer.alloc(0);
        }});
        if (inferred) {
          run.status = inferred.status;
          run.statusDetail = inferred.detail;
          const stamp = files.get('validation-result.json') ?? files.get('validation-errors-3.json') ?? files.get('verification-index.json') ?? files.get('report.md');
          if (inferred.status !== 'running') run.endedAt = iso(stamp?.mtimeMs ?? null);
        } else {
          run.status = 'not-recorded';
          run.statusDetail = "Running, or ended without a result. Deep Proof's own page knows.";
        }
      }
    }
    run.statusLabel = STATUS_LABELS[run.status];
    run.kindLabel = KIND_LABELS[run.kind];
    return run;
  }

  /** Hash of a file below a run folder, cached by size and modification time. Null when it cannot be read. */
  function fileHash(pid, rid, rel, entry, {trim = false} = {}) {
    const key = `${pid}/${rid}/${rel}:${entry.size}:${entry.mtimeMs}:${trim}`;
    if (hashes.has(key)) return hashes.get(key);
    const r = gate.read(data, `${folderRel(pid, rid)}/${rel}`, {maxBytes: 4 * 1024 * 1024});
    const value = r.ok ? sha(trim ? Buffer.from(r.bytes.toString('utf8').trim(), 'utf8') : r.bytes) : null;
    remember(hashes, key, value);
    return value;
  }

  /** The documents of a run with their SHA-256: from the trace when it has them, else from `sources/<id>.md`. */
  function documents(run) {
    const out = [];
    const traced = run.runStart?.documents;
    const titles = new Map();
    if (Array.isArray(traced)) for (const d of traced) if (d && isId(d.id)) titles.set(d.id, d);
    const sourceFiles = run.runFolderExists && run.dirs.has('sources') ? gate.list(data, `${folderRel(run.pid, run.rid)}/sources`) : [];
    const have = new Map(sourceFiles.filter(e => e.kind === 'file' && /^[A-Za-z0-9_-]+\.md$/.test(e.name) && e.name !== 'index.md').map(e => [e.name.replace(/\.md$/, ''), e]));
    for (const [id, d] of titles) {
      const entry = have.get(id);
      out.push({id, title: d.title ?? id, originalName: d.originalName ?? null, bytes: d.bytes ?? entry?.size ?? null, sha256: typeof d.sha256 === 'string' ? d.sha256 : entry ? fileHash(run.pid, run.rid, `sources/${id}.md`, entry) : null,
        trimmedSha256: entry ? fileHash(run.pid, run.rid, `sources/${id}.md`, entry, {trim: true}) : null});
    }
    if (!titles.size) {
      const facts = inputFacts(run.pid, run.rid, run.files.get('input.json'));
      for (const [id, entry] of have) {
        const f = facts?.documents.find(d => d.id === id);
        out.push({id, title: f?.title ?? id, originalName: f?.originalName ?? null, bytes: entry.size, sha256: fileHash(run.pid, run.rid, `sources/${id}.md`, entry), trimmedSha256: fileHash(run.pid, run.rid, `sources/${id}.md`, entry, {trim: true})});
      }
    }
    return out;
  }

  /** The report under review of a run: `sources/<documentId>.md` of a Check Report, `report.md` of an imported report. */
  function reportUnderReview(run) {
    if (!run.runFolderExists) return null;
    let documentId = null;
    if (run.kind === 'quality') {
      documentId = run.runStart?.sourceReport?.documentId ?? inputFacts(run.pid, run.rid, run.files.get('input.json'))?.sourceReport?.documentId ?? null;
      if (!isId(documentId)) return null;
      const rel = `sources/${documentId}.md`;
      const entry = gate.list(data, `${folderRel(run.pid, run.rid)}/sources`).find(e => e.name === `${documentId}.md`);
      return entry ? {documentId, sha256: fileHash(run.pid, run.rid, rel, entry), trimmedSha256: fileHash(run.pid, run.rid, rel, entry, {trim: true})} : null;
    }
    if (run.kind === 'imported') {
      const entry = run.files.get('report.md');
      return entry ? {documentId: null, sha256: fileHash(run.pid, run.rid, 'report.md', entry), trimmedSha256: fileHash(run.pid, run.rid, 'report.md', entry, {trim: true})} : null;
    }
    return null;
  }

  const publicItem = run => ({
    key: run.key, projectName: run.projectName, kind: run.kind, kindLabel: run.kindLabel, title: run.title, status: run.status, statusLabel: run.statusLabel,
    statusDetail: run.statusDetail, statusSource: run.statusSource, startedAt: run.startedAt, endedAt: run.endedAt, lastActivityAt: run.lastActivityAt,
    agent: run.agent, model: run.model, attempt: run.attempt, maxAttempts: run.maxAttempts, traced: run.traced,
    dossier: link ? link(run, {documents, reportUnderReview}) : null
  });

  function discover() {
    const found = [];
    if (!data) return found;
    for (const project of gate.list(data, 'projects')) {
      if (project.kind !== 'dir' || !isId(project.name)) continue;
      const seen = new Map();
      for (const e of gate.list(data, `projects/${project.name}/analyses`)) if (e.kind === 'dir' && isId(e.name)) seen.set(e.name, e);
      for (const e of gate.list(data, `projects/${project.name}/traces`)) if (e.kind === 'dir' && isId(e.name) && !seen.has(e.name)) seen.set(e.name, null);
      for (const [rid, entry] of seen) found.push({pid: project.name, rid, entry});
    }
    return found;
  }

  return {
    /** Every run, newest first, at most MAX_RUNS. Internal descriptors; use `publicList` for the route. */
    all() {
      const runs = [];
      for (const {pid, rid, entry} of discover()) {
        try {
          const run = describeRun(pid, rid, entry);
          if (run) runs.push(run);
        } catch {
          // One unreadable run must not hide the others.
        }
      }
      const stamp = r => msOf(r.startedAt) ?? msOf(r.lastActivityAt) ?? 0;
      runs.sort((a, b) => stamp(b) - stamp(a) || (a.key < b.key ? -1 : 1));
      return runs.slice(0, MAX_RUNS);
    },
    publicList() {
      return this.all().map(publicItem);
    },
    get: (pid, rid) => describeRun(pid, rid),
    publicItem,
    documents,
    reportUnderReview
  };
}
