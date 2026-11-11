# Repository instructions

- Write all repository content in English, including documentation, examples, reports, code comments, and skill instructions.
- Communicate with the user in English.
- The product audits findings in supplied reports against supplied documents. It does not generate the original substantive report. Its output is an evidence-backed claim audit with formal proofs or refutations where justified.
- Example dossiers must contain both a correct report and a report with deliberate errors. Auditors receive the report under review and the source documents; only the evaluator's expected outcomes are withheld during blind evaluation.
- Every audit result must show the exact claim, verification problem, source evidence, applicable rules, assumptions, reasoning, and limits. Refutations require an explicit explanation of why the claim fails. Report missing or insufficient arguments as valuable support-gap findings without treating them as proof that the underlying claim is false.
- The user approved implementation and validation on 4 November 2026. Implement the skill and validate all five example dossiers, auditing both report variants separately. Preserve actual proof logs and distinguish executed validation from design expectations.
- Use subagents with minimal task-specific context for example preparation and independent review. Do not provide evaluator-only expected outcomes to agents conducting blind audits.
- Keep per-run working directories under the repository's `tmp/` directory, which is ignored by Git. Retain them so the user can inspect progress and artifacts; do not automatically delete them. Use a distinct directory for each audit and reproduction run.
- The user also authorized all remaining planned extensions: native PDF/DOCX/OCR ingestion, coordinate provenance and uncertainty, disk-backed indexing, bounded/resumable batches, dependency invalidation, and at least five additional longer and more sophisticated dossiers with actual validation.

- A separate review brief is optional. Infer and disclose verification criteria from each report finding and source rules. Audit report premises/citations/inferences separately from the conclusion; enforce v2 claim registration for new runs and explicitly label legacy bundles.
