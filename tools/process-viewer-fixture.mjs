/** Builds a synthetic Deep Proof data folder, a live Check Report work folder and a benchmark dossier tree with answer
 * keys beside it, for the tests of the process viewer only. Nothing here comes from a real run: every name, text and
 * number is made up, and no model is involved. The viewer itself never imports this file. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

export const SENTINEL = 'SENTINEL-MUST-NEVER-BE-SERVED-7c1e5b';
export const sha256 = value => crypto.createHash('sha256').update(value).digest('hex');
export const WORK_PREFIX = 'deep-proof-verification-';
const START = Date.parse('2026-01-01T00:00:00.000Z');

function put(base, rel, content, mode) {
  const file = path.join(base, rel);
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.writeFileSync(file, content);
  if (mode !== undefined) fs.chmodSync(file, mode);
  return file;
}
const jsonl = rows => rows.map(r => JSON.stringify(r)).join('\n') + (rows.length ? '\n' : '');

/** Builds the lines of one trace file, following version 1 of the Deep Proof trace contract. */
export class TraceBuilder {
  constructor({start = START, stepMs = 1000} = {}) {
    this.time = start;
    this.stepMs = stepMs;
    this.records = [];
  }
  add(type, fields = {}, advance = this.stepMs) {
    this.time += advance;
    const record = {seq: this.records.length + 1, at: new Date(this.time).toISOString(), type, ...fields};
    this.records.push(record);
    return record;
  }
  text() {
    return jsonl(this.records);
  }
}

export function runStartFields({runId, projectId = 'proj-alpha', kind = 'analysis', createdAt, documents = [], overrides = {}, pid = process.pid}) {
  return {
    schema: 'deep-proof-trace', version: 1, runId, projectId, projectName: 'Alpha Project', kind, createdAt,
    title: kind === 'quality' ? 'Quarterly Report Under Review' : null,
    sourceReport: kind === 'quality' ? {id: 'src-run', kind: 'analysis', title: 'Quarterly Report Under Review', documentId: 'report-under-review'} : null,
    skill: {id: 'skill-demo', name: kind === 'quality' ? 'Report Check' : 'Evidence Review', revision: 3, methodologyRevision: 1, verification: {status: 'verified'}},
    agent: 'codex', model: 'model-demo', effort: 'high', reportLanguage: 'en', timeLimitMinutes: 30,
    workMs: 1800000, wrapUpMs: 300000, maxAttempts: 3,
    qualityOptions: kind === 'quality' ? {depth: 'deep', researchMode: 'closed'} : null,
    documents, runFolder: `projects/${projectId}/analyses/${runId}`,
    server: {pid, startedAt: createdAt}, limits: {recordBytes: 262144, traceBytes: 67108864},
    ...overrides
  };
}

const ROMANIAN = 'Raport „verificat”: ăâîșț ĂÂÎȘȚ, emoji 😀, and <script>window.__bad = 1</script> as plain text.';

