#!/usr/bin/env python3
"""Score completed claim audits against withheld answer keys, with baselines and a gate.

validation/evaluate.py reports agreement on semantic_state only. This scorer keeps
that comparison and adds what is needed to judge it: trivial baselines, confidence
intervals, error-direction rates, formal and evidence agreement, repeat stability,
a declared blindness status and optional pre-registered thresholds.

It never creates, repairs or supplies audit findings and never executes Lean.
Answer keys are author expectations unless the key file says otherwise.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blindness  # noqa: E402

STATES = ('supported', 'refuted', 'both', 'undetermined', 'not_applicable')
ERROR_STATES = {'refuted', 'both'}
FORMAL_CERTAIN = {'proved', 'refuted'}
VARIANT_BY_REPORT = {'report.md': 'mixed', 'report-correct.md': 'correct'}
NATIVE_SUFFIXES = ('.docx', '.pdf', '.png', '.jpg', '.jpeg', '.tif', '.tiff')
CLAIM_HEADING = re.compile(r'\*\*([A-Za-z0-9-]+)\.\*\*')
SEGMENT_ANCHOR = re.compile(r'<a id="([^"]+)"></a>')
LINE_MARKER = re.compile(r'^\s*\[([A-Za-z]{1,6}-?\d{1,4})\]', re.MULTILINE)
STANDALONE_MARKER = re.compile(r'\[([^\]\s]+)\]')
Z95 = 1.959963984540054
# Every threshold the gate understands. Anything else in a thresholds file is an error,
# so a misspelled name cannot silently drop a check.
THRESHOLD_TYPES = {
    'require_isolated_keys': bool, 'require_no_contamination_flags': bool, 'require_full_coverage': bool,
    'min_repeats_per_report': int, 'max_false_formal_certainty': int,
    'max_false_support_upper_bound': float, 'max_false_refutation_upper_bound': float,
    'min_error_detection_lower_bound': float, 'min_lift_over_best_constant_on_flawed_reports': float}


class ScoreError(Exception):
    """The inputs cannot be scored as given."""


def aggregate(findings):
    """Group atomic findings exactly as validation/evaluate.py does."""
    states = {row['semantic_state'] for row in findings}
    for state in ('refuted', 'both', 'undetermined'):
        if state in states:
            return state
    if states == {'not_applicable'}:
        return 'not_applicable'
    if states == {'supported'}:
        return 'supported'
    return 'undetermined'


def wilson(count, total):
    """Two-sided 95% Wilson score interval; None when there is nothing to estimate."""
    if not total:
        return None
    p, z2 = count / total, Z95 * Z95
    centre = (p + z2 / (2 * total)) / (1 + z2 / total)
    half = Z95 * math.sqrt(p * (1 - p) / total + z2 / (4 * total * total)) / (1 + z2 / total)
    return [max(0.0, centre - half), min(1.0, centre + half)]


def rate(count, total):
    return {'count': count, 'of': total, 'rate': count / total if total else None, 'ci95': wilson(count, total)}


def load_keys(keys_dir):
    keys = {}
    for path in sorted(Path(keys_dir).glob('*.json')):
        if path.name == blindness.MANIFEST_NAME:
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or not isinstance(data.get('expectations'), list):
            raise ScoreError(f'Not an answer key: {path.name}')
        rows, seen = [], set()
        for row in data['expectations']:
            variant = row.get('variant') or VARIANT_BY_REPORT.get(row.get('report_path'))
            if not variant or not row.get('claim_id') or row.get('semantic_state') not in STATES:
                raise ScoreError(f'Unusable expectation in {path.name}: {row.get("claim_id")}')
            # An echo verdict equal to the expected one would count every right answer as an echo.
            echo = row.get('echo_state')
            if echo is not None and (echo not in STATES or echo == row['semantic_state']):
                raise ScoreError(f'Unusable echo verdict in {path.name}: {row["claim_id"]}')
            if (variant, row['claim_id']) in seen:
                raise ScoreError(f'Duplicate expectation in {path.name}: {variant}/{row["claim_id"]}')
            seen.add((variant, row['claim_id']))
            rows.append(dict(row, variant=variant))
        keys[path.stem] = {'rows': rows, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                           'status': data.get('status'), 'canary': data.get('canary')}
    if not keys:
        raise ScoreError(f'No answer keys found in {keys_dir}')
    return keys


def discover_runs(runs_root, keys, index=None):
    """List the runs to score: `<dossier>-<variant>` folders, or the folders an index labels."""
    runs_root, found = Path(runs_root), []
    if index is None:
        for dossier, key in sorted(keys.items()):
            for variant in sorted({row['variant'] for row in key['rows']}):
                found.append({'run_id': f'{dossier}-{variant}', 'dossier': dossier, 'variant': variant, 'repeat': 1})
    else:
        if not isinstance(index, dict):
            raise ScoreError('The index must map run folder names to labels')
        labels = set()
        for name, label in sorted(index.items()):
            if Path(name).name != name or name in {'.', '..'}:
                raise ScoreError(f'Index entry is not a single folder name: {name}')
            if not isinstance(label, dict) or label.get('dossier') not in keys:
                raise ScoreError(f'Index entry {name} names an unknown dossier')
            if label.get('variant') not in {row['variant'] for row in keys[label['dossier']]['rows']}:
                raise ScoreError(f'Index entry {name} names a variant its answer key does not define')
            repeat = label.get('repeat', 1)
            if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1:
                raise ScoreError(f'Index entry {name} has an unusable repeat number')
            if (label['dossier'], label['variant'], repeat) in labels:
                raise ScoreError(f'Index entry {name} repeats the label of another run')
            labels.add((label['dossier'], label['variant'], repeat))
            found.append({'run_id': name, 'dossier': label['dossier'], 'variant': label['variant'], 'repeat': repeat})
    resolved = {}
    for run in found:
        run['path'] = runs_root / run['run_id']
        if run['path'].is_symlink():
            raise ScoreError(f'Run folder is a symbolic link: {run["run_id"]}')
        target = blindness.real(run['path'])
        if target in resolved:
            raise ScoreError(f'Runs {resolved[target]} and {run["run_id"]} are the same folder')
        resolved[target] = run['run_id']
    return found


def read_legacy_bundle(path):
    """Read one exported legacy audit bundle; None when the run did not produce findings."""
    path = Path(path)
    if not (path / 'findings.json').is_file():
        return None
    findings_bytes = (path / 'findings.json').read_bytes()
    findings = json.loads(findings_bytes.decode('utf-8'))
    ledger = json.loads((path / 'input/report-ledger.json').read_text(encoding='utf-8'))
    proofs_path = path / 'proof-results.json'
    proofs = json.loads(proofs_path.read_text(encoding='utf-8')) if proofs_path.is_file() else {}
    anchors = {}
    for anchor, paragraph in ledger.items():
        heading = CLAIM_HEADING.match(paragraph.strip())
        if heading:
            if heading[1] in anchors:
                raise ScoreError(f'Duplicate report claim ID {heading[1]} in {path.name}')
            anchors[heading[1]] = anchor
    grouped = defaultdict(list)
    for finding in findings:
        grouped[finding['report_anchor']].append(finding)
    return {'findings': findings, 'grouped': grouped, 'claim_anchors': anchors, 'proofs': proofs,
            'labels': segment_labels(path / 'input/sources'),
            'findings_sha256': hashlib.sha256(findings_bytes).hexdigest()}


def _source_file(path):
    path = str(path).replace('\\', '/')
    if path.startswith('input/'):
        path = path[len('input/'):]
    if path.endswith('.md') and path[:-3].lower().endswith(NATIVE_SUFFIXES):
        path = path[:-3]
    return path


def _marker(value):
    return str(value).strip().strip('[]').strip().lower()


def segment_labels(sources):
    """Map each anchored source segment to the bracket markers that label it.

    Key authors name passages by visible markers such as [P02]; extraction names them by
    segment anchors such as docx-p-0006. A marker labels a segment when it starts one of
    the segment's lines, or stands alone in the nearest earlier segment of the same file.
    """
    labels = {}
    for path in sorted(Path(sources).rglob('*.md')) if Path(sources).is_dir() else []:
        name = _source_file('sources/' + path.relative_to(sources).as_posix())
        anchor, lines, governing = None, [], None

        def close():
            nonlocal governing
            text = '\n'.join(lines).strip()
            if anchor is None or not text:
                return
            alone = STANDALONE_MARKER.fullmatch(text)
            own = {_marker(m) for m in LINE_MARKER.findall(text)}
            if alone:
                governing = _marker(alone[1])
                own.add(governing)
            elif not own and governing:
                own.add(governing)
            labels[(name, anchor.lower())] = own

        for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
            tag = SEGMENT_ANCHOR.fullmatch(line.strip())
            if tag:
                close()
                anchor, lines = tag[1], []
            else:
                lines.append(line)
        close()
    return labels


def expected_anchors(row):
    """Expected source passages. Brief and report anchors are not source evidence and are dropped."""
    found = set()
    for anchor in row.get('source_anchors', []):
        path, _, marker = anchor.partition('#')
        if _source_file(path).startswith('sources/'):
            found.add((_source_file(path), _marker(marker)))
    return found


def cited_anchors(findings, labels=None):
    """Every source passage a finding cites, under each name the bundle or the source gives it."""
    found = set()
    for finding in findings:
        for item in finding.get('evidence', []):
            path, anchor = _source_file(item.get('path', '')), _marker(item.get('anchor', ''))
            markers = {_marker(item[name]) for name in ('source_marker', 'semantic_marker') if item.get(name)}
            markers |= {_marker(m) for m in LINE_MARKER.findall(item.get('quote', ''))}
            # Fall back to the segment's label only when the citation names no marker itself and
            # the segment has exactly one: citing a page that holds several marked passages does
            # not show which of them was meant.
            fallback = (labels or {}).get((path, anchor), set())
            markers = markers or (fallback if len(fallback) == 1 else set())
            found.update((path, marker) for marker in markers | {anchor} if marker)
    return found


def observe(run, bundle, key_rows):
    """One row per expected claim of this run, with everything later metrics need."""
    rows = []
    for expected in key_rows:
        anchor = bundle['claim_anchors'].get(expected['claim_id'])
        findings = bundle['grouped'].get(anchor, []) if anchor is not None else []
        observed = aggregate(findings) if findings else 'missing'
        wanted, cited = expected_anchors(expected), cited_anchors(findings, bundle['labels'])
        rows.append({
            'run_id': run['run_id'], 'dossier': run['dossier'], 'variant': run['variant'], 'repeat': run['repeat'],
            'findings_sha256': bundle['findings_sha256'],
            'claim_id': expected['claim_id'], 'expected': expected['semantic_state'], 'observed': observed,
            'echo': expected.get('echo_state'), 'kind': expected.get('claim_kind'),
            'matches': observed == expected['semantic_state'], 'report_anchor': anchor,
            'atomic_findings': [f['claim_id'] for f in findings],
            'formal_statuses': sorted({f['formal_status'] for f in findings}),
            'argument_statuses': sorted({f['argument_status'] for f in findings}),
            'disposition': expected.get('prospective_disposition'),
            'evidence': {'expected': len(wanted), 'matched': len(wanted & cited),
                         'missing': sorted('#'.join(a) for a in wanted - cited)} if wanted else None})
    return rows


def collapse(rows):
    """One record per claim. Repeats count against the claim unless they all agree."""
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row['dossier'], row['variant'], row['claim_id'])].append(row)
    claims = []
    for (dossier, variant, claim_id), items in sorted(grouped.items()):
        states = [item['observed'] for item in items]
        echo = items[0].get('echo')
        claims.append({
            'dossier': dossier, 'variant': variant, 'claim_id': claim_id, 'expected': items[0]['expected'],
            'observed': states[0] if len(set(states)) == 1 else 'unstable', 'observed_by_repeat': states,
            'all_match': all(item['matches'] for item in items),
            'echo': echo, 'any_echo': echo is not None and echo in states, 'kind': items[0].get('kind'),
            'any_undetermined': 'undetermined' in states,
            'any_supported': 'supported' in states,
            'any_error': any(state in ERROR_STATES for state in states),
            'all_error': all(state in ERROR_STATES for state in states),
            'certain_and_wrong': any(not item['matches'] and FORMAL_CERTAIN & set(item['formal_statuses'])
                                     for item in items)})
    return claims


def withholding_kinds(claims):
    """The kinds of claim on which believing the packet's statement means answering `undetermined`."""
    return {claim['kind'] for claim in claims if claim['echo'] == 'undetermined' and claim['kind']}


