"""Independent rendered-text checks for the succession family (a harbour authority's licence correspondence).

The round trip reads the rendered sources with a parser of its own that uses only the module's phrase inventory
(`big_succession.INVENTORY`: the wordings, the slot patterns, the name pools), recovers every event, and replays the
events with an evaluator of its own (the rules of procedure.md written a second time, with the three flawed
readers as the same rules with one rule replaced). It then recomputes the verdict of every statement of both
reports and of every flawed reader, and each must equal what the key says. The module's interpreter, its facts
(`case['base']`) and its predicates are not used there; they are read only by the checks that say so (the log
comparison, the passages map, the `state_on` tests).
"""
import collections
import datetime
import fractions
import functools
import hashlib
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
import generate
import generate_big
import big_succession


_FAMILIES_BEFORE = {}


def setUpModule():
    """The large families are registered for the tests of this module only; `tearDownModule` puts the list back."""
    _FAMILIES_BEFORE.update(generate.HARD_FAMILIES)
    generate_big.register()


def tearDownModule():
    generate.HARD_FAMILIES.clear()
    generate.HARD_FAMILIES.update(_FAMILIES_BEFORE)      # the list as it was found, for the modules that follow


INV = big_succession.INVENTORY
R_AGREE, R_NAMES, R_FIXES = big_succession.READERS
# the two shortcuts of each bundled reader, run alone
LITERAL_LABELS, LITERAL_NAMES = (R_NAMES, 'numbers_only'), (R_NAMES, 'names_only')
NO_FIXES, WITHDRAWN_GRANTED = (R_FIXES, 'fixes_only'), (R_FIXES, 'withdrawals_only')
BUNDLED = {R_NAMES: ('names_only', 'numbers_only'), R_FIXES: ('fixes_only', 'withdrawals_only')}
SEEDS = (1, 2, 3, 4, 5)
PRINT = os.environ.get('SUCCESSION_VERBOSE')


# ---------------------------------------------------------------------------------------------------------------
# Reading numbers, dates, names
# ---------------------------------------------------------------------------------------------------------------

MONTHS = list(INV['months'])
SHORT = list(INV['months_short'])
WORD_VALUE = {word: n for n, word in enumerate(INV['ones'])}
WORD_VALUE.update({word: 10 * n for n, word in enumerate(INV['tens']) if n > 1})


def words_to_number(text):
    total = current = 0
    for token in re.split(r'[ -]', text):
        if token == 'and':
            continue
        if token == 'hundred':
            current *= 100
        elif token == 'thousand':
            total += current * 1000
            current = 0
        else:
            current += WORD_VALUE[token]
    return total + current


def parse_amount(text):
    if text.startswith('£'):
        return int(text[1:].replace(',', ''))
    if text.endswith(' pounds'):
        body = text[:-7]
        return int(body.replace(',', '')) if re.fullmatch(r'[\d,]+', body) else words_to_number(body)
    raise ValueError(text)


def parse_date(text, year=None):
    month = '(' + '|'.join(MONTHS) + ')'
    short = '(' + '|'.join(SHORT) + ')'
    ordinal = r'(\d{1,2})(?:st|nd|rd|th)'
    forms = [(r'(\d{4})-(\d\d)-(\d\d)', lambda m: (int(m[1]), int(m[2]), int(m[3]))),
             (r'(\d{1,2}) ' + month + r' (\d{4})', lambda m: (int(m[3]), MONTHS.index(m[2]) + 1, int(m[1]))),
             (month + r' (\d{1,2}), (\d{4})', lambda m: (int(m[3]), MONTHS.index(m[1]) + 1, int(m[2]))),
             (ordinal + ' ' + month + r' (\d{4})', lambda m: (int(m[3]), MONTHS.index(m[2]) + 1, int(m[1]))),
             (r'the ' + ordinal + ' of ' + month + r' (\d{4})', lambda m: (int(m[3]), MONTHS.index(m[2]) + 1, int(m[1]))),
             (r'(\d{1,2}) ' + short + r' (\d{4})', lambda m: (int(m[3]), SHORT.index(m[2]) + 1, int(m[1]))),
             (r'(\d{1,2}) ' + month, lambda m: (year, MONTHS.index(m[2]) + 1, int(m[1]))),
             (ordinal + ' ' + month, lambda m: (year, MONTHS.index(m[2]) + 1, int(m[1])))]
    for pattern, build in forms:
        found = re.fullmatch(pattern, text)
        if found:
            y, mo, d = build(found)
            if y is None:
                raise ValueError('date without a year outside a document: ' + text)
            return f'{y:04d}-{mo:02d}-{d:02d}'
    raise ValueError('date ' + text)


def parse_small(text):
    """A count: digits, "no", or a number word; a times-phrase: "once", "twice", "3 times"."""
    token = text.split(' ')[0]
    if token in ('no', 'once', 'twice'):
        return {'no': 0, 'once': 1, 'twice': 2}[token]
    return int(token) if token.isdigit() else WORD_VALUE[token]


SLOT = re.compile(r'\{(\w+)\}')


def compile_template(template):
    parts = SLOT.split(template)
    pattern, literals, seen = '', [], set()
    for index, part in enumerate(parts):
        if index % 2:
            # a slot written twice in a template stands for the same text both times
            pattern += f'(?P={part})' if part in seen else f'(?P<{part}>{INV["slots"][part]})'
            seen.add(part)
        else:
            pattern += re.escape(part)
            literals.append(part)
    return re.compile(pattern), max(literals, key=len)


EVENT_TEMPLATES = [(kind, number, *compile_template(text))
                   for kind, texts in INV['events'].items() for number, text in enumerate(texts)]
FOLLOW_TEMPLATES = [compile_template(text) for text in INV['follow']['templates']]
ROLE_OF = {phrase: role for role, phrases in INV['follow']['roles'].items() for phrase in phrases}
DATE_END = re.compile(INV['slots']['date'] + '$')


def sentences_of(block):
    return re.split(r'(?<=\.) ', block)


def lower_first(text):
    return text[0].lower() + text[1:]


# ---------------------------------------------------------------------------------------------------------------
# The parser: sources -> events
# ---------------------------------------------------------------------------------------------------------------

GIVEN, SURNAMES, STEMS = set(INV['given_names']), set(INV['surnames']), set(INV['firm_stems'])
SUFFIXES, TITLES, RELATIONS = set(INV['firm_suffixes']), INV['titles'], set(INV['relations'])
VACANT = 'vacant'


class ParseError(Exception):
    pass


def read_documents(sources):
    docs = []
    for name in sorted(n for n in sources if n.startswith('correspondence-')):
        blocks = sources[name].rstrip('\n').split('\n\n')
        current = None
        for index, block in enumerate(blocks):
            if block.startswith('### '):
                found = DATE_END.search(block)
                current = {'file': name, 'date': parse_date(found.group(0)), 'blocks': [], 'heading': block}
                docs.append(current)
            elif current is not None:
                current['blocks'].append((index, block))
    return docs


class Parser:
    def __init__(self, sources):
        self.docs = read_documents(sources)
        self.names = {}               # party key -> names it has borne, first first
        self.by_name, self.by_surname, self.by_stem = {}, {}, {}
        self.kin, self.business = {}, {}
        self.berth_of = {}            # every number a berth has borne -> its first number
        self.events, self.resolved, self.stray, self.ambiguous = [], [], [], []
        self.berth_resolved, self.partner, self.cur_label = [], {}, {}
        self.paths = {}               # sentence index of an event -> (file, block index)
        self.sentence_texts = []
        self.parse()

    # -- parties
    def register(self, name, key=None):
        if name in self.by_name:
            raise ParseError('name borne twice: ' + name)
        key = key or name
        self.names.setdefault(key, []).append(name)
        self.by_name[name] = key
        words = name.split(' ')
        if words[0] in STEMS:
            if self.by_stem.setdefault(words[0], key) != key:
                raise ParseError('two firms share the stem ' + words[0])
        elif len(words) == 2 and words[1] in SURNAMES:
            if self.by_surname.setdefault(words[1], key) != key:
                raise ParseError('two persons share the surname ' + words[1])
        return key

    def party(self, text, doc_block, forms=None, focus=None, now=None):
        """The party key a mention names; every mention must name exactly one party."""
        text = lower_first(text) if text.startswith('The ') else text
        key, form = self.resolve(text, focus or {})
        self.resolved.append({'block': doc_block, 'text': text, 'key': key, 'form': form, 'event': len(self.events)})
        return key

    def resolve(self, text, focus):
        found = re.fullmatch(r'the only ([a-z]+) of (?:the late )?(.+)', text)
        if found:
            anchor, _ = self.resolve(found[2], {})
            if (anchor, found[1]) not in self.kin:
                raise ParseError('no such kin: ' + text)
            return self.kin[(anchor, found[1])], 'kin'
        found = re.fullmatch(r'the (purchaser|buyer|new owner|seller|former owner) of the ([a-z-]+) business', text)
        if found:
            side = 'buyer' if found[1] in ('purchaser', 'buyer', 'new owner') else 'seller'
            return self.business[(found[2], side)], 'trade'
        found = re.fullmatch(r'the (?:present |then )?(?:holder|licensee) of berth (\S+)', text)
        if found:
            holder = Book(self.events).holder_now(self.berth_of[found[1]])
            if holder in (None, VACANT):
                raise ParseError('holder description without a holder: ' + text)
            return holder, 'holder'
        if text in ROLE_OF:
            if ROLE_OF[text] not in focus:
                raise ParseError('role without a referent: ' + text)
            return focus[ROLE_OF[text]], 'role'
        found = re.fullmatch(r'(?:(' + '|'.join(TITLES) + r') )?([A-Z][a-z]+) ([A-Z][a-z]+)', text)
        if found and found[2] in GIVEN and found[3] in SURNAMES:
            name = f'{found[2]} {found[3]}'
            return (self.by_name[name] if name in self.by_name else self.register(name)), 'full'
        found = re.fullmatch(r'(' + '|'.join(TITLES) + r') ([A-Z][a-z]+)', text)
        if found and found[2] in SURNAMES:
            return self.by_surname[found[2]], 'short'
        if text in STEMS:
            return self.by_stem[text], 'short'
        words = text.split(' ', 1)
        if words[0] in STEMS and len(words) == 2 and words[1] in SUFFIXES:
            return (self.by_name[text] if text in self.by_name else self.register(text)), 'full'
        raise ParseError('cannot resolve a mention: ' + text)

    # -- berths
    def berth(self, label, new_ok=False):
        if label not in self.berth_of:
            if not new_ok:
                raise ParseError('unknown berth number ' + label)
            self.berth_of[label] = label
        return self.berth_of[label]

    def bref(self, text, where, focus):
        """The number a berth reference stands for at this point of the file: the number written, or, for a
        description, the number the described berth carries now. Descriptions name exactly one berth."""
        text = lower_first(text)
        found = re.fullmatch(r'berth (\S+)', text)
        if found:
            return found[1]
        found = re.fullmatch(r'the berth next to berth (\S+)', text)
        if found:
            other = self.berth(found[1])
            if other not in self.partner:
                raise ParseError('no berth next to ' + text)
            key = self.partner[other]
        else:
            found = re.fullmatch(r'the berth held by (.+)', text)
            party = self.party(found[1], where, focus=focus)
            held = [k for k, holder, _ in Book(self.events).holding() if holder == party]
            if len(held) != 1:
                raise ParseError(f'{len(held)} berths held by {party}')
            key = held[0]
        self.berth_resolved.append({'block': where, 'text': text, 'key': key})
        return self.cur_label.get(key, key)

    # -- reading
    def parse(self):
        for doc in self.docs:
            for block in doc['blocks']:
                self.read_block(doc, block[0], block[1])

    def read_block(self, doc, index, text):
        focus = {}
        for sentence in sentences_of(text):
            hits = [(kind, number, match) for kind, number, rx, needle in EVENT_TEMPLATES
                    if needle in sentence for match in [rx.fullmatch(sentence)] if match]
            follow = [m for rx, needle in FOLLOW_TEMPLATES if needle in sentence for m in [rx.fullmatch(sentence)] if m]
            if len(hits) + len(follow) > 1:
                self.ambiguous.append(sentence)
            if follow and not hits:
                text_role = lower_first(follow[0]['role'])
                self.resolved.append({'block': (doc['file'], index), 'text': text_role,
                                      'key': self.resolve(text_role, focus)[0], 'form': 'role',
                                      'event': len(self.events) - 1})
                continue
            if not hits:
                self.stray.append((doc['file'], index, sentence))
                continue
            kind, number, match = hits[0]
            event = self.handle(kind, match.groupdict(), doc, (doc['file'], index), focus)
            event.update(idx=len(self.events), date=doc['date'], block=(doc['file'], index), sentence=sentence,
                         slots=match.groupdict())
            self.events.append(event)

    def d(self, text, doc):
        return parse_date(text, int(doc['date'][:4]))

    def find(self, kind, berth, **match):
        found = [e for e in self.events if e['kind'] == kind and e.get('berth') == berth and
                 all(e.get(k) == v for k, v in match.items())]
        if len(found) != 1:
            raise ParseError(f'{len(found)} {kind} events for {berth} {match}')
        return found[0]['idx']

    def handle(self, kind, g, doc, where, focus):
        P = lambda text, **kw: self.party(text, where, focus=focus, **kw)  # noqa: E731
        D = lambda text: self.d(text, doc)  # noqa: E731
        ev = {'kind': kind.split('_')[0]}
        if kind in ('pair', 'renumber', 'split', 'join'):
            g = dict(g, berth=g['num'])
        elif 'berth' in g:
            ev['described'] = not re.fullmatch(r'[Bb]erth \S+', g['berth'])
            g = dict(g, berth=self.bref(g['berth'], where, focus))
        if kind == 'grant':
            new = g['berth'] not in self.berth_of
            ev.update(berth=self.berth(g['berth'], True), label=g['berth'], party=P(g['party']), new=new)
            self.cur_label.setdefault(ev['berth'], g['berth'])
            focus.clear()
            focus['holder'] = ev['party']
        elif kind.startswith('fee_'):
            spec = {'abs': lambda: {'type': 'abs', 'amount': parse_amount(g['amount'])},
                    'rel': lambda: {'type': 'rel', 'num': INV['relative'][g['rel']][0],
                                    'den': INV['relative'][g['rel']][1]},
                    'same': lambda: {'type': 'ref', 'target': 'berth', 'other': self.berth(g['berth2']),
                                     'other_label': g['berth2'], 'num': 1, 'den': 1},
                    'frac': lambda: {'type': 'ref', 'target': 'berth', 'other': self.berth(g['berth2']),
                                     'other_label': g['berth2'], 'num': INV['fractions'][g['frac']][0],
                                     'den': INV['fractions'][g['frac']][1]},
                    'adjoin': lambda: {'type': 'ref', 'target': 'adjoin', 'other': None, 'other_label': None,
                                       'num': 1, 'den': 1},
                    'parent': lambda: {'type': 'ref', 'target': 'parent', 'other': None, 'other_label': None,
                                       'num': INV['parent_fractions'][g['pfrac']][0],
                                       'den': INV['parent_fractions'][g['pfrac']][1]}}[kind[4:]]()
            ev.update(kind='fee', berth=self.berth(g['berth']), label=g['berth'], year=int(g['year']), spec=spec)
        elif kind == 'pair':
            ev.update(berths=[self.berth(g['berth']), self.berth(g['berth2'])], labels=[g['berth'], g['berth2']])
            self.partner[ev['berths'][0]], self.partner[ev['berths'][1]] = ev['berths'][1], ev['berths'][0]
        elif kind == 'agree':
            ev.update(berth=self.berth(g['berth']), label=g['berth'], assignor=P(g['party']), assignee=P(g['party2']))
            focus.clear()
            focus.update(assignor=ev['assignor'], assignee=ev['assignee'])
        elif kind == 'register':
            agreed = D(g['agreed'])
            target = self.find('agree', self.berth(g['berth']), date=agreed)
            ev.update(berth=self.berth(g['berth']), label=g['berth'], when=D(g['date']), ref=target)
            focus.clear()
            focus.update(assignor=self.events[target]['assignor'], assignee=self.events[target]['assignee'])
        elif kind in ('withdraw', 'refuse'):
            ev.update(kind=kind, berth=self.berth(g['berth']), label=g['berth'],
                      ref=self.find('agree', self.berth(g['berth']), date=D(g['agreed'])))
        elif kind == 'death':
            ev.update(deceased=P(g['party']), executor=P(g['party2']), when=D(g['date']))
            focus.clear()
            focus.update(executor=ev['executor'], deceased=ev['deceased'])
        elif kind == 'estate':
            ev.update(berth=self.berth(g['berth']), label=g['berth'], heir=P(g['party']), when=D(g['date']))
            focus.clear()
            focus['holder'] = ev['heir']
        elif kind == 'rename':
            old = P(g['old'])
            self.register(g['name'], old)
            ev.update(party=old, name=g['name'], when=D(g['date']))
            focus.clear()
            focus['firm'] = old
        elif kind == 'merge':
            a, b = P(g['party']), P(g['party2'])
            new = self.register(g['name'])
            ev.update(merged=[a, b], party=new, name=g['name'], when=D(g['date']))
            focus.clear()
            focus['merged'] = new
        elif kind in ('surrender', 'forfeit'):
            ev.update(kind=kind, party=P(g['party']), berth=self.berth(g['berth']), label=g['berth'],
                      when=D(g['date']))
        elif kind == 'renumber':
            berth = self.berth(g['berth'])
            if g['berth2'] in self.berth_of:
                raise ParseError('number borne twice ' + g['berth2'])
            self.berth_of[g['berth2']] = berth
            self.cur_label[berth] = g['berth2']
            ev.update(berth=berth, old=g['berth'], label=g['berth2'], when=D(g['date']))
        elif kind == 'split':
            parent = self.berth(g['berth'])
            kids = []
            for label, text in ((g['berth2'], g['hold1']), (g['berth3'], g['hold2'])):
                holder = VACANT if text.lower() == 'nobody' else self.party_in_berth(text, where, focus, parent)
                kids.append({'berth': self.berth(label, True), 'label': label, 'holder': holder})
            ev.update(berth=parent, label=g['berth'], children=kids, when=D(g['date']))
            self.partner.pop(self.partner.pop(parent, None), None)
            for kid in kids:
                self.cur_label[kid['berth']] = kid['label']
        elif kind == 'join':
            x, y = self.berth(g['berth']), self.berth(g['berth2'])
            survivor = self.berth(g['berth3'])
            if survivor not in (x, y):
                raise ParseError('survivor is neither berth')
            holder = VACANT if g['hold'].lower() == 'nobody' else self.party_in_berth(g['hold'], where, focus, x, y)
            ev.update(berths=[x, y], labels=[g['berth'], g['berth2']], survivor=survivor, label=g['berth3'],
                      holder=holder, when=D(g['date']))
            for berth in (x, y):
                self.partner.pop(self.partner.pop(berth, None), None)
        elif kind.startswith('fix_'):
            berth = self.berth(g['berth'])
            ev.update(kind='fix', berth=berth, label=g['berth'])
            if kind == 'fix_amount':
                ev.update(field='amount', value=parse_amount(g['amount']),
                          ref=self.find('fee', berth, date=D(g['entered'])))
            elif kind == 'fix_assignee':
                ev.update(field='assignee', value=P(g['party']), wrong=P(g['party2']), agreed=D(g['agreed']),
                          ref=self.find('agree', berth, date=D(g['agreed'])))
            else:
                ev.update(field='when', value=D(g['correct']), entry=D(g['entry']),
                          ref=self.find('register', berth, when=D(g['entry'])))
        elif kind == 'decoy':
            ev.update(party=P(g['party']))
            if 'num' in g:
                ev.update(berth=self.berth(g['num']), label=g['num'])
        elif kind == 'kin':
            person, anchor = P(g['party']), P(g['party2'])
            if (anchor, g['relation']) in self.kin:
                raise ParseError('two only ' + g['relation'])
            self.kin[(anchor, g['relation'])] = person
            ev.update(person=person, anchor=anchor, relation=g['relation'])
        elif kind == 'bought':
            buyer, seller = P(g['party']), P(g['party2'])
            for side, who in (('buyer', buyer), ('seller', seller)):
                if (g['trade'], side) in self.business:
                    raise ParseError('business sold twice')
                self.business[(g['trade'], side)] = who
            ev.update(buyer=buyer, seller=seller, trade=g['trade'])
        else:
            raise ParseError('unhandled ' + kind)
        return ev

    def party_in_berth(self, text, where, focus, *berths):
        """A holder slot of a split or joining: a mention that may be the holder of one of the berths."""
        return self.party(text, where, focus=focus)


