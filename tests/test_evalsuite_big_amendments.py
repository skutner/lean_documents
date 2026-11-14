"""Checks of the large amendments family (`evalsuite/big_amendments.py`).

The round trip reads only the rendered sources, the module's phrase inventory (data) and the statements. It never
reads `case['base']`, a claim's `pred`, or the module's formatter, resolver or interpreter. The parser below recovers
every fact from the text; the evaluator below recomputes every statement of both reports and every flawed reader's
verdict from the recovered facts. The interpreter and the subset function of the module are tested separately, against
the facts the module publishes as `case['events']`.
"""
import collections
import contextlib
import datetime
import functools
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
sys.dont_write_bytecode = True
import generate  # noqa: E402
import generate_big  # noqa: E402
generate_big.register()
import big_amendments as family  # noqa: E402

INV = family.INVENTORY
SEEDS = (1, 2, 3, 4, 5)
DAY = datetime.timedelta(days=1)
NO_VALUE = 'none'
READER_NAMES = list(INV['readers'])


@contextlib.contextmanager
def size_env(size):
    """BIG_AMENDMENTS_SIZE set to `size` (None: not set) for the length of the block."""
    saved = os.environ.get(family.SIZE_VARIABLE)
    if size is None:
        os.environ.pop(family.SIZE_VARIABLE, None)
    else:
        os.environ[family.SIZE_VARIABLE] = str(size)
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop(family.SIZE_VARIABLE, None)
        else:
            os.environ[family.SIZE_VARIABLE] = saved


# Everything below that takes (seed, index) works at the size that BIG_AMENDMENTS_SIZE names when it is called, and
# remembers each draw by size. The tests of the default dossier pin size 1; those of a size say so with `size_env`.
@functools.lru_cache(maxsize=None)
def _built(seed, index, size):
    with size_env(size):
        return generate.build_case('amendments', seed, index)


@functools.lru_cache(maxsize=None)
def _raw_case(seed, index, size):
    with size_env(size):
        return family.build(random.Random(f'evalsuite:{generate.GENERATOR_VERSION}:{seed}:{index}:amendments'))


def built(seed, index=1):
    return _built(seed, index, family.current_size())


def raw_case(seed, index=1):
    return _raw_case(seed, index, family.current_size())


def sources_of(case):
    return {name[len('sources/'):]: text for name, text in case['files'].items() if name.startswith('sources/')}


# ---------------------------------------------------------------------------------------------------------
# Regular expressions from the inventory
# ---------------------------------------------------------------------------------------------------------
NUM = r'[\d,]+(?:\.\d+)?'
WORDS = r'[a-z]+(?:[- ](?:and )?[a-z]+)*'
UNITS = sorted({r['unit'] for r in INV['parameters'] if r['kind'] == 'count'})
CATS = '(?:' + '|'.join(re.escape(c) for c in INV['categories']) + ')'
NOUNS = sorted(set(INV['nouns'].values()))


def pieces(pattern):
    parts = re.split(r'\{(\w+)\}', pattern)
    return list(zip(parts[0::2] + [''], parts[1::2] + [None]))


def value_regex(kind):
    alts = []
    for f in INV['value_formats'][kind]:
        s = ''
        for lit, name in pieces(f):
            s += re.escape(lit)
            if name:
                s += {'n': NUM, 'w': WORDS, 'unit': '(?:' + '|'.join(re.escape(u) for u in UNITS) + ')',
                      'party': '(?:Customer|Supplier)', 'title': '[A-Z][A-Za-z ]+?'}[name]
        alts.append(s)
    return '(?:' + '|'.join(alts) + ')'


VALUE_ANY = '(?:' + '|'.join(value_regex(k) for k in INV['value_formats']) + ')'


def date_formats():
    out = []
    for f in INV['date_formats']:
        embed, named = '', ''
        table = {'y': (r'\d{4}', r'(?P<y>\d{4})'), 'm02': (r'\d{2}', r'(?P<m>\d{2})'),
                 'd02': (r'\d{2}', r'(?P<d>\d{2})'), 'd': (r'\d{1,2}', r'(?P<d>\d{1,2})'),
                 'dord': (r'\d{1,2}(?:st|nd|rd|th)', r'(?P<d>\d{1,2})(?:st|nd|rd|th)'),
                 'mname': ('(?i:' + '|'.join(INV['months']) + ')', '(?P<mn>(?i:' + '|'.join(INV['months']) + '))'),
                 'mabbr': ('(?:' + '|'.join(INV['month_abbr']) + ')', '(?P<ma>' + '|'.join(INV['month_abbr']) + ')')}
        for lit, name in pieces(f):
            embed += re.escape(lit)
            named += re.escape(lit)
            if name:
                embed += table[name][0]
                named += table[name][1]
        out.append((embed, re.compile('^' + named + '$')))
    return out


DATE_FORMATS = date_formats()
DATE_EMBED = '(?:' + '|'.join(e for e, _ in DATE_FORMATS) + ')'


def parse_date(text):
    for _, rx in DATE_FORMATS:
        m = rx.match(text)
        if m:
            g = m.groupdict()
            month = int(g['m']) if 'm' in g else [x.lower() for x in INV['months']].index(g['mn'].lower()) + 1 \
                if 'mn' in g else INV['month_abbr'].index(g['ma']) + 1
            return datetime.date(int(g['y']), month, int(g['d']))
    raise AssertionError('not a date: %r' % text)


def words_to_int(text):
    total, current = 0, 0
    for tok in re.split(r'[- ]', text):
        if tok == 'and':
            continue
        if tok in INV['ones']:
            current += INV['ones'].index(tok)
        elif tok in INV['tens'] and tok:
            current += INV['tens'].index(tok) * 10
        elif tok == 'hundred':
            current *= 100
        elif tok == 'thousand':
            total, current = total + current * 1000, 0
        elif tok == 'million':
            total, current = total + current * 1000000, 0
        else:
            raise AssertionError('unknown number word %r' % tok)
    return total + current


def parse_value(kind, text, row=None):
    for f in INV['value_formats'][kind]:
        s = ''
        for lit, name in pieces(f):
            s += re.escape(lit)
            if name:
                s += {'n': '(?P<n>%s)' % NUM, 'w': '(?P<w>%s)' % WORDS,
                      'unit': '(?P<unit>' + '|'.join(re.escape(u) for u in UNITS) + ')',
                      'party': '(?P<party>Customer|Supplier)', 'title': '(?P<title>[A-Z][A-Za-z ]+?)'}[name]
        m = re.fullmatch(s, text)
        if not m:
            continue
        g = m.groupdict()
        if kind == 'officer':
            if row is not None and row.get('party') != g['party']:
                continue
            return g['title']
        n = None
        if g.get('n'):
            n = float(g['n'].replace(',', ''))
            n = int(n) if n == int(n) else n
        if g.get('w') is not None and g.get('w') != '':
            w = words_to_int(g['w'])
            if n is not None and w != n:
                raise AssertionError('digits and words disagree in %r' % text)
            n = w
        if kind == 'count' and row is not None and g['unit'] != row['unit']:
            continue
        return n
    return None


def sentences_of(text):
    return [s for s in re.split(r'(?<=[.!?]) (?=[A-Z0-9"(€])', text.strip()) if s]


def low(s):
    return s if len(s) > 1 and s[1].isupper() else s[0].lower() + s[1:]


def blocks_of(text):
    return text.rstrip('\n').split('\n\n')


def compile_template(template, subs):
    out = ''
    for lit, name in pieces(low(template)):
        out += re.escape(lit)
        if name:
            out += subs[name]
    return re.compile('^' + out + '$')


# ---------------------------------------------------------------------------------------------------------
# The parser: sources -> items
# ---------------------------------------------------------------------------------------------------------
ROWS = INV['parameters']
BY_TERM = {r['term']: r for r in ROWS}
OP_SUBS = {'ref': '(?P<ref>the (?:(?! (?:for|to) %s)[^,;])+?)' % CATS, 'pref': '(?P<pref>paragraph \\d+ of .+?)', 'value': '(?P<value>%s)' % VALUE_ANY,
           'cat': '(?P<cat>%s)' % CATS, 'cdate': '(?P<cdate>%s)' % DATE_EMBED, 'sdate': '(?P<sdate>%s)' % DATE_EMBED,
           'start': '(?P<start>%s)' % DATE_EMBED, 'end': '(?P<end>%s)' % DATE_EMBED, 'event': '(?P<event>.+?)',
           'other': '(?P<other>the (?:(?! (?:for|to) %s)[^,;])+?)' % CATS, 'a': r'(?P<a>\d+)', 'b': r'(?P<b>\d+)', 'c': r'(?P<c>\d+)', 'd': r'(?P<d>\d+)', 'short': '(?P<short>[^"]+)'}


def operative_templates():
    out = []
    for kind, key in (('sub', 'substitute'), ('exc', 'exception'), ('dis', 'disapply'), ('rev', 'revoke'),
                      ('sun', 'sunset_later'), ('ren', 'renumber'), ('def', 'define'), ('rec', 'record'),
                      ('dec', 'decoy'), ('decev', 'decoy_event'), ('lnk', 'link'), ('cdate', 'commence_date'),
                      ('cevent', 'commence_event'), ('sown', 'sunset_own')):
        for t in INV[key]:
            out.append((kind, compile_template(t, OP_SUBS)))
    for field, what in INV['correct_what'].items():
        subs = dict(OP_SUBS, what=re.escape(what))
        typed = VALUE_ANY if field == 'value' else DATE_EMBED
        subs.update(old='(?P<old>%s)' % typed, new='(?P<new>%s)' % typed)
        for t in INV['correct']:
            out.append(('cor:' + field, compile_template(t, subs)))
    return out


OPERATIVE = operative_templates()


def classify(sentence):
    s = low(sentence)
    hits = []
    for kind, rx in OPERATIVE:
        m = rx.match(s)
        if m:
            hits.append((kind, m.groupdict()))
    assert len(hits) <= 1 or all(h == hits[0] for h in hits), 'ambiguous sentence %r: %s' % (
        sentence, [k for k, _ in hits])
    return hits[0] if hits else (None, None)


class Parsed:
    """Everything the text says."""

    def __init__(self, sources):
        self.sources = sources
        self.clauses = {}            # number -> heading
        self.params = {}             # key -> dict(row, clause, orig)
        self.terms = {}              # defined term -> key
        self.by_heading = {}         # heading -> [keys]
        self.insts = []              # dicts title, date, kind
        self.items = []
        self.agreement_blocks = {}   # block index -> list of ('states'|'titles'|'defines', key or term)
        self.e0 = None
        self.parse_agreement(sources['agreement.md'])
        self.parse_instruments(sources['instruments.md'])

    # -- agreement
    def parse_agreement(self, text):
        blocks = blocks_of(text)
        self.e0 = parse_date(re.search('(' + DATE_EMBED + ')', blocks[1]).group(1))
        by_heading = {}
        for row in ROWS:
            by_heading.setdefault(row['heading'], []).append(row)
        current, body = None, []
        sections = []
        for i, block in enumerate(blocks):
            m = re.match(r'^### (\d+)\. (.+)$', block)
            if m:
                current = (int(m.group(1)), m.group(2), i, [])
                sections.append(current)
            elif block.startswith('## Part'):
                current = None
            elif current is not None and re.match(r'^\d+\.\d+ ', block):
                current[3].append((i, block))
        for number, heading, at, paras in sections:
            self.clauses[number] = heading
            if heading == 'Definitions':
                for i, block in paras:
                    for s in sentences_of(re.sub(r'^\d+\.\d+ ', '', block)):
                        for template in INV['definition']:
                            m = compile_template(template, {'term': '(?P<term>[^"]+)', 'noun': '(?:%s)' % '|'.join(NOUNS),
                                                            'heading': '(?P<heading>[^"]+)'}).match(low(s))
                            if m:
                                rows = [r for r in ROWS if r['heading'] == m.group('heading')]
                                assert len(rows) == 1, 'definition names %r' % m.group('heading')
                                assert rows[0]['term'] == m.group('term'), (rows[0]['term'], m.group('term'))
                                assert m.group('term') not in self.terms
                                self.terms[m.group('term')] = rows[0]['term']
                                self.agreement_blocks.setdefault(i, []).append(('defines', rows[0]['term']))
                continue
            found = []
            for row in by_heading.get(heading, []):
                subs = {'term': re.escape(row['term']), 'noun': row['noun'], 'value': '(?P<value>%s)' % value_regex(row['kind'])}
                for name in ('party', 'act', 'trigger', 'subject', 'unit', 'conseq', 'duty'):
                    subs[name] = re.escape(row.get(name, ''))
                templates = [compile_template(t, subs) for t in INV['original'][row['kind']]]
                templates += [compile_template(t, subs) for t in INV['unset']]
                for i, block in paras:
                    for s in sentences_of(re.sub(r'^\d+\.\d+ ', '', block)):
                        for n, rx in enumerate(templates):
                            m = rx.match(low(s))
                            if m:
                                value = None
                                if m.groupdict().get('value'):
                                    value = parse_value(row['kind'], m.group('value'), row)
                                    assert value is not None, s
                                found.append((row, i, value, n >= len(INV['original'][row['kind']])))
            if by_heading.get(heading):
                assert len(found) == 1, 'clause %d %r holds %d value sentences' % (number, heading, len(found))
                row, i, value, unset = found[0]
                self.params[row['term']] = {'row': row, 'clause': number, 'orig': None if unset else value,
                                            'block': i}
                self.by_heading.setdefault(heading, []).append(row['term'])
                self.agreement_blocks.setdefault(i, []).append(('states', row['term']))
                self.agreement_blocks.setdefault(at, []).append(('titles', row['term']))
        assert len(self.params) == len(ROWS)

    # -- instruments
    def parse_instruments(self, text):
        blocks = blocks_of(text)
        current = None
        for i, block in enumerate(blocks):
            m = re.match(r'^## (.+) — dated (.+)$', block)
            if m:
                current = {'title': m.group(1), 'date': parse_date(m.group(2)), 'index': len(self.insts)}
                for kind, title in INV['kind_title'].items():
                    if m.group(1).startswith(title + ' ('):
                        current['kind'] = kind
                self.insts.append(current)
                continue
            m = re.match(r'^(\d+)\. (.*)$', block, re.S)
            if m and current is not None:
                self.paragraph(current, int(m.group(1)), i, m.group(2))
        titles = [i['title'] for i in self.insts]
        assert len(set(titles)) == len(titles)
        assert [i['date'] for i in self.insts] == sorted({i['date'] for i in self.insts})

    def paragraph(self, inst, par, block, text):
        op, commence, sunset, decoy = None, None, None, None
        for s in sentences_of(text):
            kind, g = classify(s)
            if kind is None:
                continue
            if kind in ('dec', 'decev'):
                decoy = (kind, g)
                continue
            if kind in ('cdate', 'cevent'):
                assert commence is None
                commence = ('date', parse_date(g['cdate'])) if kind == 'cdate' else ('event', g['event'])
            elif kind == 'sown':
                assert sunset is None
                sunset = parse_date(g['sdate'])
            else:
                assert op is None, 'two operative sentences in one paragraph: %r' % text
                op = (kind, g)
        if op is None and decoy is not None:
            assert commence is None and sunset is None
            self.items.append({'id': len(self.items), 'kind': decoy[0], 'inst': inst['index'], 'par': par, 'block': block,
                               'file': 'instruments.md', 'at': (inst['index'], par), 'date': inst['date'], 'g': decoy[1]})
            return
        if op is None:
            assert commence is None and sunset is None
            return
        kind, g = op
        item = {'id': len(self.items), 'kind': kind, 'inst': inst['index'], 'par': par, 'block': block,
                'file': 'instruments.md', 'at': (inst['index'], par), 'date': inst['date'], 'g': g}
        if kind in ('sub', 'exc', 'dis', 'lnk'):
            if g.get('cdate'):
                assert commence is None
                commence = ('date', parse_date(g['cdate']))
            if kind == 'dis':
                commence = ('date', parse_date(g['start']))
                assert sunset is None
            item.update(commence=commence, sunset=sunset, cat=g.get('cat'),
                        end=parse_date(g['end']) if kind == 'dis' else None, ref=g['ref'])
            if kind in ('sub', 'exc'):
                item['raw_value'] = g['value']
            if kind == 'lnk':
                item['other'] = g['other']
            assert commence is not None
        elif kind == 'rev':
            assert commence is not None and sunset is None
            item['commence'] = commence
        elif kind == 'ren':
            item.update(first=int(g['a']), last=int(g['b']), shift=int(g['c']) - int(g['a']),
                        effective=parse_date(g['cdate']))
            assert int(g['d']) - int(g['b']) == item['shift']
            assert commence is None and sunset is None
        elif kind.startswith('cor:'):
            item['field'] = kind[4:]
        elif kind == 'rec':
            item['phrase'] = g['event']
            assert g['event'] in INV['event_phrases'], g['event']
        elif kind == 'def':
            assert commence is None and sunset is None
        self.items.append(item)


