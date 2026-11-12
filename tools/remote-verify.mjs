#!/usr/bin/env node
/** Ships a frozen candidate of this working tree to a fresh folder on another host, runs a command there and fetches
 * the logs. Nothing on the other host is deleted or overwritten, and nothing is installed or downloaded by this script.
 *
 *   node tools/remote-verify.mjs --host <ssh-host> --target <absolute-remote-folder> [--ala <local-ala-checkout>]
 *       [--env NAME=VALUE]... [--logs <local-folder>] -- <command> [argument...]
 *
 * The candidate is every file Git lists for this repository that is not ignored (tracked plus untracked), as it is on
 * disk, packed into one .tar.gz. Its SHA-256 is the frozen identity: it is recorded in remote-verify.json, checked on
 * the other host after the transfer, and kept there beside the target as <target>.candidate.tgz. If the tree changes
 * while the archive is built, the run stops. The ALA candidate (--ala) travels the same way and is unpacked as
 * ala-under-test beside the target; the command then sees ALA_CLI pointing at it.
 *
 * Refused before anything is written (exit 3): a target folder that already exists, an AdvancedLanguageAgent folder
 * beside the target (the repository would find it by itself), an existing ala-under-test beside the target, and an
 * existing file or link at <target>.candidate.tgz or <parent>/ala-under-test.candidate.tgz. The archives are written with
 * noclobber, so an existing file can never be replaced. A failed SHA-256 check removes only the archive this run wrote.
 * The command runs inside the target. Its output goes to <target>/tmp/remote-verify/command.log, its exit status to
 * exit-code in the same folder; that folder and <target>/tmp/verify are fetched into the local logs folder.
 *
 * Exit codes: 0 the command exited 0; 1 the command failed; 2 usage error; 3 refused target; 4 the archive changed
 * while it was built, or the transfer failed its SHA-256 check; 5 the transport (ssh) failed.
 * --transport local runs the same remote steps on this machine through /bin/sh; the tests use it. */
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawn, execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {pathToFileURL} from 'node:url';
import {ROOT} from './toolchain.mjs';

const execFileAsync = promisify(execFile);
const USAGE = 'Usage: node tools/remote-verify.mjs --host <ssh-host> --target <absolute-remote-folder> [--ala <local-ala-checkout>] [--env NAME=VALUE]... [--logs <local-folder>] -- <command> [argument...]';

class Exit extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

