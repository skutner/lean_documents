#!/usr/bin/env node
/** Operator script for the pilot's dedicated agent home (tools/agent-home.mjs).
 *
 *   node tools/setup-agent-home.mjs status [--json]
 *   node tools/setup-agent-home.mjs login <agent> -- <sign-in command> [argument...]
 *   node tools/setup-agent-home.mjs logout <agent>
 *   node tools/setup-agent-home.mjs sweep
 *
 * `login` runs the given sign-in command, without a shell, in an empty scratch home, and adopts only the login files
 * ALA names for that agent into the template. It hardcodes no agent's sign-in command and prints file names, never
 * content. The base is AUDIT_AGENT_HOME, or ~/.lean-documents/agent-home.
 *
 * ALA is located as the server does, with two refusals, because this script imports ALA's login module unconfined:
 * a saved location counts only when it matches the confirmation record an authenticated server wrote, and an
 * automatically found ALA inside the repository's tmp/ or inside the base is refused. ALA_CLI is not restricted.
 * `sweep` needs no ALA, but refuses a base as every other command does.
 *
 * Exit codes: 0 done; 2 usage error, or an agent ALA does not name; 3 ALA not found, refused, or without the login
 * module; 4 base or template refused (also by logout and login); 5 the sign-in command could not start or exited
 * non-zero (template unchanged); 6 the sign-in ended but no login file in the scratch home can be adopted (template
 * unchanged); 7 the template is locked; 128 + n when signal n (SIGINT, SIGTERM or SIGHUP) interrupted a sign-in, after
 * its scratch home was removed. */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {agentHomeLocation, createAgentHomes, liesWithin, loadALALogins, prepareBase} from './agent-home.mjs';
import {ALA_LOCATION_UNCONFIRMED, savedLocationConfirmed} from './api-auth.mjs';
import {alaEnvironment, createSettingsStore, resolveALA} from './ala.mjs';
import {ROOT} from './toolchain.mjs';

const USAGE = `Usage:
  node tools/setup-agent-home.mjs status [--json]
  node tools/setup-agent-home.mjs login <agent> -- <sign-in command> [argument...]
  node tools/setup-agent-home.mjs logout <agent>
  node tools/setup-agent-home.mjs sweep`;

const EXIT_BY_KIND = {usage: 2, unknownAgent: 2, module: 3, unavailable: 4, locked: 7, signIn: 5, noFile: 6};
const SIGNALS = ['SIGINT', 'SIGTERM', 'SIGHUP'];

class Exit extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

function parse(argv) {
  const [command, ...rest] = argv;
  if (command === 'status' && (rest.length === 0 || (rest.length === 1 && rest[0] === '--json'))) return {command, json: rest.length === 1};
  if (command === 'sweep' && rest.length === 0) return {command};
  if (command === 'logout' && rest.length === 1) return {command, agent: rest[0]};
  if (command === 'login' && rest.length >= 3 && rest[1] === '--') return {command, agent: rest[0], signIn: rest.slice(2)};
  throw new Exit(2, USAGE);
}

/** Runs `resolve` with ALA_CLI and PATH of `env` in force: tools/ala.mjs reads both from process.env, and the
 * environment this script was given (main's `env`) must decide. Synchronous, and restored exactly afterwards. */
function withInjectedEnvironment(env, resolve) {
  if (env === process.env) return resolve();
  const names = ['ALA_CLI', 'PATH'];
  const saved = names.map(name => [name, Object.prototype.hasOwnProperty.call(process.env, name), process.env[name]]);
  const apply = (name, present, value) => {
    if (present) process.env[name] = value;
    else delete process.env[name];
  };
  for (const name of names) apply(name, typeof env[name] === 'string', env[name]);
  try {
    return resolve();
  } finally {
    for (const [name, present, value] of saved) apply(name, present, value);
  }
}

/** ALA for this script: the server's resolution plus the two refusals described above. resolveALA refuses a location
 * inside <root>/tmp or the base (every source but ALA_CLI) and then tries the next automatic candidate; this script
 * stops at the first refused one instead, with the location text, after the confirmation check for a saved location. */
function locateALA(root, base, env) {
  const ala = withInjectedEnvironment(env, () => resolveALA(root, createSettingsStore(path.join(root, 'tmp/readable-pilot/agent-settings.md')).read(), {refused: [base]}));
  const refusal = ala.checked.find(candidate => candidate.refusedInside);
  if (refusal) {
    if (refusal.source === 'configured' && !savedLocationConfirmed(base, ala.configured)) {
      throw new Exit(3, `The saved ALA location ${ala.configured} was ${ALA_LOCATION_UNCONFIRMED}, or start with ALA_CLI set.`);
    }
    throw new Exit(3, refusal.reason);
  }
  if (!ala.found) {
    throw new Exit(3, `ALA was not found. Checked: ${ala.checked.map(c => `${c.location} (${c.reason})`).join(', ')}. Set ALA_CLI, or save and confirm the ALA location in Agent settings.`);
  }
  if (ala.source === 'configured' && !savedLocationConfirmed(base, ala.configured)) {
    throw new Exit(3, `The saved ALA location ${ala.configured} was ${ALA_LOCATION_UNCONFIRMED}, or start with ALA_CLI set.`);
  }
  if (ala.source !== 'environment') {
    for (const refused of [path.join(root, 'tmp'), base]) {
      if (liesWithin(ala.root, refused)) throw new Exit(3, `This ALA location is not accepted: it lies inside ${refused}, where runs can write. Use a checkout outside it.`);
    }
  }
  return ala;
}