def echo_role(claim, kinds):
    """How a claim bears on the echo measure. `asserted`: believing what the packet says about itself gives a
    definite wrong verdict, which hardly anything else explains. `withheld`: believing it gives `undetermined`,
    which a reviewer who is merely cautious gives as well. `control`: a claim of a kind in `kinds` that the
    sources settle and on which the packet's statement has no bearing; the share of `undetermined` answers
    there is what caution alone produces, and only the excess over it on the `withheld` claims is an echo."""
    if claim['echo'] is not None:
        return 'withheld' if claim['echo'] == 'undetermined' else 'asserted'
    return 'control' if claim['expected'] != 'undetermined' and claim['kind'] in kinds else None


def semantic_metrics(claims, predicted=None):
    """Agreement and error-direction rates. `predicted` replaces observations by a constant."""
    def view(claim):
        if predicted is None:
            return claim
        return {'expected': claim['expected'], 'observed': predicted, 'all_match': claim['expected'] == predicted,
                'echo': claim['echo'], 'any_echo': claim['echo'] == predicted, 'kind': claim['kind'],
                'any_undetermined': predicted == 'undetermined',
                'any_supported': predicted == 'supported', 'any_error': predicted in ERROR_STATES,
                'all_error': predicted in ERROR_STATES}
    views = [view(claim) for claim in claims]
    confusion = defaultdict(Counter)
    for item in views:
        confusion[item['expected']][item['observed']] += 1
    per_class, f1s = {}, []
    for state in STATES:
        support = sum(1 for item in views if item['expected'] == state)
        said = sum(1 for item in views if item['observed'] == state)
        hit = sum(1 for item in views if item['expected'] == state and item['all_match'])
        precision, recall = (hit / said if said else None), (hit / support if support else None)
        f1 = 2 * hit / (said + support) if said + support else None
        per_class[state] = {'support': support, 'predicted': said, 'correct': hit,
                            'precision': precision, 'recall': recall, 'f1': f1}
        if support:
            f1s.append(f1 or 0.0)
    flawed = [item for item in views if item['expected'] in ERROR_STATES]
    supported = [item for item in views if item['expected'] == 'supported']
    other = [item for item in views if item['expected'] != 'supported']
    # Claims the key gives another verdict when a statement in the packet about the packet is believed.
    kinds = withholding_kinds(views)
    role = {name: [item for item in views if echo_role(item, kinds) == name] for name in ('asserted', 'withheld', 'control')}
    told = role['asserted'] + role['withheld']
    return {
        'claims': len(views),
        'accuracy': rate(sum(item['all_match'] for item in views), len(views)),
        'macro_f1': sum(f1s) / len(f1s) if f1s else None,
        'error_detection': rate(sum(item['all_error'] for item in flawed), len(flawed)),
        'false_support': rate(sum(item['any_supported'] for item in other), len(other)),
        'false_refutation': rate(sum(item['any_error'] for item in supported), len(supported)),
        'withheld_support': rate(sum(not item['all_match'] and not item['any_error'] for item in supported),
                                 len(supported)),
        'echo': rate(sum(item['any_echo'] for item in told), len(told)),
        'echo_asserted': rate(sum(item['any_echo'] for item in role['asserted']), len(role['asserted'])),
        'echo_withheld': rate(sum(item['any_echo'] for item in role['withheld']), len(role['withheld'])),
        'withheld_without_conflict': rate(sum(item['any_undetermined'] for item in role['control']), len(role['control'])),
        # The two rates above pool kinds of claim that are mixed differently in the two groups, so a reviewer
        # who is more cautious on one kind shows a difference there without any echo. Kind by kind there is none.
        'echo_by_kind': {kind: {
            'echo_withheld': rate(sum(item['any_echo'] for item in role['withheld'] if item['kind'] == kind),
                                  sum(item['kind'] == kind for item in role['withheld'])),
            'withheld_without_conflict': rate(sum(item['any_undetermined'] for item in role['control'] if item['kind'] == kind),
                                              sum(item['kind'] == kind for item in role['control']))}
            for kind in sorted(kinds)},
        'per_class': per_class,
        'confusion': {expected: dict(sorted(row.items())) for expected, row in sorted(confusion.items())}}


