/** Reader of the Deep Proof process trace (version 1) and builder of the readable timeline.
 *
 * The trace is a JSON Lines file written by another program while the viewer reads it. This module keeps an index per
 * trace (bytes consumed, the file's inode, one entry per timeline step with the byte spans of its records) and never the
 * bodies: a body is read again on demand through the read gate. Only complete lines are consumed, so a line that is
 * still being written is left for the next poll. Entry numbers are append-only: an entry is created only when it can no
 * longer change (a streamed message is held until its output completes, its kind or id changes, or any other record
 * arrives). The logic that joins streamed fragments is written here from the contract; this module imports no other
 * viewer module. */
import * as defaultGate from './pv-read.mjs';

export const PART_BYTES = 262144;
export const TIMELINE_CAP = 500;
const CHUNK = 1024 * 1024;
const MAX_LINE = 4 * 1024 * 1024;
const MAX_ENTRIES = 20000;
const MAX_FRAGMENTS = 20000;
const JOIN_CHARS = 1000000;
const SUMMARY_CACHE = 4000;
const TIMELINE_CACHE = 4;
const ID = /^[a-zA-Z0-9_-]+$/;
// One scan buffer for every trace: lines are parsed at once, so the bytes of a chunk are never kept.
const SCAN = Buffer.allocUnsafe(MAX_LINE + 1);

/** An error with an HTTP status and words fit for the page. Never carries a path. */
export class ViewerError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export const isId = value => typeof value === 'string' && value.length > 0 && value.length <= 200 && ID.test(value);

// ---------- Text helpers shared with pv-documents ----------

