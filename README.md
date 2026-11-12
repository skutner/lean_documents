# Evidence-to-Proof Review

Check claims in a supplied report against its source documents. Explain what is supported, what is contradicted, and what cannot be established from the available evidence. Audit the report's reasoning separately from its conclusions; use actual Lean proofs where justified and available.

## Current readable-document pilot

```sh
npm run start
```

This runs `node tools/pilot.mjs`; there is nothing to install first, because the tools use only built-in Node.js modules.

The tools run on Linux and macOS. Open the address the server prints: `http://127.0.0.1:8787` unless `PORT` is set; `localhost` does not work. The dependency-free Node.js server displays the investment approval case in English and Romanian, plus acquisition financing, project funding, operating budgets, research funding and three literary-analysis dossiers, each in English and Romanian. All navigation is in the left tree: examples, input files and reports, findings, evidence and Lean sources. The selected document opens directly in the right pane, without popup readers or secondary navigation bars. Each report retains only its current verification. Finding nodes show a status icon and label; unfinished audits remain explicitly pending.

Choose English or Romanian when creating an example. You can upload a supplied report or generate a new report with the separate investment-report-writer skill, then audit it independently. Uploaded documents retain their original bytes; authored audit explanations and document templates use the selected language. Internal JSON preserves typed records, while semantic templates generate complete readable documents using prose, tables and descriptive links. The UI does not display internal data as recursive trees.

Each of the seven added bilingual dossiers has a correct supplied report and a report with deliberate failures, audited separately. Literary cases distinguish finite textual checks from uncertain interpretation: two chaptered stories about chronology and identity, and a poem about repeated openings. Prior example sets remain on disk outside this workspace. New pilot executions are retained under `tmp/readable-pilot/runs/`. See [server operation](tools/README.md) and [the readable-document contract](tools/document-audit-format.md).

## Skill and validation

Give a coding agent [the skill](skills/evidence-to-proof-review/SKILL.md), one report and its sources. A separate brief is optional. The pilot uses the [readable-document contract](tools/document-audit-format.md): typed internal records, a complete claim registry, individual execution snapshots and complete human document templates. The older Markdown-only profile remains available for historical reproductions. A journal records emitted actions, not private model reasoning or proof of semantic fidelity.

The helper supports text, DOCX, PDF and OCR with provenance and uncertainty. Python helpers are optional; the agent-native workflow preserves equivalent obligations, but the audit helper `tools/document_audit.py` needs Python 3.12 or later and refuses to run with an older one. Formal checks require a real Lean toolchain; once its pins are present, `node tools/setup-toolchain.mjs` installs the pinned Lean 4.24.0, and a pinned Python when the host has none, into `tmp/toolchains` (see [the toolchain and the current state of the hosts](tools/README.md#platforms-and-toolchain)). Runtime checks do not establish source authenticity or model adequacy.

```sh
npm test
```

`npm test`, which is `node tools/verify.mjs`, runs every model-free suite and prints pass, fail and skip counts; a suite that needs Python 3.12 or later and finds none is reported as `NOT RUN`, which counts as a failure (see [the checks](tools/README.md#run-the-checks)). `node tools/verify.mjs --list` names the suites, so a single one can be run by its own command, for example `node --test tools/test-ala.mjs` or `node tools/test-markdown-server.mjs`. The Python suite is `python3 -m unittest discover -s tests -v` with Python 3.12 or later.

The formal integration tests use the Lean toolchain in `tmp/toolchains` and are skipped, with a reason, while it is missing; set `AUDIT_TEST_LEAN` to a Lean executable to make the Python tests use another one. HTTP tests explicitly use a simulated ALA checkout and do not claim a semantic audit. Real interactive runs go through [ALA](tools/README.md#coding-agents-through-ala), always inside a sandbox (Bubblewrap on Linux, Seatbelt on macOS), with the coding agent chosen in Agent settings (Codex by default) and a login made for the pilot in a [dedicated agent home](tools/README.md#dedicated-agent-home); they retain the events ALA reports plus the actual check logs. Before the first real run on a host, check the sandbox without a model: `ALA_CLI=<ALA checkout> node tools/check-ala-sandbox.mjs` (see [the sandbox check](tools/README.md#check-the-sandbox-without-a-model)). Repository content and user conversation are in English.