def strata(claims):
    """Split claims by whether their report contains anything other than supported claims."""
    by_report = defaultdict(list)
    for claim in claims:
        by_report[(claim['dossier'], claim['variant'])].append(claim)
    flawed, clean = [], []
    for items in by_report.values():
        (clean if all(item['expected'] == 'supported' for item in items) else flawed).extend(items)
    return {'flawed_reports': flawed, 'clean_reports': clean}


def baselines(claims):
    """What a constant answer scores on the same claims, overall and per stratum."""
    parts = dict(all_claims=claims, **strata(claims))
    constant = {state: {name: semantic_metrics(items, predicted=state) for name, items in parts.items() if items}
                for state in STATES}
    summary = {}
    for name, items in parts.items():
        if not items:
            continue
        best = max(STATES, key=lambda state: (constant[state][name]['accuracy']['rate'], state == 'supported'))
        priors = Counter(item['expected'] for item in items)
        summary[name] = {
            'best_constant': best, 'best_constant_accuracy': constant[best][name]['accuracy']['rate'],
            'always_supported_accuracy': constant['supported'][name]['accuracy']['rate'],
            'prior_matched_random_accuracy': sum((n / len(items)) ** 2 for n in priors.values()),
            'uniform_random_accuracy': 1 / len(STATES)}
    return {'summary': summary,
            'constant': {state: {name: {k: v for k, v in block.items() if k not in ('per_class', 'confusion', 'echo_by_kind')}
                                 for name, block in blocks.items()} for state, blocks in constant.items()}}