# ---------------------------------------------------------------------------------------------------------
# References and the evaluator (written from procedure.md, not from the module)
# ---------------------------------------------------------------------------------------------------------
def ref_regexes():
    number = [re.compile('^' + ''.join(re.escape(l) + ('(?:%s)' % '|'.join(NOUNS) if n == 'noun' else r'(?P<n>\d+)' if n == 'n' else '')
                                         for l, n in pieces(f)) + '$') for f in INV['ref_frames']['number']]
    heading_frame = INV['ref_frames']['heading']
    heading = re.compile('^' + ''.join(re.escape(l) + ('(?:%s)' % '|'.join(NOUNS) if n == 'noun' else '(?P<heading>[^"]+)' if n == 'heading' else '')
                                       for l, n in pieces(heading_frame)) + '$')
    description = {}
    for kind, frame in INV['ref_frames']['description'].items():
        description[kind] = re.compile('^' + ''.join(
            re.escape(l) + ('(?P<topic>.+?)' if n == 'topic' else '(?P<party>Customer|Supplier)' if n == 'party' else '')
            for l, n in pieces(frame)) + '$')
    return number, heading, description


NUMBER_RX, HEADING_RX, DESCRIPTION_RX = ref_regexes()
PREF_RX = [re.compile(r'^paragraph (?P<n>\d+) of (?P<title>.+)$'),
           re.compile(r'^paragraph (?P<n>\d+) of the (?P<kind>[a-z ]+) dated (?P<date>%s)$' % DATE_EMBED)]


class World:
    """The facts of a parsed dossier, resolved. `drop` removes passages (by item id) to see what depends on them."""

    def __init__(self, parsed, drop=frozenset(), strict=True):
        self.p, self.strict = parsed, strict
        items = [it for it in parsed.items if it['id'] not in drop]
        self.renumbers = [it for it in items if it['kind'] == 'ren']
        self.records = {}
        for it in items:
            if it['kind'] == 'rec':
                assert it['phrase'] not in self.records, 'event recorded twice'
                self.records[it['phrase']] = (it['date'], it['id'])
        self.shorts = []
        for it in sorted((i for i in items if i['kind'] == 'def'), key=lambda i: i['at']):
            found = self.resolve(it['g']['ref'], it['date'], it['at'])
            if found is None:
                continue
            assert all(s != it['g']['short'] for s, _, _, _ in self.shorts), 'expression defined twice'
            self.shorts.append((it['g']['short'], found[0], it['at'], it['id']))
        self.provs = []
        for it in items:
            if it['kind'] in ('sub', 'exc', 'dis', 'lnk'):
                found = self.resolve(it['ref'], it['date'], it['at'])
                if found is None:
                    continue
                link = None
                if it['kind'] == 'lnk':
                    other = self.resolve(it['other'], it['date'], it['at'])
                    if other is None:
                        continue
                    link = other[0]
                key, style, cited, via = found
                row = parsed.params[key]['row']
                pv = {'id': it['id'], 'key': key, 'op': {'sub': 'substitute', 'lnk': 'substitute', 'exc': 'exception', 'dis': 'disapply'}[it['kind']],
                      'cat': it['cat'], 'commence': it['commence'], 'sunset': it['sunset'], 'end': it['end'],
                      'at': it['at'], 'date': it['date'], 'style': style, 'cited': cited, 'via': via, 'item': it}
                pv['link'] = link
                pv['value'] = None if it['kind'] in ('dis', 'lnk') else parse_value(row['kind'], it['raw_value'], row)
                if pv['value'] is None and it['kind'] not in ('dis', 'lnk'):
                    assert not strict, it['raw_value']
                    continue
                self.provs.append(pv)
        self.provs.sort(key=lambda pv: pv['at'])
        at_of = {pv['at']: pv for pv in self.provs}
        self.revokes, self.sunsets, self.corrections = {}, {}, {}
        for it in items:
            if it['kind'] in ('rev', 'sun') or it['kind'].startswith('cor:'):
                target = self.target(it['g']['pref'])
                if target not in at_of:
                    assert not strict, 'target of %r missing' % it['g']['pref']
                    continue
                pv = at_of[target]
                if it['kind'] == 'rev':
                    self.revokes.setdefault(target, []).append((it['commence'], it['id']))
                elif it['kind'] == 'sun':
                    self.sunsets.setdefault(target, []).append((parse_date(it['g']['sdate']), it['id']))
                else:
                    field = it['field']
                    row = parsed.params[pv['key']]['row']
                    if field == 'value':
                        old, new = parse_value(row['kind'], it['g']['old'], row), parse_value(row['kind'], it['g']['new'], row)
                        assert old == pv['value'], 'a correction misstates the printed value'
                    else:
                        old, new = parse_date(it['g']['old']), parse_date(it['g']['new'])
                        printed = {'commencement': pv['commence'][1], 'sunset': pv['sunset'], 'end': pv['end']}[field]
                        assert old == printed, 'a correction misstates the printed date (%s)' % field
                    self.corrections.setdefault(target, {})[field] = (new, it['id'])

    # -- references
    def target(self, text):
        found = []
        for rx in PREF_RX:
            m = rx.match(text)
            if m:
                g = m.groupdict()
                if 'title' in g:
                    found += [i for i in self.p.insts if i['title'] == g['title']]
                else:
                    day = parse_date(g['date'])
                    found += [i for i in self.p.insts if i['date'] == day and INV['kind_noun'][i['kind']] == g['kind']]
                number = int(m.group('n'))
        assert len(found) == 1, 'paragraph reference %r names %d instruments' % (text, len(found))
        return (found[0]['index'], number)

    def heading_of(self, number, day):
        found = []
        for orig, heading in self.p.clauses.items():
            n = orig
            for r in sorted(self.renumbers, key=lambda r: r['effective']):
                if r['effective'] <= day and n >= r['first']:
                    n += r['shift']
            if n == number:
                found.append(heading)
        return found

    def resolve(self, text, day, at, shorts=True):
        """(key, style, cited number, id of the defining paragraph) or None; strict mode demands exactly one referent."""
        found = []
        m = re.match(r'^the (.+)$', text)
        if m:
            name = m.group(1)
            if name in self.p.terms:
                found.append((self.p.terms[name], 'term', None, None))
            if shorts:
                for short, key, pos, ident in self.shorts:
                    if short == name and pos < at:
                        found.append((key, 'short', None, ident))
        for rx in NUMBER_RX:
            m = rx.match(text)
            if m:
                for heading in self.heading_of(int(m.group('n')), day):
                    for key in self.p.by_heading.get(heading, []):
                        found.append((key, 'number', int(m.group('n')), None))
        m = HEADING_RX.match(text)
        if m:
            for key in self.p.by_heading.get(m.group('heading'), []):
                found.append((key, 'heading', None, None))
        for kind, rx in DESCRIPTION_RX.items():
            m = rx.match(text)
            if m:
                for heading, keys in self.p.by_heading.items():
                    if low(heading) == m.group('topic'):
                        for key in keys:
                            row = self.p.params[key]['row']
                            if row['kind'] == kind and (kind != 'days' or row['party'] == m.group('party')):
                                found.append((key, 'description', None, None))
        if self.strict:
            assert len(found) == 1, 'reference %r has %d referents: %r' % (text, len(found), found)
        return found[0] if len(found) == 1 else None

    # -- the reading
    def correction(self, pv, field, reader):
        got = self.corrections.get(pv['at'], {}).get(field)
        if got is None or reader not in (None, 'applies_everything') or (
                reader == 'applies_everything' and field not in ('value', 'end')):
            return None
        return got[0]

    def start(self, pv, reader):
        if reader == 'applies_everything':
            return pv['date'] if pv['op'] != 'disapply' else pv['commence'][1]
        kind, what = pv['commence']
        if kind == 'event':
            got = self.records.get(what)
            return got[0] if got else None
        return self.correction(pv, 'commencement', reader) or what

    def stop(self, pv, reader):
        if pv['op'] == 'disapply':
            return (self.correction(pv, 'end', reader) or pv['end']) + DAY
        if reader == 'applies_everything':
            return None
        dates = [d for d in [self.correction(pv, 'sunset', reader) or pv['sunset']] if d]
        if reader is None:
            dates += [d for d, _ in self.sunsets.get(pv['at'], [])]
        return min(dates) if dates else None

    def in_effect(self, pv, day, reader):
        begin, end = self.start(pv, reader), self.stop(pv, reader)
        if begin is None or day < begin or (end is not None and day >= end):
            return False
        if reader is None:
            for commence, _ in self.revokes.get(pv['at'], []):
                began = commence[1] if commence[0] == 'date' else (self.records.get(commence[1]) or (None,))[0]
                if began is not None and began <= day:
                    return False
        return True

    def usable(self, pv, reader):
        if reader == 'reads_agreement_only':
            return False
        if reader == 'searches_by_clause_number':
            return pv['style'] == 'number' and pv['cited'] == self.p.params[pv['key']]['clause']
        return True

    def value(self, key, cat, day, reader=None):
        spec = self.p.params[key]
        best = None
        for pv in self.provs:
            if pv['key'] == key and pv['cat'] in (None, cat) and self.usable(pv, reader) and self.in_effect(pv, day, reader):
                best = pv
        if best is None:
            return NO_VALUE if spec['orig'] is None else spec['orig']
        if best['op'] == 'disapply':
            return NO_VALUE
        if best['link'] is not None:                   # the value the other parameter has when this one takes effect
            return self.value(best['link'], None, self.start(best, reader), reader)
        new = self.correction(best, 'value', reader)
        return best['value'] if new is None else new

    def count(self, key, day, reader=None):
        found = set()
        for pv in self.provs:
            if pv['key'] == key and self.usable(pv, reader):
                begin = self.start(pv, reader)
                if begin is not None and begin <= day and self.in_effect(pv, begin, reader):
                    found.add(pv['at'][0])
        return len(found)

    def total(self, day, reader=None):
        found = 0
        for pv in self.provs:
            if self.usable(pv, reader):
                begin = self.start(pv, reader)
                if begin is not None and begin <= day and self.in_effect(pv, begin, reader):
                    found += 1
        return found

    # -- counts over the whole file (rule 10 of procedure.md, written again here from the rule, not from the module)
    def took(self, pv, reader):
        """The date on which a provision took effect: it had effect on its commencement date. None: it never did."""
        begin = self.start(pv, reader)
        return begin if begin is not None and self.in_effect(pv, begin, reader) else None

    def counted(self, q, reader=None):
        """The provisions (their `at`) that a statement of a provision-counting shape counts, or None for the shapes
        that count parameters or instruments."""
        kind = q['kind']
        usable = [pv for pv in self.provs if self.usable(pv, reader)]
        if kind == 'never':
            return {pv['at'] for pv in usable if self.took(pv, reader) is None}
        if kind == 'on':
            return {pv['at'] for pv in usable if self.in_effect(pv, q['date'], reader)}
        low = q['date'] if kind == 'between' else None
        high = q['date2'] if kind == 'between' else q['date']
        found = set()
        for pv in usable:
            began = self.took(pv, reader)
            if began is None or began > high or (low is not None and began < low):
                continue
            if kind == 'class' and pv['op'] != q['class']:
                continue
            found.add(pv['at'])
        return found

    def whole(self, q, reader=None):
        if q['kind'] in ('params', 'insts'):
            at_of = {pv['at']: pv for pv in self.provs}
            both = [at_of[at] for at in self.counted(dict(q, kind='total'), reader)]
            return len({pv['key'] for pv in both}) if q['kind'] == 'params' else len({pv['at'][0] for pv in both})
        return len(self.counted(q, reader))

    def first(self, key, value, reader=None):
        days = {self.p.e0}
        for pv in self.provs:
            if pv['key'] == key and pv['cat'] is None and self.usable(pv, reader):
                for d in (self.start(pv, reader), self.stop(pv, reader)):
                    if d is not None:
                        days.add(d)
                if reader is None:
                    for commence, _ in self.revokes.get(pv['at'], []):
                        d = commence[1] if commence[0] == 'date' else (self.records.get(commence[1]) or (None,))[0]
                        if d is not None:
                            days.add(d)
        for d in sorted(days):
            if d >= self.p.e0 and self.value(key, None, d, reader) == value:
                return d
        return None