// POSIX single-quote quoting. The scripts below are always run as `sh -c <script>`, so only POSIX sh has to read them.
export const shq = value => `'${String(value).replace(/'/g, `'\\''`)}'`;

export function parseArguments(argv) {
  const args = {host: null, target: null, ala: null, env: [], logs: null, transport: 'ssh', command: []};
  const split = argv.indexOf('--');
  const options = split === -1 ? argv : argv.slice(0, split);
  args.command = split === -1 ? [] : argv.slice(split + 1);
  for (let i = 0; i < options.length; i++) {
    const name = options[i];
    if (['--host', '--target', '--ala', '--logs', '--transport', '--env'].includes(name)) {
      const value = options[++i];
      if (!value) throw new Exit(2, `${name} needs a value.\n${USAGE}`);
      if (name === '--env') {
        if (!/^[A-Za-z_][A-Za-z0-9_]*=/.test(value)) throw new Exit(2, `--env takes NAME=VALUE, not "${value}".\n${USAGE}`);
        args.env.push(value);
      } else args[name.slice(2)] = value;
    } else throw new Exit(2, `Unknown argument: ${name}\n${USAGE}`);
  }
  if (!['ssh', 'local'].includes(args.transport)) throw new Exit(2, `--transport takes ssh or local.\n${USAGE}`);
  if (args.transport === 'ssh' && !args.host) throw new Exit(2, `--host is required.\n${USAGE}`);
  if (!args.target) throw new Exit(2, `--target is required.\n${USAGE}`);
  if (!path.posix.isAbsolute(args.target) || args.target === '/' || /\/\.\.?(\/|$)/.test(args.target)) throw new Exit(2, `--target must be an absolute path without . or .. segments and not the root, got "${args.target}".`);
  args.target = args.target.replace(/\/+$/, '');
  if (!args.command.length) throw new Exit(2, `A command is required after --.\n${USAGE}`);
  return args;
}

/** Arguments for ssh: no prompts, and the script runs under sh whatever the login shell is. */
export function sshArguments(host, script) {
  return ['-o', 'BatchMode=yes', '--', host, `sh -c ${shq(script)}`];
}

export function createTransport(kind, host) {
  const [file, argumentsFor] = kind === 'local' ? ['/bin/sh', script => ['-c', script]] : ['ssh', script => sshArguments(host, script)];
  return {
    name: kind === 'local' ? 'local' : `ssh ${host}`,
    /** Runs a script on the other host. `stdinFile` is streamed to it. Resolves with the exit code and raw stdout. */
    exec(script, {stdinFile = null} = {}) {
      return new Promise((resolve, reject) => {
        const child = spawn(file, argumentsFor(script), {stdio: [stdinFile ? 'pipe' : 'ignore', 'pipe', 'pipe']});
        const out = [];
        let stderr = '';
        child.stdout.on('data', chunk => out.push(chunk));
        child.stderr.on('data', chunk => { stderr += chunk; });
        child.on('error', reject);
        child.on('close', code => resolve({code, stdout: Buffer.concat(out), stderr}));
        if (stdinFile) {
          const input = fs.createReadStream(stdinFile);
          input.on('error', reject);
          child.stdin.on('error', () => {});
          input.pipe(child.stdin);
        }
      });
    }
  };
}

// The candidate

async function listFiles(root) {
  const {stdout} = await execFileAsync('git', ['-C', root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], {encoding: 'buffer', maxBuffer: 256 * 1024 * 1024});
  const names = [...new Set(stdout.toString('utf8').split('\0').filter(Boolean))].sort();
  const present = [];
  for (const name of names) {
    // A file that Git still lists but that is gone from disk (a pending deletion) is not part of the candidate.
    try {
      const stat = await fsp.lstat(path.join(root, name));
      if (stat.isFile() || stat.isSymbolicLink()) present.push(name);
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
  }
  return present;
}

async function signature(root, names) {
  const hash = crypto.createHash('sha256');
  for (const name of names) {
    const stat = await fsp.lstat(path.join(root, name));
    hash.update(`${name}\0${stat.size}\0${stat.mtimeMs}\0${stat.mode}\n`);
  }
  return hash.digest('hex');
}

async function sha256File(file) {
  const hash = crypto.createHash('sha256');
  for await (const chunk of fs.createReadStream(file)) hash.update(chunk);
  return hash.digest('hex');
}

/** Packs the candidate and returns its identity. Stops when the tree changed while the archive was built. */
async function freeze(root, destination, label, hooks) {
  const names = await listFiles(root);
  if (!names.length) throw new Exit(4, `${label}: Git lists no file under ${root}. Is it a Git repository?`);
  const before = await signature(root, names);
  const list = `${destination}.list`;
  await fsp.writeFile(list, names.join('\0') + '\0');
  // bsdtar (macOS) would add extended-attribute headers that GNU tar on Linux warns about for every entry.
  const bsd = await execFileAsync('tar', ['--version']).then(r => /bsdtar/.test(r.stdout), () => false);
  await execFileAsync('tar', ['-czf', destination, ...(bsd ? ['--no-xattrs', '--no-mac-metadata'] : []), '-C', root, '--null', '-T', list], {env: {...process.env, COPYFILE_DISABLE: '1'}, maxBuffer: 16 * 1024 * 1024});
  if (hooks?.afterArchive) await hooks.afterArchive(label);
  const after = await signature(root, await listFiles(root));
  if (before !== after) throw new Exit(4, `${label}: the working tree changed while the archive was built; nothing was shipped. Freeze it only while no other writer is active.`);
  const stat = await fsp.stat(destination);
  const [head, status] = await Promise.all([
    execFileAsync('git', ['-C', root, 'rev-parse', 'HEAD']).then(r => r.stdout.trim(), () => null),
    execFileAsync('git', ['-C', root, 'status', '--short']).then(r => r.stdout, () => '')
  ]);
  return {
    root, archive: destination, sha256: await sha256File(destination), bytes: stat.size, files: names.length,
    fileListSha256: crypto.createHash('sha256').update(names.join('\n')).digest('hex'),
    head, statusSha256: crypto.createHash('sha256').update(status).digest('hex'), statusEntries: status.split('\n').filter(Boolean).length
  };
}

// The steps on the other host

const SHA = 'if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d" " -f1';

/** Claims the fresh folder with a plain mkdir (it fails when the folder exists) after the beside-checks. */
function claimScript({target, ala}) {
  const parent = path.posix.dirname(target);
  return `set -u
