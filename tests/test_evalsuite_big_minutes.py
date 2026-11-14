"""Independent checks of the minutes family (the minute book of a grants committee).

The reading side of this file uses the family's phrase inventory (`big_minutes.INVENTORY`, constants only) and the
rendered Markdown. It never reads `case['base']`, a claim's predicate or the event log to recover facts: a parser
built from the inventory finds the events in the text, and an evaluator written here recomputes the verdict of every
statement of both reports, and of every flawed reader. The event log, the passage map and the `state` function are
checked against that reading in their own tests.
"""
import collections
import fractions
import functools
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
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
generate_big.register()

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
        heading_res = [re.compile('## ' + src(h, {'date': DATE_SRC}, True).replace('(?P<date>', '(?P<date>')) for h in INV['headings']]
        self.parse(heading_res)

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
        for kind, compiled in self.frames.items():
            for rx in compiled:
                for mo in rx.finditer(text):
                    hits.append((mo.start(), mo.end(), kind, mo))
        hits.sort(key=lambda h: h[0])
        for earlier, later in zip(hits, hits[1:]):
            if later[0] < earlier[1]:
                self.errors.append(f'overlapping readings in {text[earlier[0]:later[1]]!r}')
        for start, _, kind, mo in hits:
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
            elif kind == 'return':
                self.add(key, m, text, 'return', who, when=self.meeting_of(g['when']), start=start)
            elif kind == 'pay':
                self.add(key, m, text, 'pay', who, spec=self.amount(g['amt'], m, key, who), start=start)
            elif kind == 'intro':
                self.add(key, m, text, 'intro', who, sponsor=g['sponsor'], start=start)
            for fact in self.facts[before:]:
                fact['ref'] = g.get('proj')         # the words that name the project in this sentence

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


@per_edition
def made_of(seed, index=1):
    """The module's own case (events, passages, bases), built from the random source `build_case` uses."""
    return big_minutes.build(random.Random(f'evalsuite:{generate.GENERATOR_VERSION}:{seed}:{index}:minutes'))


def readers_in(seed, index=1):
    return tuple(sorted(made_of(seed, index)['readers']))


