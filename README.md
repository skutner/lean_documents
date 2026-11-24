# Evidence-to-Proof Review

Check claims in a supplied report against its source documents. Explain what is supported, what is contradicted, and what cannot be established from the available evidence. Audit the report's reasoning separately from its conclusions; use actual Lean proofs where justified and available.

## Run

```sh
npm run start
```

`npm run start` is the only command. It starts Deep Proof from the sibling folder `../deep-proof-community-edition` with recording on, or reports on a Deep Proof that is already running; starts the read-only process viewer on Deep Proof's data folder and on the six edition 8 books; and opens `http://127.0.0.1:8790/` in the browser. The first start builds the six books once below `tmp/minute-books`. Ctrl-C stops the viewer and a Deep Proof that this command started. See [the process viewer section of the tools documentation](tools/README.md#deep-proof-process-viewer) for the overrides and the limits.

## Deep Proof process viewer

The process viewer shows what a Deep Proof analysis or report check did, step by step, during the run and afterwards. It attaches read-only to a Deep Proof data folder on the same machine, and to benchmark dossier folders where they are kept. Each run is shown in three parts: Inputs (what the agent was given), Process (a timeline of prompts, messages, command output, validation errors, the time limit and the published result) and Results (the report or draft, findings, registers and the kept work folder). It never writes to a Deep Proof folder, never starts a model run, and does not audit or score Deep Proof's output.

## Audit pilot (not part of the interface)

The readable-document pilot is no longer started by `npm run start`; `node tools/pilot.mjs` still starts it for its tests, and its code, examples and tests stay in the repository. `npm install` fetches its one dependency, [ALA](tools/README.md#coding-agents-through-ala), at the commit pinned in `package.json`; the tools themselves use only built-in Node.js modules.

The tools run on Linux and macOS. The pilot server prints the address it listens on, `http://127.0.0.1:8787` unless `PORT` is set; `http://localhost:8787` redirects there. The Node.js server displays the investment approval case in English and Romanian, plus acquisition financing, project funding, operating budgets, research funding and three literary-analysis dossiers, each in English and Romanian. All navigation is in the left tree: examples, input files and reports, findings, evidence and Lean sources. The selected document opens directly in the right pane, without popup readers or secondary navigation bars. Each report retains only its current verification. Finding nodes show a status icon and label; unfinished audits remain explicitly pending.

Choose English or Romanian when creating an example. You can upload a supplied report or generate a new report with the separate investment-report-writer skill, then audit it independently. Uploaded documents retain their original bytes; authored audit explanations and document templates use the selected language. Internal JSON preserves typed records, while semantic templates generate complete readable documents using prose, tables and descriptive links. The UI does not display internal data as recursive trees.

Each of the seven added bilingual dossiers has a correct supplied report and a report with deliberate failures, audited separately. Literary cases distinguish finite textual checks from uncertain interpretation: two chaptered stories about chronology and identity, and a poem about repeated openings. Prior example sets remain on disk outside this workspace. New pilot executions are retained under `tmp/readable-pilot/runs/`. See [server operation](tools/README.md) and [the readable-document contract](tools/document-audit-format.md).

## Skill and validation

Give a coding agent [the skill](skills/evidence-to-proof-review/SKILL.md), one report and its sources. A separate brief is optional. The pilot uses the [readable-document contract](tools/document-audit-format.md): typed internal records, a complete claim registry, individual execution snapshots and complete human document templates. The older Markdown-only profile remains available for historical reproductions. A journal records emitted actions, not private model reasoning or proof of semantic fidelity.

The helper supports text, DOCX, PDF and OCR with provenance and uncertainty. Python helpers are optional; the agent-native workflow preserves equivalent obligations, but the audit helper `tools/document_audit.py` needs Python 3.12 or later and refuses to run with an older one. Formal checks require a real Lean toolchain; once its pins are present, `node tools/setup-toolchain.mjs` installs the pinned Lean 4.24.0, and a pinned Python when the host has none, into `tmp/toolchains` (see [the toolchain and the current state of the hosts](tools/README.md#platforms-and-toolchain)). Runtime checks do not establish source authenticity or model adequacy.

```sh
npm test
```

`npm test`, which is `node tools/verify.mjs`, runs every model-free suite and prints pass, fail and skip counts; a suite that needs Python 3.12 or later and finds none is reported as `NOT RUN`, which counts as a failure (see [the checks](tools/README.md#run-the-checks)). `node tools/verify.mjs --list` names the suites, so a single one can be run by its own command, for example `node --test tools/test-ala.mjs` or `node tools/test-markdown-server.mjs`. The Python suite is `python3 -m unittest discover -s tests -v` with Python 3.12 or later.

The formal integration tests use the Lean toolchain in `tmp/toolchains` and are skipped, with a reason, while it is missing; set `AUDIT_TEST_LEAN` to a Lean executable to make the Python tests use another one. HTTP tests explicitly use a simulated ALA checkout and do not claim a semantic audit. Real interactive runs go through [ALA](tools/README.md#coding-agents-through-ala), always inside a sandbox (Bubblewrap on Linux, Seatbelt on macOS), with the coding agent chosen in Agent settings (Codex by default). By default the agent uses your own login and agent state; `npm run set-home -- <folder>` gives the agents a separate home (see [Agent home](tools/README.md#agent-home)). Runs retain the events ALA reports plus the actual check logs. Before the first real run on a host, check the sandbox without a model: `node tools/check-ala-sandbox.mjs` (see [the sandbox check](tools/README.md#check-the-sandbox-without-a-model)). Repository content and user conversation are in English.