WHOLE_KINDS = ('total', 'between', 'class', 'on', 'params', 'insts', 'never')
STATE_SUBS = {'date': '(?P<date>%s)' % DATE_EMBED, 'date2': '(?P<date2>%s)' % DATE_EMBED,
              'term': '(?P<term>%s)' % '|'.join(re.escape(t) for t in sorted(BY_TERM, key=len, reverse=True)),
              'cat': '(?P<cat>%s)' % CATS, 'value': '(?P<value>%s)' % VALUE_ANY, 'n': r'(?P<n>\d+)',
              'catpart': '(?: for (?P<cat>%s))?' % CATS}
STATE_RX = [(kind, compile_template(t, STATE_SUBS)) for kind, ts in INV['statements'].items() for t in ts]


def parse_statement(text, parsed):
    hits = []
    for kind, rx in STATE_RX:
        m = rx.match(low(text))
        if m:
            hits.append((kind, m.groupdict()))
    assert len(hits) == 1, 'statement %r matches %d templates' % (text, len(hits))
    kind, g = hits[0]
    if kind == 'total':
        return {'kind': 'total', 'key': None, 'cat': None, 'date': parse_date(g['date']), 'stated': int(g['n'])}
    if kind in ('between', 'class_dis', 'class_exc', 'on', 'params', 'insts', 'never'):
        q = {'kind': kind.split('_')[0], 'key': None, 'cat': None, 'stated': int(g['n'])}
        if kind != 'never':
            q['date'] = parse_date(g['date'])
        if kind == 'between':
            q['date2'] = parse_date(g['date2'])
        if kind.startswith('class_'):
            q['class'] = {'class_dis': 'disapply', 'class_exc': 'exception'}[kind]
        return q
    row = BY_TERM[g['term']]
    q = {'kind': kind.split('_')[0], 'key': g['term'], 'cat': g.get('cat'), 'row': row}
    if kind in ('value', 'value_cat'):
        q['date'], q['stated'] = parse_date(g['date']), parse_value(row['kind'], g['value'], row)
        assert q['stated'] is not None, text
    elif kind in ('none', 'none_cat'):
        q['kind'], q['date'], q['stated'] = 'value', parse_date(g['date']), NO_VALUE
    elif kind == 'count':
        q['date'], q['stated'] = parse_date(g['date']), int(g['n'])
    elif kind == 'first':
        q['date'], q['value'] = parse_date(g['date']), parse_value(row['kind'], g['value'], row)
        q['stated'] = q['date']
    else:
        q['kind'], q['date'], q['date2'], q['stated'] = 'differs', parse_date(g['date']), parse_date(g['date2']), kind == 'differs'
    return q


def compute(world, q, reader=None):
    if q['kind'] == 'value':
        return world.value(q['key'], q['cat'], q['date'], reader)
    if q['kind'] == 'count':
        return world.count(q['key'], q['date'], reader)
    if q['kind'] == 'total':
        return world.total(q['date'], reader)
    if q['kind'] in WHOLE_KINDS:
        return world.whole(q, reader)
    if q['kind'] == 'first':
        return world.first(q['key'], q['value'], reader)
    return world.value(q['key'], q['cat'], q['date'], reader) != world.value(q['key'], q['cat'], q['date2'], reader)


def verdict(world, q, reader=None):
    return 'supported' if compute(world, q, reader) == q['stated'] else 'refuted'


@functools.lru_cache(maxsize=None)
def _parsed_of(seed, index, size):
    return Parsed(sources_of(_built(seed, index, size)))


@functools.lru_cache(maxsize=None)
def _world_of(seed, index, size):
    return World(_parsed_of(seed, index, size))


def parsed_of(seed, index=1):
    return _parsed_of(seed, index, family.current_size())


def world_of(seed, index=1):
    return _world_of(seed, index, family.current_size())


def rows_of(seed, variant=None, index=1):
    return [r for r in built(seed, index)['key']['expectations'] if variant in (None, r['variant'])]


def rows_of_size(seed, variant, size, index=1):
    return [r for r in _built(seed, index, size)['key']['expectations'] if r['variant'] == variant]


def deciding(parsed, base, q):
    """Items (ids) on which the true answer of a statement rests: dropping one changes it, plus what these act on."""
    answer = compute(base, q)
    key = q['key']
    related = set()
    if q['kind'] in WHOLE_KINDS:                 # a count over every parameter rests on every amending provision
        related = {i['id'] for i in base.p.items if i['kind'] not in ('dec', 'decev')}
    keys = {key}
    for _ in range(3):                           # a value taken over from another parameter makes that one relevant
        keys |= {pv['link'] for pv in base.provs if pv['key'] in keys and pv['link'] is not None}
    for pv in base.provs:
        if pv['key'] in keys and key is not None:
            related.add(pv['id'])
            for table in (base.revokes, base.sunsets):
                related.update(i for _, i in table.get(pv['at'], []))
            related.update(c[1] for c in base.corrections.get(pv['at'], {}).values())
            if pv['commence'][0] == 'event' and pv['commence'][1] in base.records:
                related.add(base.records[pv['commence'][1]][1])
            if pv['via'] is not None:
                related.add(pv['via'])
    related.update(r['id'] for r in base.renumbers)
    needed = set()
    for ident in sorted(related):
        if compute(World(base.p, frozenset([ident]), strict=False), q) != answer:
            needed.add(ident)
    by_id = {it['id']: it for it in base.p.items}
    at_of = {pv['at']: pv for pv in base.provs}
    todo = list(needed)
    while todo:
        it = by_id[todo.pop()]
        links = []
        if it['kind'] in ('rev', 'sun') or it['kind'].startswith('cor:'):
            target = at_of.get(base.target(it['g']['pref']))
            links.append(target['id'] if target else None)
        if it['kind'] in ('sub', 'exc', 'dis', 'lnk'):
            pv = next(p for p in base.provs if p['id'] == it['id'])
            if pv['via'] is not None:
                links.append(pv['via'])
            if pv['commence'][0] == 'event' and pv['commence'][1] in base.records:
                links.append(base.records[pv['commence'][1]][1])
        for link in links:
            if link is not None and link not in needed:
                needed.add(link)
                todo.append(link)
    return needed


def slug(term):
    return term.lower().replace(' ', '_')


def naive_value(sources, term, day):
    """A naive script reader: full-name search plus a number pattern; no inventory, no rules."""
    number = re.compile(r'(\d[\d,.]*)\s*(?:calendar days|days|per cent|percent|%|euros)|EUR ([\d,]+)|€([\d,]+)')
    found, date = None, None
    for block in blocks_of(sources['instruments.md']):
        m = re.match(r'^## .* — dated (.+)$', block)
        if m:
            date = parse_date(m.group(1))
        elif date is not None and date <= day and term in block and number.search(block):
            found = number.search(block)
    if found is None:
        for block in blocks_of(sources['agreement.md']):
            if term in block and not block.startswith('1.') and number.search(block):
                found = number.search(block)
    if found is None:
        return None
    text = next(g for g in found.groups() if g)
    return float(text.replace(',', ''))


# ---------------------------------------------------------------------------------------------------------
# The checks that `--verify SEED INDEX [FIXTURES_DIR]` runs on one draw, and that the tests run on seeds 1 to 5
# ---------------------------------------------------------------------------------------------------------
LATE = '2060-01-01'                # the file of a larger size runs well past 2040


def check_rebuild(seed, index):
    case, raw = built(seed, index), raw_case(seed, index)
    assert sources_of(case) == raw['sources'], 'the builder and the generator disagree on the sources'
    return 'sources rebuilt identically'


def check_round_trip(seed, index):
    parsed, world = parsed_of(seed, index), world_of(seed, index)
    rows = rows_of(seed, None, index)
    for row in rows:
        q = parse_statement(row['claim_text'], parsed)
        assert verdict(world, q) == row['semantic_state'], (row['claim_id'], row['variant'], row['claim_text'])
        for name in READER_NAMES:
            expected = row.get('lure_states', {}).get(name, row['semantic_state'])
            assert verdict(world, q, name) == expected, (name, row['claim_id'], row['variant'], row['claim_text'])
    return '%d statements, each verdict and each reader recomputed from the text' % len(rows)


def check_one_referent(seed, index):
    world, parsed = world_of(seed, index), parsed_of(seed, index)       # strict: exactly one referent at every point
    terms = list(parsed.terms)
    shorts = [x[0] for x in world.shorts]
    assert len(set(terms + shorts)) == len(terms) + len(shorts), 'an expression is defined twice'
    for term in terms:
        assert not any(term in short for short in shorts), term
    return '%d amending provisions and %d definitions, every reference resolves to exactly one thing' % (
        len(world.provs), len(world.shorts))


def meaning_of(e, clause_of, gone):
    """What a fact cannot be read without, written here from the reading rule (section 3.3 of the spec), not taken from
    the module: its requirements, but not the event it waits on, and, for a provision that takes over the value of
    another parameter, not the requirement that names that parameter when its clause is among `gone` (without the
    clause that parameter has no value, and the provision gives none)."""
    need = list(e.get('requires', ()))
    c = e.get('commencement')
    if c and c['type'] == 'event' and e['kind'] in ('amendment', 'revocation'):
        need = [n for n in need if n != c['event']]
    if e.get('link') and e['kind'] == 'amendment' and need and clause_of[e['link']['parameter']] in gone:
        need = need[:-1]
    return need


def left_out(events, gone):
    """`gone` and every fact that cannot be read without one of them: the whole file as it reads without them."""
    clause_of = {e['parameter']: e['id'] for e in events if e['kind'] == 'parameter'}
    gone = set(gone)
    grew = True
    while grew:
        grew = False
        for e in events:
            if e['id'] not in gone and any(n in gone for n in meaning_of(e, clause_of, gone)):
                gone.add(e['id'])
                grew = True
    return [e for e in events if e['id'] not in gone]


def reading_queries(events, rng):
    """Queries to compare readings on: every shape of whole-file count, and the statements of a few parameters."""
    out = list(whole_queries(LATE)) + list(whole_queries('2033-06-30', '2034-12-31'))
    for key in rng.sample(sorted({e['parameter'] for e in events if e['kind'] == 'amendment'}), 6):
        kind = next(e['value_kind'] for e in events if e['kind'] == 'parameter' and e['parameter'] == key)
        value = next((e['value'] for e in events if e['kind'] == 'amendment' and e['parameter'] == key and e['value'] is not None), None)
        out += [{'kind': 'value', 'parameter': key, 'date': day, 'category': None, 'value_kind': kind}
                for day in ('2032-01-01', '2033-06-30', LATE)]
        out += [{'kind': 'count', 'parameter': key, 'date': LATE}, {'kind': 'differs', 'parameter': key, 'date': '2032-06-01',
                                                                  'date2': LATE, 'category': None}]
        if value is not None:
            out.append({'kind': 'first', 'parameter': key, 'value': value})
    return out


def check_subset_rules(seed, index):
    """A subset is read as the whole file under the procedure's rules: a fact that cannot be read without something the
    subset lacks is left out, and what waits on an event the subset lacks reads as if no instrument recorded it."""
    events = raw_case(seed, index)['events']
    rng = random.Random(seed * 1000 + index)
    queries = reading_queries(events, rng)
    for e in events:
        rest = [x for x in events if x['id'] != e['id']]
        if e['kind'] == 'event' and e['date'] is not None:
            # an event the subset lacks is read as an event that no instrument records
            never = [dict(x, date=None, recorded_by=None) if x['id'] == e['id'] else x for x in events]
            for q in queries:
                assert family.answer(q, rest) == family.answer(q, never), ('event', e['id'], q)
            continue
        gone = left_out(events, [e['id']])
        for q in queries:
            assert family.answer(q, rest) == family.answer(q, gone), (e['kind'], e['id'], q)
    # what is established at all: the clause of the parameter, and counts of the whole file never are not
    for q in queries:
        if q['kind'] in family.WHOLE_FILE:
            assert isinstance(family.answer(q, []), int) and family.answer(q, []) == 0, q
            for reader in family.READERS:
                assert isinstance(family.answer(q, [e for e in events if rng.random() < 0.5], reader), int), (q, reader)
    for e in events:
        if e['kind'] == 'parameter':
            rest = [x for x in events if x['id'] != e['id']]
            for kind, extra in (('value', {'date': LATE, 'category': None}), ('count', {'date': LATE}),
                                ('first', {'value': 1}), ('differs', {'date': '2032-01-01', 'date2': LATE, 'category': None})):
                assert family.answer(dict(extra, kind=kind, parameter=e['parameter']), rest) is None, (kind, e['id'])
    for subset in ([], [e for e in events if e['kind'] == 'parameter']):          # nothing but the clauses: the agreement
        for e in subset:
            q = {'kind': 'value', 'parameter': e['parameter'], 'date': LATE, 'category': None}
            assert family.answer(q, subset) == (e['value'] if e['value'] is not None else family.NONE_VALUE), e['id']
    # a subset never raises, whatever it lacks
    amendments = [e for e in events if e['kind'] == 'amendment']
    for _ in range(40):
        subset = [e for e in events if rng.random() < 0.8]
        for e in rng.sample(amendments, 4):
            for q in ({'kind': 'value', 'parameter': e['parameter'], 'date': e['date'], 'category': e['category']},
                      {'kind': 'count', 'parameter': e['parameter'], 'date': LATE},
                      {'kind': 'differs', 'parameter': e['parameter'], 'date': e['date'], 'date2': LATE},
                      {'kind': 'first', 'parameter': e['parameter'], 'value': e['value']}) + whole_queries(e['date']):
                for reader in (None,) + tuple(family.READERS):
                    got = family.answer(q, subset, reader)
                    assert got is None or isinstance(got, (int, float, str)), got
    assert family.answer({'kind': 'value'}, events) is None            # a query that is not one: not an error
    for kind in family.WHOLE_FILE:
        if kind != 'never':                                             # which has no date to miss
            assert family.answer({'kind': kind}, events) is None
    return ('a fact that cannot be read without what is absent is left out (%d facts removed in turn, %d queries each); '
            'an absent event reads as an unrecorded one; counts of the whole file are never not established; '
            'random subsets never raise' % (len(events), len(queries)))


