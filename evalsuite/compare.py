#!/usr/bin/env python3
"""Compare the arms of one blind campaign on the same claims. Calls no model.

    python3 evalsuite/compare.py --campaign OUT --keys SEALED --baseline plain --candidate skill-lean
    python3 evalsuite/compare.py --campaign OUT --keys SEALED --ladder plain,skill-no-lean,skill-lean

A claim counts as right only when every review of its report got it right. A claim that got no
answer in some review, because the review handed in no valid sheet or the sheet left the claim
out, counts against its arm on every measure: it is not judged right, a false claim is not
caught, a wrong claim counts as let through and a true claim as not confirmed. Leaving such
claims out would favour the arm that fails on the hard cases.

The difference between two arms is reported with a 95% interval from resampling whole
dossiers, because the claims of one dossier share its documents and are not independent.
Answer keys are read here and by scorecard.py, never by the runner.
"""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scorecard  # noqa: E402

MEASURES = (('accuracy', 'Claims judged right'), ('error_detection', 'False claims caught'),
            ('gap_recognition', 'Unestablished claims recognised'),
            ('false_support', 'Wrong claims accepted or unanswered'),
            ('false_refutation', 'True claims rejected or unanswered'),
            ('echo_asserted', 'Packet’s word taken over its files'),
            ('wrong_definite_without_conflict', 'Wrong definite where its word agrees'),
            ('echo_withheld', 'Withheld where its word is wrong'),
            ('withheld_control', 'Withheld where its word is right'))
PRIMARY = 'accuracy'
MIN_DOSSIERS = 10


class CompareError(Exception):
    """The campaign cannot be compared as requested."""


def arm_reviews(campaign, arm, keys):
    """Every staged review of one arm, with one row per expected claim. A review without findings
    gets rows marked missing, so its claims stay in every denominator."""
    base = Path(campaign) / arm
    index_path = base / 'records/index.json'
    if not index_path.is_file():
        raise CompareError(f'Arm "{arm}" has no staged review in {campaign}')
    units = {}
    for path in sorted((Path(campaign) / 'units').glob('*.json')):
        record = json.loads(path.read_text(encoding='utf-8'))
        if record.get('arm') == arm:
            units[record.get('opaque_id')] = record
    reviews = {}
    for run in scorecard.discover_runs(base / 'runs', keys, json.loads(index_path.read_text(encoding='utf-8'))):
        key_rows = [row for row in keys[run['dossier']]['rows'] if row['variant'] == run['variant']]
        bundle = scorecard.read_legacy_bundle(run['path'])
        if bundle is None:
            rows = [{'run_id': run['run_id'], 'dossier': run['dossier'], 'variant': run['variant'],
                     'repeat': run['repeat'], 'claim_id': row['claim_id'], 'expected': row['semantic_state'],
                     'echo': row.get('echo_state'), 'kind': row.get('claim_kind'),
                     'lures': scorecard.reader_states(row),
                     'observed': 'missing', 'matches': False, 'formal_statuses': []} for row in key_rows]
        else:
            rows = scorecard.observe(run, bundle, key_rows)
        unit = units.get(run['run_id'], {})
        reviews[(run['dossier'], run['variant'], run['repeat'])] = {
            'rows': rows, 'failed': bundle is None, 'ended': unit.get('ended'), 'seconds': unit.get('seconds'),
            'suspect': bool(unit.get('suspect')) or unit.get('ended') == 'launch_error'}
    return reviews


def collapse(reviews, labels, strict=False):
    """One record per claim over the chosen reviews. With `strict`, a review that did not end on its own
    (time limit, or a non-zero exit) is treated as if it had handed in nothing."""
    rows = []
    for label in labels:
        review = reviews[label]
        if strict and not review['failed'] and review['ended'] != 'exit_0':
            rows += [dict(row, observed='missing', matches=False, formal_statuses=[]) for row in review['rows']]
        else:
            rows += review['rows']
    return scorecard.collapse(rows)


def unanswered(claim):
    return 'missing' in claim['observed_by_repeat']