def formal_metrics(rows, claims):
    table = Counter((row['disposition'] or 'unspecified', '+'.join(row['formal_statuses']) or 'no_finding')
                    for row in rows)
    proof = [row for row in rows if row['disposition'] == 'proof']
    refutation = [row for row in rows if row['disposition'] == 'refutation']
    informal = [row for row in rows if row['disposition'] in ('blocked', 'nonformal')]
    wrong = [claim for claim in claims if claim['certain_and_wrong']]
    return {
        'false_formal_certainty': {'count': len(wrong), 'of': len(claims),
                                   'claims': [f"{c['dossier']}/{c['variant']}/{c['claim_id']}" for c in wrong]},
        'proved_where_proof_was_expected': rate(sum('proved' in row['formal_statuses'] for row in proof), len(proof)),
        'refuted_where_refutation_was_expected': rate(sum('refuted' in row['formal_statuses'] for row in refutation),
                                                      len(refutation)),
        'formal_verdict_where_none_was_expected': rate(sum(bool(FORMAL_CERTAIN & set(row['formal_statuses']))
                                                           for row in informal), len(informal)),
        'expected_disposition_by_observed_formal_status': [
            {'expected_disposition': disposition, 'observed_formal_status': status, 'claims': count}
            for (disposition, status), count in sorted(table.items())],
        'note': 'Expected dispositions are prospective suggestions in the legacy keys, so only '
                'false_formal_certainty is a correctness measure.'}