# ---------------------------------------------------------------------------------------------------------------
# The evaluator: procedure.md written a second time. `reader` replaces exactly one rule.
# ---------------------------------------------------------------------------------------------------------------

class Book:
    def __init__(self, events, reader=None):
        self.reader = reader
        self.moves = collections.defaultdict(list)     # berth key -> (date, order, holder, deciding events)
        self.rows = collections.defaultdict(list)      # berth key -> (year, amount or None, deciding events)
        self.life = {}                                 # berth key -> dict(born, end, into, kids, labels)
        self.renamed = collections.defaultdict(list)
        self.partner, self.agreements, self.order = {}, {}, 0
        fixes, self.fixed_by, closed = {}, collections.defaultdict(list), set()
        for ev in events:
            if ev['kind'] == 'fix' and reader not in NO_FIXES:
                fixes.setdefault(ev['ref'], {})[ev['field']] = ev['value']
                self.fixed_by[ev['ref']].append(ev['idx'])
            if ev['kind'] in ('withdraw', 'refuse'):
                closed.add(ev['ref'])
        self.closed = closed
        for ev in events:
            if ev['kind'] == 'fix':
                continue
            ev = dict(ev)
            for field, value in fixes.get(ev['idx'], {}).items():
                if field == 'amount':
                    ev['spec'] = {'type': 'abs', 'amount': value}
                else:
                    ev[field] = value
            self.step(ev)

    def bk(self, berth, label):
        return ('L', label) if self.reader in LITERAL_LABELS else berth

    def why(self, ev):
        return frozenset({ev['idx']} | set(self.fixed_by.get(ev['idx'], ())))

    def lit(self, party, when):
        if self.reader in LITERAL_NAMES and party != VACANT and any(w <= when for w in self.renamed[party]):
            return party + '*'
        return party

    def put(self, key, when, holder, why):
        if key not in self.life:
            self.born(key, when, key[1] if isinstance(key, tuple) else key)
        self.order += 1
        if self.moves[key] and when < self.moves[key][-1][0]:
            when = self.moves[key][-1][0]       # an entry written later never takes effect before an earlier one
        self.moves[key].append((when, self.order, holder, why))

    def born(self, key, when, label):
        self.life[key] = {'born': when, 'end': None, 'into': None, 'kids': [], 'labels': [label]}

    def touch(self, key, when, label):
        """A berth the events never created (the literal-number reader meets numbers it never saw)."""
        if key not in self.life:
            self.born(key, when, label)
        return self.life[key]

    def holder_now(self, key):
        return self.moves[key][-1][2] if self.moves.get(key) else None

    def holding(self):
        return [(k, self.moves[k][-1][2], self.moves[k][-1][3]) for k in self.life
                if self.life[k]['end'] is None and self.moves.get(k)]

    def fee_in(self, key, year):
        found = [row for row in self.rows.get(key, ()) if row[0] <= year]
        return found[-1] if found else None

    def step(self, ev):
        kind, when = ev['kind'], ev.get('when', ev['date'])
        why = self.why(ev)
        if kind == 'grant':
            key = self.bk(ev['berth'], ev['label'])
            if ev['new'] or key not in self.life:
                self.born(key, when, ev['label'])
            self.put(key, when, self.lit(ev['party'], when), why)
        elif kind == 'fee':
            key, spec = self.bk(ev['berth'], ev['label']), ev['spec']
            fraction = lambda amount: None if amount is None else amount * fractions.Fraction(spec['num'], spec['den'])  # noqa: E731,E501
            if spec['type'] == 'abs':
                amount = fractions.Fraction(spec['amount'])
            elif spec['type'] == 'rel':
                last = self.rows[key][-1] if self.rows.get(key) else None
                amount = None if last is None or last[1] is None else last[1] * fractions.Fraction(
                    spec['den'] + spec['num'], spec['den'])
                why |= last[2] if last else frozenset()
            else:
                other = (self.bk(spec['other'], spec['other_label']) if spec['target'] == 'berth' else
                         self.partner.get(key) if spec['target'] == 'adjoin' else
                         self.life.get(key, {}).get('parent'))
                row = self.fee_in(other, ev['year']) if other is not None else None
                amount = fraction(row[1]) if row else None
                why |= row[2] if row else frozenset()
            self.rows[key].append((ev['year'], amount, why))
        elif kind == 'pair':
            a, b = (self.bk(x, y) for x, y in zip(ev['berths'], ev['labels']))
            self.partner[a], self.partner[b] = b, a
        elif kind == 'agree':
            self.agreements[ev['idx']] = (ev['assignee'], why)
            transfer = (self.reader == R_AGREE and ev['idx'] not in self.closed) or \
                       (self.reader in WITHDRAWN_GRANTED and ev['idx'] in self.closed)
            if transfer:
                self.put(self.bk(ev['berth'], ev['label']), ev['date'], self.lit(ev['assignee'], ev['date']), why)
        elif kind == 'register' and self.reader != R_AGREE:
            assignee, basis = self.agreements[ev['ref']]
            self.put(self.bk(ev['berth'], ev['label']), when, self.lit(assignee, when), why | basis)
        elif kind == 'death':
            for key, holder, basis in self.holding():
                if holder.rstrip('*') == ev['deceased']:
                    self.put(key, when, self.lit(ev['executor'], when), why | basis)
        elif kind == 'merge':
            for key, holder, basis in self.holding():
                if holder.rstrip('*') in ev['merged']:
                    self.put(key, when, self.lit(ev['party'], when), why | basis)
        elif kind == 'estate':
            self.put(self.bk(ev['berth'], ev['label']), when, self.lit(ev['heir'], when), why)
        elif kind == 'rename':
            self.renamed[ev['party']].append(when)
        elif kind in ('surrender', 'forfeit'):
            self.put(self.bk(ev['berth'], ev['label']), when, VACANT, why)
        elif kind == 'renumber' and self.reader not in LITERAL_LABELS:
            life = self.touch(ev['berth'], when, ev['old'])
            life['labels'].append(ev['label'])
            life.setdefault('why', set()).add(ev['idx'])
        elif kind == 'split':
            parent = self.bk(ev['berth'], ev['label'])
            self.touch(parent, when, ev['label'])
            self.life[parent]['end'], self.life[parent]['basis'] = when, why
            for other in (self.partner.pop(parent, None),):
                self.partner.pop(other, None)
            for kid in ev['children']:
                key = self.bk(kid['berth'], kid['label'])
                self.born(key, when, kid['label'])
                self.life[key]['parent'] = parent
                self.life[parent]['kids'].append(key)
                self.put(key, when, kid['holder'] if kid['holder'] == VACANT else self.lit(kid['holder'], when), why)
        elif kind == 'join':
            survivor = self.bk(ev['survivor'], ev['label'])
            if survivor not in self.life:
                self.born(survivor, when, ev['label'])
            self.put(survivor, when, ev['holder'] if ev['holder'] == VACANT else self.lit(ev['holder'], when), why)
            for berth, label in zip(ev['berths'], ev['labels']):
                key = self.bk(berth, label)
                for other in (self.partner.pop(key, None),):
                    self.partner.pop(other, None)
                if key != survivor:
                    self.touch(key, when, label).update(end=when, into=survivor, basis=why)

    # -- answers (None where not established); a "key" is the berth's first number in the statements
    def exists(self, key, date):
        life = self.life.get(key)
        return bool(life) and life['born'] <= date and (life['end'] is None or date < life['end'])

    def holder(self, key, date):
        if not self.exists(key, date):
            return None
        found = [m for m in self.moves[key] if m[0] <= date]
        return found[-1][2] if found else None

    def holder_why(self, key, date):
        found = [m for m in self.moves[key] if m[0] <= date]
        return found[-1][3] if found else frozenset()

    def fee(self, key, year):
        row = self.fee_in(key, year)
        return int(row[1]) if row and row[1] is not None and row[1].denominator == 1 else None

    def count(self, party, date):
        return sum(self.holder(k, date) == party for k in self.life if self.exists(k, date))

    def changes(self, key, d1, d2):
        moves, total = self.moves.get(key, []), 0
        for before, after in zip(moves, moves[1:]):
            if d1 < after[0] <= d2 and after[2] != before[2]:
                total += 1
        return total

    def first_held(self, key, party):
        found = [m[0] for m in self.moves.get(key, []) if m[2] == party]
        return found[0] if found else None

    def part(self, key):
        """(present number, deciding events) of the one present berth the berth is or is part of."""
        ends, why = set(), set()

        def walk(k):
            life = self.life[k]
            if life['end'] is None:
                ends.add(k)
                why.update(life.get('why', ()))
            else:
                why.update(life.get('basis', ()))
                if life['into'] is not None:
                    walk(life['into'])
                else:
                    for kid in life['kids']:
                        walk(kid)
        if key not in self.life:
            return None, frozenset()
        walk(key)
        if len(ends) != 1:
            return None, frozenset()
        return self.life[next(iter(ends))]['labels'][-1], frozenset(why)


# ---------------------------------------------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------------------------------------------

STATEMENT_TEMPLATES = [(kind, *compile_template(text)) for kind, texts in INV['statements'].items() for text in texts]


def parse_statement(parser, text):
    hits = [(kind, rx.fullmatch(text)) for kind, rx, needle in STATEMENT_TEMPLATES if needle in text]
    hits = [(kind, m) for kind, m in hits if m]
    if len(hits) != 1:
        raise ParseError(f'{len(hits)} readings of the statement {text}')
    kind, g = hits[0][0], hits[0][1].groupdict()
    parse = lambda t: parse_date(t)  # noqa: E731
    if 'num' in g:
        g['berth'] = g['num']
        if parser.berth_of.get(g['berth']) != g['berth']:
            raise ParseError('statement names a berth by a later number: ' + g['berth'])
    if 'party' in g and g['party'] not in parser.names:
        raise ParseError('statement names a party by other than its first name: ' + g['party'])
    if kind == 'holder':
        return dict(kind=kind, berth=g['berth'], date=parse(g['date']), value=g['party'])
    if kind == 'fee':
        return dict(kind=kind, berth=g['berth'], year=int(g['year']), value=parse_amount(g['amount']))
    if kind == 'count':
        return dict(kind=kind, party=g['party'], date=parse(g['date']), value=parse_small(g['count']))
    if kind == 'changes':
        return dict(kind=kind, berth=g['berth'], d1=parse(g['d1']), d2=parse(g['d2']), value=parse_small(g['times']))
    if kind == 'part':
        return dict(kind=kind, berth=g['berth'], value=g['label'])
    return dict(kind='first_held', berth=g['berth'], party=g['party'], value=parse(g['date']))


def compute(book, s):
    """(value the book gives, deciding events) for a parsed statement."""
    key = ('L', s.get('berth')) if book.reader in LITERAL_LABELS else s.get('berth')
    kind = s['kind']
    if kind == 'holder':
        return book.holder(key, s['date']), book.holder_why(key, s['date']) if book.exists(key, s['date']) else set()
    if kind == 'fee':
        row = book.fee_in(key, s['year'])
        return book.fee(key, s['year']), row[2] if row else frozenset()
    if kind == 'count':
        why = set()
        for k in book.life:
            if book.exists(k, s['date']) and book.holder(k, s['date']) == s['party']:
                why |= book.holder_why(k, s['date'])
        return book.count(s['party'], s['date']), why
    if kind == 'changes':
        moves = book.moves.get(key, [])
        why = set()
        for before, after in zip(moves, moves[1:]):
            if s['d1'] < after[0] <= s['d2'] and after[2] != before[2]:
                why |= after[3]
        return book.changes(key, s['d1'], s['d2']), why
    if kind == 'first_held':
        found = [m for m in book.moves.get(key, []) if m[2] == s['party']]
        return book.first_held(key, s['party']), found[0][3] if found else frozenset()
    return book.part(key)


def report_statements(text):
    return re.findall(r'\*\*C\d+\.\*\* (.*?) Citations?:', text)


@functools.lru_cache(maxsize=None)
def built(seed, index=1):
    return generate.build_case('succession', seed, index)


@functools.lru_cache(maxsize=None)
def module_case(seed, index=1):
    return big_succession.build(random.Random(f'evalsuite:{generate.GENERATOR_VERSION}:{seed}:{index}:succession'))


@functools.lru_cache(maxsize=None)
def parsed(seed, index=1):
    sources = {n[8:]: t for n, t in built(seed, index)['files'].items() if n.startswith('sources/')}
    parser = Parser(sources)
    books = {None: Book(parser.events)}
    for name in list(big_succession.READERS) + [p for parts in BUNDLED.values() for p in parts]:
        books[name] = Book(parser.events, name)
    return sources, parser, books


def rows_of(seed, index=1):
    key = built(seed, index)['key']['expectations']
    return {variant: [r for r in key if r['variant'] == variant] for variant in ('correct', 'mixed')}


def verdict(book, statement):
    return 'supported' if compute(book, statement)[0] == statement['value'] else 'refuted'


# ---------------------------------------------------------------------------------------------------------------
# The checks that `verify` runs on any draw (and the tests run on seeds 1 to 5)
# ---------------------------------------------------------------------------------------------------------------

def check_round_trip(seed, index=1):
    _, parser, books = parsed(seed, index)
    assert parser.ambiguous == [], parser.ambiguous[:2]
    for variant, items in rows_of(seed, index).items():
        texts = report_statements(built(seed, index)['files'][f'report-{variant}.md'])
        assert len(texts) == len(items) == 24, (variant, len(texts))
        for text, row in zip(texts, items):
            assert text == row['claim_text'], text
            statement = parse_statement(parser, text)
            assert verdict(books[None], statement) == row['semantic_state'], (variant, text)
            for name in big_succession.READERS:
                expected = row['lure_states'].get(name, row['semantic_state'])
                assert verdict(books[name], statement) == expected, (variant, name, text)


def check_one_referent(seed, index=1):
    _, parser, _ = parsed(seed, index)
    case = module_case(seed, index)
    name = {k: v['name'] for k, v in case['parties'].items()}
    by_block = collections.defaultdict(list)
    for item in parser.resolved:
        by_block[item['block']].append(item)
    forms = collections.Counter()
    for log in case['events']:
        where = None
        for entry in case['passages']:
            if log['id'] in entry['event_ids']:
                where = (entry['file'], entry['paragraph'])
        for record in case['mentions'].get(log['id'], ()):
            if not record.get('party'):
                continue
            texts = [i for i in by_block[where] if i['text'] in (lower_first(record['text']), record['text'])]
            assert texts, (log['id'], record)
            assert any(t['key'] == name[record['party']] for t in texts), record
            forms[record['form']] += 1
    for needed in ('full', 'short', 'holder'):
        assert forms[needed] > 0, (needed, dict(forms))
    assert 1 - forms['full'] / sum(forms.values()) > 0.4, dict(forms)
    return forms


def claims_by_text(case):
    return {c['text']: c for true, mutations in case['slots'] for c in [true] + mutations}


def check_answer_matches_key(seed, index=1):
    """`answer(query, events, reader)` equals the key for every statement of both reports and every lure state."""
    case = module_case(seed, index)
    claims = claims_by_text(case)
    for row in built(seed, index)['key']['expectations']:
        query = claims[row['claim_text']]['query']
        form = big_succession.value_form(query)
        assert form in ('integer', 'decimal', 'date', 'month', 'name') or form[0] == 'choice', form
        def state(reader):
            value = big_succession.answer(query, case['events'], reader)
            assert value is not None or reader is not None     # a reader may establish nothing: refuted
            return 'supported' if value == query['value'] else 'refuted'
        assert state(None) == row['semantic_state'], row['claim_text']
        for name in big_succession.READERS:
            assert state(name) == row['lure_states'].get(name, row['semantic_state']), (name, row['claim_text'])



# ---------------------------------------------------------------------------------------------------------------
# Small event lists written by hand. The two blocks below are the files hand_cases_written_before_run.py and
# hand_case_addendum_written_before_run.py and hand_cases_naming_written_before_run.py and hand_cases_review_written_before_run.py of the work, copied byte for byte (their hashes: see the report).
# ---------------------------------------------------------------------------------------------------------------

# Small event lists written by hand from procedure.md's rules and the subset rule of the checked-answers spec
# (section 3.3), BEFORE the changed interpreter was written or run on them. Each case gives its events and, for each
# statement: the true value (public form) and the needed set (the events whose removal from the list changes the value,
# to another value, to 'none' or to None). A `subset` gives the value the rule says for a statement on part of a list
# (None where the statement's own starting point is absent).
# Event tuple: (id, kind, date, fields). `seq` is the position; `when` is the date unless given.
CAST = {'P01': 'Clara Hartley', 'P02': 'Gaff Marine', 'P03': 'Arthur Hale', 'P04': 'Anchorage Rowing Club',
        'P05': 'Spindrift Marine', 'P06': 'Siskin Angling Society', 'P07': 'Alaric Kirkby', 'P08': 'Kingfisher Shipping'}

def holder(b, label, date): return dict(kind='holder', berth=b, label=label, date=date)
def fee(b, label, year): return dict(kind='fee', berth=b, label=label, year=year)
def count(p, date): return dict(kind='count', party=p, date=date)
def changes(b, label, d1, d2): return {'kind': 'changes', 'berth': b, 'label': label, 'from': d1, 'to': d2}
def part(b, label): return dict(kind='part', berth=b, label=label)
def first(b, label, p): return dict(kind='first_held', berth=b, label=label, party=p)
def abs_fee(n): return {'type': 'abs', 'amount': n}

