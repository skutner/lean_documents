#!/usr/bin/env python3
"""Sweeps of minutes edition 8 over many seeds. Calls no model. The gate does not run it (`unittest discover` takes `test*.py` only).

    python3 tests/sweep_minutes_edition_8.py [--first SEED] [--against-edition-6 N] [--books N] [--jobs N]

For the seeds FIRST, FIRST + 1, ... (index 1):

  A4 (the first N = 20 seeds): edition 6 against edition 8 on each seed. R6 holds when edition 8 hands out edition 6's world: the same
     event log, passages, names and readers, the same rows of the key (so the same `lure_states`), the same two reports and the same
     `procedure.md`, and the sources differ only in the paragraphs of the quiet returns the key lists, and in each of those only in the
     sentence of the return (the paragraph is edition 6's with that sentence replaced). A seed for which edition 8 redrew is listed with
     the reason the generator gave (`WHY`) and is not counted as a failure unless more than 1 seed in 20 does it.
  A5 (the first M = 30 seeds, edition 8 alone): the number of quiet returns (a third of the eligible ones, rounded, 6 at least and half of
     the returns at most), each one eligible by this module's own reading of the key's facts, no form twice in a book, and each one read
     back by the test module's reader as a return of the project and the meeting the log gives.

Exit status 0 when every check holds and at most 1 seed in 20 redrew; 1 otherwise. One build takes about 4 seconds, and edition 6 and
edition 8 are each built once per seed, so 30 seeds take a few minutes with `--jobs` (default 4) processes."""
import argparse
import concurrent.futures
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
sys.path.insert(0, str(ROOT / 'tests'))
sys.dont_write_bytecode = True
import big_minutes
import generate
import generate_big
import test_evalsuite_big_minutes as T


def build(edition, seed):
    """The dossier with its facts under `edition`, and what each attempt of the generator came to: [(handed out, why not)]."""
    log, real = [], big_minutes.attempt

    def logged(rng, which):
        big_minutes.WHY.pop('why', None)
        case = real(rng, which)
        log.append((case is not None, big_minutes.WHY.get('why', 'other')))
        return case
    big_minutes.attempt = logged
    try:
        with T.edition_env(edition):
            return T.build_with_facts(seed, 1), log
    finally:
        big_minutes.attempt = real


def spliced(six, eight):
    """Every source paragraph of the two dossiers that differs, by (file, number): edition 8's is edition 6's with the sentence of one
    return replaced (checked by this module's reader on both: the return's sentence starts at the same place and the rest is equal)."""
    book6, book8 = T.Book(T.sources_of(six)), T.Book(T.sources_of(eight))
    quiet = [f for f in book8.facts if f['t'] == 'return' and f['quiet']]
    problems, differing = [], set()
    for name in T.sources_of(six):
        for number, (a, b) in enumerate(zip(T.sources_of(six)[name].split('\n\n'), T.sources_of(eight)[name].split('\n\n'))):
            if a != b:
                differing.add((name, number))
    index = {(T.VOLUMES.index(name), number): (name, number) for name, number in differing}
    found = set()
    for f8 in quiet:
        old = [f for f in book6.facts if f['t'] == 'return' and f['key'] == f8['key'] and f['p'] == f8['p'] and f['when'] == f8['when']]
        if len(old) != 1 or old[0]['order'] != f8['order']:
            problems.append(f'{f8["key"]}: {len(old)} edition 6 returns read there, or at another place')
            continue
        a, b = book6.blocks[f8['key']], book8.blocks[f8['key']]
        start = f8['order'][1]
        if b != a[:start] + b[start:f8['end']] + a[old[0]['end']:]:
            problems.append(f'{f8["key"]}: the paragraph differs from edition 6 outside the sentence of the return')
        found.add(f8['key'])
    if set(index) != found:
        problems.append(f'paragraphs that differ {sorted(index)[:3]} are not the quiet returns\' {sorted(found)[:3]}')
    return problems


def one_seed(seed, against_six):
    """The checks on one seed; returns a dict of what was found (`problems` is empty when every check holds)."""
    row = {'seed': seed, 'problems': []}
    eight, log8 = build(8, seed)
    f8 = eight['key']['facts']
    row.update(attempts8=len(log8), why8=[w for ok, w in log8 if not ok])
    try:
        row['returns'], row['eligible'], row['quiet'] = T.quiet_book_rules(eight)
    except (T.Failed, AssertionError, KeyError, ValueError, IndexError) as error:
        row['problems'].append(f'edition 8 book: {error}')
    if against_six:
        six, log6 = build(6, seed)
        f6 = six['key']['facts']
        row.update(attempts6=len(log6), why6=[w for ok, w in log6 if not ok])
        same = all(f6[part] == f8[part] for part in ('events', 'passages', 'names', 'readers')) and sorted(six['files']) == sorted(eight['files'])
        row['redrew'] = not same
        if same:
            for name in ('report-correct.md', 'report-mixed.md', 'sources/procedure.md'):
                if six['files'][name] != eight['files'][name]:
                    row['problems'].append(f'R6/R9: {name} differs')
            if six['key']['expectations'] != eight['key']['expectations']:
                row['problems'].append('R6/R8: the key\'s rows (lure_states included) differ')
            try:
                row['problems'] += [f'R6: {p}' for p in spliced(six, eight)]
            except (T.Failed, AssertionError, KeyError, ValueError, IndexError) as error:
                row['problems'].append(f'R6: {error}')
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--first', type=int, default=101, help='the first seed (default 101)')
    parser.add_argument('--against-edition-6', type=int, default=20, help='seeds compared with edition 6 (A4, default 20)')
    parser.add_argument('--books', type=int, default=30, help='seeds of edition 8 checked on their own (A5, default 30)')
    parser.add_argument('--jobs', type=int, default=4, help='processes (default 4)')
    args = parser.parse_args()
    generate_big.register()
    seeds = list(range(args.first, args.first + max(args.books, args.against_edition_6)))
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs, initializer=generate_big.register) as pool:
        rows = list(pool.map(one_seed, seeds, [n < args.against_edition_6 for n in range(len(seeds))]))
    bad = 0
    for row in rows:
        redrew = row.get('redrew')
        held = 'n/a (not compared)' if 'redrew' not in row else 'no: edition 8 redrew' if redrew else 'yes' if not row['problems'] else 'FAILED'
        print(f'seed {row["seed"]}: returns {row.get("returns")} eligible {row.get("eligible")} quiet {row.get("quiet")}; attempts '
              f'6: {row.get("attempts6", "-")} 8: {row["attempts8"]}; R6 held: {held}'
              + (f'; edition 8 refused: {row["why8"]}' + (f' (edition 6: {row["why6"]})' if row.get('why6') else '') if row['why8'] or row.get('why6') else ''))
        for problem in row['problems']:
            print(f'    PROBLEM {problem}')
        bad += bool(row['problems'])
    compared = [r for r in rows if 'redrew' in r]
    redrew = [r['seed'] for r in compared if r['redrew']]
    print(f'edition 8 against edition 6: {len(compared) - len(redrew)} of {len(compared)} seeds held R6; redrew: {redrew or "none"}')
    print(f'edition 8 alone: {len(rows)} books, {sum(not r["problems"] for r in rows)} without a problem')
    if len(redrew) * 20 > len(compared):
        print(f'STOP: edition 8 redrew on {len(redrew)} of {len(compared)} seeds, more than 1 in 20')
        bad += 1
    raise SystemExit(1 if bad else 0)


if __name__ == '__main__':
    main()
