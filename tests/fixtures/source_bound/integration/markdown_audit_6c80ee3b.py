#!/usr/bin/env python3
"""Create and validate readable Markdown-only, individually traced claim audits."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit
import journal

STEPS = ('registration', 'evidence', 'analysis', 'verification', 'verdict')
HEADINGS = ('Exact report claim', 'Report anchor', 'Executive conclusion', 'How verified',
    'Executive limits', 'Verification criteria', 'Evidence', 'Rules', 'Assumptions',
    'Reasoning', 'Argument review', 'Support search', 'Formalization', 'Proofs', 'Limits',
    'Semantic state', 'Argument status', 'Formal status')


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def inside(root, value, exists=True):
    root = Path(root).resolve()
    path = (root / value).resolve(strict=exists)
    if not path.is_relative_to(root) or (exists and not path.is_file()):
        raise ValueError(f'Artifact must be a file inside the workspace: {value}')
    return path


def cell(value):
    return str(value).replace('|', '&#124;').replace('\n', ' ')


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |'] +
        ['| ' + ' | '.join(cell(v) for v in row) + ' |' for row in rows]) + '\n'


def rows(text):
    data = []
    for line in text.splitlines():
        if not line.startswith('|'):
            continue
        values = [part.strip().replace('&#124;', '|') for part in line.strip().strip('|').split('|')]
        if all(re.fullmatch(r':?-+:?', value) for value in values):
            continue
        data.append(values)
    return [dict(zip(data[0], row)) for row in data[1:] if len(row) == len(data[0])] if data else []


def sections(text):
    result = {}
    for match in re.finditer(r'^## ([^\n]+)\n(.*?)(?=^## |\Z)', text, re.M | re.S):
        title = match[1].strip()
        if title in result:
            raise ValueError(f'Duplicate heading: {title}')
        result[title] = match[2].strip()
    return result


def fields(text):
    return {match[1].strip(): match[2].strip().strip('`') for match in re.finditer(r'^- ([^:\n]+): (.*)$', text, re.M)}


def unquote(text):
    return '\n'.join(re.sub(r'^> ?', '', line) for line in text.splitlines()).strip()


def link_path(value):
    match = re.fullmatch(r'\[[^\]]*\]\((.*)\)', value)
    return match[1] if match else value.strip('`')


def no_json(root):
    bad = [p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.suffix.lower() in ('.json', '.jsonl')]
    if bad:
        raise ValueError('Markdown audit cannot contain JSON/JSONL files: ' + ', '.join(bad))


def prepare(args):
    root_base = Path(args.temp_root).resolve()
    sources = [Path(value).resolve() for value in args.sources]
    if any(path.is_dir() and root_base.is_relative_to(path) for path in sources):
        raise ValueError('Temporary root must be outside supplied source directories')
    text_extensions = {'.md', '.markdown', '.txt', '.text', '.csv'}
    native_report = Path(args.report).suffix.lower() not in text_extensions
    report_relative = Path('input/originals') / ('report' + Path(args.report).suffix.lower()) if native_report else Path('input/report.md')
    supplied = [(Path(args.report).resolve(), report_relative)]
    for index, source in enumerate(sources, 1):
        for path in sorted(source.rglob('*')) if source.is_dir() else [source]:
            if path.is_file():
                relative = path.relative_to(source) if source.is_dir() else Path(path.name)
                supplied.append((path, Path('input/sources') / str(index) / relative))
    brief = getattr(args, 'brief', None)
    if brief:
        brief_path = Path(brief).resolve()
        supplied.append((brief_path, Path('input') / ('brief' + brief_path.suffix.lower())))
    for path, _ in supplied:
        if path.suffix.lower() in ('.json', '.jsonl'):
            raise ValueError('The Markdown-native route does not copy JSON/JSONL inputs; supply readable source documents')
        if not path.is_file():
            raise ValueError(f'Missing supplied input: {path}')
    root_base.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='markdown-audit-', dir=root_base))
    for source, relative in supplied:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    extraction_rows = []
    segment_rows = []
    for index, (source, relative) in enumerate(supplied):
        if relative.suffix.lower() in text_extensions:
            continue
        from markdown_extraction import extract_markdown
        original = root / relative
        extraction_dir = root / 'extraction' / f'D{index:05d}'
        result = extract_markdown(original, extraction_dir,
            ocr_language=getattr(args, 'ocr_language', 'eng'), timeout=getattr(args, 'extract_timeout', 60),
            max_pages=getattr(args, 'max_pages', None))
        projection = root / 'input/report.md' if index == 0 else root / (relative.as_posix() + '.extracted.md')
        projection.write_text(result['text'], encoding='utf-8')
        projection_relative = projection.relative_to(root).as_posix()
        record = (extraction_dir / 'extraction.md').relative_to(root).as_posix()
        extraction_rows.append((relative.as_posix(), result['source_sha256'], projection_relative, digest(projection), record, result['status'], '; '.join(result['warnings']) or 'No converter warnings.'))
        for segment in result['segments']:
            segment_rows.append((relative.as_posix(), projection_relative, segment['anchor'], hashlib.sha256(segment['quote'].encode('utf-8')).hexdigest(), segment['method'], 'yes' if segment['uncertainty'] else 'no', '; '.join(segment['uncertainty']) or 'No segment uncertainty reported.', record))
    if extraction_rows:
        (root / 'input/extraction-index.md').write_text('# Native extraction provenance\n\n' + table(['Original', 'Original SHA256', 'Projection', 'Projection SHA256', 'Extraction record', 'Status', 'Warnings'], extraction_rows))
        (root / 'input/extraction-segments.md').write_text('# Native passage provenance and uncertainty\n\nCoordinates and tool outputs are retained in the linked readable extraction records.\n\n' + table(['Original', 'Projection', 'Anchor', 'Passage SHA256', 'Method', 'Review required', 'Uncertainty', 'Extraction record'], segment_rows))
    for directory in ('findings', 'models', 'trace'):
        (root / directory).mkdir()
    version = audit.validate_lean_version(args.lean_version)
    (root / 'audit-policy.md').write_text(f'# Audit policy\n\n- Profile: markdown_claim_traces_v1\n- Lean version: {version}\n- Report: input/report.md\n- Created: {now()}\n\nAudit the supplied report against supplied documents; never generate the original substantive report. All individual traces and summaries are required.\n')
    report = audit.paragraphs((root / 'input/report.md').read_text(encoding='utf-8'))
    (root / 'report-ledger.md').write_text('# Preserved report passages\n\n' + '\n'.join(f'## {anchor}\n\n' + '\n'.join('> ' + line for line in quote.splitlines()) + '\n' for anchor, quote in report.items()))
    (root / 'claims.md').write_text('# Claim registry\n\nClassify every report anchor. Replace pending rows with atomic audited/deferred claim IDs or a reasoned non_claim row.\n\n' + table(['Claim ID', 'Report anchor', 'Disposition', 'Reason'], [('-', anchor, 'pending', 'Awaiting individual registration.') for anchor in report]))
    (root / 'finding-template.md').write_text('# C-01\n\n' + ''.join(f'## {heading}\n\nREPLACE with the actual individual analysis.\n\n' for heading in HEADINGS))
    (root / 'ingestion.md').write_text('# Ingestion scope and limits\n\n' + ('Native extraction was executed with original hashes, tool logs, coordinates, coverage and uncertainty preserved. [Extraction inventory](input/extraction-index.md) and [passage provenance](input/extraction-segments.md) identify partial or blocked documents. Converter output is not source authenticity or guaranteed transcription fidelity.' if extraction_rows else 'All supplied inputs have text-compatible extensions. UTF-8 parsing and quote correspondence are checked when passages are cited; authenticity is not established.') + '\n')
    immutable = [p for p in (root / 'input').rglob('*') if p.is_file()] + [p for p in (root / 'extraction').rglob('*') if p.is_file()] + [root / 'audit-policy.md', root / 'report-ledger.md', root / 'ingestion.md']
    (root / 'manifest.md').write_text('# Immutable input manifest\n\nExact SHA-256 digests of preserved original bytes and intake records.\n\n' + table(['Path', 'SHA256'], [(path.relative_to(root).as_posix(), digest(path)) for path in immutable]))
    (root / 'phase-journal.md').write_text(f'# Phase journal\n\n## {now()} — Intake completed\n\nPreserved the supplied report and source bytes. Initialized a pending registry; no semantic analysis is claimed.\n\n[Input manifest](manifest.md) · [Report ledger](report-ledger.md)\n')
    return root


def snapshot_rows(root, values, document_directory):
    output = []
    for value in values:
        path = inside(root, value)
        if path.suffix.lower() in ('.json', '.jsonl'):
            raise ValueError('JSON/JSONL artifacts are forbidden in the Markdown audit route')
        ref = journal.artifact(root, path, capture=True)
        output.append((f"[{ref['path']}]({os.path.relpath(root / ref['path'], document_directory)})", ref['sha256'], f"[Historical bytes]({os.path.relpath(root / ref['snapshot'], document_directory)})"))
    return output


def trace(args):
    root = Path(args.workspace).resolve()
    no_json(root)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.claim_id):
        raise ValueError('Claim IDs must contain only letters, numbers, underscores and hyphens')
    if not args.output or (args.status == 'completed' and not args.input):
        raise ValueError('Individual steps require outputs; completed steps also require inputs')
    if not args.result.strip() or not args.description.strip():
        raise ValueError('Describe the actual action, result and limitations')
    directory = root / 'trace' / args.claim_id
    directory.mkdir(parents=True, exist_ok=True)
    number = len(list(directory.glob('*.md'))) + 1
    destination = directory / f'{number:04d}-{args.step}.md'
    if destination.exists():
        raise ValueError('Trace destination already exists; never overwrite history')
    content = f'# {args.title}\n\n- Claim ID: {args.claim_id}\n- Step: {args.step}\n- Status: {args.status}\n- Recorded: {now()}\n\n## Action\n\n{args.description}\n\n## Inputs\n\n'
    content += table(['Artifact', 'SHA256', 'Snapshot'], snapshot_rows(root, args.input, directory))
    content += '\n## Outputs\n\n' + table(['Artifact', 'SHA256', 'Snapshot'], snapshot_rows(root, args.output, directory))
    content += '\n## Command\n\n' + ('```sh\n' + args.command + '\n```' if args.command else 'No command executed; see the recorded documentary method.')
    content += '\n\n## Result and limits\n\n' + args.result + '\n'
    destination.write_text(content, encoding='utf-8')
    relative = destination.relative_to(root).as_posix()
    with (root / 'phase-journal.md').open('a', encoding='utf-8') as stream:
        stream.write(f'\n## {now()} — {args.claim_id}: {args.step} {args.status}\n\n[{args.title}]({relative})\n\n{args.result}\n')
    return destination


def validate_summary(ident, heading, text):
    """Keep executive summaries human-readable; artifact links belong in detail."""
    if re.search(r'!?\[[^\]]*\]\([^)]*\)', text):
        raise ValueError(f'{ident}: {heading} must be plain prose without Markdown links; put artifact links in the detailed analysis')
    if re.search(r'https?://|(?:^|\s)/[^\s]+/|\b(?:claim-work|proof-attempts|findings|models|input|trace)/', text):
        raise ValueError(f'{ident}: {heading} must describe the method and result, not raw artifact paths or URLs')
    if text.lstrip().startswith(('{', '[', '```')) or re.search(r'\b(?:axioms|status|proofs|claim_id|sha256)\s*:\s*[\[{]', text, re.I):
        raise ValueError(f'{ident}: {heading} must use human sentences, not serialized metadata or raw objects')
    if len(re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)?", text)) < 3:
        raise ValueError(f'{ident}: {heading} needs an explanatory human sentence')


def validate(root):
    root = Path(root).resolve()
    no_json(root)
    policy = fields((root / 'audit-policy.md').read_text())
    if policy.get('Profile') != 'markdown_claim_traces_v1':
        raise ValueError('Unsupported Markdown audit profile')
    manifest = rows((root / 'manifest.md').read_text())
    if not manifest:
        raise ValueError('Missing immutable input manifest entries')
    paths = set()
    for row in manifest:
        path = inside(root, row['Path'])
        if digest(path) != row['SHA256']:
            raise ValueError(f'Input changed: {row["Path"]}')
        paths.add(row['Path'])
    if not {'input/report.md', 'audit-policy.md', 'report-ledger.md'} <= paths:
        raise ValueError('Manifest must bind report, policy and report ledger')
    originals = {p.relative_to(root).as_posix() for p in (root / 'input').rglob('*') if p.is_file()}
    if not originals <= paths:
        raise ValueError('Input files missing from immutable manifest')
    report = audit.paragraphs((root / 'input/report.md').read_text(encoding='utf-8'))
    if not report:
        raise ValueError('The report has no readable extracted passages; inspect the retained extraction failure and provide a reviewed report projection')
    extraction_index = rows((root / 'input/extraction-index.md').read_text()) if (root / 'input/extraction-index.md').is_file() else []
    extracted_segments = rows((root / 'input/extraction-segments.md').read_text()) if (root / 'input/extraction-segments.md').is_file() else []
    review_rows = rows((root / 'extraction-review.md').read_text()) if (root / 'extraction-review.md').is_file() else []
    incomplete = [row for row in extraction_index if row['Status'] != 'ready']
    if incomplete:
        acknowledgement = root / 'ingestion-acknowledgement.md'
        acknowledged = rows(acknowledgement.read_text()) if acknowledgement.is_file() else []
        for row in incomplete:
            if not any(item.get('Original') == row['Original'] and item.get('Status') == row['Status'] and item.get('Impact', '').strip() and item.get('Limitation', '').strip() for item in acknowledged):
                raise ValueError('Incomplete native extraction needs an explicit impact and limitation in ingestion-acknowledgement.md: ' + row['Original'])
    registry = rows((root / 'claims.md').read_text())
    covered = set()
    registered = {}
    for row in registry:
        anchor, ident, disposition = row['Report anchor'], row['Claim ID'], row['Disposition']
        if anchor not in report or disposition not in ('audited', 'deferred', 'non_claim'):
            raise ValueError('Every registry row needs a known report anchor and an audited/deferred/non_claim disposition')
        covered.add(anchor)
        if disposition != 'audited' and not row['Reason'].strip():
            raise ValueError('Deferred or non-claim rows require a reason')
        if disposition == 'non_claim':
            continue
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_-]*', ident) or ident in registered:
            raise ValueError('Registered claim IDs must be unique and nonempty')
        registered[ident] = row
    if covered != set(report):
        raise ValueError('Claim registry does not account for every report passage')
    findings = []
    all_outputs = {}
    trace_by_claim = {}
    for path in sorted((root / 'trace').glob('*/*.md')):
        text = path.read_text(); meta = fields(text); sec = sections(text)
        for heading in ('Inputs', 'Outputs'):
            rewritten = []
            for ref in rows(sec.get(heading, '')):
                for key in ('Artifact', 'Snapshot'):
                    resolved = inside(root, path.parent.relative_to(root) / link_path(ref[key]))
                    ref[key] = resolved.relative_to(root).as_posix()
                rewritten.append((ref['Artifact'], ref['SHA256'], ref['Snapshot']))
            sec[heading] = table(['Artifact', 'SHA256', 'Snapshot'], rewritten)
        ident, step = meta.get('Claim ID'), meta.get('Step')
        if ident not in registered or step not in STEPS:
            raise ValueError(f'Unknown claim or step in {path.name}')
        for heading in ('Inputs', 'Outputs'):
            refs = rows(sec.get(heading, ''))
            for ref in refs:
                historical = inside(root, link_path(ref['Snapshot']))
                if digest(historical) != ref['SHA256']:
                    raise ValueError(f'Historical trace snapshot changed: {path.name}')
                if heading == 'Outputs':
                    all_outputs.setdefault(link_path(ref['Artifact']), set()).add(ident)
        trace_by_claim.setdefault(ident, {})[step] = (path.name, meta, sec)
    for path in sorted((root / 'findings').glob('*.md')):
        ident = path.stem
        if ident not in registered or registered[ident]['Disposition'] != 'audited':
            raise ValueError(f'Unregistered or deferred finding: {ident}')
        sec = sections(path.read_text())
        if any(not sec.get(heading, '').strip() or sec[heading].startswith('REPLACE') for heading in HEADINGS):
            raise ValueError(f'{ident}: every required finding heading needs substantive content')
        for heading in ('Executive conclusion', 'How verified', 'Executive limits'):
            validate_summary(ident, heading, sec[heading])
        if sec['Report anchor'] != registered[ident]['Report anchor'] or unquote(sec['Exact report claim']) not in report[sec['Report anchor']]:
            raise ValueError(f'{ident}: report anchor or exact quote does not match the preserved report')
        for heading, allowed in (('Semantic state', audit.STATES['semantic_state']), ('Argument status', audit.STATES['argument_status']), ('Formal status', audit.STATES['formal_status'])):
            if sec[heading] not in allowed:
                raise ValueError(f'{ident}: invalid {heading}')
        evidence = []
        for block in re.split(r'^### ', sec['Evidence'], flags=re.M)[1:]:
            data = fields(block)
            source, anchor = data.get('Source'), data.get('Anchor')
            quotes = '\n'.join(re.sub(r'^> ?', '', line) for line in block.splitlines() if line.startswith('>'))
            if source not in paths or not source.startswith('input/sources/'):
                raise ValueError(f'{ident}: evidence must cite a preserved independent source')
            source_path = inside(root, source)
            if digest(source_path) == digest(root / 'input/report.md'):
                raise ValueError(f'{ident}: a copied report cannot serve as independent source evidence')
            try:
                passage = audit.paragraphs(source_path.read_text(encoding='utf-8')).get(anchor)
            except UnicodeError as exc:
                raise ValueError(f'{ident}: cite the extracted Markdown projection instead of native bytes at {source}; see input/extraction-index.md') from exc
            if not quotes.strip() or passage is None or quotes not in passage:
                raise ValueError(f'{ident}: evidence quote/anchor mismatch')
            item = {'path': source, 'anchor': anchor, 'quote': quotes}
            native = next((row for row in extracted_segments if row['Projection'] == source and row['Anchor'] == anchor), None)
            if native:
                item.update(original_path=native['Original'], extraction_record=native['Extraction record'], method=native['Method'], uncertainty=native['Uncertainty'])
            evidence.append(item)
        proofs = []
        for block in re.split(r'^### ', sec['Proofs'], flags=re.M)[1:]:
            data = fields(block)
            proof = {key.lower(): data.get(key, '') for key in ('Module', 'Theorem', 'Target', 'Relation')}
            if not all(proof.values()) or proof['relation'] not in ('supports', 'refutes', 'auxiliary'):
                raise ValueError(f'{ident}: incomplete proof reference')
            proof['claim_id'] = ident
            proofs.append(proof)
        if sec['Formal status'] in ('proved', 'refuted', 'auxiliary') and not proofs:
            raise ValueError(f'{ident}: formal status requires actual proof references')
        pure_math = fields(sec['Formalization']).get('Domain') == 'pure_mathematics'
        if proofs and not evidence and not pure_math:
            raise ValueError(f'{ident}: formal proof requires source evidence correspondence or an explicit pure_mathematics domain')
        if sec['Semantic state'] in ('supported', 'refuted', 'both') and not evidence and not proofs:
            raise ValueError(f'{ident}: a positive or negative verdict requires independent evidence or a justified mathematical proof')
        if sec['Formal status'] in ('not_attempted', 'blocked') and proofs:
            raise ValueError(f'{ident}: not_attempted/blocked findings cannot contain formal proof references')
        expected_state = {'proved': 'supported', 'refuted': 'refuted'}.get(sec['Formal status'])
        relation = {'proved': 'supports', 'refuted': 'refutes', 'auxiliary': 'auxiliary'}.get(sec['Formal status'])
        if expected_state and sec['Semantic state'] != expected_state:
            raise ValueError(f'{ident}: semantic and formal verdicts are inconsistent')
        if relation and relation not in {proof['relation'] for proof in proofs}:
            raise ValueError(f'{ident}: formal verdict lacks a corresponding proof relation')
        if sec['Formal status'] in ('proved', 'refuted'):
            references = [(item['path'], item['anchor']) for item in evidence] + [('input/report.md', sec['Report anchor'])]
            for source, anchor in references:
                uncertain = [row for row in extracted_segments if row['Projection'] == source and row['Anchor'] == anchor and row['Review required'] == 'yes']
                for passage in uncertain:
                    original = next(row for row in extraction_index if row['Projection'] == source)
                    if not any(row.get('Projection') == source and row.get('Anchor') == anchor and row.get('Original SHA256') == original['Original SHA256'] and row.get('Passage SHA256') == passage['Passage SHA256'] and row.get('Decision') == 'accepted' and row.get('Reviewer', '').strip() and row.get('Reason', '').strip() for row in review_rows):
                        raise ValueError(f'{ident}: uncertain native passage needs recorded visual review tied to original and passage hashes: {source}#{anchor}')
        previous = ''
        for step in STEPS:
            item = trace_by_claim.get(ident, {}).get(step)
            if not item:
                raise ValueError(f'{ident}: missing individual {step} trace')
            name, meta, trace_sec = item
            if name <= previous or meta.get('Status') not in ('completed', 'blocked', 'failed'):
                raise ValueError(f'{ident}: individual steps must have ordered terminal outcomes')
            previous = name
            inputs, outputs = rows(trace_sec.get('Inputs', '')), rows(trace_sec.get('Outputs', ''))
            if not outputs or (meta['Status'] == 'completed' and not inputs):
                raise ValueError(f'{ident}: individual {step} needs actual input/output snapshots')
            if not any(all_outputs[link_path(ref['Artifact'])] == {ident} for ref in outputs):
                raise ValueError(f'{ident}: shared outputs cannot replace individual analysis')
            if not trace_sec.get('Result and limits', '').strip():
                raise ValueError(f'{ident}: step result/limitations missing')
            if step == 'verification' and proofs and (meta['Status'] != 'completed' or not trace_sec.get('Command', '').startswith('```sh') or not any(link_path(ref['Artifact']).endswith('.md') for ref in outputs)):
                raise ValueError(f'{ident}: formal verification requires an actual command and readable retained log')
        findings.append({'claim_id': ident, 'sections': sec, 'proofs': proofs, 'evidence': evidence, 'path': path})
    if {f['claim_id'] for f in findings} != {ident for ident, row in registered.items() if row['Disposition'] == 'audited'}:
        raise ValueError('Every audited registry claim requires exactly one finding')
    return policy, findings


def fingerprints(root):
    paths = [root / 'manifest.md', root / 'claims.md', root / 'audit-policy.md']
    for folder in ('input', 'findings', 'models', 'trace', 'proof-attempts', 'extraction'):
        paths += [p for p in (root / folder).rglob('*') if p.is_file()]
    paths += [root / name for name in ('extraction-review.md', 'ingestion-acknowledgement.md') if (root / name).is_file()]
    return [(p.relative_to(root).as_posix(), digest(p)) for p in sorted(paths)]


def check(args):
    root = Path(args.workspace).resolve()
    destination = root / 'validation.md'
    if destination.is_file():
        history = root / 'validation-history'
        history.mkdir(exist_ok=True)
        shutil.copyfile(destination, history / (now().replace(':', '-') + '.md'))
    destination.write_text('# Audit validation\n\n- Status: running\n- Recorded: ' + now() + '\n')
    try:
        return _check(args)
    except (ValueError, OSError, audit.AuditError) as exc:
        destination.write_text('# Audit validation\n\n- Status: failed\n- Recorded: ' + now() + '\n\n## Failure and limitations\n\n' + str(exc) + '\n')
        raise


def _check(args):
    root = Path(args.workspace).resolve()
    policy, findings = validate(root)
    proofs = [proof for finding in findings for proof in finding['proofs']]
    results = []
    if proofs or list((root / 'models').rglob('*.lean')):
        from markdown_proofs import check_models
        results = check_models(root, args.lean, proofs, policy['Lean version'], args.timeout)
    content = '# Audit validation\n\n- Status: passed\n- Recorded: ' + now() + '\n- Profile: markdown_claim_traces_v1\n\n'
    content += 'Individual trace, source hashes, exact quotes, coverage and finding structure were checked. Semantic fidelity and summary adequacy still require human or agent review.\n\n'
    content += '## Formal verification\n\n' + ('Executed exact-target and axiom checks.\n\n' if results else 'Not attempted: documentary findings only; no formal success is asserted.\n\n')
    for result in results:
        content += f"- {result.get('claim_id') or 'Model audit'}: `{result['theorem']}` — [Executed check]({result['check_file']}) · [Actual command and output]({result['log']})\n"
    content += '\n## Proof targets\n\n' + table(['Claim ID', 'Module', 'Theorem', 'Target', 'Relation', 'Axioms', 'Check', 'Log', 'Status'], [(r.get('claim_id') or '', r['module'], r['theorem'], r.get('target') or '', r.get('relation') or '', ', '.join(r['axioms']), r['check_file'], r['log'], 'passed') for r in results])
    content += '\n## Checked artifact hashes\n\n' + table(['Path', 'SHA256'], fingerprints(root))
    (root / 'validation.md').write_text(content, encoding='utf-8')
    with (root / 'phase-journal.md').open('a') as stream:
        stream.write(f'\n## {now()} — Global validation completed\n\n[Actual validation and proof logs](validation.md). Individual verification steps preceded this global check.\n')
    return root / 'validation.md'


def render(args):
    root = Path(args.workspace).resolve()
    _, findings = validate(root)
    validation_text = (root / 'validation.md').read_text()
    if fields(validation_text).get('Status') != 'passed':
        raise ValueError('Latest validation did not pass')
    checked = sections(validation_text)
    expected = {row['Path']: row['SHA256'] for row in rows(checked.get('Checked artifact hashes', ''))}
    if expected != dict(fingerprints(root)):
        raise ValueError('Audit artifacts changed after validation; recheck before rendering')
    content = report_content(findings)
    (root / 'audit-report.md').write_text(content, encoding='utf-8')
    (root / 'report-integrity.md').write_text('# Report delivery integrity\n\n- Report: audit-report.md\n- Report SHA256: ' + digest(root / 'audit-report.md') + '\n- Validation: validation.md\n- Validation SHA256: ' + digest(root / 'validation.md') + '\n- Recorded: ' + now() + '\n\nThese hashes bind the delivered report bytes to the successful validation record. Later renderer styling changes do not retroactively invalidate this delivery. Changes to the bound report, validation or checked audit artifacts require validation or rendering as appropriate.\n', encoding='utf-8')
    return root / 'audit-report.md'


def report_content(findings):
    content = '# Evidence-backed claim audit\n\nThis audits the supplied report; it is not a new substantive report.\n\n[Checked validation](validation.md) · [Claim coverage](claims.md) · [Individual chronology](phase-journal.md)\n\n'
    labels = {'supported': 'Supported by the supplied evidence within the stated limits.',
              'refuted': 'Refuted within the stated scope.',
              'undetermined': 'Unresolved on the supplied evidence.',
              'both': 'Conflicting support and refutation remain.',
              'not_applicable': 'Not applicable within this audit scope.'}
    for finding in findings:
        sec = finding['sections']; ident = finding['claim_id']
        quote = unquote(sec['Exact report claim'])
        title = re.sub(r'[#`*\[\]\\]', '', ' '.join(quote.split()))
        if len(title) > 80:
            title = title[:77].rsplit(' ', 1)[0] + '…'
        content += f'## {ident} — {title}\n\n### Exact report claim\n\n'
        content += '\n'.join('> ' + line for line in quote.splitlines()) + '\n\n'
        content += '### Finding\n\n' + labels[sec['Semantic state']] + '\n\n' + sec['Executive conclusion']
        content += '\n\n### How verified\n\n' + sec['How verified']
        content += '\n\n### Limits\n\n' + sec['Executive limits']
        content += f'\n\n[Full individual analysis](findings/{ident}.md)\n\n'
    return content


def inspect(args):
    root = Path(args.workspace).resolve()
    errors = []
    try:
        _, validated = validate(root)
    except (ValueError, OSError, UnicodeError) as exc:
        errors.append(str(exc))
        validated = []
    by_id = {finding['claim_id']: finding for finding in validated}
    events = []
    for path in sorted((root / 'trace').glob('*/*.md')):
        text = path.read_text(); meta = fields(text); sec = sections(text)
        event = {'event_id': path.relative_to(root).as_posix(), 'timestamp': meta.get('Recorded'),
            'claim_ids': [meta.get('Claim ID')], 'claim_step': meta.get('Step'), 'status': meta.get('Status'),
            'title': text.splitlines()[0].lstrip('# '), 'description': sec.get('Action', ''),
            'command': sec.get('Command', ''), 'result': sec.get('Result and limits', ''),
            'path': path.relative_to(root).as_posix()}
        event['phase'] = {'registration': 'claims', 'evidence': 'evidence', 'analysis': 'modeling', 'verification': 'verification', 'verdict': 'reporting'}.get(event['claim_step'], 'review')
        for heading, key in (('Inputs', 'inputs'), ('Outputs', 'outputs')):
            event[key] = []
            for ref in rows(sec.get(heading, '')):
                item = {'sha256': ref['SHA256']}
                for column, field in (('Artifact', 'path'), ('Snapshot', 'snapshot')):
                    target = inside(root, path.parent.relative_to(root) / link_path(ref[column]))
                    item[field] = target.relative_to(root).as_posix()
                event[key].append(item)
        events.append(event)
    findings = []
    for path in sorted((root / 'findings').glob('*.md')):
        sec = sections(path.read_text()); ident = path.stem
        item = {'claim_id': ident, 'path': path.relative_to(root).as_posix(),
            'report_quote': unquote(sec.get('Exact report claim', '')), 'report_anchor': sec.get('Report anchor', ''),
            'question': sec.get('Verification criteria', ''),
            'executive_summary': {'conclusion': sec.get('Executive conclusion', ''), 'how_verified': sec.get('How verified', ''), 'limits': sec.get('Executive limits', '')},
            'semantic_state': sec.get('Semantic state'), 'argument_status': sec.get('Argument status'), 'formal_status': sec.get('Formal status'),
            'reasoning': [sec.get('Reasoning', '')], 'limits': [sec.get('Limits', '')],
            'evidence': by_id.get(ident, {}).get('evidence', []), 'proofs': by_id.get(ident, {}).get('proofs', []),
            'trace_events': [event for event in events if event['claim_ids'] == [ident]], 'sections': sec}
        findings.append(item)
    proof_results = None
    current = False
    if (root / 'validation.md').is_file():
        validation_text = (root / 'validation.md').read_text()
        validation_sections = sections(validation_text)
        expected = {row['Path']: row['SHA256'] for row in rows(validation_sections.get('Checked artifact hashes', ''))}
        current = fields(validation_text).get('Status') == 'passed' and expected == dict(fingerprints(root)) and not errors
        targets = [{key.lower().replace(' ', '_'): value for key, value in row.items()} for row in rows(validation_sections.get('Proof targets', ''))]
        for target in targets:
            target['check_file'] = target.pop('check', '')
            target['axioms'] = [value.strip() for value in target.get('axioms', '').split(',') if value.strip()]
        proof_results = {'path': 'validation.md', 'status': fields(validation_text).get('Status'), 'integrity': 'current' if current else 'stale_or_failed', 'targets': targets}
    binding = fields((root / 'report-integrity.md').read_text()) if (root / 'report-integrity.md').is_file() else {}
    report_fresh = current and (root / 'audit-report.md').is_file() and binding.get('Report') == 'audit-report.md' and binding.get('Validation') == 'validation.md' and binding.get('Report SHA256') == digest(root / 'audit-report.md') and binding.get('Validation SHA256') == digest(root / 'validation.md')
    result = {'profile': 'markdown_claim_traces_v1', 'workspace': str(root), 'findings': findings,
        'phases': events, 'report_path': 'audit-report.md' if (root / 'audit-report.md').is_file() else None,
        'proof_results': proof_results, 'report_fresh': report_fresh,
        'trace_complete': not errors, 'errors': errors}
    if args.machine:
        return json.dumps(result, ensure_ascii=False)
    return '# Markdown audit inspection\n\n- Findings: ' + str(len(findings)) + '\n- Individual trace complete: ' + str(not errors).lower() + '\n\n' + '\n'.join('- ' + error for error in errors)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--brief', help='Optional review instructions, never independent source evidence')
    prep.add_argument('--report', required=True); prep.add_argument('--sources', required=True, nargs='+')
    prep.add_argument('--ocr-language', default='eng'); prep.add_argument('--extract-timeout', type=float, default=60); prep.add_argument('--max-pages', type=int)
    prep.add_argument('--temp-root', default='tmp'); prep.add_argument('--lean-version', default='4.24.0')
    tr = commands.add_parser('trace'); tr.add_argument('--workspace', required=True)
    tr.add_argument('--claim-id', required=True); tr.add_argument('--step', choices=STEPS, required=True)
    tr.add_argument('--status', choices=('completed', 'blocked', 'failed'), required=True)
    tr.add_argument('--title', required=True); tr.add_argument('--description', required=True)
    tr.add_argument('--input', action='append', default=[]); tr.add_argument('--output', action='append', default=[])
    tr.add_argument('--command'); tr.add_argument('--result', required=True)
    ins = commands.add_parser('inspect'); ins.add_argument('--workspace', required=True); ins.add_argument('--machine', action='store_true', help='Emit JSON transport on stdout only; never write a JSON artifact')
    for name in ('check', 'render'):
        sub = commands.add_parser(name); sub.add_argument('--workspace', required=True)
        if name == 'check':
            sub.add_argument('--lean', default='lean'); sub.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args(argv)
    try:
        print(globals()[args.operation](args))
        return 0
    except (ValueError, OSError, audit.AuditError) as exc:
        print('Audit operation failed: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