HAND_CASES = [
 dict(name='assignment agreed and registered', events=[
        ('g', 'grant', '2016-01-10', dict(berth='B1', label='10', party='P01', new=True)),
        ('f', 'fee', '2016-01-10', dict(berth='B1', label='10', year=2016, spec=abs_fee(1000))),
        ('a', 'agree', '2016-03-01', dict(berth='B1', label='10', assignor='P01', assignee='P02')),
        ('r', 'register', '2016-04-12', dict(berth='B1', label='10', ref='a', when='2016-04-05'))],
      queries=[(holder('B1', '10', '2016-04-05'), 'Gaff Marine', {'g', 'a', 'r'}),
               (holder('B1', '10', '2016-04-04'), 'Clara Hartley', {'g'}),
               (fee('B1', '10', 2016), 1000, {'g', 'f'})],
      subsets=[('register note without its agreement', ['g', 'f', 'r'], holder('B1', '10', '2016-04-05'), 'Clara Hartley'),
               ('no grant', ['f', 'a', 'r'], holder('B1', '10', '2016-04-05'), None),
               ('no fee entry', ['g', 'a', 'r'], fee('B1', '10', 2016), 'none')]),
 dict(name='agreed and never registered', events=[
        ('g', 'grant', '2016-01-10', dict(berth='B2', label='20', party='P01', new=True)),
        ('a', 'agree', '2016-03-01', dict(berth='B2', label='20', assignor='P01', assignee='P02'))],
      queries=[(holder('B2', '20', '2016-12-31'), 'Clara Hartley', {'g'}),
               (changes('B2', '20', '2016-01-10', '2016-12-31'), 0, {'g'})],
      subsets=[]),
 dict(name='withdrawn', events=[
        ('g', 'grant', '2016-01-10', dict(berth='B3', label='30', party='P01', new=True)),
        ('a', 'agree', '2016-03-01', dict(berth='B3', label='30', assignor='P01', assignee='P02')),
        ('w', 'withdraw', '2016-04-01', dict(berth='B3', label='30', ref='a'))],
      queries=[(holder('B3', '30', '2016-12-31'), 'Clara Hartley', {'g'})],
      subsets=[('withdrawal of an absent application', ['g', 'w'], holder('B3', '30', '2016-12-31'), 'Clara Hartley')]),
 dict(name='renamed firm', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B4', label='40', party='P02', new=True)),
        ('n', 'rename', '2016-06-01', dict(party='P02', name='Egret Brothers')),
        ('a', 'agree', '2016-09-01', dict(berth='B4', label='40', assignor='P02', assignee='P03', needs=['n'])),
        ('r', 'register', '2016-10-05', dict(berth='B4', label='40', ref='a', when='2016-10-01'))],
      queries=[(holder('B4', '40', '2016-12-31'), 'Arthur Hale', {'g', 'n', 'a', 'r'}),
               (holder('B4', '40', '2016-07-01'), 'Gaff Marine', {'g'})],
      subsets=[('the renaming behind the assignor missing', ['g', 'a', 'r'], holder('B4', '40', '2016-12-31'), 'Gaff Marine')]),
 dict(name='death with an executor named by kinship', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B5', label='50', party='P01', new=True)),
        ('k', 'kin', '2016-02-01', dict(person='P07', anchor='P01', relation='nephew')),
        ('d', 'death', '2016-08-20', dict(deceased='P01', executor='P07', when='2016-08-10', needs=['k']))],
      queries=[(holder('B5', '50', '2016-12-31'), 'Alaric Kirkby', {'g', 'k', 'd'}),
               (count('P07', '2016-12-31'), 1, {'g', 'k', 'd'}),
               (count('P01', '2016-07-01'), 1, {'g'})],
      subsets=[('kin passage missing: the death is left out', ['g', 'd'], holder('B5', '50', '2016-12-31'), 'Clara Hartley'),
               ('party named, no berth', ['k', 'd'], count('P07', '2016-12-31'), 0),
               ('party never named', ['g', 'k'], count('P02', '2016-12-31'), None)]),
 dict(name='berth renumbered', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B6', label='60', party='P01', new=True)),
        ('n', 'renumber', '2016-05-10', dict(berth='B6', old='60', label='65', when='2016-05-01')),
        ('a', 'agree', '2016-06-01', dict(berth='B6', label='65', assignor='P01', assignee='P02', needs=['n'])),
        ('r', 'register', '2016-07-05', dict(berth='B6', label='65', ref='a', when='2016-07-01', needs=['n']))],
      queries=[(holder('B6', '60', '2016-12-31'), 'Gaff Marine', {'g', 'n', 'a', 'r'}),
               (part('B6', '60'), '65', {'g', 'n'})],
      subsets=[('renumbering missing: what is written under the new number is left out', ['g', 'a', 'r'], holder('B6', '60', '2016-12-31'), 'Clara Hartley'),
               ('renumbering missing: the berth keeps its number', ['g', 'a', 'r'], part('B6', '60'), '60')]),
 dict(name='berth split', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B7', label='70', party='P01', new=True)),
        ('f7', 'fee', '2016-01-05', dict(berth='B7', label='70', year=2016, spec=abs_fee(2000))),
        ('s', 'split', '2016-06-15', dict(berth='B7', label='70', when='2016-06-10',
              children=[dict(berth='B8', label='70A', holder='P02'), dict(berth='B9', label='70B', holder='P01')])),
        ('f8', 'fee', '2017-01-15', dict(berth='B8', label='70A', year=2017,
              spec={'type': 'ref', 'target': 'parent', 'other': None, 'other_label': None, 'num': 1, 'den': 2}))],
      queries=[(holder('B8', '70A', '2016-12-31'), 'Gaff Marine', {'s'}),
               (fee('B8', '70A', 2017), 1000, {'s', 'f7', 'f8'})],
      subsets=[('parent fee missing: the derived fee is left out', ['g', 's', 'f8'], fee('B8', '70A', 2017), 'none'),
               ('no split: the child never appears', ['g', 'f7', 'f8'], holder('B8', '70A', '2016-12-31'), None)]),
 dict(name='two berths joined', events=[
        ('g10', 'grant', '2016-01-05', dict(berth='B10', label='100', party='P01', new=True)),
        ('g11', 'grant', '2016-01-06', dict(berth='B11', label='101', party='P02', new=True)),
        ('j', 'join', '2016-09-01', dict(berths=['B10', 'B11'], labels=['100', '101'], survivor='B10', label='100', holder='P03'))],
      queries=[(part('B11', '101'), '100', {'g11', 'j'}),
               (holder('B10', '100', '2016-12-31'), 'Arthur Hale', {'g10', 'j'}),
               (count('P03', '2016-12-31'), 1, {'g10', 'j'})],
      subsets=[('no grant of the surviving berth: the join still says what the berth is', ['g11', 'j'], part('B11', '101'), '100')]),
 dict(name='fee equal to the adjoining berth, written by number', events=[
        ('g12', 'grant', '2016-01-05', dict(berth='B12', label='120', party='P01', new=True)),
        ('g13', 'grant', '2016-01-06', dict(berth='B13', label='121', party='P02', new=True)),
        ('p', 'pair', '2016-02-01', dict(berths=['B12', 'B13'], labels=['120', '121'])),
        ('f12', 'fee', '2016-02-02', dict(berth='B12', label='120', year=2016, spec=abs_fee(1500))),
        ('f13', 'fee', '2016-02-03', dict(berth='B13', label='121', year=2016,
              spec={'type': 'ref', 'target': 'adjoin', 'other': None, 'other_label': None, 'num': 1, 'den': 1}))],
      queries=[(fee('B13', '121', 2016), 1500, {'g13', 'f13', 'p', 'f12'})],
      subsets=[('the pairing missing', ['g12', 'g13', 'f12', 'f13'], fee('B13', '121', 2016), 'none')]),
 dict(name='fee equal to the adjoining berth, written as the berth next to another', events=[
        ('g12', 'grant', '2016-01-05', dict(berth='B12', label='120', party='P01', new=True)),
        ('g13', 'grant', '2016-01-06', dict(berth='B13', label='121', party='P02', new=True)),
        ('p', 'pair', '2016-02-01', dict(berths=['B12', 'B13'], labels=['120', '121'])),
        ('f12', 'fee', '2016-02-02', dict(berth='B12', label='120', year=2016, spec=abs_fee(1500))),
        ('f13', 'fee', '2016-02-03', dict(berth='B13', label='121', year=2016, needs=['p'],
              spec={'type': 'ref', 'target': 'adjoin', 'other': None, 'other_label': None, 'num': 1, 'den': 1}))],
      queries=[(fee('B13', '121', 2016), 1500, {'g13', 'f13', 'p', 'f12'})],
      subsets=[]),
 dict(name='fee corrected', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B14', label='140', party='P01', new=True)),
        ('f', 'fee', '2016-01-05', dict(berth='B14', label='140', year=2016, spec=abs_fee(1200))),
        ('x', 'fix', '2016-08-01', dict(field='amount', value=1400, ref='f', berth='B14', label='140')),
        ('r', 'fee', '2017-01-10', dict(berth='B14', label='140', year=2017, spec={'type': 'rel', 'num': 1, 'den': 10}))],
      queries=[(fee('B14', '140', 2016), 1400, {'g', 'f', 'x'}),
               (fee('B14', '140', 2017), 1540, {'g', 'f', 'x', 'r'})],
      subsets=[('correction missing', ['g', 'f', 'r'], fee('B14', '140', 2017), 1320),
               ('base of the relative fee missing', ['g', 'x', 'r'], fee('B14', '140', 2017), 'none')]),
 dict(name='corrected assignee', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B15', label='150', party='P01', new=True)),
        ('a', 'agree', '2016-03-01', dict(berth='B15', label='150', assignor='P01', assignee='P02')),
        ('r', 'register', '2016-04-08', dict(berth='B15', label='150', ref='a', when='2016-04-01')),
        ('x', 'fix', '2016-05-10', dict(field='assignee', value='P03', ref='a', berth='B15', label='150', party='P03', wrong='P02'))],
      queries=[(holder('B15', '150', '2016-12-31'), 'Arthur Hale', {'g', 'a', 'r', 'x'}),
               (holder('B15', '150', '2016-04-15'), 'Arthur Hale', {'g', 'a', 'r', 'x'}),
               (holder('B15', '150', '2016-03-15'), 'Clara Hartley', {'g'})],
      subsets=[('correction missing', ['g', 'a', 'r'], holder('B15', '150', '2016-12-31'), 'Gaff Marine'),
               ('correction of an absent agreement', ['g', 'x'], holder('B15', '150', '2016-12-31'), 'Clara Hartley')]),
 dict(name='count of berths held by a party on a date', events=[
        ('g16', 'grant', '2016-01-05', dict(berth='B16', label='160', party='P01', new=True)),
        ('g17', 'grant', '2016-01-06', dict(berth='B17', label='161', party='P01', new=True)),
        ('g18', 'grant', '2016-01-07', dict(berth='B18', label='162', party='P02', new=True)),
        ('a18', 'agree', '2016-04-01', dict(berth='B18', label='162', assignor='P02', assignee='P01')),
        ('r18', 'register', '2016-05-05', dict(berth='B18', label='162', ref='a18', when='2016-05-01')),
        ('s16', 'surrender', '2016-06-05', dict(berth='B16', label='160', party='P01', when='2016-06-01'))],
      queries=[(count('P01', '2016-07-01'), 2, {'g17', 'g18', 'a18', 'r18', 's16'}),
               (count('P01', '2016-05-02'), 3, {'g16', 'g17', 'g18', 'a18', 'r18'})],
      subsets=[]),
 dict(name='changes of holder between two dates', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B19', label='190', party='P01', new=True)),
        ('a', 'agree', '2016-03-01', dict(berth='B19', label='190', assignor='P01', assignee='P02')),
        ('r', 'register', '2016-04-08', dict(berth='B19', label='190', ref='a', when='2016-04-01')),
        ('d', 'death', '2016-08-20', dict(deceased='P02', executor='P03', when='2016-08-15')),
        ('s', 'surrender', '2016-10-05', dict(berth='B19', label='190', party='P03', when='2016-10-01')),
        ('n', 'grant', '2016-11-20', dict(berth='B19', label='190', party='P04'))],
      queries=[(changes('B19', '190', '2016-03-01', '2016-12-31'), 4, {'g', 'a', 'r', 'd', 's', 'n'}),
               (changes('B19', '190', '2016-05-01', '2016-09-01'), 1, {'g', 'a', 'r', 'd'})],
      subsets=[]),
 dict(name='grant to a party described by the sale of a business', events=[
        ('b', 'bought', '2016-01-02', dict(seller='P06', buyer='P05', trade='chandlery')),
        ('g', 'grant', '2016-01-10', dict(berth='B20', label='200', party='P05', new=True, needs=['b']))],
      queries=[(holder('B20', '200', '2016-12-31'), 'Spindrift Marine', {'g', 'b'})],
      subsets=[('the sale missing: the grant is left out and the berth never appears', ['g'], holder('B20', '200', '2016-12-31'), None)]),
 dict(name='fee of the berth held by a named party', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B21', label='210', party='P01', new=True)),
        ('f', 'fee', '2016-01-06', dict(berth='B21', label='210', year=2016, spec=abs_fee(900), needs=['g']))],
      queries=[(fee('B21', '210', 2016), 900, {'g', 'f'})],
      subsets=[('the passage behind "the berth held by" missing', ['f'], fee('B21', '210', 2016), None)]),
 dict(name='merger', events=[
        ('g22', 'grant', '2016-01-05', dict(berth='B22', label='220', party='P01', new=True)),
        ('g23', 'grant', '2016-01-06', dict(berth='B23', label='221', party='P02', new=True)),
        ('m', 'merge', '2016-09-10', dict(merged=['P01', 'P02'], party='P08', name='Kingfisher Shipping', when='2016-09-01'))],
      queries=[(holder('B22', '220', '2016-12-31'), 'Kingfisher Shipping', {'g22', 'm'}),
               (count('P08', '2016-12-31'), 2, {'g22', 'g23', 'm'})],
      subsets=[]),
 dict(name='estate settled after a death', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B24', label='240', party='P01', new=True)),
        ('d', 'death', '2016-05-10', dict(deceased='P01', executor='P02', when='2016-05-01')),
        ('e', 'estate', '2016-09-01', dict(berth='B24', label='240', heir='P03', when='2016-09-01'))],
      queries=[(holder('B24', '240', '2016-12-31'), 'Arthur Hale', {'g', 'e'}),
               (holder('B24', '240', '2016-06-01'), 'Gaff Marine', {'g', 'd'})],
      subsets=[]),
 dict(name='first date a party held a berth', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B25', label='250', party='P01', new=True)),
        ('a', 'agree', '2016-04-01', dict(berth='B25', label='250', assignor='P01', assignee='P02')),
        ('r', 'register', '2016-05-05', dict(berth='B25', label='250', ref='a', when='2016-05-01'))],
      queries=[(first('B25', '250', 'P02'), '2016-05-01', {'g', 'a', 'r'})],
      subsets=[('agreement without register note', ['g', 'a'], first('B25', '250', 'P02'), 'none')]),
]

# Addendum, written by hand before the interpreter was run on it (the first file's cases had been run already). A fee
# entry given by a change of the previous entry comes to a whole number of pounds under the procedure ("Every sum so
# computed is a whole number of pounds"). Without the correction that the base was given, the base is 1205 and "cut by a
# tenth" is 1084.5: not a sum the file can state, so the entry is left out and the berth keeps the previous entry.
HAND_CASES += [
 dict(name='a relative fee that is not a whole number of pounds without the correction', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B26', label='260', party='P01', new=True)),
        ('f', 'fee', '2016-01-05', dict(berth='B26', label='260', year=2016, spec=abs_fee(1205))),
        ('x', 'fix', '2016-08-01', dict(field='amount', value=1200, ref='f', berth='B26', label='260')),
        ('r', 'fee', '2017-01-10', dict(berth='B26', label='260', year=2017, spec={'type': 'rel', 'num': -1, 'den': 10}))],
      queries=[(fee('B26', '260', 2017), 1080, {'g', 'f', 'x', 'r'})],
      subsets=[('correction missing: the change would give 1084.5, so it is left out', ['g', 'f', 'r'], fee('B26', '260', 2017), 1205)]),
]

# Second addendum: one case for each way a paragraph can name its subject indirectly (the coordinator's ruling of 14
# November). Written by hand from procedure.md ("Names and descriptions", "Describing a berth") before the interpreter
# was run on them. `needs` on an event is what its wording leans on (the passage that fixes the name). Each case also
# holds a holding or fee told AFTER the statement's date, with the same indirect name: it must not be needed.
HAND_CASES += [
 dict(name='naming: the berth next to berth N', events=[
        ('g30', 'grant', '2016-01-05', dict(berth='B30', label='300', party='P01', new=True)),
        ('g31', 'grant', '2016-01-06', dict(berth='B31', label='301', party='P02', new=True)),
        ('p', 'pair', '2016-02-01', dict(berths=['B30', 'B31'], labels=['300', '301'])),
        ('a1', 'agree', '2016-03-01', dict(berth='B31', label='301', assignor='P02', assignee='P03', needs=['p'])),
        ('r1', 'register', '2016-04-08', dict(berth='B31', label='301', ref='a1', when='2016-04-01', needs=['p'])),
        ('a2', 'agree', '2016-09-01', dict(berth='B31', label='301', assignor='P03', assignee='P04', needs=['p'])),
        ('r2', 'register', '2016-10-08', dict(berth='B31', label='301', ref='a2', when='2016-10-01', needs=['p']))],
      queries=[(holder('B31', '301', '2016-06-01'), 'Arthur Hale', {'g31', 'p', 'a1', 'r1'}),
               (holder('B31', '301', '2016-12-31'), 'Anchorage Rowing Club', {'g31', 'p', 'a2', 'r2'})],
      subsets=[('the pairing missing: both agreements and their register notes are left out', ['g31', 'a1', 'r1', 'a2', 'r2'],
                holder('B31', '301', '2016-06-01'), 'Gaff Marine')]),
 dict(name='naming: a firm by its new name', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B34', label='340', party='P01', new=True)),
        ('n', 'rename', '2016-02-10', dict(party='P02', name='Egret Brothers', when='2016-02-01')),
        ('a', 'agree', '2016-03-01', dict(berth='B34', label='340', assignor='P01', assignee='P02', needs=['n'])),
        ('r', 'register', '2016-04-08', dict(berth='B34', label='340', ref='a', when='2016-04-01')),
        ('a2', 'agree', '2016-09-01', dict(berth='B34', label='340', assignor='P02', assignee='P03', needs=['n'])),
        ('r2', 'register', '2016-10-08', dict(berth='B34', label='340', ref='a2', when='2016-10-01'))],
      queries=[(holder('B34', '340', '2016-06-01'), 'Gaff Marine', {'g', 'n', 'a', 'r'}),
               (holder('B34', '340', '2016-12-31'), 'Arthur Hale', {'g', 'n', 'a2', 'r2'})],
      subsets=[('the renaming missing: both agreements are left out', ['g', 'a', 'r', 'a2', 'r2'], holder('B34', '340', '2016-06-01'), 'Clara Hartley')]),
 dict(name='naming: a person by kinship', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B35', label='350', party='P01', new=True)),
        ('k', 'kin', '2016-02-01', dict(person='P07', anchor='P03', relation='nephew')),
        ('a', 'agree', '2016-03-01', dict(berth='B35', label='350', assignor='P01', assignee='P07', needs=['k'])),
        ('r', 'register', '2016-04-08', dict(berth='B35', label='350', ref='a', when='2016-04-01')),
        ('a2', 'agree', '2016-09-01', dict(berth='B35', label='350', assignor='P07', assignee='P02', needs=['k'])),
        ('r2', 'register', '2016-10-08', dict(berth='B35', label='350', ref='a2', when='2016-10-01'))],
      queries=[(holder('B35', '350', '2016-06-01'), 'Alaric Kirkby', {'g', 'k', 'a', 'r'}),
               (holder('B35', '350', '2016-12-31'), 'Gaff Marine', {'g', 'a2', 'r2', 'k'})],
      subsets=[('the kinship missing: the agreement is left out', ['g', 'a', 'r'], holder('B35', '350', '2016-06-01'), 'Clara Hartley')]),
 dict(name='naming: a party by the sale of a business', events=[
        ('b', 'bought', '2016-01-02', dict(seller='P06', buyer='P05', trade='chandlery')),
        ('g', 'grant', '2016-01-10', dict(berth='B36', label='360', party='P01', new=True)),
        ('a', 'agree', '2016-03-01', dict(berth='B36', label='360', assignor='P01', assignee='P05', needs=['b'])),
        ('r', 'register', '2016-04-08', dict(berth='B36', label='360', ref='a', when='2016-04-01')),
        ('a2', 'agree', '2016-09-01', dict(berth='B36', label='360', assignor='P05', assignee='P02', needs=['b'])),
        ('r2', 'register', '2016-10-08', dict(berth='B36', label='360', ref='a2', when='2016-10-01'))],
      queries=[(holder('B36', '360', '2016-06-01'), 'Spindrift Marine', {'g', 'b', 'a', 'r'}),
               (holder('B36', '360', '2016-12-31'), 'Gaff Marine', {'g', 'b', 'a2', 'r2'})],
      subsets=[('the sale missing: the agreement is left out', ['g', 'a', 'r'], holder('B36', '360', '2016-06-01'), 'Clara Hartley')]),
 dict(name='naming: the berth held by a party', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B37', label='370', party='P01', new=True)),
        ('a', 'agree', '2016-03-01', dict(berth='B37', label='370', assignor='P01', assignee='P02')),
        ('r', 'register', '2016-04-08', dict(berth='B37', label='370', ref='a', when='2016-04-01')),
        ('f', 'fee', '2016-05-01', dict(berth='B37', label='370', year=2017, spec=abs_fee(1800), needs=['r', 'a'])),
        ('f2', 'fee', '2018-02-01', dict(berth='B37', label='370', year=2018, spec=abs_fee(1900), needs=['r', 'a']))],
      queries=[(fee('B37', '370', 2017), 1800, {'g', 'f', 'a', 'r'}),
               (fee('B37', '370', 2018), 1900, {'g', 'f2', 'a', 'r'})],
      subsets=[('what made the party the holder missing: the fee is left out', ['g', 'f'], fee('B37', '370', 2017), 'none')]),
 dict(name='naming: a berth by the number it carries after a renumbering', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B38', label='380', party='P01', new=True)),
        ('n', 'renumber', '2016-03-10', dict(berth='B38', old='380', label='385', when='2016-03-01')),
        ('f', 'fee', '2016-04-01', dict(berth='B38', label='385', year=2016, spec=abs_fee(1300), needs=['n'])),
        ('f2', 'fee', '2018-04-01', dict(berth='B38', label='385', year=2018, spec=abs_fee(1400), needs=['n']))],
      queries=[(fee('B38', '380', 2016), 1300, {'g', 'n', 'f'}),
               (fee('B38', '380', 2018), 1400, {'g', 'n', 'f2'})],
      subsets=[('the renumbering missing: the fee written under the new number is left out', ['g', 'f'], fee('B38', '380', 2016), 'none')]),
 dict(name='naming: a merged firm by the name the merger gives it', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B33', label='330', party='P01', new=True)),
        ('s', 'surrender', '2016-03-05', dict(berth='B33', label='330', party='P01', when='2016-03-01')),
        ('m', 'merge', '2016-06-10', dict(merged=['P01', 'P02'], party='P08', name='Kingfisher Shipping', when='2016-06-01')),
        ('rg', 'grant', '2016-09-01', dict(berth='B33', label='330', party='P08', new=False, needs=['m']))],
      queries=[(holder('B33', '330', '2016-12-31'), 'Kingfisher Shipping', {'g', 'm', 'rg'})],
      subsets=[('the merger missing: the regrant is left out and the berth stays vacant', ['g', 's', 'rg'], holder('B33', '330', '2016-12-31'), 'none')]),
 dict(name='naming: a date as corrected', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B39', label='390', party='P01', new=True)),
        ('a', 'agree', '2016-03-01', dict(berth='B39', label='390', assignor='P01', assignee='P02')),
        ('r', 'register', '2016-05-06', dict(berth='B39', label='390', ref='a', when='2016-05-01')),
        ('x', 'fix', '2016-08-01', dict(field='when', value='2016-04-10', ref='r', berth='B39', label='390'))],
      queries=[(holder('B39', '390', '2016-04-20'), 'Gaff Marine', {'g', 'a', 'r', 'x'}),
               (holder('B39', '390', '2016-04-05'), 'Clara Hartley', {'g'})],
      subsets=[('correction missing: the register date stands', ['g', 'a', 'r'], holder('B39', '390', '2016-04-20'), 'Clara Hartley')]),
]