@per_edition
def read(seed, index=1):
    book = Book(sources_of(case(seed, index)))
    count = len(book.meetings)
    runs = {'truth': run(book.facts, count, book.names)}
    for name, mode in (('counts_every_mention', ('counts_every_mention',)), ('ignores_corrections', ('ignores_corrections',)),
                       ('counts_motions_as_decisions', ('counts_motions_as_decisions',)), ('full_name_only', ('full_name_only',))):
        runs[name] = run(book.facts, count, book.names, mode, strict=False)
    return book, runs


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
    own = 0
    for key, q in queries.items():
        true = big_minutes.answer(q, events)
        closed = closed_needed_of(seed, index, key)
        alone = big_minutes.answer(q, [e for e in events if e['id'] in closed])
        need(alone == true, f'{key}: the closed needed events alone give {alone!r}, not {true!r}')
        for i in nothing_of(seed, index, key):
            need(q['kind'] in OWN_AWARD_KINDS and i in own_award_closure(events, q['subject']),
                 f'{key}: removing {i} only makes the value None')
            own += 1
    return (f'{len(queries)} statements: the needed events, closed under what fixes their names, alone give the true '
            f'value (no kind exempt); the only needed events whose removal makes the value None are the own award and what '
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
        need(big_minutes.answer(q, [e for e in events if e['id'] in closed]) == true, f'{key}: the closed needed events alone differ')
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
        need(closed <= cited and big_minutes.answer(q, [e for e in events if e['id'] in cited]) == true,
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

HAND_PROJECTS = ('Alpha Hall Repair', 'Beta Hostel Extension', 'Gamma Library Roof')


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


def check_no_running_totals(seed, index=1):
    pattern = re.compile(r'\b(to date|so far|in total|altogether|cumulative|running total|in all|payments to date)\b', re.I)
    sources = sources_of(case(seed, index))
    for name in volumes_in(sources):
        for block in sources[name].split('\n\n'):
            need(not pattern.search(block), f'a running total in {name}: {block[:120]!r}')
    return 'no running totals in the minute books'


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


def check_filters(seed, index=1):
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
# Edition 2 draws that the unit tests build: every check runs on EDITION2_FULL (a dossier of five minute books), the cheaper
# ones (size, mix, leads) on EDITION2_SEEDS too; the other draws are verified with `--verify` under BIG_MINUTES_EDITION=2.
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


def verify(seed, index, fixtures=None):
    """Run the checks on exactly one draw and, given a fixtures folder, compare the rebuilt files with it byte for
    byte. Prints one line per check; returns the number of failures. The draw is built under the edition that
    BIG_MINUTES_EDITION names (1 when it is not set), and a fixtures folder made under the other edition is refused:
    edition 2 has a fourth minute book, which edition 1 never has."""
    try:
        edition = big_minutes.current_edition()
    except generate.GenerationError as error:
        print(f'FAIL  edition: {error}')
        return 1
    if fixtures is not None:
        base = Path(fixtures)
        if not (base / 'report-correct.md').exists():
            base = base / f'g{seed}-{index:03d}-minutes'
        made_under = 2 if any((base / name).exists() for name in ('sources/minute-book-4.md', 'minute-book-4.md')) else 1
        if made_under != edition:
            print(f'FAIL  fixtures: {base} was made under edition {made_under} (it {"has" if made_under == 2 else "lacks"} '
                  f'minute-book-4.md) and {big_minutes.EDITION_VARIABLE} asks for edition {edition}: set '
                  f'{big_minutes.EDITION_VARIABLE}={made_under if made_under == 2 else "1 (or leave it unset)"} and run again')
            return 1
    failures = 0
    for name, check in CHECKS + (EDITION2_CHECKS if edition == 2 else ()):
        try:
            print(f'ok    {name}: {check(seed, index)}')
        except (AssertionError, KeyError, ValueError, IndexError) as error:
            failures += 1
            print(f'FAIL  {name}: {type(error).__name__}: {str(error)[:300]}')
    if fixtures is not None:
        built = generate.build_case('minutes', seed, index)
        differing = [n for n, text in sorted(built['files'].items())
                     if not (base / n).exists() or (base / n).read_bytes() != text.encode('utf-8')]
        if differing:
            failures += 1
            print(f'FAIL  fixtures: {len(differing)} of {len(built["files"])} files differ or are missing: {differing[:3]}')
        else:
            print(f'ok    fixtures: {len(built["files"])} files identical to {base}')
    return failures



def write_fixtures(built, parent):
    folder = Path(tempfile.mkdtemp(prefix='big-minutes-edition-', dir=str(parent)))
    for name, text in built['files'].items():
        target = folder / built['dossier'] / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
    return folder


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
        for raw, want in (('1', 1), ('2', 2)):
            with edition_env(raw):
                self.assertEqual(big_minutes.current_edition(), want)
        for raw in ('', '0', '3', '12', '2 ', ' 1', 'one', '1.0', 'two'):
            with edition_env(raw):
                with self.assertRaises(generate.GenerationError, msg=repr(raw)):
                    big_minutes.current_edition()
                with self.assertRaises(generate.GenerationError, msg=repr(raw)):
                    generate.build_case('minutes', 1, 1)
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
        import contextlib
        import io
        for made_under, asks in ((2, None), (2, 1), (1, 2)):
            with edition_env(made_under):
                folder = write_fixtures(case(1), ROOT / 'tmp')
            try:
                with edition_env(asks):
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out):
                        failures = verify(1, 1, str(folder))
                self.assertEqual(failures, 1)
                self.assertIn(f'made under edition {made_under}', out.getvalue())
                self.assertEqual(out.getvalue().count('\n'), 1, out.getvalue())          # refused before any check
            finally:
                shutil.rmtree(folder)
        with edition_env('3'):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(verify(1, 1), 1)
            self.assertIn('must be 1 or 2', out.getvalue())

    def test_edition_does_not_reach_answer(self):
        """The hand-written lists and `answer` on an edition 2 dossier are the same whatever the setting says."""
        results = []
        for setting in (None, 1, 2):
            with edition_env(setting):
                results.append(check_hand_cases())
        self.assertEqual(len(set(results)), 1)
        with edition_env(2):
            events, queries = made_of(1)['events'], statement_queries(1)
            expected = [big_minutes.answer(q, events, r) for q in queries.values() for r in (None,) + big_minutes.READERS]
            check_answers(1)
        for setting in (None, 1):
            with edition_env(setting):
                self.assertEqual([big_minutes.answer(q, events, r) for q in queries.values() for r in (None,) + big_minutes.READERS], expected)

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

if __name__ == '__main__':
    if len(sys.argv) >= 4 and sys.argv[1] == '--verify':
        raise SystemExit(1 if verify(int(sys.argv[2]), int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else None) else 0)
    unittest.main()