def whole_queries(day, day2=None):
    """One query of each shape of statement about the whole file."""
    day2 = day2 or LATE
    return tuple([{'kind': 'total', 'date': day}, {'kind': 'between', 'date': day, 'date2': day2},
                  {'kind': 'class', 'class': 'disapplication', 'date': day}, {'kind': 'class', 'class': 'exception', 'date': day},
                  {'kind': 'on', 'date': day}, {'kind': 'params', 'date': day}, {'kind': 'insts', 'date': day},
                  {'kind': 'never'}])


def claim_queries(seed, index):
    queries = {}
    for true, mutations in raw_case(seed, index)['slots']:
        for item in [true] + mutations:
            queries[item['text']] = item['query']
    return queries


@functools.lru_cache(maxsize=None)
def _needed(seed, index, size, spot):
    """The ids of the events whose removal from the full list changes the answer of a query (or makes it not
    established), found by removing every event in turn. A statement and its wrong versions share one query but for
    the stated value, so they share one answer here."""
    events = _raw_case(seed, index, size)['events']
    q = json.loads(spot)
    base = family.answer(q, events)
    needed, nothing = set(), set()
    for e in events:
        got = family.answer(q, [x for x in events if x['id'] != e['id']])
        if got != base:
            needed.add(e['id'])
            if got is None:
                nothing.add(e['id'])
    return frozenset(needed), frozenset(nothing)


def needed_of(seed, index, q):
    return needed_and_nothing(seed, index, q)[0]


def needed_and_nothing(seed, index, q):
    """(needed, those whose removal makes the answer not established)"""
    return _needed(seed, index, family.current_size(), json.dumps({k: v for k, v in q.items() if k != 'stated'},
                                                                  sort_keys=True))


def check_relevant_and_needed(seed, index):
    events = raw_case(seed, index)['events']
    shown = 0
    for text, q in sorted(claim_queries(seed, index).items()):
        if q is None:
            continue
        relevant = set(family.relevant(q, events))
        needed = needed_of(seed, index, q)
        assert needed <= relevant, (text, sorted(needed - relevant))
        shown += 1
    return 'needed is inside relevant for %d statements (every event removed in turn from the full list)' % shown


def check_needed_events_are_an_argument(seed, index):
    """For every statement: the needed events (those whose removal from the full list changes the answer, to another
    value or to not established) are inside `relevant` (a); `answer` on the needed events alone is the true value (b);
    and the only needed event whose removal makes the answer not established is the clause of the statement's own
    parameter (c). (b) cannot hold for a count that one of several provisions of one instrument, parameter or
    instrument supports (each of two provisions would do, so neither is needed): counts per parameter, of parameters
    and of instruments are exempt from (b)."""
    events = raw_case(seed, index)['events']
    by_id = {e['id']: e for e in events}
    seen, argued, redundant = set(), 0, 0
    for text, q in sorted(claim_queries(seed, index).items()):
        spot = json.dumps({k: v for k, v in q.items() if k != 'stated'}, sort_keys=True)
        if spot in seen:
            continue
        seen.add(spot)
        need, nothing = needed_and_nothing(seed, index, q)
        assert need <= set(family.relevant(q, events)), (text, 'a')
        true = family.answer(q, events)
        alone = family.answer(q, [e for e in events if e['id'] in need])
        if q['kind'] in ('count', 'params', 'insts'):
            redundant += alone != true
        else:
            assert alone == true, (text, 'b', alone, true)
        argued += 1
        for i in nothing:
            assert by_id[i]['kind'] == 'parameter' and by_id[i]['parameter'] == q.get('parameter'), (text, 'c', i)
    return ('%d distinct statements: needed inside relevant; the needed events alone give the true value (%d counts '
            'supported by several provisions exempt); removal makes the answer not established only for the '
            "statement's own clause" % (argued, redundant))


def check_deciding_events(seed, index):
    events = raw_case(seed, index)['events']
    by_id = {e['id']: e for e in events}
    for text, q in sorted(claim_queries(seed, index).items()):
        base = family.answer(q, events)
        need = needed_of(seed, index, q)
        assert need, 'empty needed set: ' + text
        if base != family.answer(q, events, 'reads_agreement_only'):
            assert any(by_id[i]['kind'] != 'parameter' and by_id[i].get('source') != 'agreement' for i in need), (
                'no single deciding fact: ' + text)
    return 'no empty needed set and no value without a deciding fact'


def check_interface(seed, index):
    case = raw_case(seed, index)
    checked = 0
    for true, mutations in case['slots']:
        for item in [true] + mutations:
            q = item['query']
            assert item['stated'] is not None and item['quantity'] is not None and q['stated'] == item['stated']
            form = family.value_form(q)
            assert form in ('integer', 'decimal', 'date', 'month', 'name') or (
                form[0] == 'choice' and set(form[1]) == {'same', 'different'}), form
            got = family.answer(q, case['events'])
            assert got is None or isinstance(got, (int, float, str)), got
            if form == 'date' and got != family.NONE_VALUE:
                datetime.date.fromisoformat(got)
            checked += 1
    assert family.NONE_VALUE == 'none'
    names = case['names']
    assert names and all(isinstance(k, str) and k in v for k, v in names.items())
    note = case['preamble'].lower()
    for word in ('whole number', 'decimal', 'yyyy-mm-dd', 'title', 'same or different', 'none'):
        assert word in note, word
    for word in ('revoc', 'revoke', 'correction', 'renumber', 'sunset', 'disappl', 'exception', 'certificate',
                 'commencement', 'event', 'trap'):
        assert word not in note, word
    return '%d claims carry query, stated and quantity; value forms valid; names and preamble present' % checked


def check_no_running_totals(seed, index):
    pattern = re.compile(r'\b(running total|cumulative|total to date|payments to date|sums to date|figures to date)\b', re.I)
    for name, text in sources_of(built(seed, index)).items():
        for block in blocks_of(text):
            assert not pattern.search(block), (name, block[:100])
    for block in blocks_of(sources_of(built(seed, index))['instruments.md']) + blocks_of(
            sources_of(built(seed, index))['agreement.md']):
        for first, second in re.findall(r'\b([A-Z][a-z]+ [A-Z][a-z]+) and ([A-Z][a-z]+ [A-Z][a-z]+)\b', block):
            assert first != second, block[:100]
    return 'no running total is rendered; no person is paired with himself'


def check_fixtures(seed, index, fixtures):
    case = built(seed, index)
    folder = Path(fixtures) / case['dossier']
    for name, text in sorted(case['files'].items()):
        path = folder / name
        assert path.is_file(), 'missing in the fixtures folder: %s' % path
        assert path.read_bytes() == text.encode('utf-8'), 'differs from the rebuilt file: %s' % path
    return 'the fixtures folder holds the rebuilt files byte for byte (%d files)' % len(case['files'])


LEGACY_QUOTA = {'value': 13, 'count': 2, 'total': 3, 'first': 2, 'differs': 4}
BIG_QUOTA = {'whole file': 9, 'value': 9, 'count': 2, 'first': 2, 'differs': 2}


def quota_of(seed, index, variant='correct'):
    """The slots of a report by kind, as the parser reads them: 'whole file' for every count over the file."""
    parsed = parsed_of(seed, index)
    kinds = {}
    for row in rows_of(seed, variant, index):
        q = parse_statement(row['claim_text'], parsed)
        name = 'whole file' if q['kind'] in WHOLE_KINDS and family.current_size() > 1 else q['kind']
        kinds[name] = kinds.get(name, 0) + 1
    return kinds


def check_quotas(seed, index):
    size = family.current_size()
    for variant in ('correct', 'mixed'):
        assert quota_of(seed, index, variant) == (LEGACY_QUOTA if size == 1 else BIG_QUOTA), (variant, quota_of(seed, index, variant))
        rows = rows_of(seed, variant, index)
        controls = [r for r in rows if not r.get('lure_states')]
        assert len(controls) >= 6, (variant, len(controls))
        for name in READER_NAMES:
            assert sum(name in r.get('lure_states', {}) for r in rows) >= 4, (variant, name)
    shapes = set()
    parsed = parsed_of(seed, index)
    for row in rows_of(seed, 'correct', index):
        q = parse_statement(row['claim_text'], parsed)
        if q['kind'] in WHOLE_KINDS:
            shapes.add(q['kind'])
    assert len(shapes) >= (1 if size == 1 else 5), shapes
    return 'size %d: slots %s, at least 6 controls on which every reader is right, %d whole-file shapes' % (
        size, quota_of(seed, index), len(shapes))


def check_counted_provisions_needed(seed, index):
    """For a count of provisions, every provision it counts is needed (removing it changes the count); for a count of
    parameters or of instruments, every provision that is the only counted one of its parameter or of its instrument
    is, and there is at least one such provision."""
    case, parsed, world = raw_case(seed, index), parsed_of(seed, index), world_of(seed, index)
    by_id = {e['id']: e for e in case['events']}
    index_of = {i['title']: i['index'] for i in parsed.insts}
    at_of_event = {e['id']: (index_of[e['instrument']], e['paragraph']) for e in case['events']
                   if e['kind'] == 'amendment'}
    done, shown = set(), 0
    for row in rows_of(seed, 'correct', index):
        q = parse_statement(row['claim_text'], parsed)
        if q['kind'] not in WHOLE_KINDS or json.dumps(q, default=str, sort_keys=True) in done:
            continue
        done.add(json.dumps(q, default=str, sort_keys=True))
        item = next(i for t, ms in case['slots'] for i in [t] + ms if i['text'] == row['claim_text'])
        need = {at_of_event[i] for i in needed_of(seed, index, item['query']) if i in at_of_event}
        at_of = {pv['at']: pv for pv in world.provs}
        if q['kind'] in ('params', 'insts'):
            taken = world.counted(dict(q, kind='total'))
            group = (lambda at: at_of[at]['key']) if q['kind'] == 'params' else (lambda at: at[0])
            sole = {at for at in taken if sum(1 for other in taken if group(other) == group(at)) == 1}
            # removing the only counted provision of a parameter (an instrument) takes that parameter (instrument) out of
            # the count; removing one of several changes nothing, unless it is the target of a revocation, a
            # correction or a sunset, whose removal leaves the count not established
            assert sole <= need, (row['claim_text'], sorted(sole - need))
            assert sole, row['claim_text']
        else:
            assert world.counted(q) <= need, (row['claim_text'], sorted(world.counted(q) - need))
        shown += 1
    return 'every counted provision is needed (parameters and instruments: the sole counted one of each, and there is one) for %d whole-file statements' % shown


CHECKS = (('rebuild', check_rebuild), ('round trip', check_round_trip), ('one referent', check_one_referent),
          ('subset rules', check_subset_rules), ('relevant holds needed', check_relevant_and_needed),
          ('needed events are an argument', check_needed_events_are_an_argument),
          ('deciding events', check_deciding_events), ('interface', check_interface),
          ('running totals and names', check_no_running_totals), ('slot quotas', check_quotas),
          ('counted provisions are needed', check_counted_provisions_needed))


def verify(seed, index, fixtures=None):
    """Run the checks on exactly the draw `generate.build_case('amendments', seed, index)` at the size that
    BIG_AMENDMENTS_SIZE names (1 when it is not set); print the size and one line per check; return the number of
    failures. A value of the variable that is not 1, 2, 3 or 4 is an error."""
    print('SIZE %d (%s=%s)' % (family.current_size(), family.SIZE_VARIABLE, os.environ.get(family.SIZE_VARIABLE, 'not set')))
    failures = 0
    checks = list(CHECKS) + ([('fixtures', lambda s, i: check_fixtures(s, i, fixtures))] if fixtures else [])
    for name, check in checks:
        try:
            print('PASS %s: %s' % (name, check(seed, index)))
        except Exception as error:       # noqa: BLE001
            failures += 1
            print('FAIL %s: %s: %s' % (name, type(error).__name__, str(error)[:300]))
    return failures