# Third addendum, written by hand before the changed interpreter was run on it (after the independent review of the
# module 10261636...). Rulings: a sum worked out from a subset is kept as it comes out (fraction or not); the renumbering
# behind a number written in "the holder of berth N" is a name dependency (`needs`). Expectations: the true value, the
# needed set (events whose removal changes the value), and what a subset gives.
HAND_CASES += [
 dict(name='review W1: a correction whose absence passes through a fractional sum', events=[
        ('g', 'grant', '2013-01-05', dict(berth='B1', label='10', party='P01', new=True)),
        ('f0', 'fee', '2013-01-05', dict(berth='B1', label='10', year=2013, spec=abs_fee(4840))),
        ('f1', 'fee', '2013-03-01', dict(berth='B1', label='10', year=2013, spec={'type': 'rel', 'num': 1, 'den': 2})),
        ('f2', 'fee', '2013-06-01', dict(berth='B1', label='10', year=2013, spec={'type': 'rel', 'num': 1, 'den': 5})),
        ('x', 'fix', '2014-02-03', dict(field='amount', value=4400, ref='f0', berth='B1', label='10')),
        ('f3', 'fee', '2014-04-01', dict(berth='B1', label='10', year=2014, spec={'type': 'rel', 'num': 1, 'den': 10})),
        ('f4', 'fee', '2019-03-01', dict(berth='B1', label='10', year=2019, spec={'type': 'rel', 'num': 1, 'den': 4}))],
      queries=[(fee('B1', '10', 2019), 10890, {'g', 'f0', 'f1', 'f2', 'x', 'f3', 'f4'})],
      subsets=[('without the correction: 4840, 7260, 8712, 9583.2, 11979, not the true figure', ['g', 'f0', 'f1', 'f2', 'f3', 'f4'],
                fee('B1', '10', 2019), 11979)]),
 dict(name='review W2: a cancelling pair with a fractional fallback', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B2', label='20', party='P01', new=True)),
        ('f0', 'fee', '2016-01-05', dict(berth='B2', label='20', year=2016, spec=abs_fee(1010))),
        ('f1', 'fee', '2017-01-05', dict(berth='B2', label='20', year=2017, spec={'type': 'rel', 'num': -1, 'den': 5})),
        ('f2', 'fee', '2018-01-05', dict(berth='B2', label='20', year=2018, spec={'type': 'rel', 'num': 1, 'den': 4}))],
      queries=[(fee('B2', '20', 2018), 1010, {'g', 'f0', 'f1', 'f2'})],
      subsets=[('without the cut: 1010 raised by a quarter is 1262.5, kept', ['g', 'f0', 'f2'], fee('B2', '20', 2018), fractions.Fraction(2525, 2))]),
 dict(name='a quarter up and a fifth down, whole sums', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B3', label='30', party='P01', new=True)),
        ('f0', 'fee', '2016-01-05', dict(berth='B3', label='30', year=2016, spec=abs_fee(1000))),
        ('f1', 'fee', '2017-01-05', dict(berth='B3', label='30', year=2017, spec={'type': 'rel', 'num': 1, 'den': 4})),
        ('f2', 'fee', '2018-01-05', dict(berth='B3', label='30', year=2018, spec={'type': 'rel', 'num': -1, 'den': 5}))],
      queries=[(fee('B3', '30', 2018), 1000, {'g', 'f0', 'f1', 'f2'})],
      subsets=[('without the rise: 1000 cut by a fifth', ['g', 'f0', 'f2'], fee('B3', '30', 2018), 800),
               ('without the cut: 1250', ['g', 'f0', 'f1'], fee('B3', '30', 2018), 1250)]),
 dict(name='a quarter up and a fifth down, from a corrected base', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B4', label='40', party='P01', new=True)),
        ('f0', 'fee', '2016-01-05', dict(berth='B4', label='40', year=2016, spec=abs_fee(1005))),
        ('x', 'fix', '2016-03-01', dict(field='amount', value=1000, ref='f0', berth='B4', label='40')),
        ('f1', 'fee', '2017-01-05', dict(berth='B4', label='40', year=2017, spec={'type': 'rel', 'num': 1, 'den': 4})),
        ('f2', 'fee', '2018-01-05', dict(berth='B4', label='40', year=2018, spec={'type': 'rel', 'num': -1, 'den': 5}))],
      queries=[(fee('B4', '40', 2018), 1000, {'g', 'f0', 'x', 'f1', 'f2'})],
      subsets=[('without the correction: 1005 raised to 1256.25 and cut back to 1005', ['g', 'f0', 'f1', 'f2'], fee('B4', '40', 2018), 1005)]),
 dict(name='naming: the holder of berth N, N a later number', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B50', label='500', party='P01', new=True)),
        ('n', 'renumber', '2016-03-10', dict(berth='B50', old='500', label='505', when='2016-03-01')),
        ('a', 'agree', '2016-04-01', dict(berth='B50', label='500', assignor='P01', assignee='P02')),
        ('r', 'register', '2016-05-08', dict(berth='B50', label='500', ref='a', when='2016-05-01')),
        ('d', 'death', '2016-09-10', dict(deceased='P02', executor='P03', when='2016-09-01', needs=['r', 'a', 'n']))],
      queries=[(holder('B50', '500', '2016-12-31'), 'Arthur Hale', {'g', 'a', 'r', 'd', 'n'})],
      subsets=[('the renumbering missing: the death names its deceased by a number that is not fixed, so it is left out',
                ['g', 'a', 'r', 'd'], holder('B50', '500', '2016-12-31'), 'Gaff Marine')]),
 dict(name='naming: the holder of berth N, N the first number', events=[
        ('g', 'grant', '2016-01-05', dict(berth='B51', label='510', party='P01', new=True)),
        ('a', 'agree', '2016-04-01', dict(berth='B51', label='510', assignor='P01', assignee='P02')),
        ('r', 'register', '2016-05-08', dict(berth='B51', label='510', ref='a', when='2016-05-01')),
        ('d', 'death', '2016-09-10', dict(deceased='P02', executor='P03', when='2016-09-01', needs=['r', 'a'])),
        ('n', 'renumber', '2016-11-10', dict(berth='B51', old='510', label='515', when='2016-11-01'))],
      queries=[(holder('B51', '510', '2016-12-31'), 'Arthur Hale', {'g', 'a', 'r', 'd'})],
      subsets=[('no renumbering to miss', ['g', 'a', 'r', 'd'], holder('B51', '510', '2016-12-31'), 'Arthur Hale')]),
]

HAND_CAST = CAST


def left_out(events, gone):
    """The list without the events in `gone` and, to a fixed point, without every event that leans (`needs`, `ref`) on one
    that is not in it: what a subset reads as when the events in `gone` are absent. Written here, not taken from the module."""
    kept = [e for e in events if e['id'] not in gone]
    while True:
        ids = {e['id'] for e in kept}
        now = [e for e in kept if all(n in ids for n in e.get('needs', ())) and (not e.get('ref') or e['ref'] in ids)]
        if len(now) == len(kept):
            return kept
        kept = now


def slot_queries(seed, index=1):
    """The query of every statement of the dossier: one per slot (a statement and its wrong versions share one)."""
    return [true['query'] for true, _ in module_case(seed, index)['slots']]


def check_subset_rules(seed, index=1):
    """A subset is read as the whole correspondence file under the procedure's rules (the rule in the docstring of
    `answer`): an event whose own meaning depends on something the subset lacks is left out, with whatever leans on it;
    a missing later correction, death, renaming, withdrawal or registration is simply not applied; the value is None only
    where what the statement is about is not in the subset."""
    case = module_case(seed, index)
    events = case['events']
    by_id = {e['id']: e for e in events}
    queries = slot_queries(seed, index)
    # (1) removing an event reads as removing it and whatever leans on it (a sample of events, every statement)
    rng = random.Random(seed * 1000 + index)
    sample = rng.sample(events, 100)
    compared = 0
    for e in sample:
        got = big_succession.answers(queries, [x for x in events if x['id'] != e['id']])
        want = big_succession.answers(queries, left_out(events, {e['id']}))
        assert got == want, (e['id'], [(q['kind'], g, w) for q, g, w in zip(queries, got, want) if g != w][:2])
        compared += len(queries)
    # (2) a register note whose agreement is absent is left out: the berth reads as if neither were there
    held = 0
    for register in [e for e in events if e['kind'] == 'register']:
        agree = by_id[register['ref']]
        first = case['berths'][register['berth']]['first_label']
        query = {'kind': 'holder', 'berth': register['berth'], 'label': first, 'date': register['date']}
        got = big_succession.answer(query, [e for e in events if e['id'] != agree['id']])
        assert got is not None, register['id']
        assert got == big_succession.answer(query, [e for e in events if e['id'] not in (agree['id'], register['id'])])
        assert big_succession.answer(query, events) is not None
        held += 1
        if held == 6:
            break
    assert held, 'no register note'
    # (3) a missing later correction is not applied
    fixed = 0
    for fix in [e for e in events if e['kind'] == 'fix' and e['field'] == 'amount']:
        target = by_id[fix['ref']]
        later = [e for e in events if e['kind'] == 'fee' and e['berth'] == target['berth'] and
                 e['seq'] > target['seq'] and e['year'] <= target['year']]
        if later or fix['seq'] < target['seq']:
            continue
        first = case['berths'][target['berth']]['first_label']
        query = {'kind': 'fee', 'berth': target['berth'], 'label': first, 'year': target['year']}
        assert big_succession.answer(query, events) == fix['value']
        subset = [e for e in events if e['id'] != fix['id']]
        assert big_succession.answer(query, subset) == target['spec']['amount'], fix['id']
        fixed += 1
    assert fixed, 'no fee correction to pin'
    # (4) what the subset has to name: nothing named gives nothing; a count of the berths of a party it names is a number
    assert big_succession.answer({'kind': 'holder', 'berth': 'B99', 'label': '1', 'date': '2020-01-01'}, []) is None
    for q in queries:
        assert big_succession.answer(q, []) is None, q
    return (f'{compared} statement values on subsets read as the list without what leans on the removed event; '
            f'{held} register notes without their agreement, {fixed} fee corrections dropped')


@functools.lru_cache(maxsize=None)
def removal_table(seed, index=1):
    """(queries, true values, {event id: the value of every statement without that event}), by removing every event of
    the full list in turn."""
    events = module_case(seed, index)['events']
    queries = slot_queries(seed, index)
    full = big_succession.answers(queries, events)
    return queries, full, {e['id']: big_succession.answers(queries, [x for x in events if x['id'] != e['id']])
                           for e in events}


@functools.lru_cache(maxsize=None)
def legacy_removal_table(seed, index=1):
    """The same under the reading before the subset rule (`legacy_replay`: a missing referent makes what depends on it
    None); the reading the generator draws its dossiers by."""
    events = module_case(seed, index)['events']
    queries = slot_queries(seed, index)
    names = big_succession.party_names(events)

    def values(subset):
        history = big_succession.legacy_replay(subset)
        return [big_succession.public_value(big_succession.answer_in(history, q), q, names) for q in queries]
    return queries, values(events), {e['id']: values([x for x in events if x['id'] != e['id']]) for e in events}


def needed_sets(table):
    """(needed, only None) per statement from a removal table: the events whose removal changes the value (to another
    value, to 'none' or to None), and those among them whose removal makes it None."""
    queries, full, rows = table
    needed = [{i for i, values in rows.items() if values[n] != full[n]} for n in range(len(queries))]
    nothing = [{i for i in ids if rows[i][n] is None} for n, ids in enumerate(needed)]
    return needed, nothing


def starting_point(query, events):
    """The events a statement starts from, with what they lean on: the berth's first appearance (the grant of a new
    berth, or the split that made it) and, for a statement about a party, the events that name it. Without them the
    statement is about nothing in the subset. Written here, not taken from the module."""
    by_id = {e['id']: e for e in events}
    start = set()
    if query.get('berth'):
        start |= {e['id'] for e in events if (e['kind'] == 'grant' and e.get('new') and e['berth'] == query['berth']) or
                  (e['kind'] == 'split' and any(c['berth'] == query['berth'] for c in e['children']))}
    own = set(start)
    if query.get('party'):
        own |= {e['id'] for e in events if e['kind'] != 'decoy' and query['party'] in big_succession.concerned(e)[1]}
    frontier, closure = list(own), set(own)
    while frontier:
        e = by_id[frontier.pop()]
        for i in list(e.get('needs', ())) + ([e['ref']] if e.get('ref') else []):
            if i in by_id and i not in closure:
                closure.add(i)
                frontier.append(i)
    return own, closure


# Kinds of statement for which `answer` on the needed events alone need not give the value, because any one of several
# routes would do (so no event of any one route is needed): the value is one that can be reached twice. A holder (a berth
# that returns to a former holder: without the interlude the earlier tenure gives the same party), a fee (a later entry
# may restate the figure of an earlier one, "the same as the adjoining berth" twice), a count of berths and a number of
# changes of holder (a number: without one event another leads to the same number, for instance a berth a party held
# through a death is, without the death, held through the split that replaced it). The first date a party held a berth
# and the berth a berth forms part of are one date and one berth, and have no second route: no exemption.
EXEMPT_KINDS = ('holder', 'fee', 'count', 'changes')


def empty_needed_statements(seed, index=1):
    """The statements whose needed set is empty (no single event's removal changes the value)."""
    table = removal_table(seed, index)
    needed, _ = needed_sets(table)
    return [q for q, ids in zip(table[0], needed) if not ids]


def check_relevant_and_needed(seed, index=1):
    """(a) needed (removal from the full list changes the value, to another value or to None) lies inside `relevant`; no
    statement has an empty needed set, but a count of berths that two events give (a berth a party holds through a death
    and, without the death, through the split that replaced it): the draw cannot be changed, and the checker will class
    such a statement "basis not checkable", so it is named here."""
    case = module_case(seed, index)
    events = case['events']
    table = removal_table(seed, index)
    queries = table[0]
    needed, _ = needed_sets(table)
    for q, ids in zip(queries, needed):
        relevant = set(big_succession.relevant(q, events))
        assert relevant <= {e['id'] for e in events}
        assert ids <= relevant, ('a needed event outside relevant', q, sorted(ids - relevant))
    empty = empty_needed_statements(seed, index)
    for q in empty:
        assert q['kind'] == 'count', ('empty needed set', q)
    named = ''.join(f" [{q['kind']} of {q['party']} on {q['date']}]" for q in empty)
    return f'{len(queries)} statements, needed inside relevant, {len(empty)} with an empty needed set{named}'


def check_needed_events_are_an_argument(seed, index=1):
    """For every statement of both reports, with needed = the events whose removal from the full list changes `answer`
    (to another value, to 'none' or to None): (b) `answer` on the needed events alone is the true value, except for the
    kinds of EXEMPT_KINDS (where another route would do), and on the `relevant` events alone it is the true value for
    every kind; (c) no needed event is one whose removal only makes the value None, except the statement's
    own starting point (`starting_point`)."""
    events = module_case(seed, index)['events']
    table = removal_table(seed, index)
    queries, full, _ = table
    needed, nothing = needed_sets(table)
    redundant, own, leaning = 0, 0, 0
    for n, q in enumerate(queries):
        relevant = set(big_succession.relevant(q, events))
        assert big_succession.answer(q, [e for e in events if e['id'] in relevant]) == full[n], (q, 'relevant is not enough')
        alone = big_succession.answer(q, [e for e in events if e['id'] in needed[n]])
        if q['kind'] in EXEMPT_KINDS:
            redundant += alone != full[n]
        else:
            assert alone == full[n], (q, 'the needed events alone give', alone, 'not', full[n])
        start, closure = starting_point(q, events)
        assert nothing[n] <= closure, (q, 'removal makes the value None but is not the starting point',
                                       sorted(nothing[n] - closure))
        own += len(nothing[n] & start)
        leaning += len(nothing[n] - start)
    return (f'{len(queries)} statements: needed events alone give the true value ({redundant} of holder, fee, count and change '
            f'statements exempt: another route would do); removal makes the value None only for the starting point '
            f'({own} events) or what it leans on ({leaning})')


