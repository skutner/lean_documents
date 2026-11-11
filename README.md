# Evidence-to-Proof Review

Check claims in a supplied report against its source documents. Explain what is supported, what is contradicted, and what cannot be established from the available evidence. Audit the report's reasoning separately from its conclusions; use actual Lean proofs where justified and available.

## Current readable-document pilot

```sh
node tools/pilot.mjs
```

Open http://127.0.0.1:8787. The dependency-free Node.js server displays the investment approval case in English and Romanian, plus acquisition financing, project funding, operating budgets, research funding and three literary-analysis dossiers, each in English and Romanian. All navigation is in the left tree: examples, input files and reports, findings, evidence and Lean sources. The selected document opens directly in the right pane, without popup readers or secondary navigation bars. Each report retains only its current verification. Finding nodes show a status icon and label; unfinished audits remain explicitly pending.

Choose English or Romanian when creating an example. You can upload a supplied report or generate a new report with the separate investment-report-writer skill, then audit it independently. Uploaded documents retain their original bytes; authored audit explanations and document templates use the selected language. Internal JSON preserves typed records, while semantic templates generate complete readable documents using prose, tables and descriptive links. The UI does not display internal data as recursive trees.

Each of the seven added bilingual dossiers has a correct supplied report and a report with deliberate failures, audited separately. Literary cases distinguish finite textual checks from uncertain interpretation: two chaptered stories about chronology and identity, and a poem about repeated openings. Prior example sets remain on disk outside this workspace. New pilot executions are retained under `tmp/readable-pilot/runs/`. See [server operation](tools/README.md) and [the readable-document contract](tools/document-audit-format.md).

## Skill and validation

Give a coding agent [the skill](skills/evidence-to-proof-review/SKILL.md), one report and its sources. A separate brief is optional. The pilot uses the [readable-document contract](tools/document-audit-format.md): typed internal records, a complete claim registry, individual execution snapshots and complete human document templates. The older Markdown-only profile remains available for historical reproductions. A journal records emitted actions, not private model reasoning or proof of semantic fidelity.

The helper supports text, DOCX, PDF and OCR with provenance and uncertainty. Python helpers are optional; the agent-native workflow preserves equivalent obligations. Formal checks require a real Lean toolchain. Runtime checks do not establish source authenticity or model adequacy.

```sh
node tools/test-markdown-server.mjs
python3 -m unittest discover -s tests -v
```

Set `AUDIT_TEST_LEAN` to a Lean executable to enable formal integration tests. HTTP tests explicitly use a simulated child process and do not claim a semantic audit. Real interactive runs retain their actual Codex events and check logs. Repository content and user conversation are in English.