class BigAmendmentsTests(unittest.TestCase):
    """The tests of the dossier at one size (`SIZE`, pinned for the class whatever the environment holds) over `SEEDS`.
    `BigAmendmentsSize2Tests` runs the same tests at size 2."""
    maxDiff = None
    SIZE = 1
    SEEDS = SEEDS

    @classmethod
    def setUpClass(cls):
        cls._pin = size_env(cls.SIZE)
        cls._pin.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._pin.__exit__(None, None, None)

    def test_determinism_across_runs_and_hash_seeds(self):
        first = generate.build_case('amendments', 1, 1)
        again = generate.build_case('amendments', 1, 1)
        self.assertEqual(first['files'], again['files'])
        self.assertEqual(json.dumps(first['key'], sort_keys=True), json.dumps(again['key'], sort_keys=True))
        digest = hashlib.sha256(json.dumps([first['files'], first['key']], sort_keys=True).encode()).hexdigest()
        env = {'PATH': os.environ.get('PATH', ''), family.SIZE_VARIABLE: str(self.SIZE)}
        code = ('import sys, json, hashlib\nsys.dont_write_bytecode = True\nsys.path.insert(0, %r)\n'
                'import generate, generate_big\ngenerate_big.register()\n'
                "c = generate.build_case('amendments', 1, 1)\n"
                'print(hashlib.sha256(json.dumps([c["files"], c["key"]], sort_keys=True).encode()).hexdigest())\n'
                % str(ROOT / 'evalsuite'))
        for hash_seed in ('1', '2'):
            out = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True,
                                 env=dict(env, PYTHONHASHSEED=hash_seed), check=True)
            self.assertEqual(out.stdout.strip(), digest)

    def test_size_shape_and_citations(self):
        for seed in self.SEEDS:
            case = built(seed)
            sources = sources_of(case)
            self.assertEqual(sorted(sources), ['agreement.md', 'instruments.md', 'procedure.md'])
            parsed = parsed_of(seed)
            n = self.SIZE
            self.assertTrue(25 * n <= len(parsed.insts) <= 36 * n if n > 1 else 25 <= len(parsed.insts) <= 35,
                            (seed, len(parsed.insts)))
            self.assertTrue(120 <= len(parsed.clauses) <= 160, (seed, len(parsed.clauses)))
            total = sum(len(t) for t in sources.values())
            self.assertTrue(300000 + 150000 * (n - 1) <= total <= 450000 + 300000 * (n - 1), (seed, total))
            self.assertLess(len(sources['procedure.md']), 8000 if n == 1 else 12000)
            for variant in ('correct', 'mixed'):
                rows = rows_of(seed, variant)
                self.assertEqual(len(rows), 24)
            false = [r for r in rows_of(seed, 'mixed') if r['semantic_state'] != 'supported']
            self.assertTrue(0.29 <= len(false) / 24 <= 0.5, (seed, len(false)))
            self.assertTrue(all(r['semantic_state'] == 'supported' for r in rows_of(seed, 'correct')))
            for variant in ('correct', 'mixed'):
                cites = set(re.findall(r'(Citations?: [^\n]*)', case['files']['report-%s.md' % variant]))
                self.assertEqual(len(cites), 1, cites)
            self.assertEqual({r['semantic_state'] for r in rows_of(seed)} - {'supported', 'refuted'}, set())

    def test_round_trip_verdicts_and_readers(self):
        for seed in self.SEEDS:
            parsed, world = parsed_of(seed), world_of(seed)
            for row in rows_of(seed):
                q = parse_statement(row['claim_text'], parsed)
                self.assertEqual(verdict(world, q), row['semantic_state'], (seed, row['claim_id'], row['variant'], row['claim_text']))
                for name in READER_NAMES:
                    expected = row.get('lure_states', {}).get(name, row['semantic_state'])
                    self.assertEqual(verdict(world, q, name), expected, (seed, name, row['claim_id'], row['variant'], row['claim_text']))

    def test_one_referent_and_forms_of_reference(self):
        for seed in self.SEEDS:
            world = world_of(seed)           # strict: every reference has exactly one referent at its point
            parsed = parsed_of(seed)
            self.assertEqual(len(world.provs), sum(1 for i in parsed.items if i['kind'] in ('sub', 'exc', 'dis', 'lnk')))
            self.assertEqual(len(world.shorts), sum(1 for i in parsed.items if i['kind'] == 'def'))
            styles = {pv['style'] for pv in world.provs}
            self.assertEqual(styles, {'term', 'short', 'description', 'heading', 'number'}, (seed, styles))
            numbered = [pv for pv in world.provs if pv['style'] == 'number']
            clause = lambda pv: parsed.params[pv['key']]['clause']  # noqa: E731
            self.assertTrue(any(pv['cited'] == clause(pv) for pv in numbered))
            self.assertTrue(any(pv['cited'] != clause(pv) for pv in numbered), 'no citation made after a renumbering')
            terms = list(parsed.terms)
            shorts = [s for s, _, _, _ in world.shorts]
            self.assertEqual(len(set(terms + shorts)), len(terms) + len(shorts))
            for term in terms:
                self.assertFalse(any(term in short for short in shorts))

    def test_handles_are_gone(self):
        share_name, share_second = [], []
        naive_right, naive_total = 0, 0
        for seed in self.SEEDS:
            seed_start = len(share_second)
            case = built(seed)
            sources = sources_of(case)
            for name, text in sources.items():
                self.assertEqual(len(re.findall(r'<a id=', text)), 1, name)
                self.assertTrue(text.startswith('<a id='), name)
                self.assertFalse(re.search(r'^\s*\|', text, re.M), name)
                self.assertFalse(re.search(r'^\s*[-*+] ', text, re.M), name)
                for block in blocks_of(text)[1:]:
                    self.assertNotIn('\n', block, (name, block[:80]))
            parsed, world = parsed_of(seed), world_of(seed)
            blocks = {f: blocks_of(t) for f, t in sources.items()}
            for row in rows_of(seed, 'correct'):
                q = parse_statement(row['claim_text'], parsed)
                if self.SIZE > 1 and q['kind'] in WHOLE_KINDS:      # these rest on every provision; see the counted-provisions check
                    continue
                need = deciding(parsed, world, q)
                by_id = {i['id']: i for i in parsed.items}
                places = {(by_id[i]['file'], by_id[i]['block']) for i in need}
                if q['key'] is not None:
                    places.add(('agreement.md', parsed.params[q['key']]['block']))
                everywhere = q['key'] is not None and all(q['key'] in blocks[f][b] for f, b in places)
                share_name.append(not everywhere)
                second = len(need) >= 2 and len({by_id[i]['block'] for i in need}) >= 2
                share_second.append(second)
                if second and q['kind'] == 'value':
                    truth = compute(world, q)
                    guess = naive_value(sources, q['key'], q['date'])
                    naive_total += 1
                    naive_right += isinstance(truth, (int, float)) and guess == float(truth)
            mine = share_second[seed_start:]
            self.assertGreaterEqual(sum(mine) / len(mine), 0.4, ('second passage', seed))
        print('\nhandles: statements whose deciding passages do not all hold the full name: %.2f; '
              'statements resting on a second passage: %.2f; naive script reader right on %d of %d second-passage '
              'value statements' % (sum(share_name) / len(share_name), sum(share_second) / len(share_second),
                                     naive_right, naive_total))
        self.assertGreaterEqual(sum(share_name) / len(share_name), 0.4)
        self.assertGreaterEqual(sum(share_second) / len(share_second), 0.4)
        self.assertLessEqual(naive_right, naive_total / 2)

    def test_no_per_fact_anchor_or_table_row_for_deciding_values(self):
        for seed in self.SEEDS:
            for name, text in sources_of(built(seed)).items():
                for line in text.split('\n'):
                    self.assertFalse(re.search(r'<a id="(?!P-00|A-00|I-00)', line), line[:60])
                    self.assertFalse(re.match(r'^[^.]{0,40}:\s*\d[\d,.]*\s*(?:days|per cent|%|euros)\s*$', line), line[:60])

    def test_answer_sheets_do_no_work(self):
        for seed in self.SEEDS:
            rows = rows_of(seed, 'mixed')
            # generate.choose can alter as few as 7 of 24 statements; the bound is 7 of 24 and the draw is not steered
            wrong_if_all_supported = sum(r['semantic_state'] != 'supported' for r in rows)
            wrong_if_all_refuted = sum(r['semantic_state'] != 'refuted' for r in rows)
            self.assertGreaterEqual(wrong_if_all_supported, 7, seed)
            self.assertGreaterEqual(wrong_if_all_refuted, 7, seed)

    def test_readers_and_controls(self):
        for seed in self.SEEDS:
            for variant in ('correct', 'mixed'):
                rows = rows_of(seed, variant)
                for name in READER_NAMES:
                    wrong = [r for r in rows if name in r.get('lure_states', {})]
                    self.assertGreaterEqual(len(wrong), 4, (seed, variant, name))
                controls = [r for r in rows if not r.get('lure_states')]
                self.assertGreaterEqual(len(controls), 6, (seed, variant))
            correct = rows_of(seed, 'correct')
            for name in READER_NAMES:
                self.assertGreaterEqual(sum(name in r.get('lure_states', {}) for r in correct), 8, (seed, name))
            # a false statement that states a reader's value is endorsed by that reader
            for row in rows_of(seed, 'mixed'):
                if row['error_type'] and row['error_type'] != 'wrong_value':
                    for name in row['error_type'].split('+'):
                        self.assertEqual(row['lure_states'].get(name), 'supported', (seed, row['claim_id'], name))

    def test_form_does_not_give_the_verdict_away(self):
        counts = {name: [0, 0] for name in ('no value', 'same', 'differed', 'value', 'count', 'first')}
        for seed in range(1, 41):
            for row in rows_of(seed, 'mixed'):
                text, true = row['claim_text'], row['semantic_state'] == 'supported'
                if 'no value' in text.lower() and 'value in force' in text:
                    name = 'no value'
                elif re.search(r'\bsame\b', text):
                    name = 'same'
                elif 'differed' in text or 'different value' in text:
                    name = 'differed'
                elif 'instruments in the file' in text or 'count of instruments' in text:
                    name = 'count'
                elif 'first' in text:
                    name = 'first'
                else:
                    name = 'value'
                counts[name][0 if true else 1] += 1
        print('\nforms in the mixed reports of seeds 1 to 40 (true, false): %s' % counts)
        for name, (true, false) in counts.items():
            self.assertTrue(0.4 <= true / (true + false) <= 0.8, (name, counts))

    def test_nothing_outside_the_passage_map_reads_like_a_deciding_fact(self):
        # patterns of this test's own, not taken from the inventory
        verbs = re.compile(r'\b(award\w*|paid|pays?|grant\w*|substitut\w*|assign\w*|registered|fixed|fixes|set at|'
                           r'changed|amended|replac\w*|revok\w*|disappl\w*|correct\w*|certif\w*|becomes|stands? at)\b',
                           re.I)
        for seed in self.SEEDS:
            case = raw_case(seed)
            sources = sources_of(built(seed))
            mapped = {(p['file'], p['paragraph']) for p in case['passages']}
            parsed, world = parsed_of(seed), world_of(seed)
            forms = set()
            for row in ROWS:
                forms |= {row['term'].lower()} | {x.lower() for x in (row['heading'], row['topic']) if ' ' in x}
            forms |= {x[0].lower() for x in world.shorts}
            for name in ('agreement.md', 'instruments.md'):
                for number, block in enumerate(blocks_of(sources[name])):
                    if (name, number) in mapped:
                        continue
                    low_block = block.lower()
                    for form in forms:
                        if form in low_block:
                            self.assertFalse(verbs.search(low_block.replace(form, '')), (seed, name, number, form, block[:200]))

    def test_a_reader_that_keeps_every_paragraph_with_any_word_of_the_name(self):
        generic = {'period', 'rate', 'limit', 'cap', 'threshold', 'amount', 'ceiling', 'surcharge', 'discount', 'officer',
                   'manager', 'lead', 'authority', 'contact', 'approver', 'order', 'cover', 'bond'}
        words = lambda text: {w for w in re.findall(r'[a-z]+', text.lower()) if len(w) > 3 and w not in generic}  # noqa: E731
        statements = missed = missed_plain = passages = unshared = whole = 0
        for seed in self.SEEDS:
            parsed, world = parsed_of(seed), world_of(seed)
            blocks = blocks_of(sources_of(built(seed))['instruments.md'])
            by_id = {i['id']: i for i in parsed.items}
            for row in rows_of(seed, 'correct'):
                q = parse_statement(row['claim_text'], parsed)
                if q['key'] is None:
                    whole += 1               # a count over the whole file has no subject to search for (sizes 2 and up)
                    continue
                need = deciding(parsed, world, q)
                keep = words(q['key']) | {w for x in world.shorts if x[1] == q['key'] for w in words(x[0])}
                lost = lost_plain = False
                for i in need:
                    item = by_id[i]
                    if item['kind'] in ('sub', 'exc', 'dis', 'lnk'):
                        passages += 1
                        unshared += not (set(re.findall(r'[a-z]+', q['key'].lower())) &
                                         set(re.findall(r'[a-z]+', item['ref'].lower())) - {'the'})
                    if not any(re.search(r'\b%s\b' % w, blocks[item['block']].lower()) for w in keep):
                        lost = True
                    if not any(re.search(r'\b%s\b' % w, blocks[item['block']].lower()) for w in words(q['key'])):
                        lost_plain = True
                statements += 1
                missed += lost
                missed_plain += lost_plain
        print('\nany-word reader misses a deciding passage on %.2f of %d subject statements (%.2f when it does not know '
              'the aliases the text introduces); %.2f of %d deciding amending passages name the subject by something '
              'that shares no word with its name'
              % (missed / statements, statements, missed_plain / statements, unshared / passages, passages))
        self.assertGreaterEqual(unshared / passages, 0.25)
        if self.SIZE == 1:
            self.assertGreaterEqual(missed / statements, 0.4)
            self.assertGreaterEqual(missed_plain / statements, 0.5)
        else:
            # From size 2, 9 of the 24 statements count the whole file: the reader has no subject to search for and
            # misses every one of them, and 6 of the other 15 are controls, which no reader gets wrong and which
            # rest on no passage that the name hides. The bounds are for the statements with a subject (0.3 and 0.45,
            # for 0.4 and 0.5 at size 1) and for all of them (0.4 and 0.5, as at size 1).
            print('any-word reader at size %d: %.2f of all statements missed (%.2f without aliases)'
                  % (self.SIZE, (missed + whole) / (statements + whole), (missed_plain + whole) / (statements + whole)))
            self.assertGreaterEqual(missed / statements, 0.3)
            self.assertGreaterEqual(missed_plain / statements, 0.45)
            self.assertGreaterEqual((missed + whole) / (statements + whole), 0.4)
            self.assertGreaterEqual((missed_plain + whole) / (statements + whole), 0.5)

    def test_four_statements_depend_on_another_subject(self):
        for seed in self.SEEDS:
            parsed, world = parsed_of(seed), world_of(seed)
            taken = {pv['id'] for pv in world.provs if pv['link'] is not None}
            found = 0
            for row in rows_of(seed, 'correct'):
                q = parse_statement(row['claim_text'], parsed)
                if q['key'] is None:
                    continue
                need = deciding(parsed, world, q)
                found += bool(need & taken)             # the answer rests on a figure that belongs to another parameter
            self.assertGreaterEqual(found, 4, seed)
            self.assertTrue(any(pv['link'] for pv in world.provs))

    def test_the_procedure_defines_every_operation_by_what_it_does(self):
        procedure = sources_of(built(1))['procedure.md']
        rule_one = re.search(r'\*\*1\. What has effect\.\*\*(.*?)\n\n', procedure, re.S).group(1)
        self.assertIn('does not matter', rule_one)
        self.assertIn('no list of verbs is complete', rule_one)
        self.assertFalse(re.search(r'substituted, inserted|made, agreed', procedure))
        # operation -> (what the procedure says it is, placeholders its frames must and must not have)
        classes = {
            'substitution': (r'gives a parameter a value for all work, in place of any value it had, is a substitution',
                             {'ref', 'value'}, {'cat', 'start', 'end', 'other', 'pref', 'old'}),
            'exception': (r'gives it a value for one category of work only is an exception',
                          {'ref', 'value', 'cat'}, {'start', 'end', 'other', 'pref', 'old'}),
            'disapplication': (r'leaves a parameter without any value, for all work or for the category it names, is a '
                               r'disapplication, whatever words it uses', {'ref', 'start', 'end'}, {'value', 'other', 'pref'}),
            'link': (r'gives a parameter the value that another parameter has on the day the provision takes effect',
                     {'ref', 'other'}, {'value', 'cat', 'pref', 'start'}),
            'revocation': (r'withdraws an earlier amending provision in full is a revocation', {'pref'},
                           {'value', 'sdate', 'old', 'ref'}),
            'sunset': (r'has no effect from a date, or ceases to have effect on a date', {'sdate'}, {'value', 'old'}),
            'correction': (r'printed wrongly, and what it should have said, is a correction',
                           {'pref', 'what', 'old', 'new'}, {'value', 'ref'}),
            'renumbering': (r'gives clauses new numbers is a renumbering', {'a', 'b', 'c', 'd'}, {'ref', 'value'}),
            'definition': (r'by an expression that an instrument defines', {'short', 'ref'}, {'value'}),
            'record': (r'records an event only if it states that the event has happened', {'event'}, {'ref', 'value'}),
            'commencement': (r'commencement date is the date it names or, if it names an event', set(),
                             {'ref', 'value'}),
        }
        for _, (phrase, _, _) in classes.items():
            self.assertTrue(re.search(phrase, procedure, re.S), phrase)
        frames = {'substitution': [f for f in INV['substitute']], 'exception': INV['exception'],
                  'disapplication': INV['disapply'], 'link': INV['link'], 'revocation': INV['revoke'],
                  'sunset': list(INV['sunset_own']) + list(INV['sunset_later']), 'correction': INV['correct'],
                  'renumbering': INV['renumber'], 'definition': INV['define'], 'record': INV['record'],
                  'commencement': list(INV['commence_date']) + list(INV['commence_event'])}
        for name, group in frames.items():
            _, must, mustnot = classes[name]
            for frame in group:
                holders = set(re.findall(r'\{(\w+)\}', frame))
                self.assertTrue(must <= holders, (name, frame))
                self.assertFalse(mustnot & holders, (name, frame))
                if name == 'disapplication':
                    self.assertFalse(re.search(r'\{value\}', frame))      # a period of dates and no value

    def test_a_verb_filter_keeps_much_of_the_file_and_aliases_carry_the_decisions(self):
        verbs = re.compile(r'replace|substitut|effect|force|revoke|ceases|lapses|correct|disappl|is changed|stand at|'
                           r'is fixed|set aside|suspend|becomes|shall be')
        kept = total = decisive = alias = chained = 0
        for seed in self.SEEDS:
            parsed, world = parsed_of(seed), world_of(seed)
            for block in blocks_of(sources_of(built(seed))['instruments.md']):
                if re.match(r'^\d+\. ', block):
                    total += 1
                    kept += bool(verbs.search(block))
            seen = set()
            for row in rows_of(seed, 'correct'):
                q = parse_statement(row['claim_text'], parsed)
                if q['kind'] in WHOLE_KINDS:
                    continue
                for i in deciding(parsed, world, q) - seen:
                    seen.add(i)
                    for pv in world.provs:
                        if pv['id'] == i:
                            decisive += 1
                            alias += pv['style'] == 'short'
            chained += sum(1 for it in parsed.items if it['kind'] == 'def' and re.match(
                r'^the (%s)$' % '|'.join(re.escape(x[0]) for x in world.shorts), it['g']['ref']))
        print('\nverb filter keeps %.2f of the numbered paragraphs of instruments.md; %.2f of the deciding amending '
              'provisions name their parameter only through an expression an instrument defines; %d definitions are '
              'made through another defined expression' % (kept / total, alias / decisive, chained))
        self.assertGreaterEqual(kept / total, 0.35)
        self.assertGreaterEqual(alias / decisive, 0.4)
        self.assertGreaterEqual(chained, 3)

    def test_three_statements_rest_on_the_whole_file(self):
        for seed in self.SEEDS:
            parsed, world = parsed_of(seed), world_of(seed)
            totals = [r for r in rows_of(seed, 'correct') if parse_statement(r['claim_text'], parsed)['kind'] == 'total']
            self.assertGreaterEqual(len(totals), 3, seed)
            for row in totals:
                q = parse_statement(row['claim_text'], parsed)
                need = deciding(parsed, world, q)
                # every amending provision counted is needed: missing any one of them changes the count
                self.assertGreaterEqual(len(need), q['stated'], (seed, row['claim_text']))

    def test_the_checks_of_verify_on_seeds_1_to_5(self):
        for seed in self.SEEDS:
            for name, check in CHECKS:
                check(seed, 1)

    def test_a_draw_with_another_index_is_checked_too(self):
        self.assertEqual(verify(3, 2), 0)

    def test_inventory_has_eight_wordings_of_every_deciding_fact(self):
        for kind, frames in INV['original'].items():
            self.assertGreaterEqual(len(frames), 8, kind)
        for name in ('unset', 'definition', 'exception', 'revoke', 'commence_date', 'commence_event', 'record',
                     'sunset_own', 'sunset_later', 'correct', 'renumber', 'define', 'decoy'):
            self.assertGreaterEqual(len(INV[name]), 8, name)
        plain = [f for f in INV['substitute'] if '{cdate}' not in f]
        self.assertGreaterEqual(len(plain), 8)
        self.assertGreaterEqual(len([f for f in INV['disapply'] if '{cat}' in f]), 8)
        self.assertGreaterEqual(len([f for f in INV['disapply'] if '{cat}' not in f]), 8)
        for frames in INV['value_formats'].values():
            self.assertGreaterEqual(len(frames), 2)
        self.assertGreaterEqual(len(INV['date_formats']), 6)

    def test_facts_are_published_and_the_function_reproduces_the_key(self):
        for seed in self.SEEDS:
            case = raw_case(seed)
            events = case['events']
            self.assertEqual(json.loads(json.dumps(events)), events)
            ids = [e['id'] for e in events]
            self.assertEqual(len(set(ids)), len(ids))
            self.assertTrue(all(re.fullmatch(r'E\d{3}', i) for i in ids))
            self.assertEqual(sources_of(built(seed)), {k: v for k, v in case['sources'].items()})
            kinds = {e['kind'] for e in events}
            self.assertEqual(kinds, {'parameter', 'definition', 'amendment', 'event', 'revocation', 'correction',
                                     'renumbering', 'sunset', 'decoy'})
            for e in events:
                self.assertEqual(e['decides'], e['kind'] != 'decoy')
            claims = {}
            for true, mutations in case['slots']:
                for item in [true] + mutations:
                    claims[item['text']] = item
            for row in rows_of(seed):
                item = claims[row['claim_text']]
                q = item['query']
                got = family.answer(q, events)
                self.assertEqual('supported' if got == q['stated'] else 'refuted', row['semantic_state'])
                for name in READER_NAMES:
                    got = family.answer(q, events, name)
                    expected = row.get('lure_states', {}).get(name, row['semantic_state'])
                    self.assertEqual('supported' if got == q['stated'] else 'refuted', expected)
                if q['kind'] == 'value':
                    self.assertEqual(family.value_in_force(events, q['parameter'], q['date'], q.get('category')),
                                     family.answer(q, events))

    def test_function_on_subsets(self):
        # the reading rule of a subset (section 3.3 of the spec), as it plays out on the generated facts
        for seed in self.SEEDS:
            events = raw_case(seed)['events']
            clause_of = {e['parameter']: e['id'] for e in events if e['kind'] == 'parameter'}
            amendments = [e for e in events if e['kind'] == 'amendment']
            for a in amendments:
                self.assertIsNotNone(family.value_in_force(events, a['parameter'], LATE, a['category']))
                for need in a['requires']:
                    rest = [e for e in events if e['id'] != need]
                    got = family.value_in_force(rest, a['parameter'], LATE, a['category'])
                    # nothing a provision requires makes the value not established, except the clause of its parameter
                    self.assertEqual(got is None, need == clause_of[a['parameter']], (seed, a['id'], need))
            for param in [e for e in events if e['kind'] == 'parameter'][:10]:
                rest = [e for e in events if e['id'] != param['id']]
                self.assertIsNone(family.value_in_force(rest, param['parameter'], LATE))
            for e in [x for x in events if x['kind'] in ('revocation', 'correction', 'sunset')]:
                # whatever acts on a provision that the subset lacks does nothing; the parameter keeps a value
                rest = [x for x in events if x['id'] != e['target']]
                self.assertIsNotNone(family.value_in_force(rest, e['parameter'], LATE), (seed, e['id']))
                self.assertIsNotNone(family.answer({'kind': 'count', 'parameter': e['parameter'], 'date': LATE}, rest))
            for ev in [e for e in events if e['kind'] == 'event']:
                for a in [a for a in amendments if a['commencement'].get('event') == ev['id']]:
                    rest = [e for e in events if e['id'] != ev['id']]
                    self.assertIsNotNone(family.value_in_force(rest, a['parameter'], LATE, a['category']), (seed, ev['id']))
            self.assertTrue([e for e in events if e['kind'] == 'revocation'])
            self.assertTrue([e for e in events if e['kind'] == 'sunset'])

    def test_the_reading_rule_on_small_files(self):
        def par(i, key, kind='days', value=30, clause=1):
            return {'id': i, 'kind': 'parameter', 'parameter': key, 'term': key, 'heading': key, 'clause': clause,
                    'value_kind': kind, 'value': value, 'from': '2031-01-01', 'party': None, 'decides': True}

        def defn(i, key, requires, source='agreement'):
            return {'id': i, 'kind': 'definition', 'source': source, 'term': key, 'parameter': key,
                    'date': '2031-01-01', 'paragraph': 0, 'requires': requires, 'decides': True}

        def amend(i, key, day, value, requires, style='heading', event=None, link=None, definition=None, number=None):
            return {'id': i, 'kind': 'amendment', 'instrument': 'Deed of Variation (Number 1)', 'date': day, 'paragraph': int(i[1:]),
                    'parameter': key, 'operation': 'substitute', 'category': None, 'value': value,
                    'commencement': {'type': 'event', 'event': event} if event else {'type': 'date', 'date': day},
                    'link': link, 'sunset': None, 'ends': None, 'requires': requires, 'decides': True,
                    'reference': {'style': style, 'definition': definition, 'number': number}}

        def side(i, kind, key, target, **extra):
            e = {'id': i, 'kind': kind, 'instrument': 'Deed of Variation (Number 2)', 'date': '2031-08-01',
                 'paragraph': int(i[1:]), 'target': target, 'parameter': key, 'requires': [target], 'decides': True}
            e.update(extra)
            return e

        value = lambda events, key='cure', day='2032-01-01': family.answer(  # noqa: E731
            {'kind': 'value', 'parameter': key, 'date': day, 'category': None, 'value_kind': 'days'}, events)
        total = lambda events: family.answer({'kind': 'total', 'date': '2040-01-01'}, events)  # noqa: E731
        without = lambda events, *ids: [e for e in events if e['id'] not in ids]  # noqa: E731

        # 1. a provision made through an expression whose definition is absent names nothing: it has no effect
        short = {'id': 'E002', 'kind': 'definition', 'source': 'instrument', 'instrument': 'Deed of Variation (Number 1)',
                 'date': '2031-02-01', 'paragraph': 1, 'term': 'the Interim Window', 'parameter': 'cure',
                 'reference': {'style': 'heading', 'definition': None, 'number': None}, 'requires': ['E001'], 'decides': True}
        events = [par('E001', 'cure'), short, amend('E003', 'cure', '2031-03-01', 45, ['E002'], 'short', definition='E002')]
        self.assertEqual(value(events), 45)
        self.assertEqual(value(without(events, 'E002')), 30)
        self.assertEqual(total(events), 1)
        self.assertEqual(total(without(events, 'E002')), 0)
        self.assertEqual(value(without(events, 'E001')), None)          # the parameter's own clause: not established
        # 2. a revocation, a correction and a later sunset of an absent provision do nothing
        events = [par('E001', 'cure'), amend('E002', 'cure', '2031-03-01', 45, ['E001']),
                  amend('E003', 'cure', '2031-05-01', 60, ['E001']),
                  side('E004', 'revocation', 'cure', 'E003', commencement={'type': 'date', 'date': '2031-08-01'}),
                  side('E005', 'correction', 'cure', 'E002', field='value', old=45, new=50),
                  side('E006', 'sunset', 'cure', 'E003', sunset='2031-12-01')]
        self.assertEqual(value(events, day='2031-07-01'), 60)           # E003 stands until its revocation begins
        self.assertEqual(value(events, day='2031-09-01'), 50)           # revoked: E002 again, as corrected
        self.assertEqual(value(without(events, 'E003')), 50)            # E004 and E006 act on nothing
        self.assertEqual(value(without(events, 'E002'), day='2031-06-01'), 60)    # E005 acts on nothing
        self.assertEqual(value(without(events, 'E002')), 30)            # E003 is revoked by E004 and nothing replaces it
        # 3. what waits on an event that the subset lacks reads as if no instrument recorded it: it never has effect
        record = {'id': 'E002', 'kind': 'event', 'phrase': 'the new service desk platform has gone live',
                  'recorded_by': 'Certificate (Number 1)', 'date': '2031-05-10', 'paragraph': 1, 'requires': [], 'decides': True}
        events = [par('E001', 'cure'), record, amend('E003', 'cure', '2031-02-01', 60, ['E001', 'E002'], event='E002')]
        self.assertEqual(value(events), 60)
        self.assertEqual(value(without(events, 'E002')), 30)
        self.assertEqual(value([par('E001', 'cure'), dict(record, date=None, recorded_by=None),
                                amend('E003', 'cure', '2031-02-01', 60, ['E001', 'E002'], event='E002')]), 30)
        self.assertEqual(total(without(events, 'E002')), 0)
        self.assertEqual(family.answer({'kind': 'never'}, without(events, 'E002')), 1)
        # 4. a provision that takes over the value of another parameter: without that parameter's clause it takes no value
        link = {'parameter': 'delivery', 'style': 'heading'}
        events = [par('E001', 'delivery', value=10), par('E002', 'acceptance', value=20),
                  amend('E003', 'acceptance', '2031-03-01', None, ['E002', 'E001'], link=link)]
        self.assertEqual(value(events, 'acceptance'), 10)
        self.assertEqual(value(without(events, 'E001'), 'acceptance'), family.NONE_VALUE)
        self.assertEqual(total(without(events, 'E001')), 1)             # the provision still has effect
        # ... but when the clause is there and the expression that names it is not, the provision names nothing
        link = {'parameter': 'delivery', 'style': 'term'}
        events = [par('E001', 'delivery', value=10), par('E002', 'acceptance', value=20), defn('E003', 'delivery', ['E001']),
                  amend('E004', 'acceptance', '2031-03-01', None, ['E002', 'E003'], link=link)]
        self.assertEqual(value(events, 'acceptance'), 10)
        self.assertEqual(value(without(events, 'E003'), 'acceptance'), 20)
        self.assertEqual(total(without(events, 'E003')), 0)
        # 5. a clause number whose renumbering is absent: the provision cannot say which clause it means
        renumbering = {'id': 'E002', 'kind': 'renumbering', 'instrument': 'Deed of Variation (Number 1)', 'date': '2031-02-01',
                       'paragraph': 1, 'first': 1, 'last': 5, 'shift': 2, 'effective': '2031-03-01', 'requires': [],
                       'decides': True}
        events = [par('E001', 'cure', clause=3), renumbering,
                  amend('E003', 'cure', '2031-04-01', 45, ['E001', 'E002'], 'number', number=5)]
        self.assertEqual(value(events), 45)
        self.assertEqual(value(without(events, 'E002')), 30)
        # 6. a count over the whole file of an empty subset is zero, in every shape, for every reader
        for q in whole_queries('2033-01-01', '2034-01-01'):
            for reader in (None,) + tuple(family.READERS):
                self.assertEqual(family.answer(q, [], reader), 0, (q, reader))

    def test_the_hand_written_queries_give_the_hand_written_needed_sets(self):
        sys.path.insert(0, str(ROOT / 'tests'))
        try:
            hand = __import__('test_evalsuite_check_shown')
        finally:
            sys.path.pop(0)
        # Where this reading's needed set is not the hand-written one: (name, date or kind, category) -> this reading's
        # set. In both the hand reading keeps an event that the value does not turn on, because without it a reader
        # could not tell that the provision waits for an event that has not happened: the record dated after the day
        # asked about (an absent record reads as no record, and the provision has no effect either way), and the
        # statement that an event was never recorded (an absent one reads as never recorded).
        differs = {('commencement on an event', '2031-05-09', None): {'E001'},
                   ('commencement on an event', '2031-12-01', 'Training Services'): {'E001', 'E002', 'E003'}}
        agreed = 0
        for item in hand.HAND_CASES:
            events = item['events']
            for query in item['queries']:
                label = hand.hand_label(item, query)
                base = family.answer(query['query'], events)
                self.assertEqual(base, query['value'], label)
                need = {e['id'] for e in events if family.answer(query['query'], [x for x in events if x is not e]) != base}
                self.assertTrue(need <= set(family.relevant(query['query'], events)), label)
                if label in differs:
                    self.assertEqual(need, differs[label], label)
                else:
                    self.assertEqual(need, query['needed'], label)
                    agreed += 1
        self.assertEqual(agreed + len(differs), sum(len(i['queries']) for i in hand.HAND_CASES))
        self.assertGreaterEqual(agreed, 27)

    def test_passages_say_where_each_fact_is_told(self):
        for seed in self.SEEDS:
            case = raw_case(seed)
            events = {e['id']: e for e in case['events']}
            parsed, world = parsed_of(seed), world_of(seed)
            sources = sources_of(built(seed))
            blocks = {f: blocks_of(t) for f, t in sources.items()}
            by_block = {i['block']: i for i in parsed.items}
            told = set()
            for p in case['passages']:
                self.assertIn(p['file'], blocks)
                self.assertTrue(0 <= p['paragraph'] < len(blocks[p['file']]))
                self.assertTrue(p['event_ids'] and set(p['event_ids']) <= set(events))
                told.update(p['event_ids'])
                role, first = p['role'], events[p['event_ids'][0]]
                text = blocks[p['file']][p['paragraph']]
                if p['file'] == 'agreement.md':
                    if role == 'titles':
                        self.assertEqual(text, '### %d. %s' % (first['clause'], first['heading']))
                    elif role == 'states':
                        found = [t for r, t in parsed.agreement_blocks[p['paragraph']] if r == 'states']
                        self.assertEqual([slug(t) for t in found], [first['parameter']])
                        self.assertEqual(parsed.params[found[0]]['orig'], first['value'])
                    else:
                        self.assertEqual(role, 'defines')
                        found = sorted(slug(t) for r, t in parsed.agreement_blocks[p['paragraph']] if r == 'defines')
                        self.assertEqual(found, sorted(events[i]['parameter'] for i in p['event_ids']))
                    continue
                item = by_block[p['paragraph']]
                if role == 'proposes':
                    self.assertEqual((item['kind'] in ('dec', 'decev'), first['kind'], first['decides']),
                                     (True, 'decoy', False))
                    continue
                self.assertEqual(item['file'], 'instruments.md')
                self.assertEqual(item['date'].isoformat(), first['date'])
                self.assertEqual(item['par'], first['paragraph'])
                pv = next((x for x in world.provs if x['id'] == item['id']), None)
                if role == 'amends':
                    self.assertEqual((slug(pv['key']), pv['op'], pv['cat'], pv['value']),
                                     (first['parameter'], first['operation'], first['category'], first['value']))
                    self.assertEqual((pv['style'], pv['cited']), (first['reference']['style'], first['reference']['number']))
                elif role == 'commences':
                    if item['kind'] in ('sub', 'exc', 'dis', 'lnk', 'rev'):
                        c = item['commence']
                        spec = first['commencement']
                        if spec['type'] == 'date':
                            self.assertEqual(c, ('date', datetime.date.fromisoformat(spec['date'])))
                        else:
                            self.assertEqual(c, ('event', events[spec['event']]['phrase']))
                            # a paragraph that names an event lists it only when no instrument records it
                            if events[spec['event']]['date'] is None:
                                self.assertIn(spec['event'], p['event_ids'])
                            else:
                                self.assertEqual(p['event_ids'], [first['id']])
                elif role == 'sunsets':
                    if item['kind'] == 'sun':
                        target = events[first['target']]
                        self.assertEqual(world.target(item['g']['pref']), (parsed.insts.index(next(
                            i for i in parsed.insts if i['title'] == target['instrument'])), target['paragraph']))
                        self.assertEqual(parse_date(item['g']['sdate']).isoformat(), first['sunset'])
                    else:
                        self.assertEqual(item['sunset'].isoformat(), first['sunset'])
                elif role in ('revokes', 'corrects'):
                    target = events[first['target']]
                    self.assertEqual(world.target(item['g']['pref']), (next(
                        i['index'] for i in parsed.insts if i['title'] == target['instrument']), target['paragraph']))
                    if role == 'corrects':
                        self.assertEqual(item['field'], first['field'])
                elif role == 'records':
                    self.assertEqual((item['kind'], item['phrase']), ('rec', first['phrase']))
                elif role == 'renumbers':
                    self.assertEqual((item['first'], item['last'], item['shift'], item['effective'].isoformat()),
                                     (first['first'], first['last'], first['shift'], first['effective']))
                else:
                    self.assertEqual(role, 'defines')
                    self.assertEqual(item['kind'], 'def')
                    self.assertEqual(item['g']['short'], first['term'])
                    short = next(s for s in world.shorts if s[3] == item['id'])
                    self.assertEqual(slug(short[1]), first['parameter'])
            self.assertEqual(told, set(events), 'a fact is told nowhere')
            self.assertEqual(len({p['paragraph'] for p in case['passages'] if p['file'] == 'instruments.md'}),
                             len(parsed.items))