def tally(claim, kinds=frozenset(), definite_kinds=frozenset()):
    """(hits, chances) of one claim for every measure. A claim left unanswered in any review takes the worse
    outcome on each: for the measures where less is better, silence counts like the error itself. `kinds` are
    the kinds of claim on which any flawed reader withholds a verdict (`scorecard.withholding_kinds`)."""
    expected, silent, role = claim['expected'], unanswered(claim), scorecard.echo_role(claim, kinds)
    flawed, unsettled, true = expected in scorecard.ERROR_STATES, expected == 'undetermined', expected == 'supported'
    return {'accuracy': (int(claim['all_match']), 1),
            'error_detection': (int(claim['all_error']), 1) if flawed else (0, 0),
            'gap_recognition': (int(claim['all_match']), 1) if unsettled else (0, 0),
            'false_support': (int(claim['any_supported'] or silent), 1) if not true else (0, 0),
            'false_refutation': (int(claim['any_error'] or silent), 1) if true else (0, 0),
            # Believing what the packet says about itself gives a definite wrong verdict on the first kind of
            # claim and `undetermined` on the second. The third is the control for the second: the same kinds
            # of claim where the packet's statement has no bearing, so `undetermined` there is caution alone.
            'echo_asserted': (int(claim['any_echo'] or silent), 1) if role == 'asserted' else (0, 0),
            'wrong_definite_without_conflict': (int(scorecard.wrong_definite(claim) or silent), 1)
            if scorecard.definite_control(claim, definite_kinds) else (0, 0),
            'echo_withheld': (int(claim['any_echo'] or silent), 1) if role == 'withheld' else (0, 0),
            'withheld_control': (int(claim['any_undetermined'] or silent), 1) if role == 'control' else (0, 0)}


def by_dossier(claims):
    """Per dossier, the summed (hits, chances) of every measure."""
    totals = defaultdict(lambda: {name: [0, 0] for name, _ in MEASURES})
    kinds = scorecard.withholding_kinds(claims)
    definite_kinds = scorecard.asserting_kinds(claims)
    for claim in claims:
        for name, (hits, chances) in tally(claim, kinds, definite_kinds).items():
            totals[claim['dossier']][name][0] += hits
            totals[claim['dossier']][name][1] += chances
    return totals


def rates(totals, dossiers):
    result = {}
    for name, _ in MEASURES:
        hits = sum(totals[d][name][0] for d in dossiers)
        chances = sum(totals[d][name][1] for d in dossiers)
        result[name] = {'count': hits, 'of': chances, 'rate': hits / chances if chances else None}
    return result


def binomial_two_sided(first, second):
    """Exact two-sided sign test: how surprising a split at least this uneven is when both sides are equally likely."""
    total = first + second
    if not total:
        return 1.0
    tail = sum(math.comb(total, k) for k in range(min(first, second) + 1)) / 2 ** total
    return min(1.0, 2 * tail)