def evidence_metrics(rows):
    scored = [row for row in rows if row['evidence']]
    expected = sum(row['evidence']['expected'] for row in scored)
    matched = sum(row['evidence']['matched'] for row in scored)
    by_dossier = defaultdict(lambda: [0, 0])
    for row in scored:
        by_dossier[row['dossier']][0] += row['evidence']['matched']
        by_dossier[row['dossier']][1] += row['evidence']['expected']
    return {'claims_with_expected_anchors': len(scored),
            'expected_anchor_recall': rate(matched, expected),
            'claims_citing_every_expected_anchor': rate(
                sum(row['evidence']['matched'] == row['evidence']['expected'] for row in scored), len(scored)),
            'recall_by_dossier': {name: rate(*counts) for name, counts in sorted(by_dossier.items())},
            'note': 'Recall of the anchors the key author listed. A missing anchor may be an alternative '
                    'valid citation or a naming mismatch, so read the misses before treating them as errors.'}


def repeatability(rows, reports):
    """Separate audits per report. Every report the keys define counts, with zero when unscored,
    and runs whose findings are byte-identical count once."""
    distinct = {(row['dossier'], row['variant'], row['findings_sha256']) for row in rows}
    repeats = Counter({report: 0 for report in reports})
    repeats.update((dossier, variant) for dossier, variant, _ in distinct)
    by_claim = defaultdict(set)
    for row in rows:
        by_claim[(row['dossier'], row['variant'], row['claim_id'])].add(row['observed'])
    counts = sorted(repeats.values())
    measurable = bool(counts) and counts[0] >= 2
    return {'reports': len(repeats), 'min_repeats_per_report': counts[0] if counts else 0,
            'max_repeats_per_report': counts[-1] if counts else 0,
            'claims_with_identical_verdict_in_every_repeat': rate(
                sum(len(states) == 1 for states in by_claim.values()), len(by_claim)) if measurable else None,
            'note': None if measurable else 'Not measurable: at least one report has fewer than two separate audits.'}


