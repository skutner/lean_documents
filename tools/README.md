# Local report review workspace

The tools run on Linux and macOS. Install ALA once, then start the Node.js server in a terminal:

```sh
npm install
npm run start
```

The root `package.json` names three commands and one dependency. `npm run start` is `node tools/pilot.mjs`, `npm test` is `node tools/verify.mjs` and `npm run set-home` is `node tools/set-home.mjs` (see [Agent home](#agent-home)); either form works. `npm install` fetches ALA from GitHub at the pinned commit into `node_modules/advanced-language-agent`; see [Coding agents through ALA](#coding-agents-through-ala). The server itself uses only built-in Node.js modules, so it starts without the install, but then finds ALA only through `ALA_CLI`, a checkout beside or inside the repository, or `PATH`. npm prints `skipping integrity check for git dependency` during the install; the pin is the commit.

The server prints `Audit laboratory: http://127.0.0.1:8787`. Open that address; see [The address](#the-address). The current examples are in `examples/pilot/`; execution artifacts are retained under `tmp/readable-pilot/`. The server uses built-in Node.js modules. Real writing and auditing run through [ALA (Advanced Language Agent)](https://github.com/AssistOS-AI/AdvancedLanguageAgent) with an installed coding agent that is signed in, plus Python 3.12 or later and the repository-local Lean toolchain. See [Platforms and toolchain](#platforms-and-toolchain), [Coding agents through ALA](#coding-agents-through-ala) and [Agent home](#agent-home).

## Platforms and toolchain

Linux and macOS are supported. Coding agents always run through ALA inside a sandbox: Bubblewrap on Linux, Seatbelt (`/usr/bin/sandbox-exec`) on macOS. There is no unsandboxed fallback on either platform; if the sandbox cannot start, ALA refuses and the run fails with its message.

What a host needs for real audits:

| Need | How it is found |
| --- | --- |
| Node.js | The one that runs the tools. Node 24 or later is needed only to unpack the Lean archive with `setup-toolchain.mjs`. |
| Python 3.12 or later | Resolved as described below. The tools refuse an older interpreter. |
| Lean 4.24.0 | `tmp/toolchains/<folder>/bin/lean`, installed by `node tools/setup-toolchain.mjs`. |
| ALA | See [Coding agents through ALA](#coding-agents-through-ala). |
| A coding agent that is signed in | See [Agent home](#agent-home). |

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

The server binds to loopback, accepts only the Host `127.0.0.1:<port>`, checks Origin, and confines file access to the selected example, audit or drafting job. Uploads are limited to 90 MB per request. These variables change its behaviour: `PORT` the port; `ALA_CLI` the ALA location; `AUDIT_PYTHON` the interpreter. `AUDIT_AGENT_HOME` is no longer used; a server started with it set refuses to start (see [Agent home](#agent-home)). Agent binary variables such as `CODEX_BIN` or `CLAUDE_BIN` are read by ALA's own agent discovery. Explicit test executors are labelled simulated and use isolated directories.

## Coding agents through ALA

The server never starts Codex directly. Every verification and report draft runs `node <ALA>/bin/ala.mjs` with the coding agent chosen in **Agent settings** (header button): Codex, Claude Code, OpenCode, Pi or OMP, as reported installed by ALA. Codex with its own default model remains the default until another selection is saved. Every agent runs inside ALA's sandbox on both platforms (see [Platforms and toolchain](#platforms-and-toolchain)).

ALA is located in this order: the `ALA_CLI` environment variable, then `../AdvancedLanguageAgent` next to this repository, `./AdvancedLanguageAgent`, the copy that `npm install` puts in `node_modules/advanced-language-agent`, and an `ala` executable on `PATH`. The installed copy is the commit pinned in `package.json` and recorded in `package-lock.json`; to move to another ALA commit, change the commit there and run `npm install` again. A checkout beside or inside the repository and `ALA_CLI` come before it, so you can still run another ALA. Neither the page nor the API can set where ALA is. A location is accepted only when it contains `bin/ala.mjs`, `src/coding-agents/discovery.mjs` and a `package.json` named `advanced-language-agent`, and the pilot also needs `src/coding-agents/logins.mjs` in it. Except for `ALA_CLI`, a location inside this repository's `tmp/` or inside the folder set with `set-home` is refused, because the server imports ALA's modules without confinement and runs can write there. While ALA is missing, a banner says so and new verifications or drafts are refused with an explanation.

Agent settings lists installed agents and the models and effort levels ALA reports for each one. The agent list and a model list start ALA's catalog modules in a separate process with the same [agent home](#agent-home) a run would get, so they show what a run would find; neither sends a prompt. The server makes no login check for them: a model list without a login is answered with `Could not load the ALA catalog. Check the ALA installation and coding-agent login, then retry.` Only an installed agent and a model it reports can be saved; Agent default leaves the model or effort to the coding agent. The selection is stored in the readable `agent-settings.md` record beside the run folder (for the pilot, `tmp/readable-pilot/agent-settings.md`). Each run and draft records the agent, model and effort it was queued with, so changing the settings never alters queued or running work.

Each task is invoked as follows. The retained `instructions.md` is the task file and `final-message.md` receives ALA's final message.

```sh
node ALA/bin/ala.mjs --ca AGENT [--model MODEL [--effort EFFORT]] --config RUN/no-ala-config.json \
  --cwd RUN [--home FOLDER] --folder REPOSITORY --taskFile RUN/instructions.md --output RUN/final-message.md
```

- `--cwd` is the run or draft folder, the only repository folder the agent can write. Besides it the agent can write its [agent home](#agent-home) (your own agent state folder by default, or the folder set with `set-home`) and a private temporary folder (on macOS a private `TMPDIR`). `--folder` mounts this repository read-only at its own path, so the skill, helpers and Lean toolchain keep their absolute paths while originals, examples and other runs cannot be modified.
- `--home` is passed only after `npm run set-home -- <folder>`, and then on every start; without a set folder ALA uses your own agent folders. Each run and draft records which it got: its record has an `Agent home` field and its journal an `agent home` entry, holding the folder or `your own home (ALA started without --home)`.
- `--config` deliberately names a file that is never created. ALA treats a missing configuration as empty, so personal `~/.ala/config.json` defaults cannot change the recorded model.
- ALA shares the host network with its sandbox and enables the coding agent's web search (for Codex, `--search`). The audit and drafting prompts forbid web search and network resources and name the supplied documents as the only evidence; this instruction is not a network sandbox guarantee.
- `PLOINKY_*` variables are removed. The login is the agent's own, or the one in the set folder, or a credential variable of the server's environment that ALA passes to the agent. `ALA_EVENT_STREAM=1` lets the execution journal record the coding agent's messages, tool output and ALA diagnostics. ALA does not report individual command lines or exit codes, so the journal no longer lists them; finding documents and retained Lean logs remain the evidence for each check.
- Stop sends SIGTERM to the ALA process group. ALA cancels its coding agent and the run or draft is recorded as stopped. Any other non-zero exit, including ALA's interrupted code 130 without a requested stop, is recorded as failed with ALA's last error line.

Inside the sandbox ALA sets `PATH` itself (runtime folders, system folders and, on macOS, the Homebrew and `/usr/local` `bin` folders); the folders of your home are not on it. Every audit prompt therefore names the resolved absolute Python interpreter, which must be visible inside the sandbox: a system installation, or the one under `tmp/toolchains`. An interpreter or package under your home folder is not visible. ALA finds the agent program through `PATH` and the agent's binary variable (`CODEX_BIN`, `CLAUDE_BIN`, ...) of the server's environment; by default it also looks in the agent's install folder under your home (`~/.local/bin`; for OpenCode `~/.opencode/bin`), and with a set folder in the same place under `<folder>` instead, so an agent installed only under your own home must then be on `PATH` or named by its binary variable. `node node_modules/advanced-language-agent/bin/ala.mjs status [--home <folder>]` shows what a run started from the current shell would find.

## Agent home

The agent home is the folder a coding agent gets as its home inside ALA's sandbox. It holds the agent's login, settings and session history. There are two modes.

| Mode | How ALA is started | What it means |
| --- | --- | --- |
| Your own home (default) | Without `--home` | The agent uses the login and state you already have (`~/.codex`, `~/.claude` and the others). There is nothing to set up. A run can read and change that state. |
| A set folder | With `--home <folder>` | Every agent start gets that one folder. Your own agent folders are out of reach of a run. You sign in once inside the folder. |

In both modes all runs share one home, and nothing in it is ever removed by the pilot. See [Limits](#limits).

**The command.**

```sh
npm run set-home                 # show the setting
npm run set-home -- <folder>     # use <folder>, created with mode 0700 when missing
npm run set-home -- --reset      # back to your own home; the folder is left as it is
```

The setting is the file `~/.lean-documents/agent-home.json`, which holds `{"home": "<absolute real path>"}`. Only this command writes it; no route of the API can. The server reads it at every agent start, so a change applies to the next start without a restart. An absent file is the default. A file that cannot be used, or that names a folder that is gone, is an error: new work is refused and nothing is started, so a broken setting never falls back to your own home.

`<folder>` must be an absolute path. The command refuses, and changes nothing, when the folder:

- lies inside this repository, which every run can read;
- contains this repository, because a run could then change the tools the server runs;
- is or contains `~/.lean-documents` (your home folder, a folder above it, `/`), because a run could then rewrite the setting;
- is the setting file itself (in any letter case) or lies below it;
- exists and is not a folder;
- exists but is not owned by you, or gives group or others any access (`chmod 700 <folder>` first).

A link to an acceptable folder is accepted and its real path is saved. A Node, a Python or an `ALA_CLI` checkout that you place inside the folder is your own responsibility: runs can change it.

| Exit | Meaning |
| --- | --- |
| 0 | Done (shown, set or reset). |
| 2 | Usage error: an unknown option, more than one argument, or a path that is not absolute. |
| 3 | The folder is refused. |
| 1 | Anything else, including a setting file that cannot be read or written. |

**Signing in inside a set folder.** Run the agent's own sign-in with its state variable pointing into the folder. ALA derives the same paths from `--home`.

| Agent | Variable for the sign-in | Login ALA looks for |
| --- | --- | --- |
| Codex | `CODEX_HOME=<folder>/.codex` | `<folder>/.codex/auth.json` |
| Claude Code | `CLAUDE_CONFIG_DIR=<folder>/.claude` | `<folder>/.claude/.credentials.json` or `.claude.json` |
| OpenCode | `XDG_DATA_HOME=<folder>/.local/share XDG_CONFIG_HOME=<folder>/.config` | `<folder>/.local/share/opencode/auth.json`, `opencode.db` |
| Pi | `PI_CODING_AGENT_DIR=<folder>/.pi/agent` | `<folder>/.pi/agent/auth.json` |
| OMP | `OMP_DIR=<folder>/.omp` | `<folder>/.omp/agent/agent.db` |

For example:

```sh
mkdir -p <folder>/.codex && CODEX_HOME=<folder>/.codex codex login
node node_modules/advanced-language-agent/bin/ala.mjs status --home <folder>
```

The second command is ALA's own report of which agents it finds and which are signed in; without `--home` it reports your own home. The Agent home panel of Agent settings shows the same for the selected agent.

**When no login is found.** Before it queues or starts a verification or a draft, the server asks ALA's login module whether the selected agent has a login in the configured home. Without one it creates no run folder and answers with one of these texts, which the page also shows as a banner:

```text
No login found for <agent>. ALA looked for <files> and for a credential variable in the server's environment. Sign in with <agent>'s own command, then try again. A login kept only in the macOS Keychain is not visible inside ALA's sandbox. See tools/README.md, "Agent home".
No login found for <agent> in the agent home <folder>. ALA looked for <files> and for a credential variable in the server's environment. Sign in inside that folder: see tools/README.md, "Agent home".
```

A setting that cannot be used is answered with status 503 and `The agent home cannot be used, so no coding agent was started: <reason>`. A login check that fails or does not finish within 10 seconds is stopped and answered with status 503 and `The login check for <agent> failed or did not finish, so no coding agent was started. …` (see [Limits](#limits)). A server started with such a setting does not start: it prints `The pilot server did not start: <reason>` and exits 1; it does the same for a `PORT` that is not a TCP port.

**Claude Code on macOS, in both modes.** Its subscription login is kept in the Keychain, which the sandbox cannot read, so a Keychain-only login is unusable inside the sandbox. Put a token variable in the environment of the shell that starts the server instead: ALA passes `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`), `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` to Claude Code. The panel then reports `variable from the server's environment`.

**Credential variables still reach the sandbox.** ALA passes each agent's credential variables from the server's environment into the sandbox (for Codex `OPENAI_API_KEY`; the list for every agent is in ALA's agent descriptors). A variable counts as a login, the pilot does not change this, and a run can read it.

**Leftovers of the earlier design.** Earlier versions kept a separate login for the pilot and per-run homes under `~/.lean-documents/agent-home` (a folder; the setting file `agent-home.json` lies beside it). That folder is no longer read or written, and nothing removes it: delete it by hand when you no longer need it. To reuse the login kept there in a set folder, copy it: `cp -R ~/.lean-documents/agent-home/template/. <folder>/`. `AUDIT_AGENT_HOME` is no longer used; a server started with it set does not start and says so.

## Check the sandbox without a model

Before the first real audit on a host, run:

```sh
node tools/check-ala-sandbox.mjs                 # the mode that is configured
node tools/check-ala-sandbox.mjs --mode own      # your own home (the default)
node tools/check-ala-sandbox.mjs --mode folder   # a set folder
```

The check resolves ALA as the server does and starts it with the server's own arguments for the mode it checks. Its report begins with `Configured mode: … Mode checked: …` and a paragraph that says what that mode does and does not protect. Neither mode touches your real agent state or login:

- **`own`**: ALA is started without `--home`. `HOME` and every agent state variable point to a fixture home that holds only `.codex/auth.json` containing `{}`; the fixture stands in for your home.
- **`folder`**: ALA is started with `--home` on an empty folder in the host temporary folder, which stands in for the set folder, followed by a positive control.

`CODEX_BIN` points to a simulated Codex shell script; `OPENAI_*`, `ANTHROPIC_*`, `CLAUDE_CODE_OAUTH_TOKEN`, `PLOINKY_*` and the agent state variables are removed, and the check refuses to start ALA if a credential is left or ALA would run any other Codex than the script. No model, prompt or network request is involved. The check also puts a sentinel folder and its fixture folders in the host temporary folder, outside this repository and your home, and starts a helper process; they are removed on a normal exit, on an error and on SIGINT, SIGTERM or SIGHUP, and the report says whether your real home's list of entries changed while it ran. The readable report, probe results and run folder stay under `tmp/ala-sandbox-check/<timestamp>/`.

The simulation records one row per check. Required rows must pass; the last three are informational. Two rows exist in one mode only, so that no row claims a protection the mode does not give.

| Row | What it shows |
| --- | --- |
| `cwd`, `run_write` | It starts in the run folder and can write there, and the file reaches the host. |
| `repo_read` | It can read the repository (the audit skill). |
| `repo_write_denied`, `tmp_write_denied`, `sibling_write_denied` | It cannot write at the repository root, in `tmp/`, or next to its run folder; the host also checks that no refused file exists. |
| `outside_read_denied` | A sentinel file in the host temporary folder can be neither read nor listed. A control read in the run folder must work first, and the host must still read the sentinel afterwards, or a refusal proves nothing. |
| `home_private` | `HOME` is not your real home, and an entry of your real home that the host picked (never read; names that look like secrets or agent and runtime state are skipped) can neither be read nor appears in a listing of it. In `own` mode ALA's own `HOME` is the fixture; the row still checks only that the sandbox `HOME` is not your real home and that the picked entry is out of reach, and it does not measure what ALA would do with your real `HOME`. |
| `other_process_environment` | The host starts an outside Node process with a random marker variable. A reader inside (a `/usr/bin/perl` call of `kern.procargs2` on macOS, `/proc/<pid>/environ` on Linux) must be refused for that process while it sees its own. The host confirms with the same reader that the marker is visible; if the reader cannot run, the row fails. |
| `real_agent_state_denied` (`folder` only) | For each coding-agent state folder that exists in your real home, `$HOME/<same folder>` inside is absent or another folder, and the real folder cannot be listed and its login files cannot be read. It passes only together with a positive control, a second ALA run with a fixture home that holds `.codex`, which must pass and whose marker file written in `$HOME` must reach its `--home` folder. If no state folder exists in your home, the row's result is the control's. |
| `agent_state_shared` (`own` only) | Without a separate agent home the agent's state folder inside is the real one: `$HOME/.codex` inside is the fixture's `.codex` by device and inode, and a marker the probe writes there is found by the host in the fixture. It states and measures the exposure; it passes when the default mode would find your login, and fails with `not shared: …` otherwise. |
| `python`, `helper_import` | The resolved absolute Python is 3.12 or later inside the sandbox, and `<python> -B tools/document_audit.py --help` loads. Without a qualifying interpreter on the host both rows fail with the resolver's text. |
| `lean_version`, `lean_check` | The repository Lean toolchain reports version 4.24.0 and checks a small theorem written in the run folder. Without Lean installed both rows fail. |
| `extractors` (informational) | Which of `pdftotext`, `pdftoppm` and `tesseract` are on the sandbox `PATH`. |
| `home` (informational) | The sandbox `HOME`, temporary folder and `PATH`. |
| `launchd_residual` (informational) | On macOS a fixed statement: `Known residual on macOS: a confined process can start or unload existing launchd jobs in your login session; see ALA DS006.` On Linux `not applicable`. It is stated, not measured; the check creates no launchd job. |

Exit codes: 0 when ALA succeeded, every required row of the checked mode passed and the fixtures were removed; 1 when the check failed; 2 for a usage error, ALA not found or refused, ALA without its login module, or, when no `--mode` is given, an agent-home setting that cannot be read. ALA is stopped after 120 seconds.

## Run the checks

```sh
node tools/verify.mjs [--require-lean] [--only <suite>[,<suite>]] [--list]
```

`verify.mjs` runs every check that needs no model, coding agent or internet: it resolves Python and Lean, runs each suite and prints a table of passes, failures and skips per suite, with the logs in `tmp/verify/<timestamp>/<suite>.log`. `--list` prints the suites and their commands without running them; the suites are `toolchain`, `verify`, `remote-verify`, `agent-home`, `ala`, `markdown-server`, `report-generation` and `python-tests` (the only one that needs Python 3.12 or later). The agent-home suite covers the setting and `set-home`. It also compares its fixture's login-file lists with a real ALA: the one in `ALA_CLI`, else the installed copy; with neither, that test is skipped with a reason.

- A suite passes only if it exits 0 and prints a readable summary in which at least one test passed. A suite that cannot be started, exits non-zero, prints no summary, runs no test or skips every test fails.
- `NOT RUN` means a suite needed Python 3.12 or later and none was found. It is a failure, not a skip: the script says so, runs the suites that need no Python, marks the others `NOT RUN`, and exits 1. It never reports a pass for a suite it did not run.
- Skipped tests are listed with their reasons; they do not fail the run on their own. With `--require-lean` the run also fails when Lean is not installed, or when a test was skipped for a Lean reason (or for the missing Python 3.12), or when a skip has no reason the script could read.
- Exit codes: 0 every suite ran and passed; 1 a suite failed or did not run, or `--require-lean` found a problem; 2 usage error or an unknown suite name; 70 an unexpected error.

```sh
node tools/remote-verify.mjs --host <ssh-host> --target <absolute-remote-folder> [--ala <local-ala-checkout>] \
  [--env NAME=VALUE]... [--logs <local-folder>] -- <command> [argument...]
```

`remote-verify.mjs` ships a frozen copy of this working tree to a fresh folder on another host over `ssh`, runs the command there (for example `node tools/verify.mjs`) and fetches the logs. `ssh` runs with `BatchMode=yes`, so it cannot ask for a password.

- `node_modules` is ignored by Git, so the copy holds no installed ALA and nothing is installed on the other host: when the command needs ALA, pass `--ala` with a Git checkout of ALA (the installed copy is not one and cannot be shipped), set `ALA_CLI` with `--env`, or make the command run `npm install` first.
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

- The login. By default the agent runs with your own login and state folder. A run can read that login and any credential variable ALA passes to it, send it out over the shared network, and change or delete it. A sent login works on your account until it expires or you revoke it.
- By default a run can change your own agent state. ALA mounts the selected agent's state folder writable (`~/.codex` or `CODEX_HOME`, `~/.claude` or `CLAUDE_CONFIG_DIR`, the XDG folders of OpenCode, `~/.pi/agent`, `~/.omp`). A run can read your own session history there and change settings that your agent later reads and may execute outside any sandbox (hooks, MCP servers, notification commands). On Linux it can also retarget the link through which a standalone agent selects its program; ALA guards those links on macOS only (ALA DS006).
- Runs share one home in both modes. A run can read what earlier runs and your own sessions left in the agent's state and change what later runs read. In a set folder this includes a program at `<folder>/.local/bin/<agent>`, which ALA would start, inside the sandbox, for later runs. Pilot results are therefore not blind against earlier runs; a blind evaluation goes through `evalsuite/run_blind.py`.
- What `set-home` restores and what it does not. With a set folder your own agent folders are out of reach again (row `real_agent_state_denied` of the sandbox check). It does not isolate runs from each other, does not protect the login kept in the folder, and never removes anything: the folder and what runs wrote in it stay until you delete them.
- To check for a login, the server asks ALA's login module, in a separate process, to read the selected agent's login files in the agent home. A run can write those files. A file that blocks the read (a named pipe in place of the login file, for example) blocks only that process: it is stopped after 10 seconds and new work is refused with `The login check for <agent> failed or did not finish, …`. A run can also make the check wrong, for example by replacing the state folder with a link: a verification may then be queued, which removes the report's previous verification, and fail when ALA starts.
- The run folder stays under `tmp/` inside the repository, so it remains writable by its run and readable by later runs.
- On macOS a sandboxed process can ask launchd to start, stop or unload jobs that already exist in your login session. This is accepted and documented in ALA's `docs/specs/DS006-macos-seatbelt-sandbox.md`.
- On macOS do not move, replace or recreate a granted folder (the run folder, the repository, the agent home), or a folder above one, while a run is active: Seatbelt rules follow paths, so the rule would then point at whatever lies at the old path.
- On macOS ALA starts the agent in a process group of its own, so Stop and the server's exit signal ALA's group, not the agent's; a process that left its group can outlive a run, still confined.
- A server that is killed leaves an orphaned ALA, which keeps running until it ends and keeps using your login and agent state.
- The API has no sign-in, and a sandboxed run shares the host network. A run can therefore call the pilot's API as you do: read examples, runs and drafts, start or stop work, upload files and change the agent, model and effort in Agent settings. Two things it cannot change through the API: the agent home, which only `npm run set-home` writes to `~/.lean-documents/agent-home.json`, outside everything a run can write; and where ALA is, which comes from `ALA_CLI`, a checkout beside or inside the repository, `npm install`, or `PATH`.
- A page of another site open in your browser can make it send GET requests to the pilot's API. It cannot read the answers, but some of those requests start a process: a refreshed agent list and a model list, which starts the agent's catalog query in the sandbox with the same home as a run and sends no prompt (at most four such reads wait at a time); the Agent settings status, which starts the login check (one at a time per agent and home, at most every 10 seconds while it succeeds); and the inspection of a run whose id the page would have to know.

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