/** The records of a finished analysis with one failed and one repaired validation. */
export function analysisTrace({runId, documents, createdAt, outcome = 'completed', pid = process.pid, end = true}) {
  const t = new TraceBuilder({start: Date.parse(createdAt)});
  t.add('run-start', runStartFields({runId, createdAt, documents, pid}), 0);
  t.add('turn-start', {turn: '1', attempt: 1, repair: false, continuation: false, resume: false, sessionId: 'session-1', taskFile: 'task-1.txt', promptBytes: 120, promptSha256: sha256('prompt-1'), remainingMs: 1800000});
  t.add('ala-event', {turn: '1', event: {type: 'coding-agent-selected', agent: 'codex', model: 'model-demo', permissionMode: 'workspace-write'}});
  t.add('ala-event', {turn: '1', event: {type: 'coding-agent-message', agent: 'codex', outputKind: 'assistant', outputId: 'm1', message: 'I read the sources. '}});
  t.add('ala-event', {turn: '1', event: {type: 'coding-agent-message', agent: 'codex', outputKind: 'assistant', outputId: 'm1', message: ROMANIAN, outputComplete: true}});
  t.add('ala-event', {turn: '1', event: {type: 'coding-agent-message', agent: 'codex', outputKind: 'output', outputId: 'c1', message: 'sources/doc-a.md\nsources/doc-b.md\n', outputComplete: true}});
  t.add('progress-note', {turn: '1', text: 'Checked the first document against the criteria.', blocks: 1});
  t.add('reminder-sent', {turn: '1', id: 'reminder-1'});
  t.add('ala-event', {turn: '1', event: {type: 'message-accepted', id: 'reminder-1'}});
  t.add('ala-event', {turn: '1', event: {type: 'coding-agent-final', agent: 'codex', message: 'The review is written.'}});
  t.add('turn-end', {turn: '1', outcome: 'exited', exitCode: 0, signal: null, durationMs: 9000});
  t.add('validation', {attempt: 1, valid: false, errorCount: 2, errors: ['Finding 1 has no evidence footnote.', 'The Summary chapter is missing.']});
  t.add('turn-start', {turn: '2', attempt: 2, repair: true, continuation: false, resume: true, sessionId: 'session-1', taskFile: 'task-2.txt', promptBytes: 240, promptSha256: sha256('prompt-2'), remainingMs: 1700000});
  t.add('ala-stderr', {turn: '2', text: 'ala: a diagnostic line from the engine'});
  t.add('ala-event', {turn: '2', event: {type: 'coding-agent-final', agent: 'codex', message: 'The review is repaired.'}});
  t.add('turn-end', {turn: '2', outcome: 'exited', exitCode: 0, signal: null, durationMs: 7000});
  t.add('validation', {attempt: 2, valid: true, errorCount: 0, errors: []});
  t.add('published', {what: 'report', timeLimited: false});
  if (end) t.add('run-end', {status: outcome, timeLimited: false, partial: false, failureCode: null, error: null, quality: null, droppedAlaLines: 0, durationMs: 40000});
  return t;
}