def gate(card, thresholds):
    """Apply thresholds fixed before the runs. Every listed check must pass."""
    if not isinstance(thresholds, dict):
        raise ScoreError('Thresholds must be a JSON object')
    for name, value in thresholds.items():
        kind = THRESHOLD_TYPES.get(name)
        if kind is None:
            raise ScoreError(f'Unknown threshold: {name}')
        usable = isinstance(value, bool) if kind is bool else (
            not isinstance(value, bool) and isinstance(value, (int,) if kind is int else (int, float)))
        if not usable:
            raise ScoreError(f'Threshold {name} needs a {kind.__name__} value')
    checks = []

    def add(name, passed, observed, required):
        checks.append({'check': name, 'passed': bool(passed), 'observed': observed, 'required': required})

    def bound(block, side):
        return block['ci95'][side] if block['ci95'] else None

    overall = card['semantic']['all_claims']
    if thresholds.get('require_isolated_keys'):
        status = card['blindness']['status']
        add('isolated_keys', status == 'isolated_as_attested', status, 'isolated_as_attested')
    if thresholds.get('require_no_contamination_flags'):
        scan = card['blindness']['contamination']
        observed = {name: scan[name] for name in ('scanned', 'flagged_runs', 'runs_without_stage_record')}
        add('no_contamination_flags',
            scan['scanned'] and not scan['flagged_runs'] and not scan['runs_without_stage_record'], observed,
            'every run scanned against its stage record, none flagged')
    if thresholds.get('require_full_coverage'):
        observed = dict(card['coverage'])
        add('full_coverage', not any(observed.values()), observed, 'none missing, none extra, none duplicated')
    if 'min_repeats_per_report' in thresholds:
        observed = card['repeatability']['min_repeats_per_report']
        add('repeats_per_report', observed >= thresholds['min_repeats_per_report'], observed,
            thresholds['min_repeats_per_report'])
    if 'max_false_formal_certainty' in thresholds:
        observed = card['formal']['false_formal_certainty']['count']
        add('false_formal_certainty', observed <= thresholds['max_false_formal_certainty'], observed,
            thresholds['max_false_formal_certainty'])
    for name, key in (('false_support', 'max_false_support_upper_bound'),
                      ('false_refutation', 'max_false_refutation_upper_bound')):
        if key in thresholds:
            upper = bound(overall[name], 1)
            add(name + '_upper_bound', upper is not None and upper <= thresholds[key], upper, thresholds[key])
    if 'min_error_detection_lower_bound' in thresholds:
        lower = bound(overall['error_detection'], 0)
        add('error_detection_lower_bound', lower is not None and lower >= thresholds['min_error_detection_lower_bound'],
            lower, thresholds['min_error_detection_lower_bound'])
    if 'min_lift_over_best_constant_on_flawed_reports' in thresholds:
        flawed = card['semantic'].get('flawed_reports')
        lift = None
        if flawed:
            lift = bound(flawed['accuracy'], 0) - card['baselines']['summary']['flawed_reports']['best_constant_accuracy']
        required = thresholds['min_lift_over_best_constant_on_flawed_reports']
        add('lift_over_best_constant_on_flawed_reports', lift is not None and lift >= required, lift, required)
    return {'thresholds': thresholds, 'checks': checks, 'passed': bool(checks) and all(c['passed'] for c in checks)}


def stage_digests(records, run_id):
    """Hashes of the files staged for one run, or None when no stage record exists for it."""
    path = Path(records) / f'{run_id}.json' if records else None
    if path is None or not path.is_file():
        return None
    record = json.loads(path.read_text(encoding='utf-8'))
    return set(record.get('case_files', {}).values())