T=${shq(target)}; P=${shq(parent)}
if [ -e "$T" ] || [ -L "$T" ]; then echo "refused: the target folder $T already exists" >&2; exit 20; fi
if [ -e "$P/AdvancedLanguageAgent" ]; then echo "refused: $P/AdvancedLanguageAgent exists beside the target; the repository would find it as its ALA" >&2; exit 21; fi
${ala ? `if [ -e "$P/ala-under-test" ] || [ -L "$P/ala-under-test" ]; then echo "refused: $P/ala-under-test already exists" >&2; exit 22; fi` : ':'}
for A in "$T.candidate.tgz"${ala ? ' "$P/ala-under-test.candidate.tgz"' : ''}; do
  if [ -e "$A" ] || [ -L "$A" ]; then echo "refused: $A already exists (or is a link); it would be overwritten" >&2; exit 23; fi
done
mkdir -p "$P" && mkdir "$T" || exit 24
${ala ? `if ! mkdir "$P/ala-under-test"; then rmdir "$T"; echo "refused: could not create $P/ala-under-test" >&2; exit 22; fi` : ':'}`;
}

/** Receives the archive on stdin into <folder>.candidate.tgz, checks its SHA-256, then unpacks it into the claimed folder. */
function receiveScript({folder, sha256}) {
  return `set -u
F=${shq(folder)}; WANT=${shq(sha256)}
set -C
cat > "$F.candidate.tgz" || exit 31
set +C
sha() { ${SHA.replace(/"\$1"/g, '"$1"')}; }
GOT=$(sha "$F.candidate.tgz")
if [ "$GOT" != "$WANT" ]; then echo "the archive received for $F has SHA-256 $GOT, expected $WANT; nothing was unpacked" >&2; rm -f -- "$F.candidate.tgz"; rmdir "$F" 2>/dev/null; exit 30; fi
tar -xzf "$F.candidate.tgz" -C "$F" || exit 32`;
}

function runScript({target, command, env, ala}) {
  const parent = path.posix.dirname(target);
  const exports = [...env.map(item => { const i = item.indexOf('='); return `export ${item.slice(0, i)}=${shq(item.slice(i + 1))}`; }), ...(ala ? [`export ALA_CLI=${shq(parent + '/ala-under-test')}`] : [])].join('\n');
  return `set -u
