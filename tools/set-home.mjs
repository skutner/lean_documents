#!/usr/bin/env node
/** Shows, sets or resets the agent home of the pilot (tools/agent-home.mjs). The only writer of the setting.
 *
 *   npm run set-home                  show the setting
 *   npm run set-home -- <folder>      every coding agent starts with --home <folder> (created with mode 0700 when missing)
 *   npm run set-home -- --reset       back to your own home; the folder is left as it is
 *
 * Exit codes: 0 done; 2 usage; 3 folder refused; 1 anything else, including a setting that cannot be read or written.
 * AUDIT_AGENT_HOME is ignored here; the server refuses to start while it is set. */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {readAgentHome, writeAgentHome} from './agent-home.mjs';

const ROOT = fs.realpathSync.native(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'));
const USAGE = 'Usage: npm run set-home [-- <absolute folder> | -- --reset]';
const EXIT_BY_KIND = {usage: 2, refused: 3};

function show(setting, out) {
  out.write(setting.folder
    ? `Agent home: ${setting.folder}\nSetting file: ${setting.setting}\n`
    : `Agent home: your own home (default). ALA starts coding agents without --home, with your own login and agent state.\nSetting file: ${setting.setting} (absent)\n`);
}

export async function main(argv = process.argv.slice(2), {out = process.stdout, err = process.stderr, home = os.homedir(), root = ROOT} = {}) {
  try {
    // `npm run set-home --reset` without `--` gives the flag to npm: the script then only shows the setting.
    if (argv.length > 1 || (argv.length === 1 && argv[0].startsWith('-') && argv[0] !== '--reset')) {
      err.write(`${USAGE}\n`);
      return 2;
    }
    if (argv.length === 0) {
      show(readAgentHome({root, home}), out);
      return 0;
    }
    if (argv[0] === '--reset') {
      const setting = writeAgentHome(null, {root, home});
      show(setting, out);
      if (setting.previous) out.write(`${setting.previous} was left as it is.\n`);
      return 0;
    }
    const setting = writeAgentHome(argv[0], {root, home});
    show(setting, out);
    out.write(`Every coding agent now starts with --home ${setting.folder}. All runs share it and can read and change what it holds. No restart is needed. To sign in inside it, see tools/README.md, "Agent home".\n`);
    return 0;
  } catch (error) {
    err.write(`${error.reason || error.message}\n`);
    return EXIT_BY_KIND[error.kind] ?? 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(fs.realpathSync(process.argv[1])).href) process.exitCode = await main();