GOLDEN_SIZE_1 = {1: 'd4eea92fa4d59efc0868ea32a7ddcd452aa433a052027d43a3ac61de0c50af31',
                 2: '809cb13bab3490fd9fd17b15461e8b429ed0853b292fe65ad95cba0eecc779fd',
                 3: 'f33a31a8f9208fdf78ece4ea88e533e5b07a027d33edf07ff6dc5ed541867d9e',
                 4: '9406bf25193f402d8bf5b1ee30c552b00b885829ea6b972a315a833f7a0595a5',
                 5: '567f5411579a74a4eec28254ddc345d3afb3033139e0780c39133304fa478642'}


def module_digest(seed, index=1):
    """A digest of everything the module makes of one draw (sources, statements and their wrong versions, facts,
    passages, names), with the random stream of generator version 4 written out, so that it does not move when the
    generator does."""
    case = family.build(random.Random('evalsuite:4:%d:%d:amendments' % (seed, index)))
    brief = lambda item: {k: item[k] for k in ('kind', 'text', 'stated', 'error', 'query')}  # noqa: E731
    blob = {'sources': case['sources'], 'title': case['title'], 'preamble': case['preamble'],
            'slots': [[brief(item) for item in [true] + mutations] for true, mutations in case['slots']],
            'events': case['events'], 'passages': case['passages'], 'names': case['names']}
    return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()