/** Hides absolute-looking paths in a short line that came from a host record. Agent text is not changed. */
export const scrub = text => String(text ?? '').replace(/(?<![\w:/])(?:\/[\w.@%+~-]+){2,}/g, '<path>');
export const firstLine = (text, limit = 200) => {
  const line = String(text ?? '').split(/\r?\n/).find(l => l.trim()) ?? '';
  const flat = line.trim().replace(/\s+/g, ' ');
  return flat.length > limit ? flat.slice(0, limit - 1) + '…' : flat;
};
export function formatBytes(n) {
  if (!Number.isFinite(n)) return 'unknown size';
  if (n < 1024) return `${n} bytes`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
export function formatDuration(ms) {
  if (!Number.isFinite(ms)) return 'unknown';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} s`;
  return `${Math.floor(s / 60)} min ${s % 60} s`;
}
/** A Markdown table cell: no pipe, no line break. */
export const cell = value => String(value ?? 'Not recorded').replace(/\r?\n/g, ' ').replace(/\|/g, '\\|').trim() || 'Not recorded';
export function table(headers, rows) {
  if (!rows.length) return '';
  return [`| ${headers.map(cell).join(' | ')} |`, `| ${headers.map(() => '---').join(' | ')} |`, ...rows.map(r => `| ${r.map(cell).join(' | ')} |`)].join('\n');
}
const plain = value => (value === null || ['string', 'number', 'boolean'].includes(typeof value));

/** Splits a text into parts of at most PART_BYTES bytes, never inside a character. Returns null past the last part. */
let lastText = null;
let lastBuffer = null;
export function partOf(text, part = 0) {
  const k = Number.isInteger(part) && part >= 0 ? part : 0;
  // The parts of one long text are asked for one after the other: its bytes are made once.
  if (typeof text !== 'string' || text !== lastText) {
    lastBuffer = Buffer.from(String(text), 'utf8');
    lastText = typeof text === 'string' && lastBuffer.length > PART_BYTES ? text : null;
  }
  const buffer = lastBuffer;
  if (buffer.length <= PART_BYTES) return k === 0 ? {text: buffer.toString('utf8'), part: 0, parts: 1} : null;
  const bounds = [0];
  while (bounds.at(-1) < buffer.length) {
    let end = Math.min(bounds.at(-1) + PART_BYTES, buffer.length);
    while (end < buffer.length && (buffer[end] & 0xc0) === 0x80) end--;
    bounds.push(end);
  }
  const parts = bounds.length - 1;
  if (k >= parts) return null;
  return {text: buffer.subarray(bounds[k], bounds[k + 1]).toString('utf8'), part: k, parts};
}

// ---------- Entry descriptions ----------

const KIND_LABELS = {analysis: 'Analysis', quality: 'Check Report'};
export const kindLabel = kind => KIND_LABELS[kind] || 'Run';
const attemptLabel = attempt => (attempt === 'wrapup' ? 'Wrap-Up' : `Attempt ${attempt ?? '?'}`);
const PUBLISHED = {report: 'The report', 'check-report': 'The Check Report', 'time-limit-notes': 'The time-limit notes'};

function numbered(list) {
  return (Array.isArray(list) ? list : []).map((e, i) => `${i + 1}. ${cell(typeof e === 'string' ? e : JSON.stringify(e))}`).join('\n');
}

function fieldsTable(record) {
  const rows = Object.entries(record).filter(([k, v]) => !['seq', 'at', 'type'].includes(k) && plain(v)).map(([k, v]) => [k, v]);
  return table(['Field', 'Value'], rows);
}

/** Describes one record that is not a streamed message fragment: kind, title, one-line summary and whether it has a body. */
function describe(rec) {
  const ev = rec.event && typeof rec.event === 'object' ? rec.event : {};
  switch (rec.type) {
    case 'run-start':
      return {kind: 'run-started', title: 'Run Started', summary: `${kindLabel(rec.kind)}, ${Array.isArray(rec.documents) ? rec.documents.length : 0} documents`, hasBody: false};
    case 'workspace-created':
      return {kind: 'workspace', title: 'Work Folder Created', summary: 'The Check Report work folder was made', hasBody: false};
    case 'workspace-kept':
      return {kind: 'workspace', title: 'Work Folder Copy Kept', summary: `${rec.files ?? '?'} files, ${formatBytes(rec.bytes)}${rec.complete === false ? ', incomplete' : ''}`, hasBody: true};
    case 'workspace-keep-failed':
      return {kind: 'workspace', title: 'Work Folder Copy Failed', summary: firstLine(scrub(rec.message)), hasBody: true};
    case 'turn-start': {
      const repair = rec.repair === true;
      return {
        kind: repair ? 'repair-prompt' : 'prompt-sent',
        title: `${repair ? 'Repair Prompt Sent' : 'Prompt Sent'} (${attemptLabel(rec.attempt)})`,
        summary: `${formatBytes(rec.promptBytes)}${rec.resume ? ', resumed session' : ''}${rec.continuation ? ', continuation' : ''}`,
        hasBody: true
      };
    }
    case 'ala-event':
      switch (ev.type) {
        case 'coding-agent-selected':
          return {kind: 'agent-selected', title: 'Agent Selected', summary: `${ev.agent ?? 'unknown agent'}, ${ev.model || 'agent default'}`, hasBody: true};
        case 'coding-agent-final':
          return {kind: 'final-message', title: 'Final Agent Message', summary: firstLine(ev.message), hasBody: true};
        case 'coding-agent-request':
          return {kind: 'agent-request', title: 'Agent Request', summary: firstLine(scrub(ev.title || ev.message)), hasBody: true};
        case 'coding-agent-request-resolved':
          return {kind: 'agent-request', title: 'Agent Request Resolved', summary: firstLine(scrub(ev.title || ev.message)), hasBody: true};
        case 'message-accepted':
          return {kind: 'reminder', title: 'Progress Reminder Accepted', summary: 'The agent accepted the reminder', hasBody: true};
        case 'message-rejected':
          return {kind: 'reminder', title: 'Progress Reminder Rejected', summary: 'The agent rejected the reminder', hasBody: true};
        default:
          return {kind: 'other', title: 'Other Step', summary: `Engine event ${scrub(ev.type ?? 'without a type')}`, hasBody: true};
      }
    case 'reminder-sent':
      return {kind: 'reminder', title: 'Progress Reminder Sent', summary: 'A reminder was sent to the agent', hasBody: true};
    case 'progress-note':
      return {kind: 'progress-note', title: 'Progress Note', summary: firstLine(rec.text), hasBody: true};
    case 'turn-end':
      return {kind: 'turn-ended', title: 'Turn Ended', summary: `${rec.outcome ?? 'ended'}${rec.exitCode !== null && rec.exitCode !== undefined ? `, exit code ${rec.exitCode}` : ''}, ${formatDuration(rec.durationMs)}`, hasBody: true};
    case 'validation':
      return rec.valid
        ? {kind: 'validation-passed', title: 'Validation Passed', summary: `${attemptLabel(rec.attempt)}`, hasBody: false}
        : {kind: 'validation-failed', title: `Validation Failed (${rec.errorCount ?? (Array.isArray(rec.errors) ? rec.errors.length : '?')} errors)`, summary: firstLine(scrub(Array.isArray(rec.errors) ? rec.errors[0] : '')), hasBody: true};
    case 'continuation':
      return {kind: 'continuation', title: 'Empty Response: Continuing', summary: `Retry ${rec.used ?? '?'} in the same session`, hasBody: true};
    case 'time-limit':
      return {kind: 'time-limit', title: 'Time Limit Reached', summary: `${formatDuration(rec.elapsedMs)} of ${formatDuration(rec.workMs)}`, hasBody: true};
    case 'wrap-up': {
      const titles = {start: 'Wrap-Up Turn', validated: 'Wrap-Up Validated', 'not-validated': 'Wrap-Up Not Validated'};
      return {kind: 'wrap-up', title: titles[rec.phase] || 'Wrap-Up Turn', summary: rec.remainingMs ? `${formatDuration(rec.remainingMs)} left` : '', hasBody: false};
    }
    case 'published':
      return {kind: 'published', title: 'Published', summary: `${PUBLISHED[rec.what] || 'A result'} was written${rec.timeLimited ? ' after the time limit' : ''}`, hasBody: true};
    case 'ala-stderr':
      return {kind: 'diagnostic', title: 'Engine Diagnostic', summary: firstLine(scrub(rec.text)), hasBody: true};
    case 'trace-limit':
      return {kind: 'trace-limit', title: 'Trace Size Limit Reached', summary: `${formatBytes(rec.traceBytes)} written`, hasBody: true};
    case 'run-end':
      return {kind: 'run-ended', title: rec.status === 'failed' ? 'Run Failed' : 'Run Completed', summary: rec.status === 'failed' ? firstLine(scrub(rec.error)) || `Failure: ${rec.failureCode ?? 'unexpected'}` : rec.timeLimited ? 'Stopped by the time limit' : 'Finished', hasBody: true};
    default:
      return {kind: 'other', title: 'Other Step', summary: scrub(String(rec.type)), hasBody: true};
  }
}

const LEGEND = "ALA reports a command's output, not the command line.";

/** The body of one entry from its records. Returns {format, text, note?} or {needsPrompt: turn}. */
function bodyFrom(entry, records) {
  const rec = records[0];
  const ev = rec.event && typeof rec.event === 'object' ? rec.event : {};
  switch (entry.kind) {
    case 'agent-message':
    case 'command-output': {
      let text = '';
      for (const r of records) {
        text += typeof r.event?.message === 'string' ? r.event.message : '';
        if (text.length > JOIN_CHARS) {
          text = text.slice(0, JOIN_CHARS);
          break;
        }
      }
      return entry.kind === 'agent-message' ? {format: 'markdown', text} : {format: 'text', text, note: LEGEND};
    }
    case 'prompt-sent':
    case 'repair-prompt':
      return {needsPrompt: rec.turn ?? null, format: 'text', text: ''};
    case 'agent-selected':
      return {format: 'markdown', text: table(['Field', 'Value'], [['Agent', ev.agent], ['Model', ev.model || 'Agent default'], ['Permission mode', ev.permissionMode]])};
    case 'final-message':
      return {format: 'markdown', text: String(ev.message ?? '')};
    case 'agent-request':
      return {format: 'markdown', text: [ev.title ? `**${cell(ev.title)}**` : '', String(ev.message ?? ''), ev.detail ? `\n${String(ev.detail)}` : ''].filter(Boolean).join('\n\n')};
    case 'reminder':
      return {format: 'text', text: rec.type === 'reminder-sent' ? 'A progress reminder was written to the agent.' : ev.type === 'message-accepted' ? 'The agent accepted the progress reminder.' : 'The agent rejected the progress reminder.'};
    case 'progress-note':
      return {format: 'markdown', text: String(rec.text ?? '')};
    case 'turn-ended':
      return {format: 'markdown', text: table(['Field', 'Value'], [['Outcome', rec.outcome], ['Exit code', rec.exitCode], ['Signal', rec.signal], ['Duration', formatDuration(rec.durationMs)], ['Error', scrub(rec.error)]])};
    case 'validation-failed':
      return {format: 'markdown', text: numbered(rec.errors)};
    case 'continuation':
      return {format: 'text', text: `The reply was empty (${rec.reason ?? 'empty_response'}); Deep Proof asked the agent to continue in the same session.`};
    case 'time-limit':
      return {format: 'markdown', text: table(['Field', 'Value'], [['Elapsed', formatDuration(rec.elapsedMs)], ['Limit', formatDuration(rec.workMs)]])};
    case 'published':
      return {format: 'text', text: `${PUBLISHED[rec.what] || 'A result'} was written${rec.timeLimited ? ' after the time limit' : ''}.`};
    case 'diagnostic':
      return {format: 'text', text: scrub(rec.text)};
    case 'trace-limit':
      return {format: 'text', text: `The trace reached its size limit (${formatBytes(rec.traceBytes)}). Later engine lines were counted, not written.`};
    case 'workspace': {
      if (rec.type === 'workspace-keep-failed') return {format: 'text', text: scrub(rec.message)};
      const skipped = Array.isArray(rec.skipped) ? rec.skipped.map(s => [s?.name, s?.reason]) : [];
      return {format: 'markdown', text: [table(['Field', 'Value'], [['Files', rec.files], ['Bytes', rec.bytes], ['Skipped', rec.skippedCount], ['Complete', rec.complete], ['Duration', formatDuration(rec.durationMs)]]), skipped.length ? '\n' + table(['Skipped file', 'Reason'], skipped) : ''].join('\n')};
    }
    case 'run-ended':
      return {format: 'markdown', text: table(['Field', 'Value'], [['Status', rec.status], ['Failure code', rec.failureCode], ['Time limited', rec.timeLimited], ['Dropped engine lines', rec.droppedAlaLines], ['Error', scrub(rec.error)]])};
    default:
      return {format: 'markdown', text: `**${cell(rec.type)}**\n\n${fieldsTable(rec) || 'No plain fields.'}`};
  }
}

// ---------- Scanner ----------

const newState = (rel, wantEntries) => ({
  rel, wantEntries, ino: null, offset: 0, size: 0, mtimeMs: 0, skipping: false,
  count: 0, bad: 0, oversize: 0, head: null, runStart: null, runEnd: null, lastAt: null, lastSeq: null, turn: null, attempt: null,
  wrapUp: null, timeLimit: false, published: null, workspacePath: null, kept: null, keepFailed: false, validations: 0, lastValidation: null,
  startMs: null, entries: [], dropped: 0, pending: null, limitNoted: false
});

function emit(state, entry) {
  if (!state.wantEntries) return;
  if (state.entries.length >= MAX_ENTRIES) {
    if (!state.limitNoted) {
      state.limitNoted = true;
      state.entries.push({n: state.entries.length + 1, at: entry.at, offsetMs: entry.offsetMs, kind: 'other', title: 'Other Step', summary: 'Viewer entry limit reached; later steps are not listed', hasBody: false, turn: null, seq: null, spans: [], limit: true});
    }
    state.dropped++;
    return;
  }
  state.entries.push({n: state.entries.length + 1, ...entry});
}

function offsetOf(state, at) {
  const ms = Date.parse(at);
  return Number.isFinite(ms) && state.startMs !== null ? Math.max(0, ms - state.startMs) : 0;
}

function flushPending(state) {
  const p = state.pending;
  state.pending = null;
  if (!p || p.chars === 0) return;
  const assistant = p.kind === 'assistant';
  emit(state, {
    at: p.at, offsetMs: offsetOf(state, p.at), kind: assistant ? 'agent-message' : 'command-output', title: assistant ? 'Agent Message' : 'Command Output',
    summary: firstLine(p.head), hasBody: true, turn: p.turn, seq: p.seq, spans: p.spans
  });
}

function fragment(state, rec, off, len) {
  const ev = rec.event;
  const kind = ev.outputKind === 'assistant' ? 'assistant' : 'output';
  const id = typeof ev.outputId === 'string' ? ev.outputId : null;
  const text = typeof ev.message === 'string' ? ev.message : '';
  const turn = rec.turn ?? null;
  const p = state.pending;
  if (p && (p.kind !== kind || p.id !== id || p.turn !== turn)) flushPending(state);
  if (kind === 'output' && id === null) {
    flushPending(state);
    if (text) emit(state, {at: rec.at, offsetMs: offsetOf(state, rec.at), kind: 'command-output', title: 'Command Output', summary: firstLine(text), hasBody: true, turn, seq: rec.seq ?? null, spans: [off, len]});
    return;
  }
  state.pending ??= {kind, id, turn, at: rec.at, seq: rec.seq ?? null, spans: [], chars: 0, head: ''};
  const q = state.pending;
  q.spans.push(off, len);
  q.chars += rec.__chars ?? text.length;
  if (q.head.length < 400) q.head += text;
  if (ev.outputComplete || q.chars > JOIN_CHARS || q.spans.length > MAX_FRAGMENTS * 2) flushPending(state);
}

function apply(state, rec, off, len) {
  state.count++;
  if (typeof rec.at === 'string') state.lastAt = rec.at;
  if (Number.isInteger(rec.seq)) state.lastSeq = rec.seq;
  if (state.head === null) state.head = {type: rec.type, schema: rec.schema ?? null, version: rec.version ?? null};
  switch (rec.type) {
    case 'run-start':
      if (!state.runStart && state.head.type === 'run-start') {
        state.runStart = rec;
        const ms = Date.parse(rec.at);
        state.startMs = Number.isFinite(ms) ? ms : null;
      }
      break;
    case 'turn-start': state.turn = rec.turn ?? null; state.attempt = rec.attempt ?? null; break;
    case 'wrap-up': state.wrapUp = rec.phase ?? 'start'; break;
    case 'time-limit': state.timeLimit = true; break;
    case 'workspace-created': if (typeof rec.path === 'string') state.workspacePath = rec.path; break;
    case 'workspace-kept': state.kept = {files: rec.files, bytes: rec.bytes, complete: rec.complete, skippedCount: rec.skippedCount}; break;
    case 'workspace-keep-failed': state.keepFailed = true; break;
    case 'published': state.published = rec.what ?? null; break;
    case 'validation': state.validations++; state.lastValidation = {attempt: rec.attempt, valid: rec.valid === true}; break;
    case 'run-end': state.runEnd = rec; break;
    default: break;
  }
  if (!state.wantEntries) return;
  if (state.startMs === null && Number.isFinite(Date.parse(rec.at))) state.startMs = Date.parse(rec.at);
  if (rec.type === 'ala-event' && rec.event && typeof rec.event === 'object' && rec.event.type === 'coding-agent-message') {
    fragment(state, rec, off, len);
    return;
  }
  flushPending(state);
  const d = describe(rec);
  emit(state, {at: rec.at, offsetMs: offsetOf(state, rec.at), ...d, turn: rec.turn ?? null, seq: rec.seq ?? null, spans: [off, len]});
}

const CHEAP = /^\{"seq":(\d+),"at":"([^"]*)","type":"(ala-event|ala-stderr)"/;

// A line this long is not parsed whole for the timeline: engine lines carry their long text in one field, so the
// fields that decide the entry lie in the first and last bytes. The body is read and parsed in full only when opened.
const LARGE = 64 * 1024;
const unquote = chunk => {
  for (let cut = 0; cut < 8; cut++) {
    try {
      return JSON.parse(`"${cut ? chunk.slice(0, -cut) : chunk}"`);
    } catch {
      // The cut fell inside an escape sequence: try a shorter piece.
    }
  }
  return null;
};
function largeRecord(line) {
  const head = line.subarray(0, 4096).toString('utf8');
  const m = /^\{"seq":(\d+),"at":"([^"]*)","type":"(ala-stderr|ala-event)"/.exec(head);
  if (!m) return null;
  const turn = /,"turn":"([^"]*)"/.exec(head)?.[1] ?? null;
  const rec = {seq: Number(m[1]), at: m[2], type: m[3], ...(turn !== null ? {turn} : {})};
  const lead = (marker, text) => {
    const i = text.indexOf(marker);
    return i < 0 ? null : unquote(text.slice(i + marker.length, i + marker.length + 600).split('"')[0].replace(/\\$/, ''));
  };
  if (m[3] === 'ala-stderr') {
    const text = lead('"text":"', head);
    return text === null ? null : {...rec, text, __chars: line.length};
  }
  const ends = head + line.subarray(-768).toString('utf8');
  const kind = /"event":\{"type":"([a-z-]+)"/.exec(head)?.[1];
  if (kind !== 'coding-agent-message') return null;
  const outputKind = /"outputKind":"([a-z]+)"/.exec(ends)?.[1];
  if (!outputKind) return null;
  const outputId = /"outputId":"([^"]*)"/.exec(ends)?.[1];
  const message = lead('"message":"', head);
  if (message === null) return null;
  return {...rec, event: {type: kind, outputKind, ...(outputId !== undefined ? {outputId} : {}), outputComplete: /"outputComplete":true/.test(ends), message}, __chars: line.length};
}

function consume(state, line, off) {
  if (state.wantEntries && line.length > LARGE) {
    const rec = largeRecord(line);
    if (rec) return apply(state, rec, off, line.length);
  }
  if (!state.wantEntries) {
    const m = CHEAP.exec(line.subarray(0, 200).toString('latin1'));
    if (m) {
      state.head ??= {type: m[3], schema: null, version: null};
      state.count++;
      state.lastAt = m[2];
      state.lastSeq = Number(m[1]);
      return;
    }
  }
  let rec;
  try {
    rec = JSON.parse(line.toString('utf8'));
  } catch {
    state.bad++;
    return;
  }
  if (!rec || typeof rec !== 'object' || Array.isArray(rec) || typeof rec.type !== 'string') {
    state.bad++;
    return;
  }
  apply(state, rec, off, line.length);
}

function resetState(state) {
  Object.assign(state, newState(state.rel, state.wantEntries));
}

/** Reads the bytes that arrived since the last call and consumes the complete lines among them. */
function advance(state, root, gate) {
  for (let rounds = 0; rounds < 4096; rounds++) {
    let window = CHUNK;
    let r;
    let last;
    for (;;) {
      r = gate.read(root, state.rel, {offset: state.offset, length: window, maxBytes: window, buffer: SCAN});
      if (!r.ok) return r;
      if (state.ino !== null && (r.ino !== state.ino || r.size < state.offset)) {
        resetState(state);
        break;
      }
      state.ino = r.ino;
      state.size = r.size;
      state.mtimeMs = r.mtimeMs;
      last = r.bytes.lastIndexOf(10);
      if (last >= 0 || r.bytes.length < window || window >= MAX_LINE + 1) break;
      window = Math.min(window * 4, MAX_LINE + 1);
    }
    if (state.ino === null) continue; // the file was replaced: start again from its beginning
    const buffer = r.bytes;
    if (buffer.length === 0) return {ok: true};
    if (state.skipping) {
      const nl = buffer.indexOf(10);
      state.offset += nl < 0 ? buffer.length : nl + 1;
      if (nl >= 0) state.skipping = false;
      continue;
    }
    if (last < 0) {
      if (buffer.length < window) return {ok: true}; // a line still being written: left for the next poll
      state.oversize++; // a line longer than the viewer reads: skipped up to its end
      state.skipping = true;
      state.offset += buffer.length;
      continue;
    }
    let pos = 0;
    while (pos <= last) {
      const nl = buffer.indexOf(10, pos);
      const line = buffer.subarray(pos, nl);
      if (line.length > 0 && line.length <= MAX_LINE) consume(state, line, state.offset + pos);
      else if (line.length > MAX_LINE) state.oversize++;
      pos = nl + 1;
    }
    state.offset += last + 1;
  }
  return {ok: true};
}

function touch(map, key, limit) {
  const value = map.get(key);
  if (value === undefined) return undefined;
  map.delete(key);
  map.set(key, value);
  while (map.size > limit) map.delete(map.keys().next().value);
  return value;
}
function remember(map, key, value, limit) {
  map.delete(key);
  map.set(key, value);
  while (map.size > limit) map.delete(map.keys().next().value);
}

const toPublic = e => ({n: e.n, at: e.at, offsetMs: e.offsetMs, kind: e.kind, title: e.title, summary: e.summary, hasBody: e.hasBody, turn: e.turn});

/** `root` is the data root of the read gate. `gate` is the read gate (a test may pass a wrapper). */
export function createTraceIndex({root, gate = defaultGate}) {
  let lastBody = null;
  const summaries = new Map();
  const timelines = new Map();
  const relOf = (pid, rid) => `projects/${pid}/traces/${rid}/trace.jsonl`;

  function state(pid, rid, wantEntries) {
    if (!root || !isId(pid) || !isId(rid)) return null;
    const key = `${pid}/${rid}`;
    const cache = wantEntries ? timelines : summaries;
    let s = wantEntries ? touch(cache, key, TIMELINE_CACHE) : touch(cache, key, SUMMARY_CACHE);
    if (!s) {
      // A timeline state also answers for the summary, so a second scan is not needed.
      s = newState(relOf(pid, rid), wantEntries);
      remember(cache, key, s, wantEntries ? TIMELINE_CACHE : SUMMARY_CACHE);
    }
    const result = advance(s, root, gate);
    if (!result.ok && (result.reason === 'missing' || result.reason === 'link' || result.reason === 'refused')) {
      cache.delete(key);
      return null;
    }
    return s;
  }

  return {
    /** Facts of one trace, or null when it does not exist. The returned object must not be changed. */
    summary(pid, rid) {
      const full = timelines.get(`${pid}/${rid}`);
      if (full && isId(pid) && isId(rid)) {
        const s = state(pid, rid, true);
        if (s) return s;
      }
      return state(pid, rid, false);
    },
    /** The next entries after number `after`, at most TIMELINE_CAP. Null when there is no trace. */
    timeline(pid, rid, after = 0) {
      const s = state(pid, rid, true);
      if (!s) return null;
      const start = Math.max(0, Math.min(Number.isInteger(after) ? after : 0, s.entries.length));
      const slice = s.entries.slice(start, start + TIMELINE_CAP);
      return {
        entries: slice.map(toPublic),
        next: slice.length ? slice.at(-1).n : start,
        more: start + slice.length < s.entries.length,
        lastChanged: s.mtimeMs,
        total: s.entries.length
      };
    },
    /** The body of entry `n`: {format, text, note?} or {needsPrompt, turn}; null when the entry does not exist. */
    body(pid, rid, n) {
      const s = state(pid, rid, true);
      if (!s || !Number.isInteger(n) || n < 1 || n > s.entries.length) return null;
      const entry = s.entries[n - 1];
      if (!entry.hasBody) return {format: 'text', text: ''};
      // The parts of one long body are asked for one after the other: the last body is kept for them.
      if (lastBody && lastBody.state === s && lastBody.n === n) return lastBody.body;
      const records = [];
      for (let i = 0; i < entry.spans.length; i += 2) {
        const r = gate.read(root, s.rel, {offset: entry.spans[i], length: entry.spans[i + 1], maxBytes: MAX_LINE, buffer: SCAN});
        if (!r.ok) return {format: 'text', text: 'This step could not be read again. The trace changed or is no longer there.'};
        try {
          const rec = JSON.parse(r.bytes.toString('utf8'));
          if (entry.seq !== null && records.length === 0 && rec.seq !== entry.seq) throw new Error('moved');
          records.push(rec);
        } catch {
          return {format: 'text', text: 'This step could not be read again. The trace changed or is no longer there.'};
        }
      }
      if (!records.length) return {format: 'text', text: ''};
      const body = bodyFrom(entry, records);
      const answer = body.needsPrompt !== undefined ? {...body, turn: entry.turn, kind: entry.kind} : body;
      lastBody = answer.text && answer.text.length > PART_BYTES ? {state: s, n, body: answer} : null;
      return answer;
    }
  };
}