def compare(baseline, candidate, bootstrap=10000, seed=1, strict=False):
    """Paired comparison of two arms over the reviews both of them staged."""
    labels = sorted(set(baseline) & set(candidate))
    if not labels:
        raise CompareError('The two arms share no review of the same report and repeat')
    claims_a, claims_b = collapse(baseline, labels, strict), collapse(candidate, labels, strict)
    totals_a, totals_b = by_dossier(claims_a), by_dossier(claims_b)
    dossiers = sorted(set(totals_a) | set(totals_b))
    rate_a, rate_b = rates(totals_a, dossiers), rates(totals_b, dossiers)

    def difference(chosen):
        a, b = rates(totals_a, chosen), rates(totals_b, chosen)
        return {name: None if a[name]['rate'] is None or b[name]['rate'] is None else b[name]['rate'] - a[name]['rate']
                for name, _ in MEASURES}
    point, rng, draws = difference(dossiers), random.Random(seed), defaultdict(list)
    if len(dossiers) > 1:
        for _ in range(bootstrap):
            for name, value in difference([rng.choice(dossiers) for _ in dossiers]).items():
                if value is not None:
                    draws[name].append(value)
    differences = {}
    for name, _ in MEASURES:
        values = sorted(draws[name])
        # A resample that holds no claim of a kind gives no value; an interval built from the rest would be too narrow.
        usable = len(values) >= 0.95 * bootstrap and point[name] is not None
        interval = [values[int(0.025 * len(values))], values[min(len(values) - 1, int(0.975 * len(values)))]] if usable else None
        differences[name] = {'value': point[name], 'ci95': interval}
    right_a = {(c['dossier'], c['variant'], c['claim_id']): c['all_match'] for c in claims_a}
    right_b = {(c['dossier'], c['variant'], c['claim_id']): c['all_match'] for c in claims_b}
    only_b = sum(right_b[key] and not right_a[key] for key in right_a)
    only_a = sum(right_a[key] and not right_b[key] for key in right_a)
    better = sum(rates(totals_b, [d])['accuracy']['rate'] > rates(totals_a, [d])['accuracy']['rate'] for d in dossiers)
    worse = sum(rates(totals_b, [d])['accuracy']['rate'] < rates(totals_a, [d])['accuracy']['rate'] for d in dossiers)

    def reviews(arm, claims):
        chosen = [arm[label] for label in labels]
        return {'reviews': len(chosen), 'failed': sum(r['failed'] for r in chosen),
                'suspect': sum(r['suspect'] for r in chosen),
                'ended_by_time_limit': sum(r['ended'] == 'timeout' for r in chosen),
                'not_ended_on_their_own': sum(not r['failed'] and r['ended'] != 'exit_0' for r in chosen),
                'claims_unanswered': sum(unanswered(claim) for claim in claims),
                'answers_missing_from_sheets': sum(row['observed'] == 'missing' for r in chosen if not r['failed']
                                                   for row in r['rows']),
                'answers_expected_from_sheets': sum(len(r['rows']) for r in chosen if not r['failed']),
                'lures_by_reader': scorecard.lures_by_reader(claims, count_silence=True),
                'minutes': round(sum(r['seconds'] or 0 for r in chosen) / 60, 1)}
    return {'reviews_compared': len(labels), 'dossiers': len(dossiers), 'claims': len(claims_a),
            'repeats': sorted({label[2] for label in labels}),
            'baseline': dict(rate_a, **reviews(baseline, claims_a)), 'candidate': dict(rate_b, **reviews(candidate, claims_b)),
            'difference': differences,
            'claims_only_candidate_right': only_b, 'claims_only_baseline_right': only_a,
            'mcnemar_p': binomial_two_sided(only_b, only_a),
            'dossiers_candidate_better': better, 'dossiers_baseline_better': worse,
            'dossier_sign_test_p': binomial_two_sided(better, worse)}