const FINDINGS = [
  {id: 'F-001', title: 'The revenue total does not match the table', categories: ['F01'], profiles: ['review'], level: 'target', object: {text: 'Revenue grew by 12 percent.', locator: {documentId: 'doc-a', startLine: 3, endLine: 3, quote: 'Revenue was 112 against 100.', sourceHash: sha256('doc-a'), section: 'Results'}}, criterionIds: ['C-001'], evidenceIds: ['E-001'], checkIds: ['K-001'], challenge: {alternative: 'The figures may be restated in a later table.', checkIds: ['K-001'], result: 'No later table restates the figures, so the candidate stands.'}, verdict: 'confirmed', impact: 'The headline growth figure is overstated.', severity: 'major', severityReason: 'It changes the headline.', urgency: 'before_decision', urgencyReason: 'It is read before the decision.', confidence: 'high', confidenceReason: 'Two sources agree.', remedy: 'State the growth as 12.0 against 100.', closure: 'Compare the corrected figure.', limitations: ['Original spreadsheet not inspected.'], originalReview: {status: 'limited', checkIds: [], limitation: 'Only extracted text was available.'}},
  {id: 'F-002', title: 'A rejected candidate about rounding', categories: ['F02'], profiles: [], level: 'target', object: {text: 'Figures are rounded.', locator: null}, criterionIds: [], evidenceIds: [], checkIds: [], challenge: {alternative: 'Rounding is stated.', checkIds: [], result: 'The rounding rule is stated in the notes.'}, verdict: 'rejected', impact: null, severity: null, confidence: 'low'}
];
const REGISTERS = {
  claims: [{id: 'CL-001', text: 'Revenue grew by 12 percent.', locator: {documentId: 'doc-a', startLine: 3, endLine: 3, quote: 'Revenue was 112 against 100.'}, type: 'quantity', status: 'contradicted', limitations: []}, {id: 'CL-002', text: 'Costs fell | by a tenth.\nSecond line.', locator: null, type: 'comparison', status: 'insufficient_support'}],
  criteria: [{id: 'C-001', text: 'Figures must match the source tables.', basis: {kind: 'source', locator: {documentId: 'doc-a', startLine: 1, endLine: 2, quote: 'Tables are authoritative.'}}, obligation: 'mandatory', applicability: 'applicable', coverage: 'addressed', reason: 'Checked by comparison.'}],
  evidence: [{id: 'E-001', claimId: 'CL-001', relation: 'contradicts', locator: {documentId: 'doc-a', startLine: 3, endLine: 3, quote: 'Revenue was 112 against 100.'}, originId: 'o1'}],
  checks: [{id: 'K-001', kind: 'comparison', status: 'executed', claimIds: ['CL-001'], criterionIds: ['C-001'], findingIds: ['F-001'], method: 'Compared the two figures.', result: 'They differ by 2 points.', limitation: null, artifactPaths: []}],
  omissions: [{id: 'O-001', expected: 'A restatement note', basisCriterionIds: ['C-001'], coverage: 'All segments searched', materiality: 'minor', verdict: 'confirmed_absent', findingId: 'F-001'}],
  findings: FINDINGS,
  'review-coverage': [{id: 'R-001', originalClaimId: 'CL-001', reviewerClaimId: null, findingId: 'F-001', status: 'covered_partially', reason: 'The original named the total only.'}],
  'external-sources': []
};
const MANDATE = {id: 'M-001', targetDocumentId: 'report-under-review', scope: 'The quarterly figures', purpose: 'Decide on funding', audience: 'Board', reportDate: '2026-01-01', coveredPeriod: {from: '2026-01-01', to: '2026-03-31'}, depth: 'deep', researchMode: 'closed', materiality: 'Figures that change a decision', profiles: [{id: 'review', reason: 'A review is under check'}], assumptions: ['Dates are in UTC'], limits: ['No spreadsheet access']};
const COVERAGE = {id: 'CV-001', completion: 'complete', counts: {segmentsRead: 4, claimsChecked: 2, confirmedFindings: 1}, gates: [{id: 'G1', status: 'passed', reason: 'All references resolve.'}], sources: [{id: 'doc-a', originalReview: 'limited', reason: 'Extracted text only.'}], segments: [{id: 's1', reading: 'read', claims: 'checked', requirements: 'checked'}], limitations: ['Spreadsheet not inspected.']};

/** Writes the quality registers into `base/<rel>/quality` and the matching report. */
export function writeQuality(base, rel, {partial = false} = {}) {
  const names = partial ? ['claims'] : Object.keys(REGISTERS);
  for (const name of names) put(base, `${rel}/quality/${name}.jsonl`, jsonl(REGISTERS[name]));
  if (!partial) {
    put(base, `${rel}/quality/mandate.json`, JSON.stringify(MANDATE));
    put(base, `${rel}/quality/coverage.json`, JSON.stringify(COVERAGE));
    put(base, `${rel}/quality-input/manifest.json`, JSON.stringify({sources: []}));
    put(base, `${rel}/quality-input/segments.jsonl`, jsonl([{id: 's1', text: 'segment'}]));
    put(base, `${rel}/quality-input/options.json`, JSON.stringify({depth: 'deep'}));
  }
  put(base, `${rel}/report.md`, `# Check Report\n\n${partial ? 'Draft text.' : 'Final text.'} [^e1]\n\n[^e1]: doc-a lines 3-3\n`);
}

function writeSources(base, rel, texts) {
  for (const [id, text] of Object.entries(texts)) put(base, `${rel}/sources/${id}.md`, text);
  put(base, `${rel}/sources/index.md`, '# Evidence inventory\n\n' + Object.keys(texts).map(id => `- ${id}.md`).join('\n'));
}

