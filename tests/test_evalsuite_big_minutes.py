"""Independent checks of the minutes family (the minute book of a grants committee).

The reading side of this file uses the family's phrase inventory (`big_minutes.INVENTORY`, constants only) and the
rendered Markdown. It never reads `case['base']`, a claim's predicate or the event log to recover facts: a parser
built from the inventory finds the events in the text, and an evaluator written here recomputes the verdict of every
statement of both reports, and of every flawed reader. The event log, the passage map and the `state` function are
checked against that reading in their own tests.

Editions 3 and 4 (BIG_MINUTES_EDITION=3 or 4) add statements about sums: over three, five or eight named projects, over every
project up to a meeting, and over every project across a span of meetings. The same reading side checks them: a reader of the
sum frames built from `INVENTORY['sum_statements']` and `['sum_list']`, and the same evaluator, which adds the per-project figures
it already computes (the truth, and each flawed reader's own). The expected value of every sum, and every flawed reader's sum,
comes from this module's parser and evaluator on the rendered text, never from `answer`, `sum_value`, `state`, `case['base']`, a
claim's predicate or the key; the hand-written logs carry values written down from the procedure's rules. `CHECKS3` is the check list of
editions 3 and 4; the heavy sweeps run through `--verify` (`BIG_MINUTES_EDITION=3 python3 tests/test_evalsuite_big_minutes.py
--verify SEED 1`), a few dossiers in the unit tests.

Edition 5 (BIG_MINUTES_EDITION=5) adds sums on the award side: over eight named projects (`asum8`), over every project up to an
earlier meeting (`aupto`) and up to the last meeting (`abook`), of the award in force (`award`) or of the unpaid balance (`balance`).
The same reading side recomputes them from the text: a project that holds no award in force at the close, or whose award a reading
cannot establish, adds nothing to either figure. `CHECKS5` is its check list. The hand-written logs of the award side
(`HAND_AWARD_CASES`, `HAND_AWARD_SUBSETS`, `HAND_AWARD_READERS`) carry values written down from the procedure's rules.

Edition 6 (BIG_MINUTES_EDITION=6) is edition 5's text, procedure, interpreter and readers in a dense world: 36 to 40 projects over 44 to
48 meetings, 16 to 20 payments to a project where the calendar allows, 450 to 700 payments in all. A dossier of it is expensive (`--verify`
under edition 6 takes 10 to 20 minutes, by the load of the machine), so the unit suite builds ONE (`facts_built`, through the cache), reads it once
with this module's own parser (`dense_view`) and holds that reading to the rules of the edition: the independent round trip of every
statement, the quota table and the rules of choice written out in this module, the readers wrong on four statements, the world and the
density (`world6`), what the amounts of the text must be (`dense_rules`: whole, at least 1,000, no close with more paid than awarded,
regular first instalments, each way of stating an amount used once), every event told once with the values of the log
(`events_told`), this module's evaluator against the interpreter's `state` at every close for the truth and the four readers
(`whole_base_agrees`), the procedure (that of edition 5) and the same bytes in a second process. `CHECKS6` is the check list of
`--verify` (all of `CHECKS5` and these, run on one more dossier). Editions 2 to 5 are pinned as they were at commit 5d1ca73d.

Item 9 of the fifth-edition design (the unchanged files), a command and not a test of this tree, run from `lean_documents`:
    git diff --quiet 7e4beca9 -- evalsuite/check_shown.py evalsuite/generate_big.py evalsuite/generate.py evalsuite/run_blind.py \
        evalsuite/prompts/task-shown-sums.md evalsuite/arms-shown-sums.json
(exit status 0 when edition 6 was built, and the same against 5d1ca73d, the commit edition 6 is built on). It does not exit 0 now: on main
`run_blind.py` already differs from both commits, and with edition 8 `generate_big.py` differs too, because it carries `quiet_returns` into the key's facts. What holds
now is that `check_shown.py`, `generate.py` and the two data files are those of the commit, and `generate_big.py` is the commit's file plus exactly three lines (`EDITION8_KEY_LINES`), each once, and nothing else changed.
`test_the_six_files_are_those_of_commits_7e4beca9_and_5d1ca73d` runs both where those commits are in the repository's history (on main it
fails on `run_blind.py`, which differs from both commits, before and after edition 8).

Edition 8 (BIG_MINUTES_EDITION=8) is edition 6 with a third of the eligible returns told in quiet words (`QUIET_STOCK`, `quiet_form_rules`,
`quiet_book_rules`, `QuietReturnsTest`; 20 and 30 seed sweeps: `tests/sweep_minutes_edition_8.py`, which the gate does not discover). Every
check of edition 6 runs on it (`checks_of` treats 8 as 6), and `Book` reads a quiet return as a return (`quiet_frames`, from the inventory).
"""
import collections
import contextlib
import copy
import fractions
import functools
import hashlib
import io
import json
import os
from pathlib import Path
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
sys.dont_write_bytecode = True
import generate
import generate_big
import big_minutes


_FAMILIES_BEFORE = {}


def setUpModule():
    """The large families are registered for the tests of this module only; `tearDownModule` puts the list back."""
    _FAMILIES_BEFORE.update(generate.HARD_FAMILIES)
    generate_big.register()


def tearDownModule():
    generate.HARD_FAMILIES.clear()
    generate.HARD_FAMILIES.update(_FAMILIES_BEFORE)      # the list as it was found, for the modules that follow

INV = big_minutes.INVENTORY
SEEDS = (1, 2, 3, 4, 5)
VOLUMES = ('minute-book-1.md', 'minute-book-2.md', 'minute-book-3.md', 'minute-book-4.md', 'minute-book-5.md')   # edition 1 has three


def volumes_in(sources):
    """The minute books of a dossier (three in edition 1; four or five in edition 2), in order."""
    return [name for name in VOLUMES if name in sources]


def per_edition(function):
    """Remember each result by the edition that BIG_MINUTES_EDITION names when the function is called (1 when not set)."""
    results = {}

    @functools.wraps(function)
    def wrapper(*args):
        key = (big_minutes.current_edition(),) + args
        if key not in results:
            results[key] = function(*args)
        return results[key]
    return wrapper


class edition_env:
    """BIG_MINUTES_EDITION set to `edition` (None: not set) for the length of a block."""

    def __init__(self, edition):
        self.edition = edition

    def __enter__(self):
        self.saved = os.environ.get(big_minutes.EDITION_VARIABLE)
        if self.edition is None:
            os.environ.pop(big_minutes.EDITION_VARIABLE, None)
        else:
            os.environ[big_minutes.EDITION_VARIABLE] = str(self.edition)
        return self

    def __exit__(self, *exc):
        if self.saved is None:
            os.environ.pop(big_minutes.EDITION_VARIABLE, None)
        else:
            os.environ[big_minutes.EDITION_VARIABLE] = self.saved
BUSINESS = ('intro', 'award', 'defer_award', 'vary', 'fell_vary', 'defer_vary', 'correct', 'rescind', 'authorise')
READERS = ('counts_every_mention', 'ignores_corrections', 'counts_motions_as_decisions')


@per_edition
def case(seed, index=1):
    return generate.build_case('minutes', seed, index)


def sources_of(built):
    return {name[len('sources/'):]: text for name, text in built['files'].items() if name.startswith('sources/')}


def report_statements(built, variant):
    return re.findall(r'\*\*C\d+\.\*\* (.*?) Citations?:', built['files'][f'report-{variant}.md'])


def key_rows(built, variant):
    return [r for r in built['key']['expectations'] if r['variant'] == variant]


# ---------- Regular expressions built from the inventory ----------

ATOMS = {'d': r'\d{1,2}', 'od': r'\d{1,2}(?:st|nd|rd|th)', 'Month': '(?:' + '|'.join(INV['months']) + ')',
         'Y': r'\d{4}', 'mm': r'\d{2}', 'dd': r'\d{2}'}


def src(template, atoms, capture):
    out = []
    for part in re.split(r'(\{\w+\})', template):
        if part.startswith('{'):
            body = atoms[part[1:-1]]
            out.append(f'(?P<{part[1:-1]}>{body})' if capture else f'(?:{body})')
        else:
            out.append(re.escape(part))
    return ''.join(out)


DATE_SRC = '(?:' + '|'.join(src(f, ATOMS, False) for f in INV['date_forms']) + ')'
MONTH_YEAR_SRC = src(INV['month_year'], ATOMS, False)
WHEN_SRC = '(?:' + '|'.join(src(f, {'date': DATE_SRC, 'monthyear': MONTH_YEAR_SRC}, False)
                            for _, f in INV['when_forms']) + ')'
SMALL = ('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen '
         'eighteen nineteen').split()
TENS = {w: 10 * i for i, w in enumerate('zero ten twenty thirty forty fifty sixty seventy eighty ninety'.split())}
MONEY_SRC = r'(?:£\d[\d,]*|\d[\d,]* pounds|(?i:[a-z]+(?:[- ][a-z]+)*) pounds)'


def parse_money(text):
    text = text.replace('£', '').replace(' pounds', '').strip().lower()
    if re.fullmatch(r'\d[\d,]*', text):
        return int(text.replace(',', ''))
    total, current = 0, 0
    for word in re.split(r'[- ]+', text):
        if word == 'and':
            continue
        if word in SMALL:
            current += SMALL.index(word)
        elif word in TENS:
            current += TENS[word]
        elif word == 'hundred':
            current *= 100
        elif word == 'thousand':
            total, current = total + current * 1000, 0
        else:
            raise ValueError(f'Unknown number word {word!r} in {text!r}')
    return total + current


def parse_count(text):
    return int(text) if text.isdigit() else SMALL.index(text)


FRACTION_OF = {text: fractions.Fraction(a, b) for text, a, b in INV['fractions']}
ORDINAL_OF = dict(INV['ordinals'])
FRAC_SRC = '(?:' + '|'.join(re.escape(t) for t in FRACTION_OF) + ')'
ORD_SRC = '(?:' + '|'.join(ORDINAL_OF) + ')'
BUILDINGS = {b.title(): b for b in INV['buildings']}
WORK_VERB = {noun: verb for noun, verb in INV['works']}
FULL_SRC = (r'(?P<place>' + '|'.join(re.escape(p) for p in INV['places']) + r') (?P<building>' + '|'.join(BUILDINGS) +
            r') (?P<work>' + '|'.join(re.escape(n) for n in sorted(WORK_VERB, key=len, reverse=True)) + ')')
FULL_RE = re.compile(FULL_SRC)
SPONSOR_SRC = '(?:' + '|'.join(re.escape(s) for s in INV['sponsors']) + ')'


def date_parts(text):
    """(year, month, day or None) of a meeting reference."""
    for form in INV['date_forms']:
        mo = re.search(src(form, ATOMS, True), text)
        if mo:
            g = mo.groupdict()
            month = INV['months'].index(g['Month']) + 1 if 'Month' in g else int(g['mm'])
            day = int(g.get('d') or g.get('dd') or re.sub(r'\D', '', g['od']))
            return int(g['Y']), month, day
    mo = re.search(src(INV['month_year'], ATOMS, True), text)
    return int(mo['Y']), INV['months'].index(mo['Month']) + 1, None


def quiet_frames(refs_src):
    """The 48 sentences of a quiet return (edition 8), written from the inventory's opening, deed and "what" phrase: the words for the
    project, and the month and year of the payment's meeting (captured as `when`, which `meeting_of` resolves to the one meeting of
    that month)."""
    atoms = {'proj': f'(?P<proj>{refs_src})', 'monthyear': f'(?P<when>{MONTH_YEAR_SRC})'}
    out = []
    for opening in INV['quiet_openings']:
        for deed in INV['quiet_deeds']:
            for what in INV['quiet_whats']:
                template = opening + ' ' + deed.replace('{what}', what)
                out.append(re.compile(r'(?:^|(?<=\. ))' + ''.join(
                    atoms[part[1:-1]] if part.startswith('{') else re.escape(part) for part in re.split(r'(\{\w+\})', template)) + r'(?=\s|$)'))
    return out


def amount_phrases(refs_src):
    atoms = {'ord': ORD_SRC, 'frac': FRAC_SRC, 'when': WHEN_SRC, 'proj2': refs_src}
    return {kind: [src(p, atoms, True) for p in phrases] for kind, phrases in INV['amount_phrases'].items()}


class Book:
    """The minute book as read by the inventory: meetings, projects, aliases and the events found in the text."""

    def __init__(self, sources):
        self.meetings, self.blocks = [], {}
        order = []
        for index, name in enumerate(VOLUMES):
            if name not in sources:
                continue
            for number, text in enumerate(sources[name].rstrip('\n').split('\n\n')):
                self.blocks[(index, number)] = text
                order.append((index, number))
        self.order = order
        self.names = sorted({mo.group(0) for text in self.blocks.values() for mo in FULL_RE.finditer(text)})
        self.facts = []
        self.errors = []
        self.alias_of = {}
        for name in self.names:
            mo = FULL_RE.fullmatch(name)
            parts = {'place': mo['place'], 'building': BUILDINGS[mo['building']], 'work': mo['work'].lower(),
                     'verb': WORK_VERB[mo['work']]}
            for form in INV['alias_forms']:
                self.alias_of.setdefault(form.format(**parts), set()).add(name)
        self.sponsor_forms = {INV['sponsor_alias'].format(sponsor=s): s for s in INV['sponsors']}
        self.ordinal_forms = {INV['ordinal_ref'].format(rank=w): n for n, w in enumerate(INV['rank_words'])}
        named = list(self.alias_of) + list(self.sponsor_forms) + self.names + list(self.ordinal_forms)
        named += [f[0].upper() + f[1:] for f in named]
        self.named_src = '(?:' + '|'.join(re.escape(f) for f in sorted(set(named), key=len, reverse=True)) + ')'
        forms = named + list(INV['anaphors']) + [f[0].upper() + f[1:] for f in INV['anaphors']]
        self.refs_src = '(?:' + '|'.join(re.escape(f) for f in sorted(set(forms), key=len, reverse=True)) + ')'
        self.phrases = amount_phrases(self.refs_src)
        atoms = {'proj': f'(?P<proj>{self.refs_src})', 'full': f'(?P<full>{FULL_SRC})', 'sponsor': f'(?P<sponsor>{SPONSOR_SRC})',
                 'money': MONEY_SRC, 'when': WHEN_SRC}
        amt = '(?i:' + '|'.join([MONEY_SRC] + [s for v in INV['amount_phrases'].values() for s in
                                                [src(p, {'ord': ORD_SRC, 'frac': FRAC_SRC, 'when': WHEN_SRC, 'proj2': self.refs_src}, False)
                                                 for p in v]]) + ')'
        delta = '(?:' + '|'.join([MONEY_SRC] + [src(p, {'frac': FRAC_SRC}, False) for p in INV['delta_phrases']]) + ')'
        self.frames = {}
        for kind, frames in INV['frames'].items():
            verb = ('|'.join(INV['raise'] + INV['lower']) if kind == 'vary' else '|'.join(INV['raise_inf'] + INV['lower_inf']))
            local = dict(atoms, amt=amt, delta=delta, verb=f'(?P<verb>{verb})')
            compiled = []
            for frame in frames:
                pieces = []
                for part in re.split(r'(\{\w+\})', frame):
                    if part.startswith('{'):
                        name = part[1:-1]
                        body = local[name]
                        if name in ('amt', 'delta', 'money', 'when'):
                            body = f'(?P<{name}>{body})'
                        pieces.append(body)
                    else:
                        pieces.append(re.escape(part))
                compiled.append(re.compile(r'(?:^|(?<=\. ))' + ''.join(pieces) + r'(?=\s|$)'))
            self.frames[kind] = compiled
        self.quiet = None        # the 48 sentences of a quiet return, compiled by `quiet_res` when a paragraph says "as minuted in" (edition 8)
        heading_res = [re.compile('## ' + src(h, {'date': DATE_SRC}, True).replace('(?P<date>', '(?P<date>')) for h in INV['headings']]
        self.parse(heading_res)

    def quiet_res(self):
        if self.quiet is None:
            self.quiet = quiet_frames(self.refs_src)
        return self.quiet

    # -- reading --
    def parse(self, heading_res):
        index = -1
        by_month = {}
        pending = []
        for key in self.order:
            text = self.blocks[key]
            if text.startswith('## '):
                if not any(h.fullmatch(text) for h in heading_res):
                    self.errors.append(f'unreadable heading {text!r}')
                year, month, day = date_parts(text)
                index += 1
                self.meetings.append((year, month, day))
                by_month[(year, month)] = index
                continue
            if index >= 0 and not text.startswith('###'):
                pending.append((key, index, text))
        self.by_month = by_month
        self.sponsor_of = {}
        intro_at = {}
        for key, m, text in pending:
            for rx in self.frames['intro']:
                for mo in rx.finditer(text):
                    intro_at[mo['sponsor']] = (key, m)
                    self.sponsor_of[INV['sponsor_alias'].format(sponsor=mo['sponsor'])] = (mo['full'], key)
        self.intro_at = intro_at
        for key, m, text in pending:
            self.read_block(key, m, text)
        self.projects = sorted({f['p'] for f in self.facts if f['t'] == 'award'})     # "all projects": those the minutes award

    def meeting_of(self, text):
        year, month, day = date_parts(text)
        m = self.by_month[(year, month)]
        if day is not None and day != self.meetings[m][2]:
            self.errors.append(f'wrong day in {text!r}')
        return m

    def named_in(self, key):
        """The projects named (by name, short form, sponsor or place in the order of backing) in one paragraph."""
        return {self.resolve(mo.group(0), key) for mo in re.finditer(self.named_src, self.blocks[key])} - {None}

    def resolve(self, ref, key):
        ref = ref if ref in self.alias_of or ref in self.names or ref in self.sponsor_forms or ref in self.ordinal_forms \
            else ref[0].lower() + ref[1:]
        if ref in INV['anaphors']:
            before = (key[0], key[1] - 1)
            found = self.named_in(before) if before in self.blocks else set()
            if len(found) != 1:
                self.errors.append(f'{ref!r} at {key} points back to {len(found)} projects')
                return None
            return next(iter(found))
        if ref in self.ordinal_forms:
            awards = [f for f in self.facts if f['t'] == 'award']
            number = self.ordinal_forms[ref]
            if number >= len(awards) or sum(f['m'] == awards[number]['m'] for f in awards) != 1:
                self.errors.append(f'{ref!r} at {key} names no single project')
                return None
            return awards[number]['p']
        if ref in self.names:
            return ref
        if ref in self.alias_of:
            if len(self.alias_of[ref]) != 1:
                self.errors.append(f'ambiguous {ref!r}')
            return sorted(self.alias_of[ref])[0]
        full, at = self.sponsor_of.get(ref, (None, None))
        if full is None or not at < key:
            self.errors.append(f'{ref!r} used before it is established at {key}')
            return None
        return full

    def amount(self, text, m, key, who):
        text = text[0].lower() + text[1:]
        if re.fullmatch(MONEY_SRC, text):
            return {'type': 'abs', 'amount': parse_money(text)}
        for kind, templates in self.phrases.items():
            for template in templates:
                mo = re.fullmatch(template, text)
                if mo:
                    g = mo.groupdict()
                    spec = {'type': kind}
                    if 'ord' in g:
                        spec['k'] = ORDINAL_OF[g['ord']]
                    if 'frac' in g:
                        spec['frac'] = FRACTION_OF[g['frac']]
                    if 'when' in g:
                        spec['when'] = self.meeting_of(g['when'])
                    if 'proj2' in g:
                        spec['other'] = self.resolve(g['proj2'], key)
                        spec['other_ref'] = g['proj2']
                    return spec
        self.errors.append(f'unreadable amount {text!r}')
        return {'type': 'bad'}

    def delta(self, text):
        if re.fullmatch(MONEY_SRC, text):
            return ('money', parse_money(text))
        for frac in sorted(FRACTION_OF, key=len, reverse=True):
            if frac in text:
                return ('frac', FRACTION_OF[frac])
        raise ValueError(text)

    def read_block(self, key, m, text):
        for intro in INV['restate_intros']:
            if text.startswith(intro):
                body = text[len(intro) + 1:]
                body = body[:body.index('.')] if '.' in body else body
                entries, refs = [], []
                for part in body.split('; '):
                    for form in INV['restate_entries']:
                        mo = re.fullmatch(src(form, {'proj': self.refs_src, 'money': MONEY_SRC, 'when': WHEN_SRC}, True), part)
                        if mo:
                            entries.append((self.resolve(mo['proj'], key), parse_money(mo['money'])))
                            refs.append((entries[-1][0], mo['proj']))
                            break
                    else:
                        self.errors.append(f'unreadable summary entry {part!r}')
                for who in sorted({p for p, _ in entries}):
                    self.add(key, m, text, 'restate', who, entries=[a for p, a in entries if p == who], start=0,
                             refs=[r for p, r in refs if p == who])
                return
        hits = []
        frames = list(self.frames.items())
        if 'as minuted in' in text:         # only a quiet return says this
            frames.append(('quiet_return', self.quiet_res()))
        for kind, compiled in frames:
            for rx in compiled:
                for mo in rx.finditer(text):
                    hits.append((mo.start(), mo.end(), kind, mo))
        hits.sort(key=lambda h: h[0])
        for earlier, later in zip(hits, hits[1:]):
            if later[0] < earlier[1]:
                self.errors.append(f'overlapping readings in {text[earlier[0]:later[1]]!r}')
        for start, end, kind, mo in hits:
            g = mo.groupdict()
            who = self.resolve(g['proj'], key) if g.get('proj') else g.get('full')
            before = len(self.facts)
            if kind == 'award':
                self.add(key, m, text, 'award', who, amount=parse_money(g['money']), start=start)
            elif kind == 'outside':
                self.add(key, m, text, 'outside', who, amount=parse_money(g['money']), start=start)
            elif kind == 'defer_award' or kind == 'authorise':
                self.add(key, m, text, 'defer_award' if kind == 'defer_award' else 'authorise', who,
                         amount=parse_money(g['money']), start=start)
            elif kind in ('vary', 'fell_vary', 'defer_vary'):
                sign = 1 if g['verb'] in INV['raise'] + INV['raise_inf'] else -1
                self.add(key, m, text, kind, who, sign=sign, delta=self.delta(g['delta']), start=start)
            elif kind in ('correct_award', 'correct_pay'):
                self.add(key, m, text, 'correct', who, target='award' if kind == 'correct_award' else 'pay',
                         when=self.meeting_of(g['when']), new=parse_money(g['money']), start=start)
            elif kind == 'rescind':
                self.add(key, m, text, 'rescind', who, start=start)
            elif kind in ('return', 'quiet_return'):
                self.add(key, m, text, 'return', who, when=self.meeting_of(g['when']), start=start, quiet=kind == 'quiet_return',
                         form=self.quiet_res().index(mo.re) if kind == 'quiet_return' else None)
            elif kind == 'pay':
                self.add(key, m, text, 'pay', who, spec=self.amount(g['amt'], m, key, who), start=start,
                         recipient_first=mo.start('proj') < mo.start('amt'))      # the recipient is named before the amount
            elif kind == 'intro':
                self.add(key, m, text, 'intro', who, sponsor=g['sponsor'], start=start)
            for fact in self.facts[before:]:
                fact['ref'] = g.get('proj')         # the words that name the project in this sentence
                fact['end'] = end                   # and where the sentence ends in its paragraph (`order` holds where it starts)

    def add(self, key, m, text, t, who, start, **fields):
        fact = {'t': t, 'm': m, 'p': who, 'key': key, 'full': who in text, 'order': (key, start)}
        fact.update(fields)
        self.facts.append(fact)


# ---------- The evaluator: the procedure, applied to recovered facts ----------

def run(facts, count, names, mode=(), strict=True):
    """Per meeting and project: total, award, balance, count, largest; number of projects; first fully paid.
    `mode` holds the shortcut of a flawed reader. In strict mode a reference to a fact that is not there kills the
    project from that meeting on (value None); otherwise the fact is skipped."""
    mode = set(mode)
    bundle = {'ignores_corrections': {'corrections', 'rescissions', 'returns'},
              'counts_motions_as_decisions': {'fell_vary', 'defer_vary', 'defer_award', 'authorise'}}
    for name in list(mode):
        mode |= bundle.get(name, set())
    visible = [f for f in facts if 'full_name_only' not in mode or f['t'] in ('intro', 'restate') or f['full']]
    fix = {}
    if 'corrections' not in mode:
        fix = {(f['p'], f['target'], f['when']): f['new'] for f in visible if f['t'] == 'correct'}
    state = {n: {'award': None, 'at': {}, 'pays': [], 'numbered': [], 'auth': {}, 'dead': False, 'first': None} for n in names}
    out = {}
    for m in range(count):
        today = sorted((f for f in visible if f['m'] == m), key=lambda f: (f['t'] in ('pay', 'return', 'restate'), f['order']))
        for f in today:
            s = state[f['p']]
            t = f['t']
            if t == 'award':
                s['award'] = s['at'][m] = fix.get((f['p'], 'award', m), f['amount'])
            elif t == 'defer_award' and 'defer_award' in mode:
                s['award'] = s['at'][m] = f['amount']
            elif t == 'authorise':
                s['auth'][m] = f['amount']
                if 'authorise' in mode:
                    pay = {'amount': f['amount'], 'ret': None, 'auth': m}
                    s['pays'].append(pay)
                    s['numbered'].append(pay)
            elif t == 'vary' or (t in ('fell_vary', 'defer_vary') and t in mode):
                if s['award'] is not None:
                    kind, size = f['delta']
                    s['award'] += f['sign'] * (size if kind == 'money' else s['award'] * size)
            elif t == 'rescind' and 'rescissions' not in mode:
                s['award'] = None
            elif t == 'return' and 'returns' not in mode:
                hit = [p for p in s['pays'] if p.get('m') == f['when'] and 'auth' not in p and not p.get('extra')]
                if hit:
                    hit[0]['ret'] = m
                elif strict:
                    s['dead'] = s['dead'] or m
            elif t == 'restate' and 'counts_every_mention' in mode:
                for amount in f['entries']:
                    s['pays'].append({'amount': amount, 'ret': None, 'm': m, 'extra': True})
            elif t == 'pay':
                spec = f['spec']
                amount = None
                kind = spec['type']
                if kind == 'abs':
                    amount = fix.get((f['p'], 'pay', m), spec['amount'])
                elif kind == 'authorised':
                    if 'authorise' in mode:
                        continue
                    amount = s['auth'].get(spec['when'])
                elif kind in ('first', 'ordinal'):
                    k = 1 if kind == 'first' else spec['k']
                    amount = s['numbered'][k - 1]['amount'] if len(s['numbered']) >= k else None
                elif kind == 'first_of':
                    other = state[spec['other']]['numbered']
                    amount = other[0]['amount'] if other else None
                elif kind == 'frac_award':
                    amount = s['at'][spec['when']] * spec['frac'] if spec['when'] in s['at'] else None
                elif kind == 'frac_current':
                    amount = s['award'] * spec['frac'] if s['award'] is not None else None
                elif kind == 'balance' and s['award'] is not None:
                    counting = [p['amount'] for p in s['pays'] if p['ret'] is None]
                    amount = None if None in counting else s['award'] - sum(counting)
                if amount is None and not strict:
                    continue
                if amount is None:
                    s['dead'] = s['dead'] or m
                pay = {'amount': amount, 'ret': None, 'm': m}
                s['pays'].append(pay)
                s['numbered'].append(pay)
        for n in names:
            s = state[n]
            live = [p for p in s['pays'] if p['ret'] is None]
            paid = sum(p['amount'] for p in live) if all(p['amount'] is not None for p in live) else None
            big = max((p['amount'] for p in live), default=None) if paid is not None else None
            dead = s['dead'] is not False
            award = s['award']
            if s['first'] is None and not dead and award is not None and paid is not None and paid >= award:
                s['first'] = m
            show = lambda v: None if dead else v  # noqa: E731
            out[('total', n, m)] = show(paid)
            out[('award', n, m)] = show(award if award is not None else 'none')
            out[('balance', n, m)] = show(award - paid if award is not None and paid is not None else None)
            out[('count', n, m)] = show(len(live))
            out[('amounts', n, m)] = tuple(sorted(p['amount'] for p in live if p['amount'] is not None))
            out[('largest', n, m)] = show(big)
        out[('projects', None, m)] = sum(state[n]['award'] is not None for n in names)
    for n in names:
        out[('first', n, None)] = state[n]['first']
    return out


STATEMENT_SRC = {}
for _kind, _frames in INV['statements'].items():
    _value = {'total': MONEY_SRC, 'award': MONEY_SRC, 'balance': MONEY_SRC, 'largest': MONEY_SRC,
              'count': r'(?:\d+|[a-z]+)', 'projects': r'(?:\d+|[a-z]+)', 'first': WHEN_SRC}[_kind]
    STATEMENT_SRC[_kind] = [re.compile('^' + ''.join(
        {'{proj}': f'(?P<proj>{FULL_SRC})', '{when}': f'(?P<when>{WHEN_SRC})', '{value}': f'(?P<value>{_value})'}.get(p, re.escape(p))
        for p in re.split(r'(\{\w+\})', _f)) + '$') for _f in _frames]


def read_statement(book, text):
    """(kind, project, meeting index or None, stated value) of one statement."""
    for kind, patterns in STATEMENT_SRC.items():
        for rx in patterns:
            mo = rx.match(text)
            if mo:
                g = mo.groupdict()
                until = book.meeting_of(g['when']) if g.get('when') else None
                raw = g['value']
                value = book.meeting_of(raw) if kind == 'first' else parse_money(raw) if kind in ('total', 'award', 'balance', 'largest') else parse_count(raw)
                return kind, g.get('proj'), until, value
    raise AssertionError(f'unreadable statement {text!r}')


def verdict(results, statement):
    kind, who, until, value = statement
    return 'supported' if results[(kind, who, until)] == value else 'refuted'


# ---------- Sums (editions 3 and 4): the statement reader and the evaluator, from the inventory and the text ----------

SUM_SHAPES = {'sum': 'named', 'all': 'all', 'span': 'span'}      # the first word of a frame table's name
FULL_PLAIN = re.sub(r'\(\?P<\w+>', '(?:', FULL_SRC)                # a full name, without group names
SUM_LIST = INV.get('sum_list', {'separator': ', ', 'last': ' and '})
LIST_SRC = (f'{FULL_PLAIN}(?:{re.escape(SUM_LIST["separator"])}{FULL_PLAIN})*{re.escape(SUM_LIST["last"])}{FULL_PLAIN}')
MONEY_FIGURES = ('total', 'award', 'balance')      # the figures a sum states in pounds; `count` is a number of payments


def compile_sum_frames(tables):
    """(shape, figure, compiled frames) of every table of statement frames of sums: the first word of a table's name is the shape
    and the second the figure (`sum_award`, `all_balance`, ...)."""
    out = []
    for table, frames in tables.items():
        shape, figure = SUM_SHAPES[table.split('_')[0]], table.split('_')[1]
        atoms = {'{names}': f'(?P<names>{LIST_SRC})', '{when}': f'(?P<when>{WHEN_SRC})', '{earlier}': f'(?P<earlier>{WHEN_SRC})',
                 '{later}': f'(?P<later>{WHEN_SRC})',
                 '{value}': '(?P<value>' + (MONEY_SRC if figure in MONEY_FIGURES else r'(?:\d+|[a-z]+)') + ')'}
        out.append((shape, figure, [re.compile('^' + ''.join(atoms.get(part, re.escape(part)) for part in re.split(r'(\{\w+\})', f)) + '$')
                                    for f in frames]))
    return out


SUM_SRC = compile_sum_frames(INV.get('sum_statements', {}))


def name_list(names):
    return SUM_LIST['separator'].join(names[:-1]) + SUM_LIST['last'] + names[-1]


def is_sum_text(text):
    return any(rx.match(text) for _, _, patterns in SUM_SRC for rx in patterns)


def read_sum(book, text):
    """The statement about a sum in `text`, or None when it is not one: shape (`named`, `all`, `span`), figure (`total` or
    `count`), the names in the order written, the meeting numbers of the close(s) and the stated value. Exactly one frame of the
    inventory must fit, and the names must be the list as `sum_list` writes it."""
    found = [(shape, figure, mo) for shape, figure, patterns in SUM_SRC for rx in patterns for mo in [rx.match(text)] if mo]
    if not found:
        return None
    need(len(found) == 1, f'{len(found)} frames fit {text!r}')
    shape, figure, mo = found[0]
    g = mo.groupdict()
    names = None
    if shape == 'named':
        names = [m.group(0) for m in FULL_RE.finditer(g['names'])]
        need(name_list(names) == g['names'] and len(set(names)) == len(names) and set(names) <= set(book.names),
             f'the names of {text!r} are not a list of distinct projects')
    until = book.meeting_of(g['later'] if shape == 'span' else g['when'])
    since = book.meeting_of(g['earlier']) if shape == 'span' else None
    raw = g['value']
    return {'shape': shape, 'figure': figure, 'names': names, 'since': since, 'until': until,
            'value': parse_money(raw) if figure in MONEY_FIGURES else parse_count(raw), 'value_span': mo.span('value'), 'text': text}


def statement_of(book, text):
    """Any statement of a report as a dict: `shape` is `single` (kind, who, until, value) or that of a sum."""
    found = read_sum(book, text)
    if found is not None:
        need(not any(rx.match(text) for patterns in STATEMENT_SRC.values() for rx in patterns),
             f'{text!r} fits a frame of a sum and a frame of a statement about one project')
        return found
    kind, who, until, value = read_statement(book, text)
    return {'shape': 'single', 'kind': kind, 'who': who, 'until': until, 'value': value, 'text': text}


AWARD_FIGURES = ('award', 'balance')      # the figures of the award side (edition 5)


def part_of(results, name, figure, m):
    """The figure of one project at the close of meeting `m` as a part of a sum, or None when it is not established. On the award
    side (`award`, `balance`) a project that holds no award in force at that close, and a project whose award a reading cannot
    establish, add nothing (0): the interpreter writes the established "no award in force" as 'none' (the balance of such a project
    as nothing at all in this module's evaluator, whose balance is only read where the award is), and the balance is read only
    where the award is."""
    if figure in AWARD_FIGURES:
        award = results[('award', name, m)]
        if award is None or award == 'none':
            return 0
        part = results[(figure, name, m)]
        return 0 if part is None else part
    return results[(figure, name, m)]


def sum_of(results, projects, st):
    """The value of a sum statement under one reading: the figure of each project at the close, added exactly; for a span the
    figure at the later close less that at the earlier close (None when a figure is not established, which for the award side
    cannot happen: a part that is not established adds nothing)."""
    names = st['names'] if st['shape'] == 'named' else list(projects)

    def at(m):
        parts = [part_of(results, n, st['figure'], m) for n in names]
        return None if any(p is None for p in parts) else sum((fractions.Fraction(p) for p in parts), fractions.Fraction(0))
    later = at(st['until'])
    if st['since'] is None or later is None:
        return later
    earlier = at(st['since'])
    return None if earlier is None else later - earlier


def decide(results, projects, st):
    """supported or refuted: the verdict of a statement of either kind under one reading."""
    if st['shape'] == 'single':
        return verdict(results, (st['kind'], st['who'], st['until'], st['value']))
    got = sum_of(results, projects, st)
    return 'supported' if got is not None and got == st['value'] else 'refuted'


def good_figure(figure, value):
    """A good value of a sum (supplement 4): a positive whole number of pounds, or a number of payments of at least two."""
    return value is not None and value.denominator == 1 and value >= (2 if figure == 'count' else 1)


@per_edition
def made_of(seed, index=1):
    """The module's own case (events, passages, bases), built from the random source `build_case` uses."""
    return big_minutes.build(random.Random(f'evalsuite:{generate.GENERATOR_VERSION}:{seed}:{index}:minutes'))


def readers_in(seed, index=1):
    return tuple(sorted(made_of(seed, index)['readers']))


def runs_of(book):
    """The truth and each flawed reader's reading of a book, by this module's own evaluator."""
    count = len(book.meetings)
    runs = {'truth': run(book.facts, count, book.names)}
    for name, mode in (('counts_every_mention', ('counts_every_mention',)), ('ignores_corrections', ('ignores_corrections',)),
                       ('counts_motions_as_decisions', ('counts_motions_as_decisions',)), ('full_name_only', ('full_name_only',))):
        runs[name] = run(book.facts, count, book.names, mode, strict=False)
    return runs


@per_edition
def read(seed, index=1):
    book = Book(sources_of(case(seed, index)))
    return book, runs_of(book)


def same_values(event, fact, by_id):
    """Does the fact the parser read from a passage carry the values the event log gives?"""
    kind = event['kind']
    if kind in ('award', 'defer_award', 'authorise', 'outside'):
        return fact['amount'] == event['amount']
    if kind in ('vary', 'fell_vary', 'defer_vary'):
        delta = event['delta']
        want = ('money', delta['money']) if 'money' in delta else ('frac', fractions.Fraction(*delta['frac']))
        return fact['sign'] == event['sign'] and fact['delta'] == want
    if kind == 'correct':
        return (fact['target'], fact['when'], fact['new']) == (event['target_kind'], event['target_m'], event['new'])
    if kind == 'return':
        return fact['when'] == event['target_m']
    if kind == 'intro':
        return fact['sponsor'] == event['sponsor']
    if kind == 'restate':
        return sorted(fact['entries']) == sorted(x['amount'] for x in event['entries'])
    if kind == 'pay':
        spec, found = event['spec'], fact['spec']
        if found['type'] != spec['type']:
            return False
        return {'abs': lambda: found['amount'] == spec['amount'], 'ordinal': lambda: found['k'] == spec['k'],
                'frac_award': lambda: (found['when'], found['frac']) == (spec['award_m'], fractions.Fraction(*spec['frac'])),
                'frac_current': lambda: found['frac'] == fractions.Fraction(*spec['frac']),
                'first_of': lambda: found['other'] == spec['other'],
                'authorised': lambda: found['when'] == by_id[spec['auth']]['m']}.get(spec['type'], lambda: True)()
    return True


# ---------- Checks that run on any one draw (the unit tests call them for seeds 1 to 5, `verify` for a given draw) ----------

class Failed(AssertionError):
    pass


def need(condition, message):
    if not condition:
        raise Failed(message)


def check_round_trip(seed, index=1):
    built = case(seed, index)
    book, runs = read(seed, index)
    need(book.errors == [], f'unreadable text: {book.errors[:2]}')
    for variant in ('correct', 'mixed'):
        for text, row in zip(report_statements(built, variant), key_rows(built, variant)):
            need(text == row['claim_text'], 'report and key disagree on a statement')
            statement = read_statement(book, text)
            need(verdict(runs['truth'], statement) == row['semantic_state'], f'{variant}: verdict differs for {text!r}')
            for name in readers_in(seed, index):
                need(verdict(runs[name], statement) == row['lure_states'].get(name, row['semantic_state']),
                     f'{variant}: reader {name} differs for {text!r}')
    return f'{2 * len(key_rows(built, "correct"))} statements, readers {", ".join(readers_in(seed, index))}'


def check_one_referent(seed, index=1):
    book, _ = read(seed, index)
    need(book.errors == [], f'unreadable or ambiguous text: {book.errors[:2]}')
    full = {n.lower() for n in book.names}
    for alias, owners in book.alias_of.items():
        need(len(owners) == 1, f'{alias!r} fits {len(owners)} projects')
        for other in list(book.alias_of) + list(full):
            need(other == alias or alias.lower() not in other.lower(), f'{alias!r} is part of {other!r}')
    uses = collections.Counter()
    for fact in book.facts:
        if fact['t'] in ('pay', 'award', 'vary', 'correct', 'return', 'rescind'):
            uses['facts'] += 1
            uses['full'] += fact['full']
    need(uses['facts'] - uses['full'] > 0.25 * uses['facts'], f'too few passages name their subject by a short form: {dict(uses)}')
    return f'{uses["facts"] - uses["full"]} of {uses["facts"]} deciding passages use a short form'


def claims_by_text(made):
    return {c['text']: c for true, wrong in made['slots'] for c in [true] + wrong}


def check_answers(seed, index=1):
    """`answer` agrees with the key for every statement and with every flawed reader's verdict; forms, claims, preamble."""
    built, made = case(seed, index), made_of(seed, index)
    events, by_text = made['events'], claims_by_text(made)
    forms = {'integer', 'decimal', 'date', 'month', 'name'}
    for variant in ('correct', 'mixed'):
        for row in key_rows(built, variant):
            claim = by_text[row['claim_text']]
            q = claim['query']
            need(callable(claim['quantity']) and claim['stated'] is not None, 'a claim lacks quantity or stated')
            form = big_minutes.value_form(q)
            need(form in forms, f'bad value form {form!r}')
            true_value = big_minutes.answer(q, events)
            need(true_value not in (None, big_minutes.NONE_VALUE), f'no value established for {row["claim_text"]!r}')
            need(isinstance(true_value, str) == (form == 'date'), f'value {true_value!r} is not in form {form}')
            need(('supported' if true_value == q['value'] else 'refuted') == row['semantic_state'], f'{variant}: {row["claim_text"]!r}')
            for name, reader in made['readers'].items():
                theirs = big_minutes.answer(q, events, name)
                need(('supported' if theirs == q['value'] else 'refuted') == row['lure_states'].get(name, row['semantic_state']),
                     f'{variant}: reader {name}: {row["claim_text"]!r}')
    note = built['files']['report-correct.md']
    for phrase in ('whole number of pounds', 'YYYY-MM-DD', 'none'):
        need(phrase in note, f'the note on value forms lacks {phrase!r}')
    head = note.split('<a id="C01">')[0].lower()
    for word in ('correction', 'return', 'restate', 'authoris', 'defer', 'motion', 'rescind', 'recall', 'variation', 'amend', 'trap'):
        need(word not in head, f'the note names {word!r}')
    need(all(isinstance(v, list) and k in v for k, v in made['names'].items()), 'case names are malformed')
    return f'{len({r["claim_text"] for v in ("correct", "mixed") for r in key_rows(built, v)})} distinct statements'


def last_date(made):
    return made['meetings'][-1]


def query_of(kind, subject, until):
    return {'kind': kind, 'subject': subject, 'until': until, 'key': big_minutes.claim_key(kind, subject, until)}


def is_balance_sum(q):
    """A sum of unpaid balances (edition 5). What it turns on is not "the needed events alone give the true value": a fully paid
    project has a balance of nil, so losing its part changes nothing, and the loss of an award can be cancelled where another
    project's payment is stated through an instalment of it (fifth-edition design, section 2). For these sums the checks assert only
    that the set of needed events is not empty and lies inside `relevant`."""
    return q['kind'] in ('asum8', 'aupto', 'abook') and q.get('figure') == 'balance'


OWN_AWARD_KINDS = ('award', 'balance', 'first')     # the statements that are not established where the award is absent
LINKS = ('refs', 'named_by', 'amount_by')


def gone_with(events, removed=()):
    """The list without `removed` and without every event that cannot be attributed without what is not there, to a fixed
    point: an event whose name rests on an absent event (`named_by`) and a correction, return, variation or rescission of
    an absent minute or payment (`refs`). A payment whose amount cannot be read (it is stated by reference to, or by the
    number of, something that is not there: `refs`, `amount_by`) stays, since it is a payment; the interpreter leaves its
    sum out of every quantity but the number of payments, and what that does is pinned by `check_dependencies` and the hand
    cases. Written here from the events' fields; the module's `reading` and `readable` are not used. `removed` may be empty:
    the events of a subset that can be attributed as it stands."""
    kept = [e for e in events if e['id'] not in set(removed)]
    while True:
        ids = {e['id'] for e in kept}
        now = [e for e in kept if all(d in ids for d in e.get('named_by', ()))
               and (e['kind'] == 'pay' or all(d in ids for d in e.get('refs', ())))]
        if len(now) == len(kept):
            return kept
        kept = now


def closure(events, ids, links=LINKS):
    """`ids` and everything they cannot be read without (`refs`, `named_by`, `amount_by`, or the fields of `links`),
    followed to the end."""
    by_id = {e['id']: e for e in events}
    found, todo = set(ids), list(ids)
    while todo:
        e = by_id[todo.pop()]
        for key in links:
            for d in e.get(key, ()):
                if d in by_id and d not in found:
                    found.add(d)
                    todo.append(d)
    return found


def statement_queries(seed, index=1):
    """The distinct queries of the statements of both reports, by key."""
    built, made = case(seed, index), made_of(seed, index)
    by_text = claims_by_text(made)
    return {by_text[row['claim_text']]['query']['key']: by_text[row['claim_text']]['query']
            for variant in ('correct', 'mixed') for row in key_rows(built, variant)}


@per_edition
def cut_answers(seed, index, key):
    """`answer` of one statement with each event removed in turn from the full list (one run per event, kept for every
    check): {event id: value}. This is how the checker finds the events a statement turns on."""
    events, q = made_of(seed, index)['events'], statement_queries(seed, index)[key]
    return {e['id']: big_minutes.answer(q, [x for x in events if x['id'] != e['id']]) for e in events}


def needed_of(seed, index, key):
    """The events whose removal from the full list changes the value of a statement or makes it None."""
    true = big_minutes.answer(statement_queries(seed, index)[key], made_of(seed, index)['events'])
    return {i for i, value in cut_answers(seed, index, key).items() if value != true}


def closed_needed_of(seed, index, key):
    """Needed, closed under what fixes the name of an event (the module's `depends`, `name` only, followed to the end):
    what the checker takes a statement to turn on."""
    events = made_of(seed, index)['events']
    depends, found = big_minutes.depends(events), set(needed_of(seed, index, key))
    todo = list(found)
    while todo:
        for d in depends[todo.pop()]['name']:
            if d not in found:
                found.add(d)
                todo.append(d)
    return found


def nothing_of(seed, index, key):
    """Of those, the events whose removal only makes the value None."""
    return {i for i in needed_of(seed, index, key) if cut_answers(seed, index, key)[i] is None}


def own_award_closure(events, subject):
    """The project's own award and what it cannot be read without (what names it): removing any of these leaves the
    award, the balance and the first full payment of the project not established."""
    return closure(events, [e['id'] for e in events if e['kind'] == 'award' and e['subject'] == subject], ('named_by',))


def check_subset_rules(seed, index=1):
    """A subset is read as the whole minute book under the procedure's rules: an event that refers to something the
    subset lacks, or whose project is named through something it lacks, is left out (what the subset then gives is what
    the list gives with the dependents removed too, a payment whose amount cannot be read still counting as a payment);
    a missing later correction, return, variation or rescission is simply not applied; the value is None only for the
    award, the balance or the first full payment of a project whose award (or what names it) is absent."""
    made = made_of(seed, index)
    events, end = made['events'], last_date(made)
    queries = statement_queries(seed, index)
    compared = 0
    for key, q in queries.items():
        own = own_award_closure(events, q['subject'])
        for event_id in sorted(big_minutes.relevant(q, events)):
            got = big_minutes.answer(q, [x for x in events if x['id'] != event_id])
            need(got == big_minutes.answer(q, gone_with(events, {event_id})),
                 f'{key} without {event_id}: the subset does not read as the list without what cannot be read without it')
            if got is None:
                need(q['kind'] in OWN_AWARD_KINDS and event_id in own,
                     f'{key} without {event_id}: not established, and it is not the award of the statement or what names it')
            compared += 1
    cut = 0
    for e in events:
        for ref in e.get('refs', ()):
            subset = [x for x in events if x['id'] != ref]
            if e['kind'] in ('vary', 'rescind') or (e['kind'] == 'correct' and e['target_kind'] == 'award'):
                q = query_of('award', e['subject'], end)
                need(big_minutes.answer(q, subset) is None, f'{e["id"]} without {ref}: the award should not be established')
            else:
                q = query_of('total', e['subject'], end)
                got = big_minutes.answer(q, subset)
                need(got is not None and got == big_minutes.answer(q, gone_with(events, {ref})),
                     f'{e["id"]} without {ref}: the total is {got!r}, not that of the list without it and what cannot be read without it')
            cut += 1
    returns = [e for e in events if e['kind'] == 'return']
    fixes = [e for e in events if e['kind'] == 'correct']
    need(returns and fixes, 'the dossier has no return or no correction to test')
    for e in returns:
        q = query_of('count', e['subject'], end)
        here = big_minutes.answer(q, events)
        without = big_minutes.answer(q, [x for x in events if x['id'] != e['id']])
        need(here is not None and without == here + 1, f'{e["id"]}: without the return the payment should count again ({here} -> {without})')
    for e in fixes:
        kind = 'award' if e['target_kind'] == 'award' else 'total'
        q = query_of(kind, e['subject'], e['date'])
        without = big_minutes.answer(q, [x for x in events if x['id'] != e['id']])
        need(without is not None and without != big_minutes.NONE_VALUE, f'{e["id"]} missing leaves {kind} not established')
    # what any subset gives: the number of projects, a total, a count and a largest payment are always established
    rng = random.Random(seed * 1000 + index)
    for keep in (0.0, 0.05, 0.3, 0.7, 0.95):
        subset = [x for x in events if rng.random() < keep]
        awards = {x['subject'] for x in gone_with(subset) if x['kind'] == 'award'}
        raw_awards = {x['subject'] for x in subset if x['kind'] == 'award'}
        deferred = {x['subject'] for x in subset if x['kind'] == 'defer_award'}
        for key, q in list(queries.items())[::2]:
            for reader in (None,) + big_minutes.READERS:       # the flawed readers follow the same rule in their own facts
                got = big_minutes.answer(q, subset, reader)
                need(got is None or isinstance(got, (int, float, str)), f'{key}: {got!r} is not a value')
                if q['kind'] == 'projects':
                    need(isinstance(got, int), f'the number of projects is {got!r} on a subset')
                if q['kind'] in ('total', 'count', 'largest'):
                    need(got is not None, f'{key} is not established on a subset')
                if q['kind'] in OWN_AWARD_KINDS:
                    has = q['subject'] in (awards if reader is None else raw_awards) or (
                        reader == 'counts_motions_as_decisions' and q['subject'] in deferred)
                    need((got is None) == (not has), f'{key}: established or not does not follow the award of the project')
    return (f'{compared} statement/event removals read as the list without what cannot be read without them; {cut} referents cut, '
            f'{len(returns)} returns and {len(fixes)} corrections dropped; random subsets give a value')


def check_relevant_and_needed(seed, index=1):
    """(a): the events a statement turns on (those whose removal from the full list changes `answer`, and what fixes
    their names) are among `relevant`; none is empty; no tie leaves the largest payment without one deciding
    event."""
    built, made = case(seed, index), made_of(seed, index)
    events = made['events']
    queries = statement_queries(seed, index)
    for key, q in queries.items():
        needed = needed_of(seed, index, key)
        need(needed, f'nothing turns the value of {key}')
        relevant = set(big_minutes.relevant(q, events))
        need(not needed - relevant, f'{key}: needed events {sorted(needed - relevant)} are not relevant')
        closed = closed_needed_of(seed, index, key)
        need(not closed - relevant, f'{key}: closed needed events {sorted(closed - relevant)} are not relevant')
        if q['kind'] == 'largest':
            book, runs = read(seed, index)
            amounts = runs['truth'][('amounts', q['subject'], made['meetings'].index(q['until']))]
            need(amounts.count(max(amounts)) == 1, f'{key}: a tie for the largest payment')
    return f'{len(queries)} statements, smallest needed set {min(len(needed_of(seed, index, k)) for k in queries)}'


def check_needed_events_are_an_argument(seed, index=1):
    """For every statement of both reports, with needed = the events whose removal from the full list changes `answer`
    (to another value or to None), closed under what fixes the name of an event (`depends`, `name`: the checker's reading, spec 3.3 step 4):
    (b) `answer` on the needed events alone is the true value, for every kind of statement (none is exempt: a count or a
    largest payment that one of several events would support would need to be, and the generator draws no tie and no
    payment that any of several would replace); (c) no needed event is one whose removal only makes the value None, but
    the project's own award and what names it, for the award in force, the balance and the first full payment."""
    events = made_of(seed, index)['events']
    queries = statement_queries(seed, index)
    own = exempt = 0
    for key, q in queries.items():
        true = big_minutes.answer(q, events)
        closed = closed_needed_of(seed, index, key)
        alone = big_minutes.answer(q, [e for e in events if e['id'] in closed])
        if is_balance_sum(q):
            need(closed, f'{key}: nothing turns the value of a sum of balances')
            exempt += 1
        else:
            need(alone == true, f'{key}: the closed needed events alone give {alone!r}, not {true!r}')
        for i in nothing_of(seed, index, key):
            need(q['kind'] in OWN_AWARD_KINDS and i in own_award_closure(events, q['subject']),
                 f'{key}: removing {i} only makes the value None')
            own += 1
    return (f'{len(queries)} statements: the needed events, closed under what fixes their names, alone give the true '
            f'value (only the sums of balances of edition 5 are exempt, {exempt} of them: their needed set is asserted not empty and inside '
            f'relevant); the only needed events whose removal makes the value None are the own award and what '
            f'names it ({own}) of award, balance and first statements')


# ---------- Names: what each paragraph's own words say fixes the project, read from the text ----------

@per_edition
def naming_by_text(seed, index=1):
    """For every event the parser reads from its stating paragraph (and for every lead paragraph): what the words of its
    sentence fix, as ({event id: (named_by, amount_by)}, {lead event id: named_by}). Written from the rendered text, the
    inventory and the log's kinds, meetings and subjects: not from `refs`, `readable` or `link`.
      - the full name and the short forms: nothing (the procedure lets a reader resolve them alone);
      - `the {sponsor} scheme`: the one application event of that project;
      - `the Nth undertaking to receive the committee's backing`: every award up to and including the Nth, in the order
        of the meetings;
      - a phrase that points back: the lead paragraph's event (which depends on what names its project);
      - `the k-th instalment`: the instalments of the project before the k-th; `the first instalment paid to` another
        project: what names that project in the sentence."""
    book, _ = read(seed, index)
    made = made_of(seed, index)
    events, by_id = made['events'], {e['id']: e for e in made['events']}
    intro = {e['subject']: e['id'] for e in events if e['kind'] == 'intro'}
    awards = sorted((e for e in events if e['kind'] == 'award'), key=lambda e: (e['m'], e['id']))
    pays = collections.defaultdict(list)
    for e in sorted((e for e in events if e['kind'] == 'pay'), key=lambda e: (e['m'], e['id'])):
        pays[e['subject']].append(e['id'])
    names_at = {(VOLUMES.index(p['file']), p['paragraph']): p['event_ids'][0] for p in made['passages'] if p['role'] == 'names'}
    kind_of = {'correct': 'correct', 'restate': 'restate'}

    def fixes(ref, key):
        known = (book.alias_of, book.names, book.sponsor_forms, book.ordinal_forms, INV['anaphors'])
        ref = ref if any(ref in k for k in known) else ref[0].lower() + ref[1:]
        if ref in book.names or ref in book.alias_of:
            return set()
        if ref in book.sponsor_forms:
            return {intro[book.sponsor_of[ref][0]]}
        if ref in book.ordinal_forms:
            number = book.ordinal_forms[ref]
            return {a['id'] for a in awards[:number + 1]}
        if ref in INV['anaphors']:
            return {names_at[(key[0], key[1] - 1)]}
        raise Failed(f'unknown form of reference {ref!r}')

    told, leads = {}, {}
    for entry in made['passages']:
        key = (VOLUMES.index(entry['file']), entry['paragraph'])
        if entry['role'] == 'names':
            refs = {mo.group(0) for mo in re.finditer(book.named_src, book.blocks[key])}
            need(len(refs) == 1, f'a lead paragraph names {len(refs)} forms: {book.blocks[key][:120]!r}')
            leads[entry['event_ids'][0]] = fixes(refs.pop(), key)
        elif entry['role'] == 'states':
            here = [f for f in book.facts if f['key'] == key]
            for event_id in entry['event_ids']:
                event = by_id[event_id]
                matches = [f for f in here if f['t'] == kind_of.get(event['kind'], event['kind']) and f['p'] == event['subject']
                           and f['m'] == made['meetings'].index(event['date'])]
                need(len(matches) == 1, f'{event_id}: {len(matches)} sentences of its paragraph read as the event')
                fact, named, amount = matches[0], set(), set()
                if fact['t'] == 'restate':
                    for ref in fact['refs']:
                        named |= fixes(ref, key)
                elif fact['t'] != 'intro':
                    named = fixes(fact['ref'], key)
                if fact['t'] == 'pay':
                    spec = fact['spec']
                    if spec['type'] == 'ordinal':
                        amount |= set(pays[event['subject']][:spec['k'] - 1])
                    if spec['type'] == 'first_of':
                        amount |= fixes(spec['other_ref'], key)
                told[event_id] = (named, amount)
    return told, leads


def check_naming_recorded(seed, index=1):
    """The dependencies recorded in `named_by` and `amount_by` are what the words of each sentence say they are (see
    `naming_by_text`), for every event and every lead paragraph; the lead paragraph is the one just above the paragraph
    that points back; every list is of distinct known events, none its own."""
    made = made_of(seed, index)
    events, by_id = made['events'], {e['id']: e for e in made['events']}
    told, leads = naming_by_text(seed, index)
    forms = collections.Counter()
    for e in events:
        for key in ('named_by', 'amount_by'):
            ids = e[key]
            need(isinstance(ids, list) and len(set(ids)) == len(ids) and e['id'] not in ids and all(i in by_id for i in ids),
                 f'{e["id"]}: {key} is not a list of distinct other events: {ids!r}')
        if e['kind'] == 'names':
            need(e['id'] in leads, f'{e["id"]}: a lead event without its paragraph')
            need(set(e['named_by']) == leads[e['id']], f'{e["id"]}: lead named_by {e["named_by"]} against the words {sorted(leads[e["id"]])}')
            need(e['amount_by'] == [] and e['refs'] == [] and not e['decides'], f'{e["id"]}: a lead event carries more than a name')
            forms['lead'] += 1
            continue
        need(e['id'] in told, f'{e["id"]}: no stating paragraph was read')
        named, amount = told[e['id']]
        need(set(e['named_by']) == named, f'{e["id"]} ({e["kind"]}): named_by {e["named_by"]} against the words {sorted(named)}')
        need(set(e['amount_by']) == amount, f'{e["id"]} ({e["kind"]}): amount_by {e["amount_by"]} against the words {sorted(amount)}')
        forms['indirect' if named else 'alone'] += 1
        pointing = [i for i in e['named_by'] if by_id[i]['kind'] == 'names']
        if pointing:
            lead = by_id[pointing[0]]
            need(len(e['named_by']) == 1 and lead['subject'] == e['subject'] and lead['m'] == e['m'],
                 f'{e["id"]}: a paragraph that points back depends on one lead paragraph of its project and meeting')
            forms['pointing back'] += 1
    # a lead paragraph is the paragraph just above the one that points back to it
    for entry in made['passages']:
        if entry['role'] == 'names':
            below = [p for p in made['passages'] if p['role'] == 'states' and p['file'] == entry['file']
                     and p['paragraph'] == entry['paragraph'] + 1 and entry['event_ids'][0] in by_id[p['event_ids'][0]]['named_by']]
            need(len(below) == 1, f'{entry["event_ids"]}: the paragraph below a lead paragraph does not point back to it')
    return ', '.join(f'{c} {n}' for n, c in sorted(forms.items())) + ' events whose recorded dependencies match the words'


def check_dependencies(seed, index=1):
    """`depends` and `reading`: the whole list reads as itself; for every event E and every D that E cannot be read
    without, the list without D reads as the list without D and E (the same quantities; but for a payment whose amount D fixes,
    which stays a payment: every quantity but the number of payments); and on subsets the reading leaves an event
    out only when something in the closure of its dependencies is absent, and reads one only with its dependencies present.
    A paragraph reporting a return earns the
    return and not the payment it reverses (no wording of a return gives a sum)."""
    made = made_of(seed, index)
    events, by_id = made['events'], {e['id']: e for e in made['events']}
    ids = lambda items: [e['id'] for e in items]  # noqa: E731
    need(big_minutes.readable(events) == events and big_minutes.reading(events) == (events, frozenset()),
         'the whole list does not read as itself')
    depends = big_minutes.depends(events)
    need(sorted(depends) == sorted(ids(events)) and all(sorted(ds) == ['amount', 'name', 'target'] for ds in depends.values())
         and all(d in by_id and d != i for i, ds in depends.items() for found in ds.values() for d in found),
         'depends names unknown events or an event itself, or lacks one of its three lists')
    none = {'name': [], 'amount': [], 'target': []}
    need(big_minutes.depends([]) == {} and big_minutes.depends([{'id': 'x', 'kind': 'award'}, {'id': 'y', 'kind': 'vary', 'refs': ['x', 'zz']},
                                                                {'id': 'z', 'kind': 'pay', 'refs': ['x'], 'named_by': ['y'], 'amount_by': ['x']},
                                                                {'id': 'w'}]) ==
         {'x': none, 'y': dict(none, target=['x']), 'z': {'name': ['y'], 'amount': ['x'], 'target': []}, 'w': none},
         'depends on small lists')
    for e in events:
        found = depends[e['id']]
        need(found['name'] == [d for d in e['named_by']] and found['target'] == ([] if e['kind'] == 'pay' else e['refs'])
             and found['amount'] == list(dict.fromkeys((e['refs'] if e['kind'] == 'pay' else []) + e['amount_by'])),
             f'{e["id"]}: depends does not sort the dependencies by kind')
    links = {'name': 0, 'target': 0, 'amount': 0}
    meetings = made['meetings']
    for e in events:
        for kind, found in depends[e['id']].items():
            for d in found:
                rest = [x for x in events if x['id'] != d]
                alone = [x for x in rest if x['id'] != e['id']]
                if kind == 'amount':
                    # the event stays a payment (the number of payments counts it): every other quantity is as without it
                    need(e['id'] not in ids(big_minutes.readable(rest)), f'{e["id"]} is read in full without {d}, which fixes its amount')
                    flat, flat_without = big_minutes.state(rest, None, meetings), big_minutes.state(alone, None, meetings)
                    need({k: v for k, v in flat.items() if not k.startswith('count|')} ==
                         {k: v for k, v in flat_without.items() if not k.startswith('count|')},
                         f'{e["id"]} without {d}: a quantity other than a number of payments still turns on it')
                else:
                    need(ids(big_minutes.readable(rest)) == ids(big_minutes.readable(alone)),
                         f'{e["id"]} still reads without {d}, which fixes its {kind}')
                    need(big_minutes.state(rest, None, meetings) == big_minutes.state(alone, None, meetings),
                         f'{e["id"]} without {d}: the quantities differ from those without it')
                links[kind] += 1
    closed = {i: closure(events, [i]) for i in by_id}
    rng = random.Random(seed * 77 + index)
    checked = 0
    for keep in (0.1, 0.4, 0.7, 0.9, 0.97) * 3:
        subset = [x for x in events if rng.random() < keep]
        present, kept = {x['id'] for x in subset}, set(ids(big_minutes.readable(subset)))
        for e in subset:
            missing = [i for i in closed[e['id']] if i not in present]
            if e['id'] in kept:
                need(all(d in present for found in depends[e['id']].values() for d in found), f'{e["id"]}: read with what it depends on absent')
            else:
                need(missing, f'{e["id"]}: left out, though nothing in the closure of what it depends on is absent')
            checked += 1
    earned = collections.defaultdict(set)
    for entry in made['passages']:
        need(entry['role'] in ('states', 'names'), f'unknown role {entry["role"]!r}')
        earned[(entry['file'], entry['paragraph'])] |= set(entry['event_ids'])
    stating = {i: (p['file'], p['paragraph']) for p in made['passages'] if p['role'] == 'states' for i in p['event_ids']}
    # a paragraph earns the events it states and no other: every event is listed in exactly one entry, `states` for an event
    # of the minutes and `names` for a lead event, so a correction or a return paragraph earns the correction or the return
    listed = collections.Counter(i for entry in made['passages'] for i in entry['event_ids'])
    need(all(listed[e['id']] == 1 for e in events), 'an event is listed in no entry, or in more than one paragraph')
    need(all((entry['role'] == 'names') == (by_id[i]['kind'] == 'names') for entry in made['passages'] for i in entry['event_ids']),
         'a lead event is listed under another role than names, or another event under names')
    fixes = [e for e in events if e['kind'] == 'correct']
    need(fixes, 'the dossier has no correction')
    for e in fixes:
        need(e['target'] not in earned[stating[e['id']]], f'{e["id"]}: the paragraph that reports the correction earns the minute it corrects')
    returns = [e for e in events if e['kind'] == 'return']
    need(returns, 'the dossier has no return')
    for e in returns:
        need(e['target'] not in earned[stating[e['id']]], f'{e["id"]}: the paragraph that reports the return earns the payment')
    need(all('{money}' not in f and '{amt}' not in f for f in INV['frames']['return']), 'a return wording gives a sum')
    return (f'{sum(links.values())} dependencies ({links}) each read as removed together with their dependent (for an amount, except in the number of payments); {checked} event readings on subsets follow the '
            f'closure; {len(fixes)} correction and {len(returns)} return paragraphs earn the correction or the return only; every '
            f'event is listed in exactly one entry')


def check_lead_paragraphs_quotable(seed, index=1):
    """UNMET (see the expected failure that uses it): the paragraph of every closed needed event can be quoted, that is,
    occurs once in the sources, since the checker locates a quote only when it lies in exactly one paragraph. A lead
    paragraph is one bare sentence of ordinary business and can occur twice in a dossier; no basis can show a statement that
    needs it, and the bytes of the dossiers are fixed."""
    made = made_of(seed, index)
    sources = sources_of(case(seed, index))
    texts = {}
    for entry in made['passages']:
        file, number = entry['file'], entry['paragraph']
        for i in entry['event_ids']:
            if entry['role'] in ('states', 'names'):
                texts[i] = sources[file].rstrip('\n').split('\n\n')[number]
    bad = collections.Counter()
    for key in statement_queries(seed, index):
        for i in closed_needed_of(seed, index, key):
            if sum(body.count(texts[i]) for body in sources.values()) > 1:
                bad[key] += 1
    need(not bad, f'{len(bad)} statements need a paragraph that occurs more than once in the sources: {sorted(bad)[:2]}')
    return 'every needed paragraph can be quoted'


def check_closed_needed(seed, index=1):
    """Constraint C4 on the draw: with the dependencies read from the words (`naming_by_text`), (a) a needed event told
    through an indirect name has what fixes the name among the needed events once the needed set is closed (the figure
    by removal alone is reported); (b) a count turns on payments and returns of its project and what names them, and
    on nothing else, by removal alone; (c) sizes are reported; (d) the closed needed events alone give the true value,
    every one has a stating paragraph of at least 40 characters (that of a lead event may occur twice in the sources: the
    number of statements affected is reported, and `check_lead_paragraphs_quotable` states the requirement), and quoting
    those paragraphs earns them all and gives the true value."""
    made = made_of(seed, index)
    events, by_id = made['events'], {e['id']: e for e in made['events']}
    told, leads = naming_by_text(seed, index)
    expected = {i: named for i, (named, _) in told.items()}
    expected.update(leads)
    sources = sources_of(case(seed, index))
    earns, spot_of = collections.defaultdict(set), {}
    for entry in made['passages']:
        spot = (entry['file'], entry['paragraph'])
        earns[spot] |= set(entry['event_ids'])
        if entry['role'] in ('states', 'names'):
            for i in entry['event_ids']:
                spot_of[i] = spot
    queries = statement_queries(seed, index)
    gaps, repeated = collections.Counter(), set()
    sizes = {'removal': [], 'closed': []}
    for key, q in queries.items():
        true = big_minutes.answer(q, events)
        needed, closed = needed_of(seed, index, key), closed_needed_of(seed, index, key)
        sizes['removal'].append(len(needed))
        sizes['closed'].append(len(closed))
        for i in closed:
            need(expected.get(i, set()) <= closed, f'{key}: {i} is needed and what fixes its name is not')
        gaps[q['kind']] += any(not expected.get(i, set()) <= needed for i in needed)
        if q['kind'] == 'count':
            for r in events:
                if r['kind'] == 'return' and r['subject'] == q['subject'] and r['date'] <= q['until']:
                    need(r['target'] not in needed, f'{key}: the payment reversed by {r["id"]} is needed by a count dated after it')
            naming = set()
            for i in needed:
                if by_id[i]['kind'] in ('pay', 'return'):
                    todo = [i]
                    while todo:
                        for d in expected.get(todo.pop(), set()):
                            if d not in naming:
                                naming.add(d)
                                todo.append(d)
            for i in needed:
                kind = by_id[i]['kind']
                need((kind in ('pay', 'return') and by_id[i]['subject'] == q['subject']) or i in naming,
                     f'{key}: a count turns on {i} ({kind} of {by_id[i]["subject"]}), which is neither a payment or return of the '
                     f'project nor what names one')
        need(is_balance_sum(q) or big_minutes.answer(q, [e for e in events if e['id'] in closed]) == true,
             f'{key}: the closed needed events alone differ')
        spots = set()
        for i in closed:
            need(i in spot_of, f'{key}: {i} has no stating paragraph')
            file, number = spot_of[i]
            text = sources[file].rstrip('\n').split('\n\n')[number]
            need(len(text) >= 40, f'{key}: the paragraph of {i} is shorter than 40 characters')
            if sum(body.count(text) for body in sources.values()) > 1:
                # A lead paragraph is one bare sentence of the ordinary business and can occur twice in a dossier (see
                # `check_lead_paragraphs_quotable`); a stating paragraph never does.
                need(by_id[i]['kind'] == 'names', f'{key}: the stating paragraph of {i} occurs more than once in the sources: {text[:100]!r}')
                repeated.add(key)
            spots.add(spot_of[i])
        cited = set().union(*(earns[s] for s in spots))
        need(closed <= cited and (is_balance_sum(q) or big_minutes.answer(q, [e for e in events if e['id'] in cited]) == true),
             f'{key}: quoting the stating paragraphs of the needed events is not a sufficient basis')
    spread = lambda xs: f'{min(xs)}/{sorted(xs)[len(xs) // 2]}/{max(xs)}'  # noqa: E731
    return (f'{len(queries)} statements; needed by removal {spread(sizes["removal"])}, closed {spread(sizes["closed"])} (min/median/max); '
            f'a needed event whose name-fixing event is not needed by removal alone: {sum(gaps.values())} statements '
            f'{dict(sorted(gaps.items()))}; none after closing; {len(repeated)} statements need a lead paragraph that occurs twice')


# ---------- Small event lists written by hand, with the expected value and the expected needed set ----------
#
# Written from procedure.md's rules and the subset rule of the checked-answers spec (section 3.3) before the interpreter
# was changed or run on them (the cases numbered 13 and above, and the changed expectations of 5, 8 and 9b, after the
# ruling on names and on counts of 14 November, again before the interpreter was run on them). A is project 0, B project 1,
# C project 2; meeting m has the date 2036-(m+1)-10. A query may carry a sixth item: the needed set closed under what fixes
# a name (`named_by`), the set the checker takes the statement to turn on. Each list gives its
# events (id, kind, meeting, project, fields) and then (statement kind, project, meeting, value, needed set), where the
# needed set is the events whose removal from the list changes the value or makes it None. `HAND_SUBSETS` gives only
# the value of statements on part of a list (what the rule says a subset gives).

HAND_PROJECTS = ('Alpha Hall Repair', 'Beta Hostel Extension', 'Gamma Library Roof', 'Delta Clinic Rewiring',
                 'Epsilon Pavilion Extension')       # D and E serve the award-side cases


HAND_CASES = [
 # 1. a relative instalment and its referent
 dict(name='relative instalment', events=[
        ('a1', 'award', 1, 0, dict(amount=20000)),
        ('p1', 'pay', 3, 0, dict(spec={'type': 'abs', 'amount': 4000})),
        ('p2', 'pay', 5, 0, dict(spec={'type': 'first'}))],
      queries=[('total', 0, 5, 8000, {'p1', 'p2'}),
               ('count', 0, 5, 2, {'p1', 'p2'}),
               ('total', 0, 3, 4000, {'p1'}),
               ('balance', 0, 5, 12000, {'a1', 'p1', 'p2'}),
               ('award', 0, 5, 20000, {'a1'})]),
 # 2. a correction read back into an earlier relative payment (the correction is minuted after the date asked about)
 dict(name='correction read back', events=[
        ('a1', 'award', 1, 0, dict(amount=20000)),
        ('p1', 'pay', 3, 0, dict(spec={'type': 'frac_award', 'award_m': 1, 'frac': [1, 2]})),
        ('c1', 'correct', 6, 0, dict(target='a1', target_kind='award', target_m=1, new=24000))],
      queries=[('total', 0, 3, 12000, {'a1', 'p1', 'c1'}),
               ('award', 0, 3, 24000, {'a1', 'c1'}),
               ('award', 0, 7, 24000, {'a1', 'c1'}),
               ('balance', 0, 7, 12000, {'a1', 'p1', 'c1'})]),
 # 3. a returned payment: it stops counting from the meeting that reports the return, and not before
 dict(name='returned payment', events=[
        ('a1', 'award', 1, 0, dict(amount=10000)),
        ('p1', 'pay', 2, 0, dict(spec={'type': 'abs', 'amount': 3000})),
        ('p2', 'pay', 4, 0, dict(spec={'type': 'abs', 'amount': 2000})),
        ('r1', 'return', 6, 0, dict(target='p1', target_m=2))],
      queries=[('count', 0, 7, 1, {'p2', 'r1'}),
               ('total', 0, 7, 2000, {'p2', 'r1'}),
               ('count', 0, 5, 2, {'p1', 'p2'}),
               ('total', 0, 5, 5000, {'p1', 'p2'}),
               ('largest', 0, 7, 2000, {'p2', 'r1'})]),
 # 4. a rescinded award
 dict(name='rescinded award', events=[
        ('a1', 'award', 1, 0, dict(amount=10000)),
        ('b1', 'award', 2, 1, dict(amount=8000)),
        ('x1', 'rescind', 3, 0, {})],
      queries=[('projects', None, 4, 1, {'b1', 'x1'}),
               ('projects', None, 2, 2, {'a1', 'b1'}),
               ('award', 0, 4, 'none', {'a1', 'x1'}),
               ('award', 0, 2, 10000, {'a1'})]),
 # 5. an authorisation later reported paid (and a second authorisation that nothing reports)
 dict(name='authorisation reported paid', events=[
        ('a1', 'award', 1, 0, dict(amount=10000)),
        ('u1', 'authorise', 2, 0, dict(amount=3000)),
        ('u2', 'authorise', 3, 0, dict(amount=5000)),
        ('p1', 'pay', 5, 0, dict(spec={'type': 'authorised', 'auth': 'u1'}))],
      queries=[('total', 0, 5, 3000, {'u1', 'p1'}),
               # a count does not turn on amounts: the payment is a payment whatever its sum rests on (before 14 November
               # the expectation was {'u1', 'p1'})
               ('count', 0, 5, 1, {'p1'}),
               ('balance', 0, 5, 7000, {'a1', 'u1', 'p1'})]),
 # 6. a restatement in a treasurer's summary (not a payment)
 dict(name='restatement', events=[
        ('a1', 'award', 1, 0, dict(amount=10000)),
        ('p1', 'pay', 2, 0, dict(spec={'type': 'abs', 'amount': 3000})),
        ('s1', 'restate', 3, 0, dict(entries=[{'pay': 'p1', 'amount': 3000}]))],
      queries=[('total', 0, 4, 3000, {'p1'}),
               ('count', 0, 4, 1, {'p1'}),
               ('largest', 0, 4, 3000, {'p1'})]),
 # 7. a motion that fell and a variation that was deferred
 dict(name='fell motion', events=[
        ('a1', 'award', 1, 0, dict(amount=10000)),
        ('p1', 'pay', 2, 0, dict(spec={'type': 'abs', 'amount': 3000})),
        ('f1', 'fell_vary', 3, 0, dict(sign=1, delta={'money': 2000})),
        ('d1', 'defer_vary', 4, 0, dict(sign=-1, delta={'frac': [1, 2]}))],
      queries=[('award', 0, 5, 10000, {'a1'}),
               ('balance', 0, 5, 7000, {'a1', 'p1'})]),
 # 8. a payment equal to another project's first instalment, which a later correction changes
 dict(name='first instalment of another project', events=[
        ('a1', 'award', 1, 0, dict(amount=10000)),
        ('bp1', 'pay', 2, 1, dict(spec={'type': 'abs', 'amount': 2500})),
        ('bp2', 'pay', 3, 1, dict(spec={'type': 'abs', 'amount': 4000})),
        ('ap1', 'pay', 4, 0, dict(spec={'type': 'first_of', 'other': 'Beta Hostel Extension'})),
        ('c1', 'correct', 6, 1, dict(target='bp1', target_kind='pay', target_m=2, new=3000))],
      queries=[('total', 0, 4, 3000, {'ap1', 'bp1', 'c1'}),
               # (before 14 November: {'ap1', 'bp1'}; the other project's payment fixes only an amount)
               ('count', 0, 4, 1, {'ap1'}),
               ('total', 1, 4, 7000, {'bp1', 'bp2', 'c1'})]),
 # 9. the largest of several payments, and the largest when a balance payment depends on the smaller ones
 dict(name='largest payment', events=[
        ('a1', 'award', 1, 0, dict(amount=12000)),
        ('p1', 'pay', 2, 0, dict(spec={'type': 'abs', 'amount': 2000})),
        ('p2', 'pay', 3, 0, dict(spec={'type': 'abs', 'amount': 5000})),
        ('p3', 'pay', 4, 0, dict(spec={'type': 'abs', 'amount': 3000}))],
      queries=[('largest', 0, 4, 5000, {'p2'}),
               ('largest', 0, 2, 2000, {'p1'}),
               ('total', 0, 4, 10000, {'p1', 'p2', 'p3'})]),
 dict(name='largest payment, balance', events=[
        ('a1', 'award', 1, 0, dict(amount=12000)),
        ('p1', 'pay', 2, 0, dict(spec={'type': 'abs', 'amount': 2000})),
        ('p2', 'pay', 3, 0, dict(spec={'type': 'abs', 'amount': 4000})),
        ('p3', 'pay', 4, 0, dict(spec={'type': 'balance'}))],
      queries=[('largest', 0, 4, 6000, {'a1', 'p1', 'p2', 'p3'}),
               # the balance payment absorbs whatever the earlier ones were: the total is the award, and turns on it
               ('total', 0, 4, 12000, {'a1', 'p3'}),
               # (before 14 November: {'a1', 'p1', 'p2', 'p3'}; the award fixes only the amount of p3)
               ('count', 0, 4, 3, {'p1', 'p2', 'p3'}),
               ('balance', 0, 4, 0, {'a1', 'p3'})]),
 # 10. the first full payment, after a variation lowered the award
 dict(name='first full payment after a variation', events=[
        ('a1', 'award', 1, 0, dict(amount=10000)),
        ('p1', 'pay', 3, 0, dict(spec={'type': 'abs', 'amount': 6000})),
        ('v1', 'vary', 4, 0, dict(sign=-1, delta={'money': 2000})),
        ('p2', 'pay', 6, 0, dict(spec={'type': 'abs', 'amount': 2000}))],
      queries=[('first', 0, None, '2036-07-10', {'a1', 'p1', 'v1', 'p2'}),
               ('award', 0, 5, 8000, {'a1', 'v1'}),
               ('balance', 0, 6, 0, {'a1', 'v1', 'p1', 'p2'})]),
 # 11. a variation by a fraction, and a correction read back into it
 dict(name='variation and correction', events=[
        ('a1', 'award', 1, 0, dict(amount=10000)),
        ('v1', 'vary', 3, 0, dict(sign=1, delta={'frac': [1, 2]})),
        ('c1', 'correct', 5, 0, dict(target='a1', target_kind='award', target_m=1, new=12000))],
      queries=[('award', 0, 4, 18000, {'a1', 'v1', 'c1'}),
               ('award', 0, 2, 12000, {'a1', 'c1'})]),
 # 12. a chain of instalments: the third equals the second, which equals the first
 dict(name='chain of instalments', events=[
        ('a1', 'award', 1, 0, dict(amount=20000)),
        ('p1', 'pay', 2, 0, dict(spec={'type': 'abs', 'amount': 4000})),
        ('p2', 'pay', 3, 0, dict(spec={'type': 'first'})),
        ('p3', 'pay', 4, 0, dict(spec={'type': 'ordinal', 'k': 2}))],
      queries=[('total', 0, 4, 12000, {'p1', 'p2', 'p3'}),
               ('count', 0, 4, 3, {'p1', 'p2', 'p3'})]),
 # 13. the project is named through its sponsor (the application is the one event the name rests on), in one payment
 dict(name='named through its sponsor', events=[
        ('i1', 'intro', 0, 0, {}),
        ('a1', 'award', 1, 0, dict(amount=20000)),
        ('p1', 'pay', 3, 0, dict(spec={'type': 'abs', 'amount': 4000}, named_by=['i1'])),
        ('p2', 'pay', 5, 0, dict(spec={'type': 'abs', 'amount': 3000}))],
      queries=[('total', 0, 5, 7000, {'i1', 'p1', 'p2'}),
               ('count', 0, 5, 2, {'i1', 'p1', 'p2'}),
               ('largest', 0, 5, 4000, {'i1', 'p1'}),
               ('total', 0, 3, 4000, {'i1', 'p1'}),
               ('award', 0, 5, 20000, {'a1'}),
               ('balance', 0, 5, 13000, {'a1', 'i1', 'p1', 'p2'})]),
 # 14. the Nth undertaking to receive the backing: every award up to and including the project's own is a dependency, also one
 # that is rescinded later, and an award made after the project's place is not (C is awarded after B)
 dict(name='order of backing', events=[
        ('a1', 'award', 1, 0, dict(amount=10000)),
        ('b1', 'award', 2, 1, dict(amount=8000)),
        ('q1', 'pay', 4, 1, dict(spec={'type': 'abs', 'amount': 3000}, named_by=['a1', 'b1'])),
        ('g1', 'award', 3, 2, dict(amount=9000)),
        ('x1', 'rescind', 5, 0, {}),
        ('q2', 'pay', 6, 1, dict(spec={'type': 'abs', 'amount': 2000}))],
      queries=[('total', 1, 6, 5000, {'a1', 'b1', 'q1', 'q2'}),
               ('count', 1, 6, 2, {'a1', 'b1', 'q1', 'q2'}),
               ('largest', 1, 6, 3000, {'a1', 'b1', 'q1'}),
               ('award', 1, 6, 8000, {'b1'}),
               ('projects', None, 6, 2, {'b1', 'g1', 'x1'}, {'b1', 'g1', 'x1'})]),
 # 15. the paragraph above: the lead paragraph is an event of its own (n1, n2) that the told event depends on; n1 names the
 # project through its sponsor, n2 by a short form
 dict(name='paragraph above', events=[
        ('i1', 'intro', 0, 0, {}),
        ('a1', 'award', 1, 0, dict(amount=20000)),
        ('n1', 'names', 3, 0, dict(named_by=['i1'])),
        ('p1', 'pay', 3, 0, dict(spec={'type': 'abs', 'amount': 5000}, named_by=['n1'])),
        ('n2', 'names', 5, 0, {}),
        ('p2', 'pay', 5, 0, dict(spec={'type': 'abs', 'amount': 2000}, named_by=['n2']))],
      queries=[('total', 0, 5, 7000, {'i1', 'n1', 'p1', 'n2', 'p2'}),
               ('count', 0, 5, 2, {'i1', 'n1', 'p1', 'n2', 'p2'}),
               ('total', 0, 3, 5000, {'i1', 'n1', 'p1'}),
               ('largest', 0, 5, 5000, {'i1', 'n1', 'p1'})]),
 # 16. "a sum equal to the second instalment": the instalments before it fix the numbering, so it is read only with the first
 dict(name='second instalment', events=[
        ('a1', 'award', 1, 0, dict(amount=30000)),
        ('p1', 'pay', 2, 0, dict(spec={'type': 'abs', 'amount': 4000})),
        ('p2', 'pay', 3, 0, dict(spec={'type': 'abs', 'amount': 6000})),
        ('p3', 'pay', 5, 0, dict(spec={'type': 'ordinal', 'k': 2}))],
      queries=[('total', 0, 5, 16000, {'p1', 'p2', 'p3'}),
               ('count', 0, 5, 3, {'p1', 'p2', 'p3'}),
               ('largest', 0, 5, 6000, {'p2'})]),
 # 17. a count does not turn on amounts, and a returned payment whose amount cannot be read still stops counting
 dict(name='count and amounts', events=[
        ('a1', 'award', 1, 0, dict(amount=20000)),
        ('p1', 'pay', 2, 0, dict(spec={'type': 'abs', 'amount': 4000})),
        ('p2', 'pay', 3, 0, dict(spec={'type': 'frac_award', 'award_m': 1, 'frac': [1, 2]})),
        ('p3', 'pay', 5, 0, dict(spec={'type': 'balance'})),
        ('r1', 'return', 6, 0, dict(target='p2', target_m=3))],
      queries=[('count', 0, 7, 2, {'p1', 'p3', 'r1'}, {'p1', 'p3', 'r1'}),
               ('total', 0, 7, 10000, {'a1', 'p2', 'r1', 'p3'}),
               ('count', 0, 5, 3, {'p1', 'p2', 'p3'})]),
 # 18. a correction names its target directly while the target's own paragraph names the project through the paragraph above
 # (n1): the correction is read only with the corrected entry, and the corrected entry only with the lead paragraph
 dict(name='correction and a target named through the paragraph above', events=[
        ('n1', 'names', 1, 0, {}),
        ('a1', 'award', 1, 0, dict(amount=20000, named_by=['n1'])),
        ('c1', 'correct', 3, 0, dict(target='a1', target_kind='award', target_m=1, new=12000))],
      queries=[('award', 0, 4, 12000, {'a1', 'c1', 'n1'}, {'a1', 'c1', 'n1'}),
               ('balance', 0, 4, 12000, {'a1', 'c1', 'n1'})]),
 # 19. what fixes a name can be left out of what a statement turns on by removal alone: the application ties the sponsor to the
 # award and to the rescission, removing it removes both, and the number of projects is the same
 dict(name='name cancelled by removal', events=[
        ('i1', 'intro', 0, 0, {}),
        ('a1', 'award', 1, 0, dict(amount=10000, named_by=['i1'])),
        ('b1', 'award', 2, 1, dict(amount=8000)),
        ('x1', 'rescind', 3, 0, dict(named_by=['i1']))],
      queries=[('projects', None, 4, 1, {'b1', 'x1'}, {'b1', 'x1', 'i1'})]),
]

# Value-only expectations for lists that lack something (what the rule says of a subset).
# Each: (name, events, [(kind, project, meeting, expected)]); the events are those of the cases above, by id.
HAND_SUBSETS = [
 # a relative payment whose referent is absent is left out; the award it does not touch is still read
 ('first instalment absent', 'relative instalment', ['a1', 'p2'],
  # (the count was 0 before 14 November: p2 is a payment whose amount cannot be read, and a count does not turn on amounts)
  [('total', 0, 5, 0), ('count', 0, 5, 1), ('largest', 0, 5, 'none'), ('award', 0, 5, 20000), ('balance', 0, 5, 20000)]),
 # a return of an absent payment is left out
 ('return of an absent payment', 'returned payment', ['a1', 'p2', 'r1'],
  [('count', 0, 7, 1), ('total', 0, 7, 2000)]),
 # a correction of an absent award, a variation and a rescission of an absent award: left out; the award itself is not
 # established, whatever is said of it
 ('correction of an absent award', 'correction read back', ['p1', 'c1'],
  # (count 1: p1 is a share of the absent award, so counted and not summed; it was 0 before 14 November)
  [('award', 0, 7, None), ('balance', 0, 7, None), ('first', 0, None, None), ('total', 0, 7, 0), ('count', 0, 7, 1),
   ('largest', 0, 7, 'none'), ('projects', None, 7, 0)]),
 ('variation of an absent award', 'variation and correction', ['v1', 'c1'],
  [('award', 0, 4, None), ('projects', None, 4, 0)]),
 ('rescission of an absent award', 'rescinded award', ['b1', 'x1'],
  [('award', 0, 4, None), ('projects', None, 4, 1), ('award', 1, 4, 8000)]),
 # an authorised payment whose authorisation is absent is left out
 ('authorisation absent', 'authorisation reported paid', ['a1', 'u2', 'p1'],
  # (count 1: the report that the authorised sum was paid is a payment; it was 0 before 14 November)
  [('total', 0, 5, 0), ('count', 0, 5, 1), ('balance', 0, 5, 10000)]),
 # a payment relative to an award that is absent is left out; a payment that is absolute stays
 ('award absent, relative and absolute payment', 'correction read back', ['p1'],
  [('total', 0, 3, 0), ('award', 0, 3, None)]),
 ('award absent, absolute payment', 'largest payment', ['p1', 'p2'],
  [('total', 0, 3, 7000), ('largest', 0, 3, 5000), ('award', 0, 3, None), ('balance', 0, 3, None),
   ('first', 0, None, None)]),
 # a missing later correction, return or variation is simply not applied
 ('later correction missing', 'correction read back', ['a1', 'p1'],
  [('total', 0, 3, 10000), ('award', 0, 7, 20000)]),
 ('later return missing', 'returned payment', ['a1', 'p1', 'p2'],
  [('count', 0, 7, 2), ('total', 0, 7, 5000)]),
 # a chain: a second relative payment whose referent is itself a relative payment left out
 ('chain: the middle instalment absent', 'chain of instalments', ['a1', 'p1', 'p3'],
  [('total', 0, 4, 4000), ('count', 0, 4, 2)]),         # (count 1 before 14 November)
 ('chain: the first instalment absent leaves out the second and, through it, the third', 'chain of instalments',
  ['a1', 'p2', 'p3'], [('total', 0, 4, 0), ('count', 0, 4, 2)]),         # (count 0 before 14 November)
 ('referent present, award absent', 'relative instalment', ['p1', 'p2'],
  [('total', 0, 5, 8000)]),
 ('until before every meeting of the list', 'relative instalment', ['a1', 'p1'],
  [('total', 0, '2035-12-10', 0), ('award', 0, '2035-12-10', 'none'), ('projects', None, '2035-12-10', 0)]),
 ('the empty list', 'relative instalment', [],
  [('projects', None, 5, 0), ('total', 0, 5, 0), ('count', 0, 5, 0), ('largest', 0, 5, 'none'), ('award', 0, 5, None),
   ('balance', 0, 5, None), ('first', 0, None, None)]),
 # nothing at all, and an empty list
 ('no events of the project', 'rescinded award', ['b1'],
  [('total', 0, 4, 0), ('count', 0, 4, 0), ('largest', 0, 4, 'none'), ('award', 0, 4, None), ('projects', None, 4, 1)]),
 # a correction paragraph earns the correction only: quoted alone it corrects nothing, and the corrected entry is read only
 # with the lead paragraph that fixes its name
 ('correction alone', 'correction and a target named through the paragraph above', ['c1'],
  [('award', 0, 4, None), ('balance', 0, 4, None)]),
 ('correction and its target, without the lead paragraph', 'correction and a target named through the paragraph above', ['a1', 'c1'],
  [('award', 0, 4, None), ('balance', 0, 4, None)]),
 ('correction, its target and the lead paragraph', 'correction and a target named through the paragraph above', ['n1', 'a1', 'c1'],
  [('award', 0, 4, 12000), ('balance', 0, 4, 12000)]),
 ('target and lead paragraph, without the correction', 'correction and a target named through the paragraph above', ['n1', 'a1'],
  [('award', 0, 4, 20000)]),
 # a return paragraph earns the return and not the payment: quoted without its payment, the return reverses nothing
 ('return quoted without its payment', 'returned payment', ['r1'],
  [('count', 0, 7, 0), ('total', 0, 7, 0), ('largest', 0, 7, 'none')]),
 # a payment whose amount cannot be read and that was returned does not count
 ('unreadable payment, returned', 'count and amounts', ['p2', 'r1'],
  [('count', 0, 7, 0), ('total', 0, 7, 0)]),
 ('unreadable payment, not returned', 'count and amounts', ['p2', 'p1'],
  [('count', 0, 7, 2), ('total', 0, 7, 4000), ('largest', 0, 7, 4000)]),
 # the sponsor form: the payment is left out (and so not counted) without the application, read with it
 ('sponsor form without the application', 'named through its sponsor', ['p1', 'p2'],
  [('total', 0, 5, 3000), ('count', 0, 5, 1), ('largest', 0, 5, 3000)]),
 ('sponsor form with the application', 'named through its sponsor', ['i1', 'p1', 'p2'],
  [('total', 0, 5, 7000), ('count', 0, 5, 2), ('award', 0, 5, None)]),
 # the order of backing: the earlier award (rescinded later) fixes the place; the award after it is not needed
 ('order of backing without the earlier award', 'order of backing', ['b1', 'q1', 'q2'],
  [('total', 1, 6, 2000), ('count', 1, 6, 1), ('award', 1, 6, 8000)]),
 ('order of backing without the project\'s own award', 'order of backing', ['a1', 'q1', 'q2'],
  [('total', 1, 6, 2000), ('count', 1, 6, 1), ('award', 1, 6, None), ('balance', 1, 6, None)]),
 ('order of backing, the earlier award rescinded in the subset', 'order of backing', ['a1', 'x1', 'b1', 'q1', 'q2'],
  [('total', 1, 6, 5000), ('count', 1, 6, 2), ('projects', None, 6, 1)]),
 ('order of backing, the rescission without the award it ends', 'order of backing', ['x1', 'b1', 'q1', 'q2'],
  [('total', 1, 6, 2000), ('projects', None, 6, 1)]),
 ('order of backing, without the award made after the place', 'order of backing', ['a1', 'b1', 'q1', 'q2'],
  [('total', 1, 6, 5000), ('count', 1, 6, 2), ('largest', 1, 6, 3000)]),
 # the paragraph above: the lead event stands between the told event and what names the project
 ('paragraph above without the lead event', 'paragraph above', ['i1', 'p1'],
  [('total', 0, 5, 0), ('count', 0, 5, 0)]),
 ('paragraph above, lead event without the application', 'paragraph above', ['n1', 'p1', 'n2', 'p2'],
  [('total', 0, 5, 2000), ('count', 0, 5, 1)]),
 ('paragraph above with the lead event and the application', 'paragraph above', ['i1', 'n1', 'p1'],
  [('total', 0, 5, 5000), ('count', 0, 5, 1)]),
 # "the second instalment": read only with the instalments that fix the numbering, counted without them
 ('second instalment without the first', 'second instalment', ['p2', 'p3'],
  [('total', 0, 5, 6000), ('count', 0, 5, 2), ('largest', 0, 5, 6000)]),
 ('second instalment without the instalment it equals', 'second instalment', ['p1', 'p3'],
  [('total', 0, 5, 4000), ('count', 0, 5, 2), ('largest', 0, 5, 4000)]),
 ('second instalment with both', 'second instalment', ['p1', 'p2', 'p3'],
  [('total', 0, 5, 16000), ('count', 0, 5, 3)]),
 ('second instalment alone', 'second instalment', ['p3'],
  [('total', 0, 5, 0), ('count', 0, 5, 1)]),
]


def hand_date(m):
    return f'2036-{m + 1:02d}-10'


def hand_events(spec):
    events = []
    for ident, kind, m, p, fields in spec:
        event = {'id': ident, 'kind': kind, 'm': m, 'date': hand_date(m), 'p': p, 'subject': HAND_PROJECTS[p],
                 'decides': kind in ('award', 'vary', 'correct', 'rescind', 'pay', 'return')}
        event.update(json.loads(json.dumps(fields)))
        events.append(event)
    return big_minutes.link(events)


def hand_query(kind, project, meeting):
    until = None if kind == 'first' else meeting if isinstance(meeting, str) else hand_date(meeting)
    subject = None if kind == 'projects' else HAND_PROJECTS[project]
    return query_of(kind, subject, until)


def check_hand_cases(seed=None, index=None):
    """Every hand-written list gives the value and the needed set written down for it, the needed events alone give the
    value, and every hand-written subset gives the value the rule says. Any disagreement is reported with both readings."""
    by_name = {c['name']: c for c in HAND_CASES}
    problems, checked = [], 0
    for c in HAND_CASES:
        events = hand_events(c['events'])
        for kind, project, meeting, value, needed, *more in c['queries']:
            q = hand_query(kind, project, meeting)
            got = big_minutes.answer(q, events)
            cut = {e['id'] for e in events if big_minutes.answer(q, [x for x in events if x['id'] != e['id']]) != got}
            closed = set(cut)
            todo = list(cut)
            while todo:                    # closed under what fixes a name, by the module's `depends`
                for d in big_minutes.depends(events)[todo.pop()]['name']:
                    if d not in closed:
                        closed.add(d)
                        todo.append(d)
            alone = big_minutes.answer(q, [e for e in events if e['id'] in closed])
            checked += 1
            if more and closed != more[0]:
                problems.append(f'{c["name"]}: {kind} of {HAND_PROJECTS[project] if project is not None else "all"} at {meeting}: '
                                f'by hand closed {sorted(more[0])}, by the interpreter {sorted(closed)}')
            if got != value or cut != needed or alone != got:
                problems.append(f'{c["name"]}: {kind} of {HAND_PROJECTS[project] if project is not None else "all"} at {meeting}: '
                                f'by hand {value!r} with {sorted(needed)}, by the interpreter {got!r} with {sorted(cut)} '
                                f'(the closed needed events alone give {alone!r})')
    for name, base, ids, queries in HAND_SUBSETS:
        events = [e for e in hand_events(by_name[base]['events']) if e['id'] in ids]
        for kind, project, meeting, value in queries:
            got = big_minutes.answer(hand_query(kind, project, meeting), events)
            checked += 1
            if got != value:
                problems.append(f'subset {name}: {kind} at {meeting}: by hand {value!r}, by the interpreter {got!r}')
    need(not problems, f'{len(problems)} of {checked} hand-written expectations disagree: {problems[:3]}')
    return (f'{len(HAND_CASES)} hand-written lists with {sum(len(c["queries"]) for c in HAND_CASES)} statements (value and '
            f'needed set), {len(HAND_SUBSETS)} subsets with {sum(len(s[3]) for s in HAND_SUBSETS)} values')


def no_running_totals(sources):
    pattern = re.compile(r'\b(to date|so far|in total|altogether|cumulative|running total|in all|payments to date)\b', re.I)
    for name in volumes_in(sources):
        for block in sources[name].split('\n\n'):
            need(not pattern.search(block), f'a running total in {name}: {block[:120]!r}')
    return 'no running totals in the minute books'


def check_no_running_totals(seed, index=1):
    return no_running_totals(sources_of(case(seed, index)))


DECIDING = ('award', 'vary', 'correct', 'rescind', 'pay', 'return')
# The scout run's filter: two lists of words, then the project names.
VERBS_ONE = re.compile(r'award|Treasurer:|treasurer reported|treasurer,|treasurer told|Payment of|Correction:|should have read|should be read', re.I)
VERBS_TWO = re.compile(r'resolved|agreed|Carried:|grant|instalment|authoris|paid|transferred|sent|application from|returned|recalled|corrected', re.I)
STOP_WORDS = {'the', 'and', 'for', 'project', 'scheme', 'that', 'with', 'from', 'this', 'their', 'at', 'in', 'to', 'of', 'a'}


def word_set(text):
    return {w.lower() for w in re.findall(r"[A-Za-z']+", text) if len(w) >= 3 and w.lower() not in STOP_WORDS}


def minute_blocks(seed, index):
    sources = sources_of(case(seed, index))
    return {(v, n): text for v, name in enumerate(VOLUMES) if name in sources
            for n, text in enumerate(sources[name].rstrip('\n').split('\n\n'))}


def check_filters(seed, index=1, any_word=True):
    """What two verb lists keep, what a reader that keeps any word of the subject's names keeps, and how many deciding
    passages name their project with no word of its name. Reported on every draw; the bounds are the design's."""
    book, runs = read(seed, index)
    blocks = {k: t for k, t in minute_blocks(seed, index).items() if not t.startswith('#') and not t.startswith('<a')}
    total = sum(len(t) for t in blocks.values())
    kept = sum(len(t) for t in blocks.values() if VERBS_ONE.search(t) or VERBS_TWO.search(t))
    deciding = [f for f in book.facts if f['t'] in DECIDING]
    unique = {(f['key']): f for f in deciding}.values()
    with_verb = sum(1 for f in unique if VERBS_ONE.search(book.blocks[f['key']]) or VERBS_TWO.search(book.blocks[f['key']]))
    no_name = sum(1 for f in unique if not word_set(f['p']) & word_set(book.blocks[f['key']]))
    sponsor = {f['p']: f['sponsor'] for f in book.facts if f['t'] == 'intro'}
    keep_words = {}
    for name in book.names:
        words_of = word_set(name) | word_set(sponsor.get(name, ''))
        for alias, owners in book.alias_of.items():
            if name in owners:
                words_of |= word_set(alias)
        keep_words[name] = words_of
    missed = projects = 0
    for text in report_statements(case(seed, index), 'correct'):
        if is_sum_text(text):
            continue
        kind, who, until, _ = read_statement(book, text)
        if who is None:
            continue
        projects += 1
        missed += any(not keep_words[who] & word_set(book.blocks[f['key']]) for f in deciding
                      if f['p'] == who and (until is None or f['m'] <= until))
    share = {'text': kept / total, 'deciding_verbs': with_verb / len(unique), 'no_name_word': no_name / len(unique),
             'any_word_misses': missed / projects}
    need(share['text'] >= 0.70, f'the verb lists keep only {share["text"]:.0%} of the text')
    need(share['deciding_verbs'] <= 0.75, f'the verb lists keep {share["deciding_verbs"]:.0%} of the deciding passages')
    need(share['no_name_word'] >= 0.40, f'only {share["no_name_word"]:.0%} of deciding passages name the project with no word of its name')
    if any_word:        # editions 3 and 4 have only four statements about one project: the share is reported, not asserted
        need(share['any_word_misses'] >= 0.50, f'the any-word reader misses a deciding passage on only {share["any_word_misses"]:.0%} of statements')
    return (f'verb lists keep {share["text"]:.0%} of the text and {share["deciding_verbs"]:.0%} of {len(unique)} deciding passages; '
            f'{share["no_name_word"]:.0%} name the project with no word of its name; the any-word reader misses a deciding '
            f'passage on {share["any_word_misses"]:.0%} of project statements')


DECIDING_WORDS = re.compile(
    r"\b(award(?:ed|s|ing)?|grant(?:ed|s)?|paid|pay(?:s|ing|ment|ments)?|funded|approv\w+|authoris\w+|rescind\w*|"
    r"cancel\w*|withdr[a-z]+|return(?:ed|s)?|recall(?:ed|s)?|correct\w*|transfer\w*|releas\w+|assign\w+|substitut\w+|"
    r"register\w*|cheques?|repaid|credited|debited|backed|settled|adopted|resolved|decided|carried|reversed)\b", re.I)


def check_nothing_outside_the_map_decides(seed, index=1):
    """No paragraph that the passage map leaves out holds a form of a project's name together with a verb of
    deciding or paying (patterns written here, not taken from the inventory)."""
    book, _ = read(seed, index)
    made = made_of(seed, index)
    mapped = {(VOLUMES.index(p['file']), p['paragraph']) for p in made['passages']}
    names = list(book.names) + list(book.alias_of) + list(book.sponsor_forms) + list(book.ordinal_forms)
    found = re.compile('|'.join(re.escape(n) for n in sorted(names, key=len, reverse=True)), re.I)
    checked = 0
    for key, text in minute_blocks(seed, index).items():
        if key in mapped or text.startswith('#') or text.startswith('<a'):
            continue
        checked += 1
        need(not (found.search(text) and DECIDING_WORDS.search(text)),
             f'an unmapped paragraph names a project and uses a deciding verb: {text[:200]!r}')
    non_deciding = [e for e in made['events'] if not e['decides']]
    need(non_deciding and all(e['id'] in {i for p in made['passages'] for i in p['event_ids']} for e in non_deciding),
         'a decoy is missing from the passage map')
    need(any(e['kind'] == 'outside' for e in non_deciding), 'no sum given by an outside body is told')
    return f'{checked} unmapped paragraphs checked; {len(non_deciding)} events that decide nothing are all in the map'


@per_edition
def legacy_flats_without(seed, index):
    """The whole state of the dossier with each event cut out in turn, under the reading the generator draws its dossiers
    by (`legacy_state`: a missing referent makes what depends on it not established)."""
    made = made_of(seed, index)
    events = [e for e in made['events'] if e['kind'] != 'names']     # a lead event is no fact of the generator's own reading
    return {e['id']: big_minutes.legacy_state([x for x in events if x['id'] != e['id']], made['meetings']) for e in events}


def legacy_needed_of(seed, index, key):
    made = made_of(seed, index)
    value = big_minutes.legacy_state(made['events'], made['meetings']).get(key)
    return {i for i, flat in legacy_flats_without(seed, index).items() if flat.get(key) != value}


def check_across_subjects(seed, index=1):
    """Statements that turn on a figure of another subject, and statements over every project. The generator chooses
    them under its own (legacy) reading, in which an event whose removal only makes the value not established counts as
    one the value turns on: at least four statements of the correct report turn on another subject's figure, and at
    least three on every award and rescission; both are asserted under that reading, as before. Under the reading the
    checker uses (needed = removal changes `answer`) the value turns on fewer events, so fewer statements cross
    subjects, and on some draws fewer than the four the generator promises: that count is reported, and nothing is
    asserted of it (the draws cannot change without changing the bytes). The number of projects holding an award turns
    on every award held at the date and on every rescission up to it (the award of a project rescinded by then is not
    one: the rescission is left out without it), which is asserted for at least three statements."""
    built, made = case(seed, index), made_of(seed, index)
    by_text, events = claims_by_text(made), made['events']
    by_id = {e['id']: e for e in events}
    queries = {}
    for row in key_rows(built, 'correct'):
        q = by_text[row['claim_text']]['query']
        queries[q['key']] = q
    crossing = lambda needed_of_key: [k for k, q in queries.items() if q['subject'] and any(  # noqa: E731
        by_id[i]['subject'] != q['subject'] for i in needed_of_key(k))]
    legacy_across = crossing(lambda k: legacy_needed_of(seed, index, k))
    across = crossing(lambda k: closed_needed_of(seed, index, k))
    legacy_whole = whole_file = 0
    for k, q in queries.items():
        if q['kind'] == 'projects':
            every = {e['id'] for e in events if e['kind'] in ('award', 'rescind') and e['date'] <= q['until']}
            legacy_whole += every <= legacy_needed_of(seed, index, k)
            rescinded = {e['subject'] for e in events if e['kind'] == 'rescind' and e['date'] <= q['until']}
            held = {i for i in every if by_id[i]['kind'] == 'rescind' or by_id[i]['subject'] not in rescinded}
            whole_file += held <= closed_needed_of(seed, index, k)
    need(len(legacy_across) >= 4, f'only {len(legacy_across)} statements turn on another subject (generator reading)')
    need(legacy_whole >= 3, f'only {legacy_whole} statements change if any one award or rescission is missed (generator reading)')
    need(whole_file >= 3, f'only {whole_file} statements turn on every award held and every rescission')
    return (f'generator reading: {len(legacy_across)} statements turn on another subject, {legacy_whole} count over every project; '
            f'needed events (closed under names): {len(across)} turn on another subject, {whole_file} turn on every award held and rescission')


def check_single_devices(seed, index=1):
    """A reader made of several shortcuts differs from the truth, on a lure statement, through exactly one of them."""
    built, made = case(seed, index), made_of(seed, index)
    book, runs = read(seed, index)
    count = len(book.meetings)
    bundles = {'ignores_corrections': ('corrections', 'rescissions', 'returns'),
               'counts_motions_as_decisions': ('fell_vary', 'defer_vary', 'defer_award', 'authorise')}
    alone = {d: run(book.facts, count, book.names, (d,), strict=False) for ds in bundles.values() for d in ds}
    lures = collections.Counter()
    for variant in ('correct', 'mixed'):
        for row in key_rows(built, variant):
            if is_sum_text(row['claim_text']):
                continue                # the rule is not applied to sums (supplement 5): `check_false_sum` stands in for it
            kind, who, until, value = read_statement(book, row['claim_text'])
            want = (kind, who, until)
            for name, devices in bundles.items():
                if name not in row['lure_states']:
                    continue
                theirs = runs[name][want]
                moved = [d for d in devices if alone[d][want] != runs['truth'][want]]
                lures[name] += 1
                need(len(moved) == 1 and alone[moved[0]][want] == theirs, f'{name} turns on {moved} for {row["claim_text"]!r}')
    return ', '.join(f'{n}: {c} lure rows, each through one device' for n, c in sorted(lures.items()))


# ---------- Editions 3 and 4: the check list of the sums ----------
#
# Written from the supplement (sections 3 to 9) and the rendered text; the tables below are written out here, from the
# supplement's table, and compared with the inventory's, never read from it.

EXPECTED_QUOTAS = {
    3: {'award': 2, 'single': 2, 'sum3': 2, 'sum5': 4, 'sum8': 6, 'upto': 0, 'projects': 2, 'span3': 2, 'span6': 2, 'book': 2},
    4: {'award': 2, 'single': 2, 'sum3': 0, 'sum5': 2, 'sum8': 4, 'upto': 6, 'projects': 2, 'span3': 2, 'span6': 2, 'book': 2},
    # edition 5 (the fifth-edition design, section 3): ten statements on the payment side, eight on the award side
    5: {'award': 2, 'single': 2, 'projects': 2, 'sum3': 0, 'sum5': 0, 'sum8': 2, 'upto': 2, 'span3': 2, 'span6': 2, 'book': 2,
        'asum8': 2, 'aupto': 4, 'abook': 2},
    # edition 6 (a dense world): no statement about the number of projects holding an award and no span of three meetings
    6: {'award': 2, 'single': 2, 'projects': 0, 'sum3': 0, 'sum5': 0, 'sum8': 4, 'upto': 8, 'span3': 0, 'span6': 2, 'book': 2,
        'asum8': 2, 'aupto': 0, 'abook': 2}}
# The figures of the sum rungs per report (design section 3): {rung: {figure: number}}; the rungs of edition 5 that are not
# here are those of the first supplement (half totals, half numbers of payments, as near as the quota allows).
EDITION5_FIGURES = {'sum8': {'total': 1, 'count': 1}, 'upto': {'total': 1, 'count': 1}, 'span3': {'total': 1, 'count': 1},
                    'span6': {'total': 1, 'count': 1}, 'book': {'total': 1, 'count': 1},
                    'asum8': {'award': 1, 'balance': 1}, 'aupto': {'award': 2, 'balance': 2}, 'abook': {'award': 1, 'balance': 1}}
# edition 6: the same rule (half of each rung one figure, half the other, as near as the quota allows), written out for its table
EDITION6_FIGURES = {'sum8': {'total': 2, 'count': 2}, 'upto': {'total': 4, 'count': 4}, 'span6': {'total': 1, 'count': 1},
                    'book': {'total': 1, 'count': 1}, 'asum8': {'award': 1, 'balance': 1}, 'abook': {'award': 1, 'balance': 1}}
AWARD_RUNGS = ('asum8', 'aupto', 'abook')
SINGLES = ('total', 'count', 'balance', 'largest', 'first')
STATEMENTS_PER_REPORT = 24
ALTERED = (8, 12)


def guarded(check):
    """Whatever else a check meets (a refusal of the checker, a type error) is reported as a failed check, not a crash."""
    @functools.wraps(check)
    def wrapper(*args):
        try:
            return check(*args)
        except (AssertionError, KeyError, ValueError, IndexError):
            raise
        except Exception as error:      # noqa: BLE001
            raise Failed(f'{type(error).__name__}: {str(error)[:300]}')
    return wrapper


def strip_value(query):
    return {k: v for k, v in query.items() if k != 'value'}


def rung_of(st, count):
    """The rung of a statement, from its own text: what its claim kind (`minutes_<rung>`) should say."""
    if st['shape'] == 'single':
        return st['kind'] if st['kind'] in ('award', 'projects') else 'single'
    side = 'a' if st['figure'] in AWARD_FIGURES else ''       # the award side: asum8, aupto, abook
    if st['shape'] == 'named':
        return f'{side}sum{len(st["names"])}'
    if st['shape'] == 'all':
        return side + ('book' if st['until'] == count - 1 else 'upto')
    return f'span{st["until"] - st["since"]}'


def claim_kind_of(st, count):
    rung = rung_of(st, count)
    return 'minutes_' + (st['kind'] if rung == 'single' else rung)


def identity(st):
    if st['shape'] == 'single':
        return ('single', st['kind'], st['who'], st['until'])
    return (st['shape'], st['figure'], tuple(sorted(st['names'])) if st['names'] else None, st['since'], st['until'])


def round_trip3(built, book, runs, readers):
    """Every statement of both reports, sums included, re-decided by this module's evaluator for the truth and for every flawed
    reader the dossier carries; every false sum states the sum the named reader gets (supplement 5, checks 1 and 3)."""
    need(book.errors == [], f'unreadable text: {book.errors[:2]}')
    projects, kinds, errors = book.projects, collections.Counter(), collections.Counter()
    need(projects == book.names, 'a project named in the minute books has no award in them')
    for variant in ('correct', 'mixed'):
        texts, rows = report_statements(built, variant), key_rows(built, variant)
        need(len(texts) == len(rows) == STATEMENTS_PER_REPORT, f'{variant}: {len(texts)} statements, {len(rows)} rows')
        for text, row in zip(texts, rows):
            need(text == row['claim_text'], 'report and key disagree on a statement')
            st = statement_of(book, text)
            need(decide(runs['truth'], projects, st) == row['semantic_state'], f'{variant}: verdict differs for {text!r}')
            for name in readers:
                need(decide(runs[name], projects, st) == row['lure_states'].get(name, row['semantic_state']),
                     f'{variant}: reader {name} differs for {text!r}')
            if variant == 'correct':
                need(row['semantic_state'] == 'supported' and row['error_type'] is None, f'a false statement in the correct report: {text!r}')
            if st['shape'] != 'single':
                kinds[st['shape']] += 1
                derivation, truth_sum = row['derivation'], sum_of(runs['truth'], projects, st)     # the key is compared with, not used
                need(truth_sum == derivation['feasible_min'] == derivation['feasible_max'],
                     f'the key\'s true value {derivation["feasible_min"]}..{derivation["feasible_max"]} is not the sum {truth_sum} '
                     f'read from the text of {text!r}')
                if row['semantic_state'] == 'refuted':
                    errors[check_false_sum(st, row, runs, projects)] += 1
                else:
                    need(row['error_type'] is None, f'a true sum with an error type: {text!r}')
    return (f'{2 * STATEMENTS_PER_REPORT} statements ({sum(kinds.values())} sums: {dict(sorted(kinds.items()))}), readers '
            f'{", ".join(readers)}; false sums by reader {dict(sorted(errors.items()))}')


def check_false_sum(st, row, runs, projects):
    """A false sum states the sum its named reader gets from its own figures, and not the true sum (supplement 5, checks 1 and 3);
    where the error is the plain wrong value, no reader gets a good value other than the truth. Returns the error."""
    truth = sum_of(runs['truth'], projects, st)
    need(truth is not None, f'no true sum for {st["text"]!r}')
    need(good_figure(st['figure'], truth), f'the true sum {truth} of {st["text"]!r} is not a good value')
    stated, error = fractions.Fraction(st['value']), row['error_type']
    need(stated != truth, f'a false sum states the true value: {st["text"]!r}')
    need(error is not None, f'a false sum without an error type: {st["text"]!r}')
    if error == 'plain_wrong_value':
        for name in READERS + ('full_name_only',):
            theirs = sum_of(runs[name], projects, st)
            need(not (good_figure(st['figure'], theirs) and theirs != truth),
                 f'plain wrong value stated, but reader {name} gets the good value {theirs} (true sum {truth}) for {st["text"]!r}')
    else:
        need(error in runs and error != 'truth', f'unknown reader {error!r}')
        theirs = sum_of(runs[error], projects, st)
        need(theirs == stated, f'false sum {st["text"]!r} states {st["value"]}, but reader {error} gets {theirs} (true sum {truth})')
    return error


@guarded
def check_round_trip3(seed, index=1):
    built = case(seed, index)
    book, runs = read(seed, index)
    return round_trip3(built, book, runs, readers_in(seed, index))


def statements_in(built, book):
    """{variant: [statement dict]} of both reports of a built dossier, read by `book`."""
    return {v: [statement_of(book, t) for t in report_statements(built, v)] for v in ('correct', 'mixed')}


def statements_of(seed, index=1):
    """{variant: [statement dict]} of both reports of the dossier."""
    return statements_in(case(seed, index), read(seed, index)[0])


def quota_rules(edition, count, both, rows):
    """The quota table of the edition per report, by rung as the text shows it and by claim kind as the key says it (`both`: the
    statements of each report, `rows`: its key rows)."""
    need({e: dict(t) for e, t in INV['sum_quotas'].items()} == EXPECTED_QUOTAS, f'the inventory\'s tables differ: {INV["sum_quotas"]}')
    for variant, sts in both.items():
        rungs = collections.Counter(rung_of(st, count) for st in sts)
        need({r: n for r, n in rungs.items()} == {r: n for r, n in EXPECTED_QUOTAS[edition].items() if n},
             f'{variant}: the text shows {dict(sorted(rungs.items()))}, edition {edition} has {EXPECTED_QUOTAS[edition]}')
        need([r['claim_kind'] for r in rows[variant]] == [claim_kind_of(st, count) for st in sts],
             f'{variant}: the claim kinds are not those the text shows')
        singles = [st['kind'] for st in sts if rung_of(st, count) == 'single']
        need(len(set(singles)) == len(singles) and set(singles) <= set(SINGLES), f'{variant}: one-project statements {singles}')


@guarded
def check_quotas(seed, index=1):
    """The quota table of the edition, per report and by claim kind, from the supplement's table written out above (and equal to the
    inventory's); the rungs the text shows are the claim kinds of the rows; 8 to 12 altered statements."""
    edition, built = big_minutes.current_edition(), case(seed, index)
    book, _ = read(seed, index)
    need(all(sum(t.values()) == STATEMENTS_PER_REPORT for t in EXPECTED_QUOTAS.values()), 'the expected tables are not of 24')
    both = statements_of(seed, index)
    quota_rules(big_minutes.shape_of(edition), len(book.meetings), both, {v: key_rows(built, v) for v in both})
    altered = sum(a != b for a, b in zip(report_statements(built, 'correct'), report_statements(built, 'mixed')))
    need(ALTERED[0] <= altered <= ALTERED[1], f'{altered} altered statements')
    kinds = collections.Counter(claim_kind_of(st, len(book.meetings)) for st in both['correct'])
    return f'edition {edition}: {dict(sorted(kinds.items()))} per report; {altered} altered statements of 24'


def rests_on_correction_or_variation(book, name, until):
    """Does the award in force of `name` after meeting `until` rest on a correction of its award or a variation of it, minuted at or
    before that meeting? (Pinned after the build, design section 3: a correction minuted later is read into the figure all the same
    but does not count towards the three.) Read from the facts this module's parser found in the text."""
    return any(f['t'] in ('correct', 'vary') and f['p'] == name and f['m'] <= until and (f['t'] == 'vary' or f['target'] == 'award')
               for f in book.facts)


def choice_rules(edition, count, runs, projects, both, book=None):
    """The rules of choice (supplement 3 and 4; for edition 5 the fifth-edition design, section 3) for both reports, from
    statements read out of the text (`both`) and this module's evaluator's readings (`runs`). `book` (the facts the parser read)
    is needed for the award-side rule on corrections and variations. Returns the number of uses of each named project."""
    truth = runs['truth']
    for correct, mixed in zip(both['correct'], both['mixed']):
        need({k: v for k, v in correct.items() if k not in ('value', 'value_span', 'text')} ==
             {k: v for k, v in mixed.items() if k not in ('value', 'value_span', 'text')}, 'an altered statement changes more than its value')
    for variant, sts in both.items():
        need(len({identity(st) for st in sts}) == len(sts), f'{variant}: two statements are the same statement')
        spans = collections.defaultdict(list)
        by_rung = collections.defaultdict(list)
        for st in sts:
            rung = rung_of(st, count)
            by_rung[rung].append(st)
            if st['shape'] == 'named' and st['figure'] in AWARD_FIGURES:
                # an award-side named sum: every project holds an award in force at the meeting; at least three (where the world
                # allows, else as many as it has) rest on a correction or a variation; asked in the last third of the book
                need(book is not None, 'the award-side rule on corrections and variations needs the facts read from the text')
                for name in st['names']:
                    need(truth[('award', name, st['until'])] not in (None, 'none'), f'{variant}: {name} holds no award at its sum')
                pool = [n for n in projects if truth[('award', n, st['until'])] not in (None, 'none')
                        and rests_on_correction_or_variation(book, n, st['until'])]
                have = sum(rests_on_correction_or_variation(book, n, st['until']) for n in st['names'])
                need(have >= min(3, len(pool)),
                     f'{variant}: only {have} of the projects of {st["text"]!r} rest on a correction or a variation ({len(pool)} could)')
                need(3 * st['until'] >= 2 * count, f'{variant}: a named sum at meeting {st["until"]} of {count}, not in the last third')
            elif st['shape'] == 'named':
                for name in st['names']:
                    need(truth[('count', name, st['until'])] >= 2, f'{variant}: {name} has fewer than two payments at its sum')
                need(3 * st['until'] >= 2 * count, f'{variant}: a named sum at meeting {st["until"]} of {count}, not in the last third')
            if st['shape'] != 'single':
                value = sum_of(truth, projects, st)
                need(good_figure(st['figure'], value), f'{variant}: {st["text"]!r} has the true value {value}')
                need(good_figure(st['figure'], fractions.Fraction(st['value'])), f'{variant}: {st["text"]!r} states a value that is not positive')
                if st['figure'] == 'balance':       # a true statement in which a part's balance is negative is refused
                    names = st['names'] if st['shape'] == 'named' else projects
                    need(all(part_of(truth, n, 'balance', st['until']) >= 0 for n in names), f'{variant}: a negative balance in {st["text"]!r}')
            if st['shape'] == 'all' and rung in ('upto', 'aupto'):
                need(3 * count // 5 <= st['until'] <= count - 2, f'{variant}: an {rung} sum at meeting {st["until"]} of {count}')
            if st['shape'] == 'span':
                length = st['until'] - st['since']
                gap = sum_of(truth, projects, dict(st, figure='count'))
                need(gap >= length, f'{variant}: a span of {length} meetings holds {gap} more payments at its later close')
                spans[length].append((st['since'], st['until']))
        for length, items in spans.items():
            for i, (s1, u1) in enumerate(items):
                for s2, u2 in items[:i]:
                    need(max(s1, s2) >= min(u1, u2), f'{variant}: two spans of {length} meetings overlap: {(s1, u1)} {(s2, u2)}')
        for rung, items in by_rung.items():
            figures = collections.Counter(st['figure'] for st in items if st['shape'] != 'single')
            if figures:
                pair = ('award', 'balance') if rung in AWARD_RUNGS else ('total', 'count')
                need(set(figures) <= set(pair) and abs(figures[pair[0]] - figures[pair[1]]) <= len(items) % 2,
                     f'{variant}: {rung} has {dict(figures)}')
                if edition >= 5:
                    table = EDITION5_FIGURES if edition == 5 else EDITION6_FIGURES
                    need(dict(figures) == table[rung], f'{variant}: {rung} has {dict(figures)}, the table says {table[rung]}')
        if edition == 4:
            need(len({st['until'] for st in by_rung['upto']}) >= 3, f'{variant}: the upto sums use fewer than three meetings')
        if edition == 6:
            need(len({st['until'] for st in by_rung['upto']}) == EXPECTED_QUOTAS[6]['upto'],
                 f'{variant}: the eight upto sums do not use eight different meetings')
        if edition >= 5:
            need(len({st['until'] for st in by_rung['aupto']}) == EXPECTED_QUOTAS[edition]['aupto'],
                 f'{variant}: the aupto sums do not use as many different meetings as there are of them')
            need(not {st['until'] for st in by_rung['aupto']} & {st['until'] for st in by_rung['upto']},
                 f'{variant}: an aupto sum is at a meeting of an upto sum')
        need(all(len(st['names']) == len(set(st['names'])) for st in
                 by_rung['sum3'] + by_rung['sum5'] + by_rung['sum8'] + by_rung['asum8']), 'repeated names')
        need(all(st['until'] == count - 1 for st in by_rung['book'] + by_rung['abook']), f'{variant}: a book sum not at the last meeting')
        need(sorted(3 * st['until'] // count for st in by_rung['projects']) == ([0, 2] if EXPECTED_QUOTAS[edition]['projects'] else []),
             f'{variant}: the projects statements are not one in the first and one in the last third (edition 6: none)')
        need(all(runs[name][('award', st['who'], st['until'])] == truth[('award', st['who'], st['until'])]
                 for st in by_rung['award'] for name in READERS), f'{variant}: a flawed reader differs on an award control')
    return collections.Counter(n for st in both['correct'] if st['shape'] == 'named' for n in st['names'])


@guarded
def check_choice_rules(seed, index=1):
    """The rules of choice, recomputed from this module's reading of the text (see `choice_rules`)."""
    edition, built = big_minutes.current_edition(), case(seed, index)
    book, runs = read(seed, index)
    both = statements_of(seed, index)
    uses = choice_rules(big_minutes.shape_of(edition), len(book.meetings), runs, book.projects, both, book)       # edition 8 is edition 6's
    # the readers' sums nearest the truth give the false value of a sum (the order of the picker's rule), by this evaluator
    for st, row in zip(both['mixed'], key_rows(built, 'mixed')):
        if st['shape'] != 'single' and row['error_type'] not in (None, 'plain_wrong_value'):
            truth_sum = sum_of(runs['truth'], book.projects, st)
            valid = {name: sum_of(runs[name], book.projects, st) for name in READERS + ('full_name_only',)}
            valid = {n: v for n, v in valid.items() if good_figure(st['figure'], v) and v != truth_sum}
            options = {}
            for name in sorted(valid, key=lambda n: (abs(valid[n] - truth_sum), n)):
                options.setdefault(valid[name], name)
            need(fractions.Fraction(st['value']) in list(options)[:3],
                 f'{st["text"]!r}: the stated value is not one of the three readers\' sums nearest the truth {truth_sum}: {options}')
    return (f'edition {edition}: every rule holds in both reports; named sums over {len(uses)} projects, each used {min(uses.values(), default=0)} '
            f'to {max(uses.values(), default=0)} times')


INSTALMENT_SPECS = ('first', 'ordinal', 'first_of')     # an amount that is another payment's: its own first, its own numbered, another project's first


def text_rules3(sources, book, edition=3):
    """Text rules of editions 3 and 4 (supplement 6; edition 5 keeps them, with its own sums paragraph: `check_text_rules5` reads that
    one), read from the sources of a dossier and the facts `book` found in them."""
    books = {n: t for n, t in sources.items() if n != 'procedure.md'}
    # a project is named by its place in the order of backing only up to the twelfth place
    need(INV['ordinal_cap'] == 12, f'the inventory\'s cap is {INV["ordinal_cap"]}')
    uses = collections.Counter()
    for form, number in book.ordinal_forms.items():
        uses[number] = sum(len(re.findall(re.escape(form), t, re.I)) for t in books.values())
    need(all(n == 0 for number, n in uses.items() if number >= 12), f'a project is named by a place beyond the twelfth: {dict(uses)}')
    need(any(n for number, n in uses.items() if number < 12), 'no project is named by its place, up to the twelfth: the check is empty')
    # where a payment's amount is stated through an instalment (the first of another project, or the project's own first or
    # numbered one: the kind is what this module's parser reads from the sentence), the recipient is named before the amount
    first_of = [f for f in book.facts if f['t'] == 'pay' and f['spec']['type'] in INSTALMENT_SPECS]
    for f in first_of:
        need(f['recipient_first'], f'an amount stated through an instalment ({f["spec"]["type"]}) comes before its recipient: '
                                   f'{book.blocks[f["key"]][f["order"][1]:][:160]!r}')
    by_type = collections.Counter(f['spec']['type'] for f in first_of)
    # procedure.md: the sums paragraph, and the wordings of a rescission that the minute books use
    paragraphs = sources['procedure.md'].rstrip('\n').split('\n\n')
    sums = [p for p in paragraphs if all(v in p for v in ('"stood higher"', '"rose by"', '"grew by"'))]
    need(len(sums) == 1, f'{len(sums)} paragraphs of procedure.md name the verbs of a span')
    for phrase in ('together', '"all projects"', 'adds nothing', 'less the same figure', 'after the later close', 'both closes'):
        need(phrase in sums[0], f'the sums paragraph lacks {phrase!r}')
    need(edition == 5 or sums[0] == INV['procedure_sums'], 'the sums paragraph is not the inventory\'s')
    rescissions = [p for p in paragraphs if p.startswith('Rescissions.')]
    need(len(rescissions) == 1 and rescissions[0] == INV['procedure_rescissions'], 'the rescission paragraph is missing or not the inventory\'s')
    need(all(w in rescissions[0] for w in INV['rescission_wordings']), 'the rescission paragraph lacks a wording')
    used = collections.Counter()
    for f in (f for f in book.facts if f['t'] == 'rescind'):
        block = book.blocks[f['key']]
        sentence = block[f['order'][1]:].split('. ')[0]
        listed = [w for w in INV['rescission_wordings'] if w in sentence]
        need(listed or re.search(r'rescind|withdraw|cancel', sentence), f'a rescission worded outside the procedure: {sentence!r}')
        for w in listed:
            need(w in rescissions[0], f'the wording {w!r} of a rescission is not named in procedure.md')
            used[w] += 1
    # every frame of a span names both closes and says the difference in its own words
    for table, frames in INV['sum_statements'].items():
        if table.startswith('span_'):
            for frame in frames:
                need('close of {earlier}' in frame and 'close of {later}' in frame and
                     any(v in frame for v in ('{value} higher', 'rose by', 'grew by')), f'a span frame without both closes or a verb: {frame!r}')
    found = ', '.join(f'{n} {t}' for t, n in sorted(by_type.items())) if first_of else 'none in this dossier'
    return (f'{len(first_of)} amounts through an instalment name their recipient first ({found}); projects named by place only up to '
            f'the twelfth ({sum(uses.values())} uses); procedure.md holds the sums paragraph and the wordings used ({dict(sorted(used.items()))})')


@guarded
def check_text_rules3(seed, index=1):
    return text_rules3(sources_of(case(seed, index)), read(seed, index)[0])


# ---------- Edition 5: sums on the award side ----------
#
# Written from the fifth-edition design (sections 2 to 5) and the rendered text. Wordings are written out here, from the design, and
# compared with the inventory's frames (data), never read from it.

BALANCE_WORDINGS = ('unpaid balance', 'remained unpaid', 'still to be paid')        # design, section 4: what a balance frame of a sum says
ONE_PROJECT_BALANCE_WORDING = 'short of being fully paid'                          # the one-project balance frame the procedure names too
NEW_FRAME_TABLES = {'sum_award': ('names', 'when', 'value'), 'sum_balance': ('names', 'when', 'value'),
                    'all_award': ('when', 'value'), 'all_balance': ('when', 'value')}
SAMPLE_WHEN = 'the meeting of 12 May 2036'


def sample_names(number):
    """`number` distinct full names of projects, made from the inventory's pieces."""
    names = []
    for place in INV['places']:
        for building in INV['buildings'][:2]:
            names.append(f'{place} {building.title()} {INV["works"][0][0]}')
    return names[:number]


def check_frames5(seed=None, index=None):
    """The four new tables of statement frames (design, section 4), independent of any seed: three frames each with the placeholders
    the shape asks for; every frame names the close and says "in force" (awards) or one of the balance wordings (balances); no balance
    frame says "fully paid"; filled with a sample, every frame is read back by this module's parser as the shape, the figure, the
    names and the value it was made from, and by no other frame of the sums or of the statements about one project."""
    tables = INV['sum_statements']
    need(sorted(NEW_FRAME_TABLES) == sorted(t for t in tables if t.split('_')[1] in AWARD_FIGURES),
         f'the award-side tables are {sorted(t for t in tables if t.split("_")[1] in AWARD_FIGURES)}')
    names = sample_names(8)
    parsers = compile_sum_frames(tables)
    checked = 0
    for table, placeholders in NEW_FRAME_TABLES.items():
        frames = tables[table]
        need(len(frames) == 3 and len(set(frames)) == 3, f'{table}: {len(frames)} frames, three distinct ones are asked for')
        figure = table.split('_')[1]
        for frame in frames:
            need(sorted(re.findall(r'\{(\w+)\}', frame)) == sorted(placeholders), f'{table}: the placeholders of {frame!r} are not {placeholders}')
            need('{when}' in frame, f'{frame!r} does not name the close')
            if figure == 'award':
                need('in force' in frame, f'{frame!r} does not say "in force"')
            else:
                need('fully paid' not in frame, f'{frame!r} says "fully paid"')
                need(any(w in frame for w in BALANCE_WORDINGS), f'{frame!r} says none of the balance wordings {BALANCE_WORDINGS}')
            for value_text, value in (('£12,000', 12000), ('12,000 pounds', 12000), ('twelve thousand pounds', 12000)):
                listed = name_list(names) if '{names}' in frame else ''
                text = frame.format(names=listed, when=SAMPLE_WHEN, value=value_text)
                hits = [(shape, fig, mo) for shape, fig, patterns in parsers for rx in patterns for mo in [rx.match(text)] if mo]
                need(len(hits) == 1, f'{len(hits)} frames of the sums fit {text!r}')
                shape, fig, mo = hits[0]
                need(fig == figure and shape == ('named' if '{names}' in frame else 'all'), f'{text!r} is read as a {shape} sum of {fig}')
                need(parse_money(mo['value']) == value and (not listed or mo['names'] == listed), f'{text!r}: value or names misread')
                need(not any(rx.match(text) for patterns in STATEMENT_SRC.values() for rx in patterns),
                     f'{text!r} also fits a frame of a statement about one project')
                checked += 1
    return f'{checked} frames filled in and read back: three frames each of the four new tables, none says "fully paid" in a balance frame'


def rescission_verbs_and_wordings(paragraph):
    """What the rescission paragraph itself names: the stems of the verbs of its first sentence ("When the committee rescinds,
    withdraws or cancels an award, ...": rescind, withdraw, cancel) and the wordings it puts in quotation marks. Fails when the
    paragraph lacks that sentence or any wording: nothing is assumed beyond what the rendered text says."""
    first = re.match(r'Rescissions\. When the committee (\w+), (\w+) or (\w+) an award,', paragraph)
    need(first, 'the rescission paragraph lacks its first sentence "When the committee rescinds, withdraws or cancels an award,"')
    stems = [verb[:-1] if verb.endswith('s') else verb for verb in first.groups()]
    wordings = re.findall(r'"([^"]+)"', paragraph)
    need(wordings, 'the rescission paragraph names no wording in quotation marks')
    return stems, wordings


def rescission_frames_unnamed(frames, paragraph):
    """The frames of a rescission that hold neither a verb nor a wording that `paragraph` names."""
    stems, wordings = rescission_verbs_and_wordings(paragraph)
    return [f for f in frames if not any(w in f for w in wordings) and not any(stem in f.lower() for stem in stems)]


def check_rescission_frames(seed=None, index=None):
    """Item 7 of the design: whatever a seed draws, every frame of a rescission that the minute books can use holds a wording that the
    procedure names. What the procedure names is read from its rescission paragraph as rendered: the verbs of its first sentence
    (as stems) and the wordings it puts in quotation marks; a frame is accepted only by one of those. Checked on the procedure of
    each of the four editions (edition 6 is read from the dossier `facts_built` already holds: a second dense build is not made)."""
    frames = INV['frames']['rescind']
    need(frames, 'no frame of a rescission')
    for edition in (3, 4, 5, 6):
        with edition_env(edition):
            sources = sources_of(facts_built(ED_SEED) if edition == 6 else case(ED_SEED))
        paragraphs = [p for p in sources['procedure.md'].rstrip('\n').split('\n\n') if p.startswith('Rescissions.')]
        need(len(paragraphs) == 1, f'edition {edition}: {len(paragraphs)} rescission paragraphs')
        unnamed = rescission_frames_unnamed(frames, paragraphs[0])
        need(not unnamed, f'edition {edition}: the rescission frame {unnamed[0] if unnamed else ""!r} holds none of the verbs and wordings '
                          f'the procedure names {rescission_verbs_and_wordings(paragraphs[0])}')
    return (f'{len(frames)} rescission frames each hold a verb or a wording that the rescission paragraph itself names (read from the '
            f'rendered procedure), in the procedures of editions 3, 4, 5 and 6')


@per_edition
def flats_of(seed, index=1):
    """The interpreter's `state` of the whole log under the truth and each of the four flawed readers (the figures a reader reads
    by its own facts, `None` where it cannot establish them)."""
    made = made_of(seed, index)
    return {name: big_minutes.state(made['events'], None if name == 'truth' else name, made['meetings'])
            for name in ('truth',) + READERS + ('full_name_only',)}


@guarded
def check_award_side(seed, index=1):
    """The sums on the award side of the dossier (design, sections 2, 3 and 5), from the text: (1) no part of a statement is not
    established, for the truth (this module's own evaluator, from the text) or for any of the four flawed readers (by the generator's
    `state`, which alone says "not established": for the readers this is a self-consistency check of the picker's own refusal, which
    refuses on the same dicts, and not an independent one);
    (2) for all projects, at every close, the unpaid balance is the awards in force less the total paid; (3) for a sum of awards, every
    award held is among the checker's CLOSED needed events and no award made after the close is; (4) for a sum of balances the set of
    needed events is not empty and lies inside `relevant`, and no award made after the close is among the closed needed events;
    (5) no balance statement says "fully paid"."""
    built, made = case(seed, index), made_of(seed, index)
    book, runs = read(seed, index)
    both, by_text, events = statements_of(seed, index), claims_by_text(made), made['events']
    count, flats = len(book.meetings), flats_of(seed, index)
    sums = [st for sts in both.values() for st in sts if st['shape'] != 'single' and st['figure'] in AWARD_FIGURES]
    need(sums, 'no statement on the award side')
    parts = truth_parts = 0
    for st in sums:
        names, date = (st['names'] if st['shape'] == 'named' else book.projects), made['meetings'][st['until']]
        for n in names:
            award = runs['truth'][('award', n, st['until'])]      # this module's own evaluator: a number or the established 'none'
            need(award is not None and (award == 'none' or st['figure'] == 'award' or runs['truth'][('balance', n, st['until'])] is not None),
                 f'{st["text"]!r}: the {st["figure"]} of {n} is not established for the truth')
            truth_parts += 1
            for reader, flat in flats.items():
                if reader != 'truth':          # the generator's `state`: the same dicts the picker refuses on
                    need(flat[f'{st["figure"]}|{n}|{date}'] is not None, f'{st["text"]!r}: the {st["figure"]} of {n} is not established for {reader}')
                    parts += 1
        need(st['figure'] != 'balance' or 'fully paid' not in st['text'], f'a balance statement says "fully paid": {st["text"]!r}')
    # the identity, at every close
    truth, bad = runs['truth'], 0
    for m in range(count):
        awards = sum(part_of(truth, n, 'award', m) for n in book.projects)
        paid = sum(truth[('total', n, m)] for n in book.projects)
        balances = sum(part_of(truth, n, 'balance', m) for n in book.projects)
        need(balances == awards - paid, f'at meeting {m}: the unpaid balance of all projects {balances} is not the awards in force {awards} less the total paid {paid}')
        bad += any(part_of(truth, n, 'balance', m) < 0 for n in book.projects)
    # what the sums turn on
    seen, alone_differs = set(), 0
    for st in sums:
        q = by_text[st['text']]['query']
        if q['key'] in seen:
            continue
        seen.add(q['key'])
        closed, by_removal = closed_needed_of(seed, index, q['key']), needed_of(seed, index, q['key'])
        names = st['names'] if st['shape'] == 'named' else book.projects
        awards = collections.defaultdict(list)
        for e in events:
            if e['kind'] == 'award':
                awards[e['subject']].append(e)
        later = {e['id'] for ws in awards.values() for e in ws if e['m'] > st['until']}
        need(not later & closed, f'{q["key"]}: an award made after the close is needed: {sorted(later & closed)}')
        if st['figure'] == 'award':
            held = {e['id'] for n in names if truth[('award', n, st['until'])] not in (None, 'none') for e in awards[n]}
            need(held and held <= closed, f'{q["key"]}: awards held and not needed: {sorted(held - closed)}')
        else:
            need(by_removal and closed <= set(big_minutes.relevant(q, events)), f'{q["key"]}: the needed set is empty or not inside relevant')
            alone_differs += big_minutes.answer(q, [e for e in events if e['id'] in closed]) != big_minutes.answer(q, events)
    return (f'{len(sums)} statements on the award side ({truth_parts} parts established for the truth by this module evaluator; {parts} for the four readers by the generator state function, a self-consistency check of the refusal of the picker and not an independent one); the unpaid balance of '
            f'all projects is the awards less the total paid at all {count} closes ({bad} closes with a negative balance of a project); every award held is '
            f'needed by sums of awards; {alone_differs} sums of balances are not given by their needed events alone')


@guarded
def check_text_rules5(seed, index=1):
    return text_rules5(sources_of(case(seed, index)), read(seed, index)[0])


def text_rules5(sources, book):
    """Text rules of edition 5 (design, section 4), which edition 6 keeps, on the sources of a dossier and the facts `book` read: the
    rules of editions 3 and 4, and the procedure of edition 5 on
    the award side. The procedure is read as text: the sums paragraph says what the awards in force and the unpaid balances of several
    projects together are, that a project holding no award in force adds nothing, that each wording of a balance frame means the award
    in force at that close less the total paid by then, that a correction minuted at any meeting is read into the figure and that a
    variation or a rescission minuted after the close is not, and the procedure says whose instalment is meant."""
    done = text_rules3(sources, book, 5)
    paragraphs = sources['procedure.md'].rstrip('\n').split('\n\n')
    sums = [p for p in paragraphs if all(v in p for v in ('"stood higher"', '"rose by"', '"grew by"'))][0]
    for phrase in ('awards in force', 'unpaid balance', 'adds nothing', 'no award in force', 'less the total paid'):
        need(phrase in sums, f'the sums paragraph of edition 5 lacks {phrase!r}')
    need(sums == INV['procedure_sums5'], 'the sums paragraph of edition 5 is not the inventory\'s')
    for wording in BALANCE_WORDINGS + (ONE_PROJECT_BALANCE_WORDING,):
        need(wording in sums, f'the sums paragraph of edition 5 does not name the balance wording {wording!r}')
    # it does not state the identity of section 2 (a derived fact): no sentence sets the awards or the balances of all projects
    # against the total paid to all projects
    for sentence in re.split(r'(?<=\.) ', sums):
        need(not re.search(r'all projects[^.]*less the total paid|less the total paid[^.]*all projects', sentence),
             f'the procedure states the identity of the balances: {sentence!r}')
    need(re.search(r'correction[^.;]*any meeting[^.;]*read into the figure[^.;]*; a variation or a rescission minuted after[^.]*is not\.', sums),
         'the sums paragraph does not say what is read into a figure at the close and what is not')
    instalment = [p for p in paragraphs if p.startswith('Amounts defined by reference.')]
    need(len(instalment) == 1 and INV['procedure_instalment'] in instalment[0], 'the instalment sentence is not in the paragraph on amounts')
    sentence = re.search(r'In a payment to a project,[^.]*?instalment\.', instalment[0])
    need(sentence and all(w in sentence.group(0) for w in ('"the first instalment"', '"the opening cheque"', 'numbered', 'no other project named',
                                                          'that project\'s own')), 'the instalment sentence lacks a part')
    return done + '; the procedure names the balance wordings, the award side and whose instalment is meant'


def check_procedures_differ(seed=None, index=None):
    """The procedure of edition 5 names the balance wordings that are new ("remained unpaid", "still to be paid", the one-project "short
    of being fully paid"; "unpaid balance" is in the procedures of editions 3 and 4 already) and the instalment sentence (its marker is
    the clause "with no other project named as having been paid it"), and the procedures of editions 3 and 4 name none of them. The
    procedure of edition 6 is that of edition 5, byte for byte."""
    texts = {}
    for edition in (3, 4, 5, 6):
        with edition_env(edition):
            texts[edition] = sources_of(facts_built(ED_SEED) if edition == 6 else case(ED_SEED))['procedure.md']
    markers = BALANCE_WORDINGS[1:] + (ONE_PROJECT_BALANCE_WORDING, 'with no other project named as having been paid it')
    for edition in (3, 4):
        for marker in markers:
            need(marker not in texts[edition], f'the procedure of edition {edition} names {marker!r}')
    for marker in markers:
        need(marker in texts[5], f'the procedure of edition 5 does not name {marker!r}')
    need(texts[3] == texts[4], 'the procedures of editions 3 and 4 differ')
    need(texts[5] != texts[4] and len(texts[5]) > len(texts[4]), 'the procedure of edition 5 is not longer than that of edition 4')
    need(texts[6] == texts[5], 'the procedure of edition 6 is not that of edition 5')
    return 'the procedure of edition 5 names the award-side wordings, those of editions 3 and 4 do not'


@guarded
def check_filters3(seed, index=1):
    return check_filters(seed, index, any_word=False)


@guarded
def check_leads3(seed, index=1):
    return check_edition2_leads(seed, index)


@guarded
def check_sums_turn_on_projects(seed, index=1):
    """Editions 3 and 4 have no use for the rule that four statements turn on another subject; what holds for them: a named sum
    turns on a payment of each named project, a sum over every project up to a meeting on at least three projects, and the
    number of projects holding an award on every award held and every rescission up to the meeting."""
    made = made_of(seed, index)
    events, by_id = made['events'], {e['id']: e for e in made['events']}
    built, (book, _) = case(seed, index), read(seed, index)
    spread = collections.defaultdict(list)
    for key, q in statement_queries(seed, index).items():
        needed = needed_of(seed, index, key)
        closed = closed_needed_of(seed, index, key)
        if q['kind'] in ('sum3', 'sum5', 'sum8'):
            for name in q['subjects']:
                need(any(by_id[i]['subject'] == name and by_id[i]['kind'] == 'pay' for i in needed), f'{key}: nothing of {name} is needed')
        if q['kind'] in ('upto', 'book', 'span3', 'span6', 'aupto', 'abook'):
            subjects = {by_id[i]['subject'] for i in closed if by_id[i]['kind'] != 'names'}
            need(q['kind'].startswith('span') or len(subjects) >= 3, f'{key}: only {len(subjects)} projects are needed')
            spread[q['kind']].append(len(subjects))
        if q['kind'] == 'projects':
            every = {e['id'] for e in events if e['kind'] in ('award', 'rescind') and e['date'] <= q['until']}
            rescinded = {e['subject'] for e in events if e['kind'] == 'rescind' and e['date'] <= q['until']}
            held = {i for i in every if by_id[i]['kind'] == 'rescind' or by_id[i]['subject'] not in rescinded}
            need(held <= closed, f'{key}: not every award held and rescission is needed')
    return 'projects needed by the all-project sums (min/max): ' + ', '.join(f'{k} {min(v)}/{max(v)}' for k, v in sorted(spread.items()))


# ---------- The real pipeline and the basis, through the frozen checker ----------

def scratch_folder(prefix, parent=None):
    """A new empty folder under `parent` (the repository's ignored `tmp` folder by default), made with the parent where a fresh clone or
    worktree has none."""
    parent = Path(parent) if parent is not None else ROOT / 'tmp'
    parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(parent)))


@guarded
def check_pipeline(seed, index=1):
    """Build the dossier with the facts in its key, then read its keys through the real `check_shown.read_keys` (which runs
    `check_row` on every row) with the edition variable unset and set to 1, 2, 3, 4, 5, 6 and 8: no refusal, and `answer`, `value_form`
    and `relevant` give the same for every row and every reader under every setting."""
    import blindness
    import check_shown
    built = build_with_facts(seed, index)                  # under the edition that is set now
    key = built['key']
    folder = scratch_folder('big-minutes-keys-')
    try:
        (folder / 'draft').mkdir()
        (folder / 'arena').mkdir()
        (folder / 'draft' / f'{key["dossier_id"]}.json').write_text(json.dumps(key), encoding='utf-8')
        blindness.seal_keys(folder / 'draft', folder / 'sealed', [folder / 'arena'])
        seen, rows_read = {}, 0
        for setting in (None, 1, 2, 3, 4, 5, 6, 8):
            with edition_env(setting):
                dossier = check_shown.read_keys(folder / 'sealed')['dossiers'][key['dossier_id']]
                rows, found = dossier['rows'], {}
                rows_read = len(rows)
                for row in rows:
                    query = row['query']
                    found[row['claim_id'], row['variant']] = (
                        big_minutes.value_form(query), big_minutes.relevant(query, dossier['family'].events),
                        [big_minutes.answer(query, dossier['family'].events, name) for name in (None,) + big_minutes.READERS])
                seen[setting] = found
        need(rows_read == 2 * STATEMENTS_PER_REPORT, f'{rows_read} rows read')
        for setting, found in seen.items():
            need(found == seen[None], f'answer, value_form or relevant differ under {big_minutes.EDITION_VARIABLE}={setting}')
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return f'read_keys accepts {rows_read} rows with the variable unset and set to 1, 2, 3, 4, 5, 6, 8; answer, value_form and relevant are the same'


@per_edition
def facts_built(seed, index=1):
    """The dossier built now with the facts in its key (cached by edition)."""
    return build_with_facts(seed, index)


@per_edition
def checker_view(seed, index=1):
    """The checker's own view of the dossier built now: the key, `check_shown.Family` over its facts and `Sources` over the
    files (the stand-in for what a sheet is judged against)."""
    import check_shown
    built = facts_built(seed, index)
    return built['key'], check_shown.Family(big_minutes, built['key']['facts'], 'minutes'), check_shown.Sources(sources_of(built))


@guarded
def check_basis(seed, index=1):
    """For every statement of the key: the checker's needed set is not empty and is the one this module computes by removal; it
    is among `relevant`; no needed event is unquotable under the checker's `Sources.quotable`, so none is `not_checkable` on a stand-in
    sheet; every paragraph that states an event or serves as a lead paragraph is quotable under the same test."""
    import check_shown
    key, family, sources = checker_view(seed, index)
    made = made_of(seed, index)
    spots = collections.defaultdict(set)
    for p in made['passages']:
        if p['role'] in ('states', 'names'):
            for i in p['event_ids']:
                spots[i].add((p['file'], p['paragraph']))
    lost = sorted(spot for spot in set().union(*spots.values()) if not sources.quotable(spot))
    need(not lost, f'{len(lost)} paragraphs that state an event or lead one cannot be quoted: {lost[:3]}')
    need({e: sorted(s) for e, s in spots.items()} == {e: sorted(s) for e, s in family.spots_of.items()},
         'the checker\'s paragraphs for an event are not the passage map\'s')
    queries, done, sizes = statement_queries(seed, index), set(), []
    for row in key['expectations']:
        stripped = strip_value(row['query'])
        got = check_shown.judge_statement(family, dict(row, query=stripped), [], sources, 'answered')
        need(got['class'] != check_shown.NOT_CHECKABLE, f'{row["claim_id"]} {row["variant"]} is not checkable: {got["detail"]}')
        name = stripped['key']
        if name in done:
            continue
        done.add(name)
        analysis = family.analysis(stripped)
        by_removal, closed = needed_of(seed, index, name), closed_needed_of(seed, index, name)
        need(by_removal and closed, f'{name}: the needed set is empty')
        need(set(analysis['needed_by_removal']) == by_removal and set(analysis['needed']) == closed,
             f'{name}: the checker\'s needed set is not the one this module finds by removal')
        need(not analysis['outside'] and by_removal <= set(big_minutes.relevant(queries[name], made['events'])),
             f'{name}: needed events {sorted(closed - analysis["relevant"])} are not relevant')
        need(not family.unquotable(analysis['needed'], sources), f'{name}: a needed event cannot be quoted')
        need(all(any(sources.quotable(spot) for spot in spots[i]) for i in closed), f'{name}: a needed event has no quotable paragraph')
        sizes.append(len(closed))
    return f'{len(done)} statements: needed sets not empty (smallest {min(sizes)}), as found by removal, relevant, every event quotable'


@guarded
def check_slots(seed, index=1):
    """Both reports list the same 24 slots in the same order: the same claim ids (read from the reports' texts), and a row of the
    mixed report has the same query without its value, claim kind and structure as the row of the correct report."""
    built = facts_built(seed, index)
    ids = {v: re.findall(r'\*\*(C\d+)\.\*\*', built['files'][f'report-{v}.md']) for v in ('correct', 'mixed')}
    need(ids['correct'] == ids['mixed'] and len(set(ids['correct'])) == len(ids['correct']) == STATEMENTS_PER_REPORT,
         f'the reports do not list the same 24 claim ids in the same order: {ids}')
    rows = {v: key_rows(built, v) for v in ids}
    need(all([r['claim_id'] for r in rows[v]] == ids[v] for v in ids), 'the key\'s rows are not the reports\' claims in their order')
    for number, (correct, mixed) in enumerate(zip(rows['correct'], rows['mixed'])):
        need(strip_value(correct['query']) == strip_value(mixed['query']) and correct['claim_kind'] == mixed['claim_kind'],
             f'slot {ids["correct"][number]}: the two reports ask different questions')
    both = statements_of(seed, index)
    need([identity(st) for st in both['correct']] == [identity(st) for st in both['mixed']],
         'the reports\' texts ask about different things in some slot')
    return f'{len(ids["correct"])} slots, the same claim ids and the same question (query without its value) in both reports'


def world6(built, book, sources):
    """The world of edition 6 (a dense world), from the rendered sources and this module's reading of them: 44 to 48 meetings, 36 to 40
    projects with an award, four minute books (five from 47 meetings) and the preamble that names them (`volumes 1 to N`), 430,000 to
    510,000 characters, the key says `size` 6, no name or sponsor used twice (one introduction per project, each sponsor once), and the
    density: 450 to 700 payments in all (design: about 480 to 670), at most 20 to a project, and at least half of the projects that are
    paid with 14 or more. `built` is the dossier built with its facts. Edition 8 is edition 6's world (its key says `size` 8)."""
    size = sum(len(t) for t in sources.values())
    meetings, projects = len(book.meetings), len(book.projects)
    need(44 <= meetings <= 48, f'{meetings} meetings')
    need(36 <= projects <= 40, f'{projects} projects with an award')
    books = volumes_in(sources)
    need(len(books) == (5 if meetings >= 47 else 4) and sorted(sources) == sorted(['procedure.md'] + books), f'sources {sorted(sources)}')
    need(430000 <= size <= 510000, f'{size} characters')
    need(built['key']['facts']['size'] in (6, 8), 'the key does not say which edition it is')       # 8: edition 6's world with quiet returns
    for variant in ('correct', 'mixed'):
        need(f'volumes 1 to {len(books)}' in built['files'][f'report-{variant}.md'], f'the {variant} report names other volumes than there are')
    intros = [f for f in book.facts if f['t'] == 'intro']
    need(len(intros) == projects == len({f['p'] for f in intros}) == len({f['sponsor'] for f in intros}),
         f'{len(intros)} introductions for {projects} projects: a project or a sponsor is used twice, or one is not introduced')
    per_project = collections.Counter(f['p'] for f in book.facts if f['t'] == 'pay')
    total = sum(per_project.values())
    need(PAYMENT_BOUNDS[0] <= total <= PAYMENT_BOUNDS[1], f'{total} payments in all (the bounds are {PAYMENT_BOUNDS})')
    need(max(per_project.values()) <= 20, f'a project with {max(per_project.values())} payments')
    dense = sum(n >= 14 for n in per_project.values())
    need(2 * dense >= len(per_project), f'only {dense} of {len(per_project)} paid projects have 14 or more payments')
    return (f'{meetings} meetings, {projects} projects with an award, {len(books)} minute books, {size} characters, size {built["key"]["facts"]["size"]}; {total} payments '
            f'in all, {dense} of {len(per_project)} paid projects with 14 or more')


@guarded
def check_world3(seed, index=1):
    """The world of editions 3 and 4, from the rendered sources and this module's reading: 29 to 32 meetings, 18 to 24 projects with
    an award, three minute books, 300,000 to 450,000 characters of sources (counted as the edition-2 shape check counts; these
    are the bounds the generator enforces, as for edition 1: 350,000 to 420,000 is only the size the minute books are drawn
    towards, and about one valid dossier in fifteen lies outside it), and the key's `size` is the edition. Edition 6 (a dense world)
    has its own bounds: 44 to 48 meetings, 36 to 40 projects, four minute books (five from 47 meetings) and 430,000 to 510,000
    characters, and its density: 450 to 700 payments in all, no project with more than 20, and at least half of the projects that are
    paid with 14 or more (`world6`)."""
    edition, built = big_minutes.current_edition(), case(seed, index)
    book, _ = read(seed, index)
    sources = sources_of(built)
    size = sum(len(t) for t in sources.values())
    if big_minutes.shape_of(edition) == 6:
        need(facts_built(seed, index)['key']['facts']['size'] == edition, 'the key does not say which edition it is')
        return world6(facts_built(seed, index), book, sources)
    need(29 <= len(book.meetings) <= 32, f'{len(book.meetings)} meetings')
    need(18 <= len(book.projects) <= 24, f'{len(book.projects)} projects with an award')
    need(volumes_in(sources) == list(VOLUMES[:3]) and sorted(sources) == sorted(['procedure.md'] + list(VOLUMES[:3])),
         f'sources {sorted(sources)}')
    need(300000 <= size <= 450000, f'{size} characters')
    need(facts_built(seed, index)['key']['facts']['size'] == edition, 'the key does not say which edition it is')
    return f'{len(book.meetings)} meetings, {len(book.projects)} projects with an award, 3 minute books, {size} characters, size {edition}'


def readers_wrong(both, projects, runs, readers):
    """Every flawed reader of `readers` is wrong, by this module's evaluator, on at least four statements of each report."""
    counts = {}
    for variant, sts in both.items():
        for name in readers:
            wrong = sum(decide(runs[name], projects, st) != decide(runs['truth'], projects, st) for st in sts)
            need(wrong >= 4, f'{variant}: reader {name} is wrong on only {wrong} statements')
            counts.setdefault(name, []).append(wrong)
    return 'wrong on (correct, mixed): ' + ', '.join(f'{n} {c[0]}/{c[1]}' for n, c in sorted(counts.items()))


@guarded
def check_readers_wrong(seed, index=1):
    """Every flawed reader the dossier carries is wrong, by this module's evaluator, on at least four statements of each report."""
    return readers_wrong(statements_of(seed, index), read(seed, index)[0].projects, read(seed, index)[1], readers_in(seed, index))


@guarded
def check_depth(seed, index=1):
    """Informative, always ok: per claim kind, the minimum / median / maximum number of paragraphs of the checker's closed needed set."""
    key, family, sources = checker_view(seed, index)
    kinds, seen = collections.defaultdict(list), set()
    for row in key['expectations']:
        stripped = strip_value(row['query'])
        if stripped['key'] in seen:
            continue
        seen.add(stripped['key'])
        needed = family.analysis(stripped)['needed']
        kinds[row['claim_kind']].append(len({spot for e in needed for spot in family.spots_of.get(e, ())}))
    return 'paragraphs of the closed needed set (min/median/max): ' + ', '.join(
        f'{k[len("minutes_"):]} {min(v)}/{statistics.median(v):g}/{max(v)}' for k, v in sorted(kinds.items()))


# ---------- Hand-written logs for the sums ----------
#
# Written from the procedure's rules and the supplement (sections 4 and 5) before they were run: a sum is the figure of each project
# (the total paid, or the number of payments, that count at the close of the meeting), added; "all projects" are those with an award
# in the minutes (on a subset: every project that has any event in it, whether or not the subset holds its award); a span is the
# figure for all projects at the later close less that at the earlier one; a payment returned stops counting from the meeting that
# reports the return; a correction is read back from the start, whenever it is minuted. A is project 0, B project 1, C project 2;
# meeting m has the date 2036-(m+1)-10. A query is (figure, projects or None for all, earlier meeting or None, later meeting, value).

HAND_SUM_CASES = [
 dict(name='named, all and a span', events=[
        ('a1', 'award', 0, 0, dict(amount=20000)), ('b1', 'award', 0, 1, dict(amount=15000)), ('g1', 'award', 1, 2, dict(amount=9000)),
        ('pa1', 'pay', 1, 0, dict(spec={'type': 'abs', 'amount': 4000})), ('pb1', 'pay', 2, 1, dict(spec={'type': 'abs', 'amount': 5000})),
        ('pa2', 'pay', 3, 0, dict(spec={'type': 'abs', 'amount': 3000})), ('pg1', 'pay', 3, 2, dict(spec={'type': 'abs', 'amount': 1000})),
        ('pb2', 'pay', 4, 1, dict(spec={'type': 'abs', 'amount': 2500}))],
      queries=[('total', (0, 1), None, 3, 12000),          # A 7000 and B 5000
               ('count', (0, 1), None, 3, 3),
               ('total', (0, 1, 2), None, 2, 9000),        # C holds an award but has had no payment by then: it adds nothing
               ('count', (0, 1, 2), None, 2, 2),
               ('total', None, None, 4, 15500),            # 7000 + 7500 + 1000
               ('count', None, None, 4, 5),
               ('total', None, None, 0, 0),                # awards, no payment yet
               ('count', None, None, 0, 0),
               ('total', None, 1, 4, 11500),               # 15500 less 4000
               ('count', None, 1, 4, 4),
               ('total', None, 2, 3, 4000),                # 13000 less 9000: the payments of the meeting of A and C
               ('count', None, 2, 3, 2)]),
 dict(name='returns inside and after a span', events=[
        ('a1', 'award', 0, 0, dict(amount=30000)), ('b1', 'award', 0, 1, dict(amount=20000)),
        ('p1', 'pay', 1, 0, dict(spec={'type': 'abs', 'amount': 4000})), ('q1', 'pay', 1, 1, dict(spec={'type': 'abs', 'amount': 2000})),
        ('p2', 'pay', 2, 0, dict(spec={'type': 'abs', 'amount': 3000})), ('q2', 'pay', 3, 1, dict(spec={'type': 'abs', 'amount': 6000})),
        ('p3', 'pay', 4, 0, dict(spec={'type': 'abs', 'amount': 5000})),
        ('r1', 'return', 5, 0, dict(target='p2', target_m=2)), ('r2', 'return', 5, 1, dict(target='q2', target_m=3))],
      queries=[('total', None, None, 2, 9000), ('count', None, None, 2, 3),
               ('total', None, None, 4, 20000), ('count', None, None, 4, 5),      # the returns are minuted after this close
               ('total', None, None, 5, 11000), ('count', None, None, 5, 3),      # p2 and q2 stop counting from the meeting of the return
               ('total', None, 2, 5, 2000),     # +5000 (p3) -3000 (p2 counted at the earlier close, returned inside); q2 is made and returned inside: at neither
               ('count', None, 2, 5, 0),        # +1 (p3) -1 (p2)
               ('total', None, 3, 5, -4000),    # the returns outweigh p3: the difference goes down below zero
               ('count', None, 3, 5, -1),
               ('total', None, 1, 4, 14000),    # the returns of the meeting after the later close change neither figure
               ('count', None, 1, 4, 3),
               ('total', (0, 1), None, 5, 11000), ('total', (0,), None, 5, 9000), ('count', (0,), None, 5, 2)]),
 dict(name='corrections minuted after the later close', events=[
        ('a1', 'award', 0, 0, dict(amount=20000)), ('b1', 'award', 0, 1, dict(amount=10000)),
        ('p1', 'pay', 1, 0, dict(spec={'type': 'abs', 'amount': 4000})), ('q1', 'pay', 2, 1, dict(spec={'type': 'abs', 'amount': 3000})),
        ('p2', 'pay', 3, 0, dict(spec={'type': 'abs', 'amount': 2000})),
        ('c1', 'correct', 6, 0, dict(target='p1', target_kind='pay', target_m=1, new=5000)),
        ('c2', 'correct', 7, 0, dict(target='p2', target_kind='pay', target_m=3, new=2500))],
      queries=[('total', None, None, 1, 5000), ('count', None, None, 1, 1),          # c1 is read into the close of meeting 1, minuted at 6
               ('total', None, None, 3, 10500), ('count', None, None, 3, 3),         # 5000 + 2500 + 3000
               ('total', None, None, 5, 10500),                                      # before either correction was minuted
               ('total', None, 1, 3, 5500),     # both corrections read in: 10500 less 5000 (c1 moves both closes alike, c2 only the later one)
               ('count', None, 1, 3, 2),
               ('total', (0,), None, 3, 7500), ('count', (0,), None, 3, 2)]),
 dict(name='a relative payment', events=[
        ('a1', 'award', 0, 0, dict(amount=20000)), ('b1', 'award', 0, 1, dict(amount=10000)),
        ('pa1', 'pay', 1, 0, dict(spec={'type': 'abs', 'amount': 4000})), ('pb1', 'pay', 1, 1, dict(spec={'type': 'abs', 'amount': 2000})),
        ('pa2', 'pay', 2, 0, dict(spec={'type': 'frac_award', 'award_m': 0, 'frac': [1, 4]}))],
      queries=[('total', None, None, 2, 11000), ('count', None, None, 2, 3),         # 4000 + 5000 + 2000
               ('total', None, 1, 2, 5000), ('count', None, 1, 2, 1)]),
]

# Subsets: (label, case, events kept or None, events dropped, [queries]); what the rule for a subset says. A project that has no
# event in the subset adds nothing; "all projects" are those with an event in the subset, its award or not; a payment whose amount
# cannot be read (it is a share of an award that is absent) is a payment and adds no sum.
HAND_SUM_SUBSETS = [
 ('all projects, a subset that lacks the award of C (its payment still counts)', 'named, all and a span', None, ['g1'],
  [('total', None, None, 4, 15500), ('count', None, None, 4, 5), ('total', None, None, 2, 9000), ('total', None, 2, 3, 4000)]),
 ('a named project and a project with no event in the subset', 'named, all and a span', ['a1', 'pa1', 'pa2'], [],
  [('total', (0, 1, 2), None, 4, 7000), ('count', (0, 1, 2), None, 4, 2), ('total', None, None, 4, 7000), ('count', None, None, 4, 2),
   ('total', (1,), None, 4, 0), ('count', (1,), None, 4, 0)]),
 ('payments with no award at all, a span', 'named, all and a span', ['pa1', 'pa2', 'pb1'], [],
  [('total', None, None, 3, 12000), ('count', None, None, 3, 3), ('total', None, 1, 3, 8000), ('count', None, 1, 3, 2)]),
 ('the returns left out', 'returns inside and after a span', None, ['r1', 'r2'],
  [('total', None, None, 5, 20000), ('count', None, None, 5, 5), ('total', None, 2, 5, 11000), ('count', None, 2, 5, 2)]),
 ('the return of an absent payment is left out', 'returns inside and after a span', None, ['p2'],
  [('total', None, None, 5, 11000), ('count', None, None, 5, 3), ('total', None, 2, 5, 5000), ('count', None, 2, 5, 1)]),
 ('only the later side of a span', 'returns inside and after a span', ['p3', 'q2', 'r1', 'r2'], [],
  [('total', None, None, 4, 11000), ('count', None, None, 4, 2), ('total', None, None, 5, 5000), ('count', None, None, 5, 1),
   ('total', None, 2, 4, 11000), ('count', None, 2, 4, 2)]),
 ('no corrections', 'corrections minuted after the later close', None, ['c1', 'c2'],
  [('total', None, None, 3, 9000), ('total', None, 1, 3, 5000)]),
 ('the correction of the earlier close only: both closes move alike', 'corrections minuted after the later close', None, ['c2'],
  [('total', None, None, 3, 10000), ('total', None, 1, 3, 5000)]),
 ('the correction inside the span only', 'corrections minuted after the later close', None, ['c1'],
  [('total', None, None, 1, 4000), ('total', None, None, 3, 9500), ('total', None, 1, 3, 5500)]),
 ('a share of an absent award', 'a relative payment', None, ['a1'],
  [('total', None, None, 2, 6000), ('count', None, None, 2, 3), ('total', None, 1, 2, 0), ('count', None, 1, 2, 1)]),
 ('the empty list', 'a relative payment', [], [],
  [('total', (0, 1), None, 2, 0), ('total', None, None, 2, 0), ('count', None, 1, 2, 0)]),
]


def hand_sum_query(figure, projects, since, until):
    rung = 'sum3' if projects is not None else 'upto' if since is None else 'span6'
    names = sorted(HAND_PROJECTS[p] for p in projects) if projects is not None else None
    return {'kind': rung, 'subject': None, 'figure': figure, 'subjects': names, 'since': None if since is None else hand_date(since),
            'until': hand_date(until), 'key': f'hand|{figure}|{projects}|{since}|{until}'}


def check_hand_sums(seed=None, index=None):
    """Every hand-written log gives the value written down for each sum, on the whole log and on its subsets; the events
    removal changes the value of a statement are among `relevant`; a query is plain JSON with an integer form."""
    by_name = {c['name']: c for c in HAND_SUM_CASES}
    problems, checked = [], 0
    for c in HAND_SUM_CASES:
        events = hand_events(c['events'])
        for figure, projects, since, until, value in c['queries']:
            q = hand_sum_query(figure, projects, since, until)
            got = big_minutes.answer(q, events)
            checked += 1
            if got != value:
                problems.append(f'{c["name"]}: {figure} of {projects} from {since} to {until}: by hand {value!r}, by the interpreter {got!r}')
            cut = {e['id'] for e in events if big_minutes.answer(q, [x for x in events if x['id'] != e['id']]) != got}
            if not cut <= set(big_minutes.relevant(q, events)):
                problems.append(f'{c["name"]}: {figure} of {projects}: events {sorted(cut)} change the value and are not relevant')
            if json.loads(json.dumps(q)) != q or big_minutes.value_form(q) != 'integer':
                problems.append(f'{c["name"]}: the query of {figure} is not plain JSON of an integer form')
    for label, base, keep, drop, queries in HAND_SUM_SUBSETS:
        events = [e for e in hand_events(by_name[base]['events']) if (keep is None or e['id'] in keep) and e['id'] not in drop]
        for figure, projects, since, until, value in queries:
            got = big_minutes.answer(hand_sum_query(figure, projects, since, until), events)
            checked += 1
            if got != value:
                problems.append(f'subset {label}: {figure} of {projects} from {since} to {until}: by hand {value!r}, by the interpreter {got!r}')
    need(not problems, f'{len(problems)} of {checked} hand-written sums disagree: {problems[:3]}')
    return (f'{len(HAND_SUM_CASES)} hand-written logs with {sum(len(c["queries"]) for c in HAND_SUM_CASES)} sums, '
            f'{len(HAND_SUM_SUBSETS)} subsets with {sum(len(s[4]) for s in HAND_SUM_SUBSETS)} values')


# ---------- Hand-written logs for the sums on the award side (edition 5) ----------
#
# Written from the procedure's rules and the fifth-edition design (sections 2 and 5) before the interpreter was asked: the award
# in force of a project after a meeting is its award with every correction applied from the start, then every variation minuted up
# to that meeting (a fraction taken of the award in force just before it), until a rescission; variations that fell or were
# deferred, recommendations deferred and authorisations decide nothing. A sum of awards adds the award in force of every
# project that holds one at the close; a sum of unpaid balances adds the award in force less the total paid, for those projects only.
# A project that holds no award at the close (none yet, rescinded, or one the reading of a subset cannot establish) adds nothing.
# A is project 0, B project 1, C project 2, D project 3, E project 4; meeting m has the date 2036-(m+1)-10 (m up to 11); events of
# one meeting are applied in the order of their ids (the treasurer's part last). A query is (figure, projects or None for all
# projects, meeting, value, the events whose removal from the list changes the value).

HAND_AWARD_CASES = [
 dict(name='awards: a correction read back, a fraction after it, a rescission, no award yet, motions that decide nothing', events=[
        ('a1', 'award', 1, 0, dict(amount=20000)), ('c1', 'correct', 6, 0, dict(target='a1', target_kind='award', target_m=1, new=24000)),
        ('v1', 'vary', 3, 0, dict(sign=1, delta={'frac': [1, 2]})),
        ('f1', 'fell_vary', 2, 0, dict(sign=1, delta={'money': 5000})), ('d1', 'defer_vary', 3, 0, dict(sign=-1, delta={'frac': [1, 2]})),
        ('b1', 'award', 2, 1, dict(amount=10000)), ('x1', 'rescind', 4, 1, {}),
        ('g1', 'award', 5, 2, dict(amount=8000)), ('h1', 'defer_award', 2, 3, dict(amount=6000)),
        ('pa1', 'pay', 2, 0, dict(spec={'type': 'abs', 'amount': 5000})), ('pa2', 'pay', 4, 0, dict(spec={'type': 'abs', 'amount': 7000})),
        ('pg1', 'pay', 6, 2, dict(spec={'type': 'abs', 'amount': 3000}))],
      queries=[('award', None, 1, 24000, {'a1', 'c1'}),                    # the correction (minuted at 6) is read back
               ('award', None, 2, 34000, {'a1', 'c1', 'b1'}),              # the fallen variation (m2) changes nothing
               ('award', None, 3, 46000, {'a1', 'c1', 'v1', 'b1'}),        # 24000 + half of it; the deferred variation changes nothing
               ('award', None, 4, 36000, {'a1', 'c1', 'v1', 'x1'}),        # B is rescinded at 4 and adds nothing: its award is not needed, its rescission is
               ('award', None, 5, 44000, {'a1', 'c1', 'v1', 'x1', 'g1'}),  # C holds an award from 5
               ('award', None, 6, 44000, {'a1', 'c1', 'v1', 'x1', 'g1'}),
               ('award', (0, 2), 5, 44000, {'a1', 'c1', 'v1', 'g1'}),
               ('award', (0, 1, 2), 3, 46000, {'a1', 'c1', 'v1', 'b1'}),   # C has no award yet at 3: it adds nothing
               ('award', (1, 2), 4, 0, {'x1'}),                            # B rescinded and C not yet awarded: nothing at all
               ('balance', None, 2, 29000, {'a1', 'c1', 'pa1', 'b1'}),
               ('balance', None, 3, 41000, {'a1', 'c1', 'v1', 'pa1', 'b1'}),
               ('balance', None, 4, 24000, {'a1', 'c1', 'v1', 'pa1', 'pa2', 'x1'}),
               ('balance', None, 6, 29000, {'a1', 'c1', 'v1', 'pa1', 'pa2', 'x1', 'g1', 'pg1'}),
               ('balance', (0, 1, 2), 3, 41000, {'a1', 'c1', 'v1', 'pa1', 'b1'}),
               ('balance', (0, 2), 6, 29000, {'a1', 'c1', 'v1', 'pa1', 'pa2', 'g1', 'pg1'})]),
 dict(name='awards: what is minuted after the close of the statement', events=[
        ('a1', 'award', 1, 0, dict(amount=20000)), ('b1', 'award', 1, 1, dict(amount=10000)), ('c1', 'award', 2, 2, dict(amount=8000)),
        ('pa', 'pay', 2, 0, dict(spec={'type': 'abs', 'amount': 4000})), ('pc', 'pay', 3, 2, dict(spec={'type': 'abs', 'amount': 2000})),
        ('d1', 'award', 6, 3, dict(amount=9000)),
        ('va', 'vary', 6, 0, dict(sign=1, delta={'money': 5000})), ('xb', 'rescind', 6, 1, {}),
        ('ca', 'correct', 7, 0, dict(target='a1', target_kind='award', target_m=1, new=25000)),
        ('vc', 'vary', 8, 2, dict(sign=-1, delta={'money': 2000}))],
      queries=[('award', None, 4, 43000, {'a1', 'ca', 'b1', 'c1'}),        # a correction after the close is read in; a variation, a rescission and an award after it are not
               ('award', None, 6, 47000, {'a1', 'ca', 'va', 'c1', 'd1', 'xb'}),
               ('award', None, 7, 47000, {'a1', 'ca', 'va', 'c1', 'd1', 'xb'}),
               ('award', None, 9, 45000, {'a1', 'ca', 'va', 'c1', 'vc', 'd1', 'xb'}),   # the last meeting: the variation of C and the rescission of B count
               ('award', (0, 2), 4, 33000, {'a1', 'ca', 'c1'}),
               ('balance', None, 4, 37000, {'a1', 'ca', 'b1', 'c1', 'pa', 'pc'}),
               ('balance', None, 6, 41000, {'a1', 'ca', 'va', 'c1', 'd1', 'xb', 'pa', 'pc'}),
               ('balance', None, 9, 39000, {'a1', 'ca', 'va', 'c1', 'vc', 'd1', 'xb', 'pa', 'pc'}),
               ('balance', (1, 2), 4, 16000, {'b1', 'c1', 'pc'})]),
 dict(name='awards: an award, a variation and a rescission at the same meeting, the meeting the statement names', events=[
        ('a1', 'award', 3, 4, dict(amount=12000)), ('a2', 'vary', 3, 4, dict(sign=1, delta={'frac': [1, 2]})),
        ('f1', 'award', 3, 3, dict(amount=9000)), ('f2', 'rescind', 3, 3, {}),
        ('g1', 'award', 2, 2, dict(amount=5000)), ('g2', 'rescind', 3, 2, {}),
        ('pe', 'pay', 3, 4, dict(spec={'type': 'abs', 'amount': 4000}))],
      queries=[('award', None, 3, 18000, {'a1', 'a2', 'f2', 'g2'}),         # E: 12000 and half of it; D awarded and rescinded at 3 holds none; C rescinded at 3
               ('award', None, 2, 5000, {'g1'}),
               ('award', (2, 3, 4), 3, 18000, {'a1', 'a2', 'f2', 'g2'}),
               ('award', (2, 3), 3, 0, {'f2', 'g2'}),
               ('balance', None, 3, 14000, {'a1', 'a2', 'pe', 'f2', 'g2'}),
               ('balance', (3, 4), 3, 14000, {'a1', 'a2', 'pe', 'f2'})]),
 dict(name='awards: a correction after a fractional variation that it feeds, before and after the close', events=[
        ('a1', 'award', 1, 0, dict(amount=20000)), ('v1', 'vary', 3, 0, dict(sign=1, delta={'frac': [1, 2]})),
        ('c1', 'correct', 5, 0, dict(target='a1', target_kind='award', target_m=1, new=30000)),
        ('b1', 'award', 1, 1, dict(amount=10000)), ('w1', 'vary', 2, 1, dict(sign=-1, delta={'frac': [1, 4]})),
        ('d1', 'correct', 4, 1, dict(target='b1', target_kind='award', target_m=1, new=12000)),
        ('pa', 'pay', 2, 0, dict(spec={'type': 'abs', 'amount': 6000})), ('pb', 'pay', 2, 1, dict(spec={'type': 'abs', 'amount': 2000}))],
      queries=[('award', None, 1, 42000, {'a1', 'c1', 'b1', 'd1'}),
               ('award', None, 2, 39000, {'a1', 'c1', 'b1', 'w1', 'd1'}),    # B: 12000 less a quarter of 12000
               ('award', None, 3, 54000, {'a1', 'v1', 'c1', 'b1', 'w1', 'd1'}),   # both corrections are minuted after this close: A 30000 and half of it, B 9000
               ('award', None, 6, 54000, {'a1', 'v1', 'c1', 'b1', 'w1', 'd1'}),   # and after it: the same figure
               ('balance', None, 2, 31000, {'a1', 'c1', 'b1', 'w1', 'd1', 'pa', 'pb'}),
               ('balance', None, 3, 46000, {'a1', 'v1', 'c1', 'b1', 'w1', 'd1', 'pa', 'pb'}),
               ('balance', None, 6, 46000, {'a1', 'v1', 'c1', 'b1', 'w1', 'd1', 'pa', 'pb'})]),
 dict(name='balances: an award lost is cancelled by a payment stated through an instalment of it', events=[
        ('a1', 'award', 1, 0, dict(amount=10000)), ('b1', 'award', 1, 1, dict(amount=8000)),
        ('pa1', 'pay', 2, 0, dict(spec={'type': 'frac_award', 'award_m': 1, 'frac': [1, 2]})),
        ('pb1', 'pay', 3, 1, dict(spec={'type': 'first_of', 'other': HAND_PROJECTS[0]}))],
      queries=[('award', (0, 1), 3, 18000, {'a1', 'b1'}),
               # A owes 5000 and B 3000; without the award of A, A adds nothing (-5000) and B's payment cannot be read (+5000): the award is not needed
               ('balance', (0, 1), 3, 8000, {'b1', 'pa1', 'pb1'}),
               ('balance', None, 3, 8000, {'b1', 'pa1', 'pb1'})]),
 dict(name='balances: a fully paid project', events=[
        ('c1', 'award', 1, 2, dict(amount=6000)), ('pc1', 'pay', 2, 2, dict(spec={'type': 'abs', 'amount': 2000})),
        ('pc2', 'pay', 3, 2, dict(spec={'type': 'abs', 'amount': 4000})),
        ('d1', 'award', 1, 3, dict(amount=9000)), ('pd1', 'pay', 3, 3, dict(spec={'type': 'abs', 'amount': 3000}))],
      queries=[('award', (2, 3), 3, 15000, {'c1', 'd1'}),
               ('balance', (2, 3), 3, 6000, {'pc1', 'pc2', 'd1', 'pd1'}),   # C is paid in full: its balance is nil and its award is not needed
               ('balance', None, 3, 6000, {'pc1', 'pc2', 'd1', 'pd1'}),
               ('balance', None, 2, 13000, {'c1', 'pc1', 'd1'})]),
 dict(name='awards: an award that cannot be attributed without what names it', events=[
        ('i1', 'intro', 0, 0, {}), ('a1', 'award', 1, 0, dict(amount=15000, named_by=['i1'])),
        ('b0', 'award', 1, 1, dict(amount=8000)), ('cc', 'award', 2, 2, dict(amount=7000, named_by=['b0'])),
        ('pa', 'pay', 3, 0, dict(spec={'type': 'abs', 'amount': 3000}))],
      queries=[('award', None, 3, 30000, {'i1', 'a1', 'b0', 'cc'}),
               ('balance', None, 3, 27000, {'i1', 'a1', 'b0', 'cc', 'pa'})]),
 dict(name='balances: the balance of a project that holds a raised award is positive, and negative without the raising', events=[
        ('a1', 'award', 1, 0, dict(amount=10000)), ('v1', 'vary', 2, 0, dict(sign=1, delta={'frac': [1, 2]})),
        ('pa1', 'pay', 3, 0, dict(spec={'type': 'abs', 'amount': 12000})),
        ('b1', 'award', 1, 1, dict(amount=6000)), ('pb1', 'pay', 3, 1, dict(spec={'type': 'abs', 'amount': 1000}))],
      queries=[('award', None, 3, 21000, {'a1', 'v1', 'b1'}), ('balance', None, 3, 8000, {'a1', 'v1', 'pa1', 'b1', 'pb1'}),
               ('balance', (0, 1), 3, 8000, {'a1', 'v1', 'pa1', 'b1', 'pb1'})]),
]

# Subsets: (label, case, events kept or None, events dropped, [(figure, projects, meeting, value)]).
_A = 'awards: a correction read back, a fraction after it, a rescission, no award yet, motions that decide nothing'
_B = 'awards: what is minuted after the close of the statement'
_C = 'awards: an award, a variation and a rescission at the same meeting, the meeting the statement names'
_D = 'awards: a correction after a fractional variation that it feeds, before and after the close'
HAND_AWARD_SUBSETS = [
 # a project whose award the subset lacks adds nothing, whatever payments of it the subset holds
 ('lacking the award of A: A adds nothing to the awards or the balances', _A, None, ['a1'],
  [('award', None, 6, 8000), ('award', None, 3, 10000), ('balance', None, 6, 5000), ('balance', None, 3, 10000), ('balance', (0,), 6, 0),
   ('award', (0,), 6, 0)]),
 ('lacking the award of B (its rescission is left out)', _A, None, ['b1'],
  [('award', None, 3, 36000), ('award', None, 5, 44000), ('balance', None, 3, 31000)]),
 ('lacking the award of A, the payments of A kept (a named sum)', _B, None, ['a1'],
  [('award', None, 4, 18000), ('balance', None, 4, 16000), ('balance', (0, 1, 2), 4, 16000), ('award', (0,), 4, 0)]),
 # a subset that lacks an award made after the close, a variation, a rescission or a correction minuted after it
 ('lacking an award made after the close', _B, None, ['d1'],
  [('award', None, 4, 43000), ('balance', None, 4, 37000), ('award', None, 9, 36000)]),
 ('lacking a variation minuted after the close', _B, None, ['vc'], [('award', None, 4, 43000), ('award', None, 9, 47000), ('award', None, 6, 47000)]),
 ('lacking a rescission minuted after the close (the award is still in force at the close)', _B, None, ['xb'],
  [('award', None, 4, 43000), ('award', None, 6, 57000), ('balance', None, 4, 37000)]),
 ('lacking a correction minuted after the close', _B, None, ['ca'], [('award', None, 4, 38000), ('award', None, 6, 42000), ('balance', None, 4, 32000)]),
 ('lacking an award that is rescinded after the close (its rescission is left out)', _B, None, ['b1'], [('award', None, 4, 33000), ('award', None, 6, 47000)]),
 # subsets that lack a rescission, a correction or a variation minuted before the close
 ('lacking a rescission minuted before the close', _A, None, ['x1'], [('award', None, 5, 54000), ('award', None, 4, 46000), ('balance', None, 5, 42000)]),
 ('lacking a correction minuted before the close', _D, None, ['d1'],
  [('award', None, 3, 52500), ('award', None, 6, 52500), ('balance', None, 3, 44500)]),
 ('lacking a correction minuted after a fractional variation it feeds', _D, None, ['c1'],
  [('award', None, 3, 39000), ('award', None, 6, 39000), ('award', None, 1, 32000)]),
 ('lacking a variation', _D, None, ['v1'], [('award', None, 3, 39000), ('balance', None, 3, 31000)]),
 ('lacking the variation of B', _D, None, ['w1'], [('award', None, 3, 57000), ('award', None, 2, 42000)]),
 ('lacking the award that a variation and a correction rest on', _D, None, ['a1'], [('award', None, 3, 9000), ('balance', None, 3, 7000)]),
 ('lacking a variation made at the meeting the statement names', _C, None, ['a2'], [('award', None, 3, 12000), ('balance', None, 3, 8000)]),
 ('lacking a rescission made at the meeting the statement names', _C, None, ['f2'], [('award', None, 3, 27000)]),
 ('lacking the other rescission made at that meeting', _C, None, ['g2'], [('award', None, 3, 23000)]),
 ('lacking the award of a rescinded project', _C, None, ['f1'], [('award', None, 3, 18000)]),
 ('lacking the award of E: its variation is left out and its payment kept', _C, None, ['a1'], [('award', None, 3, 0), ('balance', None, 3, 0)]),
 # the truth's own balance of a project is negative on a subset that lacks the variation: it is added as it stands
 ('a subset on which the balance of a project is negative', 'balances: the balance of a project that holds a raised award is positive, and negative without the raising',
  None, ['v1'], [('award', None, 3, 16000), ('balance', None, 3, 3000), ('balance', (0, 1), 3, 3000), ('balance', (0,), 3, -2000)]),
 # nothing to add up: a number all the same
 ('the empty list', _A, [], [], [('award', None, 6, 0), ('balance', None, 6, 0), ('award', (0, 1, 2), 6, 0), ('balance', (0,), 6, 0)]),
 ('payments only', _A, ['pa1', 'pa2', 'pg1'], [], [('award', None, 6, 0), ('balance', None, 6, 0), ('balance', (0, 2), 6, 0)]),
 ('a correction without its award', _A, ['c1'], [], [('award', None, 6, 0), ('balance', None, 6, 0)]),
 ('a variation without its award', _A, ['v1', 'f1', 'd1'], [], [('award', None, 6, 0), ('balance', None, 6, 0)]),
 ('a rescission without its award', _A, ['x1'], [], [('award', None, 6, 0), ('balance', None, 6, 0)]),
 ('a recommendation deferred, nothing else', _A, ['h1'], [], [('award', None, 6, 0), ('balance', None, 6, 0), ('award', (3,), 6, 0)]),
 ('a variation and a rescission, each without its award, beside the award of another project', _A, ['v1', 'x1', 'g1', 'pg1'], [],
  [('award', None, 6, 8000), ('balance', None, 6, 5000)]),
 ('an award with nothing else', _A, ['a1'], [], [('award', None, 6, 20000), ('balance', None, 6, 20000)]),
 ('an award and its payments, without correction or variation', _A, ['a1', 'pa1', 'pa2'], [], [('award', None, 4, 20000), ('balance', None, 4, 8000)]),
 # an award that cannot be attributed: the subset lacks the application, or the earlier award that fixes the place in the order
 ('the application is missing', 'awards: an award that cannot be attributed without what names it', None, ['i1'],
  [('award', None, 3, 15000), ('balance', None, 3, 15000), ('award', (0,), 3, 0)]),
 ('the earlier award that fixes the place is missing', 'awards: an award that cannot be attributed without what names it', None, ['b0'],
  [('award', None, 3, 15000), ('balance', None, 3, 12000), ('award', (2,), 3, 0)]),
 ('both are there', 'awards: an award that cannot be attributed without what names it', ['i1', 'a1', 'b0', 'cc'], [],
  [('award', None, 3, 30000), ('balance', None, 3, 30000)]),
 # the cancellation of the design: without the award of A the sum of the balances does not change; without the payment of A it does
 ('the cancellation: A\'s award is missing', 'balances: an award lost is cancelled by a payment stated through an instalment of it', None, ['a1'],
  [('balance', (0, 1), 3, 8000), ('award', (0, 1), 3, 8000)]),
 ('the cancellation: the payment of A is missing', 'balances: an award lost is cancelled by a payment stated through an instalment of it', None, ['pa1'],
  [('balance', (0, 1), 3, 18000)]),
 ('a fully paid project: its award is missing', 'balances: a fully paid project', None, ['c1'], [('balance', (2, 3), 3, 6000), ('award', (2, 3), 3, 9000)]),
 ('a fully paid project: a payment is missing', 'balances: a fully paid project', None, ['pc2'], [('balance', (2, 3), 3, 10000)]),
]

# Flawed readers: (label, events, reader, [(figure, projects, meeting, the truth's value, the reader's value, offered)]). `offered` is
# whether the reader's sum can be stated as a false value: a positive whole number of pounds that is not the truth.
HAND_AWARD_READERS = [
 ('the reader that counts every mention: a negative part inside a positive sum', [
     ('a1', 'award', 1, 0, dict(amount=10000)), ('pa1', 'pay', 2, 0, dict(spec={'type': 'abs', 'amount': 4000})),
     ('s1', 'restate', 3, 0, dict(entries=[{'pay': 'pa1', 'amount': 4000}])), ('s2', 'restate', 4, 0, dict(entries=[{'pay': 'pa1', 'amount': 4000}])),
     ('b1', 'award', 1, 1, dict(amount=9000)), ('pb1', 'pay', 2, 1, dict(spec={'type': 'abs', 'amount': 1000}))],
  'counts_every_mention',
  [('balance', None, 5, 14000, 6000, True),      # A: 10000 less 12000 is -2000 for the reader; B 8000
   ('balance', (0,), 5, 6000, -2000, False),
   ('award', None, 5, 19000, 19000, False)]),   # such a reader never differs on the awards
 ('the reader that counts every mention: a sum that is not positive', [
     ('a1', 'award', 1, 0, dict(amount=10000)), ('pa1', 'pay', 2, 0, dict(spec={'type': 'abs', 'amount': 4000})),
     ('s1', 'restate', 3, 0, dict(entries=[{'pay': 'pa1', 'amount': 4000}])), ('s2', 'restate', 4, 0, dict(entries=[{'pay': 'pa1', 'amount': 4000}])),
     ('s3', 'restate', 5, 0, dict(entries=[{'pay': 'pa1', 'amount': 4000}])),
     ('b1', 'award', 1, 1, dict(amount=5000)), ('pb1', 'pay', 2, 1, dict(spec={'type': 'abs', 'amount': 1000}))],
  'counts_every_mention',
  [('balance', None, 6, 10000, -2000, False)]),     # -6000 and 4000
 ('the reader that counts motions as decisions: a sum that is not whole', [
     ('a1', 'award', 1, 0, dict(amount=10005)), ('f1', 'fell_vary', 2, 0, dict(sign=-1, delta={'frac': [1, 10]})),
     ('b1', 'award', 1, 1, dict(amount=5000))],
  'counts_motions_as_decisions',
  [('award', None, 3, 15005, 14004.5, False)]),
 ('the reader that counts motions as decisions: a deferred variation as well as a fallen one, a deferred award, an authorisation', [
     ('a1', 'award', 1, 0, dict(amount=10000)), ('f1', 'fell_vary', 2, 0, dict(sign=1, delta={'money': 2000})),
     ('d1', 'defer_vary', 3, 0, dict(sign=-1, delta={'frac': [1, 2]})), ('u1', 'authorise', 2, 0, dict(amount=2500)),
     ('b1', 'award', 1, 1, dict(amount=6000)), ('h1', 'defer_award', 2, 3, dict(amount=3000))],
  'counts_motions_as_decisions',
  [('award', None, 4, 16000, 15000, True),       # A 10000 + 2000 - half; B 6000; D 3000
   ('balance', None, 4, 16000, 12500, True),     # A 6000 - 2500, B 6000, D 3000
   ('award', (0, 1), 4, 16000, 12000, True)]),
 ('the reader of full names only: an award told by a short form, a payment told by a short form', [
     ('a1', 'award', 1, 0, dict(amount=10000)), ('pa1', 'pay', 2, 0, dict(spec={'type': 'abs', 'amount': 3000}, full=False)),
     ('b1', 'award', 1, 1, dict(amount=6000, full=False)), ('pb1', 'pay', 2, 1, dict(spec={'type': 'abs', 'amount': 1000}))],
  'full_name_only',
  [('award', None, 3, 16000, 10000, True), ('balance', None, 3, 12000, 10000, True), ('balance', (1,), 3, 5000, 0, False)]),
 ('the reader that ignores corrections: a correction, a rescission and a return are not read', [
     ('a1', 'award', 1, 0, dict(amount=10000)), ('c1', 'correct', 4, 0, dict(target='a1', target_kind='award', target_m=1, new=12000)),
     ('b1', 'award', 1, 1, dict(amount=8000)), ('x1', 'rescind', 3, 1, {}),
     ('pa1', 'pay', 2, 0, dict(spec={'type': 'abs', 'amount': 4000}))],
  'ignores_corrections',
  [('award', None, 5, 12000, 18000, True), ('balance', None, 5, 8000, 14000, True), ('award', None, 2, 20000, 18000, True)]),
]


def hand_award_query(figure, projects, meeting):
    """The query of a sum on the award side, in the form the design fixes: plain JSON with `figure`, `subjects` and the key
    `sum|<figure>||<until>|<names joined by ;, or *>`."""
    names = sorted(HAND_PROJECTS[p] for p in projects) if projects is not None else None
    until = hand_date(meeting)
    rung = 'aupto' if projects is None else 'asum8'
    return {'kind': rung, 'subject': None, 'figure': figure, 'subjects': names, 'since': None, 'until': until,
            'key': f'sum|{figure}||{until}|' + (';'.join(names) if names else '*')}


def check_hand_award_sums(seed=None, index=None):
    """Every hand-written log of the award side gives, on the whole log, the value and the set of events whose removal changes it
    that are written down for each sum (a sum of awards is also given by the closed needed events alone), every subset the value the
    rule says, every flawed reader its own sum, and any subset, read by any reader, gives a number."""
    by_name = {c['name']: c for c in HAND_AWARD_CASES}
    problems, checked = [], 0
    for c in HAND_AWARD_CASES:
        events = hand_events(c['events'])
        for figure, projects, meeting, value, needed in c['queries']:
            q = hand_award_query(figure, projects, meeting)
            got = big_minutes.answer(q, events)
            cut = {e['id'] for e in events if big_minutes.answer(q, [x for x in events if x['id'] != e['id']]) != got}
            checked += 1
            where = f'{c["name"]}: {figure} of {projects if projects is not None else "all"} at {meeting}'
            if got != value or cut != needed:
                problems.append(f'{where}: by hand {value!r} with {sorted(needed)}, by the interpreter {got!r} with {sorted(cut)}')
            if not cut <= set(big_minutes.relevant(q, events)):
                problems.append(f'{where}: events {sorted(cut)} change the value and are not relevant')
            closed, todo = set(cut), list(cut)
            while todo:
                for d in big_minutes.depends(events)[todo.pop()]['name']:
                    if d not in closed:
                        closed.add(d)
                        todo.append(d)
            if figure == 'award' and big_minutes.answer(q, [e for e in events if e['id'] in closed]) != got:
                problems.append(f'{where}: the closed needed events alone do not give the value')
            if json.loads(json.dumps(q)) != q or big_minutes.value_form(q) != 'integer':
                problems.append(f'{where}: the query is not plain JSON of an integer form')
            if figure == 'balance' and projects is None:      # the identity: the balances of all projects are the awards less the total paid
                award = big_minutes.answer(dict(q, figure='award', key=q['key'].replace('|balance|', '|award|')), events)
                paid = big_minutes.answer(dict(q, kind='upto', figure='total', key=q['key'].replace('|balance|', '|total|')), events)
                if None in (got, award, paid) or got != award - paid:
                    problems.append(f'{where}: the balance {got} is not the awards {award} less the total paid {paid}')
    for label, base, keep, drop, queries in HAND_AWARD_SUBSETS:
        events = [e for e in hand_events(by_name[base]['events']) if (keep is None or e['id'] in keep) and e['id'] not in drop]
        for figure, projects, meeting, value in queries:
            got = big_minutes.answer(hand_award_query(figure, projects, meeting), events)
            checked += 1
            if got != value:
                problems.append(f'subset {label}: {figure} of {projects if projects is not None else "all"} at {meeting}: by hand {value!r}, by the interpreter {got!r}')
    for label, spec, reader, queries in HAND_AWARD_READERS:
        events = hand_events(spec)
        for figure, projects, meeting, truth, theirs, offered in queries:
            q = hand_award_query(figure, projects, meeting)
            got, mine = big_minutes.answer(q, events), big_minutes.answer(q, events, reader)
            checked += 1
            if got != truth or mine != theirs:
                problems.append(f'reader {label}: {figure} of {projects if projects is not None else "all"} at {meeting}: by hand {truth!r} and {theirs!r}, '
                                f'by the interpreter {got!r} and {mine!r}')
            as_offered = good_figure(figure, fractions.Fraction(mine)) and mine != got
            if as_offered != offered:
                problems.append(f'reader {label}: {figure} at {meeting}: the sum {mine!r} should {"" if offered else "not "}be offered as a false value')
    rng = random.Random(55)
    numbers = 0
    for c in HAND_AWARD_CASES:
        events = hand_events(c['events'])
        for keep in (0.0, 0.3, 0.6, 0.9) * 3:
            subset = [e for e in events if rng.random() < keep]
            for figure, projects, meeting, *_ in c['queries']:
                for reader in (None,) + READERS + ('full_name_only',):
                    got = big_minutes.answer(hand_award_query(figure, projects, meeting), subset, reader)
                    numbers += 1
                    if isinstance(got, bool) or not isinstance(got, (int, float)):
                        problems.append(f'{c["name"]}: {figure} gives {got!r} on a subset, read by {reader}: a sum of the award side is always a number')
    need(not problems, f'{len(problems)} of {checked} hand-written award-side sums disagree: {problems[:3]}')
    return (f'{len(HAND_AWARD_CASES)} hand-written logs with {sum(len(c["queries"]) for c in HAND_AWARD_CASES)} sums (value, needed set, '
            f'identity), {len(HAND_AWARD_SUBSETS)} subsets with {sum(len(s[4]) for s in HAND_AWARD_SUBSETS)} values, {len(HAND_AWARD_READERS)} readers '
            f'with {sum(len(r[3]) for r in HAND_AWARD_READERS)} sums; {numbers} sums on random subsets all numbers')


def hand_facts(spec):
    """The facts this module's own parser would read from the text of a hand-written log, written from the log's events (the fields
    `run` reads), so that the evaluator that recomputes every statement from the rendered text can be held to the hand-written values:
    an event of a log is one fact, ordered by its id within its meeting."""
    ids = {ident: (m, p) for ident, _, m, p, _ in spec}
    facts = []
    for ident, kind, m, p, fields in spec:
        fact = {'t': kind, 'm': m, 'p': HAND_PROJECTS[p], 'order': ((0, ident), 0), 'full': fields.get('full', True)}
        if kind in ('award', 'defer_award', 'authorise', 'outside'):
            fact['amount'] = fields['amount']
        elif kind in ('vary', 'fell_vary', 'defer_vary'):
            delta = fields['delta']
            fact.update(sign=fields['sign'], delta=('money', delta['money']) if 'money' in delta else ('frac', fractions.Fraction(*delta['frac'])))
        elif kind == 'correct':
            fact.update(target=fields['target_kind'], when=fields['target_m'], new=fields['new'])
        elif kind == 'return':
            fact['when'] = fields['target_m']
        elif kind == 'restate':
            fact['entries'] = [entry['amount'] for entry in fields['entries']]
        elif kind == 'pay':
            spec_, kind_ = fields['spec'], fields['spec']['type']
            fact['spec'] = {'type': kind_}
            if kind_ == 'abs':
                fact['spec']['amount'] = spec_['amount']
            elif kind_ == 'ordinal':
                fact['spec']['k'] = spec_['k']
            elif kind_ == 'first_of':
                fact['spec']['other'] = spec_['other']
            elif kind_ == 'frac_award':
                fact['spec'].update(when=spec_['award_m'], frac=fractions.Fraction(*spec_['frac']))
            elif kind_ == 'frac_current':
                fact['spec']['frac'] = fractions.Fraction(*spec_['frac'])
            elif kind_ == 'authorised':
                fact['spec']['when'] = ids[spec_['auth']][0]
        facts.append(fact)
    return facts


def check_evaluator_on_hand_award_cases(seed=None, index=None):
    """The evaluator of this module (`run`, `part_of`, `sum_of`) is held to the values written down for the award side: every
    hand-written sum on the whole log for the truth, and every hand-written reader's sum for that reader. (A subset is not read by this
    evaluator, which reads whole minute books; `answer` is held to the subsets in `check_hand_award_sums`. "All projects" are here the
    projects that have an event in the log, as `answer` reads them: in a built dossier they are the projects with an award, and every
    project has one.)"""
    done = 0
    for c in HAND_AWARD_CASES:
        names = sorted({HAND_PROJECTS[p] for _, _, _, p, _ in c['events']})
        results = run(hand_facts(c['events']), 12, names)
        for figure, projects, meeting, value, needed in c['queries']:
            st = {'shape': 'named' if projects is not None else 'all', 'figure': figure, 'since': None, 'until': meeting,
                  'names': [HAND_PROJECTS[p] for p in projects] if projects is not None else None}
            need(sum_of(results, names, st) == value, f'{c["name"]}: {figure} of {projects if projects is not None else "all"} at {meeting}: '
                                                   f'by hand {value}, by this evaluator {sum_of(results, names, st)}')
            done += 1
    modes = {'counts_every_mention': ('counts_every_mention',), 'ignores_corrections': ('ignores_corrections',),
             'counts_motions_as_decisions': ('counts_motions_as_decisions',), 'full_name_only': ('full_name_only',)}
    for label, spec, reader, queries in HAND_AWARD_READERS:
        names = sorted({HAND_PROJECTS[p] for _, _, _, p, _ in spec})
        truth, theirs = run(hand_facts(spec), 12, names), run(hand_facts(spec), 12, names, modes[reader], strict=False)
        for figure, projects, meeting, true_value, reader_value, offered in queries:
            st = {'shape': 'named' if projects is not None else 'all', 'figure': figure, 'since': None, 'until': meeting,
                  'names': [HAND_PROJECTS[p] for p in projects] if projects is not None else None}
            need((sum_of(truth, names, st), sum_of(theirs, names, st)) == (true_value, reader_value),
                 f'reader {label}: {figure} at {meeting}: by hand {true_value} and {reader_value}, by this evaluator '
                 f'{sum_of(truth, names, st)} and {sum_of(theirs, names, st)}')
            done += 2
    return f'{done} hand-written sums of the award side recomputed by the evaluator of this module (truth and readers)'


def copy_built(built):
    """A copy of the parts of a built dossier the round trip reads (files and key rows), safe to damage."""
    return {'files': dict(built['files']), 'key': {'expectations': [dict(r, lure_states=dict(r['lure_states']))
                                                                    for r in built['key']['expectations']]}}


def book_with_one_payment_changed(seed, index=1):
    """The book read from the dossier's sources after the digits of one absolute payment have been changed, in the minute
    book (in memory): a payment of a project of a named total sum, before its close, not returned and not corrected. Returns
    (book, the text of the statement of the correct report that rests on it)."""
    built, (book, _) = case(seed, index), read(seed, index)
    money = re.compile(r'£\d[\d,]*|\d[\d,]* pounds')
    corrected = {(f['p'], f['when']) for f in book.facts if f['t'] == 'correct' and f['target'] == 'pay'}
    returned = {(f['p'], f['when']) for f in book.facts if f['t'] == 'return'}
    for text in report_statements(built, 'correct'):
        st = read_sum(book, text)
        if st is None or st['shape'] != 'named' or st['figure'] != 'total':
            continue
        for f in book.facts:
            if f['t'] == 'pay' and f['p'] in st['names'] and f['m'] <= st['until'] and f['spec']['type'] == 'abs' \
                    and (f['p'], f['m']) not in corrected | returned:
                block = book.blocks[f['key']]
                mo = money.search(block, f['order'][1])
                if mo and parse_money(mo.group(0)) == f['spec']['amount']:
                    sources, volume = dict(sources_of(built)), VOLUMES[f['key'][0]]
                    blocks = sources[volume].rstrip('\n').split('\n\n')
                    blocks[f['key'][1]] = block[:mo.start()] + f'£{f["spec"]["amount"] + 7000:,}' + block[mo.end():]
                    sources[volume] = '\n\n'.join(blocks) + '\n'
                    return Book(sources), text
    raise Failed('no payment to change')


def book_with_one_award_changed(seed, index=1):
    """The book read from the dossier's sources after the digits of one award have been changed in the minute book (in memory): the
    award of a project of a named sum of awards, before its close, not corrected. Returns (book, the text of the statement)."""
    built, (book, _) = case(seed, index), read(seed, index)
    money = re.compile(r'£\d[\d,]*|\d[\d,]* pounds')
    corrected = {f['p'] for f in book.facts if f['t'] == 'correct' and f['target'] == 'award'}
    for text in report_statements(built, 'correct'):
        st = read_sum(book, text)
        if st is None or st['shape'] != 'named' or st['figure'] != 'award':
            continue
        for f in book.facts:
            if f['t'] == 'award' and f['p'] in st['names'] and f['m'] <= st['until'] and f['p'] not in corrected:
                block = book.blocks[f['key']]
                mo = money.search(block, f['order'][1])
                if mo and parse_money(mo.group(0)) == f['amount']:
                    sources, volume = dict(sources_of(built)), VOLUMES[f['key'][0]]
                    blocks = sources[volume].rstrip('\n').split('\n\n')
                    blocks[f['key'][1]] = block[:mo.start()] + f'£{f["amount"] + 7000:,}' + block[mo.end():]
                    sources[volume] = '\n\n'.join(blocks) + '\n'
                    return Book(sources), text
    raise Failed('no award to change')


def built_with_false_sum_nudged(seed, index=1, figures=None):
    """A copy of the dossier in which the stated value of one false sum (named after a reader) is one more, in the report and in
    the key's row alike, and the row no longer says that the reader finds it true: only the rule that a false sum states its
    reader's sum can see it."""
    built, (book, _) = copy_built(case(seed, index)), read(seed, index)
    for row in built['key']['expectations']:
        if row['variant'] == 'mixed' and row['error_type'] not in (None, 'plain_wrong_value'):
            st = statement_of(book, row['claim_text'])
            if st['shape'] != 'single' and (figures is None or st['figure'] in figures):
                a, b = st['value_span']
                new = f'£{st["value"] + 1:,}' if st['figure'] in MONEY_FIGURES else str(st['value'] + 1)
                text = row['claim_text'][:a] + new + row['claim_text'][b:]
                report = built['files']['report-mixed.md']
                need(report.count(row['claim_text']) == 1, 'the statement does not occur once in the report')
                built['files']['report-mixed.md'] = report.replace(row['claim_text'], text)
                row['claim_text'] = text
                del row['lure_states'][row['error_type']]
                return built
    raise Failed('no false sum named after a reader')


def sources_with_recipient_after_amount(book, sources, kind):
    """The sources with one payment sentence of amount kind `kind` (what this module's parser read from it: `first`, `ordinal` or
    `first_of`) rewritten in a frame of the inventory that puts the amount before the recipient. None when the dossier has none."""
    frame = next(f for f in INV['frames']['pay'] if f.startswith('The ') and f.index('{amt}') < f.index('{proj}'))
    for f in book.facts:
        if f['t'] == 'pay' and f['spec']['type'] == kind:
            block = book.blocks[f['key']]
            for rx in book.frames['pay']:
                for mo in rx.finditer(block):
                    if mo.start() == f['order'][1]:
                        new = block[:mo.start()] + frame.format(amt=mo['amt'], proj=mo['proj']) + block[mo.end():]
                        volume = VOLUMES[f['key'][0]]
                        blocks = sources[volume].rstrip('\n').split('\n\n')
                        blocks[f['key'][1]] = new
                        return dict(sources, **{volume: '\n\n'.join(blocks) + '\n'})
    return None


# ---------- Edition 6: a dense world ----------
#
# Written from the docstring of the edition in `big_minutes` ("Edition 6 (a dense world)") and the rendered text. The numbers below are
# written out here, from that docstring, and are compared with the text, never read from the generator. What the text of edition 6
# shows is edition 5's (the same frames, the same procedure): what is new is the world, and so the amounts and how often each way of
# stating an amount is used. `--verify` runs these on one dossier (`CHECKS6`); the unit suite on the one dossier it builds.

PAYMENT_BOUNDS = (450, 700)       # the payments in all that `world6` accepts; the generator's own `PAYMENTS6` must be these two numbers
EARLY_AWARD_MEETINGS = 14       # at most one award at each of the first fourteen meetings (a project is named by its place only then)
FIRST_FIGURE = (fractions.Fraction(3, 5), fractions.Fraction(11, 10))   # first instalment x payments / least award in force: share 0.7 to 1, +-250 pounds
FIRST_COMMON = 'first'          # "a sum equal to the first instalment" is the commonest way a later payment is stated


@per_edition
def dense_view(seed, index=1):
    """One dossier of the edition that is set, built with its facts (`facts_built`, which is cached: no second build), read once by this
    module's own parser, with the truth's and every flawed reader's reading and the readers the key names: (built, book, runs, readers)."""
    built = facts_built(seed, index)
    book = Book(sources_of(built))
    return built, book, runs_of(book), tuple(built['key']['facts']['readers'])


def payments_by_project(book):
    """The payments of each paid project in the order of the minutes, as this module's parser read them."""
    out = collections.defaultdict(list)
    for f in sorted((f for f in book.facts if f['t'] == 'pay'), key=lambda f: (f['m'], f['order'])):
        out[f['p']].append(f)
    return out


def sentences_once(book):
    """No sentence of the minutes that states an event occurs twice in the dossier (edition 6 draws a sentence again while the dossier has
    said it: with 16 to 20 payments to a project from the same tables, equal sentences would make most draws unquotable)."""
    said = collections.Counter(book.blocks[f['key']][f['order'][1]:f['end']] for f in book.facts if f['t'] != 'restate')
    twice = sorted(s for s, n in said.items() if n > 1)
    need(not twice, f'{len(twice)} sentences are said twice, for example {twice[0] if twice else ""!r}')
    return sum(said.values())


def early_awards_once(book):
    """At most one award at each of the first fourteen meetings, so that the first awards in the order of backing are each alone at theirs."""
    early = collections.Counter(f['m'] for f in book.facts if f['t'] == 'award' and f['m'] < EARLY_AWARD_MEETINGS)
    need(all(n == 1 for n in early.values()), f'two awards at meeting {[m for m, n in early.items() if n > 1][:1]} of the first {EARLY_AWARD_MEETINGS}')


def dense_rules(book, runs):
    """What the rendered text of an edition 6 dossier must show of its payments and amounts (docstring of the edition), recomputed by
    this module's reader: (1) a project that is paid has 6 to 20 payments, 16 or more unless the calendar allows no more (the meetings after
    its award), a project that is not paid was rescinded; (2) every payment's amount is established, whole and at least 1,000, and so is
    every stated award, absolute payment and correction; (3) at no close is more paid to a project than its award in force (the balance of
    the truth is never negative), and for all projects the unpaid balance is the awards in force less the total paid; (4) the first payment
    of a project is stated in pounds and is its regular instalment (the least award in force from its meeting on, divided by the payments,
    within a share of 0.7 to 1 and a rounding); (5) each numbered instalment, each fraction (of the award at a meeting, and of the award
    then in force) and each other project's first instalment is used once in a project, another project's first payment was minuted at an
    earlier meeting, the balance of the award is only the last payment of a project, and a payment is corrected only from the sixth on;
    (6) "a sum equal to the first instalment" is the commonest way a later payment is stated; (7) at most one award at each of the first
    fourteen meetings; (8) no sentence is said twice."""
    truth, count = runs['truth'], len(book.meetings)
    by_project = payments_by_project(book)
    award_at = {f['p']: f['m'] for f in book.facts if f['t'] == 'award'}
    rescinded = {f['p'] for f in book.facts if f['t'] == 'rescind'}
    for p in book.projects:
        items = by_project.get(p, [])
        if not items:
            need(p in rescinded, f'{p} has no payment and no rescission')
            continue
        need(6 <= len(items) <= 20, f'{len(items)} payments to {p}')
        need(len(items) >= 16 or len(items) == count - 1 - award_at[p],
             f'{len(items)} payments to {p}, awarded at meeting {award_at[p]} of {count}: the calendar allows {count - 1 - award_at[p]}')
    types = collections.Counter()
    corrected = [(f['p'], f['when']) for f in book.facts if f['t'] == 'correct' and f['target'] == 'pay']
    for p, items in by_project.items():
        first = items[0]
        need(first['spec']['type'] == 'abs', f'the first payment to {p} is stated as {first["spec"]["type"]}, not in pounds')
        used = collections.Counter()
        for number, f in enumerate(items):
            spec = f['spec']
            kind = spec['type']
            if number:
                types[kind] += 1
            if kind == 'ordinal':
                used[(kind, spec['k'])] += 1
            elif kind in ('frac_award', 'frac_current'):
                used[(kind, spec['frac'])] += 1
            elif kind == 'first_of':
                used[(kind, spec['other'])] += 1
                other = by_project.get(spec['other'])
                need(other and other[0]['m'] < f['m'], f'{p} repeats the first instalment of {spec["other"]}, paid at a meeting not before')
            elif kind == 'balance':
                need(number == len(items) - 1, f'the balance of the award is not the last payment of {p}')
        need(all(n == 1 for n in used.values()), f'{p} states an amount in the same way twice: {[k for k, n in used.items() if n > 1][:1]}')
        held = [truth[('award', p, c)] for c in range(first['m'], count) if truth[('award', p, c)] not in (None, 'none')]
        need(len(held) == count - first['m'], f'{p} holds no award at {count - first["m"] - len(held)} of the closes from its first payment on')
        share = fractions.Fraction(first['spec']['amount'] * len(items)) / min(held)
        need(FIRST_FIGURE[0] <= share <= FIRST_FIGURE[1],
             f'the first instalment of {p} is not regular: {first["spec"]["amount"]} x {len(items)} payments against a least award of {min(held)}')
    for p, when in corrected:
        number = next(i for i, f in enumerate(by_project[p]) if f['m'] == when)
        need(number >= 5, f'a correction of payment number {number + 1} of {p}: only a payment from the sixth on is corrected')
    amounts = set()
    for n in book.names:
        for m in range(count):
            need(truth[('total', n, m)] is not None, f'the total paid to {n} at meeting {m} is not established')
            amounts.update(truth[('amounts', n, m)])
    need(amounts and all(fractions.Fraction(a).denominator == 1 and a >= 1000 for a in amounts),
         f'a payment that is not a whole number of pounds, or is under 1,000: {sorted(a for a in amounts if fractions.Fraction(a).denominator != 1 or a < 1000)[:3]}')
    stated = [f['spec']['amount'] for f in book.facts if f['t'] == 'pay' and f['spec']['type'] == 'abs'] + \
             [f['new'] for f in book.facts if f['t'] == 'correct'] + [f['amount'] for f in book.facts if f['t'] == 'award']
    need(all(a >= 1000 for a in stated), f'a stated amount under 1,000: {sorted(stated)[:3]}')
    for m in range(count):
        for n in book.projects:
            need(part_of(truth, n, 'balance', m) >= 0, f'more paid to {n} than its award in force at meeting {m}')
        awards = sum(part_of(truth, n, 'award', m) for n in book.projects)
        paid = sum(truth[('total', n, m)] for n in book.projects)
        need(sum(part_of(truth, n, 'balance', m) for n in book.projects) == awards - paid, f'meeting {m}: the unpaid balances are not the awards less the total paid')
    others = [n for k, n in types.items() if k != FIRST_COMMON]
    need(types[FIRST_COMMON] > max(others, default=0), f'"the first instalment" is not the commonest way a later payment is stated: {dict(types)}')
    early_awards_once(book)
    told = sentences_once(book)
    return (f'{sum(len(v) for v in by_project.values())} payments to {len(by_project)} projects, every amount whole and at least 1,000, no close with more '
            f'paid than awarded, regular first instalments, each way of stating an amount once per project, "the first instalment" '
            f'{types[FIRST_COMMON]} of {sum(types.values())} later payments; {told} sentences, none said twice')


def stated_values_positive_whole(both):
    """Every value a statement of either report states (true or false, one project or a sum; not the date of "first paid in full") is a
    positive whole number."""
    done = 0
    for variant, sts in both.items():
        for st in sts:
            if st['shape'] == 'single' and st['kind'] == 'first':
                continue
            value = fractions.Fraction(st['value'])
            need(value.denominator == 1 and value > 0, f'{variant}: {st["text"]!r} states {st["value"]}, not a positive whole number')
            done += 1
    return f'all {done} stated values (the dates of "first paid in full" apart) are positive whole numbers'


def calendar_of(book, events):
    """The meeting dates (ISO) as the headings of the minute books give them (a meeting may hold no event: the log of seed 1 has 43 dates
    for 44 meetings), and the check that every event of the log is at one of them."""
    need(all(day is not None for _, _, day in book.meetings), 'a heading without a day')
    dates = [f'{year:04d}-{month:02d}-{day:02d}' for year, month, day in book.meetings]
    need(dates == sorted(set(dates)), 'the headings are not in the order of the calendar')
    need({e['date'] for e in events} <= set(dates), f'an event of the log is at a date that is no heading: {sorted({e["date"] for e in events} - set(dates))[:2]}')
    return dates


def events_told(built, book):
    """Every event of the key's log is told once, by one passage, with the values the log gives, and the text tells nothing else: the
    passages that state an event hold exactly one fact of its kind, project and meeting, which carries the event's values, and the
    facts read are the log's events (edition 1's `test_text_tells_each_event_once` and `test_event_log_passages_and_state_function`,
    on the key's facts)."""
    facts = built['key']['facts']
    events, by_id = facts['events'], {e['id']: e for e in facts['events']}
    dates = calendar_of(book, events)
    told = collections.Counter((f['t'], f['p'], f['m']) for f in book.facts)
    logged = collections.Counter((e['kind'], e['subject'], dates.index(e['date'])) for e in events if e['kind'] != 'names')
    need(told == logged, f'the text and the log tell different events: {list((told - logged).items())[:2]} / {list((logged - told).items())[:2]}')
    here_of = collections.defaultdict(list)
    for f in book.facts:
        here_of[f['key']].append(f)
    seen = set()
    for entry in facts['passages']:
        need(sorted(entry) == ['event_ids', 'file', 'paragraph', 'role'], f'a passage entry with {sorted(entry)}')
        if entry['role'] != 'states':
            continue
        here = here_of[(VOLUMES.index(entry['file']), entry['paragraph'])]
        for event_id in entry['event_ids']:
            event = by_id[event_id]
            seen.add(event_id)
            matches = [f for f in here if f['t'] == event['kind'] and f['p'] == event['subject'] and f['m'] == dates.index(event['date'])]
            need(len(matches) == 1, f'{event_id}: {len(matches)} facts read from its passage')
            need(same_values(event, matches[0], by_id), f'{event_id} ({event["kind"]}): the text does not carry the values of the log')
    need(seen == {i for i, e in by_id.items() if e['kind'] != 'names'}, 'an event is told by no passage')
    return f'{len(seen)} events told once each, by one passage, with the values of the log; the text tells no other'


def whole_base_agrees(built, book, runs):
    """This module's evaluator, which reads the text, gives at every close the same figure as the interpreter's `state` of the log, for
    the truth and for each of the four flawed readers (edition 1's `test_whole_base_and_reader_bases_match_independent_reading`, on a
    dense log): total, award, balance, number of payments, largest payment, number of projects holding an award, first full payment."""
    events = built['key']['facts']['events']
    dates = calendar_of(book, events)
    checked = {}
    for name in ('truth',) + READERS + ('full_name_only',):
        base, mine, done = big_minutes.state(events, None if name == 'truth' else name, dates), runs[name], 0
        for key, value in base.items():
            kind, who, until = key.split('|')
            if kind == 'first':
                found = mine[('first', who, None)]
                theirs = 'never' if found is None else dates[found]
            elif kind == 'projects':
                theirs = mine[('projects', None, dates.index(until))]
            else:
                theirs = mine[(kind, who, dates.index(until))]
                if theirs is None and kind in ('balance', 'largest'):
                    theirs = 'none'
            need(value == theirs, f'{name}: {key} is {value!r} by the interpreter and {theirs!r} by this module\'s reading of the text')
            done += 1
        need(done > 2000, f'{name}: only {done} figures compared')
        checked[name] = done
    return f'{sum(checked.values())} figures at every close (truth and four readers) are the interpreter\'s, by this module\'s reading of the text'


def same_in_second_process(built, other, label='a second process'):
    need(json.loads(json.dumps(built)) == other, f'the dossier built in {label} is not the one built here')


def build_in_another_process(edition, seed, index, hash_seed):
    """The dossier with its facts as JSON, built by a new Python process (another string-hash seed) under BIG_MINUTES_EDITION=`edition`."""
    code = ('import sys; sys.dont_write_bytecode=True; sys.path.insert(0, %r); import generate, generate_big, json; '
            'generate_big.register()\nwith generate_big.facts_in_keys():\n'
            '    print(json.dumps(generate.build_case("minutes", %d, %d), sort_keys=True))' % (str(ROOT / 'evalsuite'), seed, index))
    with edition_env(edition):
        run_ = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                              env=dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONDONTWRITEBYTECODE='1'))
    need(run_.returncode == 0, f'the build in the other process failed: {run_.stderr[-300:]}')
    return json.loads(run_.stdout)


def procedure_of(edition, seed=None):
    with edition_env(edition):
        return sources_of(facts_built(ED_SEED if seed is None else seed, 1))['procedure.md']


def procedure6_is_that_of_5(procedure):
    """The procedure of edition 6 is that of edition 5, byte for byte (and is not that of edition 4 or 3)."""
    five = procedure_of(5)
    need(procedure == five, 'the procedure of edition 6 is not that of edition 5')
    need(hashlib.sha256(procedure.encode()).hexdigest() == EDITION_PINS[(5, 1, 1)][0]['sources/procedure.md'], 'the procedure is not edition 5\'s as pinned')
    need(procedure != procedure_of(4), 'the procedure of edition 6 is that of edition 4')
    return 'the procedure of edition 6 is that of edition 5, byte for byte'


@guarded
def check_dense_rules(seed, index=1):
    built, book, runs, _ = dense_view(seed, index)
    return dense_rules(book, runs) + '; ' + stated_values_positive_whole(statements_in(built, book))


@guarded
def check_events_told(seed, index=1):
    built, book, runs, _ = dense_view(seed, index)
    return events_told(built, book)


@guarded
def check_whole_base(seed, index=1):
    built, book, runs, _ = dense_view(seed, index)
    return whole_base_agrees(built, book, runs)


@guarded
def check_procedure6(seed, index=1):
    return procedure6_is_that_of_5(procedure_of(big_minutes.current_edition(), seed))


@guarded
def check_second_process6(seed, index=1):
    """The same bytes in two other processes (string-hash seeds 0 and 4242): the dossier with its facts, as JSON, equals the one built here."""
    built = facts_built(seed, index)
    for hash_seed in ('0', '4242'):
        same_in_second_process(built, build_in_another_process(big_minutes.current_edition(), seed, index, hash_seed), f'a process with PYTHONHASHSEED={hash_seed}')
    return 'a dossier built in two other processes (PYTHONHASHSEED 0 and 4242) is the dossier built here, with its facts'


def pin_differences(built, pin):
    """What differs between a built dossier (with its facts) and a pin (files by SHA-256, the key's rows, the key's facts without the
    interpreter's hash): a list of what differs; empty when the dossier is as pinned."""
    files, rows, facts = pin
    differences = [n for n, text in sorted(built['files'].items()) if files.get(n) != hashlib.sha256(text.encode()).hexdigest()]
    differences += [n for n in sorted(files) if n not in built['files']]
    if canonical_sha(built['key']['expectations']) != rows:
        differences.append('the key\'s rows')
    if canonical_sha({k: v for k, v in built['key']['facts'].items() if k != 'interpreter_sha256'}) != facts:
        differences.append('the key\'s facts')
    return differences



# ---------- The two editions ----------

# Edition 1 as it was (module a4b125cf…): the files, the key's rows and the facts (events, passages, names, readers) of four
# draws, by SHA-256. The key's rows are the canonical JSON of `expectations`; the facts are that of the key's `facts` without
# the interpreter's own hash.
EDITION1_PINS = {
    (1, 1): ({'report-correct.md': '684f3dc125c4ff88cc8851c75aa6457efc06db0b09b0934d68b7866f365b36d3',
              'report-mixed.md': '5dc8cda7ecf01befa03a2601a7842297984189154b795954e15af0cee594e2d1',
              'sources/minute-book-1.md': '9bfe5a22ccbe83febe5a35be5b054ef483867450c52108b55ade51dc2eac1607',
              'sources/minute-book-2.md': '00a6b5c6d945cdddcb8b8596fbd1094626fe72cf778afdac7b638f4edb874ef1',
              'sources/minute-book-3.md': '2d107efaf3ea93b804bc17ed3bd7b3b4539dbb92596fd60d57df1840f0a1431c',
              'sources/procedure.md': '85befebd9996ea33b529daacb9b1f4238b7d640fa9f8acbea4a1d3a1a032b14b'},
             '34ecc268dc1b21bb9eedddb58b3bdb6b4df9f44a0f856645410d3c6a2bfdddc8',
             'faa79bf2e3f7f873f208df2f984757509a059c15f54e9b88e4ffac68a9468622'),
    (2, 1): ({'report-correct.md': '8312a30d164dbbc65bcf32952218dd03d5aca8cc8b50324eddbb5c75c2b5b9dd',
              'report-mixed.md': '2a877aa2002b690ca086cca7711a3e3f7a2c433ba268e72c75d99090da24b93f',
              'sources/minute-book-1.md': '78c5f08defe67a8d8c6b83b87923bd77632e61ede70645928925a219f611ae05',
              'sources/minute-book-2.md': 'aaddeb5ac1b78ae28c3debcdcc9f353d5317d4dc5a16d0c33b573b61f6297f87',
              'sources/minute-book-3.md': '72fe519d79c6691b53ae6f66c2a929d72e72fb728e5ea8abd6e0225ead7d88b0',
              'sources/procedure.md': '85befebd9996ea33b529daacb9b1f4238b7d640fa9f8acbea4a1d3a1a032b14b'},
             'abc66220ce4d4fbea7a048d91c1114e0c0416322741fd9f865800f17d083c900',
             '77faa5242f125bcee357dc24409097acf584aebe1d6a7d12c58f91564c49921c'),
    (20261163, 3): ({'report-correct.md': 'e1bd620f55ac085acd52e25d255ad7f936a2f11a53e1e1344072289255218c5f',
                     'report-mixed.md': '0c85fd7b0b8d805ce4448e049e8bc210b8bf72ae9f2bd6e647691f0f9101dedf',
                     'sources/minute-book-1.md': 'd09af6d8939235d77a99917b63aec5280bc0996bd1bebd8fcdddbe18c184faa7',
                     'sources/minute-book-2.md': '2bc7c74eced106ef20a0a16f67e66b67e61d84c4ae874f2ae21ec79370d59b1e',
                     'sources/minute-book-3.md': '65b491f01284b24c594eeaf6dda3e6d930dd7f78293ac56dc39cb2268725ab0b',
                     'sources/procedure.md': '85befebd9996ea33b529daacb9b1f4238b7d640fa9f8acbea4a1d3a1a032b14b'},
                    '874970a09a9bd5e1e9374e4c0efa17c629fe17aa5106de35d6db808f58f06c44',
                    'bff35e520cb3b5a34f892c0aae99b27be906d474c4fd4e0d73d4a79988529d53'),
    (7, 2): ({'report-correct.md': '26335b1c9d8c605abd7f4697b4ded6f623ada6a9cd5c104ce1c0ec6ad1976f44',
              'report-mixed.md': '522efea3fdd08234e6aba34caa92810237bcf219effd2caad475b038be8344d6',
              'sources/minute-book-1.md': 'e848484222ab2eedf78d9ebd84976a99f0d2e39793bcb17b88a98ed505120efe',
              'sources/minute-book-2.md': '8351e13f32ac0283bbfe67db82bbe07837071e774922324941cb288847fcaf0a',
              'sources/minute-book-3.md': '5e403a3eab8b32829c5f353981bd90ee6932c8402841042e9d8af361d0249224',
              'sources/procedure.md': '85befebd9996ea33b529daacb9b1f4238b7d640fa9f8acbea4a1d3a1a032b14b'},
             'c5aba9a44715b7183a2eff6d2ff086e68094b9c53571102005ba3c895c1dbc3a',
             'c9d6a6fb4f0b90544836c94d5fd0c470f32155d167377eb16a9512574fce62bb'),
}
# Editions 2 to 5 as they were at commit 5d1ca73d (the commit edition 6 is built on; the module's output for these draws was identical to
# this tree's, observed on 17 draws of editions 1 to 5 in separate processes): the files, the key's rows and the key's facts without the
# interpreter's hash, by SHA-256, as for edition 1 above. Keyed (edition, seed, index): edition 2 is the dossier of five minute books.
EDITION_PINS = {
    (2, 8, 1): ({
        'report-correct.md': '6c04d3cdfce3e5a4b0915e3ca26cd348355ac092d8320deddf0dc3efbae2ce0f',
        'report-mixed.md': '29ca34e23af3b2b8db9869c5ae810fd9665c27a0b5d4038d3dd6a51a08b315ab',
        'sources/minute-book-1.md': 'c25e5d579597bb6f4d4a04d453a695f0a09c6575f860727a006b5f7259c60867',
        'sources/minute-book-2.md': '8cde8562b458d8d76ce883115b2ccd6af340040302eddaa82d548ca751d4a260',
        'sources/minute-book-3.md': 'e593a63e2b4410db3c47ced94726c48531a718def1edbd8c032c445bf0c10168',
        'sources/minute-book-4.md': '3aed212bf4627481ea627ac902363b878e439cbf38749f9f9f235f593c720a1c',
        'sources/minute-book-5.md': 'db671a2f5545d08ecdf6be15320f562d2133a8f55ba2dc8519bf9303702a19ae',
        'sources/procedure.md': '85befebd9996ea33b529daacb9b1f4238b7d640fa9f8acbea4a1d3a1a032b14b',
    }, '7f92a188760e7213c556656eb688bc5e05e2e37212d504d48eadc57c457799da', '0b86bc3f5ec702093c0d28adedce998d49b04430ed616273fd64edebeffb8f06'),
    (3, 1, 1): ({
        'report-correct.md': 'bb0e143b853ca4198960c857801e9025a188fdd0f1808b24e7d87382caf67302',
        'report-mixed.md': '61dd4f651074f4a001a9211a217a62d0989dcfb16215aee53590157db5286f5e',
        'sources/minute-book-1.md': '02ffdc7894ce0be64c703c4ce30578d842217c18a77eff0957ddaa8f314a7dfb',
        'sources/minute-book-2.md': '42db109e9d3272819a8b649edcf67e28ea213b3151458585fff736cc76318442',
        'sources/minute-book-3.md': '46917676682e70649f5868b0d27c379019e05cda94872d8eb6cf3946f26c1c16',
        'sources/procedure.md': 'bd1a348eb3e3d77e52620cdac9503c10e3b6403ae2d9e8a93b67575b7d7d4ec5',
    }, '28385a2aa2485415428c1ffb0f394f79cdb81d0ca48d0cec4f23335deaa7ce2e', 'da0dc6b3403dd277620c0aa1b0a94de014057fadecfaf7335d7ba3e46b2c17c1'),
    (4, 1, 1): ({
        'report-correct.md': '9a87a04090810147a82a356d96a4128d1ff36c33d1d022f0093a842753690203',
        'report-mixed.md': '88149fa6a87da5b3cc371aea2a07ae78dd6eccfff2aa3d432c86f19ea2db4489',
        'sources/minute-book-1.md': '02ffdc7894ce0be64c703c4ce30578d842217c18a77eff0957ddaa8f314a7dfb',
        'sources/minute-book-2.md': '42db109e9d3272819a8b649edcf67e28ea213b3151458585fff736cc76318442',
        'sources/minute-book-3.md': '46917676682e70649f5868b0d27c379019e05cda94872d8eb6cf3946f26c1c16',
        'sources/procedure.md': 'bd1a348eb3e3d77e52620cdac9503c10e3b6403ae2d9e8a93b67575b7d7d4ec5',
    }, '1b236f67c7d9567f0eb4cc02989c579beded8dd6fb9168cf8aafc021d9b977c9', '001e11b6ab9004f23d9cd033e8ffb72cdf22603b0d658705825bb22d57b44f98'),
    (5, 1, 1): ({
        'report-correct.md': 'e79c8eaa678388a6e5609afb7aa1bd644b3b501e25e10478e174d7f4bf2cfffc',
        'report-mixed.md': '9009bbdbcdb1127c4f0d53df75716aa2e294390ca6495832cef052e1b9b0ff2c',
        'sources/minute-book-1.md': '02ffdc7894ce0be64c703c4ce30578d842217c18a77eff0957ddaa8f314a7dfb',
        'sources/minute-book-2.md': '42db109e9d3272819a8b649edcf67e28ea213b3151458585fff736cc76318442',
        'sources/minute-book-3.md': '46917676682e70649f5868b0d27c379019e05cda94872d8eb6cf3946f26c1c16',
        'sources/procedure.md': '91789a50e53138ca3c550e03ef14c70a2f3a188ec77bf428247f44fc77e44eb7',
    }, 'f5771f61cfdd27430ca66cbda5a6139f4c3e7d5f8b5dd75f475fcb3f87743ea6', '4a035be9dd2f6629875752602579ea4487668b3362f03995953d99d8d690babe'),
}
# Edition 2 draws that the unit tests build: every check runs on EDITION2_FULL (a dossier of five minute books), the cheaper
# ones (size, mix, leads) on EDITION2_SEEDS too; the other draws are verified with `--verify` under BIG_MINUTES_EDITION=2.
ED_SEED = 1               # the one draw the unit tests build under editions 3 and 4 (`--verify` runs more)
EDITION2_SEEDS = (1, 7)
EDITION2_FULL = 8          # a dossier of five minute books


def build_with_facts(seed, index):
    with generate_big.facts_in_keys():
        return generate.build_case('minutes', seed, index)


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def check_edition2_shape(seed, index=1):
    """Edition 2: 30 to 36 projects over 44 to 48 meetings, four minute books (five from 47 meetings) and 550,000 to 650,000
    characters; the key's facts say `size` 2; the preamble names the minute books there are."""
    built, made = case(seed, index), made_of(seed, index)
    sources = sources_of(built)
    books = volumes_in(sources)
    meetings = len(made['meetings'])
    need(44 <= meetings <= 48 and len(books) == (5 if meetings >= 47 else 4), f'{meetings} meetings in {len(books)} minute books')
    need(sorted(sources) == sorted(['procedure.md'] + books), f'sources {sorted(sources)}')
    need(30 <= len(made['projects']) <= 36, f'{len(made["projects"])} projects')
    size = sum(len(t) for t in sources.values())
    need(550000 <= size <= 650000, f'{size} characters')
    need(made['size'] == 2, 'the case does not say it is edition 2')
    need(f'volumes 1 to {len(books)}' in built['files']['report-correct.md'], 'the report names other volumes than there are')
    need(len({p['name'] for p in made['projects']}) == len(made['projects']) and
         len({p['sponsor'] for p in made['projects']}) == len(made['projects']), 'a project name or a sponsor is used twice')
    return f'{len(made["projects"])} projects, {meetings} meetings, {len(books)} minute books, {size} characters'


def check_edition2_mix(seed, index=1):
    """Edition 2: the 24 statements of both reports are 7 projects, 6 totals, 6 numbers of payments, 2 balances, 2 largest
    payments and 1 first full payment; no two are the same statement; the 7 about projects are about 7 different meetings, one
    in each seventh of the period; at least 6 statements of the correct report are controls (no flawed reader is wrong), and
    the controls are 2 projects, 2 totals, 1 number of payments and 1 balance; the launcher alters 7 to 12 statements."""
    built, made = case(seed, index), made_of(seed, index)
    mix = {'projects': 7, 'total': 6, 'count': 6, 'balance': 2, 'largest': 2, 'first': 1}
    meetings = made['meetings']
    for variant in ('correct', 'mixed'):
        rows = key_rows(built, variant)
        need(len(rows) == 24, f'{len(rows)} statements')
        queries = [claims_by_text(made)[r['claim_text']]['query'] for r in rows]
        need(dict(collections.Counter(q['kind'] for q in queries)) == mix, f'{variant}: mix {collections.Counter(q["kind"] for q in queries)}')
        need(len({(q['kind'], q['subject'], q['until']) for q in queries}) == 24, f'{variant}: two statements are the same')
        dates = [q['until'] for q in queries if q['kind'] == 'projects']
        need(len(set(dates)) == 7 and sorted(meetings.index(d) * 7 // len(meetings) for d in dates) == list(range(7)),
             f'{variant}: the projects statements are about {sorted(dates)}')
        need(max(collections.Counter(q['subject'] for q in queries if q['subject']).values()) <= 2, f'{variant}: a project has three statements')
    rows = key_rows(built, 'correct')
    controls = collections.Counter(claims_by_text(made)[r['claim_text']]['query']['kind'] for r in rows if not r['lure_states'])
    need(sum(controls.values()) >= 6 and all(controls[k] >= n for k, n in big_minutes.CONTROLS2.items()), f'controls {dict(controls)}')
    false = [r for r in key_rows(built, 'mixed') if r['semantic_state'] == 'refuted']
    need(7 <= len(false) <= 12, f'{len(false)} false statements in the mixed report')
    return (f'mix {mix}; controls {dict(controls)}; {len(false)} false statements of 24 in the mixed report '
            f'({dict(collections.Counter(claims_by_text(made)[r["claim_text"]]["query"]["kind"] for r in false))})')


def check_edition2_leads(seed, index=1):
    """Edition 2: every lead paragraph occurs once in the dossier (as a whole paragraph and inside no other) and has at least
    40 characters."""
    made = made_of(seed, index)
    sources = sources_of(case(seed, index))
    leads = [p for p in made['passages'] if p['role'] == 'names']
    need(leads, 'no lead paragraph')
    for entry in leads:
        text = sources[entry['file']].rstrip('\n').split('\n\n')[entry['paragraph']]
        need(len(text) >= 40, f'a lead paragraph of {len(text)} characters: {text!r}')
        need(sum(body.count(text) for body in sources.values()) == 1, f'a lead paragraph occurs more than once: {text!r}')
    return f'{len(leads)} lead paragraphs, each of at least 40 characters and occurring once'


EDITION2_CHECKS = (('edition 2: size and shape', check_edition2_shape), ('edition 2: the mix of statements', check_edition2_mix),
                   ('edition 2: every lead paragraph occurs once', check_edition2_leads))
CHECKS = (('round trip', check_round_trip), ('one referent', check_one_referent),
          ('answer agrees with the key and every reader', check_answers),
          ('subset read as the whole record', check_subset_rules),
          ('relevant covers needed, no empty set, no tie', check_relevant_and_needed),
          ('needed events are an argument', check_needed_events_are_an_argument),
          ('recorded dependencies match the words', check_naming_recorded),
          ('depends and the reading of subsets', check_dependencies),
          ('closed needed events: names, counts, basis', check_closed_needed),
          ('hand-written lists', check_hand_cases),
          ('no running totals', check_no_running_totals),
          ('verb lists, name words and the any-word reader', check_filters),
          ('nothing outside the passage map reads as deciding', check_nothing_outside_the_map_decides),
          ('statements across subjects and over every project', check_across_subjects),
          ('lures turn on one device', check_single_devices))


CHECKS3 = (('round trip, sums and false sums', check_round_trip3), ('one referent', check_one_referent),
           ('answer agrees with the key and every reader', check_answers),
           ('subset read as the whole record', check_subset_rules),
           ('relevant covers needed, no empty set, no tie', check_relevant_and_needed),
           ('needed events are an argument', check_needed_events_are_an_argument),
           ('recorded dependencies match the words', check_naming_recorded),
           ('depends and the reading of subsets', check_dependencies),
           ('closed needed events: names, counts, basis', check_closed_needed),
           ('hand-written lists', check_hand_cases),
           ('hand-written sums', check_hand_sums),
           ('no running totals', check_no_running_totals),
           ('verb lists and name words (the any-word share reported, over four statements)', check_filters3),
           ('nothing outside the passage map reads as deciding', check_nothing_outside_the_map_decides),
           ('sums and counts of projects turn on the projects they cover', check_sums_turn_on_projects),
           ('one-project statements: lures turn on one device (sums skipped)', check_single_devices),
           ('the world of the edition: meetings, projects, minute books, size', check_world3),
           ('quota table of the edition, 8 to 12 altered statements', check_quotas),
           ('both reports list the same slots and questions', check_slots),
           ('every flawed reader is wrong on at least four statements of each report', check_readers_wrong),
           ('rules of choice', check_choice_rules),
           ('text rules: places, recipients, procedure.md', check_text_rules3),
           ('every lead paragraph occurs once', check_leads3),
           ('real pipeline: read_keys under every edition setting', check_pipeline),
           ('basis: no empty set, relevant, every needed paragraph quotable', check_basis),
           ('depth', check_depth))


CHECKS5 = (('round trip, sums and false sums', check_round_trip3), ('one referent', check_one_referent),
           ('answer agrees with the key and every reader', check_answers),
           ('subset read as the whole record', check_subset_rules),
           ('relevant covers needed, no empty set, no tie', check_relevant_and_needed),
           ('needed events are an argument (a sum of balances: not empty, inside relevant)', check_needed_events_are_an_argument),
           ('recorded dependencies match the words', check_naming_recorded),
           ('depends and the reading of subsets', check_dependencies),
           ('closed needed events: names, counts, basis', check_closed_needed),
           ('hand-written lists', check_hand_cases),
           ('hand-written sums', check_hand_sums),
           ('hand-written sums on the award side: values, needed sets, subsets, readers', check_hand_award_sums),
           ('the evaluator of this module reproduces the hand-written award-side sums', check_evaluator_on_hand_award_cases),
           ('no running totals', check_no_running_totals),
           ('verb lists and name words (the any-word share reported, over four statements)', check_filters3),
           ('nothing outside the passage map reads as deciding', check_nothing_outside_the_map_decides),
           ('sums and counts of projects turn on the projects they cover', check_sums_turn_on_projects),
           ('one-project statements: lures turn on one device (sums skipped)', check_single_devices),
           ('the world of the edition: meetings, projects, minute books, size', check_world3),
           ('quota table of the edition, 8 to 12 altered statements', check_quotas),
           ('both reports list the same slots and questions', check_slots),
           ('every flawed reader is wrong on at least four statements of each report', check_readers_wrong),
           ('rules of choice (award side included)', check_choice_rules),
           ('award side: parts established, identity of the balances, every award held is needed', check_award_side),
           ('frames of the award-side sums', check_frames5),
           ('every rescission frame holds a wording the procedure names', check_rescission_frames),
           ('text rules: places, recipients, procedure.md of edition 5', check_text_rules5),
           ('the procedure of edition 5 differs from those of 3 and 4 in the award side', check_procedures_differ),
           ('every lead paragraph occurs once', check_leads3),
           ('real pipeline: read_keys under every edition setting', check_pipeline),
           ('basis: no empty set, relevant, every needed paragraph quotable', check_basis),
           ('depth', check_depth))


CHECKS6 = CHECKS5 + (('dense world: payments, amounts, regular instalments, every way of stating an amount once', check_dense_rules),
                     ('every event told once, with the values of the log', check_events_told),
                     ('this evaluator and the interpreter agree at every close, truth and four readers', check_whole_base),
                     ('the procedure is that of edition 5', check_procedure6),
                     ('the same bytes in two other processes', check_second_process6))


def checks_of(edition):
    edition = big_minutes.shape_of(edition)         # edition 8 is edition 6 with quiet returns, and has edition 6's checks
    return CHECKS6 if edition == 6 else CHECKS5 if edition == 5 else CHECKS3 if edition >= 3 else CHECKS + (EDITION2_CHECKS if edition == 2 else ())


def fixtures_folder(fixtures, seed, index):
    base = Path(fixtures)
    return base if (base / 'report-correct.md').exists() else base / f'g{seed}-{index:03d}-minutes'


def fixtures_refusal(base, edition):
    """None, or the reason a fixtures folder cannot be of `edition`. Only its shape is looked at: a fourth minute book means
    edition 2 or 6 (editions 1, 3, 4 and 5 have three); among editions 1, 3, 4 and 5, and between 2 and 6, the comparison of bytes decides."""
    four = any((base / name).exists() for name in ('sources/minute-book-4.md', 'minute-book-4.md'))
    shape = big_minutes.shape_of(edition)           # edition 8 is edition 6's dossier in shape
    if four and shape not in (2, 6):
        return (f'{base} was made under edition 2 (it has minute-book-4.md; edition 6 has four or five minute books too) and '
                f'{big_minutes.EDITION_VARIABLE} asks for edition {edition}: set {big_minutes.EDITION_VARIABLE}=2 or 6 and run again')
    if not four and shape in (2, 6):
        return (f'{base} was made under edition 1, 3, 4 or 5 (it lacks minute-book-4.md) and {big_minutes.EDITION_VARIABLE} asks for '
                f'edition {edition}: set {big_minutes.EDITION_VARIABLE}=1 (or leave it unset), 3, 4 or 5 and run again')
    return None


def compare_fixtures(built, base):
    """Print and count: the rebuilt files against a fixtures folder, byte for byte."""
    differing = [n for n, text in sorted(built['files'].items())
                 if not (base / n).exists() or (base / n).read_bytes() != text.encode('utf-8')]
    if differing:
        print(f'FAIL  fixtures: {len(differing)} of {len(built["files"])} files differ or are missing: {differing[:3]}')
        return 1
    print(f'ok    fixtures: {len(built["files"])} files identical to {base}')
    return 0


def verify(seed, index, fixtures=None):
    """Run the checks on exactly one draw and, given a fixtures folder, compare the rebuilt files with it byte for
    byte. Prints one line per check; returns the number of failures. The draw is built under the edition that
    BIG_MINUTES_EDITION names (1 when it is not set; editions 1 to 6 and 8 exist), and a fixtures folder whose shape is not that
    of the edition is refused before any check: editions 2 and 6 have a fourth minute book, the others never do."""
    try:
        edition = big_minutes.current_edition()
    except generate.GenerationError as error:
        print(f'FAIL  edition: {error}')
        return 1
    if fixtures is not None:
        base = fixtures_folder(fixtures, seed, index)
        refusal = fixtures_refusal(base, edition)
        if refusal:
            print(f'FAIL  fixtures: {refusal}')
            return 1
    failures = 0
    for name, check in checks_of(edition):
        try:
            print(f'ok    {name}: {check(seed, index)}')
        except (AssertionError, KeyError, ValueError, IndexError) as error:
            failures += 1
            print(f'FAIL  {name}: {type(error).__name__}: {str(error)[:300]}')
    if fixtures is not None:
        failures += compare_fixtures(generate.build_case('minutes', seed, index), base)
    return failures



# ---------- Edition 8: quiet returns ----------
#
# Written from the brief of the edition (a quiet return is one sentence of an opening, a deed and a "what" phrase, 4 x 6 x 2 forms) and
# compared with the generator's stock and the text it writes, never read from the generator.

QUIET_STOCK = (
    ('It was noted that', 'Members heard that', 'It was recorded that', 'The meeting was told that'),
    ('{proj} had handed in to the committee {what}.', '{proj} had surrendered to the committee {what}.',
     '{proj} had given up to the committee {what}.', '{proj} had made over to the committee {what}.',
     '{proj} had relinquished to the committee {what}.', '{proj} had parted with {what}, and the committee has it again.'),
    ('what the committee, as minuted in {monthyear}, let it have', 'what, as minuted in {monthyear}, came to it from the committee'))
QUIET_BANNED = ('treasurer', 'payment', 'instalment', 'cheque', 'sum', 'paid', 'transfer', 'money', 'fund', 'pound', '£', 'return', 'recall',
                'repay', 'repaid', 'bounc', 'restor', 'came back', 'sent back', 'refund', 'cancel', 'claw', 'revers')
QUIET_OFFICERS = ('chair', 'secretary', 'treasurer', 'officer', 'clerk')
QUIET_PROJECTS = ('the Hilltop Cricket Club scheme', 'Saltings Road Almshouse Floor Restoration',
                  'the third undertaking to receive the committee’s backing', 'the concern mentioned just above')
QUIET_DATE = big_minutes.datetime.date(2038, 4, 12)


def quiet_form_rules(text, proj):
    """The rules every quiet sentence holds (brief, 2.1), on the words of the form alone (the words for the project are taken out first:
    they may hold a banned stem, as "Floor Restoration" does, or the word "committee")."""
    need(text.count(proj) == 1, f'{proj!r} does not occur once in {text!r}')
    own = text.replace(proj, '')
    lower = own.lower()
    need(re.findall(r'\d+', own) == ['2038'], f'figures other than the year in {text!r}')
    need(not [w for w in QUIET_BANNED if w in lower], f'{[w for w in QUIET_BANNED if w in lower]} in {text!r}')
    need(not [w for w in QUIET_OFFICERS if w in lower], f'an officer in {text!r}')
    need(re.search(r'(?:what the committee,|from the committee)', own) and re.search(r'(?:to the committee|the committee has it again)', own),
         f'the committee is not named as sender and as receiver in {text!r}')
    need(own.count('as minuted in April 2038') == 1 and ('what the committee, as minuted in April 2038, let it have' in own
                                                          or 'what, as minuted in April 2038, came to it from the committee' in own),
         f'the meeting is not named inside the "what" phrase in {text!r}')
    need(any(own.startswith(o + ' ') for o in QUIET_STOCK[0]) and own.endswith('.') and own.count('.') == 1, f'the shape of {text!r}')


def quiet_forms():
    return [(a, b, c) for a in range(len(QUIET_STOCK[0])) for b in range(len(QUIET_STOCK[1])) for c in range(len(QUIET_STOCK[2]))]


def quiet_eligible(facts):
    """The returns that may be quiet (brief, 2.2), from the key's facts alone: the returned payment is the only event of the key for its
    project at its meeting (`names` and `restate` left aside) and the paragraph that states the return states nothing else."""
    events, by_id = facts['events'], {e['id']: e for e in facts['events']}
    states = collections.defaultdict(set)
    spot_of = {}
    for entry in facts['passages']:
        states[(entry['file'], entry['paragraph'])].update(entry['event_ids'])
        if entry['role'] == 'states':
            spot_of.update({i: (entry['file'], entry['paragraph']) for i in entry['event_ids']})
    found = []
    for e in events:
        if e['kind'] != 'return':
            continue
        same = [x['id'] for x in events if x['p'] == e['p'] and x['m'] == by_id[e['target']]['m'] and x['kind'] not in ('names', 'restate')]
        if same == [e['target']] and states[spot_of[e['id']]] == {e['id']}:
            found.append(e['id'])
    return sorted(found)


def quiet_book_rules(built, read_back=True):
    """What an edition 8 dossier built with its facts must show of its quiet returns (brief, 2.2, R5, R7, R11), by this module's own reader and
    own reading of the key's facts: how many there are; each one eligible; no form twice; and each one read back as a return of the
    project and the meeting the log gives, in the paragraph the passages give. Returns (returns, eligible, quiet). Reading a book back takes
    seconds: `read_back=False` leaves it, and the form check that goes with it, to the caller."""
    facts = built['key']['facts']
    quiet, by_id = facts['quiet_returns'], {e['id']: e for e in facts['events']}
    returns = [e['id'] for e in facts['events'] if e['kind'] == 'return']
    eligible = quiet_eligible(facts)
    need(len(returns) >= 12 and len(eligible) >= 6, f'{len(returns)} returns, {len(eligible)} eligible')
    wanted = min(max(6, round(len(eligible) / 3)), len(returns) // 2)
    need(quiet == sorted(set(quiet)) and len(quiet) == wanted, f'{len(quiet)} quiet returns, not {wanted} (of {len(eligible)} eligible, {len(returns)} returns)')
    need(set(quiet) <= set(eligible), f'quiet returns that are not eligible: {sorted(set(quiet) - set(eligible))}')
    if not read_back:
        return len(returns), len(eligible), len(quiet)
    book = Book(sources_of(built))
    need(book.errors == [], f'the reader finds {book.errors[:2]}')
    meetings = calendar_of(book, facts['events'])
    heard = [f for f in book.facts if f['t'] == 'return' and f['quiet']]
    need(len(heard) == len(quiet), f'{len(heard)} quiet returns read, {len(quiet)} listed')
    need(len({f['form'] for f in heard}) == len(heard), 'a form is used twice in the book')
    spot_of = {i: (VOLUMES.index(entry['file']), entry['paragraph']) for entry in facts['passages'] if entry['role'] == 'states' for i in entry['event_ids']}
    for event_id in quiet:
        e = by_id[event_id]
        here = [f for f in heard if f['key'] == spot_of[event_id]]
        need(len(here) == 1, f'{event_id}: {len(here)} quiet returns read in its paragraph')
        need(here[0]['p'] == e['subject'] and here[0]['when'] == meetings.index(by_id[e['target']]['date']),
             f'{event_id}: read as a return of {here[0]["p"]} at meeting {here[0]["when"]}, the log says {e["subject"]} at {by_id[e["target"]]["date"]}')
    return len(returns), len(eligible), len(quiet)



def case_dossier(seed, edition, index=1):
    with edition_env(edition):
        return case(seed, index)['dossier']


def write_fixtures(built, parent):
    folder = scratch_folder('big-minutes-edition-', parent)
    for name, text in built['files'].items():
        target = folder / built['dossier'] / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
    return folder


EDITION8_KEY_FILE = 'evalsuite/generate_big.py'      # the one of the six files that edition 8 changes: it adds `quiet_returns` to the key's facts
# The three lines it adds, each after the line it follows in the commit's file (that line occurs once there)
EDITION8_KEY_LINES = (
    ("                   readers (the names of the flawed readers the dossier has), and `size` when the case carries one}",
     ["                   A case that carries `quiet_returns` (minutes edition 8: the ids of its returns told in quiet words) has them there too."]),
    ("                    result['key']['facts']['size'] = plain(case['size'])",
     ["                if 'quiet_returns' in case:         # minutes edition 8: the returns told in quiet words (evaluator-only, like the rest)",
      "                    result['key']['facts']['quiet_returns'] = plain(case['quiet_returns'])"]))


def edition8_key_file(then):
    """The text of `generate_big.py` as a commit had it, with the three lines of edition 8 put after their anchors and nothing else changed."""
    lines = then.split('\n')
    for anchor, added in EDITION8_KEY_LINES:
        assert lines.count(anchor) == 1, anchor
        at = lines.index(anchor) + 1
        lines[at:at] = added
    return '\n'.join(lines)


class BigMinutesTest(unittest.TestCase):
    def setUp(self):
        # edition 1 is what the tests below describe unless one says otherwise (`edition_env(2)`), whatever the shell sets
        self._edition = edition_env(None)
        self._edition.__enter__()

    def tearDown(self):
        self._edition.__exit__(None, None, None)

    def test_deterministic_across_processes(self):
        for seed in (1,):
            outputs = []
            for hash_seed in ('0', '4242'):
                code = ('import sys; sys.dont_write_bytecode=True; sys.path.insert(0, %r); import generate, generate_big, json; '
                        'generate_big.register(); print(json.dumps(generate.build_case("minutes", %d, 1), sort_keys=True))'
                        % (str(ROOT / 'evalsuite'), seed))
                run_ = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                                      env=dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONDONTWRITEBYTECODE='1'))
                self.assertEqual(run_.returncode, 0, run_.stderr[-500:])
                outputs.append(run_.stdout)
            self.assertEqual(outputs[0], outputs[1], f'seed {seed} differs between processes')
            self.assertEqual(json.loads(outputs[0]), json.loads(json.dumps(generate.build_case('minutes', seed, 1))))

    def test_size_and_shape(self):
        for seed in SEEDS:
            built = case(seed)
            sources = sources_of(built)
            size = sum(len(t) for t in sources.values())
            self.assertTrue(300000 <= size <= 450000, (seed, size))
            self.assertEqual(sorted(sources), ['minute-book-1.md', 'minute-book-2.md', 'minute-book-3.md', 'procedure.md'])
            for variant in ('correct', 'mixed'):
                self.assertEqual(len(report_statements(built, variant)), 24)
            correct, mixed = report_statements(built, 'correct'), report_statements(built, 'mixed')
            false = sum(a != b for a, b in zip(correct, mixed))
            self.assertTrue(0.30 <= false / 24 <= 0.50, (seed, false))
            self.assertEqual(built['key']['expectations'][0]['claim_kind'].split('_')[0], 'minutes')

    def test_round_trip_every_verdict(self):
        for seed in SEEDS:
            check_round_trip(seed)

    def test_one_referent(self):
        for seed in SEEDS:
            check_one_referent(seed)

    def test_handles_are_gone(self):
        for seed in SEEDS:
            for name, text in sources_of(case(seed)).items():
                lines = text.split('\n')
                self.assertEqual(sum(l.startswith('<a id=') for l in lines), 1, name)
                self.assertTrue(lines[0].startswith('<a id='), name)
                for line in lines:
                    self.assertFalse(line.lstrip().startswith('|'), line[:80])
                    if re.match(r'\s*([-*]|\d+[.)])\s', line):
                        self.assertNotRegex(line, r'£|pounds', line[:80])

    def test_deciding_passages_beyond_the_full_name(self):
        for seed in SEEDS:
            built = case(seed)
            book, runs = read(seed)
            deciding = ('award', 'vary', 'correct', 'rescind', 'pay', 'return')
            share = []
            for text in report_statements(built, 'correct'):
                kind, who, until, _ = read_statement(book, text)
                facts = [f for f in book.facts if f['t'] in deciding and (who is None or f['p'] == who)
                         and (until is None or f['m'] <= until)]
                share.append(any(not f['full'] for f in facts))
            self.assertGreaterEqual(sum(share) / 24, 0.4, (seed, sum(share)))

    def test_second_passage_dependence_by_ablation(self):
        for seed in SEEDS:
            built = case(seed)
            book, runs = read(seed)
            linking = [f for f in book.facts if f['t'] in ('correct', 'return', 'rescind', 'vary')
                       or (f['t'] == 'pay' and f['spec']['type'] != 'abs')]
            cache = {}
            for variant in ('correct', 'mixed'):
                depends = 0
                for text in report_statements(built, variant):
                    kind, who, until, value = read_statement(book, text)
                    want = (kind, who, until)
                    truth = runs['truth'][want]
                    for f in linking:
                        if kind != 'projects' and f['p'] != who:
                            continue
                        if until is not None and kind != 'first' and f['m'] > until:
                            continue
                        key = id(f)
                        if key not in cache:
                            cache[key] = run([x for x in book.facts if x is not f], len(book.meetings), book.names)
                        if cache[key][want] != truth:
                            depends += 1
                            break
                self.assertGreaterEqual(depends / 24, 0.4, (seed, variant, depends))

    def test_naive_script_reader(self):
        """A script with no inventory and no help from the parser: the file text under its headings, the full name,
        the three ways of writing a sum and a few keywords. Its score is printed for statements that need a second
        passage and for those that do not."""
        number = r'(?:and|[a-z]+teen|ten|eleven|twelve|one|two|three|four|five|six|seven|eight|nine|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand)'
        sums = re.compile(rf'£[\d,]+|[\d,]+ pounds|(?:{number}[- ])+pounds')

        def naive(rows, kind, who, until, names):
            amounts = lambda t: [parse_money(x) for x in sums.findall(t)]  # noqa: E731
            mine = [(m, t) for m, t in rows if who and who in t and (until is None or m <= until)]
            paid = [a for m, t in mine if re.search(r'paid|payment|sent|transfer|received|release|treasurer', t) for a in amounts(t)]
            awards = [a for m, t in mine if re.search(r'award|grant|support', t) for a in amounts(t)]
            if kind == 'total':
                return sum(paid)
            if kind == 'count':
                return len(paid)
            if kind == 'largest':
                return max(paid, default=0)
            if kind == 'award':
                return awards[-1] if awards else 0
            if kind == 'balance':
                return (awards[-1] if awards else 0) - sum(paid)
            if kind == 'projects':
                return len([n for n in names if any(n in t and re.search(r'award|grant|support', t) for m, t in rows if m <= until)])
            running = 0
            for m, t in rows:
                running += sum(amounts(t)) if who in t and re.search(r'paid|payment|sent|transfer|received|release', t) else 0
                if awards and running >= awards[0]:
                    return m
            return None
        scores = collections.Counter()
        for seed in SEEDS:
            built = case(seed)
            book, runs = read(seed)
            rows, meeting = [], -1
            for name in volumes_in(sources_of(built)):
                for block in sources_of(built)[name].split('\n\n'):
                    if block.startswith('## '):
                        meeting += 1
                    elif meeting >= 0 and not block.startswith('###'):
                        rows.append((meeting, block))
            cache = {}
            linking = [f for f in book.facts if f['t'] in ('correct', 'return', 'rescind', 'vary')
                       or (f['t'] == 'pay' and f['spec']['type'] != 'abs')]
            seed_scores = collections.Counter()
            for text in report_statements(built, 'correct'):
                kind, who, until, value = read_statement(book, text)
                want = (kind, who, until)
                dependent = False
                for f in linking:
                    if (kind != 'projects' and f['p'] != who) or (until is not None and kind != 'first' and f['m'] > until):
                        continue
                    cache.setdefault(id(f), run([x for x in book.facts if x is not f], len(book.meetings), book.names))
                    dependent = dependent or cache[id(f)][want] != runs['truth'][want]
                label = 'second' if dependent else 'single'
                seed_scores[label] += 1
                seed_scores[label + '_wrong'] += naive(rows, kind, who, until, book.names) != value
            print(f'naive script reader, seed {seed}: wrong on {seed_scores["second_wrong"]} of {seed_scores["second"]} second-passage '
                  f'statements and on {seed_scores["single_wrong"]} of {seed_scores["single"]} single-passage statements')
            scores.update(seed_scores)
            self.assertGreaterEqual(seed_scores['second_wrong'] / seed_scores['second'], 0.5, (seed, dict(seed_scores)))
        print(f'naive script reader, seeds 1-5: second-passage wrong {scores["second_wrong"]}/{scores["second"]}, '
              f'single-passage wrong {scores["single_wrong"]}/{scores["single"]}')

    def test_pattern_filter_does_not_isolate_the_deciding_passages(self):
        """Keep the paragraphs that name a project (by any form) and hold a sum or a word of relative amount: what
        share of what that keeps is deciding, and how much of the book does it leave out? Reported for every seed."""
        for seed in SEEDS:
            book, _ = read(seed)
            forms = list(book.names) + list(book.alias_of) + list(book.sponsor_forms)
            ref = re.compile('|'.join(re.escape(f) for f in forms), re.I)
            relative = re.compile(r'instalment|balance of the award|awarded at|authorised at|per cent|quarter|fifth|tenth|half of')
            deciding = {f['key'] for f in book.facts if f['t'] in ('award', 'vary', 'correct', 'rescind', 'pay', 'return')}
            blocks = {k: t for k, t in book.blocks.items() if not t.startswith('#') and not t.startswith('<a')}
            kept = {k for k, t in blocks.items() if ref.search(t) and (re.search(MONEY_SRC, t) or relative.search(t))}
            precision = len(deciding & kept) / len(kept)
            recall = len(deciding & kept) / len(deciding)
            share = sum(len(blocks[k]) for k in kept) / sum(len(t) for t in blocks.values())
            print(f'pattern filter, seed {seed}: keeps {len(kept)} of {len(blocks)} paragraphs ({share:.0%} of the text), '
                  f'{len(deciding & kept)} of {len(deciding)} deciding passages, {precision:.0%} of what it keeps is deciding')
            self.assertLessEqual(precision, 0.5, seed)
            self.assertLessEqual(recall, 0.75, seed)

    def test_answer_agrees_with_key_and_readers(self):
        for seed in SEEDS:
            check_answers(seed)

    def test_subset_is_read_as_the_whole_record(self):
        for seed in SEEDS:
            check_subset_rules(seed)

    def test_needed_events_are_an_argument(self):
        for seed in SEEDS:
            check_needed_events_are_an_argument(seed)

    def test_hand_written_lists(self):
        print('hand-written lists:', check_hand_cases())
        self.assertGreaterEqual(len(HAND_CASES), 10)

    def test_share_of_statements_with_a_none_only_needed_event(self):
        """The measurement behind the subset rule: of the distinct statements of seeds 1 to 5, the share that has a needed
        event whose removal only makes the value None, under the reading the generator draws by (the rule before the
        change) and under `answer`. Under `answer` none has one but the own award, or what names it, of an award, balance or first
        statement."""
        total = {'legacy': 0, 'legacy_other': 0, 'now': 0, 'now_other': 0, 'statements': 0}
        for seed in SEEDS:
            made = made_of(seed)
            events, by_id = made['events'], {e['id']: e for e in made['events']}
            for key, q in statement_queries(seed).items():
                total['statements'] += 1
                full = big_minutes.legacy_state(events, made['meetings']).get(key)
                old = {i for i, flat in legacy_flats_without(seed, 1).items() if flat.get(key) is None and full is not None}
                new = nothing_of(seed, 1, key)
                own = own_award_closure(events, q['subject'])       # the award and what names it (the legacy reading has no names)
                for label, ids in (('legacy', old), ('now', new)):
                    total[label] += bool(ids)
                    total[label + '_other'] += any(not (q['kind'] in OWN_AWARD_KINDS and (
                        i in own if label == 'now' else by_id[i]['kind'] == 'award' and by_id[i]['subject'] == q['subject']))
                        for i in ids)
        print('statements with a needed event whose removal only makes the value None, seeds 1-5: '
              f'before the subset rule {total["legacy"]} of {total["statements"]} ({total["legacy"] / total["statements"]:.0%}; '
              f'{total["legacy_other"]} other than the own award), after {total["now"]} ({total["now"] / total["statements"]:.0%}; '
              f'{total["now_other"]} other than the own award)')
        self.assertGreater(total['legacy_other'], 0.3 * total['statements'])
        self.assertEqual(total['now_other'], 0)

    def test_relevant_covers_needed_and_no_empty_or_tied_statement(self):
        for seed in SEEDS:
            check_relevant_and_needed(seed)

    def test_no_running_totals(self):
        for seed in SEEDS:
            check_no_running_totals(seed)

    def test_verify_reports_every_check(self):
        for seed in SEEDS[:1]:
            self.assertEqual(verify(seed, 1), 0)
            built = case(seed)
            folder = Path(tempfile.mkdtemp(prefix='big-minutes-verify-', dir=str(ROOT / 'tmp')))
            try:
                for name, text in built['files'].items():
                    target = folder / built['dossier'] / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text, encoding='utf-8')
                self.assertEqual(verify(seed, 1, str(folder)), 0)
                (folder / built['dossier'] / 'report-mixed.md').write_text('changed', encoding='utf-8')
                self.assertEqual(verify(seed, 1, str(folder)), 1)
            finally:
                shutil.rmtree(folder)

    def test_verb_and_name_filters(self):
        for seed in SEEDS:
            print(f'filters, seed {seed}:', check_filters(seed))

    def test_nothing_outside_the_map_decides(self):
        for seed in SEEDS:
            check_nothing_outside_the_map_decides(seed)

    def test_statements_across_subjects_and_over_every_project(self):
        for seed in SEEDS:
            check_across_subjects(seed)

    def test_recorded_dependencies_match_the_words(self):
        for seed in SEEDS:
            print(f'naming, seed {seed}:', check_naming_recorded(seed))

    def test_depends_and_the_reading_of_subsets(self):
        for seed in SEEDS:
            check_dependencies(seed)

    def test_closed_needed_events(self):
        for seed in SEEDS:
            print(f'closed needed events, seed {seed}:', check_closed_needed(seed))

    def test_edition_switch(self):
        self.assertEqual(big_minutes.current_edition(), 1)                    # not set: edition 1
        for raw, want in (('1', 1), ('2', 2), ('3', 3), ('4', 4), ('5', 5), ('6', 6), ('8', 8)):
            with edition_env(raw):
                self.assertEqual(big_minutes.current_edition(), want)
        for raw in ('', '0', '7', '12', '2 ', ' 1', 'one', '1.0', 'two', '34', '03', '-1', '5th', '05', '55'):
            with edition_env(raw):
                with self.assertRaises(generate.GenerationError, msg=repr(raw)):
                    big_minutes.current_edition()
                with self.assertRaises(generate.GenerationError, msg=repr(raw)):
                    generate.build_case('minutes', 1, 1)
        self.assertEqual(big_minutes.EDITIONS, (1, 2, 3, 4, 5, 6, 8))             # the editions that exist: the list is pinned
        default = build_with_facts(1, 1)
        with edition_env(1):
            one = build_with_facts(1, 1)
        self.assertEqual(default['files'], one['files'])
        self.assertEqual({k: v for k, v in default['key']['facts'].items()}, one['key']['facts'])
        self.assertNotIn('size', default['key']['facts'])
        with edition_env(2):
            two = build_with_facts(1, 1)
        self.assertEqual(two['key']['facts']['size'], 2)
        self.assertNotEqual(two['files']['sources/minute-book-1.md'], default['files']['sources/minute-book-1.md'])
        self.assertIn('sources/minute-book-4.md', two['files'])
        self.assertNotIn('sources/minute-book-4.md', default['files'])
        self.assertEqual(default['key']['facts']['interpreter'], two['key']['facts']['interpreter'])
        for edition in (3, 4, 5):
            with edition_env(edition):
                built = build_with_facts(ED_SEED, 1)
            self.assertEqual(built['key']['facts']['size'], edition)
            self.assertEqual(built['key']['facts']['interpreter'], default['key']['facts']['interpreter'])
            self.assertNotIn('sources/minute-book-4.md', built['files'])           # editions 3, 4 and 5 have three minute books
            self.assertEqual(sorted(n for n in built['files'] if n.startswith('sources/')),
                             [f'sources/{n}' for n in ('minute-book-1.md', 'minute-book-2.md', 'minute-book-3.md', 'procedure.md')])

    def test_edition_1_is_unchanged(self):
        """The files, the key's rows and the facts of four draws are those of the module before editions existed."""
        for (seed, index), (files, rows, facts) in EDITION1_PINS.items():
            for setting in (None, 1):
                with edition_env(setting):
                    built = build_with_facts(seed, index)
                self.assertEqual({n: hashlib.sha256(t.encode()).hexdigest() for n, t in built['files'].items()}, files, (seed, index, setting))
                self.assertEqual(canonical_sha(built['key']['expectations']), rows, (seed, index, setting))
                shown = {k: v for k, v in built['key']['facts'].items() if k != 'interpreter_sha256'}
                self.assertEqual(canonical_sha(shown), facts, (seed, index, setting))

    def test_edition_2_shape_mix_and_leads(self):
        with edition_env(2):
            for seed in EDITION2_SEEDS:
                print(f'edition 2, seed {seed}:', check_edition2_shape(seed))
                print(f'edition 2, seed {seed}:', check_edition2_mix(seed))
                print(f'edition 2, seed {seed}:', check_edition2_leads(seed))

    def test_edition_2_passes_every_check(self):
        """Every check of `verify`, and the comparison with fixtures, on a dossier of five minute books."""
        with edition_env(2):
            self.assertEqual(len(volumes_in(sources_of(case(EDITION2_FULL)))), 5)
            folder = write_fixtures(case(EDITION2_FULL), ROOT / 'tmp')
            try:
                self.assertEqual(verify(EDITION2_FULL, 1, str(folder)), 0)
            finally:
                shutil.rmtree(folder)

    def test_verify_refuses_fixtures_of_the_other_edition(self):
        for made_under, asks in ((2, None), (2, 1), (1, 2), (2, 3), (2, 4), (3, 2), (4, 2), (2, 5), (5, 2), (1, 6), (3, 6), (4, 6), (5, 6),
                                  (1, 8), (3, 8), (4, 8), (5, 8)):
            with edition_env(made_under):
                folder = write_fixtures(case(1), ROOT / 'tmp')
            try:
                with edition_env(asks):
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out):
                        failures = verify(1, 1, str(folder))
                self.assertEqual(failures, 1)
                self.assertIn('made under edition 2' if made_under == 2 else 'made under edition 1, 3, 4 or 5', out.getvalue())
                self.assertEqual(out.getvalue().count('\n'), 1, out.getvalue())          # refused before any check
            finally:
                shutil.rmtree(folder)
        for raw in ('7', 'x', '0', ''):                     # editions 1 to 6 and 8 exist; nothing else does
            with edition_env(raw):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(verify(1, 1), 1, raw)
                self.assertIn('must be 1, 2, 3, 4, 5, 6 or 8', out.getvalue())
                self.assertEqual(out.getvalue().count('\n'), 1, out.getvalue())

    def test_a_three_book_folder_is_accepted_by_editions_1_3_4_and_5_and_the_bytes_decide(self):
        made, rebuilt = {}, {}
        for edition in (1, 3, 4, 5):
            with edition_env(edition):
                made[edition] = write_fixtures(case(ED_SEED), ROOT / 'tmp')
        try:
            for made_under, folder in made.items():
                base = folder / case_dossier(ED_SEED, made_under)
                for asks in (1, 3, 4, 5):
                    with edition_env(asks):
                        self.assertIsNone(fixtures_refusal(base, asks), (made_under, asks))
                        if asks not in rebuilt:                       # the build `verify` compares, made afresh once per asking edition
                            rebuilt[asks] = generate.build_case('minutes', ED_SEED, 1)
                        out = io.StringIO()
                        with contextlib.redirect_stdout(out):
                            differing = compare_fixtures(rebuilt[asks], base)
                    self.assertEqual(differing, 0 if asks == made_under else 1, (made_under, asks, out.getvalue()))
                    self.assertEqual(out.getvalue().startswith('ok    fixtures:'), asks == made_under, out.getvalue())
                with edition_env(2):
                    self.assertIn('made under edition 1, 3, 4 or 5', fixtures_refusal(base, 2))
        finally:
            for folder in made.values():
                shutil.rmtree(folder)

    def test_edition_does_not_reach_answer(self):
        """The hand-written lists and `answer` on an edition 2 dossier are the same whatever the setting says."""
        results = []
        for setting in (None, 1, 2, 6):
            with edition_env(setting):
                results.append(check_hand_cases())
        self.assertEqual(len(set(results)), 1)
        with edition_env(2):
            events, queries = made_of(1)['events'], statement_queries(1)
            expected = [big_minutes.answer(q, events, r) for q in queries.values() for r in (None,) + big_minutes.READERS]
            check_answers(1)
        for setting in (None, 1, 6):
            with edition_env(setting):
                self.assertEqual([big_minutes.answer(q, events, r) for q in queries.values() for r in (None,) + big_minutes.READERS], expected)

    def test_editions_3_to_5_are_deterministic_across_processes(self):
        for edition in (3, 4, 5):
            outputs = []
            for hash_seed in ('0', '4242'):
                code = ('import sys; sys.dont_write_bytecode=True; sys.path.insert(0, %r); import generate, generate_big, json; '
                        'generate_big.register()\nwith generate_big.facts_in_keys():\n'
                        '    print(json.dumps(generate.build_case("minutes", %d, 1), sort_keys=True))' % (str(ROOT / 'evalsuite'), ED_SEED))
                with edition_env(edition):
                    run_ = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                                          env=dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONDONTWRITEBYTECODE='1'))
                self.assertEqual(run_.returncode, 0, run_.stderr[-500:])
                outputs.append(run_.stdout)
            self.assertEqual(outputs[0], outputs[1], f'edition {edition} differs between processes')
            with edition_env(edition):
                self.assertEqual(json.loads(outputs[0]), json.loads(json.dumps(build_with_facts(ED_SEED, 1))))

    def test_editions_3_and_4_draw_the_same_minute_books(self):
        """The same seed gives the same minute books and events in editions 3 and 4; only the reports (the mix of statements) differ."""
        with edition_env(3):
            three = build_with_facts(ED_SEED, 1)
        with edition_env(4):
            four = build_with_facts(ED_SEED, 1)
        sources = lambda b: {n: t for n, t in b['files'].items() if n.startswith('sources/')}  # noqa: E731
        self.assertEqual(sources(three), sources(four))
        self.assertEqual(three['key']['facts']['events'], four['key']['facts']['events'])
        self.assertNotEqual(three['files']['report-correct.md'], four['files']['report-correct.md'])
        self.assertNotEqual(three['key']['facts']['size'], four['key']['facts']['size'])

    def test_edition_5_draws_the_world_of_editions_3_and_4(self):
        """Design, section 3: the world and the size of editions 3 and 4: three minute books, the key says size 5, the same file names.
        Whether the same seed gives the very same events and minute books as edition 4 is reported and not asserted: a draw whose
        statements the picker refuses is redrawn, and the redrawn world is another world (the bounds of the world are asserted for
        every dossier by `check_world3`)."""
        with edition_env(4):
            four = facts_built(ED_SEED, 1)
        with edition_env(5):
            five = facts_built(ED_SEED, 1)
        self.assertEqual(sorted(five['files']), sorted(four['files']))
        self.assertEqual(five['key']['facts']['size'], 5)
        self.assertNotEqual(five['files']['sources/procedure.md'], four['files']['sources/procedure.md'])
        self.assertNotEqual(five['files']['report-correct.md'], four['files']['report-correct.md'])
        same = five['key']['facts']['events'] == four['key']['facts']['events']
        print(f'edition 5, seed {ED_SEED}: the events and the minute books are {"those" if same else "not those"} of edition 4')

    def run_verify(self, edition, folder=None):
        with edition_env(edition):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                failures = verify(ED_SEED, 1, folder)
        return failures, out.getvalue()

    def test_edition_3_passes_every_check(self):
        """Every check of `verify` under edition 3, and the comparison with fixtures, on one dossier."""
        with edition_env(3):
            folder = write_fixtures(case(ED_SEED), ROOT / 'tmp')
        try:
            failures, out = self.run_verify(3, str(folder))
        finally:
            shutil.rmtree(folder)
        self.assertEqual(failures, 0, out)
        self.assertEqual(out.count('ok    '), len(CHECKS3) + 1, out)
        print(out.split('ok    depth: ')[1].split('\n')[0])

    def test_edition_4_passes_every_check(self):
        with edition_env(4):
            folder = write_fixtures(case(ED_SEED), ROOT / 'tmp')
        try:
            failures, out = self.run_verify(4, str(folder))
        finally:
            shutil.rmtree(folder)
        self.assertEqual(failures, 0, out)
        self.assertEqual(out.count('ok    '), len(CHECKS3) + 1, out)
        print(out.split('ok    depth: ')[1].split('\n')[0])

    def test_edition_5_passes_every_check(self):
        """Every check of `verify` under edition 5, and the comparison with fixtures, on one dossier."""
        with edition_env(5):
            folder = write_fixtures(case(ED_SEED), ROOT / 'tmp')
        try:
            failures, out = self.run_verify(5, str(folder))
        finally:
            shutil.rmtree(folder)
        self.assertEqual(failures, 0, out)
        self.assertEqual(out.count('ok    '), len(CHECKS5) + 1, out)
        print(out.split('ok    depth: ')[1].split('\n')[0])

    def test_edition_6_draws_a_dense_world(self):
        """One edition-6 dossier only (a build takes seconds; `--verify` under BIG_MINUTES_EDITION=6 runs every check of the edition on
        a dossier, and is how several seeds are checked): its world (44 to 48 meetings, 36 to 40 projects, four or five minute books,
        430,000 to 510,000 characters), many payments to a project, whole positive amounts that keep every close within the award (the
        interpreter reports no problem), numbered instalments up to the fifth only, the table of the edition per report, and the key's
        size. The full set of checks is not run here: it takes minutes on a dossier of this size."""
        with edition_env(6):
            built = facts_built(ED_SEED, 1)
        facts = built['key']['facts']
        self.assertEqual(facts['size'], 6)
        events = facts['events']
        sources = sources_of(built)
        books = sorted(n for n in sources if n.startswith('minute-book-'))
        dates_with_events = len({e['date'] for e in events})          # the dates the log has an event at: a meeting may hold none (seed 1: 43 of 44)
        self.assertIn(len(books), (4, 5))
        self.assertEqual(sorted(sources), sorted(['procedure.md'] + books))
        self.assertTrue(430000 <= sum(len(t) for t in sources.values()) <= 510000)
        self.assertTrue(36 <= len(facts['names']) <= 40, len(facts['names']))
        self.assertTrue(43 <= dates_with_events <= 48, dates_with_events)
        pays = [e for e in events if e['kind'] == 'pay']
        per_project = collections.Counter(e['subject'] for e in pays)
        self.assertTrue(400 <= len(pays) <= 800, len(pays))
        self.assertTrue(all(6 <= n <= 20 for n in per_project.values()), sorted(per_project.values()))
        self.assertGreaterEqual(sum(n >= 14 for n in per_project.values()), len(per_project) // 2)
        flat, problems, resolved = big_minutes._run(events)
        self.assertEqual(problems, [])
        self.assertTrue(all(isinstance(v, int) and v > 0 for v in resolved.values()))
        self.assertTrue(all(e['spec'].get('k', 2) <= 5 for e in pays))
        # the table of the edition, per report: the one-project statements merge into `single`
        for variant in ('correct', 'mixed'):
            kinds = collections.Counter(r['claim_kind'][len('minutes_'):] for r in key_rows(built, variant))
            singles = sum(kinds.pop(k, 0) for k in SINGLES)
            self.assertEqual({**kinds, 'single': singles}, {k: v for k, v in EXPECTED_QUOTAS[6].items() if v}, variant)

    def test_edition_6_is_read_back_from_its_text(self):
        """The one dense dossier the unit suite builds (the cache of `facts_built`: no second build), read once by this module's own
        parser, is held to the rules of the edition: no unreadable sentence, every statement of both reports and every flawed reader's
        verdict recomputed from the text (false sums included), the quota table and the rules of choice written out in this module, every
        reader wrong on four statements, 8 to 12 altered statements, the world and its density, the amounts, every event told once with
        the values of the log, this evaluator against the interpreter at every close, the text rules and the procedure of edition 5."""
        with edition_env(6):
            built, book, runs, readers = dense_view(ED_SEED)
            sources, count = sources_of(built), len(book.meetings)
            self.assertEqual(book.errors, [])
            self.assertTrue(3 <= len(readers) <= 4 and set(readers) <= set(READERS + ('full_name_only',)), readers)
            print('edition 6, round trip:', round_trip3(built, book, runs, readers))
            both = statements_in(built, book)
            quota_rules(6, count, both, {v: key_rows(built, v) for v in both})
            uses = choice_rules(6, count, runs, book.projects, both, book)
            print(f'edition 6, rules of choice: named sums over {len(uses)} projects, each used {min(uses.values())} to {max(uses.values())} times')
            print('edition 6, readers:', readers_wrong(both, book.projects, runs, readers))
            altered = sum(a != b for a, b in zip(report_statements(built, 'correct'), report_statements(built, 'mixed')))
            self.assertTrue(ALTERED[0] <= altered <= ALTERED[1], altered)
            for step in (world6(built, book, sources), dense_rules(book, runs), stated_values_positive_whole(both), events_told(built, book), whole_base_agrees(built, book, runs),
                         text_rules5(sources, book), no_running_totals(sources), procedure6_is_that_of_5(sources['procedure.md'])):
                print('edition 6:', step)

    def test_the_rules_of_edition_6_see_a_broken_world(self):
        """Negative controls of the new checks, on copies of the dense dossier's facts (the text is not read again): each planted defect
        must be refused, the undamaged dossier not."""
        with edition_env(6):
            built, book, runs, readers = dense_view(ED_SEED)
        sources, count = sources_of(built), len(book.meetings)

        def doctored(change):
            facts = [dict(f, spec=dict(f['spec'])) if 'spec' in f else dict(f) for f in book.facts]
            change(facts, [f for f in facts if f['t'] == 'pay'])
            changed = copy.copy(book)
            changed.facts = facts
            return changed, runs_of(changed)

        def later(facts, kind='pay'):
            """The payments of the doctored facts of each paid project, in the order of the minutes."""
            out = collections.defaultdict(list)
            for f in sorted((f for f in facts if f['t'] == 'pay'), key=lambda f: (f['m'], f['order'])):
                out[f['p']].append(f)
            return out
        dense_rules(book, runs)                                                       # undamaged: passes

        def refused(message, change):
            changed, changed_runs = doctored(change)
            with self.assertRaisesRegex(Failed, message):
                dense_rules(changed, changed_runs)

        def too_many(facts, pays):                                  # a 21st payment to a project
            items = later(facts, 'pay')
            last = max(items.values(), key=len)[-1]
            facts.append(dict(last, spec=dict(last['spec']), order=(last['order'][0], last['order'][1] + 1)))
            facts.append(dict(last, spec=dict(last['spec']), order=(last['order'][0], last['order'][1] + 2)))
        refused(r'\d+ payments to ', too_many)

        def pay_of(facts, kind, no_balance=False):
            """A payment of `kind`, not the first of its project (of a project whose award is not paid off by a balance payment if asked)."""
            return next(f for items in later(facts, 'pay').values() for f in items[1:] if f['spec']['type'] == kind
                        and not (no_balance and any(g['spec']['type'] == 'balance' for g in items)))
        refused('not a whole number of pounds, or is under 1,000', lambda facts, pays: pay_of(facts, 'abs')['spec'].update(amount=999))
        refused('not a whole number of pounds, or is under 1,000',
                lambda facts, pays: pay_of(facts, 'frac_award')['spec'].update(frac=fractions.Fraction(7001, 7003)))
        refused('more paid to', lambda facts, pays: pay_of(facts, 'abs', True)['spec'].update(amount=10 ** 6))
        refused('holds no award at|is not established', lambda facts, pays: pay_of(facts, 'first')['spec'].update(type='ordinal', k=5))   # an amount that is not established

        def ordinal_twice(facts, pays):                             # an ordinal payment, and a later one of the project stated the same way
            for items in later(facts, 'pay').values():
                ordinals = [i for i, f in enumerate(items) if f['spec']['type'] == 'ordinal']
                firsts = [i for i, f in enumerate(items) if f['spec']['type'] == 'first' and ordinals and i > ordinals[0]]
                if firsts:
                    items[firsts[0]]['spec'].clear()
                    items[firsts[0]]['spec'].update(items[ordinals[0]]['spec'])
                    return
            raise AssertionError('no project with an ordinal payment and a later payment stated as the first instalment')
        refused('in the same way twice', ordinal_twice)

        def irregular(facts, pays):                                 # a first instalment of 1,000 against an award of tens of thousands
            next(items for items in later(facts, 'pay').values())[0]['spec'].update(amount=1000)
        refused('is not regular', irregular)

        def corrected_early(facts, pays):                           # a correction of the second payment
            fix = next(f for f in facts if f['t'] == 'correct' and f['target'] == 'pay')
            fix['when'] = later(facts, 'pay')[fix['p']][1]['m']
        refused('only a payment from the sixth on is corrected', corrected_early)

        def no_first(facts, pays):                                  # every "first instalment" stated in pounds instead
            firsts = {p: items[0]['spec']['amount'] for p, items in later(facts, 'pay').items()}
            for f in pays:
                if f['spec']['type'] == 'first':
                    f['spec'] = {'type': 'abs', 'amount': firsts[f['p']]}
        refused('is not the commonest way', no_first)

        def balance_early(facts, pays):                             # the balance of the award as the second payment of a project
            for items in later(facts, 'pay').values():
                if items[-1]['spec']['type'] == 'balance':
                    items[1]['spec'].clear()
                    items[1]['spec'].update(type='balance')
                    return
        refused('is not the last payment of', balance_early)
        # a project that is neither paid nor rescinded
        paid = next(iter(payments_by_project(book)))
        changed, changed_runs = doctored(lambda facts, pays: facts.__setitem__(slice(None), [f for f in facts if not (f['t'] == 'pay' and f['p'] == paid)]))
        with self.assertRaisesRegex(Failed, 'has no payment and no rescission'):
            dense_rules(changed, changed_runs)
        # a first payment that is not stated in pounds
        refused('not in pounds', lambda facts, pays: later(facts)[next(iter(later(facts)))][0].__setitem__('spec', {'type': 'first'}))

        def first_of_before(facts, pays):                           # a payment that repeats the first instalment of a project paid first later
            items = later(facts)
            f = min((f for fs in items.values() for f in fs if f['spec']['type'] == 'first_of'), key=lambda f: f['m'])
            f['spec']['other'] = next(p for p, fs in items.items() if p != f['p'] and fs[0]['m'] > f['m'])
        refused('paid at a meeting not before', first_of_before)

        def small_award(facts, pays):                               # an award under 1,000 (of a rescinded project: nothing is paid on it)
            rescinded = next(f['p'] for f in facts if f['t'] == 'rescind')
            next(f for f in facts if f['t'] == 'award' and f['p'] == rescinded)['amount'] = 900
        refused('a stated amount under 1,000', small_award)

        def few(facts, pays):                                       # eight payments where the calendar allows sixteen or more
            items = later(facts)
            gone = {id(f) for f in next(fs for fs in items.values() if len(fs) >= 16)[8:]}
            facts[:] = [f for f in facts if id(f) not in gone]
        refused('the calendar allows', few)

        def rescinded_late(facts, pays):                            # a paid project whose award does not stand at every close from its first payment
            facts.append({'t': 'rescind', 'm': count - 2, 'p': next(iter(later(facts))), 'key': (0, 0), 'order': ((0, 0), 0), 'full': True})
        refused('holds no award at', rescinded_late)
        # the calendar the headings give
        events = built['key']['facts']['events']
        calendar_of(book, events)
        for what, damage, message in (('no day', lambda b: setattr(b, 'meetings', [(y, m, None) for y, m, _ in b.meetings]), 'heading without a day'),
                                      ('order', lambda b: setattr(b, 'meetings', list(reversed(b.meetings))), 'not in the order of the calendar')):
            damaged = copy.copy(book)
            damage(damaged)
            with self.assertRaisesRegex(Failed, message):
                calendar_of(damaged, events)
        with self.assertRaisesRegex(Failed, 'no heading'):
            calendar_of(book, events + [dict(events[0], date='1999-01-01')])
        # two awards at one of the first fourteen meetings
        early_awards_once(book)
        early = next(f for f in book.facts if f['t'] == 'award' and f['m'] < EARLY_AWARD_MEETINGS)
        two = copy.copy(book)
        two.facts = book.facts + [dict(early, p=next(p for p in book.projects if p != early['p']))]
        with self.assertRaisesRegex(Failed, 'two awards at meeting'):
            early_awards_once(two)
        # no sentence is said twice
        said = copy.copy(book)
        said.facts = book.facts + [dict(next(f for f in book.facts if f['t'] == 'pay'))]
        with self.assertRaisesRegex(Failed, 'said twice'):
            sentences_once(said)
        self.assertGreater(sentences_once(book), 600)
        # a stated value that is not a positive whole number, true or false
        both = statements_in(built, book)
        stated_values_positive_whole(both)
        for change in (dict(value=0), dict(value=fractions.Fraction(3, 2)), dict(value=-5)):
            zero = copy.deepcopy(both)
            next(st for st in zero['mixed'] if st['shape'] == 'single' and st['kind'] != 'first').update(change)
            with self.assertRaisesRegex(Failed, 'not a positive whole number'):
                stated_values_positive_whole(zero)
        # every event told once, with the values of the log
        events_told(built, book)
        gone = copy.copy(book)
        gone.facts = [f for f in book.facts if f is not next(g for g in book.facts if g['t'] == 'pay')]
        with self.assertRaisesRegex(Failed, 'tell different events'):
            events_told(built, gone)
        changed, _ = doctored(lambda facts, pays: pay_of(facts, 'abs')['spec'].update(amount=1500))
        with self.assertRaisesRegex(Failed, 'does not carry the values of the log'):
            events_told(built, changed)
        # this evaluator and the interpreter agree at every close
        whole_base_agrees(built, book, runs)
        changed, changed_runs = doctored(lambda facts, pays: pay_of(facts, 'abs')['spec'].update(amount=pay_of(facts, 'abs')['spec']['amount'] + 1000))
        with self.assertRaisesRegex(Failed, 'by the interpreter and'):
            whole_base_agrees(built, changed, changed_runs)
        # the world and its density
        world6(built, book, sources)
        with self.assertRaisesRegex(Failed, 'sources'):
            world6(built, book, {n: t for n, t in sources.items() if n != 'minute-book-2.md'})
        thin = copy.copy(book)
        thin.facts = [f for f in book.facts if not (f['t'] == 'pay' and f['m'] % 3 == 0)]
        with self.assertRaisesRegex(Failed, 'payments in all|have 14 or more'):
            world6(built, thin, sources)
        sparse = copy.copy(book)
        sparse.facts = [f for f in book.facts if not (f['t'] == 'pay' and f['order'][1] % 2)]
        with self.assertRaisesRegex(Failed, 'payments in all|have 14 or more'):
            world6(built, sparse, sources)
        dear = copy.copy(book)
        busiest = max(payments_by_project(book).items(), key=lambda item: len(item[1]))[0]
        dear.facts = book.facts + [dict(f) for f in book.facts if f['t'] == 'pay' and f['p'] == busiest]
        with self.assertRaisesRegex(Failed, r'a project with \d\d payments'):
            world6(built, dear, sources)
        twice = copy.copy(book)
        twice.facts = book.facts + [next(f for f in book.facts if f['t'] == 'intro')]
        with self.assertRaisesRegex(Failed, 'used twice'):
            world6(built, twice, sources)
        with self.assertRaisesRegex(Failed, 'names other volumes'):
            world6(dict(built, files=dict(built['files'], **{'report-mixed.md': built['files']['report-mixed.md'].replace('volumes 1 to', 'volumes 2 to')})), book, sources)
        # a reader that is no worse than the truth is wrong on no statement
        both = statements_in(built, book)
        readers_wrong(both, book.projects, runs, readers)
        with self.assertRaisesRegex(Failed, 'is wrong on only 0 statements'):
            readers_wrong(both, book.projects, dict(runs, **{readers[0]: runs['truth']}), readers)
        # the procedure is that of edition 5
        procedure6_is_that_of_5(sources['procedure.md'])
        with self.assertRaisesRegex(Failed, 'is not that of edition 5'):
            procedure6_is_that_of_5(sources['procedure.md'] + 'An added sentence.\n')
        # the same bytes in another process
        same_in_second_process(built, json.loads(json.dumps(built)))
        with self.assertRaisesRegex(Failed, 'is not the one built here'):
            same_in_second_process(built, dict(json.loads(json.dumps(built)), dossier='another'))
        # a running total in a minute book
        with self.assertRaisesRegex(Failed, 'a running total'):
            no_running_totals(dict(sources, **{'minute-book-1.md': sources['minute-book-1.md'] + '\nThe payments to date came to a great deal.\n'}))

    def test_the_rules_of_choice_and_quota_see_a_broken_dense_dossier(self):
        """Negative controls of the quota table and the rules of choice of edition 6, on doctored statement lists of the dense dossier."""
        with edition_env(6):
            built, book, runs, readers = dense_view(ED_SEED)
        both, count, projects, truth = statements_in(built, book), len(book.meetings), book.projects, runs['truth']
        rows = {v: key_rows(built, v) for v in both}
        choice_rules(6, count, runs, projects, both, book)
        quota_rules(6, count, both, rows)

        def doctored(rung, edit, which=None):
            out = copy.deepcopy(both)
            for variant in out:
                items = [st for st in out[variant] if rung_of(st, count) == rung]
                for number, st in enumerate(items):
                    if which is None or number in which:
                        edit(st, number, items)
            return out
        # the eight upto sums are at eight different meetings: two of them (of different figures, so that they are not the same statement)
        # at one meeting, where the sum is still a good value
        upto = [st for st in both['correct'] if rung_of(st, count) == 'upto']
        self.assertEqual(len({st['until'] for st in upto}), 8)
        other = next(i for i, st in enumerate(upto) if st['figure'] != upto[0]['figure'] and good_figure(
            st['figure'], sum_of(truth, projects, dict(st, until=upto[0]['until']))))
        with self.assertRaisesRegex(Failed, 'do not use eight different meetings'):
            choice_rules(6, count, runs, projects, doctored('upto', lambda st, n, items: st.update(until=items[0]['until']), {other}), book)
        with self.assertRaisesRegex(Failed, 'the same statement'):               # the same two with one figure
            choice_rules(6, count, runs, projects, doctored('upto', lambda st, n, items: st.update(
                {k: v for k, v in items[0].items() if k not in ('value', 'value_span', 'text')}) if n == other else None), book)
        with self.assertRaisesRegex(Failed, 'an upto sum at meeting'):
            choice_rules(6, count, runs, projects, doctored('upto', lambda st, n, items: st.update(until=3 * count // 5 - 1), {0}), book)
        # the figures of a rung: only totals where half should be numbers of payments
        for rung in ('sum8', 'upto'):
            with self.assertRaisesRegex(Failed, 'has \\{'):
                choice_rules(6, count, runs, projects, doctored(rung, lambda st, n, items: st.update(figure='total')), book)
        # the rungs that are not asked: the number of projects holding an award, a span of three meetings, a sum of awards up to a meeting
        extra = {'shape': 'single', 'kind': 'projects', 'who': None, 'until': count // 3, 'value': 5, 'text': 'After a meeting, 5 projects held an award in force.'}
        with_projects = {v: sts + [extra] for v, sts in both.items()}
        with self.assertRaisesRegex(Failed, 'the text shows'):
            quota_rules(6, count, with_projects, rows)
        with self.assertRaisesRegex(Failed, 'projects statements are not one in the first and one in the last third'):
            choice_rules(6, count, runs, projects, with_projects, book)
        with self.assertRaisesRegex(Failed, 'the text shows'):
            quota_rules(6, count, doctored('span6', lambda st, n, items: st.update(since=st['until'] - 3), {0}), rows)
        with self.assertRaisesRegex(Failed, 'the text shows'):
            quota_rules(6, count, doctored('upto', lambda st, n, items: st.update(figure='award'), {0}), rows)
        # a statement too few; a claim kind that is not the one the text shows
        lacking = copy.deepcopy(both)
        lacking['correct'].pop()
        with self.assertRaisesRegex(Failed, 'the text shows'):
            quota_rules(6, count, lacking, rows)
        with self.assertRaisesRegex(Failed, 'the claim kinds are not those'):
            quota_rules(6, count, both, {v: [dict(r, claim_kind='minutes_upto') if r['claim_kind'] == 'minutes_book' else r for r in rows[v]] for v in rows})
        # a named sum over eight projects of two payments each, at a meeting outside the last third
        for early in range((2 * count - 1) // 3, 0, -1):
            pool = [n for n in projects if truth[('count', n, early)] >= 2]
            if len(pool) >= 8:
                break
        self.assertLess(3 * early, 2 * count)
        with self.assertRaisesRegex(Failed, 'not in the last third'):
            choice_rules(6, count, runs, projects, doctored('sum8', lambda st, n, items: st.update(until=early, names=pool[:8]), {0}), book)
        # a sum that is not a good value, and a stated value that is not positive
        with self.assertRaisesRegex(Failed, 'states a value that is not positive'):
            choice_rules(6, count, runs, projects, doctored('book', lambda st, n, items: st.update(value=0), {0}), book)

    def test_edition_6_is_the_same_bytes_in_another_process(self):
        """One process only (the check of `--verify` runs two): the dense dossier with its facts, built by a new process with another
        string-hash seed, is the one built here (the unit suite builds it once; this builds the same draw a second time)."""
        with edition_env(6):
            built = facts_built(ED_SEED)
        same_in_second_process(built, build_in_another_process(6, ED_SEED, 1, '4242'))

    def test_editions_2_to_5_are_as_they_were_at_commit_5d1ca73d(self):
        """The pins of editions 2 to 5 (files, key rows and key facts of one draw each, from the module of commit 5d1ca73d), as
        `test_edition_1_is_unchanged` pins edition 1: edition 6 must not have changed them. The pins see a changed file, row or fact."""
        for (edition, seed, index), pin in EDITION_PINS.items():
            with edition_env(edition):
                built = facts_built(seed, index)
            self.assertEqual(pin_differences(built, pin), [], (edition, seed, index))
        with edition_env(5):
            five = facts_built(ED_SEED)
        pin = EDITION_PINS[(5, 1, 1)]
        damaged = dict(five, files=dict(five['files'], **{'report-mixed.md': five['files']['report-mixed.md'] + ' '}))
        self.assertEqual(pin_differences(damaged, pin), ['report-mixed.md'])
        damaged = dict(five, key=dict(five['key'], expectations=five['key']['expectations'][:-1]))
        self.assertEqual(pin_differences(damaged, pin), ['the key\'s rows'])
        damaged = dict(five, key=dict(five['key'], facts=dict(five['key']['facts'], size=7)))
        self.assertEqual(pin_differences(damaged, pin), ['the key\'s facts'])
        self.assertEqual(sorted(EDITION_PINS), [(2, 8, 1), (3, 1, 1), (4, 1, 1), (5, 1, 1)])

    def test_the_check_lists_of_editions_5_and_6(self):
        """Seed-free: edition 6's list is edition 5's (32 checks, unchanged) and the new checks of the dense world; nothing else is added to
        edition 5's list."""
        self.assertEqual(len(CHECKS5), 32)
        self.assertIs(checks_of(5), CHECKS5)
        self.assertIs(checks_of(6), CHECKS6)
        self.assertEqual(CHECKS6[:len(CHECKS5)], CHECKS5)
        self.assertEqual([c for _, c in CHECKS6[len(CHECKS5):]], [check_dense_rules, check_events_told, check_whole_base, check_procedure6, check_second_process6])
        self.assertEqual(len({name for name, _ in CHECKS6}), len(CHECKS6))
        for edition in (3, 4):
            self.assertIs(checks_of(edition), CHECKS3)

    def test_the_scratch_folder_is_made_with_its_parent(self):
        """`check_pipeline` and the tests that write fixtures work in a clone or worktree that has no `tmp` folder."""
        with tempfile.TemporaryDirectory() as base:
            parent = Path(base) / 'missing' / 'tmp'
            self.assertFalse(parent.exists())
            folder = scratch_folder('probe-', parent)
            self.assertTrue(folder.is_dir() and folder.parent == parent)
            self.assertEqual(write_fixtures({'dossier': 'd', 'files': {'a.md': 'x'}}, Path(base) / 'again' / 'tmp').name[:len('big-minutes-edition-')],
                             'big-minutes-edition-')
            # and the real pipeline check, in a tree with no `tmp` folder (an edition 5 dossier: the cheapest the check takes)
            import unittest.mock as mock
            tree = Path(base) / 'clone'
            tree.mkdir()
            with mock.patch.object(sys.modules[__name__], 'ROOT', tree), edition_env(5):
                self.assertFalse((tree / 'tmp').exists())
                self.assertIn('read_keys accepts 48 rows', check_pipeline(ED_SEED))
            self.assertEqual([p.name for p in (tree / 'tmp').iterdir()], [])         # the check made `tmp` and removed its own folder

    def test_fixtures_of_edition_6_have_four_or_five_minute_books(self):
        """`fixtures_refusal` for edition 6 and the shape of a folder (no dossier is built): a folder with a fourth minute book is not
        refused by editions 2 and 6 and is by the others; one without is refused by editions 2 and 6 and not by the others."""
        with tempfile.TemporaryDirectory() as base:
            three, four = Path(base) / 'three', Path(base) / 'four'
            for folder in (three, four):
                (folder / 'sources').mkdir(parents=True)
            (four / 'sources' / 'minute-book-4.md').write_text('x', encoding='utf-8')
            for edition in (1, 3, 4, 5):
                with edition_env(edition):
                    self.assertIsNone(fixtures_refusal(three, edition), edition)
                    self.assertIn('made under edition 2 (it has minute-book-4.md; edition 6 has four or five minute books too)', fixtures_refusal(four, edition))
            for edition in (2, 6):
                with edition_env(edition):
                    self.assertIsNone(fixtures_refusal(four, edition), edition)
                    self.assertIn('made under edition 1, 3, 4 or 5', fixtures_refusal(three, edition))

    def test_a_rejected_paying_plan_leaves_no_first_instalment_behind(self):
        """Regression test of `draw_log` under edition 6 (no dossier is built: one log is drawn). A plan of a project that pays is
        rejected by the interpreter, and the project is then drawn again as one that pays nothing (a rescinded one): the first payment
        the rejected plan recorded for other projects to repeat must be gone, or a later project states a payment through the first
        instalment of a project that has none. The rejection and the second plan are forced as the fixer's scratch script did."""
        import unittest.mock as mock
        rng = random.Random(5)
        dates, projects = big_minutes.draw_calendar(rng, 6), big_minutes.draw_projects(rng, 6)
        real_plan, real_run = big_minutes.plan_project6, big_minutes._run
        seen = {'entry': [], 'runs': 0, 'calls0': 0}

        def plan(rng_, p, projects_, dates_, events, busy, firsts):
            seen['entry'].append((p, 0 in firsts))                 # does the first-payment map still name project 0?
            if p == 0:
                seen['calls0'] += 1
                if seen['calls0'] == 2:                            # the second plan of project 0 pays nothing (it is rescinded)
                    for _ in range(500):
                        out = real_plan(rng_, p, projects_, dates_, events, busy, dict(firsts))
                        if out is not None and not any(e['kind'] == 'pay' for e in out):
                            return out
                    raise AssertionError('no plan without a payment in 500 draws')
            return real_plan(rng_, p, projects_, dates_, events, busy, firsts)

        def run(candidate, *args, **kwargs):
            seen['runs'] += 1
            return (None, ['forced rejection']) if seen['runs'] == 1 else real_run(candidate, *args, **kwargs)
        with mock.patch.object(big_minutes, 'plan_project6', plan), mock.patch.object(big_minutes, '_run', run):
            events = big_minutes.draw_log(rng, projects, dates, 6)
        self.assertIsNotNone(events)
        self.assertGreaterEqual(seen['calls0'], 2)
        self.assertEqual([p for p, named in seen['entry'] if named], [], 'a plan was drawn while the map held a first payment of project 0')
        paid = {e['subject'] for e in events if e['kind'] == 'pay'}
        self.assertNotIn(projects[0]['full'], paid)
        through = [e['spec']['other'] for e in events if e['kind'] == 'pay' and e['spec']['type'] == 'first_of']
        self.assertTrue(through, 'no payment is stated through another project\'s first instalment: the test sees nothing')
        self.assertLessEqual(set(through), paid)                    # every first instalment repeated is one that was paid

    def test_the_generator_enforces_the_payments_of_edition_6(self):
        """The generator hands out only an edition-6 dossier with 450 to 700 payments and no sentence said twice (`PAYMENTS6`, and the
        flag `ctx['repeated']` that `sentence` sets when its 60 redraws all repeat; both tested by `attempt` after its last use of the
        random source). `world6`'s two numbers are `PAYMENTS6`, compared with literals written here; an attempt under a range no world
        meets, and an attempt in which a sentence gives up, hand out nothing, and the same attempt under the real range hands out the
        cached dossier (two further attempts on the draw of seed 1 are built here: about two seconds each)."""
        import unittest.mock as mock
        self.assertEqual(big_minutes.PAYMENTS6, (450, 700))
        self.assertEqual(PAYMENT_BOUNDS, (450, 700))
        self.assertEqual(PAYMENT_BOUNDS, tuple(big_minutes.PAYMENTS6))
        with edition_env(6):
            built, book, runs, readers = dense_view(ED_SEED)
            events = built['key']['facts']['events']
            pays = sum(e['kind'] == 'pay' for e in events)
            self.assertTrue(big_minutes.PAYMENTS6[0] <= pays <= big_minutes.PAYMENTS6[1], pays)
            stream = lambda: random.Random(f'evalsuite:{generate.GENERATOR_VERSION}:{ED_SEED}:1:minutes')    # noqa: E731
            real = big_minutes.attempt(stream(), 6)                      # the first attempt of the draw the cached dossier comes from
            self.assertIsNotNone(real, 'the first attempt of seed 1 is refused: pick another draw for this test')
            self.assertEqual(sum(e['kind'] == 'pay' for e in real['events']), pays)
            with mock.patch.object(big_minutes, 'PAYMENTS6', (10 ** 6, 10 ** 6 + 1)):
                self.assertIsNone(big_minutes.attempt(stream(), 6))
            real_sentence = big_minutes.sentence

            def gives_up(rng, e, ctx, anaphor=None):
                text = real_sentence(rng, e, ctx, anaphor)
                ctx['repeated'] = ctx['repeated'] or ctx['edition'] == 6 and len(ctx['said']) > 100      # as if its 60 redraws had all repeated
                return text
            with mock.patch.object(big_minutes, 'sentence', gives_up):
                self.assertIsNone(big_minutes.attempt(stream(), 6))
        # the flag itself, with no dossier: `sentence` sets it when every redraw says what the dossier has said
        ctx = {'edition': 6, 'said': set(), 'last_form': None, 'repeated': False}
        with mock.patch.object(big_minutes, 'sentence_once', lambda rng, e, ctx, anaphor=None: 'The same sentence.'):
            self.assertEqual(big_minutes.sentence(random.Random(1), {}, ctx), 'The same sentence.')
            self.assertFalse(ctx['repeated'])                            # said for the first time
            self.assertEqual(big_minutes.sentence(random.Random(1), {}, ctx, 'the matter above'), 'The same sentence.')
            self.assertTrue(ctx['repeated'])                             # said again, and no redraw differs: the dossier is refused
        ctx = {'edition': 6, 'said': set(), 'last_form': None, 'repeated': False}
        texts = iter(f'Sentence {n}.' for n in range(1000))
        with mock.patch.object(big_minutes, 'sentence_once', lambda rng, e, ctx, anaphor=None: next(texts)):
            for _ in range(5):
                big_minutes.sentence(random.Random(1), {}, ctx)
        self.assertFalse(ctx['repeated'])                                # redraws that differ never set it
        # world6's numbers are the generator's: 449 and 701 payments are refused, 450 and 700 are not refused for their number
        with edition_env(6):
            for total, refused in ((449, True), (450, False), (700, False), (701, True)):
                doctored = copy.copy(book)
                have = [f for f in book.facts if f['t'] == 'pay']
                rest = [f for f in book.facts if f['t'] != 'pay']
                doctored.facts = rest + [dict(have[i % len(have)], order=(have[i % len(have)]['order'][0], i)) for i in range(total)]
                try:
                    world6(built, doctored, sources_of(built))
                    message = ''
                except Failed as error:
                    message = str(error)
                self.assertEqual('payments in all' in message, refused, (total, message))

    def test_the_inventory_tables_are_those_of_the_supplement(self):
        self.assertEqual({e: dict(t) for e, t in INV['sum_quotas'].items()}, EXPECTED_QUOTAS)
        self.assertEqual(sorted(EXPECTED_QUOTAS), [3, 4, 5, 6])
        for table in EXPECTED_QUOTAS.values():
            self.assertEqual(sum(table.values()), 24)
        self.assertEqual(sorted(INV['sum_statements']), ['all_award', 'all_balance', 'all_count', 'all_total', 'span_count', 'span_total',
                                                         'sum_award', 'sum_balance', 'sum_count', 'sum_total'])
        self.assertEqual(INV['span_verbs'], ('stood higher', 'rose by', 'grew by'))
        self.assertEqual(INV['ordinal_cap'], 12)
        for key in ('sum_statements', 'sum_list', 'sum_kinds', 'sum_figures', 'sum_quotas', 'single_kinds', 'span_verbs',
                    'rescission_wordings', 'procedure_sums', 'procedure_rescissions', 'ordinal_cap', 'award_figures', 'award_rungs',
                    'balance_wordings', 'procedure_sums5', 'procedure_instalment'):
            self.assertIn(key, INV)
        self.assertEqual(tuple(INV['award_figures']), AWARD_FIGURES)
        self.assertEqual(tuple(INV['award_rungs']), AWARD_RUNGS)
        self.assertEqual(tuple(INV['balance_wordings']), BALANCE_WORDINGS)
        self.assertEqual(sorted(INV['statements']), ['award', 'balance', 'count', 'first', 'largest', 'projects', 'total'])  # no sum kind in it
        # the rungs of edition 5 are sum kinds (so `answer` reads them as sums) and the figures of editions 3 and 4 still drive their draws
        for rung, shape in (('asum8', 'named'), ('aupto', 'all'), ('abook', 'all')):
            self.assertEqual(INV['sum_kinds'][rung]['shape'], shape, rung)
        self.assertEqual(INV['sum_kinds']['asum8']['size'], 8)
        self.assertEqual(tuple(INV['sum_figures'][:2]), ('total', 'count'))
        for rung in AWARD_RUNGS:
            self.assertEqual(big_minutes.value_form({'kind': rung}), 'integer')

    def test_hand_written_sums(self):
        print('hand-written sums:', check_hand_sums())

    def test_the_round_trip_sees_a_corrupted_dossier(self):
        """Negative controls of this module's evaluator: damage a payment a sum rests on, or the stated value of a false sum, in
        memory, and the round trip must report it (a check that cannot fail proves nothing)."""
        with edition_env(3):
            built, (book, runs) = case(ED_SEED), read(ED_SEED)
            readers = readers_in(ED_SEED)
            round_trip3(built, book, runs, readers)                                 # undamaged: passes
            changed, text = book_with_one_payment_changed(ED_SEED)
            with self.assertRaisesRegex(Failed, r'(correct|mixed): (verdict|reader \w+) differs for'):
                round_trip3(built, changed, runs_of(changed), readers)
            nudged = built_with_false_sum_nudged(ED_SEED)
            with self.assertRaisesRegex(Failed, r'false sum .* states \d+, but reader \w+ gets \d+'):
                round_trip3(nudged, book, runs, readers)
            # the key's true value of a sum that does not match the sum read from the text
            wrong_key = copy_built(case(ED_SEED))
            row = next(r for r in wrong_key['key']['expectations'] if r['variant'] == 'mixed' and r['claim_kind'].startswith('minutes_sum')
                       and r['semantic_state'] == 'refuted')
            row['derivation'] = dict(row['derivation'], feasible_min=row['derivation']['feasible_min'] + 1)
            with self.assertRaisesRegex(Failed, 'the key.s true value'):
                round_trip3(wrong_key, book, runs, readers)
            # the damage is to copies: the cached dossier still passes
            round_trip3(case(ED_SEED), read(ED_SEED)[0], read(ED_SEED)[1], readers)

    def test_the_rules_of_choice_quota_and_text_see_a_broken_draw(self):
        """Negative controls of the rules themselves: each doctored statement list or text must be refused, the undamaged one not."""
        for edition in (3, 4):
            with edition_env(edition):
                built, (book, runs) = case(ED_SEED), read(ED_SEED)
                both, count, projects = statements_of(ED_SEED), len(book.meetings), book.projects
                rows = {v: key_rows(built, v) for v in both}
                choice_rules(edition, count, runs, projects, both)
                quota_rules(edition, count, both, rows)

                def doctored(rung, edit, which=None):
                    out = copy.deepcopy(both)
                    for variant in out:
                        items = [st for st in out[variant] if rung_of(st, count) == rung]
                        for number, st in enumerate(items):
                            if which is None or number in which:
                                edit(st, number, items)
                    return out
                # two spans of one length that overlap, in both reports
                # (built so that both spans hold enough payments and are good values: only the overlap can be refused)
                first = next(st for st in both['correct'] if rung_of(st, count) == 'span3')
                second = [st for st in both['correct'] if rung_of(st, count) == 'span3'][1]
                taken = {first['since']}

                def holds(since):
                    probe = dict(second, since=since, until=since + 3)
                    return (0 <= since and since + 3 <= count - 1 and since not in taken
                            and sum_of(runs['truth'], projects, dict(probe, figure='count')) >= 3
                            and good_figure(second['figure'], sum_of(runs['truth'], projects, probe)))
                moved = next(since for since in range(first['since'] - 2, first['since'] + 3) if holds(since))

                def shift(st, number, items):
                    if number == 1:
                        st['since'], st['until'] = moved, moved + 3
                with self.assertRaisesRegex(Failed, 'two spans of 3 meetings overlap'):
                    choice_rules(edition, count, runs, projects, doctored('span3', shift))
                # two statements that are the same statement
                def same(st, number, items):
                    if number == 1:
                        st.update({k: v for k, v in items[0].items() if k not in ('value', 'value_span', 'text')})
                with self.assertRaisesRegex(Failed, 'the same statement'):
                    choice_rules(edition, count, runs, projects, doctored('book', same))
                # a named sum at a meeting outside the last third, and over a project with fewer than two payments there
                # (at the latest meeting before the last third where five projects each have two payments, over those five: only the
                # place of the meeting can be refused)
                early, pool = next((m, [n for n in projects if runs['truth'][('count', n, m)] >= 2]) for m in range((2 * count - 1) // 3, 0, -1)
                                   if sum(runs['truth'][('count', n, m)] >= 2 for n in projects) >= 5)
                self.assertLess(3 * early, 2 * count)
                with self.assertRaisesRegex(Failed, 'not in the last third'):
                    choice_rules(edition, count, runs, projects, doctored('sum5', lambda st, n, items: st.update(
                        until=early, names=pool[:5]), {0}))
                # totals only, where half should be counts
                with self.assertRaisesRegex(Failed, 'has \\{'):
                    choice_rules(edition, count, runs, projects, doctored('sum8', lambda st, n, items: st.update(figure='total')))
                # an upto sum at the last meeting is not an upto sum; the quota table sees it
                if edition == 4:
                    with self.assertRaisesRegex(Failed, 'fewer than three meetings|the same statement'):     # six distinct statements need three
                        choice_rules(edition, count, runs, projects, doctored('upto', lambda st, n, items: st.update(until=items[0]['until'])))
                    with self.assertRaisesRegex(Failed, 'an upto sum at meeting'):
                        choice_rules(edition, count, runs, projects,
                                     doctored('upto', lambda st, n, items: st.update(until=3 * count // 5 - 1), {0}))
                # a statement too few of a rung, and a sum over four projects
                lacking = copy.deepcopy(both)
                lacking['correct'].pop()
                with self.assertRaisesRegex(Failed, 'the text shows'):
                    quota_rules(edition, count, lacking, rows)
                with self.assertRaisesRegex(Failed, 'the text shows'):
                    quota_rules(edition, count, doctored('sum5', lambda st, n, items: st.update(names=st['names'][:4])), rows)
        with edition_env(3):
            built, (book, _) = case(ED_SEED), read(ED_SEED)
            sources = sources_of(built)
            text_rules3(sources, book)
            paragraphs = sources['procedure.md'].rstrip('\n').split('\n\n')

            def without(drop):
                return dict(sources, **{'procedure.md': '\n\n'.join(p for p in paragraphs if not drop(p)) + '\n'})
            with self.assertRaisesRegex(Failed, 'paragraphs of procedure.md name the verbs'):
                text_rules3(without(lambda p: '"stood higher"' in p), book)
            with self.assertRaisesRegex(Failed, 'rescission paragraph'):
                text_rules3(without(lambda p: p.startswith('Rescissions.')), book)
            with self.assertRaisesRegex(Failed, 'rescission paragraph'):
                text_rules3(dict(sources, **{'procedure.md': sources['procedure.md'].replace('was struck out', 'was lost')}), book)
            thirteenth = next(form for form, number in book.ordinal_forms.items() if number == 12)
            with self.assertRaisesRegex(Failed, 'beyond the twelfth'):
                text_rules3(dict(sources, **{'minute-book-1.md': sources['minute-book-1.md'] + f'\n{thirteenth.capitalize()} was discussed.\n'}), book)
            reordered = copy.copy(book)
            reordered.facts = [dict(f, recipient_first=False) if f['t'] == 'pay' and f['spec']['type'] == 'first_of' else f for f in book.facts]
            with self.assertRaisesRegex(Failed, 'before its recipient'):
                text_rules3(sources, reordered)
            # a real sentence rewritten with the amount before its recipient, for each way an amount can be an instalment
            for kind in INSTALMENT_SPECS:
                doctored_sources = sources_with_recipient_after_amount(book, sources, kind)
                self.assertIsNotNone(doctored_sources, f'the dossier has no payment of amount kind {kind}')
                with self.assertRaisesRegex(Failed, rf'\({kind}\) comes before its recipient'):
                    text_rules3(doctored_sources, Book(doctored_sources))
            self.assertIn('first', text_rules3(sources, book))

    def test_hand_written_award_side_sums(self):
        print('hand-written award-side sums:', check_hand_award_sums())
        print('evaluator on the hand-written award-side sums:', check_evaluator_on_hand_award_cases())
        results = []
        for setting in (None, 1, 2, 5, 6):                                  # `answer` does not read the edition variable
            with edition_env(setting):
                results.append(check_hand_award_sums())
        self.assertEqual(len(set(results)), 1)
        names = ' '.join(c['name'] for c in HAND_AWARD_CASES) + ' ' + ' '.join(x[0] for x in HAND_AWARD_SUBSETS) + ' ' + ' '.join(x[0] for x in HAND_AWARD_READERS)
        for topic in ('correction read back', 'fraction', 'rescission', 'no award yet', 'motions that decide nothing', 'after the close',
                      'same meeting', 'fully paid', 'cancelled', 'cannot be attributed', 'negative', 'empty list', 'payments only',
                      'without its award', 'application is missing', 'short form', 'not positive', 'not whole', 'negative part',
                      'deferred variation', 'lacking the award of A', 'lacking a rescission', 'lacking a correction', 'lacking a variation'):
            self.assertIn(topic, names)

    def test_the_hand_written_award_side_check_can_fail(self):
        """Negative controls: one value, one needed set and one reader's value written wrongly each make the checks report it."""
        import unittest.mock as mock
        here = sys.modules[__name__]
        cases = copy.deepcopy(HAND_AWARD_CASES)
        cases[0]['queries'][0] = cases[0]['queries'][0][:3] + (cases[0]['queries'][0][3] + 1,) + cases[0]['queries'][0][4:]
        with mock.patch.object(here, 'HAND_AWARD_CASES', cases):
            with self.assertRaisesRegex(Failed, 'hand-written award-side sums disagree'):
                check_hand_award_sums()
            with self.assertRaisesRegex(Failed, 'by hand 24001'):
                check_evaluator_on_hand_award_cases()
        cases = copy.deepcopy(HAND_AWARD_CASES)
        figure, projects, meeting, value, needed = cases[4]['queries'][1]         # the cancellation: the award of A is not needed
        cases[4]['queries'][1] = (figure, projects, meeting, value, needed | {'a1'})
        with mock.patch.object(here, 'HAND_AWARD_CASES', cases):
            with self.assertRaisesRegex(Failed, 'hand-written award-side sums disagree'):
                check_hand_award_sums()
        readers = copy.deepcopy(HAND_AWARD_READERS)
        readers[0] = readers[0][:3] + ([readers[0][3][0][:4] + (6001,) + readers[0][3][0][5:]],)
        with mock.patch.object(here, 'HAND_AWARD_READERS', readers):
            with self.assertRaisesRegex(Failed, 'hand-written award-side sums disagree'):
                check_hand_award_sums()
            with self.assertRaisesRegex(Failed, 'reader'):
                check_evaluator_on_hand_award_cases()
        readers = copy.deepcopy(HAND_AWARD_READERS)
        readers[0][3][0] = readers[0][3][0][:5] + (False,)                           # 6000 is a positive whole sum: it is offered
        with mock.patch.object(here, 'HAND_AWARD_READERS', readers):
            with self.assertRaisesRegex(Failed, 'should be offered|should not be offered|hand-written award-side sums disagree'):
                check_hand_award_sums()

    def test_the_evaluator_adds_nothing_for_a_project_without_an_award(self):
        """Design, section 2: on the award side a project that holds no award at the close (the established "none", or not
        established) adds nothing; a negative balance of a reader is added as it stands; a payment sum is strict as before."""
        results = {}
        for name, award, balance, total in (('A', 'none', None, 0), ('B', None, None, 4000), ('C', 5000, -200, 5200), ('D', 7000, 7000, 0)):
            results.update({('award', name, 3): award, ('balance', name, 3): balance, ('total', name, 3): total, ('count', name, 3): 1})
        names = ['A', 'B', 'C', 'D']
        take = lambda figure, which=None: sum_of(results, names, {'shape': 'named' if which else 'all', 'figure': figure, 'since': None, 'until': 3,  # noqa: E731
                                                                 'names': which})
        self.assertEqual(take('award'), 12000)
        self.assertEqual(take('balance'), 6800)
        self.assertEqual(take('balance', ['A', 'B']), 0)
        self.assertEqual(take('balance', ['C']), -200)
        self.assertEqual(take('total'), 9200)                                       # the payment side adds every project's figure
        results[('total', 'A', 3)] = None
        self.assertIsNone(take('total'))                                            # and is strict where one is not established
        self.assertEqual(take('award'), 12000)
        # the rung of a statement, from its own text
        eight = {'shape': 'named', 'names': list('abcdefgh'), 'since': None}
        for figure, rung in (('award', 'asum8'), ('balance', 'asum8'), ('total', 'sum8'), ('count', 'sum8')):
            self.assertEqual(rung_of(dict(eight, figure=figure, until=25), 30), rung)
        for figure, until, rung in (('award', 20, 'aupto'), ('balance', 29, 'abook'), ('total', 20, 'upto'), ('count', 29, 'book')):
            self.assertEqual(rung_of({'shape': 'all', 'figure': figure, 'until': until, 'since': None, 'names': None}, 30), rung)
        self.assertEqual(claim_kind_of({'shape': 'all', 'figure': 'balance', 'until': 29, 'since': None, 'names': None}, 30), 'minutes_abook')
        # a good value of a sum is the generator's own rule for the money figures too
        for figure in ('award', 'balance', 'total'):
            for value in (fractions.Fraction(1), fractions.Fraction(12000), fractions.Fraction(0), fractions.Fraction(-2000), fractions.Fraction(9009, 2)):
                self.assertEqual(good_figure(figure, value), big_minutes.good_sum(figure, int(value) if value.denominator == 1 else float(value)),
                                 (figure, value))

    def test_the_frames_of_the_award_side_and_the_rescission_frames(self):
        print(check_frames5())
        print(check_rescission_frames())
        import unittest.mock as mock
        tables = INV['sum_statements']
        for table, frame, message in (
                ('sum_balance', 'At the close of {when}, the award to {names} stood {value} short of being fully paid.', 'fully paid'),
                ('all_balance', 'At the close of {when}, {value} of the awards to all projects was fully paid.', 'balance wordings|fully paid'),
                ('sum_award', 'After {when}, the awards to {names} came to {value}.', 'in force'),
                ('all_award', 'In force at the close, {value} of awards.', 'name the close|placeholders'),
                ('all_balance', 'After {when}, the unpaid balance of the awards to {names} was {value}.', 'placeholders')):
            with mock.patch.dict(tables, {table: tables[table][:2] + (frame,)}):
                with self.assertRaisesRegex(Failed, message):
                    check_frames5()
        with mock.patch.dict(tables, {'sum_balance': tables['sum_balance'][:2]}):
            with self.assertRaisesRegex(Failed, 'three distinct ones'):
                check_frames5()
        # a rescission frame whose wording the procedure does not name is refused, whatever a seed draws
        frames = INV['frames']
        with mock.patch.dict(frames, {'rescind': frames['rescind'] + ('{proj} was set aside by the committee.',)}):
            with self.assertRaisesRegex(Failed, 'holds none of the verbs and wordings the procedure names'):
                check_rescission_frames()
        # the procedure's own text decides: take its first sentence out, or change its verbs, and the check fails (a frame is no longer
        # accepted by a verb the test itself assumes)
        sentence = 'When the committee rescinds, withdraws or cancels an award, '
        here = sys.modules[__name__]
        real_sources_of = sources_of
        with edition_env(5):
            procedure = real_sources_of(case(ED_SEED))['procedure.md']
        self.assertEqual(procedure.count(sentence), 1)
        self.assertEqual(rescission_frames_unnamed(INV['frames']['rescind'], next(
            p for p in procedure.split('\n\n') if p.startswith('Rescissions.'))), [])

        def doctored_sources(replacement):
            def read(built):
                return dict(real_sources_of(built), **{'procedure.md': procedure.replace(sentence, replacement)})
            return read
        for replacement, message in (('', 'lacks its first sentence'),
                                     ('When the committee ends, stops or closes an award, ', 'holds none of the verbs and wordings')):
            with mock.patch.object(here, 'sources_of', doctored_sources(replacement)):
                with self.assertRaisesRegex(Failed, message):
                    check_rescission_frames()

    def test_the_round_trip_sees_a_corrupted_award_side(self):
        """Negative controls of the award side on an edition 5 dossier: an award whose digits are changed in the minute book, the stated
        value of a false award-side sum, and the key's true value of one of them must each be reported."""
        with edition_env(5):
            built, (book, runs) = case(ED_SEED), read(ED_SEED)
            readers = readers_in(ED_SEED)
            round_trip3(built, book, runs, readers)
            changed, text = book_with_one_award_changed(ED_SEED)
            with self.assertRaisesRegex(Failed, r'(correct|mixed): (verdict|reader \w+) differs for'):
                round_trip3(built, changed, runs_of(changed), readers)
            nudged = built_with_false_sum_nudged(ED_SEED, figures=AWARD_FIGURES)
            with self.assertRaisesRegex(Failed, r'false sum .* states \d+, but reader \w+ gets'):
                round_trip3(nudged, book, runs, readers)
            wrong_key = copy_built(case(ED_SEED))
            row = next(r for r in wrong_key['key']['expectations'] if r['variant'] == 'mixed' and r['claim_kind'] in
                       ('minutes_asum8', 'minutes_aupto', 'minutes_abook') and r['semantic_state'] == 'refuted')
            row['derivation'] = dict(row['derivation'], feasible_max=row['derivation']['feasible_max'] + 1)
            with self.assertRaisesRegex(Failed, 'the key.s true value'):
                round_trip3(wrong_key, book, runs, readers)
            round_trip3(case(ED_SEED), read(ED_SEED)[0], read(ED_SEED)[1], readers)

    def test_the_rules_of_choice_and_quota_see_a_broken_award_side(self):
        """Negative controls of the rules of edition 5: each doctored statement list must be refused, the undamaged one not."""
        with edition_env(5):
            built, (book, runs) = case(ED_SEED), read(ED_SEED)
            both, count, projects = statements_of(ED_SEED), len(book.meetings), book.projects
            rows = {v: key_rows(built, v) for v in both}
            choice_rules(5, count, runs, projects, both, book)
            quota_rules(5, count, both, rows)
            truth = runs['truth']

            def doctored(rung, edit, which=None):
                out = copy.deepcopy(both)
                for variant in out:
                    items = [st for st in out[variant] if rung_of(st, count) == rung]
                    for number, st in enumerate(items):
                        if which is None or number in which:
                            edit(st, number, items)
                return out

            def holders(meeting):
                return [n for n in projects if truth[('award', n, meeting)] not in (None, 'none')]
            late = [m for m in range(count) if 3 * m >= 2 * count]
            # a named award sum over a project that holds no award at the meeting (the last close before every award is made)
            first = min(m for m in range(count) if holders(m))
            self.assertGreater(first, 0, 'no meeting before the first award: the control has nothing to refuse')
            lacking = next(n for n in projects if truth[('award', n, first - 1)] in (None, 'none'))
            with self.assertRaisesRegex(Failed, 'holds no award at its sum'):
                choice_rules(5, count, runs, projects, doctored('asum8', lambda st, n, items: st.update(
                    until=first - 1, names=[lacking] + st['names'][1:]), {0}), book)
            # a named award sum at a meeting outside the last third, over eight projects that hold an award there and rest on corrections
            # or variations as far as the world allows: only the place of the meeting can be refused
            for early in range((2 * count - 1) // 3, 0, -1):
                pool = [n for n in holders(early) if rests_on_correction_or_variation(book, n, early)]
                rest = [n for n in holders(early) if n not in pool]
                if len(pool) + len(rest) >= 8:
                    names = (pool + rest)[:8]
                    break
            self.assertLess(3 * early, 2 * count)
            with self.assertRaisesRegex(Failed, 'not in the last third'):
                choice_rules(5, count, runs, projects, doctored('asum8', lambda st, n, items: st.update(until=early, names=names), {0}), book)
            # named award sums over eight projects of which none rests on a correction or a variation, where the world has more of them
            refused = False
            for m in late:
                pool = [n for n in holders(m) if rests_on_correction_or_variation(book, n, m)]
                plain = [n for n in holders(m) if n not in pool]
                if len(pool) >= 3 and len(plain) >= 8:
                    with self.assertRaisesRegex(Failed, 'rest on a correction or a variation'):
                        choice_rules(5, count, runs, projects, doctored('asum8', lambda st, n, items: st.update(
                            until=m, names=plain[:8]), {0}), book)
                    refused = True
                    break
            self.assertTrue(refused, 'no meeting of the last third has eight projects that rest on neither a correction nor a variation')
            # the aupto sums at fewer than four meetings; at the last meeting; an abook sum before the last meeting; the same statement
            with self.assertRaisesRegex(Failed, 'fewer than four different meetings|the same statement'):
                choice_rules(5, count, runs, projects, doctored('aupto', lambda st, n, items: st.update(until=items[0]['until'])), book)
            with self.assertRaisesRegex(Failed, 'an aupto sum at meeting'):
                choice_rules(5, count, runs, projects, doctored('aupto', lambda st, n, items: st.update(until=3 * count // 5 - 1), {0}), book)
            with self.assertRaisesRegex(Failed, 'the same statement'):
                choice_rules(5, count, runs, projects, doctored('abook', lambda st, n, items: st.update(
                    {k: v for k, v in items[0].items() if k not in ('value', 'value_span', 'text')}) if n == 1 else None), book)
            # the figures: two sums of awards where one award and one balance are asked; a sum that is not a good value
            with self.assertRaisesRegex(Failed, 'has \\{'):
                choice_rules(5, count, runs, projects, doctored('asum8', lambda st, n, items: st.update(figure='award')), book)
            with self.assertRaisesRegex(Failed, 'has \\{'):
                choice_rules(5, count, runs, projects, doctored('aupto', lambda st, n, items: st.update(figure='award')), book)
            with self.assertRaisesRegex(Failed, 'has \\{|has the true value|states a value|the same statement'):
                choice_rules(5, count, runs, projects, doctored('abook', lambda st, n, items: st.update(figure='total')), book)
            with self.assertRaisesRegex(Failed, 'states a value that is not positive'):
                choice_rules(5, count, runs, projects, doctored('abook', lambda st, n, items: st.update(value=0), {0}), book)
            # the quota table: a statement too few, and a payment sum where an award sum is asked
            lacking_one = copy.deepcopy(both)
            lacking_one['correct'].pop()
            with self.assertRaisesRegex(Failed, 'the text shows'):
                quota_rules(5, count, lacking_one, rows)
            with self.assertRaisesRegex(Failed, 'the text shows'):
                quota_rules(5, count, doctored('aupto', lambda st, n, items: st.update(figure='total'), {0}), rows)
            with self.assertRaisesRegex(Failed, 'the claim kinds are not those'):
                quota_rules(5, count, both, {v: [dict(r, claim_kind='minutes_sum8') if r['claim_kind'] == 'minutes_abook' else r
                                                 for r in rows[v]] for v in rows})
            # no balance statement of the dossier says "fully paid"
            self.assertNotIn('fully paid', ' '.join(st['text'] for st in both['correct'] if st['shape'] != 'single' and st['figure'] == 'balance'))

    def test_the_six_files_are_those_of_commits_7e4beca9_and_5d1ca73d(self):
        """Item 9 of the design, where the commits are in the history of this repository: the six files the build must not touch are
        those of commit 7e4beca9 (edition 5's base) and of 5d1ca73d (edition 5 itself, the commit edition 6 is built on); the commands
        are in the docstring of this module. A commit that is not in this repository's history is reported as skipped by name, and
        at least one of the two must be there."""
        files = ('evalsuite/check_shown.py', 'evalsuite/generate_big.py', 'evalsuite/generate.py', 'evalsuite/run_blind.py',
                 'evalsuite/prompts/task-shown-sums.md', 'evalsuite/arms-shown-sums.json')
        try:
            top = subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=ROOT, capture_output=True, text=True)
            known = {commit: subprocess.run(['git', 'cat-file', '-e', f'{commit}^{{commit}}'], cwd=ROOT, capture_output=True).returncode == 0
                     for commit in ('7e4beca9', '5d1ca73d')}
        except OSError:
            self.skipTest('git is not available')
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != ROOT:
            self.skipTest(f'{ROOT} is not the top of its own git repository (git resolves {top.stdout.strip() or "none"}): '
                          'the comparison would be vacuous')
        if not any(known.values()):
            self.skipTest('neither commit 7e4beca9 nor 5d1ca73d is in this repository')
        for commit, there in known.items():
            if not there:
                print(f'commit {commit} is not in this repository: its comparison is skipped')
                continue
            # the one file edition 8 touches is the commit's file plus the three lines below, each once, in their places, and nothing else
            then = subprocess.run(['git', 'show', f'{commit}:{EDITION8_KEY_FILE}'], cwd=ROOT, capture_output=True, text=True).stdout
            self.assertEqual((ROOT / EDITION8_KEY_FILE).read_text(encoding='utf-8'), edition8_key_file(then),
                             f'{EDITION8_KEY_FILE} is not commit {commit}\'s file plus the three lines of edition 8')
            for name in files:        # the blob of each file as committed at the commit against the file in this tree
                want = subprocess.run(['git', 'rev-parse', f'{commit}:{name}'], cwd=ROOT, capture_output=True, text=True)
                have = subprocess.run(['git', 'hash-object', '--', name], cwd=ROOT, capture_output=True, text=True)
                self.assertEqual((want.returncode, have.returncode), (0, 0), name)
                if name == EDITION8_KEY_FILE:
                    continue      # `generate_big.py` carries `quiet_returns` into the key's facts (edition 8): checked line by line below
                self.assertEqual(have.stdout.strip(), want.stdout.strip(), f'{name} differs from commit {commit}')
            done = subprocess.run(['git', 'diff', '--quiet', commit, '--'] + [n for n in files if n != EDITION8_KEY_FILE], cwd=ROOT,
                                  capture_output=True)
            self.assertEqual(done.returncode, 0, f'one of the six files differs from commit {commit}')

    def test_the_sum_task_files(self):
        root = ROOT / 'evalsuite'
        old = (root / 'prompts' / 'task-shown.md').read_text(encoding='utf-8').split('\n')
        new = (root / 'prompts' / 'task-shown-sums.md').read_text(encoding='utf-8').split('\n')
        self.assertEqual(len(old), len(new))
        changed = [i for i, (a, b) in enumerate(zip(old, new)) if a != b]
        self.assertEqual(len(changed), 2, changed)
        first, second = changed
        sentence = 'For a claim about several projects together, or about all projects, the value is asked and quotes are not required.'
        self.assertEqual(new[first], old[first] + ' ' + sentence)
        self.assertIn('by a description', old[second])
        self.assertNotIn('by a description', new[second])
        for phrase in ('a name or role fixed in another passage', 'its place in an order', 'pointing to another passage', 'needs none'):
            self.assertIn(phrase, new[second])
        for placeholder in ('{{brief}}', '{{method}}', '{{sheet}}', '{{minutes}}'):
            self.assertEqual(sum(l.count(placeholder) for l in new), sum(l.count(placeholder) for l in old), placeholder)
            self.assertGreaterEqual(sum(l.count(placeholder) for l in new), 1, placeholder)
        arms_old = json.loads((root / 'arms-shown.json').read_text(encoding='utf-8'))
        arms_new = json.loads((root / 'arms-shown-sums.json').read_text(encoding='utf-8'))
        self.assertEqual(arms_new['task'], 'prompts/task-shown-sums.md')
        self.assertEqual(dict(arms_new, task=arms_old['task']), arms_old)           # the task path is the only difference
        import run_blind
        loaded_old, loaded_new = run_blind.load_arms(root / 'arms-shown.json'), run_blind.load_arms(root / 'arms-shown-sums.json')
        self.assertEqual(sorted(loaded_new), sorted(loaded_old))
        for ident, arm in loaded_new.items():
            self.assertEqual(arm['task'], '\n'.join(new))
            self.assertNotEqual(arm['fingerprint'], loaded_old[ident]['fingerprint'])         # a new arm fingerprint: no pooling
            text = run_blind.instructions(arm, 30)                                           # every placeholder is filled
            self.assertIn(sentence, text)
            self.assertNotIn('{{', text)

    @unittest.expectedFailure
    def test_lead_paragraphs_can_be_quoted(self):
        """Edition 1 (its bytes are fixed): about one statement in ten needs a lead paragraph that occurs twice in its
        dossier, which no quote can single out; edition 2 draws every lead paragraph once (the next test)."""
        for seed in SEEDS:
            check_lead_paragraphs_quotable(seed)

    def test_lead_paragraphs_can_be_quoted_in_edition_2(self):
        with edition_env(2):
            print(f'edition 2, seed {EDITION2_FULL}:', check_lead_paragraphs_quotable(EDITION2_FULL))

    def test_lures_turn_on_one_device(self):
        for seed in SEEDS:
            check_single_devices(seed)

    def test_text_tells_each_event_once(self):
        for seed in SEEDS:
            book, _ = read(seed)
            told = collections.Counter((f['t'], f['p'], f['m']) for f in book.facts)
            made = made_of(seed)
            logged = collections.Counter((e['kind'], e['subject'], e['m']) for e in made['events'] if e['kind'] != 'names')
            self.assertEqual(told, logged, seed)
            self.assertEqual(max(told.values()), 1, seed)

    def test_whole_base_and_reader_bases_match_independent_reading(self):
        for seed in SEEDS:
            book, runs = read(seed)
            made = made_of(seed)
            dates = made['meetings']
            bases = {'truth': (made['base'], runs['truth'])}
            bases.update({name: (reader['base'], runs[name]) for name, reader in made['readers'].items()})
            for name, (base, mine) in bases.items():
                checked = 0
                for key, value in base.items():
                    kind, who, until = key.split('|')
                    if kind == 'first':
                        found = mine[('first', who, None)]
                        theirs = 'never' if found is None else dates[found]
                    elif kind == 'projects':
                        theirs = mine[('projects', None, dates.index(until))]
                    else:
                        theirs = mine[(kind, who, dates.index(until))]
                        if theirs is None and kind in ('balance', 'largest'):
                            theirs = 'none'
                    self.assertEqual(value, theirs, (seed, name, key))
                    checked += 1
                self.assertGreater(checked, 2000, (seed, name))

    def test_answer_sheets_do_no_work(self):
        for seed in SEEDS:
            rows = key_rows(case(seed), 'mixed')
            states = [r['semantic_state'] for r in rows]
            self.assertGreaterEqual(states.count('refuted') / 24, 0.30, seed)
            self.assertGreaterEqual(states.count('supported') / 24, 0.30, seed)

    def test_flawed_readers_and_controls(self):
        for seed in SEEDS:
            built = case(seed)
            for variant in ('correct', 'mixed'):
                rows = key_rows(built, variant)
                wrong = collections.Counter(name for r in rows for name in r['lure_states'])
                for name in READERS:
                    self.assertGreaterEqual(wrong[name], 4, (seed, variant, name, dict(wrong)))
            rows = key_rows(built, 'correct')
            self.assertGreaterEqual(sum(not r['lure_states'] for r in rows), 6, seed)
            for row in rows:
                self.assertEqual(row['semantic_state'], 'supported')

    def test_event_log_passages_and_state_function(self):
        for seed in SEEDS:
            rng = random.Random(f'evalsuite:{generate.GENERATOR_VERSION}:{seed}:1:minutes')
            made = big_minutes.build(rng)
            events = made['events']
            self.assertEqual(json.loads(json.dumps(events)), events)
            self.assertEqual(len({e['id'] for e in events}), len(events))
            self.assertTrue(all(re.fullmatch(r'E\d{3}', e['id']) for e in events if e['kind'] != 'names'))
            self.assertTrue(all(re.fullmatch(r'N\d{3}', e['id']) for e in events if e['kind'] == 'names'))
            self.assertTrue(any(e['kind'] == 'names' for e in events))
            self.assertTrue(any(not e['decides'] for e in events))
            book, _ = read(seed)
            built = generate.build_case('minutes', seed, 1)
            sources = sources_of(built)
            told = set()
            kind_of = {'correct': 'correct', 'restate': 'restate'}
            by_id = {e['id']: e for e in events}
            for entry in made['passages']:
                self.assertEqual(sorted(entry), ['event_ids', 'file', 'paragraph', 'role'])
                blocks = sources[entry['file']].rstrip('\n').split('\n\n')
                text = blocks[entry['paragraph']]
                if entry['role'] != 'states':
                    continue
                file_index = VOLUMES.index(entry['file'])
                here = [f for f in book.facts if f['key'] == (file_index, entry['paragraph'])]
                for event_id in entry['event_ids']:
                    event = by_id[event_id]
                    told.add(event_id)
                    matches = [f for f in here if f['t'] == kind_of.get(event['kind'], event['kind']) and f['p'] == event['subject']
                               and f['m'] == made['meetings'].index(event['date'])]
                    self.assertEqual(len(matches), 1, (event_id, text[:200]))
                    self.assertTrue(same_values(event, matches[0], by_id), (event_id, event['kind'], text[:300]))
            self.assertEqual(told, {i for i, e in by_id.items() if e['kind'] != 'names'})     # a lead event is named by its own entry
            self.assertEqual({i for p in made['passages'] if p['role'] == 'names' for i in p['event_ids']},
                             {i for i, e in by_id.items() if e['kind'] == 'names'})
            full = made['base']
            self.assertEqual(big_minutes.state(events, None, made['meetings']), full)
            self.assertEqual(full, json.loads(json.dumps(full)))
            for claim_pair in made['slots']:
                true = claim_pair[0]
                self.assertEqual(big_minutes.answer(true['query'], events), true['query']['value'])
            sub_rng = random.Random(seed)
            for keep in (0.0, 0.05, 0.2, 0.5, 0.8, 0.95) * 4:
                subset = [e for e in events if sub_rng.random() < keep]
                for calendar in (made['meetings'], None):
                    for reader in (None,) + big_minutes.READERS:
                        self.assertIsInstance(big_minutes.state(subset, reader, calendar), dict)
            # A payment "equal to the first" is left out when the first is missing (what is paid is not established
            # only for the award, the balance and the first full payment of a project without its award); so is a correction
            # of an absent figure. Neither makes the project's total not established.
            end = made['meetings'][-1]
            relative = next(e for e in events if e['kind'] == 'pay' and e['spec']['type'] == 'first')
            cut = [e for e in events if e['id'] != relative['spec']['of']]
            flat = big_minutes.state(cut, None, made['meetings'])
            both = big_minutes.state([e for e in cut if e['id'] != relative['id']], None, made['meetings'])
            self.assertIsNotNone(flat[f'total|{relative["subject"]}|{end}'])
            self.assertEqual(flat[f'total|{relative["subject"]}|{end}'], both[f'total|{relative["subject"]}|{end}'])
            fixed = next(e for e in events if e['kind'] == 'correct')
            flat = big_minutes.state([e for e in events if e['id'] != fixed['target']], None, made['meetings'])
            if fixed['target_kind'] == 'award':
                self.assertIsNone(flat[f'award|{fixed["subject"]}|{end}'])           # its own award is not there
                self.assertIsNotNone(flat[f'total|{fixed["subject"]}|{end}'])
            else:
                self.assertIsNotNone(flat[f'total|{fixed["subject"]}|{end}'])
            # the same on answer, on any subset and any reader: never raises, and says a value or None
            for keep in (0.0, 0.05, 0.2, 0.5, 0.8, 0.95) * 2:
                subset = [e for e in events if sub_rng.random() < keep]
                for key, q in list(statement_queries(seed).items())[::4]:
                    for reader in (None,) + big_minutes.READERS:
                        value = big_minutes.answer(q, subset, reader)
                        self.assertTrue(value is None or isinstance(value, (int, float, str)), (seed, key, reader, value))
                        if q['kind'] == 'projects':
                            self.assertIsInstance(value, int)


class QuietReturnsTest(unittest.TestCase):
    """Edition 8: the 48 forms of a quiet return, the choice of returns, the swap, and the edition against edition 6 on two seeds. The sweeps
    over 20 and 30 seeds are `tests/sweep_minutes_edition_8.py`, which the gate does not discover."""

    def test_the_stock_is_the_48_forms_of_the_brief(self):
        self.assertEqual((big_minutes.QUIET_OPENINGS, big_minutes.QUIET_DEEDS, big_minutes.QUIET_WHATS), QUIET_STOCK)
        self.assertEqual(len(quiet_forms()), 48)
        self.assertEqual((INV['quiet_openings'], INV['quiet_deeds'], INV['quiet_whats']), QUIET_STOCK)
        self.assertEqual(big_minutes.QUIET_FORMS, 48)
        for table in (big_minutes.FRAMES, big_minutes.PLAIN_FRAMES):
            for frames in table.values():
                self.assertFalse([f for f in frames if 'minuted in' in f], 'a quiet part is in a shared table')
        self.assertEqual(len(INV['when_forms']), 7)        # `WHEN_FORMS` is as it was: every draw from it is unchanged

    def test_every_form_holds_every_rule_for_other_words_for_the_project(self):
        """Each of the 48 forms, with four different words for the project (one holds "Restoration" and one the word "committee"), holds
        the rules of the brief, and this module's reader reads it back as one return of that project at the meeting of April 2038."""
        refs = '(?:' + '|'.join(re.escape(w) for w in sorted(QUIET_PROJECTS, key=len, reverse=True)) + ')'
        readers = quiet_frames(refs)
        self.assertEqual(len(readers), 48)
        for proj in QUIET_PROJECTS:
            texts = [big_minutes.quiet_sentence(form, proj, QUIET_DATE) for form in quiet_forms()]
            self.assertEqual(len(set(texts)), 48, proj)
            for text in texts:
                quiet_form_rules(text, proj)
                found = [mo for rx in readers for mo in [rx.fullmatch(text)] if mo]
                self.assertEqual(len(found), 1, text)
                self.assertEqual((found[0]['proj'], date_parts(found[0]['when'])), (proj, (2038, 4, None)), text)

    def test_the_rules_see_a_broken_form(self):
        """Negative controls: each rule refuses a sentence that breaks it (the unbroken sentence passes)."""
        proj = QUIET_PROJECTS[0]
        good = big_minutes.quiet_sentence((0, 0, 0), proj, QUIET_DATE)
        quiet_form_rules(good, proj)
        for broken in (good.replace('handed in', 'repaid'), good.replace('handed in', 'handed in £500 to'), good.replace('let it have', 'paid'),
                       good.replace('It was noted that', 'The treasurer noted that'), good.replace('It was noted that', 'The chair noted that'),
                       good.replace('as minuted in April 2038', 'as minuted in April'), good.replace('to the committee', 'to the sponsor'),
                       good.replace('what the committee,', 'what the sponsor,'), good + ' It was returned.', good.replace('April 2038', '12 April 2038')):
            self.assertNotEqual(broken, good)
            with self.assertRaises(Failed, msg=broken):
                quiet_form_rules(broken, proj)

    def test_which_returns_are_eligible_and_how_many_are_quiet(self):
        """Hand-written logs: a return is eligible only when its payment is the one event of the project at its meeting (`names` and
        `restate` apart) and its paragraph states nothing else; a book needs 12 returns and 6 eligible ones; a third, rounded, at least 6."""
        def book(count, spoil):
            events, passages = [], []
            for n in range(count):
                events += [{'id': f'P{n}', 'kind': 'pay', 'p': n, 'm': 3}, {'id': f'R{n}', 'kind': 'return', 'p': n, 'm': 9, 'target': f'P{n}', 'target_m': 3},
                           {'id': f'N{n}', 'kind': 'names', 'p': n, 'm': 3}, {'id': f'S{n}', 'kind': 'restate', 'p': n, 'm': 3}]
                passages += [{'file': 'minute-book-1.md', 'paragraph': 2 * n + 1, 'event_ids': [f'R{n}'], 'role': 'states'},
                             {'file': 'minute-book-1.md', 'paragraph': 2 * n + 2, 'event_ids': [f'N{n}'], 'role': 'names'}]
            for n in spoil.get('award', ()):
                events.append({'id': f'A{n}', 'kind': 'award', 'p': n, 'm': 3})
            for n in spoil.get('shared', ()):
                passages[2 * n]['event_ids'].append(f'P{n}')
            return events, passages
        events, passages = book(12, {})
        self.assertEqual(big_minutes.eligible_returns(events, passages), sorted(f'R{n}' for n in range(12)))
        events, passages = book(12, {'award': (0, 1), 'shared': (2,)})
        self.assertEqual(big_minutes.eligible_returns(events, passages), sorted(f'R{n}' for n in range(3, 12)))
        state = random.Random(5).getstate()
        self.assertIsNone(big_minutes.quiet_plan(state, *book(11, {})))                                  # 11 returns: too few
        self.assertIsNone(big_minutes.quiet_plan(state, *book(12, {'award': tuple(range(7))})))          # 5 eligible: too few
        self.assertEqual(len(big_minutes.quiet_plan(state, *book(12, {}))), 6)                           # 12 eligible: a third is 4, the least is 6
        self.assertEqual(len(big_minutes.quiet_plan(state, *book(12, {'award': (0, 1, 2, 3, 4, 5)}))), 6)  # exactly 6 eligible: all of them
        self.assertEqual(len(big_minutes.quiet_plan(state, *book(30, {}))), 10)
        self.assertEqual(len(big_minutes.quiet_plan(state, *book(31, {}))), 10)
        self.assertEqual(len(big_minutes.quiet_plan(state, *book(32, {}))), 11)
        plan = big_minutes.quiet_plan(state, *book(40, {}))
        self.assertEqual(len(set(plan.values())), len(plan))                                              # no form twice
        self.assertEqual(plan, big_minutes.quiet_plan(state, *book(40, {})))                             # the same state gives the same plan
        self.assertNotEqual(plan, big_minutes.quiet_plan(random.Random(6).getstate(), *book(40, {})))

    def test_the_swap_needs_the_sentence_once_in_its_paragraph(self):
        """The sentence edition 6 drew must be found exactly once in the paragraph that states the return; otherwise the generator refuses."""
        passages = [{'file': 'minute-book-1.md', 'paragraph': 1, 'event_ids': ['R1'], 'role': 'states'}]
        events = [{'id': 'R1', 'target_m': 0}]
        def swap(paragraph, old):
            files = {'minute-book-1.md': 'head\n\n' + paragraph + '\n\ntail\n'}
            blocks_of = {}
            out = big_minutes.swap_quiet(files, passages, blocks_of, {'quiet_log': {'R1': ['the scheme', old]}, 'dates': [QUIET_DATE]},
                                         events, {'R1': (0, 0, 0)})
            return out, blocks_of
        out, blocks_of = swap('Before. It was repaid. After.', 'It was repaid.')
        self.assertEqual(out['minute-book-1.md'], 'head\n\nBefore. It was noted that the scheme had handed in to the committee what the committee, '
                                                  'as minuted in April 2038, let it have. After.\n\ntail\n')
        self.assertEqual(blocks_of['R1'], out['minute-book-1.md'].split('\n\n')[1])
        for paragraph in ('Before. After.', 'It was repaid. It was repaid.'):
            with self.assertRaises(generate.GenerationError, msg=paragraph):
                swap(paragraph, 'It was repaid.')

    def test_edition_8_is_edition_6_but_for_its_quiet_returns(self):
        """Two seeds (the second is built twice, once under each edition; the first is the cached dossier of the other tests): the same event
        log, passages, names, readers, procedure, both reports and the key's rows; the sources differ only in the paragraphs of the quiet
        returns the key lists; the key says `size` 8; the quiet returns are those the rules allow, each read back by this module's reader."""
        for seed in (ED_SEED, 2):
            with edition_env(6):
                six = facts_built(seed)
            with edition_env(8):
                eight = facts_built(seed)
            f6, f8 = six['key']['facts'], eight['key']['facts']
            self.assertEqual((f6.get('size'), f8['size']), (6, 8))
            for part in ('events', 'passages', 'names', 'readers', 'family', 'interpreter', 'interpreter_sha256'):
                self.assertEqual(f6[part], f8[part], (seed, part))
            self.assertEqual(six['key']['expectations'], eight['key']['expectations'], seed)
            for name in ('report-correct.md', 'report-mixed.md', 'sources/procedure.md'):
                self.assertEqual(six['files'][name], eight['files'][name], (seed, name))
            self.assertEqual(sorted(six['files']), sorted(eight['files']))
            self.assertNotIn('quiet_returns', f6)
            spots = {(entry['file'], entry['paragraph']) for entry in f8['passages'] if entry['role'] == 'states' and set(entry['event_ids']) & set(f8['quiet_returns'])}
            differing = set()
            for name in sources_of(six):
                a, b = sources_of(six)[name].split('\n\n'), sources_of(eight)[name].split('\n\n')
                self.assertEqual(len(a), len(b), (seed, name))
                differing |= {(name, n) for n, (x, y) in enumerate(zip(a, b)) if x != y}
            self.assertEqual(differing, spots, seed)
            returns, eligible, quiet = quiet_book_rules(eight, read_back=seed == ED_SEED)       # one reading of a book: it takes seconds
            print(f'edition 8, seed {seed}: {returns} returns, {eligible} eligible, {quiet} quiet')

    def test_a_late_refusal_leaves_the_main_source_as_edition_6_leaves_it(self):
        """Edition 8 turns a world down for its own text only after every draw edition 6 makes for it, so that the next world is the same in
        both editions. Forced here: the size test fails on the edition 8 text of the first attempt (not on edition 6's); edition 6 hands the
        world out, edition 8 refuses it, the main source is in the same state, and the second attempt of each is the same world."""
        import unittest.mock as mock
        real = big_minutes.text_fits
        stream = lambda: random.Random(f'evalsuite:{generate.GENERATOR_VERSION}:{ED_SEED}:1:minutes')    # noqa: E731
        calls = []

        def fits(sources, files, ctx, passages, edition):
            calls.append(edition)
            if len(calls) == 2:                          # the edition 8 text of the first attempt (the first call is edition 6's text)
                big_minutes.WHY['why'] = 'size'
                return False
            return real(sources, files, ctx, passages, edition)
        rng6, rng8 = stream(), stream()
        six = big_minutes.attempt(rng6, 6)
        with mock.patch.object(big_minutes, 'text_fits', fits):
            self.assertIsNone(big_minutes.attempt(rng8, 8))
        self.assertEqual(big_minutes.WHY['why'], 'quiet size')
        self.assertIsNotNone(six)
        self.assertEqual(rng6.getstate(), rng8.getstate())
        self.assertEqual(big_minutes.attempt(rng6, 6)['events'], big_minutes.attempt(rng8, 8)['events'])      # the next world

    def test_edition_8_is_the_same_bytes_in_another_process(self):
        """One process (another string-hash seed): the dossier with its facts, quiet returns included, is the one built here."""
        with edition_env(8):
            built = facts_built(ED_SEED)
        same_in_second_process(built, build_in_another_process(8, ED_SEED, 1, '4242'), 'a process with PYTHONHASHSEED=4242')


if __name__ == '__main__':
    if len(sys.argv) >= 4 and sys.argv[1] == '--verify':
        generate_big.register()         # `setUpModule` does not run on this path, and the process ends with the check
        raise SystemExit(1 if verify(int(sys.argv[2]), int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else None) else 0)
    unittest.main()
