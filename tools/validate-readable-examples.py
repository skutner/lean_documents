#!/usr/bin/env python3
"""Read-only API verification of the latest readable audit for every selected report."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import sys
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen

CASES = ['acquisition-financing', 'project-funding-gate', 'operating-budget', 'research-funding', 'story-timeline', 'narrative-consistency', 'poem-form-and-voice']
DEFAULTS = [case + suffix for case in CASES for suffix in ('', '-ro')]
STEPS = ['registration', 'evidence', 'analysis', 'verification', 'verdict']
REPO = Path(__file__).resolve().parents[1]


def table(headers, rows):
    def cell(value):
        return str(value).replace('|', '&#124;').replace('\n', ' ')
    return '\n'.join('| ' + ' | '.join(map(cell, row)) + ' |' for row in [headers, ['---'] * len(headers), *rows]) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', default='http://127.0.0.1:8787')
    parser.add_argument('--examples', nargs='+', default=DEFAULTS)
    parser.add_argument('--output', required=True)
    parser.add_argument('--allow-single-report', action='store_true')
    args = parser.parse_args(argv)
    output = Path(args.output).resolve()
    if not output.is_relative_to(REPO / 'tmp'):
        parser.error('--output must be a file inside repository tmp/')
    origin = args.origin.rstrip('/')
    parts = urlsplit(origin)
    if parts.scheme != 'http':
        parser.error('--origin must be an http origin')
    # The server answers only on the literal 127.0.0.1: the name localhost also reaches [::1].
    if parts.hostname != '127.0.0.1' or parts.path or parts.query or parts.username or parts.password:
        parser.error('--origin must be http://127.0.0.1:<port>; the pilot server answers on no other host')
    blockers, summaries, details = [], [], []

    def fetch(route):
        with urlopen(origin + route, timeout=45) as response:
            return json.load(response)

    def require(condition, message):
        if not condition:
            blockers.append(message)

    def review(example, report, run):
        ident, scope = run['id'], example['id'] + ' / ' + report['path']
        data = fetch('/api/runs/' + ident)
        start = len(blockers)
        require(data.get('status') == 'finished', scope + ': process is ' + str(data.get('status')))
        require(data.get('reportAvailability', {}).get('status') == 'ready', scope + ': report is not ready')
        require(data.get('reportBinding', {}).get('status') == 'matches', scope + ': report binding is not current')
        require(data.get('proofIntegrity', {}).get('status') == 'matches', scope + ': proof integrity is not current')
        require((data.get('proofResults') or {}).get('status') == 'passed', scope + ': validation did not pass')
        require(data.get('phaseRecordComplete'), scope + ': individual trace contract is incomplete')
        require(not data.get('journalErrors') and not data.get('findingsError'), scope + ': helper reports validation errors')
        cached = {}

        def artifact(path, digest=None):
            require(bool(path), scope + ': missing artifact reference')
            if not path:
                return
            require(not PurePosixPath(path).is_absolute() and '..' not in PurePosixPath(path).parts, scope + ': unsafe artifact path')
            if PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts:
                return
            try:
                if path not in cached:
                    cached[path] = fetch('/api/file?' + urlencode({'run': ident, 'path': path, 'hash': '1'}))
                value = cached[path]
                require(value.get('size', 0) > 0, scope + ': empty artifact ' + path)
                if digest:
                    require(value.get('sha256') == digest, scope + ': snapshot digest mismatch ' + path)
            except Exception as error:
                blockers.append(scope + ': cannot read ' + path + ': ' + str(error))

        def document_link(path, label):
            folder = Path(data.get('folder', ''))
            destination = (REPO / folder / path).resolve()
            if not destination.is_relative_to(REPO / 'tmp'):
                return label
            return '[' + label + '](' + os.path.relpath(destination, output.parent) + ')'

        originals = [f for f in example.get('files', []) if f.get('role') == 'source' or f.get('path') == report['path']]
        expected_sources = set()
        for original in originals:
            source = original.get('role') == 'source'
            preserved = 'inputs/sources/' + PurePosixPath(original['path']).name if source else 'inputs/report' + PurePosixPath(original['path']).suffix
            if source:
                expected_sources.add(preserved)
            artifact(preserved)
            current = fetch('/api/file?' + urlencode({'example': example['id'], 'path': original['path'], 'hash': '1'}))
            require(current.get('sha256') == cached.get(preserved, {}).get('sha256'), scope + ': current input differs from the audited snapshot: ' + original['path'])
        retained_sources = {f['path'] for f in data.get('files', []) if f['path'].startswith('inputs/sources/')}
        require(expected_sources == retained_sources, scope + ': current and audited source inventories differ')
        artifact(data.get('auditReportPath'))
        findings = data.get('findings', [])
        require(bool(findings), scope + ': no findings')
        targets = (data.get('proofResults') or {}).get('targets', [])
        for finding in findings:
            cid = finding['claim_id']
            events = sorted([e for e in data.get('phases', []) if e.get('claim_ids') == [cid]], key=lambda e: e.get('timestamp', ''))
            latest = {e.get('claim_step'): e for e in events}
            selected = [latest[s] for s in STEPS if s in latest]
            require(len(selected) == 5, scope + '/' + cid + ': missing individual steps')
            require([e.get('timestamp') for e in selected] == sorted(e.get('timestamp', '') for e in selected), scope + '/' + cid + ': steps out of order')
            for event in selected:
                require(event.get('status') in ('completed', 'blocked'), scope + '/' + cid + ': unsuccessful step')
                require(bool(event.get('outputs')), scope + '/' + cid + ': step has no retained output')
                artifact(event.get('path'))
                for ref in event.get('inputs', []) + event.get('outputs', []):
                    artifact(ref.get('snapshot'), ref.get('sha256'))
            artifact(finding.get('path'))
            proofs = finding.get('proofs', [])
            require(bool(proofs) or finding.get('formal_status') in ('not_attempted', 'blocked'), scope + '/' + cid + ': formal result has no proof')
            require(not proofs or bool(latest.get('verification', {}).get('command')), scope + '/' + cid + ': formal verification has no recorded command')
            for proof in proofs:
                matches = [t for t in targets if all(t.get(k) == (cid if k == 'claim_id' else proof.get(k)) for k in ('claim_id', 'module', 'theorem', 'target', 'relation'))]
                require(any(t.get('status') == 'passed' for t in matches), scope + '/' + cid + ': exact proof reference has no passed target')
                artifact(proof.get('path'))
                for target in matches:
                    artifact(target.get('check_file')); artifact(target.get('log'))
            details.append([example['id'], report.get('label', report['path']), document_link(finding['path'], cid), finding.get('semantic_state'), finding.get('formal_status'), str(len(selected)) + '/5 (' + str(len(events)) + ' recorded)'])
        summaries.append([example['id'], report.get('label', report['path']), ident, data.get('status'), data.get('reportAvailability', {}).get('status'), len(findings), 'PASS' if len(blockers) == start else 'BLOCKED', document_link(data['auditReportPath'], 'Audit report') if data.get('auditReportPath') else 'Unavailable'])

    try:
        catalog = {e['id']: e for e in fetch('/api/examples')}
        for example_id in args.examples:
            example = catalog.get(example_id)
            if not example:
                blockers.append(example_id + ': example not in catalog'); continue
            reports = example.get('reports', [])
            require(args.allow_single_report or {'report-correct.md', 'report-errors.md'} <= {r['path'] for r in reports}, example_id + ': correct and failures variants are required')
            require(bool(reports), example_id + ': no reports')
            for report in reports:
                runs = sorted([r for r in example.get('runs', []) if r.get('reportPath') == report['path']], key=lambda r: r.get('created', ''), reverse=True)
                if not runs:
                    blockers.append(example_id + '/' + report['path'] + ': no run')
                    summaries.append([example_id, report.get('label', report['path']), 'None', 'not started', 'unavailable', 0, 'BLOCKED', 'Unavailable']); continue
                try:
                    review(example, report, runs[0])
                except Exception as error:
                    blockers.append(example_id + '/' + report['path'] + ': inspection failed: ' + str(error))
                    summaries.append([example_id, report.get('label', report['path']), runs[0]['id'], runs[0].get('status'), 'inspection failed', 'Unknown', 'BLOCKED', 'Unavailable'])
    except Exception as error:
        blockers.append('Catalog unavailable: ' + str(error))
    text = '# Readable audit validation\n\nChecked at ' + datetime.now(timezone.utc).isoformat() + '. [Open audit UI](' + origin + '/). Latest runs only; GET requests only. No evaluator outcomes were read, no Lean execution was repeated, and no audit files were changed.\n\n'
    text += '## Reports\n\n' + table(['Example', 'Report', 'Run', 'Process', 'Delivery', 'Findings', 'Check', 'Document'], summaries)
    text += '\n## Individual findings\n\n' + table(['Example', 'Report', 'Finding', 'Semantic result', 'Formal result', 'Individual steps'], details)
    text += '\n## Blockers\n\n' + ('\n\n'.join(dict.fromkeys(blockers)) if blockers else 'None identified by these structural and retained-artifact checks.')
    text += '\n\n## Limits\n\nThis checks declared outcomes, current bindings, current example input bytes, individual recorded steps and accessible proof artifacts. It does not certify source authenticity, semantic correspondence, completeness of literary interpretation or independent kernel correctness.\n'
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text(text, encoding='utf-8')
    print(str(output)); return int(bool(blockers))


if __name__ == '__main__':
    sys.exit(main())