def hand_events(rows):
    events = []
    for seq, (ident, kind, date, fields) in enumerate(rows, 1):
        event = dict({'needs': []}, id=ident, kind=kind, seq=seq, date=date,
                     when=fields.get('when', date), **{k: v for k, v in fields.items() if k != 'when'})
        events.append(event)
    for event in events:
        event['party_names'] = {p: HAND_CAST[p] for p in big_succession.concerned(event)[1]}
    return events


# One expectation written under the earlier rule is superseded by the ruling that a sum worked out from a subset is kept as
# it comes out (spec 3.3). The subset of the case below, without the correction, cuts 1205 by a tenth: 1084.5. The old
# expectation (1205: the entry left out because the sum is not whole) no longer holds; the reading keeps 1084.5. The file
# text stays as it was written; the check reads the new value from here, and the report states the disagreement.
SUPERSEDED = {('a relative fee that is not a whole number of pounds without the correction',
               'correction missing: the change would give 1084.5, so it is left out'): fractions.Fraction(2169, 2)}


def check_hand_cases(seed=None, index=None):
    """Every hand-written list gives the value and the needed set written down for it (needed = removal changes the value),
    the needed events alone give the value, and every hand-written subset gives the value the rule says. Any
    disagreement is reported with both readings."""
    problems, checked = [], 0
    for c in HAND_CASES:
        events = hand_events(c['events'])
        for q, value, needed in c['queries']:
            got = big_succession.answer(q, events)
            cut = {e['id'] for e in events if big_succession.answer(q, [x for x in events if x['id'] != e['id']]) != got}
            alone = big_succession.answer(q, [e for e in events if e['id'] in cut])
            checked += 1
            if got != value or cut != needed or alone != got:
                problems.append(f"{c['name']}: {q['kind']}: by hand {value!r} with {sorted(needed)}, by the interpreter "
                                f"{got!r} with {sorted(cut)} (the needed events alone give {alone!r})")
        for label, ids, q, value in c['subsets']:
            got = big_succession.answer(q, [e for e in events if e['id'] in ids])
            checked += 1
            value = SUPERSEDED.get((c['name'], label), value)
            if got != value:
                problems.append(f"subset {c['name']} / {label}: by hand {value!r}, by the interpreter {got!r}")
    assert not problems, f'{len(problems)} of {checked} hand-written expectations disagree: {problems[:3]}'
    return (f"{len(HAND_CASES)} hand-written lists with {sum(len(c['queries']) for c in HAND_CASES)} statements (value and "
            f"needed set) and {sum(len(c['subsets']) for c in HAND_CASES)} subsets")


def check_none_means_nothing_to_start_from(seed, index=1):
    """On random subsets, for every statement and every reading, `answer` (called without the module's guard, so that a
    fault is an error here, not a quiet None) is None exactly where the statement's own starting point is not in the
    subset: the berth's first appearance, or the party never named. A flawed reader follows the same rule in its own
    facts: the two that go by the berth as the true reading identifies it give nothing exactly there too; the one that
    goes by the number as written has a berth wherever a number is written (its first naming), so it may give a value
    without the grant; and a fee that a reader cannot find or cannot state as a whole number of pounds in its own facts
    is None for it, as on the full list (the only other None)."""
    case = module_case(seed, index)
    events = case['events']
    queries = slot_queries(seed, index)
    names = big_succession.party_names(events)
    rng = random.Random(seed * 7919 + index)
    readers = (None,) + big_succession.READERS
    cases = unstated = 0
    for keep in (0.02, 0.15, 0.4, 0.7, 0.9, 0.97):
        for _ in range(3):
            subset = [e for e in events if rng.random() < keep]
            readable = left_out(subset, set())
            for reader in readers:
                history = big_succession.replay(subset, reader)
                for q in queries:
                    got = big_succession.public_value(big_succession.answer_in(history, q), q, names)
                    assert got == big_succession.answer(q, subset, reader), (q, reader)
                    berth_there = q.get('berth') is None or any(
                        (e['kind'] == 'grant' and e.get('new') and e['berth'] == q['berth']) or
                        (e['kind'] == 'split' and any(c['berth'] == q['berth'] for c in e['children'])) for e in readable)
                    party_there = q.get('party') is None or any(
                        e['kind'] != 'decoy' and q['party'] in big_succession.concerned(e)[1] for e in readable)
                    there = berth_there and party_there
                    if reader is None:
                        assert (got is None) == (not there), (keep, q, got)
                    else:
                        if reader != R_NAMES:
                            assert there or got is None, (reader, keep, q, got)
                        if got is None and there:
                            assert q['kind'] == 'fee', (reader, keep, q)
                            unstated += 1
                    cases += 1
    return (f'{cases} statement readings of random subsets: None exactly where the berth or the party is not in the subset '
            f'({unstated} fees a flawed reader cannot state in its own facts)')


# The draws the ruling of 14 November is measured on: seeds 1 to 5 at index 1, and seed 20261161 at indexes 1 and 2.
RULING_DRAWS = ((1, 1), (2, 1), (3, 1), (4, 1), (5, 1), (20261161, 1), (20261161, 2))


def name_dependencies(event, events):
    """What fixes the names an event is told with: the passages its wording leans on (`needs`) and, for a fee given as
    "the same as the adjoining berth" or a share of "the berth it was split from", the passage that pairs the berths or
    splits the parent. Not taken from the module's `needs` alone: the last two are found here from the events."""
    out = list(event.get('needs', ()))
    if event['kind'] == 'fee' and event['spec'].get('target') == 'adjoin':
        pairs = [p for p in events if p['kind'] == 'pair' and event['berth'] in p['berths'] and p['seq'] < event['seq']]
        out += [max(pairs, key=lambda p: p['seq'])['id']] if pairs else []
    if event['kind'] == 'fee' and event['spec'].get('target') == 'parent':
        out += [x['id'] for x in events if x['kind'] == 'split' and any(c['berth'] == event['berth'] for c in x['children'])]
    return out


def statements_with_unneeded_name_fixer(seed, index=1):
    """The statements in which a needed event is told through an indirect name while what fixes the name is not needed:
    [(query, [(event, its name fixer)])]."""
    events = module_case(seed, index)['events']
    by_id = {e['id']: e for e in events}
    table = removal_table(seed, index)
    needed, _ = needed_sets(table)
    found = []
    for q, ids in zip(table[0], needed):
        gaps = [(i, n) for i in sorted(ids) for n in name_dependencies(by_id[i], events) if n in by_id and n not in ids]
        if gaps:
            found.append((q, gaps))
    return found


def check_names_are_dependencies(seed, index=1):
    """A name is a dependency (spec 3.3), checked against the rendered text and not against the module's `needs`: every
    paragraph that tells an event through a description (the only nephew of..., the buyer of the chandlery business, the
    holder of berth N, the berth next to berth N, the berth held by...) is listed for an event that leans on the
    passage that fixes it (a kinship passage with that relation, a sale of that trade, an event on the berth that carries
    that number, a pairing of berths one of which is that berth) and, when the number written is not the berth's first,
    on the renumbering that gave it. The paragraphs are found by patterns
    written here from the phrases of the inventory; the event ids of a paragraph are grouped over its passage entries."""
    case = module_case(seed, index)
    events = {e['id']: e for e in case['events']}
    files = built(seed, index)['files']
    paragraphs = {}
    for name, text in files.items():
        if name.startswith('sources/') and name != 'sources/procedure.md':
            for i, block in enumerate(text.rstrip('\n').split('\n\n')):
                paragraphs[(name[8:], i)] = block
    relations = '|'.join(INV['relations'])
    # case-insensitive: a description may start a sentence ("The holder of berth 55 writes ...", 361 of 1,248 holder phrases
    # of seeds 1 to 30); "became the holder of berth N" is the estate sentence, which states the heir and describes nobody
    flags = re.IGNORECASE
    patterns = {'kin': re.compile(r'the only (%s) of' % relations, flags),
                'trade': re.compile(r'the (?:purchaser|buyer|new owner|seller|former owner) of the ([a-z-]+) business', flags),
                'holder': re.compile(r'(?<!became )the (?:present |then )?(?:holder|licensee) of berth ([^\s,.;:!?)]+)', flags),
                'partner': re.compile(r'the berth next to berth ([^\s,.;:!?)]+)', flags),
                'held by': re.compile(r'the berth held by ', flags)}
    grouped = collections.defaultdict(list)
    for entry in case['passages']:
        grouped[(entry['file'], entry['paragraph'])] += entry['event_ids']
    seen = collections.Counter()
    for where, ids in grouped.items():
        kinds = {events[i]['kind'] for i in ids}
        needs = [events[n] for i in ids for n in events[i].get('needs', ()) if n in events]
        for form, pattern in patterns.items():
            for m in pattern.finditer(paragraphs[where]):
                if (form == 'kin' and 'kin' in kinds) or (form == 'trade' and 'bought' in kinds):
                    continue          # the passage that fixes the name states it
                seen[form] += 1
                labels = lambda berth: case['berths'][berth]['labels']  # noqa: E731
                carrier = {label: b for b, info in case['berths'].items() for label in info['labels']}
                if form in ('holder', 'partner'):
                    berth = carrier[m.group(1)]
                    # the number written is a name too: unless it is the berth's first number, the renumbering that gave it
                    renumbered = m.group(1) == labels(berth)[0] or any(
                        n['kind'] == 'renumber' and n['berth'] == berth and n['label'] == m.group(1) for n in needs)
                if form == 'kin':
                    ok = any(n['kind'] == 'kin' and n['relation'] == m.group(1) for n in needs)
                elif form == 'trade':
                    ok = any(n['kind'] == 'bought' and n['trade'] == m.group(1) for n in needs)
                elif form == 'holder':
                    ok = renumbered and any(n['kind'] != 'renumber' and berth in n.get('berths', ()) for n in needs)
                elif form == 'partner':
                    ok = renumbered and any(n['kind'] == 'pair' and berth in n['berths'] for n in needs)
                else:
                    ok = bool(needs)
                assert ok, (form, where, m.group(0), ids)
    return ', '.join(f'{n} {form}' for form, n in sorted(seen.items())) + ' descriptions, each with its fixing passage in `needs`'


def check_decoy_paragraphs_decide_nothing(seed, index=1):
    """A paragraph of the role `decoy_passage` is listed so that every paragraph that uses the words of a decision for
    something that decides nothing (a permit for a night, a prize, a payment for services) is in the map: the test that
    nothing outside the map reads like a decision depends on it, and a quote of such a paragraph is then on the subject
    of a berth it names without earning anything. It earns only the decoy event, which the interpreter ignores: no
    paragraph lists a decoy together with an event that decides, and no decoy is in the needed set of a statement."""
    case = module_case(seed, index)
    events = {e['id']: e for e in case['events']}
    grouped, roles = collections.defaultdict(list), collections.defaultdict(set)
    for entry in case['passages']:
        grouped[(entry['file'], entry['paragraph'])] += entry['event_ids']
        roles[(entry['file'], entry['paragraph'])].add(entry['role'])
    decoys = 0
    for where, ids in grouped.items():
        kinds = {events[i]['kind'] for i in ids}
        if 'decoy' in kinds:
            assert kinds == {'decoy'} and roles[where] == {'decoy_passage'}, (where, kinds, roles[where])
            decoys += 1
        else:
            assert 'decoy_passage' not in roles[where], where
    needed, _ = needed_sets(removal_table(seed, index))
    assert not any(events[i]['kind'] == 'decoy' for ids in needed for i in ids)
    no_decoys = [e for e in case['events'] if e['kind'] != 'decoy']
    queries = slot_queries(seed, index)
    assert big_succession.answers(queries, no_decoys) == big_succession.answers(queries, case['events'])
    return f'{decoys} decoy paragraphs list only decoy events; no decoy is needed; the statements read the same without them'


def check_relevant_includes_the_dependencies(seed, index=1):
    """`relevant` holds, for every event it holds, what fixes the names that event is told with."""
    events = module_case(seed, index)['events']
    by_id = {e['id']: e for e in events}
    checked = 0
    for q in slot_queries(seed, index):
        relevant = set(big_succession.relevant(q, events))
        for i in relevant:
            for n in name_dependencies(by_id[i], events):
                assert n in relevant or q['kind'] == 'count', (q, i, n)
                checked += 1
    return f'{checked} name dependencies of relevant events are relevant'


def check_names_in_needed_sets(seed, index=1):
    """(a) of the ruling: a statement in which a needed event is told through an indirect name while what fixes the name
    is not needed can only be one of a kind where another route would do (EXEMPT_KINDS: without the fixer the event is
    left out and the value is reached by the other route, so removing the fixer changes nothing). A first date and the
    berth a berth forms part of have none."""
    found = statements_with_unneeded_name_fixer(seed, index)
    for q, gaps in found:
        assert q['kind'] in EXEMPT_KINDS, (q, gaps)
    return f'{len(found)} statements with an unneeded name fixer (kinds with another route only)'


def related_to_count(query, events):
    """The events a count of the berths of a party can turn on, found from the events and not from `needs`: the party and
    those whose holdings pass to it (an executor holds what the deceased held; a merged firm what its parts held), the
    events about them, about every berth any of them has held, and what those events lean on."""
    by_id = {e['id']: e for e in events}
    parties = {query['party']}
    while True:
        more = {e['deceased'] for e in events if e['kind'] == 'death' and e['executor'] in parties} | \
               {m for e in events if e['kind'] == 'merge' and e['party'] in parties for m in e['merged']}
        if more <= parties:
            break
        parties |= more
    history = big_succession.legacy_replay(events)
    held = {k for k, r in history.recs.items() if any(t[2] in parties for t in r['tl'])}
    related = {e['id'] for e in events if parties & set(big_succession.concerned(e)[1]) and e['kind'] != 'decoy'}
    related |= {e['id'] for e in events if held & set(big_succession.concerned(e)[0]) and e['kind'] not in ('fee', 'pair', 'decoy')}
    frontier = list(related)
    while frontier:
        e = by_id[frontier.pop()]
        for i in list(e.get('needs', ())) + ([e['ref']] if e.get('ref') else []):
            if i in by_id and i not in related:
                related.add(i)
                frontier.append(i)
    return related


def check_counts_do_not_turn_on_amounts(seed, index=1):
    """A count of berths does not turn on a fee: no fee entry (and no decoy) is in the needed set of a count, and
    every needed event of a count is one the count can turn on (`related_to_count`)."""
    events = module_case(seed, index)['events']
    by_id = {e['id']: e for e in events}
    table = removal_table(seed, index)
    needed, _ = needed_sets(table)
    counts = 0
    for q, ids in zip(table[0], needed):
        if q['kind'] != 'count':
            continue
        counts += 1
        assert not any(by_id[i]['kind'] in ('fee', 'decoy') for i in ids), (q, 'a fee is needed for a count')
        assert ids <= related_to_count(q, events), (q, sorted(ids - related_to_count(q, events)))
    return f'{counts} count statements: no fee or other unrelated event is needed'


def needed_sizes():
    """{kind of statement: (statements, smallest, mean, largest needed set)} over the draws of the ruling."""
    sizes = collections.defaultdict(list)
    for seed, index in RULING_DRAWS:
        table = removal_table(seed, index)
        needed, _ = needed_sets(table)
        for q, ids in zip(table[0], needed):
            sizes[q['kind']].append(len(ids))
    return {k: (len(v), min(v), sum(v) / len(v), max(v)) for k, v in sorted(sizes.items())}



def reading_state(history):
    """Everything a subset reading holds, for comparing two readings: berths, agreements, renamings, pairings, what it
    names, and which events it kept."""
    return (history.recs, history.agreements, history.renames, history.partner, history.incomplete, history.counter,
            history.clamped, history.named, history.kept)


DEPENDENCY_KINDS = ('name', 'amount', 'target')


def closure_of(deps, event_id, kinds=DEPENDENCY_KINDS):
    """Everything `event_id` cannot be read without by the given kinds of dependency, followed to the end."""
    seen, stack = set(), [event_id]
    while stack:
        here = stack.pop()
        for kind in kinds:
            for n in deps[here][kind]:
                if n not in seen:
                    seen.add(n)
                    stack.append(n)
    return seen


@functools.lru_cache(maxsize=None)
def exported_dependencies(seed, index=1):
    return big_succession.depends(module_case(seed, index)['events'])


@functools.lru_cache(maxsize=None)
def left_out_by_removal(seed, index=1):
    """{D: ids of the events the subset reading leaves out when D alone is removed from the full list}, observed as the
    events of the list (other than D) that the reading does not keep (`Hist.kept`, set by `replay`)."""
    events = module_case(seed, index)['events']
    ids = {e['id'] for e in events}
    return {e['id']: frozenset(ids - {e['id']} - big_succession.replay([x for x in events if x['id'] != e['id']]).kept)
            for e in events}


def check_depends_are_consistent_with_the_reading(seed, index=1):
    """1. Per kind of dependency: for every event E and every D in depends(events)[E][kind], the reading of (all events minus
    D) is the reading of (all events minus D and minus E): E is left out when D is absent, and the rest of the reading is
    what it is without E. The comparison is of the whole history (`reading_state`), not of one statement's answer. The
    kinds also match their sources: `name` holds the `needs` of the event (and the pairing or split a fee is taken from),
    `target` is exactly its `ref`, `amount` holds no `needs` or `ref`."""
    case_events = module_case(seed, index)['events']
    by_id = {e['id']: e for e in case_events}
    events = case_events
    assert big_succession.replay(events).kept == set(by_id), 'the full list leaves something out'
    deps = exported_dependencies(seed, index)
    assert set(deps) == set(by_id)
    for e, kinds in deps.items():
        assert set(kinds) == set(DEPENDENCY_KINDS)
        needs = set(by_id[e].get('needs', ()))
        extra = set(kinds['name']) - needs
        assert needs <= set(kinds['name']), e
        assert not extra or (by_id[e]['kind'] == 'fee' and all(by_id[x]['kind'] in ('pair', 'split') for x in extra)), e
        assert kinds['target'] == ([by_id[e]['ref']] if by_id[e].get('ref') else []), e
        assert not set(kinds['amount']) & (set(by_id[e].get('needs', ())) | {by_id[e].get('ref')}), e
        assert by_id[e]['kind'] == 'fee' or not kinds['amount'], e
    pairs = collections.Counter()
    for kind in DEPENDENCY_KINDS:
        for d in sorted({n for ks in deps.values() for n in ks[kind]}):
            without = big_succession.replay([x for x in events if x['id'] != d])
            for e in sorted(e for e, ks in deps.items() if d in ks[kind]):
                assert e not in without.kept, (kind, e, 'is not left out without', d)
                both = big_succession.replay([x for x in events if x['id'] not in (d, e)])
                assert reading_state(without) == reading_state(both), (kind, e, d)
                pairs[kind] += 1
    return ', '.join(f'{n} {kind}' for kind, n in sorted(pairs.items())) + ' (event, dependency) pairs: the reading without the dependency is the reading without the event too'