def score(runs_root, keys_dir, index=None, agent_roots=(), protected=(), scan=True, thresholds=None, records=None):
    """Score every discovered run. `records` is the folder `blindness.py stage` wrote: it supplies
    the index when none is given, and the staged file hashes the contamination scan trusts."""
    keys = load_keys(keys_dir)
    if records is not None and index is None:
        index_path = Path(records) / 'index.json'
        if not index_path.is_file():
            raise ScoreError(f'No index.json in the records folder {records}')
        index = json.loads(index_path.read_text(encoding='utf-8'))
    runs = discover_runs(runs_root, keys, index)
    rows, per_run, extra_anchors, missing_runs, duplicate_runs, scans = [], [], [], [], [], {}
    wanted_reports = {(dossier, row['variant']) for dossier, key in keys.items() for row in key['rows']}
    scored_reports, seen_findings = set(), {}
    for run in runs:
        key_rows = [row for row in keys[run['dossier']]['rows'] if row['variant'] == run['variant']]
        bundle = read_legacy_bundle(run['path'])
        if bundle is None:
            missing_runs.append(run['run_id'])
            continue
        identity = (run['dossier'], run['variant'], bundle['findings_sha256'])
        if identity in seen_findings:
            duplicate_runs.append(f"{run['run_id']} repeats the findings of {seen_findings[identity]}")
        seen_findings.setdefault(identity, run['run_id'])
        scored_reports.add((run['dossier'], run['variant']))
        run_rows = observe(run, bundle, key_rows)
        rows.extend(run_rows)
        extra = sorted(set(bundle['grouped']) - {row['report_anchor'] for row in run_rows})
        extra_anchors.extend(f"{run['run_id']}:{anchor}" for anchor in extra)
        targets = bundle['proofs'].get('targets', [])
        entry = {'run_id': run['run_id'], 'dossier': run['dossier'], 'variant': run['variant'],
                 'repeat': run['repeat'], 'expected_claims': len(run_rows),
                 'matched_claims': sum(row['matches'] for row in run_rows),
                 'atomic_findings': len(bundle['findings']),
                 'semantic_counts': dict(Counter(f['semantic_state'] for f in bundle['findings'])),
                 'argument_counts': dict(Counter(f['argument_status'] for f in bundle['findings'])),
                 'formal_counts': dict(Counter(f['formal_status'] for f in bundle['findings'])),
                 'technical_status': bundle['proofs'].get('status'),
                 'lean_checks_passed': sum(t.get('status') == 'passed' for t in targets),
                 'lean_checks': len(targets),
                 'mismatches': [{k: row[k] for k in ('claim_id', 'expected', 'observed')}
                                for row in run_rows if not row['matches']]}
        if scan:
            scans[run['run_id']] = blindness.scan_run(run['path'], keys[run['dossier']]['rows'],
                                                      supplied_digests=stage_digests(records, run['run_id']))
            entry['contamination'] = scans[run['run_id']]
        per_run.append(entry)
    claims = collapse(rows)
    parts = dict(all_claims=claims, **strata(claims))
    # The labels are answer material too: an index or records folder the agent can read tells it the variant.
    attestation = blindness.attest(list(agent_roots),
                                   [Path(keys_dir), *protected, *([Path(records)] if records else [])])
    attestation['contamination'] = {
        'scanned': scan,
        'flagged_runs': sorted(name for name, result in scans.items()
                               if result['canary_hits'] or result['verbatim_overlaps']),
        'runs_without_stage_record': sorted(name for name, result in scans.items()
                                            if result['supplied_basis'] != 'stage_record'),
        'note': 'A flag needs human reading; a clean scan does not prove the keys were never read. Without a '
                'stage record the scan trusts folder names to tell supplied files from authored ones.'}
    missing_reports = sorted(f'{dossier}-{variant}' for dossier, variant in wanted_reports - scored_reports)
    card = {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'inputs': {'runs': str(Path(runs_root)), 'keys': str(Path(keys_dir)), 'indexed': index is not None,
                   'key_files': {name: {'sha256': key['sha256'], 'status': key['status']}
                                 for name, key in sorted(keys.items())}},
        'suite': {'dossiers': len(keys), 'reports': len(wanted_reports), 'runs_scored': len(per_run),
                  'claims': len(claims), 'claim_observations': len(rows),
                  'atomic_findings': sum(run['atomic_findings'] for run in per_run),
                  'lean_checks_passed': sum(run['lean_checks_passed'] for run in per_run),
                  'lean_checks': sum(run['lean_checks'] for run in per_run)},
        'blindness': attestation,
        'coverage': {'missing_reports': missing_reports, 'missing_runs': missing_runs,
                     'missing_claims': [f"{r['run_id']}:{r['claim_id']}" for r in rows if r['observed'] == 'missing'],
                     'extra_anchors': extra_anchors, 'duplicate_runs': duplicate_runs},
        'semantic': {name: semantic_metrics(items) for name, items in parts.items() if items},
        'baselines': baselines(claims) if claims else None,
        'formal': formal_metrics(rows, claims),
        'evidence': evidence_metrics(rows),
        'argument': {'scored': False,
                     'observed_counts': dict(Counter(s for row in rows for s in row['argument_statuses'])),
                     'note': 'Keys carry no expected argument_status, so the argument audit is not scored.'},
        'repeatability': repeatability(rows, wanted_reports),
        'per_run': per_run,
        'limits': [
            'Agreement is measured against the answer keys. Keys written by the fixture authors are '
            'expectations, not independently certified truth.',
            'Intervals are 95% Wilson intervals that treat claims as independent. Claims share dossiers, '
            'so the real uncertainty is wider.',
            'Blindness is declared by the caller, not observed. Runs audited with the answer keys readable '
            'cannot be made blind afterwards.',
            'The scorer reads recorded findings and proof results. It does not rerun Lean or the audits.']}
    if not claims:
        raise ScoreError('No completed run could be scored')
    card['gate'] = gate(card, thresholds) if thresholds is not None else None
    return card


