# Local report review workspace

The tools run on Linux and macOS. Start the dependency-free Node.js server in a terminal:

```sh
npm run start
```

The root `package.json` only names two commands and has no dependencies, so there is no `npm install` step: `npm run start` is `node tools/pilot.mjs` and `npm test` is `node tools/verify.mjs`. Either form works.

The server prints `Audit laboratory: http://127.0.0.1:8787`. Open that address; see [The address](#the-address). The current examples are in `examples/pilot/`; execution artifacts are retained under `tmp/readable-pilot/`. The server uses built-in Node.js modules. Real writing and auditing run through [ALA (Advanced Language Agent)](https://github.com/AssistOS-AI/AdvancedLanguageAgent) with an installed coding agent that has a pilot login, plus Python 3.12 or later and the repository-local Lean toolchain. See [Platforms and toolchain](#platforms-and-toolchain), [Coding agents through ALA](#coding-agents-through-ala) and [Dedicated agent home](#dedicated-agent-home).

## Platforms and toolchain

Linux and macOS are supported. Coding agents always run through ALA inside a sandbox: Bubblewrap on Linux, Seatbelt (`/usr/bin/sandbox-exec`) on macOS. There is no unsandboxed fallback on either platform; if the sandbox cannot start, ALA refuses and the run fails with its message.

What a host needs for real audits:

| Need | How it is found |
| --- | --- |
| Node.js | The one that runs the tools. Node 24 or later is needed only to unpack the Lean archive with `setup-toolchain.mjs`. |
| Python 3.12 or later | Resolved as described below. The tools refuse an older interpreter. |
| Lean 4.24.0 | `tmp/toolchains/<folder>/bin/lean`, installed by `node tools/setup-toolchain.mjs`. |
| ALA | See [Coding agents through ALA](#coding-agents-through-ala). |
| A coding agent with a pilot login | See [Dedicated agent home](#dedicated-agent-home). |

Optional `pdftotext`, `pdftoppm` and `tesseract` extractors must be installed system-wide to be visible inside the sandbox.

### Current state of the hosts

Not done yet: `tools/toolchain.json` carries no SHA-256 pins (every `sha256` is `null`, and the Python entries still hold `<PATCH>` and `<RELEASE>` placeholders), so `setup-toolchain.mjs` refuses to download anything and nothing has been downloaded. Lean is therefore installed on no host by this repository yet. On a host without Lean, the Lean tests are skipped and the `lean_version` and `lean_check` rows of the sandbox check fail until Lean is installed. The macOS development host has only the system Python 3.9, so it cannot run the Python suites or the `python` and `helper_import` rows of the sandbox check until the setup script has installed Python. No live agent run on macOS is documented here. The rest of this file describes the finished state, with pins present.

### The pinned toolchain

`tools/toolchain.json` pins, for each of `linux-x64`, `linux-arm64`, `darwin-x64` and `darwin-arm64`, a Lean 4.24.0 archive (`.tar.zst`) and a standalone CPython 3.12 archive (`.tar.gz`, the `install_only` builds of python-build-standalone), each with its asset name, URL, folder name and SHA-256. `tools/toolchain.mjs` and `tools/toolchain.py` read the same file.

```sh
node tools/setup-toolchain.mjs [--check] [--only lean|python] [--python] [--platform <key>] [--source <url-or-file>]
```

- Without options it installs Lean for this host into `tmp/toolchains/<folder>`, and a standalone Python only when none of the resolver's system locations (below) holds Python 3.12 or later. A Python found only on `PATH` does not count, because the sandbox may not see it. `--python` installs the pinned Python anyway. `--only lean` or `--only python` restricts the run to one component; `--python` cannot be combined with `--only lean`.
- `--check` installs nothing: it prints, per component, whether it is installed with a matching marker, and exits 1 if something is missing or unverified.
- `--platform <key>` selects another manifest entry than the host's. `--source` replaces the download address with an `http(s)` URL, a `file:` URL or a local path. A file, or a URL that ends in an asset name, is one asset; a folder or base URL holds the assets under their manifest names. Naming one asset while both components are pending is refused. The SHA-256 pin applies to every source.
- The archive is downloaded into a staging folder under `tmp/toolchains`, and its SHA-256 is computed over the downloaded bytes and compared with the pin before anything is unpacked. It is unpacked with Node's decompressor and `tar` (run with a fixed minimal environment), must contain exactly one top-level folder of the expected name, and is checked while still in staging: `lean --version` must report version 4.24.0, and the Python must report the pinned version and run `tools/document_audit.py --help`. Only then is the folder renamed into place and `tmp/toolchains/<folder>.installed.json` written.
- A null or malformed pin, or a placeholder in the manifest entry, is refused before anything is downloaded. A folder that already exists without a matching marker, or that came from a different pin, is never overwritten: the script names it and stops. An installed toolchain whose marker matches the manifest needs no network.

| Exit | Meaning |
| --- | --- |
| 0 | Installed and verified, or already installed (no network used). |
| 1 | `--check`, and something is missing or unverified. |
| 2 | Usage error, unsupported platform, a pin that is null, a placeholder or malformed, or a Node without built-in zstd (use Node 24 or later). |
| 3 | The download failed. |
| 4 | SHA-256 mismatch; nothing was installed. |
| 5 | Unpacking, the self-check or the installation failed, or an existing folder was found that the script will not overwrite. |
| 70 | An unexpected error. |

### Where Python and Lean are looked for

Python 3.12 or later is required. `node tools/verify.mjs`, the server and the sandbox check resolve one absolute interpreter, in this order; the first at version 3.12 or later wins:

1. `AUDIT_PYTHON`, an absolute path. It is authoritative: a relative path, or one that cannot be used, is an error and is never replaced by another candidate.
2. The interpreter installed by the setup script (`tmp/toolchains/python-<version>-<key>/bin/python3`), when its install marker matches the manifest.
3. The system locations in `tools/toolchain.json`. Linux: `/usr/bin/python3`, `/usr/local/bin/python3`. macOS: `/opt/homebrew/bin/python3`, `/usr/local/bin/python3`, `/Library/Frameworks/Python.framework/Versions/Current/bin/python3`, `/usr/bin/python3`.
4. `python3`, `python3.14`, `python3.13` and `python3.12` on `PATH`.

An interpreter under your home folder but outside this repository is used with a warning: it works on the host but is not visible inside the ALA sandbox, so the audit agent could not run it. If no interpreter qualifies, the tools stop with this text, and the server refuses new work with it:

```text
Python 3.12 or later is required (tools/document_audit.py). Found: <path version, ...>. Run "node tools/setup-toolchain.mjs" to install a pinned interpreter into tmp/toolchains, or set AUDIT_PYTHON to an absolute path. Other interpreters under your home folder are not visible inside the ALA sandbox.
```

`tools/document_audit.py` carries the same guard: run directly with an older Python it prints that text (naming the interpreter and its version) and exits 1; imported by an older Python it raises `ImportError`.

Lean is looked for only at `tmp/toolchains/<folder>/bin/lean`, where `<folder>` comes from the manifest for the host's platform key: `lean-4.24.0-linux`, `lean-4.24.0-linux_aarch64`, `lean-4.24.0-darwin` or `lean-4.24.0-darwin_aarch64`. A folder placed there by hand counts as installed for the server and `verify.mjs`, but the setup script will not overwrite it. The Lean toolchain is called by its absolute path and must be a real folder inside this repository, which is what the sandbox mounts. The server has no override for this location. The Python tests use the repository toolchain too; `AUDIT_TEST_LEAN` replaces it for those tests only, and neither the server, `verify.mjs` nor `--require-lean` looks at it.

## The address

Open `http://127.0.0.1:<port>`; the port is `PORT`, else 8787. Only that address works: the server answers a request for any other host name, `localhost` included, with `Use http://127.0.0.1:<port>`. The name `localhost` also reaches `[::1]`, where a sandboxed run may be listening.

The API has no sign-in. Any process on this machine that can reach the port can call it, including a sandboxed run, which shares the host network (see [Limits](#limits)). A page of another site open in your browser is only partly kept out: its POST requests are refused by the Origin check and it cannot read any answer, but its GET requests are served (see [Limits](#limits)).

`tools/validate-readable-examples.py` defaults to `--origin http://127.0.0.1:8787` and accepts only the literal host `127.0.0.1`. `tools/test-audit-workspace.mjs` reads the server on port 8787.

## Navigate the documents

The left tree contains examples. Each example contains its input files and its reports. Each report contains its findings; a finding can be expanded to its evidence, recorded work and Lean source. Selecting a node opens its content in the right pane. Normal source citations within documents remain clickable. Finding nodes carry an icon and an explicit semantic status: Supported, Refuted, Undetermined, Conflicting or Not applicable. Draft verdicts remain Pending until the audit finishes. Opening and closing a branch is independent of document selection, and automatic updates preserve branch state.

There are no popup readers, tabs, refresh controls or previous-verification histories. The display updates automatically. A new verification replaces the previous verification for that same supplied report. Distinct report documents remain separate report nodes.

## Work on an example

Choose English or Romanian when creating an example. The language is retained in its metadata and controls authored explanations and document templates. Uploaded originals are preserved byte-for-byte.

At the example level, add source documents or upload a supplied report. The red Stop control stops the example's active verification or report-writing processes and removes its queued work. Stopped work is not labelled verified.

Create report launches the separate [investment-report-writer skill](../skills/investment-report-writer/SKILL.md) through ALA and the selected coding agent. A grounded report uses the supplied sources and states unresolved evidence. The deliberate-errors mode produces a local test report with intentional mistakes, recorded separately in generation notes. No mode authorizes invented underlying evidence or source quotations.

A successfully drafted report is added as a new report node. It is not certified by its author. Select it and run Verify report to invoke the independent audit skill. The auditor receives only that report and its preserved source documents, never the report writer's generation notes or intended errors.

## Understand the records

Internal JSON stores typed claim, evidence, execution and integrity records. Dedicated templates generate complete human documents using prose, comparison tables and descriptive links. JSON is not displayed as a recursive tree. See the [readable document contract](document-audit-format.md).

Each finding retains its own source review, reasoning, formalization, executed checks and conclusion. Real Lean source, exact checked targets, compiler output and declared axioms are accessible from the tree and document citations. Formal results establish the encoded targets under the stated source assumptions; they do not authenticate documents or establish an unknown real-world approval.

Current audits live under `tmp/readable-pilot/runs/`. Report-writing jobs live under `tmp/readable-pilot/report-jobs/`; their `generation-notes.md` and execution records are kept separate from the source corpus. Example metadata is in `example.md`. Source digests are checked before a generated report is published, so an input change during drafting cannot silently publish a result against different documents.

The server binds to loopback, accepts only the Host `127.0.0.1:<port>`, checks Origin, and confines file access to the selected example, audit or drafting job. Uploads are limited to 90 MB per request. These variables change its behaviour: `PORT` the port; `ALA_CLI` the ALA location; `AUDIT_AGENT_HOME` the [agent-home base](#dedicated-agent-home); `AUDIT_PYTHON` the interpreter. Agent binary variables such as `CODEX_BIN` or `CLAUDE_BIN` are read by ALA's own agent discovery. Explicit test executors are labelled simulated and use isolated directories.

## Coding agents through ALA

The server never starts Codex directly. Every verification and report draft runs `node <ALA>/bin/ala.mjs` with the coding agent chosen in **Agent settings** (header button): Codex, Claude Code, OpenCode, Pi or OMP, as reported installed by ALA. Codex with its own default model remains the default until another selection is saved. Every agent runs inside ALA's sandbox on both platforms (see [Platforms and toolchain](#platforms-and-toolchain)).

ALA is located in this order: the `ALA_CLI` environment variable, then a location saved in Agent settings, then `../AdvancedLanguageAgent` next to this repository, `./AdvancedLanguageAgent`, and an `ala` executable on `PATH`. A location is accepted only when it contains `bin/ala.mjs`, `src/coding-agents/discovery.mjs` and a `package.json` named `advanced-language-agent`, and the pilot also needs `src/coding-agents/logins.mjs` in it. Except for `ALA_CLI`, a location inside this repository's `tmp/` or inside the agent-home base is refused, because the server imports ALA's modules without confinement and runs can write there. An invalid saved location is reported, never silently replaced. While ALA is missing, a banner links to Agent settings and new verifications or drafts are refused with an explanation.

Agent settings lists installed agents and the models and effort levels ALA reports for each one. The agent list uses a home that is never created and sends no prompt. A model list starts ALA's catalog modules in a separate process with a fresh [dedicated agent home](#dedicated-agent-home) of the selected agent, so it needs that agent's pilot login; it never sends a prompt either. Only an installed agent and a model it reports can be saved; Agent default leaves the model or effort to the coding agent. The selection is stored in the readable `agent-settings.md` record beside the run folder (for the pilot, `tmp/readable-pilot/agent-settings.md`). Each run and draft records the agent, model and effort it was queued with, so changing the settings never alters queued or running work.

Each task is invoked as follows. The retained `instructions.md` is the task file and `final-message.md` receives ALA's final message.

```sh
node ALA/bin/ala.mjs --ca AGENT [--model MODEL [--effort EFFORT]] --config RUN/no-ala-config.json \
  --cwd RUN --home HOME --folder REPOSITORY --taskFile RUN/instructions.md --output RUN/final-message.md
```

- `--cwd` is the run or draft folder, the only repository folder the agent can write. Besides it the agent has `--home`, a fresh agent home made for this one process and destroyed afterwards, and a private temporary folder (on macOS a private `TMPDIR`). `--folder` mounts this repository read-only at its own path, so the skill, helpers and Lean toolchain keep their absolute paths while originals, examples and other runs cannot be modified.
- `--home` is always passed; there is no call without it, so ALA never falls back to your own agent folders. See [Dedicated agent home](#dedicated-agent-home).
- `--config` deliberately names a file that is never created. ALA treats a missing configuration as empty, so personal `~/.ala/config.json` defaults cannot change the recorded model.
- ALA shares the host network with its sandbox and enables the coding agent's web search (for Codex, `--search`). The audit and drafting prompts forbid web search and network resources and name the supplied documents as the only evidence; this instruction is not a network sandbox guarantee.
- `PLOINKY_*` variables are removed. The login is the pilot login copied into the run's agent home, or a credential variable of the server's environment that ALA passes to the agent. `ALA_EVENT_STREAM=1` lets the execution journal record the coding agent's messages, tool output and ALA diagnostics. ALA does not report individual command lines or exit codes, so the journal no longer lists them; finding documents and retained Lean logs remain the evidence for each check.
- Stop sends SIGTERM to the ALA process group. ALA cancels its coding agent and the run or draft is recorded as stopped. Any other non-zero exit, including ALA's interrupted code 130 without a requested stop, is recorded as failed with ALA's last error line.

Inside the sandbox ALA sets `PATH` itself (runtime folders, system folders and, on macOS, the Homebrew and `/usr/local` `bin` folders); the folders of your home are not on it. Every audit prompt therefore names the resolved absolute Python interpreter, which must be visible inside the sandbox: a system installation, or the one under `tmp/toolchains`. An interpreter or package under your home folder is not visible. ALA finds the agent program through `PATH` and the agent's binary variable (`CODEX_BIN`, `CLAUDE_BIN`, ...) of the server's environment; under a dedicated home it no longer looks in `~/.local/bin` by itself, so an agent that is installed there must be named by one of those. `node tools/setup-agent-home.mjs status` shows what a run started from the current shell would find.

## Dedicated agent home

Why it exists: ALA's default gives the coding agent write access to your own agent state (`~/.codex`, `~/.claude` and the others), so a sandboxed run could change what your own agent later reads or executes there. The pilot therefore never uses those folders. Every coding agent process it starts (audit, report draft, model listing) gets `--home` with a fresh folder made for that one process, holding only a copy of a login made for the pilot.

**The base.** `~/.lean-documents/agent-home`, or the absolute path in `AUDIT_AGENT_HOME`; an explicit value is authoritative, and its parent folder must already exist. Every folder under it has mode 0700 and every file 0600. Layout:

| Folder or file | Content |
| --- | --- |
| `template/` | The login files ALA names for each agent, nothing else. Only `setup-agent-home.mjs login` writes it. |
| `homes/` | `home-<server pid>-<16 hex>` for each process in use (and `gone-...` while one is being removed), with a `home-<...>.owner` record beside it that holds the pids and process group of the ALA process. |
| `staging/` | `login-<pid>-<16 hex>` scratch homes of a sign-in. |
| `template.lock` | The pid of the process that is adopting a login. |

The base is created at the first `login` or at the first server start; for the default location `~/.lean-documents` is created as well. `status`, `logout` and `sweep` do not create the base.

**What is refused.** A relative `AUDIT_AGENT_HOME`, a base inside this repository (which every run can read), a base inside the Node installation that ALA makes readable to every run, and a base that is a link or not a real folder of yours with no access for group or others. The server then does not start: it prints `The pilot server did not start: <reason>` and exits 1; it does the same for a `PORT` that is not a TCP port. `status`, `login` and `logout` print `The dedicated agent home could not be prepared, so no coding agent was started: <reason>` and exit 4. `status`, `login` and `logout` also check the folders inside the base (the template, `homes/` and `staging/`), and refuse a template that holds anything but login files (below). The server does not check the template: a refused template does not stop the server, but new work is refused with that same text (status 503), and the page shows `The dedicated agent home cannot be used.` `sweep` refuses only a relative `AUDIT_AGENT_HOME`.

**The template.** It holds the login files ALA names for the agents, such as `.codex/auth.json` for Codex, and nothing else. Any other entry (another file, a link, a special file, a file over 16777216 bytes, an entry that is not yours or that others can access, a stray `.DS_Store`) makes every agent unavailable until you remove it by hand; the message names the entry. A `-shm` file beside an agent's `.db` login file is tolerated. The template is only ever written by `login`, from the sign-in's scratch home, never from your own agent folders, so a pilot run does not see your personal agent configuration. What a login file contains is the client's own business: for Claude Code, `.claude.json` is both a login file and its configuration file, and for OpenCode and OMP the whole database is the login.

**One fresh home per agent process.** For each process the server creates `homes/home-<server pid>-<16 hex>`, copies only the selected agent's login files from the template into it, starts ALA with it as `--home`, and after the ALA process has ended unlinks the copies and removes the home. Nothing a run wrote is ever copied back to the template, and the home's name is never reused. If the client rewrote its login file during the run (for example a refreshed token), the new file is discarded, the run's journal records `login_files_rewritten` in its `agent home removed` entry, Agent settings shows `login rewritten by a run`, and the next run starts again from the template: you may have to sign in again. A static credential (an API-key login or a token variable) is not affected. On macOS the removal runs in a child confined by Seatbelt to the folder being removed; if that child cannot run, the home is left and reported, because there is no unconfined fallback. At start-up, before each new home and on `sweep`, homes whose server has gone are removed, unless their recorded ALA process group is still running. At exit the server signals its open homes' groups, waits up to 5 seconds, removes the homes, and reports any it had to leave.

A server that queues work for an agent without a pilot login refuses it, creates no run folder, and answers with this text (also shown as a banner in the page):

```text
No pilot login for <agent>. The pilot never uses your own <agent> folder; it starts <agent> with a dedicated agent home under <base>. Sign in there once: node tools/setup-agent-home.mjs login <agent> -- <sign-in command>. Or start the server with a credential variable that ALA passes to <agent>. See tools/README.md, "Dedicated agent home".
```

**The command.** ALA is found as the server finds it (`ALA_CLI`, then a saved location, then automatic detection; a location inside `tmp/` or the base is refused unless it comes from `ALA_CLI`). `sweep` needs no ALA.

```sh
node tools/setup-agent-home.mjs status [--json]
node tools/setup-agent-home.mjs login <agent> -- <sign-in command> [argument...]
node tools/setup-agent-home.mjs logout <agent>
node tools/setup-agent-home.mjs sweep
```

- `status` prints the base, whether the template is usable, and for each agent ALA names whether a run would find it installed and whether it has a pilot login (a login file, or the names, never the values, of credential variables in the current environment), then the number of leftover homes. `--json` prints the same as JSON.
- `login <agent> -- <command>` runs your sign-in command, without a shell, with the terminal attached, in an empty scratch home (`staging/login-...`); `HOME` and the agent state variables point there. The command is the agent's own program and runs without a sandbox, as every sign-in does; the script hardcodes no agent's sign-in command. Afterwards it requires that ALA finds a login file for that agent in the scratch home, adopts only the login files ALA names (by write-then-rename, replacing that agent's earlier files and leaving other agents' alone), prints their names, never their content, and removes the scratch home. The template lock is held only while files are adopted (`logout` and the copy of a login into a new home take it briefly too).
- `logout <agent>` removes that agent's files from the template. The token itself stays valid until you revoke it with the provider.
- `sweep` removes leftover homes and scratch homes as described above and prints how many it removed and which it kept and why. It refuses the same base locations as the other commands (exit 4) and needs no ALA; a base that does not exist yet has nothing to sweep (exit 0).

| Exit | Meaning |
| --- | --- |
| 0 | Done. |
| 1 | An unexpected error. |
| 2 | Usage error, or an agent that ALA does not name. |
| 3 | ALA not found or refused, or it has no login module. |
| 4 | The base or the template is refused (`status` also exits 4 when the template is refused). |
| 5 | The sign-in command could not start or exited non-zero (including when a signal of its own ended it); the template is unchanged. |
| 6 | The sign-in ended, but no login file in the scratch home could be adopted (none exists, or one is a link, not a regular file, or too large); the template is unchanged. A login kept only in the macOS Keychain ends here. |
| 7 | The template is locked by a live process. |
| 128 + n | Signal n (SIGINT, SIGTERM or SIGHUP) interrupted the sign-in, after its scratch home was removed. The signal is passed on to the sign-in command. If it arrived after the login was already adopted, the message says `Interrupted by <signal> after the login was adopted.` and the template has changed; otherwise the template is unchanged. |

**Claude Code on macOS.** Its subscription login is kept in the Keychain, which the sandbox cannot read, so a Keychain-only login is unusable inside the sandbox: if a sign-in keeps its login only there, `login` exits 6 and adopts nothing. Put a token variable in the server's environment instead (ALA passes `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` to Claude Code); `status` then reports `variable from the server's environment`.

**Credential variables still reach the sandbox.** ALA passes each agent's credential variables from the server's environment into the sandbox (for Codex `OPENAI_API_KEY`; the list for every agent is in ALA's agent descriptors). A variable counts as a login, the pilot does not change this, and a run can read it.

## Check the sandbox without a model

Before the first real audit on a host, run:

```sh
ALA_CLI=<ALA checkout> node tools/check-ala-sandbox.mjs
ALA_CLI=<ALA checkout> node tools/check-ala-sandbox.mjs --negative-control
```

The check resolves ALA as the server does (`ALA_CLI`, then a saved location, then automatic detection), and starts it with the server's own arguments, including a dedicated `--home`. `CODEX_BIN` points to a simulated Codex shell script and `--home` to an empty folder in the host temporary folder; `OPENAI_*`, `ANTHROPIC_*`, `CLAUDE_CODE_OAUTH_TOKEN`, `PLOINKY_*` and the agent state variables are removed, and the check refuses to start ALA if a credential is left or ALA would run any other Codex than the script. No model, prompt or network request is involved. The check also puts a sentinel folder and a temporary agent-home base in the host temporary folder, outside this repository and your home, and starts a helper process; they are removed on a normal exit, on an error and on SIGINT, SIGTERM or SIGHUP, and the report says whether your real home's list of entries changed while it ran. The readable report, probe results and run folder stay under `tmp/ala-sandbox-check/<timestamp>/`.

The simulation records one row per check. Required rows must pass; the other three are informational.

| Row | What it shows |
| --- | --- |
| `cwd`, `run_write` | It starts in the run folder and can write there, and the file reaches the host. |
| `repo_read` | It can read the repository (the audit skill). |
| `repo_write_denied`, `tmp_write_denied`, `sibling_write_denied` | It cannot write at the repository root, in `tmp/`, or next to its run folder; the host also checks that no refused file exists. |
| `outside_read_denied` | A sentinel file in the host temporary folder can be neither read nor listed. A control read in the run folder must work first, and the host must still read the sentinel afterwards, or a refusal proves nothing. |
| `home_private` | `HOME` is not your real home, and an entry of your real home that the host picked (never read; names that look like secrets or agent and runtime state are skipped) can neither be read nor appears in a listing of it. |
| `other_process_environment` | The host starts an outside Node process with a random marker variable. A reader inside (a `/usr/bin/perl` call of `kern.procargs2` on macOS, `/proc/<pid>/environ` on Linux) must be refused for that process while it sees its own. The host confirms with the same reader that the marker is visible; if the reader cannot run, the row fails. |
| `real_agent_state_denied` | For each coding-agent state folder that exists in your real home, `$HOME/<same folder>` inside is absent or another folder, and the real folder cannot be listed and its login files cannot be read. It passes only together with a positive control, a second ALA run with a fixture home that holds `.codex`, which must pass and whose marker file written in `$HOME` must reach its `--home` folder. If no state folder exists in your home, the row's result is the control's. |
| `python`, `helper_import` | The resolved absolute Python is 3.12 or later inside the sandbox, and `<python> -B tools/document_audit.py --help` loads. Without a qualifying interpreter on the host both rows fail with the resolver's text. |
| `lean_version`, `lean_check` | The repository Lean toolchain reports version 4.24.0 and checks a small theorem written in the run folder. Without Lean installed both rows fail. |
| `extractors` (informational) | Which of `pdftotext`, `pdftoppm` and `tesseract` are on the sandbox `PATH`. |
| `home` (informational) | The sandbox `HOME`, temporary folder and `PATH`. |
| `launchd_residual` (informational) | On macOS a fixed statement: `Known residual on macOS: a confined process can start or unload existing launchd jobs in your login session; see ALA DS006.` On Linux `not applicable`. It is stated, not measured; the check creates no launchd job. |

Exit codes: 0 when ALA succeeded, every required row passed and the fixtures were removed; 1 when the check failed; 2 for a usage error, ALA not found or refused, or ALA without its login module. ALA is stopped after 120 seconds.

`--negative-control` runs ALA once without `--home`, with `HOME` and the agent state variables pointing at a fixture home that holds only `.codex/auth.json` containing `{}`. It exits 0 only when `real_agent_state_denied` fails with `is your real folder`, which shows that the row can detect a shared state folder. Its report is written next to the others.

## Run the checks

```sh
node tools/verify.mjs [--require-lean] [--only <suite>[,<suite>]] [--list]
```

`verify.mjs` runs every check that needs no model, coding agent or internet: it resolves Python and Lean, runs each suite and prints a table of passes, failures and skips per suite, with the logs in `tmp/verify/<timestamp>/<suite>.log`. `--list` prints the suites and their commands without running them; the suites are `toolchain`, `verify`, `remote-verify`, `agent-home`, `ala`, `markdown-server`, `report-generation` and `python-tests` (the only one that needs Python 3.12 or later). With `ALA_CLI` set, the agent-home suite also compares its fixture's login-file lists with the real ALA; without it that test is skipped with the reason `ALA_CLI is not set`.

- A suite passes only if it exits 0 and prints a readable summary in which at least one test passed. A suite that cannot be started, exits non-zero, prints no summary, runs no test or skips every test fails.
- `NOT RUN` means a suite needed Python 3.12 or later and none was found. It is a failure, not a skip: the script says so, runs the suites that need no Python, marks the others `NOT RUN`, and exits 1. It never reports a pass for a suite it did not run.
- Skipped tests are listed with their reasons; they do not fail the run on their own. With `--require-lean` the run also fails when Lean is not installed, or when a test was skipped for a Lean reason (or for the missing Python 3.12), or when a skip has no reason the script could read.
- Exit codes: 0 every suite ran and passed; 1 a suite failed or did not run, or `--require-lean` found a problem; 2 usage error or an unknown suite name; 70 an unexpected error.

```sh
node tools/remote-verify.mjs --host <ssh-host> --target <absolute-remote-folder> [--ala <local-ala-checkout>] \
  [--env NAME=VALUE]... [--logs <local-folder>] -- <command> [argument...]
```

`remote-verify.mjs` ships a frozen copy of this working tree to a fresh folder on another host over `ssh`, runs the command there (for example `node tools/verify.mjs`) and fetches the logs. `ssh` runs with `BatchMode=yes`, so it cannot ask for a password.

- The copy is every file Git lists for this repository that is not ignored, tracked or untracked, as it is on disk, in one `.tar.gz`. Its SHA-256 is recorded in `remote-verify.json`, checked on the other host after the transfer, and the archive stays there as `<target>.candidate.tgz`. If the tree changes while the archive is built, nothing is shipped. Ignored files are not shipped, so `tmp/toolchains` is not: a toolchain must already be installed there, because the script installs and downloads nothing.
- `--ala` ships a local ALA checkout the same way as `ala-under-test` beside the target, and the command sees `ALA_CLI` pointing at it. Each `--env` is exported for the command.
- It never overwrites: it refuses, before writing anything, when the target folder already exists, when `AdvancedLanguageAgent` already exists beside the target (the repository would find it by itself), when `ala-under-test` already exists beside the target, or when `<target>.candidate.tgz` or `<parent>/ala-under-test.candidate.tgz` exists as a file or link. A failed SHA-256 check removes only the archive this run wrote.
- The command runs inside the target. Its output goes to `<target>/tmp/remote-verify/command.log` and its exit status to `exit-code` in that folder. `tmp/remote-verify` and `tmp/verify` of the target are fetched into `<logs>/fetched`; the default `<logs>` is `tmp/remote-verify/<timestamp>` here. The target must be an absolute path without `.` or `..` segments.
- Exit codes: 0 the command exited 0; 1 it failed; 2 usage error; 3 refused target; 4 the archive changed while it was built, or the transfer failed its SHA-256 check; 5 the transport failed or the command could not be started; 70 an unexpected error.

## Validation

```sh
node tools/verify.mjs
node tools/test-audit-workspace.mjs investment-approval
node tools/test-audit-workspace.mjs investment-approval-ro
python3 tools/validate-readable-examples.py --output tmp/readable-pilot/example-validation.md
```

The Python suite needs Python 3.12 or later; on a host without one, `verify.mjs` reports it as `NOT RUN`. `verify.mjs` covers the server, ALA integration, agent-home, toolchain and Python suites; individual suites can be run by their commands from `--list`, for example `node --test tools/test-ala.mjs` or `node tools/test-markdown-server.mjs`. The two `test-audit-workspace.mjs` commands read an already running server on port 8787.

Server tests use controlled isolated inputs and an explicitly simulated ALA checkout; they never start a coding agent. Proof tests execute real Lean and are skipped, with a reason, when Lean or Python 3.12 is missing. Browser tests read the running application without creating examples or mutating audit documents. Their artifacts remain under `tmp/` for inspection; the browser profile lives in the host temporary folder and is removed afterwards. The read-only example validator checks the seven added dossiers in both languages, both current report variants, retained source snapshots, individual phases, proof targets and logs. It returns a failure while any required run is pending or incomplete and writes a readable summary; it does not substitute structural validity for semantic review.

## Limits

- The token. A compromised run can read the login in its own home and any credential variable ALA passes to it, and send it out over the shared network; it works on your account until it expires or you revoke it. A pilot login does not narrow that; it only keeps your own client's login undisturbed.
- The run folder stays under `tmp/` inside the repository, so it remains writable by its run and readable by later runs.
- On macOS a sandboxed process can ask launchd to start, stop or unload jobs that already exist in your login session. This is accepted and documented in ALA's `docs/specs/DS006-macos-seatbelt-sandbox.md`.
- On macOS do not move, replace or recreate a granted folder (the run folder, the repository, the agent home), or a folder above one, while a run is active: Seatbelt rules follow paths, so the rule would then point at whatever lies at the old path.
- On macOS ALA starts the agent in a process group of its own, so the group the server records and waits for does not cover the agent; the confined removal is what protects a home in that case. A process that left its group can outlive a run, still confined. A later sweep removes anything left.
- A server that is killed leaves the homes it had in use, with their login copies, under the private base until the next server start or `sweep`, and an orphaned ALA keeps running until it ends.
- The API has no sign-in, and a sandboxed run shares the host network. A run can therefore call the pilot's API as you do: read examples, runs and drafts, start or stop work, upload files and change Agent settings, the saved ALA location included. The server imports ALA's modules without confinement, and the only guard left on a saved location is the refusal of one inside `tmp/` or the agent-home base. A server, `setup-agent-home.mjs` or `check-ala-sandbox.mjs` started with `ALA_CLI` set ignores the saved location.
- A page of another site open in your browser can make it send GET requests to the pilot's API. It cannot read the answers, but some of those requests start a process: a refreshed agent list, a model list, which opens an agent home with a copy of the pilot login and sends no prompt, and the inspection of a run whose id the page would have to know. At most four such reads wait at a time.

## Current examples

| Example | Main verification problem |
| --- | --- |
| Investment approval (English and Romanian) | Valuation, allocation limit and missing clearance evidence. |
| Acquisition financing | Net debt, the leverage ceiling and lender approval authority. |
| Project funding release | Matching cash, milestone acceptance and board approval. |
| Annual IT budget | Recurring and one-off costs, VAT, annual allocation and delegated approval. |
| ThermalLoop research funding | Eligible costs, independent replication and an unknown ethics determination. |
| The Last Crossing | Elapsed time, inconsistent testimony and an unstated motive. |
| The Name on the Crate | Names versus people, incompatible locations and an unknown motive. |
| The House at Low Tide | Exact verse-line counts, repeated openings and unsupported universal reception. |

The seven added dossiers each have English and Romanian editions, with two separate supplied reports in each edition. A correct report can accurately identify a failed covenant or an evidence gap: “correct” describes its claims, not approval of the transaction. The auditor receives only the selected report and sources. Evaluator expectations and independent review notes remain outside the source corpus. Formal checks concern the supplied facts and declared reading conventions, not document authenticity, literary quality or actual reader psychology.