def check_depends_are_complete(seed, index=1):
    """2. Every event the subset reading leaves out when some other event D alone is removed has D in the closure of its
    dependencies (all kinds together: one removal can leave an event out through a name, then an amount): no dependency the
    interpreter uses is missing from the export. "Left out" is observed as `Hist.kept` (the events the reading read) of
    `replay` on the list without D. Per kind, the pairs that no single kind explains are counted."""
    deps = exported_dependencies(seed, index)
    left, observed, mixed = left_out_by_removal(seed, index), 0, 0
    for d, others in left.items():
        for e in others:
            assert d in closure_of(deps, e), (e, 'is left out without', d, 'but does not depend on it')
            observed += 1
            mixed += not any(d in closure_of(deps, e, (kind,)) for kind in DEPENDENCY_KINDS)
    return (f'{observed} left-out events over {len(left)} single removals, each with the removed event in its dependencies '
            f'({mixed} reached only through a mix of kinds)')


def closed_needed_sets(seed, index=1):
    """Needed (removal changes the value) closed under the NAME dependencies only, per statement: [(query, needed, closed)].
    What fixes an amount is not dragged in (a count does not turn on a fee), nor is the target an event corrects or cancels
    (an event and the one that cancels it cancel by design)."""
    deps = exported_dependencies(seed, index)
    table = removal_table(seed, index)
    needed, _ = needed_sets(table)
    return [(q, ids, set(ids).union(*[closure_of(deps, i, ('name',)) for i in ids])) for q, ids in zip(table[0], needed)]


def check_closed_needed_sets(seed, index=1):
    """3. With needed closed under the name dependencies, no statement has a needed event told through an indirect name while
    what fixes the name (`name_dependencies`, found here) is outside the closed set. 4. The closed set lies inside `relevant`."""
    events = module_case(seed, index)['events']
    by_id = {e['id']: e for e in events}
    rows = closed_needed_sets(seed, index)
    for q, ids, closed in rows:
        relevant = set(big_succession.relevant(q, events))
        assert closed <= relevant, (q, 'closed needed is not relevant', sorted(closed - relevant))
        for i in closed:
            for n in name_dependencies(by_id[i], events):
                assert n in closed, (q, i, n)
    return f'{len(rows)} statements: closed needed inside relevant, every name fixer of a closed event in it'


def closed_sizes():
    """{kind: (statements, smallest, mean, largest)} of the closed needed sets over the draws of the ruling."""
    sizes = collections.defaultdict(list)
    for seed, index in RULING_DRAWS:
        for q, _, closed in closed_needed_sets(seed, index):
            sizes[q['kind']].append(len(closed))
    return {k: (len(v), min(v), sum(v) / len(v), max(v)) for k, v in sorted(sizes.items())}



# The draws of the reviewer's sweep: seeds 1 to 30 at index 1 and seed 20261161 at indexes 1 to 3, and the three further
# draws in which the review found a quoted "holder of berth N" without its renumbering (6/2, 34/1, 80/1).
SWEEP_DRAWS = tuple((seed, 1) for seed in range(1, 31)) + ((20261161, 1), (20261161, 2), (20261161, 3), (6, 2), (34, 1), (80, 1))
HOLDER_FORM = re.compile(r'(?<!became )the (?:present |then )?(?:holder|licensee) of berth ([^\s,.;:!?)]+)', re.IGNORECASE)


@functools.lru_cache(maxsize=None)
def built_files(seed, index=1):
    return built(seed, index)['files']


# The statements the independent review named (claim id of the correct report), the kind of each, and for each (needed by
# removal, closed needed, sufficient) before the two fixes (module 10261636: the whole-pound leave-out; the renumbering behind
# "the holder of berth N" not recorded) and after. Before, each basis was sufficient while a needed fee entry of it was not read
# (4/1 C24: E132; 20261161/3 C14: E374) or a quoted number lacked its renumbering (6/2 C09: E364; 34/1 C17: E242; 80/1 C05: E169;
# and, in a sentence that begins with the phrase, which the first version of the check could not see: 5/1 C03 and C04: E233, 6/1 C21: E127).
REVIEW_STATEMENTS = {(4, 1, 'C24'): 'fee', (20261161, 3, 'C14'): 'fee', (6, 2, 'C09'): 'count', (34, 1, 'C17'): 'holder',
                     (80, 1, 'C05'): 'holder', (5, 1, 'C03'): 'count', (5, 1, 'C04'): 'changes', (6, 1, 'C21'): 'first_held'}
REVIEW_BEFORE = {(4, 1, 'C24'): (7, 7, True), (20261161, 3, 'C14'): (7, 7, True), (6, 2, 'C09'): (6, 6, True),
                 (34, 1, 'C17'): (5, 5, True), (80, 1, 'C05'): (4, 4, True), (5, 1, 'C03'): (37, 37, True),
                 (5, 1, 'C04'): (7, 7, True), (6, 1, 'C21'): (3, 3, True)}
REVIEW_AFTER = {(4, 1, 'C24'): (8, 8, True), (20261161, 3, 'C14'): (8, 8, True), (6, 2, 'C09'): (7, 7, True),
                (34, 1, 'C17'): (6, 6, True), (80, 1, 'C05'): (5, 5, True), (5, 1, 'C03'): (38, 38, True),
                (5, 1, 'C04'): (8, 8, True), (6, 1, 'C21'): (4, 4, True)}


def basis_of(seed, index, n):
    """What the checker's sufficiency test (spec 3.3 step 4) does with the statement `n` of a draw: needed = removal changes
    the value, closed under the `name` dependencies; cited = the events the passage map lists for one quoted paragraph of
    each needed event (the first that lists it, at least 40 characters, not a `titles` paragraph). Returns the closed set,
    the quoted paragraphs, the cited events and whether the basis is sufficient (needed all cited, cited give the value)."""
    case = module_case(seed, index)
    events = case['events']
    queries, full, _ = removal_table(seed, index)
    closed = closed_needed_sets(seed, index)[n][2]
    earns = collections.OrderedDict()
    for entry in case['passages']:
        if entry['role'] != 'titles':
            earns.setdefault((entry['file'], entry['paragraph']), set()).update(entry['event_ids'])
    text = {}
    for name, body in built_files(seed, index).items():
        if name.startswith('sources/'):
            for i, block in enumerate(body.rstrip('\n').split('\n\n')):
                text[(name[8:], i)] = block
    quoted = []
    for i in sorted(closed):
        spots = [spot for spot, ids in earns.items() if i in ids and len(text[spot]) >= 40]
        if spots and spots[0] not in quoted:
            quoted.append(spots[0])
    cited = set().union(*[earns[spot] for spot in quoted]) if quoted else set()
    sufficient = closed <= cited and big_succession.answer(queries[n], [e for e in events if e['id'] in cited]) == full[n]
    return closed, quoted, cited, sufficient, text


def check_complete_basis_is_read(seed, index=1):
    """Every needed event of a complete basis is read: for every statement whose closed needed set gives the true value, each
    event of that set is kept by the reading of exactly those events (none is left out for want of what it leans on). Two
    exceptions, both by design: a statement whose closed needed set does not give the true value (another route would do:
    a fee restated by a later entry, so neither entry is needed and a fee that takes the first as its base is not read; the
    second condition of the checker's sufficiency test decides these), and, by the ruling that the closure follows names
    only, a correction or registration needed while the agreement or entry it is about is not (the two cancel: without the
    target the correction is not read, and the statement has the value it has with both). Those are counted."""
    events = module_case(seed, index)['events']
    deps = exported_dependencies(seed, index)
    rows = closed_needed_sets(seed, index)
    full = removal_table(seed, index)[1]
    by_target = other_route = 0
    for n, (q, ids, closed) in enumerate(rows):
        basis = [e for e in events if e['id'] in closed]
        if big_succession.answer(q, basis) != full[n]:
            other_route += 1
            continue
        kept = big_succession.replay(basis).kept
        for e in sorted(closed - kept):
            assert deps[e]['target'] and not set(deps[e]['target']) <= closed and set(deps[e]['name']) <= closed and \
                not deps[e]['amount'], (q, 'needed event left out when read together', e)
            by_target += 1
    return (f'{len(rows)} statements: the closed needed set is read whole ({by_target} events left out only for want of the '
            f'target they correct; {other_route} statements where another route would do)')


def basis_problems(seed, index=1):
    """The statements whose basis is sufficient while a needed fee entry of it is not read, or while a "holder of berth N"
    name in a quoted paragraph lacks the renumbering behind N (N not the berth's first number)."""
    case = module_case(seed, index)
    events = case['events']
    by_id = {e['id']: e for e in events}
    carrier = {label: b for b, info in case['berths'].items() for label in info['labels']}
    problems = []
    for n, (q, ids, closed) in enumerate(closed_needed_sets(seed, index)):
        closed, quoted, cited, sufficient, text = basis_of(seed, index, n)
        if not sufficient:
            continue
        kept = big_succession.replay([e for e in events if e['id'] in cited]).kept
        unread = sorted(i for i in closed if by_id[i]['kind'] == 'fee' and i not in kept)
        missing = []
        for spot in quoted:
            for m in HOLDER_FORM.finditer(text[spot]):
                berth = carrier[m.group(1)]
                if m.group(1) != case['berths'][berth]['labels'][0]:
                    gave = [e['id'] for e in events if e['kind'] == 'renumber' and e['berth'] == berth and e['label'] == m.group(1)]
                    missing += [i for i in gave if i not in cited]
        if unread or missing:
            problems.append((q, unread, missing))
    return problems




def check_no_misleading_basis(seed, index=1):
    """No statement has a sufficient basis (see `basis_of`) in which a needed fee entry is not read, or a quoted "holder of
    berth N" name lacks its renumbering."""
    problems = basis_problems(seed, index)
    assert not problems, problems[:2]
    return 'no sufficient basis with an unread fee entry or an unfixed number'


def key_with_facts(seed, index=1):
    """The key of a dossier as the checker gets it: rows with their query and value form, and the facts (names among them)."""
    with generate_big.facts_in_keys():
        return generate.build_case('succession', seed, index)['key']


def check_names_read_what_the_key_says(seed, index=1):
    """Written from the key's rows, not from `names`: for every `part` and `holder` row, read through the checker's own reader
    with the key's `names`, (1) the true value (the stated value of the row's supported twin) reads as the true value,
    (2) the stated value of a refuted row reads as something else, (3) the stated value of a supported row reads as the
    true value. Then what each value form needs of `names`: a party is one key listing every form it is written in, a berth
    number is its own key listing only itself, and no written form belongs to two keys."""
    import check_shown
    key = key_with_facts(seed, index)
    names = check_shown.Names(key['facts']['names'])

    def agrees(text, target):
        return check_shown.agrees(check_shown.read_value(text, 'name', names), target, 'name', big_succession.NONE_VALUE)
    twin = lambda q: json.dumps({k: v for k, v in q.items() if k != 'value'}, sort_keys=True)  # noqa: E731
    true_value = {twin(r['query']): r['query']['value'] for r in key['expectations']
                  if r['semantic_state'] == 'supported' and r['query']['kind'] in ('part', 'holder')}
    rows = {'part': 0, 'holder': 0}
    refuted = 0
    for r in key['expectations']:
        q = r['query']
        if q['kind'] not in ('part', 'holder'):
            continue
        true = true_value[twin(q)]
        assert r['value_form'] == 'name'
        assert agrees(true, true), ('the true value does not read as itself', q['kind'], true)
        if r['semantic_state'] == 'supported':
            assert q['value'] == true and agrees(q['value'], true), (q, 'a supported row does not read as the true value')
        else:
            assert q['value'] != true and not agrees(q['value'], true), (q, 'a refuted row reads as the true value')
            refuted += 1
        rows[q['kind']] += 1
    forms = collections.defaultdict(set)
    for owner, listed in key['facts']['names'].items():
        for form in list(listed) + [owner]:
            forms[check_shown.name_key(form)].add(owner)
    assert all(len(owners) == 1 for owners in forms.values()), [f for f, o in forms.items() if len(o) > 1][:3]
    case = module_case(seed, index)
    for party in case['parties'].values():
        assert set(party['names']) <= set(key['facts']['names'][party['name']])
    for berth in case['berths'].values():
        assert all(key['facts']['names'][label] == [label] for label in berth['labels'])
    return f"{rows['part']} part and {rows['holder']} holder rows ({refuted} refuted) read as the key says; no written form belongs to two keys"



def check_full_list_unchanged(seed, index=1):
    """The reading of the full list that the generator draws by (`legacy_replay`) and the subset reading (`replay`) give the
    same histories for the true reading and every flawed reader, and every statement the same value under each: the
    subset rule changes nothing on the full list."""
    case = module_case(seed, index)
    events = case['events']
    queries = slot_queries(seed, index)
    names = big_succession.party_names(events)
    variants = [None] + list(big_succession.READERS) + [p for parts in BUNDLED.values() for p in parts]

    def state(h):
        return (h.recs, h.agreements, h.renames, h.partner, h.incomplete, h.counter, h.clamped)
    for reader in variants:
        old, new = big_succession.legacy_replay(events, reader), big_succession.replay(events, reader)
        assert state(old) == state(new), reader
        if reader in (None,) + big_succession.READERS:
            for q in queries:
                assert big_succession.answer_in(old, q) == big_succession.answer_in(new, q), (reader, q)
    assert case['base']['hist'].recs == big_succession.replay(events).recs
    return f'{len(variants)} readings of the full list equal under both, {len(queries)} statements'


def check_no_ties(seed, index=1):
    """No berth has two changes of holder on one day, so no value rests on a tie between two events."""
    history = module_case(seed, index)['base']['hist']
    for record in history.recs.values():
        dates = [entry[0] for entry in record['tl']]
        assert len(dates) == len(set(dates)), record['k']


def check_decoys(seed, index=1):
    case = module_case(seed, index)
    mapped = {i for p in case['passages'] for i in p['event_ids']}
    decoys = [e for e in case['events'] if not e['decides']]
    assert 20 <= len(decoys) <= 70, len(decoys)
    assert all(e['kind'] == 'decoy' and e['effect'] == 'none' and e['id'] in mapped for e in decoys)
    assert all(e['decides'] for e in case['events'] if e['kind'] != 'decoy')


def check_names_and_preamble(seed, index=1):
    case = module_case(seed, index)
    names = case['names']
    for party in case['parties'].values():
        assert party['name'] in names and set(party['names']) <= set(names[party['name']]), party
    for berth in case['berths'].values():
        for label in berth['labels']:
            assert names[label] == [label], (berth, label)       # a number is its own key, with only itself listed
    for true, _ in case['slots']:
        if true['query']['kind'] == 'holder':
            assert true['query']['value'] in names
    preamble = built(seed, index)['files']['report-correct.md']
    note = preamble.split('<a id="C01">')[0]
    assert 'none' in note and 'YYYY-MM-DD' in note and 'whole number' in note
    for word in ('assign', 'regist', 'correct', 'merge', 'renam', 'renumber', 'death', 'died', 'forfeit', 'surrender'):
        assert word not in note.lower(), word
    sources = built(seed, index)['files']
    for name, text in sources.items():
        if name.startswith('sources/'):
            assert not re.search(r'\bto date\b|\brunning total\b|\bso far\b|\bcumulative\b', text), name


def check_files(seed, index, fixtures):
    """Rebuilt files equal the files in a fixtures folder, byte for byte."""
    case = built(seed, index)
    folder = Path(fixtures)
    if not (folder / 'report-correct.md').exists():
        folder = folder / case['dossier']
    for name, text in case['files'].items():
        assert (folder / name).read_bytes() == text.encode('utf-8'), name


def verify(seed, index, fixtures=None):
    """Run the round trip, the one-referent check and the checker's contract on one draw; print one line per check
    and return True when every check holds."""
    checks = [('round trip', check_round_trip), ('one referent', check_one_referent),
              ('answer equals the key', check_answer_matches_key),
              ('full list read as before', check_full_list_unchanged),
              ('subset read as the whole file', check_subset_rules),
              ('needed inside relevant, not empty', check_relevant_and_needed),
              ('needed events are an argument', check_needed_events_are_an_argument),
              ('None only without a starting point', check_none_means_nothing_to_start_from),
              ('hand-written lists', check_hand_cases),
              ('names are dependencies (from the text)', check_names_are_dependencies),
              ('relevant includes the dependencies', check_relevant_includes_the_dependencies),
              ('needed sets include what fixes a name', check_names_in_needed_sets),
              ('counts do not turn on amounts', check_counts_do_not_turn_on_amounts),
              ('decoy paragraphs decide nothing', check_decoy_paragraphs_decide_nothing),
              ('depends agree with the reading', check_depends_are_consistent_with_the_reading),
              ('depends are complete', check_depends_are_complete),
              ('closed needed sets', check_closed_needed_sets),
              ('complete basis is read', check_complete_basis_is_read),
              ('no sufficient basis with an unread fee or an unfixed number', check_no_misleading_basis),
              ('names read as the key says', check_names_read_what_the_key_says),
              ('names, preamble, no running totals', check_names_and_preamble), ('no ties', check_no_ties),
              ('decoys listed and decide nothing', check_decoys)]
    if fixtures:
        checks.append(('files equal the fixtures', lambda s, i: check_files(s, i, fixtures)))
    ok = True
    for label, check in checks:
        try:
            note = check(seed, index)
            print(f'ok    {label}' + (f': {note}' if isinstance(note, str) else ''))
        except Exception as error:  # noqa: BLE001
            ok = False
            print(f'FAIL  {label}: {type(error).__name__}: {str(error)[:200]}')
    return ok


def subject_words(case, statement, every_form=False):
    """The words a search for the statement's subject would use: the numbers of its berth, or every name the file
    gives its party (the distinctive words only, unless `every_form`: then 'berth N' for each number)."""
    names = case['names']
    words = set()
    if statement.get('berth'):
        labels = next(info['labels'] for info in case['berths'].values() if info['first_label'] == statement['berth'])
        words |= {f'berth {label}' for label in labels} if every_form else set(labels)
    party = statement.get('party')
    if party:
        for form in names[party]:
            words |= set(form.split(' ')) if not every_form else {form}
        words -= {'Mr', 'Ms', 'Mrs', 'Miss', 'Dr', '&', 'Sons', 'Co', 'Partners', 'Daughters', 'Marine', 'Brothers', 'Ltd',
                  'Boatyard', 'Chandlers', 'Fisheries', 'Shipping', 'Charters', 'Ferries', 'Trawlers', 'Yachts',
                  'Sailing', 'Club', 'Rowing', 'Angling', 'Society', 'Boat', 'Hire', 'Services'}
    return words or {'\\none'}


# ---------------------------------------------------------------------------------------------------------------
# The naive script reader: full-name search and a number pattern, no phrase inventory
# ---------------------------------------------------------------------------------------------------------------

def naive_verdict(paragraphs, statement, text):
    """Supported when the last paragraph that names the thing concerned says what the statement says."""
    if statement['kind'] in ('count', 'first_held'):
        hits = [p for p in paragraphs if statement['party'] in p]
    else:
        hits = [p for p in paragraphs if re.search(r'\bberth ' + statement['berth'] + r'\b', p)]
    if not hits:
        return 'refuted'
    last = hits[-1]
    money = re.findall(r'£?(\d[\d,]*)(?: pounds)?', last)
    values = [int(x.replace(',', '')) for x in money if x.replace(',', '').isdigit()]
    if statement['kind'] == 'fee':
        return 'supported' if values and values[-1] == statement['value'] else 'refuted'
    if statement['kind'] == 'holder':
        return 'supported' if statement['value'] in last else 'refuted'
    if statement['kind'] == 'count':
        return 'supported' if len(hits) == statement['value'] else 'refuted'
    if statement['kind'] == 'changes':
        return 'supported' if sum('assign' in p or 'died' in p for p in hits) == statement['value'] else 'refuted'
    if statement['kind'] == 'first_held':
        return 'supported' if hits[0] and statement['value'][:4] in hits[0] else 'refuted'
    return 'supported' if re.search(r'\b' + statement['value'] + r'\b', last) else 'refuted'