def warnings(campaign, keys, baseline, candidate, result):
    """Conditions that weaken a comparison. Those marked exploratory mean it is not a campaign result."""
    notes = []

    def add(text, exploratory=False):
        notes.append({'text': text, 'exploratory': exploratory})
    record = campaign.get('record')
    if not record:
        add('The campaign folder has no campaign.json, so nothing shows the arms ran under the same settings.', True)
    elif record.get('agent_state') != 'private_home_per_review':
        add('Reviews shared the agent state of other sessions, so a review could read the history of another.', True)
    probes = campaign.get('probes') or []
    if not probes or any(probe.get('status') != 'passed' for probe in probes):
        add('Reviews were started without a passed sandbox probe, so nothing checked that the answer keys were out '
            'of the agent\'s reach.', True)
    left_out = len(set(baseline) ^ set(candidate))
    if left_out:
        add(f'{left_out} review(s) exist for only one of the two arms and were left out. Reviews that are hard to '
            'finish are the ones most likely to be missing.', True)
    wanted = {(dossier, row['variant']) for dossier, key in keys.items() for row in key['rows']}
    covered = {(label[0], label[1]) for label in set(baseline) & set(candidate)}
    if wanted - covered:
        add(f'{len(wanted - covered)} report(s) of the answer keys were reviewed by neither arm or by only one.', True)
    counts = defaultdict(set)
    for label in set(baseline) & set(candidate):
        counts[label[:2]].add(label[2])
    fewest = min((len(repeats) for repeats in counts.values()), default=0)
    if fewest < 3:
        add(f'Some reports were reviewed {fewest} time(s) per arm. Three is the minimum for measuring run-to-run variation.')
    if record and fewest < record.get('repeats', 0):
        add(f'The campaign planned {record["repeats"]} review(s) of each report per arm, and some reports have only '
            f'{fewest} in both arms. The campaign is not finished.', True)
    if result['dossiers'] < MIN_DOSSIERS:
        add(f'Only {result["dossiers"]} dossier(s): an interval from so few is unreliable and usually too narrow.', True)
    authored = sorted(name for name, key in keys.items() if key['status'] != 'computed_by_generator')
    if authored:
        add(f'{len(authored)} answer key(s) are author expectations, not computed keys.')
    for name, arm in (('baseline', result['baseline']), ('candidate', result['candidate'])):
        if arm['failed']:
            add(f'{arm["failed"]} of {arm["reviews"]} review(s) of the {name} handed in no valid answer sheet.')
        if arm['claims_unanswered']:
            add(f'{arm["claims_unanswered"]} of {result["claims"]} claim(s) got no answer from the {name} in at least one '
                'review. They count against it on every measure.')
        if arm['answers_missing_from_sheets'] > 0.1 * arm['answers_expected_from_sheets']:
            add(f'The answer sheets of the {name} leave out {arm["answers_missing_from_sheets"]} of '
                f'{arm["answers_expected_from_sheets"]} expected answers. That is more likely a sheet that names the claims '
                "differently from the report than a reviewer with nothing to say, so check the sheets before reading "
                'this as a difference between the arms.', True)
        if arm['suspect']:
            add(f'{arm["suspect"]} review(s) of the {name} ended with a crash or could not start, and left no answer '
                'sheet. That looks like an outage, not like a weak reviewer, and an outage charged to one arm is not a '
                'difference between the arms.', True)
    return notes


def position(interval):
    return 'none' if not interval else 'above' if interval[0] > 0 else 'below' if interval[1] < 0 else 'includes'


SENTENCE = {'none': 'There are too few dossiers for an interval.',
            'above': 'The interval lies above zero: on these cases the candidate judged more claims right.',
            'below': 'The interval lies below zero: on these cases the baseline judged more claims right.',
            'includes': 'The interval includes zero: these cases do not show a difference.'}


def percent(value):
    return 'n/a' if value is None else f'{value * 100:.1f}%'


def points(block):
    if block['value'] is None:
        return 'n/a'
    text = f'{block["value"] * 100:+.1f} points'
    return text + (f' (95% interval {block["ci95"][0] * 100:.1f} to {block["ci95"][1] * 100:.1f})' if block['ci95']
                   else ' (no interval)')


def render(names, result, strict, notes):
    a, b = result['baseline'], result['candidate']
    exploratory = any(note['exploratory'] for note in notes)
    lines = [f'Candidate "{names[1]}" against baseline "{names[0]}": {result["claims"]} claims from '
             f'{result["dossiers"]} dossiers, {result["reviews_compared"]} reviews per arm.', '',
             f'{"Measure":38}{"baseline":>20}{"candidate":>20}   difference']
    for name, label in MEASURES:
        cell = lambda arm: f'{percent(arm[name]["rate"])} ({arm[name]["count"]}/{arm[name]["of"]})'  # noqa: E731
        lines.append(f'{label:38}{cell(a):>20}{cell(b):>20}   {points(result["difference"][name])}')
    for arm_name, arm in zip(names, (a, b)):
        for reader, block in arm['lures_by_reader'].items():
            cells = [f'{kind} {percent(value["rate"])} ({value["count"]}/{value["of"]})'
                     for kind, value in block.items()]
            lines.append(f'Reader "{reader}", arm "{arm_name}": ' + '; '.join(cells))
    lines += [f'{"Reviews with no valid sheet":38}{a["failed"]:>20}{b["failed"]:>20}',
              f'{"Claims left unanswered":38}{a["claims_unanswered"]:>20}{b["claims_unanswered"]:>20}',
              f'{"Reviews ended by the time limit":38}{a["ended_by_time_limit"]:>20}{b["ended_by_time_limit"]:>20}',
              f'{"Run time, minutes":38}{a["minutes"]:>20}{b["minutes"]:>20}', '',
              f'Claims only the candidate judged right: {result["claims_only_candidate_right"]}; only the baseline: '
              f'{result["claims_only_baseline_right"]} (exact McNemar p = {result["mcnemar_p"]:.3g}; claims of one '
              'dossier are not independent, so read this with the dossier count).',
              f'Dossiers where the candidate judged more claims right: {result["dossiers_candidate_better"]}; fewer: '
              f'{result["dossiers_baseline_better"]} (exact sign test p = {result["dossier_sign_test_p"]:.3g}).',
              ('Exploratory reading, not a campaign result. ' if exploratory else '')
              + SENTENCE[position(result['difference'][PRIMARY]['ci95'])]]
    if strict:
        lines.append('Strict reading, with every review that ran into the time limit or ended with an error counted as '
                     f'failed: {percent(strict["baseline"][PRIMARY]["rate"])} against '
                     f'{percent(strict["candidate"][PRIMARY]["rate"])}, {points(strict["difference"][PRIMARY])}. '
                     + SENTENCE[position(strict['difference'][PRIMARY]['ci95'])])
    lines += [f'Warning: {note["text"]}' for note in notes]
    return '\n'.join(lines)


