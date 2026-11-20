/** Browser regression test of the process viewer's page (criterion 15 of the process viewer specification).
 *
 *   CHROME="<path of a Chrome binary>" node tools/test-process-viewer-browser.mjs
 *
 * It builds the synthetic fixture of tools/process-viewer-fixture.mjs in the host temporary folder, starts its own viewer on
 * a free port against it, and drives headless Chrome (a throwaway profile, the protocol over a pipe, see chrome-pipe.mjs)
 * through the page the way a reader would: real clicks, real key presses. It never uses port 8787 or 8790, a user's own
 * Chrome profile or the network, and it leaves no Chrome process behind. No model runs.
 *
 * Output: tmp/process-viewer-browser-<stamp>/ with result.md (every assertion as pass or fail) and the screenshots of a run
 * page and of a dossier page at 1440 and 390 pixels wide. The exit code is 0 only when every assertion passed. */
import fs from 'node:fs/promises';
import {existsSync, appendFileSync, readFileSync, realpathSync, mkdtempSync, writeFileSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {startViewer} from './process-viewer.mjs';
import {buildFixture, removeFixture} from './process-viewer-fixture.mjs';
import {startChrome, makeProfileRoot, removeProfileRoot, chromeBinary, sleep} from './chrome-pipe.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const WATCHDOG_MS = 8 * 60 * 1000;
// To see that an assertion has teeth, run the test against a page with a fix taken out: PV_BROWSER_PAGE=<that page file>.
const PAGE_FILE = process.env.PV_BROWSER_PAGE ? path.resolve(process.env.PV_BROWSER_PAGE) : undefined;
const WIDTHS = [[1440, 900], [390, 844]];
const STATUS_WORDS = ['Running', 'Completed', 'Time Limit Reached', 'Failed', 'Interrupted', 'Not Recorded', 'Imported Report'];
// A path in a page text: a slash followed by the name of a top folder of a unix machine. Built from parts so that this file holds none.
const PATH_PATTERN = new RegExp(`(^|[\\s"'(=:])/(?:${['Users', 'home', 'private', 'var', 'tmp', 'etc', 'opt', 'root', 'Volumes', 'mnt'].join('|')})/`);

const results = [];
const notes = [];
const events = {requests: [], exceptions: [], consoleErrors: [], ignored: [], hookErrors: []};
let browser = null;
let viewer = null;
let fetchHook = null;
const usedKeys = [];
let fixtureBase = null;
let profileRoot = null;
let outDir = null;

// ---------- Cleanup that survives a kill ----------
const killChrome = () => { try { browser?.chrome.kill('SIGKILL'); } catch { /* already gone */ } };
process.on('exit', killChrome);
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => { killChrome(); process.exit(130); });
const watchdog = setTimeout(() => { console.error(`The browser test took longer than ${WATCHDOG_MS / 1000} seconds and was stopped.`); killChrome(); process.exit(3); }, WATCHDOG_MS);
watchdog.unref();

// ---------- Harness ----------
async function check(name, fn) {
  const began = Date.now();
  try {
    const detail = await fn();
    results.push({name, ok: true, detail: detail ?? ''});
    console.log(`PASS ${name}${detail ? ` (${detail})` : ''} [${Date.now() - began} ms]`);
  } catch (error) {
    results.push({name, ok: false, detail: String(error.message ?? error).split('\n').slice(0, 6).join(' | ')});
    console.log(`FAIL ${name}: ${String(error.message ?? error).split('\n')[0]}`);
  }
}