class SuccessionTests(unittest.TestCase):
    def test_inventory_has_enough_wordings(self):
        for kind, texts in INV['events'].items():
            self.assertGreaterEqual(len(texts), 8, kind)
            self.assertEqual(len(texts), len(set(texts)), kind)
        self.assertGreaterEqual(len(INV['relative']), 100)

    def test_deterministic(self):
        for seed in SEEDS:
            again = generate.build_case('succession', seed, 1)
            self.assertEqual(built(seed)['files'], again['files'])
            self.assertEqual(built(seed)['key'], again['key'])

    def test_deterministic_across_hash_seeds(self):
        digests = []
        for hash_seed in ('1', '2'):
            with tempfile.TemporaryDirectory() as tmp:
                env = dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONDONTWRITEBYTECODE='1')
                run = subprocess.run([sys.executable, str(ROOT / 'evalsuite' / 'generate_big.py'), '--fixtures',
                                      tmp + '/f', '--keys', tmp + '/k', '--seed', '2', '--dossiers', '1', '--families',
                                      'succession'], env=env, capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                files = sorted(Path(tmp).rglob('*'))
                digests.append({str(p.relative_to(tmp)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in files if p.is_file() and not p.name.endswith('.generation.json')})
        self.assertEqual(digests[0], digests[1])
        self.assertGreater(len(digests[0]), 5)

    def test_sizes_and_reports(self):
        for seed in SEEDS:
            files = built(seed)['files']
            size = sum(len(t) for n, t in files.items() if n.startswith('sources/'))
            self.assertTrue(300000 <= size <= 450000, (seed, size))
            rows = rows_of(seed)
            for variant in ('correct', 'mixed'):
                self.assertEqual(len(report_statements(files[f'report-{variant}.md'])), 24)
                self.assertEqual(len(rows[variant]), 24)
            self.assertTrue(all(r['semantic_state'] == 'supported' for r in rows['correct']))
            false = sum(r['semantic_state'] != 'supported' for r in rows['mixed'])
            self.assertTrue(7 <= false <= 12, (seed, false))
            if PRINT:
                print(f'seed {seed}: {size} characters, {false} of 24 mixed statements false ({false / 24:.1%})')
            for line in files['report-correct.md'].splitlines():
                if line.startswith('**C'):
                    cited = re.search(r'Citations: (.*)\.$', line)[1]
                    self.assertEqual(cited.count('F-0'), len([n for n in files if 'correspondence' in n]), line)

    def test_round_trip(self):
        for seed in SEEDS:
            check_round_trip(seed)

    def test_every_reader_has_statements_and_controls(self):
        for seed in SEEDS:
            correct = rows_of(seed)['correct']
            for name in big_succession.READERS:
                wrong = sum(name in r['lure_states'] for r in correct)
                self.assertGreaterEqual(wrong, 4, (seed, name))
            controls = sum(not r['lure_states'] for r in correct)
            self.assertGreaterEqual(controls, 6, seed)
            mixed = rows_of(seed)['mixed']
            if PRINT:
                print(f'seed {seed}: lure rows per reader',
                      {n: sum(n in r['lure_states'] for r in correct) for n in big_succession.READERS},
                      'controls', controls, 'mixed lure rows', sum(bool(r['lure_states']) for r in mixed))

    def test_parsed_events_equal_the_log(self):
        for seed in SEEDS:
            _, parser, _ = parsed(seed)
            case = module_case(seed)
            first = {k: v['first_label'] for k, v in case['berths'].items()}
            name = {k: v['name'] for k, v in case['parties'].items()}
            ids = [e['id'] for e in case['events']]
            told = [e for e in case['events']]
            self.assertEqual(len(parser.events), len(told), seed)
            party_fields = ('party', 'assignor', 'assignee', 'deceased', 'executor', 'heir', 'buyer', 'seller',
                            'person', 'anchor', 'wrong')
            for parsed_event, log in zip(parser.events, told):
                self.assertEqual(parsed_event['kind'], log['kind'])
                self.assertEqual(parsed_event['date'], log['date'])
                for field in ('year', 'trade', 'relation', 'field', 'name', 'old', 'agreed', 'entry', 'new'):
                    if field in log and field != 'old':
                        self.assertEqual(parsed_event[field], log[field], (seed, log['id'], field))
                if 'when' in parsed_event:
                    self.assertEqual(parsed_event['when'], log['when'], (seed, log['id']))
                if 'berth' in log:
                    self.assertEqual(parsed_event['berth'], first[log['berth']], (seed, log['id']))
                    self.assertEqual(parsed_event['label'], log['label'])
                for field in party_fields:
                    if field in log:
                        value = log[field]
                        if log['kind'] == 'fix' and field == 'party':
                            continue
                        self.assertEqual(parsed_event.get(field), name.get(value), (seed, log['id'], field))
                if 'ref' in log:
                    self.assertEqual(ids[parsed_event['ref']], log['ref'])
                if log['kind'] == 'fee':
                    spec, mine = log['spec'], parsed_event['spec']
                    self.assertEqual({k: v for k, v in mine.items() if k not in ('other',)},
                                     {k: v for k, v in spec.items() if k not in ('other',)}, (seed, log['id']))
                    if spec.get('other'):
                        self.assertEqual(mine['other'], first[spec['other']])
                if log['kind'] == 'split':
                    self.assertEqual([(k['berth'], k['holder']) for k in parsed_event['children']],
                                     [(first[k['berth']], VACANT if k['holder'] == VACANT else name[k['holder']])
                                      for k in log['children']])
                if log['kind'] == 'join':
                    self.assertEqual(parsed_event['survivor'], first[log['survivor']])
                    self.assertEqual(parsed_event['holder'], VACANT if log['holder'] == VACANT else name[log['holder']])
                if log['kind'] == 'merge':
                    self.assertEqual(parsed_event['merged'], [name[p] for p in log['merged']])

    def test_one_referent(self):
        for seed in SEEDS:
            forms = check_one_referent(seed)
            if PRINT:
                print(f'seed {seed}: mention forms', dict(forms))

    def test_checker_contract(self):
        for seed in SEEDS:
            check_answer_matches_key(seed)
            check_subset_rules(seed)
            check_names_and_preamble(seed)
            check_no_ties(seed)
            check_decoys(seed)

    def test_full_list_is_read_as_before(self):
        for seed in SEEDS:
            check_full_list_unchanged(seed)

    def test_needed_events_lie_inside_relevant(self):
        for seed in SEEDS:
            check_relevant_and_needed(seed)

    def test_needed_events_are_an_argument(self):
        failing = collections.Counter()
        for seed in SEEDS:
            note = check_needed_events_are_an_argument(seed)
            if PRINT:
                print(f'seed {seed}: {note}')
            events = module_case(seed)['events']
            table = removal_table(seed)
            needed, _ = needed_sets(table)
            for n, q in enumerate(table[0]):
                alone = big_succession.answer(q, [e for e in events if e['id'] in needed[n]])
                failing[q['kind']] += alone != table[1][n]
        # pinned on seeds 1 to 5 (120 statements): the statements where another route would do, by kind
        self.assertEqual(dict(failing), {'holder': 0, 'fee': 3, 'count': 1, 'changes': 1, 'first_held': 0, 'part': 0})

    def test_the_one_statement_with_an_empty_needed_set(self):
        empty = [(seed, q['kind'], q['party'], q['date']) for seed in SEEDS for q in empty_needed_statements(seed)]
        self.assertEqual(empty, [(3, 'count', 'P69', '2027-04-03')])

    def test_none_only_where_the_statement_has_nothing_to_start_from(self):
        for seed in SEEDS:
            check_none_means_nothing_to_start_from(seed)

    def test_names_are_dependencies(self):
        for seed, index in RULING_DRAWS:
            note = check_names_are_dependencies(seed, index)
            if PRINT:
                print(f'seed {seed} index {index}: {note}')
            check_relevant_includes_the_dependencies(seed, index)

    def test_needed_sets_include_what_fixes_a_name(self):
        """(a) of the ruling of 14 November, on seeds 1 to 5 (index 1) and 20261161 (indexes 1, 2): the statements with a
        needed event told through an indirect name whose fixer is not needed. They are two counts, pinned: in each the
        fixer's removal also removes the berth the told event is about (its grant, or the holding it leans on), so the
        count is the same without the fixer as with the event."""
        found = [(seed, index, q['kind'], q['party'], q['date']) for seed, index in RULING_DRAWS
                 for q, _ in statements_with_unneeded_name_fixer(seed, index)]
        print('statements with a needed event told through an indirect name whose fixer is not needed:', len(found), 'of',
              24 * len(RULING_DRAWS), found)
        self.assertEqual(found, [(1, 1, 'count', 'P58', '2022-09-02'), (20261161, 2, 'count', 'P10', '2026-02-06')])
        for seed, index in RULING_DRAWS:
            check_names_in_needed_sets(seed, index)

    def test_counts_do_not_turn_on_amounts(self):
        counts = 0
        for seed, index in RULING_DRAWS:
            check_counts_do_not_turn_on_amounts(seed, index)
            counts += sum(q['kind'] == 'count' for q in slot_queries(seed, index))
        print('count statements checked:', counts, '(none needs a fee or an unrelated event)')

    def test_decoy_paragraphs_decide_nothing(self):
        for seed, index in RULING_DRAWS:
            check_decoy_paragraphs_decide_nothing(seed, index)

    def test_needed_set_sizes(self):
        sizes = needed_sizes()
        print('needed-set sizes per kind of statement (statements, smallest, mean, largest):',
              {k: (n, lo, round(mean, 1), hi) for k, (n, lo, mean, hi) in sizes.items()})
        self.assertEqual(sum(v[0] for v in sizes.values()), 24 * len(RULING_DRAWS))
        self.assertTrue(all(v[1] >= 1 for k, v in sizes.items() if k != 'count'))

    def test_depends_are_consistent_with_the_reading(self):
        for seed in SEEDS:
            note = check_depends_are_consistent_with_the_reading(seed)
            if PRINT:
                print(f'seed {seed}: {note}')

    def test_depends_are_complete(self):
        for seed in SEEDS:
            note = check_depends_are_complete(seed)
            if PRINT:
                print(f'seed {seed}: {note}')

    def test_depends_on_any_list(self):
        events = module_case(1)['events']
        self.assertEqual(big_succession.depends([]), {})
        self.assertEqual(big_succession.depends([{'id': 'X'}]), {})        # not an event of this family: no dependencies, no error
        rng = random.Random(5)
        for _ in range(5):
            subset = [e for e in events if rng.random() < 0.6]
            deps = big_succession.depends(subset)
            ids = {e['id'] for e in subset}
            self.assertEqual(set(deps), ids)
            self.assertTrue(all(set(ks[k]) <= ids for ks in deps.values() for k in DEPENDENCY_KINDS))
            self.assertEqual(deps, big_succession.depends(subset))          # pure
        self.assertEqual(events, module_case(1)['events'])                  # the list is not changed

    def test_closed_needed_sets(self):
        """3. and 4. of the addition after the ruling: on seeds 1 to 5 (index 1) and 20261161 (indexes 1, 2), closing needed
        under the NAME dependencies of `depends` leaves no needed event whose name fixer is outside, in particular not in
        the two statements pinned by `test_needed_sets_include_what_fixes_a_name`; and the closed set lies inside
        `relevant`. Before the closure those two statements each had needed events whose fixer was not needed."""
        for seed, index in RULING_DRAWS:
            check_closed_needed_sets(seed, index)
        pinned = [(1, 1, 'count', 'P58', '2022-09-02'), (20261161, 2, 'count', 'P10', '2026-02-06')]
        open_gaps = {(seed, index, q['kind'], q['party'], q['date']): gaps for seed, index in RULING_DRAWS
                     for q, gaps in statements_with_unneeded_name_fixer(seed, index)}
        self.assertEqual(sorted(open_gaps), pinned)
        still_open = {}
        for seed, index in RULING_DRAWS:
            events = module_case(seed, index)['events']
            by_id = {e['id']: e for e in events}
            for q, ids, closed in closed_needed_sets(seed, index):
                gaps = [(i, n) for i in ids for n in name_dependencies(by_id[i], events) if n not in closed]
                if gaps:
                    still_open[(seed, index, q['kind'])] = gaps
                if q['kind'] == 'count' and (seed, index, q['kind'], q['party'], q['date']) in pinned:
                    print(f"pinned {(seed, index, q['party'], q['date'])}: needed events {len(ids)}, closed {len(closed)}; "
                          f"unneeded fixers before the closure {sorted(set(n for _, n in open_gaps[(seed, index, q['kind'], q['party'], q['date'])]))}, "
                          f"after: none outside the closed set")
        self.assertEqual(still_open, {})
        sizes = closed_sizes()
        print('closed needed-set sizes per kind of statement (statements, smallest, mean, largest):',
              {k: (n, lo, round(mean, 1), hi) for k, (n, lo, mean, hi) in sizes.items()})

    def test_complete_basis_is_read_and_no_misleading_basis_on_the_reviewers_sweep(self):
        """Points 1 and 4 after the review: on seeds 1 to 30 (index 1) and 20261161 (indexes 1 to 3), every needed event of
        the closed needed set is read by the reading of that set alone, and no statement is sufficient while a needed fee
        entry of its basis is not read or a quoted "holder of berth N" name lacks its renumbering."""
        problems = by_target = 0
        for seed, index in SWEEP_DRAWS:
            by_target += int(check_complete_basis_is_read(seed, index).split('(')[1].split(' ')[0])
            problems += len(basis_problems(seed, index))
        print('draws swept:', len(SWEEP_DRAWS), '; sufficient bases with an unread fee or an unfixed number:', problems,
              '; needed events not read only for want of their target:', by_target)
        self.assertEqual(problems, 0)

    def test_the_five_statements_of_the_review(self):
        """The statements the review named, with the needed set before (the module `10261636`) and after: pinned."""
        rows = {}
        for (seed, index, claim), kind in REVIEW_STATEMENTS.items():
            case = module_case(seed, index)
            row = [r for r in built(seed, index)['key']['expectations'] if r['claim_id'] == claim and r['variant'] == 'correct'][0]
            query = claims_by_text(case)[row['claim_text']]['query']
            n = slot_queries(seed, index).index(query)
            closed, quoted, cited, sufficient, _ = basis_of(seed, index, n)
            needed = needed_sets(removal_table(seed, index))[0][n]
            rows[(seed, index, claim)] = (len(needed), len(closed), sufficient)
            self.assertEqual(query['kind'], kind)
        print('the five statements of the review (needed, closed, sufficient):', rows)
        self.assertEqual(rows, REVIEW_AFTER)
        for key, (needed, closed, _) in REVIEW_BEFORE.items():
            self.assertEqual(REVIEW_AFTER[key][:2], (needed + 1, closed + 1))      # the correction or the renumbering is needed now

    def test_names_read_what_the_key_says(self):
        """Seeds 1 to 8: the key's part and holder rows read, through `names`, as the key says (a later number of a berth is
        not read as its first)."""
        for seed in range(1, 9):
            note = check_names_read_what_the_key_says(seed)
            if PRINT:
                print(f'seed {seed}: {note}')
        self.assertEqual(big_succession.FORMS, {'holder': 'name', 'fee': 'integer', 'count': 'integer', 'changes': 'integer',
                                                 'first_held': 'date', 'part': 'name'})

    def test_hand_written_lists(self):
        print('hand-written lists:', check_hand_cases())
        self.assertGreaterEqual(len(HAND_CASES), 12)
        self.assertEqual(len(HAND_CASES), 34)
        kinds = {c['name'] for c in HAND_CASES}
        for wanted in ('assignment agreed and registered', 'agreed and never registered', 'withdrawn', 'renamed firm',
                       'death with an executor named by kinship', 'berth renumbered', 'berth split', 'two berths joined',
                       'corrected assignee', 'fee corrected', 'count of berths held by a party on a date',
                       'changes of holder between two dates'):
            self.assertIn(wanted, kinds)

    def test_share_of_statements_with_a_none_only_needed_event(self):
        """The measurement behind the subset rule: of the 24 statements of each of seeds 1 to 5, the share that has a needed
        event whose removal only makes the value None, before the rule (`legacy_replay`, the reading the generator draws
        by) and after it (`answer`); and the share that has one other than the statement's own starting point (and what
        that leans on). After the rule none has one other than that."""
        total = collections.Counter()
        for seed in SEEDS:
            events = module_case(seed)['events']
            for label, table in (('before', legacy_removal_table(seed)), ('after', removal_table(seed))):
                needed, nothing = needed_sets(table)
                for q, ids in zip(table[0], nothing):
                    start, closure = starting_point(q, events)
                    total[label] += bool(ids)
                    total[label + '_beyond_start'] += bool(ids - closure)
                    total[label + '_beyond_the_start_event'] += bool(ids - start)
                total[label + '_needed'] += sum(len(ids) for ids in needed)
            total['statements'] += len(table[0])
        n = total['statements']
        print(f"statements with a needed event whose removal only makes the value None, seeds 1-5 ({n} statements): "
              f"before the subset rule {total['before']} ({total['before'] / n:.1%}), of which {total['before_beyond_start']} "
              f"({total['before_beyond_start'] / n:.1%}) beyond the starting point and what it leans on, "
              f"{total['before_beyond_the_start_event']} ({total['before_beyond_the_start_event'] / n:.1%}) beyond the starting event itself; after "
              f"{total['after']} ({total['after'] / n:.1%}), of which {total['after_beyond_start']} beyond the starting point "
              f"(and {total['after_beyond_the_start_event']} beyond the starting event itself, that is, on what it leans on); "
              f"mean needed events per statement {total['before_needed'] / n:.1f} before, {total['after_needed'] / n:.1f} after")
        self.assertGreater(total['before_beyond_start'], 0.3 * n)
        self.assertEqual(total['after_beyond_start'], 0)
        self.assertLess(total['after_needed'], total['before_needed'])

    def test_passages_map(self):
        for seed in SEEDS:
            sources, parser, _ = parsed(seed)
            case = module_case(seed)
            ids = [e['id'] for e in case['events']]
            kind_role = {'grant': 'grants', 'fee': 'sets_fee', 'pair': 'pairs', 'agree': 'agrees',
                         'register': 'registers', 'withdraw': 'withdraws', 'refuse': 'refuses',
                         'death': 'records_death', 'estate': 'passes_to_heir', 'rename': 'renames',
                         'merge': 'merges', 'surrender': 'surrenders', 'forfeit': 'forfeits',
                         'renumber': 'renumbers', 'split': 'splits', 'join': 'joins', 'fix': 'corrects',
                         'kin': 'introduces_kin', 'bought': 'introduces_purchase', 'decoy': 'decoy_passage'}
            covered = set()
            for entry in case['passages']:
                blocks = sources[entry['file']].rstrip('\n').split('\n\n')
                for event_id in entry['event_ids']:
                    mine = parser.events[ids.index(event_id)]
                    self.assertEqual(mine['block'], (entry['file'], entry['paragraph']), (seed, event_id))
                    self.assertEqual(kind_role[mine['kind']], entry['role'])
                    self.assertIn(mine['sentence'], blocks[entry['paragraph']])
                    covered.add(event_id)
                for item in entry['resolves']:
                    if item['form'] == 'later_number':
                        self.assertIn(item['text'], blocks[entry['paragraph']])
                        self.assertEqual(parser.berth_of[item['text']], item['berth_name'])
                    elif item['party']:
                        found = [r for r in parser.resolved if r['block'] == (entry['file'], entry['paragraph'])
                                 and r['text'] in (item['text'], lower_first(item['text']))]
                        self.assertTrue(any(r['key'] == item['party_name'] for r in found), (seed, item))
            self.assertEqual(covered, set(ids))

    def test_handles_are_gone(self):
        for seed in SEEDS:
            sources, parser, _ = parsed(seed)
            for name, text in sources.items():
                lines = text.splitlines()
                self.assertEqual(sum(l.startswith('<a id=') for l in lines), 1, name)
                for line in lines:
                    self.assertFalse(re.match(r'\s*\|', line), line)
                    self.assertFalse(re.match(r'\s*(?:[-*+]|\d+[.)]) ', line), line)
            for event in parser.events:
                block = sources[event['block'][0]].rstrip('\n').split('\n\n')[event['block'][1]]
                self.assertGreaterEqual(len(sentences_of(block)), 3, (seed, event['sentence']))
            names = [n for n in parser.by_name]
            for _, _, sentence in parser.stray:
                if re.search(r'\bfee\b', sentence):
                    self.assertFalse(re.search(r'£|\d{3}|pounds', sentence), sentence)
                if re.search(r'\bberths? \d', sentence):
                    self.assertFalse(any(n in sentence for n in names), sentence)
                    self.assertFalse(re.search(r'\b(?:Mr|Ms|Mrs|Miss|Dr) [A-Z]', sentence), sentence)

    def test_search_by_full_name_misses_deciding_passages(self):
        for seed in SEEDS:
            sources, parser, books = parsed(seed)
            paragraphs = {}
            for file, text in sources.items():
                for index, block in enumerate(text.rstrip('\n').split('\n\n')):
                    paragraphs[(file, index)] = block
            evaders = second = plain = naive_wrong = naive_total = 0
            for text in report_statements(built(seed)['files']['report-correct.md']):
                statement = parse_statement(parser, text)
                _, why = compute(books[None], statement)
                if statement['kind'] in ('count', 'first_held'):
                    needle = re.compile(re.escape(statement['party']))
                else:
                    needle = re.compile(r'\bberth ' + statement['berth'] + r'\b')
                where = {parser.events[i]['block'] for i in why}
                evaders += any(not needle.search(paragraphs[w]) for w in where)
                party = statement.get('party') or (statement['value'] if statement['kind'] == 'holder' else None)
                # a second passage is needed when a deciding passage points to another one: a register note to its
                # agreement, a correction to its entry, a death or merger to what the party held, a fee given as a
                # change of, or as equal to, another fee, a berth named by a later number, a party named by a short
                # form or a description, or a berth that is only part of the present one
                dependent = statement['kind'] == 'part' and bool(why) or any(
                    parser.events[i]['kind'] in ('register', 'fix', 'death', 'merge') or parser.events[i].get('described') or
                    (parser.events[i]['kind'] == 'fee' and parser.events[i]['spec']['type'] != 'abs') or
                    parser.events[i].get('label') not in (None, statement.get('berth')) and
                    parser.events[i].get('berth') == statement.get('berth') for i in why)
                for item in parser.resolved:
                    if item['event'] in why and item['form'] != 'full' and item['key'] == party:
                        dependent = True
                second += dependent
                plain += (not dependent) and len(why) == 1
                if dependent:
                    naive_total += 1
                    naive_wrong += naive_verdict(list(paragraphs.values()), statement, text) != 'supported'
            self.assertGreaterEqual(evaders / 24, 0.4, (seed, evaders))
            self.assertGreaterEqual(second / 24, 0.4, (seed, second))
            self.assertGreaterEqual(plain, 3, (seed, plain))   # statements one passage settles
            self.assertGreaterEqual(naive_wrong / max(1, naive_total), 0.5, (seed, naive_wrong, naive_total))
            if True:
                print(f'seed {seed}: {evaders}/24 statements have a deciding passage without the full name; '
                      f'{second}/24 need a second passage, {plain} are settled by one passage; naive reader wrong on '
                      f'{naive_wrong}/{naive_total} of those')

    def test_wordings_vary(self):
        families = {'iso': r'\d{4}-\d\d-\d\d', 'day month year': r'\d{1,2} [A-Z][a-z]+ \d{4}',
                    'month day, year': r'[A-Z][a-z]+ \d{1,2}, \d{4}',
                    'ordinal month year': r'\d{1,2}(?:st|nd|rd|th) [A-Z][a-z]+ \d{4}',
                    'the ordinal of month year': r'the \d{1,2}(?:st|nd|rd|th) of [A-Z][a-z]+ \d{4}',
                    'day month': r'\d{1,2} [A-Z][a-z]+', 'ordinal month': r'\d{1,2}(?:st|nd|rd|th) [A-Z][a-z]+'}
        for seed in SEEDS:
            _, parser, _ = parsed(seed)
            seen, amounts = set(), collections.Counter()
            for event in parser.events:
                for slot, text in event['slots'].items():
                    if slot in ('date', 'agreed', 'entered', 'entry', 'correct'):
                        seen.update(name for name, rx in families.items() if re.fullmatch(rx, text))
                    if slot == 'amount':
                        amounts['pound sign with comma' if re.fullmatch(r'£\d{1,3}(,\d{3})+', text) else
                                'pound sign' if text[0] == '£' else 'digits and pounds' if text[0].isdigit() else
                                'words'] += 1
            self.assertEqual(seen, set(families), (seed, seen))
            self.assertEqual(len(amounts), 4, (seed, amounts))

    def test_search_by_any_word_of_the_subject(self):
        """A reader that keeps every paragraph holding any word of the subject's name or of an alias."""
        totals = []
        for seed in SEEDS:
            sources, parser, books = parsed(seed)
            case = module_case(seed)
            paragraphs = {}
            for file, text in sources.items():
                for index, block in enumerate(text.rstrip('\n').split('\n\n')):
                    paragraphs[(file, index)] = block
            hidden = deciding = missed = described = 0
            for text in report_statements(built(seed)['files']['report-correct.md']):
                statement = parse_statement(parser, text)
                _, why = compute(books[None], statement)
                party = statement.get('party') or (statement['value'] if statement['kind'] == 'holder' else None)
                words = subject_words(case, statement)
                pattern = re.compile(r'\b(?:' + '|'.join(re.escape(w) for w in words) + r')\b')
                kept = {k for k, block in paragraphs.items() if pattern.search(block)}
                places = {i: parser.events[i]['block'] for i in why}
                missed += any(place not in kept for place in places.values())
                for i in why:
                    deciding += 1
                    hidden += not pattern.search(parser.events[i]['sentence'])
                    # the subject is named by a description (a berth by its neighbour or its holder, a party by
                    # kinship, a business sold, its berth or its role)
                    event = parser.events[i]
                    described += bool(event.get('described') and event.get('berth') == statement.get('berth')) or any(
                        r['event'] == i and r['key'] == party and r['form'] in ('kin', 'trade', 'holder', 'role')
                        for r in parser.resolved)
            totals.append((described, deciding))
            self.assertGreaterEqual(hidden / deciding, 0.25, (seed, hidden, deciding))
            self.assertGreaterEqual(described / deciding, 0.2, (seed, described, deciding))
            if PRINT:
                print(f'seed {seed}: a search by any word of the subject misses a deciding passage for {missed}/24 '
                      f'statements; {hidden}/{deciding} deciding passages name the subject by no word of its names, '
                      f'{described}/{deciding} by a description')
        self.assertGreaterEqual(sum(a for a, _ in totals) / sum(b for _, b in totals), 0.25, totals)

    def test_nothing_outside_the_map_reads_like_a_decision(self):
        """No paragraph outside the passages map holds a name form of a statement's subject with a deciding verb."""
        verbs = re.compile(r'\b(?:award\w*|paid|pay\w*|grant\w*|substitut\w*|assign\w*|regist\w*|licen[cs]\w*|'
                           r'transferr\w*|surrender\w*|forfeit\w*|correct\w*|alter\w*|settled|bought|purchas\w*|'
                           r'sold|merg\w*|renam\w*|died)\b', re.I)
        for seed in SEEDS:
            sources, parser, books = parsed(seed)
            case = module_case(seed)
            mapped = {(p['file'], p['paragraph']) for p in case['passages']}
            kept = total = deciding = 0
            subjects, short = [], 0
            blocks = {(f, i): b for f, t in sources.items() for i, b in enumerate(t.rstrip('\n').split('\n\n'))}
            for text in report_statements(built(seed)['files']['report-correct.md']):
                statement = parse_statement(parser, text)
                subjects.append(subject_words(case, statement, every_form=True))
                short += any(not verbs.search(blocks[parser.events[i]['block']]) for i in compute(books[None], statement)[1])
            for file, text in sources.items():
                if not file.startswith('correspondence'):
                    continue
                for index, block in enumerate(text.rstrip('\n').split('\n\n')):
                    if block.startswith('###'):
                        continue
                    total += 1
                    if verbs.search(block):
                        kept += 1
                        deciding += (file, index) in mapped
                    if (file, index) in mapped:
                        continue
                    for words in subjects:
                        if verbs.search(block) and re.search(r'\b(?:' + '|'.join(re.escape(w) for w in words) + r')\b', block):
                            self.fail((seed, file, index, block[:200]))
            if PRINT:
                print(f'seed {seed}: a filter on deciding verbs keeps {kept} of {total} paragraphs '
                      f'({kept / total:.1%}); {deciding / kept:.1%} of those are in the passages map; it misses a '
                      f'deciding passage for {short}/24 statements')

    def test_cross_subject_figures_and_whole_file_counts(self):
        for seed in SEEDS:
            _, parser, books = parsed(seed)
            cross = counts = 0
            for text in report_statements(built(seed)['files']['report-correct.md']):
                statement = parse_statement(parser, text)
                counts += statement['kind'] == 'count'
                _, why = compute(books[None], statement)
                cross += statement['kind'] == 'fee' and any(
                    parser.events[i]['kind'] == 'fee' and parser.events[i]['berth'] != statement['berth'] for i in why)
            self.assertGreaterEqual(cross, 4, seed)
            self.assertGreaterEqual(counts, 3, seed)
            if PRINT:
                print(f'seed {seed}: {cross} fees depend on another berth\'s figure; {counts} counts over the whole file')

    def test_headings_do_not_mark_decisions(self):
        def heading_class(heading):
            return ('letter' if heading.startswith('### Letter') else
                    'minutes' if 'minutes' in heading or 'extract' in heading else 'note')
        for seed in SEEDS:
            _, parser, _ = parsed(seed)
            deciding_days = {(e['block'][0], e['date']) for e in parser.events}
            with_events = collections.Counter(heading_class(d['heading']) for d in parser.docs
                                              if (d['file'], d['date']) in deciding_days)
            without = collections.Counter(heading_class(d['heading']) for d in parser.docs
                                          if (d['file'], d['date']) not in deciding_days)
            for kind in ('letter', 'note', 'minutes'):
                a, b = with_events[kind] / sum(with_events.values()), without[kind] / sum(without.values())
                self.assertLess(abs(a - b), 0.12, (seed, kind, a, b))

    def test_each_lure_turns_on_one_shortcut(self):
        single = total = 0
        for seed in SEEDS:
            _, parser, books = parsed(seed)
            for text, row in zip(report_statements(built(seed)['files']['report-correct.md']),
                                 rows_of(seed)['correct']):
                statement = parse_statement(parser, text)
                for name, parts in BUNDLED.items():
                    if name in row['lure_states']:
                        total += 1
                        single += sum(verdict(books[part], statement) != row['semantic_state'] for part in parts) == 1
        self.assertGreaterEqual(single / total, 0.6, (single, total))
        if PRINT:
            print(f'{single} of {total} lure statements of the two bundled readers turn on one shortcut only')

    def test_documents_are_in_date_order(self):
        for seed in SEEDS:
            _, parser, _ = parsed(seed)
            dates = [doc['date'] for doc in parser.docs]
            self.assertEqual(dates, sorted(dates), seed)
            self.assertGreater(len(dates), 450, (seed, len(dates)))
            self.assertTrue(dates[-1][:4] > dates[0][:4])
            self.assertTrue(10 <= int(dates[-1][:4]) - int(dates[0][:4]) + 1 <= 13)

    def test_answer_sheets_do_no_work(self):
        for seed in SEEDS:
            rows = rows_of(seed)['mixed']
            wrong_if_supported = sum(r['semantic_state'] != 'supported' for r in rows) / len(rows)
            wrong_if_refuted = sum(r['semantic_state'] != 'refuted' for r in rows) / len(rows)
            self.assertGreaterEqual(wrong_if_supported, 7 / 24 - 1e-9)   # generate.choose alters at least 7 of 24
            self.assertGreaterEqual(wrong_if_refuted, 0.3)
            if PRINT:
                print(f'seed {seed}: all-supported wrong on {wrong_if_supported:.1%}, all-refuted on {wrong_if_refuted:.1%}')

    def test_extras_for_the_checker(self):
        for seed in SEEDS:
            case = module_case(seed)
            json.dumps(case['events'])
            json.dumps(case['passages'])
            self.assertEqual([e['id'] for e in case['events']], sorted(e['id'] for e in case['events']))
            outcomes = collections.Counter(e.get('outcome') for e in case['events'] if e['kind'] == 'agree')
            self.assertTrue({'registered', 'never_registered', 'withdrawn', 'refused'} <= set(outcomes), outcomes)
            for true, mutations in case['slots']:
                json.dumps(true['query'])
                for mutation in mutations:
                    json.dumps(mutation['query'])
            key = built(seed)['key']
            for row in key['expectations']:
                self.assertIn('claim_text', row)

    def test_bulk_does_not_repeat(self):
        for seed in SEEDS:
            sources, parser, _ = parsed(seed)
            sizes = {}
            for file, text in sources.items():
                for index, block in enumerate(text.rstrip('\n').split('\n\n')):
                    sizes[(file, index)] = len(sentences_of(block))
            counts = collections.Counter(sentence for file, index, sentence in parser.stray
                                         if sizes[(file, index)] > 1 and len(sentence) > 40)
            total = sum(counts.values())
            repeated = sum(n for n in counts.values() if n > 1) / total
            self.assertLess(repeated, 0.08, (seed, repeated, counts.most_common(3)))
            self.assertLessEqual(max(counts.values()), 5, (seed, counts.most_common(2)))
            self.assertGreater(total, 2000, (seed, total))
            if PRINT:
                print(f'seed {seed}: {total} bulk sentences, {repeated:.1%} of them in a verbatim repeat')

    def test_statements_in_every_readers_value(self):
        for seed in SEEDS:
            slots = module_case(seed)['slots']
            for name in big_succession.READERS:
                stated = sum(any(m['error'] == name for m in mutations) for _, mutations in slots)
                self.assertGreaterEqual(stated, 4, (seed, name))
                if PRINT:
                    print(f'seed {seed}: {name}: {stated} slots with a statement in its value')

    def test_true_timelines_are_in_date_order(self):
        for seed in SEEDS:
            self.assertEqual(module_case(seed)['base']['hist'].clamped, 0, seed)

    def test_state_on_with_missing_identity_passages(self):
        """An event that leans on a renaming, renumbering, kinship or sale passage the subset lacks is left out (it was: its
        holder was None and the state incomplete): the state is that of the list without it and whatever leans on it."""
        found = collections.Counter()
        for seed in SEEDS:
            events = module_case(seed)['events']
            by_id = {e['id']: e for e in events}
            for e in events:
                if e['kind'] in ('grant', 'estate', 'surrender', 'forfeit', 'register'):
                    berths = [e['berth']]
                elif e['kind'] == 'death':
                    day_before = (datetime.date.fromisoformat(e['when']) - datetime.timedelta(days=1)).isoformat()
                    before = big_succession.state_on(events, day_before)['berths']
                    berths = [k for k, v in before.items() if v['holder'] == e['deceased']]
                else:
                    continue
                for need in e.get('needs', ()):
                    if by_id[need]['kind'] not in ('rename', 'renumber', 'kin', 'bought'):
                        continue
                    subset = [x for x in events if x['id'] != need]
                    state = big_succession.state_on(subset, e['date'])
                    self.assertEqual(state, big_succession.state_on(left_out(events, {need}), e['date']), (seed, e['id'], need))
                    if e['kind'] != 'grant':        # a berth whose grant is left out is not in the state at all
                        for berth in berths:
                            self.assertIsNotNone(state['berths'][berth]['holder'], (seed, e['id'], need))
                    found[by_id[need]['kind']] += berths != []
        for kind in ('rename', 'renumber', 'kin', 'bought'):
            self.assertGreater(found[kind], 0, (kind, dict(found)))

    def test_state_on(self):
        for seed in SEEDS:
            case = module_case(seed)
            events = case['events']
            now = case['now']
            full = big_succession.state_on(events, now)
            self.assertTrue(full['complete'])
            for true, _ in case['slots']:
                q = true['query']
                if q['kind'] == 'holder':
                    state = big_succession.state_on(events, q['date'])['berths'][q['berth']]
                    self.assertEqual(big_succession.party_names(events)[state['holder']], q['value'])
                elif q['kind'] == 'fee':
                    state = big_succession.state_on(events, f"{q['year']}-12-31")['berths'].get(q['berth'])
                    if state:
                        self.assertEqual(state['fee'], q['value'])
            # subsets: no error, and what depends on a missing event is not established
            rng = random.Random(seed)
            for _ in range(8):
                subset = [e for e in events if rng.random() < 0.7]
                for date in (events[len(events) // 3]['date'], now):
                    state = big_succession.state_on(subset, date)
                    json.dumps(state)
                for name in big_succession.READERS:
                    big_succession.state_on(subset, now, name)
            registers = [e for e in events if e['kind'] == 'register']
            for register in registers[:6]:
                # a register note whose agreement is absent is left out: the berth has the holder it had without it
                subset = [e for e in events if e['id'] != register['ref']]
                state = big_succession.state_on(subset, register['date'])
                self.assertIsNotNone(state['berths'][register['berth']]['holder'], (seed, register['id']))
                self.assertEqual(state, big_succession.state_on(left_out(events, {register['ref']}), register['date']))
            rel = [e for e in events if e['kind'] == 'fee' and (e['spec']['type'] == 'rel' or e['spec'].get('other'))]
            for e in rel[:8]:
                # a fee given by reference to an entry that is absent is left out: the berth has the fee it had without it
                spec = e['spec']
                base = [x for x in events if x['kind'] == 'fee' and x['berth'] == (spec.get('other') or e['berth'])
                        and x['seq'] < e['seq']]
                subset = [x for x in events if x['seq'] <= e['seq'] and x not in base]
                state = big_succession.state_on(subset, f"{e['year']}-12-31")['berths'].get(e['berth'])
                without = big_succession.state_on([x for x in subset if x['id'] != e['id']],
                                                  f"{e['year']}-12-31")['berths'].get(e['berth'])
                self.assertEqual(state, without, (seed, e['id']))

if __name__ == '__main__':
    if len(sys.argv) >= 4 and sys.argv[1] == '--verify':
        generate_big.register()         # `setUpModule` does not run on this path, and the process ends with the check
        sys.exit(0 if verify(int(sys.argv[2]), int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else None) else 1)
    unittest.main()