T=${shq(target)}
mkdir -p "$T/tmp/remote-verify" || exit 40
${exports}
cd "$T" || exit 41
${command.map(shq).join(' ')} > "$T/tmp/remote-verify/command.log" 2>&1
echo $? > "$T/tmp/remote-verify/exit-code"
exit 0`;
}

const fetchScript = target => `set -u
cd ${shq(target)} || exit 50
set --
for d in tmp/remote-verify tmp/verify; do [ -e "$d" ] && set -- "$@" "$d"; done
tar -czf - "$@"`;

export async function main(argv, {transport = null, hooks = null, log = console.log} = {}) {
  const args = parseArguments(argv);
  const link = transport ?? createTransport(args.transport, args.host);
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const logs = path.resolve(args.logs ?? path.join(ROOT, 'tmp/remote-verify', stamp));
  await fsp.mkdir(logs, {recursive: true});
  const staging = await fsp.mkdtemp(path.join(logs, 'staging-'));
  const record = {host: args.transport === 'local' ? 'local' : args.host, transport: link.name, target: args.target, command: args.command, startedAt: new Date().toISOString(), candidate: null, ala: null, exitCode: null};
  const save = () => fsp.writeFile(path.join(logs, 'remote-verify.json'), JSON.stringify(record, null, 2) + '\n');
  const remote = async (script, options) => {
    let result;
    try {
      result = await link.exec(script, options);
    } catch (error) {
      throw new Exit(5, `Transport failed: ${error.message}`);
    }
    if (result.code === 255 && args.transport === 'ssh') throw new Exit(5, `ssh failed: ${result.stderr.trim()}`);
    return result;
  };

  try {
    log(`Freezing ${ROOT}`);
    record.candidate = await freeze(ROOT, path.join(staging, 'candidate.tar.gz'), 'candidate', hooks);
    log(`Candidate: ${record.candidate.files} files, ${record.candidate.bytes} bytes, SHA-256 ${record.candidate.sha256}`);
    log(`  HEAD ${record.candidate.head}, ${record.candidate.statusEntries} status entries (SHA-256 of the status text ${record.candidate.statusSha256})`);
    if (args.ala) {
      record.ala = await freeze(path.resolve(args.ala), path.join(staging, 'ala-under-test.tar.gz'), 'ALA candidate', hooks);
      log(`ALA candidate: ${record.ala.files} files, ${record.ala.bytes} bytes, SHA-256 ${record.ala.sha256}`);
    }
    await save();

    const parent = path.posix.dirname(args.target);
    const claim = await remote(claimScript({target: args.target, ala: Boolean(args.ala)}));
    if ([20, 21, 22, 23].includes(claim.code)) throw new Exit(3, claim.stderr.trim());
    if (claim.code !== 0) throw new Exit(5, `Could not prepare the target on the other host (exit ${claim.code}): ${claim.stderr.trim()}`);
    const ship = async (folder, frozen, label) => {
      const received = await remote(receiveScript({folder, sha256: frozen.sha256}), {stdinFile: frozen.archive});
      if (received.code === 30) throw new Exit(4, received.stderr.trim());
      if (received.code !== 0) throw new Exit(5, `Transfer of the ${label} failed (exit ${received.code}): ${received.stderr.trim()}`);
      log(`${label}: SHA-256 verified on the other host and unpacked into ${folder}`);
    };
    await ship(args.target, record.candidate, 'candidate');
    if (args.ala) await ship(parent + '/ala-under-test', record.ala, 'ALA candidate');

    log(`Running on ${link.name} in ${args.target}: ${args.command.join(' ')}`);
    const ran = await remote(runScript({target: args.target, command: args.command, env: args.env, ala: Boolean(args.ala)}));
    if (ran.code !== 0) throw new Exit(5, `Could not start the command (exit ${ran.code}): ${ran.stderr.trim()}`);
    const status = await remote(`cat ${shq(args.target + '/tmp/remote-verify/exit-code')}`);
    record.exitCode = Number.parseInt(status.stdout.toString('utf8').trim(), 10);
    if (!Number.isInteger(record.exitCode)) throw new Exit(5, 'The command left no exit code on the other host.');

    const fetched = await remote(fetchScript(args.target));
    if (fetched.code === 0) {
      const fetchedArchive = path.join(staging, 'logs.tar.gz');
      await fsp.writeFile(fetchedArchive, fetched.stdout);
      await fsp.mkdir(path.join(logs, 'fetched'), {recursive: true});
      await execFileAsync('tar', ['-xzf', fetchedArchive, '-C', path.join(logs, 'fetched')]);
      log(`Logs fetched into ${path.join(logs, 'fetched')}`);
    } else log(`Warning: the logs could not be fetched (exit ${fetched.code}): ${fetched.stderr.trim()}`);
    record.finishedAt = new Date().toISOString();
    await save();
    log(`Command exit code: ${record.exitCode}. Evidence: ${path.join(logs, 'remote-verify.json')}`);
    return record.exitCode === 0 ? 0 : 1;
  } catch (error) {
    record.error = error.message;
    record.finishedAt = new Date().toISOString();
    await save();
    throw error;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main(process.argv.slice(2)).then(
    code => { process.exitCode = code; },
    error => {
      console.error(error instanceof Exit ? error.message : error.stack || String(error));
      process.exitCode = error instanceof Exit ? error.code : 70;
    }
  );
}
