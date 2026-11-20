/** Readable documents of the Deep Proof process viewer: the run page, the timeline, prompts, responses, validation
 * errors, progress notes, the session transcript, registers, findings and the report. No raw JSON is ever sent as a
 * document: registers become tables and cards, and a data file is listed by name and size as "Data file, not displayed".
 * Every file is reached through the read gate, and a file path is never taken from a request except for a file of the
 * kept copy of a work folder, which the gate checks like every other path. */
import * as defaultGate from './pv-read.mjs';
import {ViewerError, isId, partOf, table, cell, firstLine, scrub, formatBytes, formatDuration, TIMELINE_CAP} from './pv-trace.mjs';

const TEXT_LIMIT = 10 * 1024 * 1024;
const TRANSCRIPT_LIMIT = 8 * 1024 * 1024;
const KEPT_FILES = 2000;
const KEPT_DEPTH = 8;
export const REGISTERS = ['mandate', 'claims', 'criteria', 'evidence', 'checks', 'omissions', 'findings', 'review-coverage', 'external-sources', 'coverage'];
const REGISTER_TITLES = {
  mandate: 'Mandate', claims: 'Claims', criteria: 'Criteria', evidence: 'Evidence', checks: 'Checks', omissions: 'Omissions', findings: 'Findings',
  'review-coverage': 'Review Coverage', 'external-sources': 'External Sources', coverage: 'Coverage'
};
const JSON_FILES = new Set(['mandate', 'coverage']);
const VERDICTS = {confirmed: 'Confirmed', narrowed: 'Narrowed', clarification: 'Clarification', rejected: 'Rejected', out_of_scope: 'Out Of Scope'};
const NOT_RECORDED = 'Not recorded';
const DRAFT_NOTICE = 'Draft: not validated. This text comes from the work folder of a run that is not published.';
const DATA_FILE = 'Data file, not displayed.';

const label = value => (typeof value === 'string' && value ? value.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase()) : NOT_RECORDED);
const text = value => (value === null || value === undefined || value === '' ? NOT_RECORDED : typeof value === 'string' ? value : typeof value === 'object' ? JSON.stringify(value) : String(value));
const isDataFile = name => /\.(?:jsonl?|ndjson)$/i.test(name);
const clip = (value, limit = 1000) => {
  const s = text(value);
  return s.length > limit ? s.slice(0, limit - 1) + '…' : s;
};