def compare_pair(campaign, keys, reviews, baseline, candidate, bootstrap, seed):
    result = compare(reviews[baseline], reviews[candidate], bootstrap, seed)
    late = result['baseline']['not_ended_on_their_own'] or result['candidate']['not_ended_on_their_own']
    strict = compare(reviews[baseline], reviews[candidate], bootstrap, seed, strict=True) if late else None
    notes = warnings(campaign, keys, reviews[baseline], reviews[candidate], result)
    if strict and position(strict['difference'][PRIMARY]['ci95']) != position(result['difference'][PRIMARY]['ci95']):
        notes.append({'text': 'The two readings disagree: the result depends on reviews that did not end on their own.',
                      'exploratory': True})
    return {'baseline_arm': baseline, 'candidate_arm': candidate, 'exploratory': any(n['exploratory'] for n in notes),
            **result, 'strict_reading': strict, 'warnings': [n['text'] for n in notes]}, notes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--campaign', type=Path, required=True, help='The --out folder of run_blind.py')
    parser.add_argument('--keys', type=Path, required=True, help='The sealed answer keys')
    parser.add_argument('--baseline')
    parser.add_argument('--candidate')
    parser.add_argument('--ladder', help='Comma-separated arms; each is compared with the next, and the first with the last')
    parser.add_argument('--bootstrap', type=int, default=10000)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.ladder:
            arms = [name for name in args.ladder.split(',') if name]
            pairs = list(zip(arms, arms[1:])) + ([(arms[0], arms[-1])] if len(arms) > 2 else [])
        else:
            pairs = [(args.baseline, args.candidate)]
        if not pairs or any(not a or not b or a == b for a, b in pairs):
            raise CompareError('Pass --baseline and --candidate, or --ladder with at least two different arms')
        if args.bootstrap < 1000:
            raise CompareError('--bootstrap needs at least 1000 resamples')
        try:
            keys = scorecard.load_keys(args.keys)
        except scorecard.ScoreError as error:
            raise CompareError(str(error))
        record_path = args.campaign / 'campaign.json'
        probes_path = args.campaign / 'probes.json'
        campaign = {'record': json.loads(record_path.read_text(encoding='utf-8')) if record_path.is_file() else None,
                    'probes': json.loads(probes_path.read_text(encoding='utf-8')) if probes_path.is_file() else []}
        reviews = {arm: arm_reviews(args.campaign, arm, keys) for arm in sorted({arm for pair in pairs for arm in pair})}
        results = []
        for baseline, candidate in pairs:
            result, notes = compare_pair(campaign, keys, reviews, baseline, candidate, args.bootstrap, args.seed)
            results.append(result)
            print(render((baseline, candidate), result, result['strict_reading'], notes) + '\n')
    except (CompareError, scorecard.ScoreError) as error:
        print(f'Cannot compare: {error}', file=sys.stderr)
        return 1
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({'schema_version': 1, 'setup': campaign['record'], 'comparisons': results},
                                          indent=2) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