const json = value => JSON.stringify(value);
async function ev(expression) {
  const r = await browser.page('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
  if (r.exceptionDetails) throw new Error(`The page threw: ${r.exceptionDetails.exception?.description ?? r.exceptionDetails.text}`);
  return r.result.value;
}
async function waitFor(expression, {timeout = 8000, label = expression} = {}) {
  const until = Date.now() + timeout;
  let last;
  while (Date.now() < until) {
    try {
      last = await ev(expression);
      if (last) return last;
    } catch (error) {
      last = error.message;
    }
    await sleep(60);
  }
  throw new Error(`Timed out after ${timeout} ms waiting for: ${label} (last value ${json(last)})`);
}
async function click(selector) {
  const point = await ev(`(() => { const e = document.querySelector(${json(selector)}); if (!e) return null; e.scrollIntoView({block: 'center'}); const r = e.getBoundingClientRect(); return {x: r.left + Math.min(r.width / 2, 60), y: r.top + r.height / 2}; })()`);
  assert(point, `No element for ${selector}`);
  for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) {
    await browser.page('Input.dispatchMouseEvent', {type, x: point.x, y: point.y, button: 'left', buttons: type === 'mousePressed' ? 1 : 0, clickCount: 1});
  }
}
async function pressEnter(selector) {
  assert(await ev(`(() => { const e = document.querySelector(${json(selector)}); if (!e) return false; e.focus(); return document.activeElement === e; })()`), `Cannot focus ${selector}`);
  const key = {key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13, text: '\r', unmodifiedText: '\r'};
  await browser.page('Input.dispatchKeyEvent', {type: 'keyDown', ...key});
  await browser.page('Input.dispatchKeyEvent', {type: 'keyUp', ...key});
}
const polls = () => ev(`Number(document.body.getAttribute('data-poll-count') || 0)`);
async function waitPolls(count) {
  const before = await polls();
  await waitFor(`Number(document.body.getAttribute('data-poll-count') || 0) >= ${before + count}`, {timeout: 20000, label: `${count} polls`});
}
// Under phone emulation Chrome widens the layout viewport to fit wide content, so window.innerWidth cannot show an overflow:
// the width that was asked for is the measure.
let viewportWidth = 0;
async function setViewport(width, height) {
  viewportWidth = width;
  await browser.page('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: width < 500});
  await sleep(200);
}
async function openRun(rid) {
  await ev(`location.hash = '#/run/proj-alpha/${rid}'`);
  await waitFor(`!!document.querySelector('main [data-run-key="proj-alpha/${rid}"]') && document.querySelector('main h1') && document.querySelector('main h1').textContent !== 'Loading the run…' && !!document.querySelector('main [data-block="results"]') && document.querySelector('[data-block="process"] .total') && document.querySelector('[data-block="process"] .total').textContent !== ''`, {label: `run ${rid} shown`});
}
const openDossier = async n => {
  await ev(`location.hash = '#/dossier/${n}'`);
  await waitFor(`!!document.querySelector('main [data-dossier="${n}"]') && document.querySelector('main h1').textContent !== 'Loading the dossier…'`, {label: `dossier ${n} shown`});
};
/** Shows the welcome page, a run page or a dossier page and waits until it is there. */
async function showView(view) {
  if (view === 'welcome') {
    await ev(`location.hash = ''`);
    await waitFor(`!!document.querySelector('main .welcome')`, {label: 'the welcome page'});
  } else if (view.startsWith('dossier:')) await openDossier(view.slice(8));
  else await openRun(view);
}
/** Opens every details element in the main area, then waits until every body is loaded. Returns the texts of failed bodies. */
async function openEverything() {
  await ev(`(() => { for (const d of document.querySelectorAll('main details')) d.open = true; return true; })()`);
  await waitFor(`[...document.querySelectorAll('main details[open] > .body')].every(b => !b.hasAttribute('aria-busy') && b.firstChild && !/^Loading/.test(b.textContent))`, {timeout: 20000, label: 'every opened body loaded'});
}
async function screenshot(name) {
  const [width, height] = await ev('[document.documentElement.scrollWidth, document.documentElement.scrollHeight]');
  const shot = await browser.page('Page.captureScreenshot', {format: 'png', captureBeyondViewport: true, clip: {x: 0, y: 0, width, height: Math.min(height, 9000), scale: 1}});
  const file = path.join(outDir, `${name}.png`);
  await fs.writeFile(file, Buffer.from(shot.data, 'base64'));
  return file;
}
const timings = [];
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const fmtDuration = ms => {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  const pad = n => String(n).padStart(2, '0');
  return m < 60 ? `${m} min ${pad(s % 60)} s` : `${Math.floor(m / 60)} h ${pad(m % 60)} min`;
};
/** Changes the viewer's own answers on their way to the page (or answers in its place). The viewer itself is not touched. */
async function intercept(patterns, handler) {
  fetchHook = handler;
  await browser.page('Fetch.enable', {patterns});
}
async function stopIntercept() {
  await browser.page('Fetch.disable');
  fetchHook = null;
}
const answerJson = (p, status, value) => browser.page('Fetch.fulfillRequest', {
  requestId: p.requestId, responseCode: status,
  responseHeaders: [{name: 'Content-Type', value: 'application/json; charset=utf-8'}, {name: 'Cache-Control', value: 'no-store'}],
  body: Buffer.from(JSON.stringify(value)).toString('base64')
});
async function changeAnswer(p, change) {
  const got = await browser.page('Fetch.getResponseBody', {requestId: p.requestId});
  const value = JSON.parse(got.base64Encoded ? Buffer.from(got.body, 'base64').toString('utf8') : got.body);
  change(value, new URL(p.request.url).pathname);
  return answerJson(p, p.responseStatusCode, value);
}
/** One GET of the viewer's own API from this process (not the page), for the numbers the page should show. */
const apiGet = async route => (await fetch(`http://127.0.0.1:${viewer.port}${route}`, {headers: {'X-Viewer-Key': viewer.key}})).json();
/** Opens the document of a card and returns the milliseconds until its text is on the page. */
async function openCardTimed(selector, bodySelector, timeout = 15000) {
  const began = Date.now();
  await click(`${selector} summary`);
  await waitFor(`(() => { const b = document.querySelector(${json(`${selector} .body`)}); return !!b && !b.hasAttribute('aria-busy') && !!b.querySelector(${json(bodySelector)}); })()`, {timeout, label: `${selector} rendered`});
  return Date.now() - began;
}
const MARKUP_ALLOWED = ['WBR', 'P', 'H3', 'H4', 'H5', 'H6', 'EM', 'STRONG', 'CODE', 'BR', 'SPAN', 'DIV', 'PRE', 'BLOCKQUOTE', 'UL', 'OL', 'LI', 'TABLE', 'THEAD', 'TBODY', 'TR', 'TH', 'TD', 'HR'];
const foreignElements = selector => ev(`[...document.querySelectorAll(${json(`${selector} *`)})].map(e => e.tagName).filter(t => !${json(MARKUP_ALLOWED)}.includes(t))`);

// ---------- The checks that look at any page state ----------
const overflow = () => ev('({scroll: document.documentElement.scrollWidth, inner: window.innerWidth, body: document.body.scrollWidth})');
async function iconProblems() {
  return ev(`(() => {
    const problems = [], rendered = [];
    for (const svg of document.querySelectorAll('svg.icon')) {
      const use = svg.querySelector('use');
      const target = use && document.querySelector(use.getAttribute('href'));
      if (!target || target.tagName.toLowerCase() !== 'symbol') { problems.push('unresolved ' + (use && use.getAttribute('href'))); continue; }
      if (!svg.getClientRects().length) continue;
      const box = svg.getBoundingClientRect();
      let drawn = 0;
      try { drawn = use.getBBox().width; } catch (e) { drawn = 0; }
      rendered.push(use.getAttribute('href'));
      if (!(box.width > 0 && box.height > 0)) problems.push('zero size ' + use.getAttribute('href'));
      else if (!(drawn > 0)) problems.push('nothing drawn ' + use.getAttribute('href'));
    }
    return {problems, rendered: rendered.length};
  })()`);
}

function contrastScript(selectors) {
  return `(() => {
    const lum = ([r, g, b]) => { const f = c => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
    const parse = s => { const m = s.match(/[\\d.]+/g).map(Number); return {rgb: m.slice(0, 3), a: m.length > 3 ? m[3] : 1}; };
    const backdrop = el => { for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c.a > 0.95) return c.rgb; } return [255, 255, 255]; };
    const low = [];
    let checked = 0;
    for (const sel of ${json(selectors)}) for (const el of document.querySelectorAll(sel)) {
      if (!el.getClientRects().length || !el.textContent.trim()) continue;
      const fg = parse(getComputedStyle(el).color).rgb, bg = backdrop(el);
      const a = lum(fg), b = lum(bg), ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      checked++;
      if (ratio < 4.5) low.push(sel + ' ' + ratio.toFixed(2) + ' ' + el.textContent.trim().slice(0, 30));
    }
    return {low, checked};
  })()`;
}

// ---------- Main ----------
let failedSetup = null;
try {
  await fs.mkdir(path.join(root, 'tmp'), {recursive: true});
  outDir = await fs.mkdtemp(path.join(root, 'tmp', 'process-viewer-browser-'));
  fixtureBase = mkdtempSync(path.join(realpathSync(os.tmpdir()), 'pv-browser-'));
  const fx = buildFixture(fixtureBase);
  const realBase = realpathSync(fixtureBase);
  // Hostile text in the kept copy of the Check Report, opened by the checks below.
  const keptNotes = path.join(fx.data, 'projects', fx.pid, 'traces', 'run-check-done', 'workspace', 'notes');
  const HOSTILE = [
    '# Hostile text', '',
    'A [click me](javascript:window.__bad=5) link, an image ![pixel](http://example.invalid/pixel.png) and a <a href="http://example.invalid/x">raw anchor</a>.', '',
    '<img src="x" onerror="window.__bad=6"> <script>window.__bad=7</script> <iframe src="http://example.invalid/frame"></iframe>', '',
    'An attribute quote: "><svg onload="window.__bad=8"> and \'><b onmouseover=\'window.__bad=9\'>bold?</b>', '',
    '[titled](http://example.invalid/page "a title") and <http://example.invalid/auto>.', '',
    '```html', '<script>window.__bad=10</script>', '```', ''
  ].join('\n');
  writeFileSync(path.join(keptNotes, 'hostile.md'), HOSTILE);
  writeFileSync(path.join(keptNotes, 'quotes.md'), `${'>'.repeat(3000)} deep text\n`);
  writeFileSync(path.join(keptNotes, 'wide-cell.md'), ['| A | B |', '| --- | --- |', `| ${'x'.repeat(100000)} | y |`, ''].join('\n'));
  writeFileSync(path.join(keptNotes, 'wide-columns.md'), [`|${Array.from({length: 2000}, (_, i) => ` h${i} `).join('|')}|`, `|${' --- |'.repeat(2000)}`, `|${' c |'.repeat(2000)}`, ''].join('\n'));
  writeFileSync(path.join(keptNotes, 'nested.md'), [`${Array.from({length: 40}, () => '>').join(' ')} forty quotes deep`, '', ...Array.from({length: 40}, (_, i) => `${'  '.repeat(i)}- level ${i + 1}`), ''].join('\n'));
  // A report of about 320 KB, so that one document is served in two parts of 262,144 bytes (the page offers the second).
  const LONG_PARAGRAPHS = 8000;
  writeFileSync(path.join(fx.data, 'projects', fx.pid, 'analyses', 'run-analysis-done', 'report.md'), Array.from({length: LONG_PARAGRAPHS}, (_, i) => `Paragraph ${i + 1} of the long report, with some words.`).join('\n\n') + '\n');
  const settings = {deepProofData: fx.data, dossiers: fx.dossiers, deepProofTmp: fx.tmp, port: 0};
  viewer = await startViewer({settings, pageFile: PAGE_FILE});
  usedKeys.push(viewer.key);
  const origin = `http://127.0.0.1:${viewer.port}`;
  assert.notEqual(viewer.port, 8787);
  assert.notEqual(viewer.port, 8790);
  profileRoot = await makeProfileRoot();
  browser = await startChrome({profileRoot, binary: chromeBinary(), cwd: root});
  const downloadDir = path.join(outDir, 'downloads');
  await fs.mkdir(downloadDir);
  const downloads = {begun: [], done: []};
  browser.on((message, source) => {
    const p = message.params ?? {};
    switch (message.method) {
      case 'Network.requestWillBeSent': events.requests.push({method: p.request.method, url: p.request.url}); break;
      case 'Runtime.exceptionThrown': events.exceptions.push(p.exceptionDetails.exception?.description ?? p.exceptionDetails.text); break;
      case 'Runtime.consoleAPICalled': if (p.type === 'error') events.consoleErrors.push(`console.error ${json(p.args.map(a => a.value ?? a.description))}`); break;
      case 'Log.entryAdded': if (p.entry.level === 'error') events.consoleErrors.push(`${p.entry.source}: ${p.entry.text} ${p.entry.url ?? ''}`); break;
      case 'Browser.downloadWillBegin': downloads.begun.push({guid: p.guid, name: p.suggestedFilename, url: p.url}); break;
      case 'Browser.downloadProgress': if (p.state === 'completed') downloads.done.push(p.guid); break;
      case 'Fetch.requestPaused':
        // A request the test chose to answer itself or to change; any other that arrives while interception is on goes on unchanged.
        (fetchHook ? fetchHook(p) : browser.page('Fetch.continueRequest', {requestId: p.requestId})).catch(error => events.hookErrors.push(error.message));
        break;
      default: break;
    }
  });
  for (const method of ['Page.enable', 'Runtime.enable', 'Log.enable', 'Network.enable']) await browser.page(method);
  await browser.page('Network.setCacheDisabled', {cacheDisabled: true});
  await browser.browser('Browser.setDownloadBehavior', {behavior: 'allow', downloadPath: downloadDir, eventsEnabled: true});
  const version = (await ev('navigator.userAgent')).replace(/^.*(HeadlessChrome\/\S+).*$/, '$1');
  notes.push(`Browser: ${version}`);
  await setViewport(...WIDTHS[0]);

  // ----- Start: the key leaves the address -----
  await check('the page loads with the policy of its one script, moves the key from the address into the tab and clears the address', async () => {
    const page = await fetch(`${origin}/`);
    const policy = page.headers.get('content-security-policy');
    assert.match(policy, /script-src 'sha256-[A-Za-z0-9+/=]+'/, 'the script is allowed by hash, not refused');
    await browser.page('Page.navigate', {url: viewer.url});
    await waitFor(`document.querySelectorAll('nav [data-run-key]').length > 0`, {label: 'the run list'});
    assert.equal(await ev('location.hash'), '', 'the address holds no key any more');
    assert.equal(await ev(`sessionStorage.getItem('viewer-key')`), viewer.key);
    assert.ok(!(await ev('location.href')).includes(viewer.key));
    assert.ok(events.requests.every(r => !r.url.includes(viewer.key)), 'no request carries the key in its address');
    return 'script-src carries a hash';
  });

  await check('both left-bar lists exist: Deep Proof Runs and Benchmark Dossiers, with six dossiers', async () => {
    const lists = await ev(`({
      runsHeading: document.querySelector('nav [data-list="runs"] h2').textContent.trim(),
      dossiersHeading: document.querySelector('nav [data-list="dossiers"] h2').textContent.trim(),
      runs: document.querySelectorAll('nav [data-list="runs"] [data-run-key]').length,
      dossiers: document.querySelectorAll('nav [data-list="dossiers"] [data-dossier]').length,
      nav: !!document.querySelector('nav[aria-label]'), main: document.querySelectorAll('main').length, h1: document.querySelectorAll('h1').length
    })`);
    assert.deepEqual([lists.runsHeading, lists.dossiersHeading], ['Deep Proof Runs', 'Benchmark Dossiers']);
    assert.equal(lists.dossiers, 6);
    assert.equal(lists.runs, 19, 'every run of the fixture has a button');
    assert.equal(lists.nav && lists.main === 1, true, 'a labelled nav and one main');
    assert.equal(lists.h1, 1, 'exactly one h1');
    return `${lists.runs} runs, ${lists.dossiers} dossiers`;
  });

  await check('each run shows an icon and a status word, and every kind of status of the fixture is present', async () => {
    const rows = await ev(`[...document.querySelectorAll('nav [data-run-key]')].map(b => ({key: b.dataset.runKey, status: b.dataset.status, kind: b.dataset.kind, word: b.querySelector('.st-word')?.textContent.trim(), icon: !!b.querySelector('.st-icon svg use'), title: b.querySelector('.run-title')?.textContent.trim()}))`);
    for (const row of rows) {
      assert.ok(row.icon, `${row.key} shows a status icon`);
      assert.ok(STATUS_WORDS.includes(row.word), `${row.key} shows a status word, got ${json(row.word)}`);
      assert.ok(row.title, `${row.key} shows a title`);
    }
    const present = new Set(rows.map(r => r.word));
    for (const word of STATUS_WORDS) assert.ok(present.has(word), `a run with status "${word}" is listed`);
    return `${present.size} different status words`;
  });

  await check('the filter buttons narrow the list: Running shows only running runs, Problems only failed, interrupted and limited runs', async () => {
    await click('[data-filter="running"]');
    const running = await ev(`[...document.querySelectorAll('nav [data-run-key]')].filter(b => !b.hidden).map(b => b.dataset.status)`);
    assert.ok(running.length === 3 && running.every(s => s === 'running'), json(running));
    await click('[data-filter="problems"]');
    const problems = await ev(`[...new Set([...document.querySelectorAll('nav [data-run-key]')].filter(b => !b.hidden).map(b => b.dataset.status))].sort()`);
    assert.deepEqual(problems, ['failed', 'interrupted', 'time-limit']);
    await click('[data-filter="all"]');
    assert.equal(await ev(`[...document.querySelectorAll('nav [data-run-key]')].filter(b => !b.hidden).length`), 19);
  });

  // ----- A run page -----
  await check('a click on a run opens its page; the blocks appear in the order Inputs, Process, Results, each with its own icon, word and colour', async () => {
    await click('nav [data-run-key="proj-alpha/run-analysis-done"]');
    await openRun('run-analysis-done');
    const blocks = await ev(`[...document.querySelectorAll('main [data-block]')].map(b => ({block: b.dataset.block, heading: b.querySelector('h2')?.textContent.trim(), icon: b.querySelector('.block-head svg use')?.getAttribute('href'), border: getComputedStyle(b).borderLeftColor, tag: b.querySelector('h2')?.tagName}))`);
    assert.deepEqual(blocks.map(b => b.block), ['inputs', 'process', 'results']);
    assert.deepEqual(blocks.map(b => b.heading), ['Inputs', 'Process', 'Results']);
    assert.equal(new Set(blocks.map(b => b.icon)).size, 3, 'three different heading icons');
    assert.equal(new Set(blocks.map(b => b.border)).size, 3, 'three different accent colours');
    assert.ok(blocks.every(b => b.tag === 'H2'));
    const order = await ev(`(() => { const [a, b, c] = ['inputs', 'process', 'results'].map(n => document.querySelector('main [data-block="' + n + '"]')); return !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) && !!(b.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING); })()`);
    assert.ok(order, 'the blocks follow each other in the page');
    assert.equal(await ev(`location.hash`), '#/run/proj-alpha/run-analysis-done');
    assert.equal(await ev(`document.querySelector('main h1').textContent`), 'Evidence Review');
    assert.equal(await ev(`document.querySelectorAll('h1').length`), 1);
    return blocks.map(b => b.border).join(' / ');
  });

  await check('the header shows chips for status, kind, agent, model, elapsed, attempt, time limit and trace', async () => {
    const text = await ev(`document.querySelector('main .chips').innerText`);
    for (const part of ['Completed', 'Analysis', 'codex', 'model-demo', 'Elapsed', 'Attempt 2 of 3', '30 min', 'Recorded']) assert.ok(text.includes(part), `the chips hold "${part}": ${text}`);
  });

  await check('the Elapsed chip of a run that has stopped ends at its end or its last sign of life, and an Imported Report has none (F3)', async () => {
    const seen = [];
    try {
      for (const rid of ['run-interrupted-pid', 'run-interrupted-late', 'run-check-done', 'run-failed']) {
        const {header} = await apiGet(`/api/runs/proj-alpha/${rid}`);
        await openRun(rid);
        await waitFor(`!!document.querySelector('main [data-elapsed]')`, {label: 'the Elapsed chip'});
        const expected = fmtDuration(Date.parse(header.endedAt ?? header.lastActivityAt) - Date.parse(header.startedAt));
        const shown = await ev(`document.querySelector('main [data-elapsed]').textContent`);
        assert.equal(shown, expected, `${rid} (${header.statusLabel}) shows ${shown}, expected ${expected}`);
        const sinceStart = fmtDuration(Date.now() - Date.parse(header.startedAt));
        if (header.status === 'interrupted') assert.notEqual(shown, sinceStart, `${rid} must not show the time since its start`);
        seen.push(`${rid} ${shown}`);
      }
      await openRun('run-imported');
      assert.equal(await ev(`document.querySelectorAll('main [data-elapsed]').length`), 0, 'no Elapsed chip on an Imported Report');
      assert.ok(!(await ev(`document.querySelector('main .chips').innerText`)).includes('Elapsed'));
    } finally {
      await openRun('run-analysis-done');
    }
    return seen.join('; ');
  });

  await check('a timeline entry opens in place by click and by the Enter key and stays open, and the page keeps its scroll position, across two polls', async () => {
    await click('[data-entry-n="4"] summary');
    await waitFor(`document.querySelector('[data-entry-n="4"] details').open && document.querySelector('[data-entry-n="4"] .body .md')`, {label: 'entry 4 opened by click'});
    await pressEnter('[data-entry-n="11"] summary');
    await waitFor(`document.querySelector('[data-entry-n="11"] details').open && document.querySelectorAll('[data-entry-n="11"] .body ol li').length === 2`, {label: 'entry 11 opened by Enter, with its two errors'});
    await ev(`document.querySelector('[data-entry-n="4"]').__mark = 'same node'; document.querySelector('[data-entry-n="11"]').__mark = 'same node'; document.querySelector('[data-card-id="skill"]').__mark = 'same node'; window.scrollTo(0, 420); true`);
    const scrollBefore = await ev('window.scrollY');
    const requestsBefore = events.requests.filter(r => r.url.includes('/api/runs?') || r.url.endsWith('/api/runs')).length;
    await waitPolls(2);
    const requestsAfter = events.requests.filter(r => r.url.includes('/api/runs?') || r.url.endsWith('/api/runs')).length;
    assert.ok(requestsAfter - requestsBefore >= 2, `the run list was requested again (${requestsAfter - requestsBefore} times)`);
    const after = await ev(`({
      open4: document.querySelector('[data-entry-n="4"] details').open, open11: document.querySelector('[data-entry-n="11"] details').open,
      mark4: document.querySelector('[data-entry-n="4"]').__mark, mark11: document.querySelector('[data-entry-n="11"]').__mark, markCard: document.querySelector('[data-card-id="skill"]').__mark,
      body4: document.querySelector('[data-entry-n="4"] .body').textContent.length > 20, body11: document.querySelectorAll('[data-entry-n="11"] .body ol li').length, y: window.scrollY,
      selected: document.querySelector('nav [aria-current="page"]')?.dataset.runKey
    })`);
    assert.ok(after.open4 && after.open11, 'both entries are still open');
    assert.deepEqual([after.mark4, after.mark11, after.markCard], ['same node', 'same node', 'same node'], 'the poll patched the page and did not rebuild it');
    assert.ok(after.body4 && after.body11 === 2, 'the bodies are still there');
    assert.ok(Math.abs(after.y - scrollBefore) <= 2, `scroll position ${scrollBefore} -> ${after.y}`);
    assert.equal(after.selected, 'proj-alpha/run-analysis-done', 'the selected run is still selected');
    return `scrollY ${scrollBefore} kept`;
  });

  await check('text of a file is shown as text: <script>, Romanian diacritics and a four-byte character in an agent message', async () => {
    const shown = await ev(`({
      bad: typeof window.__bad, scripts: document.querySelectorAll('main script').length, images: document.querySelectorAll('main img').length,
      body: document.querySelector('[data-entry-n="4"] .body').textContent, summary: document.querySelector('[data-entry-n="4"] .entry-summary').textContent
    })`);
    assert.equal(shown.bad, 'undefined', 'the script text did not run');
    assert.equal(shown.scripts + shown.images, 0, 'no element was made from the text');
    for (const where of [shown.body, shown.summary]) {
      assert.ok(where.includes('<script>window.__bad = 1</script>'), 'the markup is shown as literal text');
      assert.ok(where.includes('ăâîșț ĂÂÎȘȚ') && where.includes('😀'), 'diacritics and the four-byte character survive');
    }
  });

  await check('a command output step shows the legend that ALA reports output and not the command line, and the step filters narrow the list', async () => {
    await click('[data-entry-n="5"] summary');
    await waitFor(`document.querySelector('[data-entry-n="5"] .body .note')`, {label: 'command output note'});
    assert.match(await ev(`document.querySelector('[data-entry-n="5"] .body .note').textContent`), /not the command line/);
    await click('[data-step-filter="command"]');
    assert.deepEqual(await ev(`[...document.querySelectorAll('.timeline .entry')].filter(e => getComputedStyle(e).display !== 'none').map(e => e.dataset.entryKind)`), ['command-output']);
    await click('[data-step-filter="all"]');
    assert.equal(await ev(`[...document.querySelectorAll('.timeline .entry')].filter(e => getComputedStyle(e).display !== 'none').length`), 18);
  });

  // ----- A live run -----
  await check('a new trace record appended by the test appears in the open run within 7 seconds, as text', async () => {
    await openRun('run-check-live');
    const traceFile = path.join(fx.data, 'projects', fx.pid, 'traces', 'run-check-live', 'trace.jsonl');
    const lines = readFileSync(traceFile, 'utf8').trim().split('\n');
    const last = JSON.parse(lines.at(-1));
    const note = 'Appended by the browser test: "quotes", <b>tags</b> and ' + String.fromCharCode(0x219);
    assert.equal(await ev(`document.querySelectorAll('[data-entry-kind="progress-note"]').length`), 0);
    const began = Date.now();
    appendFileSync(traceFile, `${JSON.stringify({seq: last.seq + 1, at: new Date().toISOString(), type: 'progress-note', turn: '1', text: note, blocks: 1})}\n`);
    await waitFor(`document.querySelector('[data-entry-kind="progress-note"] .entry-summary')`, {timeout: 7000, label: 'the appended step'});
    const seen = Date.now() - began;
    const shown = await ev(`({summary: document.querySelector('[data-entry-kind="progress-note"] .entry-summary').textContent, bold: document.querySelectorAll('[data-entry-kind="progress-note"] b').length})`);
    assert.ok(shown.summary.includes('<b>tags</b>') && shown.bold === 0, 'the appended text is shown as text');
    assert.ok(seen <= 7000, `${seen} ms`);
    const chip = await ev(`document.querySelector('main [data-chip="status"]').textContent`);
    assert.ok(chip.includes('Running'), chip);
    return `${seen} ms`;
  });

  await check('the Elapsed chip of a running run counts up', async () => {
    await openRun('run-check-live');
    const first = await ev(`document.querySelector('main [data-elapsed]').textContent`);
    await sleep(2300);
    const second = await ev(`document.querySelector('main [data-elapsed]').textContent`);
    assert.notEqual(first, second, `${first} then ${second}`);
    return `${first} then ${second}`;
  });

  await check('on a running run whose trace grows, an opened entry stays open, keeps its node and its place on the screen while new steps arrive', async () => {
    const traceFile = path.join(fx.data, 'projects', fx.pid, 'traces', 'run-check-live', 'trace.jsonl');
    const append = (count, label) => {
      const last = JSON.parse(readFileSync(traceFile, 'utf8').trim().split('\n').at(-1));
      appendFileSync(traceFile, Array.from({length: count}, (_, i) => JSON.stringify({seq: last.seq + 1 + i, at: new Date().toISOString(), type: 'progress-note', turn: '1', text: `${label} ${i + 1}`, blocks: 1})).join('\n') + '\n');
    };
    append(30, 'Filler note');
    await waitFor(`document.querySelectorAll('.timeline [data-entry-n]').length >= 34`, {label: 'filler steps listed'});
    await click('.timeline [data-entry-kind="progress-note"]:nth-child(10) summary');
    await waitFor(`document.querySelector('.timeline [data-entry-kind="progress-note"]:nth-child(10) details').open && document.querySelector('.timeline [data-entry-kind="progress-note"]:nth-child(10) .body').textContent.length > 5`, {label: 'the entry is open'});
    const state = await ev(`(() => { const li = document.querySelector('.timeline [data-entry-kind="progress-note"]:nth-child(10)'); li.__mark = 'same node'; li.scrollIntoView({block: 'center'}); window.scrollBy(0, 40); return {n: li.dataset.entryN, top: li.getBoundingClientRect().top, y: window.scrollY, count: document.querySelectorAll('.timeline [data-entry-n]').length, body: li.querySelector('.body').textContent}; })()`);
    assert.ok(state.y > 400, `the page is scrolled (${state.y})`);
    const pollsBefore = await polls();
    for (let i = 1; i <= 7; i++) {
      append(1, `Arriving note ${i}`);
      await sleep(1000);
    }
    await waitFor(`document.querySelectorAll('.timeline [data-entry-n]').length === ${state.count + 7}`, {timeout: 8000, label: 'the arriving steps'});
    const after = await ev(`(() => { const li = document.querySelector('.timeline [data-entry-n="${state.n}"]'); return {mark: li.__mark, open: li.querySelector('details').open, top: li.getBoundingClientRect().top, y: window.scrollY, body: li.querySelector('.body').textContent, running: document.querySelector('main [data-chip="status"]').textContent.includes('Running')}; })()`);
    assert.ok((await polls()) - pollsBefore >= 2, 'at least two polls ran while the steps arrived');
    assert.equal(after.mark, 'same node', 'the entry was patched, not rebuilt');
    assert.equal(after.open, true);
    assert.equal(after.body, state.body, 'its body is unchanged');
    assert.ok(Math.abs(after.top - state.top) <= 2, `the entry stayed where it was on the screen: ${state.top} -> ${after.top} (scrollY ${state.y} -> ${after.y})`);
    assert.ok(after.running, 'the run is still shown as running');
    return `entry ${state.n}: top ${Math.round(state.top)} -> ${Math.round(after.top)}, scrollY ${Math.round(state.y)} -> ${Math.round(after.y)}, ${(await polls()) - pollsBefore} polls`;
  });

  // ----- A Check Report with findings -----
  await check('a finding card shows its verdict, and its challenge and an evidence quote once opened', async () => {
    await openRun('run-check-done');
    const verdict = await ev(`document.querySelector('[data-card-id="finding:F-001"] .verdict').textContent.trim()`);
    assert.equal(verdict, 'Confirmed');
    assert.equal(await ev(`document.querySelector('[data-card-id="finding:F-002"] .verdict').textContent.trim()`), 'Rejected', 'a rejected candidate is shown, marked Rejected');
    await click('[data-card-id="finding:F-001"] summary');
    await waitFor(`document.querySelector('[data-card-id="finding:F-001"] .body .md blockquote')`, {label: 'finding body'});
    const card = await ev(`({
      text: document.querySelector('[data-card-id="finding:F-001"] .body').innerText,
      headings: [...document.querySelectorAll('[data-card-id="finding:F-001"] .body h5')].map(h => h.textContent),
      quotes: [...document.querySelectorAll('[data-card-id="finding:F-001"] .body blockquote')].map(q => q.textContent.trim())
    })`);
    assert.ok(card.headings.includes('Challenge'), `a Challenge section: ${json(card.headings)}`);
    assert.ok(card.text.includes('The figures may be restated in a later table.') && card.text.includes('No later table restates the figures'));
    assert.ok(card.quotes.includes('Revenue was 112 against 100.'), `an evidence quote: ${json(card.quotes)}`);
    return `sections: ${card.headings.join(', ')}`;
  });

  await check('a register is a table and a kept copy is listed in a closed group; no raw JSON line is shown', async () => {
    await click('[data-card-id="register:claims"] summary');
    await waitFor(`document.querySelector('[data-card-id="register:claims"] .body table')`, {label: 'claims table'});
    const rows = await ev(`document.querySelectorAll('[data-card-id="register:claims"] .body tbody tr').length`);
    assert.equal(rows, 2);
    assert.equal(await ev(`document.querySelector('details.kept').open`), false, 'the kept files are closed by default');
    assert.equal(await ev(`document.querySelectorAll('details.kept .cards > *').length`), 13);
  });

  await check('hostile Markdown (a javascript: link, an image, raw HTML, an attribute quote) is shown as text: no element, no script run, no request leaving the origin', async () => {
    await click('details.kept > summary');
    const requestsBefore = events.requests.length;
    const ms = await openCardTimed('[data-card-id="kept:notes/hostile.md"]', '.md');
    const shown = await ev(`({bad: typeof window.__bad, text: document.querySelector('[data-card-id="kept:notes/hostile.md"] .body').textContent})`);
    assert.equal(shown.bad, 'undefined', 'nothing ran');
    assert.deepEqual(await foreignElements('[data-card-id="kept:notes/hostile.md"] .md'), [], 'no element other than the renderer\'s own');
    assert.equal(await ev(`document.querySelectorAll('[data-card-id="kept:notes/hostile.md"] .body a, [data-card-id="kept:notes/hostile.md"] .body img, [data-card-id="kept:notes/hostile.md"] .body script, [data-card-id="kept:notes/hostile.md"] .body iframe, [data-card-id="kept:notes/hostile.md"] .body svg, [data-card-id="kept:notes/hostile.md"] .body b').length`), 0);
    for (const literal of ['click me', 'javascript:window.__bad=5', 'pixel', 'http://example.invalid/pixel.png', '<a href="http://example.invalid/x">raw anchor</a>', '<img src="x" onerror="window.__bad=6">', '<script>window.__bad=7</script>', '<iframe src="http://example.invalid/frame"></iframe>', '"><svg onload="window.__bad=8">', '<b onmouseover=\'window.__bad=9\'>bold?</b>', 'titled', '<script>window.__bad=10</script>']) {
      assert.ok(shown.text.includes(literal), `shown as text: ${literal}`);
    }
    const sent = events.requests.slice(requestsBefore).filter(r => new URL(r.url).origin !== origin);
    assert.deepEqual(sent, [], 'no request left the origin');
    assert.ok(!events.requests.some(r => r.url.includes('example.invalid')));
    return `${ms} ms`;
  });

  await check('a line of 3,000 quote marks, forty nested quotes and lists, a table row of 100 KB and a table of 2,000 columns are shown in full and quickly, and the page keeps polling (F2)', async () => {
    const quote = await openCardTimed('[data-card-id="kept:notes/quotes.md"]', 'blockquote');
    const shown = await ev(`({text: document.querySelector('[data-card-id="kept:notes/quotes.md"] .body').textContent, busy: document.querySelector('[data-card-id="kept:notes/quotes.md"] .body').hasAttribute('aria-busy'), depth: (() => { let d = 0; let e = document.querySelector('[data-card-id="kept:notes/quotes.md"] .body blockquote'); while (e && e.querySelector(':scope > blockquote')) { e = e.querySelector(':scope > blockquote'); d++; } return d; })()})`);
    assert.ok(!shown.busy && !/^Loading/.test(shown.text), 'the document is not stuck on Loading');
    assert.ok(shown.text.includes('deep text'), 'the text after the quote marks is there');
    assert.ok(shown.depth <= 30, `quotes nest only so deep: ${shown.depth}`);
    assert.ok(quote < 1000, `quotes: ${quote} ms`);
    const nested = await openCardTimed('[data-card-id="kept:notes/nested.md"]', 'ul');
    assert.ok((await ev(`document.querySelector('[data-card-id="kept:notes/nested.md"] .body').textContent`)).includes('forty quotes deep'));
    assert.ok((await ev(`document.querySelector('[data-card-id="kept:notes/nested.md"] .body').textContent`)).includes('level 40'), 'the deepest list level is shown, as text');
    assert.ok(nested < 1000, `nesting: ${nested} ms`);
    const cell = await openCardTimed('[data-card-id="kept:notes/wide-cell.md"]', 'table');
    const c = await ev(`({cells: document.querySelectorAll('[data-card-id="kept:notes/wide-cell.md"] tbody td').length, len: document.querySelector('[data-card-id="kept:notes/wide-cell.md"] tbody td').textContent.length})`);
    assert.deepEqual([c.cells, c.len], [2, 100000], 'the long cell is whole');
    assert.ok(cell < 1000, `wide cell: ${cell} ms`);
    const columns = await openCardTimed('[data-card-id="kept:notes/wide-columns.md"]', 'table');
    assert.equal(await ev(`document.querySelectorAll('[data-card-id="kept:notes/wide-columns.md"] tbody td').length`), 2000);
    assert.ok(columns < 1000, `wide table: ${columns} ms`);
    for (const id of ['quotes', 'nested', 'wide-cell', 'wide-columns']) assert.deepEqual(await foreignElements(`[data-card-id="kept:notes/${id}.md"] .md`), []);
    const before = await polls();
    await waitPolls(1);
    assert.ok((await polls()) > before, 'the page still polls');
    timings.push(`3,000 quote marks ${quote} ms; 40 nested quotes and lists ${nested} ms; table row of 100 KB ${cell} ms; table of 2,000 columns ${columns} ms`);
    return `quotes ${quote} ms, nesting ${nested} ms, wide cell ${cell} ms, 2,000 columns ${columns} ms`;
  });

  // ----- Sweep: every run, everything opened -----
  await check('the main text of every run, with every entry and card opened, holds no absolute path, no trace file name and no raw record', async () => {
    const keys = await ev(`[...document.querySelectorAll('nav [data-run-key]')].map(b => b.dataset.runKey.split('/')[1])`);
    const problems = [];
    const failedBodies = [];
    let opened = 0;
    let fixtureAnswers = 0;
    for (const rid of keys) {
      await openRun(rid);
      await openEverything();
      const text = await ev(`document.querySelector('main').innerText`);
      const side = await ev(`document.querySelector('nav').innerText`);
      opened += await ev(`document.querySelectorAll('main details[open]').length`);
      for (const [where, t] of [['main', text], ['nav', side]]) {
        const stray = t.match(/(^|[\s(])(null|undefined|NaN)([\s).,:]|$)|\[object /);
        if (stray) problems.push(`${rid} ${where}: stray word ${json(stray[0])}`);
        if (PATH_PATTERN.test(t)) problems.push(`${rid} ${where}: absolute path ${t.match(PATH_PATTERN)[0]}`);
        for (const needle of [fx.base, realBase, path.basename(fixtureBase), 'trace.jsonl', '"seq":', '"type":"', fx.sentinel]) if (t.includes(needle)) problems.push(`${rid} ${where}: contains ${needle}`);
      }
      // The fixture's runs that name a prompt file the fixture never wrote get the server's plain sentence for it; that is an answer, not a failure.
      for (const body of await ev(`[...document.querySelectorAll('main details[open] > .body')].map(b => b.textContent.trim()).filter(t => /could not be read|not available any more|There is no such|does not exist|Not found/i.test(t))`)) {
        if (body === 'The prompt file is not available any more.') fixtureAnswers++;
        else failedBodies.push(`${rid}: ${body.slice(0, 80)}`);
      }
    }
    assert.deepEqual(problems, []);
    notes.push(`Opened ${opened} entries and cards on ${keys.length} runs; bodies that could not be read: ${failedBodies.length ? json(failedBodies) : 'none'}; ${fixtureAnswers} prompt bodies answered "The prompt file is not available any more." because the fixture wrote no prompt file for that run.`);
    assert.deepEqual(failedBodies, [], 'a body that cannot be read is a defect of the page or of the fixture');
    return `${keys.length} runs, ${opened} opened`;
  });

  await check('a document of about 320 KB is served in two parts: the page offers the second and then shows every paragraph once', async () => {
    await openRun('run-analysis-done');
    await click('[data-card-id="report"] summary');
    await waitFor(`document.querySelector('[data-card-id="report"] .body .md')`, {label: 'the first part'});
    const first = await ev(`({paragraphs: document.querySelectorAll('[data-card-id="report"] .body .md p').length, more: document.querySelector('[data-card-id="report"] .body .more')?.textContent ?? null})`);
    assert.equal(first.more, 'Show the next part (2 of 2)');
    assert.ok(first.paragraphs > 0 && first.paragraphs < LONG_PARAGRAPHS, `part one holds ${first.paragraphs} paragraphs`);
    await click('[data-card-id="report"] .body .more');
    await waitFor(`document.querySelectorAll('[data-card-id="report"] .body .md p').length === ${LONG_PARAGRAPHS}`, {label: 'every paragraph after the second part'});
    const all = await ev(`[...document.querySelectorAll('[data-card-id="report"] .body .md p')].map(p => p.textContent)`);
    assert.equal(all.length, LONG_PARAGRAPHS);
    assert.ok(all.every((text, i) => text === `Paragraph ${i + 1} of the long report, with some words.`), 'paragraphs in order, none cut, none repeated');
    assert.equal(await ev(`document.querySelector('[data-card-id="report"] .body .more')`), null, 'no button once everything is loaded');
    return `part one ${first.paragraphs} paragraphs, then all ${LONG_PARAGRAPHS}`;
  });

  // ----- Dossier page -----
  await check('a dossier page shows sources, two reports under neutral names, the warning, linked runs, and downloads a report with its neutral name and unchanged bytes', async () => {
    await click('nav [data-dossier="1"]');
    await openDossier(1);
    const reportBytes = {correct: readFileSync(path.join(fx.dossiers[0], 'report-correct.md')), mixed: readFileSync(path.join(fx.dossiers[0], 'report-mixed.md'))};
    const neutral = Object.values(reportBytes).map(b => `report-${sha(b).slice(0, 12)}.md`).sort();
    const page = await ev(`({
      text: document.querySelector('main').innerText, sources: document.querySelectorAll('main [data-block="sources"] details').length,
      reports: [...document.querySelectorAll('main [data-block="reports"] details .card-title')].map(e => e.textContent).sort(),
      warning: document.querySelector('main .warning')?.textContent ?? '', buttons: [...document.querySelectorAll('main [data-download]')].map(b => b.textContent.trim()),
      linked: [...document.querySelectorAll('main [data-block="linked"] [data-run-key]')].map(b => b.dataset.runKey).sort()
    })`);
    assert.equal(page.sources, 5);
    assert.deepEqual(page.reports, neutral, 'both reports appear under neutral names');
    assert.ok(!/report-(correct|mixed)/i.test(page.text), 'no original report name is shown');
    assert.match(page.warning, /Deep Proof gives the reviewing agent the file name and the title of an imported report/);
    assert.deepEqual(page.buttons, ['Download With A Neutral Name', 'Download With A Neutral Name']);
    assert.ok(page.linked.includes('proj-alpha/run-check-done') && page.linked.includes('proj-alpha/run-analysis-done'), `linked runs: ${json(page.linked)}`);
    const first = await ev(`document.querySelector('main [data-block="reports"] details summary .card-title').textContent`);
    await click('main [data-block="reports"] details summary');
    await waitFor(`document.querySelector('main [data-block="reports"] details .body .md')`, {label: 'report text'});
    const requestsBefore = events.requests.length;
    await click(`main [data-download="${first.slice(7, 19)}"]`);
    await waitFor('true');
    const until = Date.now() + 10000;
    while (Date.now() < until && !downloads.done.length) await sleep(100);
    assert.equal(downloads.begun.length, 1, 'one download began');
    assert.equal(downloads.begun[0].name, first, 'the file is saved under its neutral name');
    assert.ok(downloads.done.includes(downloads.begun[0].guid), 'the download completed');
    // Chrome, told to allow downloads, saves the file under the name the page gave it.
    const saved = await fs.readFile(path.join(downloadDir, downloads.begun[0].name));
    assert.equal(sha(saved).slice(0, 12), first.slice(7, 19), 'the bytes are those of the report');
    const fetched = events.requests.slice(requestsBefore).filter(r => r.url.includes('/download'));
    assert.ok(fetched.length >= 1 && fetched.every(r => r.method === 'GET'), 'the download was a GET');
    await fs.rm(path.join(downloadDir, downloads.begun[0].name), {force: true});
    return `${first}, ${saved.length} bytes`;
  });

  await check('a linked run opens from the dossier page', async () => {
    await click('main [data-block="linked"] [data-run-key="proj-alpha/run-check-done"]');
    await waitFor(`location.hash === '#/run/proj-alpha/run-check-done' && document.querySelector('main [data-run-key="proj-alpha/run-check-done"]')`, {label: 'the linked run'});
  });

  await check('a run that was not there can be tried again by clicking it again (F4)', async () => {
    await showView('welcome');
    let refused = 0;
    await intercept([{urlPattern: '*/api/runs/proj-alpha/run-failed', requestStage: 'Request'}], async p => {
      if (refused < 1) { refused++; return answerJson(p, 404, {error: 'This run does not exist.'}); }
      return browser.page('Fetch.continueRequest', {requestId: p.requestId});
    });
    try {
      await click('nav [data-run-key="proj-alpha/run-failed"]');
      await waitFor(`/This run is not there/.test(document.querySelector('main h1')?.textContent ?? '')`, {label: 'the not-there page'});
      assert.equal(refused, 1);
      await click('nav [data-run-key="proj-alpha/run-failed"]');
      await waitFor(`!!document.querySelector('main [data-run-key="proj-alpha/run-failed"]') && document.querySelector('main h1').textContent === 'Evidence Review'`, {timeout: 2500, label: 'the run after the second click'});
    } finally {
      await stopIntercept();
    }
  });

  await check('a run with no start time shows no date at all, not 1 January 1970', async () => {
    await showView('welcome');
    await intercept([{urlPattern: '*/api/runs', requestStage: 'Response'}, {urlPattern: '*/api/runs/proj-alpha/run-empty', requestStage: 'Response'}], p => changeAnswer(p, (value, pathname) => {
      const none = {startedAt: null, endedAt: null, lastActivityAt: null};
      if (pathname.endsWith('/api/runs')) {
        for (const run of value) if (run.key === 'proj-alpha/run-empty') Object.assign(run, none);
      } else Object.assign(value.header, none);
    }));
    try {
      await waitFor(`(() => { const b = document.querySelector('nav [data-run-key="proj-alpha/run-empty"] .run-when'); return !!b && /^status inferred/.test(b.textContent); })()`, {timeout: 8000, label: 'the run without a start shows no time, only the inferred note'});
      await ev(`location.hash = '#/run/proj-alpha/run-empty'`);
      await waitFor(`document.querySelector('main [data-run-key="proj-alpha/run-empty"]') && document.querySelector('main h1').textContent === 'run-empty'`, {label: 'the run page'});
      const text = await ev(`document.body.innerText`);
      assert.ok(!/1970|1 Jan\b/.test(text), 'no 1970 anywhere');
      assert.equal(await ev(`document.querySelectorAll('main [data-elapsed]').length`), 0, 'no Elapsed chip without a start');
    } finally {
      await stopIntercept();
    }
    return 'sidebar time empty, no Elapsed chip';
  });

  // ----- Settings -----
  await check('the settings panel is the only place that shows the configured folders, and Escape closes it', async () => {
    assert.ok(!(await ev(`document.querySelector('main').innerText`)).includes(fx.data));
    await click('#settings-open');
    await waitFor(`!document.getElementById('settings-panel').hidden && document.querySelector('#settings-panel dl')`, {label: 'settings'});
    const panel = await ev(`document.getElementById('settings-panel').innerText`);
    assert.ok(panel.includes(realBase) || panel.includes(fx.data), 'the panel shows the data folder');
    assert.ok(panel.includes('Dossier folders (6)'));
    assert.equal(await ev(`document.getElementById('settings-panel').closest('main')`), null, 'the panel is outside main');
    await browser.page('Input.dispatchKeyEvent', {type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27});
    await browser.page('Input.dispatchKeyEvent', {type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27});
    await waitFor(`document.getElementById('settings-panel').hidden`, {label: 'settings closed'});
  });

  // ----- Icons, contrast, overflow at 1440 -----
  await check('every icon has a non-zero size, draws something and resolves to a symbol (run page with everything open, dossier page)', async () => {
    await openRun('run-check-done');
    await openEverything();
    const run = await iconProblems();
    await openDossier(1);
    const dossier = await iconProblems();
    assert.deepEqual([...run.problems, ...dossier.problems], []);
    assert.ok(run.rendered > 40 && dossier.rendered > 10, `icons rendered: ${run.rendered} and ${dossier.rendered}`);
    return `${run.rendered} + ${dossier.rendered} icons`;
  });

  await check('text contrast is at least 4.5 to 1 on the main texts of the left bar, the header, the blocks and the cards', async () => {
    await openRun('run-analysis-done');
    const r = await ev(contrastScript(['nav .st-word', 'nav .run-when', 'nav .group h3', '.filters button', '.chip', '.chip.status', '.block-head h2', '.block-head p', '.card-title', '.card-meta', '.mini', '.entry-title', '.entry-summary', '.entry-time', '.toolbar button', '.verdict', '.group-title', '.empty', '.sub']));
    assert.deepEqual(r.low, []);
    return `${r.checked} texts checked`;
  });

  await check('no horizontal overflow at 1440 pixels on the welcome page, a run page with everything open and a dossier page', async () => {
    for (const [name, view] of [['welcome', 'welcome'], ['run', 'run-check-done'], ['dossier', 'dossier:2']]) {
      await showView(view);
      if (name === 'run') await openEverything();
      const o = await overflow();
      assert.ok(o.scroll <= viewportWidth && o.body <= viewportWidth, `${name}: scrollWidth ${o.scroll}, body ${o.body}, viewport ${viewportWidth}`);
    }
  });

  // ----- Screenshots at 1440 -----
  const shots = [];
  await check('screenshots of a run page with open entries and of a dossier page at 1440 pixels wide', async () => {
    await openRun('run-analysis-done');
    await ev(`(() => { for (const n of [4, 11]) document.querySelector('[data-entry-n="' + n + '"] details').open = true; return true; })()`);
    await waitFor(`document.querySelectorAll('[data-entry-n="4"] .body .md, [data-entry-n="11"] .body ol').length === 2`);
    await ev('window.scrollTo(0, 0)');
    shots.push(await screenshot('run-1440'));
    await openDossier(1);
    await click('main [data-block="reports"] details summary');
    await waitFor(`document.querySelector('main [data-block="reports"] details .body .md')`);
    await ev('window.scrollTo(0, 0)');
    shots.push(await screenshot('dossier-1440'));
  });

  // ----- 390 pixels wide -----
  await setViewport(...WIDTHS[1]);
  await check('at 390 pixels the left bar opens from its button and picks a run, and closes after the pick', async () => {
    await showView('welcome');
    await ev('window.scrollTo(0, 0)');
    assert.ok(await ev(`getComputedStyle(document.getElementById('side-body')).display !== 'none'`), 'the lists show on the welcome page');
    await click('nav [data-run-key="proj-alpha/run-failed"]');
    await openRun('run-failed');
    assert.equal(await ev(`getComputedStyle(document.getElementById('side-body')).display`), 'none', 'the bar folds away once a run is open');
    await click('#nav-toggle');
    await waitFor(`getComputedStyle(document.getElementById('side-body')).display !== 'none' && document.getElementById('nav-toggle').getAttribute('aria-expanded') === 'true'`, {label: 'the bar opened'});
    await click('nav [data-run-key="proj-alpha/run-analysis-done"]');
    await openRun('run-analysis-done');
    assert.equal(await ev(`getComputedStyle(document.getElementById('side-body')).display`), 'none');
  });

  await check('no horizontal overflow at 390 pixels on the welcome page, run pages with everything open (tables, findings, entries), a dossier page and the settings panel', async () => {
    const problems = [];
    for (const [name, view] of [['welcome', 'welcome'], ['analysis', 'run-analysis-done'], ['check', 'run-check-done'], ['live', 'run-check-live'], ['untraced', 'run-untraced-done'], ['dossier', 'dossier:1']]) {
      await showView(view);
      if (name !== 'welcome') await openEverything();
      const o = await overflow();
      if (!(o.scroll <= viewportWidth && o.body <= viewportWidth)) problems.push(`${name}: scrollWidth ${o.scroll}, body ${o.body}, viewport ${viewportWidth}`);
    }
    await click('#settings-open');
    await waitFor(`!document.getElementById('settings-panel').hidden && document.querySelector('#settings-panel dl')`);
    const panel = await ev(`(() => { const r = document.getElementById('settings-panel').getBoundingClientRect(); return {left: r.left, right: r.right, inner: window.innerWidth}; })()`);
    if (panel.left < 0 || panel.right > viewportWidth) problems.push(`settings panel ${json(panel)}`);
    const o = await overflow();
    if (!(o.scroll <= viewportWidth)) problems.push(`settings open: scrollWidth ${o.scroll}`);
    await ev(`document.getElementById('settings-panel').hidden = true; true`);
    assert.deepEqual(problems, []);
  });

  await check('icons at 390 pixels have a size and draw something', async () => {
    await openRun('run-check-done');
    await openEverything();
    const r = await iconProblems();
    assert.deepEqual(r.problems, []);
    assert.ok(r.rendered > 40);
  });

  await check('screenshots of a run page with open entries and of a dossier page at 390 pixels wide', async () => {
    await openRun('run-analysis-done');
    await ev(`(() => { for (const n of [4, 11]) document.querySelector('[data-entry-n="' + n + '"] details').open = true; return true; })()`);
    await waitFor(`document.querySelectorAll('[data-entry-n="4"] .body .md, [data-entry-n="11"] .body ol').length === 2`);
    await ev('window.scrollTo(0, 0)');
    shots.push(await screenshot('run-390'));
    await openDossier(1);
    await click('main [data-block="reports"] details summary');
    await waitFor(`document.querySelector('main [data-block="reports"] details .body .md')`);
    await ev('window.scrollTo(0, 0)');
    shots.push(await screenshot('dossier-390'));
    await setViewport(...WIDTHS[0]);
  });

  await check('a trace with 1,100 more steps (three requests of at most 500) is listed whole: unique, contiguous step numbers and the total shown', async () => {
    await openRun('run-check-live');
    const traceFile = path.join(fx.data, 'projects', fx.pid, 'traces', 'run-check-live', 'trace.jsonl');
    const last = JSON.parse(readFileSync(traceFile, 'utf8').trim().split('\n').at(-1));
    const before = await ev(`document.querySelectorAll('.timeline [data-entry-n]').length`);
    const MORE = 1100;
    const at = Date.now();
    appendFileSync(traceFile, Array.from({length: MORE}, (_, i) => JSON.stringify({seq: last.seq + 1 + i, at: new Date(at + i).toISOString(), type: 'progress-note', turn: '1', text: `Bulk note ${i + 1}`, blocks: 1})).join('\n') + '\n');
    const expected = before + MORE;
    await waitFor(`document.querySelectorAll('.timeline [data-entry-n]').length === ${expected}`, {timeout: 30000, label: `${expected} entries`});
    const numbers = await ev(`[...document.querySelectorAll('.timeline [data-entry-n]')].map(e => Number(e.dataset.entryN))`);
    assert.deepEqual(numbers, Array.from({length: expected}, (_, i) => i + 1), 'step numbers are unique and contiguous, in order');
    assert.equal(await ev(`document.querySelector('[data-block="process"] .total').textContent`), `${expected} steps`);
    assert.equal(await ev(`document.querySelector('.timeline [data-entry-n="${expected}"] .entry-summary').textContent`), `Bulk note ${MORE}`);
    return `${expected} entries`;
  });

  await check('when the viewer restarts with another key, the page goes to the key screen, closes the settings panel and takes the new key', async () => {
    await showView('run-analysis-done');
    await click('#settings-open');
    await waitFor(`!document.getElementById('settings-panel').hidden && document.querySelector('#settings-panel dl')`, {label: 'settings open'});
    const port = viewer.port;
    const newKey = crypto.randomBytes(16).toString('hex');
    await viewer.close();
    viewer = await startViewer({settings: {...settings, port}, key: newKey, pageFile: PAGE_FILE});
    usedKeys.push(newKey);
    assert.equal(viewer.port, port);
    await waitFor(`!!document.querySelector('[data-key-form]')`, {timeout: 15000, label: 'the key screen'});
    assert.equal(await ev(`document.getElementById('settings-panel').hidden`), true, 'the settings panel is closed');
    assert.equal(await ev(`document.getElementById('settings-open').getAttribute('aria-expanded')`), 'false');
    assert.equal(await ev(`sessionStorage.getItem('viewer-key')`), null);
    assert.match(await ev(`document.getElementById('key-error').textContent`), /refused/);
    assert.equal(await ev(`document.querySelectorAll('nav [data-run-key]').length`), 0, 'no data left on the page');
    await click('#key-input');
    await browser.page('Input.insertText', {text: newKey});
    await pressEnter('#key-input');
    await waitFor(`document.querySelectorAll('nav [data-run-key]').length > 0 && !!document.querySelector('main [data-run-key="proj-alpha/run-analysis-done"]')`, {timeout: 10000, label: 'the page after the new key'});
  });

  // ----- The key screen -----
  await check('without a key the page asks for one; a wrong key is refused in words; the right key opens the lists', async () => {
    await ev(`sessionStorage.clear(); true`);
    await browser.page('Page.navigate', {url: `${origin}/`});
    await waitFor(`document.querySelector('[data-key-form] input') && document.querySelectorAll('nav [data-run-key]').length === 0`, {label: 'the key field'});
    assert.ok(!(await ev(`document.body.innerText`)).includes('Alpha Project'), 'no data without a key');
    await click('#key-input');
    await browser.page('Input.insertText', {text: '0'.repeat(32)});
    await click('[data-key-go]');
    await waitFor(`document.querySelector('[data-key-form]') && /refused/.test(document.getElementById('key-error').textContent)`, {label: 'the refusal'});
    assert.equal(await ev(`sessionStorage.getItem('viewer-key')`), null, 'the refused key is not kept');
    await click('#key-input');
    await ev(`document.getElementById('key-input').value = ''; true`);
    await browser.page('Input.insertText', {text: `#key=${viewer.key}`});
    await pressEnter('#key-input');
    await waitFor(`document.querySelectorAll('nav [data-run-key]').length === 19`, {label: 'the run list after the right key'});
    assert.equal(await ev(`sessionStorage.getItem('viewer-key')`), viewer.key);
  });

  // ----- Hostile text that the page's old patterns could not survive -----
  await check('text that made the old patterns run for seconds or hours is shown in full in well under a second: a heading followed by 50,000 spaces, 100 KB of "[a](", long runs of * _ and backticks (F1)', async () => {
    const reports = path.join(fx.data, 'projects', fx.pid, 'analyses');
    const symbols = ['x' + '*'.repeat(60000), 'x' + '_'.repeat(60000), 'x' + '`'.repeat(60000), '*a'.repeat(6000), '_a'.repeat(6000), '`a'.repeat(6000), '``a`'.repeat(3000), 'a *b _c `d '.repeat(1000)].join('\n\n');
    assert.ok(Buffer.byteLength(symbols) < 262144, 'one part');
    writeFileSync(path.join(reports, 'run-untraced-done', 'report.md'), `# Title${' '.repeat(50000)}Page 3\n\nAfter the heading.\n`);
    writeFileSync(path.join(reports, 'run-untraced-pasted', 'report.md'), '[a]('.repeat(25000) + '\n');
    writeFileSync(path.join(reports, 'run-untraced-check', 'report.md'), `${symbols}\n`);
    const done = [];
    // Each document is opened after the previous one has been shown, so that a stuck page shows as a failure of the first.
    await openRun('run-untraced-done');
    const heading = await openCardTimed('[data-card-id="report"]', 'h3');
    const h = await ev(`({text: document.querySelector('[data-card-id="report"] .md h3').textContent, after: document.querySelector('[data-card-id="report"] .md').textContent.includes('After the heading.')})`);
    assert.ok(h.text.startsWith('Title') && h.text.trim().endsWith('Page 3') && h.after, `heading: ${json(h.text.slice(0, 20))}`);
    assert.deepEqual(await foreignElements('[data-card-id="report"] .md'), []);
    assert.ok(heading < 1000, `heading: ${heading} ms`);
    done.push(`heading ${heading} ms`);
    await openRun('run-untraced-pasted');
    const links = await openCardTimed('[data-card-id="report"]', 'p');
    const l = await ev(`document.querySelector('[data-card-id="report"] .md').textContent`);
    assert.ok(l.startsWith('[a]([a]([a](') && l.length >= 100000, `the text of the links stays text (${l.length} characters)`);
    assert.deepEqual(await foreignElements('[data-card-id="report"] .md'), []);
    assert.ok(links < 1000, `links: ${links} ms`);
    done.push(`links ${links} ms`);
    await openRun('run-untraced-check');
    const marks = await openCardTimed('[data-card-id="report"]', 'p');
    const m = await ev(`({paragraphs: document.querySelectorAll('[data-card-id="report"] .md p').length, text: document.querySelector('[data-card-id="report"] .md').textContent.length, scripts: document.querySelectorAll('[data-card-id="report"] .md script').length})`);
    assert.ok(m.paragraphs >= 8 && m.text > 100000 && m.scripts === 0, json(m));
    assert.deepEqual(await foreignElements('[data-card-id="report"] .md'), []);
    assert.ok(marks < 1000, `marks: ${marks} ms`);
    done.push(`* _ \` ${marks} ms`);
    timings.push(`heading plus 50,000 spaces ${heading} ms; 100 KB of "[a](" ${links} ms; runs of * _ and backticks (${Math.round(Buffer.byteLength(symbols) / 1000)} KB) ${marks} ms`);
    const before = await polls();
    await waitPolls(1);
    assert.ok((await polls()) > before, 'the page still polls');
    return done.join(', ');
  });

  // ----- Whole session -----
  await check('no request other than GET was sent, every request went to the viewer, and every request carried no key in its address', async () => {
    const wrong = events.requests.filter(r => r.method !== 'GET');
    assert.deepEqual(wrong, []);
    const foreign = events.requests.filter(r => new URL(r.url).origin !== origin && !r.url.startsWith('about:') && !r.url.startsWith('blob:'));
    assert.deepEqual(foreign, []);
    assert.ok(events.requests.every(r => usedKeys.every(key => !r.url.includes(key))));
    assert.ok(events.requests.length > 100, `${events.requests.length} requests were seen`);
    return `${events.requests.length} requests, all GET`;
  });

  await check('no page exception and no console error, and no request for a file that is not served', async () => {
    // Chrome asks every page for /favicon.ico; the viewer serves two routes and answers 404 in JSON. That one request is recorded, not counted.
    // Chrome also logs the deliberate 401 of the wrong key in the key-screen check, once, on /api/status.
    // Also expected: the 401 of the restart with another key (the next poll, the same step as the page's first refused request),
    // and the one 404 the test itself made the viewer's answer for a run in the check of the second click.
    const isRefusal = e => /\(Unauthorized\)/.test(e) && /\/api\//.test(e);
    const isMade404 = e => /\(Not Found\)/.test(e) && /\/api\/runs\/proj-alpha\/run-failed/.test(e);
    const refusals = events.consoleErrors.filter(isRefusal);
    assert.ok(refusals.length <= 3, `only the deliberate 401s (wrong key, restart, and requests in flight): ${json(refusals)}`);
    assert.ok(events.consoleErrors.filter(isMade404).length <= 1);
    const expected = e => /favicon\.ico/.test(e) || isRefusal(e) || isMade404(e);
    const real = events.consoleErrors.filter(e => !expected(e));
    for (const e of events.consoleErrors) if (expected(e)) events.ignored.push(e);
    assert.deepEqual(events.hookErrors, [], 'the test\'s own interception worked');
    assert.deepEqual(events.exceptions, []);
    assert.deepEqual(real, []);
    const other = events.requests.filter(r => !/\/api\//.test(r.url) && !/favicon\.ico$/.test(r.url) && new URL(r.url).pathname !== '/');
    assert.deepEqual(other, []);
    return events.ignored.length ? `${events.ignored.length} expected console message(s) set aside (favicon 404, the deliberate 401s, the 404 the test made)` : '';
  });
} catch (error) {
  failedSetup = error;
  results.push({name: 'test setup', ok: false, detail: String(error.stack ?? error).split('\n').slice(0, 8).join(' | ')});
  console.log(`FAIL setup: ${error.message}`);
} finally {
  try { await browser?.close(); } catch (error) { notes.push(`Closing Chrome: ${error.message}`); }
  await removeProfileRoot(profileRoot).catch(() => {});
  await viewer?.close().catch(() => {});
  if (fixtureBase) { try { removeFixture(fixtureBase); } catch (error) { notes.push(`Fixture removal: ${error.message}`); } }
}

// ---------- Result file ----------
const failed = results.filter(r => !r.ok);
const lines = [
  '# Process viewer browser test', '',
  `Command: \`CHROME=<Chrome binary> node tools/test-process-viewer-browser.mjs\``,
  `Result: **${failed.length ? 'FAIL' : 'PASS'}**, ${results.length - failed.length} of ${results.length} assertions passed.`, '',
  ...notes.map(n => `- ${n}`), ...timings.map(t => `- Time in Chrome, from the click to the text on the page, including the request: ${t}.`), '',
  '| Assertion | Result | Detail |', '| --- | --- | --- |',
  ...results.map(r => `| ${r.name.replace(/\|/g, '/')} | ${r.ok ? 'PASS' : 'FAIL'} | ${String(r.detail).replace(/\|/g, '/').replace(/\n/g, ' ')} |`), '',
  '## Screenshots', '', ...['run-1440', 'run-390', 'dossier-1440', 'dossier-390'].map(n => `- ${n}.png`), '',
  `## Requests`, '', `${events.requests.length} requests, methods: ${[...new Set(events.requests.map(r => r.method))].join(', ') || 'none'}.`
];
if (events.ignored.length) lines.push('', '## Set aside', '', ...events.ignored.map(e => `- ${e}`));
if (outDir) await fs.writeFile(path.join(outDir, 'result.md'), lines.join('\n') + '\n');
console.log(`${failed.length ? 'FAIL' : 'PASS'}: ${results.length - failed.length} of ${results.length} assertions. Output: ${outDir ? path.relative(root, outDir) : 'none'}`);
if (failedSetup) console.error(failedSetup.stack);
process.exit(failed.length ? 1 : 0);