export function createDocuments({roots, traces, runs, dossiers = null, gate = defaultGate}) {
  const data = roots?.data ?? null;

  // ---------- Where the files of a run can be found ----------

  const keptBase = (pid, rid) => `projects/${pid}/traces/${rid}/workspace`;

  function locations(run) {
    const out = [];
    if (run.runFolderExists) out.push({origin: 'run', root: data, base: `projects/${run.pid}/analyses/${run.rid}`});
    if (data && gate.stat(data, keptBase(run.pid, run.rid)).ok) out.push({origin: 'kept', root: data, base: keptBase(run.pid, run.rid)});
    const path = run.trace?.workspacePath;
    if (path) {
      const live = gate.openWorkFolder(roots, path);
      if (live) out.push({origin: 'live', root: live, base: ''});
    }
    return out;
  }
  const join = (base, rel) => (base && rel ? `${base}/${rel}` : base || rel);

  /** Reads the first copy of a file found in the given places. `origins` limits and orders the places. */
  function find(run, rel, {origins = ['run', 'kept', 'live'], maxBytes = TEXT_LIMIT} = {}) {
    const places = locations(run);
    let reason = 'missing';
    for (const origin of origins) {
      const place = places.find(p => p.origin === origin);
      if (!place) continue;
      const r = gate.read(place.root, join(place.base, rel), {maxBytes});
      if (r.ok) return {ok: true, bytes: r.bytes, origin, mtimeMs: r.mtimeMs};
      if (r.reason !== 'missing') reason = r.reason;
    }
    return {ok: false, reason};
  }
  function findList(run, rel, origins = ['run', 'kept', 'live']) {
    const places = locations(run);
    for (const origin of origins) {
      const place = places.find(p => p.origin === origin);
      if (!place) continue;
      const r = gate.tryList(place.root, join(place.base, rel));
      if (r.ok) return {ok: true, entries: r.entries, origin, place};
    }
    return {ok: false, entries: []};
  }

  const requireRun = (pid, rid) => {
    const run = runs.get(pid, rid);
    if (!run) throw new ViewerError(404, 'This run does not exist.');
    return run;
  };
  const textDoc = (format, body, extra = {}) => ({format, text: body, ...extra});

  // ---------- Titles and locators ----------

  function titlesOf(run) {
    const map = new Map();
    for (const d of runs.documents(run)) map.set(d.id, d.title);
    return map;
  }
  const locatorText = (loc, titles) => {
    if (!loc || typeof loc !== 'object') return NOT_RECORDED;
    const name = titles.get(loc.documentId) || text(loc.documentId);
    const lines = loc.startLine !== undefined ? (loc.endLine !== undefined && loc.endLine !== loc.startLine ? `lines ${loc.startLine} to ${loc.endLine}` : `line ${loc.startLine}`) : '';
    return [name, lines, loc.section ? `section ${loc.section}` : ''].filter(Boolean).join(', ');
  };
  const quoteOf = loc => (loc && typeof loc === 'object' && typeof loc.quote === 'string' && loc.quote ? clip(loc.quote) : NOT_RECORDED);

  // ---------- Registers ----------

  function loadRegister(run, name) {
    if (!REGISTERS.includes(name)) throw new ViewerError(404, 'There is no such register.');
    const rel = `quality/${name}.${JSON_FILES.has(name) ? 'json' : 'jsonl'}`;
    const found = find(run, rel);
    if (!found.ok) return null;
    const raw = found.bytes.toString('utf8');
    if (JSON_FILES.has(name)) {
      try {
        const value = JSON.parse(raw);
        return {origin: found.origin, value: value && typeof value === 'object' ? value : {}, rows: [], bad: value && typeof value === 'object' ? 0 : 1};
      } catch {
        return {origin: found.origin, value: {}, rows: [], bad: 1};
      }
    }
    const rows = [];
    let bad = 0;
    for (const line of raw.split('\n')) {
      if (!line.trim()) continue;
      try {
        const row = JSON.parse(line);
        if (row && typeof row === 'object' && !Array.isArray(row)) rows.push(row);
        else bad++;
      } catch {
        bad++;
      }
    }
    return {origin: found.origin, rows, value: null, bad};
  }

  function findingCard(f, ctx) {
    const {titles, criteria, evidence, checks} = ctx;
    const out = [`## ${cell(f.id)}: ${cell(f.title)}`];
    out.push(`**Verdict:** ${VERDICTS[f.verdict] || label(f.verdict)}`);
    out.push(`**Severity:** ${label(f.severity)}. ${text(f.severityReason)}`);
    out.push(`**Urgency:** ${label(f.urgency)}. ${text(f.urgencyReason)}`);
    out.push(`**Confidence:** ${label(f.confidence)}. ${text(f.confidenceReason)}`);
    out.push(`**Level:** ${label(f.level)}. **Categories:** ${Array.isArray(f.categories) && f.categories.length ? f.categories.join(', ') : NOT_RECORDED}`);
    const obj = f.object && typeof f.object === 'object' ? f.object : {};
    out.push(`### Object\n\n${text(obj.text)}\n\n${obj.locator ? `Location: ${locatorText(obj.locator, titles)}\n\n> ${quoteOf(obj.locator)}` : 'Location: Not recorded'}`);
    const crit = (Array.isArray(f.criterionIds) ? f.criterionIds : []).map(id => {
      const c = criteria.get(id);
      return `- ${cell(id)}: ${c ? cell(c.text) : 'Not recorded'}`;
    });
    out.push(`### Criteria\n\n${crit.length ? crit.join('\n') : 'None recorded.'}`);
    const ev = (Array.isArray(f.evidenceIds) ? f.evidenceIds : []).map(id => {
      const e = evidence.get(id);
      return e ? `- ${cell(id)} (${label(e.relation)}): ${locatorText(e.locator, titles)}\n\n  > ${quoteOf(e.locator)}` : `- ${cell(id)}: Not recorded`;
    });
    out.push(`### Evidence\n\n${ev.length ? ev.join('\n') : 'None recorded.'}`);
    const chk = (Array.isArray(f.checkIds) ? f.checkIds : []).map(id => {
      const c = checks.get(id);
      return c ? `- ${cell(id)} (${label(c.kind)}, ${label(c.status)}): ${text(c.method)} Result: ${text(c.result)}` : `- ${cell(id)}: Not recorded`;
    });
    out.push(`### Checks\n\n${chk.length ? chk.join('\n') : 'None recorded.'}`);
    const ch = f.challenge && typeof f.challenge === 'object' ? f.challenge : {};
    out.push(`### Challenge\n\n**Alternative:** ${text(ch.alternative)}\n\n**Result:** ${text(ch.result)}\n\n**Checks:** ${Array.isArray(ch.checkIds) && ch.checkIds.length ? ch.checkIds.join(', ') : NOT_RECORDED}`);
    out.push(`### Impact\n\n${text(f.impact)}`);
    out.push(`### Remedy\n\n${text(f.remedy)}`);
    out.push(`### Closure\n\n${text(f.closure)}`);
    out.push(`### Limitations\n\n${Array.isArray(f.limitations) && f.limitations.length ? f.limitations.map(l => `- ${cell(l)}`).join('\n') : 'None recorded.'}`);
    const orr = f.originalReview && typeof f.originalReview === 'object' ? f.originalReview : null;
    out.push(`### Original review\n\n${orr ? `${label(orr.status)}. ${text(orr.limitation)}` : NOT_RECORDED}`);
    return out.join('\n\n');
  }

  function findingContext(run) {
    const byId = name => new Map((loadRegister(run, name)?.rows ?? []).filter(r => typeof r.id === 'string').map(r => [r.id, r]));
    return {titles: titlesOf(run), criteria: byId('criteria'), evidence: byId('evidence'), checks: byId('checks')};
  }

  function registerDocument(run, name) {
    const reg = loadRegister(run, name);
    if (!reg) throw new ViewerError(404, 'This register is not available for this run.');
    const titles = titlesOf(run);
    const rows = reg.rows;
    let body;
    switch (name) {
      case 'mandate': {
        const m = reg.value;
        const list = (title, items, render) => `### ${title}\n\n${Array.isArray(items) && items.length ? items.map(render).join('\n') : 'None recorded.'}`;
        body = [table(['Field', 'Value'], [['Scope', m.scope], ['Purpose', m.purpose], ['Audience', m.audience], ['Report date', m.reportDate], ['Covered period', m.coveredPeriod], ['Decision date', m.decisionDate], ['Evaluation date', m.evaluationDate], ['Reference date', m.referenceDate], ['Temporal basis', m.temporalBasis], ['Research mode', m.researchMode], ['Depth', m.depth], ['Materiality', m.materiality]]),
          list('Profiles', m.profiles, p => `- ${cell(p?.id)}: ${cell(p?.reason)}`), list('Assumptions', m.assumptions, a => `- ${cell(a)}`), list('Limits', m.limits, l => `- ${cell(l)}`)].join('\n\n');
        break;
      }
      case 'claims':
        body = table(['ID', 'Claim', 'Type', 'Status', 'Document and lines', 'Quote'], rows.map(r => [r.id, r.text, label(r.type), label(r.status), locatorText(r.locator, titles), quoteOf(r.locator)]));
        break;
      case 'criteria':
        body = table(['ID', 'Criterion', 'Obligation', 'Applicability', 'Coverage', 'Reason', 'Basis quote'], rows.map(r => [r.id, r.text, label(r.obligation), label(r.applicability), label(r.coverage), r.reason, r.basis?.locator ? quoteOf(r.basis.locator) : r.basis?.quote ?? NOT_RECORDED]));
        break;
      case 'evidence':
        body = table(['ID', 'Claim', 'Relation', 'Document', 'Lines', 'Quote'], rows.map(r => [r.id, r.claimId, label(r.relation), titles.get(r.locator?.documentId) || text(r.locator?.documentId), r.locator ? `${text(r.locator.startLine)} to ${text(r.locator.endLine)}` : NOT_RECORDED, quoteOf(r.locator)]));
        break;
      case 'checks':
        body = table(['ID', 'Kind', 'Status', 'Method', 'Result', 'Limitation', 'Artifacts'], rows.map(r => [r.id, label(r.kind), label(r.status), r.method, r.result, r.limitation, Array.isArray(r.artifactPaths) && r.artifactPaths.length ? r.artifactPaths.join(', ') : NOT_RECORDED]));
        break;
      case 'omissions':
        body = table(['Expected content', 'Verdict', 'Coverage', 'Materiality', 'Linked finding'], rows.map(r => [r.expected, label(r.verdict), r.coverage, r.materiality, r.findingId]));
        break;
      case 'findings': {
        const ctx = findingContext(run);
        body = rows.map(f => findingCard(f, ctx)).join('\n\n---\n\n') || 'No findings were recorded.';
        break;
      }
      case 'review-coverage':
        body = table(['Status', 'Original claim', 'Reviewer claim', 'Finding', 'Reason'], rows.map(r => [label(r.status), r.originalClaimId, r.reviewerClaimId, r.findingId, r.reason]));
        break;
      case 'external-sources':
        // The address is shown as text and never as a link; the retained content opens in place.
        body = table(['Address', 'Accessed', 'Published', 'Availability', 'Role', 'Content'], rows.map(r => [String(r.url ?? NOT_RECORDED).replace(/^(https?):\/\//i, '$1 : // '), r.accessedAt, r.publishedAt, label(r.availability), label(r.role), clip(r.content, 2000)]));
        break;
      case 'coverage': {
        const c = reg.value;
        const counts = c.counts && typeof c.counts === 'object' ? c.counts : {};
        const segments = Array.isArray(c.segments) ? c.segments : [];
        const tally = key => {
          const n = new Map();
          for (const s of segments) n.set(label(s?.[key]), (n.get(label(s?.[key])) ?? 0) + 1);
          return [...n].map(([k, v]) => `${k}: ${v}`).join(', ') || NOT_RECORDED;
        };
        body = [`**Completion:** ${label(c.completion)}`,
          table(['Count', 'Value'], [['Segments read', counts.segmentsRead], ['Claims checked', counts.claimsChecked], ['Confirmed findings', counts.confirmedFindings]]),
          '### Gates\n\n' + (Array.isArray(c.gates) && c.gates.length ? table(['Gate', 'Status', 'Reason'], c.gates.map(g => [g?.id, label(g?.status), g?.reason])) : 'None recorded.'),
          '### Sources\n\n' + (Array.isArray(c.sources) && c.sources.length ? table(['Source', 'Original review', 'Reason'], c.sources.map(s => [titles.get(s?.id) || s?.id, label(s?.originalReview), s?.reason])) : 'None recorded.'),
          `### Segments\n\n${segments.length} segments. By reading: ${tally('reading')}. By claims: ${tally('claims')}. By requirements: ${tally('requirements')}.`,
          '### Limitations\n\n' + (Array.isArray(c.limitations) && c.limitations.length ? c.limitations.map(l => `- ${cell(l)}`).join('\n') : 'None recorded.')].join('\n\n');
        break;
      }
      default: body = '';
    }
    if (reg.bad) body += `\n\n*${reg.bad} row${reg.bad === 1 ? '' : 's'} of this register could not be read and ${reg.bad === 1 ? 'is' : 'are'} not shown.*`;
    if (!rows.length && !reg.value) body = (body || '') + (body ? '' : 'This register has no rows.');
    return textDoc('markdown', body || 'This register has no rows.', {title: REGISTER_TITLES[name], ...(reg.origin !== 'run' ? {notice: DRAFT_NOTICE} : {})});
  }

  // ---------- Other documents ----------

  function progressDocument(run) {
    const found = find(run, 'progress.md', {maxBytes: 1024 * 1024});
    if (!found.ok) throw new ViewerError(404, 'There are no progress notes for this run.');
    const blocks = found.bytes.toString('utf8').split(/\n---\s*\n/).map(b => b.replace(/<!--[\s\S]*?-->/g, '').trim()).filter(Boolean);
    return textDoc('markdown', blocks.map((b, i) => `## Note ${i + 1}\n\n${b}`).join('\n\n') || 'The progress file is empty.', {title: 'Progress Notes'});
  }

  function validationDocument(run, turn) {
    const found = find(run, `validation-errors-${turn}.json`, {maxBytes: 1024 * 1024});
    if (!found.ok) throw new ViewerError(404, 'There are no validation errors for this attempt.');
    let feedback;
    try {
      feedback = JSON.parse(found.bytes.toString('utf8'));
    } catch {
      throw new ViewerError(404, 'The validation errors of this attempt could not be read.');
    }
    const errors = Array.isArray(feedback.errors) ? feedback.errors : [];
    return textDoc('markdown', `${errors.map((e, i) => `${i + 1}. ${cell(typeof e === 'string' ? e : JSON.stringify(e))}`).join('\n') || 'No errors were recorded.'}\n\n${text(feedback.instruction)}`, {title: `Validation Errors, Attempt ${turn}`});
  }

  function transcriptRecords(run) {
    const listed = findList(run, '.ala/sessions');
    const name = listed.entries.filter(e => e.kind === 'file' && /^[\w.-]+\.jsonl$/.test(e.name)).map(e => e.name).sort()[0];
    if (!name) return null;
    const r = gate.read(listed.place.root, join(listed.place.base, `.ala/sessions/${name}`), {maxBytes: TRANSCRIPT_LIMIT});
    if (!r.ok) return null;
    const lines = r.bytes.toString('utf8').split('\n');
    lines.pop(); // the last element is empty or a line still being written
    const records = [];
    for (const line of lines) {
      try {
        const rec = JSON.parse(line);
        if (rec && typeof rec === 'object' && typeof rec.type === 'string') records.push(rec);
      } catch {
        // A line that does not parse is skipped.
      }
    }
    return {records, mtimeMs: r.mtimeMs};
  }

  function transcriptDocument(run) {
    const t = transcriptRecords(run);
    if (!t) throw new ViewerError(404, 'There is no session transcript for this run.');
    const rows = t.records.map(rec => {
      const body = String(rec.text ?? rec.error ?? '');
      const shown = body.replace(/\s+/g, ' ').trim();
      const more = shown.length > 600 ? ` … (open record ${rec.seq} to read all)` : '';
      return [String(rec.at ?? '').slice(11, 19) || '--:--:--', rec.type === 'agent-message' && rec.outputKind !== 'assistant' ? 'Command output' : label(rec.type), shown.slice(0, 600) + more];
    });
    return textDoc('markdown', table(['Time', 'Kind', 'Text'], rows) || 'The transcript is empty.', {title: 'Session Transcript'});
  }

  function transcriptRecord(run, seq) {
    const t = transcriptRecords(run);
    const rec = t?.records.find(r => String(r.seq) === String(seq));
    if (!rec) throw new ViewerError(404, 'This transcript record does not exist.');
    return textDoc('markdown', String(rec.text ?? rec.error ?? ''), {title: `Transcript Record ${seq}`});
  }

  function fileDocument(run, rel, origins, title, {draft = false} = {}) {
    const found = find(run, rel, {origins});
    if (!found.ok) throw new ViewerError(404, found.reason === 'too-large' ? 'This file is too large to show.' : 'This file is not available for this run.');
    const isDraft = draft && (found.origin !== 'run' || ['running', 'interrupted', 'failed'].includes(run.status));
    return textDoc(rel.endsWith('.md') ? 'markdown' : 'text', found.bytes.toString('utf8'), {title, ...(isDraft ? {notice: DRAFT_NOTICE} : {})});
  }

  function keptDocument(run, rel) {
    const place = locations(run).find(p => p.origin === 'kept');
    if (!place) throw new ViewerError(404, 'There is no kept copy for this run.');
    if (isDataFile(rel)) {
      const st = gate.stat(place.root, join(place.base, rel));
      if (!st.ok) throw new ViewerError(404, 'This file is not in the kept copy.');
      return textDoc('text', `${DATA_FILE} It is ${formatBytes(st.size)}.`, {title: 'Data file'});
    }
    const r = gate.read(place.root, join(place.base, rel), {maxBytes: TEXT_LIMIT});
    if (!r.ok) throw new ViewerError(404, r.reason === 'too-large' ? 'This file is too large to show.' : 'This file is not in the kept copy.');
    if (r.bytes.includes(0)) return textDoc('text', `Binary file, not displayed. It is ${formatBytes(r.size)}.`, {title: 'Binary file'});
    return textDoc(/\.md$/i.test(rel) ? 'markdown' : 'text', r.bytes.toString('utf8'));
  }

  function keptFiles(run) {
    const place = locations(run).find(p => p.origin === 'kept');
    if (!place) return [];
    const out = [];
    const walk = (rel, depth) => {
      if (depth > KEPT_DEPTH || out.length >= KEPT_FILES) return;
      for (const e of gate.list(place.root, join(place.base, rel))) {
        const name = rel ? `${rel}/${e.name}` : e.name;
        if (e.kind === 'dir') walk(name, depth + 1);
        else if (out.length < KEPT_FILES) out.push({name, size: e.size});
      }
    };
    walk('', 0);
    return out;
  }

  // ---------- Documents by logical name ----------

  const SIMPLE = {criteria: ['review-criteria.md', 'Review Criteria'], playbook: ['review-playbook.md', 'Review Playbook'], template: ['report-template.md', 'Report Template']};

  function document(pid, rid, name, part = 0) {
    const run = requireRun(pid, rid);
    if (typeof name !== 'string' || name.length > 300) throw new ViewerError(400, 'Name the document to open.');
    const [kind, ...rest] = name.split(':');
    const arg = rest.join(':');
    let doc;
    if (SIMPLE[name]) doc = fileDocument(run, SIMPLE[name][0], ['run'], SIMPLE[name][1]);
    else if (name === 'report') doc = fileDocument(run, 'report.md', ['run', 'kept', 'live'], 'Report', {draft: true});
    else if (name === 'progress') doc = progressDocument(run);
    else if (name === 'transcript') doc = transcriptDocument(run);
    else if (kind === 'transcript' && /^\d{1,9}$/.test(arg)) doc = transcriptRecord(run, arg);
    else if (kind === 'prompt' && isId(arg)) doc = fileDocument(run, `task-${arg}.txt`, ['run', 'kept', 'live'], `Prompt, Turn ${arg}`);
    else if (kind === 'response' && isId(arg)) doc = fileDocument(run, `response-${arg}.txt`, ['run', 'kept', 'live'], `Response, Turn ${arg}`);
    else if (kind === 'validation' && isId(arg)) doc = validationDocument(run, arg);
    else if (kind === 'source' && isId(arg)) doc = fileDocument(run, `sources/${arg}.md`, ['run'], titlesOf(run).get(arg) || 'Source');
    else if (kind === 'register') doc = registerDocument(run, arg);
    else if (kind === 'finding' && isId(arg)) {
      const reg = loadRegister(run, 'findings');
      const f = reg?.rows.find(r => r.id === arg);
      if (!f) throw new ViewerError(404, 'This finding does not exist.');
      doc = textDoc('markdown', findingCard(f, findingContext(run)), {title: `Finding ${arg}`, ...(reg.origin !== 'run' ? {notice: DRAFT_NOTICE} : {})});
    } else if (kind === 'kept' && arg) doc = keptDocument(run, arg);
    else throw new ViewerError(404, 'There is no such document.');
    const piece = partOf(doc.text, part);
    if (!piece) throw new ViewerError(404, 'There is no such part of this document.');
    return {...doc, ...piece};
  }

  // ---------- Timeline ----------

  /** The timeline of a run without a trace: transcript records and validation-error files, under a banner. */
  function untracedEntries(run) {
    const entries = [];
    const t = transcriptRecords(run);
    const start = Date.parse(run.startedAt ?? '') || Date.parse(t?.records[0]?.at ?? '') || 0;
    const add = (at, kind, title, summary, body, turn = null) => entries.push({at, ms: Date.parse(at) || 0, kind, title, summary, body, turn});
    for (const rec of t?.records ?? []) {
      const body = String(rec.text ?? '');
      switch (rec.type) {
        case 'session': add(rec.at, 'run-started', 'Session Started', 'The agent session began', ''); break;
        case 'user': add(rec.at, 'prompt-sent', 'Prompt Sent', firstLine(body), body, rec.turnId ?? null); break;
        case 'agent-message':
          if (body) add(rec.at, rec.outputKind === 'assistant' ? 'agent-message' : 'command-output', rec.outputKind === 'assistant' ? 'Agent Message' : 'Command Output', firstLine(body), body, rec.turnId ?? null);
          break;
        case 'final': add(rec.at, 'final-message', 'Final Agent Message', firstLine(body), body, rec.turnId ?? null); break;
        case 'turn-end': add(rec.at, 'turn-ended', 'Turn Ended', `${text(rec.status)}, ${formatDuration(rec.durationMs)}`, table(['Field', 'Value'], [['Status', rec.status], ['Duration', formatDuration(rec.durationMs)], ['Error', scrub(rec.error)]]), rec.turnId ?? null); break;
        case 'continuation': add(rec.at, 'continuation', 'Empty Response: Continuing', 'The reply was empty', 'Deep Proof asked the agent to continue in the same session.', rec.turnId ?? null); break;
        default: break;
      }
    }
    for (const [name, entry] of run.files) {
      const m = /^validation-errors-([A-Za-z0-9_-]+)\.json$/.exec(name);
      if (!m) continue;
      let errors = [];
      const r = gate.read(data, `projects/${run.pid}/analyses/${run.rid}/${name}`, {maxBytes: 1024 * 1024});
      try {
        errors = JSON.parse(r.ok ? r.bytes.toString('utf8') : '{}').errors ?? [];
      } catch {
        errors = [];
      }
      const list = Array.isArray(errors) ? errors : [];
      add(new Date(entry.mtimeMs).toISOString(), 'validation-failed', `Validation Failed (${list.length} errors)`, firstLine(scrub(typeof list[0] === 'string' ? list[0] : '')), list.map((e, i) => `${i + 1}. ${cell(typeof e === 'string' ? e : JSON.stringify(e))}`).join('\n'), m[1]);
    }
    entries.sort((a, b) => a.ms - b.ms);
    return entries.map((e, i) => ({...e, n: i + 1, offsetMs: Math.max(0, e.ms - start)}));
  }

  const MISSING_UNTRACED = {
    analysis: ['Host step times and outcomes (exit code, signal)', 'Progress reminders', 'The time-limit moment and the wrap-up decision', 'The publish step', 'The final status and failure text', "The engine's diagnostics and the agent selection"],
    quality: ['The whole process: prompts, responses, transcript, validation errors and the timeline'],
    unknown: ['The whole process']
  };

  function timeline(pid, rid, after = 0) {
    const run = requireRun(pid, rid);
    if (run.traced) {
      const answer = traces.timeline(pid, rid, after);
      if (!answer) throw new ViewerError(404, 'The trace of this run is not available.');
      return {...answer, traced: true, banner: null};
    }
    const entries = untracedEntries(run);
    const start = Math.max(0, Math.min(Number.isInteger(after) ? after : 0, entries.length));
    const slice = entries.slice(start, start + TIMELINE_CAP);
    return {
      entries: slice.map(({n, at, offsetMs, kind, title, summary, body, turn}) => ({n, at, offsetMs, kind, title, summary, hasBody: Boolean(body), turn})),
      next: slice.length ? slice.at(-1).n : start, more: start + slice.length < entries.length, lastChanged: run.lastActivityAt, total: entries.length,
      traced: false, banner: {text: 'This run has no recorded process. The steps below come from the files it left.', missing: MISSING_UNTRACED[run.kind] ?? MISSING_UNTRACED.unknown}
    };
  }

  function entryBody(pid, rid, n, part = 0) {
    const run = requireRun(pid, rid);
    let body;
    if (run.traced) {
      body = traces.body(pid, rid, n);
      if (!body) throw new ViewerError(404, 'This step does not exist.');
      if (body.needsPrompt !== undefined) {
        const turn = body.needsPrompt;
        const found = isId(String(turn)) ? find(run, `task-${turn}.txt`) : {ok: false};
        body = found.ok ? {format: 'text', text: found.bytes.toString('utf8')} : {format: 'text', text: 'The prompt file is not available any more.'};
      }
    } else {
      const entry = untracedEntries(run).find(e => e.n === n);
      if (!entry) throw new ViewerError(404, 'This step does not exist.');
      body = {format: entry.kind === 'turn-ended' || entry.kind === 'validation-failed' ? 'markdown' : entry.kind === 'agent-message' ? 'markdown' : 'text', text: entry.body};
    }
    const piece = partOf(body.text, part);
    if (!piece) throw new ViewerError(404, 'There is no such part of this step.');
    return {format: body.format, ...piece, ...(body.note ? {note: body.note} : {})};
  }

  // ---------- The run page ----------

  function missingList(run) {
    if (run.traced || run.kind === 'imported') return [];
    if (run.kind === 'analysis') return MISSING_UNTRACED.analysis;
    if (run.kind === 'quality') {
      if (run.status === 'completed') return ['The whole process: prompts, responses, transcript, validation errors and the timeline'];
      if (run.status === 'time-limit' || run.status === 'failed') return ['Everything except the inputs, the notes and the notes report: the draft report, the registers and the process'];
      return ['The work folder cannot be found while the check runs; only the progress notes are shown'];
    }
    return MISSING_UNTRACED.unknown;
  }

  function runPage(pid, rid) {
    const run = requireRun(pid, rid);
    const rs = run.runStart ?? {};
    const docs = runs.documents(run);
    const report = runs.reportUnderReview(run);
    const places = locations(run);
    const notices = [];
    const header = {
      key: run.key, title: run.title, projectName: run.projectName, kind: run.kind, kindLabel: run.kindLabel, status: run.status, statusLabel: run.statusLabel,
      statusDetail: run.statusDetail, statusSource: run.statusSource, traced: run.traced, trace: run.traced ? 'Recorded' : 'Not Recorded',
      agent: run.agent, model: run.model, effort: rs.effort ?? null, attempt: run.attempt, maxAttempts: run.maxAttempts,
      timeLimitMinutes: rs.timeLimitMinutes ?? null, startedAt: run.startedAt, endedAt: run.endedAt, lastActivityAt: run.lastActivityAt,
      skill: rs.skill ? {name: rs.skill.name ?? null, revision: rs.skill.revision ?? null} : null, reportLanguage: rs.reportLanguage ?? null
    };
    if (run.otherVersion !== null) notices.push({kind: 'trace-version', text: `This run has a trace of another version (${text(run.otherVersion)}). The files decide what is shown.`});
    if (!run.traced && run.kind !== 'imported') notices.push({kind: 'not-recorded', text: 'This run has no recorded process. Its status is inferred from files.'});
    if (run.traced && run.trace.workspacePath && !places.some(p => p.origin === 'live' || p.origin === 'kept') && run.kind === 'quality') {
      notices.push({kind: 'work-folder-gone', text: 'The work folder of this check is gone. The recorded steps are still shown.'});
    }
    if (run.trace?.oversize) notices.push({kind: 'trace-lines', text: `${run.trace.oversize} lines of the trace were too long to read and are skipped.`});
    if (run.trace?.bad) notices.push({kind: 'trace-lines', text: `${run.trace.bad} lines of the trace could not be read and are skipped.`});
    const named = [run.title, ...docs.map(d => d.originalName)].some(s => typeof s === 'string' && /correct|mixed/i.test(s));
    if (named) notices.push({kind: 'name-told', text: 'The name told the agent which report this is.'});

    const inputs = [];
    for (const d of docs) {
      const match = dossiers?.matchHash(d.sha256, d.trimmedSha256);
      const isReport = report?.documentId === d.id;
      inputs.push({id: `source:${d.id}`, kind: isReport ? 'report-under-review' : 'document', title: d.title, bytes: d.bytes, doc: `source:${d.id}`,
        note: match ? `Matches dossier ${match.id}${match.role === 'report' ? `, report ${match.sha12}` : `, source ${match.file}`}${match.trimmed ? ' after trimming' : ''}` : null});
    }
    if (run.kind === 'imported' && report) {
      const match = dossiers?.matchHash(report.sha256, report.trimmedSha256);
      inputs.push({id: 'report-under-review', kind: 'report-under-review', title: run.title, doc: 'report', note: match ? `Matches dossier ${match.id}, report ${match.sha12}${match.trimmed ? ' after trimming' : ''}` : null});
    }
    if (rs.skill) inputs.push({id: 'skill', kind: 'skill', title: rs.skill.name ?? 'Analyst Skill', chips: [{label: 'Revision', value: text(rs.skill.revision)}, {label: 'Verification', value: text(typeof rs.skill.verification === 'object' ? rs.skill.verification?.status : rs.skill.verification)}], doc: null});
    for (const [key, [file, title]] of Object.entries(SIMPLE)) if (run.files.has(file)) inputs.push({id: key, kind: key, title, doc: key});
    const options = [];
    if (rs.qualityOptions) options.push({label: 'Depth', value: text(rs.qualityOptions.depth)}, {label: 'Research mode', value: text(rs.qualityOptions.researchMode)});
    if (rs.timeLimitMinutes) options.push({label: 'Time limit', value: `${rs.timeLimitMinutes} min`});
    if (rs.reportLanguage) options.push({label: 'Report language', value: rs.reportLanguage});
    if (options.length) inputs.push({id: 'options', kind: 'options', title: 'Options', chips: options, doc: null});

    const results = [];
    const reportAvailable = find(run, 'report.md').ok;
    if (reportAvailable) {
      const origin = find(run, 'report.md').origin;
      const draft = origin !== 'run' || ['running', 'interrupted', 'failed'].includes(run.status);
      results.push({id: 'report', kind: 'report', title: draft ? 'Draft Report' : 'Report', doc: 'report', draft});
    }
    if (find(run, 'progress.md', {maxBytes: 1024 * 1024}).ok) results.push({id: 'progress', kind: 'progress', title: 'Progress Notes', doc: 'progress'});
    if (transcriptRecords(run)) results.push({id: 'transcript', kind: 'transcript', title: 'Session Transcript', doc: 'transcript'});
    let findingsRegister = null;
    for (const name of REGISTERS) {
      const reg = loadRegister(run, name);
      if (!reg) continue;
      if (name === 'findings') findingsRegister = reg;
      results.push({id: `register:${name}`, kind: 'register', title: REGISTER_TITLES[name], count: JSON_FILES.has(name) ? null : reg.rows.length, doc: `register:${name}`, draft: reg.origin !== 'run'});
    }
    for (const f of findingsRegister?.rows ?? []) {
      if (typeof f.id !== 'string' || !isId(f.id)) continue;
      results.push({id: `finding:${f.id}`, kind: 'finding', title: text(f.title), doc: `finding:${f.id}`, verdict: f.verdict ?? null, verdictLabel: VERDICTS[f.verdict] || label(f.verdict),
        chips: [{label: 'Verdict', value: VERDICTS[f.verdict] || label(f.verdict)}, {label: 'Severity', value: label(f.severity)}, {label: 'Urgency', value: label(f.urgency)}, {label: 'Confidence', value: label(f.confidence)}]});
    }
    for (const f of keptFiles(run)) {
      results.push({id: `kept:${f.name}`, kind: 'kept-file', title: f.name, bytes: f.size, doc: isDataFile(f.name) ? null : `kept:${f.name}`, note: isDataFile(f.name) ? DATA_FILE : null});
    }
    return {header, inputs, results, notices, missing: missingList(run)};
  }

  return {runPage, timeline, entryBody, document};
}