class BigAmendmentsSize2Tests(BigAmendmentsTests):
    """Every test of the default dossier, again on a dossier of size 2 (seeds 1 and 2), except the three below, which
    are about the default size's slots and are replaced by their counterparts for size 2."""
    SIZE = 2
    SEEDS = (1, 2)

    def test_form_does_not_give_the_verdict_away(self):
        # the counterpart for size 2: each shape of whole-file statement appears true and false, in several wordings
        seen = {}
        for seed in range(1, 6):
            parsed = parsed_of(seed)
            for variant in ('correct', 'mixed'):
                for row in rows_of(seed, variant):
                    q = parse_statement(row['claim_text'], parsed)
                    if q['kind'] in WHOLE_KINDS:
                        name = q['kind']
                        seen.setdefault(name, {'supported': 0, 'refuted': 0, 'wordings': set(), 'variants': set()})
                        seen[name][row['semantic_state']] += 1
                        seen[name]['variants'].add(q.get('class'))
                        seen[name]['wordings'].add(re.sub(r'\d+|[A-Z][a-z]+ \d+, \d{4}', '#', row['claim_text'])[:40])
        print('\nwhole-file statements at size 2, seeds 1 to 5 (both reports): %s' % {
            k: (v['supported'], v['refuted'], len(v['wordings'])) for k, v in sorted(seen.items())})
        self.assertEqual(set(seen), {'total', 'between', 'class', 'on', 'params', 'insts', 'never'})
        for name, v in seen.items():
            self.assertGreater(v['supported'], 0, name)
            self.assertGreater(v['refuted'], 0, name)
        self.assertEqual(seen['class']['variants'], {'disapply', 'exception'})     # disapplications and exceptions both

    def test_counts_between_dates_touch_the_boundaries(self):
        # a provision takes effect on the first or on the last date of a period in most of the counts between two dates,
        # so that a reader who leaves a date out, or counts one too many, gets a different number
        moves = {'first date left out': 0, 'day before the first date counted': 0, 'last date left out': 0,
                 'day after the last date counted': 0}
        total = 0
        for seed in range(1, 6):
            parsed, world = parsed_of(seed), world_of(seed)
            for row in rows_of(seed, 'correct'):
                q = parse_statement(row['claim_text'], parsed)
                if q['kind'] == 'between':
                    total += 1
                    for name, other in (('first date left out', dict(q, date=q['date'] + DAY)),
                                        ('day before the first date counted', dict(q, date=q['date'] - DAY)),
                                        ('last date left out', dict(q, date2=q['date2'] - DAY)),
                                        ('day after the last date counted', dict(q, date2=q['date2'] + DAY))):
                        moves[name] += compute(world, other) != compute(world, q)
        print('\ncounts between two dates, seeds 1 to 5 at size 2: %d; the count changes in these cases: %s' % (total, moves))
        self.assertGreaterEqual(total, 5)
        for name, changed in moves.items():
            self.assertGreaterEqual(changed, 2, name)

    def test_three_statements_rest_on_the_whole_file(self):
        # the counterpart for size 2: nine statements about the whole file, in at least five shapes
        for seed in self.SEEDS:
            parsed = parsed_of(seed)
            whole = [parse_statement(r['claim_text'], parsed) for r in rows_of(seed, 'correct')]
            whole = [q for q in whole if q['kind'] in WHOLE_KINDS]
            self.assertEqual(len(whole), 9, seed)
            self.assertGreaterEqual(len({q['kind'] for q in whole}), 5, seed)
            check_counted_provisions_needed(seed, 1)
            case = raw_case(seed)
            for true, _ in case['slots']:
                q = true['query']
                if q['kind'] in ('total', 'between', 'class', 'on', 'never'):
                    self.assertGreaterEqual(len(needed_of(seed, 1, q)), true['stated'], (seed, true['text']))