function loginLine(login) {
  if (login.source === 'file') return 'signed in for the pilot (login file)';
  if (login.source === 'environment') return `variable from the server's environment (${login.variables.join(', ')})`;
  return 'not signed in for the pilot';
}

async function installations(ala, homes) {
  try {
    const {discoverCodingAgents} = await import(pathToFileURL(path.join(ala.root, 'src/coding-agents/discovery.mjs')).href);
    const {homeEnvironment} = await loadALALogins(ala.entry);
    const records = await discoverCodingAgents({env: homeEnvironment(alaEnvironment(), homes.discoveryHome)});
    return Object.fromEntries(records.map(record => [record.name, record.available ? (record.binary || 'available') : null]));
  } catch {
    return null;
  }
}

/** While the sign-in runs, SIGINT, SIGTERM and SIGHUP do not end this script: the first one is passed to the sign-in
 * command (a terminal's Ctrl-C reaches it directly as well), the scratch home is removed, and the script exits with
 * 128 + the signal's number. */
async function signIn(homes, options, env, out) {
  const controller = new AbortController();
  let received = null;
  const handlers = SIGNALS.map(name => [name, () => {
    received ??= name;
    controller.abort(name);
  }]);
  for (const [name, handler] of handlers) process.on(name, handler);
  try {
    const {adopted} = await homes.login(options.agent, options.signIn, {stdio: 'inherit', env, signal: controller.signal});
    out.write(`Adopted ${adopted.join(', ')} into the pilot template of ${options.agent}.\n`);
  } finally {
    for (const [name, handler] of handlers) process.off(name, handler);
  }
  if (received) throw new Exit(128 + os.constants.signals[received], `Interrupted by ${received} after the login was adopted.`);
  return 0;
}

async function run(argv, {out, env, home, root, pid}) {
  const options = parse(argv);
  let location;
  try {
    location = agentHomeLocation({env, home});
  } catch (error) {
    throw new Exit(4, error.message);
  }
  if (options.command === 'sweep') {
    // The sweep needs no ALA: it never loads the login module. It applies the base checks of every other command
    // (relative, inside the repository or Node's installation, a link, not private: exit 4 with their texts), since a
    // base where runs can write must not be operated on. A base that does not exist yet has nothing to sweep.
    prepareBase({base: location.base, root, explicit: location.explicit});
    const homes = createAgentHomes({root, base: location.base, explicit: location.explicit, logins: () => Promise.reject(new Error('sweep loads no login module')), pid});
    const {removed, kept} = await homes.sweep();
    out.write(`Removed ${removed.length} leftover home${removed.length === 1 ? '' : 's'}.${kept.map(k => `\nKept ${k.name}: ${k.reason}.`).join('')}\n`);
    return 0;
  }
  const ala = locateALA(root, location.base, env);
  const homes = createAgentHomes({root, base: location.base, explicit: location.explicit, logins: () => loadALALogins(ala.entry), pid});
  await homes.agents();
  if ((options.command === 'login' || options.command === 'logout') && !(await homes.agents()).includes(options.agent)) {
    throw new Exit(2, `ALA does not name the coding agent "${options.agent}". Known: ${(await homes.agents()).join(', ')}.`);
  }

  if (options.command === 'status') {
    const status = await homes.status();
    const installed = await installations(ala, homes);
    if (options.json) {
      out.write(JSON.stringify({...status, installed}, null, 2) + '\n');
    } else {
      const lines = [`Dedicated agent home: ${status.base}`, `Template: ${status.ready ? 'usable' : `refused. ${status.error}`}`, ''];
      for (const [name, login] of Object.entries(status.agents)) {
        const binary = installed?.[name];
        lines.push(`${name}: ${installed ? (binary ? `installed (${binary})` : 'not installed for a run') : 'installation unknown'}; ${loginLine(login)}${login.rewritten ? `; login rewritten by a run at ${login.rewritten.at}` : ''}`);
      }
      lines.push('', `Leftover homes: ${status.leftovers.length}`);
      out.write(lines.join('\n') + '\n');
    }
    return status.ready ? 0 : 4;
  }
  if (options.command === 'logout') {
    const {removed} = await homes.logout(options.agent);
    out.write(removed.length
      ? `Removed ${removed.join(', ')} of ${options.agent} from the pilot template. The token itself stays valid until you revoke it with the provider.\n`
      : `No pilot login for ${options.agent} was stored.\n`);
    return 0;
  }
  return signIn(homes, options, env, out);
}

export async function main(argv = process.argv.slice(2), {out = process.stdout, err = process.stderr, env = process.env, home = os.homedir(), root = ROOT, pid = process.pid} = {}) {
  try {
    return await run(argv, {out, env, home, root, pid});
  } catch (error) {
    err.write(`${error.message}\n`);
    if (error instanceof Exit) return error.code;
    if (error.kind === 'interrupted' && os.constants.signals[error.signal]) return 128 + os.constants.signals[error.signal];
    return EXIT_BY_KIND[error.kind] ?? 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(fs.realpathSync(process.argv[1])).href) process.exitCode = await main();