/** Text of the dossier sources and reports, by book number. Sources end with a newline. */
export function bookTexts(n) {
  const sources = {};
  for (let i = 1; i <= 4; i++) sources[`minute-book-${i}.md`] = `# Minute book ${i} of book ${n}\n\nThe board met on day ${i}. Resolution ${n}-${i} was adopted.\n`;
  sources['procedure.md'] = '# Procedure\n\nThe same procedure for every book.\n';
  return {
    sources,
    reports: {
      'report-correct.md': `# Review of book ${n}\n\nEvery resolution is recorded as adopted. [correct text]\n`,
      'report-mixed.md': `# Review of book ${n}\n\nOne resolution is recorded wrongly. [mixed text]\n`
    }
  };
}

/** Builds everything below `base`. Returns paths, ids and the sentinel files. `now` fixes the start of live runs. */
export function buildFixture(base, {now = Date.now(), deadPid = 2147483000, livePid = process.pid} = {}) {
  fs.mkdirSync(base, {recursive: true});
  const data = path.join(base, 'data');
  const tmp = path.join(base, 'tmp');
  const bench = path.join(base, 'bench');
  fs.mkdirSync(tmp, {recursive: true});
  const out = {base, data, tmp, bench, pid: 'proj-alpha', sentinel: SENTINEL, ids: {}, sentinelFiles: [], dossiers: [], keysFolders: []};
  const iso = ms => new Date(ms).toISOString();

  // The database files are never read: mode 000 and a sentinel that no answer may contain.
  for (const name of ['analyst.sqlite', 'analyst.sqlite-wal', 'analyst.sqlite-shm']) out.sentinelFiles.push(put(data, name, SENTINEL, 0o000));
  put(data, 'jobs/stale/note.txt', SENTINEL);
  out.sentinelFiles.push(path.join(data, 'jobs/stale/note.txt'));

  // Benchmark dossiers: six books, with the answer keys beside the fixtures folder.
  for (let n = 1; n <= 6; n++) {
    const id = `book-00${n}`;
    const texts = bookTexts(n);
    const root = path.join(bench, 'stage-a', 'fixtures', id);
    for (const [name, text] of Object.entries(texts.sources)) put(root, `sources/${name}`, text);
    for (const [name, text] of Object.entries(texts.reports)) put(root, name, text);
    const keysDraft = put(bench, `stage-a/keys-draft/${id}.json`, JSON.stringify({sentinel: SENTINEL}), 0o000);
    const keysSealed = put(bench, `stage-a/keys-sealed/${id}.json`, JSON.stringify({sentinel: SENTINEL}), 0o000);
    out.sentinelFiles.push(keysDraft, keysSealed);
    out.dossiers.push(root);
  }
  out.keysFolders.push(path.join(bench, 'stage-a/keys-draft'), path.join(bench, 'stage-a/keys-sealed'));

  const P = out.pid;
  put(data, `projects/${P}/project.md`, '# Alpha Project\n\nA synthetic project.\n');
  const doc = (n, name) => ({id: name.replace(/\.md$/, ''), title: name.replace(/\.md$/, ''), fileType: 'md', originalName: name, bytes: Buffer.byteLength(bookTexts(n).sources[name]), sha256: sha256(bookTexts(n).sources[name])});
  const book1Docs = Object.keys(bookTexts(1).sources).map(name => doc(1, name));
  const folder = rid => `projects/${P}/analyses/${rid}`;
  const traceFile = rid => `projects/${P}/traces/${rid}/trace.jsonl`;
  const created = ms => iso(ms);
  const input = (extra = {}) => JSON.stringify({kind: 'analysis', project: {id: P, name: 'Alpha Project'}, type: {id: 'skill-demo', name: 'Evidence Review', criteria: '## Criteria\n- Be exact.', playbook: '## Playbook\n- Read every source.', skill_revision: 3}, documents: [], ...extra});
  const analysisFiles = (rid, texts) => {
    writeSources(data, folder(rid), texts);
    put(data, `${folder(rid)}/review-criteria.md`, '# Review criteria\n\n- Be exact.\n');
    put(data, `${folder(rid)}/review-playbook.md`, '# Review playbook\n\n- Read every source.\n');
    put(data, `${folder(rid)}/report-template.md`, '# Report template\n\n## Summary\n');
    put(data, `${folder(rid)}/config.json`, JSON.stringify({codingAgent: 'codex', models: {codex: 'model-demo'}, efforts: {codex: 'high'}}));
    put(data, `${folder(rid)}/task.txt`, 'Review the dossier.');
  };

  // 1. A finished analysis with a trace, linked to book 1 by hashes.
  {
    const rid = out.ids.analysisDone = 'run-analysis-done';
    const t0 = now - 3 * 3600 * 1000;
    const t = analysisTrace({runId: rid, documents: book1Docs, createdAt: created(t0)});
    put(data, traceFile(rid), t.text());
    const texts = Object.fromEntries(Object.entries(bookTexts(1).sources).map(([n, text]) => [n.replace(/\.md$/, ''), text]));
    analysisFiles(rid, texts);
    put(data, `${folder(rid)}/task-1.txt`, 'PROMPT ONE: review the dossier carefully.');
    put(data, `${folder(rid)}/task-2.txt`, 'PROMPT TWO: repair the report.');
    put(data, `${folder(rid)}/response-1.txt`, 'Response one text.');
    put(data, `${folder(rid)}/validation-errors-1.json`, JSON.stringify({attempt: 1, errors: ['Finding 1 has no evidence footnote.', 'The Summary chapter is missing.'], instruction: 'Fix the Markdown output.'}));
    put(data, `${folder(rid)}/validation-result.json`, JSON.stringify({valid: true, attempt: 2, validatedAt: created(t0 + 30000)}));
    put(data, `${folder(rid)}/progress.md`, 'Checked the first document.\n\n---\n<!-- time -->\nChecked the second document.\n');
    put(data, `${folder(rid)}/report.md`, '# Analysis Report\n\n## Summary\n\nAll resolutions match. [^e1]\n\n[^e1]: minute-book-1 lines 1-2\n');
    put(data, `${folder(rid)}/input.json`, input({documents: book1Docs.map(d => ({id: d.id, title: d.title, markdown: bookTexts(1).sources[d.id + '.md']}))}));
    put(data, `${folder(rid)}/.ala/sessions/session-1.jsonl`, jsonl([
      {seq: 1, type: 'session', at: created(t0), id: 'session-1'},
      {seq: 2, type: 'user', at: created(t0 + 1000), turnId: 't1', text: 'PROMPT ONE: review the dossier carefully.'},
      {seq: 3, type: 'agent-message', at: created(t0 + 2000), turnId: 't1', outputKind: 'assistant', text: 'I read the sources. ' + 'x'.repeat(700)},
      {seq: 4, type: 'agent-message', at: created(t0 + 3000), turnId: 't1', outputKind: 'output', outputId: 'c1', text: 'sources/doc-a.md\n'},
      {seq: 5, type: 'final', at: created(t0 + 4000), turnId: 't1', text: 'The review is written.'},
      {seq: 6, type: 'turn-end', at: created(t0 + 5000), turnId: 't1', status: 'ok', durationMs: 4000}
    ]));
  }

  // 2. A finished Check Report with a trace, a kept copy of its work folder and a source report that matches book 1.
  {
    const rid = out.ids.checkDone = 'run-check-done';
    const t0 = now - 2 * 3600 * 1000;
    const reportText = bookTexts(1).reports['report-mixed.md'];
    const docs = [{id: 'report-under-review', title: 'Quarterly Report Under Review', fileType: 'md', originalName: null, bytes: Buffer.byteLength(reportText), sha256: sha256(reportText)}, ...book1Docs];
    const t = new TraceBuilder({start: t0});
    t.add('run-start', runStartFields({runId: rid, kind: 'quality', createdAt: created(t0), documents: docs}), 0);
    t.add('workspace-created', {path: `${out.tmp}/${WORK_PREFIX}Gone01`});
    t.add('turn-start', {turn: '1', attempt: 1, repair: false, continuation: false, resume: false, sessionId: 's', taskFile: 'task-1.txt', promptBytes: 50, promptSha256: sha256('p'), remainingMs: 900000});
    t.add('ala-event', {turn: '1', event: {type: 'coding-agent-final', agent: 'codex', message: 'Done.'}});
    t.add('turn-end', {turn: '1', outcome: 'exited', exitCode: 0, signal: null, durationMs: 5000});
    t.add('validation', {attempt: 1, valid: true, errorCount: 0, errors: []});
    t.add('published', {what: 'check-report', timeLimited: false});
    t.add('workspace-kept', {files: 6, bytes: 1234, skippedCount: 1, skipped: [{name: 'big.bin', reason: 'too large'}], excluded: ['skills'], complete: true, durationMs: 40});
    t.add('run-end', {status: 'completed', timeLimited: false, partial: false, failureCode: null, error: null, quality: {completion: 'complete', confirmedFindings: 1}, droppedAlaLines: 0, durationMs: 9000});
    put(data, traceFile(rid), t.text());
    writeQuality(data, folder(rid));
    writeSources(data, folder(rid), {'report-under-review': reportText, ...Object.fromEntries(Object.entries(bookTexts(1).sources).map(([n, text]) => [n.replace(/\.md$/, ''), text]))});
    put(data, `${folder(rid)}/verification-index.json`, JSON.stringify({schemaVersion: 1, files: []}));
    put(data, `${folder(rid)}/progress.md`, 'Compared the totals.\n');
    put(data, `${folder(rid)}/input.json`, JSON.stringify({kind: 'quality', documents: docs.map(d => ({id: d.id, title: d.title, markdown: ''})), sourceReport: {documentId: 'report-under-review'}}));
    const kept = `projects/${P}/traces/${rid}/workspace`;
    put(data, `${kept}/task-1.txt`, 'KEPT PROMPT: check the report.');
    put(data, `${kept}/response-1.txt`, 'Kept response text.');
    put(data, `${kept}/progress.md`, 'Kept progress note.\n');
    put(data, `${kept}/validation-result.json`, JSON.stringify({valid: true, attempt: 1}));
    put(data, `${kept}/notes/agent-notes.md`, '# Agent notes\n\nA file the agent wrote.\n');
    // Answer-key and evaluator names inside the kept copy, which the viewer must never open or list.
    put(data, `${kept}/keys-x/s.md`, SENTINEL);
    put(data, `${kept}/notes/keys-deep/s.md`, SENTINEL);
    put(data, `${kept}/notes/evaluator/e.md`, SENTINEL);
    put(data, `${kept}/data/raw.json`, JSON.stringify({sentinel: 'raw json must not be shown'}));
    writeQuality(data, kept, {partial: true});
  }

  // 3. A running Check Report: the live work folder holds a draft and partial registers.
  {
    const rid = out.ids.checkLive = 'run-check-live';
    const t0 = now - 60 * 1000;
    const work = fs.mkdtempSync(path.join(tmp, WORK_PREFIX));
    out.liveWork = work;
    const t = new TraceBuilder({start: t0, stepMs: 500});
    t.add('run-start', runStartFields({runId: rid, kind: 'quality', createdAt: created(t0), documents: [], pid: livePid}), 0);
    t.add('workspace-created', {path: work});
    t.add('turn-start', {turn: '1', attempt: 1, repair: false, continuation: false, resume: false, sessionId: 's', taskFile: 'task-1.txt', promptBytes: 50, promptSha256: sha256('p'), remainingMs: 900000});
    put(data, traceFile(rid), t.text());
    put(data, `${folder(rid)}/progress.md`, 'Live progress.\n');
    put(work, 'task-1.txt', 'LIVE PROMPT: check the report now.');
    put(work, 'progress.md', 'Live progress block.\n');
    writeQuality(work, '', {partial: true});
    put(work, 'input.json', SENTINEL);
  }

  // 4. A run with no run-end whose server is gone, and one whose time bound has passed.
  for (const [key, rid, pid, ageMs] of [['interruptedPid', 'run-interrupted-pid', deadPid, 5 * 60 * 1000], ['interruptedLate', 'run-interrupted-late', livePid, 6 * 3600 * 1000]]) {
    out.ids[key] = rid;
    const t = analysisTrace({runId: rid, documents: [], createdAt: created(now - ageMs), pid, end: false});
    put(data, traceFile(rid), t.text());
    fs.mkdirSync(path.join(data, folder(rid)), {recursive: true});
  }

  // 5. A failed run, a time-limited run with notes only and a time-limited run with a validated wrap-up report.
  {
    const rid = out.ids.failed = 'run-failed';
    const t = analysisTrace({runId: rid, documents: [], createdAt: created(now - 4 * 3600 * 1000), end: false});
    t.records.pop();
    t.add('run-end', {status: 'failed', timeLimited: false, partial: false, failureCode: 'validation', error: 'Deep Proof could not validate the review after 3 attempts.', quality: null, droppedAlaLines: 0, durationMs: 90000});
    put(data, traceFile(rid), t.text());
    fs.mkdirSync(path.join(data, folder(rid)), {recursive: true});
  }
  for (const [key, rid, partial] of [['timeLimitNotes', 'run-timelimit-notes', true], ['timeLimitWrap', 'run-timelimit-wrap', false]]) {
    out.ids[key] = rid;
    const t = new TraceBuilder({start: now - 5 * 3600 * 1000});
    t.add('run-start', runStartFields({runId: rid, createdAt: created(now - 5 * 3600 * 1000), documents: []}), 0);
    t.add('time-limit', {elapsedMs: 1800000, workMs: 1800000});
    t.add('wrap-up', {phase: 'start', remainingMs: 300000});
    t.add('wrap-up', {phase: partial ? 'not-validated' : 'validated'});
    t.add('published', {what: partial ? 'time-limit-notes' : 'report', timeLimited: true});
    t.add('run-end', {status: 'completed', timeLimited: true, partial, failureCode: null, error: null, quality: null, droppedAlaLines: 0, durationMs: 2100000});
    put(data, traceFile(rid), t.text());
    fs.mkdirSync(path.join(data, folder(rid)), {recursive: true});
  }

  // 6. Runs without a trace, inferred from files. The first holds book 2 exactly; the second holds book 3 trimmed.
  {
    const rid = out.ids.untracedDone = 'run-untraced-done';
    analysisFiles(rid, Object.fromEntries(Object.entries(bookTexts(2).sources).map(([n, text]) => [n.replace(/\.md$/, ''), text])));
    put(data, `${folder(rid)}/validation-result.json`, JSON.stringify({valid: true, attempt: 1}));
    put(data, `${folder(rid)}/report.md`, '# Untraced Report\n\nText.\n');
    put(data, `${folder(rid)}/task-1.txt`, 'Untraced prompt.');
    put(data, `${folder(rid)}/response-1.txt`, 'Untraced response.');
    put(data, `${folder(rid)}/input.json`, input());
    put(data, `${folder(rid)}/.ala/sessions/s.jsonl`, jsonl([{seq: 1, type: 'session', at: created(now - 7 * 3600 * 1000), id: 's'}, {seq: 2, type: 'user', at: created(now - 7 * 3600 * 1000 + 1000), turnId: 't1', text: 'Untraced prompt.'}, {seq: 3, type: 'turn-end', at: created(now - 7 * 3600 * 1000 + 5000), turnId: 't1', status: 'ok', durationMs: 4000}]));
    out.ids.untracedPasted = 'run-untraced-pasted';
    analysisFiles(out.ids.untracedPasted, Object.fromEntries(Object.entries(bookTexts(3).sources).map(([n, text]) => [n.replace(/\.md$/, ''), text.trim()])));
    put(data, `${folder(out.ids.untracedPasted)}/validation-result.json`, JSON.stringify({valid: true, attempt: 'wrapup'}));
    put(data, `${folder(out.ids.untracedPasted)}/report.md`, '# Wrap-up\n');
    out.ids.untracedNotes = 'run-untraced-notes';
    analysisFiles(out.ids.untracedNotes, {'doc-a': 'text'});
    put(data, `${folder(out.ids.untracedNotes)}/task-wrapup.txt`, 'wrap up');
    out.ids.untracedFailed = 'run-untraced-failed';
    analysisFiles(out.ids.untracedFailed, {'doc-a': 'text'});
    put(data, `${folder(out.ids.untracedFailed)}/validation-errors-3.json`, JSON.stringify({attempt: 3, errors: ['bad'], instruction: 'fix'}));
    out.ids.untracedCheck = 'run-untraced-check';
    writeQuality(data, folder(out.ids.untracedCheck));
    put(data, `${folder(out.ids.untracedCheck)}/verification-index.json`, JSON.stringify({schemaVersion: 1, files: []}));
    out.ids.untracedUnknown = 'run-untraced-unknown';
    put(data, `${folder(out.ids.untracedUnknown)}/sources/index.md`, '# Evidence inventory\n');
    out.ids.emptyRun = 'run-empty';
    fs.mkdirSync(path.join(data, folder(out.ids.emptyRun)), {recursive: true});
  }

  // 7. An imported report is an input, not a run.
  {
    const rid = out.ids.imported = 'run-imported';
    put(data, `${folder(rid)}/original.md`, bookTexts(1).reports['report-correct.md']);
    put(data, `${folder(rid)}/report.md`, bookTexts(1).reports['report-correct.md']);
  }

  // 8. A trace of version 2 falls back to the files; a trace with only run-start; a half-written last line.
  {
    const rid = out.ids.versionTwo = 'run-version-two';
    const t = new TraceBuilder({start: now - 9 * 3600 * 1000});
    t.add('run-start', {...runStartFields({runId: rid, createdAt: created(now - 9 * 3600 * 1000)}), version: 2}, 0);
    put(data, traceFile(rid), t.text());
    put(data, `${folder(rid)}/task.txt`, 'A version 2 run.');
    const only = out.ids.onlyStart = 'run-only-start';
    const o = new TraceBuilder({start: now - 30 * 1000});
    o.add('run-start', runStartFields({runId: only, createdAt: created(now - 30 * 1000), pid: livePid}), 0);
    put(data, traceFile(only), o.text());
    const half = out.ids.halfLine = 'run-half-line';
    const h = analysisTrace({runId: half, documents: [], createdAt: created(now - 20 * 1000), end: false, pid: livePid});
    put(data, traceFile(half), h.text() + '{"seq":99,"at":"2026-01-01T00:00:');
  }
  // A second project with no runs, and a project folder with no files at all.
  put(data, 'projects/proj-empty/project.md', '# Empty Project\n');
  fs.mkdirSync(path.join(data, 'projects/proj-bare'), {recursive: true});
  return out;
}

/** Restores write permission to every file below `root` so that a test can remove it. */
export function makeRemovable(root) {
  const walk = dir => {
    let entries;
    try {
      fs.chmodSync(dir, 0o755);
      entries = fs.readdirSync(dir, {withFileTypes: true});
    } catch {
      return;
    }
    for (const entry of entries) {
      const file = path.join(dir, entry.name);
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) walk(file);
      else try { fs.chmodSync(file, 0o644); } catch { /* already removable */ }
    }
  };
  walk(root);
}

/** Removes a fixture, whatever the modes its files were given. */
export function removeFixture(base) {
  makeRemovable(base);
  fs.rmSync(base, {recursive: true, force: true});
}

/** Makes a tree read-only for its owner: directories 0555, files 0444. Mode-000 files stay unreadable. */
export function makeReadOnly(root) {
  const walk = dir => {
    for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
      const file = path.join(dir, entry.name);
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) walk(file);
      else if ((fs.statSync(file).mode & 0o777) !== 0) fs.chmodSync(file, 0o444);
    }
    fs.chmodSync(dir, 0o555);
  };
  walk(root);
}