class SizeTests(unittest.TestCase):
    """What BIG_AMENDMENTS_SIZE does: how it is read, that size 1 is the module as it was, and what a larger size
    holds."""
    maxDiff = None

    def test_size_1_is_the_module_as_it_was_before_sizes_existed(self):
        # GOLDEN_SIZE_1 was made by the module as it stood before sizes existed; the same draws, with the variable
        # not set and set to 1, must give the same digests
        for seed, digest in GOLDEN_SIZE_1.items():
            with size_env(None):
                self.assertEqual(module_digest(seed), digest, seed)
            with size_env(1):
                self.assertEqual(module_digest(seed), digest, seed)

    def test_the_size_is_read_from_the_environment_and_a_bad_value_is_a_plain_error(self):
        with size_env(None):
            self.assertEqual(family.current_size(), 1)
        for size in (1, 2, 3, 4):
            with size_env(size):
                self.assertEqual(family.current_size(), size)
        for bad in ('0', '5', '2.0', '', ' 2', 'two', '-1', '02', '1,2'):
            with size_env(bad):
                with self.assertRaises(generate.GenerationError, msg=bad):
                    family.current_size()
                with self.assertRaises(generate.GenerationError, msg=bad):
                    family.build(random.Random(1))
                self.assertIn(family.SIZE_VARIABLE, self.cli_error(bad))
        with size_env('7'):
            with self.assertRaises(generate.GenerationError):
                generate.build_case('amendments', 1, 1)

    def cli_error(self, bad):
        """What the command line says when the size is bad: no folder is made, and the message names the variable."""
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            out = subprocess.run([sys.executable, '-I', str(ROOT / 'evalsuite' / 'generate_big.py'), '--fixtures',
                                  str(Path(folder) / 'fixtures'), '--keys', str(Path(folder) / 'keys'), '--seed', '1',
                                  '--dossiers', '1', '--families', 'amendments'], capture_output=True, text=True,
                                 env={'PATH': os.environ.get('PATH', ''), family.SIZE_VARIABLE: bad})
            self.assertNotEqual(out.returncode, 0)
            self.assertEqual(list(Path(folder).iterdir()), [], 'a folder was made')
        self.assertIn('Cannot generate', out.stderr)
        return out.stderr

    def test_size_changes_the_draw_and_the_case_says_which_size(self):
        sources = {}
        for size in (1, 2):
            case = _raw_case(1, 1, size)
            self.assertEqual(case['size'], size)
            sources[size] = case['sources']
        self.assertNotEqual(sources[1]['instruments.md'], sources[2]['instruments.md'])
        self.assertNotEqual(sources[1]['procedure.md'], sources[2]['procedure.md'])
        # size 1 keeps the rules it had; size 2 adds the rule that defines the counts over the whole file
        self.assertNotIn('Counts over the whole file', sources[1]['procedure.md'])
        # the note on how a value is written says, from size 2, that a count of provisions or parameters is a whole number
        one, two = _raw_case(1, 1, 1)['preamble'], _raw_case(1, 1, 2)['preamble']
        self.assertNotIn('provisions', one)
        self.assertTrue(two.startswith(one))
        self.assertIn('A number of provisions, of parameters or of instruments is likewise a whole number', two)
        self.assertIn('**10. Counts over the whole file.**', sources[2]['procedure.md'])
        self.assertIn('**11. Nothing else.**', sources[2]['procedure.md'])

    def test_size_2_holds_about_twice_the_instruments_and_provisions(self):
        report = []
        for seed in (1, 2):
            one, two = _raw_case(seed, 1, 1), _raw_case(seed, 1, 2)
            counts = {}
            for size, case in ((1, one), (2, two)):
                parsed = _parsed_of(seed, 1, size)
                kinds = {}
                for e in case['events']:
                    kinds[e['kind']] = kinds.get(e['kind'], 0) + 1
                counts[size] = (len(parsed.insts), kinds['amendment'], sum(v for k, v in kinds.items() if k not in (
                    'parameter', 'definition', 'renumbering')), parsed.insts[-1]['date'] - parsed.e0, {
                    name: len(text) for name, text in case['sources'].items()})
            (i1, a1, f1, span1, sz1), (i2, a2, f2, span2, sz2) = counts[1], counts[2]
            report.append('seed %d: instruments %d -> %d, amending provisions %d -> %d, all later facts %d -> %d, '
                          'period %d -> %d days, sources %s -> %s' % (seed, i1, i2, a1, a2, f1, f2, span1.days,
                                                                    span2.days, sz1, sz2))
            for before, after in ((i1, i2), (a1, a2), (f1, f2)):
                self.assertTrue(1.6 <= after / before <= 2.5, (seed, before, after))
            self.assertTrue(1.6 <= span2 / span1 <= 2.6, (seed, span1, span2))
            self.assertTrue(1.6 <= sz2['instruments.md'] / sz1['instruments.md'] <= 2.6, seed)
            self.assertTrue(0.7 <= sz2['agreement.md'] / sz1['agreement.md'] <= 1.35, seed)    # the agreement keeps its size
        print('\n' + '\n'.join(report))

    def test_the_rule_for_counts_over_the_whole_file_and_the_wordings_of_each_shape(self):
        procedure = _raw_case(1, 1, 2)['sources']['procedure.md']
        rule = re.search(r'\*\*10\. Counts over the whole file\.\*\*(.*?)\n\n', procedure, re.S).group(1)
        for phrase in ('(a) Provisions that had taken effect on or before D', '(b) Provisions that took effect between D1 and D2, both dates included',
                       '(c) Disapplications that had taken effect on or before D, and likewise exceptions',
                       '(d) Provisions that had effect on D', '(e) Parameters for which an amending provision had taken effect',
                       '(f) Instruments that hold such a provision', '(g) Provisions that never took effect',
                       'whether or not they were later overridden, revoked or ended', 'It does not matter whether another provision prevails',
                       'need not have begun on that date'):
            self.assertIn(phrase, rule)
        self.assertLess(len(procedure), 12000)
        statements = INV['statements']
        for name in ('total', 'between', 'class_dis', 'class_exc', 'on', 'params', 'insts', 'never'):
            self.assertGreaterEqual(len(statements[name]), 3, name)
            self.assertEqual(len(set(statements[name])), len(statements[name]))
        # no two wordings of different shapes can be read the same way: each statement text matches one template
        for name, templates in statements.items():
            for template in templates:
                text = re.sub(r'\{(date2?|n|term|cat|value|catpart)\}', lambda m: {
                    'date': '2034-05-06', 'date2': '2035-07-08', 'n': '12', 'term': 'Cure Period', 'cat': 'Hardware Services',
                    'value': '30 days', 'catpart': ''}[m.group(1)], template)
                hits = [k for k, rx in STATE_RX if rx.match(low(text))]
                self.assertEqual(len(hits), 1, (name, template, hits))

    def test_the_wrong_counts_are_one_out_in_about_half_the_cases_and_a_named_readers_number_otherwise(self):
        one_out, readers, plain, shapes = 0, 0, 0, {}
        for seed in range(1, 6):
            parsed = _parsed_of(seed, 1, 2)
            correct, mixed = rows_of_size(seed, 'correct', 2), rows_of_size(seed, 'mixed', 2)
            for was, now in zip(correct, mixed):
                if was['claim_text'] == now['claim_text']:
                    continue
                q_was, q_now = parse_statement(was['claim_text'], parsed), parse_statement(now['claim_text'], parsed)
                if q_was['kind'] not in WHOLE_KINDS:
                    continue
                shapes[q_was['kind']] = shapes.get(q_was['kind'], 0) + 1
                if abs(q_now['stated'] - q_was['stated']) == 1:
                    one_out += 1
                if now['error_type'] and now['error_type'] != 'wrong_value':
                    readers += 1
                    for name in now['error_type'].split('+'):
                        self.assertEqual(now['lure_states'][name], 'supported')
                else:
                    plain += 1
        print('\nwrong whole-file counts at size 2, seeds 1 to 5: %d one out, %d that a named reader endorses (the two can '
              'coincide), %d that no reader endorses; by shape %s' % (one_out, readers, plain, shapes))
        self.assertGreaterEqual(one_out, 3)
        self.assertGreaterEqual(readers, 3)
        self.assertTrue(0.25 <= one_out / sum(shapes.values()) <= 0.75)


class DependsTests(unittest.TestCase):
    """`depends(events)`: for every event, what it cannot be read without, by kind (`name`, `amount`, `target`). Each is a
    dependency of the reading itself: without the event named, `answer` leaves the dependent event out."""

    KINDS = ('name', 'amount', 'target')

    @staticmethod
    def queries(case):
        out = []
        for true, mutations in case['slots']:
            for item in [true] + mutations:
                out.append(item['query'])
        return out

    @classmethod
    def closure(cls, deps, event, kinds=KINDS):
        seen, todo = set(), [n for k in kinds for n in deps[event][k]]
        while todo:
            one = todo.pop()
            if one not in seen:
                seen.add(one)
                todo += [n for k in kinds for n in deps[one][k]]
        return seen

    @staticmethod
    def left_out(events, removed):
        """The events of the subset without `removed` that the reading leaves out (not established)."""
        subset = [e for e in events if e['id'] not in removed]
        index = family._Index(subset)
        return {e['id'] for e in subset if not index.established(e)}

    def test_the_dependencies_are_events_of_the_list_by_kind_and_the_list_is_acyclic(self):
        for seed in SEEDS:
            events = raw_case(seed)['events']
            deps = family.depends(events)
            ids = {e['id'] for e in events}
            self.assertEqual(set(deps), ids)
            for event, kinds in deps.items():
                self.assertEqual(set(kinds), set(self.KINDS))
                for kind in self.KINDS:
                    self.assertTrue(set(kinds[kind]) <= ids and event not in kinds[kind])
                    self.assertEqual(len(set(kinds[kind])), len(kinds[kind]))
                self.assertNotIn(event, self.closure(deps, event))
            for kind in self.KINDS:
                self.assertTrue(any(kinds[kind] for kinds in deps.values()), kind)

    def test_without_a_dependency_the_reading_leaves_the_event_out_and_reads_the_rest_as_if_it_were_not_there(self):
        for seed in SEEDS[:3]:
            case = raw_case(seed)
            events, queries = case['events'], self.queries(case)
            deps = family.depends(events)
            pairs = 0
            for event in events:
                for kind in self.KINDS:
                    for need in deps[event['id']][kind]:
                        without = [e for e in events if e['id'] != need]
                        without_both = [e for e in without if e['id'] != event['id']]
                        self.assertIn(event['id'], self.left_out(events, {need}), (seed, event['id'], kind, need))
                        for query in queries:
                            for reader in (None,) + tuple(family.READERS):
                                self.assertEqual(family.answer(query, without, reader),
                                                 family.answer(query, without_both, reader),
                                                 (seed, event['id'], kind, need, query))
                        pairs += 1
            self.assertGreater(pairs, 100)

    def test_whatever_the_reading_leaves_out_when_something_is_absent_depends_on_it(self):
        """Complete: removing D leaves out only events that have D in their closure (over all three kinds); and the one
        thing in a closure that is not left out is the clause of the other parameter of a provision that takes over its
        value (the reading keeps such a provision without the clause: it then gives no value)."""
        for seed in SEEDS:
            events = raw_case(seed)['events']
            deps = family.depends(events)
            by_id = {e['id']: e for e in events}
            closures = {e['id']: self.closure(deps, e['id']) for e in events}
            for removed in events:
                gone = {e['id'] for e in events if removed['id'] in closures[e['id']]}
                left = self.left_out(events, {removed['id']})
                self.assertTrue(left <= gone, (seed, removed['id'], sorted(left - gone)))
                for event in sorted(gone - left):
                    link = by_id[event].get('link')
                    self.assertTrue(link and removed['kind'] == 'parameter' and removed['parameter'] == link['parameter'],
                                    (seed, event, removed['id']))
            self.assertGreater(len([e for e in events if self.left_out(events, {e['id']})]), 50)

    def test_the_kinds_are_what_they_say(self):
        """A name is indirect: a defined term or expression (the definition), a clause number that was changed (the
        renumberings), a description (the clause it describes). A clause named by its own number or heading is named
        directly: it is what the event is about (`target`), as is the clause a definition is made through."""
        seen = collections.Counter()
        for seed in SEEDS:
            events = raw_case(seed)['events']
            deps = family.depends(events)
            clause = {e['parameter']: e['id'] for e in events if e['kind'] == 'parameter'}
            renumberings = {e['id'] for e in events if e['kind'] == 'renumbering'}
            for e in events:
                got = deps[e['id']]
                if e['kind'] in ('revocation', 'correction', 'sunset'):
                    self.assertEqual((got['name'], got['amount'], got['target']), ([], [], [e['target']]))
                    seen['target of a revocation, correction or sunset'] += 1
                    continue
                ref = e.get('reference') or {}
                if e['kind'] == 'definition' and e.get('source') == 'agreement':
                    self.assertEqual((got['name'], got['amount'], got['target']), ([], [], [clause[e['parameter']]]))
                    seen['agreement definition'] += 1
                elif e['kind'] in ('amendment', 'definition'):
                    style = ref['style']
                    self.assertEqual(got['target'], [] if style == 'description' else [clause[e['parameter']]])
                    if style in ('term', 'short'):
                        self.assertEqual(got['name'], [ref['definition']])
                    elif style == 'number':
                        self.assertTrue(set(got['name']) <= renumberings)
                        self.assertNotIn(clause[e['parameter']], got['name'])
                    elif style == 'description':
                        self.assertEqual(got['name'], [clause[e['parameter']]])
                    else:
                        self.assertEqual(got['name'], [])
                    seen[style] += 1
                    if not e.get('link'):
                        self.assertEqual(got['amount'], [])
                    else:
                        self.assertNotIn(clause[e['link']['parameter']], got['amount'])
                        seen['link'] += 1
                else:
                    self.assertEqual((got['name'], got['amount'], got['target']), ([], [], []))
        for kind in ('target of a revocation, correction or sunset', 'agreement definition', 'term', 'short', 'heading',
                     'description', 'number', 'link'):
            self.assertGreater(seen[kind], 0, kind)

    def test_what_a_provision_waits_on_is_not_a_dependency(self):
        seen = 0
        for seed in SEEDS:
            events = raw_case(seed)['events']
            deps = family.depends(events)
            for e in events:
                commence = e.get('commencement')
                if commence and commence['type'] == 'event' and e['kind'] in ('amendment', 'revocation'):
                    self.assertNotIn(commence['event'], [n for k in self.KINDS for n in deps[e['id']][k]])
                    seen += 1
        self.assertGreater(seen, 0)

    def test_nothing_else_of_the_module_changed_for_it(self):
        """The generated files and key rows of a draw are what they were before `depends` was added."""
        with generate_big.facts_in_keys():
            made = generate.build_case('amendments', 1, 1)
        self.assertEqual(sources_of(made), sources_of(built(1)))
        self.assertEqual(sorted(made['key']['facts']), ['events', 'family', 'interpreter', 'interpreter_sha256', 'names',
                                                        'passages', 'readers', 'size', 'sources'])


if __name__ == '__main__':
    if len(sys.argv) >= 4 and sys.argv[1] == '--verify':
        try:
            family.current_size()
        except generate.GenerationError as error:
            sys.exit('Cannot verify: %s' % error)
        sys.exit(1 if verify(int(sys.argv[2]), int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else None) else 0)
    unittest.main()
