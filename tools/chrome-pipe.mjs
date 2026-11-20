/** Starts headless Chrome for a regression test and speaks the Chrome DevTools protocol to it over a pipe.
 *
 * The protocol runs over descriptors 3 (commands to Chrome) and 4 (answers and events from it), never over a TCP port: a
 * debugging port listens on loopback without credentials, where a confined run could attach and drive the browser.
 * The browser binary is named by the environment variable CHROME (on macOS, for example, the path of the binary inside
 * Google Chrome.app); without it `google-chrome` is looked up on the path. The profile is a throwaway folder in the host
 * temporary folder and is removed by the caller with `removeProfileRoot`. Nothing here touches a user's own profile. */
import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';

export const PROFILE_PREFIX = 'process-viewer-browser-profile-';
export const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

/** The browser binary: CHROME, else `google-chrome`. */
export const chromeBinary = (env = process.env) => env.CHROME || 'google-chrome';

/** A fresh profile folder in the host temporary folder. */
export async function makeProfileRoot() {
  return fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), PROFILE_PREFIX)));
}

/** Removes a profile folder made by `makeProfileRoot`, and nothing else: only a folder of that name directly in the host temporary folder. */
export async function removeProfileRoot(profileRoot) {
  if (profileRoot && path.dirname(profileRoot) === await fs.realpath(os.tmpdir()) && path.basename(profileRoot).startsWith(PROFILE_PREFIX)) {
    await fs.rm(profileRoot, {recursive: true, force: true, maxRetries: 3});
  }
}

async function stopChrome(chrome) {
  if (chrome.pid === undefined || chrome.exitCode !== null || chrome.signalCode !== null) return;
  chrome.kill('SIGTERM');
  await new Promise(resolve => {
    if (chrome.exitCode !== null || chrome.signalCode !== null) return resolve();
    chrome.once('exit', resolve);
    setTimeout(resolve, 5000);
  });
  if (chrome.exitCode === null && chrome.signalCode === null) chrome.kill('SIGKILL');
}

/** Starts Chrome on a blank page. Returns the process, a `page(method, params)` call for the first page, a `browser(method,
 * params)` call for the browser itself, `on(listener)` for the events of the page, and `close()`. Fails, after stopping
 * Chrome, when it does not answer or when the profile holds a DevToolsActivePort file, which Chrome writes only for a TCP
 * debugging endpoint. */
export async function startChrome({profileRoot, binary = chromeBinary(), cwd = process.cwd(), width = 1440, height = 900} = {}) {
  const profile = path.join(profileRoot, 'profile');
  const args = [
    '--headless', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--disable-dev-shm-usage',
    // Chrome's own background traffic and prompts are switched off: the test uses no network but the viewer's loopback address.
    '--disable-background-networking', '--disable-component-update', '--disable-sync', '--disable-default-apps', '--disable-extensions',
    '--use-mock-keychain', '--password-store=basic', '--metrics-recording-only', '--no-service-autorun',
    `--window-size=${width},${height}`, '--remote-debugging-pipe', `--user-data-dir=${profile}`, `--disk-cache-dir=${path.join(profileRoot, 'cache')}`,
    ...(process.platform === 'linux' ? ['--no-sandbox'] : []), 'about:blank'
  ];
  const chrome = spawn(binary, args, {cwd, stdio: ['ignore', 'ignore', 'pipe', 'pipe', 'pipe']});
  let log = '';
  let buffer = '';
  let seq = 0;
  let ended = null;
  const pending = new Map();
  const listeners = new Set();
  const end = error => {
    ended ??= error;
    for (const p of pending.values()) {
      clearTimeout(p.timer);
      p.reject(ended);
    }
    pending.clear();
  };
  chrome.stderr.on('data', chunk => { log += chunk; });
  chrome.once('error', error => end(new Error(`Chrome could not start: ${error.message}`)));
  chrome.once('exit', (code, signal) => end(new Error(`Chrome exited (${signal || code}): ${log.slice(-4000)}`)));
  const commands = chrome.stdio[3];
  const answers = chrome.stdio[4];
  commands.on('error', error => end(new Error(`The debugging pipe to Chrome failed: ${error.message}`)));
  answers.on('error', () => {});
  answers.setEncoding('utf8');
  answers.on('data', chunk => {
    buffer += chunk;
    let at;
    while ((at = buffer.indexOf('\0')) >= 0) {
      const text = buffer.slice(0, at);
      buffer = buffer.slice(at + 1);
      let message;
      try {
        message = JSON.parse(text);
      } catch {
        end(new Error('Chrome sent an unreadable protocol message'));
        return;
      }
      if (message.id) {
        const p = pending.get(message.id);
        if (!p) continue;
        pending.delete(message.id);
        clearTimeout(p.timer);
        if (message.error) p.reject(new Error(JSON.stringify(message.error)));
        else p.resolve(message.result);
      } else for (const listener of listeners) listener(message);
    }
  });
  function send(method, params = {}, sessionId) {
    if (ended) return Promise.reject(ended);
    return new Promise((resolve, reject) => {
      const id = ++seq;
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`CDP timeout: ${method}`));
      }, 30000);
      pending.set(id, {resolve, reject, timer});
      commands.write(`${JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})})}\0`);
    });
  }
  const noPort = () => assert.equal(existsSync(path.join(profile, 'DevToolsActivePort')), false, 'Chrome must not open a TCP debugging endpoint: the profile holds DevToolsActivePort');
  try {
    let target;
    for (let i = 0; i < 100 && !target; i++) {
      target = (await send('Target.getTargets')).targetInfos.find(t => t.type === 'page');
      if (!target) await sleep(100);
    }
    assert(target, `Chrome opened no page: ${log}`);
    const {sessionId} = await send('Target.attachToTarget', {targetId: target.targetId, flatten: true});
    noPort();
    return {
      chrome, profile, noPort, log: () => log,
      browser: (method, params = {}) => send(method, params),
      page: (method, params = {}) => send(method, params, sessionId),
      /** Events of the page (those with its session id) and of the browser (those without any). */
      on(listener) {
        listeners.add(message => listener(message, message.sessionId === sessionId ? 'page' : 'browser'));
      },
      async close() {
        if (!ended) await Promise.race([send('Browser.close').catch(() => {}), sleep(3000)]);
        await stopChrome(chrome);
      }
    };
  } catch (error) {
    await stopChrome(chrome);
    throw error;
  }
}