def headline(card):
    overall, summary = card['semantic']['all_claims'], card['baselines']['summary']
    lines = [f"runs scored {card['suite']['runs_scored']}, claims {card['suite']['claims']}, "
             f"blindness {card['blindness']['status']}"]
    for name in ('all_claims', 'flawed_reports', 'clean_reports'):
        if name in card['semantic']:
            block, base = card['semantic'][name]['accuracy'], summary[name]
            lines.append(f"{name}: accuracy {block['count']}/{block['of']}, "
                         f"best constant ({base['best_constant']}) {base['best_constant_accuracy']:.3f}")
    for name in ('error_detection', 'false_support', 'false_refutation', 'echo_asserted', 'echo_withheld',
                 'withheld_without_conflict'):
        block = overall[name]
        if block['ci95']:
            lines.append(f"{name}: {block['count']}/{block['of']}, 95% interval "
                         f"{block['ci95'][0]:.3f} to {block['ci95'][1]:.3f}")
    lines.append(f"false formal certainty: {card['formal']['false_formal_certainty']['count']}")
    lines.append(f"contamination flags: {len(card['blindness']['contamination']['flagged_runs'])} runs")
    if card['gate']:
        failed = [check['check'] for check in card['gate']['checks'] if not check['passed']]
        lines.append('gate: passed' if card['gate']['passed'] else 'gate: NOT passed (' + ', '.join(failed) + ')')
    return '\n'.join(lines)


def main():
    base = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--runs', type=Path, default=base / 'validation/runs')
    parser.add_argument('--keys', type=Path, required=True, help='Directory of answer-key JSON files')
    parser.add_argument('--index', type=Path, help='JSON mapping run folder names to dossier, variant and repeat')
    parser.add_argument('--records', type=Path,
                        help='Records folder written by `blindness.py stage`; supplies the index and staged hashes')
    parser.add_argument('--agent-root', action='append', default=[], type=Path,
                        help='A directory the audited agent could read during the runs (repeatable)')
    parser.add_argument('--protected', action='append', default=[], type=Path,
                        help='Further answer-bearing paths that must lie outside every agent root')
    parser.add_argument('--gate', type=Path, help='Thresholds JSON; exit 1 unless every check passes')
    parser.add_argument('--no-scan', action='store_true', help='Skip the key-material scan of run folders')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        index = json.loads(args.index.read_text(encoding='utf-8')) if args.index else None
        thresholds = json.loads(args.gate.read_text(encoding='utf-8')) if args.gate else None
        protected = args.protected + ([args.index] if args.index else [])
        card = score(args.runs.resolve(), args.keys.resolve(), index, args.agent_root, protected,
                     scan=not args.no_scan, thresholds=thresholds, records=args.records)
    except ScoreError as error:
        raise SystemExit(f'Cannot score: {error}')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(card, indent=2) + '\n', encoding='utf-8')
    print(headline(card))
    raise SystemExit(1 if card['gate'] and not card['gate']['passed'] else 0)


if __name__ == '__main__':
    main()
